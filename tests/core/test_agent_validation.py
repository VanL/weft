"""Tests for shared agent runtime validation helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.provider_cli_fixture import (
    PROVIDER_FIXTURE_NAMES,
    write_provider_cli_wrapper,
)
from tests.taskspec.fixtures import create_valid_provider_cli_agent_taskspec
from weft.core.agents.provider_cli.registry import list_provider_cli_providers
from weft.core.agents.validation import (
    validate_taskspec_agent_runtime,
    validate_taskspec_agent_tool_profile,
)

_MODEL_PROVIDERS = frozenset({"claude_code", "codex", "gemini", "opencode", "qwen"})


def test_provider_registry_matches_agent_mcp_provider_set() -> None:
    assert tuple(provider.name for provider in list_provider_cli_providers()) == (
        "claude_code",
        "codex",
        "gemini",
        "opencode",
        "qwen",
    )


def test_validate_agent_runtime_rejects_unknown_provider(tmp_path) -> None:
    taskspec = create_valid_provider_cli_agent_taskspec(
        executable=str(write_provider_cli_wrapper(tmp_path, "codex")),
    )
    payload = taskspec.model_dump(mode="python")
    payload["spec"]["agent"]["runtime_config"]["provider"] = "missing"

    with pytest.raises(ValueError, match="Unknown provider_cli provider"):
        validate_taskspec_agent_runtime(payload, load_runtime=True)


def test_validate_agent_runtime_preflight_rejects_missing_executable() -> None:
    taskspec = create_valid_provider_cli_agent_taskspec(
        executable=str(Path("/nonexistent/provider-cli")),
    )
    payload = taskspec.model_dump(mode="python")

    with pytest.raises(RuntimeError, match="Unable to locate executable"):
        validate_taskspec_agent_runtime(payload, load_runtime=True, preflight=True)


def test_validate_agent_runtime_preflight_docker_one_shot_skips_host_executable_check(
    tmp_path,
) -> None:
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider="codex",
        executable=str(Path("/nonexistent/provider-cli")),
    )
    payload = taskspec.model_dump(mode="python")
    payload["spec"]["runner"] = {
        "name": "docker",
        "options": {},
    }

    validate_taskspec_agent_runtime(payload, load_runtime=True, preflight=True)


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_validate_agent_runtime_preflight_accepts_real_fixture_executable(
    tmp_path,
    provider_name: str,
) -> None:
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider=provider_name,
        executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
        model="fixture-model" if provider_name in _MODEL_PROVIDERS else None,
    )
    payload = taskspec.model_dump(mode="python")

    validate_taskspec_agent_runtime(payload, load_runtime=True, preflight=True)


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_validate_agent_runtime_preflight_does_not_launch_provider_subprocess(
    tmp_path,
    provider_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_CLI_FIXTURE_FAIL_PROBE", "1")
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider=provider_name,
        executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
        model="fixture-model" if provider_name in _MODEL_PROVIDERS else None,
    )
    payload = taskspec.model_dump(mode="python")

    validate_taskspec_agent_runtime(payload, load_runtime=True, preflight=True)


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_validate_agent_runtime_preflight_accepts_persistent_provider_cli_session(
    tmp_path,
    provider_name: str,
) -> None:
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider=provider_name,
        executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
        model="fixture-model" if provider_name in _MODEL_PROVIDERS else None,
        persistent=True,
        conversation_scope="per_task",
    )
    payload = taskspec.model_dump(mode="python")

    validate_taskspec_agent_runtime(payload, load_runtime=True, preflight=True)


def test_validate_agent_runtime_preflight_does_not_check_opencode_run_support(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_CLI_FIXTURE_OPENCODE_NO_RUN", "1")
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider="opencode",
        executable=str(write_provider_cli_wrapper(tmp_path, "opencode")),
    )
    payload = taskspec.model_dump(mode="python")

    validate_taskspec_agent_runtime(payload, load_runtime=True, preflight=True)

    monkeypatch.delenv("PROVIDER_CLI_FIXTURE_OPENCODE_NO_RUN", raising=False)


def test_validate_agent_runtime_preflight_uses_project_agent_settings_for_executable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".weft").mkdir()
    wrapper = write_provider_cli_wrapper(tmp_path, "codex")
    (tmp_path / ".weft" / "agents.json").write_text(
        json.dumps(
            {
                "provider_cli": {
                    "providers": {
                        "codex": {
                            "executable": str(wrapper),
                        }
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider="codex",
        executable=str(wrapper),
    )
    payload = taskspec.model_dump(mode="python")
    payload["spec"]["agent"]["runtime_config"].pop("executable", None)

    validate_taskspec_agent_runtime(payload, load_runtime=True, preflight=True)


def test_validate_agent_runtime_rejects_bounded_opencode(tmp_path) -> None:
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider="opencode",
        executable=str(write_provider_cli_wrapper(tmp_path, "opencode")),
        authority_class="bounded",
    )
    payload = taskspec.model_dump(mode="python")

    with pytest.raises(ValueError, match="does not support authority_class='bounded'"):
        validate_taskspec_agent_runtime(payload, load_runtime=True)


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_validate_agent_tool_profile_accepts_structured_workspace_access(
    tmp_path,
    provider_name: str,
) -> None:
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider=provider_name,
        executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
        model="fixture-model" if provider_name in _MODEL_PROVIDERS else None,
    )
    payload = taskspec.model_dump(mode="python")
    payload["spec"]["agent"]["runtime_config"]["tool_profile_ref"] = (
        "tests.fixtures.runtime_profiles_fixture:structured_tool_profile"
    )

    validate_taskspec_agent_tool_profile(payload, load_runtime=True, preflight=True)


def test_validate_agent_tool_profile_accepts_claude_explicit_mcp(tmp_path) -> None:
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider="claude_code",
        executable=str(write_provider_cli_wrapper(tmp_path, "claude_code")),
    )
    payload = taskspec.model_dump(mode="python")
    payload["spec"]["agent"]["runtime_config"]["tool_profile_ref"] = (
        "tests.fixtures.runtime_profiles_fixture:claude_mcp_tool_profile"
    )

    validate_taskspec_agent_tool_profile(payload, load_runtime=True, preflight=True)


@pytest.mark.parametrize("provider_name", ("codex", "gemini", "opencode", "qwen"))
def test_validate_agent_tool_profile_rejects_unsupported_explicit_mcp(
    tmp_path,
    provider_name: str,
) -> None:
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider=provider_name,
        executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
        model="fixture-model" if provider_name in _MODEL_PROVIDERS else None,
    )
    payload = taskspec.model_dump(mode="python")
    payload["spec"]["agent"]["runtime_config"]["tool_profile_ref"] = (
        "tests.fixtures.runtime_profiles_fixture:unsupported_mcp_tool_profile"
    )

    with pytest.raises(
        ValueError,
        match="does not support explicit MCP server descriptors",
    ):
        validate_taskspec_agent_tool_profile(payload, load_runtime=True, preflight=True)
