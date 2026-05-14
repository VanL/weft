"""Tests for TaskMonitor cleanup composition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    QUEUE_RESERVED_SUFFIX,
    TASK_MONITOR_POLICY_RESERVED_DELETE_TERMINAL_PROVEN,
    TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE,
    TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START,
    TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED,
    TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START,
    TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED,
    TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.pruning.policies import malformed_row_candidates, older_than_candidates
from weft.core.queue_window import DecodedQueueWindowRow, QueueWindowRow
from weft.core.task_log_collation import collate_next_task_log_group
from weft.core.tasks.task_monitor_cleanup import (
    TaskMonitorCleanupConfig,
    run_task_monitor_cleanup,
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


def _decoded_row(
    queue_name: str,
    message_id: int,
    payload: dict[str, Any] | None,
    *,
    malformed_reason: str | None = None,
) -> DecodedQueueWindowRow:
    body = json.dumps(payload) if payload is not None else "{bad-json"
    return DecodedQueueWindowRow(
        raw=QueueWindowRow(queue=queue_name, body=body, message_id=message_id),
        payload=payload,
        malformed_reason=malformed_reason,
    )


def test_malformed_policy_selects_only_explicitly_malformed_rows() -> None:
    rows = (
        _decoded_row("owned.queue", 100, None, malformed_reason="invalid_json"),
        _decoded_row("owned.queue", 101, {"tid": "1778000000000000001"}),
    )

    candidates = malformed_row_candidates(
        rows,
        policy="test.delete_malformed",
        candidate_class="malformed_owned_queue",
    )

    assert [candidate.message_id for candidate in candidates] == [100]
    assert candidates[0].policy == "test.delete_malformed"
    assert candidates[0].candidate_class == "malformed_owned_queue"
    assert candidates[0].reason == "invalid_json"


def test_older_than_policy_skips_claimed_rows_and_stops_at_young_fifo_row() -> None:
    rows = (
        _decoded_row("owned.queue", 1_000_000_000, {"tid": "claimed"}),
        _decoded_row("owned.queue", 2_000_000_000, {"tid": "old"}),
        _decoded_row("owned.queue", 3_000_000_000, {"tid": "young"}),
    )

    selection = older_than_candidates(
        rows,
        policy="test.delete_old",
        now_ns=3_500_000_000,
        min_age_seconds=1.0,
        candidate_class="old_owned_row",
        reason="older_than_policy",
        stop_reason="first_owned_row_too_young",
        claimed_ids={1_000_000_000},
    )

    assert [candidate.tid for candidate in selection.candidates] == ["old"]
    assert [candidate.policy for candidate in selection.candidates] == [
        "test.delete_old"
    ]
    assert selection.stop_reason == "first_owned_row_too_young"


def _policy_summary_by_policy(result) -> dict[str, dict[str, Any]]:
    return {stat.policy: stat.to_summary() for stat in result.policy_stats}


def test_task_log_collation_skips_nonterminal_anchor_for_later_terminal() -> None:
    rows = (
        _decoded_row(
            WEFT_GLOBAL_LOG_QUEUE,
            1_000_000_000,
            {"event": "work_started", "tid": "1778000000000000001"},
        ),
        _decoded_row(
            WEFT_GLOBAL_LOG_QUEUE,
            2_000_000_000,
            {"event": "work_started", "tid": "1778000000000000002"},
        ),
        _decoded_row(
            WEFT_GLOBAL_LOG_QUEUE,
            3_000_000_000,
            {"event": "work_completed", "tid": "1778000000000000002"},
        ),
    )
    first = collate_next_task_log_group(
        rows,
        now_ns=4_500_000_000,
        min_age_seconds=1.0,
        exclude_tids=set(),
        claimed_ids=set(),
        skipped_tids=set(),
    )

    second = collate_next_task_log_group(
        rows,
        now_ns=4_500_000_000,
        min_age_seconds=1.0,
        exclude_tids=set(),
        claimed_ids=set(),
        skipped_tids={first.skipped_tid or ""},
    )

    assert first.group is None
    assert first.skipped_tid == "1778000000000000001"
    assert second.group is not None
    assert second.group.tid == "1778000000000000002"
    assert second.group.message_ids == (2_000_000_000, 3_000_000_000)


def test_task_monitor_cleanup_deletes_malformed_task_log(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    bad_id = _write_raw(ctx, WEFT_GLOBAL_LOG_QUEUE, "{not json")
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=60.0),
        apply=True,
        now_ns=bad_id,
    )

    assert result.success
    assert result.deleted == 1
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "malformed_task_log"
    ]
    stats = _policy_summary_by_policy(result)
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED]["selected"] == 1
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED]["deleted"] == 1
    assert (
        stats[TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE]["selected"] == 0
    )
    assert (
        stats[TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START]["selected"]
        == 0
    )
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START]["selected"] == 0
    assert [body for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)] == [
        json.dumps({"event": "work_started", "tid": "1778000000000000001"})
    ]


def test_task_monitor_cleanup_deletes_malformed_tid_mapping(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    bad_id = _write_raw(ctx, WEFT_TID_MAPPINGS_QUEUE, json.dumps({"short": "bad"}))
    _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "0000000001", "full": "1778000000000000001"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, tid_mapping_min_age_seconds=60.0),
        apply=True,
        now_ns=bad_id,
    )

    assert result.success
    assert result.deleted == 1
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "malformed_tid_mapping"
    ]
    stats = _policy_summary_by_policy(result)
    assert stats[TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED]["selected"] == 1
    assert stats[TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED]["deleted"] == 1
    assert stats[TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN]["selected"] == 0
    rows = _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)
    assert len(rows) == 1
    assert json.loads(rows[0][0])["full"] == "1778000000000000001"


def test_task_monitor_cleanup_report_only_keeps_selected_rows(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    _write_raw(ctx, WEFT_GLOBAL_LOG_QUEUE, "[1, 2, 3]")

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10),
        apply=False,
    )

    assert result.success
    assert result.processed == 1
    assert result.reported == 1
    assert result.deleted == 0
    stats = _policy_summary_by_policy(result)
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED]["selected"] == 1
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED]["reported"] == 1
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED]["deleted"] == 0
    assert len(_read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)) == 1


def test_task_monitor_cleanup_deletes_old_tid_mapping(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    mapping_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "0000000001", "full": "1778000000000000001"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, tid_mapping_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(mapping_id, 2.0),
    )

    assert result.success
    assert result.deleted == 1
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "old_tid_mapping"
    ]
    stats = _policy_summary_by_policy(result)
    assert stats[TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED]["selected"] == 0
    assert stats[TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN]["selected"] == 1
    assert stats[TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN]["deleted"] == 1
    assert _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE) == []


def test_task_monitor_cleanup_preserves_young_tid_mapping(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    mapping_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "0000000001", "full": "1778000000000000001"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, tid_mapping_min_age_seconds=60.0),
        apply=True,
        now_ns=_now_after(mapping_id, 1.0),
    )

    assert result.success
    assert result.deleted == 0
    assert result.queue_stats[0].stop_reason == "first_tid_mapping_too_young"
    stats = _policy_summary_by_policy(result)
    assert stats[TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED]["selected"] == 0
    assert stats[TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN]["selected"] == 0
    assert (
        stats[TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN]["stop_reason"]
        == "first_tid_mapping_too_young"
    )
    assert len(_read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)) == 1


def test_task_monitor_cleanup_collates_terminal_task_log_for_anchor_tid(
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

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(start_id, 2.0),
    )

    assert result.success
    assert result.deleted == 2
    assert {candidate.message_id for candidate in result.candidates} == {
        candidate.candidate.message_id for candidate in result.applied_candidates
    }
    stats = _policy_summary_by_policy(result)
    assert (
        stats[TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE]["selected"] == 2
    )
    assert (
        stats[TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE]["deleted"] == 2
    )
    assert (
        stats[TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START]["selected"]
        == 0
    )
    assert all(
        stat.policy != TASK_MONITOR_POLICY_RESERVED_DELETE_TERMINAL_PROVEN
        for stat in result.policy_stats
    )
    remaining = [
        json.loads(body) for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
    ]
    assert remaining == [{"event": "work_started", "tid": "1778000000000000002"}]


def test_task_monitor_cleanup_deletes_reserved_work_with_terminal_log_proof(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    tid = "1778000000000000001"
    reserved_queue = f"T{tid}.{QUEUE_RESERVED_SUFFIX}"
    _write_json(ctx, reserved_queue, {"payload": "retained failed work"})
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": tid},
    )
    terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_failed", "tid": tid, "status": "failed"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(terminal_id, 2.0),
    )

    assert result.success
    assert result.deleted == 3
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "collated_terminal_task_log",
        "collated_terminal_task_log",
        "terminal_reserved_with_log",
    ]
    reserved_policy_stats = [
        stat
        for stat in result.policy_stats
        if stat.policy == TASK_MONITOR_POLICY_RESERVED_DELETE_TERMINAL_PROVEN
    ]
    assert len(reserved_policy_stats) == 1
    assert reserved_policy_stats[0].queue == reserved_queue
    assert reserved_policy_stats[0].selected == 1
    assert reserved_policy_stats[0].deleted == 1
    reserved_stats = next(
        stat for stat in result.queue_stats if stat.queue == reserved_queue
    )
    assert reserved_stats.selected == 1
    assert reserved_stats.deleted == 1
    assert _read_rows(ctx, reserved_queue) == []


def test_task_monitor_cleanup_preserves_reserved_work_without_terminal_log_proof(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    tid = "1778000000000000001"
    reserved_queue = f"T{tid}.{QUEUE_RESERVED_SUFFIX}"
    reserved_id = _write_json(ctx, reserved_queue, {"payload": "retained work"})

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(reserved_id, 2.0),
    )

    assert result.success
    assert result.deleted == 0
    assert [
        json.loads(body) for body, _message_id in _read_rows(ctx, reserved_queue)
    ] == [{"payload": "retained work"}]


def test_task_monitor_cleanup_repeats_collate_for_multiple_terminal_tids(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    _write_json(
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
    last_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000003"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        # Anchor after the final row so the assertion is independent of slow
        # queue writes on loaded Windows CI runners.
        now_ns=_now_after(last_id, 2.0),
    )

    assert result.success
    assert result.deleted == 4
    task_log_stats = next(
        stat for stat in result.queue_stats if stat.queue == WEFT_GLOBAL_LOG_QUEUE
    )
    assert [
        summary.to_summary()["tid"] for summary in task_log_stats.collated_tasks
    ] == [
        "1778000000000000001",
        "1778000000000000002",
    ]
    assert task_log_stats.to_summary()["collated_task_count"] == 2
    assert [
        json.loads(body) for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
    ] == [{"event": "work_started", "tid": "1778000000000000003"}]


def test_task_monitor_cleanup_policy_order_collates_complete_before_truncated(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    terminal_id = _write_json(
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

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=False,
        now_ns=_now_after(terminal_id, 2.0),
    )

    assert result.success
    assert [candidate.policy for candidate in result.candidates] == [
        TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE,
        TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE,
        TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START,
    ]


def test_task_monitor_cleanup_nonterminal_anchor_does_not_block_later_collate(
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

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(start_id, 2.0),
    )

    assert result.success
    assert result.deleted == 2
    assert [
        json.loads(body) for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
    ] == [{"event": "work_started", "tid": "1778000000000000001"}]


def test_task_monitor_cleanup_deletes_old_rows_after_collation(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000001"},
    )
    orphan_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "orphan_observation", "tid": "1778000000000000002"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(orphan_id, 2.0),
    )

    assert result.success
    assert result.deleted == 3
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "collated_terminal_task_log",
        "collated_terminal_task_log",
        "old_task_log",
    ]
    stats = _policy_summary_by_policy(result)
    assert (
        stats[TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE]["selected"] == 2
    )
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START]["selected"] == 1
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START]["deleted"] == 1
    assert _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE) == []


def test_task_monitor_cleanup_preserves_open_start_when_deleting_old_rows(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )
    orphan_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "orphan_observation", "tid": "1778000000000000002"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(orphan_id, 2.0),
    )

    assert result.success
    assert result.deleted == 1
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "old_task_log"
    ]
    assert [
        json.loads(body) for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
    ] == [{"event": "work_started", "tid": "1778000000000000001"}]


def test_task_monitor_cleanup_classifies_terminal_without_visible_start_as_truncated(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000001"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(terminal_id, 2.0),
    )

    assert result.success
    assert result.deleted == 1
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "truncated_terminal_task_log"
    ]
    stats = _policy_summary_by_policy(result)
    assert (
        stats[TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START]["selected"]
        == 1
    )
    assert (
        stats[TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START]["deleted"]
        == 1
    )
    assert (
        stats[TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE]["selected"] == 0
    )
    assert _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE) == []


def test_task_monitor_cleanup_does_not_collate_young_terminal_task_log(
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

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=60.0),
        apply=True,
        now_ns=_now_after(start_id, 1.0),
    )

    assert result.success
    assert result.deleted == 0
    assert len(_read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)) == 2


def test_task_monitor_cleanup_without_terminal_preserves_open_start(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    start_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(start_id, 2.0),
    )

    assert result.success
    assert result.deleted == 0
    assert result.candidates == ()
    assert [
        json.loads(body) for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
    ] == [{"event": "work_started", "tid": "1778000000000000001"}]


def test_task_monitor_cleanup_policy_order_classifies_malformed_before_age(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    bad_id = _write_raw(ctx, WEFT_GLOBAL_LOG_QUEUE, "null")

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=0.0),
        apply=True,
        now_ns=_now_after(bad_id, 10.0),
    )

    assert result.success
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "malformed_task_log"
    ]


def test_task_monitor_cleanup_scan_budget_and_followup_delete_do_not_skip(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    for index in range(5):
        _write_raw(ctx, WEFT_GLOBAL_LOG_QUEUE, f"bad-{index}")

    config = TaskMonitorCleanupConfig(batch_size=2)
    first = run_task_monitor_cleanup(ctx, config, apply=True)
    second = run_task_monitor_cleanup(ctx, config, apply=True)

    assert first.records_scanned == 2
    assert first.deleted == 2
    assert second.records_scanned == 2
    assert second.deleted == 2
    assert len(_read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)) == 1
