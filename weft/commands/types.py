"""Shared runtime dataclasses for ops and client surfaces.

Spec references:
- docs/specifications/09-Implementation_Plan.md [IP-1.1]
- docs/specifications/10-CLI_Interface.md [CLI-1], [CLI-4], [CLI-6]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from weft.core.task_evidence import QueueAckTarget, TaskTerminalSnapshot

__all__ = [
    "EndpointResolution",
    "ManagerSnapshot",
    "PreparedSubmissionRequest",
    "QueueAckTarget",
    "QueueEntry",
    "RunExecutionResult",
    "ServiceSnapshot",
    "SpecRecord",
    "SpecValidationResult",
    "SubmittedTaskReceipt",
    "SystemLoadResult",
    "SystemStatusSnapshot",
    "SystemTidyResult",
    "TaskEvent",
    "TaskResult",
    "TaskSnapshot",
    "TaskTerminalSnapshot",
]


@dataclass(frozen=True, slots=True)
class SubmittedTaskReceipt:
    """Internal submission receipt shared by ops and tests."""

    tid: str
    name: str
    submitted_at_ns: int


@dataclass(frozen=True, slots=True)
class InitResult:
    """Outcome of project initialization. Spec: [PY-2]."""

    root: Path
    config_path: Path
    created: bool


@dataclass(frozen=True, slots=True)
class RunSpecDescription:
    """Resolved dynamic help metadata for one stored spec."""

    reference: str
    usage: str
    arguments: tuple[Mapping[str, Any], ...]
    stdin: Mapping[str, Any] | None


class CommandStream[T](Iterator[T], Protocol):
    """Closable structured command event stream."""

    def close(self) -> None: ...


class RunSession(Protocol):
    """Interactive or waiting run session returned by `cmd_run`."""

    tid: str

    def events(self) -> CommandStream[TaskEvent]: ...
    def send_input(self, text: str) -> None: ...
    def close_input(self) -> None: ...
    def stop(self) -> TaskControlResult: ...
    def wait(self, timeout: float | None = None) -> RunExecutionResult: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PreparedSubmissionRequest:
    """Validated, immutable-enough queue submission request.

    The `taskspec` field intentionally stays typed as `Any` so the shared
    command type module does not import core TaskSpec models.
    """

    name: str
    taskspec: Any
    payload: Any | None
    seed_start_envelope: bool = True
    allow_internal_runtime: bool = False


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
    reconciliation: dict[str, Any] | None = None
    runner_diagnostics: dict[str, Any] | None = None
    host_pids: tuple[int, ...] | None = None
    managed_pids: tuple[int, ...] | None = None
    live_managed_pids: tuple[int, ...] | None = None


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
class ServiceSnapshot:
    """Public current-state view for one manager-owned service."""

    key: str
    name: str
    desired: bool
    enabled: bool
    status: str
    evidence: str
    tid: str | None = None
    manager_tid: str | None = None
    queue: str | None = None
    pid: int | None = None
    updated_at: int | None = None
    reconciliation: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None


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
    submission_error: str | None = None
    error_prefix: str = "Error executing task"
    submitted_payload: dict[str, Any] | None = None
    manager_started_payload: dict[str, Any] | None = None


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
class QueueMoveResult:
    """Lossless outcome of a queue move."""

    source: str
    destination: str
    entries: tuple[QueueEntry, ...]
    moved_count: int


@dataclass(frozen=True, slots=True)
class QueueDeleteReceipt:
    """Outcome of a queue delete."""

    queue: str | None
    deleted_count: int
    queues_deleted: int = 0
    all_queues: bool = False
    exact_message: str | None = None


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
    runtime_handle: dict[str, Any] | None
    timestamp: int | None
    role: str | None = None
    requests: str | None = None
    internal_requests: str | None = None
    internal_reserved: str | None = None
    outbox: str | None = None
    ctrl_in: str | None = None
    ctrl_out: str | None = None
    liveness: Literal["live", "stale", "unknown", "non_live"] | None = None
    proof_source: str | None = None
    proof_detail: str | None = None
    dispatch_eligible: bool | None = None
    canonical_candidate: bool | None = None
    canonical: bool | None = None


@dataclass(frozen=True, slots=True)
class SpecRecord:
    """Stored or builtin spec listing row."""

    spec_type: str
    name: str
    path: Path
    source: str
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SpecValidationResult:
    """Structured spec validation outcome."""

    valid: bool
    spec_type: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    errors_by_stage: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SystemStatusSnapshot:
    """Top-level project status view."""

    broker: dict[str, Any]
    managers: list[ManagerSnapshot]
    tasks: list[TaskSnapshot]
    services: list[ServiceSnapshot] = field(default_factory=list)


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


@dataclass(frozen=True, slots=True)
class TaskPingResult:
    """Structured task PING observation."""

    tid: str
    acknowledged: bool
    timed_out: bool
    error: str | None
    observed_at: int | None
    pong: Mapping[str, Any] | None
    snapshot: TaskSnapshot | None


@dataclass(frozen=True, slots=True)
class TaskControlResult:
    """Structured stop/kill outcome."""

    command: Literal["stop", "kill"]
    requested: tuple[str, ...]
    accepted: tuple[str, ...]
    snapshots: tuple[TaskSnapshot, ...]


@dataclass(frozen=True, slots=True)
class SpecMutationResult:
    """Structured stored-spec mutation outcome."""

    action: Literal["create", "delete"]
    record: SpecRecord


@dataclass(frozen=True, slots=True)
class SystemDumpResult:
    path: Path
    queues: int
    messages: int
    aliases: int
    omitted_claimed_queues: int
    omitted_claimed_messages: int


@dataclass(frozen=True, slots=True)
class SystemPruneResult:
    families: tuple[str, ...]
    applied: bool
    candidates: int
    deleted: int
    failed: int
    details: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class BuiltinSpecRecord:
    name: str
    description: str | None
    category: str | None
    function_target: str | None
    supported_platforms: tuple[str, ...]
    path: Path
    source: str = "builtin"
