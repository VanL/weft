"""Tests for shared long-lived service task helpers.

Spec references:
- docs/specifications/07-System_Invariants.md [IMPL.10]
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.core.tasks.base import TaskWorkerResult
from weft.core.tasks.multiqueue_watcher import QueueMessageContext
from weft.core.tasks.service import (
    ServiceTask,
    ServiceWorkerContext,
    ServiceWorkerEvent,
    ServiceWorkerSpec,
)
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.shared]


@pytest.fixture
def unique_tid() -> str:
    """Return a unique task id for service-task tests."""

    return str(time.time_ns())


class ServiceTestTask(ServiceTask):
    """Small concrete service for shared helper tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.worker_results: list[tuple[Any | None, TaskWorkerResult, int]] = []
        self.service_worker_events: list[tuple[ServiceWorkerEvent, int]] = []
        super().__init__(*args, **kwargs)

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        return {
            self._queue_names["inbox"]: self._read_queue_config(
                self._handle_work_message
            ),
        }

    def _handle_work_message(
        self,
        message: str,
        timestamp: int,
        context: QueueMessageContext,
    ) -> None:
        del message, timestamp, context

    def _handle_worker_result(self, result: TaskWorkerResult) -> None:
        if isinstance(result.value, ServiceWorkerEvent):
            super()._handle_worker_result(result)
            return
        work = self._pop_service_lane_work(result.lane)
        self.worker_results.append((work, result, threading.get_ident()))

    def _handle_service_worker_event(self, event: ServiceWorkerEvent) -> None:
        self.service_worker_events.append((event, threading.get_ident()))
        if event.kind not in {"result", "error"}:
            return
        work = self._pop_service_lane_work(event.name)
        if work is None:
            return
        self.worker_results.append(
            (
                work,
                TaskWorkerResult(
                    lane=event.name,
                    value=event.value,
                    error=event.error,
                ),
                threading.get_ident(),
            )
        )


def make_service_taskspec(
    tid: str,
    *,
    reporting_interval: str = "transition",
) -> TaskSpec:
    """Create a minimal service TaskSpec with explicit queue mappings."""

    return TaskSpec(
        tid=tid,
        name="service-test",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
            reporting_interval=reporting_interval,
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"},
            control={
                "ctrl_in": f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )


def drain_log_events(log_queue) -> list[dict[str, Any]]:
    """Read and decode all currently visible global task-log events."""

    records: list[dict[str, Any]] = []
    while True:
        raw = log_queue.read_one()
        if raw is None:
            return records
        records.append(json.loads(raw))


def drain_worker_results_until(
    task: ServiceTestTask,
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
) -> None:
    """Drive owner turns until a predicate is true or the deadline expires.

    The first ``process_once()`` claims reactor drive ownership for this
    thread, so the later public ``wait_for_activity()`` calls are owner-legal
    regardless of how quickly worker results arrive.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task.process_once()
        if predicate():
            return
        task.wait_for_activity(timeout=0.01)
    task.process_once()
    if not predicate():
        raise AssertionError("worker result predicate was not satisfied")


def test_service_task_activation_publishes_running_lifecycle_once(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)

    try:
        assert task._activate_service_task() is True
        assert task._activate_service_task() is False
    finally:
        task.stop()

    events = [
        json.loads(message)
        for message, _timestamp in log_queue.peek_generator(with_timestamps=True)
        if json.loads(message).get("tid") == unique_tid
    ]
    event_names = [event["event"] for event in events]
    assert event_names.count("task_spawning") == 1
    assert event_names.count("task_started") == 1
    assert task.taskspec.state.status == "running"


def test_service_task_activity_is_live_only_not_task_log_event(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_log_events(log_queue)

    try:
        task._set_activity("working")
        task._set_activity("waiting", waiting_on=task._queue_names["inbox"])
    finally:
        task.stop()

    records = drain_log_events(log_queue)
    assert [record["event"] for record in records] == []
    snapshot = task._control_snapshot_fields()
    assert snapshot["activity"] == "waiting"
    assert snapshot["waiting_on"] == task._queue_names["inbox"]


def test_service_task_poll_reporting_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    task = ServiceTestTask(
        db_path,
        make_service_taskspec(unique_tid, reporting_interval="poll"),
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_log_events(log_queue)

    try:
        task._last_poll_report_at = 0.0
        monkeypatch.setattr("weft.core.tasks.base.time.monotonic", lambda: 60.0)
        task.process_once()
    finally:
        task.stop()

    assert drain_log_events(log_queue) == []


def test_service_task_cleanup_passes_one_absolute_deadline_to_worker_groups(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))
    task._register_service_worker(
        ServiceWorkerSpec(name="deadline", target=lambda _context: None)
    )
    observed_deadlines: list[float] = []

    def record_stop(
        _name: str,
        *,
        drain: bool = False,
        deadline: float | None = None,
    ) -> None:
        del drain
        assert deadline is not None
        observed_deadlines.append(deadline)

    monkeypatch.setattr(task, "_stop_service_worker", record_stop)
    started_at = time.monotonic()
    task.stop(timeout=0.2)

    assert len(observed_deadlines) == 1
    assert started_at < observed_deadlines[0] <= started_at + 0.25


def test_service_task_full_sentinel_queue_respects_cleanup_deadline(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))
    worker_entered = threading.Event()
    release_worker = threading.Event()

    def blocked_target(_context: ServiceWorkerContext) -> None:
        worker_entered.set()
        assert release_worker.wait(timeout=3.0)

    task._register_service_worker(
        ServiceWorkerSpec(
            name="blocked",
            target=blocked_target,
            input_queue_maxsize=1,
        )
    )
    task._start_service_worker("blocked")
    assert worker_entered.wait(timeout=2.0)
    registration = task._service_worker_registrations["blocked"]
    registration.input_queue.put_nowait("occupy-sentinel-slot")

    started_at = time.monotonic()
    task.stop(timeout=0.05)
    elapsed = time.monotonic() - started_at
    release_worker.set()
    for thread in registration.threads:
        thread.join(timeout=2.0)

    assert elapsed < 0.3
    assert task._task_lifecycle.value == "closed"


def test_service_task_due_time_helpers_bound_waits(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))

    try:
        assert task._timeout_until_ns(900, now_ns=1_000) == 0.0
        assert task._timeout_until_ns(1_500_000_000, now_ns=1_000_000_000) == 0.5
        assert task._interval_timeout(0, 1.0, now_ns=1_000_000_000) == 0.0
        assert (
            abs(
                task._interval_timeout(
                    1_000_000_000,
                    0.25,
                    now_ns=1_100_000_000,
                )
                - 0.15
            )
            < 0.000001
        )
    finally:
        task.stop()


def test_service_worker_group_runs_registered_target_on_reactor_result_drain(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))

    def target(context: ServiceWorkerContext) -> dict[str, Any]:
        for item in context.iter_items():
            return {"item": item, "worker_index": context.worker_index}
        raise AssertionError("target did not receive an input item")

    task._register_service_worker(ServiceWorkerSpec(name="api.one", target=target))

    try:
        handle = task._start_service_worker(
            "api.one",
            request_id="request-one",
            initial_items=({"payload": "hello"},),
        )
        assert handle.name == "api.one"
        assert task.service_worker_events == []

        drain_worker_results_until(
            task,
            lambda: any(
                event.name == "api.one" and event.kind == "result"
                for event, _thread_id in task.service_worker_events
            ),
        )
    finally:
        task.stop()

    result_events = [
        event
        for event, thread_id in task.service_worker_events
        if event.name == "api.one"
        and event.kind == "result"
        and thread_id == threading.get_ident()
    ]
    assert len(result_events) == 1
    assert result_events[0].request_id == "request-one"
    assert result_events[0].value == {"item": {"payload": "hello"}, "worker_index": 0}


def test_service_worker_groups_run_independently(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))

    def target(context: ServiceWorkerContext, label: str) -> tuple[str, Any]:
        for item in context.iter_items():
            return (label, item)
        raise AssertionError("target did not receive an input item")

    task._register_service_worker(
        ServiceWorkerSpec(name="api.alpha", target=target, args=("alpha",))
    )
    task._register_service_worker(
        ServiceWorkerSpec(name="api.beta", target=target, args=("beta",))
    )

    try:
        task._start_service_worker("api.alpha", initial_items=("a",))
        task._start_service_worker("api.beta", initial_items=("b",))
        drain_worker_results_until(
            task,
            lambda: (
                {
                    event.value
                    for event, _thread_id in task.service_worker_events
                    if event.kind == "result"
                }
                == {("alpha", "a"), ("beta", "b")}
            ),
        )
    finally:
        task.stop()


def test_service_worker_group_can_run_multiple_workers_on_one_input_queue(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))

    def target(context: ServiceWorkerContext) -> tuple[int, Any]:
        for item in context.iter_items():
            return (context.worker_index, item)
        raise AssertionError("target did not receive an input item")

    task._register_service_worker(
        ServiceWorkerSpec(
            name="api.pool",
            target=target,
            worker_count=2,
        )
    )

    try:
        task._start_service_worker("api.pool", initial_items=("a", "b"))
        drain_worker_results_until(
            task,
            lambda: (
                len(
                    [
                        event
                        for event, _thread_id in task.service_worker_events
                        if event.name == "api.pool" and event.kind == "result"
                    ]
                )
                == 2
            ),
        )
    finally:
        task.stop()

    result_events = [
        event
        for event, _thread_id in task.service_worker_events
        if event.name == "api.pool" and event.kind == "result"
    ]
    assert {event.worker_index for event in result_events} == {0, 1}
    assert {event.value[1] for event in result_events} == {"a", "b"}


def test_service_worker_group_can_restart_after_terminal_result_before_thread_exit(
    broker_env,
    unique_tid: str,
) -> None:
    """Terminal result delivery, not thread teardown timing, releases a lane."""

    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))
    result_published = threading.Event()
    release_result_publish = threading.Event()
    original_publish = task._publish_service_worker_event

    def target(context: ServiceWorkerContext) -> tuple[str, Any]:
        for item in context.iter_items():
            return (context.request_id, item)
        raise AssertionError("target did not receive an input item")

    def blocking_publish(event: ServiceWorkerEvent) -> bool:
        published = original_publish(event)
        if event.request_id == "first" and event.kind == "result":
            result_published.set()
            release_result_publish.wait(timeout=2.0)
        return published

    task._publish_service_worker_event = blocking_publish  # type: ignore[method-assign]
    task._register_service_worker(ServiceWorkerSpec(name="api.restart", target=target))

    try:
        task._start_service_worker(
            "api.restart",
            request_id="first",
            initial_items=("one",),
        )
        assert result_published.wait(timeout=2.0)
        drain_worker_results_until(
            task,
            lambda: any(
                event.name == "api.restart"
                and event.request_id == "first"
                and event.kind == "result"
                for event, _thread_id in task.service_worker_events
            ),
        )

        task._start_service_worker(
            "api.restart",
            request_id="second",
            initial_items=("two",),
        )
        release_result_publish.set()
        drain_worker_results_until(
            task,
            lambda: any(
                event.name == "api.restart"
                and event.request_id == "second"
                and event.kind == "result"
                for event, _thread_id in task.service_worker_events
            ),
        )
    finally:
        release_result_publish.set()
        task.stop()

    result_values = [
        event.value
        for event, _thread_id in task.service_worker_events
        if event.name == "api.restart" and event.kind == "result"
    ]
    assert ("first", "one") in result_values
    assert ("second", "two") in result_values


def test_service_worker_exception_publishes_error_and_clears_active_state(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))

    def target(context: ServiceWorkerContext) -> None:
        del context
        raise RuntimeError("worker exploded")

    task._register_service_worker(ServiceWorkerSpec(name="api.error", target=target))

    try:
        task._start_service_worker("api.error")
        drain_worker_results_until(
            task,
            lambda: any(
                event.name == "api.error" and event.kind == "error"
                for event, _thread_id in task.service_worker_events
            ),
        )
        snapshot = task._service_worker_snapshot("api.error")
    finally:
        task.stop()

    error_events = [
        event
        for event, _thread_id in task.service_worker_events
        if event.name == "api.error" and event.kind == "error"
    ]
    assert len(error_events) == 1
    assert isinstance(error_events[0].error, RuntimeError)
    assert str(error_events[0].error) == "worker exploded"
    assert snapshot["active"] is False
    assert snapshot["stopping"] is True


def test_service_task_lanes_are_single_flight(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))
    worker_started = threading.Event()
    release_worker = threading.Event()

    def worker_body() -> str:
        worker_started.set()
        release_worker.wait(timeout=2.0)
        return "done"

    try:
        task._start_service_lane("unit", {"request": 1}, worker_body)
        assert worker_started.wait(timeout=2.0)
        assert task._service_lane_in_flight("unit") is True
        assert task._service_lane_work("unit") == {"request": 1}

        try:
            task._start_service_lane("unit", {"request": 2}, lambda: "duplicate")
        except RuntimeError as exc:
            assert "already has work in flight" in str(exc)
        else:  # pragma: no cover - assertion guard
            raise AssertionError("duplicate service lane start unexpectedly succeeded")

        assert task._service_lane_work("unit") == {"request": 1}
    finally:
        release_worker.set()
        deadline = time.monotonic() + 2.0
        while task._service_lane_in_flight("unit") and time.monotonic() < deadline:
            task.process_once()
            if task._service_lane_in_flight("unit"):
                task.wait_for_activity(timeout=0.01)
        task.stop()

    assert not task._service_lane_in_flight("unit")
    assert task.worker_results
    work, result, thread_id = task.worker_results[0]
    assert work == {"request": 1}
    assert result.value == "done"
    assert thread_id == threading.get_ident()
