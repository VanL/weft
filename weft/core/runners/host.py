"""Built-in host runner implementation.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3], [CC-3.2], [CC-3.4], [CC-3.5]
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1], [RM-5.2]
- docs/specifications/07-System_Invariants.md [EXEC.5]-[EXEC.10]
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from functools import lru_cache
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue as MPQueue
from typing import Any, TextIO, cast

from simplebroker import BrokerTarget
from weft._constants import (
    ACTIVE_CONTROL_POLL_INTERVAL,
    AGENT_SESSION_READY_TIMEOUT_SECONDS,
    TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS,
)
from weft.core.agents import register_builtin_agent_runtimes
from weft.core.agents.runtime import (
    execute_agent_target,
    normalize_agent_work_item,
    start_agent_runtime_session,
)
from weft.core.resource_monitor import ResourceMetrics, load_resource_monitor
from weft.core.runner_diagnostics import runner_diagnostics
from weft.core.runners.outcome import RunnerOutcome
from weft.core.runners.subprocess_runner import (
    prepare_command_invocation,
    run_monitored_subprocess,
)
from weft.core.targets import execute_command_target, execute_function_target
from weft.core.tasks.agent_session_protocol import (
    make_booted_response,
    make_ready_response,
    make_result_response,
    make_startup_error_response,
    parse_request_type,
)
from weft.core.tasks.sessions import AgentSession, CommandSession
from weft.core.taskspec import AgentSection
from weft.core.terminal_handoff import (
    TerminalHandoffEvent,
    TerminalHandoffProgress,
    TerminalHandoffStep,
    drive_terminal_handoff_turn,
)
from weft.core.terminal_handoff_transport import (
    TerminalHandoffTransportError,
    TerminalPayloadSerializationFailure,
    poll_terminal_payload,
    receive_terminal_payload,
    send_terminal_payload,
)
from weft.ext import (
    RunnerCapabilities,
    RunnerHandle,
    RunnerPlugin,
    RunnerRuntimeDescription,
)
from weft.helpers import (
    ContainerRuntimeDetection,
    detect_container_runtime,
    kill_process_tree,
    pid_is_live,
    pid_matches_create_time,
    process_create_time,
    safe_cancel,
    terminate_process_tree,
)

logger = logging.getLogger(__name__)

register_builtin_agent_runtimes()


def _plain_spawn_value(value: Any) -> Any:
    """Copy frozen TaskSpec containers into spawn-pickle-safe built-ins."""

    if isinstance(value, Mapping):
        return {key: _plain_spawn_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_plain_spawn_value(item) for item in value]
    return value


def _host_handle(pid: int | None) -> RunnerHandle | None:
    if pid is None or pid <= 0:
        return None
    create_time = process_create_time(pid)
    return RunnerHandle(
        runner="host",
        kind="process",
        id=str(pid),
        control={"authority": "host-pid"},
        observations={
            "host_pids": [pid],
            "host_processes": [{"pid": pid, "create_time": create_time}],
        },
    )


def _host_pid_matches(pid: int, create_time: float | None) -> bool:
    """Return whether a host PID is still the observed process."""

    if create_time is None:
        return pid_is_live(pid)
    return pid_matches_create_time(pid, create_time)


@lru_cache(maxsize=1)
def _current_container_runtime() -> ContainerRuntimeDetection | None:
    """Return cached container evidence for host-PID visibility decisions."""

    return detect_container_runtime()


def _worker_entry(
    spec_data: Mapping[str, Any],
    work_item: Any,
    result_sender: Connection,
) -> None:
    """Execute a single work item in a spawned process."""
    start = time.monotonic()
    status = "ok"
    value = None
    error = None
    stdout = None
    stderr = None
    returncode: int | None = 0
    diagnostics: dict[str, Any] | None

    try:
        with _worker_runtime_context(spec_data):
            if spec_data["type"] == "function":
                value = execute_function_target(
                    spec_data["function_target"],
                    work_item,
                    args=spec_data.get("args"),
                    kwargs=spec_data.get("kwargs"),
                    bundle_root=cast(str | None, spec_data.get("bundle_root")),
                )
            elif spec_data["type"] == "agent":
                agent = AgentSection.model_validate(spec_data["agent"])
                value = execute_agent_target(
                    agent,
                    work_item,
                    tid=spec_data.get("tid"),
                    bundle_root=cast(str | None, spec_data.get("bundle_root")),
                )
            else:
                completed = execute_command_target(
                    spec_data["process_target"],
                    work_item,
                    args=spec_data.get("args"),
                    env=spec_data.get("env") or {},
                    working_dir=spec_data.get("working_dir"),
                    # HostTaskRunner owns timeout enforcement for one-shot command
                    # tasks. Passing the same timeout into subprocess.run() races the
                    # outer worker timeout and can orphan grandchildren when the
                    # direct child exits first.
                    timeout=None,
                )
                value = completed.stdout.strip() if completed.stdout is not None else ""
                stdout = completed.stdout
                stderr = completed.stderr
                returncode = completed.returncode
                if completed.returncode != 0:
                    status = "error"
                    error = (
                        f"Command exited with {completed.returncode}: "
                        f"{(completed.stderr or '').strip()}"
                    )
    except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-342] exception
        traceback_text = traceback.format_exc()
        status = "error"
        error = traceback_text
        returncode = None
        diagnostics = runner_diagnostics(
            phase="execute",
            runner="host",
            target_type=str(spec_data.get("type") or "unknown"),
            message=str(exc),
            exception_type=type(exc).__name__,
            traceback_text=traceback_text,
        )
    else:
        diagnostics = None

    end = time.monotonic()
    outcome = RunnerOutcome(
        status=status,
        value=value,
        error=error,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        duration=end - start,
        diagnostics=diagnostics,
    )

    def _serialization_failure(
        failure: TerminalPayloadSerializationFailure,
    ) -> RunnerOutcome:
        message = (
            f"Task returned a value that Weft could not serialize: {failure.cause}"
        )
        return RunnerOutcome(
            status="error",
            value=None,
            error=message,
            stdout=stdout,
            stderr=stderr,
            returncode=None,
            duration=end - start,
            diagnostics=runner_diagnostics(
                phase="result_serialization",
                runner="host",
                target_type=str(spec_data.get("type") or "unknown"),
                message=message,
            ),
        )

    try:
        send_terminal_payload(
            result_sender,
            outcome,
            serialization_failure_factory=_serialization_failure,
        )
    finally:
        result_sender.close()


@contextlib.contextmanager
def _worker_runtime_context(
    spec_data: Mapping[str, Any],
) -> Iterator[None]:
    """Apply task-scoped env and cwd overrides inside a spawned worker."""
    original_cwd = os.getcwd()
    env_override = spec_data.get("env") or {}
    previous_env: dict[str, str | None] = {}
    try:
        if isinstance(env_override, Mapping):
            for key, value in env_override.items():
                key_text = str(key)
                previous_env[key_text] = os.environ.get(key_text)
                os.environ[key_text] = str(value)
        working_dir_obj = spec_data.get("working_dir")
        if working_dir_obj:
            os.chdir(str(working_dir_obj))
        yield
    finally:
        os.chdir(original_cwd)
        if isinstance(env_override, Mapping):
            for key in env_override:
                key_text = str(key)
                previous_value = previous_env.get(key_text)
                if previous_value is None:
                    os.environ.pop(key_text, None)
                else:
                    os.environ[key_text] = previous_value


def _agent_session_worker_entry(
    spec_data: Mapping[str, Any],
    request_queue: MPQueue[dict[str, Any]],
    response_sender: Connection,
) -> None:
    """Run a long-lived agent session in a spawned subprocess."""
    session = None
    ready_sent = False
    try:
        send_terminal_payload(response_sender, make_booted_response())
        with _worker_runtime_context(spec_data):
            agent = AgentSection.model_validate(spec_data["agent"])
            session = start_agent_runtime_session(
                agent,
                tid=spec_data.get("tid"),
                bundle_root=cast(str | None, spec_data.get("bundle_root")),
            )
            send_terminal_payload(response_sender, make_ready_response())
            ready_sent = True

            while True:
                request = request_queue.get()
                request_type = parse_request_type(request)
                if request_type == "stop":
                    break
                if request_type != "execute":
                    continue

                try:
                    normalized = normalize_agent_work_item(
                        agent,
                        request.get("work_item"),
                    )
                    result = session.execute(normalized)
                except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-356] exception
                    traceback_text = traceback.format_exc()
                    send_terminal_payload(
                        response_sender,
                        make_result_response(
                            status="error",
                            error=traceback_text,
                            diagnostics=runner_diagnostics(
                                phase="execute",
                                runner="host",
                                target_type="agent",
                                message=str(exc),
                                exception_type=type(exc).__name__,
                                traceback_text=traceback_text,
                            ),
                        ),
                    )
                    break

                original_sent = send_terminal_payload(
                    response_sender,
                    make_result_response(status="ok", result=result),
                    serialization_failure_factory=lambda failure: make_result_response(
                        status="error",
                        error=(
                            "Task returned a value that Weft could not "
                            f"serialize: {failure.cause}"
                        ),
                    ),
                )
                if not original_sent:
                    break
    except TerminalHandoffTransportError:
        return
    except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-356] exception
        traceback_text = traceback.format_exc()
        diagnostics = runner_diagnostics(
            phase="execute" if ready_sent else "runtime_startup",
            runner="host",
            target_type=str(spec_data.get("type") or "agent"),
            message=str(exc),
            exception_type=type(exc).__name__,
            traceback_text=traceback_text,
        )
        with contextlib.suppress(TerminalHandoffTransportError):
            response = (
                make_result_response(
                    status="error",
                    error=traceback_text,
                    diagnostics=diagnostics,
                )
                if ready_sent
                else make_startup_error_response(
                    traceback_text,
                    diagnostics=diagnostics,
                )
            )
            send_terminal_payload(
                response_sender,
                response,
            )
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # pragma: no cover - defensive  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-298] exception
                logger.warning("Failed to close host agent runtime session")
        with contextlib.suppress(Exception):
            request_queue.close()
        with contextlib.suppress(Exception):
            response_sender.close()


def _start_optional_monitor(monitor: Any, pid: int | None) -> Any | None:
    if pid is None:
        logger.warning(
            "Host resource monitor disabled because worker PID is unavailable"
        )
        try:
            monitor.stop()
        except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-313] exception
            logger.warning("Failed to stop host resource monitor without a worker PID")
        return None

    try:
        monitor.start(pid)
    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-313] exception
        logger.warning("Failed to start host resource monitor")
        try:
            monitor.stop()
        except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-313] exception
            logger.warning("Failed to stop host resource monitor after startup failure")
        return None

    return monitor


class HostTaskRunner:
    """Managed wrapper around local multiprocessing for target execution."""

    def __init__(
        self,
        *,
        target_type: str,
        tid: str | None,
        function_target: str | None,
        process_target: str | None,
        agent: Mapping[str, Any] | None,
        args: Sequence[Any] | None,
        kwargs: Mapping[str, Any] | None,
        env: Mapping[str, str] | None,
        working_dir: str | None,
        timeout: float | None,
        limits: Any | None,
        monitor_class: str | None,
        monitor_interval: float | None,
        bundle_root: str | None = None,
    ) -> None:
        self._spec_data = {
            "type": target_type,
            "tid": tid,
            "function_target": function_target,
            "process_target": process_target,
            "agent": _plain_spawn_value(agent) if agent is not None else None,
            "args": _plain_spawn_value(args or ()),
            "kwargs": _plain_spawn_value(kwargs or {}),
            "env": _plain_spawn_value(env or {}),
            "working_dir": working_dir,
            "command_timeout": timeout,
            "bundle_root": bundle_root,
        }
        self._timeout = timeout
        self._ctx = multiprocessing.get_context("spawn")
        self._ctx.set_executable(sys.executable)
        self._limits = limits
        self._monitor_class = monitor_class
        self._monitor_interval = monitor_interval or 1.0

    def run(self, work_item: Any) -> RunnerOutcome:
        """Execute a work item with resource monitoring and timeout handling."""
        return self.run_with_hooks(work_item)

    def run_with_hooks(
        self,
        work_item: Any,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_worker_started: Callable[[int | None], None] | None = None,
        on_runtime_handle_started: Callable[[RunnerHandle], None] | None = None,
        on_stdout_chunk: Callable[[str, bool], None] | None = None,
        on_stderr_chunk: Callable[[str, bool], None] | None = None,
    ) -> RunnerOutcome:
        """Execute a work item with optional lifecycle hooks.

        Spec: [CC-3.2] (live runtime identity callbacks); [RM-5], [RM-5.1]
        (resource monitor polling and limit enforcement).
        """
        if self._spec_data["type"] == "command":
            return self._run_command_with_hooks(
                work_item,
                cancel_requested=cancel_requested,
                on_worker_started=on_worker_started,
                on_runtime_handle_started=on_runtime_handle_started,
                on_stdout_chunk=on_stdout_chunk,
                on_stderr_chunk=on_stderr_chunk,
            )

        response_receiver, response_sender = self._ctx.Pipe(duplex=False)
        process = self._ctx.Process(
            target=_worker_entry,
            args=(self._spec_data, work_item, response_sender),
            daemon=True,
        )
        process_started = False
        try:
            process.start()
            process_started = True
            response_sender.close()
            worker_pid = process.pid
            runtime_handle = _host_handle(worker_pid)
            if on_worker_started is not None:
                try:
                    on_worker_started(worker_pid)
                except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-343] exception
                    logger.warning("Host worker-start callback failed")
            if on_runtime_handle_started is not None and runtime_handle is not None:
                try:
                    on_runtime_handle_started(runtime_handle)
                except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-343] exception
                    logger.warning("Host runtime-handle callback failed")
            return self._run_one_shot_terminal_handoff(
                process,
                response_receiver,
                worker_pid=worker_pid,
                runtime_handle=runtime_handle,
                cancel_requested=cancel_requested,
            )
        finally:
            with contextlib.suppress(Exception):
                response_sender.close()
            with contextlib.suppress(Exception):
                response_receiver.close()
            if process_started:
                with contextlib.suppress(Exception):
                    if process.is_alive():
                        self._stop_process(process)
                    else:
                        process.join(timeout=0.2)
            self._close_process_handle(process)

    def _run_one_shot_terminal_handoff(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-034] exception
        self,
        process: BaseProcess,
        response_receiver: Connection,
        *,
        worker_pid: int | None,
        runtime_handle: RunnerHandle | None,
        cancel_requested: Callable[[], bool] | None,
    ) -> RunnerOutcome:
        """Observe one worker and reduce its private terminal handoff.

        Spec: [CC-3.5], [RM-5.2], [EXEC.5]-[EXEC.10].
        """

        monitor = None
        last_metrics: ResourceMetrics | None = None
        if self._monitor_class:
            monitor = load_resource_monitor(
                self._monitor_class,
                limits=self._limits,
                polling_interval=self._monitor_interval,
            )
            monitor = _start_optional_monitor(monitor, worker_pid)

        start_time = time.monotonic()
        next_monitor_at = start_time + self._monitor_interval
        drain_deadline: float | None = None
        progress = TerminalHandoffProgress()
        pending_transport_failure: str | None = None
        producer_exit_observed = False
        observed_exitcode: int | None = None
        limit_error: str | None = None
        outcome: RunnerOutcome | None = None

        try:
            while outcome is None:
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

                if pending_transport_failure is not None:
                    observations["transport_failed"] = TerminalHandoffEvent(
                        kind="transport_failed",
                        detail=pending_transport_failure,
                    )
                    pending_transport_failure = None
                else:
                    try:
                        channel_ready = poll_terminal_payload(response_receiver, 0.0)
                    except (OSError, ValueError) as exc:
                        channel_ready = False
                        observations["transport_failed"] = TerminalHandoffEvent(
                            kind="transport_failed",
                            detail=str(exc),
                        )
                    if channel_ready:
                        try:
                            payload = receive_terminal_payload(response_receiver)
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
                            if isinstance(payload, RunnerOutcome):
                                observations["outcome_received"] = TerminalHandoffEvent(
                                    kind="outcome_received",
                                    outcome=payload,
                                )
                            else:
                                observations["transport_failed"] = TerminalHandoffEvent(
                                    kind="transport_failed",
                                    detail=(
                                        "decoded terminal payload has invalid type "
                                        f"{type(payload).__name__}"
                                    ),
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

                if (
                    monitor is not None
                    and progress.accepted_stop is None
                    and "limit_reached" not in progress.consumed_edge_kinds
                    and now >= next_monitor_at
                    and process.is_alive()
                ):
                    ok, violation = True, None
                    try:
                        ok, violation = monitor.check_limits()
                    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-313] exception
                        logger.warning("Failed to check host resource limits")
                    try:
                        observed_metrics = monitor.last_metrics()
                    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-313] exception
                        logger.warning("Failed to collect host resource metrics")
                    else:
                        last_metrics = observed_metrics or last_metrics
                    next_monitor_at = now + self._monitor_interval
                    if not ok:
                        limit_error = violation or "Resource limit exceeded"
                        observations["limit_reached"] = TerminalHandoffEvent(
                            kind="limit_reached",
                            detail=limit_error,
                        )

                if (
                    "producer_exited" not in progress.consumed_edge_kinds
                    and not process.is_alive()
                ):
                    process.join(timeout=0.0)
                    producer_exit_observed = True
                    observed_exitcode = process.exitcode
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
                        next_monitor_at=next_monitor_at if monitor else None,
                        drain_deadline=drain_deadline,
                    )
                    try:
                        poll_terminal_payload(response_receiver, wait_for)
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
                    self._stop_process(process)
                    continue
                if action == "begin_drain":
                    if drain_deadline is None:
                        drain_deadline = now + TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS
                    continue
                if action == "wait":
                    continue
                if action == "return_outcome":
                    outcome = cast(RunnerOutcome, step.event.outcome)
                    continue
                if action == "return_timeout":
                    outcome = RunnerOutcome(
                        status="timeout",
                        value=None,
                        error="Target execution timed out",
                        stdout=None,
                        stderr=None,
                        returncode=None,
                        duration=(
                            self._timeout
                            if self._timeout is not None
                            else time.monotonic() - start_time
                        ),
                    )
                    continue
                if action == "return_cancelled":
                    outcome = RunnerOutcome(
                        status="cancelled",
                        value=None,
                        error="Target execution cancelled",
                        stdout=None,
                        stderr=None,
                        returncode=None,
                        duration=time.monotonic() - start_time,
                    )
                    continue
                if action == "return_limit":
                    outcome = RunnerOutcome(
                        status="limit",
                        value=None,
                        error=limit_error or "Resource limit exceeded",
                        stdout=None,
                        stderr=None,
                        returncode=None,
                        duration=time.monotonic() - start_time,
                    )
                    continue
                if action == "return_protocol_failure":
                    if (
                        step.event.kind == "channel_sealed"
                        and not producer_exit_observed
                    ):
                        process.join(timeout=TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS)
                        if not process.is_alive():
                            producer_exit_observed = True
                            observed_exitcode = process.exitcode
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
                    outcome = RunnerOutcome(
                        status="error",
                        value=None,
                        error=error,
                        stdout=None,
                        stderr=None,
                        returncode=observed_exitcode,
                        duration=time.monotonic() - start_time,
                        diagnostics=runner_diagnostics(
                            phase="result_handoff",
                            runner="host",
                            target_type=str(self._spec_data.get("type") or "unknown"),
                            pid=worker_pid,
                            exitcode=observed_exitcode,
                            alive=process.is_alive(),
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
                    continue
                raise AssertionError(f"Unhandled terminal handoff action: {action}")

            if process.is_alive():
                self._stop_process(process)
            else:
                process.join(timeout=0.2)

            if monitor is not None:
                try:
                    observed_metrics = monitor.last_metrics()
                except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-313] exception
                    logger.warning("Failed to collect final host resource metrics")
                else:
                    last_metrics = observed_metrics or last_metrics
                if last_metrics is None:
                    try:
                        last_metrics = monitor.snapshot()
                    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-313] exception
                        logger.warning("Failed to snapshot final host resource metrics")

            outcome.metrics = outcome.metrics or last_metrics
            outcome.runtime_handle = outcome.runtime_handle or runtime_handle
            return outcome
        finally:
            if monitor is not None:
                try:
                    monitor.stop()
                except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-313] exception
                    logger.warning("Failed to stop host resource monitor")

    def _terminal_handoff_wait_seconds(
        self,
        *,
        now: float,
        start_time: float,
        next_monitor_at: float | None,
        drain_deadline: float | None,
    ) -> float:
        """Return the bounded wait until the next owned observation boundary."""

        deadlines = [now + ACTIVE_CONTROL_POLL_INTERVAL]
        if self._timeout is not None:
            deadlines.append(start_time + self._timeout)
        if next_monitor_at is not None:
            deadlines.append(next_monitor_at)
        if drain_deadline is not None:
            deadlines.append(drain_deadline)
        return max(0.0, min(deadlines) - now)

    @staticmethod
    def _reduce_terminal_observations(
        progress: TerminalHandoffProgress,
        observations: Sequence[TerminalHandoffEvent],
    ) -> TerminalHandoffStep | None:
        """Route one observation batch through the one-shot policy."""

        if not observations:
            return None
        return drive_terminal_handoff_turn(
            progress,
            observations,
            policy="one_shot",
        )

    def _run_command_with_hooks(
        self,
        work_item: Any,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_worker_started: Callable[[int | None], None] | None = None,
        on_runtime_handle_started: Callable[[RunnerHandle], None] | None = None,
        on_stdout_chunk: Callable[[str, bool], None] | None = None,
        on_stderr_chunk: Callable[[str, bool], None] | None = None,
    ) -> RunnerOutcome:
        process_target_obj = self._spec_data.get("process_target")
        if not isinstance(process_target_obj, str) or not process_target_obj:
            raise TypeError("process_target must be a non-empty command string")

        command, stdin_data = prepare_command_invocation(
            process_target_obj,
            work_item,
            args=cast(Sequence[Any] | None, self._spec_data.get("args")),
        )

        env_vars: dict[str, str] = dict(os.environ)
        env_override = self._spec_data.get("env") or {}
        if isinstance(env_override, Mapping):
            env_vars.update({str(k): str(v) for k, v in env_override.items()})
        else:
            raise TypeError("Spec env must be a mapping of string keys to values")

        working_dir_obj = self._spec_data.get("working_dir")
        cwd_value: str | None = str(working_dir_obj) if working_dir_obj else None

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if stdin_data is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd_value,
                env=env_vars,
                creationflags=creation_flags,
            )
        except OSError as exc:
            return RunnerOutcome(
                status="error",
                value=None,
                error=str(exc),
                stdout=None,
                stderr=None,
                returncode=None,
                duration=0.0,
                diagnostics=runner_diagnostics(
                    phase="process_spawn",
                    runner="host",
                    target_type="command",
                    message=str(exc),
                    exception_type=type(exc).__name__,
                    extra={"command": command[:1], "cwd": cwd_value},
                ),
            )

        runtime_handle = _host_handle(process.pid)
        if runtime_handle is None:
            raise RuntimeError("Command process did not expose a PID")

        def _stop_runtime() -> None:
            terminate_process_tree(process.pid or -1, timeout=0.2)

        def _kill_runtime() -> None:
            kill_process_tree(process.pid or -1, timeout=0.2)

        return run_monitored_subprocess(
            process=process,
            stdin_data=stdin_data,
            timeout=self._timeout,
            limits=self._limits,
            monitor_class=self._monitor_class,
            monitor_interval=self._monitor_interval,
            monitor=None,
            runtime_handle=runtime_handle,
            cancel_requested=cancel_requested,
            on_worker_started=on_worker_started,
            on_runtime_handle_started=on_runtime_handle_started,
            on_stdout_chunk=on_stdout_chunk,
            on_stderr_chunk=on_stderr_chunk,
            stop_runtime=_stop_runtime,
            kill_runtime=_kill_runtime,
            worker_pid=process.pid,
        )

    @staticmethod
    def _stop_process(process: BaseProcess, *, timeout: float = 0.2) -> None:
        """Stop a worker process, escalating to kill if needed."""
        if not process.is_alive():
            process.join(timeout=timeout)
            return

        pid = process.pid
        if isinstance(pid, int) and pid > 0:
            terminate_process_tree(pid, timeout=timeout)

        try:
            process.join(timeout=timeout)
        except OSError:  # pragma: no cover - platform wait failure
            logger.warning("Failed to join host runner process before escalation")
        if not process.is_alive():
            return

        process.terminate()
        process.join(timeout=timeout)
        if process.is_alive():
            process.kill()
            process.join(timeout=timeout)

    @staticmethod
    def _close_mp_queue(mp_queue: MPQueue[Any]) -> None:
        """Release multiprocessing Queue handles owned by this process."""

        try:
            mp_queue.close()
        except Exception:  # pragma: no cover - defensive cleanup  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-295] exception
            logger.warning("Failed to close host runner multiprocessing queue")
        try:
            mp_queue.join_thread()
        except Exception:  # pragma: no cover - defensive cleanup  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-295] exception
            logger.warning("Failed to join host runner queue thread")

    @staticmethod
    def _close_process_handle(process: BaseProcess) -> None:
        """Release the OS handle held by a multiprocessing Process wrapper."""

        try:
            process.close()
        except (OSError, ValueError):  # pragma: no cover - platform cleanup
            logger.warning("Failed to close host runner process handle")

    def start_session(self) -> CommandSession:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-035] exception
        """Start a line-oriented interactive command session for streaming IO."""
        if self._spec_data["type"] != "command":
            raise ValueError(
                "Interactive sessions are only supported for command targets"
            )

        env_vars: dict[str, str] = dict(os.environ)
        env_override = self._spec_data.get("env") or {}
        if isinstance(env_override, Mapping):
            env_vars.update({str(k): str(v) for k, v in env_override.items()})
        else:
            raise TypeError("Spec env must be a mapping of string keys to values")
        env_vars.setdefault("PYTHONUNBUFFERED", "1")

        process_target_obj = self._spec_data.get("process_target")
        if not isinstance(process_target_obj, str) or not process_target_obj:
            raise TypeError("process_target must be a non-empty command string")
        command: list[str] = [process_target_obj]
        raw_args = self._spec_data.get("args")
        if isinstance(raw_args, Sequence) and not isinstance(raw_args, (str, bytes)):
            command.extend(str(item) for item in raw_args)

        working_dir_obj = self._spec_data.get("working_dir")
        cwd_value: str | None = str(working_dir_obj) if working_dir_obj else None

        stdout_queue: queue.Queue[str | None] = queue.Queue()
        stderr_queue: queue.Queue[str | None] = queue.Queue()
        process: subprocess.Popen[Any]

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd_value,
            env=env_vars,
            bufsize=0,
            creationflags=creation_flags,
        )

        def _reader(stream: TextIO, target_queue: queue.Queue[str | None]) -> None:
            try:
                while True:
                    chunk = stream.read(CommandSession._READ_SIZE)
                    if chunk == "":
                        break
                    target_queue.put(chunk)
            finally:
                target_queue.put(None)

        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Failed to create pipes for interactive session")

        threading.Thread(
            target=_reader,
            args=(process.stdout, stdout_queue),
            daemon=True,
        ).start()
        threading.Thread(
            target=_reader,
            args=(process.stderr, stderr_queue),
            daemon=True,
        ).start()

        monitor = None
        if self._monitor_class:
            monitor = load_resource_monitor(
                self._monitor_class,
                limits=self._limits,
                polling_interval=self._monitor_interval,
            )
            monitor = _start_optional_monitor(monitor, process.pid)

        return CommandSession(
            process,
            stdout_queue,
            stderr_queue,
            monitor,
            handle=_host_handle(process.pid),
        )

    def start_agent_session(self) -> AgentSession:
        """Start a long-lived agent session for persistent agent tasks."""
        if self._spec_data["type"] != "agent":
            raise ValueError("Agent sessions are only supported for agent targets")

        agent_data = self._spec_data.get("agent")
        if not isinstance(agent_data, Mapping):
            raise TypeError("agent configuration is required for agent sessions")

        request_queue = self._ctx.Queue()
        response_receiver, response_sender = self._ctx.Pipe(duplex=False)
        process = self._ctx.Process(
            target=_agent_session_worker_entry,
            args=(self._spec_data, request_queue, response_sender),
            daemon=True,
        )
        try:
            process.start()
        except BaseException:
            self._close_mp_queue(request_queue)
            with contextlib.suppress(Exception):
                response_receiver.close()
            with contextlib.suppress(Exception):
                response_sender.close()
            self._close_process_handle(process)
            raise
        monitor = None
        session = None
        try:
            response_sender.close()
            if self._monitor_class:
                monitor = load_resource_monitor(
                    self._monitor_class,
                    limits=self._limits,
                    polling_interval=self._monitor_interval,
                )
                monitor = _start_optional_monitor(monitor, process.pid)
            session = AgentSession(
                process,
                request_queue,
                response_receiver,
                monitor,
                timeout=self._timeout,
                handle=_host_handle(process.pid),
            )
            ready_timeout = max(
                AGENT_SESSION_READY_TIMEOUT_SECONDS,
                self._timeout if self._timeout is not None else 0.0,
            )
            session.wait_ready(timeout=ready_timeout)
            return session
        except BaseException:  # pragma: no cover - session startup cleanup
            if session is not None:
                session.close()
            else:
                if monitor is not None:
                    try:
                        monitor.stop()
                    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-313] exception
                        logger.warning(
                            "Failed to stop host agent resource monitor during startup cleanup"
                        )
                with contextlib.suppress(Exception):
                    if process.is_alive():
                        self._stop_process(process)
                    else:
                        process.join(timeout=0.2)
                self._close_mp_queue(request_queue)
                with contextlib.suppress(Exception):
                    response_receiver.close()
                self._close_process_handle(process)
            raise


class HostRunnerPlugin:
    """Built-in host runner plugin."""

    name = "host"
    capabilities = RunnerCapabilities()

    def check_version(self) -> None:
        return None

    def validate_taskspec(
        self,
        taskspec_payload: Mapping[str, Any],
        *,
        bundle_root: str | None = None,
        preflight: bool = False,
    ) -> None:
        del taskspec_payload, bundle_root, preflight

    def create_runner(
        self,
        *,
        target_type: str,
        tid: str | None,
        function_target: str | None,
        process_target: str | None,
        agent: Mapping[str, Any] | None,
        args: Sequence[Any] | None,
        kwargs: Mapping[str, Any] | None,
        env: Mapping[str, str] | None,
        working_dir: str | None,
        timeout: float | None,
        limits: Any | None,
        monitor_class: str | None,
        monitor_interval: float | None,
        runner_options: Mapping[str, Any] | None,
        bundle_root: str | None,
        persistent: bool,
        interactive: bool,
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> HostTaskRunner:
        del persistent, interactive, runner_options, db_path, config
        return HostTaskRunner(
            target_type=target_type,
            tid=tid,
            function_target=function_target,
            process_target=process_target,
            agent=agent,
            args=args,
            kwargs=kwargs,
            env=env,
            working_dir=working_dir,
            timeout=timeout,
            limits=limits,
            monitor_class=monitor_class,
            monitor_interval=monitor_interval,
            bundle_root=bundle_root,
        )

    def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        host_processes = handle.scoped_host_processes()
        if not host_processes:
            return False
        for pid, create_time in host_processes:
            if not _host_pid_matches(pid, create_time):
                continue
            terminate_process_tree(pid, timeout=timeout)
        return True

    def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        host_processes = handle.scoped_host_processes()
        if not host_processes:
            return False
        for pid, create_time in host_processes:
            if not _host_pid_matches(pid, create_time):
                continue
            kill_process_tree(pid, timeout=timeout)
        return True

    def describe(self, handle: RunnerHandle) -> RunnerRuntimeDescription | None:
        host_processes = handle.scoped_host_processes()
        primary_pid = host_processes[0][0] if host_processes else None
        metadata: dict[str, Any] = {
            "host_pids": [pid for pid, _create_time in host_processes],
        }
        state = "missing"
        if primary_pid is not None and any(
            _host_pid_matches(pid, create_time) for pid, create_time in host_processes
        ):
            state = "running"
        elif (
            primary_pid is not None
            and (container := _current_container_runtime()) is not None
        ):
            state = "unknown"
            metadata["host_pid_visibility"] = "namespace_unobservable"
            metadata.update(container.observations())
        return RunnerRuntimeDescription(
            runner="host",
            id=handle.id,
            state=state,
            metadata=metadata,
        )


_HOST_PLUGIN = HostRunnerPlugin()


def get_runner_plugin() -> RunnerPlugin:
    return _HOST_PLUGIN
