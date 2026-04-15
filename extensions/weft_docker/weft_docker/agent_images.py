"""Provider image recipes and cache helpers for Docker-backed agent runs.

Spec references:
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ._sdk import docker_client
from .images import DockerImageRecipe, ensure_docker_image


@dataclass(frozen=True, slots=True)
class AgentImageRecipe:
    """Deterministic image recipe for one provider CLI runtime."""

    provider: str
    default_executable: str | None
    image_recipe: DockerImageRecipe


@dataclass(frozen=True, slots=True)
class AgentImageEnsureResult:
    """Result of ensuring one provider runtime image."""

    provider: str
    image: str
    cache_key: str
    action: Literal["reused", "built"]
    recipe: AgentImageRecipe


def get_agent_image_recipe(provider_name: str) -> AgentImageRecipe | None:
    """Return the shipped agent image recipe for *provider_name*, if any."""

    normalized = provider_name.strip()
    if normalized == "codex":
        return _node_agent_recipe(
            provider="codex",
            default_executable="codex",
            npm_package="@openai/codex",
        )
    if normalized == "claude_code":
        return _node_agent_recipe(
            provider="claude_code",
            default_executable="claude",
            npm_package="@anthropic-ai/claude-code",
        )
    if normalized == "gemini":
        return _node_agent_recipe(
            provider="gemini",
            default_executable="gemini",
            npm_package="@google/gemini-cli",
        )
    if normalized == "opencode":
        return _node_agent_recipe(
            provider="opencode",
            default_executable="opencode",
            npm_package="opencode-ai",
        )
    if normalized == "qwen":
        return _node_agent_recipe(
            provider="qwen",
            default_executable="qwen",
            npm_package="@qwen-code/qwen-code",
        )
    return None


def _node_agent_recipe(
    *,
    provider: str,
    default_executable: str,
    npm_package: str,
) -> AgentImageRecipe:
    return AgentImageRecipe(
        provider=provider,
        default_executable=default_executable,
        image_recipe=DockerImageRecipe(
            name=provider,
            version="v1",
            dockerfile=f"""
FROM node:22-bookworm-slim
RUN apt-get update \\
    && apt-get install -y --no-install-recommends ca-certificates git \\
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g {npm_package}
""".strip()
            + "\n",
            labels={
                "weft.agent.provider": provider,
                "weft.agent.recipe.version": "v1",
            },
        ),
    )


def ensure_agent_image(
    provider_name: str,
    *,
    refresh: bool = False,
) -> AgentImageEnsureResult:
    """Build or reuse the image for *provider_name*."""

    recipe = get_agent_image_recipe(provider_name)
    if recipe is None:
        raise ValueError(
            f"No Docker-backed agent image recipe is available for provider '{provider_name}'"
        )

    with docker_client() as client:
        build_result = ensure_docker_image(
            client,
            recipe.image_recipe,
            refresh=refresh,
        )
    return AgentImageEnsureResult(
        provider=provider_name,
        image=build_result.image,
        cache_key=build_result.cache_key,
        action=build_result.action,
        recipe=recipe,
    )


__all__ = [
    "AgentImageEnsureResult",
    "AgentImageRecipe",
    "ensure_agent_image",
    "get_agent_image_recipe",
]
