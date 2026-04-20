"""Public exception types for Weft client and shared ops surfaces.

Spec references:
- docs/specifications/07-System_Invariants.md
- docs/specifications/13C-Using_Weft_With_Django.md [DJ-2.1], [DJ-2.2]
"""

from __future__ import annotations


class WeftError(Exception):
    """Base exception for Weft-specific failures."""


class InvalidTID(WeftError, ValueError):
    """Raised when a TID is malformed."""


class TaskNotFound(WeftError, LookupError):
    """Raised when a task cannot be found on public surfaces."""


class ControlRejected(WeftError, RuntimeError):
    """Raised when a task or manager control request is not accepted."""


class SpecNotFound(WeftError, FileNotFoundError):
    """Raised when a stored or file-backed spec reference cannot be resolved."""


class ManagerNotRunning(WeftError, RuntimeError):
    """Raised when a manager-specific action requires a live manager."""


class ManagerStartFailed(WeftError, RuntimeError):
    """Raised when manager bootstrap cannot prove a stable startup."""
