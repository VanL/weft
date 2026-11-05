"""Reservation/checkpoint recovery without prospective debt [MF-5], [MANAGER.11]."""

from __future__ import annotations

import json
import multiprocessing
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from simplebroker.ext import BrokerError
from tests.helpers.reactor_driver import drive_until
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
    task_state_queue_name,
)
from weft.core.tasks import Consumer
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
