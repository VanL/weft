"""Foreground lifecycle monitor archive command.

The lifecycle monitor scans task-log evidence without consuming broker
messages, emits compact JSONL archive records, and stores an operational
checkpoint only after archive output succeeds.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.1], [OBS.2], [OBS.3]
- docs/specifications/10-CLI_Interface.md [CLI-6]
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from simplebroker.ext import BrokerError
from weft._constants import (
    LIFECYCLE_MONITOR_ARCHIVE_SUBDIR,
    LIFECYCLE_MONITOR_CHECKPOINT_PATH,
    LIFECYCLE_MONITOR_SCHEMA_VERSION,
    LIFECYCLE_MONITOR_WEFT_ANOMALY_CLASSIFICATIONS,
    TASKSPEC_TID_SHORT_LENGTH,
)
from weft.commands import task_evidence
from weft.context import WeftContext, build_context
from weft.core.tasks.lifecycle_monitor import (
    LifecycleMonitorTask,
    make_lifecycle_monitor_taskspec,
)

ArchiveSinkName = Literal["stdout", "disk"]


@dataclass(frozen=True, slots=True)
class LifecycleMonitorConfig:
    """Configuration for one foreground lifecycle monitor invocation."""

    context_path: str | Path | None = None
    once: bool = True
    follow: bool = False
    sink: ArchiveSinkName = "stdout"
    archive_dir: Path | None = None
    checkpoint_path: Path | None = None
    no_checkpoint: bool = False
    since_timestamp: int | None = None
    limit: int | None = None
    json_output: bool = False
    monitor_name: str = "default"


@dataclass(frozen=True, slots=True)
class LifecycleMonitorCheckpoint:
    """Operational cursor for lifecycle monitor task-log scans."""

    schema_version: int
    monitor_name: str
    updated_at: int
    last_task_log_timestamp: int


@dataclass(frozen=True, slots=True)
class LifecycleMonitorResult:
    """Command result returned to the CLI adapter."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    archive_path: Path | None = None
    records_written: int = 0
    events_scanned: int = 0
    tids_seen: int = 0
    summaries_emitted: int = 0
    checkpoint_timestamp: int | None = None

    def summary_payload(self) -> dict[str, Any]:
        """Return the final non-archive command summary."""

        payload: dict[str, Any] = {
            "exit_code": self.exit_code,
            "records_written": self.records_written,
            "events_scanned": self.events_scanned,
            "tids_seen": self.tids_seen,
            "summaries_emitted": self.summaries_emitted,
            "checkpoint_timestamp": self.checkpoint_timestamp,
        }
        if self.archive_path is not None:
            payload["archive_path"] = str(self.archive_path)
        return payload


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
class TaskLifecycleSummary:
    """Compact archive summary for one terminal or anomalous task."""

    record: dict[str, Any]


@dataclass(slots=True)
class ScanResult:
    """Reduced scan result before archive serialization."""

    reduced: dict[str, ReducedTaskLog] = field(default_factory=dict)
    events_scanned: int = 0
    last_task_log_timestamp: int | None = None


class StdoutArchiveSink:
    """Archive sink that buffers JSONL records for stdout."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    @property
    def output(self) -> str:
        """Buffered JSONL output."""

        if not self._lines:
            return ""
        return "\n".join(self._lines) + "\n"

    @property
    def archive_path(self) -> Path | None:
        """Stdout has no archive path."""

        return None

    def write_records(self, records: Iterable[dict[str, Any]]) -> int:
        """Append JSONL records to the stdout buffer."""

        count = 0
        for record in records:
            self._lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
            count += 1
        sys.stdout.flush()
        return count


class DiskJsonlArchiveSink:
    """Append-only date-partitioned JSONL archive sink."""

    def __init__(self, archive_dir: Path, *, run_date: str) -> None:
        self._archive_dir = archive_dir
        self._archive_path = archive_dir / f"{run_date}.jsonl"

    @property
    def archive_path(self) -> Path:
        """Path receiving archive records."""

        return self._archive_path

    def write_records(self, records: Iterable[dict[str, Any]]) -> int:
        """Append records to the date-partitioned archive file."""

        self._archive_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        with self._archive_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                count += 1
            handle.flush()
        return count


ArchiveSink = StdoutArchiveSink | DiskJsonlArchiveSink


def default_archive_dir(ctx: WeftContext) -> Path:
    """Return the default lifecycle monitor archive directory."""

    return ctx.weft_dir / LIFECYCLE_MONITOR_ARCHIVE_SUBDIR


def default_checkpoint_path(ctx: WeftContext) -> Path:
    """Return the default lifecycle monitor checkpoint path."""

    return ctx.weft_dir / LIFECYCLE_MONITOR_CHECKPOINT_PATH


def _record_base(
    record_type: str, monitor_run_id: str, emitted_at: int
) -> dict[str, Any]:
    return {
        "schema_version": LIFECYCLE_MONITOR_SCHEMA_VERSION,
        "record_type": record_type,
        "monitor_run_id": monitor_run_id,
        "emitted_at": emitted_at,
    }


def _load_checkpoint(
    path: Path, monitor_name: str
) -> LifecycleMonitorCheckpoint | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid lifecycle monitor checkpoint: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid lifecycle monitor checkpoint: {path}")
    try:
        schema_version = int(payload["schema_version"])
        last_timestamp = int(payload["last_task_log_timestamp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid lifecycle monitor checkpoint: {path}") from exc
    name = payload.get("monitor_name")
    if name != monitor_name or schema_version != LIFECYCLE_MONITOR_SCHEMA_VERSION:
        raise ValueError(f"Invalid lifecycle monitor checkpoint: {path}")
    updated_at = payload.get("updated_at")
    return LifecycleMonitorCheckpoint(
        schema_version=schema_version,
        monitor_name=monitor_name,
        updated_at=int(updated_at) if isinstance(updated_at, int) else 0,
        last_task_log_timestamp=last_timestamp,
    )


def _write_checkpoint(path: Path, checkpoint: LifecycleMonitorCheckpoint) -> None:
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


def _extract_state_timestamp(payload: dict[str, Any], key: str) -> int | None:
    taskspec = payload.get("taskspec")
    if not isinstance(taskspec, dict):
        return None
    state = taskspec.get("state")
    if not isinstance(state, dict):
        return None
    value = state.get(key)
    return value if isinstance(value, int) else None


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
        started_at = _extract_state_timestamp(payload, "started_at")
        if started_at is not None:
            existing.started_at = started_at
        completed_at = _extract_state_timestamp(payload, "completed_at")
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


def _scan_with_lifecycle_task(
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

    monitor = LifecycleMonitorTask(
        ctx.broker_target,
        make_lifecycle_monitor_taskspec(),
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
    if snapshot.classification in LIFECYCLE_MONITOR_WEFT_ANOMALY_CLASSIFICATIONS:
        return "weft_lifecycle"
    if snapshot.status == "completed":
        return None
    if snapshot.terminal:
        return "task_or_runner"
    return None


def _archive_classification(snapshot: task_evidence.TaskEvidenceSnapshot) -> str:
    if snapshot.classification == "terminal_log" and snapshot.status != "completed":
        return "domain_failure"
    return snapshot.classification


def _task_name(taskspec_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(taskspec_payload, dict):
        return None
    value = taskspec_payload.get("name")
    return value if isinstance(value, str) and value else None


def _build_summary_record(
    snapshot: task_evidence.TaskEvidenceSnapshot,
    reduced: ReducedTaskLog,
    *,
    monitor_run_id: str,
    emitted_at: int,
) -> dict[str, Any]:
    classification = _archive_classification(snapshot)
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
            "name": _task_name(snapshot.taskspec_payload or reduced.taskspec_payload),
            "status": snapshot.status,
            "classification": classification,
            "source": source,
            "event": event,
            "last_task_log_timestamp": reduced.latest_timestamp,
            "started_at": snapshot.started_at or reduced.started_at,
            "completed_at": snapshot.completed_at or reduced.completed_at,
            "return_code": snapshot.return_code,
            "error": snapshot.error,
            "failure_owner": _failure_owner(snapshot),
            "cleanup_candidate": False,
            "reconciliation": snapshot.reconciliation
            or {"classification": classification},
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
            "checkpoint_timestamp": checkpoint_timestamp,
        }
    )
    records.append(completed)
    return records, summaries, classification_counts, active_tasks


def _resolve_since(
    config: LifecycleMonitorConfig,
    checkpoint: LifecycleMonitorCheckpoint | None,
) -> int | None:
    if config.since_timestamp is not None:
        return config.since_timestamp
    if checkpoint is not None:
        return checkpoint.last_task_log_timestamp
    return None


def run_lifecycle_monitor(config: LifecycleMonitorConfig) -> LifecycleMonitorResult:
    """Run one foreground lifecycle monitor pass.

    The first Release 4 implementation accepts `follow` for CLI shape but keeps
    command execution to a single pass unless a later slice expands it.
    """

    if config.sink not in {"stdout", "disk"}:
        return LifecycleMonitorResult(1, stderr=f"Invalid sink: {config.sink}")
    if config.sink == "stdout" and config.json_output:
        return LifecycleMonitorResult(
            1,
            stderr="--sink stdout and --json cannot be combined",
        )
    if config.limit is not None and config.limit < 0:
        return LifecycleMonitorResult(1, stderr="--limit must be non-negative")

    try:
        ctx = build_context(spec_context=config.context_path)
        checkpoint_path = config.checkpoint_path or default_checkpoint_path(ctx)
        checkpoint = (
            None
            if config.no_checkpoint
            else _load_checkpoint(checkpoint_path, config.monitor_name)
        )
        since_timestamp = _resolve_since(config, checkpoint)
        scan = _scan_with_lifecycle_task(
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

        sink: ArchiveSink
        if config.sink == "stdout":
            sink = StdoutArchiveSink()
        else:
            sink = DiskJsonlArchiveSink(
                config.archive_dir or default_archive_dir(ctx),
                run_date=run_date,
            )
        records_written = sink.write_records(records)

        if not config.no_checkpoint and checkpoint_timestamp is not None:
            _write_checkpoint(
                checkpoint_path,
                LifecycleMonitorCheckpoint(
                    schema_version=LIFECYCLE_MONITOR_SCHEMA_VERSION,
                    monitor_name=config.monitor_name,
                    updated_at=time.time_ns(),
                    last_task_log_timestamp=checkpoint_timestamp,
                ),
            )

        stdout = ""
        if isinstance(sink, StdoutArchiveSink):
            stdout = sink.output
        elif config.json_output:
            stdout = json.dumps(
                {
                    "records_written": records_written,
                    "events_scanned": scan.events_scanned,
                    "tids_seen": len(scan.reduced),
                    "summaries_emitted": summaries,
                    "checkpoint_timestamp": checkpoint_timestamp,
                    "archive_path": str(sink.archive_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        else:
            stdout = (
                f"Lifecycle monitor wrote {records_written} record(s) "
                f"to {sink.archive_path}"
            )
        return LifecycleMonitorResult(
            0,
            stdout=stdout,
            archive_path=sink.archive_path,
            records_written=records_written,
            events_scanned=scan.events_scanned,
            tids_seen=len(scan.reduced),
            summaries_emitted=summaries,
            checkpoint_timestamp=checkpoint_timestamp,
        )
    except (ValueError, OSError, BrokerError, RuntimeError) as exc:
        return LifecycleMonitorResult(1, stderr=str(exc))
