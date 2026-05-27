"""CLI tests for queue passthrough commands."""

from __future__ import annotations

import json
import time

import pytest

from tests.conftest import run_cli
from tests.tasks.test_task_execution import make_function_taskspec
from weft.context import build_context
from weft.core.tasks import Consumer

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


def test_queue_read_after_and_before_filters(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "range.queue", "first", cwd=workdir)[0] == 0
    assert run_cli("queue", "write", "range.queue", "second", cwd=workdir)[0] == 0
    assert run_cli("queue", "write", "range.queue", "third", cwd=workdir)[0] == 0

    rc, out, err = run_cli(
        "queue",
        "peek",
        "range.queue",
        "--all",
        "--json",
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""
    rows = [json.loads(line) for line in out.splitlines() if line]
    first_ts = str(rows[0]["timestamp"])
    third_ts = str(rows[2]["timestamp"])

    rc, out, err = run_cli(
        "queue",
        "read",
        "range.queue",
        "--all",
        "--after",
        first_ts,
        "--before",
        third_ts,
        cwd=workdir,
    )
    assert rc == 0
    assert out.splitlines() == ["second"]
    assert err == ""


def test_queue_since_filter_is_not_accepted(workdir):
    build_context(spec_context=workdir)

    rc, out, err = run_cli(
        "queue",
        "read",
        "range.queue",
        "--since",
        "1",
        cwd=workdir,
    )

    assert rc != 0
    assert out == ""
    assert "No such option" in err


def test_queue_list_json(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "jsonlist.queue", "item", cwd=workdir)[0] == 0

    rc, out, err = run_cli("queue", "list", "--json", cwd=workdir)
    assert rc == 0
    assert err == ""
    data = [json.loads(line) for line in out.splitlines() if line]
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
    data = [json.loads(line) for line in out.splitlines() if line]
    assert all(entry["queue"].startswith("beta") for entry in data)


def test_queue_list_prefix(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "prefix.alpha", "item", cwd=workdir)[0] == 0
    assert run_cli("queue", "write", "other.alpha", "item", cwd=workdir)[0] == 0

    rc, out, err = run_cli(
        "queue",
        "list",
        "--prefix",
        "prefix.",
        cwd=workdir,
    )
    assert rc == 0
    assert "prefix.alpha" in out
    assert "other.alpha" not in out
    assert err == ""


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
    data = [json.loads(line) for line in out.splitlines() if line]
    stat_entry = next(entry for entry in data if entry["queue"] == "stat.queue")
    assert "total" in stat_entry
    assert "claimed" in stat_entry
    assert "pending" in stat_entry


def test_queue_exists_and_stats(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "meta.queue", "item", cwd=workdir)[0] == 0

    rc, out, err = run_cli("queue", "exists", "meta.queue", "--json", cwd=workdir)
    assert rc == 0
    assert json.loads(out) == {"queue": "meta.queue", "exists": True}
    assert err == ""

    rc, out, err = run_cli("queue", "exists", "missing.queue", "--json", cwd=workdir)
    assert rc == 2
    assert json.loads(out) == {"queue": "missing.queue", "exists": False}
    assert err == ""

    rc, out, err = run_cli("queue", "stats", "meta.queue", "--json", cwd=workdir)
    assert rc == 0
    assert json.loads(out) == {
        "queue": "meta.queue",
        "pending": 1,
        "claimed": 0,
        "total": 1,
        "exists": True,
    }
    assert err == ""


def test_queue_invalid_message_id_returns_input_error(workdir):
    build_context(spec_context=workdir)

    rc, out, err = run_cli(
        "queue",
        "peek",
        "any.queue",
        "--message",
        "123",
        "--json",
        cwd=workdir,
    )

    assert rc == 1
    assert out == ""
    payload = json.loads(err)
    assert payload["error"] == "INVALID_MESSAGE_ID"


def test_queue_delete_rejects_all_with_message_and_preserves_queues(workdir):
    build_context(spec_context=workdir)
    assert run_cli("queue", "write", "delete.one", "one", cwd=workdir)[0] == 0
    assert run_cli("queue", "write", "delete.two", "two", cwd=workdir)[0] == 0

    rc, out, err = run_cli(
        "queue",
        "peek",
        "delete.one",
        "--json",
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""
    message_id = str(json.loads(out)["timestamp"])

    rc, out, err = run_cli(
        "queue",
        "delete",
        "--all",
        "--message",
        message_id,
        cwd=workdir,
    )

    assert rc == 1
    assert out == ""
    assert "--message cannot be used with --all" in err
    assert run_cli("queue", "read", "delete.one", cwd=workdir) == (0, "one", "")
    assert run_cli("queue", "read", "delete.two", cwd=workdir) == (0, "two", "")


def test_queue_resolve_reports_named_endpoint(workdir):
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
        weft_context=str(workdir),
    )
    task = Consumer(context.broker_target, spec, config=context.config)

    try:
        task.register_endpoint_name("mayor", metadata={"role": "operator-facing"})

        rc, out, err = run_cli("queue", "resolve", "mayor", cwd=workdir)
        assert rc == 0
        assert err == ""
        assert "name: mayor" in out
        assert f"tid: {tid}" in out
        assert f"inbox: {spec.io.inputs['inbox']}" in out
    finally:
        task.cleanup()


def test_queue_write_can_target_named_endpoint(workdir):
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
        weft_context=str(workdir),
    )
    task = Consumer(context.broker_target, spec, config=context.config)

    try:
        task.register_endpoint_name("mayor")

        rc, out, err = run_cli(
            "queue",
            "write",
            "--endpoint",
            "mayor",
            "hello",
            cwd=workdir,
        )
        assert rc == 0
        assert out == ""
        assert err == ""

        rc, out, err = run_cli("queue", "read", spec.io.inputs["inbox"], cwd=workdir)
        assert rc == 0
        assert out.strip() == "hello"
        assert err == ""
    finally:
        task.cleanup()


def test_queue_list_endpoints_json_reports_canonical_owner(workdir):
    context = build_context(spec_context=workdir)
    low_tid = str(time.time_ns())
    high_tid = str(int(low_tid) + 1)
    low_task = Consumer(
        context.broker_target,
        make_function_taskspec(
            low_tid,
            "tests.tasks.sample_targets:echo_payload",
            weft_context=str(workdir),
        ),
        config=context.config,
    )
    high_task = Consumer(
        context.broker_target,
        make_function_taskspec(
            high_tid,
            "tests.tasks.sample_targets:echo_payload",
            weft_context=str(workdir),
        ),
        config=context.config,
    )

    try:
        low_task.register_endpoint_name("mayor")
        high_task.register_endpoint_name("mayor")

        rc, out, err = run_cli(
            "queue",
            "list",
            "--endpoints",
            "--json",
            cwd=workdir,
        )
        assert rc == 0
        assert err == ""
        payload = json.loads(out or "[]")
        assert len(payload) == 1
        assert payload[0]["name"] == "mayor"
        assert payload[0]["tid"] == low_tid
        assert payload[0]["live_candidates"] == 2
    finally:
        high_task.cleanup()
        low_task.cleanup()
