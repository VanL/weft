"""Tests for monitored subprocess runner behavior."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import itertools
import logging
import os
import queue
import subprocess
import sys
import threading
import time as real_time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import IO, Any, cast

import psutil
import pytest

from weft.core.runners import subprocess_runner
from weft.core.runners.outcome import RunnerOutcome
from weft.core.runners.subprocess_runner import run_monitored_subprocess
from weft.ext import RunnerHandle

pytestmark = [pytest.mark.shared]


class _FakeRunnerClock:
    def __init__(self) -> None:
        self._ticks = itertools.count(start=100.0, step=0.02)

    def monotonic(self) -> float:
        return next(self._ticks)

    @staticmethod
    def sleep(seconds: float) -> None:
        real_time.sleep(seconds)


class _TimeoutAfterMetricsClock:
    def __init__(self, metrics_sampled: Callable[[], bool]) -> None:
        self._metrics_sampled = metrics_sampled
        self._calls = 0

    def monotonic(self) -> float:
        self._calls += 1
        if self._metrics_sampled():
            return 102.0
        return (100.0, 100.02)[self._calls > 1]

    @staticmethod
    def sleep(seconds: float) -> None:
        real_time.sleep(seconds)


class _FatalCallbackSignal(BaseException):
    pass


class _OutputReadyClock:
    """Advance real elapsed time only after the fixture child produced output."""

    def __init__(self, ready: Path) -> None:
        self.ready = ready
        self.origin = real_time.monotonic()
        self.ready_at: float | None = None

    def monotonic(self) -> float:
        now = real_time.monotonic()
        if self.ready_at is None:
            assert now - self.origin < 5.0, "child did not finish fixture output"
            if self.ready.exists():
                self.ready_at = now
            return self.origin
        return self.origin + now - self.ready_at

    @staticmethod
    def sleep(seconds: float) -> None:
        real_time.sleep(seconds)


class _CallbackCleanupProcess:
    pid = 123

    def __init__(
        self,
        *,
        wait_failures: list[BaseException] | None = None,
        poll_failure: BaseException | None = None,
        kill_failure: BaseException | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.reaped = False
        self.returncode: int | None = None
        self._wait_failures = list(wait_failures or [])
        self._poll_failure = poll_failure
        self._kill_failure = kill_failure

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(f"wait:{timeout}")
        if self._wait_failures:
            raise self._wait_failures.pop(0)
        self.reaped = True
        self.returncode = -15
        return self.returncode

    def poll(self) -> int | None:
        self.calls.append("poll")
        if self._poll_failure is not None:
            raise self._poll_failure
        return self.returncode

    def kill(self) -> None:
        self.calls.append("process.kill")
        if self._kill_failure is not None:
            raise self._kill_failure


def _runner_handle(process: subprocess.Popen[str]) -> RunnerHandle:
    return RunnerHandle(
        runner="host",
        kind="process",
        id=str(process.pid),
        control={"authority": "host-pid"},
        observations={"host_pids": [process.pid]},
    )


def _run_process_with_monitor(
    monitor: Any,
    *,
    script: str = "print('done')",
    monitor_interval: float = 0.01,
    timeout: float = 5.0,
    cancel_requested: Callable[[], bool] | None = None,
) -> RunnerOutcome:
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        return run_monitored_subprocess(
            process=process,
            stdin_data=None,
            timeout=timeout,
            limits=None,
            monitor_class=None,
            monitor_interval=monitor_interval,
            monitor=monitor,
            runtime_handle=_runner_handle(process),
            cancel_requested=cancel_requested,
            on_worker_started=None,
            on_runtime_handle_started=None,
            stop_runtime=lambda: None,
            kill_runtime=lambda: None,
        )
    finally:
        if process.poll() is None:  # pragma: no cover - failure cleanup
            process.kill()
            process.wait(timeout=5.0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(b"\r\nnext\r", "\nnext\n"), ("€𐍈\r\n".encode(), "€𐍈\n"), (b"\xe2\x82", "�")],
)
def test_stream_reader_normalizes_crlf_split_across_chunks(
    monkeypatch: pytest.MonkeyPatch, raw: bytes, expected: str
) -> None:
    # A one-byte read forces split codec/newline input without assuming how the
    # operating system groups writes. Output chunks themselves are not a contract.
    monkeypatch.setattr(subprocess_runner, "SUBPROCESS_STREAM_READ_SIZE", 1)
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
    target_queue: queue.Queue[str | None] = queue.Queue()
    try:
        os.write(write_fd, raw)
    finally:
        os.close(write_fd)
    adapter = subprocess_runner._PipeAdapter(stream, target_queue=target_queue)
    adapter.start()
    try:
        parts: list[str] = []
        while (chunk := target_queue.get(timeout=5.0)) is not None:
            parts.append(chunk)
        assert "".join(parts) == expected
        adapter.thread.join(timeout=5.0)
        assert not adapter.thread.is_alive()
        assert stream.closed
        assert target_queue.empty(), "EOF must be published exactly once"
    finally:
        adapter.stop.set()
        adapter.thread.join(timeout=5.0)


def test_completed_process_at_timeout_wake_boundary_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "print('done')"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        process.wait(timeout=5.0)
        assert process.returncode == 0

        # Model the runner waking after the timeout boundary even though the
        # subprocess has already exited cleanly.
        monkeypatch.setattr(subprocess_runner, "time", _FakeRunnerClock())
        kill_calls: list[str] = []

        outcome = run_monitored_subprocess(
            process=process,
            stdin_data=None,
            timeout=0.01,
            limits=None,
            monitor_class=None,
            monitor_interval=1.0,
            monitor=None,
            runtime_handle=_runner_handle(process),
            cancel_requested=None,
            on_worker_started=None,
            on_runtime_handle_started=None,
            stop_runtime=lambda: None,
            kill_runtime=lambda: kill_calls.append("kill"),
        )
    finally:
        if process.poll() is None:  # pragma: no cover - failure cleanup
            process.kill()
            process.wait(timeout=5.0)

    assert kill_calls == []
    assert outcome.status == "ok"
    assert outcome.returncode == 0
    assert outcome.stdout == "done\n"


def test_start_callback_failures_are_logged_without_replacing_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "print('done')"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def fail_callback(_value: object) -> None:
        raise RuntimeError("contains secret")

    try:
        with caplog.at_level(
            logging.WARNING,
            logger="weft.core.runners.subprocess_runner",
        ):
            outcome = run_monitored_subprocess(
                process=process,
                stdin_data=None,
                timeout=5.0,
                limits=None,
                monitor_class=None,
                monitor_interval=1.0,
                monitor=None,
                runtime_handle=_runner_handle(process),
                cancel_requested=None,
                on_worker_started=fail_callback,
                on_runtime_handle_started=fail_callback,
                stop_runtime=lambda: None,
                kill_runtime=lambda: None,
            )
    finally:
        if process.poll() is None:  # pragma: no cover - failure cleanup
            process.kill()
            process.wait(timeout=5.0)

    assert outcome.status == "ok"
    assert outcome.returncode == 0
    assert outcome.stdout == "done\n"
    assert [record.message for record in caplog.records] == [
        "Subprocess worker-start callback failed",
        "Subprocess runtime-handle callback failed",
    ]
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize(
    "callback_name",
    ["on_worker_started", "on_runtime_handle_started"],
)
def test_start_callback_propagates_non_exception_failure_identity(
    callback_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FatalSignal(BaseException):
        pass

    fatal = FatalSignal("stop callback")
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def fail_callback(_value: object) -> None:
        raise fatal

    stop_calls: list[str] = []

    def stop_runtime() -> None:
        stop_calls.append("stop")
        process.terminate()

    def kill_runtime() -> None:
        stop_calls.append("kill")
        process.kill()

    production_reaped = False
    try:
        with (
            caplog.at_level(
                logging.WARNING,
                logger="weft.core.runners.subprocess_runner",
            ),
            pytest.raises(FatalSignal) as caught,
        ):
            run_monitored_subprocess(
                process=process,
                stdin_data=None,
                timeout=5.0,
                limits=None,
                monitor_class=None,
                monitor_interval=1.0,
                monitor=None,
                runtime_handle=_runner_handle(process),
                cancel_requested=None,
                stop_runtime=stop_runtime,
                kill_runtime=kill_runtime,
                on_worker_started=fail_callback
                if callback_name == "on_worker_started"
                else None,
                on_runtime_handle_started=fail_callback
                if callback_name == "on_runtime_handle_started"
                else None,
            )
        production_reaped = process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)

    assert caught.value is fatal
    assert stop_calls == ["stop"]
    assert production_reaped is True
    assert "stop callback" not in caplog.text
    assert caplog.records == []


def _run_fatal_callback_cleanup_scenario(
    process: _CallbackCleanupProcess,
    *,
    stop_runtime: Callable[[], None],
    kill_runtime: Callable[[], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fatal = _FatalCallbackSignal("private callback detail")

    def fail_callback(_value: object) -> None:
        raise fatal

    with (
        caplog.at_level(
            logging.WARNING,
            logger="weft.core.runners.subprocess_runner",
        ),
        pytest.raises(_FatalCallbackSignal) as caught,
    ):
        run_monitored_subprocess(
            process=process,  # type: ignore[arg-type]
            stdin_data=None,
            timeout=5.0,
            limits=None,
            monitor_class=None,
            monitor_interval=1.0,
            monitor=None,
            runtime_handle=_runner_handle(process),  # type: ignore[arg-type]
            cancel_requested=None,
            on_worker_started=fail_callback,
            on_runtime_handle_started=None,
            stop_runtime=stop_runtime,
            kill_runtime=kill_runtime,
        )

    assert caught.value is fatal
    assert "private callback detail" not in caplog.text
    assert caplog.records == []


def test_fatal_callback_cleanup_reaps_after_stop_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    process = _CallbackCleanupProcess()

    def stop_runtime() -> None:
        process.calls.append("stop_runtime")
        raise RuntimeError("private stop detail")

    _run_fatal_callback_cleanup_scenario(
        process,
        stop_runtime=stop_runtime,
        kill_runtime=lambda: process.calls.append("kill_runtime"),
        caplog=caplog,
    )

    assert process.reaped is True
    assert process.calls == [
        "stop_runtime",
        "poll",
        "process.kill",
        f"wait:{subprocess_runner.SUBPROCESS_TERMINATION_WAIT_TIMEOUT}",
    ]


def test_fatal_callback_cleanup_escalates_wait_timeout_through_runtime_kill(
    caplog: pytest.LogCaptureFixture,
) -> None:
    process = _CallbackCleanupProcess(
        wait_failures=[subprocess.TimeoutExpired("fixture", 1.0)]
    )

    _run_fatal_callback_cleanup_scenario(
        process,
        stop_runtime=lambda: process.calls.append("stop_runtime"),
        kill_runtime=lambda: process.calls.append("kill_runtime"),
        caplog=caplog,
    )

    assert process.reaped is True
    assert process.calls == [
        "stop_runtime",
        f"wait:{subprocess_runner.SUBPROCESS_TERMINATION_WAIT_TIMEOUT}",
        "kill_runtime",
        f"wait:{subprocess_runner.SUBPROCESS_TERMINATION_WAIT_TIMEOUT}",
        "poll",
    ]


def test_fatal_callback_cleanup_uses_direct_kill_after_runtime_kill_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    process = _CallbackCleanupProcess(
        wait_failures=[subprocess.TimeoutExpired("fixture", 1.0)]
    )

    def kill_runtime() -> None:
        process.calls.append("kill_runtime")
        raise RuntimeError("private runtime kill detail")

    _run_fatal_callback_cleanup_scenario(
        process,
        stop_runtime=lambda: process.calls.append("stop_runtime"),
        kill_runtime=kill_runtime,
        caplog=caplog,
    )

    assert process.reaped is True
    assert process.calls == [
        "stop_runtime",
        f"wait:{subprocess_runner.SUBPROCESS_TERMINATION_WAIT_TIMEOUT}",
        "kill_runtime",
        "poll",
        "process.kill",
        f"wait:{subprocess_runner.SUBPROCESS_TERMINATION_WAIT_TIMEOUT}",
    ]


def test_fatal_callback_cleanup_protects_throwing_poll_and_still_reaps(
    caplog: pytest.LogCaptureFixture,
) -> None:
    process = _CallbackCleanupProcess(poll_failure=RuntimeError("private poll detail"))

    _run_fatal_callback_cleanup_scenario(
        process,
        stop_runtime=lambda: process.calls.append("stop_runtime"),
        kill_runtime=lambda: process.calls.append("kill_runtime"),
        caplog=caplog,
    )

    assert process.reaped is True
    assert process.calls == [
        "stop_runtime",
        f"wait:{subprocess_runner.SUBPROCESS_TERMINATION_WAIT_TIMEOUT}",
        "poll",
        "process.kill",
        f"wait:{subprocess_runner.SUBPROCESS_TERMINATION_WAIT_TIMEOUT}",
    ]


def test_fatal_callback_cleanup_preserves_signal_across_cleanup_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    process = _CallbackCleanupProcess(
        poll_failure=RuntimeError("private poll detail"),
        kill_failure=RuntimeError("private direct kill detail"),
    )

    def stop_runtime() -> None:
        process.calls.append("stop_runtime")
        raise RuntimeError("private stop detail")

    _run_fatal_callback_cleanup_scenario(
        process,
        stop_runtime=stop_runtime,
        kill_runtime=lambda: process.calls.append("kill_runtime"),
        caplog=caplog,
    )

    assert process.reaped is True
    assert process.calls == [
        "stop_runtime",
        "poll",
        "process.kill",
        f"wait:{subprocess_runner.SUBPROCESS_TERMINATION_WAIT_TIMEOUT}",
    ]


def test_monitor_start_failure_attempts_stop_without_replacing_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    class Monitor:
        def start(self, _pid: int) -> None:
            calls.append("start")
            raise RuntimeError("startup secret")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("cleanup secret")

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.subprocess_runner"):
        outcome = _run_process_with_monitor(Monitor())

    assert outcome.status == "ok"
    assert outcome.stdout == "done\n"
    assert calls == ["start", "stop"]
    assert [record.message for record in caplog.records] == [
        "Subprocess resource monitor failed during startup",
        "Subprocess resource monitor failed while stopping during startup cleanup",
    ]
    assert "secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_monitor_poll_failures_preserve_cached_metrics_and_primary_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    metrics = subprocess_runner.ResourceMetrics(memory_mb=12.5)

    class Monitor:
        def __init__(self) -> None:
            self.metrics_reads = 0

        def start(self, _pid: int) -> None:
            calls.append("start")

        def check_limits(self) -> tuple[bool, str | None]:
            calls.append("check_limits")
            raise RuntimeError("check secret")

        def last_metrics(self) -> subprocess_runner.ResourceMetrics | None:
            calls.append("last_metrics")
            self.metrics_reads += 1
            if self.metrics_reads == 1:
                return metrics
            raise RuntimeError("metrics secret")

        def snapshot(self) -> subprocess_runner.ResourceMetrics:
            raise AssertionError("cached metrics must prevent a snapshot")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("stop secret")

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.subprocess_runner"):
        outcome = _run_process_with_monitor(
            Monitor(),
            script="import time; time.sleep(0.1); print('done')",
        )

    assert outcome.status == "ok"
    assert outcome.stdout == "done\n"
    assert outcome.metrics is metrics
    assert "check_limits" in calls
    assert calls[-1] == "stop"
    assert any(
        record.message == "Subprocess resource monitor failed during limit check"
        for record in caplog.records
    )
    assert any(
        record.message
        in {
            "Subprocess resource monitor failed while reading metrics during limit check",
            "Subprocess resource monitor failed while reading metrics during completion cleanup",
        }
        for record in caplog.records
    )
    assert caplog.records[-1].message == (
        "Subprocess resource monitor failed while stopping during completion cleanup"
    )
    assert "secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_monitor_limit_cleanup_preserves_polled_metrics_and_primary_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    metrics = subprocess_runner.ResourceMetrics(memory_mb=24.0)

    class Monitor:
        def __init__(self) -> None:
            self.metrics_reads = 0

        def start(self, _pid: int) -> None:
            calls.append("start")

        def check_limits(self) -> tuple[bool, str]:
            calls.append("check_limits")
            return False, "memory limit exceeded"

        def last_metrics(self) -> subprocess_runner.ResourceMetrics:
            calls.append("last_metrics")
            self.metrics_reads += 1
            if self.metrics_reads == 1:
                return metrics
            raise RuntimeError("metrics secret")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("stop secret")

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.subprocess_runner"):
        outcome = _run_process_with_monitor(
            Monitor(),
            script="import time; time.sleep(1)",
        )

    assert outcome.status == "limit"
    assert outcome.error == "memory limit exceeded"
    assert outcome.metrics is metrics
    assert calls == ["start", "check_limits", "last_metrics", "last_metrics", "stop"]
    assert [record.message for record in caplog.records] == [
        "Subprocess resource monitor failed while reading metrics during limit cleanup",
        "Subprocess resource monitor failed while stopping during limit cleanup",
    ]
    assert "secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_monitor_snapshot_and_stop_failures_do_not_replace_primary_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    class Monitor:
        def start(self, _pid: int) -> None:
            calls.append("start")

        def check_limits(self) -> tuple[bool, str | None]:
            return True, None

        def last_metrics(self) -> None:
            calls.append("last_metrics")

        def snapshot(self) -> subprocess_runner.ResourceMetrics:
            calls.append("snapshot")
            raise RuntimeError("snapshot secret")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("stop secret")

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.subprocess_runner"):
        outcome = _run_process_with_monitor(Monitor())

    assert outcome.status == "ok"
    assert outcome.stdout == "done\n"
    assert outcome.metrics is None
    assert calls[0] == "start"
    assert "last_metrics" in calls
    assert calls[-2:] == ["snapshot", "stop"]
    assert [record.message for record in caplog.records] == [
        "Subprocess resource monitor failed during final snapshot",
        "Subprocess resource monitor failed while stopping during completion cleanup",
    ]
    assert "secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_monitor_cleanup_failures_do_not_replace_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    metrics = subprocess_runner.ResourceMetrics(memory_mb=31.0)

    class Monitor:
        def __init__(self) -> None:
            self.metrics_reads = 0

        def start(self, _pid: int) -> None:
            calls.append("start")

        def check_limits(self) -> tuple[bool, str | None]:
            calls.append("check_limits")
            return True, None

        def last_metrics(self) -> subprocess_runner.ResourceMetrics:
            calls.append("last_metrics")
            self.metrics_reads += 1
            if self.metrics_reads == 1:
                return metrics
            raise RuntimeError("cancel metrics secret")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("cancel stop secret")

    monitor = Monitor()

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.subprocess_runner"):
        outcome = _run_process_with_monitor(
            monitor,
            script="import time; time.sleep(1)",
            cancel_requested=lambda: monitor.metrics_reads > 0,
        )

    assert outcome.status == "cancelled"
    assert outcome.metrics is metrics
    assert calls[-2:] == ["last_metrics", "stop"]
    assert [record.message for record in caplog.records] == [
        "Subprocess resource monitor failed while reading metrics during cancellation cleanup",
        "Subprocess resource monitor failed while stopping during cancellation cleanup",
    ]
    assert "secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_monitor_cleanup_failures_do_not_replace_timeout(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    metrics = subprocess_runner.ResourceMetrics(memory_mb=32.0)

    def metric_results() -> Iterator[subprocess_runner.ResourceMetrics]:
        yield metrics
        raise RuntimeError("timeout metrics secret")

    class Monitor:
        def __init__(self) -> None:
            self.metrics_reads = 0
            self._metric_results = metric_results()

        def start(self, _pid: int) -> None:
            calls.append("start")

        def check_limits(self) -> tuple[bool, str | None]:
            calls.append("check_limits")
            return True, None

        def last_metrics(self) -> subprocess_runner.ResourceMetrics:
            calls.append("last_metrics")
            self.metrics_reads += 1
            return next(self._metric_results)

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("timeout stop secret")

    monitor = Monitor()

    monkeypatch.setattr(
        subprocess_runner,
        "time",
        _TimeoutAfterMetricsClock(lambda: monitor.metrics_reads > 0),
    )

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.subprocess_runner"):
        outcome = _run_process_with_monitor(
            monitor,
            script="import time; time.sleep(1)",
            timeout=1.0,
        )

    assert outcome.status == "timeout"
    assert outcome.metrics is metrics
    assert calls[-2:] == ["last_metrics", "stop"]
    messages = [record.message for record in caplog.records]
    assert messages[-2:] == [
        "Subprocess resource monitor failed while reading metrics during timeout cleanup",
        "Subprocess resource monitor failed while stopping during timeout cleanup",
    ]
    assert all(
        message
        == "Subprocess resource monitor failed while reading metrics during limit check"
        for message in messages[:-2]
    )
    assert "secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.fixture
def owned_pipe_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[threading.Thread]]:
    """Observe real owner threads without replacing their I/O or lifecycle."""
    threads: list[threading.Thread] = []

    def start_thread(*args: Any, **kwargs: Any) -> threading.Thread:
        thread = threading.Thread(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(
        subprocess_runner,
        "threading",
        SimpleNamespace(Thread=start_thread, Event=threading.Event),
    )
    yield threads
    for thread in threads:
        if thread.ident is not None:
            thread.join(timeout=5.0)
        assert not thread.is_alive(), "owned subprocess adapter survived test cleanup"


def _wait_for_child_marker(path: Path) -> None:
    deadline = real_time.monotonic() + 5.0
    while not path.exists():
        assert real_time.monotonic() < deadline, f"child did not publish {path}"
        threading.Event().wait(0.01)


def _supervise_real_process(
    process: subprocess.Popen[str],
    *,
    stdin_data: str | None = None,
    timeout: float | None = 5.0,
    cancel_requested: Callable[[], bool] | None = None,
    monitor_class: str | None = None,
    monitor: Any = None,
    on_stdout_chunk: Callable[[str, bool], None] | None = None,
    on_stderr_chunk: Callable[[str, bool], None] | None = None,
) -> RunnerOutcome:
    return run_monitored_subprocess(
        process=process,
        stdin_data=stdin_data,
        timeout=timeout,
        limits=None,
        monitor_class=monitor_class,
        monitor_interval=0.01,
        monitor=monitor,
        runtime_handle=_runner_handle(process),
        cancel_requested=cancel_requested,
        on_worker_started=None,
        on_runtime_handle_started=None,
        on_stdout_chunk=on_stdout_chunk,
        on_stderr_chunk=on_stderr_chunk,
        stop_runtime=process.terminate,
        kill_runtime=process.kill,
    )


def _reap_test_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5.0)


def _assert_owned_pipes_released(
    process: subprocess.Popen[str], threads: list[threading.Thread]
) -> None:
    assert process.poll() is not None, "runner returned with its root still alive"
    assert all(not thread.is_alive() for thread in threads)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            assert stream.closed, "runner left an owned pipe open"


@pytest.mark.parametrize("stop_kind", ["timeout", "cancelled"])
def test_input_backpressure_cannot_block_supervision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_pipe_threads: list[threading.Thread],
    stop_kind: str,
) -> None:
    """A ready child never reads input larger than the pipe's capacity [RM-5.2]."""
    ready = tmp_path / "ready"
    script = (
        "from pathlib import Path; import sys,threading; "
        "Path(sys.argv[1]).touch(); threading.Event().wait(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(ready)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    input_blocked = threading.Event()
    assert process.stdin is not None
    original_stdin = process.stdin

    class ObservedInput:
        def __getattr__(self, name: str) -> Any:
            return getattr(original_stdin, name)

        def write(self, data: str) -> int:
            # The pre-fix buffered write attempts the whole 8 MiB against a
            # child that never reads. Forward it unchanged for the RED control.
            input_blocked.set()
            return original_stdin.write(data)

    def observed_write(descriptor: int, data: Any) -> int:
        try:
            return os.write(descriptor, data)
        except BlockingIOError:
            if descriptor == original_stdin.fileno():
                input_blocked.set()
            raise

    process.stdin = cast(IO[str], ObservedInput())
    monkeypatch.setattr(
        subprocess_runner,
        "os",
        SimpleNamespace(
            set_blocking=os.set_blocking,
            read=os.read,
            write=observed_write,
            linesep=os.linesep,
        ),
        raising=False,
    )
    cancel = threading.Event()
    result: queue.Queue[RunnerOutcome | BaseException] = queue.Queue()

    def run() -> None:
        try:
            result.put(
                _supervise_real_process(
                    process,
                    stdin_data="x" * (8 * 1024 * 1024),
                    timeout=0.2 if stop_kind == "timeout" else None,
                    cancel_requested=cancel.is_set,
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            result.put(exc)

    owner = threading.Thread(target=run)
    try:
        _wait_for_child_marker(ready)
        owner.start()
        if stop_kind == "cancelled":
            assert input_blocked.wait(timeout=5.0), (
                "input did not reach pipe backpressure"
            )
            cancel.set()
        owner.join(timeout=5.0)
        assert not owner.is_alive(), "input backpressure disabled supervision"
        outcome = result.get_nowait()
        assert isinstance(outcome, RunnerOutcome), repr(outcome)
        assert outcome.status == stop_kind
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        cancel.set()
        _reap_test_process(process)
        if owner.ident is not None:
            owner.join(timeout=5.0)


@pytest.mark.parametrize(("returncode", "flood"), [(0, False), (7, False), (0, True)])
def test_root_completion_bounds_inherited_pipe_drain(
    tmp_path: Path,
    owned_pipe_threads: list[threading.Thread],
    caplog: pytest.LogCaptureFixture,
    returncode: int,
    flood: bool,
) -> None:
    """An inherited writer is not evidence of a running root [RM-5.2]."""
    ready = tmp_path / "descendant.pid"
    release = tmp_path / "release"
    descendant_source = (
        "import os,sys,time; from pathlib import Path\n"
        "ready=Path(sys.argv[1]); release=Path(sys.argv[2])\n"
        "tmp=ready.with_suffix('.tmp'); tmp.write_text(str(os.getpid())); tmp.replace(ready)\n"
        "while not release.exists():\n"
        " try:\n"
        + (
            "  os.write(1,b'x'*4096); os.write(2,b'y'*4096)\n"
            if flood
            else "  time.sleep(.01)\n"
        )
        + " except BrokenPipeError: time.sleep(.01)\n"
    )
    script = (
        "import subprocess,sys\n"
        "print('ROOT_OUT',flush=True); print('ROOT_ERR',file=sys.stderr,flush=True)\n"
        "subprocess.Popen([sys.executable,'-u','-c',sys.argv[1],sys.argv[2],sys.argv[3]])\n"
        "sys.exit(int(sys.argv[4]))\n"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            script,
            descendant_source,
            str(ready),
            str(release),
            str(returncode),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    result: queue.Queue[RunnerOutcome | BaseException] = queue.Queue()
    descendant: psutil.Process | None = None

    def run() -> None:
        try:
            result.put(_supervise_real_process(process, timeout=0.2))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            result.put(exc)

    owner = threading.Thread(target=run)
    try:
        _wait_for_child_marker(ready)
        descendant = psutil.Process(int(ready.read_text()))
        process.wait(timeout=5.0)
        with caplog.at_level(
            logging.WARNING, logger="weft.core.runners.subprocess_runner"
        ):
            owner.start()
            owner.join(timeout=5.0)
        assert not owner.is_alive(), "completed root waited for inherited pipe EOF"
        outcome = result.get_nowait()
        assert isinstance(outcome, RunnerOutcome), repr(outcome)
        assert outcome.status == ("ok" if returncode == 0 else "error")
        assert outcome.returncode == returncode
        assert (outcome.stdout or "").startswith("ROOT_OUT\n")
        assert (outcome.stderr or "").startswith("ROOT_ERR\n")
        assert descendant.is_running() and descendant.status() != psutil.STATUS_ZOMBIE
        assert "capture" in caplog.text and "EOF" in caplog.text
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        release.touch()
        _reap_test_process(process)
        if owner.ident is not None:
            owner.join(timeout=5.0)
        if descendant is not None:
            with contextlib.suppress(psutil.NoSuchProcess):
                try:
                    descendant.wait(timeout=5.0)
                except psutil.TimeoutExpired:
                    descendant.kill()
                    descendant.wait(timeout=5.0)


def test_monitor_acquisition_failure_reaps_root_and_preserves_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_pipe_threads: list[threading.Thread],
) -> None:
    ready = tmp_path / "ready"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys,time; Path(sys.argv[1]).touch(); time.sleep(30)",
            str(ready),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    failure = TypeError("monitor constructor failure")

    def fail_monitor(*_args: Any, **_kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(subprocess_runner, "load_resource_monitor", fail_monitor)
    try:
        _wait_for_child_marker(ready)
        with pytest.raises(TypeError) as caught:
            _supervise_real_process(process, monitor_class="fixture.Monitor")
        assert caught.value is failure
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


def test_stream_queue_drain_yields_with_continuing_production() -> None:
    """A live producer cannot keep supervision inside one queue drain [RM-5.2]."""
    pending: queue.Queue[str | None] = queue.Queue()
    pending.put("output")
    chunks: list[str] = []

    def replenish(_chunk: str, _final: bool) -> None:
        assert len(chunks) < 10_000, "stream drain did not yield to supervision"
        pending.put("output")

    assert not subprocess_runner._drain_stream_queue(
        pending, chunks, on_chunk=replenish, closed=False
    )
    assert chunks and not pending.empty()


def test_large_input_preserves_text_encoding_and_stream_separation(
    owned_pipe_threads: list[threading.Thread],
) -> None:
    payload = ("é\r\nλ\nx\ry" * 100_000) + "\ud800"
    reference_bytes = io.BytesIO()
    reference_writer = io.TextIOWrapper(
        reference_bytes, encoding="utf-8", errors="replace", newline=None
    )
    reference_writer.write(payload)
    reference_writer.flush()
    encoded = reference_bytes.getvalue()
    reference_reader = io.TextIOWrapper(
        io.BytesIO(encoded), encoding="utf-8", errors="replace", newline=None
    )
    expected = reference_reader.read()
    reference_reader.close()
    reference_writer.close()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys,hashlib; b=sys.stdin.buffer.read(); "
                "sys.stdout.buffer.write(b); "
                "sys.stderr.write(hashlib.sha256(b).hexdigest())"
            ),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        outcome = _supervise_real_process(process, stdin_data=payload, timeout=10.0)
        assert outcome.status == "ok"
        assert outcome.stdout == expected
        assert outcome.stderr == hashlib.sha256(encoded).hexdigest()
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


def test_stdin_encoding_failure_rolls_back_owned_process(
    owned_pipe_threads: list[threading.Thread],
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    try:
        with pytest.raises(UnicodeEncodeError):
            _supervise_real_process(process, stdin_data="\ud800")
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


@pytest.mark.parametrize("failure_phase", ["setup", "read"])
def test_partial_pipe_acquisition_or_read_failure_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
    owned_pipe_threads: list[threading.Thread],
    failure_phase: str,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stderr is not None
    failed_descriptor = process.stderr.fileno()
    failure = OSError("pipe dependency failure")

    def set_blocking(descriptor: int, blocking: bool) -> None:
        if failure_phase == "setup" and descriptor == failed_descriptor:
            raise failure
        os.set_blocking(descriptor, blocking)

    def read(descriptor: int, size: int) -> bytes:
        if failure_phase == "read" and descriptor == failed_descriptor:
            raise failure
        return os.read(descriptor, size)

    monkeypatch.setattr(
        subprocess_runner,
        "os",
        SimpleNamespace(
            set_blocking=set_blocking, read=read, write=os.write, linesep=os.linesep
        ),
    )
    try:
        with pytest.raises(OSError) as caught:
            _supervise_real_process(process)
        assert caught.value is failure
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


@pytest.mark.parametrize("stop_kind", ["timeout", "cancelled"])
def test_continuous_output_yields_to_supervision(
    stop_kind: str, owned_pipe_threads: list[threading.Thread]
) -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import os\nwhile True:\n os.write(1,b'x'*64); os.write(2,b'y'*64)\n",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    cancel = threading.Event()

    def on_chunk(chunk: str, _final: bool) -> None:
        if chunk and stop_kind == "cancelled":
            cancel.set()

    try:
        outcome = _supervise_real_process(
            process,
            timeout=0.2 if stop_kind == "timeout" else 5.0,
            cancel_requested=cancel.is_set,
            on_stdout_chunk=on_chunk,
        )
        assert outcome.status == stop_kind
        assert outcome.stdout
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


def test_interrupted_adapter_start_keeps_join_ownership(
    monkeypatch: pytest.MonkeyPatch,
    owned_pipe_threads: list[threading.Thread],
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    create_thread = subprocess_runner.threading.Thread
    joined: list[threading.Thread] = []
    failure = RuntimeError("start interrupted after creating an OS thread")
    starts = 0

    def interrupted_thread(*args: Any, **kwargs: Any) -> threading.Thread:
        thread = create_thread(*args, **kwargs)
        start = thread.start
        join = thread.join

        def interrupted_start() -> None:
            nonlocal starts
            start()
            starts += 1
            if starts == 2:
                raise failure

        def observed_join(timeout: float | None = None) -> None:
            joined.append(thread)
            join(timeout=timeout)

        monkeypatch.setattr(thread, "start", interrupted_start)
        monkeypatch.setattr(thread, "join", observed_join)
        return thread

    monkeypatch.setattr(subprocess_runner.threading, "Thread", interrupted_thread)
    try:
        with pytest.raises(RuntimeError) as caught:
            _supervise_real_process(process)
        assert caught.value is failure
        started = [thread for thread in owned_pipe_threads if thread.ident is not None]
        assert len(started) == 2
        assert all(thread in joined for thread in started), "lost an acquired adapter"
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


def test_fatal_monitor_cleanup_preserves_primary_execution_failure(
    owned_pipe_threads: list[threading.Thread],
) -> None:
    primary = OSError("stream callback failed")
    cleanup_signal = _FatalCallbackSignal("monitor teardown failed")

    class Monitor:
        def start(self, _pid: int) -> None:
            pass

        def check_limits(self) -> tuple[bool, None]:
            return True, None

        def last_metrics(self) -> None:
            return None

        def stop(self) -> None:
            raise cleanup_signal

    def fail_output(_chunk: str, _final: bool) -> None:
        raise primary

    process = subprocess.Popen(
        [sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with pytest.raises(OSError) as caught:
            _supervise_real_process(
                process, monitor=Monitor(), on_stdout_chunk=fail_output
            )
        assert caught.value is primary
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


@pytest.mark.parametrize(
    "wait_for_drain", [False, True], ids=["exits-unprompted", "awaits-release"]
)
def test_ready_output_drains_without_waiting_between_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_pipe_threads: list[threading.Thread],
    wait_for_drain: bool,
) -> None:
    """Ready output is work, not a reason to spend the EOF grace waiting [RM-5.2]."""
    monkeypatch.setattr(subprocess_runner, "SUBPROCESS_STREAM_READ_SIZE", 32)
    release = tmp_path / "output-consumed"
    expected_stdout = "x" * 100_000
    expected_stderr = "y" * 100_000
    callbacks: dict[str, list[tuple[str, bool]]] = {"stdout": [], "stderr": []}
    received = {"stdout": 0, "stderr": 0}

    def observe(stream: str, chunk: str, final: bool) -> None:
        callbacks[stream].append((chunk, final))
        received[stream] += len(chunk)
        if received == {"stdout": 100_000, "stderr": 100_000}:
            release.touch()

    command = (
        "import os, pathlib, sys, time\n"
        "sys.stdout.write('x' * 100000); sys.stdout.flush()\n"
        "sys.stderr.write('y' * 100000); sys.stderr.flush()\n"
        "os.close(1); os.close(2)\n"
    )
    if wait_for_drain:
        command += (
            f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)\n"
        )
    process = subprocess.Popen(
        [sys.executable, "-c", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        outcome = _supervise_real_process(
            process,
            timeout=3.0,
            on_stdout_chunk=lambda chunk, final: observe("stdout", chunk, final),
            on_stderr_chunk=lambda chunk, final: observe("stderr", chunk, final),
        )
        assert outcome.status == "ok"
        assert outcome.stdout == expected_stdout
        assert outcome.stderr == expected_stderr
        for stream, expected in (
            ("stdout", expected_stdout),
            ("stderr", expected_stderr),
        ):
            assert "".join(chunk for chunk, _ in callbacks[stream]) == expected
            assert [chunk for chunk, final in callbacks[stream] if final] == [""]
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


def test_caller_exception_does_not_hide_fatal_monitor_cleanup(
    owned_pipe_threads: list[threading.Thread],
) -> None:
    """Only this invocation's failure can take precedence over cleanup [RM-5.1]."""
    cleanup_signal = _FatalCallbackSignal("monitor teardown failed")

    class Monitor:
        def start(self, _pid: int) -> None:
            pass

        def check_limits(self) -> tuple[bool, None]:
            return True, None

        def last_metrics(self) -> None:
            return None

        def snapshot(self) -> None:
            return None

        def stop(self) -> None:
            raise cleanup_signal

    process = subprocess.Popen(
        [sys.executable, "-c", "print('done')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        try:
            raise ValueError("caller is handling an unrelated failure")
        except ValueError:
            with pytest.raises(_FatalCallbackSignal) as caught:
                _supervise_real_process(process, monitor=Monitor())
        assert caught.value is cleanup_signal
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


def test_early_root_exit_does_not_turn_unread_input_into_failure(
    monkeypatch: pytest.MonkeyPatch,
    owned_pipe_threads: list[threading.Thread],
) -> None:
    """A child may finish successfully without accepting all supplied stdin."""
    write_errors: list[OSError] = []
    write_attempted = threading.Event()

    def observed_write(descriptor: int, data: bytes | memoryview) -> int:
        try:
            return os.write(descriptor, data)
        except OSError as exc:
            write_errors.append(exc)
            write_attempted.set()
            raise

    def observe_output(_chunk: str, _final: bool) -> None:
        assert write_attempted.wait(5.0), (
            "input adapter did not attempt the closed pipe"
        )

    monkeypatch.setattr(
        subprocess_runner,
        "os",
        SimpleNamespace(
            set_blocking=os.set_blocking,
            read=os.read,
            write=observed_write,
            linesep=os.linesep,
        ),
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "print('EARLY_EXIT')"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        process.wait(timeout=5.0)
        outcome = _supervise_real_process(
            process,
            stdin_data="x" * (8 * 1024 * 1024),
            on_stdout_chunk=observe_output,
        )
        assert outcome.status == "ok"
        assert outcome.stdout == "EARLY_EXIT\n"
        assert any(
            isinstance(exc, BrokenPipeError)
            or (sys.platform == "win32" and exc.errno == errno.EINVAL)
            for exc in write_errors
        ), "the real closed input pipe was not exercised"
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


@pytest.mark.parametrize(
    ("platform", "error_number", "ignore_error"),
    [
        pytest.param("win32", errno.EINVAL, True, id="windows-einval"),
        pytest.param("linux", errno.EINVAL, False, id="non-windows-einval"),
        pytest.param("win32", errno.EIO, False, id="windows-eio"),
    ],
)
def test_closed_input_einval_is_only_ignored_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    owned_pipe_threads: list[threading.Thread],
    platform: str,
    error_number: int,
    ignore_error: bool,
) -> None:
    """Partition CPython's Windows closed-pipe exception from real I/O faults."""
    failure = OSError(error_number, "input pipe write failed")
    write_attempted = threading.Event()

    def closed_pipe_write(_descriptor: int, _data: bytes | memoryview) -> int:
        write_attempted.set()
        raise failure

    monkeypatch.setattr(
        subprocess_runner,
        "os",
        SimpleNamespace(
            set_blocking=os.set_blocking,
            read=os.read,
            write=closed_pipe_write,
            linesep=os.linesep,
        ),
    )
    monkeypatch.setattr(
        subprocess_runner,
        "sys",
        SimpleNamespace(platform=platform),
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read(); print('done')"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        if ignore_error:
            outcome = _supervise_real_process(process, stdin_data="input")
            assert outcome.status == "ok"
            assert outcome.stdout == "done\n"
        else:
            with pytest.raises(OSError) as caught:
                _supervise_real_process(process, stdin_data="input")
            assert caught.value is failure
        assert write_attempted.is_set()
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


@pytest.mark.parametrize("terminal", ["ok", "error", "cancelled", "timeout", "limit"])
def test_terminal_capture_delivers_backlog_after_capture_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_pipe_threads: list[threading.Thread],
    terminal: str,
) -> None:
    """A slow sink cannot discard data captured before terminal shutdown [RM-5.2]."""
    monkeypatch.setattr(subprocess_runner, "SUBPROCESS_STREAM_READ_SIZE", 1024)
    ready = tmp_path / "output-written"
    if terminal == "timeout":
        # This case isolates retained output, not interpreter startup latency.
        # The separate input-backpressure test owns the execution-clock proof.
        monkeypatch.setattr(subprocess_runner, "time", _OutputReadyClock(ready))
    expected = "x" * (128 * 1024)
    script = (
        "import pathlib, sys, threading\n"
        "sys.stdout.buffer.write(b'x' * (128 * 1024)); sys.stdout.buffer.flush()\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        + (
            f"sys.exit({7 if terminal == 'error' else 0})\n"
            if terminal in {"ok", "error"}
            else "threading.Event().wait(30)\n"
        )
    )
    callbacks: list[tuple[str, bool]] = []

    def slow_sink(chunk: str, final: bool) -> None:
        callbacks.append((chunk, final))
        if chunk:
            # This is the sink's controlled latency, not child readiness. The
            # revised contract must deliver a finite backlog beyond 250 ms.
            threading.Event().wait(0.015)

    class LimitAfterOutput:
        def start(self, _pid: int) -> None:
            pass

        def check_limits(self) -> tuple[bool, str | None]:
            return (False, "test resource limit") if ready.exists() else (True, None)

        def last_metrics(self) -> None:
            return None

        def stop(self) -> None:
            pass

    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        outcome = _supervise_real_process(
            process,
            timeout=0.3 if terminal == "timeout" else 5.0,
            cancel_requested=ready.exists if terminal == "cancelled" else None,
            monitor=LimitAfterOutput() if terminal == "limit" else None,
            on_stdout_chunk=slow_sink,
        )
        assert ready.exists(), "the root must produce the complete fixture output"
        assert outcome.status == terminal
        assert outcome.stdout == expected
        assert "".join(chunk for chunk, _ in callbacks) == expected
        assert [chunk for chunk, final in callbacks if final] == [""]
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


def test_late_captured_output_callback_failure_preserves_exception_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    owned_pipe_threads: list[threading.Thread],
) -> None:
    """Failure while delivering a frozen backlog remains an execution failure."""
    monkeypatch.setattr(subprocess_runner, "SUBPROCESS_STREAM_READ_SIZE", 1024)
    failure = OSError("captured-output sink failed")
    delivered = 0
    callback_failed = False
    calls_after_failure = 0

    def fail_late(chunk: str, _final: bool) -> None:
        nonlocal delivered, callback_failed, calls_after_failure
        if callback_failed:
            calls_after_failure += 1
        delivered += len(chunk)
        if delivered >= 64 * 1024:
            callback_failed = True
            raise failure
        if chunk:
            threading.Event().wait(0.015)

    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * (128 * 1024))"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with pytest.raises(OSError) as caught:
            _supervise_real_process(process, on_stdout_chunk=fail_late)
        assert caught.value is failure
        assert calls_after_failure == 0
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        _reap_test_process(process)


def test_capture_join_reports_adapter_error_before_delivering_callbacks(
    monkeypatch: pytest.MonkeyPatch,
    owned_pipe_threads: list[threading.Thread],
) -> None:
    """An adapter failing at capture completion precedes sink errors [RM-5.1]."""
    failure = OSError("read failed after root completion")
    accepted_exit = threading.Event()
    stderr_read = threading.Event()
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stderr.write('queued diagnostic')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    stdout_descriptor = process.stdout.fileno()
    original_poll = process.poll

    def observe_poll() -> int | None:
        result = original_poll()
        if result is not None:
            accepted_exit.set()
        return result

    def fail_after_exit(descriptor: int, size: int) -> bytes:
        # Hold only the read dependency until the actual owner's root poll.
        # Both readers then finish during capture joins, after the loop checks.
        assert accepted_exit.wait(5.0), "supervisor did not observe root completion"
        if descriptor == stdout_descriptor:
            raise failure
        data = os.read(descriptor, size)
        if data:
            stderr_read.set()
        return data

    def fail_sink(_chunk: str, _final: bool) -> None:
        raise RuntimeError("sink failure must not hide the adapter failure")

    monkeypatch.setattr(process, "poll", observe_poll)
    monkeypatch.setattr(
        subprocess_runner,
        "os",
        SimpleNamespace(
            read=fail_after_exit,
            write=os.write,
            set_blocking=os.set_blocking,
            linesep=os.linesep,
        ),
    )
    try:
        with pytest.raises(OSError) as caught:
            _supervise_real_process(process, on_stderr_chunk=fail_sink)
        assert caught.value is failure
        assert stderr_read.is_set(), "a competing callback must have captured data"
        _assert_owned_pipes_released(process, owned_pipe_threads)
    finally:
        accepted_exit.set()
        _reap_test_process(process)
