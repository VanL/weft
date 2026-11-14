"""Session helpers for interactive commands and persistent agent runtimes.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2], [CC-3.5]
- docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1], [RM-5.2]
- docs/specifications/07-System_Invariants.md [EXEC.5]-[EXEC.10]
- docs/specifications/13-Agent_Runtime.md [AR-6], [AR-9]
"""

from __future__ import annotations

import contextlib
import logging
import queue
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
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
    limits_configured,
    sample_delay,
)
from weft.core.runner_diagnostics import diagnostic_summary, runner_diagnostics
from weft.core.runners.agent_session_protocol import (
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
    poll_terminal_payload,
    receive_terminal_payload,
    wait_terminal_payload,
)
from weft.ext import RunnerHandle, SessionExecutionResult
from weft.helpers import (
    process_create_time,
    safe_cancel,
    terminate_verified_process_tree,
)

logger = logging.getLogger(__name__)


def _resolve_session_create_time(
    process: Any,
    handle: RunnerHandle | None,
) -> float | None:
    """Return the creation-time identity captured at session start [RM-5.1]."""
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return None
    if handle is not None:
        for handle_pid, create_time in handle.scoped_host_processes():
            if handle_pid == pid and create_time is not None:
                return create_time
    return process_create_time(pid)


class CommandSession:
    """Interactive command execution session supporting stdin/stdout streaming."""

    _READ_SIZE = 64 * 1024

    def __init__(
        self,
        process: subprocess.Popen[Any],
        stdout_queue: queue.Queue[str | None],
        stderr_queue: queue.Queue[str | None],
        monitor: BaseResourceMonitor | None,
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
        self._handle = handle
        self._process_create_time = _resolve_session_create_time(process, handle)
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
                        terminate_verified_process_tree(
                            pid,
                            self._process_create_time,
                            kill=(remaining <= 0.0),
                            timeout=remaining / 2.0 if remaining > 0.0 else 0.0,
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
                    terminate_verified_process_tree(
                        pid,
                        self._process_create_time,
                        kill=False,
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
                        with contextlib.suppress(subprocess.TimeoutExpired):
                            self._process.wait(
                                timeout=COMMAND_SESSION_POST_TERMINATION_WAIT
                            )
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
        except Exception:  # pragma: no cover - defensive  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-299] exception
            logger.warning("Failed to run command session cleanup callback")

    def poll_limits(self) -> tuple[bool, str | None]:
        return _poll_monitor_limits(self)

    def stop_monitor(self) -> None:
        if self._monitor:
            try:
                self._last_metrics = self._monitor.last_metrics() or self._last_metrics
            except Exception:  # pragma: no cover - process may have exited  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-300] exception
                logger.warning("Failed to collect final command session metrics")
            try:
                self._monitor.stop()
            except Exception:  # pragma: no cover - defensive  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-300] exception
                logger.warning("Failed to stop command session resource monitor")
            self._monitor = None

    @property
    def last_metrics(self) -> ResourceMetrics | None:
        return self._last_metrics


class AgentSession:
    """Managed long-lived agent worker session."""

    def __init__(
        self,
        process: BaseProcess,
        request_queue: MPQueue[Any],
        response_receiver: Connection,
        monitor: BaseResourceMonitor | None,
        *,
        timeout: float | None,
        handle: RunnerHandle | None = None,
        limits: Any | None = None,
    ) -> None:
        self._process = process
        self._request_queue = request_queue
        self._response_receiver = response_receiver
        self._monitor = monitor
        self._timeout = timeout
        self._handle = handle
        self._process_create_time = _resolve_session_create_time(process, handle)
        # The runner's limits input chooses the sampling clock ([RM-5.1]);
        # the monitor protocol does not expose limits.
        self._limits = limits
        self._last_metrics: ResourceMetrics | None = None
        self._closed = False
        # Session-scoped next limit-sample instant ([RM-5.1]); with a limit
        # configured it is never reset per work item, so short items cannot
        # postpone enforcement. ``None`` means an immediate sample is due.
        self._next_limit_sample_at: float | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid

    @property
    def handle(self) -> RunnerHandle | None:
        return self._handle

    def wait_ready(self, *, timeout: float = 5.0) -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-047] exception
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
            except OSError:  # pragma: no cover - platform wait failure
                logger.warning(
                    "Failed to join agent startup process after channel seal"
                )

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
        except OSError:  # pragma: no cover - platform wait failure
            logger.warning("Failed to join agent startup process before late drain")
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
            ready = poll_terminal_payload(self._response_receiver, timeout)
        except (OSError, ValueError) as exc:
            raise TerminalHandoffTransportError(
                f"terminal payload poll failed: {exc}"
            ) from exc
        if not ready:
            return None
        payload = receive_terminal_payload(self._response_receiver)
        if response_type(payload) is None:
            raise TerminalHandoffTransportError(
                "invalid session response fields or discriminants"
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
        if self._monitor is not None and not limits_configured(self._limits):
            # Without a limit nothing is enforced and sampling is metrics
            # only: each work item samples at its start and then on the
            # doubling schedule from that start ([RM-5.1]).
            self._next_limit_sample_at = None
        progress = TerminalHandoffProgress()
        drain_deadline: float | None = None
        pending_transport_failure: str | None = None
        producer_exit_observed = False
        observed_exitcode: int | None = None
        limit_error: str | None = None

        while True:
            now = time.monotonic()
            next_sample_at = self._next_limit_sample_at
            observations, limit_error = self._observe_terminal_handoff(
                progress,
                now=now,
                start_time=start_time,
                pending_transport_failure=pending_transport_failure,
                limit_error=limit_error,
                cancel_requested=cancel_requested,
                sample_limits=self._limit_sampling_active(progress)
                and (next_sample_at is None or now >= next_sample_at),
            )
            pending_transport_failure = None

            if (
                "producer_exited" not in progress.consumed_edge_kinds
                and not self.is_alive()
            ):
                self._process.join(timeout=0.0)
                producer_exit_observed = True
                observed_exitcode = getattr(self._process, "exitcode", None)
                observations.append(
                    TerminalHandoffEvent(
                        kind="producer_exited",
                        detail=(
                            str(observed_exitcode)
                            if observed_exitcode is not None
                            else None
                        ),
                    )
                )
            if drain_deadline is not None and now >= drain_deadline:
                observations.append(TerminalHandoffEvent(kind="drain_expired"))

            step = self._reduce_terminal_observations(
                progress,
                tuple(observations),
            )
            if step is None:
                # Recomputed after the pass: a monitor that poll_limits()
                # stopped, an accepted stop, or a consumed producer exit
                # removes the sampling deadline from this same wait (D14).
                sampling_deadline = (
                    self._next_limit_sample_at
                    if self._limit_sampling_active(progress)
                    else None
                )
                # A fresh clock reading: the pass's ``now`` predates the
                # limit sample, and a sample that takes time would otherwise
                # stretch this wait past the deadline armed after it.
                wait_for = self._terminal_handoff_wait_seconds(
                    now=time.monotonic(),
                    start_time=start_time,
                    drain_deadline=drain_deadline,
                    cancel_requested=cancel_requested,
                    next_sample_at=sampling_deadline,
                )
                # The sentinel is level-triggered, so it leaves the wait set
                # once producer exit has been reduced.
                producer_sentinel = (
                    None
                    if "producer_exited" in progress.consumed_edge_kinds
                    else self._process.sentinel
                )
                try:
                    wait_terminal_payload(
                        self._response_receiver,
                        producer_sentinel=producer_sentinel,
                        timeout=wait_for,
                    )
                except (OSError, ValueError) as exc:
                    pending_transport_failure = str(exc)
                continue

            progress = step.progress
            drain_deadline, continue_waiting = self._apply_terminal_handoff_effect(
                step,
                now=now,
                drain_deadline=drain_deadline,
            )
            if continue_waiting:
                continue
            return self._terminal_handoff_result(
                step,
                start_time=start_time,
                producer_exit_observed=producer_exit_observed,
                observed_exitcode=observed_exitcode,
                limit_error=limit_error,
            )

    def _observe_terminal_handoff(
        self,
        progress: TerminalHandoffProgress,
        *,
        now: float,
        start_time: float,
        pending_transport_failure: str | None,
        limit_error: str | None,
        cancel_requested: Callable[[], bool] | None,
        sample_limits: bool,
    ) -> tuple[list[TerminalHandoffEvent], str | None]:
        """Collect one persistent-session observation batch.

        A limit sample is taken only when ``sample_limits`` says the
        session's next-sample instant is due, and never for a dead producer or
        after an accepted stop. The next instant is armed ``sample_delay``
        after a clock reading taken after the sample: one monitor
        ``polling_interval`` with a limit configured, the doubling schedule
        from the work item's start without one, so samples are never closer
        than one interval ([RM-5.1]).
        """

        observations: dict[str, TerminalHandoffEvent] = {}
        if (
            progress.accepted_stop is None
            and "cancel_requested" not in progress.consumed_edge_kinds
            and safe_cancel(cancel_requested)
        ):
            observations["cancel_requested"] = TerminalHandoffEvent(
                kind="cancel_requested"
            )
        if (
            progress.accepted_stop is None
            and "timeout_requested" not in progress.consumed_edge_kinds
            and self._timeout is not None
            and now - start_time >= self._timeout
        ):
            observations["timeout_requested"] = TerminalHandoffEvent(
                kind="timeout_requested"
            )

        response_event = self._observe_terminal_response(pending_transport_failure)
        if response_event is not None:
            observations[response_event.kind] = response_event

        monitor = self._monitor
        if (
            sample_limits
            and monitor is not None
            and progress.accepted_stop is None
            and self.is_alive()
        ):
            # Read before sampling: poll_limits() stops the monitor on a
            # monitor failure, so it may be gone afterwards.
            interval = float(monitor.polling_interval)
            ok, violation = self.poll_limits()
            if self._monitor is not None:
                sampled_at = time.monotonic()
                self._next_limit_sample_at = sampled_at + sample_delay(
                    self._limits,
                    interval=interval,
                    elapsed=sampled_at - start_time,
                )
            if not ok:
                limit_error = violation or "Resource limit exceeded"
                observations["limit_reached"] = TerminalHandoffEvent(
                    kind="limit_reached",
                    detail=limit_error,
                )
        return list(observations.values()), limit_error

    def _observe_terminal_response(
        self,
        pending_transport_failure: str | None,
    ) -> TerminalHandoffEvent | None:
        """Read and parse one session response observation."""

        if pending_transport_failure is not None:
            return TerminalHandoffEvent(
                kind="transport_failed",
                detail=pending_transport_failure,
            )
        try:
            payload = self._read_response_payload(timeout=0.0)
        except EOFError:
            return TerminalHandoffEvent(kind="channel_sealed")
        except TerminalHandoffTransportError as exc:
            return TerminalHandoffEvent(kind="transport_failed", detail=str(exc))
        if payload is None:
            return None
        try:
            parsed = parse_result_response(payload)
        except (TypeError, ValueError) as exc:
            return TerminalHandoffEvent(kind="transport_failed", detail=str(exc))
        if parsed is None:
            return TerminalHandoffEvent(
                kind="transport_failed",
                detail="invalid session result payload",
            )
        status, result, error, diagnostics = parsed
        return TerminalHandoffEvent(
            kind="outcome_received",
            outcome=SessionExecutionResult(
                status=status,
                value=result,
                error=error,
                diagnostics=diagnostics,
            ),
        )

    def _apply_terminal_handoff_effect(
        self,
        step: TerminalHandoffStep,
        *,
        now: float,
        drain_deadline: float | None,
    ) -> tuple[float | None, bool]:
        """Apply a selected stop or drain effect and report whether to wait."""

        action = step.decision.action
        if action in {
            "stop_for_timeout",
            "stop_for_cancel",
            "stop_for_limit",
        }:
            if drain_deadline is None:
                drain_deadline = now + TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS
            self.terminate()
            return drain_deadline, True
        if action == "begin_drain":
            if drain_deadline is None:
                drain_deadline = now + TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS
            return drain_deadline, True
        return drain_deadline, action == "wait"

    def _terminal_handoff_result(
        self,
        step: TerminalHandoffStep,
        *,
        start_time: float,
        producer_exit_observed: bool,
        observed_exitcode: int | None,
        limit_error: str | None,
    ) -> SessionExecutionResult:
        """Build the selected persistent-session terminal result."""

        action = step.decision.action
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
            return self._terminal_protocol_failure_result(
                step,
                start_time=start_time,
                producer_exit_observed=producer_exit_observed,
                observed_exitcode=observed_exitcode,
            )
        raise AssertionError(f"Unhandled terminal handoff action: {action}")

    def _terminal_protocol_failure_result(
        self,
        step: TerminalHandoffStep,
        *,
        start_time: float,
        producer_exit_observed: bool,
        observed_exitcode: int | None,
    ) -> SessionExecutionResult:
        """Build and invalidate a persistent-session protocol failure."""

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

    def _limit_sampling_active(self, progress: TerminalHandoffProgress) -> bool:
        """Return whether limit sampling is clock work for this pass.

        Sampling needs a monitor, no accepted stop, and a producer whose exit
        has not been reduced ([RM-5.1]; D14 of the reactor-edge plan).
        """

        return (
            self._monitor is not None
            and progress.accepted_stop is None
            and "producer_exited" not in progress.consumed_edge_kinds
        )

    def _terminal_handoff_wait_seconds(
        self,
        *,
        now: float,
        start_time: float,
        drain_deadline: float | None,
        cancel_requested: Callable[[], bool] | None,
        next_sample_at: float | None,
    ) -> float | None:
        """Return the wait until the next session observation boundary.

        Readiness (a payload, channel seal or producer exit) wakes the wait;
        this returns only its deadlines: the work-item timeout, the drain
        deadline, the active limit-sampling instant, and, only while a
        pull-style ``cancel_requested`` predicate exists, the
        ``ACTIVE_CONTROL_POLL_INTERVAL`` re-check. ``None`` means wait on
        readiness alone.

        Spec: [CC-2.2.1], [CC-3.5], [RM-5.1]
        """

        deadlines: list[float] = []
        if cancel_requested is not None:
            deadlines.append(now + ACTIVE_CONTROL_POLL_INTERVAL)
        if self._timeout is not None:
            deadlines.append(start_time + self._timeout)
        if drain_deadline is not None:
            deadlines.append(drain_deadline)
        if next_sample_at is not None:
            deadlines.append(next_sample_at)
        if not deadlines:
            return None
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

    def terminate(self, *, deadline: float | None = None) -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-049] exception
        if deadline is not None:
            if not self.is_alive():
                try:
                    self._process.join(
                        timeout=min(0.2, max(0.0, deadline - time.monotonic()))
                    )
                except OSError:  # pragma: no cover - platform wait failure
                    logger.warning(
                        "Failed to join exited agent session process before deadline"
                    )
                return

            pid = self._process.pid
            remaining = max(0.0, deadline - time.monotonic())
            if isinstance(pid, int) and pid > 0:
                terminate_verified_process_tree(
                    pid,
                    self._process_create_time,
                    kill=(remaining <= 0.0),
                    timeout=remaining / 2.0 if remaining > 0.0 else 0.0,
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
            except OSError:  # pragma: no cover - platform wait failure
                logger.warning("Failed to join agent session process before deadline")
            return

        if not self.is_alive():
            try:
                self._process.join(timeout=0.2)
            except OSError:  # pragma: no cover - platform wait failure
                logger.warning("Failed to join exited agent session process")
            return

        pid = self._process.pid
        if isinstance(pid, int) and pid > 0:
            terminate_verified_process_tree(
                pid,
                self._process_create_time,
                kill=False,
                timeout=0.5,
            )

        try:
            self._process.join(timeout=0.2)
        except OSError:  # pragma: no cover - platform wait failure
            logger.warning("Failed to join agent session process before escalation")
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
        except (OSError, ValueError):  # pragma: no cover - platform cleanup
            logger.warning("Failed to close agent session process handle")

    def poll_limits(self) -> tuple[bool, str | None]:
        return _poll_monitor_limits(self)

    def stop_monitor(self) -> None:
        if self._monitor:
            try:
                self._last_metrics = self._monitor.last_metrics() or self._last_metrics
            except Exception:  # pragma: no cover - process may have exited  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-303] exception
                logger.warning("Failed to collect final agent session metrics")
            try:
                self._monitor.stop()
            except Exception:  # pragma: no cover - defensive  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-303] exception
                logger.warning("Failed to stop agent session resource monitor")
            self._monitor = None

    @property
    def last_metrics(self) -> ResourceMetrics | None:
        if self._monitor:
            try:
                self._last_metrics = self._monitor.last_metrics() or self._last_metrics
            except Exception:  # pragma: no cover - optional monitor observation  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-304] exception
                logger.warning("Failed to collect agent session metrics")
        return self._last_metrics

    def close(self, *, deadline: float | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            try:
                if self.is_alive():
                    stop_request = make_stop_request()
                    try:
                        self._request_queue.put(stop_request)
                    except Exception:  # pragma: no cover - queue cleanup boundary  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-307] exception
                        logger.warning("Failed to send agent session stop request")
                    else:
                        join_timeout = 0.5
                        if deadline is not None:
                            join_timeout = min(
                                join_timeout,
                                max(0.0, deadline - time.monotonic()),
                            )
                        try:
                            self._process.join(timeout=join_timeout)
                        except OSError:  # pragma: no cover - platform wait failure
                            logger.warning(
                                "Failed to join agent session after stop request"
                            )
            finally:
                if self.is_alive():
                    self.terminate(deadline=deadline)
        finally:
            try:
                self.stop_monitor()
            finally:
                self._close_ipc_resources(deadline=deadline)


def _poll_monitor_limits(
    session: CommandSession | AgentSession,
) -> tuple[bool, str | None]:
    if not session._monitor:
        return True, None
    try:
        ok, violation = session._monitor.check_limits()
        if not ok:
            return ok, violation
        session._last_metrics = session._monitor.last_metrics() or session._last_metrics
        return ok, violation
    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-344] exception
        session.stop_monitor()
        return True, None


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
