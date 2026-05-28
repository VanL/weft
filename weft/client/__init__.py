"""Public Python client surface for Weft.

Spec references:
- docs/specifications/09-Implementation_Plan.md [IP-1]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5]
"""

from __future__ import annotations

from ._client import WeftClient
from ._errors import (
    ControlRejected,
    InvalidTID,
    ManagerNotRunning,
    ManagerStartFailed,
    SpecNotFound,
    TaskNotFound,
    WeftError,
)
from ._prepared import PreparedSubmission
from ._task import Task
from ._types import (
    QueueAckTarget,
    TaskEvent,
    TaskResult,
    TaskSnapshot,
    TaskTerminalSnapshot,
)

__all__ = [
    "ControlRejected",
    "InvalidTID",
    "ManagerNotRunning",
    "ManagerStartFailed",
    "PreparedSubmission",
    "QueueAckTarget",
    "SpecNotFound",
    "Task",
    "TaskEvent",
    "TaskNotFound",
    "TaskResult",
    "TaskSnapshot",
    "TaskTerminalSnapshot",
    "WeftClient",
    "WeftError",
]
