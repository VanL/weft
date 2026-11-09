"""Shared manager capability operations plus CLI formatting helpers.

Spec references:
- docs/specifications/10-CLI_Interface.md (manager start, stop, list, status, serve)
- docs/specifications/03-Manager_Architecture.md [MA-0]--[MA-4]
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any, Literal, cast

from simplebroker import format_message_id
from simplebroker.ext import BrokerError
from weft._constants import MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS
from weft._exceptions import (
    ControlRejected,
    InvalidTID,
    ManagerNotRunning,
    ManagerStartFailed,
)
from weft.commands._boundary import typed_command_errors
from weft.commands.serve import _serve_manager_context
from weft.commands.submission import normalize_tid
from weft.commands.types import ManagerSnapshot
from weft.context import WeftContext, build_context
from weft.core import manager_runtime
from weft.core.dispatch_refusals import (
    dispatch_refusal_lines as format_dispatch_refusal_lines,
)
from weft.core.dispatch_refusals import (
    dispatch_refusals_json,
    dispatch_refusals_observation,
)
from weft.core.task_state import latest_task_state_rows, task_state_queue_name


def dispatch_refusal_lines(snapshot: ManagerSnapshot) -> list[str]:
    """Render the shared Manager observation at the CLI capability boundary."""
    return format_dispatch_refusal_lines(
        snapshot.dispatch_refusals, snapshot.dispatch_refusals_source
    )


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
    for key in ("dispatch_refusals", "dispatch_refusals_source"):
        if key in payload:
            payload[key] = dispatch_refusals_json(payload[key])
    return payload


def _manager_snapshot(
    record: dict[str, Any], *, state: Mapping[str, Any] | None = None
) -> ManagerSnapshot:
    return ManagerSnapshot(
        dispatch_refusals=dispatch_refusals_observation(
            state.get("dispatch_refusals") if state else None
        ),
        dispatch_refusals_source={
            "queue": task_state_queue_name(str(record["tid"])),
            "message_id": state["timestamp"],
        }
        if state and type(state.get("timestamp")) is int
        else None,
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


def _observed_manager_snapshots(
    context: WeftContext, records: Sequence[dict[str, Any]]
) -> list[ManagerSnapshot]:
    """Read only selected Manager snapshots, retaining unavailable on read failure."""
    try:
        rows = (
            latest_task_state_rows(
                context, tids=[str(record["tid"]) for record in records]
            )
            if records
            else {}
        )
    except (BrokerError, OSError, RuntimeError):
        rows = {}
    return [
        _manager_snapshot(
            record,
            state={
                **rows[str(record["tid"])][1],
                "timestamp": rows[str(record["tid"])][0],
            }
            if str(record["tid"]) in rows
            else None,
        )
        for record in records
    ]


@typed_command_errors
def cmd_manager_list(
    *,
    all: bool = False,
    diagnostic: bool = False,
    context: Path | None = None,
) -> tuple[ManagerSnapshot, ...]:
    """Return manager registry snapshots, optionally with liveness proof.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2];
    docs/specifications/10-CLI_Interface.md [CLI-1.2.1], [CLI-1.3].
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
            observe=True,
        )
    )
    return tuple(_observed_manager_snapshots(resolved, records))


def start_manager(context: WeftContext) -> ManagerSnapshot:
    """Ensure a canonical manager exists and return its registry snapshot."""

    result = manager_runtime.ensure_manager(context)
    if result.outcome != "ready" or result.manager_record is None:
        raise ManagerStartFailed(
            f"Manager readiness could not be established: {result.reason}"
        )
    return _manager_snapshot(result.manager_record)


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
        result = manager_runtime.ensure_manager(resolved)
        if result.outcome != "ready" or result.manager_record is None:
            raise ManagerStartFailed(
                f"Manager readiness could not be established: {result.reason}"
            )
        record = result.manager_record
        started_here = result.started_here
    snapshot = _manager_snapshot(record)
    return dataclass_replace(snapshot, started_here=started_here)


def serve_manager(context: WeftContext) -> None:
    """Run the canonical manager in the foreground."""

    _serve_manager_context(context)


def stop_manager(
    context: WeftContext,
    tid: str | None = None,
    *,
    force: bool = False,
    timeout: float = MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    _record: dict[str, Any] | None = None,
) -> None:
    """Stop one manager or the active manager, or raise a typed exception.

    ``_record`` preserves a typed facade's already-selected control identity.
    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    record = _record
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
        # An unknown spelling keeps the existing not-found path [CLI-1.2.3].
        with suppress(InvalidTID):
            tid = normalize_tid(tid)
        record = manager_runtime.manager_record(resolved, tid)

    stop_manager(resolved, tid, force=force, timeout=timeout, _record=record)
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
    """Return manager registry rows as typed snapshots ([CLI-1.2.1])."""

    return _observed_manager_snapshots(
        context,
        manager_runtime.list_manager_records(
            context,
            include_stopped=include_stopped,
            canonical_only=False,
            observe=True,
        ),
    )


def _observed_manager_record(context: WeftContext, tid: str) -> dict[str, Any] | None:
    """Return one Manager row through the status-command read ([CLI-1.2.1])."""

    records = manager_runtime.list_manager_records(
        context,
        include_stopped=True,
        canonical_only=False,
        observe=True,
        tid=tid,
    )
    return records[0] if records else None


def manager_status(
    context: WeftContext,
    tid: str,
) -> ManagerSnapshot | None:
    """Return one manager snapshot or `None` if absent."""

    record = _observed_manager_record(context, tid)
    if record is None:
        return None
    return _observed_manager_snapshots(context, [record])[0]


@typed_command_errors
def cmd_manager_status(
    tid: str,
    *,
    context: Path | None = None,
) -> ManagerSnapshot:
    """Return the requested manager snapshot or raise a typed absence error.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    # An unknown spelling keeps the existing not-found path [CLI-1.2.3].
    with suppress(InvalidTID):
        tid = normalize_tid(tid)
    resolved = build_context(context)
    record = _observed_manager_record(resolved, tid)
    if record is None:
        raise ManagerNotRunning(f"Manager {tid} not found")
    return _observed_manager_snapshots(resolved, [record])[0]


__all__ = [
    "list_managers",
    "manager_status",
    "serve_manager",
    "start_manager",
    "stop_manager",
]
