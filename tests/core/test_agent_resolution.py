"""Tests for delegated agent resolver and tool-profile helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft.core.agents.resolution import (
    resolve_agent_prompt,
    resolve_agent_tool_profile,
)
from weft.core.agents.runtime import NormalizedAgentMessage, NormalizedAgentWorkItem
from weft.core.taskspec import AgentSection


def make_agent_section(**overrides) -> AgentSection:
    payload = {
        "runtime": "provider_cli",
        "model": "gpt-5-codex",
        "runtime_config": {"provider": "codex"},
    }
    payload.update(overrides)
    return AgentSection.model_validate(payload)


def test_default_resolver_renders_plain_text_prompt() -> None:
    result = resolve_agent_prompt(
        make_agent_section(),
        NormalizedAgentWorkItem(content="hello"),
        tid="123",
    )

    assert result.prompt == "hello"
    assert result.instructions is None
    assert dict(result.metadata) == {}
    assert tuple(result.artifacts) == ()


def test_default_resolver_flattens_structured_messages() -> None:
    result = resolve_agent_prompt(
        make_agent_section(),
        NormalizedAgentWorkItem(
            content=(
                NormalizedAgentMessage(role="system", content="be brief"),
                NormalizedAgentMessage(role="user", content={"path": "README.md"}),
            )
        ),
        tid="123",
    )

    assert result.prompt == 'system: be brief\n\nuser: {"path":"README.md"}'


def test_resolver_ref_must_return_agent_resolver_result() -> None:
    with pytest.raises(
        TypeError, match="Agent resolver must return AgentResolverResult"
    ):
        resolve_agent_prompt(
            make_agent_section(
                runtime_config={
                    "provider": "codex",
                    "resolver_ref": "tests.fixtures.provider_cli_fixture:invalid_resolver",
                }
            ),
            NormalizedAgentWorkItem(content="hello"),
            tid="123",
        )


def test_tool_profile_ref_must_return_agent_tool_profile_result() -> None:
    with pytest.raises(
        TypeError,
        match="Agent tool profile must return AgentToolProfileResult",
    ):
        resolve_agent_tool_profile(
            make_agent_section(
                runtime_config={
                    "provider": "codex",
                    "tool_profile_ref": "tests.fixtures.provider_cli_fixture:invalid_tool_profile",
                }
            ),
            tid="123",
        )


def test_bundle_local_resolver_and_tool_profile(tmp_path: Path) -> None:
    helper = tmp_path / "helper_module.py"
    helper.write_text(
        "\n".join(
            [
                "from weft.ext import AgentResolverResult, AgentToolProfileResult",
                "",
                "def bundle_resolver(*, agent, work_item, tid=None):",
                "    del agent, tid",
                "    return AgentResolverResult(prompt=f'bundle:{work_item.content}')",
                "",
                "def bundle_tool_profile(*, agent, tid=None):",
                "    del agent, tid",
                "    return AgentToolProfileResult(",
                "        instructions='bundle-tools',",
                "        metadata={'profile': 'bundle-local'},",
                "    )",
                "",
            ]
        ),
        encoding="utf-8",
    )
    agent = make_agent_section(
        runtime_config={
            "provider": "codex",
            "resolver_ref": "helper_module:bundle_resolver",
            "tool_profile_ref": "helper_module:bundle_tool_profile",
        }
    )

    prompt = resolve_agent_prompt(
        agent,
        NormalizedAgentWorkItem(content="hello"),
        tid="123",
        bundle_root=str(tmp_path),
    )
    tool_profile = resolve_agent_tool_profile(
        agent,
        tid="123",
        bundle_root=str(tmp_path),
    )

    assert prompt.prompt == "bundle:hello"
    assert tool_profile.instructions == "bundle-tools"
    assert dict(tool_profile.metadata) == {"profile": "bundle-local"}
