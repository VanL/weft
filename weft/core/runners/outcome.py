"""Shared result contract for task runner implementations.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3], [CC-3.2], [CC-3.4], [CC-3.5]
- docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1]
"""

from __future__ import annotations

from weft.ext import RunnerOutcome

__all__ = ["RunnerOutcome"]
