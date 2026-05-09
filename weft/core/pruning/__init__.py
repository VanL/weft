"""Canonical prune candidate selection and exact-delete helpers.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
"""

from __future__ import annotations

from .apply import apply_exact_prune_candidates

__all__ = ["apply_exact_prune_candidates"]
