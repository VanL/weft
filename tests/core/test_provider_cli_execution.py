"""Tests for shared one-shot provider_cli preparation helpers."""

from __future__ import annotations

from tests.fixtures.provider_cli_fixture import provider_tool_profile
from weft.core.agents.provider_cli.execution import prepare_provider_cli_execution
from weft.core.agents.runtime import normalize_agent_work_item
from weft.core.taskspec import AgentSection


def test_prepare_provider_cli_execution_uses_supplied_cwd_and_tempdir(tmp_path) -> None:
    agent = AgentSection.model_validate(
        {
            "runtime": "provider_cli",
            "instructions": "base instructions",
            "runtime_config": {
                "provider": "codex",
                "resolver_ref": "tests.fixtures.provider_cli_fixture:resolve_operator_question",
                "tool_profile_ref": "tests.fixtures.provider_cli_fixture:provider_tool_profile",
            },
        }
    )
    work_item = normalize_agent_work_item(agent, "hello")
    cwd = "/container/workspace"
    tempdir = tmp_path / "runtime"
    tempdir.mkdir()

    prepared = prepare_provider_cli_execution(
        agent=agent,
        work_item=work_item,
        tid="123",
        executable="/usr/local/bin/codex",
        cwd=cwd,
        tempdir=tempdir,
    )

    assert prepared.provider.name == "codex"
    assert prepared.executable == "/usr/local/bin/codex"
    assert prepared.invocation.command[0] == "/usr/local/bin/codex"
    assert "-C" in prepared.invocation.command
    assert cwd in prepared.invocation.command
    assert prepared.invocation.output_path == tempdir / "last-message.txt"
    assert prepared.provider_options["sandbox"] == "read-only"
    assert (
        prepared.tool_profile.instructions
        == provider_tool_profile(agent=agent, tid="123").instructions
    )
