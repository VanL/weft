"""Real global PG pressure and bounded finite progress [MA-1.8], [SB-0.4]."""

from __future__ import annotations

import json
import multiprocessing
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from multiprocessing.connection import Connection
from typing import Any

import pytest

from simplebroker import BrokerSession, BrokerTarget, serialize_config
from tests.helpers.family_workload import FAMILY_EVENTS
from tests.helpers.test_backend import active_test_backend
from tests.helpers.weft_harness import (
    COMPLETION_WAIT_REORDER_WINDOW_NS,
    WeftTestHarness,
)
from weft._constants import (
    ADMISSION_SERVICE_RESERVE_SLOTS,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    resolve_runtime_config,
)
from weft.client import WeftClient
from weft.context import build_context
from weft.core.queue_wait import QueueChangeMonitor
from weft.helpers import closing_queue_iterator, iter_queue_json_entries

pytest_plugins = ["tests.helpers.pytest_pg_runtime"]

pytestmark = [
    pytest.mark.shared,
    pytest.mark.timeout(900),
    pytest.mark.skipif(
        active_test_backend() != "postgres", reason="requires bin/pytest-pg"
    ),
]

# Bound this fixture's explicit topology, never arbitrary TaskSpecs. The installed
# PG backend defaults to at most three pool connections plus one shared listener
# per process (simplebroker_pg.runner.PostgresRunner / shared activity registry).
# A host function task has wrapper + worker; the complete transient finite path
# has rollup + enrichment + leaf. Worker phases force three simultaneous public
# checkouts and listener/result waits. Observations below validate the bound; no
# budget or child forecast enters a request or production admission calculation.
FIXTURE_PROCESS_CONNECTION_BOUND = 3 + 1
FIXTURE_TASK_CONNECTION_BOUND = 2 * FIXTURE_PROCESS_CONNECTION_BOUND
FIXTURE_PATH_CONNECTION_BOUND = 3 * FIXTURE_TASK_CONNECTION_BOUND


def _wait(
    monitor: QueueChangeMonitor, condition: Callable[[], bool], *, timeout: float = 90.0
) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("PG fixture did not reach its evidence barrier")
        monitor.wait(min(remaining, 0.1))


def _events(harness: WeftTestHarness) -> list[dict[str, Any]]:
    with harness.context.queue(FAMILY_EVENTS, persistent=True) as queue:
        return [json.loads(body) for body in queue.peek_generator()]


def _release(harness: WeftTestHarness, kind: str, index: int) -> None:
    suffix = "leaf.release" if kind == "leaf" else "release"
    with harness.context.queue(
        f"test.family.{suffix}.{index}", persistent=True
    ) as queue:
        queue.write("continue")


def _collect_enrichment(
    harness: WeftTestHarness, tid: str, monitor: QueueChangeMonitor
) -> dict[str, Any]:
    """Collect the fixture's known default output route without history rediscovery."""
    result: list[str] = []
    with harness.context.queue(
        f"T{tid}.{QUEUE_OUTBOX_SUFFIX}", persistent=True
    ) as outbox:

        def output_visible() -> bool:
            body = outbox.read_one()
            if body is not None:
                result.append(body)
            return bool(result)

        _wait(monitor, output_visible)
    value = json.loads(result[0])
    assert isinstance(value, dict)
    return value


def _require_task_completions(harness: WeftTestHarness, tids: set[str]) -> None:
    """Validate the completed outputs against one cursor-based terminal-log pass."""
    completed: set[str] = set()
    cursor = 0
    with (
        harness.context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log,
        closing(QueueChangeMonitor([log])) as monitor,
    ):

        def terminal_facts_visible() -> bool:
            nonlocal cursor
            entries = iter_queue_json_entries(
                log,
                since_timestamp=max(0, cursor - COMPLETION_WAIT_REORDER_WINDOW_NS),
                strict=True,
            )
            with closing_queue_iterator(entries) as rows:
                for event, timestamp in rows:
                    cursor = max(cursor, timestamp)
                    if (
                        event.get("event") == "work_completed"
                        and event.get("source") == "task"
                    ):
                        completed.add(event["tid"])
            return tids <= completed

        _wait(monitor, terminal_facts_visible)


def _pressure_actor(
    target: BrokerTarget, config: str, owner: str | None, pipe: Connection
) -> None:
    # Optional PG dependencies belong only to this real PG-selected process.
    import psycopg
    from simplebroker_pg import bind_connection_owner

    raw: list[Any] = []
    try:
        with ExitStack() as resources:
            if owner is not None:
                bind_connection_owner(owner)
                session = resources.enter_context(
                    BrokerSession.connect(target, config=resolve_runtime_config(config))
                )
                queue = resources.enter_context(session.queue("test.external.pressure"))
                queue.write("external-owner-open")
                resources.enter_context(closing(QueueChangeMonitor([queue])))
            pipe.send("ready")
            while True:
                command = pipe.recv()
                if isinstance(command, int):
                    for _ in range(command):
                        connection = resources.enter_context(
                            psycopg.connect(
                                target.target,
                                autocommit=True,
                                application_name="external-unattributed",
                            )
                        )
                        connection.execute("SELECT 1")
                        raw.append(connection)
                    pipe.send(len(raw))
                elif command == "release":
                    for connection in raw:
                        connection.close()
                    raw.clear()
                    pipe.send("released")
                elif command == "stop":
                    return
                else:
                    raise ValueError("unexpected pressure command")
    finally:
        pipe.close()


@contextmanager
def _pressure(harness: WeftTestHarness, owner: str | None) -> Iterator[Connection]:
    spawn = multiprocessing.get_context("spawn")
    received, sent = spawn.Pipe()
    process = spawn.Process(
        target=_pressure_actor,
        args=(
            harness.context.broker_target,
            serialize_config(harness.context.broker_config),
            owner,
            sent,
        ),
    )
    process.start()
    sent.close()
    assert process.pid is not None
    harness.register_pid(process.pid)
    try:
        assert received.poll(30.0), "external process did not open resources"
        assert received.recv() == "ready"
        yield received
    finally:
        if process.is_alive():
            received.send("stop")
        process.join(10.0)
        if process.is_alive():
            process.terminate()
            process.join(5.0)
        if process.is_alive():
            process.kill()
            process.join(5.0)
        received.close()
        assert not process.is_alive(), "external connection process survived cleanup"
        exitcode = process.exitcode
        process.close()
        assert exitcode == 0


def _calibrate_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise every known opening phase against the independently derived bound."""
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", "1")
    with WeftTestHarness(manager_timeout=120.0) as harness:
        harness.ensure_foreground_manager()
        with (
            WeftClient(context=harness.context) as client,
            harness.context.queue(FAMILY_EVENTS, persistent=True) as queue,
            closing(QueueChangeMonitor([queue])) as monitor,
        ):
            rollup = client.submit(
                {
                    "name": "calibrate-rollup",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.helpers.family_workload:family_rollup",
                    },
                },
                payload={
                    "count": 1,
                    "gated": True,
                    "leaf_gated": True,
                    "measure_usage": True,
                    "materialize_pool": True,
                },
            )
            harness.register_tid(rollup.tid)
            root_result = rollup.result(timeout=60.0)
            assert root_result.status == "completed", root_result.error
            assert isinstance(root_result.value, list)
            parent_tid = root_result.value[0]
            harness.register_tid(parent_tid)
            _wait(
                monitor,
                lambda: any(
                    row["event"] == "enrichment_waiting" for row in _events(harness)
                ),
            )
            _release(harness, "enrichment", 0)
            _wait(
                monitor,
                lambda: any(row["event"] == "leaf_waiting" for row in _events(harness)),
            )
            _release(harness, "leaf", 0)
            result = client.task(parent_tid).result(timeout=60.0)
            assert result.status == "completed", result.error
            assert isinstance(result.value, dict)
            harness.register_tid(result.value["child_tid"])
            phases = _events(harness)
            assert {
                "rollup_pool_ready",
                "enrichment_waiting",
                "leaf_waiting",
                "enrichment_completed",
            } <= {row["event"] for row in phases}
            maxima: dict[str, int] = {}
            for row in phases:
                owner = row["connection_owner"]
                value = int(row["usage"]["by_owner"].get(owner, 0))
                maxima[owner] = max(maxima.get(owner, 0), value)
            assert len(maxima) == 3
            assert all(
                3 <= value <= FIXTURE_TASK_CONNECTION_BOUND for value in maxima.values()
            ), maxima
            assert sum(maxima.values()) <= FIXTURE_PATH_CONNECTION_BOUND


def _read_denial(
    capsys: pytest.CaptureFixture[str],
    public_limit: int,
    diagnostics: list[dict[str, Any]],
) -> bool:
    """Observe the real diagnostic sink without replacing any admission operation."""
    for line in capsys.readouterr().err.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            diagnostics.append(record)
    return any(
        row.get("event") == "admission_state"
        and row.get("state") in {"public_paused", "all_paused"}
        and row.get("used", 0) >= public_limit
        for row in diagnostics
    )


@pytest.mark.parametrize("frontiers", [1, 5])
def test_real_global_pressure_pauses_then_drains_nested_fifo_backlog(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    frontiers: int,
    isolated_postgres_server: None,
    record_property: Callable[[str, Any], None],
) -> None:
    """Global used includes external owners and unlabeled clients; queued tasks do not.

    Each case owns its physical server because schema isolation cannot bound
    unrelated pytest workers' global pressure. All pytest workers remain active;
    foreign labeled and unlabeled processes create real controlled server load.
    Owned raw sessions hold the entire configured public limit. Filling only
    a sampled gap would let unrelated suite workers closing sessions release
    our supposed saturation before the Manager's next measured decision.
    """
    # Optional backend dependencies: SQLite never loads PG accounting.
    from simplebroker_pg import get_connection_stats, get_connection_usage

    monkeypatch.setenv("WEFT_ADMISSION_RESERVE_FRACTION", "0")
    monkeypatch.setenv("WEFT_MANAGER_SERVE_LOG_LEVEL", "info")
    _calibrate_fixture(monkeypatch)
    monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", str(frontiers))
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness(manager_timeout=240.0) as harness:
        with harness.context.queue(
            "test.family.measurement", persistent=True
        ) as measurement:
            outside = int(get_connection_usage(measurement)["total"])
            ceiling = (
                outside
                + 2 * FIXTURE_PROCESS_CONNECTION_BOUND
                + frontiers * FIXTURE_PATH_CONNECTION_BOUND
                + ADMISSION_SERVICE_RESERVE_SLOTS
            )
            assert ceiling <= int(get_connection_stats(measurement)["max_connections"])
        # Rebuild the immutable context before starting its Manager. The
        # broker target/schema remains this harness's prepared target.
        monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", str(ceiling))
        original_target = harness.context.broker_target
        harness._context = build_context(
            spec_context=harness.root,
            config=resolve_runtime_config(
                {
                    **dict(harness.context.config),
                    "ADMISSION_MAX_CONNECTIONS": ceiling,
                    "MANAGER_SERVE_LOG_ACTIVE": True,
                    "MANAGER_SERVE_LOG_LEVEL": "info",
                }
            ),
        )
        assert harness.context.broker_target == original_target
        context = harness.context
        harness.ensure_foreground_manager()
        owner = f"external-holder-{frontiers}"
        with (
            _pressure(harness, owner),
            _pressure(harness, None) as unlabeled,
            WeftClient(context=context) as client,
            context.queue("test.family.measurement", persistent=True) as measurement,
            context.queue(FAMILY_EVENTS, persistent=True) as events,
            context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as source,
            closing(QueueChangeMonitor([events, source])) as monitor,
        ):
            _wait(
                monitor,
                lambda: (
                    int(get_connection_usage(measurement)["by_owner"].get(owner, 0)) > 0
                ),
            )
            public_limit = ceiling - ADMISSION_SERVICE_RESERVE_SLOTS
            added = public_limit
            physical_limit = int(get_connection_stats(measurement)["max_connections"])
            # The fixture owns physical capacity; measure actual opening headroom.
            # Await actual opening headroom without raising C or changing
            # any deadline; two existing process-topology bounds cover
            # observer/Manager pool growth while raw sessions open.
            _wait(
                monitor,
                lambda: (
                    int(get_connection_usage(measurement)["total"])
                    + added
                    + 2 * FIXTURE_PROCESS_CONNECTION_BOUND
                    <= physical_limit
                ),
            )
            unlabeled.send(added)
            assert unlabeled.poll(30.0)
            assert unlabeled.recv() == added
            _wait(
                monitor,
                lambda: int(get_connection_usage(measurement)["total"]) >= public_limit,
            )
            saturated = get_connection_usage(measurement)
            assert saturated["total"] - sum(saturated["by_owner"].values()) >= added
            assert saturated["by_owner"][owner] > 0
            rollup = client.submit(
                {
                    "name": "loaded-family-rollup",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.helpers.family_workload:family_rollup",
                    },
                },
                payload={
                    "count": 70,
                    "gated": True,
                    "leaf_gated": True,
                    "measure_usage": True,
                    "materialize_pool": True,
                },
            )
            harness.register_tid(rollup.tid)
            diagnostics: list[dict[str, Any]] = []

            def denied_attempt_visible() -> bool:
                return _read_denial(capsys, public_limit, diagnostics)

            _wait(monitor, denied_attempt_visible)
            # A paused diagnostic may precede submission because state
            # edges are deduplicated. The still-held acknowledged own
            # floor, exact accepted row and absence of work prove that
            # the gate remains closed now despite unrelated client churn.
            assert source.peek_one(exact_timestamp=int(rollup.tid)) is not None
            assert not any(
                row["event"] == "rollup_returned" for row in _events(harness)
            )
            denial = next(
                row
                for row in diagnostics
                if row.get("event") == "admission_state"
                and row.get("used", 0) >= public_limit
            )
            assert denial["used"] == denial["observed"]
            unlabeled.send("release")
            assert unlabeled.poll(30.0) and unlabeled.recv() == "released"
            _wait(
                monitor,
                lambda: int(get_connection_usage(measurement)["total"]) < public_limit,
            )
            root_result = rollup.result(timeout=90.0)
            assert root_result.status == "completed", root_result.error
            tids = root_result.value
            assert isinstance(tids, list) and len(tids) == 70
            for tid in tids:
                harness.register_tid(tid)
            _wait(
                monitor,
                lambda: (
                    sum(
                        row["event"] == "enrichment_waiting" for row in _events(harness)
                    )
                    >= frontiers
                ),
            )
            first = [
                row["index"]
                for row in _events(harness)
                if row["event"] == "enrichment_waiting"
            ][:frontiers]
            for index in first:
                _release(harness, "enrichment", index)
            _wait(
                monitor,
                lambda: (
                    sum(row["event"] == "leaf_waiting" for row in _events(harness))
                    >= frontiers
                ),
            )
            assert source.peek_one() is not None
            # The descendant lies behind >64 accepted siblings. Classifying
            # only the next five/one page would trap these occupied paths.
            for index in first:
                _release(harness, "leaf", index)
            first_result = _collect_enrichment(harness, tids[first[0]], monitor)
            assert first_result["value"] == f"leaf:{first[0]}"
            assert source.peek_one() is not None
            for index in range(70):
                if index not in first:
                    _release(harness, "enrichment", index)
                    _release(harness, "leaf", index)
            completed_tids = {rollup.tid, *tids}
            for index, tid in enumerate(tids):
                # Outbox collection consumes a result; reuse the first
                # value already collected for the backlog progress oracle.
                result = (
                    first_result
                    if index == first[0]
                    else _collect_enrichment(harness, tid, monitor)
                )
                assert result["value"] == f"leaf:{index}"
                harness.register_tid(result["child_tid"])
                completed_tids.add(result["child_tid"])
            _require_task_completions(harness, completed_tids)
            rows = _events(harness)
            assert sum(row["event"] == "leaf_started" for row in rows) == 70
            assert all(row["root_tid"] == rollup.tid for row in rows)
            assert all(
                int(row["usage"]["by_owner"].get(row["connection_owner"], 0))
                <= FIXTURE_TASK_CONNECTION_BOUND
                for row in rows
            )
            assert get_connection_usage(measurement)["by_owner"].get(owner, 0) > 0
            with context.queue(FAMILY_EVENTS, persistent=True) as metric_queue:
                timed_rows = [
                    (json.loads(body), int(stamp))
                    for body, stamp in metric_queue.peek_generator(with_timestamps=True)
                ]
            start = min(
                stamp
                for row, stamp in timed_rows
                if row["event"] == "enrichment_started"
            )
            end = max(
                stamp
                for row, stamp in timed_rows
                if row["event"] == "enrichment_completed"
            )
            elapsed = (end - start) / 1_000_000_000
            assert elapsed > 0
            sampled_total_peak = max(
                int(row["usage"]["total"]) for row, _stamp in timed_rows
            )
            sampled_owner_peak = max(
                int(row["usage"]["by_owner"].get(row["connection_owner"], 0))
                for row, _stamp in timed_rows
            )
            record_property(
                "family_progress_metrics",
                json.dumps(
                    {
                        "W": frontiers,
                        "completed": 70,
                        "configured_C": ceiling,
                        "public_limit": public_limit,
                        "saturation_U": saturated["total"],
                        "sampled_U_peak": sampled_total_peak,
                        "sampled_task_owner_peak": sampled_owner_peak,
                        "topology_H": FIXTURE_PATH_CONNECTION_BOUND,
                        "broker_elapsed_s": elapsed,
                        "enrichments_per_s": 70 / elapsed,
                    }
                ),
            )
