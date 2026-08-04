"""Session helpers for interactive commands and persistent agent runtimes.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2], [CC-3.5]
- docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1], [RM-5.2]
- docs/specifications/07-System_Invariants.md [EXEC.5]-[EXEC.10]
- docs/specifications/13-Agent_Runtime.md [AR-6], [AR-9]
"""

from __future__ import annotations

import contextlib
import queue
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue as MPQueue
from typing import Any, cast

from weft._constants import (
    ACTIVE_CONTROL_POLL_INTERVAL,
    COMMAND_SESSION_POST_TERMINATION_WAIT,
    COMMAND_SESSION_TERMINATION_TIMEOUT,
    TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS,
)
from weft.core.resource_monitor import (
    BaseResourceMonitor,
    ResourceMetrics,
)
from weft.core.runner_diagnostics import diagnostic_summary, runner_diagnostics
from weft.core.tasks.agent_session_protocol import (
    is_booted_response,
    is_ready_response,
    make_execute_request,
    make_stop_request,
    parse_result_response,
    response_type,
    startup_error_diagnostics,
    startup_error_message,
)
from weft.core.terminal_handoff import (
    TerminalHandoffEvent,
    TerminalHandoffProgress,
    TerminalHandoffStep,
    drive_terminal_handoff_turn,
)
from weft.core.terminal_handoff_transport import (
    TerminalHandoffTransportError,
    receive_terminal_payload,
)
from weft.ext import RunnerHandle
from weft.helpers import safe_cancel, terminate_process_tree


class CommandSession:
    """Interactive command execution session supporting stdin/stdout streaming."""

    _READ_SIZE = 64 * 1024

    def __init__(
        self,
        process: subprocess.Popen[Any],
        stdout_queue: queue.Queue[str | None],
        stderr_queue: queue.Queue[str | None],
        monitor: BaseResourceMonitor | None,
        limits: Any,
        *,
        handle: RunnerHandle | None = None,
        stdin_writer: Callable[[str], None] | None = None,
        stdin_closer: Callable[[], None] | None = None,
        cleanup_callback: Callable[[], None] | None = None,
    ) -> None:
        self._process = process
        self._stdout_queue = stdout_queue
        self._stderr_queue = stderr_queue
        self._monitor: BaseResourceMonitor | None = monitor
        self._limits = limits
        self._handle = handle
        self._stdin_writer = stdin_writer
        self._stdin_closer = stdin_closer
        self._cleanup_callback = cleanup_callback
        self._last_metrics: ResourceMetrics | None = None
        self._stdout_closed = False
        self._stderr_closed = False
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._process.pid

    @property
    def handle(self) -> RunnerHandle | None:
        return self._handle

    def send(self, data: str) -> None:
        if self._stdin_writer is not None:
            self._stdin_writer(data)
            return
        if self._process.stdin is None:
            raise RuntimeError("Session stdin is not available")
        self._process.stdin.write(data)
        self._process.stdin.flush()

    def close_stdin(self) -> None:
        if self._stdin_closer is not None:
            self._stdin_closer()
            return
        stdin = self._process.stdin
        if stdin and not stdin.closed:
            stdin.close()

    def _drain_queue(self, q: queue.Queue[str | None], closed_flag: str) -> list[str]:
        chunks: list[str] = []
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                break
            if item is None:
                setattr(self, closed_flag, True)
                continue
            chunks.append(item)
        return chunks

    def poll_stdout(self) -> list[str]:
        return self._drain_queue(self._stdout_queue, "_stdout_closed")

    def poll_stderr(self) -> list[str]:
        return self._drain_queue(self._stderr_queue, "_stderr_closed")

    def is_alive(self) -> bool:
        return self._process.poll() is None

    def returncode(self) -> int | None:
        return self._process.poll()

    def terminate(self, *, deadline: float | None = None) -> None:
        if deadline is not None:
            try:
                if self.is_alive():
                    pid = self._process.pid
                    remaining = max(0.0, deadline - time.monotonic())
                    if isinstance(pid, int) and pid > 0:
                        terminate_process_tree(
                            pid,
                            timeout=remaining / 2.0 if remaining > 0 else 0.0,
                            kill_after=True,
                        )
                    if self.is_alive():
                        self._process.kill()
            finally:
                self.close()
            return

        try:
            if self.is_alive():
                pid = self._process.pid
                if isinstance(pid, int) and pid > 0:
                    terminate_process_tree(
                        pid,
                        timeout=COMMAND_SESSION_TERMINATION_TIMEOUT,
                    )
                try:
                    self._process.wait(timeout=COMMAND_SESSION_POST_TERMINATION_WAIT)
                except subprocess.TimeoutExpired:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=COMMAND_SESSION_TERMINATION_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._cleanup_callback is None:
            return
        try:
            self._cleanup_callback()
        except Exception:  # pragma: no cover - defensive
            pass

    def poll_limits(self) -> tuple[bool, str | None]:
        if not self._monitor:
            return True, None
        try:
            ok, violation = self._monitor.check_limits(self._limits)
            self._last_metrics = self._monitor.last_metrics() or self._last_metrics
            return ok, violation
        except Exception:  # pragma: no cover - process may have exited
            self.stop_monitor()
            return True, None

    def stop_monitor(self) -> None:
        if self._monitor:
            try:
                self._last_metrics = self._monitor.last_metrics() or self._last_metrics
            except Exception:  # pragma: no cover - process may have exited
                pass
            try:
                self._monitor.stop()
            except Exception:  # pragma: no cover - defensive
                pass
            self._monitor = None

    @property
    def last_metrics(self) -> ResourceMetrics | None:
        return self._last_metrics


@dataclass(slots=True)
class SessionExecutionResult:
    """Result envelope returned by a long-lived session worker."""

    status: str
    value: Any | None
    error: str | None
    metrics: ResourceMetrics | None = None
    diagnostics: dict[str, Any] | None = None


class AgentSession:
    """Managed long-lived agent worker session."""

    def __init__(
        self,
        process: BaseProcess,
        request_queue: MPQueue[Any],
        response_receiver: Connection,
        monitor: BaseResourceMonitor | None,
        limits: Any,
        *,
        timeout: float | None,
        handle: RunnerHandle | None = None,
    ) -> None:
        self._process = process
        self._request_queue = request_queue
        self._response_receiver = response_receiver
        self._monitor = monitor
        self._limits = limits
        self._timeout = timeout
        self._handle = handle
        self._last_metrics: ResourceMetrics | None = None
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._process.pid

    @property
    def handle(self) -> RunnerHandle | None:
        return self._handle

    def wait_ready(self, *, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        last_handshake: str | None = None
        channel_sealed = False
        start = time.monotonic()
        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0.01)
            try:
                payload = self._read_response_payload(timeout=remaining)
            except EOFError:
                channel_sealed = True
                break
            except TerminalHandoffTransportError as exc:
                raise RuntimeError(
                    self._format_ready_failure(
                        "Agent session result channel failed during startup",
                        timeout=timeout,
                        started_at=start,
                        last_handshake=last_handshake,
                        diagnostics={"transport_error": str(exc)},
                    )
                ) from None
            if payload is None:
                if not self.is_alive():
                    try:
                        late_payload = self._join_and_drain_ready_response()
                    except (EOFError, TerminalHandoffTransportError):
                        late_payload = None
                    if late_payload is not None:
                        if is_ready_response(late_payload):
                            return
                        late_type = response_type(late_payload)
                        if late_type is not None:
                            last_handshake = late_type
                        startup_error = startup_error_message(late_payload)
                        if startup_error is not None:
                            raise RuntimeError(
                                self._format_ready_failure(
                                    startup_error,
                                    timeout=timeout,
                                    started_at=start,
                                    last_handshake=last_handshake,
                                    diagnostics=startup_error_diagnostics(late_payload),
                                )
                            ) from None
                    break
                continue
            payload_type = response_type(payload)
            if payload_type is not None:
                last_handshake = payload_type
            if is_booted_response(payload):
                continue
            if is_ready_response(payload):
                return
            startup_error = startup_error_message(payload)
            if startup_error is not None:
                raise RuntimeError(
                    self._format_ready_failure(
                        startup_error,
                        timeout=timeout,
                        started_at=start,
                        last_handshake=last_handshake,
                        diagnostics=startup_error_diagnostics(payload),
                    )
                )
        if channel_sealed:
            try:
                self._process.join(timeout=TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS)
            except Exception:  # pragma: no cover - defensive process observation
                pass

        try:
            late_payload = self._drain_ready_response(timeout=0.2)
        except (EOFError, TerminalHandoffTransportError):
            late_payload = None
        if late_payload is not None:
            if is_ready_response(late_payload):
                return
            late_type = response_type(late_payload)
            if late_type is not None:
                last_handshake = late_type
            startup_error = startup_error_message(late_payload)
            if startup_error is not None:
                raise RuntimeError(
                    self._format_ready_failure(
                        startup_error,
                        timeout=timeout,
                        started_at=start,
                        last_handshake=last_handshake,
                        diagnostics=startup_error_diagnostics(late_payload),
                    )
                ) from None

        raise RuntimeError(
            self._format_ready_failure(
                "Agent session failed to signal readiness",
                timeout=timeout,
                started_at=start,
                last_handshake=last_handshake,
                diagnostics=None,
            )
        )

    def _join_and_drain_ready_response(self) -> Mapping[str, Any] | None:
        """Join a dead startup child briefly and drain one late response."""

        try:
            self._process.join(timeout=0.2)
        except Exception:  # pragma: no cover - defensive process observation
            pass
        return self._drain_ready_response(timeout=0.0)

    def _drain_ready_response(self, *, timeout: float) -> Mapping[str, Any] | None:
        """Drain one late startup response without assuming process liveness."""

        return self._read_response_payload(timeout=timeout)

    def _read_response_payload(
        self,
        *,
        timeout: float,
    ) -> Mapping[str, Any] | None:
        """Read one private response frame, preserving EOF and decode failures."""

        try:
            ready = self._response_receiver.poll(timeout)
        except (OSError, ValueError) as exc:
            raise TerminalHandoffTransportError(
                f"terminal payload poll failed: {exc}"
            ) from exc
        if not ready:
            return None
        payload = receive_terminal_payload(self._response_receiver)
        if not isinstance(payload, Mapping):
            raise TerminalHandoffTransportError(
                f"decoded session payload has invalid type {type(payload).__name__}"
            )
        return payload

    def _format_ready_failure(
        self,
        message: str,
        *,
        timeout: float,
        started_at: float,
        last_handshake: str | None,
        diagnostics: Mapping[str, Any] | None,
    ) -> str:
        elapsed = time.monotonic() - started_at
        process_diagnostics = runner_diagnostics(
            phase="runtime_startup",
            runner="host",
            pid=self.pid,
            exitcode=getattr(self._process, "exitcode", None),
            alive=self.is_alive(),
            duration_seconds=elapsed,
            timeout_seconds=timeout,
            message=message,
            last_handshake=last_handshake,
            extra=diagnostics,
        )
        summary = diagnostic_summary(process_diagnostics)
        if summary is None:
            return message
        return f"{message} ({summary})"

    def execute(
        self,
        work_item: Any,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> SessionExecutionResult:
        if self._closed:
            raise RuntimeError("Agent session is closed")

        self._request_queue.put(make_execute_request(work_item))
        start_time = time.monotonic()
        progress = TerminalHandoffProgress()
        drain_deadline: float | None = None
        pending_transport_failure: str | None = None
        producer_exit_observed = False
        observed_exitcode: int | None = None
        limit_error: str | None = None

        while True:
            now = time.monotonic()
            observations: dict[str, TerminalHandoffEvent] = {}

            if (
                progress.accepted_stop is None
                and "cancel_requested" not in progress.consumed_edge_kinds
                and safe_cancel(cancel_requested)
            ):
                observations["cancel_requested"] = TerminalHandoffEvent(
                    kind="cancel_requested"
                )

            elapsed = now - start_time
            if (
                progress.accepted_stop is None
                and "timeout_requested" not in progress.consumed_edge_kinds
                and self._timeout is not None
                and elapsed >= self._timeout
            ):
                observations["timeout_requested"] = TerminalHandoffEvent(
                    kind="timeout_requested"
                )

            if pending_transport_failure is not None:
                observations["transport_failed"] = TerminalHandoffEvent(
                    kind="transport_failed",
                    detail=pending_transport_failure,
                )
                pending_transport_failure = None
            else:
                try:
                    payload = self._read_response_payload(timeout=0.0)
                except EOFError:
                    observations["channel_sealed"] = TerminalHandoffEvent(
                        kind="channel_sealed"
                    )
                except TerminalHandoffTransportError as exc:
                    observations["transport_failed"] = TerminalHandoffEvent(
                        kind="transport_failed",
                        detail=str(exc),
                    )
                else:
                    if payload is not None:
                        try:
                            parsed = parse_result_response(payload)
                        except (TypeError, ValueError) as exc:
                            parsed = None
                            observations["transport_failed"] = TerminalHandoffEvent(
                                kind="transport_failed",
                                detail=str(exc),
                            )
                        if parsed is None:
                            observations.setdefault(
                                "transport_failed",
                                TerminalHandoffEvent(
                                    kind="transport_failed",
                                    detail="invalid session result payload",
                                ),
                            )
                        else:
                            status, result, error = parsed
                            session_result = SessionExecutionResult(
                                status=status,
                                value=result,
                                error=error,
                            )
                            observations["outcome_received"] = TerminalHandoffEvent(
                                kind="outcome_received",
                                outcome=session_result,
                            )

            if progress.accepted_stop is None and self.is_alive():
                ok, violation = self.poll_limits()
                if not ok:
                    limit_error = violation or "Resource limit exceeded"
                    observations["limit_reached"] = TerminalHandoffEvent(
                        kind="limit_reached",
                        detail=limit_error,
                    )

            if "producer_exited" not in progress.consumed_edge_kinds:
                if not self.is_alive():
                    self._process.join(timeout=0.0)
                    producer_exit_observed = True
                    observed_exitcode = getattr(self._process, "exitcode", None)
                    observations["producer_exited"] = TerminalHandoffEvent(
                        kind="producer_exited",
                        detail=(
                            str(observed_exitcode)
                            if observed_exitcode is not None
                            else None
                        ),
                    )

            if drain_deadline is not None and now >= drain_deadline:
                observations["drain_expired"] = TerminalHandoffEvent(
                    kind="drain_expired"
                )

            step = self._reduce_terminal_observations(
                progress,
                tuple(observations.values()),
            )
            if step is None:
                wait_for = self._terminal_handoff_wait_seconds(
                    now=now,
                    start_time=start_time,
                    drain_deadline=drain_deadline,
                )
                try:
                    self._response_receiver.poll(wait_for)
                except (OSError, ValueError) as exc:
                    pending_transport_failure = str(exc)
                continue

            progress = step.progress
            action = step.decision.action
            if action in {
                "stop_for_timeout",
                "stop_for_cancel",
                "stop_for_limit",
            }:
                if drain_deadline is None:
                    drain_deadline = now + TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS
                self.terminate()
                continue
            if action == "begin_drain":
                if drain_deadline is None:
                    drain_deadline = now + TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS
                continue
            if action == "wait":
                continue
            if action == "return_outcome":
                session_outcome = cast(SessionExecutionResult, step.event.outcome)
                session_outcome.metrics = session_outcome.metrics or self.last_metrics
                if session_outcome.status != "ok":
                    self.close()
                return session_outcome
            if action == "return_timeout":
                return self._finish_invalid_result(
                    SessionExecutionResult(
                        status="timeout",
                        value=None,
                        error="Target execution timed out",
                    )
                )
            if action == "return_cancelled":
                return self._finish_invalid_result(
                    SessionExecutionResult(
                        status="cancelled",
                        value=None,
                        error="Target execution cancelled",
                    )
                )
            if action == "return_limit":
                return self._finish_invalid_result(
                    SessionExecutionResult(
                        status="limit",
                        value=None,
                        error=limit_error or "Resource limit exceeded",
                    )
                )
            if action == "return_protocol_failure":
                if step.event.kind == "channel_sealed" and not producer_exit_observed:
                    self._process.join(timeout=TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS)
                    if not self.is_alive():
                        producer_exit_observed = True
                        observed_exitcode = getattr(self._process, "exitcode", None)
                error = "Worker result channel failed before a result was received"
                if (
                    step.event.kind == "channel_sealed"
                    and producer_exit_observed
                    and observed_exitcode is not None
                ):
                    error = (
                        "Worker exited before returning a result "
                        f"(exit code {observed_exitcode})"
                    )
                return self._finish_invalid_result(
                    SessionExecutionResult(
                        status="error",
                        value=None,
                        error=error,
                        diagnostics=runner_diagnostics(
                            phase="result_handoff",
                            runner="host",
                            target_type="agent",
                            pid=getattr(self._process, "pid", None),
                            exitcode=observed_exitcode,
                            alive=self.is_alive(),
                            duration_seconds=time.monotonic() - start_time,
                            message=step.event.detail or error,
                            extra={
                                "handoff_state": step.decision.source,
                                "handoff_event": step.event.kind,
                                "handoff_transition": step.decision.transition_id,
                                "drain_timeout_seconds": (
                                    TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS
                                ),
                            },
                        ),
                    )
                )
            raise AssertionError(f"Unhandled terminal handoff action: {action}")

    @staticmethod
    def _reduce_terminal_observations(
        progress: TerminalHandoffProgress,
        observations: Sequence[TerminalHandoffEvent],
    ) -> TerminalHandoffStep | None:
        """Route one observation batch through the persistent-session policy."""

        if not observations:
            return None
        return drive_terminal_handoff_turn(
            progress,
            observations,
            policy="persistent_session",
        )

    def _terminal_handoff_wait_seconds(
        self,
        *,
        now: float,
        start_time: float,
        drain_deadline: float | None,
    ) -> float:
        """Return the bounded wait until the next session observation boundary."""

        deadlines = [now + ACTIVE_CONTROL_POLL_INTERVAL]
        if self._timeout is not None:
            deadlines.append(start_time + self._timeout)
        if drain_deadline is not None:
            deadlines.append(drain_deadline)
        return max(0.0, min(deadlines) - now)

    def _finish_invalid_result(
        self,
        result: SessionExecutionResult,
    ) -> SessionExecutionResult:
        """Attach metrics and invalidate a session before returning a verdict."""

        result.metrics = result.metrics or self.last_metrics
        self.close()
        return result

    def is_alive(self) -> bool:
        return self._process.is_alive()

    def terminate(self, *, deadline: float | None = None) -> None:
        if deadline is not None:
            if not self.is_alive():
                try:
                    self._process.join(
                        timeout=min(0.2, max(0.0, deadline - time.monotonic()))
                    )
                except Exception:  # pragma: no cover - defensive
                    pass
                return

            pid = self._process.pid
            remaining = max(0.0, deadline - time.monotonic())
            if isinstance(pid, int) and pid > 0:
                terminate_process_tree(
                    pid,
                    timeout=remaining / 2.0 if remaining > 0 else 0.0,
                    kill_after=True,
                )
            if self.is_alive():
                try:
                    self._process.kill()
                except (OSError, ValueError):  # pragma: no cover - defensive
                    pass
            try:
                self._process.join(
                    timeout=min(0.2, max(0.0, deadline - time.monotonic()))
                )
            except Exception:  # pragma: no cover - defensive
                pass
            return

        if not self.is_alive():
            try:
                self._process.join(timeout=0.2)
            except Exception:  # pragma: no cover - defensive
                pass
            return

        pid = self._process.pid
        if isinstance(pid, int) and pid > 0:
            terminate_process_tree(pid, timeout=0.5)

        try:
            self._process.join(timeout=0.2)
        except Exception:  # pragma: no cover - defensive
            pass
        if not self.is_alive():
            return

        self._process.terminate()
        self._process.join(timeout=0.5)
        if self._process.is_alive():
            self._process.kill()
            self._process.join(timeout=0.5)

    def _close_ipc_resources(self, *, deadline: float | None = None) -> None:
        """Release multiprocessing handles owned by this session wrapper."""

        with contextlib.suppress(Exception):
            self._request_queue.cancel_join_thread()
        with contextlib.suppress(Exception):
            self._request_queue.close()
        with contextlib.suppress(Exception):
            self._response_receiver.close()
        try:
            self._process.close()
        except Exception:  # pragma: no cover - process may still be running
            pass

    def poll_limits(self) -> tuple[bool, str | None]:
        if not self._monitor:
            return True, None
        try:
            ok, violation = self._monitor.check_limits(self._limits)
            self._last_metrics = self._monitor.last_metrics() or self._last_metrics
            return ok, violation
        except Exception:  # pragma: no cover - process may have exited
            self.stop_monitor()
            return True, None

    def stop_monitor(self) -> None:
        if self._monitor:
            try:
                self._last_metrics = self._monitor.last_metrics() or self._last_metrics
            except Exception:  # pragma: no cover - process may have exited
                pass
            try:
                self._monitor.stop()
            except Exception:  # pragma: no cover - defensive
                pass
            self._monitor = None

    @property
    def last_metrics(self) -> ResourceMetrics | None:
        if self._monitor:
            try:
                self._last_metrics = self._monitor.last_metrics() or self._last_metrics
            except Exception:  # pragma: no cover - process may have exited
                pass
        return self._last_metrics

    def close(self, *, deadline: float | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            try:
                if self.is_alive():
                    self._request_queue.put(make_stop_request())
                    join_timeout = 0.5
                    if deadline is not None:
                        join_timeout = min(
                            join_timeout,
                            max(0.0, deadline - time.monotonic()),
                        )
                    self._process.join(timeout=join_timeout)
            except Exception:  # pragma: no cover - defensive
                pass
            if self.is_alive():
                self.terminate(deadline=deadline)
        finally:
            try:
                self.stop_monitor()
            finally:
                self._close_ipc_resources(deadline=deadline)


class InProcessCommandSession:
    """Command session compatible with InteractiveTaskMixin without spawning a subprocess."""

    def __init__(
        self,
        handler: Callable[[str], tuple[str | None, str | None, bool]],
    ) -> None:
        self._handler = handler
        self._stdout_buffer: list[str] = []
        self._stderr_buffer: list[str] = []
        self._alive = True
        self._stdin_closed = False
        self._returncode: int | None = None
        self._last_metrics: ResourceMetrics | None = None

    @property
    def pid(self) -> int | None:
        return None

    @property
    def handle(self) -> RunnerHandle | None:
        return None

    def send(self, data: str) -> None:
        if self._stdin_closed:
            raise RuntimeError("stdin is closed")
        stdout_chunk, stderr_chunk, done = self._handler(data)
        if stdout_chunk:
            self._stdout_buffer.append(stdout_chunk)
        if stderr_chunk:
            self._stderr_buffer.append(stderr_chunk)
        if done:
            self._alive = False
            self._returncode = 0

    def close_stdin(self) -> None:
        self._stdin_closed = True
        if self._alive:
            self._alive = False
            if self._returncode is None:
                self._returncode = 0

    def poll_stdout(self) -> list[str]:
        chunks = list(self._stdout_buffer)
        self._stdout_buffer.clear()
        return chunks

    def poll_stderr(self) -> list[str]:
        chunks = list(self._stderr_buffer)
        self._stderr_buffer.clear()
        return chunks

    def is_alive(self) -> bool:
        return self._alive

    def returncode(self) -> int | None:
        return self._returncode

    def terminate(self, *, deadline: float | None = None) -> None:
        del deadline
        self._alive = False
        if self._returncode is None:
            self._returncode = -1

    def poll_limits(self) -> tuple[bool, str | None]:
        return True, None

    def stop_monitor(self) -> None:
        pass

    @property
    def last_metrics(self) -> ResourceMetrics | None:
        return self._last_metrics


__all__ = ["AgentSession", "InProcessCommandSession", "SessionExecutionResult"]
