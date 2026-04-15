"""Helpers for managing stored task and pipeline specs.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.4], [CLI-1.4.1]
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/12-Pipeline_Composition_and_UX.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from weft.builtins import builtin_task_specs, builtin_tasks_dir
from weft.context import WeftContext, build_context
from weft.core.pipelines import (
    generate_pipeline_example,
    validate_pipeline_spec_payload,
)
from weft.core.taskspec import validate_taskspec

SPEC_TYPE_TASK = "task"
SPEC_TYPE_PIPELINE = "pipeline"
SPEC_SOURCE_FILE: Final[Literal["file"]] = "file"
SPEC_SOURCE_STORED: Final[Literal["stored"]] = "stored"
SPEC_SOURCE_BUILTIN: Final[Literal["builtin"]] = "builtin"
_SPEC_ENTRY_FILES: Final[dict[str, str]] = {
    SPEC_TYPE_TASK: "taskspec.json",
    SPEC_TYPE_PIPELINE: "pipeline.json",
}


@dataclass(frozen=True, slots=True)
class ResolvedSpecReference:
    """Resolved explicit spec reference from file, project store, or builtins."""

    spec_type: str
    name: str
    path: Path
    bundle_root: Path | None
    payload: dict[str, Any]
    source: Literal["file", "stored", "builtin"]


def _spec_root(context: WeftContext) -> Path:
    return context.weft_dir


def _spec_dir(context: WeftContext, spec_type: str) -> Path:
    if spec_type == SPEC_TYPE_TASK:
        return _spec_root(context) / "tasks"
    if spec_type == SPEC_TYPE_PIPELINE:
        return _spec_root(context) / "pipelines"
    raise ValueError(f"Unknown spec type: {spec_type}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to read JSON from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Spec file {path} must contain a JSON object")
    return payload


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _spec_path(context: WeftContext, spec_type: str, name: str) -> Path:
    directory = _spec_dir(context, spec_type)
    return directory / f"{name}.json"


def _spec_bundle_path(context: WeftContext, spec_type: str, name: str) -> Path:
    directory = _spec_dir(context, spec_type)
    return directory / name / _spec_entry_file(spec_type)


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


def _spec_entry_file(spec_type: str) -> str:
    try:
        return _SPEC_ENTRY_FILES[spec_type]
    except KeyError as exc:
        raise ValueError(f"Unknown spec type: {spec_type}") from exc


def _spec_name_from_path(path: Path, *, spec_type: str) -> str:
    if path.name == _spec_entry_file(spec_type):
        return path.parent.name
    return path.stem


def _resolve_explicit_spec_path(path: Path, *, spec_type: str) -> Path:
    if path.is_dir():
        entry = path / _spec_entry_file(spec_type)
        if entry.is_file():
            return entry
        raise FileNotFoundError(
            f"Spec bundle directory '{path}' does not contain {_spec_entry_file(spec_type)!r}"
        )
    return path


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


def _resolve_named_candidate(
    context: WeftContext,
    *,
    spec_type: str,
    name: str,
) -> ResolvedSpecReference | None:
    stored_path = _spec_path(context, spec_type, name)
    if stored_path.exists():
        return _resolved_spec(
            spec_type=spec_type,
            name=name,
            path=stored_path,
            source=SPEC_SOURCE_STORED,
        )
    stored_bundle_path = _spec_bundle_path(context, spec_type, name)
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
    """Resolve a stored or builtin spec name."""
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


def resolve_spec_reference(
    reference: str | Path,
    *,
    spec_type: str,
    context_path: Path | None = None,
) -> ResolvedSpecReference:
    """Resolve an explicit file-or-name spec reference using current CLI rules.

    Resolution order is:
    1. existing file path
    2. local stored spec
    3. builtin task spec fallback (task specs only)

    Spec: docs/specifications/10-CLI_Interface.md [CLI-1.4],
    docs/specifications/10B-Builtin_TaskSpecs.md
    """
    path = Path(reference)
    if path.exists():
        resolved_path = _resolve_explicit_spec_path(path, spec_type=spec_type)
        return _resolved_spec(
            spec_type=spec_type,
            name=_spec_name_from_path(resolved_path, spec_type=spec_type),
            path=resolved_path,
            source=SPEC_SOURCE_FILE,
        )
    return resolve_named_spec(
        str(reference),
        spec_type=spec_type,
        context_path=context_path,
    )


def load_spec(
    name: str, *, spec_type: str | None = None, context_path: Path | None = None
) -> tuple[str, Path, dict[str, Any]]:
    resolved = resolve_named_spec(name, spec_type=spec_type, context_path=context_path)
    return resolved.spec_type, resolved.path, resolved.payload


def list_specs(
    *, spec_type: str | None = None, context_path: Path | None = None
) -> list[dict[str, Any]]:
    context = build_context(spec_context=context_path)
    kinds = [spec_type] if spec_type else [SPEC_TYPE_TASK, SPEC_TYPE_PIPELINE]
    specs: list[dict[str, Any]] = []
    for kind in kinds:
        local_names: set[str] = set()
        directory = _spec_dir(context, kind)
        if not directory.exists():
            pass
        else:
            for path in sorted(directory.glob("*.json")):
                local_names.add(path.stem)
                specs.append(
                    {
                        "type": kind,
                        "name": path.stem,
                        "path": str(path),
                        "source": SPEC_SOURCE_STORED,
                    }
                )
            for bundle_dir in sorted(
                path for path in directory.iterdir() if path.is_dir()
            ):
                entry = bundle_dir / _spec_entry_file(kind)
                if not entry.is_file():
                    continue
                if bundle_dir.name in local_names:
                    continue
                local_names.add(bundle_dir.name)
                specs.append(
                    {
                        "type": kind,
                        "name": bundle_dir.name,
                        "path": str(entry),
                        "source": SPEC_SOURCE_STORED,
                    }
                )

        builtin_paths = builtin_task_specs() if kind == SPEC_TYPE_TASK else ()
        for path in builtin_paths:
            name = _spec_name_from_path(path, spec_type=kind)
            if name in local_names:
                continue
            specs.append(
                {
                    "type": kind,
                    "name": name,
                    "path": str(path),
                    "source": SPEC_SOURCE_BUILTIN,
                }
            )
    return specs


def create_spec(
    name: str,
    *,
    spec_type: str,
    source_path: Path,
    context_path: Path | None = None,
    force: bool = False,
) -> Path:
    context = build_context(spec_context=context_path)
    dest = _spec_path(context, spec_type, name)
    _ensure_directory(dest.parent)
    if dest.exists() and not force:
        raise FileExistsError(f"Spec '{name}' already exists at {dest}")

    payload = _read_json(source_path)

    if spec_type == SPEC_TYPE_TASK:
        valid, errors = validate_taskspec(json.dumps(payload))
        if not valid:
            raise ValueError(f"Invalid TaskSpec: {errors}")
    elif spec_type == SPEC_TYPE_PIPELINE:
        valid, errors = validate_pipeline_spec_payload(payload)
        if not valid:
            raise ValueError(f"Invalid pipeline spec: {errors}")
    else:
        raise ValueError(f"Unknown spec type: {spec_type}")

    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


def delete_spec(
    name: str, *, spec_type: str | None = None, context_path: Path | None = None
) -> Path:
    resolved = resolve_named_spec(
        name,
        spec_type=spec_type,
        context_path=context_path,
    )
    if resolved.source == SPEC_SOURCE_BUILTIN:
        raise ValueError(f"Builtin spec '{name}' is read-only and cannot be deleted")
    resolved.path.unlink()
    return resolved.path


def generate_spec(spec_type: str) -> dict[str, Any]:
    if spec_type == SPEC_TYPE_TASK:
        return {
            "name": "example-task",
            "spec": {
                "type": "function",
                "function_target": "module:function",
            },
            "metadata": {},
        }
    if spec_type == SPEC_TYPE_PIPELINE:
        return generate_pipeline_example()
    raise ValueError(f"Unknown spec type: {spec_type}")


def infer_spec_type(path: Path) -> str:
    payload = _read_json(path)
    return SPEC_TYPE_PIPELINE if "stages" in payload else SPEC_TYPE_TASK


def validate_spec(
    path: Path, *, spec_type: str | None = None
) -> tuple[bool, dict[str, Any]]:
    payload = _read_json(path)
    if spec_type is None:
        spec_type = SPEC_TYPE_PIPELINE if "stages" in payload else SPEC_TYPE_TASK

    if spec_type == SPEC_TYPE_TASK:
        return validate_taskspec(json.dumps(payload))
    if spec_type == SPEC_TYPE_PIPELINE:
        return validate_pipeline_spec_payload(payload)
    return False, {"type": f"Unknown spec type: {spec_type}"}


def normalize_spec_type(spec_type: str) -> str:
    lowered = spec_type.lower()
    if lowered in {SPEC_TYPE_TASK, "tasks"}:
        return SPEC_TYPE_TASK
    if lowered in {SPEC_TYPE_PIPELINE, "pipelines"}:
        return SPEC_TYPE_PIPELINE
    raise ValueError(f"Unknown spec type: {spec_type}")
