"""Support code for the Docker-backed agent builtin.

Spec references:
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import copy
from typing import Any

from weft._constants import (
    DOCKERIZED_AGENT_CLAUDE_ENVIRONMENT_PROFILE_REF,
    DOCKERIZED_AGENT_DEFAULT_ENVIRONMENT_PROFILE_REF,
    DOCKERIZED_AGENT_PROVIDER_CHOICES,
)
from weft.builtins.dockerized_agent_examples import (
    dockerized_agent_environment_profile,
    dockerized_agent_run_input,
)
from weft.core.spec_parameterization import SpecParameterizationRequest
from weft.ext import AgentToolProfileResult


def dockerized_agent_parameterization(
    request: SpecParameterizationRequest,
) -> dict[str, Any]:
    """Materialize one concrete provider-specific builtin TaskSpec template."""
    provider = request.arguments["provider"]
    if provider not in DOCKERIZED_AGENT_PROVIDER_CHOICES:
        raise ValueError(f"Unsupported provider for dockerized-agent: {provider}")

    payload = copy.deepcopy(dict(request.taskspec_payload))
    payload["name"] = f"dockerized-agent-{provider}"
    payload["description"] = (
        "Docker-backed delegated "
        f"{provider} TaskSpec that reads a caller-supplied document and explains "
        "it in one sentence."
    )

    spec = payload.setdefault("spec", {})
    agent = spec.setdefault("agent", {})
    agent["runtime"] = "provider_cli"
    agent["authority_class"] = "general"
    if provider == "opencode":
        agent["model"] = request.arguments.get("model") or "openai/gpt-5"
    elif "model" in request.arguments:
        agent["model"] = request.arguments["model"]
    else:
        agent.pop("model", None)

    runtime_config = dict(agent.get("runtime_config") or {})
    runtime_config["provider"] = provider
    runtime_config["tool_profile_ref"] = (
        "dockerized_agent:dockerized_agent_tool_profile"
    )
    agent["runtime_config"] = runtime_config
    runner = spec.setdefault("runner", {})
    if provider == "claude_code":
        runner["environment_profile_ref"] = (
            DOCKERIZED_AGENT_CLAUDE_ENVIRONMENT_PROFILE_REF
        )
    else:
        runner["environment_profile_ref"] = (
            DOCKERIZED_AGENT_DEFAULT_ENVIRONMENT_PROFILE_REF
        )
    return payload


def dockerized_agent_tool_profile(
    *,
    agent: Any,
    tid: str | None,
) -> AgentToolProfileResult:
    """Resolve the delegated tool profile for the shipped Docker builtin."""
    provider = None
    if hasattr(agent, "runtime_config"):
        runtime_config = agent.runtime_config
        if isinstance(runtime_config, dict):
            provider = runtime_config.get("provider")
    provider_options: dict[str, Any] = {}
    if provider == "codex":
        provider_options = {
            "sandbox": "danger-full-access",
            "skip_git_repo_check": True,
        }
    return AgentToolProfileResult(
        provider_options=provider_options,
        metadata={
            "builtin": "dockerized-agent",
            "provider": provider,
            "tid": tid,
        },
    )


__all__ = [
    "dockerized_agent_environment_profile",
    "dockerized_agent_parameterization",
    "dockerized_agent_run_input",
    "dockerized_agent_tool_profile",
]
