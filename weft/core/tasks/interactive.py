"""Interactive task mixin and helpers."""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from simplebroker import Queue
from weft._constants import CONTROL_STOP
from weft.core.targets import decode_work_message
from weft.core.taskspec import ReservedPolicy, TaskSpec

from .multiqueue_watcher import QueueMessageContext
from .runner import CommandSession, TaskRunner

logger = logging.getLogger(__name__)


class InteractiveTaskMixin(ABC):
    """Mixin providing interactive command session support."""

    _interactive_mode: bool
    _interactive_runner: TaskRunner | None
    _interactive_session: CommandSession | None
    _interactive_started: bool
    _interactive_completion_reported: bool
    _interactive_stdout_index: int
    _interactive_stderr_index: int
    _interactive_stdout_final_sent: bool
    _interactive_stderr_final_sent: bool
    _interactive_total_stdout_bytes: int
    _stop_event: threading.Event

    @property
    @abstractmethod
    def taskspec(self) -> TaskSpec:  # pragma: no cover - interface definition
        """Return the TaskSpec associated with this task."""

    @property
    @abstractmethod
    def _queue_names(self) -> dict[str, str]:  # pragma: no cover - interface definition
        """Return the queue-name mapping established by BaseTask."""

    @property
    @abstractmethod
    def _ctrl_out_queue(self) -> Queue:  # pragma: no cover - interface definition
        """Return the control-out queue handle."""

    @abstractmethod
    def _queue(self, name: str) -> Queue:  # pragma: no cover - interface definition
        """Lookup a managed SimpleBroker queue."""

    @abstractmethod
    def _get_reserved_queue(self) -> Queue:  # pragma: no cover - interface definition
        """Return the reserved queue handle."""

    @abstractmethod
    def _update_process_title(
        self, status: str, details: str | None = None
    ) -> None:  # pragma: no cover - interface definition
        """Update the process title for observability."""

    @abstractmethod
    def _report_state_change(
        self, event: str, **extra: Any
    ) -> None:  # pragma: no cover - interface definition
        """Publish a state-change event."""

    @abstractmethod
    def register_managed_pid(
        self, pid: int | None
    ) -> None:  # pragma: no cover - interface definition
        """Register a subprocess PID managed by the task."""

    @abstractmethod
    def _begin_streaming_session(
        self,
        *,
        mode: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:  # pragma: no cover - interface definition
        """Record that a streaming session has started."""

    @abstractmethod
    def _end_streaming_session(self) -> None:  # pragma: no cover - interface definition
        """Record that a streaming session has completed."""

    @abstractmethod
    def _apply_reserved_policy(
        self,
        policy: ReservedPolicy,
        message_timestamp: int | None = None,
    ) -> None:  # pragma: no cover - interface definition
        """Apply reserved-queue cleanup policy."""

    @abstractmethod
    def _ensure_reserved_empty(self) -> None:  # pragma: no cover - interface definition
        """Ensure the reserved queue does not retain stale work."""

    @abstractmethod
    def _cleanup_reserved_if_needed(
        self,
    ) -> None:  # pragma: no cover - interface definition
        """Perform post-policy reserved-queue cleanup."""

    @abstractmethod
    def _send_control_response(
        self,
        command: str,
        status: str,
        /,
        **extra: Any,
    ) -> None:  # pragma: no cover - interface definition
        """Publish a control response on the task control channel."""

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    def _init_interactive(self) -> None:
        """Initialize per-task interactive state."""
        self._interactive_mode = bool(getattr(self.taskspec.spec, "interactive", False))
        self._interactive_runner: TaskRunner | None = None
        self._interactive_session: CommandSession | None = None
        self._interactive_started = False
        self._interactive_completion_reported = False
        self._interactive_stdout_index = 0
        self._interactive_stderr_index = 0
        self._interactive_stdout_final_sent = False
        self._interactive_stderr_final_sent = False
        self._interactive_total_stdout_bytes = 0

    # ------------------------------------------------------------------
    # Interactive helpers
    # ------------------------------------------------------------------
    def _interactive_maybe_handle_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> bool:
        if not getattr(self, "_interactive_mode", False):
            return False

        session = self._interactive_ensure_session(timestamp)

        payload = decode_work_message(message)
        if isinstance(payload, dict):
            data = payload.get("stdin")
            if data is None and "payload" in payload:
                data = payload["payload"]
            close = bool(payload.get("close"))
        else:
            data = payload
            close = False

        if data is not None:
            session.send(str(data))

        if close:
            session.close_stdin()

        try:
            self._get_reserved_queue().delete(message_id=timestamp)
        except Exception:
            logger.debug(
                "Failed to acknowledge interactive message %s", timestamp, exc_info=True
            )

        self._interactive_flush_outputs()
        return True

    def _interactive_ensure_session(self, message_id: int) -> CommandSession:
        if self._interactive_session is not None:
            return self._interactive_session

        self.taskspec.mark_started()
        self._update_process_title("spawning")
        self._report_state_change(event="work_spawning", message_id=message_id)

        runner = TaskRunner(
            target_type=self.taskspec.spec.type,
            tid=self.taskspec.tid,
            function_target=self.taskspec.spec.function_target,
            process_target=self.taskspec.spec.process_target,
            agent=(
                self.taskspec.spec.agent.model_dump(mode="python")
                if self.taskspec.spec.agent is not None
                else None
            ),
            args=getattr(self.taskspec.spec, "args", None),
            kwargs=getattr(self.taskspec.spec, "keyword_args", None),
            env=self.taskspec.spec.env or {},
            working_dir=self.taskspec.spec.working_dir,
            timeout=self.taskspec.spec.timeout,
            limits=self.taskspec.spec.limits,
            monitor_class=getattr(
                self.taskspec.spec,
                "monitor_class",
                "weft.core.resource_monitor.ResourceMonitor",
            ),
            monitor_interval=self.taskspec.spec.polling_interval,
            db_path=getattr(self, "_db_path", None),
            config=getattr(self, "_config", None),
        )

        session = runner.start_session()
        self._interactive_runner = runner
        self._interactive_session = session
        self._interactive_started = True

        if session.pid is not None:
            self.register_managed_pid(session.pid)

        self.taskspec.mark_running(pid=session.pid)
        self._update_process_title("running")
        self._report_state_change(event="work_started", message_id=message_id)
        self._begin_streaming_session(
            mode="interactive",
            metadata={
                "session_pid": session.pid,
                "queue": self._queue_names.get("outbox"),
                "ctrl_queue": self._queue_names.get("ctrl_out"),
            },
        )
        return session

    def _interactive_flush_outputs(self) -> None:
        session_obj = getattr(self, "_interactive_session", None)
        if not session_obj:
            return
        session = session_obj

        outbox_queue = self._queue(self._queue_names["outbox"])
        self._interactive_emit_chunks(session, outbox_queue)

        ok, violation = session.poll_limits()
        if not ok and violation:
            self.taskspec.mark_killed(reason=violation)
            self._report_state_change(
                event="work_limit_violation",
                message_id=time.time_ns(),
                error=violation,
            )
            policy = self.taskspec.spec.reserved_policy_on_error
            self._apply_reserved_policy(policy)
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()
            self._update_process_title("killed", "limit")
            session.terminate()
            session.stop_monitor()
            self._interactive_finalize_session(failure_reason=violation)
            return

        metrics = session.last_metrics
        if metrics is not None:
            cpu_percent = int(round(metrics.cpu_percent))
            self.taskspec.update_metrics(
                memory=metrics.memory_mb,
                cpu=cpu_percent,
                fds=metrics.open_files,
                net_connections=metrics.connections,
            )

        if not session.is_alive():
            self._interactive_drain_remaining(session, outbox_queue)
            session.stop_monitor()
            self._interactive_finalize_session()

    def _interactive_emit_chunks(
        self, session: CommandSession, outbox_queue: Queue
    ) -> None:
        stdout_chunks = session.poll_stdout()
        for chunk in stdout_chunks:
            envelope = {
                "type": "stream",
                "stream": "stdout",
                "chunk": self._interactive_stdout_index,
                "final": False,
                "encoding": "text",
                "data": chunk,
            }
            outbox_queue.write(json.dumps(envelope))
            self._interactive_stdout_index += 1
            self._interactive_total_stdout_bytes += len(chunk)
            self._interactive_stdout_final_sent = False

        stderr_chunks = session.poll_stderr()
        for chunk in stderr_chunks:
            envelope = {
                "type": "stream",
                "stream": "stderr",
                "chunk": self._interactive_stderr_index,
                "final": False,
                "encoding": "text",
                "data": chunk,
            }
            self._ctrl_out_queue.write(json.dumps(envelope))
            self._interactive_stderr_index += 1
            self._interactive_stderr_final_sent = False

    def _interactive_drain_remaining(
        self, session: CommandSession, outbox_queue: Queue
    ) -> None:
        deadline = time.time() + 0.25
        while time.time() < deadline:
            self._interactive_emit_chunks(session, outbox_queue)
            if getattr(session, "_stdout_closed", False) and getattr(
                session, "_stderr_closed", False
            ):
                break
            time.sleep(0.01)

    def _interactive_finalize_session(self, failure_reason: str | None = None) -> None:
        if getattr(self, "_interactive_session", None) is None:
            return
        if getattr(self, "_interactive_completion_reported", False):
            return

        session = self._interactive_session
        if session is None:
            return
        returncode = session.returncode()

        current_status = self.taskspec.state.status
        terminal_override_allowed = current_status not in {
            "completed",
            "failed",
            "timeout",
            "cancelled",
            "killed",
        }

        if failure_reason == "cancelled":
            terminal_override_allowed = False

        if failure_reason not in (None, "cancelled") or (
            returncode is not None and returncode != 0
        ):
            if terminal_override_allowed:
                message = failure_reason or (
                    f"Interactive session exited with {returncode}"
                )
                self.taskspec.mark_failed(error=message, return_code=returncode)
                self._report_state_change(
                    event="work_failed",
                    message_id=time.time_ns(),
                    error=message,
                )
                policy = self.taskspec.spec.reserved_policy_on_error
                self._apply_reserved_policy(policy)
                if policy is not ReservedPolicy.KEEP:
                    self._ensure_reserved_empty()
                    self._cleanup_reserved_if_needed()
                self._update_process_title("failed")
        else:
            if terminal_override_allowed:
                rc = 0 if returncode is None else returncode
                self.taskspec.mark_completed(return_code=rc)
                self._report_state_change(
                    event="work_completed",
                    message_id=time.time_ns(),
                    result_bytes=self._interactive_total_stdout_bytes,
                )
                self._update_process_title("completed")

        outbox_queue = self._queue(self._queue_names["outbox"])
        if not self._interactive_stdout_final_sent:
            envelope = {
                "type": "stream",
                "stream": "stdout",
                "chunk": self._interactive_stdout_index,
                "final": True,
                "encoding": "text",
                "data": "",
            }
            outbox_queue.write(json.dumps(envelope))
            self._interactive_stdout_index += 1
            self._interactive_stdout_final_sent = True

        if not self._interactive_stderr_final_sent:
            envelope = {
                "type": "stream",
                "stream": "stderr",
                "chunk": self._interactive_stderr_index,
                "final": True,
                "encoding": "text",
                "data": "",
            }
            self._ctrl_out_queue.write(json.dumps(envelope))
            self._interactive_stderr_index += 1
            self._interactive_stderr_final_sent = True

        self.should_stop = True
        self._interactive_completion_reported = True
        self._interactive_session = None
        self._interactive_runner = None
        self._end_streaming_session()

    def _interactive_shutdown(self, *, reason: str | None = None) -> None:
        session_obj = getattr(self, "_interactive_session", None)
        if not session_obj:
            return
        session = session_obj
        try:
            session.close_stdin()
        except Exception:
            logger.debug("Failed to close interactive stdin", exc_info=True)
        deadline = time.time() + 2.0
        while session.is_alive() and time.time() < deadline:
            time.sleep(0.05)
            self._interactive_flush_outputs()
        if session.is_alive():
            session.terminate()
        session.stop_monitor()
        self._interactive_flush_outputs()
        self._interactive_finalize_session(failure_reason=reason)

    def _interactive_on_stop(self) -> None:
        if getattr(self, "_interactive_mode", False):
            self._interactive_shutdown()

    def _interactive_handle_control(self, command: str) -> bool:
        if not getattr(self, "_interactive_mode", False):
            return False

        if command == CONTROL_STOP:
            self.should_stop = True
            self.taskspec.mark_cancelled(reason="STOP command received")
            self._report_state_change(event="control_stop", message_id=time.time_ns())
            self._update_process_title("cancelled")
            policy = self.taskspec.spec.reserved_policy_on_stop
            self._apply_reserved_policy(policy)
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()
            if self._stop_event:
                self._stop_event.set()
            self._send_control_response("STOP", "ack")
            self._interactive_shutdown(reason="cancelled")
            return True
        return False


# ~
