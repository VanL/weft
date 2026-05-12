"""Task-monitor processor contract tests."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

import weft.core.task_monitoring as task_monitoring_mod
from weft._constants import (
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    SERVICE_OWNER_SCHEMA,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    load_config,
)
from weft.core.pruning.retention import (
    RetentionPruneConfig,
    run_retention_prune_for_context,
)
from weft.core.service_convergence import build_manager_service_payload
from weft.core.task_monitoring import (
    TaskMonitorCandidate,
    TaskMonitorProcessorRequest,
    TaskMonitorProcessorResult,
    TaskMonitorRuntimeConfig,
    build_task_monitor_cycle_snapshot,
    delete_processor,
    report_only_processor,
    resolve_task_monitor_processor,
    task_log_seen_candidate,
)
from weft.core.tasks.task_monitor import TaskMonitorTask, make_task_monitor_taskspec
from weft.helpers import iter_queue_entries

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


def serve_log_events(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in capsys.readouterr().err.splitlines()
        if line.strip()
    ]


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


def _write_json(ctx: Any, queue_name: str, payload: dict[str, Any]) -> int:
    return _write_raw(ctx, queue_name, json.dumps(payload), persistent=False)


def _manager_service_payload(ctx: Any, *, tid: str) -> dict[str, Any]:
    return build_manager_service_payload(
        context=ctx,
        tid=tid,
        name="manager",
        status="active",
        queues={
            "requests": WEFT_SPAWN_REQUESTS_QUEUE,
            "ctrl_in": f"T{tid}.ctrl_in",
            "ctrl_out": f"T{tid}.ctrl_out",
            "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
        },
        runtime_handle={},
    )


def _write_raw(
    ctx: Any,
    queue_name: str,
    message: str,
    *,
    persistent: bool = False,
) -> int:
    queue = ctx.queue(queue_name, persistent=persistent)
    try:
        message_id = queue.write(message)
        if message_id is not None:
            return int(message_id)
        latest: int | None = None
        for body, timestamp in iter_queue_entries(queue):
            if body == message:
                latest = int(timestamp)
        assert latest is not None
        return latest
    finally:
        queue.close()


def _read_ids(
    ctx: Any,
    queue_name: str,
    *,
    persistent: bool = False,
) -> set[int]:
    queue = ctx.queue(queue_name, persistent=persistent)
    try:
        return {int(timestamp) for _body, timestamp in iter_queue_entries(queue)}
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


def test_runtime_config_defaults_to_delete_processor() -> None:
    runtime_config = TaskMonitorRuntimeConfig.from_config(load_config({}))

    assert runtime_config.processor == "delete"


def test_task_monitor_operational_log_emits_config_and_cycle(
    weft_harness,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = dict(weft_harness.context.config)
    config.update(
        {
            MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: True,
            "WEFT_MANAGER_SERVE_LOG_LEVEL": "info",
            "WEFT_TASK_MONITOR_ENABLED": True,
            "WEFT_TASK_MONITOR_PROCESSOR": "report_only",
            "WEFT_TASK_MONITOR_BATCH_SIZE": 5,
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999001")
    spec.metadata["parent_tid"] = "1778089999999999000"
    monitor = TaskMonitorTask(
        weft_harness.context.broker_target,
        spec,
        config=config,
    )
    try:
        monitor.process_once()
    finally:
        monitor.cleanup()

    events = serve_log_events(capsys)
    assert any(event.get("event") == "task_monitor_config" for event in events)
    cycle = next(
        event for event in events if event.get("event") == "task_monitor_cycle"
    )
    assert cycle["manager_tid"] == "1778089999999999000"
    assert cycle["component"] == "task_monitor"
    assert cycle["processor"] == "report_only"
    assert "events_scanned" in cycle


def test_task_monitor_operational_log_off_is_silent(
    weft_harness,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = dict(weft_harness.context.config)
    config.update(
        {
            MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: True,
            "WEFT_MANAGER_SERVE_LOG_LEVEL": "off",
            "WEFT_TASK_MONITOR_ENABLED": True,
            "WEFT_TASK_MONITOR_PROCESSOR": "report_only",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999002")
    monitor = TaskMonitorTask(
        weft_harness.context.broker_target,
        spec,
        config=config,
    )
    try:
        monitor.process_once()
    finally:
        monitor.cleanup()

    assert serve_log_events(capsys) == []


def test_task_monitor_operational_log_reports_processor_error(
    weft_harness,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = dict(weft_harness.context.config)
    config.update(
        {
            MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: True,
            "WEFT_MANAGER_SERVE_LOG_LEVEL": "info",
            "WEFT_TASK_MONITOR_ENABLED": True,
            "WEFT_TASK_MONITOR_PROCESSOR": "jsonl_then_delete",
        }
    )
    spec = make_task_monitor_taskspec("1778089999999999003")
    monitor = TaskMonitorTask(
        weft_harness.context.broker_target,
        spec,
        config=config,
    )
    try:
        monitor.process_once()
    finally:
        monitor.cleanup()

    events = serve_log_events(capsys)
    assert any(
        event.get("event") == "task_monitor_processor_error"
        and event.get("processor") == "jsonl_then_delete"
        for event in events
    )


def test_resolve_report_only_processor() -> None:
    assert resolve_task_monitor_processor("report_only") is report_only_processor


def test_resolve_delete_processor() -> None:
    assert resolve_task_monitor_processor("delete") is delete_processor


def test_jsonl_then_delete_is_reserved_until_logging_callback_lands(
    weft_harness,
) -> None:
    processor = resolve_task_monitor_processor("jsonl_then_delete")
    request = TaskMonitorProcessorRequest(
        context=weft_harness.context,
        config=TaskMonitorRuntimeConfig(processor="jsonl_then_delete"),
        cycle_id="cycle-logging-later",
        monitor_tid="1778089999999999999",
        candidates=(),
        now_ns=1,
    )

    result = processor(request)

    assert result.success is False
    assert result.deleted == 0
    assert "logging callback" in result.errors[0]


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


def test_delete_processor_deletes_only_safe_exact_candidates(weft_harness) -> None:
    ctx = weft_harness.context
    tid = "1778084345905438730"
    safe_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "5438730", "full": tid, "name": "old"},
    )
    unsafe_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "5438730", "full": tid, "name": "active"},
    )
    missing_id = safe_id - 1
    while missing_id in {safe_id, unsafe_id}:
        missing_id -= 1
    candidates = (
        TaskMonitorCandidate(
            candidate_id="safe-runtime",
            tid=tid,
            queue=WEFT_TID_MAPPINGS_QUEUE,
            message_id=safe_id,
            candidate_class="superseded_tid_mapping",
            reason="older duplicate mapping",
            safe_to_delete=True,
        ),
        TaskMonitorCandidate(
            candidate_id="unsafe-runtime",
            tid=tid,
            queue=WEFT_TID_MAPPINGS_QUEUE,
            message_id=unsafe_id,
            candidate_class="task_log_tid_seen",
            reason="active evidence",
            safe_to_delete=False,
        ),
        TaskMonitorCandidate(
            candidate_id="missing-runtime",
            tid=tid,
            queue=WEFT_TID_MAPPINGS_QUEUE,
            message_id=missing_id,
            candidate_class="superseded_tid_mapping",
            reason="already cleaned",
            safe_to_delete=True,
        ),
        TaskMonitorCandidate(
            candidate_id="ambiguous-runtime",
            tid=tid,
            queue=None,
            message_id=None,
            candidate_class="superseded_tid_mapping",
            reason="no exact row",
            safe_to_delete=True,
        ),
    )
    request = TaskMonitorProcessorRequest(
        context=ctx,
        config=TaskMonitorRuntimeConfig(processor="delete"),
        cycle_id="cycle-delete",
        monitor_tid="1778089999999999999",
        candidates=candidates,
        now_ns=1,
    )

    result = delete_processor(request)

    remaining = _read_ids(ctx, WEFT_TID_MAPPINGS_QUEUE)
    assert result.success is True
    assert result.processed == 4
    assert result.deleted == 1
    assert len(result.warnings) == 2
    assert safe_id not in remaining
    assert unsafe_id in remaining


def test_delete_processor_deletes_safe_persistent_outbox_candidate(
    weft_harness,
) -> None:
    ctx = weft_harness.context
    tid = "1778084345905438731"
    queue_name = f"T{tid}.outbox"
    safe_id = _write_raw(ctx, queue_name, '{"ok": true}', persistent=True)
    claimed_id = _write_raw(ctx, queue_name, '{"claimed": true}', persistent=True)
    request = TaskMonitorProcessorRequest(
        context=ctx,
        config=TaskMonitorRuntimeConfig(processor="delete"),
        cycle_id="cycle-delete-outbox",
        monitor_tid="1778089999999999999",
        candidates=(
            TaskMonitorCandidate(
                candidate_id="safe-outbox",
                tid=tid,
                queue=queue_name,
                message_id=safe_id,
                candidate_class="terminal_result_outbox_archived",
                reason="terminal result archived",
                safe_to_delete=True,
            ),
            TaskMonitorCandidate(
                candidate_id="claimed-outbox",
                tid=tid,
                queue=queue_name,
                message_id=claimed_id,
                candidate_class="claimed_result_without_terminal",
                reason="claimed result requires recovery",
                safe_to_delete=False,
            ),
        ),
        now_ns=1,
    )

    result = delete_processor(request)

    remaining = _read_ids(ctx, queue_name, persistent=True)
    assert result.success is True
    assert result.deleted == 1
    assert safe_id not in remaining
    assert claimed_id in remaining


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


def test_canonical_retention_prune_sees_superseded_rows_across_monitor_batches(
    weft_harness,
) -> None:
    ctx = weft_harness.context
    target_tid = "1778084345905438729"
    old_id = _write_raw(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        json.dumps(
            {
                "event": "work_started",
                "status": "running",
                "tid": target_tid,
                "taskspec": _taskspec_payload(target_tid),
            }
        ),
    )
    for index in range(5):
        tid = f"17780843459054388{index:02d}"
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
            "tid": target_tid,
            "taskspec": _taskspec_payload(
                target_tid,
                status="completed",
                completed_at=time.time_ns(),
            ),
        },
    )

    batch_snapshot = build_task_monitor_cycle_snapshot(ctx, since_timestamp=0, limit=1)
    prune_result = run_retention_prune_for_context(
        ctx,
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            min_age_seconds=0,
            limit=20,
        ),
    )

    assert not any(
        candidate.message_id == old_id and candidate.safe_to_delete
        for candidate in batch_snapshot.candidates
    )
    assert {
        (candidate.message_id, candidate.candidate_class)
        for candidate in prune_result.candidates
    } >= {(old_id, "nonterminal_task_log_superseded")}


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
        WEFT_SERVICES_REGISTRY_QUEUE,
        _manager_service_payload(ctx, tid=stale_manager_tid),
    )
    malformed_id = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        {
            "schema": SERVICE_OWNER_SCHEMA,
            "service_key": "bad",
            "service_type": "manager",
            "owner_tid": "not-a-tid",
            "status": "active",
        },
    )

    snapshot = build_task_monitor_cycle_snapshot(ctx, since_timestamp=0)

    classes_by_tid = {
        (candidate.tid, candidate.candidate_class): candidate
        for candidate in snapshot.candidates
    }
    superseded_mapping = classes_by_tid[(tid, "superseded_tid_mapping")]
    stale_manager = classes_by_tid[(stale_manager_tid, "stale_manager_registry")]
    malformed = next(
        candidate
        for candidate in snapshot.candidates
        if candidate.message_id == malformed_id
    )
    assert superseded_mapping.queue == WEFT_TID_MAPPINGS_QUEUE
    assert superseded_mapping.safe_to_delete is True
    assert stale_manager.queue == WEFT_SERVICES_REGISTRY_QUEUE
    assert stale_manager.safe_to_delete is True
    assert malformed.reason == "malformed_service_owner_row"
    assert malformed.safe_to_delete is True
