"""Task monitor task peek-loop tests."""

from __future__ import annotations

import json
import threading
import time

import pytest

import weft.core.monitor.task_monitor as task_monitor_mod
import weft.core.tasks.base as base_task_mod
from weft._constants import (
    CONTROL_PING,
    PONG_EXTENSION_KEY,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MONITOR_SCHEMA_VERSION,
    load_config,
)
from weft.core.monitor.runtime import (
    TaskMonitorProcessorRequest,
    TaskMonitorProcessorResult,
)
from weft.core.monitor.store import MonitorStore, MonitorStoreIngestResult
from weft.core.monitor.task_monitor import (
    TaskMonitorTask,
    make_task_monitor_taskspec,
)
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
    task: TaskMonitorTask,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while task._processor_work_in_flight is not None and time.monotonic() < deadline:
        task.process_once()
        task.wait_for_activity(timeout=0.05)
    assert task._processor_work_in_flight is None


@pytest.fixture(autouse=True)
def clear_processor_requests() -> None:
    PROCESSOR_REQUESTS.clear()
    BLOCKING_PROCESSOR_STARTED.clear()
    BLOCKING_PROCESSOR_RELEASE.clear()


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
    task = TaskMonitorTask(
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
    task = TaskMonitorTask(
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

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999999"),
        config=config,
    )
    try:
        task.process_once()
        drive_task_monitor_until_idle(task)
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
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write("{not-json")

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999986"),
        config=config,
    )
    try:
        task.process_once()
    finally:
        task.stop()

    assert "{not-json" not in list(log_queue.peek_generator())
    assert task._last_processor_success is True
    assert task._last_processed == 1
    assert task._last_deleted == 1
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
            "WEFT_TASK_MONITOR_PROCESSOR": "report_only",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write("{not-json")

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999985"),
        config=config,
    )
    try:
        task.process_once()
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
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:recording_processor",
        }
    )
    task = TaskMonitorTask(
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
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:recording_processor",
        }
    )
    task = TaskMonitorTask(
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
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:recording_processor",
        }
    )
    task = TaskMonitorTask(
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
            "WEFT_TASK_MONITOR_PROCESSOR": "report_only",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999998")
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(json.dumps({"event": "work_started", "tid": "1778084345905438720"}))
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "ping-before"}))

    task = TaskMonitorTask(db_path, spec, config=config)
    try:
        task.process_once()
        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "ping-after"}))
        task.wait_for_activity(timeout=task.next_wait_timeout())
        task.process_once()
    finally:
        task.stop()

    responses = [json.loads(item) for item in ctrl_out.peek_generator()]
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
    assert pong["processor"] == "report_only"
    assert pong["interval_seconds"] == 60
    assert pong["batch_size"] == 10
    assert pong["last_candidate_class_counts"] == {}
    assert pong["last_safe_to_delete_candidates"] == 0
    assert pong["last_cleanup_queue_stats"]
    assert pong["last_cleanup_policy_stats"]
    extended = pong[PONG_EXTENSION_KEY]["task_monitor"]
    assert extended["enabled"] is True
    assert extended["mode"] == "persistent"
    assert extended["processor"] == "report_only"
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
            "WEFT_TASK_MONITOR_PROCESSOR": "report_only",
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

    task = TaskMonitorTask(db_path, spec, config=config)
    try:
        task.process_once()

        def fail_store_cycle(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise AssertionError("PING must not run Monitor store collation")

        monkeypatch.setattr(task, "_run_monitor_store_cycle", fail_store_cycle)
        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "store"}))
        task.wait_for_activity(timeout=task.next_wait_timeout())
        task.process_once()
    finally:
        task.stop()

    responses = [json.loads(item) for item in ctrl_out.peek_generator()]
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
    assert last_cycle["collation_messages_marked_deleted"] == 0


def test_task_monitor_table_delete_requires_delete_processor(
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
            "WEFT_TASK_MONITOR_PROCESSOR": "report_only",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
            "WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED": "1",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1778084345905438722",
    }
    log_queue.write(json.dumps(payload))

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999981"),
        config=config,
    )
    try:
        task.process_once()
    finally:
        task.stop()

    target_rows = [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == payload["tid"]
    ]
    assert target_rows == [payload]
    assert task._last_collation_messages_marked_deleted == 0


def test_task_monitor_table_delete_removes_exact_task_log_rows(
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
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
            "WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED": "1",
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

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999980"),
        config=config,
    )
    try:
        task.process_once()
    finally:
        task.stop()

    target_rows = [
        json.loads(message)
        for message in log_queue.peek_generator()
        if json.loads(message).get("tid") == "1778084345905438723"
    ]
    assert target_rows == []
    assert task._last_collation_messages_marked_deleted >= 1


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
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
            "WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED": "1",
        }
    )
    tid = "1778084345905438729"
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    for index in range(5):
        log_queue.write(
            json.dumps(
                {
                    "event": "task_activity",
                    "status": "running",
                    "tid": tid,
                    "sequence": index,
                }
            )
        )

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999977"),
        config=config,
    )
    try:
        task.process_once()
        store = task._monitor_store
        assert store is not None
        record = store.get_task(tid)
        assert record is not None
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
    assert retained.deleted_mark_chunks == 1
    assert retained.checkpoint_written is True


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
            "WEFT_TASK_MONITOR_BATCH_SIZE": 1,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0.000001",
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
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

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999976"),
        config=config,
    )
    try:
        cycle_started_at = time.monotonic()
        task.process_once()
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


def test_task_monitor_table_delete_tolerates_already_absent_exact_rows(
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
            "WEFT_TASK_MONITOR_PROCESSOR": "report_only",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
            "WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED": "0",
        }
    )
    report_task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999979"),
        config=report_config,
    )
    try:
        report_task.process_once()
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
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
            "WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED": "1",
        }
    )
    delete_task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999978"),
        config=delete_config,
    )
    try:
        delete_task.process_once()
        store = delete_task._monitor_store
        assert store is not None
        record = store.get_task(payload["tid"])
        assert record is not None
        assert record.raw_deleted_at_ns is None
    finally:
        delete_task.stop()

    assert delete_task._last_processor_success is True


def test_task_monitor_failed_summary_disposition_blocks_table_delete(
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
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "stdout",
            "WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED": "1",
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
        TaskMonitorTask,
        "_emit_monitor_store_summary",
        fail_summary,
    )
    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999977"),
        config=config,
    )
    try:
        task.process_once()
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
    assert task._last_collation_messages_marked_deleted >= 1


def test_task_monitor_collated_external_log_precedes_table_delete(
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
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
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

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999970"),
        config=config,
    )
    try:
        task.process_once()
        store = task._monitor_store
        assert store is not None
        record = store.get_task(payload["tid"])
        assert record is not None
        assert record.summary_emitted_at_ns is not None
        assert record.raw_deleted_at_ns is not None
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


def test_task_monitor_terminal_disposition_deletes_task_control_queues_only(
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
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
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
    ctrl_out.write("pong")
    inbox.write("input")
    outbox.write("result")
    reserved.write("reserved")

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999967"),
        config=config,
    )
    try:
        task.process_once()
        store = task._monitor_store
        assert store is not None
        record = store.get_task(tid)
        assert record is not None
        assert record.summary_emitted_at_ns is not None
        assert record.task_control_deleted_at_ns is not None
        assert record.disposition_reason == "terminal"
        assert record.disposition_at_ns is not None
        assert task._last_control_rows_deleted == 2
    finally:
        task.stop()

    assert list(ctrl_in.peek_generator()) == []
    assert list(ctrl_out.peek_generator()) == []
    assert list(inbox.peek_generator()) == ["input"]
    assert list(outbox.peek_generator()) == ["result"]
    assert list(reserved.peek_generator()) == ["reserved"]


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
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
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
    ctrl_in.write("stop")
    ctrl_out.write("pong")

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999966"),
        config=config,
    )
    try:
        task.process_once()
        store = task._monitor_store
        assert store is not None
        record = store.get_task(tid)
        assert record is not None
        assert record.disposition_reason == "terminal"
        assert record.task_control_deleted_at_ns is not None
        assert task._last_control_rows_deleted == 0
    finally:
        task.stop()

    assert list(ctrl_in.peek_generator()) == ["stop"]
    assert list(ctrl_out.peek_generator()) == ["pong"]


def test_task_monitor_collated_external_failure_blocks_table_delete(
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
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "collated",
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
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

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999969"),
        config=config,
    )
    try:
        task.process_once()
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
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "raw",
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
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

    task = TaskMonitorTask(
        db_path,
        make_task_monitor_taskspec("1778089999999999968"),
        config=config,
    )
    try:
        task.process_once()
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
            "WEFT_TASK_MONITOR_PROCESSOR": "delete",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999994")
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write("{not-json")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    task = TaskMonitorTask(db_path, spec, config=config)
    try:
        task.process_once()
        cached_policy_stats = list(task._last_cleanup_policy_stats)
        assert cached_policy_stats

        def fail_cleanup(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise AssertionError("PING must not run cleanup")

        monkeypatch.setattr(task_monitor_mod, "run_task_monitor_cleanup", fail_cleanup)
        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "cached"}))
        task.wait_for_activity(timeout=task.next_wait_timeout())
        task.process_once()
    finally:
        task.stop()

    responses = [json.loads(item) for item in ctrl_out.peek_generator()]
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
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:blocking_processor",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999993")
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(json.dumps({"event": "work_started", "tid": "1778084345905438720"}))
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    task = TaskMonitorTask(db_path, spec, config=config)
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
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:failing_processor",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(json.dumps({"event": "work_failed", "tid": "1778084345905438720"}))

    task = TaskMonitorTask(
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
            "WEFT_TASK_MONITOR_PROCESSOR": "tests.tasks.test_task_monitor:recording_processor",
        }
    )
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(json.dumps({"event": "work_started", "tid": "1778084345905438720"}))

    task = TaskMonitorTask(
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
