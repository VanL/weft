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


@pytest.fixture(params=[1, 5])
def family_harness(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[WeftTestHarness]:
    """The public config controls both live Manager and inherited worker clients."""
    monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", str(request.param))
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        yield harness


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


def test_early_rollup_exit_keeps_delayed_children_progressing_with_bounded_parallelism(
    family_harness: WeftTestHarness,
) -> None:
    """A retained branch can descend behind queued siblings; spare branches overlap.

    Gates make the preparation delay explicit. The whole fixture drains before
    ordering and overlap assertions, so absence claims do not race live producers.
    Admission is disabled here to isolate scheduling from resource accounting;
    the PostgreSQL pressure tests exercise that separate gate with this workload.
    """
    harness = family_harness
    limit = int(harness.context.config["SCHEDULER_MAX_FRONTIERS"])
    count = limit + 4
    with WeftClient(context=harness.context) as client:
        rollup = client.submit(
            {
                "name": "family-rollup",
                "spec": {
                    "type": "function",
                    "function_target": "tests.helpers.family_workload:family_rollup",
                },
            },
            payload={"count": count, "gated": True},
        )
        harness.register_tid(rollup.tid)
        accepted = rollup.result(timeout=60.0)
        assert accepted.status == "completed", accepted.error
        assert isinstance(accepted.value, list) and len(accepted.value) == count
        tids = accepted.value
        for tid in tids:
            harness.register_tid(tid)
        rows = _wait_events(harness, "enrichment_started", limit)
        first_wave = [
            row["index"] for row in rows if row["event"] == "enrichment_started"
        ][:limit]
        for index in first_wave:
            with harness.context.queue(
                f"test.family.release.{index}", persistent=True
            ) as queue:
                queue.write("ready-to-descend")
        _wait_events(harness, "leaf_started", limit)
        # Pre-fill later gates only after the first wave's descendants run. This
        # proves preparation does not let siblings steal an occupied branch.
        for index in range(count):
            if index not in first_wave:
                with harness.context.queue(
                    f"test.family.release.{index}", persistent=True
                ) as queue:
                    queue.write("ready-to-descend")
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
        assert active <= limit
    assert active == 0
    assert peak == limit
    first_leaf = next(i for i, row in enumerate(rows) if row["event"] == "leaf_started")
    assert (
        sum(row["event"] == "enrichment_started" for row in rows[:first_leaf]) == limit
    )
    assert all(row["frontier_limit"] == limit for row in rows)
    assert all(row["root_tid"] == rollup.tid for row in rows)
    leaves = [row for row in rows if row["event"] == "leaf_started"]
    assert len(leaves) == count
    assert all(
        row["ancestry_tids"][0] == rollup.tid and len(row["ancestry_tids"]) == 2
        for row in leaves
    )
