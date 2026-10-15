"""Realtime event observers retain bounded broker ownership [SB-0.4]."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.task_observation import write_task_observation
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands import events as events_cmd
from weft.context import WeftContext, build_context
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared, pytest.mark.timeout(30)]


@pytest.fixture
def observed_connections(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[WeftContext, list[Any], list[Queue]]]:
    ctx = build_context(spec_context=workdir)
    connections: list[Any] = []
    queues: list[Queue] = []
    if ctx.backend_name == "postgres":
        psycopg = pytest.importorskip("psycopg")
        original_connect = psycopg.Connection.connect.__func__

        def connect(cls: Any, *args: Any, **kwargs: Any) -> Any:
            connection = original_connect(cls, *args, **kwargs)
            connections.append(connection)
            return connection

        monkeypatch.setattr(psycopg.Connection, "connect", classmethod(connect))
        monkeypatch.setattr(psycopg, "connect", psycopg.Connection.connect)
    else:
        original_sqlite_connect = sqlite3.connect

        def sqlite_connect(*args: Any, **kwargs: Any) -> Any:
            connection = original_sqlite_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        monkeypatch.setattr(sqlite3, "connect", sqlite_connect)
    original_queue = WeftContext.queue

    def queue(self: WeftContext, name: str, *, persistent: bool = False) -> Queue:
        opened = original_queue(self, name, persistent=persistent)
        queues.append(opened)
        return opened

    monkeypatch.setattr(WeftContext, "queue", queue)
    yield ctx, connections, queues
    for connection in connections:
        if ctx.backend_name == "postgres":
            assert connection.closed
        else:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")


def _seed_task(
    ctx: WeftContext, *, status: str = "running", streams: bool = False
) -> tuple[str, dict[str, Any]]:
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        tid = str(log.generate_timestamp())
        payload = TaskSpec.model_validate(
            {
                "tid": tid,
                "name": "event-observer",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "persistent": True,
                    "stream_output": streams,
                },
            }
        ).model_dump(mode="json")
        log.write(json.dumps({"tid": tid, "status": status, "taskspec": payload}))
    write_task_observation(
        ctx,
        {
            "tid": tid,
            "event": "work_completed" if status == "completed" else "work_started",
            "status": status,
            "taskspec": {**payload, "state": {"status": status}},
        },
    )
    return tid, payload


@pytest.mark.parametrize("streams", [False, True])
def test_realtime_evidence_rechecks_reuse_connection_after_initial_scan(
    observed_connections: tuple[WeftContext, list[Any], list[Queue]],
    monkeypatch: pytest.MonkeyPatch,
    streams: bool,
) -> None:
    ctx, connections, queues = observed_connections
    tid, _payload = _seed_task(ctx, streams=streams)
    cancelled = threading.Event()
    previous: tuple[int, int] | None = None
    deltas: list[tuple[int, int]] = []
    turns = 0

    def wait(_monitor: Any, _timeout: float | None) -> bool:
        nonlocal turns, previous
        turns += 1
        assert turns <= 5
        if previous is not None and turns > 2:
            deltas.append((len(connections) - previous[0], len(queues) - previous[1]))
        if turns == 5:
            cancelled.set()
        else:
            with ctx.broker() as writer:
                writer.write(f"T{tid}.ctrl_out", json.dumps({"unrelated": turns}))
        previous = (len(connections), len(queues))
        return True

    monkeypatch.setattr(events_cmd.QueueChangeMonitor, "wait", wait)
    emitted = list(
        events_cmd.iter_task_realtime_events(ctx, tid, cancel_event=cancelled)
    )
    assert emitted[0].event_type == "snapshot"
    assert all(event.event_type not in {"result", "end"} for event in emitted)
    assert deltas == [(0, 0)] * 3
    with ctx.broker() as reader:
        assert reader.get_queue_stat(f"T{tid}.ctrl_out").pending == 4


def test_realtime_result_grace_borrows_owner_and_observes_late_result(
    observed_connections: tuple[WeftContext, list[Any], list[Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, connections, queues = observed_connections
    tid, _payload = _seed_task(ctx, status="completed", streams=True)
    now = 100.0
    reads = 0
    deltas: list[tuple[int, int]] = []
    original_peek = events_cmd._peek_result_value

    def peek(*args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        previous = (len(connections), len(queues))
        reads += 1
        assert reads <= 4
        result = original_peek(*args, **kwargs)
        if reads > 1:
            deltas.append((len(connections) - previous[0], len(queues) - previous[1]))
        return result

    def wait(_monitor: Any, timeout: float | None) -> bool:
        nonlocal now
        now += max(timeout or 0.0, 0.001)
        if reads == 1:
            with ctx.broker() as writer:
                writer.write(f"T{tid}.outbox", json.dumps({"late": True}))
        return True

    monkeypatch.setattr(
        events_cmd, "time", SimpleNamespace(monotonic=lambda: now, time_ns=time.time_ns)
    )
    monkeypatch.setattr(events_cmd, "_peek_result_value", peek)
    monkeypatch.setattr(events_cmd.QueueChangeMonitor, "wait", wait)
    emitted = list(events_cmd.iter_task_realtime_events(ctx, tid, timeout=10))
    assert reads == 2
    assert deltas == [(0, 0)]
    assert emitted[-2].event_type == "result"
    assert emitted[-2].payload["value"] == {"late": True}
    assert emitted[-1].event_type == "end"
    with ctx.broker() as reader:
        assert reader.peek_one(f"T{tid}.outbox", with_timestamps=False) == json.dumps(
            {"late": True}
        )


def test_realtime_observer_rebinds_to_independent_custom_route_commits(
    observed_connections: tuple[WeftContext, list[Any], list[Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _connections, _queues = observed_connections
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        tid = str(log.generate_timestamp())
    payload = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "first-discovery",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "persistent": True,
                "stream_output": True,
            },
        }
    ).model_dump(mode="json")
    custom_outbox = f"custom.{tid}.outbox"
    custom_control = f"custom.{tid}.ctrl_out"
    now = 100.0
    published = False
    routes_open = False
    original_open = events_cmd._open_realtime_routes

    def open_routes(*args: Any, **kwargs: Any) -> Any:
        nonlocal routes_open
        opened = original_open(*args, **kwargs)
        routes_open = True
        return opened

    stream_body = json.dumps(
        {"type": "stream", "stream": "stdout", "data": "new route"}
    )
    terminal_body = json.dumps(
        {"tid": tid, "type": "terminal", "source": "task", "status": "cancelled"}
    )

    def wait(_monitor: Any, timeout: float | None) -> bool:
        nonlocal now, published
        now += max(timeout or 0.0, 0.001)
        if not published and routes_open:
            published = True
            payload["io"]["outputs"]["outbox"] = custom_outbox
            payload["io"]["control"]["ctrl_out"] = custom_control
            with ctx.broker() as writer:
                writer.write(
                    WEFT_GLOBAL_LOG_QUEUE,
                    json.dumps({"tid": tid, "status": "running", "taskspec": payload}),
                )
                write_task_observation(
                    ctx,
                    {
                        "tid": tid,
                        "event": "work_started",
                        "status": "running",
                        "taskspec": payload,
                    },
                )
                writer.write(custom_outbox, stream_body)
                writer.write(custom_control, terminal_body)
        return True

    monkeypatch.setattr(
        events_cmd,
        "time",
        SimpleNamespace(monotonic=lambda: now, time_ns=time.time_ns),
    )
    monkeypatch.setattr(events_cmd, "_open_realtime_routes", open_routes)
    monkeypatch.setattr(events_cmd.QueueChangeMonitor, "wait", wait)
    emitted = list(events_cmd.iter_task_realtime_events(ctx, tid, timeout=10))
    assert [
        event.payload["data"] for event in emitted if event.event_type == "stdout"
    ] == ["new route"]
    assert emitted[-2].event_type == "result"
    assert emitted[-2].payload["status"] == "cancelled"
    assert emitted[-1].event_type == "end"
    with ctx.broker() as reader:
        assert reader.peek_one(custom_outbox, with_timestamps=False) == stream_body
        assert reader.peek_one(custom_control, with_timestamps=False) == terminal_body


@pytest.mark.parametrize(
    "exit_kind",
    [
        "snapshot-close",
        "stream-close",
        "cancel",
        "cancel-after-snapshot",
        "wait-error",
        "read-error",
    ],
)
def test_realtime_early_exit_closes_owned_connections(
    observed_connections: tuple[WeftContext, list[Any], list[Queue]],
    monkeypatch: pytest.MonkeyPatch,
    exit_kind: str,
) -> None:
    ctx, _connections, _queues = observed_connections
    tid, _payload = _seed_task(ctx, streams=True)
    if exit_kind == "stream-close":
        with ctx.broker() as writer:
            writer.write(
                f"T{tid}.outbox",
                json.dumps({"type": "stream", "stream": "stdout", "data": "hi"}),
            )
    cancelled = threading.Event()
    if exit_kind == "cancel":
        cancelled.set()

    def fail_wait(_monitor: Any, _timeout: float | None) -> bool:
        raise RuntimeError("injected observer wait failure")

    def fail_read(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("injected observer read failure")

    iterator = events_cmd.iter_task_realtime_events(ctx, tid, cancel_event=cancelled)
    try:
        if exit_kind == "cancel":
            assert list(iterator) == []
        elif exit_kind == "wait-error":
            monkeypatch.setattr(events_cmd.QueueChangeMonitor, "wait", fail_wait)
            with pytest.raises(RuntimeError, match="injected observer wait failure"):
                list(iterator)
        elif exit_kind == "read-error":
            monkeypatch.setattr(events_cmd, "_classify_result_observation", fail_read)
            with pytest.raises(RuntimeError, match="injected observer read failure"):
                list(iterator)
        else:
            assert next(iterator).event_type == "snapshot"
            if exit_kind == "cancel-after-snapshot":
                with ctx.broker() as broker:
                    broker_type = type(broker)
                monkeypatch.setattr(broker_type, "latest_pending_timestamp", fail_read)
                cancelled.set()
                assert list(iterator) == []
            if exit_kind == "stream-close":
                assert next(iterator).event_type == "stdout"
    finally:
        close = getattr(iterator, "close", None)
        assert callable(close)
        close()


def test_realtime_replays_startup_lifecycle_with_original_timestamps(
    observed_connections: tuple[WeftContext, list[Any], list[Queue]],
) -> None:
    """Result discovery must not consume the realtime observer's lifecycle cursor."""
    ctx, _connections, _queues = observed_connections
    tid, _payload = _seed_task(ctx)
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        initial_rows = list(log.peek_generator(with_timestamps=True))
        timestamps = [initial_rows[0][1]]
        for status in ("running", "completed"):
            timestamps.append(log.write(json.dumps({"tid": tid, "status": status})))
    emitted = list(events_cmd.iter_task_events(ctx, tid, follow=False))
    states = emitted
    assert [event.timestamp for event in states] == timestamps
    assert sum(event.payload.get("status") == "completed" for event in states) == 1
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        assert len(list(log.peek_generator())) == 3


def test_queued_realtime_observer_does_not_expand_to_pre_tid_history(
    observed_connections: tuple[WeftContext, list[Any], list[Queue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing initialization must not widen the existing queued-task replay bound."""
    ctx, _connections, _queues = observed_connections
    body = json.dumps({"tid": "other", "event": "before-child-submission"})
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        log.write(body)
        tid = str(log.generate_timestamp())
    cancelled = threading.Event()
    observing = False
    reads = 0
    waits = 0
    owner = threading.current_thread()
    original_open = events_cmd._open_realtime_routes
    original_peek = Queue.peek_generator
    original_wait = events_cmd.QueueChangeMonitor.wait

    def open_routes(*args: Any, **kwargs: Any) -> Any:
        nonlocal observing
        opened = original_open(*args, **kwargs)
        observing = True
        return opened

    def peek(queue: Queue, *args: Any, **kwargs: Any) -> Iterator[Any]:
        nonlocal reads
        iterator = original_peek(queue, *args, **kwargs)
        try:
            for row in iterator:
                if observing and threading.current_thread() is owner and row[0] == body:
                    reads += 1
                yield row
        finally:
            iterator.close()

    def wait(monitor: Any, timeout: float | None) -> bool:
        nonlocal waits
        if not observing:
            return original_wait(monitor, timeout)
        waits += 1
        if waits == 2:
            cancelled.set()
        assert waits <= 2
        return False

    monkeypatch.setattr(events_cmd, "_open_realtime_routes", open_routes)
    monkeypatch.setattr(Queue, "peek_generator", peek)
    monkeypatch.setattr(events_cmd.QueueChangeMonitor, "wait", wait)
    emitted = list(
        events_cmd.iter_task_realtime_events(
            ctx, tid, timeout=10, cancel_event=cancelled
        )
    )
    assert [event.event_type for event in emitted] == ["snapshot"]
    assert emitted[0].payload["status"] == "unknown"
    assert waits == 2
    assert reads == 0


@pytest.mark.parametrize("cancel_at", ["grace_entry", "deadline_wake"])
def test_realtime_cancellation_precedes_coincident_caller_timeout(
    observed_connections: tuple[WeftContext, list[Any], list[Queue]],
    monkeypatch: pytest.MonkeyPatch,
    cancel_at: str,
) -> None:
    """Cancellation wins at grace entry and at a real deadline wake [MF-5]."""
    ctx, _connections, _queues = observed_connections
    tid, _payload = _seed_task(ctx, status="completed", streams=True)
    cancelled = threading.Event()
    now = 100.0
    timeout = 1.0
    deadline = now + timeout
    peeks = 0
    original_peek = events_cmd._peek_result_value

    def peek(*args: Any, **kwargs: Any) -> Any:
        nonlocal now, peeks
        assert not cancelled.is_set(), "durable result read after cancellation"
        value = original_peek(*args, **kwargs)
        peeks += 1
        if cancel_at == "grace_entry":
            # Model cancellation racing the fresh read at its caller deadline.
            # The next grace condition sees cancellation before any new wait.
            now = deadline
            cancelled.set()
        return value

    def wait(_monitor: Any, wait_timeout: float | None) -> bool:
        nonlocal now
        assert not cancelled.is_set()
        if peeks:
            assert cancel_at == "deadline_wake"
            now = deadline
            cancelled.set()
            return False  # The supplied deadline, not a broker publication.
        now += max(wait_timeout or 0.0, 0.001)
        return False

    clock = SimpleNamespace(monotonic=lambda: now, time_ns=time.time_ns)
    monkeypatch.setattr(events_cmd, "time", clock)
    monkeypatch.setattr(events_cmd.task_ops, "time", clock)
    monkeypatch.setattr(events_cmd, "_peek_result_value", peek)
    monkeypatch.setattr(events_cmd.QueueChangeMonitor, "wait", wait)
    emitted = list(
        events_cmd.iter_task_realtime_events(
            ctx, tid, timeout=timeout, cancel_event=cancelled
        )
    )
    assert peeks == 1
    assert cancelled.is_set()
    assert all(event.event_type not in {"result", "end"} for event in emitted)
