"""Task listing and control helpers.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.2.1]
- docs/specifications/10-CLI_Interface.md [CLI-1.3]
- docs/specifications/01-Core_Components.md [CC-3.2]
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Iterable
from fnmatch import fnmatchcase
from typing import Any

from simplebroker import Queue
from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    CONTROL_SURFACE_WAIT_INTERVAL,
    CONTROL_SURFACE_WAIT_TIMEOUT,
    QUEUE_CTRL_IN_SUFFIX,
    TASKSPEC_TID_SHORT_LENGTH,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft._runner_plugins import require_runner_plugin
from weft.context import WeftContext, build_context
from weft.ext import RunnerHandle
from weft.helpers import (
    iter_queue_json_entries,
    kill_process_tree,
    terminate_process_tree,
)

from . import status as status_cmd
from .result import _load_taskspec_payload


def _resolve_context(context_path: str | os.PathLike[str] | None) -> WeftContext:
    return build_context(spec_context=context_path)


def _read_tid_mapping_entries(ctx: WeftContext) -> list[dict[str, Any]]:
    queue = Queue(
        WEFT_TID_MAPPINGS_QUEUE,
        db_path=ctx.broker_target,
        persistent=False,
        config=ctx.broker_config,
    )
    entries: list[dict[str, Any]] = []
    for payload, _timestamp in iter_queue_json_entries(queue):
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def mapping_for_tid(ctx: WeftContext, tid: str) -> dict[str, Any] | None:
    full = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    if not full:
        return None
    latest_match: dict[str, Any] | None = None
    for entry in _read_tid_mapping_entries(ctx):
        if entry.get("full") == full:
            latest_match = entry
    return latest_match


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
    """TID resolution: short-to-full, PID-to-TID, and reverse lookup.

    Spec: [CLI-1.2] (task tid)
    """
    ctx = _resolve_context(context_path)
    if reverse:
        value = reverse.strip().lstrip("T")
        if value.isdigit() and len(value) == 19:
            return value[-TASKSPEC_TID_SHORT_LENGTH:]
        return None
    if pid is not None:
        entries = list(_read_tid_mapping_entries(ctx))
        for entry in reversed(entries):
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
    """Write a control command to a task's ctrl_in queue.

    Spec: [MF-3]
    """
    ctrl_in = _ctrl_in_for_tid(ctx, tid)
    queue = Queue(
        ctrl_in,
        db_path=ctx.broker_target,
        persistent=False,
        config=ctx.broker_config,
    )
    queue.write(command)


def _runtime_handle_from_mapping(entry: dict[str, Any]) -> RunnerHandle | None:
    payload = entry.get("runtime_handle")
    if not isinstance(payload, dict):
        return None
    try:
        return RunnerHandle.from_dict(payload)
    except ValueError:
        return None


def _task_pid_from_mapping(entry: dict[str, Any]) -> int | None:
    pid = entry.get("pid") or entry.get("task_pid")
    return pid if isinstance(pid, int) else None


def _pid_exists(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _await_control_surface(
    ctx: WeftContext,
    tid: str,
    *,
    timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
) -> tuple[dict[str, Any] | None, status_cmd.TaskSnapshot | None]:
    deadline = time.monotonic() + timeout
    latest_entry: dict[str, Any] | None = None
    latest_snapshot: status_cmd.TaskSnapshot | None = None
    while True:
        mapping_entry = mapping_for_tid(ctx, tid)
        if mapping_entry is not None:
            latest_entry = mapping_entry
        snapshot = task_status(tid, context_path=ctx.root)
        if snapshot is not None:
            latest_snapshot = snapshot
            if snapshot.status in status_cmd.TERMINAL_STATUSES:
                return latest_entry, latest_snapshot
        if time.monotonic() >= deadline:
            return latest_entry, latest_snapshot
        time.sleep(CONTROL_SURFACE_WAIT_INTERVAL)


def _latest_task_entry(
    ctx: WeftContext,
    lookup: dict[str, dict[str, Any]],
    tid: str,
    current: dict[str, Any] | None,
) -> dict[str, Any] | None:
    return mapping_for_tid(ctx, tid) or current or lookup.get(tid)


def _stop_via_fallback(task_entry: dict[str, Any] | None) -> bool:
    if task_entry is None:
        return False
    handle = _runtime_handle_from_mapping(task_entry)
    if handle is not None:
        if handle.runner_name == "host":
            pid = _task_pid_from_mapping(task_entry)
            if pid is not None:
                terminate_process_tree(pid, timeout=0.2, kill_after=False)
                return False
        plugin = require_runner_plugin(handle.runner_name)
        plugin.stop(handle, timeout=0.2)
        return True

    pid = _task_pid_from_mapping(task_entry)
    if pid is not None:
        terminate_process_tree(pid, timeout=0.2, kill_after=False)
    return False


def _kill_via_fallback(task_entry: dict[str, Any] | None) -> bool:
    if task_entry is None:
        return False

    handle = _runtime_handle_from_mapping(task_entry)
    if handle is not None:
        if handle.runner_name == "host":
            pid = _task_pid_from_mapping(task_entry)
            if pid is None:
                plugin = require_runner_plugin(handle.runner_name)
                plugin.kill(handle, timeout=0.2)
                return True
        else:
            plugin = require_runner_plugin(handle.runner_name)
            plugin.kill(handle, timeout=0.2)
            return True

    pid = _task_pid_from_mapping(task_entry)
    if pid is None:
        return False

    sigusr1 = getattr(signal, "SIGUSR1", None)
    if sigusr1 is not None:
        try:
            os.kill(pid, sigusr1)
        except OSError:
            pass
    return False


def _stop_terminal_task_process(task_entry: dict[str, Any] | None) -> None:
    if task_entry is None:
        return

    pid = _task_pid_from_mapping(task_entry)
    if pid is not None and _pid_exists(pid):
        terminate_process_tree(pid, timeout=0.2)


def _kill_terminal_task_process(task_entry: dict[str, Any] | None) -> None:
    if task_entry is None:
        return

    pid = _task_pid_from_mapping(task_entry)
    if pid is not None and _pid_exists(pid):
        kill_process_tree(pid, timeout=0.2)


def _force_kill_task_processes(task_entry: dict[str, Any] | None) -> bool:
    if task_entry is None:
        return False

    pids: list[int] = []
    pid = _task_pid_from_mapping(task_entry)
    if isinstance(pid, int):
        pids.append(pid)
    managed = task_entry.get("managed_pids")
    if isinstance(managed, list):
        pids.extend(item for item in managed if isinstance(item, int))

    task_killed = False
    for pid_value in set(pids):
        if kill_process_tree(pid_value, timeout=0.2):
            task_killed = True
    return task_killed


def stop_tasks(
    tids: Iterable[str],
    *,
    context_path: str | os.PathLike[str] | None = None,
) -> int:
    """Gracefully stop one or more tasks by sending STOP control messages.

    Spec: [CLI-1.2] (task stop)
    """
    ctx = _resolve_context(context_path)
    entries = _read_tid_mapping_entries(ctx)
    lookup: dict[str, dict[str, Any]] = {}
    for mapping_entry in entries:
        full_tid = mapping_entry.get("full")
        if isinstance(full_tid, str):
            lookup[full_tid] = mapping_entry
    count = 0
    for tid in tids:
        full = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
        if not full:
            continue
        _send_control(ctx, full, CONTROL_STOP)
        task_entry, snapshot = _await_control_surface(ctx, full)
        handled_by_runner = False
        if snapshot is None or snapshot.status not in status_cmd.TERMINAL_STATUSES:
            task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
            handled_by_runner = _stop_via_fallback(task_entry)
            task_entry, snapshot = _await_control_surface(ctx, full)

        if snapshot is None or snapshot.status not in status_cmd.TERMINAL_STATUSES:
            if not handled_by_runner:
                task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
                _stop_via_fallback(task_entry)
        elif snapshot.status == "cancelled":
            task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
            _stop_terminal_task_process(task_entry)
        count += 1
    return count


def kill_tasks(
    tids: Iterable[str],
    *,
    context_path: str | os.PathLike[str] | None = None,
) -> int:
    """Force-terminate one or more tasks by sending KILL control messages.

    Spec: [CLI-1.2] (task kill)
    """
    ctx = _resolve_context(context_path)
    entries = _read_tid_mapping_entries(ctx)
    lookup: dict[str, dict[str, Any]] = {}
    for mapping_entry in entries:
        full_tid = mapping_entry.get("full")
        if isinstance(full_tid, str):
            lookup[full_tid] = mapping_entry
    killed = 0
    for tid in tids:
        full = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
        if not full:
            continue
        _send_control(ctx, full, CONTROL_KILL)
        task_entry, snapshot = _await_control_surface(ctx, full)

        if snapshot is not None and snapshot.status == "killed":
            task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
            _kill_terminal_task_process(task_entry)
            killed += 1
            continue

        task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
        handled_by_runner = _kill_via_fallback(task_entry)
        task_entry, snapshot = _await_control_surface(ctx, full)

        if snapshot is not None and snapshot.status == "killed":
            task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
            _kill_terminal_task_process(task_entry)
            killed += 1
            continue

        task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
        task_killed = False
        if not handled_by_runner:
            task_killed = _force_kill_task_processes(task_entry)
        if task_killed or handled_by_runner:
            killed += 1
    return killed


def filter_tids_by_pattern(
    snapshots: Iterable[status_cmd.TaskSnapshot], pattern: str
) -> list[str]:
    if not pattern:
        return [snap.tid for snap in snapshots]
    return [snap.tid for snap in snapshots if fnmatchcase(snap.name, pattern)]
