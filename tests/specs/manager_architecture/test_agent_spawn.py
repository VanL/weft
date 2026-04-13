"""Spec checks for agent spawn-request TID correlation (MA-2)."""

from __future__ import annotations

import json

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_SPAWN_REQUESTS_QUEUE
from weft.commands import run as run_cmd
from weft.context import build_context
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.shared]


def test_agent_spawn_request_timestamp_matches_tid(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = run_cmd._generate_tid(context)

    taskspec = TaskSpec(
        tid=tid,
        name="agent-spawn-test",
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
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )

    run_cmd._enqueue_taskspec(context, taskspec, None)

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True)
    payload, timestamp = queue.read_one(with_timestamps=True)
    assert isinstance(payload, str)
    assert json.loads(payload)["taskspec"]["tid"] == tid
    assert timestamp == int(tid)
