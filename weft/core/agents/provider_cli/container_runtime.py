"""Provider container runtime descriptors for delegated CLI agents.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-0.1], [AR-7]
- docs/specifications/13A-Agent_Runtime_Planned.md [AR-A4]
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ValidationError, field_validator, model_validator

_DESCRIPTOR_VERSION = "v2"
_DESCRIPTOR_PACKAGE = "weft.core.agents.provider_cli"
_DESCRIPTOR_DIRECTORY = "runtime_descriptors"
_DEFAULT_RUNTIME_HOME_ENV = ("HOME", "USERPROFILE")

EnvSourceKind = Literal["host_env"]
MountSourceKind = Literal[
    "home_dir",
    "home_file",
    "project_dir",
    "project_file",
    "task_working_dir",
]
CopySourceKind = Literal["home_file", "project_file"]
DiagnosticKind = Literal[
    "missing_env",
    "missing_mount_source",
    "missing_copy_source",
    "missing_source_context",
]


@dataclass(frozen=True, slots=True)
class ProviderContainerEnvRequirement:
    """One provider runtime environment requirement."""

    name: str
    source_kind: EnvSourceKind
    source_name: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class ProviderContainerMountRequirement:
    """One provider runtime bind-mount requirement."""

    name: str
    source_kind: MountSourceKind
    target: str
    source_path: str | None = None
    required: bool = False
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class ProviderContainerCopiedFileRequirement:
    """One provider runtime file-copy requirement."""

    name: str
    source_kind: CopySourceKind
    source_path: str
    target_path: str
    required: bool = False


@dataclass(frozen=True, slots=True)
class ProviderContainerRuntimeHome:
    """One generated provider runtime home mounted into the container."""

    target: str
    env_names: tuple[str, ...] = _DEFAULT_RUNTIME_HOME_ENV


@dataclass(frozen=True, slots=True)
class ProviderContainerRuntimeDescriptor:
    """Typed internal descriptor for one container-backed provider runtime."""

    version: Literal["v2"]
    provider: str
    env: tuple[ProviderContainerEnvRequirement, ...] = ()
    runtime_mounts: tuple[ProviderContainerMountRequirement, ...] = ()
    runtime_home: ProviderContainerRuntimeHome | None = None
    copied_files: tuple[ProviderContainerCopiedFileRequirement, ...] = ()
    runtime_dirs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedProviderContainerMount:
    """Resolved runtime mount to apply to a container run."""

    name: str
    source: Path
    target: str
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedProviderContainerCopiedFile:
    """Resolved file copy to materialize in a generated runtime home."""

    name: str
    source: Path
    target_path: str


@dataclass(frozen=True, slots=True)
class ProviderContainerRuntimeDiagnostic:
    """Non-fatal resolution diagnostic for a provider runtime requirement."""

    kind: DiagnosticKind
    requirement_name: str
    message: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class ProviderContainerRuntimeResolution:
    """Resolved container runtime defaults and diagnostics for one provider."""

    descriptor: ProviderContainerRuntimeDescriptor
    env: Mapping[str, str] = field(default_factory=dict)
    mounts: tuple[ResolvedProviderContainerMount, ...] = ()
    runtime_home: ProviderContainerRuntimeHome | None = None
    copied_files: tuple[ResolvedProviderContainerCopiedFile, ...] = ()
    runtime_dirs: tuple[str, ...] = ()
    diagnostics: tuple[ProviderContainerRuntimeDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "env",
            MappingProxyType(
                {str(key): str(value) for key, value in dict(self.env).items()}
            ),
        )
        object.__setattr__(self, "mounts", tuple(self.mounts))
        object.__setattr__(self, "copied_files", tuple(self.copied_files))
        object.__setattr__(self, "runtime_dirs", tuple(self.runtime_dirs))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @property
    def has_missing_required(self) -> bool:
        """Whether any required runtime source could not be resolved."""

        return any(item.required for item in self.diagnostics)


class _EnvRequirementModel(BaseModel):
    name: str
    source_kind: EnvSourceKind = "host_env"
    source_name: str | None = None
    required: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must be a non-empty string")
        return cleaned

    @field_validator("source_name")
    @classmethod
    def _validate_source_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("source_name must be a non-empty string when provided")
        return cleaned

    @model_validator(mode="after")
    def _default_source_name(self) -> _EnvRequirementModel:
        if self.source_name is None:
            self.source_name = self.name
        return self


class _MountRequirementModel(BaseModel):
    name: str
    source_kind: MountSourceKind
    target: str
    source_path: str | None = None
    required: bool = False
    read_only: bool = True

    @field_validator("name", "target")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must be a non-empty string")
        return cleaned

    @field_validator("source_path")
    @classmethod
    def _validate_source_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("source_path must be a non-empty string when provided")
        return cleaned

    @model_validator(mode="after")
    def _validate_source_path_requirement(self) -> _MountRequirementModel:
        requires_path = self.source_kind != "task_working_dir"
        if requires_path and self.source_path is None:
            raise ValueError(
                f"source_path is required for source_kind={self.source_kind!r}"
            )
        if not requires_path and self.source_path is not None:
            raise ValueError(
                "source_path is not allowed for source_kind='task_working_dir'"
            )
        return self


class _CopiedFileRequirementModel(BaseModel):
    name: str
    source_kind: CopySourceKind
    source_path: str
    target_path: str
    required: bool = False

    @field_validator("name", "source_path")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must be a non-empty string")
        return cleaned

    @field_validator("target_path")
    @classmethod
    def _validate_target_path(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("target_path must be a non-empty string")
        if cleaned.startswith("/"):
            raise ValueError("target_path must be a relative path")
        return cleaned


class _RuntimeHomeModel(BaseModel):
    target: str
    env_names: tuple[str, ...] = _DEFAULT_RUNTIME_HOME_ENV

    @field_validator("target")
    @classmethod
    def _validate_target(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("target must be a non-empty string")
        return cleaned

    @field_validator("env_names")
    @classmethod
    def _validate_env_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("env_names must not be empty")
        cleaned: list[str] = []
        for item in value:
            normalized = item.strip()
            if not normalized:
                raise ValueError("env_names entries must be non-empty strings")
            if normalized not in cleaned:
                cleaned.append(normalized)
        return tuple(cleaned)


class _DescriptorModel(BaseModel):
    version: Literal["v2"]
    provider: str
    env: tuple[_EnvRequirementModel, ...] = ()
    runtime_mounts: tuple[_MountRequirementModel, ...] = ()
    runtime_home: _RuntimeHomeModel | None = None
    copied_files: tuple[_CopiedFileRequirementModel, ...] = ()
    runtime_dirs: tuple[str, ...] = ()

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("provider must be a non-empty string")
        return cleaned

    @field_validator("runtime_dirs")
    @classmethod
    def _validate_runtime_dirs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for item in value:
            normalized = item.strip()
            if not normalized:
                raise ValueError("runtime_dirs entries must be non-empty strings")
            if normalized.startswith("/"):
                raise ValueError("runtime_dirs entries must be relative paths")
            cleaned.append(normalized)
        return tuple(cleaned)

    @model_validator(mode="after")
    def _validate_runtime_home_requirements(self) -> _DescriptorModel:
        if (self.copied_files or self.runtime_dirs) and self.runtime_home is None:
            raise ValueError(
                "runtime_home is required when copied_files or runtime_dirs are present"
            )
        return self


def parse_provider_container_runtime_descriptor(
    payload: Mapping[str, Any],
    *,
    source: str,
) -> ProviderContainerRuntimeDescriptor:
    """Parse one descriptor payload into the typed internal model."""

    try:
        model = _DescriptorModel.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid provider container runtime descriptor {source}: {exc}"
        ) from exc
    return ProviderContainerRuntimeDescriptor(
        version=model.version,
        provider=model.provider,
        env=tuple(
            ProviderContainerEnvRequirement(
                name=item.name,
                source_kind=item.source_kind,
                source_name=item.source_name or item.name,
                required=item.required,
            )
            for item in model.env
        ),
        runtime_mounts=tuple(
            ProviderContainerMountRequirement(
                name=item.name,
                source_kind=item.source_kind,
                target=item.target,
                source_path=item.source_path,
                required=item.required,
                read_only=item.read_only,
            )
            for item in model.runtime_mounts
        ),
        runtime_home=(
            ProviderContainerRuntimeHome(
                target=model.runtime_home.target,
                env_names=tuple(model.runtime_home.env_names),
            )
            if model.runtime_home is not None
            else None
        ),
        copied_files=tuple(
            ProviderContainerCopiedFileRequirement(
                name=item.name,
                source_kind=item.source_kind,
                source_path=item.source_path,
                target_path=item.target_path,
                required=item.required,
            )
            for item in model.copied_files
        ),
        runtime_dirs=tuple(model.runtime_dirs),
    )


@cache
def get_provider_container_runtime_descriptor(
    provider_name: str,
) -> ProviderContainerRuntimeDescriptor | None:
    """Return the packaged runtime descriptor for *provider_name*, if present."""

    normalized = provider_name.strip()
    if not normalized:
        raise ValueError("provider_name must be a non-empty string")
    resource = (
        resources.files(_DESCRIPTOR_PACKAGE)
        .joinpath(_DESCRIPTOR_DIRECTORY)
        .joinpath(f"{normalized}.json")
    )
    if not resource.is_file():
        return None
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid provider container runtime descriptor {resource}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"Invalid provider container runtime descriptor {resource}: root must be an object"
        )
    descriptor = parse_provider_container_runtime_descriptor(
        payload,
        source=str(resource),
    )
    if descriptor.provider != normalized:
        raise ValueError(
            "Provider container runtime descriptor provider mismatch: "
            f"expected {normalized!r}, found {descriptor.provider!r}"
        )
    return descriptor


def require_provider_container_runtime_descriptor(
    provider_name: str,
) -> ProviderContainerRuntimeDescriptor:
    """Return the descriptor for *provider_name* or raise a clear error."""

    descriptor = get_provider_container_runtime_descriptor(provider_name)
    if descriptor is None:
        raise ValueError(
            "No Docker-backed provider container runtime descriptor is available "
            f"for provider '{provider_name}'"
        )
    return descriptor


def resolve_provider_container_runtime(
    provider_name: str,
    *,
    task_env: Mapping[str, str] | None,
    working_dir: str | None,
    explicit_mounts: Sequence[Mapping[str, Any]] | None = None,
    host_env: Mapping[str, str] | None = None,
    home_dir: str | Path | None = None,
    project_root: str | Path | None = None,
) -> ProviderContainerRuntimeResolution:
    """Resolve env forwarding and runtime inputs for one provider runtime."""

    descriptor = require_provider_container_runtime_descriptor(provider_name)
    task_env_map = {str(key): str(value) for key, value in dict(task_env or {}).items()}
    host_env_map = dict(os.environ if host_env is None else host_env)
    explicit_targets = _explicit_mount_targets(explicit_mounts or ())
    resolved_env: dict[str, str] = {}
    resolved_mounts: list[ResolvedProviderContainerMount] = []
    resolved_copied_files: list[ResolvedProviderContainerCopiedFile] = []
    diagnostics: list[ProviderContainerRuntimeDiagnostic] = []

    for env_requirement in descriptor.env:
        if env_requirement.name in task_env_map:
            continue
        env_value = host_env_map.get(env_requirement.source_name)
        if isinstance(env_value, str) and env_value:
            resolved_env[env_requirement.name] = env_value
            continue
        if env_requirement.required:
            diagnostics.append(
                ProviderContainerRuntimeDiagnostic(
                    kind="missing_env",
                    requirement_name=env_requirement.name,
                    required=True,
                    message=(
                        "Missing required host environment variable "
                        f"{env_requirement.source_name!r} for provider "
                        f"'{provider_name}'"
                    ),
                )
            )

    runtime_home = descriptor.runtime_home
    if runtime_home is not None and runtime_home.target in explicit_targets:
        runtime_home = None

    for mount_requirement in descriptor.runtime_mounts:
        if mount_requirement.target in explicit_targets:
            continue
        resolved_source, mount_diagnostics = _resolve_mount_source(
            provider_name,
            mount_requirement,
            working_dir=working_dir,
            home_dir=home_dir,
            project_root=project_root,
        )
        diagnostics.extend(mount_diagnostics)
        if resolved_source is None:
            continue
        resolved_mounts.append(
            ResolvedProviderContainerMount(
                name=mount_requirement.name,
                source=resolved_source,
                target=mount_requirement.target,
                read_only=mount_requirement.read_only,
            )
        )

    if runtime_home is not None:
        for copied_file in descriptor.copied_files:
            resolved_source, copy_diagnostics = _resolve_copy_source(
                provider_name,
                copied_file,
                home_dir=home_dir,
                project_root=project_root,
            )
            diagnostics.extend(copy_diagnostics)
            if resolved_source is None:
                continue
            resolved_copied_files.append(
                ResolvedProviderContainerCopiedFile(
                    name=copied_file.name,
                    source=resolved_source,
                    target_path=copied_file.target_path,
                )
            )

    return ProviderContainerRuntimeResolution(
        descriptor=descriptor,
        env=resolved_env,
        mounts=tuple(resolved_mounts),
        runtime_home=runtime_home,
        copied_files=tuple(resolved_copied_files),
        runtime_dirs=tuple(descriptor.runtime_dirs if runtime_home is not None else ()),
        diagnostics=tuple(diagnostics),
    )


def format_provider_container_runtime_diagnostics(
    diagnostics: Sequence[ProviderContainerRuntimeDiagnostic],
) -> str:
    """Format one human-readable diagnostic string."""

    if not diagnostics:
        return ""
    return "; ".join(item.message for item in diagnostics)


def _resolve_mount_source(
    provider_name: str,
    requirement: ProviderContainerMountRequirement,
    *,
    working_dir: str | None,
    home_dir: str | Path | None,
    project_root: str | Path | None,
) -> tuple[Path | None, tuple[ProviderContainerRuntimeDiagnostic, ...]]:
    base_path, diagnostics = _resolve_base_path(
        provider_name,
        requirement_name=requirement.name,
        source_kind=requirement.source_kind,
        required=requirement.required,
        working_dir=working_dir,
        home_dir=home_dir,
        project_root=project_root,
    )
    if base_path is None:
        return None, diagnostics
    candidate = (
        base_path
        if requirement.source_path is None
        else (base_path / requirement.source_path).expanduser()
    )
    if (
        requirement.source_kind.endswith("_dir")
        or requirement.source_kind == "task_working_dir"
    ):
        if candidate.is_dir():
            return candidate.resolve(), ()
        expected = "directory"
    else:
        if candidate.is_file():
            return candidate.resolve(), ()
        expected = "file"
    if requirement.required:
        return None, (
            ProviderContainerRuntimeDiagnostic(
                kind="missing_mount_source",
                requirement_name=requirement.name,
                required=True,
                message=(
                    f"Missing required {expected} for provider '{provider_name}': "
                    f"{candidate}"
                ),
            ),
        )
    return None, ()


def _resolve_copy_source(
    provider_name: str,
    requirement: ProviderContainerCopiedFileRequirement,
    *,
    home_dir: str | Path | None,
    project_root: str | Path | None,
) -> tuple[Path | None, tuple[ProviderContainerRuntimeDiagnostic, ...]]:
    base_path, diagnostics = _resolve_base_path(
        provider_name,
        requirement_name=requirement.name,
        source_kind=requirement.source_kind,
        required=requirement.required,
        working_dir=None,
        home_dir=home_dir,
        project_root=project_root,
    )
    if base_path is None:
        return None, diagnostics
    candidate = (base_path / requirement.source_path).expanduser()
    if candidate.is_file():
        return candidate.resolve(), ()
    if requirement.required:
        return None, (
            ProviderContainerRuntimeDiagnostic(
                kind="missing_copy_source",
                requirement_name=requirement.name,
                required=True,
                message=(
                    f"Missing required file copy source for provider "
                    f"'{provider_name}': {candidate}"
                ),
            ),
        )
    return None, ()


def _resolve_base_path(
    provider_name: str,
    *,
    requirement_name: str,
    source_kind: str,
    required: bool,
    working_dir: str | None,
    home_dir: str | Path | None,
    project_root: str | Path | None,
) -> tuple[Path | None, tuple[ProviderContainerRuntimeDiagnostic, ...]]:
    if source_kind == "task_working_dir":
        if working_dir is None:
            return None, _missing_source_context(
                provider_name,
                requirement_name=requirement_name,
                required=required,
                message="Task working_dir is not set",
            )
        return Path(working_dir).expanduser(), ()
    if source_kind.startswith("home_"):
        return Path(home_dir).expanduser() if home_dir is not None else Path.home(), ()
    if source_kind.startswith("project_"):
        if project_root is None:
            return None, _missing_source_context(
                provider_name,
                requirement_name=requirement_name,
                required=required,
                message="Project root is unavailable for resolution",
            )
        return Path(project_root).expanduser(), ()
    return None, _missing_source_context(
        provider_name,
        requirement_name=requirement_name,
        required=required,
        message=f"Unsupported source_kind={source_kind!r}",
    )


def _missing_source_context(
    provider_name: str,
    *,
    requirement_name: str,
    required: bool,
    message: str,
) -> tuple[ProviderContainerRuntimeDiagnostic, ...]:
    if not required:
        return ()
    return (
        ProviderContainerRuntimeDiagnostic(
            kind="missing_source_context",
            requirement_name=requirement_name,
            required=True,
            message=f"{message} for provider '{provider_name}'",
        ),
    )


def _explicit_mount_targets(explicit_mounts: Sequence[Mapping[str, Any]]) -> set[str]:
    targets: set[str] = set()
    for mount in explicit_mounts:
        target = mount.get("target")
        if isinstance(target, str) and target.strip():
            targets.add(target.strip())
    return targets


__all__ = [
    "ProviderContainerCopiedFileRequirement",
    "ProviderContainerEnvRequirement",
    "ProviderContainerMountRequirement",
    "ProviderContainerRuntimeDescriptor",
    "ProviderContainerRuntimeDiagnostic",
    "ProviderContainerRuntimeHome",
    "ProviderContainerRuntimeResolution",
    "ResolvedProviderContainerCopiedFile",
    "ResolvedProviderContainerMount",
    "format_provider_container_runtime_diagnostics",
    "get_provider_container_runtime_descriptor",
    "parse_provider_container_runtime_descriptor",
    "require_provider_container_runtime_descriptor",
    "resolve_provider_container_runtime",
]
