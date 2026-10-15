"""Tests for pure Monitor task-log collation reducers."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_LOG_TASKS_RAW_BODY_PREVIEW_BYTES,
    WRAPPER_LOST_ERROR,
)
from weft.core.monitor.collation import (
    ControlTerminalObservation,
    select_control_terminal_observation,
    selected_terminal_identity,
    update_from_task_log_row,
)
from weft.core.queue_window import DecodedQueueWindowRow, QueueWindowRow
from weft.core.task_evidence import QueueAckTarget, TaskEvidenceSnapshot

pytestmark = [pytest.mark.shared]


def _row(message_id: int, payload: dict[str, Any]) -> DecodedQueueWindowRow:
    return DecodedQueueWindowRow(
        raw=QueueWindowRow(
            queue=WEFT_GLOBAL_LOG_QUEUE,
            body=json.dumps(payload),
            message_id=message_id,
        ),
        payload=payload,
    )


def test_monitor_collation_captures_successful_task_summary_without_reserved_probe() -> (
    None
):
    payload = {
        "event": "work_completed",
        "status": "completed",
        "tid": "1779000000000000001",
        "taskspec": {
            "tid": "1779000000000000001",
            "version": "1.0",
            "name": "echo",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "runner": {"name": "host"},
            },
            "io": {"inputs": {"inbox": "T1779000000000000001.inbox"}},
            "state": {
                "status": "completed",
                "return_code": 0,
                "started_at": 1779000000000001000,
                "completed_at": 1779000000000002000,
                "peak_memory": 12,
            },
            "metadata": {"parent_tid": "1778999999999999999", "role": "worker"},
        },
    }

    update = update_from_task_log_row(_row(1779000000000003000, payload))

    assert update is not None
    assert update.tid == "1779000000000000001"
    assert update.name == "echo"
    assert update.runner == "host"
    assert update.parent_tid == "1778999999999999999"
    assert update.role == "worker"
    assert update.terminal_seen is True
    assert update.terminal_status == "completed"
    assert update.return_code == 0
    assert update.reserved_probe_needed is False
    assert update.taskspec_summary["spec"]["function_target"] == (
        "tests.tasks.sample_targets:echo_payload"
    )
    assert update.resources["peak_memory"] == 12


def test_monitor_collation_marks_failure_like_terminal_for_reserved_probe() -> None:
    payload = {
        "event": "work_failed",
        "status": "failed",
        "tid": "1779000000000000002",
        "taskspec": {
            "tid": "1779000000000000002",
            "version": "1.0",
            "name": "fail",
            "state": {"status": "failed", "return_code": 1},
        },
    }

    update = update_from_task_log_row(_row(1779000000000004000, payload))

    assert update is not None
    assert update.terminal_seen is True
    assert update.terminal_status == "failed"
    assert update.return_code == 1
    assert update.reserved_probe_needed is True


def test_monitor_collation_does_not_treat_activity_status_as_terminal() -> None:
    payload = {
        "event": "task_activity",
        "status": "failed",
        "tid": "1779000000000000003",
        "activity": "working",
    }

    update = update_from_task_log_row(_row(1779000000000004500, payload))

    assert update is not None
    assert update.terminal_seen is False
    assert update.terminal_status is None
    assert update.reserved_probe_needed is False


def test_monitor_collation_ignores_malformed_rows() -> None:
    row = DecodedQueueWindowRow(
        raw=QueueWindowRow(
            queue=WEFT_GLOBAL_LOG_QUEUE,
            body="{not-json",
            message_id=1779000000000005000,
        ),
        payload=None,
        malformed_reason="invalid_json",
    )

    assert update_from_task_log_row(row) is None


@pytest.mark.parametrize("task_message_id", [10, 30])
def test_control_projection_retains_task_verdict_against_supervisor_fallback(
    task_message_id: int,
) -> None:
    """Persisted task evidence keeps authority even after its queue row disappears."""
    supervisor = ControlTerminalObservation(
        source="manager",
        status="failed",
        queue="T1.ctrl_out",
        message_id=20,
        reported_at_ns=500,
        first_observed_at_ns=1000,
        error=WRAPPER_LOST_ERROR,
    )
    task = ControlTerminalObservation(
        source="task",
        status="completed",
        queue="T1.ctrl_out",
        message_id=task_message_id,
        reported_at_ns=100,
        first_observed_at_ns=2000,
    )
    assert select_control_terminal_observation(supervisor, task) == task
    assert select_control_terminal_observation(task, supervisor) == task
    replay = replace(task, first_observed_at_ns=3000)
    assert (
        select_control_terminal_observation(task, replay).first_observed_at_ns == 2000
    )
    newer = replace(task, message_id=40, status="failed")
    assert select_control_terminal_observation(task, newer) == newer


def test_selected_terminal_identity_prioritizes_task_log_over_control_history() -> None:
    """Report correction and store invalidation share one selected evidence identity."""
    observation = ControlTerminalObservation(
        source="manager",
        status="failed",
        queue="T1.ctrl_out",
        message_id=20,
        reported_at_ns=None,
        first_observed_at_ns=1000,
        error=WRAPPER_LOST_ERROR,
    )
    diagnostics = {"control_terminal_observation": observation.to_dict()}
    assert selected_terminal_identity(
        terminal_seen=False,
        terminal_status=None,
        terminal_message_id=None,
        diagnostics=diagnostics,
    ) == ("T1.ctrl_out", 20, "manager", "failed")
    assert selected_terminal_identity(
        terminal_seen=True,
        terminal_status="completed",
        terminal_message_id=10,
        diagnostics=diagnostics,
    ) == (WEFT_GLOBAL_LOG_QUEUE, 10, "task", "completed")


def test_control_observation_bounds_unicode_error_without_losing_identity() -> None:
    """The persisted diagnostic cannot retain an unbounded producer error."""
    observation = ControlTerminalObservation.from_snapshot(
        TaskEvidenceSnapshot(
            tid="1",
            status="failed",
            classification="terminal_ctrl_out",
            source="ctrl_out",
            terminal=True,
            error="α" * WEFT_LOG_TASKS_RAW_BODY_PREVIEW_BYTES,
            observed_at=123,
            metadata={"terminal_source": "task", "terminal_reported_at_ns": 42},
            ack_targets=(QueueAckTarget(queue="T1.ctrl_out", message_id=123),),
        ),
        first_observed_at_ns=1000,
    )
    assert observation.error is not None
    assert (
        len(observation.error.encode("utf-8")) <= WEFT_LOG_TASKS_RAW_BODY_PREVIEW_BYTES
    )
    assert observation.error.startswith("α")
    assert observation.identity() == ("T1.ctrl_out", 123, "task", "failed")
    assert observation.reported_at_ns == 42
    assert observation.first_observed_at_ns == 1000
    assert ControlTerminalObservation.from_dict(observation.to_dict()) == observation
