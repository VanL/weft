"""Builtin inventory commands.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-6]
- docs/specifications/10B-Builtin_TaskSpecs.md
"""

from __future__ import annotations

import json

from weft.builtins import builtin_task_catalog


def cmd_system_builtins(*, json_output: bool = False) -> tuple[int, str | None]:
    """Return the shipped builtin TaskSpec inventory.

    This command reports what Weft ships, not the project-resolved spec
    namespace. Local shadows in `.weft/tasks/` do not affect this output.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-6],
    docs/specifications/10B-Builtin_TaskSpecs.md
    """

    builtins = builtin_task_catalog()
    if json_output:
        payload = [
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
            for item in builtins
        ]
        return 0, json.dumps(payload, ensure_ascii=False)

    if not builtins:
        return 0, "No builtins shipped"

    lines: list[str] = []
    for index, item in enumerate(builtins):
        if index:
            lines.append("")
        lines.append(f"task: {item.name}")
        if item.category:
            lines.append(f"  Category: {item.category}")
        if item.description:
            lines.append(f"  Description: {item.description}")
        if item.function_target:
            lines.append(f"  Target: {item.function_target}")
        if item.supported_platforms is not None:
            lines.append("  Platforms: " + ", ".join(item.supported_platforms))
    return 0, "\n".join(lines)


__all__ = ["cmd_system_builtins"]
