"""Shared result contract for task runner implementations.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3], [CC-3.2], [CC-3.4], [CC-3.5]
- docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from weft.core.resource_monitor import ResourceMetrics
from weft.ext import RunnerHandle


@dataclass(slots=True)
class RunnerOutcome:
    """Result returned after executing a work item."""

    status: str
    value: Any | None
    error: str | None
    stdout: str | None
    stderr: str | None
    returncode: int | None
    duration: float
    metrics: ResourceMetrics | None = None
    runtime_handle: RunnerHandle | None = None
    diagnostics: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        """Return whether execution completed successfully."""

        return self.status == "ok"
