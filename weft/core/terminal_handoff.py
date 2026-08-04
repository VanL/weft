"""Pure terminal handoff reducer for private host result channels.

This module selects deterministic state transitions from typed observations.
Process, channel, clock, monitor, and cleanup I/O remain with the host adapters.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.5]
- docs/specifications/07-System_Invariants.md [REDUCER.5]-[REDUCER.8]
- docs/specifications/08-Testing_Strategy.md [TS-0]
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Literal, cast

from weft._constants import (
    TERMINAL_HANDOFF_ACTION_VALUES,
    TERMINAL_HANDOFF_EDGE_EVENT_KIND_VALUES,
    TERMINAL_HANDOFF_EVENT_KIND_VALUES,
    TERMINAL_HANDOFF_ONE_SHOT_EVENT_ORDER,
    TERMINAL_HANDOFF_PERSISTENT_SESSION_EVENT_ORDER,
    TERMINAL_HANDOFF_STATE_VALUES,
    TERMINAL_HANDOFF_STOP_EVENT_KIND_VALUES,
    TERMINAL_HANDOFF_TERMINAL_STATE_VALUES,
    TERMINAL_HANDOFF_TRANSITION_SPECS,
)
from weft.core.state_machines import StateDecision, StateMachine, Transition

TerminalHandoffState = Literal[
    "observing",
    "draining",
    "stopping_timeout",
    "stopping_cancel",
    "stopping_limit",
    "decided",
]
TerminalHandoffEventKind = Literal[
    "outcome_received",
    "producer_exited",
    "channel_sealed",
    "timeout_requested",
    "cancel_requested",
    "limit_reached",
    "drain_expired",
    "transport_failed",
]
TerminalHandoffAction = Literal[
    "return_outcome",
    "begin_drain",
    "stop_for_timeout",
    "stop_for_cancel",
    "stop_for_limit",
    "return_timeout",
    "return_cancelled",
    "return_limit",
    "return_protocol_failure",
    "wait",
]
TerminalHandoffObservationPolicy = Literal["one_shot", "persistent_session"]
TerminalHandoffStopEventKind = Literal[
    "timeout_requested",
    "cancel_requested",
    "limit_reached",
]

terminal_handoff_states = cast(
    frozenset[TerminalHandoffState],
    TERMINAL_HANDOFF_STATE_VALUES,
)
terminal_terminal_handoff_states = cast(
    frozenset[TerminalHandoffState],
    TERMINAL_HANDOFF_TERMINAL_STATE_VALUES,
)
terminal_handoff_event_kinds = cast(
    frozenset[TerminalHandoffEventKind],
    TERMINAL_HANDOFF_EVENT_KIND_VALUES,
)
terminal_handoff_actions = cast(
    frozenset[TerminalHandoffAction],
    TERMINAL_HANDOFF_ACTION_VALUES,
)


@dataclass(frozen=True, slots=True)
class TerminalHandoffEvent:
    """One carrier-neutral observation supplied to the handoff reducer."""

    kind: TerminalHandoffEventKind
    outcome: object | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class TerminalHandoffProgress:
    """Immutable adapter-owned progress for one terminal handoff."""

    state: TerminalHandoffState = "observing"
    consumed_edge_kinds: frozenset[TerminalHandoffEventKind] = frozenset()
    accepted_stop: TerminalHandoffStopEventKind | None = None


@dataclass(frozen=True, slots=True)
class TerminalHandoffStep:
    """One selected event, reducer decision, and updated handoff progress."""

    event: TerminalHandoffEvent
    decision: StateDecision[TerminalHandoffState, TerminalHandoffAction]
    progress: TerminalHandoffProgress


def _event_kind(
    expected: TerminalHandoffEventKind,
) -> Callable[[TerminalHandoffState, TerminalHandoffEvent], bool]:
    def _predicate(
        _current: TerminalHandoffState,
        event: TerminalHandoffEvent,
    ) -> bool:
        return event.kind == expected

    return _predicate


terminal_handoff_machine: StateMachine[
    TerminalHandoffState,
    TerminalHandoffEvent,
    TerminalHandoffAction,
] = StateMachine(
    states=terminal_handoff_states,
    actions=terminal_handoff_actions,
    transitions=tuple(
        Transition(
            id=f"terminal-handoff-{source}-{event_kind}",
            source=cast(TerminalHandoffState, source),
            target=cast(TerminalHandoffState, target),
            action=cast(TerminalHandoffAction, action),
            predicate=_event_kind(cast(TerminalHandoffEventKind, event_kind)),
            reason=reason,
        )
        for source, event_kind, target, action, reason in (
            TERMINAL_HANDOFF_TRANSITION_SPECS
        )
    ),
    terminal_states=terminal_terminal_handoff_states,
)


def reduce_terminal_handoff(
    current: TerminalHandoffState,
    event: TerminalHandoffEvent,
) -> StateDecision[TerminalHandoffState, TerminalHandoffAction]:
    """Return the exact transition selected for one terminal observation.

    Spec: [CC-3.5], [REDUCER.5]-[REDUCER.8].
    """

    if event.kind not in terminal_handoff_event_kinds:
        raise ValueError(f"Unknown terminal handoff event kind: {event.kind!r}")
    if event.kind == "outcome_received":
        if event.outcome is None:
            raise ValueError("outcome_received requires an outcome payload")
    elif event.outcome is not None:
        raise ValueError(f"{event.kind} cannot carry an outcome payload")

    return terminal_handoff_machine.decide(current, event)


def select_terminal_handoff_event(
    observations: Collection[TerminalHandoffEvent],
    *,
    policy: TerminalHandoffObservationPolicy,
) -> TerminalHandoffEvent:
    """Select one observation using the policy's normative same-turn order.

    Spec: [CC-3.5], [EXEC.10].
    """

    if not observations:
        raise ValueError("terminal handoff selection requires at least one observation")
    if policy == "one_shot":
        policy_order = cast(
            tuple[TerminalHandoffEventKind, ...],
            TERMINAL_HANDOFF_ONE_SHOT_EVENT_ORDER,
        )
    elif policy == "persistent_session":
        policy_order = cast(
            tuple[TerminalHandoffEventKind, ...],
            TERMINAL_HANDOFF_PERSISTENT_SESSION_EVENT_ORDER,
        )
    else:
        raise ValueError(f"Unknown terminal handoff observation policy: {policy!r}")

    events_by_kind: dict[TerminalHandoffEventKind, TerminalHandoffEvent] = {}
    duplicate_kinds: set[TerminalHandoffEventKind] = set()
    for event in observations:
        if event.kind not in terminal_handoff_event_kinds:
            raise ValueError(f"Unknown terminal handoff event kind: {event.kind!r}")
        if event.kind in events_by_kind:
            duplicate_kinds.add(event.kind)
        events_by_kind[event.kind] = event

    if duplicate_kinds:
        formatted = ", ".join(sorted(duplicate_kinds))
        raise ValueError(
            f"terminal handoff observations contain duplicate event kinds: {formatted}"
        )

    return next(events_by_kind[kind] for kind in policy_order if kind in events_by_kind)


def drive_terminal_handoff_turn(
    progress: TerminalHandoffProgress,
    observations: Collection[TerminalHandoffEvent],
    *,
    policy: TerminalHandoffObservationPolicy,
) -> TerminalHandoffStep | None:
    """Reduce the highest-priority eligible observation for one adapter turn.

    Stop and producer-exit observations become consumed edges after their first
    reduction. Once a stop intent is accepted, every later stop observation is
    ineligible. The function is pure; adapters retain the returned progress and
    own all I/O and effects.

    Spec: [CC-3.5], [EXEC.10].
    """

    eligible = tuple(
        event
        for event in observations
        if event.kind not in progress.consumed_edge_kinds
        and not (
            progress.accepted_stop is not None
            and event.kind in TERMINAL_HANDOFF_STOP_EVENT_KIND_VALUES
        )
    )
    if not eligible:
        return None

    event = select_terminal_handoff_event(eligible, policy=policy)
    decision = reduce_terminal_handoff(progress.state, event)
    consumed = progress.consumed_edge_kinds
    if event.kind in TERMINAL_HANDOFF_EDGE_EVENT_KIND_VALUES:
        consumed = consumed | frozenset((event.kind,))
    accepted_stop = progress.accepted_stop
    if accepted_stop is None and event.kind in TERMINAL_HANDOFF_STOP_EVENT_KIND_VALUES:
        accepted_stop = cast(TerminalHandoffStopEventKind, event.kind)

    return TerminalHandoffStep(
        event=event,
        decision=decision,
        progress=TerminalHandoffProgress(
            state=decision.target,
            consumed_edge_kinds=consumed,
            accepted_stop=accepted_stop,
        ),
    )


__all__ = [
    "TerminalHandoffAction",
    "TerminalHandoffEvent",
    "TerminalHandoffEventKind",
    "TerminalHandoffObservationPolicy",
    "TerminalHandoffProgress",
    "TerminalHandoffState",
    "TerminalHandoffStep",
    "TerminalHandoffStopEventKind",
    "drive_terminal_handoff_turn",
    "reduce_terminal_handoff",
    "select_terminal_handoff_event",
    "terminal_handoff_actions",
    "terminal_handoff_event_kinds",
    "terminal_handoff_machine",
    "terminal_handoff_states",
    "terminal_terminal_handoff_states",
]
