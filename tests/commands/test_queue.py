"""Unit tests for queue command helpers."""

from __future__ import annotations

import io
import json
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import WEFT_ENDPOINTS_REGISTRY_QUEUE
from weft.commands import queue as queue_cmd
from weft.context import build_context
from weft.core.endpoints import build_endpoint_record_payload
from weft.core.tasks import Consumer
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared]


class _FakeQueueChangeMonitor:
    def __init__(self, queues, *, config=None) -> None:
        del config
        self.queue_names = [queue.name for queue in queues]
        self.wait_calls: list[float | None] = []

    def wait(self, timeout: float | None) -> bool:
        self.wait_calls.append(timeout)
        return False

    def close(self) -> None:
        return


class _FakeWatchQueue:
    def __init__(self, name: str, batches: list[list[tuple[str, int]]]) -> None:
        self.name = name
        self._batches = list(batches)
        self.closed = False

    def read_generator(
        self,
        *,
        with_timestamps: bool,
        since_timestamp: int | None = None,
    ):
        del since_timestamp
        batch = self._batches.pop(0) if self._batches else []
        if with_timestamps:
            return iter(batch)
        return iter([body for body, _timestamp in batch])

    def peek_generator(
        self,
        *,
        with_timestamps: bool,
        since_timestamp: int | None = None,
    ):
        return self.read_generator(
            with_timestamps=with_timestamps,
            since_timestamp=since_timestamp,
        )

    def move_generator(
        self,
        _move_to: str,
        *,
        with_timestamps: bool,
        since_timestamp: int | None = None,
    ):
        return self.read_generator(
            with_timestamps=with_timestamps,
            since_timestamp=since_timestamp,
        )

    def close(self) -> None:
        self.closed = True


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


def test_watch_queue_uses_queue_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_queue = _FakeWatchQueue("watch.queue", [[], [("payload", 5)]])
    monitor_queue = _FakeWatchQueue("watch.queue", [])
    created_monitors: list[_FakeQueueChangeMonitor] = []

    class _FakeContext:
        config: dict[str, Any] = {}

        def __init__(self) -> None:
            self._queues = [data_queue, monitor_queue]

        def queue(self, _name: str, *, persistent: bool = True):
            del persistent
            return self._queues.pop(0)

    def _fake_monitor(queues, *, config=None):
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(queue_cmd, "QueueChangeMonitor", _fake_monitor)

    messages = list(
        queue_cmd.watch_queue(
            _FakeContext(),
            "watch.queue",
            interval=0.25,
            max_messages=1,
            with_timestamps=True,
        )
    )

    assert [message.body for message in messages] == ["payload"]
    assert len(created_monitors) == 1
    assert created_monitors[0].queue_names == ["watch.queue"]
    assert created_monitors[0].wait_calls == [0.25]
    assert data_queue.closed
    assert monitor_queue.closed


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


def test_resolve_command_returns_registered_endpoint_details(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
        weft_context=str(root),
    )
    task = Consumer(ctx.broker_target, spec, config=ctx.config)

    try:
        task.register_endpoint_name("mayor", metadata={"role": "operator-facing"})

        exit_code, stdout, stderr = queue_cmd.resolve_command(
            "mayor",
            json_output=True,
            spec_context=str(root),
        )

        assert exit_code == 0
        assert stderr == ""
        payload = json.loads(stdout)
        assert payload["name"] == "mayor"
        assert payload["tid"] == tid
        assert payload["inbox"] == spec.io.inputs["inbox"]
        assert payload["live_candidates"] == 1
    finally:
        task.cleanup()


def test_list_command_endpoints_uses_lowest_live_tid_as_canonical(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    low_tid = str(time.time_ns())
    high_tid = str(int(low_tid) + 1)
    low_task = Consumer(
        ctx.broker_target,
        make_function_taskspec(
            low_tid,
            "tests.tasks.sample_targets:echo_payload",
            weft_context=str(root),
        ),
        config=ctx.config,
    )
    high_task = Consumer(
        ctx.broker_target,
        make_function_taskspec(
            high_tid,
            "tests.tasks.sample_targets:echo_payload",
            weft_context=str(root),
        ),
        config=ctx.config,
    )

    try:
        low_task.register_endpoint_name("mayor")
        high_task.register_endpoint_name("mayor")

        exit_code, stdout, stderr = queue_cmd.list_command(
            json_output=True,
            endpoints=True,
            spec_context=str(root),
        )

        assert exit_code == 0
        assert stderr == ""
        payload = json.loads(stdout)
        assert len(payload) == 1
        entry = payload[0]
        assert entry["name"] == "mayor"
        assert entry["tid"] == low_tid
        assert entry["status"] == "active"
        assert entry["inbox"] == f"T{low_tid}.inbox"
        assert entry["outbox"] == f"T{low_tid}.outbox"
        assert entry["ctrl_in"] == f"T{low_tid}.ctrl_in"
        assert entry["ctrl_out"] == f"T{low_tid}.ctrl_out"
        assert isinstance(entry["registered_at"], int)
        assert isinstance(entry["last_seen"], int)
        assert entry["metadata"] == {}
        assert entry["live_candidates"] == 2
    finally:
        high_task.cleanup()
        low_task.cleanup()


def test_resolve_command_prunes_stale_endpoint_records(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)
    try:
        registry.write(
            json.dumps(
                build_endpoint_record_payload(
                    name="ghost",
                    tid="1775630560447778816",
                    inbox="T1775630560447778816.inbox",
                    outbox="T1775630560447778816.outbox",
                    ctrl_in="T1775630560447778816.ctrl_in",
                    ctrl_out="T1775630560447778816.ctrl_out",
                )
            )
        )

        exit_code, stdout, stderr = queue_cmd.resolve_command(
            "ghost",
            spec_context=str(root),
        )

        assert exit_code == 2
        assert stdout == ""
        assert "No active endpoint named 'ghost'" in stderr
        assert list(iter_queue_json_entries(registry)) == []
    finally:
        registry.close()
