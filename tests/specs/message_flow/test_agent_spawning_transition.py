"""Spec checks for spawning transitions in agent execution (MF-5)."""

from __future__ import annotations

import json
import time

import pytest

from weft._constants import TERMINAL_TASK_STATUSES, WEFT_GLOBAL_LOG_QUEUE
from weft.core.tasks import Consumer
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


def _build_spec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="agent-spawning-check",
        spec=SpecSection(
            type="agent",
            agent={
                "runtime": "llm",
                "model": "weft-test-agent-model",
                "runtime_config": {
                    "plugin_modules": ["tests.fixtures.llm_test_models"],
                },
            },
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )


def _drain(queue) -> list[str]:
    items: list[str] = []
    while True:
        message = queue.read_one()
        if message is None:
            break
        items.append(message)
    return items


def _drive_task_until_complete(task: Consumer, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task.process_once()
        status = task.taskspec.state.status
        if status == "completed":
            return
        if status in TERMINAL_TASK_STATUSES:
            raise AssertionError(
                "Task reached terminal state before completion; "
                f"status={status!r}, "
                f"error={task.taskspec.state.error!r}, "
                f"should_stop={task.should_stop!r}, "
                f"worker_activity={task._has_worker_activity()!r}"
            )
        task.wait_for_activity(timeout=0.02)
    raise AssertionError(
        "Task did not complete before timeout; "
        f"status={task.taskspec.state.status!r}, "
        f"error={task.taskspec.state.error!r}, "
        f"should_stop={task.should_stop!r}, "
        f"worker_activity={task._has_worker_activity()!r}"
    )


def test_agent_work_spawning_logged(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    _drain(log_queue)

    task = Consumer(db_path, _build_spec(unique_tid))
    inbox = make_queue(f"T{unique_tid}.inbox")
    inbox.write("hello")

    try:
        _drive_task_until_complete(task)
    finally:
        task.stop(join=False)
        task.cleanup()

    records = [json.loads(msg) for msg in _drain(log_queue)]
    events = [record.get("event") for record in records]
    statuses = [record.get("status") for record in records]

    assert "work_spawning" in events
    assert "work_started" in events
    assert "work_completed" in events
    assert "spawning" in statuses
    assert "running" in statuses
    assert "completed" in statuses
