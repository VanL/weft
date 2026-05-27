"""Common internal API for TaskMonitor cleanup policies.

The monitor exposes five top-level cleanup policies. Policy implementations may
keep private phases, but they report progress through this common result shape.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from weft._constants import TASK_MONITOR_CLEANUP_POLICY_NAMES
from weft.core.monitor.progress import PolicyProgress

if TYPE_CHECKING:
    from weft.context import WeftContext
    from weft.core.monitor.store import MonitorStore
    from weft.core.tasks.service import ServiceWorkerContext


CleanupPolicyName = Literal[
    "task_log.retention",
    "monitor_store.lifecycle",
    "task_local.terminal_runtime",
    "task_local.dead_tid",
    "runtime_state.retention",
]


@dataclass(frozen=True, slots=True)
class CleanupPolicyWork:
    """One bounded unit of cleanup policy work."""

    policy: CleanupPolicyName
    now_ns: int
    request_id: str
    phase: str | None = None
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class CleanupPolicyConfig:
    """Immutable monitor cleanup settings for one policy worker run."""

    batch_size: int
    task_log_scan_limit: int
    task_log_retention_period_seconds: float
    store_write_batch_size: int
    table_delete_enabled: bool
    control_queue_delete_limit: int
    runtime_family_limit: int
    runtime_cleanup_slice_seconds: float


@dataclass(frozen=True, slots=True)
class CleanupPolicyContext:
    """Shared dependencies for one monitor cleanup worker run."""

    context: WeftContext
    monitor_store: MonitorStore | None
    config: CleanupPolicyConfig
    service_worker: ServiceWorkerContext | None = None


@dataclass(frozen=True, slots=True)
class CleanupPolicyResult:
    """Common result shape for every top-level cleanup policy."""

    policy: CleanupPolicyName
    domain: str
    scanned: int = 0
    selected: int = 0
    applied: int = 0
    deferred: int = 0
    source_total: int | None = None
    reason_counts: Mapping[str, int] = field(default_factory=dict)
    base_reached: bool = False
    waypoint_reached: bool = False
    blocked_reason: str | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)


class CleanupPolicy(Protocol):
    """Callable contract for one top-level cleanup policy."""

    name: CleanupPolicyName

    def run(
        self,
        work: CleanupPolicyWork,
        context: CleanupPolicyContext,
    ) -> CleanupPolicyResult:
        """Run one bounded cleanup policy unit."""
        ...


def policy_result_to_progress(result: CleanupPolicyResult) -> PolicyProgress:
    """Convert one policy result into the monitor's cached progress contract."""

    if result.policy not in TASK_MONITOR_CLEANUP_POLICY_NAMES:
        raise ValueError(f"unknown cleanup policy: {result.policy}")
    return PolicyProgress(
        policy=result.policy,
        domain=result.domain,
        scanned=result.scanned,
        selected=result.selected,
        applied=result.applied,
        deferred=result.deferred,
        source_total=result.source_total,
        waypoint_reached=result.waypoint_reached,
        base_reached=result.base_reached,
        blocked_reason=result.blocked_reason,
        reason_counts=dict(result.reason_counts),
    )


def cleanup_policy_names() -> Sequence[str]:
    """Return the allowed top-level cleanup policy names."""

    return TASK_MONITOR_CLEANUP_POLICY_NAMES
