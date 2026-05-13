"""Tests for pure state-machine reducer helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest

from weft.core.state_machines import StateMachine, Transition

pytestmark = [pytest.mark.shared]

SimpleState = Literal["idle", "running", "done", "unused"]
SimpleAction = Literal["start", "finish"]


@dataclass(frozen=True, slots=True)
class Signal:
    value: str


def _is_signal(expected: str):
    def _predicate(_state: str, signal: Signal) -> bool:
        return signal.value == expected

    return _predicate


def _simple_machine() -> StateMachine[SimpleState, Signal, SimpleAction]:
    return StateMachine(
        states=("idle", "running", "done"),
        actions=("start", "finish"),
        transitions=(
            Transition(
                id="idle-start",
                source="idle",
                target="running",
                action="start",
                predicate=_is_signal("start"),
                reason="start requested",
            ),
            Transition(
                id="running-finish",
                source="running",
                target="done",
                action="finish",
                predicate=_is_signal("finish"),
                reason="finish requested",
            ),
        ),
        terminal_states=("done",),
    )


def test_decide_returns_first_matching_transition() -> None:
    machine = _simple_machine()

    decision = machine.decide("idle", Signal("start"))

    assert decision.source == "idle"
    assert decision.target == "running"
    assert decision.action == "start"
    assert decision.transition_id == "idle-start"
    assert decision.reason == "start requested"


def test_source_set_uses_current_state_as_decision_source() -> None:
    machine: StateMachine[SimpleState, Signal, SimpleAction] = StateMachine(
        states=("idle", "running", "done"),
        actions=("finish",),
        transitions=(
            Transition(
                id="finish-from-active",
                source=frozenset(("idle", "running")),
                target="done",
                action="finish",
                predicate=_is_signal("finish"),
                reason="finish from active state",
            ),
        ),
        terminal_states=("done",),
    )

    decision = machine.decide("running", Signal("finish"))

    assert decision.source == "running"
    assert decision.target == "done"
    assert machine.transition_source_states(machine.transitions[0]) == frozenset(
        ("idle", "running")
    )


def test_transition_order_is_deterministic() -> None:
    machine: StateMachine[SimpleState, Signal, SimpleAction] = StateMachine(
        states=("idle", "running", "done"),
        actions=("start", "finish"),
        transitions=(
            Transition(
                id="first",
                source="idle",
                target="running",
                action="start",
                predicate=lambda _state, _signal: True,
                reason="first wins",
            ),
            Transition(
                id="second",
                source="idle",
                target="done",
                action="finish",
                predicate=lambda _state, _signal: True,
                reason="second loses",
            ),
            Transition(
                id="running-finish",
                source="running",
                target="done",
                action="finish",
                predicate=_is_signal("finish"),
                reason="finish requested",
            ),
        ),
        terminal_states=("done",),
    )

    decision = machine.decide("idle", Signal("anything"))

    assert decision.transition_id == "first"


def test_unknown_current_state_fails() -> None:
    machine = _simple_machine()

    with pytest.raises(ValueError, match="Unknown current state"):
        machine.decide("missing", Signal("start"))  # type: ignore[arg-type]


def test_no_matching_transition_fails_explicitly() -> None:
    machine = _simple_machine()

    with pytest.raises(ValueError, match="No transition matched"):
        machine.decide("idle", Signal("finish"))


def test_duplicate_transition_ids_fail() -> None:
    with pytest.raises(ValueError, match="Duplicate transition IDs"):
        StateMachine(
            states=("idle", "running", "done"),
            actions=("start", "finish"),
            transitions=(
                Transition(
                    id="same",
                    source="idle",
                    target="running",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="start requested",
                ),
                Transition(
                    id="same",
                    source="running",
                    target="done",
                    action="finish",
                    predicate=_is_signal("finish"),
                    reason="finish requested",
                ),
            ),
            terminal_states=("done",),
        )


def test_unknown_transition_source_fails() -> None:
    with pytest.raises(ValueError, match="unknown source states"):
        StateMachine(
            states=("idle", "running", "done"),
            actions=("start",),
            transitions=(
                Transition(
                    id="bad-source",
                    source="unused",
                    target="running",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="bad source",
                ),
            ),
            terminal_states=("done",),
        )


def test_unknown_transition_target_fails() -> None:
    with pytest.raises(ValueError, match="unknown target state"):
        StateMachine(
            states=("idle", "running", "done"),
            actions=("start",),
            transitions=(
                Transition(
                    id="bad-target",
                    source="idle",
                    target="unused",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="bad target",
                ),
            ),
            terminal_states=("done",),
        )


def test_unknown_transition_action_fails() -> None:
    with pytest.raises(ValueError, match="unknown action"):
        StateMachine(
            states=("idle", "running", "done"),
            actions=("start",),
            transitions=(
                Transition(
                    id="bad-action",
                    source="idle",
                    target="running",
                    action="finish",
                    predicate=_is_signal("start"),
                    reason="bad action",
                ),
                Transition(
                    id="running-start",
                    source="running",
                    target="done",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="finish path",
                ),
            ),
            terminal_states=("done",),
        )


def test_declared_action_without_transition_fails() -> None:
    with pytest.raises(ValueError, match="Actions without transitions"):
        StateMachine(
            states=("idle", "running", "done"),
            actions=("start", "finish"),
            transitions=(
                Transition(
                    id="idle-start",
                    source="idle",
                    target="running",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="start requested",
                ),
                Transition(
                    id="running-start",
                    source="running",
                    target="done",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="finish path",
                ),
            ),
            terminal_states=("done",),
        )


def test_plain_set_source_fails_with_clear_error() -> None:
    with pytest.raises(ValueError, match="source must be a state string"):
        StateMachine(
            states=("idle", "running", "done"),
            actions=("start",),
            transitions=(
                Transition(
                    id="bad-source-type",
                    source={"idle"},  # type: ignore[arg-type]
                    target="running",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="bad source type",
                ),
                Transition(
                    id="running-start",
                    source="running",
                    target="done",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="finish path",
                ),
            ),
            terminal_states=("done",),
        )


def test_terminal_state_with_outgoing_transition_fails_by_default() -> None:
    with pytest.raises(ValueError, match="Terminal states have outgoing transitions"):
        StateMachine(
            states=("idle", "done"),
            actions=("start",),
            transitions=(
                Transition(
                    id="done-start",
                    source="done",
                    target="idle",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="restart",
                ),
            ),
            terminal_states=("done",),
        )


def test_terminal_outgoing_transition_can_be_allowed_explicitly() -> None:
    machine: StateMachine[SimpleState, Signal, SimpleAction] = StateMachine(
        states=("idle", "done"),
        actions=("start",),
        transitions=(
            Transition(
                id="done-start",
                source="done",
                target="idle",
                action="start",
                predicate=_is_signal("start"),
                reason="restart",
            ),
            Transition(
                id="idle-start",
                source="idle",
                target="done",
                action="start",
                predicate=_is_signal("finish"),
                reason="finish",
            ),
        ),
        terminal_states=("done",),
        allow_terminal_outgoing=True,
    )

    assert machine.decide("done", Signal("start")).target == "idle"


def test_nonterminal_state_without_transition_fails_unless_sink() -> None:
    with pytest.raises(ValueError, match="without outgoing transitions"):
        StateMachine(
            states=("idle", "running", "done"),
            actions=("start",),
            transitions=(
                Transition(
                    id="idle-start",
                    source="idle",
                    target="running",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="start requested",
                ),
            ),
            terminal_states=("done",),
        )

    machine: StateMachine[SimpleState, Signal, SimpleAction] = StateMachine(
        states=("idle", "running", "done"),
        actions=("start",),
        transitions=(
            Transition(
                id="idle-start",
                source="idle",
                target="running",
                action="start",
                predicate=_is_signal("start"),
                reason="start requested",
            ),
        ),
        terminal_states=("done",),
        sink_states=("running",),
    )
    assert machine.decide("idle", Signal("start")).target == "running"


def test_terminal_and_sink_states_cannot_overlap() -> None:
    with pytest.raises(ValueError, match="both terminal and sink"):
        StateMachine(
            states=("idle", "done"),
            actions=("start",),
            transitions=(
                Transition(
                    id="idle-start",
                    source="idle",
                    target="done",
                    action="start",
                    predicate=_is_signal("start"),
                    reason="start requested",
                ),
            ),
            terminal_states=("done",),
            sink_states=("done",),
        )


def test_reachability_detects_unreachable_states() -> None:
    machine: StateMachine[SimpleState, Signal, SimpleAction] = StateMachine(
        states=("idle", "running", "done", "unused"),
        actions=("start", "finish"),
        transitions=(
            Transition(
                id="idle-start",
                source="idle",
                target="running",
                action="start",
                predicate=_is_signal("start"),
                reason="start requested",
            ),
            Transition(
                id="running-finish",
                source="running",
                target="done",
                action="finish",
                predicate=_is_signal("finish"),
                reason="finish requested",
            ),
        ),
        terminal_states=("done",),
        sink_states=("unused",),
    )

    assert machine.reachable_states(("idle",)) == frozenset(("idle", "running", "done"))
    assert machine.unreachable_states(("idle",)) == frozenset(("unused",))
    with pytest.raises(AssertionError, match="Unreachable states"):
        machine.assert_all_states_reachable(("idle",))


def test_coverage_assertions_report_missing_and_unknown_values() -> None:
    machine = _simple_machine()

    with pytest.raises(AssertionError, match="Uncovered transition IDs"):
        machine.assert_transition_ids_covered(("idle-start",))
    with pytest.raises(AssertionError, match="Unknown covered transition IDs"):
        machine.assert_transition_ids_covered(
            ("idle-start", "running-finish", "not-real")
        )

    with pytest.raises(AssertionError, match="Uncovered states"):
        machine.assert_states_covered(("idle", "running"))
    with pytest.raises(AssertionError, match="Unknown covered states"):
        machine.assert_states_covered(("idle", "running", "done", "not-real"))

    with pytest.raises(AssertionError, match="Uncovered actions"):
        machine.assert_actions_covered(("start",))
    with pytest.raises(AssertionError, match="Unknown covered actions"):
        machine.assert_actions_covered(("start", "finish", "not-real"))


TaskState = Literal[
    "created",
    "spawning",
    "running",
    "completed",
    "failed",
    "timeout",
    "cancelled",
    "killed",
]
TaskAction = Literal[
    "begin_spawn",
    "start_running",
    "complete",
    "fail",
    "timeout",
    "cancel",
    "kill",
]


@dataclass(frozen=True, slots=True)
class TargetStatus:
    status: TaskState


def _target_status(expected: TaskState):
    def _predicate(_state: TaskState, target: TargetStatus) -> bool:
        return target.status == expected

    return _predicate


def _task_lifecycle_machine() -> StateMachine[TaskState, TargetStatus, TaskAction]:
    return StateMachine(
        states=(
            "created",
            "spawning",
            "running",
            "completed",
            "failed",
            "timeout",
            "cancelled",
            "killed",
        ),
        actions=(
            "begin_spawn",
            "start_running",
            "complete",
            "fail",
            "timeout",
            "cancel",
            "kill",
        ),
        transitions=(
            Transition(
                id="created-to-spawning",
                source="created",
                target="spawning",
                action="begin_spawn",
                predicate=_target_status("spawning"),
                reason="task spawn started",
            ),
            Transition(
                id="created-to-failed",
                source="created",
                target="failed",
                action="fail",
                predicate=_target_status("failed"),
                reason="task failed before start",
            ),
            Transition(
                id="created-to-cancelled",
                source="created",
                target="cancelled",
                action="cancel",
                predicate=_target_status("cancelled"),
                reason="task cancelled before start",
            ),
            Transition(
                id="spawning-to-running",
                source="spawning",
                target="running",
                action="start_running",
                predicate=_target_status("running"),
                reason="task running",
            ),
            Transition(
                id="spawning-to-completed",
                source="spawning",
                target="completed",
                action="complete",
                predicate=_target_status("completed"),
                reason="task completed during spawn",
            ),
            Transition(
                id="spawning-to-failed",
                source="spawning",
                target="failed",
                action="fail",
                predicate=_target_status("failed"),
                reason="task failed during spawn",
            ),
            Transition(
                id="spawning-to-timeout",
                source="spawning",
                target="timeout",
                action="timeout",
                predicate=_target_status("timeout"),
                reason="task timed out during spawn",
            ),
            Transition(
                id="spawning-to-cancelled",
                source="spawning",
                target="cancelled",
                action="cancel",
                predicate=_target_status("cancelled"),
                reason="task cancelled during spawn",
            ),
            Transition(
                id="spawning-to-killed",
                source="spawning",
                target="killed",
                action="kill",
                predicate=_target_status("killed"),
                reason="task killed during spawn",
            ),
            Transition(
                id="running-to-completed",
                source="running",
                target="completed",
                action="complete",
                predicate=_target_status("completed"),
                reason="task completed",
            ),
            Transition(
                id="running-to-failed",
                source="running",
                target="failed",
                action="fail",
                predicate=_target_status("failed"),
                reason="task failed",
            ),
            Transition(
                id="running-to-timeout",
                source="running",
                target="timeout",
                action="timeout",
                predicate=_target_status("timeout"),
                reason="task timed out",
            ),
            Transition(
                id="running-to-cancelled",
                source="running",
                target="cancelled",
                action="cancel",
                predicate=_target_status("cancelled"),
                reason="task cancelled",
            ),
            Transition(
                id="running-to-killed",
                source="running",
                target="killed",
                action="kill",
                predicate=_target_status("killed"),
                reason="task killed",
            ),
        ),
        terminal_states=("completed", "failed", "timeout", "cancelled", "killed"),
    )


def test_task_lifecycle_allowed_transitions_are_covered() -> None:
    machine = _task_lifecycle_machine()
    cases: tuple[tuple[TaskState, TaskState, str], ...] = (
        ("created", "spawning", "created-to-spawning"),
        ("created", "failed", "created-to-failed"),
        ("created", "cancelled", "created-to-cancelled"),
        ("spawning", "running", "spawning-to-running"),
        ("spawning", "completed", "spawning-to-completed"),
        ("spawning", "failed", "spawning-to-failed"),
        ("spawning", "timeout", "spawning-to-timeout"),
        ("spawning", "cancelled", "spawning-to-cancelled"),
        ("spawning", "killed", "spawning-to-killed"),
        ("running", "completed", "running-to-completed"),
        ("running", "failed", "running-to-failed"),
        ("running", "timeout", "running-to-timeout"),
        ("running", "cancelled", "running-to-cancelled"),
        ("running", "killed", "running-to-killed"),
    )
    seen_transitions: set[str] = set()
    seen_states: set[TaskState] = set()
    seen_actions: set[TaskAction] = set()

    for current, target, transition_id in cases:
        decision = machine.decide(current, TargetStatus(target))
        assert decision.target == target
        assert decision.transition_id == transition_id
        seen_transitions.add(decision.transition_id)
        seen_states.update((decision.source, decision.target))
        seen_actions.add(decision.action)

    machine.assert_all_states_reachable(("created",))
    machine.assert_transition_ids_covered(seen_transitions)
    machine.assert_states_covered(seen_states)
    machine.assert_actions_covered(seen_actions)


@pytest.mark.parametrize(
    ("current", "target"),
    (
        ("created", "completed"),
        ("created", "running"),
        ("running", "spawning"),
        ("completed", "running"),
        ("failed", "running"),
        ("timeout", "running"),
        ("cancelled", "running"),
        ("killed", "running"),
    ),
)
def test_task_lifecycle_rejects_representative_forbidden_transitions(
    current: TaskState,
    target: TaskState,
) -> None:
    machine = _task_lifecycle_machine()

    with pytest.raises(ValueError, match="No transition matched"):
        machine.decide(current, TargetStatus(target))
