"""TaskSpec behaviour tests aligned with the v1 specification."""

from __future__ import annotations

import json

import pytest

from weft.core import taskspec as taskspec_pkg
from weft.core.taskspec import (
    AgentSection,
    IOSection,
    ReservedPolicy,
    SpecSection,
    StateSection,
    TaskSpec,
    validate_taskspec,
)
from weft.ext import AgentMCPServerDescriptor, AgentToolProfileResult

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
        assert taskspec.spec.runner.name == "host"
        assert taskspec.spec.runner.options == {}
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
        assert taskspec.spec.agent.resolved_authority_class == "bounded"


class TestValidation:
    """Validate key schema constraints."""

    def test_invalid_tid_format(self) -> None:
        with pytest.raises(ValueError):
            fixtures.create_minimal_taskspec(tid="not-a-timestamp")

    def test_validate_taskspec_does_not_swallow_programmer_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _raise_programmer_error(*args: object, **kwargs: object) -> None:
            raise RuntimeError("programmer error")

        monkeypatch.setattr(
            taskspec_pkg.TaskSpec,
            "model_validate_json",
            _raise_programmer_error,
        )

        with pytest.raises(RuntimeError, match="programmer error"):
            validate_taskspec("{}")

    def test_missing_required_queue(self) -> None:
        with pytest.raises(ValueError):
            TaskSpec(
                tid=fixtures.VALID_TEST_TID,
                name="missing-ctrl",
                spec=SpecSection(type="function", function_target="pkg:fn"),
                io=IOSection(outputs={"outbox": "T.foo"}, control={}),
            )

    def test_runner_options_must_be_json_serializable(self) -> None:
        with pytest.raises(ValueError):
            SpecSection(
                type="function",
                function_target="pkg:fn",
                runner={
                    "name": "host",
                    "options": {"bad": object()},
                },
            )

    def test_runner_environment_profile_ref_accepts_non_empty_string(self) -> None:
        spec = SpecSection(
            type="function",
            function_target="pkg:fn",
            runner={
                "name": "host",
                "environment_profile_ref": "tests.fixtures.runtime_profiles_fixture:host_environment_profile",
            },
        )

        assert spec.runner.environment_profile_ref == (
            "tests.fixtures.runtime_profiles_fixture:host_environment_profile"
        )

    def test_runner_environment_profile_ref_rejects_blank_string(self) -> None:
        with pytest.raises(ValueError, match="environment_profile_ref"):
            SpecSection(
                type="function",
                function_target="pkg:fn",
                runner={
                    "name": "host",
                    "environment_profile_ref": "   ",
                },
            )

    def test_run_input_accepts_valid_declared_arguments(self) -> None:
        spec = SpecSection(
            type="function",
            function_target="pkg:fn",
            run_input={
                "adapter_ref": "tests.tasks.sample_targets:echo_payload",
                "arguments": {
                    "prompt": {
                        "type": "string",
                        "required": True,
                    },
                    "document": {
                        "type": "path",
                    },
                },
                "stdin": {
                    "type": "text",
                },
            },
        )

        assert spec.run_input is not None
        assert spec.run_input.adapter_ref == "tests.tasks.sample_targets:echo_payload"
        assert sorted(spec.run_input.arguments) == ["document", "prompt"]
        assert spec.run_input.stdin is not None

    def test_run_input_rejects_reserved_option_name(self) -> None:
        with pytest.raises(ValueError, match="collides with reserved"):
            SpecSection(
                type="function",
                function_target="pkg:fn",
                run_input={
                    "adapter_ref": "tests.tasks.sample_targets:echo_payload",
                    "arguments": {
                        "spec": {
                            "type": "string",
                        }
                    },
                },
            )

    def test_run_input_rejects_unclean_long_option_name(self) -> None:
        with pytest.raises(ValueError, match="normalize cleanly"):
            SpecSection(
                type="function",
                function_target="pkg:fn",
                run_input={
                    "adapter_ref": "tests.tasks.sample_targets:echo_payload",
                    "arguments": {
                        "_prompt": {
                            "type": "string",
                        }
                    },
                },
            )

    def test_parameterization_accepts_valid_declared_arguments(self) -> None:
        spec = SpecSection(
            type="function",
            function_target="pkg:fn",
            parameterization={
                "adapter_ref": "tests.tasks.sample_targets:echo_payload",
                "arguments": {
                    "provider": {
                        "type": "string",
                        "default": "codex",
                        "choices": ["codex", "gemini"],
                    },
                    "model": {
                        "type": "string",
                    },
                },
            },
        )

        assert spec.parameterization is not None
        assert (
            spec.parameterization.adapter_ref
            == "tests.tasks.sample_targets:echo_payload"
        )
        assert sorted(spec.parameterization.arguments) == ["model", "provider"]
        assert spec.parameterization.arguments["provider"].default == "codex"

    def test_parameterization_rejects_default_outside_choices(self) -> None:
        with pytest.raises(
            ValueError, match="default must be one of the declared choices"
        ):
            SpecSection(
                type="function",
                function_target="pkg:fn",
                parameterization={
                    "adapter_ref": "tests.tasks.sample_targets:echo_payload",
                    "arguments": {
                        "provider": {
                            "type": "string",
                            "default": "codex",
                            "choices": ["gemini"],
                        }
                    },
                },
            )

    def test_parameterization_and_run_input_reject_colliding_option_names(self) -> None:
        with pytest.raises(ValueError, match="collide after '_' to '-' normalization"):
            SpecSection(
                type="function",
                function_target="pkg:fn",
                parameterization={
                    "adapter_ref": "tests.tasks.sample_targets:echo_payload",
                    "arguments": {
                        "provider": {
                            "type": "string",
                        }
                    },
                },
                run_input={
                    "adapter_ref": "tests.tasks.sample_targets:echo_payload",
                    "arguments": {
                        "provider": {
                            "type": "string",
                        }
                    },
                },
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

    def test_provider_cli_template_requires_provider(self) -> None:
        provider_cli_template = {
            "name": "template-provider-cli-agent",
            "spec": {
                "type": "agent",
                "agent": {
                    "runtime": "provider_cli",
                    "runtime_config": {
                        "provider": "codex",
                    },
                },
            },
            "io": {},
            "state": {},
            "metadata": {},
        }

        is_valid, errors = validate_taskspec(json.dumps(provider_cli_template))
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

    def test_runner_assignment_blocked(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()

        with pytest.raises(AttributeError):
            taskspec.spec.runner.name = "docker"

    def test_runner_options_reject_nested_mutation(self) -> None:
        taskspec = TaskSpec.model_validate(
            {
                "tid": fixtures.VALID_TEST_TID,
                "name": "runner-options",
                "spec": {
                    "type": "function",
                    "function_target": "pkg:fn",
                    "runner": {
                        "name": "docker",
                        "options": {"image": "busybox"},
                    },
                },
                "io": {},
                "state": {},
                "metadata": {},
            }
        )

        with pytest.raises((TypeError, AttributeError)):
            taskspec.spec.runner.options["image"] = "ubuntu"

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


class TestProviderCLIValidation:
    """Validation rules specific to the Phase 1 delegated runtime."""

    def test_provider_cli_allows_persistent_per_task_sessions(self) -> None:
        spec = SpecSection(
            type="agent",
            persistent=True,
            agent=AgentSection(
                runtime="provider_cli",
                conversation_scope="per_task",
                runtime_config={"provider": "codex"},
            ),
        )

        assert spec.persistent is True
        assert spec.agent is not None
        assert spec.agent.conversation_scope == "per_task"

    def test_provider_cli_defaults_authority_class_to_general_for_compatibility(
        self,
    ) -> None:
        agent = AgentSection(
            runtime="provider_cli",
            runtime_config={"provider": "codex"},
        )

        assert agent.authority_class is None
        assert agent.resolved_authority_class == "general"

    def test_provider_cli_accepts_explicit_bounded_authority_class(self) -> None:
        agent = AgentSection(
            runtime="provider_cli",
            authority_class="bounded",
            runtime_config={"provider": "codex"},
        )

        assert agent.resolved_authority_class == "bounded"

    def test_llm_rejects_general_authority_class(self) -> None:
        with pytest.raises(
            ValueError, match="llm only supports authority_class='bounded'"
        ):
            AgentSection(
                runtime="llm",
                authority_class="general",
                model="test-model",
            )

    def test_provider_cli_rejects_persistent_per_message_tasks(self) -> None:
        with pytest.raises(
            ValueError,
            match="provider_cli persistent tasks require conversation_scope='per_task'",
        ):
            SpecSection(
                type="agent",
                persistent=True,
                agent=AgentSection(
                    runtime="provider_cli",
                    conversation_scope="per_message",
                    runtime_config={"provider": "codex"},
                ),
            )

    def test_provider_cli_rejects_non_text_output_mode(self) -> None:
        with pytest.raises(ValueError, match="only supports output_mode='text'"):
            AgentSection(
                runtime="provider_cli",
                output_mode="json",
                runtime_config={"provider": "codex"},
            )

    def test_provider_cli_rejects_tools(self) -> None:
        with pytest.raises(ValueError, match="does not support spec.agent.tools"):
            AgentSection(
                runtime="provider_cli",
                tools=(
                    {
                        "name": "echo_payload",
                        "kind": "python",
                        "ref": "tests.tasks.sample_targets:echo_payload",
                    },
                ),
                runtime_config={"provider": "codex"},
            )

    def test_agent_tool_profile_result_rejects_invalid_workspace_access(self) -> None:
        with pytest.raises(ValueError, match="workspace_access"):
            AgentToolProfileResult(workspace_access="danger-full-access")

    def test_agent_tool_profile_result_rejects_invalid_mcp_server_descriptor(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="command must be non-empty"):
            AgentToolProfileResult(
                mcp_servers=(
                    AgentMCPServerDescriptor(
                        name="fixture",
                        command="   ",
                    ),
                )
            )

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
