"""Regression coverage for terminal-event write retry behavior (Task A4).

Background
----------
``BaseTask._report_state_change`` (the ``weft.log.tasks`` state-change
write) and ``BaseTask._send_terminal_envelope`` (the ``ctrl_out`` terminal
observation write) previously made a single best-effort attempt and logged
at DEBUG on failure, same as every other observability write. Because these
two writes are the only durable evidence of a task's one-shot terminal
transition ([OBS.1]), Task A4 promotes *terminal* writes only to a bounded
retry (``TERMINAL_EVENT_WRITE_RETRIES``) with a short pause between
attempts (``TERMINAL_EVENT_WRITE_RETRY_INTERVAL``) and a WARNING-level log
(with ``exc_info``) once retries are exhausted. Non-terminal writes are
unchanged: single attempt, DEBUG level.

The failure is a broker I/O error, which is external and nondeterministic
to produce for real. Per the testing plan, this is the one sanctioned
narrow stub: a proxy around the *queue write call only* that raises
``BrokerError`` a controlled number of times. Task construction, state
transitions, and the eventual successful queue write all go through real
``Consumer``/``TaskSpec``/``Queue`` objects.

Spec references:
- docs/specifications/07-System_Invariants.md [OBS.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-3]
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Callable
from typing import Any

import pytest

from simplebroker import Queue
from simplebroker.ext import BrokerError
from tests.tasks.test_task_execution import make_command_taskspec
from weft._constants import (
    TERMINAL_EVENT_WRITE_RETRIES,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.core.tasks import Consumer

pytestmark = [pytest.mark.shared]


class _FailNTimesQueueProxy:
    """Narrow stub wrapping a real ``Queue``: fails ``write`` N times.

    Only the ``write`` call is intercepted; every other attribute
    (including subsequent successful writes) delegates to the real,
    broker-backed queue object. This is the one sanctioned stub per the
    testing plan: the failure mode (broker I/O error) is external and not
    practical to trigger deterministically against a real backend.
    """

    def __init__(self, real_queue: Queue, *, fail_times: int) -> None:
        self._real_queue = real_queue
        self._fail_times = fail_times
        self.write_attempts = 0

    def write(self, message: str) -> Any:
        self.write_attempts += 1
        if self.write_attempts <= self._fail_times:
            raise BrokerError("simulated transient broker write failure")
        return self._real_queue.write(message)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real_queue, name)


@pytest.fixture
def unique_tid_terminal_retry() -> str:
    return str(time.time_ns())


def _make_completed_consumer(
    broker_env: tuple[object, Callable[[str], Queue]],
    tid: str,
) -> Consumer:
    db_path, _make_queue = broker_env
    spec = make_command_taskspec(tid, sys.executable, args=["-c", "pass"])
    task = Consumer(db_path, spec)
    task.taskspec.mark_started(pid=1234)
    task.taskspec.mark_running(pid=1234)
    task.taskspec.mark_completed(return_code=0)
    return task


def test_terminal_state_write_retries_and_warns_then_completes_shutdown(
    broker_env: tuple[object, Callable[[str], Queue]],
    unique_tid_terminal_retry: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Terminal state-change write: bounded retries, WARNING on exhaustion.

    Verifies (Spec: [OBS.1]):
    - the write is retried exactly ``TERMINAL_EVENT_WRITE_RETRIES`` times
      when every attempt fails,
    - a WARNING (with ``exc_info``) is logged once retries are exhausted,
    - the caller (``_report_state_change``) returns normally afterward --
      the process's shutdown path is not blocked by the exhausted retries.
    """
    task = _make_completed_consumer(broker_env, unique_tid_terminal_retry)
    try:
        real_log_queue = task._queue(WEFT_GLOBAL_LOG_QUEUE)
        proxy = _FailNTimesQueueProxy(
            real_log_queue, fail_times=TERMINAL_EVENT_WRITE_RETRIES + 5
        )
        task._queue_cache[WEFT_GLOBAL_LOG_QUEUE] = proxy

        with caplog.at_level(logging.WARNING, logger="weft.core.tasks.base"):
            # Should not raise -- non-fatal after retries exhaust.
            task._report_state_change("work_completed")

        assert proxy.write_attempts == TERMINAL_EVENT_WRITE_RETRIES

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "expected a WARNING-level log on retry exhaustion"
        assert any(r.exc_info is not None for r in warnings)
    finally:
        task._queue_cache.pop(WEFT_GLOBAL_LOG_QUEUE, None)
        task.stop(join=False)


def test_terminal_envelope_write_retries_and_warns_then_completes_shutdown(
    broker_env: tuple[object, Callable[[str], Queue]],
    unique_tid_terminal_retry: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Terminal ctrl_out envelope write: bounded retries, WARNING on exhaustion."""
    task = _make_completed_consumer(broker_env, unique_tid_terminal_retry)
    try:
        real_ctrl_out = task._ctrl_out_queue
        proxy = _FailNTimesQueueProxy(
            real_ctrl_out, fail_times=TERMINAL_EVENT_WRITE_RETRIES + 5
        )
        task._ctrl_out_queue = proxy  # type: ignore[assignment]

        with caplog.at_level(logging.WARNING, logger="weft.core.tasks.base"):
            task._send_terminal_envelope()

        assert proxy.write_attempts == TERMINAL_EVENT_WRITE_RETRIES

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "expected a WARNING-level log on retry exhaustion"
        assert any(r.exc_info is not None for r in warnings)
    finally:
        task.stop(join=False)


def test_terminal_state_write_transient_failure_then_success_emits_exactly_one(
    broker_env: tuple[object, Callable[[str], Queue]],
    unique_tid_terminal_retry: str,
) -> None:
    """One transient failure followed by success lands exactly one event.

    Verifies (Spec: [OBS.1]): the retry loop stops as soon as the write
    succeeds, and exactly one terminal event reaches the real,
    broker-backed ``weft.log.tasks`` queue -- no duplicate emission.
    """
    task = _make_completed_consumer(broker_env, unique_tid_terminal_retry)
    try:
        real_log_queue = task._queue(WEFT_GLOBAL_LOG_QUEUE)
        # Fail once, then delegate to the real queue -- well within budget.
        assert TERMINAL_EVENT_WRITE_RETRIES > 1
        proxy = _FailNTimesQueueProxy(real_log_queue, fail_times=1)
        task._queue_cache[WEFT_GLOBAL_LOG_QUEUE] = proxy

        task._report_state_change("work_completed")

        assert proxy.write_attempts == 2

        payloads: list[dict[str, Any]] = []
        for raw in real_log_queue.peek_many(limit=50) or []:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if payload.get("tid") == task.tid:
                payloads.append(payload)

        terminal_payloads = [p for p in payloads if p.get("event") == "work_completed"]
        assert len(terminal_payloads) == 1
    finally:
        task._queue_cache.pop(WEFT_GLOBAL_LOG_QUEUE, None)
        task.stop(join=False)


def test_non_terminal_state_write_failure_is_single_attempt_debug_only(
    broker_env: tuple[object, Callable[[str], Queue]],
    unique_tid_terminal_retry: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-terminal state-change writes keep today's single-attempt/DEBUG behavior.

    Verifies no behavior change on the non-terminal path: a task still in a
    non-terminal status (e.g. ``running``) that fails to write its
    state-change event must not retry, and must log at DEBUG (not WARNING).
    """
    db_path, _make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid_terminal_retry, sys.executable, args=["-c", "pass"]
    )
    task = Consumer(db_path, spec)
    try:
        task.taskspec.mark_running(pid=1234)
        assert task.taskspec.state.status == "running"

        real_log_queue = task._queue(WEFT_GLOBAL_LOG_QUEUE)
        proxy = _FailNTimesQueueProxy(real_log_queue, fail_times=100)
        task._queue_cache[WEFT_GLOBAL_LOG_QUEUE] = proxy

        with caplog.at_level(logging.DEBUG, logger="weft.core.tasks.base"):
            task._report_state_change("task_progress")

        assert proxy.write_attempts == 1

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not warnings
        debugs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "state change event" in r.getMessage()
        ]
        assert debugs
    finally:
        task._queue_cache.pop(WEFT_GLOBAL_LOG_QUEUE, None)
        task.stop(join=False)
