"""Tests for Docker-backed agent image helpers."""

from __future__ import annotations

import io
import tarfile
from typing import cast

import pytest
from weft_docker.agent_images import get_agent_image_recipe
from weft_docker.images import (
    DockerBuildFile,
    DockerImageRecipe,
    default_image_tag,
    ensure_docker_image,
)

pytestmark = [pytest.mark.shared]


def test_get_agent_image_recipe_support_is_explicit() -> None:
    recipes = {
        name: get_agent_image_recipe(name)
        for name in ("claude_code", "codex", "gemini", "opencode", "qwen")
    }

    assert all(recipe is not None for recipe in recipes.values())
    assert recipes["claude_code"].default_executable == "claude"  # type: ignore[union-attr]
    assert recipes["codex"].default_executable == "codex"  # type: ignore[union-attr]
    assert recipes["gemini"].default_executable == "gemini"  # type: ignore[union-attr]


def test_ensure_docker_image_reuses_existing_tag_without_rebuilding() -> None:
    recipe = DockerImageRecipe(
        name="codex",
        version="v1",
        dockerfile="FROM busybox\n",
    )
    build_calls: list[dict[str, object]] = []

    class FakeImages:
        def get(self, tag: str) -> object:
            assert tag == default_image_tag(recipe)
            return object()

        def build(self, **kwargs: object) -> None:
            build_calls.append(kwargs)

    client = type("FakeClient", (), {"images": FakeImages()})()

    result = ensure_docker_image(client, recipe)

    assert result.action == "reused"
    assert result.image == default_image_tag(recipe)
    assert build_calls == []


def test_ensure_docker_image_builds_in_memory_context() -> None:
    recipe = DockerImageRecipe(
        name="codex",
        version="v1",
        dockerfile="FROM busybox\nCOPY hello.txt /hello.txt\n",
        files=(DockerBuildFile(path="hello.txt", content=b"hello\n"),),
        labels={"weft.agent.provider": "codex"},
    )
    build_calls: list[dict[str, object]] = []

    class FakeImages:
        def get(self, tag: str) -> object:
            raise RuntimeError(f"missing image: {tag}")

        def build(self, **kwargs: object) -> None:
            build_calls.append(kwargs)

    client = type("FakeClient", (), {"images": FakeImages()})()

    result = ensure_docker_image(client, recipe)

    assert result.action == "built"
    assert result.image == default_image_tag(recipe)
    assert len(build_calls) == 1
    call = build_calls[0]
    assert call["custom_context"] is True
    assert call["tag"] == default_image_tag(recipe)
    assert call["labels"] == {"weft.agent.provider": "codex"}

    context_file = cast(io.BytesIO, call["fileobj"])
    context_file.seek(0)
    with tarfile.open(fileobj=context_file, mode="r:") as archive:
        names = archive.getnames()
        assert names == ["Dockerfile", "hello.txt"]
        dockerfile_member = archive.extractfile("Dockerfile")
        hello_member = archive.extractfile("hello.txt")
        assert dockerfile_member is not None
        assert hello_member is not None
        assert dockerfile_member.read().decode("utf-8") == recipe.dockerfile
        assert hello_member.read() == b"hello\n"
