"""Explicit task-local and task-log retention pruning.

This module implements foreground, archive-backed pruning for task-local
queues and selected lifecycle-log rows. It is intentionally separate from
runtime-state pruning because task-local queues contain recovery material and
need different safety rules.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.14], [OBS.16]
- docs/specifications/10-CLI_Interface.md [CLI-6]
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_KILL,
    CONTROL_PING,
    CONTROL_STATUS,
    CONTROL_STOP,
    EXIT_ERROR,
    EXIT_SUCCESS,
    QUEUE_INBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    RETENTION_PRUNE_CLASS_CLAIMED_OUTBOX_RESIDUE_FORCE,
    RETENTION_PRUNE_CLASS_NONTERMINAL_TASK_LOG_SUPERSEDED,
    RETENTION_PRUNE_CLASS_OBSOLETE_CTRL_IN_CONTROL,
    RETENTION_PRUNE_CLASS_OBSOLETE_INBOX_WORK,
    RETENTION_PRUNE_CLASS_OBSOLETE_RESERVED_WORK,
    RETENTION_PRUNE_CLASS_RESULT_WITHOUT_TERMINAL_OUTBOX_REPORTED,
    RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_ARCHIVED,
    RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_WITHOUT_LOG_REPORTED,
    RETENTION_PRUNE_CLASS_TERMINAL_RESULT_OUTBOX_ARCHIVED,
    RETENTION_PRUNE_CLASS_TERMINAL_TASK_LOG_SUPERSEDED,
    RETENTION_PRUNE_CLASS_UNSUPPORTED_TASK_LOCAL_SHAPE,
    RETENTION_PRUNE_DEFAULT_KEEP_RECENT_PER_TASK,
    RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS,
    RETENTION_PRUNE_LOG_SUBDIR,
    RETENTION_PRUNE_REPORT_ONLY_CLASSES,
    RETENTION_PRUNE_SCHEMA_VERSION,
    RETENTION_PRUNE_SUPPORTED_CLASSES,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.task_evidence import (
    coerce_terminal_envelope,
    control_queue_names_for_tid,
    peek_final_outbox_evidence,
    peek_terminal_ctrl_out_evidence,
    queue_names_for_tid,
    terminal_status_from_event,
)
from weft.helpers import iter_queue_entries, iter_queue_json_entries

RetentionFamily = Literal["task-local", "task-log", "retention"]


@dataclass(frozen=True, slots=True)
class RetentionPruneConfig:
    """Configuration for one retention prune scan."""

    context_path: Path | None = None
    family: RetentionFamily = "retention"
    apply: bool = False
    force: bool = False
    task_filters: tuple[str, ...] = ()
    class_filters: tuple[str, ...] = ()
    min_age_seconds: float = RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS
    keep_recent_per_task: int = RETENTION_PRUNE_DEFAULT_KEEP_RECENT_PER_TASK
    limit: int | None = None
    json_output: bool = False
    archive_path: Path | None = None
    report_path: Path | None = None


@dataclass(frozen=True, slots=True)
class RetentionPruneCandidate:
    """One exact broker row selected by retention prune rules."""

    queue: str
    family: RetentionFamily
    message_id: int
    tid: str
    candidate_class: str
    reason: str
    age_seconds: float
    payload_sha256: str
    payload_size_bytes: int
    payload_excerpt: str | None = None
    payload: Any | None = None
    report_only: bool = False
    force_only: bool = False
    overridden_protections: tuple[str, ...] = ()
    applied: bool = False
    error: str | None = None

    def for_apply_result(
        self,
        *,
        applied: bool,
        error: str | None = None,
    ) -> RetentionPruneCandidate:
        """Return this candidate with apply result fields updated."""

        return RetentionPruneCandidate(
            queue=self.queue,
            family=self.family,
            message_id=self.message_id,
            tid=self.tid,
            candidate_class=self.candidate_class,
            reason=self.reason,
            age_seconds=self.age_seconds,
            payload_sha256=self.payload_sha256,
            payload_size_bytes=self.payload_size_bytes,
            payload_excerpt=self.payload_excerpt,
            payload=self.payload,
            report_only=self.report_only,
            force_only=self.force_only,
            overridden_protections=self.overridden_protections,
            applied=applied,
            error=error,
        )


@dataclass(frozen=True, slots=True)
class RetentionQueueScanStats:
    """Scan counters for one retention queue."""

    queue: str
    scanned: int = 0
    candidates: int = 0


@dataclass(frozen=True, slots=True)
class RetentionPruneResult:
    """Result of one retention prune command."""

    config: RetentionPruneConfig
    run_id: str
    candidates: tuple[RetentionPruneCandidate, ...]
    applied_candidates: tuple[RetentionPruneCandidate, ...]
    scan_stats: tuple[RetentionQueueScanStats, ...]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    archived: int = 0

    @property
    def dry_run(self) -> bool:
        return not self.config.apply

    @property
    def records_scanned(self) -> int:
        return sum(stat.scanned for stat in self.scan_stats)

    @property
    def deleted(self) -> int:
        return sum(1 for candidate in self.applied_candidates if candidate.applied)

    @property
    def failed(self) -> int:
        return sum(1 for candidate in self.applied_candidates if candidate.error)

    @property
    def exit_code(self) -> int:
        return EXIT_ERROR if self.errors or self.failed else EXIT_SUCCESS

    @property
    def candidate_class_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in self.candidates:
            counts[candidate.candidate_class] = (
                counts.get(candidate.candidate_class, 0) + 1
            )
        return counts


@dataclass(frozen=True, slots=True)
class _LogRow:
    payload: dict[str, Any]
    message_id: int
    tid: str
    status: str | None
    terminal: bool
    taskspec_payload: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class _TaskEvidence:
    tid: str
    latest_status: str | None = None
    terminal_log_message_id: int | None = None
    latest_log_message_id: int | None = None
    taskspec_payload: dict[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        return self.latest_status in TERMINAL_TASK_STATUSES


def run_retention_prune(config: RetentionPruneConfig) -> RetentionPruneResult:
    """Scan and optionally prune retention candidates."""

    validation_error = _validate_config(config)
    run_id = _new_run_id()
    if validation_error is not None:
        return RetentionPruneResult(
            config=config,
            run_id=run_id,
            candidates=(),
            applied_candidates=(),
            scan_stats=(),
            errors=(validation_error,),
        )

    ctx = build_context(spec_context=config.context_path)
    candidates, stats, scan_errors = _build_candidates(ctx, config)
    visible_candidates = _apply_limit(candidates, config.limit)
    if scan_errors:
        return _write_optional_report(
            RetentionPruneResult(
                config=config,
                run_id=run_id,
                candidates=tuple(visible_candidates),
                applied_candidates=(),
                scan_stats=tuple(stats),
                errors=tuple(scan_errors),
            )
        )

    archived = 0
    warnings: list[str] = []
    applied: tuple[RetentionPruneCandidate, ...] = ()
    apply_stats = stats
    if config.apply:
        fresh_candidates, apply_stats, apply_errors = _build_candidates(ctx, config)
        visible_candidates = _apply_limit(fresh_candidates, config.limit)
        scan_errors.extend(apply_errors)
        archive_error: str | None = None
        archive_result = _write_archive(
            ctx,
            run_id,
            config,
            visible_candidates,
            applied_candidates=(),
            result_errors=tuple(scan_errors),
            warnings=tuple(warnings),
        )
        archived = archive_result[0]
        archive_error = archive_result[1]
        if archive_error is not None:
            if config.force and config.archive_path is None:
                warnings.append(
                    f"force apply continued after best-effort archive failure: {archive_error}"
                )
            else:
                scan_errors.append(f"failed to write archive: {archive_error}")
                return _write_optional_report(
                    RetentionPruneResult(
                        config=config,
                        run_id=run_id,
                        candidates=tuple(visible_candidates),
                        applied_candidates=(),
                        scan_stats=tuple(apply_stats),
                        errors=tuple(scan_errors),
                        warnings=tuple(warnings),
                        archived=archived,
                    )
                )
        applied = tuple(_apply_candidates(ctx, visible_candidates, force=config.force))

    result = RetentionPruneResult(
        config=config,
        run_id=run_id,
        candidates=tuple(visible_candidates),
        applied_candidates=applied,
        scan_stats=tuple(apply_stats if config.apply else stats),
        errors=tuple(scan_errors),
        warnings=tuple(warnings),
        archived=archived,
    )
    if config.apply and (config.archive_path is not None or result.archived > 0):
        archive_path = _resolve_archive_path(ctx, config.archive_path)
        try:
            _append_summary_record(archive_path, result)
        except OSError as exc:
            return RetentionPruneResult(
                config=config,
                run_id=run_id,
                candidates=result.candidates,
                applied_candidates=result.applied_candidates,
                scan_stats=result.scan_stats,
                errors=(*result.errors, f"failed to update archive: {exc}"),
                warnings=result.warnings,
                archived=result.archived,
            )
    return _write_optional_report(result)


def cmd_retention_prune(
    *,
    context: Path | None = None,
    family: str = "retention",
    apply: bool = False,
    force: bool = False,
    tasks: Sequence[str] | None = None,
    retention_classes: Sequence[str] | None = None,
    min_age_seconds: float = RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS,
    keep_recent_per_task: int = RETENTION_PRUNE_DEFAULT_KEEP_RECENT_PER_TASK,
    limit: int | None = None,
    json_output: bool = False,
    archive_path: Path | None = None,
    report_path: Path | None = None,
) -> tuple[int, str, str]:
    """Command adapter for retention pruning."""

    normalized_family = cast(RetentionFamily, family)
    config = RetentionPruneConfig(
        context_path=context,
        family=normalized_family,
        apply=apply,
        force=force,
        task_filters=tuple(tasks or ()),
        class_filters=tuple(retention_classes or ()),
        min_age_seconds=min_age_seconds,
        keep_recent_per_task=keep_recent_per_task,
        limit=limit,
        json_output=json_output,
        archive_path=archive_path,
        report_path=report_path,
    )
    result = run_retention_prune(config)
    stdout = (
        json.dumps(retention_prune_summary(result), sort_keys=True)
        if json_output
        else render_retention_prune_human(result)
    )
    stderr = "\n".join([*result.errors, *result.warnings])
    return result.exit_code, stdout, stderr


def retention_prune_summary(result: RetentionPruneResult) -> dict[str, Any]:
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


def render_retention_prune_human(result: RetentionPruneResult) -> str:
    """Render concise human output for the CLI."""

    mode = (
        "dry-run"
        if result.dry_run
        else "force apply"
        if result.config.force
        else "apply"
    )
    lines = [
        f"Retention prune {mode}: scanned {result.records_scanned} records, "
        f"found {len(result.candidates)} candidates, archived {result.archived}, "
        f"deleted {result.deleted}, failed {result.failed}."
    ]
    for warning in result.warnings:
        lines.append(f"warning: {warning}")
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


def _validate_config(config: RetentionPruneConfig) -> str | None:
    if config.family not in {"task-local", "task-log", "retention"}:
        return (
            f"unknown retention prune family: {config.family}; allowed: "
            "task-local, task-log, retention"
        )
    if config.min_age_seconds < 0:
        return "--min-age must be >= 0"
    if config.keep_recent_per_task < 1:
        return "--keep-recent-per-task must be >= 1"
    if config.limit is not None and config.limit < 1:
        return "--limit must be >= 1"
    if config.force and not config.apply:
        return "--force requires --apply"
    unknown_classes = sorted(
        set(config.class_filters) - set(RETENTION_PRUNE_SUPPORTED_CLASSES)
    )
    if unknown_classes:
        allowed = ", ".join(sorted(RETENTION_PRUNE_SUPPORTED_CLASSES))
        return (
            f"unknown retention class: {', '.join(unknown_classes)}; allowed: {allowed}"
        )
    if config.apply and not config.force and config.archive_path is None:
        return "--archive is required for retention --apply unless --force is set"
    return None


def _new_run_id() -> str:
    return f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}.{time.time_ns() % 1_000_000_000:09d}Z:pid-{os.getpid()}"


def _build_candidates(
    ctx: WeftContext,
    config: RetentionPruneConfig,
) -> tuple[list[RetentionPruneCandidate], list[RetentionQueueScanStats], list[str]]:
    now_ns = time.time_ns()
    log_rows, log_scanned = _read_task_log_rows(ctx)
    task_evidence = _reduce_task_evidence(log_rows)
    if config.task_filters:
        task_evidence = {
            tid: evidence
            for tid, evidence in task_evidence.items()
            if tid in set(config.task_filters)
        }
        for tid in config.task_filters:
            task_evidence.setdefault(tid, _TaskEvidence(tid=tid))

    candidates: list[RetentionPruneCandidate] = []
    stats: list[RetentionQueueScanStats] = []
    errors: list[str] = []
    if config.family in {"task-log", "retention"}:
        log_candidates = _task_log_candidates(
            log_rows,
            config=config,
            now_ns=now_ns,
        )
        candidates.extend(log_candidates)
        stats.append(
            RetentionQueueScanStats(
                queue=WEFT_GLOBAL_LOG_QUEUE,
                scanned=log_scanned,
                candidates=len(log_candidates),
            )
        )
    if config.family in {"task-local", "retention"}:
        local_candidates, local_stats, local_errors = _task_local_candidates(
            ctx,
            config=config,
            task_evidence=task_evidence,
            now_ns=now_ns,
        )
        candidates.extend(local_candidates)
        stats.extend(local_stats)
        errors.extend(local_errors)
    candidates = _filter_classes(candidates, config.class_filters)
    candidates.sort(
        key=lambda item: (item.queue, item.message_id, item.candidate_class)
    )
    return candidates, stats, errors


def _read_task_log_rows(ctx: WeftContext) -> tuple[list[_LogRow], int]:
    queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    rows: list[_LogRow] = []
    scanned = 0
    try:
        for payload, message_id in iter_queue_json_entries(queue):
            scanned += 1
            tid = payload.get("tid")
            if not isinstance(tid, str) or not tid:
                continue
            status = _status_from_log_payload(payload)
            taskspec = payload.get("taskspec")
            rows.append(
                _LogRow(
                    payload=payload,
                    message_id=int(message_id),
                    tid=tid,
                    status=status,
                    terminal=terminal_status_from_event(payload) is not None,
                    taskspec_payload=taskspec if isinstance(taskspec, dict) else None,
                )
            )
    finally:
        queue.close()
    return rows, scanned


def _status_from_log_payload(payload: Mapping[str, Any]) -> str | None:
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    taskspec = payload.get("taskspec")
    state = taskspec.get("state") if isinstance(taskspec, Mapping) else None
    state_status = state.get("status") if isinstance(state, Mapping) else None
    return state_status if isinstance(state_status, str) and state_status else None


def _reduce_task_evidence(rows: Sequence[_LogRow]) -> dict[str, _TaskEvidence]:
    grouped: dict[str, list[_LogRow]] = defaultdict(list)
    for row in rows:
        grouped[row.tid].append(row)
    reduced: dict[str, _TaskEvidence] = {}
    for tid, task_rows in grouped.items():
        ordered = sorted(task_rows, key=lambda item: item.message_id)
        latest = ordered[-1]
        terminal_rows = [row for row in ordered if row.terminal]
        taskspec_payload = next(
            (
                row.taskspec_payload
                for row in reversed(ordered)
                if row.taskspec_payload is not None
            ),
            None,
        )
        reduced[tid] = _TaskEvidence(
            tid=tid,
            latest_status=latest.status,
            terminal_log_message_id=terminal_rows[-1].message_id
            if terminal_rows
            else None,
            latest_log_message_id=latest.message_id,
            taskspec_payload=taskspec_payload,
        )
    return reduced


def _task_log_candidates(
    rows: Sequence[_LogRow],
    *,
    config: RetentionPruneConfig,
    now_ns: int,
) -> list[RetentionPruneCandidate]:
    grouped: dict[str, list[_LogRow]] = defaultdict(list)
    task_filter = set(config.task_filters)
    for row in rows:
        if task_filter and row.tid not in task_filter:
            continue
        grouped[row.tid].append(row)

    candidates: list[RetentionPruneCandidate] = []
    for tid, task_rows in grouped.items():
        ordered = sorted(task_rows, key=lambda item: item.message_id, reverse=True)
        protected = {row.message_id for row in ordered[: config.keep_recent_per_task]}
        terminal_rows = [row for row in ordered if row.terminal]
        newest_terminal = terminal_rows[0].message_id if terminal_rows else None
        has_terminal = newest_terminal is not None
        for row in ordered:
            overridden: list[str] = []
            if row.message_id in protected:
                if not config.force:
                    continue
                overridden.append("keep_recent_per_task")
            if row.message_id == newest_terminal:
                if not config.force:
                    continue
                overridden.append("newest_terminal_log")
            if not _is_old_enough(row.message_id, now_ns, config.min_age_seconds):
                if not config.force:
                    continue
                overridden.append("min_age")
            if not has_terminal and row.status not in TERMINAL_TASK_STATUSES:
                if not config.force:
                    continue
                overridden.append("nonterminal_task")
            if _is_active_manager_row(row.payload):
                if not config.force:
                    continue
                overridden.append("active_manager")
            classification = (
                RETENTION_PRUNE_CLASS_TERMINAL_TASK_LOG_SUPERSEDED
                if row.terminal
                else RETENTION_PRUNE_CLASS_NONTERMINAL_TASK_LOG_SUPERSEDED
            )
            candidates.append(
                _candidate(
                    queue=WEFT_GLOBAL_LOG_QUEUE,
                    family="task-log",
                    message_id=row.message_id,
                    tid=tid,
                    candidate_class=classification,
                    reason="older_task_log_row_with_retained_newer_evidence",
                    now_ns=now_ns,
                    raw_payload=json.dumps(row.payload, sort_keys=True),
                    payload=row.payload,
                    force_only=bool(overridden),
                    overridden_protections=tuple(overridden),
                )
            )
    return candidates


def _is_active_manager_row(payload: Mapping[str, Any]) -> bool:
    name = payload.get("name")
    status = _status_from_log_payload(payload)
    runtime_class = payload.get("_weft_runtime_task_class")
    return bool(
        status == "running" and (name == "manager" or runtime_class == "manager")
    )


def _task_local_candidates(
    ctx: WeftContext,
    *,
    config: RetentionPruneConfig,
    task_evidence: Mapping[str, _TaskEvidence],
    now_ns: int,
) -> tuple[list[RetentionPruneCandidate], list[RetentionQueueScanStats], list[str]]:
    candidates: list[RetentionPruneCandidate] = []
    stats: list[RetentionQueueScanStats] = []
    errors: list[str] = []
    for tid, evidence in sorted(task_evidence.items()):
        try:
            task_candidates, task_stats = _task_local_candidates_for_tid(
                ctx,
                config=config,
                evidence=evidence,
                now_ns=now_ns,
            )
        except (BrokerError, OSError, RuntimeError) as exc:
            errors.append(f"failed to scan task-local queues for {tid}: {exc}")
            continue
        candidates.extend(task_candidates)
        stats.extend(task_stats)
    return candidates, stats, errors


def _task_local_candidates_for_tid(
    ctx: WeftContext,
    *,
    config: RetentionPruneConfig,
    evidence: _TaskEvidence,
    now_ns: int,
) -> tuple[list[RetentionPruneCandidate], list[RetentionQueueScanStats]]:
    tid = evidence.tid
    outbox_name, ctrl_out_name = queue_names_for_tid(tid, evidence.taskspec_payload)
    ctrl_in_name, _ctrl_out = control_queue_names_for_tid(
        tid, evidence.taskspec_payload
    )
    inbox_name = f"T{tid}.{QUEUE_INBOX_SUFFIX}"
    reserved_name = f"T{tid}.{QUEUE_RESERVED_SUFFIX}"
    candidates: list[RetentionPruneCandidate] = []
    stats: list[RetentionQueueScanStats] = []

    ctrl_out_candidates, scanned = _ctrl_out_candidates(
        ctx,
        config=config,
        evidence=evidence,
        queue_name=ctrl_out_name,
        now_ns=now_ns,
    )
    candidates.extend(ctrl_out_candidates)
    stats.append(
        RetentionQueueScanStats(
            queue=ctrl_out_name,
            scanned=scanned,
            candidates=len(ctrl_out_candidates),
        )
    )

    outbox_candidates, scanned = _outbox_candidates(
        ctx,
        config=config,
        evidence=evidence,
        queue_name=outbox_name,
        now_ns=now_ns,
    )
    candidates.extend(outbox_candidates)
    stats.append(
        RetentionQueueScanStats(
            queue=outbox_name,
            scanned=scanned,
            candidates=len(outbox_candidates),
        )
    )

    ctrl_in_candidates, scanned = _ctrl_in_candidates(
        ctx,
        config=config,
        evidence=evidence,
        queue_name=ctrl_in_name,
        now_ns=now_ns,
    )
    candidates.extend(ctrl_in_candidates)
    stats.append(
        RetentionQueueScanStats(
            queue=ctrl_in_name,
            scanned=scanned,
            candidates=len(ctrl_in_candidates),
        )
    )

    for queue_name, candidate_class in (
        (inbox_name, RETENTION_PRUNE_CLASS_OBSOLETE_INBOX_WORK),
        (reserved_name, RETENTION_PRUNE_CLASS_OBSOLETE_RESERVED_WORK),
    ):
        queue_candidates, scanned = _work_queue_candidates(
            ctx,
            config=config,
            evidence=evidence,
            queue_name=queue_name,
            candidate_class=candidate_class,
            now_ns=now_ns,
        )
        candidates.extend(queue_candidates)
        stats.append(
            RetentionQueueScanStats(
                queue=queue_name,
                scanned=scanned,
                candidates=len(queue_candidates),
            )
        )
    return candidates, stats


def _ctrl_out_candidates(
    ctx: WeftContext,
    *,
    config: RetentionPruneConfig,
    evidence: _TaskEvidence,
    queue_name: str,
    now_ns: int,
) -> tuple[list[RetentionPruneCandidate], int]:
    rows = _read_raw_queue(ctx, queue_name)
    snapshot = peek_terminal_ctrl_out_evidence(
        ctx,
        tid=evidence.tid,
        ctrl_out_name=queue_name,
        taskspec_payload=evidence.taskspec_payload,
    )
    terminal_ids = (
        {
            target.message_id
            for target in snapshot.ack_targets
            if snapshot is not None and target.queue == queue_name
        }
        if snapshot is not None
        else set()
    )
    candidates: list[RetentionPruneCandidate] = []
    for body, message_id in rows:
        payload = _json_payload(body)
        terminal_payload = coerce_terminal_envelope(body, tid=evidence.tid)
        if terminal_payload is not None and message_id in terminal_ids:
            if evidence.terminal_log_message_id is not None:
                if _age_protected(message_id, now_ns, config):
                    continue
                candidates.append(
                    _candidate(
                        queue=queue_name,
                        family="task-local",
                        message_id=message_id,
                        tid=evidence.tid,
                        candidate_class=RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_ARCHIVED,
                        reason="terminal_ctrl_out_has_retained_terminal_log",
                        now_ns=now_ns,
                        raw_payload=body,
                        payload=payload,
                    )
                )
            else:
                candidates.append(
                    _candidate(
                        queue=queue_name,
                        family="task-local",
                        message_id=message_id,
                        tid=evidence.tid,
                        candidate_class=RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_WITHOUT_LOG_REPORTED,
                        reason="terminal_ctrl_out_without_retained_terminal_log",
                        now_ns=now_ns,
                        raw_payload=body,
                        payload=payload,
                        report_only=not config.force,
                        force_only=config.force,
                        overridden_protections=("only_terminal_ctrl_out_proof",)
                        if config.force
                        else (),
                    )
                )
            continue
        if config.force:
            candidates.append(
                _candidate(
                    queue=queue_name,
                    family="task-local",
                    message_id=message_id,
                    tid=evidence.tid,
                    candidate_class=RETENTION_PRUNE_CLASS_UNSUPPORTED_TASK_LOCAL_SHAPE,
                    reason="force_selected_nonterminal_ctrl_out_row",
                    now_ns=now_ns,
                    raw_payload=body,
                    payload=payload,
                    force_only=True,
                    overridden_protections=("unsupported_ctrl_out_shape",),
                )
            )
    return candidates, len(rows)


def _outbox_candidates(
    ctx: WeftContext,
    *,
    config: RetentionPruneConfig,
    evidence: _TaskEvidence,
    queue_name: str,
    now_ns: int,
) -> tuple[list[RetentionPruneCandidate], int]:
    rows = _read_raw_queue(ctx, queue_name, persistent=True)
    snapshot = peek_final_outbox_evidence(
        ctx,
        tid=evidence.tid,
        outbox_name=queue_name,
        taskspec_payload=evidence.taskspec_payload,
    )
    final_ids = (
        {
            target.message_id
            for target in snapshot.ack_targets
            if snapshot is not None and target.queue == queue_name
        }
        if snapshot is not None
        else set()
    )
    candidates: list[RetentionPruneCandidate] = []
    for body, message_id in rows:
        payload = _json_payload(body)
        if message_id in final_ids:
            if evidence.terminal_log_message_id is not None:
                if _age_protected(message_id, now_ns, config):
                    continue
                candidate_class = RETENTION_PRUNE_CLASS_TERMINAL_RESULT_OUTBOX_ARCHIVED
                report_only = False
                force_only = False
                protections: tuple[str, ...] = ()
            else:
                candidate_class = (
                    RETENTION_PRUNE_CLASS_RESULT_WITHOUT_TERMINAL_OUTBOX_REPORTED
                )
                report_only = not config.force
                force_only = config.force
                protections = (
                    ("only_result_outbox_terminal_proof",) if config.force else ()
                )
            candidates.append(
                _candidate(
                    queue=queue_name,
                    family="task-local",
                    message_id=message_id,
                    tid=evidence.tid,
                    candidate_class=candidate_class,
                    reason="final_one_shot_outbox_result",
                    now_ns=now_ns,
                    raw_payload=body,
                    payload=payload,
                    report_only=report_only,
                    force_only=force_only,
                    overridden_protections=protections,
                )
            )
            continue
        if config.force:
            candidates.append(
                _candidate(
                    queue=queue_name,
                    family="task-local",
                    message_id=message_id,
                    tid=evidence.tid,
                    candidate_class=RETENTION_PRUNE_CLASS_UNSUPPORTED_TASK_LOCAL_SHAPE,
                    reason="force_selected_nonfinal_outbox_row",
                    now_ns=now_ns,
                    raw_payload=body,
                    payload=payload,
                    force_only=True,
                    overridden_protections=("unsupported_outbox_shape",),
                )
            )
    return candidates, len(rows)


def _ctrl_in_candidates(
    ctx: WeftContext,
    *,
    config: RetentionPruneConfig,
    evidence: _TaskEvidence,
    queue_name: str,
    now_ns: int,
) -> tuple[list[RetentionPruneCandidate], int]:
    rows = _read_raw_queue(ctx, queue_name)
    candidates: list[RetentionPruneCandidate] = []
    for body, message_id in rows:
        payload = _json_payload(body)
        is_control = _is_control_message(body, payload)
        overridden: list[str] = []
        if not evidence.terminal:
            if not config.force:
                continue
            overridden.append("nonterminal_task")
        if not is_control:
            if not config.force:
                continue
            overridden.append("malformed_or_unknown_control")
        if _age_protected(message_id, now_ns, config):
            continue
        candidates.append(
            _candidate(
                queue=queue_name,
                family="task-local",
                message_id=message_id,
                tid=evidence.tid,
                candidate_class=RETENTION_PRUNE_CLASS_OBSOLETE_CTRL_IN_CONTROL,
                reason="obsolete_task_control_input",
                now_ns=now_ns,
                raw_payload=body,
                payload=payload,
                force_only=bool(overridden),
                overridden_protections=tuple(overridden),
            )
        )
    return candidates, len(rows)


def _work_queue_candidates(
    ctx: WeftContext,
    *,
    config: RetentionPruneConfig,
    evidence: _TaskEvidence,
    queue_name: str,
    candidate_class: str,
    now_ns: int,
) -> tuple[list[RetentionPruneCandidate], int]:
    rows = _read_raw_queue(ctx, queue_name)
    candidates: list[RetentionPruneCandidate] = []
    for body, message_id in rows:
        overridden: list[str] = []
        if not evidence.terminal:
            if not config.force:
                continue
            overridden.append("nonterminal_or_ambiguous_task")
        if _age_protected(message_id, now_ns, config):
            continue
        candidates.append(
            _candidate(
                queue=queue_name,
                family="task-local",
                message_id=message_id,
                tid=evidence.tid,
                candidate_class=candidate_class,
                reason="obsolete_task_work_queue_row",
                now_ns=now_ns,
                raw_payload=body,
                payload=_json_payload(body),
                report_only=not config.force,
                force_only=config.force,
                overridden_protections=("work_queue_report_only", *overridden)
                if config.force
                else (),
            )
        )
    return candidates, len(rows)


def _read_raw_queue(
    ctx: WeftContext,
    queue_name: str,
    *,
    persistent: bool = False,
) -> list[tuple[str, int]]:
    queue = ctx.queue(queue_name, persistent=persistent)
    try:
        return [
            (body, int(message_id)) for body, message_id in iter_queue_entries(queue)
        ]
    finally:
        queue.close()


def _json_payload(body: str) -> Any | None:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _is_control_message(body: str, payload: Any | None) -> bool:
    if body in {CONTROL_STOP, CONTROL_KILL, CONTROL_PING, CONTROL_STATUS}:
        return True
    if not isinstance(payload, Mapping):
        return False
    command = payload.get("command") or payload.get("type")
    return command in {CONTROL_STOP, CONTROL_KILL, CONTROL_PING, CONTROL_STATUS}


def _age_protected(
    message_id: int,
    now_ns: int,
    config: RetentionPruneConfig,
) -> bool:
    return not config.force and not _is_old_enough(
        message_id, now_ns, config.min_age_seconds
    )


def _is_old_enough(message_id: int, now_ns: int, min_age_seconds: float) -> bool:
    if min_age_seconds <= 0:
        return True
    return _age_seconds(message_id, now_ns) >= min_age_seconds


def _age_seconds(message_id: int, now_ns: int) -> float:
    return max(0.0, (now_ns - int(message_id)) / 1_000_000_000)


def _candidate(
    *,
    queue: str,
    family: RetentionFamily,
    message_id: int,
    tid: str,
    candidate_class: str,
    reason: str,
    now_ns: int,
    raw_payload: str,
    payload: Any | None,
    report_only: bool = False,
    force_only: bool = False,
    overridden_protections: tuple[str, ...] = (),
) -> RetentionPruneCandidate:
    payload_bytes = raw_payload.encode("utf-8", errors="replace")
    return RetentionPruneCandidate(
        queue=queue,
        family=family,
        message_id=message_id,
        tid=tid,
        candidate_class=candidate_class,
        reason=reason,
        age_seconds=_age_seconds(message_id, now_ns),
        payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        payload_size_bytes=len(payload_bytes),
        payload_excerpt=raw_payload[:200],
        payload=payload if len(payload_bytes) <= 4096 else None,
        report_only=report_only
        or (candidate_class in RETENTION_PRUNE_REPORT_ONLY_CLASSES and not force_only),
        force_only=force_only,
        overridden_protections=overridden_protections,
    )


def _filter_classes(
    candidates: Sequence[RetentionPruneCandidate],
    class_filters: Sequence[str],
) -> list[RetentionPruneCandidate]:
    if not class_filters:
        return list(candidates)
    allowed = set(class_filters)
    return [
        candidate for candidate in candidates if candidate.candidate_class in allowed
    ]


def _apply_limit(
    candidates: Sequence[RetentionPruneCandidate],
    limit: int | None,
) -> list[RetentionPruneCandidate]:
    if limit is None:
        return list(candidates)
    return list(candidates[:limit])


def _apply_candidates(
    ctx: WeftContext,
    candidates: Sequence[RetentionPruneCandidate],
    *,
    force: bool,
) -> list[RetentionPruneCandidate]:
    by_queue: dict[str, list[RetentionPruneCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_queue[candidate.queue].append(candidate)

    applied: list[RetentionPruneCandidate] = []
    for queue_name, queue_candidates in by_queue.items():
        persistent = queue_name.endswith(".outbox")
        queue = ctx.queue(queue_name, persistent=persistent)
        try:
            for candidate in queue_candidates:
                if candidate.report_only and not force:
                    applied.append(candidate.for_apply_result(applied=False))
                    continue
                if (
                    candidate.candidate_class
                    == RETENTION_PRUNE_CLASS_CLAIMED_OUTBOX_RESIDUE_FORCE
                ):
                    applied.append(
                        candidate.for_apply_result(
                            applied=False,
                            error="claimed outbox residue has no exact readable message id",
                        )
                    )
                    continue
                try:
                    deleted = queue.delete(message_id=candidate.message_id)
                except (BrokerError, OSError, RuntimeError) as exc:
                    applied.append(
                        candidate.for_apply_result(applied=False, error=str(exc))
                    )
                    continue
                applied.append(candidate.for_apply_result(applied=bool(deleted)))
        finally:
            queue.close()
    return applied


def _write_optional_report(result: RetentionPruneResult) -> RetentionPruneResult:
    if result.config.report_path is None:
        return result
    try:
        _write_records(
            result.config.report_path,
            result.run_id,
            result.config,
            result.candidates,
            applied_candidates=result.applied_candidates,
            errors=result.errors,
            warnings=result.warnings,
            truncate_payload=True,
        )
    except OSError as exc:
        return RetentionPruneResult(
            config=result.config,
            run_id=result.run_id,
            candidates=result.candidates,
            applied_candidates=result.applied_candidates,
            scan_stats=result.scan_stats,
            errors=(*result.errors, f"failed to write report: {exc}"),
            warnings=result.warnings,
            archived=result.archived,
        )
    return result


def _write_archive(
    ctx: WeftContext,
    run_id: str,
    config: RetentionPruneConfig,
    candidates: Sequence[RetentionPruneCandidate],
    *,
    applied_candidates: Sequence[RetentionPruneCandidate],
    result_errors: Sequence[str],
    warnings: Sequence[str],
) -> tuple[int, str | None]:
    path = _resolve_archive_path(ctx, config.archive_path)
    try:
        _write_records(
            path,
            run_id,
            config,
            candidates,
            applied_candidates=applied_candidates,
            errors=result_errors,
            warnings=warnings,
            truncate_payload=False,
        )
    except OSError as exc:
        return 0, str(exc)
    return len(candidates), None


def _resolve_archive_path(ctx: WeftContext, archive_path: Path | None) -> Path:
    if archive_path is None:
        return (
            ctx.logs_dir
            / RETENTION_PRUNE_LOG_SUBDIR
            / (f"{time.strftime('%Y-%m-%d')}-retention-prune.jsonl")
        )
    if archive_path.exists() and archive_path.is_dir():
        return archive_path / f"{time.strftime('%Y-%m-%d')}-retention-prune.jsonl"
    if str(archive_path).endswith(os.sep):
        return archive_path / f"{time.strftime('%Y-%m-%d')}-retention-prune.jsonl"
    return archive_path


def _write_records(
    path: Path,
    run_id: str,
    config: RetentionPruneConfig,
    candidates: Sequence[RetentionPruneCandidate],
    *,
    applied_candidates: Sequence[RetentionPruneCandidate],
    errors: Sequence[str],
    warnings: Sequence[str],
    truncate_payload: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    applied_by_id = {
        (candidate.queue, candidate.message_id): candidate
        for candidate in applied_candidates
    }
    with path.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            visible = applied_by_id.get(
                (candidate.queue, candidate.message_id),
                candidate,
            )
            handle.write(
                json.dumps(
                    _candidate_record(run_id, config, visible, truncate_payload),
                    sort_keys=True,
                )
            )
            handle.write("\n")
        handle.write(
            json.dumps(
                _summary_record(
                    run_id, config, candidates, applied_candidates, errors, warnings
                ),
                sort_keys=True,
            )
        )
        handle.write("\n")
        handle.flush()


def _append_summary_record(path: Path, result: RetentionPruneResult) -> None:
    """Append the post-apply archive summary without rewriting pre-delete rows."""

    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                _summary_record(
                    result.run_id,
                    result.config,
                    result.candidates,
                    result.applied_candidates,
                    result.errors,
                    result.warnings,
                ),
                sort_keys=True,
            )
        )
        handle.write("\n")
        handle.flush()


def _candidate_record(
    run_id: str,
    config: RetentionPruneConfig,
    candidate: RetentionPruneCandidate,
    truncate_payload: bool,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": RETENTION_PRUNE_SCHEMA_VERSION,
        "record_type": "retention_prune_candidate",
        "run_id": run_id,
        "emitted_at": time.time_ns(),
        "queue": candidate.queue,
        "message_id": candidate.message_id,
        "tid": candidate.tid,
        "candidate_class": candidate.candidate_class,
        "reason": candidate.reason,
        "age_seconds": candidate.age_seconds,
        "dry_run": not config.apply,
        "force": config.force,
        "applied": candidate.applied,
        "error": candidate.error,
        "payload_sha256": candidate.payload_sha256,
        "payload_size_bytes": candidate.payload_size_bytes,
        "payload_excerpt": candidate.payload_excerpt,
        "overridden_protections": list(candidate.overridden_protections),
    }
    if not truncate_payload and candidate.payload is not None:
        record["payload"] = candidate.payload
    return record


def _summary_record(
    run_id: str,
    config: RetentionPruneConfig,
    candidates: Sequence[RetentionPruneCandidate],
    applied_candidates: Sequence[RetentionPruneCandidate],
    errors: Sequence[str],
    warnings: Sequence[str],
) -> dict[str, Any]:
    deleted = sum(1 for candidate in applied_candidates if candidate.applied)
    failed = sum(1 for candidate in applied_candidates if candidate.error)
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.candidate_class] = counts.get(candidate.candidate_class, 0) + 1
    return {
        "schema_version": RETENTION_PRUNE_SCHEMA_VERSION,
        "record_type": "retention_prune_completed",
        "run_id": run_id,
        "dry_run": not config.apply,
        "force": config.force,
        "candidates": len(candidates),
        "archived": len(candidates),
        "deleted": deleted,
        "failed": failed,
        "candidate_class_counts": counts,
        "errors": list(errors),
        "warnings": list(warnings),
    }
