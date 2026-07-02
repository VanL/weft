"""Tests for TaskMonitor cleanup composition."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
import pytest

import weft.core.monitor.cleanup as cleanup_mod
from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    QUEUE_RESERVED_SUFFIX,
    TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
    TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME,
    TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.client import WeftClient
from weft.commands.queue import peek_queue
from weft.context import WeftContext, build_context
from weft.core.monitor.cleanup import (
    TaskMonitorCleanupConfig,
    run_task_monitor_cleanup,
)
from weft.core.monitor.task_log_collation import collate_next_task_log_group
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.core.pruning.policies import malformed_row_candidates, older_than_candidates
from weft.core.queue_window import DecodedQueueWindowRow, QueueWindowRow
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


def _queue_stats(ctx: WeftContext, queue_name: str) -> tuple[int, int, int]:
    queue = ctx.queue(queue_name, persistent=False)
    try:
        stats = queue.stats()
        return stats.pending, stats.claimed, stats.total
    finally:
        queue.close()


def _claim_one(ctx: WeftContext, queue_name: str) -> tuple[str, int]:
    queue = ctx.queue(queue_name, persistent=False)
    try:
        claimed = queue.read_one(with_timestamps=True)
        assert isinstance(claimed, tuple)
        body, message_id = claimed
        assert isinstance(body, str)
        assert isinstance(message_id, int)
        return body, message_id
    finally:
        queue.close()


def _now_after(message_id: int, seconds: float) -> int:
    return message_id + int(seconds * 1_000_000_000) + 1


@dataclass(frozen=True, slots=True)
class _ExactDeleteCandidate:
    queue: str
    message_id: int
    report_only: bool = False


def test_apply_exact_prune_candidates_reports_missing_row_not_deleted(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    missing_id = 1779000000000020000

    applied = apply_exact_prune_candidates(
        ctx,
        (
            _ExactDeleteCandidate(
                queue="test.exact-delete",
                message_id=missing_id,
            ),
        ),
        apply_result=lambda candidate, deleted, error: (
            candidate.message_id,
            deleted,
            error,
        ),
    )

    assert applied == [(missing_id, False, None)]


def test_apply_exact_prune_candidates_reports_deleted_rows(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    queue_name = "test.exact-delete"
    message_id = _write_raw(ctx, queue_name, "payload")

    applied = apply_exact_prune_candidates(
        ctx,
        (
            _ExactDeleteCandidate(
                queue=queue_name,
                message_id=message_id,
            ),
        ),
        apply_result=lambda candidate, deleted, error: (
            candidate.message_id,
            deleted,
            error,
        ),
    )

    assert applied == [(message_id, True, None)]
    assert _read_rows(ctx, queue_name) == []


def test_apply_exact_prune_candidates_exact_status_handles_mixed_missing_rows(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    queue_name = "test.exact-delete"
    first_id = _write_raw(ctx, queue_name, "first")
    second_id = _write_raw(ctx, queue_name, "second")
    missing_id = second_id + 100_000

    applied = apply_exact_prune_candidates(
        ctx,
        (
            _ExactDeleteCandidate(queue=queue_name, message_id=first_id),
            _ExactDeleteCandidate(queue=queue_name, message_id=missing_id),
            _ExactDeleteCandidate(queue=queue_name, message_id=second_id),
        ),
        apply_result=lambda candidate, deleted, error: (
            candidate.message_id,
            deleted,
            error,
        ),
        exact_status=True,
    )

    assert applied == [
        (first_id, True, None),
        (missing_id, False, None),
        (second_id, True, None),
    ]
    assert _read_rows(ctx, queue_name) == []


def test_apply_exact_prune_candidates_reconcile_missing_marks_success(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    queue_name = "test.exact-delete"
    first_id = _write_raw(ctx, queue_name, "first")
    second_id = _write_raw(ctx, queue_name, "second")
    missing_id = second_id + 100_000

    applied = apply_exact_prune_candidates(
        ctx,
        (
            _ExactDeleteCandidate(queue=queue_name, message_id=first_id),
            _ExactDeleteCandidate(queue=queue_name, message_id=missing_id),
            _ExactDeleteCandidate(queue=queue_name, message_id=second_id),
        ),
        apply_result=lambda candidate, deleted, error: (
            candidate.message_id,
            deleted,
            error,
        ),
        reconcile_missing=True,
    )

    assert applied == [
        (first_id, True, None),
        (missing_id, True, None),
        (second_id, True, None),
    ]
    assert _read_rows(ctx, queue_name) == []


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
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["selected"] == 1
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["deleted"] == 1
    assert [body for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)] == [
        json.dumps({"event": "work_started", "tid": "1778000000000000001"})
    ]


def test_task_monitor_cleanup_physically_deletes_task_log_rows(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    first_bad_id = _write_raw(ctx, WEFT_GLOBAL_LOG_QUEUE, "{bad-json")
    second_bad_id = _write_raw(ctx, WEFT_GLOBAL_LOG_QUEUE, "[1, 2, 3]")

    assert _queue_stats(ctx, WEFT_GLOBAL_LOG_QUEUE) == (2, 0, 2)

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=60.0),
        apply=True,
        now_ns=second_bad_id,
    )

    assert result.success
    assert result.deleted == 2
    assert {candidate.message_id for candidate in result.candidates} == {
        first_bad_id,
        second_bad_id,
    }
    assert _queue_stats(ctx, WEFT_GLOBAL_LOG_QUEUE) == (0, 0, 0)


def test_task_monitor_cleanup_deletes_claimed_task_log_before_collation(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    claimed_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000001"},
    )
    claimed_body, claimed_message_id = _claim_one(ctx, WEFT_GLOBAL_LOG_QUEUE)
    assert claimed_message_id == claimed_id
    assert json.loads(claimed_body)["tid"] == "1778000000000000001"
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000002"},
    )
    terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000002"},
    )
    assert _queue_stats(ctx, WEFT_GLOBAL_LOG_QUEUE) == (2, 1, 3)

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(
            batch_size=10,
            task_log_min_age_seconds=1.0,
            reserved_cleanup_enabled=True,
        ),
        apply=True,
        now_ns=_now_after(terminal_id, 2.0),
    )

    assert result.success
    assert result.deleted == 3
    assert [candidate.policy for candidate in result.candidates] == [
        TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
    ]
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "claimed_task_log",
        "collated_terminal_task_log",
        "collated_terminal_task_log",
    ]
    stats = _policy_summary_by_policy(result)
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["selected"] == 3
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["deleted"] == 3
    assert _queue_stats(ctx, WEFT_GLOBAL_LOG_QUEUE) == (0, 0, 0)


def test_task_monitor_cleanup_skips_claimed_scan_when_queue_has_no_claimed_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    started_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": "1778000000000000001"},
    )

    def fail_claimed_retrieve(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "claimed scan should be skipped when claimed count is zero"
        )

    monkeypatch.setattr(
        "weft.core.monitor.cleanup._peek_rows_including_claimed",
        fail_claimed_retrieve,
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=60.0),
        apply=True,
        now_ns=started_id,
    )

    assert result.success
    assert result.deleted == 0
    stats = _policy_summary_by_policy(result)
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["selected"] == 0


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
    assert stats[TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION]["selected"] == 1
    assert stats[TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION]["deleted"] == 1
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
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["selected"] == 1
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["reported"] == 1
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["deleted"] == 0
    assert len(_read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)) == 1


def _tid_mapping_payload(
    *,
    full: str,
    short: str,
    host_processes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"short": short, "full": full}
    if host_processes is not None:
        payload["runtime_handle"] = {
            "runner": "host",
            "kind": "process",
            "id": full,
            "control": {"authority": "host-pid"},
            "observations": {"host_processes": host_processes},
            "metadata": {},
        }
    return payload


def test_task_monitor_cleanup_deletes_old_tid_mapping_with_no_probeable_owner(
    tmp_path: Path,
) -> None:
    """A mapping payload with no probeable host PIDs is undecidable liveness.

    Per [OBS.13.7] and the Cleanup Boundary policy, undecidable liveness
    means skip, never delete -- even past min-age. This replaces the old
    assertion (which encoded the KEYSTONE defect: deleting the newest, and
    only, mapping row of a task regardless of liveness).
    """
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
    assert result.deleted == 0
    assert result.candidates == ()
    stats = _policy_summary_by_policy(result)
    assert stats[TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION]["selected"] == 0
    assert stats[TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION]["deleted"] == 0
    assert len(_read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)) == 1


def test_task_monitor_cleanup_deletes_superseded_rows_of_live_task(
    tmp_path: Path,
) -> None:
    """Non-newest rows for a live task's key keep the age-only rule."""
    ctx = _context(tmp_path)
    tid = "1778000000000000002"
    self_process = psutil.Process(os.getpid())
    host_processes = [
        {"pid": os.getpid(), "create_time": self_process.create_time()}
    ]

    superseded_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        _tid_mapping_payload(
            full=tid, short="0000000002", host_processes=host_processes
        ),
    )
    newest_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        _tid_mapping_payload(
            full=tid, short="0000000002", host_processes=host_processes
        ),
    )
    assert newest_id > superseded_id

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, tid_mapping_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(newest_id, 2.0),
    )

    assert result.success
    assert result.deleted == 1
    assert [candidate.candidate_class for candidate in result.candidates] == [
        "superseded_tid_mapping"
    ]
    remaining = _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)
    assert len(remaining) == 1
    remaining_payload = json.loads(remaining[0][0])
    assert remaining_payload["full"] == tid


def test_superseded_live_row_is_deleted_when_newer_row_is_beyond_scan_window(
    tmp_path: Path,
) -> None:
    """A superseded row must keep age-only deletion even in a truncated window.

    Regression for the window-bounded newest-per-key defect: with
    batch_size=1 the scan window holds only the superseded live-owner row,
    whose newer sibling lies beyond the window. Newest-per-key must be
    determined from full-queue evidence, not the partial slice, so the
    superseded row is deleted (age-only) and cleanup progress advances
    instead of wedging on a permanently "protected" stale row.
    """
    ctx = _context(tmp_path)
    tid = "1778000000000000006"
    self_process = psutil.Process(os.getpid())
    host_processes = [
        {"pid": os.getpid(), "create_time": self_process.create_time()}
    ]

    superseded_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        _tid_mapping_payload(
            full=tid, short="0000000006", host_processes=host_processes
        ),
    )
    newest_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        _tid_mapping_payload(
            full=tid, short="0000000006", host_processes=host_processes
        ),
    )
    assert newest_id > superseded_id

    config = TaskMonitorCleanupConfig(
        batch_size=1,
        tid_mapping_min_age_seconds=1.0,
    )
    first = run_task_monitor_cleanup(
        ctx,
        config,
        apply=True,
        now_ns=_now_after(newest_id, 2.0),
    )

    assert first.success
    assert first.deleted == 1
    assert [candidate.candidate_class for candidate in first.candidates] == [
        "superseded_tid_mapping"
    ]
    remaining = _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)
    assert [message_id for _body, message_id in remaining] == [newest_id]
    tid_mapping_progress = [
        progress
        for progress in first.policy_progress
        if progress.domain == WEFT_TID_MAPPINGS_QUEUE
    ]
    assert len(tid_mapping_progress) == 1
    assert tid_mapping_progress[0].waypoint_reached is True

    # Convergence: the next cycle sees the (live, genuinely newest) row,
    # protects it, and must NOT claim a catch-up waypoint -- otherwise the
    # monitor hot-loops on catch-up cadence with zero forward progress.
    second = run_task_monitor_cleanup(
        ctx,
        config,
        apply=True,
        now_ns=_now_after(newest_id, 2.0),
    )
    assert second.success
    assert second.deleted == 0
    assert len(_read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)) == 1
    second_progress = [
        progress
        for progress in second.policy_progress
        if progress.domain == WEFT_TID_MAPPINGS_QUEUE
    ]
    assert len(second_progress) == 1
    assert second_progress[0].waypoint_reached is False


def test_task_monitor_cleanup_preserves_newest_row_of_live_owner_past_min_age(
    tmp_path: Path,
) -> None:
    """The newest mapping row of a live-probing owner survives regardless of age."""
    ctx = _context(tmp_path)
    tid = "1778000000000000003"
    self_process = psutil.Process(os.getpid())
    mapping_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        _tid_mapping_payload(
            full=tid,
            short="0000000003",
            host_processes=[
                {"pid": os.getpid(), "create_time": self_process.create_time()}
            ],
        ),
    )

    # Age well past min-age using the policy's own min-age parameter (not a
    # monkeypatched constant): a plain task running for "days" should keep
    # its one mapping row.
    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, tid_mapping_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(mapping_id, 3600.0),
    )

    assert result.success
    assert result.deleted == 0
    assert result.candidates == ()
    assert len(_read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)) == 1


def test_task_monitor_cleanup_deletes_newest_row_of_dead_owner_after_min_age(
    tmp_path: Path,
) -> None:
    """The newest mapping row of a dead owner is deletable once past min-age."""
    ctx = _context(tmp_path)
    tid = "1778000000000000004"

    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ps_proc = psutil.Process(proc.pid)
    create_time = ps_proc.create_time()
    proc.wait(timeout=10)
    deadline = time.monotonic() + 5.0
    while psutil.pid_exists(proc.pid) and time.monotonic() < deadline:
        time.sleep(0.05)

    mapping_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        _tid_mapping_payload(
            full=tid,
            short="0000000004",
            host_processes=[{"pid": proc.pid, "create_time": create_time}],
        ),
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
    assert stats[TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION]["selected"] == 0
    assert stats[TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION]["selected"] == 0
    assert (
        stats[TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION]["stop_reason"]
        == "first_tid_mapping_too_young"
    )
    assert len(_read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)) == 1


def test_task_monitor_cleanup_collates_terminal_task_log_for_anchor_tid(
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
        {"event": "work_started", "tid": "1778000000000000002"},
    )
    terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000001"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(
            batch_size=10,
            task_log_min_age_seconds=1.0,
            reserved_cleanup_enabled=True,
        ),
        apply=True,
        now_ns=_now_after(terminal_id, 2.0),
    )

    assert result.success
    assert result.deleted == 2
    assert {candidate.message_id for candidate in result.candidates} == {
        candidate.candidate.message_id for candidate in result.applied_candidates
    }
    stats = _policy_summary_by_policy(result)
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["selected"] == 2
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["deleted"] == 2
    assert all(
        stat.policy != TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME
        for stat in result.policy_stats
    )
    remaining = [
        json.loads(body) for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
    ]
    assert remaining == [{"event": "work_started", "tid": "1778000000000000002"}]


def test_task_monitor_cleanup_collates_complete_family_behind_open_prefix(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    open_tid = "1778000000000000001"
    complete_tid = "1778000000000000002"
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": open_tid},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "task_activity", "tid": open_tid, "activity": "waiting"},
    )
    complete_start_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": complete_tid},
    )
    complete_terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "status": "completed", "tid": complete_tid},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(
            batch_size=2,
            task_log_scan_limit=10,
            task_log_min_age_seconds=1.0,
        ),
        apply=True,
        now_ns=_now_after(complete_terminal_id, 2.0),
    )

    assert result.success
    assert result.deleted == 2
    assert {candidate.message_id for candidate in result.candidates} == {
        complete_start_id,
        complete_terminal_id,
    }
    stats = _policy_summary_by_policy(result)
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["selected"] == 2
    progress = next(
        progress
        for progress in result.policy_progress
        if progress.policy == TASK_MONITOR_POLICY_TASK_LOG_RETENTION
    )
    assert progress.deferred == 1
    queue_summary = next(
        stat.to_summary()
        for stat in result.queue_stats
        if stat.queue == WEFT_GLOBAL_LOG_QUEUE
    )
    assert (
        queue_summary["metadata"]["family_selection"]["skipped_open_family_count"] == 1
    )
    remaining = [
        json.loads(body) for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
    ]
    assert [row["tid"] for row in remaining] == [open_tid, open_tid]


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
        TaskMonitorCleanupConfig(
            batch_size=10,
            task_log_min_age_seconds=1.0,
            reserved_cleanup_enabled=True,
        ),
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
        if stat.policy == TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME
    ]
    assert len(reserved_policy_stats) == 1
    assert reserved_policy_stats[0].queue == reserved_queue
    assert reserved_policy_stats[0].selected == 1
    assert reserved_policy_stats[0].deleted == 1
    reserved_progress = [
        progress
        for progress in result.policy_progress
        if progress.policy == TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME
    ]
    assert len(reserved_progress) == 1
    assert reserved_progress[0].domain == "task_runtime_queues"
    assert reserved_progress[0].selected == 1
    assert reserved_progress[0].applied == 1
    reserved_stats = next(
        stat for stat in result.queue_stats if stat.queue == reserved_queue
    )
    assert reserved_stats.selected == 1
    assert reserved_stats.deleted == 1
    assert _read_rows(ctx, reserved_queue) == []


def test_task_monitor_cleanup_applies_task_log_deletes_before_reserved_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    observed_task_log_rows_at_reserved_probe: list[list[dict[str, Any]]] = []
    real_scan = cleanup_mod.scan_queue_window

    def scan_with_reserved_observation(*args: object, **kwargs: object) -> object:
        queue_name = args[1]
        if queue_name == reserved_queue:
            observed_task_log_rows_at_reserved_probe.append(
                [
                    json.loads(body)
                    for body, _message_id in _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)
                ]
            )
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(
        "weft.core.monitor.cleanup.scan_queue_window",
        scan_with_reserved_observation,
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(
            batch_size=10,
            task_log_min_age_seconds=1.0,
            reserved_cleanup_enabled=True,
        ),
        apply=True,
        now_ns=_now_after(terminal_id, 2.0),
    )

    assert result.success
    assert result.deleted == 3
    assert observed_task_log_rows_at_reserved_probe == [[]]
    assert _read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE) == []


def test_task_monitor_cleanup_skips_reserved_probe_when_budget_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    real_scan = cleanup_mod.scan_queue_window

    def fail_reserved_scan(*args: object, **kwargs: object) -> object:
        queue_name = args[1]
        if queue_name == reserved_queue:
            raise AssertionError("reserved probe should wait for cleanup budget")
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(
        "weft.core.monitor.cleanup.scan_queue_window",
        fail_reserved_scan,
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(
            batch_size=2,
            task_log_scan_limit=10,
            task_log_min_age_seconds=1.0,
            reserved_cleanup_enabled=True,
        ),
        apply=True,
        now_ns=_now_after(terminal_id, 2.0),
    )

    assert result.success
    assert result.deleted == 2
    assert _read_rows(ctx, reserved_queue) != []


def test_task_monitor_cleanup_does_not_probe_reserved_for_successful_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    tid = "1778000000000000001"
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": tid},
    )
    terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": tid, "status": "completed"},
    )
    real_scan = cleanup_mod.scan_queue_window

    def fail_reserved_scan(*args: object, **kwargs: object) -> object:
        queue_name = args[1]
        if isinstance(queue_name, str) and queue_name.endswith(
            f".{QUEUE_RESERVED_SUFFIX}"
        ):
            raise AssertionError("successful completions must not probe reserved")
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(
        "weft.core.monitor.cleanup.scan_queue_window",
        fail_reserved_scan,
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(terminal_id, 2.0),
    )

    assert result.success
    assert result.deleted == 2
    assert all(
        stat.policy != TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME
        for stat in result.policy_stats
    )


def test_task_monitor_cleanup_default_does_not_probe_reserved_for_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    real_scan = cleanup_mod.scan_queue_window

    def fail_reserved_scan(*args: object, **kwargs: object) -> object:
        queue_name = args[1]
        if isinstance(queue_name, str) and queue_name.endswith(
            f".{QUEUE_RESERVED_SUFFIX}"
        ):
            raise AssertionError("default cleanup must not probe reserved queues")
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(
        "weft.core.monitor.cleanup.scan_queue_window",
        fail_reserved_scan,
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(terminal_id, 2.0),
    )

    assert result.success
    assert result.deleted == 2
    assert _read_rows(ctx, reserved_queue) != []
    assert all(
        stat.policy != TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME
        for stat in result.policy_stats
    )


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
    last_id = _write_json(
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
    last_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000002"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=False,
        now_ns=_now_after(last_id, 2.0),
    )

    assert result.success
    assert [candidate.policy for candidate in result.candidates] == [
        TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
        TASK_MONITOR_POLICY_TASK_LOG_RETENTION,
    ]


def test_task_monitor_cleanup_nonterminal_anchor_does_not_block_later_collate(
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
        {"event": "work_started", "tid": "1778000000000000002"},
    )
    last_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "tid": "1778000000000000002"},
    )

    result = run_task_monitor_cleanup(
        ctx,
        TaskMonitorCleanupConfig(batch_size=10, task_log_min_age_seconds=1.0),
        apply=True,
        now_ns=_now_after(last_id, 2.0),
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
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["selected"] == 3
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["deleted"] == 3
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
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["selected"] == 1
    assert stats[TASK_MONITOR_POLICY_TASK_LOG_RETENTION]["deleted"] == 1
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

    config = TaskMonitorCleanupConfig(batch_size=2, task_log_scan_limit=2)
    first = run_task_monitor_cleanup(ctx, config, apply=True)
    second = run_task_monitor_cleanup(ctx, config, apply=True)

    assert first.records_scanned == 2
    assert first.deleted == 2
    assert second.records_scanned == 2
    assert second.deleted == 2
    assert len(_read_rows(ctx, WEFT_GLOBAL_LOG_QUEUE)) == 1


def test_live_task_keeps_tid_mapping_row_through_destructive_monitor_cycle() -> None:
    """End-to-end: a running task's mapping row survives a destructive cleanup.

    Proves the KEYSTONE fix against a real process, not synthetic rows: a
    task that outlives `tid_mapping_min_age_seconds` (overridden via the
    policy's config parameter, never a monkeypatched constant) must still
    have a `weft.state.tid_mappings` row after a destructive
    `run_task_monitor_cleanup(apply=True)` pass, because its own liveness
    probe finds a live host process.
    """
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        release_file = None
        task = None
        try:
            client = WeftClient(path=harness.root)
            release_file = harness.root / "release-tid-mapping-e2e"
            task = client.submit(
                {
                    "name": "tid-mapping-e2e-wait-for-file",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.tasks.sample_targets:wait_for_file",
                        "args": [str(release_file)],
                        "keyword_args": {"timeout": 30.0},
                    },
                },
            )
            harness.register_tid(task.tid)

            deadline = time.monotonic() + 15.0
            mapping_rows: list[str] = []
            while time.monotonic() < deadline:
                snapshot = task.snapshot()
                if snapshot is not None and snapshot.status == "running":
                    mapping_rows = [
                        entry.message
                        for entry in peek_queue(
                            client.context,
                            WEFT_TID_MAPPINGS_QUEUE,
                            all_messages=True,
                        )
                        if json.loads(entry.message).get("full") == task.tid
                    ]
                    if mapping_rows:
                        break
                time.sleep(0.05)
            assert mapping_rows, "task never reported a tid mapping row while running"

            # Destructive cleanup pass with min-age forced far below the
            # task's actual (short) age, so the only thing keeping the row
            # alive is the liveness probe, not min-age headroom.
            result = run_task_monitor_cleanup(
                client.context,
                TaskMonitorCleanupConfig(batch_size=50, tid_mapping_min_age_seconds=0.0),
                apply=True,
                exclude_tids=(),
            )
            assert result.success

            remaining = [
                entry.message
                for entry in peek_queue(
                    client.context,
                    WEFT_TID_MAPPINGS_QUEUE,
                    all_messages=True,
                )
                if json.loads(entry.message).get("full") == task.tid
            ]
            assert remaining, (
                "live task's tid mapping row was deleted by a destructive "
                "monitor cleanup cycle"
            )

            snapshot = task.snapshot()
            assert snapshot is not None
            assert snapshot.status == "running"
        finally:
            if release_file is not None:
                release_file.write_text("go", encoding="utf-8")
            if task is not None:
                harness.wait_for_completion(task.tid, timeout=30.0)
