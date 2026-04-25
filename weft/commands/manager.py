"""Shared manager capability operations plus CLI formatting helpers.

Spec references:
- docs/specifications/10-CLI_Interface.md (manager start, stop, list, status, serve)
- docs/specifications/03-Manager_Architecture.md [MA-0]--[MA-4]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from weft._exceptions import ControlRejected, ManagerNotRunning
from weft.commands.types import ManagerSnapshot
from weft.context import WeftContext, build_context
from weft.core.manager_runtime import (
    build_manager_spec,
    ensure_manager,
    generate_tid,
    list_manager_records,
    manager_record,
    select_active_manager,
    serve_manager_foreground,
)
from weft.core.manager_runtime import (
    start_manager as start_manager_runtime,
)
from weft.core.manager_runtime import (
    stop_manager as stop_manager_runtime,
)

_build_manager_spec = build_manager_spec
_ensure_manager = ensure_manager
_generate_tid = generate_tid
_list_manager_records = list_manager_records
_manager_record = manager_record
_select_active_manager = select_active_manager
_serve_manager_foreground = serve_manager_foreground
_start_manager = start_manager_runtime
_stop_manager = stop_manager_runtime


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


def start_manager(context: WeftContext) -> ManagerSnapshot:
    """Ensure a canonical manager exists and return its registry snapshot."""

    record, _started_here, _process_handle = _ensure_manager(context, verbose=False)
    return _manager_snapshot(record)


def serve_manager(context: WeftContext) -> None:
    """Run the canonical manager in the foreground."""

    exit_code, message = _serve_manager_foreground(context)
    if exit_code != 0:
        raise ManagerNotRunning(message or "Manager serve failed")


def stop_manager(
    context: WeftContext,
    tid: str,
    *,
    force: bool = False,
    timeout: float = 5.0,
) -> None:
    """Stop one manager or raise a typed exception."""

    stopped, message = _stop_manager(
        context,
        None,
        tid=tid,
        timeout=timeout,
        force=force,
    )
    if not stopped:
        raise ControlRejected(message or f"Manager {tid} did not stop")


def list_managers(
    context: WeftContext,
    *,
    include_stopped: bool = False,
) -> list[ManagerSnapshot]:
    """Return manager registry rows as typed snapshots."""

    return [
        _manager_snapshot(record)
        for record in _list_manager_records(
            context,
            include_stopped=include_stopped,
            canonical_only=False,
        )
    ]


def manager_status(
    context: WeftContext,
    tid: str,
) -> ManagerSnapshot | None:
    """Return one manager snapshot or `None` if absent."""

    record = _manager_record(context, tid)
    if record is None:
        return None
    return _manager_snapshot(record)


def start_command(*, context_path: Path | None = None) -> tuple[int, str | None]:
    context = build_context(context_path)
    record, started_here, _process_handle = _ensure_manager(context, verbose=False)
    tid = cast(str, record.get("tid"))

    if started_here:
        return 0, f"Started manager {tid}"
    return 0, f"Manager {tid} already running"


def stop_command(
    *,
    tid: str,
    force: bool,
    timeout: float,
    context_path: Path | None = None,
    stop_if_absent: bool = False,
) -> tuple[int, str | None]:
    context = build_context(context_path)
    stopped, message = _stop_manager(
        context,
        None,
        tid=tid,
        timeout=timeout,
        force=force,
        stop_if_absent=stop_if_absent,
    )
    if stopped:
        return 0, None
    if message is None:
        return 1, f"Manager {tid} did not stop within {timeout:.1f}s"
    return 1, message


def list_command(
    *,
    json_output: bool,
    include_stopped: bool = False,
    context_path: Path | None = None,
) -> tuple[int, str | None]:
    context = build_context(context_path)
    records = _list_manager_records(
        context,
        include_stopped=include_stopped,
        canonical_only=False,
    )

    if json_output:
        payload = json.dumps(records, indent=2)
        return 0, payload

    if not records:
        return 0, "No registered managers"

    lines = ["TID        STATUS    NAME"]
    for data in sorted(records, key=lambda record: str(record.get("tid", ""))):
        tid = str(data.get("tid", ""))
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
    record = _manager_record(context, tid)

    if not record:
        return 1, f"Manager {tid} not found"

    if json_output:
        return 0, json.dumps(record, indent=2)

    parts = [
        f"Manager {tid}",
        f"Name: {record.get('name', '')}",
        f"Status: {record.get('status', 'unknown')}",
    ]
    runtime_handle = record.get("runtime_handle")
    if isinstance(runtime_handle, dict):
        parts.append(f"Runtime: {json.dumps(runtime_handle, sort_keys=True)}")
    return 0, "\n".join(parts)


__all__ = [
    "list_managers",
    "manager_status",
    "serve_manager",
    "start_manager",
    "start_command",
    "stop_manager",
    "stop_command",
    "list_command",
    "status_command",
]
