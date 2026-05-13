"""Tests for manager CLI command helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from simplebroker import Queue
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
    MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    SERVICE_STATUS_SUPERSEDED,
    WEFT_SERVICES_REGISTRY_QUEUE,
)
from weft.commands import manager as manager_cmd
from weft.context import build_context
from weft.core import manager_runtime as core_manager_runtime
from weft.core.service_convergence import build_manager_service_payload
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared]


def _host_runtime_handle(pid: int) -> dict[str, object]:
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {"host_pids": [pid]},
        "metadata": {},
    }


def _external_supervisor_runtime_handle() -> dict[str, object]:
    return {
        "runner": "manager-supervisor",
        "kind": "supervised-process",
        "id": "container:weft-manager-1",
        "control": {"authority": "external-supervisor"},
        "observations": {"container_pid": 1, "container_name": "weft-manager-1"},
        "metadata": {},
    }


def _manager_service_payload(
    context,
    tid: str,
    *,
    status: str = "active",
    name: str = "manager",
    runtime_handle: dict[str, object] | None = None,
    ctrl_in: str | None = None,
    ctrl_out: str | None = None,
    outbox: str = "weft.manager.outbox",
) -> dict[str, object]:
    return build_manager_service_payload(
        context=context,
        tid=tid,
        name=name,
        status=status,
        queues={
            "requests": "weft.spawn.requests",
            "ctrl_in": ctrl_in or f"T{tid}.ctrl_in",
            "ctrl_out": ctrl_out or f"T{tid}.ctrl_out",
            "outbox": outbox,
        },
        runtime_handle=runtime_handle or {},
    )


def _latest_manager_record(context, tid: str) -> dict[str, object] | None:
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        latest: tuple[dict[str, object], int] | None = None
        for payload, timestamp in iter_queue_json_entries(queue):
            if payload.get("tid") != tid:
                continue
            if latest is None or latest[1] < timestamp:
                latest = (payload, timestamp)
        return None if latest is None else latest[0]
    finally:
        queue.close()


def test_start_command_delegates_to_shared_bootstrap(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)

    def _fake_ensure(context_arg, *, verbose):
        assert context_arg is context
        assert verbose is False
        calls.append("ensure")
        return (
            {
                "tid": "1761000000000000000",
                "runtime_handle": _host_runtime_handle(12345),
            },
            True,
            None,
        )

    monkeypatch.setattr(manager_cmd, "_ensure_manager", _fake_ensure)

    exit_code, message = manager_cmd.start_command(context_path=context_root)

    assert exit_code == 0
    assert message == "Started manager 1761000000000000000"
    assert calls == ["ensure"]


def test_start_command_reports_existing_manager(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        manager_cmd,
        "_ensure_manager",
        lambda context_arg, *, verbose: (
            {
                "tid": "1761000000000000001",
                "runtime_handle": _host_runtime_handle(54321),
            },
            False,
            None,
        ),
    )

    exit_code, message = manager_cmd.start_command(context_path=context_root)

    assert exit_code == 0
    assert message == "Manager 1761000000000000001 already running"


def test_start_command_replace_supersedes_before_start(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)

    def fake_replace(context_arg):
        assert context_arg is context
        calls.append("replace")
        return True, None

    def fake_start(context_arg, *, verbose):
        assert context_arg is context
        assert verbose is False
        calls.append("start")
        return (
            {
                "tid": "1761000000000000002",
                "runtime_handle": _host_runtime_handle(12345),
            },
            True,
            None,
        )

    monkeypatch.setattr(manager_cmd, "_replace_active_manager", fake_replace)
    monkeypatch.setattr(manager_cmd, "_start_manager", fake_start)

    exit_code, message = manager_cmd.start_command(
        context_path=context_root,
        replace=True,
    )

    assert exit_code == 0
    assert message == "Started manager 1761000000000000002"
    assert calls == ["replace", "start"]


def test_start_command_replace_failure_does_not_start(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        manager_cmd,
        "_replace_active_manager",
        lambda context_arg: (False, "failed to send STOP"),
    )
    monkeypatch.setattr(
        manager_cmd,
        "_start_manager",
        lambda *args, **kwargs: calls.append("start"),
    )

    exit_code, message = manager_cmd.start_command(
        context_path=context_root,
        replace=True,
    )

    assert exit_code == 1
    assert message == "failed to send STOP"
    assert calls == []


def test_replace_active_manager_sends_stop_and_marks_superseded(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000010"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                context,
                tid,
                runtime_handle=_host_runtime_handle(os.getpid()),
                ctrl_in=f"manager.{tid}.ctrl_in",
            )
        )
    )
    registry_queue.close()
    monkeypatch.setattr(
        core_manager_runtime,
        "_await_manager_stop_confirmation",
        lambda *args, **kwargs: pytest.fail("replacement should not wait"),
    )

    replaced, message = core_manager_runtime.replace_active_manager(
        context,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    )

    assert replaced is True, message
    assert message is None
    ctrl_queue = Queue(
        f"manager.{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    try:
        assert ctrl_queue.read_one() == "STOP"
    finally:
        ctrl_queue.close()
    latest = _latest_manager_record(context, tid)
    assert latest is not None
    assert latest["status"] == SERVICE_STATUS_SUPERSEDED
    assert core_manager_runtime.select_active_manager(context) is None


def test_replace_active_manager_reselects_after_superseding_lower_tid(
    tmp_path,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    lower_tid = "1761000000000000011"
    higher_tid = "1761000000000000012"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    try:
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    lower_tid,
                    runtime_handle=_host_runtime_handle(os.getpid()),
                )
            )
        )
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    higher_tid,
                    runtime_handle=_host_runtime_handle(os.getpid()),
                )
            )
        )
    finally:
        registry_queue.close()

    replaced, message = core_manager_runtime.replace_active_manager(
        context,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    )

    assert replaced is True, message
    assert message is None
    lower_latest = _latest_manager_record(context, lower_tid)
    higher_latest = _latest_manager_record(context, higher_tid)
    assert lower_latest is not None
    assert higher_latest is not None
    assert lower_latest["status"] == SERVICE_STATUS_SUPERSEDED
    assert higher_latest["status"] == SERVICE_STATUS_SUPERSEDED


def test_stop_command_delegates_to_shared_lifecycle_helper(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    calls: list[tuple[object, object, object, object, object]] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)

    def fake_stop_manager(
        context_arg,
        record,
        process=None,
        *,
        tid=None,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
        force=False,
        stop_if_absent=False,
    ):
        calls.append((context_arg, record, tid, timeout, force))
        assert stop_if_absent is False
        return True, None

    monkeypatch.setattr(manager_cmd, "_stop_manager", fake_stop_manager)

    exit_code, message = manager_cmd.stop_command(
        tid="1761000000000000001",
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    assert calls == [(context, None, "1761000000000000001", 0.1, False)]


def test_stop_command_without_tid_stops_active_manager(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    active_record = {
        "tid": "1761000000000000006",
        "runtime_handle": _host_runtime_handle(os.getpid()),
    }
    select_calls: list[tuple[object, bool, object]] = []
    stop_calls: list[tuple[object, object, object, object, object]] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)

    def fake_select_active_manager(
        context_arg,
        *,
        probe_stale=False,
        probe_cache=None,
    ):
        select_calls.append((context_arg, probe_stale, probe_cache))
        return active_record

    def fake_stop_manager(
        context_arg,
        record,
        process=None,
        *,
        tid=None,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
        force=False,
        stop_if_absent=False,
    ):
        del process, stop_if_absent
        stop_calls.append((context_arg, record, tid, timeout, force))
        return True, None

    monkeypatch.setattr(
        manager_cmd, "_select_active_manager", fake_select_active_manager
    )
    monkeypatch.setattr(manager_cmd, "_stop_manager", fake_stop_manager)

    exit_code, message = manager_cmd.stop_command(
        tid=None,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    assert select_calls == [(context, True, {})]
    assert stop_calls == [(context, active_record, "1761000000000000006", 0.1, False)]


def test_stop_command_without_tid_noops_when_no_active_manager(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    stop_calls: list[object] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        manager_cmd,
        "_select_active_manager",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        manager_cmd,
        "_stop_manager",
        lambda *args, **kwargs: stop_calls.append(args),
    )

    exit_code, message = manager_cmd.stop_command(
        tid=None,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    assert stop_calls == []


def test_stop_command_default_timeout_exceeds_manager_drain_budget(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    calls: list[float] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)

    def fake_stop_manager(
        context_arg,
        record,
        process=None,
        *,
        tid=None,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
        force=False,
        stop_if_absent=False,
    ):
        del context_arg, record, process, tid, force, stop_if_absent
        calls.append(timeout)
        return True, None

    monkeypatch.setattr(manager_cmd, "_stop_manager", fake_stop_manager)

    exit_code, message = manager_cmd.stop_command(
        tid="1761000000000000001",
        force=False,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    assert calls == [MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS]
    assert (
        MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS
        >= MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS + 40.0
    )


def test_stop_manager_default_timeout_exceeds_manager_drain_budget(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    calls: list[float] = []

    def fake_stop_manager(
        context_arg,
        record,
        process=None,
        *,
        tid=None,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
        force=False,
        stop_if_absent=False,
    ):
        del context_arg, record, process, tid, force, stop_if_absent
        calls.append(timeout)
        return True, None

    monkeypatch.setattr(manager_cmd, "_stop_manager", fake_stop_manager)

    manager_cmd.stop_manager(context, "1761000000000000001")

    assert calls == [MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS]
    assert (
        MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS
        >= MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS + 40.0
    )


def test_stop_command_rewrites_timeout_message(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        manager_cmd,
        "_stop_manager",
        lambda *args, **kwargs: (
            False,
            "Manager 1761000000000000001 did not stop within 0.1s",
        ),
    )

    exit_code, message = manager_cmd.stop_command(
        tid="1761000000000000001",
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 1
    assert message == "Manager 1761000000000000001 did not stop within 0.1s"


def test_stop_command_writes_stop_for_active_manager(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000001"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                context,
                tid,
                runtime_handle=_host_runtime_handle(os.getpid()),
            )
        )
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 1
    assert message is not None
    assert "did not stop" in message
    ctrl_queue = Queue(
        f"T{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    assert ctrl_queue.read_one() == "STOP"


def test_stop_command_noops_for_stopped_manager(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000002"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(_manager_service_payload(context, tid, status="stopped"))
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    ctrl_queue = Queue(
        f"T{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    assert ctrl_queue.read_one() is None


def test_stop_command_uses_registry_control_queue(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000003"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                context,
                tid,
                runtime_handle=_host_runtime_handle(os.getpid()),
                ctrl_in=f"manager.{tid}.ctrl_in",
            )
        )
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 1
    assert message is not None
    assert "did not stop" in message
    ctrl_queue = Queue(
        f"manager.{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    assert ctrl_queue.read_one() == "STOP"


def test_stop_command_stop_if_absent_still_sends_stop(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000004"

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=0.1,
        context_path=context_root,
        stop_if_absent=True,
    )

    assert exit_code == 0
    assert message is None
    ctrl_queue = Queue(
        f"T{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    assert ctrl_queue.read_one() == "STOP"


def test_stop_command_waits_for_pid_exit_after_stopped_status(
    tmp_path, monkeypatch
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        manager_cmd, "_stop_manager", lambda *args, **kwargs: (True, None)
    )

    exit_code, message = manager_cmd.stop_command(
        tid="1761000000000000005",
        force=False,
        timeout=1.0,
        context_path=context_root,
        stop_if_absent=True,
    )

    assert exit_code == 0
    assert message is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_list_command_omits_stale_active_manager(tmp_path) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000006"

    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        process.wait(timeout=2.0)
        registry_queue = Queue(
            WEFT_SERVICES_REGISTRY_QUEUE,
            db_path=context.broker_target,
            persistent=False,
            config=context.config,
        )
        registry_queue.write(
            json.dumps(
                {
                    "tid": tid,
                    "status": "active",
                    "name": "stale",
                    "pid": process.pid,
                    "role": "manager",
                    "requests": "custom.manager.requests",
                }
            )
        )
        exit_code, payload = manager_cmd.list_command(
            json_output=True, context_path=context_root
        )
    finally:
        process.wait()

    assert exit_code == 0
    assert tid not in {record["tid"] for record in json.loads(payload)}


def test_list_command_omits_stale_external_supervisor_manager(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000008"

    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        -1.0,
    )
    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "active",
                "name": "stale-supervised-manager",
                "runtime_handle": _external_supervisor_runtime_handle(),
                "role": "manager",
                "requests": "weft.spawn.requests",
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
                "outbox": "weft.manager.outbox",
            }
        )
    )

    exit_code, payload = manager_cmd.list_command(
        json_output=True, context_path=context_root
    )

    assert exit_code == 0
    assert tid not in {record["tid"] for record in json.loads(payload)}


def test_stop_command_force_reports_fresh_external_supervisor_without_host_pid(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000009"

    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                context,
                tid,
                name="fresh-supervised-manager",
                runtime_handle=_external_supervisor_runtime_handle(),
            )
        )
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=True,
        timeout=0.0,
        context_path=context_root,
    )

    assert exit_code == 1
    assert message is not None
    assert "externally supervised" in message
    assert "no host PID" in message


def test_stop_command_force_ignores_registry_only_pid_without_mapping(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000007"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "active",
                "name": "legacy-manager",
                "pid": os.getpid(),
                "role": "manager",
                "requests": "legacy.requests",
            }
        )
    )

    monkeypatch.setattr(
        "weft.core.manager_runtime.terminate_process_tree",
        lambda *args, **kwargs: pytest.fail(
            "force stop must not trust an uncorroborated registry pid"
        ),
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=True,
        timeout=0.0,
        context_path=context_root,
        stop_if_absent=True,
    )

    assert exit_code == 0
    assert message is None


def test_stop_command_force_replaces_active_registry_record(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000010"
    kill_pid = 8765

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    try:
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    tid,
                    runtime_handle=_host_runtime_handle(kill_pid),
                )
            )
        )
    finally:
        registry_queue.close()

    monkeypatch.setattr(
        "weft.core.manager_runtime._lookup_manager_pid",
        lambda *args, **kwargs: kill_pid,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._is_pid_alive",
        lambda pid: pid == kill_pid,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.terminate_process_tree",
        lambda *args, **kwargs: {kill_pid},
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=True,
        timeout=0.0,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None

    reader = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    try:
        records = [payload for payload, _timestamp in iter_queue_json_entries(reader)]
    finally:
        reader.close()

    assert len(records) == 1
    assert records[0]["tid"] == tid
    assert records[0]["status"] == "stopped"


def test_list_command_returns_table(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                context,
                "1",
                name="alpha",
                runtime_handle=_host_runtime_handle(os.getpid()),
            )
        )
    )

    exit_code, payload = manager_cmd.list_command(
        json_output=False, context_path=context_root
    )
    assert exit_code == 0
    assert "alpha" in payload


def test_status_command_not_found(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    build_context(context_root)
    exit_code, payload = manager_cmd.status_command(
        tid="999", json_output=False, context_path=context_root
    )
    assert exit_code == 1
    assert "not found" in payload.lower()
