"""Public client type aliases backed by shared core dataclasses."""

from __future__ import annotations

from typing import Protocol

from weft.commands.types import TaskEvent, TaskResult, TaskSnapshot
from weft.context import WeftContext


class ClientContextHandle(Protocol):
    """Minimal protocol shared by the client namespaces and task handle."""

    context: WeftContext


__all__ = ["ClientContextHandle", "TaskEvent", "TaskResult", "TaskSnapshot"]
