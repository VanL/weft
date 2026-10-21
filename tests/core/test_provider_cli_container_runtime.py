"""Tests for provider container runtime descriptors and prep."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft.core.agents.provider_cli import container_runtime as container_runtime_module
from weft.core.agents.provider_cli.container_runtime import (
    ProviderContainerRuntimeDescriptor,
    get_provider_container_runtime_descriptor,
    parse_provider_container_runtime_descriptor,
    resolve_provider_container_runtime,
)


def test_parse_provider_container_runtime_descriptor_rejects_runtime_dirs_without_home() -> (
    None
):
    with pytest.raises(ValueError, match="runtime_home is required"):
        parse_provider_container_runtime_descriptor(
            {
                "version": "v2",
                "provider": "fixture",
                "runtime_dirs": [".fixture/tmp"],
            },
            source="<test>",
        )


def test_get_provider_container_runtime_descriptor_loads_all_supported_providers() -> (
    None
):
    loaded: dict[str, ProviderContainerRuntimeDescriptor] = {}
    for name in ("claude_code", "codex", "opencode", "qwen"):
        descriptor = get_provider_container_runtime_descriptor(name)
        assert descriptor is not None
        loaded[name] = descriptor
    assert loaded["codex"].runtime_mounts[0].target == "/root/.codex"
    assert loaded["claude_code"].runtime_mounts[0].target == "/root/.claude"
    assert loaded["claude_code"].env[0].name == "ANTHROPIC_API_KEY"
    assert loaded["qwen"].runtime_mounts[0].target == "/root/.qwen"
    assert loaded["opencode"].env[0].name == "OPENAI_API_KEY"


def test_provider_container_descriptor_rejects_non_object_json_as_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResource:
        def joinpath(self, _name: str) -> FakeResource:
            return self

        def is_file(self) -> bool:
            return True

        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            return "[]"

        def __str__(self) -> str:
            return "<fake-descriptor>"

    monkeypatch.setattr(
        container_runtime_module.resources,
        "files",
        lambda _package: FakeResource(),
    )

    with pytest.raises(ValueError) as exc_info:
        get_provider_container_runtime_descriptor("test-non-object-descriptor")
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == (
        "Invalid provider container runtime descriptor <fake-descriptor>: "
        "root must be an object"
    )
    assert exc_info.value.__cause__ is None


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
