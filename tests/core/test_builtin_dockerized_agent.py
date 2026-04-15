"""Tests for the bundled Docker-backed agent builtin."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft.builtins import dockerized_agent_examples as builtin_examples
from weft.commands import specs as spec_cmd
from weft.core.agents.resolution import resolve_agent_tool_profile
from weft.core.environment_profiles import materialize_runner_environment
from weft.core.spec_parameterization import materialize_taskspec_template
from weft.core.spec_run_input import SpecRunInputRequest, invoke_run_input_adapter
from weft.core.taskspec import TaskSpec


def _load_builtin_taskspec() -> TaskSpec:
    resolved = spec_cmd.resolve_named_spec(
        "dockerized-agent",
        spec_type=spec_cmd.SPEC_TYPE_TASK,
    )
    taskspec = TaskSpec.model_validate(
        resolved.payload,
        context={"template": True, "auto_expand": False},
    )
    taskspec.set_bundle_root(resolved.bundle_root)
    return taskspec


def test_dockerized_agent_builtin_resolves_from_bundle() -> None:
    resolved = spec_cmd.resolve_named_spec(
        "dockerized-agent",
        spec_type=spec_cmd.SPEC_TYPE_TASK,
    )

    assert resolved.path.as_posix().endswith(
        "weft/builtins/tasks/dockerized-agent/taskspec.json"
    )
    assert resolved.bundle_root is not None


def test_dockerized_agent_environment_profile_defers_document_mount_to_work_item() -> (
    None
):
    taskspec = _load_builtin_taskspec()
    bundle_root = taskspec.get_bundle_root()
    assert bundle_root is not None

    result = materialize_runner_environment(
        target_type="agent",
        runner_name=taskspec.spec.runner.name,
        runner_options=taskspec.spec.runner.options,
        env=taskspec.spec.env or {},
        working_dir=taskspec.spec.working_dir,
        environment_profile_ref=taskspec.spec.runner.environment_profile_ref,
        bundle_root=bundle_root,
        tid="123",
    )

    assert result.working_dir is None
    assert result.env == {}
    assert result.runner_options == {
        "container_workdir": "/tmp",
        "mount_workdir": False,
        "work_item_mounts": [
            {
                "source_path_ref": "metadata.document_path",
                "target": "/tmp/00-Overview_and_Architecture.md",
                "read_only": True,
                "required": False,
                "kind": "file",
            }
        ],
    }


def test_dockerized_agent_run_input_builds_mounted_document_work_item() -> None:
    taskspec = _load_builtin_taskspec()
    run_input = taskspec.spec.run_input
    assert run_input is not None

    payload = invoke_run_input_adapter(
        run_input.adapter_ref,
        request=SpecRunInputRequest(
            arguments={
                "prompt": "Summarize this document",
                "document": str(Path("/tmp/example.md")),
            },
            stdin_text=None,
            context_root="/tmp",
            spec_name=taskspec.name,
        ),
        bundle_root=taskspec.get_bundle_root(),
    )

    assert payload == {
        "template": "explain_mounted",
        "template_args": {
            "prompt": "Summarize this document",
            "document_target_path": "/tmp/00-Overview_and_Architecture.md",
        },
        "metadata": {"document_path": "/tmp/example.md"},
    }


def test_dockerized_agent_run_input_builds_inline_document_work_item() -> None:
    taskspec = _load_builtin_taskspec()
    run_input = taskspec.spec.run_input
    assert run_input is not None

    payload = invoke_run_input_adapter(
        run_input.adapter_ref,
        request=SpecRunInputRequest(
            arguments={"prompt": "Tell me the readability level"},
            stdin_text="document text",
            context_root="/tmp",
            spec_name=taskspec.name,
        ),
        bundle_root=taskspec.get_bundle_root(),
    )

    assert payload == {
        "template": "explain_inline",
        "template_args": {
            "prompt": "Tell me the readability level",
            "document_text": "document text",
        },
    }


def test_dockerized_agent_parameterization_materializes_selected_provider() -> None:
    taskspec = _load_builtin_taskspec()

    materialized = materialize_taskspec_template(
        taskspec,
        arguments={"provider": "gemini"},
        context_root="/tmp",
    )

    agent = materialized.spec.agent
    assert agent is not None
    assert materialized.name == "dockerized-agent-gemini"
    assert materialized.spec.parameterization is None
    assert agent.runtime_config["provider"] == "gemini"
    assert agent.model is None
    assert agent.resolved_authority_class == "general"


def test_dockerized_agent_parameterization_applies_model_override() -> None:
    taskspec = _load_builtin_taskspec()

    materialized = materialize_taskspec_template(
        taskspec,
        arguments={
            "provider": "opencode",
            "model": "openai/gpt-5.1",
        },
        context_root="/tmp",
    )

    agent = materialized.spec.agent
    assert agent is not None
    assert materialized.name == "dockerized-agent-opencode"
    assert agent.runtime_config["provider"] == "opencode"
    assert agent.model == "openai/gpt-5.1"


def test_dockerized_agent_parameterization_swaps_claude_environment_profile() -> None:
    taskspec = _load_builtin_taskspec()

    materialized = materialize_taskspec_template(
        taskspec,
        arguments={"provider": "claude_code"},
        context_root="/tmp",
    )

    assert (
        materialized.spec.runner.environment_profile_ref
        == "weft.builtins.dockerized_agent_examples:"
        "claude_code_dockerized_agent_environment_profile"
    )


def test_dockerized_agent_tool_profile_uses_outer_docker_boundary() -> None:
    taskspec = _load_builtin_taskspec()
    agent = taskspec.spec.agent
    assert agent is not None

    result = resolve_agent_tool_profile(
        agent,
        tid="123",
        bundle_root=taskspec.get_bundle_root(),
    )

    assert result.provider_options == {
        "sandbox": "danger-full-access",
        "skip_git_repo_check": True,
    }
    assert result.workspace_access is None
    assert result.metadata == {
        "builtin": "dockerized-agent",
        "provider": "codex",
        "tid": "123",
    }


def test_claude_code_environment_profile_injects_keychain_token_on_macos(
    tmp_path: Path,
) -> None:
    result = builtin_examples.claude_code_dockerized_agent_environment_profile(
        target_type="agent",
        runner_name="docker",
        runner_options={},
        env={},
        working_dir=None,
        tid="123",
        platform="darwin",
        home_dir=tmp_path,
        oauth_token_reader=lambda: "oauth-token",
    )

    assert result.env == {
        "IS_SANDBOX": "1",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-token",
    }
    assert result.metadata["auth_mode"] == "keychain_oauth_token"
    assert result.metadata["auth_env_injected"] is True


def test_claude_code_environment_profile_preserves_explicit_portable_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "host-api-key")

    result = builtin_examples.claude_code_dockerized_agent_environment_profile(
        target_type="agent",
        runner_name="docker",
        runner_options={},
        env={},
        working_dir=None,
        tid="123",
        platform="darwin",
        home_dir=tmp_path,
        oauth_token_reader=lambda: (_ for _ in ()).throw(
            AssertionError("keychain lookup should not run")
        ),
    )
    assert result.env == {"IS_SANDBOX": "1"}
    assert result.metadata["auth_mode"] == "explicit_env"
    assert result.metadata["auth_env_injected"] is False


def test_claude_code_environment_profile_defers_keychain_lookup_until_runtime(
    tmp_path: Path,
) -> None:
    result = builtin_examples.claude_code_dockerized_agent_environment_profile(
        target_type="agent",
        runner_name="docker",
        runner_options={},
        env={},
        working_dir=None,
        tid=None,
        platform="darwin",
        home_dir=tmp_path,
        oauth_token_reader=lambda: (_ for _ in ()).throw(
            AssertionError("keychain lookup should not run during validation")
        ),
    )

    assert result.env == {"IS_SANDBOX": "1"}
    assert result.metadata["auth_mode"] == "runtime_deferred"
    assert result.metadata["auth_env_injected"] is False


def test_claude_code_environment_profile_uses_existing_credentials_file(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / ".claude" / ".credentials.json"
    credentials_path.parent.mkdir(parents=True)
    credentials_path.write_text("{}", encoding="utf-8")

    result = builtin_examples.claude_code_dockerized_agent_environment_profile(
        target_type="agent",
        runner_name="docker",
        runner_options={},
        env={},
        working_dir=None,
        tid="123",
        platform="darwin",
        home_dir=tmp_path,
        oauth_token_reader=lambda: (_ for _ in ()).throw(
            AssertionError("keychain lookup should not run")
        ),
    )

    assert result.env == {"IS_SANDBOX": "1"}
    assert result.metadata["auth_mode"] == "mounted_credentials_file"
    assert result.metadata["auth_env_injected"] is False


def test_claude_code_environment_profile_raises_when_macos_keychain_lookup_fails(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="dockerized-agent could not acquire Claude Code runtime auth from macOS Keychain",
    ):
        builtin_examples.claude_code_dockerized_agent_environment_profile(
            target_type="agent",
            runner_name="docker",
            runner_options={},
            env={},
            working_dir=None,
            tid="123",
            platform="darwin",
            home_dir=tmp_path,
            oauth_token_reader=lambda: (_ for _ in ()).throw(
                ValueError("No matching Claude Code Keychain credential was found")
            ),
        )


def test_dockerized_agent_parameterization_covers_all_supported_providers() -> None:
    taskspec = _load_builtin_taskspec()
    expected: dict[str, dict[str, str | None]] = {
        "claude_code": {
            "name": "dockerized-agent-claude_code",
            "model": None,
            "environment_profile_ref": (
                "weft.builtins.dockerized_agent_examples:"
                "claude_code_dockerized_agent_environment_profile"
            ),
        },
        "codex": {
            "name": "dockerized-agent-codex",
            "model": None,
            "environment_profile_ref": (
                "dockerized_agent:dockerized_agent_environment_profile"
            ),
        },
        "gemini": {
            "name": "dockerized-agent-gemini",
            "model": None,
            "environment_profile_ref": (
                "dockerized_agent:dockerized_agent_environment_profile"
            ),
        },
        "opencode": {
            "name": "dockerized-agent-opencode",
            "model": "openai/gpt-5",
            "environment_profile_ref": (
                "dockerized_agent:dockerized_agent_environment_profile"
            ),
        },
        "qwen": {
            "name": "dockerized-agent-qwen",
            "model": None,
            "environment_profile_ref": (
                "dockerized_agent:dockerized_agent_environment_profile"
            ),
        },
    }

    for provider, config in expected.items():
        materialized = materialize_taskspec_template(
            taskspec,
            arguments={"provider": provider},
            context_root="/tmp",
        )

        agent = materialized.spec.agent
        assert agent is not None
        assert materialized.name == config["name"]
        assert agent.runtime_config["provider"] == provider
        assert agent.model == config["model"]
        assert agent.resolved_authority_class == "general"
        assert (
            materialized.spec.runner.environment_profile_ref
            == config["environment_profile_ref"]
        )

        run_input = materialized.spec.run_input
        assert run_input is not None
        payload = invoke_run_input_adapter(
            run_input.adapter_ref,
            request=SpecRunInputRequest(
                arguments={"prompt": "Explain this document"},
                stdin_text="document text",
                context_root="/tmp",
                spec_name=materialized.name,
            ),
            bundle_root=materialized.get_bundle_root(),
        )
        assert payload == {
            "template": "explain_inline",
            "template_args": {
                "prompt": "Explain this document",
                "document_text": "document text",
            },
        }
