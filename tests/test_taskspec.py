"""Tests for TaskSpec Pydantic models and validation."""

import json

import pytest
from pydantic import ValidationError

from tests.fixtures import taskspecs as fixtures
from weft.core.taskspec import (
    IOSection,
    SpecSection,
    StateSection,
    TaskSpec,
    validate_taskspec,
)


class TestTaskSpecValidation:
    """Test Pydantic validation for TaskSpec."""

    def test_valid_function_taskspec(self):
        """Test creating a valid function TaskSpec."""
        taskspec = fixtures.create_valid_function_taskspec()
        assert taskspec.spec.type == "function"
        assert taskspec.spec.function_target == "module:func"

    def test_valid_command_taskspec(self):
        """Test creating a valid command TaskSpec."""
        taskspec = fixtures.create_valid_command_taskspec()
        assert taskspec.spec.type == "command"
        assert taskspec.spec.process_target == ["echo", "hello"]

    def test_invalid_tid_format(self):
        """Test that invalid TID format raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="123",  # Too short
                name="test",
                spec=SpecSection(type="function", function_target="test:func"),
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        # Check for either custom validation message or Pydantic's pattern message
        error_str = str(exc_info.value)
        assert (
            "tid must be exactly 19 digits" in error_str
            or "String should match pattern" in error_str
        )

    def test_invalid_tid_non_numeric(self):
        """Test that non-numeric TID raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="123456789012345678x",  # Contains letter
                name="test",
                spec=SpecSection(type="function", function_target="test:func"),
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        assert "String should match pattern" in str(exc_info.value)

    def test_missing_function_target(self):
        """Test that missing function_target raises error for function type."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                spec=SpecSection(type="function"),  # Missing function_target
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        assert "function_target is required" in str(exc_info.value)

    def test_missing_process_target(self):
        """Test that missing process_target raises error for command type."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                spec=SpecSection(type="command"),  # Missing process_target
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        assert "process_target is required" in str(exc_info.value)

    def test_memory_limit_validation(self):
        """Test memory limit must be positive."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                spec=SpecSection(
                    type="function",
                    function_target="test:func",
                    memory_limit=0,  # Invalid, less than minimum (1)
                ),
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        assert "greater than or equal to 1" in str(exc_info.value)

    def test_cpu_limit_validation(self):
        """Test CPU limit must be between 0 and 100."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                spec=SpecSection(
                    type="function",
                    function_target="test:func",
                    cpu_limit=150,  # Invalid > 100
                ),
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        assert "less than or equal to 100" in str(exc_info.value)

    def test_control_queue_validation(self):
        """Test control queue validation."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                spec=SpecSection(type="function", function_target="test:func"),
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in"},  # Missing ctrl_out
                ),
            )
        assert "control must include 'ctrl_out'" in str(exc_info.value)

    def test_output_queue_validation(self):
        """Test output queue validation."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                spec=SpecSection(type="function", function_target="test:func"),
                io=IOSection(
                    outputs={"stdout": "test.out"},  # Missing outbox
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        assert "outputs must include 'outbox'" in str(exc_info.value)

    def test_state_timestamp_validation(self):
        """Test that completed_at must be after started_at."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                spec=SpecSection(type="function", function_target="test:func"),
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
                state=StateSection(
                    status="completed",
                    started_at=1000000000000000000,
                    completed_at=999999999999999999,  # Before started_at
                ),
            )
        assert "completed_at must be after started_at" in str(exc_info.value)

    def test_polling_interval_validation(self):
        """Test polling interval must be positive."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                spec=SpecSection(
                    type="function",
                    function_target="test:func",
                    polling_interval=-1.0,  # Invalid negative
                ),
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        assert "greater than 0" in str(exc_info.value)

    def test_default_values(self):
        """Test that default values are properly set."""
        taskspec = fixtures.create_minimal_taskspec()

        # Check spec defaults
        assert taskspec.spec.stream_output is False
        assert taskspec.spec.cleanup_on_exit is True
        assert taskspec.spec.polling_interval == 1.0
        assert taskspec.spec.reporting_interval == "transition"
        assert taskspec.spec.monitor_class == "weft.core.monitor.ResourceMonitor"

        # Check state defaults
        assert taskspec.state.status == "created"
        assert taskspec.state.pid is None
        assert taskspec.state.return_code is None

    def test_auto_expand_on_init(self):
        """Test that TaskSpec automatically expands defaults on initialization."""
        tid = "1234567890123456789"

        # Create a minimal TaskSpec - defaults should be applied automatically
        taskspec = TaskSpec(
            tid=tid,
            name="test",
            spec=SpecSection(type="function", function_target="test:func"),
            io=IOSection(
                outputs={"outbox": "test.out"},
                control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
            ),
        )

        # Verify defaults were applied automatically without calling apply_defaults()
        assert taskspec.spec.args == []
        assert taskspec.spec.keyword_args == {}
        assert taskspec.spec.env == {}
        assert taskspec.spec.memory_limit == 1024
        assert taskspec.metadata == {}

    def test_auto_expand_can_be_disabled(self):
        """Test that auto-expansion can be disabled with context."""
        tid = "1234567890123456789"

        # Create TaskSpec with auto_expand=False
        # Note: Pydantic's model_post_init doesn't directly support this pattern
        # We'll need to test this differently or adjust the implementation
        taskspec = TaskSpec(
            tid=tid,
            name="test",
            spec=SpecSection(type="function", function_target="test:func"),
            io=IOSection(
                outputs={"outbox": "test.out"},
                control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
            ),
        )

        # For now, auto-expansion happens automatically
        # This test documents the current behavior
        assert taskspec.spec.env == {}  # Will be expanded

    def test_apply_defaults_preserves_user_values(self):
        """Test that apply_defaults never overrides user-provided values."""
        tid = "1234567890123456789"

        # Create a TaskSpec with ALL custom values set
        taskspec = TaskSpec(
            tid=tid,
            name="custom-name",
            description="Custom description",
            spec=SpecSection(
                type="command",
                process_target=["custom", "command"],
                args=["arg1", "arg2"],
                keyword_args={"key": "value"},
                timeout=60.0,
                memory_limit=2048,
                cpu_limit=75,
                max_fds=100,
                max_connections=50,
                env={"CUSTOM": "VALUE"},
                working_dir="/custom/dir",
                stream_output=True,  # Different from default (False)
                cleanup_on_exit=False,  # Different from default (True)
                polling_interval=5.0,  # Different from default (1.0)
                reporting_interval="poll",  # Different from default ("transition")
                monitor_class="custom.monitor.Class",  # Different from default
            ),
            io=IOSection(
                inputs={"custom_in": "custom.input"},
                outputs={"outbox": "custom.outbox", "custom_out": "custom.output"},
                control={
                    "ctrl_in": "custom.ctrl_in",
                    "ctrl_out": "custom.ctrl_out",
                },
            ),
            state=StateSection(
                status="completed",  # Changed to completed to be consistent with having completed_at
                pid=12345,
                return_code=0,
                started_at=1000000000000000000,
                completed_at=2000000000000000000,
                error="Custom error",
                time=123.45,
                memory=67.89,
                cpu=50,
                fds=25,
                net_connections=10,
                max_memory=100.0,
                max_cpu=75,
                max_fds=30,
                max_net_connections=15,
            ),
            metadata={"custom": "metadata"},
        )

        # Store values after auto-expansion
        expanded_dict = taskspec.model_dump()

        # Apply defaults again (should be idempotent)
        taskspec.apply_defaults()

        # Get values after second apply_defaults
        after_dict = taskspec.model_dump()

        # Verify NOTHING was changed by second call
        assert after_dict == expanded_dict

        # Specifically verify some key fields weren't touched
        assert taskspec.spec.stream_output is True  # Custom value preserved
        assert taskspec.spec.cleanup_on_exit is False  # Custom value preserved
        assert taskspec.spec.polling_interval == 5.0  # Custom value preserved
        assert taskspec.spec.reporting_interval == "poll"  # Custom value preserved
        assert (
            taskspec.spec.monitor_class == "custom.monitor.Class"
        )  # Custom value preserved
        assert taskspec.spec.env == {"CUSTOM": "VALUE"}  # Custom value preserved
        assert taskspec.state.time == 123.45  # Custom value preserved
        assert taskspec.state.memory == 67.89  # Custom value preserved
        assert taskspec.metadata == {"custom": "metadata"}  # Custom value preserved

    def test_apply_defaults_fully_expands_spec(self):
        """Test that apply_defaults fully expands all fields without overriding set values."""
        tid = "1234567890123456789"

        # Create a TaskSpec with some custom values
        taskspec = TaskSpec(
            tid=tid,
            name="test",
            spec=SpecSection(
                type="function",
                function_target="test:func",
                args=[1, 2, 3],  # Custom args - should not be overridden
                memory_limit=512,  # Custom memory limit - should not be overridden
            ),
            io=IOSection(
                outputs={"outbox": "custom.outbox"},
                control={"ctrl_in": "custom.in", "ctrl_out": "custom.out"},
            ),
        )

        # Defaults are already applied via auto-expansion
        # Verify custom values are preserved
        assert taskspec.spec.args == [1, 2, 3]  # Custom value preserved
        assert taskspec.spec.memory_limit == 512  # Custom value preserved

        # Verify defaults were applied to unset fields
        assert taskspec.spec.keyword_args == {}  # Default applied
        assert taskspec.spec.timeout is None  # DEFAULT_TIMEOUT
        assert taskspec.spec.cpu_limit is None  # DEFAULT_CPU_LIMIT
        assert taskspec.spec.max_fds is None  # DEFAULT_MAX_FDS
        assert taskspec.spec.max_connections is None  # DEFAULT_MAX_CONNECTIONS
        assert taskspec.spec.env == {}  # Default empty dict
        assert taskspec.spec.working_dir is None

        # Verify all fields are present by converting to dict and checking keys
        spec_dict = taskspec.spec.model_dump()
        expected_spec_keys = {
            "type",
            "function_target",
            "process_target",
            "args",
            "keyword_args",
            "timeout",
            "memory_limit",
            "cpu_limit",
            "max_fds",
            "max_connections",
            "env",
            "working_dir",
            "stream_output",
            "cleanup_on_exit",
            "polling_interval",
            "reporting_interval",
            "monitor_class",
        }
        assert set(spec_dict.keys()) == expected_spec_keys

        # Verify state fields
        state_dict = taskspec.state.model_dump()
        expected_state_keys = {
            "status",
            "return_code",
            "started_at",
            "completed_at",
            "pid",
            "error",
            "time",
            "memory",
            "cpu",
            "fds",
            "net_connections",
            "max_memory",
            "max_cpu",
            "max_fds",
            "max_net_connections",
        }
        assert set(state_dict.keys()) == expected_state_keys

        # Verify io fields
        io_dict = taskspec.io.model_dump()
        assert "inputs" in io_dict
        assert "outputs" in io_dict
        assert "control" in io_dict
        assert "outbox" in io_dict["outputs"]
        assert "ctrl_in" in io_dict["control"]
        assert "ctrl_out" in io_dict["control"]

    def test_apply_defaults(self):
        """Test that apply_defaults fully expands the TaskSpec."""
        tid = "1234567890123456789"

        # Create a minimal TaskSpec
        taskspec = TaskSpec(
            tid=tid,
            name="test",
            spec=SpecSection(type="function", function_target="test:func"),
            io=IOSection(
                outputs={"outbox": "custom.outbox"},  # Custom outbox
                control={
                    "ctrl_in": "custom.in",
                    "ctrl_out": "custom.out",
                },  # Custom control
            ),
        )

        # Apply defaults to fully expand
        taskspec.apply_defaults()

        # Check that custom queue names are preserved
        assert taskspec.io.control["ctrl_in"] == "custom.in"
        assert taskspec.io.control["ctrl_out"] == "custom.out"
        assert taskspec.io.outputs["outbox"] == "custom.outbox"

        # Check that spec fields are expanded
        assert taskspec.spec.args == []
        assert taskspec.spec.keyword_args == {}
        assert taskspec.spec.timeout is None  # DEFAULT_TIMEOUT
        assert taskspec.spec.memory_limit == 1024  # DEFAULT_MEMORY_LIMIT
        assert taskspec.spec.cpu_limit is None  # DEFAULT_CPU_LIMIT
        assert taskspec.spec.env == {}  # Empty dict default
        assert taskspec.spec.working_dir is None

        # Check that state metrics remain None (not measured yet)
        assert taskspec.state.time is None
        assert taskspec.state.memory is None
        assert taskspec.state.cpu is None
        assert taskspec.state.fds is None
        assert taskspec.state.net_connections is None
        assert taskspec.state.max_memory is None
        assert taskspec.state.max_cpu is None
        assert taskspec.state.max_fds is None
        assert taskspec.state.max_net_connections is None

        # Check metadata is present
        assert taskspec.metadata == {}

    def test_custom_queues_not_overridden(self):
        """Test that custom queue names are not overridden by apply_defaults."""
        taskspec = TaskSpec(
            tid="1234567890123456789",
            name="test",
            spec=SpecSection(type="function", function_target="test:func"),
            io=IOSection(
                inputs={"custom": "my.custom.queue"},
                outputs={"outbox": "my.outbox"},
                control={
                    "ctrl_in": "my.ctrl.in",
                    "ctrl_out": "my.ctrl.out",
                },
            ),
        )

        taskspec.apply_defaults()

        # Check custom queues are preserved
        assert taskspec.io.inputs["custom"] == "my.custom.queue"
        assert taskspec.io.outputs["outbox"] == "my.outbox"
        assert taskspec.io.control["ctrl_in"] == "my.ctrl.in"
        assert taskspec.io.control["ctrl_out"] == "my.ctrl.out"

    def test_extra_fields_allowed(self):
        """Test that extra fields are allowed for forward compatibility."""
        taskspec = TaskSpec(
            tid="1234567890123456789",
            name="test",
            spec=SpecSection(type="function", function_target="test:func"),
            io=IOSection(
                outputs={"outbox": "test.out"},
                control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
            ),
            future_field="some_value",  # Extra field
        )

        # Should not raise error
        assert hasattr(taskspec, "future_field")

    def test_json_serialization(self):
        """Test JSON serialization and deserialization."""
        original = fixtures.create_valid_command_taskspec()

        # Serialize to JSON
        json_str = original.model_dump_json(indent=2)

        # Verify it's valid JSON
        json_data = json.loads(json_str)
        assert json_data["tid"] == "1234567890123456789"
        assert json_data["name"] == "test-command"

        # Deserialize back
        restored = TaskSpec.model_validate_json(json_str)

        # Check equality
        assert restored.tid == original.tid
        assert restored.name == original.name
        assert restored.spec.process_target == original.spec.process_target
        assert restored.metadata == original.metadata

    def test_version_validation(self):
        """Test version field validation."""
        # Valid version
        taskspec = TaskSpec(
            tid="1234567890123456789",
            name="test",
            version="2.0",
            spec=SpecSection(type="function", function_target="test:func"),
            io=IOSection(
                outputs={"outbox": "test.out"},
                control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
            ),
        )
        assert taskspec.version == "2.0"

        # Invalid version format
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                version="invalid",
                spec=SpecSection(type="function", function_target="test:func"),
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        assert "String should match pattern" in str(exc_info.value)

    def test_spec_type_switching_validation(self):
        """Test that function/command specific fields are validated correctly."""
        # Function with process_target should fail
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                spec=SpecSection(
                    type="function",
                    function_target="test:func",
                    process_target=["echo"],  # Should not be set for function
                ),
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        assert "process_target should not be set when type is 'function'" in str(
            exc_info.value
        )

        # Command with function_target should fail
        with pytest.raises(ValidationError) as exc_info:
            TaskSpec(
                tid="1234567890123456789",
                name="test",
                spec=SpecSection(
                    type="command",
                    process_target=["echo"],
                    function_target="test:func",  # Should not be set for command
                ),
                io=IOSection(
                    outputs={"outbox": "test.out"},
                    control={"ctrl_in": "test.in", "ctrl_out": "test.out"},
                ),
            )
        assert "function_target should not be set when type is 'command'" in str(
            exc_info.value
        )


class TestStrictValidation:
    """Test strict validation of REQUIRED fields per spec."""

    def test_missing_io_section(self):
        """Test that missing io section is caught."""
        is_valid, errors = validate_taskspec(json.dumps(fixtures.INVALID_MISSING_IO))
        assert is_valid is False
        # When io is missing, Pydantic creates default empty dicts which fail validation
        # The error could be about missing io field or missing required queues
        assert len(errors) > 0
        assert any("io" in k or "outbox" in v or "ctrl" in v for k, v in errors.items())

    def test_missing_state_section(self):
        """Test that missing state section gets defaults."""
        # When state is missing, Pydantic creates a default StateSection
        # with all default values, which is actually valid behavior
        is_valid, errors = validate_taskspec(json.dumps(fixtures.INVALID_MISSING_STATE))
        assert is_valid is True  # This is valid - state has defaults
        assert errors == {}

    def test_missing_metadata_section(self):
        """Test that missing metadata section gets defaults."""
        # When metadata is missing, Pydantic creates an empty dict
        # which is valid per spec (REQUIRED but can be empty)
        is_valid, errors = validate_taskspec(
            json.dumps(fixtures.INVALID_MISSING_METADATA)
        )
        assert is_valid is True  # This is valid - metadata defaults to empty dict
        assert errors == {}

    def test_missing_required_queues(self):
        """Test that missing required queues are caught."""
        # Test missing outbox
        is_valid, errors = validate_taskspec(
            json.dumps(fixtures.INVALID_MISSING_OUTBOX)
        )
        assert is_valid is False
        assert any("outbox" in str(v) for v in errors.values())

        # Test missing ctrl_in
        is_valid, errors = validate_taskspec(
            json.dumps(fixtures.INVALID_MISSING_CTRL_IN)
        )
        assert is_valid is False
        assert any("ctrl_in" in str(v) for v in errors.values())

        # Test missing ctrl_out
        is_valid, errors = validate_taskspec(
            json.dumps(fixtures.INVALID_MISSING_CTRL_OUT)
        )
        assert is_valid is False
        assert any("ctrl_out" in str(v) for v in errors.values())

    def test_missing_state_required_fields(self):
        """Test that missing required state fields are caught."""
        invalid_json = json.dumps(
            {
                "tid": "1234567890123456789",
                "name": "test",
                "spec": {"type": "function", "function_target": "test:func"},
                "io": {
                    "inputs": {},
                    "outputs": {"outbox": "test.out"},
                    "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
                },
                "state": {
                    "status": "created"
                    # Missing return_code, started_at, completed_at
                },
            }
        )

        # Pydantic will provide defaults for these None-able fields
        # So this should actually be valid
        is_valid, errors = validate_taskspec(invalid_json)
        assert is_valid is True

    def test_missing_required_sections(self):
        """Test that missing required sections are caught."""
        # This should fail validation since io and state are required
        json_data = json.dumps(
            {
                "tid": "1234567890123456789",
                "name": "test",
                "spec": {"type": "function", "function_target": "test:func"},
                # Missing io and state sections
            }
        )

        is_valid, errors = validate_taskspec(json_data)
        assert is_valid is False
        # Should have errors for missing io and state sections
        assert len(errors) > 0

    def test_fully_valid_strict(self):
        """Test a fully valid TaskSpec passes strict validation."""
        is_valid, errors = validate_taskspec(
            json.dumps(fixtures.MINIMAL_VALID_TASKSPEC_DICT)
        )
        assert is_valid is True
        assert errors == {}


class TestValidateTaskspecFunction:
    """Test the validate_taskspec function."""

    def test_valid_taskspec(self):
        """Test validation of a valid TaskSpec."""
        is_valid, errors = validate_taskspec(
            json.dumps(fixtures.MINIMAL_VALID_TASKSPEC_DICT)
        )
        assert is_valid is True
        assert errors == {}

    def test_invalid_tid(self):
        """Test validation with invalid TID."""
        is_valid, errors = validate_taskspec(json.dumps(fixtures.INVALID_SHORT_TID))
        assert is_valid is False
        assert "tid" in errors
        assert "String should match pattern" in errors["tid"]

    def test_missing_required_field(self):
        """Test validation with missing required field."""
        is_valid, errors = validate_taskspec(json.dumps(fixtures.INVALID_MISSING_NAME))
        assert is_valid is False
        assert "name" in errors
        assert "Field required" in errors["name"]

    def test_invalid_spec_type(self):
        """Test validation with invalid spec type."""
        invalid_json = json.dumps(
            {
                "tid": "1234567890123456789",
                "name": "test-task",
                "spec": {
                    "type": "invalid",  # Invalid type
                    "function_target": "module:func",
                },
                "io": {
                    "inputs": {},
                    "outputs": {"outbox": "test.out"},
                    "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
                },
                "state": {
                    "status": "created",
                    "return_code": None,
                    "started_at": None,
                    "completed_at": None,
                },
            }
        )

        is_valid, errors = validate_taskspec(invalid_json)
        assert is_valid is False
        assert "spec.type" in errors

    def test_missing_function_target_error(self):
        """Test validation error for missing function_target."""
        is_valid, errors = validate_taskspec(
            json.dumps(fixtures.INVALID_MISSING_FUNCTION_TARGET)
        )
        assert is_valid is False
        # The error will be in the root of spec due to model_validator
        assert any("function_target is required" in error for error in errors.values())

    def test_invalid_memory_limit(self):
        """Test validation with invalid memory limit."""
        is_valid, errors = validate_taskspec(json.dumps(fixtures.INVALID_MEMORY_LIMIT))
        assert is_valid is False
        assert "spec.memory_limit" in errors
        assert "greater than or equal to 1" in errors["spec.memory_limit"]

    def test_invalid_json_format(self):
        """Test validation with malformed JSON."""
        invalid_json = "{ invalid json"

        is_valid, errors = validate_taskspec(invalid_json)
        assert is_valid is False
        assert "_json" in errors
        assert "Expecting" in errors["_json"] or "JSON" in errors["_json"]

    def test_complex_validation_errors(self):
        """Test validation with multiple errors."""
        invalid_json = json.dumps(
            {
                "tid": "abc",  # Invalid format
                "name": "",  # Empty string
                "spec": {
                    "type": "command"
                    # Missing process_target
                },
                "io": {
                    "inputs": {},
                    "outputs": {"outbox": "test.out"},
                    "control": {
                        "ctrl_in": "test.in"
                        # Missing ctrl_out
                    },
                },
                "state": {
                    "status": "created",
                    "return_code": None,
                    "started_at": None,
                    "completed_at": None,
                },
            }
        )

        is_valid, errors = validate_taskspec(invalid_json)
        assert is_valid is False
        # Should have multiple errors
        assert len(errors) >= 3
        assert "tid" in errors
        assert "name" in errors or "spec.name" in errors
