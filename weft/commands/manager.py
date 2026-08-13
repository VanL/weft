"""Shared manager capability operations plus CLI formatting helpers.

Spec references:
- docs/specifications/10-CLI_Interface.md (manager start, stop, list, status, serve)
- docs/specifications/03-Manager_Architecture.md [MA-0]--[MA-4]
"""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any, Literal, cast

from simplebroker import format_message_id
from weft._constants import MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS
from weft._exceptions import ControlRejected, ManagerNotRunning, ManagerStartFailed
from weft.commands._boundary import typed_command_errors
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
        liveness=(
            cast(
                Literal["live", "stale", "unknown", "non_live"],
                record["liveness"],
            )
            if record.get("liveness") in {"live", "stale", "unknown", "non_live"}
            else None
        ),
        proof_source=(
            record.get("proof_source")
            if isinstance(record.get("proof_source"), str)
            else None
        ),
        proof_detail=(
            record.get("proof_detail")
            if isinstance(record.get("proof_detail"), str)
            else None
        ),
        dispatch_eligible=(
            record.get("dispatch_eligible")
            if isinstance(record.get("dispatch_eligible"), bool)
            else None
        ),
        canonical_candidate=(
            record.get("canonical_candidate")
            if isinstance(record.get("canonical_candidate"), bool)
            else None
        ),
        canonical=(
            record.get("canonical")
            if isinstance(record.get("canonical"), bool)
            else None
        ),
    )


@typed_command_errors
def cmd_manager_list(
    *,
    all: bool = False,
    diagnostic: bool = False,
    context: Path | None = None,
) -> tuple[ManagerSnapshot, ...]:
    """Return manager registry snapshots, optionally with liveness proof.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    resolved = build_context(context)
    records = (
        manager_runtime.manager_diagnostic_records(
            resolved,
            include_stopped=all,
        )
        if diagnostic
        else manager_runtime.list_manager_records(
            resolved,
            include_stopped=all,
            canonical_only=False,
        )
    )
    return tuple(_manager_snapshot(record) for record in records)


def start_manager(context: WeftContext) -> ManagerSnapshot:
    """Ensure a canonical manager exists and return its registry snapshot."""

    record, _started_here, _process_handle = manager_runtime.ensure_manager(context)
    return _manager_snapshot(record)


@typed_command_errors
def cmd_manager_start(
    *,
    context: Path | None = None,
    replace: bool = False,
) -> ManagerSnapshot:
    """Start or reuse the canonical manager and return its snapshot.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    resolved = build_context(context)
    if replace:
        replaced, message = manager_runtime.replace_active_manager(resolved)
        if not replaced:
            raise ManagerStartFailed(message or "Manager replacement failed")
        record, started_here, _process_handle = manager_runtime.start_manager(resolved)
    else:
        record, started_here, _process_handle = manager_runtime.ensure_manager(resolved)
    snapshot = _manager_snapshot(record)
    return dataclass_replace(snapshot, started_here=started_here)


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


@typed_command_errors
def cmd_manager_stop(
    tid: str | None = None,
    *,
    force: bool = False,
    timeout: float = MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    context: Path | None = None,
) -> ManagerSnapshot | None:
    """Stop a manager and return its terminal snapshot, or `None` if absent.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    resolved = build_context(context)
    record: dict[str, Any] | None = None
    if tid is None:
        record = manager_runtime.select_active_manager(
            resolved,
            probe_stale=True,
            probe_cache={},
        )
        if record is None:
            return None
        record_tid = record.get("tid")
        if not isinstance(record_tid, str) or not record_tid:
            raise ManagerNotRunning("Active manager record is missing a TID")
        tid = record_tid
    else:
        record = manager_runtime.manager_record(resolved, tid)

    stopped, message = manager_runtime.stop_manager(
        resolved,
        record,
        tid=tid,
        timeout=timeout,
        force=force,
    )
    if not stopped:
        raise ControlRejected(message or f"Manager {tid} did not stop")
    terminal = manager_runtime.manager_record(resolved, tid)
    if terminal is None:
        terminal = dict(record or {"tid": tid, "name": "manager"})
        terminal["status"] = "stopped"
    return _manager_snapshot(terminal)


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


@typed_command_errors
def cmd_manager_status(
    tid: str,
    *,
    context: Path | None = None,
) -> ManagerSnapshot:
    """Return the requested manager snapshot or raise a typed absence error.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    record = manager_runtime.manager_record(build_context(context), tid)
    if record is None:
        raise ManagerNotRunning(f"Manager {tid} not found")
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


__all__ = [  # noqa: RUF022 approved [TS-3.1] [RUFF-SUP-245] exception
    "list_managers",
    "manager_status",
    "serve_manager",
    "start_manager",
    "start_command",
    "stop_manager",
    "stop_command",
]
