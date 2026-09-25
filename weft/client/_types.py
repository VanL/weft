"""Public client type aliases backed by shared command result dataclasses.

Spec references:
- docs/specifications/09-Implementation_Plan.md [IP-1]
- docs/specifications/10-CLI_Interface.md [CLI-1.2], [CLI-4], [CLI-6]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from weft.commands.types import (
    PreparedSubmissionRequest,
    QueueAckTarget,
    TaskEvent,
    TaskResult,
    TaskSnapshot,
    TaskTerminalSnapshot,
)
from weft.context import WeftContext

if TYPE_CHECKING:
    from ._task import Task


class ClientContextHandle(Protocol):
    """Minimal protocol shared by the client namespaces and task handle."""

    context: WeftContext


class SubmissionClientHandle(ClientContextHandle, Protocol):
    """Client handle that owns prepared-submission dispatch."""

    def _submit_prepared(self, prepared: PreparedSubmissionRequest) -> Task: ...


__all__ = [
    "ClientContextHandle",
    "QueueAckTarget",
    "SubmissionClientHandle",
    "TaskEvent",
    "TaskResult",
    "TaskSnapshot",
    "TaskTerminalSnapshot",
]
