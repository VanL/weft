"""Process-local runtime liveness probe registry.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2]
- docs/specifications/03-Manager_Architecture.md [MA-1.4]
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import RLock
from typing import Literal

from weft.ext import RunnerHandle

RuntimeLiveness = Literal["live", "stale", "unknown"]
RuntimeLivenessProbe = Callable[[RunnerHandle], RuntimeLiveness]

_RUNTIME_LIVENESS_PROBES: dict[str, RuntimeLivenessProbe] = {}
_RUNTIME_LIVENESS_LOCK = RLock()

logger = logging.getLogger(__name__)


def register_runtime_liveness_probe(
    key: str,
    probe: RuntimeLivenessProbe,
) -> None:
    """Register a process-local runtime liveness probe for ``key``.

    Registration is intentionally lightweight and non-durable. Later
    registrations for the same key replace earlier registrations.

    Spec: docs/specifications/01-Core_Components.md [CC-3.2]
    """

    normalized = key.strip()
    if not normalized:
        raise ValueError("runtime liveness probe key must be non-empty")
    with _RUNTIME_LIVENESS_LOCK:
        if normalized in _RUNTIME_LIVENESS_PROBES:
            logger.debug("Replacing runtime liveness probe for %s", normalized)
        _RUNTIME_LIVENESS_PROBES[normalized] = probe


def runtime_liveness_from_registered_probe(handle: RunnerHandle) -> RuntimeLiveness:
    """Return registered runtime liveness for ``handle`` or ``unknown``."""

    key = handle.runner.strip()
    with _RUNTIME_LIVENESS_LOCK:
        probe = _RUNTIME_LIVENESS_PROBES.get(key)
    if probe is None:
        return "unknown"
    try:
        result = probe(handle)
    except Exception:  # pragma: no cover - extension boundary guard
        logger.debug(
            "Runtime liveness probe for %s failed",
            key,
            exc_info=True,
        )
        return "unknown"
    if result not in {"live", "stale", "unknown"}:
        logger.debug(
            "Runtime liveness probe for %s returned invalid value %r",
            key,
            result,
        )
        return "unknown"
    return result
