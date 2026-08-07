"""Tests for monitored subprocess runner behavior."""

from __future__ import annotations

import itertools
import logging
import queue
import subprocess
import sys
import time as real_time
from collections.abc import Callable, Iterator
from typing import Any

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
            db_path=None,
            config=None,
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


def test_stream_reader_normalizes_crlf_split_across_chunks() -> None:
    class ChunkedBuffer:
        def __init__(self) -> None:
            self._chunks = iter((b"\r", b"\nnext\r", b""))

        def read1(self, _size: int) -> bytes:
            return next(self._chunks)

    class ChunkedStream:
        buffer = ChunkedBuffer()
        encoding = "utf-8"
        errors = "strict"

    target_queue: queue.Queue[str | None] = queue.Queue()

    subprocess_runner._start_stream_reader(ChunkedStream(), target_queue)

    assert target_queue.get(timeout=1.0) == "\nnext"
    assert target_queue.get(timeout=1.0) == "\n"
    assert target_queue.get(timeout=1.0) is None


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
            db_path=None,
            config=None,
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
                db_path=None,
                config=None,
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

    callback_args: dict[str, Callable[[Any], None] | None] = {
        "on_worker_started": None,
        "on_runtime_handle_started": None,
    }
    callback_args[callback_name] = fail_callback
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
                db_path=None,
                config=None,
                runtime_handle=_runner_handle(process),
                cancel_requested=None,
                stop_runtime=stop_runtime,
                kill_runtime=kill_runtime,
                **callback_args,
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
            db_path=None,
            config=None,
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
        == "Subprocess resource monitor failed while reading metrics during limit check"
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
