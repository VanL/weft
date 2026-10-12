"""Result observers reuse connections without retaining snapshots [SB-0.4]."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from psycopg.pq import TransactionStatus

from simplebroker import Queue, QueueWatcher
from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft._exceptions import CommandExecutionError, CommandTimeoutError
from weft.commands import result
from weft.context import WeftContext, build_context
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.taskspec import TaskSpec

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
    original_monitor_init = QueueChangeMonitor.__init__
    initialized: dict[QueueWatcher, Event] = {}

    def drain(watcher: QueueWatcher) -> None:
        original_drain(watcher)
        initialized.setdefault(watcher, Event()).set()

    def monitor_init(
        monitor: QueueChangeMonitor,
        queues: Sequence[Queue],
        *,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        original_monitor_init(monitor, queues, config=config)
        try:
            # The monitor owns run_forever threads. Await each actual fallback
            # initial drain before counting post-warmup connections.
            for watcher in monitor._watchers:
                assert initialized.setdefault(watcher, Event()).wait(10)
        except BaseException:
            monitor.close()
            raise

    monkeypatch.setattr(QueueWatcher, "_drain_queue", drain)
    monkeypatch.setattr(QueueChangeMonitor, "__init__", monitor_init)
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
    custom = surface in {"one-shot", "persistent"}
    outbox_name = (
        "custom.outbox"
        if custom
        else f"{'P' if surface == 'pipeline' else 'T'}{_TID}.outbox"
    )
    payload = {
        "tid": _TID,
        "name": "late-connection-result",
        "spec": {
            "type": "function",
            "persistent": surface == "persistent",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
        "io": {
            "outputs": {"outbox": outbox_name},
            "control": {
                "ctrl_out": f"{'P' if surface == 'pipeline' else 'T'}{_TID}.ctrl_out"
            },
        },
        "metadata": {"role": "pipeline"} if surface == "pipeline" else {},
    }
    accepted = (
        TaskSpec.model_validate(payload) if custom or surface == "pipeline" else None
    )

    def writer() -> None:
        with (
            ctx.queue("writer", persistent=True) as queue,
            queue.get_connection() as broker,
        ):
            ready.set()
            for _turn in range(2):
                assert publish.wait(10)
                if finish.is_set():
                    return
                publish.clear()
                broker.write(WEFT_GLOBAL_LOG_QUEUE, json.dumps({"tid": "unrelated"}))
                published.set()
            assert publish.wait(10)
            if finish.is_set():
                return
            publish.clear()
            if surface == "ctrl":
                broker.write(f"T{_TID}.ctrl_out", "ready")
            elif surface == "pipeline":
                broker.write(f"P{_TID}.status", "ready")
            broker.write(
                WEFT_GLOBAL_LOG_QUEUE, json.dumps({"tid": _TID, "taskspec": payload})
            )
            broker.write(outbox_name, "late-result")
            broker.write(
                WEFT_GLOBAL_LOG_QUEUE, json.dumps({"tid": _TID, "status": "completed"})
            )
            published.set()
            assert finish.wait(10)

    original_wait = QueueChangeMonitor.wait
    counts: list[int] = []

    def wait(monitor: QueueChangeMonitor, timeout: float | None) -> bool:
        counts.append(len(connections))
        assert counts[-1] == counts[0], "observer reconnected after warm-up"
        if len(counts) <= 3:
            published.clear()
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
            observed = result.await_task_result(
                ctx, _TID, timeout=5, _accepted_taskspec=accepted
            )
            assert (observed.status, observed.value, observed.error) == (
                "completed",
                "late-result",
                None,
            )
            assert len(counts) >= 3
            assert len(connections) == counts[0]
        finally:
            finish.set()
            publish.set()
            future.result(timeout=10)


@pytest.mark.parametrize("failure", ["timeout", "wait", "constructor", "close"])
def test_result_collection_releases_resources_on_timeout_or_failure(
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

        def fail_start(_monitor: QueueChangeMonitor, queues: list[Queue]) -> bool:
            # Subscription now precedes durable reads. Exercise partial real
            # connection acquisition before the injected constructor failure.
            with queues[0].get_connection() as broker:
                broker.latest_pending_timestamp(queues[0].name)
            raise RuntimeError("constructor boundary failed")

        monkeypatch.setattr(QueueChangeMonitor, "_start_multi_queue_waiter", fail_start)
    elif failure == "close":
        original_close = QueueChangeMonitor.close

        def fail_close(monitor: QueueChangeMonitor) -> None:
            original_close(monitor)
            raise RuntimeError("close boundary failed")

        monkeypatch.setattr(QueueChangeMonitor, "close", fail_close)

    if failure in {"timeout", "close"}:
        with pytest.raises(CommandTimeoutError) as timeout_info:
            result.await_task_result(ctx, _TID, timeout=0)
        if failure == "close":
            assert any(
                "close boundary failed" in note for note in timeout_info.value.__notes__
            )
    else:
        with pytest.raises(
            CommandExecutionError, match=f"{failure} boundary failed"
        ) as failure_info:
            result.await_task_result(ctx, _TID, timeout=5 if failure == "wait" else 0)
        assert isinstance(failure_info.value.__cause__, RuntimeError)
    assert connections
