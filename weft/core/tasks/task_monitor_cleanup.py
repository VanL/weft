"""TaskMonitor-owned cleanup orchestration.

TaskMonitor decides when cleanup runs and whether the cycle is destructive.
Reusable pruning policies live under ``weft.core.pruning`` and task-log
grouping lives in ``weft.core.task_log_collation``.

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
    RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS,
    TASK_LOG_START_EVENTS,
    TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.core.pruning.models import (
    AppliedCleanupCandidate,
    CleanupCandidate,
    CleanupQueueStats,
    applied_cleanup_candidate,
    cleanup_candidate_from_row,
    cleanup_queue_stats,
)
from weft.core.pruning.policies import (
    malformed_row_candidates,
    older_than_candidates,
)
from weft.core.queue_window import (
    DecodedQueueWindowRow,
    QueueWindowRow,
    payload_string,
    scan_queue_window,
)
from weft.core.task_log_collation import (
    CollatedMessageGroup,
    collate_next_task_log_group,
    is_terminal_task_log,
)


@dataclass(frozen=True, slots=True)
class TaskMonitorCleanupConfig:
    """Configuration for one TaskMonitor cleanup pass."""

    batch_size: int = WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT
    tid_mapping_min_age_seconds: float = (
        TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS
    )
    task_log_min_age_seconds: float = RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS
    queues: tuple[str, ...] = (WEFT_TID_MAPPINGS_QUEUE, WEFT_GLOBAL_LOG_QUEUE)


@dataclass(frozen=True, slots=True)
class TaskMonitorCleanupResult:
    """Result of one TaskMonitor cleanup pass."""

    success: bool
    candidates: tuple[CleanupCandidate, ...] = ()
    applied_candidates: tuple[AppliedCleanupCandidate, ...] = ()
    queue_stats: tuple[CleanupQueueStats, ...] = ()
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

    current_ns = time.time_ns() if now_ns is None else now_ns
    excluded = {tid for tid in exclude_tids if tid}
    candidates: list[CleanupCandidate] = []
    queue_stats: list[CleanupQueueStats] = []
    errors: list[str] = []

    for queue_name in config.queues:
        try:
            rows = scan_queue_window(ctx, queue_name, limit=config.batch_size)
            selected, stats = _select_queue_candidates(
                ctx,
                queue_name,
                rows,
                config=config,
                now_ns=current_ns,
                exclude_tids=excluded,
            )
        except (BrokerError, OSError, RuntimeError, ValueError) as exc:
            errors.append(f"failed to scan {queue_name}: {exc}")
            continue
        candidates.extend(selected)
        queue_stats.extend(stats)

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
    apply_errors = tuple(
        candidate.error for candidate in applied if candidate.error is not None
    )
    return TaskMonitorCleanupResult(
        success=not errors and not apply_errors,
        candidates=tuple(candidates),
        applied_candidates=applied,
        queue_stats=final_queue_stats,
        errors=(*errors, *apply_errors),
    )


def _select_queue_candidates(
    ctx: WeftContext,
    queue_name: str,
    rows: Sequence[QueueWindowRow],
    *,
    config: TaskMonitorCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
) -> tuple[list[CleanupCandidate], tuple[CleanupQueueStats, ...]]:
    decoded = tuple(_decode_row(row, queue_name=queue_name) for row in rows)
    if queue_name == WEFT_TID_MAPPINGS_QUEUE:
        candidates, stats = _tid_mapping_candidates(
            decoded,
            config=config,
            now_ns=now_ns,
            exclude_tids=exclude_tids,
        )
        return candidates, (stats,)
    if queue_name == WEFT_GLOBAL_LOG_QUEUE:
        return _task_log_candidates(
            ctx,
            decoded,
            config=config,
            now_ns=now_ns,
            exclude_tids=exclude_tids,
        )
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
    elif queue_name == WEFT_GLOBAL_LOG_QUEUE and not _valid_task_log_payload(payload):
        return DecodedQueueWindowRow(
            raw=row,
            payload=payload,
            malformed_reason="invalid_task_log_shape",
        )
    return DecodedQueueWindowRow(raw=row, payload=payload)


def _valid_tid_mapping_payload(payload: Mapping[str, Any]) -> bool:
    full = payload.get("full")
    short = payload.get("short")
    return (
        isinstance(full, str) and bool(full) and isinstance(short, str) and bool(short)
    )


def _valid_task_log_payload(payload: Mapping[str, Any]) -> bool:
    tid = payload.get("tid")
    return isinstance(tid, str) and bool(tid)


def _tid_mapping_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    config: TaskMonitorCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
) -> tuple[list[CleanupCandidate], CleanupQueueStats]:
    candidates = malformed_row_candidates(
        rows,
        candidate_class="malformed_tid_mapping",
    )
    claimed = {candidate.message_id for candidate in candidates}
    old_rows = older_than_candidates(
        rows,
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
    return candidates, cleanup_queue_stats(
        WEFT_TID_MAPPINGS_QUEUE,
        scanned=len(rows),
        candidates=candidates,
        stop_reason=old_rows.stop_reason,
    )


def _task_log_candidates(
    ctx: WeftContext,
    rows: Sequence[DecodedQueueWindowRow],
    *,
    config: TaskMonitorCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
) -> tuple[list[CleanupCandidate], tuple[CleanupQueueStats, ...]]:
    candidates = malformed_row_candidates(
        rows,
        candidate_class="malformed_task_log",
    )
    claimed = {candidate.message_id for candidate in candidates}
    stop_reason: str | None = None

    collated_summaries: list[CollatedMessageGroup] = []
    collate_skipped_tids: set[str] = set()
    while True:
        collated = collate_next_task_log_group(
            rows,
            now_ns=now_ns,
            min_age_seconds=config.task_log_min_age_seconds,
            exclude_tids=exclude_tids,
            claimed_ids=claimed,
            skipped_tids=collate_skipped_tids,
        )
        if collated.group is None:
            stop_reason = collated.stop_reason
            if collated.skipped_tid is not None:
                collate_skipped_tids.add(collated.skipped_tid)
                continue
            break
        candidate_class, reason = _collated_task_log_candidate_class(collated.group)
        group_candidates = [
            cleanup_candidate_from_row(
                row.raw,
                candidate_class=candidate_class,
                reason=reason,
                tid=collated.group.tid,
                payload=row.payload,
            )
            for row in collated.group.message_rows
        ]
        candidates.extend(group_candidates)
        claimed.update(collated.group.message_ids)
        collated_summaries.append(collated.group)

    protected_tids = _open_started_task_log_tids(
        rows,
        claimed_ids=claimed,
        exclude_tids=exclude_tids,
    )
    old_rows = older_than_candidates(
        rows,
        now_ns=now_ns,
        min_age_seconds=config.task_log_min_age_seconds,
        candidate_class="old_task_log",
        reason="older_than_task_log_cleanup_min_age",
        stop_reason="first_task_log_too_young",
        claimed_ids=claimed,
        exclude_tids=exclude_tids | protected_tids,
    )
    candidates.extend(old_rows.candidates)
    stop_reason = stop_reason or old_rows.stop_reason
    reserved_candidates, reserved_stats = _terminal_reserved_candidates(
        ctx,
        collated_summaries,
        config=config,
        now_ns=now_ns,
    )
    candidates.extend(reserved_candidates)
    task_log_candidates = [
        candidate
        for candidate in candidates
        if candidate.queue == WEFT_GLOBAL_LOG_QUEUE
    ]
    task_log_stats = cleanup_queue_stats(
        WEFT_GLOBAL_LOG_QUEUE,
        scanned=len(rows),
        candidates=task_log_candidates,
        stop_reason=stop_reason,
        collated_tasks=tuple(collated_summaries),
    )
    return candidates, (task_log_stats, *reserved_stats)


def _terminal_reserved_candidates(
    ctx: WeftContext,
    groups: Sequence[CollatedMessageGroup],
    *,
    config: TaskMonitorCleanupConfig,
    now_ns: int,
) -> tuple[list[CleanupCandidate], tuple[CleanupQueueStats, ...]]:
    candidates: list[CleanupCandidate] = []
    stats: list[CleanupQueueStats] = []
    for group in groups:
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
    return candidates, tuple(stats)


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
        )
        for stat in stats
    ]
