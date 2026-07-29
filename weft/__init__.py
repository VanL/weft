"""Lazy public facade for the Weft package.

Durable task execution on SimpleBroker queues: persistent managers,
multiprocess isolation, and comprehensive observability. Public client and
logging exports load on first access so importing a low-level leaf does not
initialize upper layers.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from ._constants import PROG_NAME, __version__

if TYPE_CHECKING:
    from .client import Task, TaskEvent, TaskResult, TaskSnapshot, WeftClient
    from .helpers import (
        debug_print,
        log_debug,
        log_error,
        log_info,
        log_warning,
        send_log,
    )

__all__ = [
    "__version__",
    "PROG_NAME",
    "Task",
    "TaskEvent",
    "TaskResult",
    "TaskSnapshot",
    "WeftClient",
    "debug_print",
    "send_log",
    "log_debug",
    "log_info",
    "log_warning",
    "log_error",
]

_LAZY_EXPORTS = {
    "Task": ("weft.client", "Task"),
    "TaskEvent": ("weft.client", "TaskEvent"),
    "TaskResult": ("weft.client", "TaskResult"),
    "TaskSnapshot": ("weft.client", "TaskSnapshot"),
    "WeftClient": ("weft.client", "WeftClient"),
    "debug_print": ("weft.helpers", "debug_print"),
    "send_log": ("weft.helpers", "send_log"),
    "log_debug": ("weft.helpers", "log_debug"),
    "log_info": ("weft.helpers", "log_info"),
    "log_warning": ("weft.helpers", "log_warning"),
    "log_error": ("weft.helpers", "log_error"),
}


def __getattr__(name: str) -> Any:
    """Load a public export on first access."""

    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return eager and lazy public names."""

    return sorted({*globals(), *__all__})
