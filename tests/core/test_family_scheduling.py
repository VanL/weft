"""Finite branch scheduling contracts [MA-1.8], [MF-5].

The reducer boundary proves order and frontier custody without broker/process
fakes. Manager integration separately owns reservation and liveness proof.
"""

from __future__ import annotations

from typing import Any, NoReturn

import pytest

import weft.core.family_scheduling as scheduling_mod
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


def heap_ids(index: scheduling_mod._PendingIndex) -> list[int]:
    """Inspect retained storage: this regression's contract is physical memory."""
    nodes = [index.newest] if index.newest is not None else []
    result = []
    while nodes:
        node = nodes.pop()
        result.append(-node.key)
        if node.left is not None:
            nodes.append(node.left)
        if node.right is not None:
            nodes.append(node.right)
    return result


def assert_pending_storage_bounded(scheduler: FamilyScheduler) -> None:
    for index in scheduler._parents.values():
        retained = heap_ids(index)
        assert len(retained) == index.physical_count
        assert len(retained) <= 2 * index.count, (retained, index.count)


def test_sibling_churn_bounds_history_and_preserves_fifo_then_lifo() -> None:
    """A live tip borrows oldest siblings without retaining every selected ID."""
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.recover("150", "100", ("100",))
    waiting = {200, 201}
    for request_id in waiting:
        scheduler.add_pending(pending(request_id, "100", ("100",)))

    for next_id in range(202, 402):
        # With no children at the live tip, the independent policy is FIFO.
        request = selected(scheduler)
        assert request.request_id == min(waiting)
        waiting.remove(request.request_id)
        scheduler.remove_pending(request.request_id)
        assert_pending_storage_bounded(scheduler)
        scheduler.launched(request)
        scheduler.retire(str(request.request_id))
        scheduler.add_pending(pending(next_id, "100", ("100",)))
        waiting.add(next_id)
        assert_pending_storage_bounded(scheduler)

    scheduler.retire("150")
    while waiting:
        # Once root becomes the tip, its remaining children descend LIFO.
        request = selected(scheduler)
        assert request.request_id == max(waiting)
        waiting.remove(request.request_id)
        scheduler.remove_pending(request.request_id)
        assert_pending_storage_bounded(scheduler)
    assert scheduler.select() is None
    scheduler.add_pending(pending(500, "100", ("100",)))
    assert selected(scheduler).request_id == 500
    assert_pending_storage_bounded(scheduler)


def test_reclamation_keeps_only_current_bucket_owners_after_late_recovery() -> None:
    """Re-added IDs can be globally live but belong to a newly recovered parent."""
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    for request_id in (200, 201, 202):
        scheduler.add_pending(pending(request_id, "100", ("100", "150")))
    scheduler.add_pending(pending(999, "100", ("100",)))
    scheduler.remove_pending(200)
    scheduler.remove_pending(201)
    original = scheduler._parents["100"]
    assert len(heap_ids(original)) == 2 * original.count

    scheduler.recover("150", "100", ("100",))
    for request_id in (200, 201):
        scheduler.add_pending(pending(request_id, "100", ("100", "150")))
    scheduler.remove_pending(202)
    assert original.count == 1
    assert heap_ids(original) == [999]
    assert set(heap_ids(scheduler._parents["150"])) == {200, 201}
    assert selected(scheduler).request_id == 201
    assert_pending_storage_bounded(scheduler)

    old_handle = scheduler._request_indexes[200]
    scheduler.retire("150")
    assert old_handle.owner() is scheduler._parents["100"]
    assert selected(scheduler).request_id == 999
    assert_pending_storage_bounded(scheduler)


def test_candidate_discards_readded_id_owned_by_another_bucket() -> None:
    """Stale-root pruning needs ownership even below the compaction threshold."""
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    for request_id in (110, 120, 200, 201, 202):
        scheduler.add_pending(pending(request_id, "100", ("100", "150")))
    scheduler.remove_pending(201)
    original = scheduler._parents["100"]
    scheduler.recover("150", "100", ("100",))
    scheduler.add_pending(pending(201, "100", ("100", "150")))
    scheduler.remove_pending(202)
    assert len(heap_ids(original)) <= 2 * original.count
    assert heap_ids(original)[0] == 201

    # The bucket primitive is inspected because another live branch owns 201;
    # global selection alone could choose that other branch and mask this bug.
    candidate = original.candidate(scheduler._pending, scheduler._request_indexes)
    assert candidate is not None and candidate.request_id == 200
    assert 201 not in heap_ids(original)
    assert_pending_storage_bounded(scheduler)


def test_reclamation_deduplicates_same_id_refill_in_one_bucket() -> None:
    """A removed/re-added ID contributes one live node when history is rebuilt."""
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    for request_id in (200, 201, 202, 999):
        scheduler.add_pending(pending(request_id, "100", ("100",)))
    scheduler.remove_pending(200)
    scheduler.remove_pending(201)
    for request_id in (200, 201):
        scheduler.add_pending(pending(request_id, "100", ("100",)))
    scheduler.remove_pending(202)
    scheduler.remove_pending(201)
    index = scheduler._parents["100"]
    assert sorted(heap_ids(index)) == [200, 999]
    assert index.count == 2
    assert selected(scheduler).request_id == 999
    assert_pending_storage_bounded(scheduler)


def test_meld_preserves_stale_counts_until_local_reclamation_and_root_pops() -> None:
    """Contraction sums physical/live counts without scanning either bucket."""
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.recover("150", "100", ("100",))
    for request_id in (110, 120, 130, 140):
        scheduler.add_pending(pending(request_id, "100", ("100",)))
    for request_id in (200, 201, 202):
        scheduler.add_pending(pending(request_id, "100", ("100", "150")))
    scheduler.remove_pending(110)
    scheduler.remove_pending(200)
    source = scheduler._parents["150"]
    scheduler.retire("150")
    bucket = scheduler._parents["100"]
    assert bucket.count == 5 and bucket.physical_count == 7
    assert source.count == source.physical_count == 0
    assert source.owner() is bucket
    scheduler.remove_pending(120)
    scheduler.remove_pending(130)
    assert sorted(heap_ids(bucket)) == [140, 201, 202]
    assert_pending_storage_bounded(scheduler)

    scheduler.remove_pending(201)
    assert selected(scheduler).request_id == 202
    scheduler.remove_pending(202)
    assert selected(scheduler).request_id == 140
    assert_pending_storage_bounded(scheduler)
    scheduler.remove_pending(140)
    assert bucket.count == bucket.physical_count == 0
    assert bucket.newest is None


class _LookupOnlyDict(dict[int, Any]):
    """Keep real mappings while rejecting scans of unrelated pending work."""

    def __init__(self, source: dict[int, Any]) -> None:
        super().__init__(source)
        self.probes: list[object] = []

    def __contains__(self, key: object) -> bool:
        self.probes.append(key)
        return super().__contains__(key)

    def get(self, key: int, default: Any = None) -> Any:
        self.probes.append(key)
        return super().get(key, default)

    def __iter__(self) -> NoReturn:
        raise AssertionError("reclamation scanned the global mapping")

    def keys(self) -> NoReturn:
        raise AssertionError("reclamation scanned global keys")

    def values(self) -> NoReturn:
        raise AssertionError("reclamation scanned global values")

    def items(self) -> NoReturn:
        raise AssertionError("reclamation scanned global items")


@pytest.mark.parametrize("depth", [1, 8, 64])
@pytest.mark.parametrize("unrelated_count", [0, 1000])
def test_contracted_reclamation_is_local_and_compresses_owner_handles(
    monkeypatch: pytest.MonkeyPatch, depth: int, unrelated_count: int
) -> None:
    """Cheap unequal meld retains old handles; rebuilding resolves only its IDs."""
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    ancestry: tuple[str, ...] = ("100",)
    chain = [str(150 + offset) for offset in range(depth)]
    for tid in chain:
        scheduler.recover(tid, "100", ancestry, defer=True)
        ancestry = (*ancestry, tid)
    scheduler.finish_recovery()
    for request_id in (300, 301, 302, 303):
        scheduler.add_pending(pending(request_id, "100", ("100",)))
    scheduler.add_pending(pending(999, "100", ancestry))
    original_handle = scheduler._request_indexes[999]
    for offset in range(unrelated_count):
        scheduler.add_pending(pending(9_000_000 + offset, "9000000"))

    for tid in reversed(chain):
        source = scheduler._parents[tid]
        scheduler.retire(tid)
        assert source.count == source.physical_count == 0
        assert source.newest is None
        assert_pending_storage_bounded(scheduler)

    bucket = scheduler._parents["100"]
    physical_before = len(heap_ids(bucket))
    assert bucket.count == physical_before == 5
    pending_view = _LookupOnlyDict(scheduler._pending)
    index_view = _LookupOnlyDict(scheduler._request_indexes)
    scheduler._pending = pending_view
    scheduler._request_indexes = index_view
    owner_steps: list[int] = []
    original_owner = scheduling_mod._PendingIndex.owner

    def counted_owner(
        index: scheduling_mod._PendingIndex,
    ) -> scheduling_mod._PendingIndex:
        cursor = index
        steps = 0
        while cursor.parent is not None:
            steps += 1
            cursor = cursor.parent
        owner_steps.append(steps)
        return original_owner(index)

    monkeypatch.setattr(scheduling_mod._PendingIndex, "owner", counted_owner)
    for request_id in (300, 301, 302):
        scheduler.remove_pending(request_id)

    assert sorted(heap_ids(bucket)) == [303, 999]
    assert bucket.count == bucket.physical_count == 2
    # Each node is visited; each retained ID resolves its owner at most once.
    assert len(pending_view.probes) == physical_before
    assert set(pending_view.probes) == {300, 301, 302, 303, 999}
    assert len(index_view.probes) == 2
    assert set(index_view.probes) == {303, 999}
    assert sum(owner_steps) == depth
    assert original_handle.owner() is bucket
    assert owner_steps[-1] <= 1  # The first lookup compressed the retained handle.
    scheduler.remove_pending(999)
    assert selected(scheduler).request_id == 303
    assert_pending_storage_bounded(scheduler)
