"""Tests for worker CLI command helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import weft.commands._manager_bootstrap as manager_lifecycle
from simplebroker import Queue
from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_WORKERS_REGISTRY_QUEUE
from weft.commands import worker as worker_cmd
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def test_start_command_delegates_to_shared_bootstrap(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []

    monkeypatch.setattr(worker_cmd, "build_context", lambda spec_context=None: context)

    def _fake_ensure(context_arg, *, verbose):
        assert context_arg is context
        assert verbose is False
        calls.append("ensure")
        return (
            {"tid": "1761000000000000000", "pid": 12345},
            True,
            None,
        )

    monkeypatch.setattr(worker_cmd, "_ensure_manager", _fake_ensure)

    exit_code, message = worker_cmd.start_command(context_path=context_root)

    assert exit_code == 0
    assert message == "Started manager 1761000000000000000 (pid 12345)"
    assert calls == ["ensure"]


def test_start_command_reports_existing_manager(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)

    monkeypatch.setattr(worker_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        worker_cmd,
        "_ensure_manager",
        lambda context_arg, *, verbose: (
            {"tid": "1761000000000000001", "pid": 54321},
            False,
            None,
        ),
    )

    exit_code, message = worker_cmd.start_command(context_path=context_root)

    assert exit_code == 0
    assert message == "Manager 1761000000000000001 already running (pid 54321)"


def test_stop_command_delegates_to_shared_lifecycle_helper(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    calls: list[tuple[object, object, object, object, object]] = []

    monkeypatch.setattr(worker_cmd, "build_context", lambda spec_context=None: context)

    def fake_stop_manager(
        context_arg,
        record,
        process=None,
        *,
        tid=None,
        timeout=5.0,
        force=False,
        stop_if_absent=False,
    ):
        calls.append((context_arg, record, tid, timeout, force))
        assert stop_if_absent is False
        return True, None

    monkeypatch.setattr(worker_cmd, "_stop_manager", fake_stop_manager)

    exit_code, message = worker_cmd.stop_command(
        tid="1761000000000000001",
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    assert calls == [(context, None, "1761000000000000001", 0.1, False)]


def test_stop_command_rewrites_timeout_message(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)

    monkeypatch.setattr(worker_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        worker_cmd,
        "_stop_manager",
        lambda *args, **kwargs: (
            False,
            "Manager 1761000000000000001 did not stop within 0.1s",
        ),
    )

    exit_code, message = worker_cmd.stop_command(
        tid="1761000000000000001",
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 1
    assert message == "Worker 1761000000000000001 did not stop within 0.1s"


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
    context = build_context(context_root)

    monkeypatch.setattr(worker_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        worker_cmd, "_stop_manager", lambda *args, **kwargs: (True, None)
    )

    exit_code, message = worker_cmd.stop_command(
        tid="1761000000000000005",
        force=False,
        timeout=1.0,
        context_path=context_root,
        stop_if_absent=True,
    )

    assert exit_code == 0
    assert message is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_list_command_omits_stale_active_worker(tmp_path) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000006"

    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        process.wait(timeout=2.0)
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
                    "name": "stale",
                    "pid": process.pid,
                    "role": "manager",
                    "requests": "custom.manager.requests",
                }
            )
        )
        exit_code, payload = worker_cmd.list_command(
            json_output=True, context_path=context_root
        )
    finally:
        process.wait()

    assert exit_code == 0
    assert tid not in {record["tid"] for record in json.loads(payload)}


def test_stop_command_force_ignores_registry_only_pid_without_mapping(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000007"

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
                "name": "legacy-manager",
                "pid": os.getpid(),
                "role": "manager",
                "requests": "legacy.requests",
            }
        )
    )

    monkeypatch.setattr(
        manager_lifecycle,
        "terminate_process_tree",
        lambda *args, **kwargs: pytest.fail(
            "force stop must not trust an uncorroborated registry pid"
        ),
    )

    exit_code, message = worker_cmd.stop_command(
        tid=tid,
        force=True,
        timeout=0.0,
        context_path=context_root,
        stop_if_absent=True,
    )

    assert exit_code == 0
    assert message is None


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
