"""Borrowed manager fallback connections preserve registry evidence [SB-0.4]."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
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
                    record, started, process = manager_runtime.ensure_manager(
                        ctx, broker=broker
                    )
                    assert record["tid"] == _TID
                    assert not started
                    assert process is None
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
            assert manager_runtime.ensure_manager(ctx, broker=broker)[0]["tid"] == _TID
            assert owner.peek_one(exact_timestamp=v1) is None
            v1 = owner.write(json.dumps({"schema": "weft.service_owner.v1"}))
            future = owner.write(json.dumps({"schema": "weft.service_owner.v3"}))
            with pytest.raises(ValueError, match="future service-owner schema"):
                manager_runtime.ensure_manager(ctx, broker=broker)
            assert owner.peek_one(exact_timestamp=v1) is not None
            assert owner.peek_one(exact_timestamp=future) is not None
            assert len(connections) == before


def test_borrowed_manager_probe_reuses_connection_and_retires_reply(
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
        with owner.get_connection() as broker:
            before = len(connections)
            for _ in range(3):
                broker.write(
                    f"T{_TID}.ctrl_out",
                    json.dumps(
                        {
                            "command": "PING",
                            "status": "ok",
                            "message": "PONG",
                            "tid": _TID,
                            "request_id": request_id.hex,
                            "task_status": "running",
                            "should_stop": False,
                            "role": "manager",
                            "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                            "ctrl_in": f"T{_TID}.ctrl_in",
                            "ctrl_out": f"T{_TID}.ctrl_out",
                            "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
                            "weft_context": str(ctx.root),
                        }
                    ),
                )
                record, started, process = manager_runtime.ensure_manager(
                    ctx, broker=broker
                )
                assert record["tid"] == _TID
                assert not started
                assert process is None
                assert broker.peek_one(f"T{_TID}.ctrl_out") is None
            assert broker.get_queue_stat(f"T{_TID}.ctrl_in").pending == 3
            assert len(connections) == before


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
