"""Tests for external TaskMonitor task-log JSONL output."""

from __future__ import annotations

import json

import pytest

from weft.core.monitor.external_log import ExternalTaskLogError, ExternalTaskLogSink

pytestmark = [pytest.mark.shared]


def test_external_task_log_sink_writes_raw_jsonl(tmp_path) -> None:
    path = tmp_path / "task-log.jsonl"
    sink = ExternalTaskLogSink(
        path=path,
        mode="raw",
        monitor_tid="1779100000000000001",
    )

    assert sink.validate() is True
    sink.emit_raw(
        queue="weft.log.tasks",
        message_id=1779100000000000002,
        emitted_at_ns=1779100000000000003,
        payload={"tid": "1779100000000000004", "event": "work_completed"},
        raw_body="{}",
        malformed_reason=None,
    )

    [line] = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record["record_type"] == "task_log_raw"
    assert record["message_id"] == 1779100000000000002
    assert record["payload"]["event"] == "work_completed"
    assert sink.status().healthy is True
    assert sink.status().last_emitted == 1


def test_external_task_log_sink_writes_collated_jsonl(tmp_path) -> None:
    path = tmp_path / "task-summary.jsonl"
    sink = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000010",
    )

    sink.emit_collated(
        task_summary={"tid": "1779100000000000011", "status": "completed"},
        emitted_at_ns=1779100000000000012,
        close_reason="terminal",
    )

    [line] = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record["record_type"] == "task_log_collated"
    assert record["close_reason"] == "terminal"
    assert record["task"]["tid"] == "1779100000000000011"


def test_external_task_log_sink_represents_malformed_raw_payload(tmp_path) -> None:
    path = tmp_path / "malformed.jsonl"
    sink = ExternalTaskLogSink(
        path=path,
        mode="raw",
        monitor_tid="1779100000000000020",
    )

    sink.emit_raw(
        queue="weft.log.tasks",
        message_id=1779100000000000021,
        emitted_at_ns=1779100000000000022,
        payload=None,
        raw_body="{not-json",
        malformed_reason="invalid_json",
    )

    [line] = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record["malformed_reason"] == "invalid_json"
    assert record["raw_body_preview"] == "{not-json"


def test_external_task_log_sink_fails_closed_for_directory_path(tmp_path) -> None:
    sink = ExternalTaskLogSink(
        path=tmp_path,
        mode="raw",
        monitor_tid="1779100000000000030",
    )

    assert sink.validate() is False
    with pytest.raises(ExternalTaskLogError):
        sink.emit_collated(
            task_summary={"tid": "1779100000000000031"},
            emitted_at_ns=1779100000000000032,
            close_reason="terminal",
        )
    assert sink.status().healthy is False
    assert "directory" in str(sink.status().last_error)
