"""Shared helpers for one-shot command runners backed by external processes.

The shared supervisor (`run_monitored_subprocess`) is a single-arbiter
readiness reactor. Subprocess pipe I/O is owned by an opaque platform
readiness adapter (`_open_process_io`): `_PosixSelectorProcessIO`
demultiplexes non-blocking pipes and a kernel child-exit source on the
supervisor thread, and `_WindowsOverlappedProcessIO` waits on overlapped pipe
completions together with the process handle. Neither starts a helper thread
or polls pipes on a timer; the only timers are the supervision deadlines
(execution timeout, resource-monitor sample interval, and the pull-style
cancellation predicate). Supervised processes are constructed through
`open_supervised_process` so the Windows leaf owns overlapped pipe creation.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2], [CC-3.4]
- docs/specifications/06-Resource_Management.md [RM-5.1], [RM-5.2]
- docs/program-theory.md [WT-2] item 4, [ALT-WT-021]
"""

from __future__ import annotations

import codecs
import collections
import contextlib
import errno
import io
import locale
import logging
import math
import os
import select
import selectors
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import IO, Any, Final, Literal, Protocol, cast

from weft._constants import (
    ACTIVE_CONTROL_POLL_INTERVAL,
    SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS,
    SUBPROCESS_STREAM_DRAIN_TIMEOUT,
    SUBPROCESS_STREAM_READ_SIZE,
    SUBPROCESS_STREAM_TURN_READ_BYTES,
    SUBPROCESS_TERMINATION_WAIT_TIMEOUT,
)
from weft.core.resource_monitor import ResourceMetrics, load_resource_monitor
from weft.core.runner_diagnostics import runner_diagnostics
from weft.core.runners.outcome import RunnerOutcome
from weft.ext import RunnerHandle
from weft.helpers import safe_cancel

if sys.platform == "win32":
    import _winapi
    import msvcrt
    from asyncio import windows_utils

logger = logging.getLogger(__name__)

_MonitorPhase = Literal[
    "startup cleanup",
    "cancellation cleanup",
    "timeout cleanup",
    "limit check",
    "limit cleanup",
    "completion cleanup",
]

_OutputCallback = Callable[[str, bool], None]

_CHILD_EXIT: Final[object] = object()
"""Selector/wait-set tag for the child-exit source; it carries no data."""


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

    Each reactor turn checks cancellation, root exit, the execution timeout,
    and the resource monitor, then blocks in ``wait_and_drain`` until pipe
    readiness, EOF, child exit, or the next supervision deadline. Every
    terminal decision runs the adapter's capture, endpoint-release, and
    delivery sequence before the outcome is formed.

    Spec: [RM-5.1], [RM-5.2]
    """
    last_metrics: ResourceMetrics | None = None
    actual_worker_pid = worker_pid if worker_pid is not None else process.pid
    start_time = time.monotonic()
    io_session: _ProcessIO | None = None
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

        # The session registers its child-exit watch before this loop's first
        # process.poll(): an exit before registration is caught by that poll,
        # an exit after it wakes the readiness wait.
        io_session = _open_process_io(
            process,
            stdin_data=stdin_data,
            on_stdout_chunk=on_stdout_chunk,
            on_stderr_chunk=on_stderr_chunk,
        )

        interval = monitor_interval or 1.0
        next_monitor_at = start_time + interval

        cleanup_phase = "completion cleanup"
        while True:
            if safe_cancel(cancel_requested):
                cleanup_phase = "cancellation cleanup"
                _stop_process_runtime(
                    process, stop_runtime=stop_runtime, kill_runtime=kill_runtime
                )
                stdout, stderr = io_session.finish_after_exit()
                last_metrics = _last_monitor_metrics(
                    monitor,
                    last_metrics,
                    phase="cancellation cleanup",
                )
                return RunnerOutcome(
                    status="cancelled",
                    value=None,
                    error="Target execution cancelled",
                    stdout=stdout,
                    stderr=stderr,
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
                stdout, stderr = io_session.finish_after_exit()
                last_metrics = _last_monitor_metrics(
                    monitor,
                    last_metrics,
                    phase="timeout cleanup",
                )
                return RunnerOutcome(
                    status="timeout",
                    value=None,
                    error="Target execution timed out",
                    stdout=stdout,
                    stderr=stderr,
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
                    stdout, stderr = io_session.finish_after_exit()
                    last_metrics = _last_monitor_metrics(
                        monitor,
                        last_metrics,
                        phase="limit cleanup",
                    )
                    return RunnerOutcome(
                        status="limit",
                        value=None,
                        error=violation,
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
                            exitcode=process.returncode,
                            alive=process.poll() is None,
                            duration_seconds=time.monotonic() - start_time,
                            message=violation,
                        ),
                    )

            io_session.wait_and_drain(
                _supervision_wait_timeout(
                    cancel_requested=cancel_requested,
                    remaining_timeout=None if timeout is None else timeout - elapsed,
                    next_monitor_at=None if monitor is None else next_monitor_at,
                )
            )

        stdout, stderr = io_session.finish_after_exit()

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
            cleanup_after_execution_failure(
                process, stop_runtime=stop_runtime, kill_runtime=kill_runtime
            )
        raise
    finally:
        if io_session is not None:
            io_session.close()
        close_supervised_pipes(
            *(getattr(process, name, None) for name in ("stdin", "stdout", "stderr"))
        )
        if not primary_failure:
            _stop_monitor(monitor, phase=cleanup_phase)
        else:
            # A fatal cleanup signal cannot replace the failure being unwound.
            with contextlib.suppress(BaseException):
                _stop_monitor(monitor, phase=cleanup_phase)


def _supervision_wait_timeout(
    *,
    cancel_requested: Callable[[], bool] | None,
    remaining_timeout: float | None,
    next_monitor_at: float | None,
) -> float | None:
    """Return the readiness-wait ceiling: the next supervision deadline.

    Pipe output, EOF, and child exit wake the wait on their own, so only
    conditions that cannot notify the reactor contribute a timer [WT-7]: the
    pull-style ``cancel_requested`` predicate (no waitable cancellation exists
    in the runner plugin protocol), the execution timeout, and the next
    resource-monitor sample. ``None`` waits for readiness alone.

    Spec: [RM-5.2]
    """
    deadlines: list[float] = []
    if cancel_requested is not None:
        deadlines.append(ACTIVE_CONTROL_POLL_INTERVAL)
    if remaining_timeout is not None:
        deadlines.append(max(0.0, remaining_timeout))
    if next_monitor_at is not None:
        deadlines.append(max(0.0, next_monitor_at - time.monotonic()))
    return min(deadlines) if deadlines else None


def cleanup_after_execution_failure(
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


def close_supervised_pipes(*streams: IO[str] | None) -> None:
    """Close owned process endpoints, including before supervisor acquisition.

    Spec: [RM-5.2]
    """
    for stream in streams:
        if stream is not None and not stream.closed:
            _close_pipe(stream)


def _close_pipe(stream: IO[str]) -> None:
    try:
        stream.close()
    except OSError:  # pragma: no cover - OS endpoint close failure
        logger.warning("Failed to close an owned subprocess pipe")


class _ProcessIO(Protocol):
    """Opaque platform readiness adapter for one supervised subprocess [RM-5.2]."""

    def wait_and_drain(self, timeout: float | None) -> None:
        """Wait for readiness, EOF, child exit, or ``timeout``; do one turn."""
        ...

    def finish_after_exit(self) -> tuple[str, str]:
        """Capture, release endpoints, deliver the frozen backlog, return output."""
        ...

    def close(self) -> None:
        """Release every owned endpoint and wait source (idempotent)."""
        ...

    def owned_threads(self) -> tuple[threading.Thread, ...]:
        """Return helper threads the session owns; always empty by design."""
        ...


def _open_process_io(
    process: subprocess.Popen[str],
    *,
    stdin_data: str | None,
    on_stdout_chunk: _OutputCallback | None,
    on_stderr_chunk: _OutputCallback | None,
) -> _ProcessIO:
    """Open the platform readiness adapter for ``process``.

    Spec: [RM-5.2]
    """
    if sys.platform == "win32":
        return _WindowsOverlappedProcessIO(
            process,
            stdin_data=stdin_data,
            on_stdout_chunk=on_stdout_chunk,
            on_stderr_chunk=on_stderr_chunk,
        )
    return _PosixSelectorProcessIO(
        process,
        stdin_data=stdin_data,
        on_stdout_chunk=on_stdout_chunk,
        on_stderr_chunk=on_stderr_chunk,
    )


class _OutputStreamState:
    """One supervised output pipe between the kernel and its chunk callback.

    ``pending_chunks`` holds decoded, undelivered chunks; ``None`` is the true
    EOF marker. ``in_flight_bytes`` counts their consumed raw bytes plus the
    decoder's retained raw buffer. Consumed bytes producing no text are freed;
    chunk credits are freed on delivery. A pending newline CR is constant-size
    decoder metadata, not buffered raw input. The active-phase bound and the
    decoder's progress limit use SUBPROCESS_STREAM_TURN_READ_BYTES [RM-5.2].
    """

    def __init__(self, stream: IO[str], on_chunk: _OutputCallback | None) -> None:
        self.stream = stream
        self.descriptor = stream.fileno()
        encoding: str = getattr(stream, "encoding", None) or "utf-8"
        errors: str = getattr(stream, "errors", None) or "replace"
        self.decoder = io.IncrementalNewlineDecoder(
            codecs.getincrementaldecoder(encoding)(errors=errors),
            translate=True,
        )
        self.collected: list[str] = []
        self.pending_chunks: collections.deque[str | None] = collections.deque()
        self.pending_sizes: collections.deque[int] = collections.deque()
        self.in_flight_bytes = 0
        self.on_chunk = on_chunk
        self.eof_reached = False
        self.closed_callback_sent = False
        self.closed = False

    def capacity(self) -> int:
        """Return how many more undelivered bytes the in-flight bound admits."""
        return max(0, SUBPROCESS_STREAM_TURN_READ_BYTES - self.in_flight_bytes)

    def accept(self, data: bytes) -> None:
        """Decode raw bytes and queue them for delivery; ``b""`` is true EOF."""
        if not data:
            self.accept_eof()
            return
        offset = 0
        while offset < len(data):
            remaining = SUBPROCESS_STREAM_TURN_READ_BYTES - len(
                self.decoder.getstate()[0]
            )
            end = min(len(data), offset + remaining)
            self._decode(data[offset:end], final=False)
            offset = end

    def _decode(self, data: bytes, *, final: bool) -> None:
        previous = len(self.decoder.getstate()[0])
        chunk = self.decoder.decode(data, final=final)
        retained = len(self.decoder.getstate()[0])
        self.in_flight_bytes += retained - previous
        if chunk:
            consumed = previous + len(data) - retained
            self.pending_chunks.append(chunk)
            self.pending_sizes.append(consumed)
            self.in_flight_bytes += consumed
        # getstate()[0] is undecoded raw input. Newline translation's pending
        # CR is a separate, fixed-size character flag, not a raw byte buffer.
        # Consumed bytes producing no text (e.g. errors=ignore) need no credit.
        if retained >= SUBPROCESS_STREAM_TURN_READ_BYTES:
            raise UnicodeError(
                "Subprocess decoder buffer limit reached "
                f"({SUBPROCESS_STREAM_TURN_READ_BYTES} bytes)"
            )

    def accept_eof(self) -> None:
        """Flush the decoder and queue the EOF marker exactly once."""
        if self.eof_reached:
            return
        self.eof_reached = True
        self._decode(b"", final=True)
        self.pending_chunks.append(None)
        self.pending_sizes.append(0)

    def deliver(self, max_chunks: int, turn_deadline: float) -> None:
        """Deliver up to ``max_chunks``, yielding once ``turn_deadline`` passes."""
        for _ in range(max_chunks):
            if not self.pending_chunks:
                return
            chunk = self.pending_chunks.popleft()
            self.in_flight_bytes -= self.pending_sizes.popleft()
            if chunk is None:
                # Only an actual EOF emits the public final callback marker.
                if not self.closed_callback_sent and self.on_chunk is not None:
                    self.on_chunk("", True)
                self.closed_callback_sent = True
            else:
                self.collected.append(chunk)
                if self.on_chunk is not None:
                    self.on_chunk(chunk, False)
            if time.monotonic() >= turn_deadline:
                return


class _StdinStreamState:
    """The supervised stdin pipe and the work item's remaining input."""

    def __init__(self, stream: IO[str], input_data: str) -> None:
        self.stream = stream
        self.descriptor = stream.fileno()
        encoding: str = getattr(stream, "encoding", None) or "utf-8"
        errors: str = getattr(stream, "errors", None) or "replace"
        self.encoder = codecs.getincrementalencoder(encoding)(errors=errors)
        self.input_data = input_data
        self.offset = 0
        self.pending = b""
        # Encode the first slice now so invalid input fails before execution.
        self._encode_next()

    def _encode_next(self) -> bool:
        if self.offset >= len(self.input_data):
            return False
        chunk = self.input_data[self.offset : self.offset + SUBPROCESS_STREAM_READ_SIZE]
        self.offset += len(chunk)
        final = self.offset == len(self.input_data)
        # Popen's text=True wrapper defaults to newline=None: on Windows it
        # translates every LF to CRLF, including the LF in an existing CRLF.
        self.pending = self.encoder.encode(chunk.replace("\n", os.linesep), final)
        return True

    def next_slice(self, budget: int) -> bytes | None:
        """Return up to ``budget`` encoded bytes to write, or None when done."""
        while not self.pending:
            if not self._encode_next():
                return None
        return self.pending[: min(SUBPROCESS_STREAM_READ_SIZE, budget)]

    def advance(self, written: int) -> None:
        self.pending = self.pending[written:]


def _turn_deadline(timeout: float | None) -> float:
    """Return when this turn's delivery must yield back to supervision."""
    if timeout is None:
        return math.inf
    return time.monotonic() + max(0.0, timeout)


def _deliver_frozen(streams: Sequence[_OutputStreamState]) -> None:
    """Deliver a frozen backlog in interleaved batches without idle waits."""
    while any(stream.pending_chunks for stream in streams):
        for stream in streams:
            stream.deliver(SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS, math.inf)
    if not all(stream.eof_reached for stream in streams):
        logger.warning("Subprocess stream capture ended before stdout/stderr EOF")


class _ExitSource(Protocol):
    def fileno(self) -> int: ...

    def close(self) -> None: ...


class _DescriptorExitSource:
    """An owned child-exit descriptor (a Linux pidfd)."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def fileno(self) -> int:
        return self._descriptor

    def close(self) -> None:
        os.close(self._descriptor)


def _open_child_exit_source(pid: int) -> _ExitSource | None:
    """Open a selectable kernel object that becomes readable when ``pid`` exits.

    Linux uses a pidfd; macOS and BSD use a kqueue holding a one-shot
    process-exit filter, whose own descriptor joins the selector. Neither
    reaps the child or touches ``Popen._waitpid_lock``. ``None`` means the
    child is already gone, which the caller treats as an immediate wake.

    Spec: [RM-5.2]
    """
    if sys.platform == "linux":
        try:
            return _DescriptorExitSource(os.pidfd_open(pid))
        except ProcessLookupError:
            return None
        except (AttributeError, OSError) as exc:
            if isinstance(exc, OSError) and exc.errno not in (
                errno.ENOSYS,
                errno.EPERM,
                errno.EACCES,
            ):
                raise
            raise RuntimeError(
                "Subprocess supervision requires Linux 5.3+ with pidfd_open "
                "available in Python and permitted by the container/security policy: "
                f"{exc}"
            ) from exc
    if sys.platform == "win32":
        raise NotImplementedError("the Windows leaf waits on the process handle")
    kq = select.kqueue()
    try:
        kq.control(
            [
                select.kevent(
                    pid,
                    filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                    fflags=select.KQ_NOTE_EXIT,
                )
            ],
            0,
            0,
        )
    except ProcessLookupError:
        kq.close()
        return None
    except BaseException:
        kq.close()
        raise
    return kq


class _PosixSelectorProcessIO:
    """POSIX platform readiness adapter for one supervised subprocess [RM-5.2].

    All pipe I/O runs on the supervisor thread. One selector demultiplexes
    non-blocking stdout/stderr (EVENT_READ), stdin (EVENT_WRITE), and a kernel
    child-exit source, so a silent child costs zero wakeups and no helper
    thread or queue exists. Child exit is a pidfd on Linux and a kqueue
    process filter on macOS/BSD. No thread may park in ``Popen.wait()``: it
    holds ``_waitpid_lock`` across the blocking ``waitpid``, and
    ``Popen.poll()`` returns None while that lock is held, which would blind
    the supervisor, the sole caller of ``process.poll()``.
    """

    def __init__(
        self,
        process: subprocess.Popen[str],
        *,
        stdin_data: str | None,
        on_stdout_chunk: _OutputCallback | None,
        on_stderr_chunk: _OutputCallback | None,
        selector_factory: Callable[[], selectors.BaseSelector] = (
            selectors.DefaultSelector
        ),
    ) -> None:
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Managed subprocess requires stdout and stderr pipes")
        self._stdout = _OutputStreamState(process.stdout, on_stdout_chunk)
        self._stderr = _OutputStreamState(process.stderr, on_stderr_chunk)
        self._stdin: _StdinStreamState | None = None
        self._selector: selectors.BaseSelector | None = None
        self._exit_source: _ExitSource | None = None
        self._exit_ready = False
        try:
            self._selector = selector_factory()
            if process.returncode is not None:
                self._exit_ready = True
            else:
                self._watch_child_exit(process.pid)
            for stream in (self._stdout, self._stderr):
                os.set_blocking(stream.descriptor, False)
                self._selector.register(stream.descriptor, selectors.EVENT_READ, stream)
            self._open_stdin(process.stdin, stdin_data)
        except BaseException:
            self.close()
            raise

    def owned_threads(self) -> tuple[threading.Thread, ...]:
        return ()

    def _watch_child_exit(self, pid: int) -> None:
        assert self._selector is not None
        source = _open_child_exit_source(pid)
        if source is None:
            self._exit_ready = True
            return
        self._exit_source = source
        self._selector.register(source.fileno(), selectors.EVENT_READ, _CHILD_EXIT)

    def _open_stdin(self, stream: IO[str] | None, stdin_data: str | None) -> None:
        if stream is None:
            return
        if not stdin_data:
            _close_pipe(stream)
            return
        self._stdin = _StdinStreamState(stream, stdin_data)
        os.set_blocking(self._stdin.descriptor, False)
        # Initial write pass under the same per-turn budget as every later
        # turn: small inputs complete with no registration, and input to an
        # already-exited child still observes the closed pipe.
        self._write_stdin()
        if self._stdin is not None:
            assert self._selector is not None
            self._selector.register(
                self._stdin.descriptor, selectors.EVENT_WRITE, self._stdin
            )

    def wait_and_drain(self, timeout: float | None) -> None:
        """Wait for readiness or ``timeout``, service ready pipes, deliver.

        Queued undelivered chunks make the wait non-blocking, so delivery
        never waits behind a full-timeout wait.
        """
        assert self._selector is not None, "session already closed"
        turn_deadline = _turn_deadline(timeout)
        if (
            self._exit_ready
            or self._stdout.pending_chunks
            or self._stderr.pending_chunks
        ):
            wait: float | None = 0.0
        else:
            wait = None if timeout is None else max(0.0, timeout)
        for key, _events in self._selector.select(wait):
            if isinstance(key.data, _OutputStreamState):
                self._read_ready(key.data)
            elif isinstance(key.data, _StdinStreamState):
                self._write_stdin()
            # Child-exit readiness only ends the wait; the supervisor's
            # process.poll() observes the exit.
        for stream in (self._stdout, self._stderr):
            stream.deliver(SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS, turn_deadline)

    def _read_ready(self, stream: _OutputStreamState) -> None:
        turn_read = 0
        while not stream.closed:
            size = min(
                SUBPROCESS_STREAM_READ_SIZE,
                stream.capacity(),
                SUBPROCESS_STREAM_TURN_READ_BYTES - turn_read,
            )
            if size <= 0:
                # At the in-flight bound the stream stays registered unread;
                # queued chunks keep the next wait non-blocking.
                return
            try:
                data = os.read(stream.descriptor, size)
            except BlockingIOError:
                return
            turn_read += len(data)
            stream.accept(data)
            if not data:
                self._close_output(stream)

    def _write_stdin(self) -> None:
        budget = SUBPROCESS_STREAM_TURN_READ_BYTES
        while self._stdin is not None and budget > 0:
            data = self._stdin.next_slice(budget)
            if data is None:
                self._close_stdin()
                return
            try:
                written = os.write(self._stdin.descriptor, data)
            except BlockingIOError:
                return
            except BrokenPipeError:
                # The child exited or closed stdin; unread input is not a failure.
                self._close_stdin()
                return
            self._stdin.advance(written)
            budget -= written

    def finish_after_exit(self) -> tuple[str, str]:
        """Capture remaining kernel output, release endpoints, then deliver.

        Capture ends when every open stream reports EOF or nothing readable,
        bounded by SUBPROCESS_STREAM_DRAIN_TIMEOUT only while a descendant
        keeps writing. A capture-phase read error is raised after every
        endpoint is closed and before any captured chunk is delivered.

        Spec: [RM-5.2]
        """
        self._close_stdin()
        error = self._capture_remaining()
        self.close()
        if error is not None:
            raise error
        _deliver_frozen((self._stdout, self._stderr))
        return "".join(self._stdout.collected), "".join(self._stderr.collected)

    def _capture_remaining(self) -> BaseException | None:
        deadline = time.monotonic() + SUBPROCESS_STREAM_DRAIN_TIMEOUT
        error: BaseException | None = None
        open_streams = [s for s in (self._stdout, self._stderr) if not s.closed]
        while open_streams and time.monotonic() < deadline:
            progressed = False
            for stream in list(open_streams):
                try:
                    data = os.read(stream.descriptor, SUBPROCESS_STREAM_READ_SIZE)
                    stream.accept(data)
                except BlockingIOError:
                    continue
                except (OSError, ValueError) as exc:
                    error = exc if error is None else error
                    open_streams.remove(stream)
                    continue
                progressed = True
                if not data:
                    open_streams.remove(stream)
            if not progressed:
                # Every open pipe is empty: an idle inherited writer holds it.
                break
        return error

    def _unregister(self, descriptor: int) -> None:
        if self._selector is not None:
            with contextlib.suppress(KeyError, ValueError):
                self._selector.unregister(descriptor)

    def _close_stdin(self) -> None:
        if self._stdin is None:
            return
        stdin, self._stdin = self._stdin, None
        self._unregister(stdin.descriptor)
        _close_pipe(stdin.stream)

    def _close_output(self, stream: _OutputStreamState) -> None:
        if stream.closed:
            return
        stream.closed = True
        self._unregister(stream.descriptor)
        _close_pipe(stream.stream)

    def close(self) -> None:
        """Release endpoints, the exit source, and the selector (idempotent)."""
        self._close_stdin()
        self._close_output(self._stdout)
        self._close_output(self._stderr)
        if self._exit_source is not None:
            source, self._exit_source = self._exit_source, None
            self._unregister(source.fileno())
            with contextlib.suppress(OSError):
                source.close()
        if self._selector is not None:
            selector, self._selector = self._selector, None
            selector.close()


def open_supervised_process(
    args: Sequence[str],
    *,
    pipe_stdin: bool = True,
    encoding: str | None = None,
    errors: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    creationflags: int = 0,
) -> subprocess.Popen[str]:
    """Start a one-shot command whose stdio the readiness adapter can own.

    Every process passed to ``run_monitored_subprocess`` is constructed here.
    On POSIX this is ``subprocess.Popen`` with text-mode stdio pipes. On
    Windows the overlapped leaf must own pipe creation, because anonymous
    ``CreatePipe`` handles cannot be overlapped: the parent ends are created
    overlapped (the ``asyncio.windows_utils.Popen`` recipe) and attached as
    ``_OverlappedPipe`` objects carrying the text configuration.

    Args:
        args: Command argv.
        pipe_stdin: Whether to give the child a stdin pipe; otherwise the
            child inherits the parent's stdin.
        encoding: Text encoding for stdio; the platform default when None.
        errors: Codec error handler; ``"strict"`` when None.
        cwd: Working directory for the child.
        env: Child environment; the parent's when None.
        creationflags: Windows process creation flags.

    Returns:
        The started process with ``stdout``/``stderr`` (and ``stdin``) pipes.

    Spec: [RM-5.2]
    """
    if sys.platform == "win32":
        return _open_overlapped_process(
            args,
            pipe_stdin=pipe_stdin,
            encoding=encoding,
            errors=errors,
            cwd=cwd,
            env=env,
            creationflags=creationflags,
        )
    return subprocess.Popen(
        list(args),
        stdin=subprocess.PIPE if pipe_stdin else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding=encoding,
        errors=errors,
        cwd=cwd,
        env=env,
        creationflags=creationflags,
    )


def _default_text_encoding() -> str:
    """Return the encoding ``Popen(text=True)`` uses when none is given."""
    return "utf-8" if sys.flags.utf8_mode else locale.getencoding()


if sys.platform == "win32":
    _EndpointState = Literal["idle", "pending", "eof", "closed"]

    class _OverlappedOp:
        """At most one outstanding overlapped operation for one pipe endpoint."""

        def __init__(self) -> None:
            self.ov: Any = None
            self.state: _EndpointState = "idle"

    class _OverlappedPipe:
        """Parent end of an overlapped pipe with the file attributes we read.

        The stdlib ``PipeHandle`` has none of ``closed``, ``encoding``, or
        ``errors``; supervisor cleanup reads ``closed`` and the decoder and
        encoder chain read the ``Popen`` text configuration from here.
        """

        def __init__(self, handle: int, *, encoding: str, errors: str) -> None:
            self._handle: int | None = handle
            self.encoding = encoding
            self.errors = errors

        @property
        def handle(self) -> int:
            if self._handle is None:
                raise ValueError("I/O operation on closed pipe")
            return self._handle

        def fileno(self) -> int:
            return self.handle

        @property
        def closed(self) -> bool:
            return self._handle is None

        def close(self) -> None:
            if self._handle is not None:
                handle, self._handle = self._handle, None
                _winapi.CloseHandle(handle)

    def _create_overlapped_pair(
        name: str, parents: dict[str, int], child_fds: dict[str, int]
    ) -> None:
        """Create one pipe pair: an overlapped parent end and a child fd.

        Once ``open_osfhandle`` returns, the fd owns the child handle and is
        released with ``os.close``; the raw handle is closed only if that
        conversion itself failed.
        """
        if name == "stdin":
            child_handle, parent_handle = windows_utils.pipe(
                overlapped=(False, True),
                duplex=True,
                bufsize=SUBPROCESS_STREAM_TURN_READ_BYTES,
            )
            flags = os.O_RDONLY
        else:
            parent_handle, child_handle = windows_utils.pipe(
                overlapped=(True, False), bufsize=SUBPROCESS_STREAM_TURN_READ_BYTES
            )
            flags = 0
        parents[name] = parent_handle
        try:
            child_fds[name] = msvcrt.open_osfhandle(child_handle, flags)
        except BaseException:
            _winapi.CloseHandle(child_handle)
            raise

    def _close_handles(handles: Iterable[int]) -> None:
        for handle in handles:
            with contextlib.suppress(OSError):
                _winapi.CloseHandle(handle)

    def _open_overlapped_process(
        args: Sequence[str],
        *,
        pipe_stdin: bool,
        encoding: str | None,
        errors: str | None,
        cwd: str | None,
        env: Mapping[str, str] | None,
        creationflags: int,
    ) -> subprocess.Popen[str]:
        """Start ``args`` with overlapped parent pipe ends (Windows only).

        Mirrors ``asyncio.windows_utils.Popen`` endpoint for endpoint, except
        that each pair is created under the rollback scope, the pipe buffer is
        one fairness budget, and the parent ends are wrapped as
        ``_OverlappedPipe``. ``Popen`` itself runs unbuffered and not in text
        mode, as that recipe requires; text decoding is the adapter's.
        """
        text_encoding = encoding or _default_text_encoding()
        text_errors = errors or "strict"
        parents: dict[str, int] = {}
        child_fds: dict[str, int] = {}
        try:
            for name in ("stdin", "stdout", "stderr"):
                if name != "stdin" or pipe_stdin:
                    _create_overlapped_pair(name, parents, child_fds)
            process = subprocess.Popen(
                list(args),
                stdin=child_fds.get("stdin"),
                stdout=child_fds["stdout"],
                stderr=child_fds["stderr"],
                bufsize=0,
                cwd=cwd,
                env=env,
                creationflags=creationflags,
            )
        except BaseException:
            _close_handles(parents.values())
            raise
        finally:
            for fd in child_fds.values():
                with contextlib.suppress(OSError):
                    os.close(fd)
        _attach_overlapped_pipes(
            process, parents, encoding=text_encoding, errors=text_errors
        )
        return cast("subprocess.Popen[str]", process)

    def _attach_overlapped_pipes(
        process: subprocess.Popen[bytes],
        parents: dict[str, int],
        *,
        encoding: str,
        errors: str,
    ) -> None:
        """Attach parent ends to ``process``; on failure reap it and close all."""
        attached: list[_OverlappedPipe] = []
        try:
            for name, handle in parents.items():
                pipe = _OverlappedPipe(handle, encoding=encoding, errors=errors)
                attached.append(pipe)
                setattr(process, name, pipe)
        except BaseException:
            with contextlib.suppress(BaseException):
                cleanup_after_execution_failure(
                    cast("subprocess.Popen[str]", process),
                    stop_runtime=process.kill,
                    kill_runtime=process.kill,
                )
            for pipe in attached:
                with contextlib.suppress(OSError):
                    pipe.close()
            _close_handles(list(parents.values())[len(attached) :])
            raise

    class _WindowsOverlappedProcessIO:
        """Windows platform readiness adapter for one supervised subprocess.

        Every pipe operation is overlapped and the supervisor thread waits in
        one ``WaitForMultipleObjects`` over the completion events of pending
        operations plus the process handle: no helper thread exists, a quiet
        child costs zero wakeups, and exit is never data-gated.

        Battle scar and rejected design: anonymous ``CreatePipe`` handles are
        synchronous, so the leaf must own pipe creation
        (``open_supervised_process``). A synchronous-handle design with
        blocking reader threads was rejected: ``PeekNamedPipe`` on a handle
        another thread is reading can block, and ``CancelSynchronousIo``
        cannot cancel a read that has not yet been submitted, so a stop racing
        the reader's next ``ReadFile`` finds nothing to cancel. Here the only
        peek happens on the supervisor thread with no read outstanding.

        Spec: [RM-5.2]; docs/program-theory.md [ALT-WT-021]
        """

        def __init__(
            self,
            process: subprocess.Popen[str],
            *,
            stdin_data: str | None,
            on_stdout_chunk: _OutputCallback | None,
            on_stderr_chunk: _OutputCallback | None,
        ) -> None:
            if process.stdout is None or process.stderr is None:
                raise RuntimeError(
                    "Managed subprocess requires stdout and stderr pipes"
                )
            self._process_handle = int(cast(Any, process)._handle)
            self._stdout = _OutputStreamState(process.stdout, on_stdout_chunk)
            self._stderr = _OutputStreamState(process.stderr, on_stderr_chunk)
            self._outputs = (self._stdout, self._stderr)
            self._ops = {id(stream): _OverlappedOp() for stream in self._outputs}
            self._stdin: _StdinStreamState | None = None
            self._stdin_op = _OverlappedOp()
            try:
                self._open_stdin(process.stdin, stdin_data)
                for stream in self._outputs:
                    self._post_reads(stream, SUBPROCESS_STREAM_TURN_READ_BYTES)
            except BaseException:
                self.close()
                raise

        def owned_threads(self) -> tuple[threading.Thread, ...]:
            return ()

        def _op(self, stream: _OutputStreamState) -> _OverlappedOp:
            return self._ops[id(stream)]

        def _open_stdin(self, stream: IO[str] | None, stdin_data: str | None) -> None:
            if stream is None:
                return
            if not stdin_data:
                _close_pipe(stream)
                return
            self._stdin = _StdinStreamState(stream, stdin_data)
            self._write_stdin()

        # -- reads ---------------------------------------------------------

        def _post_reads(self, stream: _OutputStreamState, budget: int) -> None:
            """Issue reads while idle, below the bound, and within ``budget``."""
            op = self._op(stream)
            while op.state == "idle":
                size = min(SUBPROCESS_STREAM_READ_SIZE, stream.capacity(), budget)
                if size <= 0:
                    # At the bound no read is outstanding: the pipe fills and
                    # the child blocks until delivery frees capacity.
                    return
                try:
                    ov, err = _winapi.ReadFile(stream.descriptor, size, overlapped=True)
                except OSError as exc:
                    self._read_failed(stream, exc)
                    return
                if err == _winapi.ERROR_IO_PENDING:
                    op.ov, op.state = ov, "pending"
                    return
                budget -= self._collect_read(stream, ov, wait=False)

        def _collect_read(
            self,
            stream: _OutputStreamState,
            ov: Any,
            *,
            wait: bool,
            cancelled: bool = False,
        ) -> int:
            """Retire one read and queue its bytes; return the bytes collected."""
            try:
                nread, err = ov.GetOverlappedResult(wait)
            except OSError as exc:
                self._read_failed(stream, exc)
                return 0
            if cancelled and err == _winapi.ERROR_OPERATION_ABORTED:
                return 0
            if err != 0:
                raise OSError(f"overlapped pipe read failed with winerror {err}")
            buffer = ov.getbuffer()
            data = bytes(buffer[:nread]) if buffer is not None else b""
            if data:
                stream.accept(data)
            return len(data)

        def _retire_pending_read(self, stream: _OutputStreamState) -> int:
            op = self._op(stream)
            ov, op.ov, op.state = op.ov, None, "idle"
            return self._collect_read(stream, ov, wait=False)

        def _read_failed(self, stream: _OutputStreamState, exc: OSError) -> None:
            if exc.winerror != _winapi.ERROR_BROKEN_PIPE:
                raise exc
            self._op(stream).state = "eof"
            stream.accept_eof()

        # -- writes --------------------------------------------------------

        def _write_stdin(self) -> None:
            budget = SUBPROCESS_STREAM_TURN_READ_BYTES
            while (
                self._stdin is not None
                and self._stdin_op.state == "idle"
                and budget > 0
            ):
                data = self._stdin.next_slice(budget)
                if data is None:
                    self._close_stdin()
                    return
                try:
                    ov, err = _winapi.WriteFile(
                        self._stdin.descriptor, data, overlapped=True
                    )
                except OSError as exc:
                    self._write_failed(exc)
                    return
                if err == _winapi.ERROR_IO_PENDING:
                    self._stdin_op.ov, self._stdin_op.state = ov, "pending"
                    return
                budget -= self._collect_write(ov)

        def _collect_write(self, ov: Any) -> int:
            """Retire one write by count; a write has no buffer to read."""
            try:
                nwritten, err = ov.GetOverlappedResult(False)
            except OSError as exc:
                self._write_failed(exc)
                return 0
            if err != 0:
                raise OSError(f"overlapped pipe write failed with winerror {err}")
            if self._stdin is not None:
                self._stdin.advance(nwritten)
            return int(nwritten)

        def _retire_pending_write(self) -> None:
            op = self._stdin_op
            ov, op.ov, op.state = op.ov, None, "idle"
            self._collect_write(ov)

        def _write_failed(self, exc: OSError) -> None:
            if exc.winerror not in (_winapi.ERROR_BROKEN_PIPE, _winapi.ERROR_NO_DATA):
                raise exc
            # The child exited or closed stdin; unread input is not a failure.
            self._close_stdin()

        # -- the turn ------------------------------------------------------

        def _wait_set(self) -> list[tuple[int, object]]:
            waits: list[tuple[int, object]] = []
            for stream in self._outputs:
                op = self._op(stream)
                if op.state == "pending":
                    waits.append((int(op.ov.event), stream))
            if self._stdin_op.state == "pending":
                waits.append((int(self._stdin_op.ov.event), self._stdin_op))
            waits.append((self._process_handle, _CHILD_EXIT))
            return waits

        def _needs_turn(self) -> bool:
            """Whether work is queued that no wait-set handle would signal."""
            if any(stream.pending_chunks for stream in self._outputs):
                return True
            if self._stdin is not None and self._stdin_op.state == "idle":
                return True
            return any(
                self._op(stream).state == "idle" and stream.capacity() > 0
                for stream in self._outputs
            )

        def _wait_timeout_ms(self, timeout: float | None) -> int:
            if self._needs_turn():
                return 0
            if timeout is None:
                return _winapi.INFINITE
            return min(math.ceil(max(0.0, timeout) * 1000), _winapi.INFINITE - 1)

        @staticmethod
        def _signalled(handles: list[int], timeout_ms: int) -> list[int]:
            """Return every signalled index, not only the lowest one."""
            signalled: list[int] = []
            offset = 0
            while offset < len(handles):
                result = _winapi.WaitForMultipleObjects(
                    handles[offset:], False, timeout_ms
                )
                if result == _winapi.WAIT_TIMEOUT:
                    break
                index = result - _winapi.WAIT_OBJECT_0
                if not 0 <= index < len(handles) - offset:
                    raise OSError(f"unexpected wait result {result}")
                signalled.append(offset + index)
                offset += index + 1
                timeout_ms = 0
            return signalled

        def wait_and_drain(self, timeout: float | None) -> None:
            """Wait for completions, exit, or ``timeout``; retire, deliver, issue.

            The wait is non-blocking whenever undelivered chunks are queued, as
            the POSIX leaf's ``select(0.0)``.
            """
            turn_deadline = _turn_deadline(timeout)
            waits = self._wait_set()
            timeout_ms = self._wait_timeout_ms(timeout)
            budgets = {
                id(stream): SUBPROCESS_STREAM_TURN_READ_BYTES
                for stream in self._outputs
            }
            for index in self._signalled([handle for handle, _ in waits], timeout_ms):
                owner = waits[index][1]
                if isinstance(owner, _OutputStreamState):
                    budgets[id(owner)] -= self._retire_pending_read(owner)
                elif owner is self._stdin_op:
                    self._retire_pending_write()
                # The process handle only ends the wait; the supervisor's
                # process.poll() observes the exit.
            self._write_stdin()
            for stream in self._outputs:
                stream.deliver(SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS, turn_deadline)
            for stream in self._outputs:
                self._post_reads(stream, max(0, budgets[id(stream)]))

        # -- terminal capture ----------------------------------------------

        def finish_after_exit(self) -> tuple[str, str]:
            """Capture until an observed empty pipe, release, then deliver.

            Pending reads are settled by cancel-then-collect (a read that won
            the race keeps its bytes); emptiness is observed separately with
            ``PeekNamedPipe`` while no read is outstanding. Capture is bounded
            by SUBPROCESS_STREAM_DRAIN_TIMEOUT only while a descendant keeps
            writing. A capture-phase read error is raised after every endpoint
            is closed and before any captured chunk is delivered.
            """
            self._settle_write()
            self._close_stdin()
            error = self._capture_remaining()
            self.close()
            if error is not None:
                raise error
            _deliver_frozen(self._outputs)
            return "".join(self._stdout.collected), "".join(self._stderr.collected)

        def _capture_remaining(self) -> BaseException | None:
            deadline = time.monotonic() + SUBPROCESS_STREAM_DRAIN_TIMEOUT
            error: BaseException | None = None
            live = [
                s for s in self._outputs if self._op(s).state in ("idle", "pending")
            ]
            for stream in list(live):
                try:
                    self._settle_read(stream)
                except (OSError, ValueError) as exc:
                    error = exc if error is None else error
                    live.remove(stream)
            while live and time.monotonic() < deadline:
                for stream in list(live):
                    try:
                        finished = self._capture_step(stream, deadline)
                    except (OSError, ValueError) as exc:
                        error = exc if error is None else error
                        finished = True
                    if finished:
                        live.remove(stream)
            return error

        def _settle_read(self, stream: _OutputStreamState) -> None:
            op = self._op(stream)
            if op.state != "pending":
                return
            ov, op.ov, op.state = op.ov, None, "idle"
            ov.cancel()
            self._collect_read(stream, ov, wait=True, cancelled=True)

        def _capture_step(self, stream: _OutputStreamState, deadline: float) -> bool:
            """Observe the pipe and read what it holds; True ends this stream."""
            if self._op(stream).state != "idle":
                return True
            try:
                peeked = cast(
                    "tuple[int, int]", _winapi.PeekNamedPipe(stream.descriptor, 0)
                )
            except OSError as exc:
                self._read_failed(stream, exc)
                return True
            if peeked[0] == 0:
                return True  # the justified empty-pipe observation
            size = min(SUBPROCESS_STREAM_READ_SIZE, peeked[0])
            try:
                ov, err = _winapi.ReadFile(stream.descriptor, size, overlapped=True)
            except OSError as exc:
                self._read_failed(stream, exc)
                return True
            if err == _winapi.ERROR_IO_PENDING:
                remaining_ms = math.ceil(max(0.0, deadline - time.monotonic()) * 1000)
                waited = _winapi.WaitForMultipleObjects([ov.event], False, remaining_ms)
                if waited == _winapi.WAIT_TIMEOUT:
                    ov.cancel()
                self._collect_read(stream, ov, wait=True, cancelled=True)
            else:
                self._collect_read(stream, ov, wait=False)
            return self._op(stream).state != "idle"

        # -- release -------------------------------------------------------

        def _settle_write(self) -> None:
            op = self._stdin_op
            if op.state != "pending":
                return
            ov, op.ov, op.state = op.ov, None, "idle"
            ov.cancel()
            with contextlib.suppress(OSError):
                ov.GetOverlappedResult(True)

        def _close_stdin(self) -> None:
            if self._stdin is None:
                return
            self._settle_write()
            stdin, self._stdin = self._stdin, None
            self._stdin_op.state = "closed"
            _close_pipe(stdin.stream)

        def _close_output(self, stream: _OutputStreamState) -> None:
            op = self._op(stream)
            if op.state == "pending":
                ov, op.ov = op.ov, None
                ov.cancel()
                with contextlib.suppress(OSError):
                    ov.GetOverlappedResult(True)
            op.state = "closed"
            if not stream.closed:
                stream.closed = True
                _close_pipe(stream.stream)

        def close(self) -> None:
            """Settle outstanding operations and close parent ends (idempotent)."""
            self._close_stdin()
            for stream in self._outputs:
                self._close_output(stream)


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
