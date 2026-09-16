"""Deferred native title dependencies (Spec: 01-Core_Components.md [CC-2.4])."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


def get_setproctitle() -> ModuleType:
    """Load stock title support only when the caller is ready for its effects."""
    # Optional native dependency: on macOS import itself registers with LaunchServices.
    import setproctitle

    return setproctitle


def get_processtitle() -> ModuleType:
    """Load the macOS Unix-only title backend without preparing it."""
    # Native dependency loaded lazily so process-title setup stays explicit.
    return import_module("processtitle")
