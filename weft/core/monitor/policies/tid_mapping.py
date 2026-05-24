"""TaskMonitor cleanup policies for ``weft.state.tid_mappings``.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13]
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from weft._constants import (
    TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED,
    TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.core.monitor.progress import PolicyProgress
from weft.core.pruning.models import (
    CleanupCandidate,
    CleanupPolicyStats,
    CleanupQueueStats,
    cleanup_policy_stats,
    cleanup_queue_stats,
)
from weft.core.pruning.policies import malformed_row_candidates, older_than_candidates
from weft.core.queue_window import (
    DecodedQueueWindowRow,
    QueueWindowRow,
    payload_string,
)


def decode_tid_mapping_row(row: QueueWindowRow) -> DecodedQueueWindowRow:
    """Decode and validate one TID mapping queue row."""

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
    if not valid_tid_mapping_payload(payload):
        return DecodedQueueWindowRow(
            raw=row,
            payload=payload,
            malformed_reason="invalid_tid_mapping_shape",
        )
    return DecodedQueueWindowRow(raw=row, payload=payload)


def valid_tid_mapping_payload(payload: Mapping[str, Any]) -> bool:
    """Return whether a TID mapping payload has the required Weft shape."""

    full = payload.get("full")
    short = payload.get("short")
    return (
        isinstance(full, str) and bool(full) and isinstance(short, str) and bool(short)
    )


def tid_mapping_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    now_ns: int,
    min_age_seconds: float,
    exclude_tids: set[str],
    scan_limit_reached: bool = False,
) -> tuple[
    list[CleanupCandidate],
    CleanupQueueStats,
    tuple[CleanupPolicyStats, ...],
    tuple[PolicyProgress, ...],
]:
    """Select stale or malformed TID mapping rows for cleanup."""

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
        min_age_seconds=min_age_seconds,
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
    progress = (
        PolicyProgress(
            policy=TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED,
            domain=WEFT_TID_MAPPINGS_QUEUE,
            scanned=len(rows),
            selected=len(malformed_candidates),
            waypoint_reached=scan_limit_reached and bool(malformed_candidates),
            base_reached=not scan_limit_reached and not malformed_candidates,
            reason_counts=Counter(
                candidate.reason for candidate in malformed_candidates
            ),
        ),
        PolicyProgress(
            policy=TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN,
            domain=WEFT_TID_MAPPINGS_QUEUE,
            scanned=len(rows),
            selected=len(old_rows.candidates),
            deferred=len(exclude_tids),
            waypoint_reached=(
                scan_limit_reached
                and old_rows.stop_reason is None
                and bool(old_rows.candidates)
            ),
            base_reached=(
                old_rows.stop_reason == "first_tid_mapping_too_young"
                or (not scan_limit_reached and not old_rows.candidates)
            ),
            reason_counts={
                "older_than_tid_mapping_cleanup_min_age": len(old_rows.candidates)
            }
            if old_rows.candidates
            else {},
        ),
    )
    return candidates, queue_stats, policy_stats, progress
