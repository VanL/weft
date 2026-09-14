"""Unit tests for queue command helpers."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import WEFT_ENDPOINTS_REGISTRY_QUEUE
from weft._exceptions import CommandExecutionError, CommandUsageError
from weft.client import WeftClient
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
from weft.context import WeftContext, build_context
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


@pytest.mark.parametrize("operation", ["write", "broadcast"])
def test_public_queue_writes_use_resolved_context_message_limit(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    context = SimpleNamespace(config={"MAX_MESSAGE_SIZE": 4})
    monkeypatch.setattr(
        queue_cmd,
        "_public_command_context",
        lambda: context,
    )

    with pytest.raises(CommandUsageError, match="maximum size of 4 bytes"):
        if operation == "write":
            queue_cmd.cmd_queue_write("limited.queue", "hello")
        else:
            queue_cmd.cmd_queue_broadcast("hello", pattern="limited.*")


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


def test_move_queue_entries_moves_every_message_with_all(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue_cmd.write_message(ctx, "from.queue", "a")
    queue_cmd.write_message(ctx, "from.queue", "b")

    result = queue_cmd.move_queue_entries(
        ctx,
        "from.queue",
        "to.queue",
        all_messages=True,
    )
    assert isinstance(result, QueueMoveResult)
    assert [entry.message for entry in result.entries] == ["a", "b"]
    assert result.moved_count == 2
    assert all(entry.queue == "from.queue" for entry in result.entries)

    dest_messages = queue_cmd.read_messages(ctx, "to.queue", all_messages=True)
    assert [m.body for m in dest_messages] == ["a", "b"]


def _seed_move_source(ctx: WeftContext, name: str) -> list[int]:
    """Seed one source queue and return its message IDs in broker order."""

    for index in range(3):
        queue_cmd.write_message(ctx, name, f"m{index}")
    return [
        int(entry.timestamp)
        for entry in queue_cmd.peek_queue(ctx, name, all_messages=True)
    ]


def _move_via_command(
    source: str, destination: str, **selection: Any
) -> QueueMoveResult:
    return queue_cmd.cmd_queue_move(
        source,
        destination,
        limit=selection.get("limit"),
        all=selection.get("all_messages", False),
        message=selection.get("message_id"),
        after=selection.get("after"),
        before=selection.get("before"),
    )


def _move_via_client(
    client: WeftClient, source: str, destination: str, **selection: Any
) -> int:
    return client.queues.move(
        source,
        destination,
        limit=selection.get("limit"),
        all_messages=selection.get("all_messages", False),
        message_id=selection.get("message_id"),
        after=selection.get("after"),
        before=selection.get("before"),
    ).moved_count


@pytest.mark.parametrize(
    ("case", "selection", "expected"),
    [
        ("limit_absent", lambda ids: {}, ["m0"]),
        ("limit_one", lambda ids: {"limit": 1}, ["m0"]),
        ("limit_two", lambda ids: {"limit": 2}, ["m0", "m1"]),
        ("limit_over", lambda ids: {"limit": 99}, ["m0", "m1", "m2"]),
        ("all", lambda ids: {"all_messages": True}, ["m0", "m1", "m2"]),
        ("exact_id", lambda ids: {"message_id": ids[1]}, ["m1"]),
        ("after_single", lambda ids: {"after": ids[0]}, ["m1"]),
        (
            "after_all",
            lambda ids: {"after": ids[0], "all_messages": True},
            ["m1", "m2"],
        ),
        (
            "before_all",
            lambda ids: {"before": ids[2], "all_messages": True},
            ["m0", "m1"],
        ),
        (
            "bounded_limit",
            lambda ids: {"after": ids[0], "before": ids[2], "limit": 5},
            ["m1"],
        ),
    ],
)
def test_queue_move_selection_matches_across_command_and_client_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    selection: Callable[[list[int]], dict[str, Any]],
    expected: list[str],
) -> None:
    """Both public move surfaces select the same messages for one selection.

    Verifies:
    - `cmd_queue_move` and `client.queues.move` move identical message sets
    - Destination contents and moved counts agree for every selector
    """

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    monkeypatch.setattr(queue_cmd, "_context", lambda: ctx)
    client = WeftClient.from_weft_context(ctx)

    command_ids = _seed_move_source(ctx, f"{case}.command.source")
    client_ids = _seed_move_source(ctx, f"{case}.client.source")

    command_result = _move_via_command(
        f"{case}.command.source",
        f"{case}.command.dest",
        **selection(command_ids),
    )
    command_count = command_result.moved_count
    # [PY-2]: the command result carries the exact ordered moved set.
    assert [entry.message for entry in command_result.entries] == expected
    assert all(
        entry.queue == f"{case}.command.source" for entry in command_result.entries
    )
    timestamps = [int(entry.timestamp) for entry in command_result.entries]
    assert timestamps == sorted(timestamps)
    assert set(timestamps) <= set(command_ids)
    client_count = _move_via_client(
        client,
        f"{case}.client.source",
        f"{case}.client.dest",
        **selection(client_ids),
    )

    assert command_count == client_count == len(expected)
    for surface in ("command", "client"):
        moved = queue_cmd.read_queue(ctx, f"{case}.{surface}.dest", all_messages=True)
        assert [entry.message for entry in moved] == expected


@pytest.mark.parametrize(
    ("case", "selection", "message"),
    [
        ("limit_zero", lambda ids: {"limit": 0}, "limit must be at least 1"),
        ("limit_negative", lambda ids: {"limit": -1}, "limit must be at least 1"),
        (
            "id_with_limit",
            lambda ids: {"message_id": ids[0], "limit": 1},
            "message cannot be used with limit, all, after, or before",
        ),
        (
            "id_with_all",
            lambda ids: {"message_id": ids[0], "all_messages": True},
            "message cannot be used with limit, all, after, or before",
        ),
        (
            "id_with_after",
            lambda ids: {"message_id": ids[0], "after": ids[0]},
            "message cannot be used with limit, all, after, or before",
        ),
        (
            "id_with_before",
            lambda ids: {"message_id": ids[0], "before": ids[2]},
            "message cannot be used with limit, all, after, or before",
        ),
    ],
)
def test_queue_move_rejects_the_same_selections_on_both_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    selection: Callable[[list[int]], dict[str, Any]],
    message: str,
) -> None:
    """Rejected move selections raise the same typed error on both surfaces.

    Verifies:
    - `limit=0` is an error rather than an implicit bounded default
    - Conflicting exact-ID selectors are rejected identically
    - Nothing is moved when a selection is rejected
    """

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    monkeypatch.setattr(queue_cmd, "_context", lambda: ctx)
    client = WeftClient.from_weft_context(ctx)
    ids = _seed_move_source(ctx, f"{case}.source")

    with pytest.raises(CommandUsageError, match=message):
        _move_via_command(f"{case}.source", f"{case}.dest", **selection(ids))
    with pytest.raises(CommandUsageError, match=message):
        _move_via_client(client, f"{case}.source", f"{case}.dest", **selection(ids))

    assert queue_cmd.peek_queue(ctx, f"{case}.dest", all_messages=True) == []
    assert len(queue_cmd.peek_queue(ctx, f"{case}.source", all_messages=True)) == 3


def test_queue_move_rejects_identical_source_and_destination_on_both_surfaces(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both surfaces refuse a move whose source and destination are the same."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    monkeypatch.setattr(queue_cmd, "_context", lambda: ctx)
    client = WeftClient.from_weft_context(ctx)
    _seed_move_source(ctx, "same.source")

    with pytest.raises(CommandUsageError, match="(?i)source and destination"):
        _move_via_command("same.source", "same.source", all_messages=True)
    with pytest.raises(CommandUsageError, match="(?i)source and destination"):
        _move_via_client(client, "same.source", "same.source", all_messages=True)

    assert len(queue_cmd.peek_queue(ctx, "same.source", all_messages=True)) == 3


@pytest.mark.parametrize(
    ("case", "selection"),
    [
        ("empty_absent", {}),
        ("empty_limit", {"limit": 2}),
        ("empty_all", {"all_messages": True}),
    ],
)
def test_queue_move_from_empty_source_reports_nothing_on_both_surfaces(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    selection: dict[str, Any],
) -> None:
    """An empty source yields an empty moved set rather than an error."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    monkeypatch.setattr(queue_cmd, "_context", lambda: ctx)
    client = WeftClient.from_weft_context(ctx)

    result = queue_cmd.cmd_queue_move(
        f"{case}.source",
        f"{case}.dest",
        limit=selection.get("limit"),
        all=selection.get("all_messages", False),
    )
    assert result.entries == ()
    assert result.moved_count == 0
    assert _move_via_client(client, f"{case}.source", f"{case}.dest", **selection) == 0


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

        def move(
            self,
            _destination: str,
            *,
            message_id: object,
            after_timestamp: object,
            before_timestamp: object,
            all_messages: bool,
        ):
            assert (after_timestamp, before_timestamp, all_messages) == (
                None,
                None,
                False,
            )
            calls.append(("move", message_id))
            return {"message": "move", "timestamp": message_id}

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
    move_result = queue_cmd.move_queue_entries(
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
    assert move_result.moved_count == 1
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
    monkeypatch.setattr(
        queue_cmd,
        "_public_command_context",
        lambda: pytest.fail("context must not be resolved for missing input"),
    )
    with pytest.raises(CommandUsageError, match="message is required"):
        queue_cmd.cmd_queue_write("stdin.queue", None)


def test_broadcast_command_rejects_omitted_message_without_reading_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        queue_cmd,
        "_public_command_context",
        lambda: pytest.fail("context must not be resolved for missing input"),
    )
    with pytest.raises(CommandUsageError, match="message is required"):
        queue_cmd.cmd_queue_broadcast(None, pattern="jobs.*")


def test_resolve_command_returns_registered_endpoint_details(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

        monkeypatch.setattr(queue_cmd, "_public_command_context", lambda: ctx)
        result = queue_cmd.cmd_queue_resolve("mayor")

        assert result.name == "mayor"
        assert result.tid == tid
        assert result.inbox == spec.io.inputs["inbox"]
        assert result.live_candidates == 1
    finally:
        task.cleanup()


@pytest.mark.parametrize(
    "registration_order",
    [("low", "high"), ("high", "low")],
)
def test_list_command_endpoints_uses_lowest_live_tid_as_canonical(
    tmp_path,
    registration_order: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
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

        monkeypatch.setattr(queue_cmd, "_public_command_context", lambda: ctx)
        payload = queue_cmd.cmd_queue_list(endpoints=True)

        assert len(payload) == 1
        entry = payload[0]
        assert isinstance(entry, EndpointResolution)
        assert entry.name == "mayor"
        assert entry.tid == low_tid
        assert entry.status == "active"
        assert entry.inbox == f"T{low_tid}.inbox"
        assert entry.live_candidates == 2
    finally:
        high_task.cleanup()
        low_task.cleanup()


def test_resolve_command_filters_stale_endpoint_records_without_deleting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)
    try:
        claim_id = registry.write(
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

        monkeypatch.setattr(queue_cmd, "_public_command_context", lambda: ctx)
        with pytest.raises(CommandExecutionError, match="No active endpoint"):
            queue_cmd.cmd_queue_resolve("ghost")
        remaining = list(iter_queue_json_entries(registry))
        assert len(remaining) == 1
        assert remaining[0][1] == claim_id
        assert remaining[0][0]["tid"] == "1775630560447778816"
    finally:
        registry.close()
