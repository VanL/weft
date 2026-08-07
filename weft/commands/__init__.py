"""Lazy public facade for capabilities shared by CLI and Python clients."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import manager
    from .init import cmd_init
    from .result import cmd_result
    from .serve import serve_command
    from .status import cmd_status
    from .tidy import cmd_tidy

__all__ = [  # noqa: RUF022 approved [TS-3.1] [RUFF-SUP-244] exception
    "cmd_init",
    "cmd_result",
    "serve_command",
    "cmd_status",
    "cmd_tidy",
    "manager",
]

_LAZY_EXPORTS = {
    "cmd_init": ("weft.commands.init", "cmd_init"),
    "cmd_result": ("weft.commands.result", "cmd_result"),
    "serve_command": ("weft.commands.serve", "serve_command"),
    "cmd_status": ("weft.commands.status", "cmd_status"),
    "cmd_tidy": ("weft.commands.tidy", "cmd_tidy"),
}
_LAZY_MODULES = {"manager": "weft.commands.manager"}


def __getattr__(name: str) -> Any:
    """Load a public command capability on first access."""

    module_name = _LAZY_MODULES.get(name)
    if module_name is not None:
        module: ModuleType = import_module(module_name)
        globals()[name] = module
        return module
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
