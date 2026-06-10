"""TaskMonitor-owned cleanup orchestration.

TaskMonitor decides when cleanup runs and whether the cycle is destructive.
Reusable pruning policies live under ``weft.core.pruning`` and task-log
grouping lives in ``weft.core.monitor.task_log_collation``. The monitor's
separate default-on self-maintenance pass (backend vacuum plus runtime-state
pruning of the non-tid-mapping groups) runs through
``weft.core.pruning.runtime``, not through this module.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from simplebroker.ext import BrokerError
from weft._constants import (
    TASK_MONITOR_TASK_LOG_CLEANUP_SKIPPED_OWNER,
    TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS_DEFAULT,
    WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
    WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT_DEFAULT,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext
from weft.core.monitor.policies.task_log import (
    peek_rows_including_claimed as _peek_rows_including_claimed,
)
from weft.core.monitor.policies.task_log import (
    task_log_candidates,
)
from weft.core.monitor.policies.tid_mapping import (
    decode_tid_mapping_row,
    tid_mapping_candidates,
)
from weft.core.monitor.progress import PolicyProgress
from weft.core.monitor.task_log_scanner import (
    GeneratorTaskLogScanner,
    TaskLogScanBackend,
)
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.core.pruning.models import (
    AppliedCleanupCandidate,
    CleanupCandidate,
    CleanupPolicyStats,
    CleanupQueueStats,
    applied_cleanup_candidate,
)
from weft.core.queue_window import (
    QueueWindowRow,
    scan_queue_window,
)

PreApplyReporter = Callable[
    [Sequence[CleanupCandidate]],
    tuple[AppliedCleanupCandidate, ...],
]


@dataclass(frozen=True, slots=True)
class TaskMonitorCleanupConfig:
    """Configuration for one TaskMonitor cleanup pass."""

    batch_size: int = WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT
    task_log_scan_limit: int = WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT_DEFAULT
    tid_mapping_min_age_seconds: float = (
        TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS
    )
    task_log_min_age_seconds: float = WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS_DEFAULT
    task_log_cleanup_enabled: bool = True
    reserved_cleanup_enabled: bool = False
    queues: tuple[str, ...] = (WEFT_TID_MAPPINGS_QUEUE, WEFT_GLOBAL_LOG_QUEUE)
    task_log_scan_backend: TaskLogScanBackend | None = None
    pre_apply_reporter: PreApplyReporter | None = None


@dataclass(frozen=True, slots=True)
class TaskMonitorCleanupResult:
    """Result of one TaskMonitor cleanup pass."""

    success: bool
    candidates: tuple[CleanupCandidate, ...] = ()
    applied_candidates: tuple[AppliedCleanupCandidate, ...] = ()
    queue_stats: tuple[CleanupQueueStats, ...] = ()
    policy_stats: tuple[CleanupPolicyStats, ...] = ()
    policy_progress: tuple[PolicyProgress, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def processed(self) -> int:
        """Number of selected cleanup candidates."""

        return len(self.candidates)

    @property
    def deleted(self) -> int:
        """Number of rows deleted by this pass."""

        return sum(1 for candidate in self.applied_candidates if candidate.deleted)

    @property
    def reported(self) -> int:
        """Number of candidates reported without deletion."""

        if self.applied_candidates:
            return sum(
                1 for candidate in self.applied_candidates if not candidate.deleted
            )
        return len(self.candidates)

    @property
    def records_scanned(self) -> int:
        """Number of raw broker rows scanned by this pass."""

        return sum(stat.scanned for stat in self.queue_stats)

    def queue_stats_summary(self) -> tuple[dict[str, Any], ...]:
        """Return JSON-safe queue stats for operational logs."""

        return tuple(stat.to_summary() for stat in self.queue_stats)

    def policy_stats_summary(self) -> tuple[dict[str, Any], ...]:
        """Return JSON-safe policy stats for operational logs."""

        return tuple(stat.to_summary() for stat in self.policy_stats)

    def policy_progress_summary(self) -> tuple[dict[str, Any], ...]:
        """Return JSON-safe policy progress for operational logs."""

        return tuple(progress.to_summary() for progress in self.policy_progress)


def run_task_monitor_cleanup(
    ctx: WeftContext,
    config: TaskMonitorCleanupConfig,
    *,
    apply: bool,
    exclude_tids: Iterable[str] = (),
    now_ns: int | None = None,
) -> TaskMonitorCleanupResult:
    """Run one TaskMonitor cleanup pass.

    Spec: [MF-5], [OBS.13], [OBS.16], [OBS.17]
    """

    if config.batch_size <= 0:
        return TaskMonitorCleanupResult(
            success=False,
            errors=("task monitor cleanup batch_size must be positive",),
        )
    if config.task_log_scan_limit <= 0:
        return TaskMonitorCleanupResult(
            success=False,
            errors=("task monitor cleanup task_log_scan_limit must be positive",),
        )

    current_ns = time.time_ns() if now_ns is None else now_ns
    excluded = {tid for tid in exclude_tids if tid}
    candidates: list[CleanupCandidate] = []
    applied: list[AppliedCleanupCandidate] = []
    queue_stats: list[CleanupQueueStats] = []
    policy_stats: list[CleanupPolicyStats] = []
    policy_progress: list[PolicyProgress] = []
    errors: list[str] = []
    task_log_scan_backend = config.task_log_scan_backend or GeneratorTaskLogScanner()

    for queue_name in config.queues:
        try:
            if queue_name == WEFT_GLOBAL_LOG_QUEUE:
                if not config.task_log_cleanup_enabled:
                    queue_stats.append(
                        CleanupQueueStats(
                            queue=queue_name,
                            scanned=0,
                            selected=0,
                            stop_reason=TASK_MONITOR_TASK_LOG_CLEANUP_SKIPPED_OWNER,
                        )
                    )
                    continue
                task_log_window = task_log_scan_backend.scan_window(
                    ctx,
                    queue_name,
                    scan_limit=config.task_log_scan_limit,
                )
                policy_run = task_log_candidates(
                    ctx,
                    task_log_window,
                    batch_size=config.batch_size,
                    min_age_seconds=config.task_log_min_age_seconds,
                    reserved_cleanup_enabled=config.reserved_cleanup_enabled,
                    now_ns=current_ns,
                    exclude_tids=excluded,
                    apply_candidates=lambda selected: _apply_policy_candidates(
                        ctx,
                        selected,
                        apply=apply,
                        pre_apply_reporter=config.pre_apply_reporter,
                    ),
                    peek_rows_including_claimed_fn=_peek_rows_including_claimed,
                    scan_queue_window_fn=scan_queue_window,
                )
                selected = list(policy_run.candidates)
                applied.extend(policy_run.applied_candidates)
                queue_stat_records = policy_run.queue_stats
                policy_stat_records = policy_run.policy_stats
                policy_progress_records = policy_run.policy_progress
                errors.extend(policy_run.errors)
            else:
                rows = scan_queue_window(ctx, queue_name, limit=config.batch_size)
                (
                    selected,
                    queue_stat_records,
                    policy_stat_records,
                    policy_progress_records,
                ) = _select_queue_candidates(
                    queue_name,
                    rows,
                    config=config,
                    now_ns=current_ns,
                    exclude_tids=excluded,
                )
                applied.extend(
                    _apply_policy_candidates(
                        ctx,
                        selected,
                        apply=apply,
                        pre_apply_reporter=config.pre_apply_reporter,
                    )
                )
        except (BrokerError, OSError, RuntimeError, ValueError) as exc:
            errors.append(f"failed to scan {queue_name}: {exc}")
            continue
        candidates.extend(selected)
        queue_stats.extend(queue_stat_records)
        policy_stats.extend(policy_stat_records)
        policy_progress.extend(policy_progress_records)

    applied_tuple = tuple(applied)
    final_queue_stats = tuple(
        _with_apply_counts(queue_stats, applied_tuple, report_only=not apply)
    )
    final_policy_stats = tuple(
        _with_policy_apply_counts(policy_stats, applied_tuple, report_only=not apply)
    )
    final_policy_progress = tuple(
        _with_policy_progress_apply_counts(
            policy_progress,
            applied_tuple,
            report_only=not apply,
        )
    )
    apply_errors = tuple(
        candidate.error for candidate in applied_tuple if candidate.error is not None
    )
    return TaskMonitorCleanupResult(
        success=not errors and not apply_errors,
        candidates=tuple(candidates),
        applied_candidates=applied_tuple,
        queue_stats=final_queue_stats,
        policy_stats=final_policy_stats,
        policy_progress=final_policy_progress,
        errors=(*errors, *apply_errors),
    )


def _select_queue_candidates(
    queue_name: str,
    rows: Sequence[QueueWindowRow],
    *,
    config: TaskMonitorCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
) -> tuple[
    list[CleanupCandidate],
    tuple[CleanupQueueStats, ...],
    tuple[CleanupPolicyStats, ...],
    tuple[PolicyProgress, ...],
]:
    if queue_name == WEFT_TID_MAPPINGS_QUEUE:
        decoded = tuple(decode_tid_mapping_row(row) for row in rows)
        candidates, queue_stats, policy_stats, policy_progress = tid_mapping_candidates(
            decoded,
            now_ns=now_ns,
            min_age_seconds=config.tid_mapping_min_age_seconds,
            exclude_tids=exclude_tids,
            scan_limit_reached=len(rows) >= config.batch_size,
        )
        return candidates, (queue_stats,), policy_stats, policy_progress
    return (
        [],
        (
            CleanupQueueStats(
                queue=queue_name,
                scanned=len(rows),
                selected=0,
                stop_reason="unsupported_queue",
            ),
        ),
        (),
        (),
    )


def _apply_policy_candidates(
    ctx: WeftContext,
    candidates: Sequence[CleanupCandidate],
    *,
    apply: bool,
    pre_apply_reporter: PreApplyReporter | None = None,
) -> tuple[AppliedCleanupCandidate, ...]:
    """Apply one ordered policy's exact-delete candidates."""

    if not apply or not candidates:
        return ()
    if pre_apply_reporter is not None:
        report_failures = pre_apply_reporter(candidates)
        if report_failures:
            return tuple(report_failures)
    return tuple(
        apply_exact_prune_candidates(
            ctx,
            candidates,
            apply_result=applied_cleanup_candidate,
        )
    )


def _with_apply_counts(
    stats: Sequence[CleanupQueueStats],
    applied: Sequence[AppliedCleanupCandidate],
    *,
    report_only: bool,
) -> list[CleanupQueueStats]:
    deleted_by_queue = Counter(
        result.candidate.queue for result in applied if result.deleted
    )
    reported_by_queue: Counter[str] = Counter()
    if report_only:
        reported_by_queue.update({stat.queue: stat.selected for stat in stats})
    elif applied:
        reported_by_queue.update(
            result.candidate.queue for result in applied if not result.deleted
        )
    return [
        CleanupQueueStats(
            queue=stat.queue,
            scanned=stat.scanned,
            selected=stat.selected,
            deleted=deleted_by_queue[stat.queue],
            reported=reported_by_queue[stat.queue],
            stop_reason=stat.stop_reason,
            reason_counts=dict(stat.reason_counts),
            collated_tasks=stat.collated_tasks,
            metadata=stat.metadata,
        )
        for stat in stats
    ]


def _with_policy_apply_counts(
    stats: Sequence[CleanupPolicyStats],
    applied: Sequence[AppliedCleanupCandidate],
    *,
    report_only: bool,
) -> list[CleanupPolicyStats]:
    deleted_by_policy_queue = Counter(
        (result.candidate.policy, result.candidate.queue)
        for result in applied
        if result.deleted
    )
    reported_by_policy_queue: Counter[tuple[str, str]] = Counter()
    if report_only:
        reported_by_policy_queue.update(
            {
                (stat.policy, stat.queue): stat.selected
                for stat in stats
                if stat.selected
            }
        )
    elif applied:
        reported_by_policy_queue.update(
            (result.candidate.policy, result.candidate.queue)
            for result in applied
            if not result.deleted
        )
    return [
        CleanupPolicyStats(
            policy=stat.policy,
            queue=stat.queue,
            scanned=stat.scanned,
            selected=stat.selected,
            deleted=deleted_by_policy_queue[(stat.policy, stat.queue)],
            reported=reported_by_policy_queue[(stat.policy, stat.queue)],
            stop_reason=stat.stop_reason,
            reason_counts=dict(stat.reason_counts),
        )
        for stat in stats
    ]


def _with_policy_progress_apply_counts(
    progresses: Sequence[PolicyProgress],
    applied: Sequence[AppliedCleanupCandidate],
    *,
    report_only: bool,
) -> list[PolicyProgress]:
    """Attach apply counts to policy-level progress records."""

    applied_by_policy_queue = Counter(
        (result.candidate.policy, result.candidate.queue)
        for result in applied
        if result.deleted
    )
    applied_by_policy = Counter(
        result.candidate.policy for result in applied if result.deleted
    )
    error_by_policy_queue: dict[tuple[str, str], str] = {}
    error_by_policy: dict[str, str] = {}
    for result in applied:
        if result.error is not None:
            error_by_policy_queue.setdefault(
                (result.candidate.policy, result.candidate.queue),
                result.error,
            )
            error_by_policy.setdefault(result.candidate.policy, result.error)

    updated: list[PolicyProgress] = []
    for progress in progresses:
        key = (progress.policy, progress.domain)
        if report_only:
            applied_count = progress.selected
        else:
            applied_count = applied_by_policy_queue[key]
            if key not in applied_by_policy_queue and progress.selected:
                applied_count = min(
                    applied_by_policy[progress.policy],
                    progress.selected,
                )
        blocked_reason = error_by_policy_queue.get(key)
        if blocked_reason is None and progress.selected:
            blocked_reason = error_by_policy.get(progress.policy)
        updated.append(
            progress.with_apply_result(
                applied=applied_count,
                blocked_reason=blocked_reason,
            )
        )
    return updated
