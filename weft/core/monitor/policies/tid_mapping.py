"""TaskMonitor cleanup policies for ``weft.state.tid_mappings``.

Keep-newest-per-key + payload-liveness gating (Spec: Cleanup Boundary in
05-Message_Flow_and_State.md; [OBS.13.7]): the newest mapping row for each
``full`` key is only a deletion candidate when the payload's own liveness
probe fails. Superseded (non-newest) rows keep the age-only rule. The probe
uses only evidence carried in the row payload itself (the runtime handle's
``(pid, create_time)`` pairs) -- no terminal-evidence lookup and no
collation-store reach from this policy.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.7]
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from weft._constants import (
    RUNTIME_PRUNE_CLASS_SUPERSEDED_TID_MAPPING,
    TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.core.monitor.progress import PolicyProgress
from weft.core.pruning.models import (
    CleanupCandidate,
    CleanupPolicyStats,
    CleanupQueueStats,
    cleanup_candidate_from_row,
    cleanup_policy_stats,
    cleanup_queue_stats,
)
from weft.core.pruning.policies import malformed_row_candidates, older_than_candidates
from weft.core.queue_window import (
    DecodedQueueWindowRow,
    QueueWindowRow,
    payload_string,
)
from weft.ext import RunnerHandle
from weft.helpers import handle_has_live_host_process


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


def mapping_row_is_live(payload: Mapping[str, Any] | None) -> bool:
    """Return whether a mapping payload's own liveness probe finds a live owner.

    Undecidable payloads (no runtime handle, or a handle with no probeable
    host PIDs -- e.g. external/non-host runtime handles) are treated as
    live: undecidable means skip, never delete. This is the only liveness
    evidence this policy consults; it never looks past the row payload.

    Public because the TaskMonitor's destruction-protection gate
    (``_destruction_protected_runtime_tids``) shares these exact semantics
    for destructive decisions without terminal proof; the probe must not
    be duplicated.

    Spec: [OBS.13.7]
    """

    if payload is None:
        return True
    handle_payload = payload.get("runtime_handle")
    if not isinstance(handle_payload, Mapping):
        return True
    try:
        handle = RunnerHandle.from_dict(handle_payload)
    except ValueError:
        return True
    if not handle.scoped_host_processes():
        return True
    return handle_has_live_host_process(handle)


def _newest_message_id_per_key(
    rows: Sequence[DecodedQueueWindowRow],
) -> dict[str, int]:
    """Return the newest (max message_id) row's id observed for each mapping key."""

    newest: dict[str, int] = {}
    for row in rows:
        if row.malformed_reason is not None:
            continue
        key = payload_string(row.payload, "full")
        if key is None:
            continue
        current = newest.get(key)
        if current is None or row.raw.message_id > current:
            newest[key] = row.raw.message_id
    return newest


def tid_mapping_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    now_ns: int,
    min_age_seconds: float,
    exclude_tids: set[str],
    scan_limit_reached: bool = False,
    newest_ids: Mapping[str, int] | None = None,
) -> tuple[
    list[CleanupCandidate],
    CleanupQueueStats,
    tuple[CleanupPolicyStats, ...],
    tuple[PolicyProgress, ...],
]:
    """Select stale or malformed TID mapping rows for cleanup.

    Keeps the newest row per mapping key regardless of age unless its
    payload's liveness probe fails; superseded (non-newest) rows keep the
    current age-only rule (Spec: Cleanup Boundary in
    05-Message_Flow_and_State.md; [OBS.13.7]).

    Args:
        newest_ids: Optional newest message id per mapping key from
            evidence BEYOND this bounded row window (the caller's
            full-queue scan). Required whenever ``rows`` is a truncated
            window: without it, a superseded row whose newer sibling lies
            beyond the window would be misclassified as newest and
            liveness-protected, stalling age-only cleanup. May be omitted
            when ``rows`` covers the whole queue. Merged per key by max,
            so missing evidence degrades toward protection (skip), never
            toward deletion.
    """

    malformed_candidates = malformed_row_candidates(
        rows,
        policy=TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
        candidate_class="malformed_tid_mapping",
    )
    candidates = list(malformed_candidates)
    claimed = {candidate.message_id for candidate in candidates}
    merged_newest = _newest_message_id_per_key(rows)
    if newest_ids:
        for key, message_id in newest_ids.items():
            if message_id > merged_newest.get(key, 0):
                merged_newest[key] = message_id

    # Run the normal FIFO age-only pass over every unclaimed row so
    # `stop_reason` still reflects the true scan position (including for
    # the common case of a single row per key). The newest row per key is
    # then pulled back out of the age-only result and re-evaluated under
    # the liveness gate below instead of being deleted outright.
    old_rows = older_than_candidates(
        rows,
        policy=TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
        now_ns=now_ns,
        min_age_seconds=min_age_seconds,
        candidate_class=RUNTIME_PRUNE_CLASS_SUPERSEDED_TID_MAPPING,
        reason="older_than_tid_mapping_cleanup_min_age",
        stop_reason="first_tid_mapping_too_young",
        claimed_ids=claimed,
        exclude_tids=exclude_tids,
        tid_from_row=lambda payload, _row: payload_string(payload, "full"),
    )
    newest_row_by_id = {row.raw.message_id: row for row in rows}
    age_selected: list[CleanupCandidate] = []
    for age_candidate in old_rows.candidates:
        candidate_tid = age_candidate.tid
        if (
            candidate_tid is None
            or merged_newest.get(candidate_tid) != age_candidate.message_id
        ):
            age_selected.append(age_candidate)
            continue
        # This is the newest row for its key: only a candidate when its
        # payload's own liveness probe fails. Undecidable payloads are
        # treated as live and skipped.
        row = newest_row_by_id.get(age_candidate.message_id)
        if row is not None and not mapping_row_is_live(row.payload):
            age_selected.append(
                cleanup_candidate_from_row(
                    row.raw,
                    policy=TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
                    candidate_class="old_tid_mapping",
                    reason="older_than_tid_mapping_cleanup_min_age",
                    tid=age_candidate.tid,
                    payload=row.payload,
                )
            )
    candidates.extend(age_selected)
    queue_stats = cleanup_queue_stats(
        WEFT_TID_MAPPINGS_QUEUE,
        scanned=len(rows),
        candidates=candidates,
        stop_reason=old_rows.stop_reason,
    )
    policy_stats = (
        cleanup_policy_stats(
            WEFT_TID_MAPPINGS_QUEUE,
            policy=TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
            scanned=len(rows),
            candidates=candidates,
            stop_reason=old_rows.stop_reason,
        ),
    )
    reason_counts = Counter(candidate.reason for candidate in candidates)
    # Waypoint (catch-up cadence) must be claimed from POST-gate selection:
    # a full window of correctly liveness-protected rows selects nothing and
    # must not hot-loop the monitor on catch-up intervals with zero forward
    # progress.
    waypoint_reached = scan_limit_reached and (
        bool(malformed_candidates)
        or (old_rows.stop_reason is None and bool(age_selected))
    )
    base_reached = not waypoint_reached and (
        old_rows.stop_reason == "first_tid_mapping_too_young"
        or (not candidates and not scan_limit_reached)
    )
    progress = (
        PolicyProgress(
            policy=TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
            domain=WEFT_TID_MAPPINGS_QUEUE,
            scanned=len(rows),
            selected=len(candidates),
            deferred=len(exclude_tids),
            waypoint_reached=waypoint_reached,
            base_reached=base_reached,
            reason_counts=reason_counts,
        ),
    )
    return candidates, queue_stats, policy_stats, progress
