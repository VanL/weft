"""Heartbeat task runtime tests."""

from __future__ import annotations

import heapq
import json
import time
from pathlib import Path

import pytest

from weft._constants import (
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
)
from weft.context import build_context
from weft.core.tasks import HeartbeatTask
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.shared]


def make_heartbeat_taskspec(tid: str, root: Path) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="heartbeat-service",
        spec=SpecSection(
            type="function",
            function_target="weft.tasks:noop",
            persistent=True,
            weft_context=str(root),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
        metadata={
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
            INTERNAL_RUNTIME_ENDPOINT_NAME_KEY: INTERNAL_HEARTBEAT_ENDPOINT_NAME,
        },
    )


def test_heartbeat_service_accepts_upsert_and_cancel(tmp_path: Path) -> None:
    context = build_context(spec_context=tmp_path)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, tmp_path))
    inbox = context.queue(f"T{tid}.inbox", persistent=False)

    try:
        inbox.write(
            json.dumps(
                {
                    "action": "upsert",
                    "heartbeat_id": "build",
                    "interval_seconds": HEARTBEAT_MIN_INTERVAL_SECONDS,
                    "destination_queue": "build.queue",
                    "message": "go",
                }
            )
        )
        task.process_once()

        assert "build" in task._registrations

        inbox.write(json.dumps({"action": "cancel", "heartbeat_id": "build"}))
        task.process_once()

        assert "build" not in task._registrations
    finally:
        task.stop(join=False)
        task.cleanup()
        inbox.close()


def test_heartbeat_duplicate_upsert_replaces_existing_registration(
    tmp_path: Path,
) -> None:
    context = build_context(spec_context=tmp_path)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, tmp_path))
    inbox = context.queue(f"T{tid}.inbox", persistent=False)

    try:
        inbox.write(
            json.dumps(
                {
                    "action": "upsert",
                    "heartbeat_id": "build",
                    "interval_seconds": HEARTBEAT_MIN_INTERVAL_SECONDS,
                    "destination_queue": "build.queue",
                    "message": "go",
                }
            )
        )
        task.process_once()
        inbox.write(
            json.dumps(
                {
                    "action": "upsert",
                    "heartbeat_id": "build",
                    "interval_seconds": HEARTBEAT_MIN_INTERVAL_SECONDS * 2,
                    "destination_queue": "build.queue.next",
                    "message": {"kind": "next"},
                }
            )
        )
        task.process_once()

        registration = task._registrations["build"]
        assert len(task._registrations) == 1
        assert registration.interval_seconds == HEARTBEAT_MIN_INTERVAL_SECONDS * 2
        assert registration.destination_queue == "build.queue.next"
        assert registration.message_text == json.dumps(
            {"kind": "next"}, ensure_ascii=False
        )
    finally:
        task.stop(join=False)
        task.cleanup()
        inbox.close()


def test_heartbeat_late_wake_coalesces_to_one_emit(tmp_path: Path) -> None:
    context = build_context(spec_context=tmp_path)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, tmp_path))
    inbox = context.queue(f"T{tid}.inbox", persistent=False)
    destination = context.queue("build.queue", persistent=False)

    try:
        inbox.write(
            json.dumps(
                {
                    "action": "upsert",
                    "heartbeat_id": "build",
                    "interval_seconds": HEARTBEAT_MIN_INTERVAL_SECONDS,
                    "destination_queue": "build.queue",
                    "message": "go",
                }
            )
        )
        task.process_once()

        registration = task._registrations["build"]
        registration.next_due_at = time.monotonic() - (
            HEARTBEAT_MIN_INTERVAL_SECONDS * 2
        )
        task._due_heap.clear()
        heapq.heappush(task._due_heap, (registration.next_due_at, "build"))

        task.process_once()

        assert destination.read_one() == "go"
        assert destination.read_one() is None
        assert registration.next_due_at > time.monotonic()
    finally:
        task.stop(join=False)
        task.cleanup()
        inbox.close()
        destination.close()


def test_heartbeat_serializes_structured_payloads_on_emit(tmp_path: Path) -> None:
    context = build_context(spec_context=tmp_path)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, tmp_path))
    inbox = context.queue(f"T{tid}.inbox", persistent=False)
    destination = context.queue("build.queue", persistent=False)

    try:
        payload = {"kind": "build", "value": 1}
        inbox.write(
            json.dumps(
                {
                    "action": "upsert",
                    "heartbeat_id": "build",
                    "interval_seconds": HEARTBEAT_MIN_INTERVAL_SECONDS,
                    "destination_queue": "build.queue",
                    "message": payload,
                }
            )
        )
        task.process_once()

        registration = task._registrations["build"]
        registration.next_due_at = time.monotonic() - 1
        task._due_heap.clear()
        heapq.heappush(task._due_heap, (registration.next_due_at, "build"))

        task.process_once()

        assert destination.read_one() == json.dumps(payload, ensure_ascii=False)
    finally:
        task.stop(join=False)
        task.cleanup()
        inbox.close()
        destination.close()


def test_duplicate_heartbeat_services_converge_by_loser_exit(tmp_path: Path) -> None:
    context = build_context(spec_context=tmp_path)
    low_tid = str(time.time_ns())
    high_tid = str(int(low_tid) + 1)
    low_task = HeartbeatTask(
        context.broker_target,
        make_heartbeat_taskspec(low_tid, tmp_path),
    )
    high_task = HeartbeatTask(
        context.broker_target,
        make_heartbeat_taskspec(high_tid, tmp_path),
    )

    try:
        high_task.process_once()

        assert low_task.should_stop is False
        assert high_task.should_stop is True
        assert high_task.taskspec.state.status == "completed"
    finally:
        low_task.stop(join=False)
        low_task.cleanup()
        high_task.stop(join=False)
        high_task.cleanup()


def test_heartbeat_wait_fallback_uses_bounded_stop_event_sleep(tmp_path: Path) -> None:
    context = build_context(spec_context=tmp_path)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, tmp_path))

    class FakeEvent:
        def __init__(self) -> None:
            self.calls: list[float] = []
            self._set = False

        def is_set(self) -> bool:
            return self._set

        def set(self) -> None:
            self._set = True

        def wait(self, timeout: float | None = None) -> bool:
            self.calls.append(0.0 if timeout is None else timeout)
            return False

    fake_event = FakeEvent()
    pending_checks = iter([False, True])
    task._activity_waiters = []
    task._stop_event = fake_event  # type: ignore[assignment]
    task._has_pending_runtime_input = lambda: next(pending_checks)  # type: ignore[method-assign]

    try:
        task._wait_for_activity(timeout=0.25)
        assert fake_event.calls
        assert fake_event.calls[0] == pytest.approx(0.25, rel=0.2)
    finally:
        task.stop(join=False)
        task.cleanup()
