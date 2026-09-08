"""Canonical command ownership for explicit broker pruning.

Core pruning modules own candidate selection and exact apply. This module owns
foreground context resolution, family dispatch, reports, and typed outcomes.
The CLI adapter owns human rendering and exit classification.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/10-CLI_Interface.md [CLI-6]
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

from simplebroker import format_message_id
from weft._constants import (
    RETENTION_PRUNE_DEFAULT_KEEP_RECENT_PER_TASK,
    RETENTION_PRUNE_SCHEMA_VERSION,
    RUNTIME_PRUNE_DEFAULT_KEEP_RECENT_PER_KEY,
    RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS,
    RUNTIME_PRUNE_DEFAULT_QUEUE_GROUPS,
    RUNTIME_PRUNE_SCHEMA_VERSION,
    RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS,
)
from weft._exceptions import CommandExecutionError, CommandUsageError
from weft.commands.types import SystemPruneResult
from weft.context import build_context
from weft.core.pruning import retention as _retention
from weft.core.pruning import runtime as _runtime

from ._boundary import typed_command_errors


def run_runtime_prune(
    config: _runtime.RuntimePruneConfig,
    *,
    report_path: Path | None = None,
) -> _runtime.RuntimePruneResult:
    """Run runtime-state pruning against the configured project context."""

    context = build_context(spec_context=config.context_path)
    result = _runtime.run_runtime_prune_for_context(context, config)
    # Preserve the two families' established report boundaries. Runtime-state
    # reports start only after a successful initial scan, while retention
    # reports include scan/archive failures and suppress output only when
    # validation prevented a run from starting.
    if report_path is None or result.halted_at is not None:
        return result
    try:
        write_runtime_prune_report(result, report_path)
    except OSError as exc:
        return replace(
            result,
            errors=(*result.errors, f"failed to write report: {exc}"),
        )
    return result


def run_retention_prune(
    config: _retention.RetentionPruneConfig,
    *,
    report_path: Path | None = None,
) -> _retention.RetentionPruneResult:
    """Run retention pruning against the configured project context."""

    context = build_context(spec_context=config.context_path)
    result = _retention.run_retention_prune_for_context(context, config)
    if report_path is None or result.halted_at == "validation":
        return result
    try:
        _retention.write_retention_prune_report(result, report_path)
    except OSError as exc:
        return replace(
            result,
            errors=(*result.errors, f"failed to write report: {exc}"),
        )
    return result


def _validate_public_prune_options(
    *,
    family: str,
    apply: bool,
    force: bool,
    min_age: float,
    keep_recent_per_key: int,
    keep_recent_per_task: int,
    limit: int | None,
    archive: Path | None,
) -> str:
    """Validate caller-owned prune options before backend scanning."""

    normalized = family.strip()
    allowed = {"runtime-state", "task-local", "task-log", "retention", "all"}
    if normalized not in allowed:
        raise CommandUsageError(
            f"unknown prune family: {normalized}; allowed: "
            "runtime-state, task-local, task-log, retention, all"
        )
    if normalized == "runtime-state" and force:
        raise CommandUsageError(
            "--force is only supported for retention prune families"
        )
    if force and not apply:
        raise CommandUsageError("--force requires --apply")
    if min_age < 0:
        raise CommandUsageError("--min-age must be >= 0")
    if limit is not None and limit < 1:
        raise CommandUsageError("--limit must be >= 1")
    if normalized in {"runtime-state", "all"} and keep_recent_per_key < 1:
        raise CommandUsageError("--keep-recent-per-key must be >= 1")
    if normalized in {"task-local", "task-log", "retention", "all"} and (
        keep_recent_per_task < 1
    ):
        raise CommandUsageError("--keep-recent-per-task must be >= 1")
    if (
        apply
        and normalized in {"task-local", "task-log", "retention", "all"}
        and not force
        and archive is None
    ):
        raise CommandUsageError(
            "--archive is required with --apply unless --force is used"
        )
    return normalized


@typed_command_errors
def cmd_system_prune(
    *,
    family: str,
    context: Path | None = None,
    apply: bool = False,
    force: bool = False,
    queue: Sequence[str] | None = None,
    min_age: float = RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS,
    keep_recent_per_key: int = RUNTIME_PRUNE_DEFAULT_KEEP_RECENT_PER_KEY,
    keep_recent_per_task: int = RETENTION_PRUNE_DEFAULT_KEEP_RECENT_PER_TASK,
    task: Sequence[str] | None = None,
    retention_class: Sequence[str] | None = None,
    archive: Path | None = None,
    limit: int | None = None,
    report: Path | None = None,
) -> SystemPruneResult:
    """Prune one or all supported state families and return full details.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    normalized = _validate_public_prune_options(
        family=family,
        apply=apply,
        force=force,
        min_age=min_age,
        keep_recent_per_key=keep_recent_per_key,
        keep_recent_per_task=keep_recent_per_task,
        limit=limit,
        archive=archive,
    )

    details: dict[str, Any] = {}
    candidates = deleted = failed = 0
    families: tuple[str, ...]
    try:
        if normalized in {"runtime-state", "all"}:
            runtime_result = run_runtime_prune(
                _runtime.RuntimePruneConfig(
                    context_path=context,
                    apply=apply,
                    queues=normalize_queue_filters(queue),
                    min_age_seconds=min_age,
                    keep_recent_per_key=keep_recent_per_key,
                    limit=limit,
                ),
                report_path=report if normalized == "runtime-state" else None,
            )
            details["runtime_state"] = {
                **runtime_prune_summary(runtime_result),
                "config": asdict(runtime_result.config),
                "candidates_detail": tuple(
                    asdict(candidate) for candidate in runtime_result.candidates
                ),
                "applied_candidates": tuple(
                    asdict(candidate) for candidate in runtime_result.applied_candidates
                ),
                "scan_stats": tuple(asdict(stat) for stat in runtime_result.scan_stats),
                "halted_at": runtime_result.halted_at,
                "report_path": report if normalized == "runtime-state" else None,
            }
            candidates += len(runtime_result.candidates)
            deleted += runtime_result.deleted
            failed += runtime_result.failed
        if normalized in {"task-local", "task-log", "retention", "all"}:
            retention_family = "retention" if normalized == "all" else normalized
            retention_result = run_retention_prune(
                _retention.RetentionPruneConfig(
                    context_path=context,
                    family=cast(_retention.RetentionFamily, retention_family),
                    apply=apply,
                    force=force,
                    task_filters=tuple(task or ()),
                    class_filters=tuple(retention_class or ()),
                    min_age_seconds=min_age,
                    keep_recent_per_task=keep_recent_per_task,
                    limit=limit,
                    archive_path=archive,
                ),
                report_path=report,
            )
            details["retention"] = {
                **retention_prune_summary(retention_result),
                "config": asdict(retention_result.config),
                "candidates_detail": tuple(
                    asdict(candidate) for candidate in retention_result.candidates
                ),
                "applied_candidates": tuple(
                    asdict(candidate)
                    for candidate in retention_result.applied_candidates
                ),
                "scan_stats": tuple(
                    asdict(stat) for stat in retention_result.scan_stats
                ),
                "halted_at": retention_result.halted_at,
                "archive_path": archive,
                "report_path": report,
            }
            candidates += len(retention_result.candidates)
            deleted += retention_result.deleted
            failed += retention_result.failed
    except ValueError as exc:
        raise CommandUsageError(str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise CommandExecutionError(str(exc)) from exc

    families = ("runtime-state", "retention") if normalized == "all" else (normalized,)
    return SystemPruneResult(
        families=families,
        applied=apply,
        candidates=candidates,
        deleted=deleted,
        failed=failed,
        details=details,
    )


def normalize_queue_filters(
    values: Sequence[str] | None,
) -> tuple[_runtime.RuntimeQueueName, ...]:
    """Normalize CLI queue filters and reject unknown values."""

    if not values:
        return cast(
            tuple[_runtime.RuntimeQueueName, ...],
            RUNTIME_PRUNE_DEFAULT_QUEUE_GROUPS,
        )
    normalized = [
        part.strip()
        for raw_value in values
        for part in raw_value.split(",")
        if part.strip()
    ]
    if not normalized or "all" in normalized:
        return cast(
            tuple[_runtime.RuntimeQueueName, ...],
            RUNTIME_PRUNE_DEFAULT_QUEUE_GROUPS,
        )
    unknown = sorted(
        {
            value
            for value in normalized
            if value not in RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS
        }
    )
    if unknown:
        allowed = ", ".join([*RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS, "all"])
        raise ValueError(
            f"unknown runtime-state queue filter: {', '.join(unknown)}; allowed: {allowed}"
        )
    return tuple(
        dict.fromkeys(cast(_runtime.RuntimeQueueName, value) for value in normalized)
    )


def runtime_prune_summary(result: _runtime.RuntimePruneResult) -> dict[str, Any]:
    """Return the JSON summary contract for a runtime-state prune run."""

    return {
        "schema_version": RUNTIME_PRUNE_SCHEMA_VERSION,
        "record_type": "runtime_prune_completed",
        "run_id": result.run_id,
        "dry_run": result.dry_run,
        "queues_scanned": [stat.queue for stat in result.scan_stats],
        "records_scanned": result.records_scanned,
        "candidates": len(result.candidates),
        "deleted": result.deleted,
        "failed": result.failed,
        "classification_counts": result.classification_counts,
        "errors": list(result.errors),
    }


def retention_prune_summary(result: _retention.RetentionPruneResult) -> dict[str, Any]:
    """Return the JSON summary contract for a retention prune run."""

    return {
        "schema_version": RETENTION_PRUNE_SCHEMA_VERSION,
        "record_type": "retention_prune_completed",
        "run_id": result.run_id,
        "family": result.config.family,
        "dry_run": result.dry_run,
        "force": result.config.force,
        "queues_scanned": [stat.queue for stat in result.scan_stats],
        "records_scanned": result.records_scanned,
        "candidates": len(result.candidates),
        "archived": result.archived,
        "deleted": result.deleted,
        "failed": result.failed,
        "candidate_class_counts": result.candidate_class_counts,
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }


def write_runtime_prune_report(
    result: _runtime.RuntimePruneResult,
    path: Path,
) -> None:
    """Write runtime candidates and their final summary as JSONL."""

    path.parent.mkdir(parents=True, exist_ok=True)
    applied_by_id = {
        (candidate.queue, candidate.message_id): candidate
        for candidate in result.applied_candidates
    }
    with path.open("w", encoding="utf-8") as handle:
        for candidate in result.candidates:
            visible = applied_by_id.get(
                (candidate.queue, candidate.message_id), candidate
            )
            handle.write(
                json.dumps(_runtime_candidate_record(result, visible), sort_keys=True)
            )
            handle.write("\n")
        handle.write(json.dumps(runtime_prune_summary(result), sort_keys=True))
        handle.write("\n")


def _runtime_candidate_record(
    result: _runtime.RuntimePruneResult,
    candidate: _runtime.RuntimePruneCandidate,
) -> dict[str, Any]:
    return {
        "schema_version": RUNTIME_PRUNE_SCHEMA_VERSION,
        "record_type": "runtime_prune_candidate",
        "run_id": result.run_id,
        "emitted_at": time.time_ns(),
        "queue": candidate.queue,
        "message_id": format_message_id(candidate.message_id),
        "key": candidate.key,
        "classification": candidate.classification,
        "reason": candidate.reason,
        "age_seconds": candidate.age_seconds,
        "dry_run": result.dry_run,
        "applied": candidate.applied,
        "error": candidate.error,
        "payload_excerpt": candidate.payload_excerpt,
    }
