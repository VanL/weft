"""Tests for provider container runtime descriptors and prep."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from weft.core.agents.provider_cli.container_runtime import (
    get_provider_container_runtime_descriptor,
    parse_provider_container_runtime_descriptor,
    resolve_provider_container_runtime,
)
from weft.core.agents.provider_cli.runtime_prep import (
    build_gemini_session_environment,
    prepare_provider_container_runtime,
)


def test_parse_provider_container_runtime_descriptor_rejects_runtime_dirs_without_home() -> (
    None
):
    with pytest.raises(ValueError, match="runtime_home is required"):
        parse_provider_container_runtime_descriptor(
            {
                "version": "v2",
                "provider": "gemini",
                "runtime_dirs": [".gemini/tmp"],
            },
            source="<test>",
        )


def test_get_provider_container_runtime_descriptor_loads_all_supported_providers() -> (
    None
):
    loaded = {
        name: get_provider_container_runtime_descriptor(name)
        for name in ("claude_code", "codex", "gemini", "opencode", "qwen")
    }

    assert all(descriptor is not None for descriptor in loaded.values())
    assert loaded["codex"].runtime_mounts[0].target == "/root/.codex"  # type: ignore[index]
    assert loaded["claude_code"].runtime_mounts[0].target == "/root/.claude"  # type: ignore[index]
    assert loaded["claude_code"].env[0].name == "ANTHROPIC_API_KEY"  # type: ignore[index]
    assert loaded["qwen"].runtime_mounts[0].target == "/root/.qwen"  # type: ignore[index]
    assert loaded["gemini"].runtime_home is not None  # type: ignore[union-attr]
    assert loaded["gemini"].env[0].name == "GEMINI_API_KEY"  # type: ignore[index]
    assert loaded["opencode"].env[0].name == "OPENAI_API_KEY"  # type: ignore[index]


def test_resolve_provider_container_runtime_does_not_forward_host_env_by_default() -> (
    None
):
    result = resolve_provider_container_runtime(
        "codex",
        task_env={},
        working_dir=None,
        host_env={"OPENAI_API_KEY": "test-key"},
        home_dir=Path("/missing-home"),
    )

    assert dict(result.env) == {}
    assert result.diagnostics == ()


def test_resolve_provider_container_runtime_adds_optional_writable_codex_mount(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()

    result = resolve_provider_container_runtime(
        "codex",
        task_env={},
        working_dir=None,
        home_dir=tmp_path,
        host_env={},
    )

    assert result.diagnostics == ()
    assert len(result.mounts) == 1
    assert result.mounts[0].source == codex_home.resolve()
    assert result.mounts[0].target == "/root/.codex"
    assert result.mounts[0].read_only is False


def test_resolve_provider_container_runtime_for_opencode_forwards_allowlisted_env_only() -> (
    None
):
    result = resolve_provider_container_runtime(
        "opencode",
        task_env={},
        working_dir=None,
        home_dir=Path("/missing-home"),
        host_env={
            "OPENAI_API_KEY": "openai-key",
            "OPENROUTER_API_KEY": "openrouter-key",
            "UNRELATED": "nope",
        },
    )

    assert dict(result.env) == {
        "OPENAI_API_KEY": "openai-key",
        "OPENROUTER_API_KEY": "openrouter-key",
    }
    assert result.mounts == ()
    assert result.diagnostics == ()


def test_resolve_provider_container_runtime_for_gemini_forwards_allowlisted_env_and_runtime_home() -> (
    None
):
    result = resolve_provider_container_runtime(
        "gemini",
        task_env={},
        working_dir=None,
        home_dir=Path("/missing-home"),
        host_env={
            "GEMINI_API_KEY": "gemini-key",
            "UNRELATED": "nope",
        },
    )

    assert dict(result.env) == {"GEMINI_API_KEY": "gemini-key"}
    assert result.runtime_home is not None
    assert result.runtime_home.target == "/tmp/weft-provider-home"
    assert result.diagnostics == ()


def test_resolve_provider_container_runtime_for_claude_code_forwards_portable_auth_env() -> (
    None
):
    result = resolve_provider_container_runtime(
        "claude_code",
        task_env={},
        working_dir=None,
        home_dir=Path("/missing-home"),
        host_env={
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-token",
            "UNRELATED": "nope",
        },
    )

    assert dict(result.env) == {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-token"}
    assert result.diagnostics == ()


def test_resolve_provider_container_runtime_for_gemini_collects_copied_files(
    tmp_path: Path,
) -> None:
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir()
    for name in ("google_accounts.json", "installation_id", "state.json"):
        (gemini_home / name).write_text(f"{name}\n", encoding="utf-8")

    result = resolve_provider_container_runtime(
        "gemini",
        task_env={},
        working_dir=None,
        home_dir=tmp_path,
        host_env={},
    )

    assert result.runtime_home is not None
    assert result.runtime_home.target == "/tmp/weft-provider-home"
    assert tuple(item.target_path for item in result.copied_files) == (
        ".gemini/google_accounts.json",
        ".gemini/installation_id",
        ".gemini/state.json",
    )
    assert result.runtime_dirs == (".gemini/history", ".gemini/tmp")
    assert result.diagnostics == ()


def test_prepare_provider_container_runtime_materializes_gemini_home(
    tmp_path: Path,
) -> None:
    gemini_home = tmp_path / "host-home" / ".gemini"
    gemini_home.mkdir(parents=True)
    (gemini_home / "google_accounts.json").write_text("accounts\n", encoding="utf-8")
    (gemini_home / "installation_id").write_text("install\n", encoding="utf-8")
    (gemini_home / "state.json").write_text("state\n", encoding="utf-8")
    (gemini_home / "settings.json").write_text(
        json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}}),
        encoding="utf-8",
    )

    resolution = resolve_provider_container_runtime(
        "gemini",
        task_env={},
        working_dir=None,
        home_dir=tmp_path / "host-home",
        host_env={},
    )
    prepared = prepare_provider_container_runtime(
        "gemini",
        resolution,
        temp_root=tmp_path / "container-prep",
        host_home_dir=tmp_path / "host-home",
    )

    assert dict(prepared.env) == {
        "HOME": "/tmp/weft-provider-home",
        "USERPROFILE": "/tmp/weft-provider-home",
    }
    assert len(prepared.mounts) == 1
    runtime_home = prepared.mounts[0].source
    assert prepared.mounts[0].target == "/tmp/weft-provider-home"
    assert (runtime_home / ".gemini" / "history").is_dir()
    assert (runtime_home / ".gemini" / "tmp").is_dir()
    assert (runtime_home / ".gemini" / "google_accounts.json").read_text(
        encoding="utf-8"
    ) == "accounts\n"
    settings = json.loads(
        (runtime_home / ".gemini" / "settings.json").read_text(encoding="utf-8")
    )
    assert settings["security"]["auth"]["selectedType"] == "oauth-personal"
    assert settings["admin"]["extensions"]["enabled"] is False


def test_build_gemini_session_environment_reuses_runtime_prep(tmp_path: Path) -> None:
    host_home = tmp_path / "home"
    gemini_home = host_home / ".gemini"
    gemini_home.mkdir(parents=True)
    (gemini_home / "state.json").write_text("state\n", encoding="utf-8")

    env = build_gemini_session_environment(
        tmp_path / "runtime",
        host_home_dir=host_home,
    )

    runtime_home = Path(env["HOME"])
    assert env["USERPROFILE"] == str(runtime_home)
    assert (runtime_home / ".gemini" / "state.json").read_text(encoding="utf-8") == (
        "state\n"
    )


def test_resolve_provider_container_runtime_respects_explicit_mount_target_override(
    tmp_path: Path,
) -> None:
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir()

    result = resolve_provider_container_runtime(
        "gemini",
        task_env={},
        working_dir=None,
        home_dir=tmp_path,
        host_env={},
        explicit_mounts=[
            {
                "source": str(tmp_path / "custom"),
                "target": "/tmp/weft-provider-home",
                "read_only": False,
            }
        ],
    )

    assert result.runtime_home is None
    assert result.copied_files == ()
    assert result.runtime_dirs == ()
