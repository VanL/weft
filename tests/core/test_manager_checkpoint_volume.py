"""Manager checkpoint volume: one complete append per reactor turn [MF-5], [OBS.6].

A real Manager is driven turn by turn against the real broker and launches
real child processes. Assertions read the rows the Manager appended to its own
``weft.state.tasks.<manager_tid>`` queue, and a successor recovers from them.
Plan: docs/plans/2026-11-08-manager-checkpoint-volume-plan.md.
"""

from __future__ import annotations

import json
import multiprocessing
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from simplebroker import Queue
from simplebroker.ext import BrokerError
from tests.core.test_admission_recovery import _reserve_and_checkpoint
from tests.helpers.manager_dispatch import observe_child_exit
from tests.helpers.reactor_driver import drive_until
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import MANAGER_CHECKPOINT_RESIDENT_FIELDS, resolve_runtime_config
from weft.core import manager as manager_mod
from weft.core.manager import Manager, _ManagerChildLaunchResult
from weft.core.manager_runtime import build_manager_spec
from weft.core.spawn_requests import submit_spawn_request
from weft.core.task_state import read_task_state_snapshot, task_state_queue_name

pytestmark = [pytest.mark.shared, pytest.mark.timeout(120)]

_CUSTODY_FIELDS = {"source_queue", "reserved_queue", "request_id"}


def _new_manager(harness: WeftTestHarness, label: str) -> Manager:
    """Build a real Manager without internal services or the resource gate."""
    ctx = harness.context
    with ctx.queue(f"{label}.ids", persistent=True) as ids:
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
    return Manager(
        ctx.broker_target,
        build_manager_spec(ctx, tid, idle_timeout_override=0.0),
        config=config,
    )


@pytest.fixture
def volume_manager(
    weft_harness: WeftTestHarness, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Manager]:
    """Use the real broker, checkpoint and launch owners without services."""
    monkeypatch.setattr(Manager, "_service_supervision_allowed", lambda self: False)
    manager = _new_manager(weft_harness, "volume")
    try:
        yield manager
    finally:
        manager.stop(join=False)
        manager.cleanup()


def _checkpoint_rows(harness: WeftTestHarness, manager_tid: str) -> int:
    """Count the complete snapshots the Manager has appended so far."""
    with harness.context.broker() as broker:
        return sum(
            1 for _row in broker.peek_generator(task_state_queue_name(manager_tid))
        )


def _newest_checkpoint(harness: WeftTestHarness, manager_tid: str) -> dict[str, Any]:
    row = read_task_state_snapshot(harness.context, manager_tid)
    assert row is not None
    return row[1]


def _submit_blocked(harness: WeftTestHarness, ready: Path, release: Path) -> str:
    """Submit a real function task that signals entry, then awaits release."""
    ctx = harness.context
    tid = str(
        submit_spawn_request(
            ctx.broker_target,
            taskspec={
                "name": f"checkpoint-volume-{ready.name}",
                "spec": {
                    "type": "function",
                    "function_target": (
                        "tests.tasks.sample_targets:signal_ready_and_wait_for_release"
                    ),
                },
            },
            work_payload={
                "kwargs": {"ready_path": str(ready), "release_path": str(release)}
            },
            config=ctx.config,
            inherited_weft_context=str(ctx.root),
        )
    )
    harness.register_tid(tid)
    return tid


def _drive(
    manager: Manager, done: Callable[[], bool], *, timeout: float = 60.0
) -> None:
    drive_until(
        done,
        bool,
        step=manager.process_once,
        wait=manager.wait_for_activity,
        timeout=timeout,
    )


def _launch_blocked(
    harness: WeftTestHarness, manager: Manager, tmp_path: Path, count: int
) -> tuple[list[str], Path]:
    """Launch ``count`` blocked tasks and drive until every startup has handed off."""
    release = tmp_path / "release"
    readies = [tmp_path / f"ready-{index}" for index in range(count)]
    tids = [_submit_blocked(harness, ready, release) for ready in readies]
    _drive(
        manager,
        lambda: (
            all(ready.exists() for ready in readies)
            and set(tids) <= set(manager._child_processes)
            and not manager._startup_feedback
            and not manager._active_child_launches
        ),
    )
    return tids, release


def _reap_all(manager: Manager, release: Path) -> None:
    """Release every blocked task and wait, outside the reactor, for its exit.

    The exit sentinel is the Manager's only exit evidence and its adapter
    reports asynchronously ([MA-1.6a]), so each exit is also observed here;
    otherwise a joined child may still look live to the next turn.
    """
    release.touch()
    for tid, child in tuple(manager._child_processes.items()):
        child.process.join(timeout=30.0)
        assert not child.process.is_alive()
        observe_child_exit(manager, tid)


def test_one_turn_publishes_one_checkpoint_for_all_reaped_children(
    volume_manager: Manager, weft_harness: WeftTestHarness, tmp_path: Path
) -> None:
    """Several retirements in one turn append one complete checkpoint, not one each."""
    manager = volume_manager
    tids, release = _launch_blocked(weft_harness, manager, tmp_path, 3)
    _reap_all(manager, release)
    # Keep periodic reconciliation out of the measured turn; reaping alone
    # retires each wrapper here.
    manager._admission_refresh_after = time.monotonic() + 60.0
    before = _checkpoint_rows(weft_harness, manager.tid)
    manager.process_once()
    appended = _checkpoint_rows(weft_harness, manager.tid) - before

    assert not manager._child_processes
    assert not set(tids) & set(manager._scheduler_residents)
    assert appended == 1
    residents = _newest_checkpoint(weft_harness, manager.tid)["scheduler_frontiers"][
        "residents"
    ]
    assert not set(tids) & set(residents)


def test_checkpoint_appends_per_launch_stay_below_one_per_custody_event(
    volume_manager: Manager, weft_harness: WeftTestHarness, tmp_path: Path
) -> None:
    """A launch's custody events no longer cost one complete append each.

    Each launch has four custody events: pre-launch custody, the process
    receipt, snapshot handoff and retirement. One append per event (the
    previous design) is at least ``4 * count``. Pre-launch custody is a
    barrier and always appends; the others share their turn's single append.
    """
    manager = volume_manager
    count = 8
    manager._refresh_scheduler_evidence()  # bootstrap outside the measurement
    before = _checkpoint_rows(weft_harness, manager.tid)
    tids, release = _launch_blocked(weft_harness, manager, tmp_path, count)
    _reap_all(manager, release)
    _drive(
        manager,
        lambda: (
            not manager._child_processes
            and not set(tids) & set(manager._scheduler_residents)
        ),
    )
    appended = _checkpoint_rows(weft_harness, manager.tid) - before
    print(f"checkpoint appends for {count} launches: {appended}")
    # Fewer appends than custody events, diagnostic refusal appends included.
    assert appended < 4 * count
    assert not set(tids) & set(
        _newest_checkpoint(weft_harness, manager.tid)["scheduler_frontiers"][
            "residents"
        ]
    )


def test_launch_turn_publishes_receipt_and_successor_recovers_from_slim_checkpoint(
    volume_manager: Manager,
    weft_harness: WeftTestHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receipt is durable when its turn ends; recovery needs only custody fields.

    The measured turn is the one that commits the launch result. Bootstrap and
    pre-launch custody are barriers that publish at once ([MA-1.8], [MF-5]),
    so they happen in an earlier, unmeasured turn: the launch worker is held
    until the request has been selected, or a fast launch would let its
    commit share the selecting turn and add both barrier appends to the count.
    """
    manager = volume_manager
    context = weft_harness.context
    ready, release = tmp_path / "ready", tmp_path / "release"
    selected = threading.Event()
    launch_process = manager_mod.launch_task_process

    def launch_after_selection(*args: Any, **kwargs: Any) -> Any:
        assert selected.wait(timeout=30.0), "selection turn never finished"
        return launch_process(*args, **kwargs)

    monkeypatch.setattr(manager_mod, "launch_task_process", launch_after_selection)
    tid = _submit_blocked(weft_harness, ready, release)
    _drive(manager, lambda: tid in manager._active_child_launches)
    assert tid not in manager._child_processes
    selected.set()
    commit_turn_appends: int | None = None
    deadline = time.monotonic() + 60.0
    while commit_turn_appends is None:
        assert time.monotonic() < deadline, "launch result was never committed"
        before = _checkpoint_rows(weft_harness, manager.tid)
        manager.process_once()
        if tid in manager._child_processes:
            commit_turn_appends = _checkpoint_rows(weft_harness, manager.tid) - before
        else:
            manager.wait_for_activity(timeout=0.05)
    assert commit_turn_appends == 1
    assert tid not in manager._scheduler_undurable
    pid = manager._child_processes[tid].process.pid
    receipt = _newest_checkpoint(weft_harness, manager.tid)["scheduler_frontiers"][
        "residents"
    ][tid]
    assert pid in [process["pid"] for process in receipt["host_processes"]]

    # Wait for snapshot handoff: the exact reservation is acknowledged and its
    # reference leaves the resident.
    _drive(
        manager,
        lambda: (
            ready.exists()
            and not manager._startup_feedback
            and "reserved_queue" not in manager._scheduler_residents[tid]
        ),
    )
    task_snapshot = read_task_state_snapshot(context, tid)
    assert task_snapshot is not None and "projection" in task_snapshot[1]
    # The handoff turn adopted the task's own snapshot and published once; the
    # checkpoint carries only custody fields, the task-authored ones stay in
    # the task's queue.
    checkpoint = _newest_checkpoint(weft_harness, manager.tid)
    resident = checkpoint["scheduler_frontiers"]["residents"][tid]
    assert "projection" in manager._scheduler_residents[tid]
    assert set(resident) == set(MANAGER_CHECKPOINT_RESIDENT_FIELDS) - _CUSTODY_FIELDS
    assert pid in [process["pid"] for process in resident["host_processes"]]

    # The predecessor stops answering for its identity; a successor rebuilds
    # the live frontier from that slim checkpoint and the task's own snapshot.
    checkpoint["runtime_handle"]["observations"]["host_processes"][0][
        "create_time"
    ] -= 100
    manager._unregister_manager(status="stopped")
    with context.queue(task_state_queue_name(manager.tid), persistent=True) as state:
        state.write(json.dumps(checkpoint))
    successor = _new_manager(weft_harness, "volume.successor")
    try:
        successor._refresh_scheduler_evidence()
        assert successor._admission_bootstrapped
        assert tid in successor._scheduler_residents
        assert tid not in successor._held_launch_feedback
        assert successor._family_scheduler.frontiers == ((tid,),)
        adopted = _newest_checkpoint(weft_harness, successor.tid)[
            "scheduler_frontiers"
        ]["residents"][tid]
        assert pid in [process["pid"] for process in adopted["host_processes"]]
        assert set(adopted) <= set(MANAGER_CHECKPOINT_RESIDENT_FIELDS)
    finally:
        release.touch()
        successor.stop(join=False)
        successor.cleanup()


def test_pre_handoff_checkpoint_publishes_every_manager_authored_field(
    volume_manager: Manager, weft_harness: WeftTestHarness
) -> None:
    """Every custody field the Manager records is in the published resident."""
    manager = volume_manager
    request = _reserve_and_checkpoint(manager, weft_harness)
    tid = request.child_spec.tid
    assert tid is not None
    manager._checkpoint_spawned_admission(
        _ManagerChildLaunchResult(request, multiprocessing.current_process())
    )
    resident = _newest_checkpoint(weft_harness, manager.tid)["scheduler_frontiers"][
        "residents"
    ][tid]
    assert set(resident) == set(manager._scheduler_residents[tid])
    assert set(resident) == set(MANAGER_CHECKPOINT_RESIDENT_FIELDS)


def test_undurable_receipt_publishes_before_the_turn_dispatches(
    volume_manager: Manager,
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A receipt left undurable by a failed append publishes before dispatch.

    A turn's other checkpoint changes wait for the turn's end, but an
    undurable receipt holds ordinary dispatch, so it publishes first and the
    same turn dispatches the waiting request [MA-1.8].
    """
    manager = volume_manager
    ctx = weft_harness.context
    manager._refresh_scheduler_evidence()
    request = _reserve_and_checkpoint(manager, weft_harness)
    tid = request.child_spec.tid
    assert tid is not None
    state_name = task_state_queue_name(manager.tid)
    original_write = Queue.write

    def fail_checkpoint(self: Queue, *args: Any, **kwargs: Any) -> Any:
        if self.name == state_name:
            raise BrokerError("injected checkpoint write failure")
        return original_write(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Queue, "write", fail_checkpoint)
        manager._checkpoint_spawned_admission(
            _ManagerChildLaunchResult(request, multiprocessing.current_process())
        )
    assert tid in manager._scheduler_undurable
    assert manager._scheduler_checkpoint_dirty
    # Isolate the custody gate from the startup window.
    manager._release_startup_feedback(tid)
    waiting = str(
        submit_spawn_request(
            ctx.broker_target,
            taskspec={
                "name": "checkpoint-volume-waiting",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            },
            work_payload="waiting",
            config=ctx.config,
            inherited_weft_context=str(ctx.root),
        )
    )
    weft_harness.register_tid(waiting)
    # Keep periodic reconciliation out of the measured turn, so only the
    # pre-dispatch barrier can publish the receipt, however long setup took.
    manager._admission_refresh_after = time.monotonic() + 60.0
    manager.process_once()
    assert tid not in manager._scheduler_undurable
    assert waiting in manager._active_child_launches or (
        waiting in manager._child_processes
    )
    residents = _newest_checkpoint(weft_harness, manager.tid)["scheduler_frontiers"][
        "residents"
    ]
    assert residents[tid]["host_processes"]
