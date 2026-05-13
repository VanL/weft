"""Pure task lifecycle transition table.

TaskSpec remains responsible for mutating runtime timestamps and return codes.
This module only validates/selects task status transitions.

Spec references:
- docs/specifications/07-System_Invariants.md [STATE.1]-[STATE.6]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast

from weft._constants import (
    TASK_LIFECYCLE_ACTION_VALUES,
    TASK_LIFECYCLE_STATUS_VALUES,
    TASK_LIFECYCLE_TRANSITION_SPECS,
    TERMINAL_TASK_LIFECYCLE_STATUS_VALUES,
)
from weft.core.state_machines import StateDecision, StateMachine, Transition

TaskLifecycleStatus = Literal[
    "created",
    "spawning",
    "running",
    "completed",
    "failed",
    "timeout",
    "cancelled",
    "killed",
]
TaskLifecycleAction = Literal[
    "begin_spawn",
    "start_running",
    "complete",
    "fail",
    "timeout",
    "cancel",
    "kill",
]

task_lifecycle_statuses = cast(
    frozenset[TaskLifecycleStatus],
    TASK_LIFECYCLE_STATUS_VALUES,
)
terminal_task_lifecycle_statuses = cast(
    frozenset[TaskLifecycleStatus],
    TERMINAL_TASK_LIFECYCLE_STATUS_VALUES,
)
task_lifecycle_actions = cast(
    frozenset[TaskLifecycleAction],
    TASK_LIFECYCLE_ACTION_VALUES,
)


@dataclass(frozen=True, slots=True)
class TaskStatusTarget:
    """Requested target status for task lifecycle validation."""

    status: TaskLifecycleStatus


def _target_status(
    expected: TaskLifecycleStatus,
) -> Callable[[TaskLifecycleStatus, TaskStatusTarget], bool]:
    def _predicate(
        _current: TaskLifecycleStatus,
        target: TaskStatusTarget,
    ) -> bool:
        return target.status == expected

    return _predicate


task_lifecycle_machine: StateMachine[
    TaskLifecycleStatus,
    TaskStatusTarget,
    TaskLifecycleAction,
] = StateMachine(
    states=task_lifecycle_statuses,
    actions=task_lifecycle_actions,
    transitions=tuple(
        Transition(
            id=transition_id,
            source=cast(TaskLifecycleStatus, source),
            target=cast(TaskLifecycleStatus, target),
            action=cast(TaskLifecycleAction, action),
            predicate=_target_status(cast(TaskLifecycleStatus, target)),
            reason=reason,
        )
        for transition_id, source, target, action, reason in (
            TASK_LIFECYCLE_TRANSITION_SPECS
        )
    ),
    terminal_states=terminal_task_lifecycle_statuses,
)


def validate_task_status_transition(
    current: TaskLifecycleStatus,
    target: TaskLifecycleStatus,
) -> StateDecision[TaskLifecycleStatus, TaskLifecycleAction]:
    """Validate and return the selected lifecycle transition.

    The returned decision is useful for table tests and future reducer callers.
    `TaskSpec.set_status()` currently only needs the validation side effect.
    Same-status handling remains with `TaskSpec.set_status()` because existing
    behavior permits non-terminal idempotent status updates but rejects any
    transition attempt from a terminal state.
    """

    return task_lifecycle_machine.decide(current, TaskStatusTarget(target))


def valid_task_status_targets(current: TaskLifecycleStatus) -> frozenset[str]:
    """Return valid target statuses from *current* for error reporting."""

    targets = {
        transition.target
        for transition in task_lifecycle_machine.transitions
        if current in task_lifecycle_machine.transition_source_states(transition)
    }
    return frozenset(targets)


__all__ = [
    "TaskLifecycleAction",
    "TaskLifecycleStatus",
    "TaskStatusTarget",
    "task_lifecycle_actions",
    "task_lifecycle_machine",
    "task_lifecycle_statuses",
    "terminal_task_lifecycle_statuses",
    "valid_task_status_targets",
    "validate_task_status_transition",
]
