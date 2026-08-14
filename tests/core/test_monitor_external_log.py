"""Tests for external TaskMonitor task-log JSONL output.

Spec references:
- docs/specifications/07-System_Invariants.md [IMPL.11]
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

import weft.core.monitor.external_log as external_log_mod
from weft.core.monitor.external_log import ExternalTaskLogError, ExternalTaskLogSink

pytestmark = [pytest.mark.shared]


class _CleanupSignal(BaseException):
    """Non-Exception failure used to prove final writer cleanup semantics."""


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
    assert record["schema_version"] == 2
    assert record["record_type"] == "task_log_raw"
    assert record["message_id"] == "1779100000000000002"
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


def test_external_task_log_active_alias_is_not_resolved_again(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Target creation cannot change an active alias into a second writer key."""

    path = tmp_path / "resolution-transition.jsonl"
    first_resolution = path.resolve(strict=False)
    later_resolution = tmp_path / "simulated-alternate-spelling.jsonl"
    resolutions = iter((first_resolution, later_resolution))
    resolve_calls: list[Path] = []

    def resolve_path(alias: Path) -> Path:
        resolve_calls.append(alias)
        return next(resolutions)

    monkeypatch.setattr(external_log_mod, "_resolve_path_writer_path", resolve_path)
    first = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000050",
    )
    second: ExternalTaskLogSink | None = None
    fresh: ExternalTaskLogSink | None = None
    alias = external_log_mod._path_writer_alias(path)
    try:
        first_handler = first._ensure_handler()
        second = ExternalTaskLogSink(
            path=path,
            mode="collated",
            monitor_tid="1779100000000000051",
        )

        assert len(resolve_calls) == 1
        assert second._writer is first._writer
        assert second._ensure_handler() is first_handler
    finally:
        first.close()
        if second is not None:
            second.close()

    assert first_resolution not in external_log_mod._PATH_WRITER_REGISTRY
    assert alias not in external_log_mod._PATH_WRITER_ALIAS_REGISTRY

    try:
        fresh = ExternalTaskLogSink(
            path=path,
            mode="collated",
            monitor_tid="1779100000000000052",
        )
        assert len(resolve_calls) == 2
        assert fresh._writer.path == later_resolution
    finally:
        if fresh is not None:
            fresh.close()

    assert later_resolution not in external_log_mod._PATH_WRITER_REGISTRY
    assert alias not in external_log_mod._PATH_WRITER_ALIAS_REGISTRY


def test_external_task_log_distinct_aliases_share_resolved_writer(tmp_path) -> None:
    """Lexically distinct aliases keep coalescing through the resolved registry."""

    nested = tmp_path / "nested"
    nested.mkdir()
    direct_path = tmp_path / "same.jsonl"
    parent_alias = nested / ".." / "same.jsonl"
    first = ExternalTaskLogSink(
        path=parent_alias,
        mode="collated",
        monitor_tid="1779100000000000053",
    )
    second = ExternalTaskLogSink(
        path=direct_path,
        mode="collated",
        monitor_tid="1779100000000000054",
    )
    writer = first._writer
    aliases = set(writer.aliases)

    try:
        assert writer is second._writer
        assert len(aliases) == 2
    finally:
        first.close()
        second.close()

    assert writer.path not in external_log_mod._PATH_WRITER_REGISTRY
    assert not aliases.intersection(external_log_mod._PATH_WRITER_ALIAS_REGISTRY)


def test_external_task_log_concurrent_first_alias_resolution_rechecks_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Concurrent first resolutions cannot admit two keys for one active alias."""

    path = tmp_path / "concurrent-resolution.jsonl"
    first_resolution = path.resolve(strict=False)
    alternate_resolution = tmp_path / "simulated-concurrent-spelling.jsonl"
    resolution_barrier = threading.Barrier(2)
    resolution_lock = threading.Lock()
    resolution_calls = 0

    def resolve_path(alias: Path) -> Path:
        nonlocal resolution_calls
        with resolution_lock:
            call_index = resolution_calls
            resolution_calls += 1
        resolution_barrier.wait(timeout=5.0)
        return (first_resolution, alternate_resolution)[call_index]

    monkeypatch.setattr(external_log_mod, "_resolve_path_writer_path", resolve_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                ExternalTaskLogSink,
                path=path,
                mode="collated",
                monitor_tid=f"177910000000000005{worker_index + 5}",
            )
            for worker_index in range(2)
        ]
        sinks = [future.result(timeout=5.0) for future in futures]

    writer = sinks[0]._writer
    try:
        assert resolution_calls == 2
        assert sinks[1]._writer is writer
        assert writer.refcount == 2
    finally:
        for sink in sinks:
            sink.close()

    alias = external_log_mod._path_writer_alias(path)
    assert writer.path not in external_log_mod._PATH_WRITER_REGISTRY
    assert alias not in external_log_mod._PATH_WRITER_ALIAS_REGISTRY


def test_external_task_log_final_close_blocks_replacement_writer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A replacement lease cannot overlap the prior writer's final close."""

    path = tmp_path / "serialized-close.jsonl"
    first = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000057",
    )
    first._ensure_handler()
    first_writer = first._writer
    original_close = first_writer.close
    close_started = threading.Event()
    allow_close = threading.Event()
    blocked_acquire = threading.Event()

    class TrackingLock:
        def __init__(self) -> None:
            self._lock = threading.Lock()

        def __enter__(self) -> None:
            if self._lock.locked():
                blocked_acquire.set()
            self._lock.acquire()

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            self._lock.release()

    def blocking_close() -> None:
        close_started.set()
        assert allow_close.wait(timeout=5.0)
        original_close()

    monkeypatch.setattr(external_log_mod, "_PATH_WRITER_REGISTRY_LOCK", TrackingLock())
    monkeypatch.setattr(first_writer, "close", blocking_close)

    replacement: ExternalTaskLogSink | None = None
    with ThreadPoolExecutor(max_workers=2) as executor:
        close_future = executor.submit(first.close)
        assert close_started.wait(timeout=5.0)
        acquire_future = executor.submit(
            ExternalTaskLogSink,
            path=path,
            mode="collated",
            monitor_tid="1779100000000000058",
        )
        assert blocked_acquire.wait(timeout=5.0)
        assert acquire_future.done() is False
        allow_close.set()
        close_future.result(timeout=5.0)
        replacement = acquire_future.result(timeout=5.0)

    try:
        assert replacement._writer is not first_writer
        assert first_writer.handler is None
    finally:
        replacement.close()

    alias = external_log_mod._path_writer_alias(path)
    assert replacement._writer.path not in external_log_mod._PATH_WRITER_REGISTRY
    assert alias not in external_log_mod._PATH_WRITER_ALIAS_REGISTRY


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


def test_external_task_log_writer_close_attempts_both_cleanup_steps_and_raises_first(
    tmp_path,
) -> None:
    """Flush has priority, but its BaseException cannot skip handler close."""

    calls: list[str] = []
    flush_failure = _CleanupSignal("flush failed")
    close_failure = _CleanupSignal("close failed")

    class FailingHandler:
        def flush(self) -> None:
            calls.append("flush")
            raise flush_failure

        def close(self) -> None:
            calls.append("close")
            raise close_failure

    writer = external_log_mod._PathWriter(tmp_path / "cleanup.jsonl")
    handler = FailingHandler()
    writer.handler = handler
    writer.logger.addHandler(handler)

    with pytest.raises(_CleanupSignal) as exc_info:
        writer._close_handler_locked()

    assert exc_info.value is flush_failure
    assert calls == ["flush", "close"]
    assert writer.handler is None
    assert handler not in writer.logger.handlers


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
    errors: list[Exception] = []
    sinks_lock = threading.Lock()

    def emit_rows(worker_index: int) -> None:
        try:
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
        except (OSError, RuntimeError, ValueError) as exc:
            with sinks_lock:
                errors.append(exc)

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
        assert errors == [], [repr(error) for error in errors]
        assert len(sinks) == 3
        writers = {id(sink._writer): sink._writer for sink in sinks}
        assert len(writers) == 1, [str(writer.path) for writer in writers.values()]
        writer = next(iter(writers.values()))
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
    assert len(records) == 15
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
        task_summary={
            "tid": "1779100000000000011",
            "status": "completed",
            "first_message_id": 1779100000000000016,
            "last_message_id": 1779100000000000017,
            "terminal_message_id": 1779100000000000017,
            "completed_at_ns": 1779100000000000018,
        },
        emitted_at_ns=1779100000000000012,
        close_reason="terminal",
    )

    [line] = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record["schema_version"] == 2
    assert record["record_type"] == "task_summary"
    assert record["close_reason"] == "terminal"
    assert record["task"]["tid"] == "1779100000000000011"
    assert record["task"]["first_message_id"] == "1779100000000000016"
    assert record["task"]["last_message_id"] == "1779100000000000017"
    assert record["task"]["terminal_message_id"] == "1779100000000000017"
    assert record["task"]["completed_at_ns"] == 1779100000000000018


def test_external_task_log_sink_projects_lifetime_ids_after_report_identity(
    tmp_path,
) -> None:
    path = tmp_path / "task-lifetime.jsonl"
    sink = ExternalTaskLogSink(
        path=path,
        mode="collated",
        monitor_tid="1779100000000000023",
    )
    report = {
        "schema_version": 2,
        "record_type": "task_lifetime_report",
        "report_id": "task-lifetime:stable",
        "emitted_at_ns": 1779100000000000024,
        "subject": {"message_id": 1779100000000000025},
        "monitor": {
            "first_message_id": 1779100000000000026,
            "last_message_id": 1779100000000000027,
            "terminal_message_id": None,
        },
        "observations": {
            "message_ids": [1779100000000000028],
            "observed_at_ns": 1779100000000000029,
        },
    }

    sink.emit_lifetime_report(report, emitted_at_ns=1779100000000000024)

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["schema_version"] == 2
    assert report["subject"]["message_id"] == 1779100000000000025
    assert record["report_id"] == "task-lifetime:stable"
    assert record["subject"]["message_id"] == "1779100000000000025"
    assert record["monitor"]["first_message_id"] == "1779100000000000026"
    assert record["monitor"]["last_message_id"] == "1779100000000000027"
    assert record["monitor"]["terminal_message_id"] is None
    assert record["observations"]["message_ids"] == ["1779100000000000028"]
    assert record["observations"]["observed_at_ns"] == 1779100000000000029


@pytest.mark.parametrize("schema_version", [None, 1, 2.0, 3, "2", True])
def test_external_task_log_sink_rejects_noncurrent_lifetime_schema(
    tmp_path,
    schema_version,
) -> None:
    sink = ExternalTaskLogSink(
        path=tmp_path / "old-lifetime.jsonl",
        mode="collated",
        monitor_tid="1779100000000000033",
    )

    with pytest.raises(ExternalTaskLogError, match="current external schema"):
        sink.emit_lifetime_report(
            {
                "schema_version": schema_version,
                "record_type": "task_lifetime_report",
                "report_id": "task-lifetime:old-schema",
            },
            emitted_at_ns=1779100000000000034,
        )

    assert not (tmp_path / "old-lifetime.jsonl").exists()


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
    assert record["schema_version"] == 2
    assert record["record_type"] == "service_summary"
    assert "collation_kind" not in record
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
