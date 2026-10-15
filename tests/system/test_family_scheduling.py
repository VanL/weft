"""Real rollup → enrichment → leaf progress [MA-1.8], [MF-1], [OBS.6b]."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import closing
from typing import Any

import pytest

from tests.helpers.family_workload import FAMILY_EVENTS
from tests.helpers.weft_harness import WeftTestHarness
from weft.client import WeftClient
from weft.core.queue_wait import QueueChangeMonitor

pytestmark = [pytest.mark.shared, pytest.mark.timeout(180)]


@pytest.fixture(params=[6, 8])
def family_harness(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[WeftTestHarness, int]]:
    """The public config controls both live Manager and inherited worker clients."""
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        yield harness, int(request.param)


def _events(harness: WeftTestHarness) -> list[dict[str, Any]]:
    with harness.context.queue(FAMILY_EVENTS, persistent=True) as queue:
        return [json.loads(body) for body in queue.peek_generator()]


def _wait_events(
    harness: WeftTestHarness, event: str, count: int
) -> list[dict[str, Any]]:
    with (
        harness.context.queue(FAMILY_EVENTS, persistent=True) as queue,
        closing(QueueChangeMonitor([queue], config=harness.context.config)) as monitor,
    ):
        deadline = time.monotonic() + 60.0
        while True:
            rows = _events(harness)
            if sum(row["event"] == event for row in rows) >= count:
                return rows
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"missing {count} {event} events: {rows}")
            monitor.wait(remaining)


def test_entire_finite_family_and_its_leaves_overlap_beyond_five(
    family_harness: tuple[WeftTestHarness, int],
) -> None:
    """All input parents and leaves can overlap; no withheld sibling waves.

    Six/eight is the whole input, never an admission setting. Preparation and
    leaf gates expose actual overlap before all peers are released together.
    """
    harness, count = family_harness
    with WeftClient(context=harness.context) as client:
        rollup = client.submit(
            {
                "name": "family-rollup",
                "spec": {
                    "type": "function",
                    "function_target": "tests.helpers.family_workload:family_rollup",
                },
            },
            payload={"count": count, "gated": True, "leaf_gated": True},
        )
        harness.register_tid(rollup.tid)
        accepted = rollup.result(timeout=60.0)
        assert accepted.status == "completed", accepted.error
        assert isinstance(accepted.value, list) and len(accepted.value) == count
        tids = accepted.value
        for tid in tids:
            harness.register_tid(tid)
        _wait_events(harness, "enrichment_waiting", count)
        for index in range(count):
            with harness.context.queue(
                f"test.family.release.{index}", persistent=True
            ) as queue:
                queue.write("ready-to-descend")
        _wait_events(harness, "leaf_waiting", count)
        for index in range(count):
            with harness.context.queue(
                f"test.family.leaf.release.{index}", persistent=True
            ) as queue:
                queue.write("complete")
        for tid in tids:
            result = client.task(tid).result(timeout=60.0)
            assert result.status == "completed", result.error
            assert isinstance(result.value, dict)
            index = result.value["index"]
            assert result.value["value"] == f"leaf:{index}"
            harness.register_tid(result.value["child_tid"])
    rows = _events(harness)
    active = peak = 0
    for row in rows:
        if row["event"] == "enrichment_started":
            active += 1
            peak = max(peak, active)
        elif row["event"] == "enrichment_completed":
            active -= 1
    assert active == 0
    assert peak == count
    first_leaf = next(i for i, row in enumerate(rows) if row["event"] == "leaf_started")
    assert (
        sum(row["event"] == "enrichment_started" for row in rows[:first_leaf]) == count
    )
    assert all(row["admission_max_connections"] == 0 for row in rows)
    assert all(row["root_tid"] == rollup.tid for row in rows)
    leaves = [row for row in rows if row["event"] == "leaf_started"]
    assert len(leaves) == count
    assert all(
        row["ancestry_tids"][0] == rollup.tid and len(row["ancestry_tids"]) == 2
        for row in leaves
    )
