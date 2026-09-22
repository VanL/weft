"""Tests for Manager functionality.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.2.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-6]
- docs/specifications/07-System_Invariants.md [QUEUE.7], [IMPL.10]
"""

from __future__ import annotations

import itertools
import json
import logging
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from multiprocessing.process import BaseProcess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

import weft.core.manager as manager_mod
import weft.core.manager_runtime as manager_runtime_mod
import weft.core.tasks.base as base_task_mod
from simplebroker import Queue
from simplebroker.ext import BrokerError, DatabaseError
from tests.helpers.reactor_driver import drive_until
from tests.helpers.test_backend import active_test_backend
from tests.helpers.typing import BrokerEnv, record_and_return
from weft._constants import (
    CONTROL_KILL,
    CONTROL_PING,
    CONTROL_STOP,
    INTERNAL_AUTOSTART_ENABLED_METADATA_KEY,
    INTERNAL_AUTOSTART_SOURCE_METADATA_KEY,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS,
    MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS,
    MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS,
    MANAGER_DISPATCH_STALL_LOG_INTERVAL_SECONDS,
    MANAGER_LEADERSHIP_CHECK_INTERVAL_SECONDS,
    MANAGER_PID_LIVENESS_RECHECK_INTERVAL,
    MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS,
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    PIPELINE_RUNTIME_METADATA_KEY,
    QUEUE_INTERNAL_RESERVED_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    SERVICE_OWNER_SCHEMA,
    SERVICE_STATUS_ACTIVE,
    SERVICE_STATUS_SUPERSEDED,
    SERVICE_TYPE_MANAGED,
    TERMINAL_ENVELOPE_TYPE,
    WEFT_ADMISSION_MAX_CONNECTIONS,
    WEFT_ADMISSION_RESERVE_FRACTION,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TASK_STATE_QUEUE_PREFIX,
    WRAPPER_LOST_ERROR,
    load_config,
)
from weft.context import WeftContext
from weft.core.control_messages import encode_control_message
from weft.core.manager import DispatchOwnership, ManagedChild, Manager
from weft.core.manager_services import ManagedServiceSpec
from weft.core.monitor.task_monitor import TaskMonitor
from weft.core.service_convergence import (
    build_manager_service_payload,
    build_service_owner_payload,
    project_manager_service_record,
)
from weft.core.spawn_requests import submit_spawn_request
from weft.core.task_state import task_state_queue_name
from weft.core.tasks import (
    Consumer,
    HeartbeatTask,
    PipelineEdgeTask,
    PipelineTask,
)
from weft.core.tasks.liveness_monitor import LivenessMonitor
from weft.core.tasks.multiqueue_watcher import QueueMessageContext, QueueMode
from weft.core.taskspec import (
    IOSection,
    ReservedPolicy,
    SpecSection,
    StateSection,
    TaskSpec,
)
from weft.helpers import ContainerRuntimeDetection, process_create_time
from weft.liveness.models import HostProcessObservation

AUTOSTART_PIPELINE_PROGRESS_TIMEOUT = 60.0
"""Maximum wait without new autostart pipeline evidence under Windows CI load."""

AUTOSTART_PIPELINE_RESULT_TIMEOUT = 180.0
"""Overall safety cap for full autostart pipeline completion."""


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


def drain(queue: Queue) -> list[str]:
    items = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        items.append(value)
    return items


def serve_log_events(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in capsys.readouterr().err.splitlines()
        if line.strip()
    ]


def pending_timestamps(queue: Queue) -> list[int]:
    timestamps: list[int] = []
    for entry in queue.peek_generator(with_timestamps=True):
        if not isinstance(entry, tuple) or len(entry) != 2:
            continue
        _body, timestamp = entry
        if isinstance(timestamp, int):
            timestamps.append(timestamp)
    return timestamps


def _host_runtime_handle(pid: int) -> dict[str, object]:
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {
            "host_processes": [{"pid": pid, "create_time": process_create_time(pid)}]
        },
        "metadata": {},
    }


def _write_managed_service_owner(
    make_queue: Callable[[str], Any],
    *,
    service_key: str,
    tid: str,
    runtime_handle: dict[str, object] | None = None,
    status: Literal[
        "active", "draining", "stopped", "superseded", "terminal", "uncertain"
    ] = "active",
    ctrl_in: str | None = None,
    ctrl_out: str | None = None,
) -> None:
    make_queue(WEFT_SERVICES_REGISTRY_QUEUE).write(
        json.dumps(
            build_service_owner_payload(
                service_key=service_key,
                service_type=SERVICE_TYPE_MANAGED,
                owner_tid=tid,
                status=status,
                name="managed-service",
                queues={
                    "ctrl_in": ctrl_in or f"T{tid}.ctrl_in",
                    "ctrl_out": ctrl_out or f"T{tid}.ctrl_out",
                },
                runtime_handle=runtime_handle,
            )
        )
    )


def _managed_service_owner_rows(
    make_queue: Callable[[str], Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in make_queue(WEFT_SERVICES_REGISTRY_QUEUE).peek_generator():
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _manager_service_payload(
    manager: Manager,
    *,
    tid: str,
    name: str = "manager",
    status: Literal["active", "draining", "stopped", "superseded"] = "active",
    runtime_handle: dict[str, object] | None = None,
    requests: str = WEFT_SPAWN_REQUESTS_QUEUE,
    ctrl_in: str | None = None,
    ctrl_out: str | None = None,
    outbox: str = WEFT_MANAGER_OUTBOX_QUEUE,
) -> dict[str, object]:
    return build_manager_service_payload(
        context=manager._manager_context(),
        tid=tid,
        name=name,
        status=status,
        queues={
            "requests": requests,
            "ctrl_in": ctrl_in or f"T{tid}.ctrl_in",
            "ctrl_out": ctrl_out or f"T{tid}.ctrl_out",
            "outbox": outbox,
        },
        runtime_handle=runtime_handle or {},
    )


def _manager_service_record(
    manager: Manager,
    **kwargs: Any,
) -> dict[str, Any]:
    payload = _manager_service_payload(manager, **kwargs)
    record = project_manager_service_record(payload, timestamp=time.time_ns())
    assert record is not None
    return record


def _service_probe_for(
    manager: Manager,
    *,
    tid: str,
    source: str | None = None,
) -> Any:
    for probe in manager._service_probe_pending.values():
        if probe.tid == tid and (source is None or probe.source == source):
            return probe
    raise AssertionError(f"No pending service probe for {tid}")


def _write_service_pong(
    manager: Manager,
    make_queue: Callable[[str], Any],
    probe: Any,
) -> None:
    make_queue(manager._queue_names["ctrl_in"]).write(
        json.dumps(
            {
                "command": CONTROL_PING,
                "status": "ok",
                "message": "PONG",
                "request_id": probe.request_id,
                "tid": probe.tid,
                "task_status": "running",
            }
        )
    )
    manager._drain_control_queue_first()


def _write_manager_pong(
    manager: Manager,
    make_queue: Callable[[str], Any],
    probe: Any,
    *,
    ctrl_in_name: str,
    ctrl_out_name: str,
) -> None:
    make_queue(manager._queue_names["ctrl_in"]).write(
        json.dumps(
            {
                "command": CONTROL_PING,
                "status": "ok",
                "message": "PONG",
                "request_id": probe.request_id,
                "tid": probe.tid,
                "task_status": "running",
                "role": "manager",
                "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                "ctrl_in": ctrl_in_name,
                "ctrl_out": ctrl_out_name,
                "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
                "weft_context": str(manager._manager_context().root),
                "should_stop": False,
            }
        )
    )
    manager._drain_control_queue_first()


def _external_supervisor_runtime_handle() -> dict[str, object]:
    return {
        "runner": "manager-supervisor",
        "kind": "supervised-process",
        "id": "container:weft-manager-1",
        "control": {"authority": "external-supervisor"},
        "observations": {"container_pid": 1, "container_name": "weft-manager-1"},
        "metadata": {},
    }


def make_manager_spec(
    tid: str,
    inbox: str = WEFT_SPAWN_REQUESTS_QUEUE,
    ctrl_in: str | None = None,
    ctrl_out: str | None = None,
    *,
    idle_timeout: float | None = None,
    role: str | None = None,
    weft_context: str | None = None,
    reserved_policy_on_error: ReservedPolicy = ReservedPolicy.KEEP,
) -> TaskSpec:
    metadata: dict[str, object] = {
        "capabilities": ["tests.tasks.sample_targets:large_output"]
    }
    if idle_timeout is not None:
        metadata["idle_timeout"] = idle_timeout
    if role is not None:
        metadata["role"] = role
    return TaskSpec(
        tid=tid,
        name="manager",
        spec=SpecSection(
            type="function",
            function_target="weft.core.manager:Manager",
            timeout=None,
            weft_context=weft_context,
            reserved_policy_on_error=reserved_policy_on_error,
        ),
        io=IOSection(
            inputs={"inbox": inbox},
            outputs={"outbox": WEFT_MANAGER_OUTBOX_QUEUE},
            control={
                "ctrl_in": ctrl_in or f"T{tid}.ctrl_in",
                "ctrl_out": ctrl_out or f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
        metadata=metadata,
    )


def _manager_spec_with_queue_role(
    tid: str,
    role: str,
    queue_name: str,
) -> TaskSpec:
    payload = make_manager_spec(tid).model_dump(mode="json")
    if role == "inbox":
        payload["io"]["inputs"]["inbox"] = queue_name
    elif role == "outbox":
        payload["io"]["outputs"]["outbox"] = queue_name
    else:
        payload["io"]["control"][role] = queue_name
    return TaskSpec.model_validate(payload)


def _manager_type_with_queue_name_overrides(
    overrides: dict[str, str],
) -> type[Manager]:
    class QueueNameOverrideManager(Manager):
        def _resolve_queue_names(self) -> dict[str, str]:
            queue_names = super()._resolve_queue_names()
            queue_names.update(overrides)
            return queue_names

    return QueueNameOverrideManager


@pytest.mark.parametrize(
    "extra_role",
    ("internal_inbox", "internal_reserved", "services_registry"),
)
@pytest.mark.parametrize("base_role", ("inbox", "outbox", "ctrl_in", "ctrl_out"))
def test_manager_rejects_extra_route_collisions_with_configurable_base_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_role: str,
    base_role: str,
) -> None:
    """Manager extras cannot alias configured task-local roles [QUEUE.7]."""

    tid = "1778089999999999101"
    internal_reserved = f"T{tid}.{QUEUE_INTERNAL_RESERVED_SUFFIX}"
    extra_values = {
        "internal_inbox": WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
        "internal_reserved": internal_reserved,
        "services_registry": WEFT_SERVICES_REGISTRY_QUEUE,
    }
    manager_type: type[Manager] = Manager
    if extra_role == "internal_inbox" and base_role == "inbox":
        duplicate_queue = WEFT_SPAWN_REQUESTS_QUEUE
        monkeypatch.setattr(
            manager_mod,
            "WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE",
            duplicate_queue,
        )
        spec = make_manager_spec(tid)
    elif extra_role == "internal_reserved" and base_role == "inbox":
        duplicate_queue = WEFT_SPAWN_REQUESTS_QUEUE
        manager_type = _manager_type_with_queue_name_overrides(
            {"internal_reserved": duplicate_queue}
        )
        spec = make_manager_spec(tid)
    else:
        duplicate_queue = extra_values[extra_role]
        spec = _manager_spec_with_queue_role(tid, base_role, duplicate_queue)
    db_path = tmp_path / f"manager-{extra_role}-{base_role}-alias.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        manager_type(db_path, spec)

    message = str(exc_info.value)
    assert extra_role in message
    assert base_role in message
    assert duplicate_queue in message
    assert db_path.exists() is False


@pytest.mark.parametrize(
    "extra_role",
    ("internal_inbox", "internal_reserved", "services_registry"),
)
def test_manager_rejects_extra_route_collision_with_derived_reserved_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_role: str,
) -> None:
    """The derived base reserved lane remains distinct from Manager extras."""

    tid = "1778089999999999102"
    base_reserved = f"T{tid}.{QUEUE_RESERVED_SUFFIX}"
    manager_type: type[Manager] = Manager
    if extra_role == "internal_inbox":
        monkeypatch.setattr(
            manager_mod,
            "WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE",
            base_reserved,
        )
    elif extra_role == "internal_reserved":
        monkeypatch.setattr(
            manager_mod,
            "QUEUE_INTERNAL_RESERVED_SUFFIX",
            QUEUE_RESERVED_SUFFIX,
        )
    else:
        manager_type = _manager_type_with_queue_name_overrides(
            {"reserved": WEFT_SERVICES_REGISTRY_QUEUE}
        )
    duplicate_queue = (
        WEFT_SERVICES_REGISTRY_QUEUE
        if extra_role == "services_registry"
        else base_reserved
    )
    db_path = tmp_path / f"manager-{extra_role}-reserved-alias.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        manager_type(db_path, make_manager_spec(tid))

    message = str(exc_info.value)
    assert extra_role in message
    assert "reserved" in message
    assert duplicate_queue in message
    assert db_path.exists() is False


@pytest.mark.parametrize(
    ("left_role", "right_role"),
    tuple(
        itertools.combinations(
            ("internal_inbox", "internal_reserved", "services_registry"),
            2,
        )
    ),
)
def test_manager_rejects_pairwise_extra_route_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    left_role: str,
    right_role: str,
) -> None:
    """Manager internal and registry routes remain pairwise distinct [QUEUE.7]."""

    tid = "1778089999999999103"
    internal_reserved = f"T{tid}.{QUEUE_INTERNAL_RESERVED_SUFFIX}"
    manager_type: type[Manager] = Manager
    if (left_role, right_role) == ("internal_inbox", "internal_reserved"):
        duplicate_queue = internal_reserved
        monkeypatch.setattr(
            manager_mod,
            "WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE",
            duplicate_queue,
        )
    elif (left_role, right_role) == ("internal_inbox", "services_registry"):
        duplicate_queue = WEFT_SERVICES_REGISTRY_QUEUE
        monkeypatch.setattr(
            manager_mod,
            "WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE",
            duplicate_queue,
        )
    else:
        duplicate_queue = WEFT_SERVICES_REGISTRY_QUEUE
        manager_type = _manager_type_with_queue_name_overrides(
            {"internal_reserved": duplicate_queue}
        )

    db_path = tmp_path / f"manager-{left_role}-{right_role}-alias.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        manager_type(db_path, make_manager_spec(tid))

    message = str(exc_info.value)
    assert left_role in message
    assert right_role in message
    assert duplicate_queue in message
    assert db_path.exists() is False


def make_child_spec(size: int = 2 * 1024 * 1024) -> dict[str, object]:
    return {
        "name": "child",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:large_output",
            "output_size_limit_mb": 1,
        },
        "inbox_message": {"kwargs": {"size": size}},
    }


def write_autostart_fixture(
    root: Path,
    *,
    task_name: str,
    manifest_name: str,
    mode: str,
    max_restarts: int | None = None,
    backoff_seconds: float | None = None,
    duration: float = 0.0,
) -> tuple[Path, Path]:
    autostart_dir = root / "autostart"
    autostart_dir.mkdir()
    tasks_dir = root / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / f"{task_name}.json").write_text(
        json.dumps(
            {
                "name": task_name,
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                },
            }
        ),
        encoding="utf-8",
    )
    policy: dict[str, object] = {"mode": mode}
    if max_restarts is not None:
        policy["max_restarts"] = max_restarts
    if backoff_seconds is not None:
        policy["backoff_seconds"] = backoff_seconds
    manifest_path = autostart_dir / f"{manifest_name}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": manifest_name,
                "target": {"type": "task", "name": task_name},
                "policy": policy,
                "defaults": {"keyword_args": {"duration": duration}},
            }
        ),
        encoding="utf-8",
    )
    return autostart_dir, manifest_path


def test_manager_autostart_root_dir_uses_configured_weft_directory_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, _ = broker_env
    monkeypatch.setenv("WEFT_DIRECTORY_NAME", ".engram")
    spec = make_manager_spec(unique_tid, weft_context=str(tmp_path / "project"))
    manager = Manager(db_path, spec, config=load_config())
    try:
        assert manager._autostart_root_dir() == (tmp_path / "project" / ".engram")
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_context_is_cached_by_base_task(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    calls: list[bool | None] = []
    real_build_context = base_task_mod.build_context

    def counted_build_context(
        spec_context: str | os.PathLike[str] | None = None,
        *,
        config: Mapping[str, Any] | None = None,
        create_dirs: bool = True,
        create_database: bool = True,
        autostart: bool | None = None,
    ) -> WeftContext:
        calls.append(create_database)
        return real_build_context(
            spec_context,
            config=config,
            create_dirs=create_dirs,
            create_database=create_database,
            autostart=autostart,
        )

    monkeypatch.setattr(base_task_mod, "build_context", counted_build_context)
    spec = make_manager_spec(
        unique_tid,
        weft_context=str(tmp_path / "project"),
        idle_timeout=0.0,
    )
    manager = Manager(db_path, spec, config=load_config())
    try:
        context = manager._manager_context()
        assert manager._manager_context() is context
        manager._register_manager()
        assert manager._manager_context() is context
        assert calls == [False]
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_atexit_callback_silences_shutdown_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG, logger="weft.core.manager")

    def fail_unregister(_manager: Manager, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("sensitive interpreter shutdown failure")

    monkeypatch.setattr(Manager, "_unregister_manager", fail_unregister)
    manager = object.__new__(Manager)

    manager._atexit_unregister()

    assert caplog.records == []
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("unregister_fails", (False, True))
def test_manager_cleanup_unregisters_registered_atexit_callback(
    unregister_fails: bool,
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path, _make_queue = broker_env
    registered: list[Callable[[], None]] = []
    unregistered: list[Callable[[], None]] = []

    def fake_register(callback: Callable[[], None]) -> None:
        registered.append(callback)

    def fake_unregister(callback: Callable[[], None]) -> None:
        unregistered.append(callback)
        if unregister_fails:
            raise RuntimeError("sensitive atexit failure")

    monkeypatch.setattr(manager_mod.atexit, "register", fake_register)
    monkeypatch.setattr(manager_mod.atexit, "unregister", fake_unregister)
    spec = make_manager_spec(
        unique_tid,
        weft_context=str(tmp_path / "project"),
        idle_timeout=0.0,
    )
    manager = Manager(db_path, spec, config=load_config())

    assert len(registered) == 1

    caplog.set_level(logging.WARNING, logger="weft.core.manager")
    manager.cleanup()
    manager.cleanup()

    assert unregistered == registered
    if unregister_fails:
        assert [record.getMessage() for record in caplog.records] == [
            "Failed to unregister manager atexit callback"
        ]
        assert caplog.records[0].exc_info is None
        assert "sensitive atexit failure" not in caplog.text
    else:
        assert caplog.records == []


def write_autostart_pipeline_fixture(
    root: Path,
    *,
    task_name: str,
    pipeline_name: str,
    manifest_name: str,
    mode: str,
    max_restarts: int | None = None,
    backoff_seconds: float | None = None,
    function_target: str = "tests.tasks.sample_targets:simulate_work",
    stage_defaults: dict[str, object] | None = None,
    manifest_input: object | None = None,
    task_bundle: bool = False,
    pipeline_bundle: bool = False,
) -> tuple[Path, Path]:
    autostart_dir = root / "autostart"
    autostart_dir.mkdir()
    tasks_dir = root / "tasks"
    tasks_dir.mkdir()
    pipelines_dir = root / "pipelines"
    pipelines_dir.mkdir()

    task_payload = {
        "name": task_name,
        "spec": {
            "type": "function",
            "function_target": function_target,
        },
    }
    if task_bundle:
        task_entry = tasks_dir / task_name / "taskspec.json"
        task_entry.parent.mkdir()
    else:
        task_entry = tasks_dir / f"{task_name}.json"
    task_entry.write_text(json.dumps(task_payload), encoding="utf-8")

    pipeline_payload = {
        "name": pipeline_name,
        "stages": [
            {
                "name": "stage-one",
                "task": task_name,
                **({"defaults": stage_defaults} if stage_defaults is not None else {}),
            }
        ],
    }
    if pipeline_bundle:
        pipeline_entry = pipelines_dir / pipeline_name / "pipeline.json"
        pipeline_entry.parent.mkdir()
    else:
        pipeline_entry = pipelines_dir / f"{pipeline_name}.json"
    pipeline_entry.write_text(json.dumps(pipeline_payload), encoding="utf-8")

    policy: dict[str, object] = {"mode": mode}
    if max_restarts is not None:
        policy["max_restarts"] = max_restarts
    if backoff_seconds is not None:
        policy["backoff_seconds"] = backoff_seconds

    manifest_payload: dict[str, object] = {
        "name": manifest_name,
        "target": {"type": "pipeline", "name": pipeline_name},
        "policy": policy,
    }
    if manifest_input is not None:
        manifest_payload["defaults"] = {"input": manifest_input}

    manifest_path = autostart_dir / f"{manifest_name}.json"
    manifest_path.write_text(
        json.dumps(manifest_payload),
        encoding="utf-8",
    )
    return autostart_dir, manifest_path


def _decode_queue_payload(raw: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _pipeline_status_queue_name(child_taskspec: dict[str, object]) -> str | None:
    metadata = child_taskspec.get("metadata")
    if not isinstance(metadata, dict):
        return None
    runtime = metadata.get(PIPELINE_RUNTIME_METADATA_KEY)
    if not isinstance(runtime, dict):
        return None
    queues = runtime.get("queues")
    if not isinstance(queues, dict):
        return None
    status = queues.get("status")
    return status if isinstance(status, str) and status else None


def _drain_pipeline_status_tail(
    status_queue: Queue | None,
    status_tail: list[object],
) -> bool:
    if status_queue is None:
        return False
    status_items = drain(status_queue)
    status_tail.extend(_decode_queue_payload(item) for item in status_items)
    del status_tail[:-8]
    return bool(status_items)


def _wait_for_autostart_pipeline_result(
    manager: Manager,
    log_queue: Queue,
    make_queue: Callable[[str], Queue],
    *,
    source: str,
    progress_timeout: float = AUTOSTART_PIPELINE_PROGRESS_TIMEOUT,
    timeout: float = AUTOSTART_PIPELINE_RESULT_TIMEOUT,
) -> tuple[dict[str, Any], object]:
    start = time.monotonic()
    deadline = start + timeout
    progress_deadline = start + progress_timeout
    spawn_event: dict[str, Any] | None = None
    outbox_queue = None
    status_queue = None
    event_tail: list[dict[str, object]] = []
    status_tail: list[object] = []

    while time.monotonic() < deadline:
        progress = False
        manager.process_once()
        for item in drain(log_queue):
            progress = True
            event: dict[str, Any] = json.loads(item)
            event_tail.append(event)
            event_tail = event_tail[-12:]
            if (
                event.get("event") == "task_spawned"
                and event.get("autostart_source") == source
            ):
                spawn_event = event
                child_taskspec = event["child_taskspec"]
                assert isinstance(child_taskspec, dict)
                outbox_name = child_taskspec["io"]["outputs"]["outbox"]
                outbox_queue = make_queue(outbox_name)
                status_name = _pipeline_status_queue_name(child_taskspec)
                if status_name is not None:
                    status_queue = make_queue(status_name)

        progress = _drain_pipeline_status_tail(status_queue, status_tail) or progress

        if outbox_queue is not None:
            raw = outbox_queue.read_one()
            if raw is not None:
                return spawn_event or {}, _decode_queue_payload(raw)

        if progress:
            progress_deadline = time.monotonic() + progress_timeout
        elif time.monotonic() >= progress_deadline:
            break

        time.sleep(0.05)

    raise AssertionError(
        "Timed out waiting for autostart pipeline result "
        f"after {timeout:.1f}s or {progress_timeout:.1f}s without progress; "
        f"spawn_event={spawn_event!r}; "
        f"event_tail={event_tail!r}; status_tail={status_tail!r}"
    )


@pytest.fixture
def manager_setup(
    broker_env: BrokerEnv, unique_tid: str
) -> Iterator[tuple[Manager, Callable[[str], Queue]]]:
    db_path, make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(unique_tid, inbox, ctrl_in, ctrl_out)
    manager = Manager(db_path, spec)
    # Most tests using this fixture isolate existing Manager behavior. Tests for
    # the independently enabled LivenessMonitor construct their own Manager.
    manager._liveness_monitor_enabled = False
    yield manager, make_queue
    manager.stop(join=False)
    manager.cleanup()


def wait_for_children(manager: Manager, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while manager._user_work_children() and time.monotonic() < deadline:
        manager._cleanup_children()
        time.sleep(0.05)


def test_manager_idle_probe_uses_newest_pending_timestamp(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    """The manager's idle probe must observe its newest pending input."""

    manager, make_queue = manager_setup
    inbox = make_queue(manager._queue_names["inbox"])
    first = inbox.write("first")
    second = inbox.write("second")

    assert first < second
    assert manager._read_broker_timestamp(force=True) == second


def wait_for_log_event(
    manager: Manager,
    log_queue: Queue,
    predicate: Callable[[dict[str, object]], bool],
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    # Queue JSON carries nested TaskSpec payloads whose shape is validated by the runtime.
    deadline = time.monotonic() + timeout
    event_tail: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        manager.process_once()
        while True:
            item = log_queue.read_one()
            if item is None:
                break
            event: dict[str, Any] = json.loads(item)
            event_tail.append(event)
            event_tail = event_tail[-10:]
            if predicate(event):
                return event
        time.sleep(0.05)
    child_snapshot = {
        tid: {
            "pid": child.process.pid,
            "exitcode": child.process.exitcode,
            "alive": child.process.is_alive(),
        }
        for tid, child in manager._child_processes.items()
    }
    raise AssertionError(
        "Timed out waiting for matching task-log event; "
        f"children={child_snapshot!r}; event_tail={event_tail!r}"
    )


def preserve_consumed_log_event(log_queue: Any, event: dict[str, object]) -> None:
    """Reinsert a consumed task-log event needed as manager cleanup proof."""

    log_queue.write(json.dumps(event))


def drive_manager_until(
    manager: Manager,
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
) -> None:
    drive_until(
        predicate,
        lambda matched: matched,
        step=manager.process_once,
        wait=manager.wait_for_activity,
        timeout=timeout,
        wait_slice=0.02,
        pending_work=(manager._has_pending_worker_results,),
        diagnostics=lambda: {
            "child_tids": sorted(manager._child_processes),
            "active_child_launches": sorted(manager._active_child_launches),
            "worker_snapshot": manager._worker_activity_snapshot(),
        },
    )


# Partial process doubles below are cast only where injected into ManagedChild.
# They model lifecycle evidence without spawning unrelated operating-system processes.
class FakeLaunchProcess:
    def __init__(self, *, pid: int | None = 424242, alive: bool = True) -> None:
        self.pid = pid
        self.exitcode: int | None = None if alive else 0
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def terminate(self) -> None:
        self._alive = False
        self.exitcode = -signal.SIGTERM

    def kill(self) -> None:
        self._alive = False
        self.exitcode = -getattr(signal, "SIGKILL", signal.SIGTERM)


def _process_running(pid: int | None) -> bool:
    assert pid is not None
    psutil = pytest.importorskip("psutil")
    try:
        process = psutil.Process(pid)
    except psutil.Error:
        return False
    try:
        return bool(process.is_running() and process.status() != psutil.STATUS_ZOMBIE)
    except psutil.NoSuchProcess:
        return False


def _write_descendant_process_scripts(tmp_path: Path) -> tuple[Path, Path]:
    child_script = tmp_path / "manager_cleanup_child_sleep.py"
    child_script.write_text(
        """
from __future__ import annotations

import time


def main() -> None:
    time.sleep(60)


if __name__ == "__main__":
    main()
""".strip()
        + "\n",
        encoding="utf-8",
    )

    parent_script = tmp_path / "manager_cleanup_spawn_child.py"
    parent_script.write_text(
        """
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    child = subprocess.Popen([sys.executable, sys.argv[1]])
    Path(sys.argv[2]).write_text(str(child.pid), encoding="utf-8")
    time.sleep(60)


if __name__ == "__main__":
    main()
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return parent_script, child_script


def _write_term_trapping_descendant_scripts(tmp_path: Path) -> tuple[Path, Path]:
    """Like _write_descendant_process_scripts, but the descendant ignores SIGTERM.

    Fires the [IMPL.10] within-budget SIGKILL escalation requirement: a
    TERM-resistant descendant must still die inside the caller's cleanup
    deadline (plan section 13.1 item R-1).
    """

    child_script = tmp_path / "manager_cleanup_term_trapping_child.py"
    child_script.write_text(
        """
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path


def main() -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(60)


if __name__ == "__main__":
    main()
""".strip()
        + "\n",
        encoding="utf-8",
    )

    parent_script = tmp_path / "manager_cleanup_spawn_term_trapping_child.py"
    parent_script.write_text(
        """
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if Path(sys.argv[2]).exists():
            break
        time.sleep(0.01)
    time.sleep(60)


if __name__ == "__main__":
    main()
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return parent_script, child_script


def _wait_for_pidfile(pidfile: Path, *, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pidfile.exists():
            raw = pidfile.read_text(encoding="utf-8").strip()
            if raw:
                try:
                    return int(raw)
                except ValueError:
                    pass
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for pid file {pidfile}")


def _wait_for_pid_exit(pid: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_running(pid):
            return True
        time.sleep(0.05)
    return False


def test_manager_spawns_child(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)
    monkeypatch.setenv("BROKER_CACHE_MB", "not-an-integer")

    inbox_queue.write(json.dumps(make_child_spec()))

    spawn_event = wait_for_log_event(
        manager,
        log_queue,
        lambda event: event.get("event") == "task_spawned",
        timeout=8.0,
    )
    wait_for_children(manager)

    # gather log events to find child tid
    events = [spawn_event]
    events.extend(json.loads(item) for item in drain(log_queue))
    spawn_events = [e for e in events if e["event"] == "task_spawned"]
    assert spawn_events, "Expected task_spawned event"
    spawn_event = spawn_events[0]
    child_tid = spawn_event["child_tid"]
    child_taskspec = spawn_event["child_taskspec"]
    outbox_name = child_taskspec["io"]["outputs"].get("outbox", f"T{child_tid}.outbox")
    result_queue = make_queue(outbox_name)
    raw_reference = None
    child_events = [e for e in events if e.get("tid") == child_tid]
    result_timeout = 30.0 if os.name == "nt" else 20.0
    deadline = time.monotonic() + result_timeout
    while time.monotonic() < deadline:
        raw_reference = result_queue.read_one()
        if raw_reference is not None:
            break
        manager._cleanup_children()
        for item in drain(log_queue):
            event: dict[str, Any] = json.loads(item)
            events.append(event)
            if event.get("tid") == child_tid:
                child_events.append(event)
        time.sleep(0.05)

    if raw_reference is None:
        pytest.fail(
            "No output message "
            f"after {result_timeout:.1f}s; child events: {child_events}"
        )

    reference = json.loads(raw_reference)
    assert reference["type"] == "large_output"


def test_manager_reactor_answers_ping_while_child_launch_is_active(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    ctrl_in = make_queue(manager._queue_names["ctrl_in"])
    reply_name = f"T{int(manager.tid) + 1}.ctrl_in"
    reply_queue = make_queue(reply_name)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)
    launch_started = threading.Event()
    release_launch = threading.Event()

    def delayed_launch(*_args: object, **_kwargs: object) -> FakeLaunchProcess:
        launch_started.set()
        assert release_launch.wait(timeout=5.0)
        return FakeLaunchProcess(pid=424243)

    monkeypatch.setattr(manager_mod, "launch_task_process", delayed_launch)
    payload = make_child_spec()
    payload["name"] = "delayed-launch"
    inbox_queue.write(json.dumps(payload))

    started_at = time.monotonic()
    manager.process_once()
    elapsed = time.monotonic() - started_at

    assert elapsed < 3.0
    assert launch_started.wait(timeout=2.0)
    assert manager._active_child_launches

    ctrl_in.write(
        encode_control_message(
            CONTROL_PING,
            request_id="during",
            reply_to=reply_name,
        )
    )
    manager.process_once()

    responses = [json.loads(item) for item in drain(reply_queue)]
    pong = next(response for response in responses if response["command"] == "PING")
    assert pong["request_id"] == "during"
    assert pong["message"] == "PONG"
    assert pong["task_status"] == "running"

    release_launch.set()
    spawn_event = wait_for_log_event(
        manager,
        log_queue,
        lambda event: event.get("event") == "task_spawned",
        timeout=5.0,
    )

    assert spawn_event["child_taskspec"]["name"] == "delayed-launch"
    assert not manager._active_child_launches


def test_manager_reactor_answers_ping_while_draining(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    child_tid = "1777000000000000104"
    manager._child_processes[child_tid] = ManagedChild(
        process=cast(BaseProcess, FakeLaunchProcess(pid=424244)),
        ctrl_queue=f"T{child_tid}.ctrl_in",
        persistent=False,
    )
    manager._begin_graceful_shutdown(message_id=None)
    ctrl_in = make_queue(manager._queue_names["ctrl_in"])
    reply_name = f"T{int(manager.tid) + 1}.ctrl_in"
    reply_queue = make_queue(reply_name)

    ctrl_in.write(
        encode_control_message(
            CONTROL_PING,
            request_id="during-drain",
            reply_to=reply_name,
        )
    )
    manager.process_once()

    response = json.loads(str(reply_queue.read_one()))
    assert response["command"] == CONTROL_PING
    assert response["request_id"] == "during-drain"
    assert response["message"] == "PONG"
    assert response["should_stop"] is True
    assert response["task_status"] == "running"


def test_manager_turn_drains_control_before_registry_cleanup_and_leadership(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    calls: list[str] = []
    monkeypatch.setattr(manager, "_drain_worker_results", lambda: 0)
    monkeypatch.setattr(manager, "_retry_stale_child_launches", lambda: False)
    monkeypatch.setattr(manager, "_emit_manager_loop_summary", lambda: None)
    monkeypatch.setattr(
        manager,
        "_drain_control_queue_first",
        lambda: calls.append("control"),
    )
    monkeypatch.setattr(
        manager,
        "_refresh_manager_registration",
        lambda: calls.append("registration"),
    )
    monkeypatch.setattr(
        manager,
        "_cleanup_stale_internal_reserved_queues",
        lambda: calls.append("reserved_cleanup"),
    )

    def stop_at_leadership() -> bool:
        calls.append("leadership")
        return True

    monkeypatch.setattr(manager, "_maybe_yield_leadership", stop_at_leadership)

    manager._process_manager_reactor_turn()

    assert calls == ["control", "registration", "reserved_cleanup", "leadership"]


def test_manager_child_launch_admission_failure_is_returned_locally(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    child_spec = manager._build_child_spec(make_child_spec(size=1024), time.time_ns())
    assert child_spec is not None

    failure = LookupError("child launch lane unavailable")
    handled: list[manager_mod._ManagerChildLaunchResult] = []

    def fail_start(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(manager, "_start_service_worker", fail_start)
    monkeypatch.setattr(manager, "_handle_child_launch_failure", handled.append)

    assert manager._launch_child_task(child_spec, None) is False
    assert len(handled) == 1
    result = handled[0]
    assert result.request.child_spec is child_spec
    assert result.process is None
    assert result.launched_ns == 0
    assert result.error is failure
    assert child_spec.tid not in manager._active_child_launches
    assert child_spec.tid not in manager._child_launch_started_ns
    assert child_spec.tid not in manager._child_launch_stale_retries


def test_manager_stale_child_launch_retry_failure_is_returned_locally(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    child_spec = manager._build_child_spec(make_child_spec(size=1024), time.time_ns())
    assert child_spec is not None
    assert child_spec.tid is not None

    monkeypatch.setattr(
        manager, "_start_service_worker", lambda *_args, **_kwargs: None
    )
    assert manager._launch_child_task(child_spec, None) is True
    manager._child_launch_started_ns[child_spec.tid] = 0

    failure = LookupError("child launch retry lane unavailable")
    handled: list[manager_mod._ManagerChildLaunchResult] = []

    def fail_retry(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(manager, "_has_worker_activity", lambda: False)
    monkeypatch.setattr(
        manager, "_child_launch_runtime_evidence_seen", lambda _tid: False
    )
    monkeypatch.setattr(manager, "_start_service_worker", fail_retry)
    monkeypatch.setattr(manager, "_handle_child_launch_failure", handled.append)

    assert manager._retry_stale_child_launches() is True
    assert len(handled) == 1
    result = handled[0]
    assert result.request.child_spec is child_spec
    assert result.process is None
    assert result.launched_ns == 0
    assert result.error is failure
    assert child_spec.tid not in manager._active_child_launches
    assert child_spec.tid not in manager._child_launch_started_ns
    assert child_spec.tid not in manager._child_launch_stale_retries


def test_manager_child_launch_worker_transports_fatal_exit_identity(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    child_spec = manager._build_child_spec(make_child_spec(size=1024), time.time_ns())
    assert child_spec is not None

    request = manager_mod._ManagerChildLaunchRequest(
        child_spec=child_spec,
        task_cls=Consumer,
        internal_role=None,
        service_key=None,
        autostart_source=None,
        detach_stdio=True,
    )

    class FatalLaunchExit(BaseException):
        pass

    fatal = FatalLaunchExit("fatal launch exit")

    def fail_launch(*_args: object, **_kwargs: object) -> None:
        raise fatal

    monkeypatch.setattr(manager_mod, "launch_task_process", fail_launch)

    result = manager._run_child_launch_worker(request)

    assert result.request is request
    assert result.process is None
    assert result.launched_ns == 0
    assert result.error is fatal


def test_manager_launch_worker_success_commits_once_on_main_thread(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    reserved_queue = make_queue(manager._queue_names["reserved"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)
    launch_calls = 0

    def fake_launch(*_args: object, **_kwargs: object) -> FakeLaunchProcess:
        nonlocal launch_calls
        launch_calls += 1
        return FakeLaunchProcess(pid=424244)

    monkeypatch.setattr(manager_mod, "launch_task_process", fake_launch)
    payload = make_child_spec()
    payload["name"] = "single-commit"
    inbox_queue.write(json.dumps(payload))
    message_id = pending_timestamps(inbox_queue)[0]

    spawn_events: list[dict[str, object]] = []

    def committed_once() -> bool:
        for item in drain(log_queue):
            event: dict[str, Any] = json.loads(item)
            if event.get("event") == "task_spawned":
                spawn_events.append(event)
        return bool(spawn_events)

    drive_manager_until(manager, committed_once)

    assert launch_calls == 1
    assert len(spawn_events) == 1
    assert spawn_events[0]["child_tid"] == str(message_id)
    assert inbox_queue.peek_one(exact_timestamp=message_id) is None
    assert reserved_queue.peek_one(exact_timestamp=message_id) is None

    manager.process_once()
    for item in drain(log_queue):
        event = json.loads(item)
        if event.get("event") == "task_spawned":
            spawn_events.append(event)
    assert len(spawn_events) == 1


def test_manager_launch_worker_failure_applies_reserved_policy(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    reserved_queue = make_queue(manager._queue_names["reserved"])

    def failing_launch(*_args: object, **_kwargs: object) -> FakeLaunchProcess:
        raise RuntimeError("launch boom")

    monkeypatch.setattr(manager_mod, "launch_task_process", failing_launch)
    payload = make_child_spec()
    payload["name"] = "failed-launch"
    inbox_queue.write(json.dumps(payload))
    message_id = pending_timestamps(inbox_queue)[0]

    drive_manager_until(
        manager,
        lambda: (
            not manager._active_child_launches
            and reserved_queue.peek_one(exact_timestamp=message_id) is not None
        ),
    )

    assert inbox_queue.peek_one(exact_timestamp=message_id) is None
    assert reserved_queue.peek_one(exact_timestamp=message_id) is not None
    assert manager._child_processes == {}


def test_manager_launches_consumer_when_no_internal_task_class_is_set(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, _make_queue = manager_setup
    child_spec = manager._build_child_spec(make_child_spec(), int(time.time_ns()))
    assert child_spec is not None

    assert manager._resolve_child_task_class(child_spec) is Consumer


def test_manager_resolves_implicit_spawn_from_committed_message_id(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    tmp_path: Path,
) -> None:
    manager, make_queue = manager_setup
    stale_tid = "1777000000000000999"
    bundle_root = tmp_path / "manager-child-bundle"
    bundle_root.mkdir()
    inherited_context = str(tmp_path / "inherited-context")
    taskspec = TaskSpec(
        tid=stale_tid,
        name="implicit-child",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
        ),
        io=IOSection(
            inputs={"inbox": f"T{stale_tid}.inbox"},
            outputs={"outbox": f"T{stale_tid}.outbox"},
            control={
                "ctrl_in": f"T{stale_tid}.ctrl_in",
                "ctrl_out": f"T{stale_tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )
    taskspec.set_bundle_root(bundle_root)

    submitted_id = submit_spawn_request(
        manager._db_path,
        taskspec=taskspec,
        work_payload=None,
        config=manager._weft_config,
        inherited_weft_context=inherited_context,
    )
    spawn_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    row = spawn_queue.read_one(with_timestamps=True)

    assert row is not None
    body, message_id = row
    payload = json.loads(body)
    assert payload["taskspec"]["tid"] == stale_tid
    child_spec = manager._build_child_spec(payload, message_id)

    assert child_spec is not None
    resolved_tid = str(message_id)
    assert submitted_id == message_id
    assert child_spec.tid == resolved_tid
    assert child_spec.io.inputs["inbox"] == f"T{resolved_tid}.inbox"
    assert child_spec.io.outputs["outbox"] == f"T{resolved_tid}.outbox"
    assert child_spec.io.control["ctrl_in"] == f"T{resolved_tid}.ctrl_in"
    assert child_spec.io.control["ctrl_out"] == f"T{resolved_tid}.ctrl_out"
    assert child_spec.get_bundle_root() == str(bundle_root.resolve())
    assert child_spec.spec.weft_context == inherited_context


def test_manager_launches_pipeline_task_for_reserved_internal_class(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, _make_queue = manager_setup
    child_spec = TaskSpec(
        tid=str(time.time_ns()),
        name="pipeline-child",
        spec=SpecSection(
            type="function",
            function_target="weft.core.tasks.pipeline:runtime",
        ),
        io=IOSection(
            inputs={"inbox": "P123.inbox"},
            outputs={"outbox": "P123.outbox"},
            control={"ctrl_in": "P123.ctrl_in", "ctrl_out": "P123.ctrl_out"},
        ),
        state=StateSection(),
        metadata={
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_PIPELINE
        },
    )

    assert manager._resolve_child_task_class(child_spec) is PipelineTask

    edge_spec = TaskSpec.model_validate(
        {
            **child_spec.model_dump(mode="json"),
            "metadata": {
                **child_spec.metadata,
                INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
            },
        }
    )
    assert manager._resolve_child_task_class(edge_spec) is PipelineEdgeTask

    heartbeat_spec = TaskSpec.model_validate(
        {
            **child_spec.model_dump(mode="json"),
            "name": "heartbeat-child",
            "spec": {
                "type": "function",
                "function_target": "weft.tasks:noop",
                "persistent": True,
            },
            "metadata": {
                INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
            },
        }
    )
    assert manager._resolve_child_task_class(heartbeat_spec) is HeartbeatTask

    monitor_spec = TaskSpec.model_validate(
        {
            **child_spec.model_dump(mode="json"),
            "name": "task-monitor-child",
            "spec": {
                "type": "function",
                "function_target": "weft.tasks:noop",
                "persistent": True,
            },
            "metadata": {
                INTERNAL_RUNTIME_TASK_CLASS_KEY: (
                    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
                ),
            },
        }
    )
    assert manager._resolve_child_task_class(monitor_spec) is TaskMonitor

    liveness_spec = TaskSpec.model_validate(
        {
            **child_spec.model_dump(mode="json"),
            "name": "liveness-monitor-child",
            "spec": {
                "type": "function",
                "function_target": "weft.tasks:noop",
                "persistent": True,
            },
            "metadata": {
                INTERNAL_RUNTIME_TASK_CLASS_KEY: (
                    INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR
                ),
            },
        }
    )
    assert manager._resolve_child_task_class(liveness_spec) is LivenessMonitor


def test_manager_rejects_unknown_internal_task_class(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    child_spec = TaskSpec(
        tid=str(time.time_ns()),
        name="bad-child",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
        ),
        io=IOSection(
            inputs={"inbox": "bad.inbox"},
            outputs={"outbox": "bad.outbox"},
            control={"ctrl_in": "bad.ctrl_in", "ctrl_out": "bad.ctrl_out"},
        ),
        state=StateSection(),
        metadata={INTERNAL_RUNTIME_TASK_CLASS_KEY: "mystery"},
    )

    launched = manager._launch_child_task(child_spec, None)

    assert launched is False
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "task_spawn_rejected" for event in events)


def test_manager_enqueues_one_internal_task_monitor_spawn(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "1"})
    spec = make_manager_spec(unique_tid, idle_timeout=0.0)

    manager = Manager(db_path, spec, config=config)
    try:
        inbox_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
        payloads = [json.loads(item) for item in drain(inbox_queue)]
        assert drain(make_queue(WEFT_SPAWN_REQUESTS_QUEUE)) == []
    finally:
        manager.cleanup()

    monitor_payloads = [
        payload
        for payload in payloads
        if payload.get(INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY)
        == INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
    ]
    assert len(monitor_payloads) == 1
    monitor_payload = monitor_payloads[0]
    assert monitor_payload["taskspec"]["name"] == "task-monitor"
    assert (
        INTERNAL_RUNTIME_TASK_CLASS_KEY not in monitor_payload["taskspec"]["metadata"]
    )


def test_manager_enqueues_liveness_monitor_when_independently_enabled(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "0",
            "WEFT_LIVENESS_MONITOR_ENABLED": "1",
        }
    )
    manager = Manager(
        db_path, make_manager_spec(unique_tid, idle_timeout=0.0), config=config
    )
    try:
        payloads = [
            json.loads(item)
            for item in drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
        ]
    finally:
        manager.cleanup()

    assert {
        payload[INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY] for payload in payloads
    } == {INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR}
    payload = next(
        payload
        for payload in payloads
        if payload[INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY]
        == INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR
    )
    assert payload["taskspec"]["name"] == "liveness-monitor"
    assert payload["taskspec"]["spec"]["enable_process_title"] is False
    assert payload["inbox_message"] is None
    assert (
        payload["taskspec"]["metadata"][INTERNAL_SERVICE_KEY_METADATA_KEY]
        == INTERNAL_SERVICE_KEY_LIVENESS_MONITOR
    )


def test_manager_service_enqueue_forces_next_internal_queue_probe(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    spec = make_manager_spec(unique_tid, idle_timeout=0.0)
    manager = Manager(db_path, spec, config=config)
    seen: list[str] = []

    def record_internal_spawn(
        message: str,
        timestamp: int,
        context: QueueMessageContext,
    ) -> None:
        del timestamp, context
        payload = json.loads(message)
        seen.append(payload["taskspec"]["name"])

    try:
        drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
        manager._queues[
            WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
        ].handler = record_internal_spawn

        assert manager._enqueue_managed_service_request(
            manager._task_monitor_service_spec()
        )
        manager._drain_queue()
    finally:
        manager.cleanup()

    assert seen == ["task-monitor"]


def test_manager_convergence_drains_pending_internal_spawn_work(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    spec = make_manager_spec(unique_tid, idle_timeout=0.0)
    manager = Manager(db_path, spec, config=config)
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    internal_reserved = make_queue(manager._queue_names["internal_reserved"])
    launched: list[str] = []

    def record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        launched.append(child_spec.name)
        return True

    try:
        drain(internal_queue)
        drain(internal_reserved)
        monkeypatch.setattr(manager, "_reconcile_managed_services", lambda **_: None)
        monkeypatch.setattr(manager, "_launch_child_task", record_launch)
        manager._last_managed_service_convergence_ns = time.time_ns()
        manager._managed_internal_spawn_enqueued = True
        for index in range(5):
            internal_queue.write(
                json.dumps(
                    {
                        "name": f"internal-{index}",
                        "spec": {
                            "type": "function",
                            "function_target": (
                                "tests.tasks.sample_targets:echo_payload"
                            ),
                        },
                    }
                )
            )

        manager._run_managed_service_convergence()
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert launched == [f"internal-{index}" for index in range(5)]
    assert internal_queue.peek_one() is None
    assert internal_reserved.peek_one() is None


def test_manager_operational_log_emits_metadata_and_honors_level(
    broker_env: BrokerEnv,
    unique_tid: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "0",
            "WEFT_LIVENESS_MONITOR_ENABLED": "0",
            "WEFT_MANAGER_SERVE_LOG_ACTIVE": True,
            "WEFT_MANAGER_SERVE_LOG_LEVEL": "debug",
            "WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS": 0.1,
        }
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    capsys.readouterr()
    try:
        manager._emit_serve_log(
            "manager_loop_summary",
            component="manager",
            required_level="info",
            child_count=0,
        )
        manager._emit_serve_log(
            "trace_only",
            component="manager",
            required_level="trace",
        )
    finally:
        manager.cleanup()

    events = serve_log_events(capsys)
    assert [event["event"] for event in events] == ["manager_loop_summary"]
    event = events[0]
    assert event["schema"] == "weft.manager_serve_log"
    assert event["schema_version"] == 1
    assert event["manager_tid"] == unique_tid
    assert event["configured_level"] == "debug"
    assert event["required_level"] == "info"
    assert event["component"] == "manager"
    assert isinstance(event["timestamp_ns"], int)


def test_manager_operational_log_off_is_silent(
    broker_env: BrokerEnv,
    unique_tid: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "0",
            "WEFT_LIVENESS_MONITOR_ENABLED": "0",
            "WEFT_MANAGER_SERVE_LOG_ACTIVE": True,
            "WEFT_MANAGER_SERVE_LOG_LEVEL": "off",
        }
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    try:
        manager._emit_serve_log(
            "manager_loop_summary",
            component="manager",
            required_level="info",
        )
    finally:
        manager.cleanup()

    assert serve_log_events(capsys) == []


def test_manager_operational_log_env_without_serve_active_is_silent(
    broker_env: BrokerEnv,
    unique_tid: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "0",
            "WEFT_LIVENESS_MONITOR_ENABLED": "0",
            "WEFT_MANAGER_SERVE_LOG_LEVEL": "debug",
        }
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    try:
        manager._emit_serve_log(
            "manager_loop_summary",
            component="manager",
            required_level="info",
        )
    finally:
        manager.cleanup()

    assert serve_log_events(capsys) == []


def test_manager_service_convergence_operational_log_shows_task_monitor_start(
    broker_env: BrokerEnv,
    unique_tid: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_MANAGER_SERVE_LOG_ACTIVE": True,
            "WEFT_MANAGER_SERVE_LOG_LEVEL": "debug",
        }
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    try:
        events = serve_log_events(capsys)
    finally:
        manager.cleanup()

    decisions = [
        event
        for event in events
        if event.get("event") == "managed_service_decision"
        and event.get("service_key") == INTERNAL_SERVICE_KEY_TASK_MONITOR
    ]
    assert any(event.get("action") == "start_now" for event in decisions)
    assert any(
        event.get("enqueue_queue") == WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
        for event in decisions
    )
    assert any(
        event.get("event") == "managed_service_enqueue"
        and event.get("service_key") == INTERNAL_SERVICE_KEY_TASK_MONITOR
        and event.get("enqueue_queue") == WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
        for event in events
    )


def test_manager_does_not_enqueue_task_monitor_when_disabled(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    spec = make_manager_spec(unique_tid, idle_timeout=0.0)

    manager = Manager(db_path, spec, config=config)
    try:
        payloads = [
            json.loads(item)
            for item in drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
        ]
    finally:
        manager.cleanup()
    assert not any(
        payload.get(INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY)
        == INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
        for payload in payloads
    )
    assert not any(
        payload.get(INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY)
        == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
        for payload in payloads
    )
    assert not any(
        payload.get(INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY)
        == INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR
        for payload in payloads
    )


def test_manager_enqueues_heartbeat_through_service_path(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "1"})
    spec = make_manager_spec(unique_tid, idle_timeout=0.0)

    manager = Manager(db_path, spec, config=config)
    try:
        payloads = [
            json.loads(item)
            for item in drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
        ]
        assert drain(make_queue(WEFT_SPAWN_REQUESTS_QUEUE)) == []
    finally:
        manager.cleanup()

    heartbeat_payloads = [
        payload
        for payload in payloads
        if payload.get(INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY)
        == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
    ]
    assert len(heartbeat_payloads) == 1
    assert (
        heartbeat_payloads[0]["taskspec"]["metadata"]["heartbeat_idle_timeout"] == 0.0
    )
    metadata = heartbeat_payloads[0]["taskspec"]["metadata"]
    assert metadata[INTERNAL_SERVICE_KEY_METADATA_KEY] == INTERNAL_SERVICE_KEY_HEARTBEAT
    assert metadata[INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY] == "ensure"
    assert INTERNAL_RUNTIME_TASK_CLASS_KEY not in metadata


def test_manager_processes_internal_spawn_before_public_spawn(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(
        db_path, make_manager_spec(unique_tid, idle_timeout=0.0), config=config
    )
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    public_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    drain(internal_queue)
    drain(public_queue)

    def spawn_payload(name: str) -> dict[str, object]:
        return {
            "name": name,
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        }

    internal_queue.write(json.dumps(spawn_payload("internal-first")))
    public_queue.write(json.dumps(spawn_payload("public-second")))
    launched: list[str] = []

    def record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        launched.append(child_spec.name)
        return True

    monkeypatch.setattr(manager, "_launch_child_task", record_launch)

    try:
        manager.process_once()
    finally:
        manager.cleanup()

    assert launched == ["internal-first", "public-second"]


@pytest.mark.parametrize(
    ("used", "expected"),
    [
        (
            6,
            {
                "used": 6,
                "reserve": 3,
                "public_limit": 7,
                "internal_limit": 10,
                "public_allowed": True,
                "internal_allowed": True,
            },
        ),
        (
            7,
            {
                "used": 7,
                "reserve": 3,
                "public_limit": 7,
                "internal_limit": 10,
                "public_allowed": False,
                "internal_allowed": True,
            },
        ),
        (
            9,
            {
                "used": 9,
                "reserve": 3,
                "public_limit": 7,
                "internal_limit": 10,
                "public_allowed": False,
                "internal_allowed": True,
            },
        ),
        (
            10,
            {
                "used": 10,
                "reserve": 3,
                "public_limit": 7,
                "internal_limit": 10,
                "public_allowed": False,
                "internal_allowed": False,
            },
        ),
    ],
)
def test_admission_capacity_uses_strict_lane_limits(
    used: int,
    expected: dict[str, Any],
) -> None:
    assert (
        manager_mod._admission_capacity(
            used=used,
            max_connections=10,
            reserve_fraction=0.1,
            liveness_monitor_enabled=False,
        )
        == expected
    )


def test_admission_capacity_applies_service_floor_and_fractional_ceiling() -> None:
    assert manager_mod._admission_capacity(
        used=0,
        max_connections=2,
        reserve_fraction=0.0,
        liveness_monitor_enabled=False,
    ) == {
        "used": 0,
        "reserve": 3,
        "public_limit": 0,
        "internal_limit": 2,
        "public_allowed": False,
        "internal_allowed": True,
    }
    assert (
        manager_mod._admission_capacity(
            used=15,
            max_connections=20,
            reserve_fraction=0.21,
            liveness_monitor_enabled=True,
        )["reserve"]
        == 5
    )


def test_admission_capacity_reserves_four_slots_for_liveness_monitor() -> None:
    assert (
        manager_mod._admission_capacity(
            used=0,
            max_connections=10,
            reserve_fraction=0.0,
            liveness_monitor_enabled=True,
        )["reserve"]
        == 4
    )
    assert (
        manager_mod._admission_capacity(
            used=0,
            max_connections=10,
            reserve_fraction=0.0,
            liveness_monitor_enabled=False,
        )["reserve"]
        == 3
    )


def _write_admission_snapshot(make_queue: Callable[[str], Any], body: str) -> int:
    """Publish test state to its actual owner's runtime namespace."""
    tid = json.loads(body)["full"]
    return int(make_queue(task_state_queue_name(tid)).write(body))


def test_sqlite_admission_counts_only_live_latest_mappings(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    if active_test_backend() != "sqlite":
        pytest.skip("SQLite-specific usage observation")
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 5,
            }
        ),
    )
    drain(make_queue(task_state_queue_name(unique_tid)))
    _write_admission_snapshot(
        make_queue,
        json.dumps(
            {
                "full": "1700000000000000001",
                "short": "1700000000000000001",
                "runtime_handle": _host_runtime_handle(os.getpid()),
            }
        ),
    )
    _write_admission_snapshot(
        make_queue,
        json.dumps(
            {
                "full": "1700000000000000001",
                "short": "1700000000000000001",
                "runtime_handle": _host_runtime_handle(999_991),
            }
        ),
    )
    _write_admission_snapshot(
        make_queue,
        json.dumps(
            {
                "full": "1700000000000000002",
                "short": "1700000000000000002",
                "runtime_handle": _host_runtime_handle(os.getpid()),
            }
        ),
    )
    _write_admission_snapshot(
        make_queue,
        json.dumps({"full": "1700000000000000003", "short": "1700000000000000003"}),
    )

    try:
        # The latest row per TID decides: "finished" ends on a dead handle
        # and is released, "running" probes live, and a row with no handle
        # is undecidable and stays counted (shared cleanup-probe semantics).
        assert manager._observe_admission_usage() == 2
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_sqlite_admission_unions_launches_and_committed_children_once(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    if active_test_backend() != "sqlite":
        pytest.skip("SQLite-specific usage observation")
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 5,
            }
        ),
    )
    drain(make_queue(task_state_queue_name(unique_tid)))
    _write_admission_snapshot(
        make_queue,
        json.dumps(
            {
                "full": "1700000000000000004",
                "short": "1700000000000000004",
                "runtime_handle": _host_runtime_handle(os.getpid()),
            }
        ),
    )
    manager._active_child_launches["1700000000000000004"] = cast(Any, object())
    manager._active_child_launches["1700000000000000005"] = cast(Any, object())
    manager._child_processes["1700000000000000004"] = ManagedChild(
        process=cast(BaseProcess, FakeLaunchProcess(pid=424210)),
        ctrl_queue=None,
    )
    manager._child_processes["1700000000000000006"] = ManagedChild(
        process=cast(BaseProcess, FakeLaunchProcess(pid=424211)),
        ctrl_queue=None,
    )

    try:
        # "overlap" appears in all three evidence sets but counts once.
        # "pending" and "committed" cover both sides of the launch handoff
        # before either has durable mapping evidence.
        assert manager._observe_admission_usage() == 3
    finally:
        manager._active_child_launches.clear()
        manager._child_processes.clear()
        manager.stop(join=False)
        manager.cleanup()


def test_sqlite_admission_memoizes_probe_verdicts(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if active_test_backend() != "sqlite":
        pytest.skip("SQLite-specific usage observation")
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 5,
            }
        ),
    )
    drain(make_queue(task_state_queue_name(unique_tid)))
    _write_admission_snapshot(
        make_queue,
        json.dumps(
            {
                "full": "1700000000000000007",
                "short": "1700000000000000007",
                "runtime_handle": _host_runtime_handle(999_991),
            }
        ),
    )
    _write_admission_snapshot(
        make_queue,
        json.dumps(
            {
                "full": "1700000000000000008",
                "short": "1700000000000000008",
                "runtime_handle": _host_runtime_handle(os.getpid()),
            }
        ),
    )
    probes = 0
    real_probe = manager_mod.mapping_row_is_live

    def counting_probe(payload: Any) -> bool:
        nonlocal probes
        probes += 1
        return real_probe(payload)

    monkeypatch.setattr(manager_mod, "mapping_row_is_live", counting_probe)

    try:
        assert manager._observe_admission_usage() == 1
        assert manager._observe_admission_usage() == 1
        # One probe per row on the first pass; the dead verdict is permanent
        # for an unchanged handle and the live verdict is inside its TTL, so
        # the second observation probes nothing.
        assert probes == 2
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_sqlite_admission_memo_invalidates_when_terminal_hint_changes(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if active_test_backend() != "sqlite":
        pytest.skip("SQLite-specific usage observation")
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 5,
            }
        ),
    )
    drain(make_queue(task_state_queue_name(unique_tid)))
    payload = {
        "full": "1700000000000000009",
        "short": "1700000000000000009",
        "runtime_handle": {
            "runner": "external",
            "kind": "container",
            "id": "job-1",
            "control": {"authority": "external-supervisor"},
            "observations": {},
            "metadata": {},
        },
        "terminal": False,
    }
    _write_admission_snapshot(make_queue, json.dumps(payload))
    probes = 0
    real_probe = manager_mod.mapping_row_is_live

    def counting_probe(mapping: Any) -> bool:
        nonlocal probes
        probes += 1
        return real_probe(mapping)

    monkeypatch.setattr(manager_mod, "mapping_row_is_live", counting_probe)

    try:
        assert manager._observe_admission_usage() == 1
        _write_admission_snapshot(make_queue, json.dumps({**payload, "terminal": True}))
        assert manager._observe_admission_usage() == 0
        assert probes == 2
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_sqlite_admission_reconstructs_terminal_external_release_without_task_log(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    if active_test_backend() != "sqlite":
        pytest.skip("SQLite-specific usage observation")
    db_path, make_queue = broker_env
    task_log = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(make_queue(task_state_queue_name(unique_tid)))
    drain(task_log)
    _write_admission_snapshot(
        make_queue,
        json.dumps(
            {
                "full": "1700000000000000010",
                "short": "1700000000000000009",
                "runtime_handle": {
                    "runner": "external",
                    "kind": "container",
                    "id": "job-complete",
                    "control": {"authority": "external-supervisor"},
                    "observations": {},
                    "metadata": {},
                },
                "terminal": True,
            }
        ),
    )

    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 5,
            }
        ),
    )
    drain(task_log)

    try:
        assert manager._observe_admission_usage() == 1
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_admission_retains_public_while_internal_can_launch(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 5,
                WEFT_ADMISSION_RESERVE_FRACTION: 0.0,
            }
        ),
    )
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    internal_reserved = make_queue(manager._queue_names["internal_reserved"])
    public_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    public_reserved = make_queue(manager._queue_names["reserved"])
    for queue in (internal_queue, internal_reserved, public_queue, public_reserved):
        drain(queue)
    internal_queue.write(json.dumps(make_child_spec()))
    public_queue.write(json.dumps(make_child_spec()))
    manager._mark_pending_messages_prechecked()
    launched: list[str] = []
    monkeypatch.setattr(manager, "_observe_admission_usage", lambda: 2)
    monkeypatch.setattr(
        manager,
        "_launch_child_task",
        lambda child_spec, *_args, **_kwargs: record_and_return(
            launched, child_spec.name, True
        ),
    )

    try:
        manager.process_once()

        assert launched == ["child"]
        assert internal_queue.peek_one() is None
        assert internal_reserved.peek_one() is None
        assert public_queue.peek_one() is not None
        assert public_reserved.peek_one() is None
        wait_timeout = manager.next_wait_timeout()
        assert wait_timeout is not None
        assert wait_timeout > 0.0
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_admission_retains_both_lanes_at_internal_limit(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 5,
            }
        ),
    )
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    internal_reserved = make_queue(manager._queue_names["internal_reserved"])
    public_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    public_reserved = make_queue(manager._queue_names["reserved"])
    for queue in (internal_queue, internal_reserved, public_queue, public_reserved):
        drain(queue)
    internal_queue.write(json.dumps(make_child_spec()))
    public_queue.write(json.dumps(make_child_spec()))
    manager._mark_pending_messages_prechecked()
    monkeypatch.setattr(manager, "_observe_admission_usage", lambda: 5)

    try:
        manager.process_once()

        assert internal_queue.peek_one() is not None
        assert internal_reserved.peek_one() is None
        assert public_queue.peek_one() is not None
        assert public_reserved.peek_one() is None
        assert (
            manager._queue_counts_as_wait_activity(
                manager._queues[WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE]
            )
            is False
        )
        assert (
            manager._queue_counts_as_wait_activity(
                manager._queues[WEFT_SPAWN_REQUESTS_QUEUE]
            )
            is False
        )
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_admission_rechecks_retained_row_on_deadline_without_activity(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 5,
            }
        ),
    )
    public_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    public_reserved = make_queue(manager._queue_names["reserved"])
    drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
    drain(public_queue)
    drain(public_reserved)
    public_queue.write(json.dumps(make_child_spec()))
    manager._mark_pending_messages_prechecked()
    observations = iter((None, 0))
    observed: list[int | None] = []
    launched: list[str] = []

    def observe() -> int | None:
        value = next(observations)
        observed.append(value)
        return value

    monkeypatch.setattr(manager, "_observe_admission_usage", observe)
    monkeypatch.setattr(
        manager,
        "_launch_child_task",
        lambda child_spec, *_args, **_kwargs: record_and_return(
            launched, child_spec.name, True
        ),
    )

    try:
        manager.process_once()
        manager.process_once()

        assert observed == [None]
        assert public_queue.peek_one() is not None
        assert public_reserved.peek_one() is None

        manager._admission_retry_after_ns = time.time_ns() - 1
        manager.process_once()

        assert observed == [None, 0]
        assert launched == ["child"]
        assert public_queue.peek_one() is None
        assert public_reserved.peek_one() is None
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_failed_child_launch_restores_source_and_retries_on_admission_deadline(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(
            unique_tid,
            idle_timeout=0.0,
            reserved_policy_on_error=ReservedPolicy.REQUEUE,
        ),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 5,
                WEFT_ADMISSION_RESERVE_FRACTION: 0.0,
            }
        ),
    )
    public_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    public_reserved = make_queue(manager._queue_names["reserved"])
    drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
    drain(public_queue)
    drain(public_reserved)
    public_queue.write(json.dumps(make_child_spec()))
    manager._mark_pending_messages_prechecked()
    monkeypatch.setattr(manager, "_observe_admission_usage", lambda: 0)
    monkeypatch.setattr(
        manager,
        "_start_service_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("launch unavailable")
        ),
    )

    try:
        manager.process_once()

        assert public_queue.peek_one() is not None
        assert public_reserved.peek_one() is None
        assert manager._admission_retry_after_ns > 0

        launched: list[str] = []
        monkeypatch.setattr(
            manager,
            "_launch_child_task",
            lambda child_spec, *_args, **_kwargs: record_and_return(
                launched, child_spec.name, True
            ),
        )
        manager._admission_retry_after_ns = time.time_ns() + 60_000_000_000
        manager.process_once()
        assert launched == []

        manager._admission_retry_after_ns = time.time_ns() - 1
        manager.process_once()

        assert launched == ["child"]
        assert public_queue.peek_one() is None
    finally:
        manager.stop(join=False)
        manager.cleanup()


@pytest.mark.parametrize(
    "failure",
    [BrokerError("unavailable"), OSError("unavailable"), RuntimeError("unavailable")],
)
def test_sqlite_admission_fails_closed_on_expected_observer_errors(
    broker_env: BrokerEnv,
    unique_tid: str,
    failure: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if active_test_backend() != "sqlite":
        pytest.skip("SQLite-specific usage observation")
    db_path, _make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config({WEFT_ADMISSION_MAX_CONNECTIONS: 5}),
    )
    monkeypatch.setattr(
        manager_mod,
        "latest_tid_state_entries_for_endpoint_resolution",
        lambda _ctx, **_kwargs: (_ for _ in ()).throw(failure),
    )

    try:
        assert manager._observe_admission_usage() is None
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_sqlite_admission_fails_closed_when_state_namespace_cannot_be_listed(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if active_test_backend() != "sqlite":
        pytest.skip("SQLite-specific usage observation")
    db_path, _make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config({WEFT_ADMISSION_MAX_CONNECTIONS: 5}),
    )
    with manager._task_context().broker() as broker:
        broker_type = type(broker)
    original_list = broker_type.list_queues

    def fail_state_listing(db: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("prefix") == WEFT_TASK_STATE_QUEUE_PREFIX:
            raise BrokerError("task state namespace unavailable")
        return original_list(db, *args, **kwargs)

    monkeypatch.setattr(broker_type, "list_queues", fail_state_listing)

    try:
        assert manager._observe_admission_usage() is None
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_sqlite_admission_fails_closed_when_mapping_filter_raises(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if active_test_backend() != "sqlite":
        pytest.skip("SQLite-specific usage observation")
    db_path, _make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config({WEFT_ADMISSION_MAX_CONNECTIONS: 5}),
    )
    monkeypatch.setattr(
        manager_mod,
        "latest_tid_state_entries_for_endpoint_resolution",
        lambda _ctx, **_kwargs: {
            "undecidable": {"full": "undecidable", "short": "undecidable"}
        },
    )
    monkeypatch.setattr(
        manager_mod,
        "mapping_row_is_live",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("probe failed")),
    )

    try:
        assert manager._observe_admission_usage() is None
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_sqlite_admission_does_not_swallow_base_exception(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if active_test_backend() != "sqlite":
        pytest.skip("SQLite-specific usage observation")
    db_path, _make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config({WEFT_ADMISSION_MAX_CONNECTIONS: 5}),
    )
    monkeypatch.setattr(
        manager_mod,
        "latest_tid_state_entries_for_endpoint_resolution",
        lambda _ctx, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    try:
        with pytest.raises(KeyboardInterrupt):
            manager._observe_admission_usage()
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_disabled_admission_dispatches_without_observing_backend(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 0,
            }
        ),
    )
    public_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
    drain(public_queue)
    public_queue.write(json.dumps(make_child_spec()))
    manager._mark_pending_messages_prechecked()
    launched: list[str] = []
    monkeypatch.setattr(
        manager,
        "_observe_admission_usage",
        lambda: (_ for _ in ()).throw(
            AssertionError("disabled admission observed backend usage")
        ),
    )
    monkeypatch.setattr(
        manager,
        "_launch_child_task",
        lambda child_spec, *_args, **_kwargs: record_and_return(
            launched, child_spec.name, True
        ),
    )

    try:
        manager.process_once()
        assert launched == ["child"]
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_postgres_admission_uses_real_connection_stats_and_retains_tight_row(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    if active_test_backend() != "postgres":
        pytest.skip("requires the real PostgreSQL test backend")
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                WEFT_ADMISSION_MAX_CONNECTIONS: 4,
                WEFT_ADMISSION_RESERVE_FRACTION: 0.0,
            }
        ),
    )
    public_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    public_reserved = make_queue(manager._queue_names["reserved"])
    drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
    drain(public_queue)
    drain(public_reserved)
    public_queue.write(json.dumps(make_child_spec()))
    manager._mark_pending_messages_prechecked()

    try:
        manager.process_once()

        assert public_queue.peek_one() is not None
        assert public_reserved.peek_one() is None
    finally:
        manager.stop(join=False)
        manager.cleanup()


@pytest.mark.parametrize(
    "failure",
    [DatabaseError("unavailable"), ValueError("invalid stats")],
)
def test_postgres_admission_fails_closed_on_expected_observer_errors(
    broker_env: BrokerEnv,
    unique_tid: str,
    failure: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if active_test_backend() != "postgres":
        pytest.skip("requires the real PostgreSQL test backend")
    db_path, _make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config({WEFT_ADMISSION_MAX_CONNECTIONS: 5}),
    )
    monkeypatch.setattr(
        manager,
        "_read_postgres_connection_stats",
        lambda: (_ for _ in ()).throw(failure),
    )

    try:
        assert manager._observe_admission_usage() is None
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_stops_spawn_drain_after_child_launch_starts(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(
        db_path, make_manager_spec(unique_tid, idle_timeout=0.0), config=config
    )
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    public_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    public_reserved = make_queue(manager._queue_names["reserved"])
    drain(internal_queue)
    drain(public_queue)
    drain(public_reserved)

    def spawn_payload(name: str) -> dict[str, object]:
        return {
            "name": name,
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        }

    internal_queue.write(json.dumps(spawn_payload("internal-first")))
    public_queue.write(json.dumps(spawn_payload("public-second")))
    launched: list[str] = []

    def record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        launched.append(child_spec.name)
        manager._child_launch_started_this_turn = True
        return True

    monkeypatch.setattr(manager, "_launch_child_task", record_launch)

    try:
        manager.process_once()
    finally:
        manager.cleanup()

    assert launched == ["internal-first"]
    assert public_queue.peek_one() is not None
    assert public_reserved.peek_one() is None


def test_custom_inbox_manager_does_not_consume_internal_spawn_queue(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, inbox="custom.spawn.requests", idle_timeout=0.0),
        config=config,
    )
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    drain(internal_queue)
    internal_queue.write(
        json.dumps(
            {
                "name": "must-not-launch",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            }
        )
    )
    launched: list[str] = []
    monkeypatch.setattr(
        manager,
        "_launch_child_task",
        lambda child_spec, *_args, **_kwargs: record_and_return(
            launched, child_spec.name, True
        ),
    )

    try:
        manager.process_once()
    finally:
        manager.cleanup()

    assert launched == []
    assert internal_queue.peek_one() is not None


def test_internal_spawn_launch_failure_keeps_internal_reserved_until_shutdown(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(
        db_path, make_manager_spec(unique_tid, idle_timeout=0.0), config=config
    )
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    public_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    internal_reserved = make_queue(manager._queue_names["internal_reserved"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(internal_queue)
    drain(public_queue)
    drain(log_queue)
    payload = {
        "name": "internal-fenced",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    internal_queue.write(json.dumps(payload))
    message_id = pending_timestamps(internal_queue)[0]
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: (_ for _ in ()).throw(
            AssertionError("dispatch ownership should not gate internal spawn")
        ),
    )
    monkeypatch.setattr(
        manager,
        "_launch_child_task",
        lambda *_args, **_kwargs: False,
    )

    try:
        manager.process_once()
        assert internal_queue.peek_one(exact_timestamp=message_id) is None
        assert public_queue.peek_one(exact_timestamp=message_id) is None
        assert internal_reserved.peek_one(exact_timestamp=message_id) is not None
    finally:
        manager.cleanup()

    assert internal_reserved.peek_one(exact_timestamp=message_id) is None
    events = [json.loads(item) for item in drain(log_queue)]
    assert not any(
        str(event.get("event", "")).startswith("manager_spawn_fence")
        for event in events
    )


def test_internal_reserved_spawn_counts_as_pending_service(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(
        db_path, make_manager_spec(unique_tid, idle_timeout=0.0), config=config
    )
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    internal_reserved = make_queue(manager._queue_names["internal_reserved"])
    drain(internal_queue)
    drain(internal_reserved)
    internal_reserved.write(json.dumps(manager._build_task_monitor_spawn_payload()))

    try:
        pending = manager._pending_service_keys(
            {INTERNAL_SERVICE_KEY_TASK_MONITOR},
            queue_names=(
                manager._queue_names["internal_inbox"],
                manager._queue_names["internal_reserved"],
            ),
        )
    finally:
        manager.cleanup()

    assert pending == {INTERNAL_SERVICE_KEY_TASK_MONITOR}


def test_terminal_service_child_retries_reserved_spawn_ack(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "1"})
    manager = Manager(
        db_path, make_manager_spec(unique_tid, idle_timeout=0.0), config=config
    )
    internal_reserved = make_queue(manager._queue_names["internal_reserved"])
    drain(internal_reserved)
    internal_reserved.write(json.dumps(manager._build_heartbeat_spawn_payload()))
    message_id = pending_timestamps(internal_reserved)[0]
    child_tid = "1779000000000000042"
    manager._child_processes[child_tid] = ManagedChild(
        process=cast(BaseProcess, FakeLaunchProcess(pid=4242, alive=False)),
        ctrl_queue=f"T{child_tid}.ctrl_in",
        ctrl_out_queue=f"T{child_tid}.ctrl_out",
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
        service_key=INTERNAL_SERVICE_KEY_HEARTBEAT,
        reserved_queue=manager._queue_names["internal_reserved"],
        message_timestamp=message_id,
    )
    manager._service_state(INTERNAL_SERVICE_KEY_HEARTBEAT).active_tid = child_tid
    monkeypatch.setattr(
        manager, "_child_terminal_proof_still_within_grace", lambda *_args: False
    )
    monkeypatch.setattr(
        manager, "_write_manager_terminal_envelope", lambda *_args: None
    )

    try:
        assert manager._cleanup_children() is True
        assert internal_reserved.peek_one(exact_timestamp=message_id) is None
        pending = manager._pending_service_keys(
            {INTERNAL_SERVICE_KEY_HEARTBEAT},
            queue_names=(manager._queue_names["internal_reserved"],),
        )
    finally:
        manager.cleanup()

    assert pending == set()


def test_task_monitor_spawn_payload_uses_manager_owned_envelope(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, _make_queue = manager_setup
    payload = manager._build_task_monitor_spawn_payload()
    child_spec = manager._build_child_spec(payload, int(time.time_ns()))

    assert child_spec is not None
    assert (
        payload[INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY]
        == INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
    )
    assert INTERNAL_RUNTIME_TASK_CLASS_KEY not in payload["taskspec"]["metadata"]
    assert (
        child_spec.metadata[INTERNAL_RUNTIME_TASK_CLASS_KEY]
        == INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
    )
    assert (
        child_spec.metadata[INTERNAL_SERVICE_KEY_METADATA_KEY]
        == INTERNAL_SERVICE_KEY_TASK_MONITOR
    )
    assert child_spec.metadata[INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY] == "ensure"


def test_manager_task_monitor_supervision_ignores_dispatch_ownership(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: (_ for _ in ()).throw(
            AssertionError("task monitor supervision should not check ownership")
        ),
    )

    manager._tick_internal_services(force=True)

    payloads = [
        json.loads(item) for item in drain(make_queue(WEFT_SPAWN_REQUESTS_QUEUE))
    ]
    service_keys = {
        payload["taskspec"]["metadata"][INTERNAL_SERVICE_KEY_METADATA_KEY]
        for payload in payloads
    }
    assert service_keys == {
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    }


def test_manager_restarts_dead_task_monitor_after_backoff(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class FakeProcess:
        pid = None
        exitcode = 1

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def kill(self) -> None:
            pass

    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._task_monitor_tid = "monitor-child"
    manager._task_monitor_restart_backoff_ns = 1_000_000_000
    manager._child_processes["monitor-child"] = ManagedChild(
        process=cast(BaseProcess, FakeProcess()),
        ctrl_queue=None,
        persistent=True,
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._cleanup_children()
    manager._tick_internal_services()

    assert manager._task_monitor_tid is None
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR not in enqueued

    manager._task_monitor_next_start_allowed_ns = 0
    manager._tick_internal_services()

    assert INTERNAL_SERVICE_KEY_TASK_MONITOR in enqueued


def test_manager_restarts_dead_liveness_monitor_after_backoff(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class FakeProcess:
        pid = None
        exitcode = 1

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    tid = "liveness-monitor-child"
    manager._task_monitor_enabled = False
    manager._liveness_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._liveness_monitor_tid = tid
    manager._task_monitor_restart_backoff_ns = 1_000_000_000
    manager._child_processes[tid] = ManagedChild(
        process=cast(BaseProcess, FakeProcess()),
        ctrl_queue=None,
        persistent=True,
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR,
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._cleanup_children()
    manager._tick_internal_services()

    assert manager._liveness_monitor_tid is None
    assert INTERNAL_SERVICE_KEY_LIVENESS_MONITOR not in enqueued

    manager._service_state(INTERNAL_SERVICE_KEY_LIVENESS_MONITOR).next_allowed_ns = 0
    manager._tick_internal_services()

    assert INTERNAL_SERVICE_KEY_LIVENESS_MONITOR in enqueued


def test_task_monitor_terminal_tracked_child_allows_restart(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup

    class FakeLiveProcess:
        pid = None
        exitcode = None

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def kill(self) -> None:
            self.alive = False

    old_tid = "1777000000000000050"
    ctrl_out = f"T{old_tid}.ctrl_out"
    make_queue(ctrl_out).write(
        json.dumps(
            {
                "type": TERMINAL_ENVELOPE_TYPE,
                "tid": old_tid,
                "source": "task",
                "status": "killed",
            }
        )
    )
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._task_monitor_tid = old_tid
    manager._task_monitor_restart_backoff_ns = 0
    manager._child_processes[old_tid] = ManagedChild(
        process=cast(BaseProcess, FakeLiveProcess()),
        ctrl_queue=f"T{old_tid}.ctrl_in",
        ctrl_out_queue=ctrl_out,
        persistent=True,
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services()

    assert manager._task_monitor_tid is None
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR in enqueued


def test_stable_managed_service_convergence_uses_audit_interval(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    state = manager._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR)
    state.active_tid = "1777000000000000051"
    state.launched_once = True
    manager._last_managed_service_convergence_ns = time.time_ns()
    calls: list[str] = []

    monkeypatch.setattr(
        manager,
        "_cleanup_children",
        lambda: record_and_return(calls, "cleanup", None),
    )

    def reconcile(*, include_autostart: bool = True, **_kwargs: object) -> None:
        calls.append(f"reconcile:{include_autostart}")

    monkeypatch.setattr(manager, "_reconcile_managed_services", reconcile)

    manager._run_managed_service_convergence(include_autostart=False)

    assert calls == []

    manager._last_managed_service_convergence_ns = time.time_ns() - 10_000_000_000
    manager._run_managed_service_convergence(include_autostart=False)

    assert calls == ["cleanup", "reconcile:False"]


def test_active_managed_service_convergence_uses_active_interval(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    state = manager._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR)
    state.active_tid = "1777000000000000051"
    state.launched_once = True
    state.uncertain_attempts = 1
    manager._last_managed_service_convergence_ns = time.time_ns()
    calls: list[str] = []

    monkeypatch.setattr(
        manager,
        "_cleanup_children",
        lambda: record_and_return(calls, "cleanup", None),
    )
    monkeypatch.setattr(
        manager,
        "_reconcile_managed_services",
        lambda **_: calls.append("reconcile"),
    )

    manager._run_managed_service_convergence(include_autostart=False)

    assert calls == []

    manager._last_managed_service_convergence_ns = time.time_ns() - int(
        (MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS + 0.1) * 1_000_000_000
    )
    manager._run_managed_service_convergence(include_autostart=False)

    assert calls == ["cleanup", "reconcile"]


def test_pending_service_pong_selects_active_cadence_and_stored_reply_bypasses_gate(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = time.time_ns()
    service_key = INTERNAL_SERVICE_KEY_TASK_MONITOR
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._task_monitor_enabled = True
    probe_key = manager._service_probe_key(
        source="service-registry-pong",
        service_key=service_key,
        tid="1777000000000000051",
        timestamp=1,
    )
    manager._service_probe_pending[probe_key] = manager_mod._ServicePendingPongProbe(
        key=probe_key,
        service_key=service_key,
        tid="1777000000000000051",
        row_timestamp=1,
        source="service-registry-pong",
        request_id="pending-service",
        created_turn=manager._loop_iteration,
        pong={"message": "PONG"},
    )
    previous_policy_ns = now_ns - 500_000_000
    manager._last_managed_service_convergence_ns = previous_policy_ns
    calls: list[bool] = []
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)
    monkeypatch.setattr(manager, "_cleanup_children", lambda: False)
    monkeypatch.setattr(manager, "_internal_spawn_pending", lambda: False)

    def reconcile(**kwargs: object) -> None:
        calls.append(bool(kwargs.get("resolve_unanswered")))
        manager._service_probe_pending.clear()

    monkeypatch.setattr(manager, "_reconcile_managed_services", reconcile)

    reasons = manager._managed_service_convergence_active_reasons(
        include_autostart=False,
        include_broker=False,
    )
    manager._run_managed_service_convergence(include_autostart=False)

    assert "pong_probe_pending" in reasons
    assert calls == [False]
    assert manager._last_managed_service_convergence_ns == previous_policy_ns


def test_managed_service_convergence_active_reasons_are_stable(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    heartbeat = manager._service_state(INTERNAL_SERVICE_KEY_HEARTBEAT)
    heartbeat.active_tid = None
    task_monitor = manager._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR)
    task_monitor.spawn_pending = True
    task_monitor.uncertain_attempts = 1
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._managed_internal_spawn_enqueued = True
    manager._managed_service_duplicate_scan_pending.add(
        INTERNAL_SERVICE_KEY_TASK_MONITOR
    )
    monkeypatch.setattr(manager, "_internal_spawn_pending", lambda: True)

    reasons = manager._managed_service_convergence_active_reasons(
        include_autostart=False
    )

    assert reasons == (
        "internal_spawn_enqueued",
        "internal_spawn_pending",
        "duplicate_scan_pending",
        "missing_active_tid",
        "spawn_pending",
        "uncertain_attempts",
    )


def test_spawn_pending_internal_service_with_active_tid_remains_unsettled(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, _make_queue = manager_setup
    manager._managed_service_state.clear()
    task_monitor = manager._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR)
    task_monitor.spawn_pending = True
    task_monitor.active_tid = "1777000000000000052"
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE

    assert manager._managed_service_convergence_active_reasons(
        include_autostart=False,
        include_broker=False,
    ) == ("spawn_pending", "missing_active_tid")


def test_throttled_managed_service_convergence_skips_broker_work(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    state = manager._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR)
    state.active_tid = "1777000000000000051"
    state.launched_once = True
    manager._last_managed_service_convergence_ns = time.time_ns()
    calls: list[str] = []

    monkeypatch.setattr(
        manager,
        "_internal_spawn_pending",
        lambda: record_and_return(calls, "internal_pending", False),
    )
    monkeypatch.setattr(
        manager,
        "_cleanup_children",
        lambda: record_and_return(calls, "cleanup", False),
    )
    monkeypatch.setattr(
        manager,
        "_pending_service_keys",
        lambda _keys, **_kwargs: record_and_return(calls, "pending_keys", set()),
    )
    monkeypatch.setattr(
        manager,
        "_observed_service_candidates_by_key",
        lambda _keys, **_kwargs: record_and_return(calls, "observed", {}),
    )

    manager._run_managed_service_convergence(include_autostart=False)

    assert calls == []


def test_managed_service_convergence_reuses_internal_pending_probe_per_pass(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    cleanup_calls = 0
    pending_calls = 0

    def child_exited() -> bool:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return cleanup_calls < 3

    def internal_pending() -> bool:
        nonlocal pending_calls
        pending_calls += 1
        return False

    monkeypatch.setattr(manager, "_cleanup_children", child_exited)
    monkeypatch.setattr(manager, "_internal_spawn_pending", internal_pending)
    monkeypatch.setattr(manager, "_reconcile_managed_services", lambda **_kwargs: None)
    monkeypatch.setattr(
        manager,
        "_drain_internal_spawn_requests",
        lambda: pytest.fail("empty internal inbox should not be drained"),
    )

    manager._run_managed_service_convergence(
        include_autostart=False,
        max_passes=3,
        force=True,
    )

    assert pending_calls == 1


def test_manager_leadership_yield_rate_gate_precedes_actionable_work(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    manager._last_leader_check_ns = time.time_ns()

    monkeypatch.setattr(
        manager,
        "_has_actionable_leadership_work",
        lambda: pytest.fail("actionable work must not run before rate gate"),
    )
    monkeypatch.setattr(
        manager,
        "_read_active_manager_records",
        lambda **_kwargs: pytest.fail("registry read must not run before rate gate"),
    )

    assert manager._maybe_yield_leadership() is False


def test_nonprimary_yields_with_capacity_blocked_shared_internal_work(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {
            "WEFT_AUTOSTART_TASKS": "0",
            WEFT_ADMISSION_MAX_CONNECTIONS: 2,
            WEFT_ADMISSION_RESERVE_FRACTION: 0.0,
            "WEFT_TASK_MONITOR_ENABLED": "0",
            "WEFT_LIVENESS_MONITOR_ENABLED": "0",
        }
    )
    primary = Manager(db_path, make_manager_spec(unique_tid), config=config)
    duplicate: Manager | None = None
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    primary_reserved = make_queue(primary._queue_names["internal_reserved"])
    launched: list[str] = []

    def record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        launched.append(child_spec.name)
        return True

    try:
        drain(internal_queue)
        drain(primary_reserved)
        internal_queue.write(
            json.dumps(
                {
                    "name": "retained-internal",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.tasks.sample_targets:echo_payload",
                    },
                }
            )
        )
        monkeypatch.setattr(primary, "_observe_admission_usage", lambda: 2)

        assert primary._drain_internal_spawn_requests() == 0
        assert internal_queue.peek_one() is not None
        assert primary_reserved.peek_one() is None

        duplicate_tid = str(int(unique_tid) + 1)
        duplicate = Manager(
            db_path,
            make_manager_spec(duplicate_tid),
            config=config,
        )

        assert duplicate.should_stop is True
        assert internal_queue.peek_one() is not None
        assert (
            make_queue(duplicate._queue_names["internal_reserved"]).peek_one() is None
        )

        monkeypatch.setattr(primary, "_observe_admission_usage", lambda: 1)
        monkeypatch.setattr(primary, "_launch_child_task", record_launch)
        primary._admission_retry_after_ns = time.time_ns() - 1
        primary._expire_admission_retry_if_due()

        assert primary._drain_internal_spawn_requests() == 1
        assert launched == ["retained-internal"]
        assert internal_queue.peek_one() is None
        assert primary_reserved.peek_one() is None
    finally:
        if duplicate is not None:
            duplicate.stop(join=False)
            duplicate.cleanup()
        primary.stop(join=False)
        primary.cleanup()


def _prime_manager_next_wait_baseline(manager: Manager, now_ns: int) -> None:
    manager.should_stop = False
    manager._draining = False
    manager._drain_immediate_work_pending = False
    manager._drain_started_ns = None
    manager._drain_stops_children = True
    manager._drain_leader_tid = None
    manager._drain_signaled_children.clear()
    manager._drain_escalated_children.clear()
    manager._drain_signal_started_ns.clear()
    manager._pending_termination_sources.clear()
    manager._managed_internal_spawn_enqueued = False
    manager._stalled_control_retry_after_ns = 0
    manager._child_processes.clear()
    manager._active_child_launches.clear()
    manager._child_launch_started_ns.clear()
    manager._leader_probe_pending.clear()
    manager._service_probe_pending.clear()
    manager._managed_service_state.clear()
    manager._managed_service_duplicate_scan_pending.clear()
    manager._autostart_enabled = False
    manager._autostart_dir = None
    manager._last_public_dispatch_stall_log_ns = 0
    manager._idle_timeout = 120.0
    manager._last_activity_ns = now_ns
    far_future_ns = now_ns + 60_000_000_000
    manager._last_managed_service_convergence_ns = far_future_ns
    manager._last_leader_check_ns = far_future_ns
    manager._last_registry_heartbeat_ns = far_future_ns
    manager._last_broker_probe_ns = far_future_ns


def test_manager_next_wait_timeout_returns_nearest_due_source(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)

    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._last_managed_service_convergence_ns = now_ns - int(
        (MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS - 0.70) * 1_000_000_000
    )
    assert manager.next_wait_timeout() == pytest.approx(0.70)

    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._last_leader_check_ns = (
        now_ns - manager._leader_check_interval_ns + 50_000_000
    )
    assert manager.next_wait_timeout() == pytest.approx(0.05)

    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._last_registry_heartbeat_ns = now_ns - int(
        (MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS - 0.40) * 1_000_000_000
    )
    assert manager.next_wait_timeout() == pytest.approx(0.40)

    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._last_broker_probe_ns = (
        now_ns - manager._broker_probe_interval_ns + 300_000_000
    )
    assert manager.next_wait_timeout() == pytest.approx(0.30)

    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._last_activity_ns = now_ns - int(
        (manager._idle_timeout - 0.20) * 1_000_000_000
    )
    assert manager.next_wait_timeout() == pytest.approx(0.20)

    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._autostart_enabled = True
    manager._autostart_dir = Path("/tmp/weft-autostart-test")
    manager._autostart_last_scan_ns = (
        now_ns - manager._autostart_scan_interval_ns + 250_000_000
    )
    assert manager.next_wait_timeout() == pytest.approx(0.25)

    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._stalled_control_retry_after_ns = now_ns + 125_000_000
    assert manager.next_wait_timeout() == pytest.approx(0.125)

    _prime_manager_next_wait_baseline(manager, now_ns)
    child = ManagedChild(
        process=cast(BaseProcess, SimpleNamespace(pid=1234)),
        ctrl_queue="Tchild.ctrl_in",
        ctrl_out_queue="Tchild.ctrl_out",
        service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
        sentinel_observed_ns=now_ns,
    )
    manager._child_processes["1777000000000000051"] = child
    try:
        assert manager.next_wait_timeout() == pytest.approx(
            MANAGER_PID_LIVENESS_RECHECK_INTERVAL
        )
    finally:
        manager._child_processes.pop("1777000000000000051", None)

    _prime_manager_next_wait_baseline(manager, now_ns)
    child = ManagedChild(
        process=cast(BaseProcess, SimpleNamespace(pid=1234)),
        ctrl_queue="Tchild.ctrl_in",
        ctrl_out_queue="Tchild.ctrl_out",
        service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
        sentinel_observed_ns=now_ns - 1_000_000_000,
        terminal_proof_missing_since_ns=now_ns,
    )
    manager._child_processes["1777000000000000051"] = child
    try:
        assert manager.next_wait_timeout() == pytest.approx(
            MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS
        )
    finally:
        manager._child_processes.pop("1777000000000000051", None)

    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._active_child_launches["launch"] = cast(Any, object())
    manager._child_launch_started_ns["launch"] = now_ns - int(
        (manager_mod.MANAGER_CHILD_STARTUP_LIVENESS_GRACE_SECONDS - 0.4) * 1_000_000_000
    )
    monkeypatch.setattr(manager, "_has_worker_activity", lambda: False)
    assert manager.next_wait_timeout() == pytest.approx(0.4)


def test_manager_next_wait_timeout_ignores_broker_probe_when_idle_disabled(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)

    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._idle_timeout = 0.0
    manager._last_broker_probe_ns = now_ns - manager._broker_probe_interval_ns - 1

    wait_timeout = manager.next_wait_timeout()
    assert wait_timeout is not None
    assert wait_timeout > 0.0


def test_manager_pending_pong_probes_add_no_private_wait_timeout(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)
    _prime_manager_next_wait_baseline(manager, now_ns)
    baseline = manager.next_wait_timeout()
    manager._leader_probe_pending["leader"] = manager_mod._ManagerPendingPongProbe(
        tid="leader",
        row_timestamp=1,
        request_id="leader-request",
        created_turn=manager._loop_iteration,
    )
    service_key = "source\x1fservice\x1ftid\x1f1"
    manager._service_probe_pending[service_key] = manager_mod._ServicePendingPongProbe(
        key=service_key,
        service_key="service",
        tid="tid",
        row_timestamp=1,
        source="control-pong",
        request_id="service-request",
        created_turn=manager._loop_iteration,
    )

    assert manager.next_wait_timeout() == baseline

    monkeypatch.setattr(manager, "_process_manager_reactor_turn", lambda: None)
    manager._process_reactor_turn()
    assert set(manager._leader_probe_pending) == {"leader"}
    assert set(manager._service_probe_pending) == {service_key}


def test_manager_child_sentinel_adapter_coalesces_level_triggered_exit() -> None:
    notified = threading.Event()
    adapter = manager_mod._ManagerChildSentinelAdapter(notified.set)
    assert adapter._thread.is_alive() is True
    process = multiprocessing.get_context("spawn").Process(
        target=time.sleep,
        args=(0.05,),
    )
    process.start()
    sentinel = process.sentinel
    try:
        adapter.publish((("child", sentinel),))
        assert notified.wait(timeout=2.0)
        results = adapter.drain()
        assert len(results) == 1
        result = results[0]
        assert isinstance(result, manager_mod._ManagerChildSentinelEvent)
        assert (result.tid, result.sentinel) == ("child", sentinel)
        process.join(timeout=2.0)
        assert process.is_alive() is False

        adapter.publish((("child", sentinel),))
        assert adapter.wait(0.05) is False
        adapter.publish(())
        adapter.close(timeout=1.0)
        adapter.close(timeout=1.0)

        assert adapter._recv_conn.closed is True
        assert adapter._send_conn.closed is True
        assert adapter._thread.is_alive() is False
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=2.0)
        adapter.close(timeout=1.0)


def test_manager_child_sentinel_adapter_reports_fatal_observer_failure(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class SentinelFailure(BaseException):
        pass

    failure = SentinelFailure("sentinel observer failed")
    monkeypatch.setattr(
        manager_mod,
        "wait_for_connections",
        lambda _waitables: (_ for _ in ()).throw(failure),
    )
    adapter = manager_mod._ManagerChildSentinelAdapter(lambda: None)
    try:
        adapter.publish((("child", 123),))
        assert adapter.wait(1.0) is True
        results = adapter.drain()
        assert len(results) == 1
        assert isinstance(results[0], manager_mod._ManagerChildSentinelFailure)
        assert results[0].error is failure
    finally:
        adapter.close(timeout=1.0)

    manager._child_sentinel_adapter._results.put(
        manager_mod._ManagerChildSentinelFailure(failure)
    )
    with pytest.raises(SentinelFailure) as exc_info:
        manager._drain_child_sentinel_events()
    assert exc_info.value is failure


def test_manager_sentinel_failure_during_shutdown_still_reaps_children(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class SentinelFailure(BaseException):
        pass

    class LiveProcess:
        pid = None
        exitcode = None

        def __init__(self) -> None:
            self.kill_calls = 0

        def is_alive(self) -> bool:
            return self.kill_calls == 0

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def kill(self) -> None:
            self.kill_calls += 1

    failure = SentinelFailure("sentinel observer failed during shutdown")
    process = LiveProcess()
    manager._child_processes["child"] = ManagedChild(
        process=cast(BaseProcess, process),
        ctrl_queue="Tchild.ctrl_in",
    )
    manager._child_sentinel_adapter._results.put(
        manager_mod._ManagerChildSentinelFailure(failure)
    )
    manager._child_sentinel_adapter._result_event.set()
    monkeypatch.setattr(manager, "_cleanup_children", lambda **_kwargs: False)
    monkeypatch.setattr(manager, "_send_stop_command", lambda _queue: None)
    monkeypatch.setattr(manager, "_managed_pids_for_child", lambda _tid: set())

    with pytest.raises(SentinelFailure) as exc_info:
        manager._terminate_children(time.monotonic() + 1.0)

    assert exc_info.value is failure
    assert process.kill_calls == 1
    assert manager._child_processes == {}


def test_manager_sentinel_failure_without_children_is_not_dropped(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, _make_queue = manager_setup

    class SentinelFailure(BaseException):
        pass

    failure = SentinelFailure("sentinel observer failed after final child exit")
    manager._child_sentinel_adapter._results.put(
        manager_mod._ManagerChildSentinelFailure(failure)
    )
    manager._child_sentinel_adapter._result_event.set()

    with pytest.raises(SentinelFailure) as exc_info:
        manager._terminate_children(time.monotonic() + 1.0)

    assert exc_info.value is failure
    assert manager._child_sentinel_adapter.drain() == []


def test_manager_cleanup_surfaces_sentinel_failure_published_after_termination(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class SentinelFailure(BaseException):
        pass

    failure = SentinelFailure("sentinel observer failed before adapter close")

    def terminate_children(_deadline: float) -> None:
        manager._child_sentinel_adapter._results.put(
            manager_mod._ManagerChildSentinelFailure(failure)
        )
        manager._child_sentinel_adapter._result_event.set()

    monkeypatch.setattr(
        manager, "_drain_active_child_launches_for_cleanup", lambda _: None
    )
    monkeypatch.setattr(manager, "_terminate_children", terminate_children)
    monkeypatch.setattr(manager, "_cleanup_own_internal_reserved_queue", lambda: None)
    monkeypatch.setattr(manager, "_unregister_manager", lambda: None)
    monkeypatch.setattr(
        manager_mod.ServiceTask,
        "_cleanup_task_resources",
        lambda _self, _deadline: None,
    )
    monkeypatch.setattr(manager, "_unregister_atexit_callback", lambda: None)

    with pytest.raises(SentinelFailure) as exc_info:
        manager._cleanup_task_resources(time.monotonic() + 1.0)

    assert exc_info.value is failure
    assert manager._child_sentinel_adapter._recv_conn.closed is True
    assert manager._child_sentinel_adapter._send_conn.closed is True
    assert manager._child_sentinel_adapter.drain() == []


def test_manager_cleanup_attempts_every_phase_and_raises_first_failure(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class FirstFailure(BaseException):
        pass

    class LaterFailure(BaseException):
        pass

    first = FirstFailure("first")
    later = LaterFailure("later")
    calls: list[str] = []

    def phase(name: str, failure: BaseException | None = None) -> Callable[..., None]:
        def run(*_args: object, **_kwargs: object) -> None:
            calls.append(name)
            if failure is not None:
                raise failure

        return run

    with monkeypatch.context() as patcher:
        patcher.setattr(
            manager,
            "_drain_active_child_launches_for_cleanup",
            phase("launches", first),
        )
        patcher.setattr(manager, "_terminate_children", phase("children"))
        patcher.setattr(
            manager,
            "_cleanup_own_internal_reserved_queue",
            phase("reserved", later),
        )
        patcher.setattr(manager, "_unregister_manager", phase("registry"))
        patcher.setattr(
            manager._child_sentinel_adapter,
            "close",
            phase("sentinel"),
        )
        patcher.setattr(
            manager_mod.ServiceTask,
            "_cleanup_task_resources",
            phase("service"),
        )
        patcher.setattr(
            manager,
            "_unregister_atexit_callback",
            phase("atexit"),
        )

        with pytest.raises(FirstFailure) as exc_info:
            manager._cleanup_task_resources(time.monotonic() + 1.0)

    manager._draining = False
    assert exc_info.value is first
    assert calls == [
        "launches",
        "children",
        "reserved",
        "registry",
        "sentinel",
        "service",
        "atexit",
    ]


def test_manager_partial_initialization_cleanup_attempts_both_owners(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class AdapterFailure(BaseException):
        pass

    class BaseFailure(BaseException):
        pass

    adapter_failure = AdapterFailure("adapter")
    base_failure = BaseFailure("base")
    calls: list[str] = []

    def close_adapter(*_args: object, **_kwargs: object) -> None:
        calls.append("adapter")
        raise adapter_failure

    def close_base(*_args: object, **_kwargs: object) -> None:
        calls.append("base")
        raise base_failure

    with monkeypatch.context() as patcher:
        patcher.setattr(manager._child_sentinel_adapter, "close", close_adapter)
        patcher.setattr(
            manager_mod.ServiceTask,
            "_abort_partial_initialization",
            close_base,
        )
        with pytest.raises(AdapterFailure) as exc_info:
            manager._abort_partial_initialization()

    assert exc_info.value is adapter_failure
    assert calls == ["adapter", "base"]


def test_manager_init_skips_initial_broker_probe_when_idle_disabled(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env

    def fail_read_broker_timestamp(
        self: Manager,
        *,
        force: bool = False,
    ) -> int:
        del self, force
        raise AssertionError("idle_timeout=0 managers must not probe broker activity")

    monkeypatch.setattr(Manager, "_read_broker_timestamp", fail_read_broker_timestamp)
    spec = make_manager_spec(unique_tid, idle_timeout=0.0)

    manager = Manager(
        db_path,
        spec,
        config=load_config(
            {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
        ),
    )
    try:
        assert manager._last_broker_timestamp == 0
        assert manager._last_broker_probe_ns == 0
    finally:
        manager.cleanup()


def test_manager_constructor_anchors_cadence_for_bootstrap_probes(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env

    def register(manager: Manager) -> None:
        manager._leader_probe_pending["bootstrap-leader"] = (
            manager_mod._ManagerPendingPongProbe(
                tid="bootstrap-leader",
                row_timestamp=1,
                request_id="bootstrap-leader-request",
                created_turn=manager._loop_iteration,
            )
        )

    def reconcile(manager: Manager, **_kwargs: object) -> None:
        key = "bootstrap-service"
        manager._service_probe_pending[key] = manager_mod._ServicePendingPongProbe(
            key=key,
            service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
            tid="bootstrap-service-tid",
            row_timestamp=1,
            source="service-registry-pong",
            request_id="bootstrap-service-request",
            created_turn=manager._loop_iteration,
        )

    monkeypatch.setattr(Manager, "_register_manager", register)
    monkeypatch.setattr(
        Manager, "_maybe_yield_leadership", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(Manager, "_reconcile_managed_services", reconcile)
    manager = Manager(db_path, make_manager_spec(unique_tid))
    try:
        assert manager._last_leader_check_ns > 0
        assert manager._last_managed_service_convergence_ns > 0
        assert manager._loop_iteration == 0
    finally:
        manager.cleanup()


def test_manager_next_wait_timeout_does_not_child_poll_supervision_only_services(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)
    _prime_manager_next_wait_baseline(manager, now_ns)
    child = ManagedChild(
        process=cast(BaseProcess, SimpleNamespace(pid=1234)),
        ctrl_queue="Tchild.ctrl_in",
        ctrl_out_queue="Tchild.ctrl_out",
        persistent=True,
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
        service_key=INTERNAL_SERVICE_KEY_HEARTBEAT,
    )
    manager._child_processes["1777000000000000052"] = child
    try:
        wait_timeout = manager.next_wait_timeout()
        assert wait_timeout is not None
        assert wait_timeout > MANAGER_PID_LIVENESS_RECHECK_INTERVAL
    finally:
        manager._child_processes.pop("1777000000000000052", None)


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("should_stop", True),
        (
            "_pending_termination_sources",
            deque([("signal", signal.SIGTERM)]),
        ),
    ],
)
def test_manager_next_wait_timeout_returns_zero_for_immediate_work(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    value: object,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)
    _prime_manager_next_wait_baseline(manager, now_ns)
    setattr(manager, attribute, value)

    assert manager.next_wait_timeout() == 0.0


def test_manager_drain_publishes_only_exact_clocks(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)
    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._draining = True
    manager._drain_stops_children = True
    manager._drain_started_ns = now_ns
    manager._child_processes["child"] = ManagedChild(
        process=cast(BaseProcess, SimpleNamespace(pid=1234)),
        ctrl_queue="Tchild.ctrl_in",
    )
    manager._drain_signaled_children.add("child")
    manager._drain_signal_started_ns["child"] = now_ns - int(
        (manager_mod.MANAGER_CHILD_STOP_ESCALATION_SECONDS - 0.35) * 1_000_000_000
    )

    assert manager.next_wait_timeout() == pytest.approx(0.35)

    manager._drain_escalated_children.add("child")
    assert manager.next_wait_timeout() == pytest.approx(
        manager_mod.MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS
    )

    manager._drain_stops_children = False
    manager._drain_leader_tid = "leader"
    manager._last_leadership_drain_revalidate_ns = now_ns - int(
        (manager_mod.MANAGER_LEADERSHIP_DRAIN_REVALIDATE_SECONDS - 0.2) * 1_000_000_000
    )
    assert manager.next_wait_timeout() == pytest.approx(0.2)


def test_manager_drain_immediate_continuation_is_one_shot(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)
    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._draining = True
    manager._drain_immediate_work_pending = True
    manager._drain_started_ns = now_ns
    manager._child_processes["child"] = ManagedChild(
        process=cast(BaseProcess, SimpleNamespace(pid=1234)),
        ctrl_queue="Tchild.ctrl_in",
    )
    monkeypatch.setattr(manager, "_revalidate_leadership_drain", lambda: True)
    monkeypatch.setattr(manager, "_cleanup_children", lambda: False)
    monkeypatch.setattr(manager, "_send_stop_command", lambda _queue: None)

    assert manager.next_wait_timeout() == 0.0
    manager._continue_shutdown_drain()
    assert manager._drain_immediate_work_pending is False
    assert manager.next_wait_timeout() == pytest.approx(
        manager_mod.MANAGER_CHILD_STOP_ESCALATION_SECONDS
    )


def test_manager_autostart_due_bypasses_convergence_throttle(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    autostart_dir = tmp_path / "autostart"
    autostart_dir.mkdir()
    config = dict(
        load_config(
            {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
        )
    )
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=config,
    )
    now_ns = time.time_ns()
    stale_scan_ns = now_ns - manager._autostart_scan_interval_ns - 1
    manager._autostart_last_scan_ns = stale_scan_ns
    manager._last_managed_service_convergence_ns = now_ns

    try:
        assert manager.next_wait_timeout() == 0.0

        manager.process_once()

        assert manager._autostart_last_scan_ns > stale_scan_ns
        assert "autostart_scan_due" not in (
            manager._managed_service_convergence_active_reasons(
                include_autostart=True,
                include_broker=False,
            )
        )
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_stored_autostart_pong_bypasses_only_included_manifest_scan(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    source = "/tmp/weft-autostart.yaml"
    probe_key = manager._service_probe_key(
        source="service-registry-pong",
        service_key=source,
        tid="1777000000000000052",
        timestamp=1,
    )
    manager._autostart_sources = {source}
    manager._service_probe_pending[probe_key] = manager_mod._ServicePendingPongProbe(
        key=probe_key,
        service_key=source,
        tid="1777000000000000052",
        row_timestamp=1,
        source="service-registry-pong",
        request_id="autostart-pong",
        created_turn=manager._loop_iteration,
        pong={"message": "PONG"},
    )
    manager._task_monitor_enabled = False
    manager._liveness_monitor_enabled = False
    scan_forces: list[bool] = []
    monkeypatch.setattr(
        manager,
        "_desired_autostart_services",
        lambda *, force=False: record_and_return(scan_forces, force, []),
    )

    manager._reconcile_managed_services(include_autostart=False)
    manager._reconcile_managed_services(include_autostart=True)

    assert scan_forces == [True]


def test_complete_autostart_scan_retires_absent_unanswered_probe(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, _make_queue = manager_setup
    manager._autostart_enabled = True
    manager._autostart_dir = tmp_path
    source = "/tmp/removed-weft-autostart.json"
    probe_key = manager._service_probe_key(
        source="service-registry-pong",
        service_key=source,
        tid="1777000000000000053",
        timestamp=1,
    )
    manager._autostart_sources = {source}
    manager._service_probe_pending[probe_key] = manager_mod._ServicePendingPongProbe(
        key=probe_key,
        service_key=source,
        tid="1777000000000000053",
        row_timestamp=1,
        source="service-registry-pong",
        request_id="removed-autostart",
        created_turn=manager._loop_iteration,
    )
    monkeypatch.setattr(
        manager,
        "_autostart_manifest_path_snapshot",
        lambda: ([], True),
    )

    manager._reconcile_managed_services(force=True, include_autostart=True)

    assert probe_key not in manager._service_probe_pending
    assert source not in manager._autostart_sources


def test_failed_autostart_scan_retains_pending_probe_and_source(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, _make_queue = manager_setup
    manager._autostart_enabled = True
    manager._autostart_dir = tmp_path
    source = "/tmp/unreadable-weft-autostart.json"
    probe_key = manager._service_probe_key(
        source="service-registry-pong",
        service_key=source,
        tid="1777000000000000054",
        timestamp=1,
    )
    manager._autostart_sources = {source}
    manager._service_probe_pending[probe_key] = manager_mod._ServicePendingPongProbe(
        key=probe_key,
        service_key=source,
        tid="1777000000000000054",
        row_timestamp=1,
        source="service-registry-pong",
        request_id="unreadable-autostart",
        created_turn=manager._loop_iteration,
        pong={"message": "PONG"},
    )
    monkeypatch.setattr(
        manager,
        "_autostart_manifest_path_snapshot",
        lambda: ([], False),
    )

    manager._reconcile_managed_services(force=True, include_autostart=True)

    assert probe_key in manager._service_probe_pending
    assert source in manager._autostart_sources


def test_manager_unscanned_autostart_is_due_without_reading_clock(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, _make_queue = manager_setup
    manager._autostart_enabled = True
    manager._autostart_dir = tmp_path
    manager._autostart_last_scan_ns = 0
    monkeypatch.setattr(
        manager_mod.time,
        "time_ns",
        lambda: pytest.fail("unscanned autostart must not read the clock"),
    )

    reasons = manager._managed_service_convergence_active_reasons(
        include_autostart=True,
        include_broker=False,
    )

    assert "autostart_scan_due" in reasons


def test_manager_disabled_autostart_is_not_due_without_reading_clock(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, _make_queue = manager_setup
    manager._autostart_enabled = False
    manager._autostart_dir = tmp_path
    manager._autostart_last_scan_ns = 1
    monkeypatch.setattr(
        manager_mod.time,
        "time_ns",
        lambda: pytest.fail("disabled autostart must not read the clock"),
    )

    reasons = manager._managed_service_convergence_active_reasons(
        include_autostart=True,
        include_broker=False,
    )

    assert "autostart_scan_due" not in reasons


def test_manager_clears_dispatch_stall_timer_when_backlog_drains(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)
    _prime_manager_next_wait_baseline(manager, now_ns)
    manager._weft_config[MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY] = True
    manager._weft_config["MANAGER_SERVE_LOG_LEVEL"] = "info"
    manager._last_public_dispatch_stall_log_ns = now_ns - int(
        (MANAGER_DISPATCH_STALL_LOG_INTERVAL_SECONDS + 1.0) * 1_000_000_000
    )

    assert manager.next_wait_timeout() == 0.0

    manager._maybe_log_public_dispatch_stall(manager._queue_names["inbox"])

    assert manager._last_public_dispatch_stall_log_ns == 0
    wait_timeout = manager.next_wait_timeout()
    assert wait_timeout is not None
    assert wait_timeout > 0.0


def test_manager_local_notification_wakes_unbounded_shared_wait(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    monkeypatch.setattr(manager, "_has_pending_messages", lambda: False)
    manager._strategy.notify_activity()

    started_at = time.monotonic()
    manager._wait_for_reactor_activity(timeout=None)

    assert time.monotonic() - started_at < 0.2


@pytest.mark.parametrize("timeout", [0.0, -0.1])
def test_manager_nonpositive_wait_does_not_probe_or_sleep(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
    timeout: float | None,
) -> None:
    manager, _make_queue = manager_setup
    monkeypatch.setattr(
        manager,
        "_ensure_multi_activity_waiter",
        lambda: pytest.fail("nonpositive wait probed backend"),
    )
    monkeypatch.setattr(
        manager,
        "_has_pending_messages",
        lambda: pytest.fail("nonpositive wait scanned queues"),
    )
    monkeypatch.setattr(
        manager._stop_event,
        "wait",
        lambda timeout: pytest.fail("nonpositive wait slept"),
    )
    manager._wait_for_reactor_activity(timeout=timeout)


def test_manager_fallback_pending_work_does_not_sleep(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    monkeypatch.setattr(manager, "_has_pending_messages", lambda: True)
    monkeypatch.setattr(
        manager._stop_event, "wait", lambda timeout: pytest.fail("pending work slept")
    )
    manager._strategy.notify_activity()
    manager._wait_for_reactor_activity(timeout=None)


def test_manager_leadership_self_owner_skips_actionable_scan_per_turn(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    current_ns = {"value": now_ns}
    registry_calls = 0

    def count_registry_reads(**_kwargs: object) -> dict[str, dict[str, object]]:
        nonlocal registry_calls
        registry_calls += 1
        return {manager.tid: {"tid": manager.tid}}

    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: current_ns["value"])
    monkeypatch.setattr(
        manager,
        "_has_actionable_leadership_work",
        lambda: pytest.fail("self-owned leadership should not scan work queues"),
    )
    monkeypatch.setattr(manager, "_read_active_manager_records", count_registry_reads)
    manager._loop_iteration = 42
    manager._leader_check_interval_ns = int(
        MANAGER_LEADERSHIP_CHECK_INTERVAL_SECONDS * 1_000_000_000
    )
    manager._last_leader_check_ns = now_ns - manager._leader_check_interval_ns - 1
    manager._leader_check_turn = None

    assert manager._maybe_yield_leadership() is False
    current_ns["value"] += manager._leader_check_interval_ns + 1
    assert manager._maybe_yield_leadership() is False

    assert registry_calls == 1

    manager._invalidate_leadership_work_cache()
    current_ns["value"] += manager._leader_check_interval_ns + 1

    assert manager._maybe_yield_leadership() is False
    assert registry_calls == 2


def test_manager_leadership_lower_owner_checks_actionable_work_before_yield(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    actionable_calls = 0
    lower_tid = str(int(manager.tid) - 1)

    def count_actionable_work() -> bool:
        nonlocal actionable_calls
        actionable_calls += 1
        return True

    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)
    monkeypatch.setattr(
        manager, "_has_actionable_leadership_work", count_actionable_work
    )
    monkeypatch.setattr(
        manager,
        "_read_active_manager_records",
        lambda **_kwargs: {
            lower_tid: {"tid": lower_tid},
            manager.tid: {"tid": manager.tid},
        },
    )
    manager._leader_check_interval_ns = int(
        MANAGER_LEADERSHIP_CHECK_INTERVAL_SECONDS * 1_000_000_000
    )
    manager._last_leader_check_ns = now_ns - manager._leader_check_interval_ns - 1
    manager._leader_check_turn = None

    assert manager._maybe_yield_leadership() is False
    assert actionable_calls == 1
    assert manager.should_stop is False


def test_tracked_service_candidate_uses_live_child_without_terminal_scan(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    child = ManagedChild(
        process=cast(BaseProcess, SimpleNamespace(pid=1234)),
        ctrl_queue="Tchild.ctrl_in",
        ctrl_out_queue="Tchild.ctrl_out",
        service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
    )
    manager._child_processes["1777000000000000051"] = child
    monkeypatch.setattr(manager, "_child_has_exited", lambda _child: False)
    monkeypatch.setattr(
        manager,
        "_child_terminal_proof_visible",
        lambda *_args: pytest.fail("live child should not scan terminal proof"),
    )

    try:
        candidate = manager._tracked_service_candidate(
            INTERNAL_SERVICE_KEY_TASK_MONITOR
        )
    finally:
        manager._child_processes.pop("1777000000000000051", None)

    assert candidate is not None
    assert candidate.state == "live"
    assert candidate.source == "manager-child"


def test_reconcile_reuses_tracked_service_candidates(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._task_monitor_enabled = True
    calls: list[str] = []

    def tracked(
        service_key: str,
        *,
        scan_terminal_proof: bool = False,
    ) -> manager_mod.ServiceCandidate:
        del scan_terminal_proof
        calls.append(service_key)
        return manager_mod.ServiceCandidate(
            key=service_key,
            tid=f"17770000000000000{len(calls)}",
            state="live",
            source="manager-child",
        )

    monkeypatch.setattr(
        manager, "_pending_service_keys", lambda _keys, **_kwargs: set()
    )
    monkeypatch.setattr(manager, "_tracked_service_candidate", tracked)
    monkeypatch.setattr(
        manager,
        "_observed_service_candidates_by_key",
        lambda _keys, **_kwargs: pytest.fail("live tracked services need no replay"),
    )

    manager._reconcile_managed_services(include_autostart=False)

    assert calls == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]


def test_reconcile_clears_duplicate_scan_after_live_tracked_evidence(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._task_monitor_enabled = True
    service_keys = {
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    }
    candidates = {
        key: manager_mod.ServiceCandidate(
            key=key,
            tid=f"17770000000000000{index}",
            state="live",
            source="manager-child",
        )
        for index, key in enumerate(sorted(service_keys), start=1)
    }
    manager._managed_service_duplicate_scan_pending.update(service_keys)

    monkeypatch.setattr(
        manager, "_pending_service_keys", lambda _keys, **_kwargs: set()
    )
    monkeypatch.setattr(
        manager,
        "_tracked_service_candidate",
        lambda service_key, **_kwargs: candidates[service_key],
    )
    monkeypatch.setattr(
        manager,
        "_observed_service_candidates_by_key",
        lambda keys, **_kwargs: {key: [candidates[key]] for key in keys},
    )
    monkeypatch.setattr(
        manager, "_tick_managed_service", lambda *_args, **_kwargs: None
    )

    manager._reconcile_managed_services(include_autostart=False)

    assert manager._managed_service_duplicate_scan_pending.isdisjoint(service_keys)


def test_managed_service_progress_reasons_are_coarse_and_stable() -> None:
    before = {
        INTERNAL_SERVICE_KEY_TASK_MONITOR: {
            "spawn_pending": True,
            "active_tid": None,
            "next_allowed_ns": 0,
            "launched_once": False,
            "restarts": 0,
            "uncertain_attempts": 1,
            "uncertain_since_ns": 100,
            "last_uncertain_reason": "old",
        }
    }
    after = {
        INTERNAL_SERVICE_KEY_TASK_MONITOR: {
            "spawn_pending": False,
            "active_tid": "1777000000000000051",
            "next_allowed_ns": 1,
            "launched_once": True,
            "restarts": 0,
            "uncertain_attempts": 0,
            "uncertain_since_ns": None,
            "last_uncertain_reason": None,
        }
    }

    reasons = Manager._managed_service_progress_reasons(
        before=before,
        after=after,
        child_exited=True,
        service_request_enqueued=True,
        internal_spawn_drained=True,
    )

    assert reasons == (
        "child_exited",
        "service_request_enqueued",
        "internal_spawn_drained",
        "active_tid_changed",
        "spawn_pending_changed",
        "uncertain_state_changed",
        "service_state_changed",
    )


def test_task_monitor_terminal_log_overrides_tracked_live_child(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup

    class FakeLiveProcess:
        pid = None
        exitcode = None

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def kill(self) -> None:
            self.alive = False

    old_tid = "1777000000000000052"
    metadata = {
        "internal": True,
        "role": "task_monitor",
        INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
        INTERNAL_SERVICE_KEY_METADATA_KEY: INTERNAL_SERVICE_KEY_TASK_MONITOR,
    }
    make_queue(WEFT_GLOBAL_LOG_QUEUE).write(
        json.dumps(
            {
                "tid": old_tid,
                "status": "killed",
                "event": "control_kill",
                "taskspec": {
                    "metadata": metadata,
                    "io": {
                        "control": {
                            "ctrl_in": f"T{old_tid}.ctrl_in",
                            "ctrl_out": f"T{old_tid}.ctrl_out",
                        }
                    },
                },
            }
        )
    )
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._task_monitor_tid = old_tid
    manager._task_monitor_restart_backoff_ns = 0
    manager._child_processes[old_tid] = ManagedChild(
        process=cast(BaseProcess, FakeLiveProcess()),
        ctrl_queue=f"T{old_tid}.ctrl_in",
        ctrl_out_queue=f"T{old_tid}.ctrl_out",
        persistent=True,
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert manager._task_monitor_tid is None
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR in enqueued


def test_task_monitor_manager_spawned_pid_counts_as_live_owner(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    child_tid = "1777000000000000053"
    _write_managed_service_owner(
        make_queue,
        service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
        tid=child_tid,
        runtime_handle=_host_runtime_handle(os.getpid()),
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services()

    assert manager._task_monitor_tid == child_tid
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR not in enqueued


def test_task_monitor_terminal_tracked_child_does_not_hide_new_live_owner(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup

    class FakeLiveProcess:
        pid = None
        exitcode = None

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def kill(self) -> None:
            self.alive = False

    old_tid = "1777000000000000051"
    new_tid = "1777000000000000052"
    old_ctrl_out = f"T{old_tid}.ctrl_out"
    make_queue(old_ctrl_out).write(
        json.dumps(
            {
                "type": TERMINAL_ENVELOPE_TYPE,
                "tid": old_tid,
                "source": "task",
                "status": "killed",
            }
        )
    )
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._task_monitor_tid = new_tid
    manager._child_processes[old_tid] = ManagedChild(
        process=cast(BaseProcess, FakeLiveProcess()),
        ctrl_queue=f"T{old_tid}.ctrl_in",
        ctrl_out_queue=old_ctrl_out,
        persistent=True,
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    )
    manager._child_processes[new_tid] = ManagedChild(
        process=cast(BaseProcess, FakeLiveProcess()),
        ctrl_queue=f"T{new_tid}.ctrl_in",
        ctrl_out_queue=f"T{new_tid}.ctrl_out",
        persistent=True,
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services()

    assert manager._task_monitor_tid == new_tid
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR not in enqueued


def test_task_monitor_stale_log_without_liveness_does_not_block_restart(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    old_tid = "1777000000000000100"
    make_queue(WEFT_GLOBAL_LOG_QUEUE).write(
        json.dumps(
            {
                "tid": old_tid,
                "status": "running",
                "taskspec": {
                    "metadata": {
                        "internal": True,
                        "role": "task_monitor",
                        INTERNAL_RUNTIME_TASK_CLASS_KEY: (
                            INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
                        ),
                        INTERNAL_SERVICE_KEY_METADATA_KEY: (
                            INTERNAL_SERVICE_KEY_TASK_MONITOR
                        ),
                    },
                    "io": {
                        "control": {
                            "ctrl_in": f"T{old_tid}.ctrl_in",
                            "ctrl_out": f"T{old_tid}.ctrl_out",
                        }
                    },
                },
            }
        )
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert enqueued == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]


def test_task_monitor_recent_log_without_liveness_blocks_duplicate_restart(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    recent_tid = str(time.time_ns())
    _write_managed_service_owner(
        make_queue,
        service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
        tid=recent_tid,
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert enqueued == [INTERNAL_SERVICE_KEY_HEARTBEAT]


def _desired_internal_service_keys(
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Return the internal service keys one reconcile pass wants to launch."""

    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )
    manager._tick_internal_services(force=True)
    return enqueued


def test_liveness_monitor_only_does_not_desire_heartbeat(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LivenessMonitor is not a heartbeat dependent, so it must not pull it in."""

    manager, _make_queue = manager_setup
    manager._task_monitor_enabled = False
    manager._liveness_monitor_enabled = True

    assert _desired_internal_service_keys(manager, monkeypatch) == [
        INTERNAL_SERVICE_KEY_LIVENESS_MONITOR
    ]


def test_task_monitor_only_desires_heartbeat_and_task_monitor(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TaskMonitor registers with heartbeat, so heartbeat stays desired with it."""

    manager, _make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._liveness_monitor_enabled = False

    assert _desired_internal_service_keys(manager, monkeypatch) == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]


def test_both_internal_monitors_disabled_desires_no_internal_service(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no enabled dependent, heartbeat is not run as standalone work."""

    manager, _make_queue = manager_setup
    manager._task_monitor_enabled = False
    manager._liveness_monitor_enabled = False

    assert _desired_internal_service_keys(manager, monkeypatch) == []


def test_liveness_monitor_only_convergence_ignores_missing_heartbeat(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle shutdown must not wait on a heartbeat no enabled dependent needs."""

    manager, _make_queue = manager_setup
    manager._task_monitor_enabled = False
    manager._liveness_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._service_state(INTERNAL_SERVICE_KEY_HEARTBEAT).active_tid = None
    manager._service_state(
        INTERNAL_SERVICE_KEY_LIVENESS_MONITOR
    ).active_tid = "1777000000000000450"
    monkeypatch.setattr(manager, "_internal_spawn_pending", lambda: False)

    assert (
        manager._managed_service_convergence_active_reasons(include_autostart=False)
        == ()
    )

    manager._task_monitor_enabled = True

    assert manager._managed_service_convergence_active_reasons(
        include_autostart=False
    ) == ("missing_active_tid",)


def test_managed_service_pong_probe_is_nonblocking(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    service_key = INTERNAL_SERVICE_KEY_TASK_MONITOR
    old_tid = "1777000000000000150"
    _write_managed_service_owner(
        make_queue,
        service_key=service_key,
        tid=old_tid,
    )

    candidates = manager._observed_service_candidates_by_key({service_key})[service_key]

    pending_candidate = next(
        candidate for candidate in candidates if candidate.tid == old_tid
    )
    assert pending_candidate.state == "uncertain"
    assert pending_candidate.source == "service-registry-pong"
    assert pending_candidate.reason == "ping_pending"
    probe = _service_probe_for(
        manager,
        tid=old_tid,
        source="service-registry-pong",
    )
    target_ctrl_in = f"T{old_tid}.ctrl_in"
    ping_messages = [json.loads(item) for item in drain(make_queue(target_ctrl_in))]
    assert ping_messages == [
        {
            "command": CONTROL_PING,
            "request_id": probe.request_id,
            "reply_to": manager._queue_names["ctrl_in"],
        }
    ]

    _write_service_pong(manager, make_queue, probe)
    candidates = manager._observed_service_candidates_by_key({service_key})[service_key]

    live_candidate = next(
        candidate for candidate in candidates if candidate.tid == old_tid
    )
    assert live_candidate.state == "live"
    assert live_candidate.source == "service-registry-pong"
    assert probe.key not in manager._service_probe_pending


def test_pending_service_probe_keeps_live_tracked_key_in_evidence_scope(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    service_key = INTERNAL_SERVICE_KEY_TASK_MONITOR
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._task_monitor_enabled = True
    probe_key = manager._service_probe_key(
        source="service-registry-pong",
        service_key=service_key,
        tid="1777000000000000159",
        timestamp=1,
    )
    manager._service_probe_pending[probe_key] = manager_mod._ServicePendingPongProbe(
        key=probe_key,
        service_key=service_key,
        tid="1777000000000000159",
        row_timestamp=1,
        source="service-registry-pong",
        request_id="pending-duplicate",
        created_turn=manager._loop_iteration,
    )
    monkeypatch.setattr(
        manager, "_pending_service_keys", lambda *_args, **_kwargs: set()
    )
    monkeypatch.setattr(
        manager,
        "_tracked_service_candidate",
        lambda key, **_kwargs: manager_mod.ServiceCandidate(
            key=key,
            tid="1777000000000000100",
            state="live",
            source="manager-child",
        ),
    )
    observed_scopes: list[set[str]] = []

    def observe(desired_keys: set[str], **_kwargs: object) -> dict[str, list[Any]]:
        observed_scopes.append(set(desired_keys))
        return {key: [] for key in desired_keys}

    monkeypatch.setattr(manager, "_observed_service_candidates_by_key", observe)
    monkeypatch.setattr(
        manager, "_tick_managed_service", lambda *_args, **_kwargs: None
    )

    manager._reconcile_managed_services(include_autostart=False)

    assert observed_scopes == [{service_key}]


@pytest.mark.parametrize(
    ("child_pid", "taskspec_pid", "expected"),
    [
        (42, 9, 42),
        (True, 9, 9),
        (0, 9, 9),
        (-1, None, None),
    ],
)
def test_service_candidate_pid_prefers_positive_non_boolean_child_pid(
    child_pid: object,
    taskspec_pid: int | None,
    expected: int | None,
) -> None:
    payload: dict[str, object] = {"child_pid": child_pid}
    if taskspec_pid is not None:
        payload["taskspec"] = {"state": {"pid": taskspec_pid}}

    assert Manager._service_candidate_pid(payload) == expected


def test_managed_service_no_pong_resolves_only_on_later_ordinary_pass(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    service_key = INTERNAL_SERVICE_KEY_TASK_MONITOR
    old_tid = "1777000000000000151"
    _write_managed_service_owner(
        make_queue,
        service_key=service_key,
        tid=old_tid,
    )

    candidates = manager._observed_service_candidates_by_key({service_key})[service_key]
    pending_candidate = next(
        candidate for candidate in candidates if candidate.tid == old_tid
    )
    assert pending_candidate.reason == "ping_pending"
    probe = _service_probe_for(
        manager,
        tid=old_tid,
        source="service-registry-pong",
    )
    target_ctrl_in = f"T{old_tid}.ctrl_in"
    assert make_queue(target_ctrl_in).stats().total == 1
    manager._loop_iteration += 1

    pending = manager._advance_service_pong_probe(
        probe,
        timestamp=probe.row_timestamp,
        metadata={},
        resolve_unanswered=False,
    )
    assert pending is not None and pending.reason == "ping_pending"
    assert probe.key in manager._service_probe_pending

    candidate = manager._advance_service_pong_probe(
        probe,
        timestamp=probe.row_timestamp,
        metadata={},
        resolve_unanswered=True,
    )
    assert candidate is None
    assert make_queue(target_ctrl_in).stats().total == 1
    assert probe.key not in manager._service_probe_pending


def test_managed_service_malformed_pong_is_consumed_without_resolving_probe(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    service_key = INTERNAL_SERVICE_KEY_TASK_MONITOR
    old_tid = "1777000000000000152"
    _write_managed_service_owner(
        make_queue,
        service_key=service_key,
        tid=old_tid,
    )

    candidates = manager._observed_service_candidates_by_key({service_key})[service_key]
    pending_candidate = next(
        candidate for candidate in candidates if candidate.tid == old_tid
    )
    assert pending_candidate.reason == "ping_pending"
    probe = _service_probe_for(
        manager,
        tid=old_tid,
        source="service-registry-pong",
    )
    manager_ctrl_in = make_queue(manager._queue_names["ctrl_in"])
    manager_ctrl_in.write(
        json.dumps(
            {
                "command": CONTROL_PING,
                "status": "ok",
                "message": "PONG",
                "request_id": probe.request_id,
                "tid": old_tid,
            }
        )
    )
    manager._drain_control_queue_first()

    assert manager_ctrl_in.peek_one() is None
    assert manager._service_probe_pending[probe.key].pong is None


def test_complete_service_scan_retires_absent_probe_but_failed_scan_retains_it(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    service_key = INTERNAL_SERVICE_KEY_TASK_MONITOR
    probe_key = manager._service_probe_key(
        source="service-registry-pong",
        service_key=service_key,
        tid="1777000000000000199",
        timestamp=1,
    )

    def add_pending() -> None:
        manager._service_probe_pending[probe_key] = (
            manager_mod._ServicePendingPongProbe(
                key=probe_key,
                service_key=service_key,
                tid="1777000000000000199",
                row_timestamp=1,
                source="service-registry-pong",
                request_id="missing-service",
                created_turn=manager._loop_iteration,
            )
        )

    drain(make_queue(WEFT_SERVICES_REGISTRY_QUEUE))
    add_pending()
    manager._observed_service_candidates_by_key({service_key})
    assert probe_key not in manager._service_probe_pending

    class FailedRegistryQueue:
        def peek_generator(self, **_kwargs: object) -> Iterator[tuple[str, int]]:
            raise RuntimeError("registry unavailable")

    original_queue = manager._queue
    add_pending()
    monkeypatch.setattr(
        manager,
        "_queue",
        lambda name: (
            cast(Queue, FailedRegistryQueue())
            if name == WEFT_SERVICES_REGISTRY_QUEUE
            else original_queue(name)
        ),
    )
    candidates = manager._observed_service_candidates_by_key({service_key})
    assert probe_key in manager._service_probe_pending
    assert [
        (candidate.tid, candidate.state, candidate.reason)
        for candidate in candidates[service_key]
    ] == [("1777000000000000199", "uncertain", "ping_pending")]


def test_failed_service_registry_scan_does_not_spawn_over_pending_probe(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    service_key = INTERNAL_SERVICE_KEY_TASK_MONITOR
    tid = "1777000000000000201"
    probe_key = manager._service_probe_key(
        source="service-registry-pong",
        service_key=service_key,
        tid=tid,
        timestamp=1,
    )
    manager._service_probe_pending[probe_key] = manager_mod._ServicePendingPongProbe(
        key=probe_key,
        service_key=service_key,
        tid=tid,
        row_timestamp=1,
        source="service-registry-pong",
        request_id="pending-service",
        created_turn=manager._loop_iteration,
    )
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE

    class FailedRegistryQueue:
        def peek_generator(self, **_kwargs: object) -> Iterator[tuple[str, int]]:
            raise RuntimeError("registry unavailable")

    original_queue = manager._queue
    monkeypatch.setattr(
        manager,
        "_queue",
        lambda name: (
            cast(Queue, FailedRegistryQueue())
            if name == WEFT_SERVICES_REGISTRY_QUEUE
            else original_queue(name)
        ),
    )
    monkeypatch.setattr(
        manager,
        "_pending_service_keys",
        lambda *_args, **_kwargs: {INTERNAL_SERVICE_KEY_HEARTBEAT},
    )
    monkeypatch.setattr(
        manager, "_tracked_service_candidate", lambda *_args, **_kwargs: None
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._reconcile_managed_services(include_autostart=False)

    assert service_key not in enqueued
    assert probe_key in manager._service_probe_pending


def test_managed_service_observation_preserves_other_owner_history(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    first_tid = "1777000000000000600"
    second_tid = "1777000000000000601"
    _write_managed_service_owner(
        make_queue,
        service_key=INTERNAL_SERVICE_KEY_HEARTBEAT,
        tid=first_tid,
    )
    _write_managed_service_owner(
        make_queue,
        service_key=INTERNAL_SERVICE_KEY_HEARTBEAT,
        tid=first_tid,
        status="terminal",
    )
    _write_managed_service_owner(
        make_queue,
        service_key=INTERNAL_SERVICE_KEY_HEARTBEAT,
        tid=second_tid,
    )

    manager._observed_service_candidates_by_key({INTERNAL_SERVICE_KEY_HEARTBEAT})

    rows = [
        payload
        for payload in _managed_service_owner_rows(make_queue)
        if payload.get("service_key") == INTERNAL_SERVICE_KEY_HEARTBEAT
    ]
    assert [(row.get("owner_tid"), row.get("status")) for row in rows] == [
        (first_tid, "active"),
        (first_tid, "terminal"),
        (second_tid, "active"),
    ]


def test_managed_service_dead_registered_pid_is_terminal_without_recent_grace(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    recent_tid = str(time.time_ns())
    _write_managed_service_owner(
        make_queue,
        service_key=INTERNAL_SERVICE_KEY_HEARTBEAT,
        tid=recent_tid,
        runtime_handle=_host_runtime_handle(15251),
    )
    monkeypatch.setattr(
        manager_mod,
        "handle_has_live_host_process",
        lambda handle: False,
    )

    candidates = manager._observed_service_candidates_by_key(
        {INTERNAL_SERVICE_KEY_HEARTBEAT}
    )[INTERNAL_SERVICE_KEY_HEARTBEAT]

    stale = next(candidate for candidate in candidates if candidate.tid == recent_tid)
    assert stale.state == "terminal"
    assert stale.reason == "registered host pid is not live"


def test_task_monitor_duplicate_live_candidates_get_kill_signal(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    canonical_tid = "1777000000000000200"
    duplicate_tid = "1777000000000000300"
    for tid in (canonical_tid, duplicate_tid):
        _write_managed_service_owner(
            make_queue,
            service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
            tid=tid,
            runtime_handle=_host_runtime_handle(os.getpid()),
        )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    killed: list[int] = []
    monkeypatch.setattr(
        manager_mod,
        "kill_process_tree",
        lambda pid, *, timeout=0.5: record_and_return(killed, pid, {pid}),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert make_queue(f"T{canonical_tid}.ctrl_in").read_one() is None
    assert make_queue(f"T{duplicate_tid}.ctrl_in").read_one() == encode_control_message(
        CONTROL_KILL
    )
    assert killed == []
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR not in enqueued


def test_liveness_monitor_duplicate_live_candidates_converge(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = False
    manager._liveness_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    canonical_tid = "1777000000000000250"
    duplicate_tid = "1777000000000000350"
    for tid in (canonical_tid, duplicate_tid):
        _write_managed_service_owner(
            make_queue,
            service_key=INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
            tid=tid,
            runtime_handle=_host_runtime_handle(os.getpid()),
        )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert make_queue(f"T{canonical_tid}.ctrl_in").read_one() is None
    assert make_queue(f"T{duplicate_tid}.ctrl_in").read_one() == encode_control_message(
        CONTROL_KILL
    )
    assert INTERNAL_SERVICE_KEY_LIVENESS_MONITOR not in enqueued


def test_internal_service_trust_preserves_endpoint_asymmetry() -> None:
    def metadata(key: str, role: str, endpoint: str | None) -> dict[str, object]:
        result: dict[str, object] = {
            "internal": True,
            "role": role,
            INTERNAL_SERVICE_KEY_METADATA_KEY: key,
        }
        if endpoint is not None:
            result[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = endpoint
        return result

    heartbeat = metadata(
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        "heartbeat_service",
        INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    )
    assert (
        Manager._trusted_internal_service_key(
            heartbeat,
            runtime_class=INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
        )
        == INTERNAL_SERVICE_KEY_HEARTBEAT
    )
    heartbeat[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = "wrong"
    assert (
        Manager._trusted_internal_service_key(
            heartbeat,
            runtime_class=INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
        )
        is None
    )

    task_monitor = metadata(
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
        "task_monitor",
        "legacy-compatible-endpoint",
    )
    assert (
        Manager._trusted_internal_service_key(
            task_monitor,
            runtime_class=INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
        )
        == INTERNAL_SERVICE_KEY_TASK_MONITOR
    )

    liveness_monitor = metadata(
        INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
        "liveness_monitor",
        None,
    )
    assert (
        Manager._trusted_internal_service_key(
            liveness_monitor,
            runtime_class=INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR,
        )
        == INTERNAL_SERVICE_KEY_LIVENESS_MONITOR
    )
    liveness_monitor[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = "forbidden"
    assert (
        Manager._trusted_internal_service_key(
            liveness_monitor,
            runtime_class=INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR,
        )
        is None
    )


def test_task_monitor_duplicate_manager_spawned_candidates_do_not_force_kill_raw_pid(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    canonical_tid = "1777000000000000400"
    duplicate_tid = "1777000000000000500"
    canonical_pid = 424200
    duplicate_pid = 424201
    for tid, _pid in (
        (canonical_tid, canonical_pid),
        (duplicate_tid, duplicate_pid),
    ):
        _write_managed_service_owner(
            make_queue,
            service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
            tid=tid,
            runtime_handle=_host_runtime_handle(os.getpid()),
        )
    monkeypatch.setattr(manager_mod, "pid_is_live", lambda pid: pid is not None)
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    killed: list[tuple[int, float]] = []

    def _record_kill(pid: int, *, timeout: float = 0.5) -> set[int]:
        killed.append((pid, timeout))
        return {pid}

    monkeypatch.setattr(manager_mod, "kill_process_tree", _record_kill)
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert make_queue(f"T{canonical_tid}.ctrl_in").read_one() is None
    assert make_queue(f"T{duplicate_tid}.ctrl_in").read_one() == encode_control_message(
        CONTROL_KILL
    )
    assert killed == []
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR not in enqueued


def test_task_monitor_duplicate_tracked_child_force_kills_owned_process(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    canonical_tid = "1777000000000000600"
    duplicate_tid = "1777000000000000700"
    duplicate_pid = 424701

    class FakeLiveProcess:
        exitcode = None

        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def kill(self) -> None:
            pass

    manager._child_processes[duplicate_tid] = ManagedChild(
        process=cast(BaseProcess, FakeLiveProcess(duplicate_pid)),
        ctrl_queue=f"T{duplicate_tid}.ctrl_in",
        ctrl_out_queue=f"T{duplicate_tid}.ctrl_out",
        persistent=True,
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    )
    killed: list[tuple[int, float]] = []

    def _record_kill(pid: int, *, timeout: float = 0.5) -> set[int]:
        killed.append((pid, timeout))
        return {pid}

    monkeypatch.setattr(manager_mod, "kill_process_tree", _record_kill)

    manager._terminate_duplicate_service_candidates(
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
        canonical_tid=canonical_tid,
        candidates=[
            manager_mod.ServiceCandidate(
                key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
                tid=canonical_tid,
                state="live",
                source="control-pong",
            ),
            manager_mod.ServiceCandidate(
                key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
                tid=duplicate_tid,
                state="live",
                source="manager-child",
            ),
        ],
    )

    assert make_queue(f"T{duplicate_tid}.ctrl_in").read_one() == encode_control_message(
        CONTROL_KILL
    )
    assert killed == [(duplicate_pid, 0.2)]


def test_task_monitor_duplicate_runtime_handle_force_kills_scoped_host_pid(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    canonical_tid = "1777000000000000800"
    duplicate_tid = "1777000000000000900"
    duplicate_pid = 424901
    for tid in (canonical_tid, duplicate_tid):
        _write_managed_service_owner(
            make_queue,
            service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
            tid=tid,
            runtime_handle=_host_runtime_handle(duplicate_pid)
            if tid == duplicate_tid
            else _host_runtime_handle(424900),
        )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    monkeypatch.setattr(
        manager_mod, "handle_has_live_host_process", lambda handle: True
    )
    monkeypatch.setattr(
        manager_mod,
        "live_host_processes_from_handle",
        lambda handle: ((int(handle.id), None),),
    )
    killed: list[tuple[int, float]] = []

    def _record_kill(pid: int, *, timeout: float = 0.5) -> set[int]:
        killed.append((pid, timeout))
        return {pid}

    monkeypatch.setattr(manager_mod, "kill_process_tree", _record_kill)

    manager._tick_internal_services(force=True)

    assert make_queue(f"T{canonical_tid}.ctrl_in").read_one() is None
    assert make_queue(f"T{duplicate_tid}.ctrl_in").read_one() == encode_control_message(
        CONTROL_KILL
    )
    assert killed == [(duplicate_pid, 0.2)]


def test_task_monitor_internal_pending_spawn_request_blocks_duplicate_restart(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._queue_names["internal_inbox"] = WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
    manager._queue_names["internal_reserved"] = f"T{manager.tid}.internal_reserved"
    make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE).write(
        json.dumps(manager._build_task_monitor_spawn_payload())
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert enqueued == [INTERNAL_SERVICE_KEY_HEARTBEAT]
    assert manager._task_monitor_spawn_pending is True


def test_task_monitor_public_pending_spawn_request_does_not_block_internal_restart(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    manager._queue_names["internal_inbox"] = WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
    manager._queue_names["internal_reserved"] = f"T{manager.tid}.internal_reserved"
    make_queue(WEFT_SPAWN_REQUESTS_QUEUE).write(
        json.dumps(manager._build_task_monitor_spawn_payload())
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert enqueued == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]
    assert manager._task_monitor_spawn_pending is True


def test_task_monitor_spoofed_pending_spawn_without_internal_envelope_does_not_block(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    make_queue(WEFT_SPAWN_REQUESTS_QUEUE).write(
        json.dumps(
            {
                "taskspec": {
                    "name": "spoofed-public-monitor",
                    "spec": {"type": "function", "persistent": True},
                    "metadata": {
                        "internal": True,
                        "role": "task_monitor",
                        INTERNAL_SERVICE_KEY_METADATA_KEY: (
                            INTERNAL_SERVICE_KEY_TASK_MONITOR
                        ),
                        INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY: "ensure",
                    },
                },
                "inbox_message": None,
            }
        )
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert enqueued == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]
    assert manager._task_monitor_spawn_pending is True


def test_process_once_reconciles_internal_services_before_user_spawn_work(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {"WEFT_TASK_MONITOR_ENABLED": False, "WEFT_LIVENESS_MONITOR_ENABLED": False}
        ),
    )
    try:

        class FakeDeadProcess:
            pid = None
            exitcode = 1

            def is_alive(self) -> bool:
                return False

            def join(self, timeout: float | None = None) -> None:
                del timeout

        old_tid = "1777000000000000250"
        manager._task_monitor_enabled = True
        manager._task_monitor_tid = old_tid
        manager._task_monitor_restart_backoff_ns = 0
        manager._child_processes[old_tid] = ManagedChild(
            process=cast(BaseProcess, FakeDeadProcess()),
            ctrl_queue=f"T{old_tid}.ctrl_in",
            ctrl_out_queue=f"T{old_tid}.ctrl_out",
            persistent=True,
            internal_role=INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
        )
        make_queue(WEFT_GLOBAL_LOG_QUEUE).write(
            json.dumps(
                {
                    "tid": old_tid,
                    "status": "killed",
                    "taskspec": {
                        "metadata": {
                            "internal": True,
                            "role": "task_monitor",
                            INTERNAL_RUNTIME_TASK_CLASS_KEY: (
                                INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
                            ),
                            INTERNAL_SERVICE_KEY_METADATA_KEY: (
                                INTERNAL_SERVICE_KEY_TASK_MONITOR
                            ),
                        },
                        "io": {
                            "control": {
                                "ctrl_in": f"T{old_tid}.ctrl_in",
                                "ctrl_out": f"T{old_tid}.ctrl_out",
                            }
                        },
                    },
                }
            )
        )
        make_queue(WEFT_SPAWN_REQUESTS_QUEUE).write("{}")
        monkeypatch.setattr(
            manager,
            "_evaluate_dispatch_ownership",
            lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
        )

        order: list[str] = []

        def record_service_enqueue(service: ManagedServiceSpec) -> bool:
            order.append(f"service:{service.key}")
            return True

        def record_user_work(
            message: str,
            timestamp: int,
            context: QueueMessageContext,
        ) -> None:
            del message, timestamp, context
            order.append("user-work")

        monkeypatch.setattr(
            manager,
            "_enqueue_managed_service_request",
            record_service_enqueue,
        )
        manager._queues[WEFT_SPAWN_REQUESTS_QUEUE].handler = record_user_work

        manager.process_once()

        assert order.index(f"service:{INTERNAL_SERVICE_KEY_TASK_MONITOR}") < (
            order.index("user-work")
        )
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_process_once_launches_service_spawn_after_shared_self_write_wake(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {"WEFT_TASK_MONITOR_ENABLED": False, "WEFT_LIVENESS_MONITOR_ENABLED": False}
        ),
    )
    drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
    manager._task_monitor_enabled = True
    manager._task_monitor_restart_backoff_ns = 0
    manager._last_managed_service_convergence_ns = 0
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    launched: list[str] = []

    def record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        launched.append(child_spec.name)
        return True

    monkeypatch.setattr(manager, "_launch_child_task", record_launch)

    try:
        manager.process_once()
        assert launched == []
        for _ in range(3):
            manager.wait_for_activity(timeout=None)
            manager.process_once()
            if "task-monitor" in launched:
                break
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert "task-monitor" in launched


def test_task_monitor_spoofed_public_metadata_does_not_claim_singleton(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    old_tid = "1777000000000000300"
    make_queue(WEFT_GLOBAL_LOG_QUEUE).write(
        json.dumps(
            {
                "tid": old_tid,
                "status": "running",
                "taskspec": {
                    "metadata": {
                        "internal": True,
                        "role": "task_monitor",
                        INTERNAL_SERVICE_KEY_METADATA_KEY: (
                            INTERNAL_SERVICE_KEY_TASK_MONITOR
                        ),
                        INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY: "ensure",
                    },
                    "io": {
                        "control": {
                            "ctrl_in": f"T{old_tid}.ctrl_in",
                            "ctrl_out": f"T{old_tid}.ctrl_out",
                        }
                    },
                },
            }
        )
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert enqueued == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]
    assert manager._task_monitor_tid is None


def test_task_monitor_latest_terminal_log_overrides_older_running_evidence(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    old_tid = "1777000000000000400"
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    metadata = {
        "internal": True,
        "role": "task_monitor",
        INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
        INTERNAL_SERVICE_KEY_METADATA_KEY: INTERNAL_SERVICE_KEY_TASK_MONITOR,
    }
    log_queue.write(
        json.dumps(
            {
                "tid": old_tid,
                "status": "running",
                "taskspec": {
                    "metadata": metadata,
                    "io": {
                        "control": {
                            "ctrl_in": f"T{old_tid}.ctrl_in",
                            "ctrl_out": f"T{old_tid}.ctrl_out",
                        }
                    },
                },
            }
        )
    )
    log_queue.write(
        json.dumps(
            {
                "tid": old_tid,
                "status": "completed",
                "taskspec": {
                    "metadata": metadata,
                    "io": {
                        "control": {
                            "ctrl_in": f"T{old_tid}.ctrl_in",
                            "ctrl_out": f"T{old_tid}.ctrl_out",
                        }
                    },
                },
            }
        )
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert enqueued == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]
    assert manager._task_monitor_tid is None


def test_task_monitor_matching_pong_blocks_duplicate_restart(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    old_tid = "1777000000000000200"
    _write_managed_service_owner(
        make_queue,
        service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
        tid=old_tid,
        runtime_handle=_host_runtime_handle(os.getpid()),
    )
    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: record_and_return(enqueued, service.key, True),
    )

    manager._tick_internal_services(force=True)

    assert enqueued == [INTERNAL_SERVICE_KEY_HEARTBEAT]
    assert manager._task_monitor_tid == old_tid


def test_internal_task_monitor_child_does_not_block_idle_shutdown(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_manager_spec(
        unique_tid,
        f"manager.{unique_tid}.inbox",
        f"manager.{unique_tid}.ctrl_in",
        f"manager.{unique_tid}.ctrl_out",
        idle_timeout=0.01,
    )
    manager = Manager(db_path, spec)

    class FakeProcess:
        pid = None
        exitcode = None

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            del timeout

    try:
        manager._child_processes["monitor-child"] = ManagedChild(
            process=cast(BaseProcess, FakeProcess()),
            ctrl_queue=None,
            persistent=True,
            internal_role=INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
        )
        manager._last_activity_ns = time.time_ns() - 1_000_000_000
        monkeypatch.setattr(
            manager,
            "_update_idle_activity_from_broker",
            lambda *, force=False: None,
        )

        manager.process_once()

        assert manager.should_stop is True
        assert manager.taskspec.state.status == "completed"
    finally:
        manager._child_processes.clear()
        manager.cleanup()


def test_manager_idle_shutdown_waits_for_missing_internal_service(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_manager_spec(unique_tid, idle_timeout=0.01)
    manager = Manager(
        db_path,
        spec,
        config=load_config({"WEFT_TASK_MONITOR_ENABLED": True}),
    )
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
    state = manager._service_state(INTERNAL_SERVICE_KEY_HEARTBEAT)
    state.launched_once = True
    state.active_tid = None
    manager._last_activity_ns = time.time_ns() - 1_000_000_000
    manager._last_managed_service_convergence_ns = time.time_ns()
    monkeypatch.setattr(
        manager,
        "_update_idle_activity_from_broker",
        lambda *, force=False: None,
    )

    try:
        manager.process_once()
        assert manager.should_stop is False
    finally:
        manager.cleanup()


def test_manager_process_once_skips_idle_broker_probe_when_idle_disabled(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_manager_spec(
        unique_tid,
        f"manager.{unique_tid}.inbox",
        f"manager.{unique_tid}.ctrl_in",
        f"manager.{unique_tid}.ctrl_out",
        idle_timeout=0.0,
    )
    manager = Manager(
        db_path,
        spec,
        config=load_config(
            {"WEFT_TASK_MONITOR_ENABLED": False, "WEFT_LIVENESS_MONITOR_ENABLED": False}
        ),
    )
    try:
        now_ns = time.time_ns()
        manager._autostart_enabled = False
        manager._last_managed_service_convergence_ns = now_ns
        manager._last_leader_check_ns = now_ns
        manager._last_registry_heartbeat_ns = now_ns
        monkeypatch.setattr(
            manager,
            "_update_idle_activity_from_broker",
            lambda *, force=False: pytest.fail(
                "disabled idle timeout should not probe broker activity"
            ),
        )

        manager.process_once()
    finally:
        manager.cleanup()


def test_manager_seeded_child_inbox_does_not_enter_queue_cache(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup

    class FakeProcess:
        pid = None
        exitcode = 0

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    monkeypatch.setattr(
        manager_mod,
        "launch_task_process",
        lambda *args, **kwargs: FakeProcess(),
    )
    child_spec = TaskSpec(
        tid=str(time.time_ns()),
        name="seeded-child",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
        ),
        io=IOSection(
            inputs={"inbox": "seeded.inbox"},
            outputs={"outbox": "seeded.outbox"},
            control={"ctrl_in": "seeded.ctrl_in", "ctrl_out": "seeded.ctrl_out"},
        ),
        state=StateSection(),
        metadata={},
    )

    assert manager._launch_child_task(child_spec, {"args": ["payload"]}) is True
    assert "seeded.inbox" not in manager._queue_cache
    with make_queue("seeded.inbox") as reader:
        assert drain(reader) == [json.dumps({"args": ["payload"]})]


def test_manager_cleanup_waits_for_active_child_launch_worker(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    child_spec = manager._build_child_spec(make_child_spec(size=1024), time.time_ns())
    assert child_spec is not None

    launch_entered = threading.Event()
    release_launch = threading.Event()
    cleanup_returned = threading.Event()
    cleanup_errors: list[BaseException] = []
    original_stop_worker_lanes = manager._stop_worker_lanes

    def blocked_launch(*args: object, **kwargs: object) -> FakeLaunchProcess:
        del args, kwargs
        launch_entered.set()
        assert release_launch.wait(timeout=10.0)
        return FakeLaunchProcess(alive=False)

    def fast_stop_worker_lanes(deadline: float) -> None:
        original_stop_worker_lanes(min(deadline, time.monotonic() + 0.05))

    def skip_full_queue_resource_cleanup(deadline: float) -> None:
        del deadline

    def run_cleanup() -> None:
        try:
            manager.cleanup()
        except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-338] exception
            cleanup_errors.append(exc)
        finally:
            cleanup_returned.set()

    class CleanupSignal(BaseException):
        pass

    signal = CleanupSignal("cleanup interrupted")
    real_cleanup = manager.cleanup

    def fail_cleanup() -> None:
        raise signal

    monkeypatch.setattr(manager, "cleanup", fail_cleanup)
    sentinel_thread = threading.Thread(target=run_cleanup)
    sentinel_thread.start()
    sentinel_thread.join(timeout=1.0)
    assert not sentinel_thread.is_alive()
    assert cleanup_errors == [signal]
    cleanup_errors.clear()
    cleanup_returned.clear()
    monkeypatch.setattr(manager, "cleanup", real_cleanup)

    monkeypatch.setattr(manager_mod, "launch_task_process", blocked_launch)
    monkeypatch.setattr(manager, "_stop_worker_lanes", fast_stop_worker_lanes)
    monkeypatch.setattr(
        manager,
        "_cleanup_base_task_resources",
        skip_full_queue_resource_cleanup,
    )

    assert manager._launch_child_task(child_spec, None) is True
    assert launch_entered.wait(timeout=3.0)

    cleanup_thread = threading.Thread(target=run_cleanup, daemon=True)
    cleanup_thread.start()
    try:
        assert not cleanup_returned.wait(timeout=0.25), (
            "Manager.cleanup() returned while a child launch worker was still active"
        )
    finally:
        release_launch.set()
        cleanup_thread.join(timeout=5.0)

    if cleanup_thread.is_alive():
        assert cleanup_thread.ident is not None
        frame = sys._current_frames().get(cleanup_thread.ident)
        stack = (
            "".join(traceback.format_stack(frame, limit=24))
            if frame is not None
            else "<no frame>"
        )
        pytest.fail(f"cleanup thread did not exit:\n{stack}")
    assert not cleanup_errors


def test_manager_late_child_launch_self_reaps_after_cleanup_deadline(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-009] exception
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    psutil = pytest.importorskip("psutil")
    manager, _make_queue = manager_setup
    child_spec = manager._build_child_spec(make_child_spec(size=1024), time.time_ns())
    assert child_spec is not None

    locked_dir = tmp_path / "late-launch-tree"
    locked_dir.mkdir()
    parent_script, child_script = _write_descendant_process_scripts(locked_dir)
    child_pidfile = locked_dir / "late-launch-child.pid"
    launch_entered = threading.Event()
    release_launch = threading.Event()
    termination_entered = threading.Event()
    allow_termination = threading.Event()
    stop_errors: list[BaseException] = []
    tree_kills: list[tuple[int, float]] = []
    process_start_timeout = 20.0 if os.name == "nt" else 10.0

    class RealLaunchProcess:
        def __init__(self, process: subprocess.Popen[bytes]) -> None:
            self._process = process
            self.pid = process.pid

        @property
        def exitcode(self) -> int | None:
            return self._process.poll()

        def is_alive(self) -> bool:
            return self._process.poll() is None

        def kill(self) -> None:
            if self._process.poll() is None:
                self._process.kill()

    launched: dict[str, RealLaunchProcess] = {}

    def blocked_launch(*args: object, **kwargs: object) -> RealLaunchProcess:
        del args, kwargs
        process = RealLaunchProcess(
            subprocess.Popen(
                [
                    sys.executable,
                    str(parent_script),
                    str(child_script),
                    str(child_pidfile),
                ],
                cwd=locked_dir,
            )
        )
        launched["process"] = process
        launch_entered.set()
        release_launch.wait()
        return process

    monkeypatch.setattr(manager_mod, "launch_task_process", blocked_launch)
    monkeypatch.setattr(manager_mod, "terminate_process_tree", lambda *a, **k: set())
    real_kill_process_tree = manager_mod.kill_process_tree

    def record_tree_kill(pid: int, *, timeout: float) -> set[int]:
        tree_kills.append((pid, timeout))
        return real_kill_process_tree(pid, timeout=timeout)

    monkeypatch.setattr(manager_mod, "kill_process_tree", record_tree_kill)

    def block_termination(_deadline: float) -> None:
        termination_entered.set()
        allow_termination.wait()

    def skip_full_queue_resource_cleanup(deadline: float) -> None:
        del deadline

    def run_stop() -> None:
        try:
            manager.stop(timeout=0.05)
        except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-338] exception
            stop_errors.append(exc)

    class StopSignal(BaseException):
        pass

    signal = StopSignal("stop interrupted")
    real_stop = manager.stop

    def fail_stop(*, timeout: float) -> None:
        del timeout
        raise signal

    monkeypatch.setattr(manager, "stop", fail_stop)
    sentinel_thread = threading.Thread(target=run_stop)
    sentinel_thread.start()
    sentinel_thread.join(timeout=process_start_timeout)
    assert not sentinel_thread.is_alive()
    assert stop_errors == [signal]
    stop_errors.clear()
    monkeypatch.setattr(manager, "stop", real_stop)

    monkeypatch.setattr(manager, "_terminate_children", block_termination)
    monkeypatch.setattr(
        manager,
        "_cleanup_base_task_resources",
        skip_full_queue_resource_cleanup,
    )

    assert manager._launch_child_task(child_spec, None) is True
    process: RealLaunchProcess | None = None
    worker_pid: int | None = None
    stop_thread: threading.Thread | None = None
    try:
        assert launch_entered.wait(timeout=process_start_timeout)
        process = launched["process"]
        worker_pid = _wait_for_pidfile(
            child_pidfile,
            timeout=process_start_timeout,
        )
        assert _process_running(worker_pid)

        stop_thread = threading.Thread(target=run_stop)
        stop_thread.start()
        assert termination_entered.wait(timeout=process_start_timeout)
        assert process.is_alive() is True

        release_launch.set()
        deadline = time.monotonic() + (20.0 if os.name == "nt" else 5.0)
        while time.monotonic() < deadline:
            if not process.is_alive() and not _process_running(worker_pid):
                break
            time.sleep(0.01)

        assert process.is_alive() is False
        assert not _process_running(worker_pid)
        assert tree_kills == [(process.pid, 0.0)]
        assert child_spec.tid not in manager._child_processes
    finally:
        release_launch.set()
        allow_termination.set()
        if stop_thread is not None:
            stop_thread.join(timeout=process_start_timeout)
        process = process or launched.get("process")
        if process is not None and process.is_alive():
            real_kill_process_tree(process.pid, timeout=2.0)
        if worker_pid is not None and _process_running(worker_pid):
            try:
                psutil.Process(worker_pid).kill()
            except psutil.Error:
                pass
            _wait_for_pid_exit(worker_pid, timeout=2.0)
        shutil.rmtree(locked_dir)

    assert stop_thread is not None
    if stop_thread.is_alive():
        assert stop_thread.ident is not None
        frame = sys._current_frames().get(stop_thread.ident)
        stack = (
            "".join(traceback.format_stack(frame, limit=24))
            if frame is not None
            else "<no frame>"
        )
        pytest.fail(f"stop thread did not exit:\n{stack}")
    assert not stop_errors
    assert not locked_dir.exists()


def test_manager_terminal_envelope_does_not_cache_child_ctrl_out_queue(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    child_tid = str(time.time_ns())
    child_ctrl_out = f"T{child_tid}.ctrl_out"

    class FakeProcess:
        pid = None
        exitcode = 1

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    child = ManagedChild(
        process=cast(BaseProcess, FakeProcess()),
        ctrl_queue=None,
        ctrl_out_queue=child_ctrl_out,
    )

    manager._write_manager_terminal_envelope(child_tid, child)

    assert child_ctrl_out not in manager._queue_cache
    with make_queue(child_ctrl_out) as reader:
        messages = drain(reader)
    assert len(messages) == 1
    payload = json.loads(messages[0])
    assert payload["type"] == TERMINAL_ENVELOPE_TYPE
    assert payload["source"] == "manager"
    assert payload["tid"] == child_tid
    assert payload["status"] == "failed"
    assert payload["error"] == WRAPPER_LOST_ERROR
    assert payload["return_code"] == 1


def test_manager_terminal_envelope_skips_when_task_terminal_proof_exists(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    child_tid = str(time.time_ns())
    child_ctrl_out = f"T{child_tid}.ctrl_out"
    terminal_payload = json.dumps(
        {
            "type": TERMINAL_ENVELOPE_TYPE,
            "source": "task",
            "tid": child_tid,
            "status": "completed",
            "timestamp": time.time_ns(),
            "return_code": 0,
        }
    )

    class FakeProcess:
        pid = None
        exitcode = 1

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    child = ManagedChild(
        process=cast(BaseProcess, FakeProcess()),
        ctrl_queue=None,
        ctrl_out_queue=child_ctrl_out,
    )

    with make_queue(child_ctrl_out) as queue:
        queue.write(terminal_payload)
        manager._write_manager_terminal_envelope(child_tid, child)
        assert drain(queue) == [terminal_payload]
    assert child_ctrl_out not in manager._queue_cache


def test_manager_registry_entries(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    entries = [json.loads(item) for item in drain(registry_queue)]
    relevant = [entry for entry in entries if entry.get("owner_tid") == manager.tid]
    assert len(relevant) == 1
    assert relevant[0]["status"] == "active"
    manager.cleanup()
    entries = [json.loads(item) for item in drain(registry_queue)]
    relevant = [entry for entry in entries if entry.get("owner_tid") == manager.tid]
    assert len(relevant) == 1
    assert relevant[0]["status"] == "stopped"


def test_manager_bootstrap_discards_v1_registry_rows(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    registry = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    registry.write(json.dumps({"schema": "weft.service_owner.v1"}))
    config = dict(load_config())
    config["AUTOSTART_TASKS"] = False

    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    try:
        rows = [json.loads(body) for body in registry.peek_generator()]
        assert all(row.get("schema") != "weft.service_owner.v1" for row in rows)
        assert any(
            row.get("schema") == SERVICE_OWNER_SCHEMA
            and row.get("owner_tid") == manager.tid
            for row in rows
        )
    finally:
        manager.cleanup()


def test_manager_bootstrap_rejects_future_schema_before_v1_discard(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    registry = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    v1_id = registry.write(json.dumps({"schema": "weft.service_owner.v1"}))
    future_id = registry.write(json.dumps({"schema": "weft.service_owner.v3"}))
    config = dict(load_config())
    config["AUTOSTART_TASKS"] = False

    with pytest.raises(ValueError, match="future service-owner schema"):
        Manager(db_path, make_manager_spec(unique_tid), config=config)

    rows = list(registry.peek_generator(with_timestamps=True, include_claimed=True))
    assert {message_id for _body, message_id in rows} == {v1_id, future_id}


def test_manager_refreshes_active_registry_heartbeat(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    before = pending_timestamps(registry_queue)

    monkeypatch.setattr(manager_mod, "MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS", 0.0)
    manager._refresh_manager_registration()

    after = pending_timestamps(registry_queue)
    entries = [json.loads(item) for item in drain(registry_queue)]
    relevant = [entry for entry in entries if entry.get("owner_tid") == manager.tid]
    assert len(before) == 1
    assert len(after) == 1
    assert after != before
    assert len(relevant) == 1
    assert relevant[0]["status"] == "active"


def test_manager_supersedes_fresh_higher_tid_active_refresh(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    higher_tid = str(int(manager.tid) + 1)
    first_higher_payload = _manager_service_payload(
        manager,
        tid=higher_tid,
        runtime_handle=_host_runtime_handle(os.getpid()),
    )
    registry_queue.write(json.dumps(first_higher_payload))
    first_observed_timestamp = pending_timestamps(registry_queue)[-1]

    active = manager._active_dispatch_manager_records()

    assert active is not None
    assert higher_tid not in active
    rows = _managed_service_owner_rows(make_queue)
    higher_rows = [row for row in rows if row.get("owner_tid") == higher_tid]
    assert [row["status"] for row in higher_rows] == [
        SERVICE_STATUS_ACTIVE,
        SERVICE_STATUS_SUPERSEDED,
    ]
    assert (
        higher_rows[-1]["metadata"]["supersession_observed_timestamp"]
        == first_observed_timestamp
    )

    active = manager._active_dispatch_manager_records()
    assert active is not None
    rows = _managed_service_owner_rows(make_queue)
    higher_rows = [row for row in rows if row.get("owner_tid") == higher_tid]
    assert [row["status"] for row in higher_rows] == [
        SERVICE_STATUS_ACTIVE,
        SERVICE_STATUS_SUPERSEDED,
    ]

    second_higher_payload = _manager_service_payload(
        manager,
        tid=higher_tid,
        runtime_handle=_host_runtime_handle(os.getpid()),
    )
    registry_queue.write(json.dumps(second_higher_payload))
    observed_timestamp = pending_timestamps(registry_queue)[-1]

    active = manager._active_dispatch_manager_records()

    assert active is not None
    assert higher_tid not in active
    rows = _managed_service_owner_rows(make_queue)
    higher_rows = [row for row in rows if row.get("owner_tid") == higher_tid]
    assert [row["status"] for row in higher_rows] == [
        SERVICE_STATUS_ACTIVE,
        SERVICE_STATUS_SUPERSEDED,
        SERVICE_STATUS_ACTIVE,
        SERVICE_STATUS_SUPERSEDED,
    ]
    superseded = higher_rows[-1]
    assert superseded["metadata"]["superseded_by"] == manager.tid
    assert (
        superseded["metadata"]["supersession_reason"]
        == "higher_tid_active_refresh_seen"
    )
    assert (
        superseded["metadata"]["supersession_observed_timestamp"] == observed_timestamp
    )


def test_manager_registry_refresh_preserves_expired_peer_and_malformed_rows(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    drain(registry_queue)
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=str(int(manager.tid) - 10),
                name="old-manager",
                runtime_handle=_host_runtime_handle(os.getpid()),
            )
        )
    )
    managed_payload = build_service_owner_payload(
        service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
        service_type=SERVICE_TYPE_MANAGED,
        owner_tid=str(int(manager.tid) - 9),
        status="active",
        name="task-monitor",
    )
    registry_queue.write(json.dumps(managed_payload))
    registry_queue.write(
        json.dumps(
            {
                "schema": SERVICE_OWNER_SCHEMA,
                "service_key": "bad",
                "service_type": "manager",
                "owner_tid": "not-a-tid",
                "status": "active",
            }
        )
    )

    monkeypatch.setattr(manager_mod, "MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(
        manager_mod,
        "MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    observed_now_ns = time.time_ns() + 120_000_000_000
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: observed_now_ns)
    manager._refresh_manager_registration()

    entries = [json.loads(item) for item in drain(registry_queue)]
    assert any(entry.get("name") == "old-manager" for entry in entries)
    assert any(entry.get("name") == "task-monitor" for entry in entries)
    assert any(entry.get("owner_tid") == "not-a-tid" for entry in entries)
    assert [
        entry["owner_tid"] for entry in entries if entry.get("owner_tid") == manager.tid
    ] == [manager.tid]


def test_manager_publishes_inactive_when_recent_lower_canonical_manager_exists(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    drain(registry_queue)
    lower_tid = str(int(manager.tid) - 1)
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=lower_tid,
                runtime_handle=_host_runtime_handle(os.getpid()),
            )
        )
    )

    monkeypatch.setattr(manager_mod, "MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS", 0.0)
    manager._refresh_manager_registration()

    entries = [json.loads(item) for item in drain(registry_queue)]
    assert [entry["owner_tid"] for entry in entries] == [lower_tid, manager.tid]
    own_entry = entries[-1]
    assert own_entry["status"] == "draining"


def test_manager_registers_when_lower_canonical_manager_is_stale(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    drain(registry_queue)
    lower_tid = str(int(manager.tid) - 1)
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=lower_tid,
                runtime_handle=_host_runtime_handle(999_999_999),
            )
        )
    )

    monkeypatch.setattr(manager_mod, "MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS", 0.0)
    manager._refresh_manager_registration()

    pending = manager._leader_probe_pending.get(lower_tid)
    assert pending is not None
    manager._loop_iteration += 1
    manager._active_dispatch_manager_records(
        consume_stored=True,
        resolve_unanswered=True,
        retire_absent=True,
    )
    manager._refresh_manager_registration()

    entries = [json.loads(item) for item in drain(registry_queue)]
    tids = {entry["owner_tid"] for entry in entries}
    assert lower_tid in tids
    assert manager.tid in tids


def test_manager_leadership_ping_probe_is_event_routed_and_cadence_owned(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    lower_tid = str(int(manager.tid) - 1)
    ctrl_in_name = f"T{lower_tid}.ctrl_in"
    ctrl_out_name = f"T{lower_tid}.ctrl_out"
    record = _manager_service_record(
        manager,
        tid=lower_tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        ctrl_in=ctrl_in_name,
        ctrl_out=ctrl_out_name,
    )

    proof = manager._manager_pong_dispatch_proof(record, now_ns=time.time_ns())

    assert proof.liveness == "unknown"
    assert proof.reason == "ping_pending"
    pending = manager._leader_probe_pending[lower_tid]
    ping_messages = [json.loads(item) for item in drain(make_queue(ctrl_in_name))]
    assert ping_messages == [
        {
            "command": CONTROL_PING,
            "request_id": pending.request_id,
            "reply_to": manager._queue_names["ctrl_in"],
        }
    ]

    same_turn = manager._manager_pong_dispatch_proof(
        record,
        now_ns=time.time_ns(),
        consume_stored=True,
        resolve_unanswered=True,
    )
    assert same_turn.reason == "ping_pending"
    assert lower_tid in manager._leader_probe_pending

    manager._loop_iteration += 1
    unanswered = manager._manager_pong_dispatch_proof(
        record,
        now_ns=time.time_ns(),
        consume_stored=True,
        resolve_unanswered=True,
    )
    assert unanswered.liveness == "unknown"
    assert unanswered.reason == "ping_unanswered"
    assert lower_tid not in manager._leader_probe_pending


def test_manager_leadership_stores_pong_until_owning_reducer_runs(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    lower_tid = str(int(manager.tid) - 1)
    ctrl_in_name = f"T{lower_tid}.ctrl_in"
    ctrl_out_name = f"T{lower_tid}.ctrl_out"
    record = _manager_service_record(
        manager,
        tid=lower_tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        ctrl_in=ctrl_in_name,
        ctrl_out=ctrl_out_name,
    )
    initial = manager._manager_pong_dispatch_proof(record, now_ns=time.time_ns())
    assert initial.reason == "ping_pending"
    pending = manager._leader_probe_pending[lower_tid]
    _write_manager_pong(
        manager,
        make_queue,
        pending,
        ctrl_in_name=ctrl_in_name,
        ctrl_out_name=ctrl_out_name,
    )

    stored = manager._leader_probe_pending[lower_tid]
    assert stored.pong is not None
    nonowning = manager._manager_pong_dispatch_proof(
        record,
        now_ns=time.time_ns(),
    )
    assert nonowning.reason == "ping_pending"
    assert manager._leader_probe_pending[lower_tid].pong is not None

    proof = manager._manager_pong_dispatch_proof(
        record,
        now_ns=time.time_ns(),
        consume_stored=True,
    )
    assert proof.liveness == "live"
    assert proof.dispatch_eligible is True
    assert proof.source == "control-pong"
    assert lower_tid not in manager._leader_probe_pending


def test_manager_control_reply_drops_ambiguous_pong(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    target_tid = str(int(manager.tid) - 1)
    request_id = "shared-request"
    manager._leader_probe_pending[target_tid] = manager_mod._ManagerPendingPongProbe(
        tid=target_tid,
        row_timestamp=1,
        request_id=request_id,
        created_turn=manager._loop_iteration,
    )
    service_key = manager._service_probe_key(
        source="service-registry-pong",
        service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
        tid=target_tid,
        timestamp=1,
    )
    manager._service_probe_pending[service_key] = manager_mod._ServicePendingPongProbe(
        key=service_key,
        service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
        tid=target_tid,
        row_timestamp=1,
        source="service-registry-pong",
        request_id=request_id,
        created_turn=manager._loop_iteration,
    )
    make_queue(manager._queue_names["ctrl_in"]).write(
        json.dumps(
            {
                "command": CONTROL_PING,
                "status": "ok",
                "message": "PONG",
                "request_id": request_id,
                "tid": target_tid,
                "task_status": "running",
            }
        )
    )

    manager._drain_control_queue_first()

    assert manager._leader_probe_pending[target_tid].pong is None
    assert manager._service_probe_pending[service_key].pong is None


def test_complete_leadership_scan_retires_absent_probe_but_failed_scan_retains_it(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    missing_tid = str(int(manager.tid) - 100)

    def add_pending() -> None:
        manager._leader_probe_pending[missing_tid] = (
            manager_mod._ManagerPendingPongProbe(
                tid=missing_tid,
                row_timestamp=1,
                request_id="missing-manager",
                created_turn=manager._loop_iteration,
            )
        )

    manager._manager_registry_snapshot = {}
    add_pending()
    monkeypatch.setattr(manager, "_update_manager_registry_snapshot", lambda: True)

    manager._active_dispatch_manager_records(retire_absent=True)

    assert missing_tid not in manager._leader_probe_pending

    add_pending()
    monkeypatch.setattr(manager, "_update_manager_registry_snapshot", lambda: False)

    assert manager._active_dispatch_manager_records(retire_absent=True) is None
    assert missing_tid in manager._leader_probe_pending


def test_manager_forced_leadership_pass_does_not_manufacture_no_pong(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    lower_tid = str(int(manager.tid) - 1)
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=lower_tid,
                runtime_handle=_host_runtime_handle(987654321),
            )
        )
    )
    monkeypatch.setattr(
        manager_runtime_mod,
        "detect_container_runtime",
        lambda: ContainerRuntimeDetection(
            runtime="docker",
            markers=("dockerenv",),
            identifier="container123",
        ),
    )
    monkeypatch.setattr(
        manager_runtime_mod,
        "inspect_host_process",
        lambda pid, create_time: HostProcessObservation("stale", "process_absent"),
    )

    assert manager._maybe_yield_leadership(force=True) is False
    pending = manager._leader_probe_pending[lower_tid]
    manager._loop_iteration += 1

    assert manager._maybe_yield_leadership(force=True) is False
    assert manager._leader_probe_pending[lower_tid] == pending
    assert manager.should_stop is False
    rows = _managed_service_owner_rows(make_queue)
    assert any(row.get("owner_tid") == lower_tid for row in rows)


def test_manager_unknown_lower_owner_does_not_suppress_or_yield(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    drain(registry_queue)
    lower_tid = str(int(manager.tid) - 1)
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=lower_tid,
                runtime_handle=_external_supervisor_runtime_handle(),
            )
        )
    )
    lower_timestamp = pending_timestamps(registry_queue)[0]

    monkeypatch.setattr(
        manager_runtime_mod,
        "runtime_liveness_from_registered_probe",
        lambda handle: "unknown",
    )
    monkeypatch.setattr(manager, "_manager_registry_retention_ns", lambda: 1_000)

    assert not manager._recent_lower_canonical_manager_exists(
        registry_queue,
        now_ns=lower_timestamp + 499,
    )
    assert not manager._recent_lower_canonical_manager_exists(
        registry_queue,
        now_ns=lower_timestamp + 500,
    )
    assert manager._maybe_yield_leadership(force=True) is False
    assert manager.should_stop is False


def test_manager_strong_live_lower_owner_can_trigger_immediate_yield(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    lower_tid = str(int(manager.tid) - 1)
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=lower_tid,
                runtime_handle=_external_supervisor_runtime_handle(),
            )
        )
    )

    monkeypatch.setattr(
        manager_runtime_mod,
        "runtime_liveness_from_registered_probe",
        lambda handle: "live",
    )

    assert manager._maybe_yield_leadership(force=True) is True
    assert manager.should_stop is True


def test_manager_pong_from_draining_candidate_is_not_dispatch_eligible(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, _make_queue = manager_setup
    record = _manager_service_record(
        manager,
        tid=str(int(manager.tid) - 1),
        runtime_handle=_external_supervisor_runtime_handle(),
    )
    payload = {
        "tid": str(int(manager.tid) - 1),
        "task_status": "draining",
        "message": "PONG",
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "ctrl_in": "weft.manager.ctrl_in",
        "ctrl_out": "weft.manager.ctrl_out",
        "weft_context": str(manager._manager_context().root),
    }

    assert not manager._pong_dispatch_eligible(
        payload,
        record=record,
        ctrl_in_name="weft.manager.ctrl_in",
        ctrl_out_name="weft.manager.ctrl_out",
    )


def test_manager_leadership_drain_resumes_when_leader_proof_disappears(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    lower_tid = str(int(manager.tid) - 1)
    manager._begin_leadership_drain(leader_tid=lower_tid)
    monkeypatch.setattr(manager, "_active_dispatch_manager_records", dict)

    manager._continue_shutdown_drain()

    assert manager._draining is False
    assert manager.should_stop is False
    assert manager._unregistered is False


def test_manager_liveness_keeps_expired_external_supervisor_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manager_mod,
        "MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    record = {
        "tid": "1761000000000000010",
        "status": "active",
        "runtime_handle": _external_supervisor_runtime_handle(),
        "_timestamp": time.time_ns() - 120_000_000_000,
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
    }

    assert Manager._manager_record_liveness(record) == "unknown"
    assert Manager._manager_record_is_live(record) is False


def test_manager_liveness_rejects_missing_docker_supervisor_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manager_runtime_mod,
        "runtime_liveness_from_registered_probe",
        lambda handle: "stale",
    )
    record = {
        "tid": "1761000000000000012",
        "status": "active",
        "runtime_handle": {
            "runner": "manager-supervisor",
            "kind": "supervised-process",
            "id": "docker:container123",
            "control": {"authority": "external-supervisor"},
            "observations": {
                "container_runtime": "docker",
                "container_pid": 1,
                "container_id": "container123",
            },
            "metadata": {},
        },
        "_timestamp": time.time_ns(),
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
    }

    assert Manager._manager_record_is_live(record) is False


def test_manager_liveness_uses_supervisor_probe_before_host_pid_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manager_runtime_mod,
        "runtime_liveness_from_registered_probe",
        lambda handle: "live",
    )
    monkeypatch.setattr(
        manager_mod,
        "handle_has_live_host_process",
        lambda handle: (_ for _ in ()).throw(
            AssertionError("supervised manager used host PID identity")
        ),
    )
    record = {
        "tid": "1761000000000013",
        "status": "active",
        "runtime_handle": {
            "runner": "manager-supervisor",
            "kind": "supervised-process",
            "id": "docker:container123",
            "control": {"authority": "external-supervisor"},
            "observations": {
                "container_runtime": "docker",
                "container_id": "container123",
                "host_processes": [{"pid": 57, "create_time": 111.0}],
            },
            "metadata": {},
        },
        "_timestamp": time.time_ns(),
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
    }

    assert Manager._manager_record_is_live(record) is True


def test_manager_liveness_rejects_host_pid_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manager_runtime_mod,
        "inspect_host_process",
        lambda pid, create_time: HostProcessObservation("stale", "identity_mismatch"),
    )
    record = {
        "tid": "1761000000000000011",
        "status": "active",
        "runtime_handle": {
            "runner": "host",
            "kind": "process",
            "id": "1",
            "control": {"authority": "host-pid"},
            "observations": {
                "host_pids": [1],
                "host_processes": [{"pid": 1, "create_time": 111.0}],
            },
            "metadata": {},
        },
        "_timestamp": time.time_ns(),
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
    }

    assert Manager._manager_record_liveness(record) == "stale"
    assert Manager._manager_record_is_live(record) is False


def test_manager_liveness_treats_host_pid_miss_as_unknown_inside_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manager_runtime_mod,
        "detect_container_runtime",
        lambda: ContainerRuntimeDetection(
            runtime="docker",
            markers=("dockerenv",),
            identifier="container123",
        ),
    )
    monkeypatch.setattr(
        manager_runtime_mod,
        "inspect_host_process",
        lambda pid, create_time: HostProcessObservation("stale", "process_absent"),
    )
    record = {
        "tid": "1761000000000000011",
        "status": "active",
        "runtime_handle": {
            "runner": "host",
            "kind": "process",
            "id": "1",
            "control": {"authority": "host-pid"},
            "observations": {
                "host_pids": [1],
                "host_processes": [{"pid": 1, "create_time": 111.0}],
            },
            "metadata": {},
        },
        "_timestamp": time.time_ns(),
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
    }

    assert Manager._manager_record_liveness(record) == "unknown"
    assert Manager._manager_record_is_live(record) is False


def test_manager_runtime_handle_uses_external_supervisor_in_container(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    monkeypatch.setattr(
        manager_mod,
        "detect_container_runtime",
        lambda: ContainerRuntimeDetection(
            runtime="docker",
            markers=("dockerenv",),
            identifier="container123",
        ),
    )

    handle = manager._manager_runtime_handle()

    assert handle.runner == "manager-supervisor"
    assert handle.kind == "supervised-process"
    assert handle.id == "docker:container123"
    assert handle.control == {"authority": "external-supervisor"}
    assert handle.observations["container_runtime"] == "docker"
    assert handle.observations["container_markers"] == ["dockerenv"]
    assert handle.observations["container_id"] == "container123"
    assert isinstance(handle.observations["container_pid"], int)
    assert not handle.scoped_host_pids()


def test_manager_unregister_registry_broker_error_is_best_effort(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    registry_queue = manager._queue(WEFT_SERVICES_REGISTRY_QUEUE)
    deleted_ids: list[int | None] = []
    written_payloads: list[str] = []

    def fail_delete(*, message_id: int | None = None) -> bool:
        deleted_ids.append(message_id)
        raise BrokerError("registry delete failed")

    def fail_write(payload: str) -> None:
        written_payloads.append(payload)
        raise BrokerError("registry write failed")

    manager._unregistered = False
    manager._registry_message_id = 456
    with monkeypatch.context() as patch:
        patch.setattr(registry_queue, "delete", fail_delete)
        patch.setattr(registry_queue, "write", fail_write)
        patch.setattr(manager, "_latest_registry_entry", lambda _queue, _tid: None)

        manager._unregister_manager()

    assert deleted_ids == [456]
    assert len(written_payloads) == 1
    assert manager._unregistered is True
    assert manager._registry_message_id is None


def test_manager_tid_state_forces_role_manager(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_manager_spec(
        unique_tid,
        f"manager.{unique_tid}.inbox",
        f"manager.{unique_tid}.ctrl_in",
        f"manager.{unique_tid}.ctrl_out",
        role="task",
    )
    manager = Manager(db_path, spec)
    try:
        mapping_queue = make_queue(task_state_queue_name(manager.tid))
        entries = [json.loads(item) for item in drain(mapping_queue)]
        relevant = [entry for entry in entries if entry.get("full") == manager.tid]
        assert relevant
        assert relevant[-1]["role"] == "manager"
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_tid_state_defaults_role_manager(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    mapping_queue = make_queue(task_state_queue_name(manager.tid))
    entries = [json.loads(item) for item in drain(mapping_queue)]
    relevant = [entry for entry in entries if entry.get("full") == manager.tid]
    assert relevant
    assert relevant[-1]["role"] == "manager"


def test_manager_cleanup_reaps_running_children(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    pytest.importorskip("psutil")
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    inbox_queue.write(
        json.dumps(
            {
                "name": "long-running",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                    "keyword_args": {"duration": 5.0},
                },
            }
        )
    )

    manager.process_once()
    drive_manager_until(
        manager,
        lambda: bool(manager._child_processes),
        timeout=20.0 if os.name == "nt" else 10.0,
    )

    assert manager._child_processes, "child process should be running"
    child_info = next(iter(manager._child_processes.values()))
    assert child_info.process.is_alive()

    manager.cleanup()

    assert not _process_running(child_info.process.pid)


def test_manager_cleanup_terminates_worker_descendants(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    tmp_path: Path,
) -> None:
    psutil = pytest.importorskip("psutil")
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)
    parent_script, child_script = _write_descendant_process_scripts(tmp_path)
    child_pidfile = tmp_path / "manager-cleanup-child.pid"

    inbox_queue.write(
        json.dumps(
            {
                "name": "long-running-command-with-descendant",
                "spec": {
                    "type": "command",
                    "process_target": sys.executable,
                    "args": [
                        str(parent_script),
                        str(child_script),
                        str(child_pidfile),
                    ],
                },
            }
        )
    )

    child_info: ManagedChild | None = None
    worker_pid: int | None = None
    try:
        drive_manager_until(
            manager,
            lambda: bool(manager._child_processes),
            timeout=20.0 if os.name == "nt" else 10.0,
        )

        assert manager._child_processes, "child process should be running"
        child_tid, child_info = next(iter(manager._child_processes.items()))

        worker_pid = _wait_for_pidfile(
            child_pidfile,
            timeout=20.0 if os.name == "nt" else 10.0,
        )
        assert _process_running(worker_pid), (
            f"expected worker descendant for {child_tid}"
        )

        manager.cleanup()

        deadline = time.time() + (20.0 if os.name == "nt" else 5.0)
        while time.time() < deadline:
            root_alive = _process_running(child_info.process.pid)
            worker_alive = _process_running(worker_pid)
            if not root_alive and not worker_alive:
                break
            time.sleep(0.05)

        assert not _process_running(child_info.process.pid)
        assert not _process_running(worker_pid)
    finally:
        try:
            manager.cleanup()
        finally:
            if child_info is not None:
                root_pid = child_info.process.pid
                if isinstance(root_pid, int) and _process_running(root_pid):
                    manager_mod.kill_process_tree(root_pid, timeout=2.0)
            if worker_pid is not None and _process_running(worker_pid):
                try:
                    psutil.Process(worker_pid).kill()
                except psutil.Error:
                    pass
                _wait_for_pid_exit(worker_pid, timeout=2.0)


def _spawn_sigterm_trapping_process(ready_file: Path) -> subprocess.Popen[bytes]:
    """Spawn a real process that ignores SIGTERM, signal readiness, and sleep."""

    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import signal, sys, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "open(sys.argv[1], 'w').write('ready'); "
                "time.sleep(60)"
            ),
            str(ready_file),
        ]
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if ready_file.exists():
            return process
        time.sleep(0.01)
    raise AssertionError("SIGTERM-trapping helper process never became ready")


def test_manager_terminate_children_kills_sigterm_trapping_managed_pid(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TERM-resistant managed pid dies within the cleanup deadline [IMPL.10].

    Fires the within-budget SIGKILL escalation on the managed-pids rung:
    `kill_after=False` alone leaves a SIGTERM-ignoring worker alive forever
    (plan section 13.1 item R-1).
    """

    psutil = pytest.importorskip("psutil")
    manager, _make_queue = manager_setup
    trapping = _spawn_sigterm_trapping_process(tmp_path / "trapping-ready.txt")

    class ExitedChild:
        pid = trapping.pid + 100000
        exitcode = 0

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            return None

    manager._child_processes["trapping"] = ManagedChild(
        process=cast(BaseProcess, ExitedChild()),
        ctrl_queue=None,
        persistent=False,
    )
    monkeypatch.setattr(
        manager,
        "_managed_pids_for_child",
        lambda tid: {trapping.pid} if tid == "trapping" else set(),
    )

    try:
        manager._terminate_children(time.monotonic() + 2.0)
        assert trapping.wait(timeout=3.0) is not None
        assert not _process_running(trapping.pid), (
            "SIGTERM-trapping managed pid survived _terminate_children: "
            "KILL escalation did not run within the deadline"
        )
    finally:
        if trapping.poll() is None:
            try:
                psutil.Process(trapping.pid).kill()
            except psutil.Error:
                pass
            trapping.wait(timeout=2.0)


def test_manager_terminate_children_kills_sigterm_trapping_descendant_tree(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-214] exception
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direct-child tree rung escalates to SIGKILL within budget [IMPL.10].

    A live direct child whose descendant traps SIGTERM: the tree pass must
    still remove the descendant inside the caller deadline (plan 13.1 R-1).
    """

    psutil = pytest.importorskip("psutil")
    manager, _make_queue = manager_setup
    parent_script, child_script = _write_term_trapping_descendant_scripts(tmp_path)
    pidfile = tmp_path / "trapping-descendant.pid"
    parent = subprocess.Popen(
        [sys.executable, str(parent_script), str(child_script), str(pidfile)]
    )
    worker_pid = _wait_for_pidfile(pidfile, timeout=10.0)
    assert _process_running(worker_pid)

    class PopenChild:
        pid = parent.pid
        exitcode = None

        def is_alive(self) -> bool:
            return parent.poll() is None

        def join(self, timeout: float | None = None) -> None:
            try:
                parent.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass

        def kill(self) -> None:
            parent.kill()

    manager._child_processes["tree"] = ManagedChild(
        process=cast(BaseProcess, PopenChild()),
        ctrl_queue=None,
        persistent=False,
    )
    monkeypatch.setattr(manager, "_managed_pids_for_child", lambda tid: set())
    monkeypatch.setattr(manager, "_send_stop_command", lambda *a, **k: None)

    try:
        manager._terminate_children(time.monotonic() + 2.0)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if parent.poll() is not None and not _process_running(worker_pid):
                break
            time.sleep(0.05)
        assert parent.poll() is not None
        assert not _process_running(worker_pid), (
            "SIGTERM-trapping descendant survived the direct-child tree rung"
        )
    finally:
        for pid in (worker_pid,):
            if _process_running(pid):
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=2.0)


def test_manager_cleanup_terminates_reaped_child_managed_pids(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class FakeProcess:
        pid = 424242
        exitcode = 0

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            return None

    manager._child_processes["child"] = ManagedChild(
        process=cast(BaseProcess, FakeProcess()),
        ctrl_queue=None,
        persistent=False,
    )
    monkeypatch.setattr(
        manager,
        "_managed_pids_for_child",
        lambda tid: {515151} if tid == "child" else set(),
    )

    terminated: list[tuple[int, float, bool]] = []

    def _record_terminate(
        pid: int,
        *,
        timeout: float = 0.5,
        kill_after: bool = True,
    ) -> set[int]:
        terminated.append((pid, timeout, kill_after))
        return {pid}

    monkeypatch.setattr(manager_mod, "terminate_process_tree", _record_terminate)

    cleanup_budget = 5.0 if os.name == "nt" else 1.0
    deadline = time.monotonic() + cleanup_budget
    manager._terminate_children(deadline)

    assert manager._child_processes == {}
    assert len(terminated) == 1
    assert terminated[0][0] == 515151
    # Split budget: TERM wait consumes at most half the remaining deadline so
    # the kill_after=True SIGKILL reap wait fits inside the same budget.
    assert 0.0 < terminated[0][1] <= cleanup_budget / 2.0
    assert terminated[0][2] is True


def test_manager_cleanup_retains_live_managed_pid_after_wrapper_exit(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed survivors remain diagnosable after their wrapper exits [IMPL.10]."""

    manager, _make_queue = manager_setup

    class ExitedProcess:
        pid = 424242
        exitcode = 0

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            return None

    manager._child_processes["child"] = ManagedChild(
        process=cast(BaseProcess, ExitedProcess()),
        ctrl_queue=None,
        persistent=False,
    )
    monkeypatch.setattr(
        manager,
        "_managed_pids_for_child",
        lambda tid: {515151} if tid == "child" else set(),
    )
    monkeypatch.setattr(manager_mod, "pid_is_live", lambda pid: pid == 515151)
    monkeypatch.setattr(
        manager_mod,
        "terminate_process_tree",
        lambda *args, **kwargs: set(),
    )

    manager._terminate_children(time.monotonic())

    assert manager._child_processes == {}
    assert manager._last_child_cleanup_survivors == (
        {
            "tid": "child",
            "pid": 424242,
            "managed_pids": [515151],
            "surviving_managed_pids": [515151],
            "kill_issued": False,
        },
    )


def test_manager_child_termination_uses_one_deadline_for_multiple_children(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    clock = {"now": 100.0}
    join_timeouts: list[float] = []

    class StubbornProcess(FakeLaunchProcess):
        def join(self, timeout: float | None = None) -> None:
            allocated = float(timeout or 0.0)
            join_timeouts.append(allocated)
            clock["now"] += allocated

    for index in range(3):
        manager._child_processes[f"child-{index}"] = ManagedChild(
            process=cast(BaseProcess, StubbornProcess(pid=424240 + index, alive=True)),
            ctrl_queue=None,
            persistent=False,
        )

    monkeypatch.setattr(manager_mod, "pid_is_live", lambda _pid: True)
    monkeypatch.setattr(manager, "_managed_pids_for_child", lambda _tid: set())
    monkeypatch.setattr(manager_mod, "terminate_process_tree", lambda *a, **k: set())
    real_time = manager_mod.time

    class FakeTimeModule:
        def monotonic(self) -> float:
            return clock["now"]

        def sleep(self, seconds: float) -> None:
            clock["now"] += seconds

        def __getattr__(self, name: str) -> object:
            return getattr(real_time, name)

    fake_time = FakeTimeModule()
    monkeypatch.setattr(manager_mod, "time", fake_time)
    monkeypatch.setattr(base_task_mod, "time", fake_time)

    def advance_sentinel_wait(timeout: float) -> bool:
        clock["now"] += timeout
        return False

    monkeypatch.setattr(
        manager._child_sentinel_adapter,
        "wait",
        advance_sentinel_wait,
    )

    deadline = clock["now"] + 0.08
    manager._terminate_children(deadline)

    assert clock["now"] <= deadline
    assert manager._child_processes == {}
    assert join_timeouts
    assert sum(join_timeouts) <= 0.12


def test_manager_stop_command_drains_nonpersistent_children(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    pytest.importorskip("psutil")
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    ctrl_in_queue = make_queue(manager._queue_names["ctrl_in"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    inbox_queue.write(
        json.dumps(
            {
                "name": "long-running",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                    "keyword_args": {"duration": 5.0},
                },
            }
        )
    )

    start = time.time()
    while not manager._child_processes and time.time() - start < 5.0:
        manager.process_once()
        time.sleep(0.05)

    assert manager._child_processes, "child process should be running"
    _child_tid, child_info = next(iter(manager._child_processes.items()))
    assert child_info.process.is_alive()
    wait_for_log_event(
        manager,
        log_queue,
        lambda event: (
            event.get("tid") == _child_tid and event.get("event") == "work_started"
        ),
        timeout=30.0 if os.name == "nt" else 20.0,
    )

    ctrl_in_queue.write(encode_control_message(CONTROL_STOP))

    deadline = time.time() + 8.0
    while time.time() < deadline and not manager.should_stop:
        manager.process_once()
        time.sleep(0.05)

    assert manager.should_stop
    assert manager.taskspec.state.status == "cancelled"
    assert not _process_running(child_info.process.pid)

    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "control_stop" for event in events)
    assert any(event.get("event") == "manager_stop_drained" for event in events)


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals required")
def test_manager_sigterm_drains_nonpersistent_children(
    manager_setup: tuple[Manager, Callable[[str], Queue]], tmp_path: Path
) -> None:
    pytest.importorskip("psutil")
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    ready = tmp_path / "worker-ready"
    release = tmp_path / "worker-release"

    inbox_queue.write(
        json.dumps(
            {
                "name": "long-running",
                "spec": {
                    "type": "function",
                    "function_target": (
                        "tests.tasks.sample_targets:signal_ready_and_wait_for_release"
                    ),
                    "args": [str(ready), str(release)],
                },
            }
        )
    )

    def diagnostics() -> dict[str, object]:
        return {
            "ready": ready.exists(),
            "released": release.exists(),
            "draining": manager._draining,
            "children": {
                tid: {"pid": child.process.pid, "exitcode": child.process.exitcode}
                for tid, child in manager._child_processes.items()
            },
        }

    try:
        drive_until(
            ready.exists,
            bool,
            step=manager.process_once,
            wait=manager.wait_for_activity,
            timeout=20.0,
            diagnostics=diagnostics,
        )
        child_tid, child_info = next(iter(manager._child_processes.items()))
        assert child_info.process.is_alive()
        assert not release.exists()
        manager.note_termination_signal(signal.SIGTERM)

        assert list(manager._pending_termination_sources) == [
            ("signal", signal.SIGTERM)
        ]
        assert manager._draining is False
        assert manager.should_stop is False
        assert manager.taskspec.state.status == "running"

        manager.process_once()
        assert manager._draining
        assert child_tid in manager._drain_signaled_children
        release.touch()
        drive_until(
            lambda: manager.should_stop,
            bool,
            step=manager.process_once,
            wait=manager.wait_for_activity,
            timeout=5.0,
            diagnostics=diagnostics,
        )

        assert manager.taskspec.state.status == "cancelled"
        assert not _process_running(child_info.process.pid)
        events = [json.loads(item) for item in log_queue.peek_generator()]
        assert any(event.get("event") == "task_signal_stop" for event in events)
        assert not any(event.get("event") == "task_signal_kill" for event in events)
        assert any(
            event.get("tid") == child_tid and event.get("event") == "work_started"
            for event in events
        ), "observing readiness must preserve child lifecycle history"
    finally:
        release.touch()


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals required")
def test_foreground_serve_sigterm_uses_async_drain_path(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    """Foreground serve SIGTERM must not do broker work in the signal handler."""

    manager, _make_queue = manager_setup
    manager.taskspec.metadata["foreground_serve"] = True
    drain_started = False
    original_terminate_children = manager._terminate_children
    original_begin_shutdown_drain = manager._begin_shutdown_drain

    def fail_if_signal_handler_starts_drain(*args: object, **kwargs: object) -> None:
        nonlocal drain_started
        drain_started = True
        raise AssertionError("signal handler must not synchronously start draining")

    def fail_if_signal_handler_terminates_children(deadline: float) -> None:
        raise AssertionError("signal handler must not synchronously terminate children")

    try:
        manager._begin_shutdown_drain = fail_if_signal_handler_starts_drain  # type: ignore[method-assign]
        manager._terminate_children = fail_if_signal_handler_terminates_children  # type: ignore[method-assign]

        manager.note_termination_signal(signal.SIGTERM)

        assert drain_started is False
        assert list(manager._pending_termination_sources) == [
            ("signal", signal.SIGTERM)
        ]
        assert manager._draining is False
        assert manager.should_stop is False
        assert manager.taskspec.state.status == "running"
    finally:
        manager._terminate_children = original_terminate_children  # type: ignore[method-assign]
        manager._begin_shutdown_drain = original_begin_shutdown_drain  # type: ignore[method-assign]

    manager.process_once()

    assert manager._has_pending_termination_request() is False
    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"


def test_manager_drain_timeout_force_finishes_stubborn_children(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    ctrl_queue_name = "manager.stubborn-child.ctrl_in"
    ctrl_queue = make_queue(ctrl_queue_name)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    class StubbornProcess:
        pid = os.getpid()
        exitcode = None

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

    manager._child_processes["stubborn-child"] = ManagedChild(
        process=cast(BaseProcess, StubbornProcess()),
        ctrl_queue=ctrl_queue_name,
        persistent=False,
    )
    terminated = False

    def finish_stubborn_child(_deadline: float) -> None:
        nonlocal terminated
        terminated = True
        manager._child_processes.clear()

    monkeypatch.setattr(manager_mod, "MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(manager, "_terminate_children", finish_stubborn_child)

    manager._begin_graceful_shutdown(message_id=None)
    manager.process_once()

    assert ctrl_queue.read_one() == encode_control_message(CONTROL_STOP)
    assert terminated is True
    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"

    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "control_stop" for event in events)
    assert any(event.get("event") == "manager_stop_drained" for event in events)


@pytest.mark.skipif(
    os.name == "nt" or getattr(signal, "SIGUSR1", None) is None,
    reason="SIGUSR1 not available",
)
def test_manager_sigusr1_keeps_kill_semantics(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    """SIGUSR1 should stay on the immediate kill path and emit task_signal_kill."""

    pytest.importorskip("psutil")
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    inbox_queue.write(
        json.dumps(
            {
                "name": "long-running",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                    "keyword_args": {"duration": 5.0},
                },
            }
        )
    )

    start = time.time()
    while not manager._child_processes and time.time() - start < 5.0:
        manager.process_once()
        time.sleep(0.05)

    assert manager._child_processes, "child process should be running"
    _child_tid, child_info = next(iter(manager._child_processes.items()))

    manager.note_termination_signal(signal.SIGUSR1)
    assert list(manager._pending_termination_sources) == [("signal", signal.SIGUSR1)]

    manager.process_once()

    assert manager.should_stop is True
    assert manager.taskspec.state.status == "killed"
    assert not _process_running(child_info.process.pid)

    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "task_signal_kill" for event in events)


@pytest.mark.skipif(
    getattr(signal, "SIGUSR1", None) is None,
    reason="SIGUSR1 not available",
)
def test_manager_sigusr1_outranks_pending_parent_loss(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    termination_deadlines: list[float] = []

    monkeypatch.setattr(
        manager,
        "_terminate_children",
        lambda deadline: termination_deadlines.append(deadline),
    )
    manager.note_parent_loss()
    manager.note_termination_signal(signal.SIGUSR1)

    manager.process_once()

    assert termination_deadlines
    assert manager._has_pending_termination_request() is False
    assert manager.taskspec.state.status == "killed"
    assert manager._draining is False


def test_manager_parent_loss_enters_graceful_drain_with_live_children(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent loss alone drains gracefully, never generic-cancels [IMPL.10].

    Fires the first half of plan red test 27 (plan section 13.1 item R-3):
    ``note_parent_loss()`` with a live child enters ``_begin_shutdown_drain``
    with the parent-loss reason, sends the child a STOP control command, and
    neither terminates children nor publishes a terminal manager state on
    that owner turn.
    """

    manager, make_queue = manager_setup
    live_child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    class LiveChild:
        pid = live_child.pid
        exitcode = None

        def is_alive(self) -> bool:
            return live_child.poll() is None

        def join(self, timeout: float | None = None) -> None:
            try:
                live_child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass

    child_ctrl_queue = "T616161.ctrl_in"
    manager._child_processes["616161"] = ManagedChild(
        process=cast(BaseProcess, LiveChild()),
        ctrl_queue=child_ctrl_queue,
        persistent=False,
    )
    termination_deadlines: list[float] = []
    monkeypatch.setattr(
        manager,
        "_terminate_children",
        lambda deadline: termination_deadlines.append(deadline),
    )

    try:
        manager.note_parent_loss()
        manager.process_once()

        assert manager._draining is True
        assert manager._drain_reason == "Parent process exited"
        assert manager._drain_stops_children is True
        assert termination_deadlines == []
        assert "616161" in manager._child_processes
    finally:
        live_child.kill()
        live_child.wait(timeout=5.0)
    assert manager.taskspec.state.status not in {
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "killed",
    }
    stop_commands = drain(make_queue(child_ctrl_queue))
    assert encode_control_message(CONTROL_STOP) in stop_commands, (
        f"child did not receive STOP during graceful drain: {stop_commands!r}"
    )


@pytest.mark.skipif(
    getattr(signal, "SIGUSR1", None) is None,
    reason="SIGUSR1 not available",
)
def test_manager_sigusr1_arriving_during_parent_snapshot_is_not_lost(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    termination_deadlines: list[float] = []

    monkeypatch.setattr(
        manager,
        "_terminate_children",
        lambda deadline: termination_deadlines.append(deadline),
    )
    manager.note_parent_loss()

    class InjectingDeque(deque[tuple[Literal["signal", "parent_loss"], int | None]]):
        injected = False

        def popleft(self) -> tuple[Literal["signal", "parent_loss"], int | None]:
            source = super().popleft()
            if not self.injected:
                self.injected = True
                manager.note_termination_signal(signal.SIGUSR1)
            return source

    manager._pending_termination_sources = InjectingDeque(
        manager._pending_termination_sources
    )

    manager.process_once()

    assert list(manager._pending_termination_sources) == [("signal", signal.SIGUSR1)]
    assert termination_deadlines == []

    manager.process_once()

    assert termination_deadlines
    assert manager._kill_requested is True
    assert manager._has_pending_termination_request() is False


def test_manager_stop_command_does_not_launch_new_children_after_stop(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    ctrl_in_queue = make_queue(manager._queue_names["ctrl_in"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    payload = {
        "name": "queued-stop",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:simulate_work",
            "keyword_args": {"duration": 0.5, "result": "ok"},
        },
    }
    inbox_queue.write(json.dumps(payload))
    inbox_queue.write(json.dumps(payload))

    manager.process_once()
    deadline = time.time() + (20.0 if os.name == "nt" else 10.0)
    while time.time() < deadline and len(manager._child_processes) < 1:
        manager._drain_worker_results()
        if len(manager._child_processes) < 1:
            manager.wait_for_activity(timeout=0.02)
    assert len(manager._child_processes) == 1

    ctrl_in_queue.write(encode_control_message(CONTROL_STOP))

    deadline = time.time() + (20.0 if os.name == "nt" else 10.0)
    max_children_seen = len(manager._child_processes)
    while time.time() < deadline and not (manager._draining or manager.should_stop):
        manager.process_once()
        max_children_seen = max(max_children_seen, len(manager._child_processes))
        time.sleep(0.05)

    assert manager._draining or manager.should_stop
    for _ in range(3):
        manager.process_once()
        max_children_seen = max(max_children_seen, len(manager._child_processes))
        time.sleep(0.05)

    assert max_children_seen == 1

    events = [json.loads(item) for item in drain(log_queue)]
    spawn_events = [event for event in events if event.get("event") == "task_spawned"]
    assert len(spawn_events) == 1
    assert any(event.get("event") == "control_stop" for event in events)


def test_manager_drain_reissues_stop_for_child_added_after_stop(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    ctrl_queue_name = "manager.late-child.ctrl_in"
    ctrl_queue = make_queue(ctrl_queue_name)

    class FakeProcess:
        pid = 424245
        exitcode = None

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

    manager._begin_graceful_shutdown(message_id=None)
    manager._child_processes["late-child"] = ManagedChild(
        process=cast(BaseProcess, FakeProcess()),
        ctrl_queue=ctrl_queue_name,
        persistent=False,
    )

    manager.process_once()

    assert ctrl_queue.read_one() == encode_control_message(CONTROL_STOP)


def test_manager_stop_mid_handler_requeues_reserved_work_unlaunched(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    ctrl_in_queue = make_queue(manager._queue_names["ctrl_in"])
    reserved_queue = make_queue(manager._queue_names["reserved"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    payload = {
        "name": "queued-stop",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:simulate_work",
            "keyword_args": {"duration": 0.5, "result": "ok"},
        },
    }
    inbox_queue.write(json.dumps(payload))

    original_build_child_spec = manager._build_child_spec

    def inject_stop(payload: dict[str, object], timestamp: int) -> TaskSpec | None:
        child_spec = original_build_child_spec(payload, timestamp)
        ctrl_in_queue.write(encode_control_message(CONTROL_STOP))
        return child_spec

    monkeypatch.setattr(manager, "_build_child_spec", inject_stop)

    manager.process_once()

    assert manager._child_processes == {}
    assert reserved_queue.peek_one() is None
    assert inbox_queue.peek_one() is not None
    assert manager.taskspec.state.status == "cancelled"

    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "control_stop" for event in events)
    assert not any(event.get("event") == "task_spawned" for event in events)


@pytest.mark.parametrize("active_records", [None, {}])
def test_manager_public_dispatch_steals_work_when_registry_ownership_is_unproved(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
    active_records: dict[str, dict[str, object]] | None,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    spawn_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    reserved_queue = make_queue(manager._queue_names["reserved"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(spawn_queue)
    drain(log_queue)

    payload = {
        "name": "work-steal",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    spawn_queue.write(json.dumps(payload))
    message_id = pending_timestamps(spawn_queue)[0]
    launched: list[str] = []

    def _record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        assert child_spec.tid is not None
        launched.append(child_spec.tid)
        return True

    monkeypatch.setattr(
        manager,
        "_read_active_manager_records",
        lambda **_kwargs: active_records,
    )
    monkeypatch.setattr(manager, "_launch_child_task", _record_launch)

    try:
        manager.process_once()
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert launched == [str(message_id)]
    assert spawn_queue.peek_one(exact_timestamp=message_id) is None
    assert reserved_queue.peek_one(exact_timestamp=message_id) is None
    events = [json.loads(item) for item in drain(log_queue)]
    assert not any(
        str(event.get("event", "")).startswith("manager_spawn_fence")
        for event in events
    )


def test_manager_leadership_yields_when_only_public_backlog_is_pending(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    spawn_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    reserved_queue = make_queue(manager._queue_names["reserved"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(spawn_queue)
    drain(log_queue)

    payload = {
        "name": "unowned-public-backlog",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    spawn_queue.write(json.dumps(payload))
    message_id = pending_timestamps(spawn_queue)[0]
    lower_leader_tid = str(int(manager.tid) - 1)
    launched: list[str] = []

    def _record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        assert child_spec.tid is not None
        launched.append(child_spec.tid)
        return True

    monkeypatch.setattr(
        manager,
        "_read_active_manager_records",
        lambda **_kwargs: {lower_leader_tid: {"tid": lower_leader_tid}},
    )
    monkeypatch.setattr(
        manager,
        "_active_dispatch_manager_records",
        lambda **_kwargs: {lower_leader_tid: {"tid": lower_leader_tid}},
    )
    monkeypatch.setattr(manager, "_launch_child_task", _record_launch)

    try:
        yielded = manager._maybe_yield_leadership(force=True)
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert yielded is True
    assert launched == []
    assert manager.should_stop is True
    assert spawn_queue.peek_one(exact_timestamp=message_id) is not None
    assert reserved_queue.peek_one(exact_timestamp=message_id) is None
    events = [json.loads(item) for item in drain(log_queue)]
    yield_event = next(
        event for event in events if event.get("event") == "manager_leadership_yielded"
    )
    assert yield_event["leader_tid"] == lower_leader_tid


def test_manager_leadership_requeues_reserved_public_work_before_yield(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    spawn_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    reserved_queue = make_queue(manager._queue_names["reserved"])
    drain(spawn_queue)
    drain(reserved_queue)

    payload = {
        "name": "owned-public-reserved",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    spawn_queue.write(json.dumps(payload))
    message_id = pending_timestamps(spawn_queue)[0]
    moved = spawn_queue.move_one(
        reserved_queue.name,
        exact_timestamp=message_id,
        with_timestamps=True,
    )
    lower_leader_tid = str(int(manager.tid) - 1)

    monkeypatch.setattr(
        manager,
        "_read_active_manager_records",
        lambda **_kwargs: {lower_leader_tid: {"tid": lower_leader_tid}},
    )

    try:
        yielded = manager._maybe_yield_leadership(force=True)
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert moved == (json.dumps(payload), message_id)
    assert yielded is True
    assert manager.should_stop is True
    assert reserved_queue.peek_one(exact_timestamp=message_id) is None
    assert spawn_queue.peek_one(exact_timestamp=message_id) is not None


def test_manager_leadership_waits_while_child_launch_is_in_flight(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    manager._active_child_launches["pending-child"] = object()  # type: ignore[assignment]

    def fail_read_active_records(**_kwargs: object) -> dict[str, dict[str, Any]]:
        raise AssertionError("active child launch should block leadership yield check")

    monkeypatch.setattr(
        manager, "_read_active_manager_records", fail_read_active_records
    )

    yielded = manager._maybe_yield_leadership(force=True)

    assert yielded is False
    assert manager.should_stop is False


def test_manager_services_successfully_reserved_public_work(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    spawn_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    reserved_queue = make_queue(manager._queue_names["reserved"])
    drain(spawn_queue)
    drain(reserved_queue)

    payload = {
        "name": "owned-public-reserved",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    spawn_queue.write(json.dumps(payload))
    message_id = pending_timestamps(spawn_queue)[0]
    moved = spawn_queue.move_one(
        reserved_queue.name,
        exact_timestamp=message_id,
        with_timestamps=True,
    )
    launched: list[str] = []

    def _record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        assert child_spec.tid is not None
        launched.append(child_spec.tid)
        return True

    monkeypatch.setattr(manager, "_launch_child_task", _record_launch)

    try:
        manager._handle_work_message(
            json.dumps(payload),
            message_id,
            QueueMessageContext(
                queue_name=WEFT_SPAWN_REQUESTS_QUEUE,
                queue=make_queue(WEFT_SPAWN_REQUESTS_QUEUE),
                mode=QueueMode.RESERVE,
                timestamp=message_id,
                reserved_queue_name=reserved_queue.name,
            ),
        )
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert moved == (json.dumps(payload), message_id)
    assert launched == [str(message_id)]
    assert reserved_queue.peek_one(exact_timestamp=message_id) is None


def test_manager_does_not_probe_inactive_public_spawn_queue(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    spawn_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    reserved_queue = make_queue(manager._queue_names["reserved"])
    drain(spawn_queue)

    payload = {
        "name": "inactive-public-queue",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    spawn_queue.write(json.dumps(payload))
    message_id = pending_timestamps(spawn_queue)[0]
    launched: list[str] = []

    def _record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        assert child_spec.tid is not None
        launched.append(child_spec.tid)
        return True

    manager._active_queues = []
    manager._queue_iterator = itertools.cycle([])
    manager._next_inactive_probe_at = time.monotonic() + 60
    manager._pending_messages_precheck_confirmed = False
    monkeypatch.setattr(manager, "_leader_tid", lambda: manager.tid)
    monkeypatch.setattr(manager, "_launch_child_task", _record_launch)

    try:
        manager.process_once()
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert launched == []
    assert spawn_queue.peek_one(exact_timestamp=message_id) is not None
    assert reserved_queue.peek_one(exact_timestamp=message_id) is None


def test_manager_pending_precheck_activates_public_spawn_queue(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    spawn_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    reserved_queue = make_queue(manager._queue_names["reserved"])
    drain(spawn_queue)

    payload = {
        "name": "inactive-public-queue",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    spawn_queue.write(json.dumps(payload))
    message_id = pending_timestamps(spawn_queue)[0]
    launched: list[str] = []

    def _record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        assert child_spec.tid is not None
        launched.append(child_spec.tid)
        return True

    manager._active_queues = []
    manager._queue_iterator = itertools.cycle([])
    manager._next_inactive_probe_at = time.monotonic() + 60
    manager._pending_messages_precheck_confirmed = True
    monkeypatch.setattr(manager, "_leader_tid", lambda: manager.tid)
    monkeypatch.setattr(manager, "_launch_child_task", _record_launch)

    try:
        manager.process_once()
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert launched == [str(message_id)]
    assert spawn_queue.peek_one(exact_timestamp=message_id) is None
    assert reserved_queue.peek_one(exact_timestamp=message_id) is None


def test_manager_idle_discovery_skips_reserved_queues(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    reserved_names = {
        reserved_queue
        for reserved_queue, _source_queue in manager._spawn_reserved_queue_pairs()
    }

    def pending_probe(queue: object) -> bool:
        queue_name = getattr(queue, "name", "")
        if queue_name in reserved_names:
            raise AssertionError("reserved queues are not ordinary idle discovery")
        return False

    monkeypatch.setattr(manager, "_queue_has_pending", pending_probe)
    manager._active_queues = []
    manager._queue_iterator = itertools.cycle([])
    manager._pending_messages_precheck_confirmed = True
    manager._next_inactive_probe_at = 0

    try:
        manager._drain_queue()
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_spawn_drains_require_pending_evidence(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    internal_reserved = make_queue(manager._queue_names["internal_reserved"])
    spawn_queue = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    reserved_queue = make_queue(manager._queue_names["reserved"])
    drain(internal_queue)
    drain(internal_reserved)
    drain(spawn_queue)
    drain(reserved_queue)
    launched: list[str] = []

    def _record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        launched.append(child_spec.name)
        return True

    def _missing_pending_hint(_queue: object) -> bool:
        return False

    monkeypatch.setattr(manager, "_queue_has_pending", _missing_pending_hint)
    monkeypatch.setattr(manager, "_launch_child_task", _record_launch)
    internal_queue.write(
        json.dumps(
            {
                "name": "internal-first",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            }
        )
    )
    for index in range(5):
        spawn_queue.write(
            json.dumps(
                {
                    "name": f"public-{index}",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.tasks.sample_targets:echo_payload",
                    },
                }
            )
        )

    try:
        manager.process_once()
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert launched == []
    assert internal_queue.peek_one() is not None
    assert internal_reserved.peek_one() is None
    assert spawn_queue.peek_one() is not None
    assert reserved_queue.peek_one() is None


def test_manager_cleanup_clears_own_internal_reserved_even_without_cleanup_on_exit(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    internal_reserved = make_queue(manager._queue_names["internal_reserved"])
    drain(internal_reserved)
    internal_reserved.write("stale-internal-spawn")

    cleaned = False
    try:
        manager.stop(join=False)
        manager.cleanup()
        cleaned = True
    finally:
        if not cleaned:
            manager.stop(join=False)
            manager.cleanup()

    assert internal_reserved.peek_one() is None


def test_manager_deletes_stale_internal_reserved_for_inactive_manager(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    stale_tid = str(int(unique_tid) - 100)
    active_tid = str(int(unique_tid) + 100)
    stale_reserved = make_queue(f"T{stale_tid}.internal_reserved")
    active_reserved = make_queue(f"T{active_tid}.internal_reserved")
    own_reserved = make_queue(manager._queue_names["internal_reserved"])
    drain(stale_reserved)
    drain(active_reserved)
    drain(own_reserved)
    stale_reserved.write("stale")
    active_reserved.write("active")
    own_reserved.write("own")
    manager._manager_registry_snapshot = {
        active_tid: {"tid": active_tid, "status": SERVICE_STATUS_ACTIVE},
    }
    monkeypatch.setattr(
        manager,
        "_read_active_manager_records",
        lambda **_kwargs: {
            manager.tid: {"tid": manager.tid},
            active_tid: {"tid": active_tid},
        },
    )

    try:
        manager._cleanup_stale_internal_reserved_queues(force=True)
        assert stale_reserved.peek_one() is None
        assert active_reserved.peek_one() == "active"
        assert own_reserved.peek_one() == "own"
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_keeps_internal_reserved_when_manager_liveness_unknown(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    stale_tid = str(int(unique_tid) - 100)
    stale_reserved = make_queue(f"T{stale_tid}.internal_reserved")
    drain(stale_reserved)
    stale_reserved.write("unknown")
    monkeypatch.setattr(
        manager,
        "_read_active_manager_records",
        lambda **_kwargs: None,
    )

    try:
        manager._cleanup_stale_internal_reserved_queues(force=True)
        assert stale_reserved.peek_one() == "unknown"
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_service_convergence_advances_without_dispatch_ownership(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=config,
    )
    manager._task_monitor_enabled = True
    internal_queue = make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE)
    internal_reserved = make_queue(manager._queue_names["internal_reserved"])
    drain(internal_queue)
    drain(internal_reserved)
    launched: list[str] = []

    def _record_launch(child_spec: TaskSpec, *_args: object, **_kwargs: object) -> bool:
        service_key = child_spec.metadata.get(INTERNAL_SERVICE_KEY_METADATA_KEY)
        assert isinstance(service_key, str)
        state = manager._service_state(service_key)
        state.active_tid = child_spec.tid
        state.spawn_pending = False
        state.launched_once = True
        launched.append(service_key)
        return True

    monkeypatch.setattr(
        manager,
        "_evaluate_dispatch_ownership",
        lambda: (_ for _ in ()).throw(
            AssertionError("service convergence must not require dispatch ownership")
        ),
    )
    monkeypatch.setattr(manager, "_launch_child_task", _record_launch)

    try:
        manager._run_managed_service_convergence(force=True)
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert INTERNAL_SERVICE_KEY_HEARTBEAT in launched
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR in launched
    assert internal_queue.peek_one() is None
    assert internal_reserved.peek_one() is None


def test_manager_self_registry_record_is_live_without_external_liveness_probe(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
    )
    manager = Manager(db_path, make_manager_spec(unique_tid), config=config)
    monkeypatch.setattr(
        manager_mod,
        "handle_has_live_host_process",
        lambda _handle: (_ for _ in ()).throw(
            AssertionError("self liveness must not use external probes")
        ),
    )

    try:
        active = manager._read_active_manager_records()
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert active is not None
    assert manager.tid in active


def test_manager_leadership_yield_drains_nonpersistent_children(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    ctrl_queue_name = "manager.leadership-child.ctrl_in"
    ctrl_queue = make_queue(ctrl_queue_name)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    class FakeProcess:
        pid = None
        exitcode = None

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

        def kill(self) -> None:
            return None

    manager._child_processes["child"] = ManagedChild(
        process=cast(BaseProcess, FakeProcess()),
        ctrl_queue=ctrl_queue_name,
        persistent=False,
    )

    lower_leader_tid = str(int(manager.tid) - 1)
    unregister_calls: list[str] = []

    monkeypatch.setattr(
        manager,
        "_read_active_manager_records",
        lambda **_kwargs: {lower_leader_tid: {"tid": lower_leader_tid}},
    )
    monkeypatch.setattr(
        manager,
        "_active_dispatch_manager_records",
        lambda: {lower_leader_tid: {"tid": lower_leader_tid}},
    )
    monkeypatch.setattr(
        manager,
        "_unregister_manager",
        lambda *, status="stopped": unregister_calls.append(status),
    )

    yielded = manager._maybe_yield_leadership(force=True)

    assert yielded is True
    assert manager._draining is True
    assert manager.should_stop is False
    assert unregister_calls == ["draining"]
    assert manager.taskspec.state.status == "running"

    events = [json.loads(item) for item in drain(log_queue)]
    yield_events = [
        event for event in events if event.get("event") == "manager_leadership_yielded"
    ]
    assert len(yield_events) == 1
    assert yield_events[0]["leader_tid"] == lower_leader_tid
    assert yield_events[0]["draining"] is True
    assert yield_events[0]["status"] == "running"

    manager.process_once()

    assert ctrl_queue.peek_one() is None
    assert manager.should_stop is False
    assert manager._child_processes

    manager._child_processes.clear()
    manager.process_once()

    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"

    events = [json.loads(item) for item in drain(log_queue)]
    drained_events = [
        event for event in events if event.get("event") == "manager_leadership_drained"
    ]
    assert len(drained_events) == 1
    assert drained_events[0]["status"] == "cancelled"


def test_manager_leadership_yield_waits_while_persistent_children_exist(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class FakeProcess:
        pid = 424244
        exitcode = None

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

    manager._child_processes["child"] = ManagedChild(
        process=cast(BaseProcess, FakeProcess()),
        ctrl_queue=None,
        persistent=True,
    )

    lower_leader_tid = str(int(manager.tid) - 1)
    monkeypatch.setattr(manager, "_leader_tid", lambda: lower_leader_tid)

    yielded = manager._maybe_yield_leadership(force=True)

    assert yielded is False
    assert manager._draining is False
    assert manager.should_stop is False
    assert manager.taskspec.state.status == "running"
    manager._child_processes.clear()


def test_manager_lower_leader_blocks_active_heartbeat_with_persistent_child(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    lower_leader_tid = str(int(manager.tid) - 1)

    class FakeProcess:
        pid = 424245
        exitcode = None

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=lower_leader_tid,
                runtime_handle=_host_runtime_handle(os.getpid()),
            )
        )
    )
    manager._child_processes["persistent-child"] = ManagedChild(
        process=cast(BaseProcess, FakeProcess()),
        ctrl_queue=None,
        persistent=True,
    )

    try:
        manager._refresh_manager_registration(force=True)
    finally:
        manager._child_processes.clear()

    rows = _managed_service_owner_rows(make_queue)
    own_rows = [row for row in rows if row.get("owner_tid") == manager.tid]
    assert own_rows
    assert own_rows[-1]["status"] == "draining"
    assert not any(row.get("status") == "active" for row in own_rows)

    active = manager._active_dispatch_manager_records()
    assert active is not None
    assert manager.tid not in active
    assert lower_leader_tid in active


def test_manager_leadership_ignores_noncanonical_lower_manager(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    lower_tid = str(int(manager.tid) - 1)
    payload = _manager_service_payload(
        manager,
        tid=lower_tid,
        name="custom-manager",
        runtime_handle=_host_runtime_handle(os.getpid()),
        requests="custom.requests",
        ctrl_in="custom.ctrl_in",
        ctrl_out="custom.ctrl_out",
        outbox="custom.outbox",
    )
    payload["service_key"] = "manager:custom.requests:test"
    registry_queue.write(json.dumps(payload))

    yielded = manager._maybe_yield_leadership(force=True)

    assert yielded is False
    assert manager._draining is False
    assert manager.should_stop is False
    assert manager.taskspec.state.status == "running"


def test_manager_leadership_yields_to_canonical_lower_manager(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)
    lower_tid = str(int(manager.tid) - 1)
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=lower_tid,
                runtime_handle=_host_runtime_handle(os.getpid()),
            )
        )
    )

    yielded = manager._maybe_yield_leadership(force=True)

    assert yielded is True
    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"

    events = [json.loads(item) for item in drain(log_queue)]
    yield_event = next(
        event for event in events if event.get("event") == "manager_leadership_yielded"
    )
    assert yield_event["leader_tid"] == lower_tid


def test_manager_leadership_can_rescue_unreachable_host_pid_with_pong(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    lower_tid = str(int(manager.tid) - 1)
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=lower_tid,
                runtime_handle=_host_runtime_handle(987654321),
            )
        )
    )
    monkeypatch.setattr(
        manager_mod,
        "handle_has_live_host_process",
        lambda _handle: False,
    )

    assert manager._maybe_yield_leadership(force=True) is False
    pending = manager._leader_probe_pending[lower_tid]
    _write_manager_pong(
        manager,
        make_queue,
        pending,
        ctrl_in_name=f"T{lower_tid}.ctrl_in",
        ctrl_out_name=f"T{lower_tid}.ctrl_out",
    )

    yielded = manager._maybe_yield_leadership(force=True)
    assert yielded is True
    assert manager.should_stop is True


def test_active_child_launch_preserves_stored_leadership_pong_and_policy_clock(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    lower_tid = str(int(manager.tid) - 1)
    ctrl_in_name = f"T{lower_tid}.ctrl_in"
    ctrl_out_name = f"T{lower_tid}.ctrl_out"
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=lower_tid,
                runtime_handle=_host_runtime_handle(987654321),
            )
        )
    )
    monkeypatch.setattr(
        manager_mod,
        "handle_has_live_host_process",
        lambda _handle: False,
    )
    assert manager._maybe_yield_leadership(force=True) is False
    pending = manager._leader_probe_pending[lower_tid]
    _write_manager_pong(
        manager,
        make_queue,
        pending,
        ctrl_in_name=ctrl_in_name,
        ctrl_out_name=ctrl_out_name,
    )
    previous_policy_ns = time.time_ns()
    manager._last_leader_check_ns = previous_policy_ns
    manager._active_child_launches["pending-child"] = cast(Any, object())

    assert manager._maybe_yield_leadership() is False
    assert manager._leader_probe_pending[lower_tid].pong is not None
    assert manager._last_leader_check_ns == previous_policy_ns

    manager._active_child_launches.clear()
    assert manager._maybe_yield_leadership() is True
    assert lower_tid not in manager._leader_probe_pending
    assert manager._last_leader_check_ns == previous_policy_ns


def test_manager_superseded_self_record_stops_without_republishing_active(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=manager.tid,
                status=SERVICE_STATUS_SUPERSEDED,
                runtime_handle=_host_runtime_handle(os.getpid()),
            )
        )
    )

    manager._refresh_manager_registration(force=True)

    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"
    rows = _managed_service_owner_rows(make_queue)
    own_rows = [row for row in rows if row.get("owner_tid") == manager.tid]
    assert own_rows
    assert own_rows[-1]["status"] == SERVICE_STATUS_SUPERSEDED


def test_manager_superseded_self_record_drains_children_without_stopping(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    monkeypatch.setattr(manager, "_user_work_children", lambda: {"child-tid": object()})
    monkeypatch.setattr(
        manager,
        "_signal_children_to_stop",
        lambda: pytest.fail("superseded manager must not stop launched user work"),
    )

    manager._begin_superseded_shutdown()

    assert manager._draining is True
    assert manager._drain_stops_children is False
    assert manager.should_stop is False
    assert manager.taskspec.state.status == "running"


def test_manager_leadership_observes_superseded_services_row_without_spawn_probe(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    make_queue(WEFT_SERVICES_REGISTRY_QUEUE).write(
        json.dumps(
            _manager_service_payload(
                manager,
                tid=manager.tid,
                status=SERVICE_STATUS_SUPERSEDED,
                runtime_handle=_host_runtime_handle(os.getpid()),
            )
        )
    )
    monkeypatch.setattr(
        manager,
        "_has_actionable_leadership_work",
        lambda: pytest.fail("superseded registry proof must not inspect spawn work"),
    )
    monkeypatch.setattr(
        manager,
        "_internal_spawn_pending",
        lambda: pytest.fail("superseded registry proof must not inspect spawn queues"),
    )

    yielded = manager._maybe_yield_leadership(force=True)

    assert yielded is True
    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"


def test_manager_active_heartbeat_race_preserves_superseded_record(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-231] exception
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    original_queue = manager._queue
    superseded_payload = json.dumps(
        _manager_service_payload(
            manager,
            tid=manager.tid,
            status=SERVICE_STATUS_SUPERSEDED,
            runtime_handle=_host_runtime_handle(os.getpid()),
        )
    )

    class InterleavingRegistryQueue:
        def __init__(self) -> None:
            self.injected = False

        def write(self, payload: str) -> int | None:
            if not self.injected:
                self.injected = True
                for raw, timestamp in list(
                    registry_queue.peek_generator(with_timestamps=True)
                ):
                    try:
                        existing = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if (
                        isinstance(existing, dict)
                        and existing.get("owner_tid") == manager.tid
                    ):
                        registry_queue.delete(message_id=timestamp)
                registry_queue.write(superseded_payload)
            return registry_queue.write(payload)

        def peek_generator(
            self,
            *,
            with_timestamps: bool = False,
            after_timestamp: int | None = None,
            before_timestamp: int | None = None,
        ) -> Iterator[str | tuple[str, int]]:
            return registry_queue.peek_generator(
                with_timestamps=with_timestamps,
                after_timestamp=after_timestamp,
                before_timestamp=before_timestamp,
            )

        def delete(self, *args: object, **kwargs: object) -> object:
            return registry_queue.delete(*args, **kwargs)

    interleaving_queue = InterleavingRegistryQueue()

    def fake_queue(name: str, *args: object, **kwargs: object) -> object:
        if name == WEFT_SERVICES_REGISTRY_QUEUE:
            return interleaving_queue
        return original_queue(name, *args, **kwargs)

    monkeypatch.setattr(manager, "_queue", fake_queue)

    manager._refresh_manager_registration(force=True)

    rows = _managed_service_owner_rows(make_queue)
    own_rows = [row for row in rows if row.get("owner_tid") == manager.tid]
    assert [row["status"] for row in own_rows] == [SERVICE_STATUS_SUPERSEDED]
    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"


def test_cleanup_children_reaps_os_dead_child_without_mapping_scan(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class FakeProcess:
        pid = 424242
        exitcode = None

        def __init__(self) -> None:
            self.join_calls: list[float] = []

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(0.0 if timeout is None else float(timeout))

    fake_process = FakeProcess()
    manager._child_processes["child"] = ManagedChild(
        process=cast(BaseProcess, fake_process),
        ctrl_queue=None,
        persistent=False,
    )

    monkeypatch.setattr(manager_mod, "pid_is_live", lambda pid: False)

    def _unexpected_mapping_scan(_tid: str) -> set[int]:
        raise AssertionError("normal dead-child cleanup should not scan tid mappings")

    monkeypatch.setattr(manager, "_managed_pids_for_child", _unexpected_mapping_scan)

    manager._cleanup_children()

    assert manager._child_processes == {}
    assert fake_process.join_calls == [0.0, 0.1]


def test_cleanup_children_waits_for_terminal_proof_after_clean_exit(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    tid = "1777000000000000061"
    ctrl_out = f"T{tid}.ctrl_out"

    class FakeProcess:
        pid = 424245
        exitcode = 0

        def __init__(self) -> None:
            self.join_calls: list[float] = []

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(0.0 if timeout is None else float(timeout))

    now_ns = 2_000_000_000_000
    fake_process = FakeProcess()
    child = ManagedChild(
        process=cast(BaseProcess, fake_process),
        ctrl_queue=None,
        ctrl_out_queue=ctrl_out,
        launched_ns=now_ns - 1,
    )
    manager._child_processes[tid] = child
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)
    monkeypatch.setattr(manager, "_child_terminal_proof_visible", lambda *_args: False)

    manager._cleanup_children()

    assert manager._child_processes[tid] is child
    assert child.terminal_proof_missing_since_ns == now_ns
    assert fake_process.join_calls == []
    assert make_queue(ctrl_out).peek_one() is None

    later_ns = now_ns + int(
        (MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS + 0.1) * 1_000_000_000
    )
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: later_ns)

    manager._cleanup_children()

    assert tid not in manager._child_processes
    assert fake_process.join_calls == [0.1]
    payload = json.loads(str(make_queue(ctrl_out).read_one()))
    assert payload["status"] == "failed"
    assert payload["error"] == WRAPPER_LOST_ERROR


def test_child_has_exited_trusts_live_host_pid_before_process_view(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class FakeProcess:
        pid = 424243
        exitcode = None

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    monkeypatch.setattr(manager_mod, "pid_is_live", lambda pid: pid == 424243)

    assert (
        manager._child_has_exited(ManagedChild(cast(BaseProcess, FakeProcess()), None))
        is False
    )


def test_child_has_exited_allows_startup_liveness_visibility_grace(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class FakeProcess:
        pid = 424244
        exitcode = None

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    monkeypatch.setattr(manager_mod, "pid_is_live", lambda pid: False)

    child = ManagedChild(
        cast(BaseProcess, FakeProcess()), None, launched_ns=time.time_ns()
    )

    assert manager._child_has_exited(child) is False


def test_manager_autostart_templates(
    tmp_path: Path, broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env

    autostart_dir, template_path = write_autostart_fixture(
        tmp_path,
        task_name="simulate",
        manifest_name="watcher",
        mode="once",
        duration=0.2,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(unique_tid, inbox, ctrl_in, ctrl_out)

    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    try:
        start = time.time()
        while not manager._child_processes and time.time() - start < 5.0:
            manager.process_once()
            time.sleep(0.05)

        assert manager._child_processes, "expected autostart child"
        child_info = next(iter(manager._child_processes.values()))
        assert child_info.autostart_source == str(template_path.resolve())

        events = [json.loads(item) for item in drain(log_queue)]
        assert any(
            event.get("autostart_source") == str(template_path.resolve())
            for event in events
        ), "autostart launch should be logged"
    finally:
        manager.cleanup()


def test_manager_control_drain_yields_when_peek_message_does_not_advance(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
) -> None:
    manager, make_queue = manager_setup
    ctrl_name = manager._queue_names["ctrl_in"]
    reply_name = f"T{int(manager.tid) + 1}.ctrl_in"
    reply_queue = make_queue(reply_name)
    stuck_timestamp = 1777000000000005000

    class StuckControlQueue:
        name = ctrl_name

        def __init__(self) -> None:
            self.delete_calls = 0

        def peek_one(self, *, with_timestamps: bool = False) -> str | tuple[str, int]:
            payload = encode_control_message(
                CONTROL_PING,
                request_id="stuck-control",
                reply_to=reply_name,
            )
            if with_timestamps:
                return payload, stuck_timestamp
            return payload

        def delete(self, *, message_id: int | None = None) -> bool:
            assert message_id == stuck_timestamp
            self.delete_calls += 1
            return False

        def has_pending(self) -> bool:
            return True

        def close(self) -> None:
            pass

    stuck_queue = StuckControlQueue()
    manager._queue_cache[ctrl_name] = cast(Queue, stuck_queue)
    manager._queues[ctrl_name].queue = cast(Queue, stuck_queue)

    start = time.monotonic()
    manager._drain_control_queue_first()

    assert time.monotonic() - start < 2.0
    assert stuck_queue.delete_calls == 1
    raw_response = reply_queue.read_one()
    assert raw_response is not None
    response = json.loads(raw_response)
    assert response["command"] == CONTROL_PING
    assert response["request_id"] == "stuck-control"
    assert manager._stalled_control_message_id == stuck_timestamp

    manager._drain_control_queue_first()

    assert stuck_queue.delete_calls == 1
    assert reply_queue.read_one() is None
    assert manager._has_pending_messages() is False
    assert manager._control_allows_child_launch() is True


def test_manager_idle_shutdown(broker_env: BrokerEnv, unique_tid: str) -> None:
    db_path, _make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(
        unique_tid,
        inbox,
        ctrl_in,
        ctrl_out,
        idle_timeout=0.2,
    )
    manager = Manager(db_path, spec)
    try:
        start = time.time()
        while not manager.should_stop and time.time() - start < 2.0:
            manager.process_once()
            time.sleep(0.05)
        assert manager.should_stop is True
        assert manager.taskspec.state.status == "completed"
    finally:
        manager.cleanup()


def test_manager_control_replies_do_not_reset_idle_clock(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    """Only canonical control requests count as idle activity [MA-1.5].

    Verifies:
    - a PONG-shaped reply on the manager's own ctrl_in is consumed without
      resetting the idle clock
    - an unparseable control row is consumed without resetting it
    - a canonical control request still resets it
    """
    db_path, make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(unique_tid, inbox, ctrl_in, ctrl_out, idle_timeout=60.0)
    manager = Manager(db_path, spec)
    ctrl_queue = make_queue(ctrl_in)
    try:
        manager._last_activity_ns = 1
        ctrl_queue.write(
            json.dumps(
                {
                    "command": "PING",
                    "status": "ok",
                    "message": "PONG",
                    "tid": "1",
                    "task_status": "running",
                    "request_id": "unmatched-probe",
                }
            )
        )
        ctrl_queue.write("not-a-control-envelope")
        manager._drain_control_queue_first()

        assert ctrl_queue.peek_one() is None
        assert manager._last_activity_ns == 1

        ctrl_queue.write(encode_control_message("STATUS"))
        manager._drain_control_queue_first()

        assert ctrl_queue.peek_one() is None
        assert manager._last_activity_ns > 1
    finally:
        manager.cleanup()


def test_build_child_spec_propagates_unexpected_resolution_error(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    def _unexpected_validation_error(*args: object, **kwargs: object) -> TaskSpec:
        del args, kwargs
        raise RuntimeError("unexpected resolution bug")

    monkeypatch.setattr(
        "weft.core.manager.validate_taskspec_payload",
        _unexpected_validation_error,
    )

    with pytest.raises(RuntimeError, match="unexpected resolution bug"):
        manager._build_child_spec(make_child_spec(), int(time.time_ns()))


def test_manager_idle_timeout_ignores_unrelated_broker_activity(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(
        unique_tid,
        inbox,
        ctrl_in,
        ctrl_out,
        idle_timeout=0.2,
    )
    manager = Manager(db_path, spec)
    try:
        manager._last_activity_ns = time.time_ns() - 1_000_000_000

        activity_queue = make_queue("manager.activity")
        activity_queue.write("ping")

        start = time.time()
        while not manager.should_stop and time.time() - start < 2.0:
            manager.process_once()
            time.sleep(0.05)

        assert manager.should_stop is True
    finally:
        manager.cleanup()


def test_manager_idle_pending_work_includes_reserved_spawn_rows(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_manager_spec(unique_tid, idle_timeout=0.2)
    manager = Manager(db_path, spec)
    try:
        reserved_queue = make_queue(manager._queue_names["reserved"])
        reserved_queue.write(json.dumps({"taskspec": make_child_spec(size=1)}))

        assert manager._manager_owned_work_pending() is True

        manager._last_activity_ns = time.time_ns() - 1_000_000_000
        manager.process_once()

        assert manager.should_stop is False
    finally:
        manager.cleanup()


def test_manager_overrides_supplied_tid(
    manager_setup: tuple[Manager, Callable[[str], Queue]], unique_tid: str
) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    supplied_tid = unique_tid
    child_spec = {
        "tid": supplied_tid,
        "name": "child-explicit",
        "version": "1.0",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:provide_payload",
        },
        "io": {
            "inputs": {"inbox": f"T{supplied_tid}.inbox"},
            "outputs": {"outbox": f"T{supplied_tid}.outbox"},
            "control": {
                "ctrl_in": f"T{supplied_tid}.ctrl_in",
                "ctrl_out": f"T{supplied_tid}.ctrl_out",
            },
        },
        "state": {},
        "metadata": {},
    }

    inbox_queue.write(json.dumps({"taskspec": child_spec}))
    message_id = getattr(inbox_queue, "last_ts", None)
    assert isinstance(message_id, int)

    spawn_event = wait_for_log_event(
        manager,
        log_queue,
        lambda event: event.get("event") == "task_spawned",
        timeout=8.0,
    )
    wait_for_children(manager)

    events = [spawn_event]
    events.extend(json.loads(item) for item in drain(log_queue))
    spawn_events = [e for e in events if e["event"] == "task_spawned"]
    assert spawn_events, "Expected spawn event"
    assert spawn_events[-1]["child_tid"] == str(message_id)
    assert spawn_events[-1]["child_taskspec"]["tid"] == str(message_id)
    assert isinstance(spawn_events[-1]["child_pid"], int)
    assert (
        spawn_events[-1]["child_taskspec"]["state"]["pid"]
        == (spawn_events[-1]["child_pid"])
    )


def test_manager_idle_timeout_waits_for_active_child_to_finish(
    broker_env: BrokerEnv, unique_tid: str, tmp_path: Path
) -> None:
    db_path, make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(
        unique_tid,
        inbox,
        ctrl_in,
        ctrl_out,
        idle_timeout=0.2,
    )
    manager = Manager(db_path, spec)
    try:
        release_file = tmp_path / "release-child"
        inbox_queue = make_queue(manager._queue_names["inbox"])
        log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
        drain(log_queue)
        inbox_queue.write(
            json.dumps(
                {
                    "spec": {
                        "type": "function",
                        "function_target": "tests.tasks.sample_targets:wait_for_file",
                        "args": [str(release_file)],
                        "keyword_args": {"timeout": 30.0},
                    },
                }
            )
        )

        start = time.monotonic()
        while not manager._child_processes and time.monotonic() - start < 2.0:
            manager.process_once()
            time.sleep(0.05)
        assert manager._child_processes
        child_tid = next(iter(manager._child_processes))
        wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("tid") == child_tid and event.get("event") == "work_started"
            ),
            timeout=30.0 if os.name == "nt" else 20.0,
        )

        start = time.monotonic()
        while time.monotonic() - start < 0.35:
            manager.process_once()
            time.sleep(0.05)
        if not manager._child_processes:
            events = [json.loads(item) for item in drain(log_queue)]
            pytest.fail(
                "child exited before idle-timeout assertion; "
                f"manager_should_stop={manager.should_stop!r}; "
                f"events={events!r}"
            )
        assert manager.should_stop is False
        assert manager._child_processes

        release_file.touch()
        wait_for_children(manager, timeout=20.0)
        assert not manager._user_work_children()

        start = time.time()
        while not manager.should_stop and time.time() - start < 2.0:
            manager.process_once()
            time.sleep(0.05)
        assert manager.should_stop is True
    finally:
        manager.cleanup()


def test_manager_does_not_launch_child_when_initial_inbox_seed_fails(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config({"WEFT_MAX_MESSAGE_SIZE": 32768}),
    )
    child_tid = str(int(unique_tid) + 1)
    child = TaskSpec(
        tid=child_tid,
        name="manager-child",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:simulate_work",
        ),
        io=IOSection(
            inputs={"inbox": f"T{child_tid}.inbox"},
            outputs={"outbox": f"T{child_tid}.outbox"},
            control={
                "ctrl_in": f"T{child_tid}.ctrl_in",
                "ctrl_out": f"T{child_tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )

    try:
        # The real broker rejects oversized input before any launch can occur.
        launched = manager._launch_child_task(child, "x" * 32769)

        assert launched is False
        assert manager._child_processes == {}
    finally:
        manager.cleanup()


def test_manager_idle_timeout_does_not_kill_persistent_child(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(
        unique_tid,
        inbox,
        ctrl_in,
        ctrl_out,
        idle_timeout=0.2,
    )
    manager = Manager(db_path, spec)
    try:
        inbox_queue = make_queue(manager._queue_names["inbox"])
        child_tid = str(int(unique_tid) + 1)
        inbox_queue.write(
            json.dumps(
                {
                    "taskspec": {
                        "tid": child_tid,
                        "name": "persistent-child",
                        "version": "1.0",
                        "spec": {
                            "type": "function",
                            "persistent": True,
                            "function_target": "tests.tasks.sample_targets:echo_payload",
                        },
                        "io": {
                            "inputs": {"inbox": f"T{child_tid}.inbox"},
                            "outputs": {"outbox": f"T{child_tid}.outbox"},
                            "control": {
                                "ctrl_in": f"T{child_tid}.ctrl_in",
                                "ctrl_out": f"T{child_tid}.ctrl_out",
                            },
                        },
                        "state": {},
                        "metadata": {},
                    },
                    "inbox_message": None,
                }
            )
        )

        start = time.time()
        startup_timeout = 20.0 if os.name == "nt" else 5.0
        while not manager._child_processes and time.time() - start < startup_timeout:
            manager.process_once()
            time.sleep(0.05)
        assert manager._child_processes

        manager._last_activity_ns = time.time_ns() - 5_000_000_000
        for _ in range(5):
            manager.process_once()

        assert manager.should_stop is False
        assert manager._child_processes
    finally:
        manager.cleanup()


def test_manager_autostart_skips_active_templates(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env

    autostart_dir, template_path = write_autostart_fixture(
        tmp_path,
        task_name="observer",
        manifest_name="observer",
        mode="once",
        duration=0.1,
    )

    active_tid = str(int(unique_tid) - 1)
    _write_managed_service_owner(
        make_queue,
        service_key=str(template_path.resolve()),
        tid=active_tid,
        runtime_handle=_host_runtime_handle(os.getpid()),
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(unique_tid, inbox, ctrl_in, ctrl_out)

    manager = Manager(db_path, spec, config=config)
    try:
        manager.process_once()
        assert not manager._user_work_children()
        assert (
            manager._service_state(str(template_path.resolve())).launched_once is False
        )
    finally:
        manager.cleanup()


def test_manager_autostart_active_sources_include_tracked_children(
    tmp_path: Path, broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, _manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="tracked-autostart",
        manifest_name="tracked-autostart",
        mode="ensure",
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=0.0)
    manager = Manager(db_path, spec, config=config)
    source = str((autostart_dir / "tracked-autostart.json").resolve())
    inbox_queue = make_queue(manager._queue_names["inbox"])

    class FakeProcess:
        pid = None
        exitcode = None

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

    try:
        manager._child_processes["tracked-child"] = ManagedChild(
            process=cast(BaseProcess, FakeProcess()),
            ctrl_queue=None,
            persistent=False,
            autostart_source=source,
        )

        assert source in manager._active_autostart_sources()
        drain(inbox_queue)
        manager._tick_autostart(force=True)
        assert drain(inbox_queue) == []
    finally:
        inbox_queue.close()
        manager._child_processes.clear()
        manager.cleanup()


def test_manager_autostart_prunes_deleted_manifest_state(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    autostart_dir = tmp_path / "autostart"
    autostart_dir.mkdir()

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    stale_source = str((autostart_dir / "deleted.json").resolve())
    try:
        state = manager._service_state(stale_source)
        state.restarts = 2
        state.next_allowed_ns = time.time_ns()
        state.launched_once = True
        manager._autostart_sources.add(stale_source)

        manager._tick_autostart(force=True)

        assert stale_source not in manager._managed_service_state
        assert stale_source not in manager._autostart_sources
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_restarts(
    tmp_path: Path, broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env

    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart",
        manifest_name="restart",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=0,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)
    try:
        source = str(manifest_path.resolve())
        first_spawn = wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("event") == "task_spawned"
                and event.get("autostart_source") == source
            ),
        )
        first_child_tid = first_spawn.get("child_tid")
        assert isinstance(first_child_tid, str)
        completed_event = wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("tid") == first_child_tid
                and event.get("event") == "work_completed"
            ),
            timeout=30.0 if os.name == "nt" else 20.0,
        )
        preserve_consumed_log_event(log_queue, completed_event)
        wait_for_children(manager, timeout=20.0 if os.name == "nt" else 10.0)
        assert not manager._user_work_children()
        second_spawn = wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("event") == "task_spawned"
                and event.get("autostart_source") == source
            ),
        )
        assert first_spawn["child_tid"] != second_spawn["child_tid"]
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_restarts_after_child_exit_without_scan_wait(
    tmp_path: Path, broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, _make_queue = broker_env

    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart",
        manifest_name="restart",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=0,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    try:
        ctx = multiprocessing.get_context("spawn")
        child = ctx.Process(target=time.sleep, args=(0.0,))
        child.start()
        child.join(timeout=2.0)
        assert child.is_alive() is False

        manager._child_processes["child"] = ManagedChild(
            process=cast(BaseProcess, child),
            ctrl_queue=None,
            persistent=False,
            autostart_source=str(manifest_path.resolve()),
        )
        manager._autostart_last_scan_ns = 123_456_789

        manager._cleanup_children()

        assert manager._child_processes == {}
        assert manager._autostart_last_scan_ns == 0
    finally:
        manager.cleanup()


def test_manager_idle_shutdown_waits_for_autostart_ensure_restart_budget(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart-budget",
        manifest_name="restart-budget",
        mode="ensure",
        max_restarts=1,
        backoff_seconds=1.0,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=0.2)
    manager = Manager(db_path, spec, config=config)
    source = str(manifest_path.resolve())
    try:
        state = manager._service_state(source)
        state.restarts = 0
        state.next_allowed_ns = time.time_ns() + 1_000_000_000
        state.launched_once = True
        manager._last_activity_ns = time.time_ns() - 1_000_000_000
        manager._autostart_last_scan_ns = time.time_ns()
        manager._last_managed_service_convergence_ns = time.time_ns()

        manager.process_once()

        assert manager.should_stop is False
    finally:
        manager.cleanup()


def test_manager_autostart_stale_active_log_without_liveness_is_not_active(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env

    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart",
        manifest_name="restart",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=0,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    source = str(manifest_path.resolve())
    child_tid = str(int(unique_tid) - 1)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    try:
        ctx = multiprocessing.get_context("spawn")
        child = ctx.Process(target=time.sleep, args=(0.0,))
        child.start()
        child.join(timeout=2.0)
        assert child.is_alive() is False

        manager._child_processes[child_tid] = ManagedChild(
            process=cast(BaseProcess, child),
            ctrl_queue=None,
            persistent=False,
            autostart_source=source,
            service_key=source,
        )
        log_queue.write(
            json.dumps(
                {
                    "event": "task_started",
                    "tid": child_tid,
                    "status": "running",
                    "taskspec": {
                        "metadata": {
                            "autostart": True,
                            "autostart_source": source,
                        }
                    },
                }
            )
        )

        assert source not in manager._active_autostart_sources()

        manager._cleanup_children()

        assert manager._child_processes == {}
        assert source not in manager._active_autostart_sources()
    finally:
        log_queue.close()
        manager.cleanup()


def test_manager_autostart_pipeline_target_launches_pipeline_run(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_pipeline_fixture(
        tmp_path,
        task_name="simulate-bundle",
        pipeline_name="autostart-pipeline",
        manifest_name="autostart-pipeline",
        mode="once",
        function_target="tests.tasks.sample_targets:echo_payload",
        manifest_input="autostart-pipeline",
        task_bundle=True,
        pipeline_bundle=True,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    source = str(manifest_path.resolve())
    try:
        spawn_event, result_payload = _wait_for_autostart_pipeline_result(
            manager,
            log_queue,
            make_queue,
            source=source,
        )
        child_taskspec = spawn_event["child_taskspec"]
        assert child_taskspec["metadata"]["role"] == "pipeline"
        assert (
            child_taskspec["metadata"][INTERNAL_RUNTIME_TASK_CLASS_KEY]
            == INTERNAL_RUNTIME_TASK_CLASS_PIPELINE
        )
        assert result_payload == "autostart-pipeline"
    finally:
        manager.cleanup()


def test_manager_autostart_pipeline_compile_broker_error_is_retryable(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    autostart_dir, _manifest_path = write_autostart_pipeline_fixture(
        tmp_path,
        task_name="pipeline-broker-error-task",
        pipeline_name="pipeline-broker-error",
        manifest_name="pipeline-broker-error",
        mode="ensure",
        max_restarts=1,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    def fail_compile(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise BrokerError("disk I/O error")

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    try:
        monkeypatch.setattr(manager_mod, "compile_linear_pipeline", fail_compile)

        assert manager._load_autostart_pipeline("pipeline-broker-error") is None
    finally:
        manager.cleanup()


def test_manager_autostart_pipeline_ensure_restarts(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_pipeline_fixture(
        tmp_path,
        task_name="pipeline-restart-task",
        pipeline_name="pipeline-restart",
        manifest_name="pipeline-restart",
        mode="ensure",
        max_restarts=1,
        backoff_seconds=0,
        function_target="tests.tasks.sample_targets:echo_payload",
        manifest_input="restart-me",
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    source = str(manifest_path.resolve())
    try:
        first_spawn, first_result = _wait_for_autostart_pipeline_result(
            manager,
            log_queue,
            make_queue,
            source=source,
        )
        second_spawn, second_result = _wait_for_autostart_pipeline_result(
            manager,
            log_queue,
            make_queue,
            source=source,
        )

        assert first_spawn["child_tid"] != second_spawn["child_tid"]
        assert first_result == "restart-me"
        assert second_result == "restart-me"
        assert manager._service_state(source).restarts == 1
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_enqueue_failure_does_not_advance_state(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="retry-on-failure",
        manifest_name="retry-on-failure",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=1.0,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = False
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    source = str(manifest_path.resolve())
    try:
        manager._autostart_enabled = True
        monkeypatch.setattr(
            manager,
            "_enqueue_managed_service_request",
            lambda service: False,
        )

        manager._tick_autostart(force=True)

        state = manager._service_state(source)
        assert state.launched_once is False
        assert state.restarts == 0
        assert state.next_allowed_ns == 0
    finally:
        manager.cleanup()


def test_manager_autostart_pending_spawn_blocks_duplicate_restart(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="pending-restart",
        manifest_name="pending-restart",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=0,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = False
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    source = str(manifest_path.resolve())
    enqueued: list[Any] = []
    try:
        manifest = manager._load_autostart_manifest(manifest_path)
        assert manifest is not None
        spawn_payload = manager._build_autostart_spawn_payload(manifest, source)
        assert spawn_payload is not None
        taskspec_payload, inbox_message = spawn_payload
        make_queue(WEFT_SPAWN_REQUESTS_QUEUE).write(
            json.dumps(
                {
                    "taskspec": taskspec_payload,
                    "inbox_message": inbox_message,
                }
            )
        )
        manager._autostart_enabled = True
        monkeypatch.setattr(
            manager,
            "_enqueue_managed_service_request",
            lambda service: record_and_return(enqueued, service, True),
        )

        manager._tick_autostart(force=True)

        assert enqueued == []
        assert manager._service_state(source).spawn_pending is True
    finally:
        manager.cleanup()


def test_manager_autostart_active_launch_blocks_duplicate_restart(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="active-launch-restart",
        manifest_name="active-launch-restart",
        mode="ensure",
        max_restarts=1,
        backoff_seconds=0,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = False
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    source = str(manifest_path.resolve())
    enqueued: list[Any] = []
    try:
        manager._autostart_enabled = True
        state = manager._service_state(source)
        state.restarts = 0
        state.next_allowed_ns = 0
        state.launched_once = True
        manager._active_child_launches["active-launch-child"] = (
            manager_mod._ManagerChildLaunchRequest(
                child_spec=manager.taskspec,
                task_cls=Consumer,
                internal_role=None,
                service_key=source,
                autostart_source=source,
                detach_stdio=True,
            )
        )
        monkeypatch.setattr(
            manager,
            "_enqueue_managed_service_request",
            lambda service: record_and_return(enqueued, service, True),
        )

        manager._tick_autostart(force=True)

        assert enqueued == []
        assert manager._service_state(source).spawn_pending is True
        assert manager._service_state(source).restarts == 0
    finally:
        manager.cleanup()


def test_manager_autostart_spoofed_public_metadata_does_not_claim_manifest(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="spoofed-autostart",
        manifest_name="spoofed-autostart",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=0,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = False
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    source = str(manifest_path.resolve())
    spoof_tid = str(time.time_ns())
    make_queue(WEFT_GLOBAL_LOG_QUEUE).write(
        json.dumps(
            {
                "tid": spoof_tid,
                "status": "running",
                "taskspec": {
                    "metadata": {
                        INTERNAL_AUTOSTART_ENABLED_METADATA_KEY: True,
                        INTERNAL_AUTOSTART_SOURCE_METADATA_KEY: source,
                        INTERNAL_SERVICE_KEY_METADATA_KEY: source,
                        INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY: "ensure",
                    },
                    "io": {
                        "control": {
                            "ctrl_in": f"T{spoof_tid}.ctrl_in",
                            "ctrl_out": f"T{spoof_tid}.ctrl_out",
                        }
                    },
                },
            }
        )
    )
    try:
        monkeypatch.setattr(
            manager,
            "_evaluate_dispatch_ownership",
            lambda: DispatchOwnership(state="self", leader_tid=manager.tid),
        )
        enqueued: list[Any] = []
        monkeypatch.setattr(
            manager,
            "_enqueue_managed_service_request",
            lambda service: record_and_return(enqueued, service, True),
        )
        manager._autostart_enabled = True

        manager._tick_autostart(force=True)

        assert [service.key for service in enqueued] == [source]
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_allows_one_restart_after_initial_launch(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart-limit",
        manifest_name="restart-limit",
        mode="ensure",
        max_restarts=1,
        backoff_seconds=0,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    source = str(manifest_path.resolve())
    drain(log_queue)
    try:
        first_spawn = wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("event") == "task_spawned"
                and event.get("autostart_source") == source
            ),
            timeout=30.0 if os.name == "nt" else 20.0,
        )
        first_child_tid = first_spawn.get("child_tid")
        assert isinstance(first_child_tid, str)
        completed_event = wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("tid") == first_child_tid
                and event.get("event") == "work_completed"
            ),
            timeout=30.0 if os.name == "nt" else 20.0,
        )
        preserve_consumed_log_event(log_queue, completed_event)
        wait_for_children(manager, timeout=20.0 if os.name == "nt" else 10.0)

        second_spawn = wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("event") == "task_spawned"
                and event.get("autostart_source") == source
            ),
            timeout=30.0 if os.name == "nt" else 20.0,
        )
        assert first_spawn["child_tid"] != second_spawn["child_tid"]
        assert manager._service_state(source).restarts == 1

        spawn_events = [first_spawn, second_spawn]
        extra_deadline = time.time() + 1.0
        while time.time() < extra_deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event: dict[str, Any] = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    spawn_events.append(event)

        assert len(spawn_events) == 2
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_applies_backoff_to_restart_only(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart-backoff",
        manifest_name="restart-backoff",
        mode="ensure",
        max_restarts=1,
        backoff_seconds=0.5,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    source = str(manifest_path.resolve())
    drain(log_queue)
    try:
        first_spawn = wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("event") == "task_spawned"
                and event.get("autostart_source") == source
            ),
            timeout=8.0,
        )
        first_child_tid = first_spawn.get("child_tid")
        assert isinstance(first_child_tid, str)
        initial_restart_due_ns = manager._service_state(source).next_allowed_ns
        assert isinstance(initial_restart_due_ns, int)
        assert initial_restart_due_ns > 0
        completed_event = wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("tid") == first_child_tid
                and event.get("event") == "work_completed"
            ),
            timeout=30.0 if os.name == "nt" else 20.0,
        )
        preserve_consumed_log_event(log_queue, completed_event)
        wait_for_children(manager, timeout=20.0 if os.name == "nt" else 10.0)
        assert not manager._user_work_children()

        restart_events: list[dict[str, object]] = []
        deadline = time.time() + 8.0
        while not restart_events and time.time() < deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event: dict[str, Any] = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    restart_events.append(event)

        assert len(restart_events) == 1
        restart_timestamp = restart_events[0].get("timestamp")
        assert isinstance(restart_timestamp, int)
        assert restart_timestamp >= initial_restart_due_ns
    finally:
        manager.cleanup()


def test_manager_autostart_backoff_rescan_uses_due_time(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart-backoff-due",
        manifest_name="restart-backoff-due",
        mode="ensure",
        max_restarts=1,
        backoff_seconds=0.5,
        duration=0.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = False
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    manager._autostart_enabled = True
    source = str(manifest_path.resolve())
    enqueued: list[Any] = []

    now_ns = 1_000_000_000
    fake_time = SimpleNamespace(
        time=time.time,
        time_ns=lambda: now_ns,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )
    monkeypatch.setattr(manager_mod, "time", fake_time)

    def enqueue(service: Any) -> bool:
        enqueued.append(service)
        return True

    monkeypatch.setattr(manager, "_enqueue_managed_service_request", enqueue)

    try:
        manager._tick_autostart(force=True)
        assert len(enqueued) == 1
        assert manager._service_state(source).next_allowed_ns == 1_500_000_000
        manager._autostart_last_scan_ns = 0

        now_ns = 1_250_000_000
        manager._tick_autostart()
        assert len(enqueued) == 1

        now_ns = 1_499_999_999
        manager._tick_autostart()
        assert len(enqueued) == 1

        now_ns = 1_500_000_000
        manager._tick_autostart()
        assert len(enqueued) == 2
        assert manager._service_state(source).restarts == 1
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_restarts_after_abrupt_child_kill(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart-after-kill",
        manifest_name="restart-after-kill",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=0,
        duration=10.0,
    )

    config = dict(load_config())
    config["AUTOSTART_TASKS"] = True
    config["AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    source = str(manifest_path.resolve())
    try:
        spawn_events: list[dict[str, object]] = []
        event_tail: list[dict[str, object]] = []
        child_tid: str | None = None
        start = time.monotonic()
        while not spawn_events and time.monotonic() - start < 5.0:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event: dict[str, Any] = json.loads(item)
                event_tail.append(event)
                event_tail = event_tail[-10:]
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    spawn_events.append(event)
                    event_child_tid = event.get("child_tid")
                    if isinstance(event_child_tid, str):
                        child_tid = event_child_tid

        assert spawn_events, f"expected autostart spawn event; tail={event_tail!r}"
        assert child_tid is not None, (
            f"spawn event missing child tid: {spawn_events[-1]!r}"
        )
        assert child_tid in manager._child_processes
        child = manager._child_processes[child_tid].process
        child_pid = child.pid
        assert isinstance(child_pid, int) and child_pid > 0
        live_deadline = time.monotonic() + 2.0
        while not _process_running(child_pid) and time.monotonic() < live_deadline:
            time.sleep(0.05)
        assert _process_running(child_pid) is True
        assert len(spawn_events) == 1

        child.kill()
        child.join(timeout=2.0)

        kill_deadline = time.monotonic() + 2.0
        while _process_running(child_pid) and time.monotonic() < kill_deadline:
            time.sleep(0.05)
        assert _process_running(child_pid) is False

        restart_timeout = 30.0 if active_test_backend() == "postgres" else 8.0
        second_spawn = wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("event") == "task_spawned"
                and event.get("autostart_source") == source
            ),
            timeout=restart_timeout,
        )
        spawn_events.append(second_spawn)
        assert len(spawn_events) >= 2
    finally:
        manager.cleanup()


def _host_runtime_handle_with_create_time(
    pid: int, create_time: float
) -> dict[str, object]:
    """Host runtime handle carrying both bare PIDs and create-time identities."""
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {
            "host_pids": [pid],
            "host_processes": [{"pid": pid, "create_time": create_time}],
        },
        "metadata": {},
    }


def test_managed_pids_for_child_excludes_create_time_mismatch(
    tmp_path: Path,
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    """Shutdown reap must not target a recycled PID (create-time mismatch).

    A stale ``weft.state.tasks.<tid>`` row may hold a PID the OS has recycled to
    an unrelated process. ``_managed_pids_for_child`` feeds the forced-shutdown
    ``terminate_process_tree`` loop, so it must return only PIDs whose recorded
    creation time still matches the live process. The bare-PID path would return
    the recycled PID and kill an unrelated process tree.

    Spec: [MA-1] item 4 (create-time validation), item 7 (force-reap authority).
    """
    db_path, make_queue = broker_env
    spec = make_manager_spec(unique_tid, weft_context=str(tmp_path / "project"))
    manager = Manager(db_path, spec, config=load_config())
    try:
        live_pid = os.getpid()
        actual_create_time = process_create_time(live_pid)
        assert actual_create_time is not None

        # Recycled PID: live, but the recorded create_time no longer matches.
        stale_tid = "1000000000000000001"
        make_queue(task_state_queue_name(stale_tid)).write(
            json.dumps(
                {
                    "full": stale_tid,
                    "short": stale_tid[-8:],
                    "runtime_handle": _host_runtime_handle_with_create_time(
                        live_pid, actual_create_time - 100.0
                    ),
                }
            )
        )
        assert manager._managed_pids_for_child(stale_tid) == set()

        # Genuine match: same PID with its actual create_time stays targetable.
        match_tid = "1000000000000000002"
        make_queue(task_state_queue_name(match_tid)).write(
            json.dumps(
                {
                    "full": match_tid,
                    "short": match_tid[-8:],
                    "runtime_handle": _host_runtime_handle_with_create_time(
                        live_pid, actual_create_time
                    ),
                }
            )
        )
        assert manager._managed_pids_for_child(match_tid) == {live_pid}
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_failed_launch_clear_policy_preserves_failed_delete_residue(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failed Manager CLEAR applies once and retains the request for recovery."""
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(
            unique_tid, idle_timeout=0.0, reserved_policy_on_error=ReservedPolicy.CLEAR
        ),
        config=load_config(
            {"WEFT_TASK_MONITOR_ENABLED": "0", "WEFT_LIVENESS_MONITOR_ENABLED": "0"}
        ),
    )
    public = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    reserved_name = manager._queue_names["reserved"]
    reserved = make_queue(reserved_name)
    drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
    request = json.dumps(make_child_spec())
    public.write(request)
    manager._mark_pending_messages_prechecked()
    real_queue = manager._queue
    calls = []

    class FailedDeleteQueue:
        def __getattr__(self, name: str) -> Any:
            return getattr(reserved, name)

        def delete(self, **kwargs: Any) -> bool:
            calls.append(kwargs)
            raise RuntimeError("injected Manager CLEAR failure")

    monkeypatch.setattr(
        manager,
        "_queue",
        lambda name: FailedDeleteQueue() if name == reserved_name else real_queue(name),
    )
    monkeypatch.setattr(
        manager,
        "_start_service_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("launch unavailable")
        ),
    )
    try:
        manager.process_once()
        assert len(calls) == 1
        assert reserved.peek_one() == request
        assert public.peek_one() is None
        warnings = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING
            and "clear reserved spawn message" in record.getMessage()
        ]
        assert len(warnings) == 1
        assert str(calls[0]["message_id"]) in warnings[0].getMessage()
        assert reserved_name in warnings[0].getMessage()
    finally:
        manager.stop(join=False)
        manager.cleanup()


@pytest.mark.parametrize("age_seconds", [30, 120])
def test_manager_leadership_unknown_expiry_controls_ping_without_changing_evidence(
    manager_setup: tuple[Manager, Callable[[str], Queue]],
    monkeypatch: pytest.MonkeyPatch,
    age_seconds: int,
) -> None:
    manager, make_queue = manager_setup
    tid = str(int(manager.tid) - 1)
    now_ns = time.time_ns()
    monkeypatch.setattr(
        manager_mod, "MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS", 60.0
    )
    monkeypatch.setattr(
        manager_runtime_mod,
        "runtime_liveness_from_registered_probe",
        lambda handle: "unknown",
    )
    record = _manager_service_record(
        manager,
        tid=tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        ctrl_in=f"T{tid}.ctrl_in",
        ctrl_out=f"T{tid}.ctrl_out",
    )
    record["_timestamp"] = now_ns - age_seconds * 1_000_000_000
    assert manager._manager_record_liveness(record) == "unknown"
    proof = manager._manager_leadership_proof(record, now_ns=now_ns, allow_ping=True)
    pings = [json.loads(message) for message in drain(make_queue(f"T{tid}.ctrl_in"))]
    if age_seconds > 60:
        assert proof.liveness == "stale"
        assert proof.reason == "expired"
        assert pings == []
        assert tid not in manager._leader_probe_pending
    else:
        assert proof.liveness == "unknown"
        assert proof.reason == "ping_pending"
        assert pings == [
            {
                "command": CONTROL_PING,
                "request_id": manager._leader_probe_pending[tid].request_id,
                "reply_to": manager._queue_names["ctrl_in"],
            }
        ]


@pytest.mark.parametrize("child_tid", ["unresolved", "123"])
def test_child_launch_invalid_state_tid_still_checks_durable_log(
    tmp_path: Path, child_tid: str
) -> None:
    manager = Manager(tmp_path / "manager.db", make_manager_spec(str(time.time_ns())))
    try:
        assert manager._latest_tid_runtime_handle(child_tid) is None
        assert not manager._child_launch_runtime_evidence_seen(child_tid)
        manager._queue(WEFT_GLOBAL_LOG_QUEUE).write(
            json.dumps({"event": "task_spawned", "child_tid": child_tid})
        )
        assert manager._child_launch_runtime_evidence_seen(child_tid)
    finally:
        manager.cleanup()
