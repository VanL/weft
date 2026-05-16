"""External task-log JSONL sink for TaskMonitor retention output.

This module owns only the external file logging boundary. It does not scan
queues, query Monitor tables, or decide deletion eligibility.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from weft._constants import (
    WEFT_LOG_TASKS_EXTERNAL_SCHEMA_VERSION,
    WEFT_LOG_TASKS_RAW_BODY_PREVIEW_BYTES,
)


class ExternalTaskLogError(RuntimeError):
    """Raised when required external task-log emission fails."""


class _RaisingFileHandler(logging.FileHandler):
    """FileHandler variant whose emit failures propagate to callers."""

    def handleError(self, record: logging.LogRecord) -> None:
        """Raise the active handler exception instead of swallowing it."""

        exc = sys.exc_info()[1]
        if exc is not None:
            raise exc
        super().handleError(record)


@dataclass(frozen=True, slots=True)
class ExternalTaskLogStatus:
    """Cached external task-log sink status for PONG and control snapshots."""

    enabled: bool
    mode: str
    path: str | None
    healthy: bool | None
    last_error: str | None = None
    last_emit_at: int | None = None
    last_emitted: int = 0
    last_blocked_deletions: int = 0
    total_emitted: int = 0
    total_blocked_deletions: int = 0

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe cached status summary."""

        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "path": self.path,
            "healthy": self.healthy,
            "last_error": self.last_error,
            "last_emit_at": self.last_emit_at,
            "last_emitted": self.last_emitted,
            "last_blocked_deletions": self.last_blocked_deletions,
            "total_emitted": self.total_emitted,
            "total_blocked_deletions": self.total_blocked_deletions,
        }


class ExternalTaskLogSink:
    """Small fail-closed JSONL sink backed by Python logging."""

    def __init__(self, *, path: Path, mode: str, monitor_tid: str) -> None:
        self._path = path
        self._mode = mode
        self._monitor_tid = monitor_tid
        digest = sha256(str(path).encode("utf-8")).hexdigest()[:16]
        self._logger = logging.getLogger(
            f"weft.monitor.external_task_log.{digest}.{monitor_tid}"
        )
        self._logger.handlers.clear()
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._handler: logging.Handler | None = None
        self._healthy: bool | None = None
        self._last_error: str | None = None
        self._last_emit_at: int | None = None
        self._last_emitted = 0
        self._last_blocked_deletions = 0
        self._total_emitted = 0
        self._total_blocked_deletions = 0

    @property
    def path(self) -> Path:
        """Resolved external JSONL path."""

        return self._path

    def status(self) -> ExternalTaskLogStatus:
        """Return the cached sink status without active I/O."""

        return ExternalTaskLogStatus(
            enabled=True,
            mode=self._mode,
            path=str(self._path),
            healthy=self._healthy,
            last_error=self._last_error,
            last_emit_at=self._last_emit_at,
            last_emitted=self._last_emitted,
            last_blocked_deletions=self._last_blocked_deletions,
            total_emitted=self._total_emitted,
            total_blocked_deletions=self._total_blocked_deletions,
        )

    def validate(self) -> bool:
        """Open/configure the handler and cache sink health."""

        try:
            self._ensure_handler()
        except (OSError, RuntimeError, ValueError) as exc:
            self._healthy = False
            self._last_error = str(exc)
            return False
        self._healthy = True
        self._last_error = None
        return True

    def record_blocked_deletions(self, count: int) -> None:
        """Record rows whose deletion was blocked by external emit failure."""

        blocked = max(0, int(count))
        self._last_blocked_deletions = blocked
        self._total_blocked_deletions += blocked

    def reset_cycle_counts(self) -> None:
        """Reset per-cycle counters before a monitor cycle runs."""

        self._last_emitted = 0
        self._last_blocked_deletions = 0

    def emit_raw(
        self,
        *,
        queue: str,
        message_id: int,
        emitted_at_ns: int,
        payload: dict[str, Any] | None,
        raw_body: str,
        malformed_reason: str | None,
    ) -> None:
        """Emit one raw ``weft.log.tasks`` row before exact deletion."""

        record: dict[str, Any] = {
            "schema_version": WEFT_LOG_TASKS_EXTERNAL_SCHEMA_VERSION,
            "record_type": "task_log_raw",
            "queue": queue,
            "message_id": int(message_id),
            "monitor_tid": self._monitor_tid,
            "emitted_at_ns": int(emitted_at_ns),
        }
        if payload is not None:
            record["payload"] = payload
        if malformed_reason is not None:
            record["malformed_reason"] = malformed_reason
            record["raw_body_preview"] = _bounded_preview(raw_body)
        self._emit(record, emitted_at_ns=emitted_at_ns, level=logging.DEBUG)

    def emit_collated(
        self,
        *,
        task_summary: dict[str, Any],
        emitted_at_ns: int,
        close_reason: str,
    ) -> None:
        """Emit one collated task lifecycle summary before raw-row deletion."""

        self._emit(
            {
                "schema_version": WEFT_LOG_TASKS_EXTERNAL_SCHEMA_VERSION,
                "record_type": "task_log_collated",
                "monitor_tid": self._monitor_tid,
                "emitted_at_ns": int(emitted_at_ns),
                "close_reason": close_reason,
                "task": task_summary,
            },
            emitted_at_ns=emitted_at_ns,
            level=logging.INFO,
        )

    def _ensure_handler(self) -> logging.Handler:
        if self._handler is not None:
            return self._handler
        if self._path.exists() and self._path.is_dir():
            raise ExternalTaskLogError(f"external task-log path is a directory: {self._path}")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handler = _RaisingFileHandler(self._path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)
        self._handler = handler
        return handler

    def _emit(
        self,
        record: dict[str, Any],
        *,
        emitted_at_ns: int,
        level: int,
    ) -> None:
        try:
            handler = self._ensure_handler()
            self._logger.log(level, json.dumps(record, sort_keys=True, default=str))
            handler.flush()
        except (OSError, RuntimeError, ValueError) as exc:
            self._healthy = False
            self._last_error = str(exc)
            raise ExternalTaskLogError(str(exc)) from exc
        self._healthy = True
        self._last_error = None
        self._last_emit_at = int(emitted_at_ns)
        self._last_emitted += 1
        self._total_emitted += 1


def disabled_external_task_log_status(*, mode: str, path: str | None) -> ExternalTaskLogStatus:
    """Return a cached status for disabled external logging."""

    return ExternalTaskLogStatus(
        enabled=False,
        mode=mode,
        path=path or None,
        healthy=None,
    )


def _bounded_preview(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= WEFT_LOG_TASKS_RAW_BODY_PREVIEW_BYTES:
        return value
    return encoded[:WEFT_LOG_TASKS_RAW_BODY_PREVIEW_BYTES].decode(
        "utf-8",
        errors="replace",
    )
