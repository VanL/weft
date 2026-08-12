"""Canonical command ownership for explicit broker pruning.

Core pruning modules own candidate selection and exact apply. This module owns
foreground context resolution, family dispatch, reports, rendering, and exit
classification.

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
    EXIT_ERROR,
    EXIT_SUCCESS,
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


def cmd_prune(
    *,
    family: str,
    context: Path | None = None,
    apply: bool = False,
    force: bool = False,
    queues: Sequence[str] | None = None,
    tasks: Sequence[str] | None = None,
    retention_classes: Sequence[str] | None = None,
    min_age_seconds: float = RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS,
    keep_recent_per_key: int = RUNTIME_PRUNE_DEFAULT_KEEP_RECENT_PER_KEY,
    keep_recent_per_task: int = RETENTION_PRUNE_DEFAULT_KEEP_RECENT_PER_TASK,
    limit: int | None = None,
    json_output: bool = False,
    archive_path: Path | None = None,
    report_path: Path | None = None,
) -> tuple[int, str, str]:
    """Dispatch one explicit prune family and render its command result."""

    normalized_family = family.strip()
    if normalized_family == "runtime-state":
        if force:
            return (
                EXIT_ERROR,
                "",
                "--force is only supported for retention prune families",
            )
        return _runtime_prune_command(
            context=context,
            apply=apply,
            queues=queues,
            min_age_seconds=min_age_seconds,
            keep_recent_per_key=keep_recent_per_key,
            limit=limit,
            json_output=json_output,
            report_path=report_path,
        )
    if normalized_family in {"task-local", "task-log", "retention"}:
        return _retention_prune_command(
            context=context,
            family=cast(_retention.RetentionFamily, normalized_family),
            apply=apply,
            force=force,
            tasks=tasks,
            retention_classes=retention_classes,
            min_age_seconds=min_age_seconds,
            keep_recent_per_task=keep_recent_per_task,
            limit=limit,
            json_output=json_output,
            archive_path=archive_path,
            report_path=report_path,
        )
    if normalized_family == "all":
        runtime_code, runtime_out, runtime_err = _runtime_prune_command(
            context=context,
            apply=apply,
            queues=queues,
            min_age_seconds=min_age_seconds,
            keep_recent_per_key=keep_recent_per_key,
            limit=limit,
            json_output=True,
            report_path=None,
        )
        retention_code, retention_out, retention_err = _retention_prune_command(
            context=context,
            family="retention",
            apply=apply,
            force=force,
            tasks=tasks,
            retention_classes=retention_classes,
            min_age_seconds=min_age_seconds,
            keep_recent_per_task=keep_recent_per_task,
            limit=limit,
            json_output=True,
            archive_path=archive_path,
            report_path=report_path,
        )
        exit_code = EXIT_ERROR if runtime_code or retention_code else EXIT_SUCCESS
        stdout = (
            json.dumps(
                {
                    "runtime_state": json.loads(runtime_out),
                    "retention": json.loads(retention_out),
                },
                sort_keys=True,
            )
            if json_output
            else f"Runtime state:\n{runtime_out}\nRetention:\n{retention_out}"
        )
        stderr = "\n".join(part for part in (runtime_err, retention_err) if part)
        return exit_code, stdout, stderr
    return (
        EXIT_ERROR,
        "",
        (
            "unknown prune family: "
            f"{normalized_family}; allowed: runtime-state, task-local, task-log, "
            "retention, all"
        ),
    )


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


def render_runtime_prune_human(result: _runtime.RuntimePruneResult) -> str:
    """Render concise runtime-state output for the CLI."""

    mode = "dry-run" if result.dry_run else "apply"
    lines = [
        (
            f"Runtime state prune {mode}: scanned {result.records_scanned} records, "
            f"found {len(result.candidates)} candidates, deleted {result.deleted}, "
            f"failed {result.failed}."
        )
    ]
    for candidate in result.candidates:
        applied = "report-only" if candidate.report_only else "kept"
        if not result.dry_run and candidate.applied:
            applied = "deleted"
        elif candidate.error:
            applied = f"error: {candidate.error}"
        lines.append(
            f"{candidate.queue} {candidate.message_id} {candidate.classification} "
            f"{candidate.key} {applied}"
        )
    return "\n".join(lines)


def render_retention_prune_human(result: _retention.RetentionPruneResult) -> str:
    """Render concise retention output for the CLI."""

    mode = (
        "dry-run"
        if result.dry_run
        else "force apply"
        if result.config.force
        else "apply"
    )
    lines = [
        (
            f"Retention prune {mode}: scanned {result.records_scanned} records, "
            f"found {len(result.candidates)} candidates, archived {result.archived}, "
            f"deleted {result.deleted}, failed {result.failed}."
        )
    ]
    lines.extend(f"warning: {warning}" for warning in result.warnings)
    applied_by_id = {
        (candidate.queue, candidate.message_id): candidate
        for candidate in result.applied_candidates
    }
    for candidate in result.candidates:
        visible = applied_by_id.get((candidate.queue, candidate.message_id), candidate)
        state = (
            "report-only"
            if candidate.report_only and not result.config.force
            else "kept"
        )
        if visible.applied:
            state = "deleted"
        elif visible.error:
            state = f"error: {visible.error}"
        protections = (
            f" overrides={','.join(candidate.overridden_protections)}"
            if candidate.overridden_protections
            else ""
        )
        lines.append(
            f"{candidate.queue} {candidate.message_id} {candidate.candidate_class} "
            f"{candidate.tid} {state}{protections}"
        )
    return "\n".join(lines)


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


def _runtime_prune_command(
    *,
    context: Path | None,
    apply: bool,
    queues: Sequence[str] | None,
    min_age_seconds: float,
    keep_recent_per_key: int,
    limit: int | None,
    json_output: bool,
    report_path: Path | None,
) -> tuple[int, str, str]:
    try:
        normalized_queues = normalize_queue_filters(queues)
    except ValueError as exc:
        return EXIT_ERROR, "", str(exc)
    result = run_runtime_prune(
        _runtime.RuntimePruneConfig(
            context_path=context,
            apply=apply,
            queues=normalized_queues,
            min_age_seconds=min_age_seconds,
            keep_recent_per_key=keep_recent_per_key,
            limit=limit,
        ),
        report_path=report_path,
    )
    stdout = (
        json.dumps(runtime_prune_summary(result), sort_keys=True)
        if json_output
        else render_runtime_prune_human(result)
    )
    return (
        _prune_exit_code(result.errors, result.failed),
        stdout,
        "\n".join(result.errors),
    )


def _retention_prune_command(
    *,
    context: Path | None,
    family: _retention.RetentionFamily,
    apply: bool,
    force: bool,
    tasks: Sequence[str] | None,
    retention_classes: Sequence[str] | None,
    min_age_seconds: float,
    keep_recent_per_task: int,
    limit: int | None,
    json_output: bool,
    archive_path: Path | None,
    report_path: Path | None,
) -> tuple[int, str, str]:
    result = run_retention_prune(
        _retention.RetentionPruneConfig(
            context_path=context,
            family=family,
            apply=apply,
            force=force,
            task_filters=tuple(tasks or ()),
            class_filters=tuple(retention_classes or ()),
            min_age_seconds=min_age_seconds,
            keep_recent_per_task=keep_recent_per_task,
            limit=limit,
            archive_path=archive_path,
        ),
        report_path=report_path,
    )
    stdout = (
        json.dumps(retention_prune_summary(result), sort_keys=True)
        if json_output
        else render_retention_prune_human(result)
    )
    stderr = "\n".join([*result.errors, *result.warnings])
    return _prune_exit_code(result.errors, result.failed), stdout, stderr


def _prune_exit_code(errors: Sequence[str], failed: int) -> int:
    return EXIT_ERROR if errors or failed else EXIT_SUCCESS


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
