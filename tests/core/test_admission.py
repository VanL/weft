"""Pure admission policy contracts [MA-1.8], [MANAGER.18].

Explicit timestamps isolate resource-progress budgets from the reactor clock.
Manager tests separately prove that real observation and reap paths supply them.
"""

from __future__ import annotations

from typing import Any

import pytest

from weft.core.admission import AdmissionController, _admission_capacity


@pytest.mark.parametrize(
    ("used", "expected"),
    [
        (
            6,
            {
                "used": 6,
                "reserve": 3,
                "public_limit": 7,
                "internal_limit": 10,
                "public_allowed": True,
                "internal_allowed": True,
            },
        ),
        (
            7,
            {
                "used": 7,
                "reserve": 3,
                "public_limit": 7,
                "internal_limit": 10,
                "public_allowed": False,
                "internal_allowed": True,
            },
        ),
        (
            9,
            {
                "used": 9,
                "reserve": 3,
                "public_limit": 7,
                "internal_limit": 10,
                "public_allowed": False,
                "internal_allowed": True,
            },
        ),
        (
            10,
            {
                "used": 10,
                "reserve": 3,
                "public_limit": 7,
                "internal_limit": 10,
                "public_allowed": False,
                "internal_allowed": False,
            },
        ),
    ],
)
def test_admission_capacity_uses_strict_lane_limits(
    used: int,
    expected: dict[str, Any],
) -> None:
    assert (
        _admission_capacity(
            used=used,
            max_connections=10,
            reserve_fraction=0.1,
            liveness_monitor_enabled=False,
        )
        == expected
    )


def test_admission_capacity_applies_service_floor_and_fractional_ceiling() -> None:
    assert _admission_capacity(
        used=0,
        max_connections=2,
        reserve_fraction=0.0,
        liveness_monitor_enabled=False,
    ) == {
        "used": 0,
        "reserve": 3,
        "public_limit": 0,
        "internal_limit": 2,
        "public_allowed": False,
        "internal_allowed": True,
    }
    assert (
        _admission_capacity(
            used=15,
            max_connections=20,
            reserve_fraction=0.21,
            liveness_monitor_enabled=True,
        )["reserve"]
        == 5
    )


def test_admission_capacity_reserves_four_slots_for_liveness_monitor() -> None:
    assert (
        _admission_capacity(
            used=0,
            max_connections=10,
            reserve_fraction=0.0,
            liveness_monitor_enabled=True,
        )["reserve"]
        == 4
    )
    assert (
        _admission_capacity(
            used=0,
            max_connections=10,
            reserve_fraction=0.0,
            liveness_monitor_enabled=False,
        )["reserve"]
        == 3
    )


@pytest.fixture
def admission() -> AdmissionController:
    return AdmissionController(10, 0.1, False)


@pytest.mark.parametrize(
    ("used", "blocked"),
    [
        (6, frozenset()),
        (7, frozenset({"public"})),
        (10, frozenset({"public", "internal"})),
        (None, frozenset({"public", "internal"})),
    ],
)
def test_admission_decision_preserves_lane_policy(
    admission: AdmissionController,
    used: int | None,
    blocked: frozenset[str],
) -> None:
    decision = admission.evaluate(used=used, backend="postgres", now=100.0)
    assert decision.blocked_lanes == blocked
    if used is None:
        assert decision.capacity is None
    else:
        assert decision.capacity is not None
        assert decision.capacity["used"] == used
    if blocked:
        assert decision.wait_phase == "waiting"
        assert decision.elapsed_seconds == decision.idle_seconds == 0.0
    else:
        assert decision.wait_phase is None
        assert decision.elapsed_seconds is None
        assert decision.idle_seconds is None


@pytest.mark.parametrize(("age", "phase"), [(29.999, "waiting"), (30.0, "stalled")])
def test_admission_idle_budget_expires_at_thirty_seconds(
    admission: AdmissionController, age: float, phase: str
) -> None:
    admission.evaluate(used=10, backend="postgres", now=100.0)
    decision = admission.evaluate(used=10, backend="postgres", now=100.0 + age)
    assert decision.wait_phase == phase
    assert decision.elapsed_seconds == pytest.approx(age)
    assert decision.idle_seconds == pytest.approx(age)
    assert decision.blocked_lanes == frozenset({"public", "internal"})


def test_admission_count_progress_extends_idle_but_not_absolute_budget(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=16, backend="postgres", now=0.0)
    for now, used in [
        (25.0, 15),
        (50.0, 14),
        (75.0, 13),
        (100.0, 12),
        (125.0, 11),
        (150.0, 10),
    ]:
        decision = admission.evaluate(used=used, backend="postgres", now=now)
        assert decision.wait_phase == "waiting"
        assert decision.idle_seconds == 0.0
        assert decision.elapsed_seconds == now
    assert (
        admission.evaluate(used=10, backend="postgres", now=179.999).wait_phase
        == "waiting"
    )
    decision = admission.evaluate(used=9, backend="postgres", now=180.0)
    assert decision.wait_phase == "stalled"
    assert decision.elapsed_seconds == 180.0
    assert decision.idle_seconds == 0.0
    assert decision.blocked_lanes == frozenset({"public"})


def test_admission_first_known_baseline_does_not_renew_unknown_wait(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=None, backend="postgres", now=100.0)
    decision = admission.evaluate(used=10, backend="postgres", now=129.0)
    assert decision.idle_seconds == 29.0
    assert decision.elapsed_seconds == 29.0
    assert (
        admission.evaluate(used=10, backend="postgres", now=130.0).wait_phase
        == "stalled"
    )
    decision = admission.evaluate(used=9, backend="postgres", now=131.0)
    assert decision.wait_phase == "waiting"
    assert decision.idle_seconds == 0.0
    assert decision.elapsed_seconds == 31.0


def test_admission_unknown_samples_preserve_progress_and_low_water(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=10, backend="postgres", now=0.0)
    admission.evaluate(used=9, backend="postgres", now=10.0)
    decision = admission.evaluate(used=None, backend="postgres", now=39.0)
    assert decision.wait_phase == "waiting"
    assert decision.idle_seconds == 29.0
    assert decision.capacity is None
    decision = admission.evaluate(used=9, backend="postgres", now=40.0)
    assert decision.wait_phase == "stalled"
    assert decision.idle_seconds == 30.0
    assert decision.elapsed_seconds == 40.0


def test_admission_oscillation_above_episode_low_is_not_progress(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=10, backend="postgres", now=0.0)
    admission.evaluate(used=9, backend="postgres", now=10.0)
    admission.evaluate(used=11, backend="postgres", now=20.0)
    decision = admission.evaluate(used=10, backend="postgres", now=40.0)
    assert decision.wait_phase == "stalled"
    assert decision.idle_seconds == 30.0
    decision = admission.evaluate(used=8, backend="postgres", now=41.0)
    assert decision.wait_phase == "waiting"
    assert decision.idle_seconds == 0.0
    assert decision.elapsed_seconds == 41.0


def test_admission_reap_revives_idle_stall_without_renewing_total_budget(
    admission: AdmissionController,
) -> None:
    admission.note_child_reap(now=5.0)
    admission.evaluate(used=10, backend="postgres", now=10.0)
    assert (
        admission.evaluate(used=10, backend="postgres", now=40.0).wait_phase
        == "stalled"
    )
    admission.note_child_reap(now=41.0)
    decision = admission.evaluate(used=10, backend="postgres", now=42.0)
    assert decision.wait_phase == "waiting"
    assert decision.idle_seconds == 1.0
    assert decision.elapsed_seconds == 32.0
    admission.note_child_reap(now=129.0)
    decision = admission.evaluate(used=10, backend="postgres", now=130.0)
    assert decision.wait_phase == "waiting"
    assert decision.idle_seconds == 1.0
    assert decision.elapsed_seconds == 120.0
    admission.note_child_reap(now=189.0)
    decision = admission.evaluate(used=10, backend="postgres", now=190.0)
    assert decision.wait_phase == "stalled"
    assert decision.idle_seconds == 1.0
    assert decision.elapsed_seconds == 180.0
    admission.note_child_reap(now=191.0)
    assert (
        admission.evaluate(used=9, backend="postgres", now=192.0).wait_phase
        == "stalled"
    )


@pytest.mark.parametrize("expiry", [30.0, 180.0])
def test_admission_fresh_both_open_capacity_wins_at_expiry(
    admission: AdmissionController, expiry: float
) -> None:
    admission.evaluate(used=10, backend="postgres", now=0.0)
    decision = admission.evaluate(used=6, backend="postgres", now=expiry)
    assert decision.blocked_lanes == frozenset()
    assert decision.wait_phase is None
    assert decision.elapsed_seconds is None
    assert decision.idle_seconds is None
    admission.note_child_reap(now=expiry + 10.0)
    decision = admission.evaluate(used=10, backend="postgres", now=expiry + 20.0)
    assert decision.wait_phase == "waiting"
    assert decision.elapsed_seconds == decision.idle_seconds == 0.0


def test_admission_internal_capacity_does_not_end_public_wait(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=10, backend="postgres", now=0.0)
    decision = admission.evaluate(used=9, backend="postgres", now=25.0)
    assert decision.blocked_lanes == frozenset({"public"})
    decision = admission.evaluate(used=9, backend="postgres", now=55.0)
    assert decision.wait_phase == "stalled"
    assert decision.elapsed_seconds == 55.0
    assert decision.idle_seconds == 30.0


def test_admission_empty_source_reset_starts_a_new_episode(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=10, backend="postgres", now=0.0)
    assert (
        admission.evaluate(used=10, backend="postgres", now=180.0).wait_phase
        == "stalled"
    )
    admission.reset_wait()
    admission.note_child_reap(now=190.0)
    decision = admission.evaluate(used=11, backend="postgres", now=200.0)
    assert decision.wait_phase == "waiting"
    assert decision.elapsed_seconds == decision.idle_seconds == 0.0
    decision = admission.evaluate(used=10, backend="postgres", now=225.0)
    assert decision.idle_seconds == 0.0
    assert decision.elapsed_seconds == 25.0


def test_admission_sqlite_never_starts_progress_assessment(
    admission: AdmissionController,
) -> None:
    for now, used in [(0.0, 10), (30.0, 9), (120.0, None)]:
        decision = admission.evaluate(used=used, backend="sqlite", now=now)
        admission.note_child_reap(now=now + 1.0)
        assert decision.wait_phase is None
        assert decision.elapsed_seconds is None
        assert decision.idle_seconds is None
    decision = admission.evaluate(used=10, backend="postgres", now=150.0)
    assert decision.wait_phase == "waiting"
    assert decision.elapsed_seconds == decision.idle_seconds == 0.0
