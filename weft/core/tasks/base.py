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
import signal
import socket
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from simplebroker import BrokerTarget, Queue
from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    DEFAULT_OUTPUT_SIZE_LIMIT_MB,
    PIPELINE_OWNER_METADATA_KEY,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    TASKSPEC_TID_SHORT_LENGTH,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    load_config,
)
from weft._runner_plugins import require_runner_plugin
from weft.core.taskspec import ReservedPolicy, TaskSpec
from weft.ext import RunnerHandle
from weft.helpers import (
    iter_queue_json_entries,
    kill_process_tree,
    redact_taskspec_dump,
    terminate_process_tree,
)

from .multiqueue_watcher import MultiQueueWatcher, QueueMessageContext, QueueMode

logger = logging.getLogger(__name__)


STREAM_CHUNK_SIZE_BYTES = 512 * 1024
TERMINAL_TASK_STATUSES = frozenset(
    {"completed", "failed", "timeout", "cancelled", "killed"}
)


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
        tid = taskspec.tid
        assert tid is not None
        self.tid: str = tid
        self.tid_short = self.tid[-TASKSPEC_TID_SHORT_LENGTH:]
        self.should_stop = False
        self._paused = False
        self._resource_monitor: Any | None = None
        self._spilled_output_dirs: set[Path] = set()
        self._last_poll_report_at = time.monotonic()
        self._last_reported_status: str | None = None
        self._activity: str | None = None
        self._waiting_on: str | None = None

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
        self._runtime_handle: RunnerHandle | None = None
        self._kill_requested = False
        self._external_stop_handled = False

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
        for global_name in (
            WEFT_GLOBAL_LOG_QUEUE,
            WEFT_TID_MAPPINGS_QUEUE,
            WEFT_STREAMING_SESSIONS_QUEUE,
        ):
            self._queue(global_name)

        self._ctrl_out_queue = self._queue(self._queue_names["ctrl_out"])
        self._streaming_session_info: dict[str, Any] | None = None
        self._streaming_session_message_id: int | None = None

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
            if hasattr(managed, "set_stop_event"):
                managed.set_stop_event(self._stop_event)
            self._queue_cache[name] = managed
        else:
            queue_obj = Queue(
                name,
                db_path=self._db_path,
                persistent=True,
                config=self._config,
            )
            if hasattr(queue_obj, "set_stop_event"):
                queue_obj.set_stop_event(self._stop_event)
            self._queue_cache[name] = queue_obj
            self._owned_queue_names.add(name)

        return self._queue_cache[name]

    def _get_connected_queue(self) -> Queue:
        """Return any queue object backed by this task's shared DB connection.

        Useful for helper logic (metrics, metadata lookups, timestamp checks)
        that only needs a live Queue handle without caring about which logical
        queue is used.
        """

        queue_name = self._queue_names.get("inbox")
        if not queue_name:
            try:
                queue_name = next(iter(self._queue_names.values()))
            except StopIteration as exc:  # pragma: no cover - construction bug guard
                raise RuntimeError("Task is missing configured queues") from exc
        return self._queue(queue_name)

    def _outputs_base_dir(self) -> Path:
        spec_context = getattr(self.taskspec.spec, "weft_context", None)
        if spec_context:
            base = Path(spec_context).expanduser()
            return base / ".weft" / "outputs"
        return Path(tempfile.gettempdir()) / "weft" / "outputs"

    def _spill_large_output(self, encoded: bytes) -> dict[str, Any]:
        assert self.tid is not None
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
        """Close cached Queue objects and release backend resources.

        Spec: [CC-2.5], [SB-0.1]
        """
        self._end_streaming_session()
        seen_queue_ids: set[int] = set()
        for queue in list(self._queue_cache.values()):
            queue_id = id(queue)
            if queue_id in seen_queue_ids:
                continue
            seen_queue_ids.add(queue_id)
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
            self._handle_stop_request(
                reason="STOP command received",
                event="control_stop",
                message_id=context.timestamp,
                apply_reserved_policy=True,
            )
            self._send_control_response("STOP", "ack")
            return True

        if command == CONTROL_KILL:
            self._handle_kill_request(
                reason="KILL command received",
                event="control_kill",
                message_id=context.timestamp,
                apply_reserved_policy=True,
            )
            self._send_control_response("KILL", "ack")
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
        if (
            self._activity is not None
            and self.taskspec.state.status not in TERMINAL_TASK_STATUSES
        ):
            payload["activity"] = self._activity
            if self._waiting_on is not None:
                payload["waiting_on"] = self._waiting_on
        payload.update(extra)
        payload.setdefault("task_pid", self._task_pid)
        payload.setdefault(
            "caller_pid", self._caller_pid if self._caller_pid > 0 else None
        )
        payload.setdefault("managed_pids", self._all_managed_pids())
        payload.setdefault("runner", self._current_runner_name())
        payload.setdefault(
            "runtime_handle",
            self._runtime_handle.to_dict()
            if self._runtime_handle is not None
            else None,
        )

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
        if (
            self._activity is not None
            and self.taskspec.state.status not in TERMINAL_TASK_STATUSES
        ):
            payload.setdefault("activity", self._activity)
            if self._waiting_on is not None:
                payload.setdefault("waiting_on", self._waiting_on)
        payload.update(extra)
        try:
            self._ctrl_out_queue.write(json.dumps(payload))
        except Exception:
            logger.debug("Failed to write control response %s", payload, exc_info=True)

    def handle_termination_signal(self, signum: int) -> None:
        """Handle an external termination signal inside a task process."""

        if self._external_stop_handled:
            return
        self._external_stop_handled = True

        signal_name = _signal_name(signum)
        sigusr1 = getattr(signal, "SIGUSR1", None)
        if sigusr1 is not None and signum == sigusr1:
            self._stop_registered_runtime_handle(timeout=0.2, graceful=False)
            for pid in sorted(self._managed_pids):
                kill_process_tree(pid, timeout=0.2)
            self._handle_kill_request(
                reason=f"{signal_name} received",
                event="task_signal_kill",
                message_id=None,
                apply_reserved_policy=False,
            )
            return

        self._stop_registered_runtime_handle(timeout=0.2, graceful=True)
        for pid in sorted(self._managed_pids):
            terminate_process_tree(pid, timeout=0.2)
        self._handle_stop_request(
            reason=f"{signal_name} received",
            event="task_signal_stop",
            message_id=None,
            apply_reserved_policy=False,
        )

    def _handle_stop_request(
        self,
        *,
        reason: str,
        event: str,
        message_id: int | None,
        apply_reserved_policy: bool,
    ) -> None:
        """Transition the task into a cancelled state and stop processing."""

        self.should_stop = True
        terminal_states = {"completed", "failed", "timeout", "cancelled", "killed"}
        if self.taskspec.state.status not in terminal_states:
            self.taskspec.mark_cancelled(reason=reason)
            self._clear_activity()
            self._report_state_change(event=event, message_id=message_id)
            self._emit_pipeline_terminal_event(
                status="cancelled",
                error=self.taskspec.state.error,
            )
            self._update_process_title("cancelled")

        if apply_reserved_policy:
            policy = self.taskspec.spec.reserved_policy_on_stop
            self._apply_reserved_policy(policy)
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()

        if self._stop_event:
            self._stop_event.set()

    def _handle_kill_request(
        self,
        *,
        reason: str,
        event: str,
        message_id: int | None,
        apply_reserved_policy: bool,
    ) -> None:
        """Transition the task into a killed state and stop processing."""

        self.should_stop = True
        self._kill_requested = True
        terminal_states = {"completed", "failed", "timeout", "cancelled", "killed"}
        if self.taskspec.state.status not in terminal_states:
            self.taskspec.mark_killed(reason=reason)
            self._clear_activity()
            self._report_state_change(event=event, message_id=message_id)
            self._emit_pipeline_terminal_event(
                status="killed",
                error=self.taskspec.state.error,
            )
            self._update_process_title("killed")

        if apply_reserved_policy:
            policy = self.taskspec.spec.reserved_policy_on_error
            self._apply_reserved_policy(policy)
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()

        if self._stop_event:
            self._stop_event.set()

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
            detail_value = details
            if (
                detail_value is None
                and self._activity is not None
                and status not in TERMINAL_TASK_STATUSES
                and self._activity != status
            ):
                detail_value = self._activity
            title = self._format_process_title(status, detail_value)
            assert self._setproctitle_module is not None  # typing guard
            self._setproctitle_module.setproctitle(title)
            setthreadtitle = getattr(self._setproctitle_module, "setthreadtitle", None)
            if callable(setthreadtitle):
                try:
                    setthreadtitle(title)
                except Exception:
                    logger.debug("Failed to update thread title", exc_info=True)
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
                if isinstance(self._db_path, BrokerTarget):
                    context_path = self._db_path.project_root
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

        Spec: [CC-2.4], [MA-2]
        """
        mapping = self._build_tid_mapping_payload()
        queue = self._queue(WEFT_TID_MAPPINGS_QUEUE)
        latest = self._latest_tid_mapping(queue, mapping["full"])
        if latest is not None:
            latest_payload, _ = latest
            if self._tid_mapping_equivalent(latest_payload, mapping):
                return

        try:
            queue.write(json.dumps(mapping))
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
        self._merge_runtime_handle_host_pid(pid)
        self._register_tid_mapping()

    def register_runtime_handle(self, handle: RunnerHandle | None) -> None:
        """Persist the runtime handle used to control the active runner."""
        if handle is None:
            return

        merged_handle = handle
        if handle.runner_name == "host" and self._managed_pids:
            merged_handle = RunnerHandle(
                runner_name=handle.runner_name,
                runtime_id=handle.runtime_id,
                host_pids=tuple(
                    sorted(set(handle.host_pids).union(self._managed_pids))
                ),
                metadata=dict(handle.metadata),
            )

        if self._runtime_handle == merged_handle:
            return
        self._runtime_handle = merged_handle
        self._register_tid_mapping()

    def _build_tid_mapping_payload(self) -> dict[str, Any]:
        runtime_handle = self._runtime_handle
        role = self.taskspec.metadata.get("role")
        payload = {
            "short": self.tid_short,
            "full": self.tid,
            "pid": self._task_pid,
            "task_pid": self._task_pid,
            "caller_pid": self._caller_pid if self._caller_pid > 0 else None,
            "managed_pids": self._all_managed_pids(),
            "runner": self._current_runner_name(),
            "runtime_handle": (
                runtime_handle.to_dict() if runtime_handle is not None else None
            ),
            "name": self.taskspec.name,
            "role": role if isinstance(role, str) and role else None,
            "started": time.time_ns(),
            "hostname": socket.gethostname(),
        }
        if (
            self._activity is not None
            and self.taskspec.state.status not in TERMINAL_TASK_STATUSES
        ):
            payload["activity"] = self._activity
            if self._waiting_on is not None:
                payload["waiting_on"] = self._waiting_on
        return payload

    def _set_activity(
        self,
        activity: str | None,
        *,
        waiting_on: str | None = None,
    ) -> None:
        """Update live activity without changing durable task lifecycle state."""

        normalized_activity = activity.strip() if isinstance(activity, str) else None
        normalized_waiting_on = (
            waiting_on.strip()
            if isinstance(waiting_on, str) and waiting_on.strip()
            else None
        )
        if self.taskspec.state.status in TERMINAL_TASK_STATUSES:
            normalized_activity = None
            normalized_waiting_on = None
        if (
            normalized_activity == self._activity
            and normalized_waiting_on == self._waiting_on
        ):
            return
        self._activity = normalized_activity
        self._waiting_on = normalized_waiting_on
        self._emit_activity_event()
        self._register_tid_mapping()
        self._update_process_title(self.taskspec.state.status)

    def _clear_activity(self) -> None:
        self._set_activity(None)

    def _emit_activity_event(self) -> None:
        payload = {
            "event": "task_activity",
            "tid": self.tid,
            "tid_short": self.tid_short,
            "status": self.taskspec.state.status,
            "timestamp": time.time_ns(),
        }
        if self._activity is not None:
            payload["activity"] = self._activity
        if self._waiting_on is not None:
            payload["waiting_on"] = self._waiting_on
        try:
            self._queue(WEFT_GLOBAL_LOG_QUEUE).write(json.dumps(payload))
        except Exception:
            logger.debug(
                "Failed to write task activity event %s", payload, exc_info=True
            )

    def _pipeline_owner_config(self) -> dict[str, Any] | None:
        payload = self.taskspec.metadata.get(PIPELINE_OWNER_METADATA_KEY)
        return payload if isinstance(payload, dict) else None

    def _emit_pipeline_owner_event(self, event_type: str, **extra: Any) -> None:
        owner = self._pipeline_owner_config()
        if owner is None:
            return
        events_queue = owner.get("events_queue")
        pipeline_tid = owner.get("pipeline_tid")
        if not isinstance(events_queue, str) or not isinstance(pipeline_tid, str):
            return
        payload = {
            "type": event_type,
            "pipeline_tid": pipeline_tid,
            "child_tid": self.tid,
            "timestamp": time.time_ns(),
        }
        payload.update(extra)
        try:
            self._queue(events_queue).write(json.dumps(payload))
        except Exception:
            logger.debug(
                "Failed to write pipeline owner event %s",
                payload,
                exc_info=True,
            )

    def _emit_pipeline_started_event(self) -> None:
        owner = self._pipeline_owner_config()
        if owner is None:
            return

        payload: dict[str, Any] = {
            "status": self.taskspec.state.status,
        }
        if self._activity is not None:
            payload["activity"] = self._activity
        if self._waiting_on is not None:
            payload["waiting_on"] = self._waiting_on

        role = owner.get("role")
        if role == "pipeline_stage":
            payload["stage_name"] = owner.get("stage_name")
            self._emit_pipeline_owner_event("stage_started", **payload)
            return
        if role == "pipeline_edge":
            payload["edge_name"] = owner.get("edge_name")
            self._emit_pipeline_owner_event("edge_started", **payload)

    def _emit_pipeline_terminal_event(
        self,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        owner = self._pipeline_owner_config()
        if owner is None:
            return

        role = owner.get("role")
        if role == "pipeline_stage":
            payload: dict[str, Any] = {
                "stage_name": owner.get("stage_name"),
                "status": status,
            }
            if error:
                payload["error"] = error
            self._emit_pipeline_owner_event("stage_terminal", **payload)
            return

        if role == "pipeline_edge":
            payload = {
                "edge_name": owner.get("edge_name"),
                "status": status,
            }
            if error:
                payload["error"] = error
            self._emit_pipeline_owner_event("edge_terminal", **payload)

    def _locate_streaming_session(self, queue: Queue, session_id: str) -> int | None:
        """Look up the message id for an active streaming record (Spec: [CC-2.4])."""
        for payload, ts in iter_queue_json_entries(queue):
            if payload.get("session_id") == session_id:
                return int(ts)
        return None

    def _begin_streaming_session(
        self,
        *,
        mode: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Record the start of a streaming session (Spec: [CC-2.4])."""
        if self._streaming_session_info is not None:
            return

        queue = self._queue(WEFT_STREAMING_SESSIONS_QUEUE)
        queue_name = metadata.get("queue") if metadata else None
        assert self.tid is not None
        identifier_parts: list[str] = [self.tid]
        if queue_name:
            identifier_parts.append(queue_name)
        started_at = time.time_ns()
        identifier_parts.append(str(started_at))
        session_id = ":".join(identifier_parts)

        payload: dict[str, Any] = {
            "session_id": session_id,
            "tid": self.tid,
            "mode": mode,
            "started_at": started_at,
        }
        if metadata:
            payload.update(metadata)

        try:
            queue.write(json.dumps(payload, ensure_ascii=False))
        except Exception:
            logger.debug("Failed to register streaming session", exc_info=True)
            return

        message_id = self._locate_streaming_session(queue, session_id)
        self._streaming_session_info = payload
        self._streaming_session_message_id = message_id

    def _end_streaming_session(self) -> None:
        """Remove any streaming session marker once streaming completes (Spec: [CC-2.4])."""
        if self._streaming_session_info is None:
            return

        queue = self._queue(WEFT_STREAMING_SESSIONS_QUEUE)
        session_id = self._streaming_session_info.get("session_id")
        message_id = self._streaming_session_message_id
        if message_id is None and session_id:
            message_id = self._locate_streaming_session(queue, session_id)

        if message_id is not None:
            try:
                queue.delete(message_id=message_id)
            except Exception:
                logger.debug(
                    "Failed to clear streaming session %s",
                    message_id,
                    exc_info=True,
                )

        self._streaming_session_info = None
        self._streaming_session_message_id = None

    def _latest_tid_mapping(
        self, queue: Queue, full_tid: str
    ) -> tuple[dict[str, Any], int] | None:
        """Return the most recent mapping entry for ``full_tid`` if present."""
        latest: tuple[dict[str, Any], int] | None = None
        try:
            generator = queue.peek_generator(with_timestamps=True)
        except Exception:
            return None

        for entry in generator:
            if not isinstance(entry, tuple) or len(entry) != 2:
                continue
            body, timestamp = entry
            if not isinstance(timestamp, int):
                continue
            try:
                payload = json.loads(body)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("full") == full_tid:
                latest = (payload, timestamp)
        return latest

    @staticmethod
    def _tid_mapping_equivalent(
        current: Mapping[str, Any], incoming: Mapping[str, Any]
    ) -> bool:
        """Return True if ``incoming`` does not change any observable fields."""
        comparable_keys = {
            "short",
            "full",
            "pid",
            "task_pid",
            "caller_pid",
            "managed_pids",
            "runner",
            "runtime_handle",
            "name",
            "role",
            "hostname",
        }
        for key in comparable_keys:
            if current.get(key) != incoming.get(key):
                return False
        return True

    def _all_managed_pids(self) -> list[int]:
        runtime_pids: tuple[int, ...] = ()
        if self._runtime_handle is not None:
            runtime_pids = self._runtime_handle.host_pids
        return sorted(set(self._managed_pids).union(runtime_pids))

    def _current_runner_name(self) -> str:
        if self._runtime_handle is not None:
            return self._runtime_handle.runner_name
        runner = getattr(self.taskspec.spec, "runner", None)
        name = getattr(runner, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return "host"

    def _merge_runtime_handle_host_pid(self, pid: int) -> None:
        if self._runtime_handle is None or self._runtime_handle.runner_name != "host":
            return
        if pid in self._runtime_handle.host_pids:
            return
        self._runtime_handle = RunnerHandle(
            runner_name=self._runtime_handle.runner_name,
            runtime_id=self._runtime_handle.runtime_id,
            host_pids=tuple(sorted(set(self._runtime_handle.host_pids).union({pid}))),
            metadata=dict(self._runtime_handle.metadata),
        )

    def _stop_registered_runtime_handle(
        self,
        *,
        timeout: float,
        graceful: bool,
    ) -> None:
        handle = self._runtime_handle
        if handle is None:
            return
        try:
            plugin = require_runner_plugin(handle.runner_name)
            if graceful:
                plugin.stop(handle, timeout=timeout)
            else:
                plugin.kill(handle, timeout=timeout)
        except Exception:
            logger.debug("Failed to stop runtime handle %s", handle, exc_info=True)

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

        self._begin_streaming_session(
            mode="stream",
            metadata={
                "chunks": len(chunks),
                "bytes": total,
                "queue": outbox_queue.name,
            },
        )

        try:
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
        except Exception:
            self._end_streaming_session()
            raise

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

        Spec: [CC-2.4], [MF-2], [TS-1.1]
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


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return f"signal {signum}"


# ~
