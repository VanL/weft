"""Tests for the local live-provider pytest helper script."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.shared


def _load_live_provider_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "bin" / "pytest-live-providers"
    loader = importlib.machinery.SourceFileLoader(
        "weft_pytest_live_providers_script",
        str(script_path),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError(
            f"Unable to load pytest-live-providers script: {script_path}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


def test_build_pytest_command_selects_exact_live_cases() -> None:
    """The runner should execute slow live cases serially and fail fast."""

    live_providers = _load_live_provider_module()

    command = live_providers.build_pytest_command(("claude_code", "codex"))

    assert command == (
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "",
        "-n",
        "0",
        "-x",
        "--maxfail=1",
        "--override-ini=addopts=-ra -q --strict-markers",
        "tests/tasks/test_agent_execution.py::test_consumer_live_provider_cli_smoke[claude_code]",
        "tests/tasks/test_agent_execution.py::test_consumer_live_provider_cli_smoke[codex]",
        "tests/tasks/test_agent_execution.py::test_consumer_live_provider_cli_persistent_smoke[claude_code]",
        "tests/tasks/test_agent_execution.py::test_consumer_live_provider_cli_persistent_smoke[codex]",
        "tests/tasks/test_agent_execution.py::test_consumer_live_provider_cli_mcp_smoke",
    )


def test_build_pytest_command_omits_mcp_without_claude_code() -> None:
    """The Claude-only MCP smoke should not run for another target set."""

    live_providers = _load_live_provider_module()

    command = live_providers.build_pytest_command(("codex",))

    assert all("mcp_smoke" not in argument for argument in command)


def test_resolve_live_provider_targets_discovers_registered_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-discovery should select registered default executables on PATH."""

    live_providers = _load_live_provider_module()
    executable_paths = {
        "claude": "/tools/claude",
        "gemini": "/tools/gemini",
    }
    monkeypatch.setattr(
        live_providers.shutil,
        "which",
        executable_paths.get,
    )

    targets = live_providers.resolve_live_provider_targets(None)

    assert targets == ("claude_code", "gemini")


def test_resolve_live_provider_targets_rejects_unknown_explicit_target() -> None:
    """An explicit typo should fail instead of silently selecting nothing."""

    live_providers = _load_live_provider_module()

    with pytest.raises(ValueError, match="Unknown live provider target: typo"):
        live_providers.resolve_live_provider_targets("codex,typo")


def test_resolve_live_provider_targets_rejects_unavailable_explicit_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit target must resolve instead of degrading to a skip."""

    live_providers = _load_live_provider_module()
    monkeypatch.setattr(live_providers.shutil, "which", lambda _name: None)

    with pytest.raises(ValueError, match="codex"):
        live_providers.resolve_live_provider_targets("codex")


def test_resolve_live_provider_targets_deduplicates_in_registry_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated or reordered input must not duplicate paid provider calls."""

    live_providers = _load_live_provider_module()
    monkeypatch.setattr(
        live_providers.shutil,
        "which",
        lambda name: f"/tools/{name}",
    )

    targets = live_providers.resolve_live_provider_targets("qwen,codex,qwen")

    assert targets == ("codex", "qwen")


def test_main_runs_exact_live_suite_with_isolated_pytest_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entry point should prevent inherited options from changing coverage."""

    live_providers = _load_live_provider_module()
    recorded: dict[str, object] = {}

    class _CompletedProcess:
        returncode = 7

    def _fake_run(command: tuple[str, ...], **kwargs: object) -> _CompletedProcess:
        recorded["command"] = command
        recorded.update(kwargs)
        return _CompletedProcess()

    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    monkeypatch.setattr(
        live_providers,
        "resolve_live_provider_targets",
        lambda _raw_targets: ("claude_code", "codex"),
    )
    monkeypatch.setattr(live_providers.subprocess, "run", _fake_run)

    exit_code = live_providers.main()

    assert exit_code == 7
    assert recorded["command"] == live_providers.build_pytest_command(("claude_code",))
    assert recorded["cwd"] == live_providers.ROOT
    assert recorded["check"] is False
    child_env = recorded["env"]
    assert isinstance(child_env, dict)
    assert child_env["PYTEST_ADDOPTS"] == ""
    assert child_env["WEFT_RUN_LIVE_PROVIDER_CLI_TESTS"] == "1"
    assert child_env["WEFT_RUN_LIVE_PROVIDER_CLI_MCP_TESTS"] == "1"
    assert child_env["WEFT_LIVE_PROVIDER_CLI_TARGETS"] == "claude_code"


def test_build_provider_env_isolates_qwen_openrouter_authentication() -> None:
    """Qwen's OpenRouter compatibility variables must stay provider-scoped."""

    live_providers = _load_live_provider_module()
    base_env = {
        "OPENAI_API_KEY": "openai-key",
        "OPENROUTER_API_KEY": "openrouter-key",
        "PYTEST_ADDOPTS": "--collect-only",
    }

    qwen_env = live_providers.build_provider_env("qwen", base_env=base_env)
    codex_env = live_providers.build_provider_env("codex", base_env=base_env)

    assert qwen_env["QWEN_DEFAULT_AUTH_TYPE"] == "openai"
    assert qwen_env["OPENAI_API_KEY"] == "openrouter-key"
    assert qwen_env["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert qwen_env["WEFT_LIVE_PROVIDER_CLI_MODEL_QWEN"] == "poolside/laguna-s-2.1:free"
    assert qwen_env["WEFT_LIVE_PROVIDER_CLI_TARGETS"] == "qwen"
    assert qwen_env["WEFT_RUN_LIVE_PROVIDER_CLI_MCP_TESTS"] == "0"
    assert codex_env["OPENAI_API_KEY"] == "openai-key"
    assert "OPENAI_BASE_URL" not in codex_env


def test_build_provider_env_preserves_explicit_qwen_model() -> None:
    """A model override should reuse provider-scoped OpenRouter auth unchanged."""

    live_providers = _load_live_provider_module()
    qwen_env = live_providers.build_provider_env(
        "qwen",
        base_env={
            "OPENROUTER_API_KEY": "openrouter-key",
            "WEFT_LIVE_PROVIDER_CLI_MODEL_QWEN": "operator/model",
        },
    )

    assert qwen_env["WEFT_LIVE_PROVIDER_CLI_MODEL_QWEN"] == "operator/model"
    assert qwen_env["QWEN_DEFAULT_AUTH_TYPE"] == "openai"
    assert qwen_env["OPENAI_API_KEY"] == "openrouter-key"


def test_build_provider_env_preserves_explicit_qwen_authentication() -> None:
    """An operator-selected Qwen endpoint must take priority over OpenRouter."""

    live_providers = _load_live_provider_module()
    qwen_env = live_providers.build_provider_env(
        "qwen",
        base_env={
            "OPENROUTER_API_KEY": "openrouter-key",
            "QWEN_DEFAULT_AUTH_TYPE": "openai",
            "OPENAI_API_KEY": "operator-key",
            "OPENAI_BASE_URL": "https://operator.invalid/v1",
            "WEFT_LIVE_PROVIDER_CLI_MODEL_QWEN": "operator/model",
        },
    )

    assert qwen_env["OPENAI_API_KEY"] == "operator-key"
    assert qwen_env["OPENAI_BASE_URL"] == "https://operator.invalid/v1"
    assert qwen_env["WEFT_LIVE_PROVIDER_CLI_MODEL_QWEN"] == "operator/model"


def test_main_runs_each_provider_in_its_own_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful provider must not share credentials with the next provider."""

    live_providers = _load_live_provider_module()
    recorded: list[tuple[tuple[str, ...], dict[str, str]]] = []

    class _CompletedProcess:
        returncode = 0

    def _fake_run(command: tuple[str, ...], **kwargs: object) -> _CompletedProcess:
        child_env = kwargs["env"]
        assert isinstance(child_env, dict)
        recorded.append((command, child_env))
        return _CompletedProcess()

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setattr(
        live_providers,
        "resolve_live_provider_targets",
        lambda _raw_targets: ("codex", "qwen"),
    )
    monkeypatch.setattr(live_providers.subprocess, "run", _fake_run)

    assert live_providers.main() == 0
    assert [command for command, _env in recorded] == [
        live_providers.build_pytest_command(("codex",)),
        live_providers.build_pytest_command(("qwen",)),
    ]
    assert recorded[0][1]["WEFT_LIVE_PROVIDER_CLI_TARGETS"] == "codex"
    assert "QWEN_DEFAULT_AUTH_TYPE" not in recorded[0][1]
    assert recorded[1][1]["WEFT_LIVE_PROVIDER_CLI_TARGETS"] == "qwen"
    assert recorded[1][1]["QWEN_DEFAULT_AUTH_TYPE"] == "openai"


def test_main_fails_when_auto_discovery_finds_no_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Zero runnable providers must not produce a green release precheck."""

    live_providers = _load_live_provider_module()
    monkeypatch.setattr(
        live_providers,
        "resolve_live_provider_targets",
        lambda _raw_targets: (),
    )
    monkeypatch.setattr(
        live_providers.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("pytest must not run"),
    )

    exit_code = live_providers.main()

    assert exit_code == 1
    assert (
        "No registered provider_cli executable is available" in capsys.readouterr().err
    )


def test_entry_point_rejects_unknown_target_without_traceback() -> None:
    """Invalid target input should return a clean invocation error."""

    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["WEFT_LIVE_PROVIDER_CLI_TARGETS"] = "typo"

    completed = subprocess.run(
        (sys.executable, "bin/pytest-live-providers"),
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "Unknown live provider target: typo" in completed.stderr
    assert "Traceback" not in completed.stderr
