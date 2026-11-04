"""Heartbeat task runtime tests.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-3.2]
- docs/specifications/07-System_Invariants.md [QUEUE.7]
"""

from __future__ import annotations

import heapq
import json
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import weft.core.tasks.base as base_task_mod
import weft.core.tasks.heartbeat as heartbeat_module
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    CONTROL_PAUSE,
    CONTROL_PING,
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
    WEFT_QUEUE_NAMESPACE_PREFIX,
    WEFT_STREAMING_SESSIONS_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.control_messages import encode_control_message
from weft.core.heartbeat import cancel_heartbeat, upsert_heartbeat
from weft.core.task_state import task_state_queue_name
from weft.core.tasks import HeartbeatTask
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.shared]


def make_heartbeat_taskspec(tid: str, root: Path) -> TaskSpec:
    metadata: dict[str, Any] = {
        INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
        INTERNAL_RUNTIME_ENDPOINT_NAME_KEY: INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    }
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
        metadata=metadata,
    )


def test_heartbeat_uses_cached_base_task_context(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=workdir)
    calls: list[bool | None] = []
    real_build_context = base_task_mod.build_context

    def counted_build_context(
        spec_context: str | os.PathLike[str] | None = None,
        *,
        config: Mapping[str, Any] | None = None,
        create_dirs: bool = True,
        create_database: bool = True,
        autostart: bool | None = None,
    ) -> WeftContext:
        calls.append(create_database)
        return real_build_context(
            spec_context,
            config=config,
            create_dirs=create_dirs,
            create_database=create_database,
            autostart=autostart,
        )

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

    try:
        _upsert(context, tid, "build")
        task.process_once()

        assert "build" in task._registrations

        cancel_heartbeat(context, heartbeat_id="build")
        task.wait_for_activity(0.05)
        task.process_once()

        assert "build" not in task._registrations
    finally:
        task.stop(join=False)
        task.cleanup()


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
        if destination_kind.startswith("weft."):
            assert destination_queue.startswith(WEFT_QUEUE_NAMESPACE_PREFIX)
            with pytest.raises(ValueError):
                _upsert(context, tid, "unsafe", destination_queue=destination_queue)
        with context.broker() as writer:
            writer.write(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
                _row_text(
                    tid,
                    "unsafe",
                    destination_queue=destination_queue,
                    message="unsafe",
                ),
            )
        task.process_once()

        assert "unsafe" not in task._registrations
        assert _raw_registration_ids(context) == []
        assert len(_task_events(context, tid, "heartbeat_request_invalid")) == 1

        _upsert(
            context, tid, "safe", destination_queue=safe_destination, message="safe"
        )
        task.wait_for_activity(0.05)
        task.process_once()

        registration = task._registrations["safe"]
        assert registration.destination_queue == safe_destination
        _force_due(task, "safe", time.monotonic())
        task.process_once()
        assert safe_queue.read_one() == "safe"
    finally:
        task.stop(join=False)
        task.cleanup()
        safe_queue.close()


def test_heartbeat_duplicate_upsert_replaces_existing_registration(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))

    try:
        _upsert(context, tid, "build")
        task.process_once()
        _upsert(
            context,
            tid,
            "build",
            interval_seconds=HEARTBEAT_MIN_INTERVAL_SECONDS * 2,
            destination_queue="build.queue.next",
            message={"kind": "next"},
        )
        task.wait_for_activity(0.05)
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


def test_heartbeat_late_wake_coalesces_to_one_emit(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    destination = context.queue("build.queue", persistent=False)

    try:
        _upsert(context, tid, "build")
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
        destination.close()


def test_heartbeat_serializes_structured_payloads_on_emit(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    destination = context.queue("build.queue", persistent=False)

    try:
        payload = {"kind": "build", "value": 1}
        _upsert(context, tid, "build", message=payload)
        task.process_once()

        _force_due(task, "build", time.monotonic())

        task.process_once()

        assert destination.read_one() == json.dumps(payload, ensure_ascii=False)
    finally:
        task.stop(join=False)
        task.cleanup()
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
    mappings = context.queue(task_state_queue_name(high_tid), persistent=False)

    try:
        high_task.process_once()

        assert low_task.should_stop is False
        assert high_task.should_stop is True
        assert high_task.taskspec.state.status == "completed"
        high_task_mappings = [
            json.loads(message)
            for message in mappings.peek_generator()
            if json.loads(message).get("full") == high_tid
        ]
        assert high_task_mappings[-1]["terminal"] is True
    finally:
        low_task.stop(join=False)
        low_task.cleanup()
        high_task.stop(join=False)
        high_task.cleanup()
        mappings.close()


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
        *,
        broker: Any | None = None,
    ) -> object:
        resolve_calls.append(name)
        return real_resolve_endpoint(ctx, name, broker=broker)  # type: ignore[arg-type]

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

        # An empty table publishes no deadline, so only the caller's bound applies.
        assert task.next_wait_timeout() is None
        assert wait_calls == [9.0]
    finally:
        task.stop(join=False)
        task.cleanup()


def test_heartbeat_pending_input_wakes_through_reactor_wait(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))

    try:
        assert task.next_wait_timeout() == 0.0
        task.process_once()

        _upsert(context, tid, "build")

        # No timer with an empty table: the upsert itself must end the wait.
        assert task.next_wait_timeout() is None
        started_at = time.monotonic()
        task.wait_for_activity(timeout=5.0)
        assert time.monotonic() - started_at < 0.5
    finally:
        task.stop(join=False)
        task.cleanup()


def test_heartbeat_next_wait_timeout_returns_zero_for_due_registration(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))

    try:
        _upsert(context, tid, "build")
        task.process_once()
        _force_due(task, "build", time.monotonic())

        assert task.next_wait_timeout() == 0.0
    finally:
        task.stop(join=False)
        task.cleanup()


def test_heartbeat_ping_while_waiting_is_handled_promptly(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    ctrl_in = context.queue(f"T{tid}.ctrl_in", persistent=False)
    ctrl_out = context.queue(f"T{tid}.ctrl_out", persistent=False)
    reply_to = f"test.heartbeat.{tid}.ctrl_in"
    reply_queue = context.queue(reply_to, persistent=False)

    try:
        task.process_once()
        ctrl_in.write(
            encode_control_message(
                CONTROL_PING,
                request_id="ping",
                reply_to=reply_to,
            )
        )

        # No timer with an empty table: the PING itself must end the wait.
        assert task.next_wait_timeout() is None
        started_at = time.monotonic()
        task.wait_for_activity(timeout=5.0)
        assert time.monotonic() - started_at < 0.5
        task.process_once()

        responses = [json.loads(item) for item in reply_queue.peek_generator()]
        pong = next(response for response in responses if response["command"] == "PING")
        assert pong["status"] == "ok"
        assert pong["message"] == "PONG"
        assert pong["request_id"] == "ping"
    finally:
        task.stop(join=False)
        task.cleanup()
        ctrl_in.close()
        ctrl_out.close()
        reply_queue.close()


def _upsert(
    context: WeftContext,
    registrant_tid: str,
    heartbeat_id: str,
    *,
    interval_seconds: int = HEARTBEAT_MIN_INTERVAL_SECONDS,
    lease_seconds: int = 3600,
    destination_queue: str = "build.queue",
    message: Any = "go",
) -> None:
    upsert_heartbeat(
        context,
        registrant_tid=registrant_tid,
        heartbeat_id=heartbeat_id,
        interval_seconds=interval_seconds,
        lease_seconds=lease_seconds,
        destination_queue=destination_queue,
        message=message,
    )


def _registration_rows(context: WeftContext) -> list[tuple[dict[str, Any], int]]:
    with context.broker() as reader:
        return [
            (json.loads(body), int(timestamp))
            for body, timestamp in reader.peek_generator(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, with_timestamps=True
            )
        ]


def _raw_registration_ids(context: WeftContext) -> list[int]:
    with context.broker() as reader:
        return [
            int(timestamp)
            for _body, timestamp in reader.peek_generator(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, with_timestamps=True
            )
        ]


def _task_events(context: WeftContext, tid: str, event: str) -> list[dict[str, Any]]:
    with context.broker() as reader:
        rows = [
            json.loads(body)
            for body in reader.peek_generator(
                WEFT_GLOBAL_LOG_QUEUE, with_timestamps=False
            )
        ]
    return [row for row in rows if row.get("event") == event and row.get("tid") == tid]


def _row_text(tid: str, heartbeat_id: str, **overrides: Any) -> str:
    row: dict[str, Any] = {
        "heartbeat_id": heartbeat_id,
        "registrant_tid": tid,
        "interval_seconds": HEARTBEAT_MIN_INTERVAL_SECONDS,
        "lease_seconds": 3600,
        "destination_queue": "build.queue",
        "message": "go",
    }
    row.update(overrides)
    return json.dumps(row)


def _patch_heartbeat_clock(
    monkeypatch: pytest.MonkeyPatch,
    clock: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        heartbeat_module,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock["monotonic"],
            time=time.time,
            time_ns=lambda: time.time_ns() if clock.get("ns") is None else clock["ns"],
            sleep=time.sleep,
        ),
    )


def _force_due(task: HeartbeatTask, heartbeat_id: str, now: float) -> None:
    registration = task._registrations[heartbeat_id]
    registration.next_due_at = now - 1.0
    task._due_heap.clear()
    heapq.heappush(task._due_heap, (registration.next_due_at, heartbeat_id))


def test_heartbeat_table_is_peeked_not_consumed(workdir: Path) -> None:
    """The service reads registrations by peeking; it consumes no valid row."""

    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    try:
        _upsert(context, tid, "build")
        before = _registration_rows(context)

        task.process_once()

        assert "build" in task._registrations
        assert _registration_rows(context) == before
    finally:
        task.stop(join=False)
        task.cleanup()


def test_heartbeat_restart_rebuilds_table_from_queue(workdir: Path) -> None:
    """A new heartbeat task rebuilds its table with no re-registration [MF-3.2]."""

    context = build_context(spec_context=workdir)
    first_tid = str(time.time_ns())
    first = HeartbeatTask(
        context.broker_target, make_heartbeat_taskspec(first_tid, workdir)
    )
    _upsert(context, first_tid, "build")
    try:
        first.process_once()
        assert "build" in first._registrations
    finally:
        first.stop(join=False)
        first.cleanup()

    second_tid = str(time.time_ns())
    second = HeartbeatTask(
        context.broker_target, make_heartbeat_taskspec(second_tid, workdir)
    )
    destination = context.queue("build.queue", persistent=False)
    try:
        second.process_once()
        assert "build" in second._registrations
        _force_due(second, "build", time.monotonic())

        second.process_once()

        assert destination.read_one() == "go"
        assert destination.read_one() is None
    finally:
        second.stop(join=False)
        second.cleanup()
        destination.close()


def test_heartbeat_empty_table_never_idle_exits(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An always-on service keeps running with an empty table [MF-3.2].

    Mutation caught: an idle exit on an empty table (any timeout).
    """

    clock: dict[str, Any] = {"monotonic": 1000.0, "ns": None}
    _patch_heartbeat_clock(monkeypatch, clock)
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    try:
        _upsert(context, tid, "build")
        task.process_once()
        assert "build" in task._registrations

        cancel_heartbeat(context, heartbeat_id="build")
        task.wait_for_activity(0.05)
        task.process_once()
        assert task._registrations == {}

        for offset in (61.0, 3600.0, 10.0**6):
            clock["monotonic"] = 1000.0 + offset
            task.process_once()
            assert not task.should_stop
        assert _task_events(context, tid, "heartbeat_service_idle_shutdown") == []
    finally:
        task.stop(join=False)
        task.cleanup()


def test_heartbeat_empty_table_publishes_no_timer(workdir: Path) -> None:
    """With no rows the service has no deadline and blocks on its watcher.

    This is the "idles when not needed" care for an always-on service.
    Mutation caught: any timer (audit, idle, poll) armed with an empty table.
    """

    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    try:
        task.process_once()
        assert task._registrations == {}
        assert task.next_wait_timeout() is None
    finally:
        task.stop(join=False)
        task.cleanup()


HEARTBEAT_QUIET_WINDOW_SECONDS = 30.0
# The quiet window is the measured quantity (turns counted inside it), not a
# synchronization sleep.


def test_heartbeat_with_empty_table_runs_zero_timer_turns_and_wakes_on_upsert(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
    thread_exception_guard: list[Any],
) -> None:
    """An always-on heartbeat idles when not needed, then serves an upsert.

    This test is the obligation behind the owner's rule (2026-11-03) that
    internal services are always on "with care taken that the service idles
    when not needed": with an empty table the driven service runs zero
    reactor turns over a 30 s quiet window, stays alive, then wakes on an
    upsert, and emits once due. Mutations caught: an idle exit; a timer with
    no rows.
    """

    del thread_exception_guard
    clock: dict[str, Any] = {"monotonic": 1000.0, "ns": None}
    _patch_heartbeat_clock(monkeypatch, clock)
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    turns: list[float] = []
    original_turn = task._process_reactor_turn

    def counted() -> None:
        turns.append(time.monotonic())
        original_turn()

    monkeypatch.setattr(task, "_process_reactor_turn", counted)
    destination = context.queue("build.queue", persistent=False)
    ctrl_in = context.queue(f"T{tid}.ctrl_in", persistent=False)
    thread = threading.Thread(target=task.run_until_stopped)
    thread.start()
    try:
        # Settle past the one-time post-start turn (startup lifecycle
        # publication), then measure the steady state.
        settle_deadline = time.monotonic() + 20.0
        while not turns or time.monotonic() - turns[-1] < 4.0:
            assert time.monotonic() < settle_deadline, "heartbeat did not settle"
            time.sleep(0.05)
        baseline = len(turns)

        time.sleep(HEARTBEAT_QUIET_WINDOW_SECONDS)

        assert thread.is_alive(), "heartbeat driver exited during the quiet window"
        assert not task.should_stop
        turns_in_window = len(turns) - baseline
        assert turns_in_window == 0, (
            f"heartbeat ran {turns_in_window} turns over "
            f"{HEARTBEAT_QUIET_WINDOW_SECONDS}s with an empty table"
        )

        _upsert(context, tid, "build")
        wake_deadline = time.monotonic() + 5.0
        while "build" not in task._registrations:
            assert time.monotonic() < wake_deadline, "upsert did not wake the service"
            time.sleep(0.05)

        clock["monotonic"] = 1000.0 + HEARTBEAT_MIN_INTERVAL_SECONDS + 1.0
        ctrl_in.write(
            encode_control_message(
                CONTROL_PING,
                request_id="wake",
                reply_to=f"test.heartbeat.{tid}.ctrl_in",
            )
        )
        emit_deadline = time.monotonic() + 5.0
        while destination.peek_one() is None:
            assert time.monotonic() < emit_deadline, "due registration did not emit"
            time.sleep(0.05)
        assert destination.read_one() == "go"
    finally:
        task.stop(join=False)
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        task.cleanup()
        destination.close()
        ctrl_in.close()


def test_heartbeat_cancel_before_due_turn_suppresses_emission(workdir: Path) -> None:
    """A cancel committed before the pre-emission rebuild suppresses the emit."""

    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    destination = context.queue("build.queue", persistent=False)
    try:
        _upsert(context, tid, "build")
        task.process_once()
        _force_due(task, "build", time.monotonic())
        cancel_heartbeat(context, heartbeat_id="build")

        task.process_once()

        assert destination.read_one() is None
        assert "build" not in task._registrations
    finally:
        task.stop(join=False)
        task.cleanup()
        destination.close()


def test_heartbeat_inbox_mutation_is_rejected(workdir: Path) -> None:
    """Inbox rows are not registrations; each is rejected [MF-3.2]."""

    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    inbox = context.queue(f"T{tid}.inbox", persistent=False)
    reserved = context.queue(f"T{tid}.reserved", persistent=False)
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

        assert len(_task_events(context, tid, "heartbeat_request_invalid")) == 1
        assert task._registrations == {}
        assert inbox.peek_one() is None
        # reserved_policy_on_error defaults to keep: the rejected row is retained.
        assert reserved.peek_one() is not None
    finally:
        task.stop(join=False)
        task.cleanup()
        inbox.close()
        reserved.close()


def test_heartbeat_invalid_registration_row_is_deleted_and_reported(
    workdir: Path,
) -> None:
    """Invalid rows are deleted by exact ID and reported, never fatal [MF-3.2]."""

    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    try:
        with context.broker() as writer:
            writer.write(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
                _row_text(tid, "self-route", destination_queue=f"T{tid}.inbox"),
            )
            writer.write(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, "not a json object")
            writer.write(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
                _row_text(tid, "unschedulable", interval_seconds=10**400),
            )
            writer.write(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
                _row_text(
                    tid,
                    "short-lease",
                    interval_seconds=HEARTBEAT_MIN_INTERVAL_SECONDS,
                    lease_seconds=HEARTBEAT_MIN_INTERVAL_SECONDS - 1,
                ),
            )
            writer.write(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
                _row_text(tid, "oversized-lease", lease_seconds=10**400),
            )
            # Past Python's 4,300-digit int conversion limit: json.loads raises
            # plain ValueError, not JSONDecodeError (mutation: catching only
            # JSONDecodeError lets the decoder error escape the build).
            writer.write(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
                _row_text(tid, "digit-limit-lease", lease_seconds=0).replace(
                    '"lease_seconds": 0', '"lease_seconds": ' + "9" * 5000
                ),
            )
        # An unrelated upsert and cancel still progress past the bad rows.
        _upsert(context, tid, "unrelated")
        cancel_heartbeat(context, heartbeat_id="unrelated")
        _upsert(context, tid, "valid")
        valid_id = max(_raw_registration_ids(context))

        task.process_once()

        assert _raw_registration_ids(context) == [valid_id]
        assert len(_task_events(context, tid, "heartbeat_request_invalid")) == 6
        assert set(task._registrations) == {"valid"}
        assert task.should_stop is False
    finally:
        task.stop(join=False)
        task.cleanup()


def test_heartbeat_registrations_queue_joins_reactor_wait(workdir: Path) -> None:
    """The registrations queue is a notification interest of the wait [QUEUE.7]."""

    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    try:
        task.process_once()
        task.wait_for_activity(0.05)
        task.wait_for_activity(0.05)

        signature = task._multi_activity_waiter_signature
        assert signature is not None
        assert WEFT_HEARTBEAT_REGISTRATIONS_QUEUE in signature
        assert WEFT_STREAMING_SESSIONS_QUEUE not in signature
        assert task._has_pending_observation() is False

        _upsert(context, tid, "build")

        assert task._has_pending_observation() is True
    finally:
        task.stop(join=False)
        task.cleanup()


def test_heartbeat_cancel_while_paused_empties_table(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused service still learns a cancel and empties its table [MF-3.2]."""

    clock: dict[str, Any] = {"monotonic": 1000.0, "ns": None}
    _patch_heartbeat_clock(monkeypatch, clock)
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(
        context.broker_target,
        make_heartbeat_taskspec(tid, workdir),
    )
    ctrl_in = context.queue(f"T{tid}.ctrl_in", persistent=False)
    try:
        _upsert(context, tid, "build")
        task.process_once()
        assert "build" in task._registrations
        ctrl_in.write(encode_control_message(CONTROL_PAUSE, request_id="pause"))
        task.process_once()
        # PAUSE's observable outcome since plan 3 is its lifecycle event.
        assert len(_task_events(context, tid, "control_pause")) == 1
        assert task._paused is True
        assert task._has_pending_observation() is False

        cancel_heartbeat(context, heartbeat_id="build")
        task.wait_for_activity(0.05)
        task.process_once()
        assert task._registrations == {}
        assert task.next_wait_timeout() is None

        clock["monotonic"] = 1005.0
        task.process_once()

        assert not task.should_stop
        assert task._paused is True
    finally:
        task.stop(join=False)
        task.cleanup()
        ctrl_in.close()


def test_heartbeat_expired_registration_is_deleted_at_its_deadline(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row is deleted at its own lease deadline, even while paused [MF-3.2]."""

    clock: dict[str, Any] = {"monotonic": 1000.0, "ns": None}
    _patch_heartbeat_clock(monkeypatch, clock)
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(
        context.broker_target,
        make_heartbeat_taskspec(tid, workdir),
    )
    ctrl_in = context.queue(f"T{tid}.ctrl_in", persistent=False)
    destination = context.queue("build.queue", persistent=False)

    def advance(seconds_since_write: float) -> None:
        clock["monotonic"] = 1000.0 + seconds_since_write
        clock["ns"] = row_id + int(seconds_since_write * 1_000_000_000)

    try:
        _upsert(context, tid, "build", lease_seconds=90)
        row_id = _raw_registration_ids(context)[0]
        advance(0.0)
        task.process_once()
        assert "build" in task._registrations

        advance(61.0)
        task.process_once()
        assert destination.peek_one() == "go"

        ctrl_in.write(encode_control_message(CONTROL_PAUSE, request_id="pause"))
        task.process_once()
        # PAUSE's observable outcome since plan 3 is its lifecycle event.
        assert len(_task_events(context, tid, "control_pause")) == 1
        assert task._paused is True

        advance(89.7)
        task.process_once()
        wait_timeout = task.next_wait_timeout()
        assert wait_timeout is not None
        assert 0.25 <= wait_timeout <= 0.35

        advance(90.1)
        task.process_once()

        assert _raw_registration_ids(context) == []
        expired = _task_events(context, tid, "heartbeat_registration_expired")
        assert len(expired) == 1
        assert expired[0]["message_id"] == row_id
        assert expired[0]["heartbeat_id"] == "build"
        assert expired[0]["registrant_tid"] == tid
        assert task._registrations == {}
        assert destination.read_one() == "go"
        assert destination.read_one() is None
        assert task._paused is True

        assert task.next_wait_timeout() is None
        advance(95.2)
        task.process_once()
        assert not task.should_stop
    finally:
        task.stop(join=False)
        task.cleanup()
        ctrl_in.close()
        destination.close()


@pytest.mark.parametrize("case", ["expired-newest", "unexpired"])
def test_heartbeat_build_deletes_residue_and_an_expired_newest_row(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """The newest row of an id is its registration; older rows are residue."""

    clock: dict[str, Any] = {"monotonic": 1000.0, "ns": None}
    _patch_heartbeat_clock(monkeypatch, clock)
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    task = HeartbeatTask(context.broker_target, make_heartbeat_taskspec(tid, workdir))
    destination = context.queue("build.queue", persistent=False)
    try:
        with context.broker() as writer:
            writer.write(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
                _row_text(tid, "x", lease_seconds=3600),
            )
            row_b = writer.write(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
                _row_text(tid, "x", lease_seconds=60),
            )
        clock["ns"] = row_b + (0 if case == "unexpired" else 61 * 1_000_000_000)

        if case == "unexpired":
            task.process_once()
            assert _raw_registration_ids(context) == [row_b]
            assert task._registrations["x"].expires_at_ns == (
                row_b + 60 * 1_000_000_000
            )
            return

        task.process_once()

        assert _raw_registration_ids(context) == []
        assert task._registrations == {}
        expired = _task_events(context, tid, "heartbeat_registration_expired")
        assert [event["message_id"] for event in expired] == [row_b]
        assert destination.peek_one() is None
    finally:
        task.stop(join=False)
        task.cleanup()
        destination.close()
