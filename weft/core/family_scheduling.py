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

    def merge(self, other: _PendingIndex) -> None:
        self.newest = _meld(self.newest, other.newest)
        self.count += other.count
        other.newest = None
        other.parent = self

    def remove(self, request_id: int) -> None:
        self.count -= 1
        if self.count == 0:
            self.newest = None
        elif self.newest is not None and self.newest.key == -request_id:
            self.newest = _pop(self.newest)

    def candidate(self, pending: dict[int, PendingRequest]) -> PendingRequest | None:
        while self.newest is not None and -self.newest.key not in pending:
            self.newest = _pop(self.newest)
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
    """

    def __init__(self) -> None:
        self._pending: dict[int, PendingRequest] = {}
        self._parents: dict[str, _PendingIndex] = {}
        self._family_pending: dict[str, _FamilyPending] = {}
        self._request_indexes: dict[int, _PendingIndex] = {}
        self._family_counts: dict[str, int] = {}
        self._roots: list[tuple[int, str]] = []
        self._indexed_roots: set[str] = set()
        self._residents: dict[str, _Resident] = {}
        self._stacks: list[list[str]] = []
        self._turn = 0

    @property
    def frontiers(self) -> tuple[tuple[str, ...], ...]:
        """Return checkpoint stacks; process evidence stays with the Manager."""
        return tuple(tuple(stack) for stack in self._stacks)

    @property
    def resident_tids(self) -> frozenset[str]:
        return frozenset(self._residents)

    def pending(self, request_id: int) -> PendingRequest | None:
        return self._pending.get(request_id)

    def _parent(self, root_tid: str, ancestry_tids: tuple[str, ...]) -> str:
        return next(
            (tid for tid in reversed(ancestry_tids) if tid in self._residents), root_tid
        )

    def add_pending(self, request: PendingRequest) -> None:
        """Index accepted work once; a repeated projection is idempotent."""
        if request.request_id in self._pending:
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
        """Forget an exact reserved/absent request; lazy heads discard its ID."""
        request = self._pending.pop(request_id, None)
        if request is not None:
            self._family_counts[request.root_tid] -= 1
            index = self._request_indexes.pop(request_id).owner()
            index.remove(request_id)
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
        return {self._residents[stack[-1]].root_tid for stack in self._stacks}

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
        """Choose one candidate without consuming work or reserving a place."""
        count = len(self._stacks)
        for offset in range(count):
            index = (self._turn + offset) % count
            tip = self._stacks[index][-1]
            bucket = self._parents.get(tip)
            candidate = bucket.candidate(self._pending) if bucket else None
            if candidate is not None:
                self._turn = (index + 1) % count
                return candidate
        candidate = self._oldest_independent()
        if candidate is not None:
            return candidate
        # Live paths supply sibling candidates when no independent family waits.
        for offset in range(count):
            index = (self._turn + offset) % count
            root = self._residents[self._stacks[index][-1]].root_tid
            family = self._family_pending.get(root)
            candidate = family.candidate(self._pending) if family else None
            if candidate is not None:
                self._turn = (index + 1) % count
                return candidate
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
        path = [
            ancestor
            for ancestor in request.ancestry_tids
            if ancestor in self._residents
        ]
        parent = path[-1] if path else None
        self._residents[tid] = resident
        for stack in self._stacks:
            if parent is not None and stack[-1] == parent:
                stack.append(tid)
                return
        self._stacks.append([*path, tid])

    def release_background(self, tid: str) -> None:
        """Release a validated background task after its startup gate clears."""
        resident = self._residents.get(tid)
        if resident is not None and resident.scheduling_class == "background":
            self.retire(tid)

    def retire(self, tid: str) -> None:
        """Contract a positively exited resident without touching pending tokens."""
        resident = self._residents.pop(tid, None)
        if resident is None:
            return
        parent = self._parent(resident.root_tid, resident.ancestry_tids)
        source = self._parents.pop(tid, None) if parent != tid else None
        if source is not None:
            self._parents.setdefault(parent, _PendingIndex()).merge(source)
        stacks = [[item for item in stack if item != tid] for stack in self._stacks]
        # Borrowed paths may collapse to the same shared ancestor after exit.
        maximal: list[list[str]] = []
        for stack in sorted(stacks, key=len, reverse=True):
            if stack and not any(other[: len(stack)] == stack for other in maximal):
                maximal.append(stack)
        self._stacks = maximal
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
        """Adopt live/unknown finite evidence; bootstrap may defer path rebuilding."""
        if scheduling_class != "finite":
            return
        self._residents[tid] = _Resident(root_tid, ancestry_tids, scheduling_class)
        if not defer:
            self.finish_recovery()

    def finish_recovery(self) -> None:
        """Reconstruct all maximal live paths once after an unordered state sweep."""
        paths = [
            [*(item for item in resident.ancestry_tids if item in self._residents), tid]
            for tid, resident in self._residents.items()
        ]
        maximal: list[list[str]] = []
        for path in sorted(paths, key=len, reverse=True):
            if not any(other[: len(path)] == path for other in maximal):
                maximal.append(path)
        self._stacks = maximal
