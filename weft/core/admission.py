"""Pure admission capacity and PostgreSQL drain-wait policy.

Manager owns observation, lifecycle evidence, retry scheduling and logs.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1.8]
- docs/specifications/07-System_Invariants.md [MANAGER.18]
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from weft._constants import (
    ADMISSION_SERVICE_RESERVE_SLOTS,
    MANAGER_ADMISSION_PROGRESS_TIMEOUT_SECONDS,
    MANAGER_ADMISSION_WAIT_MAX_SECONDS,
    WEFT_ADMISSION_TASK_CONNECTION_BUDGET_DEFAULT,
)

AdmissionLane = Literal["public", "child", "internal"]


def postgres_accounted_usage(
    total: int, by_owner: Mapping[str, int], obligations: Iterable[tuple[str, int]]
) -> int:
    """Charge unopened budget once, retaining actual over-budget pressure.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1.8].
    Manager deduplicates admitted full TIDs before supplying obligations.
    """
    return total + sum(
        max(0, budget - by_owner.get(owner, 0)) for owner, budget in obligations
    )


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


def _admission_blocked_lanes_for_capacity(
    capacity: Mapping[str, Any],
) -> set[AdmissionLane]:
    """Return the monotonic lane blocks encoded by one capacity source."""

    blocked: set[AdmissionLane] = set()
    if not capacity["public_allowed"]:
        blocked.add("public")
    if not capacity.get("child_allowed", capacity["public_allowed"]):
        blocked.add("child")
    if not capacity["internal_allowed"]:
        blocked.update(("public", "child", "internal"))
    return blocked


@dataclass(frozen=True)
class AdmissionDecision:
    """Lane eligibility plus optional PG resource-drain evidence [MA-1.8]."""

    capacity: dict[str, Any] | None
    blocked_lanes: frozenset[AdmissionLane]
    wait_phase: Literal["waiting", "stalled"] | None = None
    elapsed_seconds: float | None = None
    idle_seconds: float | None = None


class AdmissionController:
    """Assess supplied usage without observing or scheduling [MANAGER.18]."""

    def __init__(
        self,
        max_connections: int,
        reserve_fraction: float,
        liveness_monitor_enabled: bool,
        task_connection_budget: int = WEFT_ADMISSION_TASK_CONNECTION_BUDGET_DEFAULT,
    ) -> None:
        if type(task_connection_budget) is not int or task_connection_budget <= 0:
            raise ValueError("task_connection_budget must be a positive integer")
        self._task_connection_budget = task_connection_budget
        self._max_connections = max_connections
        self._reserve_fraction = reserve_fraction
        self._liveness_monitor_enabled = liveness_monitor_enabled
        self._wait_started: float | None = None
        self._last_progress: float | None = None
        self._lowest_usage: int | None = None

    def evaluate(
        self, *, used: int | None, backend: str, now: float
    ) -> AdmissionDecision:
        """Apply lane limits and PG monotonic assessment budgets [MA-1.8]."""

        capacity = (
            None
            if used is None
            else _admission_capacity(
                used=used,
                max_connections=self._max_connections,
                reserve_fraction=self._reserve_fraction,
                liveness_monitor_enabled=self._liveness_monitor_enabled,
            )
        )
        if capacity is not None and backend == "postgres":
            assert used is not None
            budget = self._task_connection_budget
            child_limit = capacity["public_limit"]
            capacity.update(
                public_limit=max(0, child_limit - budget),
                child_limit=child_limit,
                candidate_budget=budget,
            )
            capacity.update(
                public_allowed=used + budget <= capacity["public_limit"],
                child_allowed=used + budget <= child_limit,
                internal_allowed=used + budget <= capacity["internal_limit"],
            )
        blocked = (
            frozenset[AdmissionLane](("public", "child", "internal"))
            if capacity is None
            else frozenset(_admission_blocked_lanes_for_capacity(capacity))
        )
        if backend != "postgres":
            return AdmissionDecision(capacity, blocked)
        if not blocked:
            self.reset_wait()
            return AdmissionDecision(capacity, blocked)

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
        return AdmissionDecision(capacity, blocked, phase, elapsed, idle)

    def note_child_reap(self, *, now: float) -> None:
        """Record actual resource retirement only during a PG wait [MANAGER.18]."""

        if self._wait_started is not None:
            self._last_progress = now

    def reset_wait(self) -> None:
        """End an episode after observed open capacity or proven idle [MANAGER.18]."""

        self._wait_started = None
        self._last_progress = None
        self._lowest_usage = None
