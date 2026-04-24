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

    def _fake_prepare(context, taskspec, *, payload=None, **overrides):
        captured["prepare_context"] = context
        captured["taskspec"] = taskspec
        captured["payload"] = payload
        captured["overrides"] = overrides
        return submission_mod.PreparedSubmissionRequest(
            name="demo",
            taskspec=taskspec,
            payload=payload,
        )

    def _fake_submit_prepared(context, prepared):
        captured["submit_context"] = context
        captured["prepared"] = prepared
        return submission_mod.SubmittedTaskReceipt(
            tid="1776000000000000001",
            name="demo",
            submitted_at_ns=1776000000000000001,
        )

    monkeypatch.setattr(submission_mod, "prepare", _fake_prepare)
    monkeypatch.setattr(submission_mod, "submit_prepared", _fake_submit_prepared)

    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        task = client.submit(
            {"name": "demo", "spec": {"type": "command", "process_target": "echo"}}
        )

    assert task.tid == "1776000000000000001"
    assert captured["submit_context"] is captured["prepare_context"]
    assert captured["payload"] is None
    assert captured["prepared"] == submission_mod.PreparedSubmissionRequest(
        name="demo",
        taskspec=captured["taskspec"],
        payload=None,
    )


def test_follow_task_events_reuses_shared_result_wait_without_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_iter(context, tid, *, follow=False, timeout=None):
        assert follow is True
        captured["iter_timeout"] = timeout
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

    assert captured["iter_timeout"] is None
    assert captured["timeout"] is None
    assert events[-1].event_type == "result"
    assert events[-1].payload["status"] == "completed"


def test_follow_task_events_passes_remaining_timeout_to_result_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_iter(context, tid, *, follow=False, timeout=None):
        assert follow is True
        captured["iter_timeout"] = timeout
        yield TaskEvent(
            tid=tid,
            event_type="completed",
            timestamp=1,
            payload={"tid": tid, "status": "completed"},
        )

    def _fake_await(
        context, tid, *, timeout=None, show_stderr=False, emit_stream=False
    ):
        captured["result_timeout"] = timeout
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

    events = list(
        events_mod.follow_task_events(
            object(),
            "1776000000000000001",
            timeout=5.0,
        )
    )

    assert captured["iter_timeout"] == 5.0
    assert isinstance(captured["result_timeout"], float)
    assert 0.0 < captured["result_timeout"] <= 5.0
    assert events[-1].event_type == "result"


def test_follow_task_events_uses_visible_result_after_event_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_iter(context, tid, *, follow=False, timeout=None):
        assert follow is True
        captured["iter_timeout"] = timeout
        raise TimeoutError("event wait expired")
        yield  # pragma: no cover - makes this a generator

    def _fake_await(
        context, tid, *, timeout=None, show_stderr=False, emit_stream=False
    ):
        captured["result_timeout"] = timeout
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

    events = list(
        events_mod.follow_task_events(
            object(),
            "1776000000000000001",
            timeout=0.0,
        )
    )

    assert captured["iter_timeout"] == 0.0
    assert captured["result_timeout"] == events_mod.WEFT_COMPLETED_RESULT_GRACE_SECONDS
    assert events[-1].event_type == "result"
    assert events[-1].payload["status"] == "completed"


def test_follow_task_events_preserves_event_timeout_when_result_not_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_iter(context, tid, *, follow=False, timeout=None):
        raise TimeoutError("event wait expired")
        yield  # pragma: no cover - makes this a generator

    def _fake_await(
        context, tid, *, timeout=None, show_stderr=False, emit_stream=False
    ):
        return TaskResult(
            tid=tid,
            status="timeout",
            value=None,
            stdout=None,
            stderr=None,
            error="not ready",
        )

    monkeypatch.setattr(events_mod, "iter_task_events", _fake_iter)
    monkeypatch.setattr(events_mod, "await_task_result", _fake_await)

    with pytest.raises(TimeoutError, match="event wait expired"):
        list(
            events_mod.follow_task_events(
                object(),
                "1776000000000000001",
                timeout=0.0,
            )
        )


def test_realtime_events_uses_terminal_state_seen_during_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1776000000000000001"

    class _FakeQueue:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            return None

    class _FakeContext:
        config: dict[str, object] = {}

        def queue(self, name: str, *, persistent: bool = False) -> _FakeQueue:
            del persistent
            return _FakeQueue(name)

    class _FakeMonitor:
        def __init__(self, queues, *, config=None) -> None:
            del queues, config

        def wait(self, timeout=None) -> bool:
            del timeout
            return False

        def close(self) -> None:
            return None

    materialized = result_mod.ResultMaterialization(
        taskspec_payload=None,
        outbox_name=f"T{tid}.outbox",
        ctrl_out_name=f"T{tid}.ctrl_out",
        log_last_timestamp=42,
        terminal_status="completed",
        terminal_event_payload={
            "tid": tid,
            "status": "completed",
            "event": "work_completed",
        },
        terminal_event_timestamp=42,
    )

    monkeypatch.setattr(
        events_mod,
        "_await_result_materialization",
        lambda *args, **kwargs: materialized,
    )
    monkeypatch.setattr(events_mod, "QueueChangeMonitor", _FakeMonitor)
    monkeypatch.setattr(
        events_mod, "iter_queue_json_entries", lambda *args, **kwargs: iter(())
    )
    monkeypatch.setattr(
        events_mod, "_peek_result_value", lambda *args, **kwargs: "done"
    )
    monkeypatch.setattr(
        events_mod,
        "_task_snapshot_event",
        lambda context, normalized_tid: TaskEvent(
            tid=normalized_tid,
            event_type="snapshot",
            timestamp=1,
            payload={"tid": normalized_tid, "status": "completed"},
        ),
    )

    events = list(
        events_mod.iter_task_realtime_events(
            _FakeContext(),
            tid,
            timeout=1.0,
        )
    )

    assert [event.event_type for event in events] == [
        "snapshot",
        "state",
        "result",
        "end",
    ]
    assert events[-2].payload == {"status": "completed", "value": "done", "error": None}
