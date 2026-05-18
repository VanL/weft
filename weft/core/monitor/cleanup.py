"""TaskMonitor-owned cleanup orchestration.

TaskMonitor decides when cleanup runs and whether the cycle is destructive.
Reusable pruning policies live under ``weft.core.pruning`` and task-log
grouping lives in ``weft.core.monitor.task_log_collation``.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from simplebroker.ext import BrokerError
from weft._constants import (
    QUEUE_RESERVED_SUFFIX,
    STATUS_COMPLETED,
    TASK_LOG_START_EVENTS,
    TASK_MONITOR_POLICY_RESERVED_DELETE_TERMINAL_PROVEN,
    TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE,
    TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START,
    TASK_MONITOR_POLICY_TASK_LOG_DELETE_CLAIMED,
    TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED,
    TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START,
    TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED,
    TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN,
    TASK_MONITOR_PONG_DETAIL_LIMIT,
    TASK_MONITOR_TASK_LOG_CLEANUP_SKIPPED_OWNER,
    TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED,
    TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
    TERMINAL_TASK_EVENTS,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS_DEFAULT,
    WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
    WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT_DEFAULT,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext
from weft.core.monitor.task_log_collation import (
    CollatedMessageGroup,
    is_terminal_task_log,
)
from weft.core.monitor.task_log_scanner import (
    GeneratorTaskLogScanner,
    TaskLogFamilySelection,
    TaskLogScanBackend,
    TaskLogScanWindow,
    select_task_log_family_groups,
)
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.core.pruning.models import (
    AppliedCleanupCandidate,
    CleanupCandidate,
    CleanupPolicyStats,
    CleanupQueueStats,
    applied_cleanup_candidate,
    cleanup_candidate_from_row,
    cleanup_policy_stats,
    cleanup_queue_stats,
)
from weft.core.pruning.policies import (
    malformed_row_candidates,
    older_than_candidates,
)
from weft.core.pruning.policies.older_than import OlderThanSelection
from weft.core.queue_window import (
    DecodedQueueWindowRow,
    QueueWindowRow,
    payload_string,
    scan_queue_window,
)


@dataclass(frozen=True, slots=True)
class TaskMonitorCleanupConfig:
    """Configuration for one TaskMonitor cleanup pass."""

    batch_size: int = WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT
    task_log_scan_limit: int = WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT_DEFAULT
    tid_mapping_min_age_seconds: float = (
        TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS
    )
    task_log_min_age_seconds: float = WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS_DEFAULT
    task_log_cleanup_enabled: bool = True
    queues: tuple[str, ...] = (WEFT_TID_MAPPINGS_QUEUE, WEFT_GLOBAL_LOG_QUEUE)
    task_log_scan_backend: TaskLogScanBackend | None = None


@dataclass(frozen=True, slots=True)
class TaskMonitorCleanupResult:
    """Result of one TaskMonitor cleanup pass."""

    success: bool
    candidates: tuple[CleanupCandidate, ...] = ()
    applied_candidates: tuple[AppliedCleanupCandidate, ...] = ()
    queue_stats: tuple[CleanupQueueStats, ...] = ()
    policy_stats: tuple[CleanupPolicyStats, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def processed(self) -> int:
        """Number of selected cleanup candidates."""

        return len(self.candidates)

    @property
    def deleted(self) -> int:
        """Number of rows deleted by this pass."""

        return sum(1 for candidate in self.applied_candidates if candidate.deleted)

    @property
    def reported(self) -> int:
        """Number of candidates reported without deletion."""

        if self.applied_candidates:
            return sum(
                1 for candidate in self.applied_candidates if not candidate.deleted
            )
        return len(self.candidates)

    @property
    def records_scanned(self) -> int:
        """Number of raw broker rows scanned by this pass."""

        return sum(stat.scanned for stat in self.queue_stats)

    def queue_stats_summary(self) -> tuple[dict[str, Any], ...]:
        """Return JSON-safe queue stats for operational logs."""

        return tuple(stat.to_summary() for stat in self.queue_stats)

    def policy_stats_summary(self) -> tuple[dict[str, Any], ...]:
        """Return JSON-safe policy stats for operational logs."""

        return tuple(stat.to_summary() for stat in self.policy_stats)


def run_task_monitor_cleanup(
    ctx: WeftContext,
    config: TaskMonitorCleanupConfig,
    *,
    apply: bool,
    exclude_tids: Iterable[str] = (),
    now_ns: int | None = None,
) -> TaskMonitorCleanupResult:
    """Run one TaskMonitor cleanup pass.

    Spec: [MF-5], [OBS.13], [OBS.16], [OBS.17]
    """

    if config.batch_size <= 0:
        return TaskMonitorCleanupResult(
            success=False,
            errors=("task monitor cleanup batch_size must be positive",),
        )
    if config.task_log_scan_limit <= 0:
        return TaskMonitorCleanupResult(
            success=False,
            errors=("task monitor cleanup task_log_scan_limit must be positive",),
        )

    current_ns = time.time_ns() if now_ns is None else now_ns
    excluded = {tid for tid in exclude_tids if tid}
    candidates: list[CleanupCandidate] = []
    queue_stats: list[CleanupQueueStats] = []
    policy_stats: list[CleanupPolicyStats] = []
    errors: list[str] = []
    task_log_scan_backend = config.task_log_scan_backend or GeneratorTaskLogScanner()

    for queue_name in config.queues:
        try:
            if queue_name == WEFT_GLOBAL_LOG_QUEUE:
                if not config.task_log_cleanup_enabled:
                    queue_stats.append(
                        CleanupQueueStats(
                            queue=queue_name,
                            scanned=0,
                            selected=0,
                            stop_reason=TASK_MONITOR_TASK_LOG_CLEANUP_SKIPPED_OWNER,
                        )
                    )
                    continue
                task_log_window = task_log_scan_backend.scan_window(
                    ctx,
                    queue_name,
                    scan_limit=config.task_log_scan_limit,
                )
                selected, queue_stat_records, policy_stat_records = (
                    _task_log_candidates(
                        ctx,
                        task_log_window,
                        config=config,
                        now_ns=current_ns,
                        exclude_tids=excluded,
                    )
                )
            else:
                rows = scan_queue_window(ctx, queue_name, limit=config.batch_size)
                selected, queue_stat_records, policy_stat_records = (
                    _select_queue_candidates(
                        queue_name,
                        rows,
                        config=config,
                        now_ns=current_ns,
                        exclude_tids=excluded,
                    )
                )
        except (BrokerError, OSError, RuntimeError, ValueError) as exc:
            errors.append(f"failed to scan {queue_name}: {exc}")
            continue
        candidates.extend(selected)
        queue_stats.extend(queue_stat_records)
        policy_stats.extend(policy_stat_records)

    applied: tuple[AppliedCleanupCandidate, ...] = ()
    if apply and candidates:
        applied = tuple(
            apply_exact_prune_candidates(
                ctx,
                candidates,
                apply_result=applied_cleanup_candidate,
            )
        )

    final_queue_stats = tuple(
        _with_apply_counts(queue_stats, applied, report_only=not apply)
    )
    final_policy_stats = tuple(
        _with_policy_apply_counts(policy_stats, applied, report_only=not apply)
    )
    apply_errors = tuple(
        candidate.error for candidate in applied if candidate.error is not None
    )
    return TaskMonitorCleanupResult(
        success=not errors and not apply_errors,
        candidates=tuple(candidates),
        applied_candidates=applied,
        queue_stats=final_queue_stats,
        policy_stats=final_policy_stats,
        errors=(*errors, *apply_errors),
    )


def _select_queue_candidates(
    queue_name: str,
    rows: Sequence[QueueWindowRow],
    *,
    config: TaskMonitorCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
) -> tuple[
    list[CleanupCandidate],
    tuple[CleanupQueueStats, ...],
    tuple[CleanupPolicyStats, ...],
]:
    decoded = tuple(_decode_row(row, queue_name=queue_name) for row in rows)
    if queue_name == WEFT_TID_MAPPINGS_QUEUE:
        candidates, queue_stats, policy_stats = _tid_mapping_candidates(
            decoded,
            config=config,
            now_ns=now_ns,
            exclude_tids=exclude_tids,
        )
        return candidates, (queue_stats,), policy_stats
    return (
        [],
        (
            CleanupQueueStats(
                queue=queue_name,
                scanned=len(rows),
                selected=0,
                stop_reason="unsupported_queue",
            ),
        ),
        (),
    )


def _decode_row(row: QueueWindowRow, *, queue_name: str) -> DecodedQueueWindowRow:
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
    if queue_name == WEFT_TID_MAPPINGS_QUEUE:
        if not _valid_tid_mapping_payload(payload):
            return DecodedQueueWindowRow(
                raw=row,
                payload=payload,
                malformed_reason="invalid_tid_mapping_shape",
            )
    return DecodedQueueWindowRow(raw=row, payload=payload)


def _valid_tid_mapping_payload(payload: Mapping[str, Any]) -> bool:
    full = payload.get("full")
    short = payload.get("short")
    return (
        isinstance(full, str) and bool(full) and isinstance(short, str) and bool(short)
    )


def _tid_mapping_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    config: TaskMonitorCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
) -> tuple[list[CleanupCandidate], CleanupQueueStats, tuple[CleanupPolicyStats, ...]]:
    malformed_candidates = malformed_row_candidates(
        rows,
        policy=TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED,
        candidate_class="malformed_tid_mapping",
    )
    candidates = list(malformed_candidates)
    claimed = {candidate.message_id for candidate in candidates}
    old_rows = older_than_candidates(
        rows,
        policy=TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN,
        now_ns=now_ns,
        min_age_seconds=config.tid_mapping_min_age_seconds,
        candidate_class="old_tid_mapping",
        reason="older_than_tid_mapping_cleanup_min_age",
        stop_reason="first_tid_mapping_too_young",
        claimed_ids=claimed,
        exclude_tids=exclude_tids,
        tid_from_row=lambda payload, _row: payload_string(payload, "full"),
    )
    candidates.extend(old_rows.candidates)
    queue_stats = cleanup_queue_stats(
        WEFT_TID_MAPPINGS_QUEUE,
        scanned=len(rows),
        candidates=candidates,
        stop_reason=old_rows.stop_reason,
    )
    policy_stats = (
        cleanup_policy_stats(
            WEFT_TID_MAPPINGS_QUEUE,
            policy=TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED,
            scanned=len(rows),
            candidates=malformed_candidates,
            stop_reason=None,
        ),
        cleanup_policy_stats(
            WEFT_TID_MAPPINGS_QUEUE,
            policy=TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN,
            scanned=len(rows),
            candidates=old_rows.candidates,
            stop_reason=old_rows.stop_reason,
        ),
    )
    return candidates, queue_stats, policy_stats


def _task_log_candidates(
    ctx: WeftContext,
    scan_window: TaskLogScanWindow,
    *,
    config: TaskMonitorCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
) -> tuple[
    list[CleanupCandidate],
    tuple[CleanupQueueStats, ...],
    tuple[CleanupPolicyStats, ...],
]:
    rows = scan_window.rows
    remaining = config.batch_size
    malformed_candidates = _select_malformed_task_log_candidates(
        rows,
        limit=remaining,
    )
    candidates = list(malformed_candidates)
    claimed = {candidate.message_id for candidate in candidates}
    remaining = max(0, config.batch_size - len(candidates))
    claimed_total = (
        _queue_claimed_count(ctx, WEFT_GLOBAL_LOG_QUEUE) if remaining > 0 else 0
    )
    claimed_rows = _scan_claimed_task_log_rows(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        limit=remaining,
    )
    claimed_task_log_candidates = _select_claimed_task_log_candidates(
        claimed_rows,
        limit=remaining,
    )
    candidates.extend(claimed_task_log_candidates)
    claimed.update(candidate.message_id for candidate in claimed_task_log_candidates)
    remaining = max(0, config.batch_size - len(candidates))
    claimed_stop_reason = (
        TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED
        if claimed_total > len(claimed_task_log_candidates)
        else None
    )

    family_selection = select_task_log_family_groups(
        rows,
        now_ns=now_ns,
        min_age_seconds=config.task_log_min_age_seconds,
        exclude_tids=exclude_tids,
        claimed_ids=claimed,
        selection_limit=remaining,
    )
    complete_lifecycle_summaries = family_selection.complete_lifecycle_groups
    terminal_without_start_summaries = family_selection.terminal_without_start_groups
    complete_lifecycle_candidates = [
        candidate
        for group in complete_lifecycle_summaries
        for candidate in _task_log_candidates_from_collated_group(group)
    ]
    candidates.extend(complete_lifecycle_candidates)
    claimed.update(candidate.message_id for candidate in complete_lifecycle_candidates)
    remaining = max(0, config.batch_size - len(candidates))

    terminal_without_start_candidates = [
        candidate
        for group in terminal_without_start_summaries
        for candidate in _task_log_candidates_from_collated_group(group)
    ]
    candidates.extend(terminal_without_start_candidates)
    claimed.update(
        candidate.message_id for candidate in terminal_without_start_candidates
    )
    remaining = max(0, config.batch_size - len(candidates))

    old_rows = _select_old_unstarted_task_log_candidates(
        rows,
        now_ns=now_ns,
        min_age_seconds=config.task_log_min_age_seconds,
        claimed_ids=claimed,
        exclude_tids=exclude_tids,
    )
    old_task_log_candidates = tuple(old_rows.candidates[:remaining])
    old_stop_reason = old_rows.stop_reason
    if len(old_rows.candidates) > len(old_task_log_candidates):
        old_stop_reason = TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED
    candidates.extend(old_task_log_candidates)
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
    reserved_candidates, reserved_stats, reserved_policy_stats = (
        _terminal_reserved_candidates(
            ctx,
            collated_summaries,
            config=config,
            now_ns=now_ns,
        )
    )
    candidates.extend(reserved_candidates)
    task_log_candidates = [
        candidate
        for candidate in candidates
        if candidate.queue == WEFT_GLOBAL_LOG_QUEUE
    ]
    task_log_stats = cleanup_queue_stats(
        WEFT_GLOBAL_LOG_QUEUE,
        scanned=scan_window.scanned + len(claimed_rows),
        candidates=task_log_candidates,
        stop_reason=stop_reason,
        collated_tasks=tuple(collated_summaries),
        metadata=_task_log_scan_metadata(scan_window, family_selection),
    )
    task_log_policy_stats = (
        cleanup_policy_stats(
            WEFT_GLOBAL_LOG_QUEUE,
            policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED,
            scanned=scan_window.scanned,
            candidates=malformed_candidates,
            stop_reason=None,
        ),
        cleanup_policy_stats(
            WEFT_GLOBAL_LOG_QUEUE,
            policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_CLAIMED,
            scanned=len(claimed_rows),
            candidates=claimed_task_log_candidates,
            stop_reason=claimed_stop_reason,
        ),
        cleanup_policy_stats(
            WEFT_GLOBAL_LOG_QUEUE,
            policy=TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE,
            scanned=scan_window.scanned,
            candidates=complete_lifecycle_candidates,
            stop_reason=family_selection.stop_reason,
        ),
        cleanup_policy_stats(
            WEFT_GLOBAL_LOG_QUEUE,
            policy=TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START,
            scanned=scan_window.scanned,
            candidates=terminal_without_start_candidates,
            stop_reason=None,
        ),
        cleanup_policy_stats(
            WEFT_GLOBAL_LOG_QUEUE,
            policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START,
            scanned=scan_window.scanned,
            candidates=old_task_log_candidates,
            stop_reason=old_stop_reason,
        ),
    )
    return (
        candidates,
        (task_log_stats, *reserved_stats),
        (*task_log_policy_stats, *reserved_policy_stats),
    )


def _select_malformed_task_log_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    limit: int,
) -> list[CleanupCandidate]:
    return malformed_row_candidates(
        rows,
        policy=TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED,
        candidate_class="malformed_task_log",
    )[:limit]


def _queue_claimed_count(ctx: WeftContext, queue_name: str) -> int:
    queue = ctx.queue(queue_name, persistent=False)
    try:
        return int(queue.stats().claimed)
    finally:
        queue.close()


def _scan_claimed_task_log_rows(
    ctx: WeftContext,
    queue_name: str,
    *,
    limit: int,
) -> tuple[QueueWindowRow, ...]:
    if limit <= 0:
        return ()
    with ctx.broker() as broker:
        all_rows = _peek_rows_including_claimed(
            broker,
            queue_name,
            limit=limit + _queue_pending_count(ctx, queue_name),
        )
    pending_ids = {
        row.message_id
        for row in scan_queue_window(ctx, queue_name, limit=max(1, len(all_rows)))
    }
    claimed_rows = [row for row in all_rows if row.message_id not in pending_ids]
    return tuple(claimed_rows[:limit])


def _queue_pending_count(ctx: WeftContext, queue_name: str) -> int:
    queue = ctx.queue(queue_name, persistent=False)
    try:
        return int(queue.stats().pending)
    finally:
        queue.close()


def _peek_rows_including_claimed(
    broker: Any,
    queue_name: str,
    *,
    limit: int,
) -> tuple[QueueWindowRow, ...]:
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


def _select_claimed_task_log_candidates(
    rows: Sequence[QueueWindowRow],
    *,
    limit: int,
) -> list[CleanupCandidate]:
    candidates: list[CleanupCandidate] = []
    for row in rows[:limit]:
        decoded = _decode_row(row, queue_name=WEFT_GLOBAL_LOG_QUEUE)
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


def _task_log_candidates_from_collated_group(
    group: CollatedMessageGroup,
) -> list[CleanupCandidate]:
    candidate_class, reason = _collated_task_log_candidate_class(group)
    policy = _collated_task_log_policy(group)
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


def _task_log_scan_metadata(
    scan_window: TaskLogScanWindow,
    family_selection: TaskLogFamilySelection,
) -> dict[str, Any]:
    return {
        "scan": scan_window.to_summary(),
        "family_selection": family_selection.to_summary(
            skipped_open_family_limit=TASK_MONITOR_PONG_DETAIL_LIMIT,
        ),
    }


def _select_old_unstarted_task_log_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    now_ns: int,
    min_age_seconds: float,
    claimed_ids: set[int],
    exclude_tids: set[str],
) -> OlderThanSelection:
    protected_tids = _open_started_task_log_tids(
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


def _terminal_reserved_candidates(
    ctx: WeftContext,
    groups: Sequence[CollatedMessageGroup],
    *,
    config: TaskMonitorCleanupConfig,
    now_ns: int,
) -> tuple[
    list[CleanupCandidate],
    tuple[CleanupQueueStats, ...],
    tuple[CleanupPolicyStats, ...],
]:
    candidates: list[CleanupCandidate] = []
    stats: list[CleanupQueueStats] = []
    policy_stats: list[CleanupPolicyStats] = []
    for group in groups:
        if _collated_group_is_successful_completion(group):
            continue
        queue_name = f"T{group.tid}.{QUEUE_RESERVED_SUFFIX}"
        rows = scan_queue_window(ctx, queue_name, limit=config.batch_size)
        if not rows:
            continue
        decoded = tuple(_decode_reserved_row(row) for row in rows)

        def reserved_tid(
            _payload: Mapping[str, Any] | None,
            _row: DecodedQueueWindowRow,
            tid: str = group.tid,
        ) -> str:
            return tid

        selected = older_than_candidates(
            decoded,
            policy=TASK_MONITOR_POLICY_RESERVED_DELETE_TERMINAL_PROVEN,
            now_ns=now_ns,
            min_age_seconds=config.task_log_min_age_seconds,
            candidate_class="terminal_reserved_with_log",
            reason="terminal_task_log_proof_for_reserved_tid",
            stop_reason="first_reserved_row_too_young",
            tid_from_row=reserved_tid,
        )
        candidates.extend(selected.candidates)
        stats.append(
            cleanup_queue_stats(
                queue_name,
                scanned=len(rows),
                candidates=selected.candidates,
                stop_reason=selected.stop_reason,
            )
        )
        policy_stats.append(
            cleanup_policy_stats(
                queue_name,
                policy=TASK_MONITOR_POLICY_RESERVED_DELETE_TERMINAL_PROVEN,
                scanned=len(rows),
                candidates=selected.candidates,
                stop_reason=selected.stop_reason,
            )
        )
    return candidates, tuple(stats), tuple(policy_stats)


def _collated_group_is_successful_completion(group: CollatedMessageGroup) -> bool:
    if group.terminal_status == STATUS_COMPLETED:
        return True
    if group.terminal_event is None:
        return False
    return TERMINAL_TASK_EVENTS.get(group.terminal_event) == STATUS_COMPLETED


def _decode_reserved_row(row: QueueWindowRow) -> DecodedQueueWindowRow:
    try:
        payload = json.loads(row.body)
    except json.JSONDecodeError:
        return DecodedQueueWindowRow(raw=row, payload=None)
    if not isinstance(payload, dict):
        return DecodedQueueWindowRow(raw=row, payload=None)
    return DecodedQueueWindowRow(raw=row, payload=payload)


def _collated_task_log_candidate_class(
    group: CollatedMessageGroup,
) -> tuple[str, str]:
    if _collated_group_has_visible_start(group):
        return "collated_terminal_task_log", "anchor_tid_terminal_event_found_in_window"
    return (
        "truncated_terminal_task_log",
        "terminal_event_without_visible_start_in_window",
    )


def _collated_task_log_policy(group: CollatedMessageGroup) -> str:
    if _collated_group_has_visible_start(group):
        return TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE
    return TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START


def _collated_group_has_visible_start(group: CollatedMessageGroup) -> bool:
    return any(
        payload_string(row.payload, "event") in TASK_LOG_START_EVENTS
        for row in group.message_rows
    )


def _open_started_task_log_tids(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    claimed_ids: set[int],
    exclude_tids: set[str],
) -> set[str]:
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


def _with_apply_counts(
    stats: Sequence[CleanupQueueStats],
    applied: Sequence[AppliedCleanupCandidate],
    *,
    report_only: bool,
) -> list[CleanupQueueStats]:
    deleted_by_queue = Counter(
        result.candidate.queue for result in applied if result.deleted
    )
    reported_by_queue: Counter[str] = Counter()
    if report_only:
        reported_by_queue.update({stat.queue: stat.selected for stat in stats})
    elif applied:
        reported_by_queue.update(
            result.candidate.queue for result in applied if not result.deleted
        )
    return [
        CleanupQueueStats(
            queue=stat.queue,
            scanned=stat.scanned,
            selected=stat.selected,
            deleted=deleted_by_queue[stat.queue],
            reported=reported_by_queue[stat.queue],
            stop_reason=stat.stop_reason,
            reason_counts=dict(stat.reason_counts),
            collated_tasks=stat.collated_tasks,
            metadata=stat.metadata,
        )
        for stat in stats
    ]


def _with_policy_apply_counts(
    stats: Sequence[CleanupPolicyStats],
    applied: Sequence[AppliedCleanupCandidate],
    *,
    report_only: bool,
) -> list[CleanupPolicyStats]:
    deleted_by_policy_queue = Counter(
        (result.candidate.policy, result.candidate.queue)
        for result in applied
        if result.deleted
    )
    reported_by_policy_queue: Counter[tuple[str, str]] = Counter()
    if report_only:
        reported_by_policy_queue.update(
            {
                (stat.policy, stat.queue): stat.selected
                for stat in stats
                if stat.selected
            }
        )
    elif applied:
        reported_by_policy_queue.update(
            (result.candidate.policy, result.candidate.queue)
            for result in applied
            if not result.deleted
        )
    return [
        CleanupPolicyStats(
            policy=stat.policy,
            queue=stat.queue,
            scanned=stat.scanned,
            selected=stat.selected,
            deleted=deleted_by_policy_queue[(stat.policy, stat.queue)],
            reported=reported_by_policy_queue[(stat.policy, stat.queue)],
            stop_reason=stat.stop_reason,
            reason_counts=dict(stat.reason_counts),
        )
        for stat in stats
    ]
