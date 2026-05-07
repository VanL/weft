"""Task-monitor processor contract tests."""

from __future__ import annotations

import pytest

from weft._constants import HEARTBEAT_MIN_INTERVAL_SECONDS, load_config
from weft.core.task_monitoring import (
    TaskMonitorCandidate,
    TaskMonitorProcessorRequest,
    TaskMonitorProcessorResult,
    TaskMonitorRuntimeConfig,
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
