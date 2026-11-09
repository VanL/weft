"""Finite branch scheduling contracts [MA-1.8], [MF-5].

The reducer boundary proves order and frontier custody without broker/process
fakes. Manager integration separately owns reservation and liveness proof.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, NoReturn

import pytest

import weft.core.family_scheduling as scheduling_mod
from weft.core.family_scheduling import (
    FamilyScheduler,
    PendingRequest,
    SchedulingClass,
)

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


def test_readded_requests_keep_current_parent_after_recovery_and_contraction() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    children = [pending(i, "100", ("100", "150")) for i in (200, 201, 202)]
    sibling = pending(999, "100", ("100",))
    for request in (*children, sibling):
        scheduler.add_pending(request)
    assert scheduler.remove_pending(201) == children[1]

    scheduler.recover("150", "100", ("100",))
    scheduler.add_pending(children[1])
    assert scheduler.remove_pending(202) == children[2]
    assert selected(scheduler) == children[1]
    assert_pending_storage_bounded(scheduler)

    scheduler.retire("150")
    assert selected(scheduler) == sibling
    assert scheduler.remove_pending(999) == sibling
    assert selected(scheduler) == children[1]
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


# --- Frontier ring and contracted ancestry (plan 2026-11-03 frontier ring) ---
# Spec: [MA-1.8], [MANAGER.18]. Plan:
# docs/plans/2026-11-03-family-scheduler-frontier-ring-plan.md Task 3.


def _reference_maximal_paths(
    residents: Mapping[str, tuple[str, ...]],
) -> set[tuple[str, ...]]:
    """Return today's maximal live paths for ``residents`` (TID -> ancestry).

    The body is ``FamilyScheduler.finish_recovery`` at ``b7eed9f9``
    (``weft/core/family_scheduling.py`` lines 423-430), copied verbatim except
    that it reads a mapping of ancestries. It changes only if the frontier
    contract changes on purpose.
    """
    paths = [
        [*(item for item in ancestry_tids if item in residents), tid]
        for tid, ancestry_tids in residents.items()
    ]
    maximal: list[list[str]] = []
    for path in sorted(paths, key=len, reverse=True):
        if not any(other[: len(path)] == path for other in maximal):
            maximal.append(path)
    return {tuple(path) for path in maximal}


def _nearest_resident(
    ancestry_tids: tuple[str, ...], residents: Collection[str]
) -> str | None:
    return next((tid for tid in reversed(ancestry_tids) if tid in residents), None)


def _childless(residents: Mapping[str, tuple[str, ...]]) -> list[str]:
    """Residents no other resident names as its nearest live ancestor."""
    parents = {
        _nearest_resident(ancestry, residents) for ancestry in residents.values()
    }
    return [tid for tid in residents if tid not in parents]


@dataclass
class _FamilyModel:
    """The generator's own table: every launched node and its lineage.

    A finite parent appends its own TID to its child's ancestry; a background
    parent passes its ancestry through unchanged (``submission_lineage``).
    """

    scheduler: FamilyScheduler
    rng: random.Random
    nodes: dict[str, tuple[str, tuple[str, ...], SchedulingClass]] = field(
        default_factory=dict
    )
    next_id: int = 1000

    def resident_ancestries(self) -> dict[str, tuple[str, ...]]:
        return {tid: self.nodes[tid][1] for tid in self.scheduler.resident_tids}

    def _new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def _residents(self) -> list[str]:
        return sorted(self.scheduler.resident_tids)

    def _exited_finite(self) -> list[str]:
        live = self.scheduler.resident_tids
        return sorted(
            tid
            for tid, (_, _, cls) in self.nodes.items()
            if cls == "finite" and tid not in live
        )

    def add_root(self) -> None:
        request_id = self._new_id()
        self.scheduler.add_pending(pending(request_id, str(request_id)))

    def add_child(self) -> None:
        if not self.nodes:
            return self.add_root()
        parent = self.rng.choice(sorted(self.nodes))
        root, ancestry, cls = self.nodes[parent]
        lineage = (*ancestry, parent) if cls == "finite" else ancestry
        self.scheduler.add_pending(
            PendingRequest(self._new_id(), root, parent, lineage)
        )

    def launch(self) -> None:
        request = self.scheduler.select()
        if request is None:
            return
        self.scheduler.remove_pending(request.request_id)
        cls: SchedulingClass = "background" if self.rng.random() < 1 / 6 else "finite"
        self.scheduler.launched(request, cls)
        self.nodes[str(request.request_id)] = (
            request.root_tid,
            request.ancestry_tids,
            cls,
        )

    def retire(self) -> None:
        if residents := self._residents():
            self.scheduler.retire(self.rng.choice(residents))

    def release_background(self) -> None:
        if residents := self._residents():
            self.scheduler.release_background(self.rng.choice(residents))

    def recover(self) -> None:
        if exited := self._exited_finite():
            tid = self.rng.choice(exited)
            self.scheduler.recover(tid, self.nodes[tid][0], self.nodes[tid][1])

    def recover_deferred(self) -> None:
        exited = self._exited_finite()
        if not exited:
            return
        for tid in self.rng.sample(exited, min(len(exited), self.rng.randint(1, 3))):
            root, ancestry, _ = self.nodes[tid]
            self.scheduler.recover(tid, root, ancestry, defer=True)
        self.scheduler.finish_recovery()


def _drive_random_family(
    seed: int, steps: int, check: Callable[[_FamilyModel], None]
) -> None:
    """Apply random scheduler operations and ``check`` the model after each.

    Every entry point the Manager uses is exercised: launch, retire (interior
    residents included), background release, non-deferred recovery, and a
    deferred sweep followed by ``finish_recovery``.
    """
    rng = random.Random(seed)
    model = _FamilyModel(FamilyScheduler(), rng)
    operations = (
        (model.add_root, 12),
        (model.add_child, 30),
        (model.launch, 30),
        (model.retire, 18),
        (model.release_background, 4),
        (model.recover, 3),
        (model.recover_deferred, 3),
    )
    actions = [action for action, _ in operations]
    weights = [weight for _, weight in operations]
    for _ in range(steps):
        rng.choices(actions, weights)[0]()
        check(model)


def _assert_public_frontier(model: _FamilyModel) -> None:
    residents = model.resident_ancestries()
    frontiers = model.scheduler.frontiers
    tips = [path[-1] for path in frontiers]
    assert len(tips) == len(set(tips))
    assert set(tips) == set(_childless(residents))
    assert set(frontiers) == _reference_maximal_paths(residents)


@pytest.mark.parametrize("seed", range(200))
def test_frontier_tips_are_exactly_the_childless_residents_over_random_families(
    seed: int,
) -> None:
    """Derived frontiers equal today's maximal live paths on every step."""
    _drive_random_family(seed, 400, _assert_public_frontier)


def test_contracted_stack_keeps_its_rotation_position() -> None:
    scheduler = FamilyScheduler()
    for request in (
        pending(300, "300"),
        pending(301, "300", ("300",)),
        pending(100, "100"),
        pending(101, "100", ("100",)),
        pending(102, "100", ("100", "101")),
        pending(200, "200"),
    ):
        scheduler.launched(request)
    assert scheduler.frontiers == (("300", "301"), ("100", "101", "102"), ("200",))
    scheduler.retire("301")
    assert scheduler.frontiers == (("300",), ("100", "101", "102"), ("200",))


def test_exit_beside_a_live_sibling_drops_the_stack_and_keeps_order() -> None:
    scheduler = FamilyScheduler()
    for request in (
        pending(100, "100"),
        pending(101, "100", ("100",)),
        pending(102, "100", ("100",)),
        pending(200, "200"),
        pending(201, "200", ("200",)),
        pending(202, "200", ("200", "201")),
    ):
        scheduler.launched(request)
    scheduler.retire("101")
    assert scheduler.frontiers == (("100", "102"), ("200", "201", "202"))


def test_interior_exit_leaves_ring_unchanged_and_children_contract_to_ancestor() -> (
    None
):
    scheduler = FamilyScheduler()
    for request in (
        pending(100, "100"),
        pending(101, "100", ("100",)),
        pending(102, "100", ("100", "101")),
        pending(103, "100", ("100", "101")),
        pending(200, "200"),
    ):
        scheduler.launched(request)
    scheduler.retire("101")
    assert scheduler.frontiers == (("100", "102"), ("100", "103"), ("200",))
    assert scheduler._find("101") == "100"
    scheduler.retire("102")
    assert scheduler.frontiers == (("100", "103"), ("200",))
    scheduler.retire("103")
    assert scheduler.frontiers == (("100",), ("200",))


def test_contracted_pointer_lookup_compresses_dead_chain() -> None:
    """Inspect pointers: this test's subject is the compression mechanism."""
    scheduler = FamilyScheduler()
    for root in range(900, 910):
        scheduler.recover(str(root), str(root), ())
    ancestry: tuple[str, ...] = ()
    for request_id in range(100, 105):
        scheduler.launched(pending(request_id, "100", ancestry))
        ancestry = (*ancestry, str(request_id))
    for tid in ("103", "102", "101"):
        scheduler.retire(tid)
    assert scheduler._up["103"] == "102"
    assert scheduler._up["102"] == "101"
    assert scheduler._find("103") == "100"
    assert scheduler._up["103"] == scheduler._up["102"] == "100"


def _expected_tip_candidate(
    tips: list[str], turn: int, waiting: Mapping[str, set[int]]
) -> tuple[int | None, int]:
    """Today's cursor arithmetic: the first tip from ``turn`` with work."""
    for offset in range(len(tips)):
        index = (turn + offset) % len(tips)
        if waiting.get(tips[index]):
            return max(waiting[tips[index]]), (index + 1) % len(tips)
    return None, turn


@pytest.mark.parametrize("seed", range(50))
def test_select_rotation_matches_cursor_arithmetic_while_stacks_are_fixed(
    seed: int,
) -> None:
    rng = random.Random(seed)
    scheduler = FamilyScheduler()
    roots = [str(index * 100) for index in range(1, 9)]
    waiting: dict[str, set[int]] = {root: set() for root in roots}
    next_id = 1000
    for root in roots:
        scheduler.recover(root, root, ())
    for root in roots:
        for _ in range(rng.randint(0, 3)):
            next_id += 1
            scheduler.add_pending(pending(next_id, root, (root,)))
            waiting[root].add(next_id)
    tips = [path[-1] for path in scheduler.frontiers]
    turn = 0
    selections = 0
    while True:
        if selections < 40 and rng.random() < 0.5:
            # New work under a tip that missed earlier makes rotation visible.
            root = rng.choice(roots)
            next_id += 1
            scheduler.add_pending(pending(next_id, root, (root,)))
            waiting[root].add(next_id)
        expected, turn = _expected_tip_candidate(tips, turn, waiting)
        actual = scheduler.select()
        if expected is None:
            assert actual is None
            break
        assert actual is not None and actual.request_id == expected
        scheduler.remove_pending(expected)
        waiting[actual.root_tid].discard(expected)
        selections += 1
    _assert_sibling_pass_matches_cursor_arithmetic()


def _assert_sibling_pass_matches_cursor_arithmetic() -> None:
    """With no tip work and no independent family, siblings rotate by cursor."""
    scheduler = FamilyScheduler()
    for tid, ancestry in (
        ("100", ()),
        ("101", ("100",)),
        ("200", ()),
        ("201", ("200",)),
    ):
        scheduler.recover(tid, tid if not ancestry else ancestry[0], ancestry)
    oldest: dict[str, list[int]] = {"100": [111, 112, 113], "200": [211, 212, 213]}
    family_of = {"101": "100", "201": "200"}
    for root, request_ids in oldest.items():
        for request_id in request_ids:
            scheduler.add_pending(pending(request_id, root, (root,)))
    tips = [path[-1] for path in scheduler.frontiers]
    assert set(tips) == {"101", "201"}
    turn = 0
    while any(oldest.values()):
        expected = None
        for offset in range(len(tips)):
            index = (turn + offset) % len(tips)
            family = oldest[family_of[tips[index]]]
            if family:
                expected = family.pop(0)
                turn = (index + 1) % len(tips)
                break
        actual = selected(scheduler)
        assert actual.request_id == expected
        scheduler.remove_pending(actual.request_id)
    assert scheduler.select() is None


def test_recovery_rebuilds_the_same_frontiers_from_residents() -> None:
    first = FamilyScheduler()
    chain: tuple[str, ...] = ()
    for request_id in range(100, 105):
        first.launched(pending(request_id, "100", chain))
        chain = (*chain, str(request_id))
    first.launched(pending(200, "200"))
    for request_id in (201, 202, 203):
        first.launched(pending(request_id, "200", ("200",)))
    first.retire("102")
    first.retire("200")
    chain_ancestry = ("100", "101", "102", "103")
    ancestries = {str(tid): chain_ancestry[: tid - 100] for tid in range(100, 105)}
    ancestries |= {"200": (), "201": ("200",), "202": ("200",), "203": ("200",)}
    second = FamilyScheduler()
    order = sorted(first.resident_tids)
    for tid in order:
        root = ancestries[tid][0] if ancestries[tid] else tid
        second.recover(tid, root, ancestries[tid], defer=True)
    second.finish_recovery()
    assert set(second.frontiers) == set(first.frontiers)
    assert len(second.frontiers) == len(first.frontiers) == 4
    residents = {tid: ancestries[tid] for tid in order}
    assert [path[-1] for path in second.frontiers] == _childless(residents)


def _assert_pointer_mechanics(model: _FamilyModel) -> None:
    scheduler = model.scheduler
    residents = model.resident_ancestries()
    assert len(scheduler._up) <= 2 * len(scheduler.resident_tids)
    for tid, ancestry in residents.items():
        assert scheduler._live_parent(tid) == _nearest_resident(ancestry, residents)
    ring_tip = scheduler._ring_tip
    assert (scheduler._turn is None) == (not ring_tip)
    assert set(scheduler._ring_next) == set(scheduler._ring_prev) == set(ring_tip)
    for stack_id in ring_tip:
        assert scheduler._ring_prev[scheduler._ring_next[stack_id]] == stack_id
    if scheduler._turn is not None:
        visited = [scheduler._turn]
        while (following := scheduler._ring_next[visited[-1]]) != scheduler._turn:
            visited.append(following)
        assert sorted(visited) == sorted(ring_tip)


@pytest.mark.parametrize("seed", range(20))
def test_contracted_pointers_stay_bounded_by_live_residents(seed: int) -> None:
    """Inspect pointers and ring links: their bounds are the subject here."""
    _drive_random_family(seed, 400, _assert_pointer_mechanics)


def test_new_stack_joins_rotation_after_existing_stacks() -> None:
    scheduler = FamilyScheduler()
    for root in ("100", "200"):
        scheduler.recover(root, root, ())
        for offset in (1, 2, 3):
            scheduler.add_pending(pending(int(root) + offset, root, (root,)))
    assert selected(scheduler).request_id == 103
    scheduler.launched(pending(300, "300"))
    for request_id in (301, 302, 303):
        scheduler.add_pending(pending(request_id, "300", ("300",)))
    sequence = []
    for _ in range(3):
        request = selected(scheduler)
        scheduler.remove_pending(request.request_id)
        sequence.append(request.request_id)
    assert sequence == [203, 103, 303]


@dataclass
class _CursorModel:
    """Today's cursor list edited by the [MA-1.8] rotation rules.

    A new stack is inserted before the cursor; a deleted stack is removed and
    a deleted cursor passes to its successor; an extension or contraction
    replaces its tip in place.
    """

    scheduler: FamilyScheduler
    rng: random.Random
    tips: list[str] = field(default_factory=list)
    cursor: int = 0
    ancestry: dict[str, tuple[str, ...]] = field(default_factory=dict)
    parent: dict[str, str | None] = field(default_factory=dict)
    children: dict[str, int] = field(default_factory=dict)
    waiting: dict[str, set[int]] = field(default_factory=dict)
    next_id: int = 1000

    def _new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def rotation(self) -> list[str]:
        return self.tips[self.cursor :] + self.tips[: self.cursor]

    def has_tip_work(self) -> bool:
        return any(self.waiting.get(tip) for tip in self.tips)

    def add_child(self, tip: str) -> None:
        request_id = self._new_id()
        root = (self.ancestry[tip] or (tip,))[0]
        lineage = (*self.ancestry[tip], tip)
        self.scheduler.add_pending(pending(request_id, root, lineage))
        self.waiting.setdefault(tip, set()).add(request_id)

    def add_child_anywhere(self) -> None:
        self.add_child(self.rng.choice(self.tips))

    def launch_root(self) -> str:
        tid = str(self._new_id())
        self.scheduler.launched(pending(int(tid), tid))
        self.ancestry[tid], self.parent[tid], self.children[tid] = (), None, 0
        if self.tips:
            self.tips.insert(self.cursor, tid)
            self.cursor += 1
        else:
            self.tips, self.cursor = [tid], 0
        return tid

    def select_and_check(self) -> tuple[PendingRequest, str]:
        expected, self.cursor = _expected_tip_candidate(
            self.tips, self.cursor, self.waiting
        )
        request = selected(self.scheduler)
        assert request.request_id == expected
        self.scheduler.remove_pending(request.request_id)
        holder = next(t for t in reversed(request.ancestry_tids) if t in self.children)
        self.waiting[holder].discard(request.request_id)
        return request, holder

    def extend(self, request: PendingRequest, holder: str) -> None:
        tid = str(request.request_id)
        self.scheduler.launched(request)
        self.ancestry[tid], self.parent[tid], self.children[tid] = (
            request.ancestry_tids,
            holder,
            0,
        )
        self.children[holder] += 1
        self.tips[self.tips.index(holder)] = tid
        if self.rng.random() < 0.5:
            self.add_child(tid)

    def retire_tip(self) -> None:
        tid = self.rng.choice(self.tips)
        index = self.tips.index(tid)
        self.scheduler.retire(tid)
        up = self.parent[tid]
        del self.children[tid]
        moved = self.waiting.pop(tid, set())
        if up is not None:
            self.waiting.setdefault(up, set()).update(moved)
            self.children[up] -= 1
            if not self.children[up]:
                self.tips[index] = up
                return
        self.tips.pop(index)
        if index < self.cursor:
            self.cursor -= 1
        if self.cursor == len(self.tips):
            self.cursor = 0


@pytest.mark.parametrize("seed", range(50))
def test_select_rotation_matches_cursor_model_across_stack_edits(seed: int) -> None:
    """A cursor list edited by the [MA-1.8] rotation rules predicts each choice."""
    rng = random.Random(seed)
    model = _CursorModel(FamilyScheduler(), rng)
    for _ in range(6):
        root = model.launch_root()
        for _ in range(rng.randint(0, 2)):
            model.add_child(root)
    for _ in range(120):
        if not model.has_tip_work():
            if model.tips:
                model.add_child_anywhere()
            else:
                model.launch_root()
        else:
            request, holder = model.select_and_check()
            action = rng.randrange(4)
            if action == 0:
                model.extend(request, holder)
            elif action == 1:
                model.add_child(model.launch_root())
            elif action == 2:
                model.retire_tip()
            else:
                model.add_child_anywhere()
        assert [path[-1] for path in model.scheduler.frontiers] == model.rotation()


def test_adoption_after_child_launch_rebuilds_the_parent_path() -> None:
    """Inspect the ring: the handoff case of the leaf-launch assumption."""
    scheduler = FamilyScheduler()
    scheduler.launched(PendingRequest(101, "100", "100", ("100",)))
    assert scheduler.frontiers == (("101",),)
    assert len(scheduler._ring_tip) == 1
    assert list(scheduler._ring_tip.values()) == ["101"]
    assert scheduler._live_parent("101") is None
    scheduler.recover("100", "100", ())
    assert scheduler.frontiers == (("100", "101"),)
    assert "100" not in scheduler._tip_stack
    assert len(scheduler._ring_tip) == 1
    assert scheduler._live_parent("101") == "100"


def test_retire_of_a_deferred_resident_merges_its_bucket_and_leaves_the_ring() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.recover("101", "100", ("100",), defer=True)
    scheduler.add_pending(pending(150, "100", ("100",)))
    scheduler.add_pending(pending(160, "100", ("100", "101")))
    scheduler.retire("101")
    assert scheduler.frontiers == (("100",),)
    scheduler.finish_recovery()
    assert scheduler.frontiers == (("100",),)
    assert selected(scheduler).request_id == 160

    # A former resident's retained pointer is followed past while deferred.
    scheduler = FamilyScheduler()
    for request in (
        pending(100, "100"),
        pending(101, "100", ("100",)),
        pending(102, "100", ("100", "101")),
    ):
        scheduler.launched(request)
    scheduler.retire("101")
    scheduler.recover("101", "100", ("100",), defer=True)
    scheduler.retire("102")
    assert scheduler.frontiers == (("100",),)
    scheduler.finish_recovery()
    assert scheduler.frontiers == (("100", "101"),)

    # The exiting resident's bucket merges into the deferred resident.
    scheduler = FamilyScheduler()
    for request in (
        pending(100, "100"),
        pending(101, "100", ("100",)),
        pending(102, "100", ("100", "101")),
    ):
        scheduler.launched(request)
    scheduler.retire("101")
    scheduler.recover("101", "100", ("100",), defer=True)
    scheduler.add_pending(pending(150, "100", ("100", "101")))
    scheduler.add_pending(pending(160, "100", ("100", "101", "102")))
    scheduler.retire("102")
    scheduler.finish_recovery()
    assert scheduler.frontiers == (("100", "101"),)
    assert selected(scheduler).request_id == 160


def test_launch_under_a_deferred_parent_is_repaired_by_finish_recovery() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", (), defer=True)
    scheduler.launched(PendingRequest(101, "100", "100", ("100",)))
    scheduler.finish_recovery()
    assert scheduler.frontiers == (("100", "101"),)
    scheduler.retire("101")
    assert scheduler.frontiers == (("100",),)


def test_launch_under_a_deferred_parent_with_a_retained_pointer_attaches_to_the_registered_ancestor() -> (
    None
):
    """Inspect the pointer: ``frontiers`` filters by residents and cannot show it."""
    scheduler = FamilyScheduler()
    for request in (
        pending(100, "100"),
        pending(101, "100", ("100",)),
        pending(102, "100", ("100", "101")),
    ):
        scheduler.launched(request)
    scheduler.retire("101")
    scheduler.recover("101", "100", ("100",), defer=True)
    scheduler.launched(PendingRequest(103, "100", "101", ("100", "101")))
    assert scheduler._live_parent("103") == "100"
    scheduler.finish_recovery()
    assert scheduler.frontiers == (("100", "101", "102"), ("100", "101", "103"))
    assert scheduler._live_parent("103") == "101"


def test_ambiguous_ancestor_holds_only_dependent_pending_requests() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.recover("101", "100", ("100",))
    dependent = pending(102, "100", ("100", "101"))
    sibling = pending(103, "100", ("100",))
    independent = pending(200, "200")
    for request in (dependent, sibling, independent):
        scheduler.add_pending(request)
    scheduler.hold("101")
    assert scheduler.pending(102) == dependent
    assert selected(scheduler) == independent
    scheduler.remove_pending(200)
    assert selected(scheduler) == sibling
    scheduler.hold("100")
    assert scheduler.select() is None
    scheduler.release("101")
    assert scheduler.select() is None
    assert scheduler.remove_pending(102) == dependent
    scheduler.release("100")
    assert selected(scheduler) == sibling
    assert scheduler.frontiers == (("100", "101"),)


def test_repeated_hold_release_keeps_exact_pending_index_membership() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    requests = [pending(i, "100", ("100",)) for i in range(101, 151)]
    for request in requests:
        scheduler.add_pending(request)
    for _ in range(100):
        scheduler.hold("100")
        for request in requests:
            scheduler.add_pending(request)
        assert scheduler.select() is None
        scheduler.release("100")
        assert selected(scheduler).request_id == 150
        family = scheduler._family_pending["100"]
        assert len(family.ids) == len(family.positions) == len(requests)
        assert len(set(family.ids)) == len(requests)
    assert [
        scheduler.remove_pending(request.request_id) for request in requests
    ] == requests
    assert scheduler.select() is None


def test_eligible_selection_does_not_scan_large_held_prefix() -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    scheduler.hold("100")
    for request_id in range(101, 10_101):
        scheduler.add_pending(pending(request_id, "100", ("100",)))
    # Same real map and request objects, but forbid a backlog scan at selection.
    scheduler._held_pending = _LookupOnlyDict(scheduler._held_pending)
    for request_id in range(20_000, 20_100):
        request = pending(request_id, str(request_id))
        scheduler.add_pending(request)
        assert selected(scheduler) == request
        assert scheduler.remove_pending(request_id) == request
    assert scheduler.select() is None
