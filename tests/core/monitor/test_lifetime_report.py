"""Tests for TaskMonitor lifetime report builders."""

from __future__ import annotations

import json

import pytest

from weft._constants import (
    TASK_MONITOR_CLEANUP_POLICY_NAMES,
    TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE,
    TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
    TASK_MONITOR_POLICY_TASK_LOCAL_DEAD_TID,
    TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME,
    TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
)
from weft.core.monitor.lifetime_report import (
    build_candidate_lifetime_report,
    build_collation_lifetime_report,
    build_inferred_tid_lifetime_report,
    build_raw_row_lifetime_report,
)
from weft.core.monitor.store import MonitorTaskCollationRecord
from weft.core.pruning.models import CleanupCandidate
from weft.core.queue_window import QueueWindowRow

pytestmark = [pytest.mark.shared]

EXPECTED_REPORT_KEYS = {
    "schema_version",
    "record_type",
    "report_id",
    "emitted_at_ns",
    "monitor_tid",
    "source_policy",
    "report_kind",
    "completeness",
    "subject",
    "lifetime",
    "taskspec",
    "monitor",
    "observations",
}

EXPECTED_LIFETIME_KEYS = {
    "tid",
    "name",
    "runner",
    "parent_tid",
    "role",
    "status",
    "terminal_seen",
    "terminal_status",
    "return_code",
    "first_seen_at_ns",
    "last_seen_at_ns",
    "started_at_ns",
    "completed_at_ns",
    "close_reason",
}


def _assert_common_report_shape(
    report: dict[str, object],
    *,
    source_policy: str,
) -> None:
    assert set(report) == EXPECTED_REPORT_KEYS
    assert report["record_type"] == "task_lifetime_report"
    assert report["source_policy"] == source_policy
    assert source_policy in TASK_MONITOR_CLEANUP_POLICY_NAMES
    assert isinstance(report["subject"], dict)
    assert isinstance(report["lifetime"], dict)
    assert set(report["lifetime"]) == EXPECTED_LIFETIME_KEYS
    assert "collation" not in report
    assert "effect" not in report


def _record() -> MonitorTaskCollationRecord:
    return MonitorTaskCollationRecord(
        context_key="test",
        tid="1779000000000000001",
        name="sample",
        runner="host",
        parent_tid=None,
        role=None,
        status="completed",
        terminal_seen=True,
        terminal_event="work_completed",
        terminal_status="completed",
        terminal_message_id=1779000000000000003,
        return_code=0,
        first_message_id=1779000000000000001,
        last_message_id=1779000000000000003,
        first_seen_at_ns=1779000000000000001,
        last_seen_at_ns=1779000000000000003,
        started_at_ns=1779000000000000001,
        completed_at_ns=1779000000000000003,
    )


def test_collation_lifetime_report_promotes_taskspec_and_monitor_context() -> None:
    report = build_collation_lifetime_report(
        _record(),
        monitor_tid="1779000000000000999",
        emitted_at_ns=1779000000000000100,
        close_reason="terminal",
    )

    _assert_common_report_shape(
        report,
        source_policy=TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE,
    )
    assert report["completeness"] == "collated"
    assert report["subject"] == {"tid": "1779000000000000001"}
    assert report["taskspec"]["tid"] == "1779000000000000001"
    assert report["taskspec"]["name"] == "sample"
    assert report["taskspec"]["state"]["status"] == "completed"
    assert report["lifetime"]["terminal_seen"] is True
    assert report["lifetime"]["completed_at_ns"] == 1779000000000000003
    assert report["lifetime"]["return_code"] == 0
    assert report["monitor"]["first_message_id"] == 1779000000000000001
    assert report["monitor"]["last_message_id"] == 1779000000000000003
    assert report["monitor"]["terminal_message_id"] == 1779000000000000003
    assert report["monitor"]["collation_kind"] == "user_task"


def test_lifetime_report_id_is_stable_across_emit_times() -> None:
    first = build_inferred_tid_lifetime_report(
        tid="1779000000000000002",
        monitor_tid="1779000000000000999",
        emitted_at_ns=1779000000000000100,
        source_policy=TASK_MONITOR_POLICY_TASK_LOCAL_DEAD_TID,
        report_kind="dead_tid_runtime_cleanup",
        close_reason="dead_tid_runtime_cleanup",
        queue_names=("T1779000000000000002.inbox",),
    )
    second = build_inferred_tid_lifetime_report(
        tid="1779000000000000002",
        monitor_tid="1779000000000000999",
        emitted_at_ns=1779000000000000200,
        source_policy=TASK_MONITOR_POLICY_TASK_LOCAL_DEAD_TID,
        report_kind="dead_tid_runtime_cleanup",
        close_reason="dead_tid_runtime_cleanup",
        queue_names=("T1779000000000000002.inbox",),
    )

    assert first["report_id"] == second["report_id"]
    assert first["emitted_at_ns"] != second["emitted_at_ns"]
    _assert_common_report_shape(
        first,
        source_policy=TASK_MONITOR_POLICY_TASK_LOCAL_DEAD_TID,
    )


@pytest.mark.parametrize(
    "source_policy",
    [
        TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE,
        TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME,
        TASK_MONITOR_POLICY_TASK_LOCAL_DEAD_TID,
        TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
    ],
)
def test_candidate_lifetime_report_uses_common_policy_shape(
    source_policy: str,
) -> None:
    candidate = CleanupCandidate(
        queue="weft.test.queue",
        message_id=1779000000000000201,
        policy=source_policy,
        candidate_class="test_candidate",
        reason="test_reason",
        tid="1779000000000000200",
    )

    report = build_candidate_lifetime_report(
        candidate,
        monitor_tid="1779000000000000999",
        emitted_at_ns=1779000000000000202,
    )

    _assert_common_report_shape(report, source_policy=source_policy)
    assert report["subject"]["tid"] == "1779000000000000200"
    assert report["taskspec"] is None
    assert report["lifetime"]["tid"] == "1779000000000000200"
    assert report["monitor"]["queue"] == "weft.test.queue"
    assert report["monitor"]["message_id"] == 1779000000000000201
    assert report["monitor"]["candidate_class"] == "test_candidate"


def test_candidate_lifetime_report_promotes_available_taskspec() -> None:
    candidate = CleanupCandidate(
        queue="weft.log.tasks",
        message_id=1779000000000000301,
        policy=TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        candidate_class="old_task_log",
        reason="older_than_task_log_cleanup_min_age",
        tid="1779000000000000300",
        metadata={
            "event": "work_completed",
            "status": "completed",
            "taskspec": {
                "tid": "1779000000000000300",
                "name": "sample",
                "spec": {"runner": {"name": "host"}},
                "state": {
                    "status": "completed",
                    "started_at": 1779000000000000300,
                    "completed_at": 1779000000000000301,
                    "return_code": 0,
                },
            },
        },
    )

    report = build_candidate_lifetime_report(
        candidate,
        monitor_tid="1779000000000000999",
        emitted_at_ns=1779000000000000302,
    )

    assert report["taskspec"]["tid"] == "1779000000000000300"
    assert report["taskspec"]["name"] == "sample"
    assert report["lifetime"]["status"] == "completed"
    assert report["lifetime"]["terminal_status"] == "completed"
    assert report["lifetime"]["return_code"] == 0


def test_raw_row_lifetime_report_promotes_available_taskspec() -> None:
    row = QueueWindowRow(
        queue="weft.log.tasks",
        message_id=1779000000000000401,
        body=json.dumps(
            {
                "tid": "1779000000000000400",
                "event": "work_completed",
                "status": "completed",
                "taskspec": {
                    "tid": "1779000000000000400",
                    "name": "sample",
                    "spec": {"runner": {"name": "host"}},
                    "state": {
                        "status": "completed",
                        "started_at": 1779000000000000400,
                        "completed_at": 1779000000000000401,
                    },
                },
            }
        ),
    )

    report = build_raw_row_lifetime_report(
        row,
        monitor_tid="1779000000000000999",
        emitted_at_ns=1779000000000000402,
        source_policy=TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        report_kind="old_task_log",
        close_reason="older_than_task_log_cleanup_min_age",
    )

    _assert_common_report_shape(
        report,
        source_policy=TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
    )
    assert report["subject"]["tid"] == "1779000000000000400"
    assert report["taskspec"]["tid"] == "1779000000000000400"
    assert report["lifetime"]["status"] == "completed"
    assert report["lifetime"]["terminal_status"] == "completed"
    assert report["monitor"]["queue"] == "weft.log.tasks"
    assert report["monitor"]["message_id"] == 1779000000000000401
