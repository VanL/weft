"""Spec checks for spawning transitions in agent execution (MF-5)."""

from __future__ import annotations

import json
import time

import pytest

from weft._constants import WEFT_GLOBAL_LOG_QUEUE
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


def test_agent_work_spawning_logged(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    _drain(log_queue)

    task = Consumer(db_path, _build_spec(unique_tid))
    inbox = make_queue(f"T{unique_tid}.inbox")
    inbox.write("hello")

    task._drain_queue()

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
