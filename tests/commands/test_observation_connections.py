"""Observation handles own bounded broker leases (Spec: [SB-0.4])."""

from __future__ import annotations

import json
from typing import Any

import pytest

from simplebroker import Queue
from simplebroker.ext import BrokerError
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft._exceptions import CommandExecutionError
from weft.commands import _result_wait
from weft.commands import events as events_cmd
from weft.commands import result as result_cmd
from weft.context import WeftContext
from weft.core import queue_wait
from weft.core.tasks.consumer import Consumer
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared, pytest.mark.timeout(30)]


def test_event_follow_reuses_physical_connections(
    weft_harness: WeftTestHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Advance real observations causally, without a wall-clock polling budget."""
    ctx = weft_harness.context
    if ctx.backend_name != "postgres":
        pytest.skip("Counts physical PostgreSQL connections")
    psycopg = pytest.importorskip("psycopg")
    original_connect = psycopg.Connection.connect.__func__
    connections: list[Any] = []

    def connect(cls: Any, *args: Any, **kwargs: Any) -> Any:
        connection = original_connect(cls, *args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(connect))
    monkeypatch.setattr(psycopg, "connect", psycopg.Connection.connect)
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as writer:
        tid = str(writer.generate_timestamp())
        iterator = events_cmd.iter_task_events(ctx, tid, follow=True)
        try:
            for index in range(5):
                writer.write(
                    json.dumps(
                        {
                            "tid": tid,
                            "event": "work_started",
                            "status": "running",
                            "sequence": index,
                        }
                    )
                )
                assert next(iterator).payload["sequence"] == index
                if index == 0:
                    initial_connections = len(connections)
            assert len(connections) == initial_connections
        finally:
            close = getattr(iterator, "close", None)
            assert callable(close)
            close()
        # Closing the observer must not close a distinct owner's shared lease.
        writer.write("writer still owns its lease")
    assert connections
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize(
    "accepted,fail_at", [(True, 2), (True, 3), (True, None), (False, 4)]
)
def test_result_acquisition_failure_releases_previous_queues(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    accepted: bool,
    fail_at: int | None,
) -> None:
    """Partial local and unresolved-discovery queue leases close on acquisition failure."""
    ctx = weft_harness.context
    original_queue = WeftContext.queue
    queues: list[Any] = []
    closed: list[Any] = []

    def queue(self: WeftContext, *args: Any, **kwargs: Any) -> Any:
        if len(queues) + 1 == fail_at:
            raise RuntimeError("injected acquisition failure")
        result = original_queue(self, *args, **kwargs)
        queues.append(result)
        original_close = result.close

        def close() -> None:
            original_close()
            closed.append(result)

        monkeypatch.setattr(result, "close", close)
        return result

    def failed_monitor(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected acquisition failure")

    monkeypatch.setattr(WeftContext, "queue", queue)
    if fail_at is None:
        monkeypatch.setattr(_result_wait, "QueueChangeMonitor", failed_monitor)
    spec = TaskSpec.model_validate(
        {
            "tid": "1789010695101849600",
            "name": "partial-acquisition",
            "spec": {"type": "function", "function_target": "builtins:dict"},
            "io": {
                "outputs": {"outbox": "lease-test.outbox"},
                "control": {"ctrl_out": "lease-test.ctrl"},
            },
        }
    )
    assert spec.tid is not None
    with pytest.raises(
        CommandExecutionError, match="injected acquisition failure"
    ) as caught:
        result_cmd.await_task_result(
            ctx, spec.tid, _accepted_taskspec=spec if accepted else None
        )
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert queues
    assert sorted(map(id, closed)) == sorted(map(id, queues))


@pytest.mark.parametrize("persistent", [False, True])
def test_result_wait_reuses_physical_connections(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    persistent: bool,
) -> None:
    """Real unrelated wakes reuse leases before a real Consumer finishes work."""
    ctx = weft_harness.context
    if ctx.backend_name != "postgres":
        pytest.skip("Counts physical PostgreSQL connections")
    psycopg = pytest.importorskip("psycopg")
    original_connect = psycopg.Connection.connect.__func__
    connections: list[Any] = []

    def connect(cls: Any, *args: Any, **kwargs: Any) -> Any:
        connection = original_connect(cls, *args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(connect))
    monkeypatch.setattr(psycopg, "connect", psycopg.Connection.connect)
    original_wait = _result_wait.QueueChangeMonitor.wait
    wakes = 0
    initial_connections = 0
    repeated_connections = -1
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log_writer:
        tid = str(log_writer.generate_timestamp())
        spec = TaskSpec.model_validate(
            {
                "tid": tid,
                "name": "retained-result-observer",
                "spec": {
                    "type": "function",
                    "function_target": "builtins:dict",
                    "persistent": persistent,
                    "cleanup_on_exit": False,
                    "enable_process_title": False,
                    "weft_context": str(ctx.root),
                },
            }
        )
        consumer = Consumer(ctx.broker_target, spec, config=ctx.config)
        try:

            def wait(monitor: Any, timeout: float | None) -> bool:
                nonlocal wakes, initial_connections, repeated_connections
                assert timeout is None, "queued result must await actual activity"
                if wakes == 0:
                    initial_connections = len(connections)
                if wakes < 3:
                    log_writer.write(
                        json.dumps({"tid": str(int(tid) + 1), "wake": wakes})
                    )
                elif wakes == 3:
                    repeated_connections = len(connections) - initial_connections
                    assert consumer.run_work_item({"kwargs": {"ok": True}}) == {
                        "ok": True
                    }
                wakes += 1
                return original_wait(monitor, timeout)

            monkeypatch.setattr(_result_wait.QueueChangeMonitor, "wait", wait)
            observed = result_cmd.await_task_result(ctx, tid, _accepted_taskspec=spec)
            assert observed.status == "completed"
            assert observed.value == {"ok": True}
            assert observed.error is None
        finally:
            consumer.cleanup()
    assert wakes >= 4
    assert repeated_connections == 0
    assert connections, "physical connection instrumentation must not be vacuous"
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize("operation", ["result", "realtime"])
def test_result_surfaces_register_before_initial_evidence_read(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """A commit during subscription setup must be visible to the first read."""
    ctx = weft_harness.context
    tid = "1780000000000000001"
    spec = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "registration",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "io": {"outputs": {"outbox": "registered.output"}},
        }
    )
    registered = False
    evidence_reads = 0
    original_init = _result_wait.QueueChangeMonitor.__init__

    def init(monitor: Any, queues: Any, **kwargs: Any) -> None:
        nonlocal registered
        original_init(monitor, queues, **kwargs)
        registered = True
        # Publish through a separate owner after registration. No task-log
        # initialization exists, so accepted configuration must remain enough.
        with ctx.queue("registered.output", persistent=True) as output:
            output.write(json.dumps({"ready": True}))
        with ctx.queue(f"T{tid}.ctrl_out", persistent=True) as control:
            control.write(
                json.dumps(
                    {
                        "tid": tid,
                        "type": "terminal",
                        "source": "task",
                        "status": "completed",
                    }
                )
            )

    monkeypatch.setattr(_result_wait.QueueChangeMonitor, "__init__", init)
    if operation == "result":
        original_evidence = _result_wait._acquire_result_observation

        def acquire(*args: Any, **kwargs: Any) -> Any:
            nonlocal evidence_reads
            evidence_reads += 1
            assert registered, "result evidence read before activity registration"
            return original_evidence(*args, **kwargs)

        monkeypatch.setattr(_result_wait, "_acquire_result_observation", acquire)
        observed = result_cmd.await_task_result(
            ctx, tid, timeout=1, _accepted_taskspec=spec
        )
        assert observed.value == {"ready": True}
    else:
        original_snapshot = events_cmd._task_snapshot_event

        def snapshot(*args: Any, **kwargs: Any) -> Any:
            nonlocal evidence_reads
            evidence_reads += 1
            assert registered, "startup snapshot read before activity registration"
            return original_snapshot(*args, **kwargs)

        monkeypatch.setattr(events_cmd, "_task_snapshot_event", snapshot)
        observed_events = list(
            events_cmd.iter_task_realtime_events(
                ctx, tid, timeout=1, _accepted_taskspec=spec
            )
        )
        assert observed_events[-1].event_type == "end"
        assert next(
            event for event in observed_events if event.event_type == "result"
        ).payload["value"] == {"ready": True}
    assert evidence_reads > 0


def test_result_wait_preserves_primary_failure_when_queue_cleanup_fails(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared observation teardown reports secondary failure without masking wait."""
    ctx = weft_harness.context
    primary = RuntimeError("primary wait failure")
    original_close = Queue.close

    def close(queue: Queue) -> None:
        original_close(queue)
        if queue.name == "primary.output":
            raise RuntimeError("secondary queue cleanup failure")

    def wait(_monitor: Any, _timeout: float | None) -> bool:
        raise primary

    monkeypatch.setattr(Queue, "close", close)
    monkeypatch.setattr(_result_wait.QueueChangeMonitor, "wait", wait)
    spec = TaskSpec.model_validate(
        {
            "tid": "1780000000000000001",
            "name": "cleanup-failure",
            "spec": {"type": "function", "function_target": "builtins:dict"},
            "io": {
                "outputs": {"outbox": "primary.output"},
                "control": {"ctrl_out": "primary.control"},
            },
        }
    )
    assert spec.tid is not None
    with pytest.raises(CommandExecutionError, match="primary wait failure") as caught:
        result_cmd.await_task_result(ctx, spec.tid, _accepted_taskspec=spec)
    assert caught.value.__cause__ is primary
    assert any("secondary queue cleanup failure" in note for note in primary.__notes__)


@pytest.mark.parametrize("operation", ["result", "events", "realtime"])
def test_direct_observation_reports_listener_failure_as_execution_error(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Operational observer faults must not become usage errors or escape raw."""
    ctx = weft_harness.context
    tid = "1780000000000000001"
    cause = ValueError("listener protocol failure")
    owned_queues: list[Queue] = []
    closed_queue_ids: set[int] = set()
    original_queue = WeftContext.queue
    original_close = Queue.close

    def queue(owner: WeftContext, name: str, *, persistent: bool = False) -> Queue:
        opened = original_queue(owner, name, persistent=persistent)
        owned_queues.append(opened)
        return opened

    def close_queue(opened: Queue) -> None:
        original_close(opened)
        closed_queue_ids.add(id(opened))

    monkeypatch.setattr(WeftContext, "queue", queue)
    monkeypatch.setattr(Queue, "close", close_queue)

    def wait(_monitor: Any, _timeout: float | None) -> bool:
        raise BrokerError("observer failed") from cause

    monkeypatch.setattr(_result_wait.QueueChangeMonitor, "wait", wait)
    spec = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "waiting",
            "spec": {"type": "command", "process_target": "echo hello"},
        }
    )
    with pytest.raises(CommandExecutionError, match="observer failed") as caught:
        if operation == "result":
            result_cmd.await_task_result(ctx, tid, _accepted_taskspec=spec)
        elif operation == "events":
            list(events_cmd.iter_task_events(ctx, tid, follow=True))
        else:
            list(
                events_cmd.iter_task_realtime_events(ctx, tid, _accepted_taskspec=spec)
            )
    assert isinstance(caught.value.__cause__, BrokerError)
    assert caught.value.__cause__.__cause__ is cause
    assert owned_queues
    assert all(id(queue) in closed_queue_ids for queue in owned_queues)


def test_overlapping_realtime_routes_preserve_callers_log_lease(
    weft_harness: WeftTestHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing old then new fallback subscriptions cannot poison borrowed IO."""
    ctx = weft_harness.context
    monkeypatch.setattr(
        queue_wait, "create_activity_waiter_for_queues", lambda *a, **k: None
    )
    with ctx.session(), ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        old_scope, _old_out, _old_ctrl, old_monitor = events_cmd._open_realtime_routes(
            ctx, "old.result", "old.control", log
        )
        try:
            log.write("first subscription observes real activity")
            assert old_monitor.wait(2.0)
            new_scope, _new_out, _new_ctrl, new_monitor = (
                events_cmd._open_realtime_routes(ctx, "new.result", "new.control", log)
            )
            try:
                log.write("replacement subscription observes real activity")
                assert new_monitor.wait(2.0)
                old_scope.close()
            finally:
                new_scope.close()
            log.write("caller retains a usable log lease")
            assert log.read_one() == "first subscription observes real activity"
        finally:
            old_scope.close()
