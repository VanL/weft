"""Measured admission with short and long nested work on real brokers.

Specs: [MA-1.8], [MANAGER.18], [MF-1], [MF-5], [TS-0].
Plan: docs/plans/2026-11-07-nested-admission-load-plan.md.
Ops burst: docs/plans/2026-11-07-ops-shaped-admission-burst-plan.md.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from itertools import accumulate
from statistics import median, quantiles
from typing import Any, Final

import pytest

from benchmarks.admission_workload import (
    OPS_BURST_OUTBOX,
    OPS_BURST_TIMEOUT_SECONDS,
    OPS_ENRICHMENT_TARGET,
    OPS_ROLLUP_INDEX,
    OPS_ROLLUP_TARGET,
    WORKLOAD_TARGET,
    ops_burst_taskspec,
)
from simplebroker import Queue
from tests.helpers.test_backend import active_test_backend, postgres_test_dsn
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    TERMINAL_TASK_STATUSES,
    WEFT_ADMISSION_MAX_CONNECTIONS,
    WEFT_ADMISSION_RESERVE_FRACTION,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.client import connect
from weft.context import WeftContext
from weft.core.manager_runtime import start_manager
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.spawn_requests import (
    generate_spawn_request_timestamp,
    submit_spawn_request,
)
from weft.core.task_state import task_state_queue_name
from weft.core.taskspec import resolve_taskspec_payload
from weft.helpers import iter_queue_json_entries

pytest_plugins = ["tests.helpers.pytest_pg_runtime"]
pytestmark = [pytest.mark.shared, pytest.mark.slow]

SHALLOW_ROOTS: Final[int] = 500
ONE_CHILD_ROOTS: Final[int] = 250
CHAIN_ROOTS: Final[int] = 100
CHAIN_DEPTHS: Final[tuple[int, ...]] = (4, 3, 5)
ROOT_COUNT: Final[int] = SHALLOW_ROOTS + CHAIN_ROOTS
ROOT_OUTBOX: Final[str] = "test.admission.stress.results"
PG_CONNECTION_LIMIT: Final[int] = 300
PG_BACKGROUND_SESSIONS: Final[int] = 250
PG_PRESSURED_RESERVE: Final[str] = "0.05"
PG_PRESSURE_MIN_SESSIONS: Final[int] = 280
PG_PRESSURED_ROOT_OUTPUTS: Final[int] = 100
PG_SAMPLE_INTERVAL_SECONDS: Final[float] = 0.1
PG_SLOT_REJECTION = re.compile(
    r"FATAL:.*(?:too many clients|remaining connection slots are reserved|"
    r"too many connections)",
    re.IGNORECASE,
)


def _workload_durations(*, long_tasks: bool) -> dict[int, list[float]]:
    rng = random.Random(20261107)
    root_seconds = [(index % 10 + 1) / 2 for index in range(SHALLOW_ROOTS)]
    child_seconds = [(index % 10 + 1) / 2 for index in range(ONE_CHILD_ROOTS)]
    rng.shuffle(root_seconds)
    rng.shuffle(child_seconds)
    parents = rng.sample(range(SHALLOW_ROOTS), ONE_CHILD_ROOTS)
    durations = {index: [seconds] for index, seconds in enumerate(root_seconds)}
    for index, seconds in zip(parents, child_seconds, strict=True):
        durations[index].append(seconds)
    for index in range(CHAIN_ROOTS):
        depth = CHAIN_DEPTHS[index % len(CHAIN_DEPTHS)]
        durations[SHALLOW_ROOTS + index] = [
            rng.randint(1, 10) / 2 for _ in range(depth + 1)
        ]
    if long_tasks:
        # Preserve the exact family graph and draw independently for every node.
        long_rng = random.Random(20261109)
        return {
            index: [long_rng.randint(1, 30) for _ in seconds]
            for index, seconds in durations.items()
        }
    return durations


def _submit_roots(
    context: WeftContext,
    durations: dict[int, list[float]],
    *,
    result_timeout: float,
) -> dict[int, str]:
    # Mix deep and shallow work throughout the same backlog. Put a deepest
    # chain up front so external pressure tests the path from first dispatch.
    first_chain = SHALLOW_ROOTS + CHAIN_DEPTHS.index(max(CHAIN_DEPTHS))
    order = [index for index in durations if index != first_chain]
    random.Random(20261108).shuffle(order)
    tids: dict[int, str] = {}
    with context.broker() as broker:
        for index in (first_chain, *order):
            tid = generate_spawn_request_timestamp(context.broker_target, broker=broker)
            tid = submit_spawn_request(
                context.broker_target,
                tid=tid,
                taskspec=resolve_taskspec_payload(
                    {
                        "name": f"admission-root-{index}",
                        "spec": {
                            "type": "function",
                            "function_target": WORKLOAD_TARGET,
                        },
                        "io": {"outputs": {"outbox": ROOT_OUTBOX}},
                    },
                    tid=str(tid),
                    inherited_weft_context=str(context.root),
                ),
                work_payload={
                    "index": index,
                    "durations": durations[index],
                    "result_timeout": result_timeout,
                },
                config=context.config,
                broker=broker,
            )
            tids[index] = str(tid)
    return tids


@contextmanager
def _pg_sampler(
    enabled: bool, background_sessions: int
) -> Iterator[tuple[Callable[[], int] | None, Callable[[], None] | None]]:
    if not enabled:
        yield None, None
        return

    # Optional backend dependency, imported only in the real PostgreSQL case.
    import psycopg

    dsn = postgres_test_dsn()
    assert dsn is not None
    with (
        ExitStack() as pressure,
        psycopg.connect(
            dsn, autocommit=True, connect_timeout=5, options="-c statement_timeout=5000"
        ) as connection,
    ):
        for _ in range(background_sessions):
            pressure.enter_context(
                psycopg.connect(dsn, autocommit=True, connect_timeout=5)
            )
        maximum = connection.execute("SHOW max_connections").fetchone()
        assert maximum == (str(PG_CONNECTION_LIMIT),)

        def sample() -> int:
            row = connection.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'"
            ).fetchone()
            assert row is not None
            return int(row[0])

        yield sample, pressure.close if background_sessions else None


def _collect_outputs(
    outbox: Queue,
    monitor: QueueChangeMonitor,
    deadline: float,
    sample: Callable[[], int] | None,
    release_pressure: Callable[[], None] | None,
    *,
    output_count: int,
    pressure_idle_timeout: float = 90.0,
) -> tuple[list[dict[str, Any]], int | None, float | None]:
    outputs: list[dict[str, Any]] = []
    peak = None
    next_sample = 0.0
    released_at = None
    deepest = 0
    pressure_deadline = time.monotonic() + pressure_idle_timeout
    while True:
        now = time.monotonic()
        if sample is not None and now >= next_sample:
            peak = max(peak or 0, sample())
            assert peak <= PG_CONNECTION_LIMIT
            next_sample = now + PG_SAMPLE_INTERVAL_SECONDS
        batch = [json.loads(row) for row in outbox.read_many(output_count + 1)]
        outputs.extend(batch)
        if batch and release_pressure is not None:
            pressure_deadline = time.monotonic() + pressure_idle_timeout
            deepest = max(deepest, max(len(row["descendant_tids"]) for row in batch))
        assert len(outputs) <= output_count, "Duplicate workload output"
        if (
            release_pressure is not None
            and len(outputs) >= PG_PRESSURED_ROOT_OUTPUTS
            and deepest == max(CHAIN_DEPTHS)
        ):
            assert peak is not None and peak >= PG_PRESSURE_MIN_SESSIONS, (
                f"Workload did not reach the intended PostgreSQL pressure: {peak=}"
            )
            release_pressure()
            released_at = time.monotonic()
            release_pressure = None
        if len(outputs) == output_count:
            return outputs, peak, released_at
        next_deadline = (
            min(deadline, pressure_deadline)
            if release_pressure is not None
            else deadline
        )
        assert now < next_deadline, (
            f"Only {len(outputs)} of {output_count} results returned, {deepest=}, {peak=}"
        )
        wake_at = (
            min(next_deadline, next_sample) if sample is not None else next_deadline
        )
        monitor.wait(wake_at - time.monotonic())


def _check_outputs(
    outputs: list[dict[str, Any]],
    roots: dict[int, str],
    depths: dict[int, int],
) -> dict[str, str | None]:
    assert Counter(row["index"] for row in outputs) == Counter(roots.keys())
    submitters: dict[str, str | None] = dict.fromkeys(roots.values())
    for row in outputs:
        index = row["index"]
        chain = row["descendant_tids"]
        assert len(chain) == depths[index], f"Wrong depth for task index {index}"
        parent_tid = roots[index]
        for child_tid in chain:
            assert isinstance(child_tid, str) and child_tid.isdecimal()
            assert child_tid not in submitters, f"Repeated task identity {child_tid}"
            submitters[child_tid] = parent_tid
            parent_tid = child_tid
    return submitters


def _wait_for_drained_workload(
    context: WeftContext, manager_tid: str, deadline: float
) -> None:
    """Observe the producer boundary before asserting exact lifecycle history."""
    state_name = task_state_queue_name(manager_tid)
    with (
        context.queue(state_name, persistent=True) as state,
        closing(QueueChangeMonitor([state], config=context.config)) as monitor,
        context.broker() as broker,
    ):
        while True:
            body = broker.peek_one(state_name, order="newest", with_timestamps=False)
            assert body is not None
            frontiers = json.loads(body)["scheduler_frontiers"]
            if (
                all(
                    row["scheduling_class"] == "internal"
                    for row in frontiers["residents"].values()
                )
                and broker.peek_one(WEFT_SPAWN_REQUESTS_QUEUE) is None
                and broker.peek_one(frontiers["reserved_queue"]) is None
            ):
                return
            remaining = deadline - time.monotonic()
            assert remaining > 0, "Manager did not drain the public workload"
            monitor.wait(remaining)


def _workload_history(
    context: WeftContext,
    submitters: dict[str, str | None],
    *,
    targets: set[str],
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Correlate real launches and task-owned terminal events with every output."""
    starts: dict[str, int] = {}
    completions: dict[str, int] = {}
    launches: dict[str, str | None] = {}
    launched_ns: dict[str, int] = {}
    with context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
        for row, _ in iter_queue_json_entries(log, strict=True):
            event, tid = row["event"], row.get("tid")
            if (
                event == "task_spawned"
                and row["child_taskspec"]["spec"].get("function_target") in targets
            ):
                child = row["child_tid"]
                assert child not in launches, f"Duplicate launch of {child}"
                launches[child] = row["submitter_tid"]
                launched_ns[child] = row["launched_ns"]
            if tid not in submitters:
                continue
            if event == "work_started":
                assert tid not in starts, f"Duplicate work start for {tid}"
                starts[tid] = row["timestamp"]
            if (
                row.get("status") in TERMINAL_TASK_STATUSES
                and row.get("source") == "task"
            ):
                assert event == "work_completed" and row["status"] == "completed", row
                assert tid not in completions, f"Duplicate work completion for {tid}"
                completions[tid] = row["timestamp"]
    assert launches == submitters
    assert starts.keys() == completions.keys() == submitters.keys()
    return launched_ns, starts, completions


def _peak_running(starts: dict[str, int], completions: dict[str, int]) -> int:
    edges = [(stamp, 1) for stamp in starts.values()]
    edges.extend((stamp, -1) for stamp in completions.values())
    return max(accumulate(delta for _, delta in sorted(edges)))


def _manager_pressure(context: WeftContext, tid: str) -> dict[str, Any]:
    refusals: Counter[str] = Counter()
    peak_unsettled_startups = 0
    with context.queue(task_state_queue_name(tid)) as snapshots:
        for row, _ in iter_queue_json_entries(snapshots, strict=True):
            peak_unsettled_startups = max(
                peak_unsettled_startups,
                len(row["scheduler_frontiers"]["startup_feedback_tids"]),
            )
            refusals.update(
                {item["reason"] for item in row["dispatch_refusals"]["items"]}
            )
    return {
        "peak_unsettled_startups": peak_unsettled_startups,
        "checkpoints_with_refusal": dict(refusals),
    }


def _check_server_logs(container: str) -> None:
    logs = subprocess.run(
        ["docker", "logs", container],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    rejected = PG_SLOT_REJECTION.findall(logs.stdout + logs.stderr)
    assert not rejected, (
        f"PostgreSQL rejected connections ({len(rejected)}): {rejected[:5]}"
    )


@pytest.mark.parametrize(
    ("long_tasks", "workload_timeout", "pressure_idle_timeout"),
    [
        pytest.param(False, 600.0, 90.0, id="short", marks=pytest.mark.timeout(1500)),
        # Six nodes can each do 30 seconds of work before a root returns.
        pytest.param(
            True, 1800.0, 240.0, id="rand-1-30s", marks=pytest.mark.timeout(2700)
        ),
    ],
)
@pytest.mark.parametrize(
    "background_sessions",
    [pytest.param(0, id="baseline")]
    + (
        [pytest.param(PG_BACKGROUND_SESSIONS, id="pg-pressure")]
        if active_test_backend() == "postgres"
        else []
    ),
)
def test_nested_admission_sustains_a_large_backlog(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    background_sessions: int,
    long_tasks: bool,
    workload_timeout: float,
    pressure_idle_timeout: float,
) -> None:
    postgres = active_test_backend() == "postgres"
    if postgres:
        container = request.getfixturevalue("isolated_postgres_server")
        monkeypatch.delenv(WEFT_ADMISSION_MAX_CONNECTIONS, raising=False)
        if background_sessions and WEFT_ADMISSION_RESERVE_FRACTION not in os.environ:
            monkeypatch.setenv(WEFT_ADMISSION_RESERVE_FRACTION, PG_PRESSURED_RESERVE)

    with WeftTestHarness(manager_timeout=workload_timeout) as harness:
        context = harness.context
        assert context.backend_name == ("postgres" if postgres else "sqlite")
        durations = _workload_durations(long_tasks=long_tasks)
        roots = _submit_roots(context, durations, result_timeout=workload_timeout)
        with (
            _pg_sampler(postgres, background_sessions) as (sample, release_pressure),
            context.queue(ROOT_OUTBOX, persistent=True) as outbox,
            closing(QueueChangeMonitor([outbox], config=context.config)) as monitor,
        ):
            start = time.monotonic()
            deadline = start + workload_timeout
            record, started, _ = start_manager(context)
            assert started
            manager_tid = record["tid"]
            harness.register_manager_tid(manager_tid)
            outputs, peak_sessions, released_at = _collect_outputs(
                outbox,
                monitor,
                deadline,
                sample,
                release_pressure,
                output_count=ROOT_COUNT,
                pressure_idle_timeout=pressure_idle_timeout,
            )
            elapsed = time.monotonic() - start
            if background_sessions:
                assert released_at is not None
            depths = {index: len(seconds) - 1 for index, seconds in durations.items()}
            submitters = _check_outputs(outputs, roots, depths)
            _wait_for_drained_workload(context, manager_tid, deadline)
            _, starts, completions = _workload_history(
                context, submitters, targets={WORKLOAD_TARGET}
            )
            peak_running = _peak_running(starts, completions)
            assert outbox.read_one() is None, "Unexpected additional root output"

        metrics = {
            "backend": context.backend_name,
            "admission_ceiling": context.config["ADMISSION_MAX_CONNECTIONS"],
            "admission_reserve_fraction": context.config["ADMISSION_RESERVE_FRACTION"],
            "background_pg_sessions": background_sessions,
            "duration_profile": "rand-1-30s" if long_tasks else "short",
            "roots": len(roots),
            "tasks": len(submitters),
            "elapsed_seconds": round(elapsed, 2),
            "tasks_per_second": round(len(submitters) / elapsed, 2),
            "peak_running_tasks": peak_running,
            "peak_pg_sessions_sampled": peak_sessions,
            "pg_pressure_seconds": round(released_at - start, 2)
            if released_at
            else None,
            **_manager_pressure(context, manager_tid),
        }

    if postgres:
        _check_server_logs(container)
    print(f"Nested admission load: {json.dumps(metrics, sort_keys=True)}")


def _timing_summary(seconds: list[float]) -> dict[str, float]:
    return {
        "median": round(median(seconds), 3),
        "p95": round(quantiles(seconds, n=20, method="inclusive")[-1], 3),
    }


def _start_timings(
    tids: list[str], launches: dict[str, int], starts: dict[str, int]
) -> dict[str, dict[str, float]]:
    return {
        "submit_to_launch_return_seconds": _timing_summary(
            [(launches[tid] - int(tid)) / 1e9 for tid in tids]
        ),
        "launch_return_to_start_seconds": _timing_summary(
            [(starts[tid] - launches[tid]) / 1e9 for tid in tids]
        ),
        "submit_to_start_seconds": _timing_summary(
            [(starts[tid] - int(tid)) / 1e9 for tid in tids]
        ),
    }


@pytest.mark.timeout(900)
@pytest.mark.parametrize(
    "enrichment_count",
    [pytest.param(50, id="101-tasks"), pytest.param(87, id="175-tasks")],
)
def test_ops_burst_admission(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    enrichment_count: int,
) -> None:
    postgres = active_test_backend() == "postgres"
    if postgres:
        container = request.getfixturevalue("isolated_postgres_server")
    monkeypatch.setenv(
        WEFT_ADMISSION_MAX_CONNECTIONS, str(PG_CONNECTION_LIMIT if postgres else 0)
    )
    monkeypatch.setenv(WEFT_ADMISSION_RESERVE_FRACTION, "0.1")
    rng = random.Random(20261107)
    child_seconds = [rng.randint(16, 28) for _ in range(enrichment_count)]

    with WeftTestHarness(manager_timeout=OPS_BURST_TIMEOUT_SECONDS) as harness:
        context = harness.context
        assert context.backend_name == ("postgres" if postgres else "sqlite")
        record, started, _ = start_manager(context)
        assert started
        manager_tid = record["tid"]
        harness.register_manager_tid(manager_tid)
        with (
            _pg_sampler(postgres, 0) as (sample, _),
            context.queue(OPS_BURST_OUTBOX, persistent=True) as outbox,
            closing(QueueChangeMonitor([outbox], config=context.config)) as monitor,
        ):
            deadline = time.monotonic() + OPS_BURST_TIMEOUT_SECONDS
            with connect(autostart=False) as client:
                rollup = client.submit(
                    ops_burst_taskspec("ops-burst-rollup", OPS_ROLLUP_TARGET),
                    payload={"child_seconds": child_seconds},
                )
            harness.register_tid(rollup.tid)
            outputs, peak_sessions, _ = _collect_outputs(
                outbox,
                monitor,
                deadline,
                sample,
                None,
                output_count=enrichment_count + 1,
            )
            rollup_outputs = [
                row for row in outputs if row["index"] == OPS_ROLLUP_INDEX
            ]
            assert len(rollup_outputs) == 1
            enrichment_tids = rollup_outputs[0]["descendant_tids"]
            assert len(enrichment_tids) == enrichment_count
            parents = dict(enumerate(enrichment_tids))
            results = [row for row in outputs if row["index"] != OPS_ROLLUP_INDEX]
            submitters = _check_outputs(results, parents, dict.fromkeys(parents, 1))
            assert rollup.tid not in submitters
            submitters.update(dict.fromkeys(enrichment_tids, rollup.tid))
            submitters[rollup.tid] = None
            assert len(submitters) == 2 * enrichment_count + 1
            _wait_for_drained_workload(context, manager_tid, deadline)
            launches, starts, completions = _workload_history(
                context,
                submitters,
                targets={OPS_ROLLUP_TARGET, OPS_ENRICHMENT_TARGET, WORKLOAD_TARGET},
            )
            assert outbox.read_one() is None, "Unexpected additional ops burst output"

        leaf_tids = [row["descendant_tids"][0] for row in results]
        submission_times = [int(tid) for tid in enrichment_tids]
        elapsed = (
            max(completions[tid] for tid in enrichment_tids) - min(submission_times)
        ) / 1e9
        metrics = {
            "backend": context.backend_name,
            "admission_ceiling": context.config["ADMISSION_MAX_CONNECTIONS"],
            "tasks": len(submitters),
            "enrichments": enrichment_count,
            "submission_span_seconds": round(
                (max(submission_times) - min(submission_times)) / 1e9, 3
            ),
            "elapsed_seconds": round(elapsed, 3),
            "enrichments_per_second": round(enrichment_count / elapsed, 3),
            "peak_running_tasks": _peak_running(starts, completions),
            "peak_pg_sessions_sampled": peak_sessions,
            "enrichments_launched_after_rollup": sum(
                launches[tid] > completions[rollup.tid] for tid in enrichment_tids
            ),
            "enrichment_start": _start_timings(enrichment_tids, launches, starts),
            "leaf_start": _start_timings(leaf_tids, launches, starts),
            "result_wait_cpu_seconds": round(
                sum(row["wait_cpu_seconds"] for row in results), 3
            ),
            "result_wait_elapsed_seconds": round(
                sum(row["wait_elapsed_seconds"] for row in results), 3
            ),
            **_manager_pressure(context, manager_tid),
        }
    if postgres:
        _check_server_logs(container)
    print(f"Ops admission burst: {json.dumps(metrics, sort_keys=True)}")
