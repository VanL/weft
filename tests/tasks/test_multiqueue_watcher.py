"""Tests for the enhanced MultiQueueWatcher with queue modes."""

from __future__ import annotations

from weft.core.tasks.multiqueue_watcher import (
    MultiQueueWatcher,
    QueueMessageContext,
    QueueMode,
)


def run_single_drain(watcher: MultiQueueWatcher) -> None:
    """Helper to run a single drain cycle for deterministic tests."""
    watcher._drain_queue()


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
