"""Task-monitor runtime config and processor contract.

This module contains the command-neutral pieces used by the supervised
``TaskMonitorTask``. It deliberately stays below the command and CLI layers:
processors receive typed candidates plus a context, not manager or command
objects. Built-in processor names are handled by ``TaskMonitorTask`` and are
not resolved through the custom ``module:function`` processor hook.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3]
- docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
"""

from __future__ import annotations

import hashlib
import importlib
import json
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, cast

from simplebroker.ext import BrokerError
from weft._constants import (
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    STATUS_RUNTIMELESS_STALE_AFTER_SECONDS,
    TASK_MONITOR_WEFT_ANOMALY_CLASSIFICATIONS,
    TERMINAL_TASK_EVENTS,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
    WEFT_TASK_MONITOR_ENABLED_DEFAULT,
    WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT,
    WEFT_TASK_MONITOR_LOG_SINK_DEFAULT,
    WEFT_TASK_MONITOR_LOG_SINKS,
    WEFT_TASK_MONITOR_PROCESSOR_BUILTINS,
    WEFT_TASK_MONITOR_PROCESSOR_DEFAULT,
    WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
)
from weft.context import WeftContext
from weft.core import task_evidence
from weft.helpers import iter_queue_entries

TaskLogObserver = Callable[[str, str, int], None]


@dataclass(frozen=True, slots=True)
class TaskMonitorRuntimeConfig:
    """Typed config used by one supervised task-monitor process.

    Spec: [CC-2.3], [MF-5]
    """

    enabled: bool = WEFT_TASK_MONITOR_ENABLED_DEFAULT
    interval_seconds: int = WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT
    batch_size: int = WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT
    processor: str = WEFT_TASK_MONITOR_PROCESSOR_DEFAULT
    log_sink: str = WEFT_TASK_MONITOR_LOG_SINK_DEFAULT
    restart_backoff_seconds: float = WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> TaskMonitorRuntimeConfig:
        """Build typed runtime config from a loaded Weft config mapping."""

        enabled = bool(
            config.get("WEFT_TASK_MONITOR_ENABLED", WEFT_TASK_MONITOR_ENABLED_DEFAULT)
        )
        interval_seconds = int(
            config.get(
                "WEFT_TASK_MONITOR_INTERVAL_SECONDS",
                WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT,
            )
        )
        if interval_seconds < HEARTBEAT_MIN_INTERVAL_SECONDS:
            raise ValueError(
                "WEFT_TASK_MONITOR_INTERVAL_SECONDS must be at least "
                f"{HEARTBEAT_MIN_INTERVAL_SECONDS}"
            )

        batch_size = int(
            config.get(
                "WEFT_TASK_MONITOR_BATCH_SIZE",
                WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
            )
        )
        if batch_size <= 0:
            raise ValueError("WEFT_TASK_MONITOR_BATCH_SIZE must be positive")

        processor = str(
            config.get(
                "WEFT_TASK_MONITOR_PROCESSOR",
                WEFT_TASK_MONITOR_PROCESSOR_DEFAULT,
            )
        ).strip()
        if not processor:
            raise ValueError("WEFT_TASK_MONITOR_PROCESSOR must be non-empty")
        if (
            processor not in WEFT_TASK_MONITOR_PROCESSOR_BUILTINS
            and ":" not in processor
        ):
            raise ValueError(
                "WEFT_TASK_MONITOR_PROCESSOR must be a built-in processor name "
                "or a module:function reference"
            )

        log_sink = str(
            config.get("WEFT_TASK_MONITOR_LOG_SINK", WEFT_TASK_MONITOR_LOG_SINK_DEFAULT)
        ).strip()
        if log_sink not in WEFT_TASK_MONITOR_LOG_SINKS:
            allowed = ", ".join(sorted(WEFT_TASK_MONITOR_LOG_SINKS))
            raise ValueError(f"WEFT_TASK_MONITOR_LOG_SINK must be one of: {allowed}")

        restart_backoff_seconds = float(
            config.get(
                "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS",
                WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
            )
        )
        if restart_backoff_seconds <= 0:
            raise ValueError(
                "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS must be positive"
            )

        return cls(
            enabled=enabled,
            interval_seconds=interval_seconds,
            batch_size=batch_size,
            processor=processor,
            log_sink=log_sink,
            restart_backoff_seconds=restart_backoff_seconds,
        )


@dataclass(frozen=True, slots=True)
class TaskMonitorCandidate:
    """One task-monitor candidate row discovered from broker evidence."""

    candidate_id: str
    tid: str | None
    queue: str | None
    message_id: int | None
    candidate_class: str
    reason: str
    safe_to_delete: bool
    payload_sha256: str | None = None
    payload_size_bytes: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskMonitorProcessorRequest:
    """Input supplied to one task-monitor processor invocation."""

    context: WeftContext
    config: TaskMonitorRuntimeConfig
    cycle_id: str
    monitor_tid: str
    candidates: tuple[TaskMonitorCandidate, ...]
    now_ns: int


@dataclass(frozen=True, slots=True)
class TaskMonitorProcessorResult:
    """Result returned by a task-monitor processor."""

    success: bool
    processed: int = 0
    deleted: int = 0
    reported: int = 0
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class TaskMonitorProcessor(Protocol):
    """Callable protocol for task-monitor processors."""

    def __call__(
        self,
        request: TaskMonitorProcessorRequest,
    ) -> TaskMonitorProcessorResult:
        """Process one monitor cycle."""


@dataclass(slots=True)
class TaskLogRow:
    """One decoded task-log row retained for exact candidate construction."""

    payload: dict[str, Any]
    message_id: int
    raw_message: str
    tid: str
    status: str | None
    terminal: bool


@dataclass(slots=True)
class ReducedTaskLog:
    """Latest lifecycle evidence reduced from task-log rows for one TID."""

    tid: str
    latest_payload: dict[str, Any]
    latest_timestamp: int
    latest_raw_message: str
    taskspec_payload: dict[str, Any] | None = None
    started_at: int | None = None
    completed_at: int | None = None
    terminal_payload: dict[str, Any] | None = None
    terminal_timestamp: int | None = None
    terminal_raw_message: str | None = None
    events_seen: int = 0


@dataclass(frozen=True, slots=True)
class TaskMonitorCycleSnapshot:
    """One deterministic non-consuming monitor cycle snapshot."""

    candidates: tuple[TaskMonitorCandidate, ...]
    last_task_log_timestamp: int | None
    events_scanned: int
    tids_seen: int


def resolve_task_monitor_processor(name: str) -> TaskMonitorProcessor:
    """Resolve a custom ``module:function`` processor reference.

    Built-in processors are handled directly by ``TaskMonitorTask`` so the
    supervised monitor has one cleanup path.
    """

    if name in WEFT_TASK_MONITOR_PROCESSOR_BUILTINS:
        raise ValueError(
            "built-in task-monitor processors are handled by TaskMonitorTask"
        )

    if ":" not in name:
        raise ValueError("task-monitor processor must be a module:function reference")
    module_name, function_name = name.split(":", 1)
    if not module_name or not function_name:
        raise ValueError("task-monitor processor reference is malformed")

    module = importlib.import_module(module_name)
    processor = getattr(module, function_name)
    if not callable(processor):
        raise TypeError(f"task-monitor processor {name!r} is not callable")
    return cast(TaskMonitorProcessor, processor)


def task_log_seen_candidate(
    *,
    queue_name: str,
    message: str,
    message_id: int,
) -> TaskMonitorCandidate | None:
    """Build a part-1 candidate from one task-log row.

    Returns ``None`` for rows that are not JSON objects with a task ID.
    """

    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    tid = payload.get("tid")
    if not isinstance(tid, str) or not tid:
        return None

    encoded = message.encode("utf-8", errors="replace")
    payload_sha256 = hashlib.sha256(encoded).hexdigest()
    candidate_class = "task_log_tid_seen"
    candidate_id = ":".join(
        (
            queue_name,
            str(message_id),
            candidate_class,
            payload_sha256,
        )
    )
    metadata: dict[str, Any] = {}
    event = payload.get("event")
    if isinstance(event, str):
        metadata["event"] = event
    status = payload.get("status")
    if isinstance(status, str):
        metadata["status"] = status

    return TaskMonitorCandidate(
        candidate_id=candidate_id,
        tid=tid,
        queue=queue_name,
        message_id=message_id,
        candidate_class=candidate_class,
        reason="task ID observed in task log",
        safe_to_delete=False,
        payload_sha256=payload_sha256,
        payload_size_bytes=len(encoded),
        metadata=metadata,
    )


def build_task_monitor_cycle_snapshot(
    ctx: WeftContext,
    *,
    since_timestamp: int | None = None,
    limit: int | None = None,
    monitor_tid: str | None = None,
    observer: TaskLogObserver | None = None,
) -> TaskMonitorCycleSnapshot:
    """Build part-2 lifecycle candidates from real broker queues.

    The snapshot is read-only. It reduces task-log rows by TID first, then
    inspects task-local queues only for TIDs discovered from the task log.

    Spec: [MF-5]
    """

    queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        scan = reduce_task_log_messages(
            iter_queue_entries(queue, since_timestamp=since_timestamp),
            since_timestamp=since_timestamp,
            limit=limit,
            observer=observer,
        )
    finally:
        queue.close()

    now_ns = time.time_ns()
    queue_counts = _queue_message_counts_by_name(ctx)
    lifecycle_candidates = [
        candidate
        for reduced in scan.reduced.values()
        if reduced.tid != monitor_tid
        for candidate in (
            _candidate_for_reduced(
                ctx,
                reduced,
                now_ns=now_ns,
                queue_counts=queue_counts,
            ),
        )
        if candidate is not None
    ]
    candidates = [*lifecycle_candidates]
    candidates.sort(key=lambda item: (item.tid or "", item.candidate_class))
    return TaskMonitorCycleSnapshot(
        candidates=tuple(candidates),
        last_task_log_timestamp=scan.last_task_log_timestamp,
        events_scanned=scan.events_scanned,
        tids_seen=len(scan.reduced),
    )


@dataclass(slots=True)
class _ReducedScan:
    reduced: dict[str, ReducedTaskLog] = field(default_factory=dict)
    rows: list[TaskLogRow] = field(default_factory=list)
    events_scanned: int = 0
    last_task_log_timestamp: int | None = None


def reduce_task_log_messages(
    queue_entries: Iterable[tuple[str, int]],
    *,
    since_timestamp: int | None = None,
    limit: int | None = None,
    observer: TaskLogObserver | None = None,
) -> _ReducedScan:
    """Reduce task-log messages by TID using generator/high-water semantics."""

    result = _ReducedScan()
    for message, timestamp in queue_entries:
        if since_timestamp is not None and timestamp <= since_timestamp:
            continue
        if observer is not None:
            observer(WEFT_GLOBAL_LOG_QUEUE, message, timestamp)
        result.events_scanned += 1
        result.last_task_log_timestamp = max(
            result.last_task_log_timestamp or 0,
            timestamp,
        )
        payload = _json_object(message)
        if payload is None:
            if limit is not None and result.events_scanned >= limit:
                break
            continue
        tid = payload.get("tid")
        if not isinstance(tid, str) or not tid:
            if limit is not None and result.events_scanned >= limit:
                break
            continue
        terminal = task_evidence.terminal_status_from_event(payload) is not None
        result.rows.append(
            TaskLogRow(
                payload=payload,
                message_id=timestamp,
                raw_message=message,
                tid=tid,
                status=_status_from_log_payload(payload),
                terminal=terminal,
            )
        )
        existing = result.reduced.get(tid)
        if existing is None:
            existing = ReducedTaskLog(
                tid=tid,
                latest_payload=payload,
                latest_timestamp=timestamp,
                latest_raw_message=message,
            )
            result.reduced[tid] = existing
        _update_reduced_task_log(existing, payload, timestamp, message)
        if limit is not None and result.events_scanned >= limit:
            break
    return result


def task_monitor_candidate_class_counts(
    candidates: Iterable[TaskMonitorCandidate],
) -> dict[str, int]:
    """Return stable candidate-class counts for control/status reporting."""

    counts: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        counts[candidate.candidate_class] += 1
    return dict(sorted(counts.items()))


def _update_reduced_task_log(
    reduced: ReducedTaskLog,
    payload: dict[str, Any],
    timestamp: int,
    raw_message: str,
) -> None:
    taskspec = payload.get("taskspec")
    if isinstance(taskspec, dict):
        reduced.taskspec_payload = taskspec
    started_at = _extract_state_timestamp(payload, "started_at")
    if started_at is not None:
        reduced.started_at = started_at
    completed_at = _extract_state_timestamp(payload, "completed_at")
    if completed_at is not None:
        reduced.completed_at = completed_at
    terminal = task_evidence.log_terminal_evidence(
        payload,
        tid=reduced.tid,
        timestamp=timestamp,
    )
    if terminal is not None:
        reduced.terminal_payload = payload
        reduced.terminal_timestamp = timestamp
        reduced.terminal_raw_message = raw_message
    if timestamp >= reduced.latest_timestamp:
        reduced.latest_payload = payload
        reduced.latest_timestamp = timestamp
        reduced.latest_raw_message = raw_message
    reduced.events_seen += 1


def _candidate_for_reduced(
    ctx: WeftContext,
    reduced: ReducedTaskLog,
    *,
    now_ns: int,
    queue_counts: Mapping[str, task_evidence.QueueMessageCounts],
) -> TaskMonitorCandidate | None:
    conflict_reason = _lifecycle_conflict_reason(reduced.latest_payload)
    snapshot = _best_evidence(
        ctx,
        reduced,
        now_ns=now_ns,
        queue_counts=queue_counts,
    )
    if conflict_reason is not None:
        return _snapshot_candidate(
            reduced,
            snapshot=snapshot,
            candidate_class="runtime_conflict",
            reason=conflict_reason,
            failure_owner="weft_lifecycle",
            now_ns=now_ns,
        )
    if snapshot is not None:
        candidate_class = _monitor_classification(snapshot)
        return _snapshot_candidate(
            reduced,
            snapshot=snapshot,
            candidate_class=candidate_class,
            reason=_classification_reason(snapshot, candidate_class),
            failure_owner=_failure_owner(snapshot, candidate_class),
            now_ns=now_ns,
        )

    status = _status_from_log_payload(reduced.latest_payload)
    candidate_class = "active"
    reason = "latest_task_log_is_nonterminal"
    failure_owner: str | None = None
    if status == "created" and _is_stale(reduced.latest_timestamp, now_ns):
        candidate_class = "stale_created"
        reason = "created_task_has_no_terminal_or_runtime_evidence"
        failure_owner = "weft_lifecycle"
    return _base_candidate(
        reduced,
        candidate_class=candidate_class,
        reason=reason,
        status=status,
        event=_event_from_payload(reduced.latest_payload),
        source="log",
        failure_owner=failure_owner,
        now_ns=now_ns,
    )


def _queue_message_counts_by_name(
    ctx: WeftContext,
) -> dict[str, task_evidence.QueueMessageCounts]:
    try:
        with ctx.broker() as db:
            queue_stats = db.get_queue_stats()
    except (BrokerError, OSError, RuntimeError):  # pragma: no cover - best effort
        return {}

    counts: dict[str, task_evidence.QueueMessageCounts] = {}
    for name, unclaimed, total in queue_stats:
        if not isinstance(name, str):
            continue
        counts[name] = task_evidence.QueueMessageCounts(
            queue=name,
            unclaimed=max(0, int(unclaimed)),
            total=max(0, int(total)),
        )
    return counts


def _best_evidence(
    ctx: WeftContext,
    reduced: ReducedTaskLog,
    *,
    now_ns: int,
    queue_counts: Mapping[str, task_evidence.QueueMessageCounts],
) -> task_evidence.TaskEvidenceSnapshot | None:
    if reduced.terminal_payload is not None:
        snapshot = task_evidence.log_terminal_evidence(
            reduced.terminal_payload,
            tid=reduced.tid,
            timestamp=reduced.terminal_timestamp,
        )
        if snapshot is not None:
            return snapshot
    local_snapshot = task_evidence.task_local_terminal_evidence(
        ctx,
        tid=reduced.tid,
        taskspec_payload=reduced.taskspec_payload,
    )
    if local_snapshot is not None:
        return local_snapshot
    outbox_name, _ctrl_out_name = task_evidence.queue_names_for_tid(
        reduced.tid,
        reduced.taskspec_payload,
    )
    return _claimed_outbox_result_evidence_from_counts(
        tid=reduced.tid,
        outbox_name=outbox_name,
        taskspec_payload=reduced.taskspec_payload,
        queue_counts=queue_counts,
        now_ns=now_ns,
    )


def _claimed_outbox_result_evidence_from_counts(
    *,
    tid: str,
    outbox_name: str,
    taskspec_payload: dict[str, Any] | None,
    queue_counts: Mapping[str, task_evidence.QueueMessageCounts],
    now_ns: int,
) -> task_evidence.TaskEvidenceSnapshot | None:
    if task_evidence.task_is_persistent_payload(
        taskspec_payload
    ) or task_evidence.task_is_interactive_payload(taskspec_payload):
        return None
    counts = queue_counts.get(outbox_name)
    if counts is None or counts.total <= 0:
        return None
    if counts.unclaimed > 0 or counts.claimed <= 0:
        return None
    snapshot = task_evidence.TaskEvidenceSnapshot(
        tid=tid,
        status="failed",
        classification="claimed_result_without_terminal",
        source="outbox",
        terminal=True,
        taskspec_payload=taskspec_payload,
        observed_at=now_ns,
        error=(
            f"Task {tid} result output is claimed in {outbox_name} and requires "
            "explicit recovery"
        ),
        metadata={
            "outbox_queue": outbox_name,
            "claimed_messages": counts.claimed,
            "total_messages": counts.total,
            "unclaimed_messages": counts.unclaimed,
        },
    )
    return replace(
        snapshot,
        reconciliation=task_evidence.reconciliation_for_claimed_result_without_terminal(
            snapshot
        ),
    )


def _snapshot_candidate(
    reduced: ReducedTaskLog,
    *,
    snapshot: task_evidence.TaskEvidenceSnapshot | None,
    candidate_class: str,
    reason: str,
    failure_owner: str | None,
    now_ns: int,
) -> TaskMonitorCandidate:
    if snapshot is None:
        return _base_candidate(
            reduced,
            candidate_class=candidate_class,
            reason=reason,
            status=_status_from_log_payload(reduced.latest_payload),
            event=_event_from_payload(reduced.latest_payload),
            source="log",
            failure_owner=failure_owner,
            now_ns=now_ns,
        )
    metadata = {
        "source": snapshot.source,
        "status": snapshot.status,
        "event": snapshot.event,
        "classification": snapshot.classification,
        "failure_owner": failure_owner,
        "terminal": snapshot.terminal,
        "cleanup_candidate": False,
        "task_name": _task_name(snapshot.taskspec_payload or reduced.taskspec_payload),
        "reconciliation": snapshot.reconciliation,
        **dict(snapshot.metadata),
    }
    queue_name, message_id = _evidence_queue_target(snapshot, reduced)
    return _candidate_from_metadata(
        tid=reduced.tid,
        queue_name=queue_name,
        message_id=message_id,
        candidate_class=candidate_class,
        reason=reason,
        safe_to_delete=False,
        raw_payload=_raw_payload_for_target(snapshot, reduced),
        metadata=metadata,
    )


def _base_candidate(
    reduced: ReducedTaskLog,
    *,
    candidate_class: str,
    reason: str,
    status: str | None,
    event: str | None,
    source: str,
    failure_owner: str | None,
    now_ns: int,
) -> TaskMonitorCandidate:
    metadata = {
        "source": source,
        "status": status,
        "event": event,
        "failure_owner": failure_owner,
        "terminal": status in TERMINAL_TASK_STATUSES if status is not None else False,
        "cleanup_candidate": False,
        "task_name": _task_name(reduced.taskspec_payload),
        "age_seconds": max(0.0, (now_ns - reduced.latest_timestamp) / 1_000_000_000),
    }
    return _candidate_from_metadata(
        tid=reduced.tid,
        queue_name=WEFT_GLOBAL_LOG_QUEUE,
        message_id=reduced.latest_timestamp,
        candidate_class=candidate_class,
        reason=reason,
        safe_to_delete=False,
        raw_payload=reduced.latest_raw_message,
        metadata=metadata,
    )


def _candidate_from_metadata(
    *,
    tid: str,
    queue_name: str | None,
    message_id: int | None,
    candidate_class: str,
    reason: str,
    safe_to_delete: bool,
    raw_payload: str | None,
    metadata: Mapping[str, Any],
) -> TaskMonitorCandidate:
    encoded = (
        raw_payload.encode("utf-8", errors="replace")
        if raw_payload is not None
        else json.dumps(metadata, sort_keys=True, default=str).encode()
    )
    payload_sha256 = hashlib.sha256(encoded).hexdigest()
    candidate_id = ":".join(
        (
            tid,
            queue_name or "none",
            str(message_id or 0),
            candidate_class,
            payload_sha256,
        )
    )
    return TaskMonitorCandidate(
        candidate_id=candidate_id,
        tid=tid,
        queue=queue_name,
        message_id=message_id,
        candidate_class=candidate_class,
        reason=reason,
        safe_to_delete=safe_to_delete,
        payload_sha256=payload_sha256,
        payload_size_bytes=len(encoded),
        metadata=metadata,
    )


def _evidence_queue_target(
    snapshot: task_evidence.TaskEvidenceSnapshot,
    reduced: ReducedTaskLog,
) -> tuple[str | None, int | None]:
    if snapshot.ack_targets:
        target = snapshot.ack_targets[0]
        return target.queue, target.message_id
    if snapshot.source == "log":
        return WEFT_GLOBAL_LOG_QUEUE, snapshot.observed_at or reduced.latest_timestamp
    queue_name = snapshot.metadata.get("outbox_queue")
    if isinstance(queue_name, str) and queue_name:
        return queue_name, snapshot.observed_at
    return None, snapshot.observed_at


def _raw_payload_for_target(
    snapshot: task_evidence.TaskEvidenceSnapshot,
    reduced: ReducedTaskLog,
) -> str | None:
    if snapshot.source == "log":
        return reduced.terminal_raw_message or reduced.latest_raw_message
    return None


def _monitor_classification(snapshot: task_evidence.TaskEvidenceSnapshot) -> str:
    if snapshot.classification == "terminal_log" and snapshot.status != "completed":
        return "domain_failure"
    return snapshot.classification


def _classification_reason(
    snapshot: task_evidence.TaskEvidenceSnapshot,
    candidate_class: str,
) -> str:
    if candidate_class == "domain_failure":
        return "terminal_task_log_reports_failed_task_or_runner"
    if snapshot.classification == "claimed_result_without_terminal":
        return "claimed_outbox_blocks_result_classification"
    if snapshot.classification == "result_without_terminal":
        return "final_outbox_without_terminal_task_log"
    if snapshot.classification == "wrapper_lost":
        return "manager_wrapper_lost_before_terminal_task_log"
    if snapshot.classification == "terminal_ctrl_out":
        return "terminal_ctrl_out_without_task_log"
    return "terminal_task_log"


def _failure_owner(
    snapshot: task_evidence.TaskEvidenceSnapshot,
    candidate_class: str,
) -> str | None:
    if (
        snapshot.classification in TASK_MONITOR_WEFT_ANOMALY_CLASSIFICATIONS
        or candidate_class in TASK_MONITOR_WEFT_ANOMALY_CLASSIFICATIONS
        or candidate_class == "claimed_result_without_terminal"
    ):
        return "weft_lifecycle"
    if snapshot.status == "completed":
        return None
    if snapshot.terminal:
        return "task_or_runner"
    return None


def _lifecycle_conflict_reason(payload: dict[str, Any]) -> str | None:
    status = _status_from_log_payload(payload)
    completed_at = _extract_state_timestamp(payload, "completed_at")
    event = _event_from_payload(payload)
    if (
        isinstance(status, str)
        and status not in TERMINAL_TASK_STATUSES
        and completed_at is not None
    ):
        return "nonterminal_status_with_completed_at"
    if (
        isinstance(status, str)
        and status not in TERMINAL_TASK_STATUSES
        and isinstance(event, str)
        and event in TERMINAL_TASK_EVENTS
    ):
        return "nonterminal_status_with_terminal_event"
    return None


def _is_stale(timestamp: int, now_ns: int) -> bool:
    stale_ns = int(STATUS_RUNTIMELESS_STALE_AFTER_SECONDS * 1_000_000_000)
    return now_ns - timestamp > stale_ns


def _extract_state_timestamp(payload: dict[str, Any], key: str) -> int | None:
    taskspec = payload.get("taskspec")
    if not isinstance(taskspec, dict):
        return None
    state = taskspec.get("state")
    if not isinstance(state, dict):
        return None
    value = state.get(key)
    return value if isinstance(value, int) else None


def _status_from_log_payload(payload: Mapping[str, Any]) -> str | None:
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    taskspec = payload.get("taskspec")
    state = taskspec.get("state") if isinstance(taskspec, Mapping) else None
    state_status = state.get("status") if isinstance(state, Mapping) else None
    return state_status if isinstance(state_status, str) and state_status else None


def _event_from_payload(payload: Mapping[str, Any]) -> str | None:
    event = payload.get("event")
    return event if isinstance(event, str) and event else None


def _task_name(taskspec_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(taskspec_payload, dict):
        return None
    value = taskspec_payload.get("name")
    return value if isinstance(value, str) and value else None


def _json_object(message: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "ReducedTaskLog",
    "TaskMonitorCandidate",
    "TaskMonitorCycleSnapshot",
    "TaskMonitorProcessor",
    "TaskMonitorProcessorRequest",
    "TaskMonitorProcessorResult",
    "TaskMonitorRuntimeConfig",
    "build_task_monitor_cycle_snapshot",
    "reduce_task_log_messages",
    "resolve_task_monitor_processor",
    "task_log_seen_candidate",
    "task_monitor_candidate_class_counts",
]
