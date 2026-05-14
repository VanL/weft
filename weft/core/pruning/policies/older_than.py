"""Age-based FIFO cleanup policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from weft.core.pruning.models import CleanupCandidate, cleanup_candidate_from_row
from weft.core.queue_window import (
    DecodedQueueWindowRow,
    is_old_enough,
)

TidExtractor = Callable[[Mapping[str, Any] | None, DecodedQueueWindowRow], str | None]


@dataclass(frozen=True, slots=True)
class OlderThanSelection:
    """Result from one older-than policy pass."""

    candidates: tuple[CleanupCandidate, ...]
    stop_reason: str | None = None


def older_than_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    policy: str,
    now_ns: int,
    min_age_seconds: float,
    candidate_class: str,
    reason: str,
    stop_reason: str,
    claimed_ids: set[int] | None = None,
    exclude_tids: set[str] | None = None,
    tid_from_row: TidExtractor | None = None,
) -> OlderThanSelection:
    """Select old unclaimed rows until the first too-young FIFO row."""

    claimed = claimed_ids or set()
    excluded = exclude_tids or set()
    candidates: list[CleanupCandidate] = []
    for row in rows:
        if row.raw.message_id in claimed or row.malformed_reason is not None:
            continue
        tid = tid_from_row(row.payload, row) if tid_from_row is not None else row.tid
        if tid is not None and tid in excluded:
            continue
        if is_old_enough(row.raw.message_id, now_ns, min_age_seconds):
            candidates.append(
                cleanup_candidate_from_row(
                    row.raw,
                    policy=policy,
                    candidate_class=candidate_class,
                    reason=reason,
                    tid=tid,
                    payload=row.payload,
                )
            )
            continue
        return OlderThanSelection(tuple(candidates), stop_reason)
    return OlderThanSelection(tuple(candidates), None)
