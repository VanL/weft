"""Shared internal result types for TaskMonitor cleanup policies.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
"""

from __future__ import annotations

from dataclasses import dataclass

from weft.core.monitor.progress import PolicyProgress
from weft.core.pruning.models import (
    AppliedCleanupCandidate,
    CleanupCandidate,
    CleanupPolicyStats,
    CleanupQueueStats,
)


@dataclass(frozen=True, slots=True)
class CleanupPolicyRun:
    """Internal result for one ordered cleanup-policy run."""

    candidates: tuple[CleanupCandidate, ...] = ()
    applied_candidates: tuple[AppliedCleanupCandidate, ...] = ()
    queue_stats: tuple[CleanupQueueStats, ...] = ()
    policy_stats: tuple[CleanupPolicyStats, ...] = ()
    policy_progress: tuple[PolicyProgress, ...] = ()
    errors: tuple[str, ...] = ()
