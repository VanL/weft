"""Tests for task-local and task-log retention pruning."""

from __future__ import annotations

import json
import multiprocessing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    CONTROL_PING,
    RETENTION_PRUNE_CLASS_NONTERMINAL_TASK_LOG_SUPERSEDED,
    RETENTION_PRUNE_CLASS_OBSOLETE_INBOX_WORK,
    RETENTION_PRUNE_CLASS_OBSOLETE_RESERVED_WORK,
    RETENTION_PRUNE_CLASS_RESULT_WITHOUT_TERMINAL_OUTBOX_REPORTED,
    RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_ARCHIVED,
    RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_WITHOUT_LOG_REPORTED,
    RETENTION_PRUNE_CLASS_TERMINAL_RESULT_OUTBOX_ARCHIVED,
    RETENTION_PRUNE_CLASS_TERMINAL_TASK_LOG_SUPERSEDED,
    TERMINAL_ENVELOPE_TYPE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
    load_config,
)
from weft.client import WeftClient
from weft.commands.prune import cmd_system_prune, run_retention_prune
from weft.commands.types import SystemPruneResult
from weft.context import WeftContext, build_context
from weft.core.pruning import retention as retention_pruning
from weft.core.pruning.retention import (
    RetentionPruneCandidate,
    RetentionPruneConfig,
    _append_records,
    _candidate_record,
)
from weft.helpers import encode_tid, iter_queue_entries

pytestmark = [pytest.mark.shared]


def test_retention_prune_preserves_exact_run_id_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    monkeypatch.setattr(
        retention_pruning.time,
        "strftime",
        lambda *_args: "2030-01-02T03:04:05",
    )
    monkeypatch.setattr(retention_pruning.time, "time_ns", lambda: 9_876_543_210)
    # Keep the broker's process identity real while pinning the report field.
    os_proxy = SimpleNamespace(**vars(retention_pruning.os))
    os_proxy.getpid = lambda: 4321
    monkeypatch.setattr(retention_pruning, "os", os_proxy)

    result = retention_pruning.run_retention_prune_for_context(
        ctx,
        RetentionPruneConfig(context_path=ctx.root, family="task-log"),
    )

    assert result.run_id == "2030-01-02T03:04:05.876543210Z:pid-4321"


def test_retention_prune_candidate_json_formats_message_id_only() -> None:
    candidate = RetentionPruneCandidate(
        queue=WEFT_GLOBAL_LOG_QUEUE,
        family="task-log",
        message_id=1779400000000000010,
        tid="1779400000000000011",
        candidate_class=RETENTION_PRUNE_CLASS_TERMINAL_TASK_LOG_SUPERSEDED,
        reason="superseded",
        age_seconds=2.0,
        payload_sha256="0" * 64,
        payload_size_bytes=2,
        payload={"observed_at_ns": 1779400000000000012},
    )

    record = _candidate_record(
        "retention-prune:test",
        RetentionPruneConfig(),
        candidate,
        False,
    )

    assert record["message_id"] == "1779400000000000010"
    assert record["payload"]["observed_at_ns"] == 1779400000000000012
    assert isinstance(candidate.message_id, int)


def _context(tmp_path: Path) -> WeftContext:
    root = prepare_project_root(tmp_path)
    return build_context(spec_context=root)


def _write_raw(
    ctx: WeftContext,
    queue_name: str,
    body: str,
    *,
    persistent: bool = False,
) -> int:
    queue = ctx.queue(queue_name, persistent=persistent)
    try:
        queue.write(body)
        latest: int | None = None
        for row, message_id in iter_queue_entries(queue):
            if row == body:
                latest = int(message_id)
        assert latest is not None
        return latest
    finally:
        queue.close()


def _write_json(
    ctx: WeftContext,
    queue_name: str,
    payload: dict[str, Any],
    *,
    persistent: bool = False,
) -> int:
    return _write_raw(ctx, queue_name, json.dumps(payload), persistent=persistent)


def _read_ids(
    ctx: WeftContext,
    queue_name: str,
    *,
    persistent: bool = False,
) -> set[int]:
    queue = ctx.queue(queue_name, persistent=persistent)
    try:
        return {int(message_id) for _body, message_id in iter_queue_entries(queue)}
    finally:
        queue.close()


def _read_json_records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_task_log_dry_run_reports_superseded_rows_without_deleting(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    tid = "1770000000000001001"
    created_id = _write_json(
        ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid, "status": "created"}
    )
    terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "completed", "type": "terminal", "source": "task"},
    )
    latest_terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {
            "tid": tid,
            "status": "completed",
            "type": "terminal",
            "source": "task",
            "event": "task_completed",
        },
    )

    result = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            min_age_seconds=0,
        )
    )

    assert result.errors == ()
    assert result.failed == 0
    assert {
        (candidate.message_id, candidate.candidate_class)
        for candidate in result.candidates
    } == {
        (created_id, RETENTION_PRUNE_CLASS_NONTERMINAL_TASK_LOG_SUPERSEDED),
        (terminal_id, RETENTION_PRUNE_CLASS_TERMINAL_TASK_LOG_SUPERSEDED),
    }
    assert _read_ids(ctx, WEFT_GLOBAL_LOG_QUEUE) >= {
        created_id,
        terminal_id,
        latest_terminal_id,
    }


def test_retention_selector_includes_candidate_at_exact_minimum_age(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    tid = "1770000000000001090"
    created_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "created"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "completed", "type": "terminal", "source": "task"},
    )
    monkeypatch.setattr(
        retention_pruning.time,
        "time_ns",
        lambda: created_id + 5_000_000_000,
    )

    result = retention_pruning.run_retention_prune_for_context(
        ctx,
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            min_age_seconds=5.0,
        ),
    )

    assert [
        (candidate.message_id, candidate.age_seconds) for candidate in result.candidates
    ] == [(created_id, 5.0)]


def test_task_log_apply_requires_archive_and_deletes_exact_rows(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    archive = tmp_path / "archive.jsonl"
    tid = "1770000000000001002"
    old_id = _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid, "status": "created"})
    keep_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "completed", "type": "terminal", "source": "task"},
    )

    missing_archive = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            apply=True,
            min_age_seconds=0,
        )
    )
    assert missing_archive.errors
    assert missing_archive.failed == 0
    assert "--archive is required" in missing_archive.errors[0]

    result = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            apply=True,
            archive_path=archive,
            min_age_seconds=0,
        )
    )

    assert result.errors == ()
    assert result.failed == 0
    assert result.deleted == 1
    remaining = _read_ids(ctx, WEFT_GLOBAL_LOG_QUEUE)
    assert old_id not in remaining
    assert keep_id in remaining
    archive_records = _read_json_records(archive)
    assert archive_records[0]["message_id"] == str(old_id)
    assert archive_records[-1]["record_type"] == "retention_prune_completed"


def test_retention_limit_applies_to_dry_run_and_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    tid = "1770000000000001091"
    oldest_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "created"},
    )
    middle_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "running"},
    )
    latest_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "completed", "type": "terminal", "source": "task"},
    )
    config = RetentionPruneConfig(
        context_path=ctx.root,
        family="task-log",
        min_age_seconds=0,
        limit=1,
    )

    dry_run = retention_pruning.run_retention_prune_for_context(ctx, config)

    assert [candidate.message_id for candidate in dry_run.candidates] == [oldest_id]
    assert _read_ids(ctx, WEFT_GLOBAL_LOG_QUEUE) >= {
        oldest_id,
        middle_id,
        latest_id,
    }

    real_build = retention_pruning._build_candidates
    calls = 0

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(retention_pruning, "_build_candidates", counting_build)

    applied = retention_pruning.run_retention_prune_for_context(
        ctx,
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            min_age_seconds=0,
            limit=1,
            apply=True,
            archive_path=tmp_path / "archive.jsonl",
        ),
    )

    assert calls == 1
    assert [candidate.message_id for candidate in applied.candidates] == [oldest_id]
    assert [candidate.message_id for candidate in applied.applied_candidates] == [
        oldest_id
    ]
    remaining_ids = _read_ids(ctx, WEFT_GLOBAL_LOG_QUEUE)
    assert oldest_id not in remaining_ids
    assert remaining_ids >= {middle_id, latest_id}


def test_retention_apply_report_error_is_classified_after_delete(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    archive = tmp_path / "archive.jsonl"
    tid = "1770000000000001008"
    old_id = _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid, "status": "created"})
    keep_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "completed", "type": "terminal", "source": "task"},
    )
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")

    result = cmd_system_prune(
        family="task-log",
        context=ctx.root,
        apply=True,
        min_age=0,
        archive=archive,
        report=blocked_parent / "report.jsonl",
    )

    assert result.deleted == 1
    assert result.details["retention"]["errors"][0].startswith(
        "failed to write report:"
    )
    remaining = _read_ids(ctx, WEFT_GLOBAL_LOG_QUEUE)
    assert old_id not in remaining
    assert keep_id in remaining


def test_retention_validation_error_does_not_create_or_truncate_report(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    report_path = tmp_path / "retention-report.jsonl"
    report_path.write_text("sentinel\n", encoding="utf-8")

    result = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            keep_recent_per_task=0,
        ),
        report_path=report_path,
    )

    assert result.errors == ("--keep-recent-per-task must be >= 1",)
    assert report_path.read_text(encoding="utf-8") == "sentinel\n"

    missing_report = tmp_path / "missing-retention-report.jsonl"
    run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            keep_recent_per_task=0,
        ),
        report_path=missing_report,
    )
    assert not missing_report.exists()


def test_retention_initial_scan_error_writes_optional_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    report_path = tmp_path / "retention-report.jsonl"
    monkeypatch.setattr(
        retention_pruning,
        "_build_candidates",
        lambda *_args, **_kwargs: ([], [], ["scan failed"]),
    )

    result = run_retention_prune(
        RetentionPruneConfig(context_path=ctx.root),
        report_path=report_path,
    )

    assert result.errors == ("scan failed",)
    records = _read_json_records(report_path)
    assert records == [
        {
            "archived": 0,
            "candidate_class_counts": {},
            "candidates": 0,
            "deleted": 0,
            "dry_run": True,
            "errors": ["scan failed"],
            "failed": 0,
            "force": False,
            "record_type": "retention_prune_completed",
            "run_id": result.run_id,
            "schema_version": 1,
            "warnings": [],
        }
    ]


def test_retention_archive_error_still_writes_optional_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    report_path = tmp_path / "retention-report.jsonl"
    monkeypatch.setattr(
        retention_pruning,
        "_write_archive",
        lambda *_args, **_kwargs: (0, "archive failed"),
    )

    result = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            apply=True,
            archive_path=tmp_path / "archive.jsonl",
            min_age_seconds=0,
        ),
        report_path=report_path,
    )

    assert result.errors == ("failed to write archive: archive failed",)
    assert _read_json_records(report_path)[-1]["errors"] == [
        "failed to write archive: archive failed"
    ]


def test_task_log_apply_appends_to_same_day_archive_across_runs(
    tmp_path: Path,
) -> None:
    """A second apply run against the same archive path must not truncate
    the first run's pre-delete recovery records.

    Spec: docs/specifications/07-System_Invariants.md [OBS.13.5] (archived
    payload is the only recovery record for a destructive prune).
    """
    ctx = _context(tmp_path)
    archive = tmp_path / "archive.jsonl"

    tid_a = "1770000000000001003"
    old_id_a = _write_json(
        ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid_a, "status": "created"}
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid_a, "status": "completed", "type": "terminal", "source": "task"},
    )

    result_a = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            apply=True,
            archive_path=archive,
            min_age_seconds=0,
        )
    )
    assert result_a.errors == ()
    assert result_a.failed == 0
    assert result_a.deleted == 1
    run_id_a = result_a.run_id

    tid_b = "1770000000000001004"
    old_id_b = _write_json(
        ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid_b, "status": "created"}
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid_b, "status": "completed", "type": "terminal", "source": "task"},
    )

    result_b = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            apply=True,
            archive_path=archive,
            min_age_seconds=0,
        )
    )
    assert result_b.errors == ()
    assert result_b.failed == 0
    assert result_b.deleted == 1
    run_id_b = result_b.run_id
    assert run_id_a != run_id_b

    archive_records = _read_json_records(archive)
    candidate_message_ids = {
        record["message_id"]
        for record in archive_records
        if record["record_type"] == "retention_prune_candidate"
    }
    candidate_run_ids = {
        record["run_id"]
        for record in archive_records
        if record["record_type"] == "retention_prune_candidate"
    }
    # Both runs' pre-delete candidate rows must survive in the archive.
    assert str(old_id_a) in candidate_message_ids
    assert str(old_id_b) in candidate_message_ids
    assert run_id_a in candidate_run_ids
    assert run_id_b in candidate_run_ids

    completion_run_ids = {
        record["run_id"]
        for record in archive_records
        if record["record_type"] == "retention_prune_completed"
    }
    assert run_id_a in completion_run_ids
    assert run_id_b in completion_run_ids


_CONCURRENT_APPEND_RECORDS = 50
"""Records per concurrent append worker.

With the ~2KB per-record payload below, each worker writes well past the
default text-IO buffer size, so an unserialized append would issue multiple
interleavable write syscalls per batch.
"""

_CONCURRENT_APPEND_MESSAGE_ID_BASE = 1779400000000000100
"""First exact broker-domain ID used by concurrent archive fixtures."""

_CONCURRENT_APPEND_JOIN_TIMEOUT = 60.0
"""Deadline for joining append workers; generous to avoid CI flakiness."""


def _concurrent_append_worker(
    archive: Path,
    run_id: str,
    start_event: Any,
) -> None:
    """Append one run's full record batch to *archive* (spawn target)."""

    config = RetentionPruneConfig(
        family="task-log",
        apply=True,
        archive_path=archive,
        min_age_seconds=0,
    )
    payload = {
        "tid": run_id,
        "status": "completed",
        "type": "terminal",
        "source": "task",
        "filler": "x" * 2048,
    }
    payload_text = json.dumps(payload)
    candidates = [
        RetentionPruneCandidate(
            queue=WEFT_GLOBAL_LOG_QUEUE,
            family="task-log",
            message_id=_CONCURRENT_APPEND_MESSAGE_ID_BASE + index,
            tid=run_id,
            candidate_class=RETENTION_PRUNE_CLASS_TERMINAL_TASK_LOG_SUPERSEDED,
            reason="concurrent append test",
            age_seconds=1.0,
            payload_sha256="0" * 64,
            payload_size_bytes=len(payload_text),
            payload_excerpt=payload_text[:200],
            payload=payload,
        )
        for index in range(_CONCURRENT_APPEND_RECORDS)
    ]
    start_event.wait()
    _append_records(
        archive,
        run_id,
        config,
        candidates,
        applied_candidates=(),
        errors=(),
        warnings=(),
        truncate_payload=False,
    )


def test_concurrent_archive_appends_produce_complete_jsonl_lines(
    tmp_path: Path,
) -> None:
    """Two real processes appending to the same archive concurrently must
    each land a complete, non-interleaved batch of parseable JSONL lines.

    The archive is the only pre-delete recovery record; a torn or
    interleaved line would defeat it. The exclusive append lock is a
    cross-process file lock, so this test uses real processes (spawn
    context, per the fork invariant) with a shared start event.
    """
    archive = tmp_path / "archive.jsonl"
    n = _CONCURRENT_APPEND_RECORDS
    ctx = multiprocessing.get_context("spawn")
    start_event = ctx.Event()
    workers = [
        ctx.Process(
            target=_concurrent_append_worker,
            args=(archive, run_id, start_event),
        )
        for run_id in ("run-alpha", "run-beta")
    ]
    for worker in workers:
        worker.start()
    start_event.set()
    for worker in workers:
        worker.join(timeout=_CONCURRENT_APPEND_JOIN_TIMEOUT)
        assert worker.exitcode == 0

    lines = [
        line
        for line in archive.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # 2N candidate records + 2 summary records, every line parseable.
    assert len(lines) == 2 * n + 2
    records = [json.loads(line) for line in lines]

    for run_id in ("run-alpha", "run-beta"):
        run_records = [record for record in records if record["run_id"] == run_id]
        candidate_ids = [
            record["message_id"]
            for record in run_records
            if record["record_type"] == "retention_prune_candidate"
        ]
        assert candidate_ids == [
            str(_CONCURRENT_APPEND_MESSAGE_ID_BASE + index) for index in range(n)
        ]
        summaries = [
            record
            for record in run_records
            if record["record_type"] == "retention_prune_completed"
        ]
        assert len(summaries) == 1


def test_ctrl_out_terminal_with_log_is_deleted_and_unrelated_row_is_preserved(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    archive = tmp_path / "archive.jsonl"
    tid = "1770000000000001003"
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "completed", "type": "terminal", "source": "task"},
    )
    terminal_id = _write_json(
        ctx,
        f"T{tid}.ctrl_out",
        {
            "type": TERMINAL_ENVELOPE_TYPE,
            "tid": tid,
            "source": "task",
            "status": "completed",
        },
    )
    unrelated_id = _write_raw(ctx, f"T{tid}.ctrl_out", "unrelated-control-row")

    result = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            apply=True,
            archive_path=archive,
            min_age_seconds=0,
        )
    )

    assert result.deleted == 1
    assert result.candidates[0].candidate_class == (
        RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_ARCHIVED
    )
    remaining = _read_ids(ctx, f"T{tid}.ctrl_out")
    assert terminal_id not in remaining
    assert unrelated_id in remaining


def test_ctrl_out_without_log_is_report_only_unless_force(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    tid = "1770000000000001004"
    terminal_id = _write_json(
        ctx,
        f"T{tid}.ctrl_out",
        {
            "type": TERMINAL_ENVELOPE_TYPE,
            "tid": tid,
            "source": "task",
            "status": "completed",
        },
    )

    ordinary = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            task_filters=(tid,),
            apply=True,
            archive_path=tmp_path / "ordinary.jsonl",
            min_age_seconds=0,
        )
    )
    assert ordinary.deleted == 0
    assert ordinary.candidates[0].candidate_class == (
        RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_WITHOUT_LOG_REPORTED
    )
    assert terminal_id in _read_ids(ctx, f"T{tid}.ctrl_out")

    forced = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            task_filters=(tid,),
            apply=True,
            force=True,
            min_age_seconds=0,
        )
    )

    assert forced.errors == ()
    assert forced.failed == 0
    assert forced.deleted == 1
    assert terminal_id not in _read_ids(ctx, f"T{tid}.ctrl_out")
    assert forced.warnings == ()
    assert forced.archived >= 1
    assert list((ctx.logs_dir / "retention-prune").glob("*.jsonl"))


def test_outbox_final_result_with_terminal_log_is_deleted(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    archive = tmp_path / "archive.jsonl"
    tid = "1770000000000001005"
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "completed", "type": "terminal", "source": "task"},
    )
    result_id = _write_json(
        ctx,
        f"T{tid}.outbox",
        {"stdout": "ok", "return_code": 0},
        persistent=True,
    )

    result = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            apply=True,
            archive_path=archive,
            min_age_seconds=0,
        )
    )

    assert result.deleted == 1
    assert result.candidates[0].candidate_class == (
        RETENTION_PRUNE_CLASS_TERMINAL_RESULT_OUTBOX_ARCHIVED
    )
    assert result_id not in _read_ids(ctx, f"T{tid}.outbox", persistent=True)


@pytest.mark.parametrize(
    ("suffix", "payload", "persistent", "terminal_log", "candidate_class"),
    [
        (
            "inbox",
            {"work": True},
            False,
            True,
            RETENTION_PRUNE_CLASS_OBSOLETE_INBOX_WORK,
        ),
        (
            "reserved",
            {"work": True},
            False,
            True,
            RETENTION_PRUNE_CLASS_OBSOLETE_RESERVED_WORK,
        ),
        (
            "outbox",
            {"stdout": "unread", "return_code": 0},
            True,
            False,
            RETENTION_PRUNE_CLASS_RESULT_WITHOUT_TERMINAL_OUTBOX_REPORTED,
        ),
    ],
)
def test_task_local_data_is_report_only_but_force_archives_and_deletes(
    tmp_path: Path,
    suffix: str,
    payload: dict[str, Any],
    persistent: bool,
    terminal_log: bool,
    candidate_class: str,
) -> None:
    ctx = _context(tmp_path)
    tid = "1770000000000001006"
    if terminal_log:
        _write_json(
            ctx,
            WEFT_GLOBAL_LOG_QUEUE,
            {"tid": tid, "status": "completed", "type": "terminal", "source": "task"},
        )
    queue_name = f"T{tid}.{suffix}"
    work_id = _write_json(ctx, queue_name, payload, persistent=persistent)

    ordinary = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            task_filters=(tid,),
            apply=True,
            archive_path=tmp_path / "ordinary.jsonl",
            min_age_seconds=0,
        )
    )

    assert ordinary.deleted == 0
    assert ordinary.candidates[0].candidate_class == candidate_class
    assert work_id in _read_ids(ctx, queue_name, persistent=persistent)

    forced_archive = tmp_path / "forced.jsonl"
    forced = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            task_filters=(tid,),
            apply=True,
            force=True,
            archive_path=forced_archive,
            min_age_seconds=0,
        )
    )

    assert forced.deleted == 1
    assert work_id not in _read_ids(ctx, queue_name, persistent=persistent)
    assert forced.warnings == ()
    assert forced.archived >= 1
    archived_candidates = [
        record
        for record in _read_json_records(forced_archive)
        if record["record_type"] == "retention_prune_candidate"
    ]
    assert any(
        record["queue"] == queue_name
        and record["message_id"] == str(work_id)
        and record["payload"] == payload
        for record in archived_candidates
    )


def test_force_without_apply_is_rejected(tmp_path: Path) -> None:
    ctx = _context(tmp_path)

    result = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            force=True,
        )
    )

    assert result.errors
    assert result.failed == 0
    assert result.errors == ("--force requires --apply",)


def test_force_deletes_active_ping_in_selected_scope(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    tid = "1770000000000001007"
    other = "1770000000000001008"
    _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid, "status": "running"})
    _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": other, "status": "running"})
    ping_id = _write_raw(ctx, f"T{tid}.ctrl_in", CONTROL_PING)
    other_ping_id = _write_raw(ctx, f"T{other}.ctrl_in", CONTROL_PING)

    result = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            task_filters=(tid,),
            apply=True,
            force=True,
            min_age_seconds=0,
        )
    )

    assert result.deleted == 1
    assert ping_id not in _read_ids(ctx, f"T{tid}.ctrl_in")
    assert result.candidates[0].overridden_protections == ("nonterminal_task",)

    # The encoded selector selects by the canonical full TID through both
    # adapters of the shared prune capability [OBS.5], [CLI-1.2.3].
    client = WeftClient(context=ctx)
    for adapter in ("cli", "client"):
        selected_ping = _write_raw(ctx, f"T{tid}.ctrl_in", CONTROL_PING)
        options: dict[str, Any] = {
            "family": "task-local",
            "apply": True,
            "force": True,
            "min_age": 0,
            "task": [encode_tid(tid)],
        }
        pruned = (
            cmd_system_prune(context=ctx.root, **options)
            if adapter == "cli"
            else client.system.prune(**options)
        )
        assert pruned.deleted == 1, adapter
        assert selected_ping not in _read_ids(ctx, f"T{tid}.ctrl_in"), adapter
        assert other_ping_id in _read_ids(ctx, f"T{other}.ctrl_in"), adapter


def test_client_prune_preserves_resolved_directory_and_broker(
    tmp_path: Path,
) -> None:
    """Client prune scans its resolved context, not a context rebuilt from root.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    root = prepare_project_root(tmp_path / "project")
    alternate = build_context(prepare_project_root(tmp_path / "alternate"))
    configured = build_context(
        root, config=load_config({"WEFT_DIRECTORY_NAME": ".client-state"})
    )
    context = replace(
        configured,
        broker_target=alternate.broker_target,
        broker_config=alternate.broker_config,
        database_path=alternate.database_path,
    )
    tid = "1770000000000001101"
    seeded = {
        _write_json(context, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid, "status": "created"}),
        _write_json(
            context,
            WEFT_GLOBAL_LOG_QUEUE,
            {"tid": tid, "status": "completed", "type": "terminal", "source": "task"},
        ),
        _write_json(
            context,
            WEFT_GLOBAL_LOG_QUEUE,
            {
                "tid": tid,
                "status": "completed",
                "type": "terminal",
                "source": "task",
                "event": "task_completed",
            },
        ),
    }
    client = WeftClient.from_weft_context(context)

    result = client.system.prune(family="task-log", min_age=0)

    assert isinstance(result, SystemPruneResult)
    assert result.applied is False
    assert result.families == ("task-log",)
    assert result.candidates == 2
    assert _read_ids(context, WEFT_GLOBAL_LOG_QUEUE) >= seeded
    assert cmd_system_prune(family="task-log", context=root, min_age=0).candidates == 0

    # The runtime-state branch resolves its context separately.
    stream_tid = "1770000000000001120"
    marker_id = _write_json(
        context,
        WEFT_STREAMING_SESSIONS_QUEUE,
        {"tid": stream_tid, "session_id": "stale", "queue": "T1.outbox"},
    )
    _write_json(
        context,
        WEFT_GLOBAL_LOG_QUEUE,
        {
            "tid": stream_tid,
            "status": "completed",
            "type": "terminal",
            "source": "task",
        },
    )

    runtime = client.system.prune(
        family="runtime-state", queue=["streaming"], min_age=0
    )

    assert runtime.families == ("runtime-state",)
    assert runtime.candidates == 1
    assert marker_id in _read_ids(context, WEFT_STREAMING_SESSIONS_QUEUE)
    assert (
        cmd_system_prune(
            family="runtime-state", queue=["streaming"], context=root, min_age=0
        ).candidates
        == 0
    )


def test_retention_prune_preserves_custom_shared_queues_under_report_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Foreground retention and task-local prune never scan or delete custom/shared queues.

    Spec: docs/specifications/07-System_Invariants.md [OBS.13.9];
    docs/program-theory.md [WT-2a.2], [WT-4.2].
    """
    monkeypatch.setenv("WEFT_TASK_MONITOR_MODE", "report_only")
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        ctx = harness.context
        client = WeftClient(context=ctx)
        shared_outbox = "shared.coordination.outbox"
        shared_ctrl_out = "shared.coordination.ctrl_out"
        shared_ctrl_in = "shared.coordination.ctrl_in"

        task = client.submit(
            {
                "name": "shared-queue-task",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "runner": {"name": "host", "options": {}},
                },
                "io": {
                    "outputs": {"outbox": shared_outbox},
                    "control": {
                        "ctrl_in": shared_ctrl_in,
                        "ctrl_out": shared_ctrl_out,
                    },
                },
            },
            payload="done",
        )
        harness.register_tid(task.tid)
        completed = task.result(timeout=30.0)
        assert completed.status == "completed"
        assert completed.value == "done"

        external_outbox_id = _write_raw(
            ctx,
            shared_outbox,
            "external-live-workflow-message",
            persistent=True,
        )
        external_ctrl_out_id = _write_raw(
            ctx,
            shared_ctrl_out,
            "external-live-ctrl-out-message",
            persistent=True,
        )
        external_ctrl_in_id = _write_raw(
            ctx,
            shared_ctrl_in,
            "external-live-ctrl-in-message",
            persistent=True,
        )

        for family in ("retention", "task-local"):
            archive_path = harness.root / f"archive-{family}.jsonl"
            result = cmd_system_prune(
                family=family,
                apply=True,
                min_age=0,
                archive=archive_path,
                context=harness.root,
            )
            assert result.failed == 0, family
            assert external_outbox_id in _read_ids(
                ctx, shared_outbox, persistent=True
            ), family
            assert external_ctrl_out_id in _read_ids(
                ctx, shared_ctrl_out, persistent=True
            ), family
            assert external_ctrl_in_id in _read_ids(
                ctx, shared_ctrl_in, persistent=True
            ), family
            if archive_path.exists():
                archived_queues = {
                    record.get("queue") for record in _read_json_records(archive_path)
                }
                assert archived_queues.isdisjoint(
                    {shared_outbox, shared_ctrl_out, shared_ctrl_in}
                ), family
