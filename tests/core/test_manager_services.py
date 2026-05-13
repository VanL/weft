"""Tests for manager-owned service transition reduction."""

from __future__ import annotations

import pytest

from weft.core.manager_services import (
    ManagedServiceAction,
    ManagedServiceEvidence,
    ManagedServiceSpec,
    ManagedServiceState,
    ServiceCandidate,
    ServiceCandidateState,
    ServiceLifecycle,
    reduce_managed_service_state,
)

pytestmark = [pytest.mark.shared]

SERVICE_KEY = "_weft.test.service"
NOW_NS = 1_000_000_000


def _service(
    *,
    lifecycle: ServiceLifecycle = "ensure",
    restart_backoff_ns: int = 0,
    max_restarts: int | None = None,
) -> ManagedServiceSpec:
    return ManagedServiceSpec(
        key=SERVICE_KEY,
        lifecycle=lifecycle,
        spawn_payload={},
        restart_backoff_ns=restart_backoff_ns,
        max_restarts=max_restarts,
    )


def _candidate(
    tid: str,
    state: ServiceCandidateState,
    *,
    timestamp: int | None = None,
    reason: str | None = None,
) -> ServiceCandidate:
    return ServiceCandidate(
        key=SERVICE_KEY,
        tid=tid,
        state=state,
        source="test",
        timestamp=timestamp,
        reason=reason,
    )


def test_terminal_proof_for_same_tid_beats_live_evidence() -> None:
    state = ManagedServiceState(active_tid="10", launched_once=True)
    evidence = ManagedServiceEvidence(
        candidates=(
            _candidate("10", "live", timestamp=20),
            _candidate("10", "terminal", timestamp=10),
        )
    )

    decision = reduce_managed_service_state(
        _service(),
        state,
        evidence,
        now_ns=NOW_NS,
    )

    assert decision.action == "start_now"
    assert decision.state.active_tid is None
    assert "10" in decision.terminal_tids


def test_old_terminal_tid_does_not_hide_newer_live_tid() -> None:
    evidence = ManagedServiceEvidence(
        candidates=(
            _candidate("10", "terminal", timestamp=10),
            _candidate("20", "live", timestamp=20),
        )
    )

    decision = reduce_managed_service_state(
        _service(),
        ManagedServiceState(),
        evidence,
        now_ns=NOW_NS,
    )

    assert decision.action == "keep_live"
    assert decision.canonical_live is not None
    assert decision.canonical_live.tid == "20"
    assert decision.state.active_tid == "20"


def test_pending_spawn_blocks_duplicate_start() -> None:
    decision = reduce_managed_service_state(
        _service(),
        ManagedServiceState(),
        ManagedServiceEvidence(pending_spawn=True),
        now_ns=NOW_NS,
    )

    assert decision.action == "wait_pending"
    assert decision.state.spawn_pending is True


def test_active_tid_without_live_or_terminal_evidence_waits_uncertain() -> None:
    decision = reduce_managed_service_state(
        _service(),
        ManagedServiceState(active_tid="10", launched_once=True),
        ManagedServiceEvidence(),
        now_ns=NOW_NS,
    )

    assert decision.action == "wait_uncertain"
    assert decision.state.active_tid == "10"


def test_local_spawn_pending_does_not_block_forever_without_durable_pending() -> None:
    decision = reduce_managed_service_state(
        _service(),
        ManagedServiceState(spawn_pending=True),
        ManagedServiceEvidence(pending_spawn=False),
        now_ns=NOW_NS,
    )

    assert decision.action == "start_now"
    assert decision.state.spawn_pending is False


def test_once_service_suppresses_restart_after_first_launch() -> None:
    decision = reduce_managed_service_state(
        _service(lifecycle="once"),
        ManagedServiceState(launched_once=True),
        ManagedServiceEvidence(),
        now_ns=NOW_NS,
    )

    assert decision.action == "suppress_once"


def test_ensure_service_waits_for_backoff_before_restart() -> None:
    decision = reduce_managed_service_state(
        _service(),
        ManagedServiceState(launched_once=True, next_allowed_ns=NOW_NS + 1),
        ManagedServiceEvidence(),
        now_ns=NOW_NS,
    )

    assert decision.action == "schedule_restart"


def test_terminal_backoff_uses_manager_observation_time_not_broker_timestamp() -> None:
    service = _service(restart_backoff_ns=100)
    evidence = ManagedServiceEvidence(
        candidates=(_candidate("10", "terminal", timestamp=NOW_NS + 10_000_000_000),)
    )

    first = reduce_managed_service_state(
        service,
        ManagedServiceState(active_tid="10", launched_once=True),
        evidence,
        now_ns=NOW_NS,
    )
    second = reduce_managed_service_state(
        service,
        first.state,
        evidence,
        now_ns=NOW_NS + 101,
    )

    assert first.action == "schedule_restart"
    assert first.state.next_allowed_ns == NOW_NS + 100
    assert second.action == "start_now"


def test_ensure_service_suppresses_after_max_restarts() -> None:
    decision = reduce_managed_service_state(
        _service(max_restarts=1),
        ManagedServiceState(launched_once=True, restarts=1),
        ManagedServiceEvidence(),
        now_ns=NOW_NS,
    )

    assert decision.action == "suppress_max_restarts"


def test_uncertain_evidence_waits_then_degrades_without_starting() -> None:
    evidence = ManagedServiceEvidence(
        candidates=(_candidate("10", "uncertain", reason="probe failed"),)
    )

    first = reduce_managed_service_state(
        _service(),
        ManagedServiceState(),
        evidence,
        now_ns=NOW_NS,
        uncertain_retry_limit=1,
    )
    second = reduce_managed_service_state(
        _service(),
        first.state,
        evidence,
        now_ns=NOW_NS + 1,
        uncertain_retry_limit=1,
    )

    assert first.action == "wait_uncertain"
    assert second.action == "degraded_wait"
    assert second.state.last_uncertain_reason == "probe failed"


def test_successful_no_pong_stale_evidence_can_start_when_lifecycle_allows() -> None:
    evidence = ManagedServiceEvidence(
        candidates=(
            _candidate(
                "10",
                "terminal",
                reason="non-terminal state without live runtime proof or PONG",
            ),
        )
    )

    decision = reduce_managed_service_state(
        _service(),
        ManagedServiceState(launched_once=True),
        evidence,
        now_ns=NOW_NS,
    )

    assert decision.action == "start_now"


def test_candidate_order_does_not_change_decision() -> None:
    candidates = (
        _candidate("30", "live", timestamp=30),
        _candidate("10", "terminal", timestamp=10),
        _candidate("20", "live", timestamp=20),
    )

    forward = reduce_managed_service_state(
        _service(),
        ManagedServiceState(),
        ManagedServiceEvidence(candidates=candidates),
        now_ns=NOW_NS,
    )
    reversed_order = reduce_managed_service_state(
        _service(),
        ManagedServiceState(),
        ManagedServiceEvidence(candidates=tuple(reversed(candidates))),
        now_ns=NOW_NS,
    )

    assert forward.action == reversed_order.action == "keep_live"
    assert forward.canonical_live is not None
    assert reversed_order.canonical_live is not None
    assert forward.canonical_live.tid == reversed_order.canonical_live.tid == "20"


def test_all_managed_service_actions_have_table_coverage() -> None:
    """Lock the reducer's action space to explicit evidence cases."""

    cases: tuple[
        tuple[
            str,
            ManagedServiceSpec,
            ManagedServiceState,
            ManagedServiceEvidence,
            ManagedServiceAction,
            int,
        ],
        ...,
    ] = (
        (
            "keep live owner",
            _service(),
            ManagedServiceState(),
            ManagedServiceEvidence(candidates=(_candidate("10", "live"),)),
            "keep_live",
            NOW_NS,
        ),
        (
            "wait pending spawn",
            _service(),
            ManagedServiceState(),
            ManagedServiceEvidence(pending_spawn=True),
            "wait_pending",
            NOW_NS,
        ),
        (
            "wait uncertain active",
            _service(),
            ManagedServiceState(active_tid="10", launched_once=True),
            ManagedServiceEvidence(),
            "wait_uncertain",
            NOW_NS,
        ),
        (
            "degrade uncertain evidence",
            _service(),
            ManagedServiceState(uncertain_attempts=1),
            ManagedServiceEvidence(candidates=(_candidate("10", "uncertain"),)),
            "degraded_wait",
            NOW_NS,
        ),
        (
            "schedule restart backoff",
            _service(),
            ManagedServiceState(launched_once=True, next_allowed_ns=NOW_NS + 1),
            ManagedServiceEvidence(),
            "schedule_restart",
            NOW_NS,
        ),
        (
            "start now",
            _service(),
            ManagedServiceState(),
            ManagedServiceEvidence(),
            "start_now",
            NOW_NS,
        ),
        (
            "suppress once",
            _service(lifecycle="once"),
            ManagedServiceState(launched_once=True),
            ManagedServiceEvidence(),
            "suppress_once",
            NOW_NS,
        ),
        (
            "suppress max restarts",
            _service(max_restarts=1),
            ManagedServiceState(launched_once=True, restarts=1),
            ManagedServiceEvidence(),
            "suppress_max_restarts",
            NOW_NS,
        ),
    )
    expected_actions: set[ManagedServiceAction] = {
        "keep_live",
        "wait_pending",
        "wait_uncertain",
        "degraded_wait",
        "schedule_restart",
        "start_now",
        "suppress_once",
        "suppress_max_restarts",
    }
    seen_actions: set[ManagedServiceAction] = set()

    for label, service, state, evidence, expected_action, now_ns in cases:
        decision = reduce_managed_service_state(
            service,
            state,
            evidence,
            now_ns=now_ns,
            uncertain_retry_limit=1,
        )
        assert decision.action == expected_action, label
        seen_actions.add(decision.action)

    assert seen_actions == expected_actions
