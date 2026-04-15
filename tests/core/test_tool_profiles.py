"""Tests for structured delegated-runtime tool profiles."""

from __future__ import annotations

import json

import pytest

from tests.fixtures.provider_cli_fixture import (
    PROVIDER_FIXTURE_NAMES,
    write_provider_cli_wrapper,
)
from weft.core.agents import register_builtin_agent_runtimes
from weft.core.agents.runtime import clear_agent_runtime_registry, execute_agent_target
from weft.core.taskspec import AgentSection

_MODEL_PROVIDERS = frozenset({"claude_code", "codex", "gemini", "opencode", "qwen"})


@pytest.fixture(autouse=True)
def reset_runtime_registry() -> None:
    clear_agent_runtime_registry()
    register_builtin_agent_runtimes()


def make_agent_section(
    *,
    executable: str,
    provider_name: str,
    tool_profile_ref: str,
    model: str | None = None,
    options: dict[str, object] | None = None,
) -> AgentSection:
    return AgentSection.model_validate(
        {
            "runtime": "provider_cli",
            "model": model,
            "instructions": "base instructions",
            "options": options or {},
            "runtime_config": {
                "provider": provider_name,
                "executable": executable,
                "resolver_ref": (
                    "tests.fixtures.provider_cli_fixture:resolve_operator_question"
                ),
                "tool_profile_ref": tool_profile_ref,
            },
        }
    )


def _provider_model(provider_name: str) -> str | None:
    if provider_name in _MODEL_PROVIDERS:
        return "fixture-model"
    return None


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_structured_tool_profile_materializes_provider_specific_workspace_access(
    tmp_path,
    provider_name: str,
) -> None:
    result = execute_agent_target(
        make_agent_section(
            executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
            provider_name=provider_name,
            model=_provider_model(provider_name),
            tool_profile_ref=(
                "tests.fixtures.runtime_profiles_fixture:structured_tool_profile"
            ),
        ),
        {"task": "hello"},
        tid="123",
    )

    payload = json.loads(result.aggregate_public_output())
    if provider_name == "codex":
        assert payload["options"]["sandbox"] == "read-only"
    elif provider_name == "claude_code":
        assert payload["options"]["permission_mode"] == "plan"
    elif provider_name in {"gemini", "qwen"}:
        assert payload["options"]["approval_mode"] == "plan"
    elif provider_name == "opencode":
        assert "approval_mode" not in payload["options"]


def test_structured_tool_profile_rejects_conflicting_raw_provider_options(
    tmp_path,
) -> None:
    with pytest.raises(
        ValueError,
        match="spec.agent.options.sandbox conflicts with the resolved tool-profile workspace_access",
    ):
        execute_agent_target(
            make_agent_section(
                executable=str(write_provider_cli_wrapper(tmp_path, "codex")),
                provider_name="codex",
                tool_profile_ref=(
                    "tests.fixtures.runtime_profiles_fixture:structured_tool_profile"
                ),
                options={"sandbox": "workspace-write"},
            ),
            {"task": "hello"},
            tid="123",
        )


def test_claude_mcp_tool_profile_wires_explicit_config(tmp_path) -> None:
    result = execute_agent_target(
        make_agent_section(
            executable=str(write_provider_cli_wrapper(tmp_path, "claude_code")),
            provider_name="claude_code",
            tool_profile_ref=(
                "tests.fixtures.runtime_profiles_fixture:claude_mcp_tool_profile"
            ),
        ),
        {"task": "hello"},
        tid="123",
    )

    payload = json.loads(result.aggregate_public_output())
    assert payload["options"]["permission_mode"] == "plan"
    assert payload["options"]["strict_mcp_config"] is True
    assert "fixture-server" in payload["options"]["mcp_servers"]


@pytest.mark.parametrize("provider_name", ("codex", "gemini", "opencode", "qwen"))
def test_unsupported_mcp_tool_profile_fails_for_providers_without_explicit_support(
    tmp_path,
    provider_name: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="does not support explicit MCP server descriptors",
    ):
        execute_agent_target(
            make_agent_section(
                executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
                provider_name=provider_name,
                model=_provider_model(provider_name),
                tool_profile_ref=(
                    "tests.fixtures.runtime_profiles_fixture:unsupported_mcp_tool_profile"
                ),
            ),
            {"task": "hello"},
            tid="123",
        )
