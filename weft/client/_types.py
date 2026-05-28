"""Public client type aliases backed by shared command result dataclasses.

Spec references:
- docs/specifications/09-Implementation_Plan.md [IP-1]
- docs/specifications/10-CLI_Interface.md [CLI-1.2], [CLI-4], [CLI-6]
"""

from __future__ import annotations

from typing import Protocol

from weft.commands.types import (
    QueueAckTarget,
    TaskEvent,
    TaskResult,
    TaskSnapshot,
    TaskTerminalSnapshot,
)
from weft.context import WeftContext


class ClientContextHandle(Protocol):
    """Minimal protocol shared by the client namespaces and task handle."""

    context: WeftContext


__all__ = [
    "ClientContextHandle",
    "QueueAckTarget",
    "TaskEvent",
    "TaskResult",
    "TaskSnapshot",
    "TaskTerminalSnapshot",
]
