"""Pure command-control convergence decisions.

This module classifies already-observed control evidence. It does not read
queues, inspect processes, write control messages, or call runner plugins.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.4], [CC-2.5]
- docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]
- docs/specifications/07-System_Invariants.md [STATE.1]-[STATE.6]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from weft._constants import (
    CONTROL_CONVERGENCE_ACTION_VALUES,
    CONTROL_CONVERGENCE_STATE_VALUES,
    CONTROL_KILL,
    TERMINAL_CONTROL_CONVERGENCE_STATE_VALUES,
)
from weft.core.state_machines import StateDecision, StateMachine, Transition

ControlConvergenceState = Literal[
    "command_sent",
    "accepted",
    "terminal_observed",
    "runtime_dead_after_control",
    "escalating_runner",
    "escalating_host",
    "unknown",
]
ControlConvergenceAction = Literal[
    "wait",
    "accept_terminal",
    "accept_dead_runtime",
    "escalate_runner",
    "escalate_host",
    "report_unknown",
]
control_convergence_states = cast(
    frozenset[ControlConvergenceState],
    CONTROL_CONVERGENCE_STATE_VALUES,
)
terminal_control_convergence_states = cast(
    frozenset[ControlConvergenceState],
    TERMINAL_CONTROL_CONVERGENCE_STATE_VALUES,
)
control_convergence_actions = cast(
    frozenset[ControlConvergenceAction],
    CONTROL_CONVERGENCE_ACTION_VALUES,
)


@dataclass(frozen=True, slots=True)
class ControlConvergenceEvidence:
    """Evidence snapshot for command-layer STOP/KILL convergence."""

    command: str
    ack_seen: bool = False
    terminal_status: str | None = None
    runtime_dead_after_control: bool | None = None
    runner_fallback_attempted: bool = False
    host_fallback_attempted: bool = False
    observation_budget_expired: bool = False


def reduce_control_convergence(
    current: ControlConvergenceState,
    evidence: ControlConvergenceEvidence,
) -> StateDecision[ControlConvergenceState, ControlConvergenceAction]:
    """Return the deterministic command-control convergence decision."""

    return control_convergence_machine.decide(current, evidence)


def _accepts_terminal_status(
    _current: ControlConvergenceState,
    evidence: ControlConvergenceEvidence,
) -> bool:
    if evidence.terminal_status is None:
        return False
    return evidence.command != CONTROL_KILL or evidence.terminal_status == "killed"


def _accepts_dead_runtime(
    _current: ControlConvergenceState,
    evidence: ControlConvergenceEvidence,
) -> bool:
    return (
        evidence.command == CONTROL_KILL and evidence.runtime_dead_after_control is True
    )


def _should_wait(
    _current: ControlConvergenceState,
    evidence: ControlConvergenceEvidence,
) -> bool:
    return evidence.ack_seen and not evidence.observation_budget_expired


def _should_wait_for_evidence(
    _current: ControlConvergenceState,
    evidence: ControlConvergenceEvidence,
) -> bool:
    return not evidence.ack_seen and not evidence.observation_budget_expired


def _should_escalate_runner(
    _current: ControlConvergenceState,
    evidence: ControlConvergenceEvidence,
) -> bool:
    return (
        evidence.observation_budget_expired and not evidence.runner_fallback_attempted
    )


def _should_escalate_host(
    _current: ControlConvergenceState,
    evidence: ControlConvergenceEvidence,
) -> bool:
    return (
        evidence.command == CONTROL_KILL
        and evidence.observation_budget_expired
        and evidence.runner_fallback_attempted
        and not evidence.host_fallback_attempted
        and evidence.runtime_dead_after_control is not True
    )


def _should_report_unknown(
    _current: ControlConvergenceState,
    evidence: ControlConvergenceEvidence,
) -> bool:
    return evidence.observation_budget_expired


control_convergence_machine = StateMachine[
    ControlConvergenceState,
    ControlConvergenceEvidence,
    ControlConvergenceAction,
](
    states=control_convergence_states,
    actions=control_convergence_actions,
    terminal_states=terminal_control_convergence_states,
    transitions=(
        Transition(
            id="terminal-observed-from-command-sent",
            source="command_sent",
            target="terminal_observed",
            action="accept_terminal",
            predicate=_accepts_terminal_status,
            reason="terminal status satisfies the command result",
        ),
        Transition(
            id="terminal-observed-from-accepted",
            source="accepted",
            target="terminal_observed",
            action="accept_terminal",
            predicate=_accepts_terminal_status,
            reason="terminal status satisfies the accepted command",
        ),
        Transition(
            id="terminal-observed-from-runner-escalation",
            source="escalating_runner",
            target="terminal_observed",
            action="accept_terminal",
            predicate=_accepts_terminal_status,
            reason="terminal status appeared after runner escalation",
        ),
        Transition(
            id="terminal-observed-from-host-escalation",
            source="escalating_host",
            target="terminal_observed",
            action="accept_terminal",
            predicate=_accepts_terminal_status,
            reason="terminal status appeared after host escalation",
        ),
        Transition(
            id="runtime-dead-from-command-sent",
            source="command_sent",
            target="runtime_dead_after_control",
            action="accept_dead_runtime",
            predicate=_accepts_dead_runtime,
            reason="runtime death proves the control command succeeded",
        ),
        Transition(
            id="runtime-dead-from-accepted",
            source="accepted",
            target="runtime_dead_after_control",
            action="accept_dead_runtime",
            predicate=_accepts_dead_runtime,
            reason="runtime death proves the accepted control command succeeded",
        ),
        Transition(
            id="runtime-dead-from-runner-escalation",
            source="escalating_runner",
            target="runtime_dead_after_control",
            action="accept_dead_runtime",
            predicate=_accepts_dead_runtime,
            reason="runtime death appeared after runner escalation",
        ),
        Transition(
            id="runtime-dead-from-host-escalation",
            source="escalating_host",
            target="runtime_dead_after_control",
            action="accept_dead_runtime",
            predicate=_accepts_dead_runtime,
            reason="runtime death appeared after host escalation",
        ),
        Transition(
            id="command-sent-waits-for-evidence",
            source="command_sent",
            target="command_sent",
            action="wait",
            predicate=_should_wait_for_evidence,
            reason="observation budget remains open",
        ),
        Transition(
            id="accepted-command-waits",
            source=frozenset(("command_sent", "accepted")),
            target="accepted",
            action="wait",
            predicate=_should_wait,
            reason="control acknowledgement seen within the observation budget",
        ),
        Transition(
            id="command-timeout-escalates-runner",
            source=frozenset(("command_sent", "accepted")),
            target="escalating_runner",
            action="escalate_runner",
            predicate=_should_escalate_runner,
            reason="no sufficient terminal proof arrived before the wait budget",
        ),
        Transition(
            id="runner-timeout-escalates-host",
            source="escalating_runner",
            target="escalating_host",
            action="escalate_host",
            predicate=_should_escalate_host,
            reason="runner fallback did not produce terminal or runtime-dead proof",
        ),
        Transition(
            id="host-timeout-reports-unknown",
            source="escalating_host",
            target="unknown",
            action="report_unknown",
            predicate=_should_report_unknown,
            reason="host fallback did not produce terminal or runtime-dead proof",
        ),
        Transition(
            id="runner-timeout-reports-unknown",
            source="escalating_runner",
            target="unknown",
            action="report_unknown",
            predicate=_should_report_unknown,
            reason="runner fallback did not produce sufficient proof",
        ),
    ),
)

control_convergence_machine.assert_all_states_reachable(("command_sent",))


__all__ = [
    "ControlConvergenceAction",
    "ControlConvergenceEvidence",
    "ControlConvergenceState",
    "control_convergence_actions",
    "control_convergence_machine",
    "control_convergence_states",
    "reduce_control_convergence",
    "terminal_control_convergence_states",
]
