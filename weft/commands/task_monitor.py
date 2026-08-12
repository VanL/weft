"""Foreground task monitor log command.

The task monitor scans task-log evidence without consuming broker messages,
emits compact JSONL operational log records, and stores an operational
checkpoint only after disk/stdout output succeeds.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.1], [OBS.2], [OBS.3]
- docs/specifications/10-CLI_Interface.md [CLI-6]
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import weft.core.task_evidence as task_evidence  # noqa: PLR0402 approved [TS-3.1] [RUFF-SUP-253] exception
from simplebroker import format_message_id
from simplebroker.ext import BrokerError
from weft._constants import (
    BROKER_BACKED_RECONCILIATION_OBSERVATION_CLASSIFICATIONS,
    TASK_MONITOR_CHECKPOINT_PATH,
    TASK_MONITOR_LOG_SUBDIR,
    TASK_MONITOR_SCHEMA_VERSION,
    TASK_MONITOR_WEFT_ANOMALY_CLASSIFICATIONS,
    TASKSPEC_TID_SHORT_LENGTH,
    WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS_DEFAULT,
)
from weft._exceptions import CommandExecutionError, CommandUsageError
from weft.commands.types import CommandStream
from weft.context import WeftContext, build_context
from weft.core.monitor.task_monitor import (
    TaskMonitor,
    make_task_monitor_taskspec,
)

TaskMonitorSinkName = Literal["stdout", "disk"]


@dataclass(frozen=True, slots=True)
class TaskMonitorConfig:
    """Configuration for one foreground task monitor invocation."""

    context: str | Path | None = None
    follow: bool = False
    sink: TaskMonitorSinkName = "stdout"
    log_dir: Path | None = None
    checkpoint: Path | None = None
    no_checkpoint: bool = False
    since: int | None = None
    limit: int | None = None
    monitor_name: str = "default"

    @property
    def context_path(self) -> str | Path | None:
        """Internal compatibility spelling for context construction."""

        return self.context

    @property
    def checkpoint_path(self) -> Path | None:
        """Internal compatibility spelling for checkpoint helpers."""

        return self.checkpoint

    @property
    def since_timestamp(self) -> int | None:
        """Internal compatibility spelling for scan helpers."""

        return self.since


@dataclass(frozen=True, slots=True)
class TaskMonitorCheckpoint:
    """Operational cursor for task monitor task-log scans."""

    schema_version: int
    monitor_name: str
    updated_at: int
    last_task_log_timestamp: int


@dataclass(frozen=True, slots=True)
class TaskMonitorResult:
    """Structured result of one task-monitor pass. Spec: [PY-2]."""

    log_path: Path | None
    records_written: int
    events_scanned: int
    tids_seen: int
    summaries_emitted: int
    checkpoint_timestamp: int | None
    records: tuple[TaskMonitorRecord, ...]


@dataclass(frozen=True, slots=True)
class TaskMonitorRecord:
    """Lossless pre-serialization task-monitor record. Spec: [PY-2]."""

    record: Mapping[str, Any]


@dataclass(slots=True)
class ReducedTaskLog:
    """Reduced task-log evidence for one seen TID."""

    tid: str
    latest_payload: dict[str, Any]
    latest_timestamp: int
    taskspec_payload: dict[str, Any] | None = None
    started_at: int | None = None
    completed_at: int | None = None
    terminal_payload: dict[str, Any] | None = None
    terminal_timestamp: int | None = None
    events_seen: int = 0


@dataclass(frozen=True, slots=True)
class TaskMonitorSummary:
    """Compact task-monitor summary for one terminal or anomalous task."""

    record: Mapping[str, Any]


@dataclass(slots=True)
class ScanResult:
    """Reduced scan result before task-monitor serialization."""

    reduced: dict[str, ReducedTaskLog] = field(default_factory=dict)
    events_scanned: int = 0
    last_task_log_timestamp: int | None = None


class StdoutTaskMonitorSink:
    """Task-monitor sink that buffers JSONL records for stdout."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    @property
    def output(self) -> str:
        """Buffered JSONL output."""

        if not self._lines:
            return ""
        return "\n".join(self._lines) + "\n"

    @property
    def log_path(self) -> Path | None:
        """Stdout has no log path."""

        return None

    def write_records(self, records: Iterable[dict[str, Any]]) -> int:
        """Append JSONL records to the stdout buffer."""

        count = 0
        for record in records:
            self._lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
            count += 1
        return count


class DiskJsonlTaskMonitorSink:
    """Append-only date-partitioned JSONL task-monitor sink."""

    def __init__(self, log_dir: Path, *, run_date: str) -> None:
        self._log_dir = log_dir
        self._log_path = log_dir / f"{run_date}.jsonl"

    @property
    def log_path(self) -> Path:
        """Path receiving task-monitor records."""

        return self._log_path

    def write_records(self, records: Iterable[dict[str, Any]]) -> int:
        """Append records to the date-partitioned task-monitor file."""

        self._log_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        with self._log_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                count += 1
            handle.flush()
        return count


TaskMonitorSink = StdoutTaskMonitorSink | DiskJsonlTaskMonitorSink


def default_log_dir(ctx: WeftContext) -> Path:
    """Return the default task monitor log directory."""

    return ctx.logs_dir / TASK_MONITOR_LOG_SUBDIR


def default_checkpoint_path(ctx: WeftContext) -> Path:
    """Return the default task monitor checkpoint path."""

    return ctx.weft_dir / TASK_MONITOR_CHECKPOINT_PATH


def _record_base(
    record_type: str, monitor_run_id: str, emitted_at: int
) -> dict[str, Any]:
    return {
        "schema_version": TASK_MONITOR_SCHEMA_VERSION,
        "record_type": record_type,
        "monitor_run_id": monitor_run_id,
        "emitted_at": emitted_at,
    }


def _load_checkpoint(path: Path, monitor_name: str) -> TaskMonitorCheckpoint | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid task monitor checkpoint: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid task monitor checkpoint: {path}")  # noqa: TRY004 approved [TS-3.1] [RUFF-SUP-265] exception
    try:
        schema_version = int(payload["schema_version"])
        last_timestamp = int(payload["last_task_log_timestamp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid task monitor checkpoint: {path}") from exc
    name = payload.get("monitor_name")
    if name != monitor_name or schema_version != TASK_MONITOR_SCHEMA_VERSION:
        raise ValueError(f"Invalid task monitor checkpoint: {path}")
    updated_at = payload.get("updated_at")
    return TaskMonitorCheckpoint(
        schema_version=schema_version,
        monitor_name=monitor_name,
        updated_at=int(updated_at) if isinstance(updated_at, int) else 0,
        last_task_log_timestamp=last_timestamp,
    )


def _write_checkpoint(path: Path, checkpoint: TaskMonitorCheckpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": checkpoint.schema_version,
        "monitor_name": checkpoint.monitor_name,
        "updated_at": checkpoint.updated_at,
        "last_task_log_timestamp": checkpoint.last_task_log_timestamp,
    }
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _reduce_task_log(
    queue_entries: Iterable[tuple[dict[str, Any], int]],
    *,
    limit: int | None,
) -> ScanResult:
    result = ScanResult()
    for payload, timestamp in queue_entries:
        tid = payload.get("tid")
        if not isinstance(tid, str) or not tid:
            continue
        result.events_scanned += 1
        result.last_task_log_timestamp = max(
            result.last_task_log_timestamp or 0,
            timestamp,
        )
        taskspec = payload.get("taskspec")
        existing = result.reduced.get(tid)
        if existing is None:
            existing = ReducedTaskLog(
                tid=tid,
                latest_payload=payload,
                latest_timestamp=timestamp,
            )
            result.reduced[tid] = existing
        if isinstance(taskspec, dict):
            existing.taskspec_payload = taskspec
        started_at = task_evidence.state_timestamp_from_log_payload(
            payload,
            "started_at",
        )
        if started_at is not None:
            existing.started_at = started_at
        completed_at = task_evidence.state_timestamp_from_log_payload(
            payload,
            "completed_at",
        )
        if completed_at is not None:
            existing.completed_at = completed_at
        terminal = task_evidence.log_terminal_evidence(
            payload,
            tid=tid,
            timestamp=timestamp,
        )
        if terminal is not None:
            existing.terminal_payload = payload
            existing.terminal_timestamp = timestamp
        if timestamp >= existing.latest_timestamp:
            existing.latest_payload = payload
            existing.latest_timestamp = timestamp
        existing.events_seen += 1
        if limit is not None and result.events_scanned >= limit:
            break
    return result


def _scan_with_task_monitor(
    ctx: WeftContext,
    *,
    since_timestamp: int | None,
    limit: int | None,
) -> ScanResult:
    entries: list[tuple[dict[str, Any], int]] = []

    def collect(_queue_name: str, message: str, timestamp: int) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            entries.append((payload, timestamp))

    monitor = TaskMonitor(
        ctx.broker_target,
        make_task_monitor_taskspec(),
        observer=collect,
        config=ctx.config,
    )
    try:
        monitor.scan_once(since_timestamp=since_timestamp, limit=limit)
    finally:
        monitor.stop()
    return _reduce_task_log(entries, limit=limit)


def _best_evidence(
    ctx: WeftContext,
    reduced: ReducedTaskLog,
) -> task_evidence.TaskEvidenceSnapshot | None:
    if reduced.terminal_payload is not None:
        snapshot = task_evidence.log_terminal_evidence(
            reduced.terminal_payload,
            tid=reduced.tid,
            timestamp=reduced.terminal_timestamp,
        )
        if snapshot is not None:
            return snapshot
    return task_evidence.task_local_terminal_evidence(
        ctx,
        tid=reduced.tid,
        taskspec_payload=reduced.taskspec_payload,
    )


def _failure_owner(snapshot: task_evidence.TaskEvidenceSnapshot) -> str | None:
    if snapshot.classification in TASK_MONITOR_WEFT_ANOMALY_CLASSIFICATIONS:
        return "weft_lifecycle"
    if snapshot.status == "completed":
        return None
    if snapshot.terminal:
        return "task_or_runner"
    return None


def _external_reconciliation(
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    """Project an owned broker-backed reconciliation for external JSON."""

    projected = dict(reconciliation)
    classification = projected.get("classification")
    observed_at = projected.get("observed_at")
    if (
        classification in BROKER_BACKED_RECONCILIATION_OBSERVATION_CLASSIFICATIONS
        and isinstance(observed_at, int)
        and not isinstance(observed_at, bool)
    ):
        projected["observed_at"] = format_message_id(observed_at)
    return projected


def _build_summary_record(
    snapshot: task_evidence.TaskEvidenceSnapshot,
    reduced: ReducedTaskLog,
    *,
    monitor_run_id: str,
    emitted_at: int,
) -> dict[str, Any]:
    classification = task_evidence.monitor_failure_classification(snapshot)
    observed_at = snapshot.observed_at or reduced.latest_timestamp
    source = snapshot.source
    event = snapshot.event
    summary_id = f"{reduced.tid}:{classification}:{observed_at}"
    record = _record_base("task_summary", monitor_run_id, emitted_at)
    record.update(
        {
            "summary_id": summary_id,
            "tid": reduced.tid,
            "tid_short": reduced.tid[-TASKSPEC_TID_SHORT_LENGTH:],
            "name": task_evidence.task_name_from_taskspec(
                snapshot.taskspec_payload or reduced.taskspec_payload
            ),
            "status": snapshot.status,
            "classification": classification,
            "source": source,
            "event": event,
            "last_task_log_timestamp": format_message_id(reduced.latest_timestamp),
            "started_at": snapshot.started_at or reduced.started_at,
            "completed_at": snapshot.completed_at or reduced.completed_at,
            "return_code": snapshot.return_code,
            "error": snapshot.error,
            "failure_owner": _failure_owner(snapshot),
            "cleanup_candidate": False,
            "reconciliation": _external_reconciliation(
                snapshot.reconciliation or {"classification": classification}
            ),
        }
    )
    return record


def _records_for_scan(
    ctx: WeftContext,
    scan: ScanResult,
    *,
    monitor_name: str,
    monitor_run_id: str,
    started_at: int,
    completed_at: int,
    checkpoint_timestamp: int | None,
) -> tuple[list[dict[str, Any]], int, dict[str, int], int]:
    records = [
        {
            **_record_base("monitor_run_started", monitor_run_id, started_at),
            "monitor_name": monitor_name,
        }
    ]
    classification_counts: dict[str, int] = {}
    active_tasks = 0
    summaries = 0
    for reduced in scan.reduced.values():
        snapshot = _best_evidence(ctx, reduced)
        if snapshot is None or not snapshot.terminal:
            active_tasks += 1
            continue
        record = _build_summary_record(
            snapshot,
            reduced,
            monitor_run_id=monitor_run_id,
            emitted_at=completed_at,
        )
        classification = str(record["classification"])
        classification_counts[classification] = (
            classification_counts.get(classification, 0) + 1
        )
        records.append(record)
        summaries += 1
    completed = _record_base("monitor_run_completed", monitor_run_id, completed_at)
    completed.update(
        {
            "events_scanned": scan.events_scanned,
            "tids_seen": len(scan.reduced),
            "summaries_emitted": summaries,
            "active_tasks": active_tasks,
            "classification_counts": classification_counts,
            "checkpoint_timestamp": (
                format_message_id(checkpoint_timestamp)
                if checkpoint_timestamp is not None
                else None
            ),
        }
    )
    records.append(completed)
    return records, summaries, classification_counts, active_tasks


def _resolve_since(
    config: TaskMonitorConfig,
    checkpoint: TaskMonitorCheckpoint | None,
) -> int | None:
    if config.since_timestamp is not None:
        return config.since_timestamp
    if checkpoint is not None:
        return checkpoint.last_task_log_timestamp
    return None


def run_task_monitor(config: TaskMonitorConfig) -> TaskMonitorResult:
    """Run one task-monitor pass and return pre-serialization records."""

    if config.sink not in {"stdout", "disk"}:
        raise CommandUsageError(f"Invalid sink: {config.sink}")
    if config.limit is not None and config.limit < 0:
        raise CommandUsageError("--limit must be non-negative")

    try:
        ctx = build_context(spec_context=config.context_path)
        checkpoint_path = config.checkpoint_path or default_checkpoint_path(ctx)
        checkpoint = (
            None
            if config.no_checkpoint
            else _load_checkpoint(checkpoint_path, config.monitor_name)
        )
        since_timestamp = _resolve_since(config, checkpoint)
        scan = _scan_with_task_monitor(
            ctx,
            since_timestamp=since_timestamp,
            limit=config.limit,
        )

        previous_checkpoint = (
            checkpoint.last_task_log_timestamp if checkpoint is not None else None
        )
        processed_checkpoint = scan.last_task_log_timestamp
        checkpoint_timestamp = processed_checkpoint
        if previous_checkpoint is not None and checkpoint_timestamp is not None:
            checkpoint_timestamp = max(previous_checkpoint, checkpoint_timestamp)
        elif previous_checkpoint is not None:
            checkpoint_timestamp = previous_checkpoint

        run_started = datetime.now(UTC)
        run_date = run_started.date().isoformat()
        monitor_run_id = f"{run_started.isoformat()}:pid-{os.getpid()}"
        started_at = time.time_ns()
        completed_at = time.time_ns()
        records, summaries, _counts, _active = _records_for_scan(
            ctx,
            scan,
            monitor_name=config.monitor_name,
            monitor_run_id=monitor_run_id,
            started_at=started_at,
            completed_at=completed_at,
            checkpoint_timestamp=checkpoint_timestamp,
        )

        log_path: Path | None = None
        if config.sink == "disk":
            sink = DiskJsonlTaskMonitorSink(
                config.log_dir or default_log_dir(ctx),
                run_date=run_date,
            )
            records_written = sink.write_records(records)
            log_path = sink.log_path
        else:
            records_written = len(records)

        if not config.no_checkpoint and checkpoint_timestamp is not None:
            _write_checkpoint(
                checkpoint_path,
                TaskMonitorCheckpoint(
                    schema_version=TASK_MONITOR_SCHEMA_VERSION,
                    monitor_name=config.monitor_name,
                    updated_at=time.time_ns(),
                    last_task_log_timestamp=checkpoint_timestamp,
                ),
            )

        return TaskMonitorResult(
            log_path=log_path,
            records_written=records_written,
            events_scanned=scan.events_scanned,
            tids_seen=len(scan.reduced),
            summaries_emitted=summaries,
            checkpoint_timestamp=checkpoint_timestamp,
            records=tuple(TaskMonitorRecord(record) for record in records),
        )
    except (ValueError, OSError, BrokerError, RuntimeError) as exc:
        raise CommandExecutionError(str(exc)) from exc


class _TaskMonitorSummaryStream(Iterator[TaskMonitorSummary]):
    """Closable polling stream over newly emitted task summaries."""

    def __init__(self, config: TaskMonitorConfig) -> None:
        self._config = config
        self._buffer: list[TaskMonitorSummary] = []
        self._closed = False

    def __iter__(self) -> _TaskMonitorSummaryStream:
        return self

    def __next__(self) -> TaskMonitorSummary:
        while not self._closed:
            if self._buffer:
                return self._buffer.pop(0)
            result = run_task_monitor(self._config)
            self._buffer.extend(
                TaskMonitorSummary(record.record)
                for record in result.records
                if record.record.get("record_type") == "task_summary"
            )
            if not self._buffer:
                time.sleep(WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS_DEFAULT)
        raise StopIteration

    def close(self) -> None:
        """Stop polling; repeated calls are harmless."""

        self._closed = True
        self._buffer.clear()


def cmd_system_task_monitor(
    *,
    context: str | Path | None = None,
    follow: bool = False,
    sink: TaskMonitorSinkName = "stdout",
    log_dir: Path | None = None,
    checkpoint: Path | None = None,
    no_checkpoint: bool = False,
    since: int | None = None,
    limit: int | None = None,
) -> TaskMonitorResult | CommandStream[TaskMonitorSummary]:
    """Run once or follow task-monitor summaries without process output.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    config = TaskMonitorConfig(
        context=context,
        follow=follow,
        sink=sink,
        log_dir=log_dir,
        checkpoint=checkpoint,
        no_checkpoint=no_checkpoint,
        since=since,
        limit=limit,
    )
    if follow:
        return _TaskMonitorSummaryStream(config)
    return run_task_monitor(config)
