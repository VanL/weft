"""Tests for persistent provider_cli session behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.provider_cli_fixture import (
    PROVIDER_FIXTURE_NAMES,
    TOOL_PROFILE_CALLS,
    reset_counting_tool_profile_calls,
    write_provider_cli_wrapper,
)
from weft.core.agents import register_builtin_agent_runtimes
from weft.core.agents.backends.provider_cli import ProviderCLIBackendSession
from weft.core.agents.runtime import (
    clear_agent_runtime_registry,
    normalize_agent_work_item,
    start_agent_runtime_session,
)
from weft.core.taskspec import AgentSection

_MODEL_PROVIDERS = frozenset({"agy", "claude_code", "codex", "opencode", "qwen"})


@pytest.fixture(autouse=True)
def reset_runtime_registry() -> None:
    clear_agent_runtime_registry()
    register_builtin_agent_runtimes()


def make_agent_section(
    *,
    executable: str,
    provider_name: str = "codex",
    model: str | None = None,
    **overrides: object,
) -> AgentSection:
    payload: dict[str, object] = {
        "runtime": "provider_cli",
        "authority_class": "general",
        "model": model,
        "instructions": "base instructions",
        "conversation_scope": "per_task",
        "runtime_config": {
            "provider": provider_name,
            "executable": executable,
            "resolver_ref": "tests.fixtures.provider_cli_fixture:resolve_operator_question",
            "tool_profile_ref": "tests.fixtures.provider_cli_fixture:provider_tool_profile",
        },
    }
    payload.update(overrides)
    return AgentSection.model_validate(payload)


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_provider_cli_session_continues_across_turns(
    tmp_path: Path,
    provider_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = str(write_provider_cli_wrapper(tmp_path, provider_name))
    model = "fixture-model" if provider_name in _MODEL_PROVIDERS else None
    monkeypatch.chdir(tmp_path)
    agent = make_agent_section(
        executable=executable,
        provider_name=provider_name,
        model=model,
    )

    session = start_agent_runtime_session(agent, tid="123")
    assert isinstance(session, ProviderCLIBackendSession)
    try:
        first = session.execute(
            normalize_agent_work_item(agent, {"task": "remember:phase2-token"})
        )
        second = session.execute(normalize_agent_work_item(agent, {"task": "recall"}))
    finally:
        session.close()

    first_payload = json.loads(first.aggregate_public_output())
    second_payload = json.loads(second.aggregate_public_output())

    assert first_payload["provider"] == provider_name
    assert first_payload["turn_index"] == 1
    assert second_payload["provider"] == provider_name
    assert second_payload["turn_index"] == 2
    assert second_payload["remembered"] == "phase2-token"
    assert second.metadata["session"]["turn_index"] == 2
    if provider_name in {"agy", "codex"}:
        assert (
            first.metadata["session"]["provider_session_id"]
            == second.metadata["session"]["provider_session_id"]
        )
        assert isinstance(
            first.metadata["session"]["provider_session_id"],
            str,
        )
    if provider_name == "codex":
        assert second_payload["cwd"] == str(tmp_path)
        assert second_payload["options"]["color"] == "never"
        assert second_payload["options"]["sandbox"] == "read-only"
        assert second_payload["options"]["skip_git_repo_check"] is True


def test_codex_concurrent_sessions_sharing_codex_home_preserve_isolation_and_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "shared-codex-home"
    codex_home.mkdir()
    work_a = tmp_path / "work-a"
    work_b = tmp_path / "work-b"
    work_a.mkdir()
    work_b.mkdir()
    executable = str(write_provider_cli_wrapper(tmp_path / "bin", "codex"))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    agent_a = make_agent_section(
        executable=executable,
        provider_name="codex",
        model="fixture-model",
        options={"profile": "alpha-profile", "color": "never"},
    )
    agent_b = make_agent_section(
        executable=executable,
        provider_name="codex",
        model="fixture-model",
        options={"profile": "beta-profile", "color": "always"},
    )

    monkeypatch.chdir(work_a)
    session_a = start_agent_runtime_session(agent_a, tid="101")
    monkeypatch.chdir(work_b)
    session_b = start_agent_runtime_session(agent_b, tid="102")
    try:
        first_a = session_a.execute(
            normalize_agent_work_item(agent_a, {"task": "remember:alpha-secret"})
        )
        first_b = session_b.execute(
            normalize_agent_work_item(agent_b, {"task": "remember:beta-secret"})
        )
        second_a = session_a.execute(
            normalize_agent_work_item(agent_a, {"task": "recall"})
        )
        second_b = session_b.execute(
            normalize_agent_work_item(agent_b, {"task": "recall"})
        )
    finally:
        session_a.close()
        session_b.close()

    assert (
        first_a.metadata["session"]["provider_session_id"]
        != first_b.metadata["session"]["provider_session_id"]
    )
    payload_a2 = json.loads(second_a.aggregate_public_output())
    payload_b2 = json.loads(second_b.aggregate_public_output())
    assert payload_a2["remembered"] == "alpha-secret"
    assert payload_b2["remembered"] == "beta-secret"
    assert payload_a2["cwd"] == str(work_a)
    assert payload_b2["cwd"] == str(work_b)
    assert payload_a2["options"]["profile"] == "alpha-profile"
    assert payload_b2["options"]["profile"] == "beta-profile"
    assert payload_a2["options"]["sandbox"] == "read-only"
    assert payload_b2["options"]["sandbox"] == "read-only"
    assert payload_a2["options"]["color"] == "never"
    assert payload_b2["options"]["color"] == "always"


def test_provider_cli_session_resolves_callable_tool_profile_once_per_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that session preparation evaluates a callable profile once per turn.

    Verifies:
    - `start_session` preflight resolves the tool profile exactly once
    - every subsequent turn resolves it exactly once more
    """

    reset_counting_tool_profile_calls()
    executable = str(write_provider_cli_wrapper(tmp_path, "codex"))
    monkeypatch.chdir(tmp_path)
    agent = make_agent_section(
        executable=executable,
        provider_name="codex",
        model="fixture-model",
        runtime_config={
            "provider": "codex",
            "executable": executable,
            "resolver_ref": (
                "tests.fixtures.provider_cli_fixture:resolve_operator_question"
            ),
            "tool_profile_ref": (
                "tests.fixtures.provider_cli_fixture:counting_provider_tool_profile"
            ),
        },
    )
    expected_call = {"provider": "codex", "tid": "123"}

    session = start_agent_runtime_session(agent, tid="123")
    assert isinstance(session, ProviderCLIBackendSession)
    try:
        assert TOOL_PROFILE_CALLS == [expected_call]
        session.execute(normalize_agent_work_item(agent, {"task": "first"}))
        assert TOOL_PROFILE_CALLS == [expected_call] * 2
        session.execute(normalize_agent_work_item(agent, {"task": "second"}))
    finally:
        session.close()

    assert TOOL_PROFILE_CALLS == [expected_call] * 3


def test_provider_cli_session_close_cleans_up_tempdir_after_failure(
    tmp_path: Path,
) -> None:
    executable = str(write_provider_cli_wrapper(tmp_path, "codex"))
    session = start_agent_runtime_session(
        make_agent_section(
            executable=executable,
            provider_name="codex",
        ),
        tid="123",
    )
    assert isinstance(session, ProviderCLIBackendSession)
    tempdir = Path(session._tempdir.name)  # Lifecycle assertion.

    try:
        with pytest.raises(RuntimeError, match="fixture exec failed"):
            session.execute(
                normalize_agent_work_item(
                    session._agent,  # Fixture-backed lifecycle assertion.
                    {"task": "fail:fixture exec failed"},
                )
            )
    finally:
        session.close()

    assert not tempdir.exists()


def test_provider_cli_session_reports_real_opencode_invocation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_CLI_FIXTURE_OPENCODE_NO_RUN", "1")
    executable = str(write_provider_cli_wrapper(tmp_path, "opencode"))

    session = start_agent_runtime_session(
        make_agent_section(
            executable=executable,
            provider_name="opencode",
            model="fixture-model",
        )
    )
    assert isinstance(session, ProviderCLIBackendSession)
    try:
        with pytest.raises(RuntimeError) as exc_info:
            session.execute(
                normalize_agent_work_item(
                    session._agent,  # Fixture-backed error assertion.
                    {"task": "hello"},
                )
            )
        assert "opencode execution failed" in str(exc_info.value)
        assert "unsupported opencode command" in str(exc_info.value)
    finally:
        session.close()
