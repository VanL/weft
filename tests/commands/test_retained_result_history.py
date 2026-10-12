"""Results recover retained Monitor evidence without replay or maintenance [MF-5]."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

import pytest

from tests.helpers.reactor_driver import drive_until
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft._exceptions import CommandTimeoutError
from weft.commands import _result_wait as result_wait
from weft.commands import events as events_cmd
from weft.commands import result as result_cmd
from weft.commands import tasks as task_cmd
from weft.context import WeftContext, build_context
from weft.core.monitor import store as monitor_store
from weft.core.monitor.collation import update_from_task_log_row
from weft.core.monitor.store import MonitorStore, open_monitor_store
from weft.core.queue_window import DecodedQueueWindowRow, QueueWindowRow
from weft.core.task_evidence import queue_message_counts, queue_names_for_tid
from weft.core.task_state import task_state_queue_name
from weft.core.tasks.consumer import Consumer
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared, pytest.mark.timeout(30)]


def _acquired_configuration(
    context: WeftContext, tid: str
) -> result_wait.ResultMaterialization:
    """Inspect the actual shared acquisition owner with routes registered first."""
    outbox, control = queue_names_for_tid(tid, None)
    state = result_wait.ResultMaterialization(None, outbox, control)
    names = result_wait._result_observation_names(tid, state)
    with result_wait.result_observation(context, names) as initial:
        queues, _monitor = initial
        log = queues[WEFT_GLOBAL_LOG_QUEUE]
        with log.get_connection() as broker:
            state, _rows = result_wait._acquire_result_observation(
                context, tid, state, log, broker, now=time.monotonic()
            )
        # A new scope has its own log handle. It can overlap registration safely
        # while both scopes borrow the same retained context session.
        with result_wait.result_observation(
            context, result_wait._result_observation_names(tid, state)
        ) as configured:
            configured_log = configured[0][WEFT_GLOBAL_LOG_QUEUE]
            with configured_log.get_connection() as broker:
                return result_wait._classify_result_observation(
                    context, tid, state, configured_log, broker, now=time.monotonic()
                )


def _retire_task(
    context: WeftContext,
    *,
    retain_terminal: bool = True,
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
            if retain_terminal or not update.terminal_seen:
                store.upsert_task_event(update)
            if (
                retain_nonterminal_history
                and payload.get("event") == "task_activity"
                and not update.terminal_seen
            ):
                continue
            log.delete(message_id=message_id)
    record = store.get_task(tid)
    assert record is not None and record.terminal_seen == retain_terminal
    assert record.terminal_status == (status if retain_terminal else None)
    assert (
        record.taskspec_summary["io"]["outputs"]["outbox"] == spec.io.outputs["outbox"]
    )
    with context.queue(spec.io.control["ctrl_out"]) as control:
        for _body, message_id in list(control.peek_generator(with_timestamps=True)):
            control.delete(message_id=message_id)
    # Inline execution uses this test process as its host PID. Retire that
    # runtime-only mapping so it cannot pretend the finished owner is live.
    with context.queue(task_state_queue_name(tid)) as state:
        for _body, message_id in list(state.peek_generator(with_timestamps=True)):
            state.delete(message_id=message_id)
    if not retain_terminal or status == "failed":
        with context.queue(spec.io.outputs["outbox"]) as outbox:
            for _body, message_id in list(outbox.peek_generator(with_timestamps=True)):
                outbox.delete(message_id=message_id)
    return spec


@pytest.mark.parametrize("surface", ["result", "realtime"])
def test_retired_custom_result_recovers_production_monitor_record_without_maintenance(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    context = build_context(spec_context=workdir)
    spec = _retire_task(context)
    assert spec.tid is not None
    lookups: list[str] = []
    original = MonitorStore.get_task

    def get_task(self: MonitorStore, tid: str) -> Any:
        lookups.append(tid)
        return original(self, tid)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("result observation must not initialize or write Monitor tables")

    monkeypatch.setattr(MonitorStore, "get_task", get_task)
    monkeypatch.setattr(MonitorStore, "ensure_schema", forbidden)
    monkeypatch.setattr(MonitorStore, "upsert_task_event", forbidden)
    monkeypatch.setattr(
        monitor_store._MonitorTableAccess, "migrate_v5_to_v6", forbidden
    )
    if surface == "result":
        result = result_cmd.await_task_result(context, spec.tid, timeout=2.0)
        assert result.status == "completed"
        assert result.value == "briefing"
    else:
        emitted = list(
            events_cmd.iter_task_realtime_events(context, spec.tid, timeout=2.0)
        )
        results = [event for event in emitted if event.event_type == "result"]
        assert len(results) == 1
        assert results[0].payload["status"] == "completed"
        assert results[0].payload["value"] == "briefing"
        with context.queue(spec.io.outputs["outbox"]) as outbox:
            assert outbox.peek_one() is not None
    assert lookups == [spec.tid]


@pytest.mark.parametrize("timeout", [None, 0.0, 0.03])
def test_unresolved_result_preserves_unavailable_monitor_diagnostic_once(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, timeout: float | None
) -> None:
    context = build_context(spec_context=workdir)
    lookups: list[str] = []
    tid = "1791580000000000111"

    def unavailable(self: MonitorStore, target_tid: str) -> Any:
        lookups.append(target_tid)
        raise RuntimeError("retained store read failed")

    monkeypatch.setattr(MonitorStore, "get_task", unavailable)
    result = result_cmd.await_task_result(context, tid, timeout=timeout)
    assert result.status == "unknown"
    assert result.error == "monitor store unavailable: retained store read failed"
    assert result.reconciliation == {
        "classification": "monitor_store_unavailable",
        "reason": "store_read_failed",
    }
    assert lookups == [tid]


def test_nonterminal_retained_record_cannot_supply_routes_or_presence(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    # Collate the actual nonterminal publications only. The record is stale
    # diagnostic history, and cannot grant this fresh observer a custom route.
    spec = _retire_task(context, retain_terminal=False)
    assert spec.tid is not None
    assert result_cmd.await_task_result(context, spec.tid).status == "missing"
    with pytest.raises(CommandTimeoutError):
        result_cmd.await_task_result(context, spec.tid, timeout=0.0)


def test_optional_monitor_failure_does_not_downgrade_accepted_result(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = build_context(spec_context=workdir)
    spec = _retire_task(context)
    assert spec.tid is not None

    def unavailable(self: MonitorStore, target_tid: str) -> Any:
        raise RuntimeError("optional read failed")

    monkeypatch.setattr(MonitorStore, "get_task", unavailable)
    result = result_cmd.await_task_result(
        context, spec.tid, timeout=0.0, _accepted_taskspec=spec
    )
    assert result.status == "completed" and result.value == "briefing"


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_retained_terminal_record_survives_partial_raw_history_retirement(
    workdir: Path,
    status: Literal["completed", "failed"],
) -> None:
    """Real leftover activity cannot hide collated terminal proof or routes.

    Earlier retirement tests delete all raw rows. A partial exact-delete can
    leave task-owned nonterminal activity without its configuration event.
    """
    context = build_context(spec_context=workdir)
    spec = _retire_task(context, status=status, retain_nonterminal_history=True)
    assert spec.tid is not None
    with context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
        payloads = [json.loads(body) for body in log.peek_generator()]
    assert payloads
    assert all(payload["event"] == "task_activity" for payload in payloads)
    assert all("taskspec" not in payload for payload in payloads)
    materialized = _acquired_configuration(context, spec.tid)
    assert materialized is not None
    assert materialized.taskspec_payload is not None
    assert materialized.outbox_name == spec.io.outputs["outbox"]
    assert materialized.terminal_evidence is not None
    assert materialized.terminal_evidence.status == status
    result = result_cmd.await_task_result(context, spec.tid, timeout=2.0)
    assert result.status == status
    if status == "completed":
        assert result.value == "briefing"
    else:
        assert result.error is not None
        assert "intentional failure" in result.error


@pytest.mark.parametrize("surface", ["status", "terminal", "materialization"])
def test_claimed_residue_does_not_override_retained_completion(
    workdir: Path, surface: str
) -> None:
    """A real consumed result is recovery residue, not task failure [MF-5]."""
    context = build_context(spec_context=workdir)
    spec = _retire_task(context, retain_nonterminal_history=True)
    assert spec.tid is not None
    with context.queue(spec.io.outputs["outbox"]) as outbox:
        assert outbox.read_one() is not None
        with outbox.get_connection() as broker:
            counts = queue_message_counts(context, outbox.name, broker=broker)
        assert counts is not None and counts.claimed > 0 and counts.unclaimed == 0
    if surface == "status":
        snapshot = task_cmd.task_snapshot(spec.tid, context=context)
        assert snapshot is not None
        assert snapshot.status == "completed"
        assert snapshot.reconciliation is not None
        assert snapshot.reconciliation["classification"] == "terminal_monitor_store"
    elif surface == "terminal":
        terminal = task_cmd.task_terminal_snapshot(spec.tid, context=context)
        assert terminal.status == "completed"
        assert terminal.source == "monitor_store"
    else:
        materialized = _acquired_configuration(context, spec.tid)
        assert materialized is not None
        assert materialized.terminal_evidence is not None
        assert materialized.terminal_evidence.status == "completed"
        assert materialized.terminal_evidence.source == "monitor_store"


def test_result_observation_folds_each_production_history_row_only_once(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated observation turns consume only fresh target lifecycle rows."""
    context = build_context(spec_context=workdir)
    with context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
        tid = str(log.generate_timestamp())
    spec = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "incremental-evidence",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "cleanup_on_exit": False,
                "enable_process_title": False,
            },
        }
    )
    task = Consumer(context.broker_target, spec, config=context.config)
    with task.drive_scope():
        assert task.run_work_item({"args": ["briefing"]}) == "briefing"
    evidence_owner = result_wait.task_evidence
    original = evidence_owner.log_terminal_evidence
    inspected: list[int | None] = []

    def terminal(
        payload: dict[str, Any], *, tid: str, timestamp: int | None = None
    ) -> Any:
        inspected.append(timestamp)
        return original(payload, tid=tid, timestamp=timestamp)

    monkeypatch.setattr(evidence_owner, "log_terminal_evidence", terminal)
    state = result_wait._accepted_result_materialization(spec)
    with result_wait.result_observation(
        context, result_wait._result_observation_names(tid, state)
    ) as observation:
        log = observation[0][WEFT_GLOBAL_LOG_QUEUE]
        for _turn in range(2):
            with log.get_connection() as broker:
                state, _fresh = result_wait._acquire_result_observation(
                    context, tid, state, log, broker, now=time.monotonic()
                )
                state = result_wait._classify_result_observation(
                    context, tid, state, log, broker, now=time.monotonic()
                )
    assert inspected
    assert len(inspected) == len(set(inspected)), "old target rows were folded again"
    assert state.terminal_evidence is not None
    assert state.terminal_evidence.status == "completed"
