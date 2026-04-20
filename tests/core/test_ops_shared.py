"""Tests for shared ops wiring between CLI adapters and the client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import weft.commands.events as events_mod
import weft.commands.queue as queue_mod
import weft.commands.result as result_mod
import weft.commands.run as run_mod
import weft.commands.specs as specs_mod
import weft.commands.submission as submission_mod
from tests.helpers.weft_harness import WeftTestHarness
from weft.client import WeftClient
from weft.commands.types import TaskEvent, TaskResult

pytestmark = [pytest.mark.shared]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_run_adapter_routes_manager_recovery_through_shared_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_shared(
        context,
        *,
        submitted_tid,
        verbose=False,
        ensure_manager_fn=None,
        delete_spawn_request_fn=None,
    ):
        captured["context"] = context
        captured["submitted_tid"] = submitted_tid
        captured["verbose"] = verbose
        captured["ensure_manager_fn"] = ensure_manager_fn
        captured["delete_spawn_request_fn"] = delete_spawn_request_fn
        return ({"tid": "1776000000000000000"}, False, None)

    monkeypatch.setattr(
        run_mod,
        "_shared_ensure_manager_after_submission",
        _fake_shared,
    )

    context = object()
    result = run_mod._ensure_manager_after_submission(
        context,
        submitted_tid="1776000000000000001",
        verbose=True,
    )

    assert result == ({"tid": "1776000000000000000"}, False, None)
    assert captured["context"] is context
    assert captured["submitted_tid"] == "1776000000000000001"
    assert captured["verbose"] is True
    assert captured["ensure_manager_fn"] is run_mod._ensure_manager
    assert captured["delete_spawn_request_fn"] is run_mod._delete_spawn_request


def test_client_submission_and_shared_result_wait_match() -> None:
    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        task = client.submit_command(["echo", "hello"])
        result = result_mod.await_task_result(client.context, task.tid, timeout=30.0)

        assert result.status == "completed"
        assert "hello" in (result.stdout or str(result.value))


def test_shared_queue_and_spec_ops_work_through_real_context() -> None:
    with WeftTestHarness() as harness:
        _write_json(
            harness.root / ".weft" / "tasks" / "stored-echo.json",
            {
                "name": "stored-echo",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
                "metadata": {},
            },
        )
        ctx = WeftClient(path=harness.root).context
        specs_list = specs_mod.list_spec_records(context_path=ctx.root)
        queue_mod.write_queue(ctx, "shared.ops.queue", "message")
        peeked = queue_mod.peek_queue(ctx, "shared.ops.queue", all_messages=True)

        assert any(item.name == "stored-echo" for item in specs_list)
        assert [item.message for item in peeked] == ["message"]


def test_client_submit_uses_shared_submission_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_submit(context, taskspec, *, payload=None, **overrides):
        captured["context"] = context
        captured["taskspec"] = taskspec
        captured["payload"] = payload
        captured["overrides"] = overrides
        return submission_mod.SubmittedTaskReceipt(
            tid="1776000000000000001",
            name="demo",
            submitted_at_ns=1776000000000000001,
        )

    monkeypatch.setattr(submission_mod, "submit", _fake_submit)

    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        task = client.submit(
            {"name": "demo", "spec": {"type": "command", "process_target": "echo"}}
        )

    assert task.tid == "1776000000000000001"
    assert captured["payload"] is None


def test_follow_task_events_reuses_shared_result_wait_without_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_iter(context, tid, *, follow=False):
        assert follow is True
        yield TaskEvent(
            tid=tid,
            event_type="completed",
            timestamp=1,
            payload={"tid": tid, "status": "completed"},
        )

    def _fake_await(
        context, tid, *, timeout=None, show_stderr=False, emit_stream=False
    ):
        captured["timeout"] = timeout
        return TaskResult(
            tid=tid,
            status="completed",
            value="done",
            stdout="done\n",
            stderr=None,
            error=None,
        )

    monkeypatch.setattr(events_mod, "iter_task_events", _fake_iter)
    monkeypatch.setattr(events_mod, "await_task_result", _fake_await)

    events = list(events_mod.follow_task_events(object(), "1776000000000000001"))

    assert captured["timeout"] is None
    assert events[-1].event_type == "result"
    assert events[-1].payload["status"] == "completed"
