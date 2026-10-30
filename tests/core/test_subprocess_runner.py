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
import select
import selectors
import socket
import subprocess
import sys
import threading
import time as real_time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import psutil
import pytest

from weft.core.runners import subprocess_runner
from weft.core.runners.outcome import RunnerOutcome
from weft.core.runners.subprocess_runner import (
    open_supervised_process,
    run_monitored_subprocess,
)
from weft.ext import RunnerHandle

if sys.platform == "win32":
    import _winapi

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
            # A backpressured child reaches its marker only as delivery frees
            # pipe capacity; this is a failure bound, not a timing assumption.
            assert now - self.origin < _EVENT_FAILURE_BOUND, (
                "child did not finish fixture output"
            )
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


_EVENT_FAILURE_BOUND = 30.0
"""Generous outer bound for an expected event; only a hang reaches it."""

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="injects through the POSIX leaf's os calls"
)
_WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="exercises the Windows overlapped leaf"
)


class _Forwarding:
    """Forward every attribute to a real module except explicit overrides."""

    def __init__(self, target: Any, **overrides: Any) -> None:
        self._target = target
        self.__dict__.update(overrides)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class _ObservedOverlapped:
    """Forward one real overlapped operation while observing its retirement."""

    def __init__(
        self, ov: Any, on_result: Callable[[Any, bool], tuple[int, int]]
    ) -> None:
        self._ov = ov
        self._on_result = on_result

    @property
    def event(self) -> int:
        return int(self._ov.event)

    def cancel(self) -> None:
        self._ov.cancel()

    def getbuffer(self) -> bytes | None:
        return cast("bytes | None", self._ov.getbuffer())

    def GetOverlappedResult(self, wait: bool) -> tuple[int, int]:
        return self._on_result(self._ov, wait)


def _patch_os(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    monkeypatch.setattr(subprocess_runner, "os", _Forwarding(os, **overrides))


_WINDOWS_RUNNER: Any = subprocess_runner
"""The runner module typed for Windows-only names absent from other platforms."""


def _patch_winapi(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    real = _WINDOWS_RUNNER._winapi
    monkeypatch.setattr(subprocess_runner, "_winapi", _Forwarding(real, **overrides))


def _winerror(code: int) -> OSError:
    return OSError(None, f"injected winerror {code}", None, code)


class _ChildBarrier:
    """A localhost socket the child blocks on until the test releases it."""

    def __init__(self) -> None:
        self._server = socket.create_server(("127.0.0.1", 0))
        self._server.settimeout(_EVENT_FAILURE_BOUND)
        self.port = int(self._server.getsockname()[1])
        self._conn: socket.socket | None = None

    def accept(self) -> None:
        self._conn, _ = self._server.accept()

    def release(self) -> None:
        assert self._conn is not None, "child never connected to the barrier"
        self._conn.sendall(b"\n")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._server.close()


_BARRIER_PRELUDE = (
    "import os,socket,sys,time\n"
    "barrier=socket.create_connection(('127.0.0.1',int(sys.argv[1])))"
    ".makefile('rb')\n"
)
"""Child prelude: ``barrier.readline()`` blocks until the test releases it."""


class _WindowsWaitSpy:
    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.real_wait = _WINDOWS_RUNNER._winapi.WaitForMultipleObjects
        self.timeouts: list[float | None] = []
        self.returns = 0

    def wait(self, handles: Any, wait_all: bool, timeout_ms: int) -> int:
        active = self.process.returncode is None
        if active:
            self.timeouts.append(
                None
                if timeout_ms == _WINDOWS_RUNNER._winapi.INFINITE
                else timeout_ms / 1000
            )
        result = int(self.real_wait(handles, wait_all, timeout_ms))
        if active:
            self.returns += 1
        return result


class _SpySelector(selectors.BaseSelector):
    """Forward a real selector while recording waits and registrations."""

    def __init__(self, *, fail_register_at: int | None = None) -> None:
        self._selector = selectors.DefaultSelector()
        self._fail_register_at = fail_register_at
        self.timeouts: list[float | None] = []
        self.returns = 0
        self.registered: list[int] = []
        self.closed = False

    def register(
        self, fileobj: Any, events: int, data: Any = None
    ) -> selectors.SelectorKey:
        if self._fail_register_at == len(self.registered) + 1:
            raise OSError("injected registration failure")
        key = self._selector.register(fileobj, events, data)
        self.registered.append(key.fd)
        return key

    def unregister(self, fileobj: Any) -> selectors.SelectorKey:
        return self._selector.unregister(fileobj)

    def select(
        self, timeout: float | None = None
    ) -> list[tuple[selectors.SelectorKey, int]]:
        self.timeouts.append(timeout)
        events = self._selector.select(timeout)
        self.returns += 1
        return events

    def get_map(self) -> Mapping[Any, selectors.SelectorKey]:
        return self._selector.get_map()

    def close(self) -> None:
        self.closed = True
        self._selector.close()


def _start_owner(
    run: Callable[[], RunnerOutcome],
) -> tuple[threading.Thread, queue.Queue[RunnerOutcome | BaseException]]:
    result: queue.Queue[RunnerOutcome | BaseException] = queue.Queue()

    def target() -> None:
        try:
            result.put(run())
        except (AssertionError, OSError, RuntimeError, TypeError, ValueError) as exc:
            result.put(exc)

    owner = threading.Thread(target=target)
    owner.start()
    return owner, result


def _owner_outcome(
    owner: threading.Thread, result: queue.Queue[RunnerOutcome | BaseException]
) -> RunnerOutcome:
    owner.join(timeout=_EVENT_FAILURE_BOUND)
    assert not owner.is_alive(), "supervisor did not return"
    outcome = result.get_nowait()
    assert isinstance(outcome, RunnerOutcome), repr(outcome)
    return outcome


def _run_process_with_monitor(
    monitor: Any,
    *,
    script: str = "print('done')",
    monitor_interval: float = 0.01,
    timeout: float = 5.0,
    cancel_requested: Callable[[], bool] | None = None,
) -> RunnerOutcome:
    process = open_supervised_process(
        [sys.executable, "-c", script],
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
    process = open_supervised_process(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))",
            raw.hex(),
        ],
        pipe_stdin=False,
        encoding="utf-8",
        errors="replace",
    )
    callbacks: list[tuple[str, bool]] = []
    try:
        outcome = _supervise_real_process(
            process,
            on_stdout_chunk=lambda chunk, final: callbacks.append((chunk, final)),
        )
        assert outcome.stdout == expected
        assert "".join(chunk for chunk, _ in callbacks) == expected
        assert [chunk for chunk, final in callbacks if final] == [""], (
            "EOF must be published exactly once"
        )
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


def test_completed_process_at_timeout_wake_boundary_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = open_supervised_process(
        [sys.executable, "-c", "print('done')"],
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
    process = open_supervised_process(
        [sys.executable, "-c", "print('done')"],
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
    process = open_supervised_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
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


def _assert_owned_pipes_released(process: subprocess.Popen[str]) -> None:
    assert process.poll() is not None, "runner returned with its root still alive"
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            assert stream.closed, "runner left an owned pipe open"


@pytest.mark.parametrize("stop_kind", ["timeout", "cancelled"])
def test_input_backpressure_cannot_block_supervision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_kind: str,
) -> None:
    """A ready child never reads input larger than the pipe's capacity [RM-5.2]."""
    ready = tmp_path / "ready"
    script = (
        "from pathlib import Path; import sys,threading; "
        "Path(sys.argv[1]).touch(); threading.Event().wait(30)"
    )
    process = open_supervised_process(
        [sys.executable, "-c", script, str(ready)],
        encoding="utf-8",
        errors="replace",
    )
    input_blocked = threading.Event()
    assert process.stdin is not None
    stdin_descriptor = process.stdin.fileno()
    if sys.platform == "win32":
        real_write_file = _WINDOWS_RUNNER._winapi.WriteFile

        def observed_write_file(handle: int, data: Any, overlapped: bool) -> Any:
            ov, err = real_write_file(handle, data, overlapped)
            if handle == stdin_descriptor and err == _winapi.ERROR_IO_PENDING:
                input_blocked.set()
            return ov, err

        _patch_winapi(monkeypatch, WriteFile=observed_write_file)

    def wait_for_input_backpressure() -> bool:
        if sys.platform == "win32":
            return input_blocked.wait(timeout=5.0)
        # Observe the OS pipe itself: it is full once it stops being writable.
        deadline = real_time.monotonic() + 5.0
        while real_time.monotonic() < deadline:
            _, writable, _ = select.select([], [stdin_descriptor], [], 0)
            if not writable:
                return True
            threading.Event().wait(0.01)
        return False

    cancel = threading.Event()
    owner: threading.Thread | None = None
    try:
        _wait_for_child_marker(ready)
        owner, result = _start_owner(
            lambda: _supervise_real_process(
                process,
                stdin_data="x" * (8 * 1024 * 1024),
                timeout=0.2 if stop_kind == "timeout" else None,
                cancel_requested=cancel.is_set,
            )
        )
        if stop_kind == "cancelled":
            assert wait_for_input_backpressure(), (
                "input did not reach pipe backpressure"
            )
            cancel.set()
        outcome = _owner_outcome(owner, result)
        assert outcome.status == stop_kind
        _assert_owned_pipes_released(process)
    finally:
        cancel.set()
        _reap_test_process(process)
        if owner is not None:
            owner.join(timeout=5.0)


def test_idle_subprocess_waits_on_readiness_without_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent child costs the supervisor zero wakes [RM-5.2] [WT-2]."""
    barrier = _ChildBarrier()
    spies: list[Any] = []
    sessions: list[Any] = []
    real_open = subprocess_runner._open_process_io

    def open_observed(process: subprocess.Popen[str], **kwargs: Any) -> Any:
        if sys.platform == "win32":
            wait_spy = _WindowsWaitSpy(process)
            spies.append(wait_spy)
            _patch_winapi(monkeypatch, WaitForMultipleObjects=wait_spy.wait)
            session = real_open(process, **kwargs)
        else:
            spy = _SpySelector()
            spies.append(spy)
            session = subprocess_runner._PosixSelectorProcessIO(
                process, selector_factory=lambda: spy, **kwargs
            )
        sessions.append(session)
        return session

    monkeypatch.setattr(subprocess_runner, "_open_process_io", open_observed)
    script = _BARRIER_PRELUDE + (
        "time.sleep(0.2); print('ready', flush=True)\n"
        "barrier.readline(); print('echo', flush=True)\n"
        "barrier.readline()\n"
    )
    process = open_supervised_process(
        [sys.executable, "-c", script, str(barrier.port)],
        pipe_stdin=False,
        encoding="utf-8",
    )
    seen = {"ready": threading.Event(), "echo": threading.Event()}

    def on_chunk(chunk: str, _final: bool) -> None:
        for word, event in seen.items():
            if word in chunk:
                event.set()

    owner: threading.Thread | None = None
    try:
        barrier.accept()
        owner, result = _start_owner(
            lambda: _supervise_real_process(
                process, timeout=None, on_stdout_chunk=on_chunk
            )
        )
        assert seen["ready"].wait(_EVENT_FAILURE_BOUND), "first output not delivered"
        assert len(spies) == 1
        wakes_before = spies[0].returns
        # Controlled silent window: the child is blocked on the barrier.
        threading.Event().wait(0.2)
        assert spies[0].returns == wakes_before, (
            f"supervisor woke while the child was silent: {spies[0].timeouts}"
        )
        barrier.release()
        assert seen["echo"].wait(_EVENT_FAILURE_BOUND), "released output not delivered"
        barrier.release()
        outcome = _owner_outcome(owner, result)
        assert outcome.status == "ok"
        assert outcome.stdout == "ready\necho\n"
        assert [session.owned_threads() for session in sessions] == [()]
        for spy in spies:
            # None waits for readiness alone; 0.0 only services queued delivery.
            assert all(timeout in (None, 0.0) for timeout in spy.timeouts), spy.timeouts
            if isinstance(spy, _SpySelector):
                assert spy.closed
        _assert_owned_pipes_released(process)
    finally:
        barrier.close()
        _reap_test_process(process)
        if owner is not None:
            owner.join(timeout=5.0)


def _spawn_inherited_writer_root(
    tmp_path: Path, root_body: str
) -> tuple[subprocess.Popen[str], Path]:
    """Start a root that leaves an idle descendant holding its stdout/stderr."""
    ready = tmp_path / "descendant.pid"
    descendant_source = (
        "import os,sys,time; from pathlib import Path\n"
        "p=Path(sys.argv[1]); t=p.with_suffix('.tmp')\n"
        "t.write_text(str(os.getpid())); t.replace(p)\n"
        "time.sleep(60)\n"
    )
    script = (
        "import subprocess,sys,time; from pathlib import Path\n"
        + root_body
        + "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]])\n"
        "p=Path(sys.argv[2])\n"
        "while not p.exists(): time.sleep(.01)\n"
        "Path(sys.argv[3]).write_text(repr(time.monotonic()))\n"
    )
    process = open_supervised_process(
        [
            sys.executable,
            "-c",
            script,
            descendant_source,
            str(ready),
            str(tmp_path / "root-exit"),
        ],
        pipe_stdin=False,
        encoding="utf-8",
    )
    return process, ready


def _reap_descendant(ready: Path) -> None:
    if not ready.exists():
        return
    with contextlib.suppress(psutil.NoSuchProcess):
        descendant = psutil.Process(int(ready.read_text()))
        descendant.kill()
        descendant.wait(timeout=5.0)


def test_root_exit_with_inherited_pipe_and_no_deadlines_completes_within_drain_budget(
    tmp_path: Path,
) -> None:
    """Child exit wakes a deadline-free wait on its own [RM-5.2]."""
    process, ready = _spawn_inherited_writer_root(
        tmp_path, "print('ROOT', flush=True)\n"
    )
    returned_at: list[float] = []
    owner: threading.Thread | None = None

    def run() -> RunnerOutcome:
        try:
            return _supervise_real_process(process, timeout=None)
        finally:
            returned_at.append(real_time.monotonic())

    try:
        owner, result = _start_owner(run)
        outcome = _owner_outcome(owner, result)
        assert outcome.status == "ok"
        assert outcome.stdout == "ROOT\n"
        root_exit = float((tmp_path / "root-exit").read_text())
        assert returned_at[0] - root_exit < (
            subprocess_runner.SUBPROCESS_STREAM_DRAIN_TIMEOUT + 0.5
        )
        descendant = psutil.Process(int(ready.read_text()))
        assert descendant.is_running() and descendant.status() != psutil.STATUS_ZOMBIE
        _assert_owned_pipes_released(process)
    finally:
        _reap_descendant(ready)
        _reap_test_process(process)
        if owner is not None:
            owner.join(timeout=5.0)


def test_queued_delivery_never_waits_behind_blocking_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued chunks are delivered without waiting for new readiness [RM-5.2]."""
    monkeypatch.setattr(subprocess_runner, "SUBPROCESS_STREAM_READ_SIZE", 64)
    # Several delivery turns' worth of chunks, written in one burst.
    total = 64 * subprocess_runner.SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS * 4
    barrier = _ChildBarrier()
    script = _BARRIER_PRELUDE + (
        f"os.write(1, b'x' * {total})\nbarrier.readline()\nos.write(1, b'done')\n"
    )
    process = open_supervised_process(
        [sys.executable, "-c", script, str(barrier.port)], pipe_stdin=False
    )
    received = 0

    def release_after_all_output(chunk: str, _final: bool) -> None:
        nonlocal received
        received += len(chunk)
        if received == total:
            barrier.release()

    owner: threading.Thread | None = None
    try:
        barrier.accept()
        owner, result = _start_owner(
            lambda: _supervise_real_process(
                process, timeout=None, on_stdout_chunk=release_after_all_output
            )
        )
        outcome = _owner_outcome(owner, result)
        assert outcome.status == "ok"
        assert outcome.stdout == "x" * total + "done"
        _assert_owned_pipes_released(process)
    finally:
        barrier.close()
        _reap_test_process(process)
        if owner is not None:
            owner.join(timeout=5.0)


class _StdoutReadLedger:
    """Record active-phase stdout read requests against undelivered bytes."""

    SHORT_READ = 256
    """The kernel may legitimately return short reads; this forces many."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        self.descriptor = process.stdout.fileno()
        self.accepted_exit = threading.Event()
        self.requests: list[tuple[int, int]] = []
        self.read = 0
        self.delivered = 0
        self._poll = process.poll

    def poll(self) -> int | None:
        returncode = self._poll()
        if returncode is not None:
            self.accepted_exit.set()
        return returncode

    def request(self, size: int) -> int:
        if not self.accepted_exit.is_set():
            self.requests.append((self.read - self.delivered, size))
        return min(size, self.SHORT_READ)

    def deliver(self, chunk: str, _final: bool) -> None:
        self.delivered += len(chunk)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if sys.platform == "win32":
            real_read_file = _WINDOWS_RUNNER._winapi.ReadFile

            def observe_result(ov: Any, wait: bool) -> tuple[int, int]:
                nread, err = ov.GetOverlappedResult(wait)
                if err == 0:
                    self.read += nread
                return nread, err

            def read_file(handle: int, size: int, overlapped: bool) -> Any:
                if handle != self.descriptor:
                    return real_read_file(handle, size, overlapped)
                ov, err = real_read_file(handle, self.request(size), overlapped)
                return _ObservedOverlapped(ov, observe_result), err

            _patch_winapi(monkeypatch, ReadFile=read_file)
            return

        def read(descriptor: int, size: int) -> bytes:
            if descriptor != self.descriptor:
                return os.read(descriptor, size)
            data = os.read(descriptor, self.request(size))
            self.read += len(data)
            return data

        _patch_os(monkeypatch, read=read)


def test_output_reads_never_carry_in_flight_past_turn_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No read request can carry undelivered output past the bound [RM-5.2]."""
    bound = subprocess_runner.SUBPROCESS_STREAM_TURN_READ_BYTES
    process = open_supervised_process(
        [
            sys.executable,
            "-c",
            "import os\nfor _ in range(64): os.write(1, b'x' * 8192)\n",
        ],
        pipe_stdin=False,
    )
    ledger = _StdoutReadLedger(process)
    monkeypatch.setattr(process, "poll", ledger.poll)
    ledger.install(monkeypatch)
    try:
        outcome = _supervise_real_process(
            process, timeout=None, on_stdout_chunk=ledger.deliver
        )
        assert outcome.status == "ok"
        assert outcome.stdout == "x" * (64 * 8192)
        assert ledger.requests, "no active-phase reads were observed"
        oversized = [
            (outstanding, size)
            for outstanding, size in ledger.requests
            if outstanding + size > bound
            or size > subprocess_runner.SUBPROCESS_STREAM_READ_SIZE
        ]
        assert oversized == []
        assert max(outstanding for outstanding, _ in ledger.requests) >= 48 * 1024, (
            "the fixture never built a partially delivered backlog"
        )
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


def test_terminal_capture_collects_buffered_output_with_inherited_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capture takes everything buffered before root exit, then stops [RM-5.2]."""
    monkeypatch.setattr(subprocess_runner, "SUBPROCESS_STREAM_READ_SIZE", 1024)
    payload = 12 * 1024  # several reads, and within any platform's pipe buffer
    process, ready = _spawn_inherited_writer_root(
        tmp_path,
        f"sys.stdout.buffer.write(b'x' * {payload}); sys.stdout.buffer.flush()\n",
    )
    callbacks: list[str] = []
    try:
        # All root output is in the kernel pipe before supervision begins.
        process.wait(timeout=_EVENT_FAILURE_BOUND)
        started = real_time.monotonic()
        outcome = _supervise_real_process(
            process,
            timeout=None,
            on_stdout_chunk=lambda chunk, _final: callbacks.append(chunk),
        )
        elapsed = real_time.monotonic() - started
        assert outcome.status == "ok"
        assert outcome.stdout == "x" * payload
        assert "".join(callbacks) == "x" * payload
        # An idle inherited writer ends capture on the empty-pipe observation.
        assert elapsed < subprocess_runner.SUBPROCESS_STREAM_DRAIN_TIMEOUT + 0.5
        descendant = psutil.Process(int(ready.read_text()))
        assert descendant.is_running() and descendant.status() != psutil.STATUS_ZOMBIE
        _assert_owned_pipes_released(process)
    finally:
        _reap_descendant(ready)
        _reap_test_process(process)


@pytest.mark.parametrize(("returncode", "flood"), [(0, False), (7, False), (0, True)])
def test_root_completion_bounds_inherited_pipe_drain(
    tmp_path: Path,
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
    process = open_supervised_process(
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
        _assert_owned_pipes_released(process)
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
) -> None:
    ready = tmp_path / "ready"
    process = open_supervised_process(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys,time; Path(sys.argv[1]).touch(); time.sleep(30)",
            str(ready),
        ],
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
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


def test_stream_queue_drain_yields_with_continuing_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live producer cannot keep supervision inside one delivery turn [RM-5.2]."""
    monkeypatch.setattr(subprocess_runner, "SUBPROCESS_STREAM_READ_SIZE", 64)
    process = open_supervised_process(
        [sys.executable, "-c", "import os\nwhile True: os.write(1, b'x' * 64)\n"],
        pipe_stdin=False,
    )
    delivered: list[str] = []

    def sink(chunk: str, _final: bool) -> None:
        delivered.append(chunk)

    session = subprocess_runner._open_process_io(
        process, stdin_data=None, on_stdout_chunk=sink, on_stderr_chunk=None
    )
    try:
        session.wait_and_drain(None)
        cap = subprocess_runner.SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS
        assert 0 < len(delivered) <= cap, "one turn exceeded the delivery cap"
        assert process.poll() is None, "producer exited before the bounded turn"
    finally:
        session.close()
        _reap_test_process(process)


@pytest.mark.parametrize(
    ("max_chunks", "deadline"),
    [(1, float("inf")), (16, float("-inf"))],
    ids=["chunk-cap", "expired-deadline"],
)
def test_stream_delivery_yields_with_queued_work(
    max_chunks: int, deadline: float
) -> None:
    """Either delivery bound leaves queued output for the next turn [RM-5.2]."""
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    delivered: list[str] = []
    with os.fdopen(read_fd, "r", encoding="utf-8") as pipe:
        state = subprocess_runner._OutputStreamState(
            pipe, lambda chunk, _final: delivered.append(chunk)
        )
        state.accept(b"first")
        state.accept(b"second")
        state.deliver(max_chunks, deadline)
        assert delivered == ["first"]
        assert list(state.pending_chunks) == ["second"]
        state.deliver(16, float("inf"))
        assert delivered == ["first", "second"]
        assert state.in_flight_bytes == 0


def test_large_input_preserves_text_encoding_and_stream_separation() -> None:
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
    process = open_supervised_process(
        [
            sys.executable,
            "-c",
            (
                "import sys,hashlib; b=sys.stdin.buffer.read(); "
                "sys.stdout.buffer.write(b); "
                "sys.stderr.write(hashlib.sha256(b).hexdigest())"
            ),
        ],
        encoding="utf-8",
        errors="replace",
    )
    try:
        outcome = _supervise_real_process(process, stdin_data=payload, timeout=10.0)
        assert outcome.status == "ok"
        assert outcome.stdout == expected
        assert outcome.stderr == hashlib.sha256(encoded).hexdigest()
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


def test_stdin_encoding_failure_rolls_back_owned_process() -> None:
    process = open_supervised_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        encoding="utf-8",
        errors="strict",
    )
    try:
        with pytest.raises(UnicodeEncodeError):
            _supervise_real_process(process, stdin_data="\ud800")
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


@pytest.mark.parametrize("failure_phase", ["setup", "read"])
def test_partial_pipe_acquisition_or_read_failure_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    # The child writes before sleeping so a readiness-driven read is due.
    process = open_supervised_process(
        [
            sys.executable,
            "-c",
            "import sys,time; sys.stderr.write('x'); sys.stderr.flush(); time.sleep(30)",
        ],
    )
    assert process.stderr is not None
    failed_descriptor = process.stderr.fileno()
    failure = OSError("pipe dependency failure")
    if sys.platform == "win32":
        real_read_file = _WINDOWS_RUNNER._winapi.ReadFile

        def fail_result(_ov: Any, _wait: bool) -> tuple[int, int]:
            raise failure

        def read_file(handle: int, size: int, overlapped: bool) -> Any:
            if handle != failed_descriptor:
                return real_read_file(handle, size, overlapped)
            if failure_phase == "setup":
                raise failure
            ov, err = real_read_file(handle, size, overlapped)
            return _ObservedOverlapped(ov, fail_result), err

        _patch_winapi(monkeypatch, ReadFile=read_file)
    else:

        def set_blocking(descriptor: int, blocking: bool) -> None:
            if failure_phase == "setup" and descriptor == failed_descriptor:
                raise failure
            os.set_blocking(descriptor, blocking)

        def read(descriptor: int, size: int) -> bytes:
            if failure_phase == "read" and descriptor == failed_descriptor:
                raise failure
            return os.read(descriptor, size)

        _patch_os(monkeypatch, set_blocking=set_blocking, read=read)
    try:
        with pytest.raises(OSError) as caught:
            _supervise_real_process(process)
        assert caught.value is failure
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


@pytest.mark.parametrize("stop_kind", ["timeout", "cancelled"])
def test_continuous_output_yields_to_supervision(stop_kind: str) -> None:
    process = open_supervised_process(
        [
            sys.executable,
            "-u",
            "-c",
            "import os\nwhile True:\n os.write(1,b'x'*64); os.write(2,b'y'*64)\n",
        ],
        pipe_stdin=False,
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
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


@_POSIX_ONLY
def test_process_io_initialization_failure_releases_registered_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after the first registration releases every acquired source."""
    spy = _SpySelector(fail_register_at=2)

    def open_with_spy(process: subprocess.Popen[str], **kwargs: Any) -> Any:
        return subprocess_runner._PosixSelectorProcessIO(
            process, selector_factory=lambda: spy, **kwargs
        )

    monkeypatch.setattr(subprocess_runner, "_open_process_io", open_with_spy)
    process = open_supervised_process(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    try:
        with pytest.raises(OSError, match="injected registration failure"):
            _supervise_real_process(process)
        assert len(spy.registered) == 1
        assert spy.closed, "selector survived initialization failure"
        for descriptor in spy.registered:
            with pytest.raises(OSError):
                os.fstat(descriptor)
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


@_WINDOWS_ONLY
@pytest.mark.parametrize("phase", ["pending-read", "no-pending-read"])
def test_windows_cancel_tears_down_overlapped_reads(phase: str) -> None:
    """Teardown works with and without an overlapped read in flight."""
    if phase == "pending-read":
        script = "import time; time.sleep(30)"  # held before its first write
    else:
        script = "import os,time; os.close(1); os.close(2); time.sleep(30)"
    process = open_supervised_process([sys.executable, "-c", script], pipe_stdin=False)
    finals: list[str] = []
    turns = itertools.count()

    def cancel_requested() -> bool:
        if phase == "pending-read":
            return next(turns) >= 1
        return len(finals) == 2

    try:
        outcome = _supervise_real_process(
            process,
            timeout=None,
            cancel_requested=cancel_requested,
            on_stdout_chunk=lambda _c, final: finals.append("out") if final else None,
            on_stderr_chunk=lambda _c, final: finals.append("err") if final else None,
        )
        assert outcome.status == "cancelled"
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


class _ConstructionFaults:
    """Inject one Windows construction failure and record handle ownership."""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.failure = OSError("injected construction failure")
        self.made: list[int] = []
        self.closed_handles: list[int] = []
        self.handle_fds: dict[int, int] = {}
        self.closed_fds: list[int] = []
        self.processes: list[subprocess.Popen[bytes]] = []
        self._wrappers = itertools.count()
        self._real_open_osfhandle = _WINDOWS_RUNNER.msvcrt.open_osfhandle
        self._real_pipe = _WINDOWS_RUNNER.windows_utils.pipe
        self._real_wrapper = _WINDOWS_RUNNER._OverlappedPipe
        self._real_close_handle = _WINDOWS_RUNNER._winapi.CloseHandle

    def pipe(self, **kwargs: Any) -> tuple[int, int]:
        if self.phase == "pipe" and len(self.made) == 2:
            raise self.failure
        pair: tuple[int, int] = self._real_pipe(**kwargs)
        self.made.extend(pair)
        return pair

    def open_osfhandle(self, handle: int, flags: int) -> int:
        fd = int(self._real_open_osfhandle(handle, flags))
        self.handle_fds[handle] = fd
        return fd

    def popen(self, *args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        if self.phase == "process":
            raise self.failure
        process: subprocess.Popen[bytes] = subprocess.Popen(*args, **kwargs)
        self.processes.append(process)
        return process

    def wrapper(self, *args: Any, **kwargs: Any) -> Any:
        if self.phase == "attach" and next(self._wrappers) == 1:
            raise self.failure
        return self._real_wrapper(*args, **kwargs)

    def close_handle(self, handle: int) -> None:
        self.closed_handles.append(handle)
        self._real_close_handle(handle)

    def close_fd(self, fd: int) -> None:
        self.closed_fds.append(fd)
        os.close(fd)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _WINDOWS_RUNNER
        monkeypatch.setattr(
            module, "windows_utils", _Forwarding(module.windows_utils, pipe=self.pipe)
        )
        monkeypatch.setattr(
            module,
            "msvcrt",
            _Forwarding(module.msvcrt, open_osfhandle=self.open_osfhandle),
        )
        monkeypatch.setattr(
            module, "subprocess", _Forwarding(subprocess, Popen=self.popen)
        )
        monkeypatch.setattr(module, "_OverlappedPipe", self.wrapper)
        _patch_winapi(monkeypatch, CloseHandle=self.close_handle)
        _patch_os(monkeypatch, close=self.close_fd)

    def leaked(self) -> list[int]:
        return [
            handle
            for handle in self.made
            if (
                self.handle_fds[handle] not in self.closed_fds
                if handle in self.handle_fds
                else handle not in self.closed_handles
            )
        ]


@_WINDOWS_ONLY
@pytest.mark.parametrize("phase", ["pipe", "process", "attach"])
def test_windows_supervised_process_construction_rolls_back(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Every handle made before a construction failure is released."""
    faults = _ConstructionFaults(phase)
    faults.install(monkeypatch)
    try:
        with pytest.raises(OSError) as caught:
            open_supervised_process(
                [sys.executable, "-c", "import time; time.sleep(30)"]
            )
        assert caught.value is faults.failure
        assert faults.made, "the fixture never created a pipe"
        assert faults.leaked() == []
        assert all(process.poll() is not None for process in faults.processes)
    finally:
        for process in faults.processes:
            _reap_test_process(cast("subprocess.Popen[str]", process))


def test_fatal_monitor_cleanup_preserves_primary_execution_failure() -> None:
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

    process = open_supervised_process(
        [sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(30)"],
        pipe_stdin=False,
    )
    try:
        with pytest.raises(OSError) as caught:
            _supervise_real_process(
                process, monitor=Monitor(), on_stdout_chunk=fail_output
            )
        assert caught.value is primary
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


@pytest.mark.parametrize(
    "wait_for_drain", [False, True], ids=["exits-unprompted", "awaits-release"]
)
def test_ready_output_drains_without_waiting_between_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    process = open_supervised_process(
        [sys.executable, "-c", command],
        pipe_stdin=False,
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
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


def test_caller_exception_does_not_hide_fatal_monitor_cleanup() -> None:
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

    process = open_supervised_process(
        [sys.executable, "-c", "print('done')"],
        pipe_stdin=False,
    )
    try:
        try:
            raise ValueError("caller is handling an unrelated failure")
        except ValueError:
            with pytest.raises(_FatalCallbackSignal) as caught:
                _supervise_real_process(process, monitor=Monitor())
        assert caught.value is cleanup_signal
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


def test_early_root_exit_does_not_turn_unread_input_into_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child may finish successfully without accepting all supplied stdin."""
    write_errors: list[OSError] = []
    write_attempted = threading.Event()
    if sys.platform == "win32":
        real_write_file = _WINDOWS_RUNNER._winapi.WriteFile

        def observed_write_file(handle: int, data: Any, overlapped: bool) -> Any:
            try:
                return real_write_file(handle, data, overlapped)
            except OSError as exc:
                write_errors.append(exc)
                write_attempted.set()
                raise

        _patch_winapi(monkeypatch, WriteFile=observed_write_file)
    else:

        def observed_write(descriptor: int, data: bytes | memoryview) -> int:
            try:
                return os.write(descriptor, data)
            except OSError as exc:
                write_errors.append(exc)
                write_attempted.set()
                raise

        _patch_os(monkeypatch, write=observed_write)

    def observe_output(_chunk: str, _final: bool) -> None:
        assert write_attempted.is_set(), "input was not attempted before delivery"

    process = open_supervised_process([sys.executable, "-c", "print('EARLY_EXIT')"])
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
            or (
                sys.platform == "win32"
                and exc.winerror in (_winapi.ERROR_BROKEN_PIPE, _winapi.ERROR_NO_DATA)
            )
            for exc in write_errors
        ), "the real closed input pipe was not exercised"
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


_CLOSED_INPUT_CASES = [
    pytest.param("posix", errno.EPIPE, True, marks=_POSIX_ONLY, id="posix-epipe"),
    pytest.param("posix", errno.EINVAL, False, marks=_POSIX_ONLY, id="posix-einval"),
    pytest.param("posix", errno.EIO, False, marks=_POSIX_ONLY, id="posix-eio"),
    pytest.param("win32", 109, True, marks=_WINDOWS_ONLY, id="windows-broken-pipe"),
    pytest.param("win32", 232, True, marks=_WINDOWS_ONLY, id="windows-no-data"),
    pytest.param("win32", 5, False, marks=_WINDOWS_ONLY, id="windows-access-denied"),
]
"""Closed-pipe write errors per leaf; Windows values are winerror codes."""


@pytest.mark.parametrize(("leaf", "error_code", "ignore_error"), _CLOSED_INPUT_CASES)
def test_closed_input_write_errors_partition_closed_pipe_from_faults(
    monkeypatch: pytest.MonkeyPatch,
    leaf: str,
    error_code: int,
    ignore_error: bool,
) -> None:
    """Only the leaf's closed-pipe signal closes stdin; real I/O faults raise."""
    if leaf == "win32":
        failure = _winerror(error_code)
    else:
        failure = OSError(error_code, "input pipe write failed")
        if error_code == errno.EPIPE:
            failure = BrokenPipeError(error_code, "input pipe write failed")
    write_attempted = threading.Event()

    def closed_pipe_write(
        _handle: int, _data: bytes, *, overlapped: bool = False
    ) -> Any:
        if leaf == "win32":
            assert overlapped, "Windows write fault must intercept overlapped I/O"
        write_attempted.set()
        raise failure

    if leaf == "win32":
        _patch_winapi(monkeypatch, WriteFile=closed_pipe_write)
    else:
        _patch_os(monkeypatch, write=closed_pipe_write)
    process = open_supervised_process(
        [sys.executable, "-c", "import sys; sys.stdin.read(); print('done')"]
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
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


@pytest.mark.parametrize("terminal", ["ok", "error", "cancelled", "timeout", "limit"])
def test_terminal_capture_delivers_backlog_after_capture_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    process = open_supervised_process(
        [sys.executable, "-c", script],
        pipe_stdin=False,
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
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


def test_late_captured_output_callback_failure_preserves_exception_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
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

    process = open_supervised_process(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * (128 * 1024))"],
        pipe_stdin=False,
    )
    try:
        with pytest.raises(OSError) as caught:
            _supervise_real_process(process, on_stdout_chunk=fail_late)
        assert caught.value is failure
        assert calls_after_failure == 0
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


class _CaptureFailure:
    """Make the stdout read fail only once root completion is accepted."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None and process.stderr is not None
        self.failure = OSError("read failed after root completion")
        self.accepted_exit = threading.Event()
        self.stderr_read = threading.Event()
        self.stdout_descriptor = process.stdout.fileno()
        self.stderr_descriptor = process.stderr.fileno()
        self._poll = process.poll

    def poll(self) -> int | None:
        result = self._poll()
        if result is not None:
            self.accepted_exit.set()
        return result

    def sink(self, _chunk: str, _final: bool) -> None:
        if self.accepted_exit.is_set():
            raise RuntimeError("sink failure must not hide the adapter failure")

    def cancel_after_diagnostic(self) -> bool:
        if self.stderr_read.is_set():
            self.accepted_exit.set()
            return True
        return False

    def read_after_exit(self, descriptor: int, size: int) -> bytes:
        # On the single supervisor thread, defer both reads (as an empty
        # non-blocking pipe would) until the root poll accepts exit; the
        # failing stdout read is then a capture-phase read.
        if not self.accepted_exit.is_set():
            raise BlockingIOError(errno.EAGAIN, "deferred until exit")
        if descriptor == self.stdout_descriptor:
            raise self.failure
        data = os.read(descriptor, size)
        if data:
            self.stderr_read.set()
        return data

    def install_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_read_file = _WINDOWS_RUNNER._winapi.ReadFile

        def observe_result(ov: Any, wait: bool, handle: int) -> tuple[int, int]:
            # Only the capture-phase settle waits on a stdout read.
            if handle == self.stdout_descriptor and wait:
                raise self.failure
            nread, err = ov.GetOverlappedResult(wait)
            if handle == self.stderr_descriptor and err == 0 and nread:
                self.stderr_read.set()
            return nread, err

        def read_file(handle: int, size: int, overlapped: bool) -> Any:
            ov, err = real_read_file(handle, size, overlapped)
            observed = _ObservedOverlapped(
                ov, lambda real, wait: observe_result(real, wait, handle)
            )
            return observed, err

        _patch_winapi(monkeypatch, ReadFile=read_file)


def test_capture_join_reports_adapter_error_before_delivering_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adapter failing at capture completion precedes sink errors [RM-5.1]."""
    if sys.platform == "win32":
        # Cancellation drives the terminal decision so the stdout read is
        # still pending, and its capture-phase settle is the failing read.
        script = (
            "import sys,time; sys.stderr.write('queued diagnostic'); "
            "sys.stderr.flush(); time.sleep(30)"
        )
    else:
        script = "import sys; sys.stderr.write('queued diagnostic')"
    process = open_supervised_process([sys.executable, "-c", script], pipe_stdin=False)
    capture = _CaptureFailure(process)
    monkeypatch.setattr(process, "poll", capture.poll)
    cancel_requested: Callable[[], bool] | None = None
    if sys.platform == "win32":
        cancel_requested = capture.cancel_after_diagnostic
        capture.install_windows(monkeypatch)
    else:
        _patch_os(monkeypatch, read=capture.read_after_exit)
    try:
        with pytest.raises(OSError) as caught:
            _supervise_real_process(
                process,
                cancel_requested=cancel_requested,
                on_stderr_chunk=capture.sink,
            )
        assert caught.value is capture.failure
        assert capture.stderr_read.is_set(), (
            "a competing stream must have captured data"
        )
        _assert_owned_pipes_released(process)
    finally:
        capture.accepted_exit.set()
        _reap_test_process(process)


@pytest.mark.parametrize(
    ("pieces", "expected"),
    [([b"abc", b"\xe2", b"\x82\xac"], "abc€"), ([b"a\r", b"\n", b"b\r"], "a\nb\n")],
)
def test_decoder_preserves_undelivered_raw_byte_credits(
    pieces: list[bytes], expected: str
) -> None:
    """Incomplete codec input stays charged until it can be delivered."""
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    with os.fdopen(read_fd, "r", encoding="utf-8") as pipe:
        state = subprocess_runner._OutputStreamState(pipe, None)
        fed = 0
        for piece in pieces:
            state.accept(piece)
            fed += len(piece)
            assert state.in_flight_bytes == fed
        state.accept_eof()
        state.deliver(16, float("inf"))
        assert "".join(state.collected) == expected
        assert state.in_flight_bytes == 0


def test_decoder_retains_split_prefix_after_prior_output_delivery() -> None:
    """Delivering ASCII must not free an incomplete UTF8 prefix."""
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    with os.fdopen(read_fd, "r", encoding="utf-8") as pipe:
        state = subprocess_runner._OutputStreamState(pipe, None)
        state.accept(b"abc\xe2")
        state.deliver(16, float("inf"))
        assert state.in_flight_bytes == 1
        state.accept(b"\x82\xac")
        state.accept_eof()
        state.deliver(16, float("inf"))
        assert "".join(state.collected) == "abc€"
        assert state.in_flight_bytes == 0


def test_oversized_stateful_decode_fails_without_hanging_or_leaking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid large UTF7 shift cannot silently evade bounded buffering."""
    admitted: list[int] = []
    real_accept = subprocess_runner._OutputStreamState.accept

    def observed_accept(state: Any, data: bytes) -> None:
        admitted.append(len(state.decoder.getstate()[0]) + len(data))
        real_accept(state, data)

    monkeypatch.setattr(subprocess_runner._OutputStreamState, "accept", observed_accept)
    process = open_supervised_process(
        [
            sys.executable,
            "-c",
            "import os,time; os.write(1,b'+'+b'A'*262144+b'-'); time.sleep(30)",
        ],
        pipe_stdin=False,
        encoding="utf-7",
    )
    try:
        with pytest.raises(UnicodeError, match="decoder.*limit"):
            _supervise_real_process(process, timeout=2.0)
        assert admitted
        assert max(admitted) <= subprocess_runner.SUBPROCESS_STREAM_TURN_READ_BYTES
        assert process.returncode is not None
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PID exit-source ownership")
def test_pre_reaped_child_never_opens_reusable_pid_exit_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached returncode ends ownership of the numeric PID."""
    process = open_supervised_process([sys.executable, "-c", "print('reaped')"])
    try:
        process.wait(timeout=_EVENT_FAILURE_BOUND)

        def forbidden(_pid: int) -> Any:
            raise AssertionError("looked up an already-reaped PID")

        monkeypatch.setattr(subprocess_runner, "_open_child_exit_source", forbidden)
        outcome = _supervise_real_process(process, timeout=None)
        assert outcome.stdout == "reaped\n"
        _assert_owned_pipes_released(process)
    finally:
        _reap_test_process(process)


@pytest.mark.parametrize("code", [errno.ENOSYS, errno.EPERM, errno.EACCES])
def test_linux_pidfd_requirement_explains_capability_failure(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """Unsupported or denied pidfd fails clearly, retaining the syscall cause."""
    failure = OSError(code, os.strerror(code))

    def denied(_pid: int) -> int:
        raise failure

    monkeypatch.setattr(subprocess_runner, "sys", _Forwarding(sys, platform="linux"))
    _patch_os(monkeypatch, pidfd_open=denied)
    with pytest.raises(RuntimeError, match=r"Linux 5\.3.*pidfd_open") as raised:
        subprocess_runner._open_child_exit_source(os.getpid())
    assert raised.value.__cause__ is failure
    assert str(failure) in str(raised.value)


def test_linux_pidfd_requirement_explains_missing_python_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Python build without pidfd_open cannot supply the required exit source."""
    monkeypatch.setattr(subprocess_runner, "sys", _Forwarding(sys, platform="linux"))
    monkeypatch.setattr(subprocess_runner, "os", object())
    with pytest.raises(RuntimeError, match=r"Linux 5\.3.*pidfd_open") as raised:
        subprocess_runner._open_child_exit_source(1)
    assert isinstance(raised.value.__cause__, AttributeError)


@pytest.mark.parametrize(
    "payload",
    [b"+IKw-\r\n", b"+" + b"A" * 4096 + b"-"],
    ids=["small-valid-shift", "oversized-shift"],
)
def test_terminal_capture_applies_decoder_limit_and_preserves_small_shift(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    """A real terminal read cannot bypass the decoder's input resource limit."""
    barrier = _ChildBarrier()
    process = open_supervised_process(
        [
            sys.executable,
            "-c",
            _BARRIER_PRELUDE + f"barrier.readline(); os.write(1, {payload!r})",
            str(barrier.port),
        ],
        pipe_stdin=False,
        encoding="utf-7",
    )
    session: Any = None
    try:
        barrier.accept()
        session = subprocess_runner._open_process_io(
            process, stdin_data=None, on_stdout_chunk=None, on_stderr_chunk=None
        )
        barrier.release()
        process.wait(timeout=_EVENT_FAILURE_BOUND)
        # Keep the real pipe capacity and Windows priming-read size. Only the
        # decoder's terminal capture budget is reduced after child completion.
        monkeypatch.setattr(
            subprocess_runner, "SUBPROCESS_STREAM_TURN_READ_BYTES", 1024
        )
        if len(payload) > 1024:
            with pytest.raises(UnicodeError, match="decoder.*limit"):
                session.finish_after_exit()
        else:
            stdout, stderr = session.finish_after_exit()
            assert (stdout, stderr) == ("€\n", "")
        _assert_owned_pipes_released(process)
    finally:
        barrier.close()
        if session is not None:
            session.close()
        _reap_test_process(process)


def test_utf16_newline_carry_is_constant_state_and_output_is_preserved() -> None:
    """A pending CR is a codec flag, while partial UTF16 raw input stays charged."""
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    with os.fdopen(read_fd, "r", encoding="utf-16-le") as pipe:
        state = subprocess_runner._OutputStreamState(pipe, None)
        state.accept(b"A\0\r\0")
        state.deliver(16, float("inf"))
        assert state.decoder.getstate()[1] & 1
        assert state.in_flight_bytes == 0
        state.accept(b"\n\0\xa9")
        state.deliver(16, float("inf"))
        assert state.in_flight_bytes == 1
        state.accept(b"\x03")
        state.accept_eof()
        state.deliver(16, float("inf"))
        assert "".join(state.collected) == "A\nΩ"
        assert state.in_flight_bytes == 0


def test_ignored_decode_bytes_do_not_consume_retained_input_capacity() -> None:
    """The decoder limit counts buffered data, not bytes an error handler drops."""
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    with os.fdopen(read_fd, "r", encoding="utf-8", errors="ignore") as pipe:
        state = subprocess_runner._OutputStreamState(pipe, None)
        state.accept(
            b"\xff" * (2 * subprocess_runner.SUBPROCESS_STREAM_TURN_READ_BYTES)
        )
        state.accept(b"ok")
        state.accept_eof()
        state.deliver(16, float("inf"))
        assert "".join(state.collected) == "ok"
        assert state.capacity() == subprocess_runner.SUBPROCESS_STREAM_TURN_READ_BYTES


@pytest.mark.parametrize("code", [errno.ESRCH, errno.EMFILE])
def test_linux_pidfd_preserves_exit_and_noncapability_errors(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """Gone children are ready; descriptor exhaustion is not unsupported Linux."""
    failure = OSError(code, os.strerror(code))

    def failed(_pid: int) -> int:
        raise failure

    monkeypatch.setattr(subprocess_runner, "sys", _Forwarding(sys, platform="linux"))
    _patch_os(monkeypatch, pidfd_open=failed)
    if code == errno.ESRCH:
        assert subprocess_runner._open_child_exit_source(os.getpid()) is None
    else:
        with pytest.raises(OSError) as raised:
            subprocess_runner._open_child_exit_source(os.getpid())
        assert raised.value is failure


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX exit registration ordering")
def test_live_child_exit_source_is_registered_before_first_reaping_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live child cannot fall between the exit watch and its owning poll."""
    process = open_supervised_process([sys.executable, "-c", "print('live')"])
    real_source = subprocess_runner._open_child_exit_source
    real_poll = process.poll
    opened = False

    def watched(pid: int) -> Any:
        nonlocal opened
        source = real_source(pid)
        opened = True
        return source

    def polled() -> int | None:
        assert opened, "child polled before exit-source acquisition"
        return real_poll()

    monkeypatch.setattr(subprocess_runner, "_open_child_exit_source", watched)
    monkeypatch.setattr(process, "poll", polled)
    try:
        assert _supervise_real_process(process, timeout=None).stdout == "live\n"
        assert opened
    finally:
        monkeypatch.setattr(process, "poll", real_poll)
        _reap_test_process(process)
