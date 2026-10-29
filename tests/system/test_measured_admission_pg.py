"""Real global PG pressure and whole finite-input progress [MA-1.8], [SB-0.4]."""

from __future__ import annotations

import json
import math
import multiprocessing
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any

import pytest

import weft.core.manager as manager_mod
from simplebroker import BrokerSession, BrokerTarget, serialize_config
from tests.core.test_manager import make_manager_spec
from tests.core.test_manager_family_maintenance import (
    _join_waiting_processes,
    _publish_waiting_task,
)
from tests.helpers.family_workload import FAMILY_EVENTS
from tests.helpers.manager_dispatch import dispatch_witness_body, settled_launches
from tests.helpers.reactor_driver import drive_until
from tests.helpers.test_backend import active_test_backend
from tests.helpers.weft_harness import (
    COMPLETION_WAIT_REORDER_WINDOW_NS,
    WeftTestHarness,
)
from tests.tasks.sample_targets import wait_for_file
from weft._constants import (
    ADMISSION_SERVICE_RESERVE_SLOTS,
    CONTROL_PING,
    MANAGER_STARTUP_FAST_PATH_FRACTION,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    load_config,
    resolve_runtime_config,
)
from weft.client import WeftClient
from weft.context import build_context
from weft.core.control_messages import encode_control_message
from weft.core.manager import Manager
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.taskspec import TaskSpec
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


def test_startup_pressure_waits_without_hot_loop_and_reopens_on_fresh_usage(
    monkeypatch: pytest.MonkeyPatch,
    isolated_postgres_server: None,
) -> None:
    """Real U crosses H with two unfinished startups, then reopens on fresh U."""
    public_limit = 72
    startup_threshold = math.floor(public_limit * MANAGER_STARTUP_FAST_PATH_FRACTION)
    with WeftTestHarness() as harness:
        with harness.context.queue("startup-pressure-ids", persistent=True) as queue:
            manager_tid = str(queue.generate_timestamp())
        manager = Manager(
            harness.context.broker_target,
            make_manager_spec(manager_tid, idle_timeout=0.0),
            config=load_config(
                {
                    "WEFT_ADMISSION_MAX_CONNECTIONS": 80,
                    "WEFT_ADMISSION_RESERVE_FRACTION": 0.1,
                    "WEFT_TASK_MONITOR_ENABLED": False,
                    "WEFT_LIVENESS_MONITOR_ENABLED": False,
                    "WEFT_HEARTBEAT_ENABLED": False,
                    "WEFT_AUTOSTART_TASKS": False,
                }
            ),
        )
        spawn = multiprocessing.get_context("spawn")
        publication, release = spawn.Event(), spawn.Event()
        ready = [spawn.Event() for _ in range(3)]
        started = [spawn.Event() for _ in range(3)]
        processes: list[BaseProcess] = []

        def launch(
            _task_cls: type[Any], _target: Any, child_spec: TaskSpec, **kwargs: Any
        ) -> BaseProcess:
            index = len(processes)
            process = spawn.Process(
                target=_publish_waiting_task,
                args=(
                    child_spec.model_dump(mode="json"),
                    kwargs["runtime_identity"].to_dict(),
                    ready[index],
                    release,
                    publication,
                    started[index],
                    False,
                    True,
                ),
            )
            processes.append(process)
            process.start()
            assert process.pid is not None
            harness.register_pid(process.pid)
            assert started[index].wait(timeout=30.0)
            return process

        monkeypatch.setattr(manager_mod, "launch_task_process", launch)
        try:
            manager._refresh_scheduler_evidence()
            manager._admission_refresh_after = time.monotonic() + 120.0
            manager._next_inactive_probe_at = time.monotonic() + 120.0
            source = manager._queue(manager._queue_names["inbox"])
            reserved = manager._queue(manager._queue_names["reserved"])
            body = json.dumps(
                {
                    "spec": {
                        "type": "function",
                        "function_target": "tests.tasks.sample_targets:provide_payload",
                    }
                }
            )
            request_ids = [int(source.write(body)) for _ in range(2)]
            tids = [str(request_id) for request_id in request_ids]
            for tid in tids:
                harness.register_tid(tid)

            def receipts(count: int) -> bool:
                return all(tid in manager._child_processes for tid in tids[:count])

            drive_until(
                lambda: receipts(2),
                bool,
                step=manager.process_once,
                wait=manager.wait_for_activity,
                timeout=30.0,
            )
            assert not ready[0].is_set()
            with _pressure(harness, None) as pressure:
                pressure.send(startup_threshold)
                assert pressure.poll(30.0) and pressure.recv() == startup_threshold
                # A turn that receives the second launch may also dispatch a third.
                # Make that request eligible only after real pressure is installed.
                request_ids.append(int(source.write(body)))
                tids.append(str(request_ids[-1]))
                harness.register_tid(tids[-1])
                observations: list[int] = []
                read_usage = manager._read_postgres_connection_usage

                def observe_usage() -> Any:
                    usage = read_usage()
                    observations.append(int(usage["total"]))
                    return usage

                # Count real observations without substituting the usage or decision.
                monkeypatch.setattr(
                    manager, "_read_postgres_connection_usage", observe_usage
                )
                drive_until(
                    lambda: manager._startup_blocked,
                    bool,
                    step=manager.process_once,
                    wait=manager.wait_for_activity,
                    timeout=30.0,
                )
                assert startup_threshold <= observations[-1] < public_limit
                assert not manager._admission_blocked_lanes
                assert source.peek_one(exact_timestamp=request_ids[2]) is not None
                assert reserved.peek_one(exact_timestamp=request_ids[2]) is None
                manager._admission_retry_after_ns = time.time_ns() + 120_000_000_000
                observed = len(observations)
                for _ in range(5):
                    manager.process_once()
                    manager.wait_for_activity(timeout=0.0)
                assert len(observations) == observed
                assert not manager._queue_has_pending(source)
                assert not manager._has_pending_messages()
                assert not started[2].is_set()

                reply_name = f"T{int(manager.tid) + 1}.ctrl_in"
                manager._queue(manager._queue_names["ctrl_in"]).write(
                    encode_control_message(
                        CONTROL_PING, request_id="startup-pressure", reply_to=reply_name
                    )
                )
                manager.process_once()
                response = json.loads(str(manager._queue(reply_name).read_one()))
                assert response["request_id"] == "startup-pressure"
                assert response["message"] == "PONG"

                pressure.send("release")
                assert pressure.poll(30.0) and pressure.recv() == "released"
                manager._admission_retry_after_ns = time.time_ns() - 1
                drive_until(
                    lambda: receipts(3),
                    bool,
                    step=manager.process_once,
                    wait=manager.wait_for_activity,
                    timeout=30.0,
                )
                assert observations[-1] < startup_threshold
                assert not ready[0].is_set()
                assert source.peek_one(exact_timestamp=request_ids[2]) is None
                publication.set()
                release.set()
                outboxes = [manager._queue(f"T{tid}.outbox") for tid in tids]
                drive_until(
                    lambda: all(queue.peek_one() is not None for queue in outboxes),
                    bool,
                    step=manager.process_once,
                    wait=manager.wait_for_activity,
                    timeout=60.0,
                )
                assert [
                    json.loads(queue.read_one() or "null") for queue in outboxes
                ] == [
                    {"data": "payload"},
                    {"data": "payload"},
                    {"data": "payload"},
                ]
        finally:
            publication.set()
            release.set()
            _join_waiting_processes(processes)
            manager.stop(join=False)
            manager.cleanup()


@pytest.mark.parametrize("count", [6, 8])
def test_real_global_pressure_pauses_then_drains_nested_fifo_backlog(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    count: int,
    isolated_postgres_server: None,
    record_property: Callable[[str, Any], None],
) -> None:
    """Global used includes external owners and unlabeled clients; queued tasks do not.

    The whole input fits its independently established topology envelope:
    outside + four observer/Manager/holder process bounds + count complete path bounds
    is at most C-R, below physical300. No runtime receives those bounds.
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
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness(manager_timeout=240.0) as harness:
        with harness.context.queue(
            "test.family.measurement", persistent=True
        ) as measurement:
            outside = int(get_connection_usage(measurement)["total"])
            ceiling = (
                outside
                + 4 * FIXTURE_PROCESS_CONNECTION_BOUND
                + count * FIXTURE_PATH_CONNECTION_BOUND
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
        owner = f"external-holder-{count}"
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
                    "count": count,
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
            assert isinstance(tids, list) and len(tids) == count
            for tid in tids:
                harness.register_tid(tid)
            _wait(
                monitor,
                lambda: (
                    sum(
                        row["event"] == "enrichment_waiting" for row in _events(harness)
                    )
                    == count
                ),
            )
            # The entire finite input is live before any preparation release.
            # No pending sibling is withheld to simulate a concurrency bound.
            for index in range(count):
                _release(harness, "enrichment", index)
            _wait(
                monitor,
                lambda: (
                    sum(row["event"] == "leaf_waiting" for row in _events(harness))
                    == count
                ),
            )
            assert not any(
                row["event"] == "enrichment_completed" for row in _events(harness)
            )
            for index in range(count):
                _release(harness, "leaf", index)
            completed_tids = {rollup.tid, *tids}
            for index, tid in enumerate(tids):
                result = _collect_enrichment(harness, tid, monitor)
                assert result["value"] == f"leaf:{index}"
                harness.register_tid(result["child_tid"])
                completed_tids.add(result["child_tid"])
            _require_task_completions(harness, completed_tids)
            rows = _events(harness)
            assert sum(row["event"] == "leaf_started" for row in rows) == count
            assert all(row["root_tid"] == rollup.tid for row in rows)
            assert all(row["admission_max_connections"] == ceiling for row in rows)
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
                        "input_count": count,
                        "completed": count,
                        "configured_C": ceiling,
                        "public_limit": public_limit,
                        "saturation_U": saturated["total"],
                        "sampled_U_peak": sampled_total_peak,
                        "sampled_task_owner_peak": sampled_owner_peak,
                        "topology_complete_input_bound": count
                        * FIXTURE_PATH_CONNECTION_BOUND,
                        "broker_elapsed_s": elapsed,
                        "enrichments_per_s": count / elapsed,
                    }
                ),
            )


@pytest.mark.parametrize("configured_ceiling", [None, 0])
def test_native_postgres_ceiling_denies_real_pressure_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    configured_ceiling: int | None,
    isolated_postgres_server: None,
) -> None:
    """Unset/zero uses native metadata; real external usage closes the same gate.

    A large operating reserve makes saturation fit comfortably inside the
    unchanged physical300. It changes reserve policy, never server capacity.
    """
    # Optional backend dependency: this module is PG-selected only.
    from simplebroker_pg import get_connection_stats, get_connection_usage

    if configured_ceiling is None:
        monkeypatch.delenv("WEFT_ADMISSION_MAX_CONNECTIONS", raising=False)
    else:
        monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", str(configured_ceiling))
    monkeypatch.setenv("WEFT_ADMISSION_RESERVE_FRACTION", "0.75")
    with WeftTestHarness() as harness:
        harness._context = build_context(
            spec_context=harness.root,
            config=resolve_runtime_config(
                {
                    **dict(harness.context.config),
                    "MANAGER_SERVE_LOG_ACTIVE": True,
                    "MANAGER_SERVE_LOG_LEVEL": "info",
                }
            ),
        )
        context = harness.context
        assert context.config["ADMISSION_MAX_CONNECTIONS"] == 0
        harness.ensure_foreground_manager()
        with (
            _pressure(harness, "native-external-holder"),
            _pressure(harness, None) as unlabeled,
            WeftClient(context=context) as client,
            context.queue("test.family.measurement", persistent=True) as measurement,
            context.queue(FAMILY_EVENTS, persistent=True) as events,
            context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as source,
            closing(QueueChangeMonitor([events, source])) as monitor,
        ):
            native_ceiling = int(get_connection_stats(measurement)["max_connections"])
            assert native_ceiling == 300
            public_limit = native_ceiling - int(native_ceiling * 0.75)
            unlabeled.send(public_limit)
            assert unlabeled.poll(30.0) and unlabeled.recv() == public_limit
            assert get_connection_usage(measurement)["total"] >= public_limit
            task = client.submit(
                {
                    "name": "native-pressure-leaf",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.helpers.family_workload:family_leaf",
                    },
                },
                payload={"index": 1, "measure_usage": True},
            )
            harness.register_tid(task.tid)
            diagnostics: list[dict[str, Any]] = []
            _wait(monitor, lambda: _read_denial(capsys, public_limit, diagnostics))
            denied = next(
                row
                for row in diagnostics
                if row.get("event") == "admission_state"
                and row.get("used", 0) >= public_limit
            )
            assert denied["internal_limit"] == native_ceiling
            assert denied["public_limit"] == public_limit
            assert denied["used"] == denied["observed"]
            assert source.peek_one(exact_timestamp=int(task.tid)) is not None
            assert not _events(harness)
            unlabeled.send("release")
            assert unlabeled.poll(30.0) and unlabeled.recv() == "released"
            result = task.result(timeout=60.0)
            assert result.status == "completed", result.error
            assert result.value == "leaf:1"
            rows = _events(harness)
            assert len(rows) == 1
            assert rows[0]["admission_max_connections"] == 0


def test_extra_dispatch_reads_fresh_pressure_before_reservation(
    monkeypatch: pytest.MonkeyPatch,
    isolated_postgres_server: None,
    tmp_path: Path,
) -> None:
    """The same-turn successor observes real pressure before taking custody."""
    with WeftTestHarness() as harness:
        with harness.context.queue("batch-pressure-ids", persistent=True) as queue:
            manager_tid = str(queue.generate_timestamp())
        manager = Manager(
            harness.context.broker_target,
            make_manager_spec(manager_tid, idle_timeout=0.0),
            config=load_config(
                {
                    "WEFT_ADMISSION_MAX_CONNECTIONS": 80,
                    "WEFT_ADMISSION_RESERVE_FRACTION": 0.1,
                    "WEFT_TASK_MONITOR_ENABLED": False,
                    "WEFT_LIVENESS_MONITOR_ENABLED": False,
                    "WEFT_HEARTBEAT_ENABLED": False,
                    "WEFT_AUTOSTART_TASKS": False,
                }
            ),
        )
        releases = [tmp_path / f"pressure-{index}.release" for index in range(3)]
        try:
            manager._refresh_scheduler_evidence()
            source = manager._queue(manager._queue_names["inbox"])
            internal = manager._queue(manager._queue_names["internal_inbox"])
            internal_ids: list[int] = []
            ids = [
                int(
                    source.write(
                        dispatch_witness_body(
                            tmp_path / f"pressure-{index}.ready", releases[index]
                        )
                    )
                )
                for index in range(2)
            ]
            for request_id in ids:
                harness.register_tid(str(request_id))
            with _pressure(harness, None) as pressure:
                owning_turn = False
                tagged_gate = False
                inside_tagged_gate = False
                pressure_installed = False
                ready_completed = False
                before_pressure: list[int] = []
                installed_pressure: list[int] = []
                tagged_usage: list[int] = []
                tail_usage: list[int] = []
                in_tail = False
                real_convergence = manager._run_managed_service_convergence
                real_internal_drain = manager._drain_internal_spawn_requests
                real_gate = manager._admission_allows_queue
                real_usage = manager._read_postgres_connection_usage

                def read_usage() -> Any:
                    usage = real_usage()
                    if inside_tagged_gate:
                        tagged_usage.append(int(usage["total"]))
                    return usage

                def admission(queue_name: str) -> bool:
                    nonlocal tagged_gate, inside_tagged_gate
                    nonlocal pressure_installed, ready_completed
                    if (
                        owning_turn
                        and queue_name == manager._queue_names["inbox"]
                        and evidence.applied == [str(ids[0])]
                    ):
                        tagged_gate = True
                        if not pressure_installed:
                            # Install pressure independently of the decision's
                            # fresh read, so a cached-read mutant sees it too.
                            wait_for_file(str(tmp_path / "pressure-0.ready"))
                            ready_completed = True
                            before_pressure.append(int(real_usage()["total"]))
                            amount = max(0, 74 - before_pressure[-1])
                            pressure.send(amount)
                            pressure_installed = (
                                pressure.poll(30.0) and pressure.recv() == amount
                            )
                            installed_pressure.append(int(real_usage()["total"]))
                            internal_ids.append(
                                int(
                                    internal.write(
                                        dispatch_witness_body(
                                            tmp_path / "pressure-internal.ready",
                                            releases[2],
                                        )
                                    )
                                )
                            )
                            harness.register_tid(str(internal_ids[0]))
                        inside_tagged_gate = True
                        try:
                            return real_gate(queue_name)
                        finally:
                            inside_tagged_gate = False
                    return real_gate(queue_name)

                def convergence(*args: Any, **kwargs: Any) -> Any:
                    nonlocal in_tail
                    in_tail = (
                        owning_turn and tagged_gate and not kwargs.get("force", False)
                    )
                    try:
                        return real_convergence(*args, **kwargs)
                    finally:
                        in_tail = False

                def internal_drain() -> int:
                    if in_tail:
                        # This read observes headroom; the actual drain still
                        # reaches the original admission owner if the flag opens.
                        tail_usage.append(int(real_usage()["total"]))
                    return real_internal_drain()

                with (
                    settled_launches(manager, monkeypatch, releases) as evidence,
                    monkeypatch.context() as patch,
                ):
                    patch.setattr(
                        manager, "_run_managed_service_convergence", convergence
                    )
                    patch.setattr(
                        manager, "_drain_internal_spawn_requests", internal_drain
                    )
                    patch.setattr(manager, "_admission_allows_queue", admission)
                    patch.setattr(
                        manager, "_read_postgres_connection_usage", read_usage
                    )
                    owning_turn = True
                    try:
                        manager.process_once()
                    finally:
                        owning_turn = False
                    assert not evidence.publication_timeouts
                    assert (
                        evidence.applied[:1] == evidence.submitted[:1] == [str(ids[0])]
                    )
                    assert tagged_gate, (
                        "no extra public admission before owning turn returned"
                    )
                    assert ready_completed, (
                        "actual child readiness barrier did not complete"
                    )
                    assert len(before_pressure) == 1 and before_pressure[
                        0
                    ] < math.floor(72 * MANAGER_STARTUP_FAST_PATH_FRACTION)
                    assert pressure_installed
                    assert (
                        len(installed_pressure) == 1
                        and 72 <= installed_pressure[0] < 80
                    )
                    # Pressure is independently proved before this oracle: stale
                    # admission must fail on actual forbidden custody, not a call count.
                    assert evidence.reserved == [ids[0]]
                    assert internal_ids and tail_usage, (
                        "real tail internal drain did not run"
                    )
                    assert all(72 <= used < 80 for used in tail_usage)
                    assert tagged_usage
                    assert all(72 <= used < 80 for used in tagged_usage)
                    assert evidence.applied == evidence.submitted == [str(ids[0])]
                    assert source.peek_one(exact_timestamp=ids[1]) is not None
                    assert (
                        manager._queue(manager._queue_names["reserved"]).peek_one(
                            exact_timestamp=ids[1]
                        )
                        is None
                    )
                    assert (
                        internal.peek_one(exact_timestamp=internal_ids[0]) is not None
                    )
                    pressure.send("release")
                    assert pressure.poll(30.0) and pressure.recv() == "released"
                    drive_until(
                        lambda: evidence.applied,
                        lambda applied: (
                            applied == [str(ids[0]), str(internal_ids[0]), str(ids[1])]
                        ),
                        step=manager.process_once,
                        wait=manager.wait_for_activity,
                        timeout=30.0,
                    )
                    assert not evidence.publication_timeouts
                    assert evidence.reserved == [ids[0], internal_ids[0], ids[1]]
                    assert evidence.maximum_creators == 1
        finally:
            manager.stop(join=False)
            manager.cleanup()
