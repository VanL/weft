"""Resolver and tool-profile helpers for delegated agent runtimes.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-5]
- docs/specifications/13A-Agent_Runtime_Planned.md [AR-A3]
"""

from __future__ import annotations

from typing import cast

from weft.core.imports import import_callable_ref
from weft.core.taskspec import AgentSection
from weft.ext import (
    AgentResolver,
    AgentResolverResult,
    AgentToolProfile,
    AgentToolProfileResult,
)

from .runtime import NormalizedAgentWorkItem, content_to_prompt_text


def resolve_agent_prompt(
    agent: AgentSection,
    work_item: NormalizedAgentWorkItem,
    *,
    tid: str | None = None,
    bundle_root: str | None = None,
) -> AgentResolverResult:
    """Return the resolved delegated-runtime prompt envelope."""
    resolver_ref = _runtime_config_ref(agent, "resolver_ref")
    if resolver_ref is None:
        return AgentResolverResult(prompt=content_to_prompt_text(work_item.content))

    resolver = cast(
        AgentResolver,
        import_callable_ref(resolver_ref, bundle_root=bundle_root),
    )
    result = resolver(agent=agent, work_item=work_item, tid=tid)
    if not isinstance(result, AgentResolverResult):
        raise TypeError("Agent resolver must return AgentResolverResult")
    return result


def resolve_agent_tool_profile(
    agent: AgentSection,
    *,
    tid: str | None = None,
    bundle_root: str | None = None,
) -> AgentToolProfileResult:
    """Return the resolved delegated-runtime tool profile."""
    profile_ref = _runtime_config_ref(agent, "tool_profile_ref")
    if profile_ref is None:
        return AgentToolProfileResult()

    profile = cast(
        AgentToolProfile,
        import_callable_ref(profile_ref, bundle_root=bundle_root),
    )
    result = profile(agent=agent, tid=tid)
    if not isinstance(result, AgentToolProfileResult):
        raise TypeError("Agent tool profile must return AgentToolProfileResult")
    return result


def load_agent_resolver(
    agent: AgentSection,
    *,
    bundle_root: str | None = None,
) -> AgentResolver | None:
    """Resolve the configured agent resolver callable if present."""
    resolver_ref = _runtime_config_ref(agent, "resolver_ref")
    if resolver_ref is None:
        return None
    return cast(
        AgentResolver,
        import_callable_ref(resolver_ref, bundle_root=bundle_root),
    )


def load_agent_tool_profile(
    agent: AgentSection,
    *,
    bundle_root: str | None = None,
) -> AgentToolProfile | None:
    """Resolve the configured agent tool-profile callable if present."""
    profile_ref = _runtime_config_ref(agent, "tool_profile_ref")
    if profile_ref is None:
        return None
    return cast(
        AgentToolProfile,
        import_callable_ref(profile_ref, bundle_root=bundle_root),
    )


def _runtime_config_ref(agent: AgentSection, key: str) -> str | None:
    value = agent.runtime_config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"spec.agent.runtime_config.{key} must be a non-empty string")
    return value.strip()


__all__ = [
    "load_agent_resolver",
    "load_agent_tool_profile",
    "resolve_agent_prompt",
    "resolve_agent_tool_profile",
]
