"""Tests for the ``weft status`` command helpers."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.commands import status as status_cmd
from weft.commands import tasks as task_cmd
from weft.commands.status import cmd_status, collect_status
from weft.context import build_context
from weft.core.runners import host as host_runner
from weft.ext import RunnerRuntimeDescription

pytestmark = [pytest.mark.shared]


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
) -> None:
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": event,
                "status": status,
                "tid": tid,
                "taskspec": {
                    "name": name,
                    "spec": {"runner": {"name": runner_name, "options": {}}},
                    "state": {
                        "status": status,
                        "started_at": started_at,
                        "completed_at": completed_at,
                    },
                    "metadata": {},
                },
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
                "pid": 11111,
                "task_pid": 11111,
                "managed_pids": [],
                "runner": "docker",
                "runtime_handle": {
                    "runner_name": "docker",
                    "runtime_id": "container-123",
                    "host_pids": [],
                    "metadata": {"image": "python:3.13-alpine"},
                },
            }
        )
    )

    class FakeRunnerPlugin:
        def describe(self, handle: Any) -> RunnerRuntimeDescription | None:
            return RunnerRuntimeDescription(
                runner_name=handle.runner_name,
                runtime_id=handle.runtime_id,
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
    assert entry["runtime_handle"]["runner_name"] == "docker"
    assert entry["runtime_handle"]["runtime_id"] == "container-123"
    assert entry["runtime"]["runner_name"] == "docker"
    assert entry["runtime"]["runtime_id"] == "container-123"
    assert entry["runtime"]["state"] == "running"
    assert entry["runtime"]["metadata"]["image"] == "python:3.13-alpine"
    assert entry["metadata"]["owner"] == "tests"


def test_task_status_keeps_terminal_log_state_running_while_task_pid_is_alive(
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
                "pid": os.getpid(),
                "task_pid": os.getpid(),
                "managed_pids": [],
                "runner": "host",
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == "control_stop"
    assert snapshot.status == "running"


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
                "runtime_handle": {
                    "runner_name": "docker",
                    "runtime_id": "container-123",
                    "host_pids": [],
                    "metadata": {"image": "python:3.13-alpine"},
                },
            }
        )
    )

    class FakeRunnerPlugin:
        def describe(self, handle: Any) -> RunnerRuntimeDescription | None:
            return RunnerRuntimeDescription(
                runner_name=handle.runner_name,
                runtime_id=handle.runtime_id,
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
                "task_pid": os.getpid(),
                "pid": os.getpid(),
                "runner": "host",
                "runtime_handle": {
                    "runner_name": "host",
                    "runtime_id": "host-current",
                    "host_pids": [os.getpid()],
                    "metadata": {},
                },
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
    assert snapshot.status == "running"


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
                "pid": 999_999_999,
                "task_pid": 999_999_999,
                "managed_pids": [],
                "runner": "host",
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.status == "cancelled"


def test_task_status_marks_dead_host_running_snapshot_failed(
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
                "pid": 999_999_998,
                "task_pid": 999_999_998,
                "managed_pids": [],
                "runner": "host",
            }
        )
    )

    snapshot = task_cmd.task_status(tid, context_path=root)

    assert snapshot is not None
    assert snapshot.event == "task_started"
    assert snapshot.status == "failed"


def test_cmd_status_surfaces_dead_host_running_snapshot_as_failed_with_all(
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
                "pid": 999_999_997,
                "task_pid": 999_999_997,
                "managed_pids": [],
                "runner": "host",
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
    assert tasks[0]["status"] == "failed"


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
                "pid": os.getpid(),
                "task_pid": os.getpid(),
                "managed_pids": [],
                "runner": "docker",
                "runtime_handle": {
                    "runner_name": "docker",
                    "runtime_id": "container-123",
                    "host_pids": [],
                    "metadata": {"image": "python:3.13-alpine"},
                },
            }
        )
    )

    class FakeRunnerPlugin:
        def describe(self, handle: Any) -> RunnerRuntimeDescription | None:
            return RunnerRuntimeDescription(
                runner_name=handle.runner_name,
                runtime_id=handle.runtime_id,
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
                "pid": 43210,
                "task_pid": 43210,
                "managed_pids": [43210],
                "runner": "host",
                "runtime_handle": {
                    "runner_name": "host",
                    "runtime_id": "43210",
                    "host_pids": [43210],
                    "metadata": {},
                },
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
    assert entry["runtime_handle"]["runner_name"] == "host"
    assert entry["runtime"]["runner_name"] == "host"
    assert entry["runtime"]["state"] == "missing"


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
    monkeypatch.setattr(
        status_cmd, "_iter_log_events", lambda _ctx: next(log_iterations, [])
    )

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
    assert created_monitors[0].wait_calls == [0.25, 0.25]
