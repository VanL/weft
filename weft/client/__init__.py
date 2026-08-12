"""Public Python client surface for Weft.

Spec references:
- docs/specifications/09-Implementation_Plan.md [IP-1]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5]
"""

from __future__ import annotations

from weft._exceptions import (
    CommandError,
    CommandExecutionError,
    CommandTimeoutError,
    CommandUsageError,
    ControlRejected,
    InvalidTID,
    ManagerNotRunning,
    ManagerStartFailed,
    SpecNotFound,
    SubmissionError,
    SubmissionManagerError,
    SubmissionValidationError,
    TaskNotFound,
    WeftError,
)

from ._client import WeftClient, connect
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
    "CommandError",
    "CommandExecutionError",
    "CommandTimeoutError",
    "CommandUsageError",
    "ControlRejected",
    "InvalidTID",
    "ManagerNotRunning",
    "ManagerStartFailed",
    "PreparedSubmission",
    "QueueAckTarget",
    "SpecNotFound",
    "SubmissionError",
    "SubmissionManagerError",
    "SubmissionValidationError",
    "Task",
    "TaskEvent",
    "TaskNotFound",
    "TaskResult",
    "TaskSnapshot",
    "TaskTerminalSnapshot",
    "WeftClient",
    "WeftError",
    "connect",
]
