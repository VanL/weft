"""Helpers for managing stored task and pipeline specs.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.3]
- docs/specifications/09-Implementation_Plan.md [IP-1], [IP-1.0], [IP-1.1]
- docs/specifications/10-CLI_Interface.md [CLI-1.4], [CLI-1.4.1]
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/12-Pipeline_Composition_and_UX.md
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from weft._constants import (
    SPEC_SOURCE_BUILTIN,
    SPEC_SOURCE_FILE,
    SPEC_SOURCE_STORED,
    SPEC_TYPE_PIPELINE,
    SPEC_TYPE_TASK,
)
from weft._exceptions import SpecNotFound
from weft.builtins import builtin_task_specs
from weft.commands.types import SpecRecord, SpecValidationResult
from weft.context import WeftContext, build_context
from weft.core.agents.validation import (
    validate_taskspec_agent_runtime,
    validate_taskspec_agent_tool_profile,
)
from weft.core.pipelines import (
    generate_pipeline_example,
    validate_pipeline_spec_payload,
)
from weft.core.runner_validation import (
    validate_taskspec_runner,
    validate_taskspec_runner_environment,
)
from weft.core.spec_store import (
    ResolvedSpecReference,
    load_resolved_spec_reference,
    read_spec_json,
    spec_directory,
    spec_entry_filename,
    stored_spec_path,
)
from weft.core.spec_store import (
    resolve_named_spec as core_resolve_named_spec,
)
from weft.core.taskspec import (
    apply_bundle_root_to_taskspec_payload,
    bundle_root_from_taskspec_payload,
    validate_taskspec,
)
from weft.core.taskspec.parameterization import validate_parameterization_adapter
from weft.core.taskspec.run_input import validate_run_input_adapter


def _coerce_context(
    *,
    context: WeftContext | None = None,
    context_path: Path | None = None,
) -> WeftContext:
    if context is not None:
        return context
    return build_context(spec_context=context_path)


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _spec_name_from_path(path: Path, *, spec_type: str) -> str:
    if path.name == spec_entry_filename(spec_type):
        return path.parent.name
    return path.stem


def _resolve_explicit_spec_path(path: Path, *, spec_type: str) -> Path:
    if path.is_dir():
        entry = path / spec_entry_filename(spec_type)
        if entry.is_file():
            return entry
        raise FileNotFoundError(
            f"Spec bundle directory '{path}' does not contain {spec_entry_filename(spec_type)!r}"
        )
    return path


def resolve_named_spec(
    name: str,
    *,
    spec_type: str | None = None,
    context: WeftContext | None = None,
    context_path: Path | None = None,
) -> ResolvedSpecReference:
    """Resolve a stored or builtin spec name."""
    ctx = _coerce_context(context=context, context_path=context_path)
    return core_resolve_named_spec(
        name,
        spec_type=spec_type,
        context_path=ctx.root,
    )


def resolve_spec_reference(
    reference: str | Path,
    *,
    spec_type: str,
    context: WeftContext | None = None,
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
    ctx = _coerce_context(context=context, context_path=context_path)
    path = Path(reference)
    if path.exists():
        resolved_path = _resolve_explicit_spec_path(path, spec_type=spec_type)
        return load_resolved_spec_reference(
            spec_type=spec_type,
            name=_spec_name_from_path(resolved_path, spec_type=spec_type),
            path=resolved_path,
            source=SPEC_SOURCE_FILE,
        )
    return resolve_named_spec(
        str(reference),
        spec_type=spec_type,
        context=ctx,
    )


def load_spec(
    name: str,
    *,
    spec_type: str | None = None,
    context: WeftContext | None = None,
    context_path: Path | None = None,
) -> tuple[str, Path, dict[str, Any]]:
    resolved = resolve_named_spec(
        name,
        spec_type=spec_type,
        context=context,
        context_path=context_path,
    )
    return resolved.spec_type, resolved.path, resolved.payload


def list_specs(
    *,
    spec_type: str | None = None,
    context: WeftContext | None = None,
    context_path: Path | None = None,
) -> list[dict[str, Any]]:
    context = _coerce_context(context=context, context_path=context_path)
    kinds = [spec_type] if spec_type else [SPEC_TYPE_TASK, SPEC_TYPE_PIPELINE]
    specs: list[dict[str, Any]] = []
    for kind in kinds:
        local_names: set[str] = set()
        directory = spec_directory(context, kind)
        if directory.exists():
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
                entry = bundle_dir / spec_entry_filename(kind)
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
    context: WeftContext | None = None,
    context_path: Path | None = None,
    force: bool = False,
) -> Path:
    context = _coerce_context(context=context, context_path=context_path)
    dest = stored_spec_path(context, spec_type, name)
    _ensure_directory(dest.parent)
    if dest.exists() and not force:
        raise FileExistsError(f"Spec '{name}' already exists at {dest}")

    payload = read_spec_json(source_path)

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
    name: str,
    *,
    spec_type: str | None = None,
    context: WeftContext | None = None,
    context_path: Path | None = None,
) -> Path:
    resolved = resolve_named_spec(
        name,
        spec_type=spec_type,
        context=context,
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
    payload = read_spec_json(path)
    return SPEC_TYPE_PIPELINE if "stages" in payload else SPEC_TYPE_TASK


def validate_spec(
    path: Path, *, spec_type: str | None = None
) -> tuple[bool, dict[str, Any]]:
    payload = read_spec_json(path)
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


def list_spec_records(
    *,
    spec_type: str | None = None,
    context: WeftContext | None = None,
    context_path: Path | None = None,
) -> list[SpecRecord]:
    """Return structured spec listings."""

    return [
        SpecRecord(
            spec_type=str(item["type"]),
            name=str(item["name"]),
            path=Path(str(item["path"])),
            source=str(item["source"]),
        )
        for item in list_specs(
            spec_type=spec_type,
            context=context,
            context_path=context_path,
        )
    ]


def show_spec(
    name: str,
    *,
    spec_type: str | None = None,
    context: WeftContext | None = None,
    context_path: Path | None = None,
) -> dict[str, Any]:
    """Return a serialized stored or builtin spec payload."""

    try:
        _resolved_type, _path, payload = load_spec(
            name,
            spec_type=spec_type,
            context=context,
            context_path=context_path,
        )
    except FileNotFoundError as exc:
        raise SpecNotFound(str(exc)) from exc
    return payload


def create_spec_record(
    name: str,
    source: Path | dict[str, Any],
    *,
    spec_type: str = SPEC_TYPE_TASK,
    force: bool = False,
    context: WeftContext | None = None,
    context_path: Path | None = None,
) -> SpecRecord:
    """Create a stored spec from a path or serialized payload."""

    context = _coerce_context(context=context, context_path=context_path)
    if isinstance(source, Path):
        created_path = create_spec(
            name,
            spec_type=spec_type,
            source_path=source,
            context=context,
            force=force,
        )
    else:
        destination = stored_spec_path(context, spec_type, name)
        _ensure_directory(destination.parent)
        if destination.exists() and not force:
            raise FileExistsError(f"Spec '{name}' already exists at {destination}")
        payload = dict(source)
        if spec_type == SPEC_TYPE_TASK:
            valid, errors = validate_taskspec(json.dumps(payload))
            if not valid:
                raise ValueError(f"Invalid TaskSpec: {errors}")
        elif spec_type == SPEC_TYPE_PIPELINE:
            valid, errors = validate_pipeline_spec_payload(payload)
            if not valid:
                raise ValueError(f"Invalid pipeline spec: {errors}")
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        created_path = destination
    return SpecRecord(
        spec_type=spec_type,
        name=name,
        path=created_path,
        source=SPEC_SOURCE_STORED,
    )


def validate_spec_source(
    source: Path | dict[str, Any],
    *,
    spec_type: str | None = None,
    load_runner: bool = False,
    preflight: bool = False,
    context: WeftContext | None = None,
    context_path: Path | None = None,
) -> SpecValidationResult:
    """Validate a spec source and return a structured result."""

    if isinstance(source, Path):
        resolved_source = source
        if not source.is_absolute() and (
            context is not None or context_path is not None
        ):
            ctx = _coerce_context(context=context, context_path=context_path)
            resolved_source = (ctx.root / source).resolve(strict=False)

        payload = read_spec_json(resolved_source)
        effective_type = spec_type or (
            SPEC_TYPE_PIPELINE if "stages" in payload else SPEC_TYPE_TASK
        )
        bundle_root = (
            resolved_source.parent
            if resolved_source.name == spec_entry_filename(SPEC_TYPE_TASK)
            else bundle_root_from_taskspec_payload(payload)
        )
    else:
        payload = deepcopy(source)
        effective_type = spec_type or (
            SPEC_TYPE_PIPELINE if "stages" in payload else SPEC_TYPE_TASK
        )
        bundle_root = bundle_root_from_taskspec_payload(payload)

    if effective_type == SPEC_TYPE_TASK:
        result = validate_task_spec_text(
            json.dumps(payload),
            bundle_root=bundle_root,
            load_runner=load_runner,
            preflight=preflight,
        )
        if result.payload is None:
            return replace(result, payload=deepcopy(payload))
        return result
    if effective_type == SPEC_TYPE_PIPELINE:
        if load_runner or preflight:
            return _validation_failure(
                spec_type=SPEC_TYPE_PIPELINE,
                stage="options",
                field="options",
                message="--load-runner and --preflight only apply to task specs",
                payload=payload,
            )
        valid, errors = validate_pipeline_spec_payload(payload)
        normalized_errors = _normalize_validation_errors(errors)
        return SpecValidationResult(
            valid=bool(valid),
            spec_type=SPEC_TYPE_PIPELINE,
            errors=[] if valid else list(normalized_errors.values()),
            payload=payload,
            errors_by_stage=(
                {} if valid else {"schema": normalized_errors}
            ),
        )
    return _validation_failure(
        spec_type=effective_type,
        stage="schema",
        field="type",
        message=f"Unknown spec type: {effective_type}",
        payload=payload,
    )


def validate_task_spec_text(
    json_content: str,
    *,
    bundle_root: str | Path | None = None,
    load_runner: bool = False,
    preflight: bool = False,
) -> SpecValidationResult:
    """Validate raw TaskSpec JSON through the shared ordered capability.

    Spec: docs/specifications/01-Core_Components.md [CC-3.3];
    docs/specifications/09-Implementation_Plan.md [IP-1.1]
    """

    load_runner = load_runner or preflight
    valid, errors = validate_taskspec(json_content)
    if not valid:
        normalized_errors = _normalize_validation_errors(errors)
        return SpecValidationResult(
            valid=False,
            spec_type=SPEC_TYPE_TASK,
            errors=list(normalized_errors.values()),
            errors_by_stage={"schema": normalized_errors},
        )

    payload = json.loads(json_content)
    if not isinstance(payload, dict):  # pragma: no cover - schema contract
        return _validation_failure(
            spec_type=SPEC_TYPE_TASK,
            stage="schema",
            field="_json",
            message="TaskSpec JSON root must be an object",
        )
    return _validate_task_spec_payload(
        payload,
        bundle_root=bundle_root,
        load_runner=load_runner,
        preflight=preflight,
    )


def _validate_task_spec_payload(
    payload: dict[str, Any],
    *,
    bundle_root: str | Path | None,
    load_runner: bool,
    preflight: bool,
) -> SpecValidationResult:
    visible_payload = deepcopy(payload)
    validation_payload = deepcopy(payload)
    apply_bundle_root_to_taskspec_payload(validation_payload, bundle_root)

    try:
        _validate_taskspec_parameterization(validation_payload)
    except Exception as exc:  # pragma: no cover - validation probe boundary
        return _validation_failure(
            spec_type=SPEC_TYPE_TASK,
            stage="parameterization",
            field="parameterization",
            message=str(exc),
            payload=visible_payload,
        )

    try:
        _validate_taskspec_run_input(validation_payload)
    except Exception as exc:  # pragma: no cover - validation probe boundary
        return _validation_failure(
            spec_type=SPEC_TYPE_TASK,
            stage="run_input",
            field="run_input",
            message=str(exc),
            payload=visible_payload,
        )

    if load_runner:
        try:
            materialized_environment = validate_taskspec_runner_environment(
                validation_payload
            )
        except Exception as exc:  # pragma: no cover - validation probe boundary
            return _validation_failure(
                spec_type=SPEC_TYPE_TASK,
                stage="environment_profile",
                field="environment_profile",
                message=str(exc),
                payload=visible_payload,
            )

        try:
            validate_taskspec_runner(
                validation_payload,
                load_runner=True,
                preflight=preflight,
                materialized_environment=materialized_environment,
            )
        except Exception as exc:  # pragma: no cover - validation probe boundary
            return _validation_failure(
                spec_type=SPEC_TYPE_TASK,
                stage="runner",
                field="runner",
                message=str(exc),
                payload=visible_payload,
            )

        try:
            validate_taskspec_agent_runtime(
                validation_payload,
                load_runtime=True,
                preflight=preflight,
            )
        except Exception as exc:  # pragma: no cover - validation probe boundary
            return _validation_failure(
                spec_type=SPEC_TYPE_TASK,
                stage="agent_runtime",
                field="agent_runtime",
                message=str(exc),
                payload=visible_payload,
            )

        if _agent_runtime_name(validation_payload) == "provider_cli":
            try:
                validate_taskspec_agent_tool_profile(
                    validation_payload,
                    load_runtime=True,
                    preflight=preflight,
                )
            except Exception as exc:  # pragma: no cover - validation probe boundary
                return _validation_failure(
                    spec_type=SPEC_TYPE_TASK,
                    stage="tool_profile",
                    field="tool_profile",
                    message=str(exc),
                    payload=visible_payload,
                )

    return SpecValidationResult(
        valid=True,
        spec_type=SPEC_TYPE_TASK,
        payload=visible_payload,
    )


def _validate_taskspec_parameterization(payload: dict[str, Any]) -> None:
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return
    parameterization = spec.get("parameterization")
    if not isinstance(parameterization, dict):
        return
    adapter_ref = parameterization.get("adapter_ref")
    if isinstance(adapter_ref, str):
        validate_parameterization_adapter(
            adapter_ref,
            bundle_root=bundle_root_from_taskspec_payload(payload),
        )


def _validate_taskspec_run_input(payload: dict[str, Any]) -> None:
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return
    run_input = spec.get("run_input")
    if not isinstance(run_input, dict):
        return
    adapter_ref = run_input.get("adapter_ref")
    if isinstance(adapter_ref, str):
        validate_run_input_adapter(
            adapter_ref,
            bundle_root=bundle_root_from_taskspec_payload(payload),
        )


def _agent_runtime_name(payload: dict[str, Any]) -> str | None:
    spec = payload.get("spec")
    if not isinstance(spec, dict) or spec.get("type") != "agent":
        return None
    agent = spec.get("agent")
    if not isinstance(agent, dict):
        return None
    runtime = agent.get("runtime")
    return runtime if isinstance(runtime, str) else None


def _normalize_validation_errors(errors: object) -> dict[str, str]:
    if isinstance(errors, dict):
        return {str(field): str(message) for field, message in errors.items()}
    return {"validation": str(errors)}


def _validation_failure(
    *,
    spec_type: str,
    stage: str,
    field: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> SpecValidationResult:
    return SpecValidationResult(
        valid=False,
        spec_type=spec_type,
        errors=[message],
        payload=payload,
        errors_by_stage={stage: {field: message}},
    )
