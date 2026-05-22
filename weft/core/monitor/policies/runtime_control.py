"""TaskMonitor policies for task-local runtime control queues.

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
    QUEUE_RESERVED_SUFFIX,
)
from weft.core.monitor.store import MonitorTaskCollationRecord


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
            "pending": self.pending,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "family_limit_hit": self.family_limit_hit,
            "deadline_hit": self.deadline_hit,
        }


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


def reserved_queue_tid(queue_name: str) -> str | None:
    """Return the TID encoded in a standard task-local reserved queue name."""

    prefix = "T"
    suffix = f".{QUEUE_RESERVED_SUFFIX}"
    if not queue_name.startswith(prefix) or not queue_name.endswith(suffix):
        return None
    tid = queue_name[len(prefix) : -len(suffix)]
    return tid if tid.isdigit() else None
