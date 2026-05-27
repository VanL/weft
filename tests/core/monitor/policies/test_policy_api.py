"""Tests for the TaskMonitor cleanup policy API."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft._constants import (
    TASK_MONITOR_CLEANUP_POLICY_NAMES,
    TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE,
    TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
    TASK_MONITOR_POLICY_TASK_LOCAL_DEAD_TID,
    TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME,
    TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
)
from weft.core.monitor.policies.api import (
    CleanupPolicyResult,
    cleanup_policy_names,
    policy_result_to_progress,
)

pytestmark = [pytest.mark.shared]


OLD_CLEANUP_POLICY_NAMES = (
    "task_log.retained_fifo_ingest",
    "task_log.delete_malformed",
    "task_log.delete_claimed",
    "task_log.collate_complete_lifecycle",
    "task_log.collate_terminal_without_start",
    "task_log.delete_old_without_start",
    "task_log.external_raw",
    "monitor_store.summary_disposition",
    "monitor_store.raw_ref_delete",
    "monitor_store.raw_deleted_child_ref_repair",
    "monitor_store.child_tombstone_prune",
    "monitor_store.family_retirement",
    "monitor_store.orphan_raw_recovery",
    "runtime.terminal_control_cleanup",
    "runtime.reserved_cleanup",
    "runtime.dead_tid_cleanup",
    "runtime.dead_task_log_coalesce",
    "tid_mapping.delete_malformed",
    "tid_mapping.delete_older_than",
)


def test_cleanup_policy_name_set_is_the_five_top_level_policies() -> None:
    """The monitor should expose policy owners, not private cleanup phases."""

    assert tuple(cleanup_policy_names()) == (
        TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE,
        TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME,
        TASK_MONITOR_POLICY_TASK_LOCAL_DEAD_TID,
        TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
    )
    assert TASK_MONITOR_CLEANUP_POLICY_NAMES == tuple(cleanup_policy_names())


def test_policy_result_to_progress_preserves_common_fields() -> None:
    """Every policy result uses the same cached progress contract."""

    result = CleanupPolicyResult(
        policy=TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        domain="weft.log.tasks",
        scanned=10,
        selected=3,
        applied=2,
        deferred=1,
        source_total=4,
        waypoint_reached=True,
        reason_counts={"valid_ingested": 3},
    )

    progress = policy_result_to_progress(result)

    assert progress.policy == TASK_MONITOR_POLICY_TASK_LOG_RETENTION
    assert progress.domain == "weft.log.tasks"
    assert progress.scanned == 10
    assert progress.selected == 3
    assert progress.applied == 2
    assert progress.deferred == 1
    assert progress.source_total == 4
    assert progress.waypoint_reached is True
    assert progress.reason_counts == {"valid_ingested": 3}


def test_policy_result_to_progress_rejects_unknown_policy() -> None:
    """A new policy name should require an explicit top-level decision."""

    result = CleanupPolicyResult(
        policy="not.a.policy",  # type: ignore[arg-type]
        domain="weft.log.tasks",
    )

    with pytest.raises(ValueError, match="unknown cleanup policy"):
        policy_result_to_progress(result)


def test_old_cleanup_policy_names_are_removed_from_active_code_and_tests() -> None:
    """Old phase names should only survive in historical plan documents."""

    repo_root = Path(__file__).resolve().parents[4]
    scanned_roots = (repo_root / "weft", repo_root / "tests")
    this_file = Path(__file__).resolve()
    offenders: list[str] = []
    for root in scanned_roots:
        for path in root.rglob("*.py"):
            if path == this_file or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for old_name in OLD_CLEANUP_POLICY_NAMES:
                if old_name in text:
                    offenders.append(f"{path.relative_to(repo_root)}: {old_name}")

    assert offenders == []
