"""Terminal observers use accepted routes and task-owned retained proof [MF-5]."""

from __future__ import annotations

import json

import pytest

from tests.helpers.reactor_driver import drive_until
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.client import WeftClient
from weft.client._task import _task_with_configuration
from weft.commands import result, tasks
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.tasks import Consumer
from weft.core.taskspec import TaskSpec
from weft.core.taskspec.transport import validate_taskspec_payload

pytestmark = [pytest.mark.shared]


def _spec(tid: str, target: str = "echo_payload") -> TaskSpec:
    return validate_taskspec_payload(
        {
            "name": "terminal-observation",
            "spec": {
                "type": "function",
                "function_target": f"tests.tasks.sample_targets:{target}",
            },
        },
        resolved_tid=tid,
    )


def test_submitted_terminal_snapshot_uses_custom_route_before_initialization() -> None:
    with WeftTestHarness() as harness, WeftClient(harness.context) as client:
        with harness.context.queue("test.terminal.tid") as queue:
            tid = str(queue.generate_timestamp())
        payload = _spec(tid).model_dump(mode="json")
        payload["io"]["outputs"]["outbox"] = "terminal.accepted.outbox"
        accepted = validate_taskspec_payload(payload, resolved_tid=tid)
        task = _task_with_configuration(client, tid, harness.context, accepted)
        with harness.context.queue("terminal.accepted.outbox") as queue:
            queue.write(json.dumps({"answer": "ready"}))
            snapshot = task.terminal_snapshot()
            assert queue.peek_one() == json.dumps({"answer": "ready"})
        assert snapshot.status == "completed" and snapshot.source == "outbox"


def test_terminal_snapshot_waits_on_initial_task_interests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completion arriving after the initial empty read wakes the observer."""
    with WeftTestHarness() as harness:
        context = harness.context
        with context.queue("test.terminal.tid") as allocator:
            tid = str(allocator.generate_timestamp())
        terminal = json.dumps(
            {"type": "terminal", "source": "task", "tid": tid, "status": "cancelled"}
        )
        deadlines: list[float | None] = []
        with context.queue(f"T{tid}.ctrl_out") as control:

            def wake(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
                deadlines.append(timeout)
                control.write(terminal)
                return True

            monkeypatch.setattr(QueueChangeMonitor, "wait", wake)
            snapshot = tasks.task_terminal_snapshot(tid, timeout=10, context=context)
            assert snapshot.status == "cancelled" and snapshot.source == "ctrl_out"
            assert control.peek_one() == terminal
        assert deadlines and deadlines[0] is not None and 0 < deadlines[0] <= 10


def test_real_consumer_terminal_proof_survives_raw_history_retirement() -> None:
    with WeftTestHarness() as harness:
        context = harness.context
        with context.queue("test.terminal.tid") as queue:
            tid = str(queue.generate_timestamp())
        spec = _spec(tid, "fail_payload")
        consumer = Consumer(context.broker_target, spec, config=context.broker_config)
        try:
            with context.queue(spec.io.inputs["inbox"]) as inbox:
                inbox.write(json.dumps({"args": ["payload"]}))
            drive_until(
                lambda: consumer.taskspec.state.status,
                lambda status: status == "failed",
                step=consumer.process_once,
                wait=consumer.wait_for_activity,
                timeout=10,
            )
        finally:
            consumer.cleanup()
        with context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
            rows = list(log.peek_generator(with_timestamps=True))
            for _raw, message_id in rows:
                assert log.delete(message_id=message_id)
        snapshot = tasks.task_snapshot(tid, context=context)
        assert snapshot is not None and snapshot.status == "failed"
        outcome = result.await_task_result(context, tid, timeout=0)
        assert outcome.status == "failed"
        assert outcome.error and "intentional failure" in outcome.error
