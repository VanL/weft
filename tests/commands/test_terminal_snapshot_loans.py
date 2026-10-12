"""Terminal observations reuse history and retained terminal proof [MF-5]."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from tests.helpers.reactor_driver import drive_until
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    TERMINAL_TASK_EVENTS,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TASK_STATE_QUEUE_PREFIX,
)
from weft.client import WeftClient
from weft.client._task import _task_with_configuration
from weft.commands import _task_history, result, tasks
from weft.core import task_evidence
from weft.core.monitor.collation import update_from_task_log_payload
from weft.core.monitor.store import open_monitor_store
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.tasks import Consumer
from weft.core.taskspec import TaskSpec
from weft.core.taskspec.transport import validate_taskspec_payload

pytestmark = [pytest.mark.shared]


def _spec(tid: str, target: str = "echo_payload") -> TaskSpec:
    return validate_taskspec_payload(
        {
            "name": "terminal-loan",
            "spec": {
                "type": "function",
                "function_target": f"tests.tasks.sample_targets:{target}",
            },
        },
        resolved_tid=tid,
    )


def test_terminal_snapshot_acquires_history_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int | None] = []
    original = task_evidence.read_task_history

    def read(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs.get("after_timestamp"))
        return original(*args, **kwargs)

    monkeypatch.setattr(task_evidence, "read_task_history", read)
    monkeypatch.setattr(_task_history, "read_task_history", read)
    with WeftTestHarness() as harness:
        with harness.context.queue("test.terminal.tid", persistent=False) as queue:
            tid = str(queue.generate_timestamp())
        snapshot = tasks.task_terminal_snapshot(tid, context=harness.context)
    assert snapshot.status == "unknown"
    assert calls == [None]


def test_ping_status_reuses_one_log_fold_for_monitor_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PING and retained-source selection share one acquired lifecycle fold."""
    folded_ids: list[tuple[int, ...]] = []
    original_fold = task_evidence.LogFold.fold

    def fold(
        self: task_evidence.LogFold,
        rows: Sequence[tuple[dict[str, Any], int]],
        *,
        tid: str,
    ) -> task_evidence.LogFold:
        folded_ids.append(tuple(message_id for _payload, message_id in rows))
        return original_fold(self, rows, tid=tid)

    with WeftTestHarness() as harness:
        context = harness.context
        with context.queue("test.terminal.tid") as allocator:
            tid = str(allocator.generate_timestamp())
        spec = _spec(tid)
        consumer = Consumer(context.broker_target, spec, config=context.broker_config)
        with consumer.drive_scope():
            consumer.run_work_item("published result")
            with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
                store = open_monitor_store(context, queue=log)
                store.ensure_schema()
                published_ids: list[int] = []
                for raw, message_id in log.peek_generator(with_timestamps=True):
                    payload = json.loads(raw)
                    if payload.get("tid") != tid:
                        continue
                    published_ids.append(message_id)
                    update = update_from_task_log_payload(
                        payload, message_id=message_id
                    )
                    assert update is not None
                    store.upsert_task_event(update)
                retained = store.get_task(tid)
                assert retained is not None and retained.terminal_status == "completed"
                with monkeypatch.context() as patch:
                    patch.setattr(task_evidence.LogFold, "fold", fold)
                    record = tasks._task_status_record(
                        tid,
                        context=context,
                        observation_queue=log,
                        ping=True,
                        probe_timeout=0,
                        monitor_lookup=(retained, None),
                    )
                assert record is not None and record.snapshot.status == "completed"
                assert record.snapshot.event in TERMINAL_TASK_EVENTS
                assert folded_ids == [tuple(published_ids)]


def test_submitted_terminal_snapshot_uses_custom_route_without_initialization() -> None:
    with WeftTestHarness() as harness, WeftClient(harness.context) as client:
        with harness.context.queue("test.terminal.tid", persistent=False) as queue:
            tid = str(queue.generate_timestamp())
        payload = _spec(tid).model_dump(mode="json")
        payload["io"]["outputs"]["outbox"] = "terminal.accepted.outbox"
        accepted = validate_taskspec_payload(payload, resolved_tid=tid)
        task = _task_with_configuration(client, tid, harness.context, accepted)
        with harness.context.queue(
            "terminal.accepted.outbox", persistent=False
        ) as queue:
            queue.write(json.dumps({"answer": "ready"}))
            snapshot = task.terminal_snapshot()
            assert queue.peek_one() == json.dumps({"answer": "ready"})
        assert snapshot.status == "completed"
        assert snapshot.source == "outbox"


def test_terminal_snapshot_reuses_negative_history_after_queue_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queue wake advances one history cursor and retains local terminal proof."""
    calls: list[int | None] = []
    waits: list[float | None] = []
    original = task_evidence.read_task_history

    def read(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs.get("after_timestamp"))
        return original(*args, **kwargs)

    monkeypatch.setattr(task_evidence, "read_task_history", read)
    monkeypatch.setattr(_task_history, "read_task_history", read)
    with WeftTestHarness() as harness:
        context = harness.context
        with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False) as log:
            tid = str(log.generate_timestamp())
            horizon = log.write(json.dumps({"tid": "unrelated", "event": "noise"}))
        terminal = json.dumps(
            {"type": "terminal", "source": "task", "tid": tid, "status": "cancelled"}
        )
        with context.queue(f"T{tid}.ctrl_out", persistent=False) as control:

            def wake(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
                waits.append(timeout)
                control.write(terminal)
                return True

            monkeypatch.setattr(QueueChangeMonitor, "wait", wake)
            snapshot = tasks.task_terminal_snapshot(tid, timeout=10, context=context)
            assert snapshot.status == "cancelled"
            assert snapshot.source == "ctrl_out"
            assert control.peek_one() == terminal
    assert calls == [None, horizon]
    assert len(waits) == 1
    assert waits[0] is not None and 1 < waits[0] <= 10


def _retire_terminal_log_rows(harness: WeftTestHarness, tid: str) -> None:
    with harness.context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        store = open_monitor_store(harness.context, queue=log)
        store.ensure_schema()
        rows = list(log.peek_generator(with_timestamps=True))
        terminal_ids: list[int] = []
        for raw, message_id in rows:
            payload = json.loads(raw)
            if payload.get("tid") != tid:
                continue
            update = update_from_task_log_payload(payload, message_id=message_id)
            assert update is not None
            store.upsert_task_event(update)
            if payload.get("event") in TERMINAL_TASK_EVENTS:
                terminal_ids.append(message_id)
        assert terminal_ids
        for message_id in terminal_ids:
            assert log.delete(message_id=message_id)
        retained = [
            json.loads(raw) for raw, _mid in log.peek_generator(with_timestamps=True)
        ]
        assert any(row.get("tid") == tid for row in retained)
        assert not any(
            row.get("tid") == tid and row.get("event") in TERMINAL_TASK_EVENTS
            for row in retained
        )


def test_partial_log_retirement_recovers_real_consumer_terminal_status_and_result() -> (
    None
):
    with WeftTestHarness() as harness:
        context = harness.context
        with context.queue("test.terminal.tid", persistent=False) as queue:
            tid = str(queue.generate_timestamp())
        spec = _spec(tid, "fail_payload")
        consumer = Consumer(context.broker_target, spec, config=context.broker_config)
        try:
            with context.queue(spec.io.inputs["inbox"], persistent=False) as inbox:
                inbox.write(json.dumps({"args": ["payload"]}))
            drive_until(
                lambda: consumer.taskspec.state.status,
                lambda status: status == "failed",
                step=consumer.process_once,
                wait=consumer.wait_for_activity,
                timeout=10,
            )
        finally:
            consumer.cleanup()
        _retire_terminal_log_rows(harness, tid)
        # Remove local terminal publications so this proves retained fallback,
        # while keeping the Consumer's original nonterminal task-log rows.
        # The inline Consumer shares this live test process, so retire its
        # runtime rows too instead of leaving a falsely live process handle.
        for queue_name in (
            spec.io.control["ctrl_out"],
            f"{WEFT_TASK_STATE_QUEUE_PREFIX}{tid}",
        ):
            with context.queue(queue_name, persistent=False) as queue:
                for _body, message_id in list(
                    queue.peek_generator(with_timestamps=True)
                ):
                    assert queue.delete(message_id=message_id)
        snapshot = tasks.task_snapshot(tid, context=context)
        assert snapshot is not None
        assert snapshot.status == "failed"
        assert snapshot.reconciliation is not None
        assert snapshot.reconciliation["classification"] == "terminal_monitor_store"
        outcome = result.await_task_result(context, tid, timeout=0)
        assert outcome.status == "failed"
        assert outcome.error and "intentional failure" in outcome.error
