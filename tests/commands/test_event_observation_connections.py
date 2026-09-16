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
    return tid, payload


@pytest.mark.parametrize("streams", [False, True])
def test_realtime_evidence_rechecks_create_no_connections_or_facades(
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
        assert turns <= 4
        if previous is not None:
            deltas.append((len(connections) - previous[0], len(queues) - previous[1]))
        if turns == 4:
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
        assert reader.get_queue_stat(f"T{tid}.ctrl_out").pending == 3


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
        deltas.append((len(connections) - previous[0], len(queues) - previous[1]))
        return result

    def wait(_monitor: Any, timeout: float | None) -> bool:
        nonlocal now
        now += max(timeout or 0.0, 0.001)
        if reads == 3:
            with ctx.broker() as writer:
                writer.write(f"T{tid}.outbox", json.dumps({"late": True}))
        return True

    monkeypatch.setattr(
        events_cmd, "time", SimpleNamespace(monotonic=lambda: now, time_ns=time.time_ns)
    )
    monkeypatch.setattr(events_cmd, "_peek_result_value", peek)
    monkeypatch.setattr(events_cmd.QueueChangeMonitor, "wait", wait)
    emitted = list(events_cmd.iter_task_realtime_events(ctx, tid))
    assert reads == 4
    assert deltas == [(0, 0)] * 4
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
    tid, payload = _seed_task(ctx, streams=True)
    custom_outbox = f"custom.{tid}.outbox"
    custom_control = f"custom.{tid}.ctrl_out"
    now = 100.0
    published = False
    stream_body = json.dumps(
        {"type": "stream", "stream": "stdout", "data": "new route"}
    )
    terminal_body = json.dumps(
        {"tid": tid, "type": "terminal", "source": "task", "status": "cancelled"}
    )

    def wait(_monitor: Any, timeout: float | None) -> bool:
        nonlocal now, published
        now += max(timeout or 0.0, 0.001)
        if not published:
            published = True
            payload["io"]["outputs"]["outbox"] = custom_outbox
            payload["io"]["control"]["ctrl_out"] = custom_control
            with ctx.broker() as writer:
                writer.write(
                    WEFT_GLOBAL_LOG_QUEUE,
                    json.dumps({"tid": tid, "status": "running", "taskspec": payload}),
                )
                writer.write(custom_outbox, stream_body)
                writer.write(custom_control, terminal_body)
        return True

    monkeypatch.setattr(
        events_cmd,
        "time",
        SimpleNamespace(monotonic=lambda: now, time_ns=time.time_ns),
    )
    monkeypatch.setattr(events_cmd.QueueChangeMonitor, "wait", wait)
    emitted = list(events_cmd.iter_task_realtime_events(ctx, tid))
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
    ["snapshot-close", "stream-close", "cancel", "wait-error", "read-error"],
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
            monkeypatch.setattr(
                events_cmd, "peek_terminal_ctrl_out_evidence", fail_read
            )
            with pytest.raises(RuntimeError, match="injected observer read failure"):
                list(iterator)
        else:
            assert next(iterator).event_type == "snapshot"
            if exit_kind == "stream-close":
                assert next(iterator).event_type == "stdout"
    finally:
        close = getattr(iterator, "close", None)
        assert callable(close)
        close()
