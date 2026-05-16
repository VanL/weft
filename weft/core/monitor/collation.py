"""Pure Monitor task-log collation reducers.

This module converts decoded ``weft.log.tasks`` rows into durable Monitor-store
updates. It performs no broker I/O, logging, table creation, or deletion.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from weft._constants import (
    STATUS_COMPLETED,
    TERMINAL_TASK_EVENTS,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.core.queue_window import DecodedQueueWindowRow, payload_string


@dataclass(frozen=True, slots=True)
class MonitorTaskEventUpdate:
    """One durable Monitor-store update derived from a task-log row."""

    tid: str
    queue_name: str
    message_id: int
    event: str | None
    status: str | None
    observed_at_ns: int
    name: str | None = None
    runner: str | None = None
    parent_tid: str | None = None
    role: str | None = None
    terminal_seen: bool = False
    terminal_event: str | None = None
    terminal_status: str | None = None
    return_code: int | None = None
    first_seen_at_ns: int | None = None
    last_seen_at_ns: int | None = None
    started_at_ns: int | None = None
    completed_at_ns: int | None = None
    taskspec_summary: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    bookkeeping: dict[str, Any] = field(default_factory=dict)
    reserved_probe_needed: bool = False

    def message_summary(self) -> dict[str, Any]:
        """Return JSON-safe child-message metadata for the store."""

        return {
            "queue_name": self.queue_name,
            "message_id": self.message_id,
            "event": self.event,
            "status": self.status,
            "observed_at_ns": self.observed_at_ns,
        }


def update_from_task_log_row(
    row: DecodedQueueWindowRow,
) -> MonitorTaskEventUpdate | None:
    """Return a Monitor-store update for one decoded task-log row.

    Malformed rows and non-task rows are intentionally ignored here. The
    cleanup policy path owns malformed deletion and diagnostics.
    """

    if row.malformed_reason is not None or row.payload is None:
        return None
    return update_from_task_log_payload(
        row.payload,
        queue_name=row.raw.queue,
        message_id=row.raw.message_id,
    )


def update_from_task_log_payload(
    payload: Mapping[str, Any],
    *,
    queue_name: str = WEFT_GLOBAL_LOG_QUEUE,
    message_id: int,
) -> MonitorTaskEventUpdate | None:
    """Return a Monitor-store update for one task-log JSON object."""

    tid = payload_string(payload, "tid")
    if tid is None:
        return None

    event = payload_string(payload, "event")
    status = payload_string(payload, "status")
    terminal_status = _terminal_status(event=event, status=status)
    terminal_seen = terminal_status is not None
    taskspec = _mapping(payload.get("taskspec"))
    taskspec_state = _mapping(taskspec.get("state") if taskspec else None)
    metadata = _mapping(taskspec.get("metadata") if taskspec else None)
    spec = _mapping(taskspec.get("spec") if taskspec else None)
    runner_section = _mapping(spec.get("runner") if spec else None)
    state = _state_summary(payload, taskspec_state)
    diagnostics = _diagnostics_summary(payload)
    resources = _resource_summary(payload, taskspec_state)
    lifecycle = _lifecycle_summary(
        payload,
        event=event,
        status=status,
        terminal_status=terminal_status,
    )
    started_at = _int_or_none(state.get("started_at"))
    completed_at = _int_or_none(state.get("completed_at"))
    return_code = _int_or_none(state.get("return_code"))

    return MonitorTaskEventUpdate(
        tid=tid,
        queue_name=queue_name,
        message_id=message_id,
        event=event,
        status=status,
        observed_at_ns=message_id,
        name=payload_string(taskspec, "name") if taskspec else None,
        runner=payload_string(runner_section, "name") if runner_section else None,
        parent_tid=payload_string(metadata, "parent_tid") if metadata else None,
        role=payload_string(metadata, "role") if metadata else None,
        terminal_seen=terminal_seen,
        terminal_event=event if terminal_seen else None,
        terminal_status=terminal_status,
        return_code=return_code,
        first_seen_at_ns=message_id,
        last_seen_at_ns=message_id,
        started_at_ns=started_at,
        completed_at_ns=completed_at,
        taskspec_summary=_taskspec_summary(taskspec),
        state=state,
        lifecycle=lifecycle,
        resources=resources,
        diagnostics=diagnostics,
        bookkeeping={
            "last_event": event,
            "last_message_id": message_id,
        },
        reserved_probe_needed=(terminal_seen and terminal_status != STATUS_COMPLETED),
    )


def _terminal_status(*, event: str | None, status: str | None) -> str | None:
    if event is not None:
        terminal = TERMINAL_TASK_EVENTS.get(event)
        if terminal is not None:
            return terminal
    if status in TERMINAL_TASK_STATUSES:
        return status
    return None


def _taskspec_summary(taskspec: Mapping[str, Any] | None) -> dict[str, Any]:
    if taskspec is None:
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "tid",
        "version",
        "name",
        "description",
        "spec",
        "io",
        "state",
        "metadata",
    ):
        value = taskspec.get(key)
        if value is not None:
            summary[key] = value
    return summary


def _state_summary(
    payload: Mapping[str, Any],
    taskspec_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    state: dict[str, Any] = dict(taskspec_state or {})
    for key in (
        "status",
        "return_code",
        "started_at",
        "completed_at",
        "pid",
        "error",
        "time",
        "memory",
        "cpu",
        "fds",
        "net_connections",
        "peak_memory",
        "peak_cpu",
        "peak_fds",
        "peak_net_connections",
    ):
        value = payload.get(key)
        if value is not None:
            state[key] = value
    return state


def _lifecycle_summary(
    payload: Mapping[str, Any],
    *,
    event: str | None,
    status: str | None,
    terminal_status: str | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "event": event,
        "status": status,
        "terminal_status": terminal_status,
    }
    for key in ("activity", "waiting_on", "message_id", "checkpoint"):
        value = payload.get(key)
        if value is not None:
            summary[key] = value
    return summary


def _resource_summary(
    payload: Mapping[str, Any],
    taskspec_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    source: dict[str, Any] = {}
    if taskspec_state is not None:
        source.update(taskspec_state)
    source.update(payload)
    for key in (
        "memory",
        "cpu",
        "fds",
        "net_connections",
        "peak_memory",
        "peak_cpu",
        "peak_fds",
        "peak_net_connections",
    ):
        value = source.get(key)
        if value is not None:
            resources[key] = value
    return resources


def _diagnostics_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    for key in ("runner_diagnostics", "runtime_handle", "error"):
        value = payload.get(key)
        if value is not None:
            diagnostics[key] = value
    return diagnostics


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
