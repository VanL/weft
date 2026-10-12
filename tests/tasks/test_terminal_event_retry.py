"""Bounded atomic completion publication at the real broker boundary.

Only broker I/O failure is injected. Consumer, serialization, lifecycle state,
retry ownership, and successful broker transactions remain real.
Spec: docs/specifications/05-Message_Flow_and_State.md [MF-3].
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

import pytest

from simplebroker.ext import BrokerError
from tests.helpers.typing import BrokerEnv
from tests.tasks.test_task_execution import make_command_taskspec
from weft._constants import TERMINAL_EVENT_WRITE_RETRIES, WEFT_GLOBAL_LOG_QUEUE
from weft.core.tasks import Consumer

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("failure", ["transient", "exhausted", "ambiguous"])
def test_completion_retry_preserves_one_serialized_fact_and_atomic_addresses(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    persistent: bool,
    failure: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        str(time.time_ns()), sys.executable, args=["-c", "pass"], persistent=persistent
    )
    task = Consumer(db_path, spec)
    task.taskspec.mark_running()
    if not persistent:
        task.taskspec.mark_completed(return_code=0)
    global_log = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    local = make_queue(spec.io.control["ctrl_out"])
    with global_log.get_connection() as broker:
        broker_type = type(broker)
        real_broadcast = broker_type.broadcast
    attempts: list[str] = []

    def broadcast(self: Any, message: str, **kwargs: Any) -> int:
        attempts.append(message)
        assert kwargs == {
            "queue_names": (WEFT_GLOBAL_LOG_QUEUE, spec.io.control["ctrl_out"]),
            "create_missing": True,
        }
        if failure == "exhausted" or len(attempts) == 1:
            if failure == "ambiguous":
                real_broadcast(self, message, **kwargs)
            raise BrokerError("injected completion publication failure")
        return real_broadcast(self, message, **kwargs)

    monkeypatch.setattr(broker_type, "broadcast", broadcast)
    try:
        event = "work_item_completed" if persistent else "work_completed"
        with caplog.at_level(logging.WARNING, logger="weft.core.tasks.base"):
            task._report_state_change(event)
        expected_attempts = (
            TERMINAL_EVENT_WRITE_RETRIES if failure == "exhausted" else 2
        )
        assert len(attempts) == expected_attempts
        assert len(set(attempts)) == 1
        global_facts = [
            raw
            for raw in global_log.peek_generator()
            if json.loads(raw).get("event") == event
        ]
        local_facts = list(local.peek_generator())
        assert local_facts == global_facts
        assert (
            len(local_facts)
            == {"exhausted": 0, "transient": 1, "ambiguous": 2}[failure]
        )
        warnings = [row for row in caplog.records if row.levelno == logging.WARNING]
        assert bool(warnings) is (failure == "exhausted")
        if warnings:
            assert warnings[0].exc_info is not None
    finally:
        task.cleanup()


def test_progress_failure_is_single_attempt_and_debug_only(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(db_path, make_command_taskspec(str(time.time_ns()), sys.executable))
    queue = task._queue(WEFT_GLOBAL_LOG_QUEUE)
    real_write = type(queue).write
    attempts = 0

    def write(self: Any, message: str) -> int:
        nonlocal attempts
        if self.name == WEFT_GLOBAL_LOG_QUEUE:
            attempts += 1
            raise BrokerError("injected progress write failure")
        return real_write(self, message)

    monkeypatch.setattr(type(queue), "write", write)
    try:
        with caplog.at_level(logging.DEBUG, logger="weft.core.tasks.base"):
            task._report_state_change("task_progress")
        assert attempts == 1
        assert not [row for row in caplog.records if row.levelno >= logging.WARNING]
        assert not make_queue(task.taskspec.io.control["ctrl_out"]).has_pending()
    finally:
        task.cleanup()
