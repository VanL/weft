"""Task listing and control helpers.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.2.1]
- docs/specifications/10-CLI_Interface.md [CLI-1.3]
- docs/specifications/01-Core_Components.md [CC-3.2]
- docs/specifications/12-Pipeline_Composition_and_UX.md [PL-5.2], [PL-5.3]
"""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Iterable
from dataclasses import replace
from fnmatch import fnmatchcase
from typing import Any

from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    CONTROL_SURFACE_WAIT_INTERVAL,
    CONTROL_SURFACE_WAIT_TIMEOUT,
    QUEUE_CTRL_IN_SUFFIX,
    TASKSPEC_TID_SHORT_LENGTH,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft._exceptions import ControlRejected, TaskNotFound
from weft._runner_plugins import require_runner_plugin
from weft.commands.types import TaskSnapshot as PublicTaskSnapshot
from weft.context import WeftContext, build_context
from weft.core.queue_wait import QueueChangeMonitor
from weft.helpers import (
    iter_queue_json_entries,
    kill_process_tree,
    terminate_process_tree,
)

from . import system as status_cmd
from ._task_history import load_latest_taskspec_payload, pipeline_status_queue_name


def _resolve_context(context_path: str | os.PathLike[str] | None) -> WeftContext:
    return build_context(spec_context=context_path)


def _coerce_context(
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> WeftContext:
    if context is not None:
        return context
    return _resolve_context(context_path)


def _read_tid_mapping_entries(ctx: WeftContext) -> list[dict[str, Any]]:
    queue = ctx.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    try:
        entries: list[dict[str, Any]] = []
        for payload, _timestamp in iter_queue_json_entries(queue):
            if isinstance(payload, dict):
                entries.append(payload)
        return entries
    finally:
        queue.close()


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
            if pid in _host_pids_from_mapping(entry):
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
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> list[status_cmd.TaskSnapshot]:
    ctx = _coerce_context(context=context, context_path=context_path)
    snapshots = status_cmd._collect_task_snapshots(
        ctx, include_terminal=include_terminal, tid_filters=None
    )
    if status_filter:
        snapshots = [s for s in snapshots if s.status == status_filter]
    return snapshots


def _state_from_taskspec(taskspec_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(taskspec_payload, dict):
        return {}
    state = taskspec_payload.get("state")
    return state if isinstance(state, dict) else {}


def _public_snapshot(
    status_snapshot: status_cmd.TaskSnapshot,
    *,
    taskspec_payload: dict[str, Any] | None,
) -> PublicTaskSnapshot:
    state = _state_from_taskspec(taskspec_payload)
    error = state.get("error")
    return_code = state.get("return_code")
    return PublicTaskSnapshot(
        tid=status_snapshot.tid,
        tid_short=status_snapshot.tid_short,
        name=status_snapshot.name,
        status=status_snapshot.status,
        event=status_snapshot.event,
        activity=status_snapshot.activity,
        waiting_on=status_snapshot.waiting_on,
        return_code=return_code if isinstance(return_code, int) else None,
        started_at=status_snapshot.started_at,
        completed_at=status_snapshot.completed_at,
        error=error if isinstance(error, str) and error else None,
        last_timestamp=status_snapshot.last_timestamp,
        duration_seconds=status_snapshot.duration_seconds,
        runner=status_snapshot.runner,
        runtime_handle=status_snapshot.runtime_handle,
        runtime=status_snapshot.runtime,
        metadata=dict(status_snapshot.metadata),
        pipeline_status=status_snapshot.pipeline_status,
    )


def _deadline_from_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    return time.monotonic() + max(0.0, timeout)


def _remaining_timeout(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _raise_watch_timeout(
    *,
    tid: str,
    timeout: float | None,
) -> None:
    raise TimeoutError(f"Timed out after {timeout} seconds watching task {tid}")


def task_status(
    tid: str,
    *,
    include_terminal: bool = True,
    context_path: str | os.PathLike[str] | None = None,
) -> status_cmd.TaskSnapshot | None:
    ctx = _resolve_context(context_path)
    full_tid = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    pipeline_snapshot = _latest_pipeline_status_snapshot(ctx, full_tid)
    snapshots = status_cmd._collect_task_snapshots(
        ctx,
        include_terminal=include_terminal,
        tid_filters={full_tid, full_tid[-TASKSPEC_TID_SHORT_LENGTH:]},
    )
    base_snapshot = snapshots[0] if snapshots else None
    if pipeline_snapshot is not None and _prefer_pipeline_snapshot(
        pipeline_snapshot, base_snapshot
    ):
        return _pipeline_task_snapshot(ctx, full_tid, pipeline_snapshot, base_snapshot)
    if pipeline_snapshot is not None and base_snapshot is not None:
        return replace(base_snapshot, pipeline_status=pipeline_snapshot)
    return base_snapshot


def list_task_snapshots(
    *,
    status_filter: str | None = None,
    include_terminal: bool = False,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> list[PublicTaskSnapshot]:
    """Return public task snapshots for the selected context."""

    ctx = _coerce_context(context=context, context_path=context_path)
    snapshots = list_tasks(
        status_filter=status_filter,
        include_terminal=include_terminal,
        context=ctx,
    )
    return [
        _public_snapshot(
            snapshot,
            taskspec_payload=load_latest_taskspec_payload(ctx, snapshot.tid),
        )
        for snapshot in snapshots
    ]


def task_stats(
    *,
    status_filter: str | None = None,
    include_terminal: bool = False,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> dict[str, int]:
    """Return status counts for the selected task set."""

    counts: dict[str, int] = {}
    for snapshot in list_task_snapshots(
        status_filter=status_filter,
        include_terminal=include_terminal,
        context=context,
        context_path=context_path,
    ):
        counts[snapshot.status] = counts.get(snapshot.status, 0) + 1
    return counts


def task_snapshot(
    tid: str,
    *,
    include_process: bool = False,
    include_terminal: bool = True,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> PublicTaskSnapshot | None:
    """Return one public task snapshot or `None` if absent."""

    del include_process
    ctx = _coerce_context(context=context, context_path=context_path)
    snapshot = task_status(
        tid,
        include_terminal=include_terminal,
        context_path=ctx.root,
    )
    if snapshot is None:
        return None
    return _public_snapshot(
        snapshot,
        taskspec_payload=load_latest_taskspec_payload(ctx, snapshot.tid),
    )


def watch_task_status(
    tid: str,
    *,
    include_process: bool = False,
    include_terminal: bool = True,
    timeout: float | None = None,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> Iterable[PublicTaskSnapshot]:
    """Yield snapshots as the task changes until terminal state."""

    del include_process
    ctx = _coerce_context(context=context, context_path=context_path)
    full_tid = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    deadline = _deadline_from_timeout(timeout)
    monitor_queues = [
        ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False),
        ctx.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False),
    ]
    monitor = QueueChangeMonitor(monitor_queues, config=ctx.config)
    last_seen: tuple[int | None, str | None] | None = None
    try:
        while True:
            snapshot = task_snapshot(
                full_tid,
                include_terminal=include_terminal,
                context=ctx,
            )
            current = (
                snapshot.last_timestamp if snapshot is not None else None,
                snapshot.status if snapshot is not None else None,
            )
            if snapshot is not None and current != last_seen:
                last_seen = current
                yield snapshot
                if snapshot.status in status_cmd.TERMINAL_TASK_STATUSES:
                    return
            if deadline is not None and time.monotonic() >= deadline:
                _raise_watch_timeout(tid=full_tid, timeout=timeout)
            remaining = _remaining_timeout(deadline)
            wait_timeout = (
                status_cmd.STATUS_WATCH_MIN_INTERVAL
                if remaining is None
                else min(status_cmd.STATUS_WATCH_MIN_INTERVAL, remaining)
            )
            monitor.wait(wait_timeout)
    finally:
        monitor.close()
        for queue in monitor_queues:
            queue.close()


def resolve_tid(
    *,
    tid: str | None = None,
    pid: int | None = None,
    reverse: str | None = None,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> str | None:
    """Resolve task id variants through the public TID mapping rules."""

    ctx = _coerce_context(context=context, context_path=context_path)
    return task_tid(tid=tid, pid=pid, reverse=reverse, context_path=ctx.root)


def _pipeline_snapshot_timestamp(pipeline_status: dict[str, Any]) -> int | None:
    timestamp_raw = pipeline_status.get("timestamp")
    if isinstance(timestamp_raw, int):
        return timestamp_raw
    if isinstance(timestamp_raw, float):
        return int(timestamp_raw)
    if isinstance(timestamp_raw, str) and timestamp_raw.isdigit():
        return int(timestamp_raw)
    return None


def _prefer_pipeline_snapshot(
    pipeline_status: dict[str, Any],
    base_snapshot: status_cmd.TaskSnapshot | None,
) -> bool:
    if base_snapshot is None:
        return True
    pipeline_timestamp = _pipeline_snapshot_timestamp(pipeline_status)
    if pipeline_timestamp is None:
        return False
    return pipeline_timestamp >= base_snapshot.last_timestamp


def _latest_pipeline_status_snapshot(
    ctx: WeftContext,
    tid: str,
) -> dict[str, Any] | None:
    taskspec_payload = load_latest_taskspec_payload(ctx, tid)
    if not isinstance(taskspec_payload, dict):
        return None
    status_queue = pipeline_status_queue_name(tid, taskspec_payload)
    if not isinstance(status_queue, str) or not status_queue:
        return None

    queue = ctx.queue(status_queue, persistent=True)
    try:
        latest: dict[str, Any] | None = None
        for payload, _timestamp in iter_queue_json_entries(queue):
            payload_tid = payload.get("pipeline_tid")
            if payload.get("type") != "pipeline_status":
                continue
            if isinstance(payload_tid, str) and payload_tid != tid:
                continue
            latest = payload
        return latest
    finally:
        queue.close()


def _pipeline_task_snapshot(
    ctx: WeftContext,
    tid: str,
    pipeline_status: dict[str, Any],
    base_snapshot: status_cmd.TaskSnapshot | None,
) -> status_cmd.TaskSnapshot:
    taskspec_payload = load_latest_taskspec_payload(ctx, tid) or {}
    state = taskspec_payload.get("state") if isinstance(taskspec_payload, dict) else {}
    state = state if isinstance(state, dict) else {}
    started_at = (
        base_snapshot.started_at
        if base_snapshot is not None
        else state.get("started_at")
        if isinstance(state.get("started_at"), int)
        else None
    )
    completed_at = (
        base_snapshot.completed_at
        if base_snapshot is not None
        else state.get("completed_at")
        if isinstance(state.get("completed_at"), int)
        else None
    )
    timestamp_raw = pipeline_status.get("timestamp")
    last_timestamp = (
        int(timestamp_raw)
        if isinstance(timestamp_raw, int | float | str) and str(timestamp_raw).isdigit()
        else base_snapshot.last_timestamp
        if base_snapshot is not None
        else 0
    )
    now_ns = time.time_ns()
    if isinstance(started_at, int) and not isinstance(completed_at, int):
        duration = max(0.0, (now_ns - started_at) / 1_000_000_000)
    elif isinstance(started_at, int) and isinstance(completed_at, int):
        duration = max(0.0, (completed_at - started_at) / 1_000_000_000)
    else:
        duration = None

    status_value = pipeline_status.get("status")
    status_text = status_value if isinstance(status_value, str) else "created"
    runner = base_snapshot.runner if base_snapshot is not None else None
    runtime_handle = base_snapshot.runtime_handle if base_snapshot is not None else None
    runtime = base_snapshot.runtime if base_snapshot is not None else None

    if base_snapshot is None:
        mapping_entry = mapping_for_tid(ctx, tid)
        runner = status_cmd._runner_name_for_snapshot(
            taskspec=taskspec_payload if isinstance(taskspec_payload, dict) else {},
            mapping_entry=mapping_entry,
        )
        runtime_handle_obj = status_cmd._runtime_handle_from_mapping(
            mapping_entry or {}
        )
        runtime_handle = (
            runtime_handle_obj.to_dict() if runtime_handle_obj is not None else None
        )
        runtime = status_cmd._describe_runtime_handle(runtime_handle_obj)
        status_text = status_cmd._effective_public_status(
            status_text,
            runner_name=runner,
            mapping_entry=mapping_entry,
            runtime_description=runtime,
        )

    activity = pipeline_status.get("activity")
    waiting_on = pipeline_status.get("waiting_on")
    if status_text in status_cmd.TERMINAL_TASK_STATUSES:
        activity = None
        waiting_on = None

    metadata: dict[str, Any] = {}
    if base_snapshot is not None:
        metadata.update(base_snapshot.metadata)
    task_metadata = taskspec_payload.get("metadata")
    if isinstance(task_metadata, dict):
        metadata.update(task_metadata)
    pipeline_name = pipeline_status.get("pipeline_name")
    if isinstance(pipeline_name, str) and pipeline_name:
        snapshot_name = pipeline_name
    elif base_snapshot is not None:
        snapshot_name = base_snapshot.name
    else:
        snapshot_name = str(taskspec_payload.get("name") or tid)

    return status_cmd.TaskSnapshot(
        tid=tid,
        tid_short=tid[-TASKSPEC_TID_SHORT_LENGTH:],
        name=snapshot_name,
        status=status_text,
        event="pipeline_status",
        activity=activity if isinstance(activity, str) and activity else None,
        waiting_on=waiting_on if isinstance(waiting_on, str) and waiting_on else None,
        started_at=started_at if isinstance(started_at, int) else None,
        completed_at=completed_at if isinstance(completed_at, int) else None,
        last_timestamp=last_timestamp,
        duration_seconds=duration,
        runner=runner,
        runtime_handle=runtime_handle,
        runtime=runtime,
        metadata=metadata,
        pipeline_status=pipeline_status,
    )


def _ctrl_in_for_tid(ctx: WeftContext, tid: str) -> str:
    taskspec = load_latest_taskspec_payload(ctx, tid)
    if taskspec:
        io_section = taskspec.get("io") or {}
        control = io_section.get("control") or {}
        ctrl_in = control.get("ctrl_in")
        if isinstance(ctrl_in, str) and ctrl_in:
            return ctrl_in
    return f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"


def _ctrl_out_for_tid(
    ctx: WeftContext,
    tid: str,
    *,
    taskspec: dict[str, Any] | None = None,
) -> str:
    taskspec = taskspec or load_latest_taskspec_payload(ctx, tid)
    if taskspec:
        io_section = taskspec.get("io") or {}
        control = io_section.get("control") or {}
        ctrl_out = control.get("ctrl_out")
        if isinstance(ctrl_out, str) and ctrl_out:
            return ctrl_out
    return f"T{tid}.ctrl_out"


def _send_control(ctx: WeftContext, tid: str, command: str) -> None:
    """Write a control command to a task's ctrl_in queue.

    Spec: [MF-3]
    """
    ctrl_in = _ctrl_in_for_tid(ctx, tid)
    queue = ctx.queue(ctrl_in, persistent=False)
    try:
        queue.write(command)
    finally:
        queue.close()


def _host_pids_from_mapping(entry: dict[str, Any]) -> tuple[int, ...]:
    handle = status_cmd._runtime_handle_from_mapping(entry)
    if handle is None or handle.control.get("authority") != "host-pid":
        return ()
    return handle.scoped_host_pids()


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
    initial_taskspec_payload = load_latest_taskspec_payload(ctx, tid) or {}
    watched_pipeline_status_queue = pipeline_status_queue_name(
        tid,
        initial_taskspec_payload,
    )
    watched_ctrl_out_queue = _ctrl_out_for_tid(
        ctx,
        tid,
        taskspec=initial_taskspec_payload
        if isinstance(initial_taskspec_payload, dict)
        else None,
    )
    public_signal_deadline: float | None = None
    monitor_queues = [
        ctx.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False),
        ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False),
        ctx.queue(watched_ctrl_out_queue, persistent=False),
    ]
    if isinstance(watched_pipeline_status_queue, str) and watched_pipeline_status_queue:
        monitor_queues.append(ctx.queue(watched_pipeline_status_queue, persistent=True))
    monitor = QueueChangeMonitor(monitor_queues, config=ctx.config)
    try:
        while True:
            taskspec_payload = load_latest_taskspec_payload(ctx, tid) or {}
            pipeline_status_queue = pipeline_status_queue_name(tid, taskspec_payload)
            ctrl_out_queue = _ctrl_out_for_tid(
                ctx,
                tid,
                taskspec=taskspec_payload
                if isinstance(taskspec_payload, dict)
                else None,
            )
            if (
                pipeline_status_queue != watched_pipeline_status_queue
                or ctrl_out_queue != watched_ctrl_out_queue
            ):
                monitor.close()
                for queue in monitor_queues:
                    queue.close()
                monitor_queues = [
                    ctx.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False),
                    ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False),
                    ctx.queue(ctrl_out_queue, persistent=False),
                ]
                if isinstance(pipeline_status_queue, str) and pipeline_status_queue:
                    monitor_queues.append(
                        ctx.queue(pipeline_status_queue, persistent=True)
                    )
                monitor = QueueChangeMonitor(monitor_queues, config=ctx.config)
                watched_pipeline_status_queue = pipeline_status_queue
                watched_ctrl_out_queue = ctrl_out_queue

            ctrl_queue = next(
                (
                    queue
                    for queue in monitor_queues
                    if queue.name == watched_ctrl_out_queue
                ),
                None,
            )
            while ctrl_queue is not None:
                ctrl_raw = ctrl_queue.read_one()
                if ctrl_raw is None:
                    break
                ctrl_payload = ctrl_raw[0] if isinstance(ctrl_raw, tuple) else ctrl_raw
                try:
                    payload = json.loads(str(ctrl_payload))
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if ("command" in payload and "status" in payload) or (
                    payload.get("type") == "terminal"
                    and isinstance(payload.get("status"), str)
                ):
                    public_signal_deadline = (
                        time.monotonic() + CONTROL_SURFACE_WAIT_INTERVAL
                    )

            mapping_entry = mapping_for_tid(ctx, tid)
            if mapping_entry is not None:
                latest_entry = mapping_entry
            snapshot = task_status(tid, context_path=ctx.root)
            if snapshot is not None:
                latest_snapshot = snapshot
                if snapshot.status in status_cmd.TERMINAL_TASK_STATUSES:
                    return latest_entry, latest_snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if public_signal_deadline is not None:
                    grace_remaining = public_signal_deadline - time.monotonic()
                    if grace_remaining > 0:
                        monitor.wait(
                            min(grace_remaining, CONTROL_SURFACE_WAIT_INTERVAL)
                        )
                        continue
                return latest_entry, latest_snapshot
            monitor.wait(min(remaining, CONTROL_SURFACE_WAIT_INTERVAL))
    finally:
        monitor.close()
        for queue in monitor_queues:
            queue.close()


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

    handle = status_cmd._runtime_handle_from_mapping(task_entry)
    if handle is not None:
        if handle.control.get("authority") == "external-supervisor":
            return False
        plugin = require_runner_plugin(handle.runner)
        plugin.stop(handle, timeout=0.2)
        return True

    for pid in _host_pids_from_mapping(task_entry):
        if _pid_exists(pid):
            terminate_process_tree(pid, timeout=0.2, kill_after=False)
            return False

    return False


def _kill_via_fallback(task_entry: dict[str, Any] | None) -> bool:
    if task_entry is None:
        return False

    handle = status_cmd._runtime_handle_from_mapping(task_entry)
    if handle is not None:
        if handle.control.get("authority") == "external-supervisor":
            return False
        plugin = require_runner_plugin(handle.runner)
        plugin.kill(handle, timeout=0.2)
        return True

    for pid in _host_pids_from_mapping(task_entry):
        if _pid_exists(pid):
            sigusr1 = getattr(signal, "SIGUSR1", None)
            if sigusr1 is not None:
                try:
                    os.kill(pid, sigusr1)
                except OSError:
                    pass
            else:
                kill_process_tree(pid, timeout=0.2)
            return False

    return False


def _force_kill_task_processes(task_entry: dict[str, Any] | None) -> bool:
    if task_entry is None:
        return False

    pids: list[int] = []
    pids.extend(_host_pids_from_mapping(task_entry))

    task_killed = False
    for pid_value in set(pids):
        if kill_process_tree(pid_value, timeout=0.2):
            task_killed = True
    return task_killed


def stop_tasks(
    tids: Iterable[str],
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> int:
    """Gracefully stop one or more tasks by sending STOP control messages.

    Spec: [CLI-1.2] (task stop)
    """
    ctx = _coerce_context(context=context, context_path=context_path)
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
        if snapshot is None or snapshot.status not in status_cmd.TERMINAL_TASK_STATUSES:
            task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
            handled_by_runner = _stop_via_fallback(task_entry)
            task_entry, snapshot = _await_control_surface(ctx, full)

        if snapshot is None or snapshot.status not in status_cmd.TERMINAL_TASK_STATUSES:
            if not handled_by_runner:
                task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
                _stop_via_fallback(task_entry)
        count += 1
    return count


def stop_task(
    tid: str,
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> None:
    """Stop one task or raise a typed exception."""

    ctx = _coerce_context(context=context, context_path=context_path)
    full = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    if (
        task_status(full, context_path=ctx.root) is None
        and mapping_for_tid(ctx, full) is None
    ):
        raise TaskNotFound(f"Task {tid} not found")
    if stop_tasks([full], context=ctx) <= 0:
        raise ControlRejected(f"Failed to stop task {tid}")


def kill_tasks(
    tids: Iterable[str],
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> int:
    """Force-terminate one or more tasks by sending KILL control messages.

    Spec: [CLI-1.2] (task kill)
    """
    ctx = _coerce_context(context=context, context_path=context_path)
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
            killed += 1
            continue

        task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
        handled_by_runner = _kill_via_fallback(task_entry)
        task_entry, snapshot = _await_control_surface(ctx, full)

        if snapshot is not None and snapshot.status == "killed":
            killed += 1
            continue

        task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
        task_killed = False
        if not handled_by_runner:
            task_killed = _force_kill_task_processes(task_entry)
        if task_killed or handled_by_runner:
            killed += 1
    return killed


def kill_task(
    tid: str,
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> None:
    """Kill one task or raise a typed exception."""

    ctx = _coerce_context(context=context, context_path=context_path)
    full = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    if (
        task_status(full, context_path=ctx.root) is None
        and mapping_for_tid(ctx, full) is None
    ):
        raise TaskNotFound(f"Task {tid} not found")
    if kill_tasks([full], context=ctx) <= 0:
        raise ControlRejected(f"Failed to kill task {tid}")


def filter_tids_by_pattern(
    snapshots: Iterable[status_cmd.TaskSnapshot | PublicTaskSnapshot],
    pattern: str,
) -> list[str]:
    if not pattern:
        return [snap.tid for snap in snapshots]
    return [snap.tid for snap in snapshots if fnmatchcase(snap.name, pattern)]
