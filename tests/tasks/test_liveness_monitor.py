"""LivenessMonitor service and exact mapping-reaper tests."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import weft.core.tasks.liveness_monitor as liveness_monitor_mod
from simplebroker import Queue
from weft._constants import (
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR,
    LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES,
    LIVENESS_PROBE_WORKER_NAME,
)
from weft.context import WeftContext, build_context
from weft.core.task_state import task_state_queue_name
from weft.core.tasks.liveness_monitor import (
    LivenessMonitor,
    ProbeResult,
    ProbeWork,
)
from weft.core.tasks.service import ServiceWorkerEvent
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec
from weft.helpers import tid_short_form
from weft.liveness.models import LivenessObservation
from weft.liveness.policy import UnknownDeadlineState

pytestmark = [pytest.mark.shared]


def test_liveness_constructor_failure_stops_started_service_workers(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=workdir)
    worker_threads: list[threading.Thread] = []
    worker_stop_events: list[threading.Event] = []

    def fail_after_worker_start(self: LivenessMonitor) -> None:
        with self._service_worker_lock:
            registration = self._service_worker_registrations[
                LIVENESS_PROBE_WORKER_NAME
            ]
            worker_threads.extend(registration.threads)
            worker_stop_events.append(registration.stop_event)
        raise RuntimeError("injected liveness initialization failure")

    monkeypatch.setattr(
        LivenessMonitor, "_activate_service_task", fail_after_worker_start
    )

    with pytest.raises(RuntimeError, match="liveness initialization failure"):
        LivenessMonitor(
            context.broker_target,
            _taskspec(str(time.time_ns()), workdir),
            config=context.config,
        )

    assert len(worker_threads) == LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES
    assert all(stop_event.is_set() for stop_event in worker_stop_events)
    assert all(not thread.is_alive() for thread in worker_threads)


@pytest.fixture
def namespace_case(
    workdir: Path,
) -> Iterator[tuple[WeftContext, LivenessMonitor, Queue, str, list[float]]]:
    """Real namespace and a deterministic monitor clock, without probe dispatch."""
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(tid), persistent=False)
    queue.write(json.dumps(_mapping(tid)))
    now = [10.0]
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
        monotonic_clock=lambda: now[0],
        mapping_min_age_seconds=0.0,
    )
    try:
        yield context, monitor, queue, tid, now
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()


def _taskspec(tid: str, root: Path) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="liveness-monitor",
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
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR
        },
    )


def _mapping(tid: str) -> dict[str, object]:
    return {
        "full": tid,
        "short": tid_short_form(tid),
        "terminal": False,
        "hostname": "test",
        "runtime_handle": {
            "runner": "missing",
            "kind": "supervised-process",
            "id": "runtime-1",
            "control": {"authority": "external-supervisor"},
            "observations": {},
            "metadata": {},
        },
    }


def test_reconcile_discovers_task_state_namespace(workdir: Path) -> None:
    """The custodian discovers per-task snapshots without the legacy queue."""
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(tid), persistent=False)
    message_id = queue.write(json.dumps(_mapping(tid)))
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
    )
    try:
        monitor._reconcile_mapping_rows(full=True)
        assert tid in monitor._latest_rows
        assert monitor._latest_rows[tid].message_id == message_id
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()


def test_full_reconcile_keeps_newest_row_and_exact_deletes_superseded(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    target_tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(target_tid), persistent=False)
    old_id = queue.write(json.dumps(_mapping(target_tid)))
    newest_id = queue.write(json.dumps(_mapping(target_tid)))
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
        mapping_min_age_seconds=0.0,
    )
    try:
        monitor._reconcile_mapping_rows(full=True)
        remaining_ids = {
            timestamp for _body, timestamp in queue.peek_generator(with_timestamps=True)
        }
        assert old_id not in remaining_ids
        assert newest_id in remaining_ids
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()


def test_full_reconcile_does_not_skip_rows_while_retiring_paginated_history(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    # This test retains one handle while seeding and inspecting 1,200 rows.
    target_tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(target_tid), persistent=True)
    message_ids = [
        queue.write(json.dumps(_mapping(target_tid))) for _index in range(1_200)
    ]
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
        mapping_min_age_seconds=0.0,
    )
    try:
        monitor._reconcile_mapping_rows(full=True)

        remaining_target_ids = [
            timestamp
            for body, timestamp in queue.peek_generator(with_timestamps=True)
            if json.loads(body).get("full") == target_tid
        ]
        assert remaining_target_ids == [message_ids[-1]]
        assert monitor._latest_rows[target_tid].message_id == message_ids[-1]
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()


def test_unknown_timeout_deletes_only_exact_mapping_after_completed_probe(
    workdir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    context = build_context(spec_context=workdir)
    unrelated = context.queue("unrelated", persistent=False)
    target_tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(target_tid), persistent=False)
    message_id = queue.write(json.dumps(_mapping(target_tid)))
    unrelated.write("keep")
    now = [10.0]
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
        monotonic_clock=lambda: now[0],
        unknown_timeout_seconds=5.0,
        mapping_min_age_seconds=0.0,
    )
    try:
        caplog.set_level("INFO", logger=liveness_monitor_mod.__name__)
        monitor._reconcile_mapping_rows(full=True)
        row = monitor._latest_rows[target_tid]
        work = ProbeWork(
            target_tid, row.message_id, row.payload, row.generation, "token"
        )
        unknown = LivenessObservation(
            target_tid, "unknown", "miss", True, row.generation
        )

        monitor._in_flight[target_tid] = work
        monitor._apply_probe_result(ProbeResult(work, unknown, attempted=True))
        assert message_id in {
            timestamp for _body, timestamp in queue.peek_generator(with_timestamps=True)
        }

        now[0] = 15.0
        monitor._in_flight[target_tid] = work
        monitor._apply_probe_result(ProbeResult(work, unknown, attempted=True))
        assert message_id not in {
            timestamp for _body, timestamp in queue.peek_generator(with_timestamps=True)
        }
        assert unrelated.peek_one() == "keep"
        retirement_records = [
            record
            for record in caplog.records
            if record.getMessage() == "Retired TID mapping after liveness probe"
        ]
        assert len(retirement_records) == 1
        assert retirement_records[0].__dict__["tid"] == target_tid
        assert retirement_records[0].__dict__["hostname"] == "test"
        assert retirement_records[0].__dict__["reason"] == "unknown_timeout"
        assert retirement_records[0].__dict__["probe_reason"] == "miss"
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()
        unrelated.close()


def test_not_attempted_probe_pauses_unknown_deadline(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    target_tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(target_tid), persistent=False)
    message_id = queue.write(json.dumps(_mapping(target_tid)))
    now = [10.0]
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
        monotonic_clock=lambda: now[0],
        unknown_timeout_seconds=5.0,
        mapping_min_age_seconds=0.0,
    )
    try:
        monitor._reconcile_mapping_rows(full=True)
        row = monitor._latest_rows[target_tid]
        work = ProbeWork(
            target_tid, row.message_id, row.payload, row.generation, "token"
        )
        unknown = LivenessObservation(
            target_tid, "unknown", "miss", True, row.generation
        )
        monitor._in_flight[target_tid] = work
        monitor._apply_probe_result(ProbeResult(work, unknown, attempted=True))

        now[0] = 12.0
        monitor._in_flight[target_tid] = work
        monitor._apply_probe_result(ProbeResult(work, None, attempted=False))
        now[0] = 20.0
        monitor._in_flight[target_tid] = work
        monitor._apply_probe_result(ProbeResult(work, unknown, attempted=True))
        assert message_id in {
            timestamp for _body, timestamp in queue.peek_generator(with_timestamps=True)
        }

        now[0] = 23.0
        monitor._in_flight[target_tid] = work
        monitor._apply_probe_result(ProbeResult(work, unknown, attempted=True))
        assert message_id not in {
            timestamp for _body, timestamp in queue.peek_generator(with_timestamps=True)
        }
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()


def test_restart_resets_unknown_deadline(workdir: Path) -> None:
    """A replacement monitor does not inherit an expired in-memory deadline."""

    context = build_context(spec_context=workdir)
    target_tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(target_tid), persistent=False)
    message_id = queue.write(json.dumps(_mapping(target_tid)))
    now = [10.0]
    first = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
        monotonic_clock=lambda: now[0],
        unknown_timeout_seconds=5.0,
        mapping_min_age_seconds=0.0,
    )
    try:
        first._reconcile_mapping_rows(full=True)
        row = first._latest_rows[target_tid]
        work = ProbeWork(
            target_tid, row.message_id, row.payload, row.generation, "first"
        )
        unknown = LivenessObservation(
            target_tid,
            "unknown",
            "miss",
            True,
            row.generation,
        )
        first._in_flight[target_tid] = work
        first._apply_probe_result(ProbeResult(work, unknown, attempted=True))
    finally:
        first.stop(join=False)
        first.cleanup()

    now[0] = 20.0
    replacement = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
        monotonic_clock=lambda: now[0],
        unknown_timeout_seconds=5.0,
        mapping_min_age_seconds=0.0,
    )
    try:
        replacement._reconcile_mapping_rows(full=True)
        row = replacement._latest_rows[target_tid]
        work = ProbeWork(
            target_tid,
            row.message_id,
            row.payload,
            row.generation,
            "replacement",
        )
        unknown = LivenessObservation(
            target_tid,
            "unknown",
            "miss",
            True,
            row.generation,
        )
        replacement._in_flight[target_tid] = work
        replacement._apply_probe_result(ProbeResult(work, unknown, attempted=True))

        assert message_id in {
            timestamp for _body, timestamp in queue.peek_generator(with_timestamps=True)
        }
    finally:
        replacement.stop(join=False)
        replacement.cleanup()
        queue.close()


def test_probe_worker_contains_one_ordinary_failure_and_processes_next_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _mapping("1778000000000000001")
    payload.pop("hostname")
    first = ProbeWork("1778000000000000001", 1, payload, "generation", "first")
    second = ProbeWork("1778000000000000002", 2, payload, "generation", "second")
    calls = 0

    def analyze(*_args: object, **_kwargs: object) -> LivenessObservation:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected adapter defect")
        return LivenessObservation(
            second.tid,
            "live",
            "extension_live",
            True,
            second.generation,
        )

    events: list[ServiceWorkerEvent] = []

    class Context:
        def iter_items(self) -> object:
            return iter((first, second))

        def publish_event(
            self,
            kind: str,
            value: object,
            *,
            item_id: str | None = None,
        ) -> None:
            events.append(
                ServiceWorkerEvent(
                    name="liveness.probes",
                    request_id="request",
                    worker_index=0,
                    kind=kind,
                    value=value,
                    item_id=item_id,
                )
            )

    monkeypatch.setattr(
        liveness_monitor_mod, "get_runner_plugin", lambda _name: object()
    )
    monkeypatch.setattr(liveness_monitor_mod, "analyze_liveness", analyze)

    LivenessMonitor._run_probe_worker(Context())  # type: ignore[arg-type]

    assert [event.item_id for event in events] == ["first", "second"]
    first_result = events[0].value
    second_result = events[1].value
    assert isinstance(first_result, ProbeResult)
    assert first_result.attempted is False
    assert first_result.diagnostic == "probe_internal_error:RuntimeError"
    assert isinstance(second_result, ProbeResult)
    assert second_result.attempted is True
    assert second_result.observation is not None
    assert second_result.observation.evidence == "live"


def test_probe_worker_propagates_fatal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FatalProbeFailure(BaseException):
        pass

    failure = FatalProbeFailure("fatal")
    payload = _mapping("1778000000000000001")
    payload.pop("hostname")
    work = ProbeWork("1778000000000000001", 1, payload, "generation", "token")

    class Context:
        def iter_items(self) -> object:
            return iter((work,))

        def publish_event(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("fatal probe must not publish a result")

    def fail(*_args: object, **_kwargs: object) -> LivenessObservation:
        raise failure

    monkeypatch.setattr(
        liveness_monitor_mod, "get_runner_plugin", lambda _name: object()
    )
    monkeypatch.setattr(liveness_monitor_mod, "analyze_liveness", fail)

    with pytest.raises(FatalProbeFailure) as exc_info:
        LivenessMonitor._run_probe_worker(Context())  # type: ignore[arg-type]

    assert exc_info.value is failure


def test_probe_worker_fatal_event_escalates_on_reactor(
    workdir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fatal lane collapse is visible and terminates the monitor reactor."""

    class FatalProbeFailure(BaseException):
        pass

    failure = FatalProbeFailure("fatal")
    context = build_context(spec_context=workdir)
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
    )
    event = ServiceWorkerEvent(
        name="liveness.probes",
        request_id="request",
        worker_index=3,
        kind="error",
        error=failure,
    )
    try:
        caplog.set_level("CRITICAL", logger=liveness_monitor_mod.__name__)
        with pytest.raises(FatalProbeFailure) as exc_info:
            monitor._handle_service_worker_event(event)

        assert exc_info.value is failure
        failure_records = [
            record
            for record in caplog.records
            if record.getMessage() == "Liveness probe worker failed"
        ]
        assert len(failure_records) == 1
        assert failure_records[0].__dict__["worker_index"] == 3
        assert failure_records[0].__dict__["error_type"] == "FatalProbeFailure"
    finally:
        monitor.stop(join=False)
        monitor.cleanup()


def test_probe_worker_reports_foreign_host_as_attempted_unknown() -> None:
    payload = _mapping("1778000000000000001")
    payload["hostname"] = f"foreign-{socket.gethostname()}"
    work = ProbeWork("1778000000000000001", 1, payload, "generation", "token")
    results: list[ProbeResult] = []

    class Context:
        def iter_items(self) -> object:
            return iter((work,))

        def publish_event(
            self,
            _kind: str,
            value: object,
            *,
            item_id: str | None = None,
        ) -> None:
            assert item_id == "token"
            assert isinstance(value, ProbeResult)
            results.append(value)

    LivenessMonitor._run_probe_worker(Context())  # type: ignore[arg-type]

    assert len(results) == 1
    assert results[0].attempted is True
    assert results[0].observation is not None
    assert results[0].observation.evidence == "unknown"
    assert results[0].observation.reason.startswith("foreign_hostname:")


def test_probe_worker_marks_plugin_load_failure_not_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _mapping("1778000000000000001")
    payload.pop("hostname")
    work = ProbeWork("1778000000000000001", 1, payload, "generation", "token")
    results: list[ProbeResult] = []

    class Context:
        def iter_items(self) -> object:
            return iter((work,))

        def publish_event(
            self,
            _kind: str,
            value: object,
            **_kwargs: object,
        ) -> None:
            assert isinstance(value, ProbeResult)
            results.append(value)

    monkeypatch.setattr(
        liveness_monitor_mod,
        "get_runner_plugin",
        lambda _name: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )

    LivenessMonitor._run_probe_worker(Context())  # type: ignore[arg-type]

    assert results == [ProbeResult(work, None, False, "provider_load_failed:missing")]


def test_probe_worker_treats_invalid_handle_as_attempted_unknown() -> None:
    payload = _mapping("1778000000000000001")
    payload.pop("hostname")
    payload["runtime_handle"] = {"runner": "invalid"}
    work = ProbeWork("1778000000000000001", 1, payload, "generation", "token")
    results: list[ProbeResult] = []

    class Context:
        def iter_items(self) -> object:
            return iter((work,))

        def publish_event(
            self,
            _kind: str,
            value: object,
            **_kwargs: object,
        ) -> None:
            assert isinstance(value, ProbeResult)
            results.append(value)

    LivenessMonitor._run_probe_worker(Context())  # type: ignore[arg-type]

    assert len(results) == 1
    assert results[0].attempted is True
    assert results[0].observation is not None
    assert results[0].observation.evidence == "unknown"
    assert results[0].observation.reason == "missing_or_invalid_runtime_handle"


def test_probe_worker_discards_observation_when_budget_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _mapping("1778000000000000001")
    payload.pop("hostname")
    work = ProbeWork("1778000000000000001", 1, payload, "generation", "token")
    results: list[ProbeResult] = []
    clock = iter((10.0, 13.0))

    class Context:
        def iter_items(self) -> object:
            return iter((work,))

        def publish_event(
            self,
            _kind: str,
            value: object,
            **_kwargs: object,
        ) -> None:
            assert isinstance(value, ProbeResult)
            results.append(value)

    monkeypatch.setattr(
        liveness_monitor_mod, "get_runner_plugin", lambda _name: object()
    )
    monkeypatch.setattr(
        liveness_monitor_mod,
        "analyze_liveness",
        lambda *_args, **_kwargs: LivenessObservation(
            work.tid,
            "live",
            "extension_live",
            True,
            work.generation,
        ),
    )
    monkeypatch.setattr(liveness_monitor_mod.time, "monotonic", lambda: next(clock))

    LivenessMonitor._run_probe_worker(Context())  # type: ignore[arg-type]

    assert results == [ProbeResult(work, None, False, "probe_budget_expired")]


def test_lane_saturation_pauses_existing_unknown_deadline(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(tid), persistent=False)
    queue.write(json.dumps(_mapping(tid)))
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
        monotonic_clock=lambda: 12.0,
    )
    try:
        monitor._reconcile_mapping_rows(full=True)
        row = monitor._latest_rows[tid]
        monitor._deadlines[tid] = UnknownDeadlineState(row.generation, 15.0, None)
        monitor._in_flight.update(
            {
                f"occupied-{index}": ProbeWork(
                    f"occupied-{index}",
                    index,
                    {},
                    "generation",
                    f"token-{index}",
                )
                for index in range(LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES)
            }
        )

        monitor._schedule_due_probes(now=12.0)

        assert monitor._deadlines[tid] == UnknownDeadlineState(
            row.generation,
            15.0,
            12.0,
        )
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()


def test_late_probe_result_requires_current_token_and_generation(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(tid), persistent=False)
    queue.write(json.dumps(_mapping(tid)))
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
    )
    try:
        monitor._reconcile_mapping_rows(full=True)
        row = monitor._latest_rows[tid]
        current = ProbeWork(tid, row.message_id, row.payload, row.generation, "current")
        monitor._in_flight[tid] = current
        stale_token = ProbeWork(
            tid,
            row.message_id,
            row.payload,
            row.generation,
            "stale",
        )
        observation = LivenessObservation(
            tid,
            "unknown",
            "extension_unknown",
            True,
            row.generation,
        )

        monitor._apply_probe_result(ProbeResult(stale_token, observation, True))
        assert tid not in monitor._deadlines
        assert monitor._in_flight[tid] == current

        stale_generation = ProbeWork(
            tid,
            row.message_id,
            row.payload,
            "old-generation",
            "current",
        )
        monitor._apply_probe_result(ProbeResult(stale_generation, observation, True))
        assert tid not in monitor._deadlines
        assert monitor._in_flight[tid] == current

        monitor._apply_probe_result(ProbeResult(current, observation, True))
        accepted_deadline = monitor._deadlines[tid]
        due_count = sum(entry[1] == tid for entry in monitor._due_heap)
        assert tid not in monitor._in_flight

        monitor._apply_probe_result(ProbeResult(current, observation, True))
        assert monitor._deadlines[tid] == accepted_deadline
        assert sum(entry[1] == tid for entry in monitor._due_heap) == due_count
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()


def test_full_reconcile_preserves_unchanged_deadline_and_in_flight_probe(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(tid), persistent=False)
    queue.write(json.dumps(_mapping(tid)))
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
    )
    try:
        monitor._reconcile_mapping_rows(full=True)
        row = monitor._latest_rows[tid]
        deadline = UnknownDeadlineState(row.generation, 20.0, None)
        in_flight = ProbeWork(
            tid,
            row.message_id,
            row.payload,
            row.generation,
            "token",
        )
        monitor._deadlines[tid] = deadline
        monitor._in_flight[tid] = in_flight
        due_count = sum(entry[1] == tid for entry in monitor._due_heap)

        monitor._reconcile_mapping_rows(full=True)

        assert monitor._latest_rows[tid] == row
        assert monitor._deadlines[tid] == deadline
        assert monitor._in_flight[tid] == in_flight
        assert sum(entry[1] == tid for entry in monitor._due_heap) == due_count
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()


def test_same_generation_mapping_appends_replace_due_entry(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(tid), persistent=False)
    payload = _mapping(tid)
    queue.write(json.dumps(payload))
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
    )
    try:
        monitor._reconcile_mapping_rows(full=True)
        initial_due_count = len(monitor._due_heap)
        assert sum(entry[1] == tid for entry in monitor._due_heap) == 1

        newest_id = queue.write(json.dumps(payload))
        monitor._reconcile_mapping_rows(full=False)

        assert monitor._latest_rows[tid].message_id == newest_id
        assert len(monitor._due_heap) == initial_due_count
        target_entries = [entry for entry in monitor._due_heap if entry[1] == tid]
        assert len(target_entries) == 1
        assert target_entries[0][1:] == (tid, monitor._latest_rows[tid].generation)
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()


def test_mapping_update_keeps_probe_lane_owned_until_old_result_finishes(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    queue = context.queue(task_state_queue_name(tid), persistent=False)
    payload = _mapping(tid)
    queue.write(json.dumps(payload))
    monitor = LivenessMonitor(
        context.broker_target,
        _taskspec(str(time.time_ns()), workdir),
        monotonic_clock=lambda: 10.0,
    )
    try:
        monitor._reconcile_mapping_rows(full=True)
        original = monitor._latest_rows[tid]
        in_flight = ProbeWork(
            tid,
            original.message_id,
            original.payload,
            original.generation,
            "old-token",
        )
        monitor._in_flight[tid] = in_flight

        payload["activity"] = "busy"
        newest_id = queue.write(json.dumps(payload))
        monitor._reconcile_mapping_rows(full=False)
        monitor._schedule_due_probes(now=10.0)

        assert monitor._latest_rows[tid].message_id == newest_id
        assert monitor._in_flight[tid] == in_flight

        observation = LivenessObservation(
            tid,
            "live",
            "host_identity_match",
            True,
            original.generation,
        )
        monitor._apply_probe_result(ProbeResult(in_flight, observation, True))

        assert tid not in monitor._in_flight
        assert sum(entry[1] == tid for entry in monitor._due_heap) == 1

        enqueued: list[ProbeWork] = []

        def enqueue(_name: str, item: object, *, block: bool) -> bool:
            assert block is False
            assert isinstance(item, ProbeWork)
            enqueued.append(item)
            return True

        monkeypatch.setattr(monitor, "_enqueue_service_work", enqueue)
        monitor._schedule_due_probes(now=10.0)

        assert len(enqueued) == 1
        assert enqueued[0].message_id == newest_id
        assert monitor._in_flight[tid] == enqueued[0]
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()


def _complete_stale_probe(monitor: LivenessMonitor, tid: str) -> None:
    """Deliver a completed read-only stale observation for the current row."""
    row = monitor._latest_rows[tid]
    work = ProbeWork(tid, row.message_id, row.payload, row.generation, "stale-token")
    monitor._in_flight[tid] = work
    observation = LivenessObservation(tid, "stale", "gone", True, row.generation)
    monitor._apply_probe_result(ProbeResult(work, observation, True))


def test_namespace_refresh_is_separate_from_fast_reactor_turns(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, monitor, queue, tid, now = namespace_case
    refreshes: list[float] = []
    original = liveness_monitor_mod.list_task_state_tids

    def counted(ctx: WeftContext, *, broker: Any = None) -> list[str]:
        refreshes.append(now[0])
        return original(ctx, broker=broker)

    monkeypatch.setattr(liveness_monitor_mod, "list_task_state_tids", counted)
    monitor._process_reactor_turn()
    initial_id = monitor._latest_rows[tid].message_id
    latest_id = queue.write(json.dumps({**_mapping(tid), "activity": "busy"}))
    new_tid = str(time.time_ns())
    newcomer = context.queue(task_state_queue_name(new_tid), persistent=False)
    try:
        newcomer.write(json.dumps(_mapping(new_tid)))
        for _ in range(20):
            now[0] += 0.05
            monitor._process_reactor_turn()
        assert refreshes == [10.0]
        assert monitor._latest_rows[tid].message_id == initial_id
        assert new_tid not in monitor._latest_rows
        now[0] = 15.0
        monitor._process_reactor_turn()
        assert refreshes == [10.0, 15.0]
        assert monitor._latest_rows[tid].message_id == latest_id
        assert new_tid in monitor._latest_rows
    finally:
        newcomer.close()


def test_namespace_sampling_coalesces_unseen_generation_round_trip(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
) -> None:
    _context, monitor, queue, tid, _now = namespace_case
    monitor._reconcile_mapping_rows(full=True)
    current = monitor._latest_rows[tid]
    deadline = UnknownDeadlineState(current.generation, 100.0, None)
    monitor._deadlines[tid] = deadline
    changed = _mapping(tid)
    changed["runtime_handle"] = {
        **dict(current.payload["runtime_handle"]),
        "id": "different-generation",
    }
    queue.write(json.dumps(changed))
    newest_id = queue.write(json.dumps(_mapping(tid)))
    monitor._reconcile_mapping_rows(full=False)
    assert monitor._latest_rows[tid].message_id == newest_id
    assert monitor._deadlines[tid] == deadline


def test_namespace_disappearance_keeps_in_flight_lane_owned(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
) -> None:
    _context, monitor, queue, tid, _now = namespace_case
    monitor._reconcile_mapping_rows(full=True)
    row = monitor._latest_rows[tid]
    work = ProbeWork(tid, row.message_id, row.payload, row.generation, "pending")
    monitor._in_flight[tid] = work
    monitor._deadlines[tid] = UnknownDeadlineState(row.generation, 100.0, None)
    queue.delete(message_id=row.message_id)
    monitor._reconcile_mapping_rows(full=False)
    assert tid not in monitor._latest_rows
    assert tid not in monitor._deadlines
    assert tid not in monitor._due_tids
    assert monitor._in_flight[tid] == work


@pytest.mark.parametrize("full", [False, True])
def test_namespace_read_failure_retains_previous_evidence(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
    monkeypatch: pytest.MonkeyPatch,
    full: bool,
) -> None:
    _context, monitor, _queue, tid, _now = namespace_case
    monitor._reconcile_mapping_rows(full=True)
    row = monitor._latest_rows[tid]
    deadline = UnknownDeadlineState(row.generation, 100.0, None)
    monitor._deadlines[tid] = deadline
    if full:
        original_history = monitor._read_mapping_history

        def failed_history(selected: str, *, broker: Any) -> Any:
            if selected == tid:
                raise OSError("injected read failure")
            return original_history(selected, broker=broker)

        monkeypatch.setattr(monitor, "_read_mapping_history", failed_history)
    else:
        original_snapshot = liveness_monitor_mod.read_task_state_snapshot

        def failed_snapshot(ctx: WeftContext, selected: str, **kwargs: Any) -> Any:
            if selected == tid:
                raise OSError("injected read failure")
            return original_snapshot(ctx, selected, **kwargs)

        monkeypatch.setattr(
            liveness_monitor_mod, "read_task_state_snapshot", failed_snapshot
        )
    monitor._reconcile_mapping_rows(full=full)
    assert monitor._latest_rows[tid] == row
    assert monitor._deadlines[tid] == deadline


def test_namespace_listing_failure_is_not_disappearance(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context, monitor, _queue, tid, _now = namespace_case
    monitor._reconcile_mapping_rows(full=True)
    row = monitor._latest_rows[tid]

    def failed_listing(*args: Any, **kwargs: Any) -> list[str]:
        raise OSError("injected listing failure")

    monkeypatch.setattr(liveness_monitor_mod, "list_task_state_tids", failed_listing)
    monitor._reconcile_mapping_rows(full=True)
    assert monitor._latest_rows[tid] == row
    assert tid in monitor._due_tids


def test_retirement_rejects_newer_unobserved_publication(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
) -> None:
    _context, monitor, queue, tid, _now = namespace_case
    monitor._reconcile_mapping_rows(full=False)
    newest_id = queue.write(json.dumps({**_mapping(tid), "activity": "new"}))
    _complete_stale_probe(monitor, tid)
    assert monitor._latest_rows[tid].message_id == newest_id
    assert queue.peek_one(exact_timestamp=newest_id) is not None
    assert tid in monitor._due_tids


def test_retirement_rejects_missing_probed_row_and_adopts_older_current(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
) -> None:
    _context, monitor, queue, tid, _now = namespace_case
    old_row = queue.peek_one(with_timestamps=True)
    assert old_row is not None
    old_id = old_row[1]
    latest_id = queue.write(json.dumps({**_mapping(tid), "terminal": True}))
    monitor._reconcile_mapping_rows(full=False)
    queue.delete(message_id=latest_id)
    _complete_stale_probe(monitor, tid)
    assert monitor._latest_rows[tid].message_id == old_id
    assert queue.peek_one(exact_timestamp=old_id) is not None


def test_retirement_blocks_latest_when_older_exact_delete_fails(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context, monitor, queue, tid, _now = namespace_case
    old_row = queue.peek_one(with_timestamps=True)
    assert old_row is not None
    old_id = old_row[1]
    latest_id = queue.write(json.dumps({**_mapping(tid), "terminal": True}))
    monitor._reconcile_mapping_rows(full=False)
    original = monitor._delete_mapping_message
    attempted: list[int] = []

    def fail_older(selected: str, message_id: int, *, broker: Any) -> bool:
        attempted.append(message_id)
        if message_id == old_id:
            return False
        return original(selected, message_id, broker=broker)

    with monkeypatch.context() as patch:
        patch.setattr(monitor, "_delete_mapping_message", fail_older)
        _complete_stale_probe(monitor, tid)
    assert attempted == [old_id]
    assert queue.peek_one(exact_timestamp=latest_id) is not None
    _complete_stale_probe(monitor, tid)
    assert queue.peek_one() is None
    assert tid not in monitor._latest_rows


@pytest.mark.parametrize("reported_deleted", [0, 1])
def test_retirement_requires_observed_absence_before_deleting_latest(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
    monkeypatch: pytest.MonkeyPatch,
    reported_deleted: int,
) -> None:
    """A broker return count cannot substitute for verifying older-row absence."""
    context, monitor, queue, tid, _now = namespace_case
    old_row = queue.peek_one(with_timestamps=True)
    assert old_row is not None
    old_id = old_row[1]
    latest_id = queue.write(json.dumps({**_mapping(tid), "terminal": True}))
    monitor._reconcile_mapping_rows(full=False)
    with context.broker() as broker:
        broker_type = type(broker)
        original_delete = broker_type.delete_message_ids
    attempted: list[int] = []

    def leave_older_present(self: Any, name: str, message_ids: list[int]) -> int:
        if name == task_state_queue_name(tid):
            attempted.extend(message_ids)
            if message_ids == [old_id]:
                return reported_deleted
        deleted = original_delete(self, name, message_ids)
        assert isinstance(deleted, int)
        return deleted

    with monkeypatch.context() as patch:
        patch.setattr(broker_type, "delete_message_ids", leave_older_present)
        _complete_stale_probe(monitor, tid)
    assert attempted == [old_id]
    assert queue.peek_one(exact_timestamp=old_id) is not None
    assert queue.peek_one(exact_timestamp=latest_id) is not None
    assert tid in monitor._latest_rows
    _complete_stale_probe(monitor, tid)
    assert queue.peek_one() is None
    assert tid not in monitor._latest_rows


def test_retirement_absence_read_failure_preserves_latest(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, monitor, queue, tid, _now = namespace_case
    old_row = queue.peek_one(with_timestamps=True)
    assert old_row is not None
    old_id = old_row[1]
    latest_id = queue.write(json.dumps({**_mapping(tid), "terminal": True}))
    monitor._reconcile_mapping_rows(full=False)
    with context.broker() as broker:
        broker_type = type(broker)
        original_peek = broker_type.peek_one

    def failed_verification(self: Any, name: str, **kwargs: Any) -> Any:
        if (
            name == task_state_queue_name(tid)
            and kwargs.get("exact_timestamp") == old_id
        ):
            raise OSError("injected absence verification failure")
        return original_peek(self, name, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(broker_type, "peek_one", failed_verification)
        _complete_stale_probe(monitor, tid)
    assert queue.peek_one(exact_timestamp=old_id) is None
    assert queue.peek_one(exact_timestamp=latest_id) is not None
    assert tid in monitor._latest_rows
    _complete_stale_probe(monitor, tid)
    assert queue.peek_one() is None
    assert tid not in monitor._latest_rows


def test_retirement_verified_missing_older_id_and_concurrent_append_survive(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context, monitor, queue, tid, _now = namespace_case
    old_row = queue.peek_one(with_timestamps=True)
    assert old_row is not None
    old_id = old_row[1]
    latest_id = queue.write(json.dumps({**_mapping(tid), "terminal": True}))
    monitor._reconcile_mapping_rows(full=False)
    original = monitor._delete_mapping_message
    appended: list[int] = []

    def concurrent_write(selected: str, message_id: int, *, broker: Any) -> bool:
        if message_id == old_id:
            queue.delete(message_id=old_id)
            appended.append(queue.write(json.dumps(_mapping(tid))))
        return original(selected, message_id, broker=broker)

    monkeypatch.setattr(monitor, "_delete_mapping_message", concurrent_write)
    _complete_stale_probe(monitor, tid)
    remaining = [mid for _body, mid in queue.peek_generator(with_timestamps=True)]
    assert remaining == appended
    assert latest_id not in remaining
    monitor._reconcile_mapping_rows(full=False)
    assert monitor._latest_rows[tid].message_id == appended[0]


def test_namespace_age_fences_preserve_young_history_and_terminal(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
) -> None:
    _context, monitor, queue, tid, _now = namespace_case
    monitor._mapping_min_age_seconds = 2400.0
    queue.write("malformed")
    queue.write(json.dumps({**_mapping(tid), "terminal": True}))
    initial = list(queue.peek_generator(with_timestamps=True))
    monitor._reconcile_mapping_rows(full=True)
    _complete_stale_probe(monitor, tid)
    assert list(queue.peek_generator(with_timestamps=True)) == initial
    assert tid in monitor._latest_rows


def test_namespace_history_handles_do_not_accumulate_in_task_cache(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
) -> None:
    context, monitor, _queue, _tid, _now = namespace_case
    retired_tids: list[str] = []
    for _ in range(10):
        tid = str(time.time_ns())
        queue = context.queue(task_state_queue_name(tid), persistent=False)
        try:
            queue.write(json.dumps({**_mapping(tid), "terminal": True}))
            monitor._reconcile_mapping_rows(full=False)
            _complete_stale_probe(monitor, tid)
            assert queue.peek_one() is None
            retired_tids.append(tid)
        finally:
            queue.close()
    for tid in retired_tids:
        assert task_state_queue_name(tid) not in monitor._queue_cache
        assert tid not in monitor._latest_rows
        assert tid not in monitor._due_tids


def test_retirement_read_failure_preserves_latest_and_retries(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context, monitor, queue, tid, _now = namespace_case
    monitor._reconcile_mapping_rows(full=False)
    row = monitor._latest_rows[tid]

    def failed_history(selected: str, *, broker: Any) -> Any:
        raise OSError("injected retirement history read failure")

    monkeypatch.setattr(monitor, "_read_mapping_history", failed_history)
    _complete_stale_probe(monitor, tid)
    assert monitor._latest_rows[tid] == row
    assert queue.peek_one(exact_timestamp=row.message_id) is not None
    assert tid in monitor._due_tids


def test_retirement_missing_queue_discards_unprobed_work(
    namespace_case: tuple[WeftContext, LivenessMonitor, Queue, str, list[float]],
) -> None:
    _context, monitor, queue, tid, _now = namespace_case
    monitor._reconcile_mapping_rows(full=False)
    queue.delete(message_id=monitor._latest_rows[tid].message_id)
    _complete_stale_probe(monitor, tid)
    assert tid not in monitor._latest_rows
    assert tid not in monitor._deadlines
    assert tid not in monitor._due_tids
    assert tid not in monitor._in_flight
