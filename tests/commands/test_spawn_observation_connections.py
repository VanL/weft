"""Submission observers retain connection ownership (Spec: [SB-0.4], [MF-6])."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands import _spawn_submission as submission
from weft.core.task_state import task_state_queue_name
from weft.helpers import tid_short_form

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
