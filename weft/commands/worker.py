"""Worker management commands for the Weft CLI."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from typing import Any, cast

import psutil

from simplebroker import Queue
from weft._constants import (
    WEFT_TID_MAPPINGS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.context import build_context
from weft.core import Manager, launch_task_process
from weft.core.taskspec import TaskSpec


def _load_taskspec(path: Path) -> TaskSpec:
    try:
        return TaskSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - validation tested elsewhere
        raise ValueError(f"Failed to load TaskSpec: {exc}") from exc


def _registry_snapshot(
    db_path: str, config: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    queue = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=db_path,
        persistent=False,
        config=config,
    )
    try:
        records_raw = queue.peek_many(limit=1000, with_timestamps=True)
    except Exception:  # pragma: no cover - queue errors
        return {}

    records = cast(list[tuple[str, int]], records_raw)

    snapshot: dict[str, dict[str, Any]] = {}
    for entry, timestamp in records:
        try:
            data = json.loads(entry)
        except json.JSONDecodeError:
            continue
        data["_timestamp"] = timestamp
        tid = data.get("tid")
        if tid:
            snapshot[tid] = data
    return snapshot


def _registry_entry_for_tid(
    db_path: str, config: dict[str, Any], tid: str
) -> dict[str, Any] | None:
    queue = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=db_path,
        persistent=False,
        config=config,
    )
    try:
        generator = queue.peek_generator(with_timestamps=True)
    except Exception:  # pragma: no cover - queue errors
        queue.close()
        return None

    latest: tuple[dict[str, Any], int] | None = None
    try:
        for entry in generator:
            if isinstance(entry, tuple) and len(entry) == 2:
                body, timestamp = entry
            else:
                body, timestamp = entry, 0
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue
            if data.get("tid") != tid:
                continue
            if latest is None or timestamp >= latest[1]:
                latest = (data, timestamp)
    finally:
        queue.close()

    return None if latest is None else latest[0]


def start_command(
    taskspec_path: Path,
    *,
    foreground: bool,
) -> tuple[int, str | None]:
    spec = _load_taskspec(taskspec_path)
    context = build_context(spec.spec.weft_context)
    db_path = str(context.database_path)

    if foreground:
        worker = Manager(db_path, spec, config=context.config)
        try:
            worker.run_until_stopped()
        except KeyboardInterrupt:  # pragma: no cover - interactive guard
            worker.should_stop = True
        finally:
            worker.cleanup()
        return 0, None

    process = launch_task_process(Manager, db_path, spec, config=context.config)
    message = f"Started worker {spec.tid} (pid {process.pid})"
    return 0, message


def _send_stop(
    db_path: str,
    config: dict[str, Any],
    tid: str,
) -> None:
    ctrl_queue = Queue(
        f"T{tid}.ctrl_in",
        db_path=db_path,
        persistent=False,
        config=config,
    )
    ctrl_queue.write("STOP")


def _lookup_pid(db_path: str, config: dict[str, Any], tid: str) -> int | None:
    queue = Queue(
        WEFT_TID_MAPPINGS_QUEUE,
        db_path=db_path,
        persistent=False,
        config=config,
    )
    try:
        mappings_raw = queue.peek_many(limit=1000)
    except Exception:  # pragma: no cover
        return None

    mappings = cast(list[str], mappings_raw)

    for entry in mappings:
        try:
            data = json.loads(entry)
        except json.JSONDecodeError:
            continue
        if data.get("full") == tid and "pid" in data:
            pid_value = data["pid"]
            return cast(int, pid_value)
    return None


def _pid_alive(pid: int | None) -> bool:
    """Cross-platform liveness probe using psutil (avoids os.kill(0) on Windows)."""

    if pid is None or pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        return process.is_running()
    except psutil.Error:
        return False


def stop_command(
    *,
    tid: str,
    force: bool,
    timeout: float,
    context_path: Path | None = None,
    stop_if_absent: bool = False,
) -> tuple[int, str | None]:
    context = build_context(context_path)
    db_path = str(context.database_path)

    _send_stop(db_path, context.broker_config, tid)

    deadline = time.time() + timeout
    entry_observed = False
    last_entry: dict[str, Any] | None = None
    pid_checked_at: float = 0.0

    while time.time() < deadline:
        entry = _registry_entry_for_tid(db_path, context.broker_config, tid)
        if entry is None:
            if stop_if_absent or entry_observed:
                return 0, None
        else:
            entry_observed = True
            last_entry = entry
            status = entry.get("status")
            if status == "stopped":
                return 0, None
            if stop_if_absent:
                pid = entry.get("pid")
                now = time.time()
                if now - pid_checked_at >= 0.5:
                    pid_checked_at = now
                    if not _pid_alive(pid):
                        return 0, None
        time.sleep(0.1)

    if force:
        if last_entry is None:
            return 0, None
        pid = _lookup_pid(db_path, context.broker_config, tid)
        if pid is None or not _pid_alive(pid):
            return 0, None
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return 0, None
        except PermissionError:  # pragma: no cover - unlikely in tests
            return 1, f"Permission denied sending SIGTERM to PID {pid}"
        return 0, None

    return 1, f"Worker {tid} did not stop within {timeout:.1f}s"


def list_command(
    *,
    json_output: bool,
    context_path: Path | None = None,
) -> tuple[int, str | None]:
    context = build_context(context_path)
    snapshot = _registry_snapshot(str(context.database_path), context.broker_config)

    if json_output:
        payload = json.dumps(list(snapshot.values()), indent=2)
        return 0, payload

    if not snapshot:
        return 0, "No registered workers"

    lines = ["TID        STATUS    NAME"]
    for tid, data in sorted(snapshot.items(), key=lambda item: item[0]):
        status = data.get("status", "unknown")
        name = data.get("name", "")
        lines.append(f"{tid}  {status:<9} {name}")
    return 0, "\n".join(lines)


def status_command(
    *,
    tid: str,
    json_output: bool,
    context_path: Path | None = None,
) -> tuple[int, str | None]:
    context = build_context(context_path)
    snapshot = _registry_snapshot(str(context.database_path), context.broker_config)
    record = snapshot.get(tid)

    if not record:
        return 1, f"Worker {tid} not found"

    if json_output:
        return 0, json.dumps(record, indent=2)

    parts = [
        f"Worker {tid}",
        f"Name: {record.get('name', '')}",
        f"Status: {record.get('status', 'unknown')}",
    ]
    pid = record.get("pid")
    if pid is not None:
        parts.append(f"PID: {pid}")
    return 0, "\n".join(parts)


__all__ = [
    "start_command",
    "stop_command",
    "list_command",
    "status_command",
]
