"""Tests for shared long-lived service task helpers."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.core.tasks.base import TaskWorkerResult
from weft.core.tasks.multiqueue_watcher import QueueMessageContext
from weft.core.tasks.service import ServiceTask
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
        work = self._pop_service_lane_work(result.lane)
        self.worker_results.append((work, result, threading.get_ident()))


def make_service_taskspec(tid: str) -> TaskSpec:
    """Create a minimal service TaskSpec with explicit queue mappings."""

    return TaskSpec(
        tid=tid,
        name="service-test",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
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
