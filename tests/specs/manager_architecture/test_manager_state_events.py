"""Spec checks for manager state transition logging (MF-5, MA-1)."""

from __future__ import annotations

import json
import time

import pytest

from simplebroker import Queue
from tests.helpers.typing import BrokerEnv
from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.core.manager import Manager
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


def _drain(queue: Queue) -> list[str]:
    items: list[str] = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        items.append(value)
    return items


def test_manager_emits_spawning_events(broker_env: BrokerEnv, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    _drain(log_queue)

    spec = TaskSpec(
        tid=unique_tid,
        name="manager",
        spec=SpecSection(
            type="function",
            function_target="weft.core.manager:Manager",
            timeout=None,
        ),
        io=IOSection(
            inputs={"inbox": WEFT_SPAWN_REQUESTS_QUEUE},
            outputs={"outbox": WEFT_MANAGER_OUTBOX_QUEUE},
            control={
                "ctrl_in": f"T{unique_tid}.ctrl_in",
                "ctrl_out": f"T{unique_tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )

    manager = Manager(db_path, spec)

    try:
        records = [json.loads(item) for item in _drain(log_queue)]
        events = [
            record.get("event") for record in records if record.get("tid") == unique_tid
        ]
        assert "task_spawning" in events
        assert "task_started" in events
        assert events.index("task_spawning") < events.index("task_started")
    finally:
        manager.stop(join=False)
        manager.cleanup()
