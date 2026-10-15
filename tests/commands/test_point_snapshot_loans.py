"""Fresh task-owned snapshots and accepted immutable routes [MF-5]."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.task_observation import write_task_observation
from tests.helpers.test_backend import prepare_project_root
from weft._constants import PIPELINE_RUNTIME_METADATA_KEY
from weft.client import WeftClient
from weft.client._task import _task_with_configuration
from weft.commands import _result_wait, tasks
from weft.context import build_context
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared]
TID = "1777000000000000799"


def _accepted(*, pipeline: bool = False) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "tid": TID,
            "name": "accepted-routes",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "io": {"outputs": {"outbox": "accepted.custom.outbox"}},
            "metadata": {
                "role": "pipeline",
                PIPELINE_RUNTIME_METADATA_KEY: {
                    "queues": {"status": "accepted.pipeline.status"}
                },
            }
            if pipeline
            else {"phase": "submitted"},
        }
    )


def test_submitted_handle_snapshot_uses_accepted_routes_and_fresh_projection(
    tmp_path: Path,
) -> None:
    accepted = _accepted()
    fresh = accepted.model_dump(mode="json")
    fresh.pop("io")
    fresh["state"].update(status="running", started_at=time.time_ns())
    fresh["metadata"] = {"phase": "executed"}
    with WeftClient(path=prepare_project_root(tmp_path)) as client:
        context = client.context
        task = _task_with_configuration(client, TID, context, accepted)
        write_task_observation(
            context, {"tid": TID, "event": "task_started", "taskspec": fresh}
        )
        with context.queue("accepted.custom.outbox") as outbox:
            outbox.write(json.dumps({"answer": "briefing"}))
            snapshot = task.snapshot()
            assert outbox.peek_one() is not None
    assert snapshot is not None and snapshot.status == "completed"
    assert snapshot.started_at == fresh["state"]["started_at"]
    assert snapshot.metadata == {"phase": "executed"}


def test_snapshot_retains_fresh_failure_fields_with_accepted_configuration(
    tmp_path: Path,
) -> None:
    accepted = _accepted().model_dump(mode="json")
    fresh = dict(accepted)
    fresh["state"] = {
        "status": "failed",
        "started_at": 100,
        "completed_at": 200,
        "return_code": 9,
        "error": "fresh failure",
    }
    fresh["metadata"] = {"phase": "executed"}
    context = build_context(spec_context=prepare_project_root(tmp_path))
    with context.session():
        write_task_observation(
            context, {"tid": TID, "event": "work_failed", "taskspec": fresh}
        )
        snapshot = tasks._task_snapshot(
            TID, context=context, accepted_taskspec_payload=accepted
        )
    assert snapshot is not None and snapshot.status == "failed"
    assert (snapshot.error, snapshot.return_code) == ("fresh failure", 9)
    assert (snapshot.started_at, snapshot.completed_at) == (100, 200)
    assert snapshot.metadata == {"phase": "executed"}
    assert accepted["state"]["status"] == "created"


def test_pipeline_snapshot_uses_accepted_status_route_and_fresh_state(
    tmp_path: Path,
) -> None:
    accepted = _accepted(pipeline=True).model_dump(mode="json")
    fresh: dict[str, Any] = dict(accepted)
    fresh["state"] = {
        "status": "failed",
        "started_at": 100,
        "completed_at": 200,
        "return_code": 7,
        "error": "pipeline failed",
    }
    fresh["metadata"] = {"role": "pipeline", "phase": "executed"}
    context = build_context(spec_context=prepare_project_root(tmp_path))
    with context.session():
        write_task_observation(
            context, {"tid": TID, "event": "work_failed", "taskspec": fresh}
        )
        with context.queue("accepted.pipeline.status") as queue:
            queue.write(
                json.dumps(
                    {
                        "type": "pipeline_status",
                        "pipeline_tid": TID,
                        "status": "failed",
                        "timestamp": time.time_ns(),
                    }
                )
            )
        snapshot = tasks._task_snapshot(
            TID, context=context, accepted_taskspec_payload=accepted
        )
    assert snapshot is not None and snapshot.pipeline_status is not None
    assert snapshot.error == "pipeline failed" and snapshot.return_code == 7
    assert (snapshot.started_at, snapshot.completed_at) == (100, 200)
    assert snapshot.metadata == {"role": "pipeline", "phase": "executed"}


@pytest.mark.parametrize("surface", ["status", "terminal"])
def test_point_query_uses_no_activity_waiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    """A one-read query needs durable evidence, without native waiter resources."""
    context = build_context(spec_context=prepare_project_root(tmp_path))
    accepted = _accepted().model_dump(mode="json")
    accepted["state"]["status"] = "completed"
    write_task_observation(
        context, {"tid": TID, "event": "work_completed", "taskspec": accepted}
    )

    def reject_waiter(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("point query allocated an activity waiter")

    monkeypatch.setattr(tasks.QueueChangeMonitor, "__init__", reject_waiter)
    snapshot = (
        tasks.task_snapshot(TID, context=context)
        if surface == "status"
        else tasks.task_terminal_snapshot(TID, context=context)
    )
    assert snapshot is not None and snapshot.status == "completed"


def test_status_watch_binds_custom_routes_published_after_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A watch started before initialization must wake on later custom completion."""
    context = build_context(spec_context=prepare_project_root(tmp_path))
    accepted = _accepted().model_dump(mode="json")
    accepted["io"]["control"]["ctrl_out"] = "late.custom.completion"
    accepted["state"]["status"] = "running"
    waits: list[set[str]] = []

    class PublishingMonitor:
        def __init__(self, queues: Any, *, config: Any = None) -> None:
            self.names = {queue.name for queue in queues}

        def wait(self, timeout: float | None) -> bool:
            waits.append(self.names)
            if len(waits) == 1:
                write_task_observation(
                    context, {"tid": TID, "event": "work_started", "taskspec": accepted}
                )
            else:
                assert "late.custom.completion" in self.names, (
                    "late routes were never registered"
                )
                with context.queue("late.custom.completion") as queue:
                    queue.write(
                        json.dumps(
                            {
                                "type": "terminal",
                                "source": "task",
                                "tid": TID,
                                "status": "cancelled",
                            }
                        )
                    )
            return True

        def close(self) -> None:
            pass

    monkeypatch.setattr(_result_wait, "QueueChangeMonitor", PublishingMonitor)
    stream = tasks.watch_task_status(TID, context=context, timeout=10)
    try:
        assert [snapshot.status for snapshot in stream] == [
            "unknown",
            "running",
            "cancelled",
        ]
    finally:
        close = getattr(stream, "close", None)
        assert callable(close)
        close()
