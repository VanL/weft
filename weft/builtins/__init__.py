"""Builtin Weft helper assets and support code.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.4]
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class BuiltinTaskInfo:
    """Structured metadata for a shipped builtin TaskSpec asset.

    Spec: docs/specifications/10B-Builtin_TaskSpecs.md
    """

    name: str
    description: str
    category: str | None
    function_target: str | None
    path: Path
    source: str = "builtin"


def builtin_tasks_dir() -> Path:
    """Return the shipped builtin TaskSpec directory.

    Spec: docs/specifications/10B-Builtin_TaskSpecs.md
    """
    return Path(__file__).resolve().parent / "tasks"


def builtin_task_specs() -> tuple[Path, ...]:
    """Return the shipped builtin TaskSpec assets in stable name order.

    Spec: docs/specifications/10B-Builtin_TaskSpecs.md
    """

    directory = builtin_tasks_dir()
    if not directory.exists():
        return ()
    entries: list[tuple[str, Path]] = []
    for path in sorted(directory.glob("*.json")):
        if path.is_file():
            entries.append((path.stem, path))
    for bundle_dir in sorted(path for path in directory.iterdir() if path.is_dir()):
        entry = bundle_dir / "taskspec.json"
        if entry.is_file():
            entries.append((bundle_dir.name, entry))
    entries.sort(key=lambda item: item[0])
    return tuple(path for _, path in entries)


def builtin_task_names() -> tuple[str, ...]:
    """Return the shipped builtin TaskSpec names in stable order.

    Spec: docs/specifications/10B-Builtin_TaskSpecs.md
    """

    return tuple(_builtin_task_name(path) for path in builtin_task_specs())


def builtin_task_catalog() -> tuple[BuiltinTaskInfo, ...]:
    """Return the shipped builtin TaskSpecs with public summary metadata.

    Spec: docs/specifications/10B-Builtin_TaskSpecs.md
    """

    entries: list[BuiltinTaskInfo] = []
    for path in builtin_task_specs():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):  # pragma: no cover - defensive
            raise ValueError(f"Builtin TaskSpec {path} must contain a JSON object")
        spec = payload.get("spec")
        metadata = payload.get("metadata")
        if not isinstance(spec, dict):  # pragma: no cover - defensive
            raise ValueError(f"Builtin TaskSpec {path} is missing a spec object")
        if metadata is None:
            metadata_map: dict[str, Any] = {}
        elif isinstance(metadata, dict):
            metadata_map = metadata
        else:  # pragma: no cover - defensive
            raise ValueError(f"Builtin TaskSpec {path} metadata must be an object")
        description = payload.get("description")
        if description is None:
            description_text = ""
        elif isinstance(description, str):
            description_text = description
        else:  # pragma: no cover - defensive
            raise ValueError(f"Builtin TaskSpec {path} description must be a string")
        category = metadata_map.get("category")
        if category is not None and not isinstance(category, str):  # pragma: no cover
            raise ValueError(f"Builtin TaskSpec {path} category must be a string")
        function_target = spec.get("function_target")
        if function_target is not None and not isinstance(function_target, str):
            raise ValueError(
                f"Builtin TaskSpec {path} function_target must be a string"
            )
        entries.append(
            BuiltinTaskInfo(
                name=_builtin_task_name(path),
                description=description_text,
                category=category,
                function_target=function_target,
                path=path,
            )
        )
    return tuple(entries)


def _builtin_task_name(path: Path) -> str:
    if path.name == "taskspec.json":
        return path.parent.name
    return path.stem


__all__ = [
    "BuiltinTaskInfo",
    "builtin_task_catalog",
    "builtin_task_names",
    "builtin_task_specs",
    "builtin_tasks_dir",
]
