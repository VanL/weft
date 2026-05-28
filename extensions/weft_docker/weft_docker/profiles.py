"""Docker container profile loading and materialization.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.1]
- docs/specifications/02-TaskSpec.md [TS-1.3]
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from weft._constants import (
    DOCKER_CONTAINER_PROFILE_CONTROL_KEYS as PROFILE_CONTROL_KEYS,
)
from weft._constants import (
    DOCKER_CONTAINER_PROFILE_DEFAULT_FILE as DEFAULT_PROFILE_FILE,
)
from weft._constants import (
    DOCKER_CONTAINER_PROFILE_DOCKER_OPTION_KEYS as PROFILE_DOCKER_OPTION_KEYS,
)
from weft._constants import (
    DOCKER_CONTAINER_PROFILE_SUPPORTED_KEYS as SUPPORTED_PROFILE_KEYS,
)


@dataclass(frozen=True, slots=True)
class MaterializedContainerProfile:
    """Resolved Docker runner options and env after applying a profile."""

    runner_options: Mapping[str, Any] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    profile_name: str | None = None
    profile_file: Path | None = None
    profile_root: Path | None = None
    profile_sourced_mounts: tuple[Mapping[str, Any], ...] = ()
    profile_sourced_build: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
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
        object.__setattr__(
            self,
            "profile_sourced_mounts",
            tuple(
                MappingProxyType(dict(mount)) for mount in self.profile_sourced_mounts
            ),
        )
        build = self.profile_sourced_build
        if build is not None:
            object.__setattr__(
                self, "profile_sourced_build", MappingProxyType(dict(build))
            )


def materialize_container_profile(
    *,
    runner_options: Mapping[str, Any] | None,
    env: Mapping[str, str] | None,
    bundle_root: str | None,
    preflight: bool = False,
    host_env: Mapping[str, str] | None = None,
) -> MaterializedContainerProfile:
    """Apply an optional Docker container profile to runner options and env.

    The profile is a `weft_docker` runner feature. The returned values are
    copies; callers must pass them forward without mutating the TaskSpec.
    """

    options = dict(runner_options or {})
    explicit_env = _mapping_of_strings(env or {}, name="spec.env")
    profile_name_value = options.get("container_profile")
    if profile_name_value is None:
        return MaterializedContainerProfile(
            runner_options=_strip_control_keys(options),
            env=explicit_env,
        )

    profile_name = _required_text(
        profile_name_value,
        name="spec.runner.options.container_profile",
    )
    profile_file = _resolve_profile_file(
        options.get("container_profile_file"),
        bundle_root=bundle_root,
    )
    payload = _load_profile_file(profile_file)
    _validate_profile_version(payload, profile_file=profile_file)
    profile_root = _resolve_profile_root(
        payload.get("root"),
        explicit_root=options.get("container_profile_root"),
        profile_file=profile_file,
        bundle_root=bundle_root,
    )
    profile = _load_profile_table(payload, profile_name=profile_name)
    unsupported = sorted(set(profile) - SUPPORTED_PROFILE_KEYS)
    if unsupported:
        joined = ", ".join(unsupported)
        raise ValueError(
            f"unsupported Docker profile keys in profiles.{profile_name}: {joined}"
        )

    profile_env = _mapping_of_strings(
        profile.get("env") or {},
        name=f"profiles.{profile_name}.env",
    )
    env_from_host = _string_list(
        profile.get("env_from_host"),
        name=f"profiles.{profile_name}.env_from_host",
    )
    required_env_from_host = _string_list(
        profile.get("required_env_from_host"),
        name=f"profiles.{profile_name}.required_env_from_host",
    )
    source_env = os.environ if host_env is None else host_env
    forwarded_env = _forward_host_env(
        source_env,
        keys=[*env_from_host, *required_env_from_host],
    )
    merged_env = dict(profile_env)
    merged_env.update(forwarded_env)
    merged_env.update(explicit_env)
    missing_required = [key for key in required_env_from_host if key not in merged_env]
    if missing_required:
        joined = ", ".join(missing_required)
        raise ValueError(
            f"Docker profile '{profile_name}' requires env values that are "
            f"missing from host env and TaskSpec env: {joined}"
        )

    profile_options, profile_sourced_mounts, profile_sourced_build = (
        _profile_runner_options(profile, profile_name=profile_name, root=profile_root)
    )
    explicit_options = _strip_control_keys(options)
    if "image" in explicit_options:
        profile_options.pop("build", None)
        profile_sourced_build = None
    if "build" in explicit_options:
        profile_options.pop("image", None)
        profile_sourced_build = None
    if "mounts" in explicit_options:
        profile_sourced_mounts = []

    merged_options = dict(profile_options)
    merged_options.update(explicit_options)

    if preflight:
        _validate_profile_mount_sources(profile_sourced_mounts)
        if profile_sourced_build is not None:
            _validate_profile_build_paths(profile_sourced_build)

    return MaterializedContainerProfile(
        runner_options=merged_options,
        env=merged_env,
        profile_name=profile_name,
        profile_file=profile_file,
        profile_root=profile_root,
        profile_sourced_mounts=tuple(profile_sourced_mounts),
        profile_sourced_build=profile_sourced_build,
    )


def _strip_control_keys(options: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in options.items() if key not in PROFILE_CONTROL_KEYS
    }


def _resolve_profile_file(value: object, *, bundle_root: str | None) -> Path:
    raw_path = (
        DEFAULT_PROFILE_FILE
        if value is None
        else _required_text(
            value,
            name="spec.runner.options.container_profile_file",
        )
    )
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = _relative_base(bundle_root) / path
    resolved = path.resolve()
    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"Docker profile file does not exist: {resolved}")
    return resolved


def _load_profile_file(profile_file: Path) -> Mapping[str, Any]:
    try:
        with profile_file.open("rb") as handle:
            payload = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"Docker profile file is not valid TOML: {profile_file}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"Docker profile file must contain a TOML table: {profile_file}"
        )
    return payload


def _validate_profile_version(
    payload: Mapping[str, Any],
    *,
    profile_file: Path,
) -> None:
    version = payload.get("version")
    if version is not None and (type(version) is not int or version != 1):
        raise ValueError(f"Docker profile file version must be 1: {profile_file}")


def _resolve_profile_root(
    root_value: object,
    *,
    explicit_root: object,
    profile_file: Path,
    bundle_root: str | None,
) -> Path:
    if explicit_root is not None:
        root = _required_text(
            explicit_root,
            name="spec.runner.options.container_profile_root",
        )
        return _resolve_path(root, base=_relative_base(bundle_root))
    if root_value is not None:
        root = _required_text(root_value, name="root")
        return _resolve_path(root, base=profile_file.parent)
    return profile_file.parent.resolve()


def _load_profile_table(
    payload: Mapping[str, Any],
    *,
    profile_name: str,
) -> Mapping[str, Any]:
    profiles = _require_mapping(payload.get("profiles"), name="profiles")
    if profile_name not in profiles:
        raise ValueError(f"Docker container profile '{profile_name}' was not found")
    return _require_mapping(
        profiles.get(profile_name),
        name=f"profiles.{profile_name}",
    )


def _profile_runner_options(
    profile: Mapping[str, Any],
    *,
    profile_name: str,
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    options: dict[str, Any] = {}
    for key in PROFILE_DOCKER_OPTION_KEYS:
        if key not in profile:
            continue
        value = profile[key]
        name = f"profiles.{profile_name}.{key}"
        if key == "build":
            options[key] = _resolve_profile_build(value, name=name, root=root)
        elif key == "mounts":
            options[key] = _resolve_profile_mounts(value, name=name, root=root)
        elif key == "docker_args":
            options[key] = _string_list(value, name=name)
        elif key == "mount_workdir":
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
            options[key] = value
        else:
            options[key] = _required_text(value, name=name)

    profile_sourced_mounts = list(options.get("mounts") or [])
    profile_sourced_build = options.get("build")
    return (
        options,
        profile_sourced_mounts,
        dict(profile_sourced_build)
        if isinstance(profile_sourced_build, Mapping)
        else None,
    )


def _resolve_profile_build(
    value: object,
    *,
    name: str,
    root: Path,
) -> dict[str, Any]:
    build = dict(_require_mapping(value, name=name))
    context = _required_text(build.get("context"), name=f"{name}.context")
    build["context"] = str(_resolve_path(context, base=root))
    dockerfile = build.get("dockerfile")
    if dockerfile is not None:
        build["dockerfile"] = str(
            _resolve_path(
                _required_text(dockerfile, name=f"{name}.dockerfile"),
                base=root,
            )
        )
    return build


def _resolve_profile_mounts(
    value: object,
    *,
    name: str,
    root: Path,
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a list of mount objects")
    mounts: list[dict[str, Any]] = []
    for index, raw_mount in enumerate(value):
        mount_name = f"{name}[{index}]"
        mount = dict(_require_mapping(raw_mount, name=mount_name))
        source = _required_text(mount.get("source"), name=f"{mount_name}.source")
        target = _required_text(mount.get("target"), name=f"{mount_name}.target")
        read_only = mount.get("read_only", True)
        if not isinstance(read_only, bool):
            raise ValueError(f"{mount_name}.read_only must be a boolean")
        mounts.append(
            {
                "source": str(_resolve_path(source, base=root)),
                "target": target,
                "read_only": read_only,
            }
        )
    return mounts


def _forward_host_env(
    host_env: Mapping[str, str],
    *,
    keys: Sequence[str],
) -> dict[str, str]:
    forwarded: dict[str, str] = {}
    for key in keys:
        value = host_env.get(key)
        if value is not None:
            forwarded[key] = str(value)
    return forwarded


def _validate_profile_mount_sources(mounts: Sequence[Mapping[str, Any]]) -> None:
    for mount in mounts:
        source = Path(str(mount["source"])).expanduser()
        if not source.exists():
            raise ValueError(f"Docker profile mount source does not exist: {source}")


def _validate_profile_build_paths(build: Mapping[str, Any]) -> None:
    context = Path(str(build["context"])).expanduser()
    if not context.exists() or not context.is_dir():
        raise ValueError(f"Docker profile build context does not exist: {context}")
    dockerfile = build.get("dockerfile")
    if dockerfile is not None:
        dockerfile_path = Path(str(dockerfile)).expanduser()
        if not dockerfile_path.exists() or not dockerfile_path.is_file():
            raise ValueError(
                f"Docker profile build Dockerfile does not exist: {dockerfile_path}"
            )


def _resolve_path(value: str, *, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _relative_base(bundle_root: str | None) -> Path:
    if bundle_root is None:
        return Path.cwd().resolve()
    return Path(bundle_root).expanduser().resolve()


def _mapping_of_strings(value: object, *, name: str) -> dict[str, str]:
    mapping = _require_mapping(value, name=name)
    normalized: dict[str, str] = {}
    for key, item in mapping.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError(f"{name} must be a mapping of strings to strings")
        normalized[key] = item
    return normalized


def _string_list(value: object, *, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a list of strings")
    strings: list[str] = []
    for index, item in enumerate(value):
        strings.append(_required_text(item, name=f"{name}[{index}]"))
    return strings


def _required_text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must be a non-empty string")
    return cleaned


def _require_mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


__all__ = [
    "DEFAULT_PROFILE_FILE",
    "MaterializedContainerProfile",
    "PROFILE_CONTROL_KEYS",
    "materialize_container_profile",
]
