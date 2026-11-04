"""Exact-ID prune apply repro tests.

These tests pin the contract that `apply_exact_prune_candidates` reports
`deleted=True` only when the broker rows are physically gone, across both
backends, both delete shapes (single `queue.delete(message_id=...)` via
``exact_status=True`` vs `delete_many` batch), and the monitor's production
call shape (``reconcile_missing=True``).

Plan: docs/plans/2026-06-10-self-healing-runtime-maintenance-plan.md (A1)
Spec: docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.context import WeftContext, build_context
from weft.core.monitor.store import MonitorRawMessageRef
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.core.pruning.policies import malformed_row_candidates, older_than_candidates
from weft.core.queue_window import DecodedQueueWindowRow, QueueWindowRow
from weft.helpers import iter_queue_entries

pytestmark = [pytest.mark.shared]

_QUEUE = "weft.log.tasks"
_TID = "1780000000000000000"


def _seed_rows(ctx: WeftContext, count: int) -> list[int]:
    queue = ctx.queue(_QUEUE, persistent=False)
    try:
        for index in range(count):
            queue.write(json.dumps({"event": "row", "index": index, "tid": _TID}))
        message_ids = [message_id for _body, message_id in iter_queue_entries(queue)]
    finally:
        queue.close()
    return message_ids


def _remaining_rows(ctx: WeftContext) -> list[tuple[str, int]]:
    queue = ctx.queue(_QUEUE, persistent=False)
    try:
        return list(iter_queue_entries(queue))
    finally:
        queue.close()


@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize("exact_status", [True, False])
def test_exact_id_apply_deletes_present_rows(
    tmp_path: Path, count: int, exact_status: bool
) -> None:
    """Present rows must be physically deleted and reported deleted.

    ``exact_status=False`` with ``reconcile_missing=True`` is the exact call
    shape of `_delete_monitor_store_task_log_rows`; ``exact_status=True`` is
    the per-row shape. Both must leave the queue empty.
    """
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    message_ids = _seed_rows(ctx, count)
    assert len(message_ids) == count

    candidates = [
        MonitorRawMessageRef(queue=_QUEUE, message_id=message_id, tid=_TID)
        for message_id in message_ids
    ]
    results = apply_exact_prune_candidates(
        ctx,
        candidates,
        apply_result=lambda candidate, deleted, error: (candidate, deleted, error),
        exact_status=exact_status,
        reconcile_missing=not exact_status,
    )

    assert [error for _c, _d, error in results] == [None] * count
    assert [deleted for _c, deleted, _e in results] == [True] * count
    # The oracle: reported success must mean the rows are actually gone.
    assert _remaining_rows(ctx) == []


def test_exact_id_apply_reconcile_verifies_per_id_on_batch_under_deletion(
    tmp_path: Path,
) -> None:
    """Batch under-deletion must fall back to per-ID verification.

    One candidate row is deleted out-of-band after candidate selection, so
    the batch `delete_many` under-deletes (N-1 of N). With
    ``reconcile_missing=True`` the apply layer must NOT vacuously report
    all candidates deleted off the batch call; it must re-verify each
    candidate with a per-ID exact delete. Present rows are then physically
    deleted and the missing row is verified absent, so every result is
    honestly ``deleted=True`` and the queue ends empty — a present row
    reported deleted becomes structurally impossible.

    Plan: docs/plans/2026-06-10-self-healing-runtime-maintenance-plan.md (A3)
    Spec: [OBS.13], [OBS.17]
    """
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    message_ids = _seed_rows(ctx, 3)
    assert len(message_ids) == 3

    # Out-of-band delete AFTER candidate selection: the batch branch will
    # report 2 of 3 deleted, forcing the per-ID verification fallback.
    queue = ctx.queue(_QUEUE, persistent=False)
    try:
        assert queue.delete(message_id=message_ids[1])
    finally:
        queue.close()

    candidates = [
        MonitorRawMessageRef(queue=_QUEUE, message_id=message_id, tid=_TID)
        for message_id in message_ids
    ]
    results = apply_exact_prune_candidates(
        ctx,
        candidates,
        apply_result=lambda candidate, deleted, error: (candidate, deleted, error),
        reconcile_missing=True,
    )

    assert [error for _c, _d, error in results] == [None] * 3
    # Missing row: verified absent (idempotent-complete). Present rows:
    # physically deleted by the per-ID fallback.
    assert [deleted for _c, deleted, _e in results] == [True] * 3
    assert _remaining_rows(ctx) == []


def test_exact_id_apply_reports_missing_rows_without_reconcile(tmp_path: Path) -> None:
    """Absent rows report deleted=False when reconcile_missing is off."""
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    present = _seed_rows(ctx, 1)

    bogus = present[0] + 4096  # valid-shaped hybrid timestamp, not present
    candidates = [MonitorRawMessageRef(queue=_QUEUE, message_id=bogus, tid=_TID)]
    results = apply_exact_prune_candidates(
        ctx,
        candidates,
        apply_result=lambda candidate, deleted, error: (candidate, deleted, error),
    )

    assert [(deleted, error) for _c, deleted, error in results] == [(False, None)]
    assert len(_remaining_rows(ctx)) == 1


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


def _read_rows(ctx: WeftContext, queue_name: str) -> list[tuple[str, int]]:
    queue = ctx.queue(queue_name, persistent=False)
    try:
        return list(iter_queue_entries(queue))
    finally:
        queue.close()


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
