"""Tests for persistent provider_cli session behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.provider_cli_fixture import (
    PROVIDER_FIXTURE_NAMES,
    write_provider_cli_wrapper,
)
from weft.core.agents import register_builtin_agent_runtimes
from weft.core.agents.runtime import (
    clear_agent_runtime_registry,
    normalize_agent_work_item,
    start_agent_runtime_session,
)
from weft.core.taskspec import AgentSection

_MODEL_PROVIDERS = frozenset({"claude_code", "codex", "gemini", "opencode", "qwen"})


@pytest.fixture(autouse=True)
def reset_runtime_registry() -> None:
    clear_agent_runtime_registry()
    register_builtin_agent_runtimes()


def make_agent_section(
    *,
    executable: str,
    provider_name: str = "codex",
    model: str | None = None,
    **overrides,
) -> AgentSection:
    payload = {
        "runtime": "provider_cli",
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
    tmp_path,
    provider_name: str,
) -> None:
    executable = str(write_provider_cli_wrapper(tmp_path, provider_name))
    model = "fixture-model" if provider_name in _MODEL_PROVIDERS else None
    agent = make_agent_section(
        executable=executable,
        provider_name=provider_name,
        model=model,
    )

    session = start_agent_runtime_session(agent, tid="123")
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


def test_provider_cli_session_close_cleans_up_tempdir_after_failure(
    tmp_path,
) -> None:
    executable = str(write_provider_cli_wrapper(tmp_path, "codex"))
    session = start_agent_runtime_session(
        make_agent_section(
            executable=executable,
            provider_name="codex",
        ),
        tid="123",
    )
    tempdir = Path(session._tempdir.name)  # noqa: SLF001 - lifecycle assertion

    try:
        with pytest.raises(RuntimeError, match="fixture exec failed"):
            session.execute(
                normalize_agent_work_item(
                    session._agent,  # noqa: SLF001 - fixture-backed lifecycle assertion
                    {"task": "fail:fixture exec failed"},
                )
            )
    finally:
        session.close()

    assert not tempdir.exists()


def test_provider_cli_session_execute_rejects_opencode_without_run_support(
    tmp_path,
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
    try:
        with pytest.raises(RuntimeError, match="does not support 'run'"):
            session.execute(
                normalize_agent_work_item(session._agent, {"task": "hello"})
            )  # noqa: SLF001 - fixture-backed error assertion
    finally:
        session.close()
