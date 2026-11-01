"""Spec checks for TaskSpec state transitions (STATE.1/STATE.2)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.taskspec import fixtures
from weft.core.task_lifecycle import (
    TaskLifecycleAction,
    TaskLifecycleStatus,
    task_lifecycle_machine,
    task_lifecycle_statuses,
    terminal_task_lifecycle_statuses,
    valid_task_status_targets,
    validate_task_status_transition,
)
from weft.core.taskspec import TaskSpec


def test_mark_started_sets_spawning_state() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    taskspec.mark_started()
    assert taskspec.state.status == "spawning"
    assert taskspec.state.started_at is not None


def test_mark_running_promotes_from_created() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    taskspec.mark_running()
    assert taskspec.state.status == "running"
    assert taskspec.state.started_at is not None


@pytest.mark.parametrize("target", ("completed", "timeout", "killed"))
def test_invalid_transition_raises(target: TaskLifecycleStatus) -> None:
    taskspec = fixtures.create_minimal_taskspec()
    before = taskspec.state.model_dump()

    with pytest.raises(ValueError, match="Invalid transition"):
        taskspec.set_status(target)

    assert taskspec.state.model_dump() == before


def test_terminal_state_is_immutable() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    taskspec.mark_running()
    taskspec.mark_completed()
    with pytest.raises(ValueError):
        taskspec.set_status("running")


def test_lifecycle_machine_covers_allowed_state_transitions() -> None:
    cases: tuple[
        tuple[TaskLifecycleStatus, TaskLifecycleStatus, str, TaskLifecycleAction], ...
    ] = (
        ("created", "spawning", "created-to-spawning", "begin_spawn"),
        ("created", "failed", "created-to-failed", "fail"),
        ("created", "cancelled", "created-to-cancelled", "cancel"),
        ("spawning", "running", "spawning-to-running", "start_running"),
        ("spawning", "completed", "spawning-to-completed", "complete"),
        ("spawning", "failed", "spawning-to-failed", "fail"),
        ("spawning", "timeout", "spawning-to-timeout", "timeout"),
        ("spawning", "cancelled", "spawning-to-cancelled", "cancel"),
        ("spawning", "killed", "spawning-to-killed", "kill"),
        ("running", "completed", "running-to-completed", "complete"),
        ("running", "failed", "running-to-failed", "fail"),
        ("running", "timeout", "running-to-timeout", "timeout"),
        ("running", "cancelled", "running-to-cancelled", "cancel"),
        ("running", "killed", "running-to-killed", "kill"),
    )
    seen_transitions: set[str] = set()
    seen_states: set[TaskLifecycleStatus] = set()
    seen_actions: set[TaskLifecycleAction] = set()

    for current, target, transition_id, expected_action in cases:
        decision = validate_task_status_transition(current, target)
        assert decision.target == target
        assert decision.action == expected_action
        assert decision.transition_id == transition_id
        seen_transitions.add(decision.transition_id)
        seen_states.update((decision.source, decision.target))
        seen_actions.add(decision.action)

    task_lifecycle_machine.assert_all_states_reachable(("created",))
    task_lifecycle_machine.assert_transition_ids_covered(seen_transitions)
    task_lifecycle_machine.assert_states_covered(seen_states)
    task_lifecycle_machine.assert_actions_covered(seen_actions)


def test_lifecycle_machine_pair_matrix_matches_transition_table() -> None:
    # Independent oracle from [STATE.1], not the production transition table.
    allowed: dict[TaskLifecycleStatus, frozenset[TaskLifecycleStatus]] = {
        "created": frozenset({"spawning", "failed", "cancelled"}),
        "spawning": frozenset(
            {"running", "completed", "failed", "timeout", "cancelled", "killed"}
        ),
        "running": frozenset({"completed", "failed", "timeout", "cancelled", "killed"}),
        "completed": frozenset(),
        "failed": frozenset(),
        "timeout": frozenset(),
        "cancelled": frozenset(),
        "killed": frozenset(),
    }
    assert task_lifecycle_statuses == allowed.keys()
    for current, valid_targets in allowed.items():
        assert valid_task_status_targets(current) == valid_targets
        for target in allowed:
            if target in valid_targets:
                decision = validate_task_status_transition(current, target)
                assert decision.source == current
                assert decision.target == target
            else:
                with pytest.raises(ValueError, match="No transition matched"):
                    validate_task_status_transition(current, target)

    assert terminal_task_lifecycle_statuses == {
        status for status, targets in allowed.items() if not targets
    }


def test_direct_created_to_running_still_fails() -> None:
    taskspec = fixtures.create_minimal_taskspec()

    with pytest.raises(ValueError, match="Invalid transition"):
        taskspec.set_status("running")


def test_nonterminal_same_status_update_stays_allowed() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    statuses: tuple[TaskLifecycleStatus, ...] = ("created", "spawning", "running")

    for status in statuses:
        if status != "created":
            taskspec.set_status(status)
        before = taskspec.state.model_dump()
        taskspec.set_status(status)

        assert taskspec.state.model_dump() == before


@pytest.mark.parametrize(
    "status", ("completed", "failed", "timeout", "cancelled", "killed")
)
def test_terminal_same_status_update_is_rejected(status: TaskLifecycleStatus) -> None:
    taskspec = fixtures.create_minimal_taskspec()
    taskspec.mark_running()
    taskspec.set_status(status)
    before = taskspec.state.model_dump()

    with pytest.raises(ValueError, match="Cannot transition from terminal state"):
        taskspec.set_status(status)

    assert taskspec.state.model_dump() == before


def test_unknown_current_status_fails_explicitly() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    object.__setattr__(taskspec.state, "status", "unknown")

    with pytest.raises(ValueError, match="Unknown current status"):
        taskspec.set_status("running")


_STATUS_OPERATIONS: dict[str, Callable[[TaskSpec], None]] = {
    "started": lambda taskspec: taskspec.mark_started(),
    "running": lambda taskspec: taskspec.mark_running(),
    "completed": lambda taskspec: taskspec.mark_completed(),
    "failed": lambda taskspec: taskspec.mark_failed(error="generated"),
    "timeout": lambda taskspec: taskspec.mark_timeout(error="generated"),
    "cancelled": lambda taskspec: taskspec.mark_cancelled(reason="generated"),
    "killed": lambda taskspec: taskspec.mark_killed(reason="generated"),
}
_LEGAL_INITIAL_STATUS_OPERATIONS = ("started", "running", "failed", "cancelled")


@pytest.mark.property
@given(
    initial_operation=st.sampled_from(_LEGAL_INITIAL_STATUS_OPERATIONS),
    tail=st.lists(
        st.sampled_from(tuple(_STATUS_OPERATIONS)),
        min_size=0,
        max_size=11,
    ),
)
def test_generated_status_operation_sequences_never_leave_terminal_state(
    initial_operation: str,
    tail: list[str],
) -> None:
    taskspec = fixtures.create_minimal_taskspec()

    terminal_status: str | None = None
    successful_operations = 0
    for operation in (initial_operation, *tail):
        try:
            _STATUS_OPERATIONS[operation](taskspec)
        except ValueError:
            pass
        else:
            successful_operations += 1

        if terminal_status is not None:
            assert taskspec.state.status == terminal_status
        elif taskspec.state.status in terminal_task_lifecycle_statuses:
            terminal_status = taskspec.state.status

        if taskspec.state.status in {"spawning", "running"}:
            assert taskspec.state.started_at is not None
        if taskspec.state.status in terminal_task_lifecycle_statuses:
            assert taskspec.state.completed_at is not None
    assert successful_operations >= 1
