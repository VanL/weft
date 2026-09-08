"""Pipeline handoff acknowledgement custody (Spec: [QUEUE.6], [MF-2], [PL-4.1])."""

from __future__ import annotations

import logging
import time
from typing import Any

import pytest

from tests.tasks.test_pipeline_runtime import _entry_edge_spec
from weft.core.tasks.pipeline import PipelineEdgeTask
from weft.core.taskspec import TaskSpec
from weft.helpers import iter_queue_json_entries

pytestmark = pytest.mark.shared


@pytest.mark.parametrize("fail_ack", [False, True])
def test_override_handoff_disposes_input_once_and_preserves_backlog(
    broker_env, monkeypatch: pytest.MonkeyPatch, caplog, fail_ack: bool
) -> None:
    db_path, make_queue = broker_env
    payload = _entry_edge_spec(
        str(time.time_ns()), override_input="downstream"
    ).model_dump(mode="json")
    payload["spec"]["reserved_policy_on_error"] = "clear"
    edge = PipelineEdgeTask(db_path, TaskSpec.model_validate(payload))
    reserved = make_queue(edge._queue_names["reserved"])
    reserved.write("unrelated")
    make_queue(edge._queue_names["inbox"]).write("source")
    real_queue = edge._queue
    delete_calls = []

    class AckQueue:
        def __init__(self, queue: Any) -> None:
            self.queue = queue

        def __getattr__(self, name: str) -> Any:
            return getattr(self.queue, name)

        def delete(self, **kwargs: Any) -> bool:
            delete_calls.append(kwargs)
            if fail_ack and len(delete_calls) == 1:
                raise RuntimeError("injected handoff acknowledgement failure")
            return self.queue.delete(**kwargs)

    monkeypatch.setattr(
        edge,
        "_queue",
        lambda name: (
            AckQueue(real_queue(name))
            if name == edge._queue_names["reserved"]
            else real_queue(name)
        ),
    )
    caplog.set_level(logging.WARNING)
    try:
        edge.process_once()
        assert list(make_queue(edge._queue_names["outbox"]).peek_generator()) == [
            "downstream"
        ]
        assert len(delete_calls) == 1
        assert set(reserved.peek_generator()) == (
            {"unrelated", "source"} if fail_ack else {"unrelated"}
        )
        assert edge.taskspec.state.status == "completed"
        assert edge.should_stop
        events = [
            row
            for row, _ in iter_queue_json_entries(
                make_queue(edge._runtime.events_queue)
            )
        ]
        assert sum(row.get("type") == "edge_checkpoint" for row in events) == 1
        checkpoint = next(row for row in events if row.get("type") == "edge_checkpoint")
        assert checkpoint["checkpoint"] == "queued_for_downstream"
        assert not any(
            row.get("type") == "edge_terminal" and row.get("status") == "failed"
            for row in events
        )
        if fail_ack:
            assert any(
                record.levelno >= logging.WARNING
                and "acknowledg" in record.getMessage().lower()
                for record in caplog.records
            )
    finally:
        edge.cleanup()
