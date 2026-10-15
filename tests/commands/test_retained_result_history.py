"""Task-owned observation survives raw-history retirement; private Monitor records do not route [MF-5]."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pytest

from tests.helpers.reactor_driver import drive_until
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft._exceptions import CommandExecutionError, CommandTimeoutError
from weft.commands import events as events_cmd
from weft.commands import result as result_cmd
from weft.commands import tasks as task_cmd
from weft.context import WeftContext, build_context
from weft.core.monitor.collation import update_from_task_log_row
from weft.core.monitor.store import MonitorStore, open_monitor_store
from weft.core.queue_window import DecodedQueueWindowRow, QueueWindowRow
from weft.core.task_evidence import queue_message_counts
from weft.core.task_state import task_state_queue_name
from weft.core.tasks.consumer import Consumer
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared, pytest.mark.timeout(30)]


def _finish_and_retire_history(
    context: WeftContext,
    *,
    retain_snapshot: bool = True,
    status: Literal["completed", "failed"] = "completed",
    retain_nonterminal_history: bool = False,
) -> TaskSpec:
    """Run a real target, collate its publications, then retire its raw proof."""
    with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        tid = str(log.generate_timestamp())
    spec = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "retained-briefing",
            "spec": {
                "type": "function",
                "function_target": {
                    "completed": "tests.tasks.sample_targets:echo_payload",
                    "failed": "tests.tasks.sample_targets:fail_payload",
                }[status],
                "cleanup_on_exit": False,
                "enable_process_title": False,
            },
            "io": {
                "inputs": {"inbox": f"retained.{tid}.inbox"},
                "outputs": {"outbox": f"retained.{tid}.outbox"},
                "control": {"ctrl_out": f"retained.{tid}.ctrl_out"},
            },
        }
    )
    with context.queue(spec.io.inputs["inbox"]) as inbox:
        inbox.write(json.dumps({"args": ["briefing"]}))
    task = Consumer(context.broker_target, spec, config=context.config)
    with task.drive_scope():
        drive_until(
            lambda: task.taskspec.state.status,
            lambda actual_status: actual_status == status,
            step=task.process_once,
            wait=task.wait_for_activity,
            timeout=15.0,
            diagnostics=lambda: task.taskspec.state.status,
        )
    # The finalizer has published before retirement. No runtime can republish.
    store = open_monitor_store(context)
    store.ensure_schema()
    with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        rows = list(log.peek_generator(with_timestamps=True))
        for body, message_id in rows:
            payload = json.loads(body)
            if payload.get("tid") != tid:
                continue
            update = update_from_task_log_row(
                DecodedQueueWindowRow(
                    raw=QueueWindowRow(WEFT_GLOBAL_LOG_QUEUE, body, message_id),
                    payload=payload,
                )
            )
            assert update is not None
            store.upsert_task_event(update)
            if (
                retain_nonterminal_history
                and payload.get("event") == "task_activity"
                and not update.terminal_seen
            ):
                continue
            log.delete(message_id=message_id)
    record = store.get_task(tid)
    assert record is not None and record.terminal_seen
    assert record.terminal_status == status
    assert (
        record.taskspec_summary["io"]["outputs"]["outbox"] == spec.io.outputs["outbox"]
    )
    with context.queue(spec.io.control["ctrl_out"]) as control:
        for _body, message_id in list(control.peek_generator(with_timestamps=True)):
            control.delete(message_id=message_id)
    # Inline execution uses this test process as its host PID. Retire that
    # runtime-only mapping so it cannot pretend the finished owner is live.
    if not retain_snapshot:
        with context.queue(task_state_queue_name(tid)) as state:
            for _body, message_id in list(state.peek_generator(with_timestamps=True)):
                state.delete(message_id=message_id)
    if status == "failed":
        with context.queue(spec.io.outputs["outbox"]) as outbox:
            for _body, message_id in list(outbox.peek_generator(with_timestamps=True)):
                outbox.delete(message_id=message_id)
    return spec


@pytest.mark.parametrize("surface", ["result", "realtime"])
@pytest.mark.parametrize("status", ["completed", "failed"])
def test_task_snapshot_preserves_custom_result_after_raw_history_retirement(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    status: Literal["completed", "failed"],
) -> None:
    context = build_context(spec_context=workdir)
    spec = _finish_and_retire_history(
        context, status=status, retain_nonterminal_history=True
    )
    assert spec.tid is not None

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail(
            "ordinary observation must not read or write private Monitor records"
        )

    monkeypatch.setattr(MonitorStore, "get_task", forbidden)
    monkeypatch.setattr(MonitorStore, "ensure_schema", forbidden)
    monkeypatch.setattr(MonitorStore, "upsert_task_event", forbidden)
    if surface == "result":
        result = result_cmd.await_task_result(context, spec.tid, timeout=2.0)
        assert result.status == status
        if status == "completed":
            assert result.value == "briefing"
        else:
            assert result.error is not None and "intentional failure" in result.error
    else:
        emitted = list(
            events_cmd.iter_task_realtime_events(context, spec.tid, timeout=2.0)
        )
        results = [event for event in emitted if event.event_type == "result"]
        assert len(results) == 1
        assert results[0].payload["status"] == status
        if status == "completed":
            assert results[0].payload["value"] == "briefing"
            with context.queue(spec.io.outputs["outbox"]) as outbox:
                assert outbox.peek_one() is not None


def test_retired_routes_do_not_recover_from_private_terminal_record(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retained custom output stays untouched when its route evidence has expired."""
    context = build_context(spec_context=workdir)
    spec = _finish_and_retire_history(context, retain_snapshot=False)
    assert spec.tid is not None

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("expired routes must not consult private Monitor records")

    monkeypatch.setattr(MonitorStore, "get_task", forbidden)
    result = result_cmd.await_task_result(context, spec.tid)
    assert result.status == "unknown"
    assert result.reconciliation == {
        "classification": "observation_metadata_unavailable"
    }
    with pytest.raises(CommandTimeoutError):
        result_cmd.await_task_result(context, spec.tid, timeout=0.0)
    with pytest.raises(CommandExecutionError, match="observation metadata unavailable"):
        task_cmd._send_control(context, spec.tid, "STOP")
    with context.queue(spec.io.outputs["outbox"]) as outbox:
        assert outbox.peek_one() is not None
    with context.queue(spec.io.control["ctrl_in"]) as control:
        assert control.stats().total == 0


def test_accepted_custom_configuration_survives_discovery_retirement(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=workdir)
    spec = _finish_and_retire_history(context, retain_snapshot=False)
    assert spec.tid is not None

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("accepted configuration must not consult Monitor records")

    monkeypatch.setattr(MonitorStore, "get_task", forbidden)
    result = result_cmd.await_task_result(
        context, spec.tid, timeout=0.0, _accepted_taskspec=spec
    )
    assert result.status == "completed" and result.value == "briefing"


def test_claimed_residue_does_not_override_task_owned_terminal_projection(
    workdir: Path,
) -> None:
    """Consumed output is recovery residue and leaves the published lifecycle intact."""
    context = build_context(spec_context=workdir)
    spec = _finish_and_retire_history(context)
    assert spec.tid is not None
    with context.queue(spec.io.outputs["outbox"]) as outbox:
        assert outbox.read_one() is not None
        with outbox.get_connection() as broker:
            counts = queue_message_counts(context, outbox.name, broker=broker)
        assert counts is not None and counts.claimed > 0 and counts.unclaimed == 0
    snapshot = task_cmd.task_snapshot(spec.tid, context=context)
    assert snapshot is not None and snapshot.status == "completed"
    terminal = task_cmd.task_terminal_snapshot(spec.tid, context=context)
    assert terminal.status == "completed"
