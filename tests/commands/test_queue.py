"""Unit tests for queue command helpers."""

from __future__ import annotations

from weft.commands import queue as queue_cmd
from weft.context import build_context


def test_read_and_write_messages(tmp_path):
    ctx = build_context(spec_context=tmp_path)
    queue_cmd.write_message(ctx, "unit.queue", "hello")

    messages = queue_cmd.read_messages(ctx, "unit.queue")
    assert [m.body for m in messages] == ["hello"]


def test_peek_messages_preserves_queue(tmp_path):
    ctx = build_context(spec_context=tmp_path)
    queue_cmd.write_message(ctx, "peek.queue", "foo")

    first = queue_cmd.peek_messages(ctx, "peek.queue")
    assert [m.body for m in first] == ["foo"]

    second = queue_cmd.read_messages(ctx, "peek.queue")
    assert [m.body for m in second] == ["foo"]


def test_move_messages(tmp_path):
    ctx = build_context(spec_context=tmp_path)
    queue_cmd.write_message(ctx, "from.queue", "a")
    queue_cmd.write_message(ctx, "from.queue", "b")

    moved = queue_cmd.move_messages(ctx, "from.queue", "to.queue")
    assert moved == 2

    dest_messages = queue_cmd.read_messages(ctx, "to.queue", all_messages=True)
    assert [m.body for m in dest_messages] == ["a", "b"]


def test_list_queues(tmp_path):
    ctx = build_context(spec_context=tmp_path)
    queue_cmd.write_message(ctx, "list.queue", "item")

    queues = queue_cmd.list_queues(ctx)
    names = {info.name for info in queues}
    assert "list.queue" in names


def test_watch_queue(tmp_path):
    ctx = build_context(spec_context=tmp_path)
    queue_cmd.write_message(ctx, "watch.queue", "payload")

    iterator = queue_cmd.watch_queue(
        ctx, "watch.queue", interval=0.01, max_messages=1, with_timestamps=True
    )
    messages = list(iterator)
    assert len(messages) == 1
    assert messages[0].body == "payload"
