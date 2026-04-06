"""Spec checks for agent targets in TaskSpec (TS-1, AR-2.2)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tests.taskspec import fixtures
from weft.core.taskspec import SpecSection, TaskSpec, validate_taskspec


def test_agent_spec_requires_agent_section() -> None:
    with pytest.raises(ValidationError):
        SpecSection(type="agent")


def test_agent_spec_rejects_function_target() -> None:
    with pytest.raises(ValidationError):
        SpecSection(
            type="agent",
            function_target="tests.tasks.sample_targets:echo_payload",
            agent={"runtime": "llm"},
        )


def test_agent_spec_rejects_process_target() -> None:
    with pytest.raises(ValidationError):
        SpecSection(
            type="agent",
            process_target="echo",
            agent={"runtime": "llm"},
        )


def test_function_spec_rejects_agent_section() -> None:
    with pytest.raises(ValidationError):
        SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
            agent={"runtime": "llm"},
        )


def test_agent_tool_kind_rejects_unsupported_value() -> None:
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(
            {
                "tid": fixtures.VALID_TEST_TID,
                "name": "bad-tool-kind",
                "spec": {
                    "type": "agent",
                    "agent": {
                        "runtime": "llm",
                        "tools": [
                            {
                                "name": "bad",
                                "kind": "queue",
                                "ref": "tests.tasks.sample_targets:echo_payload",
                            }
                        ],
                    },
                },
                "io": {
                    "outputs": {"outbox": f"T{fixtures.VALID_TEST_TID}.outbox"},
                    "control": {
                        "ctrl_in": f"T{fixtures.VALID_TEST_TID}.ctrl_in",
                        "ctrl_out": f"T{fixtures.VALID_TEST_TID}.ctrl_out",
                    },
                },
                "state": {},
                "metadata": {},
            }
        )


def test_agent_spec_rejects_unknown_nested_keys() -> None:
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(
            {
                "tid": fixtures.VALID_TEST_TID,
                "name": "bad-agent-shape",
                "spec": {
                    "type": "agent",
                    "agent": {
                        "runtime": "llm",
                        "mode": "session",
                    },
                },
                "io": {
                    "outputs": {"outbox": f"T{fixtures.VALID_TEST_TID}.outbox"},
                    "control": {
                        "ctrl_in": f"T{fixtures.VALID_TEST_TID}.ctrl_in",
                        "ctrl_out": f"T{fixtures.VALID_TEST_TID}.ctrl_out",
                    },
                },
                "state": {},
                "metadata": {},
            }
        )


@pytest.mark.parametrize(
    "extra_field",
    [
        {"entrypoint": "tests.tasks.sample_targets:echo_payload"},
        {"approval_policy": "required"},
        {"persist_transcript": True},
        {"stream_events": True},
    ],
)
def test_agent_spec_rejects_removed_public_fields(
    extra_field: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(
            {
                "tid": fixtures.VALID_TEST_TID,
                "name": "removed-agent-field",
                "spec": {
                    "type": "agent",
                    "agent": {
                        "runtime": "llm",
                        **extra_field,
                    },
                },
                "io": {
                    "outputs": {"outbox": f"T{fixtures.VALID_TEST_TID}.outbox"},
                    "control": {
                        "ctrl_in": f"T{fixtures.VALID_TEST_TID}.ctrl_in",
                        "ctrl_out": f"T{fixtures.VALID_TEST_TID}.ctrl_out",
                    },
                },
                "state": {},
                "metadata": {},
            }
        )


@pytest.mark.parametrize("max_turns", [0, -1])
def test_agent_max_turns_must_be_positive(max_turns: int) -> None:
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(
            {
                "tid": fixtures.VALID_TEST_TID,
                "name": "bad-turns",
                "spec": {
                    "type": "agent",
                    "agent": {
                        "runtime": "llm",
                        "max_turns": max_turns,
                    },
                },
                "io": {
                    "outputs": {"outbox": f"T{fixtures.VALID_TEST_TID}.outbox"},
                    "control": {
                        "ctrl_in": f"T{fixtures.VALID_TEST_TID}.ctrl_in",
                        "ctrl_out": f"T{fixtures.VALID_TEST_TID}.ctrl_out",
                    },
                },
                "state": {},
                "metadata": {},
            }
        )


def test_agent_per_task_conversation_requires_persistent_task() -> None:
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(
            {
                "tid": fixtures.VALID_TEST_TID,
                "name": "bad-conversation-scope",
                "spec": {
                    "type": "agent",
                    "agent": {
                        "runtime": "llm",
                        "conversation_scope": "per_task",
                    },
                },
                "io": {
                    "outputs": {"outbox": f"T{fixtures.VALID_TEST_TID}.outbox"},
                    "control": {
                        "ctrl_in": f"T{fixtures.VALID_TEST_TID}.ctrl_in",
                        "ctrl_out": f"T{fixtures.VALID_TEST_TID}.ctrl_out",
                    },
                },
                "state": {},
                "metadata": {},
            }
        )


def test_agent_spec_rejects_interactive_mode() -> None:
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(
            {
                "tid": fixtures.VALID_TEST_TID,
                "name": "bad-agent-interactive",
                "spec": {
                    "type": "agent",
                    "interactive": True,
                    "agent": {
                        "runtime": "llm",
                    },
                },
                "io": {
                    "outputs": {"outbox": f"T{fixtures.VALID_TEST_TID}.outbox"},
                    "control": {
                        "ctrl_in": f"T{fixtures.VALID_TEST_TID}.ctrl_in",
                        "ctrl_out": f"T{fixtures.VALID_TEST_TID}.ctrl_out",
                    },
                },
                "state": {},
                "metadata": {},
            }
        )


def test_agent_template_section_validates() -> None:
    taskspec = TaskSpec.model_validate(
        {
            "tid": fixtures.VALID_TEST_TID,
            "name": "agent-with-template",
            "spec": {
                "type": "agent",
                "persistent": True,
                "agent": {
                    "runtime": "llm",
                    "conversation_scope": "per_task",
                    "templates": {
                        "review": {
                            "instructions": "be strict",
                            "prompt": "Patch:\n{{ patch }}",
                        }
                    },
                },
            },
            "io": {
                "outputs": {"outbox": f"T{fixtures.VALID_TEST_TID}.outbox"},
                "control": {
                    "ctrl_in": f"T{fixtures.VALID_TEST_TID}.ctrl_in",
                    "ctrl_out": f"T{fixtures.VALID_TEST_TID}.ctrl_out",
                },
            },
            "state": {},
            "metadata": {},
        }
    )

    assert taskspec.spec.agent is not None
    assert taskspec.spec.agent.templates["review"].prompt == "Patch:\n{{ patch }}"


def test_agent_output_mode_rejects_native() -> None:
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(
            {
                "tid": fixtures.VALID_TEST_TID,
                "name": "bad-output-mode",
                "spec": {
                    "type": "agent",
                    "agent": {
                        "runtime": "llm",
                        "output_mode": "native",
                    },
                },
                "io": {
                    "outputs": {"outbox": f"T{fixtures.VALID_TEST_TID}.outbox"},
                    "control": {
                        "ctrl_in": f"T{fixtures.VALID_TEST_TID}.ctrl_in",
                        "ctrl_out": f"T{fixtures.VALID_TEST_TID}.ctrl_out",
                    },
                },
                "state": {},
                "metadata": {},
            }
        )


def test_validate_taskspec_reports_agent_errors() -> None:
    valid, errors = validate_taskspec(
        json.dumps(
            {
                "name": "bad-agent-template",
                "spec": {
                    "type": "agent",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
                "io": {},
                "state": {},
                "metadata": {},
            }
        )
    )

    assert valid is False
    assert errors
