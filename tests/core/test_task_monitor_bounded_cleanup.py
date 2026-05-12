"""Tests for bounded TaskMonitor cleanup policies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, WEFT_TID_MAPPINGS_QUEUE
from weft.context import WeftContext, build_context
from weft.core.pruning.bounded_cleanup import (
    BoundedCleanupConfig,
    run_bounded_task_monitor_cleanup,
)
from weft.helpers import iter_queue_entries

pytestmark = [pytest.mark.shared]


def _context(tmp_path: Path) -> WeftContext:
    root = prepare_project_root(tmp_path)
    return build_context(spec_context=root)


def _write_raw(ctx: WeftContext, queue_name: str, body: str) -> int:
    queue = ctx.queue(queue_name, persistent=False)
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


def _write_json(ctx: WeftContext, queue_name: str, payload: dict[str, Any]) -> int:
    return _write_raw(ctx, queue_name, json.dumps(payload))


def _read_rows(ctx: WeftContext, queue_name: str) -> list[tuple[str, int]]:
    queue = ctx.queue(queue_name, persistent=False)
    try:
        return list(iter_queue_entries(queue))
    finally:
        queue.close()


def _now_after(message_id: int, seconds: float) -> int:
    return message_id + int(seconds * 1_000_000_000) + 1


def test_bounded_cleanup_deletes_malformed_task_log(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    bad_id = _write_raw(ctx, WEFT_GLOBAL_LOG_QUEUE, "{not json")
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10, task_log_min_age_seconds=60.0),
        apply=True,
        now_ns=bad_id,
    )

    assert result.success
    assert result.deleted == 1
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "malformed_task_log"
    ]
    assert [body for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)] == [
        json.dumps({"event": "work_started", "tid": "1778000000000000001"})
    ]


def test_bounded_cleanup_deletes_malformed_tid_mapping(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    bad_id = _write_raw(ctx, WEFT_TID_MAPPINGS_QUEUE, json.dumps({"short": "bad"}))
    _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "0000000001", "full": "1778000000000000001"},
    )

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10, tid_mapping_min_age_seconds=60.0),
        apply=True,
        now_ns=bad_id,
    )

    assert result.success
    assert result.deleted == 1
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "malformed_tid_mapping"
    ]
    rows = _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)
    assert len(rows) == 1
    assert json.loads(rows[0][0])["full"] == "1778000000000000001"


def test_bounded_cleanup_report_only_keeps_selected_rows(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    _write_raw(ctx, WEFT_GLOBAL_LOG_QUEUE, "[1, 2, 3]")

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10),
        apply=False,
    )

    assert result.success
    assert result.processed == 1
    assert result.reported == 1
    assert result.deleted == 0
    assert len(_read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)) == 1


def test_bounded_cleanup_deletes_old_tid_mapping(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    mapping_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "0000000001", "full": "1778000000000000001"},
    )

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10, tid_mapping_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(mapping_id, 2.0),
    )

    assert result.success
    assert result.deleted == 1
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "old_tid_mapping"
    ]
    assert _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE) == []


def test_bounded_cleanup_preserves_young_tid_mapping(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    mapping_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "0000000001", "full": "1778000000000000001"},
    )

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10, tid_mapping_min_age_seconds=60.0),
        apply=True,
        now_ns=_now_after(mapping_id, 1.0),
    )

    assert result.success
    assert result.deleted == 0
    assert result.queue_stats[0].stop_reason == "first_tid_mapping_too_young"
    assert len(_read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)) == 1


def test_bounded_cleanup_collates_terminal_task_log_for_anchor_tid(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    start_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000002"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000001"},
    )

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(start_id, 2.0),
    )

    assert result.success
    assert result.deleted == 2
    assert {candidate.message_id for candidate in result.candidates} == {
        candidate.candidate.message_id for candidate in result.applied_candidates
    }
    remaining = [
        json.loads(body) for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
    ]
    assert remaining == [{"event": "work_started", "tid": "1778000000000000002"}]


def test_bounded_cleanup_repeats_collate_for_multiple_terminal_tids(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    start_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000001"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000002"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000002"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000003"},
    )

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(start_id, 2.0),
    )

    assert result.success
    assert result.deleted == 4
    task_log_stats = next(
        stat for stat in result.queue_stats if stat.queue == WEFT_GLOBAL_LOG_QUEUE
    )
    assert [summary.tid for summary in task_log_stats.collated_tasks] == [
        "1778000000000000001",
        "1778000000000000002",
    ]
    assert task_log_stats.to_summary()["collated_task_count"] == 2
    assert [
        json.loads(body) for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
    ] == [{"event": "work_started", "tid": "1778000000000000003"}]


def test_bounded_cleanup_nonterminal_anchor_does_not_block_later_collate(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    start_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000002"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000002"},
    )

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(start_id, 2.0),
    )

    assert result.success
    assert result.deleted == 2
    assert [
        json.loads(body) for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
    ] == [{"event": "work_started", "tid": "1778000000000000001"}]


def test_bounded_cleanup_does_not_collate_young_terminal_task_log(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    start_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000001"},
    )

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10, task_log_min_age_seconds=60.0),
        apply=True,
        now_ns=_now_after(start_id, 1.0),
    )

    assert result.success
    assert result.deleted == 0
    assert len(_read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)) == 2


def test_bounded_cleanup_without_terminal_falls_back_to_older_than(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    start_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(start_id, 2.0),
    )

    assert result.success
    assert result.deleted == 1
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "old_task_log"
    ]
    assert _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE) == []


def test_bounded_cleanup_policy_order_classifies_malformed_before_age(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    bad_id = _write_raw(ctx, WEFT_GLOBAL_LOG_QUEUE, "null")

    result = run_bounded_task_monitor_cleanup(
        ctx,
        BoundedCleanupConfig(batch_size=10, task_log_min_age_seconds=0.0),
        apply=True,
        now_ns=_now_after(bad_id, 10.0),
    )

    assert result.success
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "malformed_task_log"
    ]


def test_bounded_cleanup_scan_budget_and_followup_delete_do_not_skip(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    for index in range(5):
        _write_raw(ctx, WEFT_GLOBAL_LOG_QUEUE, f"bad-{index}")

    config = BoundedCleanupConfig(batch_size=2)
    first = run_bounded_task_monitor_cleanup(ctx, config, apply=True)
    second = run_bounded_task_monitor_cleanup(ctx, config, apply=True)

    assert first.records_scanned == 2
    assert first.deleted == 2
    assert second.records_scanned == 2
    assert second.deleted == 2
    assert len(_read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)) == 1
