"""Observation handles own bounded broker leases (Spec: [SB-0.4])."""

from __future__ import annotations

import json
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands import _result_wait
from weft.commands import events as events_cmd
from weft.commands import result as result_cmd
from weft.commands import run as run_cmd
from weft.commands.interactive import InteractiveStreamClient
from weft.context import WeftContext
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
            iterator.close()
        # Closing the observer must not close a distinct owner's shared lease.
        writer.write("writer still owns its lease")
    assert connections
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize("fail_at", [2, 3, 4])
def test_result_acquisition_failure_releases_previous_queues(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    fail_at: int,
) -> None:
    """Real queue leases close even before the wait loop is constructed."""
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
    if fail_at == 4:
        monkeypatch.setattr(_result_wait, "QueueChangeMonitor", failed_monitor)
    with pytest.raises(RuntimeError, match="injected acquisition failure"):
        _result_wait.await_one_shot_result(
            ctx,
            "1789010695101849600",
            outbox_name="lease-test.outbox",
            ctrl_out_name="lease-test.ctrl",
            timeout=None,
            show_stderr=False,
        )
    assert queues
    assert sorted(map(id, closed)) == sorted(map(id, queues))


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_interactive_log_acquisition_failure_closes_client(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_fails: bool,
) -> None:
    ctx = weft_harness.context
    with ctx.queue("timestamps", persistent=True) as timestamps:
        tid = str(timestamps.generate_timestamp())
    taskspec = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "lease-test",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "io": {},
            "state": {},
        }
    )
    original_close = Queue.close
    closed: list[str] = []

    def close(queue: Queue) -> None:
        original_close(queue)
        closed.append(queue.name)

    def failed_queue(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected log acquisition failure")

    original_stop = InteractiveStreamClient.stop

    def stop(client: InteractiveStreamClient) -> None:
        original_stop(client)
        if cleanup_fails:
            raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(Queue, "close", close)
    monkeypatch.setattr(WeftContext, "queue", failed_queue)
    monkeypatch.setattr(InteractiveStreamClient, "stop", stop)
    with pytest.raises(
        RuntimeError, match="injected log acquisition failure"
    ) as caught:
        run_cmd._InteractiveRunLifecycle(ctx, taskspec, use_prompt=False)
    assert f"T{tid}.inbox" in closed
    if cleanup_fails:
        assert any(
            "injected cleanup failure" in note for note in caught.value.__notes__
        )


@pytest.mark.parametrize("persistent", [False, True])
def test_result_wait_reuses_physical_connections(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    persistent: bool,
) -> None:
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
    module = result_cmd if persistent else _result_wait
    original_poll = module.poll_log_events
    scans = 0
    initial_connections = 0
    repeated_connections = -1
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log_writer:
        tid = str(log_writer.generate_timestamp())
        outbox_name = f"T{tid}.outbox"
        with ctx.queue(outbox_name, persistent=True) as writer:

            def poll(*args: Any, **kwargs: Any) -> Any:
                nonlocal scans, initial_connections, repeated_connections
                scans += 1
                if scans == 4:
                    repeated_connections = len(connections) - initial_connections
                    writer.write(json.dumps({"ok": True}))
                    log_writer.write(
                        json.dumps(
                            {
                                "tid": tid,
                                "event": "work_completed",
                                "status": "completed",
                            }
                        )
                    )
                observed = original_poll(*args, **kwargs)
                if scans == 1:
                    initial_connections = len(connections)
                return observed

            monkeypatch.setattr(module, "poll_log_events", poll)
            if persistent:
                status, payload, error = result_cmd._await_single_result(
                    ctx,
                    tid,
                    timeout=None,
                    show_stderr=False,
                    taskspec_payload={"spec": {"persistent": True}},
                    outbox_name=outbox_name,
                    ctrl_out_name=f"T{tid}.ctrl_out",
                )
            else:
                status, payload, error = _result_wait.await_one_shot_result(
                    ctx,
                    tid,
                    timeout=None,
                    show_stderr=False,
                    outbox_name=outbox_name,
                    ctrl_out_name=f"T{tid}.ctrl_out",
                )
            assert status == "completed"
            assert payload == {"ok": True}
            assert error is None
    assert repeated_connections == 0
    assert connections
    assert all(connection.closed for connection in connections)
