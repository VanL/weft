"""Submission observers retain connection ownership (Spec: [SB-0.4], [MF-6])."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import get_ident
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands import _spawn_submission as submission
from weft.core.task_state import task_state_queue_name
from weft.helpers import closing_queue_iterator, tid_short_form

pytestmark = [pytest.mark.shared, pytest.mark.timeout(30)]


def test_spawn_observation_reuses_connection_and_sees_external_state(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown polls reuse their owner and accept a later independent commit."""
    ctx = weft_harness.context
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as seed:
        tid = str(seed.generate_timestamp())
    connections: list[Any] = []
    if ctx.backend_name == "postgres":
        psycopg = pytest.importorskip("psycopg")
        original = psycopg.Connection.connect.__func__

        def connect(cls: Any, *args: Any, **kwargs: Any) -> Any:
            connection = original(cls, *args, **kwargs)
            connections.append(connection)
            return connection

        monkeypatch.setattr(psycopg.Connection, "connect", classmethod(connect))
        monkeypatch.setattr(psycopg, "connect", psycopg.Connection.connect)

    turns = 0
    warmed = 0
    repeated: list[int] = []

    def publish() -> None:
        with ctx.queue(task_state_queue_name(tid), persistent=True) as writer:
            writer.write(json.dumps({"full": tid, "short": tid_short_form(tid)}))

    with ThreadPoolExecutor(max_workers=1) as producer:

        def wait(monitor: Any, timeout: float) -> bool:
            nonlocal turns, warmed
            assert all(
                connection.closed or connection.info.transaction_status.name == "IDLE"
                for connection in connections
            )
            turns += 1
            assert turns <= 3
            if turns == 1:
                warmed = len(connections)
            else:
                repeated.append(len(connections) - warmed)
            if turns == 3:
                producer.submit(publish).result(timeout=10)
            return True

        monkeypatch.setattr(submission.QueueChangeMonitor, "wait", wait)
        result = submission.reconcile_submitted_spawn(ctx, tid, timeout=10)

    assert result.outcome == "spawned"
    assert result.tid == tid
    assert turns == 3
    assert repeated == [0, 0]
    # This test publishes evidence without a process; retire its owned fixture row.
    with ctx.queue(task_state_queue_name(tid), persistent=True) as cleanup:
        cleanup.delete()
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize(
    "failure", ["none", "queue-init", "monitor-init", "monitor-close", "queue-close"]
)
def test_spawn_observer_closes_all_resources_on_failure(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Every acquired queue closes even when acquisition or cleanup fails."""
    ctx = weft_harness.context
    created: list[Queue] = []
    closed: list[str] = []
    context_type = type(ctx)
    original_queue = context_type.queue
    original_close = Queue.close
    original_monitor = submission.QueueChangeMonitor

    def queue(context: Any, name: str, **kwargs: Any) -> Queue:
        if failure == "queue-init" and len(created) == 1:
            raise RuntimeError("queue acquisition failed")
        owned = original_queue(context, name, **kwargs)
        created.append(owned)
        return owned

    def close(owned: Queue) -> None:
        original_close(owned)
        closed.append(owned.name)
        if failure == "queue-close" and owned is created[-1]:
            raise RuntimeError("queue close failed")

    def monitor(queues: list[Queue], **kwargs: Any) -> Any:
        if failure == "monitor-init":
            raise RuntimeError("monitor acquisition failed")
        owned = original_monitor(queues, **kwargs)
        original_monitor_close = owned.close

        def close_monitor() -> None:
            original_monitor_close()
            closed.append("monitor")
            if failure == "monitor-close":
                raise RuntimeError("monitor close failed")

        monkeypatch.setattr(owned, "close", close_monitor)
        return owned

    with monkeypatch.context() as patch:
        patch.setattr(context_type, "queue", queue)
        patch.setattr(Queue, "close", close)
        patch.setattr(submission, "QueueChangeMonitor", monitor)
        specs = (("spawn-observer.a", True), ("spawn-observer.b", True))
        expectation = (
            nullcontext()
            if failure == "none"
            else pytest.raises(RuntimeError, match="failed")
        )
        with expectation, submission._open_spawn_reconciliation_monitor(ctx, specs):
            pass
    expected = [owned.name for owned in reversed(created)]
    if failure not in {"queue-init", "monitor-init"}:
        expected.insert(0, "monitor")
    assert closed == expected


def test_spawn_observer_rebind_failure_releases_old_new_and_owner_queues(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed replacement leaves neither generation nor the owner leased."""
    ctx = weft_harness.context
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as seed:
        tid = str(seed.generate_timestamp())
    specs = iter([(("spawn-observer.first", True),), (("spawn-observer.next", True),)])
    created: list[Queue] = []
    closed: list[Queue] = []
    monitor_closes: list[str] = []
    original_queue = type(ctx).queue
    original_close = Queue.close
    original_monitor = submission.QueueChangeMonitor
    generations = 0

    def queue(context: Any, name: str, **kwargs: Any) -> Queue:
        owned = original_queue(context, name, **kwargs)
        created.append(owned)
        return owned

    def close(owned: Queue) -> None:
        original_close(owned)
        closed.append(owned)

    def monitor(queues: list[Queue], **kwargs: Any) -> Any:
        nonlocal generations
        generations += 1
        if generations == 2:
            assert monitor_closes == ["first"]
            raise RuntimeError("replacement failed")
        owned = original_monitor(queues, **kwargs)
        original_monitor_close = owned.close

        def close_monitor() -> None:
            original_monitor_close()
            monitor_closes.append("first")

        monkeypatch.setattr(owned, "close", close_monitor)
        return owned

    with monkeypatch.context() as patch:
        patch.setattr(type(ctx), "queue", queue)
        patch.setattr(Queue, "close", close)
        patch.setattr(submission, "QueueChangeMonitor", monitor)
        patch.setattr(
            submission,
            "_spawn_reconciliation_queue_specs",
            lambda *args, **kwargs: next(specs),
        )
        with pytest.raises(RuntimeError, match="replacement failed"):
            submission.reconcile_submitted_spawn(ctx, tid, timeout=10)
    assert generations == 2
    assert len(created) == 3
    assert closed == [created[1], created[2], created[0]]


def test_spawn_reconciliation_does_not_replay_inspected_history(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller advances its inspected range and still sees later rejection.

    Abnormal reconciliation deliberately watches global history. Its broker
    fallback watcher may read that queue on another thread; the retained cursor
    contract belongs to the reconciliation caller, not the broker watcher.
    """
    ctx = weft_harness.context
    caller_thread = get_ident()
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        tid = str(log.generate_timestamp())
        first = log.write(json.dumps({"tid": str(int(tid) + 1), "status": "running"}))
        with log.get_connection() as db:
            broker_type = type(db)
        original = broker_type.peek_generator
        reads: list[tuple[int | None, int | None]] = []

        def peek(db: Any, name: str, **kwargs: Any) -> Any:
            if name == WEFT_GLOBAL_LOG_QUEUE and get_ident() == caller_thread:
                reads.append(
                    (kwargs.get("after_timestamp"), kwargs.get("before_timestamp"))
                )
            return original(db, name, **kwargs)

        monkeypatch.setattr(broker_type, "peek_generator", peek)
        turns = 0
        second = 0

        def wait(_monitor: Any, _timeout: float) -> bool:
            nonlocal turns, second
            turns += 1
            assert turns <= 2
            if turns == 1:
                second = log.write(
                    json.dumps({"tid": str(int(tid) + 2), "status": "running"})
                )
            else:
                log.write(
                    json.dumps(
                        {
                            "tid": str(int(tid) + 3),
                            "child_tid": tid,
                            "event": "task_spawn_rejected",
                            "error": "rejected later",
                        }
                    )
                )
            return True

        monkeypatch.setattr(submission.QueueChangeMonitor, "wait", wait)
        result = submission.reconcile_submitted_spawn(ctx, tid, timeout=10)

    assert result.outcome == "rejected"
    assert result.error == "rejected later"
    assert turns == 2
    assert [after for after, _before in reads] == [int(tid) - 1, first, second]
    assert all(before is not None for _after, before in reads)


def test_spawn_log_open_failure_is_not_negative_evidence(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real broker reader fault must escape, then recovery finds rejection."""
    ctx = weft_harness.context
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        tid = str(log.generate_timestamp())
        log.write(
            json.dumps(
                {
                    "child_tid": tid,
                    "event": "task_spawn_rejected",
                    "error": "durable rejection",
                }
            )
        )
        with log.get_connection() as db:
            original = type(db).peek_generator
            attempts = 0

            def peek(broker: Any, name: str, **kwargs: Any) -> Any:
                nonlocal attempts
                if name == WEFT_GLOBAL_LOG_QUEUE:
                    attempts += 1
                    if attempts == 1:
                        raise RuntimeError("broker reader unavailable")
                return original(broker, name, **kwargs)

            monkeypatch.setattr(type(db), "peek_generator", peek)
            with pytest.raises(RuntimeError, match="broker reader unavailable"):
                submission._inspect_task_log_for_tid(ctx, tid, broker=db)
            result = submission._inspect_task_log_for_tid(ctx, tid, broker=db)
    assert result is not None
    assert result.outcome == "rejected"
    assert result.error == "durable rejection"


def test_spawn_log_partial_read_keeps_prior_horizon(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed suffix read neither forgets old progress nor skips new proof."""
    ctx = weft_harness.context
    observation = submission._SpawnLogObservation()
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        tid = str(log.generate_timestamp())
        first = log.write(json.dumps({"tid": str(int(tid) + 1), "status": "running"}))
        with log.get_connection() as db:
            assert (
                submission._inspect_task_log_for_tid(
                    ctx, tid, broker=db, observation=observation
                )
                is None
            )
            assert observation.inspected_through == first
            log.write(
                json.dumps(
                    {
                        "child_tid": tid,
                        "event": "task_spawn_rejected",
                        "error": "retry this range",
                    }
                )
            )
            horizon = log.write(
                json.dumps({"tid": str(int(tid) + 2), "status": "running"})
            )
            original = type(db).peek_generator

            def broken(broker: Any, name: str, **kwargs: Any) -> Any:
                entries = original(broker, name, **kwargs)
                if name != WEFT_GLOBAL_LOG_QUEUE:
                    return entries

                def partial() -> Any:
                    with closing_queue_iterator(entries) as rows:
                        yield next(iter(rows))
                        raise RuntimeError("read interrupted after first row")

                return partial()

            with monkeypatch.context() as patch:
                patch.setattr(type(db), "peek_generator", broken)
                with pytest.raises(RuntimeError, match="read interrupted"):
                    submission._inspect_task_log_for_tid(
                        ctx, tid, broker=db, observation=observation
                    )
            assert observation.inspected_through == first
            assert observation.latest_child_event is None
            result = submission._inspect_task_log_for_tid(
                ctx, tid, broker=db, observation=observation
            )
            assert result is not None
            assert result.outcome == "rejected"
            assert result.error == "retry this range"
            assert observation.inspected_through == horizon
