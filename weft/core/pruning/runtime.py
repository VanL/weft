"""Canonical runtime-only state pruning engine.

This module implements runtime-state candidate selection for `weft.state.*`
operational queues. Command wrappers use this foreground maintenance path, and
the manager-supervised TaskMonitor reuses the same engine for its default-on
background maintenance pass; the monitor keeps its own per-cycle cleanup
runner for tid-mappings and the task log.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-3.1], [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.6], [OBS.13]
- docs/specifications/10-CLI_Interface.md [CLI-6]
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from simplebroker.ext import BrokerError
from weft._constants import (
    EXIT_ERROR,
    EXIT_SUCCESS,
    RUNTIME_PRUNE_CLASS_STALE_ENDPOINT,
    RUNTIME_PRUNE_CLASS_STALE_MANAGER,
    RUNTIME_PRUNE_CLASS_STALE_STREAMING,
    RUNTIME_PRUNE_CLASS_SUPERSEDED_ENDPOINT,
    RUNTIME_PRUNE_CLASS_SUPERSEDED_MANAGER,
    RUNTIME_PRUNE_CLASS_SUPERSEDED_SERVICE,
    RUNTIME_PRUNE_CLASS_SUPERSEDED_TID_MAPPING,
    RUNTIME_PRUNE_CLASS_UNSUPPORTED_PIPELINE,
    RUNTIME_PRUNE_DEFAULT_KEEP_RECENT_PER_KEY,
    RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS,
    RUNTIME_PRUNE_DEFAULT_QUEUE_GROUPS,
    RUNTIME_PRUNE_REPORT_ONLY_CLASSIFICATIONS,
    RUNTIME_PRUNE_SCHEMA_VERSION,
    RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS,
    SERVICE_TYPE_MANAGED,
    TERMINAL_TASK_STATUSES,
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_PIPELINES_STATE_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.endpoints import (
    endpoint_record_from_payload,
    endpoint_record_owner_is_live,
    latest_task_statuses_for_endpoint_resolution,
    latest_tid_mapping_entries_for_endpoint_resolution,
)
from weft.core.manager_runtime import (
    manager_registry_record_is_stale,
    normalize_manager_registry_record,
)
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.core.service_convergence import (
    parse_service_owner_row,
    plan_service_owner_history_prune,
)
from weft.helpers import iter_queue_json_entries

RuntimeQueueName = Literal[
    "tid-mappings",
    "managers",
    "services",
    "streaming",
    "endpoints",
    "pipelines",
]


@dataclass(frozen=True, slots=True)
class RuntimePruneConfig:
    """Configuration for one runtime-state prune scan."""

    context_path: Path | None = None
    apply: bool = False
    queues: tuple[RuntimeQueueName, ...] = (
        "tid-mappings",
        "managers",
        "services",
        "streaming",
        "endpoints",
        "pipelines",
    )
    min_age_seconds: float = RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS
    keep_recent_per_key: int = RUNTIME_PRUNE_DEFAULT_KEEP_RECENT_PER_KEY
    limit: int | None = None
    json_output: bool = False
    report_path: Path | None = None


@dataclass(frozen=True, slots=True)
class RuntimePruneCandidate:
    """One exact runtime-state row selected by conservative prune rules."""

    queue: str
    queue_group: RuntimeQueueName
    message_id: int
    key: str
    classification: str
    reason: str
    age_seconds: float
    payload_excerpt: dict[str, Any] = field(default_factory=dict)
    report_only: bool = False
    applied: bool = False
    error: str | None = None

    def for_apply_result(
        self,
        *,
        applied: bool,
        error: str | None = None,
    ) -> RuntimePruneCandidate:
        return RuntimePruneCandidate(
            queue=self.queue,
            queue_group=self.queue_group,
            message_id=self.message_id,
            key=self.key,
            classification=self.classification,
            reason=self.reason,
            age_seconds=self.age_seconds,
            payload_excerpt=dict(self.payload_excerpt),
            report_only=self.report_only,
            applied=applied,
            error=error,
        )


@dataclass(frozen=True, slots=True)
class RuntimeQueueScanStats:
    """Scan counters for one runtime-state queue."""

    queue: str
    scanned: int = 0
    candidates: int = 0


@dataclass(frozen=True, slots=True)
class RuntimePruneResult:
    """Result of one runtime-state prune command."""

    config: RuntimePruneConfig
    run_id: str
    candidates: tuple[RuntimePruneCandidate, ...]
    applied_candidates: tuple[RuntimePruneCandidate, ...]
    scan_stats: tuple[RuntimeQueueScanStats, ...]
    errors: tuple[str, ...] = ()

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
    def classification_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in self.candidates:
            counts[candidate.classification] = (
                counts.get(candidate.classification, 0) + 1
            )
        return counts


def run_runtime_prune(config: RuntimePruneConfig) -> RuntimePruneResult:
    """Scan and optionally prune runtime-only state queues.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-5]
    """

    ctx = build_context(spec_context=config.context_path)
    return run_runtime_prune_for_context(ctx, config)


def run_runtime_prune_for_context(
    ctx: WeftContext,
    config: RuntimePruneConfig,
) -> RuntimePruneResult:
    """Run runtime-state pruning against an already-resolved context."""

    validation_error = _validate_config(config)
    run_id = _new_run_id()
    if validation_error is not None:
        return RuntimePruneResult(
            config=config,
            run_id=run_id,
            candidates=(),
            applied_candidates=(),
            scan_stats=(),
            errors=(validation_error,),
        )

    candidates, stats, scan_errors = _build_candidates(ctx, config)
    visible_candidates = _apply_limit(candidates, config.limit)
    if scan_errors:
        return RuntimePruneResult(
            config=config,
            run_id=run_id,
            candidates=tuple(visible_candidates),
            applied_candidates=(),
            scan_stats=tuple(stats),
            errors=tuple(scan_errors),
        )

    applied: tuple[RuntimePruneCandidate, ...] = ()
    apply_stats = stats
    if config.apply:
        fresh_candidates, apply_stats, apply_errors = _build_candidates(ctx, config)
        to_apply = _apply_limit(fresh_candidates, config.limit)
        applied = tuple(_apply_candidates(ctx, to_apply))
        scan_errors.extend(apply_errors)
        visible_candidates = to_apply

    result = RuntimePruneResult(
        config=config,
        run_id=run_id,
        candidates=tuple(visible_candidates),
        applied_candidates=applied,
        scan_stats=tuple(apply_stats if config.apply else stats),
        errors=tuple(scan_errors),
    )
    if config.report_path is not None:
        try:
            write_runtime_prune_report(result, config.report_path)
        except OSError as exc:
            return RuntimePruneResult(
                config=config,
                run_id=run_id,
                candidates=result.candidates,
                applied_candidates=result.applied_candidates,
                scan_stats=result.scan_stats,
                errors=(*result.errors, f"failed to write report: {exc}"),
            )
    return result


def cmd_prune(
    *,
    context: Path | None = None,
    apply: bool = False,
    queues: Sequence[str] | None = None,
    min_age_seconds: float = RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS,
    keep_recent_per_key: int = RUNTIME_PRUNE_DEFAULT_KEEP_RECENT_PER_KEY,
    limit: int | None = None,
    json_output: bool = False,
    report_path: Path | None = None,
) -> tuple[int, str, str]:
    """Command adapter for `weft system prune`."""

    try:
        normalized_queues = normalize_queue_filters(queues)
    except ValueError as exc:
        return EXIT_ERROR, "", str(exc)

    config = RuntimePruneConfig(
        context_path=context,
        apply=apply,
        queues=normalized_queues,
        min_age_seconds=min_age_seconds,
        keep_recent_per_key=keep_recent_per_key,
        limit=limit,
        json_output=json_output,
        report_path=report_path,
    )
    result = run_runtime_prune(config)
    if json_output:
        stdout = json.dumps(runtime_prune_summary(result), sort_keys=True)
    else:
        stdout = render_runtime_prune_human(result)
    stderr = "\n".join(result.errors)
    return result.exit_code, stdout, stderr


def normalize_queue_filters(
    values: Sequence[str] | None,
) -> tuple[RuntimeQueueName, ...]:
    """Normalize CLI queue filters and reject unknown values."""

    if not values:
        return cast(tuple[RuntimeQueueName, ...], RUNTIME_PRUNE_DEFAULT_QUEUE_GROUPS)
    normalized: list[str] = []
    for raw_value in values:
        for part in raw_value.split(","):
            value = part.strip()
            if not value:
                continue
            normalized.append(value)
    if not normalized or "all" in normalized:
        return cast(tuple[RuntimeQueueName, ...], RUNTIME_PRUNE_DEFAULT_QUEUE_GROUPS)

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
    deduped: list[RuntimeQueueName] = []
    for value in normalized:
        deduped.append(cast(RuntimeQueueName, value))
    return tuple(dict.fromkeys(deduped))


def runtime_prune_summary(result: RuntimePruneResult) -> dict[str, Any]:
    """Return the JSON summary contract for a prune run."""

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


def render_runtime_prune_human(result: RuntimePruneResult) -> str:
    """Render concise human output for the CLI."""

    mode = "dry-run" if result.dry_run else "apply"
    lines = [
        f"Runtime state prune {mode}: scanned {result.records_scanned} records, "
        f"found {len(result.candidates)} candidates, deleted {result.deleted}, "
        f"failed {result.failed}."
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


def write_runtime_prune_report(result: RuntimePruneResult, path: Path) -> None:
    """Write JSONL candidate records plus a final summary record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    applied_by_id = {
        (candidate.queue, candidate.message_id): candidate
        for candidate in result.applied_candidates
    }
    with path.open("w", encoding="utf-8") as handle:
        for candidate in result.candidates:
            candidate = applied_by_id.get(
                (candidate.queue, candidate.message_id), candidate
            )
            handle.write(
                json.dumps(_candidate_record(result, candidate), sort_keys=True)
            )
            handle.write("\n")
        handle.write(json.dumps(runtime_prune_summary(result), sort_keys=True))
        handle.write("\n")


def _validate_config(config: RuntimePruneConfig) -> str | None:
    if config.min_age_seconds < 0:
        return "--min-age must be >= 0"
    if config.keep_recent_per_key < 1:
        return "--keep-recent-per-key must be >= 1"
    if config.limit is not None and config.limit < 1:
        return "--limit must be >= 1"
    return None


def _new_run_id() -> str:
    return f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}.{time.time_ns() % 1_000_000_000:09d}Z:pid-{os.getpid()}"


def _apply_limit(
    candidates: Sequence[RuntimePruneCandidate],
    limit: int | None,
) -> list[RuntimePruneCandidate]:
    if limit is None:
        return list(candidates)
    return list(candidates[:limit])


def _build_candidates(
    ctx: WeftContext,
    config: RuntimePruneConfig,
) -> tuple[list[RuntimePruneCandidate], list[RuntimeQueueScanStats], list[str]]:
    now_ns = time.time_ns()
    builders = {
        "tid-mappings": _tid_mapping_candidates,
        "managers": _manager_candidates,
        "services": _service_candidates,
        "streaming": _streaming_candidates,
        "endpoints": _endpoint_candidates,
        "pipelines": _pipeline_candidates,
    }
    candidates: list[RuntimePruneCandidate] = []
    stats: list[RuntimeQueueScanStats] = []
    errors: list[str] = []
    for queue_group in config.queues:
        try:
            queue_candidates, scanned = builders[queue_group](ctx, config, now_ns)
        except (BrokerError, OSError, RuntimeError) as exc:
            errors.append(
                f"failed to scan {RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS[queue_group]}: {exc}"
            )
            continue
        candidates.extend(queue_candidates)
        stats.append(
            RuntimeQueueScanStats(
                queue=RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS[queue_group],
                scanned=scanned,
                candidates=len(queue_candidates),
            )
        )
    candidates.sort(key=lambda item: (item.queue, item.message_id))
    return candidates, stats, errors


def _read_runtime_queue(
    ctx: WeftContext,
    queue_name: str,
) -> tuple[list[tuple[dict[str, Any], int]], int]:
    queue = ctx.queue(queue_name, persistent=False)
    try:
        entries = [
            (payload, int(timestamp))
            for payload, timestamp in iter_queue_json_entries(queue)
        ]
        return entries, len(entries)
    finally:
        queue.close()


def _is_old_enough(message_id: int, now_ns: int, min_age_seconds: float) -> bool:
    if min_age_seconds <= 0:
        return True
    return _age_seconds(message_id, now_ns) >= min_age_seconds


def _age_seconds(message_id: int, now_ns: int) -> float:
    return max(0.0, (now_ns - int(message_id)) / 1_000_000_000)


def _payload_excerpt(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "tid",
        "short",
        "full",
        "name",
        "status",
        "runner",
        "role",
        "queue",
        "session_id",
        "service_key",
        "service_type",
        "owner_tid",
    }
    return {key: payload[key] for key in allowed if key in payload}


def _candidate(
    *,
    queue: str,
    queue_group: RuntimeQueueName,
    message_id: int,
    key: str,
    classification: str,
    reason: str,
    now_ns: int,
    payload: Mapping[str, Any],
    report_only: bool = False,
) -> RuntimePruneCandidate:
    return RuntimePruneCandidate(
        queue=queue,
        queue_group=queue_group,
        message_id=message_id,
        key=key,
        classification=classification,
        reason=reason,
        age_seconds=_age_seconds(message_id, now_ns),
        payload_excerpt=_payload_excerpt(payload),
        report_only=report_only
        or classification in RUNTIME_PRUNE_REPORT_ONLY_CLASSIFICATIONS,
    )


def _tid_mapping_candidates(
    ctx: WeftContext,
    config: RuntimePruneConfig,
    now_ns: int,
) -> tuple[list[RuntimePruneCandidate], int]:
    entries, scanned = _read_runtime_queue(ctx, WEFT_TID_MAPPINGS_QUEUE)
    grouped: dict[str, list[tuple[dict[str, Any], int]]] = defaultdict(list)
    for payload, message_id in entries:
        full = payload.get("full")
        if isinstance(full, str) and full:
            grouped[full].append((payload, message_id))

    candidates: list[RuntimePruneCandidate] = []
    for full, records in grouped.items():
        ordered = sorted(records, key=lambda item: item[1], reverse=True)
        protected_ids = {
            message_id for _payload, message_id in ordered[: config.keep_recent_per_key]
        }
        for payload, message_id in ordered[config.keep_recent_per_key :]:
            if message_id in protected_ids:
                continue
            if not _is_old_enough(message_id, now_ns, config.min_age_seconds):
                continue
            candidates.append(
                _candidate(
                    queue=WEFT_TID_MAPPINGS_QUEUE,
                    queue_group="tid-mappings",
                    message_id=message_id,
                    key=full,
                    classification=RUNTIME_PRUNE_CLASS_SUPERSEDED_TID_MAPPING,
                    reason="older_than_min_age_and_not_latest_for_tid",
                    now_ns=now_ns,
                    payload=payload,
                )
            )
    return candidates, scanned


def _manager_candidates(
    ctx: WeftContext,
    config: RuntimePruneConfig,
    now_ns: int,
) -> tuple[list[RuntimePruneCandidate], int]:
    entries, scanned = _read_runtime_queue(ctx, WEFT_SERVICES_REGISTRY_QUEUE)
    payload_by_id = {message_id: payload for payload, message_id in entries}
    record_by_id: dict[int, dict[str, Any]] = {}
    grouped: dict[str, list[int]] = defaultdict(list)
    candidates: list[RuntimePruneCandidate] = []
    for payload, message_id in entries:
        parse_result = parse_service_owner_row(payload, timestamp=message_id)
        if parse_result.disposition == "malformed":
            if _is_old_enough(message_id, now_ns, config.min_age_seconds):
                candidates.append(
                    _candidate(
                        queue=WEFT_SERVICES_REGISTRY_QUEUE,
                        queue_group="managers",
                        message_id=message_id,
                        key=str(payload.get("owner_tid") or payload.get("tid") or ""),
                        classification=RUNTIME_PRUNE_CLASS_STALE_MANAGER,
                        reason="malformed_service_owner_row",
                        now_ns=now_ns,
                        payload=payload,
                    )
                )
            continue
        record = normalize_manager_registry_record(
            ctx,
            payload,
            timestamp=message_id,
        )
        if record is None:
            continue
        tid = record.get("tid")
        if not isinstance(tid, str) or not tid:
            continue
        record_by_id[message_id] = record
        grouped[tid].append(message_id)

    for tid, message_ids in grouped.items():
        ordered = sorted(message_ids, reverse=True)
        protected_ids = set(ordered[: config.keep_recent_per_key])
        for message_id in ordered:
            record = record_by_id.get(message_id)
            candidate_payload = payload_by_id.get(message_id)
            if record is None or candidate_payload is None:
                continue
            if not _is_old_enough(message_id, now_ns, config.min_age_seconds):
                continue
            if record.get("status") == "active" and manager_registry_record_is_stale(
                record
            ):
                candidates.append(
                    _candidate(
                        queue=WEFT_SERVICES_REGISTRY_QUEUE,
                        queue_group="managers",
                        message_id=message_id,
                        key=tid,
                        classification=RUNTIME_PRUNE_CLASS_STALE_MANAGER,
                        reason="active_manager_runtime_handle_not_live",
                        now_ns=now_ns,
                        payload=candidate_payload,
                    )
                )
                continue
            if message_id in protected_ids:
                continue
            candidates.append(
                _candidate(
                    queue=WEFT_SERVICES_REGISTRY_QUEUE,
                    queue_group="managers",
                    message_id=message_id,
                    key=tid,
                    classification=RUNTIME_PRUNE_CLASS_SUPERSEDED_MANAGER,
                    reason="older_than_min_age_and_not_latest_for_manager_tid",
                    now_ns=now_ns,
                    payload=candidate_payload,
                )
            )
    return candidates, scanned


def _service_candidates(
    ctx: WeftContext,
    config: RuntimePruneConfig,
    now_ns: int,
) -> tuple[list[RuntimePruneCandidate], int]:
    entries, scanned = _read_runtime_queue(ctx, WEFT_SERVICES_REGISTRY_QUEUE)
    payload_by_id = {message_id: payload for payload, message_id in entries}
    managed_records = []
    candidates: list[RuntimePruneCandidate] = []
    for payload, message_id in entries:
        parse_result = parse_service_owner_row(payload, timestamp=message_id)
        if parse_result.record is None:
            continue
        if parse_result.record.service_type != SERVICE_TYPE_MANAGED:
            continue
        managed_records.append(parse_result.record)

    service_keys = sorted({record.service_key for record in managed_records})
    for service_key in service_keys:
        prune_ids = set(
            plan_service_owner_history_prune(
                managed_records,
                service_key=service_key,
                now_ns=now_ns,
                ttl_ns=-1,
                keep_recent_per_key=config.keep_recent_per_key,
            )
        )
        for message_id in sorted(prune_ids):
            candidate_payload = payload_by_id.get(message_id)
            if candidate_payload is None:
                continue
            if not _is_old_enough(message_id, now_ns, config.min_age_seconds):
                continue
            candidates.append(
                _candidate(
                    queue=WEFT_SERVICES_REGISTRY_QUEUE,
                    queue_group="services",
                    message_id=message_id,
                    key=service_key,
                    classification=RUNTIME_PRUNE_CLASS_SUPERSEDED_SERVICE,
                    reason="older_than_min_age_and_not_latest_for_service_key",
                    now_ns=now_ns,
                    payload=candidate_payload,
                )
            )
    return candidates, scanned


def _latest_task_statuses_from_log(ctx: WeftContext) -> dict[str, str]:
    queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        latest: dict[str, tuple[int, str]] = {}
        for payload, message_id in iter_queue_json_entries(queue):
            tid = payload.get("tid")
            status = payload.get("status")
            if not isinstance(tid, str) or not tid:
                continue
            if not isinstance(status, str) or not status:
                taskspec = payload.get("taskspec")
                if isinstance(taskspec, Mapping):
                    state = taskspec.get("state")
                    if isinstance(state, Mapping):
                        status = state.get("status")
            if not isinstance(status, str) or not status:
                continue
            previous = latest.get(tid)
            if previous is None or previous[0] <= int(message_id):
                latest[tid] = (int(message_id), status)
        return {tid: status for tid, (_message_id, status) in latest.items()}
    finally:
        queue.close()


def _streaming_candidates(
    ctx: WeftContext,
    config: RuntimePruneConfig,
    now_ns: int,
) -> tuple[list[RuntimePruneCandidate], int]:
    entries, scanned = _read_runtime_queue(ctx, WEFT_STREAMING_SESSIONS_QUEUE)
    task_statuses = _latest_task_statuses_from_log(ctx)
    tid_mappings = latest_tid_mapping_entries_for_endpoint_resolution(ctx)
    grouped: dict[str, list[tuple[dict[str, Any], int]]] = defaultdict(list)
    for payload, message_id in entries:
        key = payload.get("session_id")
        if not isinstance(key, str) or not key:
            key = payload.get("tid")
        if isinstance(key, str) and key:
            grouped[key].append((payload, message_id))

    candidates: list[RuntimePruneCandidate] = []
    for key, records in grouped.items():
        ordered = sorted(records, key=lambda item: item[1], reverse=True)
        protected_ids = {
            message_id for _payload, message_id in ordered[: config.keep_recent_per_key]
        }
        for payload, message_id in ordered:
            if not _is_old_enough(message_id, now_ns, config.min_age_seconds):
                continue
            tid = payload.get("tid")
            if not isinstance(tid, str) or not tid:
                continue
            status = task_statuses.get(tid)
            is_duplicate = message_id not in protected_ids
            owner_terminal = status in TERMINAL_TASK_STATUSES
            owner_has_no_live_proof = status is None and tid not in tid_mappings
            owner_is_prunable = owner_terminal or owner_has_no_live_proof
            if is_duplicate and not owner_is_prunable:
                continue
            if not (owner_is_prunable or is_duplicate):
                continue
            candidates.append(
                _candidate(
                    queue=WEFT_STREAMING_SESSIONS_QUEUE,
                    queue_group="streaming",
                    message_id=message_id,
                    key=key,
                    classification=RUNTIME_PRUNE_CLASS_STALE_STREAMING,
                    reason=(
                        "owner_task_terminal"
                        if owner_terminal
                        else "duplicate_or_no_live_owner_proof"
                    ),
                    now_ns=now_ns,
                    payload=payload,
                )
            )
    return candidates, scanned


def _endpoint_candidates(
    ctx: WeftContext,
    config: RuntimePruneConfig,
    now_ns: int,
) -> tuple[list[RuntimePruneCandidate], int]:
    entries, scanned = _read_runtime_queue(ctx, WEFT_ENDPOINTS_REGISTRY_QUEUE)
    parsed = []
    grouped: dict[tuple[str, str], list[tuple[dict[str, Any], int]]] = defaultdict(list)
    for payload, message_id in entries:
        record = endpoint_record_from_payload(payload, message_id=message_id)
        if record is None:
            continue
        parsed.append((payload, message_id, record))
        grouped[(record.name, record.tid)].append((payload, message_id))

    task_statuses = latest_task_statuses_for_endpoint_resolution(ctx)
    tid_mappings = latest_tid_mapping_entries_for_endpoint_resolution(ctx)
    candidates: list[RuntimePruneCandidate] = []
    superseded_ids: set[int] = set()
    for (name, tid), records in grouped.items():
        ordered = sorted(records, key=lambda item: item[1], reverse=True)
        protected_ids = {
            message_id for _payload, message_id in ordered[: config.keep_recent_per_key]
        }
        for payload, message_id in ordered[config.keep_recent_per_key :]:
            if message_id in protected_ids:
                continue
            if not _is_old_enough(message_id, now_ns, config.min_age_seconds):
                continue
            superseded_ids.add(message_id)
            candidates.append(
                _candidate(
                    queue=WEFT_ENDPOINTS_REGISTRY_QUEUE,
                    queue_group="endpoints",
                    message_id=message_id,
                    key=f"{name}:{tid}",
                    classification=RUNTIME_PRUNE_CLASS_SUPERSEDED_ENDPOINT,
                    reason="older_than_min_age_and_not_latest_for_endpoint_owner",
                    now_ns=now_ns,
                    payload=payload,
                )
            )

    for payload, message_id, record in parsed:
        if message_id in superseded_ids:
            continue
        if record.status != "active":
            continue
        if not _is_old_enough(message_id, now_ns, config.min_age_seconds):
            continue
        if endpoint_record_owner_is_live(
            record,
            task_statuses=task_statuses,
            tid_mappings=tid_mappings,
        ):
            continue
        candidates.append(
            _candidate(
                queue=WEFT_ENDPOINTS_REGISTRY_QUEUE,
                queue_group="endpoints",
                message_id=message_id,
                key=record.name,
                classification=RUNTIME_PRUNE_CLASS_STALE_ENDPOINT,
                reason="endpoint_owner_not_live",
                now_ns=now_ns,
                payload=payload,
            )
        )
    return candidates, scanned


def _pipeline_candidates(
    ctx: WeftContext,
    config: RuntimePruneConfig,
    now_ns: int,
) -> tuple[list[RuntimePruneCandidate], int]:
    entries, scanned = _read_runtime_queue(ctx, WEFT_PIPELINES_STATE_QUEUE)
    candidates: list[RuntimePruneCandidate] = []
    for payload, message_id in entries:
        if not _is_old_enough(message_id, now_ns, config.min_age_seconds):
            continue
        key = payload.get("tid") or payload.get("pipeline_tid") or payload.get("id")
        if not isinstance(key, str) or not key:
            key = str(message_id)
        candidates.append(
            _candidate(
                queue=WEFT_PIPELINES_STATE_QUEUE,
                queue_group="pipelines",
                message_id=message_id,
                key=key,
                classification=RUNTIME_PRUNE_CLASS_UNSUPPORTED_PIPELINE,
                reason="pipeline_runtime_shape_not_pruned_in_first_slice",
                now_ns=now_ns,
                payload=payload,
                report_only=True,
            )
        )
    return candidates, scanned


def _apply_candidates(
    ctx: WeftContext,
    candidates: Sequence[RuntimePruneCandidate],
) -> list[RuntimePruneCandidate]:
    return apply_exact_prune_candidates(
        ctx,
        candidates,
        apply_result=lambda candidate, applied, error: candidate.for_apply_result(
            applied=applied,
            error=error,
        ),
    )


def _candidate_record(
    result: RuntimePruneResult,
    candidate: RuntimePruneCandidate,
) -> dict[str, Any]:
    return {
        "schema_version": RUNTIME_PRUNE_SCHEMA_VERSION,
        "record_type": "runtime_prune_candidate",
        "run_id": result.run_id,
        "emitted_at": time.time_ns(),
        "queue": candidate.queue,
        "message_id": candidate.message_id,
        "key": candidate.key,
        "classification": candidate.classification,
        "reason": candidate.reason,
        "age_seconds": candidate.age_seconds,
        "dry_run": result.dry_run,
        "applied": candidate.applied,
        "error": candidate.error,
        "payload_excerpt": candidate.payload_excerpt,
    }
