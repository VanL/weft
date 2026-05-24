"""TaskMonitor cleanup policies for retained task-log rows.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from simplebroker.ext import BrokerError
from weft._constants import (
    TASK_LOG_START_EVENTS,
    TASK_MONITOR_POLICY_RESERVED_DELETE_TERMINAL_PROVEN,
    TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE,
    TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START,
    TASK_MONITOR_POLICY_TASK_LOG_DELETE_CLAIMED,
    TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED,
    TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START,
    TASK_MONITOR_PONG_DETAIL_LIMIT,
    TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.context import WeftContext
from weft.core.monitor.policies.reserved import terminal_reserved_candidates
from weft.core.monitor.policies.types import CleanupPolicyRun
from weft.core.monitor.progress import PolicyProgress
from weft.core.monitor.task_log_collation import (
    CollatedMessageGroup,
    is_terminal_task_log,
)
from weft.core.monitor.task_log_scanner import (
    TaskLogFamilySelection,
    TaskLogScanWindow,
    select_task_log_family_groups,
)
from weft.core.pruning.models import (
    AppliedCleanupCandidate,
    CleanupCandidate,
    cleanup_candidate_from_row,
    cleanup_policy_stats,
    cleanup_queue_stats,
)
from weft.core.pruning.policies import malformed_row_candidates, older_than_candidates
from weft.core.pruning.policies.older_than import OlderThanSelection
from weft.core.queue_window import (
    DecodedQueueWindowRow,
    QueueWindowRow,
    payload_string,
    scan_queue_window,
)

ApplyCandidates = Callable[
    [Sequence[CleanupCandidate]],
    tuple[AppliedCleanupCandidate, ...],
]


class PeekRowsIncludingClaimed(Protocol):
    """Callable interface for broker claimed-row peeks."""

    def __call__(
        self,
        broker: Any,
        queue_name: str,
        *,
        limit: int,
    ) -> tuple[QueueWindowRow, ...]:
        """Peek queue rows including claimed rows."""


class ScanQueueWindow(Protocol):
    """Callable interface for broker queue window scans."""

    def __call__(
        self,
        ctx: WeftContext,
        queue_name: str,
        *,
        limit: int,
    ) -> Sequence[QueueWindowRow]:
        """Scan one queue window."""


def task_log_candidates(
    ctx: WeftContext,
    scan_window: TaskLogScanWindow,
    *,
    batch_size: int,
    min_age_seconds: float,
    reserved_cleanup_enabled: bool,
    now_ns: int,
    exclude_tids: set[str],
    apply_candidates: ApplyCandidates,
    peek_rows_including_claimed_fn: PeekRowsIncludingClaimed | None = None,
    scan_queue_window_fn: ScanQueueWindow | None = None,
) -> CleanupPolicyRun:
    """Select and optionally apply one ordered task-log cleanup-policy run."""

    rows = scan_window.rows
    applied: list[AppliedCleanupCandidate] = []
    policy_stats = []
    policy_progress: list[PolicyProgress] = []
    remaining = batch_size
    malformed_candidates = select_malformed_task_log_candidates(
        rows,
        limit=remaining,
    )
    candidates = list(malformed_candidates)
    applied.extend(apply_candidates(malformed_candidates))
    claimed = {candidate.message_id for candidate in candidates}
    remaining = max(0, batch_size - len(candidates))
    policy_stats.append(
        cleanup_policy_stats(
            WEFT_GLOBAL_LOG_QUEUE,
            policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED,
            scanned=scan_window.scanned,
            candidates=malformed_candidates,
            stop_reason=None,
        )
    )
    policy_progress.append(
        PolicyProgress(
            policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED,
            domain=WEFT_GLOBAL_LOG_QUEUE,
            scanned=scan_window.scanned,
            selected=len(malformed_candidates),
            waypoint_reached=(
                scan_window.scan_limit_reached and bool(malformed_candidates)
            )
            or len(malformed_candidates) >= batch_size,
            base_reached=(
                not scan_window.scan_limit_reached and not malformed_candidates
            ),
            reason_counts=Counter(
                candidate.reason for candidate in malformed_candidates
            ),
        )
    )
    claimed_scan_skipped = remaining <= 0
    claimed_total = (
        queue_claimed_count(ctx, WEFT_GLOBAL_LOG_QUEUE)
        if not claimed_scan_skipped
        else None
    )
    if claimed_total is not None and claimed_total > 0:
        claimed_rows = scan_claimed_task_log_rows(
            ctx,
            WEFT_GLOBAL_LOG_QUEUE,
            limit=remaining,
            peek_rows_including_claimed_fn=peek_rows_including_claimed_fn,
            scan_queue_window_fn=scan_queue_window_fn,
        )
        claimed_task_log_candidates = select_claimed_task_log_candidates(
            claimed_rows,
            limit=remaining,
        )
    else:
        claimed_rows = ()
        claimed_task_log_candidates = []
    candidates.extend(claimed_task_log_candidates)
    applied.extend(apply_candidates(claimed_task_log_candidates))
    claimed.update(candidate.message_id for candidate in claimed_task_log_candidates)
    remaining = max(0, batch_size - len(candidates))
    claimed_stop_reason = (
        TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED
        if claimed_total is not None
        and claimed_total > len(claimed_task_log_candidates)
        else None
    )
    policy_stats.append(
        cleanup_policy_stats(
            WEFT_GLOBAL_LOG_QUEUE,
            policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_CLAIMED,
            scanned=len(claimed_rows),
            candidates=claimed_task_log_candidates,
            stop_reason=claimed_stop_reason,
        )
    )
    policy_progress.append(
        PolicyProgress(
            policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_CLAIMED,
            domain=WEFT_GLOBAL_LOG_QUEUE,
            scanned=len(claimed_rows),
            selected=len(claimed_task_log_candidates),
            source_total=claimed_total,
            waypoint_reached=claimed_stop_reason is not None or claimed_scan_skipped,
            base_reached=claimed_total == 0,
            reason_counts=Counter(
                candidate.reason for candidate in claimed_task_log_candidates
            ),
        )
    )

    family_selection = select_task_log_family_groups(
        rows,
        now_ns=now_ns,
        min_age_seconds=min_age_seconds,
        exclude_tids=exclude_tids,
        claimed_ids=claimed,
        selection_limit=remaining,
    )
    complete_lifecycle_summaries = family_selection.complete_lifecycle_groups
    terminal_without_start_summaries = family_selection.terminal_without_start_groups
    complete_lifecycle_candidates = [
        candidate
        for group in complete_lifecycle_summaries
        for candidate in task_log_candidates_from_collated_group(group)
    ]
    candidates.extend(complete_lifecycle_candidates)
    applied.extend(apply_candidates(complete_lifecycle_candidates))
    claimed.update(candidate.message_id for candidate in complete_lifecycle_candidates)
    remaining = max(0, batch_size - len(candidates))
    policy_stats.append(
        cleanup_policy_stats(
            WEFT_GLOBAL_LOG_QUEUE,
            policy=TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE,
            scanned=scan_window.scanned,
            candidates=complete_lifecycle_candidates,
            stop_reason=family_selection.stop_reason,
        )
    )
    complete_deferred = sum(
        1 for family in family_selection.skipped_open_families if family.start_rows > 0
    )
    policy_progress.append(
        PolicyProgress(
            policy=TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE,
            domain=WEFT_GLOBAL_LOG_QUEUE,
            scanned=scan_window.scanned,
            selected=len(complete_lifecycle_candidates),
            deferred=complete_deferred,
            waypoint_reached=(
                family_selection.stop_reason is not None
                or (
                    scan_window.scan_limit_reached
                    and bool(complete_lifecycle_candidates)
                )
            ),
            base_reached=(
                not scan_window.scan_limit_reached
                and family_selection.stop_reason is None
                and not complete_lifecycle_candidates
            ),
            reason_counts=Counter(
                candidate.reason for candidate in complete_lifecycle_candidates
            ),
        )
    )

    terminal_without_start_candidates = [
        candidate
        for group in terminal_without_start_summaries
        for candidate in task_log_candidates_from_collated_group(group)
    ]
    candidates.extend(terminal_without_start_candidates)
    applied.extend(apply_candidates(terminal_without_start_candidates))
    claimed.update(
        candidate.message_id for candidate in terminal_without_start_candidates
    )
    remaining = max(0, batch_size - len(candidates))
    policy_stats.append(
        cleanup_policy_stats(
            WEFT_GLOBAL_LOG_QUEUE,
            policy=TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START,
            scanned=scan_window.scanned,
            candidates=terminal_without_start_candidates,
            stop_reason=None,
        )
    )
    policy_progress.append(
        PolicyProgress(
            policy=TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START,
            domain=WEFT_GLOBAL_LOG_QUEUE,
            scanned=scan_window.scanned,
            selected=len(terminal_without_start_candidates),
            waypoint_reached=(
                scan_window.scan_limit_reached
                and bool(terminal_without_start_candidates)
            )
            or len(terminal_without_start_candidates) >= batch_size,
            base_reached=(
                not scan_window.scan_limit_reached
                and not terminal_without_start_candidates
            ),
            reason_counts=Counter(
                candidate.reason for candidate in terminal_without_start_candidates
            ),
        )
    )

    old_rows = select_old_unstarted_task_log_candidates(
        rows,
        now_ns=now_ns,
        min_age_seconds=min_age_seconds,
        claimed_ids=claimed,
        exclude_tids=exclude_tids,
    )
    old_task_log_candidates = tuple(old_rows.candidates[:remaining])
    old_stop_reason = old_rows.stop_reason
    if len(old_rows.candidates) > len(old_task_log_candidates):
        old_stop_reason = TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED
    candidates.extend(old_task_log_candidates)
    applied.extend(apply_candidates(old_task_log_candidates))
    remaining = max(0, batch_size - len(candidates))
    policy_stats.append(
        cleanup_policy_stats(
            WEFT_GLOBAL_LOG_QUEUE,
            policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START,
            scanned=scan_window.scanned,
            candidates=old_task_log_candidates,
            stop_reason=old_stop_reason,
        )
    )
    old_deferred = len(family_selection.skipped_open_families)
    policy_progress.append(
        PolicyProgress(
            policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START,
            domain=WEFT_GLOBAL_LOG_QUEUE,
            scanned=scan_window.scanned,
            selected=len(old_task_log_candidates),
            deferred=old_deferred,
            waypoint_reached=(
                old_stop_reason == TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED
                or (
                    scan_window.scan_limit_reached
                    and old_stop_reason != "first_task_log_too_young"
                    and bool(old_task_log_candidates)
                )
            ),
            base_reached=(
                old_stop_reason == "first_task_log_too_young"
                or (not scan_window.scan_limit_reached and not old_task_log_candidates)
            ),
            reason_counts=Counter(
                candidate.reason for candidate in old_task_log_candidates
            ),
        )
    )
    stop_reason = (
        claimed_stop_reason
        or family_selection.stop_reason
        or old_stop_reason
        or scan_window.stop_reason
    )
    collated_summaries = (
        *complete_lifecycle_summaries,
        *terminal_without_start_summaries,
    )
    reserved_errors: tuple[str, ...] = ()
    if reserved_cleanup_enabled and remaining > 0:
        try:
            (
                reserved_candidates,
                reserved_stats,
                reserved_policy_stats,
                reserved_policy_progress,
            ) = terminal_reserved_candidates(
                ctx,
                collated_summaries,
                now_ns=now_ns,
                min_age_seconds=min_age_seconds,
                selection_limit=remaining,
                scan_queue_window_fn=scan_queue_window_fn,
            )
        except (BrokerError, OSError, RuntimeError, ValueError) as exc:
            reserved_candidates = []
            reserved_stats = ()
            reserved_policy_stats = ()
            reserved_policy_progress = (
                PolicyProgress(
                    policy=TASK_MONITOR_POLICY_RESERVED_DELETE_TERMINAL_PROVEN,
                    domain="task_reserved_queues",
                    blocked_reason=str(exc),
                ),
            )
            reserved_errors = (f"failed to scan terminal reserved queues: {exc}",)
    else:
        reserved_candidates = []
        reserved_stats = ()
        reserved_policy_stats = ()
        reserved_policy_progress = ()
    candidates.extend(reserved_candidates)
    applied.extend(apply_candidates(reserved_candidates))
    task_log_candidate_rows = [
        candidate
        for candidate in candidates
        if candidate.queue == WEFT_GLOBAL_LOG_QUEUE
    ]
    task_log_stats = cleanup_queue_stats(
        WEFT_GLOBAL_LOG_QUEUE,
        scanned=scan_window.scanned + len(claimed_rows),
        candidates=task_log_candidate_rows,
        stop_reason=stop_reason,
        collated_tasks=tuple(collated_summaries),
        metadata=task_log_scan_metadata(scan_window, family_selection),
    )
    return CleanupPolicyRun(
        candidates=tuple(candidates),
        applied_candidates=tuple(applied),
        queue_stats=(task_log_stats, *reserved_stats),
        policy_stats=(*policy_stats, *reserved_policy_stats),
        policy_progress=(*policy_progress, *reserved_policy_progress),
        errors=reserved_errors,
    )


def select_malformed_task_log_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    limit: int,
) -> list[CleanupCandidate]:
    """Select malformed task-log rows up to the remaining policy limit."""

    return malformed_row_candidates(
        rows,
        policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED,
        candidate_class="malformed_task_log",
    )[:limit]


def queue_claimed_count(ctx: WeftContext, queue_name: str) -> int:
    """Return broker claimed count for a queue."""

    queue = ctx.queue(queue_name, persistent=False)
    try:
        return int(queue.stats().claimed)
    finally:
        queue.close()


def scan_claimed_task_log_rows(
    ctx: WeftContext,
    queue_name: str,
    *,
    limit: int,
    peek_rows_including_claimed_fn: PeekRowsIncludingClaimed | None = None,
    scan_queue_window_fn: ScanQueueWindow | None = None,
) -> tuple[QueueWindowRow, ...]:
    """Return claimed task-log rows by comparing all rows to pending rows."""

    if limit <= 0:
        return ()
    peek_rows = (
        peek_rows_including_claimed
        if peek_rows_including_claimed_fn is None
        else peek_rows_including_claimed_fn
    )
    scan_window = (
        scan_queue_window if scan_queue_window_fn is None else scan_queue_window_fn
    )
    with ctx.broker() as broker:
        all_rows = peek_rows(
            broker,
            queue_name,
            limit=limit + queue_pending_count(ctx, queue_name),
        )
    pending_ids = {
        row.message_id
        for row in scan_window(ctx, queue_name, limit=max(1, len(all_rows)))
    }
    claimed_rows = [row for row in all_rows if row.message_id not in pending_ids]
    return tuple(claimed_rows[:limit])


def queue_pending_count(ctx: WeftContext, queue_name: str) -> int:
    """Return broker pending count for a queue."""

    queue = ctx.queue(queue_name, persistent=False)
    try:
        return int(queue.stats().pending)
    finally:
        queue.close()


def peek_rows_including_claimed(
    broker: Any,
    queue_name: str,
    *,
    limit: int,
) -> tuple[QueueWindowRow, ...]:
    """Peek rows without filtering claimed rows using the broker backend hook."""

    retrieve = getattr(broker, "_retrieve", None)
    if not callable(retrieve):
        raise RuntimeError("broker client cannot retrieve claimed rows")
    raw_rows = retrieve(
        queue_name,
        operation="peek",
        limit=max(1, limit),
        require_unclaimed=False,
    )
    return tuple(
        QueueWindowRow(
            queue=queue_name,
            body=body if isinstance(body, str) else str(body),
            message_id=int(message_id),
        )
        for body, message_id in raw_rows
    )


def select_claimed_task_log_candidates(
    rows: Sequence[QueueWindowRow],
    *,
    limit: int,
) -> list[CleanupCandidate]:
    """Select claimed task-log rows for exact cleanup."""

    candidates: list[CleanupCandidate] = []
    for row in rows[:limit]:
        decoded = decode_task_log_row(row)
        candidates.append(
            cleanup_candidate_from_row(
                row,
                policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_CLAIMED,
                candidate_class="claimed_task_log",
                reason="claimed_task_log_row",
                tid=decoded.tid,
                payload=decoded.payload,
            )
        )
    return candidates


def task_log_candidates_from_collated_group(
    group: CollatedMessageGroup,
) -> list[CleanupCandidate]:
    """Build exact-delete candidates for one collated task-log group."""

    candidate_class, reason = collated_task_log_candidate_class(group)
    policy = collated_task_log_policy(group)
    return [
        cleanup_candidate_from_row(
            row.raw,
            policy=policy,
            candidate_class=candidate_class,
            reason=reason,
            tid=group.tid,
            payload=row.payload,
        )
        for row in group.message_rows
    ]


def task_log_scan_metadata(
    scan_window: TaskLogScanWindow,
    family_selection: TaskLogFamilySelection,
) -> dict[str, Any]:
    """Return JSON-safe task-log cleanup scan metadata."""

    return {
        "scan": scan_window.to_summary(),
        "family_selection": family_selection.to_summary(
            skipped_open_family_limit=TASK_MONITOR_PONG_DETAIL_LIMIT,
        ),
    }


def select_old_unstarted_task_log_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    now_ns: int,
    min_age_seconds: float,
    claimed_ids: set[int],
    exclude_tids: set[str],
) -> OlderThanSelection:
    """Select old task-log rows that do not belong to an open started family."""

    protected_tids = open_started_task_log_tids(
        rows,
        claimed_ids=claimed_ids,
        exclude_tids=exclude_tids,
    )
    return older_than_candidates(
        rows,
        policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START,
        now_ns=now_ns,
        min_age_seconds=min_age_seconds,
        candidate_class="old_task_log",
        reason="older_than_task_log_cleanup_min_age",
        stop_reason="first_task_log_too_young",
        claimed_ids=claimed_ids,
        exclude_tids=exclude_tids | protected_tids,
    )


def decode_task_log_row(row: QueueWindowRow) -> DecodedQueueWindowRow:
    """Decode one task-log row without task-log schema validation."""

    try:
        payload = json.loads(row.body)
    except json.JSONDecodeError:
        return DecodedQueueWindowRow(
            raw=row,
            payload=None,
            malformed_reason="invalid_json",
        )
    if not isinstance(payload, dict):
        return DecodedQueueWindowRow(
            raw=row,
            payload=None,
            malformed_reason="json_not_object",
        )
    return DecodedQueueWindowRow(raw=row, payload=payload)


def collated_task_log_candidate_class(
    group: CollatedMessageGroup,
) -> tuple[str, str]:
    """Return candidate class and reason for one collated task-log group."""

    if collated_group_has_visible_start(group):
        return "collated_terminal_task_log", "anchor_tid_terminal_event_found_in_window"
    return (
        "truncated_terminal_task_log",
        "terminal_event_without_visible_start_in_window",
    )


def collated_task_log_policy(group: CollatedMessageGroup) -> str:
    """Return cleanup policy name for one collated task-log group."""

    if collated_group_has_visible_start(group):
        return TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE
    return TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START


def collated_group_has_visible_start(group: CollatedMessageGroup) -> bool:
    """Return whether a collated group includes a visible start event."""

    return any(
        payload_string(row.payload, "event") in TASK_LOG_START_EVENTS
        for row in group.message_rows
    )


def open_started_task_log_tids(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    claimed_ids: set[int],
    exclude_tids: set[str],
) -> set[str]:
    """Return TIDs with visible start rows and no terminal rows in a window."""

    starts: set[str] = set()
    terminals: set[str] = set()
    for row in rows:
        if row.raw.message_id in claimed_ids or row.malformed_reason is not None:
            continue
        tid = row.tid
        if tid is None or tid in exclude_tids:
            continue
        if payload_string(row.payload, "event") in TASK_LOG_START_EVENTS:
            starts.add(tid)
        if is_terminal_task_log(row.payload):
            terminals.add(tid)
    return starts - terminals
