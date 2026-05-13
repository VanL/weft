"""Pure state-machine helpers for deterministic reducer tables.

This module provides internal support for expressing Weft reducer decisions as
explicit, table-tested transitions. It does not read queues, inspect processes,
write logs, or apply side effects.

Spec references:
- docs/specifications/01-Core_Components.md [CC-1], [CC-2.4], [CC-2.5]
- docs/specifications/07-System_Invariants.md [STATE.1]-[STATE.6], [MANAGER.15]
- docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1]
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Transition[StateT: str, InputT, ActionT: str]:
    """One pure transition in a reducer state machine."""

    id: str
    source: StateT | frozenset[StateT]
    target: StateT
    action: ActionT
    predicate: Callable[[StateT, InputT], bool]
    reason: str


@dataclass(frozen=True, slots=True)
class StateDecision[StateT: str, ActionT: str]:
    """Selected transition result for one reducer decision."""

    source: StateT
    target: StateT
    action: ActionT
    transition_id: str
    reason: str


class StateMachine[StateT: str, InputT, ActionT: str]:
    """Deterministic table-driven state selector.

    The helper is intentionally narrow. It evaluates declared transitions in
    order, returns the first match, and exposes structural/test coverage checks.
    Domain modules own evidence models, state mutation, and side effects.
    """

    def __init__(
        self,
        *,
        states: Iterable[StateT],
        actions: Iterable[ActionT],
        transitions: Iterable[Transition[StateT, InputT, ActionT]],
        terminal_states: Iterable[StateT] = (),
        sink_states: Iterable[StateT] = (),
        allow_terminal_outgoing: bool = False,
    ) -> None:
        self.states = frozenset(states)
        self.actions = frozenset(actions)
        self.transitions = tuple(transitions)
        self.terminal_states = frozenset(terminal_states)
        self.sink_states = frozenset(sink_states)
        self.allow_terminal_outgoing = allow_terminal_outgoing

        self._validate()
        self.transition_ids = frozenset(
            transition.id for transition in self.transitions
        )

    def decide(self, current: StateT, input: InputT) -> StateDecision[StateT, ActionT]:
        """Return the first matching transition for *current* and *input*."""

        if current not in self.states:
            raise ValueError(f"Unknown current state: {current!r}")

        for transition in self.transitions:
            if current not in self._source_states(transition):
                continue
            if not transition.predicate(current, input):
                continue
            return StateDecision(
                source=current,
                target=transition.target,
                action=transition.action,
                transition_id=transition.id,
                reason=transition.reason,
            )

        raise ValueError(f"No transition matched for state {current!r}")

    def reachable_states(self, initial_states: Iterable[StateT]) -> frozenset[StateT]:
        """Return states reachable from *initial_states* by declared edges."""

        initial = frozenset(initial_states)
        unknown = initial - self.states
        if unknown:
            raise ValueError(f"Unknown initial states: {_format_values(unknown)}")

        edges: dict[StateT, set[StateT]] = {state: set() for state in self.states}
        for transition in self.transitions:
            for source in self._source_states(transition):
                edges[source].add(transition.target)

        seen = set(initial)
        pending = deque(initial)
        while pending:
            state = pending.popleft()
            for target in edges[state]:
                if target in seen:
                    continue
                seen.add(target)
                pending.append(target)
        return frozenset(seen)

    def unreachable_states(self, initial_states: Iterable[StateT]) -> frozenset[StateT]:
        """Return declared states not reachable from *initial_states*."""

        return self.states - self.reachable_states(initial_states)

    def transition_source_states(
        self,
        transition: Transition[StateT, InputT, ActionT],
    ) -> frozenset[StateT]:
        """Return the declared source states for *transition*."""

        return self._source_states(transition)

    def assert_all_states_reachable(self, initial_states: Iterable[StateT]) -> None:
        """Raise if any declared state is unreachable from *initial_states*."""

        unreachable = self.unreachable_states(initial_states)
        if unreachable:
            raise AssertionError(f"Unreachable states: {_format_values(unreachable)}")

    def assert_transition_ids_covered(self, covered_ids: Iterable[str]) -> None:
        """Raise if tests did not cover every declared transition ID."""

        covered = frozenset(covered_ids)
        missing = self.transition_ids - covered
        if missing:
            raise AssertionError(f"Uncovered transition IDs: {_format_values(missing)}")
        unknown = covered - self.transition_ids
        if unknown:
            raise AssertionError(
                f"Unknown covered transition IDs: {_format_values(unknown)}"
            )

    def assert_states_covered(self, covered_states: Iterable[StateT]) -> None:
        """Raise if tests did not cover every declared state."""

        covered = frozenset(covered_states)
        missing = self.states - covered
        if missing:
            raise AssertionError(f"Uncovered states: {_format_values(missing)}")
        unknown = covered - self.states
        if unknown:
            raise AssertionError(f"Unknown covered states: {_format_values(unknown)}")

    def assert_actions_covered(self, covered_actions: Iterable[ActionT]) -> None:
        """Raise if tests did not cover every declared action."""

        covered = frozenset(covered_actions)
        missing = self.actions - covered
        if missing:
            raise AssertionError(f"Uncovered actions: {_format_values(missing)}")
        unknown = covered - self.actions
        if unknown:
            raise AssertionError(f"Unknown covered actions: {_format_values(unknown)}")

    def _validate(self) -> None:
        if not self.states:
            raise ValueError("StateMachine requires at least one state")
        if not self.actions:
            raise ValueError("StateMachine requires at least one action")
        if not self.transitions:
            raise ValueError("StateMachine requires at least one transition")

        if unknown := self.terminal_states - self.states:
            raise ValueError(f"Unknown terminal states: {_format_values(unknown)}")
        if unknown := self.sink_states - self.states:
            raise ValueError(f"Unknown sink states: {_format_values(unknown)}")
        if overlap := self.terminal_states & self.sink_states:
            raise ValueError(
                "States cannot be both terminal and sink states: "
                f"{_format_values(overlap)}"
            )

        ids: set[str] = set()
        duplicate_ids: set[str] = set()
        action_sources: set[ActionT] = set()
        source_states: set[StateT] = set()
        terminal_sources: set[StateT] = set()

        for transition in self.transitions:
            if not transition.id:
                raise ValueError("Transition IDs must be non-empty")
            if transition.id in ids:
                duplicate_ids.add(transition.id)
            ids.add(transition.id)

            sources = self._source_states(transition)
            if not sources:
                raise ValueError(f"Transition {transition.id!r} has no source states")
            if unknown := sources - self.states:
                raise ValueError(
                    f"Transition {transition.id!r} has unknown source states: "
                    f"{_format_values(unknown)}"
                )
            if transition.target not in self.states:
                raise ValueError(
                    f"Transition {transition.id!r} has unknown target state: "
                    f"{transition.target!r}"
                )
            if transition.action not in self.actions:
                raise ValueError(
                    f"Transition {transition.id!r} has unknown action: "
                    f"{transition.action!r}"
                )
            if not transition.reason:
                raise ValueError(f"Transition {transition.id!r} requires a reason")

            source_states.update(sources)
            action_sources.add(transition.action)
            terminal_sources.update(sources & self.terminal_states)

        if duplicate_ids:
            raise ValueError(
                f"Duplicate transition IDs: {_format_values(duplicate_ids)}"
            )

        actions_without_transitions = self.actions - action_sources
        if actions_without_transitions:
            raise ValueError(
                "Actions without transitions: "
                f"{_format_values(actions_without_transitions)}"
            )

        if terminal_sources and not self.allow_terminal_outgoing:
            raise ValueError(
                "Terminal states have outgoing transitions: "
                f"{_format_values(terminal_sources)}"
            )

        required_sources = self.states - self.terminal_states - self.sink_states
        missing_sources = required_sources - source_states
        if missing_sources:
            raise ValueError(
                "Non-terminal states without outgoing transitions: "
                f"{_format_values(missing_sources)}"
            )

    @staticmethod
    def _source_states(
        transition: Transition[StateT, InputT, ActionT],
    ) -> frozenset[StateT]:
        if isinstance(transition.source, frozenset):
            return transition.source
        if not isinstance(transition.source, str):
            raise ValueError(
                f"Transition {transition.id!r} source must be a state string "
                "or frozenset of state strings"
            )
        return frozenset((transition.source,))


def _format_values(values: Iterable[object]) -> str:
    """Return stable debug text for validation failures."""

    return ", ".join(repr(value) for value in sorted(values, key=repr))


__all__ = [
    "StateDecision",
    "StateMachine",
    "Transition",
]
