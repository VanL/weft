"""Status reporting helpers for the Weft CLI."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from simplebroker import Queue
from simplebroker.db import BrokerDB
from weft._constants import (
    TASKSPEC_TID_SHORT_LENGTH,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.helpers import format_byte_size, format_timestamp_ns_relative

StatusMapping = Mapping[str, int | float | str | None]
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "timeout", "cancelled", "killed"}
)


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


@dataclass(frozen=True)
class BrokerStatusSnapshot:
    """Immutable container for broker status metrics."""

    total_messages: int
    last_timestamp: int
    db_size: int

    @classmethod
    def from_mapping(cls, data: StatusMapping) -> BrokerStatusSnapshot:
        return cls(
            total_messages=_to_int(data.get("total_messages")),
            last_timestamp=_to_int(data.get("last_timestamp")),
            db_size=_to_int(data.get("db_size")),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "total_messages": self.total_messages,
            "last_timestamp": self.last_timestamp,
            "db_size": self.db_size,
        }

    def to_text(self) -> str:
        human_size = format_byte_size(self.db_size)
        relative_ts = format_timestamp_ns_relative(self.last_timestamp)

        timestamp_line = f"last_timestamp: {self.last_timestamp}"
        if relative_ts:
            timestamp_line += f" ({relative_ts})"

        size_line = f"db_size: {self.db_size} bytes ({human_size})"

        return "\n".join(
            (
                f"total_messages: {self.total_messages}",
                timestamp_line,
                size_line,
            )
        )


@dataclass(frozen=True)
class TaskSnapshot:
    tid: str
    tid_short: str
    name: str
    status: str
    event: str
    started_at: int | None
    completed_at: int | None
    last_timestamp: int
    duration_seconds: float | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tid": self.tid,
            "tid_short": self.tid_short,
            "name": self.name,
            "status": self.status,
            "event": self.event,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "last_timestamp": self.last_timestamp,
            "duration_seconds": self.duration_seconds,
            "metadata": self.metadata,
        }


def _resolve_context(
    spec_context: str | os.PathLike[str] | None = None,
) -> WeftContext:
    if spec_context:
        return build_context(spec_context=spec_context)

    env_context = os.environ.get("WEFT_CONTEXT")
    if env_context:
        return build_context(spec_context=env_context)

    return build_context(spec_context=os.getcwd())


def collect_broker_status(ctx: WeftContext) -> BrokerStatusSnapshot:
    with BrokerDB(str(ctx.database_path)) as db:
        metrics = db.status()
    return BrokerStatusSnapshot.from_mapping(metrics)


def collect_status(ctx: WeftContext) -> BrokerStatusSnapshot:
    """Backward-compatible alias for :func:`collect_broker_status`."""

    return collect_broker_status(ctx)


def _queue(
    ctx: WeftContext,
    name: str,
    *,
    persistent: bool = False,
) -> Queue:
    return Queue(
        name,
        db_path=str(ctx.database_path),
        persistent=persistent,
        config=ctx.broker_config,
    )


def _collect_manager_records(
    ctx: WeftContext, *, include_stopped: bool = False
) -> list[dict[str, Any]]:
    queue = _queue(ctx, WEFT_WORKERS_REGISTRY_QUEUE)
    try:
        records_raw = cast(
            Sequence[tuple[str, int]] | None,
            queue.peek_many(limit=2000, with_timestamps=True),
        )
    except Exception:
        return []

    if not records_raw:
        return []

    records: dict[str, dict[str, Any]] = {}
    for entry, timestamp in records_raw:
        try:
            data = cast(dict[str, Any], json.loads(entry))
        except json.JSONDecodeError:
            continue
        tid = data.get("tid")
        if not tid:
            continue
        data["_timestamp"] = timestamp
        existing = records.get(tid)
        previous_timestamp = _to_int(existing.get("_timestamp")) if existing else -1
        if existing is None or previous_timestamp < timestamp:
            records[tid] = data

    formatted: list[dict[str, Any]] = []
    for record in records.values():
        timestamp = _to_int(record.get("_timestamp"))
        record.pop("_timestamp", None)
        record["timestamp"] = timestamp
        record.setdefault("requests", WEFT_SPAWN_REQUESTS_QUEUE)
        formatted.append(record)
    formatted.sort(key=lambda rec: rec.get("timestamp", 0), reverse=True)
    if not include_stopped:
        formatted = [rec for rec in formatted if rec.get("status") != "stopped"]
    return formatted


def _format_manager_summary(records: list[dict[str, Any]]) -> str:
    if not records:
        return "Managers: none registered"

    lines = ["Managers:"]
    for record in records:
        tid = record.get("tid", "?")
        status = record.get("status", "unknown")
        role = record.get("role", "manager")
        pid = record.get("pid")
        requests = record.get("requests", WEFT_SPAWN_REQUESTS_QUEUE)
        outbox = record.get("outbox", "")
        timestamp = _to_int(record.get("timestamp"))
        relative_ts = format_timestamp_ns_relative(timestamp)
        ts_line = f"timestamp: {timestamp}"
        if relative_ts:
            ts_line += f" ({relative_ts})"

        lines.extend(
            [
                f"  - tid: {tid}",
                f"    role: {role}",
                f"    status: {status}",
                f"    pid: {pid if pid is not None else 'n/a'}",
                f"    requests: {requests}",
                f"    outbox: {outbox}",
                f"    {ts_line}",
            ]
        )

    return "\n".join(lines)


def _read_tid_mappings(ctx: WeftContext) -> dict[str, str]:
    queue = _queue(ctx, WEFT_TID_MAPPINGS_QUEUE)
    try:
        raw_records = queue.peek_many(limit=5000)
    except Exception:
        raw_records = None
    records = cast(Sequence[str], raw_records or [])
    if not records:
        return {}

    mapping: dict[str, str] = {}
    for raw in records:
        try:
            payload = cast(dict[str, Any], json.loads(raw))
        except (TypeError, json.JSONDecodeError):
            continue
        full = payload.get("full")
        short = payload.get("short")
        if isinstance(full, str) and isinstance(short, str):
            mapping[short] = full
    return mapping


def _resolve_tid_filters(ctx: WeftContext, raw: str | None) -> set[str] | None:
    if raw is None:
        return None

    candidate = raw.strip()
    if not candidate:
        return None

    if candidate.isdigit() and len(candidate) == 19:
        return {candidate, candidate[-TASKSPEC_TID_SHORT_LENGTH:]}

    mapping = _read_tid_mappings(ctx)
    full = mapping.get(candidate)
    if full:
        return {full, candidate}

    # Fall back to treating the input as a bare identifier
    return {candidate}


def _iter_log_events(ctx: WeftContext) -> Iterable[tuple[dict[str, Any], int]]:
    queue = _queue(ctx, WEFT_GLOBAL_LOG_QUEUE)
    try:
        iterator_raw = queue.peek_generator(with_timestamps=True)
    except Exception:
        return []

    def _generator() -> Iterable[tuple[dict[str, Any], int]]:
        for entry_raw in cast(Iterable[Any], iterator_raw):
            if isinstance(entry_raw, tuple):
                if len(entry_raw) != 2:
                    continue
                body_candidate, timestamp = entry_raw
                if not isinstance(body_candidate, str):
                    continue
                body_str = body_candidate
            elif isinstance(entry_raw, str):
                body_str = entry_raw
                timestamp = 0
            else:
                continue
            try:
                payload = cast(dict[str, Any], json.loads(body_str))
            except (TypeError, json.JSONDecodeError):
                continue
            yield payload, int(timestamp)

    return _generator()


def _format_timestamp(ts: int | None) -> str:
    if not ts:
        return "-"
    dt = datetime.fromtimestamp(ts / 1_000_000_000, tz=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 1:
        return f"{seconds:.3f}s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(seconds, 60.0)
    if minutes < 60:
        return f"{int(minutes)}m{secs:04.1f}s"
    hours, minutes = divmod(minutes, 60.0)
    return f"{int(hours)}h{int(minutes):02}m"


def _collect_task_snapshots(
    ctx: WeftContext,
    *,
    include_terminal: bool,
    tid_filters: set[str] | None,
) -> list[TaskSnapshot]:
    now_ns = time.time_ns()
    snapshots: dict[str, TaskSnapshot] = {}

    for payload, timestamp in _iter_log_events(ctx):
        tid = payload.get("tid")
        if not isinstance(tid, str):
            continue

        if (
            tid_filters is not None
            and tid not in tid_filters
            and tid[-TASKSPEC_TID_SHORT_LENGTH:] not in tid_filters
        ):
            continue

        taskspec = payload.get("taskspec")
        if not isinstance(taskspec, dict):
            continue

        name = taskspec.get("name") or payload.get("name") or tid
        state = taskspec.get("state") or {}
        status = payload.get("status") or state.get("status") or "created"
        event = payload.get("event", "unknown")
        started_at = state.get("started_at")
        completed_at = state.get("completed_at")
        metadata = taskspec.get("metadata") or {}

        if isinstance(started_at, int) and not isinstance(completed_at, int):
            duration = max(0.0, (now_ns - started_at) / 1_000_000_000)
        elif isinstance(started_at, int) and isinstance(completed_at, int):
            duration = max(0.0, (completed_at - started_at) / 1_000_000_000)
        else:
            duration = None

        snapshot = TaskSnapshot(
            tid=tid,
            tid_short=tid[-TASKSPEC_TID_SHORT_LENGTH:],
            name=str(name),
            status=str(status),
            event=str(event),
            started_at=started_at if isinstance(started_at, int) else None,
            completed_at=completed_at if isinstance(completed_at, int) else None,
            last_timestamp=timestamp,
            duration_seconds=duration,
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        snapshots[tid] = snapshot

    result = list(snapshots.values())
    if not include_terminal:
        result = [snap for snap in result if snap.status not in TERMINAL_STATUSES]
    result.sort(key=lambda snap: (snap.status not in {"running", "spawning"}, snap.tid))
    return result


def _format_task_summary(snapshots: Sequence[TaskSnapshot]) -> str:
    if not snapshots:
        return "Tasks: none"

    headers = ("TID", "STATUS", "NAME", "STARTED", "DURATION", "EVENT")
    lines = ["Tasks:", "  {:<19} {:<10} {:<20} {:<20} {:<10} {}".format(*headers)]
    for snap in snapshots:
        lines.append(
            f"  {snap.tid:<19} {snap.status:<10} {snap.name[:20]:<20} {_format_timestamp(snap.started_at):<20} {_format_duration(snap.duration_seconds):<10} {snap.event}"
        )
    return "\n".join(lines)


def _render_json_payload(
    broker: BrokerStatusSnapshot,
    managers: list[dict[str, Any]],
    tasks: Sequence[TaskSnapshot],
) -> str:
    payload = {
        "broker": broker.to_dict(),
        "managers": managers,
        "tasks": [snap.to_dict() for snap in tasks],
    }
    return json.dumps(payload, ensure_ascii=False)


def _watch_task_events(
    ctx: WeftContext,
    *,
    tid_filters: set[str] | None,
    json_output: bool,
    interval: float,
) -> int:
    last_timestamp = 0
    try:
        while True:
            emitted = False
            for payload, timestamp in _iter_log_events(ctx):
                if timestamp <= last_timestamp:
                    continue
                tid = payload.get("tid")
                if not isinstance(tid, str):
                    continue
                short_tid = tid[-TASKSPEC_TID_SHORT_LENGTH:]
                if (
                    tid_filters is not None
                    and tid not in tid_filters
                    and short_tid not in tid_filters
                ):
                    continue

                taskspec = payload.get("taskspec") or {}
                name = taskspec.get("name") or payload.get("name") or tid
                status = payload.get("status") or taskspec.get("state", {}).get(
                    "status"
                )
                event = payload.get("event") or "event"
                record = {
                    "timestamp": timestamp,
                    "tid": tid,
                    "tid_short": short_tid,
                    "status": status,
                    "event": event,
                    "name": name,
                }
                if json_output:
                    print(json.dumps(record, ensure_ascii=False))
                else:
                    ts_text = _format_timestamp(timestamp)
                    print(
                        f"{ts_text} {tid:<19} {status or 'unknown':<10} {event:<16} {name}",
                        flush=True,
                    )
                emitted = True
                last_timestamp = max(last_timestamp, timestamp)

            if json_output and emitted:
                sys.stdout.flush()
            time.sleep(max(0.1, interval))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        print(f"weft: status watch failed: {exc}", file=sys.stderr)
        return 1


def cmd_status(
    *,
    tid: str | None = None,
    include_terminal: bool = False,
    json_output: bool = False,
    watch: bool = False,
    watch_interval: float = 1.0,
    spec_context: str | os.PathLike[str] | None = None,
) -> tuple[int, str | None]:
    try:
        context = _resolve_context(spec_context)
        tid_filters = _resolve_tid_filters(context, tid)
        broker_snapshot = collect_broker_status(context)
        managers = _collect_manager_records(context, include_stopped=include_terminal)
    except Exception as exc:  # pragma: no cover - defensive guard
        return 1, f"weft: failed to retrieve status: {exc}"

    if watch:
        exit_code = _watch_task_events(
            context,
            tid_filters=tid_filters,
            json_output=json_output,
            interval=watch_interval,
        )
        return exit_code, None

    tasks = _collect_task_snapshots(
        context,
        include_terminal=include_terminal,
        tid_filters=tid_filters,
    )

    if tid and not tasks:
        return 2, f"weft: task {tid} not found"

    if json_output:
        payload = _render_json_payload(broker_snapshot, managers, tasks)
    else:
        payload = "\n".join(
            (
                broker_snapshot.to_text(),
                _format_manager_summary(managers),
                _format_task_summary(tasks),
            )
        )

    return 0, payload


__all__ = [
    "BrokerStatusSnapshot",
    "TaskSnapshot",
    "collect_broker_status",
    "collect_status",
    "cmd_status",
]
