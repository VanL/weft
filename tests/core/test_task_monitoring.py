"""Task-monitor processor contract tests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from weft._constants import (
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    WEFT_GLOBAL_LOG_QUEUE,
    load_config,
)
from weft.core.monitor.runtime import (
    TaskMonitorProcessorRequest,
    TaskMonitorProcessorResult,
    TaskMonitorRuntimeConfig,
    build_task_monitor_cycle_snapshot,
    resolve_task_monitor_processor,
    task_log_seen_candidate,
)
from weft.core.monitor.task_monitor import (
    TaskMonitorTask,
    make_task_monitor_taskspec,
)
from weft.core.pruning.retention import (
    RetentionPruneConfig,
    run_retention_prune_for_context,
)
from weft.helpers import iter_queue_entries

pytestmark = [pytest.mark.shared]

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_task_monitor_old_tasks_module_path_is_removed() -> None:
    old_module = REPO_ROOT / "weft" / "core" / "tasks" / "task_monitor.py"
    old_import = "weft.core.tasks." + "task_monitor"
    old_reexport = "from weft.core.tasks import " + "TaskMonitorTask"
    offenders: list[str] = []
    for root_name in ("weft", "tests"):
        for path in (REPO_ROOT / root_name).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if old_import in text or old_reexport in text:
                offenders.append(path.relative_to(REPO_ROOT).as_posix())

    assert not old_module.exists()
    assert offenders == []


def serve_log_events(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in capsys.readouterr().err.splitlines()
        if line.strip()
    ]


def drive_task_monitor_until_idle(
    monitor: TaskMonitorTask,
    *,
    timeout: float = 20.0,
) -> None:
    deadline = time.monotonic() + timeout
    while (
        monitor._builtin_cycle_work_in_flight is not None
        or monitor._processor_work_in_flight is not None
        or monitor._control_cleanup_work_in_flight is not None
        or monitor._has_pending_worker_results()
    ) and time.monotonic() < deadline:
        monitor.process_once()
        monitor.wait_for_activity(timeout=0.05)
    assert monitor._builtin_cycle_work_in_flight is None
    assert monitor._processor_work_in_flight is None
    assert monitor._control_cleanup_work_in_flight is None
    monitor._drain_worker_results()


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


def test_runtime_config_reads_loaded_weft_config() -> None:
    config = load_config(
        {
            "WEFT_TASK_MONITOR_ENABLED": "0",
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS": str(HEARTBEAT_MIN_INTERVAL_SECONDS),
            "WEFT_TASK_MONITOR_BATCH_SIZE": 12,
            "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": 120,
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": 172800.0,
            "WEFT_LOG_TASKS_EXTERNAL_PATH": "task-log.jsonl",
            "WEFT_LOG_TASKS_EXTERNAL_MODE": "raw",
            "WEFT_TASK_MONITOR_PROCESSOR": "report_only",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
            "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS": 3.5,
        }
    )

    runtime_config = TaskMonitorRuntimeConfig.from_config(config)

    assert runtime_config.enabled is False
    assert runtime_config.interval_seconds == HEARTBEAT_MIN_INTERVAL_SECONDS
    assert runtime_config.batch_size == 12
    assert runtime_config.task_log_scan_limit == 120
    assert runtime_config.task_log_retention_period_seconds == 172800.0
    assert runtime_config.task_log_external_path == "task-log.jsonl"
    assert runtime_config.task_log_external_enabled is True
    assert runtime_config.task_log_external_mode == "raw"
    assert runtime_config.processor == "report_only"
    assert runtime_config.log_sink == "none"
    assert runtime_config.restart_backoff_seconds == 3.5


def test_runtime_config_defaults_to_delete_builtin() -> None:
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
        drive_task_monitor_until_idle(monitor)
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
        drive_task_monitor_until_idle(monitor)
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
        drive_task_monitor_until_idle(monitor)
    finally:
        monitor.cleanup()

    events = serve_log_events(capsys)
    assert any(
        event.get("event") == "task_monitor_processor_error"
        and event.get("processor") == "jsonl_then_delete"
        for event in events
    )


def test_resolve_custom_processor() -> None:
    processor = resolve_task_monitor_processor(
        "tests.core.test_task_monitoring:custom_processor"
    )

    assert callable(processor)
    assert getattr(processor, "__name__", "") == "custom_processor"


@pytest.mark.parametrize(
    "processor_name", ["delete", "report_only", "jsonl_then_delete"]
)
def test_builtin_processors_are_not_resolved_through_custom_hook(
    processor_name: str,
) -> None:
    with pytest.raises(ValueError, match="handled by TaskMonitorTask"):
        resolve_task_monitor_processor(processor_name)


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
