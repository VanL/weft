"""Spawn-importable nested admission workload [MA-1.8], [MF-1], [MF-5]."""

from __future__ import annotations

import time
from typing import Any, Final

from weft.client import connect

WORKLOAD_TARGET: Final[str] = "tests.helpers.admission_workload:admission_work"
OPS_ROLLUP_TARGET: Final[str] = "tests.helpers.admission_workload:ops_rollup"
OPS_ENRICHMENT_TARGET: Final[str] = "tests.helpers.admission_workload:ops_enrichment"
OPS_BURST_OUTBOX: Final[str] = "test.admission.ops_burst.results"
OPS_BURST_TIMEOUT_SECONDS: Final[float] = 600.0
OPS_ROLLUP_INDEX: Final[int] = -1
OPS_SUBMISSION_INTERVAL_SECONDS: Final[float] = 0.05
"""Nominal spacing of the recorded November 7 fanout."""
OPS_PREPARATION_SECONDS: Final[float] = 2.65
"""Representative October 22 parent start to first child submission time."""


def admission_work(payload: dict[str, Any]) -> dict[str, Any]:
    """Do timed work while a real child, if present, can progress independently."""
    index = payload["index"]
    seconds, *descendant_seconds = payload["durations"]
    if not descendant_seconds:
        time.sleep(seconds)
        return {"index": index, "descendant_tids": []}

    # A one-shot submission has no use for a retained lease during work or wait.
    with connect() as client:
        child = client.submit(
            {
                "name": f"admission-child-{index}-{len(descendant_seconds)}",
                "spec": {"type": "function", "function_target": WORKLOAD_TARGET},
            },
            payload={**payload, "durations": descendant_seconds},
        )
    time.sleep(seconds)
    settled = child.result(timeout=payload["result_timeout"])
    result = settled.value
    if (
        settled.status != "completed"
        or not isinstance(result, dict)
        or result.get("index") != index
    ):
        raise RuntimeError(f"child {child.tid} did not complete its work: {settled}")
    return {
        "index": index,
        "descendant_tids": [child.tid, *result["descendant_tids"]],
    }


def ops_burst_taskspec(name: str, target: str) -> dict[str, Any]:
    """Send cohort results to one observer and keep control routes per task."""
    return {
        "name": name,
        "spec": {"type": "function", "function_target": target},
        "io": {
            "outputs": {"outbox": OPS_BURST_OUTBOX},
            "control": {
                "ctrl_in": f"test.admission.{name}.ctrl_in",
                "ctrl_out": f"test.admission.{name}.ctrl_out",
            },
        },
    }


def ops_rollup(payload: dict[str, Any]) -> dict[str, Any]:
    """Submit a paced burst and return the accepted TIDs without joining it."""
    tids: list[str] = []
    with connect() as client:
        start = time.monotonic()
        for index, seconds in enumerate(payload["child_seconds"]):
            delay = start + index * OPS_SUBMISSION_INTERVAL_SECONDS - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            enrichment = client.submit(
                ops_burst_taskspec(f"ops-enrichment-{index}", OPS_ENRICHMENT_TARGET),
                payload={"index": index, "child_seconds": seconds},
            )
            tids.append(enrichment.tid)
    return {"index": OPS_ROLLUP_INDEX, "descendant_tids": tids}


def ops_enrichment(payload: dict[str, Any]) -> dict[str, Any]:
    """Prepare, then keep the submission session through the child's result wait."""
    index = payload["index"]
    time.sleep(OPS_PREPARATION_SECONDS)
    with connect() as client:
        child = client.submit(
            {
                "name": f"ops-llm-{index}",
                "spec": {"type": "function", "function_target": WORKLOAD_TARGET},
            },
            payload={"index": index, "durations": [payload["child_seconds"]]},
        )
        started = time.monotonic()
        cpu_started = time.process_time()
        result = child.result(timeout=OPS_BURST_TIMEOUT_SECONDS)
        wait_cpu = time.process_time() - cpu_started
        wait_elapsed = time.monotonic() - started
    if result.status != "completed" or result.value != {
        "index": index,
        "descendant_tids": [],
    }:
        raise RuntimeError(f"ops leaf {child.tid} did not complete its work: {result}")
    return {
        "index": index,
        "descendant_tids": [child.tid],
        "wait_cpu_seconds": wait_cpu,
        "wait_elapsed_seconds": wait_elapsed,
    }
