"""Real PostgreSQL connection obligations and leaf-child progress.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1.8]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5], [MF-6]
- docs/specifications/07-System_Invariants.md [MANAGER.18], [OBS.6]
"""

from __future__ import annotations

import json
import multiprocessing
import time
from collections.abc import Callable
from contextlib import closing
from multiprocessing.connection import Connection
from typing import Any

import psutil
import pytest

from simplebroker import BrokerSession, BrokerTarget, Queue, serialize_config
from tests.helpers.test_backend import active_test_backend
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, resolve_runtime_config
from weft.client import WeftClient, connect
from weft.context import build_context
from weft.core.admission import postgres_accounted_usage
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.task_state import read_task_state_snapshot
from weft.helpers import iter_queue_json_entries

pytestmark = [
    pytest.mark.shared,
    pytest.mark.skipif(
        active_test_backend() != "postgres", reason="requires bin/pytest-pg"
    ),
]


def _wait_for_condition(
    monitor: QueueChangeMonitor,
    condition: Callable[[], bool],
    *,
    timeout: float = 60.0,
) -> None:
    """Check durable conditions after native activity, with a bounded failure."""
    deadline = time.monotonic() + timeout
    while not condition():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("admission test did not reach its queue barrier")
        # A bounded recheck also observes local Manager launch/denial progress;
        # no fixed interval is used as proof of either presence or absence.
        monitor.wait(min(remaining, 0.1))


def _read_barrier(queue: Queue, *, timeout: float = 60.0) -> str:
    """Await one actual broker message, retaining its Queue's owner thread."""
    with closing(QueueChangeMonitor([queue])) as monitor:
        result: list[str] = []

        def received() -> bool:
            message = queue.read_one()
            if message is not None:
                result.append(message)
            return bool(result)

        _wait_for_condition(monitor, received, timeout=timeout)
        return result[0]


def pg_admission_leaf(index: int) -> str:
    """Stand in for external LLM work, with a real task lifecycle and result."""
    with connect() as client, client.context.session() as session:
        ready = session.queue("test.admission.leaves.ready")
        release = session.queue(f"test.admission.leaf.release.{index}")
        try:
            ready.write(str(index))
            _read_barrier(release)
            return f"leaf-{index}"
        finally:
            release.close()
            ready.close()


def pg_admission_parent(index: int) -> dict[str, Any]:
    """Retain a normal client across same-context submit and result wait."""
    with connect() as client, client.context.session() as session:
        ready = session.queue("test.admission.parents.ready")
        accepted = session.queue("test.admission.children.accepted")
        release = session.queue(f"test.admission.parent.release.{index}")
        try:
            ready.write(str(index))
            _read_barrier(release)
            child = client.submit(
                {
                    "name": f"budget-leaf-{index}",
                    "spec": {
                        "type": "function",
                        "function_target": (
                            "tests.system.test_admission_budget_pg:pg_admission_leaf"
                        ),
                        "args": [index],
                        "timeout": 60.0,
                    },
                }
            )
            accepted.write(json.dumps({"index": index, "tid": child.tid}))
            result = child.result(timeout=60.0)
            if result.status != "completed":
                raise RuntimeError(f"leaf failed: {result}")
            return {"child_tid": child.tid, "value": result.value}
        finally:
            release.close()
            accepted.close()
            ready.close()


def _connection_lifetime_probe(
    target: BrokerTarget,
    config: str,
    owner: str,
    pipe: Connection,
) -> None:
    """A dedicated real process can bind before any PG resource opens."""
    # This optional backend is required only in the PG-selected test process.
    from simplebroker_pg import bind_connection_owner

    bind_connection_owner(owner)
    pipe.send("bound")
    session: BrokerSession | None = None
    queue: Queue | None = None
    monitor: QueueChangeMonitor | None = None
    try:
        while True:
            command = pipe.recv()
            if command == "open":
                session = BrokerSession.connect(
                    target, config=resolve_runtime_config(config)
                )
                queue = session.queue("test.admission.connection.probe")
                queue.write("opened")
                monitor = QueueChangeMonitor([queue])
                pipe.send("open")
            elif command == "close":
                assert session is not None and queue is not None and monitor is not None
                monitor.close()
                queue.close()
                session.close()
                monitor = None
                queue = None
                session = None
                pipe.send("closed")
            elif command == "exit":
                return
            else:
                raise ValueError(f"unexpected probe command {command!r}")
    finally:
        if monitor is not None:
            monitor.close()
        if queue is not None:
            queue.close()
        if session is not None:
            session.close()
        pipe.close()


def test_real_pg_connections_replace_then_restore_prospective_budget(
    weft_harness: WeftTestHarness,
) -> None:
    """Binding, real pool/listener reuse, and accounting agree across reopening.

    A raw-only usage guard loses the unopened cost, while a double-charge guard
    changes accounted task cost as the process materializes its connections.
    No Manager, broker, session, or observation is substituted.
    """
    # Keep the backend optional for default SQLite installations.
    from simplebroker_pg import get_connection_usage

    owner = "weft-budget-probe-1777000000000000999"
    context = weft_harness.context
    ipc = multiprocessing.get_context("spawn")
    parent_pipe, child_pipe = ipc.Pipe()
    process = ipc.Process(
        target=_connection_lifetime_probe,
        args=(
            context.broker_target,
            serialize_config(context.broker_config),
            owner,
            child_pipe,
        ),
    )
    process.start()
    child_pipe.close()
    assert process.pid is not None
    weft_harness.register_pid(process.pid)
    try:
        assert parent_pipe.poll(30.0), "probe did not bind"
        assert parent_pipe.recv() == "bound"
        with context.session() as session:
            measurement = session.queue("test.admission.measurement")
            try:
                observations = []
                for command, expected in (
                    (None, None),
                    ("open", "open"),
                    ("close", "closed"),
                    ("open", "open"),
                ):
                    if command is not None:
                        parent_pipe.send(command)
                        assert parent_pipe.poll(30.0), f"probe stalled at {command}"
                        assert parent_pipe.recv() == expected
                    with closing(QueueChangeMonitor([measurement])) as activity:
                        usage: dict[str, Any] = {}

                        def resource_state_visible(
                            materialized: bool = command == "open",
                        ) -> bool:
                            nonlocal usage
                            usage = dict(get_connection_usage(measurement))
                            count = int(usage["by_owner"].get(owner, 0))
                            return (count > 0) == materialized

                        _wait_for_condition(activity, resource_state_visible)
                    credited = usage["by_owner"].get(owner, 0)
                    observations.append((usage, credited))
                    accounted = postgres_accounted_usage(
                        usage["total"], usage["by_owner"], [(owner, 8)]
                    )
                    # Remove independently observed outside pressure. The task's
                    # accounted contribution stays eight while it can reopen.
                    assert accounted - (usage["total"] - credited) == 8
                assert observations[0][1] == observations[2][1] == 0
                assert 0 < observations[1][1] <= 8
                assert 0 < observations[3][1] <= 8
            finally:
                measurement.close()
    finally:
        if process.is_alive():
            parent_pipe.send("exit")
        process.join(timeout=10.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)
        parent_pipe.close()
        assert not process.is_alive(), "connection probe survived cleanup"
        exitcode = process.exitcode
        process.close()
    assert exitcode == 0


@pytest.mark.timeout(180)
def test_pg_saturated_roots_complete_waiting_parents_through_child_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retained root FIFO cannot trap real parents behind their own backlog.

    Queue barriers establish saturation before any parent submits a child, then
    establish a running leaf before releasing its real result. The number of
    admitted parents may vary with outside server pressure; the guarantee is
    intentionally conditional on a leaf still fitting the protected headroom.
    """
    # Keep the backend optional for default SQLite installations.
    from simplebroker_pg import get_connection_usage

    monkeypatch.setenv("WEFT_ADMISSION_RESERVE_FRACTION", "0")
    monkeypatch.setenv("WEFT_ADMISSION_TASK_CONNECTION_BUDGET", "8")
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness(manager_timeout=120.0) as harness:
        context = harness.context
        with context.session() as session:
            measurement = session.queue("test.admission.measurement")
            try:
                outside = get_connection_usage(measurement)["total"]
            finally:
                measurement.close()
        # This setting is captured by context construction, so rebuild the
        # context before starting its Manager without changing its broker target.
        monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", str(outside + 44))
        configured = build_context(spec_context=harness.root)
        harness._context = configured
        harness.ensure_foreground_manager()
        manager = harness._inline_managers[0][0]
        with WeftClient(context=configured) as client, configured.session() as session:
            ready = session.queue("test.admission.parents.ready")
            leaf_ready = session.queue("test.admission.leaves.ready")
            accepted = session.queue("test.admission.children.accepted")
            root_queue = session.queue("weft.spawn.requests")
            log = session.queue(WEFT_GLOBAL_LOG_QUEUE)
            queues = [ready, leaf_ready, accepted, root_queue, log]
            try:
                parents = [
                    client.submit(
                        {
                            "name": f"budget-parent-{index}",
                            "spec": {
                                "type": "function",
                                "function_target": (
                                    "tests.system.test_admission_budget_pg:"
                                    "pg_admission_parent"
                                ),
                                "args": [index],
                                "timeout": 120.0,
                            },
                        }
                    )
                    for index in range(8)
                ]
                for parent in parents:
                    harness.register_tid(parent.tid)
                with closing(QueueChangeMonitor(queues)) as monitor:
                    _wait_for_condition(
                        monitor,
                        lambda: (
                            "public" in manager._admission_blocked_lanes
                            and root_queue.peek_one() is not None
                            and ready.peek_one() is not None
                        ),
                    )
                    # Reaching the denied lane proves the Manager considered
                    # backlog; the negative queue assertion is not a timed guess.
                    assert root_queue.peek_one() is not None
                    for index in range(8):
                        release = session.queue(
                            f"test.admission.parent.release.{index}"
                        )
                        try:
                            release.write("submit child")
                        finally:
                            release.close()
                    _wait_for_condition(
                        monitor, lambda: leaf_ready.peek_one() is not None
                    )
                    running_leaf_index = int(str(leaf_ready.peek_one()))
                    _wait_for_condition(
                        monitor,
                        lambda: any(
                            json.loads(body)["index"] == running_leaf_index
                            for body in accepted.peek_many()
                        ),
                    )
                    child_rows = [json.loads(body) for body in accepted.peek_many()]
                    assert child_rows
                    for child in child_rows:
                        harness.register_tid(child["tid"])
                    assert root_queue.peek_one() is not None
                    child = next(
                        row for row in child_rows if row["index"] == running_leaf_index
                    )
                    parent_tid = parents[child["index"]].tid
                    state = read_task_state_snapshot(configured, child["tid"])
                    assert state is not None
                    record = state[1]["admission"]
                    assert record["connection_budget"] == 8
                    assert record["submitter_tid"] == parent_tid
                    assert 1 <= len(record["connection_owner"]) <= 54
                    _wait_for_condition(
                        monitor,
                        lambda: any(
                            event.get("event") == "task_spawned"
                            and event.get("child_tid") == child["tid"]
                            for event, _ in iter_queue_json_entries(log)
                        ),
                    )
                    events = [data for data, _ in iter_queue_json_entries(log)]
                    launch = next(
                        event
                        for event in events
                        if event.get("event") == "task_spawned"
                        and event.get("child_tid") == child["tid"]
                    )
                    assert launch["admission"] == record
                    for index in range(8):
                        release = session.queue(f"test.admission.leaf.release.{index}")
                        try:
                            release.write("return result")
                        finally:
                            release.close()
                for index, parent in enumerate(parents):
                    result = parent.result(timeout=90.0)
                    assert result.status == "completed", result
                    assert isinstance(result.value, dict)
                    assert result.value["value"] == f"leaf-{index}"
            finally:
                for queue in reversed(queues):
                    queue.close()


@pytest.mark.timeout(90)
def test_pg_disabled_gate_retires_finished_launch_obligations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recording budgets without a gate must not retain dead launches forever.

    An enabled gate regularly observes usage. The disabled-gate path has no
    observations, so only the normal child-reap owner can retire these records.
    This prevents the live Manager snapshot from growing with completed history.
    """
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    monkeypatch.setenv("WEFT_ADMISSION_TASK_CONNECTION_BUDGET", "8")
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        manager = harness._inline_managers[0][0]
        with WeftClient(context=harness.context) as client:
            tasks = [
                client.submit(
                    {
                        "name": f"ungated-budget-{index}",
                        "spec": {
                            "type": "function",
                            "function_target": "tests.tasks.sample_targets:echo_payload",
                        },
                    },
                    payload=f"result-{index}",
                )
                for index in range(3)
            ]
            for index, task in enumerate(tasks):
                harness.register_tid(task.tid)
                result = task.result(timeout=60.0)
                assert result.status == "completed", result
                assert result.value == f"result-{index}"
                snapshot = read_task_state_snapshot(harness.context, task.tid)
                assert snapshot is not None
                assert snapshot[1]["admission"]["connection_budget"] == 8
            with harness.context.session() as session:
                log = session.queue(WEFT_GLOBAL_LOG_QUEUE)
                try:

                    def retired_from_published_state() -> bool:
                        snapshot = read_task_state_snapshot(
                            harness.context, manager.tid
                        )
                        if snapshot is None:
                            return False
                        retained = snapshot[1]["admission_launches"]
                        return not any(task.tid in retained for task in tasks)

                    with closing(QueueChangeMonitor([log])) as monitor:
                        _wait_for_condition(monitor, retired_from_published_state)
                    assert not any(
                        task.tid in manager._admission_launches for task in tasks
                    )
                finally:
                    log.close()


@pytest.mark.timeout(90)
@pytest.mark.parametrize("capacity", [0, 300])
def test_pg_crash_without_terminal_snapshot_recovers_capacity(
    monkeypatch: pytest.MonkeyPatch, capacity: int
) -> None:
    """A real abruptly killed wrapper/worker cannot leave permanent budget debt.

    The task has no opportunity to publish its terminal state. Its original
    nonterminal snapshot remains, while the Manager publishes retired debt
    and continues executing work, including when the admission gate is off.
    """
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", str(capacity))
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        manager = harness._inline_managers[0][0]
        with (
            WeftClient(context=harness.context) as client,
            harness.context.session() as session,
        ):
            task = client.submit(
                {
                    "name": "crash-admission-leaf",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.system.test_admission_budget_pg:pg_admission_leaf",
                        "args": [999],
                        "timeout": 60.0,
                    },
                }
            )
            harness.register_tid(task.tid)
            ready = session.queue("test.admission.leaves.ready")
            log = session.queue(WEFT_GLOBAL_LOG_QUEUE)
            try:
                assert _read_barrier(ready) == "999"
                with closing(QueueChangeMonitor([log])) as activity:
                    _wait_for_condition(
                        activity, lambda: task.tid in manager._child_processes
                    )
                    snapshot = read_task_state_snapshot(harness.context, task.tid)
                    assert snapshot is not None and snapshot[1]["terminal"] is False
                    wrapper = manager._child_processes[task.tid].process
                    assert wrapper.pid is not None
                    processes = {
                        item["pid"]: psutil.Process(item["pid"])
                        for item in snapshot[1]["host_processes"]
                    }
                    assert wrapper.pid in processes and len(processes) >= 2
                    # Kill the wrapper first so it cannot publish terminal proof
                    # in response to its worker exiting. Every target is owned
                    # by this isolated test task and registered with the harness.
                    processes.pop(wrapper.pid).kill()
                    for process in processes.values():
                        try:
                            process.kill()
                        except psutil.NoSuchProcess:
                            pass

                    def budget_retired() -> bool:
                        state = read_task_state_snapshot(harness.context, manager.tid)
                        return (
                            state is not None
                            and task.tid not in state[1]["admission_launches"]
                        )

                    _wait_for_condition(activity, budget_retired)
                    state = read_task_state_snapshot(harness.context, task.tid)
                    assert state is not None and state[1]["terminal"] is False
                followup = client.submit(
                    {
                        "name": "capacity-recovered",
                        "spec": {
                            "type": "function",
                            "function_target": "tests.tasks.sample_targets:echo_payload",
                        },
                    },
                    payload="capacity-recovered",
                )
                harness.register_tid(followup.tid)
                result = followup.result(timeout=30.0)
                assert result.status == "completed", result
                assert result.value == "capacity-recovered"
            finally:
                log.close()
                ready.close()
