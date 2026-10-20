"""Reservation/checkpoint recovery without prospective debt [MF-5], [MANAGER.11]."""

from __future__ import annotations

import json
import multiprocessing
import socket
import subprocess
import sys
from collections.abc import Iterator

import pytest

from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import resolve_runtime_config
from weft.core.manager import (
    Manager,
    _ManagerChildLaunchRequest,
    _ManagerChildLaunchResult,
)
from weft.core.manager_runtime import build_manager_spec
from weft.core.runtime_identity import runtime_identity_for_task
from weft.core.spawn_requests import submit_spawn_request
from weft.core.task_state import (
    manager_launch_custody_pending,
    read_task_state_snapshot,
)
from weft.core.tasks import Consumer
from weft.helpers import process_create_time

pytestmark = [pytest.mark.shared, pytest.mark.timeout(45)]


@pytest.fixture
def recovery_manager(weft_harness: WeftTestHarness) -> Iterator[Manager]:
    """Use the real broker, checkpoint and reservation owners without services."""
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


def _reserve_and_checkpoint(
    manager: Manager, harness: WeftTestHarness
) -> _ManagerChildLaunchRequest:
    """Generate acceptance and resolved identity through production boundaries."""
    ctx = harness.context
    tid = submit_spawn_request(
        ctx.broker_target,
        taskspec={
            "name": "recovery-fixture",
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
