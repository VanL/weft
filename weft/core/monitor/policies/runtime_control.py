"""TaskMonitor policies for task-local runtime queue cleanup.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13]
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    STALE_SERVICE_OWNER_DISPOSITION_REASONS,
    TASK_MONITOR_RUNTIME_CLEANUP_SLICE_ORDER,
)
from weft.core.monitor.policies.dead_task import (
    dead_task_queue_cleanup_plan,
    select_dead_task_tids_from_queue_names,
    standard_dead_task_retention_queue_names,
    standard_task_queue_identity,
)
from weft.core.monitor.progress import PolicyProgress
from weft.core.monitor.store import MonitorTaskCollationRecord
from weft.core.queue_window import is_old_enough

RuntimeCleanupSliceKind = Literal["terminal_control", "reserved", "dead_tid"]


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
    dead_tids_deferred_retention: int = 0
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
    policy_progress: tuple[PolicyProgress, ...] = ()
    family_limit_hit: bool = False
    deadline_hit: bool = False
    next_slice_kind: RuntimeCleanupSliceKind | None = None

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
            "dead_tids_deferred_retention": self.dead_tids_deferred_retention,
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
            "policy_progress": [
                progress.to_summary() for progress in self.policy_progress
            ],
            "family_limit_hit": self.family_limit_hit,
            "deadline_hit": self.deadline_hit,
            "next_slice_kind": self.next_slice_kind,
        }


@dataclass(frozen=True, slots=True)
class TerminalTaskRuntimeQueueCleanupPlan:
    """Policy-selected task-local runtime queues for terminal cleanup."""

    queue_names: tuple[str, ...]
    control_queue_names: tuple[str, str]
    inbox_queue_names: tuple[str, ...]
    outbox_queue_names: tuple[str, ...]
    retention_eligible: bool


@dataclass(frozen=True, slots=True)
class RuntimeReservedCleanupSelection:
    """Policy-selected reserved queue candidates."""

    queue_names: tuple[str, ...] = ()
    skipped_active: int = 0
    skipped_not_ready: int = 0
    pending: bool = False
    deadline_hit: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeDeadTaskCleanupSelection:
    """Policy-selected dead-task queue cleanup candidates."""

    tids: tuple[str, ...] = ()
    discovered_tids: int = 0
    skipped_live: int = 0
    skipped_too_young: int = 0
    skipped_monitor_records: int = 0
    deferred_retention: int = 0
    pending: bool = False
    deadline_hit: bool = False


def next_runtime_cleanup_slice_kind(
    current: RuntimeCleanupSliceKind,
) -> RuntimeCleanupSliceKind | None:
    """Return the next discrete runtime-cleanup slice after ``current``."""

    try:
        index = TASK_MONITOR_RUNTIME_CLEANUP_SLICE_ORDER.index(current)
    except ValueError:  # pragma: no cover - type checkers prevent this.
        return None
    next_index = index + 1
    if next_index >= len(TASK_MONITOR_RUNTIME_CLEANUP_SLICE_ORDER):
        return None
    return cast(
        RuntimeCleanupSliceKind,
        TASK_MONITOR_RUNTIME_CLEANUP_SLICE_ORDER[next_index],
    )


def runtime_cleanup_queue_discovery_due(
    *,
    has_terminal_records: bool,
    previous_queue_cleanup_pending: bool,
    queue_discovery_due_monotonic: float,
    monotonic_now: float,
) -> bool:
    """Return whether queue-name discovery slices should run now."""

    return (
        has_terminal_records
        or previous_queue_cleanup_pending
        or monotonic_now >= queue_discovery_due_monotonic
    )


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


def stale_service_owner_runtime_queue_cleanup_plan(
    record: MonitorTaskCollationRecord,
) -> TerminalTaskRuntimeQueueCleanupPlan | None:
    """Return standard control queues for a disposed stale service owner."""

    if record.disposition_reason not in STALE_SERVICE_OWNER_DISPOSITION_REASONS:
        return None
    if not record.tid.isdigit():
        return None
    if not record.service_classification().is_service_record:
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
    return TerminalTaskRuntimeQueueCleanupPlan(
        queue_names=(expected_ctrl_in, expected_ctrl_out),
        control_queue_names=(expected_ctrl_in, expected_ctrl_out),
        inbox_queue_names=(),
        outbox_queue_names=(),
        retention_eligible=False,
    )


def reserved_queue_tid(queue_name: str) -> str | None:
    """Return the TID encoded in a standard task-local reserved queue name."""

    prefix = "T"
    suffix = f".{QUEUE_RESERVED_SUFFIX}"
    if not queue_name.startswith(prefix) or not queue_name.endswith(suffix):
        return None
    tid = queue_name[len(prefix) : -len(suffix)]
    return tid if tid.isdigit() else None


def reserved_queue_tids(queue_names: Iterable[str]) -> tuple[str, ...]:
    """Return unique TIDs encoded in standard task-local reserved queues."""

    return tuple(
        dict.fromkeys(
            tid
            for queue_name in queue_names
            if (tid := reserved_queue_tid(queue_name)) is not None
        )
    )


def select_runtime_reserved_cleanup_candidates(
    *,
    now_ns: int,
    retention_seconds: float,
    limit: int,
    active_tids: set[str],
    queue_names: Sequence[str],
    task_record: Callable[[str], MonitorTaskCollationRecord | None],
    deadline_reached: Callable[[], bool],
) -> RuntimeReservedCleanupSelection:
    """Select reserved queues with Monitor proof, without deleting them."""

    if limit <= 0:
        return RuntimeReservedCleanupSelection(pending=bool(queue_names))

    selected_queue_names: list[str] = []
    skipped_active = 0
    skipped_not_ready = 0
    pending = False
    deadline_hit = False
    for queue_name in queue_names:
        if len(selected_queue_names) >= limit:
            pending = True
            break
        if deadline_reached():
            pending = True
            deadline_hit = True
            break
        tid = reserved_queue_tid(queue_name)
        if tid is None:
            continue
        if tid in active_tids:
            skipped_active += 1
            continue
        record = task_record(tid)
        ready = False
        if record is not None:
            ready = (
                record.raw_deleted_at_ns is not None
                or record.disposition_at_ns is not None
                or (record.terminal_seen and record.summary_emitted_at_ns is not None)
            )
        else:
            ready = is_old_enough(int(tid), now_ns, retention_seconds)
        if not ready:
            skipped_not_ready += 1
            continue
        selected_queue_names.append(queue_name)

    return RuntimeReservedCleanupSelection(
        queue_names=tuple(selected_queue_names),
        skipped_active=skipped_active,
        skipped_not_ready=skipped_not_ready,
        pending=pending,
        deadline_hit=deadline_hit,
    )


def runtime_dead_task_record_probe_tids(
    queue_names: Iterable[str],
    *,
    now_ns: int,
    min_age_seconds: float,
    retention_seconds: float,
    active_tids: set[str],
) -> tuple[str, ...]:
    """Return actionable queue-derived dead TIDs needing Monitor-record probes."""

    queue_name_tuple = tuple(queue_names)
    dead_selection = select_dead_task_tids_from_queue_names(
        queue_name_tuple,
        live_tids=active_tids,
        now_ns=now_ns,
        min_age_seconds=min_age_seconds,
    )
    queue_name_set = set(queue_name_tuple)
    probe_tids: list[str] = []
    for tid in dead_selection.selected_tids:
        plan = dead_task_queue_cleanup_plan(
            tid,
            now_ns=now_ns,
            retention_seconds=retention_seconds,
        )
        if any(queue_name in queue_name_set for queue_name in plan.queue_names):
            probe_tids.append(tid)
    return tuple(probe_tids)


def _task_queue_suffixes_by_tid(
    queue_names: Iterable[str],
) -> dict[str, set[str]]:
    """Return standard task-local queue suffixes grouped by TID."""

    grouped: dict[str, set[str]] = {}
    for queue_name in queue_names:
        identity = standard_task_queue_identity(queue_name)
        if identity is None:
            continue
        tid, suffix = identity
        grouped.setdefault(tid, set()).add(suffix)
    return grouped


def _has_retention_deferred_only_work(
    *,
    tid: str,
    suffixes: set[str],
    now_ns: int,
    retention_seconds: float,
) -> bool:
    """Return whether a TID has only future retention-gated cleanup work."""

    if is_old_enough(int(tid), now_ns, retention_seconds):
        return False
    retention_suffixes = {QUEUE_OUTBOX_SUFFIX, QUEUE_RESERVED_SUFFIX}
    stale_suffixes = {QUEUE_CTRL_IN_SUFFIX, QUEUE_CTRL_OUT_SUFFIX, QUEUE_INBOX_SUFFIX}
    return bool(suffixes & retention_suffixes) and not bool(suffixes & stale_suffixes)


def select_runtime_dead_task_cleanup_candidates(
    queue_names: Iterable[str],
    *,
    now_ns: int,
    min_age_seconds: float,
    retention_seconds: float,
    limit: int,
    active_tids: set[str],
    task_record: Callable[[str], MonitorTaskCollationRecord | None],
    deadline_reached: Callable[[], bool],
) -> RuntimeDeadTaskCleanupSelection:
    """Select actionable dead-task queue cleanup candidates.

    Dead-task runtime cleanup is intentionally name-derived and must not mark
    Monitor-store families. If a Monitor collation row exists, terminal or
    reserved cleanup owns that family instead.
    """

    queue_name_tuple = tuple(queue_names)
    dead_selection = select_dead_task_tids_from_queue_names(
        queue_name_tuple,
        live_tids=active_tids,
        now_ns=now_ns,
        min_age_seconds=min_age_seconds,
    )
    queue_name_set = set(queue_name_tuple)
    suffixes_by_tid = _task_queue_suffixes_by_tid(queue_name_tuple)
    selected_tids: list[str] = []
    skipped_monitor_records = 0
    deferred_retention = 0
    pending = False
    deadline_hit = False

    for tid in dead_selection.selected_tids:
        suffixes = suffixes_by_tid.get(tid, set())
        if _has_retention_deferred_only_work(
            tid=tid,
            suffixes=suffixes,
            now_ns=now_ns,
            retention_seconds=retention_seconds,
        ):
            deferred_retention += 1
            continue
        plan = dead_task_queue_cleanup_plan(
            tid,
            now_ns=now_ns,
            retention_seconds=retention_seconds,
        )
        if not any(queue_name in queue_name_set for queue_name in plan.queue_names):
            if not plan.retention_eligible and any(
                queue_name in queue_name_set
                for queue_name in standard_dead_task_retention_queue_names(tid)
            ):
                deferred_retention += 1
            continue
        if task_record(tid) is not None:
            skipped_monitor_records += 1
            continue
        if len(selected_tids) >= limit:
            pending = True
            break
        if deadline_reached():
            pending = True
            deadline_hit = True
            break
        selected_tids.append(tid)

    return RuntimeDeadTaskCleanupSelection(
        tids=tuple(selected_tids),
        discovered_tids=dead_selection.discovered_tids,
        skipped_live=dead_selection.skipped_live,
        skipped_too_young=dead_selection.skipped_too_young,
        skipped_monitor_records=skipped_monitor_records,
        deferred_retention=deferred_retention,
        pending=pending,
        deadline_hit=deadline_hit,
    )
