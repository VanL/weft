"""Shared preparation helpers for delegated provider CLI execution.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-5]
- docs/specifications/13-Agent_Runtime.md [AR-7]
- docs/specifications/13A-Agent_Runtime_Planned.md [AR-A3], [AR-A5]
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from weft.core.taskspec import AgentSection

from ..resolution import resolve_agent_prompt, resolve_agent_tool_profile
from ..runtime import AgentExecutionResult, NormalizedAgentWorkItem
from .registry import (
    ProviderCLIInvocation,
    ProviderCLIProvider,
    ProviderCLIResult,
    get_provider_cli_provider,
)


@dataclass(frozen=True, slots=True)
class ProviderCLIPreparedTurn:
    """Resolved, transport-neutral inputs for one delegated provider CLI turn."""

    provider: ProviderCLIProvider
    authority_class: str
    prompt: str
    provider_options: dict[str, Any]
    resolver_result: Any
    tool_profile: Any


@dataclass(frozen=True, slots=True)
class ProviderCLIPreparedExecution:
    """Prepared one-shot provider CLI invocation and response metadata."""

    provider: ProviderCLIProvider
    executable: str
    authority_class: str
    invocation: ProviderCLIInvocation
    provider_options: dict[str, Any]
    resolver_result: Any
    tool_profile: Any


def prepare_provider_cli_turn(
    *,
    agent: AgentSection,
    work_item: NormalizedAgentWorkItem,
    tid: str | None,
    bundle_root: str | None = None,
    preflight_tool_profile: bool = False,
) -> ProviderCLIPreparedTurn:
    """Resolve the shared per-turn inputs for a delegated provider CLI request.

    One-shot execution and persistent sessions resolve identical inputs, so both
    paths go through this helper. It performs no invocation building, which
    keeps it neutral between `build_invocation` and `build_session_invocation`.

    Args:
        agent: Validated `provider_cli` agent section.
        work_item: Normalized work item for this turn.
        tid: Owning task id, passed through to resolver and tool profile.
        bundle_root: Optional spec bundle root for reference resolution.
        preflight_tool_profile: Whether the provider should apply preflight
            tool-profile checks. Sessions preflight once at `start_session`
            and pass `False` for each subsequent turn.

    Returns:
        Resolved provider, authority class, composed prompt, provider options,
        resolver result, and tool profile for this turn.

    Spec: [AR-5]
    """

    resolver_result = resolve_agent_prompt(
        agent,
        work_item,
        tid=tid,
        bundle_root=bundle_root,
    )
    tool_profile = resolve_agent_tool_profile(
        agent,
        tid=tid,
        bundle_root=bundle_root,
    )
    provider = resolve_provider_cli(agent)
    validate_authority_tool_profile(agent, tool_profile)
    provider_options = provider.resolve_options(
        authority_class=agent.resolved_authority_class,
        raw_options=dict(agent.options),
        tool_profile=tool_profile,
    )
    provider.validate_tool_profile(tool_profile, preflight=preflight_tool_profile)
    provider.validate_model(agent.model)
    prompt = compose_provider_cli_prompt(
        work_item.instructions,
        resolver_result.instructions,
        tool_profile.instructions,
        resolver_result.prompt,
    )
    return ProviderCLIPreparedTurn(
        provider=provider,
        authority_class=agent.resolved_authority_class,
        prompt=prompt,
        provider_options=provider_options,
        resolver_result=resolver_result,
        tool_profile=tool_profile,
    )


def prepare_provider_cli_execution(
    *,
    agent: AgentSection,
    work_item: NormalizedAgentWorkItem,
    tid: str | None,
    executable: str,
    cwd: str,
    tempdir: Path,
    bundle_root: str | None = None,
    preflight_tool_profile: bool = False,
) -> ProviderCLIPreparedExecution:
    """Prepare one-shot delegated CLI execution inputs.

    This helper is transport-neutral. Host and Docker-backed one-shot paths can
    both use it by supplying the executable, working directory, and tempdir as
    seen by the process that will actually execute the provider CLI.
    """

    turn = prepare_provider_cli_turn(
        agent=agent,
        work_item=work_item,
        tid=tid,
        bundle_root=bundle_root,
        preflight_tool_profile=preflight_tool_profile,
    )
    invocation = turn.provider.build_invocation(
        executable=executable,
        authority_class=turn.authority_class,
        prompt=turn.prompt,
        cwd=cwd,
        model=agent.model,
        options=turn.provider_options,
        tempdir=tempdir,
        tool_profile=turn.tool_profile,
    )
    return ProviderCLIPreparedExecution(
        provider=turn.provider,
        executable=executable,
        authority_class=turn.authority_class,
        invocation=invocation,
        provider_options=turn.provider_options,
        resolver_result=turn.resolver_result,
        tool_profile=turn.tool_profile,
    )


def build_provider_cli_execution_result(
    *,
    agent: AgentSection,
    prepared: ProviderCLIPreparedExecution,
    work_item: NormalizedAgentWorkItem,
    provider_result: ProviderCLIResult,
    session_metadata: dict[str, Any] | None = None,
) -> AgentExecutionResult:
    """Build the Weft-facing agent execution result for a provider CLI turn."""

    metadata: dict[str, Any] = {
        "provider": prepared.provider.name,
        "executable": prepared.executable,
        "resolver_ref": runtime_config_str(agent, "resolver_ref"),
        "tool_profile_ref": runtime_config_str(agent, "tool_profile_ref"),
        "request_metadata": dict(work_item.metadata),
    }
    resolver_metadata = getattr(prepared.resolver_result, "metadata", None)
    if resolver_metadata:
        metadata["resolver_metadata"] = dict(resolver_metadata)
    tool_profile_metadata = getattr(prepared.tool_profile, "metadata", None)
    if tool_profile_metadata:
        metadata["tool_profile_metadata"] = dict(tool_profile_metadata)
    if prepared.provider_options:
        metadata["provider_option_keys"] = sorted(prepared.provider_options)
    if session_metadata:
        metadata["session"] = dict(session_metadata)

    artifacts = getattr(prepared.resolver_result, "artifacts", ())
    return AgentExecutionResult(
        runtime=agent.runtime,
        model=agent.model,
        output_mode=agent.output_mode,
        outputs=(provider_result.output_text,),
        metadata=metadata,
        usage=provider_result.usage,
        tool_trace=(),
        artifacts=tuple(dict(item) for item in artifacts),
    )


def compose_provider_cli_prompt(*parts: str | None) -> str:
    """Compose the final prompt body from static and dynamic prompt parts."""

    normalized = [
        part.strip() for part in parts if isinstance(part, str) and part.strip()
    ]
    if not normalized:
        raise ValueError("provider_cli requires a non-empty prompt")
    return "\n\n".join(normalized)


def validate_authority_tool_profile(agent: AgentSection, tool_profile: Any) -> None:
    """Validate coarse Weft authority against the resolved tool profile."""

    if (
        agent.resolved_authority_class == "bounded"
        and getattr(tool_profile, "workspace_access", None) == "workspace-write"
    ):
        raise ValueError(
            "authority_class='bounded' does not allow workspace_access='workspace-write'"
        )


def resolve_provider_cli(agent: AgentSection) -> ProviderCLIProvider:
    """Resolve the provider adapter configured for a provider_cli agent."""

    provider_name = runtime_config_str(agent, "provider")
    if provider_name is None:
        raise ValueError("provider_cli requires spec.agent.runtime_config.provider")
    return get_provider_cli_provider(provider_name)


def runtime_config_str(agent: AgentSection, key: str) -> str | None:
    """Return a non-empty string runtime_config field when present."""

    value = agent.runtime_config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"spec.agent.runtime_config.{key} must be a non-empty string")
    return value.strip()


__all__ = [
    "ProviderCLIPreparedExecution",
    "ProviderCLIPreparedTurn",
    "build_provider_cli_execution_result",
    "compose_provider_cli_prompt",
    "prepare_provider_cli_execution",
    "prepare_provider_cli_turn",
    "resolve_provider_cli",
    "runtime_config_str",
    "validate_authority_tool_profile",
]
