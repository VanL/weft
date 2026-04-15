"""Tests for the built-in llm agent runtime backend."""

from __future__ import annotations

import llm
import pytest

from tests.fixtures.llm_test_models import TEST_MODEL_ID, register_test_model_plugin
from weft.core.agents import register_builtin_agent_runtimes
from weft.core.agents.runtime import clear_agent_runtime_registry, execute_agent_target
from weft.core.taskspec import AgentSection


@pytest.fixture(autouse=True)
def reset_runtime_registry() -> None:
    clear_agent_runtime_registry()
    register_builtin_agent_runtimes()


def make_agent_section(**overrides) -> AgentSection:
    payload = {
        "runtime": "llm",
        "model": TEST_MODEL_ID,
        "runtime_config": {"plugin_modules": ["tests.fixtures.llm_test_models"]},
    }
    payload.update(overrides)
    return AgentSection.model_validate(payload)


def test_llm_runtime_registry_is_registered() -> None:
    result = execute_agent_target(make_agent_section(), "hello", tid="123")

    assert result.runtime == "llm"
    assert result.model == TEST_MODEL_ID


def test_llm_model_prompt_surface_is_available() -> None:
    register_test_model_plugin()
    model = llm.get_model(TEST_MODEL_ID)

    response = model.prompt("hello", system="be brief")

    assert isinstance(response.prompt, llm.Prompt)
    assert response.prompt.prompt == "hello"
    assert response.prompt.system == "be brief"
    assert response.text() == "text:hello"


def test_llm_runtime_text_output() -> None:
    result = execute_agent_target(make_agent_section(), "hello")

    assert result.output_mode == "text"
    assert result.outputs == ("text:hello",)
    assert result.aggregate_public_output() == "text:hello"


def test_llm_runtime_json_output_with_schema() -> None:
    result = execute_agent_target(
        make_agent_section(
            output_mode="json",
            output_schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["summary", "status"],
            },
        ),
        "review me",
    )

    assert result.aggregate_public_output() == {"summary": "review me", "status": "ok"}


def test_llm_runtime_json_output_without_schema() -> None:
    result = execute_agent_target(
        make_agent_section(output_mode="json"),
        "json:review me",
    )

    assert result.aggregate_public_output() == {"task": "review me"}


def test_llm_runtime_messages_output() -> None:
    result = execute_agent_target(
        make_agent_section(output_mode="messages"),
        "hello",
    )

    assert result.outputs == ({"role": "assistant", "content": "text:hello"},)
    assert result.aggregate_public_output() == {
        "role": "assistant",
        "content": "text:hello",
    }


def test_llm_runtime_accepts_structured_messages_input() -> None:
    result = execute_agent_target(
        make_agent_section(),
        {
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hello"},
            ]
        },
    )

    assert result.aggregate_public_output() == "text:system: be brief\n\nuser: hello"


def test_llm_runtime_executes_python_tool() -> None:
    result = execute_agent_target(
        make_agent_section(
            tools=(
                {
                    "name": "echo_payload",
                    "kind": "python",
                    "ref": "tests.tasks.sample_targets:echo_payload",
                },
            )
        ),
        "tool_text:hello",
    )

    assert result.aggregate_public_output() == "hello"
    assert result.tool_trace == ({"name": "echo_payload", "status": "completed"},)


def test_llm_runtime_passes_instructions_options_and_tools() -> None:
    result = execute_agent_target(
        make_agent_section(
            output_mode="json",
            instructions="system instructions",
            options={"temperature": 0.2},
            tools=(
                {
                    "name": "echo_payload",
                    "kind": "python",
                    "ref": "tests.tasks.sample_targets:echo_payload",
                },
            ),
        ),
        {
            "task": "inspect_json:hello",
            "tool_overrides": {"allow": ["echo_payload"]},
        },
    )

    assert result.aggregate_public_output() == {
        "task": "hello",
        "system": "system instructions",
        "temperature": 0.2,
        "tools": ["echo_payload"],
        "history": [],
    }


def test_llm_runtime_template_overrides_instructions() -> None:
    result = execute_agent_target(
        make_agent_section(
            output_mode="json",
            instructions="base instructions",
            templates={
                "review": {
                    "instructions": "template instructions",
                    "prompt": "inspect_json:{{ payload }}",
                }
            },
        ),
        {
            "template": "review",
            "template_args": {"payload": "templated"},
        },
    )

    assert result.aggregate_public_output() == {
        "task": "templated",
        "system": "template instructions",
        "temperature": None,
        "tools": [],
        "history": [],
    }


def test_llm_runtime_enforces_max_turns() -> None:
    with pytest.raises(ValueError, match="Chain limit"):
        execute_agent_target(
            make_agent_section(
                max_turns=2,
                tools=(
                    {
                        "name": "echo_payload",
                        "kind": "python",
                        "ref": "tests.tasks.sample_targets:echo_payload",
                    },
                ),
            ),
            "tool_text:__loop__",
        )


def test_llm_runtime_tool_overrides_narrow_visible_tools() -> None:
    agent = make_agent_section(
        tools=(
            {
                "name": "echo_payload",
                "kind": "python",
                "ref": "tests.tasks.sample_targets:echo_payload",
            },
            {
                "name": "surround_payload",
                "kind": "python",
                "ref": "tests.tasks.sample_targets:surround_payload",
            },
        )
    )

    result = execute_agent_target(
        agent,
        {
            "task": 'tool_json: {"payload": "hello", "prefix": "<", "suffix": ">"}',
            "tool_overrides": {
                "allow": ["echo_payload", "surround_payload"],
                "deny": ["echo_payload"],
            },
        },
    )

    assert result.aggregate_public_output() == "<hello>"
