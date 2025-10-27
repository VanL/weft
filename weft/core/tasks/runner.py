"""Process orchestration utilities for task execution.

Implements the execution model described in
docs/specifications/01-Core_Components.md [CC-3] and
docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1].
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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from multiprocessing.queues import Queue as MPQueue
from typing import Any, TextIO

from weft.core.resource_monitor import (
    ResourceMetrics,
    load_resource_monitor,
)
from weft.core.targets import execute_command_target, execute_function_target

from .sessions import CommandSession


@dataclass(slots=True)
class RunnerOutcome:
    """Result returned by :class:`TaskRunner` after executing a work item (Spec: [CC-3])."""

    status: str
    value: Any | None
    error: str | None
    stdout: str | None
    stderr: str | None
    returncode: int | None
    duration: float
    metrics: ResourceMetrics | None = None
    worker_pid: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _worker_entry(
    spec_data: Mapping[str, Any],
    work_item: Any,
    result_queue: MPQueue[RunnerOutcome],
) -> None:
    """Execute a single work item in a spawned process (Spec: [CC-3])."""
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
        else:
            completed = execute_command_target(
                spec_data["process_target"],
                work_item,
                env=spec_data.get("env") or {},
                working_dir=spec_data.get("working_dir"),
                timeout=spec_data.get("command_timeout"),
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


class TaskRunner:
    """Managed wrapper around multiprocessing for TaskSpec target execution (Spec: [CC-3], [RM-5])."""

    def __init__(
        self,
        *,
        target_type: str,
        function_target: str | None,
        process_target: Sequence[str] | None,
        args: Sequence[Any] | None,
        kwargs: Mapping[str, Any] | None,
        env: Mapping[str, str] | None,
        working_dir: str | None,
        timeout: float | None,
        limits: Any | None,
        monitor_class: str | None,
        monitor_interval: float | None,
    ) -> None:
        self._spec_data = {
            "type": target_type,
            "function_target": function_target,
            "process_target": list(process_target or []),
            "args": list(args or []),
            "kwargs": dict(kwargs or {}),
            "env": dict(env or {}),
            "working_dir": working_dir,
            "command_timeout": timeout,
        }
        self._timeout = timeout
        self._ctx = multiprocessing.get_context("spawn")
        self._limits = limits
        self._monitor_class = monitor_class
        self._monitor_interval = monitor_interval or 0.5

    def run(self, work_item: Any) -> RunnerOutcome:
        """Execute *work_item* with resource monitoring and timeout handling.

        Spec: [CC-3], [RM-5], [RM-5.1]
        """
        result_queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=_worker_entry,
            args=(self._spec_data, work_item, result_queue),
            daemon=True,
        )
        process.start()
        worker_pid = process.pid
        monitor = None
        last_metrics: ResourceMetrics | None = None
        outcome: RunnerOutcome | None = None
        if self._monitor_class:
            monitor = load_resource_monitor(self._monitor_class)
            try:
                if worker_pid is None:
                    raise RuntimeError("Worker process has no PID")
                monitor.start(worker_pid)
            except Exception:  # pragma: no cover
                monitor = None

        start_time = time.monotonic()
        interval = self._monitor_interval

        while process.is_alive():
            elapsed = time.monotonic() - start_time
            if self._timeout is not None and elapsed >= self._timeout:
                process.terminate()
                process.join()
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
                )

            remaining: float | None = None
            if self._timeout is not None:
                remaining = self._timeout - elapsed
                if remaining <= 0:
                    continue
            sleep_for = interval
            if remaining is not None:
                sleep_for = min(interval, max(0.01, remaining))

            if outcome is None:
                try:
                    outcome = result_queue.get(timeout=sleep_for)
                    break
                except queue.Empty:
                    pass
            else:
                time.sleep(sleep_for)

            if monitor:
                try:
                    ok, violation = monitor.check_limits(self._limits)
                except Exception:  # pragma: no cover - process may have exited
                    ok, violation = True, None
                last_metrics = monitor.last_metrics()
                if not ok:
                    process.terminate()
                    process.join()
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
                    )

        process.join()

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
                )

        outcome.metrics = outcome.metrics or last_metrics
        outcome.worker_pid = worker_pid
        return outcome

    def start_session(self) -> CommandSession:
        """Start an interactive command session for streaming IO."""

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

        process_target_obj = self._spec_data.get("process_target")
        if not isinstance(process_target_obj, (list, tuple)):
            raise TypeError("process_target must be a sequence of command arguments")
        command: list[str] = [str(part) for part in process_target_obj]

        working_dir_obj = self._spec_data.get("working_dir")
        cwd_value: str | None = str(working_dir_obj) if working_dir_obj else None

        # Windows-specific subprocess flags for proper console handling
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
            bufsize=0,  # Unbuffered for interactive communication
            creationflags=creation_flags,
        )

        stdout_queue: queue.Queue[str | None] = queue.Queue()
        stderr_queue: queue.Queue[str | None] = queue.Queue()

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
            monitor = load_resource_monitor(self._monitor_class)
            try:
                pid = process.pid
                if pid is None:
                    raise RuntimeError("Interactive process has no PID")
                monitor.start(pid)
            except Exception:  # pragma: no cover - fall back without monitoring
                monitor = None

        return CommandSession(
            process, stdout_queue, stderr_queue, monitor, self._limits
        )


__all__ = ["TaskRunner", "RunnerOutcome", "CommandSession"]
