"""Tests for the enhanced MultiQueueWatcher with queue modes."""

from __future__ import annotations

import threading
import time

import pytest

from weft._constants import QUEUE_PRIORITY_INTERNAL, QUEUE_PRIORITY_NORMAL
from weft.core.tasks.multiqueue_watcher import (
    MultiQueueWatcher,
    QueueMessageContext,
    QueueMode,
)


def run_single_drain(watcher: MultiQueueWatcher) -> None:
    """Helper to run a single drain cycle for deterministic tests."""
    watcher._drain_queue()


class FakeWaiter:
    def __init__(self, *, raises: bool = False, result: bool = False) -> None:
        self.wait_calls: list[float | None] = []
        self.wait_entered = threading.Event()
        self.close_calls = 0
        self.raises = raises
        self.result = result

    def wait(self, timeout: float | None) -> bool:
        self.wait_calls.append(timeout)
        self.wait_entered.set()
        if self.raises:
            raise RuntimeError("waiter failed")
        return self.result

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


def test_wait_for_activity_positive_timeout_uses_waiter_without_precheck(
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
    assert fake_waiter.wait_calls == [0.25]
    assert queue.peek_one() == "ready"


def test_wait_for_activity_zero_timeout_does_not_probe_queues(
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

    watcher = MultiQueueWatcher(
        queue_configs={"zero-timeout.one": {"handler": handler}},
        db=db_path,
    )
    monkeypatch.setattr(
        watcher,
        "_has_pending_messages",
        lambda: pytest.fail("zero-timeout waits must not poll watched queues"),
    )

    try:
        watcher.wait_for_activity(timeout=0)
    finally:
        watcher.stop(join=False)


def test_native_waiter_activity_forces_inactive_queue_probe(
    broker_env,
    monkeypatch,
) -> None:
    db_path, make_queue = broker_env
    queue = make_queue("native-precheck.two")
    queue.write("ready")
    seen: list[str] = []
    fake_waiter = FakeWaiter(result=True)

    def handler(
        message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        seen.append(message)

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={
            "native-precheck.one": {"handler": handler},
            "native-precheck.two": {"handler": handler},
        },
        db=db_path,
        check_interval=10,
    )

    try:
        watcher._next_inactive_probe_at = time.monotonic() + 60
        watcher._check_counter = 1
        watcher.wait_for_activity(timeout=0.25)
        watcher._drain_queue()
    finally:
        watcher.stop(join=False)

    assert seen == ["ready"]
    assert fake_waiter.wait_calls == [0.25]


def test_native_waiter_timeout_does_not_probe_inactive_queues_before_deadline(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    fake_waiter = FakeWaiter(result=False)

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
        queue_configs={"native-timeout.one": {"handler": handler}},
        db=db_path,
        check_interval=10,
    )
    monkeypatch.setattr(
        watcher,
        "_queue_has_pending",
        lambda _queue: pytest.fail(
            "native waiter timeout must not become an inactive queue probe"
        ),
    )

    try:
        watcher._next_inactive_probe_at = time.monotonic() + 60
        watcher._check_counter = 1
        watcher.wait_for_activity(timeout=0.01)
        watcher._drain_queue()
    finally:
        watcher.stop(join=False)

    assert fake_waiter.wait_calls == [0.01]


def test_inactive_queue_discovery_is_time_bounded(
    broker_env,
) -> None:
    db_path, make_queue = broker_env
    queue = make_queue("periodic-discovery.two")
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
            "periodic-discovery.one": {"handler": handler},
            "periodic-discovery.two": {"handler": handler},
        },
        db=db_path,
        check_interval=10,
    )

    try:
        watcher._check_counter = 1
        watcher._next_inactive_probe_at = time.monotonic() + 60
        watcher._drain_queue()
        assert seen == []

        watcher._next_inactive_probe_at = time.monotonic() - 1
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
        assert fake_waiter.wait_entered.wait(timeout=5.0)
        assert fake_waiter.wait_calls
    finally:
        watcher.stop(join=True)
        thread.join(timeout=1.0)


def test_priority_queue_drains_before_lower_priority(broker_env) -> None:
    db_path, make_queue = broker_env
    internal_name = "priority.internal"
    public_name = "priority.public"
    internal_reserved_name = "priority.internal.reserved"
    public_reserved_name = "priority.public.reserved"
    internal = make_queue(internal_name)
    public = make_queue(public_name)
    internal_reserved = make_queue(internal_reserved_name)
    public_reserved = make_queue(public_reserved_name)

    for body in ["internal-1", "internal-2", "internal-3"]:
        internal.write(body)
    for body in ["public-1", "public-2"]:
        public.write(body)

    seen: list[tuple[str, str]] = []

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        seen.append((context.queue_name, message))
        assert context.reserved_queue_name is not None
        make_queue(context.reserved_queue_name).delete(message_id=timestamp)

    watcher = MultiQueueWatcher(
        queue_configs={
            internal_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": internal_reserved_name,
                "priority": QUEUE_PRIORITY_INTERNAL,
            },
            public_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": public_reserved_name,
                "priority": QUEUE_PRIORITY_NORMAL,
            },
        },
        db=db_path,
    )

    try:
        run_single_drain(watcher)
    finally:
        watcher.stop(join=False)

    assert seen[:3] == [
        (internal_name, "internal-1"),
        (internal_name, "internal-2"),
        (internal_name, "internal-3"),
    ]
    assert seen[3:] == [(public_name, "public-1")]
    assert internal.peek_one() is None
    assert internal_reserved.peek_one() is None
    assert public_reserved.peek_one() is None
    assert public.peek_one() == "public-2"


def test_equal_priority_preserves_existing_round_robin_behavior(broker_env) -> None:
    db_path, make_queue = broker_env
    first_name = "priority.equal.first"
    second_name = "priority.equal.second"
    first_reserved_name = "priority.equal.first.reserved"
    second_reserved_name = "priority.equal.second.reserved"
    make_queue(first_name).write("first-1")
    make_queue(first_name).write("first-2")
    make_queue(second_name).write("second-1")
    make_queue(second_name).write("second-2")

    seen: list[tuple[str, str]] = []

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        seen.append((context.queue_name, message))
        assert context.reserved_queue_name is not None
        make_queue(context.reserved_queue_name).delete(message_id=timestamp)

    watcher = MultiQueueWatcher(
        queue_configs={
            first_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": first_reserved_name,
            },
            second_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": second_reserved_name,
            },
        },
        db=db_path,
    )

    try:
        run_single_drain(watcher)
    finally:
        watcher.stop(join=False)

    assert seen == [
        (first_name, "first-1"),
        (second_name, "second-1"),
    ]
    assert make_queue(first_name).peek_one() == "first-2"
    assert make_queue(second_name).peek_one() == "second-2"


def test_priority_drain_stops_when_high_priority_queue_is_empty(broker_env) -> None:
    db_path, make_queue = broker_env
    internal_name = "priority.empty.internal"
    public_name = "priority.empty.public"
    internal_reserved_name = "priority.empty.internal.reserved"
    public_reserved_name = "priority.empty.public.reserved"
    public = make_queue(public_name)
    public.write("public-1")
    public.write("public-2")

    seen: list[tuple[str, str]] = []

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        seen.append((context.queue_name, message))
        assert context.reserved_queue_name is not None
        make_queue(context.reserved_queue_name).delete(message_id=timestamp)

    watcher = MultiQueueWatcher(
        queue_configs={
            internal_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": internal_reserved_name,
                "priority": QUEUE_PRIORITY_INTERNAL,
            },
            public_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": public_reserved_name,
                "priority": QUEUE_PRIORITY_NORMAL,
            },
        },
        db=db_path,
    )

    try:
        run_single_drain(watcher)
    finally:
        watcher.stop(join=False)

    assert seen == [(public_name, "public-1")]
    assert public.peek_one() == "public-2"


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
