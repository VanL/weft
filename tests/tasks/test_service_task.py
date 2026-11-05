"""Tests for shared long-lived service task helpers.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1] item 7
- docs/specifications/05-Message_Flow_and_State.md [MF-3.2]
- docs/specifications/07-System_Invariants.md [IMPL.10]
"""

from __future__ import annotations

import json
import os
import queue as thread_queue
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import psutil
import pytest

import weft.core.tasks.service as service_module
from simplebroker import Queue
from tests.helpers.typing import BrokerEnv
from weft._constants import (
    CONTROL_STOP,
    MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    SERVICE_IDLE_EXIT_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
)
from weft.core.control_messages import encode_control_message
from weft.core.service_convergence import build_manager_service_payload
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


def drain_log_events(log_queue: Queue) -> list[dict[str, Any]]:
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
    broker_env: BrokerEnv,
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
    broker_env: BrokerEnv,
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
    broker_env: BrokerEnv,
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
        assert task.next_wait_timeout() is None
        task.process_once()
    finally:
        task.stop()

    assert drain_log_events(log_queue) == []


def test_service_task_cleanup_passes_one_absolute_deadline_to_worker_groups(
    broker_env: BrokerEnv,
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
        deadline: float | None = None,
    ) -> None:
        assert deadline is not None
        observed_deadlines.append(deadline)

    monkeypatch.setattr(task, "_stop_service_worker", record_stop)
    started_at = time.monotonic()
    task.stop(timeout=0.2)

    assert len(observed_deadlines) == 1
    assert started_at < observed_deadlines[0] <= started_at + 0.25


def test_service_task_full_sentinel_queue_respects_cleanup_deadline(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))
    worker_entered = threading.Event()
    release_worker = threading.Event()

    def blocked_target(
        context: ServiceWorkerContext, *args: object, **kwargs: object
    ) -> None:
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
    task._stop_service_worker("blocked", deadline=started_at + 0.05)
    elapsed = time.monotonic() - started_at
    release_worker.set()
    for thread in registration.threads:
        thread.join(timeout=2.0)
    task.stop(timeout=2.0)

    assert elapsed < 0.3
    assert task._task_lifecycle.value == "closed"


class _CountingInputQueue(thread_queue.Queue[Any]):
    """Delegating input queue that records each ``get`` call and its budget."""

    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize=maxsize)
        self.get_timeouts: list[float | None] = []
        self.entered_get = threading.Event()

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        self.get_timeouts.append(timeout)
        self.entered_get.set()
        return super().get(block=block, timeout=timeout)


def _register_idle_iterating_worker(
    task: ServiceTestTask,
    name: str,
) -> _CountingInputQueue:
    """Register a worker that loops over ``iter_items`` behind a counting queue."""

    def target(context: ServiceWorkerContext) -> None:
        for _item in context.iter_items():
            pass

    task._register_service_worker(ServiceWorkerSpec(name=name, target=target))
    counting_queue = _CountingInputQueue()
    task._service_worker_registrations[name].input_queue = counting_queue
    return counting_queue


def test_idle_service_worker_does_not_wake_until_stopped(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    """An idle service worker blocks in one untimed ``get`` until stop.

    Spec: [CC-2.2.1], [IMPL.9]
    """

    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))
    counting_queue = _register_idle_iterating_worker(task, "idle")
    threads: tuple[threading.Thread, ...] = ()
    try:
        task._start_service_worker("idle")
        threads = task._service_worker_registrations["idle"].threads
        assert counting_queue.entered_get.wait(2.0)
        time.sleep(0.5)

        assert len(counting_queue.get_timeouts) == 1
        assert counting_queue.get_timeouts == [None]
    finally:
        task.stop(timeout=2.0)

    assert len(threads) == 1
    assert not any(thread.is_alive() for thread in threads)


def test_stop_delivers_sentinel_after_cleanup_deadline_expired(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    """Stop wakes a blocked worker even when the cleanup deadline has passed.

    Spec: [IMPL.10]
    """

    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))
    counting_queue = _register_idle_iterating_worker(task, "expired")
    try:
        task._start_service_worker("expired")
        threads = task._service_worker_registrations["expired"].threads
        assert counting_queue.entered_get.wait(2.0)

        task._stop_service_worker("expired", deadline=time.monotonic() - 1.0)
        for thread in threads:
            thread.join(timeout=2.0)

        assert len(threads) == 1
        assert not any(thread.is_alive() for thread in threads)
    finally:
        task.stop(timeout=2.0)


def test_service_task_due_time_helpers_bound_waits(
    broker_env: BrokerEnv,
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
    broker_env: BrokerEnv,
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
    broker_env: BrokerEnv,
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
    broker_env: BrokerEnv,
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
    broker_env: BrokerEnv,
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
    broker_env: BrokerEnv,
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


def test_service_worker_fatal_exit_is_published_with_exact_identity(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))

    class FatalServiceExit(BaseException):
        pass

    fatal = FatalServiceExit("fatal service exit")

    def target(context: ServiceWorkerContext, *args: object, **kwargs: object) -> None:
        raise fatal

    task._register_service_worker(ServiceWorkerSpec(name="api.fatal", target=target))

    try:
        task._start_service_worker("api.fatal", request_id="fatal-request")
        drain_worker_results_until(
            task,
            lambda: any(
                event.name == "api.fatal" and event.kind == "stopped"
                for event, _thread_id in task.service_worker_events
            ),
        )
        snapshot = task._service_worker_snapshot("api.fatal")
    finally:
        task.stop()

    fatal_events = [
        event
        for event, thread_id in task.service_worker_events
        if event.name == "api.fatal"
        and event.kind == "error"
        and thread_id == threading.get_ident()
    ]
    assert len(fatal_events) == 1
    fatal_event = fatal_events[0]
    assert fatal_event.request_id == "fatal-request"
    assert fatal_event.worker_index == 0
    assert fatal_event.value is None
    assert fatal_event.error is fatal
    assert fatal_event.item_id is None
    assert snapshot["active"] is False
    assert snapshot["stopping"] is True


def test_service_task_lanes_are_single_flight(
    broker_env: BrokerEnv,
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


# --- Service idle clock ([MA-1] item 7, [MF-3.2]; owner rules 2026-11-04) ---

_IDLE_WINDOW_NS = int(SERVICE_IDLE_EXIT_SECONDS * 1_000_000_000)
_THIS_CREATE_TIME = psutil.Process(os.getpid()).create_time()


class IdleClockServiceTask(ServiceTestTask):
    """Service double whose work and observation state the test sets.

    Its turn runs one "cycle" (counted) that changes nothing the predicate
    reads, so a turn without work is an empty cycle.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.work = False
        self.undetermined = False
        self.cycles = 0
        super().__init__(*args, **kwargs)

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        configs = super()._build_queue_configs()
        configs[self._queue_names["ctrl_in"]] = self._peek_queue_config(
            self._handle_control_message
        )
        return configs

    def _service_reactor_turn(self) -> None:
        super()._service_reactor_turn()
        self.cycles += 1

    def _has_work(self) -> bool:
        return self.work

    def _work_undetermined(self) -> bool:
        return self.undetermined


@pytest.fixture
def idle_clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Freeze the service idle clock through the service module's seam."""

    clock = {"ns": 10**12}
    monkeypatch.setattr(service_module, "_service_monotonic_ns", lambda: clock["ns"])
    return clock


def _task_events(log_queue: Queue, tid: str, event: str) -> list[dict[str, Any]]:
    """Return this task's visible global-log events with one event name."""

    return [
        payload
        for payload in (
            json.loads(body)
            for body, _timestamp in log_queue.peek_generator(with_timestamps=True)
        )
        if payload.get("tid") == tid and payload.get("event") == event
    ]


def _write_stop(make_queue: Callable[[str], Queue], task: ServiceTask) -> None:
    """Write STOP to the task's ctrl_in and let its watcher observe it."""

    make_queue(task.taskspec.io.control["ctrl_in"]).write(
        encode_control_message(CONTROL_STOP, request_id="stop")
    )
    task.wait_for_activity(timeout=1.0)


def test_service_with_work_has_no_idle_deadline(
    broker_env: BrokerEnv,
    unique_tid: str,
    idle_clock: dict[str, int],
) -> None:
    """(a) With work, ``next_wait_timeout`` carries no idle deadline."""

    db_path, _make_queue = broker_env
    task = IdleClockServiceTask(db_path, make_service_taskspec(unique_tid))
    task.work = True
    try:
        task.process_once()
        idle_clock["ns"] += 10 * _IDLE_WINDOW_NS
        task.process_once()
        assert task.next_wait_timeout() is None
        assert task._idle_since_monotonic_ns is None
        assert not task.should_stop
    finally:
        task.stop()


def test_service_without_work_exits_once_the_window_elapses(
    broker_env: BrokerEnv,
    unique_tid: str,
    idle_clock: dict[str, int],
) -> None:
    """(b) Without work the remainder counts down and the task exits."""

    db_path, make_queue = broker_env
    task = IdleClockServiceTask(db_path, make_service_taskspec(unique_tid))
    task._activate_service_task()
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    try:
        task.process_once()
        assert task.next_wait_timeout() == SERVICE_IDLE_EXIT_SECONDS
        idle_clock["ns"] += _IDLE_WINDOW_NS // 2
        assert task.next_wait_timeout() == SERVICE_IDLE_EXIT_SECONDS / 2
        task.process_once()
        assert not task.should_stop

        idle_clock["ns"] += _IDLE_WINDOW_NS // 2
        assert task.next_wait_timeout() == 0.0
        task.process_once()

        assert task.should_stop
        assert task.taskspec.state.status == "completed"
        assert task.taskspec.state.return_code == 0
        exits = _task_events(log_queue, unique_tid, "service_idle_exit")
        assert len(exits) == 1
        assert exits[0]["idle_seconds"] == SERVICE_IDLE_EXIT_SECONDS
    finally:
        task.stop()


def test_service_work_returning_mid_window_clears_the_clock(
    broker_env: BrokerEnv,
    unique_tid: str,
    idle_clock: dict[str, int],
) -> None:
    """(c) Work mid-window clears the clock; the next window starts afresh."""

    db_path, _make_queue = broker_env
    task = IdleClockServiceTask(db_path, make_service_taskspec(unique_tid))
    try:
        task.process_once()
        assert task._idle_since_monotonic_ns == idle_clock["ns"]
        idle_clock["ns"] += _IDLE_WINDOW_NS - 1
        task.work = True
        task.process_once()
        assert task._idle_since_monotonic_ns is None

        task.work = False
        idle_clock["ns"] += _IDLE_WINDOW_NS
        task.process_once()
        restarted = idle_clock["ns"]
        assert task._idle_since_monotonic_ns == restarted
        idle_clock["ns"] += _IDLE_WINDOW_NS - 1
        task.process_once()
        assert not task.should_stop
        assert task._idle_since_monotonic_ns == restarted
    finally:
        task.stop()


def test_service_stop_during_idle_window_stops_normally(
    broker_env: BrokerEnv,
    unique_tid: str,
    idle_clock: dict[str, int],
) -> None:
    """(d) A STOP during the idle window stops the task as for any task."""

    db_path, make_queue = broker_env
    task = IdleClockServiceTask(db_path, make_service_taskspec(unique_tid))
    task._activate_service_task()
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    try:
        task.process_once()
        idle_clock["ns"] += _IDLE_WINDOW_NS // 2
        _write_stop(make_queue, task)
        task.process_once()
        assert task.should_stop
        assert task.taskspec.state.status == "cancelled"
        assert _task_events(log_queue, unique_tid, "service_idle_exit") == []
    finally:
        task.stop()


def test_service_empty_turn_does_not_move_a_set_clock(
    broker_env: BrokerEnv,
    unique_tid: str,
    idle_clock: dict[str, int],
) -> None:
    """(e) Checking for work is not work: empty cycles leave the clock alone."""

    db_path, _make_queue = broker_env
    task = IdleClockServiceTask(db_path, make_service_taskspec(unique_tid))
    try:
        task.process_once()
        started = task._idle_since_monotonic_ns
        assert started == idle_clock["ns"]
        for _ in range(5):
            idle_clock["ns"] += _IDLE_WINDOW_NS // 10
            task.process_once()
            assert task._idle_since_monotonic_ns == started
        assert task.cycles == 6
        assert not task.should_stop
    finally:
        task.stop()


@pytest.mark.parametrize("held_result", ["empty", "substantive"])
def test_service_undetermined_turn_defers_the_decision(
    broker_env: BrokerEnv,
    unique_tid: str,
    idle_clock: dict[str, int],
    held_result: str,
) -> None:
    """(f) An observation in flight at an expired window neither exits nor moves.

    The held asynchronous result then decides: substantive clears the clock,
    empty exits on the original window.
    """

    db_path, _make_queue = broker_env
    task = IdleClockServiceTask(db_path, make_service_taskspec(unique_tid))
    task._activate_service_task()
    try:
        task.process_once()
        started = task._idle_since_monotonic_ns
        idle_clock["ns"] += _IDLE_WINDOW_NS
        task.undetermined = True
        task.process_once()
        assert not task.should_stop
        assert task._idle_since_monotonic_ns == started
        assert task.next_wait_timeout() is None

        task.undetermined = False
        task.work = held_result == "substantive"
        task.process_once()
        if held_result == "substantive":
            assert not task.should_stop
            assert task._idle_since_monotonic_ns is None
        else:
            assert task.should_stop
            assert task.taskspec.state.status == "completed"
    finally:
        task.stop()


def test_service_stop_in_the_expiry_turn_is_not_marked_completed(
    broker_env: BrokerEnv,
    unique_tid: str,
    idle_clock: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(g) A STOP handled in the turn that reaches expiry wins; no completion."""

    db_path, make_queue = broker_env
    task = IdleClockServiceTask(db_path, make_service_taskspec(unique_tid))
    task._activate_service_task()
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    completions: list[int | None] = []
    real_mark_completed = TaskSpec.mark_completed

    def spy_mark_completed(self: TaskSpec, *args: Any, **kwargs: Any) -> None:
        completions.append(kwargs.get("return_code"))
        real_mark_completed(self, *args, **kwargs)

    try:
        task.process_once()
        monkeypatch.setattr(TaskSpec, "mark_completed", spy_mark_completed)
        idle_clock["ns"] += _IDLE_WINDOW_NS
        _write_stop(make_queue, task)
        task.process_once()
        assert task.should_stop
        assert task.taskspec.state.status == "cancelled"
        assert completions == []
        assert _task_events(log_queue, unique_tid, "service_idle_exit") == []
    finally:
        task.stop()


@pytest.mark.parametrize(
    ("manager_status", "handle_kind", "stays"),
    [
        ("active", "this_process", True),
        ("active", "unverified", True),
        ("active", "dead_identity", False),
        ("superseded", "this_process", False),
    ],
)
def test_service_live_manager_keeps_an_idle_service(
    broker_env: BrokerEnv,
    unique_tid: str,
    idle_clock: dict[str, int],
    manager_status: str,
    handle_kind: str,
    stays: bool,
) -> None:
    """A live non-superseded Manager keeps an idle service (owner ruling 10.1).

    The window restarts on each check, so the service re-reads the registry
    at its next idle deadline instead of exiting. A superseded Manager row or
    a host-pid identity that no longer matches is not a live Manager.
    """

    db_path, make_queue = broker_env
    task = IdleClockServiceTask(db_path, make_service_taskspec(unique_tid))
    task._activate_service_task()
    handles: dict[str, dict[str, Any]] = {
        "this_process": {
            "runner": "host",
            "kind": "process",
            "id": str(os.getpid()),
            "control": {"authority": "host-pid"},
            "observations": {
                "host_pids": [os.getpid()],
                "host_processes": [
                    {"pid": os.getpid(), "create_time": _THIS_CREATE_TIME}
                ],
            },
            "metadata": {},
        },
        "unverified": {},
        "dead_identity": {
            "runner": "host",
            "kind": "process",
            "id": str(os.getpid()),
            "control": {"authority": "host-pid"},
            "observations": {
                "host_pids": [os.getpid()],
                "host_processes": [{"pid": os.getpid(), "create_time": 1.0}],
            },
            "metadata": {},
        },
    }
    make_queue(WEFT_SERVICES_REGISTRY_QUEUE).write(
        json.dumps(
            build_manager_service_payload(
                context=task._task_context(),
                tid=str(time.time_ns()),
                name="manager",
                status=cast(Any, manager_status),
                queues={},
                runtime_handle=handles[handle_kind],
            )
        )
    )
    try:
        task.process_once()
        idle_clock["ns"] += _IDLE_WINDOW_NS
        task.process_once()
        assert task.should_stop is (not stays)
        if stays:
            assert task._idle_since_monotonic_ns == idle_clock["ns"]
            assert task.next_wait_timeout() == SERVICE_IDLE_EXIT_SECONDS
    finally:
        task.stop()


def test_service_without_work_definition_never_idle_exits(
    broker_env: BrokerEnv,
    unique_tid: str,
    idle_clock: dict[str, int],
) -> None:
    """A service that does not define its work keeps the default: never idle."""

    db_path, _make_queue = broker_env
    task = ServiceTestTask(db_path, make_service_taskspec(unique_tid))
    try:
        task.process_once()
        idle_clock["ns"] += 10 * _IDLE_WINDOW_NS
        task.process_once()
        assert not task.should_stop
        assert task.next_wait_timeout() is None
    finally:
        task.stop()


def _host_pid_manager_payload(task: ServiceTask) -> dict[str, Any]:
    """Return an active canonical Manager row naming this process exactly."""

    return build_manager_service_payload(
        context=task._task_context(),
        tid=str(time.time_ns()),
        name="manager",
        status="active",
        queues={},
        runtime_handle={
            "runner": "host",
            "kind": "process",
            "id": str(os.getpid()),
            "control": {"authority": "host-pid"},
            "observations": {
                "host_pids": [os.getpid()],
                "host_processes": [
                    {"pid": os.getpid(), "create_time": _THIS_CREATE_TIME}
                ],
            },
            "metadata": {},
        },
    )


@pytest.mark.parametrize("row_age", ["fresh", "past_unknown_policy"])
def test_service_unresolved_manager_identity_follows_the_unknown_row_policy(
    broker_env: BrokerEnv,
    unique_tid: str,
    idle_clock: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    row_age: str,
) -> None:
    """An unresolved Manager identity is ``unknown``, not absence (review R3).

    With the host probe denied (``AccessDenied``), the Manager reader's
    three-valued liveness is ``unknown``. A fresh row keeps the idle service
    alive past its window; a row older than the unknown-row age policy that
    the Manager reader and ``weft status`` apply no longer does, so the
    service idle-exits.
    """

    db_path, make_queue = broker_env
    task = IdleClockServiceTask(db_path, make_service_taskspec(unique_tid))
    task._activate_service_task()
    make_queue(WEFT_SERVICES_REGISTRY_QUEUE).write(
        json.dumps(_host_pid_manager_payload(task))
    )

    def access_denied(pid: int) -> Any:
        raise psutil.AccessDenied(pid)

    # Every host-identity probe in the process is denied, as for a Manager
    # owned by another user.
    monkeypatch.setattr(psutil, "Process", access_denied)
    if row_age == "past_unknown_policy":
        aged_ns = time.time_ns() + int(
            (MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS + 1) * 1_000_000_000
        )
        monkeypatch.setattr(
            service_module,
            "time",
            SimpleNamespace(
                time_ns=lambda: aged_ns,
                monotonic=time.monotonic,
                monotonic_ns=time.monotonic_ns,
            ),
        )
    try:
        task.process_once()
        idle_clock["ns"] += _IDLE_WINDOW_NS
        task.process_once()
        if row_age == "fresh":
            assert not task.should_stop
            assert task.taskspec.state.status == "running"
        else:
            assert task.should_stop
            assert task.taskspec.state.status == "completed"
    finally:
        task.stop()
