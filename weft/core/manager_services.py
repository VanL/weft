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
from dataclasses import dataclass, field
from typing import Any, Literal

from weft._constants import (
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
)

ServiceLifecycle = Literal["once", "ensure"]
ServiceCandidateState = Literal["live", "terminal", "uncertain"]


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
    autostart_source: str | None = None


@dataclass(slots=True)
class ManagedServiceState:
    """Mutable Manager-local state for one desired service key."""

    spawn_pending: bool = False
    active_tid: str | None = None
    next_allowed_ns: int = 0
    launched_once: bool = False
    restarts: int = 0


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
    candidates: list[ServiceCandidate],
) -> ServiceCandidate | None:
    """Return the deterministic canonical live service candidate.

    Singleton ownership follows the same deterministic rule as managers:
    among live candidates, the lowest TID wins.
    """

    live = [candidate for candidate in candidates if candidate.state == "live"]
    if not live:
        return None
    return min(live, key=lambda candidate: candidate.tid)


__all__ = [
    "ManagedServiceSpec",
    "ManagedServiceState",
    "ServiceCandidate",
    "ServiceCandidateState",
    "ServiceLifecycle",
    "apply_service_metadata",
    "select_canonical_live_candidate",
    "service_key_from_metadata",
    "service_metadata",
]
