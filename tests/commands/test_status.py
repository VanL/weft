"""Tests for the ``weft status`` command helpers."""

from __future__ import annotations

import json

from weft.commands.status import collect_status, cmd_status
from weft.context import build_context


def test_collect_status_reports_message_counts(tmp_path):
    ctx = build_context(spec_context=tmp_path)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("hello")
    queue.write("there")

    snapshot = collect_status(ctx)

    assert snapshot.total_messages == 2
    assert snapshot.db_size >= 0


def test_cmd_status_text_output(tmp_path):
    ctx = build_context(spec_context=tmp_path)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("payload")

    exit_code, payload = cmd_status(spec_context=tmp_path)

    assert exit_code == 0
    assert payload is not None
    lines = payload.splitlines()
    assert lines[0].startswith("total_messages: ")

    ts_line = next(line for line in lines if line.startswith("last_timestamp: "))
    assert ts_line.endswith(")")
    assert "(" in ts_line

    size_line = next(line for line in lines if line.startswith("db_size: "))
    assert "bytes" in size_line
    assert "(" in size_line


def test_cmd_status_json_output(tmp_path):
    ctx = build_context(spec_context=tmp_path)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("payload")

    exit_code, payload = cmd_status(json_output=True, spec_context=tmp_path)

    assert exit_code == 0
    assert payload is not None
    data = json.loads(payload)
    assert data["total_messages"] >= 1
    assert "db_size" in data
