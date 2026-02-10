"""Task listing and control helpers."""

from __future__ import annotations

import json
import os
import signal
from fnmatch import fnmatchcase
from typing import Any, Iterable

from simplebroker import Queue

from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    TASKSPEC_TID_SHORT_LENGTH,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext, build_context

from . import status as status_cmd
from .result import _load_taskspec_payload


def _resolve_context(context_path: str | os.PathLike[str] | None) -> WeftContext:
    return build_context(spec_context=context_path)


def _read_tid_mapping_entries(ctx: WeftContext) -> list[dict[str, Any]]:
    queue = Queue(
        WEFT_TID_MAPPINGS_QUEUE,
        db_path=str(ctx.database_path),
        persistent=False,
        config=ctx.broker_config,
    )
    try:
        records = queue.peek_many(limit=2048) or []
    except Exception:
        return []

    entries: list[dict[str, Any]] = []
    for raw in records:
        body = raw[0] if isinstance(raw, tuple) else raw
        try:
            payload = json.loads(body)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def mapping_for_tid(ctx: WeftContext, tid: str) -> dict[str, Any] | None:
    full = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    if not full:
        return None
    for entry in _read_tid_mapping_entries(ctx):
        if entry.get("full") == full:
            return entry
    return None


def resolve_full_tid(ctx: WeftContext, raw: str) -> str | None:
    candidate = raw.strip().lstrip("T")
    if not candidate:
        return None
    if candidate.isdigit() and len(candidate) == 19:
        return candidate
    mappings = _read_tid_mapping_entries(ctx)
    for entry in mappings:
        if entry.get("short") == candidate:
            full = entry.get("full")
            if isinstance(full, str):
                return full
    return None


def task_tid(
    *,
    tid: str | None = None,
    pid: int | None = None,
    reverse: str | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> str | None:
    ctx = _resolve_context(context_path)
    if reverse:
        value = reverse.strip().lstrip("T")
        if value.isdigit() and len(value) == 19:
            return value[-TASKSPEC_TID_SHORT_LENGTH:]
        return None
    if pid is not None:
        entries = _read_tid_mapping_entries(ctx)
        for entry in entries:
            entry_pid = entry.get("pid") or entry.get("task_pid")
            if entry_pid == pid:
                full = entry.get("full")
                return full if isinstance(full, str) else None
            managed = entry.get("managed_pids") or []
            if isinstance(managed, list) and pid in managed:
                full = entry.get("full")
                return full if isinstance(full, str) else None
        return None
    if tid:
        return resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    return None


def list_tasks(
    *,
    status_filter: str | None = None,
    include_terminal: bool = False,
    context_path: str | os.PathLike[str] | None = None,
) -> list[status_cmd.TaskSnapshot]:
    ctx = _resolve_context(context_path)
    snapshots = status_cmd._collect_task_snapshots(
        ctx, include_terminal=include_terminal, tid_filters=None
    )
    if status_filter:
        snapshots = [s for s in snapshots if s.status == status_filter]
    return snapshots


def task_status(
    tid: str,
    *,
    include_terminal: bool = True,
    context_path: str | os.PathLike[str] | None = None,
) -> status_cmd.TaskSnapshot | None:
    ctx = _resolve_context(context_path)
    full_tid = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    snapshots = status_cmd._collect_task_snapshots(
        ctx,
        include_terminal=include_terminal,
        tid_filters={full_tid, full_tid[-TASKSPEC_TID_SHORT_LENGTH:]},
    )
    if not snapshots:
        return None
    return snapshots[0]


def _ctrl_in_for_tid(ctx: WeftContext, tid: str) -> str:
    taskspec = _load_taskspec_payload(ctx, tid)
    if taskspec:
        io_section = taskspec.get("io") or {}
        control = io_section.get("control") or {}
        ctrl_in = control.get("ctrl_in")
        if isinstance(ctrl_in, str) and ctrl_in:
            return ctrl_in
    return f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"


def _send_control(ctx: WeftContext, tid: str, command: str) -> None:
    ctrl_in = _ctrl_in_for_tid(ctx, tid)
    queue = Queue(
        ctrl_in,
        db_path=str(ctx.database_path),
        persistent=False,
        config=ctx.broker_config,
    )
    queue.write(command)


def stop_tasks(
    tids: Iterable[str],
    *,
    context_path: str | os.PathLike[str] | None = None,
) -> int:
    ctx = _resolve_context(context_path)
    count = 0
    for tid in tids:
        full = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
        if not full:
            continue
        _send_control(ctx, full, "STOP")
        count += 1
    return count


def kill_tasks(
    tids: Iterable[str],
    *,
    context_path: str | os.PathLike[str] | None = None,
) -> int:
    ctx = _resolve_context(context_path)
    entries = _read_tid_mapping_entries(ctx)
    lookup: dict[str, dict[str, Any]] = {
        entry.get("full"): entry for entry in entries if isinstance(entry.get("full"), str)
    }
    killed = 0
    for tid in tids:
        full = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
        if not full:
            continue
        entry = lookup.get(full)
        if not entry:
            continue
        pids: list[int] = []
        pid = entry.get("pid") or entry.get("task_pid")
        if isinstance(pid, int):
            pids.append(pid)
        managed = entry.get("managed_pids")
        if isinstance(managed, list):
            pids.extend(pid for pid in managed if isinstance(pid, int))
        for pid in set(pids):
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except OSError:
                continue
    return killed


def filter_tids_by_pattern(
    snapshots: Iterable[status_cmd.TaskSnapshot], pattern: str
) -> list[str]:
    if not pattern:
        return [snap.tid for snap in snapshots]
    return [snap.tid for snap in snapshots if fnmatchcase(snap.name, pattern)]
