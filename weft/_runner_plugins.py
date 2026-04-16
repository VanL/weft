"""Runner plugin contracts and resolver support.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.1]
- docs/specifications/02-TaskSpec.md [TS-1.3]
"""

from __future__ import annotations

from importlib import metadata
from typing import Any, cast

from weft._constants import (
    DEFAULT_RUNNER_NAME,
    RUNNER_ENTRY_POINT_GROUP,
    RUNNER_PLUGIN_EXTRA_INSTALL_HINTS,
)
from weft.ext import RunnerPlugin


def _load_entry_point_plugin(name: str) -> RunnerPlugin:
    """Load an external runner plugin by entry point name."""
    entry_points_obj: Any = metadata.entry_points()
    if hasattr(entry_points_obj, "select"):
        matches = entry_points_obj.select(group=RUNNER_ENTRY_POINT_GROUP, name=name)
    else:  # pragma: no cover - Python <3.10 compatibility fallback
        matches = [
            entry_point
            for entry_point in entry_points_obj.get(RUNNER_ENTRY_POINT_GROUP, [])
            if entry_point.name == name
        ]

    for entry_point in matches:
        loaded = entry_point.load()
        plugin = loaded() if callable(loaded) else loaded
        if getattr(plugin, "name", None) != name:
            raise RuntimeError(
                f"Runner plugin '{name}' resolved to object with mismatched name "
                f"'{getattr(plugin, 'name', None)}'"
            )
        return cast(RunnerPlugin, plugin)

    raise RuntimeError(f"Unknown runner plugin: {name}")


def get_runner_plugin(name: str) -> RunnerPlugin:
    """Return the built-in or installed runner plugin by name."""
    normalized = name.strip()
    if normalized == DEFAULT_RUNNER_NAME:
        from weft.core.runners.host import get_runner_plugin as get_host_runner_plugin

        return get_host_runner_plugin()
    return _load_entry_point_plugin(normalized)


def require_runner_plugin(name: str) -> RunnerPlugin:
    """Resolve a runner plugin and rewrite missing-plugin errors into install hints."""
    try:
        return get_runner_plugin(name)
    except RuntimeError as exc:
        if str(exc) != f"Unknown runner plugin: {name.strip()}":
            raise
        extra = RUNNER_PLUGIN_EXTRA_INSTALL_HINTS.get(name.strip())
        if extra:
            raise RuntimeError(
                f"Requested runner '{name.strip()}' is not available. Install {extra}."
            ) from exc
        raise RuntimeError(
            f"Requested runner '{name.strip()}' is not available."
        ) from exc
