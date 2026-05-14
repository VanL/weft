"""Shared pruning candidate models.

These models describe deletion/report eligibility. Neutral queue-window row
types live in ``weft.core.queue_window`` so non-pruning actions do not depend on
cleanup-specific concepts.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol

from weft.core.queue_window import QueueWindowRow


class SummaryLike(Protocol):
    """Protocol for operational summaries embedded in cleanup stats."""

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe summary."""
        ...


@dataclass(frozen=True, slots=True)
class CleanupCandidate:
    """One exact row selected by cleanup policy."""

    queue: str
    message_id: int
    policy: str
    candidate_class: str
    reason: str
    tid: str | None = None
    payload_sha256: str | None = None
    payload_size_bytes: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    report_only: bool = False


@dataclass(frozen=True, slots=True)
class AppliedCleanupCandidate:
    """Result of applying one cleanup candidate."""

    candidate: CleanupCandidate
    deleted: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class CleanupQueueStats:
    """Summary of one queue's cleanup pass."""

    queue: str
    scanned: int
    selected: int
    deleted: int = 0
    reported: int = 0
    stop_reason: str | None = None
    reason_counts: Mapping[str, int] = field(default_factory=dict)
    collated_tasks: tuple[SummaryLike, ...] = ()

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
class CleanupPolicyStats:
    """Summary of one cleanup policy's work in a queue."""

    policy: str
    queue: str
    scanned: int
    selected: int
    deleted: int = 0
    reported: int = 0
    stop_reason: str | None = None
    reason_counts: Mapping[str, int] = field(default_factory=dict)

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe operational summary."""

        return {
            "policy": self.policy,
            "queue": self.queue,
            "scanned": self.scanned,
            "selected": self.selected,
            "deleted": self.deleted,
            "reported": self.reported,
            "stop_reason": self.stop_reason,
            "reason_counts": dict(self.reason_counts),
        }


def cleanup_candidate_from_row(
    row: QueueWindowRow,
    *,
    policy: str,
    candidate_class: str,
    reason: str,
    tid: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> CleanupCandidate:
    """Build an exact cleanup candidate from a queue-window row."""

    metadata: dict[str, Any] = {}
    if payload is not None:
        event = payload.get("event")
        status = payload.get("status")
        if isinstance(event, str):
            metadata["event"] = event
        if isinstance(status, str):
            metadata["status"] = status
    encoded = row.body.encode("utf-8")
    return CleanupCandidate(
        queue=row.queue,
        message_id=row.message_id,
        policy=policy,
        candidate_class=candidate_class,
        reason=reason,
        tid=tid,
        payload_sha256=sha256(encoded).hexdigest(),
        payload_size_bytes=len(encoded),
        metadata=metadata,
    )


def cleanup_queue_stats(
    queue_name: str,
    *,
    scanned: int,
    candidates: Sequence[CleanupCandidate],
    stop_reason: str | None,
    collated_tasks: tuple[SummaryLike, ...] = (),
) -> CleanupQueueStats:
    """Build cleanup stats from selected candidates."""

    reason_counts = Counter(candidate.reason for candidate in candidates)
    return CleanupQueueStats(
        queue=queue_name,
        scanned=scanned,
        selected=len(candidates),
        stop_reason=stop_reason,
        reason_counts=dict(reason_counts),
        collated_tasks=collated_tasks,
    )


def cleanup_policy_stats(
    queue_name: str,
    *,
    policy: str,
    scanned: int,
    candidates: Sequence[CleanupCandidate],
    stop_reason: str | None,
) -> CleanupPolicyStats:
    """Build cleanup stats for one policy from selected candidates."""

    reason_counts = Counter(candidate.reason for candidate in candidates)
    return CleanupPolicyStats(
        policy=policy,
        queue=queue_name,
        scanned=scanned,
        selected=len(candidates),
        stop_reason=stop_reason,
        reason_counts=dict(reason_counts),
    )


def applied_cleanup_candidate(
    candidate: CleanupCandidate,
    deleted: bool,
    error: str | None,
) -> AppliedCleanupCandidate:
    """Adapt exact-delete results to cleanup result records."""

    return AppliedCleanupCandidate(candidate=candidate, deleted=deleted, error=error)
