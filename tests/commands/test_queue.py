"""Unit tests for queue command helpers."""

from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import WEFT_ENDPOINTS_REGISTRY_QUEUE
from weft._exceptions import CommandExecutionError, CommandUsageError
from weft.commands import queue as queue_cmd
from weft.commands.types import (
    EndpointResolution,
    QueueAliasRecord,
    QueueBroadcastReceipt,
    QueueDeleteReceipt,
    QueueEntry,
    QueueInfo,
    QueueMoveResult,
    QueueWriteReceipt,
)
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
        after_timestamp: int | None = None,
        before_timestamp: int | None = None,
    ):
        del after_timestamp, before_timestamp
        batch = self._batches.pop(0) if self._batches else []
        if with_timestamps:
            return iter(batch)
        return iter([body for body, _timestamp in batch])

    def peek_generator(
        self,
        *,
        with_timestamps: bool,
        after_timestamp: int | None = None,
        before_timestamp: int | None = None,
    ):
        del after_timestamp, before_timestamp
        return self.read_generator(
            with_timestamps=with_timestamps,
        )

    def move_generator(
        self,
        _move_to: str,
        *,
        with_timestamps: bool,
        after_timestamp: int | None = None,
        before_timestamp: int | None = None,
    ):
        del after_timestamp, before_timestamp
        return self.read_generator(
            with_timestamps=with_timestamps,
        )

    def close(self) -> None:
        self.closed = True


class _ClosableIterator:
    def __init__(self, rows: list[tuple[str, int]]) -> None:
        self._rows = iter(rows)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self) -> tuple[str, int]:
        return next(self._rows)

    def close(self) -> None:
        self.closed = True


class _ClosableWatchQueue(_FakeWatchQueue):
    def __init__(self, name: str, batches: list[list[tuple[str, int]]]) -> None:
        super().__init__(name, batches)
        self.generators: list[_ClosableIterator] = []

    def read_generator(
        self,
        *,
        with_timestamps: bool,
        after_timestamp: int | None = None,
        before_timestamp: int | None = None,
    ):
        del with_timestamps, after_timestamp, before_timestamp
        batch = self._batches.pop(0) if self._batches else []
        generator = _ClosableIterator(batch)
        self.generators.append(generator)
        return generator


def test_public_queue_commands_return_structured_values(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    monkeypatch.setattr(queue_cmd, "_context", lambda: ctx)

    written = queue_cmd.cmd_queue_write("public.source", "one")
    queue_cmd.cmd_queue_write("public.source", "two")
    assert isinstance(written, QueueWriteReceipt)
    assert written == QueueWriteReceipt(queue="public.source", message="one")

    peeked = queue_cmd.cmd_queue_peek("public.source", all=True)
    assert isinstance(peeked, tuple)
    assert all(isinstance(entry, QueueEntry) for entry in peeked)
    assert [entry.message for entry in peeked] == ["one", "two"]

    moved = queue_cmd.cmd_queue_move(
        "public.source",
        "public.destination",
        all=True,
    )
    assert isinstance(moved, QueueMoveResult)
    assert [entry.message for entry in moved.entries] == ["one", "two"]
    assert moved.moved_count == 2

    read = queue_cmd.cmd_queue_read("public.destination", all=True)
    assert [entry.message for entry in read] == ["one", "two"]
    assert queue_cmd.cmd_queue_exists("public.destination") is True


def test_public_queue_metadata_alias_and_broadcast_commands(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    monkeypatch.setattr(queue_cmd, "_context", lambda: ctx)
    queue_cmd.write_message(ctx, "public.target", "seed")

    listed = queue_cmd.cmd_queue_list(stats=True)
    assert all(isinstance(info, QueueInfo) for info in listed)
    assert next(info for info in listed if info.name == "public.target").messages == 1
    assert queue_cmd.cmd_queue_exists("public.target") is True
    assert queue_cmd.cmd_queue_stats("public.target").total_messages == 1

    added = queue_cmd.cmd_queue_alias_add("public-alias", "public.target")
    assert added == QueueAliasRecord(alias="public-alias", target="public.target")
    assert queue_cmd.cmd_queue_alias_list() == (added,)
    alias_write = queue_cmd.cmd_queue_write("@public-alias", "via-alias")
    assert alias_write.queue == "public.target"
    assert (
        queue_cmd.cmd_queue_read("@public-alias", all=True)[-1].message == "via-alias"
    )
    assert queue_cmd.cmd_queue_alias_remove("public-alias") == added
    assert queue_cmd.cmd_queue_alias_list() == ()

    broadcast_result = queue_cmd.cmd_queue_broadcast("payload", pattern="public.*")
    assert isinstance(broadcast_result, QueueBroadcastReceipt)
    assert broadcast_result.target_count >= 1


def test_public_queue_endpoint_commands_return_endpoint_records(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    monkeypatch.setattr(queue_cmd, "_context", lambda: ctx)
    tid = str(time.time_ns())
    task = Consumer(
        ctx.broker_target,
        make_function_taskspec(
            tid,
            "tests.tasks.sample_targets:echo_payload",
            weft_context=str(root),
        ),
        config=ctx.config,
    )
    try:
        task.register_endpoint_name("public-endpoint")
        resolved = queue_cmd.cmd_queue_resolve("public-endpoint")
        assert isinstance(resolved, EndpointResolution)
        assert resolved.tid == tid
        endpoints = queue_cmd.cmd_queue_list(endpoints=True)
        assert endpoints == (resolved,)
        receipt = queue_cmd.cmd_queue_write(
            "endpoint payload",
            endpoint="public-endpoint",
        )
        assert receipt.queue == resolved.inbox
    finally:
        task.cleanup()


def test_public_queue_delete_reports_exact_and_queue_counts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    monkeypatch.setattr(queue_cmd, "_context", lambda: ctx)
    queue_cmd.write_message(ctx, "delete.public", "one")
    entry = queue_cmd.peek_queue(ctx, "delete.public")[0]
    assert entry.timestamp is not None

    exact = queue_cmd.cmd_queue_delete(
        "delete.public",
        message=str(entry.timestamp),
    )
    assert exact == QueueDeleteReceipt(
        queue="delete.public",
        deleted_count=1,
        queues_deleted=0,
        all_queues=False,
        exact_message=str(entry.timestamp),
    )

    queue_cmd.write_message(ctx, "delete.public", "two")
    whole_queue = queue_cmd.cmd_queue_delete("delete.public")
    assert whole_queue.deleted_count == 1
    assert whole_queue.queues_deleted == 1


def test_public_queue_watch_returns_closable_structured_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_queue = _ClosableWatchQueue("watch.queue", [[("payload", 5)]])
    monitor_queue = _FakeWatchQueue("watch.queue", [])

    class _FakeContext:
        def __init__(self) -> None:
            self.config: dict[str, Any] = {}
            self._queues = [data_queue, monitor_queue]

        def queue(self, _name: str, *, persistent: bool = True):
            del persistent
            return self._queues.pop(0)

    monkeypatch.setattr(queue_cmd, "_context", lambda: _FakeContext())
    monkeypatch.setattr(queue_cmd, "QueueChangeMonitor", _FakeQueueChangeMonitor)

    stream = queue_cmd.cmd_queue_watch("watch.queue", limit=1, interval=0.01)
    assert next(stream) == QueueEntry(
        queue="watch.queue", message="payload", timestamp=5
    )
    with pytest.raises(StopIteration):
        next(stream)
    stream.close()
    stream.close()
    assert data_queue.closed
    assert monitor_queue.closed


@pytest.mark.parametrize(
    ("invoke", "message"),
    [
        (lambda: queue_cmd.cmd_queue_write("q", None), "message is required"),
        (lambda: queue_cmd.cmd_queue_broadcast(None), "message is required"),
        (
            lambda: queue_cmd.cmd_queue_read(
                "q", all=True, message="1779600000000000001"
            ),
            "message cannot be used with all, after, or before",
        ),
        (
            lambda: queue_cmd.cmd_queue_move("q", "q"),
            "source and destination queues cannot be the same",
        ),
        (
            lambda: queue_cmd.cmd_queue_list(pattern="a*", prefix="a"),
            "pattern and prefix cannot be used together",
        ),
        (
            lambda: queue_cmd.cmd_queue_watch("q", peek=True, move="other"),
            "peek cannot be used with move",
        ),
        (
            lambda: queue_cmd.cmd_queue_delete(None),
            "queue name is required unless all=True",
        ),
    ],
)
def test_public_queue_commands_raise_typed_usage_errors(invoke, message: str) -> None:
    with pytest.raises(CommandUsageError, match=message):
        invoke()


def test_public_queue_resolve_raises_typed_error_for_missing_endpoint(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    monkeypatch.setattr(queue_cmd, "_context", lambda: ctx)

    with pytest.raises(CommandExecutionError, match="No active endpoint"):
        queue_cmd.cmd_queue_resolve("missing")


def test_public_queue_commands_do_not_read_stdin_or_write_process_output(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _ForbiddenStdin:
        def read(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("public queue command read process stdin")

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    monkeypatch.setattr(queue_cmd, "_context", lambda: ctx)
    monkeypatch.setattr(sys, "stdin", _ForbiddenStdin())

    queue_cmd.cmd_queue_write("no.io", "payload")
    queue_cmd.cmd_queue_broadcast("broadcast", pattern="no.*")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_public_queue_backend_failures_are_typed_and_chained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OSError("broker unavailable")

    def fail_context():
        raise failure

    monkeypatch.setattr(queue_cmd, "_context", fail_context)

    with pytest.raises(
        CommandExecutionError, match="failed to resolve queue context"
    ) as caught:
        queue_cmd.cmd_queue_exists("queue")
    assert caught.value.__cause__ is failure


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


def test_list_queues_supports_prefix(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "alpha.one", "item")
    queue_cmd.write_message(ctx, "beta.one", "item")

    queues = queue_cmd.list_queues(ctx, prefix="alpha.")

    assert [info.name for info in queues] == ["alpha.one"]


def test_exists_and_stats_commands_delegate_to_simplebroker(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "meta.queue", "item")

    exists_result = queue_cmd.exists_command(
        "meta.queue",
        json_output=True,
        spec_context=str(root),
    )
    stats_result = queue_cmd.stats_command(
        "meta.queue",
        json_output=True,
        spec_context=str(root),
    )

    assert exists_result[0] == 0
    assert json.loads(exists_result[1]) == {"queue": "meta.queue", "exists": True}
    assert stats_result[0] == 0
    assert json.loads(stats_result[1]) == {
        "queue": "meta.queue",
        "pending": 1,
        "claimed": 0,
        "total": 1,
        "exists": True,
    }


def test_alias_list_command_returns_empty_exit_for_missing_target(tmp_path) -> None:
    root = prepare_project_root(tmp_path)

    exit_code, stdout, stderr = queue_cmd.alias_list_command(
        target="missing.queue",
        spec_context=str(root),
    )

    assert exit_code == 2
    assert stdout == ""
    assert stderr == ""


def test_queue_command_filter_validation_matches_simplebroker(tmp_path) -> None:
    root = prepare_project_root(tmp_path)

    read_result = queue_cmd.read_command(
        "meta.queue",
        all_messages=True,
        message_id="1234567890123456789",
        spec_context=str(root),
    )
    delete_result = queue_cmd.delete_command(
        "meta.queue",
        delete_all=False,
        message_id="123",
        spec_context=str(root),
    )
    watch_result = queue_cmd.watch_command(
        "meta.queue",
        limit=1,
        interval=0.01,
        with_timestamps=False,
        json_output=False,
        peek=False,
        after="1234567890123456789",
        move_to="other.queue",
        spec_context=str(root),
    )

    assert read_result == (
        1,
        "",
        "--message cannot be used with --all, --after, or --before",
    )
    assert delete_result == (
        1,
        "",
        "invalid message ID: expected exactly 19 digits within range",
    )
    assert watch_result == (
        1,
        "",
        "--move drains ALL messages from source queue, incompatible with --after filtering",
    )


def test_delete_queue_messages_rejects_message_with_all_queues_without_deleting(
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "delete.api.one", "one")
    queue_cmd.write_message(ctx, "delete.api.two", "two")
    entry = queue_cmd.peek_queue(ctx, "delete.api.one")[0]
    assert entry.timestamp is not None

    with pytest.raises(ValueError, match="message_id cannot be used with all_queues"):
        queue_cmd.delete_queue_messages(
            ctx,
            all_queues=True,
            message_id=entry.timestamp,
        )

    assert [m.body for m in queue_cmd.read_messages(ctx, "delete.api.one")] == ["one"]
    assert [m.body for m in queue_cmd.read_messages(ctx, "delete.api.two")] == ["two"]


def test_delete_queue_messages_requires_explicit_target_without_deleting(
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "delete.api.default", "keep")

    with pytest.raises(
        ValueError, match="queue_name is required unless all_queues=True"
    ):
        queue_cmd.delete_queue_messages(ctx)

    assert [m.body for m in queue_cmd.read_messages(ctx, "delete.api.default")] == [
        "keep"
    ]


def test_delete_queue_messages_rejects_queue_name_with_all_queues_without_deleting(
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "delete.api.named", "keep")

    with pytest.raises(ValueError, match="queue_name cannot be used with all_queues"):
        queue_cmd.delete_queue_messages(
            ctx,
            "delete.api.named",
            all_queues=True,
        )

    assert [m.body for m in queue_cmd.read_messages(ctx, "delete.api.named")] == [
        "keep"
    ]


def test_exact_queue_message_inputs_normalize_strings_before_queue_calls() -> None:
    message_id = 1_779_600_000_000_000_001
    calls: list[tuple[str, object]] = []

    class _ExactQueue:
        def read_one(self, *, exact_timestamp: object, with_timestamps: bool):
            assert with_timestamps is True
            calls.append(("read", exact_timestamp))
            return "read", message_id

        def peek_one(self, *, exact_timestamp: object, with_timestamps: bool):
            assert with_timestamps is True
            calls.append(("peek", exact_timestamp))
            return "peek", message_id

        def move_generator(
            self,
            _destination: str,
            *,
            with_timestamps: bool,
            exact_timestamp: object,
        ):
            assert with_timestamps is True
            calls.append(("move", exact_timestamp))
            return iter([("move", message_id)])

        def delete(self, *, message_id: object) -> bool:
            calls.append(("delete", message_id))
            return True

        def close(self) -> None:
            return

    class _ExactContext:
        def queue(self, _name: str, *, persistent: bool = True) -> _ExactQueue:
            del persistent
            return _ExactQueue()

    context = _ExactContext()
    canonical = "1779600000000000001"

    read_entry = queue_cmd.read_queue(context, "source", message_id=canonical)[0]
    peek_entry = queue_cmd.peek_queue(context, "source", message_id=canonical)[0]
    move_receipt = queue_cmd.move_queue_messages(
        context,
        "source",
        "destination",
        message_id=canonical,
    )
    delete_receipt = queue_cmd.delete_queue_messages(
        context,
        "source",
        message_id=canonical,
    )

    assert calls == [
        ("read", message_id),
        ("peek", message_id),
        ("move", message_id),
        ("delete", message_id),
    ]
    assert read_entry.timestamp == message_id
    assert peek_entry.timestamp == message_id
    assert move_receipt.moved_count == 1
    assert delete_receipt.deleted_count == 1


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


def test_queue_message_json_formats_broker_id_without_mutating_domain_value() -> None:
    message_id = 1_779_100_000_000_000_002
    message = queue_cmd.QueueMessage("payload", message_id)

    assert message.as_dict() == {
        "message": "payload",
        "timestamp": "1779100000000000002",
    }
    assert message.timestamp == message_id


def test_bounded_move_json_formats_broker_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_id = 1_779_100_000_000_000_003

    class _MoveQueue:
        def move_many(self, *_args: object, **_kwargs: object):
            return [("payload", message_id)]

        def close(self) -> None:
            return None

    class _MoveContext:
        def queue(self, _name: str, *, persistent: bool = True) -> _MoveQueue:
            del persistent
            return _MoveQueue()

    monkeypatch.setattr(
        queue_cmd, "_context", lambda _spec_context=None: _MoveContext()
    )

    exit_code, stdout, stderr = queue_cmd.move_command(
        "source",
        "destination",
        limit=1,
        json_output=True,
    )

    assert exit_code == 0
    assert stderr == ""
    _summary, raw_record = stdout.splitlines()
    assert json.loads(raw_record) == {
        "message": "payload",
        "timestamp": "1779100000000000003",
    }


def test_watch_queue_uses_queue_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_queue = _FakeWatchQueue("watch.queue", [[], [("payload", 5)]])
    monitor_queue = _FakeWatchQueue("watch.queue", [])
    created_monitors: list[_FakeQueueChangeMonitor] = []

    class _FakeContext:
        def __init__(self) -> None:
            self.config: dict[str, Any] = {}
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


def test_watch_queue_closes_generator_when_limit_stops_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_queue = _ClosableWatchQueue("watch.queue", [[("payload", 5), ("extra", 6)]])
    monitor_queue = _FakeWatchQueue("watch.queue", [])

    class _FakeContext:
        def __init__(self) -> None:
            self.config: dict[str, Any] = {}
            self._queues = [data_queue, monitor_queue]

        def queue(self, _name: str, *, persistent: bool = True):
            del persistent
            return self._queues.pop(0)

    first_context = _FakeContext()
    second_context = _FakeContext()
    assert first_context.config is not second_context.config

    monkeypatch.setattr(queue_cmd, "QueueChangeMonitor", _FakeQueueChangeMonitor)

    messages = list(
        queue_cmd.watch_queue(
            first_context,
            "watch.queue",
            interval=0.25,
            max_messages=1,
            with_timestamps=True,
        )
    )

    assert [message.body for message in messages] == ["payload"]
    assert data_queue.generators[0].closed
    assert data_queue.closed
    assert monitor_queue.closed


def test_write_command_rejects_omitted_message_without_reading_stdin(
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
    result = queue_cmd.write_command("stdin.queue", None)

    assert result == (1, "", "message content must be supplied by the CLI adapter")
    assert captured == {}


def test_broadcast_command_rejects_omitted_message_without_reading_stdin(
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
    result = queue_cmd.broadcast_command(None, pattern="jobs.*")

    assert result == (1, "", "message content must be supplied by the CLI adapter")
    assert captured == {}


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


@pytest.mark.parametrize(
    "registration_order",
    [("low", "high"), ("high", "low")],
)
def test_list_command_endpoints_uses_lowest_live_tid_as_canonical(
    tmp_path,
    registration_order: tuple[str, str],
) -> None:
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
        tasks = {"low": low_task, "high": high_task}
        for owner in registration_order:
            tasks[owner].register_endpoint_name("mayor")

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
