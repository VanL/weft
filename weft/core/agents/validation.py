"""Shared validation helpers for agent-runtime-backed TaskSpecs.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-5]
- docs/specifications/02-TaskSpec.md [TS-1]
- docs/specifications/10-CLI_Interface.md [CLI-1.4.1]
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from weft.core.agents.backends import register_builtin_agent_runtimes
from weft.core.taskspec import AgentSection, bundle_root_from_taskspec_payload

from .runtime import get_agent_runtime


def validate_taskspec_agent_runtime(
    taskspec_payload: Mapping[str, Any],
    *,
    load_runtime: bool = False,
    preflight: bool = False,
) -> None:
    """Validate the configured agent runtime for a TaskSpec payload."""
    if preflight:
        load_runtime = True
    if not load_runtime:
        return

    spec = _require_mapping(taskspec_payload.get("spec"), name="spec")
    if spec.get("type") != "agent":
        return

    register_builtin_agent_runtimes()

    agent = AgentSection.model_validate(
        _require_mapping(spec.get("agent"), name="spec.agent")
    )
    bundle_root = bundle_root_from_taskspec_payload(taskspec_payload)
    runtime = get_agent_runtime(agent.runtime)
    validate = getattr(runtime, "validate", None)
    if callable(validate):
        effective_preflight = preflight and not _is_docker_one_shot_provider_cli(
            taskspec_payload
        )
        validate(
            agent=agent,
            preflight=effective_preflight,
            bundle_root=bundle_root,
        )


def validate_taskspec_agent_tool_profile(
    taskspec_payload: Mapping[str, Any],
    *,
    load_runtime: bool = False,
    preflight: bool = False,
) -> None:
    """Validate the configured delegated-runtime tool profile for a TaskSpec."""
    if preflight:
        load_runtime = True
    if not load_runtime:
        return

    spec = _require_mapping(taskspec_payload.get("spec"), name="spec")
    if spec.get("type") != "agent":
        return

    register_builtin_agent_runtimes()

    agent = AgentSection.model_validate(
        _require_mapping(spec.get("agent"), name="spec.agent")
    )
    bundle_root = bundle_root_from_taskspec_payload(taskspec_payload)
    runtime = get_agent_runtime(agent.runtime)
    validate = getattr(runtime, "validate_tool_profile", None)
    if callable(validate):
        effective_preflight = preflight and not _is_docker_one_shot_provider_cli(
            taskspec_payload
        )
        validate(
            agent=agent,
            tid=_tid_from_payload(taskspec_payload),
            preflight=effective_preflight,
            bundle_root=bundle_root,
        )


def agent_runtime_name_from_taskspec(taskspec_payload: Mapping[str, Any]) -> str | None:
    """Return the configured agent runtime name when the TaskSpec is an agent."""
    spec = _require_mapping(taskspec_payload.get("spec"), name="spec")
    if spec.get("type") != "agent":
        return None
    agent = _require_mapping(spec.get("agent"), name="spec.agent")
    name = agent.get("runtime")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("spec.agent.runtime must be a non-empty string")
    return name.strip()


def _require_mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _tid_from_payload(taskspec_payload: Mapping[str, Any]) -> str | None:
    value = taskspec_payload.get("tid")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _is_docker_one_shot_provider_cli(taskspec_payload: Mapping[str, Any]) -> bool:
    spec = taskspec_payload.get("spec")
    if not isinstance(spec, Mapping):
        return False
    if spec.get("type") != "agent":
        return False
    if bool(spec.get("persistent", False)):
        return False
    runner = spec.get("runner")
    if not isinstance(runner, Mapping):
        return False
    runner_name = runner.get("name")
    if not isinstance(runner_name, str) or runner_name.strip() != "docker":
        return False
    agent = spec.get("agent")
    if not isinstance(agent, Mapping):
        return False
    runtime = agent.get("runtime")
    if not isinstance(runtime, str) or runtime.strip() != "provider_cli":
        return False
    conversation_scope = agent.get("conversation_scope", "per_message")
    return (
        isinstance(conversation_scope, str)
        and conversation_scope.strip() == "per_message"
    )


__all__ = [
    "agent_runtime_name_from_taskspec",
    "validate_taskspec_agent_runtime",
    "validate_taskspec_agent_tool_profile",
]
