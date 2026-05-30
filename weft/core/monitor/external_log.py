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
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from weft._constants import (
    WEFT_LOG_TASKS_EXTERNAL_ROTATE_BACKUP_COUNT,
    WEFT_LOG_TASKS_EXTERNAL_ROTATE_MAX_BYTES,
    WEFT_LOG_TASKS_EXTERNAL_SCHEMA_VERSION,
    WEFT_LOG_TASKS_RAW_BODY_PREVIEW_BYTES,
)


class ExternalTaskLogError(RuntimeError):
    """Raised when required external task-log emission fails."""


class _RaisingRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler variant whose emit failures propagate to callers."""

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
    deferred_pending: int = 0
    last_deferred_error: str | None = None
    last_deferred_flush_at: int | None = None

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
            "deferred_pending": self.deferred_pending,
            "last_deferred_error": self.last_deferred_error,
            "last_deferred_flush_at": self.last_deferred_flush_at,
        }

    def with_deferred(
        self,
        *,
        pending: int,
        last_error: str | None,
        last_flush_at: int | None,
    ) -> ExternalTaskLogStatus:
        """Return a copy with cached Monitor deferred-write diagnostics."""

        return ExternalTaskLogStatus(
            enabled=self.enabled,
            mode=self.mode,
            path=self.path,
            healthy=self.healthy,
            last_error=self.last_error,
            last_emit_at=self.last_emit_at,
            last_emitted=self.last_emitted,
            last_blocked_deletions=self.last_blocked_deletions,
            total_emitted=self.total_emitted,
            total_blocked_deletions=self.total_blocked_deletions,
            deferred_pending=max(0, int(pending)),
            last_deferred_error=last_error,
            last_deferred_flush_at=last_flush_at,
        )


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
        self._lock = threading.RLock()
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

        with self._lock:
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

        return self.probe()

    def probe(self) -> bool:
        """Reopen the handler to validate current path writability."""

        with self._lock:
            self._close_handler_locked()
            try:
                self._ensure_handler_locked()
            except (OSError, RuntimeError, ValueError) as exc:
                self._healthy = False
                self._last_error = str(exc)
                return False
            self._healthy = True
            self._last_error = None
            return True

    def _close_handler_locked(self) -> None:
        handler = self._handler
        if handler is None:
            return
        try:
            handler.flush()
        finally:
            self._logger.removeHandler(handler)
            handler.close()
            self._handler = None

    def record_blocked_deletions(self, count: int) -> None:
        """Record rows whose deletion was blocked by external emit failure."""

        with self._lock:
            blocked = max(0, int(count))
            self._last_blocked_deletions = blocked
            self._total_blocked_deletions += blocked

    def reset_cycle_counts(self) -> None:
        """Reset per-cycle counters before a monitor cycle runs."""

        with self._lock:
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
        """Emit one collated task lifecycle summary for a terminal family."""

        record: dict[str, Any] = {
            "schema_version": WEFT_LOG_TASKS_EXTERNAL_SCHEMA_VERSION,
            "record_type": "task_log_collated",
            "monitor_tid": self._monitor_tid,
            "emitted_at_ns": int(emitted_at_ns),
            "close_reason": close_reason,
            "task": task_summary,
        }
        collation_kind = task_summary.get("collation_kind")
        if isinstance(collation_kind, str):
            record["collation_kind"] = collation_kind
        service_summary = task_summary.get("service")
        if isinstance(service_summary, Mapping):
            record["service"] = dict(service_summary)
        self._emit(record, emitted_at_ns=emitted_at_ns, level=logging.INFO)

    def emit_lifetime_report(
        self,
        report: Mapping[str, Any],
        *,
        emitted_at_ns: int,
    ) -> None:
        """Emit one task lifetime report JSONL record."""

        self._emit(dict(report), emitted_at_ns=emitted_at_ns, level=logging.INFO)

    def emit_json_text(
        self,
        body_json: str,
        *,
        emitted_at_ns: int,
    ) -> None:
        """Emit a pre-serialized JSONL record body."""

        self._emit_text(body_json, emitted_at_ns=emitted_at_ns, level=logging.INFO)

    def _ensure_handler(self) -> logging.Handler:
        with self._lock:
            return self._ensure_handler_locked()

    def _ensure_handler_locked(self) -> logging.Handler:
        if self._handler is not None:
            return self._handler
        if self._path.exists() and self._path.is_dir():
            raise ExternalTaskLogError(
                f"external task-log path is a directory: {self._path}"
            )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handler = _RaisingRotatingFileHandler(
            self._path,
            maxBytes=WEFT_LOG_TASKS_EXTERNAL_ROTATE_MAX_BYTES,
            backupCount=WEFT_LOG_TASKS_EXTERNAL_ROTATE_BACKUP_COUNT,
            encoding="utf-8",
        )
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
        with self._lock:
            try:
                handler = self._ensure_handler_locked()
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

    def _emit_text(
        self,
        body_json: str,
        *,
        emitted_at_ns: int,
        level: int,
    ) -> None:
        with self._lock:
            try:
                handler = self._ensure_handler_locked()
                self._logger.log(level, body_json)
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


def disabled_external_task_log_status(
    *, mode: str, path: str | None
) -> ExternalTaskLogStatus:
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
