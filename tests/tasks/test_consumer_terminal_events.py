"""Consumer terminal outcome event-count regressions."""

from __future__ import annotations

import json

import pytest

from simplebroker import Queue
from tests.taskspec import fixtures
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.core.tasks import Consumer
from weft.core.tasks.runner import RunnerOutcome

pytestmark = [
    pytest.mark.shared,
    pytest.mark.xdist_group(name="weft_broker_serial"),
]


def _terminal_events(db_path: str, tid: str) -> list[str]:
    queue = Queue(WEFT_GLOBAL_LOG_QUEUE, db_path=db_path, persistent=False)
    try:
        events: list[str] = []
        for raw, _timestamp in queue.peek_generator(with_timestamps=True):
            payload = json.loads(raw)
            if isinstance(payload, dict) and payload.get("tid") == tid:
                event = payload.get("event")
                if isinstance(event, str):
                    events.append(event)
        return events
    finally:
        queue.close()


@pytest.mark.parametrize(
    ("status", "error", "expected_event"),
    [
        ("timeout", "too slow", "work_timeout"),
        ("limit", "memory limit", "work_limit_violation"),
        ("error", "boom", "work_failed"),
    ],
)
def test_consumer_terminal_outcome_emits_one_state_event(
    broker_env,
    status: str,
    error: str,
    expected_event: str,
) -> None:
    db_path, _ = broker_env
    taskspec = fixtures.create_minimal_taskspec()
    task = Consumer(db_path, taskspec)
    task.taskspec.mark_running()
    outcome = RunnerOutcome(
        status=status,
        value=None,
        error=error,
        stdout=None,
        stderr=None,
        returncode=None,
        duration=0.01,
    )

    with pytest.raises((RuntimeError, TimeoutError)):
        task._ensure_outcome_ok(outcome, timestamp=None, metrics_payload=None)

    events = _terminal_events(db_path, taskspec.tid)
    assert events.count(expected_event) == 1
