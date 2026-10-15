"""Public non-result observers fail as execution errors and release custody.

Spec: docs/specifications/05-Message_Flow_and_State.md [MF-5];
docs/specifications/14-Python_API_Surfaces.md [PY-2].
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Literal

import pytest

from simplebroker import Queue
from simplebroker.ext import BrokerError
from tests.helpers.task_observation import write_task_observation
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import CONTROL_KILL, CONTROL_STOP, WEFT_GLOBAL_LOG_QUEUE
from weft._exceptions import CommandExecutionError
from weft.client import WeftClient
from weft.commands import _result_wait
from weft.commands import queue as queue_cmd
from weft.commands import system as system_cmd
from weft.commands import tasks as task_cmd
from weft.commands.types import QueueEntry, TaskEvent, TaskSnapshot
from weft.context import WeftContext
from weft.core import queue_wait
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize("surface", ["queue", "status", "stop", "kill"])
@pytest.mark.parametrize("failure_at", ["construction", "wait"])
def test_client_observer_failure_is_typed_and_closes_owned_resources(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    surface: Literal["queue", "status", "stop", "kill"],
    failure_at: Literal["construction", "wait"],
) -> None:
    """Client paths bypass cmd_* decorators but still owe typed failure cleanup.

    The monitor is the external failure boundary; queue contents, client calls,
    status projection, control writes, and owner cleanup remain real. A late
    failure follows an emitted queue/status value, protecting lazy translation
    separately from constructor failure. Existing monitor tests prove failure
    delivery, not these direct Client namespace paths.
    """
    context = weft_harness.context
    with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as writer:
        tid = str(writer.generate_timestamp())
        taskspec = TaskSpec.model_validate(
            {
                "tid": tid,
                "name": "observer-error",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "persistent": True,
                },
                "io": {},
                "state": {},
            }
        )
        writer.write(
            json.dumps(
                {
                    "tid": tid,
                    "event": "work_started",
                    "status": "running",
                    "taskspec": taskspec.model_dump(mode="json"),
                }
            )
        )
    write_task_observation(
        context,
        {
            "tid": tid,
            "event": "work_started",
            "status": "running",
            "taskspec": taskspec.model_dump(mode="json"),
        },
    )
    watched_name = "observer-error.values"
    with context.queue(watched_name) as writer:
        writer.write("first value")

    fault = BrokerError(f"observer failed for {surface}")
    observed_queues: list[Queue] = []
    owned_queues: list[Queue] = []
    closed_queue_ids: set[int] = set()
    monitor_closed: set[object] = set()
    monitor_opened: list[object] = []
    real_close = Queue.close
    real_queue = WeftContext.queue

    def create_queue(
        owner_context: WeftContext, name: str, *, persistent: bool = False
    ) -> Queue:
        queue = real_queue(owner_context, name, persistent=persistent)
        owned_queues.append(queue)
        return queue

    def close_queue(queue: Queue) -> None:
        real_close(queue)
        closed_queue_ids.add(id(queue))

    class FailingMonitor:
        def __init__(
            self,
            queues: Sequence[Queue],
            *,
            config: Mapping[str, Any] | None = None,
        ) -> None:
            del config
            observed_queues.extend(queues)
            if failure_at == "construction":
                raise fault
            self.queues = tuple(queues)
            monitor_opened.append(self)

        def wait(self, timeout: float | None) -> bool:
            del timeout
            raise fault

        def close(self) -> None:
            # A rebound scope may share queue loans, but its waiter must still
            # release before any queue it observes is closed.
            assert all(id(queue) not in closed_queue_ids for queue in self.queues)
            monitor_closed.add(self)

    monkeypatch.setattr(Queue, "close", close_queue)
    monkeypatch.setattr(WeftContext, "queue", create_queue)
    owner = (
        queue_cmd
        if surface == "queue"
        else _result_wait
        if surface == "status"
        else task_cmd
    )
    monkeypatch.setattr(owner, "QueueChangeMonitor", FailingMonitor)

    with WeftClient(context=context) as client:
        if surface in {"queue", "status"}:
            stream = (
                client.queues.watch(watched_name, peek=True)
                if surface == "queue"
                else client.tasks.watch(tid)
            )
            try:
                if failure_at == "wait":
                    first = next(stream)
                    if surface == "queue":
                        assert isinstance(first, QueueEntry)
                        assert first.message == "first value"
                    else:
                        assert isinstance(first, TaskSnapshot)
                        assert first.tid == tid
                with pytest.raises(CommandExecutionError, match=str(fault)) as caught:
                    next(stream)
                assert caught.value.__cause__ is fault
                assert observed_queues
                assert all(id(queue) in closed_queue_ids for queue in owned_queues)
                assert monitor_closed == set(monitor_opened)
            finally:
                close = getattr(stream, "close", None)
                assert callable(close)
                close()
        else:
            operation = client.tasks.stop if surface == "stop" else client.tasks.kill
            with pytest.raises(CommandExecutionError, match=str(fault)) as caught:
                operation(tid)
            assert caught.value.__cause__ is fault
            assert observed_queues
            assert all(id(queue) in closed_queue_ids for queue in owned_queues)
            assert monitor_closed == set(monitor_opened)
            # The failed observation follows a committed command. It cannot
            # be reported as a rejected/missing task or erase that custody.
            with context.queue(f"T{tid}.ctrl_in") as control:
                row = control.peek_one(with_timestamps=True)
            assert row is not None
            command = json.loads(row[0])["command"]
            assert command == {"stop": CONTROL_STOP, "kill": CONTROL_KILL}[surface]


def test_public_system_stream_delivers_real_monitor_failure_and_cleanup(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native listener fault reaches the public stream after a real event.

    Keep the actual monitor, owned loop, queue reads, and command boundary.
    Only the external native waiter fails. A pass-through wait guard bounds the
    pre-fix dead-observer loop without manufacturing task or broker evidence.
    """
    context = weft_harness.context
    with context.queue(WEFT_GLOBAL_LOG_QUEUE) as writer:
        tid = str(writer.generate_timestamp())
        writer.write(
            json.dumps({"tid": tid, "event": "work_started", "status": "running"})
        )

    entered = threading.Event()
    release = threading.Event()
    fault = RuntimeError("native listener failed")
    listener_closed: list[bool] = []
    owned_queues: list[Queue] = []
    closed_queue_ids: set[int] = set()
    real_queue = WeftContext.queue
    real_close = Queue.close
    real_wait = queue_wait.QueueChangeMonitor.wait

    class FailingNativeWaiter:
        def wait(self, timeout: float | None) -> bool:
            entered.set()
            if release.wait(timeout):
                raise fault
            return False

        def close(self) -> None:
            listener_closed.append(True)
            release.set()

    waiter = FailingNativeWaiter()

    def native_waiter(
        queues: Sequence[Queue], *, stop_event: threading.Event
    ) -> FailingNativeWaiter:
        del queues, stop_event
        return waiter

    def create_queue(
        owner_context: WeftContext, name: str, *, persistent: bool = False
    ) -> Queue:
        queue = real_queue(owner_context, name, persistent=persistent)
        owned_queues.append(queue)
        return queue

    def close_queue(queue: Queue) -> None:
        real_close(queue)
        closed_queue_ids.add(id(queue))

    def wait_for_failure(
        monitor: queue_wait.QueueChangeMonitor, timeout: float | None
    ) -> bool:
        real_wait(monitor, min(timeout, 1.0) if timeout is not None else 1.0)
        raise AssertionError("listener failure returned as ordinary queue activity")

    monkeypatch.setattr(queue_wait, "create_activity_waiter_for_queues", native_waiter)
    monkeypatch.setattr(queue_wait.QueueChangeMonitor, "wait", wait_for_failure)
    monkeypatch.setattr(WeftContext, "queue", create_queue)
    monkeypatch.setattr(Queue, "close", close_queue)
    stream = system_cmd.cmd_status(watch=True, context=context.root)
    assert isinstance(stream, Iterator)
    try:
        first = next(stream)
        assert isinstance(first, TaskEvent)
        assert first.tid == tid and first.event_type == "work_started"
        assert entered.wait(2.0)
        release.set()
        with pytest.raises(CommandExecutionError) as caught:
            next(stream)
        assert isinstance(caught.value.__cause__, BrokerError)
        assert caught.value.__cause__.__cause__ is fault
        assert owned_queues
        assert all(id(queue) in closed_queue_ids for queue in owned_queues)
        assert listener_closed
    finally:
        release.set()
        close = getattr(stream, "close", None)
        if callable(close):
            close()
