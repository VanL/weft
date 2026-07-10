"""Heartbeat task runtime tests.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-3.2]
- docs/specifications/07-System_Invariants.md [QUEUE.7]
"""

from __future__ import annotations

import heapq
import json
import time
from pathlib import Path

import pytest

import weft.core.tasks.base as base_task_mod
import weft.core.tasks.heartbeat as heartbeat_module
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    CONTROL_PING,
    HEARTBEAT_ACTIVITY_WAIT_CAP_SECONDS,
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_QUEUE_NAMESPACE_PREFIX,
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


def test_heartbeat_uses_cached_base_task_context(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=workdir)
    calls: list[bool | None] = []
    real_build_context = base_task_mod.build_context

    def counted_build_context(*args: object, **kwargs: object) -> object:
        value = kwargs.get("create_database")
        calls.append(value if isinstance(value, bool) else None)
        return real_build_context(*args, **kwargs)

    monkeypatch.setattr(base_task_mod, "build_context", counted_build_context)
    task = HeartbeatTask(
        context.broker_target,
        make_heartbeat_taskspec("1778089999999999901", workdir),
    )
    try:
        assert task._context is task._task_context()
        assert calls == [False]
    finally:
        task.cleanup()


def test_heartbeat_service_accepts_upsert_and_cancel(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
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


@pytest.mark.parametrize(
    "destination_kind",
    (
        "inbox",
        "reserved",
        "ctrl_in",
        "ctrl_out",
        "weft.log.tasks",
        "weft.state.services",
        "weft.state.pipelines",
        "weft.spawn.requests",
        "weft.spawn.internal",
        "weft.manager.ctrl_in",
        "weft.manager.ctrl_out",
        "weft.manager.outbox",
        "weft.future_reserved",
    ),
)
def test_heartbeat_rejects_unsafe_destination_and_later_input_progresses(
    tmp_path: Path,
    destination_kind: str,
) -> None:
    """Runtime egress rejects self/system routes without blocking later work [MF-3.2]."""

    workdir = prepare_project_root(tmp_path)
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    inbox = context.queue(f"T{tid}.inbox", persistent=False)
    reserved = context.queue(f"T{tid}.reserved", persistent=False)
    task_log = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    task_local_destinations = {
        "inbox": f"T{tid}.inbox",
        "reserved": f"T{tid}.reserved",
        "ctrl_in": f"T{tid}.ctrl_in",
        "ctrl_out": f"T{tid}.ctrl_out",
    }
    destination_queue = task_local_destinations.get(
        destination_kind,
        destination_kind,
    )
    safe_destination = f"T{tid}1.inbox"
    safe_queue = context.queue(safe_destination, persistent=False)

    try:
        inbox.write(
            json.dumps(
                {
                    "action": "upsert",
                    "heartbeat_id": "unsafe",
                    "interval_seconds": HEARTBEAT_MIN_INTERVAL_SECONDS,
                    "destination_queue": destination_queue,
                    "message": "unsafe",
                }
            )
        )
        task.process_once()

        assert "unsafe" not in task._registrations
        assert reserved.peek_one() is not None
        assert any(
            json.loads(row).get("event") == "heartbeat_request_invalid"
            for row in task_log.peek_generator()
        )

        inbox.write(
            json.dumps(
                {
                    "action": "upsert",
                    "heartbeat_id": "safe",
                    "interval_seconds": HEARTBEAT_MIN_INTERVAL_SECONDS,
                    "destination_queue": safe_destination,
                    "message": "safe",
                }
            )
        )
        task.process_once()

        registration = task._registrations["safe"]
        assert registration.destination_queue == safe_destination
        registration.next_due_at = time.monotonic() - 1.0
        task._due_heap = [(registration.next_due_at, registration.heartbeat_id)]
        task.process_once()
        assert safe_queue.read_one() == "safe"
        if destination_kind.startswith("weft."):
            assert destination_queue.startswith(WEFT_QUEUE_NAMESPACE_PREFIX)
    finally:
        task.stop(join=False)
        task.cleanup()
        inbox.close()
        reserved.close()
        task_log.close()
        safe_queue.close()


def test_heartbeat_duplicate_upsert_replaces_existing_registration(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
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


def test_heartbeat_late_wake_coalesces_to_one_emit(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
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


def test_heartbeat_serializes_structured_payloads_on_emit(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
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


def test_duplicate_heartbeat_services_converge_by_loser_exit(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    low_tid = str(time.time_ns())
    high_tid = str(int(low_tid) + 1)
    low_task = HeartbeatTask(
        context.broker_target,
        make_heartbeat_taskspec(low_tid, workdir),
    )
    high_task = HeartbeatTask(
        context.broker_target,
        make_heartbeat_taskspec(high_tid, workdir),
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


def test_heartbeat_owner_resolution_is_endpoint_registry_version_gated(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=workdir)
    low_tid = str(time.time_ns())
    high_tid = str(int(low_tid) + 1)
    low_task = HeartbeatTask(
        context.broker_target,
        make_heartbeat_taskspec(low_tid, workdir),
    )
    high_task: HeartbeatTask | None = None
    resolve_calls: list[str] = []
    real_resolve_endpoint = heartbeat_module.resolve_endpoint

    def counted_resolve_endpoint(
        ctx: object,
        name: str,
    ) -> object:
        resolve_calls.append(name)
        return real_resolve_endpoint(ctx, name)  # type: ignore[arg-type]

    monkeypatch.setattr(
        heartbeat_module,
        "resolve_endpoint",
        counted_resolve_endpoint,
    )

    try:
        low_task.process_once()
        assert resolve_calls == [INTERNAL_HEARTBEAT_ENDPOINT_NAME]

        low_task.process_once()
        assert resolve_calls == [INTERNAL_HEARTBEAT_ENDPOINT_NAME]

        high_task = HeartbeatTask(
            context.broker_target,
            make_heartbeat_taskspec(high_tid, workdir),
        )
        low_task.process_once()

        assert resolve_calls == [
            INTERNAL_HEARTBEAT_ENDPOINT_NAME,
            INTERNAL_HEARTBEAT_ENDPOINT_NAME,
        ]
    finally:
        low_task.stop(join=False)
        low_task.cleanup()
        if high_task is not None:
            high_task.stop(join=False)
            high_task.cleanup()


def test_heartbeat_process_once_returns_without_waiting_when_no_work_is_due(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))

    def fail_wait_for_activity(timeout: float | None) -> None:
        del timeout
        raise AssertionError("process_once must not wait internally")

    monkeypatch.setattr(task, "wait_for_activity", fail_wait_for_activity)

    try:
        task.process_once()
        assert task.should_stop is False
    finally:
        task.stop(join=False)
        task.cleanup()


def test_heartbeat_run_until_stopped_uses_next_wait_timeout(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    wait_calls: list[float | None] = []

    def fake_wait_for_activity(timeout: float | None) -> None:
        wait_calls.append(timeout)
        task.should_stop = True

    monkeypatch.setattr(task, "wait_for_activity", fake_wait_for_activity)

    try:
        task.run_until_stopped(poll_interval=9.0)

        assert wait_calls == [pytest.approx(HEARTBEAT_ACTIVITY_WAIT_CAP_SECONDS)]
    finally:
        task.stop(join=False)
        task.cleanup()


def test_heartbeat_pending_input_wakes_through_reactor_wait(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    inbox = context.queue(f"T{tid}.inbox", persistent=False)

    try:
        assert task.next_wait_timeout() == pytest.approx(
            HEARTBEAT_ACTIVITY_WAIT_CAP_SECONDS
        )
        task.process_once()

        inbox.write(json.dumps({"action": "cancel", "heartbeat_id": "build"}))

        assert task.next_wait_timeout() == pytest.approx(
            HEARTBEAT_ACTIVITY_WAIT_CAP_SECONDS
        )
        started_at = time.monotonic()
        task.wait_for_activity(timeout=task.next_wait_timeout())
        assert time.monotonic() - started_at < 0.5
    finally:
        task.stop(join=False)
        task.cleanup()
        inbox.close()


def test_heartbeat_next_wait_timeout_returns_zero_for_due_registration(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
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
        registration = task._registrations["build"]
        registration.next_due_at = time.monotonic() - 1
        task._due_heap.clear()
        heapq.heappush(task._due_heap, (registration.next_due_at, "build"))

        assert task.next_wait_timeout() == 0.0
    finally:
        task.stop(join=False)
        task.cleanup()
        inbox.close()


def test_heartbeat_ping_while_waiting_is_handled_promptly(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    ctrl_in = context.queue(f"T{tid}.ctrl_in", persistent=False)
    ctrl_out = context.queue(f"T{tid}.ctrl_out", persistent=False)

    try:
        task.process_once()
        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "ping"}))

        assert task.next_wait_timeout() == pytest.approx(
            HEARTBEAT_ACTIVITY_WAIT_CAP_SECONDS
        )
        started_at = time.monotonic()
        task.wait_for_activity(timeout=task.next_wait_timeout())
        assert time.monotonic() - started_at < 0.5
        task.process_once()

        responses = [json.loads(item) for item in ctrl_out.peek_generator()]
        pong = next(response for response in responses if response["command"] == "PING")
        assert pong["status"] == "ok"
        assert pong["message"] == "PONG"
        assert pong["request_id"] == "ping"
    finally:
        task.stop(join=False)
        task.cleanup()
        ctrl_in.close()
        ctrl_out.close()
