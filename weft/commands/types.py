"""Shared runtime dataclasses for ops and client surfaces.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1], [CLI-4], [CLI-6]
- docs/specifications/13C-Using_Weft_With_Django.md [DJ-2.2]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SubmittedTaskReceipt:
    """Internal submission receipt shared by ops and tests."""

    tid: str
    name: str
    submitted_at_ns: int


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    """Public current-state view for one task."""

    tid: str
    name: str
    status: str
    return_code: int | None
    started_at: int | None
    completed_at: int | None
    error: str | None
    runtime_handle: dict[str, Any] | None
    metadata: dict[str, Any]
    tid_short: str | None = None
    event: str | None = None
    activity: str | None = None
    waiting_on: str | None = None
    last_timestamp: int | None = None
    duration_seconds: float | None = None
    runner: str | None = None
    runtime: dict[str, Any] | None = None
    pipeline_status: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Public result payload for one task."""

    tid: str
    status: str
    value: Any | None
    stdout: str | None
    stderr: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """Lifecycle or synthetic result event for one task."""

    tid: str
    event_type: str
    timestamp: int
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunExecutionResult:
    """Outcome of a shared run-path execution request."""

    tid: str
    status: str | None = None
    result_value: Any | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One queue-visible message."""

    queue: str
    message: str
    timestamp: int | None


@dataclass(frozen=True, slots=True)
class QueueInfo:
    """One queue listing row."""

    name: str
    messages: int
    total_messages: int | None = None
    claimed_messages: int | None = None
    is_endpoint: bool = False


@dataclass(frozen=True, slots=True)
class QueueWriteReceipt:
    """Outcome of a queue write."""

    queue: str
    message: str
    timestamp: int | None = None


@dataclass(frozen=True, slots=True)
class QueueMoveReceipt:
    """Outcome of a queue move."""

    source: str
    destination: str
    moved_count: int


@dataclass(frozen=True, slots=True)
class QueueDeleteReceipt:
    """Outcome of a queue delete."""

    queue: str | None
    deleted_count: int
    all_queues: bool = False


@dataclass(frozen=True, slots=True)
class QueueBroadcastReceipt:
    """Outcome of a queue broadcast."""

    pattern: str | None
    target_count: int


@dataclass(frozen=True, slots=True)
class QueueAliasRecord:
    """One queue alias record."""

    alias: str
    target: str


@dataclass(frozen=True, slots=True)
class EndpointResolution:
    """Resolved runtime endpoint metadata."""

    name: str
    tid: str
    status: str
    inbox: str
    outbox: str
    ctrl_in: str
    ctrl_out: str
    registered_at: int | None
    last_seen: int | None
    live_candidates: int
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ManagerSnapshot:
    """Public manager registry view."""

    tid: str
    status: str
    name: str
    pid: int | None
    timestamp: int | None
    role: str | None = None
    requests: str | None = None
    outbox: str | None = None
    ctrl_in: str | None = None
    ctrl_out: str | None = None


@dataclass(frozen=True, slots=True)
class SpecRecord:
    """Stored or builtin spec listing row."""

    spec_type: str
    name: str
    path: Path
    source: str


@dataclass(frozen=True, slots=True)
class SpecValidationResult:
    """Structured spec validation outcome."""

    valid: bool
    spec_type: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SystemStatusSnapshot:
    """Top-level project status view."""

    broker: dict[str, Any]
    managers: list[ManagerSnapshot]
    tasks: list[TaskSnapshot]


@dataclass(frozen=True, slots=True)
class SystemTidyResult:
    """Outcome of a broker tidy operation."""

    target: str


@dataclass(frozen=True, slots=True)
class SystemLoadResult:
    """Outcome of a system import operation."""

    imported: bool
    message: str
    aliases_created: int | None = None
    aliases_updated: int | None = None
    queues_created: int | None = None
    total_messages: int | None = None
