"""TaskMonitor policies for task-local runtime queue cleanup.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13]
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
)
from weft.core.monitor.store import MonitorTaskCollationRecord
from weft.core.queue_window import is_old_enough


@dataclass(frozen=True, slots=True)
class TaskControlCleanupResult:
    """Cached result for task-local runtime queue cleanup."""

    families_processed: int = 0
    families_disposed: int = 0
    families_retired: int = 0
    queues_deleted: int = 0
    rows_estimated_deleted: int = 0
    skipped_nonstandard: int = 0
    reserved_families_processed: int = 0
    reserved_queues_deleted: int = 0
    reserved_rows_estimated_deleted: int = 0
    reserved_skipped_active: int = 0
    reserved_skipped_not_ready: int = 0
    dead_tids_discovered: int = 0
    dead_tids_processed: int = 0
    dead_tids_skipped_live: int = 0
    dead_tids_skipped_too_young: int = 0
    dead_tids_pending: int = 0
    dead_tid_queues_deleted: int = 0
    dead_tid_rows_estimated_deleted: int = 0
    dead_tid_control_queues_deleted: int = 0
    dead_tid_control_rows_estimated_deleted: int = 0
    dead_tid_inbox_queues_deleted: int = 0
    dead_tid_outbox_queues_deleted: int = 0
    dead_tid_reserved_queues_deleted: int = 0
    dead_tid_log_refs_selected: int = 0
    dead_tid_log_rows_deleted: int = 0
    cleanup_workers_configured: int = 0
    cleanup_jobs_started: int = 0
    cleanup_jobs_completed: int = 0
    cleanup_jobs_pending: int = 0
    cleanup_jobs_by_kind: Mapping[str, int] | None = None
    cleanup_jobs_pending_by_kind: Mapping[str, int] | None = None
    pending: bool = False
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    family_limit_hit: bool = False
    deadline_hit: bool = False

    @property
    def success(self) -> bool:
        """Return whether the cleanup finished without delete errors."""

        return not self.errors

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe cached PONG summary."""

        return {
            "families_processed": self.families_processed,
            "families_disposed": self.families_disposed,
            "families_retired": self.families_retired,
            "queues_deleted": self.queues_deleted,
            "rows_estimated_deleted": self.rows_estimated_deleted,
            "skipped_nonstandard": self.skipped_nonstandard,
            "reserved_families_processed": self.reserved_families_processed,
            "reserved_queues_deleted": self.reserved_queues_deleted,
            "reserved_rows_estimated_deleted": self.reserved_rows_estimated_deleted,
            "reserved_skipped_active": self.reserved_skipped_active,
            "reserved_skipped_not_ready": self.reserved_skipped_not_ready,
            "dead_tids_discovered": self.dead_tids_discovered,
            "dead_tids_processed": self.dead_tids_processed,
            "dead_tids_skipped_live": self.dead_tids_skipped_live,
            "dead_tids_skipped_too_young": self.dead_tids_skipped_too_young,
            "dead_tids_pending": self.dead_tids_pending,
            "dead_tid_queues_deleted": self.dead_tid_queues_deleted,
            "dead_tid_rows_estimated_deleted": (self.dead_tid_rows_estimated_deleted),
            "dead_tid_control_queues_deleted": (self.dead_tid_control_queues_deleted),
            "dead_tid_control_rows_estimated_deleted": (
                self.dead_tid_control_rows_estimated_deleted
            ),
            "dead_tid_inbox_queues_deleted": self.dead_tid_inbox_queues_deleted,
            "dead_tid_outbox_queues_deleted": self.dead_tid_outbox_queues_deleted,
            "dead_tid_reserved_queues_deleted": (self.dead_tid_reserved_queues_deleted),
            "dead_tid_log_refs_selected": self.dead_tid_log_refs_selected,
            "dead_tid_log_rows_deleted": self.dead_tid_log_rows_deleted,
            "cleanup_workers_configured": self.cleanup_workers_configured,
            "cleanup_jobs_started": self.cleanup_jobs_started,
            "cleanup_jobs_completed": self.cleanup_jobs_completed,
            "cleanup_jobs_pending": self.cleanup_jobs_pending,
            "cleanup_jobs_by_kind": dict(self.cleanup_jobs_by_kind or {}),
            "cleanup_jobs_pending_by_kind": dict(
                self.cleanup_jobs_pending_by_kind or {}
            ),
            "pending": self.pending,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "family_limit_hit": self.family_limit_hit,
            "deadline_hit": self.deadline_hit,
        }


@dataclass(frozen=True, slots=True)
class TerminalTaskRuntimeQueueCleanupPlan:
    """Policy-selected task-local runtime queues for terminal cleanup."""

    queue_names: tuple[str, ...]
    control_queue_names: tuple[str, str]
    inbox_queue_names: tuple[str, ...]
    outbox_queue_names: tuple[str, ...]
    retention_eligible: bool


def standard_task_control_queue_pair(tid: str) -> tuple[str, str]:
    """Return the standard task-local control queues for a TID."""

    return f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}", f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}"


def standard_task_control_queue_names(
    record: MonitorTaskCollationRecord,
) -> tuple[str, str] | None:
    """Return standard task-local control queues eligible for cleanup."""

    if record.role == "manager" or not record.tid.isdigit():
        return None
    expected_ctrl_in, expected_ctrl_out = standard_task_control_queue_pair(record.tid)
    io_summary = record.taskspec_summary.get("io")
    if not isinstance(io_summary, Mapping):
        return None
    control = io_summary.get("control")
    if not isinstance(control, Mapping):
        return None
    if (
        control.get("ctrl_in") != expected_ctrl_in
        or control.get("ctrl_out") != expected_ctrl_out
    ):
        return None
    return expected_ctrl_in, expected_ctrl_out


def terminal_task_runtime_queue_cleanup_plan(
    record: MonitorTaskCollationRecord,
    *,
    now_ns: int,
    retention_seconds: float,
) -> TerminalTaskRuntimeQueueCleanupPlan | None:
    """Return standard task-local queues eligible for terminal cleanup."""

    control_queue_names = standard_task_control_queue_names(record)
    if control_queue_names is None:
        return None
    retention_eligible = is_old_enough(int(record.tid), now_ns, retention_seconds)
    inbox_queue_names = (f"T{record.tid}.{QUEUE_INBOX_SUFFIX}",)
    outbox_queue_names = (
        (f"T{record.tid}.{QUEUE_OUTBOX_SUFFIX}",) if retention_eligible else ()
    )
    return TerminalTaskRuntimeQueueCleanupPlan(
        queue_names=(*control_queue_names, *inbox_queue_names, *outbox_queue_names),
        control_queue_names=control_queue_names,
        inbox_queue_names=inbox_queue_names,
        outbox_queue_names=outbox_queue_names,
        retention_eligible=retention_eligible,
    )


def reserved_queue_tid(queue_name: str) -> str | None:
    """Return the TID encoded in a standard task-local reserved queue name."""

    prefix = "T"
    suffix = f".{QUEUE_RESERVED_SUFFIX}"
    if not queue_name.startswith(prefix) or not queue_name.endswith(suffix):
        return None
    tid = queue_name[len(prefix) : -len(suffix)]
    return tid if tid.isdigit() else None
