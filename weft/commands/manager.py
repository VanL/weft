"""Shared manager capability operations plus CLI formatting helpers.

Spec references:
- docs/specifications/10-CLI_Interface.md (manager start, stop, list, status, serve)
- docs/specifications/03-Manager_Architecture.md [MA-0]--[MA-4]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from simplebroker import format_message_id
from weft._constants import MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS
from weft._exceptions import ControlRejected, ManagerNotRunning
from weft.commands.types import ManagerSnapshot
from weft.context import WeftContext, build_context
from weft.core import manager_runtime


def _manager_record_to_json(record: dict[str, Any]) -> dict[str, Any]:
    """Project manager-owned broker message IDs for external JSON."""

    payload = dict(record)
    for key in ("timestamp", "_pong_live_at"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            payload[key] = format_message_id(value)

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        projected_metadata = dict(metadata)
        supersession_timestamp = projected_metadata.get(
            "supersession_observed_timestamp"
        )
        if isinstance(supersession_timestamp, int) and not isinstance(
            supersession_timestamp, bool
        ):
            projected_metadata["supersession_observed_timestamp"] = format_message_id(
                supersession_timestamp
            )
        payload["metadata"] = projected_metadata
    return payload


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
        internal_requests=(
            record.get("internal_requests")
            if isinstance(record.get("internal_requests"), str)
            else None
        ),
        internal_reserved=(
            record.get("internal_reserved")
            if isinstance(record.get("internal_reserved"), str)
            else None
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

    record, _started_here, _process_handle = manager_runtime.ensure_manager(context)
    return _manager_snapshot(record)


def serve_manager(context: WeftContext) -> None:
    """Run the canonical manager in the foreground."""

    exit_code, message = manager_runtime.serve_manager_foreground(context)
    if exit_code != 0:
        raise ManagerNotRunning(message or "Manager serve failed")


def stop_manager(
    context: WeftContext,
    tid: str | None = None,
    *,
    force: bool = False,
    timeout: float = MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
) -> None:
    """Stop one manager or the active manager, or raise a typed exception."""

    record: dict[str, Any] | None = None
    if tid is None:
        record = manager_runtime.select_active_manager(
            context,
            probe_stale=True,
            probe_cache={},
        )
        if record is None:
            raise ManagerNotRunning("No active manager")
        tid_value = record.get("tid")
        if not isinstance(tid_value, str) or not tid_value:
            raise ManagerNotRunning("Active manager record is missing a TID")
        tid = tid_value
    stopped, message = manager_runtime.stop_manager(
        context,
        record,
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
        for record in manager_runtime.list_manager_records(
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

    record = manager_runtime.manager_record(context, tid)
    if record is None:
        return None
    return _manager_snapshot(record)


def start_command(
    *,
    context_path: Path | None = None,
    replace: bool = False,
) -> tuple[int, str | None]:
    context = build_context(context_path)
    if replace:
        replaced, message = manager_runtime.replace_active_manager(context)
        if not replaced:
            return 1, message or "Manager replacement failed"
        record, started_here, _process_handle = manager_runtime.start_manager(context)
    else:
        record, started_here, _process_handle = manager_runtime.ensure_manager(context)
    tid = cast(str, record.get("tid"))

    if started_here:
        return 0, f"Started manager {tid}"
    return 0, f"Manager {tid} already running"


def stop_command(
    *,
    tid: str | None,
    force: bool,
    timeout: float = MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    context_path: Path | None = None,
    stop_if_absent: bool = False,
) -> tuple[int, str | None]:
    context = build_context(context_path)
    record: dict[str, Any] | None = None
    if tid is None:
        record = manager_runtime.select_active_manager(
            context,
            probe_stale=True,
            probe_cache={},
        )
        if record is None:
            return 0, None
        tid_value = record.get("tid")
        if not isinstance(tid_value, str) or not tid_value:
            return 1, "Active manager record is missing a TID"
        tid = tid_value
    stopped, message = manager_runtime.stop_manager(
        context,
        record,
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
    diagnostic: bool = False,
    context_path: Path | None = None,
) -> tuple[int, str | None]:
    context = build_context(context_path)
    if diagnostic:
        records = manager_runtime.manager_diagnostic_records(
            context,
            include_stopped=include_stopped,
        )
    else:
        records = manager_runtime.list_manager_records(
            context,
            include_stopped=include_stopped,
            canonical_only=False,
        )

    if json_output:
        payload = json.dumps(
            [_manager_record_to_json(record) for record in records],
            indent=2,
        )
        return 0, payload

    if not records:
        return 0, "No registered managers"

    if diagnostic:
        lines = ["TID        STATUS    LIVE      CANONICAL  PROOF               NAME"]
        for data in sorted(records, key=lambda record: str(record.get("tid", ""))):
            tid = str(data.get("tid", ""))
            status = str(data.get("status", "unknown"))
            liveness = str(data.get("liveness", "unknown"))
            canonical = "yes" if data.get("canonical") is True else "no"
            proof = str(data.get("proof_source", ""))
            name = str(data.get("name", ""))
            lines.append(
                f"{tid}  {status:<9} {liveness:<9} {canonical:<10} {proof:<19} {name}"
            )
        return 0, "\n".join(lines)

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
    record = manager_runtime.manager_record(context, tid)

    if not record:
        return 1, f"Manager {tid} not found"

    if json_output:
        return 0, json.dumps(_manager_record_to_json(record), indent=2)

    parts = [
        f"Manager {tid}",
        f"Name: {record.get('name', '')}",
        f"Status: {record.get('status', 'unknown')}",
    ]
    runtime_handle = record.get("runtime_handle")
    if isinstance(runtime_handle, dict):
        parts.append(f"Runtime: {json.dumps(runtime_handle, sort_keys=True)}")
    return 0, "\n".join(parts)


__all__ = [  # noqa: RUF022 approved [TS-3.1] [RUFF-SUP-245] exception
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
