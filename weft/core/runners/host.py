"""Built-in host runner implementation.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3], [CC-3.4]
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1]
"""

from __future__ import annotations

import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue as MPQueue
from typing import Any, TextIO

import psutil

import weft.core.agents  # noqa: F401 - register built-in agent runtimes
from simplebroker import BrokerTarget
from weft._constants import ACTIVE_CONTROL_POLL_INTERVAL
from weft.core.agent_runtime import (
    execute_agent_target,
    normalize_agent_work_item,
    start_agent_runtime_session,
)
from weft.core.resource_monitor import ResourceMetrics, load_resource_monitor
from weft.core.targets import execute_command_target, execute_function_target
from weft.core.tasks.agent_session_protocol import (
    make_ready_response,
    make_result_response,
    make_startup_error_response,
    parse_request_type,
)
from weft.core.tasks.sessions import AgentSession, CommandSession
from weft.core.taskspec import AgentSection
from weft.ext import (
    RunnerCapabilities,
    RunnerHandle,
    RunnerPlugin,
    RunnerRuntimeDescription,
)
from weft.helpers import kill_process_tree, terminate_process_tree


@dataclass(slots=True)
class RunnerOutcome:
    """Result returned after executing a work item."""

    status: str
    value: Any | None
    error: str | None
    stdout: str | None
    stderr: str | None
    returncode: int | None
    duration: float
    metrics: ResourceMetrics | None = None
    worker_pid: int | None = None
    runtime_handle: RunnerHandle | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _host_handle(pid: int | None) -> RunnerHandle | None:
    if pid is None or pid <= 0:
        return None
    return RunnerHandle(
        runner_name="host",
        runtime_id=str(pid),
        host_pids=(pid,),
    )


def _worker_entry(
    spec_data: Mapping[str, Any],
    work_item: Any,
    result_queue: MPQueue[RunnerOutcome],
) -> None:
    """Execute a single work item in a spawned process."""
    start = time.monotonic()
    status = "ok"
    value = None
    error = None
    stdout = None
    stderr = None
    returncode: int | None = 0

    try:
        if spec_data["type"] == "function":
            value = execute_function_target(
                spec_data["function_target"],
                work_item,
                args=spec_data.get("args"),
                kwargs=spec_data.get("kwargs"),
            )
        elif spec_data["type"] == "agent":
            agent = AgentSection.model_validate(spec_data["agent"])
            value = execute_agent_target(
                agent,
                work_item,
                tid=spec_data.get("tid"),
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
    except Exception:  # pragma: no cover - propagated via parent
        status = "error"
        error = traceback.format_exc()
        returncode = None

    end = time.monotonic()
    result_queue.put(
        RunnerOutcome(
            status=status,
            value=value,
            error=error,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            duration=end - start,
        )
    )


def _agent_session_worker_entry(
    spec_data: Mapping[str, Any],
    request_queue: MPQueue[dict[str, Any]],
    response_queue: MPQueue[dict[str, Any]],
) -> None:
    """Run a long-lived agent session in a spawned subprocess."""
    session = None
    try:
        agent = AgentSection.model_validate(spec_data["agent"])
        session = start_agent_runtime_session(
            agent,
            tid=spec_data.get("tid"),
        )
        response_queue.put(make_ready_response())

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
            except Exception:  # pragma: no cover - propagated via parent
                response_queue.put(
                    make_result_response(status="error", error=traceback.format_exc())
                )
                break

            response_queue.put(make_result_response(status="ok", result=result))
    except Exception:  # pragma: no cover - propagated via parent
        response_queue.put(make_startup_error_response(traceback.format_exc()))
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # pragma: no cover - defensive
                pass


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
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._spec_data = {
            "type": target_type,
            "tid": tid,
            "function_target": function_target,
            "process_target": process_target,
            "agent": dict(agent or {}) if agent is not None else None,
            "args": list(args or []),
            "kwargs": dict(kwargs or {}),
            "env": dict(env or {}),
            "working_dir": working_dir,
            "command_timeout": timeout,
        }
        self._timeout = timeout
        self._ctx = multiprocessing.get_context("spawn")
        self._ctx.set_executable(sys.executable)
        self._limits = limits
        self._monitor_class = monitor_class
        self._monitor_interval = monitor_interval or 1.0
        self._db_path = db_path
        self._config = dict(config) if config is not None else None

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
    ) -> RunnerOutcome:
        """Execute a work item with optional lifecycle hooks.

        Spec: [RM-5], [RM-5.1] (resource monitor polling and limit enforcement)
        """
        result_queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=_worker_entry,
            args=(self._spec_data, work_item, result_queue),
            daemon=True,
        )
        process.start()
        worker_pid = process.pid
        runtime_handle = _host_handle(worker_pid)
        if on_worker_started is not None:
            try:
                on_worker_started(worker_pid)
            except Exception:  # pragma: no cover - defensive
                pass
        if on_runtime_handle_started is not None and runtime_handle is not None:
            try:
                on_runtime_handle_started(runtime_handle)
            except Exception:  # pragma: no cover - defensive
                pass

        monitor = None
        last_metrics: ResourceMetrics | None = None
        outcome: RunnerOutcome | None = None
        if self._monitor_class:
            monitor = load_resource_monitor(
                self._monitor_class,
                limits=self._limits,
                polling_interval=self._monitor_interval,
                db_path=self._db_path,
                config=self._config,
            )
            try:
                if worker_pid is None:
                    raise RuntimeError("Worker process has no PID")
                monitor.start(worker_pid)
            except Exception:  # pragma: no cover
                monitor = None

        start_time = time.monotonic()
        next_monitor_at = start_time + self._monitor_interval

        while process.is_alive():
            if cancel_requested is not None and _cancel_requested(cancel_requested):
                self._stop_process(process)
                if monitor:
                    last_metrics = monitor.last_metrics()
                    monitor.stop()
                return RunnerOutcome(
                    status="cancelled",
                    value=None,
                    error="Target execution cancelled",
                    stdout=None,
                    stderr=None,
                    returncode=None,
                    duration=time.monotonic() - start_time,
                    metrics=last_metrics,
                    worker_pid=worker_pid,
                    runtime_handle=runtime_handle,
                )

            elapsed = time.monotonic() - start_time
            if self._timeout is not None and elapsed >= self._timeout:
                self._stop_process(process)
                if monitor:
                    last_metrics = monitor.last_metrics()
                    monitor.stop()
                return RunnerOutcome(
                    status="timeout",
                    value=None,
                    error="Target execution timed out",
                    stdout=None,
                    stderr=None,
                    returncode=None,
                    duration=self._timeout,
                    metrics=last_metrics,
                    worker_pid=worker_pid,
                    runtime_handle=runtime_handle,
                )

            remaining: float | None = None
            if self._timeout is not None:
                remaining = self._timeout - elapsed
                if remaining <= 0:
                    continue
            sleep_for = ACTIVE_CONTROL_POLL_INTERVAL
            if remaining is not None:
                sleep_for = min(sleep_for, max(0.01, remaining))

            try:
                outcome = result_queue.get(timeout=sleep_for)
                break
            except queue.Empty:
                pass

            if monitor and time.monotonic() >= next_monitor_at:
                try:
                    ok, violation = monitor.check_limits()
                except Exception:  # pragma: no cover - process may have exited
                    ok, violation = True, None
                last_metrics = monitor.last_metrics()
                next_monitor_at = time.monotonic() + self._monitor_interval
                if not ok:
                    self._stop_process(process)
                    last_metrics = monitor.last_metrics()
                    monitor.stop()
                    return RunnerOutcome(
                        status="limit",
                        value=None,
                        error=violation,
                        stdout=None,
                        stderr=None,
                        returncode=None,
                        duration=time.monotonic() - start_time,
                        metrics=last_metrics,
                        worker_pid=worker_pid,
                        runtime_handle=runtime_handle,
                    )

        process.join()

        if cancel_requested is not None and _cancel_requested(cancel_requested):
            if monitor:
                last_metrics = monitor.last_metrics()
                monitor.stop()
            return RunnerOutcome(
                status="cancelled",
                value=None,
                error="Target execution cancelled",
                stdout=None,
                stderr=None,
                returncode=None,
                duration=time.monotonic() - start_time,
                metrics=last_metrics,
                worker_pid=worker_pid,
                runtime_handle=runtime_handle,
            )

        if monitor:
            last_metrics = monitor.last_metrics()
            if last_metrics is None:
                try:
                    last_metrics = monitor.snapshot()
                except Exception:  # pragma: no cover
                    last_metrics = None
            monitor.stop()

        if outcome is None:
            try:
                outcome = result_queue.get_nowait()
            except queue.Empty:
                return RunnerOutcome(
                    status="error",
                    value=None,
                    error="Worker produced no result",
                    stdout=None,
                    stderr=None,
                    returncode=None,
                    duration=time.monotonic() - start_time,
                    metrics=last_metrics,
                    worker_pid=worker_pid,
                    runtime_handle=runtime_handle,
                )

        outcome.metrics = outcome.metrics or last_metrics
        outcome.worker_pid = worker_pid
        outcome.runtime_handle = outcome.runtime_handle or runtime_handle
        return outcome

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
        except Exception:  # pragma: no cover - defensive
            pass
        if not process.is_alive():
            return

        process.terminate()
        process.join(timeout=timeout)
        if process.is_alive():
            process.kill()
            process.join(timeout=timeout)

    def start_session(self) -> CommandSession:
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
                db_path=self._db_path,
                config=self._config,
            )
            try:
                pid = process.pid
                if pid is None:
                    raise RuntimeError("Interactive process has no PID")
                monitor.start(pid)
            except Exception:  # pragma: no cover
                monitor = None

        return CommandSession(
            process,
            stdout_queue,
            stderr_queue,
            monitor,
            self._limits,
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
        response_queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=_agent_session_worker_entry,
            args=(self._spec_data, request_queue, response_queue),
            daemon=True,
        )
        process.start()

        monitor = None
        if self._monitor_class:
            monitor = load_resource_monitor(
                self._monitor_class,
                limits=self._limits,
                polling_interval=self._monitor_interval,
                db_path=self._db_path,
                config=self._config,
            )
            try:
                pid = process.pid
                if pid is None:
                    raise RuntimeError("Agent session process has no PID")
                monitor.start(pid)
            except Exception:  # pragma: no cover
                monitor = None

        session = AgentSession(
            process,
            request_queue,
            response_queue,
            monitor,
            self._limits,
            timeout=self._timeout,
            handle=_host_handle(process.pid),
        )
        ready_timeout = self._timeout if self._timeout is not None else 5.0
        try:
            session.wait_ready(timeout=max(ready_timeout, 0.1))
        except Exception:
            session.close()
            raise
        return session


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
        preflight: bool = False,
    ) -> None:
        del taskspec_payload, preflight
        return None

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
        persistent: bool,
        interactive: bool,
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> HostTaskRunner:
        del persistent, interactive, runner_options
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
            db_path=db_path,
            config=config,
        )

    def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        host_pids = handle.host_pids
        if not host_pids:
            return False
        for pid in host_pids:
            terminate_process_tree(pid, timeout=timeout, kill_after=False)
        return True

    def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        host_pids = handle.host_pids
        if not host_pids:
            return False
        for pid in host_pids:
            kill_process_tree(pid, timeout=timeout)
        return True

    def describe(self, handle: RunnerHandle) -> RunnerRuntimeDescription | None:
        primary_pid = handle.primary_pid
        state = "missing"
        if primary_pid is not None and psutil.pid_exists(primary_pid):
            state = "running"
        return RunnerRuntimeDescription(
            runner_name="host",
            runtime_id=handle.runtime_id,
            state=state,
            metadata={"host_pids": list(handle.host_pids)},
        )


_HOST_PLUGIN = HostRunnerPlugin()


def get_runner_plugin() -> RunnerPlugin:
    return _HOST_PLUGIN


def _cancel_requested(callback: Callable[[], bool]) -> bool:
    try:
        return bool(callback())
    except Exception:  # pragma: no cover - defensive
        return False
