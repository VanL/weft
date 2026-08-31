"""Process-local runtime liveness probe registry.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2]
- docs/specifications/07-System_Invariants.md [LIVENESS.R5]
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import RLock

from weft._constants import LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS
from weft.ext import RunnerHandle

from .models import RuntimeLiveness

RuntimeLivenessProbe = Callable[[RunnerHandle, float], RuntimeLiveness]

_runtime_liveness_probes: dict[str, RuntimeLivenessProbe] = {}
_runtime_liveness_lock = RLock()

logger = logging.getLogger(__name__)


def liveness_provider_key(handle: RunnerHandle) -> str:
    """Return the extension key that owns liveness for ``handle``."""

    provider = handle.observations.get("liveness_provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip()
    return handle.runner.strip()


def register_runtime_liveness_probe(key: str, probe: RuntimeLivenessProbe) -> None:
    """Register or replace one process-local runtime liveness probe."""

    normalized = key.strip()
    if not normalized:
        raise ValueError("runtime liveness probe key must be non-empty")
    with _runtime_liveness_lock:
        if normalized in _runtime_liveness_probes:
            logger.debug("Replacing runtime liveness probe for %s", normalized)
        _runtime_liveness_probes[normalized] = probe


def attempt_runtime_liveness_from_registered_probe(
    handle: RunnerHandle,
    timeout_seconds: float = LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS,
) -> RuntimeLiveness | None:
    """Attempt one extension probe, returning ``None`` when it does not complete."""

    key = liveness_provider_key(handle)
    with _runtime_liveness_lock:
        probe = _runtime_liveness_probes.get(key)
    if probe is None:
        return "unknown"
    try:
        result = probe(handle, timeout_seconds)
    except Exception:  # pragma: no cover - extension boundary guard
        logger.debug("Runtime liveness probe for %s failed", key, exc_info=True)
        return None
    if result not in {"live", "stale", "unknown"}:
        logger.debug(
            "Runtime liveness probe for %s returned invalid value %r", key, result
        )
        return None
    return result


def runtime_liveness_from_registered_probe(
    handle: RunnerHandle,
    timeout_seconds: float = LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS,
) -> RuntimeLiveness:
    """Return extension-owned liveness, conservatively projecting failures unknown."""

    return (
        attempt_runtime_liveness_from_registered_probe(handle, timeout_seconds)
        or "unknown"
    )
