"""Spec checks for spawning transitions in task execution (MF-5)."""

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
        name="spawning-check",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
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


def test_work_spawning_logged(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    _drain(log_queue)

    spec = _build_spec(unique_tid)
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    task.stop(join=False)
    task.cleanup()

    records = [json.loads(msg) for msg in _drain(log_queue)]
    events = [record.get("event") for record in records]
    statuses = [record.get("status") for record in records]

    assert "work_spawning" in events
    assert "work_started" in events
    assert "spawning" in statuses
    assert "running" in statuses
    assert statuses.index("spawning") < statuses.index("running")
