"""CLI tests for queue passthrough commands."""

from __future__ import annotations

import json

from tests.conftest import run_cli
from weft.context import build_context


def test_queue_write_and_read(workdir):
    build_context(spec_context=workdir)

    rc, out, err = run_cli(
        "queue",
        "write",
        "cli.queue",
        "hello",
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""

    rc, out, err = run_cli("queue", "read", "cli.queue", cwd=workdir)
    assert rc == 0
    assert "hello" in out
    assert err == ""


def test_queue_read_json(workdir):
    build_context(spec_context=workdir)

    rc, _, err = run_cli("queue", "write", "json.queue", "data", cwd=workdir)
    assert rc == 0
    assert err == ""

    rc, out, err = run_cli(
        "queue",
        "read",
        "json.queue",
        "--json",
        cwd=workdir,
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload == [{"message": "data", "timestamp": None}]
    assert err == ""


def test_queue_peek_preserves_message(workdir):
    build_context(spec_context=workdir)

    rc, _, err = run_cli("queue", "write", "peek.queue", "value", cwd=workdir)
    assert rc == 0
    assert err == ""

    rc, out, err = run_cli("queue", "peek", "peek.queue", cwd=workdir)
    assert rc == 0
    assert "value" in out
    assert err == ""

    rc, out, err = run_cli("queue", "read", "peek.queue", cwd=workdir)
    assert rc == 0
    assert "value" in out
    assert err == ""


def test_queue_move(workdir):
    build_context(spec_context=workdir)

    assert run_cli("queue", "write", "from.queue", "first", cwd=workdir)[0] == 0
    assert run_cli("queue", "write", "from.queue", "second", cwd=workdir)[0] == 0

    rc, out, err = run_cli(
        "queue",
        "move",
        "from.queue",
        "dest.queue",
        cwd=workdir,
    )
    assert rc == 0
    assert "Moved 2" in out
    assert err == ""

    rc, out, err = run_cli(
        "queue",
        "read",
        "dest.queue",
        "--all",
        cwd=workdir,
    )
    assert rc == 0
    assert "first" in out
    assert "second" in out
    assert err == ""


def test_queue_list(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "list.queue", "item", cwd=workdir)[0] == 0

    rc, out, err = run_cli("queue", "list", cwd=workdir)
    assert rc == 0
    assert "list.queue" in out
    assert err == ""


def test_queue_watch(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "watch.queue", "payload", cwd=workdir)[0] == 0

    rc, out, err = run_cli(
        "queue",
        "watch",
        "watch.queue",
        "--limit",
        "1",
        cwd=workdir,
    )
    assert rc == 0
    assert "payload" in out
    assert err == ""


def test_queue_read_with_timestamps(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "ts.queue", "payload", cwd=workdir)[0] == 0

    rc, out, err = run_cli(
        "queue",
        "read",
        "ts.queue",
        "--timestamps",
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""
    timestamp, message = out.split(" ", 1)
    assert timestamp.isdigit()
    assert message == "payload"


def test_queue_list_json(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "jsonlist.queue", "item", cwd=workdir)[0] == 0

    rc, out, err = run_cli("queue", "list", "--json", cwd=workdir)
    assert rc == 0
    assert err == ""
    data = json.loads(out or "[]")
    assert any(entry["queue"] == "jsonlist.queue" for entry in data)
