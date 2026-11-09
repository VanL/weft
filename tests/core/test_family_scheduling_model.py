"""Compare pending custody and dispatch eligibility to [MA-1.8] over traces."""

from __future__ import annotations

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from weft.core.family_scheduling import FamilyScheduler, PendingRequest

pytestmark = [pytest.mark.shared, pytest.mark.property]

# Fixed request identities let generated traces revisit a request, add a
# descendant before its parent is observed, or hold overlapping ancestors.
_REQUESTS = (
    PendingRequest(100, "100", None, ()),
    PendingRequest(101, "100", "100", ("100",)),
    PendingRequest(102, "100", "101", ("100", "101")),
    PendingRequest(103, "100", "100", ("100",)),
    PendingRequest(200, "200", None, ()),
    PendingRequest(201, "200", "200", ("200",)),
)
_TIDS = tuple(str(request.request_id) for request in _REQUESTS)


@settings(max_examples=200)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(
                ("project", "remove", "hold", "release", "launch", "retire")
            ),
            st.integers(min_value=0, max_value=len(_REQUESTS) - 1),
        ),
        min_size=1,
        max_size=60,
    )
)
@example(
    trace=[
        ("project", 0),
        ("launch", 0),
        ("hold", 0),
        ("hold", 1),
        ("project", 2),
        ("release", 1),
        ("remove", 2),
        ("release", 0),
        ("retire", 0),
        ("project", 0),
    ]
)
def test_family_pending_custody_matches_model(
    trace: list[tuple[str, int]],
) -> None:
    """Holds filter eligibility by ancestry, without consuming pending work."""
    scheduler = FamilyScheduler()
    pending: dict[int, PendingRequest] = {}
    held: set[str] = set()
    live: set[str] = set()
    launched: set[str] = set()
    selected: PendingRequest | None = None

    for action, index in trace:
        request = _REQUESTS[index]
        request_id = request.request_id
        tid = _TIDS[index]
        if action == "project" and tid not in launched:
            scheduler.add_pending(request)
            pending[request_id] = request
        elif action == "remove":
            assert scheduler.remove_pending(request_id) == pending.pop(request_id, None)
        elif action == "hold":
            scheduler.hold(tid)
            held.add(tid)
        elif action == "release":
            scheduler.release(tid)
            held.discard(tid)
        elif action == "launch" and selected is not None:
            # Consume the candidate checked against the oracle on the last step.
            assert scheduler.remove_pending(selected.request_id) == pending.pop(
                selected.request_id
            )
            scheduler.launched(selected)
            launched_tid = str(selected.request_id)
            live.add(launched_tid)
            launched.add(launched_tid)
        elif action == "retire":
            scheduler.retire(tid)
            live.discard(tid)

        assert scheduler.resident_tids == live
        assert scheduler.indexed_pending_count == len(pending)
        for known in _REQUESTS:
            assert scheduler.pending(known.request_id) == pending.get(known.request_id)

        selected = _check_selection(scheduler, pending, held, live)


def _check_selection(
    scheduler: FamilyScheduler,
    pending: dict[int, PendingRequest],
    held: set[str],
    live: set[str],
) -> PendingRequest | None:
    eligible = [
        entry for entry in pending.values() if held.isdisjoint(entry.ancestry_tids)
    ]
    selected = scheduler.select()
    if not eligible:
        assert selected is None
    elif not live:
        # With no live paths, take the oldest family, then its oldest row.
        assert selected == min(
            eligible, key=lambda entry: (int(entry.root_tid), entry.request_id)
        )
    else:
        assert selected in eligible
    return selected


@given(st.integers(min_value=1, max_value=8), st.booleans())
def test_recovered_tip_reclaims_already_indexed_children(
    child_count: int, defer: bool
) -> None:
    scheduler = FamilyScheduler()
    scheduler.recover("100", "100", ())
    for request_id in range(102, 102 + child_count):
        scheduler.add_pending(PendingRequest(request_id, "100", "101", ("100", "101")))
    scheduler.add_pending(PendingRequest(200, "200", None))
    scheduler.recover("101", "100", ("100",), defer=defer)
    if defer:
        scheduler.finish_recovery()

    # The live tip gets its newest child before an independent family enters.
    for request_id in reversed(range(102, 102 + child_count)):
        candidate = scheduler.select()
        assert candidate is not None and candidate.request_id == request_id
        assert scheduler.remove_pending(request_id) == candidate
    candidate = scheduler.select()
    assert candidate is not None and candidate.request_id == 200
