from __future__ import annotations

import pytest

from weft._constants import TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS
from weft.core.monitor.policies.runtime_control import (
    runtime_dead_task_record_probe_tids,
    select_runtime_dead_task_cleanup_candidates,
    standard_task_control_queue_names,
    terminal_task_runtime_queue_cleanup_plan,
)
from weft.core.monitor.store import MonitorTaskCollationRecord

pytestmark = [pytest.mark.shared]


def _record(tid: str, *, role: str | None = None) -> MonitorTaskCollationRecord:
    return MonitorTaskCollationRecord(
        context_key="test",
        tid=tid,
        name="sample",
        runner=None,
        parent_tid=None,
        role=role,
        status="completed",
        terminal_seen=True,
        terminal_event="work_completed",
        terminal_status="completed",
        terminal_message_id=int(tid) + 1,
        return_code=0,
        first_message_id=int(tid),
        last_message_id=int(tid) + 1,
        first_seen_at_ns=int(tid),
        last_seen_at_ns=int(tid) + 1,
        started_at_ns=int(tid),
        completed_at_ns=int(tid) + 1,
        taskspec_summary={
            "io": {
                "control": {
                    "ctrl_in": f"T{tid}.ctrl_in",
                    "ctrl_out": f"T{tid}.ctrl_out",
                },
            },
        },
    )


def test_terminal_runtime_cleanup_plan_deletes_stale_queues_before_retention() -> None:
    tid = "1778084345905438801"
    record = _record(tid)

    plan = terminal_task_runtime_queue_cleanup_plan(
        record,
        now_ns=int(tid) + 10_000_000_000,
        retention_seconds=60.0,
    )

    assert plan is not None
    assert plan.queue_names == (
        f"T{tid}.ctrl_in",
        f"T{tid}.ctrl_out",
        f"T{tid}.inbox",
    )
    assert plan.outbox_queue_names == ()
    assert not plan.retention_eligible


def test_terminal_runtime_cleanup_plan_adds_outbox_after_retention() -> None:
    tid = "1778084345905438801"
    record = _record(tid)

    plan = terminal_task_runtime_queue_cleanup_plan(
        record,
        now_ns=int(tid) + 10_000_000_000,
        retention_seconds=1.0,
    )

    assert plan is not None
    assert plan.queue_names == (
        f"T{tid}.ctrl_in",
        f"T{tid}.ctrl_out",
        f"T{tid}.inbox",
        f"T{tid}.outbox",
    )
    assert plan.outbox_queue_names == (f"T{tid}.outbox",)
    assert plan.retention_eligible


def test_terminal_runtime_cleanup_plan_rejects_nonstandard_controls() -> None:
    tid = "1778084345905438801"
    record = _record(tid, role="manager")

    assert standard_task_control_queue_names(record) is None
    assert (
        terminal_task_runtime_queue_cleanup_plan(
            record,
            now_ns=int(tid) + 10_000_000_000,
            retention_seconds=1.0,
        )
        is None
    )


def test_dead_task_selection_treats_deferred_outbox_only_as_base_for_now() -> None:
    now_ns = 1_778_100_000_000_000_000
    base_tid = now_ns - int(
        (TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS + 60.0) * 1e9
    )
    queue_names = tuple(f"T{base_tid - offset}.outbox" for offset in range(25))

    def fail_task_record(tid: str) -> MonitorTaskCollationRecord | None:
        raise AssertionError(f"deferred-only TID should not be probed: {tid}")

    def fail_deadline() -> bool:
        raise AssertionError("deferred-only work should not consult deadline")

    selection = select_runtime_dead_task_cleanup_candidates(
        queue_names,
        now_ns=now_ns,
        min_age_seconds=TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
        retention_seconds=172800.0,
        limit=1,
        active_tids=set(),
        task_record=fail_task_record,
        deadline_reached=fail_deadline,
    )

    assert selection.tids == ()
    assert selection.deferred_retention == len(queue_names)
    assert selection.pending is False
    assert selection.deadline_hit is False


def test_dead_task_record_probe_tids_ignores_retention_deferred_only_queues() -> None:
    now_ns = 1_778_100_000_000_000_000
    deferred_tid = str(
        now_ns - int((TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS + 60.0) * 1e9)
    )
    actionable_tid = str(
        now_ns - int((TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS + 120.0) * 1e9)
    )

    probe_tids = runtime_dead_task_record_probe_tids(
        (
            f"T{deferred_tid}.outbox",
            f"T{deferred_tid}.reserved",
            f"T{actionable_tid}.ctrl_in",
        ),
        now_ns=now_ns,
        min_age_seconds=TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
        retention_seconds=172800.0,
        active_tids=set(),
    )

    assert probe_tids == (actionable_tid,)


def test_dead_task_selection_preserves_actionable_limit_waypoint() -> None:
    now_ns = 1_778_100_000_000_000_000
    base_tid = now_ns - int(
        (TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS + 60.0) * 1e9
    )
    queue_names = tuple(f"T{base_tid - offset}.ctrl_in" for offset in range(3))

    selection = select_runtime_dead_task_cleanup_candidates(
        queue_names,
        now_ns=now_ns,
        min_age_seconds=TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
        retention_seconds=172800.0,
        limit=1,
        active_tids=set(),
        task_record=lambda _tid: None,
        deadline_reached=lambda: False,
    )

    assert selection.tids == (str(base_tid - 2),)
    assert selection.pending is True
    assert selection.deferred_retention == 0
