"""Worker management commands for the Weft CLI.

Spec references:
- docs/specifications/10-CLI_Interface.md (worker start, stop, list, status)
- docs/specifications/03-Worker_Architecture.md [WA-0]--[WA-4]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from weft.commands._manager_bootstrap import (
    _ensure_manager,
    _list_manager_records,
    _manager_record,
    _stop_manager,
)
from weft.context import build_context


def start_command(*, context_path: Path | None = None) -> tuple[int, str | None]:
    context = build_context(context_path)
    record, started_here, _process_handle = _ensure_manager(context, verbose=False)
    tid = cast(str, record.get("tid"))
    pid = record.get("pid")

    if started_here:
        return 0, f"Started manager {tid} (pid {pid})"
    return 0, f"Manager {tid} already running (pid {pid})"


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
        return 1, f"Worker {tid} did not stop within {timeout:.1f}s"
    if message.startswith("Manager ") and " did not stop within " in message:
        return 1, message.replace("Manager", "Worker", 1)
    return 1, message


def list_command(
    *,
    json_output: bool,
    context_path: Path | None = None,
) -> tuple[int, str | None]:
    context = build_context(context_path)
    records = _list_manager_records(
        context,
        include_stopped=False,
        canonical_only=False,
    )

    if json_output:
        payload = json.dumps(records, indent=2)
        return 0, payload

    if not records:
        return 0, "No registered workers"

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
