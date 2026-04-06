"""CLI tests for queue passthrough commands."""

from __future__ import annotations

import json

import pytest

from tests.conftest import run_cli
from weft.context import build_context

pytestmark = [pytest.mark.shared]


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


def test_queue_write_reads_implicit_stdin(workdir):
    build_context(spec_context=workdir)

    rc, _, err = run_cli(
        "queue",
        "write",
        "stdin.queue",
        cwd=workdir,
        stdin="line1\nline2",
    )
    assert rc == 0
    assert err == ""

    rc, out, err = run_cli("queue", "read", "stdin.queue", cwd=workdir)
    assert rc == 0
    assert [line for line in out.splitlines() if line] == ["line1", "line2"]
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
    lines = [json.loads(line) for line in out.splitlines() if line]
    assert lines[0]["message"] == "data"
    assert isinstance(lines[0]["timestamp"], int)
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
        "--all",
        cwd=workdir,
    )
    assert rc == 0
    assert "first" in out and "second" in out
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
    assert "Watching queue 'watch.queue'" in err


def test_queue_broadcast_reads_implicit_stdin_with_pattern(workdir):
    build_context(spec_context=workdir)

    assert run_cli("queue", "write", "broadcast.alpha", "seed-a", cwd=workdir)[0] == 0
    assert run_cli("queue", "write", "broadcast.beta", "seed-b", cwd=workdir)[0] == 0
    assert run_cli("queue", "write", "other.queue", "seed-other", cwd=workdir)[0] == 0

    rc, out, err = run_cli(
        "queue",
        "broadcast",
        "--pattern",
        "broadcast.*",
        cwd=workdir,
        stdin="broadcast-body",
    )
    assert rc == 0
    assert out == ""
    assert err == ""

    rc, out, err = run_cli("queue", "read", "broadcast.alpha", "--all", cwd=workdir)
    assert rc == 0
    assert out.splitlines() == ["seed-a", "broadcast-body"]
    assert err == ""

    rc, out, err = run_cli("queue", "read", "broadcast.beta", "--all", cwd=workdir)
    assert rc == 0
    assert out.splitlines() == ["seed-b", "broadcast-body"]
    assert err == ""

    rc, out, err = run_cli("queue", "read", "other.queue", "--all", cwd=workdir)
    assert rc == 0
    assert out.splitlines() == ["seed-other"]
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
    timestamp, message = out.split("\t", 1)
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


def test_queue_list_pattern(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "alpha.queue", "item", cwd=workdir)[0] == 0
    assert run_cli("queue", "write", "beta.queue", "item", cwd=workdir)[0] == 0

    rc, out, err = run_cli(
        "queue",
        "list",
        "--pattern",
        "alpha.*",
        cwd=workdir,
    )
    assert rc == 0
    assert "alpha.queue" in out
    assert "beta.queue" not in out
    assert err == ""

    rc, out, err = run_cli(
        "queue",
        "list",
        "--json",
        "--pattern",
        "beta.*",
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""
    data = json.loads(out or "[]")
    assert all(entry["queue"].startswith("beta") for entry in data)


def test_queue_list_json_stats_includes_totals(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "stat.queue", "item", cwd=workdir)[0] == 0

    rc, out, err = run_cli(
        "queue",
        "list",
        "--json",
        "--stats",
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""
    data = json.loads(out or "[]")
    stat_entry = next(entry for entry in data if entry["queue"] == "stat.queue")
    assert "total_messages" in stat_entry
    assert "claimed_messages" in stat_entry
