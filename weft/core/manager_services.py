"""Shared manager-owned service supervision primitives.

Manager-owned singleton services use one contract for built-ins such as the
heartbeat and TaskMonitor and for autostart-managed work.  The Manager remains
the only component that launches these services; public helpers may discover
published endpoints but must not bypass Manager dispatch.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3]
- docs/specifications/03-Manager_Architecture.md [MA-1.5], [MA-1.6]
- docs/specifications/05-Message_Flow_and_State.md [MF-3.2], [MF-6], [MF-7]
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from weft._constants import (
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    MANAGED_SERVICE_UNCERTAIN_RETRY_LIMIT,
)

ServiceLifecycle = Literal["once", "ensure"]
ServiceCandidateState = Literal["live", "terminal", "uncertain"]
ManagedServiceAction = Literal[
    "keep_live",
    "wait_pending",
    "wait_uncertain",
    "degraded_wait",
    "schedule_restart",
    "start_now",
    "suppress_once",
    "suppress_max_restarts",
]


@dataclass(frozen=True, slots=True)
class ManagedServiceSpec:
    """Desired service declaration produced by Manager supervision code.

    ``spawn_payload`` is the complete manager spawn envelope. It should be
    writable to the manager inbox without further per-service mutation.
    """

    key: str
    lifecycle: ServiceLifecycle
    spawn_payload: dict[str, Any]
    restart_backoff_ns: int = 0
    max_restarts: int | None = None
    autostart_source: str | None = None


@dataclass(slots=True)
class ManagedServiceState:
    """Mutable Manager-local state for one desired service key."""

    spawn_pending: bool = False
    active_tid: str | None = None
    next_allowed_ns: int = 0
    launched_once: bool = False
    restarts: int = 0
    uncertain_attempts: int = 0
    uncertain_since_ns: int | None = None
    last_uncertain_reason: str | None = None
    locally_terminal_tids: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class ServiceCandidate:
    """Observed possible owner for a manager-supervised service."""

    key: str
    tid: str
    state: ServiceCandidateState
    source: str
    timestamp: int | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ManagedServiceEvidence:
    """Pure evidence snapshot for one desired manager-owned service."""

    pending_spawn: bool = False
    candidates: tuple[ServiceCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class ManagedServiceCandidateSummary:
    """Order-insensitive reduction of observed service candidates."""

    candidates: tuple[ServiceCandidate, ...]
    terminal_tids: frozenset[str]
    canonical_live: ServiceCandidate | None
    uncertain: tuple[ServiceCandidate, ...]


@dataclass(frozen=True, slots=True)
class ManagedServiceDecision:
    """Pure transition decision for one manager-owned service."""

    action: ManagedServiceAction
    state: ManagedServiceState
    canonical_live: ServiceCandidate | None = None
    terminal_tids: frozenset[str] = frozenset()
    uncertain: tuple[ServiceCandidate, ...] = ()
    reason: str | None = None


def service_metadata(
    *,
    key: str,
    lifecycle: ServiceLifecycle,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return reserved metadata for a manager-owned service."""

    metadata = dict(extra or {})
    metadata[INTERNAL_SERVICE_KEY_METADATA_KEY] = key
    metadata[INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY] = lifecycle
    return metadata


def apply_service_metadata(
    taskspec_payload: dict[str, Any],
    *,
    key: str,
    lifecycle: ServiceLifecycle,
) -> dict[str, Any]:
    """Return a copied TaskSpec payload with reserved service metadata added."""

    candidate = copy.deepcopy(taskspec_payload)
    metadata = candidate.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        candidate["metadata"] = metadata
    metadata[INTERNAL_SERVICE_KEY_METADATA_KEY] = key
    metadata[INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY] = lifecycle
    return candidate


def service_key_from_metadata(metadata: Any) -> str | None:
    """Extract the canonical service key from TaskSpec metadata, if present."""

    if not isinstance(metadata, dict):
        return None
    key = metadata.get(INTERNAL_SERVICE_KEY_METADATA_KEY)
    return key if isinstance(key, str) and key else None


def select_canonical_live_candidate(
    candidates: Sequence[ServiceCandidate],
) -> ServiceCandidate | None:
    """Return the deterministic canonical live service candidate.

    Singleton ownership follows the same deterministic rule as managers:
    among live candidates, the lowest TID wins.
    """

    live = [candidate for candidate in candidates if candidate.state == "live"]
    if not live:
        return None
    return min(live, key=lambda candidate: candidate.tid)


def summarize_service_candidates(
    candidates: Sequence[ServiceCandidate],
) -> ManagedServiceCandidateSummary:
    """Summarize candidates with terminal proof winning per TID."""

    grouped: dict[str, list[ServiceCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.tid, []).append(candidate)

    reduced: list[ServiceCandidate] = []
    for tid_candidates in grouped.values():
        terminal = [
            candidate for candidate in tid_candidates if candidate.state == "terminal"
        ]
        if terminal:
            reduced.append(max(terminal, key=_candidate_timestamp_key))
            continue

        live = [candidate for candidate in tid_candidates if candidate.state == "live"]
        if live:
            reduced.append(max(live, key=_candidate_timestamp_key))
            continue

        uncertain = [
            candidate for candidate in tid_candidates if candidate.state == "uncertain"
        ]
        if uncertain:
            reduced.append(max(uncertain, key=_candidate_timestamp_key))

    terminal_tids = frozenset(
        candidate.tid for candidate in reduced if candidate.state == "terminal"
    )
    live_candidates = [
        candidate
        for candidate in reduced
        if candidate.state == "live" and candidate.tid not in terminal_tids
    ]
    uncertain_candidates = tuple(
        sorted(
            (candidate for candidate in reduced if candidate.state == "uncertain"),
            key=lambda candidate: candidate.tid,
        )
    )
    return ManagedServiceCandidateSummary(
        candidates=tuple(sorted(reduced, key=lambda candidate: candidate.tid)),
        terminal_tids=terminal_tids,
        canonical_live=select_canonical_live_candidate(live_candidates),
        uncertain=uncertain_candidates,
    )


def reduce_managed_service_state(
    service: ManagedServiceSpec,
    state: ManagedServiceState,
    evidence: ManagedServiceEvidence,
    *,
    now_ns: int,
    uncertain_retry_limit: int = MANAGED_SERVICE_UNCERTAIN_RETRY_LIMIT,
) -> ManagedServiceDecision:
    """Return the deterministic transition for one desired service.

    The reducer is pure: it does not read queues, inspect processes, write
    logs, or enqueue spawn requests. Manager owns all side effects.
    """

    next_state = _copy_service_state(state)
    summary = summarize_service_candidates(evidence.candidates)
    next_state.locally_terminal_tids.update(summary.terminal_tids)

    terminal_active = (
        next_state.active_tid
        if next_state.active_tid in summary.terminal_tids
        else None
    )
    if terminal_active is not None:
        next_state.active_tid = None
        next_state.spawn_pending = False
        if service.lifecycle == "ensure" and service.restart_backoff_ns > 0:
            next_state.next_allowed_ns = max(
                next_state.next_allowed_ns,
                now_ns + service.restart_backoff_ns,
            )
        elif service.lifecycle != "ensure":
            next_state.next_allowed_ns = 0

    if summary.canonical_live is not None:
        next_state.active_tid = summary.canonical_live.tid
        next_state.spawn_pending = False
        _reset_uncertainty(next_state)
        return ManagedServiceDecision(
            action="keep_live",
            state=next_state,
            canonical_live=summary.canonical_live,
            terminal_tids=summary.terminal_tids,
            reason="canonical live service candidate exists",
        )

    if evidence.pending_spawn:
        next_state.spawn_pending = True
        _reset_uncertainty(next_state)
        return ManagedServiceDecision(
            action="wait_pending",
            state=next_state,
            terminal_tids=summary.terminal_tids,
            reason="durable spawn request is pending",
        )

    if next_state.spawn_pending:
        next_state.spawn_pending = False

    if summary.uncertain:
        next_state.uncertain_attempts += 1
        next_state.uncertain_since_ns = next_state.uncertain_since_ns or now_ns
        next_state.last_uncertain_reason = summary.uncertain[0].reason
        action: ManagedServiceAction = (
            "wait_uncertain"
            if next_state.uncertain_attempts <= uncertain_retry_limit
            else "degraded_wait"
        )
        return ManagedServiceDecision(
            action=action,
            state=next_state,
            terminal_tids=summary.terminal_tids,
            uncertain=summary.uncertain,
            reason=summary.uncertain[0].reason,
        )

    _reset_uncertainty(next_state)

    if service.lifecycle == "once" and next_state.launched_once:
        return ManagedServiceDecision(
            action="suppress_once",
            state=next_state,
            terminal_tids=summary.terminal_tids,
            reason="once service already launched",
        )

    if (
        service.lifecycle == "ensure"
        and service.max_restarts is not None
        and next_state.launched_once
        and next_state.restarts >= service.max_restarts
    ):
        return ManagedServiceDecision(
            action="suppress_max_restarts",
            state=next_state,
            terminal_tids=summary.terminal_tids,
            reason="restart budget exhausted",
        )

    if next_state.next_allowed_ns and now_ns < next_state.next_allowed_ns:
        return ManagedServiceDecision(
            action="schedule_restart",
            state=next_state,
            terminal_tids=summary.terminal_tids,
            reason="restart backoff has not elapsed",
        )

    return ManagedServiceDecision(
        action="start_now",
        state=next_state,
        terminal_tids=summary.terminal_tids,
        reason="no live, pending, or lifecycle blocker",
    )


def _copy_service_state(state: ManagedServiceState) -> ManagedServiceState:
    """Return a detached copy of mutable service state."""

    return ManagedServiceState(
        spawn_pending=state.spawn_pending,
        active_tid=state.active_tid,
        next_allowed_ns=state.next_allowed_ns,
        launched_once=state.launched_once,
        restarts=state.restarts,
        uncertain_attempts=state.uncertain_attempts,
        uncertain_since_ns=state.uncertain_since_ns,
        last_uncertain_reason=state.last_uncertain_reason,
        locally_terminal_tids=set(state.locally_terminal_tids),
    )


def _reset_uncertainty(state: ManagedServiceState) -> None:
    state.uncertain_attempts = 0
    state.uncertain_since_ns = None
    state.last_uncertain_reason = None


def _candidate_timestamp_key(candidate: ServiceCandidate) -> tuple[int, str]:
    return (
        candidate.timestamp if candidate.timestamp is not None else -1,
        candidate.tid,
    )


__all__ = [
    "ManagedServiceAction",
    "ManagedServiceCandidateSummary",
    "ManagedServiceDecision",
    "ManagedServiceEvidence",
    "ManagedServiceSpec",
    "ManagedServiceState",
    "ServiceCandidate",
    "ServiceCandidateState",
    "ServiceLifecycle",
    "apply_service_metadata",
    "reduce_managed_service_state",
    "select_canonical_live_candidate",
    "service_key_from_metadata",
    "service_metadata",
    "summarize_service_candidates",
]
