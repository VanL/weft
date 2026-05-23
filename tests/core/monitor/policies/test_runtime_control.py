from __future__ import annotations

import pytest

from weft.core.monitor.policies.runtime_control import (
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
