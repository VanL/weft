"""Runner environment-profile loading and materialization helpers.

Spec references:
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/13A-Agent_Runtime_Planned.md [AR-A4]
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast

from weft.core.imports import import_callable_ref
from weft.core.taskspec import bundle_root_from_taskspec_payload
from weft.ext import RunnerEnvironmentProfile, RunnerEnvironmentProfileResult


@dataclass(frozen=True, slots=True)
class MaterializedRunnerEnvironment:
    """Resolved runner inputs after applying an optional environment profile."""

    runner_name: str
    runner_options: Mapping[str, Any] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    working_dir: str | None = None
    environment_profile_ref: str | None = None
    profile_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        working_dir = self.working_dir
        if working_dir is not None:
            working_dir = working_dir.strip()
            if not working_dir:
                working_dir = None
        object.__setattr__(self, "working_dir", working_dir)
        object.__setattr__(
            self, "runner_options", MappingProxyType(dict(self.runner_options))
        )
        object.__setattr__(
            self,
            "env",
            MappingProxyType(
                {str(key): str(value) for key, value in dict(self.env).items()}
            ),
        )
        profile_ref = self.environment_profile_ref
        if profile_ref is not None:
            profile_ref = profile_ref.strip()
            if not profile_ref:
                profile_ref = None
        object.__setattr__(self, "environment_profile_ref", profile_ref)
        object.__setattr__(
            self, "profile_metadata", MappingProxyType(dict(self.profile_metadata))
        )


def load_runner_environment_profile(
    environment_profile_ref: str | None,
    *,
    bundle_root: str | None = None,
) -> RunnerEnvironmentProfile | None:
    """Resolve the configured runner environment-profile callable if present."""
    if environment_profile_ref is None:
        return None
    return cast(
        RunnerEnvironmentProfile,
        import_callable_ref(environment_profile_ref, bundle_root=bundle_root),
    )


def materialize_runner_environment(
    *,
    target_type: str,
    runner_name: str,
    runner_options: Mapping[str, Any] | None,
    env: Mapping[str, str] | None,
    working_dir: str | None,
    environment_profile_ref: str | None,
    bundle_root: str | None = None,
    tid: str | None,
) -> MaterializedRunnerEnvironment:
    """Apply a runner environment profile and merge explicit TaskSpec inputs."""
    normalized_options = dict(runner_options or {})
    normalized_env = {str(key): str(value) for key, value in dict(env or {}).items()}
    profile = load_runner_environment_profile(
        environment_profile_ref,
        bundle_root=bundle_root,
    )
    if profile is None:
        return MaterializedRunnerEnvironment(
            runner_name=runner_name,
            runner_options=normalized_options,
            env=normalized_env,
            working_dir=working_dir,
            environment_profile_ref=environment_profile_ref,
        )

    result = profile(
        target_type=target_type,
        runner_name=runner_name,
        runner_options=normalized_options,
        env=normalized_env,
        working_dir=working_dir,
        tid=tid,
    )
    if not isinstance(result, RunnerEnvironmentProfileResult):
        raise TypeError(
            "Runner environment profile must return RunnerEnvironmentProfileResult"
        )

    merged_options = dict(result.runner_options)
    merged_options.update(normalized_options)

    merged_env = dict(result.env)
    merged_env.update(normalized_env)

    merged_working_dir = working_dir if working_dir is not None else result.working_dir
    return MaterializedRunnerEnvironment(
        runner_name=runner_name,
        runner_options=merged_options,
        env=merged_env,
        working_dir=merged_working_dir,
        environment_profile_ref=environment_profile_ref,
        profile_metadata=result.metadata,
    )


def materialize_runner_environment_from_taskspec(
    taskspec_payload: Mapping[str, Any],
) -> MaterializedRunnerEnvironment:
    """Resolve environment-profile defaults for a TaskSpec mapping payload."""
    spec = _require_mapping(taskspec_payload.get("spec"), name="spec")
    target_type = _require_text(spec.get("type"), name="spec.type")
    runner_mapping = spec.get("runner") or {}
    runner = _require_mapping(runner_mapping, name="spec.runner")
    runner_name = _require_text(runner.get("name", "host"), name="spec.runner.name")
    runner_options = _require_mapping(
        runner.get("options") or {}, name="spec.runner.options"
    )
    environment_profile_ref = runner.get("environment_profile_ref")
    if environment_profile_ref is not None:
        environment_profile_ref = _require_text(
            environment_profile_ref,
            name="spec.runner.environment_profile_ref",
        )
    working_dir = spec.get("working_dir")
    if working_dir is not None and not isinstance(working_dir, str):
        raise ValueError("spec.working_dir must be a string")

    env = _mapping_of_strings(spec.get("env") or {}, name="spec.env")
    tid = taskspec_payload.get("tid")
    tid_value = tid if isinstance(tid, str) else None
    bundle_root = bundle_root_from_taskspec_payload(taskspec_payload)
    return materialize_runner_environment(
        target_type=target_type,
        runner_name=runner_name,
        runner_options=runner_options,
        env=env,
        working_dir=working_dir,
        environment_profile_ref=environment_profile_ref,
        bundle_root=bundle_root,
        tid=tid_value,
    )


def _mapping_of_strings(value: object, *, name: str) -> dict[str, str]:
    mapping = _require_mapping(value, name=name)
    normalized: dict[str, str] = {}
    for key, item in mapping.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError(f"{name} must be a mapping of strings to strings")
        normalized[key] = item
    return normalized


def _require_mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must be a non-empty string")
    return cleaned


__all__ = [
    "MaterializedRunnerEnvironment",
    "load_runner_environment_profile",
    "materialize_runner_environment",
    "materialize_runner_environment_from_taskspec",
]
