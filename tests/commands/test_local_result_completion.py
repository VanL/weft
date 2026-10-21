"""Local completion custody and ordinary-wait complexity [MF-3], [MF-5]."""

from __future__ import annotations

import json
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.weft_harness import (
    DEFAULT_TASK_COMPLETION_TIMEOUT,
    WeftTestHarness,
)
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft._exceptions import CommandExecutionError, CommandTimeoutError
from weft.commands._result_wait import drain_ctrl_out_stream_messages
from weft.commands.result import await_task_result
from weft.core.control_messages import encode_control_message
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.tasks.consumer import Consumer
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared]


def _spec(
    harness: WeftTestHarness,
    *,
    fail: bool = False,
    ctrl_out: str | None = None,
    persistent: bool = False,
) -> TaskSpec:
    with harness.context.queue("test.local-completion.ids") as allocator:
        tid = str(allocator.generate_timestamp())
    return TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "local-completion",
            "spec": {
                "type": "function",
                "function_target": (
                    "tests.tasks.sample_targets:fail_payload"
                    if fail
                    else "tests.tasks.sample_targets:echo_payload"
                ),
                "persistent": persistent,
                "cleanup_on_exit": False,
                "enable_process_title": False,
            },
            "io": {"control": {"ctrl_out": ctrl_out or f"T{tid}.ctrl_out"}},
        }
    )


@pytest.mark.parametrize("unrelated_rows", [100, 200, 300])
def test_accepted_result_has_no_global_history_interest_or_search(
    monkeypatch: pytest.MonkeyPatch,
    unrelated_rows: int,
) -> None:
    """A real failed task needs local proof; output fallback cannot mask a scan.

    Instrument both broker searches and the queues given to the actual monitor,
    so excluding watcher-thread work cannot hide a global subscription. The
    unrelated-row count stresses historical log size, not observation count.
    """
    with WeftTestHarness() as harness:
        spec = _spec(harness, fail=True)
        task = Consumer(
            harness.context.broker_target, spec, config=harness.context.config
        )
        try:
            with pytest.raises(RuntimeError, match="intentional failure"):
                task.run_work_item("fail")
            with harness.context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
                for index in range(unrelated_rows):
                    log.write(json.dumps({"tid": "unrelated", "index": index}))
            interests: list[str] = []
            searches: list[str] = []
            original_init = QueueChangeMonitor.__init__
            with (
                harness.context.queue("test.local-completion.probe") as owner,
                owner.get_connection() as broker,
            ):
                broker_type = type(broker)
            original_find = broker_type.find_message_ids

            def monitor_init(
                monitor: QueueChangeMonitor, queues: Sequence[Queue], **kwargs: Any
            ) -> None:
                interests.extend(queue.name for queue in queues)
                original_init(monitor, queues, **kwargs)

            def find(db: Any, queue: str, **kwargs: Any) -> Any:
                searches.append(queue)
                return original_find(db, queue, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(QueueChangeMonitor, "__init__", monitor_init)
                patch.setattr(broker_type, "find_message_ids", find)
                result = await_task_result(
                    harness.context, task.tid, timeout=0, _accepted_taskspec=spec
                )
                assert result.status == "failed"
                assert "intentional failure" in (result.error or "")
            assert interests
            assert WEFT_GLOBAL_LOG_QUEUE not in interests
            assert WEFT_GLOBAL_LOG_QUEUE not in searches
        finally:
            task.cleanup()


def test_control_drain_preserves_another_tasks_completion() -> None:
    """A shared control route must retain both real producers' terminal proof."""
    with WeftTestHarness() as harness:
        route = "test.shared-completion.ctrl_out"
        first = Consumer(
            harness.context.broker_target,
            _spec(harness, ctrl_out=route),
            config=harness.context.config,
        )
        second = Consumer(
            harness.context.broker_target,
            _spec(harness, fail=True, ctrl_out=route),
            config=harness.context.config,
        )
        try:
            assert first.run_work_item("first") == "first"
            with pytest.raises(RuntimeError, match="intentional failure"):
                second.run_work_item("fail")
            with harness.context.queue(route) as control:
                before = list(control.peek_generator(with_timestamps=True))
                assert len(before) >= 2
                with control.get_connection() as broker:
                    drain_ctrl_out_stream_messages(broker, route, tid=first.tid)
                assert list(control.peek_generator(with_timestamps=True)) == before
        finally:
            first.cleanup()
            second.cleanup()


def test_concurrent_accepted_waits_wake_on_local_completion_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two actual blocked readers settle on their producers' local publications.

    Count subscriptions and searches across caller and broker watcher threads.
    Unrelated global writes happen while both observers are registered.
    """
    with WeftTestHarness() as harness:
        specs = [_spec(harness, fail=True), _spec(harness, fail=True)]
        tasks = [
            Consumer(harness.context.broker_target, spec, config=harness.context.config)
            for spec in specs
        ]
        registered = Barrier(3, timeout=DEFAULT_TASK_COMPLETION_TIMEOUT)
        registered_monitors: set[int] = set()
        interests: list[str] = []
        searches: list[str] = []
        original_init = QueueChangeMonitor.__init__
        original_wait = QueueChangeMonitor.wait
        with harness.context.broker() as broker:
            broker_type = type(broker)
        original_find = broker_type.find_message_ids

        def monitor_init(
            monitor: QueueChangeMonitor, queues: Sequence[Queue], **kwargs: Any
        ) -> None:
            interests.extend(queue.name for queue in queues)
            original_init(monitor, queues, **kwargs)

        def find(db: Any, queue: str, **kwargs: Any) -> Any:
            searches.append(queue)
            return original_find(db, queue, **kwargs)

        def wait(monitor: QueueChangeMonitor, timeout: float | None) -> bool:
            if id(monitor) not in registered_monitors:
                registered_monitors.add(id(monitor))
                registered.wait()
            return original_wait(monitor, timeout)

        def observe(spec: TaskSpec) -> Any:
            assert spec.tid is not None
            return await_task_result(
                harness.context,
                spec.tid,
                timeout=DEFAULT_TASK_COMPLETION_TIMEOUT,
                _accepted_taskspec=spec,
            )

        try:
            with (
                monkeypatch.context() as patch,
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                patch.setattr(QueueChangeMonitor, "__init__", monitor_init)
                patch.setattr(QueueChangeMonitor, "wait", wait)
                patch.setattr(broker_type, "find_message_ids", find)
                futures = [pool.submit(observe, spec) for spec in specs]
                registered.wait()
                with harness.context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
                    for index in range(10):
                        log.write(json.dumps({"tid": "unrelated", "index": index}))
                for task in tasks:
                    with pytest.raises(RuntimeError, match="intentional failure"):
                        task.run_work_item("fail")
                with harness.context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
                    for index in range(10, 300):
                        log.write(json.dumps({"tid": "unrelated", "index": index}))
                assert [
                    future.result(timeout=DEFAULT_TASK_COMPLETION_TIMEOUT).status
                    for future in futures
                ] == [
                    "failed",
                    "failed",
                ]
            assert interests
            assert WEFT_GLOBAL_LOG_QUEUE not in interests
            assert WEFT_GLOBAL_LOG_QUEUE not in searches
        finally:
            for task in tasks:
                task.cleanup()


@pytest.mark.parametrize("fail", [False, True], ids=["empty-terminal", "failure"])
def test_bare_tid_reads_terminal_projection_without_local_completion(
    fail: bool,
) -> None:
    """Retained task-owned projection settles after local completion was removed.

    Failure is produced by real work; the empty persistent terminal is produced
    by a queued STOP. Removing control rows leaves the current terminal view.
    """
    with WeftTestHarness() as harness:
        spec = _spec(harness, fail=fail, persistent=not fail)
        task = Consumer(
            harness.context.broker_target, spec, config=harness.context.config
        )
        try:
            if fail:
                with pytest.raises(RuntimeError, match="intentional failure"):
                    task.run_work_item("fail")
            else:
                with harness.context.queue(spec.io.control["ctrl_in"]) as control:
                    control.write(encode_control_message("STOP"))
                with task.drive_scope():
                    task.process_once()
                assert task.taskspec.state.status == "cancelled"
            with harness.context.queue(spec.io.control["ctrl_out"]) as control:
                for _, message_id in list(control.peek_generator(with_timestamps=True)):
                    control.delete(message_id=message_id)
            result = await_task_result(harness.context, task.tid, timeout=0)
            assert result.status == ("failed" if fail else "cancelled")
            assert result.value is None
            if fail:
                assert "intentional failure" in (result.error or "")
        finally:
            task.cleanup()


def test_retried_old_persistent_boundary_does_not_become_a_new_empty_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real completion retry has one identity within the observation.

    Its old output was already consumed. Replay the exact producer body during
    the first wait: neither destination copy may create another empty batch.
    """
    with WeftTestHarness() as harness:
        spec = _spec(harness, persistent=True)
        task = Consumer(
            harness.context.broker_target, spec, config=harness.context.config
        )
        try:
            assert task.run_work_item("old") == "old"
            with harness.context.queue(spec.io.outputs["outbox"]) as outbox:
                assert outbox.read_one() is not None
            with harness.context.queue(spec.io.control["ctrl_out"]) as control:
                body = next(
                    body
                    for body in control.peek_generator()
                    if json.loads(body).get("event") == "work_item_completed"
                )
            waits: list[float | None] = []

            def wait(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
                waits.append(timeout)
                if len(waits) == 1:
                    with harness.context.broker() as broker:
                        broker.broadcast(
                            body,
                            queue_names=[
                                WEFT_GLOBAL_LOG_QUEUE,
                                spec.io.control["ctrl_out"],
                            ],
                            create_missing=True,
                        )
                    return True
                assert timeout is not None and timeout > 10, (
                    "a duplicate old fact must not start a new completion grace"
                )
                raise OSError("retry probe complete")

            with monkeypatch.context() as patch:
                patch.setattr(QueueChangeMonitor, "wait", wait)
                with pytest.raises(CommandExecutionError, match="retry probe complete"):
                    await_task_result(
                        harness.context, task.tid, timeout=20, _accepted_taskspec=spec
                    )
            assert len(waits) == 2
            with pytest.raises(CommandTimeoutError):
                await_task_result(
                    harness.context, task.tid, timeout=0, _accepted_taskspec=spec
                )
        finally:
            task.cleanup()


def test_bare_custom_route_detaches_history_and_observes_new_empty_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery changes routes once; empty batches use their local start horizon.

    A real persistent batch is already consumed when the bare-TID reader opens.
    Its local copy is newer than its global copy, but is still an old boundary.
    Another reader consumes the next output during our first registered wait.
    """
    with WeftTestHarness() as harness:
        spec = _spec(harness, persistent=True, ctrl_out="test.custom.ctrl_out")
        task = Consumer(
            harness.context.broker_target, spec, config=harness.context.config
        )
        original_init = QueueChangeMonitor.__init__
        original_wait = QueueChangeMonitor.wait
        subscriptions: list[list[str]] = []
        waits: list[float | None] = []
        try:
            assert task.run_work_item("old") == "old"
            with harness.context.queue(spec.io.outputs["outbox"]) as outbox:
                assert outbox.read_one() is not None
            with pytest.raises(CommandTimeoutError):
                await_task_result(harness.context, task.tid, timeout=0)

            def monitor_init(
                monitor: QueueChangeMonitor, queues: Sequence[Queue], **kwargs: Any
            ) -> None:
                subscriptions.append([queue.name for queue in queues])
                original_init(monitor, queues, **kwargs)

            def wait(monitor: QueueChangeMonitor, timeout: float | None) -> bool:
                waits.append(timeout)
                assert WEFT_GLOBAL_LOG_QUEUE not in subscriptions[-1]
                if len(waits) == 1:
                    assert timeout is not None and timeout > 10
                    assert task.run_work_item("new") == "new"
                    with harness.context.queue(spec.io.outputs["outbox"]) as outbox:
                        assert outbox.read_one() is not None
                    return True
                return original_wait(monitor, timeout)

            with monkeypatch.context() as patch:
                patch.setattr(QueueChangeMonitor, "__init__", monitor_init)
                patch.setattr(QueueChangeMonitor, "wait", wait)
                result = await_task_result(harness.context, task.tid, timeout=20)
            assert result.status == "completed" and result.value is None
            assert WEFT_GLOBAL_LOG_QUEUE not in subscriptions[0]
            assert WEFT_GLOBAL_LOG_QUEUE not in subscriptions[-1]
            assert spec.io.control["ctrl_out"] in subscriptions[-1]
            assert waits
        finally:
            task.cleanup()
