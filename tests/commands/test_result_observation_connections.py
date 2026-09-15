"""Result observers reuse connections without retaining snapshots [SB-0.4]."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest
from psycopg.pq import TransactionStatus

from simplebroker import Queue, QueueWatcher
from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands import result
from weft.context import WeftContext, build_context
from weft.core.queue_wait import QueueChangeMonitor

pytestmark = [pytest.mark.shared]
_TID = "1780000000000000012"


def _assert_idle_pg_connections(ctx: WeftContext, connections: list[Any]) -> None:
    """A retained operation lease must not retain a transaction while waiting."""
    if ctx.backend_name == "postgres":
        live_connections = [conn for conn in connections if not conn.closed]
        assert live_connections
        assert all(
            conn.info.transaction_status == TransactionStatus.IDLE
            for conn in live_connections
        )


@pytest.fixture
def counted_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[WeftContext, list[Any]]]:
    ctx = build_context(prepare_project_root(tmp_path))
    connections: list[Any] = []
    original_drain = QueueWatcher._drain_queue
    original_start = QueueWatcher.run_in_thread
    initialized: dict[QueueWatcher, Event] = {}

    def drain(watcher: QueueWatcher) -> None:
        original_drain(watcher)
        initialized[watcher].set()

    def start(watcher: QueueWatcher) -> Thread:
        initialized[watcher] = Event()
        thread = original_start(watcher)
        # Fallback watcher cores initialize asynchronously, outside the poll loop.
        assert initialized[watcher].wait(10)
        return thread

    monkeypatch.setattr(QueueWatcher, "_drain_queue", drain)
    monkeypatch.setattr(QueueWatcher, "run_in_thread", start)
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
    yield ctx, connections
    for connection in connections:
        if ctx.backend_name == "postgres":
            assert connection.closed
        else:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")


@pytest.mark.parametrize(
    "surface", ["taskspec", "outbox", "ctrl", "pipeline", "one-shot", "persistent"]
)
def test_result_wait_reuses_connections_and_sees_independent_late_write(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    ctx, connections = counted_connections
    ready, publish, published, finish = Event(), Event(), Event(), Event()
    payload = {
        "tid": _TID,
        "spec": {"persistent": surface == "persistent"},
        "io": {"outputs": {"outbox": "custom.outbox"}},
    }

    def writer() -> None:
        with (
            ctx.queue("writer", persistent=True) as queue,
            queue.get_connection() as broker,
        ):
            ready.set()
            assert publish.wait(10)
            if surface == "taskspec":
                broker.write(
                    WEFT_GLOBAL_LOG_QUEUE,
                    json.dumps({"tid": _TID, "taskspec": payload}),
                )
            elif surface == "pipeline":
                broker.write(f"P{_TID}.status", "ready")
            elif surface == "ctrl":
                broker.write(f"T{_TID}.ctrl_out", "ready")
            else:
                name = f"T{_TID}.outbox" if surface == "outbox" else "custom.outbox"
                broker.write(name, "late-result")
                if surface in {"one-shot", "persistent"}:
                    broker.write(
                        WEFT_GLOBAL_LOG_QUEUE,
                        json.dumps({"tid": _TID, "status": "completed"}),
                    )
            published.set()
            assert finish.wait(10)

    original_wait = QueueChangeMonitor.wait
    counts: list[int] = []

    def wait(monitor: QueueChangeMonitor, timeout: float | None) -> bool:
        counts.append(len(connections))
        assert counts[-1] == counts[0], "observer reconnected after warm-up"
        if len(counts) == 3:
            publish.set()
            assert published.wait(10)
            assert len(connections) == counts[0]
        _assert_idle_pg_connections(ctx, connections)
        return original_wait(monitor, timeout)

    monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(writer)
        try:
            assert ready.wait(10)
            if surface in {"one-shot", "persistent"}:
                status, value, error = result._await_single_result(
                    ctx,
                    _TID,
                    timeout=5,
                    show_stderr=False,
                    taskspec_payload=payload,
                )
                assert (status, value, error) == ("completed", "late-result", None)
            else:
                observed = result._await_result_materialization(ctx, _TID, timeout=5)
                assert observed is not None
                if surface == "taskspec":
                    assert observed.taskspec_payload == payload
                    assert observed.outbox_name == "custom.outbox"
                else:
                    prefix = "P" if surface == "pipeline" else "T"
                    assert observed.outbox_name == f"{prefix}{_TID}.outbox"
                    assert observed.result_surface_had_activity
            assert len(counts) >= 3
            assert len(connections) == counts[0]
        finally:
            publish.set()
            finish.set()
            future.result(timeout=10)


@pytest.mark.parametrize("failure", ["timeout", "wait", "constructor", "close"])
def test_result_materialization_releases_resources_on_timeout_or_failure(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    ctx, connections = counted_connections
    if failure == "wait":

        def fail_wait(_monitor: QueueChangeMonitor, _timeout: float | None) -> bool:
            raise RuntimeError("wait boundary failed")

        monkeypatch.setattr(QueueChangeMonitor, "wait", fail_wait)
    elif failure == "constructor":

        def fail_start(_monitor: QueueChangeMonitor, _queues: list[Queue]) -> bool:
            raise RuntimeError("constructor boundary failed")

        monkeypatch.setattr(QueueChangeMonitor, "_start_multi_queue_waiter", fail_start)
    elif failure == "close":
        original_close = QueueChangeMonitor.close

        def fail_close(monitor: QueueChangeMonitor) -> None:
            original_close(monitor)
            raise RuntimeError("close boundary failed")

        monkeypatch.setattr(QueueChangeMonitor, "close", fail_close)

    if failure == "timeout":
        assert result._await_result_materialization(ctx, _TID, timeout=0) is None
    else:
        with pytest.raises(RuntimeError, match=f"{failure} boundary failed"):
            result._await_result_materialization(
                ctx, _TID, timeout=5 if failure == "wait" else 0
            )
    assert connections
