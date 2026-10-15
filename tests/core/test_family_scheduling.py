"""Finite branch scheduling contracts [MA-1.8], [MF-5].

The reducer boundary proves order and frontier custody without broker/process
fakes. Manager integration separately owns reservation and liveness proof.
"""

from __future__ import annotations

import pytest

from weft.core.family_scheduling import FamilyScheduler, PendingRequest

pytestmark = pytest.mark.shared


def selected(scheduler: FamilyScheduler) -> PendingRequest:
    result = scheduler.select()
    assert result is not None
    return result


def pending(
    request_id: int, root: str, ancestors: tuple[str, ...] = ()
) -> PendingRequest:
    return PendingRequest(
        request_id, root, ancestors[-1] if ancestors else None, ancestors
    )


def test_live_tip_retains_lineage_then_descends_lifo() -> None:
    scheduler = FamilyScheduler()
    root = pending(100, "100")
    scheduler.add_pending(root)
    scheduler.add_pending(pending(200, "200"))
    assert scheduler.select() == root
    scheduler.remove_pending(100)
    scheduler.launched(root)
    assert selected(scheduler).request_id == 200
    scheduler.add_pending(pending(101, "100", ("100",)))
    scheduler.add_pending(pending(102, "100", ("100",)))
    assert selected(scheduler).request_id == 102
    scheduler.remove_pending(102)
    scheduler.launched(pending(102, "100", ("100",)))
    assert selected(scheduler).request_id == 200
    scheduler.retire("102")
    assert selected(scheduler).request_id == 101


def test_independent_fifo_precedes_sibling_borrowing() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.add_pending(pending(101, "100", ("100",)))
    scheduler.add_pending(pending(300, "300"))
    scheduler.add_pending(pending(200, "200"))
    assert selected(scheduler).request_id == 101
    scheduler.remove_pending(101)
    scheduler.launched(pending(101, "100", ("100",)))
    assert selected(scheduler).request_id == 200


def test_recovery_contracts_missing_intermediate_ancestor() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.recover("101", "100", ("100",))
    scheduler.add_pending(pending(103, "100", ("100", "101", "102")))
    assert selected(scheduler).request_id == 103
    scheduler.remove_pending(103)
    scheduler.launched(pending(103, "100", ("100", "101", "102")))
    assert scheduler.frontiers == (("100", "101", "103"),)


def test_recovery_order_preserves_divergent_branches() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("103", "100", ("100", "101"))
    scheduler.recover("104", "100", ("100", "102"))
    scheduler.recover("100", "100", ())
    assert len(scheduler.frontiers) == 2
    scheduler.add_pending(pending(200, "200"))
    assert selected(scheduler).request_id == 200
    scheduler.retire("103")
    scheduler.retire("104")
    assert len(scheduler.frontiers) == 1


def test_background_startup_releases_finite_lineage() -> None:
    scheduler = FamilyScheduler()
    root = pending(100, "100")
    scheduler.launched(root, "background")
    scheduler.add_pending(pending(200, "200"))
    assert selected(scheduler).request_id == 200
    scheduler.release_background("100")
    assert "100" not in scheduler.resident_tids
    assert selected(scheduler).request_id == 200


def test_frontiers_receive_round_robin_child_turns() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.recover("200", "200", ())
    scheduler.add_pending(pending(101, "100", ("100",)))
    scheduler.add_pending(pending(201, "200", ("200",)))
    assert {selected(scheduler).request_id, selected(scheduler).request_id} == {
        101,
        201,
    }


def test_sibling_sharing_keeps_shared_ancestor() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    for request_id in (101, 102):
        scheduler.add_pending(pending(request_id, "100", ("100",)))
    newest = selected(scheduler)
    scheduler.remove_pending(newest.request_id)
    scheduler.launched(newest)
    oldest = selected(scheduler)
    assert oldest.request_id == 101
    scheduler.remove_pending(oldest.request_id)
    scheduler.launched(oldest)
    assert set(scheduler.frontiers) == {("100", "102"), ("100", "101")}
    scheduler.retire("101")
    assert scheduler.frontiers == (("100", "102"),)
    scheduler.add_pending(pending(103, "100", ("100", "102")))
    assert selected(scheduler).request_id == 103


def test_sibling_fanout_starts_more_than_five_live_paths() -> None:
    """Known siblings share work without requiring a family capacity permit."""
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    for request_id in range(101, 109):
        scheduler.add_pending(pending(request_id, "100", ("100",)))
    launched = []
    for _ in range(8):
        child = selected(scheduler)
        scheduler.remove_pending(child.request_id)
        scheduler.launched(child)
        launched.append(child.request_id)
    assert launched == [108, *range(101, 108)]
    assert len(scheduler.frontiers) == 8
    assert scheduler.resident_tids == frozenset(str(tid) for tid in range(100, 109))


def test_recovered_paths_prioritize_tip_children_beyond_five_branches() -> None:
    """Recovered live ancestry preserves child-first round-robin selection."""
    scheduler = FamilyScheduler()
    roots = [str(index * 100) for index in range(1, 9)]
    for root in roots:
        scheduler.recover(root, root, ())
        scheduler.add_pending(pending(int(root) + 1, root, (root,)))
    scheduler.add_pending(pending(50, "50"))
    children = []
    for _ in roots:
        child = selected(scheduler)
        scheduler.remove_pending(child.request_id)
        scheduler.launched(child)
        children.append(child.request_id)
    assert set(children) == {int(root) + 1 for root in roots}
    assert selected(scheduler).request_id == 50


def test_exited_root_pending_family_reenters_before_newer_root() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.add_pending(pending(200, "200"))
    scheduler.retire("100")
    # A delayed request still carries its original family's FIFO position.
    scheduler.add_pending(pending(101, "100", ("100",)))
    assert selected(scheduler).request_id == 101


def test_transparent_submitter_uses_finite_ancestry_and_original_bucket() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.recover("102", "100", ("100",), "background")
    scheduler.recover("103", "100", ("100",), "internal")
    child = PendingRequest(104, "100", "103", ("100",))
    scheduler.add_pending(child)
    assert child.queue_name == "weft.state.labels.100.pending.103"
    assert selected(scheduler) == child
    scheduler.remove_pending(104)
    scheduler.launched(child)
    assert scheduler.frontiers == (("100", "104"),)


def test_parent_exit_contracts_pending_bucket_and_exact_removal_survives_merge() -> (
    None
):
    scheduler = FamilyScheduler()
    for tid, ancestors in (("100", ()), ("101", ("100",)), ("102", ("100", "101"))):
        scheduler.recover(tid, "100", ancestors)
    scheduler.add_pending(pending(103, "100", ("100", "101", "102")))
    scheduler.add_pending(pending(104, "100", ("100", "101")))
    scheduler.retire("102")
    child = selected(scheduler)
    assert child.request_id == 104
    scheduler.remove_pending(104)
    scheduler.launched(child)
    scheduler.retire("104")
    contracted = selected(scheduler)
    assert contracted.request_id == 103
    assert contracted.queue_name == "weft.state.labels.100.pending.102"
    scheduler.remove_pending(103)
    scheduler.launched(contracted)
    scheduler.retire("103")
    assert scheduler.select() is None


def test_live_borrowed_frontiers_with_drained_pending_family_wait_for_children() -> (
    None
):
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.add_pending(pending(101, "100", ("100",)))
    scheduler.add_pending(pending(102, "100", ("100",)))
    for _ in range(2):
        child = selected(scheduler)
        scheduler.remove_pending(child.request_id)
        scheduler.launched(child)
    assert len(scheduler.frontiers) == 2
    assert scheduler.select() is None
