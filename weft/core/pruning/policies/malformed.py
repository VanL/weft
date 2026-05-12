"""Malformed-row cleanup policy."""

from __future__ import annotations

from collections.abc import Sequence

from weft.core.pruning.models import CleanupCandidate, cleanup_candidate_from_row
from weft.core.queue_window import DecodedQueueWindowRow


def malformed_row_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    candidate_class: str,
    claimed_ids: set[int] | None = None,
) -> list[CleanupCandidate]:
    """Select rows already marked malformed by queue-owned schema guards."""

    claimed = claimed_ids or set()
    candidates: list[CleanupCandidate] = []
    for row in rows:
        if row.raw.message_id in claimed or row.malformed_reason is None:
            continue
        candidates.append(
            cleanup_candidate_from_row(
                row.raw,
                candidate_class=candidate_class,
                reason=row.malformed_reason,
                tid=row.tid,
                payload=row.payload,
            )
        )
    return candidates
