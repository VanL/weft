"""Deferred core dependencies.

Spec: docs/specifications/01-Core_Components.md [CC-2.4];
docs/specifications/06-Resource_Management.md [RM-5.1], [RM-5.2].
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from simplebroker_pg import ConnectionUsage

    from simplebroker import Queue


def get_ctypes() -> ModuleType:
    """Load ctypes for the native libc leaves (Spec: [RM-5.1], [RM-5.2]).

    Callers: the Linux pidfd binding when Python lacks ``os.pidfd_open`` and
    the macOS libproc descendant walk in ``weft.core.process_tree``.
    """
    # Optional native dependency: only those platform leaves need ctypes.
    return import_module("ctypes")


def get_setproctitle() -> ModuleType:
    """Load stock title support only when the caller is ready for its effects."""
    # Optional native dependency: on macOS import itself registers with LaunchServices.
    import setproctitle

    return setproctitle


def get_processtitle() -> ModuleType:
    """Load the macOS Unix-only title backend without preparing it."""
    # Native dependency loaded lazily so process-title setup stays explicit.
    return import_module("processtitle")


def get_task_evidence() -> ModuleType:
    """Break endpoints -> evidence -> spawn_requests -> endpoints imports."""
    return import_module("weft.core.task_evidence")


def get_manager_registry_record_liveness() -> Callable[
    [dict[str, Any] | None], Literal["live", "stale", "unknown"]
]:
    """Break service -> manager_runtime -> launcher -> tasks -> service imports.

    The service idle rule reuses the Manager reader's three-valued registry
    liveness ([MA-1], [LIVENESS.R3]).
    """
    return cast(
        Callable[[dict[str, Any] | None], Literal["live", "stale", "unknown"]],
        import_module("weft.core.manager_runtime").manager_registry_record_liveness,
    )


def get_pg_connection_stats() -> Callable[[Queue], dict[str, int]]:
    """Load the PostgreSQL connection-ceiling reader for a PostgreSQL Queue."""
    # Optional PostgreSQL backend: SQLite installs never load it.
    from simplebroker_pg import get_connection_stats

    return get_connection_stats


def get_pg_connection_usage() -> Callable[[Queue], ConnectionUsage]:
    """Load the PostgreSQL connection-usage reader for a PostgreSQL Queue."""
    # Optional PostgreSQL backend: SQLite installs never load it.
    from simplebroker_pg import get_connection_usage

    return get_connection_usage


def get_pg_bind_connection_owner() -> Callable[[str], None]:
    """Load the PostgreSQL connection-owner binder for a PostgreSQL process."""
    # Optional PostgreSQL backend: SQLite installs never load it.
    from simplebroker_pg import bind_connection_owner

    return bind_connection_owner
