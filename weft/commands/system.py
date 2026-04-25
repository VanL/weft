"""Status reporting helpers for the Weft CLI.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.2.1]
- docs/specifications/01-Core_Components.md [CC-3.2], [CC-3.4]
- docs/specifications/02-TaskSpec.md [TS-1.3]
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from simplebroker import Queue
from weft._constants import (
    NON_LIVE_RUNTIME_STATES,
    STATUS_WATCH_MIN_INTERVAL,
    TASKSPEC_TID_SHORT_LENGTH,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft._runner_plugins import require_runner_plugin
from weft.builtins import builtin_task_catalog
from weft.commands.manager import _list_manager_records
from weft.commands.types import (
    ManagerSnapshot,
    SystemLoadResult,
    SystemStatusSnapshot,
    SystemTidyResult,
)
from weft.commands.types import (
    TaskSnapshot as PublicTaskSnapshot,
)
from weft.context import WeftContext, build_context
from weft.core.queue_wait import QueueChangeMonitor
from weft.ext import RunnerHandle
from weft.helpers import (
    format_byte_size,
    format_timestamp_ns_relative,
    iter_queue_json_entries,
    pid_is_live,
)

from ._dump_support import cmd_dump
from ._load_support import cmd_load
from ._tidy_support import cmd_tidy

StatusMapping = Mapping[str, int | float | str | None]


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
    activity: str | None
    waiting_on: str | None
    started_at: int | None
    completed_at: int | None
    last_timestamp: int
    duration_seconds: float | None
    runner: str | None
    runtime_handle: dict[str, Any] | None
    runtime: dict[str, Any] | None
    metadata: dict[str, Any]
    pipeline_status: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "tid": self.tid,
            "tid_short": self.tid_short,
            "name": self.name,
            "status": self.status,
            "event": self.event,
            "activity": self.activity,
            "waiting_on": self.waiting_on,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "last_timestamp": self.last_timestamp,
            "duration_seconds": self.duration_seconds,
            "runner": self.runner,
            "runtime_handle": self.runtime_handle,
            "runtime": self.runtime,
            "metadata": self.metadata,
        }
        if self.pipeline_status is not None:
            payload["pipeline_status"] = self.pipeline_status
        return payload


def _resolve_context(
    spec_context: str | os.PathLike[str] | None = None,
) -> WeftContext:
    if spec_context:
        return build_context(spec_context=spec_context)

    env_context = os.environ.get("WEFT_CONTEXT")
    if env_context:
        return build_context(spec_context=env_context)

    return build_context()


def collect_broker_status(ctx: WeftContext) -> BrokerStatusSnapshot:
    with ctx.broker() as db:
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
    return ctx.queue(name, persistent=persistent)


def _collect_manager_records(
    ctx: WeftContext, *, include_stopped: bool = False
) -> list[dict[str, Any]]:
    return _list_manager_records(
        ctx,
        include_stopped=include_stopped,
        canonical_only=False,
    )


def _format_manager_summary(records: list[dict[str, Any]]) -> str:
    if not records:
        return "Managers: none registered"

    lines = ["Managers:"]
    for record in records:
        tid = record.get("tid", "?")
        status = record.get("status", "unknown")
        role = record.get("role", "manager")
        runtime_handle = record.get("runtime_handle")
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
                f"    runtime: {json.dumps(runtime_handle, sort_keys=True) if isinstance(runtime_handle, dict) else 'n/a'}",
                f"    requests: {requests}",
                f"    outbox: {outbox}",
                f"    {ts_line}",
            ]
        )

    return "\n".join(lines)


def _read_tid_mappings(ctx: WeftContext) -> dict[str, str]:
    queue = _queue(ctx, WEFT_TID_MAPPINGS_QUEUE)
    try:
        mapping: dict[str, str] = {}
        for payload, _timestamp in iter_queue_json_entries(queue):
            full = payload.get("full")
            short = payload.get("short")
            if isinstance(full, str) and isinstance(short, str):
                mapping[short] = full
        return mapping
    finally:
        queue.close()


def _latest_tid_mapping_entries(ctx: WeftContext) -> dict[str, dict[str, Any]]:
    queue = _queue(ctx, WEFT_TID_MAPPINGS_QUEUE)
    try:
        latest: dict[str, tuple[int, dict[str, Any]]] = {}
        for payload, timestamp in iter_queue_json_entries(queue):
            full = payload.get("full")
            if not isinstance(full, str):
                continue
            previous = latest.get(full)
            if previous is None or previous[0] <= timestamp:
                latest[full] = (timestamp, payload)
        return {full: payload for full, (_timestamp, payload) in latest.items()}
    finally:
        queue.close()


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


def _iter_log_events(
    queue: Queue,
    *,
    since_timestamp: int | None = None,
) -> Iterable[tuple[dict[str, Any], int]]:
    """Replay all state-change events from the global log queue.

    Spec: [MF-5]
    """
    try:
        iterator_raw = queue.peek_generator(
            with_timestamps=True,
            since_timestamp=since_timestamp,
        )
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


def _runtime_handle_from_mapping(entry: Mapping[str, Any]) -> RunnerHandle | None:
    payload = entry.get("runtime_handle")
    if not isinstance(payload, Mapping):
        return None
    try:
        return RunnerHandle.from_dict(payload)
    except ValueError:
        return None


def _merge_runtime_entry(
    mapping_entry: Mapping[str, Any] | None,
    event_payload: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Combine runtime metadata from the mapping queue and the log payload."""

    merged: dict[str, Any] = {}
    if isinstance(event_payload, Mapping):
        merged.update(event_payload)
    if mapping_entry is not None:
        merged.update(mapping_entry)
    return merged or None


def _pid_alive(pid: int | None) -> bool:
    return pid_is_live(pid)


def _task_process_alive(mapping_entry: Mapping[str, Any] | None) -> bool:
    handle = _runtime_handle_from_mapping(mapping_entry or {})
    if handle is None or handle.control.get("authority") != "host-pid":
        return False
    return any(_pid_alive(pid) for pid in handle.scoped_host_pids())


def _task_process_id(mapping_entry: Mapping[str, Any] | None) -> int | None:
    handle = _runtime_handle_from_mapping(mapping_entry or {})
    if handle is None or handle.control.get("authority") != "host-pid":
        return None
    host_pids = handle.scoped_host_pids()
    return host_pids[0] if host_pids else None


def _runtime_description_is_live(
    runtime_description: Mapping[str, Any] | None,
) -> bool:
    if runtime_description is None:
        return False
    state = runtime_description.get("state")
    if not isinstance(state, str):
        return False
    normalized = state.strip().lower()
    if not normalized:
        return False
    return normalized not in NON_LIVE_RUNTIME_STATES


def _effective_public_status(
    status: str,
    *,
    runner_name: str | None,
    mapping_entry: Mapping[str, Any] | None,
    runtime_description: Mapping[str, Any] | None,
) -> str:
    """Keep public terminal states aligned with live runtime liveness."""

    normalized_runner = (
        runner_name.strip().lower() if isinstance(runner_name, str) else ""
    )
    runtime_live = _runtime_description_is_live(runtime_description)
    host_task_pid = _task_process_id(mapping_entry)

    if status not in TERMINAL_TASK_STATUSES:
        if (
            status in {"spawning", "running"}
            and (not normalized_runner or normalized_runner == "host")
            and host_task_pid is not None
            and not _pid_alive(host_task_pid)
        ):
            return "failed"
        return status

    if normalized_runner and normalized_runner != "host":
        if runtime_live:
            return "running"
        return status

    if _task_process_alive(mapping_entry):
        return "running"
    if runtime_live:
        return "running"
    return status


def _runner_name_for_snapshot(
    *,
    taskspec: Mapping[str, Any],
    mapping_entry: Mapping[str, Any] | None,
) -> str | None:
    if mapping_entry is not None:
        mapped_runner = mapping_entry.get("runner")
        if isinstance(mapped_runner, str) and mapped_runner.strip():
            return mapped_runner
        runtime_handle = _runtime_handle_from_mapping(mapping_entry)
        if runtime_handle is not None:
            return runtime_handle.runner

    spec = taskspec.get("spec")
    if not isinstance(spec, Mapping):
        return None
    runner = spec.get("runner")
    if not isinstance(runner, Mapping):
        return "host"
    name = runner.get("name", "host")
    if isinstance(name, str) and name.strip():
        return name
    return "host"


def _describe_runtime_handle(handle: RunnerHandle | None) -> dict[str, Any] | None:
    if handle is None:
        return None
    if handle.control.get("authority") == "external-supervisor":
        return {
            "runner": handle.runner,
            "id": handle.id,
            "state": "unknown",
            "metadata": {
                **dict(handle.observations),
                **dict(handle.metadata),
            },
        }
    try:
        plugin = require_runner_plugin(handle.runner)
        runtime = plugin.describe(handle)
    except Exception as exc:  # pragma: no cover - defensive integration guard
        return {
            "runner": handle.runner,
            "id": handle.id,
            "state": "unknown",
            "metadata": {"describe_error": str(exc)},
        }
    if runtime is None:
        return None
    return runtime.to_dict()


def _collect_task_snapshots(
    ctx: WeftContext,
    *,
    include_terminal: bool,
    tid_filters: set[str] | None,
) -> list[TaskSnapshot]:
    """Reconstruct current task state from event-sourced log replay.

    Spec: [MF-5]
    """
    now_ns = time.time_ns()
    records: dict[str, dict[str, Any]] = {}
    tid_mapping_entries = _latest_tid_mapping_entries(ctx)
    log_queue = _queue(ctx, WEFT_GLOBAL_LOG_QUEUE)
    try:
        for payload, timestamp in _iter_log_events(log_queue):
            tid = payload.get("tid")
            if not isinstance(tid, str):
                continue

            if (
                tid_filters is not None
                and tid not in tid_filters
                and tid[-TASKSPEC_TID_SHORT_LENGTH:] not in tid_filters
            ):
                continue

            record = records.setdefault(
                tid,
                {
                    "tid": tid,
                    "tid_short": tid[-TASKSPEC_TID_SHORT_LENGTH:],
                    "name": tid,
                    "status": "created",
                    "event": "unknown",
                    "activity": None,
                    "waiting_on": None,
                    "started_at": None,
                    "completed_at": None,
                    "last_timestamp": timestamp,
                    "taskspec": None,
                    "metadata": {},
                    "event_payload": None,
                },
            )
            event = payload.get("event", "unknown")
            current_status = record.get("status")
            current_terminal = (
                isinstance(current_status, str)
                and current_status in TERMINAL_TASK_STATUSES
            )

            if event == "task_activity":
                if current_terminal:
                    continue
                record["last_timestamp"] = timestamp
                if isinstance(event, str):
                    record["event"] = event
                status = payload.get("status")
                if isinstance(status, str) and status:
                    record["status"] = status
                if record["status"] in TERMINAL_TASK_STATUSES:
                    record["activity"] = None
                    record["waiting_on"] = None
                else:
                    activity = payload.get("activity")
                    waiting_on = payload.get("waiting_on")
                    record["activity"] = (
                        activity.strip()
                        if isinstance(activity, str) and activity.strip()
                        else None
                    )
                    record["waiting_on"] = (
                        waiting_on.strip()
                        if isinstance(waiting_on, str) and waiting_on.strip()
                        else None
                    )
                continue

            taskspec = payload.get("taskspec")
            if not isinstance(taskspec, dict):
                continue

            name = taskspec.get("name") or payload.get("name") or tid
            state = taskspec.get("state") or {}
            status = payload.get("status") or state.get("status") or "created"
            incoming_terminal = (
                isinstance(status, str) and status in TERMINAL_TASK_STATUSES
            )
            if current_terminal and not incoming_terminal:
                continue
            record["last_timestamp"] = timestamp
            if isinstance(event, str):
                record["event"] = event
            started_at = state.get("started_at")
            completed_at = state.get("completed_at")
            metadata = taskspec.get("metadata") or {}
            record["name"] = str(name)
            record["status"] = str(status)
            record["started_at"] = started_at if isinstance(started_at, int) else None
            record["completed_at"] = (
                completed_at if isinstance(completed_at, int) else None
            )
            record["taskspec"] = taskspec
            record["metadata"] = metadata if isinstance(metadata, dict) else {}
            record["event_payload"] = dict(payload)
            if record["status"] in TERMINAL_TASK_STATUSES:
                record["activity"] = None
                record["waiting_on"] = None
            else:
                activity = payload.get("activity")
                waiting_on = payload.get("waiting_on")
                if isinstance(activity, str) and activity.strip():
                    record["activity"] = activity.strip()
                if isinstance(waiting_on, str) and waiting_on.strip():
                    record["waiting_on"] = waiting_on.strip()
    finally:
        log_queue.close()

    snapshots: list[TaskSnapshot] = []
    for tid, record in records.items():
        taskspec = record.get("taskspec")
        if not isinstance(taskspec, dict):
            continue
        mapping_entry = tid_mapping_entries.get(tid)
        runtime_entry = _merge_runtime_entry(
            mapping_entry,
            record.get("event_payload")
            if isinstance(record.get("event_payload"), Mapping)
            else None,
        )
        runtime_handle = _runtime_handle_from_mapping(runtime_entry or {})
        runner = _runner_name_for_snapshot(
            taskspec=taskspec,
            mapping_entry=runtime_entry,
        )
        runtime_description = _describe_runtime_handle(runtime_handle)
        public_status = _effective_public_status(
            str(record.get("status") or "created"),
            runner_name=runner,
            mapping_entry=runtime_entry,
            runtime_description=runtime_description,
        )

        started_at = record.get("started_at")
        completed_at = record.get("completed_at")
        if isinstance(started_at, int) and not isinstance(completed_at, int):
            duration = max(0.0, (now_ns - started_at) / 1_000_000_000)
        elif isinstance(started_at, int) and isinstance(completed_at, int):
            duration = max(0.0, (completed_at - started_at) / 1_000_000_000)
        else:
            duration = None

        activity = record.get("activity")
        waiting_on = record.get("waiting_on")
        if public_status in TERMINAL_TASK_STATUSES:
            activity = None
            waiting_on = None

        snapshots.append(
            TaskSnapshot(
                tid=tid,
                tid_short=record["tid_short"],
                name=str(record.get("name") or tid),
                status=public_status,
                event=str(record.get("event") or "unknown"),
                activity=activity if isinstance(activity, str) else None,
                waiting_on=waiting_on if isinstance(waiting_on, str) else None,
                started_at=started_at if isinstance(started_at, int) else None,
                completed_at=completed_at if isinstance(completed_at, int) else None,
                last_timestamp=int(record.get("last_timestamp") or 0),
                duration_seconds=duration,
                runner=runner,
                runtime_handle=runtime_handle.to_dict()
                if runtime_handle is not None
                else None,
                runtime=runtime_description,
                metadata=record["metadata"]
                if isinstance(record.get("metadata"), dict)
                else {},
            )
        )

    result = snapshots
    if not include_terminal:
        result = [snap for snap in result if snap.status not in TERMINAL_TASK_STATUSES]
    result.sort(key=lambda snap: (snap.status not in {"running", "spawning"}, snap.tid))
    return result


def _format_task_summary(snapshots: Sequence[TaskSnapshot]) -> str:
    if not snapshots:
        return "Tasks: none"

    headers = (
        "TID",
        "STATUS",
        "ACTIVITY",
        "RUNNER",
        "NAME",
        "STARTED",
        "DURATION",
        "EVENT",
    )
    lines = [
        "Tasks:",
        "  {:<19} {:<10} {:<12} {:<14} {:<20} {:<20} {:<10} {}".format(*headers),
    ]
    for snap in snapshots:
        lines.append(
            f"  {snap.tid:<19} {snap.status:<10} {(snap.activity or '-'): <12} {(snap.runner or '-'):<14} {snap.name[:20]:<20} {_format_timestamp(snap.started_at):<20} {_format_duration(snap.duration_seconds):<10} {snap.event}"
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
    status_filter: str | None,
    json_output: bool,
    interval: float,
) -> int:
    """Tail the global log queue for live state-change events.

    Spec: [MF-5]
    """
    last_timestamp = 0
    queue = _queue(ctx, WEFT_GLOBAL_LOG_QUEUE)
    monitor: QueueChangeMonitor | None = None
    try:
        monitor = QueueChangeMonitor([queue], config=ctx.config)
        while True:
            emitted = False
            for payload, timestamp in _iter_log_events(
                queue,
                since_timestamp=last_timestamp,
            ):
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
                if status_filter and status != status_filter:
                    continue
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
            monitor.wait(max(STATUS_WATCH_MIN_INTERVAL, interval))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        print(f"weft: status watch failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if monitor is not None:
            monitor.close()
        queue.close()


def cmd_status(
    *,
    tid: str | None = None,
    include_terminal: bool = False,
    status_filter: str | None = None,
    json_output: bool = False,
    watch: bool = False,
    watch_interval: float = 1.0,
    spec_context: str | os.PathLike[str] | None = None,
) -> tuple[int, str | None]:
    """Broker status snapshot with optional task filtering.

    Spec: [CLI-1.2.1]
    """
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
            status_filter=status_filter,
            json_output=json_output,
            interval=watch_interval,
        )
        return exit_code, None

    tasks = _collect_task_snapshots(
        context,
        include_terminal=include_terminal,
        tid_filters=tid_filters,
    )
    if status_filter:
        tasks = [snap for snap in tasks if snap.status == status_filter]

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


def _manager_snapshot(record: dict[str, Any]) -> ManagerSnapshot:
    return ManagerSnapshot(
        tid=str(record.get("tid", "")),
        status=str(record.get("status", "unknown")),
        name=str(record.get("name", "")),
        runtime_handle=(
            dict(record["runtime_handle"])
            if isinstance(record.get("runtime_handle"), dict)
            else None
        ),
        timestamp=(
            int(record["timestamp"])
            if isinstance(record.get("timestamp"), int | float | str)
            and str(record.get("timestamp")).isdigit()
            else None
        ),
        role=record.get("role") if isinstance(record.get("role"), str) else None,
        requests=(
            record.get("requests") if isinstance(record.get("requests"), str) else None
        ),
        outbox=record.get("outbox") if isinstance(record.get("outbox"), str) else None,
        ctrl_in=record.get("ctrl_in")
        if isinstance(record.get("ctrl_in"), str)
        else None,
        ctrl_out=(
            record.get("ctrl_out") if isinstance(record.get("ctrl_out"), str) else None
        ),
    )


def _public_task_snapshot(snapshot: TaskSnapshot) -> PublicTaskSnapshot:
    payload = snapshot.to_dict()
    return PublicTaskSnapshot(
        tid=str(payload["tid"]),
        tid_short=str(payload["tid_short"]),
        name=str(payload["name"]),
        status=str(payload["status"]),
        event=payload["event"] if isinstance(payload.get("event"), str) else None,
        activity=(
            payload["activity"] if isinstance(payload.get("activity"), str) else None
        ),
        waiting_on=(
            payload["waiting_on"]
            if isinstance(payload.get("waiting_on"), str)
            else None
        ),
        return_code=None,
        started_at=payload["started_at"]
        if isinstance(payload.get("started_at"), int)
        else None,
        completed_at=(
            payload["completed_at"]
            if isinstance(payload.get("completed_at"), int)
            else None
        ),
        error=None,
        last_timestamp=(
            payload["last_timestamp"]
            if isinstance(payload.get("last_timestamp"), int)
            else None
        ),
        duration_seconds=(
            float(payload["duration_seconds"])
            if isinstance(payload.get("duration_seconds"), int | float)
            else None
        ),
        runner=payload["runner"] if isinstance(payload.get("runner"), str) else None,
        runtime_handle=(
            dict(payload["runtime_handle"])
            if isinstance(payload.get("runtime_handle"), dict)
            else None
        ),
        runtime=(
            dict(payload["runtime"])
            if isinstance(payload.get("runtime"), dict)
            else None
        ),
        metadata=(
            dict(payload["metadata"])
            if isinstance(payload.get("metadata"), dict)
            else {}
        ),
        pipeline_status=(
            dict(payload["pipeline_status"])
            if isinstance(payload.get("pipeline_status"), dict)
            else None
        ),
    )


def system_status(context: WeftContext) -> SystemStatusSnapshot:
    """Return the top-level broker, manager, and task status view."""

    return SystemStatusSnapshot(
        broker=collect_broker_status(context).to_dict(),
        managers=[
            _manager_snapshot(record) for record in _collect_manager_records(context)
        ],
        tasks=[
            _public_task_snapshot(snapshot)
            for snapshot in _collect_task_snapshots(
                context,
                include_terminal=True,
                tid_filters=None,
            )
        ],
    )


def tidy_system(context: WeftContext) -> SystemTidyResult:
    """Run broker compaction and return the broker display target."""

    exit_code, message = cmd_tidy(context.root)
    if exit_code != 0:
        raise RuntimeError(message or "weft tidy failed")
    return SystemTidyResult(target=context.broker_display_target)


def dump_system(
    context: WeftContext,
    *,
    output: str | Path | None = None,
) -> Path:
    """Dump broker state and return the output path."""

    output_path = (
        context.weft_dir / "weft_export.jsonl"
        if output is None
        else Path(output)
        if Path(output).is_absolute()
        else Path.cwd() / Path(output)
    )
    exit_code, message = cmd_dump(
        output=str(output_path), context_path=str(context.root)
    )
    if exit_code != 0:
        raise RuntimeError(message or "weft dump failed")
    return output_path


def load_system(
    context: WeftContext,
    *,
    input_file: str | Path | None = None,
    dry_run: bool = False,
) -> SystemLoadResult:
    """Load broker state from a dump file."""

    exit_code, message = cmd_load(
        input_file=str(input_file) if input_file is not None else None,
        dry_run=dry_run,
        context_path=str(context.root),
    )
    if exit_code != 0:
        raise RuntimeError(message or "weft load failed")
    return SystemLoadResult(imported=not dry_run, message=message or "")


def list_builtins() -> list[dict[str, Any]]:
    """Return the builtin task inventory as serialized rows."""

    return [
        {
            "type": "task",
            "name": item.name,
            "description": item.description,
            "category": item.category,
            "function_target": item.function_target,
            "supported_platforms": (
                list(item.supported_platforms)
                if item.supported_platforms is not None
                else None
            ),
            "path": str(item.path),
            "source": item.source,
        }
        for item in builtin_task_catalog()
    ]


__all__ = [
    "BrokerStatusSnapshot",
    "TaskSnapshot",
    "collect_broker_status",
    "collect_status",
    "cmd_status",
    "dump_system",
    "list_builtins",
    "load_system",
    "system_status",
    "tidy_system",
]
