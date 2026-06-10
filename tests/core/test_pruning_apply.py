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

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.context import build_context
from weft.core.monitor.store import MonitorRawMessageRef
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.helpers import iter_queue_entries

pytestmark = [pytest.mark.shared]

_QUEUE = "weft.log.tasks"
_TID = "1780000000000000000"


def _seed_rows(ctx, count: int) -> list[int]:
    queue = ctx.queue(_QUEUE, persistent=False)
    try:
        for index in range(count):
            queue.write(json.dumps({"event": "row", "index": index, "tid": _TID}))
        message_ids = [message_id for _body, message_id in iter_queue_entries(queue)]
    finally:
        queue.close()
    return message_ids


def _remaining_rows(ctx) -> list[tuple[str, int]]:
    queue = ctx.queue(_QUEUE, persistent=False)
    try:
        return list(iter_queue_entries(queue))
    finally:
        queue.close()


@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize("exact_status", [True, False])
def test_exact_id_apply_deletes_present_rows(
    tmp_path, count: int, exact_status: bool
) -> None:
    """Present rows must be physically deleted and reported deleted.

    ``exact_status=False`` with ``reconcile_missing=True`` is the exact call
    shape of `_delete_monitor_store_task_log_rows` and
    `_repair_raw_deleted_task_message_refs`; ``exact_status=True`` is the
    per-row shape. Both must leave the queue empty.
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
    tmp_path,
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


def test_exact_id_apply_reports_missing_rows_without_reconcile(tmp_path) -> None:
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
