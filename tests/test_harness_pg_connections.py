"""Bounded harness polling reuses its broker session (Spec: [TS-0])."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers import weft_harness as harness_mod
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, WEFT_SERVICES_REGISTRY_QUEUE


@pytest.mark.shared
@pytest.mark.parametrize("method", ["wait_for_completion", "wait_for_terminal_state"])
@pytest.mark.parametrize("failure", [False, True])
def test_wait_reuses_pg_connections_and_closes(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    failure: bool,
) -> None:
    if weft_harness.context.backend_name != "postgres":
        pytest.skip("Physical PostgreSQL connection regression")
    psycopg = pytest.importorskip("psycopg")
    original_connect = psycopg.Connection.connect.__func__
    opened = 0

    def counted_connect(cls: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal opened
        opened += 1
        return original_connect(cls, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(counted_connect))
    monkeypatch.setattr(psycopg, "connect", psycopg.Connection.connect)
    original_entries = harness_mod.iter_queue_json_entries
    scans = 0
    first_scan_connections = 0
    repeated_scan_connections = 0
    closed: list[Queue] = []

    class TrackedQueue(Queue):
        def close(self) -> None:
            super().close()
            closed.append(self)

    monkeypatch.setattr(harness_mod, "Queue", TrackedQueue)
    with weft_harness.context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as writer:
        tid = str(writer.generate_timestamp())

        def scan(queue: Queue, **kwargs: Any) -> Iterator[Any]:
            nonlocal scans, first_scan_connections, repeated_scan_connections
            scans += 1
            if scans == 4:
                repeated_scan_connections = opened - first_scan_connections
                writer.write(
                    json.dumps(
                        {
                            "tid": tid,
                            "event": "work_failed" if failure else "work_completed",
                            "status": "failed" if failure else "completed",
                        }
                    )
                )
            yield from original_entries(queue, **kwargs)
            if scans == 1:
                first_scan_connections = opened

        monkeypatch.setattr(harness_mod, "iter_queue_json_entries", scan)
        if failure:
            with pytest.raises(RuntimeError):
                getattr(weft_harness, method)(tid)
        else:
            getattr(weft_harness, method)(tid)
    assert scans >= 4
    assert repeated_scan_connections == 0
    assert any(queue.name == WEFT_GLOBAL_LOG_QUEUE for queue in closed)


@pytest.mark.shared
@pytest.mark.parametrize("failure", [False, True])
def test_registry_drain_reuses_pg_connections_and_closes(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
) -> None:
    if weft_harness.context.backend_name != "postgres":
        pytest.skip("Physical PostgreSQL connection regression")
    psycopg = pytest.importorskip("psycopg")
    original_connect = psycopg.Connection.connect.__func__
    opened = 0

    def counted_connect(cls: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal opened
        opened += 1
        return original_connect(cls, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(counted_connect))
    monkeypatch.setattr(psycopg, "connect", psycopg.Connection.connect)
    reads = 0
    baseline = 0
    repeated = 0
    closed = False

    class TrackedQueue(Queue):
        def read_one(self, *args: Any, **kwargs: Any) -> Any:
            nonlocal reads, baseline, repeated
            reads += 1
            if reads == 4:
                repeated = opened - baseline
                if failure:
                    raise RuntimeError("deliberate read failure")
            result = super().read_one(*args, **kwargs)
            if reads == 1:
                baseline = opened
            return result

        def close(self) -> None:
            nonlocal closed
            super().close()
            closed = True

    with weft_harness.context.queue(
        WEFT_SERVICES_REGISTRY_QUEUE, persistent=True
    ) as writer:
        for _ in range(3):
            writer.write("{}")
        monkeypatch.setattr(harness_mod, "Queue", TrackedQueue)
        if failure:
            with pytest.raises(RuntimeError, match="deliberate read failure"):
                weft_harness._drain_registry_queue()
        else:
            weft_harness._drain_registry_queue()
    monkeypatch.setattr(harness_mod, "Queue", Queue)
    assert reads == 4
    assert repeated == 0
    assert closed
