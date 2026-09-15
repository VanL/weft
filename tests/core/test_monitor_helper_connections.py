"""Monitor helpers reuse task-owned sessions with bounded handles [SB-0.4]."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, WEFT_STREAMING_SESSIONS_QUEUE
from weft.context import WeftContext
from weft.core.monitor import runtime as monitor_runtime
from weft.core.monitor.policies.dead_task import fetch_dead_task_log_coalesce_group
from weft.core.monitor.store import MonitorRawMessageRef
from weft.core.monitor.task_log_scanner import GeneratorTaskLogScanner
from weft.core.pruning import retention, runtime
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.core.queue_window import iter_broker_queue_entries, scan_queue_window
from weft.core.task_state import task_state_queue_name
from weft.helpers import closing_queue_iterator

pytestmark = [pytest.mark.shared]

_TID = "1780000000000000000"


@pytest.fixture
def physical_connections(
    weft_harness: WeftTestHarness, monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[Any]]:
    connections: list[Any] = []
    if weft_harness.context.backend_name == "postgres":
        psycopg = pytest.importorskip("psycopg")
        original_connect = psycopg.Connection.connect.__func__

        def connect(cls: Any, *args: Any, **kwargs: Any) -> Any:
            connection = original_connect(cls, *args, **kwargs)
            connections.append(connection)
            return connection

        monkeypatch.setattr(psycopg.Connection, "connect", classmethod(connect))
        monkeypatch.setattr(psycopg, "connect", psycopg.Connection.connect)
    yield connections
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize(
    "helper",
    [
        "scanner",
        "window",
        "coalesce",
        "runtime_rows",
        "retention_rows",
        "snapshot",
        "snapshot_active",
    ],
)
@pytest.mark.parametrize("borrowed", [False, True])
def test_monitor_read_helpers_reuse_and_close_local_handles(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    physical_connections: list[Any],
    helper: str,
    borrowed: bool,
) -> None:
    ctx = weft_harness.context
    with (
        ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner,
        ExitStack() as scope,
    ):
        expected = [
            owner.write(
                json.dumps(
                    {
                        "tid": _TID,
                        "status": "running"
                        if helper == "snapshot_active"
                        else "completed",
                        "index": i,
                    }
                )
            )
            for i in range(3)
        ]
        original_queue = WeftContext.queue
        acquired: list[Queue] = []
        closed: list[Queue] = []
        persistence: list[bool] = []

        def queue(self: WeftContext, name: str, *, persistent: bool = False) -> Queue:
            result = original_queue(self, name, persistent=persistent)
            acquired.append(result)
            persistence.append(persistent)
            original_close = result.close

            def close() -> None:
                closed.append(result)
                original_close()

            monkeypatch.setattr(result, "close", close)
            return result

        monkeypatch.setattr(WeftContext, "queue", queue)
        broker = scope.enter_context(owner.get_connection()) if borrowed else None
        for _ in range(3):
            before_connections = len(physical_connections)
            before_handles = len(acquired)
            if helper == "scanner":
                window = GeneratorTaskLogScanner().scan_window(
                    ctx, WEFT_GLOBAL_LOG_QUEUE, scan_limit=2, broker=broker
                )
                assert [row.raw.message_id for row in window.rows] == expected[:2]
                assert window.scan_limit_reached
            elif helper == "window":
                rows = scan_queue_window(
                    ctx, WEFT_GLOBAL_LOG_QUEUE, limit=2, broker=broker
                )
                assert [row.message_id for row in rows] == expected[:2]
            elif helper == "coalesce":
                group = fetch_dead_task_log_coalesce_group(
                    ctx, _TID, chunk_limit=1, broker=broker
                )
                assert [row.message_id for row in group.rows] == expected
                assert group.api_matches == 3
            elif helper == "runtime_rows":
                assert (
                    runtime._read_runtime_queue(
                        ctx, WEFT_GLOBAL_LOG_QUEUE, broker=broker
                    )[1]
                    == 3
                )
                assert runtime._latest_task_statuses_from_log(ctx, broker=broker) == {
                    _TID: "completed"
                }
            elif helper == "retention_rows":
                assert retention._read_task_log_rows(ctx, broker=broker)[1] == 3
                assert [
                    message_id
                    for _body, message_id in retention._read_raw_queue(
                        ctx, WEFT_GLOBAL_LOG_QUEUE, broker=broker
                    )
                ] == expected
            else:
                snapshot = monitor_runtime.build_task_monitor_cycle_snapshot(
                    ctx, broker=broker
                )
                assert snapshot.events_scanned == 3
                assert snapshot.tids_seen == 1
            # A new handle validates its project schema once; data sessions stay owned.
            assert len(physical_connections) - before_connections <= (
                len(acquired) - before_handles
            )
            assert acquired == closed
            if borrowed:
                assert len(physical_connections) == before_connections
                assert acquired == []
        assert not persistence if borrowed else persistence and all(persistence)
        assert owner.stats().pending == 3
        owner.write("owner remains usable")


@pytest.mark.parametrize("exact_status,reconcile", [(True, False), (False, True)])
@pytest.mark.parametrize("borrowed", [False, True])
def test_prune_apply_reuses_session_for_dynamic_non_outbox_queues(
    weft_harness: WeftTestHarness,
    physical_connections: list[Any],
    exact_status: bool,
    reconcile: bool,
    borrowed: bool,
) -> None:
    ctx = weft_harness.context
    with (
        ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner,
        ExitStack() as scope,
    ):
        owner.write("keep owner active")
        broker = scope.enter_context(owner.get_connection()) if borrowed else None
        for index in range(4):
            name = f"T{int(_TID) + index}.ctrl_out"
            with ctx.queue(name, persistent=True) as writer:
                present = writer.write("present")
                missing = writer.write("removed after selection")
                assert writer.delete(message_id=missing)
                candidates = [
                    MonitorRawMessageRef(queue=name, message_id=message_id, tid=_TID)
                    for message_id in (present, missing)
                ]
                before = len(physical_connections)
                results = apply_exact_prune_candidates(
                    ctx,
                    candidates,
                    exact_status=exact_status,
                    reconcile_missing=reconcile,
                    apply_result=lambda candidate, deleted, error: (deleted, error),
                    broker=broker,
                )
                assert results == [(True, None), (reconcile, None)]
                assert len(physical_connections) <= before + (0 if borrowed else 1)
                assert writer.stats().pending == 0
        owner.write("owner remains usable")


@pytest.mark.parametrize("borrowed", [False, True])
def test_runtime_prune_borrows_persistent_state_reader_connection(
    weft_harness: WeftTestHarness,
    physical_connections: list[Any],
    borrowed: bool,
) -> None:
    ctx = weft_harness.context
    with (
        ctx.queue(WEFT_STREAMING_SESSIONS_QUEUE, persistent=True) as owner,
        ExitStack() as scope,
    ):
        for _ in range(2):
            owner.write(json.dumps({"tid": _TID, "session_id": "live-session"}))
        with ctx.queue(task_state_queue_name(_TID), persistent=True) as state:
            state.write(json.dumps({"full": _TID, "short": "live", "terminal": False}))
        broker = scope.enter_context(owner.get_connection()) if borrowed else None
        for _ in range(3):
            before = len(physical_connections)
            result = runtime.run_runtime_prune_for_context(
                ctx,
                runtime.RuntimePruneConfig(
                    queues=(
                        "streaming",
                        "endpoints",
                        "managers",
                        "services",
                        "pipelines",
                    )
                    if borrowed
                    else ("streaming",),
                    apply=True,
                    min_age_seconds=0,
                ),
                broker=broker,
            )
            assert result.errors == ()
            assert result.records_scanned == 2
            assert result.candidates == ()
            assert owner.stats().pending == 2
            # Runtime rows, log status, and task state each own one bounded handle.
            assert len(physical_connections) <= before + (0 if borrowed else 3)


def test_retention_prune_borrows_through_task_local_evidence_and_apply(
    weft_harness: WeftTestHarness,
    physical_connections: list[Any],
) -> None:
    ctx = weft_harness.context
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner:
        for _ in range(3):
            owner.write(json.dumps({"tid": _TID, "status": "completed"}))
        with ctx.queue(f"T{_TID}.ctrl_in", persistent=True) as ctrl:
            ctrl.write("STOP")
        with owner.get_connection() as broker:
            before = len(physical_connections)
            for apply in (False, True):
                result = retention.run_retention_prune_for_context(
                    ctx,
                    retention.RetentionPruneConfig(
                        family="retention",
                        task_filters=(_TID,),
                        min_age_seconds=0,
                        require_archive=False,
                        apply=apply,
                    ),
                    broker=broker,
                )
                assert result.errors == ()
                assert result.candidates
                if apply:
                    assert result.deleted > 0
                assert len(physical_connections) == before
            owner.write("still owned")


@pytest.mark.parametrize("helper", ["scanner", "window", "snapshot", "consumer_error"])
def test_borrowed_scan_closes_real_generator_on_early_exit(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    physical_connections: list[Any],
    helper: str,
) -> None:
    ctx = weft_harness.context
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner:
        for _ in range(3):
            owner.write(json.dumps({"tid": _TID, "status": "completed"}))
        with owner.get_connection() as broker:
            original_peek = broker.peek_generator
            closed: list[str] = []

            def peek(name: str, **kwargs: Any) -> Iterator[Any]:
                with closing_queue_iterator(original_peek(name, **kwargs)) as rows:
                    try:
                        yield from rows
                    finally:
                        closed.append(name)

            monkeypatch.setattr(broker, "peek_generator", peek)
            before = len(physical_connections)
            if helper == "scanner":
                GeneratorTaskLogScanner().scan_window(
                    ctx, WEFT_GLOBAL_LOG_QUEUE, scan_limit=1, broker=broker
                )
            elif helper == "window":
                scan_queue_window(ctx, WEFT_GLOBAL_LOG_QUEUE, limit=1, broker=broker)
            elif helper == "snapshot":
                monitor_runtime.build_task_monitor_cycle_snapshot(
                    ctx, limit=1, broker=broker
                )
            else:
                with (
                    pytest.raises(RuntimeError, match="consumer failed"),
                    closing_queue_iterator(
                        iter_broker_queue_entries(broker, WEFT_GLOBAL_LOG_QUEUE)
                    ) as rows,
                ):
                    next(iter(rows))
                    raise RuntimeError("consumer failed")
            assert closed == [WEFT_GLOBAL_LOG_QUEUE]
            assert len(physical_connections) == before
            owner.write("still owned")


@pytest.mark.parametrize("strict", [False, True])
def test_borrowed_iterator_preserves_creation_failure_policy(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    strict: bool,
) -> None:
    ctx = weft_harness.context
    with (
        ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner,
        owner.get_connection() as broker,
    ):

        def fail_open(*args: Any, **kwargs: Any) -> Iterator[Any]:
            raise RuntimeError("injected iterator creation failure")

        monkeypatch.setattr(broker, "peek_generator", fail_open)
        rows = iter_broker_queue_entries(broker, WEFT_GLOBAL_LOG_QUEUE, strict=strict)
        if strict:
            with pytest.raises(
                RuntimeError, match="injected iterator creation failure"
            ):
                list(rows)
        else:
            assert list(rows) == []
        owner.write("still owned")


def test_coalesce_failure_closes_local_handle_without_closing_task_owner(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    physical_connections: list[Any],
) -> None:
    ctx = weft_harness.context
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner:
        owner.write(json.dumps({"tid": _TID}))
        original_queue = WeftContext.queue
        acquired: list[Queue] = []
        closed: list[Queue] = []

        def queue(self: WeftContext, name: str, *, persistent: bool = False) -> Queue:
            result = original_queue(self, name, persistent=persistent)
            acquired.append(result)
            original_close = result.close

            def close() -> None:
                closed.append(result)
                original_close()

            monkeypatch.setattr(result, "close", close)
            return result

        with owner.get_connection() as broker:
            original_find = type(broker).find_message_ids

            def fail_lookup(self: Any, *args: Any, **kwargs: Any) -> Any:
                original_find(self, *args, **kwargs)
                raise RuntimeError("injected lookup failure")

            monkeypatch.setattr(type(broker), "find_message_ids", fail_lookup)
        monkeypatch.setattr(WeftContext, "queue", queue)
        before = len(physical_connections)
        with pytest.raises(RuntimeError, match="injected lookup failure"):
            fetch_dead_task_log_coalesce_group(ctx, _TID, chunk_limit=1)
        assert acquired == closed
        assert len(acquired) == 1
        assert len(physical_connections) <= before + 1
        owner.write("still owned")
        assert owner.stats().pending == 2


@pytest.mark.parametrize("exact_status", [False, True])
def test_prune_apply_reports_local_connection_acquisition_failure(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    exact_status: bool,
) -> None:
    ctx = weft_harness.context
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner:
        message_id = owner.write("keep on failure")
        original_connection = Queue.get_connection
        original_close = Queue.close
        closed: list[Queue] = []

        def connection(self: Queue) -> Any:
            if self is not owner:
                raise RuntimeError("injected connection acquisition failure")
            return original_connection(self)

        def close(self: Queue) -> None:
            closed.append(self)
            original_close(self)

        monkeypatch.setattr(Queue, "get_connection", connection)
        monkeypatch.setattr(Queue, "close", close)
        results = apply_exact_prune_candidates(
            ctx,
            [
                MonitorRawMessageRef(
                    queue=WEFT_GLOBAL_LOG_QUEUE, message_id=message_id, tid=_TID
                )
            ],
            exact_status=exact_status,
            apply_result=lambda candidate, deleted, error: (deleted, error),
        )
        assert results == [(False, "injected connection acquisition failure")]
        assert len(closed) == 1
        assert closed[0] is not owner
        assert owner.stats().pending == 1
