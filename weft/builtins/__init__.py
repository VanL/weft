"""Builtin Weft helper assets and support code.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.4]
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PLATFORM_DISPLAY_NAMES = {
    "linux": "Linux",
    "darwin": "macOS",
    "win32": "Windows",
}
DOCKER_BUILTIN_SUPPORTED_PLATFORMS: tuple[str, ...] = ("linux", "darwin")


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
    supported_platforms: tuple[str, ...] | None = None
    source: str = "builtin"


def builtin_platform_supported(
    supported_platforms: Sequence[str] | None,
    *,
    platform: str | None = None,
) -> bool:
    """Return whether the current platform is allowed for a builtin."""
    if supported_platforms is None:
        return True
    return _normalize_platform_key(platform) in set(supported_platforms)


def unsupported_builtin_platform_message(
    name: str,
    supported_platforms: Sequence[str],
    *,
    platform: str | None = None,
) -> str:
    """Return a user-facing unsupported-platform message for a builtin."""
    normalized = _normalize_platform_key(platform)
    supported = _human_platform_list(tuple(supported_platforms))
    current = _platform_display_name(normalized)
    return (
        f"Builtin '{name}' is currently supported only on {supported}; "
        f"{current} is not supported."
    )


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
        supported_platforms = _normalize_supported_platforms(
            metadata_map,
            path=path,
        )
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
                supported_platforms=supported_platforms,
            )
        )
    return tuple(entries)


def _builtin_task_name(path: Path) -> str:
    if path.name == "taskspec.json":
        return path.parent.name
    return path.stem


def _normalize_supported_platforms(
    metadata_map: Mapping[str, Any],
    *,
    path: Path,
) -> tuple[str, ...] | None:
    value = metadata_map.get("supported_platforms")
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(
            f"Builtin TaskSpec {path} supported_platforms must be a list of strings"
        )
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"Builtin TaskSpec {path} supported_platforms must be a list of strings"
            )
        cleaned = _normalize_platform_key(item.strip())
        if cleaned not in _PLATFORM_DISPLAY_NAMES:
            raise ValueError(
                f"Builtin TaskSpec {path} uses unsupported platform key {item!r}"
            )
        if cleaned not in normalized:
            normalized.append(cleaned)
    if not normalized:
        raise ValueError(f"Builtin TaskSpec {path} supported_platforms cannot be empty")
    return tuple(normalized)


def _normalize_platform_key(platform: str | None) -> str:
    candidate = sys.platform if platform is None else platform
    if candidate.startswith("linux"):
        return "linux"
    if candidate == "darwin":
        return "darwin"
    if candidate.startswith("win") or candidate in {"cygwin", "msys"}:
        return "win32"
    return candidate


def _platform_display_name(platform: str) -> str:
    return _PLATFORM_DISPLAY_NAMES.get(platform, platform)


def _human_platform_list(platforms: Sequence[str]) -> str:
    display_names = [_platform_display_name(platform) for platform in platforms]
    if len(display_names) == 1:
        return display_names[0]
    if len(display_names) == 2:
        return f"{display_names[0]} and {display_names[1]}"
    return ", ".join(display_names[:-1]) + f", and {display_names[-1]}"


__all__ = [
    "BuiltinTaskInfo",
    "DOCKER_BUILTIN_SUPPORTED_PLATFORMS",
    "builtin_platform_supported",
    "builtin_task_catalog",
    "builtin_task_names",
    "builtin_task_specs",
    "builtin_tasks_dir",
    "unsupported_builtin_platform_message",
]
