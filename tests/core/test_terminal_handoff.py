"""Exhaustive contract tests for the private terminal handoff reducer."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import pytest

from weft.core.terminal_handoff import (
    TerminalHandoffAction,
    TerminalHandoffEvent,
    TerminalHandoffEventKind,
    TerminalHandoffObservationPolicy,
    TerminalHandoffProgress,
    TerminalHandoffState,
    drive_terminal_handoff_turn,
    reduce_terminal_handoff,
    select_terminal_handoff_event,
    terminal_handoff_actions,
    terminal_handoff_event_kinds,
    terminal_handoff_machine,
    terminal_handoff_states,
)

pytestmark = [pytest.mark.shared]


@dataclass(frozen=True, slots=True)
class ReducerCase:
    """One literal state/event cell in the normative reducer matrix."""

    state: TerminalHandoffState
    event: TerminalHandoffEventKind
    target: TerminalHandoffState | None
    action: TerminalHandoffAction | None

    @property
    def transition_id(self) -> str | None:
        """Return the expected production transition identifier."""

        if self.target is None:
            return None
        return f"terminal-handoff-{self.state}-{self.event}"


TERMINAL_HANDOFF_CASES = (
    ReducerCase("observing", "outcome_received", "decided", "return_outcome"),
    ReducerCase("observing", "producer_exited", "draining", "begin_drain"),
    ReducerCase("observing", "channel_sealed", "decided", "return_protocol_failure"),
    ReducerCase(
        "observing", "timeout_requested", "stopping_timeout", "stop_for_timeout"
    ),
    ReducerCase("observing", "cancel_requested", "stopping_cancel", "stop_for_cancel"),
    ReducerCase("observing", "limit_reached", "stopping_limit", "stop_for_limit"),
    ReducerCase("observing", "drain_expired", None, None),
    ReducerCase("observing", "transport_failed", "decided", "return_protocol_failure"),
    ReducerCase("draining", "outcome_received", "decided", "return_outcome"),
    ReducerCase("draining", "producer_exited", "draining", "wait"),
    ReducerCase("draining", "channel_sealed", "decided", "return_protocol_failure"),
    ReducerCase(
        "draining", "timeout_requested", "stopping_timeout", "stop_for_timeout"
    ),
    ReducerCase("draining", "cancel_requested", "stopping_cancel", "stop_for_cancel"),
    ReducerCase("draining", "limit_reached", "stopping_limit", "stop_for_limit"),
    ReducerCase("draining", "drain_expired", "decided", "return_protocol_failure"),
    ReducerCase("draining", "transport_failed", "decided", "return_protocol_failure"),
    ReducerCase("stopping_timeout", "outcome_received", "decided", "return_timeout"),
    ReducerCase(
        "stopping_timeout", "producer_exited", "stopping_timeout", "begin_drain"
    ),
    ReducerCase("stopping_timeout", "channel_sealed", "decided", "return_timeout"),
    ReducerCase("stopping_timeout", "timeout_requested", "stopping_timeout", "wait"),
    ReducerCase("stopping_timeout", "cancel_requested", "stopping_timeout", "wait"),
    ReducerCase("stopping_timeout", "limit_reached", "stopping_timeout", "wait"),
    ReducerCase("stopping_timeout", "drain_expired", "decided", "return_timeout"),
    ReducerCase("stopping_timeout", "transport_failed", "decided", "return_timeout"),
    ReducerCase("stopping_cancel", "outcome_received", "decided", "return_cancelled"),
    ReducerCase("stopping_cancel", "producer_exited", "stopping_cancel", "begin_drain"),
    ReducerCase("stopping_cancel", "channel_sealed", "decided", "return_cancelled"),
    ReducerCase("stopping_cancel", "timeout_requested", "stopping_cancel", "wait"),
    ReducerCase("stopping_cancel", "cancel_requested", "stopping_cancel", "wait"),
    ReducerCase("stopping_cancel", "limit_reached", "stopping_cancel", "wait"),
    ReducerCase("stopping_cancel", "drain_expired", "decided", "return_cancelled"),
    ReducerCase("stopping_cancel", "transport_failed", "decided", "return_cancelled"),
    ReducerCase("stopping_limit", "outcome_received", "decided", "return_limit"),
    ReducerCase("stopping_limit", "producer_exited", "stopping_limit", "begin_drain"),
    ReducerCase("stopping_limit", "channel_sealed", "decided", "return_limit"),
    ReducerCase("stopping_limit", "timeout_requested", "stopping_limit", "wait"),
    ReducerCase("stopping_limit", "cancel_requested", "stopping_limit", "wait"),
    ReducerCase("stopping_limit", "limit_reached", "stopping_limit", "wait"),
    ReducerCase("stopping_limit", "drain_expired", "decided", "return_limit"),
    ReducerCase("stopping_limit", "transport_failed", "decided", "return_limit"),
    ReducerCase("decided", "outcome_received", None, None),
    ReducerCase("decided", "producer_exited", None, None),
    ReducerCase("decided", "channel_sealed", None, None),
    ReducerCase("decided", "timeout_requested", None, None),
    ReducerCase("decided", "cancel_requested", None, None),
    ReducerCase("decided", "limit_reached", None, None),
    ReducerCase("decided", "drain_expired", None, None),
    ReducerCase("decided", "transport_failed", None, None),
)


@pytest.mark.parametrize(
    "case",
    TERMINAL_HANDOFF_CASES,
    ids=lambda case: f"{case.state}-{case.event}",
)
def test_terminal_handoff_complete_transition_table(case: ReducerCase) -> None:
    """Every one of the 48 state/event cells has one exact expected result."""

    outcome = object() if case.event == "outcome_received" else None
    event = TerminalHandoffEvent(kind=case.event, outcome=outcome)

    if case.target is None:
        with pytest.raises(
            ValueError,
            match=rf"^No transition matched for state '{case.state}'$",
        ):
            reduce_terminal_handoff(case.state, event)
        return

    decision = reduce_terminal_handoff(case.state, event)
    assert decision.source == case.state
    assert decision.target == case.target
    assert decision.action == case.action
    assert decision.transition_id == case.transition_id
    assert decision.reason


def test_terminal_handoff_table_is_exact_cartesian_product() -> None:
    """The literal oracle contains every cell exactly once."""

    expected_cells = {
        (state, event)
        for state in terminal_handoff_states
        for event in terminal_handoff_event_kinds
    }
    actual_cells = {(case.state, case.event) for case in TERMINAL_HANDOFF_CASES}

    assert len(TERMINAL_HANDOFF_CASES) == len(expected_cells) == 48
    assert len(actual_cells) == len(TERMINAL_HANDOFF_CASES)
    assert actual_cells == expected_cells


def test_terminal_handoff_table_covers_structure_and_transitions() -> None:
    """The full table fires every production edge, state, and action."""

    valid_cases = tuple(case for case in TERMINAL_HANDOFF_CASES if case.target)
    transition_ids = {case.transition_id for case in valid_cases}

    assert len(valid_cases) == 39
    assert len(TERMINAL_HANDOFF_CASES) - len(valid_cases) == 9
    assert terminal_handoff_machine.terminal_states == frozenset(("decided",))
    assert not terminal_handoff_machine.unreachable_states(("observing",))
    terminal_handoff_machine.assert_transition_ids_covered(transition_ids)
    terminal_handoff_machine.assert_states_covered(terminal_handoff_states)
    terminal_handoff_machine.assert_actions_covered(terminal_handoff_actions)
    assert transition_ids == terminal_handoff_machine.transition_ids


SELECTOR_KINDS: tuple[TerminalHandoffEventKind, ...] = (
    "outcome_received",
    "producer_exited",
    "channel_sealed",
    "timeout_requested",
    "cancel_requested",
    "limit_reached",
    "drain_expired",
    "transport_failed",
)


@pytest.mark.parametrize("kind", SELECTOR_KINDS)
def test_terminal_handoff_event_payload_contract(
    kind: TerminalHandoffEventKind,
) -> None:
    """Only outcome events carry a terminal payload."""

    if kind == "outcome_received":
        with pytest.raises(
            ValueError,
            match="outcome_received requires an outcome payload",
        ):
            reduce_terminal_handoff("observing", TerminalHandoffEvent(kind=kind))
        return

    with pytest.raises(ValueError, match=f"{kind} cannot carry an outcome payload"):
        reduce_terminal_handoff(
            "observing",
            TerminalHandoffEvent(kind=kind, outcome=object()),
        )


ONE_SHOT_ORDER: tuple[TerminalHandoffEventKind, ...] = (
    "cancel_requested",
    "outcome_received",
    "timeout_requested",
    "limit_reached",
    "transport_failed",
    "channel_sealed",
    "producer_exited",
    "drain_expired",
)
PERSISTENT_SESSION_ORDER: tuple[TerminalHandoffEventKind, ...] = (
    "cancel_requested",
    "timeout_requested",
    "outcome_received",
    "limit_reached",
    "transport_failed",
    "channel_sealed",
    "producer_exited",
    "drain_expired",
)


def _selector_cases() -> tuple[
    tuple[
        TerminalHandoffObservationPolicy,
        tuple[TerminalHandoffEventKind, ...],
        TerminalHandoffEventKind,
    ],
    ...,
]:
    cases = []
    for policy, order in (
        ("one_shot", ONE_SHOT_ORDER),
        ("persistent_session", PERSISTENT_SESSION_ORDER),
    ):
        for mask in range(1, 1 << len(SELECTOR_KINDS)):
            subset = tuple(
                kind for index, kind in enumerate(SELECTOR_KINDS) if mask & (1 << index)
            )
            expected = next(kind for kind in order if kind in subset)
            cases.append((policy, subset, expected))
    return tuple(cases)


SELECTOR_CASES = _selector_cases()


@pytest.mark.parametrize(
    ("policy", "subset", "expected"),
    SELECTOR_CASES,
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_terminal_handoff_selector_all_nonempty_subsets(
    policy: TerminalHandoffObservationPolicy,
    subset: tuple[TerminalHandoffEventKind, ...],
    expected: TerminalHandoffEventKind,
) -> None:
    """Both policies select exactly one event for all 510 non-empty subsets."""

    events = tuple(
        TerminalHandoffEvent(
            kind=kind,
            outcome=object() if kind == "outcome_received" else None,
        )
        for kind in subset
    )

    selected = select_terminal_handoff_event(events, policy=policy)

    assert selected in events
    assert selected.kind == expected


def test_terminal_handoff_selector_case_set_is_complete_and_unique() -> None:
    """The selector oracle contains 255 unique subsets for each policy."""

    keys = {(policy, frozenset(subset)) for policy, subset, _ in SELECTOR_CASES}
    assert len(SELECTOR_CASES) == len(keys) == 510


@pytest.mark.parametrize(
    ("policy", "order"),
    (
        ("one_shot", ONE_SHOT_ORDER),
        ("persistent_session", PERSISTENT_SESSION_ORDER),
    ),
)
def test_terminal_handoff_selector_all_unordered_pairs(
    policy: TerminalHandoffObservationPolicy,
    order: tuple[TerminalHandoffEventKind, ...],
) -> None:
    """Each policy fires every unordered pair independently of input order."""

    rank = {kind: index for index, kind in enumerate(order)}
    pairs = tuple(combinations(SELECTOR_KINDS, 2))
    assert len(pairs) == 28

    for first_kind, second_kind in pairs:
        events = (
            TerminalHandoffEvent(
                kind=second_kind,
                outcome=object() if second_kind == "outcome_received" else None,
            ),
            TerminalHandoffEvent(
                kind=first_kind,
                outcome=object() if first_kind == "outcome_received" else None,
            ),
        )
        selected = select_terminal_handoff_event(events, policy=policy)
        expected = min((first_kind, second_kind), key=rank.__getitem__)
        assert selected.kind == expected


@pytest.mark.parametrize(
    ("policy", "expected"),
    (
        ("one_shot", "return_outcome"),
        ("persistent_session", "return_timeout"),
    ),
)
def test_terminal_handoff_driver_preserves_deadline_policy(
    policy: TerminalHandoffObservationPolicy,
    expected: TerminalHandoffAction,
) -> None:
    """One-shot and persistent sessions retain their distinct deadline edge."""

    outcome = object()
    step = drive_terminal_handoff_turn(
        TerminalHandoffProgress(),
        (
            TerminalHandoffEvent(kind="outcome_received", outcome=outcome),
            TerminalHandoffEvent(kind="timeout_requested"),
        ),
        policy=policy,
    )
    assert step is not None
    if policy == "one_shot":
        assert step.decision.action == expected
        assert step.progress.state == "decided"
        return

    assert step.decision.action == "stop_for_timeout"
    followup = drive_terminal_handoff_turn(
        step.progress,
        (TerminalHandoffEvent(kind="outcome_received", outcome=outcome),),
        policy=policy,
    )
    assert followup is not None
    assert followup.decision.action == expected
    assert followup.progress.state == "decided"


def test_terminal_handoff_driver_exit_then_outcome_returns_outcome() -> None:
    """Producer exit is consumed once and cannot hide a later outcome."""

    exit_step = drive_terminal_handoff_turn(
        TerminalHandoffProgress(),
        (TerminalHandoffEvent(kind="producer_exited"),),
        policy="one_shot",
    )
    assert exit_step is not None
    assert exit_step.progress.state == "draining"
    assert exit_step.decision.action == "begin_drain"

    outcome = object()
    outcome_step = drive_terminal_handoff_turn(
        exit_step.progress,
        (
            TerminalHandoffEvent(kind="producer_exited"),
            TerminalHandoffEvent(kind="outcome_received", outcome=outcome),
        ),
        policy="one_shot",
    )
    assert outcome_step is not None
    assert outcome_step.event.outcome is outcome
    assert outcome_step.decision.action == "return_outcome"
    assert outcome_step.progress.state == "decided"


@pytest.mark.parametrize(
    ("stop_kind", "stop_action", "return_action"),
    (
        ("cancel_requested", "stop_for_cancel", "return_cancelled"),
        ("timeout_requested", "stop_for_timeout", "return_timeout"),
        ("limit_reached", "stop_for_limit", "return_limit"),
    ),
)
@pytest.mark.parametrize("policy", ("one_shot", "persistent_session"))
def test_terminal_handoff_driver_consumes_stop_level_signal(
    stop_kind: TerminalHandoffEventKind,
    stop_action: TerminalHandoffAction,
    return_action: TerminalHandoffAction,
    policy: TerminalHandoffObservationPolicy,
) -> None:
    """A repeated stop level is filtered so later evidence can decide."""

    first = drive_terminal_handoff_turn(
        TerminalHandoffProgress(),
        (TerminalHandoffEvent(kind=stop_kind),),
        policy=policy,
    )
    assert first is not None
    assert first.decision.action == stop_action

    second = drive_terminal_handoff_turn(
        first.progress,
        (
            TerminalHandoffEvent(kind=stop_kind),
            TerminalHandoffEvent(kind="channel_sealed"),
        ),
        policy=policy,
    )
    assert second is not None
    assert second.event.kind == "channel_sealed"
    assert second.decision.action == return_action
    assert second.progress.state == "decided"


def test_terminal_handoff_driver_returns_none_for_consumed_only_turn() -> None:
    """A repeated edge is ignored instead of reducing a shadow verdict."""

    first = drive_terminal_handoff_turn(
        TerminalHandoffProgress(),
        (TerminalHandoffEvent(kind="producer_exited"),),
        policy="one_shot",
    )
    assert first is not None
    assert (
        drive_terminal_handoff_turn(
            first.progress,
            (TerminalHandoffEvent(kind="producer_exited"),),
            policy="one_shot",
        )
        is None
    )


@pytest.mark.parametrize("policy", ("one_shot", "persistent_session"))
def test_terminal_handoff_driver_consumed_exit_allows_drain_expiry(
    policy: TerminalHandoffObservationPolicy,
) -> None:
    """A dead-producer level signal cannot starve the absolute drain deadline."""

    first = drive_terminal_handoff_turn(
        TerminalHandoffProgress(),
        (TerminalHandoffEvent(kind="producer_exited"),),
        policy=policy,
    )
    assert first is not None

    expired = drive_terminal_handoff_turn(
        first.progress,
        (
            TerminalHandoffEvent(kind="producer_exited"),
            TerminalHandoffEvent(kind="drain_expired"),
        ),
        policy=policy,
    )

    assert expired is not None
    assert expired.event.kind == "drain_expired"
    assert expired.decision.action == "return_protocol_failure"
    assert expired.progress.state == "decided"


def test_terminal_handoff_selector_rejects_empty_observations() -> None:
    """An observation turn cannot select from an empty batch."""

    with pytest.raises(ValueError, match="requires at least one observation"):
        select_terminal_handoff_event((), policy="one_shot")


def test_terminal_handoff_selector_rejects_duplicate_event_kinds() -> None:
    """An adapter must coalesce repeated level observations before selection."""

    event = TerminalHandoffEvent(kind="producer_exited")
    with pytest.raises(ValueError, match="duplicate event kinds"):
        select_terminal_handoff_event((event, event), policy="one_shot")
