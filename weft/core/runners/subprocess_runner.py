"""Shared helpers for one-shot command runners backed by external processes.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2], [CC-3.4]
- docs/specifications/06-Resource_Management.md [RM-5.1]
"""

from __future__ import annotations

import codecs
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from typing import IO, TYPE_CHECKING, Any

from simplebroker import BrokerTarget
from weft._constants import ACTIVE_CONTROL_POLL_INTERVAL
from weft.core.resource_monitor import ResourceMetrics, load_resource_monitor
from weft.ext import RunnerHandle

if TYPE_CHECKING:
    from weft.core.runners.host import RunnerOutcome

_STREAM_READ_SIZE = 64 * 1024


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


def run_monitored_subprocess(
    *,
    process: subprocess.Popen[str],
    stdin_data: str | None,
    timeout: float | None,
    limits: Any | None,
    monitor_class: str | None,
    monitor_interval: float,
    monitor: Any | None,
    db_path: BrokerTarget | str | None,
    config: dict[str, Any] | None,
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
    """Run a managed subprocess with timeout, cancellation, and limit checks."""
    from weft.core.runners.host import RunnerOutcome

    last_metrics: ResourceMetrics | None = None
    actual_worker_pid = worker_pid if worker_pid is not None else process.pid
    if on_worker_started is not None:
        try:
            on_worker_started(actual_worker_pid)
        except Exception:  # pragma: no cover - defensive
            pass
    if on_runtime_handle_started is not None:
        try:
            on_runtime_handle_started(runtime_handle)
        except Exception:  # pragma: no cover - defensive
            pass

    if monitor is None and monitor_class and actual_worker_pid is not None:
        monitor = load_resource_monitor(
            monitor_class,
            limits=limits,
            polling_interval=monitor_interval,
            db_path=db_path,
            config=config,
        )
    if monitor is not None:
        try:
            monitor.start(actual_worker_pid if actual_worker_pid is not None else -1)
        except Exception:  # pragma: no cover - external process may not be monitorable
            monitor = None

    if process.stdout is None or process.stderr is None:
        raise RuntimeError("Managed subprocess requires stdout and stderr pipes")

    stdout_queue: queue.Queue[str | None] = queue.Queue()
    stderr_queue: queue.Queue[str | None] = queue.Queue()
    _start_stream_reader(process.stdout, stdout_queue)
    _start_stream_reader(process.stderr, stderr_queue)
    _write_process_input(process, stdin_data)

    start_time = time.monotonic()
    interval = monitor_interval or 1.0
    next_monitor_at = start_time + interval
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stdout_closed = False
    stderr_closed = False

    while True:
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

        if cancel_requested is not None and _cancel_requested(cancel_requested):
            _stop_process_runtime(
                process, stop_runtime=stop_runtime, kill_runtime=kill_runtime
            )
            stdout_closed, stderr_closed = _drain_streams_until_closed(
                stdout_queue,
                stderr_queue,
                stdout_parts,
                stderr_parts,
                on_stdout_chunk=on_stdout_chunk,
                on_stderr_chunk=on_stderr_chunk,
                stdout_closed=stdout_closed,
                stderr_closed=stderr_closed,
            )
            last_metrics = _stop_monitor(monitor, last_metrics)
            return RunnerOutcome(
                status="cancelled",
                value=None,
                error="Target execution cancelled",
                stdout="".join(stdout_parts),
                stderr="".join(stderr_parts),
                returncode=None,
                duration=time.monotonic() - start_time,
                metrics=last_metrics,
                worker_pid=actual_worker_pid,
                runtime_handle=runtime_handle,
            )

        elapsed = time.monotonic() - start_time
        if timeout is not None and elapsed >= timeout:
            _kill_process_runtime(process, kill_runtime=kill_runtime)
            stdout_closed, stderr_closed = _drain_streams_until_closed(
                stdout_queue,
                stderr_queue,
                stdout_parts,
                stderr_parts,
                on_stdout_chunk=on_stdout_chunk,
                on_stderr_chunk=on_stderr_chunk,
                stdout_closed=stdout_closed,
                stderr_closed=stderr_closed,
            )
            last_metrics = _stop_monitor(monitor, last_metrics)
            return RunnerOutcome(
                status="timeout",
                value=None,
                error="Target execution timed out",
                stdout="".join(stdout_parts),
                stderr="".join(stderr_parts),
                returncode=None,
                duration=timeout,
                metrics=last_metrics,
                worker_pid=actual_worker_pid,
                runtime_handle=runtime_handle,
            )

        if process.poll() is not None and stdout_closed and stderr_closed:
            break

        sleep_for = ACTIVE_CONTROL_POLL_INTERVAL
        if timeout is not None:
            remaining = timeout - elapsed
            sleep_for = min(sleep_for, max(0.01, remaining))
        if process.poll() is not None:
            sleep_for = min(sleep_for, 0.01)
        if monitor is not None:
            until_monitor = max(0.01, next_monitor_at - time.monotonic())
            sleep_for = min(sleep_for, until_monitor)

        if monitor is not None and time.monotonic() >= next_monitor_at:
            try:
                ok, violation = monitor.check_limits()
            except Exception:  # pragma: no cover - process may have exited
                ok, violation = True, None
            last_metrics = monitor.last_metrics()
            next_monitor_at = time.monotonic() + interval
            if not ok:
                _kill_process_runtime(process, kill_runtime=kill_runtime)
                stdout_closed, stderr_closed = _drain_streams_until_closed(
                    stdout_queue,
                    stderr_queue,
                    stdout_parts,
                    stderr_parts,
                    on_stdout_chunk=on_stdout_chunk,
                    on_stderr_chunk=on_stderr_chunk,
                    stdout_closed=stdout_closed,
                    stderr_closed=stderr_closed,
                )
                last_metrics = _stop_monitor(monitor, last_metrics)
                return RunnerOutcome(
                    status="limit",
                    value=None,
                    error=violation,
                    stdout="".join(stdout_parts),
                    stderr="".join(stderr_parts),
                    returncode=None,
                    duration=time.monotonic() - start_time,
                    metrics=last_metrics,
                    worker_pid=actual_worker_pid,
                    runtime_handle=runtime_handle,
                )

        time.sleep(sleep_for)

    _drain_streams_until_closed(
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

    if monitor is not None:
        last_metrics = monitor.last_metrics()
        if last_metrics is None:
            try:
                last_metrics = monitor.snapshot()
            except Exception:  # pragma: no cover - defensive
                last_metrics = None
        monitor.stop()

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
            worker_pid=actual_worker_pid,
            runtime_handle=runtime_handle,
        )

    if returncode != 0:
        return RunnerOutcome(
            status="error",
            value=None,
            error=f"Command exited with {returncode}: {(stderr or '').strip()}",
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            duration=time.monotonic() - start_time,
            metrics=last_metrics,
            worker_pid=actual_worker_pid,
            runtime_handle=runtime_handle,
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
        worker_pid=actual_worker_pid,
        runtime_handle=runtime_handle,
    )


def _stop_process_runtime(
    process: subprocess.Popen[str],
    *,
    stop_runtime: Callable[[], None],
    kill_runtime: Callable[[], None],
) -> None:
    stop_runtime()
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        _kill_process_runtime(process, kill_runtime=kill_runtime)


def _kill_process_runtime(
    process: subprocess.Popen[str],
    *,
    kill_runtime: Callable[[], None],
) -> None:
    kill_runtime()
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=0.2)


def _start_stream_reader(
    stream: IO[str],
    target_queue: queue.Queue[str | None],
) -> None:
    def _reader() -> None:
        raw_stream = getattr(stream, "buffer", None)
        encoding = getattr(stream, "encoding", None) or "utf-8"
        errors = getattr(stream, "errors", None) or "replace"
        decoder = codecs.getincrementaldecoder(encoding)(errors=errors)
        try:
            while True:
                if raw_stream is not None and hasattr(raw_stream, "read1"):
                    raw_chunk = raw_stream.read1(_STREAM_READ_SIZE)
                    if raw_chunk == b"":
                        break
                    chunk = decoder.decode(raw_chunk)
                else:
                    chunk = stream.read(_STREAM_READ_SIZE)
                    if chunk == "":
                        break
                if not chunk:
                    continue
                target_queue.put(chunk)

            final_chunk = decoder.decode(b"", final=True)
            if final_chunk:
                target_queue.put(final_chunk)
        finally:
            target_queue.put(None)

    threading.Thread(target=_reader, daemon=True).start()


def _write_process_input(
    process: subprocess.Popen[str],
    stdin_data: str | None,
) -> None:
    stdin = process.stdin
    if stdin is None:
        return
    try:
        if stdin_data is not None:
            stdin.write(stdin_data)
            stdin.flush()
    except BrokenPipeError:
        pass
    finally:
        try:
            stdin.close()
        except Exception:
            pass


def _drain_stream_queue(
    pending_queue: queue.Queue[str | None],
    collected: list[str],
    *,
    on_chunk: Callable[[str, bool], None] | None,
    closed: bool,
) -> bool:
    while True:
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


def _drain_streams_until_closed(
    stdout_queue: queue.Queue[str | None],
    stderr_queue: queue.Queue[str | None],
    stdout_parts: list[str],
    stderr_parts: list[str],
    *,
    on_stdout_chunk: Callable[[str, bool], None] | None,
    on_stderr_chunk: Callable[[str, bool], None] | None,
    stdout_closed: bool,
    stderr_closed: bool,
    timeout: float = 0.25,
) -> tuple[bool, bool]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not (stdout_closed and stderr_closed):
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
        if stdout_closed and stderr_closed:
            break
        time.sleep(0.01)
    return stdout_closed, stderr_closed


def _stop_monitor(
    monitor: Any,
    last_metrics: ResourceMetrics | None,
) -> ResourceMetrics | None:
    if monitor is None:
        return last_metrics
    metrics = monitor.last_metrics() or last_metrics
    monitor.stop()
    return metrics


def _cancel_requested(callback: Callable[[], bool]) -> bool:
    try:
        return bool(callback())
    except Exception:  # pragma: no cover - defensive
        return False
