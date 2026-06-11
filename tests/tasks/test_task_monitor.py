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
    CONTROL_STATUS,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    PONG_EXTENSION_KEY,
    QUEUE_INTERNAL_RESERVED_SUFFIX,
    SERVICE_OWNER_SCHEMA,
    SERVICE_STATUS_ACTIVE,
    SERVICE_STATUS_TERMINAL,
    SERVICE_TYPE_MANAGED,
    SERVICE_TYPE_MANAGER,
    STALE_SERVICE_OWNER_DISPOSITION_REASONS,
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
    WEFT_STREAMING_SESSIONS_QUEUE,
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
from weft.core.pruning.runtime import (
    RuntimePruneConfig,
    run_runtime_prune_for_context,
)
from weft.core.service_convergence import (
    build_service_owner_payload,
    manager_service_key,
)
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
    timeout: float = 90.0,
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
        next_wait = task.next_wait_timeout()
        assert next_wait >= 0.0
        assert next_wait <= 0.201
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


def test_task_monitor_jsonl_then_delete_recovers_precheckpoint_service_rows(
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
            "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": 20,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    old_tid = "1779555792870776832"
    live_tid = "1779555792870776999"
    old_taskspec = make_task_monitor_taskspec(old_tid).model_dump(mode="python")
    old_taskspec["metadata"] = {
        **dict(old_taskspec.get("metadata", {})),
        "internal": True,
        "role": "task_monitor",
        INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
        INTERNAL_SERVICE_KEY_METADATA_KEY: INTERNAL_SERVICE_KEY_TASK_MONITOR,
        INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY: "ensure",
    }
    old_taskspec["state"] = {
        **dict(old_taskspec.get("state", {})),
        "status": "running",
        "started_at": int(old_tid),
    }
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    for event, status in (
        ("task_initialized", "created"),
        ("task_spawning", "spawning"),
        ("task_started", "running"),
    ):
        log_queue.write(
            json.dumps(
                {
                    "event": event,
                    "status": status,
                    "tid": old_tid,
                    "taskspec": {
                        **old_taskspec,
                        "state": {**old_taskspec["state"], "status": status},
                    },
                }
            )
        )
    message_ids = [message_id for _body, message_id in iter_queue_entries(log_queue)]

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999870"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        store.set_checkpoint(WEFT_GLOBAL_LOG_QUEUE, max(message_ids) + 1)
        make_queue(WEFT_SERVICES_REGISTRY_QUEUE).write(
            json.dumps(
                build_service_owner_payload(
                    service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
                    service_type=SERVICE_TYPE_MANAGED,
                    owner_tid=live_tid,
                    status=SERVICE_STATUS_ACTIVE,
                    name="task-monitor",
                    queues={
                        "ctrl_in": f"T{live_tid}.ctrl_in",
                        "ctrl_out": f"T{live_tid}.ctrl_out",
                        "inbox": f"T{live_tid}.inbox",
                        "outbox": f"T{live_tid}.outbox",
                    },
                    metadata={"manager_tid": "1779555792870777000"},
                )
            )
        )

        task.process_once()
        drive_task_monitor_until_idle(task)
        recovery = task._last_pre_checkpoint_task_log_recovery
        store = task._monitor_store
        assert store is not None
        assert store.deferred_write_status().pending == 0
    finally:
        task.stop()

    assert recovery.selected == 3
    assert recovery.valid_ingested == 3
    assert recovery.raw_delete_errors == ()
    assert [
        json.loads(body)
        for body, _message_id in iter_queue_entries(log_queue)
        if json.loads(body).get("tid") == old_tid
    ] == []
    reports = [
        json.loads(line)
        for line in external_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("subject", {}).get("tid") == old_tid
    ]
    summary_reports = [
        report
        for report in reports
        if report["report_kind"] == "monitor_store_stale_service_owner"
    ]
    assert len(summary_reports) == 1
    report = summary_reports[0]
    assert report["record_type"] == "task_lifetime_report"
    assert report["source_policy"] == TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE
    assert report["completeness"] == "collated"
    assert report["report_kind"] == "monitor_store_stale_service_owner"
    assert report["taskspec"]["tid"] == old_tid
    assert report["taskspec"]["state"]["status"] == "running"
    assert report["lifetime"]["close_reason"] == "stale_service_owner"
    assert report["monitor"]["collation_kind"] == "internal_service"
    assert report["monitor"]["service"]["service_key"] == (
        INTERNAL_SERVICE_KEY_TASK_MONITOR
    )
    assert "collation" not in report
    assert "effect" not in report


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


def test_task_monitor_jsonl_then_delete_disposes_stale_service_owner(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Stale service-owner disposition must fire in jsonl_then_delete mode.

    Unlike test_task_monitor_disposes_old_stale_service_owner_collation,
    this drives the real cycle entry point (``process_once``) instead of
    calling ``_emit_monitor_store_summaries(apply_disposition=True)``
    directly, so the ``_run_monitor_store_cycle`` disposition gate is
    actually exercised.

    Verifies:
    - The superseded manager collation gains a stale service-owner
      disposition through the full builtin cycle. The live row is retired
      once converged, so the disposition value is asserted from the durable
      external JSONL audit reports (the runtime cleanup report copies
      ``record.disposition_reason`` into its close reason, and the cleanup
      plan only fires for stale service-owner dispositions).
    - The stale owner's standard control queues are deleted.
    - The disposed family is retired from the Monitor store.

    Spec: [MF-5]
    """

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
    tid = "1778084345905438748"
    active_tid = "1778084345905438756"
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
    ctrl_in.write("stale-ping")
    ctrl_out.write("stale-pong")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999940"),
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

        def stale_owner_converged() -> bool:
            return (
                ctrl_in.stats().total == 0
                and ctrl_out.stats().total == 0
                and store.get_task(tid) is None
            )

        drive_task_monitor_until(task, stale_owner_converged)
    finally:
        task.stop()

    assert ctrl_in.stats().total == 0
    assert ctrl_out.stats().total == 0
    reports = [
        json.loads(line)
        for line in external_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("subject", {}).get("tid") == tid
    ]
    summary_reports = [
        report
        for report in reports
        if report["report_kind"] == "monitor_store_stale_service_owner"
    ]
    assert len(summary_reports) == 1
    assert summary_reports[0]["lifetime"]["close_reason"] == "stale_service_owner"
    cleanup_reports = [
        report
        for report in reports
        if report["report_kind"] == "terminal_runtime_cleanup"
    ]
    assert len(cleanup_reports) == 1
    cleanup_report = cleanup_reports[0]
    assert (
        cleanup_report["lifetime"]["close_reason"]
        in STALE_SERVICE_OWNER_DISPOSITION_REASONS
    )
    assert cleanup_report["observations"]["queue_names"] == [
        f"T{tid}.ctrl_in",
        f"T{tid}.ctrl_out",
    ]


def test_stale_service_owner_disposes_after_maintenance_prune(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Disposition still fires after maintenance pruned the old-owner row.

    Production interleaving: the monitor's runtime-state maintenance prunes
    the service registry to newest-per-key many times before a dead
    service's disposition window opens, so by the time the stale
    service-owner classifier looks for proof the superseded old-owner
    registry row is already gone and only the live different-owner row
    remains. Pin that sequence with a real ``run_runtime_prune_for_context``
    apply pass over the services group (the same entry point the monitor's
    maintenance slice calls) instead of a pre-pruned fixture, then drive
    the full builtin cycle exactly like
    ``test_task_monitor_jsonl_then_delete_disposes_stale_service_owner``.

    Verifies:
    - The services-group prune deletes exactly the superseded old-owner
      registry row and keeps the live different-owner row.
    - The stale family still gains a stale service-owner disposition
      proved by the surviving live row alone, asserted from the durable
      external JSONL audit reports.
    - The stale owner's standard control queues are deleted.
    - The disposed family is retired from the Monitor store.

    Spec: [MF-5]
    """

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
    tid = "1778084345905438901"
    active_tid = "1778084345905438909"
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
    ctrl_in.write("stale-ping")
    ctrl_out.write("stale-pong")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999941"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        services = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
        service_key = manager_service_key(task._monitor_context())
        superseded_owner_id = _write_json_row(
            services,
            _maintenance_service_owner_payload(
                service_key=service_key,
                tid=tid,
                status=SERVICE_STATUS_TERMINAL,
            ),
        )
        live_owner_id = _write_json_row(
            services,
            _maintenance_service_owner_payload(
                service_key=service_key,
                tid=active_tid,
                status=SERVICE_STATUS_ACTIVE,
            ),
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

        prune_result = run_runtime_prune_for_context(
            task._monitor_context(),
            RuntimePruneConfig(
                apply=True,
                queues=("services",),
                # RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS binds into the
                # dataclass default at class-definition time, so the 3600s
                # floor must be overridden explicitly here for the freshly
                # seeded registry rows to be prune candidates at all.
                min_age_seconds=0.0,
            ),
        )
        assert prune_result.exit_code == 0
        assert prune_result.deleted == 1
        remaining_service_ids = set(_queue_json_rows(services))
        assert superseded_owner_id not in remaining_service_ids
        assert live_owner_id in remaining_service_ids

        def stale_owner_converged() -> bool:
            return (
                ctrl_in.stats().total == 0
                and ctrl_out.stats().total == 0
                and store.get_task(tid) is None
            )

        drive_task_monitor_until(task, stale_owner_converged)
    finally:
        task.stop()

    assert ctrl_in.stats().total == 0
    assert ctrl_out.stats().total == 0
    reports = [
        json.loads(line)
        for line in external_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("subject", {}).get("tid") == tid
    ]
    summary_reports = [
        report
        for report in reports
        if report["report_kind"] == "monitor_store_stale_service_owner"
    ]
    assert len(summary_reports) == 1
    assert summary_reports[0]["lifetime"]["close_reason"] == "stale_service_owner"
    cleanup_reports = [
        report
        for report in reports
        if report["report_kind"] == "terminal_runtime_cleanup"
    ]
    assert len(cleanup_reports) == 1
    cleanup_report = cleanup_reports[0]
    assert (
        cleanup_report["lifetime"]["close_reason"]
        in STALE_SERVICE_OWNER_DISPOSITION_REASONS
    )
    assert cleanup_report["observations"]["queue_names"] == [
        f"T{tid}.ctrl_in",
        f"T{tid}.ctrl_out",
    ]


def test_stale_service_owner_disposes_only_after_retention_window(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The retention window gates stale service-owner disposition timing.

    The sibling disposition tests disable the candidate window with
    ``WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS: "0.000001"``; production
    runs the 48h default, so a freshly dead service is intentionally NOT
    disposable until the window passes
    (``list_stale_service_owner_candidates`` bounds ``last_message_id``
    by ``now - retention``). Pin that timeline under a real 3600s window
    with two stale service families whose fabricated message ids are
    backdated relative to the wall clock: a manager-role family ~600s old
    (inside the window) and a heartbeat-role family ~7200s old (outside
    it), each with a live different-owner registry row for its service
    key, driven through ONE real builtin-cycle drive.

    Verifies:
    - The outside-window family is disposed (JSONL summary + runtime
      cleanup reports), its control queues are deleted, and its family is
      retired from the Monitor store.
    - The inside-window family stays open: no disposition mark, no
      summary, no JSONL reports, and its control queues survive intact.

    Spec: [MF-5]
    """

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
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "3600",
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    now_ns = time.time_ns()
    inside_tid = str(now_ns - 600 * 10**9)
    outside_tid = str(now_ns - 7200 * 10**9)
    live_manager_tid = str(now_ns - 1)
    live_heartbeat_tid = str(now_ns - 2)
    inside_taskspec = {
        "tid": inside_tid,
        "version": "1.0",
        "name": "manager",
        "io": {
            "control": {
                "ctrl_in": f"T{inside_tid}.ctrl_in",
                "ctrl_out": f"T{inside_tid}.ctrl_out",
            },
        },
        "state": {"status": "running"},
        "metadata": {"role": "manager"},
    }
    outside_taskspec = {
        "tid": outside_tid,
        "version": "1.0",
        "name": "heartbeat",
        "io": {
            "control": {
                "ctrl_in": f"T{outside_tid}.ctrl_in",
                "ctrl_out": f"T{outside_tid}.ctrl_out",
            },
        },
        "state": {"status": "running"},
        "metadata": {"role": "heartbeat_service"},
    }
    inside_ctrl_in = make_queue(f"T{inside_tid}.ctrl_in")
    inside_ctrl_out = make_queue(f"T{inside_tid}.ctrl_out")
    inside_ctrl_in.write("stale-ping")
    inside_ctrl_out.write("stale-pong")
    outside_ctrl_in = make_queue(f"T{outside_tid}.ctrl_in")
    outside_ctrl_out = make_queue(f"T{outside_tid}.ctrl_out")
    outside_ctrl_in.write("stale-ping")
    outside_ctrl_out.write("stale-pong")

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec("1778089999999999942"),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        services = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
        services.write(
            json.dumps(
                {
                    "schema": SERVICE_OWNER_SCHEMA,
                    "service_key": manager_service_key(task._monitor_context()),
                    "service_type": SERVICE_TYPE_MANAGER,
                    "owner_tid": live_manager_tid,
                    "status": SERVICE_STATUS_ACTIVE,
                }
            )
        )
        services.write(
            json.dumps(
                {
                    "schema": SERVICE_OWNER_SCHEMA,
                    "service_key": INTERNAL_SERVICE_KEY_HEARTBEAT,
                    "service_type": SERVICE_TYPE_MANAGED,
                    "owner_tid": live_heartbeat_tid,
                    "status": SERVICE_STATUS_ACTIVE,
                }
            )
        )
        updates = []
        for family_tid, family_taskspec in (
            (inside_tid, inside_taskspec),
            (outside_tid, outside_taskspec),
        ):
            update = update_from_task_log_payload(
                {
                    "event": "work_started",
                    "status": "running",
                    "tid": family_tid,
                    "taskspec": family_taskspec,
                },
                message_id=int(family_tid),
            )
            assert update is not None
            updates.append(update)
        store.record_task_log_updates(
            WEFT_GLOBAL_LOG_QUEUE,
            tuple(updates),
            checkpoint_message_id=None,
        )

        def outside_family_converged() -> bool:
            return (
                outside_ctrl_in.stats().total == 0
                and outside_ctrl_out.stats().total == 0
                and store.get_task(outside_tid) is None
            )

        drive_task_monitor_until(task, outside_family_converged)

        inside_record = store.get_task(inside_tid)
        assert inside_record is not None
        assert inside_record.disposition_at_ns is None
        assert inside_record.summary_emitted_at_ns is None
    finally:
        task.stop()

    assert outside_ctrl_in.stats().total == 0
    assert outside_ctrl_out.stats().total == 0
    assert inside_ctrl_in.stats().total == 1
    assert inside_ctrl_out.stats().total == 1
    lines = external_path.read_text(encoding="utf-8").splitlines()
    outside_reports = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("subject", {}).get("tid") == outside_tid
    ]
    summary_reports = [
        report
        for report in outside_reports
        if report["report_kind"] == "monitor_store_stale_service_owner"
    ]
    assert len(summary_reports) == 1
    assert summary_reports[0]["lifetime"]["close_reason"] == "stale_service_owner"
    cleanup_reports = [
        report
        for report in outside_reports
        if report["report_kind"] == "terminal_runtime_cleanup"
    ]
    assert len(cleanup_reports) == 1
    cleanup_report = cleanup_reports[0]
    assert (
        cleanup_report["lifetime"]["close_reason"]
        in STALE_SERVICE_OWNER_DISPOSITION_REASONS
    )
    assert cleanup_report["observations"]["queue_names"] == [
        f"T{outside_tid}.ctrl_in",
        f"T{outside_tid}.ctrl_out",
    ]
    inside_reports = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("subject", {}).get("tid") == inside_tid
    ]
    assert inside_reports == []


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


# ---------------------------------------------------------------------------
# WS-A Task A2 escalating-fidelity ladder
# (docs/plans/2026-06-10-self-healing-runtime-maintenance-plan.md, Task A2)
#
# Production defect signature under reproduction (Postgres, weft 0.9.75,
# mode jsonl_then_delete): collation families marked ``raw_deleted_at_ns``
# while their raw ``weft.log.tasks`` rows still exist, store refs present
# for ~all raw rows, zero raw rows ever deleted, and no recorded errors.
#
# Every rung drives the REAL ``_run_monitor_store_cycle`` entry point (the
# same call the builtin cycle worker makes) with injected ``now_ns`` and
# asserts the raw-deleted oracle at EVERY cycle boundary, not just the end.
# ---------------------------------------------------------------------------


def _jsonl_lifecycle_config(
    external_path: object,
    *,
    batch_size: int = 4,
    scan_limit: int = 4,
    retention_seconds: str = "172800",
) -> dict[str, Any]:
    """Build production-shaped jsonl_then_delete monitor config.

    Mirrors production defaults where they matter (48h retention, collated
    external mode) while shrinking batch/scan limits so a small seeded
    backlog spans multiple ingest windows like production's multi-day one.
    """

    return load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_BATCH_SIZE": batch_size,
            "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": scan_limit,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": retention_seconds,
            "WEFT_LOG_TASKS_EXTERNAL_PATH": str(external_path),
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )


def _broker_rows_by_tid(log_queue: Any) -> dict[str, set[int]]:
    """Return live ``weft.log.tasks`` row IDs grouped by payload tid."""

    # Exact top-level tid match — do not weaken to body-substring matching;
    # service-event bodies embed child tids (docs/lessons.md, 2026-06-11).
    rows: dict[str, set[int]] = {}
    for body, message_id in iter_queue_entries(log_queue):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        tid = payload.get("tid")
        if isinstance(tid, str) and tid:
            rows.setdefault(tid, set()).add(int(message_id))
    return rows


def _live_monitor_store_refs(store: MonitorStore) -> tuple[Any, ...]:
    """Return all live (non-tombstoned) Monitor-store raw-message refs.

    Unmarked families come from the deletable listing with no summary or
    state filter; marked families come from the repair listing. Together
    they cover every live ref that has a collation parent.
    """

    unmarked = store.list_deletable_task_log_messages(
        limit=1000,
        require_summary=False,
    )
    marked = store.list_raw_deleted_task_message_refs(limit=1000)
    return (*unmarked, *marked)


def _assert_raw_deleted_oracle(
    store: MonitorStore,
    log_queue: Any,
    tids: tuple[str, ...],
    *,
    cycle: str,
) -> None:
    """Assert the raw-deleted invariant the production defect violates.

    Verifies, against the REAL queue (rows carry their tid in the JSON
    body) and the real Monitor store:
    - no collation family carries ``raw_deleted_at_ns`` while raw broker
      rows for its tid remain in ``weft.log.tasks`` (vacuous marking);
    - a family with no collation record only has rows AHEAD of the durable
      checkpoint (rows behind it were seen by ingest, so a missing record
      means the family was retired while its raw rows survive); and
    - every live store ref points at an existing raw broker row.

    Spec: [MF-5], [OBS.17]
    """

    broker_rows = _broker_rows_by_tid(log_queue)
    checkpoint = store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE)
    for tid in tids:
        record = store.get_task(tid)
        present = sorted(broker_rows.get(tid, set()))
        if record is None:
            seen_by_ingest = [
                message_id
                for message_id in present
                if checkpoint is not None and message_id <= checkpoint
            ]
            assert not seen_by_ingest, (
                f"{cycle}: family {tid} has no collation record while "
                f"already-ingested raw broker rows {seen_by_ingest} "
                f"(checkpoint {checkpoint}) still exist"
            )
            continue
        if record.raw_deleted_at_ns is not None:
            assert not present, (
                f"{cycle}: family {tid} is marked raw_deleted_at_ns="
                f"{record.raw_deleted_at_ns} while raw broker rows "
                f"{present} still exist"
            )
    for ref in _live_monitor_store_refs(store):
        if ref.tid not in tids:
            continue
        assert ref.message_id in broker_rows.get(ref.tid, set()), (
            f"{cycle}: store ref {ref.tid}:{ref.message_id} has no live "
            "raw broker row backing it"
        )


def _run_oracle_checked_store_cycle(
    task: TaskMonitor,
    store: MonitorStore,
    log_queue: Any,
    tids: tuple[str, ...],
    *,
    now_ns: int,
    cycle: str,
) -> Any:
    """Run one real collated-store cycle and assert the oracle afterward.

    Returns the cycle's retained-ingest result so callers can pin the
    catch-up/high-water progression. Production showed no errors anywhere,
    so any recorded store or delete error fails the rung immediately.
    """

    task._run_monitor_store_cycle(
        now_ns=now_ns,
        task_log_owner="collated_store",
        start_control_cleanup=False,
    )
    ingest = task._last_retained_task_log_ingest
    assert task._last_collation_store_error is None, (
        f"{cycle}: unexpected collation store error: {task._last_collation_store_error}"
    )
    assert ingest.store_write_errors == (), f"{cycle}: {ingest.store_write_errors}"
    assert ingest.raw_delete_errors == (), f"{cycle}: {ingest.raw_delete_errors}"
    _assert_raw_deleted_oracle(store, log_queue, tids, cycle=cycle)
    return ingest


def _seed_terminal_family_backlog(
    log_queue: Any,
    tids: tuple[str, ...],
) -> None:
    """Seed multi-row terminal families whose rows span ingest windows.

    All start rows are written before all terminal rows so each family's
    rows land in different FIFO scan windows once the scan limit is smaller
    than the backlog (production families accumulated the same way).
    """

    for sequence, event, status in (
        (1, "task_activity", "running"),
        (2, "work_completed", "completed"),
    ):
        for tid in tids:
            log_queue.write(
                json.dumps(
                    {
                        "event": event,
                        "status": status,
                        "tid": tid,
                        "sequence": sequence,
                    }
                )
            )


def _seed_backdated_terminal_family_backlog(
    log_queue: Any,
    tids: tuple[str, ...],
    *,
    terminal_event: str,
    terminal_status: str,
    completed_at_ns: int,
    started_at_ns: int,
) -> None:
    """Seed two-row terminal families with backdated completion timestamps.

    Same two-row shape and start-rows-then-terminal-rows write order as
    ``_seed_terminal_family_backlog`` (families span FIFO scan windows like
    production), but each row nests ``started_at``/``completed_at`` under
    ``taskspec.state`` the way production ``_report_state_change`` payloads
    carry them. Collation's state summary reads ``payload["taskspec"]["state"]``
    plus top-level scalar keys (``update_from_task_log_payload``) — a bare
    top-level ``"state"`` key would be ignored — so this nesting is what makes
    ``completed_at_ns`` land on the collation record and backdates the
    retention-cutoff COALESCE without touching broker message ids (real queue
    writes always get current ids).
    """

    for sequence, event, status, state in (
        (1, "task_activity", "running", {"started_at": started_at_ns}),
        (
            2,
            terminal_event,
            terminal_status,
            {"started_at": started_at_ns, "completed_at": completed_at_ns},
        ),
    ):
        for tid in tids:
            log_queue.write(
                json.dumps(
                    {
                        "event": event,
                        "status": status,
                        "tid": tid,
                        "sequence": sequence,
                        "taskspec": {"state": state},
                    }
                )
            )


def _seeded_rows_remaining(log_queue: Any, tids: tuple[str, ...]) -> bool:
    """Return whether any seeded family still has live raw broker rows.

    The monitor's own open-family rows stay in ``weft.log.tasks`` by
    design, so convergence is judged on the seeded tids only.
    """

    broker_rows = _broker_rows_by_tid(log_queue)
    return any(broker_rows.get(tid) for tid in tids)


def _seeded_refs_remaining(store: MonitorStore, tids: tuple[str, ...]) -> bool:
    """Return whether any seeded family still has live Monitor-store refs."""

    seeded = set(tids)
    return any(ref.tid in seeded for ref in _live_monitor_store_refs(store))


def _external_report_tids(external_path: Any) -> list[str]:
    """Return subject tids of task lifetime reports in the JSONL sink."""

    if not external_path.exists():
        return []
    tids: list[str] = []
    for line in external_path.read_text(encoding="utf-8").splitlines():
        report = json.loads(line)
        if report.get("record_type") != "task_lifetime_report":
            continue
        subject = report.get("subject")
        if isinstance(subject, dict) and isinstance(subject.get("tid"), str):
            tids.append(subject["tid"])
    return tids


def _assert_jsonl_lifecycle_converged(
    store: MonitorStore,
    log_queue: Any,
    external_path: Any,
    tids: tuple[str, ...],
) -> None:
    """Assert full convergence: rows deleted, refs gone, reports exported."""

    broker_rows = _broker_rows_by_tid(log_queue)
    for tid in tids:
        assert broker_rows.get(tid, set()) == set(), (
            f"family {tid} still has raw rows {sorted(broker_rows[tid])} "
            "after convergence"
        )
        record = store.get_task(tid)
        if record is not None:
            assert record.raw_deleted_at_ns is not None
            assert record.summary_emitted_at_ns is not None
            assert record.disposition_at_ns is not None
            assert record.task_control_deleted_at_ns is not None
    live_refs = [ref for ref in _live_monitor_store_refs(store) if ref.tid in set(tids)]
    assert live_refs == []
    assert store.deferred_write_status().pending == 0
    report_tids = _external_report_tids(external_path)
    assert sorted(report_tids) == sorted(tids), (
        "each family must be exported to JSONL exactly once before its raw "
        f"rows are deleted; got {report_tids!r}"
    )


def test_task_monitor_jsonl_backlog_lifecycle_keeps_raw_deleted_invariant(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Rung 2a: multi-window backlog of terminal families, oracle per cycle.

    Seeds 6 two-row terminal families (12 rows) against a 4-row scan window
    so catch-up needs multiple cycles before the first high-water cycle can
    run summaries and deletion, then drives real cycles to convergence.

    Verifies:
    - catch-up cycles do not reach high-water and run no lifecycle slices
    - no family is ever marked raw-deleted while its raw rows survive
    - the backlog converges (rows deleted, refs retired, JSONL exported)
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    base_tid = time.time_ns()
    tids = tuple(str(base_tid + offset) for offset in range(6))
    _seed_terminal_family_backlog(log_queue, tids)

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(base_tid + 100)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        base_now = time.time_ns()
        high_water_flags: list[bool] = []
        for index in range(12):
            ingest = _run_oracle_checked_store_cycle(
                task,
                store,
                log_queue,
                tids,
                now_ns=base_now + index,
                cycle=f"cycle {index + 1}",
            )
            high_water_flags.append(ingest.completed_fifo_high_water)
            if not _seeded_rows_remaining(
                log_queue, tids
            ) and not _seeded_refs_remaining(store, tids):
                break
        # 12 seeded rows against a 4-row window: at least the first two
        # cycles are catch-up (scan limit reached) before the first
        # high-water cycle can run summaries and deletion, mirroring
        # production's backlog-then-lifecycle progression.
        assert True in high_water_flags
        assert high_water_flags.index(True) >= 2
        assert high_water_flags[:2] == [False, False]
        _assert_jsonl_lifecycle_converged(store, log_queue, external_path, tids)
    finally:
        task.stop()


def test_task_monitor_jsonl_backlog_lifecycle_survives_monitor_restart(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Rung 2b: monitor restart mid-backlog with a persisted checkpoint.

    Production restarted its manager and monitor on June 4 with rows both
    behind and ahead of the durable checkpoint. This rung stops the first
    TaskMonitor after one catch-up cycle and finishes the backlog with a
    fresh instance against the same store and checkpoint.

    Verifies:
    - the restarted monitor resumes from the durable checkpoint
    - no vacuous raw-deleted marking appears across the restart boundary
    - the backlog converges under the second incarnation
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    base_tid = time.time_ns()
    tids = tuple(str(base_tid + offset) for offset in range(6))
    _seed_terminal_family_backlog(log_queue, tids)

    first = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(base_tid + 100)),
        config=config,
    )
    base_now = time.time_ns()
    try:
        store = first._ensure_monitor_store()
        assert store is not None
        ingest = _run_oracle_checked_store_cycle(
            first,
            store,
            log_queue,
            tids,
            now_ns=base_now,
            cycle="incarnation 1 cycle 1",
        )
        assert ingest.completed_fifo_high_water is False
        checkpoint_before_restart = store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE)
        assert checkpoint_before_restart is not None
    finally:
        first.stop()

    second = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(base_tid + 101)),
        config=config,
    )
    try:
        store = second._ensure_monitor_store()
        assert store is not None
        assert store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE) == checkpoint_before_restart
        for index in range(12):
            _run_oracle_checked_store_cycle(
                second,
                store,
                log_queue,
                tids,
                now_ns=base_now + 1 + index,
                cycle=f"incarnation 2 cycle {index + 1}",
            )
            if not _seeded_rows_remaining(
                log_queue, tids
            ) and not _seeded_refs_remaining(store, tids):
                break
        _assert_jsonl_lifecycle_converged(store, log_queue, external_path, tids)
    finally:
        second.stop()


def test_task_monitor_jsonl_lifecycle_handles_families_older_than_retention(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Rung 2c: families already older than the retention window.

    Production families were 1-4 days old (against the 48h retention
    default) when the lifecycle first processed them, so retirement and
    pre-checkpoint recovery participate immediately. Time is injected
    three days ahead of the seeded rows' broker timestamps.

    Verifies:
    - old terminal families are summarized and exported before deletion
    - raw-deleted marking still only follows real broker deletion
    - immediate family retirement never strands live raw rows
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    base_tid = time.time_ns()
    tids = tuple(str(base_tid + offset) for offset in range(3))
    _seed_terminal_family_backlog(log_queue, tids)

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(base_tid + 100)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        # Three days ahead: the seeded rows are now "older" than the 48h
        # retention period at first processing, as in production.
        base_now = time.time_ns() + 3 * 86_400 * 1_000_000_000
        retired_seen = False
        for index in range(12):
            _run_oracle_checked_store_cycle(
                task,
                store,
                log_queue,
                tids,
                now_ns=base_now + index,
                cycle=f"cycle {index + 1}",
            )
            broker_rows = _broker_rows_by_tid(log_queue)
            checkpoint = store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE)
            for tid in tids:
                record = store.get_task(tid)
                ingested = any(
                    checkpoint is not None and message_id <= checkpoint
                    for message_id in broker_rows.get(tid, set())
                ) or tid in _external_report_tids(external_path)
                if record is None and ingested:
                    retired_seen = True
                    # Retirement is only legal once the rows are gone and
                    # the report is exported (the jsonl audit promise).
                    assert broker_rows.get(tid, set()) == set()
                    assert tid in _external_report_tids(external_path)
            if not _seeded_rows_remaining(
                log_queue, tids
            ) and not _seeded_refs_remaining(store, tids):
                break
        _assert_jsonl_lifecycle_converged(store, log_queue, external_path, tids)
        assert retired_seen, (
            "families older than the retention window must retire after "
            "their lifecycle completes"
        )
    finally:
        task.stop()


def test_task_monitor_jsonl_lifecycle_deletes_terminal_family_despite_clock_lag(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Minimized red repro: the lifecycle silently stalls behind message IDs.

    SimpleBroker hybrid message IDs are durably monotonic: ``meta.last_ts``
    never regresses, and after any clock regression the generator carries
    the old (future) physical component forward (simplebroker
    ``_timestamp.py`` ``_next_components``). One fast-clocked writer
    therefore pins the shared ID domain AHEAD of the monitor host's
    ``time.time_ns()`` for as long as the skew lasts. Summary readiness
    compares ``last_message_id <= now_ns``
    (``select_summary_ready_terminal_tasks``), and in ``jsonl_then_delete``
    mode raw deletion requires the summary (``require_summary``), so a
    terminal family whose IDs are ahead of the monitor clock is silently
    excluded from the ENTIRE post-terminal lifecycle: no summary, no JSONL
    export, no raw deletion, no error — while ingest, refs, and high-water
    keep reporting healthy. This matches the production deployment whose
    post-terminal lifecycle first ran four days after restart while raw
    rows accumulated from day one with ``last_error=null``.

    This test drives the real cycle entry point with the monitor clock one
    tick behind the seeded family's broker-assigned IDs and asserts the
    spec-desired outcome: a terminal, fully ingested family processed at
    high-water with a healthy sink must be summarized, exported, and its
    raw rows deleted. It is EXPECTED RED until Task A3 fixes the
    cross-domain comparison.

    Spec: [MF-5], [OBS.13], [OBS.17]
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path, batch_size=10, scan_limit=10)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    tid = str(time.time_ns())
    _seed_terminal_family_backlog(log_queue, (tid,))
    seeded_ids = sorted(_broker_rows_by_tid(log_queue)[tid])
    assert len(seeded_ids) == 2
    # The monitor host clock lags the broker-assigned hybrid IDs by one
    # tick. The ID domain never regresses to meet it, so without a fix the
    # lag persists for entire cycles, exactly like a pinned meta.last_ts.
    lagging_now_ns = seeded_ids[0] - 1

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(time.time_ns())),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        for index in range(5):
            ingest = _run_oracle_checked_store_cycle(
                task,
                store,
                log_queue,
                (tid,),
                now_ns=lagging_now_ns + index,
                cycle=f"cycle {index + 1}",
            )
            assert ingest.completed_fifo_high_water is True

        record = store.get_task(tid)
        assert record is not None
        assert record.terminal_seen is True
        remaining_rows = sorted(_broker_rows_by_tid(log_queue).get(tid, set()))
        diagnostics = (
            f"summary_emitted_at_ns={record.summary_emitted_at_ns} "
            f"raw_deleted_at_ns={record.raw_deleted_at_ns} "
            f"disposition_at_ns={record.disposition_at_ns} "
            f"live_refs={[(ref.tid, ref.message_id) for ref in _live_monitor_store_refs(store) if ref.tid == tid]} "
            f"reports={_external_report_tids(external_path)} "
            f"collation_store_error={task._last_collation_store_error}"
        )
        assert remaining_rows == [], (
            "terminal family ingested at high-water with a healthy sink and "
            "zero errors must have its raw rows deleted, but rows "
            f"{remaining_rows} survive because the family is never "
            f"summary-ready while its message IDs exceed the monitor clock; "
            f"{diagnostics}"
        )
        assert record.summary_emitted_at_ns is not None, diagnostics
        assert tid in _external_report_tids(external_path), diagnostics
    finally:
        task.stop()


def test_task_monitor_jsonl_lifecycle_with_interleaved_writer_load(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Rung 4: continuous writer load interleaved with delete cycles.

    Production log traffic never pauses (~1,300 rows/day) while the
    lifecycle deletes, and late rows for already-marked families are the
    state the production probe captured (marked families with live rows
    and refs). This rung interleaves, between every cycle, a brand-new
    terminal family AND a late row for an already-marked family, forcing
    the repair slice to re-drive marked families through exact deletion.

    Verifies:
    - late rows for marked families are deleted by the repair slice, not
      stranded behind the raw-deleted mark
    - new families keep converging while deletions run
    - the oracle holds at every cycle boundary under sustained load
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    base_tid = time.time_ns()
    seed_tids = tuple(str(base_tid + offset) for offset in range(4))
    _seed_terminal_family_backlog(log_queue, seed_tids)

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(base_tid + 100)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        all_tids = list(seed_tids)
        late_rows_written = 0
        next_new_family = 0
        for index in range(40):
            # A fresh clock per cycle, exactly like the real cycle driver:
            # rows written between cycles carry broker-assigned hybrid IDs
            # newer than any frozen test clock, and summary readiness
            # compares ``last_message_id`` against the cycle clock.
            _run_oracle_checked_store_cycle(
                task,
                store,
                log_queue,
                tuple(all_tids),
                now_ns=time.time_ns(),
                cycle=f"cycle {index + 1}",
            )
            if index < 6:
                # Concurrent writer: one new terminal family per cycle.
                new_tid = str(base_tid + 200 + next_new_family)
                next_new_family += 1
                all_tids.append(new_tid)
                _seed_terminal_family_backlog(log_queue, (new_tid,))
                # Late event for an already-marked family, the exact state
                # the production probe captured.
                marked = [
                    tid
                    for tid in all_tids
                    if (record := store.get_task(tid)) is not None
                    and record.raw_deleted_at_ns is not None
                ]
                if marked:
                    log_queue.write(
                        json.dumps(
                            {
                                "event": "task_activity",
                                "status": "running",
                                "tid": marked[0],
                                "sequence": 3,
                            }
                        )
                    )
                    late_rows_written += 1
            elif not _seeded_rows_remaining(
                log_queue, tuple(all_tids)
            ) and not _seeded_refs_remaining(store, tuple(all_tids)):
                break
        assert late_rows_written > 0, (
            "the interleave never produced a marked family to write late "
            "rows against; the rung lost its production fidelity"
        )
        broker_rows = _broker_rows_by_tid(log_queue)
        for tid in all_tids:
            record = store.get_task(tid)
            assert broker_rows.get(tid, set()) == set(), (
                f"family {tid} still has raw rows {sorted(broker_rows[tid])} "
                "after convergence under writer load; collation record: "
                f"{record.to_summary() if record is not None else None}; "
                f"reports: {_external_report_tids(external_path)}"
            )
        live_refs = [
            ref for ref in _live_monitor_store_refs(store) if ref.tid in set(all_tids)
        ]
        assert live_refs == []
        assert store.deferred_write_status().pending == 0
        report_tids = _external_report_tids(external_path)
        assert sorted(report_tids) == sorted(all_tids)
    finally:
        task.stop()


def test_retirement_backlog_identifies_binding_stage(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Characterize which retirable-predicate arm binds a terminal backlog.

    A degraded production install retired only ~2 families per cycle while
    a backlog of fully-processable terminal families waited. This test
    seeds 120 two-row terminal families in the production shape (60
    completed via ``work_completed`` and 60 failed via ``work_failed`` —
    only non-completed terminal families set ``reserved_probe_needed``, so
    the failed half is the only way to exhibit the reserved gate), with
    completion timestamps backdated three days past the 48h retention
    default, under production batch/scan limits and the production 1.0s
    runtime-cleanup slice deadline. It then drives 3 real monitor cycles
    and, if any seeded family is still unretired, fails with a
    per-predicate-arm breakdown naming the binding stage of
    ``select_retirable_task_collations``.

    Verifies:
    - ingest is not the limiter (the durable checkpoint passes every
      seeded row in cycle 1 under production batch/scan limits)
    - either all 120 families retire within 3 cycles and the JSONL sink
      saw every subject tid exactly once (a real scale pin), or the
      failure message reports per-cycle retired counts plus per-arm
      counts and sample family evidence for every unsatisfied arm,
      including the live-refs arm via ``store.has_task_messages``

    Spec: [MF-5]
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    # The module autouse fixture widens the slice deadline to 30.0s; restore
    # the production 1.0s so the slice deadline can actually bind.
    monkeypatch.setattr(
        task_monitor_mod, "TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS", 1.0
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path, batch_size=5000, scan_limit=50000)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    base_tid = time.time_ns()
    tids = tuple(str(base_tid + offset) for offset in range(120))
    retention_ns = 172_800 * 10**9
    completed_at_ns = time.time_ns() - 3 * 86_400 * 10**9
    started_at_ns = completed_at_ns - 60 * 10**9
    _seed_backdated_terminal_family_backlog(
        log_queue,
        tids[:60],
        terminal_event="work_completed",
        terminal_status="completed",
        completed_at_ns=completed_at_ns,
        started_at_ns=started_at_ns,
    )
    _seed_backdated_terminal_family_backlog(
        log_queue,
        tids[60:],
        terminal_event="work_failed",
        terminal_status="failed",
        completed_at_ns=completed_at_ns,
        started_at_ns=started_at_ns,
    )
    seeded_rows = _broker_rows_by_tid(log_queue)
    max_seeded_message_id = max(
        message_id for tid in tids for message_id in seeded_rows[tid]
    )

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(base_tid + 1000)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        per_cycle_remaining: list[int] = []
        family_limit_hits: list[bool] = []
        deadline_hits: list[bool] = []
        for cycle in range(3):
            task.process_once()
            drive_task_monitor_until_idle(task)
            task._next_cycle_due_monotonic = 0.0
            per_cycle_remaining.append(
                sum(1 for tid in tids if store.get_task(tid) is not None)
            )
            family_limit_hits.append(task._last_control_cleanup_family_limit_hit)
            deadline_hits.append(task._last_control_cleanup_deadline_hit)
            if cycle == 0:
                checkpoint = store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE)
                assert checkpoint is not None and checkpoint >= max_seeded_message_id, (
                    "ingest did not pass the seeded backlog in cycle 1 "
                    f"(checkpoint {checkpoint} < {max_seeded_message_id}); "
                    "with production batch/scan limits ingest must not be "
                    "the binding stage"
                )

        previous = len(tids)
        per_cycle_retired: list[int] = []
        for remaining in per_cycle_remaining:
            per_cycle_retired.append(previous - remaining)
            previous = remaining

        cutoff_ns = time.time_ns() - retention_ns
        unretired: list[str] = []
        arm_counts: dict[str, int] = {}
        arm_samples: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for tid in tids:
            record = store.get_task(tid)
            if record is None:
                continue
            unretired.append(tid)
            has_live_refs = store.has_task_messages(tid)
            coalesced = record.completed_at_ns
            if coalesced is None:
                coalesced = record.last_seen_at_ns
            if coalesced is None:
                coalesced = record.last_message_id
            arms: list[str] = []
            if coalesced > cutoff_ns:
                arms.append("retention_window")
            if record.raw_deleted_at_ns is None:
                arms.append("raw_not_deleted")
            if record.summary_emitted_at_ns is None:
                arms.append("summary_missing")
            if record.disposition_at_ns is None:
                arms.append("disposition_missing")
            if record.task_control_deleted_at_ns is None:
                arms.append("control_not_deleted")
            if (
                record.reserved_probe_needed
                and record.reserved_cleanup_checked_at_ns is None
            ):
                arms.append("reserved_probe_pending")
            if has_live_refs:
                arms.append("live_refs_present")
            if not arms:
                # Every predicate arm is satisfied: the family is fully
                # retirable and is only waiting for the next retirement
                # pass — a scheduling limiter, not a predicate arm.
                arms.append("all_arms_satisfied")
            evidence = {
                "completed_at_ns": record.completed_at_ns,
                "last_seen_at_ns": record.last_seen_at_ns,
                "last_message_id": record.last_message_id,
                "raw_deleted_at_ns": record.raw_deleted_at_ns,
                "summary_emitted_at_ns": record.summary_emitted_at_ns,
                "disposition_at_ns": record.disposition_at_ns,
                "task_control_deleted_at_ns": record.task_control_deleted_at_ns,
                "reserved_probe_needed": record.reserved_probe_needed,
                "reserved_cleanup_checked_at_ns": (
                    record.reserved_cleanup_checked_at_ns
                ),
                "has_live_refs": has_live_refs,
            }
            for arm in arms:
                arm_counts[arm] = arm_counts.get(arm, 0) + 1
                samples = arm_samples.setdefault(arm, [])
                if len(samples) < 3:
                    samples.append((tid, evidence))

        assert not unretired, (
            "retirement did not converge in 3 cycles; per-cycle retired counts "
            f"{per_cycle_retired!r}; binding-arm breakdown {dict(arm_counts)!r}; "
            f"sample families per arm {arm_samples!r}; "
            f"per-cycle remaining {per_cycle_remaining!r}; "
            f"per-cycle family_limit_hit {family_limit_hits!r}; "
            f"per-cycle deadline_hit {deadline_hits!r}"
        )
        # Green path: pin the scale, not just the absence of leftovers —
        # every subject family must have been exported to the JSONL sink
        # exactly once before retirement.
        seeded = set(tids)
        report_tids = [
            tid for tid in _external_report_tids(external_path) if tid in seeded
        ]
        assert sorted(report_tids) == sorted(tids), (
            "all 120 families retired but the JSONL sink did not see every "
            f"subject tid exactly once; got {report_tids!r}"
        )
    finally:
        task.stop()


# ---------------------------------------------------------------------------
# WS-A Tasks A4 and A5: raw-deleted marker invariant and self-healing
# recovery of pre-existing damage
# (docs/plans/2026-06-10-self-healing-runtime-maintenance-plan.md, A4/A5)
#
# A4 pins the marker invariant: ``raw_deleted_at_ns`` may only mean "the
# broker rows for this family are actually gone". A5 pins convergence for
# deployments already carrying marked-but-undeleted families and the
# jsonl_then_delete audit invariant (no raw row deletion before the
# family's summary/JSONL export) on BOTH recovery selections.
# ---------------------------------------------------------------------------


def _all_live_message_ids(log_queue: Any) -> set[int]:
    """Return every live raw message ID in ``weft.log.tasks``."""

    return {int(message_id) for _body, message_id in iter_queue_entries(log_queue)}


def _ingest_live_rows_without_deletion(
    store: MonitorStore,
    log_queue: Any,
    tids: tuple[str, ...],
) -> int:
    """Fold all live raw rows into the store without deleting them.

    Produces what a completed non-destructive ingest pass would have left
    behind — collation rows, child refs, and the durable checkpoint at the
    live head — so tests can layer production damage shapes (vacuous
    raw-deleted marks, destroyed refs) on top of real broker rows. Rows
    from other writers (the monitor's own task-log rows) are folded in
    too, exactly as real ingest would.

    Returns the checkpoint message ID written.
    """

    updates = []
    seeded_seen: set[str] = set()
    last_message_id: int | None = None
    for body, message_id in iter_queue_entries(log_queue):
        payload = json.loads(body)
        update = update_from_task_log_payload(
            payload,
            queue_name=WEFT_GLOBAL_LOG_QUEUE,
            message_id=int(message_id),
        )
        assert update is not None
        updates.append(update)
        if update.tid in tids:
            seeded_seen.add(update.tid)
        last_message_id = int(message_id)
    assert last_message_id is not None and seeded_seen == set(tids)
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        tuple(updates),
        checkpoint_message_id=last_message_id,
    )
    return last_message_id


def _force_raw_deleted_marks(
    store: MonitorStore,
    tids: tuple[str, ...],
    marked_at_ns: int,
) -> None:
    """Set ``raw_deleted_at_ns`` directly, simulating pre-fix vacuous damage.

    The fixed runtime can no longer produce a marked family whose raw rows
    survive (that is the WS-A invariant), so production's damage shape is
    constructed with the same direct UPDATE the Monitor-store unit tests
    use (tests/core/test_monitor_store.py repair-listing test).
    """

    with store._context.broker() as broker:
        runner = broker._runner
        for tid in tids:
            runner.run(
                "UPDATE weft_monitor_task_collations "
                "SET raw_deleted_at_ns = ?, updated_at_ns = ? "
                "WHERE context_key = ? AND tid = ?",
                (marked_at_ns, marked_at_ns, store.context_key, tid),
            )
        runner.commit()


def _run_quiet_store_cycle(task: TaskMonitor, *, cycle: str) -> None:
    """Run one real collated-store cycle and require zero recorded errors."""

    task._run_monitor_store_cycle(
        now_ns=time.time_ns(),
        task_log_owner="collated_store",
        start_control_cleanup=False,
    )
    assert task._last_collation_store_error is None, (
        f"{cycle}: unexpected collation store error: {task._last_collation_store_error}"
    )
    ingest = task._last_retained_task_log_ingest
    assert ingest.store_write_errors == (), f"{cycle}: {ingest.store_write_errors}"
    assert ingest.raw_delete_errors == (), f"{cycle}: {ingest.raw_delete_errors}"


def test_task_monitor_never_marks_family_with_uningested_rows(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A4(a): a family whose rows ingest has not reached is never marked.

    Family X's rows sit entirely beyond the first ingest window, so after
    the first (catch-up) cycle X has no collation record, no refs, and its
    raw rows survive. Nothing may mark X (or anyone) raw-deleted in that
    state. The backlog then converges with the raw-deleted oracle asserted
    at every cycle boundary.

    Spec: [MF-5], [OBS.17]
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    base_tid = time.time_ns()
    filler_tids = (str(base_tid), str(base_tid + 1))
    uningested_tid = str(base_tid + 2)
    all_tids = (*filler_tids, uningested_tid)
    # Four filler rows first, then X's two rows: the 4-row scan window of
    # the first cycle cannot reach X.
    _seed_terminal_family_backlog(log_queue, filler_tids)
    _seed_terminal_family_backlog(log_queue, (uningested_tid,))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(base_tid + 100)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        base_now = time.time_ns()
        ingest = _run_oracle_checked_store_cycle(
            task,
            store,
            log_queue,
            all_tids,
            now_ns=base_now,
            cycle="cycle 1",
        )
        assert ingest.completed_fifo_high_water is False
        # X was never ingested: no collation record, no refs, raw rows
        # intact, and in particular no raw-deleted mark anywhere.
        assert store.get_task(uningested_tid) is None
        assert sorted(_broker_rows_by_tid(log_queue)[uningested_tid]) != []
        assert not any(
            ref.tid == uningested_tid for ref in _live_monitor_store_refs(store)
        )
        for tid in filler_tids:
            record = store.get_task(tid)
            assert record is not None
            assert record.raw_deleted_at_ns is None, (
                "no family may be marked raw-deleted on a catch-up cycle "
                "while its raw rows survive"
            )

        for index in range(12):
            _run_oracle_checked_store_cycle(
                task,
                store,
                log_queue,
                all_tids,
                now_ns=base_now + 1 + index,
                cycle=f"cycle {index + 2}",
            )
            if not _seeded_rows_remaining(
                log_queue, all_tids
            ) and not _seeded_refs_remaining(store, all_tids):
                break
        _assert_jsonl_lifecycle_converged(store, log_queue, external_path, all_tids)
    finally:
        task.stop()


def test_task_monitor_non_high_water_cycle_marks_nothing(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A4(c): a cycle that does not complete high-water marks no family.

    With a backlog wider than the scan window, the first cycle stops at
    the scan limit (``completed_fifo_high_water`` False). That cycle must
    emit no summaries, delete no monitor-store-proven rows, and mark no
    family raw-deleted, even though some families are already fully
    ingested and terminal. The oracle must hold at every cycle boundary
    through convergence.

    Spec: [MF-5], [OBS.13], [OBS.17]
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    base_tid = time.time_ns()
    tids = tuple(str(base_tid + offset) for offset in range(3))
    _seed_terminal_family_backlog(log_queue, tids)

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(base_tid + 100)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        base_now = time.time_ns()
        ingest = _run_oracle_checked_store_cycle(
            task,
            store,
            log_queue,
            tids,
            now_ns=base_now,
            cycle="cycle 1",
        )
        assert ingest.completed_fifo_high_water is False
        assert task._last_collation_summaries_emitted == 0
        assert task._last_monitor_store_message_rows_deleted == 0
        assert _external_report_tids(external_path) == []
        for tid in tids:
            record = store.get_task(tid)
            if record is not None:
                assert record.raw_deleted_at_ns is None, (
                    f"family {tid} marked raw-deleted by a non-high-water "
                    "cycle while its raw rows survive"
                )

        for index in range(12):
            _run_oracle_checked_store_cycle(
                task,
                store,
                log_queue,
                tids,
                now_ns=base_now + 1 + index,
                cycle=f"cycle {index + 2}",
            )
            if not _seeded_rows_remaining(
                log_queue, tids
            ) and not _seeded_refs_remaining(store, tids):
                break
        _assert_jsonl_lifecycle_converged(store, log_queue, external_path, tids)
    finally:
        task.stop()


def test_task_monitor_malformed_row_deletion_does_not_mark_family_with_valid_rows(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A4: ingest-path malformed-row deletion cannot mark live families.

    The ingest pass deletes malformed rows (after their raw-row lifetime
    report) even on catch-up cycles, and its store reconcile call
    (``delete_task_messages_after_raw_delete``) runs only for verifiably
    deleted VALID rows. A malformed row deleted alongside family X's
    valid, undeleted rows must therefore never mark X raw-deleted.

    Spec: [MF-5], [OBS.13], [OBS.17]
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    base_tid = time.time_ns()
    family_tid = str(base_tid)
    filler_tid = str(base_tid + 1)
    tids = (family_tid, filler_tid)
    # Window 1 (scan limit 4): X's two valid rows, the malformed row, and
    # the filler family's first row. The cycle stays below high-water, so
    # the only deletion that can happen is the malformed-row one.
    _seed_terminal_family_backlog(log_queue, (family_tid,))
    log_queue.write(json.dumps({"event": "task_activity", "status": "running"}))
    _seed_terminal_family_backlog(log_queue, (filler_tid,))
    attributed = {
        message_id
        for ids in _broker_rows_by_tid(log_queue).values()
        for message_id in ids
    }
    malformed_ids = _all_live_message_ids(log_queue) - attributed
    assert len(malformed_ids) == 1
    malformed_id = next(iter(malformed_ids))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(base_tid + 100)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        base_now = time.time_ns()
        ingest = _run_oracle_checked_store_cycle(
            task,
            store,
            log_queue,
            tids,
            now_ns=base_now,
            cycle="cycle 1",
        )
        assert ingest.completed_fifo_high_water is False
        assert ingest.malformed_deleted == 1
        assert malformed_id not in _all_live_message_ids(log_queue)
        record = store.get_task(family_tid)
        assert record is not None
        assert record.terminal_seen is True
        assert record.raw_deleted_at_ns is None, (
            "deleting a malformed row vacuously marked a family whose "
            "valid raw rows survive"
        )
        assert sorted(_broker_rows_by_tid(log_queue)[family_tid]) != []
        family_refs = [
            ref for ref in _live_monitor_store_refs(store) if ref.tid == family_tid
        ]
        assert len(family_refs) == 2

        for index in range(12):
            _run_oracle_checked_store_cycle(
                task,
                store,
                log_queue,
                tids,
                now_ns=base_now + 1 + index,
                cycle=f"cycle {index + 2}",
            )
            if not _seeded_rows_remaining(
                log_queue, tids
            ) and not _seeded_refs_remaining(store, tids):
                break
        _assert_jsonl_lifecycle_converged(store, log_queue, external_path, tids)
    finally:
        task.stop()


def test_task_monitor_jsonl_converges_marked_families_with_refs_and_rows(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A5(1): the production damage state converges, then stays converged.

    Constructs production's post-defect shape directly: terminal families,
    summaries already emitted, collations marked ``raw_deleted_at_ns``,
    store refs present, raw broker rows present (2,068 families in the
    2026-06-10 investigation). The repair slice must re-drive the marked
    families' refs through real exact deletion: rows and refs converge to
    gone within a bounded number of cycles, the raw-deleted oracle holds
    afterward, further cycles are no-ops, and no family is re-exported
    (their summaries already landed before the damage).

    Spec: [MF-5], [OBS.13], [OBS.17]
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    base_tid = time.time_ns()
    tids = (str(base_tid), str(base_tid + 1))
    _seed_terminal_family_backlog(log_queue, tids)

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(base_tid + 100)),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        marked_at_ns = _ingest_live_rows_without_deletion(store, log_queue, tids)
        for tid in tids:
            store.mark_summary_emitted(tid, marked_at_ns)
        _force_raw_deleted_marks(store, tids, marked_at_ns)
        # The exact production probe shape: marked families, live refs,
        # live raw rows, summaries emitted, zero errors anywhere.
        for tid in tids:
            record = store.get_task(tid)
            assert record is not None
            assert record.terminal_seen is True
            assert record.raw_deleted_at_ns is not None
            assert record.summary_emitted_at_ns is not None
            assert _broker_rows_by_tid(log_queue).get(tid)
        assert _seeded_refs_remaining(store, tids)

        converged_at: int | None = None
        for index in range(6):
            _run_quiet_store_cycle(task, cycle=f"cycle {index + 1}")
            if not _seeded_rows_remaining(
                log_queue, tids
            ) and not _seeded_refs_remaining(store, tids):
                converged_at = index + 1
                break
        assert converged_at is not None, (
            "marked-but-undeleted families did not converge: rows "
            f"{_broker_rows_by_tid(log_queue)} refs "
            f"{[(ref.tid, ref.message_id) for ref in _live_monitor_store_refs(store)]}"
        )
        _assert_raw_deleted_oracle(store, log_queue, tids, cycle="converged")
        # Healing must not re-export already-summarized families.
        assert _external_report_tids(external_path) == []

        # Idempotence: further cycles change nothing and record no errors.
        for extra in range(2):
            _run_quiet_store_cycle(task, cycle=f"idempotent cycle {extra + 1}")
            assert not _seeded_rows_remaining(log_queue, tids)
            assert not _seeded_refs_remaining(store, tids)
            _assert_raw_deleted_oracle(
                store,
                log_queue,
                tids,
                cycle=f"idempotent cycle {extra + 1}",
            )
        assert _external_report_tids(external_path) == []
    finally:
        task.stop()


@pytest.mark.parametrize("disposed", [False, True])
def test_task_monitor_repair_path_exports_summary_before_deleting_rows(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    disposed: bool,
) -> None:
    """A5(2): repair-path audit pin — summary/JSONL before raw deletion.

    A family marked raw-deleted WITH live refs but WITHOUT a summary may
    only lose its raw rows after its summary/JSONL export lands. In the
    undisposed shape the summary stage (which runs before the repair
    slice) exports the family and deletion follows. In the disposed shape
    the summary stage can never select the family (dispositions exclude
    it), so the summary-gated repair selection must hold the rows
    indefinitely rather than silently destroying unexported data — the
    pre-fix repair selection deleted them on the first destructive pass.

    Spec: [MF-5], [OBS.13], [OBS.17]
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    tid = str(time.time_ns())
    _seed_terminal_family_backlog(log_queue, (tid,))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(time.time_ns())),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        marked_at_ns = _ingest_live_rows_without_deletion(store, log_queue, (tid,))
        _force_raw_deleted_marks(store, (tid,), marked_at_ns)
        if disposed:
            store.mark_family_disposed(
                tid,
                marked_at_ns,
                disposition_reason="terminal",
            )
        record = store.get_task(tid)
        assert record is not None
        assert record.raw_deleted_at_ns is not None
        assert record.summary_emitted_at_ns is None
        assert _seeded_refs_remaining(store, (tid,))

        for index in range(4):
            _run_quiet_store_cycle(task, cycle=f"cycle {index + 1}")
            # The audit invariant, checked at every cycle boundary: raw
            # rows may only be gone once the JSONL export landed.
            if not _broker_rows_by_tid(log_queue).get(tid):
                assert tid in _external_report_tids(external_path), (
                    f"cycle {index + 1}: raw rows deleted before the "
                    "family's summary/JSONL export"
                )

        rows_remaining = sorted(_broker_rows_by_tid(log_queue).get(tid, set()))
        if disposed:
            # No summary owner exists for a disposed-unsummarized family;
            # the gate must hold its rows and refs rather than lose them.
            assert len(rows_remaining) == 2
            assert _seeded_refs_remaining(store, (tid,))
            assert tid not in _external_report_tids(external_path)
        else:
            # Summary stage exports first, repair deletes afterward.
            assert rows_remaining == []
            assert not _seeded_refs_remaining(store, (tid,))
            assert _external_report_tids(external_path).count(tid) == 1
            record = store.get_task(tid)
            assert record is not None
            assert record.summary_emitted_at_ns is not None
            _assert_raw_deleted_oracle(store, log_queue, (tid,), cycle="converged")
    finally:
        task.stop()


@pytest.mark.parametrize("disposed", [False, True])
def test_task_monitor_orphan_path_exports_summary_before_deleting_rows(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    disposed: bool,
) -> None:
    """A5(3): orphan-path audit pin — summary/JSONL before raw deletion.

    A marked, terminal, unsummarized family with NO refs and live raw rows
    is the orphan-recovery selection's domain; its eligibility
    OR-condition admits terminal-but-unsummarized families, so without the
    summary gate the pre-fix orphan slice deleted their raw rows with no
    JSONL export ever happening. With the gate: in the undisposed shape
    the summary stage exports the family first and orphan recovery deletes
    the rows on a later pass of the lifecycle; in the disposed shape (no
    summary owner exists) the rows are held indefinitely.

    The damage shape itself is constructed through the real vacuous
    mechanism: deleting the family's refs while its raw rows survive marks
    the family via the store reconcile.

    Spec: [MF-5], [OBS.13], [OBS.17]
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    external_path = tmp_path / "task-lifetime.jsonl"
    config = _jsonl_lifecycle_config(external_path)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    tid = str(time.time_ns())
    _seed_terminal_family_backlog(log_queue, (tid,))

    task = TaskMonitor(
        db_path,
        make_task_monitor_taskspec(str(time.time_ns())),
        config=config,
    )
    try:
        store = task._ensure_monitor_store()
        assert store is not None
        marked_at_ns = _ingest_live_rows_without_deletion(store, log_queue, (tid,))
        family_ids = sorted(_broker_rows_by_tid(log_queue)[tid])
        # Destroy the refs while the raw rows survive: the reconcile step
        # marks the family raw-deleted — the vacuous-marking mechanism.
        store.delete_task_messages_after_raw_delete(
            family_ids,
            deleted_at_ns=marked_at_ns,
        )
        if disposed:
            store.mark_family_disposed(
                tid,
                marked_at_ns,
                disposition_reason="terminal",
            )
        record = store.get_task(tid)
        assert record is not None
        assert record.terminal_seen is True
        assert record.raw_deleted_at_ns is not None
        assert record.summary_emitted_at_ns is None
        assert not _seeded_refs_remaining(store, (tid,))
        assert sorted(_broker_rows_by_tid(log_queue)[tid]) == family_ids

        for index in range(4):
            _run_quiet_store_cycle(task, cycle=f"cycle {index + 1}")
            if not _broker_rows_by_tid(log_queue).get(tid):
                assert tid in _external_report_tids(external_path), (
                    f"cycle {index + 1}: orphan raw rows deleted before the "
                    "family's summary/JSONL export"
                )

        rows_remaining = sorted(_broker_rows_by_tid(log_queue).get(tid, set()))
        if disposed:
            assert rows_remaining == family_ids
            assert tid not in _external_report_tids(external_path)
        else:
            assert rows_remaining == []
            assert _external_report_tids(external_path).count(tid) == 1
            record = store.get_task(tid)
            if record is not None:
                assert record.summary_emitted_at_ns is not None
                assert record.orphan_raw_recovery_checked_at_ns is not None
            _assert_raw_deleted_oracle(store, log_queue, (tid,), cycle="converged")
    finally:
        task.stop()


def _read_status_reply(
    task: TaskMonitor,
    ctrl_in: Any,
    ctrl_out: Any,
    *,
    request_id: str,
) -> dict[str, Any]:
    """Round-trip one STATUS control request through the live monitor."""

    ctrl_in.write(json.dumps({"command": CONTROL_STATUS, "request_id": request_id}))
    task.wait_for_activity(timeout=task.next_wait_timeout())
    task.process_once()
    drive_task_monitor_until_idle(task)
    responses = [json.loads(item) for item in ctrl_out.peek_generator()]
    return next(
        response
        for response in responses
        if response.get("command") == CONTROL_STATUS
        and response.get("request_id") == request_id
    )


def _maintenance_service_owner_payload(
    *,
    service_key: str,
    tid: str,
    status: str,
) -> dict[str, Any]:
    """Mirror the CLI runtime-prune fixtures for service-owner registry rows."""

    return build_service_owner_payload(
        service_key=service_key,
        service_type=SERVICE_TYPE_MANAGED,
        owner_tid=tid,
        status=status,
        name="maintenance-prune-service",
        queues={
            "ctrl_in": f"T{tid}.ctrl_in",
            "ctrl_out": f"T{tid}.ctrl_out",
            "inbox": f"T{tid}.inbox",
            "outbox": f"T{tid}.outbox",
        },
        runtime_handle={
            "runner": "host",
            "kind": "process",
            "id": tid[-4:],
            "control": {"authority": "host-pid"},
            "observations": {"host_pids": [int(tid[-4:])]},
        },
        metadata={"internal_role": "maintenance-test"},
    )


def _write_json_row(queue: Any, payload: dict[str, Any]) -> int:
    """Write one JSON row and return its exact broker message ID."""

    queue.write(json.dumps(payload))
    latest: int | None = None
    for body, message_id in iter_queue_entries(queue):
        if not body.startswith("{"):
            continue
        if json.loads(body) == payload:
            latest = int(message_id)
    assert latest is not None
    return latest


def _queue_json_rows(queue: Any) -> dict[int, dict[str, Any]]:
    """Return remaining JSON rows keyed by exact broker message ID."""

    return {
        int(message_id): json.loads(body)
        for body, message_id in iter_queue_entries(queue)
        if body.startswith("{")
    }


def test_task_monitor_maintenance_vacuums_claimed_rows_on_monotonic_deadline(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D1: monitor maintenance vacuums claimed rows on its monotonic cadence.

    Verifies:
    - Claimed (read) rows are physically deleted once maintenance runs.
    - The cadence is a monotonic next-due deadline, not a cycle counter.
    - STATUS reports the new top-level non-policy ``maintenance`` block.
    - No new ``policy_progress[*].policy`` identity is introduced.
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    current_monotonic = {"value": 1000.0}
    monkeypatch.setattr(
        task_monitor_mod,
        "_monitor_monotonic",
        lambda: current_monotonic["value"],
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999961901")
    probe = make_queue("maintenance-vacuum-probe")
    for index in range(3):
        probe.write(f"claimed-row-{index}")
    for _ in range(3):
        assert probe.read_one() is not None
    assert probe.stats().total == 3
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    task = TaskMonitor(db_path, spec, config=config)
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        assert probe.stats().total == 0

        status = _read_status_reply(task, ctrl_in, ctrl_out, request_id="status-1")
        maintenance = status["maintenance"]
        assert isinstance(maintenance["last_run_at_ns"], int)
        assert maintenance["vacuum_ok"] is True
        assert maintenance["runtime_prune"] == {
            "candidates": 0,
            "deleted": 0,
            "partial_batches": 0,
        }
        assert maintenance["last_error"] is None
        first_run_at_ns = maintenance["last_run_at_ns"]
        assert {
            progress["policy"] for progress in status["last_policy_progress"]
        } <= set(TASK_MONITOR_CLEANUP_POLICY_NAMES)

        for index in range(2):
            probe.write(f"second-claimed-{index}")
        for _ in range(2):
            assert probe.read_one() is not None
        assert probe.stats().total == 2

        current_monotonic["value"] = 1010.0
        task._next_cycle_due_monotonic = 0.0
        task.process_once()
        drive_task_monitor_until_idle(task)
        assert probe.stats().total == 2
        status = _read_status_reply(task, ctrl_in, ctrl_out, request_id="status-2")
        assert status["maintenance"]["last_run_at_ns"] == first_run_at_ns

        current_monotonic["value"] = 1000.0 + 3600.0 + 50.0
        task._next_cycle_due_monotonic = 0.0
        task.process_once()
        drive_task_monitor_until_idle(task)
        assert probe.stats().total == 0
        status = _read_status_reply(task, ctrl_in, ctrl_out, request_id="status-3")
        assert status["maintenance"]["last_run_at_ns"] > first_run_at_ns
        assert status["maintenance"]["vacuum_ok"] is True
        assert {
            progress["policy"] for progress in status["last_policy_progress"]
        } <= set(TASK_MONITOR_CLEANUP_POLICY_NAMES)
    finally:
        task.stop()


def test_task_monitor_maintenance_opt_out_skips_vacuum(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D1: WEFT_TASK_MONITOR_MAINTENANCE=0 disables the maintenance slice."""

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
            "WEFT_TASK_MONITOR_MAINTENANCE": "0",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999961902")
    probe = make_queue("maintenance-disabled-probe")
    for index in range(2):
        probe.write(f"claimed-row-{index}")
    for _ in range(2):
        assert probe.read_one() is not None
    assert probe.stats().total == 2
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    task = TaskMonitor(db_path, spec, config=config)
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
        assert probe.stats().total == 2

        status = _read_status_reply(task, ctrl_in, ctrl_out, request_id="status-off")
        maintenance = status["maintenance"]
        assert maintenance["last_run_at_ns"] is None
        assert maintenance["vacuum_ok"] is None
        assert maintenance["runtime_prune"] == {
            "candidates": 0,
            "deleted": 0,
            "partial_batches": 0,
        }
        assert maintenance["last_error"] is None
    finally:
        task.stop()


def test_task_monitor_maintenance_prunes_superseded_runtime_state_groups(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: maintenance auto-prunes superseded runtime-state rows conservatively.

    Verifies:
    - Superseded service-owner registry rows are deleted; newest-per-key kept.
    - Task-local ``T{tid}.ctrl_out`` rows are never scanned (decoy survives).
    - A live owner's streaming row survives on fresh tid-mapping proof (decoy).
    - ``tid-mappings`` is excluded: a superseded mapping duplicate survives.
    - STATUS reports the prune counters in the ``maintenance`` block.
    """

    db_path, make_queue = broker_env
    monkeypatch.setattr(
        task_monitor_mod, "upsert_heartbeat", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        task_monitor_mod,
        "RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS",
        0.0,
    )
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": "60",
            "WEFT_TASK_MONITOR_MODE": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    services = make_queue(WEFT_SERVICES_REGISTRY_QUEUE)
    service_key = "_weft.service.maintenance-prune"
    superseded_owner = "1770000000000000100"
    retained_owner = "1770000000000000101"
    superseded_active_id = _write_json_row(
        services,
        _maintenance_service_owner_payload(
            service_key=service_key,
            tid=superseded_owner,
            status=SERVICE_STATUS_ACTIVE,
        ),
    )
    superseded_terminal_id = _write_json_row(
        services,
        _maintenance_service_owner_payload(
            service_key=service_key,
            tid=superseded_owner,
            status=SERVICE_STATUS_TERMINAL,
        ),
    )
    retained_active_id = _write_json_row(
        services,
        _maintenance_service_owner_payload(
            service_key=service_key,
            tid=retained_owner,
            status=SERVICE_STATUS_ACTIVE,
        ),
    )

    live_tid = str(time.time_ns())
    ctrl_out_decoy = make_queue(f"T{live_tid}.ctrl_out")
    ctrl_out_decoy.write("decoy-terminal-envelope")
    streaming = make_queue(WEFT_STREAMING_SESSIONS_QUEUE)
    streaming_decoy_id = _write_json_row(
        streaming,
        {
            "tid": live_tid,
            "session_id": "live-session",
            "queue": f"T{live_tid}.outbox",
        },
    )
    mappings = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    excluded_tid = "1770000000000000300"
    excluded_old_id = _write_json_row(
        mappings,
        {"short": "older-row", "full": excluded_tid, "name": "old"},
    )
    excluded_new_id = _write_json_row(
        mappings,
        {"short": "newer-row", "full": excluded_tid, "name": "new"},
    )
    _write_json_row(
        mappings,
        {"short": live_tid[-10:], "full": live_tid, "name": "live-owner"},
    )
    spec = make_task_monitor_taskspec("1778089999999961903")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    task = TaskMonitor(db_path, spec, config=config)
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)

        remaining_service_ids = set(_queue_json_rows(services))
        assert superseded_active_id not in remaining_service_ids
        assert superseded_terminal_id not in remaining_service_ids
        assert retained_active_id in remaining_service_ids

        assert ctrl_out_decoy.stats().total == 1
        assert streaming_decoy_id in _queue_json_rows(streaming)
        remaining_mapping_ids = set(_queue_json_rows(mappings))
        assert excluded_old_id in remaining_mapping_ids
        assert excluded_new_id in remaining_mapping_ids

        status = _read_status_reply(task, ctrl_in, ctrl_out, request_id="status-prune")
        maintenance = status["maintenance"]
        assert maintenance["vacuum_ok"] is True
        assert maintenance["runtime_prune"] == {
            "candidates": 2,
            "deleted": 2,
            "partial_batches": 0,
        }
        assert maintenance["last_error"] is None
        assert {
            progress["policy"] for progress in status["last_policy_progress"]
        } <= set(TASK_MONITOR_CLEANUP_POLICY_NAMES)
    finally:
        task.stop()
