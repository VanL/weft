"""LivenessMonitor service and exact mapping-reaper tests."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest

import weft.core.tasks.liveness_monitor as liveness_monitor_mod
from weft._constants import (
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR,
    LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import build_context
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


def test_full_reconcile_keeps_newest_row_and_exact_deletes_superseded(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    target_tid = str(time.time_ns())
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
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    target_tid = str(time.time_ns())
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
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    unrelated = context.queue("unrelated", persistent=False)
    target_tid = str(time.time_ns())
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
        assert retirement_records[0].tid == target_tid
        assert retirement_records[0].hostname == "test"
        assert retirement_records[0].reason == "unknown_timeout"
        assert retirement_records[0].probe_reason == "miss"
    finally:
        monitor.stop(join=False)
        monitor.cleanup()
        queue.close()
        unrelated.close()


def test_not_attempted_probe_pauses_unknown_deadline(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    target_tid = str(time.time_ns())
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
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    target_tid = str(time.time_ns())
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
        assert failure_records[0].worker_index == 3
        assert failure_records[0].error_type == "FatalProbeFailure"
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
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    tid = str(time.time_ns())
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
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    tid = str(time.time_ns())
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
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    tid = str(time.time_ns())
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
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    tid = str(time.time_ns())
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
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    tid = str(time.time_ns())
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
