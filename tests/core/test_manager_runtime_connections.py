"""Borrowed manager fallback connections preserve registry evidence [SB-0.4]."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from simplebroker import BrokerSession
from tests.helpers.test_backend import active_test_backend, prepare_project_root
from weft._constants import (
    MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.client import WeftClient
from weft.commands import submission
from weft.context import WeftContext, build_context
from weft.core import control_probe, heartbeat, manager_runtime
from weft.core.service_convergence import build_manager_service_payload
from weft.helpers import process_create_time

pytestmark = [pytest.mark.shared]
_TID = "1780000000000000012"


@pytest.fixture
def counted_manager_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[WeftContext, list[Any]]]:
    ctx = build_context(prepare_project_root(tmp_path))
    connections: list[Any] = []
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


def _manager_payload(ctx: WeftContext) -> dict[str, Any]:
    pid = os.getpid()
    return build_manager_service_payload(
        context=ctx,
        tid=_TID,
        name="manager",
        status="active",
        queues={
            "requests": WEFT_SPAWN_REQUESTS_QUEUE,
            "ctrl_in": f"T{_TID}.ctrl_in",
            "ctrl_out": f"T{_TID}.ctrl_out",
            "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
        },
        runtime_handle={
            "runner": "host",
            "kind": "process",
            "id": str(pid),
            "control": {"authority": "host-pid"},
            "observations": {
                "host_processes": [
                    {"pid": pid, "create_time": process_create_time(pid)}
                ],
            },
            "metadata": {},
        },
    )


def _start_manager_probe_responder(
    ctx: WeftContext,
    *,
    count: int,
) -> threading.Thread:
    """Answer event-routed manager PINGs on their named requester queues."""

    def respond() -> None:
        target = ctx.queue(f"T{_TID}.ctrl_in")
        try:
            remaining = count
            deadline = time.monotonic() + 10.0
            while remaining and time.monotonic() < deadline:
                raw = target.read_one()
                if raw is None:
                    time.sleep(0.001)
                    continue
                request = json.loads(str(raw))
                reply = ctx.queue(str(request["reply_to"]))
                try:
                    reply.write(
                        json.dumps(
                            {
                                "command": "PING",
                                "status": "ok",
                                "message": "PONG",
                                "tid": _TID,
                                "request_id": request["request_id"],
                                "task_status": "running",
                                "should_stop": False,
                                "role": "manager",
                                "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                                "ctrl_in": f"T{_TID}.ctrl_in",
                                "ctrl_out": f"T{_TID}.ctrl_out",
                                "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
                                "weft_context": str(ctx.root),
                            }
                        )
                    )
                finally:
                    reply.close()
                remaining -= 1
            if remaining:
                raise AssertionError("manager probe PING did not arrive")
        finally:
            target.close()

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    return thread


def test_mark_manager_stopped_unwinds_session_when_queue_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = False
    exited = False

    class _Context:
        @contextmanager
        def session(self) -> Iterator[None]:
            nonlocal entered, exited
            entered = True
            try:
                yield
            finally:
                exited = True

    def fail_queue(_context: Any) -> None:
        raise RuntimeError("registry setup failed")

    monkeypatch.setattr(manager_runtime, "_registry_queue", fail_queue)

    with pytest.raises(RuntimeError, match="registry setup failed"):
        manager_runtime._mark_manager_stopped(
            cast(WeftContext, _Context()),
            _TID,
            record=None,
        )

    assert entered
    assert exited


@pytest.mark.parametrize("heartbeat_fallback", [False, True])
def test_active_manager_fallback_opens_no_connections(
    counted_manager_connections: tuple[WeftContext, list[Any]],
    heartbeat_fallback: bool,
) -> None:
    ctx, connections = counted_manager_connections
    with ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=True) as owner:
        owner.write(json.dumps(_manager_payload(ctx)))
        with owner.get_connection() as broker:
            before = len(connections)
            for _ in range(3):
                if heartbeat_fallback:
                    with pytest.raises(
                        RuntimeError, match="did not publish a live endpoint"
                    ):
                        heartbeat.ensure_heartbeat_service(
                            ctx, startup_timeout=0, broker=broker
                        )
                else:
                    result = manager_runtime.ensure_manager(ctx, broker=broker)
                    assert result.manager_record is not None
                    assert result.manager_record["tid"] == _TID
                    assert not result.started_here
                    assert result.process_handle is None
            assert len(connections) == before
            assert owner.stats().pending == 1


def test_borrowed_registry_preserves_schema_discard_and_future_guard(
    counted_manager_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_manager_connections
    with ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=True) as owner:
        owner.write(json.dumps(_manager_payload(ctx)))
        v1 = owner.write(json.dumps({"schema": "weft.service_owner.v1"}))
        with owner.get_connection() as broker:
            before = len(connections)
            result = manager_runtime.ensure_manager(ctx, broker=broker)
            assert result.manager_record is not None
            assert result.manager_record["tid"] == _TID
            assert owner.peek_one(exact_timestamp=v1) is None
            v1 = owner.write(json.dumps({"schema": "weft.service_owner.v1"}))
            future = owner.write(json.dumps({"schema": "weft.service_owner.v3"}))
            with pytest.raises(ValueError, match="future service-owner schema"):
                manager_runtime.ensure_manager(ctx, broker=broker)
            assert owner.peek_one(exact_timestamp=v1) is not None
            assert owner.peek_one(exact_timestamp=future) is not None
            assert len(connections) == before


def test_borrowed_manager_probe_routes_reply_and_retires_requester_queue(
    counted_manager_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, connections = counted_manager_connections
    request_id = UUID("12345678-1234-5678-1234-567812345678")
    monkeypatch.setattr(control_probe.uuid, "uuid4", lambda: request_id)
    with ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=True) as owner:
        payload = _manager_payload(ctx)
        payload["runtime_handle"] = {}
        owner.write(json.dumps(payload))
        responder = _start_manager_probe_responder(ctx, count=3)
        with owner.get_connection() as broker:
            before = len(connections)
            for _ in range(3):
                result = manager_runtime.ensure_manager(ctx, broker=broker)
                assert result.manager_record is not None
                assert result.manager_record["tid"] == _TID
                assert not result.started_here
                assert result.process_handle is None
                assert broker.peek_one(f"T{_TID}.ctrl_out") is None
            responder.join(timeout=10.0)
            assert not responder.is_alive()
            assert broker.get_queue_stat(f"T{_TID}.ctrl_in").pending == 0
            assert len(connections) > before
            broker.write("probe.owner", "owner remains usable")


def test_borrowed_probe_watcher_tears_down_inside_open_caller_operation(
    counted_manager_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Guard the nonpersistent ephemeral probe watcher [SB-0.4], [MF-3].

    Submission probes while its own ``session.connection()`` is still open on
    the same thread. A persistent watcher would own a ``BrokerSession`` on the
    same process-session key, and SimpleBroker refuses to close that session
    while the caller's operation is open. The probe still returns a result in
    that case, so only teardown shows the defect.

    Verifies:
    - the probe watcher stops without raising inside the caller's operation
    - watcher cleanup logs no warning
    - the caller's borrowed connection remains usable afterwards
    """
    ctx, _connections = counted_manager_connections
    stop_failures: list[BaseException] = []
    original_stop = control_probe.MultiQueueWatcher.stop

    def recording_stop(watcher: Any, *args: Any, **kwargs: Any) -> None:
        try:
            original_stop(watcher, *args, **kwargs)
        except BaseException as exc:
            stop_failures.append(exc)
            raise

    monkeypatch.setattr(control_probe.MultiQueueWatcher, "stop", recording_stop)

    with (
        caplog.at_level("WARNING", logger="weft.core.tasks.multiqueue_watcher"),
        ctx.session() as session,
        session.connection() as broker,
    ):
        result = control_probe.send_keyed_ping_probe(
            ctx,
            tid=_TID,
            ctrl_in_name=f"T{_TID}.ctrl_in",
            timeout=0.05,
            broker=broker,
        )
        broker.write("probe.owner", "owner remains usable")

    assert result.timed_out
    assert result.error is None
    assert stop_failures == []
    assert [record.getMessage() for record in caplog.records] == []


def test_borrowed_ambiguous_backlog_check_opens_no_connections(
    counted_manager_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_manager_connections
    with (
        ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=True) as owner,
        owner.get_connection() as broker,
    ):
        record = {
            "timestamp": time.time_ns()
            - int(
                (MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS + 1) * 1_000_000_000
            )
        }
        before = len(connections)
        for _ in range(3):
            assert manager_runtime._namespace_ambiguous_incumbent_should_block_start(
                ctx, record, broker=broker
            )
        broker.write(WEFT_SPAWN_REQUESTS_QUEUE, "pending")
        for _ in range(3):
            assert (
                not manager_runtime._namespace_ambiguous_incumbent_should_block_start(
                    ctx, record, broker=broker
                )
            )
        assert len(connections) == before


@pytest.mark.parametrize("ping_ready", [False, True])
def test_prepared_submission_reuses_one_connection_and_releases_with_sibling(
    counted_manager_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    ping_ready: bool,
) -> None:
    """Accepted work and fresh manager proof share one bounded caller core."""
    ctx, connections = counted_manager_connections
    request_id = UUID("12345678-1234-5678-1234-567812345678")
    monkeypatch.setattr(control_probe.uuid, "uuid4", lambda: request_id)
    with ctx.session() as session, session.connection() as broker:
        payload = _manager_payload(ctx)
        if ping_ready:
            payload["runtime_handle"] = {}
        broker.write(WEFT_SERVICES_REGISTRY_QUEUE, json.dumps(payload))
    responder = _start_manager_probe_responder(ctx, count=1) if ping_ready else None
    prepared = submission.prepare_taskspec(
        {
            "name": "connection-probe",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
        payload={"probe": True},
    )
    brokers: list[Any] = []
    connection_counts: list[int] = []
    original_submit = submission.submit_spawn_request
    original_observe = manager_runtime.observe_manager_availability

    def submit(*args: Any, **kwargs: Any) -> int:
        tid = original_submit(*args, **kwargs)
        brokers.append(kwargs["broker"])
        connection_counts.append(len(connections))
        return tid

    def observe(
        context: WeftContext, *, broker: Any | None = None
    ) -> manager_runtime.ManagerAvailabilityObservation:
        brokers.append(broker)
        result = original_observe(context, broker=broker)
        connection_counts.append(len(connections))
        return result

    monkeypatch.setattr(submission, "submit_spawn_request", submit)
    monkeypatch.setattr(manager_runtime, "observe_manager_availability", observe)
    with ctx.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as sibling:
        receipt = submission.submit_prepared(ctx, prepared)
        assert len(brokers) == 2
        assert brokers[0] is not None and brokers[1] is brokers[0]
        if ping_ready:
            assert connection_counts[1] > connection_counts[0]
        else:
            assert connection_counts[1] == connection_counts[0]
        if responder is not None:
            responder.join(timeout=10.0)
            assert not responder.is_alive()
        # PG may retain idle pool connections under the surviving sibling lease.
        # Both backends must recycle the caller's core at submission-scope exit.
        with ctx.session(), sibling.get_connection() as broker:
            assert broker is not brokers[0]
            row = broker.peek_one(
                WEFT_SPAWN_REQUESTS_QUEUE, exact_timestamp=int(receipt.tid)
            )
            assert row is not None
            if ping_ready:
                assert broker.peek_one(f"T{_TID}.ctrl_out") is None
                assert broker.peek_one(f"T{_TID}.ctrl_in") is None


def test_retained_client_reuses_sqlite_core_across_bounded_submissions(
    counted_manager_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, connections = counted_manager_connections
    if ctx.backend_name != "sqlite":
        pytest.skip("physical core count is only a valid SQLite reuse oracle")
    monkeypatch.setattr(
        submission,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="test-ready",
        ),
    )
    client = WeftClient.from_weft_context(ctx)

    with client:
        first = client.submit(
            {
                "name": "retained-first",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            }
        )
        after_first = len(connections)
        second = client.submit(
            {
                "name": "retained-second",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            }
        )
        assert len(connections) == after_first

    with ctx.session() as session, session.connection() as broker:
        assert len(connections) > after_first
        assert (
            broker.peek_one(
                WEFT_SPAWN_REQUESTS_QUEUE,
                exact_timestamp=int(first.tid),
            )
            is not None
        )
        assert (
            broker.peek_one(
                WEFT_SPAWN_REQUESTS_QUEUE,
                exact_timestamp=int(second.tid),
            )
            is not None
        )


def test_retained_postgres_session_returns_each_pool_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if active_test_backend() != "postgres":
        pytest.skip("PostgreSQL checkout proof")
    psycopg_pool = pytest.importorskip("psycopg_pool")
    original_getconn = psycopg_pool.ConnectionPool.getconn
    original_putconn = psycopg_pool.ConnectionPool.putconn
    active = 0
    maximum_active = 0
    get_count = 0
    put_count = 0
    pool_ids: set[int] = set()

    def getconn(pool: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal active, get_count, maximum_active
        connection = original_getconn(pool, *args, **kwargs)
        pool_ids.add(id(pool))
        active += 1
        get_count += 1
        maximum_active = max(maximum_active, active)
        return connection

    def putconn(pool: Any, connection: Any) -> None:
        nonlocal active, put_count
        try:
            original_putconn(pool, connection)
        finally:
            active -= 1
            put_count += 1

    monkeypatch.setattr(psycopg_pool.ConnectionPool, "getconn", getconn)
    monkeypatch.setattr(psycopg_pool.ConnectionPool, "putconn", putconn)
    ctx = build_context(prepare_project_root(tmp_path))
    pool_ids.clear()
    active = maximum_active = get_count = put_count = 0

    ensure_active_counts: list[int] = []

    def ensure(*_args: Any, **_kwargs: Any) -> manager_runtime.ManagerEnsureResult:
        ensure_active_counts.append(active)
        return manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="test-ready",
        )

    monkeypatch.setattr(submission, "ensure_manager_after_submission", ensure)
    client = WeftClient.from_weft_context(ctx)
    with client:
        first = client.submit(
            {
                "name": "retained-pg-first",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            }
        )
        assert active == 0
        second = client.submit(
            {
                "name": "retained-pg-second",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            }
        )
        assert active == 0

    assert ensure_active_counts == [0, 0]
    assert active == 0
    assert get_count == put_count
    assert maximum_active == 1
    assert len(pool_ids) == 1
    with ctx.session() as session, session.connection() as broker:
        assert (
            broker.peek_one(
                WEFT_SPAWN_REQUESTS_QUEUE,
                exact_timestamp=int(first.tid),
            )
            is not None
        )
        assert (
            broker.peek_one(
                WEFT_SPAWN_REQUESTS_QUEUE,
                exact_timestamp=int(second.tid),
            )
            is not None
        )


def test_availability_observes_new_stopped_record(
    counted_manager_connections: tuple[WeftContext, list[Any]],
) -> None:
    """Reuse is based on a fresh registry read, even for a still-live PID."""
    ctx, _connections = counted_manager_connections
    with ctx.session() as session, session.connection() as broker:
        payload = _manager_payload(ctx)
        broker.write(WEFT_SERVICES_REGISTRY_QUEUE, json.dumps(payload))
    assert manager_runtime.observe_manager_availability(ctx).outcome == "ready"
    with ctx.session() as session, session.connection() as broker:
        payload["status"] = "stopped"
        broker.write(WEFT_SERVICES_REGISTRY_QUEUE, json.dumps(payload))
    assert manager_runtime.observe_manager_availability(ctx).outcome == "absent"


def test_prepared_submission_session_exit_failure_retains_accepted_tid(
    counted_manager_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup exception cannot conceal that enqueue already committed."""
    ctx, _connections = counted_manager_connections
    armed = False
    original_exit = BrokerSession.__exit__

    def exit_with_error(self: BrokerSession, *args: Any) -> None:
        nonlocal armed
        original_exit(self, *args)
        if armed:
            armed = False
            raise RuntimeError("submission session cleanup failed")

    def ready(*args: Any, **kwargs: Any) -> manager_runtime.ManagerEnsureResult:
        nonlocal armed
        armed = True
        return manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="test_ready",
        )

    monkeypatch.setattr(BrokerSession, "__exit__", exit_with_error)
    monkeypatch.setattr(submission, "ensure_manager_after_submission", ready)
    prepared = submission.prepare_taskspec(
        {
            "name": "cleanup-probe",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        }
    )
    with pytest.raises(
        RuntimeError, match="submission session cleanup failed"
    ) as error:
        submission.submit_prepared(ctx, prepared)
    with ctx.session() as session, session.connection() as broker:
        rows = list(broker.peek_generator(WEFT_SPAWN_REQUESTS_QUEUE))
    assert len(rows) == 1
    assert isinstance(rows[0], tuple)
    assert f"accepted_tid={rows[0][1]}" in str(error.value)


def test_observation_io_error_preserves_accepted_request(
    counted_manager_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An initial registry failure remains uncertainty after committed enqueue."""
    ctx, _connections = counted_manager_connections

    def fail_read(*args: Any, **kwargs: Any) -> Any:
        raise OSError("registry unavailable")

    monkeypatch.setattr(manager_runtime, "_read_recovery_snapshot", fail_read)
    prepared = submission.prepare_taskspec(
        {
            "name": "read-error-probe",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        }
    )
    receipt = submission.submit_prepared(ctx, prepared)
    with ctx.session(), ctx.queue(WEFT_SPAWN_REQUESTS_QUEUE) as queue:
        rows = list(queue.peek_generator(with_timestamps=True))
        assert len(rows) == 1
        assert isinstance(rows[0], tuple)
        assert str(rows[0][1]) == receipt.tid
