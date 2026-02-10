"""Spec checks for resource-limit error categorization (RM error handling)."""

from __future__ import annotations

import pytest

from weft.core.tasks import Consumer
from weft.core.tasks.runner import RunnerOutcome
from tests.taskspec import fixtures


def test_resource_limit_marks_killed(broker_env) -> None:
    db_path, _ = broker_env
    taskspec = fixtures.create_minimal_taskspec()
    task = Consumer(db_path, taskspec)
    task.taskspec.mark_running()

    outcome = RunnerOutcome(
        status="limit",
        value=None,
        error="Memory 200MB > 100MB",
        stdout=None,
        stderr=None,
        returncode=None,
        duration=0.01,
    )

    with pytest.raises(RuntimeError):
        task._ensure_outcome_ok(outcome, timestamp=None, metrics_payload=None)
    assert task.taskspec.state.status == "killed"
