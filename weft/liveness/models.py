"""Immutable point-in-time liveness evidence values.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2]
- docs/specifications/07-System_Invariants.md [LIVENESS.R2], [LIVENESS.R5]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RuntimeLiveness = Literal["live", "stale", "unknown"]


@dataclass(frozen=True, slots=True)
class HostProcessObservation:
    """One exact host-process identity observation."""

    evidence: RuntimeLiveness
    reason: str


@dataclass(frozen=True, slots=True)
class LivenessObservation:
    """Authority-reduced point-in-time evidence for one TID."""

    tid: str
    evidence: RuntimeLiveness
    reason: str
    attempted: bool
    generation: str
