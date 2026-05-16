"""Tests for pure Monitor task-log collation reducers."""

from __future__ import annotations

import json
from typing import Any

import pytest

from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.core.monitor.collation import update_from_task_log_row
from weft.core.queue_window import DecodedQueueWindowRow, QueueWindowRow

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


def test_monitor_collation_captures_successful_task_summary_without_reserved_probe() -> None:
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
