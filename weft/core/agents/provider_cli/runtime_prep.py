"""Runtime-prep helpers for delegated provider CLI environments.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

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
    del provider_name, host_home_dir


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


__all__ = [
    "PreparedProviderContainerRuntime",
    "materialize_provider_runtime_home",
    "prepare_provider_container_runtime",
]
