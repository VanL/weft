"""Tests for the backend-neutral agent runtime surface."""

from __future__ import annotations

import json

import pytest

from weft.core.agent_runtime import (
    AgentExecutionResult,
    NormalizedAgentMessage,
    clear_agent_runtime_registry,
    execute_agent_target,
    get_agent_runtime,
    normalize_agent_work_item,
    register_agent_runtime,
)
from weft.core.taskspec import AgentSection


class EchoRuntime:
    """Small real runtime used to verify registry and execution plumbing."""

    def execute(self, *, agent, work_item, tools, tid):  # noqa: ANN001
        del tid
        return AgentExecutionResult(
            runtime=agent.runtime,
            model=agent.model,
            output_mode=agent.output_mode,
            outputs=(
                {
                    "content": work_item.content,
                    "metadata": work_item.metadata,
                    "tools": [tool.name for tool in tools],
                },
            ),
            metadata=work_item.metadata,
        )


@pytest.fixture(autouse=True)
def clear_runtime_registry() -> None:
    clear_agent_runtime_registry()


def make_agent_section(**overrides) -> AgentSection:
    payload = {"runtime": "echo", "instructions": "default instructions"}
    payload.update(overrides)
    return AgentSection.model_validate(payload)


def test_runtime_registry_registers_and_rejects_duplicates() -> None:
    runtime = EchoRuntime()
    register_agent_runtime("echo", runtime)

    assert get_agent_runtime("echo") is runtime
    with pytest.raises(ValueError, match="already registered"):
        register_agent_runtime("echo", EchoRuntime())


def test_runtime_registry_rejects_unknown_runtime() -> None:
    with pytest.raises(ValueError, match="Unknown agent runtime"):
        get_agent_runtime("missing")


def test_normalize_work_item_from_string() -> None:
    normalized = normalize_agent_work_item(
        make_agent_section(),
        "review this patch",
    )

    assert normalized.content == "review this patch"
    assert normalized.instructions == "default instructions"
    assert normalized.metadata == {}
    assert normalized.tool_allow is None
    assert normalized.tool_deny is None


def test_normalize_work_item_preserves_structured_messages() -> None:
    normalized = normalize_agent_work_item(
        make_agent_section(),
        {
            "messages": [
                "raw note",
                {"role": "system", "content": "Be brief"},
                {"role": "user", "content": {"path": "README.md"}},
            ]
        },
    )

    assert normalized.content == (
        "raw note",
        NormalizedAgentMessage(role="system", content="Be brief"),
        NormalizedAgentMessage(role="user", content={"path": "README.md"}),
    )


def test_normalize_work_item_requires_exactly_one_content_field() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        normalize_agent_work_item(
            make_agent_section(),
            {
                "task": "explicit task",
                "messages": [{"role": "user", "content": "ignored"}],
            },
        )


def test_normalize_work_item_renders_template_and_overrides_instructions() -> None:
    normalized = normalize_agent_work_item(
        make_agent_section(
            templates={
                "review": {
                    "instructions": "template instructions",
                    "prompt": "Patch:\n{{ patch }}",
                }
            }
        ),
        {
            "template": "review",
            "template_args": {"patch": "diff --git a b"},
        },
    )

    assert normalized.content == "Patch:\ndiff --git a b"
    assert normalized.instructions == "template instructions"


def test_normalize_work_item_rejects_unsupported_fields() -> None:
    with pytest.raises(ValueError, match="Unsupported agent work item field"):
        normalize_agent_work_item(
            make_agent_section(),
            {"task": "ok", "attachments": []},
        )


def test_normalize_work_item_rejects_extra_template_arguments() -> None:
    with pytest.raises(ValueError, match="Unexpected template argument"):
        normalize_agent_work_item(
            make_agent_section(
                templates={
                    "review": {
                        "prompt": "Patch:\n{{ patch }}",
                    }
                }
            ),
            {
                "template": "review",
                "template_args": {"patch": "diff", "extra": "nope"},
            },
        )


def test_normalize_work_item_rejects_message_without_role() -> None:
    with pytest.raises(TypeError, match="role"):
        normalize_agent_work_item(
            make_agent_section(),
            {
                "messages": [{"content": "no role"}],
            },
        )


def test_execute_agent_target_returns_internal_execution_result() -> None:
    register_agent_runtime("echo", EchoRuntime())
    result = execute_agent_target(
        AgentSection(
            runtime="echo",
            model="unit-test-model",
            tools=(
                {
                    "name": "echo_payload",
                    "kind": "python",
                    "ref": "tests.tasks.sample_targets:echo_payload",
                },
            ),
            output_mode="json",
        ),
        {
            "task": "run me",
            "metadata": {"request_id": "abc-123"},
            "tool_overrides": {"allow": ["echo_payload"]},
        },
        tid="123",
    )

    assert result.runtime == "echo"
    assert result.output_mode == "json"
    assert result.outputs == (
        {
            "content": "run me",
            "metadata": {"request_id": "abc-123"},
            "tools": ["echo_payload"],
        },
    )
    assert result.aggregate_public_output() == {
        "content": "run me",
        "metadata": {"request_id": "abc-123"},
        "tools": ["echo_payload"],
    }
    assert (
        json.loads(json.dumps(result.to_private_payload()))
        == result.to_private_payload()
    )
