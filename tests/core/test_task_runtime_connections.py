"""Task-owned connection reuse with real broker operations (Spec: [SB-0.4])."""

from __future__ import annotations

import gc
import json
import sqlite3
import time
import weakref
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

import pytest

from simplebroker import Config, Queue
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


@pytest.mark.parametrize("failure", ["none", "cleanup", "close", "both"])
def test_consumer_queue_cleanup_attempts_each_handle_once_after_failure(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
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
    attempts: dict[int, list[str]] = {key: [] for key in handles}
    original_cleanup = Queue.cleanup_connections
    original_close = Queue.close

    def cleanup(queue: Queue) -> None:
        if id(queue) in handles:
            attempts[id(queue)].append("cleanup")
        original_cleanup(queue)
        if queue is primary and failure in {"cleanup", "both"}:
            raise RuntimeError("injected connection cleanup failure")

    def close(queue: Queue) -> None:
        if id(queue) in handles:
            attempts[id(queue)].append("close")
        original_close(queue)
        if queue is primary and failure in {"close", "both"}:
            raise RuntimeError("injected queue close failure")

    monkeypatch.setattr(Queue, "cleanup_connections", cleanup)
    monkeypatch.setattr(Queue, "close", close)
    task.stop(join=False)
    task.cleanup()
    assert attempts == {key: ["cleanup", "close"] for key in handles}
    assert task._queue_cache == {}
    assert task._task_lifecycle.value == "closed"
    if failure == "none":
        assert task._cleanup_errors == ()
    else:
        assert len(task._cleanup_errors) == 1
        if failure in {"cleanup", "both"}:
            assert "injected connection cleanup failure" in caplog.text
        if failure in {"close", "both"}:
            assert "injected queue close failure" in caplog.text


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
            manager._send_child_control_command(name, "PING")
            message = encode_control_message("PING")
            message_id = manager._find_exact_probe_message_id(name, message)
            assert message_id is not None
            manager._delete_exact_probe_message(name, message_id)
            assert manager._find_exact_probe_message_id(name, message) is None
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


def test_keyed_probe_wait_retains_connection_between_polls(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, connections = counted_connections
    observations: list[int] = []

    def observe_wait(_seconds: float) -> None:
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
        if len(observations) == 3:
            raise RuntimeError("injected wait failure")

    monkeypatch.setattr("weft.core.control_probe.time.sleep", observe_wait)
    result = send_keyed_ping_probe(
        ctx,
        tid=str(time.time_ns()),
        ctrl_in_name="probe.in",
        ctrl_out_name="probe.out",
        timeout=10.0,
    )
    assert result.error == "injected wait failure"
    assert len(observations) == 3
    assert observations == [observations[0]] * 3


@pytest.mark.parametrize("outcome", ["matched", "timeout", "error"])
def test_keyed_probe_never_closes_borrowed_broker(
    counted_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    ctx, connections = counted_connections
    tid = str(time.time_ns())

    def fail_wait(_seconds: float) -> None:
        raise RuntimeError("injected wait failure")

    monkeypatch.setattr("weft.core.control_probe.time.sleep", fail_wait)
    with (
        ctx.queue("probe.owner", persistent=True) as owner,
        owner.get_connection() as broker,
    ):
        if outcome == "matched":
            broker.write(
                "probe.out",
                json.dumps(
                    {
                        "command": "PING",
                        "status": "ok",
                        "message": "PONG",
                        "tid": tid,
                        "request_id": "borrowed",
                        "task_status": "running",
                    }
                ),
            )
        initial_connections = len(connections)
        result = send_keyed_ping_probe(
            ctx,
            tid=tid,
            ctrl_in_name="probe.in",
            ctrl_out_name="probe.out",
            request_id="borrowed",
            timeout=10.0 if outcome == "error" else 0.0,
            broker=broker,
        )
        assert (result.matched is not None) == (outcome == "matched")
        assert result.timed_out == (outcome == "timeout")
        assert (result.error is not None) == (outcome == "error")
        assert broker.peek_one("probe.out") is None
        broker.write("probe.owner", "still open")
        observed = broker.peek_one("probe.owner")
        assert isinstance(observed, tuple) and observed[0] == "still open"
        assert len(connections) == initial_connections


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
                ctrl_out_name="target.out",
                source="control-pong",
            )
            assert candidate is not None and candidate.reason == "ping_pending"
            service_pending = next(iter(manager._service_probe_pending.values()))
            request_id = service_pending.request_id
            for _ in range(3):
                candidate = manager._advance_service_pong_probe(
                    service_pending, timestamp=None, metadata={}, now_ns=now_ns
                )
                assert candidate is not None and candidate.reason == "ping_pending"
        with manager._get_connected_queue().get_connection() as broker:
            broker.write(
                "target.out",
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
            proof = manager._manager_pong_dispatch_proof(record, now_ns=now_ns)
            assert proof.liveness == "live"
        else:
            candidate = manager._advance_service_pong_probe(
                service_pending, timestamp=None, metadata={}, now_ns=now_ns
            )
            assert candidate is not None and candidate.state == "live"
        with manager._get_connected_queue().get_connection() as broker:
            assert broker.peek_one("target.out") is None
        manager._sweep_probe_reply_rows("target.out", request_id)
        manager._cleanup_stale_internal_reserved_queues(force=True)
        assert len(connections) == initial_connections
        assert set(manager._queue_cache) == cached_names
    finally:
        manager.stop(join=False)
        manager.cleanup()
