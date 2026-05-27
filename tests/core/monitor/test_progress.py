"""Tests for TaskMonitor policy progress summaries."""

from __future__ import annotations

import pytest

from weft._constants import TASK_MONITOR_POLICY_TASK_LOG_RETENTION
from weft.core.monitor.progress import PolicyProgress, progress_requires_catchup


def test_policy_progress_summary_is_json_safe() -> None:
    progress = PolicyProgress(
        policy=TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        domain="weft.log.tasks",
        scanned=10,
        selected=2,
        applied=2,
        deferred=1,
        source_total=12,
        waypoint_reached=True,
        reason_counts={"invalid_json": 2},
    )

    assert progress.to_summary() == {
        "policy": TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        "domain": "weft.log.tasks",
        "scanned": 10,
        "selected": 2,
        "applied": 2,
        "deferred": 1,
        "source_total": 12,
        "waypoint_reached": True,
        "base_reached": False,
        "blocked_reason": None,
        "reason_counts": {"invalid_json": 2},
    }


def test_policy_progress_rejects_base_and_waypoint() -> None:
    with pytest.raises(ValueError, match="both base and waypoint"):
        PolicyProgress(
            policy="p",
            domain="d",
            waypoint_reached=True,
            base_reached=True,
        )


def test_policy_progress_rejects_blocked_base() -> None:
    with pytest.raises(ValueError, match="blocked policy progress cannot be base"):
        PolicyProgress(
            policy="p",
            domain="d",
            base_reached=True,
            blocked_reason="delete failed",
        )


def test_progress_requires_catchup_uses_waypoint_only() -> None:
    assert (
        progress_requires_catchup(
            (
                PolicyProgress(policy="base", domain="d", base_reached=True),
                PolicyProgress(policy="blocked", domain="d", blocked_reason="failed"),
            )
        )
        is False
    )
    assert (
        progress_requires_catchup(
            (PolicyProgress(policy="waypoint", domain="d", waypoint_reached=True),)
        )
        is True
    )
