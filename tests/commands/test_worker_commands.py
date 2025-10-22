"""Tests for worker CLI command helpers."""

from __future__ import annotations

import json
from pathlib import Path

from simplebroker import Queue
from weft._constants import WEFT_WORKERS_REGISTRY_QUEUE
from weft.commands import worker as worker_cmd
from weft.context import build_context
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec


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
    context_root = tmp_path / "proj"
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
    assert captured["db_path"] == str(context.database_path)
    assert captured["spec_tid"] == spec.tid


def test_start_foreground_runs_worker(tmp_path, monkeypatch):
    context_root = tmp_path / "proj"
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


def test_stop_command_writes_stop(tmp_path):
    context_root = tmp_path / "ctx"
    context = build_context(context_root)
    tid = "1761000000000000001"

    registry_queue = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=str(context.database_path),
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
        db_path=str(context.database_path),
        persistent=False,
        config=context.config,
    )
    assert ctrl_queue.read_one() == "STOP"


def test_list_command_returns_table(tmp_path):
    context_root = tmp_path / "ctx"
    context = build_context(context_root)
    registry_queue = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=str(context.database_path),
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
    context_root = tmp_path / "ctx"
    build_context(context_root)
    exit_code, payload = worker_cmd.status_command(
        tid="999", json_output=False, context_path=context_root
    )
    assert exit_code == 1
    assert "not found" in payload.lower()
