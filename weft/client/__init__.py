"""Public Python client surface for Weft.

Spec references:
- docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-4]
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
from weft.context import WeftContext, build_context
from weft.core.taskspec.model import (
    AgentSection,
    AgentTemplateSection,
    AgentToolSection,
    IOSection,
    LimitsSection,
    ParameterizationArgumentSection,
    ParameterizationSection,
    ReservedPolicy,
    RunInputArgumentSection,
    RunInputSection,
    RunInputStdinSection,
    RunnerSection,
    SpecSection,
    StateSection,
    TaskSpec,
)

from ._client import WeftClient, connect, normalize_taskspec_payload
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
    "AgentSection",
    "AgentTemplateSection",
    "AgentToolSection",
    "CommandError",
    "CommandExecutionError",
    "CommandTimeoutError",
    "CommandUsageError",
    "ControlRejected",
    "IOSection",
    "InvalidTID",
    "LimitsSection",
    "ManagerNotRunning",
    "ManagerStartFailed",
    "ParameterizationArgumentSection",
    "ParameterizationSection",
    "PreparedSubmission",
    "QueueAckTarget",
    "ReservedPolicy",
    "RunInputArgumentSection",
    "RunInputSection",
    "RunInputStdinSection",
    "RunnerSection",
    "SpecNotFound",
    "SpecSection",
    "StateSection",
    "SubmissionError",
    "SubmissionManagerError",
    "SubmissionValidationError",
    "Task",
    "TaskEvent",
    "TaskNotFound",
    "TaskResult",
    "TaskSnapshot",
    "TaskSpec",
    "TaskTerminalSnapshot",
    "WeftClient",
    "WeftContext",
    "WeftError",
    "build_context",
    "connect",
    "normalize_taskspec_payload",
]
