"""Task monitor task peek-loop tests."""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

import pytest

import weft.core.monitor.task_monitor as task_monitor_mod
import weft.core.tasks.base as base_task_mod
import weft.core.tasks.service as service_task_mod
from weft._constants import (
    CONTROL_PING,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    PONG_EXTENSION_KEY,
    QUEUE_INTERNAL_RESERVED_SUFFIX,
    SERVICE_OWNER_SCHEMA,
    SERVICE_STATUS_ACTIVE,
    SERVICE_TYPE_MANAGED,
    SERVICE_TYPE_MANAGER,
    TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS,
    TASK_MONITOR_CLEANUP_POLICY_NAMES,
    TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE,
    TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
    TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_MONITOR_SCHEMA_VERSION,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    load_config,
)
from weft.core.monitor.collation import update_from_task_log_payload
from weft.core.monitor.runtime import (
    TaskMonitorProcessorRequest,
    TaskMonitorProcessorResult,
)
from weft.core.monitor.store import (
    MonitorStore,
    MonitorStoreIngestResult,
    MonitorSummaryReadyTask,
    MonitorTaskCollationRecord,
)
from weft.core.monitor.task_monitor import (
    TaskMonitor,
    make_task_monitor_taskspec,
)
from weft.core.service_convergence import manager_service_key
from weft.core.taskspec import TaskSpec
from weft.helpers import iter_queue_entries

pytestmark = [pytest.mark.shared]

PROCESSOR_REQUESTS: list[TaskMonitorProcessorRequest] = []
BLOCKING_PROCESSOR_STARTED = threading.Event()
BLOCKING_PROCESSOR_RELEASE = threading.Event()
BLOCKING_PROCESSOR_TIMEOUT_SECONDS = 5.0


def recording_processor(
    request: TaskMonitorProcessorRequest,
) -> TaskMonitorProcessorResult:
    PROCESSOR_REQUESTS.append(request)
    return TaskMonitorProcessorResult(
        success=True,
        processed=len(request.candidates),
        reported=len(request.candidates),
    )


def failing_processor(
    request: TaskMonitorProcessorRequest,
) -> TaskMonitorProcessorResult:
    PROCESSOR_REQUESTS.append(request)
    return TaskMonitorProcessorResult(
        success=False,
        errors=("processor failed",),
    )


def blocking_processor(
    request: TaskMonitorProcessorRequest,
) -> TaskMonitorProcessorResult:
    BLOCKING_PROCESSOR_STARTED.set()
    assert BLOCKING_PROCESSOR_RELEASE.wait(timeout=BLOCKING_PROCESSOR_TIMEOUT_SECONDS)
    PROCESSOR_REQUESTS.append(request)
    return TaskMonitorProcessorResult(
        success=True,
        processed=len(request.candidates),
        reported=len(request.candidates),
    )


def drive_task_monitor_until_idle(
    task: TaskMonitor,
    *,
    timeout: float = 20.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task.process_once()
        task._drain_worker_results()
        if (
            task._builtin_cycle_work_in_flight is None
            and task._processor_work_in_flight is None
            and task._control_cleanup_work_in_flight is None
            and not task._has_pending_worker_results()
        ):
            break
        task.wait_for_activity(timeout=0.05)
    diagnostics = _task_monitor_idle_diagnostics(task)
    assert task._builtin_cycle_work_in_flight is None, diagnostics
    assert task._processor_work_in_flight is None, diagnostics
    assert task._control_cleanup_work_in_flight is None, diagnostics
    task._drain_worker_results()
    diagnostics = _task_monitor_idle_diagnostics(task)
    assert task._builtin_cycle_work_in_flight is None, diagnostics
    assert task._processor_work_in_flight is None, diagnostics
    assert task._control_cleanup_work_in_flight is None, diagnostics
    assert not task._has_pending_worker_results(), diagnostics


def _task_monitor_idle_diagnostics(task: TaskMonitor) -> str:
    """Return enough state to diagnose async worker drain failures."""

    frames = sys._current_frames()
    stacks: list[str] = []
    for thread in threading.enumerate():
        if not thread.name.startswith(f"weft-worker-{task.tid_short}-"):
            continue
        frame = frames.get(thread.ident)
        if frame is None:
            continue
        stack = "".join(traceback.format_stack(frame, limit=12))
        stacks.append(f"{thread.name}:\n{stack}")
    return json.dumps(
        {
            "builtin_cycle_work_in_flight": repr(task._builtin_cycle_work_in_flight),
            "processor_work_in_flight": repr(task._processor_work_in_flight),
            "control_cleanup_work_in_flight": repr(
                task._control_cleanup_work_in_flight
            ),
            "service_snapshot": task._service_worker_snapshot(),
            "worker_snapshot": task._worker_activity_snapshot(),
            "worker_stacks": stacks,
        },
        sort_keys=True,
        default=str,
    )


def _latest_tid_mapping_payload(
    make_queue: Callable[[str], Any],
    tid: str,
) -> dict[str, Any]:
    queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    latest: tuple[int, dict[str, Any]] | None = None
    for body, timestamp in iter_queue_entries(queue):
        payload = json.loads(body)
        if payload.get("full") != tid:
            continue
        if latest is None or timestamp >= latest[0]:
            latest = (timestamp, payload)
    assert latest is not None
    return latest[1]


def drive_task_monitor_until(
    task: TaskMonitor,
    predicate: Callable[[], bool],
    *,
    timeout: float = 10.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task.process_once()
        drive_task_monitor_until_idle(task)
        if predicate():
            return
        task._next_cycle_due_monotonic = 0.0
        task.wait_for_activity(timeout=0.05)
    assert predicate()


@pytest.fixture(autouse=True)
def clear_processor_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    PROCESSOR_REQUESTS.clear()
    BLOCKING_PROCESSOR_STARTED.clear()
    BLOCKING_PROCESSOR_RELEASE.clear()
    monkeypatch.setattr(
        task_monitor_mod,
        "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS",
        30.0,
    )


def _task_monitor_taskspec_for_context(tid: str, context_path: str) -> TaskSpec:
    payload = make_task_monitor_taskspec(tid).model_dump(mode="python")
    payload["spec"]["weft_context"] = context_path
    return TaskSpec(**payload)


def test_task_monitor_uses_cached_base_task_context(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    calls: list[bool | None] = []
    real_build_context = base_task_mod.build_context

    def counted_build_context(*args: object, **kwargs: object) -> object:
        value = kwargs.get("create_database")
        calls.append(value if isinstance(value, bool) else None)
        return real_build_context(*args, **kwargs)

    monkeypatch.setattr(base_task_mod, "build_context", counted_build_context)
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999910"),
        observer=lambda _queue_name, _message, _timestamp: None,
    )
    try:
        context = task._monitor_context()
        assert task._monitor_context() is context
        assert calls == [False]
    finally:
        task.stop()


def test_task_monitor_scan_once_peeks_task_log_without_consuming(
    broker_env,
) -> None:
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payloads = [
        {"event": "work_started", "tid": "1778084345905438720"},
        {"event": "work_completed", "tid": "1778084345905438720"},
    ]
    for payload in payloads:
        log_queue.write(json.dumps(payload))

    seen: list[tuple[str, dict[str, object], int]] = []
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999999"),
        observer=lambda queue_name, message, timestamp: seen.append(
            (queue_name, json.loads(message), timestamp)
        ),
    )
    try:
        task.scan_once(since_timestamp=0)
    finally:
        task.stop()

    seen_events = [
        item[1]["event"]
        for item in seen
        if item[0] == WEFT_GLOBAL_LOG_QUEUE
        and item[1].get("tid") == "1778084345905438720"
    ]
    assert seen_events == ["work_started", "work_completed"]

    remaining = [
        json.loads(message)
        for message, _timestamp in log_queue.peek_generator(with_timestamps=True)
    ]
    assert payloads[0] in remaining
    assert payloads[1] in remaining


def test_task_monitor_process_once_calls_processor_without_consuming_task_log(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "custom",
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:recording_processor",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payloads = [
        {"event": "work_started", "tid": "1778084345905438720"},
        {"event": "work_failed", "tid": "1778084345905438720"},
    ]
    for payload in payloads:
        log_queue.write(json.dumps(payload))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999999"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        assert task._control_cleanup_work_in_flight is None
        assert task._last_control_cleanup_deadline_hit is False
    finally:
        task.stop()

    assert len(PROCESSOR_REQUESTS) == 1
    request = PROCESSOR_REQUESTS[0]
    assert [candidate.tid for candidate in request.candidates] == [
        "1778084345905438720",
    ]
    assert request.candidates[0].candidate_class == "active"
    assert all(candidate.safe_to_delete is False for candidate in request.candidates)
    remaining = [
        json.loads(message)
        for message, _timestamp in log_queue.peek_generator(with_timestamps=True)
    ]
    assert payloads[0] in remaining
    assert payloads[1] in remaining


def test_task_monitor_builtin_delete_removes_cleanup_rows(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "delete",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write("{not-json")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999986"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
    finally:
        task.stop()

    assert "{not-json" not in list(log_queue.peek_generator())
    assert task._last_processor_success is True
    assert task._last_processed >= 1
    assert task._last_deleted >= 1
    assert task._last_prune_records_scanned >= 1
    assert task._last_cleanup_queue_stats


def test_task_monitor_builtin_report_only_keeps_cleanup_rows(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "report_only",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write("{not-json")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999985"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
    finally:
        task.stop()

    assert "{not-json" in list(log_queue.peek_generator())
    assert task._last_processor_success is True
    assert task._last_processed == 0
    assert task._last_deleted == 0
    assert task._last_reported == 0
    assert task._last_retained_task_log_ingest.scanned >= 1
    assert task._last_retained_task_log_ingest.malformed_deleted == 0


def test_task_monitor_next_wait_timeout_is_capped_after_cycle(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "custom",
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:recording_processor",
        }
    )
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999988"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)

        assert 0.0 < task.next_wait_timeout() <= 1.0
    finally:
        task.stop()


def test_task_monitor_pending_wakeup_uses_shared_reactor_wait(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "custom",
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:recording_processor",
        }
    )
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999987"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        make_queue(task.taskspec.io.inputs["inbox"]).write(
            json.dumps({"type": "task_monitor_wakeup"})
        )

        assert task.next_wait_timeout() == pytest.approx(1.0)
        task.wait_for_activity(timeout=task.next_wait_timeout())
        task.process_once()
        drive_task_monitor_until_idle(task)
        assert len(PROCESSOR_REQUESTS) == 2
    finally:
        task.stop()


def test_task_monitor_disabled_uses_wait_cap_without_scanning(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod,
        "build_task_monitor_cycle_snapshot",
        lambda *args, **kwargs: pytest.fail("disabled monitor must not scan"),
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "upsert_heartbeat",
        lambda *args, **kwargs: pytest.fail("disabled monitor must not heartbeat"),
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": False,
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "custom",
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:recording_processor",
        }
    )
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999984"),
        config=config,
    )
    try:
        task.process_once()

        assert task.next_wait_timeout() == 1.0

        make_queue(task.taskspec.io.inputs["inbox"]).write(
            json.dumps({"type": "task_monitor_wakeup"})
        )
        assert task.next_wait_timeout() == 1.0
        task.wait_for_activity(timeout=task.next_wait_timeout())
        task.process_once()
        assert task.next_wait_timeout() == 1.0
    finally:
        task.stop()


def test_task_monitor_ping_includes_health_and_preserves_task_log(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "report_only",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999998")
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(json.dumps({"event": "work_started", "tid": "1778084345905438720"}))
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "ping-before"}))

    task = TaskMonitor(db_path, spec, config=config)
    responses: list[dict[str, object]] = []
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "ping-after"}))
        task.wait_for_activity(timeout=task.next_wait_timeout())
        task.process_once()
        responses = [json.loads(item) for item in ctrl_out.peek_generator()]
    finally:
        task.stop()

    pong = next(
        response
        for response in responses
        if response["command"] == CONTROL_PING
        and response.get("request_id") == "ping-after"
    )
    assert pong["status"] == "ok"
    assert pong["message"] == "PONG"
    assert pong["role"] == "task_monitor"
    assert pong["task_status"] == "running"
    assert pong["task_monitor_mode"] == "report_only"
    assert pong["processor"] is None
    assert pong["interval_seconds"] == 60
    assert pong["batch_size"] == 10
    assert pong["last_candidate_class_counts"] == {}
    assert pong["last_safe_to_delete_candidates"] == 0
    assert pong["last_cleanup_queue_stats"]
    assert pong["last_cleanup_policy_stats"]
    assert pong["last_policy_progress"]
    extended = pong[PONG_EXTENSION_KEY]["task_monitor"]
    assert extended["enabled"] is True
    assert extended["mode"] == "persistent"
    assert extended["task_monitor_mode"] == "report_only"
    assert extended["processor"] is None
    assert extended["interval_seconds"] == 60
    assert extended["batch_size"] == 10
    assert extended["log_sink"] == "stdout"
    assert extended["heartbeat"] == {
        "registered": True,
        "id": "task-monitor:1778089999999999998",
        "error": None,
        "next_registration_attempt_in_seconds": 0.0,
    }
    assert extended["schedule"]["first_cycle_pending"] is False
    assert extended["schedule"]["wake_requested"] is False
    assert extended["schedule"]["last_cycle_at"] == pong["last_cycle_at"]
    assert extended["schedule"]["last_checkpoint"] == pong["last_checkpoint"]
    assert 0.0 <= extended["schedule"]["next_cycle_due_in_seconds"] <= 60.0
    assert extended["last_cycle"]["success"] is True
    assert extended["last_cycle"]["error"] is None
    assert extended["last_cycle"]["candidates_seen"] == pong["last_candidates_seen"]
    assert extended["last_cycle"]["candidate_class_counts"] == {}
    assert extended["last_cycle"]["safe_to_delete_candidates"] == 0
    assert extended["last_cycle"]["processed"] == pong["last_processed"]
    assert extended["last_cycle"]["deleted"] == pong["last_deleted"]
    assert extended["last_cycle"]["reported"] == pong["last_reported"]
    assert (
        extended["last_cycle"]["prune_records_scanned"]
        == (pong["last_prune_records_scanned"])
    )
    assert (
        extended["last_cycle"]["cleanup_queue_stats"]
        == (pong["last_cleanup_queue_stats"])
    )
    assert (
        extended["last_cycle"]["cleanup_policy_stats"]
        == (pong["last_cleanup_policy_stats"])
    )
    assert extended["last_cycle"]["policy_progress"] == (pong["last_policy_progress"])
    assert {
        progress["policy"] for progress in extended["last_cycle"]["policy_progress"]
    } <= set(TASK_MONITOR_CLEANUP_POLICY_NAMES)
    assert any(
        progress["policy"] == TASK_MONITOR_POLICY_TASK_LOG_RETENTION
        for progress in extended["last_cycle"]["policy_progress"]
    )
    retained = extended["last_cycle"]["retained_task_log_ingest"]
    assert retained["scanned"] >= 1
    assert retained["raw_deleted"] == 0
    assert extended["last_cycle"]["warnings"] == []
    assert extended["last_cycle"]["errors"] == []
    assert log_queue.peek_one() is not None


def test_task_monitor_ping_includes_cached_collation_store_status(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "report_only",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999982")
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(
        json.dumps(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": "1778084345905438721",
                "taskspec": {
                    "tid": "1778084345905438721",
                    "version": "1.0",
                    "name": "sample",
                    "state": {"status": "completed", "return_code": 0},
                },
            }
        )
    )
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    task = TaskMonitor(db_path, spec, config=config)
    responses: list[dict[str, object]] = []
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)

        def fail_store_cycle(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise AssertionError("PING must not run Monitor store collation")

        monkeypatch.setattr(task, "_run_monitor_store_cycle", fail_store_cycle)
        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "store"}))
        task.wait_for_activity(timeout=task.next_wait_timeout())
        task.process_once()
        responses = [json.loads(item) for item in ctrl_out.peek_generator()]
    finally:
        task.stop()

    pong = next(
        response
        for response in responses
        if response["command"] == CONTROL_PING and response.get("request_id") == "store"
    )
    store = pong[PONG_EXTENSION_KEY]["task_monitor"]["collation_store"]
    assert store["enabled"] is True
    assert store["available"] is True
    assert store["schema_version"] == WEFT_MONITOR_SCHEMA_VERSION
    assert store["checkpoint"] is not None
    last_cycle = pong[PONG_EXTENSION_KEY]["task_monitor"]["last_cycle"]
    assert last_cycle["collation_rows_processed"] >= 1
    assert last_cycle["collation_tasks_updated"] >= 1
    assert last_cycle["collation_terminal_tasks"] >= 1
    assert last_cycle["collation_summaries_emitted"] == 1
    assert last_cycle["monitor_store_message_rows_deleted"] == 0


def test_task_monitor_processor_delete_requires_delete_processor(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "report_only",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1778084345905438722",
    }
    log_queue.write(json.dumps(payload))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999981"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
    finally:
        task.stop()

    target_rows = [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == payload["tid"]
    ]
    assert target_rows == [payload]
    assert task._last_monitor_store_message_rows_deleted == 0


def test_task_monitor_processor_delete_removes_exact_task_log_rows(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(
        json.dumps(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": "1778084345905438723",
            }
        )
    )

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999980"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
    finally:
        task.stop()

    target_rows = [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == "1778084345905438723"
    ]
    assert target_rows == []
    assert task._last_monitor_store_message_rows_deleted >= 1


def test_task_monitor_delete_retains_terminal_rows_until_retention_age(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "172800",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438726"
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": tid,
    }
    log_queue.write(json.dumps(payload))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999975"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        record = store.get_task(tid)
        assert record is not None
        assert record.summary_emitted_at_ns is not None
        assert record.raw_deleted_at_ns is not None
        assert record.disposition_at_ns is not None
        assert record.task_control_deleted_at_ns is not None
        assert task._last_monitor_store_message_rows_deleted >= 1
        assert task._last_monitor_store_families_retired == 0
    finally:
        task.stop()

    assert [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == tid
    ] == []


def test_task_monitor_retained_ingest_batches_store_and_delete_work(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    update_batch_sizes: list[int] = []
    original_record_updates = MonitorStore.record_task_log_updates

    def record_updates(
        self: MonitorStore,
        queue_name: str,
        updates,
        *,
        checkpoint_message_id: int | None,
    ) -> MonitorStoreIngestResult:
        update_batch_sizes.append(len(tuple(updates)))
        return original_record_updates(
            self,
            queue_name,
            updates,
            checkpoint_message_id=checkpoint_message_id,
        )

    monkeypatch.setattr(MonitorStore, "record_task_log_updates", record_updates)
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438729"
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    for index in range(5):
        event = "work_completed" if index == 4 else "task_activity"
        status = "completed" if index == 4 else "running"
        log_queue.write(
            json.dumps(
                {
                    "event": event,
                    "status": status,
                    "tid": tid,
                    "sequence": index,
                }
            )
        )

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999977"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        record = store.get_task(tid)
        assert record is None
    finally:
        task.stop()

    assert len(update_batch_sizes) == 1
    assert update_batch_sizes[0] >= 5
    target_rows = [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == tid
    ]
    assert target_rows == []
    retained = task._last_retained_task_log_ingest
    assert retained.selected >= 5
    assert retained.valid_ingested >= 5
    assert retained.raw_deleted >= 5
    assert retained.store_update_chunks == 1
    assert retained.exact_delete_chunks == 1
    assert retained.monitor_store_delete_chunks == 1
    assert retained.checkpoint_written is True
    assert task._last_monitor_store_message_rows_deleted >= 5
    assert task._last_monitor_store_families_retired >= 1


def test_task_monitor_retained_ingest_handles_own_tid_before_checkpoint(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    monitor_tid = "1778089999999999881"
    other_tid = "1778084345905438881"
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": 20,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(
        json.dumps(
            {
                "event": "task_activity",
                "status": "running",
                "tid": monitor_tid,
                "activity": "cleanup_scanning",
            }
        )
    )
    log_queue.write(
        json.dumps(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": other_tid,
            }
        )
    )
    message_ids = [
        int(message_id) for _body, message_id in iter_queue_entries(log_queue)
    ]

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(monitor_tid),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        checkpoint = store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE)
        assert checkpoint is not None
        assert checkpoint >= message_ids[-1]
        assert task._last_retained_task_log_ingest.selected >= 2
        assert task._last_retained_task_log_ingest.valid_ingested >= 2
    finally:
        task.stop()

    remaining_rows = {
        int(message_id): json.loads(body)
        for body, message_id in iter_queue_entries(log_queue)
        if body.startswith("{")
    }
    assert message_ids[0] not in remaining_rows
    assert message_ids[1] not in remaining_rows


def test_task_monitor_skips_terminal_summary_after_partial_fifo_pass(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS": "0.2",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": 1,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438728"
    terminal_payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": tid,
    }
    later_payload = {
        "event": "task_activity",
        "status": "completed",
        "tid": tid,
    }
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(json.dumps(terminal_payload))
    log_queue.write(json.dumps(later_payload))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999976"),
        config=config,
    )
    try:
        cycle_started_at = time.monotonic()
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        record = store.get_task(tid)
        assert record is not None
        assert record.terminal_seen is True
        assert record.summary_emitted_at_ns is None
        assert task._last_retained_task_log_ingest.completed_fifo_high_water is False
        assert task._last_catchup_pending is True
        assert task._next_cycle_due_monotonic > cycle_started_at
        assert 0.0 <= task.next_wait_timeout() <= 0.2
    finally:
        task.stop()

    remaining = [
        json.loads(message)
        for message in log_queue.peek_generator()
        if message.startswith("{")
    ]
    assert later_payload in remaining


def test_task_monitor_retained_ingest_batch_limit_counts_valid_rows(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS": "0.2",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 3,
            "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": 20,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "report_only",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438799"
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    for sequence in range(5):
        log_queue.write(
            json.dumps(
                {
                    "event": "task_activity",
                    "status": "running",
                    "tid": tid,
                    "sequence": sequence,
                }
            )
        )

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999876"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        retained = task._last_retained_task_log_ingest
        assert retained.selected == 3
        assert retained.valid_ingested == 3
        assert retained.stop_reason == "batch_limit"
        assert retained.completed_fifo_high_water is False
        assert any(
            progress.policy == TASK_MONITOR_POLICY_TASK_LOG_RETENTION
            and progress.waypoint_reached
            and not progress.base_reached
            for progress in task._last_policy_progress
        )
        assert task._last_collation_tasks_updated == 1
        assert task._last_catchup_pending is True
    finally:
        task.stop()


def test_task_monitor_retained_ingest_resumes_after_store_checkpoint(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS": "0.2",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 3,
            "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": 20,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "report_only",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438801"
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    for sequence in range(5):
        log_queue.write(
            json.dumps(
                {
                    "event": "task_activity",
                    "status": "running",
                    "tid": tid,
                    "sequence": sequence,
                }
            )
        )
    message_ids = [
        int(message_id) for _body, message_id in iter_queue_entries(log_queue)
    ]

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999877"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        assert task._last_retained_task_log_ingest.selected == 3
        assert store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE) == message_ids[2]

        task._next_cycle_due_monotonic = 0.0
        task.process_once()
        drive_task_monitor_until_idle(task)

        assert task._last_retained_task_log_ingest.selected >= 2
        assert store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE) >= message_ids[4]
    finally:
        task.stop()


def test_task_monitor_processor_delete_reconciles_already_absent_exact_rows(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1778084345905438724",
    }
    log_queue.write(json.dumps(payload))
    message_id = next(iter_queue_entries(log_queue))[1]
    report_config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "report_only",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    report_task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999979"),
        config=report_config,
    )
    try:
        report_task.process_once()
        drive_task_monitor_until_idle(report_task)
        store = report_task._monitor_store
        assert store is not None
        record = store.get_task(payload["tid"])
        assert record is not None
        assert record.summary_emitted_at_ns is not None
        assert record.raw_deleted_at_ns is None
    finally:
        report_task.stop()

    assert log_queue.delete(message_id=message_id) is True
    delete_config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    delete_task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999978"),
        config=delete_config,
    )
    try:

        def raw_delete_reconciled() -> bool:
            store = delete_task._monitor_store
            record = store.get_task(payload["tid"]) if store is not None else None
            return store is not None and record is None

        drive_task_monitor_until(delete_task, raw_delete_reconciled, timeout=30.0)
        store = delete_task._monitor_store
        assert store is not None
        record = store.get_task(payload["tid"])
        assert record is None
    finally:
        delete_task.stop()

    assert delete_task._last_processor_success is True


def test_task_monitor_recovers_orphan_raw_task_log_rows_after_bad_raw_mark(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "172800",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438725"
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payloads = [
        {
            "event": "task_activity",
            "status": "running",
            "tid": tid,
            "sequence": 1,
        },
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "sequence": 2,
        },
    ]
    for payload in payloads:
        log_queue.write(json.dumps(payload))
    rows = list(iter_queue_entries(log_queue))
    updates = tuple(
        update_from_task_log_payload(
            json.loads(body),
            queue_name=WEFT_GLOBAL_LOG_QUEUE,
            message_id=int(message_id),
        )
        for body, message_id in rows
    )
    assert all(update is not None for update in updates)

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999875"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            tuple(update for update in updates if update is not None),
            checkpoint_message_id=None,
        )
        store.delete_task_messages_after_raw_delete(
            tuple(int(message_id) for _body, message_id in rows),
            deleted_at_ns=time.time_ns(),
        )
        record = store.get_task(tid)
        assert record is not None
        assert record.raw_deleted_at_ns is not None
        assert list(log_queue.peek_generator()) != []

        recovery = task._recover_orphan_task_log_rows(store, now_ns=time.time_ns())
    finally:
        task.stop()

    assert recovery.refs_selected == 2
    assert recovery.rows_deleted == 2
    assert recovery.families_checked == 1
    assert recovery.empty_probes == 0
    assert recovery.errors == ()
    assert store.list_raw_deleted_task_log_recovery_tids(limit=10) == ()
    recovered_record = store.get_task(tid)
    assert recovered_record is not None
    assert recovered_record.orphan_raw_recovery_checked_at_ns is not None
    assert [
        json.loads(body)
        for body, _message_id in iter_queue_entries(log_queue)
        if json.loads(body).get("tid") == tid
    ] == []


def test_task_monitor_marks_orphan_recovery_checked_when_raw_rows_absent(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "172800",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438726"
    terminal = update_from_task_log_payload(
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
        },
        queue_name=WEFT_GLOBAL_LOG_QUEUE,
        message_id=1778084345905438727,
    )
    assert terminal is not None

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999874"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (terminal,),
            checkpoint_message_id=None,
        )
        store.delete_task_messages_after_raw_delete(
            (terminal.message_id,),
            deleted_at_ns=terminal.message_id + 1,
        )
        assert store.list_raw_deleted_task_log_recovery_tids(limit=10) == (tid,)

        recovery = task._recover_orphan_task_log_rows(
            store,
            now_ns=terminal.message_id + 2,
        )
        second_recovery = task._recover_orphan_task_log_rows(
            store,
            now_ns=terminal.message_id + 3,
        )
    finally:
        task.stop()

    assert recovery.refs_selected == 0
    assert recovery.rows_deleted == 0
    assert recovery.families_checked == 1
    assert recovery.empty_probes == 1
    assert recovery.errors == ()
    assert second_recovery.families_checked == 0
    assert second_recovery.empty_probes == 0
    assert store.list_raw_deleted_task_log_recovery_tids(limit=10) == ()
    record = store.get_task(tid)
    assert record is not None
    assert record.orphan_raw_recovery_checked_at_ns == terminal.message_id + 2
    assert task._last_policy_progress[-1].policy == (
        TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE
    )
    assert task._last_policy_progress[-1].base_reached is True


def test_task_monitor_orphan_recovery_leaves_failed_probe_retryable(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "172800",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438728"
    terminal = update_from_task_log_payload(
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
        },
        queue_name=WEFT_GLOBAL_LOG_QUEUE,
        message_id=1778084345905438729,
    )
    assert terminal is not None

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999873"),
        config=config,
    )

    def fail_fetch(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("probe failed")

    monkeypatch.setattr(
        task_monitor_mod,
        "_fetch_dead_task_log_coalesce_group",
        fail_fetch,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (terminal,),
            checkpoint_message_id=None,
        )
        store.delete_task_messages_after_raw_delete(
            (terminal.message_id,),
            deleted_at_ns=terminal.message_id + 1,
        )

        recovery = task._recover_orphan_task_log_rows(
            store,
            now_ns=terminal.message_id + 2,
        )
    finally:
        task.stop()

    assert recovery.families_checked == 0
    assert recovery.empty_probes == 0
    assert recovery.errors == (f"{tid}: probe failed",)
    assert store.list_raw_deleted_task_log_recovery_tids(limit=10) == (tid,)
    record = store.get_task(tid)
    assert record is not None
    assert record.orphan_raw_recovery_checked_at_ns is None


def test_task_monitor_orphan_recovery_reports_bounded_waypoint(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 1,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "172800",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tids = ("1778084345905438730", "1778084345905438732")
    terminals = []
    for offset, tid in enumerate(tids):
        terminal = update_from_task_log_payload(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": tid,
            },
            queue_name=WEFT_GLOBAL_LOG_QUEUE,
            message_id=1778084345905438731 + offset,
        )
        assert terminal is not None
        terminals.append(terminal)

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999872"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            tuple(terminals),
            checkpoint_message_id=None,
        )
        store.delete_task_messages_after_raw_delete(
            tuple(terminal.message_id for terminal in terminals),
            deleted_at_ns=1778084345905438740,
        )

        first = task._recover_orphan_task_log_rows(store, now_ns=1778084345905438741)
        first_progress = task._last_policy_progress[-1]
        second = task._recover_orphan_task_log_rows(store, now_ns=1778084345905438742)
        second_progress = task._last_policy_progress[-1]
        third = task._recover_orphan_task_log_rows(store, now_ns=1778084345905438743)
        third_progress = task._last_policy_progress[-1]
    finally:
        task.stop()

    assert first.families_checked == 1
    assert first.empty_probes == 1
    assert first_progress.waypoint_reached is True
    assert first_progress.base_reached is False
    assert second.families_checked == 1
    assert second.empty_probes == 1
    assert second_progress.waypoint_reached is False
    assert second_progress.base_reached is False
    assert third.families_checked == 0
    assert third.empty_probes == 0
    assert third_progress.waypoint_reached is False
    assert third_progress.base_reached is True
    assert store.list_raw_deleted_task_log_recovery_tids(limit=10) == ()


def test_task_monitor_failed_summary_disposition_blocks_processor_delete(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "stdout",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1778084345905438725",
    }
    log_queue.write(json.dumps(payload))

    def fail_summary(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("summary sink failed")

    monkeypatch.setattr(
        TaskMonitor,
        "_emit_monitor_store_summary",
        fail_summary,
    )
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999977"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        record = store.get_task(payload["tid"])
        assert record is not None
        assert record.summary_emitted_at_ns is None
        assert record.raw_deleted_at_ns is not None
    finally:
        task.stop()

    target_rows = [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == payload["tid"]
    ]
    assert target_rows == []
    assert task._last_monitor_store_message_rows_deleted >= 1


def test_task_monitor_collated_external_log_precedes_processor_delete(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-summary.jsonl"
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_ENABLED": "1",
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1778084345905438730",
    }
    log_queue.write(json.dumps(payload))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999970"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        record = store.get_task(payload["tid"])
        assert record is None
        assert task._last_monitor_store_families_retired >= 1
    finally:
        task.stop()

    assert [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == payload["tid"]
    ] == []
    [line] = external_path.read_text(encoding="utf-8").splitlines()
    external = json.loads(line)
    assert external["record_type"] == "task_log_collated"
    assert external["task"]["tid"] == payload["tid"]
    assert task._external_task_log_status.healthy is True


def test_task_monitor_jsonl_then_delete_uses_project_default_log_path(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    task = TaskMonitor(
        db_path,
        _task_monitor_taskspec_for_context(
            "1778089999999999968",
            str(tmp_path),
        ),
        config=config,
    )
    try:
        assert task._external_task_log_status.enabled is True
        assert task._external_task_log_status.path == str(
            tmp_path / "logs" / "weft.log"
        )
    finally:
        task.stop()


def test_task_monitor_external_log_probe_recovers_on_monitor_cadence(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "external-target"
    external_path.mkdir()
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999963"),
        config=config,
    )
    try:
        assert task._external_task_log_status.healthy is False

        external_path.rmdir()
        task.process_once()
        drive_task_monitor_until_idle(task)

        assert task._external_task_log_status.healthy is True
        assert external_path.is_file()
        mapping = _latest_tid_mapping_payload(make_queue, task.tid)
        task_monitor = mapping["task_monitor"]
        external = task_monitor["task_log_external"]
        assert external["healthy"] is True
        assert external["path"] == str(external_path)
    finally:
        task.stop()


def test_task_monitor_external_log_probe_reports_regression_on_monitor_cadence(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999960"),
        config=config,
    )
    try:
        assert task._external_task_log_status.healthy is True

        assert task._external_task_log_sink is not None
        task._external_task_log_sink.close()
        external_path.unlink()
        external_path.mkdir()
        task.process_once()
        drive_task_monitor_until_idle(task)

        assert task._external_task_log_status.healthy is False
        mapping = _latest_tid_mapping_payload(make_queue, task.tid)
        task_monitor = mapping["task_monitor"]
        external = task_monitor["task_log_external"]
        assert external["healthy"] is False
        assert external["path"] == str(external_path)
        assert "directory" in external["last_error"]
    finally:
        task.stop()


def test_task_monitor_jsonl_then_delete_emits_lifetime_report_before_delete(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1778084345905438733",
    }
    log_queue.write(json.dumps(payload))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999967"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        assert store.deferred_write_status().pending == 0
    finally:
        task.stop()

    assert [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == payload["tid"]
    ] == []
    [line] = external_path.read_text(encoding="utf-8").splitlines()
    external = json.loads(line)
    assert external["record_type"] == "task_lifetime_report"
    assert external["source_policy"] == TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE
    assert external["completeness"] == "collated"
    assert external["subject"]["tid"] == payload["tid"]
    assert external["taskspec"]["tid"] == payload["tid"]
    assert external["taskspec"]["state"]["status"] == "completed"
    assert external["monitor"]["collation_kind"] == "user_task"
    assert "last_message_id" in external["monitor"]
    assert "collation" not in external
    assert "effect" not in external


def test_task_monitor_jsonl_then_delete_defers_external_failure_and_deletes(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "external-target"
    external_path.mkdir()
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1778084345905438734",
    }
    log_queue.write(json.dumps(payload))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999966"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        pending = store.list_pending_deferred_writes(limit=10)
        assert len(pending) == 1
        body = pending[0].body()
        assert body["record_type"] == "task_lifetime_report"
        assert body["subject"]["tid"] == payload["tid"]
        assert store.get_task(payload["tid"]) is None
    finally:
        task.stop()

    assert [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == payload["tid"]
    ] == []
    assert task._external_task_log_status.healthy is False
    assert task._external_task_log_status.deferred_pending == 1

    external_path.rmdir()
    flush_task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999965"),
        config=config,
    )
    try:
        flush_task.process_once()
        drive_task_monitor_until_idle(flush_task)
        store = flush_task._monitor_store
        assert store is not None
        assert store.deferred_write_status().pending == 0
    finally:
        flush_task.stop()

    [line] = external_path.read_text(encoding="utf-8").splitlines()
    flushed = json.loads(line)
    assert flushed["record_type"] == "task_lifetime_report"
    assert flushed["subject"]["tid"] == payload["tid"]


def test_task_monitor_jsonl_then_delete_flushes_accumulated_deferred_reports(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "external-target"
    external_path.mkdir()
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    tids = ("1778084345905438750", "1778084345905438751")
    for tid in tids:
        log_queue.write(
            json.dumps(
                {
                    "event": "work_completed",
                    "status": "completed",
                    "tid": tid,
                }
            )
        )

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999962"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        assert store.deferred_write_status().pending == 2
    finally:
        task.stop()

    assert [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") in tids
    ] == []

    external_path.rmdir()
    flush_task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999961"),
        config=config,
    )
    try:
        flush_task.process_once()
        drive_task_monitor_until_idle(flush_task)
        store = flush_task._monitor_store
        assert store is not None
        assert store.deferred_write_status().pending == 0
    finally:
        flush_task.stop()

    records = [
        json.loads(line)
        for line in external_path.read_text(encoding="utf-8").splitlines()
    ]
    assert sorted(record["subject"]["tid"] for record in records) == sorted(tids)


def test_task_monitor_jsonl_then_delete_blocks_when_external_and_deferred_fail(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "external-target"
    external_path.mkdir()

    def fail_deferred_write(self: MonitorStore, **_kwargs: object) -> None:
        raise RuntimeError("deferred table unavailable")

    monkeypatch.setattr(MonitorStore, "upsert_deferred_write", fail_deferred_write)
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1778084345905438735",
    }
    log_queue.write(json.dumps(payload))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999964"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        record = store.get_task(payload["tid"])
        assert record is not None
        assert record.summary_emitted_at_ns is None
        assert record.raw_deleted_at_ns is None
    finally:
        task.stop()

    target_rows = [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == payload["tid"]
    ]
    assert target_rows == [payload]
    assert task._last_processor_success is False
    assert any("deferred table unavailable" in error for error in task._last_errors)


def test_task_monitor_emits_service_summary_for_service_collation(
    broker_env,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path, _make_queue = broker_env
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_LOG_SINK": "stdout",
        }
    )
    service_tid = "1778084345905438731"
    record = MonitorTaskCollationRecord(
        context_key="test",
        tid=service_tid,
        name="task-monitor",
        runner="host",
        parent_tid="1778084345905438700",
        role="task_monitor",
        status="cancelled",
        terminal_seen=True,
        terminal_event="work_cancelled",
        terminal_status="cancelled",
        terminal_message_id=int(service_tid) + 2,
        return_code=None,
        first_message_id=int(service_tid) + 1,
        last_message_id=int(service_tid) + 2,
        first_seen_at_ns=int(service_tid) + 1,
        last_seen_at_ns=int(service_tid) + 2,
        started_at_ns=int(service_tid) + 1,
        completed_at_ns=int(service_tid) + 2,
        taskspec_summary={
            "tid": service_tid,
            "name": "task-monitor",
            "metadata": {
                INTERNAL_RUNTIME_TASK_CLASS_KEY: (
                    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
                ),
                INTERNAL_SERVICE_KEY_METADATA_KEY: INTERNAL_SERVICE_KEY_TASK_MONITOR,
                INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY: "ensure",
                "role": "task_monitor",
            },
        },
        state={"status": "cancelled"},
    )
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999971"),
        observer=lambda _queue, _message, _timestamp: None,
        config=config,
    )
    try:
        task._emit_monitor_store_summary(
            MonitorSummaryReadyTask(record=record, close_reason="terminal"),
            emitted_at_ns=1778084345905439999,
        )
    finally:
        task.stop()

    [line] = capsys.readouterr().out.splitlines()
    payload = json.loads(line)
    assert payload["record_type"] == "service_summary"
    assert payload["service"]["kind"] == "internal_service"
    assert payload["service"]["runtime_class"] == (
        INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
    )
    assert payload["task"]["collation_kind"] == "internal_service"
    assert payload["task"]["tid"] == service_tid


def test_task_monitor_terminal_disposition_deletes_task_runtime_queues(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438733"
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "sample",
        "spec": {"reporting_interval": 1.0},
        "io": {
            "inputs": {"inbox": f"T{tid}.inbox"},
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "completed", "return_code": 0},
        "metadata": {},
    }
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(
        json.dumps(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": tid,
                "taskspec": taskspec,
            }
        )
    )
    ctrl_in = make_queue(f"T{tid}.ctrl_in")
    ctrl_out = make_queue(f"T{tid}.ctrl_out")
    inbox = make_queue(f"T{tid}.inbox")
    outbox = make_queue(f"T{tid}.outbox")
    reserved = make_queue(f"T{tid}.reserved")
    ctrl_in.write("stop")
    ctrl_in.write("claimed-stop")
    assert ctrl_in.read_one() == "stop"
    ctrl_out.write("pong")
    ctrl_out.write("claimed-pong")
    assert ctrl_out.read_one() == "pong"
    inbox.write("input")
    outbox.write("result")
    reserved.write("reserved")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999967"),
        config=config,
    )
    try:

        def runtime_cleanup_done() -> bool:
            store = task._monitor_store
            record = store.get_task(tid) if store is not None else None
            return (
                store is not None
                and record is None
                and list(reserved.peek_generator()) == []
            )

        drive_task_monitor_until(task, runtime_cleanup_done, timeout=30.0)
        store = task._monitor_store
        assert store is not None
        record = store.get_task(tid)
        assert record is None
    finally:
        task.stop()

    assert ctrl_in.stats().total == 0
    assert ctrl_out.stats().total == 0
    assert list(inbox.peek_generator()) == []
    assert list(outbox.peek_generator()) == []
    assert list(reserved.peek_generator()) == []


def test_task_monitor_deletes_controls_for_already_disposed_family(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 100,
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438739"
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "sample",
        "io": {
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "completed"},
        "metadata": {},
    }
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999961"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": tid,
                "taskspec": taskspec,
            },
            message_id=1778084345905438739,
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(tid, 1778084345905438740)
        store.mark_family_disposed(
            tid,
            1778084345905438741,
            disposition_reason="terminal",
        )
        make_queue(f"T{tid}.ctrl_in").write("stop")
        make_queue(f"T{tid}.ctrl_out").write("pong")

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )

        def controls_deleted() -> bool:
            record = store.get_task(tid)
            return (
                (record is None or record.task_control_deleted_at_ns is not None)
                and make_queue(f"T{tid}.ctrl_in").stats().total == 0
                and make_queue(f"T{tid}.ctrl_out").stats().total == 0
            )

        if not controls_deleted():
            drive_task_monitor_until(task, controls_deleted, timeout=30.0)

        record = store.get_task(tid)
        assert record is not None
        assert record.task_control_deleted_at_ns is not None
        assert cleanup.pending or cleanup.families_processed == 1
        assert cleanup.families_disposed == 0
    finally:
        task.stop()

    assert make_queue(f"T{tid}.ctrl_in").stats().total == 0
    assert make_queue(f"T{tid}.ctrl_out").stats().total == 0


def test_task_monitor_terminal_control_cleanup_does_not_wait_for_retention(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 100,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "172800",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = str(time.time_ns())
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "sample",
        "io": {
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "completed"},
        "metadata": {},
    }
    make_queue(f"T{tid}.ctrl_in").write("stop")
    make_queue(f"T{tid}.ctrl_out").write("pong")
    make_queue(f"T{tid}.inbox").write("input")
    outbox = make_queue(f"T{tid}.outbox")
    outbox.write("result")
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999962"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": tid,
                "taskspec": taskspec,
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(tid, int(tid) + 1)

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=int(tid) + 2,
        )
        record = store.get_task(tid)
        assert record is not None
        assert record.task_control_deleted_at_ns is not None
        assert cleanup.families_processed == 1
        assert make_queue(f"T{tid}.ctrl_in").stats().total == 0
        assert make_queue(f"T{tid}.ctrl_out").stats().total == 0
        assert make_queue(f"T{tid}.inbox").stats().total == 0
        assert list(outbox.peek_generator()) == ["result"]
    finally:
        task.stop()


def test_task_monitor_control_cleanup_does_not_mark_when_queue_delete_fails(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 100,
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438762"
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "sample",
        "io": {
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "completed"},
        "metadata": {},
    }
    ctrl_in = make_queue(f"T{tid}.ctrl_in")
    ctrl_out = make_queue(f"T{tid}.ctrl_out")
    ctrl_in.write("stop")
    ctrl_out.write("pong")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999964"),
        config=config,
    )
    with task._monitor_context().broker() as broker:
        broker_type = type(broker)
    original_delete_from_queues = broker_type.delete_from_queues

    def failing_control_delete(
        self,
        queue_names,
        *,
        before_timestamp=None,
    ) -> int:
        if f"T{tid}.ctrl_out" in queue_names:
            raise RuntimeError("control delete failed")
        return original_delete_from_queues(
            self,
            queue_names,
            before_timestamp=before_timestamp,
        )

    monkeypatch.setattr(broker_type, "delete_from_queues", failing_control_delete)
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": tid,
                "taskspec": taskspec,
            },
            message_id=1778084345905438762,
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(tid, 1778084345905438763)
        store.mark_family_disposed(
            tid,
            1778084345905438764,
            disposition_reason="terminal",
        )

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        record = store.get_task(tid)
        assert record is not None
        assert record.task_control_deleted_at_ns is None
        assert cleanup.errors
        assert cleanup.pending is True
    finally:
        task.stop()

    assert ctrl_in.stats().total == 1
    assert ctrl_out.stats().total == 1


def test_task_monitor_terminal_cleanup_repairs_control_deleted_without_disposition(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 100,
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438766"
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999966"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": tid,
                "taskspec": {
                    "tid": tid,
                    "version": "1.0",
                    "name": "sample",
                    "io": {
                        "control": {
                            "ctrl_in": f"T{tid}.ctrl_in",
                            "ctrl_out": f"T{tid}.ctrl_out",
                        },
                    },
                    "state": {"status": "completed"},
                    "metadata": {},
                },
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.delete_task_messages_after_raw_delete(
            (update.message_id,),
            deleted_at_ns=update.message_id + 1,
        )
        store.mark_summary_emitted(tid, update.message_id + 2)
        store.mark_task_control_deleted(tid, update.message_id + 3)

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )

        assert cleanup.families_disposed == 1
        assert cleanup.families_retired == 1
        assert store.get_task(tid) is None
    finally:
        task.stop()


def test_task_monitor_repairs_raw_deleted_parent_with_child_refs(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438734"
    terminal = update_from_task_log_payload(
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
        },
        queue_name=WEFT_GLOBAL_LOG_QUEUE,
        message_id=1778084345905438735,
    )
    assert terminal is not None
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999875"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (terminal,),
            checkpoint_message_id=None,
        )
        with task._monitor_context().broker() as broker:
            runner = broker._runner
            runner.run(
                "UPDATE weft_monitor_task_collations "
                "SET raw_deleted_at_ns = ?, updated_at_ns = ? "
                "WHERE context_key = ? AND tid = ?",
                (
                    terminal.message_id + 1,
                    terminal.message_id + 1,
                    store.context_key,
                    tid,
                ),
            )
            runner.commit()
        assert store.list_deletable_task_log_messages(limit=10) == ()
        assert store.list_raw_deleted_task_message_refs(limit=10)[0].tid == tid

        repair = task._repair_raw_deleted_task_message_refs(store)
        second_repair = task._repair_raw_deleted_task_message_refs(store)
    finally:
        task.stop()

    assert repair.message_rows_deleted == 1
    assert second_repair.message_rows_deleted == 0
    assert store.list_raw_deleted_task_message_refs(limit=10) == ()
    assert task._last_policy_progress[-1].policy == (
        TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE
    )
    assert task._last_policy_progress[-1].base_reached is True


def test_task_monitor_delete_removes_stale_reserved_queue_without_monitor_record(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438749"
    reserved = make_queue(f"T{tid}.reserved")
    reserved.write("stale-reserved")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999960"),
        config=config,
    )
    try:
        drive_task_monitor_until(
            task,
            lambda: list(reserved.peek_generator()) == [],
            timeout=30.0,
        )
    finally:
        task.stop()

    assert list(reserved.peek_generator()) == []


def test_task_monitor_reserved_cleanup_runs_when_task_log_ingest_is_batch_limited(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 1,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    for tid in ("1778084345905438841", "1778084345905438842"):
        log_queue.write(
            json.dumps(
                {
                    "event": "work_started",
                    "status": "running",
                    "tid": tid,
                    "taskspec": {
                        "tid": tid,
                        "version": "1.0",
                        "name": "sample",
                        "io": {},
                        "state": {"status": "running"},
                        "metadata": {},
                    },
                }
            )
        )
    stale_tid = "1778084345905438849"
    reserved = make_queue(f"T{stale_tid}.reserved")
    reserved.write("stale-reserved")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999949"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        assert task._last_retained_task_log_ingest.stop_reason == "batch_limit"
        drive_task_monitor_until(
            task,
            lambda: list(reserved.peek_generator()) == [],
            timeout=30.0,
        )
    finally:
        task.stop()

    assert list(reserved.peek_generator()) == []


def test_task_monitor_keeps_reserved_queue_for_active_service_owner(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438759"
    reserved = make_queue(f"T{tid}.reserved")
    reserved.write("active-reserved")
    services = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    services.write(
        json.dumps(
            {
                "schema": SERVICE_OWNER_SCHEMA,
                "service_key": f"managed:{tid}",
                "service_type": SERVICE_TYPE_MANAGED,
                "owner_tid": tid,
                "status": SERVICE_STATUS_ACTIVE,
            }
        )
    )

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999959"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        cleanup = task._run_reserved_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        assert cleanup.reserved_families_processed == 0
        assert cleanup.reserved_queues_deleted == 0
        assert cleanup.reserved_skipped_active == 1
    finally:
        task.stop()

    assert list(reserved.peek_generator()) == ["active-reserved"]


def test_task_monitor_reserved_cleanup_marks_absent_reserved_probe_checked(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438760"
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999961"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_failed",
                "status": "failed",
                "tid": tid,
                "taskspec": {
                    "tid": tid,
                    "version": "1.0",
                    "name": "sample",
                    "io": {},
                    "state": {"status": "failed"},
                    "metadata": {},
                },
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(tid, int(tid) + 1)

        cleanup = task._run_reserved_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        second = task._run_reserved_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        record = store.get_task(tid)
        assert record is not None
        assert record.reserved_cleanup_checked_at_ns is not None
        assert cleanup.reserved_families_processed == 1
        assert cleanup.reserved_queues_deleted == 0
        assert second.policy_progress[-1].base_reached is True
        assert second.policy_progress[-1].selected == 0
    finally:
        task.stop()


def test_task_monitor_reserved_cleanup_marks_deleted_reserved_probe_checked(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438763"
    reserved = make_queue(f"T{tid}.reserved")
    reserved.write("failed-reserved")
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999963"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_failed",
                "status": "failed",
                "tid": tid,
                "taskspec": {
                    "tid": tid,
                    "version": "1.0",
                    "name": "sample",
                    "io": {},
                    "state": {"status": "failed"},
                    "metadata": {},
                },
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(tid, int(tid) + 1)

        cleanup = task._run_reserved_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        record = store.get_task(tid)
        assert record is not None
        assert record.reserved_cleanup_checked_at_ns is not None
        assert cleanup.reserved_families_processed == 1
        assert cleanup.reserved_queues_deleted == 1
        assert list(reserved.peek_generator()) == []
    finally:
        task.stop()


def test_task_monitor_reserved_cleanup_keeps_failed_delete_retryable(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438764"
    reserved = make_queue(f"T{tid}.reserved")
    reserved.write("failed-reserved")
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999964"),
        config=config,
    )

    def fail_delete(queue_name: str):
        del queue_name
        return task_monitor_mod._TaskControlCleanupResult(
            pending=True,
            errors=("delete failed",),
        )

    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_failed",
                "status": "failed",
                "tid": tid,
                "taskspec": {
                    "tid": tid,
                    "version": "1.0",
                    "name": "sample",
                    "io": {},
                    "state": {"status": "failed"},
                    "metadata": {},
                },
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(tid, int(tid) + 1)
        monkeypatch.setattr(task, "_delete_runtime_reserved_queue", fail_delete)

        cleanup = task._run_reserved_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        record = store.get_task(tid)
        assert record is not None
        assert record.reserved_cleanup_checked_at_ns is None
        assert store.list_reserved_cleanup_pending_tasks(limit=10)[0].tid == tid
        assert cleanup.pending is True
        assert cleanup.errors == ("delete failed",)
        assert list(reserved.peek_generator()) == ["failed-reserved"]
    finally:
        task.stop()


def test_task_monitor_reserved_cleanup_reports_bounded_waypoint(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT",
        1,
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT": 10,
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tids = ("1778084345905438765", "1778084345905438766")
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999965"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        updates = []
        for tid in tids:
            update = update_from_task_log_payload(
                {
                    "event": "work_failed",
                    "status": "failed",
                    "tid": tid,
                    "taskspec": {
                        "tid": tid,
                        "version": "1.0",
                        "name": "sample",
                        "io": {},
                        "state": {"status": "failed"},
                        "metadata": {},
                    },
                },
                message_id=int(tid),
            )
            assert update is not None
            updates.append(update)
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            tuple(updates),
            checkpoint_message_id=None,
        )
        for tid in tids:
            store.mark_summary_emitted(tid, int(tid) + 1)

        first = task._run_reserved_cleanup_slice(store, now_ns=time.time_ns())
        second = task._run_reserved_cleanup_slice(store, now_ns=time.time_ns())
        third = task._run_reserved_cleanup_slice(store, now_ns=time.time_ns())

        assert first.reserved_families_processed == 1
        assert first.policy_progress[-1].waypoint_reached is True
        assert first.next_slice_kind is None
        assert second.reserved_families_processed == 1
        assert second.next_slice_kind == "dead_tid"
        assert third.policy_progress[-1].base_reached is True
        assert third.policy_progress[-1].selected == 0
    finally:
        task.stop()


def test_task_monitor_reserved_cleanup_batches_fallback_record_lookup(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "999999999",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    now_ns = time.time_ns()
    base_tid = now_ns - int(
        (TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS + 60.0) * 1e9
    )
    tid_count = 75
    for offset in range(tid_count):
        tid = str(base_tid - offset)
        make_queue(f"T{tid}.reserved").write(f"reserved-{offset}")

    class BatchOnlyStore:
        def __init__(self, wrapped: MonitorStore) -> None:
            self.wrapped = wrapped
            self.get_tasks_calls = 0

        def get_tasks(self, tids: tuple[str, ...]) -> tuple[Any, ...]:
            self.get_tasks_calls += 1
            return self.wrapped.get_tasks(tids)

        def get_task(self, tid: str) -> Any:
            raise AssertionError(f"per-TID Monitor lookup called for {tid}")

        def __getattr__(self, name: str) -> Any:
            return getattr(self.wrapped, name)

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(now_ns + 1_000_000)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        batch_store = BatchOnlyStore(store)
        cleanup = task._run_reserved_cleanup_slice(
            batch_store,
            now_ns=now_ns,
        )
    finally:
        task.stop()

    assert batch_store.get_tasks_calls == 1
    assert cleanup.reserved_families_processed == 0
    assert cleanup.reserved_skipped_not_ready == tid_count
    assert cleanup.pending is True
    assert cleanup.deadline_hit is False
    assert cleanup.next_slice_kind == "dead_tid"


def test_task_monitor_dead_task_cleanup_deletes_standard_control_queues(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438861"
    ctrl_in = make_queue(f"T{tid}.ctrl_in")
    ctrl_out = make_queue(f"T{tid}.ctrl_out")
    inbox = make_queue(f"T{tid}.inbox")
    outbox = make_queue(f"T{tid}.outbox")
    reserved = make_queue(f"T{tid}.reserved")
    ctrl_in.write("stop")
    ctrl_out.write("pong")
    inbox.write("input")
    outbox.write("result")
    reserved.write("reserved")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999861"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        cleanup = task._run_dead_task_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
    finally:
        task.stop()

    assert ctrl_in.stats().total == 0
    assert ctrl_out.stats().total == 0
    assert inbox.stats().total == 0
    assert outbox.stats().total == 0
    assert reserved.stats().total == 0
    assert cleanup.dead_tids_processed == 1
    assert cleanup.dead_tid_queues_deleted == 5
    assert cleanup.dead_tid_control_queues_deleted == 2
    assert cleanup.dead_tid_inbox_queues_deleted == 1
    assert cleanup.dead_tid_outbox_queues_deleted == 1
    assert cleanup.dead_tid_reserved_queues_deleted == 1
    assert cleanup.dead_tid_rows_estimated_deleted == 5


def test_task_monitor_dead_task_cleanup_retains_outbox_and_reserved_before_retention(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    now_ns = time.time_ns()
    tid = str(
        now_ns - int((TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS + 60.0) * 1e9)
    )
    ctrl_in = make_queue(f"T{tid}.ctrl_in")
    inbox = make_queue(f"T{tid}.inbox")
    outbox = make_queue(f"T{tid}.outbox")
    reserved = make_queue(f"T{tid}.reserved")
    ctrl_in.write("stop")
    inbox.write("input")
    outbox.write("result")
    reserved.write("reserved")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(now_ns + 1_000_000)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        cleanup = task._run_dead_task_cleanup_slice(
            store,
            now_ns=now_ns,
        )
    finally:
        task.stop()

    assert ctrl_in.stats().total == 0
    assert inbox.stats().total == 0
    assert list(outbox.peek_generator()) == ["result"]
    assert list(reserved.peek_generator()) == ["reserved"]
    assert cleanup.dead_tids_processed == 1
    assert cleanup.dead_tid_control_queues_deleted == 1
    assert cleanup.dead_tid_inbox_queues_deleted == 1
    assert cleanup.dead_tid_outbox_queues_deleted == 0
    assert cleanup.dead_tid_reserved_queues_deleted == 0


def test_task_monitor_dead_task_cleanup_defers_outbox_only_until_retention(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "172800",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    now_ns = time.time_ns()
    tid = str(
        now_ns - int((TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS + 60.0) * 1e9)
    )
    outbox = make_queue(f"T{tid}.outbox")
    reserved = make_queue(f"T{tid}.reserved")
    outbox.write("result")
    reserved.write("reserved")
    coalesce_calls: list[str] = []

    def counted_coalesce(ctx, tid_arg: str, *, chunk_limit: int):
        del ctx, chunk_limit
        coalesce_calls.append(tid_arg)
        raise AssertionError("retention-deferred dead TID should not coalesce logs")

    monkeypatch.setattr(
        task_monitor_mod,
        "_fetch_dead_task_log_coalesce_group",
        counted_coalesce,
    )

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(now_ns + 1_000_000)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        cleanup = task._run_dead_task_cleanup_slice(
            store,
            now_ns=now_ns,
        )
    finally:
        task.stop()

    assert list(outbox.peek_generator()) == ["result"]
    assert list(reserved.peek_generator()) == ["reserved"]
    assert cleanup.dead_tids_processed == 0
    assert cleanup.dead_tids_deferred_retention == 1
    assert cleanup.dead_tids_pending == 0
    assert cleanup.pending is False
    assert cleanup.policy_progress[-1].base_reached is True
    assert cleanup.policy_progress[-1].waypoint_reached is False
    assert coalesce_calls == []


def test_task_monitor_dead_task_cleanup_skips_monitor_lookup_for_deferred_only_queues(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "999999999",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    now_ns = time.time_ns()
    base_tid = now_ns - int(
        (TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS + 60.0) * 1e9
    )
    tid_count = 75
    for offset in range(tid_count):
        tid = str(base_tid - offset)
        make_queue(f"T{tid}.outbox").write(f"result-{offset}")

    class BatchOnlyStore:
        def __init__(self, wrapped: MonitorStore) -> None:
            self.wrapped = wrapped
            self.get_tasks_calls = 0

        def get_tasks(self, tids: tuple[str, ...]) -> tuple[Any, ...]:
            self.get_tasks_calls += 1
            return self.wrapped.get_tasks(tids)

        def get_task(self, tid: str) -> Any:
            raise AssertionError(f"per-TID Monitor lookup called for {tid}")

        def __getattr__(self, name: str) -> Any:
            return getattr(self.wrapped, name)

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(now_ns + 1_000_000)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        batch_store = BatchOnlyStore(store)
        cleanup = task._run_dead_task_cleanup_slice(
            batch_store,
            now_ns=now_ns,
        )
    finally:
        task.stop()

    assert batch_store.get_tasks_calls == 0
    assert cleanup.dead_tids_processed == 0
    assert cleanup.dead_tids_deferred_retention == tid_count
    assert cleanup.dead_tids_pending == 0
    assert cleanup.pending is False
    assert cleanup.deadline_hit is False
    assert cleanup.policy_progress[-1].base_reached is True
    assert cleanup.policy_progress[-1].waypoint_reached is False


def test_task_monitor_dead_task_cleanup_does_not_coalesce_task_log_refs(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "999999999",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438865"
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": tid,
        "taskspec": {
            "tid": tid,
            "version": "1.0",
            "name": "sample",
            "io": {
                "control": {
                    "ctrl_in": f"T{tid}.ctrl_in",
                    "ctrl_out": f"T{tid}.ctrl_out",
                },
            },
            "state": {"status": "completed"},
            "metadata": {},
        },
    }
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(json.dumps(payload))
    ctrl_in = make_queue(f"T{tid}.ctrl_in")
    ctrl_out = make_queue(f"T{tid}.ctrl_out")
    ctrl_in.write("stop")
    ctrl_out.write("pong")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999865"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        coalesce_calls: list[str] = []
        real_coalesce = task_monitor_mod._fetch_dead_task_log_coalesce_group

        def counted_coalesce(ctx, tid_arg: str, *, chunk_limit: int):
            coalesce_calls.append(tid_arg)
            return real_coalesce(
                ctx,
                tid_arg,
                chunk_limit=chunk_limit,
            )

        monkeypatch.setattr(
            task_monitor_mod,
            "_fetch_dead_task_log_coalesce_group",
            counted_coalesce,
        )

        cleanup = task._run_dead_task_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
    finally:
        task.stop()

    assert ctrl_in.stats().total == 0
    assert ctrl_out.stats().total == 0
    retained_task_rows = [
        json.loads(body)
        for body, _message_id in iter_queue_entries(log_queue)
        if json.loads(body).get("tid") == tid
    ]
    assert retained_task_rows == [payload]
    assert coalesce_calls == []
    assert cleanup.dead_tid_log_refs_selected == 0
    assert cleanup.dead_tid_log_rows_deleted == 0


def test_task_monitor_dead_task_cleanup_skips_live_service_owner(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438862"
    ctrl_in = make_queue(f"T{tid}.ctrl_in")
    ctrl_out = make_queue(f"T{tid}.ctrl_out")
    ctrl_in.write("stop")
    ctrl_out.write("pong")
    services = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    services.write(
        json.dumps(
            {
                "schema": SERVICE_OWNER_SCHEMA,
                "service_key": f"managed:{tid}",
                "service_type": SERVICE_TYPE_MANAGED,
                "owner_tid": tid,
                "status": SERVICE_STATUS_ACTIVE,
            }
        )
    )

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999862"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        cleanup = task._run_dead_task_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
    finally:
        task.stop()

    assert list(ctrl_in.peek_generator()) == ["stop"]
    assert list(ctrl_out.peek_generator()) == ["pong"]
    assert cleanup.dead_tids_processed == 0
    assert cleanup.dead_tids_skipped_live >= 1


def test_task_monitor_dead_task_cleanup_is_oldest_first_and_bounded(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT",
        1,
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT": 1,
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    oldest_tid = "1778084345905438863"
    newest_tid = "1778084345905438864"
    for tid in (newest_tid, oldest_tid):
        make_queue(f"T{tid}.ctrl_in").write(f"stop-{tid}")
        make_queue(f"T{tid}.ctrl_out").write(f"pong-{tid}")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999863"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        cleanup = task._run_dead_task_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
    finally:
        task.stop()

    assert make_queue(f"T{oldest_tid}.ctrl_in").stats().total == 0
    assert make_queue(f"T{oldest_tid}.ctrl_out").stats().total == 0
    assert list(make_queue(f"T{newest_tid}.ctrl_in").peek_generator()) == [
        f"stop-{newest_tid}"
    ]
    assert list(make_queue(f"T{newest_tid}.ctrl_out").peek_generator()) == [
        f"pong-{newest_tid}"
    ]
    assert cleanup.dead_tids_processed == 1
    assert cleanup.dead_tids_pending >= 1
    assert cleanup.family_limit_hit is True


def test_task_monitor_terminal_disposition_does_not_delete_manager_control_queue(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438734"
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "manager",
        "io": {
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "completed"},
        "metadata": {"role": "manager"},
    }
    ctrl_in = make_queue(f"T{tid}.ctrl_in")
    ctrl_out = make_queue(f"T{tid}.ctrl_out")
    ctrl_in.write("stop")
    ctrl_out.write("pong")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999966"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": tid,
                "taskspec": taskspec,
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(tid, int(tid) + 1)

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        record = store.get_task(tid)
        assert record is not None
        assert record.disposition_reason == "terminal"
        assert record.task_control_deleted_at_ns is not None
        assert cleanup.skipped_nonstandard == 1
        assert cleanup.rows_estimated_deleted == 0
    finally:
        task.stop()

    assert list(ctrl_in.peek_generator()) == ["stop"]
    assert list(ctrl_out.peek_generator()) == ["pong"]


def test_task_monitor_skips_ambiguous_old_service_owner_collation(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438745"
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "manager",
        "io": {
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "running"},
        "metadata": {"role": "manager"},
    }
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999967"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_started",
                "status": "running",
                "tid": tid,
                "taskspec": taskspec,
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )

        emitted = task._emit_monitor_store_summaries(
            store,
            now_ns=time.time_ns(),
            apply_disposition=True,
        )
        record = store.get_task(tid)
        assert record is not None
        assert emitted == 0
        assert record.terminal_seen is False
        assert record.status == "running"
        assert record.summary_emitted_at_ns is None
        assert record.disposition_reason is None
        assert record.suspect_reason is None
    finally:
        task.stop()


def test_task_monitor_disposes_old_stale_service_owner_collation(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438745"
    active_tid = "1778084345905438755"
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "manager",
        "io": {
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "running"},
        "metadata": {"role": "manager"},
    }
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999967"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        make_queue(WEFT_SERVICES_REGISTRY_QUEUE).write(
            json.dumps(
                {
                    "schema": SERVICE_OWNER_SCHEMA,
                    "service_key": manager_service_key(task._monitor_context()),
                    "service_type": SERVICE_TYPE_MANAGER,
                    "owner_tid": active_tid,
                    "status": SERVICE_STATUS_ACTIVE,
                }
            )
        )
        update = update_from_task_log_payload(
            {
                "event": "work_started",
                "status": "running",
                "tid": tid,
                "taskspec": taskspec,
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )

        emitted = task._emit_monitor_store_summaries(
            store,
            now_ns=time.time_ns(),
            apply_disposition=True,
        )
        record = store.get_task(tid)
        assert record is not None
        assert emitted == 1
        assert record.terminal_seen is False
        assert record.status == "running"
        assert record.summary_emitted_at_ns is not None
        assert record.disposition_reason == "stale_service_owner"
        assert record.suspect_reason == "stale_service_owner"
    finally:
        task.stop()


def test_task_monitor_stale_service_owner_cleanup_deletes_only_control_queues(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438746"
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "manager",
        "io": {
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "running"},
        "metadata": {"role": "manager"},
    }
    ctrl_in = make_queue(f"T{tid}.ctrl_in")
    ctrl_out = make_queue(f"T{tid}.ctrl_out")
    inbox = make_queue(f"T{tid}.inbox")
    internal_reserved = make_queue(f"T{tid}.{QUEUE_INTERNAL_RESERVED_SUFFIX}")
    spawn_requests = make_queue(WEFT_SPAWN_REQUESTS_QUEUE)
    manager_outbox = make_queue(WEFT_MANAGER_OUTBOX_QUEUE)
    ctrl_in.write("stale-ping")
    ctrl_out.write("stale-pong")
    inbox.write("payload")
    internal_reserved.write("internal")
    spawn_requests.write("spawn")
    manager_outbox.write("manager-output")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999968"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_started",
                "status": "running",
                "tid": tid,
                "taskspec": taskspec,
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(
            tid,
            int(tid) + 1,
            suspect_reason="stale_service_owner",
        )
        store.mark_family_disposed(
            tid,
            int(tid) + 2,
            disposition_reason="stale_service_owner",
            suspect_reason="stale_service_owner",
            suspect_at_ns=int(tid) + 2,
        )

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        record = store.get_task(tid)
        assert record is not None
        assert record.task_control_deleted_at_ns is not None
        assert cleanup.families_processed == 1
        assert cleanup.skipped_nonstandard == 0
    finally:
        task.stop()

    assert ctrl_in.stats().total == 0
    assert ctrl_out.stats().total == 0
    assert list(inbox.peek_generator()) == ["payload"]
    assert list(internal_reserved.peek_generator()) == ["internal"]
    assert list(spawn_requests.peek_generator()) == ["spawn"]
    assert list(manager_outbox.peek_generator()) == ["manager-output"]


def test_task_monitor_stale_service_owner_cleanup_skips_active_owner(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438747"
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "task-monitor",
        "io": {
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "running"},
        "metadata": {
            "role": "task_monitor",
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
            INTERNAL_SERVICE_KEY_METADATA_KEY: INTERNAL_SERVICE_KEY_TASK_MONITOR,
        },
    }
    ctrl_in = make_queue(f"T{tid}.ctrl_in")
    ctrl_out = make_queue(f"T{tid}.ctrl_out")
    ctrl_in.write("active-ping")
    ctrl_out.write("active-pong")
    make_queue(WEFT_SERVICES_REGISTRY_QUEUE).write(
        json.dumps(
            {
                "schema": SERVICE_OWNER_SCHEMA,
                "service_key": INTERNAL_SERVICE_KEY_TASK_MONITOR,
                "service_type": SERVICE_TYPE_MANAGED,
                "owner_tid": tid,
                "status": SERVICE_STATUS_ACTIVE,
            }
        )
    )

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999969"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_started",
                "status": "running",
                "tid": tid,
                "taskspec": taskspec,
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(
            tid,
            int(tid) + 1,
            suspect_reason="stale_service_owner",
        )
        store.mark_family_disposed(
            tid,
            int(tid) + 2,
            disposition_reason="stale_service_owner",
            suspect_reason="stale_service_owner",
            suspect_at_ns=int(tid) + 2,
        )

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        record = store.get_task(tid)
        assert record is not None
        assert record.task_control_deleted_at_ns is None
        assert cleanup.families_processed == 0
    finally:
        task.stop()

    assert list(ctrl_in.peek_generator()) == ["active-ping"]
    assert list(ctrl_out.peek_generator()) == ["active-pong"]


def test_task_monitor_terminal_control_cleanup_is_bounded_by_family(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS": "0.01",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT": 1,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tids = ("1778084345905438741", "1778084345905438742")
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    for tid in tids:
        taskspec = {
            "tid": tid,
            "version": "1.0",
            "name": "sample",
            "io": {
                "control": {
                    "ctrl_in": f"T{tid}.ctrl_in",
                    "ctrl_out": f"T{tid}.ctrl_out",
                },
            },
            "state": {"status": "completed"},
            "metadata": {},
        }
        log_queue.write(
            json.dumps(
                {
                    "event": "work_completed",
                    "status": "completed",
                    "tid": tid,
                    "taskspec": taskspec,
                }
            )
        )
        make_queue(f"T{tid}.ctrl_in").write(f"stop-{tid}")
        make_queue(f"T{tid}.ctrl_out").write(f"pong-{tid}")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999965"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        updates = []
        for tid in tids:
            update = update_from_task_log_payload(
                {
                    "event": "work_completed",
                    "status": "completed",
                    "tid": tid,
                    "taskspec": {
                        "tid": tid,
                        "version": "1.0",
                        "name": "sample",
                        "io": {
                            "control": {
                                "ctrl_in": f"T{tid}.ctrl_in",
                                "ctrl_out": f"T{tid}.ctrl_out",
                            },
                        },
                        "state": {"status": "completed"},
                        "metadata": {},
                    },
                },
                message_id=int(tid),
            )
            assert update is not None
            updates.append(update)
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            tuple(updates),
            checkpoint_message_id=None,
        )
        for tid in tids:
            store.mark_summary_emitted(tid, int(tid) + 1)

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        first = store.get_task(tids[0])
        second = store.get_task(tids[1])
        assert first is not None
        assert second is not None
        assert first.disposition_reason == "terminal"
        assert first.task_control_deleted_at_ns is not None
        assert second.summary_emitted_at_ns is not None
        assert second.disposition_at_ns is None
        assert second.task_control_deleted_at_ns is None
        assert cleanup.families_processed == 1
        assert cleanup.pending is True

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )

        second = store.get_task(tids[1])
        assert second is not None
        assert second.disposition_reason == "terminal"
        assert second.task_control_deleted_at_ns is not None
        assert cleanup.families_processed == 1
    finally:
        task.stop()

    for tid in tids:
        assert make_queue(f"T{tid}.ctrl_in").stats().total == 0
        assert make_queue(f"T{tid}.ctrl_out").stats().total == 0


def test_task_monitor_runtime_cleanup_runs_control_before_reserved_work(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT",
        2,
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS",
        60.0,
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS": "0.01",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tids = ("1778084345905438761", "1778084345905438762")
    for tid in tids:
        make_queue(f"T{tid}.ctrl_in").write(f"stop-{tid}")
        make_queue(f"T{tid}.ctrl_out").write(f"pong-{tid}")
    reserved = make_queue(f"T{tids[0]}.reserved")
    reserved.write("terminal-reserved")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999961761"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        updates = []
        for tid in tids:
            update = update_from_task_log_payload(
                {
                    "event": "work_completed",
                    "status": "completed",
                    "tid": tid,
                    "taskspec": {
                        "tid": tid,
                        "version": "1.0",
                        "name": "sample",
                        "io": {
                            "control": {
                                "ctrl_in": f"T{tid}.ctrl_in",
                                "ctrl_out": f"T{tid}.ctrl_out",
                            },
                        },
                        "state": {"status": "completed"},
                        "metadata": {},
                    },
                },
                message_id=int(tid),
            )
            assert update is not None
            updates.append(update)
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            tuple(updates),
            checkpoint_message_id=None,
        )
        for tid in tids:
            store.mark_summary_emitted(tid, int(tid) + 1)

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        first = store.get_task(tids[0])
        second = store.get_task(tids[1])
        assert first is not None
        assert second is not None
        assert first.task_control_deleted_at_ns is not None
        assert second.task_control_deleted_at_ns is not None
        assert cleanup.families_processed == 2
        assert cleanup.reserved_families_processed == 0
        assert cleanup.reserved_queues_deleted == 0
        assert cleanup.pending is True
        assert cleanup.next_slice_kind == "reserved"
        assert list(reserved.peek_generator()) == ["terminal-reserved"]

        reserved_cleanup = task._run_reserved_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )

        assert reserved_cleanup.reserved_families_processed == 1
        assert reserved_cleanup.reserved_queues_deleted == 1
        assert list(reserved.peek_generator()) == []
    finally:
        task.stop()


def test_task_monitor_runtime_cleanup_dispatches_three_cleanup_kinds(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT",
        3,
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    terminal_tid = "1778084345905438868"
    dead_tid = "1778084345905438869"
    make_queue(f"T{terminal_tid}.ctrl_in").write("stop")
    make_queue(f"T{terminal_tid}.ctrl_out").write("pong")
    reserved = make_queue(f"T{terminal_tid}.reserved")
    reserved.write("reserved")
    make_queue(f"T{dead_tid}.ctrl_in").write("dead-stop")
    make_queue(f"T{dead_tid}.ctrl_out").write("dead-pong")
    make_queue(f"T{dead_tid}.inbox").write("dead-input")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999961868"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": terminal_tid,
                "taskspec": {
                    "tid": terminal_tid,
                    "version": "1.0",
                    "name": "sample",
                    "io": {
                        "control": {
                            "ctrl_in": f"T{terminal_tid}.ctrl_in",
                            "ctrl_out": f"T{terminal_tid}.ctrl_out",
                        },
                    },
                    "state": {"status": "completed"},
                    "metadata": {},
                },
            },
            message_id=int(terminal_tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(terminal_tid, int(terminal_tid) + 1)

        terminal_cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        reserved_cleanup = task._run_reserved_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        dead_cleanup = task._run_dead_task_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
    finally:
        task.stop()

    assert terminal_cleanup.families_processed == 1
    assert terminal_cleanup.next_slice_kind == "reserved"
    assert reserved_cleanup.reserved_families_processed == 1
    assert reserved_cleanup.next_slice_kind == "dead_tid"
    assert dead_cleanup.dead_tids_processed == 1
    assert reserved.stats().total == 0
    assert make_queue(f"T{dead_tid}.ctrl_in").stats().total == 0
    assert make_queue(f"T{dead_tid}.ctrl_out").stats().total == 0
    assert make_queue(f"T{dead_tid}.inbox").stats().total == 0


def test_task_monitor_runtime_cleanup_skips_queue_snapshot_when_not_due(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999961869"),
        config=config,
    )

    def fail_snapshot(*args, **kwargs):
        del args
        del kwargs
        raise AssertionError("queue snapshot should not run")

    monkeypatch.setattr(task, "_queue_name_snapshot", fail_snapshot)
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
            previous_queue_cleanup_pending=False,
            queue_discovery_due_monotonic=time.monotonic() + 60.0,
        )
    finally:
        task.stop()

    assert cleanup.pending is False


def test_task_monitor_runtime_cleanup_keeps_reserved_pending_after_control_budget(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS": "0.01",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT": 1,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    control_tid = "1778084345905438851"
    reserved_tid = "1778084345905438859"
    make_queue(f"T{control_tid}.ctrl_in").write("stop")
    make_queue(f"T{control_tid}.ctrl_out").write("pong")
    reserved = make_queue(f"T{reserved_tid}.reserved")
    reserved.write("stale-reserved")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999961851"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": control_tid,
                "taskspec": {
                    "tid": control_tid,
                    "version": "1.0",
                    "name": "sample",
                    "io": {
                        "control": {
                            "ctrl_in": f"T{control_tid}.ctrl_in",
                            "ctrl_out": f"T{control_tid}.ctrl_out",
                        },
                    },
                    "state": {"status": "completed"},
                    "metadata": {},
                },
            },
            message_id=int(control_tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(control_tid, int(control_tid) + 1)

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )

        assert cleanup.families_processed == 1
        assert cleanup.reserved_families_processed == 0
        assert cleanup.pending is True
        assert cleanup.family_limit_hit is False
        assert cleanup.next_slice_kind == "reserved"
        assert list(reserved.peek_generator()) == ["stale-reserved"]

        reserved_cleanup = task._run_reserved_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        assert reserved_cleanup.reserved_families_processed == 1
        assert list(reserved.peek_generator()) == []
    finally:
        task.stop()


def test_task_monitor_runtime_cleanup_starts_after_slow_queue_snapshot(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS",
        1.0,
    )
    current_monotonic = 0.0

    def fake_monotonic() -> float:
        return current_monotonic

    monkeypatch.setattr(task_monitor_mod, "_monitor_monotonic", fake_monotonic)
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    now_ns = time.time_ns()
    tid = str(
        now_ns - int((TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS + 60.0) * 1e9)
    )
    ctrl_in = make_queue(f"T{tid}.ctrl_in")
    ctrl_in.write("stop")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(now_ns + 1_000_000)),
        config=config,
    )
    real_snapshot = task._queue_name_snapshot

    def slow_snapshot(*args, **kwargs):
        nonlocal current_monotonic
        names = real_snapshot(*args, **kwargs)
        current_monotonic += 2.0
        return names

    monkeypatch.setattr(task, "_queue_name_snapshot", slow_snapshot)
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        cleanup = task._run_dead_task_cleanup_slice(
            store,
            now_ns=now_ns,
        )
    finally:
        task.stop()

    assert cleanup.dead_tids_processed == 1
    assert cleanup.dead_tid_control_queues_deleted == 1
    assert ctrl_in.stats().total == 0


def test_task_monitor_runtime_cleanup_deadline_stops_between_families(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS",
        1.0,
    )
    monotonic_calls = 0

    def fake_monotonic() -> float:
        nonlocal monotonic_calls
        monotonic_calls += 1
        return 0.0 if monotonic_calls <= 3 else 2.0

    monkeypatch.setattr(task_monitor_mod, "_monitor_monotonic", fake_monotonic)
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS": "0.01",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tids = ("1778084345905438771", "1778084345905438772")
    updates = []
    for tid in tids:
        taskspec = {
            "tid": tid,
            "version": "1.0",
            "name": "sample",
            "io": {
                "control": {
                    "ctrl_in": f"T{tid}.ctrl_in",
                    "ctrl_out": f"T{tid}.ctrl_out",
                },
            },
            "state": {"status": "completed"},
            "metadata": {},
        }
        update = update_from_task_log_payload(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": tid,
                "taskspec": taskspec,
            },
            message_id=int(tid),
        )
        assert update is not None
        updates.append(update)
        make_queue(f"T{tid}.ctrl_in").write(f"stop-{tid}")
        make_queue(f"T{tid}.ctrl_out").write(f"pong-{tid}")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999961771"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            tuple(updates),
            checkpoint_message_id=None,
        )
        for tid in tids:
            store.mark_summary_emitted(tid, int(tid) + 1)

        cleanup = task._run_terminal_control_cleanup_slice(
            store,
            now_ns=time.time_ns(),
        )
        first = store.get_task(tids[0])
        second = store.get_task(tids[1])
        assert first is not None
        assert first.task_control_deleted_at_ns is not None
        assert second is not None
        assert second.task_control_deleted_at_ns is None
        assert cleanup.families_processed == 1
        assert cleanup.pending is True
        assert cleanup.deadline_hit is True
    finally:
        task.stop()


def test_task_monitor_raw_store_delete_reconciles_ingested_open_refs(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438762"
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write("present-running")
    message_id = max(
        int(row_message_id)
        for body, row_message_id in iter_queue_entries(log_queue)
        if body == "present-running"
    )
    running = update_from_task_log_payload(
        {
            "event": "task_activity",
            "status": "running",
            "tid": tid,
            "taskspec": {
                "tid": tid,
                "version": "1.0",
                "name": "sample",
                "io": {},
                "state": {"status": "running"},
                "metadata": {},
            },
        },
        message_id=message_id,
    )
    assert running is not None

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999961762"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (running,),
            checkpoint_message_id=None,
        )

        retired = task._delete_monitor_store_task_log_rows(store)

        record = store.get_task(tid)
        assert record is not None
        assert retired.message_rows_deleted == 1
        assert record.raw_deleted_at_ns is not None
        assert all(
            body != "present-running"
            for body, _message_id in iter_queue_entries(log_queue)
        )
        assert (
            store.list_deletable_task_log_messages(limit=10, require_summary=False)
            == ()
        )
        assert task._last_collation_store_error is None
    finally:
        task.stop()


def test_task_monitor_raw_store_delete_reconciles_missing_refs_without_stall(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS",
        60.0,
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438763"
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write("present-terminal")
    present_id = max(
        int(message_id)
        for body, message_id in iter_queue_entries(log_queue)
        if body == "present-terminal"
    )
    missing_id = present_id + 100_000
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "sample",
        "io": {
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "completed"},
        "metadata": {},
    }
    start = update_from_task_log_payload(
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": taskspec,
        },
        message_id=missing_id,
    )
    terminal = update_from_task_log_payload(
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "taskspec": taskspec,
        },
        message_id=present_id,
    )
    assert start is not None
    assert terminal is not None

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999961763"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (start, terminal),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(tid, present_id + 1)

        retired = task._delete_monitor_store_task_log_rows(store)

        assert retired.message_rows_deleted == 2
        assert all(
            body != "present-terminal"
            for body, _message_id in iter_queue_entries(log_queue)
        )
        assert (
            store.list_deletable_task_log_messages(limit=10, require_summary=True) == ()
        )
        assert task._last_collation_store_error is None
    finally:
        task.stop()


def test_task_monitor_terminal_control_cleanup_worker_does_not_block_ping(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT": 1,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999964")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    task = TaskMonitor(db_path, spec, config=config)
    started = threading.Event()
    release = threading.Event()

    def slow_cleanup_worker(work):
        started.set()
        assert release.wait(timeout=5.0)
        return task_monitor_mod._TaskControlCleanupWorkerResult(
            work=work,
            cleanup=task_monitor_mod._TaskControlCleanupResult(),
        )

    monkeypatch.setattr(
        task,
        "_run_terminal_control_cleanup_worker",
        slow_cleanup_worker,
    )
    try:
        task._maybe_start_terminal_control_cleanup_worker(now_ns=time.time_ns())
        assert started.wait(timeout=10.0)
        assert task._control_cleanup_work_in_flight is not None

        for _ in range(3):
            task.process_once()

        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "during"}))
        pong = None
        deadline = time.monotonic() + 3.0
        while pong is None and time.monotonic() < deadline:
            task.wait_for_activity(timeout=min(0.1, task.next_wait_timeout()))
            task.process_once()
            responses = [json.loads(item) for item in ctrl_out.peek_generator()]
            pong = next(
                (
                    response
                    for response in responses
                    if response["command"] == CONTROL_PING
                    and response.get("request_id") == "during"
                ),
                None,
            )
        assert pong is not None
        assert pong["message"] == "PONG"
        assert pong["control_cleanup_in_flight"] is True
        assert (
            pong[PONG_EXTENSION_KEY]["task_monitor"]["last_cycle"][
                "control_cleanup_in_flight"
            ]
            is True
        )

        release.set()
        drive_task_monitor_until_idle(task)
        assert task._control_cleanup_work_in_flight is None
    finally:
        release.set()
        task.stop()


def test_task_monitor_ignores_service_worker_sentinel_before_cleanup_result(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999966")
    task = TaskMonitor(db_path, spec, config=config)
    work = task_monitor_mod._TaskControlCleanupWork(
        request_id="cleanup-race",
        now_ns=time.time_ns(),
    )
    task._service_lane_work_items[
        task_monitor_mod.TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE
    ] = work

    try:
        task._handle_worker_result(
            base_task_mod.TaskWorkerResult(
                lane=task_monitor_mod.TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE,
                value=service_task_mod._service_worker_thread_done,
                error=None,
            )
        )

        assert task._control_cleanup_work_in_flight is work
        assert task._last_control_delete_errors == ()

        task._handle_worker_result(
            base_task_mod.TaskWorkerResult(
                lane=task_monitor_mod.TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE,
                value=service_task_mod.ServiceWorkerEvent(
                    name=task_monitor_mod.TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE,
                    request_id=work.request_id,
                    worker_index=0,
                    kind="result",
                    value=task_monitor_mod._TaskControlCleanupWorkerResult(
                        work=work,
                        cleanup=task_monitor_mod._TaskControlCleanupResult(
                            dead_tids_processed=1,
                        ),
                    ),
                ),
                error=None,
            )
        )

        assert task._control_cleanup_work_in_flight is None
        assert task._last_control_delete_errors == ()
    finally:
        task.stop()


def test_task_monitor_slow_builtin_cycle_does_not_block_ping(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "report_only",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999965")
    make_queue(WEFT_GLOBAL_LOG_QUEUE).write(
        json.dumps({"event": "work_started", "tid": "1778084345905438752"})
    )
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    task = TaskMonitor(db_path, spec, config=config)
    started = threading.Event()
    release = threading.Event()
    real_cleanup = task._run_task_monitor_cleanup_cycle

    def slow_cleanup(*args: object, **kwargs: object) -> TaskMonitorProcessorResult:
        started.set()
        assert release.wait(timeout=5.0)
        return real_cleanup(*args, **kwargs)

    monkeypatch.setattr(task, "_run_task_monitor_cleanup_cycle", slow_cleanup)
    try:
        deadline = time.monotonic() + 10.0
        while not started.is_set() and time.monotonic() < deadline:
            task.process_once()
            if task._builtin_cycle_work_in_flight is None:
                task._next_cycle_due_monotonic = 0.0
            task.wait_for_activity(timeout=0.05)
        assert started.wait(timeout=0.1)
        assert task._builtin_cycle_work_in_flight is not None

        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "during"}))
        pong = None
        deadline = time.monotonic() + 3.0
        while pong is None and time.monotonic() < deadline:
            task.wait_for_activity(timeout=min(0.1, task.next_wait_timeout()))
            task.process_once()
            responses = [json.loads(item) for item in ctrl_out.peek_generator()]
            pong = next(
                (
                    response
                    for response in responses
                    if response["command"] == CONTROL_PING
                    and response.get("request_id") == "during"
                ),
                None,
            )
        assert pong is not None
        assert pong["status"] == "ok"
        assert pong["message"] == "PONG"
        assert pong["builtin_cycle_in_flight"] is True
        assert (
            pong[PONG_EXTENSION_KEY]["task_monitor"]["last_cycle"][
                "builtin_cycle_in_flight"
            ]
            is True
        )
        assert task._last_processor_success is None

        task._wake_requested = True
        assert task.next_wait_timeout() == TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS

        release.set()
        drive_task_monitor_until_idle(task)
        assert task._last_processor_success is True
    finally:
        release.set()
        task.stop()


def test_task_monitor_terminal_control_cleanup_worker_error_is_retryable(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS",
        30.0,
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS": "0.01",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    tid = "1778084345905438752"
    taskspec = {
        "tid": tid,
        "version": "1.0",
        "name": "sample",
        "io": {
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {"status": "completed"},
        "metadata": {},
    }
    make_queue(f"T{tid}.ctrl_in").write("stop")
    make_queue(f"T{tid}.ctrl_out").write("pong")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999963"),
        config=config,
    )
    real_delete = task._delete_terminal_control_queues

    def fail_delete(record, **kwargs):
        del record
        del kwargs
        raise RuntimeError("control delete boom")

    monkeypatch.setattr(task, "_delete_terminal_control_queues", fail_delete)
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        update = update_from_task_log_payload(
            {
                "event": "work_completed",
                "status": "completed",
                "tid": tid,
                "taskspec": taskspec,
            },
            message_id=int(tid),
        )
        assert update is not None
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            (update,),
            checkpoint_message_id=None,
        )
        store.mark_summary_emitted(tid, int(tid) + 1)

        failed_result = task._run_terminal_control_cleanup_worker(
            task_monitor_mod._TaskControlCleanupWork(
                request_id=f"{tid}:failing-control-cleanup",
                now_ns=time.time_ns(),
            )
        )
        assert failed_result.cleanup.errors == ("control delete boom",)
        assert failed_result.cleanup.pending is True
        record = store.get_task(tid)
        assert record is not None
        assert record.task_control_deleted_at_ns is None
        assert record.disposition_at_ns is None

        monkeypatch.setattr(task, "_delete_terminal_control_queues", real_delete)
        recovered_result = task._run_terminal_control_cleanup_worker(
            task_monitor_mod._TaskControlCleanupWork(
                request_id=f"{tid}:retry-control-cleanup",
                now_ns=time.time_ns(),
            )
        )
        assert recovered_result.cleanup.errors == ()

        record = store.get_task(tid)
        assert record is not None
        assert record.task_control_deleted_at_ns is not None
        assert record.disposition_at_ns is not None
        assert make_queue(f"T{tid}.ctrl_in").stats().total == 0
        assert make_queue(f"T{tid}.ctrl_out").stats().total == 0
    finally:
        task.stop()


def test_task_monitor_collated_external_failure_blocks_processor_delete(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(tmp_path),
            "WEFT_LOG_TASKS_EXTERNAL_ENABLED": "1",
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1778084345905438731",
    }
    log_queue.write(json.dumps(payload))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999969"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        store = task._monitor_store
        assert store is not None
        record = store.get_task(payload["tid"])
        assert record is not None
        assert record.summary_emitted_at_ns is None
        assert record.raw_deleted_at_ns is not None
    finally:
        task.stop()

    target_rows = [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == payload["tid"]
    ]
    assert target_rows == []
    assert task._external_task_log_status.healthy is False
    assert task._external_task_log_status.last_blocked_deletions == 1


def test_task_monitor_raw_external_logs_and_deletes_without_store(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "raw-task-log.jsonl"
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_ENABLED": "1",
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "raw",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1778084345905438732",
    }
    log_queue.write(json.dumps(payload))
    log_queue.write("{not-json")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999968"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        assert task._monitor_store is None
    finally:
        task.stop()

    remaining = list(log_queue.peek_generator())
    assert "{not-json" not in remaining
    assert all(
        json.loads(message).get("tid") != payload["tid"]
        for message in remaining
        if message.startswith("{")
    )
    records = [
        json.loads(line)
        for line in external_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["record_type"] for record in records] == [
        "task_log_raw",
        "task_log_raw",
    ]
    assert records[0]["payload"]["tid"] == payload["tid"]
    assert records[1]["malformed_reason"] == "invalid_json"


def test_task_monitor_ping_uses_cached_policy_stats_without_cleanup_scan(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "delete",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999994")
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write("{not-json")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    task = TaskMonitor(db_path, spec, config=config)
    responses: list[dict[str, object]] = []
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        cached_policy_stats = list(task._last_cleanup_policy_stats)
        assert cached_policy_stats

        def fail_cleanup(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise AssertionError("PING must not run cleanup")

        monkeypatch.setattr(task_monitor_mod, "run_task_monitor_cleanup", fail_cleanup)
        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "cached"}))
        task.wait_for_activity(timeout=task.next_wait_timeout())
        task.process_once()
        responses = [json.loads(item) for item in ctrl_out.peek_generator()]
    finally:
        task.stop()

    pong = next(
        response
        for response in responses
        if response["command"] == CONTROL_PING
        and response.get("request_id") == "cached"
    )
    assert pong["last_cleanup_policy_stats"] == cached_policy_stats
    assert (
        pong[PONG_EXTENSION_KEY]["task_monitor"]["last_cycle"]["cleanup_policy_stats"]
        == cached_policy_stats
    )
    assert "policy_progress" in pong[PONG_EXTENSION_KEY]["task_monitor"]["last_cycle"]


def test_task_monitor_slow_custom_processor_does_not_block_ping(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "custom",
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:blocking_processor",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999993")
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(json.dumps({"event": "work_started", "tid": "1778084345905438720"}))
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    task = TaskMonitor(db_path, spec, config=config)
    try:
        started_at = time.monotonic()
        task.process_once()
        elapsed = time.monotonic() - started_at

        assert elapsed < BLOCKING_PROCESSOR_TIMEOUT_SECONDS - 0.5
        assert BLOCKING_PROCESSOR_STARTED.wait(timeout=2.0)
        assert task._processor_work_in_flight is not None

        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "during"}))
        pong = None
        deadline = time.monotonic() + 3.0
        while pong is None and time.monotonic() < deadline:
            task.wait_for_activity(timeout=min(0.1, task.next_wait_timeout()))
            task.process_once()
            responses = [json.loads(item) for item in ctrl_out.peek_generator()]
            pong = next(
                (
                    response
                    for response in responses
                    if response["command"] == CONTROL_PING
                    and response.get("request_id") == "during"
                ),
                None,
            )
        assert pong is not None
        assert pong["status"] == "ok"
        assert pong["message"] == "PONG"
        assert pong["role"] == "task_monitor"
        assert pong["processor_in_flight"] is True
        assert (
            pong[PONG_EXTENSION_KEY]["task_monitor"]["last_cycle"][
                "processor_in_flight"
            ]
            is True
        )
        assert task._last_processor_success is None

        BLOCKING_PROCESSOR_RELEASE.set()
        deadline = time.monotonic() + 5.0
        while (
            task._processor_work_in_flight is not None and time.monotonic() < deadline
        ):
            task.process_once()
            task.wait_for_activity(timeout=0.05)

        assert task._processor_work_in_flight is None
        assert task._last_processor_success is True
        assert len(PROCESSOR_REQUESTS) == 1
    finally:
        BLOCKING_PROCESSOR_RELEASE.set()
        task.stop()


def test_task_monitor_failed_processor_does_not_advance_checkpoint(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "custom",
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:failing_processor",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(json.dumps({"event": "work_failed", "tid": "1778084345905438720"}))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999997"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)

        assert task._last_checkpoint is None
        assert task._last_processor_success is False
        assert task._last_error == "processor failed"
    finally:
        task.stop()


def test_task_monitor_heartbeat_failure_records_health_but_still_cycles(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env

    def fail_heartbeat(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("heartbeat unavailable")

    monkeypatch.setattr(task_monitor_mod, "upsert_heartbeat", fail_heartbeat)
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 10,
            "WEFT_TASK_MONITOR_MODE": "custom",
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:recording_processor",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(json.dumps({"event": "work_started", "tid": "1778084345905438720"}))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999996"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)

        assert len(PROCESSOR_REQUESTS) == 1
        assert task._last_processor_success is True
        assert (
            task._last_error == "heartbeat registration failed: heartbeat unavailable"
        )
    finally:
        task.stop()
