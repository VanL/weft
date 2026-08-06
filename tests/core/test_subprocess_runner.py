"""Tests for monitored subprocess runner behavior."""

from __future__ import annotations

import itertools
import logging
import queue
import subprocess
import sys
import time as real_time

import pytest

from weft.core.runners import subprocess_runner
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


def _runner_handle(process: subprocess.Popen[str]) -> RunnerHandle:
    return RunnerHandle(
        runner="host",
        kind="process",
        id=str(process.pid),
        control={"authority": "host-pid"},
        observations={"host_pids": [process.pid]},
    )


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
