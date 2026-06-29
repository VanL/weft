"""Spec checks for TaskSpec state transitions (STATE.1/STATE.2)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.taskspec import fixtures
from weft.core.task_lifecycle import (
    TaskLifecycleStatus,
    TaskStatusTarget,
    task_lifecycle_machine,
    task_lifecycle_statuses,
    terminal_task_lifecycle_statuses,
    valid_task_status_targets,
    validate_task_status_transition,
)


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


def test_invalid_transition_raises() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    with pytest.raises(ValueError):
        taskspec.set_status("completed")


def test_terminal_state_is_immutable() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    taskspec.mark_running()
    taskspec.mark_completed()
    with pytest.raises(ValueError):
        taskspec.set_status("running")


def test_lifecycle_machine_covers_allowed_state_transitions() -> None:
    cases: tuple[tuple[TaskLifecycleStatus, TaskLifecycleStatus, str], ...] = (
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
    seen_states: set[TaskLifecycleStatus] = set()
    seen_actions: set[str] = set()

    for current, target, transition_id in cases:
        decision = validate_task_status_transition(current, target)
        assert decision.target == target
        assert decision.transition_id == transition_id
        seen_transitions.add(decision.transition_id)
        seen_states.update((decision.source, decision.target))
        seen_actions.add(decision.action)

    task_lifecycle_machine.assert_all_states_reachable(("created",))
    task_lifecycle_machine.assert_transition_ids_covered(seen_transitions)
    task_lifecycle_machine.assert_states_covered(seen_states)
    task_lifecycle_machine.assert_actions_covered(seen_actions)


def test_lifecycle_machine_pair_matrix_matches_transition_table() -> None:
    for current in sorted(task_lifecycle_statuses):
        valid_targets = valid_task_status_targets(current)
        for target in sorted(task_lifecycle_statuses):
            if target in valid_targets:
                decision = validate_task_status_transition(current, target)
                assert decision.source == current
                assert decision.target == target
            else:
                with pytest.raises(ValueError, match="No transition matched"):
                    task_lifecycle_machine.decide(current, TaskStatusTarget(target))

    for current in sorted(terminal_task_lifecycle_statuses):
        assert valid_task_status_targets(current) == frozenset()


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
def test_lifecycle_machine_rejects_representative_forbidden_transitions(
    current: TaskLifecycleStatus,
    target: TaskLifecycleStatus,
) -> None:
    with pytest.raises(ValueError, match="No transition matched"):
        task_lifecycle_machine.decide(current, TaskStatusTarget(target))


def test_direct_created_to_running_still_fails() -> None:
    taskspec = fixtures.create_minimal_taskspec()

    with pytest.raises(ValueError, match="Invalid transition"):
        taskspec.set_status("running")


def test_nonterminal_same_status_update_stays_allowed() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    taskspec.set_status("created")
    assert taskspec.state.status == "created"

    taskspec.mark_started()
    started_at = taskspec.state.started_at
    taskspec.set_status("spawning")

    assert taskspec.state.status == "spawning"
    assert taskspec.state.started_at == started_at


def test_unknown_current_status_fails_explicitly() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    object.__setattr__(taskspec.state, "status", "unknown")

    with pytest.raises(ValueError, match="Unknown current status"):
        taskspec.set_status("running")


_STATUS_OPERATIONS = {
    "started": lambda taskspec: taskspec.mark_started(),
    "running": lambda taskspec: taskspec.mark_running(),
    "completed": lambda taskspec: taskspec.mark_completed(),
    "failed": lambda taskspec: taskspec.mark_failed(error="generated"),
    "timeout": lambda taskspec: taskspec.mark_timeout(error="generated"),
    "cancelled": lambda taskspec: taskspec.mark_cancelled(reason="generated"),
    "killed": lambda taskspec: taskspec.mark_killed(reason="generated"),
}


@pytest.mark.property
@given(
    operations=st.lists(
        st.sampled_from(tuple(_STATUS_OPERATIONS)),
        min_size=1,
        max_size=12,
    )
)
def test_generated_status_operation_sequences_never_leave_terminal_state(
    operations: list[str],
) -> None:
    taskspec = fixtures.create_minimal_taskspec()

    terminal_status: str | None = None
    for operation in operations:
        try:
            _STATUS_OPERATIONS[operation](taskspec)
        except ValueError:
            pass

        if terminal_status is not None:
            assert taskspec.state.status == terminal_status
        elif taskspec.state.status in terminal_task_lifecycle_statuses:
            terminal_status = taskspec.state.status

        if taskspec.state.status in {"spawning", "running"}:
            assert taskspec.state.started_at is not None
        if taskspec.state.status in terminal_task_lifecycle_statuses:
            assert taskspec.state.completed_at is not None
