"""Tests for TID-mapping cleanup candidate discovery.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5], Cleanup Boundary
- docs/specifications/07-System_Invariants.md [OBS.13.7]
"""

from __future__ import annotations

import pytest

from weft.liveness.policy import (
    MappingHistoryDecision,
    UnknownDeadlineState,
    reduce_mapping_history,
    reduce_unknown_deadline,
)

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize(
    ("current", "candidate", "malformed", "now_ns", "expected"),
    [
        (None, 100, None, 100, MappingHistoryDecision(True, ())),
        (None, 100, "invalid_json", 100, MappingHistoryDecision(False, ())),
        (None, 100, "invalid_json", 200, MappingHistoryDecision(False, (100,))),
        (100, 200, None, 300, MappingHistoryDecision(True, (100,))),
        (200, 100, None, 300, MappingHistoryDecision(False, (100,))),
        (200, 199, None, 199, MappingHistoryDecision(False, ())),
    ],
)
def test_mapping_history_reducer_owns_malformed_and_superseded_retirement(
    current: int | None,
    candidate: int,
    malformed: str | None,
    now_ns: int,
    expected: MappingHistoryDecision,
) -> None:
    assert (
        reduce_mapping_history(
            current_message_id=current,
            candidate_message_id=candidate,
            malformed_reason=malformed,
            now_ns=now_ns,
            min_age_seconds=0.00000005,
        )
        == expected
    )


def test_unknown_deadline_pauses_during_not_attempted_cycles() -> None:
    state, retire = reduce_unknown_deadline(
        None,
        generation="one",
        outcome="not_attempted",
        now_monotonic=5.0,
        timeout_seconds=5.0,
    )
    assert state is None
    assert retire is False

    state, retire = reduce_unknown_deadline(
        state,
        generation="one",
        outcome="unknown",
        now_monotonic=10.0,
        timeout_seconds=5.0,
    )
    assert state == UnknownDeadlineState("one", 15.0, None)
    assert retire is False

    state, retire = reduce_unknown_deadline(
        state,
        generation="one",
        outcome="not_attempted",
        now_monotonic=12.0,
        timeout_seconds=5.0,
    )
    assert state == UnknownDeadlineState("one", 15.0, 12.0)
    assert retire is False

    state, retire = reduce_unknown_deadline(
        state,
        generation="one",
        outcome="unknown",
        now_monotonic=20.0,
        timeout_seconds=5.0,
    )
    assert state == UnknownDeadlineState("one", 23.0, None)
    assert retire is False

    state, retire = reduce_unknown_deadline(
        state,
        generation="one",
        outcome="unknown",
        now_monotonic=23.0,
        timeout_seconds=5.0,
    )
    assert retire is True


def test_unknown_deadline_resets_on_live_or_generation_change() -> None:
    state = UnknownDeadlineState("one", 15.0, None)
    assert reduce_unknown_deadline(
        state,
        generation="one",
        outcome="live",
        now_monotonic=14.0,
        timeout_seconds=5.0,
    ) == (None, False)
    replacement, retire = reduce_unknown_deadline(
        state,
        generation="two",
        outcome="unknown",
        now_monotonic=14.0,
        timeout_seconds=5.0,
    )
    assert replacement == UnknownDeadlineState("two", 19.0, None)
    assert retire is False


def test_unknown_deadline_stale_retires_and_backward_pause_time_is_rejected() -> None:
    state = UnknownDeadlineState("one", 15.0, 12.0)
    assert reduce_unknown_deadline(
        state,
        generation="one",
        outcome="stale",
        now_monotonic=13.0,
        timeout_seconds=5.0,
    ) == (None, True)
    with pytest.raises(ValueError, match="backwards"):
        reduce_unknown_deadline(
            state,
            generation="one",
            outcome="unknown",
            now_monotonic=11.0,
            timeout_seconds=5.0,
        )


def test_mapping_row_liveness_keeps_unknown_and_releases_terminal_without_live_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from weft.liveness import policy

    assert policy.mapping_row_is_live({"terminal": False}) is True
    assert policy.mapping_row_is_live({"terminal": True}) is False

    handle = {
        "runner": "host",
        "kind": "process",
        "id": "runtime",
        "control": {"authority": "host-pid"},
        "observations": {"host_processes": [{"pid": 1, "create_time": 1.0}]},
        "metadata": {},
    }
    monkeypatch.setattr(policy, "handle_has_live_host_process", lambda _handle: True)
    assert (
        policy.mapping_row_is_live(
            {"terminal": True, "runtime_handle": handle},
        )
        is True
    )
