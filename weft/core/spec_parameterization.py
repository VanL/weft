"""Helpers for submission-time TaskSpec materialization.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/02-TaskSpec.md [TS-1]
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from weft.core.imports import import_callable_ref, split_import_ref
from weft.core.spec_run_input import (
    ensure_json_serializable_work_payload,
    parse_declared_option_args,
)


@dataclass(frozen=True, slots=True)
class SpecParameterizationRequest:
    """Submission-time request passed to a spec-owned materialization adapter."""

    arguments: dict[str, str]
    context_root: str | None
    spec_name: str
    taskspec_payload: Mapping[str, Any]


def validate_parameterization_adapter_ref(ref: str) -> str:
    """Validate and normalize a parameterization adapter import ref."""
    normalized = ref.strip()
    if not normalized:
        raise ValueError("spec.parameterization.adapter_ref must be a non-empty string")
    split_import_ref(normalized)
    return normalized


def validate_parameterization_adapter(
    adapter_ref: str,
    *,
    bundle_root: str | Path | None = None,
) -> None:
    """Validate that a parameterization adapter ref is importable and callable."""
    import_callable_ref(adapter_ref, bundle_root=bundle_root)


def parse_declared_parameterization_args(
    tokens: list[str],
    arguments: Mapping[str, Any],
) -> tuple[dict[str, str], list[str]]:
    """Parse parameterization args and return untouched tokens for later stages."""
    parsed = parse_declared_option_args(
        tokens,
        arguments,
        allow_unknown=True,
        apply_defaults=True,
    )
    return parsed.arguments, parsed.remaining_tokens


def invoke_parameterization_adapter(
    adapter_ref: str,
    *,
    request: SpecParameterizationRequest,
    bundle_root: str | Path | None = None,
) -> Mapping[str, Any]:
    """Invoke a parameterization adapter and require a mapping payload."""
    adapter = import_callable_ref(adapter_ref, bundle_root=bundle_root)
    try:
        payload = adapter(request)
    except TypeError as exc:
        raise TypeError(
            f"spec.parameterization adapter {adapter_ref!r} could not be called with "
            "SpecParameterizationRequest"
        ) from exc
    ensure_json_serializable_work_payload(payload)
    if not isinstance(payload, Mapping):
        raise ValueError(
            "spec.parameterization adapter must return a JSON-serializable object"
        )
    return payload


def materialize_taskspec_template(
    taskspec: Any,
    *,
    arguments: Mapping[str, str],
    context_root: str | None,
) -> Any:
    """Materialize one stored TaskSpec template into a concrete TaskSpec template."""
    parameterization = getattr(taskspec.spec, "parameterization", None)
    if parameterization is None:
        return taskspec

    request = SpecParameterizationRequest(
        arguments={str(key): str(value) for key, value in dict(arguments).items()},
        context_root=context_root,
        spec_name=taskspec.name,
        taskspec_payload=copy.deepcopy(taskspec.model_dump(mode="json")),
    )
    payload = dict(
        invoke_parameterization_adapter(
            parameterization.adapter_ref,
            request=request,
            bundle_root=taskspec.get_bundle_root(),
        )
    )
    if payload.get("tid") is not None:
        raise ValueError(
            "spec.parameterization adapter must return a TaskSpec template payload"
        )
    spec_section = payload.get("spec")
    if not isinstance(spec_section, Mapping):
        raise ValueError("spec.parameterization adapter must return a TaskSpec payload")
    if spec_section.get("type") != taskspec.spec.type:
        raise ValueError(
            "spec.parameterization adapter must preserve spec.type for the "
            "materialized TaskSpec"
        )
    payload = json.loads(json.dumps(payload))
    if isinstance(payload.get("spec"), dict):
        payload["spec"].pop("parameterization", None)

    from weft.core.taskspec import TaskSpec

    materialized = TaskSpec.model_validate(
        payload,
        context={"template": True, "auto_expand": False},
    )
    materialized.set_bundle_root(taskspec.get_bundle_root())
    return materialized


__all__ = [
    "SpecParameterizationRequest",
    "invoke_parameterization_adapter",
    "materialize_taskspec_template",
    "parse_declared_parameterization_args",
    "validate_parameterization_adapter",
    "validate_parameterization_adapter_ref",
]
