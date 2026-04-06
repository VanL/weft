"""Unit tests for queue command helpers."""

from __future__ import annotations

import io
import sys
from types import SimpleNamespace

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.commands import queue as queue_cmd
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def test_read_and_write_messages(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "unit.queue", "hello")

    messages = queue_cmd.read_messages(ctx, "unit.queue")
    assert [m.body for m in messages] == ["hello"]


def test_peek_messages_preserves_queue(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "peek.queue", "foo")

    first = queue_cmd.peek_messages(ctx, "peek.queue")
    assert [m.body for m in first] == ["foo"]

    second = queue_cmd.read_messages(ctx, "peek.queue")
    assert [m.body for m in second] == ["foo"]


def test_move_messages(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "from.queue", "a")
    queue_cmd.write_message(ctx, "from.queue", "b")

    moved = queue_cmd.move_messages(ctx, "from.queue", "to.queue")
    assert moved == 2

    dest_messages = queue_cmd.read_messages(ctx, "to.queue", all_messages=True)
    assert [m.body for m in dest_messages] == ["a", "b"]


def test_list_queues(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "list.queue", "item")

    queues = queue_cmd.list_queues(ctx)
    names = {info.name for info in queues}
    assert "list.queue" in names


def test_watch_queue(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "watch.queue", "payload")

    iterator = queue_cmd.watch_queue(
        ctx, "watch.queue", interval=0.01, max_messages=1, with_timestamps=True
    )
    messages = list(iterator)
    assert len(messages) == 1
    assert messages[0].body == "payload"


def test_write_command_reads_omitted_message_from_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ctx = SimpleNamespace(broker_target="db", config={})
    captured: dict[str, object] = {}

    def fake_run(fn, *args, **kwargs):
        captured["fn"] = fn
        captured["args"] = args
        captured["kwargs"] = kwargs
        return (0, "", "")

    monkeypatch.setattr(queue_cmd, "_context", lambda spec_context=None: fake_ctx)
    monkeypatch.setattr(queue_cmd, "_run_simplebroker_command", fake_run)
    monkeypatch.setattr(sys, "stdin", io.StringIO("line1\nline2"))

    result = queue_cmd.write_command("stdin.queue", None)

    assert result == (0, "", "")
    assert captured["fn"] is queue_cmd.sb_commands.cmd_write
    assert captured["args"] == ("db", "stdin.queue", "line1\nline2")
    assert captured["kwargs"] == {}


def test_broadcast_command_reads_omitted_message_from_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ctx = SimpleNamespace(broker_target="db", config={})
    captured: dict[str, object] = {}

    def fake_run(fn, *args, **kwargs):
        captured["fn"] = fn
        captured["args"] = args
        captured["kwargs"] = kwargs
        return (0, "", "")

    monkeypatch.setattr(queue_cmd, "_context", lambda spec_context=None: fake_ctx)
    monkeypatch.setattr(queue_cmd, "_run_simplebroker_command", fake_run)
    monkeypatch.setattr(sys, "stdin", io.StringIO("broadcast-body"))

    result = queue_cmd.broadcast_command(None, pattern="jobs.*")

    assert result == (0, "", "")
    assert captured["fn"] is queue_cmd.sb_commands.cmd_broadcast
    assert captured["args"] == ("db", "broadcast-body")
    assert captured["kwargs"] == {"pattern": "jobs.*"}
