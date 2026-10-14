"""Combined missing-snapshot, Manager crash, cleanup and cold recovery [MF-5]."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack, closing
from typing import Any

import psutil
import pytest

from tests.helpers.family_recovery import (
    PrecheckpointFaultManager,
    SnapshotFaultManager,
)
from tests.helpers.family_workload import FAMILY_EVENTS
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.client import WeftClient
from weft.context import WeftContext
from weft.core.launcher import launch_task_process
from weft.core.manager_runtime import build_manager_spec, generate_tid
from weft.core.monitor.collation import update_from_task_log_payload
from weft.core.monitor.task_monitor import (
    TaskMonitor,
    _maintenance_worker_scope,
    make_task_monitor_taskspec,
)
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.task_state import read_task_state_snapshot, task_state_queue_name
from weft.core.tasks.liveness_monitor import LivenessMonitor

pytestmark = [pytest.mark.shared, pytest.mark.timeout(180)]


def _wait(
    harness: WeftTestHarness, queue_names: list[str], ready: Callable[[], Any]
) -> Any:
    with ExitStack() as stack:
        queues = [
            stack.enter_context(harness.context.queue(name, persistent=True))
            for name in queue_names
        ]
        monitor = stack.enter_context(
            closing(QueueChangeMonitor(queues, config=harness.context.config))
        )
        deadline = time.monotonic() + 60.0
        while True:
            value = ready()
            if value:
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"recovery did not progress: {queue_names}")
            monitor.wait(remaining)


def _resident(
    context: WeftContext, manager_tid: str, child_tid: str
) -> dict[str, Any] | None:
    snapshot = read_task_state_snapshot(context, manager_tid)
    if snapshot is None:
        return None
    evidence = (
        snapshot[1].get("scheduler_frontiers", {}).get("residents", {}).get(child_tid)
    )
    if isinstance(evidence, dict) and evidence.get("host_processes"):
        return evidence
    return None


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL crash boundary requires POSIX")
@pytest.mark.parametrize("background", [False, True])
def test_missing_snapshot_crash_preserves_custody_and_descendant_progress(
    monkeypatch: pytest.MonkeyPatch, background: bool
) -> None:
    """A real orphan survives eligible cleanup and cold admission without replay."""
    monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", "1")
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness() as harness:
        context = harness.context
        manager_tid = generate_tid(context)
        spec = build_manager_spec(context, manager_tid, idle_timeout_override=0.0)
        process = launch_task_process(
            SnapshotFaultManager, context.broker_target, spec, config=context.config
        )
        assert process.pid is not None
        harness.register_manager_tid(manager_tid)
        harness.register_pid(process.pid, manager_tid)
        try:
            _wait(
                harness,
                [task_state_queue_name(manager_tid)],
                lambda: read_task_state_snapshot(context, manager_tid),
            )
            with WeftClient(context=context) as client:
                parent = client.submit(
                    {
                        "name": "family-recovery-parent",
                        "spec": {
                            "type": "function",
                            "persistent": background,
                            "function_target": "tests.helpers.family_workload:family_enrichment",
                        },
                    },
                    payload={"index": 0, "gated": True},
                )
                harness.register_tid(parent.tid)
                evidence = _wait(
                    harness,
                    [task_state_queue_name(manager_tid), FAMILY_EVENTS],
                    lambda: _resident(context, manager_tid, parent.tid),
                )
                for host in evidence["host_processes"]:
                    pid = int(host["pid"])
                    harness.register_pid(
                        pid, parent.tid, create_time=float(host["create_time"])
                    )
                    for child in psutil.Process(pid).children(recursive=True):
                        harness.register_pid(
                            child.pid,
                            parent.tid,
                            kind="managed",
                            create_time=child.create_time(),
                        )
                _wait(
                    harness,
                    [task_state_queue_name(manager_tid)],
                    lambda: _startup_gate_cleared(context, manager_tid, parent.tid),
                )
                assert read_task_state_snapshot(context, parent.tid) is None
                with context.queue(
                    f"T{manager_tid}.reserved", persistent=True
                ) as reserved:
                    assert (
                        reserved.peek_one(
                            exact_timestamp=int(parent.tid), include_claimed=True
                        )
                        is not None
                    )
                os.kill(process.pid, signal.SIGKILL)
                process.join(timeout=10.0)
                assert not process.is_alive()
                # Exercise the real retirement owner with otherwise eligible age.
                liveness_spec = make_task_monitor_taskspec(generate_tid(context))
                liveness = LivenessMonitor(
                    context.broker_target,
                    liveness_spec,
                    config=context.config,
                    mapping_min_age_seconds=0.0,
                )
                try:
                    liveness._reconcile_mapping_rows(full=True)
                    row = liveness._latest_rows[manager_tid]
                    assert liveness._retire_mapping_row(row) is False
                finally:
                    liveness.stop(join=False)
                    liveness.cleanup()
                assert _resident(context, manager_tid, parent.tid) is not None
                monitor_config = dict(context.config)
                monitor_config["TASK_MONITOR_MODE"] = "jsonl_then_delete"
                monitor_config["LOG_TASKS_EXTERNAL_ENABLED"] = True
                task_monitor = TaskMonitor(
                    context.broker_target,
                    make_task_monitor_taskspec(generate_tid(context)),
                    config=monitor_config,
                )
                close_errors: list[str] = []
                try:
                    with _maintenance_worker_scope(
                        task_monitor._capture_maintenance_inputs(),
                        close_errors,
                        borrowed_session=task_monitor._broker_session,
                    ) as worker:
                        store = worker._ensure_monitor_store()
                        assert store is not None
                        update = update_from_task_log_payload(
                            {
                                "event": "task_killed",
                                "status": "killed",
                                "tid": manager_tid,
                                "taskspec": spec.model_dump(mode="json"),
                            },
                            message_id=int(manager_tid),
                        )
                        assert update is not None
                        store.record_task_log_updates(
                            WEFT_GLOBAL_LOG_QUEUE, (update,), checkpoint_message_id=None
                        )
                        store.mark_summary_emitted(manager_tid, int(manager_tid) + 1)
                        record = store.get_task(manager_tid)
                        assert record is not None
                        # Selected by terminal/archive policy; force retention age,
                        # not a fake liveness verdict or queue deletion implementation.
                        age_ns = time.time_ns() + 86400 * 10**9
                        names = {
                            f"T{manager_tid}.reserved",
                            f"T{manager_tid}.ctrl_in",
                            f"T{manager_tid}.ctrl_out",
                        }
                        blocked = worker._delete_runtime_reserved_queue(
                            f"T{manager_tid}.reserved"
                        )
                        assert blocked.pending
                        archived = worker._delete_dead_task_control_queues(
                            manager_tid,
                            store=store,
                            existing_queue_names=names,
                            active_tids=set(),
                            now_ns=age_ns,
                        )
                        assert archived.pending
                        retained = store.get_task(manager_tid)
                        assert retained is not None
                        assert retained.reserved_cleanup_checked_at_ns is None
                    assert close_errors == []
                finally:
                    task_monitor.stop()
                successor = harness.ensure_foreground_manager(timeout=30.0)
                successor_tid = str(successor["tid"])
                adopted = _wait(
                    harness,
                    [task_state_queue_name(successor_tid)],
                    lambda: _resident(context, successor_tid, parent.tid),
                )
                assert adopted["root_tid"] == parent.tid
                assert adopted["scheduling_class"] == (
                    "background" if background else "finite"
                )
                (context.root / ".weft" / "allow-family-snapshot").touch()
                with context.queue("test.family.release.0", persistent=True) as gate:
                    gate.write("descend-after-cold-recovery")
                rows = _wait(
                    harness, [FAMILY_EVENTS], lambda: _completed_events(harness)
                )
                leaves = [r for r in rows if r["event"] == "leaf_started"]
                assert len(leaves) == 1
                assert leaves[0]["root_tid"] == parent.tid
                assert sum(r["event"] == "enrichment_started" for r in rows) == 1
                harness.register_tid(leaves[0]["tid"])
                if background:
                    parent.stop()
                else:
                    assert parent.result(timeout=60.0).status == "completed"
                _wait(
                    harness,
                    [
                        task_state_queue_name(parent.tid),
                        task_state_queue_name(successor_tid),
                    ],
                    lambda: _reservation_absent(context, manager_tid, parent.tid),
                )
                final_liveness = LivenessMonitor(
                    context.broker_target,
                    make_task_monitor_taskspec(generate_tid(context)),
                    config=context.config,
                    mapping_min_age_seconds=0.0,
                )
                try:
                    final_liveness._reconcile_mapping_rows(full=True)
                    old_row = final_liveness._latest_rows.get(manager_tid)
                    if old_row is not None:
                        assert final_liveness._retire_mapping_row(old_row)
                    assert read_task_state_snapshot(context, manager_tid) is None
                finally:
                    final_liveness.stop(join=False)
                    final_liveness.cleanup()
        finally:
            if process.is_alive():
                process.kill()
                process.join(timeout=10.0)
            process.close()


def _completed_events(harness: WeftTestHarness) -> list[dict[str, Any]] | None:
    with harness.context.queue(FAMILY_EVENTS, persistent=True) as queue:
        rows = [json.loads(body) for body in queue.peek_generator()]
    return rows if any(row["event"] == "enrichment_completed" for row in rows) else None


def _reservation_absent(context: WeftContext, manager_tid: str, child_tid: str) -> bool:
    with context.queue(f"T{manager_tid}.reserved", persistent=True) as queue:
        return (
            queue.peek_one(exact_timestamp=int(child_tid), include_claimed=True) is None
        )


def _startup_gate_cleared(
    context: WeftContext, manager_tid: str, child_tid: str
) -> bool:
    snapshot = read_task_state_snapshot(context, manager_tid)
    if snapshot is None:
        return False
    checkpoint = snapshot[1].get("scheduler_frontiers", {})
    return (
        "startup_feedback_tid" in checkpoint
        and checkpoint["startup_feedback_tid"] is None
        and bool(
            checkpoint.get("residents", {}).get(child_tid, {}).get("host_processes")
        )
    )


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL crash boundary requires POSIX")
def test_reservation_before_checkpoint_crash_requeues_exact_request_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual reservation survives an interrupted mandatory publication fence."""
    monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", "1")
    monkeypatch.setenv("WEFT_ADMISSION_MAX_CONNECTIONS", "0")
    with WeftTestHarness() as harness:
        context = harness.context
        manager_tid = generate_tid(context)
        spec = build_manager_spec(context, manager_tid, idle_timeout_override=0.0)
        process = launch_task_process(
            PrecheckpointFaultManager,
            context.broker_target,
            spec,
            config=context.config,
        )
        assert process.pid is not None
        harness.register_manager_tid(manager_tid)
        harness.register_pid(process.pid, manager_tid)
        try:
            _wait(
                harness,
                [task_state_queue_name(manager_tid)],
                lambda: read_task_state_snapshot(context, manager_tid),
            )
            executions = context.root / ".weft" / "recovery-executions.txt"
            with WeftClient(context=context) as client:
                task = client.submit(
                    {
                        "name": "accepted-before-checkpoint",
                        "spec": {
                            "type": "command",
                            "process_target": sys.executable,
                            "args": [
                                "-c",
                                (
                                    "from pathlib import Path\nimport sys\n"
                                    "with Path(sys.argv[1]).open('a') as f:\n"
                                    "    f.write('once\\n')\nprint('accepted once')"
                                ),
                                str(executions),
                            ],
                        },
                    }
                )
                harness.register_tid(task.tid)
                # Reservation is observable broker activity. The fixture's file
                # proves the actual prelaunch publication has reached its fault.
                marker = context.root / ".weft" / "reserved-before-checkpoint"
                _wait(
                    harness,
                    [f"T{manager_tid}.reserved", FAMILY_EVENTS],
                    lambda: marker.exists() and marker.read_text() == task.tid,
                )
                snapshot = read_task_state_snapshot(context, manager_tid)
                assert snapshot is not None
                assert task.tid not in snapshot[1]["scheduler_frontiers"]["residents"]
                with context.queue(
                    f"T{manager_tid}.reserved", persistent=True
                ) as reserved:
                    accepted = reserved.peek_one(
                        exact_timestamp=int(task.tid), include_claimed=True
                    )
                    assert accepted is not None
                os.kill(process.pid, signal.SIGKILL)
                process.join(timeout=10.0)
                assert not process.is_alive()
                liveness = LivenessMonitor(
                    context.broker_target,
                    make_task_monitor_taskspec(generate_tid(context)),
                    config=context.config,
                    mapping_min_age_seconds=0.0,
                )
                try:
                    liveness._reconcile_mapping_rows(full=True)
                    assert (
                        liveness._retire_mapping_row(liveness._latest_rows[manager_tid])
                        is False
                    )
                finally:
                    liveness.stop(join=False)
                    liveness.cleanup()
                harness.ensure_foreground_manager(timeout=30.0)
                result = task.result(timeout=60.0)
                assert result.status == "completed", result.error
                assert executions.read_text().splitlines() == ["once"]
                with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
                    launches = [
                        json.loads(body)
                        for body in log.peek_generator()
                        if json.loads(body).get("event") == "task_spawned"
                        and json.loads(body).get("child_tid") == task.tid
                    ]
                assert len(launches) == 1
                assert _reservation_absent(context, manager_tid, task.tid)
        finally:
            if process.is_alive():
                process.kill()
                process.join(timeout=10.0)
            process.close()
