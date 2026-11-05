"""Broker-free finite branch selection.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1.8]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [MANAGER.18]

The Manager supplies validated lineage and positive process-exit evidence.
These indexes are hints; exact broker reservation remains launch authority.
"""

from __future__ import annotations

import heapq
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

SchedulingClass = Literal["finite", "background", "internal"]


@dataclass(frozen=True, slots=True)
class PendingRequest:
    """One primary request and its consume-on-dispatch label [MF-5]."""

    request_id: int
    root_tid: str
    submitter_tid: str | None
    ancestry_tids: tuple[str, ...] = ()
    label_message_id: int = 0

    @property
    def queue_name(self) -> str:
        """Return the original producer bucket, independent of contraction."""
        parent = self.submitter_tid or self.root_tid
        return f"weft.state.labels.{self.root_tid}.pending.{parent}"


@dataclass(slots=True)
class _Head:
    key: int
    rank: int = 1
    left: _Head | None = None
    right: _Head | None = None


def _meld(left: _Head | None, right: _Head | None) -> _Head | None:
    """Meld leftist indexes in logarithmic time without token relocation."""
    if left is None:
        return right
    if right is None:
        return left
    if left.key > right.key:
        left, right = right, left
    left.right = _meld(left.right, right)
    left_rank = left.left.rank if left.left else 0
    right_rank = left.right.rank if left.right else 0
    if left_rank < right_rank:
        left.left, left.right = left.right, left.left
    left.rank = (left.right.rank if left.right else 0) + 1
    return left


def _pop(head: _Head) -> _Head | None:
    return _meld(head.left, head.right)


@dataclass(slots=True)
class _PendingIndex:
    newest: _Head | None = None
    count: int = 0
    physical_count: int = 0
    parent: _PendingIndex | None = None

    def owner(self) -> _PendingIndex:
        path: list[_PendingIndex] = []
        owner = self
        while owner.parent is not None:
            path.append(owner)
            owner = owner.parent
        for item in path:
            item.parent = owner
        return owner

    def add(self, request_id: int) -> None:
        self.newest = _meld(self.newest, _Head(-request_id))
        self.count += 1
        self.physical_count += 1

    def merge(self, other: _PendingIndex) -> None:
        self.newest = _meld(self.newest, other.newest)
        self.count += other.count
        self.physical_count += other.physical_count
        other.newest = None
        other.count = 0
        other.physical_count = 0
        other.parent = self

    def _owns(
        self,
        request_id: int,
        pending: Mapping[int, PendingRequest],
        indexes: Mapping[int, _PendingIndex],
    ) -> bool:
        """Reject stale IDs re-added under a later recovered ancestor."""
        if request_id not in pending:
            return False
        index = indexes.get(request_id)
        return index is not None and index.owner() is self

    def _compact(
        self,
        pending: Mapping[int, PendingRequest],
        indexes: Mapping[int, _PendingIndex],
    ) -> None:
        """Reclaim only this bucket's history, preserving cheap contraction.

        Traversal and bottom-up pairwise meld cost O(H), plus owner-handle
        resolution (which compresses paths but need not be constant per ID).
        The H > 2L trigger charges rebuilding to discarded history; one call
        can still take linear time in the live bucket. Spec: [MA-1.8].
        """
        nodes = [self.newest] if self.newest is not None else []
        heads: list[_Head | None] = []
        seen: set[int] = set()
        while nodes:
            node = nodes.pop()
            if node.left is not None:
                nodes.append(node.left)
            if node.right is not None:
                nodes.append(node.right)
            request_id = -node.key
            if request_id not in seen and self._owns(request_id, pending, indexes):
                seen.add(request_id)
                node.left = node.right = None
                node.rank = 1
                heads.append(node)

        self.physical_count = len(heads)
        while len(heads) > 1:
            heads = [
                _meld(
                    heads[index], heads[index + 1] if index + 1 < len(heads) else None
                )
                for index in range(0, len(heads), 2)
            ]
        self.newest = heads[0] if heads else None

    def remove(
        self,
        request_id: int,
        pending: Mapping[int, PendingRequest],
        indexes: Mapping[int, _PendingIndex],
    ) -> None:
        self.count -= 1
        if self.count == 0:
            self.newest = None
            self.physical_count = 0
        elif self.newest is not None and self.newest.key == -request_id:
            self.newest = _pop(self.newest)
            self.physical_count -= 1
        if self.physical_count > 2 * self.count:
            self._compact(pending, indexes)

    def candidate(
        self,
        pending: Mapping[int, PendingRequest],
        indexes: Mapping[int, _PendingIndex],
    ) -> PendingRequest | None:
        while self.newest is not None and not self._owns(
            -self.newest.key, pending, indexes
        ):
            self.newest = _pop(self.newest)
            self.physical_count -= 1
        return pending.get(-self.newest.key) if self.newest else None


class _FamilyPending:
    """An indexed FIFO heap permits exact removal after LIFO branch selection."""

    def __init__(self) -> None:
        self.ids: list[int] = []
        self.positions: dict[int, int] = {}

    def _swap(self, left: int, right: int) -> None:
        self.ids[left], self.ids[right] = self.ids[right], self.ids[left]
        self.positions[self.ids[left]] = left
        self.positions[self.ids[right]] = right

    def _up(self, index: int) -> int:
        while index and self.ids[index] < self.ids[(index - 1) // 2]:
            parent = (index - 1) // 2
            self._swap(index, parent)
            index = parent
        return index

    def add(self, request_id: int) -> None:
        self.positions[request_id] = len(self.ids)
        self.ids.append(request_id)
        self._up(len(self.ids) - 1)

    def remove(self, request_id: int) -> None:
        index = self.positions.pop(request_id)
        last = self.ids.pop()
        if index == len(self.ids):
            return
        self.ids[index] = last
        self.positions[last] = index
        index = self._up(index)
        while index * 2 + 1 < len(self.ids):
            child = index * 2 + 1
            if child + 1 < len(self.ids) and self.ids[child + 1] < self.ids[child]:
                child += 1
            if self.ids[index] <= self.ids[child]:
                break
            self._swap(index, child)
            index = child

    def candidate(self, pending: dict[int, PendingRequest]) -> PendingRequest | None:
        return pending[self.ids[0]] if self.ids else None


@dataclass(frozen=True, slots=True)
class _Resident:
    root_tid: str
    ancestry_tids: tuple[str, ...]
    scheduling_class: SchedulingClass


class FamilyScheduler:
    """Work-conserving FIFO family entry, round-robin branches and LIFO descent.

    Spec: [MA-1.8]. Only the caller can declare a resident positively exited.
    No method performs broker I/O or infers death from a missing observation.

    Frontier stacks are the childless registered residents; a resident is
    registered when it has a ``_live_children`` count, which every resident
    has except a deferred adoption awaiting ``finish_recovery``. The ring is
    a circular doubly linked list of stack ids (``_ring_next``,
    ``_ring_prev``) with one tip per stack (``_ring_tip``); ``_turn`` is the
    stack asked first. A stack's path is derived from its tip's ancestry,
    never stored. ``_up`` is a union-find over residents and former
    residents: each entry points at an ancestor with no registered resident
    in between, and lookups compress chains of unregistered nodes (the
    contracted scheduling-parent pointer). ``_live_children`` counts the
    registered residents whose nearest registered ancestor is the key. All
    of these are hints rebuilt by ``finish_recovery``.
    """

    def __init__(self) -> None:
        self._pending: dict[int, PendingRequest] = {}
        self._held_pending: dict[int, PendingRequest] = {}
        self._held_ancestors: set[str] = set()
        self._parents: dict[str, _PendingIndex] = {}
        self._family_pending: dict[str, _FamilyPending] = {}
        self._request_indexes: dict[int, _PendingIndex] = {}
        self._family_counts: dict[str, int] = {}
        self._roots: list[tuple[int, str]] = []
        self._indexed_roots: set[str] = set()
        self._residents: dict[str, _Resident] = {}
        self._up: dict[str, str | None] = {}
        self._live_children: dict[str, int] = {}
        self._ring_next: dict[int, int] = {}
        self._ring_prev: dict[int, int] = {}
        self._ring_tip: dict[int, str] = {}
        self._tip_stack: dict[str, int] = {}
        self._turn: int | None = None
        self._next_stack_id = 0

    @property
    def indexed_pending_count(self) -> int:
        """Return this owner's indexed observation, not a shared-source count [MF-5]."""
        return len(self._pending) + len(self._held_pending)

    @property
    def frontiers(self) -> tuple[tuple[str, ...], ...]:
        """Return checkpoint stacks in rotation order; derived, never stored.

        Filtering a tip's ancestry yields its path only because every launch
        registers a leaf; a registration that adds a live ancestor of an
        existing resident must rebuild through ``finish_recovery``.
        """
        residents = self._residents
        live = residents.__contains__
        return tuple(
            (*filter(live, residents[tip].ancestry_tids), tip)
            for tip in self._rotation_tips()
        )

    @property
    def resident_tids(self) -> frozenset[str]:
        return frozenset(self._residents)

    def pending(self, request_id: int) -> PendingRequest | None:
        return self._pending.get(request_id) or self._held_pending.get(request_id)

    def hold(self, tid: str) -> None:
        """Park the ancestry-dependent closure without consuming requests [MA-1.8]."""
        if tid in self._held_ancestors:
            return
        self._held_ancestors.add(tid)
        for request in tuple(self._pending.values()):
            if tid in request.ancestry_tids:
                self.remove_pending(request.request_id)
                self._held_pending[request.request_id] = request

    def release(self, tid: str) -> None:
        """Restore ordinary ordering after positive started/settlement evidence."""
        if tid not in self._held_ancestors:
            return
        self._held_ancestors.remove(tid)
        for request in tuple(self._held_pending.values()):
            if not self._held_ancestors.intersection(request.ancestry_tids):
                self._held_pending.pop(request.request_id)
                self.add_pending(request)

    def _parent(self, root_tid: str, ancestry_tids: tuple[str, ...]) -> str:
        return next(
            (tid for tid in reversed(ancestry_tids) if tid in self._residents), root_tid
        )

    def _find(self, tid: str | None) -> str | None:
        """Return the nearest registered resident at or above ``tid``; compress.

        Only a resident with a live-child count stops the walk. A former
        resident and a deferred adoption with a retained pointer are both
        followed past, so lookups see the registered set until
        ``finish_recovery`` registers the deferred residents.

        Spec: [MA-1.8] ("Cache contracted scheduling-parent pointers").
        """
        registered = self._live_children
        root = tid
        while root is not None and root not in registered:
            root = self._up[root]
        while tid is not None and tid not in registered:
            self._up[tid], tid = root, self._up[tid]
        return root

    def _live_parent(self, tid: str) -> str | None:
        parent = self._find(self._up[tid])
        self._up[tid] = parent
        return parent

    def _nearest_live(self, ancestry_tids: tuple[str, ...]) -> str | None:
        for tid in reversed(ancestry_tids):
            if tid in self._up:
                return self._find(tid)
        return None

    def _rotation_tips(self) -> list[str]:
        """Return the tips in rotation order, starting at ``_turn``."""
        tips: list[str] = []
        stack_id = self._turn
        if stack_id is not None:
            for _ in range(len(self._ring_tip)):
                tips.append(self._ring_tip[stack_id])
                stack_id = self._ring_next[stack_id]
        return tips

    def _start_stack(self, tip: str) -> None:
        """Link a new stack at the ring's tail, just before ``_turn``."""
        stack_id = self._next_stack_id
        self._next_stack_id += 1
        self._ring_tip[stack_id] = tip
        self._tip_stack[tip] = stack_id
        head = self._turn
        if head is None:
            self._ring_next[stack_id] = self._ring_prev[stack_id] = stack_id
            self._turn = stack_id
            return
        tail = self._ring_prev[head]
        self._ring_next[tail] = self._ring_prev[head] = stack_id
        self._ring_next[stack_id] = head
        self._ring_prev[stack_id] = tail

    def _drop_stack(self, stack_id: int) -> None:
        """Unlink a stack; ``_turn`` moves to its successor if it was the head."""
        del self._ring_tip[stack_id]
        following = self._ring_next.pop(stack_id)
        preceding = self._ring_prev.pop(stack_id)
        if following == stack_id:
            self._turn = None
            return
        self._ring_next[preceding] = following
        self._ring_prev[following] = preceding
        if self._turn == stack_id:
            self._turn = following

    def _contract(self, tid: str, live_parent: str | None, children: int) -> None:
        """Hand an exited resident's children to its parent and fix its stack."""
        if live_parent is not None:
            self._live_children[live_parent] += children - 1
        stack_id = self._tip_stack.pop(tid, None)
        if stack_id is not None:
            # A childless exit contracts its stack in place or, when a sibling
            # path still holds the parent, drops the stack.
            if live_parent is not None and not self._live_children[live_parent]:
                self._ring_tip[stack_id] = live_parent
                self._tip_stack[live_parent] = stack_id
            else:
                self._drop_stack(stack_id)
        self._compact_up()

    def _compact_up(self) -> None:
        """Drop former residents once they outnumber residents (cf. ``_compact``)."""
        if len(self._up) > 2 * len(self._live_children):
            self._up = {tid: self._live_parent(tid) for tid in self._live_children}

    def add_pending(self, request: PendingRequest) -> None:
        """Index accepted work once; a repeated projection is idempotent."""
        if self.pending(request.request_id) is not None:
            return
        if self._held_ancestors.intersection(request.ancestry_tids):
            self._held_pending[request.request_id] = request
            return
        self._pending[request.request_id] = request
        parent = self._parent(request.root_tid, request.ancestry_tids)
        index = self._parents.setdefault(parent, _PendingIndex())
        index.add(request.request_id)
        self._request_indexes[request.request_id] = index
        self._family_pending.setdefault(request.root_tid, _FamilyPending()).add(
            request.request_id
        )
        count = self._family_counts.get(request.root_tid, 0)
        self._family_counts[request.root_tid] = count + 1
        if count == 0:
            self._offer_root(request.root_tid)

    def remove_pending(self, request_id: int) -> PendingRequest | None:
        """Forget exact reserved/absent work and bound bucket-local history [MA-1.8]."""
        held = self._held_pending.pop(request_id, None)
        if held is not None:
            return held
        request = self._pending.pop(request_id, None)
        if request is not None:
            self._family_counts[request.root_tid] -= 1
            index = self._request_indexes.pop(request_id).owner()
            index.remove(request_id, self._pending, self._request_indexes)
            if not index.count:
                parent = self._parent(request.root_tid, request.ancestry_tids)
                if self._parents.get(parent) is index:
                    self._parents.pop(parent)
            self._family_pending[request.root_tid].remove(request_id)
            if not self._family_counts[request.root_tid]:
                self._family_pending.pop(request.root_tid)
                self._family_counts.pop(request.root_tid)
        return request

    def _offer_root(self, root_tid: str) -> None:
        if root_tid not in self._indexed_roots:
            heapq.heappush(self._roots, (int(root_tid), root_tid))
            self._indexed_roots.add(root_tid)

    def _active_roots(self) -> set[str]:
        return {self._residents[tip].root_tid for tip in self._ring_tip.values()}

    def _oldest_independent(self) -> PendingRequest | None:
        active = self._active_roots()
        parked: list[tuple[int, str]] = []
        candidate = None
        while self._roots:
            key, root = heapq.heappop(self._roots)
            self._indexed_roots.discard(root)
            if not self._family_counts.get(root):
                continue
            parked.append((key, root))
            if root not in active:
                candidate = self._family_pending[root].candidate(self._pending)
                break
        for _, root in parked:
            self._offer_root(root)
        return candidate

    def select(self) -> PendingRequest | None:
        """Choose one candidate without consuming work or reserving a place.

        Each pass walks the ring from ``_turn``; a hit moves ``_turn`` to the
        successor of the stack that supplied it, and a miss leaves it unchanged.
        """
        start = self._turn
        if start is None:
            return self._oldest_independent()
        ring_next, ring_tip = self._ring_next, self._ring_tip
        stack_id = start
        for _ in range(len(ring_tip)):
            bucket = self._parents.get(ring_tip[stack_id])
            candidate = (
                bucket.candidate(self._pending, self._request_indexes)
                if bucket
                else None
            )
            if candidate is not None:
                self._turn = ring_next[stack_id]
                return candidate
            stack_id = ring_next[stack_id]
        candidate = self._oldest_independent()
        if candidate is not None:
            return candidate
        # Live paths supply sibling candidates when no independent family waits.
        for _ in range(len(ring_tip)):
            family = self._family_pending.get(
                self._residents[ring_tip[stack_id]].root_tid
            )
            candidate = family.candidate(self._pending) if family else None
            if candidate is not None:
                self._turn = ring_next[stack_id]
                return candidate
            stack_id = ring_next[stack_id]
        return None

    def launched(
        self, request: PendingRequest, scheduling_class: SchedulingClass = "finite"
    ) -> None:
        """Register a successful launch; background retains its startup lineage."""
        if scheduling_class == "internal":
            return
        tid = str(request.request_id)
        resident = _Resident(request.root_tid, request.ancestry_tids, scheduling_class)
        existing = self._residents.get(tid)
        if existing is not None:
            if existing != resident:
                raise ValueError("resident scheduling identity changed")
            return
        # A deferred adoption is not registered: the scan steps over it when it
        # has no pointer, and _find follows its retained pointer past it when it
        # has one; finish_recovery repairs the path either way.
        parent = self._nearest_live(request.ancestry_tids)
        self._residents[tid] = resident
        self._up[tid] = parent
        self._live_children[tid] = 0
        if parent is None:
            self._start_stack(tid)
            return
        self._live_children[parent] += 1
        stack_id = self._tip_stack.pop(parent, None)
        if stack_id is None:
            self._start_stack(tid)
        else:
            self._ring_tip[stack_id] = tid
            self._tip_stack[tid] = stack_id

    def release_background(self, tid: str) -> None:
        """Release a validated background task after its startup gate clears."""
        resident = self._residents.get(tid)
        if resident is not None and resident.scheduling_class == "background":
            self.retire(tid)

    def retire(self, tid: str) -> None:
        """Contract a positively exited resident without touching pending tokens.

        Pending custody follows residents, deferred ones included: the bucket
        merges into ``_parent`` exactly as before. Frontier bookkeeping follows
        the registered set: counts and ring contract through ``_find``, and a
        deferred adoption, in no stack yet, is left to ``finish_recovery``.
        """
        resident = self._residents.pop(tid, None)
        if resident is None:
            return
        parent = self._parent(resident.root_tid, resident.ancestry_tids)
        source = self._parents.pop(tid, None) if parent != tid else None
        if source is not None:
            self._parents.setdefault(parent, _PendingIndex()).merge(source)
        children = self._live_children.pop(tid, None)
        if children is not None:
            self._contract(tid, self._find(tid), children)
        if self._family_counts.get(resident.root_tid):
            self._offer_root(resident.root_tid)

    def recover(
        self,
        tid: str,
        root_tid: str,
        ancestry_tids: tuple[str, ...],
        scheduling_class: SchedulingClass = "finite",
        *,
        defer: bool = False,
    ) -> None:
        """Adopt live/unknown finite evidence; bootstrap may defer path rebuilding.

        Until ``finish_recovery`` runs, a deferred resident has no count or
        stack, and any pointer it kept as a former resident is followed past:
        ``retire`` merges its pending bucket and leaves the frontier to the
        rebuild, and lookups resolve to its nearest registered ancestor.
        """
        if scheduling_class != "finite":
            return
        self._residents[tid] = _Resident(root_tid, ancestry_tids, scheduling_class)
        if not defer:
            self.finish_recovery()

    def finish_recovery(self) -> None:
        """Rebuild pointers, counts and the ring once after an unordered sweep."""
        residents = self._residents
        self._up = {
            tid: next(
                (item for item in reversed(r.ancestry_tids) if item in residents), None
            )
            for tid, r in residents.items()
        }
        self._live_children = dict.fromkeys(residents, 0)
        for parent in self._up.values():
            if parent is not None:
                self._live_children[parent] += 1
        self._ring_next, self._ring_prev = {}, {}
        self._ring_tip, self._tip_stack = {}, {}
        self._turn = None
        for tid, children in self._live_children.items():
            if not children:
                self._start_stack(tid)
