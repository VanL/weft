"""Tests for task-local and task-log retention pruning."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    CONTROL_PING,
    RETENTION_PRUNE_CLASS_NONTERMINAL_TASK_LOG_SUPERSEDED,
    RETENTION_PRUNE_CLASS_OBSOLETE_INBOX_WORK,
    RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_ARCHIVED,
    RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_WITHOUT_LOG_REPORTED,
    RETENTION_PRUNE_CLASS_TERMINAL_RESULT_OUTBOX_ARCHIVED,
    RETENTION_PRUNE_CLASS_TERMINAL_TASK_LOG_SUPERSEDED,
    TERMINAL_ENVELOPE_TYPE,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.commands.retention_prune import RetentionPruneConfig, run_retention_prune
from weft.context import WeftContext, build_context
from weft.core.pruning.retention import RetentionPruneCandidate, _append_records
from weft.helpers import iter_queue_entries

pytestmark = [pytest.mark.shared]


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
        {"tid": tid, "status": "completed"},
    )
    latest_terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": tid, "status": "completed", "event": "task_completed"},
    )

    result = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            min_age_seconds=0,
        )
    )

    assert result.exit_code == 0
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
        {"tid": tid, "status": "completed"},
    )

    missing_archive = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            apply=True,
            min_age_seconds=0,
        )
    )
    assert missing_archive.exit_code == 1
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

    assert result.exit_code == 0
    assert result.deleted == 1
    remaining = _read_ids(ctx, WEFT_GLOBAL_LOG_QUEUE)
    assert old_id not in remaining
    assert keep_id in remaining
    archive_records = _read_json_records(archive)
    assert archive_records[0]["message_id"] == old_id
    assert archive_records[-1]["record_type"] == "retention_prune_completed"


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
    _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid_a, "status": "completed"})

    result_a = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            apply=True,
            archive_path=archive,
            min_age_seconds=0,
        )
    )
    assert result_a.exit_code == 0
    assert result_a.deleted == 1
    run_id_a = result_a.run_id

    tid_b = "1770000000000001004"
    old_id_b = _write_json(
        ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid_b, "status": "created"}
    )
    _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid_b, "status": "completed"})

    result_b = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-log",
            apply=True,
            archive_path=archive,
            min_age_seconds=0,
        )
    )
    assert result_b.exit_code == 0
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
    assert old_id_a in candidate_message_ids
    assert old_id_b in candidate_message_ids
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
    payload = {"tid": run_id, "status": "completed", "filler": "x" * 2048}
    payload_text = json.dumps(payload)
    candidates = [
        RetentionPruneCandidate(
            queue=WEFT_GLOBAL_LOG_QUEUE,
            family="task-log",
            message_id=index,
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
        assert candidate_ids == list(range(n))
        summaries = [
            record
            for record in run_records
            if record["record_type"] == "retention_prune_completed"
        ]
        assert len(summaries) == 1


def test_ctrl_out_terminal_with_log_is_deleted_and_pong_is_preserved(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    archive = tmp_path / "archive.jsonl"
    tid = "1770000000000001003"
    _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid, "status": "completed"})
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
    pong_id = _write_raw(ctx, f"T{tid}.ctrl_out", "PONG")

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
    assert pong_id in remaining


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

    assert forced.exit_code == 0
    assert forced.deleted == 1
    assert terminal_id not in _read_ids(ctx, f"T{tid}.ctrl_out")
    assert forced.warnings == ()
    assert forced.archived >= 1
    assert list((ctx.logs_dir / "retention-prune").glob("*.jsonl"))


def test_outbox_final_result_with_terminal_log_is_deleted(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    archive = tmp_path / "archive.jsonl"
    tid = "1770000000000001005"
    _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid, "status": "completed"})
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


def test_inbox_work_is_report_only_but_force_deletes(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    tid = "1770000000000001006"
    _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid, "status": "completed"})
    work_id = _write_json(ctx, f"T{tid}.inbox", {"work": True})

    ordinary = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            apply=True,
            archive_path=tmp_path / "ordinary.jsonl",
            min_age_seconds=0,
        )
    )

    assert ordinary.deleted == 0
    assert ordinary.candidates[0].candidate_class == (
        RETENTION_PRUNE_CLASS_OBSOLETE_INBOX_WORK
    )
    assert work_id in _read_ids(ctx, f"T{tid}.inbox")

    forced = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            apply=True,
            force=True,
            min_age_seconds=0,
        )
    )

    assert forced.deleted == 1
    assert work_id not in _read_ids(ctx, f"T{tid}.inbox")
    assert forced.warnings == ()
    assert forced.archived >= 1
    assert list((ctx.logs_dir / "retention-prune").glob("*.jsonl"))


def test_force_without_apply_is_rejected(tmp_path: Path) -> None:
    ctx = _context(tmp_path)

    result = run_retention_prune(
        RetentionPruneConfig(
            context_path=ctx.root,
            family="task-local",
            force=True,
        )
    )

    assert result.exit_code == 1
    assert result.errors == ("--force requires --apply",)


def test_force_deletes_active_ping_in_selected_scope(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    tid = "1770000000000001007"
    _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid, "status": "running"})
    ping_id = _write_raw(ctx, f"T{tid}.ctrl_in", CONTROL_PING)

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
