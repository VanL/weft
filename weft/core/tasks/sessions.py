"""Interactive session helpers."""

from __future__ import annotations

import queue
import subprocess
from collections.abc import Callable
from typing import Any

from weft.core.resource_monitor import (
    BaseResourceMonitor,
    ResourceMetrics,
)


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
    ) -> None:
        self._process = process
        self._stdout_queue = stdout_queue
        self._stderr_queue = stderr_queue
        self._monitor: BaseResourceMonitor | None = monitor
        self._limits = limits
        self._last_metrics: ResourceMetrics | None = None
        self._stdout_closed = False
        self._stderr_closed = False

    @property
    def pid(self) -> int | None:
        return self._process.pid

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


__all__ = ["InProcessCommandSession"]
