"""Pure admission policy contracts [MA-1.8], [MANAGER.18].

Explicit timestamps isolate resource-progress budgets from the reactor clock.
Manager tests separately prove that real observation and reap paths supply them.
"""

from __future__ import annotations

from typing import Any

import pytest

import weft.core.admission as admission_mod
from weft.core.admission import (
    AdmissionController,
    AdmissionLane,
    HostMemory,
    _admission_capacity,
    _memory_capacity,
    _startup_window_allows_launch,
    _startup_window_full,
)

# Host memory far below every limit, so the memory gate admits both lanes and
# permits the whole startup window. Tests of the backend gate use it.
AMPLE = HostMemory(total=10_000_000_000, available=10_000_000_000)


def _gate(limit: int, used: int) -> dict[str, Any]:
    return {"public_limit": limit, "used": used}


# Memory-gate scale: L_m=9,000,000,000 bytes, so H=7,200,000,000 and the
# window is min(6, max(1, floor(6(L-U)/1,800,000,000))).
_LM = 9_000_000_000
_HM = 7_200_000_000


@pytest.mark.parametrize(
    ("unfinished", "gates", "allowed"),
    [
        # L=270: H=216 and the window is min(6, max(1, floor((270-U)/9))).
        (5, (_gate(270, 215),), True),
        (6, (_gate(270, 215),), False),
        (5, (_gate(270, 216),), True),
        (6, (_gate(270, 216),), False),
        (4, (_gate(270, 217),), True),
        (5, (_gate(270, 217),), False),
        (2, (_gate(270, 243),), True),
        (3, (_gate(270, 243),), False),
        (1, (_gate(270, 252),), True),
        (2, (_gate(270, 252),), False),
        (1, (_gate(270, 253),), False),
        (1, (_gate(270, 269),), False),
        (1, (_gate(270, 300),), False),
        (0, (_gate(270, 269),), True),
        # H=floor(4L/5) must reach W=6 before any overlap.
        (1, (_gate(7, 0),), False),
        (1, (_gate(8, 0),), True),
        # L=11: H=8, the window is min(6, max(1, floor(2(11-U)))).
        (1, (_gate(11, 8),), True),
        (5, (_gate(11, 8),), True),
        (1, (_gate(11, 10),), True),
        (2, (_gate(11, 10),), False),
        # The same formula at byte scale for the memory gate.
        (5, (_gate(_LM, _HM),), True),
        (6, (_gate(_LM, _HM),), False),
        (4, (_gate(_LM, _HM + 1),), True),
        (5, (_gate(_LM, _HM + 1),), False),
        (1, (_gate(_LM, _LM - 1),), False),
        (0, (_gate(_LM, _LM - 1),), True),
        # Every gate that exists bounds the window; the smallest one decides,
        # whatever the order of the gates.
        (2, (_gate(270, 0), _gate(270, 243)), True),
        (3, (_gate(270, 0), _gate(270, 243)), False),
        (3, (_gate(270, 243), _gate(270, 0)), False),
        (3, (_gate(270, 0), _gate(_LM, _HM + 900_000_000)), False),
        (2, (_gate(270, 0), _gate(_LM, _HM + 900_000_000)), True),
        (1, (_gate(7, 0), _gate(_LM, 0)), False),
        # An unknown observation on any gate denies overlap; it never blocks
        # the first startup at this layer (the measured decision does).
        (1, (None,), False),
        (1, (_gate(270, 0), None), False),
        (1, (None, _gate(_LM, 0)), False),
        (0, (None,), True),
        (0, (_gate(270, 135), None), True),
    ],
)
def test_startup_window_is_the_smallest_measured_gate_window(
    unfinished: int,
    gates: tuple[dict[str, Any] | None, ...],
    allowed: bool,
) -> None:
    # Literal boundaries protect the 80%-capacity threshold, the six-startup
    # window, its proportional narrowing above the threshold and the minimum
    # over gates, independently of the implementation's constants.
    assert _startup_window_allows_launch(unfinished=unfinished, gates=gates) is allowed


def test_one_window_binding_bounds_readiness_and_the_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The readiness bound and the predicate read W from one module binding."""
    assert not _startup_window_full(5)
    assert _startup_window_full(6)
    monkeypatch.setattr(admission_mod, "MANAGER_STARTUP_FAST_PATH_WINDOW", 8)
    assert not _startup_window_full(7)
    assert _startup_window_full(8)
    # L=1000: H=800 >= W=8, so low usage permits the whole widened window.
    assert _startup_window_allows_launch(unfinished=7, gates=(_gate(1000, 0),))
    assert not _startup_window_allows_launch(unfinished=8, gates=(_gate(1000, 0),))


@pytest.mark.parametrize(
    ("total", "available", "fraction", "expected"),
    [
        # R_m=ceil(T*f); L_m=T-R_m; public needs U_m<L_m, internal U_m<T.
        (1000, 101, 0.1, (899, 100, 900, True, True)),
        (1000, 100, 0.1, (900, 100, 900, False, True)),
        (1000, 1, 0.1, (999, 100, 900, False, True)),
        (1000, 0, 0.1, (1000, 100, 900, False, False)),
        (1001, 102, 0.1, (899, 101, 900, True, True)),
        (1000, 1, 0.0, (999, 0, 1000, True, True)),
        (1000, 0, 0.0, (1000, 0, 1000, False, False)),
    ],
)
def test_memory_capacity_reserves_a_fraction_of_measured_host_memory(
    total: int,
    available: int,
    fraction: float,
    expected: tuple[int, int, int, bool, bool],
) -> None:
    used, reserve, public_limit, public_allowed, internal_allowed = expected
    assert _memory_capacity(
        memory=HostMemory(total=total, available=available),
        reserve_fraction=fraction,
    ) == {
        "used": used,
        "reserve": reserve,
        "public_limit": public_limit,
        "internal_limit": total,
        "public_allowed": public_allowed,
        "internal_allowed": internal_allowed,
    }


# T=1000 at f=0.1: L_m=900. Backend ceiling 10 at f=0.1: reserve 3, L=7.
_OPEN = HostMemory(total=1000, available=500)
_AT_RESERVE = HostMemory(total=1000, available=100)
_EXHAUSTED = HostMemory(total=1000, available=0)


@pytest.mark.parametrize(
    (
        "backend",
        "ceiling",
        "used",
        "memory",
        "blocked",
        "gate_count",
        "refusals",
    ),
    [
        # SQLite without a ceiling: memory is the only gate; TID usage is unused.
        ("sqlite", 0, None, _OPEN, set(), 1, {}),
        (
            "sqlite",
            0,
            None,
            _AT_RESERVE,
            {"public"},
            1,
            {"public": "measured_memory_exhausted"},
        ),
        (
            "sqlite",
            0,
            None,
            _EXHAUSTED,
            {"public", "internal"},
            1,
            {
                "public": "measured_memory_exhausted",
                "internal": "measured_memory_exhausted",
            },
        ),
        (
            "sqlite",
            0,
            None,
            None,
            {"public", "internal"},
            1,
            {
                "public": "admission_usage_unavailable",
                "internal": "admission_usage_unavailable",
            },
        ),
        # A positive SQLite ceiling adds its live/unknown-TID gate.
        (
            "sqlite",
            10,
            None,
            _OPEN,
            {"public", "internal"},
            2,
            {
                "public": "admission_usage_unavailable",
                "internal": "admission_usage_unavailable",
            },
        ),
        (
            "sqlite",
            10,
            7,
            _OPEN,
            {"public"},
            2,
            {"public": "measured_capacity_exhausted"},
        ),
        # PostgreSQL always has its connection gate, even before capacity is read.
        (
            "postgres",
            0,
            None,
            _OPEN,
            {"public", "internal"},
            2,
            {
                "public": "admission_usage_unavailable",
                "internal": "admission_usage_unavailable",
            },
        ),
        ("postgres", 10, 6, _OPEN, set(), 2, {}),
        (
            "postgres",
            10,
            6,
            _AT_RESERVE,
            {"public"},
            2,
            {"public": "measured_memory_exhausted"},
        ),
        (
            "postgres",
            10,
            7,
            _AT_RESERVE,
            {"public"},
            2,
            {"public": "measured_capacity_exhausted"},
        ),
        # Each lane names the gate that refuses it.
        (
            "postgres",
            10,
            7,
            _EXHAUSTED,
            {"public", "internal"},
            2,
            {
                "public": "measured_capacity_exhausted",
                "internal": "measured_memory_exhausted",
            },
        ),
        (
            "postgres",
            10,
            6,
            None,
            {"public", "internal"},
            2,
            {
                "public": "admission_usage_unavailable",
                "internal": "admission_usage_unavailable",
            },
        ),
    ],
)
def test_admission_requires_every_existing_gate_and_names_the_refusing_gate(
    backend: str,
    ceiling: int,
    used: int | None,
    memory: HostMemory | None,
    blocked: set[AdmissionLane],
    gate_count: int,
    refusals: dict[AdmissionLane, str],
) -> None:
    controller = AdmissionController(ceiling, 0.1, False)
    decision = controller.evaluate(used=used, memory=memory, backend=backend, now=0.0)
    assert decision.blocked_lanes == blocked
    assert len(decision.gates) == gate_count
    assert {lane: decision.refusal(lane)[0] for lane in blocked} == refusals
    for lane, reason in refusals.items():
        measurements = decision.refusal(lane)[1]
        if reason == "admission_usage_unavailable":
            assert measurements is None
        elif reason == "measured_memory_exhausted":
            assert measurements == decision.memory
            assert measurements is not None and measurements["internal_limit"] == 1000
        else:
            assert measurements == decision.capacity
            assert measurements is not None and measurements["internal_limit"] == 10


def test_memory_refusal_does_not_start_a_postgres_drain_wait() -> None:
    """Drain-wait assesses the connection gate; memory refusal is published alone."""
    controller = AdmissionController(10, 0.1, False)
    decision = controller.evaluate(
        used=5, memory=_AT_RESERVE, backend="postgres", now=0.0
    )
    assert decision.blocked_lanes == {"public"}
    assert decision.wait_phase is None
    decision = controller.evaluate(
        used=5, memory=_AT_RESERVE, backend="postgres", now=60.0
    )
    assert decision.wait_phase is None
    assert decision.elapsed_seconds is None


@pytest.mark.parametrize(
    ("backend", "ceiling", "used"), [("sqlite", 0, None), ("postgres", 300, 7)]
)
def test_backend_reserve_fraction_does_not_reserve_host_memory(
    backend: str, ceiling: int, used: int | None
) -> None:
    """A large connection reserve leaves the fixed 10% memory reserve [MA-1.8].

    Operators raise the backend fraction to leave a shared database server's
    connections to its other clients; that must not withhold host memory.
    """
    decision = AdmissionController(ceiling, 0.75, False).evaluate(
        used=used,
        memory=HostMemory(total=1000, available=500),
        backend=backend,
        now=0.0,
    )
    assert decision.blocked_lanes == frozenset()
    assert decision.memory is not None
    assert (decision.memory["reserve"], decision.memory["public_limit"]) == (100, 900)


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
        (5, frozenset()),
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
    decision = admission.evaluate(
        used=used, memory=AMPLE, backend="postgres", now=100.0
    )
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
    admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=100.0)
    decision = admission.evaluate(
        used=10, memory=AMPLE, backend="postgres", now=100.0 + age
    )
    assert decision.wait_phase == phase
    assert decision.elapsed_seconds == pytest.approx(age)
    assert decision.idle_seconds == pytest.approx(age)
    assert decision.blocked_lanes == frozenset({"public", "internal"})


def test_admission_count_progress_extends_idle_but_not_absolute_budget(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=16, memory=AMPLE, backend="postgres", now=0.0)
    for now, used in [
        (25.0, 15),
        (50.0, 14),
        (75.0, 13),
        (100.0, 12),
        (125.0, 11),
        (150.0, 10),
    ]:
        decision = admission.evaluate(
            used=used, memory=AMPLE, backend="postgres", now=now
        )
        assert decision.wait_phase == "waiting"
        assert decision.idle_seconds == 0.0
        assert decision.elapsed_seconds == now
    assert (
        admission.evaluate(
            used=10, memory=AMPLE, backend="postgres", now=179.999
        ).wait_phase
        == "waiting"
    )
    decision = admission.evaluate(used=9, memory=AMPLE, backend="postgres", now=180.0)
    assert decision.wait_phase == "stalled"
    assert decision.elapsed_seconds == 180.0
    assert decision.idle_seconds == 0.0
    assert decision.blocked_lanes == frozenset({"public"})


def test_admission_first_known_baseline_does_not_renew_unknown_wait(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=None, memory=AMPLE, backend="postgres", now=100.0)
    decision = admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=129.0)
    assert decision.idle_seconds == 29.0
    assert decision.elapsed_seconds == 29.0
    assert (
        admission.evaluate(
            used=10, memory=AMPLE, backend="postgres", now=130.0
        ).wait_phase
        == "stalled"
    )
    decision = admission.evaluate(used=9, memory=AMPLE, backend="postgres", now=131.0)
    assert decision.wait_phase == "waiting"
    assert decision.idle_seconds == 0.0
    assert decision.elapsed_seconds == 31.0


def test_admission_unknown_samples_preserve_progress_and_low_water(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=0.0)
    admission.evaluate(used=9, memory=AMPLE, backend="postgres", now=10.0)
    decision = admission.evaluate(used=None, memory=AMPLE, backend="postgres", now=39.0)
    assert decision.wait_phase == "waiting"
    assert decision.idle_seconds == 29.0
    assert decision.capacity is None
    decision = admission.evaluate(used=9, memory=AMPLE, backend="postgres", now=40.0)
    assert decision.wait_phase == "stalled"
    assert decision.idle_seconds == 30.0
    assert decision.elapsed_seconds == 40.0


def test_admission_oscillation_above_episode_low_is_not_progress(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=0.0)
    admission.evaluate(used=9, memory=AMPLE, backend="postgres", now=10.0)
    admission.evaluate(used=11, memory=AMPLE, backend="postgres", now=20.0)
    decision = admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=40.0)
    assert decision.wait_phase == "stalled"
    assert decision.idle_seconds == 30.0
    decision = admission.evaluate(used=8, memory=AMPLE, backend="postgres", now=41.0)
    assert decision.wait_phase == "waiting"
    assert decision.idle_seconds == 0.0
    assert decision.elapsed_seconds == 41.0


def test_admission_reap_revives_idle_stall_without_renewing_total_budget(
    admission: AdmissionController,
) -> None:
    admission.note_child_reap(now=5.0)
    admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=10.0)
    assert (
        admission.evaluate(
            used=10, memory=AMPLE, backend="postgres", now=40.0
        ).wait_phase
        == "stalled"
    )
    admission.note_child_reap(now=41.0)
    decision = admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=42.0)
    assert decision.wait_phase == "waiting"
    assert decision.idle_seconds == 1.0
    assert decision.elapsed_seconds == 32.0
    admission.note_child_reap(now=129.0)
    decision = admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=130.0)
    assert decision.wait_phase == "waiting"
    assert decision.idle_seconds == 1.0
    assert decision.elapsed_seconds == 120.0
    admission.note_child_reap(now=189.0)
    decision = admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=190.0)
    assert decision.wait_phase == "stalled"
    assert decision.idle_seconds == 1.0
    assert decision.elapsed_seconds == 180.0
    admission.note_child_reap(now=191.0)
    assert (
        admission.evaluate(
            used=9, memory=AMPLE, backend="postgres", now=192.0
        ).wait_phase
        == "stalled"
    )


@pytest.mark.parametrize("expiry", [30.0, 180.0])
def test_admission_fresh_both_open_capacity_wins_at_expiry(
    admission: AdmissionController, expiry: float
) -> None:
    admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=0.0)
    decision = admission.evaluate(used=5, memory=AMPLE, backend="postgres", now=expiry)
    assert decision.blocked_lanes == frozenset()
    assert decision.wait_phase is None
    assert decision.elapsed_seconds is None
    assert decision.idle_seconds is None
    admission.note_child_reap(now=expiry + 10.0)
    decision = admission.evaluate(
        used=10, memory=AMPLE, backend="postgres", now=expiry + 20.0
    )
    assert decision.wait_phase == "waiting"
    assert decision.elapsed_seconds == decision.idle_seconds == 0.0


def test_admission_internal_capacity_does_not_end_public_wait(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=0.0)
    decision = admission.evaluate(used=9, memory=AMPLE, backend="postgres", now=25.0)
    assert decision.blocked_lanes == frozenset({"public"})
    decision = admission.evaluate(used=9, memory=AMPLE, backend="postgres", now=55.0)
    assert decision.wait_phase == "stalled"
    assert decision.elapsed_seconds == 55.0
    assert decision.idle_seconds == 30.0


def test_admission_empty_source_reset_starts_a_new_episode(
    admission: AdmissionController,
) -> None:
    admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=0.0)
    assert (
        admission.evaluate(
            used=10, memory=AMPLE, backend="postgres", now=180.0
        ).wait_phase
        == "stalled"
    )
    admission.reset_wait()
    admission.note_child_reap(now=190.0)
    decision = admission.evaluate(used=11, memory=AMPLE, backend="postgres", now=200.0)
    assert decision.wait_phase == "waiting"
    assert decision.elapsed_seconds == decision.idle_seconds == 0.0
    decision = admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=225.0)
    assert decision.idle_seconds == 0.0
    assert decision.elapsed_seconds == 25.0


def test_admission_sqlite_never_starts_progress_assessment(
    admission: AdmissionController,
) -> None:
    for now, used in [(0.0, 10), (30.0, 9), (120.0, None)]:
        decision = admission.evaluate(
            used=used, memory=AMPLE, backend="sqlite", now=now
        )
        admission.note_child_reap(now=now + 1.0)
        assert decision.wait_phase is None
        assert decision.elapsed_seconds is None
        assert decision.idle_seconds is None
    decision = admission.evaluate(used=10, memory=AMPLE, backend="postgres", now=150.0)
    assert decision.wait_phase == "waiting"
    assert decision.elapsed_seconds == decision.idle_seconds == 0.0
