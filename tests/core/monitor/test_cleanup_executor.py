"""Tests for TaskMonitor cleanup executor scheduling."""

from __future__ import annotations

import threading
import time

import pytest

from weft.core.monitor.cleanup_executor import CleanupJob, run_cleanup_jobs

pytestmark = [pytest.mark.shared]


def test_cleanup_executor_runs_mixed_job_kinds() -> None:
    calls: list[str] = []

    jobs = (
        CleanupJob(
            kind="terminal_control",
            identity="terminal:1",
            run=lambda: calls.append("terminal_control"),
        ),
        CleanupJob(
            kind="reserved",
            identity="reserved:2",
            run=lambda: calls.append("reserved"),
        ),
        CleanupJob(
            kind="dead_tid",
            identity="dead:3",
            run=lambda: calls.append("dead_tid"),
        ),
    )

    result = run_cleanup_jobs(
        jobs,
        max_workers=3,
        deadline_reached=lambda: False,
    )

    assert set(calls) == {"terminal_control", "reserved", "dead_tid"}
    assert result.jobs_started == 3
    assert result.jobs_completed == 3
    assert result.jobs_pending == 0
    assert result.jobs_by_kind == {
        "terminal_control": 1,
        "reserved": 1,
        "dead_tid": 1,
    }


def test_cleanup_executor_never_exceeds_worker_cap() -> None:
    active = 0
    max_active = 0
    lock = threading.Lock()

    def make_job(index: int):
        def run() -> int:
            nonlocal active
            nonlocal max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return index

        return run

    jobs = tuple(
        CleanupJob(kind="dead_tid", identity=str(index), run=make_job(index))
        for index in range(8)
    )

    result = run_cleanup_jobs(
        jobs,
        max_workers=3,
        deadline_reached=lambda: False,
    )

    assert result.jobs_completed == len(jobs)
    assert max_active <= 3


def test_cleanup_executor_returns_results_in_input_order() -> None:
    release_first = threading.Event()

    def slow_first() -> str:
        assert release_first.wait(timeout=5.0)
        return "first"

    jobs = (
        CleanupJob(kind="dead_tid", identity="1", run=slow_first),
        CleanupJob(kind="dead_tid", identity="2", run=lambda: "second"),
    )

    releaser = threading.Timer(0.05, release_first.set)
    releaser.start()
    try:
        result = run_cleanup_jobs(
            jobs,
            max_workers=2,
            deadline_reached=lambda: False,
        )
    finally:
        releaser.cancel()
        release_first.set()

    assert [job_result.identity for job_result in result.results] == ["1", "2"]
    assert [job_result.value for job_result in result.results] == [
        "first",
        "second",
    ]


def test_cleanup_executor_counts_pending_when_deadline_prevents_start() -> None:
    ran = False

    def run() -> None:
        nonlocal ran
        ran = True

    result = run_cleanup_jobs(
        (CleanupJob(kind="reserved", identity="reserved:1", run=run),),
        max_workers=3,
        deadline_reached=lambda: True,
    )

    assert ran is False
    assert result.jobs_started == 0
    assert result.jobs_completed == 0
    assert result.jobs_pending == 1
    assert result.jobs_pending_by_kind == {"reserved": 1}
    assert result.results == ()


def test_cleanup_executor_wraps_job_exception_as_error() -> None:
    def fail() -> None:
        raise RuntimeError("delete failed")

    result = run_cleanup_jobs(
        (CleanupJob(kind="dead_tid", identity="dead:1", run=fail),),
        max_workers=1,
        deadline_reached=lambda: False,
    )

    assert result.jobs_started == 1
    assert result.jobs_completed == 1
    assert result.jobs_pending == 0
    assert result.results[0].success is False
    assert result.results[0].errors == ("delete failed",)
