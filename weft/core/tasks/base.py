"""Task implementations layered on top of the enhanced MultiQueueWatcher.

The classes in this module translate ``TaskSpec`` definitions into runnable
watchers that understand Weft's control messages, reserved-queue policies, and
logging/reporting conventions. Each concrete subclass specializes how work
messages are interpreted while sharing the queue wiring handled by
``BaseTask``.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2], [CC-2.3], [CC-2.4], [CC-2.5]
- docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3], [MF-5]
- docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1]
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from simplebroker import Queue
from weft._constants import (
    CONTROL_STOP,
    DEFAULT_OUTPUT_SIZE_LIMIT_MB,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    TASKSPEC_TID_SHORT_LENGTH,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    load_config,
)
from weft.core.taskspec import ReservedPolicy, TaskSpec
from weft.helpers import redact_taskspec_dump

from .multiqueue_watcher import MultiQueueWatcher, QueueMessageContext, QueueMode

logger = logging.getLogger(__name__)


STREAM_CHUNK_SIZE_BYTES = 512 * 1024


class BaseTask(MultiQueueWatcher, ABC):
    """Abstract base for task runtimes that share queue wiring and state tracking (Spec: [CC-2.2], [MF-2], [MF-3], [MF-5])."""

    # --- properties required by InteractiveTaskMixin ---------------------------------
    @property
    def taskspec(self) -> TaskSpec:
        return self._taskspec_value

    @taskspec.setter
    def taskspec(self, spec: TaskSpec) -> None:
        self._taskspec_value = spec

    @property
    def _queue_names(self) -> dict[str, str]:
        return self._queue_name_map

    @_queue_names.setter
    def _queue_names(self, mapping: dict[str, str]) -> None:
        self._queue_name_map = mapping

    @property
    def _ctrl_out_queue(self) -> Queue:
        return self._ctrl_out_queue_obj

    @_ctrl_out_queue.setter
    def _ctrl_out_queue(self, queue_obj: Queue) -> None:
        self._ctrl_out_queue_obj = queue_obj

    def _interactive_handle_control(self, command: str) -> bool:
        """Hook for subclasses supporting interactive mode; default is non-interactive."""
        parent = super()
        handler = getattr(parent, "_interactive_handle_control", None)
        if handler is None:
            return False
        return bool(cast(Callable[[str], bool], handler)(command))

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        stop_event: threading.Event | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize the task with queues, configuration, and bookkeeping state.

        Spec: [CC-2.2], [CC-2.5], [MF-2], [MF-5], [SB-0.1]
        """
        taskspec._validate_strict_requirements()

        self.taskspec = taskspec
        self.tid = taskspec.tid
        self.tid_short = self.tid[-TASKSPEC_TID_SHORT_LENGTH:]
        self.should_stop = False
        self._paused = False
        self._resource_monitor: Any | None = None
        self._spilled_output_dirs: set[Path] = set()
        self._last_poll_report_at = time.monotonic()
        self._last_reported_status: str | None = None

        self._queue_names = self._resolve_queue_names()
        queue_configs = self._build_queue_configs()

        config_dict = dict(config) if config is not None else load_config()
        self._config = dict(config_dict)
        redaction_setting = config_dict.get("WEFT_REDACT_TASKSPEC_FIELDS", "")
        self._taskspec_redaction_paths: tuple[str, ...] = tuple(
            part.strip() for part in str(redaction_setting).split(",") if part.strip()
        )
        self._queue_cache: dict[str, Queue] = {}
        self._owned_queue_names: set[str] = set()
        self._task_pid = os.getpid()
        self._caller_pid = os.getppid()
        self._managed_pids: set[int] = set()

        super().__init__(
            queue_configs=queue_configs,
            db=db,
            stop_event=stop_event,
            persistent=True,
            check_interval=1,
            config=config_dict,
        )

        # Pre-register canonical queues so they share the watcher's connection pool
        for queue_name in self._queue_names.values():
            self._queue(queue_name)

        # Ensure global observability queues reuse cached handles
        for global_name in (WEFT_GLOBAL_LOG_QUEUE, WEFT_TID_MAPPINGS_QUEUE):
            self._queue(global_name)

        self._ctrl_out_queue = self._queue(self._queue_names["ctrl_out"])

        # Cache for optional setproctitle module so we avoid repeated imports.
        self._setproctitle_module: Any | None = None

        self.enable_process_title = (
            bool(getattr(taskspec.spec, "enable_process_title", True))
            and self._check_setproctitle()
        )
        if self.enable_process_title:
            self._update_process_title("init")
        self._register_tid_mapping()
        self._report_state_change(event="task_initialized")

    # ------------------------------------------------------------------
    # Queue configuration
    # ------------------------------------------------------------------
    def _resolve_queue_names(self) -> dict[str, str]:
        """Map logical queue roles to concrete names derived from the TaskSpec.

        Spec: [CC-2.2], [MF-2], [MF-3]
        """
        tid_prefix = f"T{self.taskspec.tid}"

        inbox = (
            self.taskspec.io.inputs.get("inbox") or f"{tid_prefix}.{QUEUE_INBOX_SUFFIX}"
        )
        outbox = (
            self.taskspec.io.outputs.get("outbox")
            or f"{tid_prefix}.{QUEUE_OUTBOX_SUFFIX}"
        )
        ctrl_in = self.taskspec.io.control.get(
            "ctrl_in", f"{tid_prefix}.{QUEUE_CTRL_IN_SUFFIX}"
        )
        ctrl_out = self.taskspec.io.control.get(
            "ctrl_out", f"{tid_prefix}.{QUEUE_CTRL_OUT_SUFFIX}"
        )
        reserved = f"{tid_prefix}.{QUEUE_RESERVED_SUFFIX}"

        return {
            "inbox": inbox,
            "outbox": outbox,
            "ctrl_in": ctrl_in,
            "ctrl_out": ctrl_out,
            "reserved": reserved,
        }

    @abstractmethod
    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Return queue configuration dictionaries consumed by MultiQueueWatcher.

        Spec: [CC-2.1], [CC-2.2]
        """
        ...

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _queue(self, name: str) -> Queue:
        """Return a cached Queue bound to the shared SimpleBroker database.

        Spec: [SB-0.1], [SB-0.4]
        """
        if name in self._queue_cache:
            return self._queue_cache[name]

        managed = self.get_queue(name)
        if managed is not None:
            self._queue_cache[name] = managed
        else:
            queue_obj = Queue(
                name,
                db_path=self._db_path,
                persistent=True,
                config=self._config,
            )
            self._queue_cache[name] = queue_obj
            self._owned_queue_names.add(name)

        return self._queue_cache[name]

    def _outputs_base_dir(self) -> Path:
        spec_context = getattr(self.taskspec.spec, "weft_context", None)
        if spec_context:
            base = Path(spec_context).expanduser()
            return base / ".weft" / "outputs"
        return Path(tempfile.gettempdir()) / "weft" / "outputs"

    def _spill_large_output(self, encoded: bytes) -> dict[str, Any]:
        output_dir = self._outputs_base_dir() / self.tid
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "output.dat"
        output_path.write_bytes(encoded)

        sha = hashlib.sha256(encoded).hexdigest()
        preview = encoded[:1024].decode("utf-8", errors="replace")
        reference = {
            "type": "large_output",
            "path": str(output_path),
            "size": len(encoded),
            "size_mb": round(len(encoded) / (1024 * 1024), 2),
            "truncated_preview": preview,
            "sha256": sha,
            "message": (
                f"Output too large ({len(encoded)} bytes); saved to {output_path}"
            ),
        }
        self._spilled_output_dirs.add(output_dir)
        self._report_state_change(
            event="output_spilled",
            size_bytes=len(encoded),
            path=str(output_path),
            sha256=sha,
            preview=preview,
        )
        return reference

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """Close any cached Queue objects to release SQLite connections.

        Spec: [CC-2.5], [SB-0.1]
        """
        for name in list(self._owned_queue_names):
            queue = self._queue_cache.pop(name, None)
            if queue is None:
                continue
            try:
                queue.close()
            except Exception:
                logger.debug(
                    "Failed to close queue %s during cleanup", queue, exc_info=True
                )
        self._owned_queue_names.clear()
        self._queue_cache.clear()
        self._spilled_output_dirs.clear()

    def stop(self, *, join: bool = True, timeout: float = 2.0) -> None:
        """Stop the watcher and release any cached queue handles.

        Spec: [CC-2.5]
        """
        super().stop(join=join, timeout=timeout)
        self.cleanup()

    def __del__(self) -> None:  # pragma: no cover - best effort cleanup
        with contextlib.suppress(Exception):
            self.cleanup()

    def _get_reserved_queue(self) -> Queue:
        """Convenience accessor for the reserved queue used by reserve-mode processing.

        Spec: [MF-2]
        """
        return self._queue(self._queue_names["reserved"])

    def run_until_stopped(
        self,
        *,
        poll_interval: float = 0.05,
        max_iterations: int | None = None,
    ) -> None:
        """Repeatedly call :meth:`process_once` until the task stops.

        Args:
            poll_interval: Sleep duration between iterations to avoid busy loops.
            max_iterations: Optional safety cap, primarily for tests.

        Spec: [CC-2.5]
        """

        iterations = 0
        while not self.should_stop and not (
            self._stop_event and self._stop_event.is_set()
        ):
            self.process_once()
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            if poll_interval > 0:
                time.sleep(poll_interval)

    def process_once(self) -> None:
        """Process a single scheduling round across all queues.

        Spec: [CC-2.5], [MF-2]
        """
        self._drain_queue()
        self._maybe_emit_poll_report()

    def _maybe_emit_poll_report(self) -> None:
        """Emit a poll-based state report when reporting_interval == 'poll'.

        Spec: [CC-2.4], [MF-5], [RM-5]
        """
        if getattr(self.taskspec.spec, "reporting_interval", "transition") != "poll":
            return

        interval = getattr(self.taskspec.spec, "polling_interval", 1.0) or 1.0
        now = time.monotonic()
        if now - self._last_poll_report_at < interval:
            return

        summary = self.taskspec.to_log_dict()
        self._report_state_change(
            event="poll_report",
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Queue configuration helpers
    # ------------------------------------------------------------------
    def _reserve_queue_config(
        self,
        handler: Callable[[str, int, QueueMessageContext], None],
        reserved_queue: str,
        *,
        error_handler: Callable[[Exception, str, int], bool | None] | None = None,
    ) -> dict[str, Any]:
        """Build a reserve-mode queue configuration dictionary.

        Spec: [CC-2.1], [MF-2]
        """
        config: dict[str, Any] = {
            "handler": handler,
            "mode": QueueMode.RESERVE,
            "reserved_queue": reserved_queue,
        }
        if error_handler is not None:
            config["error_handler"] = error_handler
        return config

    def _peek_queue_config(
        self,
        handler: Callable[[str, int, QueueMessageContext], None],
        *,
        error_handler: Callable[[Exception, str, int], bool | None] | None = None,
    ) -> dict[str, Any]:
        """Build a peek-mode queue configuration dictionary.

        Spec: [CC-2.1], [MF-3]
        """
        config: dict[str, Any] = {
            "handler": handler,
            "mode": QueueMode.PEEK,
        }
        if error_handler is not None:
            config["error_handler"] = error_handler
        return config

    def _read_queue_config(
        self,
        handler: Callable[[str, int, QueueMessageContext], None],
        *,
        error_handler: Callable[[Exception, str, int], bool | None] | None = None,
    ) -> dict[str, Any]:
        """Build a read-mode queue configuration dictionary.

        Spec: [CC-2.1]
        """
        config: dict[str, Any] = {
            "handler": handler,
            "mode": QueueMode.READ,
        }
        if error_handler is not None:
            config["error_handler"] = error_handler
        return config

    # ------------------------------------------------------------------
    # Abstract hooks implemented by subclasses
    # ------------------------------------------------------------------
    @abstractmethod
    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Process a work message moved or peeked by the watcher.

        Spec: [CC-2.3], [MF-2]
        """
        ...

    def _handle_control_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """React to control messages and emit responses on the control-out queue.

        Spec: [CC-2.4], [MF-3]
        """
        command = message.strip().upper()

        if self._handle_control_command(command, context):
            self._ack_control_message(context.queue_name, context.timestamp)
            return

        self._report_state_change(
            event="control_unknown",
            message_id=timestamp,
            command=command,
        )
        self._send_control_response(command, "unknown", error="Unsupported command")
        self._ack_control_message(context.queue_name, context.timestamp)

    def _handle_control_command(
        self, command: str, context: QueueMessageContext
    ) -> bool:
        """Optional extension hook for subclasses to handle custom control commands.

        Returns:
            True if the command was handled and no further processing is required.

        Spec: [CC-2.4], [MF-3]
        """
        if self._interactive_handle_control(command):
            return True

        if command == "PING":
            self._send_control_response("PING", "ok", message="PONG")
            return True

        if command == CONTROL_STOP:
            self.should_stop = True
            self.taskspec.mark_cancelled(reason="STOP command received")
            self._report_state_change(
                event="control_stop", message_id=context.timestamp
            )
            self._update_process_title("cancelled")
            policy = self.taskspec.spec.reserved_policy_on_stop
            self._apply_reserved_policy(policy)
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()
            if self._stop_event:
                self._stop_event.set()
            self._send_control_response("STOP", "ack")
            return True

        if command == "PAUSE":
            if not self._paused:
                self._paused = True
                self._report_state_change(
                    event="control_pause", message_id=context.timestamp
                )
                self._update_process_title("paused")
            self._send_control_response("PAUSE", "ack", paused=True)
            return True

        if command == "RESUME":
            if self._paused:
                self._paused = False
                self._report_state_change(
                    event="control_resume", message_id=context.timestamp
                )
                self._update_process_title("running")
            self._send_control_response("RESUME", "ack", paused=False)
            return True

        if command == "STATUS":
            self._send_control_response(
                "STATUS",
                "ok",
                paused=self._paused,
                should_stop=self.should_stop,
                task_status=self.taskspec.state.status,
            )
            return True

        return False

    def _handle_reserved_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Emit debug logging for reserved-queue messages that remain unhandled.

        Spec: [MF-2]
        """
        logger.debug(
            "Reserved message for task %s id=%s body=%s",
            self.tid,
            timestamp,
            message[:200],
        )

    # ------------------------------------------------------------------
    # Logging and state utilities
    # ------------------------------------------------------------------
    def _report_state_change(self, event: str, **extra: Any) -> None:
        """Publish a state-change payload to the project log queue.

        Spec: [CC-2.4], [MF-5]
        """
        taskspec_dump = self.taskspec.model_dump(mode="json")
        payload = {
            "event": event,
            "tid": self.tid,
            "tid_short": self.tid_short,
            "status": self.taskspec.state.status,
            "timestamp": time.time_ns(),
            "taskspec": redact_taskspec_dump(
                taskspec_dump, self._taskspec_redaction_paths
            ),
        }
        payload.update(extra)
        payload.setdefault("task_pid", self._task_pid)
        payload.setdefault(
            "caller_pid", self._caller_pid if self._caller_pid > 0 else None
        )
        payload.setdefault("managed_pids", sorted(self._managed_pids))

        try:
            self._queue(WEFT_GLOBAL_LOG_QUEUE).write(json.dumps(payload))
        except Exception:
            logger.debug(
                "Failed to write state change event %s", payload, exc_info=True
            )
        self._last_poll_report_at = time.monotonic()
        self._last_reported_status = self.taskspec.state.status

    def _send_control_response(
        self, command: str, status: str, /, **extra: Any
    ) -> None:
        """Publish a control response on ``ctrl_out`` for observability/testing.

        Spec: [CC-2.4], [MF-3]
        """
        payload = {
            "command": command,
            "status": status,
            "tid": self.tid,
            "timestamp": time.time_ns(),
        }
        payload.update(extra)
        try:
            self._ctrl_out_queue.write(json.dumps(payload))
        except Exception:
            logger.debug("Failed to write control response %s", payload, exc_info=True)

    def _monitor_resource_usage(self) -> None:
        """Invoke a resource-monitor callback if one has been installed.

        Spec: [RM-5]
        """
        monitor = getattr(self, "_resource_monitor", None)
        if callable(monitor):
            try:
                monitor()
            except Exception:
                logger.debug("Resource monitor callback failed", exc_info=True)

    _process_title_warning_emitted = False

    def _check_setproctitle(self) -> bool:
        """Return True if setproctitle is available for process-title updates.

        Spec: [CC-2.4]
        """
        try:
            import setproctitle

            self._setproctitle_module = setproctitle
            return True
        except ImportError:
            self._setproctitle_module = None
            if not BaseTask._process_title_warning_emitted:
                logger.warning(
                    "Process titles disabled: optional dependency 'setproctitle' not "
                    "available for interpreter %s. Install setproctitle or set "
                    "spec.enable_process_title = False to silence this message.",
                    sys.executable,
                )
                BaseTask._process_title_warning_emitted = True
            return False

    def _update_process_title(self, status: str, details: str | None = None) -> None:
        """Update the OS process title when supported by the environment.

        Spec: [CC-2.4]
        """
        if not self.enable_process_title:
            return

        if self._setproctitle_module is None:
            try:
                import setproctitle

                self._setproctitle_module = setproctitle
            except ImportError:
                self.enable_process_title = False
                return

        try:
            title = self._format_process_title(status, details)
            assert self._setproctitle_module is not None  # typing guard
            self._setproctitle_module.setproctitle(title)
        except Exception:
            logger.debug("Failed to update process title", exc_info=True)

    def _format_process_title(self, status: str, details: str | None = None) -> str:
        """Generate a compact, safe-to-display process title fragment.

        Spec: [CC-2.4]
        """
        context_short = "proj"
        context_path: Path | None = None
        spec_context = getattr(self.taskspec.spec, "weft_context", None)
        if spec_context:
            context_path = Path(spec_context)
        else:
            try:
                db_path = Path(self._db_path)
                candidate_path = db_path.parent
                if candidate_path.name == ".weft":
                    candidate_path = candidate_path.parent
                context_path = candidate_path
            except Exception:
                context_path = None

        if context_path:
            context_label = context_path.name or context_path.stem
            context_label = context_label[:8]
            context_label = re.sub(r"[^a-zA-Z0-9_-]", "", context_label)
            if context_label:
                context_short = context_label

        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "", self.taskspec.name) or "task"
        parts = [
            f"weft-{context_short}-{self.tid_short}",
            safe_name[:20],
            status,
        ]
        if details:
            safe_details = re.sub(r"[^a-zA-Z0-9_-]", "", str(details))
            if safe_details:
                parts.append(safe_details[:15])
        return ":".join(parts)

    def _register_tid_mapping(self) -> None:
        """Register the task's short ID mapping for external observability tooling.

        Spec: [CC-2.4], [WA-2]
        """
        mapping = self._build_tid_mapping_payload()
        try:
            self._queue(WEFT_TID_MAPPINGS_QUEUE).write(json.dumps(mapping))
        except Exception:
            logger.debug("Failed to register TID mapping %s", mapping, exc_info=True)

    def register_managed_pid(self, pid: int | None) -> None:
        """Track a subprocess PID owned by this task for cleanup/observability."""
        if pid is None or pid <= 0:
            return
        if pid in (self._task_pid, self._caller_pid):
            return
        if pid in self._managed_pids:
            return
        self._managed_pids.add(pid)
        self._register_tid_mapping()

    def _build_tid_mapping_payload(self) -> dict[str, Any]:
        return {
            "short": self.tid_short,
            "full": self.tid,
            "pid": self._task_pid,
            "task_pid": self._task_pid,
            "caller_pid": self._caller_pid if self._caller_pid > 0 else None,
            "managed_pids": sorted(self._managed_pids),
            "name": self.taskspec.name,
            "started": time.time_ns(),
            "hostname": socket.gethostname(),
        }

    def _write_streaming_result(
        self, outbox_queue: Queue, data: bytes, limit_bytes: int
    ) -> int:
        """Chunk and stream result data to the outbox queue.

        Args:
            outbox_queue: Queue used for output messages.
            data: Result payload encoded as bytes.
            limit_bytes: Maximum payload size for a single queue message.

        Returns:
            Total number of bytes streamed.
        """
        if limit_bytes <= 0:
            limit_bytes = DEFAULT_OUTPUT_SIZE_LIMIT_MB * 1024 * 1024

        chunk_size = max(
            1,
            min(
                STREAM_CHUNK_SIZE_BYTES,
                limit_bytes // 2 if limit_bytes > 2 else limit_bytes,
            ),
        )

        total = len(data)
        if chunk_size >= total:
            chunks = [data]
        else:
            chunks = [data[i : i + chunk_size] for i in range(0, total, chunk_size)]

        for index, chunk in enumerate(chunks):
            envelope = {
                "type": "stream",
                "chunk": index,
                "final": index == len(chunks) - 1,
                "encoding": "base64",
                "size": len(chunk),
                "data": base64.b64encode(chunk).decode("ascii"),
            }
            outbox_queue.write(json.dumps(envelope))

        return total

    # ------------------------------------------------------------------
    # Reserved queue utilities
    # ------------------------------------------------------------------
    def _ensure_reserved_empty(self) -> None:
        """Drain reserved messages when policies expect an empty queue.

        Spec: [MF-2]
        """
        reserved_queue = self._get_reserved_queue()
        try:
            has_pending = reserved_queue.has_pending()
        except Exception:
            logger.debug("Failed to check reserved queue state", exc_info=True)
            return

        if not has_pending:
            return

        logger.warning("Reserved queue not empty for task %s", self.tid)
        while reserved_queue.read_many(64):
            continue

    def _ack_control_message(self, queue_name: str, timestamp: int) -> None:
        """Delete a processed control message, logging at debug level on failure.

        Spec: [MF-3]
        """
        try:
            self._queue(queue_name).delete(message_id=timestamp)
        except Exception:
            logger.debug(
                "Failed to acknowledge control message for %s",
                queue_name,
                exc_info=True,
            )

    def _apply_reserved_policy(
        self,
        policy: ReservedPolicy,
        message_timestamp: int | None = None,
    ) -> None:
        """Apply the ReservedPolicy configured on the TaskSpec to the reserved queue.

        Spec: [CC-2.4], [MF-2]
        """
        if policy is ReservedPolicy.KEEP:
            return

        reserved_queue = self._get_reserved_queue()

        if policy is ReservedPolicy.CLEAR:
            if message_timestamp is not None:
                try:
                    reserved_queue.delete(message_id=message_timestamp)
                except Exception:
                    logger.debug(
                        "Failed to clear reserved message %s",
                        message_timestamp,
                        exc_info=True,
                    )
            else:
                while reserved_queue.read_many(64):
                    continue
            return

        if policy is ReservedPolicy.REQUEUE:
            inbox_name = self._queue_names["inbox"]
            if message_timestamp is not None:
                try:
                    reserved_queue.move_one(
                        inbox_name,
                        exact_timestamp=message_timestamp,
                        require_unclaimed=True,
                        with_timestamps=False,
                    )
                    return
                except Exception:
                    logger.debug(
                        "Failed to requeue reserved message %s",
                        message_timestamp,
                        exc_info=True,
                    )
            self._move_reserved_to_inbox()

    def _move_reserved_to_inbox(self) -> None:
        """Move all reserved messages back into the inbox queue.

        Spec: [MF-2]
        """
        reserved_queue = self._get_reserved_queue()
        inbox_name = self._queue_names["inbox"]
        while True:
            try:
                batch = reserved_queue.move_many(
                    inbox_name,
                    limit=64,
                    with_timestamps=False,
                    require_unclaimed=True,
                )
            except Exception:
                logger.debug(
                    "Failed moving reserved messages back to inbox", exc_info=True
                )
                break

            if not batch:
                break

    def _cleanup_reserved_if_needed(self) -> None:
        """Remove reserved messages entirely when ``cleanup_on_exit`` is set.

        Spec: [CC-2.4], [MF-2]
        """
        if not getattr(self.taskspec.spec, "cleanup_on_exit", False):
            return

        reserved_queue = self._get_reserved_queue()
        while reserved_queue.read_many(64):
            continue

    def _cleanup_spilled_outputs_if_needed(self) -> None:
        """Remove spilled output directories when ``cleanup_on_exit`` is enabled.

        Spec: [CC-2.4], [MF-2]
        """
        if not getattr(self.taskspec.spec, "cleanup_on_exit", False):
            return

        for directory in list(self._spilled_output_dirs):
            try:
                if directory.exists():
                    shutil.rmtree(directory, ignore_errors=False)
            except Exception:
                logger.debug(
                    "Failed to remove spilled output directory %s",
                    directory,
                    exc_info=True,
                )
            finally:
                self._spilled_output_dirs.discard(directory)


# ~
