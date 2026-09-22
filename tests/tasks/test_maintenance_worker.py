"""Maintenance ownership and state-transfer regressions.

Spec: docs/specifications/07-System_Invariants.md [IMPL.11].
"""

from __future__ import annotations

import gc
import json
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, Literal

import pytest

import weft.core.monitor.task_monitor as task_monitor_mod
from simplebroker import BrokerSession, Queue
from tests.helpers.typing import BrokerEnv
from weft._constants import (
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    SERVICE_STATUS_ACTIVE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    load_config,
)
from weft.context import WeftContext
from weft.core.monitor.collation import update_from_task_log_payload
from weft.core.monitor.store import MonitorStore, open_monitor_store
from weft.core.monitor.task_monitor import TaskMonitor, make_task_monitor_taskspec
from weft.core.service_convergence import build_manager_service_payload
from weft.core.task_state import task_state_queue_name
from weft.core.tasks.base import BaseTask, TaskWorkerResult

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize("adapter", ["synchronous", "queued"])
@pytest.mark.parametrize("emission_fails", [False, True])
def test_external_collation_preserves_adapter_notification_edges(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    adapter: str,
    emission_fails: bool,
) -> None:
    """Sync collation refreshes cache; queued result application owns notifications."""

    db_path, make_queue = broker_env
    sink_path = tmp_path / "notification-edges.jsonl"
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999717"),
        observer=lambda _queue, _message, _timestamp: None,
        config=load_config(
            {
                "WEFT_TASK_MONITOR_MODE": (
                    "custom" if adapter == "synchronous" else "report_only"
                ),
                "WEFT_TASK_MONITOR_PROCESSOR": (
                    "tests.tasks.test_task_monitor:recording_processor"
                    if adapter == "synchronous"
                    else ""
                ),
                "WEFT_TASK_MONITOR_LOG_SINK": "none",
                "WEFT_TASK_MONITOR_MAINTENANCE_ENABLED": False,
                "WEFT_LOG_TASKS_EXTERNAL_ENABLED": True,
                "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
                "WEFT_LOG_TASKS_EXTERNAL_PATH": str(sink_path),
                "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
                "WEFT_MANAGER_SERVE_LOG_LEVEL": "info",
            }
        ),
    )
    completed_tid = "1778084345905438717"
    make_queue(WEFT_GLOBAL_LOG_QUEUE).write(
        json.dumps(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": completed_tid,
                "taskspec": {
                    "tid": completed_tid,
                    "version": "1.0",
                    "name": "external-notification-probe",
                    "state": {"status": "completed", "return_code": 0},
                },
            }
        )
    )
    task._deferred_task_log_pending = 7
    task._weft_config[MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY] = True
    task._deferred_task_log_last_error = "retained deferred status"
    task._deferred_task_log_last_flush_at = 1730000000000000001
    task._refresh_external_task_log_status()
    task._set_activity("waiting", waiting_on=task._queue_names["inbox"])
    state_queue = make_queue(task_state_queue_name(task.tid))
    prior_snapshots = state_queue.stats().total
    sink = task._external_task_log_sink
    assert sink is not None

    def fail_write(body_json: str, *, level: int) -> None:
        raise OSError("external collation write failed")

    if emission_fails:
        monkeypatch.setattr(sink._writer, "emit_text", fail_write)
    capsys.readouterr()
    try:
        now_ns = time.time_ns() + 1_000_000_000
        if adapter == "synchronous":
            task._run_monitor_store_cycle(
                now_ns=now_ns, task_log_owner="collated_store"
            )
        else:
            work = task_monitor_mod._TaskMonitorBuiltinCycleWork(
                inputs=task._capture_maintenance_inputs(),
                request_id="queued-notification-edges",
                now_ns=now_ns,
                task_log_owner="collated_store",
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(task._run_builtin_cycle_worker, work).result(
                    timeout=5.0
                )
            task._service_lane_work_items[
                task_monitor_mod.TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE
            ] = work
            task._handle_builtin_cycle_worker_result(
                TaskWorkerResult(
                    lane=task_monitor_mod.TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE,
                    value=result,
                )
            )
        status = task._external_task_log_status
        assert status.total_emitted == (0 if emission_fails else 1)
        assert status.total_blocked_deletions == (1 if emission_fails else 0)
        assert status.healthy is (not emission_fails)
        assert status.deferred_pending == task._deferred_task_log_pending == 7
        assert status.last_deferred_error == "retained deferred status"
        assert task._deferred_task_log_last_error == "retained deferred status"
        assert status.last_deferred_flush_at == 1730000000000000001
        log_rows = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
        health_rows = [
            row
            for row in log_rows
            if row["event"] == "task_monitor_external_log_health"
        ]
        assert (
            state_queue.stats().total - prior_snapshots,
            len(health_rows),
        ) == (
            1 if adapter == "queued" else 0,
            1 if adapter == "queued" and emission_fails else 0,
        )
        assert len(sink_path.read_text().splitlines()) == (0 if emission_fails else 1)
    finally:
        task.stop()


def test_diagnostic_defaults_belong_to_each_monitor_and_worker(
    broker_env: BrokerEnv,
) -> None:
    """Default mutable report containers do not join independently owned state."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999714"),
        observer=lambda _queue, _message, _timestamp: None,
    )
    try:
        inputs = task._capture_maintenance_inputs()
        first = task_monitor_mod.MaintenanceWorker(inputs)
        second = task_monitor_mod.MaintenanceWorker(inputs)
        first._scan_state.last_candidate_class_counts["worker-only"] = 3
        assert second._scan_state.last_candidate_class_counts == {}
        assert task._scan_state.last_candidate_class_counts == {}
        task._scan_state.last_candidate_class_counts["owner-only"] = 5
        assert first._scan_state.last_candidate_class_counts == {"worker-only": 3}
        assert second._scan_state.last_candidate_class_counts == {}
        assert first.capture_diagnostics() == task_monitor_mod._MaintenanceDiagnostics()
        assert (
            second.capture_diagnostics() == task_monitor_mod._MaintenanceDiagnostics()
        )
    finally:
        task.stop()


def test_captured_diagnostic_groups_detach_nested_worker_values(
    broker_env: BrokerEnv,
) -> None:
    """Later worker mutations cannot alter captured or owner-applied reports."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999715"),
        observer=lambda _queue, _message, _timestamp: None,
    )
    try:
        worker = task_monitor_mod.MaintenanceWorker(task._capture_maintenance_inputs())
        worker._scan_state = replace(
            worker._scan_state,
            last_candidate_class_counts={"terminal": 3},
            last_cleanup_queue_stats=(
                {"queue": "diagnostic-probe", "reason_counts": {"old": 3}},
            ),
            last_cleanup_policy_stats=(
                {"policy": "diagnostic-probe", "rows": [{"count": 3}]},
            ),
        )
        worker._scan_observed = True
        diagnostics = worker.capture_diagnostics()
        assert diagnostics.scan is not None
        worker._scan_state.last_candidate_class_counts["terminal"] = 9
        worker._scan_state.last_cleanup_queue_stats[0]["reason_counts"]["old"] = 9
        worker._scan_state.last_cleanup_policy_stats[0]["rows"][0]["count"] = 9
        assert diagnostics.scan.last_candidate_class_counts == {"terminal": 3}
        assert diagnostics.scan.last_cleanup_queue_stats[0]["reason_counts"] == {
            "old": 3
        }
        assert diagnostics.scan.last_cleanup_policy_stats[0]["rows"] == [{"count": 3}]
        task._apply_maintenance_diagnostics(diagnostics)
        worker._scan_state.last_candidate_class_counts["terminal"] = 11
        worker._scan_state.last_cleanup_queue_stats[0]["reason_counts"]["old"] = 11
        worker._scan_state.last_cleanup_policy_stats[0]["rows"][0]["count"] = 11
        assert task._scan_state.last_candidate_class_counts == {"terminal": 3}
        assert task._scan_state.last_cleanup_queue_stats[0]["reason_counts"] == {
            "old": 3
        }
        assert task._scan_state.last_cleanup_policy_stats[0]["rows"] == [{"count": 3}]
    finally:
        task.stop()


def test_cleanup_result_preserves_unrelated_fields_in_updated_groups(
    broker_env: BrokerEnv,
) -> None:
    """A real cleanup result replaces its fields without resetting collation history."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999716"),
        observer=lambda _queue, _message, _timestamp: None,
    )
    task._collation_state = replace(
        task._collation_state,
        last_collation_rows_processed=37,
        last_collation_tasks_updated=19,
        last_terminal_families_disposed=11,
        last_monitor_store_families_retired=13,
        last_control_families_processed=97,
    )
    retained_ingest = replace(
        task._store_state.last_retained_task_log_ingest, scanned=83
    )
    task._store_state = replace(
        task._store_state, last_retained_task_log_ingest=retained_ingest
    )
    try:
        work = task_monitor_mod._TaskControlCleanupWork(
            inputs=task._capture_maintenance_inputs(),
            request_id="partial-group-cleanup",
            now_ns=time.time_ns(),
            slice_kind="dead_tid",
            queue_discovery_due_monotonic=time.monotonic() + 3600.0,
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(
                task._run_terminal_control_cleanup_worker, work
            ).result(timeout=5.0)
        assert result.cleanup.success
        assert result.cleanup.next_slice_kind is None
        assert not result.close_errors
        task._service_lane_work_items[
            task_monitor_mod.TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE
        ] = work
        task._handle_control_cleanup_worker_result(
            TaskWorkerResult(
                lane=task_monitor_mod.TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE,
                value=result,
            )
        )
        assert task._collation_state.last_control_families_processed == 0
        assert task._collation_state.last_collation_rows_processed == 37
        assert task._collation_state.last_collation_tasks_updated == 19
        assert task._collation_state.last_terminal_families_disposed == 11
        assert task._collation_state.last_monitor_store_families_retired == 13
        assert task._store_state.last_retained_task_log_ingest == retained_ingest
    finally:
        task.stop()


def test_disabled_sink_refresh_preserves_owner_and_worker_status_rules(
    broker_env: BrokerEnv,
) -> None:
    """Disabled owner refresh clears facade health; worker retains its input seed."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999713"),
        observer=lambda _queue, _message, _timestamp: None,
        config=load_config({"WEFT_LOG_TASKS_EXTERNAL_ENABLED": False}),
    )
    try:
        assert task._external_task_log_sink is None
        task._apply_worker_external_task_log_status(
            replace(
                task._external_task_log_status,
                healthy=False,
                last_error="previous emission failure",
                last_emit_at=1730000000000000000,
                total_emitted=3,
                total_blocked_deletions=5,
                deferred_pending=7,
                last_deferred_error="pending deferred failure",
                last_deferred_flush_at=1730000000000000001,
            )
        )
        worker = task_monitor_mod.MaintenanceWorker(task._capture_maintenance_inputs())
        worker._refresh_external_task_log_status()
        worker_status = worker._external_task_log_status
        assert not worker_status.enabled
        assert worker_status.healthy is False
        assert worker_status.last_error == "previous emission failure"
        assert worker_status.last_emit_at == 1730000000000000000
        assert worker_status.total_emitted == 0
        assert worker_status.total_blocked_deletions == 0

        task._refresh_external_task_log_status()
        owner_status = task._external_task_log_status
        assert not owner_status.enabled
        assert owner_status.healthy is None
        assert owner_status.last_error is None
        assert owner_status.last_emit_at is None
        assert owner_status.total_emitted == 0
        assert owner_status.total_blocked_deletions == 0
        assert task._external_task_log_worker_total_emitted == 3
        assert task._external_task_log_worker_total_blocked_deletions == 5
        for status in (worker_status, owner_status):
            assert status.deferred_pending == 7
            assert status.last_deferred_error == "pending deferred failure"
            assert status.last_deferred_flush_at == 1730000000000000001
    finally:
        task.stop()


@pytest.mark.parametrize("acquisition_stage", ["session", "sink"])
def test_acquisition_failure_closes_only_previously_acquired_worker_resources(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    acquisition_stage: str,
) -> None:
    """The scope is active before the first session and sink acquisition."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999712"),
        observer=lambda _queue, _message, _timestamp: None,
        config=load_config(
            {
                "WEFT_LOG_TASKS_EXTERNAL_ENABLED": True,
                "WEFT_LOG_TASKS_EXTERNAL_PATH": str(tmp_path / "acquisition.jsonl"),
            }
        ),
    )
    owner_session = task._broker_session
    assert owner_session is not None
    owner_sink = task._external_task_log_sink
    assert owner_sink is not None
    real_session = WeftContext.session
    real_close = BrokerSession.close
    acquired: list[BrokerSession] = []
    closed: list[BrokerSession] = []

    def open_session(context: WeftContext) -> BrokerSession:
        if acquisition_stage == "session":
            raise RuntimeError("session acquisition failed")
        session = real_session(context)
        acquired.append(session)
        return session

    def fail_sink(*args: Any, **kwargs: Any) -> task_monitor_mod.ExternalTaskLogSink:
        raise RuntimeError("sink acquisition failed")

    def close_session(session: BrokerSession) -> None:
        real_close(session)
        if session is not owner_session:
            closed.append(session)

    monkeypatch.setattr(WeftContext, "session", open_session)
    monkeypatch.setattr(BrokerSession, "close", close_session)
    monkeypatch.setattr(task_monitor_mod, "ExternalTaskLogSink", fail_sink)
    try:
        with owner_session.connection() as owner_core:
            owner_core.list_queues()
        work = task_monitor_mod._TaskMonitorBuiltinCycleWork(
            inputs=task._capture_maintenance_inputs(),
            request_id="acquisition-failure",
            now_ns=time.time_ns(),
            task_log_owner="collated_store",
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(task._run_builtin_cycle_worker, work).result(
                timeout=5.0
            )
        assert len(acquired) == (1 if acquisition_stage == "sink" else 0)
        assert closed == acquired
        assert result.result.errors == (f"{acquisition_stage} acquisition failed",)
        assert result.diagnostics is None
        assert not result.runtime_cleanup_ready
        with owner_session.connection() as current_core:
            assert current_core is owner_core
            current_core.list_queues()
        assert task._external_task_log_sink is owner_sink
        owner_sink.probe()
        assert owner_sink.status().healthy
    finally:
        task.stop()


def test_fatal_store_setup_closes_the_acquired_store(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ownership starts at acquisition, before schema/checkpoint setup can fail."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999709"),
        observer=lambda _queue, _message, _timestamp: None,
    )
    inputs = task._capture_maintenance_inputs()
    setup_failure = KeyboardInterrupt("store schema setup interrupted")
    acquired: list[MonitorStore] = []
    closed: list[MonitorStore] = []
    real_ensure_schema = MonitorStore.ensure_schema
    real_close = MonitorStore.close

    def fail_schema(store: MonitorStore) -> None:
        acquired.append(store)
        real_ensure_schema(store)
        raise setup_failure

    def record_close(store: MonitorStore) -> None:
        real_close(store)
        closed.append(store)

    monkeypatch.setattr(MonitorStore, "ensure_schema", fail_schema)
    monkeypatch.setattr(MonitorStore, "close", record_close)

    def run() -> None:
        with task_monitor_mod._maintenance_worker_scope(inputs, []) as worker:
            worker._ensure_monitor_store()

    try:
        with (
            ThreadPoolExecutor(max_workers=1) as executor,
            pytest.raises(KeyboardInterrupt) as raised,
        ):
            executor.submit(run).result(timeout=5.0)
        assert raised.value is setup_failure
        assert len(acquired) == 1
        assert closed == acquired
    finally:
        task.stop()


def test_store_setup_close_failure_prevents_diagnostic_application(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed close during setup participates in the outer result safety gate."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999710"),
        observer=lambda _queue, _message, _timestamp: None,
    )
    real_ensure_schema = MonitorStore.ensure_schema
    real_close = MonitorStore.close
    closes: list[MonitorStore] = []

    def fail_schema(store: MonitorStore) -> None:
        real_ensure_schema(store)
        raise RuntimeError("store schema setup failed")

    def fail_close(store: MonitorStore) -> None:
        real_close(store)
        closes.append(store)
        raise RuntimeError("store setup close failed")

    monkeypatch.setattr(MonitorStore, "ensure_schema", fail_schema)
    monkeypatch.setattr(MonitorStore, "close", fail_close)
    task._collation_state = replace(
        task._collation_state, last_collation_rows_processed=97
    )
    initial_status = task._store_state.monitor_store_status
    try:
        work = task_monitor_mod._TaskMonitorBuiltinCycleWork(
            inputs=task._capture_maintenance_inputs(),
            request_id="setup-close-failure",
            now_ns=time.time_ns(),
            task_log_owner="collated_store",
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(task._run_builtin_cycle_worker, work).result(
                timeout=5.0
            )
        assert len(closes) == 1
        task._service_lane_work_items[
            task_monitor_mod.TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE
        ] = work
        task._handle_builtin_cycle_worker_result(
            TaskWorkerResult(
                lane=task_monitor_mod.TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE,
                value=result,
            )
        )
        assert result.close_errors
        assert "store setup close failed" in " ".join(result.close_errors)
        assert task._collation_state.last_collation_rows_processed == 97
        assert task._store_state.monitor_store_status == initial_status
        assert not result.runtime_cleanup_ready
        assert task._control_cleanup_work_in_flight is None
    finally:
        task.stop()


@pytest.mark.parametrize("fatal_store_close", [False, True])
def test_fatal_body_retains_all_cleanup_failures_as_notes(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    fatal_store_close: bool,
) -> None:
    """Fatal unwind preserves the body exception and every later close failure."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999708"),
        observer=lambda _queue, _message, _timestamp: None,
    )
    inputs = task._capture_maintenance_inputs()
    owner_session = task._broker_session
    assert owner_session is not None
    body_failure = KeyboardInterrupt("fatal maintenance body")
    close_failure = SystemExit("fatal store close")
    events: list[str] = []
    real_store_close = MonitorStore.close
    real_session_close = BrokerSession.close
    real_queue_close = Queue.close

    def close_store(store: MonitorStore) -> None:
        real_store_close(store)
        events.append("store")
        if fatal_store_close:
            raise close_failure

    def close_session(session: BrokerSession) -> None:
        real_session_close(session)
        if session is not owner_session:
            events.append("session")
            raise RuntimeError("ordinary close failure")

    def close_queue(queue: Queue) -> None:
        real_queue_close(queue)
        if queue.name == "fatal-cleanup-probe":
            events.append("queue")

    monkeypatch.setattr(MonitorStore, "close", close_store)
    monkeypatch.setattr(BrokerSession, "close", close_session)
    monkeypatch.setattr(Queue, "close", close_queue)

    def fatal_operation() -> None:
        with task_monitor_mod._maintenance_worker_scope(inputs, []) as worker:
            assert worker._ensure_monitor_store() is not None
            worker._queue("fatal-cleanup-probe").write("real worker resource")
            raise body_failure

    try:
        with owner_session.connection() as owner_core:
            owner_core.list_queues()
        with (
            ThreadPoolExecutor(max_workers=1) as executor,
            pytest.raises(KeyboardInterrupt) as raised,
        ):
            executor.submit(fatal_operation).result(timeout=5.0)
        assert raised.value is body_failure
        assert events[0] == "store"
        assert "queue" in events
        assert events[-1] == "session"
        with owner_session.connection() as surviving_core:
            assert surviving_core is owner_core
            surviving_core.list_queues()
        notes = "\n".join(getattr(raised.value, "__notes__", ()))
        assert "ordinary close failure" in notes
        if fatal_store_close:
            assert "fatal store close" in notes
    finally:
        task.stop()


@pytest.mark.parametrize("after_body", [False, True])
def test_custom_collation_body_failure_reports_unavailable_store(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    after_body: bool,
) -> None:
    """Unexpected body errors cannot disappear behind empty or partial groups."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999707"),
        observer=lambda _queue, _message, _timestamp: None,
        config=load_config(
            {
                "WEFT_TASK_MONITOR_MODE": "custom",
                "WEFT_TASK_MONITOR_PROCESSOR": (
                    "tests.tasks.test_task_monitor:recording_processor"
                ),
            }
        ),
    )
    real_run = task_monitor_mod.MaintenanceWorker.run_collation

    def fail_collation(
        worker: task_monitor_mod.MaintenanceWorker,
        work: task_monitor_mod._TaskMonitorCollationWork,
    ) -> bool:
        if after_body:
            real_run(worker, work)
        raise RuntimeError("collation body boom")

    monkeypatch.setattr(
        task_monitor_mod.MaintenanceWorker, "run_collation", fail_collation
    )
    scans: list[int] = []
    real_scan = task._scan_task_log_candidates

    def record_scan() -> tuple[
        tuple[task_monitor_mod.TaskMonitorCandidate, ...], int | None, int
    ]:
        result = real_scan()
        scans.append(result[2])
        return result

    monkeypatch.setattr(task, "_scan_task_log_candidates", record_scan)
    try:
        assert not task._run_monitor_store_cycle(
            now_ns=time.time_ns(), task_log_owner="collated_store"
        )
        assert task._store_state.last_collation_store_error == "collation body boom"
        assert not task._store_state.monitor_store_status.available
        assert task._store_state.monitor_store_status.error == "collation body boom"
        assert task._control_cleanup_work_in_flight is None
        task._run_monitor_cycle()
        assert len(scans) == 1
    finally:
        task.stop()


def test_custom_close_failure_preserves_groups_and_continues_scanning(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed close blocks produced groups, records failure, and keeps scanning."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999711"),
        observer=lambda _queue, _message, _timestamp: None,
        config=load_config(
            {
                "WEFT_TASK_MONITOR_MODE": "custom",
                "WEFT_TASK_MONITOR_PROCESSOR": (
                    "tests.tasks.test_task_monitor:recording_processor"
                ),
            }
        ),
    )
    task._collation_state = replace(
        task._collation_state, last_collation_rows_processed=97
    )
    task._cleanup_state = replace(task._cleanup_state, last_reserved_rows_deleted=97)
    real_close = MonitorStore.close
    real_scan = task._scan_task_log_candidates
    scans: list[int] = []

    def fail_close(store: MonitorStore) -> None:
        real_close(store)
        raise RuntimeError("custom store close failed")

    def record_scan() -> tuple[
        tuple[task_monitor_mod.TaskMonitorCandidate, ...], int | None, int
    ]:
        result = real_scan()
        scans.append(result[2])
        return result

    monkeypatch.setattr(MonitorStore, "close", fail_close)
    monkeypatch.setattr(task, "_scan_task_log_candidates", record_scan)
    try:
        task._run_monitor_cycle()
        assert len(scans) == 1
        assert task._collation_state.last_collation_rows_processed == 97
        assert task._cleanup_state.last_reserved_rows_deleted == 97
        assert "custom store close failed" in (
            task._store_state.last_collation_store_error or ""
        )
        assert not task._store_state.monitor_store_status.available
        assert "custom store close failed" in (
            task._store_state.monitor_store_status.error or ""
        )
        assert task._control_cleanup_work_in_flight is None
    finally:
        task.stop()


DIAGNOSTIC_FIELDS = {
    "scan": (
        "last_candidates_seen",
        "last_candidate_class_counts",
        "last_safe_to_delete_candidates",
        "last_prune_records_scanned",
        "last_cleanup_queue_stats",
        "last_cleanup_policy_stats",
    ),
    "collation": (
        "last_collation_rows_processed",
        "last_collation_tasks_updated",
        "last_collation_terminal_tasks",
        "last_collation_summaries_emitted",
        "last_monitor_store_message_rows_deleted",
        "last_monitor_store_families_retired",
        "last_terminal_families_disposed",
        "last_suspect_families_classified",
        "last_control_families_processed",
        "last_control_families_disposed",
        "last_control_queues_deleted",
        "last_control_rows_estimated_deleted",
        "last_control_nonstandard_skipped",
        "last_control_cleanup_pending",
        "last_control_rows_deleted",
        "last_control_delete_errors",
        "last_control_delete_warnings",
    ),
    "runtime_cleanup": (
        "last_reserved_families_processed",
        "last_reserved_queues_deleted",
        "last_reserved_rows_estimated_deleted",
        "last_reserved_skipped_active",
        "last_reserved_skipped_not_ready",
        "last_reserved_rows_deleted",
        "last_control_cleanup_family_limit_hit",
        "last_control_cleanup_deadline_hit",
    ),
    "store": (
        "monitor_store_status",
        "last_retained_task_log_ingest",
        "last_pre_checkpoint_task_log_recovery",
        "last_orphan_task_log_recovery",
        "last_collation_store_error",
    ),
    "maintenance": (
        "next_maintenance_due_monotonic",
        "last_maintenance_run_at_ns",
        "last_maintenance_vacuum_ok",
        "last_maintenance_runtime_prune_candidates",
        "last_maintenance_runtime_prune_deleted",
        "last_maintenance_runtime_prune_partial_batches",
        "last_maintenance_error",
    ),
}

DIAGNOSTIC_STATE_ATTRIBUTES = {
    "scan": "_scan_state",
    "collation": "_collation_state",
    "runtime_cleanup": "_cleanup_state",
    "store": "_store_state",
    "maintenance": "_maintenance_state",
}


def _prior_diagnostic(field: str, previous: Any) -> Any:
    """Seed distinct valid owner values so omission and zero cannot look alike."""

    if field == "monitor_store_status":
        return replace(previous, available=False, error="previous store error")
    if field in {
        "last_retained_task_log_ingest",
        "last_pre_checkpoint_task_log_recovery",
    }:
        return replace(previous, scanned=83)
    if field == "last_orphan_task_log_recovery":
        return replace(previous, rows_deleted=83)
    if isinstance(previous, bool) or field == "last_maintenance_vacuum_ok":
        return True
    if field in {"last_maintenance_error", "last_collation_store_error"}:
        return "previous error"
    if isinstance(previous, dict):
        return {"previous": 83}
    if isinstance(previous, tuple):
        return ({"previous": 83},) if field.endswith("stats") else ("previous",)
    return 83


def _assert_diagnostic_field_contract(
    task: TaskMonitor,
    diagnostics: task_monitor_mod._MaintenanceDiagnostics,
    *,
    prior: dict[str, Any],
    expected_groups: set[str],
) -> None:
    """Check the complete field map against both produced and omitted groups."""

    assert {field.name for field in fields(diagnostics)} == {
        *DIAGNOSTIC_FIELDS,
        "policy_progress",
        "external_task_log_status",
    }
    task._apply_maintenance_diagnostics(diagnostics)
    for group_name, field_names in DIAGNOSTIC_FIELDS.items():
        group = getattr(diagnostics, group_name)
        owner_state = getattr(task, DIAGNOSTIC_STATE_ATTRIBUTES[group_name])
        assert (group is not None) == (group_name in expected_groups), group_name
        if group is not None:
            assert tuple(field.name for field in fields(group)) == field_names
        for field in field_names:
            expected = getattr(group, field) if group is not None else prior[field]
            assert getattr(owner_state, field) == expected, field


def _seed_prior_diagnostics(task: TaskMonitor) -> dict[str, Any]:
    """Populate every retained field with a distinct prior observation."""

    prior: dict[str, Any] = {}
    for group_name, field_names in DIAGNOSTIC_FIELDS.items():
        attribute = DIAGNOSTIC_STATE_ATTRIBUTES[group_name]
        owner_state = getattr(task, attribute)
        updates: dict[str, Any] = {}
        for field in field_names:
            value = _prior_diagnostic(field, getattr(owner_state, field))
            prior[field] = value
            updates[field] = value
        setattr(task, attribute, replace(owner_state, **updates))
    return prior


@pytest.mark.parametrize("mode", ["collated", "raw_external", "custom"])
@pytest.mark.parametrize("maintenance_due", [False, True])
def test_diagnostic_groups_apply_produced_fields_and_preserve_omissions(
    broker_env: BrokerEnv,
    tmp_path: Path,
    mode: Literal["collated", "raw_external", "custom"],
    maintenance_due: bool,
) -> None:
    """Every diagnostic field follows its operation's explicit update contract."""

    db_path, _make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_MODE": "custom" if mode == "custom" else "delete",
            "WEFT_TASK_MONITOR_PROCESSOR": (
                "tests.tasks.test_task_monitor:recording_processor"
                if mode == "custom"
                else ""
            ),
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
            "WEFT_LOG_TASKS_EXTERNAL_ENABLED": mode == "raw_external",
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "raw",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(tmp_path / "diagnostics.jsonl"),
        }
    )
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999706"),
        observer=lambda _queue, _message, _timestamp: None,
        config=config,
    )
    prior = _seed_prior_diagnostics(task)
    task._maintenance_state = replace(
        task._maintenance_state,
        next_maintenance_due_monotonic=(
            0.0 if maintenance_due else time.monotonic() + 3600.0
        ),
    )
    prior["next_maintenance_due_monotonic"] = (
        task._maintenance_state.next_maintenance_due_monotonic
    )
    inputs = task._capture_maintenance_inputs()
    now_ns = time.time_ns()

    def run_operation() -> task_monitor_mod._MaintenanceDiagnostics:
        close_errors: list[str] = []
        with task_monitor_mod._maintenance_worker_scope(
            inputs,
            close_errors,
            borrowed_session=task._broker_session if mode == "custom" else None,
        ) as worker:
            if mode == "custom":
                worker.run_collation(
                    task_monitor_mod._TaskMonitorCollationWork(
                        inputs=inputs, now_ns=now_ns, task_log_owner="collated_store"
                    )
                )
            else:
                worker.run_builtin_cycle(
                    task_monitor_mod._TaskMonitorBuiltinCycleWork(
                        inputs=inputs,
                        request_id="diagnostic-contract",
                        now_ns=now_ns,
                        task_log_owner=(
                            "raw_external"
                            if mode == "raw_external"
                            else "collated_store"
                        ),
                    )
                )
            diagnostics = worker.capture_diagnostics()
        assert close_errors == []
        return diagnostics

    try:
        if mode == "custom":
            diagnostics = run_operation()
        else:
            with ThreadPoolExecutor(max_workers=1) as executor:
                diagnostics = executor.submit(run_operation).result(timeout=5.0)
        expected_groups = {"collation"}
        if mode != "custom":
            expected_groups.add("scan")
        if mode != "raw_external":
            expected_groups.update({"runtime_cleanup", "store"})
        if mode != "custom" and maintenance_due:
            expected_groups.add("maintenance")
        _assert_diagnostic_field_contract(
            task, diagnostics, prior=prior, expected_groups=expected_groups
        )
        assert task._collation_state.last_collation_terminal_tasks == 0
        assert task._collation_state.last_control_cleanup_pending is False
        assert task._collation_state.last_control_delete_errors == ()
        if mode != "raw_external":
            assert task._cleanup_state.last_reserved_rows_deleted == 0
            assert task._cleanup_state.last_control_cleanup_family_limit_hit is False
            assert task._store_state.last_collation_store_error is None
        if mode != "custom":
            assert task._scan_state.last_candidates_seen == 0
            assert task._scan_state.last_candidate_class_counts == {}
        assert diagnostics.policy_progress is not None
        assert task._last_policy_progress == diagnostics.policy_progress
        assert diagnostics.external_task_log_status is not None
        assert diagnostics.external_task_log_status.total_emitted == 0
        assert diagnostics.external_task_log_status.total_blocked_deletions == 0
    finally:
        task.stop()


def test_failed_construction_never_readds_owner_external_totals(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two failed constructions preserve cumulative and deferred owner state."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999701"),
        observer=lambda _queue, _message, _timestamp: None,
    )

    def fail_construction(
        self: task_monitor_mod.MaintenanceWorker,
        borrowed_session: BrokerSession | None,
    ) -> None:
        raise RuntimeError("maintenance construction failed")

    monkeypatch.setattr(
        task_monitor_mod.MaintenanceWorker, "_open_resources", fail_construction
    )
    try:
        task._apply_worker_external_task_log_status(
            replace(
                task._external_task_log_status,
                total_emitted=3,
                total_blocked_deletions=5,
                deferred_pending=7,
                last_deferred_error="retained deferred failure",
                last_deferred_flush_at=1778089999999999700,
            )
        )
        observed_totals: list[tuple[int, int]] = []
        for cycle in range(2):
            work = task_monitor_mod._TaskMonitorBuiltinCycleWork(
                request_id=f"failed-construction-{cycle}",
                now_ns=time.time_ns(),
                task_log_owner="collated_store",
                inputs=task._capture_maintenance_inputs(),
            )
            worker_result = task._run_builtin_cycle_worker(work)
            task._service_lane_work_items[
                task_monitor_mod.TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE
            ] = work
            task._handle_builtin_cycle_worker_result(
                TaskWorkerResult(
                    lane=task_monitor_mod.TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE,
                    value=worker_result,
                )
            )
            observed_totals.append(
                (
                    task._external_task_log_status.total_emitted,
                    task._external_task_log_status.total_blocked_deletions,
                )
            )
            assert not worker_result.runtime_cleanup_ready
            assert task._control_cleanup_work_in_flight is None
            assert "maintenance construction failed" in task._last_errors
            assert task._deferred_task_log_pending == 7
            assert task._deferred_task_log_last_error == "retained deferred failure"
            assert task._deferred_task_log_last_flush_at == 1778089999999999700
        assert observed_totals == [(3, 5), (3, 5)]
    finally:
        task.stop()


def test_queued_worker_consumes_detached_inputs_without_task_initialization(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused real lane retains submitted policy and owns nested input values."""

    db_path, _make_queue = broker_env
    config = load_config({"WEFT_TASK_MONITOR_BATCH_SIZE": 31})
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999705"),
        observer=lambda _queue, _message, _timestamp: None,
        config=config,
    )
    context = task._monitor_context()
    context.project_config["maintenance_test"] = {"values": ["submitted"]}
    task._maintenance_state = replace(
        task._maintenance_state,
        next_maintenance_due_monotonic=time.monotonic() + 3600.0,
    )
    task._apply_worker_external_task_log_status(
        replace(task._external_task_log_status, deferred_pending=7)
    )
    started = threading.Event()
    release = threading.Event()
    worker_threads: list[int] = []
    received: list[tuple[int, int, int, list[str]]] = []
    real_run = task_monitor_mod.MaintenanceWorker.run_builtin_cycle

    def paused_run(
        worker: task_monitor_mod.MaintenanceWorker,
        work: task_monitor_mod._TaskMonitorBuiltinCycleWork,
    ) -> tuple[task_monitor_mod.TaskMonitorProcessorResult, bool]:
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(timeout=5.0)
        received.append(
            (
                worker._monitor_config.batch_size,
                int(work.inputs.config["TASK_MONITOR_BATCH_SIZE"]),
                work.inputs.external_status.deferred_pending,
                list(work.inputs.context.project_config["maintenance_test"]["values"]),
            )
        )
        work.inputs.context.project_config["maintenance_test"]["values"].append(
            "worker-only"
        )
        return real_run(worker, work)

    def forbidden_task_initialization(self: BaseTask) -> None:
        pytest.fail("MaintenanceWorker must not initialize a task lifecycle")

    monkeypatch.setattr(
        task_monitor_mod.MaintenanceWorker, "run_builtin_cycle", paused_run
    )
    monkeypatch.setattr(
        BaseTask, "_initialize_base_task_runtime", forbidden_task_initialization
    )
    monkeypatch.setattr(task, "reactor_only_extension", {"mutable": []}, raising=False)
    thread: threading.Thread | None = None
    try:
        work = task_monitor_mod._TaskMonitorBuiltinCycleWork(
            request_id="detached-maintenance-input",
            now_ns=time.time_ns(),
            task_log_owner="collated_store",
            inputs=task._capture_maintenance_inputs(),
        )
        assert work.inputs.context.broker_config is context.broker_config
        assert work.inputs.context.broker_target is context.broker_target
        thread = task._submit_builtin_cycle_worker(work)
        assert started.wait(timeout=5.0)
        context.project_config["maintenance_test"]["values"].append("owner-only")
        task._monitor_config = replace(task._monitor_config, batch_size=97)
        task._weft_config = dict(load_config({"WEFT_TASK_MONITOR_BATCH_SIZE": 97}))
        task._external_task_log_status = replace(
            task._external_task_log_status, deferred_pending=101
        )
        release.set()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        task._drain_worker_results()
        assert received == [(31, 31, 7, ["submitted"])]
        assert worker_threads == [thread.ident]
        assert thread.ident != threading.get_ident()
        assert context.project_config["maintenance_test"]["values"] == [
            "submitted",
            "owner-only",
        ]
        assert task._builtin_cycle_work_in_flight is None
        assert task._last_processor_success
        assert not issubclass(task_monitor_mod.MaintenanceWorker, BaseTask)
    finally:
        release.set()
        if thread is not None:
            thread.join(timeout=5.0)
        task.stop()


def test_custom_collation_borrows_session_for_stale_service_summary(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The synchronous summary path reads services without recycling its owner."""

    db_path, make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999703"),
        observer=lambda _queue, _message, _timestamp: None,
        config=load_config(
            {
                "WEFT_TASK_MONITOR_MODE": "custom",
                "WEFT_TASK_MONITOR_PROCESSOR": (
                    "tests.tasks.test_task_monitor:recording_processor"
                ),
                "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
                "WEFT_TASK_MONITOR_LOG_SINK": "none",
            }
        ),
    )
    stale_tid = "1778084345905438701"
    live_tid = "1778084345905438702"
    context = task._monitor_context()
    session = task._broker_session
    assert session is not None
    global_log = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True)
    store = open_monitor_store(context, queue=global_log)
    real_queue = WeftContext.queue
    temporary_queues: list[tuple[str, weakref.ReferenceType[Queue]]] = []

    def record_queue(
        worker_context: WeftContext, name: str, *, persistent: bool = False
    ) -> Queue:
        assert worker_context.broker_target is context.broker_target
        assert worker_context.broker_config is context.broker_config
        queue = real_queue(worker_context, name, persistent=persistent)
        temporary_queues.append((name, weakref.ref(queue)))
        return queue

    try:
        store.ensure_schema()
        update = update_from_task_log_payload(
            {
                "event": "work_started",
                "status": "running",
                "tid": stale_tid,
                "taskspec": {
                    "tid": stale_tid,
                    "version": "1.0",
                    "name": "manager",
                    "state": {"status": "running"},
                    "metadata": {"role": "manager"},
                },
            },
            message_id=int(stale_tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE, (update,), checkpoint_message_id=None
        )
        make_queue(WEFT_SERVICES_REGISTRY_QUEUE).write(
            json.dumps(
                build_manager_service_payload(
                    context=context,
                    tid=live_tid,
                    name="manager",
                    status=SERVICE_STATUS_ACTIVE,
                    queues={},
                    runtime_handle={},
                )
            )
        )
        monkeypatch.setattr(WeftContext, "queue", record_queue)
        with session.connection() as owner_core:
            assert not task._run_monitor_store_cycle(
                now_ns=time.time_ns(), task_log_owner="collated_store"
            )
            assert task._store_state.last_collation_store_error is None
            record = store.get_task(stale_tid)
            assert record is not None
            assert record.summary_emitted_at_ns is not None
            assert record.suspect_reason == "stale_service_owner"
            assert record.disposition_reason is None
            assert task._control_cleanup_work_in_flight is None
        with session.connection() as current_core:
            assert current_core is owner_core
            current_core.list_queues()
        gc.collect()
        assert {WEFT_GLOBAL_LOG_QUEUE, WEFT_SERVICES_REGISTRY_QUEUE} <= {
            name for name, _reference in temporary_queues
        }
        assert all(reference() is None for _name, reference in temporary_queues)
    finally:
        store.close()
        global_log.close()
        task.stop()


def test_initial_pong_uses_unavailable_cached_store_without_opening_it(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An initial PONG reports the unobserved store without acquiring resources."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999704"),
        observer=lambda _queue, _message, _timestamp: None,
    )

    def forbidden_store_open(*args: Any, **kwargs: Any) -> None:
        pytest.fail("PONG must not open or probe the maintenance store")

    monkeypatch.setattr(task_monitor_mod, "open_monitor_store", forbidden_store_open)
    try:
        before = task._store_state.monitor_store_status
        payload = task._task_monitor_pong_extension()
        assert not before.available
        assert payload["task_monitor"]["collation_store"] == before.to_summary()
        assert task._store_state.monitor_store_status is before
    finally:
        task.stop()


@pytest.mark.parametrize("outer_operation", [False, True])
def test_custom_collation_keeps_owner_session_and_core(
    broker_env: BrokerEnv,
    outer_operation: bool,
) -> None:
    """Repeated synchronous collation preserves the owner connection lifetime."""

    db_path, _make_queue = broker_env
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999702"),
        observer=lambda _queue, _message, _timestamp: None,
        config=load_config(
            {
                "WEFT_TASK_MONITOR_MODE": "custom",
                "WEFT_TASK_MONITOR_PROCESSOR": (
                    "tests.tasks.test_task_monitor:recording_processor"
                ),
            }
        ),
    )
    session = task._broker_session
    assert session is not None
    owner_queue = task._get_connected_queue()
    try:
        with session.connection() as owner_core:
            owner_core.list_queues()
        task._run_monitor_store_cycle(
            now_ns=time.time_ns(), task_log_owner="collated_store"
        )
        retained_queue_count = len(session._queues)

        def exercise_cycles() -> None:
            for _cycle in range(3):
                assert not task._run_monitor_store_cycle(
                    now_ns=time.time_ns(), task_log_owner="collated_store"
                )
                assert task._store_state.monitor_store_status.available
                assert task._store_state.last_collation_store_error is None
                with session.connection() as current_core:
                    assert current_core is owner_core
                assert len(session._queues) == retained_queue_count

        if outer_operation:
            with session.connection() as outer_core:
                exercise_cycles()
                assert outer_core is owner_core
                outer_core.list_queues()
        else:
            exercise_cycles()
        owner_queue.write("owner remains usable")
        assert owner_queue.read_one() == "owner remains usable"
    finally:
        task.stop()
