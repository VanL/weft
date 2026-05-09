"""Command-layer compatibility exports for retention pruning."""

from __future__ import annotations

from weft.core.pruning.retention import (
    RetentionFamily,
    RetentionPruneCandidate,
    RetentionPruneConfig,
    RetentionPruneResult,
    RetentionQueueScanStats,
    cmd_retention_prune,
    render_retention_prune_human,
    retention_prune_summary,
    run_retention_prune,
    run_retention_prune_for_context,
)

__all__ = [
    "RetentionFamily",
    "RetentionPruneCandidate",
    "RetentionPruneConfig",
    "RetentionPruneResult",
    "RetentionQueueScanStats",
    "cmd_retention_prune",
    "render_retention_prune_human",
    "retention_prune_summary",
    "run_retention_prune",
    "run_retention_prune_for_context",
]
