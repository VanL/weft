"""Real finite fanout workload for family scheduling [MA-1.8], [MF-1]."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from typing import Any

from weft.client import connect
from weft.context import WeftContext
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.runtime_identity import get_runtime_identity

FAMILY_EVENTS = "test.family.events"
FAMILY_WAIT_SECONDS = 60.0


def _family_event(
    event: str, index: int | None = None, *, measure_usage: bool = False, **fields: Any
) -> None:
    identity = get_runtime_identity()
    if identity is None:
        raise RuntimeError("workload worker has no runtime identity")
    with (
        connect() as client,
        client.context.queue(FAMILY_EVENTS, persistent=True) as queue,
    ):
        if measure_usage and client.context.backend_name == "postgres":
            # Optional backend dependency: only the real PG proof samples usage.
            from simplebroker_pg import get_connection_usage

            fields["usage"] = dict(get_connection_usage(queue))
        queue.write(
            json.dumps(
                {
                    "event": event,
                    "index": index,
                    "tid": identity.tid,
                    "root_tid": identity.root_tid,
                    "ancestry_tids": list(identity.ancestry_tids),
                    "context": identity.context,
                    "admission_max_connections": client.context.config[
                        "ADMISSION_MAX_CONNECTIONS"
                    ],
                    "connection_owner": identity.connection_owner,
                    **fields,
                }
            )
        )


def _materialize_pool(context: WeftContext) -> None:
    """Drive the known three-checkout worker pool phase through public APIs.

    The caller retains another queue/session lease, so returned connections stay
    observable after all three concurrent operations finish. This is a fixture
    resource phase, never an admission charge or a prediction of arbitrary work.
    """
    ready = Barrier(3)

    def hold_checkout() -> None:
        with (
            context.queue("test.family.pool.phase", persistent=True) as queue,
            queue.get_connection() as broker,
        ):
            broker.write("test.family.pool.phase", "materialized")
            ready.wait(timeout=15.0)

    with ThreadPoolExecutor(max_workers=3) as workers:
        futures = [workers.submit(hold_checkout) for _ in range(3)]
        for future in futures:
            future.result(timeout=20.0)


def _wait_release(
    context: WeftContext, queue_name: str, payload: dict[str, Any], phase: str
) -> None:
    with (
        context.queue(queue_name, persistent=True) as gate,
        closing(QueueChangeMonitor([gate], config=context.config)) as monitor,
    ):
        if payload.get("materialize_pool"):
            _materialize_pool(context)
        _family_event(
            phase,
            int(payload["index"]),
            measure_usage=bool(payload.get("measure_usage")),
        )
        deadline = time.monotonic() + FAMILY_WAIT_SECONDS
        while gate.read_one() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("test did not release family workload")
            monitor.wait(remaining)


def family_leaf(payload: dict[str, Any]) -> str:
    """Stand in for a finite LLM call without replacing task/process plumbing."""
    index = int(payload["index"])
    _family_event(
        "leaf_started", index, measure_usage=bool(payload.get("measure_usage"))
    )
    if payload.get("gated"):
        with connect() as client:
            _wait_release(
                client.context,
                f"test.family.leaf.release.{index}",
                payload,
                "leaf_waiting",
            )
    return f"leaf:{index}"


def family_enrichment(payload: dict[str, Any]) -> dict[str, Any]:
    """Retain a branch while preparing, then block on an unknown future child."""
    index = int(payload["index"])
    _family_event(
        "enrichment_started", index, measure_usage=bool(payload.get("measure_usage"))
    )
    with connect() as client:
        if payload.get("gated"):
            _wait_release(
                client.context,
                f"test.family.release.{index}",
                payload,
                "enrichment_waiting",
            )
        child = client.submit(
            {
                "name": f"family-leaf-{index}",
                "spec": {
                    "type": "function",
                    "function_target": "tests.helpers.family_workload:family_leaf",
                },
            },
            payload={
                "index": index,
                "gated": bool(payload.get("leaf_gated")),
                "measure_usage": bool(payload.get("measure_usage")),
                "materialize_pool": bool(payload.get("materialize_pool")),
            },
        )
        _family_event(
            "leaf_accepted",
            index,
            child_tid=child.tid,
            measure_usage=bool(payload.get("measure_usage")),
        )
        result = child.result(timeout=FAMILY_WAIT_SECONDS)
        if result.status != "completed":
            raise RuntimeError(f"leaf failed: {result.status}: {result.error}")
        _family_event(
            "enrichment_completed",
            index,
            measure_usage=bool(payload.get("measure_usage")),
        )
        return {"index": index, "child_tid": child.tid, "value": result.value}


def family_rollup(payload: dict[str, Any]) -> list[str]:
    """Submit the full backlog and return before its enrichments finish."""
    count = int(payload["count"])
    tids = []
    with connect() as client:
        for index in range(count):
            child = client.submit(
                {
                    "name": f"family-enrichment-{index}",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.helpers.family_workload:family_enrichment",
                    },
                },
                payload={
                    "index": index,
                    "gated": bool(payload.get("gated")),
                    "leaf_gated": bool(payload.get("leaf_gated")),
                    "measure_usage": bool(payload.get("measure_usage")),
                    "materialize_pool": bool(payload.get("materialize_pool")),
                },
            )
            tids.append(child.tid)
            if index == 0 and payload.get("materialize_pool"):
                _materialize_pool(client.context)
                _family_event(
                    "rollup_pool_ready",
                    measure_usage=bool(payload.get("measure_usage")),
                )
    _family_event(
        "rollup_returned",
        accepted_tids=tids,
        measure_usage=bool(payload.get("measure_usage")),
    )
    return tids
