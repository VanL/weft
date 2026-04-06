"""TaskSpec behaviour tests aligned with the v1 specification."""

from __future__ import annotations

import json

import pytest

from weft.core.taskspec import (
    AgentSection,
    IOSection,
    ReservedPolicy,
    SpecSection,
    StateSection,
    TaskSpec,
    validate_taskspec,
)

from . import fixtures


class TestCreationDefaults:
    """Ensure defaults and automatic expansion follow the spec."""

    def test_minimal_taskspec_defaults(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()

        assert taskspec.tid == fixtures.VALID_TEST_TID
        assert taskspec.version == "1.0"
        assert taskspec.state.status == "created"
        assert taskspec.spec.stream_output is False
        assert taskspec.spec.cleanup_on_exit is True
        assert taskspec.spec.polling_interval == 1.0
        assert taskspec.spec.reporting_interval == "transition"
        assert taskspec.spec.enable_process_title is True
        assert taskspec.spec.output_size_limit_mb == 10
        assert taskspec.spec.reserved_policy_on_stop is ReservedPolicy.KEEP
        assert taskspec.spec.reserved_policy_on_error is ReservedPolicy.KEEP
        assert taskspec.io.outputs["outbox"] == f"T{taskspec.tid}.outbox"
        assert taskspec.io.control["ctrl_in"] == f"T{taskspec.tid}.ctrl_in"
        assert taskspec.io.control["ctrl_out"] == f"T{taskspec.tid}.ctrl_out"

    def test_apply_defaults_idempotent(self) -> None:
        taskspec = fixtures.create_valid_function_taskspec()
        initial_state = taskspec.model_dump()
        taskspec.apply_defaults()
        after_state = taskspec.model_dump()
        assert after_state == initial_state

    def test_agent_taskspec_defaults(self) -> None:
        taskspec = fixtures.create_valid_agent_taskspec()

        assert taskspec.spec.type == "agent"
        assert taskspec.spec.persistent is False
        assert taskspec.spec.agent is not None
        assert taskspec.spec.agent.output_mode == "text"
        assert taskspec.spec.agent.max_turns == 20
        assert taskspec.spec.agent.options == {}


class TestValidation:
    """Validate key schema constraints."""

    def test_invalid_tid_format(self) -> None:
        with pytest.raises(ValueError):
            fixtures.create_minimal_taskspec(tid="not-a-timestamp")

    def test_missing_required_queue(self) -> None:
        with pytest.raises(ValueError):
            TaskSpec(
                tid=fixtures.VALID_TEST_TID,
                name="missing-ctrl",
                spec=SpecSection(type="function", function_target="pkg:fn"),
                io=IOSection(outputs={"outbox": "T.foo"}, control={}),
            )


class TestTemplates:
    """Template TaskSpecs allow missing tids and skip auto-expansion."""

    def test_template_allows_missing_tid(self) -> None:
        template = TaskSpec.model_validate(
            {
                "name": "template-task",
                "spec": {"type": "function", "function_target": "pkg:fn"},
                "io": {},
                "state": {},
                "metadata": {},
            },
            context={"template": True, "auto_expand": False},
        )

        assert template.tid is None
        assert template.io.outputs == {}
        assert template.io.control == {}
        assert template.io.inputs == {}

    def test_template_apply_defaults_raises(self) -> None:
        template = TaskSpec.model_validate(
            {
                "name": "template-task",
                "spec": {"type": "function", "function_target": "pkg:fn"},
                "io": {},
                "state": {},
                "metadata": {},
            },
            context={"template": True, "auto_expand": False},
        )
        with pytest.raises(ValueError):
            template.apply_defaults()

    def test_agent_template_is_valid_for_validate_taskspec(self) -> None:
        agent_template = {
            "name": "template-agent",
            "spec": {
                "type": "agent",
                "agent": {
                    "runtime": "llm",
                    "model": "test-model",
                },
            },
            "io": {},
            "state": {},
            "metadata": {},
        }

        is_valid, errors = validate_taskspec(json.dumps(agent_template))
        assert is_valid is True
        assert errors == {}


class TestPartialImmutability:
    """Spec and IO sections become immutable after creation."""

    def test_spec_assignment_blocked(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()
        with pytest.raises(AttributeError):
            taskspec.spec.timeout = 5.0

    def test_io_assignment_blocked(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()
        with pytest.raises(AttributeError):
            taskspec.io.outputs = {}

    def test_agent_assignment_blocked(self) -> None:
        taskspec = fixtures.create_valid_agent_taskspec()
        assert taskspec.spec.agent is not None
        with pytest.raises(AttributeError):
            taskspec.spec.agent.instructions = "changed"

    def test_resolved_spec_args_reject_append(self) -> None:
        taskspec = fixtures.create_valid_function_taskspec()
        original_args = list(taskspec.spec.args)

        with pytest.raises((TypeError, AttributeError)):
            taskspec.spec.args.append(4)

        assert list(taskspec.spec.args) == original_args

    def test_resolved_spec_keyword_args_reject_item_assignment(self) -> None:
        taskspec = fixtures.create_valid_function_taskspec()
        original_kwargs = dict(taskspec.spec.keyword_args)

        with pytest.raises((TypeError, AttributeError)):
            taskspec.spec.keyword_args["extra"] = "value"

        assert dict(taskspec.spec.keyword_args) == original_kwargs

    def test_resolved_spec_env_rejects_update(self) -> None:
        taskspec = fixtures.create_valid_command_taskspec()
        assert taskspec.spec.env is not None
        original_env = dict(taskspec.spec.env)

        with pytest.raises((TypeError, AttributeError)):
            taskspec.spec.env.update({"NEW_VAR": "value"})

        assert dict(taskspec.spec.env) == original_env

    def test_resolved_io_rejects_nested_mutation(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()
        original_outputs = dict(taskspec.io.outputs)
        original_inputs = dict(taskspec.io.inputs)

        with pytest.raises((TypeError, AttributeError)):
            taskspec.io.outputs["outbox"] = "other.outbox"
        with pytest.raises((TypeError, AttributeError)):
            taskspec.io.inputs.setdefault("inbox", "other.inbox")

        assert dict(taskspec.io.outputs) == original_outputs
        assert dict(taskspec.io.inputs) == original_inputs

    def test_resolved_agent_config_rejects_nested_mapping_mutation(self) -> None:
        taskspec = TaskSpec.model_validate(
            {
                "tid": fixtures.VALID_TEST_TID,
                "name": "agent-nested-immutability",
                "spec": {
                    "type": "agent",
                    "agent": {
                        "runtime": "llm",
                        "model": "test-model",
                        "options": {"temperature": 0.7},
                        "runtime_config": {"plugin_modules": ["plugin.one"]},
                        "templates": {
                            "default": {
                                "prompt": "Hello {{name}}",
                                "instructions": "Be terse.",
                            }
                        },
                    },
                },
                "io": {
                    "inputs": {"inbox": f"T{fixtures.VALID_TEST_TID}.inbox"},
                    "outputs": {"outbox": f"T{fixtures.VALID_TEST_TID}.outbox"},
                    "control": {
                        "ctrl_in": f"T{fixtures.VALID_TEST_TID}.ctrl_in",
                        "ctrl_out": f"T{fixtures.VALID_TEST_TID}.ctrl_out",
                    },
                },
                "state": {},
                "metadata": {},
            },
            context={"auto_expand": True},
        )
        assert taskspec.spec.agent is not None

        with pytest.raises((TypeError, AttributeError)):
            taskspec.spec.agent.options["temperature"] = 0.1
        with pytest.raises((TypeError, AttributeError)):
            taskspec.spec.agent.runtime_config["plugin_modules"] = ["plugin.two"]
        with pytest.raises((TypeError, AttributeError)):
            taskspec.spec.agent.templates["default"] = {
                "prompt": "Changed",
            }


class TestStateTransitions:
    """mark_* helpers update state consistently."""

    def test_mark_running_updates_fields(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.mark_running(pid=123)
        assert taskspec.state.status == "running"
        assert taskspec.state.pid == 123
        assert taskspec.state.started_at is not None

    def test_mark_completed_sets_completed_at(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.mark_running()
        taskspec.mark_completed(return_code=0)
        assert taskspec.state.status == "completed"
        assert taskspec.state.completed_at is not None
        assert taskspec.state.return_code == 0

    def test_agent_output_schema_rejected_for_text_mode(self) -> None:
        with pytest.raises(ValueError):
            AgentSection(
                runtime="llm",
                output_mode="text",
                output_schema={"type": "object"},
            )


class TestMetadataHelpers:
    """Metadata section remains mutable with helpers."""

    def test_update_and_get_metadata(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.update_metadata({"owner": "agent"})
        assert taskspec.get_metadata("owner") == "agent"
        taskspec.set_metadata("priority", "high")
        assert taskspec.get_metadata("priority") == "high"

    def test_state_updates_remain_mutable(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.mark_running(pid=123)
        taskspec.update_metrics(time=1.5, memory=32.0, cpu=20)

        assert taskspec.state.status == "running"
        assert taskspec.state.pid == 123
        assert taskspec.state.time == 1.5
        assert taskspec.state.memory == 32.0
        assert taskspec.state.cpu == 20


class TestRuntimeReporting:
    """Runtime and logging convenience helpers."""

    def test_get_runtime_seconds(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()
        assert taskspec.get_runtime_seconds() is None
        taskspec.mark_running()
        runtime = taskspec.get_runtime_seconds()
        assert runtime is not None and runtime >= 0

    def test_should_report_modes(self) -> None:
        taskspec = fixtures.create_valid_function_taskspec()
        assert taskspec.should_report(last_status="created") is False
        taskspec.mark_running()
        assert taskspec.should_report(last_status="created") is True
        poll_spec = TaskSpec(
            tid=fixtures.VALID_TEST_TID,
            name="polling",
            spec=SpecSection(
                type="function",
                function_target="pkg:fn",
                reporting_interval="poll",
            ),
            io=IOSection(
                outputs={"outbox": f"T{fixtures.VALID_TEST_TID}.outbox"},
                control={
                    "ctrl_in": f"T{fixtures.VALID_TEST_TID}.ctrl_in",
                    "ctrl_out": f"T{fixtures.VALID_TEST_TID}.ctrl_out",
                },
            ),
            state=StateSection(),
        )
        assert poll_spec.should_report(last_status="created") is True

    def test_to_log_dict_contains_expected_fields(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()
        log_entry = taskspec.to_log_dict()
        for key in {"tid", "name", "status", "runtime_seconds", "metadata"}:
            assert key in log_entry
