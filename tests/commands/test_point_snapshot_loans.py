"""Fresh point-TID snapshots reuse acquired history and accepted routes [MF-5]."""

from __future__ import annotations

import json
import time
from typing import Any, Never

import pytest

from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import PIPELINE_RUNTIME_METADATA_KEY, WEFT_GLOBAL_LOG_QUEUE
from weft.client import WeftClient
from weft.client._task import _task_with_configuration
from weft.commands import system, tasks
from weft.core.monitor.collation import update_from_task_log_payload
from weft.core.monitor.store import open_monitor_store
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared]

TID = "1777000000000000799"


def test_submitted_handle_snapshot_lends_routes_and_keeps_fresh_state() -> None:
    """Redacted history retains fresh state; the handle already owns its routes."""
    accepted = TaskSpec.model_validate(
        {
            "tid": TID,
            "name": "known-routes",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "io": {"outputs": {"outbox": "handle.custom.outbox"}},
            "metadata": {"phase": "submitted"},
        }
    )
    fresh = accepted.model_dump(mode="json")
    del fresh["io"]  # TaskSpec log redaction may hide immutable routing.
    fresh["state"].update(status="running", started_at=time.time_ns())
    fresh["metadata"] = {"phase": "executed"}
    with WeftTestHarness() as harness, WeftClient(path=harness.root) as client:
        task = _task_with_configuration(client, TID, harness.context, accepted)
        with harness.context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
            log.write(json.dumps({"tid": TID, "status": "running", "taskspec": fresh}))
        with harness.context.queue("handle.custom.outbox") as outbox:
            outbox.write(json.dumps({"answer": "briefing"}))
            snapshot = task.snapshot()
            assert outbox.peek_one() is not None
    assert snapshot is not None and snapshot.status == "completed"
    assert snapshot.started_at == fresh["state"]["started_at"]
    assert snapshot.metadata == {"phase": "executed"}


def _reject_history_reload(*args: Any, **kwargs: Any) -> Never:
    del args, kwargs
    raise AssertionError("supplied history must not be rediscovered")


def _payload(status: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "tid": TID,
        "name": "loaned-snapshot",
        "spec": {"type": "function", "runner": {"name": "host"}},
        "state": {"status": status},
        "metadata": metadata or {},
    }


def _prevent_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    original = tasks.load_latest_taskspec_payload

    def borrowed_payload(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        assert kwargs.get("task_events") is not None, "history must be borrowed"
        return original(*args, **kwargs)

    monkeypatch.setattr(tasks, "load_latest_taskspec_payload", borrowed_payload)
    monkeypatch.setattr(
        tasks.task_evidence, "read_task_history", _reject_history_reload
    )
    monkeypatch.setattr(system, "_iter_log_events", _reject_history_reload)
    monkeypatch.setattr(
        tasks, "read_monitor_task_record", _reject_history_reload, raising=False
    )


def test_snapshot_empty_history_loan_and_negative_monitor_loan_do_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prevent_reload(monkeypatch)
    with WeftTestHarness() as harness:
        snapshot = tasks._task_snapshot(
            TID,
            context=harness.context,
            accepted_taskspec_payload=_payload("created"),
            task_events=[],
            monitor_lookup=(None, None),
        )
    assert snapshot is None


def test_snapshot_retains_fresh_failure_fields_with_accepted_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prevent_reload(monkeypatch)
    accepted = _payload("created", metadata={"phase": "submitted"})
    fresh = _payload("failed", metadata={"phase": "executed"})
    fresh["state"].update(
        started_at=100, completed_at=200, return_code=9, error="fresh failure"
    )
    with WeftTestHarness() as harness:
        snapshot = tasks._task_snapshot(
            TID,
            context=harness.context,
            accepted_taskspec_payload=accepted,
            task_events=[
                ({"tid": TID, "event": "task_failed", "taskspec": fresh}, 300)
            ],
            monitor_lookup=(None, None),
        )
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.error == "fresh failure"
    assert snapshot.return_code == 9
    assert (snapshot.started_at, snapshot.completed_at, snapshot.last_timestamp) == (
        100,
        200,
        300,
    )
    assert snapshot.metadata == {"phase": "executed"}
    assert accepted["state"] == {"status": "created"}


def test_snapshot_local_probe_uses_accepted_custom_outbox_without_consuming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prevent_reload(monkeypatch)
    accepted = _payload("created")
    accepted["io"] = {"outputs": {"outbox": "accepted.custom.outbox"}}
    fresh = _payload("running", metadata={"phase": "executed"})
    fresh["state"]["started_at"] = time.time_ns()
    with (
        WeftTestHarness() as harness,
        harness.context.queue("accepted.custom.outbox", persistent=False) as queue,
    ):
        queue.write(json.dumps({"answer": "retained route"}))
        snapshot = tasks._task_snapshot(
            TID,
            context=harness.context,
            accepted_taskspec_payload=accepted,
            task_events=[
                (
                    {"tid": TID, "event": "task_started", "taskspec": fresh},
                    time.time_ns(),
                )
            ],
            monitor_lookup=(None, None),
        )
        assert queue.peek_one() == json.dumps({"answer": "retained route"})
    assert snapshot is not None
    assert snapshot.status == "completed"
    assert snapshot.reconciliation is not None
    assert snapshot.reconciliation["classification"] == "result_without_terminal"
    assert snapshot.metadata == {"phase": "executed"}


def test_pipeline_snapshot_uses_accepted_status_route_and_fresh_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prevent_reload(monkeypatch)
    accepted = _payload("created", metadata={"role": "pipeline"})
    accepted["metadata"][PIPELINE_RUNTIME_METADATA_KEY] = {
        "queues": {"status": "accepted.pipeline.status"}
    }
    fresh = _payload("failed", metadata={"role": "pipeline", "phase": "executed"})
    fresh["state"].update(
        started_at=100, completed_at=200, return_code=7, error="pipeline failed"
    )
    with WeftTestHarness() as harness:
        with harness.context.queue(
            "accepted.pipeline.status", persistent=False
        ) as queue:
            queue.write(
                json.dumps(
                    {
                        "type": "pipeline_status",
                        "pipeline_tid": TID,
                        "status": "failed",
                        "timestamp": 400,
                    }
                )
            )
        snapshot = tasks._task_snapshot(
            TID,
            context=harness.context,
            accepted_taskspec_payload=accepted,
            task_events=[
                ({"tid": TID, "event": "task_failed", "taskspec": fresh}, 300)
            ],
            monitor_lookup=(None, None),
        )
    assert snapshot is not None
    assert snapshot.event == "pipeline_status"
    assert snapshot.error == "pipeline failed"
    assert snapshot.return_code == 7
    assert (snapshot.started_at, snapshot.completed_at) == (100, 200)
    assert snapshot.metadata == {"role": "pipeline", "phase": "executed"}


@pytest.mark.parametrize("event", ["task_activity", "work_failed"])
def test_monitor_snapshot_requires_qualified_terminal_collation(event: str) -> None:
    """An activity's diagnostic failed status must not become terminal proof."""
    terminal = event == "work_failed"
    initial = _payload("running")
    failed = _payload("failed")
    failed["state"].update(error="published failure", completed_at=200)
    publication: dict[str, Any] = {"tid": TID, "event": event, "status": "failed"}
    if terminal:
        publication["taskspec"] = failed
    with (
        WeftTestHarness() as harness,
        harness.context.session(),
        harness.context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as queue,
    ):
        store = open_monitor_store(harness.context, queue=queue)
        store.ensure_schema()
        for payload in (
            {
                "tid": TID,
                "event": "task_initialized",
                "status": "running",
                "taskspec": initial,
            },
            publication,
        ):
            message_id = queue.write(json.dumps(payload))
            published = queue.peek_one(exact_timestamp=message_id)
            assert published is not None
            update = update_from_task_log_payload(
                json.loads(published), message_id=message_id
            )
            assert update is not None
            store.upsert_task_event(update)
        record = store.get_task(TID)
        assert record is not None
        assert record.terminal_seen is terminal
        assert record.terminal_status == ("failed" if terminal else None)
        snapshot = tasks._task_snapshot(
            TID,
            context=harness.context,
            task_events=[],
            monitor_lookup=(record, None),
        )
    assert snapshot is not None
    assert snapshot.status == ("failed" if terminal else "unknown")
    if terminal:
        assert snapshot.reconciliation == {
            "classification": "terminal_monitor_store",
            "reason": "raw_task_log_retired",
        }
    else:
        assert snapshot.reconciliation is None
