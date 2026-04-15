"""Runtime-prep helpers for delegated provider CLI environments.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .container_runtime import (
    ProviderContainerRuntimeResolution,
    ResolvedProviderContainerMount,
)


@dataclass(frozen=True, slots=True)
class PreparedProviderContainerRuntime:
    """Generated runtime assets to add to a container run."""

    env: Mapping[str, str] = field(default_factory=dict)
    mounts: tuple[ResolvedProviderContainerMount, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "env",
            MappingProxyType(
                {str(key): str(value) for key, value in dict(self.env).items()}
            ),
        )
        object.__setattr__(self, "mounts", tuple(self.mounts))


def materialize_provider_runtime_home(
    provider_name: str,
    resolution: ProviderContainerRuntimeResolution,
    *,
    runtime_home_root: Path,
    host_home_dir: str | Path | None = None,
) -> None:
    """Materialize one generated provider runtime home on the local filesystem."""
    runtime_home_root.mkdir(parents=True, exist_ok=True)
    for runtime_dir in resolution.runtime_dirs:
        (runtime_home_root / runtime_dir).mkdir(parents=True, exist_ok=True)
    for copied_file in resolution.copied_files:
        target_path = runtime_home_root / copied_file.target_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(copied_file.source, target_path)
    _apply_provider_runtime_home_overrides(
        provider_name,
        runtime_home_root=runtime_home_root,
        host_home_dir=host_home_dir,
    )


def prepare_provider_container_runtime(
    provider_name: str,
    resolution: ProviderContainerRuntimeResolution,
    *,
    temp_root: Path,
    host_home_dir: str | Path | None = None,
) -> PreparedProviderContainerRuntime:
    """Prepare generated runtime assets for one Docker-backed provider run."""

    runtime_home = resolution.runtime_home
    if runtime_home is None:
        return PreparedProviderContainerRuntime()
    runtime_home_root = temp_root / "provider-runtime-home"
    materialize_provider_runtime_home(
        provider_name,
        resolution,
        runtime_home_root=runtime_home_root,
        host_home_dir=host_home_dir,
    )
    return PreparedProviderContainerRuntime(
        env=dict.fromkeys(runtime_home.env_names, runtime_home.target),
        mounts=(
            ResolvedProviderContainerMount(
                name=f"{provider_name}_runtime_home",
                source=runtime_home_root.resolve(),
                target=runtime_home.target,
                read_only=False,
            ),
        ),
    )


def build_gemini_session_environment(
    tempdir: Path,
    *,
    host_home_dir: str | Path | None = None,
) -> dict[str, str]:
    """Build an isolated Gemini home directory with copied auth state only."""

    runtime_home_root = tempdir / "gemini-home"
    resolution = _gemini_runtime_resolution(host_home_dir=host_home_dir)
    materialize_provider_runtime_home(
        "gemini",
        resolution,
        runtime_home_root=runtime_home_root,
        host_home_dir=host_home_dir,
    )
    runtime_home = resolution.runtime_home
    if runtime_home is None:  # pragma: no cover - descriptor contract guard
        return {}
    return {name: str(runtime_home_root) for name in runtime_home.env_names}


def _apply_provider_runtime_home_overrides(
    provider_name: str,
    *,
    runtime_home_root: Path,
    host_home_dir: str | Path | None,
) -> None:
    if provider_name != "gemini":
        return
    gemini_home = runtime_home_root / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    settings: dict[str, object] = {
        "admin": {
            "mcp": {"enabled": False},
            "extensions": {"enabled": False},
            "skills": {"enabled": False},
        },
        "hooks": {},
        "ui": {
            "hideBanner": True,
            "showHomeDirectoryWarning": False,
        },
    }
    selected_auth_type = _resolve_gemini_selected_auth_type(host_home_dir=host_home_dir)
    if selected_auth_type is not None:
        settings["security"] = {"auth": {"selectedType": selected_auth_type}}

    (gemini_home / "settings.json").write_text(
        f"{json.dumps(settings, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )
    (gemini_home / "projects.json").write_text(
        f"{json.dumps({'projects': {}}, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )
    (gemini_home / "trustedFolders.json").write_text(
        f"{json.dumps({}, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )


def _resolve_gemini_selected_auth_type(
    *,
    host_home_dir: str | Path | None,
) -> str | None:
    home_dir = (
        Path(host_home_dir).expanduser() if host_home_dir is not None else Path.home()
    )
    settings_path = home_dir / ".gemini" / "settings.json"
    if not settings_path.exists():
        return None
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    security = payload.get("security")
    if not isinstance(security, Mapping):
        return None
    auth = security.get("auth")
    if not isinstance(auth, Mapping):
        return None
    selected_type = auth.get("selectedType")
    if not isinstance(selected_type, str) or not selected_type.strip():
        return None
    return selected_type.strip()


def _gemini_runtime_resolution(
    *,
    host_home_dir: str | Path | None,
) -> ProviderContainerRuntimeResolution:
    from .container_runtime import resolve_provider_container_runtime

    return resolve_provider_container_runtime(
        "gemini",
        task_env={},
        working_dir=None,
        host_env={},
        home_dir=host_home_dir,
    )


__all__ = [
    "PreparedProviderContainerRuntime",
    "build_gemini_session_environment",
    "materialize_provider_runtime_home",
    "prepare_provider_container_runtime",
]
