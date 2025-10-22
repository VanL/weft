"""TaskSpec behaviour tests aligned with the v1 specification."""

from __future__ import annotations

import pytest

from weft.core.taskspec import (
    IOSection,
    ReservedPolicy,
    SpecSection,
    StateSection,
    TaskSpec,
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


class TestMetadataHelpers:
    """Metadata section remains mutable with helpers."""

    def test_update_and_get_metadata(self) -> None:
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.update_metadata({"owner": "agent"})
        assert taskspec.get_metadata("owner") == "agent"
        taskspec.set_metadata("priority", "high")
        assert taskspec.get_metadata("priority") == "high"


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
