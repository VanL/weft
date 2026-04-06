"""Helpers for managing stored task and pipeline specs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from weft.context import WeftContext, build_context
from weft.core.taskspec import validate_taskspec

SPEC_TYPE_TASK = "task"
SPEC_TYPE_PIPELINE = "pipeline"


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


def load_spec(
    name: str, *, spec_type: str | None = None, context_path: Path | None = None
) -> tuple[str, Path, dict[str, Any]]:
    context = build_context(spec_context=context_path)
    candidates: list[tuple[str, Path]] = []
    if spec_type:
        candidates.append((spec_type, _spec_path(context, spec_type, name)))
    else:
        for kind in (SPEC_TYPE_TASK, SPEC_TYPE_PIPELINE):
            candidates.append((kind, _spec_path(context, kind, name)))

    existing = [(kind, path) for kind, path in candidates if path.exists()]
    if not existing:
        raise FileNotFoundError(f"Spec '{name}' not found")
    if len(existing) > 1:
        raise ValueError(
            f"Spec '{name}' exists as multiple types; pass --type to disambiguate"
        )
    kind, path = existing[0]
    return kind, path, _read_json(path)


def list_specs(
    *, spec_type: str | None = None, context_path: Path | None = None
) -> list[dict[str, Any]]:
    context = build_context(spec_context=context_path)
    kinds = [spec_type] if spec_type else [SPEC_TYPE_TASK, SPEC_TYPE_PIPELINE]
    specs: list[dict[str, Any]] = []
    for kind in kinds:
        directory = _spec_dir(context, kind)
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            specs.append({"type": kind, "name": path.stem, "path": str(path)})
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
        if "stages" not in payload:
            raise ValueError("Pipeline spec must include 'stages'")
    else:
        raise ValueError(f"Unknown spec type: {spec_type}")

    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


def delete_spec(
    name: str, *, spec_type: str | None = None, context_path: Path | None = None
) -> Path:
    kind, path, _payload = load_spec(
        name, spec_type=spec_type, context_path=context_path
    )
    path.unlink()
    return path


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
        return {
            "name": "example-pipeline",
            "stages": [
                {
                    "name": "stage-1",
                    "task": "task-name",
                }
            ],
        }
    raise ValueError(f"Unknown spec type: {spec_type}")


def validate_spec(
    path: Path, *, spec_type: str | None = None
) -> tuple[bool, dict[str, Any]]:
    payload = _read_json(path)
    if spec_type is None:
        spec_type = SPEC_TYPE_PIPELINE if "stages" in payload else SPEC_TYPE_TASK

    if spec_type == SPEC_TYPE_TASK:
        return validate_taskspec(json.dumps(payload))
    if spec_type == SPEC_TYPE_PIPELINE:
        if "stages" not in payload:
            return False, {"stages": "pipeline spec must include 'stages'"}
        return True, {}
    return False, {"type": f"Unknown spec type: {spec_type}"}


def normalize_spec_type(spec_type: str) -> str:
    lowered = spec_type.lower()
    if lowered in {SPEC_TYPE_TASK, "tasks"}:
        return SPEC_TYPE_TASK
    if lowered in {SPEC_TYPE_PIPELINE, "pipelines"}:
        return SPEC_TYPE_PIPELINE
    raise ValueError(f"Unknown spec type: {spec_type}")
