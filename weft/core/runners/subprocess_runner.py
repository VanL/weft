"""Shared helpers for one-shot command runners backed by external processes.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2], [CC-3.4]
- docs/specifications/06-Resource_Management.md [RM-5.1], [RM-5.2]
"""

from __future__ import annotations

import codecs
import contextlib
import errno
import io
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from typing import IO, Any, Literal

from weft._constants import (
    ACTIVE_CONTROL_POLL_INTERVAL,
    SUBPROCESS_POLL_INTERVAL_FLOOR,
    SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS,
    SUBPROCESS_STREAM_DRAIN_TIMEOUT,
    SUBPROCESS_STREAM_READ_SIZE,
    SUBPROCESS_TERMINATION_WAIT_TIMEOUT,
)
from weft.core.resource_monitor import ResourceMetrics, load_resource_monitor
from weft.core.runner_diagnostics import runner_diagnostics
from weft.core.runners.outcome import RunnerOutcome
from weft.ext import RunnerHandle
from weft.helpers import safe_cancel

logger = logging.getLogger(__name__)

_MonitorPhase = Literal[
    "startup cleanup",
    "cancellation cleanup",
    "timeout cleanup",
    "limit check",
    "limit cleanup",
    "completion cleanup",
]


def prepare_command_invocation(
    process_target: str,
    work_item: Any,
    *,
    args: Sequence[Any] | None = None,
) -> tuple[list[str], str | None]:
    """Return command argv and stdin derived from a TaskSpec work item."""

    command = [process_target]
    if args:
        command.extend(str(item) for item in args)

    stdin_data: str | None = None
    if isinstance(work_item, dict):
        extra_args = work_item.get("args")
        if isinstance(extra_args, Iterable) and not isinstance(
            extra_args, (str, bytes)
        ):
            command.extend(str(item) for item in extra_args)
        raw_stdin = work_item.get("stdin")
        if raw_stdin is not None:
            stdin_data = str(raw_stdin)
    elif isinstance(work_item, str) and work_item:
        stdin_data = work_item

    return command, stdin_data


def run_monitored_subprocess(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-036] exception
    *,
    process: subprocess.Popen[str],
    stdin_data: str | None,
    timeout: float | None,
    limits: Any | None,
    monitor_class: str | None,
    monitor_interval: float,
    monitor: Any | None,
    runtime_handle: RunnerHandle,
    cancel_requested: Callable[[], bool] | None,
    on_worker_started: Callable[[int | None], None] | None,
    on_runtime_handle_started: Callable[[RunnerHandle], None] | None,
    on_stdout_chunk: Callable[[str, bool], None] | None = None,
    on_stderr_chunk: Callable[[str, bool], None] | None = None,
    stop_runtime: Callable[[], None],
    kill_runtime: Callable[[], None],
    worker_pid: int | None = None,
) -> RunnerOutcome:
    """Run a managed subprocess with timeout, cancellation, and limit checks.

    Spec: [RM-5.1], [RM-5.2]
    """
    last_metrics: ResourceMetrics | None = None
    actual_worker_pid = worker_pid if worker_pid is not None else process.pid
    start_time = time.monotonic()
    adapters: list[_PipeAdapter] = []
    cleanup_phase: _MonitorPhase = "startup cleanup"
    primary_failure = False
    try:
        if on_worker_started is not None:
            try:
                on_worker_started(actual_worker_pid)
            except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-350] exception
                logger.warning("Subprocess worker-start callback failed")
        if on_runtime_handle_started is not None:
            try:
                on_runtime_handle_started(runtime_handle)
            except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-350] exception
                logger.warning("Subprocess runtime-handle callback failed")

        if monitor is None and monitor_class and actual_worker_pid is not None:
            monitor = load_resource_monitor(
                monitor_class,
                limits=limits,
                polling_interval=monitor_interval,
            )
        if monitor is not None:
            try:
                monitor.start(
                    actual_worker_pid if actual_worker_pid is not None else -1
                )
            except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-311] exception
                logger.warning("Subprocess resource monitor failed during startup")
                _stop_monitor(monitor, phase="startup cleanup")
                monitor = None

        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Managed subprocess requires stdout and stderr pipes")

        stdout_queue: queue.Queue[str | None] = queue.Queue()
        stderr_queue: queue.Queue[str | None] = queue.Queue()
        adapters = [
            _PipeAdapter(process.stdout, target_queue=stdout_queue),
            _PipeAdapter(process.stderr, target_queue=stderr_queue),
        ]
        if process.stdin is not None:
            adapters.append(_PipeAdapter(process.stdin, input_data=stdin_data))
        # Register ownership before start: even a start interrupted after OS
        # thread creation must be stopped and joined by the acquisition scope.
        for adapter in adapters:
            adapter.start()

        interval = monitor_interval or 1.0
        next_monitor_at = start_time + interval
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        stdout_closed = False
        stderr_closed = False

        cleanup_phase = "completion cleanup"
        while True:
            _raise_pipe_errors(adapters)
            stdout_closed = _drain_stream_queue(
                stdout_queue,
                stdout_parts,
                on_chunk=on_stdout_chunk,
                closed=stdout_closed,
            )
            stderr_closed = _drain_stream_queue(
                stderr_queue,
                stderr_parts,
                on_chunk=on_stderr_chunk,
                closed=stderr_closed,
            )

            _raise_pipe_errors(adapters)
            if safe_cancel(cancel_requested):
                cleanup_phase = "cancellation cleanup"
                _stop_process_runtime(
                    process, stop_runtime=stop_runtime, kill_runtime=kill_runtime
                )
                _finish_streams(
                    adapters,
                    stdout_queue,
                    stderr_queue,
                    stdout_parts,
                    stderr_parts,
                    on_stdout_chunk=on_stdout_chunk,
                    on_stderr_chunk=on_stderr_chunk,
                    stdout_closed=stdout_closed,
                    stderr_closed=stderr_closed,
                )
                last_metrics = _last_monitor_metrics(
                    monitor,
                    last_metrics,
                    phase="cancellation cleanup",
                )
                return RunnerOutcome(
                    status="cancelled",
                    value=None,
                    error="Target execution cancelled",
                    stdout="".join(stdout_parts),
                    stderr="".join(stderr_parts),
                    returncode=None,
                    duration=time.monotonic() - start_time,
                    metrics=last_metrics,
                    runtime_handle=runtime_handle,
                )

            process_returncode = process.poll()
            if process_returncode is not None:
                break

            elapsed = time.monotonic() - start_time
            timed_out = timeout is not None and elapsed >= timeout
            if timed_out:
                process_returncode = process.poll()
            if timed_out and process_returncode is None:
                cleanup_phase = "timeout cleanup"
                assert timeout is not None
                _kill_process_runtime(process, kill_runtime=kill_runtime)
                _finish_streams(
                    adapters,
                    stdout_queue,
                    stderr_queue,
                    stdout_parts,
                    stderr_parts,
                    on_stdout_chunk=on_stdout_chunk,
                    on_stderr_chunk=on_stderr_chunk,
                    stdout_closed=stdout_closed,
                    stderr_closed=stderr_closed,
                )
                last_metrics = _last_monitor_metrics(
                    monitor,
                    last_metrics,
                    phase="timeout cleanup",
                )
                return RunnerOutcome(
                    status="timeout",
                    value=None,
                    error="Target execution timed out",
                    stdout="".join(stdout_parts),
                    stderr="".join(stderr_parts),
                    returncode=None,
                    duration=timeout,
                    metrics=last_metrics,
                    runtime_handle=runtime_handle,
                    diagnostics=runner_diagnostics(
                        phase="execute",
                        runner=runtime_handle.runner,
                        target_type="command",
                        pid=actual_worker_pid,
                        exitcode=process.returncode,
                        alive=process.poll() is None,
                        duration_seconds=elapsed,
                        timeout_seconds=timeout,
                        message="Target execution timed out",
                    ),
                )

            sleep_for = ACTIVE_CONTROL_POLL_INTERVAL
            if timeout is not None:
                remaining = timeout - elapsed
                sleep_for = min(
                    sleep_for, max(SUBPROCESS_POLL_INTERVAL_FLOOR, remaining)
                )
            if process_returncode is not None:
                sleep_for = min(sleep_for, SUBPROCESS_POLL_INTERVAL_FLOOR)
            if monitor is not None:
                until_monitor = max(
                    SUBPROCESS_POLL_INTERVAL_FLOOR,
                    next_monitor_at - time.monotonic(),
                )
                sleep_for = min(sleep_for, until_monitor)

            if (
                monitor is not None
                and process.poll() is None
                and time.monotonic() >= next_monitor_at
            ):
                ok, violation = True, None
                try:
                    ok, violation = monitor.check_limits()
                except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-311] exception
                    logger.warning(
                        "Subprocess resource monitor failed during limit check"
                    )
                last_metrics = _last_monitor_metrics(
                    monitor,
                    last_metrics,
                    phase="limit check",
                )
                next_monitor_at = time.monotonic() + interval
                if not ok:
                    cleanup_phase = "limit cleanup"
                    _kill_process_runtime(process, kill_runtime=kill_runtime)
                    _finish_streams(
                        adapters,
                        stdout_queue,
                        stderr_queue,
                        stdout_parts,
                        stderr_parts,
                        on_stdout_chunk=on_stdout_chunk,
                        on_stderr_chunk=on_stderr_chunk,
                        stdout_closed=stdout_closed,
                        stderr_closed=stderr_closed,
                    )
                    last_metrics = _last_monitor_metrics(
                        monitor,
                        last_metrics,
                        phase="limit cleanup",
                    )
                    return RunnerOutcome(
                        status="limit",
                        value=None,
                        error=violation,
                        stdout="".join(stdout_parts),
                        stderr="".join(stderr_parts),
                        returncode=None,
                        duration=time.monotonic() - start_time,
                        metrics=last_metrics,
                        runtime_handle=runtime_handle,
                        diagnostics=runner_diagnostics(
                            phase="execute",
                            runner=runtime_handle.runner,
                            target_type="command",
                            pid=actual_worker_pid,
                            exitcode=process.returncode,
                            alive=process.poll() is None,
                            duration_seconds=time.monotonic() - start_time,
                            message=violation,
                        ),
                    )

            # As sole consumer, a non-empty queue guarantees next-pass progress.
            # A put racing with an empty check costs at most one normal poll.
            if stdout_queue.empty() and stderr_queue.empty():
                time.sleep(sleep_for)

        _finish_streams(
            adapters,
            stdout_queue,
            stderr_queue,
            stdout_parts,
            stderr_parts,
            on_stdout_chunk=on_stdout_chunk,
            on_stderr_chunk=on_stderr_chunk,
            stdout_closed=stdout_closed,
            stderr_closed=stderr_closed,
        )
        stdout = "".join(stdout_parts)
        stderr = "".join(stderr_parts)

        last_metrics = _last_monitor_metrics(
            monitor,
            last_metrics,
            phase="completion cleanup",
        )
        if monitor is not None and last_metrics is None:
            try:
                last_metrics = monitor.snapshot()
            except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-311] exception
                logger.warning(
                    "Subprocess resource monitor failed during final snapshot"
                )

        returncode = process.returncode
        if returncode is None:
            return RunnerOutcome(
                status="error",
                value=None,
                error="Worker produced no result",
                stdout=stdout,
                stderr=stderr,
                returncode=None,
                duration=time.monotonic() - start_time,
                metrics=last_metrics,
                runtime_handle=runtime_handle,
                diagnostics=runner_diagnostics(
                    phase="execute",
                    runner=runtime_handle.runner,
                    target_type="command",
                    pid=actual_worker_pid,
                    exitcode=returncode,
                    alive=process.poll() is None,
                    duration_seconds=time.monotonic() - start_time,
                    message="Worker produced no result",
                ),
            )

        if returncode != 0:
            message = f"Command exited with {returncode}: {(stderr or '').strip()}"
            return RunnerOutcome(
                status="error",
                value=None,
                error=message,
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
                duration=time.monotonic() - start_time,
                metrics=last_metrics,
                runtime_handle=runtime_handle,
                diagnostics=runner_diagnostics(
                    phase="execute",
                    runner=runtime_handle.runner,
                    target_type="command",
                    pid=actual_worker_pid,
                    exitcode=returncode,
                    alive=False,
                    duration_seconds=time.monotonic() - start_time,
                    message=message,
                ),
            )

        return RunnerOutcome(
            status="ok",
            value=stdout.strip() if stdout is not None else "",
            error=None,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            duration=time.monotonic() - start_time,
            metrics=last_metrics,
            runtime_handle=runtime_handle,
        )

    except BaseException:
        primary_failure = True
        with contextlib.suppress(BaseException):
            _cleanup_after_execution_failure(
                process, stop_runtime=stop_runtime, kill_runtime=kill_runtime
            )
        raise
    finally:
        _stop_and_join_pipe_adapters(adapters)
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is not None and not stream.closed:
                _close_pipe(stream)
        if not primary_failure:
            _stop_monitor(monitor, phase=cleanup_phase)
            _raise_pipe_errors(adapters)
        else:
            # A fatal cleanup signal cannot replace the failure being unwound.
            with contextlib.suppress(BaseException):
                _stop_monitor(monitor, phase=cleanup_phase)


def _cleanup_after_execution_failure(
    process: subprocess.Popen[str],
    *,
    stop_runtime: Callable[[], None],
    kill_runtime: Callable[[], None],
) -> None:
    """Reap a post-spawn acquisition or execution failure without masking it."""

    with contextlib.suppress(BaseException):
        _stop_process_runtime(
            process,
            stop_runtime=stop_runtime,
            kill_runtime=kill_runtime,
        )

    process_alive = True
    with contextlib.suppress(BaseException):
        process_alive = process.poll() is None
    if not process_alive:
        return

    with contextlib.suppress(BaseException):
        process.kill()
    with contextlib.suppress(BaseException):
        process.wait(timeout=SUBPROCESS_TERMINATION_WAIT_TIMEOUT)


def _stop_process_runtime(
    process: subprocess.Popen[str],
    *,
    stop_runtime: Callable[[], None],
    kill_runtime: Callable[[], None],
) -> None:
    stop_runtime()
    try:
        process.wait(timeout=SUBPROCESS_TERMINATION_WAIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_process_runtime(process, kill_runtime=kill_runtime)


def _kill_process_runtime(
    process: subprocess.Popen[str],
    *,
    kill_runtime: Callable[[], None],
) -> None:
    kill_runtime()
    try:
        process.wait(timeout=SUBPROCESS_TERMINATION_WAIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=SUBPROCESS_TERMINATION_WAIT_TIMEOUT)


class _PipeAdapter:
    """Own one cancellable raw pipe endpoint for a command [RM-5.2].

    The wrapper is used only for encoding metadata and owner-thread close, never
    buffered reads or writes. Python 3.12+ supports nonblocking pipes on Windows
    as well as POSIX. Each would-block wait is interrupted by the stop event.
    """

    def __init__(
        self,
        stream: IO[str],
        *,
        target_queue: queue.Queue[str | None] | None = None,
        input_data: str | None = None,
    ) -> None:
        self.stream = stream
        self.descriptor = stream.fileno()
        self.encoding: str = getattr(stream, "encoding", None) or "utf-8"
        self.errors: str = getattr(stream, "errors", None) or "replace"
        self.target_queue = target_queue
        self.input_data = input_data
        self.stop = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, name="weft-subprocess-pipe")

    def start(self) -> None:
        os.set_blocking(self.descriptor, False)
        self.thread.start()

    def _run(self) -> None:
        try:
            if self.target_queue is None:
                self._write_input()
            else:
                self._read_output(self.target_queue)
        except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-354] exception
            # The supervisor owns failure handling; a failed adapter cannot
            # silently turn incomplete I/O into a successful command result.
            self.error = exc
        finally:
            _close_pipe(self.stream)

    def _read_output(self, target_queue: queue.Queue[str | None]) -> None:
        decoder = io.IncrementalNewlineDecoder(
            codecs.getincrementaldecoder(self.encoding)(errors=self.errors),
            translate=True,
        )
        while not self.stop.is_set():
            try:
                raw_chunk = os.read(self.descriptor, SUBPROCESS_STREAM_READ_SIZE)
            except BlockingIOError:
                self.stop.wait(SUBPROCESS_POLL_INTERVAL_FLOOR)
                continue
            chunk = decoder.decode(raw_chunk, final=not raw_chunk)
            if chunk:
                target_queue.put(chunk)
            if not raw_chunk:
                # Forced stop and real EOF are distinct. Only an actual EOF
                # flushes the decoder and emits the public final callback marker.
                target_queue.put(None)
                return

    def _write_input(self) -> None:
        if self.input_data is None:
            return
        encoder = codecs.getincrementalencoder(self.encoding)(errors=self.errors)
        offset = 0
        while not self.stop.is_set():
            chunk = self.input_data[offset : offset + SUBPROCESS_STREAM_READ_SIZE]
            offset += len(chunk)
            final = offset == len(self.input_data)
            # Popen's text=True wrapper defaults to newline=None: on Windows it
            # translates every LF to CRLF, including the LF in an existing CRLF.
            pending = memoryview(encoder.encode(chunk.replace("\n", os.linesep), final))
            while pending and not self.stop.is_set():
                try:
                    written = os.write(
                        self.descriptor, pending[:SUBPROCESS_STREAM_READ_SIZE]
                    )
                except BlockingIOError:
                    self.stop.wait(SUBPROCESS_POLL_INTERVAL_FLOOR)
                    continue
                except BrokenPipeError:
                    return
                except OSError as exc:
                    # CPython's Popen._stdin_write also accepts Windows EINVAL
                    # when the child has exited or closed stdin (bpo-19612/30418).
                    if sys.platform == "win32" and exc.errno == errno.EINVAL:
                        return
                    raise
                if not written:
                    self.stop.wait(SUBPROCESS_POLL_INTERVAL_FLOOR)
                pending = pending[written:]
            if final:
                return


def _raise_pipe_errors(adapters: Sequence[_PipeAdapter]) -> None:
    for adapter in adapters:
        if adapter.error is not None:
            raise adapter.error


def _stop_and_join_pipe_adapters(adapters: Sequence[_PipeAdapter]) -> None:
    """Stop all producers before joining their endpoint-close paths [RM-5.2]."""
    for adapter in adapters:
        adapter.stop.set()
    for adapter in adapters:
        if adapter.thread.ident is not None:
            adapter.thread.join()


def _close_pipe(stream: IO[str]) -> None:
    try:
        stream.close()
    except OSError:  # pragma: no cover - OS endpoint close failure
        logger.warning("Failed to close an owned subprocess pipe")


def _drain_stream_queue(
    pending_queue: queue.Queue[str | None],
    collected: list[str],
    *,
    on_chunk: Callable[[str, bool], None] | None,
    closed: bool,
) -> bool:
    for _ in range(SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS):
        try:
            item = pending_queue.get_nowait()
        except queue.Empty:
            break

        if item is None:
            if not closed and on_chunk is not None:
                on_chunk("", True)
            closed = True
            continue

        collected.append(item)
        if on_chunk is not None:
            on_chunk(item, False)

    return closed


def _finish_streams(
    adapters: Sequence[_PipeAdapter],
    stdout_queue: queue.Queue[str | None],
    stderr_queue: queue.Queue[str | None],
    stdout_parts: list[str],
    stderr_parts: list[str],
    *,
    on_stdout_chunk: Callable[[str, bool], None] | None,
    on_stderr_chunk: Callable[[str, bool], None] | None,
    stdout_closed: bool,
    stderr_closed: bool,
) -> None:
    """Bound further capture, then deliver the frozen backlog [RM-5.2].

    Waiting for reader completion shares one deadline. Once all producers are
    joined, delivery is finite and cannot be cut short by a slow callback.
    Only actual EOF markers close streams; stopping a reader does not create one.
    """
    deadline = time.monotonic() + SUBPROCESS_STREAM_DRAIN_TIMEOUT
    for adapter in adapters:
        if adapter.target_queue is not None:
            adapter.thread.join(timeout=max(0.0, deadline - time.monotonic()))
    _stop_and_join_pipe_adapters(adapters)
    _raise_pipe_errors(adapters)

    # No producer remains. A slow sink affects delivery time, not which already
    # captured bytes belong to this outcome. Keep per-stream fairness without
    # waiting between batches or treating a missing EOF marker as completion.
    while not (stdout_queue.empty() and stderr_queue.empty()):
        stdout_closed = _drain_stream_queue(
            stdout_queue,
            stdout_parts,
            on_chunk=on_stdout_chunk,
            closed=stdout_closed,
        )
        stderr_closed = _drain_stream_queue(
            stderr_queue,
            stderr_parts,
            on_chunk=on_stderr_chunk,
            closed=stderr_closed,
        )
    if not (stdout_closed and stderr_closed):
        logger.warning("Subprocess stream capture ended before stdout/stderr EOF")


def _last_monitor_metrics(
    monitor: Any,
    last_metrics: ResourceMetrics | None,
    *,
    phase: _MonitorPhase,
) -> ResourceMetrics | None:
    if monitor is None:
        return last_metrics
    try:
        return monitor.last_metrics() or last_metrics
    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-311] exception
        logger.warning(
            "Subprocess resource monitor failed while reading metrics during %s",
            phase,
        )
        return last_metrics


def _stop_monitor(
    monitor: Any,
    *,
    phase: _MonitorPhase,
) -> None:
    if monitor is None:
        return
    try:
        monitor.stop()
    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-311] exception
        logger.warning(
            "Subprocess resource monitor failed while stopping during %s",
            phase,
        )
