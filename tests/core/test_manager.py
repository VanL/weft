"""Tests for Manager functionality."""

from __future__ import annotations

import itertools
import json
import multiprocessing
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import weft.core.manager as manager_mod
from simplebroker.ext import BrokerError
from tests.helpers.test_backend import active_test_backend
from weft._constants import (
    CONTROL_KILL,
    CONTROL_PING,
    CONTROL_STOP,
    INTERNAL_AUTOSTART_ENABLED_METADATA_KEY,
    INTERNAL_AUTOSTART_SOURCE_METADATA_KEY,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS,
    MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS,
    MANAGER_CHILD_EXIT_POLL_INTERVAL,
    MANAGER_LEADERSHIP_CHECK_INTERVAL_SECONDS,
    MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS,
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    PIPELINE_RUNTIME_METADATA_KEY,
    SERVICE_OWNER_SCHEMA,
    SERVICE_STATUS_ACTIVE,
    SERVICE_STATUS_SUPERSEDED,
    SERVICE_TYPE_MANAGED,
    TERMINAL_ENVELOPE_TYPE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_MANAGER_CTRL_IN_QUEUE,
    WEFT_MANAGER_CTRL_OUT_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    WRAPPER_LOST_ERROR,
    load_config,
)
from weft.core.manager import DispatchOwnership, ManagedChild, Manager
from weft.core.service_convergence import (
    build_manager_service_payload,
    build_service_owner_payload,
)
from weft.core.tasks import (
    Consumer,
    HeartbeatTask,
    PipelineEdgeTask,
    PipelineTask,
    TaskMonitorTask,
)
from weft.core.tasks.multiqueue_watcher import QueueMessageContext, QueueMode
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec
from weft.helpers import ContainerRuntimeDetection

AUTOSTART_PIPELINE_RESULT_TIMEOUT = 30.0


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


def drain(queue):
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


def pending_timestamps(queue) -> list[int]:
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
        "observations": {"host_pids": [pid]},
        "metadata": {},
    }


def _write_managed_service_owner(
    make_queue: Callable[[str], Any],
    *,
    service_key: str,
    tid: str,
    runtime_handle: dict[str, object] | None = None,
    status: str = "active",
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
    status: str = "active",
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
            "ctrl_in": ctrl_in or WEFT_MANAGER_CTRL_IN_QUEUE,
            "ctrl_out": ctrl_out or WEFT_MANAGER_CTRL_OUT_QUEUE,
            "outbox": outbox,
        },
        runtime_handle=runtime_handle or {},
    )


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


def _expire_service_probe(manager: Manager, probe: Any) -> None:
    manager._service_probe_pending[probe.key] = probe.__class__(
        key=probe.key,
        service_key=probe.service_key,
        tid=probe.tid,
        row_timestamp=probe.row_timestamp,
        source=probe.source,
        ctrl_in_name=probe.ctrl_in_name,
        ctrl_out_name=probe.ctrl_out_name,
        request_id=probe.request_id,
        deadline_ns=time.time_ns() - 1,
    )


def _write_service_pong(
    make_queue: Callable[[str], Any],
    probe: Any,
) -> None:
    make_queue(probe.ctrl_out_name).write(
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
    ctrl_in: str = WEFT_MANAGER_CTRL_IN_QUEUE,
    ctrl_out: str = WEFT_MANAGER_CTRL_OUT_QUEUE,
    *,
    idle_timeout: float | None = None,
    role: str | None = None,
    weft_context: str | None = None,
) -> TaskSpec:
    metadata = {"capabilities": ["tests.tasks.sample_targets:large_output"]}
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
        ),
        io=IOSection(
            inputs={"inbox": inbox},
            outputs={"outbox": WEFT_MANAGER_OUTBOX_QUEUE},
            control={
                "ctrl_in": ctrl_in,
                "ctrl_out": ctrl_out,
            },
        ),
        state=StateSection(),
        metadata=metadata,
    )


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
    broker_env,
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


def _wait_for_autostart_pipeline_result(
    manager: Manager,
    log_queue,
    make_queue,
    *,
    source: str,
    timeout: float = AUTOSTART_PIPELINE_RESULT_TIMEOUT,
) -> tuple[dict[str, object], object]:
    deadline = time.monotonic() + timeout
    spawn_event: dict[str, object] | None = None
    outbox_queue = None
    status_queue = None
    event_tail: list[dict[str, object]] = []
    status_tail: list[object] = []

    while time.monotonic() < deadline:
        manager.process_once()
        for item in drain(log_queue):
            event = json.loads(item)
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

        if status_queue is not None:
            status_tail.extend(
                _decode_queue_payload(item) for item in drain(status_queue)
            )
            status_tail = status_tail[-8:]

        if outbox_queue is not None:
            raw = outbox_queue.read_one()
            if raw is not None:
                return spawn_event or {}, _decode_queue_payload(raw)

        time.sleep(0.05)

    raise AssertionError(
        "Timed out waiting for autostart pipeline result "
        f"after {timeout:.1f}s; spawn_event={spawn_event!r}; "
        f"event_tail={event_tail!r}; status_tail={status_tail!r}"
    )


@pytest.fixture
def manager_setup(broker_env, unique_tid):
    db_path, make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(unique_tid, inbox, ctrl_in, ctrl_out)
    manager = Manager(db_path, spec)
    yield manager, make_queue
    manager.stop(join=False)
    manager.cleanup()


def wait_for_children(manager: Manager, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while manager._user_work_children() and time.monotonic() < deadline:
        manager._cleanup_children()
        time.sleep(0.05)


def wait_for_log_event(
    manager: Manager,
    log_queue: Any,
    predicate: Callable[[dict[str, object]], bool],
    *,
    timeout: float = 8.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    event_tail: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        manager.process_once()
        while True:
            item = log_queue.read_one()
            if item is None:
                break
            event = json.loads(item)
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


def drive_manager_until(
    manager: Manager,
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        manager.process_once()
        if predicate():
            return
        manager.wait_for_activity(timeout=0.02)
    raise AssertionError("Manager did not reach expected state before timeout")


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
        self.exitcode = -signal.SIGKILL


def _process_running(pid: int) -> bool:
    psutil = pytest.importorskip("psutil")
    try:
        process = psutil.Process(pid)
    except psutil.Error:
        return False
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _write_descendant_process_scripts(tmp_path: Path) -> tuple[Path, Path]:
    child_script = tmp_path / "manager_cleanup_child_sleep.py"
    child_script.write_text(
        """
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> None:
    Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
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


def test_manager_spawns_child(manager_setup) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

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
            event = json.loads(item)
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
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    ctrl_in = make_queue(manager._queue_names["ctrl_in"])
    ctrl_out = make_queue(manager._queue_names["ctrl_out"])
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

    assert elapsed < 1.0
    assert launch_started.wait(timeout=2.0)
    assert manager._active_child_launches

    ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "during"}))
    manager.process_once()

    responses = [json.loads(item) for item in drain(ctrl_out)]
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


def test_manager_launch_worker_success_commits_once_on_main_thread(
    manager_setup,
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
            event = json.loads(item)
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
    manager_setup,
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
    manager_setup,
) -> None:
    manager, _make_queue = manager_setup
    child_spec = manager._build_child_spec(make_child_spec(), int(time.time_ns()))
    assert child_spec is not None

    assert manager._resolve_child_task_class(child_spec) is Consumer


def test_manager_launches_pipeline_task_for_reserved_internal_class(
    manager_setup,
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
    assert manager._resolve_child_task_class(monitor_spec) is TaskMonitorTask


def test_manager_rejects_unknown_internal_task_class(manager_setup) -> None:
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
    broker_env,
    unique_tid,
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


def test_manager_service_enqueue_forces_next_internal_queue_probe(
    broker_env,
    unique_tid,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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
        manager._check_counter = 1

        assert manager._enqueue_managed_service_request(
            manager._task_monitor_service_spec()
        )
        manager._drain_queue()
    finally:
        manager.cleanup()

    assert seen == ["task-monitor"]


def test_manager_convergence_drains_pending_internal_spawn_work(
    broker_env,
    unique_tid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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
    broker_env,
    unique_tid,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "0",
            MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: True,
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
    broker_env,
    unique_tid,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "0",
            MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: True,
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
    broker_env,
    unique_tid,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "0",
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
    broker_env,
    unique_tid,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: True,
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
    broker_env,
    unique_tid,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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


def test_manager_enqueues_heartbeat_through_service_path(
    broker_env,
    unique_tid,
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
    broker_env,
    unique_tid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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


def test_custom_inbox_manager_does_not_consume_internal_spawn_queue(
    broker_env,
    unique_tid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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
        lambda child_spec, *_args, **_kwargs: launched.append(child_spec.name) or True,
    )

    try:
        manager.process_once()
    finally:
        manager.cleanup()

    assert launched == []
    assert internal_queue.peek_one() is not None


def test_internal_spawn_launch_failure_leaves_internal_reserved_visible(
    broker_env,
    unique_tid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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
    finally:
        manager.cleanup()

    assert internal_queue.peek_one(exact_timestamp=message_id) is None
    assert public_queue.peek_one(exact_timestamp=message_id) is None
    assert internal_reserved.peek_one(exact_timestamp=message_id) is not None
    events = [json.loads(item) for item in drain(log_queue)]
    assert not any(
        str(event.get("event", "")).startswith("manager_spawn_fence")
        for event in events
    )


def test_task_monitor_spawn_payload_uses_manager_owned_envelope(
    manager_setup,
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
    manager_setup,
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

    manager._tick_task_monitor(force=True)

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
    manager_setup,
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
        process=FakeProcess(),
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._cleanup_children()
    manager._tick_task_monitor()

    assert manager._task_monitor_tid is None
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR not in enqueued

    manager._task_monitor_next_start_allowed_ns = 0
    manager._tick_task_monitor()

    assert INTERNAL_SERVICE_KEY_TASK_MONITOR in enqueued


def test_task_monitor_terminal_tracked_child_allows_restart(
    manager_setup,
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
        process=FakeLiveProcess(),
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor()

    assert manager._task_monitor_tid is None
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR in enqueued


def test_stable_managed_service_convergence_uses_audit_interval(
    manager_setup,
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
        lambda: calls.append("cleanup") and False,
    )

    def reconcile(*, include_autostart: bool = True) -> None:
        calls.append(f"reconcile:{include_autostart}")

    monkeypatch.setattr(manager, "_reconcile_managed_services", reconcile)

    manager._run_managed_service_convergence(include_autostart=False)

    assert calls == []

    manager._last_managed_service_convergence_ns = time.time_ns() - 10_000_000_000
    manager._run_managed_service_convergence(include_autostart=False)

    assert calls == ["cleanup", "reconcile:False"]


def test_active_managed_service_convergence_uses_active_interval(
    manager_setup,
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
        lambda: calls.append("cleanup") and False,
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


def test_managed_service_convergence_active_reasons_are_stable(
    manager_setup,
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


def test_throttled_managed_service_convergence_skips_broker_work(
    manager_setup,
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
        lambda: calls.append("internal_pending") or False,
    )
    monkeypatch.setattr(
        manager,
        "_cleanup_children",
        lambda: calls.append("cleanup") or False,
    )
    monkeypatch.setattr(
        manager,
        "_pending_service_keys",
        lambda _keys: calls.append("pending_keys") or set(),
    )
    monkeypatch.setattr(
        manager,
        "_observed_service_candidates_by_key",
        lambda _keys, **_kwargs: calls.append("observed") or {},
    )

    manager._run_managed_service_convergence(include_autostart=False)

    assert calls == []


def test_manager_leadership_yield_rate_gate_precedes_actionable_work(
    manager_setup,
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
        lambda: pytest.fail("registry read must not run before rate gate"),
    )

    assert manager._maybe_yield_leadership() is False


def _prime_manager_next_wait_baseline(manager: Manager, now_ns: int) -> None:
    manager.should_stop = False
    manager._draining = False
    manager._pending_termination_signal = None
    manager._managed_internal_spawn_enqueued = False
    manager._stalled_control_retry_after_ns = 0
    manager._child_processes.clear()
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
    manager_setup,
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
        process=SimpleNamespace(pid=1234),
        ctrl_queue="Tchild.ctrl_in",
        ctrl_out_queue="Tchild.ctrl_out",
        service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
    )
    manager._child_processes["1777000000000000051"] = child
    try:
        assert manager.next_wait_timeout() == pytest.approx(
            MANAGER_CHILD_EXIT_POLL_INTERVAL
        )
    finally:
        manager._child_processes.pop("1777000000000000051", None)


def test_manager_next_wait_timeout_does_not_child_poll_supervision_only_services(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: now_ns)
    _prime_manager_next_wait_baseline(manager, now_ns)
    child = ManagedChild(
        process=SimpleNamespace(pid=1234),
        ctrl_queue="Tchild.ctrl_in",
        ctrl_out_queue="Tchild.ctrl_out",
        persistent=True,
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
        service_key=INTERNAL_SERVICE_KEY_HEARTBEAT,
    )
    manager._child_processes["1777000000000000052"] = child
    try:
        assert manager.next_wait_timeout() > MANAGER_CHILD_EXIT_POLL_INTERVAL
    finally:
        manager._child_processes.pop("1777000000000000052", None)


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("should_stop", True),
        ("_draining", True),
        ("_pending_termination_signal", signal.SIGTERM),
        ("_managed_internal_spawn_enqueued", True),
    ],
)
def test_manager_next_wait_timeout_returns_zero_for_immediate_work(
    manager_setup,
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


def test_manager_wait_for_activity_passes_timeout_to_shared_waiter(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    timeouts: list[float | None] = []

    class RecordingWaiter:
        def wait(self, timeout: float | None) -> None:
            timeouts.append(timeout)

    monkeypatch.setattr(manager, "_has_pending_messages", lambda: False)
    monkeypatch.setattr(
        manager,
        "_ensure_multi_activity_waiter",
        lambda: RecordingWaiter(),
    )

    manager.wait_for_activity(timeout=0.2)

    assert timeouts == [0.2]


def test_manager_wait_for_activity_fallback_honors_timeout(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    wait_timeouts: list[float | None] = []
    reset_calls: list[bool] = []

    class FailingWaiter:
        def wait(self, timeout: float | None) -> None:
            raise RuntimeError(f"wait failed after {timeout}")

    class FakeStopEvent:
        def wait(self, timeout: float | None) -> bool:
            wait_timeouts.append(timeout)
            return False

        def is_set(self) -> bool:
            return False

        def set(self) -> None:
            pass

    monkeypatch.setattr(manager, "_has_pending_messages", lambda: False)
    monkeypatch.setattr(
        manager, "_ensure_multi_activity_waiter", lambda: FailingWaiter()
    )
    monkeypatch.setattr(
        manager, "_reset_multi_activity_waiter", lambda: reset_calls.append(True)
    )
    manager._stop_event = FakeStopEvent()

    manager.wait_for_activity(timeout=0.2)

    assert reset_calls == [True]
    assert wait_timeouts == [0.2]


def test_manager_leadership_yield_memoizes_per_turn_until_invalidated(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    now_ns = 2_000_000_000_000
    current_ns = {"value": now_ns}
    actionable_calls = 0
    registry_calls = 0

    def count_actionable_work() -> bool:
        nonlocal actionable_calls
        actionable_calls += 1
        return False

    def count_registry_reads() -> dict[str, dict[str, object]]:
        nonlocal registry_calls
        registry_calls += 1
        return {manager.tid: {"tid": manager.tid}}

    monkeypatch.setattr(manager_mod.time, "time_ns", lambda: current_ns["value"])
    monkeypatch.setattr(
        manager, "_has_actionable_leadership_work", count_actionable_work
    )
    monkeypatch.setattr(manager, "_read_active_manager_records", count_registry_reads)
    manager._loop_iteration = 42
    manager._leader_check_interval_ns = int(
        MANAGER_LEADERSHIP_CHECK_INTERVAL_SECONDS * 1_000_000_000
    )
    manager._last_leader_check_ns = now_ns - manager._leader_check_interval_ns - 1

    assert manager._maybe_yield_leadership() is False
    current_ns["value"] += manager._leader_check_interval_ns + 1
    assert manager._maybe_yield_leadership() is False

    assert actionable_calls == 1
    assert registry_calls == 1

    manager._invalidate_leadership_work_cache()
    current_ns["value"] += manager._leader_check_interval_ns + 1

    assert manager._maybe_yield_leadership() is False
    assert actionable_calls == 2
    assert registry_calls == 2


def test_tracked_service_candidate_uses_live_child_without_terminal_scan(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    child = ManagedChild(
        process=SimpleNamespace(pid=1234),
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
    manager_setup,
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

    monkeypatch.setattr(manager, "_pending_service_keys", lambda _keys: set())
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
    manager_setup,
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
        process=FakeLiveProcess(),
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor(force=True)

    assert manager._task_monitor_tid is None
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR in enqueued


def test_task_monitor_manager_spawned_pid_counts_as_live_owner(
    manager_setup,
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor()

    assert manager._task_monitor_tid == child_tid
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR not in enqueued


def test_task_monitor_terminal_tracked_child_does_not_hide_new_live_owner(
    manager_setup,
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
        process=FakeLiveProcess(),
        ctrl_queue=f"T{old_tid}.ctrl_in",
        ctrl_out_queue=old_ctrl_out,
        persistent=True,
        internal_role=INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    )
    manager._child_processes[new_tid] = ManagedChild(
        process=FakeLiveProcess(),
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor()

    assert manager._task_monitor_tid == new_tid
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR not in enqueued


def test_task_monitor_stale_log_without_liveness_does_not_block_restart(
    manager_setup,
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor(force=True)

    assert enqueued == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]


def test_task_monitor_recent_log_without_liveness_blocks_duplicate_restart(
    manager_setup,
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor(force=True)

    assert enqueued == [INTERNAL_SERVICE_KEY_HEARTBEAT]


def test_managed_service_pong_probe_is_nonblocking(
    manager_setup,
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
    ping_messages = [json.loads(item) for item in drain(make_queue(probe.ctrl_in_name))]
    assert ping_messages == [{"command": CONTROL_PING, "request_id": probe.request_id}]

    _write_service_pong(make_queue, probe)
    candidates = manager._observed_service_candidates_by_key({service_key})[service_key]

    live_candidate = next(
        candidate for candidate in candidates if candidate.tid == old_tid
    )
    assert live_candidate.state == "live"
    assert live_candidate.source == "service-registry-pong"


def test_managed_service_observation_prunes_superseded_service_history(
    manager_setup,
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
        (second_tid, "active")
    ]


def test_managed_service_dead_registered_pid_is_terminal_without_recent_grace(
    manager_setup,
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
    manager_setup,
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
        lambda pid, *, timeout=0.5: killed.append(pid) or {pid},
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        manager,
        "_enqueue_managed_service_request",
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor(force=True)

    assert make_queue(f"T{canonical_tid}.ctrl_in").read_one() is None
    assert make_queue(f"T{duplicate_tid}.ctrl_in").read_one() == CONTROL_KILL
    assert killed == []
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR not in enqueued


def test_task_monitor_duplicate_manager_spawned_candidates_do_not_force_kill_raw_pid(
    manager_setup,
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
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: pid is not None)
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor(force=True)

    assert make_queue(f"T{canonical_tid}.ctrl_in").read_one() is None
    assert make_queue(f"T{duplicate_tid}.ctrl_in").read_one() == CONTROL_KILL
    assert killed == []
    assert INTERNAL_SERVICE_KEY_TASK_MONITOR not in enqueued


def test_task_monitor_duplicate_tracked_child_force_kills_owned_process(
    manager_setup,
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
        process=FakeLiveProcess(duplicate_pid),
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

    assert make_queue(f"T{duplicate_tid}.ctrl_in").read_one() == CONTROL_KILL
    assert killed == [(duplicate_pid, 0.2)]


def test_task_monitor_duplicate_runtime_handle_force_kills_scoped_host_pid(
    manager_setup,
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

    manager._tick_task_monitor(force=True)

    assert make_queue(f"T{canonical_tid}.ctrl_in").read_one() is None
    assert make_queue(f"T{duplicate_tid}.ctrl_in").read_one() == CONTROL_KILL
    assert killed == [(duplicate_pid, 0.2)]


def test_task_monitor_pending_spawn_request_blocks_duplicate_restart(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    manager._task_monitor_enabled = True
    manager._queue_names["inbox"] = WEFT_SPAWN_REQUESTS_QUEUE
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor(force=True)

    assert enqueued == [INTERNAL_SERVICE_KEY_HEARTBEAT]
    assert manager._task_monitor_spawn_pending is True


def test_task_monitor_spoofed_pending_spawn_without_internal_envelope_does_not_block(
    manager_setup,
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor(force=True)

    assert enqueued == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]
    assert manager._task_monitor_spawn_pending is True


def test_process_once_reconciles_internal_services_before_user_spawn_work(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config({"WEFT_TASK_MONITOR_ENABLED": False}),
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
            process=FakeDeadProcess(),
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

        def record_service_enqueue(service) -> bool:
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


def test_process_once_launches_service_spawn_in_same_reconcile_turn(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    manager = Manager(
        db_path,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config({"WEFT_TASK_MONITOR_ENABLED": False}),
    )
    drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))
    manager._task_monitor_enabled = True
    manager._task_monitor_restart_backoff_ns = 0
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
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert "task-monitor" in launched


def test_task_monitor_spoofed_public_metadata_does_not_claim_singleton(
    manager_setup,
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor(force=True)

    assert enqueued == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]
    assert manager._task_monitor_tid is None


def test_task_monitor_latest_terminal_log_overrides_older_running_evidence(
    manager_setup,
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor(force=True)

    assert enqueued == [
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    ]
    assert manager._task_monitor_tid is None


def test_task_monitor_matching_pong_blocks_duplicate_restart(
    manager_setup,
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
        lambda service: enqueued.append(service.key) or True,
    )

    manager._tick_task_monitor(force=True)

    assert enqueued == [INTERNAL_SERVICE_KEY_HEARTBEAT]
    assert manager._task_monitor_tid == old_tid


def test_internal_task_monitor_child_does_not_block_idle_shutdown(
    broker_env,
    unique_tid,
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
            process=FakeProcess(),
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


def test_manager_closes_seeded_child_inbox_queue(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    class FakeSeedQueue:
        def __init__(self) -> None:
            self.writes: list[str] = []
            self.closed = False

        def write(self, payload: str) -> None:
            self.writes.append(payload)

        def close(self) -> None:
            self.closed = True

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
    seed_queue = FakeSeedQueue()
    original_get_queue = manager.get_queue

    def fake_get_queue(name: str):
        if name == "seeded.inbox":
            return seed_queue
        return original_get_queue(name)

    monkeypatch.setattr(manager, "get_queue", fake_get_queue)

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
    assert seed_queue.writes == [json.dumps({"args": ["payload"]})]
    assert seed_queue.closed is False

    manager.cleanup()

    assert seed_queue.closed is True


def test_manager_terminal_envelope_does_not_cache_child_ctrl_out_queue(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    child_tid = str(time.time_ns())
    child_ctrl_out = f"T{child_tid}.ctrl_out"

    class FakeTerminalQueue:
        instances: list[FakeTerminalQueue] = []

        def __init__(self, name: str, *args: object, **kwargs: object) -> None:
            del args, kwargs
            self.name = name
            self.writes: list[str] = []
            self.closed = False
            FakeTerminalQueue.instances.append(self)

        def peek_generator(self, *, with_timestamps: bool = False):
            del with_timestamps
            return iter(())

        def write(self, payload: str) -> None:
            self.writes.append(payload)

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        pid = None
        exitcode = 1

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    monkeypatch.setattr(manager_mod, "Queue", FakeTerminalQueue)
    child = ManagedChild(
        process=FakeProcess(),
        ctrl_queue=None,
        ctrl_out_queue=child_ctrl_out,
    )

    manager._write_manager_terminal_envelope(child_tid, child)

    assert child_ctrl_out not in manager._queue_cache
    assert [queue.name for queue in FakeTerminalQueue.instances] == [
        child_ctrl_out,
        child_ctrl_out,
    ]
    assert all(queue.closed for queue in FakeTerminalQueue.instances)
    assert len(FakeTerminalQueue.instances[1].writes) == 1
    payload = json.loads(FakeTerminalQueue.instances[1].writes[0])
    assert payload["type"] == TERMINAL_ENVELOPE_TYPE
    assert payload["source"] == "manager"
    assert payload["tid"] == child_tid
    assert payload["status"] == "failed"
    assert payload["error"] == WRAPPER_LOST_ERROR
    assert payload["return_code"] == 1


def test_manager_terminal_envelope_skips_when_task_terminal_proof_exists(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
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

    class FakeTerminalQueue:
        instances: list[FakeTerminalQueue] = []

        def __init__(self, name: str, *args: object, **kwargs: object) -> None:
            del args, kwargs
            self.name = name
            self.writes: list[str] = []
            self.closed = False
            FakeTerminalQueue.instances.append(self)

        def peek_generator(self, *, with_timestamps: bool = False):
            del with_timestamps
            return iter(((terminal_payload, time.time_ns()),))

        def write(self, payload: str) -> None:
            self.writes.append(payload)

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        pid = None
        exitcode = 1

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    monkeypatch.setattr(manager_mod, "Queue", FakeTerminalQueue)
    child = ManagedChild(
        process=FakeProcess(),
        ctrl_queue=None,
        ctrl_out_queue=child_ctrl_out,
    )

    manager._write_manager_terminal_envelope(child_tid, child)

    assert [queue.name for queue in FakeTerminalQueue.instances] == [child_ctrl_out]
    assert FakeTerminalQueue.instances[0].writes == []
    assert FakeTerminalQueue.instances[0].closed is True


def test_manager_registry_entries(manager_setup) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    entries = [json.loads(item) for item in drain(registry_queue)]
    relevant = [entry for entry in entries if entry.get("tid") == manager.tid]
    assert len(relevant) == 1
    assert relevant[0]["status"] == "active"
    manager.cleanup()
    entries = [json.loads(item) for item in drain(registry_queue)]
    relevant = [entry for entry in entries if entry.get("tid") == manager.tid]
    assert len(relevant) == 1
    assert relevant[0]["status"] == "stopped"


def test_manager_refreshes_active_registry_heartbeat(
    manager_setup,
    monkeypatch,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    before = pending_timestamps(registry_queue)

    monkeypatch.setattr(manager_mod, "MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS", 0.0)
    manager._refresh_manager_registration()

    after = pending_timestamps(registry_queue)
    entries = [json.loads(item) for item in drain(registry_queue)]
    relevant = [entry for entry in entries if entry.get("tid") == manager.tid]
    assert len(before) == 1
    assert len(after) == 1
    assert after != before
    assert len(relevant) == 1
    assert relevant[0]["status"] == "active"


def test_manager_supersedes_fresh_higher_tid_active_refresh(
    manager_setup,
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
    higher_rows = [row for row in rows if row.get("tid") == higher_tid]
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
    higher_rows = [row for row in rows if row.get("tid") == higher_tid]
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
    higher_rows = [row for row in rows if row.get("tid") == higher_tid]
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


def test_manager_registry_prunes_expired_rows_on_refresh(
    manager_setup,
    monkeypatch,
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
    monkeypatch.setattr(manager, "_manager_registry_retention_ns", lambda: 0)
    monkeypatch.setattr(
        manager_mod,
        "MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        -1.0,
    )
    manager._refresh_manager_registration()

    entries = [json.loads(item) for item in drain(registry_queue)]
    assert all(entry.get("name") != "old-manager" for entry in entries)
    assert any(entry.get("name") == "task-monitor" for entry in entries)
    assert all(entry.get("owner_tid") != "not-a-tid" for entry in entries)
    assert [entry["tid"] for entry in entries if entry.get("tid") == manager.tid] == [
        manager.tid
    ]


def test_manager_publishes_inactive_when_recent_lower_canonical_manager_exists(
    manager_setup,
    monkeypatch,
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
    assert [entry["tid"] for entry in entries] == [lower_tid, manager.tid]
    own_entry = entries[-1]
    assert own_entry["status"] == "draining"


def test_manager_registers_when_lower_canonical_manager_is_stale(
    manager_setup,
    monkeypatch,
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
    manager._leader_probe_pending[lower_tid] = pending.__class__(
        tid=pending.tid,
        row_timestamp=pending.row_timestamp,
        ctrl_in_name=pending.ctrl_in_name,
        ctrl_out_name=pending.ctrl_out_name,
        request_id=pending.request_id,
        deadline_ns=time.time_ns() - 1,
    )
    manager._refresh_manager_registration()

    entries = [json.loads(item) for item in drain(registry_queue)]
    tids = {entry["tid"] for entry in entries}
    assert lower_tid not in tids
    assert manager.tid in tids


def test_manager_leadership_ping_probe_is_nonblocking(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    lower_tid = str(int(manager.tid) - 1)
    ctrl_in_name = f"T{lower_tid}.ctrl_in"
    ctrl_out_name = f"T{lower_tid}.ctrl_out"
    record = _manager_service_payload(
        manager,
        tid=lower_tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        ctrl_in=ctrl_in_name,
        ctrl_out=ctrl_out_name,
    )

    assert not hasattr(manager_mod, "send_keyed_ping_probe")

    proof = manager._manager_pong_dispatch_proof(record, now_ns=time.time_ns())

    assert proof.liveness == "unknown"
    assert proof.reason == "ping_pending"
    pending = manager._leader_probe_pending[lower_tid]
    ping_messages = [json.loads(item) for item in drain(make_queue(ctrl_in_name))]
    assert ping_messages == [
        {"command": CONTROL_PING, "request_id": pending.request_id}
    ]

    manager._leader_probe_pending[lower_tid] = pending.__class__(
        tid=pending.tid,
        row_timestamp=pending.row_timestamp,
        ctrl_in_name=pending.ctrl_in_name,
        ctrl_out_name=pending.ctrl_out_name,
        request_id=pending.request_id,
        deadline_ns=time.time_ns() - 1,
    )
    expired = manager._manager_pong_dispatch_proof(record, now_ns=time.time_ns())

    assert expired.liveness == "unknown"
    assert expired.reason == "ping_timeout"
    assert lower_tid not in manager._leader_probe_pending


def test_manager_leadership_ping_probe_accepts_later_pong(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, make_queue = manager_setup
    lower_tid = str(int(manager.tid) - 1)
    ctrl_in_name = f"T{lower_tid}.ctrl_in"
    ctrl_out_name = f"T{lower_tid}.ctrl_out"
    record = _manager_service_payload(
        manager,
        tid=lower_tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        ctrl_in=ctrl_in_name,
        ctrl_out=ctrl_out_name,
    )

    assert not hasattr(manager_mod, "send_keyed_ping_probe")

    initial = manager._manager_pong_dispatch_proof(record, now_ns=time.time_ns())
    assert initial.reason == "ping_pending"
    pending = manager._leader_probe_pending[lower_tid]
    make_queue(ctrl_out_name).write(
        json.dumps(
            {
                "command": CONTROL_PING,
                "status": "ok",
                "message": "PONG",
                "request_id": pending.request_id,
                "tid": lower_tid,
                "task_status": "running",
                "role": "manager",
                "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                "ctrl_in": ctrl_in_name,
                "ctrl_out": ctrl_out_name,
                "weft_context": str(manager._manager_context().root),
            }
        )
    )

    proof = manager._manager_pong_dispatch_proof(record, now_ns=time.time_ns())

    assert proof.liveness == "live"
    assert proof.dispatch_eligible is True
    assert proof.source == "control-pong"
    assert lower_tid not in manager._leader_probe_pending


def test_manager_leadership_keeps_namespace_ambiguous_host_row_after_ping_timeout(
    manager_setup,
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
        "detect_container_runtime",
        lambda: ContainerRuntimeDetection(
            runtime="docker",
            markers=("dockerenv",),
            identifier="container123",
        ),
    )
    monkeypatch.setattr(
        manager_mod,
        "handle_has_live_host_process",
        lambda _handle: False,
    )

    assert manager._maybe_yield_leadership(force=True) is False
    pending = manager._leader_probe_pending[lower_tid]
    manager._leader_probe_pending[lower_tid] = pending.__class__(
        tid=pending.tid,
        row_timestamp=pending.row_timestamp,
        ctrl_in_name=pending.ctrl_in_name,
        ctrl_out_name=pending.ctrl_out_name,
        request_id=pending.request_id,
        deadline_ns=time.time_ns() - 1,
    )

    assert manager._maybe_yield_leadership(force=True) is False
    assert manager.should_stop is False
    rows = _managed_service_owner_rows(make_queue)
    assert any(row.get("tid") == lower_tid for row in rows)


def test_manager_unknown_lower_owner_does_not_suppress_or_yield(
    manager_setup,
    monkeypatch,
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
        manager_mod,
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
    manager_setup,
    monkeypatch,
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
        manager_mod,
        "runtime_liveness_from_registered_probe",
        lambda handle: "live",
    )

    assert manager._maybe_yield_leadership(force=True) is True
    assert manager.should_stop is True


def test_manager_pong_from_draining_candidate_is_not_dispatch_eligible(
    manager_setup,
) -> None:
    manager, _make_queue = manager_setup
    record = _manager_service_payload(
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
        "ctrl_in": WEFT_MANAGER_CTRL_IN_QUEUE,
        "ctrl_out": WEFT_MANAGER_CTRL_OUT_QUEUE,
        "weft_context": str(manager._manager_context().root),
    }

    assert not manager._pong_dispatch_eligible(
        payload,
        record=record,
        ctrl_in_name=WEFT_MANAGER_CTRL_IN_QUEUE,
        ctrl_out_name=WEFT_MANAGER_CTRL_OUT_QUEUE,
    )


def test_manager_leadership_drain_resumes_when_leader_proof_disappears(
    manager_setup,
    monkeypatch,
) -> None:
    manager, _make_queue = manager_setup
    lower_tid = str(int(manager.tid) - 1)
    manager._begin_leadership_drain(leader_tid=lower_tid)
    monkeypatch.setattr(manager, "_active_dispatch_manager_records", lambda: {})

    manager._continue_shutdown_drain()

    assert manager._draining is False
    assert manager.should_stop is False
    assert manager._unregistered is False


def test_manager_liveness_rejects_stale_external_supervisor_record(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        manager_mod,
        "MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        -1.0,
    )
    record = {
        "tid": "1761000000000000010",
        "status": "active",
        "runtime_handle": _external_supervisor_runtime_handle(),
        "_timestamp": time.time_ns(),
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
    }

    assert Manager._manager_record_is_live(record) is False


def test_manager_liveness_rejects_missing_docker_supervisor_record(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        manager_mod,
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
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        manager_mod,
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
    monkeypatch,
) -> None:
    monkeypatch.setattr("weft.helpers.process_create_time", lambda pid: 222.0)
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

    assert Manager._manager_record_is_live(record) is False


def test_manager_liveness_treats_host_pid_miss_as_unknown_inside_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manager_mod,
        "detect_container_runtime",
        lambda: ContainerRuntimeDetection(
            runtime="docker",
            markers=("dockerenv",),
            identifier="container123",
        ),
    )
    monkeypatch.setattr(
        manager_mod,
        "handle_has_live_host_process",
        lambda _handle: False,
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
    manager_setup,
    monkeypatch,
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
    unique_tid: str,
) -> None:
    class _FailingRegistryQueue:
        def generate_timestamp(self) -> int:
            return 123

        def delete(self, *, message_id: int | None = None) -> bool:
            del message_id
            raise BrokerError("registry delete failed")

        def write(self, _payload: str) -> None:
            raise BrokerError("registry write failed")

    manager = object.__new__(Manager)
    manager._unregistered = False
    manager._registry_message_id = 456
    manager.tid = unique_tid
    manager.taskspec = make_manager_spec(
        unique_tid,
        f"manager.{unique_tid}.inbox",
        f"manager.{unique_tid}.ctrl_in",
        f"manager.{unique_tid}.ctrl_out",
    )
    manager._queue_names = {
        "inbox": f"manager.{unique_tid}.inbox",
        "ctrl_in": f"manager.{unique_tid}.ctrl_in",
        "ctrl_out": f"manager.{unique_tid}.ctrl_out",
        "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
        "reserved": f"T{unique_tid}.reserved",
    }
    manager._queue = lambda _name: _FailingRegistryQueue()
    manager._latest_registry_entry = lambda _queue, _tid: None

    manager._unregister_manager()

    assert manager._unregistered is True
    assert manager._registry_message_id is None


def test_manager_tid_mapping_forces_role_manager(broker_env, unique_tid) -> None:
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
        mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
        entries = [json.loads(item) for item in drain(mapping_queue)]
        relevant = [entry for entry in entries if entry.get("full") == manager.tid]
        assert relevant
        assert relevant[-1]["role"] == "manager"
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_tid_mapping_defaults_role_manager(manager_setup) -> None:
    manager, make_queue = manager_setup
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    entries = [json.loads(item) for item in drain(mapping_queue)]
    relevant = [entry for entry in entries if entry.get("full") == manager.tid]
    assert relevant
    assert relevant[-1]["role"] == "manager"


def test_manager_cleanup_sends_stop_to_children(manager_setup) -> None:
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
    start = time.time()
    while not manager._child_processes and time.time() - start < 5.0:
        manager.process_once()
        time.sleep(0.05)

    assert manager._child_processes, "child process should be running"
    child_tid, child_info = next(iter(manager._child_processes.items()))
    ctrl_queue = make_queue(child_info.ctrl_queue or f"T{child_tid}.ctrl_in")
    assert child_info.process.is_alive()

    manager.cleanup()

    messages: list[str] = []
    while True:
        raw = ctrl_queue.read_one()
        if raw is None:
            break
        messages.append(raw)

    assert messages == [] or any(message == CONTROL_STOP for message in messages)
    assert not _process_running(child_info.process.pid)


def test_manager_cleanup_terminates_worker_descendants(
    manager_setup,
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

    start = time.time()
    while not manager._child_processes and time.time() - start < 5.0:
        manager.process_once()
        time.sleep(0.05)

    assert manager._child_processes, "child process should be running"
    child_tid, child_info = next(iter(manager._child_processes.items()))

    worker_pid = _wait_for_pidfile(
        child_pidfile,
        timeout=20.0 if os.name == "nt" else 10.0,
    )
    assert _process_running(worker_pid), f"expected worker descendant for {child_tid}"

    try:
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
        if _process_running(worker_pid):
            try:
                psutil.Process(worker_pid).kill()
            except psutil.Error:
                pass
            _wait_for_pid_exit(worker_pid, timeout=2.0)


def test_manager_cleanup_terminates_reaped_child_managed_pids(
    manager_setup, monkeypatch: pytest.MonkeyPatch
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
        process=FakeProcess(),
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

    manager._terminate_children()

    assert manager._child_processes == {}
    assert terminated == [(515151, 0.2, True)]


def test_manager_stop_command_drains_nonpersistent_children(manager_setup) -> None:
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

    ctrl_in_queue.write(CONTROL_STOP)

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
def test_manager_sigterm_drains_nonpersistent_children(manager_setup) -> None:
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
    assert child_info.process.is_alive()
    wait_for_log_event(
        manager,
        log_queue,
        lambda event: (
            event.get("tid") == _child_tid and event.get("event") == "work_started"
        ),
        timeout=30.0 if os.name == "nt" else 20.0,
    )

    manager.handle_termination_signal(signal.SIGTERM)

    assert manager._pending_termination_signal == signal.SIGTERM
    assert manager._draining is False
    assert manager.should_stop is False
    assert manager.taskspec.state.status == "running"

    deadline = time.time() + 5.0
    while time.time() < deadline and not manager.should_stop:
        manager.process_once()
        time.sleep(0.05)

    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"
    assert not _process_running(child_info.process.pid)

    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "task_signal_stop" for event in events)
    assert not any(event.get("event") == "task_signal_kill" for event in events)


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals required")
def test_foreground_serve_sigterm_uses_async_drain_path(manager_setup) -> None:
    """Foreground serve SIGTERM must not do broker work in the signal handler."""

    manager, _make_queue = manager_setup
    manager.taskspec.metadata["foreground_serve"] = True
    drain_started = False
    original_terminate_children = manager._terminate_children
    original_begin_shutdown_drain = manager._begin_shutdown_drain

    def fail_if_signal_handler_starts_drain(*args, **kwargs) -> None:
        nonlocal drain_started
        drain_started = True
        raise AssertionError("signal handler must not synchronously start draining")

    def fail_if_signal_handler_terminates_children() -> None:
        raise AssertionError("signal handler must not synchronously terminate children")

    try:
        manager._begin_shutdown_drain = fail_if_signal_handler_starts_drain  # type: ignore[method-assign]
        manager._terminate_children = fail_if_signal_handler_terminates_children  # type: ignore[method-assign]

        manager.handle_termination_signal(signal.SIGTERM)

        assert drain_started is False
        assert manager._pending_termination_signal == signal.SIGTERM
        assert manager._draining is False
        assert manager.should_stop is False
        assert manager.taskspec.state.status == "running"
    finally:
        manager._terminate_children = original_terminate_children  # type: ignore[method-assign]
        manager._begin_shutdown_drain = original_begin_shutdown_drain  # type: ignore[method-assign]

    manager.process_once()

    assert manager._pending_termination_signal is None
    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"


def test_manager_drain_timeout_force_finishes_stubborn_children(
    manager_setup,
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
        process=StubbornProcess(),  # type: ignore[arg-type]
        ctrl_queue=ctrl_queue_name,
        persistent=False,
    )
    terminated = False

    def finish_stubborn_child() -> None:
        nonlocal terminated
        terminated = True
        manager._child_processes.clear()

    monkeypatch.setattr(manager_mod, "MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(manager, "_terminate_children", finish_stubborn_child)

    manager._begin_graceful_shutdown(message_id=None)
    manager.process_once()

    assert ctrl_queue.read_one() == CONTROL_STOP
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
def test_manager_sigusr1_keeps_kill_semantics(manager_setup) -> None:
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

    manager.handle_termination_signal(signal.SIGUSR1)
    assert manager._pending_termination_signal == signal.SIGUSR1

    manager.process_once()

    assert manager.should_stop is True
    assert manager.taskspec.state.status == "killed"
    assert not _process_running(child_info.process.pid)

    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "task_signal_kill" for event in events)


def test_manager_stop_command_does_not_launch_new_children_after_stop(
    manager_setup,
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

    ctrl_in_queue.write(CONTROL_STOP)

    deadline = time.time() + (20.0 if os.name == "nt" else 10.0)
    max_children_seen = len(manager._child_processes)
    while time.time() < deadline and not manager.should_stop:
        manager.process_once()
        max_children_seen = max(max_children_seen, len(manager._child_processes))
        time.sleep(0.05)

    assert manager.should_stop is True
    assert max_children_seen == 1

    events = [json.loads(item) for item in drain(log_queue)]
    spawn_events = [event for event in events if event.get("event") == "task_spawned"]
    assert len(spawn_events) == 1
    assert any(event.get("event") == "control_stop" for event in events)


def test_manager_drain_reissues_stop_for_child_added_after_stop(
    manager_setup,
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
        process=FakeProcess(),
        ctrl_queue=ctrl_queue_name,
        persistent=False,
    )

    manager.process_once()

    assert ctrl_queue.read_one() == CONTROL_STOP


def test_manager_stop_mid_handler_requeues_reserved_work_unlaunched(
    manager_setup, monkeypatch: pytest.MonkeyPatch
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
        ctrl_in_queue.write(CONTROL_STOP)
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
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
    active_records: dict[str, dict[str, object]] | None,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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

    monkeypatch.setattr(manager, "_read_active_manager_records", lambda: active_records)
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
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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
        lambda: {lower_leader_tid: {"tid": lower_leader_tid}},
    )
    monkeypatch.setattr(
        manager,
        "_active_dispatch_manager_records",
        lambda: {lower_leader_tid: {"tid": lower_leader_tid}},
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


def test_manager_leadership_waits_when_reserved_public_work_exists(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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

    monkeypatch.setattr(manager, "_leader_tid", lambda: lower_leader_tid)

    try:
        yielded = manager._maybe_yield_leadership(force=True)
    finally:
        manager.stop(join=False)
        manager.cleanup()

    assert moved == (json.dumps(payload), message_id)
    assert yielded is False
    assert manager.should_stop is False
    assert reserved_queue.peek_one(exact_timestamp=message_id) is not None


def test_manager_leadership_waits_while_child_launch_is_in_flight(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup
    manager._active_child_launches["pending-child"] = object()  # type: ignore[assignment]

    def fail_read_active_records() -> dict[str, dict[str, Any]]:
        raise AssertionError("active child launch should block leadership yield check")

    monkeypatch.setattr(
        manager, "_read_active_manager_records", fail_read_active_records
    )

    yielded = manager._maybe_yield_leadership(force=True)

    assert yielded is False
    assert manager.should_stop is False


def test_manager_services_successfully_reserved_public_work(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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


def test_manager_pending_precheck_forces_inactive_public_queue_probe(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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
    manager._check_counter = 1
    manager._pending_messages_precheck_confirmed = False
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


def test_manager_spawn_drains_do_not_depend_on_pending_hint(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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

    assert launched == ["internal-first"] + [f"public-{index}" for index in range(5)]
    assert internal_queue.peek_one() is None
    assert internal_reserved.peek_one() is None
    assert spawn_queue.peek_one() is None
    assert reserved_queue.peek_one() is None


def test_manager_service_convergence_advances_without_dispatch_ownership(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_ENABLED": "0"})
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
    manager_setup, monkeypatch: pytest.MonkeyPatch
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
        process=FakeProcess(),
        ctrl_queue=ctrl_queue_name,
        persistent=False,
    )

    lower_leader_tid = str(int(manager.tid) - 1)
    unregister_calls: list[str] = []

    monkeypatch.setattr(
        manager,
        "_read_active_manager_records",
        lambda: {lower_leader_tid: {"tid": lower_leader_tid}},
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
    manager_setup, monkeypatch: pytest.MonkeyPatch
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
        process=FakeProcess(),
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
    manager_setup,
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
        process=FakeProcess(),
        ctrl_queue=None,
        persistent=True,
    )

    try:
        manager._refresh_manager_registration(force=True)
    finally:
        manager._child_processes.clear()

    rows = _managed_service_owner_rows(make_queue)
    own_rows = [row for row in rows if row.get("tid") == manager.tid]
    assert own_rows
    assert own_rows[-1]["status"] == "draining"
    assert not any(row.get("status") == "active" for row in own_rows)

    active = manager._active_dispatch_manager_records()
    assert active is not None
    assert manager.tid not in active
    assert lower_leader_tid in active


def test_manager_leadership_ignores_noncanonical_lower_manager(
    manager_setup,
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
    manager_setup,
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
    manager_setup,
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

    assert not hasattr(manager_mod, "send_keyed_ping_probe")

    assert manager._maybe_yield_leadership(force=True) is False
    pending = manager._leader_probe_pending[lower_tid]
    make_queue(WEFT_MANAGER_CTRL_OUT_QUEUE).write(
        json.dumps(
            {
                "command": CONTROL_PING,
                "status": "ok",
                "message": "PONG",
                "request_id": pending.request_id,
                "tid": lower_tid,
                "task_status": "running",
                "role": "manager",
                "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                "ctrl_in": WEFT_MANAGER_CTRL_IN_QUEUE,
                "ctrl_out": WEFT_MANAGER_CTRL_OUT_QUEUE,
                "should_stop": False,
            }
        )
    )

    yielded = manager._maybe_yield_leadership(force=True)
    assert yielded is True
    assert manager.should_stop is True


def test_manager_superseded_self_record_stops_without_republishing_active(
    manager_setup,
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
    own_rows = [row for row in rows if row.get("tid") == manager.tid]
    assert own_rows
    assert own_rows[-1]["status"] == SERVICE_STATUS_SUPERSEDED


def test_manager_active_heartbeat_race_preserves_superseded_record(
    manager_setup,
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
                        and existing.get("tid") == manager.tid
                    ):
                        registry_queue.delete(message_id=timestamp)
                registry_queue.write(superseded_payload)
            return registry_queue.write(payload)

        def peek_generator(self, *args: object, **kwargs: object) -> object:
            return registry_queue.peek_generator(*args, **kwargs)

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
    own_rows = [row for row in rows if row.get("tid") == manager.tid]
    assert [row["status"] for row in own_rows] == [SERVICE_STATUS_SUPERSEDED]
    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"


def test_cleanup_children_reaps_os_dead_child_without_mapping_scan(
    manager_setup, monkeypatch: pytest.MonkeyPatch
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
        process=fake_process,
        ctrl_queue=None,
        persistent=False,
    )

    monkeypatch.setattr(manager, "_pid_alive", lambda pid: False)

    def _unexpected_mapping_scan(_tid: str) -> set[int]:
        raise AssertionError("normal dead-child cleanup should not scan tid mappings")

    monkeypatch.setattr(manager, "_managed_pids_for_child", _unexpected_mapping_scan)

    manager._cleanup_children()

    assert manager._child_processes == {}
    assert fake_process.join_calls == [0.0, 0.1]


def test_child_has_exited_trusts_live_host_pid_before_process_view(
    manager_setup,
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

    monkeypatch.setattr(manager, "_pid_alive", lambda pid: pid == 424243)

    assert manager._child_has_exited(ManagedChild(FakeProcess(), None)) is False


def test_child_has_exited_allows_startup_liveness_visibility_grace(
    manager_setup,
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

    monkeypatch.setattr(manager, "_pid_alive", lambda pid: False)

    child = ManagedChild(FakeProcess(), None, launched_ns=time.time_ns())

    assert manager._child_has_exited(child) is False


def test_manager_autostart_templates(tmp_path: Path, broker_env, unique_tid) -> None:
    db_path, make_queue = broker_env

    autostart_dir, template_path = write_autostart_fixture(
        tmp_path,
        task_name="simulate",
        manifest_name="watcher",
        mode="once",
        duration=0.2,
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
    manager_setup,
) -> None:
    manager, make_queue = manager_setup
    ctrl_name = manager._queue_names["ctrl_in"]
    ctrl_out = make_queue(manager._queue_names["ctrl_out"])
    stuck_timestamp = 1777000000000005000

    class StuckControlQueue:
        name = ctrl_name

        def __init__(self) -> None:
            self.delete_calls = 0

        def peek_one(self, *, with_timestamps: bool = False):
            payload = json.dumps(
                {"command": CONTROL_PING, "request_id": "stuck-control"}
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
    manager._queue_cache[ctrl_name] = stuck_queue
    manager._queues[ctrl_name].queue = stuck_queue

    start = time.monotonic()
    manager._drain_control_queue_first()

    assert time.monotonic() - start < 0.5
    assert stuck_queue.delete_calls == 1
    response = json.loads(ctrl_out.read_one())
    assert response["command"] == CONTROL_PING
    assert response["request_id"] == "stuck-control"
    assert manager._stalled_control_message_id == stuck_timestamp

    manager._drain_control_queue_first()

    assert stuck_queue.delete_calls == 1
    assert ctrl_out.read_one() is None
    assert manager._has_pending_messages() is False
    assert manager._control_allows_child_launch() is True


def test_manager_idle_shutdown(broker_env, unique_tid) -> None:
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
        start = time.time()
        while not manager.should_stop and time.time() - start < 2.0:
            manager.process_once()
            time.sleep(0.05)
        assert manager.should_stop is True
        assert manager.taskspec.state.status == "completed"
    finally:
        manager.cleanup()


def test_build_child_spec_propagates_unexpected_resolution_error(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    def _unexpected_resolution_error(
        *args: object, **kwargs: object
    ) -> dict[str, object]:
        del args, kwargs
        raise RuntimeError("unexpected resolution bug")

    monkeypatch.setattr(
        "weft.core.manager.resolve_taskspec_payload",
        _unexpected_resolution_error,
    )

    with pytest.raises(RuntimeError, match="unexpected resolution bug"):
        manager._build_child_spec(make_child_spec(), int(time.time_ns()))


def test_manager_idle_timeout_ignores_unrelated_broker_activity(
    broker_env,
    unique_tid,
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
    broker_env,
    unique_tid,
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


def test_manager_overrides_supplied_tid(manager_setup, unique_tid) -> None:
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
    broker_env, unique_tid, tmp_path: Path
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
    broker_env,
    unique_tid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    manager = Manager(db_path, make_manager_spec(unique_tid, idle_timeout=0.0))
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

    class FailingQueue:
        def write(self, _payload: str) -> None:
            raise RuntimeError("locked")

    original_queue = manager._queue

    def fake_queue(name: str):
        if name == child.io.inputs["inbox"]:
            return FailingQueue()
        return original_queue(name)

    monkeypatch.setattr(manager, "_queue", fake_queue)

    try:
        launched = manager._launch_child_task(child, {"args": []})

        assert launched is False
        assert manager._child_processes == {}
    finally:
        manager.cleanup()


def test_manager_idle_timeout_does_not_kill_persistent_child(
    broker_env, unique_tid
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
        while not manager._child_processes and time.time() - start < 2.0:
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
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(unique_tid, inbox, ctrl_in, ctrl_out)

    manager = Manager(db_path, spec, config=config)
    try:
        manager.process_once()
        assert not manager._user_work_children()
        assert not manager._autostart_launched
    finally:
        manager.cleanup()


def test_manager_autostart_active_sources_include_tracked_children(
    tmp_path: Path, broker_env, unique_tid
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, _manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="tracked-autostart",
        manifest_name="tracked-autostart",
        mode="ensure",
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
            process=FakeProcess(),
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
    broker_env,
    unique_tid,
) -> None:
    db_path, _make_queue = broker_env
    autostart_dir = tmp_path / "autostart"
    autostart_dir.mkdir()

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    stale_source = str((autostart_dir / "deleted.json").resolve())
    try:
        manager._autostart_state[stale_source] = {
            "restarts": 2,
            "next_allowed_ns": time.time_ns(),
            "launched_once": True,
        }
        manager._autostart_launched.add(stale_source)

        manager._tick_autostart(force=True)

        assert stale_source not in manager._autostart_state
        assert stale_source not in manager._autostart_launched
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_restarts(
    tmp_path: Path, broker_env, unique_tid
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
        wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("tid") == first_child_tid
                and event.get("event") == "work_completed"
            ),
            timeout=30.0 if os.name == "nt" else 20.0,
        )
        wait_for_children(manager, timeout=10.0)
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
    tmp_path: Path, broker_env, unique_tid
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    try:
        ctx = multiprocessing.get_context("spawn")
        child = ctx.Process(target=time.sleep, args=(0.0,))
        child.start()
        child.join(timeout=2.0)
        assert child.is_alive() is False

        manager._child_processes["child"] = ManagedChild(
            process=child,
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
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=0.2)
    manager = Manager(db_path, spec, config=config)
    source = str(manifest_path.resolve())
    try:
        manager._autostart_state[source] = {
            "restarts": 0,
            "next_allowed_ns": time.time_ns() + 1_000_000_000,
            "launched_once": True,
        }
        manager._service_state(source).launched_once = True
        manager._service_state(source).next_allowed_ns = int(
            manager._autostart_state[source]["next_allowed_ns"]
        )
        manager._last_activity_ns = time.time_ns() - 1_000_000_000
        manager._autostart_last_scan_ns = time.time_ns()
        manager._last_managed_service_convergence_ns = time.time_ns()

        manager.process_once()

        assert manager.should_stop is False
    finally:
        manager.cleanup()


def test_manager_autostart_stale_active_log_without_liveness_is_not_active(
    tmp_path: Path,
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
            process=child,
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
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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


def test_manager_autostart_pipeline_ensure_restarts(
    tmp_path: Path,
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
        assert manager._autostart_state[source]["restarts"] == 1
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_enqueue_failure_does_not_advance_state(
    tmp_path: Path,
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = False
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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

        assert source not in manager._autostart_launched
        assert manager._autostart_state[source]["restarts"] == 0
        assert manager._autostart_state[source]["next_allowed_ns"] == 0
    finally:
        manager.cleanup()


def test_manager_autostart_pending_spawn_blocks_duplicate_restart(
    tmp_path: Path,
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = False
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
            lambda service: enqueued.append(service) or True,
        )

        manager._tick_autostart(force=True)

        assert enqueued == []
        assert manager._service_state(source).spawn_pending is True
    finally:
        manager.cleanup()


def test_manager_autostart_spoofed_public_metadata_does_not_claim_manifest(
    tmp_path: Path,
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = False
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
            lambda service: enqueued.append(service) or True,
        )
        manager._autostart_enabled = True

        manager._tick_autostart(force=True)

        assert [service.key for service in enqueued] == [source]
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_allows_one_restart_after_initial_launch(
    tmp_path: Path,
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
        wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("tid") == first_child_tid
                and event.get("event") == "work_completed"
            ),
            timeout=30.0 if os.name == "nt" else 20.0,
        )
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
        assert manager._autostart_state[source]["restarts"] == 1

        spawn_events = [first_spawn, second_spawn]
        extra_deadline = time.time() + 1.0
        while time.time() < extra_deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
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
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
        wait_for_log_event(
            manager,
            log_queue,
            lambda event: (
                event.get("tid") == first_child_tid
                and event.get("event") == "work_completed"
            ),
            timeout=30.0 if os.name == "nt" else 20.0,
        )
        wait_for_children(manager, timeout=20.0 if os.name == "nt" else 10.0)
        assert not manager._user_work_children()

        restart_events = []
        deadline = time.time() + 8.0
        while not restart_events and time.time() < deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    restart_events.append(event)

        assert len(restart_events) == 1
    finally:
        manager.cleanup()


def test_manager_autostart_backoff_rescan_uses_due_time(
    tmp_path: Path,
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = False
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
        assert manager._autostart_state[source]["next_allowed_ns"] == 1_500_000_000
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
        assert manager._autostart_state[source]["restarts"] == 1
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_restarts_after_abrupt_child_kill(
    tmp_path: Path,
    broker_env,
    unique_tid,
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

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

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
                event = json.loads(item)
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
