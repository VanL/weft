"""Task-owned connection reuse with real broker operations (Spec: [SB-0.4])."""

from __future__ import annotations

import gc
import json
import sqlite3
import threading
import time
import weakref
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import pytest

from simplebroker import BrokerSession, Config, Queue
from tests.core.test_manager import make_manager_spec
from tests.tasks.test_liveness_monitor import _mapping, _taskspec
from weft._constants import PIPELINE_RUNTIME_METADATA_KEY, WEFT_CONFIG_DEFAULTS
from weft.context import WeftContext, build_context
from weft.core.control_messages import encode_control_message
from weft.core.control_probe import send_keyed_ping_probe
from weft.core.manager import Manager
from weft.core.task_state import task_state_queue_name
from weft.core.tasks import Consumer
from weft.core.tasks.liveness_monitor import LivenessMonitor

pytestmark = [pytest.mark.shared, pytest.mark.timeout(60)]


@pytest.fixture
def counted_connections(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[WeftContext, list[Any]]]:
    """Instrument physical opens, never replace broker outcomes."""
    ctx = build_context(spec_context=workdir)
    connections: list[Any] = []
    if ctx.backend_name == "postgres":
        psycopg = pytest.importorskip("psycopg")
        original_connect = psycopg.Connection.connect.__func__

        def pg_connect(cls: Any, *args: Any, **kwargs: Any) -> Any:
            connection = original_connect(cls, *args, **kwargs)
            connections.append(connection)
            return connection

        monkeypatch.setattr(psycopg.Connection, "connect", classmethod(pg_connect))
        monkeypatch.setattr(psycopg, "connect", psycopg.Connection.connect)
    else:
        original_sqlite_connect = sqlite3.connect

        def sqlite_connect(*args: Any, **kwargs: Any) -> Any:
            connection = original_sqlite_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        monkeypatch.setattr(sqlite3, "connect", sqlite_connect)
    yield ctx, connections
    assert connections
    for connection in connections:
        if ctx.backend_name == "postgres":
            assert connection.closed
        else:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")


def test_consumer_cleanup_releases_thread_core_with_surviving_owner(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, _connections = counted_connections
    with ctx.queue("surviving.owner", persistent=True) as owner:
        with owner.get_connection() as owner_core:
            owner_core.list_queues()

        def run() -> weakref.ReferenceType[Any]:
            task = Consumer(
                ctx.broker_target,
                _taskspec(str(time.time_ns()), ctx.root),
                config=ctx.config,
            )
            try:
                with task._get_connected_queue().get_connection() as core:
                    assert core is not owner_core
                    core.list_queues()
                    reference = weakref.ref(core)
            finally:
                task.stop(join=False)
                task.cleanup()
            assert task._cleanup_errors == ()
            return reference

        with ThreadPoolExecutor(max_workers=1) as executor:
            worker_core = executor.submit(run).result()
        gc.collect()
        assert worker_core() is None
        with owner.get_connection() as current:
            assert current is owner_core
            current.write("surviving.owner", "still usable")
        assert owner.read_one() == "still usable"


def test_consumer_drive_scope_releases_driver_core_after_cross_thread_construction(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _connections = counted_connections
    construction_thread = threading.get_ident()
    recycled_threads: list[int] = []
    original_recycle = BrokerSession.recycle_thread

    def recycle(session: BrokerSession) -> None:
        recycled_threads.append(threading.get_ident())
        original_recycle(session)

    monkeypatch.setattr(BrokerSession, "recycle_thread", recycle)
    task = Consumer(
        ctx.broker_target,
        _taskspec(str(time.time_ns()), ctx.root),
        config=ctx.config,
    )
    assert recycled_threads
    assert set(recycled_threads) == {construction_thread}
    with ctx.queue("drive.scope.keeper", persistent=True) as keeper:
        with keeper.get_connection() as keeper_core:
            keeper_core.list_queues()

        def drive() -> tuple[weakref.ReferenceType[Any], int]:
            with task._get_connected_queue().get_connection() as driver_core:
                assert driver_core is not keeper_core
                driver_core.list_queues()
                reference = weakref.ref(driver_core)
            task.run_until_stopped(poll_interval=0.0, max_iterations=1)
            return reference, threading.get_ident()

        with ThreadPoolExecutor(max_workers=1) as executor:
            driver_reference, driver_thread = executor.submit(drive).result()

        assert driver_thread != construction_thread
        gc.collect()
        assert driver_reference() is None
        assert task._cleanup_errors == ()
        assert task._task_lifecycle.value == "closed"
        with keeper.get_connection() as current:
            assert current is keeper_core


def test_consumer_drive_scope_reuses_one_core_across_successive_turns(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_connections
    task = Consumer(
        ctx.broker_target,
        _taskspec(str(time.time_ns()), ctx.root),
        config=ctx.config,
    )

    with task.drive_scope():
        task.process_once()
        with task._get_connected_queue().get_connection() as first_core:
            first_core.list_queues()
        opened_after_first_turn = len(connections)

        task.process_once()
        with task._get_connected_queue().get_connection() as second_core:
            second_core.list_queues()

        assert second_core is first_core
        assert len(connections) == opened_after_first_turn

    assert task._task_lifecycle.value == "closed"
    assert task._cleanup_errors == ()


def test_consumer_drive_scope_rejects_nested_entry(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, _connections = counted_connections
    task = Consumer(
        ctx.broker_target,
        _taskspec(str(time.time_ns()), ctx.root),
        config=ctx.config,
    )

    with (
        task.drive_scope(),
        pytest.raises(RuntimeError, match="drive scope is reentrant"),
        task.drive_scope(),
    ):
        pass


def test_consumer_drive_scope_rejects_entry_during_active_turn(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, _connections = counted_connections
    task = Consumer(
        ctx.broker_target,
        _taskspec(str(time.time_ns()), ctx.root),
        config=ctx.config,
    )
    task._turn_active = True
    try:
        with (
            pytest.raises(RuntimeError, match="drive scope is reentrant"),
            task.drive_scope(),
        ):
            pass
    finally:
        task._turn_active = False
        task.stop(join=False)


def test_initialization_recycle_failure_unwinds_inventory_session(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _connections = counted_connections
    original_recycle = BrokerSession.recycle_thread
    recycle_calls = 0

    def recycle(session: BrokerSession) -> None:
        nonlocal recycle_calls
        recycle_calls += 1
        if recycle_calls == 2:
            raise RuntimeError("injected initialization recycle failure")
        original_recycle(session)

    monkeypatch.setattr(BrokerSession, "recycle_thread", recycle)
    with pytest.raises(RuntimeError, match="initialization recycle failure"):
        Consumer(
            ctx.broker_target,
            _taskspec(str(time.time_ns()), ctx.root),
            config=ctx.config,
        )

    assert recycle_calls == 2


@pytest.mark.parametrize(
    ("body_failure", "close_failure", "expected"),
    [
        (None, RuntimeError("driver close failed"), RuntimeError),
        (ValueError("body failed"), RuntimeError("driver close failed"), ValueError),
        (ValueError("body failed"), KeyboardInterrupt(), KeyboardInterrupt),
    ],
)
def test_consumer_drive_scope_preserves_session_exit_failure_priority(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    body_failure: BaseException | None,
    close_failure: BaseException,
    expected: type[BaseException],
) -> None:
    ctx, _connections = counted_connections
    task = Consumer(
        ctx.broker_target,
        _taskspec(str(time.time_ns()), ctx.root),
        config=ctx.config,
    )
    inventory_session = task._broker_session
    original_close = BrokerSession.close
    failed_driver_sessions: list[BrokerSession] = []

    def close(session: BrokerSession) -> None:
        if session is inventory_session:
            original_close(session)
            return
        failed_driver_sessions.append(session)
        raise close_failure

    monkeypatch.setattr(BrokerSession, "close", close)
    with pytest.raises(expected) as exc_info, task.drive_scope():
        if body_failure is not None:
            raise body_failure

    if expected is ValueError:
        assert exc_info.value is body_failure
        notes = getattr(exc_info.value, "__notes__", ())
        assert any("driver close failed" in note for note in notes)
    else:
        assert exc_info.value is close_failure
    assert len(failed_driver_sessions) == 1
    original_close(failed_driver_sessions[0])


def test_consumer_drive_scope_finalizes_when_driver_session_entry_fails(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driver-session acquisition failure still owns canonical finalization."""

    ctx, _connections = counted_connections
    task = Consumer(
        ctx.broker_target,
        _taskspec(str(time.time_ns()), ctx.root),
        config=ctx.config,
    )

    @contextmanager
    def fail_session(_context: WeftContext) -> Iterator[BrokerSession]:
        raise RuntimeError("injected driver session entry failure")
        yield  # pragma: no cover - contextmanager shape

    monkeypatch.setattr(WeftContext, "session", fail_session)

    with (
        pytest.raises(RuntimeError, match="driver session entry failure"),
        task.drive_scope(),
    ):
        raise AssertionError("unreachable")

    assert task._task_lifecycle.value == "closed"
    assert task._cleanup_errors == ()
    assert task._broker_session is None


def test_foreign_stop_with_same_key_operation_records_refused_inventory_close(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, _connections = counted_connections
    task = Consumer(
        ctx.broker_target,
        _taskspec(str(time.time_ns()), ctx.root),
        config=ctx.config,
    )
    task.process_once()
    with ctx.queue("foreign.stop.keeper", persistent=True) as keeper:
        with keeper.get_connection() as keeper_core:
            keeper_core.list_queues()

        def stop_from_foreign_operation() -> None:
            with ctx.queue("foreign.stop.operation", persistent=True) as observer:
                observer.write("held")
                rows = observer.peek_generator()
                assert next(rows) == "held"
                try:
                    task.stop(join=False)
                finally:
                    rows.close()

                assert task._task_lifecycle.value == "closed"
                assert task._cleanup_errors
                assert task._broker_session is not None
                task.stop(join=False)
                assert task._cleanup_errors
                with pytest.raises(RuntimeError, match="reactor is closed"):
                    task.process_once()
                # Test teardown explicitly releases the retained handle after
                # proving task lifecycle calls do not retry CLOSED finalization.
                task._broker_session.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(stop_from_foreign_operation).result()

        with keeper.get_connection() as current:
            assert current is keeper_core


def test_foreign_stop_defers_idle_active_scope_finalization_to_owner(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, _connections = counted_connections
    task = Consumer(
        ctx.broker_target,
        _taskspec(str(time.time_ns()), ctx.root),
        config=ctx.config,
    )
    scope_entered = threading.Event()
    release_scope = threading.Event()

    def own_idle_scope() -> None:
        with task.drive_scope():
            scope_entered.set()
            assert release_scope.wait(timeout=5.0)

    owner = threading.Thread(target=own_idle_scope)
    owner.start()
    assert scope_entered.wait(timeout=2.0)

    task.stop(join=False)

    assert task._task_lifecycle.value == "stop_requested"
    assert task._cleanup_errors == ()
    assert task._broker_session is not None

    release_scope.set()
    owner.join(timeout=5.0)
    assert not owner.is_alive()
    assert task._task_lifecycle.value == "closed"
    assert task._cleanup_errors == ()
    assert task._broker_session is None


def test_post_super_initialization_failure_releases_owned_resources(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, _connections = counted_connections

    class FailingConsumer(Consumer):
        def __init__(self) -> None:
            super().__init__(
                ctx.broker_target,
                _taskspec(str(time.time_ns()), ctx.root),
                config=ctx.config,
            )
            with self._initialization_scope():
                self._queue("failing.consumer.extra").has_pending()
                raise RuntimeError("injected post-super initialization failure")

    with pytest.raises(RuntimeError, match="post-super initialization failure"):
        FailingConsumer()


@pytest.mark.parametrize("fail_primary_close", [False, True])
def test_consumer_closes_each_queue_before_inventory_session_after_failure(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    fail_primary_close: bool,
) -> None:
    ctx, _connections = counted_connections
    task = Consumer(
        ctx.broker_target,
        _taskspec(str(time.time_ns()), ctx.root),
        config=ctx.config,
    )
    primary = task._queue_obj
    extra = task._queue("cleanup.extra")
    handles = {id(primary), id(extra)}
    attempts: dict[int, int] = dict.fromkeys(handles, 0)
    session_close_entries: list[dict[int, int]] = []
    original_close = Queue.close
    original_session_close = BrokerSession.close

    def close(queue: Queue) -> None:
        if id(queue) in handles:
            attempts[id(queue)] += 1
        original_close(queue)
        if queue is primary and fail_primary_close:
            raise RuntimeError("injected queue close failure")

    def close_session(session: BrokerSession) -> None:
        session_close_entries.append(dict(attempts))
        original_session_close(session)

    monkeypatch.setattr(Queue, "close", close)
    monkeypatch.setattr(BrokerSession, "close", close_session)
    task.stop(join=False)
    task.cleanup()
    assert session_close_entries == [dict.fromkeys(handles, 1)]
    assert attempts[id(extra)] == 1
    assert attempts[id(primary)] >= 1
    assert task._queue_cache == {}
    assert task._task_lifecycle.value == "closed"
    if fail_primary_close:
        assert task._cleanup_errors
        assert "injected queue close failure" in str(task._cleanup_errors[0])
    else:
        assert task._cleanup_errors == ()


def test_manager_dynamic_control_queues_reuse_task_connection(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_connections
    manager = Manager(
        ctx.broker_target,
        make_manager_spec(str(time.time_ns()), weft_context=str(ctx.root)),
        config=ctx.config,
    )
    try:
        manager._get_connected_queue().has_pending()
        cached_names = set(manager._queue_cache)
        initial_connections = len(connections)
        for index in range(5):
            name = f"connection-test.{index}.ctrl"
            manager._send_child_control_command(name, "STATUS")
            with manager._get_connected_queue().get_connection() as broker:
                observed = broker.peek_one(name)
                assert isinstance(observed, tuple)
                assert observed[0] == encode_control_message("STATUS")
        assert len(connections) == initial_connections
        assert set(manager._queue_cache) == cached_names
        manager._get_connected_queue().write("owner remains usable")
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_liveness_sweeps_reuse_task_connection_and_observe_new_commits(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_connections
    tid = str(time.time_ns())
    monitor = LivenessMonitor(
        ctx.broker_target,
        _taskspec(str(time.time_ns()), ctx.root),
        config=ctx.config,
        mapping_min_age_seconds=0.0,
    )
    try:
        monitor._reconcile_mapping_rows(full=True)
        cached_names = set(monitor._queue_cache)
        for index in range(3):
            # A transient independent owner commits and closes before each sweep.
            with ctx.queue(task_state_queue_name(tid)) as writer:
                message_id = writer.write(json.dumps({**_mapping(tid), "index": index}))
            initial_connections = len(connections)
            monitor._reconcile_mapping_rows(full=False)
            assert monitor._latest_rows[tid].message_id == message_id
            monitor._reconcile_mapping_rows(full=True)
            assert monitor._retire_mapping_row(monitor._latest_rows[tid])
            assert len(connections) == initial_connections
            with ctx.broker() as reader:
                assert reader.peek_one(task_state_queue_name(tid)) is None
        assert set(monitor._queue_cache) == cached_names
        monitor._get_connected_queue().write("owner remains usable")
    finally:
        monitor.stop(join=False)
        monitor.cleanup()


def test_manager_admission_helpers_reuse_connection(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_connections
    manager = Manager(
        ctx.broker_target,
        make_manager_spec(str(time.time_ns()), weft_context=str(ctx.root)),
        config=ctx.config,
    )
    try:
        tid = str(time.time_ns())
        with manager._get_connected_queue().get_connection() as broker:
            broker.write(task_state_queue_name(tid), json.dumps(_mapping(tid)))
        initial_connections = len(connections)
        for _ in range(3):
            usage = manager._observe_admission_usage()
            assert usage is not None and usage >= 1
        assert len(connections) == initial_connections
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_autostart_pipeline_compilation_reuses_owned_connection(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_connections
    config = Config(
        dict(ctx.broker_config), prefix="SNAPSHOT", defaults=WEFT_CONFIG_DEFAULTS
    )
    tasks_dir = ctx.weft_dir / "tasks"
    pipelines_dir = ctx.weft_dir / "pipelines"
    tasks_dir.mkdir(exist_ok=True)
    pipelines_dir.mkdir(exist_ok=True)
    stage_path = tasks_dir / "stage.json"
    stage_payload = {
        "name": "stage",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    stage_path.write_text(json.dumps(stage_payload), encoding="utf-8")
    (pipelines_dir / "reuse.json").write_text(
        json.dumps(
            {
                "name": "reuse",
                "stages": [
                    {"name": f"stage-{index}", "task": "stage"} for index in range(3)
                ],
            }
        ),
        encoding="utf-8",
    )
    manager = Manager(
        ctx.broker_target,
        make_manager_spec(str(time.time_ns()), weft_context=str(ctx.root)),
        config=config,
    )
    try:
        with manager._get_connected_queue().get_connection() as broker:
            previous_id = broker.generate_timestamp()
        compilation_context = manager._autostart_pipeline_context()
        assert compilation_context is not None
        assert compilation_context.broker_config is config
        cached_names = set(manager._queue_cache)
        initial_connections = len(connections)
        for _ in range(3):
            result = manager._load_autostart_pipeline("reuse")
            assert result is not None
            payload, _fallback = result
            runtime = payload["metadata"][PIPELINE_RUNTIME_METADATA_KEY]
            allocated_ids = [int(runtime["pipeline_tid"])]
            for edge, stage in zip(runtime["edges"], runtime["stages"], strict=False):
                allocated_ids.extend((int(edge["tid"]), int(stage["tid"])))
            allocated_ids.append(int(runtime["edges"][-1]["tid"]))
            assert len(allocated_ids) == 8
            assert previous_id < allocated_ids[0]
            assert allocated_ids == sorted(set(allocated_ids))
            with manager._get_connected_queue().get_connection() as broker:
                previous_id = broker.generate_timestamp()
                assert allocated_ids[-1] < previous_id
                broker.insert_messages(
                    [("compiled.ids", str(value), value) for value in allocated_ids]
                )
            assert len(connections) == initial_connections
        # A genuine compile failure must leave the borrowed connection usable.
        stage_path.write_text("{}", encoding="utf-8")
        assert manager._load_autostart_pipeline("reuse") is None
        stage_path.write_text(json.dumps(stage_payload), encoding="utf-8")
        assert manager._load_autostart_pipeline("reuse") is not None
        assert len(connections) == initial_connections
        assert set(manager._queue_cache) == cached_names
        for connection in connections:
            if ctx.backend_name == "postgres":
                if not connection.closed:
                    assert connection.info.transaction_status.name == "IDLE"
            else:
                try:
                    assert not connection.in_transaction
                except sqlite3.ProgrammingError:
                    pass
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_child_seed_and_stale_cleanup_do_not_cache_dynamic_queues(
    counted_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_connections
    manager = Manager(
        ctx.broker_target,
        make_manager_spec(str(time.time_ns()), weft_context=str(ctx.root)),
        config=ctx.config,
    )
    try:
        manager._get_connected_queue().has_pending()
        cached_names = set(manager._queue_cache)
        initial_connections = len(connections)
        for _ in range(3):
            child_tid = str(time.time_ns())
            child = _taskspec(child_tid, ctx.root)
            inbox_name = f"T{child_tid}.inbox"
            assert manager._seed_child_inbox(
                child, inbox_name=inbox_name, inbox_message="payload"
            )
            assert manager._delete_queue_messages_by_id(inbox_name, limit=1) == 1
            assert manager._delete_queue_messages_by_id(inbox_name, limit=1) == 0
        assert set(manager._queue_cache) == cached_names
        assert len(connections) == initial_connections
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_keyed_probe_manual_wait_releases_transient_connections(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, connections = counted_connections
    observations: list[int] = []

    def fail_wait(_watcher: Any, _seconds: float) -> None:
        for connection in connections:
            if ctx.backend_name == "postgres":
                if not connection.closed:
                    assert connection.info.transaction_status.name == "IDLE"
            else:
                try:
                    assert not connection.in_transaction
                except sqlite3.ProgrammingError:
                    pass
        observations.append(len(connections))
        raise RuntimeError("injected wait failure")

    initial_connections = len(connections)
    monkeypatch.setattr(
        "weft.core.control_probe.MultiQueueWatcher.wait_for_activity",
        fail_wait,
    )
    with pytest.raises(RuntimeError, match="injected wait failure"):
        send_keyed_ping_probe(
            ctx,
            tid=str(time.time_ns()),
            ctrl_in_name="probe.in",
            timeout=10.0,
        )
    assert len(observations) == 1
    assert observations[0] > initial_connections
    for connection in connections[initial_connections:]:
        if ctx.backend_name == "postgres":
            assert connection.closed
        else:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")


@pytest.mark.parametrize("outcome", ["matched", "timeout", "error"])
def test_keyed_probe_never_closes_borrowed_broker(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    ctx, connections = counted_connections
    tid = str(time.time_ns())

    def fail_wait(_watcher: Any, _seconds: float) -> None:
        raise RuntimeError("injected wait failure")

    with (
        ctx.queue("probe.owner", persistent=True) as owner,
        owner.get_connection() as broker,
    ):
        responder: threading.Thread | None = None
        if outcome == "matched":

            def respond() -> None:
                target = ctx.queue("probe.in", persistent=False)
                try:
                    deadline = time.monotonic() + 3.0
                    while time.monotonic() < deadline:
                        raw = target.read_one()
                        if raw is None:
                            time.sleep(0.001)
                            continue
                        request = json.loads(str(raw))
                        reply = ctx.queue(request["reply_to"], persistent=False)
                        try:
                            reply.write(
                                json.dumps(
                                    {
                                        "command": "PING",
                                        "status": "ok",
                                        "message": "PONG",
                                        "tid": tid,
                                        "request_id": "borrowed",
                                        "task_status": "running",
                                    }
                                )
                            )
                        finally:
                            reply.close()
                        return
                    raise AssertionError("probe PING did not arrive")
                finally:
                    target.close()

            responder = threading.Thread(target=respond, daemon=True)
            responder.start()
        initial_connections = len(connections)
        if outcome == "error":
            monkeypatch.setattr(
                "weft.core.control_probe.MultiQueueWatcher.wait_for_activity",
                fail_wait,
            )
            with pytest.raises(RuntimeError, match="injected wait failure"):
                send_keyed_ping_probe(
                    ctx,
                    tid=tid,
                    ctrl_in_name="probe.in",
                    request_id="borrowed",
                    timeout=10.0,
                    broker=broker,
                )
        else:
            result = send_keyed_ping_probe(
                ctx,
                tid=tid,
                ctrl_in_name="probe.in",
                request_id="borrowed",
                timeout=1.0 if outcome == "matched" else 0.0,
                broker=broker,
            )
            assert (result.matched is not None) == (outcome == "matched")
            assert result.timed_out == (outcome == "timeout")
            assert result.error is None
        if responder is not None:
            responder.join(timeout=3.0)
            assert not responder.is_alive()
        broker.write("probe.owner", "still open")
        observed = broker.peek_one("probe.owner")
        assert isinstance(observed, tuple) and observed[0] == "still open"
        assert len(connections) >= initial_connections
        for connection in connections[initial_connections:]:
            if ctx.backend_name == "postgres":
                assert connection.closed
            else:
                with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                    connection.execute("SELECT 1")


@pytest.mark.parametrize("kind", ["manager", "service"])
def test_manager_probe_advancement_and_state_helpers_reuse_connection(
    counted_connections: tuple[WeftContext, list[Any]],
    kind: Literal["manager", "service"],
) -> None:
    ctx, connections = counted_connections
    manager = Manager(
        ctx.broker_target,
        make_manager_spec(str(time.time_ns()), weft_context=str(ctx.root)),
        config=ctx.config,
    )
    tid = str(time.time_ns())
    try:
        with manager._get_connected_queue().get_connection() as broker:
            broker.write(task_state_queue_name(tid), json.dumps(_mapping(tid)))
        # Prime the control drain's backend resources before measuring whether
        # repeated probe advancement opens additional connections.
        manager._drain_control_queue_first()
        cached_names = set(manager._queue_cache)
        initial_connections = len(connections)
        for _ in range(3):
            handle = manager._latest_tid_runtime_handle(tid)
            assert handle is not None
            assert handle.id == "runtime-1"
        record = {"tid": tid, "ctrl_in": "target.in", "ctrl_out": "target.out"}
        now_ns = time.time_ns()
        if kind == "manager":
            proof = manager._manager_pong_dispatch_proof(record, now_ns=now_ns)
            assert proof.reason == "ping_pending"
            pending = manager._leader_probe_pending[tid]
            request_id = pending.request_id
            for _ in range(3):
                proof = manager._manager_pong_dispatch_proof(record, now_ns=now_ns)
                assert proof.reason == "ping_pending"
        else:
            candidate = manager._service_pong_candidate(
                service_key="test-service",
                tid=tid,
                timestamp=None,
                metadata={},
                ctrl_in_name="target.in",
                source="control-pong",
            )
            assert candidate is not None and candidate.reason == "ping_pending"
            service_pending = next(
                probe
                for probe in manager._service_probe_pending.values()
                if probe.service_key == "test-service"
            )
            request_id = service_pending.request_id
            for _ in range(3):
                candidate = manager._advance_service_pong_probe(
                    service_pending,
                    timestamp=None,
                    metadata={},
                    resolve_unanswered=False,
                )
                assert candidate is not None and candidate.reason == "ping_pending"
        with manager._get_connected_queue().get_connection() as broker:
            broker.write(
                manager._queue_names["ctrl_in"],
                json.dumps(
                    {
                        "command": "PING",
                        "status": "ok",
                        "message": "PONG",
                        "tid": tid,
                        "request_id": request_id,
                        "task_status": "running",
                    }
                ),
            )
        if kind == "manager":
            manager._drain_control_queue_first()
            proof = manager._manager_pong_dispatch_proof(
                record,
                now_ns=now_ns,
                consume_stored=True,
            )
            assert proof.liveness == "live"
        else:
            manager._drain_control_queue_first()
            service_pending = manager._service_probe_pending[service_pending.key]
            candidate = manager._advance_service_pong_probe(
                service_pending,
                timestamp=None,
                metadata={},
                resolve_unanswered=False,
            )
            assert candidate is not None and candidate.state == "live"
        manager._cleanup_stale_internal_reserved_queues(force=True)
        assert len(connections) == initial_connections
        assert set(manager._queue_cache) == cached_names
    finally:
        manager.stop(join=False)
        manager.cleanup()
