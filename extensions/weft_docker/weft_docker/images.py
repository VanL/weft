"""Shared Docker image build and cache helpers.

Spec references:
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class DockerBuildFile:
    """One file included in an in-memory Docker build context."""

    path: str
    content: bytes
    mode: int = 0o644


@dataclass(frozen=True, slots=True)
class DockerImageRecipe:
    """Deterministic in-memory Docker image recipe."""

    name: str
    version: str
    dockerfile: str
    files: tuple[DockerBuildFile, ...] = ()
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DockerImageBuildResult:
    """Result of ensuring a Docker image for a deterministic recipe."""

    image: str
    cache_key: str
    action: Literal["reused", "built"]


def compute_recipe_cache_key(recipe: DockerImageRecipe) -> str:
    """Return a stable cache key derived only from image-defining inputs."""

    digest = hashlib.sha256()
    digest.update(recipe.name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(recipe.version.encode("utf-8"))
    digest.update(b"\0")
    digest.update(recipe.dockerfile.encode("utf-8"))
    digest.update(b"\0")
    for file in sorted(recipe.files, key=lambda item: item.path):
        digest.update(file.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(file.mode).encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.content)
        digest.update(b"\0")
    for key, value in sorted(dict(recipe.labels).items()):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def default_image_tag(
    recipe: DockerImageRecipe, *, cache_key: str | None = None
) -> str:
    """Return the default local cache tag for *recipe*."""

    digest = cache_key or compute_recipe_cache_key(recipe)
    return f"weft-agent-{recipe.name}:{digest[:12]}"


def ensure_docker_image(
    client: Any,
    recipe: DockerImageRecipe,
    *,
    tag: str | None = None,
    refresh: bool = False,
) -> DockerImageBuildResult:
    """Build or reuse a deterministic image recipe."""

    cache_key = compute_recipe_cache_key(recipe)
    image_tag = tag or default_image_tag(recipe, cache_key=cache_key)
    if not refresh and _image_exists(client, image_tag):
        return DockerImageBuildResult(
            image=image_tag,
            cache_key=cache_key,
            action="reused",
        )

    context = _build_context_tar(
        dockerfile=recipe.dockerfile,
        files=recipe.files,
    )
    client.images.build(
        fileobj=context,
        custom_context=True,
        tag=image_tag,
        rm=True,
        pull=False,
        forcerm=True,
        labels=dict(recipe.labels),
    )
    return DockerImageBuildResult(
        image=image_tag,
        cache_key=cache_key,
        action="built",
    )


def _image_exists(client: Any, tag: str) -> bool:
    try:
        client.images.get(tag)
    except Exception:
        return False
    return True


def _build_context_tar(
    *,
    dockerfile: str,
    files: Sequence[DockerBuildFile],
) -> io.BytesIO:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        _add_tar_bytes(
            archive,
            path="Dockerfile",
            content=dockerfile.encode("utf-8"),
            mode=0o644,
        )
        for file in files:
            _add_tar_bytes(
                archive,
                path=file.path,
                content=file.content,
                mode=file.mode,
            )
    payload.seek(0)
    return payload


def _add_tar_bytes(
    archive: tarfile.TarFile,
    *,
    path: str,
    content: bytes,
    mode: int,
) -> None:
    info = tarfile.TarInfo(path)
    info.size = len(content)
    info.mode = mode
    archive.addfile(info, io.BytesIO(content))


def build_file_from_path(
    path: Path,
    *,
    target: str,
    mode: int = 0o644,
) -> DockerBuildFile:
    """Convenience helper for recipe files sourced from the local filesystem."""

    return DockerBuildFile(path=target, content=path.read_bytes(), mode=mode)


__all__ = [
    "DockerBuildFile",
    "DockerImageBuildResult",
    "DockerImageRecipe",
    "build_file_from_path",
    "compute_recipe_cache_key",
    "default_image_tag",
    "ensure_docker_image",
]
