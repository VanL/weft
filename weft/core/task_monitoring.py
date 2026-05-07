"""Task-monitor runtime config and processor contract.

This module contains the command-neutral pieces used by the supervised
``TaskMonitorTask``. It deliberately stays below the command and CLI layers:
processors receive typed candidates plus a context, not manager or command
objects.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3]
- docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from weft._constants import (
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
    WEFT_TASK_MONITOR_ENABLED_DEFAULT,
    WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT,
    WEFT_TASK_MONITOR_LOG_SINK_DEFAULT,
    WEFT_TASK_MONITOR_LOG_SINKS,
    WEFT_TASK_MONITOR_PROCESSOR_BUILTINS,
    WEFT_TASK_MONITOR_PROCESSOR_DEFAULT,
    WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
)
from weft.context import WeftContext


@dataclass(frozen=True, slots=True)
class TaskMonitorRuntimeConfig:
    """Typed config used by one supervised task-monitor process.

    Spec: [CC-2.3], [MF-5]
    """

    enabled: bool = WEFT_TASK_MONITOR_ENABLED_DEFAULT
    interval_seconds: int = WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT
    batch_size: int = WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT
    processor: str = WEFT_TASK_MONITOR_PROCESSOR_DEFAULT
    log_sink: str = WEFT_TASK_MONITOR_LOG_SINK_DEFAULT
    restart_backoff_seconds: float = WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> TaskMonitorRuntimeConfig:
        """Build typed runtime config from a loaded Weft config mapping."""

        enabled = bool(
            config.get("WEFT_TASK_MONITOR_ENABLED", WEFT_TASK_MONITOR_ENABLED_DEFAULT)
        )
        interval_seconds = int(
            config.get(
                "WEFT_TASK_MONITOR_INTERVAL_SECONDS",
                WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT,
            )
        )
        if interval_seconds < HEARTBEAT_MIN_INTERVAL_SECONDS:
            raise ValueError(
                "WEFT_TASK_MONITOR_INTERVAL_SECONDS must be at least "
                f"{HEARTBEAT_MIN_INTERVAL_SECONDS}"
            )

        batch_size = int(
            config.get(
                "WEFT_TASK_MONITOR_BATCH_SIZE",
                WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
            )
        )
        if batch_size <= 0:
            raise ValueError("WEFT_TASK_MONITOR_BATCH_SIZE must be positive")

        processor = str(
            config.get(
                "WEFT_TASK_MONITOR_PROCESSOR",
                WEFT_TASK_MONITOR_PROCESSOR_DEFAULT,
            )
        ).strip()
        if not processor:
            raise ValueError("WEFT_TASK_MONITOR_PROCESSOR must be non-empty")
        if (
            processor not in WEFT_TASK_MONITOR_PROCESSOR_BUILTINS
            and ":" not in processor
        ):
            raise ValueError(
                "WEFT_TASK_MONITOR_PROCESSOR must be a built-in processor name "
                "or a module:function reference"
            )

        log_sink = str(
            config.get("WEFT_TASK_MONITOR_LOG_SINK", WEFT_TASK_MONITOR_LOG_SINK_DEFAULT)
        ).strip()
        if log_sink not in WEFT_TASK_MONITOR_LOG_SINKS:
            allowed = ", ".join(sorted(WEFT_TASK_MONITOR_LOG_SINKS))
            raise ValueError(f"WEFT_TASK_MONITOR_LOG_SINK must be one of: {allowed}")

        restart_backoff_seconds = float(
            config.get(
                "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS",
                WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
            )
        )
        if restart_backoff_seconds <= 0:
            raise ValueError(
                "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS must be positive"
            )

        return cls(
            enabled=enabled,
            interval_seconds=interval_seconds,
            batch_size=batch_size,
            processor=processor,
            log_sink=log_sink,
            restart_backoff_seconds=restart_backoff_seconds,
        )


@dataclass(frozen=True, slots=True)
class TaskMonitorCandidate:
    """One task-monitor candidate row discovered from broker evidence."""

    candidate_id: str
    tid: str | None
    queue: str | None
    message_id: int | None
    candidate_class: str
    reason: str
    safe_to_delete: bool
    payload_sha256: str | None = None
    payload_size_bytes: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskMonitorProcessorRequest:
    """Input supplied to one task-monitor processor invocation."""

    context: WeftContext
    config: TaskMonitorRuntimeConfig
    cycle_id: str
    monitor_tid: str
    candidates: tuple[TaskMonitorCandidate, ...]
    now_ns: int


@dataclass(frozen=True, slots=True)
class TaskMonitorProcessorResult:
    """Result returned by a task-monitor processor."""

    success: bool
    processed: int = 0
    deleted: int = 0
    reported: int = 0
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class TaskMonitorProcessor(Protocol):
    """Callable protocol for task-monitor processors."""

    def __call__(
        self,
        request: TaskMonitorProcessorRequest,
    ) -> TaskMonitorProcessorResult:
        """Process one monitor cycle."""


def report_only_processor(
    request: TaskMonitorProcessorRequest,
) -> TaskMonitorProcessorResult:
    """Built-in non-destructive processor for phase 7 part 1."""

    candidate_count = len(request.candidates)
    return TaskMonitorProcessorResult(
        success=True,
        processed=candidate_count,
        reported=candidate_count,
    )


def _unsupported_destructive_processor(
    request: TaskMonitorProcessorRequest,
) -> TaskMonitorProcessorResult:
    """Return a fail-closed result for processors reserved for a later phase."""

    del request
    return TaskMonitorProcessorResult(
        success=False,
        errors=(
            "delete and jsonl_then_delete task-monitor processors are reserved "
            "until phase 7 part 3",
        ),
    )


def resolve_task_monitor_processor(name: str) -> TaskMonitorProcessor:
    """Resolve a built-in or ``module:function`` processor reference."""

    if name == "report_only":
        return report_only_processor
    if name in {"delete", "jsonl_then_delete"}:
        return _unsupported_destructive_processor

    if ":" not in name:
        raise ValueError(
            "task-monitor processor must be a built-in name or module:function"
        )
    module_name, function_name = name.split(":", 1)
    if not module_name or not function_name:
        raise ValueError("task-monitor processor reference is malformed")

    module = importlib.import_module(module_name)
    processor = getattr(module, function_name)
    if not callable(processor):
        raise TypeError(f"task-monitor processor {name!r} is not callable")
    return cast(TaskMonitorProcessor, processor)


def task_log_seen_candidate(
    *,
    queue_name: str,
    message: str,
    message_id: int,
) -> TaskMonitorCandidate | None:
    """Build a part-1 candidate from one task-log row.

    Returns ``None`` for rows that are not JSON objects with a task ID.
    """

    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    tid = payload.get("tid")
    if not isinstance(tid, str) or not tid:
        return None

    encoded = message.encode("utf-8", errors="replace")
    payload_sha256 = hashlib.sha256(encoded).hexdigest()
    candidate_class = "task_log_tid_seen"
    candidate_id = ":".join(
        (
            queue_name,
            str(message_id),
            candidate_class,
            payload_sha256,
        )
    )
    metadata: dict[str, Any] = {}
    event = payload.get("event")
    if isinstance(event, str):
        metadata["event"] = event
    status = payload.get("status")
    if isinstance(status, str):
        metadata["status"] = status

    return TaskMonitorCandidate(
        candidate_id=candidate_id,
        tid=tid,
        queue=queue_name,
        message_id=message_id,
        candidate_class=candidate_class,
        reason="task ID observed in task log",
        safe_to_delete=False,
        payload_sha256=payload_sha256,
        payload_size_bytes=len(encoded),
        metadata=metadata,
    )


__all__ = [
    "TaskMonitorCandidate",
    "TaskMonitorProcessor",
    "TaskMonitorProcessorRequest",
    "TaskMonitorProcessorResult",
    "TaskMonitorRuntimeConfig",
    "report_only_processor",
    "resolve_task_monitor_processor",
    "task_log_seen_candidate",
]
