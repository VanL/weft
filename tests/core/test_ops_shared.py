"""Tests for shared ops wiring between CLI adapters and the client."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from unittest.mock import Mock

import pytest

import weft.commands.events as events_mod
import weft.commands.queue as queue_mod
import weft.commands.result as result_mod
import weft.commands.run as run_mod
import weft.commands.specs as specs_mod
import weft.commands.submission as submission_mod
from tests.helpers.weft_harness import WeftTestHarness
from weft._exceptions import CommandTimeoutError
from weft.client import WeftClient
from weft.commands.types import TaskEvent, TaskResult
from weft.context import WeftContext
from weft.core import manager_runtime
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_run_adapter_routes_manager_recovery_through_shared_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_shared(
        context: WeftContext,
        *,
        submitted_tid: str | int,
    ) -> manager_runtime.ManagerEnsureResult:
        captured["context"] = context
        captured["submitted_tid"] = submitted_tid
        return manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record={"tid": "1776000000000000000"},
            started_here=False,
            process_handle=None,
            reason="ready",
        )

    monkeypatch.setattr(
        run_mod,
        "_shared_ensure_manager_after_submission",
        _fake_shared,
    )

    context = Mock(spec=WeftContext)
    result = run_mod._ensure_manager_after_submission(
        context,
        submitted_tid="1776000000000000001",
    )

    assert result.manager_record == {"tid": "1776000000000000000"}
    assert captured["context"] is context
    assert captured["submitted_tid"] == "1776000000000000001"


def test_client_submission_and_shared_result_wait_match() -> None:
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
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

    def _fake_prepare(
        context: WeftContext,
        taskspec: TaskSpec | Mapping[str, object],
        *,
        payload: object = None,
        **overrides: object,
    ) -> submission_mod.PreparedSubmissionRequest:
        captured["prepare_context"] = context
        captured["taskspec"] = taskspec
        captured["payload"] = payload
        captured["overrides"] = overrides
        return submission_mod.PreparedSubmissionRequest(
            name="demo",
            taskspec=taskspec,
            payload=payload,
        )

    def _fake_submit_prepared(
        context: WeftContext,
        prepared: submission_mod.PreparedSubmissionRequest,
        *,
        session: object | None = None,
        connection_queue: object | None = None,
    ) -> submission_mod._SubmittedPreparedOutcome:
        captured["submit_context"] = context
        captured["prepared"] = prepared
        captured["session"] = session
        captured["connection_queue"] = connection_queue
        return submission_mod._SubmittedPreparedOutcome(
            receipt=submission_mod.SubmittedTaskReceipt(
                tid="1776000000000000001",
                name="demo",
                submitted_at_ns=1776000000000000001,
                context_root=str(context.root),
            ),
            runtime_context=context,
            taskspec=submission_mod._bind_accepted_taskspec(
                submission_mod.encode_taskspec_transport_payload(
                    submission_mod.normalize_taskspec(prepared.taskspec)
                ),
                "1776000000000000001",
            ),
        )

    monkeypatch.setattr(submission_mod, "prepare", _fake_prepare)
    monkeypatch.setattr(
        submission_mod,
        "_submit_prepared_outcome",
        _fake_submit_prepared,
    )

    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        task = client.submit(
            {"name": "demo", "spec": {"type": "command", "process_target": "echo"}}
        )

    assert task.tid == "1776000000000000001"
    assert captured["submit_context"] is captured["prepare_context"]
    assert captured["session"] is None
    assert captured["connection_queue"] is None
    assert captured["payload"] is None
    assert captured["prepared"] == submission_mod.PreparedSubmissionRequest(
        name="demo",
        taskspec=captured["taskspec"],
        payload=None,
    )


@pytest.mark.parametrize("timeout", [None, 0.0, 5.0])
@pytest.mark.parametrize("status", ["completed", "timeout"])
def test_follow_projects_one_collector_with_original_budget(
    monkeypatch: pytest.MonkeyPatch, timeout: float | None, status: str
) -> None:
    """Projection lends one original budget and retains task-outcome meaning."""
    ctx = Mock(spec=WeftContext)
    tid = "1776000000000000001"
    spec = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "projection",
            "spec": {"type": "function", "function_target": "builtins:dict"},
        }
    )
    calls: list[tuple[WeftContext, str, float | None, TaskSpec | None, bool]] = []
    raw = TaskEvent(tid=tid, event_type=status, timestamp=1, payload={"status": status})
    result = TaskResult(
        tid=tid,
        status=status,
        value="done",
        stdout="done\n",
        stderr=None,
        error="task limit" if status == "timeout" else None,
    )

    def collect(
        context: WeftContext,
        child_tid: str,
        *,
        timeout: float | None = None,
        _accepted_taskspec: TaskSpec | None = None,
        _follow_lifecycle: bool = False,
    ) -> Iterator[TaskEvent | TaskResult]:
        calls.append(
            (context, child_tid, timeout, _accepted_taskspec, _follow_lifecycle)
        )
        yield raw
        yield result

    monkeypatch.setattr(events_mod, "_iter_task_result", collect)
    observed = list(
        events_mod.follow_task_events(
            ctx, tid, timeout=timeout, _accepted_taskspec=spec
        )
    )
    assert calls == [(ctx, tid, timeout, spec, True)]
    assert observed[0] is raw
    assert observed[-1].event_type == "result"
    assert observed[-1].payload == {
        "status": status,
        "value": "done",
        "stdout": "done\n",
        "stderr": None,
        "error": result.error,
    }


def test_follow_preserves_original_collector_timeout_after_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1776000000000000001"

    def collect(
        context: WeftContext,
        child_tid: str,
        *,
        timeout: float | None = None,
        _accepted_taskspec: TaskSpec | None = None,
        _follow_lifecycle: bool = False,
    ) -> Iterator[TaskEvent | TaskResult]:
        assert _follow_lifecycle
        yield TaskEvent(
            tid=child_tid,
            event_type="completed",
            timestamp=1,
            payload={"status": "completed"},
        )
        raise CommandTimeoutError(f"Timed out after {timeout} seconds")

    monkeypatch.setattr(events_mod, "_iter_task_result", collect)
    with pytest.raises(TimeoutError, match="Timed out after 5.0 seconds"):
        list(events_mod.follow_task_events(Mock(spec=WeftContext), tid, timeout=5.0))


@pytest.mark.parametrize("retain_log", [True, False])
def test_realtime_emits_terminal_state_from_acquired_real_task_proof(
    weft_harness: WeftTestHarness, retain_log: bool
) -> None:
    """Snapshot and terminal rendering share actual Consumer lifecycle proof."""
    ctx = weft_harness.context
    weft_harness.ensure_foreground_manager()
    with WeftClient(ctx) as client:
        task = client.submit(
            {
                "name": "realtime-proof",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "cleanup_on_exit": False,
                },
            },
            payload="done",
        )
        assert task.terminal_snapshot(timeout=15).status == "completed"
        if not retain_log:
            with ctx.queue("weft.log.tasks") as log:
                for body, message_id in list(log.peek_generator(with_timestamps=True)):
                    if json.loads(body).get("tid") == task.tid:
                        assert log.delete(message_id=message_id)
        observed = list(task.realtime_events(timeout=2))
        states = [event for event in observed if event.event_type == "state"]
        assert any(event.payload.get("status") == "completed" for event in states)
        assert observed[-2].event_type == "result"
        assert observed[-2].payload == {
            "status": "completed",
            "value": "done",
            "error": None,
        }
        assert observed[-1].event_type == "end"
        with ctx.queue(f"T{task.tid}.outbox") as outbox:
            assert outbox.peek_one() is not None
