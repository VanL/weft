"""Tests for worker CLI command helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simplebroker import Queue
from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_WORKERS_REGISTRY_QUEUE
from weft.commands import worker as worker_cmd
from weft.context import build_context
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.shared]


def write_worker_spec(path: Path, *, context: Path) -> TaskSpec:
    tid = "1761000000000000000"
    spec = TaskSpec(
        tid=tid,
        name="worker",
        spec=SpecSection(
            type="function",
            function_target="weft.core.manager:Manager",
            timeout=None,
            weft_context=str(context),
        ),
        io=IOSection(
            inputs={"inbox": f"worker.{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={
                "ctrl_in": f"worker.{tid}.ctrl_in",
                "ctrl_out": f"worker.{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
        metadata={"capabilities": ["tests.tasks.sample_targets:large_output"]},
    )
    path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return spec


def test_start_background_invokes_launcher(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    spec_path = tmp_path / "worker.json"
    spec = write_worker_spec(spec_path, context=context_root)

    captured = {}

    class DummyProcess:
        def __init__(self):
            self.pid = 12345

    def fake_launch(task_cls, db_path, spec_arg, config=None, poll_interval=0.05):
        captured["task_cls"] = task_cls
        captured["db_path"] = db_path
        captured["spec_tid"] = spec_arg.tid
        captured["config"] = config
        return DummyProcess()

    monkeypatch.setattr(worker_cmd, "launch_task_process", fake_launch)

    exit_code, message = worker_cmd.start_command(spec_path, foreground=False)

    assert exit_code == 0
    assert "Started worker" in message
    assert captured["task_cls"].__name__ == "Manager"
    assert captured["db_path"] == context.broker_target
    assert captured["spec_tid"] == spec.tid


def test_start_foreground_runs_worker(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    build_context(context_root)
    spec_path = tmp_path / "worker.json"
    write_worker_spec(spec_path, context=context_root)

    calls = {}

    class DummyWorker:
        def __init__(self, db_path, spec_arg, config=None):
            calls["init"] = (db_path, spec_arg.tid)
            self.should_stop = True

        def run_until_stopped(self, poll_interval: float = 0.05, max_iterations=None):
            calls["run"] = poll_interval

        def cleanup(self):
            calls["cleanup"] = True

    monkeypatch.setattr(worker_cmd, "Manager", DummyWorker)

    exit_code, message = worker_cmd.start_command(spec_path, foreground=True)
    assert exit_code == 0
    assert message is None
    assert "init" in calls and "run" in calls and "cleanup" in calls


def test_stop_command_writes_stop_for_active_worker(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000001"

    registry_queue = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(json.dumps({"tid": tid, "status": "active"}))

    exit_code, message = worker_cmd.stop_command(
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


def test_stop_command_noops_for_stopped_worker(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000002"

    registry_queue = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(json.dumps({"tid": tid, "status": "stopped"}))

    exit_code, message = worker_cmd.stop_command(
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
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "active",
                "ctrl_in": f"worker.{tid}.ctrl_in",
            }
        )
    )

    exit_code, message = worker_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 1
    assert message is not None
    assert "did not stop" in message
    ctrl_queue = Queue(
        f"worker.{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    assert ctrl_queue.read_one() == "STOP"


def test_stop_command_stop_if_absent_still_sends_stop(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000004"

    exit_code, message = worker_cmd.stop_command(
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
    build_context(context_root)
    tid = "1761000000000000005"
    seen_pids: list[int | None] = []

    responses = iter(
        [
            {"tid": tid, "status": "active", "pid": 4321},
            {"tid": tid, "status": "stopped", "pid": 4321},
            {"tid": tid, "status": "stopped", "pid": 4321},
            None,
        ]
    )
    pid_states = iter([True, True, False, False])

    monkeypatch.setattr(worker_cmd, "_send_stop", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker_cmd,
        "_registry_entry_for_tid",
        lambda *args, **kwargs: next(responses),
    )

    def fake_pid_alive(pid: int | None) -> bool:
        seen_pids.append(pid)
        return next(pid_states)

    monkeypatch.setattr(worker_cmd, "_pid_alive", fake_pid_alive)

    exit_code, message = worker_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=1.0,
        context_path=context_root,
        stop_if_absent=True,
    )

    assert exit_code == 0
    assert message is None
    assert seen_pids.count(4321) >= 2


def test_stop_command_waits_when_worker_is_already_marked_stopped_but_pid_is_live(
    tmp_path, monkeypatch
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    build_context(context_root)
    tid = "1761000000000000006"
    seen_pids: list[int | None] = []

    responses = iter(
        [
            {"tid": tid, "status": "stopped", "pid": 5432},
            {"tid": tid, "status": "stopped", "pid": 5432},
            None,
        ]
    )
    pid_states = iter([True, False, False])

    monkeypatch.setattr(worker_cmd, "_send_stop", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker_cmd,
        "_registry_entry_for_tid",
        lambda *args, **kwargs: next(responses),
    )

    def fake_pid_alive(pid: int | None) -> bool:
        seen_pids.append(pid)
        return next(pid_states)

    monkeypatch.setattr(worker_cmd, "_pid_alive", fake_pid_alive)

    exit_code, message = worker_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=1.0,
        context_path=context_root,
        stop_if_absent=True,
    )

    assert exit_code == 0
    assert message is None
    assert seen_pids.count(5432) >= 2


def test_list_command_returns_table(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    registry_queue = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(json.dumps({"tid": "1", "status": "active", "name": "alpha"}))

    exit_code, payload = worker_cmd.list_command(
        json_output=False, context_path=context_root
    )
    assert exit_code == 0
    assert "alpha" in payload


def test_status_command_not_found(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    build_context(context_root)
    exit_code, payload = worker_cmd.status_command(
        tid="999", json_output=False, context_path=context_root
    )
    assert exit_code == 1
    assert "not found" in payload.lower()
