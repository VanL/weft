"""TaskMonitor cleanup policies for task-local reserved queues.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13]
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from weft._constants import (
    QUEUE_RESERVED_SUFFIX,
    STATUS_COMPLETED,
    TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME,
    TERMINAL_TASK_EVENTS,
)
from weft.context import WeftContext
from weft.core.monitor.progress import PolicyProgress
from weft.core.monitor.task_log_collation import CollatedMessageGroup
from weft.core.pruning.models import (
    CleanupCandidate,
    CleanupPolicyStats,
    CleanupQueueStats,
    cleanup_policy_stats,
    cleanup_queue_stats,
)
from weft.core.pruning.policies import older_than_candidates
from weft.core.queue_window import (
    DecodedQueueWindowRow,
    QueueWindowRow,
    scan_queue_window,
)


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


def terminal_reserved_candidates(
    ctx: WeftContext,
    groups: Sequence[CollatedMessageGroup],
    *,
    now_ns: int,
    min_age_seconds: float,
    selection_limit: int,
    scan_queue_window_fn: ScanQueueWindow | None = None,
) -> tuple[
    list[CleanupCandidate],
    tuple[CleanupQueueStats, ...],
    tuple[CleanupPolicyStats, ...],
    tuple[PolicyProgress, ...],
]:
    """Select reserved rows for terminal non-successful task-log groups."""

    if selection_limit <= 0:
        return [], (), (), ()

    candidates: list[CleanupCandidate] = []
    stats: list[CleanupQueueStats] = []
    policy_stats: list[CleanupPolicyStats] = []
    probes_remaining = selection_limit
    scanned_rows = 0
    stop_reasons: list[str] = []
    scan_window = (
        scan_queue_window if scan_queue_window_fn is None else scan_queue_window_fn
    )
    for group in groups:
        if len(candidates) >= selection_limit or probes_remaining <= 0:
            break
        if collated_group_is_successful_completion(group):
            continue
        probes_remaining -= 1
        queue_name = f"T{group.tid}.{QUEUE_RESERVED_SUFFIX}"
        remaining_candidates = max(0, selection_limit - len(candidates))
        rows = tuple(scan_window(ctx, queue_name, limit=remaining_candidates))
        if not rows:
            continue
        decoded = tuple(decode_reserved_row(row) for row in rows)

        def reserved_tid(
            _payload: Mapping[str, Any] | None,
            _row: DecodedQueueWindowRow,
            tid: str = group.tid,
        ) -> str:
            return tid

        selected = older_than_candidates(
            decoded,
            policy=TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME,
            now_ns=now_ns,
            min_age_seconds=min_age_seconds,
            candidate_class="terminal_reserved_with_log",
            reason="terminal_task_log_proof_for_reserved_tid",
            stop_reason="first_reserved_row_too_young",
            tid_from_row=reserved_tid,
        )
        selected_candidates = selected.candidates[:remaining_candidates]
        candidates.extend(selected_candidates)
        scanned_rows += len(rows)
        if selected.stop_reason is not None:
            stop_reasons.append(selected.stop_reason)
        stats.append(
            cleanup_queue_stats(
                queue_name,
                scanned=len(rows),
                candidates=selected_candidates,
                stop_reason=selected.stop_reason,
            )
        )
        policy_stats.append(
            cleanup_policy_stats(
                queue_name,
                policy=TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME,
                scanned=len(rows),
                candidates=selected_candidates,
                stop_reason=selected.stop_reason,
            )
        )
    waypoint_reached = len(candidates) >= selection_limit
    too_young_boundary_reached = "first_reserved_row_too_young" in stop_reasons
    progress = (
        PolicyProgress(
            policy=TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME,
            domain="task_runtime_queues",
            scanned=scanned_rows or len(groups),
            selected=len(candidates),
            deferred=max(0, len(groups) - (selection_limit - probes_remaining)),
            waypoint_reached=waypoint_reached,
            base_reached=not waypoint_reached
            and (too_young_boundary_reached or not candidates),
            reason_counts={
                "terminal_task_log_proof_for_reserved_tid": len(candidates),
                "reserved_stop_reasons": len(stop_reasons),
            }
            if candidates or stop_reasons
            else {},
        ),
    )
    return candidates, tuple(stats), tuple(policy_stats), progress


def collated_group_is_successful_completion(group: CollatedMessageGroup) -> bool:
    """Return whether a collated task group ended in successful completion."""

    if group.terminal_status == STATUS_COMPLETED:
        return True
    if group.terminal_event is None:
        return False
    return TERMINAL_TASK_EVENTS.get(group.terminal_event) == STATUS_COMPLETED


def decode_reserved_row(row: QueueWindowRow) -> DecodedQueueWindowRow:
    """Decode a reserved queue row without marking malformed payloads."""

    try:
        payload = json.loads(row.body)
    except json.JSONDecodeError:
        return DecodedQueueWindowRow(raw=row, payload=None)
    if not isinstance(payload, dict):
        return DecodedQueueWindowRow(raw=row, payload=None)
    return DecodedQueueWindowRow(raw=row, payload=payload)
