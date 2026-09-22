"""Service helpers retain bounded connections without extending transactions [SB-0.4]."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from simplebroker import Config, Queue
from tests.tasks.test_heartbeat import make_heartbeat_taskspec
from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    TERMINAL_ENVELOPE_TYPE,
    WEFT_CONFIG_DEFAULTS,
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core import control_probe, endpoints, heartbeat, spawn_requests, task_evidence
from weft.core.control_messages import encode_control_message
from weft.core.pipelines import compile_linear_pipeline, load_pipeline_spec_payload
from weft.core.task_state import task_state_queue_name
from weft.core.tasks.heartbeat import HeartbeatTask
from weft.core.tasks.pipeline import PipelineTask
from weft.helpers import process_create_time

pytestmark = [pytest.mark.shared, pytest.mark.timeout(60)]


@pytest.fixture
def counted_service_connections(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[WeftContext, list[Any]]]:
    """Count actual backend connections and require every owned one to close."""
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
    for connection in connections:
        if ctx.backend_name == "postgres":
            assert connection.closed
        else:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")


def _seed_service(ctx: WeftContext, tid: str) -> None:
    pid = os.getpid()
    mapping = {
        "full": tid,
        "short": tid[-10:],
        "runtime_handle": {
            "runner": "host",
            "kind": "process",
            "id": str(pid),
            "control": {"authority": "host-pid"},
            "observations": {
                "host_pids": [pid],
                "host_processes": [
                    {"pid": pid, "create_time": process_create_time(pid)}
                ],
            },
            "metadata": {},
        },
    }
    with ctx.queue(task_state_queue_name(tid), persistent=True) as queue:
        queue.write(json.dumps(mapping))
    record = endpoints.build_endpoint_record_payload(
        name=INTERNAL_HEARTBEAT_ENDPOINT_NAME,
        tid=tid,
        inbox=f"T{tid}.inbox",
        outbox=f"T{tid}.outbox",
        ctrl_in=f"T{tid}.ctrl_in",
        ctrl_out=f"T{tid}.ctrl_out",
    )
    with ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=True) as queue:
        queue.write(json.dumps(record))


@pytest.mark.parametrize("operation", ["endpoints", "heartbeat", "terminal"])
def test_service_reads_reuse_owner_and_observe_independent_commits(
    counted_service_connections: tuple[WeftContext, list[Any]], operation: str
) -> None:
    ctx, connections = counted_service_connections
    tid = str(time.time_ns())
    with (
        ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner,
        owner.get_connection() as broker,
    ):
        owner.has_pending()
        _seed_service(ctx, tid)
        for status in ("running", "completed"):
            # Independent transient writers prove visibility, not borrowed setup leases.
            with ctx.queue(WEFT_GLOBAL_LOG_QUEUE) as writer:
                writer.write(
                    json.dumps(
                        {
                            "tid": tid,
                            "status": status,
                            "taskspec": {
                                "metadata": {
                                    INTERNAL_SERVICE_KEY_METADATA_KEY: INTERNAL_SERVICE_KEY_HEARTBEAT,
                                }
                            },
                        }
                    )
                )
            if status == "completed":
                with ctx.queue(f"T{tid}.ctrl_out") as writer:
                    writer.write(
                        json.dumps(
                            {
                                "type": TERMINAL_ENVELOPE_TYPE,
                                "tid": tid,
                                "source": "task",
                                "status": status,
                            }
                        )
                    )
            before = len(connections)
            for _ in range(3):
                if operation == "endpoints":
                    resolved = endpoints.resolve_endpoint(
                        ctx, INTERNAL_HEARTBEAT_ENDPOINT_NAME, broker=broker
                    )
                    assert (resolved is not None) == (status == "running")
                    if resolved is not None:
                        assert resolved.record.tid == tid
                elif operation == "heartbeat":
                    assert (
                        heartbeat._latest_heartbeat_task_status(
                            ctx, tid=tid, broker=broker
                        )
                        == status
                    )
                    assert heartbeat._heartbeat_runtime_handle_is_live(
                        ctx, tid=tid, broker=broker
                    )
                else:
                    snapshot = task_evidence.task_local_terminal_evidence(
                        ctx, tid=tid, taskspec_payload=None, broker=broker
                    )
                    assert (snapshot is not None) == (status == "completed")
                    if snapshot is not None:
                        assert snapshot.source == "ctrl_out"
                        assert snapshot.status == status
            assert len(connections) == before
        owner.write("owner remains usable")
    assert connections


def test_heartbeat_requests_commit_without_closing_owner(
    counted_service_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_service_connections
    tid = str(time.time_ns())
    with (
        ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner,
        owner.get_connection() as broker,
    ):
        owner.has_pending()
        _seed_service(ctx, tid)
        before = len(connections)
        assert (
            heartbeat.upsert_heartbeat(
                ctx,
                heartbeat_id="test",
                interval_seconds=60,
                destination_queue="test.destination",
                message="go",
                broker=broker,
            )
            is None
        )
        assert (
            heartbeat.cancel_heartbeat(ctx, heartbeat_id="test", broker=broker) is None
        )
        assert len(connections) == before
        with ctx.broker() as reader:
            rows = list(reader.peek_generator(f"T{tid}.inbox", with_timestamps=False))
        assert [json.loads(body)["action"] for body in rows] == ["upsert", "cancel"]
        owner.write("owner remains usable")


def test_empty_service_state_queries_do_not_open_connections(
    counted_service_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_service_connections
    assert (
        endpoints.latest_tid_state_entries_for_endpoint_resolution(ctx, tids=iter(()))
        == {}
    )
    assert (
        endpoints.latest_tid_state_entries_for_endpoint_resolution(ctx, tids=["bad"])
        == {}
    )
    assert not heartbeat._heartbeat_runtime_handle_is_live(ctx, tid="bad")
    assert not connections


@pytest.mark.parametrize(
    ("state", "spec"),
    [
        ("empty", {}),
        ("pending", {}),
        ("claimed", {}),
        ("mixed", {}),
        ("claimed", {"persistent": True}),
        ("claimed", {"interactive": True}),
    ],
)
def test_claimed_outbox_counts_borrow_connection_without_changing_proof(
    counted_service_connections: tuple[WeftContext, list[Any]],
    state: str,
    spec: dict[str, Any],
) -> None:
    ctx, connections = counted_service_connections
    tid = str(time.time_ns())
    outbox_name = f"T{tid}.outbox"
    claimed = int(state in {"claimed", "mixed"})
    pending = int(state in {"pending", "mixed"})
    with (
        ctx.queue(outbox_name, persistent=True) as owner,
        owner.get_connection() as broker,
    ):
        if claimed:
            owner.write("retained non-JSON result")
            assert owner.read_one() == "retained non-JSON result"
        if pending:
            owner.write("readable non-JSON result")
        before = len(connections)
        for _ in range(3):
            counts = task_evidence.queue_message_counts(ctx, outbox_name, broker=broker)
            assert counts is not None
            assert (counts.total, counts.unclaimed, counts.claimed) == (
                claimed + pending,
                pending,
                claimed,
            )
            evidence = task_evidence.claimed_outbox_result_evidence(
                ctx,
                tid=tid,
                outbox_name=outbox_name,
                taskspec_payload={"spec": spec},
                broker=broker,
            )
            if state == "claimed" and not spec:
                assert evidence is not None
                assert evidence.status == "failed"
                assert evidence.classification == "claimed_result_without_terminal"
                assert evidence.value is None
                assert evidence.ack_targets == ()
                assert evidence.reconciliation is not None
                assert evidence.reconciliation["claimed_messages"] == 1
            else:
                assert evidence is None
        assert len(connections) == before
        with ctx.broker() as reader:
            stats = reader.get_queue_stat(outbox_name)
            assert (stats.total, stats.pending) == (claimed + pending, pending)
            rows = list(
                reader.peek_generator(
                    outbox_name, with_timestamps=False, include_claimed=True
                )
            )
            assert rows == (
                (["retained non-JSON result"] if claimed else [])
                + (["readable non-JSON result"] if pending else [])
            )
        owner.write("owner remains usable")


@pytest.mark.parametrize("source", ["log", "ctrl_out", "outbox", "claimed"])
def test_known_tid_evidence_borrows_all_reads_and_sees_new_commits(
    counted_service_connections: tuple[WeftContext, list[Any]], source: str
) -> None:
    ctx, connections = counted_service_connections
    tid = str(time.time_ns())
    with (
        ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner,
        owner.get_connection() as broker,
    ):
        live_at = owner.write(json.dumps({"tid": tid, "status": "running"}))
        owner.write("{malformed")
        owner.write(json.dumps({"tid": "another-task", "status": "failed"}))
        before = len(connections)
        assert task_evidence.bounded_log_terminal_evidence(
            ctx, tid=tid, broker=broker
        ) == (
            None,
            True,
            live_at,
        )
        initial = task_evidence.known_tid_evidence(ctx, tid=tid, broker=broker)
        assert initial is not None and not initial.terminal
        assert len(connections) == before

        destination = (
            WEFT_GLOBAL_LOG_QUEUE
            if source == "log"
            else f"T{tid}.{'outbox' if source == 'claimed' else source}"
        )
        with ctx.broker() as writer:
            if source == "log":
                body = json.dumps({"tid": tid, "status": "completed"})
            elif source == "ctrl_out":
                body = json.dumps(
                    {
                        "tid": tid,
                        "type": TERMINAL_ENVELOPE_TYPE,
                        "source": "task",
                        "status": "completed",
                    }
                )
            else:
                body = json.dumps({"result": "done"})
            message_id = writer.write(destination, body)
            if source == "claimed":
                assert writer.claim_one(destination, with_timestamps=False) == body
        before = len(connections)
        for _ in range(3):
            evidence = task_evidence.known_tid_evidence(ctx, tid=tid, broker=broker)
            assert evidence is not None and evidence.terminal
            assert evidence.status == ("failed" if source == "claimed" else "completed")
            assert evidence.source == ("outbox" if source == "claimed" else source)
            if source == "claimed":
                assert evidence.classification == "claimed_result_without_terminal"
                assert evidence.ack_targets == ()
            elif source != "log":
                assert evidence.ack_targets == (
                    task_evidence.QueueAckTarget(destination, message_id),
                )
        assert len(connections) == before
        with ctx.broker() as reader:
            assert (
                reader.peek_one(
                    destination,
                    exact_timestamp=message_id,
                    with_timestamps=False,
                    include_claimed=True,
                )
                == body
            )
        owner.write("owner remains usable")


@pytest.mark.parametrize("pong_order", ["before-terminal", "after-terminal", "absent"])
def test_known_tid_ping_borrows_probe_and_preserves_timestamp_precedence(
    counted_service_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    pong_order: str,
) -> None:
    ctx, connections = counted_service_connections
    tid = str(time.time_ns())
    request_id = UUID("12345678-1234-5678-1234-567812345678")
    monkeypatch.setattr(control_probe.uuid, "uuid4", lambda: request_id)
    requester_tid = "1780000000000000401"
    reply_queue = f"T{requester_tid}.ctrl_in"
    monkeypatch.setattr(
        control_probe,
        "generate_spawn_request_timestamp",
        lambda *_args, **_kwargs: int(requester_tid),
    )
    pong = json.dumps(
        {
            "tid": tid,
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "request_id": request_id.hex,
            "task_status": "running",
        }
    )
    with (
        ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner,
        owner.get_connection() as broker,
    ):
        if pong_order == "before-terminal":
            broker.write(reply_queue, pong)
        terminal_id = owner.write(json.dumps({"tid": tid, "status": "completed"}))
        if pong_order == "after-terminal":
            broker.write(reply_queue, pong)
        broker.write(reply_queue, json.dumps({"request_id": "unrelated"}))
        before = len(connections)
        evidence = task_evidence.known_tid_evidence(
            ctx, tid=tid, ping=True, probe_timeout=0, broker=broker
        )
        opened = connections[before:]
        for connection in opened:
            if ctx.backend_name == "postgres":
                assert connection.closed
            else:
                with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                    connection.execute("SELECT 1")
        assert evidence is not None
        if pong_order == "after-terminal":
            assert evidence.status == "running"
            assert evidence.classification == "live_pong"
            assert evidence.source == "control-pong"
        else:
            assert evidence.status == "completed"
            assert evidence.observed_at == terminal_id
        assert reply_queue not in broker.list_queues()
        assert broker.get_queue_stat(f"T{tid}.ctrl_in").pending == 1
        owner.write("owner remains usable")


@pytest.mark.parametrize("explicit", [False, True])
def test_spawn_helpers_reuse_connection_and_preserve_committed_ids(
    counted_service_connections: tuple[WeftContext, list[Any]], explicit: bool
) -> None:
    ctx, connections = counted_service_connections
    template = {
        "name": "child",
        "spec": {"type": "function", "function_target": "weft.tasks:noop"},
    }
    with (
        ctx.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as owner,
        owner.get_connection() as broker,
    ):
        owner.has_pending()
        before = len(connections)
        allocated = spawn_requests.generate_spawn_request_timestamp(
            ctx.broker_target, config=ctx.broker_config, broker=broker
        )
        submitted = spawn_requests.submit_spawn_request(
            ctx.broker_target,
            taskspec=template,
            work_payload="payload",
            config=ctx.broker_config,
            tid=allocated if explicit else None,
            inherited_weft_context=str(ctx.root),
            broker=broker,
        )
        assert len(connections) == before
        with ctx.broker() as reader:
            row = reader.peek_one(WEFT_SPAWN_REQUESTS_QUEUE, with_timestamps=True)
        assert row is not None and row[1] == submitted
        assert json.loads(row[0])["inbox_message"] == "payload"
        if explicit:
            assert submitted == allocated
        before = len(connections)
        assert spawn_requests.delete_spawn_request(
            ctx.broker_target,
            message_timestamp=submitted,
            config=ctx.broker_config,
            broker=broker,
        )
        assert not spawn_requests.delete_spawn_request(
            ctx.broker_target,
            message_timestamp=submitted,
            config=ctx.broker_config,
            broker=broker,
        )
        assert len(connections) == before
        assert owner.peek_one() is None
        owner.write("owner remains usable")


def test_pipeline_child_submission_preserves_exact_config_snapshot(
    counted_service_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, connections = counted_service_connections
    config = Config(
        dict(ctx.broker_config), prefix="SNAPSHOT", defaults=WEFT_CONFIG_DEFAULTS
    )
    ctx = replace(ctx, config=config, broker_config=config)
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {"name": "pipe", "stages": [{"name": "first", "task": "first"}]}
        ),
        context=ctx,
        task_loader=lambda _name: {
            "name": "first",
            "spec": {"type": "function", "function_target": "weft.tasks:noop"},
        },
    )
    task = PipelineTask(ctx.broker_target, compiled.pipeline_taskspec, config=config)
    runtime_queues = [item.queue for item in task._queues.values()]
    closed_queues: list[Queue] = []
    original_close = Queue.close

    def record_close(queue: Queue) -> None:
        closed_queues.append(queue)
        original_close(queue)

    monkeypatch.setattr(Queue, "close", record_close)
    captured: list[object] = []
    submission_opens: list[int] = []
    original_submit = spawn_requests.submit_spawn_request

    def submit(*args: Any, **kwargs: Any) -> int:
        captured.append(kwargs["config"])
        before = len(connections)
        result = original_submit(*args, **kwargs)
        submission_opens.append(len(connections) - before)
        return result

    monkeypatch.setattr("weft.core.tasks.pipeline.submit_spawn_request", submit)
    try:
        task._get_connected_queue().has_pending()
        task.process_once()
        assert captured and all(value is config for value in captured)
        assert submission_opens == [0] * len(captured)
        with ctx.broker() as reader:
            assert reader.get_queue_stat(
                WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
            ).pending == len(captured)
    finally:
        task.stop(join=False)
        task.cleanup()
        missing = [queue.name for queue in runtime_queues if queue not in closed_queues]
        for queue in runtime_queues:
            queue.close()
        assert not missing, missing


@pytest.mark.parametrize("mode", [CONTROL_STOP, CONTROL_KILL, "failure"])
@pytest.mark.parametrize("fail_first", [False, True])
def test_pipeline_control_broadcast_reuses_owner_and_continues_after_error(
    counted_service_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    fail_first: bool,
) -> None:
    ctx, connections = counted_service_connections
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {"name": "pipe", "stages": [{"name": "first", "task": "first"}]}
        ),
        context=ctx,
        task_loader=lambda _name: {
            "name": "first",
            "spec": {"type": "function", "function_target": "weft.tasks:noop"},
        },
    )
    task = PipelineTask(
        ctx.broker_target, compiled.pipeline_taskspec, config=ctx.broker_config
    )
    try:
        task.process_once()
        destinations = [stage.ctrl_in_queue for stage in task._runtime.stages] + [
            edge.taskspec["io"]["control"]["ctrl_in"] for edge in task._runtime.edges
        ]
        cached = set(task._queue_cache)
        with task._get_connected_queue().get_connection() as broker:
            original_write = type(broker).write

            def write(db: Any, queue_name: str, *args: Any, **kwargs: Any) -> Any:
                if fail_first and queue_name == destinations[0]:
                    raise RuntimeError("injected destination failure")
                return original_write(db, queue_name, *args, **kwargs)

            monkeypatch.setattr(type(broker), "write", write)
            before = len(connections)
            if mode == "failure":
                task._fail_pipeline(
                    "child failed",
                    child_kind="stage",
                    child_name="first",
                    child_tid=task._runtime.stages[0].tid,
                    ctrl_queues=["", *destinations],
                )
                assert task.taskspec.state.status == "failed"
                assert task.should_stop
                assert task._status_snapshot["failure"]["error"] == "child failed"
            else:
                task._broadcast_control(mode)
            assert len(connections) == before
            assert set(task._queue_cache) == cached
        command = CONTROL_STOP if mode == "failure" else mode
        with ctx.broker() as reader:
            for index, destination in enumerate(destinations):
                rows = list(reader.peek_generator(destination, with_timestamps=False))
                assert rows == (
                    []
                    if fail_first and index == 0
                    else [encode_control_message(command)]
                )
    finally:
        task.stop(join=False)
        task.cleanup()


def test_raw_sqlite_task_context_preserves_target_and_exact_config(
    counted_service_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, _connections = counted_service_connections
    if ctx.backend_name != "sqlite":
        pytest.skip("Raw filesystem task targets are SQLite-only")
    config = Config(
        dict(ctx.broker_config), prefix="SNAPSHOT", defaults=WEFT_CONFIG_DEFAULTS
    )
    custom_path = ctx.root / "custom-task.sqlite3"
    assert custom_path != ctx.database_path
    task = HeartbeatTask(
        str(custom_path),
        make_heartbeat_taskspec(str(time.time_ns()), ctx.root),
        config=config,
    )
    try:
        task_context = task._task_context()
        assert task_context.broker_target.backend_name == "sqlite"
        assert task_context.broker_target.target == str(task._db_path)
        assert task_context.database_path == custom_path
        assert task_context.broker_config is config
        assert task_context.broker_config.prefix == "SNAPSHOT"
    finally:
        task.stop(join=False)
        task.cleanup()


def test_heartbeat_destination_churn_does_not_grow_task_cache(
    counted_service_connections: tuple[WeftContext, list[Any]],
) -> None:
    ctx, connections = counted_service_connections
    tid = str(time.time_ns())
    task = HeartbeatTask(
        ctx.broker_target,
        make_heartbeat_taskspec(tid, ctx.root),
        config=ctx.broker_config,
    )
    try:
        task.process_once()
        cached = set(task._queue_cache)
        for index in range(4):
            name = f"destination.{index}"
            task._queue(f"T{tid}.inbox").write(
                json.dumps(
                    {
                        "action": "upsert",
                        "heartbeat_id": "test",
                        "interval_seconds": HEARTBEAT_MIN_INTERVAL_SECONDS,
                        "destination_queue": name,
                        "message": "tick",
                    }
                )
            )
            task.process_once()
            registration = task._registrations["test"]
            registration.next_due_at = time.monotonic() - 1
            task._due_heap = [(registration.next_due_at, registration.heartbeat_id)]
            before = len(connections)
            task.process_once()
            assert len(connections) == before
            with ctx.broker() as reader:
                assert reader.peek_one(name, with_timestamps=False) == "tick"
            task._queue(f"T{tid}.inbox").write(
                json.dumps({"action": "cancel", "heartbeat_id": "test"})
            )
            task.process_once()
            assert not task._registrations
            assert set(task._queue_cache) == cached
    finally:
        task.stop(join=False)
        task.cleanup()


def test_failed_heartbeat_write_closes_local_facade_not_owner(
    counted_service_connections: tuple[WeftContext, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _connections = counted_service_connections
    tid = str(time.time_ns())
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner:
        owner.has_pending()
        _seed_service(ctx, tid)
        resolved = endpoints.resolve_endpoint(ctx, INTERNAL_HEARTBEAT_ENDPOINT_NAME)
        assert resolved is not None
        original_queue = WeftContext.queue
        opened: list[Queue] = []
        closed: list[Queue] = []

        def queue(self: WeftContext, name: str, *, persistent: bool = False) -> Queue:
            result = original_queue(self, name, persistent=persistent)
            opened.append(result)
            original_close = result.close

            def close() -> None:
                closed.append(result)
                original_close()

            monkeypatch.setattr(result, "close", close)
            return result

        monkeypatch.setattr(WeftContext, "queue", queue)
        with pytest.raises(TypeError):
            heartbeat._write_heartbeat_request(
                ctx, resolved=resolved, payload={"bad": object()}
            )
        assert opened and opened == closed
        owner.write("owner remains usable")
