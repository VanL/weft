"""Tests for external TaskMonitor task-log JSONL output.

Spec references:
- docs/specifications/07-System_Invariants.md [IMPL.11]
"""

from __future__ import annotations

import json
import threading
from logging.handlers import RotatingFileHandler

import pytest

import weft.core.monitor.external_log as external_log_mod
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


def test_external_task_log_sink_probe_creates_missing_parent_path(tmp_path) -> None:
    path = tmp_path / "missing" / "nested" / "weft.log"
    sink = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000006",
    )

    assert sink.probe() is True

    assert path.exists()
    assert path.is_file()
    assert sink.status().healthy is True


def test_external_task_log_sink_probe_tracks_permission_transitions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "permission.jsonl"
    original_handler = external_log_mod._RaisingRotatingFileHandler
    fail_open = True

    def handler_factory(*args, **kwargs):
        if fail_open:
            raise PermissionError("permission denied")
        return original_handler(*args, **kwargs)

    monkeypatch.setattr(
        external_log_mod,
        "_RaisingRotatingFileHandler",
        handler_factory,
    )
    sink = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000007",
    )

    assert sink.probe() is False
    assert sink.status().healthy is False
    assert "permission denied" in str(sink.status().last_error)

    fail_open = False
    assert sink.probe() is True
    assert sink.status().healthy is True
    assert sink.status().last_error is None

    fail_open = True
    assert sink.probe() is False
    assert sink.status().healthy is False
    assert "permission denied" in str(sink.status().last_error)


def test_external_task_log_sink_uses_rotating_file_handler(tmp_path) -> None:
    path = tmp_path / "rotating.jsonl"
    sink = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000005",
    )

    handler = sink._ensure_handler()

    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes > 0
    assert handler.backupCount > 0


def test_external_task_log_sinks_share_one_path_writer_but_not_counters(
    tmp_path,
) -> None:
    """Same-path facades lease one writer and retain local diagnostics [IMPL.11]."""

    path = tmp_path / "shared.jsonl"
    first = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000040",
    )
    second = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000041",
    )

    try:
        first_handler = first._ensure_handler()
        second_handler = second._ensure_handler()

        assert first_handler is second_handler
        assert first._writer is second._writer
        assert external_log_mod._PATH_WRITER_REGISTRY[first._writer.path] is (
            first._writer
        )

        first.emit_json_text(
            '{"source":"first"}',
            emitted_at_ns=1779100000000000042,
        )
        assert first.status().total_emitted == 1
        assert second.status().total_emitted == 0

        first.close()
        second.emit_json_text(
            '{"source":"second"}',
            emitted_at_ns=1779100000000000043,
        )
        assert second.status().total_emitted == 1
        assert external_log_mod._PATH_WRITER_REGISTRY[first._writer.path] is (
            first._writer
        )
    finally:
        first.close()
        second.close()

    assert first._writer.path not in external_log_mod._PATH_WRITER_REGISTRY
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert records == [{"source": "first"}, {"source": "second"}]


def test_external_task_log_sink_reacquires_writer_after_close_then_probe(
    tmp_path,
) -> None:
    """Close/reuse never leaves a handler detached from the path registry."""

    path = tmp_path / "reacquire.jsonl"
    sink = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000044",
    )
    original_writer = sink._writer
    assert sink.probe() is True

    sink.close()
    assert original_writer.path not in external_log_mod._PATH_WRITER_REGISTRY
    assert original_writer.handler is None

    assert sink.probe() is True
    reacquired_writer = sink._writer
    assert reacquired_writer is not original_writer
    assert external_log_mod._PATH_WRITER_REGISTRY[reacquired_writer.path] is (
        reacquired_writer
    )
    sink.emit_json_text(
        '{"reacquired":true}',
        emitted_at_ns=1779100000000000045,
    )
    sink.close()

    assert reacquired_writer.path not in external_log_mod._PATH_WRITER_REGISTRY
    assert reacquired_writer.handler is None
    assert json.loads(path.read_text(encoding="utf-8")) == {"reacquired": True}


@pytest.mark.parametrize("failure", ["flush", "close"])
def test_external_task_log_final_writer_close_failure_allows_fresh_acquire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    failure: str,
) -> None:
    """A failed final release removes poisoned writer state before retry."""

    path = tmp_path / f"failed-{failure}.jsonl"
    sink = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000046",
    )
    sink.emit_json_text(
        '{"writer":"first"}',
        emitted_at_ns=1779100000000000047,
    )
    writer = sink._writer
    handler = writer.handler
    assert handler is not None
    original_flush = handler.flush
    original_close = handler.close

    if failure == "flush":

        def fail_flush() -> None:
            raise OSError("flush close boom")

        monkeypatch.setattr(handler, "flush", fail_flush)
    else:

        def fail_close() -> None:
            original_close()
            raise OSError("handler close boom")

        monkeypatch.setattr(handler, "close", fail_close)

    try:
        with pytest.raises(OSError, match="close boom"):
            sink.close()

        assert writer.path not in external_log_mod._PATH_WRITER_REGISTRY
        assert writer.handler is None

        replacement = ExternalTaskLogSink(
            path=path,
            mode="collated",
            monitor_tid="1779100000000000048",
        )
        try:
            assert replacement._writer is not writer
            replacement.emit_json_text(
                '{"writer":"replacement"}',
                emitted_at_ns=1779100000000000049,
            )
        finally:
            replacement.close()
        assert replacement._writer.path not in (external_log_mod._PATH_WRITER_REGISTRY)
    finally:
        monkeypatch.setattr(handler, "flush", original_flush)
        monkeypatch.setattr(handler, "close", original_close)
        handler.close()

    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert {record["writer"] for record in records} == {"first", "replacement"}


def test_external_task_log_same_path_concurrent_facades_rotate_complete_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Concurrent first leases serialize one real rotating handler [IMPL.11]."""

    monkeypatch.setattr(
        external_log_mod,
        "WEFT_LOG_TASKS_EXTERNAL_ROTATE_MAX_BYTES",
        512,
    )
    monkeypatch.setattr(
        external_log_mod,
        "WEFT_LOG_TASKS_EXTERNAL_ROTATE_BACKUP_COUNT",
        10,
    )
    path = tmp_path / "concurrent.jsonl"
    barrier = threading.Barrier(3)
    sinks: list[ExternalTaskLogSink] = []
    sinks_lock = threading.Lock()

    def emit_rows(worker_index: int) -> None:
        barrier.wait(timeout=5.0)
        sink = ExternalTaskLogSink(
            path=path,
            mode="collated",
            monitor_tid=f"17791000000000001{worker_index:02d}",
        )
        with sinks_lock:
            sinks.append(sink)
        for row_index in range(5):
            sink.emit_json_text(
                json.dumps(
                    {
                        "worker": worker_index,
                        "row": row_index,
                        "padding": "x" * 80,
                    },
                    sort_keys=True,
                ),
                emitted_at_ns=1779100000000000200 + row_index,
            )

    threads = [
        threading.Thread(target=emit_rows, args=(worker_index,))
        for worker_index in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    try:
        assert all(not thread.is_alive() for thread in threads)
        assert len(sinks) == 3
        [writer] = {id(sink._writer): sink._writer for sink in sinks}.values()
        assert writer.handler is not None
        assert (
            sum(
                registered is writer
                for registered in external_log_mod._PATH_WRITER_REGISTRY.values()
            )
            == 1
        )
    finally:
        for sink in sinks:
            sink.close()

    records = []
    for output_path in sorted(tmp_path.glob("concurrent.jsonl*")):
        records.extend(
            json.loads(line)
            for line in output_path.read_text(encoding="utf-8").splitlines()
        )
    assert {(record["worker"], record["row"]) for record in records} == {
        (worker_index, row_index) for worker_index in range(3) for row_index in range(5)
    }


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


def test_external_task_log_sink_surfaces_service_classification(tmp_path) -> None:
    path = tmp_path / "service-summary.jsonl"
    sink = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000013",
    )

    sink.emit_collated(
        task_summary={
            "tid": "1779100000000000014",
            "status": "cancelled",
            "collation_kind": "internal_service",
            "service": {
                "kind": "internal_service",
                "service_key": "_weft.service.task_monitor",
            },
        },
        emitted_at_ns=1779100000000000015,
        close_reason="terminal",
    )

    [line] = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record["record_type"] == "task_log_collated"
    assert record["collation_kind"] == "internal_service"
    assert record["service"]["kind"] == "internal_service"
    assert record["task"]["tid"] == "1779100000000000014"


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
