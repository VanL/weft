"""Tests for the delegated provider_cli backend."""

from __future__ import annotations

import json
from pathlib import Path

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
    executable: str | None,
    provider_name: str = "codex",
    model: str | None = None,
    options: dict[str, object] | None = None,
    resolver_ref: str
    | None = "tests.fixtures.provider_cli_fixture:resolve_operator_question",
    tool_profile_ref: str
    | None = "tests.fixtures.provider_cli_fixture:provider_tool_profile",
    **overrides,
) -> AgentSection:
    runtime_config = {
        "provider": provider_name,
    }
    if executable is not None:
        runtime_config["executable"] = executable
    if resolver_ref is not None:
        runtime_config["resolver_ref"] = resolver_ref
    if tool_profile_ref is not None:
        runtime_config["tool_profile_ref"] = tool_profile_ref
    payload = {
        "runtime": "provider_cli",
        "model": model,
        "instructions": "base instructions",
        "options": options or {},
        "runtime_config": runtime_config,
    }
    payload.update(overrides)
    return AgentSection.model_validate(payload)


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_provider_cli_runtime_executes_one_shot_request(
    tmp_path,
    provider_name: str,
) -> None:
    executable = str(write_provider_cli_wrapper(tmp_path, provider_name))
    model = "fixture-model" if provider_name in _MODEL_PROVIDERS else None

    result = execute_agent_target(
        make_agent_section(
            executable=executable,
            provider_name=provider_name,
            model=model,
        ),
        {"task": "hello", "metadata": {"request_id": "abc-123"}},
        tid="123",
    )

    payload = json.loads(result.aggregate_public_output())
    assert payload["provider"] == provider_name
    assert payload["model"] == model
    assert payload["prompt"] == (
        "base instructions\n\n"
        "resolver instructions\n\n"
        "profile instructions\n\n"
        "resolved:hello"
    )
    assert result.metadata["provider"] == provider_name
    assert result.metadata["request_metadata"] == {"request_id": "abc-123"}
    assert result.artifacts == ({"kind": "fixture_artifact", "id": "artifact-1"},)


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_provider_cli_runtime_surfaces_non_zero_exit(
    tmp_path,
    provider_name: str,
) -> None:
    executable = str(write_provider_cli_wrapper(tmp_path, provider_name))
    model = "fixture-model" if provider_name in _MODEL_PROVIDERS else None

    with pytest.raises(RuntimeError, match="fixture exec failed"):
        execute_agent_target(
            make_agent_section(
                executable=executable,
                provider_name=provider_name,
                model=model,
            ),
            {"task": "fail:fixture exec failed"},
            tid="123",
        )


@pytest.mark.parametrize(
    ("provider_name", "options", "match"),
    (
        (
            "codex",
            {"sandbox": "workspace-write"},
            "spec.agent.options.sandbox conflicts with the resolved tool-profile sandbox",
        ),
        (
            "qwen",
            {"disable_extensions": False},
            "spec.agent.options.disable_extensions conflicts with the resolved tool profile",
        ),
    ),
)
def test_provider_cli_runtime_rejects_raw_options_that_conflict_with_tool_profile(
    tmp_path,
    provider_name: str,
    options: dict[str, object],
    match: str,
) -> None:
    executable = str(write_provider_cli_wrapper(tmp_path, provider_name))
    model = "fixture-model" if provider_name in _MODEL_PROVIDERS else None

    with pytest.raises(ValueError, match=match):
        execute_agent_target(
            make_agent_section(
                executable=executable,
                provider_name=provider_name,
                model=model,
                options=options,
            ),
            {"task": "hello"},
            tid="123",
        )


@pytest.mark.parametrize(
    ("provider_name", "expected_option", "expected_value"),
    (
        ("claude_code", "permission_mode", "plan"),
        ("codex", "sandbox", "read-only"),
        ("gemini", "approval_mode", "plan"),
        ("qwen", "approval_mode", "plan"),
    ),
)
def test_provider_cli_runtime_executes_explicit_bounded_authority(
    tmp_path,
    provider_name: str,
    expected_option: str,
    expected_value: str,
) -> None:
    executable = str(write_provider_cli_wrapper(tmp_path, provider_name))
    model = "fixture-model"

    result = execute_agent_target(
        make_agent_section(
            executable=executable,
            provider_name=provider_name,
            model=model,
            authority_class="bounded",
            resolver_ref=None,
            tool_profile_ref=None,
        ),
        {"task": "hello"},
        tid="123",
    )

    payload = json.loads(result.aggregate_public_output())
    assert payload["provider"] == provider_name
    assert payload["options"][expected_option] == expected_value
    if provider_name == "claude_code":
        assert payload["options"]["strict_mcp_config"] is True
        assert payload["options"]["mcp_servers"] == {}
    if provider_name == "qwen":
        assert payload["options"]["extensions"] == ""
        assert payload["options"]["allowed_mcp_server_names"] == ""


def test_provider_cli_runtime_uses_project_agent_settings_executable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".weft").mkdir()
    wrapper = write_provider_cli_wrapper(tmp_path, "codex")
    _write_agent_settings(
        tmp_path / ".weft" / "agents.json",
        provider_name="codex",
        executable=str(wrapper),
    )

    result = execute_agent_target(
        make_agent_section(
            executable=None,
            provider_name="codex",
        ),
        {"task": "hello"},
        tid="123",
    )

    payload = json.loads(result.aggregate_public_output())
    assert payload["provider"] == "codex"


def test_provider_cli_runtime_records_advisory_health_without_gating_execution(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    weft_dir = tmp_path / ".weft"
    weft_dir.mkdir()
    wrapper = write_provider_cli_wrapper(tmp_path, "codex")
    _write_agent_settings(
        weft_dir / "agents.json",
        provider_name="codex",
        executable=str(wrapper),
    )
    health_path = weft_dir / "agent-health.json"
    health_path.write_text(
        json.dumps(
            {
                "provider_cli": {
                    "providers": {
                        "codex": {
                            "last_failure": {
                                "at": "2026-04-14T00:00:00+00:00",
                                "executable": "/missing/codex",
                                "command_class": "execute",
                                "detail": "stale failure",
                            }
                        }
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = execute_agent_target(
        make_agent_section(
            executable=None,
            provider_name="codex",
        ),
        {"task": "hello"},
        tid="123",
    )
    success_payload = json.loads(result.aggregate_public_output())
    assert success_payload["provider"] == "codex"

    health_payload = json.loads(health_path.read_text(encoding="utf-8"))
    codex_payload = health_payload["provider_cli"]["providers"]["codex"]
    assert codex_payload["last_success"]["executable"] == str(wrapper)
    assert codex_payload["last_failure"]["detail"] == "stale failure"

    with pytest.raises(RuntimeError, match="fixture exec failed"):
        execute_agent_target(
            make_agent_section(
                executable=None,
                provider_name="codex",
            ),
            {"task": "fail:fixture exec failed"},
            tid="123",
        )

    failed_health_payload = json.loads(health_path.read_text(encoding="utf-8"))
    failed_codex_payload = failed_health_payload["provider_cli"]["providers"]["codex"]
    assert failed_codex_payload["last_failure"]["executable"] == str(wrapper)
    assert "fixture exec failed" in failed_codex_payload["last_failure"]["detail"]


def test_provider_cli_runtime_rejects_opencode_without_run_support_at_first_execution(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_CLI_FIXTURE_OPENCODE_NO_RUN", "1")
    executable = str(write_provider_cli_wrapper(tmp_path, "opencode"))

    with pytest.raises(RuntimeError, match="does not support 'run'"):
        execute_agent_target(
            make_agent_section(
                executable=executable,
                provider_name="opencode",
                model="fixture-model",
            ),
            {"task": "hello"},
            tid="123",
        )


def _write_agent_settings(path: Path, *, provider_name: str, executable: str) -> None:
    path.write_text(
        json.dumps(
            {
                "provider_cli": {
                    "providers": {
                        provider_name: {
                            "executable": executable,
                        }
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
