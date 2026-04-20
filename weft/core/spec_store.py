"""Named spec resolution shared by CLI and manager runtime.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.4], [CLI-1.4.1]
- docs/specifications/10B-Builtin_TaskSpecs.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from weft._constants import (
    SPEC_ENTRY_FILES,
    SPEC_SOURCE_BUILTIN,
    SPEC_SOURCE_STORED,
    SPEC_TYPE_PIPELINE,
    SPEC_TYPE_TASK,
)
from weft.builtins import builtin_tasks_dir
from weft.context import WeftContext, build_context


@dataclass(frozen=True, slots=True)
class ResolvedSpecReference:
    """Resolved spec from an explicit file path, project storage, or builtins."""

    spec_type: str
    name: str
    path: Path
    bundle_root: Path | None
    payload: dict[str, Any]
    source: Literal["file", "stored", "builtin"]


def spec_root(context: WeftContext) -> Path:
    """Return the Weft metadata root that stores named task and pipeline specs."""

    return _spec_root(context)


def _spec_root(context: WeftContext) -> Path:
    return context.weft_dir


def spec_directory(context: WeftContext, spec_type: str) -> Path:
    """Return the directory that stores one spec type for this context."""

    return _spec_dir(context, spec_type)


def _spec_dir(context: WeftContext, spec_type: str) -> Path:
    return _spec_dir_from_root(_spec_root(context), spec_type)


def _spec_dir_from_root(spec_root: Path, spec_type: str) -> Path:
    if spec_type == SPEC_TYPE_TASK:
        return spec_root / "tasks"
    if spec_type == SPEC_TYPE_PIPELINE:
        return spec_root / "pipelines"
    raise ValueError(f"Unknown spec type: {spec_type}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to read JSON from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Spec file {path} must contain a JSON object")
    return payload


def read_spec_json(path: Path) -> dict[str, Any]:
    """Read and validate one stored spec payload from disk."""

    return _read_json(path)


def spec_entry_filename(spec_type: str) -> str:
    """Return the canonical bundle entry filename for one spec type."""

    return _spec_entry_file(spec_type)


def _spec_entry_file(spec_type: str) -> str:
    try:
        return SPEC_ENTRY_FILES[spec_type]
    except KeyError as exc:
        raise ValueError(f"Unknown spec type: {spec_type}") from exc


def stored_spec_path(context: WeftContext, spec_type: str, name: str) -> Path:
    """Return the flat-file storage path for one named stored spec."""

    return _spec_path(context, spec_type, name)


def _spec_path(context: WeftContext, spec_type: str, name: str) -> Path:
    return _spec_dir(context, spec_type) / f"{name}.json"


def _spec_bundle_path(context: WeftContext, spec_type: str, name: str) -> Path:
    return _spec_dir(context, spec_type) / name / _spec_entry_file(spec_type)


def _spec_path_from_root(spec_root: Path, spec_type: str, name: str) -> Path:
    return _spec_dir_from_root(spec_root, spec_type) / f"{name}.json"


def _spec_bundle_path_from_root(spec_root: Path, spec_type: str, name: str) -> Path:
    return (
        _spec_dir_from_root(spec_root, spec_type) / name / _spec_entry_file(spec_type)
    )


def _builtin_spec_dir(spec_type: str) -> Path | None:
    if spec_type == SPEC_TYPE_TASK:
        return builtin_tasks_dir()
    if spec_type == SPEC_TYPE_PIPELINE:
        return None
    raise ValueError(f"Unknown spec type: {spec_type}")


def _builtin_spec_path(spec_type: str, name: str) -> Path | None:
    directory = _builtin_spec_dir(spec_type)
    if directory is None:
        return None
    return directory / f"{name}.json"


def _builtin_spec_bundle_path(spec_type: str, name: str) -> Path | None:
    directory = _builtin_spec_dir(spec_type)
    if directory is None:
        return None
    return directory / name / _spec_entry_file(spec_type)


def _resolved_spec(
    *,
    spec_type: str,
    name: str,
    path: Path,
    source: Literal["file", "stored", "builtin"],
) -> ResolvedSpecReference:
    bundle_root = path.parent if path.name == _spec_entry_file(spec_type) else None
    return ResolvedSpecReference(
        spec_type=spec_type,
        name=name,
        path=path,
        bundle_root=bundle_root,
        payload=_read_json(path),
        source=source,
    )


def load_resolved_spec_reference(
    *,
    spec_type: str,
    name: str,
    path: Path,
    source: Literal["file", "stored", "builtin"],
) -> ResolvedSpecReference:
    """Build a resolved spec record with payload and bundle metadata loaded."""

    return _resolved_spec(
        spec_type=spec_type,
        name=name,
        path=path,
        source=source,
    )


def _resolve_named_candidate(
    context: WeftContext,
    *,
    spec_type: str,
    name: str,
) -> ResolvedSpecReference | None:
    return _resolve_named_candidate_from_root(
        _spec_root(context),
        spec_type=spec_type,
        name=name,
    )


def _resolve_named_candidate_from_root(
    spec_root: Path,
    *,
    spec_type: str,
    name: str,
) -> ResolvedSpecReference | None:
    stored_path = _spec_path_from_root(spec_root, spec_type, name)
    if stored_path.exists():
        return _resolved_spec(
            spec_type=spec_type,
            name=name,
            path=stored_path,
            source=SPEC_SOURCE_STORED,
        )

    stored_bundle_path = _spec_bundle_path_from_root(spec_root, spec_type, name)
    if stored_bundle_path.exists():
        return _resolved_spec(
            spec_type=spec_type,
            name=name,
            path=stored_bundle_path,
            source=SPEC_SOURCE_STORED,
        )

    builtin_path = _builtin_spec_path(spec_type, name)
    if builtin_path is not None and builtin_path.exists():
        return _resolved_spec(
            spec_type=spec_type,
            name=name,
            path=builtin_path,
            source=SPEC_SOURCE_BUILTIN,
        )

    builtin_bundle_path = _builtin_spec_bundle_path(spec_type, name)
    if builtin_bundle_path is not None and builtin_bundle_path.exists():
        return _resolved_spec(
            spec_type=spec_type,
            name=name,
            path=builtin_bundle_path,
            source=SPEC_SOURCE_BUILTIN,
        )

    return None


def resolve_named_spec(
    name: str,
    *,
    spec_type: str | None = None,
    context_path: Path | None = None,
) -> ResolvedSpecReference:
    """Resolve a named stored or builtin spec.

    Spec: [CLI-1.4.1]
    """

    context = build_context(spec_context=context_path)
    candidates: list[ResolvedSpecReference] = []
    if spec_type:
        candidate = _resolve_named_candidate(
            context,
            spec_type=spec_type,
            name=name,
        )
        if candidate is not None:
            candidates.append(candidate)
    else:
        for kind in (SPEC_TYPE_TASK, SPEC_TYPE_PIPELINE):
            candidate = _resolve_named_candidate(
                context,
                spec_type=kind,
                name=name,
            )
            if candidate is not None:
                candidates.append(candidate)

    if not candidates:
        raise FileNotFoundError(f"Spec '{name}' not found")
    if len(candidates) > 1:
        raise ValueError(
            f"Spec '{name}' exists as multiple types; pass --type to disambiguate"
        )
    return candidates[0]


def resolve_named_spec_from_root(
    spec_root: Path,
    name: str,
    *,
    spec_type: str | None = None,
) -> ResolvedSpecReference:
    """Resolve a named spec from an explicit Weft spec root.

    The spec root is the directory that contains `tasks/` and `pipelines/`.
    This is the Weft metadata directory in the normal project layout (default
    `.weft/`), but tests and explicit autostart directory overrides may use an
    alternate sibling layout.
    """

    candidates: list[ResolvedSpecReference] = []
    if spec_type:
        candidate = _resolve_named_candidate_from_root(
            spec_root,
            spec_type=spec_type,
            name=name,
        )
        if candidate is not None:
            candidates.append(candidate)
    else:
        for kind in (SPEC_TYPE_TASK, SPEC_TYPE_PIPELINE):
            candidate = _resolve_named_candidate_from_root(
                spec_root,
                spec_type=kind,
                name=name,
            )
            if candidate is not None:
                candidates.append(candidate)

    if not candidates:
        raise FileNotFoundError(f"Spec '{name}' not found")
    if len(candidates) > 1:
        raise ValueError(
            f"Spec '{name}' exists as multiple types; pass --type to disambiguate"
        )
    return candidates[0]


__all__ = [
    "ResolvedSpecReference",
    "load_resolved_spec_reference",
    "read_spec_json",
    "resolve_named_spec",
    "resolve_named_spec_from_root",
    "spec_directory",
    "spec_entry_filename",
    "spec_root",
    "stored_spec_path",
]
