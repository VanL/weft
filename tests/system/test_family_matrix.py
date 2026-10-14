"""Real cold-recovery and transparent-pipeline matrix [MA-1.8], [MF-5]."""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Callable
from contextlib import closing
from typing import Any

import psutil
import pytest

from tests.helpers.family_matrix import PostlaunchCheckpointFaultManager
from tests.helpers.family_workload import FAMILY_EVENTS
from tests.helpers.weft_harness import WeftTestHarness
from weft.client import WeftClient
from weft.core.launcher import launch_task_process
from weft.core.manager import Manager
from weft.core.manager_runtime import build_manager_spec, generate_tid
from weft.core.monitor.task_monitor import make_task_monitor_taskspec
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.task_state import read_task_state_snapshot
from weft.core.tasks.liveness_monitor import LivenessMonitor

pytestmark = [
    pytest.mark.shared,
    pytest.mark.timeout(180),
    pytest.mark.skipif(os.name == "nt", reason="real Manager crash requires SIGKILL"),
]


def _events(harness: WeftTestHarness) -> list[dict[str, Any]]:
    with harness.context.queue(FAMILY_EVENTS, persistent=True) as queue:
        return [json.loads(body) for body in queue.peek_generator()]


def _wait(harness: WeftTestHarness, ready: Callable[[], Any]) -> Any:
    with (
        harness.context.queue(FAMILY_EVENTS, persistent=True) as queue,
        closing(QueueChangeMonitor([queue], config=harness.context.config)) as monitor,
    ):
        deadline = time.monotonic() + 60.0
        while True:
            value = ready()
            if value:
                return value
            remaining = deadline - time.monotonic()
            assert remaining > 0, _events(harness)
            monitor.wait(min(remaining, 0.25))


def _retire(harness: WeftTestHarness, tid: str) -> None:
    monitor = LivenessMonitor(
        harness.context.broker_target,
        make_task_monitor_taskspec(generate_tid(harness.context)),
        config=harness.context.config,
        mapping_min_age_seconds=0.0,
    )
    try:
        monitor._reconcile_mapping_rows(full=True)
        row = monitor._latest_rows.get(tid)
        if row is not None:
            assert monitor._retire_mapping_row(row)
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
    assert read_task_state_snapshot(harness.context, tid) is None


def _dead_snapshot_processes(harness: WeftTestHarness, tid: str) -> bool:
    snapshot = read_task_state_snapshot(harness.context, tid)
    assert snapshot is not None
    for entry in snapshot[1]["host_processes"]:
        try:
            process = psutil.Process(entry["pid"])
            if (
                process.create_time() == entry["create_time"]
                and process.status() != psutil.STATUS_ZOMBIE
            ):
                return False
        except psutil.NoSuchProcess:
            pass
    return True


def _start(
    harness: WeftTestHarness, task_class: type[Manager] = Manager
) -> tuple[Any, str]:
    tid = generate_tid(harness.context)
    process = launch_task_process(
        task_class,
        harness.context.broker_target,
        build_manager_spec(harness.context, tid, idle_timeout_override=0.0),
        config=harness.context.config,
    )
    assert process.pid is not None
    harness.register_manager_tid(tid)
    harness.register_pid(process.pid, tid)
    _wait(harness, lambda: read_task_state_snapshot(harness.context, tid))
    return process, tid


def _crash_and_retire(harness: WeftTestHarness, process: Any, tid: str) -> None:
    _wait(harness, lambda: _empty_reservation(harness, tid))
    os.kill(process.pid, signal.SIGKILL)
    process.join(timeout=10.0)
    assert not process.is_alive()
    _retire(harness, tid)


def _empty_reservation(harness: WeftTestHarness, tid: str) -> bool:
    with harness.context.queue(f"T{tid}.reserved", persistent=True) as queue:
        return queue.peek_one(include_claimed=True) is None


def test_more_background_residents_than_window_recover_without_manager_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three live backgrounds survive a cold W1 Manager and let finite work run."""
    monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", "1")
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness() as harness:
        process, manager_tid = _start(harness)
        try:
            with WeftClient(context=harness.context) as client:
                parents = [
                    client.submit(
                        {
                            "name": f"background-{i}",
                            "spec": {
                                "type": "function",
                                "function_target": "tests.helpers.family_workload:family_enrichment",
                                "persistent": True,
                            },
                        },
                        payload={"index": i, "gated": True},
                    )
                    for i in range(3)
                ]
                for parent in parents:
                    harness.register_tid(parent.tid)
                _wait(
                    harness,
                    lambda: (
                        len(
                            [
                                r
                                for r in _events(harness)
                                if r["event"] == "enrichment_waiting"
                            ]
                        )
                        == 3
                    ),
                )
                for parent in parents:
                    snapshot = read_task_state_snapshot(harness.context, parent.tid)
                    assert (
                        snapshot is not None
                        and snapshot[1]["scheduling_class"] == "background"
                    )
                _crash_and_retire(harness, process, manager_tid)
                harness.ensure_foreground_manager()
                finite = client.submit(
                    {
                        "name": "cold-finite",
                        "spec": {
                            "type": "function",
                            "function_target": "tests.helpers.family_workload:family_leaf",
                        },
                    },
                    payload={"index": 50},
                )
                harness.register_tid(finite.tid)
                assert finite.result(timeout=60.0).value == "leaf:50"
                for i, parent in enumerate(parents):
                    with harness.context.queue(
                        f"test.family.release.{i}", persistent=True
                    ) as gate:
                        gate.write("continue")
                    result = parent.result(timeout=60.0)
                    assert (
                        result.status == "completed"
                        and isinstance(result.value, dict)
                        and result.value["value"] == f"leaf:{i}"
                    )
                    harness.register_tid(result.value["child_tid"])
        finally:
            if process.is_alive():
                process.kill()
            process.join(timeout=10.0)


def test_cold_chain_contracts_removed_intermediate_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R/A remain live when B exits; C's full ancestry reconstructs the W1 branch."""
    monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", "1")
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness() as harness:
        process, manager_tid = _start(harness)
        try:
            with WeftClient(context=harness.context) as client:
                root = client.submit(
                    {
                        "name": "chain-root",
                        "spec": {
                            "type": "function",
                            "function_target": "tests.helpers.family_matrix:chain_node",
                        },
                    },
                    payload={"depth": 0},
                )
                harness.register_tid(root.tid)
                rows = _wait(
                    harness,
                    lambda: (
                        _events(harness)
                        if any(r["event"] == "chain_waiting" for r in _events(harness))
                        else None
                    ),
                )
                nodes = {
                    r["index"]: r["tid"] for r in rows if r["event"] == "chain_started"
                }
                for tid in nodes.values():
                    harness.register_tid(tid)
                _wait(
                    harness,
                    lambda: (
                        (
                            snapshot := read_task_state_snapshot(
                                harness.context, nodes[2]
                            )
                        )
                        and snapshot[1].get("terminal") is True
                    ),
                )
                _wait(harness, lambda: _dead_snapshot_processes(harness, nodes[2]))
                _retire(harness, nodes[2])
                _crash_and_retire(harness, process, manager_tid)
                harness.ensure_foreground_manager()
                with harness.context.queue(
                    "test.chain.release", persistent=True
                ) as gate:
                    gate.write("continue")
                result = root.result(timeout=60.0)
                assert result.status == "completed", result.error
                assert result.value == "leaf:99"
                rows = _events(harness)
                leaf = next(r for r in rows if r["event"] == "leaf_started")
                harness.register_tid(leaf["tid"])
                assert leaf["ancestry_tids"] == [nodes[i] for i in range(4)]
                assert leaf["root_tid"] == root.tid
        finally:
            if process.is_alive():
                process.kill()
            process.join(timeout=10.0)


def test_public_pipelines_fill_window_and_internal_stages_descend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two occupied public branches permit transparent stages and ordinary leaves."""
    monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", "2")
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness() as harness:
        tasks = harness.context.weft_dir / "tasks"
        tasks.mkdir(parents=True, exist_ok=True)
        (tasks / "nested-stage.json").write_text(
            json.dumps(
                {
                    "name": "nested-stage",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.helpers.family_workload:family_enrichment",
                    },
                }
            )
        )
        pipeline = harness.root / "pipeline.json"
        pipeline.write_text(
            json.dumps(
                {
                    "name": "nested-pipeline",
                    "stages": [{"name": "enrich", "task": "nested-stage"}],
                }
            )
        )
        harness.ensure_foreground_manager()
        with WeftClient(context=harness.context) as client:
            parents = [
                client.submit_pipeline(pipeline, payload={"index": i, "gated": True})
                for i in range(2)
            ]
            for parent in parents:
                harness.register_tid(parent.tid)
            rows = _wait(
                harness,
                lambda: (
                    _events(harness)
                    if len(
                        [
                            r
                            for r in _events(harness)
                            if r["event"] == "enrichment_waiting"
                        ]
                    )
                    == 2
                    else None
                ),
            )
            stages = [r for r in rows if r["event"] == "enrichment_waiting"]
            assert {r["root_tid"] for r in stages} == {p.tid for p in parents}
            for i in range(2):
                with harness.context.queue(
                    f"test.family.release.{i}", persistent=True
                ) as gate:
                    gate.write("continue")
            for parent in parents:
                result = parent.result(timeout=60.0)
                assert result.status == "completed", result.error
            for row in _events(harness):
                harness.register_tid(row["tid"])
            leaves = [r for r in _events(harness) if r["event"] == "leaf_started"]
            assert len(leaves) == 2
            assert {tuple(r["ancestry_tids"]) for r in leaves} == {
                (p.tid,) for p in parents
            }


def test_w1_sibling_rendezvous_is_stalled_outside_descendant_progress_assumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer dependency is a stalled state, never reported as successful progress.

    Neither peer is released or cancelled to obtain a completion assertion.
    Harness teardown alone stops the still-live fixture and cleans its queues.
    """
    monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", "1")
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        with WeftClient(context=harness.context) as client:
            root = client.submit(
                {
                    "name": "rendezvous-root",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.helpers.family_matrix:sibling_rendezvous_root",
                    },
                },
                payload={"rendezvous": True},
            )
            harness.register_tid(root.tid)
            result = root.result(timeout=60.0)
            assert result.status == "completed", result.error
            assert isinstance(result.value, list)
            peers = result.value
            assert len(peers) == 2
            for tid in peers:
                harness.register_tid(tid)
            rows = _wait(
                harness,
                lambda: (
                    _events(harness)
                    if any(r["event"] == "rendezvous_waiting" for r in _events(harness))
                    else None
                ),
            )
            waiter = next(r for r in rows if r["event"] == "rendezvous_waiting")
            blocked = next(tid for tid in peers if tid != waiter["tid"])
            with harness.context.queue(
                "weft.spawn.requests", persistent=True
            ) as source:
                assert source.peek_one(exact_timestamp=int(blocked)) is not None
                assert source.peek_one(exact_timestamp=int(waiter["tid"])) is None
            snapshot = read_task_state_snapshot(harness.context, waiter["tid"])
            assert snapshot is not None and snapshot[1]["terminal"] is False
            with harness.context.queue(f"T{blocked}.outbox", persistent=True) as outbox:
                assert outbox.peek_one() is None
            with harness.context.queue(
                f"test.sibling.ready.{1 - waiter['index']}", persistent=True
            ) as readiness:
                assert readiness.peek_one() is None


def test_postspawn_no_pid_checkpoint_preserves_unknown_frontier_and_stalls_new_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live orphan lacking durable process identity cannot release its W1 place.

    Fault only the publication boundary after actual process creation. The
    successor must retain uncertainty; teardown alone stops fixture processes.
    No release, cancellation, or timeout obtains a claimed successful drain.
    """
    monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", "1")
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness() as harness:
        process, old_tid = _start(harness, PostlaunchCheckpointFaultManager)
        try:
            with WeftClient(context=harness.context) as client:
                parent = client.submit(
                    {
                        "name": "family-recovery-parent",
                        "spec": {
                            "type": "function",
                            "function_target": "tests.helpers.family_workload:family_enrichment",
                        },
                    },
                    payload={"index": 0, "gated": True},
                )
                harness.register_tid(parent.tid)
                _wait(
                    harness,
                    lambda: any(
                        row["event"] == "postlaunch_checkpoint_paused"
                        for row in _events(harness)
                    ),
                )
                _wait(
                    harness,
                    lambda: any(
                        row["event"] == "enrichment_waiting" for row in _events(harness)
                    ),
                )
                old = read_task_state_snapshot(harness.context, old_tid)
                assert old is not None
                evidence = old[1]["scheduler_frontiers"]["residents"][parent.tid]
                assert not evidence.get("host_processes")
                assert read_task_state_snapshot(harness.context, parent.tid) is None
                descendants = psutil.Process(process.pid).children(recursive=True)
                assert descendants
                for child in descendants:
                    harness.register_pid(
                        child.pid,
                        parent.tid,
                        kind="managed",
                        create_time=child.create_time(),
                    )
                os.kill(process.pid, signal.SIGKILL)
                process.join(timeout=10.0)
                assert not process.is_alive()
                successor = harness.ensure_foreground_manager()
                successor_tid = successor["tid"]
                assert isinstance(successor_tid, str)

                def retained() -> Any:
                    row = read_task_state_snapshot(harness.context, successor_tid)
                    if row and parent.tid in row[1].get("scheduler_frontiers", {}).get(
                        "residents", {}
                    ):
                        return row[1]
                    return None

                recovered = _wait(harness, retained)
                assert recovered["scheduler_frontiers"]["stacks"] == [[parent.tid]]
                assert not recovered["scheduler_frontiers"]["residents"][
                    parent.tid
                ].get("host_processes")
                independent = client.submit(
                    {
                        "name": "must-remain-pending",
                        "spec": {
                            "type": "function",
                            "function_target": "tests.helpers.family_workload:family_leaf",
                        },
                    },
                    payload={"index": 100},
                )
                harness.register_tid(independent.tid)
                with harness.context.queue(
                    "weft.spawn.requests", persistent=True
                ) as source:
                    assert (
                        source.peek_one(exact_timestamp=int(independent.tid))
                        is not None
                    )
                    assert source.peek_one(exact_timestamp=int(parent.tid)) is None
                with harness.context.queue(
                    f"T{old_tid}.reserved", persistent=True
                ) as reserved:
                    assert (
                        reserved.peek_one(
                            exact_timestamp=int(parent.tid), include_claimed=True
                        )
                        is not None
                    )
                assert (
                    sum(
                        row["event"] == "enrichment_started" for row in _events(harness)
                    )
                    == 1
                )
                assert not any(
                    row["event"] == "leaf_started" for row in _events(harness)
                )
        finally:
            if process.is_alive():
                process.kill()
            process.join(timeout=10.0)
