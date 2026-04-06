"""Tests for backend-neutral agent tool resolution."""

from __future__ import annotations

import pytest

from weft.core.agent_tools import resolve_agent_tools
from weft.core.taskspec import AgentToolSection


def test_resolve_python_tool_preserves_schema() -> None:
    tools = resolve_agent_tools(
        [
            AgentToolSection(
                name="echo_payload",
                kind="python",
                ref="tests.tasks.sample_targets:echo_payload",
            )
        ]
    )

    assert len(tools) == 1
    resolved = tools[0]
    assert resolved.name == "echo_payload"
    assert resolved.description == "Return the payload with an optional suffix."
    assert "payload" in resolved.input_schema["properties"]
    assert resolved.input_schema["required"] == ["payload"]


def test_allow_and_deny_overrides_preserve_spec_order() -> None:
    tools = [
        AgentToolSection(
            name="echo_payload",
            kind="python",
            ref="tests.tasks.sample_targets:echo_payload",
        ),
        AgentToolSection(
            name="provide_payload",
            kind="python",
            ref="tests.tasks.sample_targets:provide_payload",
        ),
    ]

    resolved = resolve_agent_tools(
        tools,
        allow=["provide_payload", "echo_payload"],
        deny=["provide_payload"],
    )

    assert [tool.name for tool in resolved] == ["echo_payload"]


def test_unknown_override_tool_name_fails() -> None:
    with pytest.raises(ValueError, match="Unknown tool name"):
        resolve_agent_tools(
            [
                AgentToolSection(
                    name="echo_payload",
                    kind="python",
                    ref="tests.tasks.sample_targets:echo_payload",
                )
            ],
            allow=["missing_tool"],
        )
