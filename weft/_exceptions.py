"""Public exception types for Weft client and shared ops surfaces.

Spec references:
- docs/specifications/07-System_Invariants.md
- docs/specifications/09-Implementation_Plan.md [IP-1], [IP-1.1]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from weft.commands.types import TaskControlFailure


class WeftError(Exception):
    """Base exception for Weft-specific failures."""


class CommandError(WeftError):
    """Base exception for public command-surface failures. Spec: [PY-2]."""


class CommandUsageError(CommandError, ValueError):
    """Raised when parsed command arguments are semantically invalid."""


class CommandTimeoutError(CommandError, TimeoutError):
    """Raised when a command operation reaches its timeout."""


class CommandExecutionError(CommandError, RuntimeError):
    """Raised when command orchestration fails."""


class SubmissionError(CommandError):
    """Base exception for public submission failures."""


class SubmissionValidationError(SubmissionError):
    """Raised when a submission cannot be validated or materialized."""


class SubmissionManagerError(SubmissionError):
    """Raised when manager readiness or submission transport fails."""


class InvalidTID(WeftError, ValueError):
    """Raised when a TID is malformed."""


class TaskNotFound(WeftError, LookupError):
    """Raised when a task cannot be found on public surfaces."""


class ControlRejected(WeftError, RuntimeError):
    """Raised when a task or manager control request is not accepted.

    For bulk control sweeps, ``failures`` carries the full
    ``TaskControlFailure`` tuple when at least one task was requested and
    none was accepted; single-target rejections leave it empty.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    def __init__(
        self,
        message: str,
        *,
        failures: tuple[TaskControlFailure, ...] = (),
    ) -> None:
        super().__init__(message)
        self.failures = failures


class SpecNotFound(WeftError, FileNotFoundError):
    """Raised when a stored or file-backed spec reference cannot be resolved."""


class ManagerNotRunning(WeftError, RuntimeError):
    """Raised when a manager-specific action requires a live manager."""


class ManagerStartFailed(WeftError, RuntimeError):
    """Raised when manager bootstrap cannot prove a stable startup."""
