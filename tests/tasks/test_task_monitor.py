"""Task monitor task peek-loop tests."""

from __future__ import annotations

import json
import threading
import time

import pytest

import weft.core.tasks.task_monitor as task_monitor_mod
from weft._constants import (
    CONTROL_PING,
    PONG_EXTENSION_KEY,
    TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED,
    WEFT_GLOBAL_LOG_QUEUE,
    load_config,
)
from weft.core.task_monitoring import (
    TaskMonitorProcessorRequest,
    TaskMonitorProcessorResult,
)
from weft.core.tasks import TaskMonitorTask
from weft.core.tasks.task_monitor import make_task_monitor_taskspec

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
    assert task._last_processed == 1
    assert task._last_deleted == 0
    assert task._last_reported == 1


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
        started_at = time.monotonic()
        task.wait_for_activity(timeout=task.next_wait_timeout())
        assert time.monotonic() - started_at < 0.5
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
        started_at = time.monotonic()
        task.wait_for_activity(timeout=task.next_wait_timeout())
        assert time.monotonic() - started_at < 0.5
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
    assert any(
        stat["policy"] == TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED
        for stat in pong["last_cleanup_policy_stats"]
    )
    assert extended["last_cycle"]["warnings"] == []
    assert extended["last_cycle"]["errors"] == []
    assert log_queue.peek_one() is not None


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

        assert elapsed < BLOCKING_PROCESSOR_TIMEOUT_SECONDS * 0.5
        assert BLOCKING_PROCESSOR_STARTED.wait(timeout=2.0)
        assert task._processor_work_in_flight is not None

        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "during"}))
        task.process_once()

        responses = [json.loads(item) for item in ctrl_out.peek_generator()]
        pong = next(
            response
            for response in responses
            if response["command"] == CONTROL_PING
            and response.get("request_id") == "during"
        )
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
