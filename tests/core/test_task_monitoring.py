"""Task-monitor processor contract tests."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

import weft.core.task_monitoring as task_monitoring_mod
from weft._constants import (
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    load_config,
)
from weft.core.task_monitoring import (
    TaskMonitorCandidate,
    TaskMonitorProcessorRequest,
    TaskMonitorProcessorResult,
    TaskMonitorRuntimeConfig,
    build_task_monitor_cycle_snapshot,
    report_only_processor,
    resolve_task_monitor_processor,
    task_log_seen_candidate,
)

pytestmark = [pytest.mark.shared]


def custom_processor(
    request: TaskMonitorProcessorRequest,
) -> TaskMonitorProcessorResult:
    return TaskMonitorProcessorResult(
        success=True,
        processed=len(request.candidates),
        reported=len(request.candidates),
    )


def noop(_request: TaskMonitorProcessorRequest) -> TaskMonitorProcessorResult:
    return TaskMonitorProcessorResult(success=True)


def _taskspec_payload(
    tid: str,
    *,
    name: str = "sample-task",
    status: str = "running",
    completed_at: int | None = None,
) -> dict[str, Any]:
    return {
        "tid": tid,
        "name": name,
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "runner": {"name": "host", "options": {}},
        },
        "io": {
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {
            "status": status,
            "started_at": time.time_ns(),
            "completed_at": completed_at,
        },
        "metadata": {"owner": "tests"},
    }


def _write_log(ctx: Any, payload: dict[str, Any]) -> None:
    queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        queue.write(json.dumps(payload))
    finally:
        queue.close()


def _write_json(ctx: Any, queue_name: str, payload: dict[str, Any]) -> None:
    queue = ctx.queue(queue_name, persistent=False)
    try:
        queue.write(json.dumps(payload))
    finally:
        queue.close()


def test_runtime_config_reads_loaded_weft_config() -> None:
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "0",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": str(HEARTBEAT_MIN_INTERVAL_SECONDS),
            "WEFT_TASK_MONITOR_BATCH_SIZE": 12,
            "WEFT_TASK_MONITOR_PROCESSOR": "report_only",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
            "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS": 3.5,
        }
    )

    runtime_config = TaskMonitorRuntimeConfig.from_config(config)

    assert runtime_config.enabled is False
    assert runtime_config.interval_seconds == HEARTBEAT_MIN_INTERVAL_SECONDS
    assert runtime_config.batch_size == 12
    assert runtime_config.processor == "report_only"
    assert runtime_config.log_sink == "none"
    assert runtime_config.restart_backoff_seconds == 3.5


def test_resolve_report_only_processor() -> None:
    assert resolve_task_monitor_processor("report_only") is report_only_processor


def test_resolve_custom_processor() -> None:
    processor = resolve_task_monitor_processor(
        "tests.core.test_task_monitoring:custom_processor"
    )

    assert callable(processor)
    assert getattr(processor, "__name__", "") == "custom_processor"


def test_report_only_reports_all_candidates(weft_harness) -> None:
    candidate = TaskMonitorCandidate(
        candidate_id="weft.log.tasks:1:task_log_tid_seen:abc",
        tid="1778084345905438720",
        queue="weft.log.tasks",
        message_id=1,
        candidate_class="task_log_tid_seen",
        reason="task ID observed in task log",
        safe_to_delete=False,
    )
    request = TaskMonitorProcessorRequest(
        context=weft_harness.context,
        config=TaskMonitorRuntimeConfig(),
        cycle_id="cycle-1",
        monitor_tid="1778089999999999999",
        candidates=(candidate,),
        now_ns=1,
    )

    result = report_only_processor(request)

    assert result.success is True
    assert result.processed == 1
    assert result.reported == 1
    assert result.deleted == 0


def test_task_log_seen_candidate_is_stable_for_same_row() -> None:
    first = task_log_seen_candidate(
        queue_name="weft.log.tasks",
        message='{"event": "work_started", "tid": "1778084345905438720"}',
        message_id=123,
    )
    second = task_log_seen_candidate(
        queue_name="weft.log.tasks",
        message='{"event": "work_started", "tid": "1778084345905438720"}',
        message_id=123,
    )

    assert first is not None
    assert second is not None
    assert first.candidate_id == second.candidate_id
    assert first.tid == "1778084345905438720"
    assert first.safe_to_delete is False


def test_cycle_snapshot_reduces_large_task_log_by_latest_tid(
    weft_harness,
) -> None:
    ctx = weft_harness.context
    target_tid = "1778084345905438720"
    for index in range(25):
        tid = f"17780843459054387{index:02d}"
        _write_log(
            ctx,
            {
                "event": "work_started",
                "status": "running",
                "tid": tid,
                "taskspec": _taskspec_payload(tid),
            },
        )
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": target_tid,
            "taskspec": _taskspec_payload(target_tid),
        },
    )
    _write_log(
        ctx,
        {
            "event": "work_completed",
            "status": "completed",
            "tid": target_tid,
            "taskspec": _taskspec_payload(
                target_tid,
                status="completed",
                completed_at=time.time_ns(),
            ),
        },
    )

    snapshot = build_task_monitor_cycle_snapshot(ctx, since_timestamp=0)

    target = next(
        candidate for candidate in snapshot.candidates if candidate.tid == target_tid
    )
    assert snapshot.events_scanned >= 27
    assert target.candidate_class == "terminal_log"
    assert target.metadata["status"] == "completed"


def test_cycle_snapshot_keeps_wazuh_like_failure_owned_by_task(
    weft_harness,
) -> None:
    ctx = weft_harness.context
    tid = "1778084345905438721"
    _write_log(
        ctx,
        {
            "event": "work_failed",
            "status": "failed",
            "tid": tid,
            "error": "Raw observation payload changed for an existing Wazuh source ID.",
            "taskspec": _taskspec_payload(
                tid,
                name="wazuh-case-rollup",
                status="failed",
                completed_at=time.time_ns(),
            ),
        },
    )

    snapshot = build_task_monitor_cycle_snapshot(ctx, since_timestamp=0)

    candidate = next(
        candidate for candidate in snapshot.candidates if candidate.tid == tid
    )
    assert candidate.candidate_class == "domain_failure"
    assert candidate.metadata["failure_owner"] == "task_or_runner"
    assert candidate.safe_to_delete is False


def test_cycle_snapshot_reports_result_without_terminal_without_deleting(
    weft_harness,
) -> None:
    ctx = weft_harness.context
    tid = "1778084345905438722"
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": _taskspec_payload(tid),
        },
    )
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    try:
        outbox.write(json.dumps({"ok": True}))

        snapshot = build_task_monitor_cycle_snapshot(ctx, since_timestamp=0)

        candidate = next(
            candidate for candidate in snapshot.candidates if candidate.tid == tid
        )
        assert candidate.candidate_class == "result_without_terminal"
        assert candidate.metadata["failure_owner"] == "weft_lifecycle"
        assert candidate.safe_to_delete is False
        assert outbox.peek_one() is not None
    finally:
        outbox.close()


def test_cycle_snapshot_reports_claimed_outbox_as_recovery_diagnostic(
    weft_harness,
) -> None:
    ctx = weft_harness.context
    tid = "1778084345905438723"
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": _taskspec_payload(tid),
        },
    )
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    try:
        outbox.write(json.dumps({"case_count": 3}))
        assert outbox.read_one() is not None

        snapshot = build_task_monitor_cycle_snapshot(ctx, since_timestamp=0)

        candidate = next(
            candidate for candidate in snapshot.candidates if candidate.tid == tid
        )
        assert candidate.candidate_class == "claimed_result_without_terminal"
        assert candidate.metadata["failure_owner"] == "weft_lifecycle"
        assert candidate.metadata["claimed_messages"] == 1
        assert candidate.safe_to_delete is False
    finally:
        outbox.close()


def test_cycle_snapshot_reports_nonterminal_completed_state_as_weft_conflict(
    weft_harness,
) -> None:
    ctx = weft_harness.context
    tid = "1778084345905438724"
    _write_log(
        ctx,
        {
            "event": "work_failed",
            "status": "running",
            "tid": tid,
            "error": "task failed",
            "taskspec": _taskspec_payload(
                tid,
                status="running",
                completed_at=time.time_ns(),
            ),
        },
    )

    snapshot = build_task_monitor_cycle_snapshot(ctx, since_timestamp=0)

    candidate = next(
        candidate for candidate in snapshot.candidates if candidate.tid == tid
    )
    assert candidate.candidate_class == "runtime_conflict"
    assert candidate.reason == "nonterminal_status_with_completed_at"
    assert candidate.metadata["failure_owner"] == "weft_lifecycle"


def test_cycle_snapshot_reports_superseded_task_log_cleanup_candidate(
    weft_harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_monitoring_mod,
        "RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS",
        0,
    )
    ctx = weft_harness.context
    tid = "1778084345905438725"
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": _taskspec_payload(tid),
        },
    )
    _write_log(
        ctx,
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "taskspec": _taskspec_payload(
                tid,
                status="completed",
                completed_at=time.time_ns(),
            ),
        },
    )

    snapshot = build_task_monitor_cycle_snapshot(ctx, since_timestamp=0)

    candidate = next(
        candidate
        for candidate in snapshot.candidates
        if candidate.tid == tid
        and candidate.candidate_class == "nonterminal_task_log_superseded"
    )
    assert candidate.queue == WEFT_GLOBAL_LOG_QUEUE
    assert candidate.safe_to_delete is True
    assert candidate.metadata["cleanup_candidate"] is True


def test_cycle_snapshot_reports_terminal_task_local_cleanup_candidates(
    weft_harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_monitoring_mod,
        "RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS",
        0,
    )
    ctx = weft_harness.context
    tid = "1778084345905438728"
    _write_log(
        ctx,
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "taskspec": _taskspec_payload(
                tid,
                status="completed",
                completed_at=time.time_ns(),
            ),
        },
    )
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    try:
        ctrl_out.write(
            json.dumps(
                {
                    "type": "terminal",
                    "source": "task",
                    "tid": tid,
                    "status": "completed",
                    "return_code": 0,
                }
            )
        )
        outbox.write(json.dumps({"ok": True}))

        snapshot = build_task_monitor_cycle_snapshot(ctx, since_timestamp=0)

        classes = {
            candidate.candidate_class: candidate
            for candidate in snapshot.candidates
            if candidate.tid == tid
        }
        ctrl_candidate = classes["terminal_ctrl_out_archived"]
        outbox_candidate = classes["terminal_result_outbox_archived"]
        assert ctrl_candidate.queue == f"T{tid}.ctrl_out"
        assert ctrl_candidate.safe_to_delete is True
        assert outbox_candidate.queue == f"T{tid}.outbox"
        assert outbox_candidate.safe_to_delete is True
        assert ctrl_out.peek_one() is not None
        assert outbox.peek_one() is not None
    finally:
        ctrl_out.close()
        outbox.close()


def test_cycle_snapshot_reports_runtime_state_cleanup_candidates(
    weft_harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_monitoring_mod,
        "RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS",
        0,
    )
    ctx = weft_harness.context
    tid = "1778084345905438726"
    _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "5438726", "full": tid, "name": "old"},
    )
    _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "5438726", "full": tid, "name": "new"},
    )
    stale_manager_tid = "1778084345905438727"
    _write_json(
        ctx,
        WEFT_MANAGERS_REGISTRY_QUEUE,
        {"tid": stale_manager_tid, "status": "active", "name": "manager"},
    )

    snapshot = build_task_monitor_cycle_snapshot(ctx, since_timestamp=0)

    classes_by_tid = {
        (candidate.tid, candidate.candidate_class): candidate
        for candidate in snapshot.candidates
    }
    superseded_mapping = classes_by_tid[(tid, "superseded_tid_mapping")]
    stale_manager = classes_by_tid[(stale_manager_tid, "stale_manager_registry")]
    assert superseded_mapping.queue == WEFT_TID_MAPPINGS_QUEUE
    assert superseded_mapping.safe_to_delete is True
    assert stale_manager.queue == WEFT_MANAGERS_REGISTRY_QUEUE
    assert stale_manager.safe_to_delete is True
