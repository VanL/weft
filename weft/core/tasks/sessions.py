"""Interactive session helpers."""

from __future__ import annotations

import queue
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue as MPQueue
from typing import Any

from weft.core.resource_monitor import (
    BaseResourceMonitor,
    ResourceMetrics,
)
from weft.core.tasks.agent_session_protocol import (
    is_ready_response,
    make_execute_request,
    make_stop_request,
    parse_result_response,
    startup_error_message,
)
from weft.ext import RunnerHandle
from weft.helpers import terminate_process_tree


class CommandSession:
    """Interactive command execution session supporting stdin/stdout streaming."""

    _READ_SIZE = 64 * 1024

    def __init__(
        self,
        process: subprocess.Popen[str],
        stdout_queue: queue.Queue[str | None],
        stderr_queue: queue.Queue[str | None],
        monitor: BaseResourceMonitor | None,
        limits: Any,
        *,
        handle: RunnerHandle | None = None,
    ) -> None:
        self._process = process
        self._stdout_queue = stdout_queue
        self._stderr_queue = stderr_queue
        self._monitor: BaseResourceMonitor | None = monitor
        self._limits = limits
        self._handle = handle
        self._last_metrics: ResourceMetrics | None = None
        self._stdout_closed = False
        self._stderr_closed = False

    @property
    def pid(self) -> int | None:
        return self._process.pid

    @property
    def handle(self) -> RunnerHandle | None:
        return self._handle

    def send(self, data: str) -> None:
        if self._process.stdin is None:
            raise RuntimeError("Session stdin is not available")
        self._process.stdin.write(data)
        self._process.stdin.flush()

    def close_stdin(self) -> None:
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

    def terminate(self) -> None:
        if self.is_alive():
            pid = self._process.pid
            if isinstance(pid, int) and pid > 0:
                terminate_process_tree(pid, timeout=2.0)
            try:
                self._process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self._process.kill()

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
            self._last_metrics = self._monitor.last_metrics() or self._last_metrics
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


class AgentSession:
    """Managed long-lived agent worker session."""

    def __init__(
        self,
        process: BaseProcess,
        request_queue: MPQueue[Any],
        response_queue: MPQueue[dict[str, Any]],
        monitor: BaseResourceMonitor | None,
        limits: Any,
        *,
        timeout: float | None,
        handle: RunnerHandle | None = None,
    ) -> None:
        self._process = process
        self._request_queue = request_queue
        self._response_queue = response_queue
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
        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0.01)
            try:
                payload = self._response_queue.get(timeout=remaining)
            except queue.Empty:
                if not self.is_alive():
                    break
                continue
            if is_ready_response(payload):
                return
            startup_error = startup_error_message(payload)
            if startup_error is not None:
                raise RuntimeError(startup_error)
        raise RuntimeError("Agent session failed to signal readiness")

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

        while self.is_alive():
            if cancel_requested is not None and _cancel_requested(cancel_requested):
                self.terminate()
                self.stop_monitor()
                return SessionExecutionResult(
                    status="cancelled",
                    value=None,
                    error="Target execution cancelled",
                    metrics=self._last_metrics,
                )

            elapsed = time.monotonic() - start_time
            if self._timeout is not None and elapsed >= self._timeout:
                self.terminate()
                self.stop_monitor()
                return SessionExecutionResult(
                    status="timeout",
                    value=None,
                    error="Target execution timed out",
                    metrics=self._last_metrics,
                )

            remaining = 0.05
            if self._timeout is not None:
                remaining = min(remaining, max(0.01, self._timeout - elapsed))

            try:
                payload = self._response_queue.get(timeout=remaining)
            except queue.Empty:
                ok, violation = self.poll_limits()
                if not ok:
                    self.terminate()
                    self.stop_monitor()
                    return SessionExecutionResult(
                        status="limit",
                        value=None,
                        error=violation,
                        metrics=self._last_metrics,
                    )
                continue

            parsed = parse_result_response(payload)
            if parsed is None:
                continue
            status, result, error = parsed

            self._last_metrics = self.last_metrics
            return SessionExecutionResult(
                status=status,
                value=result,
                error=error,
                metrics=self._last_metrics,
            )

        self._last_metrics = self.last_metrics
        return SessionExecutionResult(
            status="error",
            value=None,
            error="Agent session exited unexpectedly",
            metrics=self._last_metrics,
        )

    def is_alive(self) -> bool:
        return self._process.is_alive()

    def terminate(self) -> None:
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
            self._last_metrics = self._monitor.last_metrics() or self._last_metrics
            try:
                self._monitor.stop()
            except Exception:  # pragma: no cover - defensive
                pass
            self._monitor = None

    @property
    def last_metrics(self) -> ResourceMetrics | None:
        if self._monitor:
            self._last_metrics = self._monitor.last_metrics() or self._last_metrics
        return self._last_metrics

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.is_alive():
                self._request_queue.put(make_stop_request())
                self._process.join(timeout=0.5)
        except Exception:  # pragma: no cover - defensive
            pass
        if self.is_alive():
            self.terminate()
        self.stop_monitor()


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

    def terminate(self) -> None:
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


def _cancel_requested(callback: Callable[[], bool]) -> bool:
    try:
        return bool(callback())
    except Exception:  # pragma: no cover - defensive
        return False
