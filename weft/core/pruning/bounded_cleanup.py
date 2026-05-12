"""Bounded TaskMonitor cleanup policies.

This module owns the background cleanup path used by the manager-supervised
``TaskMonitorTask``. It scans small FIFO windows, selects exact message IDs,
and delegates deletion to the shared exact-prune helper.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from simplebroker.ext import BrokerError
from weft._constants import (
    RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS,
    TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
    TERMINAL_TASK_EVENTS,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.helpers import iter_queue_entries


@dataclass(frozen=True, slots=True)
class BoundedCleanupConfig:
    """Configuration for one bounded background cleanup pass."""

    batch_size: int = WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT
    tid_mapping_min_age_seconds: float = (
        TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS
    )
    task_log_min_age_seconds: float = RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS
    queues: tuple[str, ...] = (WEFT_TID_MAPPINGS_QUEUE, WEFT_GLOBAL_LOG_QUEUE)


@dataclass(frozen=True, slots=True)
class BoundedCleanupCandidate:
    """One exact row selected by bounded cleanup policy."""

    queue: str
    message_id: int
    candidate_class: str
    reason: str
    tid: str | None = None
    payload_sha256: str | None = None
    payload_size_bytes: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    report_only: bool = False


@dataclass(frozen=True, slots=True)
class AppliedBoundedCleanupCandidate:
    """Result of applying one bounded cleanup candidate."""

    candidate: BoundedCleanupCandidate
    deleted: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class TaskLogCollationSummary:
    """Operational summary for one collated task-log lifecycle record."""

    tid: str
    rows: int
    first_message_id: int
    terminal_message_id: int
    terminal_event: str | None = None
    terminal_status: str | None = None

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe operational summary."""

        return {
            "tid": self.tid,
            "rows": self.rows,
            "first_message_id": self.first_message_id,
            "terminal_message_id": self.terminal_message_id,
            "terminal_event": self.terminal_event,
            "terminal_status": self.terminal_status,
        }


@dataclass(frozen=True, slots=True)
class BoundedCleanupQueueStats:
    """Summary of one queue's bounded cleanup pass."""

    queue: str
    scanned: int
    selected: int
    deleted: int = 0
    reported: int = 0
    stop_reason: str | None = None
    reason_counts: Mapping[str, int] = field(default_factory=dict)
    collated_tasks: tuple[TaskLogCollationSummary, ...] = ()

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe operational summary."""

        return {
            "queue": self.queue,
            "scanned": self.scanned,
            "selected": self.selected,
            "deleted": self.deleted,
            "reported": self.reported,
            "stop_reason": self.stop_reason,
            "reason_counts": dict(self.reason_counts),
            "collated_task_count": len(self.collated_tasks),
            "collated_tasks": [task.to_summary() for task in self.collated_tasks],
        }


@dataclass(frozen=True, slots=True)
class BoundedCleanupResult:
    """Result of one bounded cleanup pass."""

    success: bool
    candidates: tuple[BoundedCleanupCandidate, ...] = ()
    applied_candidates: tuple[AppliedBoundedCleanupCandidate, ...] = ()
    queue_stats: tuple[BoundedCleanupQueueStats, ...] = ()
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


@dataclass(frozen=True, slots=True)
class _RawQueueRow:
    queue: str
    body: str
    message_id: int


@dataclass(frozen=True, slots=True)
class _DecodedQueueRow:
    raw: _RawQueueRow
    payload: dict[str, Any] | None
    malformed_reason: str | None = None

    @property
    def tid(self) -> str | None:
        if self.payload is None:
            return None
        value = self.payload.get("tid")
        return value if isinstance(value, str) and value else None


def run_bounded_task_monitor_cleanup(
    ctx: WeftContext,
    config: BoundedCleanupConfig,
    *,
    apply: bool,
    exclude_tids: Iterable[str] = (),
    now_ns: int | None = None,
) -> BoundedCleanupResult:
    """Run one bounded background cleanup pass.

    Spec: [MF-5], [OBS.13], [OBS.16], [OBS.17]
    """

    if config.batch_size <= 0:
        return BoundedCleanupResult(
            success=False,
            errors=("bounded cleanup batch_size must be positive",),
        )

    current_ns = time.time_ns() if now_ns is None else now_ns
    excluded = {tid for tid in exclude_tids if tid}
    candidates: list[BoundedCleanupCandidate] = []
    queue_stats: list[BoundedCleanupQueueStats] = []
    errors: list[str] = []

    for queue_name in config.queues:
        try:
            rows = _scan_queue_window(ctx, queue_name, limit=config.batch_size)
            selected, stats = _select_queue_candidates(
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
        queue_stats.append(stats)

    applied: tuple[AppliedBoundedCleanupCandidate, ...] = ()
    if apply and candidates:
        applied = tuple(
            apply_exact_prune_candidates(
                ctx,
                candidates,
                apply_result=_applied_candidate,
            )
        )

    queue_stats = _with_apply_counts(queue_stats, applied, report_only=not apply)
    apply_errors = tuple(
        candidate.error for candidate in applied if candidate.error is not None
    )
    return BoundedCleanupResult(
        success=not errors and not apply_errors,
        candidates=tuple(candidates),
        applied_candidates=applied,
        queue_stats=tuple(queue_stats),
        errors=(*errors, *apply_errors),
    )


def _scan_queue_window(
    ctx: WeftContext,
    queue_name: str,
    *,
    limit: int,
) -> tuple[_RawQueueRow, ...]:
    queue = ctx.queue(queue_name, persistent=False)
    rows: list[_RawQueueRow] = []
    try:
        for body, message_id in iter_queue_entries(queue):
            rows.append(
                _RawQueueRow(
                    queue=queue_name,
                    body=body,
                    message_id=int(message_id),
                )
            )
            if len(rows) >= limit:
                break
    finally:
        queue.close()
    return tuple(rows)


def _select_queue_candidates(
    queue_name: str,
    rows: Sequence[_RawQueueRow],
    *,
    config: BoundedCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
) -> tuple[list[BoundedCleanupCandidate], BoundedCleanupQueueStats]:
    decoded = tuple(_decode_row(row, queue_name=queue_name) for row in rows)
    if queue_name == WEFT_TID_MAPPINGS_QUEUE:
        return _tid_mapping_candidates(decoded, config=config, now_ns=now_ns)
    if queue_name == WEFT_GLOBAL_LOG_QUEUE:
        return _task_log_candidates(
            decoded,
            config=config,
            now_ns=now_ns,
            exclude_tids=exclude_tids,
        )
    return (
        [],
        BoundedCleanupQueueStats(
            queue=queue_name,
            scanned=len(rows),
            selected=0,
            stop_reason="unsupported_queue",
        ),
    )


def _decode_row(row: _RawQueueRow, *, queue_name: str) -> _DecodedQueueRow:
    try:
        payload = json.loads(row.body)
    except json.JSONDecodeError:
        return _DecodedQueueRow(
            raw=row,
            payload=None,
            malformed_reason="invalid_json",
        )
    if not isinstance(payload, dict):
        return _DecodedQueueRow(
            raw=row,
            payload=None,
            malformed_reason="json_not_object",
        )
    if queue_name == WEFT_TID_MAPPINGS_QUEUE:
        if not _valid_tid_mapping_payload(payload):
            return _DecodedQueueRow(
                raw=row,
                payload=payload,
                malformed_reason="invalid_tid_mapping_shape",
            )
    elif queue_name == WEFT_GLOBAL_LOG_QUEUE and not _valid_task_log_payload(payload):
        return _DecodedQueueRow(
            raw=row,
            payload=payload,
            malformed_reason="invalid_task_log_shape",
        )
    return _DecodedQueueRow(raw=row, payload=payload)


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
    rows: Sequence[_DecodedQueueRow],
    *,
    config: BoundedCleanupConfig,
    now_ns: int,
) -> tuple[list[BoundedCleanupCandidate], BoundedCleanupQueueStats]:
    candidates: list[BoundedCleanupCandidate] = []
    claimed: set[int] = set()
    stop_reason: str | None = None

    for row in rows:
        if row.malformed_reason is None:
            continue
        candidates.append(
            _candidate(
                row.raw,
                candidate_class="malformed_tid_mapping",
                reason=row.malformed_reason,
            )
        )
        claimed.add(row.raw.message_id)

    for row in rows:
        if row.raw.message_id in claimed or row.malformed_reason is not None:
            continue
        if _is_old_enough(
            row.raw.message_id,
            now_ns,
            config.tid_mapping_min_age_seconds,
        ):
            candidates.append(
                _candidate(
                    row.raw,
                    candidate_class="old_tid_mapping",
                    reason="older_than_tid_mapping_cleanup_min_age",
                    tid=_payload_string(row.payload, "full"),
                    payload=row.payload,
                )
            )
            claimed.add(row.raw.message_id)
            continue
        stop_reason = "first_tid_mapping_too_young"
        break

    return candidates, _stats(WEFT_TID_MAPPINGS_QUEUE, rows, candidates, stop_reason)


def _task_log_candidates(
    rows: Sequence[_DecodedQueueRow],
    *,
    config: BoundedCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
) -> tuple[list[BoundedCleanupCandidate], BoundedCleanupQueueStats]:
    candidates: list[BoundedCleanupCandidate] = []
    claimed: set[int] = set()
    stop_reason: str | None = None

    for row in rows:
        if row.malformed_reason is None:
            continue
        candidates.append(
            _candidate(
                row.raw,
                candidate_class="malformed_task_log",
                reason=row.malformed_reason,
            )
        )
        claimed.add(row.raw.message_id)

    collated_any = False
    collated_summaries: list[TaskLogCollationSummary] = []
    collate_skipped_tids: set[str] = set()
    while True:
        collated, collate_stop, summary, skipped_tid = _collated_task_log_candidates(
            rows,
            config=config,
            now_ns=now_ns,
            exclude_tids=exclude_tids,
            claimed=claimed,
            skipped_tids=collate_skipped_tids,
        )
        if not collated:
            stop_reason = collate_stop
            if skipped_tid is not None:
                collate_skipped_tids.add(skipped_tid)
                continue
            break
        collated_any = True
        candidates.extend(collated)
        claimed.update(candidate.message_id for candidate in collated)
        if summary is not None:
            collated_summaries.append(summary)

    if collated_any:
        return candidates, _stats(
            WEFT_GLOBAL_LOG_QUEUE,
            rows,
            candidates,
            stop_reason,
            collated_tasks=tuple(collated_summaries),
        )

    for row in rows:
        if row.raw.message_id in claimed or row.malformed_reason is not None:
            continue
        tid = row.tid
        if tid is not None and tid in exclude_tids:
            continue
        if _is_old_enough(
            row.raw.message_id,
            now_ns,
            config.task_log_min_age_seconds,
        ):
            candidates.append(
                _candidate(
                    row.raw,
                    candidate_class="old_task_log",
                    reason="older_than_task_log_cleanup_min_age",
                    tid=tid,
                    payload=row.payload,
                )
            )
            claimed.add(row.raw.message_id)
            continue
        stop_reason = stop_reason or "first_task_log_too_young"
        break

    return candidates, _stats(
        WEFT_GLOBAL_LOG_QUEUE,
        rows,
        candidates,
        stop_reason,
        collated_tasks=tuple(collated_summaries),
    )


def _collated_task_log_candidates(
    rows: Sequence[_DecodedQueueRow],
    *,
    config: BoundedCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
    claimed: set[int],
    skipped_tids: set[str],
) -> tuple[
    list[BoundedCleanupCandidate],
    str | None,
    TaskLogCollationSummary | None,
    str | None,
]:
    anchor_index: int | None = None
    anchor_tid: str | None = None
    for index, row in enumerate(rows):
        if row.raw.message_id in claimed or row.malformed_reason is not None:
            continue
        tid = row.tid
        if tid is None or tid in exclude_tids or tid in skipped_tids:
            continue
        anchor_index = index
        anchor_tid = tid
        break

    if anchor_index is None or anchor_tid is None:
        return [], None, None, None
    anchor = rows[anchor_index]
    if not _is_old_enough(
        anchor.raw.message_id,
        now_ns,
        config.task_log_min_age_seconds,
    ):
        return [], "task_log_anchor_too_young_for_collate", None, None

    collected: list[_DecodedQueueRow] = []
    terminal_row: _DecodedQueueRow | None = None
    for row in rows[anchor_index:]:
        if row.raw.message_id in claimed or row.malformed_reason is not None:
            continue
        if row.tid != anchor_tid:
            continue
        collected.append(row)
        if _is_terminal_task_log(row.payload):
            terminal_row = row
            break

    if terminal_row is None:
        return [], "collate_anchor_without_terminal", None, anchor_tid

    return (
        [
            _candidate(
                row.raw,
                candidate_class="collated_terminal_task_log",
                reason="anchor_tid_terminal_event_found_in_window",
                tid=anchor_tid,
                payload=row.payload,
            )
            for row in collected
        ],
        None,
        _collation_summary(anchor_tid, collected, terminal_row),
        None,
    )


def _is_terminal_task_log(payload: Mapping[str, Any] | None) -> bool:
    if payload is None:
        return False
    event = payload.get("event")
    status = payload.get("status")
    if isinstance(event, str) and event in TERMINAL_TASK_EVENTS:
        return True
    return isinstance(status, str) and status in TERMINAL_TASK_STATUSES


def _collation_summary(
    tid: str,
    rows: Sequence[_DecodedQueueRow],
    terminal_row: _DecodedQueueRow,
) -> TaskLogCollationSummary:
    terminal_event = _payload_string(terminal_row.payload, "event")
    terminal_status = _payload_string(terminal_row.payload, "status")
    return TaskLogCollationSummary(
        tid=tid,
        rows=len(rows),
        first_message_id=rows[0].raw.message_id,
        terminal_message_id=terminal_row.raw.message_id,
        terminal_event=terminal_event,
        terminal_status=terminal_status,
    )


def _candidate(
    row: _RawQueueRow,
    *,
    candidate_class: str,
    reason: str,
    tid: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> BoundedCleanupCandidate:
    metadata: dict[str, Any] = {}
    if payload is not None:
        event = payload.get("event")
        status = payload.get("status")
        if isinstance(event, str):
            metadata["event"] = event
        if isinstance(status, str):
            metadata["status"] = status
    return BoundedCleanupCandidate(
        queue=row.queue,
        message_id=row.message_id,
        candidate_class=candidate_class,
        reason=reason,
        tid=tid,
        payload_sha256=sha256(row.body.encode("utf-8")).hexdigest(),
        payload_size_bytes=len(row.body.encode("utf-8")),
        metadata=metadata,
    )


def _stats(
    queue_name: str,
    rows: Sequence[_DecodedQueueRow],
    candidates: Sequence[BoundedCleanupCandidate],
    stop_reason: str | None,
    *,
    collated_tasks: tuple[TaskLogCollationSummary, ...] = (),
) -> BoundedCleanupQueueStats:
    reason_counts = Counter(candidate.reason for candidate in candidates)
    return BoundedCleanupQueueStats(
        queue=queue_name,
        scanned=len(rows),
        selected=len(candidates),
        stop_reason=stop_reason,
        reason_counts=dict(reason_counts),
        collated_tasks=collated_tasks,
    )


def _with_apply_counts(
    stats: Sequence[BoundedCleanupQueueStats],
    applied: Sequence[AppliedBoundedCleanupCandidate],
    *,
    report_only: bool,
) -> list[BoundedCleanupQueueStats]:
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
        BoundedCleanupQueueStats(
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


def _applied_candidate(
    candidate: BoundedCleanupCandidate,
    deleted: bool,
    error: str | None,
) -> AppliedBoundedCleanupCandidate:
    return AppliedBoundedCleanupCandidate(
        candidate=candidate,
        deleted=deleted,
        error=error,
    )


def _is_old_enough(message_id: int, now_ns: int, min_age_seconds: float) -> bool:
    if min_age_seconds <= 0:
        return True
    return max(0.0, (now_ns - int(message_id)) / 1_000_000_000) >= min_age_seconds


def _payload_string(payload: Mapping[str, Any] | None, key: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None
