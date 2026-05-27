"""Tests for monitored subprocess runner behavior."""

from __future__ import annotations

import itertools
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
