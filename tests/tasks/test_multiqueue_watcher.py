"""Tests for the enhanced MultiQueueWatcher with queue modes."""

from __future__ import annotations

import threading
import time

from weft.core.tasks.multiqueue_watcher import (
    MultiQueueWatcher,
    QueueMessageContext,
    QueueMode,
)


def run_single_drain(watcher: MultiQueueWatcher) -> None:
    """Helper to run a single drain cycle for deterministic tests."""
    watcher._drain_queue()


class FakeWaiter:
    def __init__(self, *, raises: bool = False) -> None:
        self.wait_calls: list[float | None] = []
        self.close_calls = 0
        self.raises = raises

    def wait(self, timeout: float | None) -> bool:
        self.wait_calls.append(timeout)
        if self.raises:
            raise RuntimeError("waiter failed")
        return False

    def close(self) -> None:
        self.close_calls += 1


def test_peek_mode_ack_removes_message(broker_env) -> None:
    """Control queue (peek) handlers should see message and remove only when acked."""
    db_path, make_queue = broker_env
    queue_name = "T123.ctrl_in"
    queue = make_queue(queue_name)
    queue.write("STOP")

    seen: list[tuple[str, QueueMode]] = []

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        seen.append((message, context.mode))
        context.queue.delete(message_id=context.timestamp)

    watcher = MultiQueueWatcher(
        queue_configs={
            queue_name: {
                "handler": handler,
                "mode": QueueMode.PEEK,
            },
        },
        db=db_path,
    )

    run_single_drain(watcher)

    assert seen == [("STOP", QueueMode.PEEK)]
    assert queue.peek_one() is None


def test_wait_for_activity_uses_simplebroker_multi_queue_waiter(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    received: dict[str, object] = {}
    fake_waiter = FakeWaiter()
    stop_event = threading.Event()

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        raise AssertionError("wait_for_activity must not drain messages")

    def fake_create(queues, *, stop_event):
        received["queues"] = queues
        received["stop_event"] = stop_event
        return fake_waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        fake_create,
    )

    watcher = MultiQueueWatcher(
        queue_configs={
            "wait.one": {"handler": handler},
            "wait.two": {"handler": handler},
        },
        db=db_path,
        stop_event=stop_event,
    )

    try:
        watcher.wait_for_activity(timeout=0.25)
    finally:
        watcher.stop(join=False)

    assert [queue.name for queue in received["queues"]] == ["wait.one", "wait.two"]
    assert received["stop_event"] is stop_event
    assert fake_waiter.wait_calls == [0.25]


def test_wait_for_activity_skips_waiter_when_work_is_pending(
    broker_env,
    monkeypatch,
) -> None:
    db_path, make_queue = broker_env
    queue = make_queue("pending.one")
    queue.write("ready")
    create_calls = 0
    fake_waiter = FakeWaiter()

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        raise AssertionError("wait_for_activity must not drain messages")

    def fake_create(queues, *, stop_event):
        nonlocal create_calls
        del queues, stop_event
        create_calls += 1
        return fake_waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        fake_create,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"pending.one": {"handler": handler}},
        db=db_path,
    )

    try:
        watcher.wait_for_activity(timeout=0.25)
    finally:
        watcher.stop(join=False)

    assert create_calls == 1
    assert fake_waiter.wait_calls == []
    assert queue.peek_one() == "ready"


def test_wake_precheck_forces_inactive_queue_probe(broker_env) -> None:
    db_path, make_queue = broker_env
    queue = make_queue("precheck.two")
    queue.write("ready")
    seen: list[str] = []

    def handler(
        message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        seen.append(message)

    watcher = MultiQueueWatcher(
        queue_configs={
            "precheck.one": {"handler": handler},
            "precheck.two": {"handler": handler},
        },
        db=db_path,
        check_interval=10,
    )

    try:
        watcher._check_counter = 1
        watcher._pending_messages_precheck_confirmed = True
        watcher._drain_queue()
    finally:
        watcher.stop(join=False)

    assert seen == ["ready"]


def test_wait_for_activity_falls_back_when_helper_returns_none(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: None,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"fallback.one": {"handler": handler}},
        db=db_path,
    )

    try:
        start = time.monotonic()
        watcher.wait_for_activity(timeout=0.01)
    finally:
        watcher.stop(join=False)

    assert time.monotonic() - start >= 0


def test_queue_set_changes_close_stale_multi_queue_waiter(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    waiters: list[FakeWaiter] = []

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    def fake_create(queues, *, stop_event):
        del queues, stop_event
        waiter = FakeWaiter()
        waiters.append(waiter)
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        fake_create,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"dynamic.one": {"handler": handler}},
        db=db_path,
    )

    try:
        watcher.wait_for_activity(timeout=0.01)
        watcher.add_queue("dynamic.two", handler)
        watcher.wait_for_activity(timeout=0.01)
    finally:
        watcher.stop(join=False)

    assert len(waiters) == 2
    assert waiters[0].close_calls == 1
    assert waiters[1].close_calls == 1


def test_stop_closes_multi_queue_waiter(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    fake_waiter = FakeWaiter()

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"stop.one": {"handler": handler}},
        db=db_path,
    )

    watcher.wait_for_activity(timeout=0.01)
    watcher.stop(join=False)

    assert fake_waiter.close_calls == 1


def test_wait_for_activity_falls_back_when_waiter_raises(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    fake_waiter = FakeWaiter(raises=True)

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"raise.one": {"handler": handler}},
        db=db_path,
    )

    try:
        watcher.wait_for_activity(timeout=0.01)
    finally:
        watcher.stop(join=False)

    assert fake_waiter.wait_calls == [0.01]
    assert fake_waiter.close_calls == 1


def test_background_watcher_path_uses_multi_queue_waiter(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    fake_waiter = FakeWaiter()

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={
            "thread.one": {"handler": handler},
            "thread.two": {"handler": handler},
        },
        db=db_path,
    )

    thread = watcher.run_in_thread()
    try:
        deadline = time.monotonic() + 1.0
        while not fake_waiter.wait_calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fake_waiter.wait_calls
    finally:
        watcher.stop(join=True)
        thread.join(timeout=1.0)


def test_peek_mode_without_ack_leaves_message(broker_env) -> None:
    """Peek mode should leave messages available if handler skips ack."""
    db_path, make_queue = broker_env
    queue_name = "T124.ctrl_in"
    queue = make_queue(queue_name)
    queue.write("PAUSE")

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        # Intentionally do not ack
        assert context.mode == QueueMode.PEEK

    watcher = MultiQueueWatcher(
        queue_configs={
            queue_name: {
                "handler": handler,
                "mode": QueueMode.PEEK,
            },
        },
        db=db_path,
    )

    run_single_drain(watcher)

    # Message should still be present because handler didn't ack
    peeked = queue.peek_one(with_timestamps=True)
    assert peeked is not None
    assert peeked[0] == "PAUSE"


def test_reserve_mode_moves_then_ack_clears_reserved(broker_env) -> None:
    """Reserve mode should move messages before handler and allow ack from reserved queue."""
    db_path, make_queue = broker_env
    inbox_name = "T125.inbox"
    reserved_name = "T125.reserved"
    inbox = make_queue(inbox_name)
    reserved = make_queue(reserved_name)
    inbox.write("job-1")

    seen: list[tuple[str, QueueMode]] = []

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        seen.append((message, context.mode))
        # Message should have been moved to the reserved queue
        reserved_peek = reserved.peek_one(with_timestamps=True)
        assert reserved_peek is not None
        assert reserved_peek[0] == message
        assert reserved_peek[1] == timestamp
        reserved.delete(message_id=timestamp)

    watcher = MultiQueueWatcher(
        queue_configs={
            inbox_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": reserved_name,
            }
        },
        db=db_path,
    )

    run_single_drain(watcher)

    assert seen == [("job-1", QueueMode.RESERVE)]
    # Inbox should be empty after move
    assert inbox.peek_one() is None
    # Reserved queue should also be empty after handler acked the message
    assert reserved.peek_one() is None


def test_read_mode_consumes_message_without_ack(broker_env) -> None:
    """Read mode should consume messages immediately, ack becomes a no-op."""
    db_path, make_queue = broker_env
    queue_name = "T126.custom"
    queue = make_queue(queue_name)
    queue.write("payload")

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        assert context.mode == QueueMode.READ
        # Even without ack, message should already be gone

    watcher = MultiQueueWatcher(
        queue_configs={
            queue_name: {
                "handler": handler,
                "mode": QueueMode.READ,
            },
        },
        db=db_path,
    )

    run_single_drain(watcher)

    assert queue.peek_one() is None
