"""TaskMonitor cleanup-policy progress contracts.

This module is deliberately policy-neutral. Individual cleanup policies own
their selection semantics; this module only provides the cached vocabulary the
TaskMonitor uses to explain base, waypoint, deferred, and blocked outcomes.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PolicyProgress:
    """Cached progress proof for one TaskMonitor cleanup policy.

    ``waypoint_reached`` means the policy yielded at a bounded stop and should
    be considered for catch-up cadence. ``base_reached`` means there is no
    eligible work for this policy at the current point in time. ``blocked`` is
    neither base nor a catch-up signal by itself.
    """

    policy: str
    domain: str
    scanned: int = 0
    selected: int = 0
    applied: int = 0
    deferred: int = 0
    source_total: int | None = None
    waypoint_reached: bool = False
    base_reached: bool = False
    blocked_reason: str | None = None
    reason_counts: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        """Validate progress-state invariants."""

        if self.base_reached and self.waypoint_reached:
            raise ValueError("policy progress cannot be both base and waypoint")
        if self.blocked_reason is not None and self.base_reached:
            raise ValueError("blocked policy progress cannot be base")
        for field_name, value in (
            ("scanned", self.scanned),
            ("selected", self.selected),
            ("applied", self.applied),
            ("deferred", self.deferred),
        ):
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.source_total is not None and self.source_total < 0:
            raise ValueError("source_total must be non-negative")

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe cached PONG/log summary."""

        summary: dict[str, Any] = {
            "policy": self.policy,
            "domain": self.domain,
            "scanned": self.scanned,
            "selected": self.selected,
            "applied": self.applied,
            "deferred": self.deferred,
            "source_total": self.source_total,
            "waypoint_reached": self.waypoint_reached,
            "base_reached": self.base_reached,
            "blocked_reason": self.blocked_reason,
            "reason_counts": dict(self.reason_counts or {}),
        }
        return summary

    def with_apply_result(
        self,
        *,
        applied: int,
        blocked_reason: str | None = None,
    ) -> PolicyProgress:
        """Return a copy with destructive-apply results attached."""

        return PolicyProgress(
            policy=self.policy,
            domain=self.domain,
            scanned=self.scanned,
            selected=self.selected,
            applied=applied,
            deferred=self.deferred,
            source_total=self.source_total,
            waypoint_reached=self.waypoint_reached,
            base_reached=self.base_reached and blocked_reason is None,
            blocked_reason=blocked_reason or self.blocked_reason,
            reason_counts=self.reason_counts,
        )


def progress_requires_catchup(progresses: Sequence[PolicyProgress]) -> bool:
    """Return whether any policy reached a bounded catch-up waypoint."""

    return any(progress.waypoint_reached for progress in progresses)


def progress_summaries(
    progresses: Sequence[PolicyProgress],
) -> tuple[dict[str, Any], ...]:
    """Return JSON-safe summaries for cached PONG/log fields."""

    return tuple(progress.to_summary() for progress in progresses)
