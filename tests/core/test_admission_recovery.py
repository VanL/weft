"""Reservation/checkpoint recovery without prospective debt [MF-5], [MANAGER.11]."""

from __future__ import annotations

import json
import multiprocessing
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import pytest

import weft.core.manager as manager_mod
from simplebroker import BrokerTarget, Queue
from simplebroker.ext import BrokerError
from tests.core.test_manager_family_maintenance import _publish_waiting_task
from tests.helpers.reactor_driver import drive_until
from tests.helpers.weft_harness import WeftTestHarness
from tests.tasks.sample_targets import signal_ready_and_wait_for_release, wait_for_file
from weft._constants import resolve_runtime_config
from weft.core.manager import (
    ManagedChild,
    Manager,
    _ManagerChildLaunchRequest,
    _ManagerChildLaunchResult,
)
from weft.core.manager_runtime import build_manager_spec
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.runtime_identity import runtime_identity_for_task, submission_lineage
from weft.core.spawn_requests import submit_spawn_request
from weft.core.task_state import (
    manager_launch_custody_pending,
    read_task_state_snapshot,
    task_state_queue_name,
)
from weft.core.tasks import Consumer
from weft.core.taskspec import TaskSpec
from weft.helpers import encode_tid, process_create_time

pytestmark = [pytest.mark.shared, pytest.mark.timeout(45)]


@pytest.fixture
def recovery_manager(
    weft_harness: WeftTestHarness, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Manager]:
    """Use the real broker, checkpoint and reservation owners without services."""
    # This fixture isolates user-work dispatch: the standing heartbeat (and
    # every other internal service) is not supervised here [MA-1] item 7.
    monkeypatch.setattr(Manager, "_service_supervision_allowed", lambda self: False)
    ctx = weft_harness.context
    with ctx.queue("recovery.fixture.ids", persistent=True) as ids:
        tid = str(ids.generate_timestamp())
    config = resolve_runtime_config(
        {
            **dict(ctx.config),
            "ADMISSION_MAX_CONNECTIONS": 0,
            "TASK_MONITOR_ENABLED": False,
            "LIVENESS_MONITOR_ENABLED": False,
            "AUTOSTART_TASKS": False,
        }
    )
    manager = Manager(
        ctx.broker_target,
        build_manager_spec(ctx, tid, idle_timeout_override=0.0),
        config=config,
    )
    try:
        yield manager
    finally:
        manager.stop(join=False)
        manager.cleanup()


def _run_prelaunch_checkpoint_gate_manager(
    target: BrokerTarget,
    spec: dict[str, Any],
    config: dict[str, Any],
    request_id: int,
    ready: Any,
    release: Any,
) -> None:
    """Publish real prelaunch custody in its own OS process, then await the kill."""
    task = Manager(target, TaskSpec.model_validate(spec), config=config)
    try:
        source, reserved = task._queue_names["inbox"], task._queue_names["reserved"]
        row = task._queue(source).move_one(
            reserved, exact_timestamp=request_id, with_timestamps=True
        )
        assert row is not None
        body, stamp = row
        child_spec = task._build_child_spec(json.loads(body), stamp)
        assert child_spec is not None
        identity = runtime_identity_for_task(
            child_spec, target, config=task._manager_context().config
        )
        request = _ManagerChildLaunchRequest(
            child_spec,
            Consumer,
            None,
            None,
            None,
            True,
            source,
            reserved,
            stamp,
            identity,
        )
        task._checkpoint_admission_launch(request)
        ready.set()
        if not release.wait(timeout=60):
            raise TimeoutError("prelaunch checkpoint gate was not released")
    finally:
        task.stop(join=False)
        task.cleanup()


def _run_bootstrap_read_fault_manager(
    target: BrokerTarget, spec: dict[str, Any], config: dict[str, Any]
) -> None:
    """Run the actual successor with an external inherited-state read failure."""

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise BrokerError("injected inherited-state acquisition failure")

    manager_mod.latest_task_state_rows = unavailable
    task = Manager(target, TaskSpec.model_validate(spec), config=config)
    try:
        task.run()
    finally:
        task.stop(join=False)
        task.cleanup()


def _reserve_and_checkpoint(
    manager: Manager, harness: WeftTestHarness, ctrl_out: str | None = None
) -> _ManagerChildLaunchRequest:
    """Generate acceptance and resolved identity through production boundaries."""
    ctx = harness.context
    tid = submit_spawn_request(
        ctx.broker_target,
        taskspec={
            "name": "recovery-fixture",
            "io": {
                "inputs": {"inbox": "recovery.custom.inbox"},
                "outputs": {"outbox": "recovery.custom.outbox"},
                "control": {"ctrl_in": "recovery.custom.ctrl_in", "ctrl_out": ctrl_out},
            }
            if ctrl_out
            else {},
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
        work_payload="payload",
        config=ctx.config,
        inherited_weft_context=str(ctx.root),
    )
    source, reserved = manager._queue_names["inbox"], manager._queue_names["reserved"]
    with ctx.queue(source, persistent=True) as queue:
        row = queue.move_one(reserved, exact_timestamp=tid, with_timestamps=True)
    assert row is not None
    body, request_id = row
    spec = manager._build_child_spec(json.loads(body), request_id)
    assert spec is not None
    identity = runtime_identity_for_task(spec, ctx.broker_target, config=ctx.config)
    request = _ManagerChildLaunchRequest(
        spec, Consumer, None, None, None, True, source, reserved, request_id, identity
    )
    manager._checkpoint_admission_launch(request)
    return request


def test_unknown_launch_custody_is_durable_and_cannot_requeue_or_spin(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
) -> None:
    """Missing process receipt means ambiguity, never permission to replay."""
    manager = recovery_manager
    request = _reserve_and_checkpoint(manager, weft_harness)
    assert request.source_queue is not None and request.reserved_queue is not None
    snapshot = read_task_state_snapshot(weft_harness.context, manager.tid)
    assert snapshot is not None
    with weft_harness.context.broker() as broker:
        assert manager_launch_custody_pending(snapshot[1], broker=broker)
    manager._requeue_public_reserved_spawn_requests_before_yield(
        leader_tid=str(int(manager.tid) - 1)
    )
    with weft_harness.context.queue(request.source_queue, persistent=True) as source:
        assert source.peek_one(exact_timestamp=request.message_timestamp) is None
    with weft_harness.context.queue(
        request.reserved_queue, persistent=True
    ) as reserved:
        assert reserved.peek_one(exact_timestamp=request.message_timestamp) is not None
        assert not manager._queue_has_pending(reserved)
    assert not manager._manager_owned_work_pending()
    manager._retire_scheduler_residents()
    assert request.child_spec.tid in manager._scheduler_residents


@pytest.mark.parametrize(
    "unknown", ["missing-time", "foreign-host", "live-worker", "terminal-event"]
)
def test_retirement_requires_scoped_whole_process_exit(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    unknown: str,
) -> None:
    """A terminal event or incomplete wrapper identity cannot free a finite path."""
    manager = recovery_manager
    request = _reserve_and_checkpoint(manager, weft_harness)
    assert request.source_queue is not None and request.reserved_queue is not None
    manager._checkpoint_spawned_admission(
        _ManagerChildLaunchResult(request, multiprocessing.current_process())
    )
    tid = request.child_spec.tid
    assert tid is not None
    evidence = manager._scheduler_residents[tid]
    if unknown == "missing-time":
        evidence["host_processes"][0]["create_time"] = None
    elif unknown == "foreign-host":
        evidence["hostname"] = socket.gethostname() + ".other"
    elif unknown == "live-worker":
        evidence["host_processes"].insert(0, {"pid": 999999999, "create_time": 1.0})
    else:
        evidence["terminal"] = True
    manager._retire_scheduler_residents()
    assert tid in manager._scheduler_residents
    with weft_harness.context.queue(request.reserved_queue, persistent=True) as queue:
        assert queue.peek_one(exact_timestamp=request.message_timestamp) is not None


def test_positive_scoped_retirement_acknowledges_exact_request_then_releases_checkpoint(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
) -> None:
    """An expired process incarnation is positive exit evidence, even for a reused PID."""
    manager = recovery_manager
    request = _reserve_and_checkpoint(manager, weft_harness)
    assert request.source_queue is not None and request.reserved_queue is not None
    manager._checkpoint_spawned_admission(
        _ManagerChildLaunchResult(request, multiprocessing.current_process())
    )
    sibling = _reserve_and_checkpoint(manager, weft_harness)
    manager._checkpoint_spawned_admission(
        _ManagerChildLaunchResult(sibling, multiprocessing.current_process())
    )
    tid = request.child_spec.tid
    sibling_tid = sibling.child_spec.tid
    assert tid is not None and sibling_tid is not None
    manager._activity_wait_queues()
    retired_feedback = manager._startup_feedback[tid]
    sibling_feedback = manager._startup_feedback[sibling_tid]
    sibling_interest = sibling_feedback.queue
    assert retired_feedback.queue is not None and sibling_interest is not None
    evidence = manager._scheduler_residents[tid]
    pid = multiprocessing.current_process().pid
    assert pid is not None
    created = process_create_time(pid)
    assert created is not None
    evidence["host_processes"][0]["create_time"] = created - 100.0
    manager._retire_scheduler_residents()
    assert tid not in manager._scheduler_residents
    assert tid not in manager._startup_feedback
    assert retired_feedback.queue is None
    assert sibling_tid in manager._scheduler_residents
    assert manager._startup_feedback[sibling_tid] is sibling_feedback
    assert sibling_feedback.queue is sibling_interest
    with weft_harness.context.queue(request.reserved_queue, persistent=True) as queue:
        assert queue.peek_one(exact_timestamp=request.message_timestamp) is None
        assert queue.peek_one(exact_timestamp=sibling.message_timestamp) is not None
    snapshot = read_task_state_snapshot(weft_harness.context, manager.tid)
    assert snapshot is not None
    residents = snapshot[1]["scheduler_frontiers"]["residents"]
    assert tid not in residents and sibling_tid in residents
    with weft_harness.context.broker() as broker:
        assert manager_launch_custody_pending(snapshot[1], broker=broker)

    manager._scheduler_residents[sibling_tid]["host_processes"][0]["create_time"] = (
        created - 100.0
    )
    manager._retire_scheduler_residents()
    assert sibling_feedback.queue is None
    snapshot = read_task_state_snapshot(weft_harness.context, manager.tid)
    assert snapshot is not None
    with weft_harness.context.broker() as broker:
        assert not manager_launch_custody_pending(snapshot[1], broker=broker)


def test_launch_failure_releases_only_failed_startup_and_keeps_sibling_custody(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real worker's OS-launch error reaches per-TID failure settlement.

    Checkpointed requests and scoped current-process evidence are real. This
    tests the failure owner boundary, not scheduling two worker operations.
    """
    manager = recovery_manager
    request = _reserve_and_checkpoint(manager, weft_harness)
    sibling = _reserve_and_checkpoint(manager, weft_harness)
    manager._checkpoint_spawned_admission(
        _ManagerChildLaunchResult(sibling, multiprocessing.current_process())
    )
    tid, sibling_tid = request.child_spec.tid, sibling.child_spec.tid
    assert tid is not None and sibling_tid is not None
    manager._activity_wait_queues()
    failed_feedback = manager._startup_feedback[tid]
    sibling_feedback = manager._startup_feedback[sibling_tid]
    sibling_interest = sibling_feedback.queue
    assert failed_feedback.queue is not None and sibling_interest is not None
    failure = OSError("named pre-process launch failure")

    def failed_launch(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr("weft.core.manager.launch_task_process", failed_launch)
    result = manager._run_child_launch_worker(request)
    assert result.error is failure and result.process is None
    manager._handle_child_launch_result(result)
    assert tid not in manager._startup_feedback
    assert failed_feedback.queue is None
    assert tid not in manager._scheduler_residents
    assert manager._startup_feedback[sibling_tid] is sibling_feedback
    assert sibling_feedback.queue is sibling_interest
    assert sibling_tid in manager._scheduler_residents
    assert request.source_queue is not None and request.reserved_queue is not None
    with weft_harness.context.queue(request.source_queue, persistent=True) as source:
        assert source.peek_one(exact_timestamp=request.message_timestamp) is None
    with weft_harness.context.queue(
        request.reserved_queue, persistent=True
    ) as reserved:
        # Existing KEEP policy retains failed acceptance. A free startup slot
        # cannot erase that row or the live sibling's exact reserved request.
        assert reserved.peek_one(exact_timestamp=request.message_timestamp) is not None
        assert reserved.peek_one(exact_timestamp=sibling.message_timestamp) is not None


def test_wrapper_checkpoint_preserves_already_observed_live_worker(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
) -> None:
    """A later launch-result checkpoint must not erase scoped worker custody."""
    manager = recovery_manager
    request = _reserve_and_checkpoint(manager, weft_harness)
    assert request.source_queue is not None and request.reserved_queue is not None
    result = _ManagerChildLaunchResult(request, multiprocessing.current_process())
    manager._checkpoint_spawned_admission(result)
    tid = request.child_spec.tid
    assert tid is not None
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.read()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as worker:
        assert worker.stdout is not None and worker.stdout.readline().strip() == "ready"
        observed = {"pid": worker.pid, "create_time": process_create_time(worker.pid)}
        manager._scheduler_residents[tid]["host_processes"].append(observed)
        try:
            manager._checkpoint_spawned_admission(result)
            snapshot = read_task_state_snapshot(weft_harness.context, manager.tid)
            assert snapshot is not None
            processes = snapshot[1]["scheduler_frontiers"]["residents"][tid][
                "host_processes"
            ]
            assert observed in processes
        finally:
            assert worker.stdin is not None
            worker.stdin.close()
            worker.wait(timeout=10.0)


@pytest.mark.parametrize("task_proof", [False, True])
@pytest.mark.parametrize("ctrl_out", [None, "recovery.custom.ctrl_out"])
def test_unhanded_launch_settlement_publishes_terminal_before_ack(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    task_proof: bool,
    ctrl_out: str | None,
) -> None:
    """Positive wrapper exit cannot discard custody without terminal evidence [MA-1.6a]."""
    manager = recovery_manager
    request = _reserve_and_checkpoint(manager, weft_harness, ctrl_out)
    manager._checkpoint_spawned_admission(
        _ManagerChildLaunchResult(request, multiprocessing.current_process())
    )
    tid = request.child_spec.tid
    assert tid is not None
    evidence = manager._scheduler_residents[tid]
    evidence["host_processes"][0]["create_time"] -= 100.0
    with weft_harness.context.queue(
        ctrl_out or f"T{tid}.ctrl_out", persistent=True
    ) as output:
        if task_proof:
            output.write(
                json.dumps(
                    {
                        "type": "terminal",
                        "tid": tid,
                        "source": "task",
                        "status": "completed",
                    }
                )
            )
        manager._retire_scheduler_residents()
        rows = [json.loads(body) for body in output.peek_generator()]
    assert len(rows) == 1
    assert rows[0]["source"] == ("task" if task_proof else "manager")
    assert rows[0]["status"] == ("completed" if task_proof else "failed")
    if not task_proof:
        assert (
            rows[0]["error"] == "Task wrapper exited before publishing terminal state"
        )
    assert request.reserved_queue is not None
    with weft_harness.context.queue(request.reserved_queue, persistent=True) as queue:
        assert (
            queue.peek_one(
                exact_timestamp=request.message_timestamp, include_claimed=True
            )
            is None
        )


def test_unhanded_launch_keeps_custody_until_publication_succeeds(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal-write failure must preserve the exact durable reservation [MA-1.6a]."""
    manager = recovery_manager
    request = _reserve_and_checkpoint(manager, weft_harness)
    manager._checkpoint_spawned_admission(
        _ManagerChildLaunchResult(request, multiprocessing.current_process())
    )
    tid = request.child_spec.tid
    assert tid is not None and request.reserved_queue is not None
    manager._scheduler_residents[tid]["host_processes"][0]["create_time"] -= 100
    manager._publish_scheduler_checkpoint()
    queue = manager._get_connected_queue()
    original = queue.get_connection

    class FailingTerminalWrite:
        def __init__(self, broker: Any) -> None:
            self.broker = broker

        def __getattr__(self, name: str) -> Any:
            return getattr(self.broker, name)

        def write(self, name: str, body: str, **kwargs: Any) -> Any:
            if name == f"T{tid}.ctrl_out":
                raise BrokerError("terminal publication unavailable")
            return self.broker.write(name, body, **kwargs)

    @contextmanager
    def failed_connection() -> Iterator[Any]:
        with original() as broker:
            yield FailingTerminalWrite(broker)

    with monkeypatch.context() as patch:
        patch.setattr(queue, "get_connection", failed_connection)
        manager._retire_scheduler_residents()
    with weft_harness.context.queue(
        request.reserved_queue, persistent=True
    ) as reserved:
        assert (
            reserved.peek_one(
                exact_timestamp=request.message_timestamp, include_claimed=True
            )
            is not None
        )
    snapshot = read_task_state_snapshot(weft_harness.context, manager.tid)
    assert snapshot is not None
    assert tid in snapshot[1]["scheduler_frontiers"]["residents"]
    with weft_harness.context.queue(f"T{tid}.ctrl_out", persistent=True) as output:
        assert output.peek_one() is None
        manager._retire_scheduler_residents()
        body = output.peek_one()
        assert body is not None
        assert json.loads(body)["source"] == "manager"
    with weft_harness.context.queue(
        request.reserved_queue, persistent=True
    ) as reserved:
        assert (
            reserved.peek_one(
                exact_timestamp=request.message_timestamp, include_claimed=True
            )
            is None
        )


def test_unhanded_launch_unresolved_route_keeps_custody_and_settles_sibling(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unreadable route holds only its own settlement and warns once [MF-5]."""
    manager = recovery_manager
    first = _reserve_and_checkpoint(manager, weft_harness)
    second = _reserve_and_checkpoint(manager, weft_harness)
    for request in (first, second):
        assert request.child_spec.tid is not None
        manager._checkpoint_spawned_admission(
            _ManagerChildLaunchResult(request, multiprocessing.current_process())
        )
        manager._scheduler_residents[request.child_spec.tid]["host_processes"][0][
            "create_time"
        ] -= 100
    tid = first.child_spec.tid
    assert tid is not None and first.reserved_queue is not None
    with weft_harness.context.queue(first.reserved_queue, persistent=True) as reserved:
        bad_id = reserved.write("{}")
        manager._scheduler_residents[tid]["request_id"] = bad_id
        manager._publish_scheduler_checkpoint()
        manager._retire_scheduler_residents()
        manager._retire_scheduler_residents()
        assert (
            reserved.peek_one(exact_timestamp=bad_id, include_claimed=True) is not None
        )
        assert (
            reserved.peek_one(
                exact_timestamp=second.message_timestamp, include_claimed=True
            )
            is None
        )
    assert tid in manager._scheduler_residents
    assert second.child_spec.tid not in manager._scheduler_residents
    assert (
        len(
            [
                r
                for r in caplog.records
                if tid in r.getMessage()
                and "held because its terminal route" in r.getMessage()
            ]
        )
        == 1
    )


@pytest.mark.parametrize("bad_snapshot", [False, True])
def test_successor_skips_mismatched_identity_and_preserves_reservation(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    caplog: pytest.LogCaptureFixture,
    bad_snapshot: bool,
) -> None:
    """Persisted foreign evidence cannot block refresh or replay another launch [MA-1.8]."""
    first = recovery_manager
    request = _reserve_and_checkpoint(first, weft_harness)
    first._checkpoint_spawned_admission(
        _ManagerChildLaunchResult(request, multiprocessing.current_process())
    )
    tid = request.child_spec.tid
    assert (
        tid is not None
        and request.reserved_queue is not None
        and request.source_queue is not None
    )
    snapshot = read_task_state_snapshot(weft_harness.context, first.tid)
    assert snapshot is not None
    payload = snapshot[1]
    payload["runtime_handle"]["observations"]["host_processes"][0]["create_time"] -= 100
    evidence = payload["scheduler_frontiers"]["residents"][tid]
    if bad_snapshot:
        child = {
            **evidence,
            "full": tid,
            "short": encode_tid(tid),
            "connection_owner": "mismatched-owner",
        }
        with weft_harness.context.queue(
            task_state_queue_name(tid), persistent=True
        ) as state:
            state.write(json.dumps(child))
    else:
        evidence["connection_owner"] = "mismatched-owner"
    first._unregister_manager(status="stopped")
    with weft_harness.context.queue(
        task_state_queue_name(first.tid), persistent=True
    ) as state:
        state.write(json.dumps(payload))
    with weft_harness.context.queue("successor.ids", persistent=True) as ids:
        successor_tid = str(ids.generate_timestamp())
    successor = Manager(
        weft_harness.context.broker_target,
        build_manager_spec(
            weft_harness.context, successor_tid, idle_timeout_override=0
        ),
        config=first._manager_context().config,
    )
    try:
        unrelated = submit_spawn_request(
            weft_harness.context.broker_target,
            taskspec={
                "name": "unrelated",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            },
            work_payload="unrelated",
            config=weft_harness.context.config,
        )
        with weft_harness.context.queue(
            request.source_queue, persistent=True
        ) as source:
            drive_until(
                lambda: source.peek_one(exact_timestamp=unrelated),
                lambda row: row is None,
                step=successor.process_once,
                wait=successor.wait_for_activity,
                timeout=15,
            )
            assert source.peek_one(exact_timestamp=request.message_timestamp) is None
        assert successor._admission_bootstrapped
        assert tid in successor._bootstrap_skipped
        # Re-enter the real reconciliation owner, including its per-resident reread.
        for _ in range(2):
            successor._admission_refresh_after = 0
            successor.process_once()
            assert successor._admission_bootstrapped
        assert (
            len(
                [
                    r
                    for r in caplog.records
                    if "Manager bootstrap ignored" in r.getMessage()
                ]
            )
            == 1
        )
        with weft_harness.context.queue(
            request.reserved_queue, persistent=True
        ) as reserved:
            assert (
                reserved.peek_one(
                    exact_timestamp=request.message_timestamp, include_claimed=True
                )
                is not None
            )
    finally:
        successor.stop(join=False)
        successor.cleanup()


@pytest.mark.parametrize("retire_first", [False, True])
def test_live_manager_wrapper_kill_commits_one_failure_with_exit_code(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    tmp_path: Path,
    retire_first: bool,
) -> None:
    """Live reap and resident retirement share one committed wrapper outcome [MA-1.6a]."""
    manager = recovery_manager
    request = _reserve_and_checkpoint(manager, weft_harness)
    tid = request.child_spec.tid
    assert tid is not None and request.reserved_queue is not None
    ready, release = tmp_path / "wrapper.ready", tmp_path / "wrapper.release"
    process = multiprocessing.get_context("spawn").Process(
        target=signal_ready_and_wait_for_release, args=(str(ready), str(release))
    )
    try:
        process.start()
        manager._checkpoint_spawned_admission(
            _ManagerChildLaunchResult(request, process)
        )
        child = ManagedChild(
            process,
            None,
            ctrl_out_queue=f"T{tid}.ctrl_out",
            reserved_queue=request.reserved_queue,
            message_timestamp=request.message_timestamp,
        )
        manager._child_processes[tid] = child
        manager._register_child_sentinel(tid, child)
        wait_for_file(str(ready), timeout=20)
        process.kill()
        process.join(timeout=10)
        assert not process.is_alive()
        exitcode = process.exitcode
        assert exitcode is not None and exitcode != 0
        if os.name != "nt":
            assert exitcode == -9
        if retire_first:
            manager._retire_scheduler_residents()
        assert manager._cleanup_children()
        with weft_harness.context.queue(f"T{tid}.ctrl_out", persistent=True) as output:
            envelopes = [json.loads(body) for body in output.peek_generator()]
        assert len(envelopes) == 1, envelopes
        assert envelopes[0]["source"] == "manager"
        assert (
            envelopes[0]["error"]
            == "Task wrapper exited before publishing terminal state"
        )
        assert envelopes[0].get("return_code") == exitcode
        with weft_harness.context.queue(
            request.reserved_queue, persistent=True
        ) as reserved:
            assert (
                reserved.peek_one(
                    exact_timestamp=request.message_timestamp, include_claimed=True
                )
                is None
            )
    finally:
        release.touch()
        if process.pid is not None:
            if process.is_alive():
                process.kill()
            process.join(timeout=10)


@pytest.mark.parametrize("difference", [None, "tid", "status", "error", "source"])
def test_wrapper_lost_commit_recognizes_only_matching_manager_failure(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    difference: str | None,
) -> None:
    """Idempotence requires the matching typed supervisor outcome, not any row."""
    manager = recovery_manager
    with weft_harness.context.queue("terminal.proof.ids", persistent=True) as ids:
        tid = str(ids.generate_timestamp())
    route = f"T{tid}.ctrl_out"
    payload = {
        "type": "terminal",
        "source": "manager",
        "tid": tid,
        "status": "failed",
        "error": "Task wrapper exited before publishing terminal state",
        "return_code": -9,
    }
    if difference is not None:
        payload[difference] = {
            "tid": str(int(tid) + 4096),
            "status": "completed",
            "error": "another failure",
            "source": "unrecognized",
        }[difference]
    with weft_harness.context.queue(route, persistent=True) as output:
        output.write(json.dumps(payload))
        # Supervisor evidence never becomes task-owned proof for the grace clock.
        assert manager._terminal_proof_visible(tid, route) is False
        assert manager._commit_wrapper_lost_envelope(
            tid, ctrl_out_name=route, exitcode=None
        )
        rows = [json.loads(body) for body in output.peek_generator()]
    assert len(rows) == (1 if difference is None else 2)
    if difference is None:
        assert rows[0] == payload


def test_held_only_source_retires_stall_deadline_and_waits_quietly(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Held source rows retain custody without keeping the real watcher ready."""
    manager = recovery_manager
    request = _reserve_and_checkpoint(manager, weft_harness)
    assert request.runtime_identity is not None
    source = manager._queue(manager._queue_names["inbox"])
    reserved = manager._queue(manager._queue_names["reserved"])
    row = reserved.peek_one(exact_timestamp=request.message_timestamp)
    assert isinstance(row, str)
    payload = json.loads(row)
    payload["submitted_by"] = submission_lineage(request.runtime_identity)
    child_id = int(source.write(json.dumps(payload)))
    manager._refresh_scheduler_evidence()
    manager._classify_family_requests()
    child = manager._family_scheduler.pending(child_id)
    assert child is not None
    token = manager._queue(child.queue_name).peek_one(
        exact_timestamp=child.label_message_id
    )
    assert token is not None
    assert manager._peek_family_candidate() is None
    monkeypatch.setitem(manager._weft_config, "MANAGER_SERVE_LOG_ACTIVE", True)
    monkeypatch.setitem(manager._weft_config, "MANAGER_SERVE_LOG_LEVEL", "info")
    assert manager._manager_log_allows("info")
    # An earlier actual stall may already have armed the diagnostic clock.
    manager._last_public_dispatch_stall_log_ns = 1
    deadline = time.monotonic() + 20
    while True:
        manager.process_once()
        assert manager._last_public_dispatch_stall_log_ns == 0
        assert not manager._queue_has_pending(source)
        started = time.monotonic()
        manager.wait_for_activity(timeout=0.3)
        if time.monotonic() - started >= 0.25:
            break
        assert time.monotonic() < deadline
    assert source.peek_one(exact_timestamp=child_id) is not None
    assert reserved.peek_one(exact_timestamp=request.message_timestamp) is not None
    assert manager._family_scheduler.pending(child_id) is not None
    assert (
        manager._queue(child.queue_name).peek_one(
            exact_timestamp=child.label_message_id
        )
        == token
    )


def test_standing_missing_receipt_publishes_complete_snapshot_once(
    recovery_manager: Manager, weft_harness: WeftTestHarness
) -> None:
    """Retained custody becomes visible only after a second due owner pass."""
    manager = recovery_manager
    request = _reserve_and_checkpoint(manager, weft_harness)
    tid = request.child_spec.tid
    assert request.reserved_queue is not None
    manager.process_once()
    first = read_task_state_snapshot(weft_harness.context, manager.tid)
    assert first is not None
    assert first[1]["dispatch_refusals"]["items"] == []
    manager._admission_refresh_after = 0
    manager.process_once()
    row = read_task_state_snapshot(weft_harness.context, manager.tid)
    assert row is not None
    observation = row[1]["dispatch_refusals"]
    assert observation["assessment"] == "known"
    assert len(observation["items"]) == 1
    refusal = observation["items"][0]
    assert refusal["reason"] == "launch_receipt_missing"
    assert refusal["ends_when"] == "matching_started_evidence"
    assert refusal["subjects"] == [tid]
    assert refusal["evidence"] == [
        {"queue": request.reserved_queue, "message_id": request.message_timestamp}
    ]
    assert tid in row[1]["scheduler_frontiers"]["residents"]
    for _ in range(3):
        manager._admission_refresh_after = 0
        manager.process_once()
        stable = read_task_state_snapshot(weft_harness.context, manager.tid)
        assert stable is not None and stable[0] == row[0]
    assert (
        manager._queue(request.reserved_queue).peek_one(
            exact_timestamp=request.message_timestamp
        )
        is not None
    )

    spawn = multiprocessing.get_context("spawn")
    ready, release = spawn.Event(), spawn.Event()
    assert request.runtime_identity is not None
    process = spawn.Process(
        target=_publish_waiting_task,
        args=(
            request.child_spec.model_dump(mode="json"),
            request.runtime_identity.to_dict(),
            ready,
            release,
            None,
            None,
            True,
        ),
    )
    try:
        manager._ensure_wait_strategy_started()
        process.start()
        assert ready.wait(timeout=20)
        manager._admission_refresh_after = 0
        manager.process_once()
        cleared = read_task_state_snapshot(weft_harness.context, manager.tid)
        assert cleared is not None
        assert not any(
            item["reason"] == "launch_receipt_missing"
            for item in cleared[1]["dispatch_refusals"]["items"]
        )
        assert (
            manager._queue(request.reserved_queue).peek_one(
                exact_timestamp=request.message_timestamp
            )
            is not None
        )
    finally:
        release.set()
        if process.pid is not None:
            process.join(timeout=10)
            if process.is_alive():
                process.kill()
                process.join(timeout=10)
        process.close()


@pytest.mark.parametrize("gate", ["bootstrap_incomplete", "checkpoint_uncommitted"])
def test_diagnostic_append_preserves_actual_bootstrap_and_checkpoint_gates(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    gate: str,
) -> None:
    """A successful diagnostic write cannot complete the failed owner transition."""
    manager = recovery_manager
    if gate == "checkpoint_uncommitted":
        manager._refresh_scheduler_evidence()
    request = _reserve_and_checkpoint(manager, weft_harness)
    assert request.reserved_queue is not None
    source = manager._queue(manager._queue_names["inbox"])
    body = manager._queue(request.reserved_queue).peek_one(
        exact_timestamp=request.message_timestamp
    )
    assert isinstance(body, str)
    waiting = int(source.write(body))
    state_name = task_state_queue_name(manager.tid)
    original_write = Queue.write
    failed_generations: set[int] = set()

    def fail_required_write(self: Queue, *args: Any, **kwargs: Any) -> Any:
        generation = manager._dispatch_generation
        if self.name == state_name and generation not in failed_generations:
            failed_generations.add(generation)
            raise BrokerError("injected required checkpoint write failure")
        return original_write(self, *args, **kwargs)

    def unreadable_inherited(*args: Any, **kwargs: Any) -> Any:
        raise BrokerError("injected inherited snapshot read failure")

    spawn = multiprocessing.get_context("spawn")
    ready, release = tmp_path / "ready", tmp_path / "release"
    process = spawn.Process(
        target=signal_ready_and_wait_for_release, args=(str(ready), str(release))
    )
    try:
        with monkeypatch.context() as patch:
            if gate == "checkpoint_uncommitted":
                process.start()
                wait_for_file(str(ready), timeout=20)
                patch.setattr(Queue, "write", fail_required_write)
                manager._checkpoint_spawned_admission(
                    _ManagerChildLaunchResult(request, process)
                )
            else:
                patch.setattr(
                    manager_mod, "latest_task_state_rows", unreadable_inherited
                )
            for _ in range(2):
                manager._admission_refresh_after = 0
                manager.process_once()
            row = read_task_state_snapshot(weft_harness.context, manager.tid)
            assert row is not None
            items = row[1]["dispatch_refusals"]["items"]
            assert any(
                item["reason"] == gate
                and item["oldest_request"] == waiting
                and item["ends_when"]
                == (
                    "bootstrap_complete"
                    if gate == "bootstrap_incomplete"
                    else "checkpoint_committed"
                )
                for item in items
            )
            assert request.child_spec.tid in row[1]["scheduler_frontiers"]["residents"]
            if gate == "checkpoint_uncommitted":
                assert request.child_spec.tid in manager._scheduler_undurable
                assert manager._admission_bootstrapped
            else:
                assert not manager._admission_bootstrapped
            assert source.peek_one(exact_timestamp=waiting) is not None
            assert (
                manager._queue(request.reserved_queue).peek_one(
                    exact_timestamp=request.message_timestamp
                )
                is not None
            )
    finally:
        release.touch()
        if process.pid is not None:
            process.join(timeout=10)
            if process.is_alive():
                process.kill()
                process.join(timeout=10)
        process.close()


def test_failed_diagnostic_append_keeps_last_full_snapshot_and_retries_on_due_pass(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = recovery_manager
    first = _reserve_and_checkpoint(manager, weft_harness)
    for _ in range(2):
        manager._admission_refresh_after = 0
        manager.process_once()
    second = _reserve_and_checkpoint(manager, weft_harness)
    second_tid = second.child_spec.tid
    assert second_tid is not None
    manager._active_child_launches[second_tid] = second
    manager._child_launch_started_ns[second_tid] = 0
    assert manager._retry_stale_child_launches()
    manager._admission_refresh_after = 0
    manager.process_once()
    before = read_task_state_snapshot(weft_harness.context, manager.tid)
    assert before is not None
    original = Queue.write
    attempts: list[str] = []

    def failed(self: Queue, *args: Any, **kwargs: Any) -> Any:
        if self.name == task_state_queue_name(manager.tid):
            attempts.append(self.name)
            raise BrokerError("injected diagnostic append failure")
        return original(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Queue, "write", failed)
        manager._admission_refresh_after = 0
        manager.process_once()
        assert attempts == [task_state_queue_name(manager.tid)]
        assert read_task_state_snapshot(weft_harness.context, manager.tid) == before
        manager.process_once()
        assert len(attempts) == 1, "incidental turn retried a failed diagnostic"
    manager._admission_refresh_after = 0
    manager.process_once()
    after = read_task_state_snapshot(weft_harness.context, manager.tid)
    assert after is not None and after[0] > before[0]
    assert {
        tid
        for item in after[1]["dispatch_refusals"]["items"]
        for tid in item.get("subjects", [])
    } == {first.child_spec.tid, second_tid}
    for request in (first, second):
        assert request.reserved_queue is not None
        assert (
            manager._queue(request.reserved_queue).peek_one(
                exact_timestamp=request.message_timestamp
            )
            is not None
        )


def test_second_successor_preserves_custody_after_bootstrap_diagnostic_crash(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
) -> None:
    """Two actual Manager deaths cannot erase the first owner's reservation."""
    manager = recovery_manager
    context = weft_harness.context
    config = {**dict(manager._weft_config), "HEARTBEAT_ENABLED": False}
    request_id = submit_spawn_request(
        context.broker_target,
        taskspec={
            "name": "never-replay",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
        work_payload="payload",
        config=context.config,
        inherited_weft_context=str(context.root),
    )
    manager.stop(join=False)
    manager.cleanup()
    spawn = multiprocessing.get_context("spawn")
    processes: list[Any] = []
    successor: Manager | None = None
    with context.queue("weft.spawn.requests", persistent=True) as source:
        first_tid = str(source.generate_timestamp())
        ready, release = spawn.Event(), spawn.Event()
        first = spawn.Process(
            target=_run_prelaunch_checkpoint_gate_manager,
            args=(
                context.broker_target,
                build_manager_spec(
                    context, first_tid, idle_timeout_override=0
                ).model_dump(mode="json"),
                config,
                request_id,
                ready,
                release,
            ),
        )
        processes.append(first)
        try:
            first.start()
            assert first.pid is not None
            weft_harness.register_pid(first.pid, first_tid)
            assert ready.wait(timeout=20)
            predecessor = read_task_state_snapshot(context, first_tid)
            assert predecessor is not None and request_id in map(
                int, predecessor[1]["scheduler_frontiers"]["residents"]
            )
            first.kill()
            first.join(timeout=10)
            assert not first.is_alive()
            with context.queue(f"T{first_tid}.reserved", persistent=True) as reserved:
                body = reserved.peek_one(exact_timestamp=request_id)
                assert isinstance(body, str)
                waiting = int(source.write(body))
                weft_harness.register_tid(str(waiting))
                second_tid = str(source.generate_timestamp())
                second = spawn.Process(
                    target=_run_bootstrap_read_fault_manager,
                    args=(
                        context.broker_target,
                        build_manager_spec(
                            context, second_tid, idle_timeout_override=0
                        ).model_dump(mode="json"),
                        config,
                    ),
                )
                processes.append(second)
                second.start()
                assert second.pid is not None
                weft_harness.register_pid(second.pid, second_tid)
                with (
                    context.queue(
                        task_state_queue_name(second_tid), persistent=True
                    ) as state,
                    closing(
                        QueueChangeMonitor([state], config=context.config)
                    ) as monitor,
                ):

                    def wait(*, timeout: float) -> None:
                        monitor.wait(timeout)

                    row = drive_until(
                        lambda: read_task_state_snapshot(context, second_tid),
                        lambda row: (
                            row is not None
                            and any(
                                item["reason"] == "bootstrap_incomplete"
                                for item in row[1]
                                .get("dispatch_refusals", {})
                                .get("items", [])
                            )
                        ),
                        step=lambda: None,
                        wait=wait,
                        timeout=20,
                    )
                assert row is not None
                assert row[1]["full"] == second_tid and row[1]["role"] == "manager"
                assert "scheduler_frontiers" in row[1], (
                    "refusal-only state destroyed the full checkpoint"
                )
                assert reserved.peek_one(exact_timestamp=request_id) is not None
                second.kill()
                second.join(timeout=10)
                assert not second.is_alive()
                next_tid = str(source.generate_timestamp())
                successor = Manager(
                    context.broker_target,
                    build_manager_spec(context, next_tid, idle_timeout_override=0),
                    config=config,
                )
                successor.process_once()
                assert successor._admission_bootstrapped
                assert str(request_id) in successor._scheduler_residents
                assert reserved.peek_one(exact_timestamp=request_id) is not None
                assert source.peek_one(exact_timestamp=request_id) is None
        finally:
            # SIGKILL leaves an Event's waiter count behind. Do not notify that
            # dead waiter during teardown; kill/join owns these processes.
            for process in processes:
                if process.pid is not None:
                    if process.is_alive():
                        process.kill()
                    process.join(timeout=10)
                process.close()
            if successor is not None:
                successor.stop(join=False)
                successor.cleanup()


def test_internal_receipt_refusal_names_its_actual_source_lane(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
) -> None:
    """Bootstrap cannot describe an internal held request as a public family path."""
    manager = recovery_manager
    source = manager._queue(manager._queue_names["internal_inbox"])
    reserved = manager._queue_names["internal_reserved"]
    payload = {
        "taskspec": {
            "name": "internal-held",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {"internal": True},
        }
    }
    request_id = int(source.write(json.dumps(payload)))
    assert source.move_one(reserved, exact_timestamp=request_id) is not None
    spec = manager._build_child_spec(payload, request_id)
    assert spec is not None
    identity = runtime_identity_for_task(
        spec,
        weft_harness.context.broker_target,
        internal=True,
        config=weft_harness.context.config,
    )
    request = _ManagerChildLaunchRequest(
        spec,
        Consumer,
        None,
        None,
        None,
        True,
        source.name,
        reserved,
        request_id,
        identity,
    )
    manager._checkpoint_admission_launch(request)
    for _ in range(2):
        manager._admission_refresh_after = 0
        manager.process_once()
    row = read_task_state_snapshot(weft_harness.context, manager.tid)
    assert row is not None
    item = next(
        item
        for item in row[1]["dispatch_refusals"]["items"]
        if item["reason"] == "launch_receipt_missing"
    )
    assert item["lane"] == "internal" and item["scope"] == "request"
    assert item["subjects"] == [str(request_id)]
    assert item["evidence"] == [{"queue": reserved, "message_id": request_id}]
    assert manager._queue(reserved).peek_one(exact_timestamp=request_id) is not None
