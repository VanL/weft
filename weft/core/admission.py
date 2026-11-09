"""Pure admission gates, startup window and PostgreSQL drain-wait policy.

Manager owns observation, lifecycle evidence, retry scheduling and logs.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1.8]
- docs/specifications/07-System_Invariants.md [MANAGER.18]
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from weft._constants import (
    ADMISSION_MEMORY_RESERVE_FRACTION,
    ADMISSION_SERVICE_RESERVE_SLOTS,
    MANAGER_ADMISSION_PROGRESS_TIMEOUT_SECONDS,
    MANAGER_ADMISSION_WAIT_MAX_SECONDS,
    MANAGER_STARTUP_FAST_PATH_FRACTION,
    MANAGER_STARTUP_FAST_PATH_WINDOW,
)

AdmissionLane = Literal["public", "internal"]

_BOTH_LANES: frozenset[AdmissionLane] = frozenset(("public", "internal"))


@dataclass(frozen=True)
class HostMemory:
    """One host memory observation in bytes [MA-1.8]."""

    total: int
    available: int


def _startup_window_full(unfinished: int) -> bool:
    """Readiness bound: no gate permits more than W unfinished startups [MA-1.8].

    The Manager checks it before measuring; W is read through this module's
    binding so the bound and `_startup_window_allows_launch` always agree.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1.8]
    """

    return unfinished >= MANAGER_STARTUP_FAST_PATH_WINDOW


def _gate_startup_window(capacity: Mapping[str, Any]) -> int:
    """Return N(U) for one measured gate with public limit L and usage U [MA-1.8].

    With threshold H and window W, N(U) = min(W, max(1, floor(W(L-U)/(L-H))))
    when H >= W: all W at or below H, then fewer in proportion to headroom
    L-U. A window wider than one never leaves an unfinished startup less
    measured headroom than the (L-H)/W share each of W startups has at H.
    A gate whose H is below W permits one.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1.8]
    """

    limit: int = capacity["public_limit"]
    used: int = capacity["used"]
    threshold = math.floor(limit * MANAGER_STARTUP_FAST_PATH_FRACTION)
    if threshold < MANAGER_STARTUP_FAST_PATH_WINDOW:
        return 1
    return max(
        1,
        min(
            MANAGER_STARTUP_FAST_PATH_WINDOW,
            MANAGER_STARTUP_FAST_PATH_WINDOW * (limit - used) // (limit - threshold),
        ),
    )


def _startup_window_allows_launch(
    *, unfinished: int, gates: Sequence[Mapping[str, Any] | None]
) -> bool:
    """Bound startup overlap after normal admission allows a launch [MA-1.8].

    The window is the smallest N(U) over every gate that exists (`gates`,
    with None for an unknown observation). Zero unfinished startups always
    pass here; only the measured decision admits the first one.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1.8]
    """

    if unfinished == 0:
        return True
    window = MANAGER_STARTUP_FAST_PATH_WINDOW
    for capacity in gates:
        if capacity is None:
            return False
        window = min(window, _gate_startup_window(capacity))
    return unfinished < window


def _admission_capacity(
    *,
    used: int,
    max_connections: int,
    reserve_fraction: float,
    liveness_monitor_enabled: bool,
) -> dict[str, Any]:
    """Return backend usage limits and lane decisions.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1.8]
    """

    reserve = max(
        math.ceil(max_connections * reserve_fraction),
        ADMISSION_SERVICE_RESERVE_SLOTS + int(liveness_monitor_enabled),
    )
    public_limit = max(0, max_connections - reserve)
    internal_limit = max_connections
    return {
        "used": used,
        "reserve": reserve,
        "public_limit": public_limit,
        "internal_limit": internal_limit,
        "public_allowed": used < public_limit,
        "internal_allowed": used < internal_limit,
    }


def _memory_capacity(*, memory: HostMemory, reserve_fraction: float) -> dict[str, Any]:
    """Return host memory limits and lane decisions, in bytes [MA-1.8].

    Usage is total minus available, so every program's memory counts. The
    reserve is `ceil(total * reserve_fraction)`, with no slot floor. No task
    is charged: a task's memory limit never enters this calculation.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1.8]
    """

    used = memory.total - memory.available
    reserve = math.ceil(memory.total * reserve_fraction)
    public_limit = memory.total - reserve
    return {
        "used": used,
        "reserve": reserve,
        "public_limit": public_limit,
        "internal_limit": memory.total,
        "public_allowed": used < public_limit,
        "internal_allowed": used < memory.total,
    }


def _admission_blocked_lanes_for_capacity(
    capacity: Mapping[str, Any],
) -> set[AdmissionLane]:
    """Return the monotonic lane blocks encoded by one capacity source."""

    blocked: set[AdmissionLane] = set()
    if not capacity["public_allowed"]:
        blocked.add("public")
    if not capacity["internal_allowed"]:
        blocked.update(("public", "internal"))
    return blocked


@dataclass(frozen=True)
class AdmissionDecision:
    """Lane eligibility from every gate plus optional PG drain evidence [MA-1.8].

    `capacity` is the backend gate (PostgreSQL connections or SQLite
    live/unknown TIDs) and exists only when `backend_gate` is true; `memory`
    is the host memory gate, which exists on every backend. Either is None
    for an unknown observation.
    """

    capacity: dict[str, Any] | None
    memory: dict[str, Any] | None
    backend_gate: bool
    blocked_lanes: frozenset[AdmissionLane]
    wait_phase: Literal["waiting", "stalled"] | None = None
    elapsed_seconds: float | None = None
    idle_seconds: float | None = None

    @property
    def gates(self) -> tuple[dict[str, Any] | None, ...]:
        """Each existing gate's capacity; None marks an unknown observation."""

        return (self.capacity, self.memory) if self.backend_gate else (self.memory,)

    def refusal(self, lane: AdmissionLane) -> tuple[str, dict[str, Any] | None]:
        """Name the gate refusing a blocked lane and its measurements [MA-1.8].

        An unknown observation on any gate refuses both lanes. Otherwise the
        backend gate is named first when it refuses the lane.
        """

        if any(gate is None for gate in self.gates):
            return "admission_usage_unavailable", None
        if self.capacity is not None and lane in (
            _admission_blocked_lanes_for_capacity(self.capacity)
        ):
            return "measured_capacity_exhausted", dict(self.capacity)
        assert self.memory is not None
        return "measured_memory_exhausted", dict(self.memory)


class AdmissionController:
    """Assess supplied usage without observing or scheduling [MANAGER.18]."""

    def __init__(
        self,
        max_connections: int,
        reserve_fraction: float,
        liveness_monitor_enabled: bool,
    ) -> None:
        self._max_connections = max_connections
        self._reserve_fraction = reserve_fraction
        self._liveness_monitor_enabled = liveness_monitor_enabled
        self._wait_started: float | None = None
        self._last_progress: float | None = None
        self._lowest_usage: int | None = None

    def set_max_connections(self, max_connections: int) -> None:
        """Update backend capacity without resetting drain history [MA-1.8]."""

        self._max_connections = max_connections

    def backend_gate_exists(self, backend: str) -> bool:
        """PostgreSQL always gates connections; SQLite only under a ceiling [MA-1.8]."""

        return backend == "postgres" or self._max_connections > 0

    def evaluate(
        self,
        *,
        used: int | None,
        memory: HostMemory | None,
        backend: str,
        now: float,
    ) -> AdmissionDecision:
        """Require every existing gate; assess PG connection drain [MA-1.8].

        `used` is the backend gate's usage and is ignored when that gate does
        not exist; None for either observation means unknown. The drain-wait
        episode follows the backend gate only on PostgreSQL.
        """

        backend_gate = self.backend_gate_exists(backend)
        capacity = (
            _admission_capacity(
                used=used,
                max_connections=self._max_connections,
                reserve_fraction=self._reserve_fraction,
                liveness_monitor_enabled=self._liveness_monitor_enabled,
            )
            if backend_gate and used is not None
            else None
        )
        memory_capacity = (
            None
            if memory is None
            else _memory_capacity(
                memory=memory, reserve_fraction=ADMISSION_MEMORY_RESERVE_FRACTION
            )
        )
        backend_blocked = (
            frozenset[AdmissionLane]()
            if not backend_gate
            else _BOTH_LANES
            if capacity is None
            else frozenset(_admission_blocked_lanes_for_capacity(capacity))
        )
        blocked = backend_blocked | (
            _BOTH_LANES
            if memory_capacity is None
            else frozenset(_admission_blocked_lanes_for_capacity(memory_capacity))
        )
        if backend != "postgres":
            return AdmissionDecision(capacity, memory_capacity, backend_gate, blocked)
        if not backend_blocked:
            self.reset_wait()
            return AdmissionDecision(capacity, memory_capacity, backend_gate, blocked)

        if self._wait_started is None:
            self._wait_started = now
            self._last_progress = now
            self._lowest_usage = used
        elif used is not None:
            if self._lowest_usage is None:
                # A first known baseline does not prove prior usage decreased.
                self._lowest_usage = used
            elif used < self._lowest_usage:
                self._lowest_usage = used
                self._last_progress = now

        assert self._last_progress is not None
        elapsed = now - self._wait_started
        idle = now - self._last_progress
        phase: Literal["waiting", "stalled"] = (
            "stalled"
            if idle >= MANAGER_ADMISSION_PROGRESS_TIMEOUT_SECONDS
            or elapsed >= MANAGER_ADMISSION_WAIT_MAX_SECONDS
            else "waiting"
        )
        return AdmissionDecision(
            capacity, memory_capacity, backend_gate, blocked, phase, elapsed, idle
        )

    def note_child_reap(self, *, now: float) -> None:
        """Record actual resource retirement only during a PG wait [MANAGER.18]."""

        if self._wait_started is not None:
            self._last_progress = now

    def reset_wait(self) -> None:
        """End an episode after observed open capacity or proven idle [MANAGER.18]."""

        self._wait_started = None
        self._last_progress = None
        self._lowest_usage = None
