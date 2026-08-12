"""Builtin inventory commands.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-6]
- docs/specifications/10B-Builtin_TaskSpecs.md#current-contract
"""

from __future__ import annotations

from typing import Any

from weft.builtins import builtin_task_catalog
from weft.commands.types import BuiltinSpecRecord


def list_builtins() -> list[dict[str, Any]]:
    """Return the builtin task inventory as serialized rows."""

    return [
        {
            "type": "task",
            "name": item.name,
            "description": item.description,
            "category": item.category,
            "function_target": item.function_target,
            "supported_platforms": (
                list(item.supported_platforms)
                if item.supported_platforms is not None
                else None
            ),
            "path": str(item.path),
            "source": item.source,
        }
        for item in builtin_task_catalog()
    ]


def cmd_system_builtins() -> tuple[BuiltinSpecRecord, ...]:
    """Return the shipped builtin TaskSpec inventory as typed records.

    This command reports what Weft ships, not the project-resolved spec
    namespace. Local shadows in the project's stored-task namespace do not
    affect this output.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-6],
    docs/specifications/10B-Builtin_TaskSpecs.md#current-contract
    """

    return tuple(
        BuiltinSpecRecord(
            name=item.name,
            description=item.description,
            category=item.category,
            function_target=item.function_target,
            supported_platforms=tuple(item.supported_platforms or ()),
            path=item.path,
            source=item.source,
        )
        for item in builtin_task_catalog()
    )


__all__ = ["cmd_system_builtins", "list_builtins"]
