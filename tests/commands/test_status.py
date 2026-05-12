"""Tests for the ``weft status`` command helpers."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.commands import system as status_cmd
from weft.commands import tasks as task_cmd
from weft.commands.status import cmd_status, collect_status
from weft.context import build_context
from weft.core.runners import host as host_runner
from weft.core.service_convergence import build_manager_service_payload
from weft.ext import RunnerRuntimeDescription

pytestmark = [pytest.mark.shared]


def _runtime_handle(
    runner: str,
    runtime_id: str,
    *,
    kind: str = "process",
    authority: str = "host-pid",
    host_pids: list[int] | None = None,
    observations: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observed = dict(observations or {})
    if host_pids is not None:
        observed["host_pids"] = host_pids
    return {
        "runner": runner,
        "kind": kind,
        "id": runtime_id,
        "control": {"authority": authority},
        "observations": observed,
        "metadata": metadata or {},
    }


def _manager_service_payload(
    ctx,
    *,
    tid: str,
    name: str = "manager",
    status: str = "active",
    runtime_handle: dict[str, Any] | None = None,
    internal_requests: str | None = None,
    internal_reserved: str | None = None,
) -> dict[str, Any]:
    return build_manager_service_payload(
        context=ctx,
        tid=tid,
        name=name,
        status=status,
        queues={
            "requests": WEFT_SPAWN_REQUESTS_QUEUE,
            "internal_requests": internal_requests,
            "internal_reserved": internal_reserved,
            "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
            "ctrl_in": f"T{tid}.ctrl_in",
            "ctrl_out": f"T{tid}.ctrl_out",
        },
        runtime_handle=runtime_handle or {},
    )


class _FakeQueueChangeMonitor:
    def __init__(self, queues, *, config=None) -> None:
        del config
        self.queue_names = [queue.name for queue in queues]
        self.wait_calls: list[float | None] = []

    def wait(self, timeout: float | None) -> bool:
        self.wait_calls.append(timeout)
        if len(self.wait_calls) >= 2:
            raise KeyboardInterrupt
        return False

    def close(self) -> None:
        return


def _write_task_log_entry(
    *,
    ctx: Any,
    tid: str,
    event: str,
    status: str,
    started_at: int,
    completed_at: int | None,
    name: str = "task",
    runner_name: str = "host",
    state_status: str | None = None,
    metadata: dict[str, Any] | None = None,
    runner_diagnostics: dict[str, Any] | None = None,
) -> None:
    state_status = state_status or status
    payload: dict[str, Any] = {
        "event": event,
        "status": status,
        "tid": tid,
        "taskspec": {
            "name": name,
            "spec": {"runner": {"name": runner_name, "options": {}}},
            "state": {
                "status": state_status,
                "started_at": started_at,
                "completed_at": completed_at,
            },
            "metadata": metadata or {},
        },
    }
    if runner_diagnostics is not None:
        payload["runner_diagnostics"] = runner_diagnostics
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    log_queue.write(json.dumps(payload))


def _task_monitor_taskspec_payload(
    tid: str,
    *,
    status: str = "running",
    started_at: int | None = None,
    completed_at: int | None = None,
) -> dict[str, Any]:
    return {
        "tid": tid,
        "name": "task-monitor",
        "spec": {
            "type": "function",
            "function_target": "weft.core.tasks.task_monitor:runtime",
            "persistent": True,
            "runner": {"name": "host", "options": {}},
        },
        "io": {
            "inputs": {"inbox": f"T{tid}.inbox"},
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
        },
        "metadata": {
            "internal": True,
            "role": "task_monitor",
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
            INTERNAL_SERVICE_KEY_METADATA_KEY: INTERNAL_SERVICE_KEY_TASK_MONITOR,
            INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY: "ensure",
        },
    }


def _write_manager_spawned_task_monitor(
    *,
    ctx: Any,
    manager_tid: str,
    child_tid: str,
    child_pid: int,
) -> None:
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": "task_spawned",
                "tid": manager_tid,
                "tid_short": manager_tid[-10:],
                "status": "running",
                "timestamp": time.time_ns(),
                "taskspec": {
                    "tid": manager_tid,
                    "name": "manager",
                    "spec": {"runner": {"name": "host", "options": {}}},
                    "state": {"status": "running", "started_at": time.time_ns()},
                    "metadata": {"role": "manager"},
                },
                "child_tid": child_tid,
                "child_pid": child_pid,
                "child_taskspec": _task_monitor_taskspec_payload(child_tid),
                "service_key": INTERNAL_SERVICE_KEY_TASK_MONITOR,
            }
        )
    )


def _write_pipeline_log_entry(
    *,
    ctx: Any,
    tid: str,
    status: str = "running",
    event: str = "task_started",
    started_at: int | None = None,
    completed_at: int | None = None,
) -> None:
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": event,
                "status": status,
                "tid": tid,
                "taskspec": {
                    "tid": tid,
                    "name": "demo-pipeline",
                    "spec": {
                        "type": "function",
                        "function_target": "weft.core.tasks.pipeline:runtime",
                        "runner": {"name": "host", "options": {}},
                    },
                    "io": {
                        "outputs": {"outbox": f"P{tid}.outbox"},
                        "control": {
                            "ctrl_in": f"P{tid}.ctrl_in",
                            "ctrl_out": f"P{tid}.ctrl_out",
                        },
                    },
                    "state": {
                        "status": status,
                        "started_at": started_at,
                        "completed_at": completed_at,
                    },
                    "metadata": {
                        "role": "pipeline",
                        "_weft_pipeline_runtime": {
                            "queues": {"status": f"P{tid}.status"},
                        },
                    },
                },
            }
        )
    )


def test_collect_status_reports_message_counts(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("hello")
    queue.write("there")

    snapshot = collect_status(ctx)

    assert snapshot.total_messages == 2
    assert snapshot.db_size >= 0


def test_system_status_manager_snapshot_includes_internal_spawn_queues(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    tid = "1779000000000000001"
    internal_reserved = f"T{tid}.internal_reserved"
    try:
        registry.write(
            json.dumps(
                _manager_service_payload(
                    ctx,
                    tid=tid,
                    internal_requests=WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
                    internal_reserved=internal_reserved,
                    runtime_handle=_runtime_handle(
                        "host",
                        str(os.getpid()),
                        host_pids=[os.getpid()],
                    ),
                )
            )
        )
    finally:
        registry.close()

    snapshot = status_cmd.system_status(ctx)

    assert len(snapshot.managers) == 1
    manager = snapshot.managers[0]
    assert manager.internal_requests == WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
    assert manager.internal_reserved == internal_reserved


def test_status_services_include_manager_spawned_task_monitor_before_child_log(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    manager_tid = "1779000000000000101"
    child_tid = "1779000000000000102"
    _write_manager_spawned_task_monitor(
        ctx=ctx,
        manager_tid=manager_tid,
        child_tid=child_tid,
        child_pid=os.getpid(),
    )

    exit_code, payload = cmd_status(json_output=True, spec_context=root)

    assert exit_code == 0
    assert payload is not None
    services = json.loads(payload)["services"]
    monitor = next(
        service
        for service in services
        if service["key"] == INTERNAL_SERVICE_KEY_TASK_MONITOR
    )
    assert monitor["status"] == "launched"
    assert monitor["tid"] == child_tid
    assert monitor["manager_tid"] == manager_tid
    assert monitor["evidence"] == "manager-task-spawned"
    assert monitor["pid"] == os.getpid()


def test_status_services_child_terminal_evidence_overrides_manager_spawn(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    manager_tid = "1779000000000000201"
    child_tid = "1779000000000000202"
    now = time.time_ns()
    _write_manager_spawned_task_monitor(
        ctx=ctx,
        manager_tid=manager_tid,
        child_tid=child_tid,
        child_pid=os.getpid(),
    )
    _write_task_log_entry(
        ctx=ctx,
        tid=child_tid,
        event="work_completed",
        status="completed",
        started_at=now - 10_000,
        completed_at=now,
        name="task-monitor",
        metadata={
            "internal": True,
            "role": "task_monitor",
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
            INTERNAL_SERVICE_KEY_METADATA_KEY: INTERNAL_SERVICE_KEY_TASK_MONITOR,
            INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY: "ensure",
        },
    )

    exit_code, payload = cmd_status(json_output=True, spec_context=root)

    assert exit_code == 0
    assert payload is not None
    services = json.loads(payload)["services"]
    monitor = next(
        service
        for service in services
        if service["key"] == INTERNAL_SERVICE_KEY_TASK_MONITOR
    )
    assert monitor["status"] == "terminal"
    assert monitor["tid"] == child_tid
    assert monitor["evidence"] == "child-task-log"
    assert monitor["reconciliation"]["lifecycle_status"] == "completed"


def test_status_services_report_pending_internal_spawn_request(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    pending_queue = ctx.queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE, persistent=False)
    pending_queue.write(
        json.dumps(
            {
                "taskspec": {
                    "name": "task-monitor",
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
                INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY: (
                    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
                ),
            }
        )
    )

    snapshot = status_cmd.system_status(ctx)

    monitor = next(
        service
        for service in snapshot.services
        if service.key == INTERNAL_SERVICE_KEY_TASK_MONITOR
    )
    assert monitor.status == "pending"
    assert monitor.evidence == "internal-spawn-pending"
    assert monitor.queue == WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE


def test_cmd_status_text_output(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("payload")

    exit_code, payload = cmd_status(spec_context=root)

    assert exit_code == 0
    assert payload is not None
    lines = payload.splitlines()
    assert lines[0].startswith("total_messages: ")

    ts_line = next(line for line in lines if line.startswith("last_timestamp: "))
    assert ts_line.endswith(")")
    assert "(" in ts_line

    size_line = next(line for line in lines if line.startswith("db_size: "))
    assert "bytes" in size_line
    assert "(" in size_line
    assert "Services:" in lines


def test_cmd_status_json_output(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("payload")

    exit_code, payload = cmd_status(json_output=True, spec_context=root)

    assert exit_code == 0
    assert payload is not None
    data = json.loads(payload)
    assert data["broker"]["total_messages"] >= 1
    assert "db_size" in data["broker"]
    assert isinstance(data["managers"], list)
    assert isinstance(data["services"], list)


def test_cmd_status_json_includes_runner_runtime_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955161"
    started = 1_762_000_000_000_000_000
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": "work_started",
                "status": "running",
                "tid": tid,
                "taskspec": {
                    "name": "docker-task",
                    "spec": {
                        "runner": {
                            "name": "docker",
                            "options": {"image": "python:3.13-alpine"},
                        }
                    },
                    "state": {
                        "status": "running",
                        "started_at": started,
                        "completed_at": None,
                    },
                    "metadata": {"owner": "tests"},
                },
            }
        )
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "docker",
                "runtime_handle": _runtime_handle(
                    "docker",
                    "container-123",
                    kind="container",
                    authority="runner",
                    observations={"container_id": "container-123"},
                    metadata={"image": "python:3.13-alpine"},
                ),
            }
        )
    )

    class FakeRunnerPlugin:
        def describe(self, handle: Any) -> RunnerRuntimeDescription | None:
            return RunnerRuntimeDescription(
                runner=handle.runner,
                id=handle.id,
                state="running",
                metadata={"image": "python:3.13-alpine", "cpu_percent": 0.5},
            )

    monkeypatch.setattr(
        status_cmd,
        "require_runner_plugin",
        lambda name: FakeRunnerPlugin(),
    )

    exit_code, payload = cmd_status(
        json_output=True, include_terminal=True, spec_context=root
    )

    assert exit_code == 0
    assert payload is not None
    data = json.loads(payload)
    assert len(data["tasks"]) == 1
    entry = data["tasks"][0]
    assert entry["tid"] == tid
    assert entry["runner"] == "docker"
    assert entry["runtime_handle"]["runner"] == "docker"
    assert entry["runtime_handle"]["id"] == "container-123"
    assert entry["runtime"]["runner"] == "docker"
    assert entry["runtime"]["id"] == "container-123"
    assert entry["runtime"]["state"] == "running"
    assert entry["runtime"]["metadata"]["image"] == "python:3.13-alpine"
    assert entry["metadata"]["owner"] == "tests"


def test_task_status_does_not_apply_host_pid_identity_to_docker_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955168"
    started = 1_762_000_000_000_000_000
    docker_host_pid = 57
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="work_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="docker-task",
        runner_name="docker",
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "docker",
                "runtime_handle": _runtime_handle(
                    "docker",
                    "container-123",
                    kind="container",
                    authority="runner",
                    observations={"container_id": "container-123"},
                    metadata={"image": "python:3.13-alpine"},
                ),
            }
        )
    )

    class FakeRunnerPlugin:
        def describe(self, handle: Any) -> RunnerRuntimeDescription | None:
            return RunnerRuntimeDescription(
                runner=handle.runner,
                id=handle.id,
                state="running",
                metadata={
                    "container_id": "container-123",
                    "host_pid": docker_host_pid,
                    "image": "python:3.13-alpine",
                },
            )

    def fail_host_liveness_check(mapping_entry: Any) -> bool:
        raise AssertionError("docker runtime was checked as a host-pid task")

    monkeypatch.setattr(status_cmd, "_task_process_alive", fail_host_liveness_check)
    monkeypatch.setattr(
        status_cmd,
        "require_runner_plugin",
        lambda name: FakeRunnerPlugin(),
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.status == "running"
    assert snapshot.runner == "docker"
    assert snapshot.runtime is not None
    assert snapshot.runtime["runner"] == "docker"
    assert snapshot.runtime["state"] == "running"
    assert snapshot.runtime["metadata"]["host_pid"] == docker_host_pid


def test_terminal_log_status_wins_over_weak_live_host_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955191"
    started = 1_762_000_000_000_000_000
    completed = started + 1_000_000_000
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="work_failed",
        status="failed",
        started_at=started,
        completed_at=completed,
        name="weak-live-pid-terminal",
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "host",
                "runtime_handle": _runtime_handle(
                    "host",
                    "100",
                    host_pids=[100],
                ),
            }
        )
    )
    monkeypatch.setattr(
        status_cmd,
        "handle_has_live_host_process",
        lambda handle: True,
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.completed_at == completed
    assert snapshot.reconciliation is not None
    assert snapshot.reconciliation["classification"] == "runtime_conflict"
    assert (
        snapshot.reconciliation["reason"]
        == "weak_host_pid_ignored_for_terminal_lifecycle"
    )

    exit_code, payload = cmd_status(
        json_output=True,
        include_terminal=True,
        spec_context=root,
    )

    assert exit_code == 0
    assert payload is not None
    tasks = json.loads(payload)["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["status"] == "failed"
    assert tasks[0]["completed_at"] == completed
    assert tasks[0]["reconciliation"]["classification"] == "runtime_conflict"

    exit_code, payload = cmd_status(json_output=True, spec_context=root)

    assert exit_code == 0
    assert payload is not None
    assert json.loads(payload)["tasks"] == []


@pytest.mark.parametrize(
    ("event", "expected_status"),
    [
        ("control_stop", "cancelled"),
        ("task_signal_stop", "cancelled"),
        ("control_kill", "killed"),
        ("task_signal_kill", "killed"),
        ("work_failed", "failed"),
        ("work_timeout", "timeout"),
        ("work_completed", "completed"),
    ],
)
def test_terminal_event_reconciles_stale_running_status_payload(
    tmp_path: Path,
    event: str,
    expected_status: str,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(1_844_674_407_370_955_200 + len(event))
    started = 1_762_000_000_000_000_000
    completed = started + 1_000_000_000

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event=event,
        status="running",
        state_status="running",
        started_at=started,
        completed_at=completed,
        name=f"stale-{event}",
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == event
    assert snapshot.status == expected_status
    assert snapshot.completed_at == completed
    assert snapshot.activity is None
    assert snapshot.waiting_on is None
    assert snapshot.reconciliation is not None
    assert snapshot.reconciliation["classification"] == "stale_status_payload"
    assert snapshot.reconciliation["reason"] == "contradictory_terminal_event_status"


def test_status_preserves_active_manager_while_terminal_manager_row_stays_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    old_tid = "1844674407370955192"
    active_tid = "1844674407370955193"
    started = 1_762_000_000_000_000_000
    completed = started + 1_000_000_000

    ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False).write(
        json.dumps(
            _manager_service_payload(
                ctx,
                tid=active_tid,
                runtime_handle=_runtime_handle(
                    "manager-supervisor",
                    "supervisor-active",
                    kind="supervised-process",
                    authority="external-supervisor",
                ),
            )
        )
    )
    _write_task_log_entry(
        ctx=ctx,
        tid=active_tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="active-manager",
        metadata={"role": "manager"},
    )
    _write_task_log_entry(
        ctx=ctx,
        tid=old_tid,
        event="task_signal_stop",
        status="running",
        state_status="running",
        started_at=started,
        completed_at=completed,
        name="old-manager",
        metadata={"role": "manager"},
    )
    ctx.queue("weft.state.tid_mappings", persistent=False).write(
        json.dumps(
            {
                "short": old_tid[-10:],
                "full": old_tid,
                "runner": "host",
                "runtime_handle": _runtime_handle(
                    "host",
                    "100",
                    host_pids=[100],
                ),
            }
        )
    )
    monkeypatch.setattr(
        status_cmd,
        "handle_has_live_host_process",
        lambda handle: True,
    )

    exit_code, payload = cmd_status(
        json_output=True,
        include_terminal=True,
        spec_context=root,
    )

    assert exit_code == 0
    assert payload is not None
    data = json.loads(payload)
    assert [(manager["tid"], manager["status"]) for manager in data["managers"]] == [
        (active_tid, "active")
    ]
    tasks = {task["tid"]: task for task in data["tasks"]}
    assert tasks[active_tid]["status"] == "running"
    assert tasks[old_tid]["status"] == "cancelled"
    assert all(
        not (task["status"] == "running" and task["completed_at"] is not None)
        for task in tasks.values()
    )


def test_task_status_keeps_terminal_log_state_when_task_pid_is_alive(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955161"
    started = 1_762_000_000_000_000_000
    completed = started + 1_000_000_000
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="control_stop",
        status="cancelled",
        started_at=started,
        completed_at=completed,
        name="live-consumer-task",
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "host",
                "runtime_handle": _runtime_handle(
                    "host",
                    str(os.getpid()),
                    host_pids=[os.getpid()],
                ),
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == "control_stop"
    assert snapshot.status == "cancelled"
    assert snapshot.reconciliation is not None
    assert snapshot.reconciliation["classification"] == "runtime_conflict"
    assert (
        snapshot.reconciliation["reason"]
        == "weak_host_pid_ignored_for_terminal_lifecycle"
    )


def test_task_snapshot_full_tid_uses_bounded_known_tid_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    started = time.time_ns()
    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="bounded-full-tid",
    )

    def fail_global_collector(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("full-TID snapshot used the unbounded collector")

    monkeypatch.setattr(status_cmd, "_collect_task_snapshots", fail_global_collector)

    snapshot = task_cmd.task_snapshot(tid, context=ctx)

    assert snapshot is not None
    assert snapshot.tid == tid
    assert snapshot.name == "bounded-full-tid"


def test_list_task_snapshots_reuses_collected_taskspec_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    started = time.time_ns()
    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="single-replay-list",
    )

    def fail_taskspec_reload(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("list re-read task history for TaskSpec payload")

    monkeypatch.setattr(task_cmd, "load_latest_taskspec_payload", fail_taskspec_reload)

    snapshots = task_cmd.list_task_snapshots(context=ctx, include_terminal=True)

    assert [snapshot.tid for snapshot in snapshots] == [tid]


def test_task_status_treats_created_runtime_as_non_live_for_terminal_docker_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955165"
    started = 1_762_000_000_000_000_000
    completed = started + 1_000_000_000
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="control_stop",
        status="cancelled",
        started_at=started,
        completed_at=completed,
        name="docker-stopped-task",
        runner_name="docker",
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "docker",
                "runtime_handle": _runtime_handle(
                    "docker",
                    "container-123",
                    kind="container",
                    authority="runner",
                    observations={"container_id": "container-123"},
                    metadata={"image": "python:3.13-alpine"},
                ),
            }
        )
    )

    class FakeRunnerPlugin:
        def describe(self, handle: Any) -> RunnerRuntimeDescription | None:
            return RunnerRuntimeDescription(
                runner=handle.runner,
                id=handle.id,
                state="created",
                metadata={"image": "python:3.13-alpine"},
            )

    monkeypatch.setattr(
        status_cmd,
        "require_runner_plugin",
        lambda name: FakeRunnerPlugin(),
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == "control_stop"
    assert snapshot.runtime is not None
    assert snapshot.runtime["state"] == "created"
    assert snapshot.status == "cancelled"


def test_task_status_uses_log_runtime_metadata_when_mapping_is_missing(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955164"
    started = 1_762_000_000_000_000_000
    completed = started + 1_000_000_000
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": "control_stop",
                "status": "cancelled",
                "tid": tid,
                "runner": "host",
                "runtime_handle": _runtime_handle(
                    "host",
                    "host-current",
                    host_pids=[os.getpid()],
                ),
                "taskspec": {
                    "name": "log-only-live-consumer-task",
                    "spec": {"runner": {"name": "host", "options": {}}},
                    "state": {
                        "status": "cancelled",
                        "started_at": started,
                        "completed_at": completed,
                    },
                    "metadata": {},
                },
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.status == "cancelled"
    assert snapshot.reconciliation is not None
    assert snapshot.reconciliation["classification"] == "runtime_conflict"
    assert (
        snapshot.reconciliation["reason"]
        == "weak_host_pid_ignored_for_terminal_lifecycle"
    )


def test_task_status_surfaces_terminal_log_state_once_task_pid_is_gone(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955162"
    started = 1_762_000_000_000_000_000
    completed = started + 1_000_000_000
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="control_stop",
        status="cancelled",
        started_at=started,
        completed_at=completed,
        name="exited-consumer-task",
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "host",
                "runtime_handle": _runtime_handle(
                    "host",
                    "999999999",
                    host_pids=[999_999_999],
                ),
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.status == "cancelled"


def test_task_status_reports_dead_host_running_snapshot_as_stale_liveness(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955167"
    started = 1_762_000_000_000_000_000
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="dead-running-host-task",
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "host",
                "runtime_handle": _runtime_handle(
                    "host",
                    "999999998",
                    host_pids=[999_999_998],
                ),
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == "task_started"
    assert snapshot.status == "running"
    assert snapshot.reconciliation is not None
    assert snapshot.reconciliation["classification"] == "stale_liveness"
    assert snapshot.reconciliation["reason"] == "host_process_not_live"


def test_cmd_status_surfaces_dead_host_running_snapshot_as_stale_liveness(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955168"
    started = 1_762_000_000_000_000_000
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="dead-running-host-task",
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "host",
                "runtime_handle": _runtime_handle(
                    "host",
                    "999999997",
                    host_pids=[999_999_997],
                ),
            }
        )
    )

    exit_code, payload = cmd_status(
        json_output=True,
        include_terminal=True,
        spec_context=root,
    )

    assert exit_code == 0
    assert payload is not None
    data = json.loads(payload)
    tasks = data["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["tid"] == tid
    assert tasks[0]["status"] == "running"
    assert tasks[0]["reconciliation"]["classification"] == "stale_liveness"
    assert tasks[0]["reconciliation"]["reason"] == "host_process_not_live"


def test_cmd_status_reports_stale_runtime_less_running_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955181"
    started = time.time_ns()

    monkeypatch.setattr(status_cmd, "STATUS_RUNTIMELESS_STALE_AFTER_SECONDS", -1.0)
    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="stale-manager",
    )

    exit_code, payload = cmd_status(json_output=True, spec_context=root)

    assert exit_code == 0
    assert payload is not None
    tasks = json.loads(payload)["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["tid"] == tid
    assert tasks[0]["status"] == "running"
    assert tasks[0]["reconciliation"]["reason"] == "runtime_missing_after_stale_window"

    exit_code, payload = cmd_status(
        json_output=True,
        include_terminal=True,
        spec_context=root,
    )

    assert exit_code == 0
    assert payload is not None
    tasks = json.loads(payload)["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["tid"] == tid
    assert tasks[0]["status"] == "running"
    assert tasks[0]["reconciliation"]["reason"] == "runtime_missing_after_stale_window"


def test_cmd_status_keeps_internal_service_running_without_runtime_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955190"
    started = time.time_ns()

    monkeypatch.setattr(status_cmd, "STATUS_RUNTIMELESS_STALE_AFTER_SECONDS", -1.0)
    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="task-monitor",
        metadata={
            "internal": True,
            "role": "task_monitor",
            "_weft_service_key": "_weft.service.task_monitor",
        },
    )
    ctx.queue("weft.log.tasks", persistent=False).write(
        json.dumps(
            {
                "event": "task_activity",
                "status": "running",
                "tid": tid,
                "activity": "scanning",
                "waiting_on": "weft.log.tasks",
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.status == "running"
    assert snapshot.activity == "scanning"
    assert snapshot.waiting_on == "weft.log.tasks"


def test_cmd_status_keeps_runtime_less_manager_running_when_registry_is_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955182"
    started = time.time_ns()

    monkeypatch.setattr(status_cmd, "STATUS_RUNTIMELESS_STALE_AFTER_SECONDS", -1.0)
    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="live-manager",
    )
    ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False).write(
        json.dumps(
            {
                "tid": tid,
                "name": "live-manager",
                "status": "active",
                "runtime_handle": _runtime_handle(
                    "host",
                    str(os.getpid()),
                    host_pids=[os.getpid()],
                ),
                "role": "manager",
                "requests": WEFT_SPAWN_REQUESTS_QUEUE,
            }
        )
    )

    exit_code, payload = cmd_status(
        json_output=True,
        include_terminal=True,
        spec_context=root,
    )

    assert exit_code == 0
    assert payload is not None
    tasks = json.loads(payload)["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["tid"] == tid
    assert tasks[0]["status"] == "running"


def test_cmd_status_marks_superseded_manager_record_failed(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    old_tid = "1844674407370955183"
    active_tid = "1844674407370955184"
    started = time.time_ns()

    _write_task_log_entry(
        ctx=ctx,
        tid=old_tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="old-manager",
        metadata={"role": "manager"},
    )
    _write_task_log_entry(
        ctx=ctx,
        tid=active_tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="active-manager",
        metadata={"role": "manager"},
    )
    ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False).write(
        json.dumps(
            _manager_service_payload(
                ctx,
                tid=active_tid,
                name="active-manager",
                runtime_handle=_runtime_handle(
                    "host",
                    str(os.getpid()),
                    host_pids=[os.getpid()],
                ),
            )
        )
    )

    exit_code, payload = cmd_status(json_output=True, spec_context=root)

    assert exit_code == 0
    assert payload is not None
    default_tasks = json.loads(payload)["tasks"]
    assert [task["tid"] for task in default_tasks] == [active_tid]
    assert default_tasks[0]["status"] == "running"

    exit_code, payload = cmd_status(
        json_output=True,
        include_terminal=True,
        spec_context=root,
    )

    assert exit_code == 0
    assert payload is not None
    tasks = {task["tid"]: task for task in json.loads(payload)["tasks"]}
    assert tasks[active_tid]["status"] == "running"
    assert tasks[old_tid]["status"] == "failed"
    assert tasks[old_tid]["completed_at"] is None
    assert tasks[old_tid]["reconciliation"]["classification"] == (
        "superseded_manager_record"
    )
    assert tasks[old_tid]["reconciliation"]["active_manager_tid"] == active_tid


def test_status_snapshot_preserves_activity_from_latest_log_event(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955170"
    started = 1_762_000_000_000_000_000

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="waiting-stage",
    )
    ctx.queue("weft.log.tasks", persistent=False).write(
        json.dumps(
            {
                "event": "task_activity",
                "tid": tid,
                "status": "running",
                "activity": "waiting",
                "waiting_on": "P123.first-to-second",
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.status == "running"
    assert snapshot.activity == "waiting"
    assert snapshot.waiting_on == "P123.first-to-second"


def test_status_snapshot_preserves_runner_diagnostics(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1778000000000000001"
    diagnostics = {
        "phase": "runtime_startup",
        "runner": "host",
        "target_type": "agent",
        "message": "startup boom",
    }
    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="work_failed",
        status="failed",
        started_at=1_000,
        completed_at=2_000,
        name="diagnostic-task",
        runner_diagnostics=diagnostics,
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.runner_diagnostics == diagnostics
    assert snapshot.to_dict()["runner_diagnostics"] == diagnostics


def test_terminal_snapshot_omits_activity_when_status_is_terminal(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955171"
    started = 1_762_000_000_000_000_000
    completed = started + 2_000_000

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="task_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="terminal-stage",
    )
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": "task_activity",
                "tid": tid,
                "status": "running",
                "activity": "waiting",
                "waiting_on": "P123.first-to-second",
            }
        )
    )
    log_queue.write(
        json.dumps(
            {
                "event": "work_completed",
                "tid": tid,
                "status": "completed",
                "taskspec": {
                    "name": "terminal-stage",
                    "spec": {"runner": {"name": "host", "options": {}}},
                    "state": {
                        "status": "completed",
                        "started_at": started,
                        "completed_at": completed,
                    },
                    "metadata": {},
                },
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.status == "completed"
    assert snapshot.activity is None
    assert snapshot.waiting_on is None


def test_task_status_ignores_late_activity_regression_after_terminal_state(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955179"
    started = 1_762_000_000_000_000_000
    completed = started + 2_000_000

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="work_completed",
        status="completed",
        started_at=started,
        completed_at=completed,
        name="terminal-stage",
    )
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": "task_activity",
                "tid": tid,
                "status": "running",
                "activity": "waiting",
                "waiting_on": "T1844674407370955179.inbox",
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == "work_completed"
    assert snapshot.status == "completed"
    assert snapshot.activity is None
    assert snapshot.waiting_on is None


def test_task_status_ignores_late_full_payload_regression_after_terminal_state(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955180"
    started = 1_762_000_000_000_000_000
    completed = started + 2_000_000

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="work_completed",
        status="completed",
        started_at=started,
        completed_at=completed,
        name="terminal-stage",
    )
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": "poll_report",
                "status": "running",
                "tid": tid,
                "taskspec": {
                    "name": "terminal-stage",
                    "spec": {"runner": {"name": "host", "options": {}}},
                    "state": {
                        "status": "running",
                        "started_at": started,
                        "completed_at": completed,
                    },
                    "metadata": {},
                },
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == "work_completed"
    assert snapshot.status == "completed"
    assert snapshot.completed_at == completed


def test_task_status_reads_pipeline_snapshot_when_available(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955172"
    started = 1_762_000_000_000_000_000

    _write_pipeline_log_entry(
        ctx=ctx,
        tid=tid,
        started_at=started,
        completed_at=None,
    )
    ctx.queue(f"P{tid}.status", persistent=True).write(
        json.dumps(
            {
                "type": "pipeline_status",
                "pipeline_tid": tid,
                "pipeline_name": "demo-pipeline",
                "status": "running",
                "activity": "waiting",
                "waiting_on": f"P{tid}.events",
                "timestamp": time.time_ns(),
                "stages": [{"name": "first", "status": "running"}],
                "edges": [{"name": "pipeline-to-first", "status": "running"}],
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == "pipeline_status"
    assert snapshot.status == "running"
    assert snapshot.activity == "waiting"
    assert snapshot.waiting_on == f"P{tid}.events"
    assert snapshot.pipeline_status is not None
    assert snapshot.pipeline_status["stages"][0]["name"] == "first"


def test_task_status_prefers_newer_terminal_log_snapshot_over_stale_pipeline_status(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955174"
    started = 1_762_000_000_000_000_000
    completed = started + 1_000

    _write_pipeline_log_entry(
        ctx=ctx,
        tid=tid,
        status="completed",
        event="work_completed",
        started_at=started,
        completed_at=completed,
    )
    ctx.queue(f"P{tid}.status", persistent=True).write(
        json.dumps(
            {
                "type": "pipeline_status",
                "pipeline_tid": tid,
                "pipeline_name": "demo-pipeline",
                "status": "running",
                "activity": "waiting",
                "timestamp": started,
                "stages": [{"name": "first", "status": "running"}],
                "edges": [{"name": "pipeline-to-first", "status": "running"}],
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == "work_completed"
    assert snapshot.status == "completed"
    assert snapshot.pipeline_status is not None
    assert snapshot.pipeline_status["status"] == "running"


def test_task_status_falls_back_to_log_snapshot_when_pipeline_snapshot_missing(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955173"
    started = 1_762_000_000_000_000_000

    _write_pipeline_log_entry(
        ctx=ctx,
        tid=tid,
        started_at=started,
        completed_at=None,
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == "task_started"
    assert snapshot.pipeline_status is None


def test_task_status_keeps_external_runner_terminal_when_runtime_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955163"
    started = 1_762_000_000_000_000_000
    completed = started + 1_000_000_000
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="control_stop",
        status="cancelled",
        started_at=started,
        completed_at=completed,
        name="docker-task",
        runner_name="docker",
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "docker",
                "runtime_handle": _runtime_handle(
                    "docker",
                    "container-123",
                    kind="container",
                    authority="runner",
                    observations={"container_id": "container-123"},
                    metadata={"image": "python:3.13-alpine"},
                ),
            }
        )
    )

    class FakeRunnerPlugin:
        def describe(self, handle: Any) -> RunnerRuntimeDescription | None:
            return RunnerRuntimeDescription(
                runner=handle.runner,
                id=handle.id,
                state="missing",
                metadata={"image": "python:3.13-alpine"},
            )

    monkeypatch.setattr(
        status_cmd,
        "require_runner_plugin",
        lambda name: FakeRunnerPlugin(),
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.status == "cancelled"


def test_cmd_status_host_runtime_uses_zombie_safe_pid_liveness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955166"
    started = 1_762_000_000_000_000_000
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": "work_started",
                "status": "running",
                "tid": tid,
                "taskspec": {
                    "name": "host-task",
                    "spec": {"runner": {"name": "host", "options": {}}},
                    "state": {
                        "status": "running",
                        "started_at": started,
                        "completed_at": None,
                    },
                    "metadata": {},
                },
            }
        )
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "host",
                "runtime_handle": _runtime_handle(
                    "host",
                    "43210",
                    host_pids=[43210],
                ),
            }
        )
    )

    monkeypatch.setattr(host_runner, "pid_is_live", lambda pid: False)

    exit_code, payload = cmd_status(
        json_output=True, include_terminal=True, spec_context=root
    )

    assert exit_code == 0
    assert payload is not None
    data = json.loads(payload)
    assert len(data["tasks"]) == 1
    entry = data["tasks"][0]
    assert entry["runtime_handle"]["runner"] == "host"
    assert entry["runtime"]["runner"] == "host"
    assert entry["runtime"]["state"] == "missing"


def test_task_status_rejects_running_host_task_when_pid_identity_mismatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955167"
    started = 1_762_000_000_000_000_000
    stale_pid = 57
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)

    _write_task_log_entry(
        ctx=ctx,
        tid=tid,
        event="work_started",
        status="running",
        started_at=started,
        completed_at=None,
        name="host-task",
        runner_name="host",
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "host",
                "runtime_handle": _runtime_handle(
                    "host",
                    str(stale_pid),
                    host_pids=[stale_pid],
                    observations={
                        "host_processes": [
                            {"pid": stale_pid, "create_time": 1_778_084_364.13}
                        ],
                    },
                ),
            }
        )
    )

    class FakeRunnerPlugin:
        def describe(self, handle: Any) -> RunnerRuntimeDescription | None:
            return RunnerRuntimeDescription(
                runner=handle.runner,
                id=handle.id,
                state="missing",
                metadata={"host_pids": [stale_pid]},
            )

    monkeypatch.setattr(status_cmd, "_pid_alive", lambda pid: pid == stale_pid)
    monkeypatch.setattr(
        status_cmd, "handle_has_live_host_process", lambda handle: False
    )
    monkeypatch.setattr(
        status_cmd,
        "require_runner_plugin",
        lambda name: FakeRunnerPlugin(),
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.runtime is not None
    assert snapshot.runtime["state"] == "missing"
    assert snapshot.status == "running"
    assert snapshot.reconciliation is not None
    assert snapshot.reconciliation["classification"] == "stale_liveness"
    assert snapshot.reconciliation["reason"] == "host_process_not_live"


def test_cmd_status_discovers_parent_context_from_subdirectory(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "project"
    nested = root / "subdir" / "child"
    nested.mkdir(parents=True)

    prepared_root = prepare_project_root(root)
    ctx = build_context(spec_context=prepared_root)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("payload")

    monkeypatch.chdir(Path(nested))

    exit_code, payload = cmd_status()

    assert exit_code == 0
    assert payload is not None
    assert "total_messages: 1" in payload


def test_watch_task_events_uses_queue_monitor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955167"
    created_monitors: list[_FakeQueueChangeMonitor] = []
    iter_queue_names: list[str] = []
    iter_since_timestamps: list[int | None] = []
    log_iterations = iter(
        [
            [],
            [
                (
                    {
                        "tid": tid,
                        "status": "completed",
                        "event": "work_completed",
                        "taskspec": {
                            "name": "status-task",
                            "state": {"status": "completed"},
                        },
                    },
                    123,
                )
            ],
        ]
    )

    def _fake_monitor(queues, *, config=None):
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(status_cmd, "QueueChangeMonitor", _fake_monitor)

    def _fake_iter_log_events(queue, *, since_timestamp=None):
        iter_queue_names.append(queue.name)
        iter_since_timestamps.append(since_timestamp)
        return next(log_iterations, [])

    monkeypatch.setattr(status_cmd, "_iter_log_events", _fake_iter_log_events)

    exit_code = status_cmd._watch_task_events(
        ctx,
        tid_filters=None,
        status_filter=None,
        json_output=False,
        interval=0.25,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "work_completed" in captured.out
    assert len(created_monitors) == 1
    assert created_monitors[0].queue_names == ["weft.log.tasks"]
    assert iter_queue_names == ["weft.log.tasks", "weft.log.tasks"]
    assert iter_since_timestamps == [0, 0]
    assert created_monitors[0].wait_calls == [0.25, 0.25]
