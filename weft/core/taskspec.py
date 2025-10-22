"""TaskSpec definition for Weft tasks using Pydantic.

Implements the configuration and state structure described in
docs/specifications/01-Core_Components.md [CC-1] and
docs/specifications/02-TaskSpec.md [TS-1].
"""

from __future__ import annotations

import json as json_module
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from weft._constants import (
    DEFAULT_CLEANUP_ON_EXIT,
    DEFAULT_CPU_PERCENT,
    DEFAULT_ENABLE_PROCESS_TITLE,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_MAX_FDS,
    DEFAULT_MEMORY_MB,
    DEFAULT_OUTPUT_SIZE_LIMIT_MB,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_REPORTING_INTERVAL,
    DEFAULT_STATUS,
    DEFAULT_STREAM_OUTPUT,
    DEFAULT_TIMEOUT,
    DEFAULT_WEFT_CONTEXT,
    MAX_CPU_LIMIT,
    MIN_CONNECTIONS_LIMIT,
    MIN_CPU_LIMIT,
    MIN_FDS_LIMIT,
    MIN_MEMORY_LIMIT,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    TASKSPEC_TID_LENGTH,
    TASKSPEC_VERSION,
)


class ReservedPolicy(str, Enum):
    KEEP = "keep"
    REQUEUE = "requeue"
    CLEAR = "clear"


class LimitsSection(BaseModel):
    """Resource limits for task execution (Spec: [CC-1], [TS-1])."""

    memory_mb: int | None = Field(
        None, ge=MIN_MEMORY_LIMIT, description="Memory limit in MB"
    )
    cpu_percent: int | None = Field(
        None, ge=MIN_CPU_LIMIT, le=MAX_CPU_LIMIT, description="CPU limit in percent"
    )
    max_fds: int | None = Field(None, ge=MIN_FDS_LIMIT)
    max_connections: int | None = Field(None, ge=MIN_CONNECTIONS_LIMIT)

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        object.__setattr__(self, "_allow_mutation", False)

    @contextmanager
    def _mutations_allowed(self) -> Iterator[None]:
        object.__setattr__(self, "_allow_mutation", True)
        try:
            yield
        finally:
            object.__setattr__(self, "_allow_mutation", False)

    def __setattr__(self, name: str, value: Any) -> None:
        """Prevent modification if this instance is frozen."""
        if name.startswith("_"):
            super().__setattr__(name, value)
            return
        if getattr(self, "_frozen", False) and not getattr(
            self, "_allow_mutation", False
        ):
            raise AttributeError(
                f"Cannot modify field '{name}' on frozen LimitsSection. "
                "LimitsSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        """Mark this instance as frozen."""
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)


class SpecSection(BaseModel):
    """Execution configuration (Spec: [CC-1], [TS-1])."""

    type: Literal["function", "command"]
    function_target: str | None = None
    process_target: list[str] | None = None
    args: list[Any] = Field(default_factory=list)
    keyword_args: dict[str, Any] = Field(default_factory=dict)
    timeout: float | None = None

    # Resource limits (grouped for clarity) - NEW STRUCTURE
    limits: LimitsSection = Field(default_factory=LimitsSection)

    # Environment and execution behavior
    env: dict[str, str] | None = None
    working_dir: str | None = None
    interactive: bool = False
    stream_output: bool = DEFAULT_STREAM_OUTPUT
    cleanup_on_exit: bool = DEFAULT_CLEANUP_ON_EXIT

    # Monitoring configuration
    polling_interval: float = Field(
        DEFAULT_POLLING_INTERVAL, gt=0, description="Polling interval in seconds"
    )
    reporting_interval: Literal["poll", "transition"] = DEFAULT_REPORTING_INTERVAL
    monitor_class: str = "weft.core.resource_monitor.ResourceMonitor"

    # NEW FIELDS from specification
    enable_process_title: bool = DEFAULT_ENABLE_PROCESS_TITLE
    output_size_limit_mb: int | None = DEFAULT_OUTPUT_SIZE_LIMIT_MB
    weft_context: str | None = DEFAULT_WEFT_CONTEXT
    reserved_policy_on_stop: ReservedPolicy = ReservedPolicy.KEEP
    reserved_policy_on_error: ReservedPolicy = ReservedPolicy.KEEP

    @model_validator(mode="after")
    def validate_target(self) -> SpecSection:
        """Ensure either function_target or process_target is provided based on type."""
        if self.type == "function" and not self.function_target:
            raise ValueError("function_target is required when type is 'function'")
        if self.type == "command" and not self.process_target:
            raise ValueError("process_target is required when type is 'command'")
        if self.type == "function" and self.process_target:
            raise ValueError("process_target should not be set when type is 'function'")
        if self.type == "command" and self.function_target:
            raise ValueError("function_target should not be set when type is 'command'")
        return self

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        object.__setattr__(self, "_allow_mutation", False)

    @contextmanager
    def _mutations_allowed(self) -> Iterator[None]:
        object.__setattr__(self, "_allow_mutation", True)
        try:
            if hasattr(self, "limits") and hasattr(self.limits, "_mutations_allowed"):
                with self.limits._mutations_allowed():
                    yield
            else:
                yield
        finally:
            object.__setattr__(self, "_allow_mutation", False)

    def __setattr__(self, name: str, value: Any) -> None:
        """Prevent modification if this instance is frozen."""
        if name.startswith("_"):
            super().__setattr__(name, value)
            return
        if getattr(self, "_frozen", False) and not getattr(
            self, "_allow_mutation", False
        ):
            raise AttributeError(
                f"Cannot modify field '{name}' on frozen SpecSection. "
                "SpecSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        """Mark this instance as frozen."""
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)
        # Also freeze nested LimitsSection
        if hasattr(self.limits, "_freeze"):
            self.limits._freeze()


class IOSection(BaseModel):
    """IO queue definitions (Spec: [CC-1], [TS-1], [MF-2], [MF-3]).

    According to the spec:
    - inputs: REQUIRED element (can be empty dict)
    - outputs: REQUIRED element, must include 'outbox'
    """

    inputs: dict[str, str] = Field(default_factory=dict)  # REQUIRED, can be empty
    outputs: dict[str, str] = Field(default_factory=dict)  # REQUIRED, must have outbox
    control: dict[str, str] = Field(
        default_factory=dict
    )  # REQUIRED, must have ctrl_in/ctrl_out

    @model_validator(mode="after")
    def validate_required_queues(self) -> IOSection:
        """Ensure all required queues are present.

        Per spec:
        - outputs MUST include 'outbox'
        - control MUST include 'ctrl_in' and 'ctrl_out'

        Note: Validation is skipped if all sections are empty (defaults will be applied later)
        """
        # Skip validation if this is an empty default (will be populated by apply_defaults)
        if not self.outputs and not self.control and not self.inputs:
            return self

        # Outputs must have outbox
        if "outbox" not in self.outputs:
            raise ValueError("outputs must include 'outbox'")

        # Control must have both ctrl_in and ctrl_out
        if "ctrl_in" not in self.control:
            raise ValueError("control must include 'ctrl_in'")
        if "ctrl_out" not in self.control:
            raise ValueError("control must include 'ctrl_out'")

        return self

    def __setattr__(self, name: str, value: Any) -> None:
        """Prevent modification if this instance is frozen."""
        if hasattr(self, "_frozen") and getattr(self, "_frozen", False):
            raise AttributeError(
                f"Cannot modify field '{name}' on frozen IOSection. "
                "IOSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        """Mark this instance as frozen."""
        object.__setattr__(self, "_frozen", True)


class StateSection(BaseModel):
    """Runtime state and metrics (Spec: [CC-1], [TS-1], [MF-1], [MF-5]).

    According to the spec, the following are REQUIRED:
    - status: REQUIRED (defaults to 'created')
    - return_code: REQUIRED (None means not yet available)
    - started_at: REQUIRED (None means not yet started)
    - completed_at: REQUIRED (None means not yet completed)
    """

    # REQUIRED fields with defaults
    status: Literal[
        "created",
        "spawning",
        "running",
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "killed",
    ] = DEFAULT_STATUS
    return_code: int | None = None  # REQUIRED, None = no return code yet
    started_at: int | None = None  # REQUIRED, nanosecond timestamp
    completed_at: int | None = None  # REQUIRED, nanosecond timestamp

    # OPTIONAL fields
    pid: int | None = None
    error: str | None = None
    time: float | None = Field(None, ge=0, description="Runtime in seconds")
    memory: float | None = Field(None, ge=0, description="Memory usage in MB")
    cpu: int | None = Field(None, ge=0, le=100, description="CPU percentage")
    fds: int | None = Field(None, ge=0)
    net_connections: int | None = Field(None, ge=0)
    max_memory: float | None = Field(None, ge=0, description="Peak memory in MB")
    max_cpu: int | None = Field(None, ge=0, le=100, description="Peak CPU percentage")
    max_fds: int | None = Field(None, ge=0)
    max_net_connections: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def validate_state_consistency(self) -> StateSection:
        """Ensure state fields are consistent and transitions are valid."""

        # Define valid state requirements
        terminal_states = {"completed", "failed", "timeout", "cancelled", "killed"}
        running_states = {"running", "spawning"}

        # Validate timestamp consistency
        if self.started_at and self.completed_at:
            if self.completed_at < self.started_at:
                raise ValueError("completed_at must be after started_at")

        # Validate state-specific requirements
        if self.status in terminal_states:
            # Terminal states should have completed_at if they have started_at
            if self.started_at and not self.completed_at:
                raise ValueError(
                    f"Status '{self.status}' requires completed_at when started_at is set"
                )

            # Should have return_code in terminal states (can be non-zero for failed)
            if self.status == "completed" and self.return_code is None:
                # Completed should have return code eventually
                pass  # Allow during transition
            elif self.status == "failed" and self.return_code is None:
                # Failed might not have return code if killed
                pass

        elif self.status in running_states:
            # Running states should not have completed_at
            if self.completed_at is not None:
                raise ValueError(
                    f"Status '{self.status}' should not have completed_at set"
                )

            # Running states should have started_at (except spawning)
            if self.status == "running" and not self.started_at:
                raise ValueError("Status 'running' requires started_at to be set")

            # Should not have return_code while running
            if self.return_code is not None:
                raise ValueError(
                    f"Status '{self.status}' should not have return_code set"
                )

        elif self.status == "created":
            # Created state should have no execution timestamps
            if self.started_at is not None:
                raise ValueError("Status 'created' should not have started_at set")
            if self.completed_at is not None:
                raise ValueError("Status 'created' should not have completed_at set")
            if self.return_code is not None:
                raise ValueError("Status 'created' should not have return_code set")
            if self.pid is not None:
                raise ValueError("Status 'created' should not have pid set")

        return self


class TaskSpec(BaseModel):
    """Complete TaskSpec structure with validation (Spec: [CC-1], [TS-1]).

    Args (via model_validate or __init__):
        auto_expand: If True (default), automatically calls apply_defaults() after initialization.
            Set to False to skip automatic expansion.
    """

    tid: str = Field(
        ...,
        pattern=rf"^\d{{{TASKSPEC_TID_LENGTH}}}$",
        description=f"{TASKSPEC_TID_LENGTH}-digit timestamp ID",
    )
    version: str = Field(TASKSPEC_VERSION, pattern=r"^\d+\.\d+$")
    name: str = Field(..., min_length=1)
    description: str | None = None
    spec: SpecSection
    io: IOSection = Field(default_factory=IOSection)
    state: StateSection = Field(default_factory=lambda: StateSection())
    metadata: dict[str, Any] = Field(
        default_factory=dict
    )  # REQUIRED per spec, but can be empty

    @field_validator("tid")
    @classmethod
    def validate_tid(cls, v: str) -> str:
        """Validate TID is a {TASKSPEC_TID_LENGTH}-digit SimpleBroker timestamp.

        Validates:
        1. Exactly 19 digits
        2. Represents a valid nanosecond timestamp
        3. Within reasonable bounds (not too far in future/past)
        """
        import time

        if not v.isdigit() or len(v) != TASKSPEC_TID_LENGTH:
            raise ValueError(f"tid must be exactly {TASKSPEC_TID_LENGTH} digits")

        # Convert to integer for timestamp validation
        try:
            tid_int = int(v)
        except ValueError as e:
            raise ValueError("tid must be a valid integer") from e

        # Validate timestamp is within reasonable bounds
        # SimpleBroker uses time.time_ns() which is nanoseconds since epoch
        current_ns = time.time_ns()

        # Allow timestamps from 2020 onwards (reasonable lower bound)
        min_timestamp_ns = (
            1577836800_000_000_000  # 2020-01-01 00:00:00 UTC in nanoseconds
        )

        # Allow timestamps up to 1 year in the future (reasonable upper bound)
        max_timestamp_ns = current_ns + (365 * 24 * 60 * 60 * 1_000_000_000)

        if tid_int < min_timestamp_ns:
            raise ValueError(f"tid timestamp too old (before 2020): {v}")

        if tid_int > max_timestamp_ns:
            raise ValueError(f"tid timestamp too far in future: {v}")

        return v

    @model_validator(mode="after")
    def validate_required_elements(self) -> TaskSpec:
        """Validate that all REQUIRED elements per spec are present.

        Strict validation is always enforced:
        - io.outputs has 'outbox'
        - io.control has 'ctrl_in' and 'ctrl_out'
        """
        # These are already validated by IOSection validator
        # This is here for documentation and could do additional cross-field validation
        return self

    def model_post_init(self, __context: Any) -> None:
        """Called after the model is initialized.

        Automatically applies defaults unless auto_expand=False was passed,
        then enforces partial immutability.
        """
        # Check if auto_expand was set in context (defaults to True)
        auto_expand = __context.get("auto_expand", True) if __context else True

        # Apply defaults automatically unless disabled
        if auto_expand:
            self.apply_defaults()

        # Enforce partial immutability after initialization
        self._freeze_spec()

    def _freeze_spec(self) -> None:
        """Make spec and io sections immutable after TaskSpec creation.

        This implements the partial immutability design pattern where:
        - tid, spec and io sections become frozen after initialization
        - state and metadata remain mutable for runtime updates
        """
        # Mark the instance as having frozen fields
        # This is checked in __setattr__ to prevent modification
        object.__setattr__(self, "_frozen_fields", {"tid", "spec", "io"})

        # Freeze the nested sections
        if hasattr(self.spec, "_freeze"):
            self.spec._freeze()
        if hasattr(self.io, "_freeze"):
            self.io._freeze()

    def __setattr__(self, name: str, value: Any) -> None:
        """Override setattr to enforce partial immutability.

        Prevents modification of frozen fields (tid, spec, io) after initialization,
        while allowing modification of mutable fields (state, metadata, etc.).
        """
        # During initialization, _frozen_fields won't exist yet
        frozen_fields: set[str] = getattr(self, "_frozen_fields", set[str]())

        # If this field is frozen and we're not in initialization, prevent modification
        if name in frozen_fields:
            raise AttributeError(
                f"Cannot modify immutable field '{name}'. "
                f"Per invariant I{1 if name == 'spec' else 2 if name == 'io' else 3}, "
                f"'{name}' is immutable after TaskSpec creation."
            )

        # Allow normal assignment for mutable fields and during initialization
        super().__setattr__(name, value)

    def get_default_queues(self) -> tuple[dict[str, str], dict[str, str]]:
        """Generate default queue names based on TID.

        Returns:
            Tuple of (input_queues, control_queues)
        """
        default_inputs = {}
        default_control = {}

        # Add defaults if not specified
        if not self.io.inputs:
            default_inputs[QUEUE_INBOX_SUFFIX] = f"T{self.tid}.{QUEUE_INBOX_SUFFIX}"

        if not self.io.control:
            default_control[QUEUE_CTRL_IN_SUFFIX] = (
                f"T{self.tid}.{QUEUE_CTRL_IN_SUFFIX}"
            )
            default_control[QUEUE_CTRL_OUT_SUFFIX] = (
                f"T{self.tid}.{QUEUE_CTRL_OUT_SUFFIX}"
            )

        return default_inputs, default_control

    def apply_defaults(self) -> None:
        """Fully expand the TaskSpec with all default values per specification.

        This method ensures all fields have explicit values, even optional ones.
        After calling this method:
        - All REQUIRED fields will be present
        - All OPTIONAL fields will have their default values if not already set
        - The TaskSpec will be fully expanded and pass strict validation

        Raises:
            ValueError: If the TaskSpec does not pass strict validation after defaults are applied
        """
        with self.spec._mutations_allowed():
            # === Optimized Spec Section Defaults ===
            # Use bulk update pattern for better performance
            spec_defaults: dict[str, Any] = {
                "args": [],
                "keyword_args": {},
                "timeout": DEFAULT_TIMEOUT,
                "env": {},
                "working_dir": None,
            }

            # Apply defaults in bulk - only if field is None
            for field, default_value in spec_defaults.items():
                if getattr(self.spec, field, None) is None:
                    setattr(self.spec, field, default_value)

            # === Limits Section Defaults ===
            # Apply defaults to nested limits section - only apply non-None defaults
            limits_defaults: dict[str, Any] = {
                "memory_mb": DEFAULT_MEMORY_MB,
                "cpu_percent": DEFAULT_CPU_PERCENT,
                "max_fds": DEFAULT_MAX_FDS,
                "max_connections": DEFAULT_MAX_CONNECTIONS,
            }

            for field, default_value in limits_defaults.items():
                # Only set defaults if field is None AND default is not None
                if (
                    getattr(self.spec.limits, field, None) is None
                    and default_value is not None
                ):
                    setattr(self.spec.limits, field, default_value)

        # === IO Section Defaults ===
        # Generate queue names if needed
        tid_prefix = f"T{self.tid}"
        queue_defaults = {
            ("outputs", "outbox"): f"{tid_prefix}.{QUEUE_OUTBOX_SUFFIX}",
            ("control", "ctrl_in"): f"{tid_prefix}.{QUEUE_CTRL_IN_SUFFIX}",
            ("control", "ctrl_out"): f"{tid_prefix}.{QUEUE_CTRL_OUT_SUFFIX}",
        }

        for (section, queue_name), default_name in queue_defaults.items():
            section_dict = getattr(self.io, section)
            if queue_name not in section_dict:
                section_dict[queue_name] = default_name

        # === State Section ===
        # State metrics remain None until measurements begin (documented decision)

        # === Metadata Section ===
        if self.metadata is None:
            self.metadata = {}

        # Validate that the TaskSpec now meets strict requirements
        self._validate_strict_requirements()

    def _validate_strict_requirements(self) -> None:
        """Validate that this TaskSpec meets all REQUIRED fields per the specification.

        Raises:
            ValueError: If any required fields are missing or invalid
        """
        errors = []

        # Check required top-level fields
        if not self.tid:
            errors.append("tid is required")
        if not self.name:
            errors.append("name is required")
        if not self.spec:
            errors.append("spec is required")

        # Check spec requirements
        if self.spec:
            if not self.spec.type:
                errors.append("spec.type is required")
            elif self.spec.type == "function" and not self.spec.function_target:
                errors.append(
                    "spec.function_target is required when type is 'function'"
                )
            elif self.spec.type == "command" and not self.spec.process_target:
                errors.append("spec.process_target is required when type is 'command'")

        # Check io requirements - all are REQUIRED per spec
        if not hasattr(self, "io") or self.io is None:
            errors.append("io section is required")
        else:
            # inputs is REQUIRED (but can be empty)
            if self.io.inputs is None:
                errors.append("io.inputs is required (can be empty dict)")

            # outputs is REQUIRED and must have outbox
            if self.io.outputs is None:
                errors.append("io.outputs is required")
            elif "outbox" not in self.io.outputs:
                errors.append("io.outputs.outbox is required")

            # control is REQUIRED and must have both ctrl_in and ctrl_out
            if self.io.control is None:
                errors.append("io.control is required")
            else:
                if "ctrl_in" not in self.io.control:
                    errors.append("io.control.ctrl_in is required")
                if "ctrl_out" not in self.io.control:
                    errors.append("io.control.ctrl_out is required")

        # Check state requirements - all listed fields are REQUIRED per spec
        if not hasattr(self, "state") or self.state is None:
            errors.append("state section is required")
        else:
            # These fields are REQUIRED (can be None but must exist)
            if not hasattr(self.state, "status"):
                errors.append("state.status is required")
            if not hasattr(self.state, "return_code"):
                errors.append("state.return_code is required (can be None)")
            if not hasattr(self.state, "started_at"):
                errors.append("state.started_at is required (can be None)")
            if not hasattr(self.state, "completed_at"):
                errors.append("state.completed_at is required (can be None)")

        # Check metadata requirement - REQUIRED per spec but can be empty dict
        if not hasattr(self, "metadata") or self.metadata is None:
            errors.append("metadata section is required (can be empty dict)")

        if errors:
            raise ValueError(
                f"TaskSpec validation failed after apply_defaults: {'; '.join(errors)}"
            )

    model_config = {
        "extra": "allow",  # Allow extra fields for forward compatibility
        "validate_assignment": True,  # Validate on assignment
        "use_enum_values": True,  # Use enum values in serialization
    }

    # === Convenience Methods for State Management ===

    def set_status(
        self,
        status: Literal[
            "created",
            "spawning",
            "running",
            "completed",
            "failed",
            "timeout",
            "cancelled",
            "killed",
        ],
        error: str | None = None,
    ) -> None:
        """Set the task status and optionally an error message.

        Args:
            status: New status (created, spawning, running, completed, failed, timeout, cancelled, killed)
            error: Optional error message for failed states

        Raises:
            ValueError: If the status transition is invalid
        """
        import time

        # Terminal states cannot transition
        terminal_states = {"completed", "failed", "timeout", "cancelled", "killed"}
        if self.state.status in terminal_states:
            raise ValueError(
                f"Cannot transition from terminal state '{self.state.status}'"
            )

        # Validate forward-only transitions
        valid_transitions = {
            "created": {"spawning", "failed", "cancelled", "killed"},
            "spawning": {
                "running",
                "completed",
                "failed",
                "timeout",
                "cancelled",
                "killed",
            },
            "running": {"completed", "failed", "timeout", "cancelled", "killed"},
        }

        if self.state.status in valid_transitions:
            if (
                status not in valid_transitions[self.state.status]
                and status != self.state.status
            ):
                raise ValueError(
                    f"Invalid transition from '{self.state.status}' to '{status}'. "
                    f"Valid transitions: {valid_transitions[self.state.status]}"
                )

        # Update status
        self.state.status = status

        # Set timestamps based on state
        if status == "spawning" and not self.state.started_at:
            self.state.started_at = time.time_ns()
        elif status == "running" and not self.state.started_at:
            self.state.started_at = time.time_ns()
        elif status in terminal_states:
            if not self.state.completed_at:
                self.state.completed_at = time.time_ns()
            if error:
                self.state.error = error

    def mark_started(self, pid: int | None = None) -> None:
        """Mark the task as started (spawning/running) with optional PID.

        Args:
            pid: Process ID of the spawned task
        """
        import time

        if self.state.status == "created":
            self.state.status = "spawning"

        if not self.state.started_at:
            self.state.started_at = time.time_ns()

        if pid is not None:
            self.state.pid = pid

    def mark_running(self, pid: int | None = None) -> None:
        """Mark the task as running with optional PID.

        Args:
            pid: Process ID of the running task
        """
        import time

        self.state.status = "running"

        if not self.state.started_at:
            self.state.started_at = time.time_ns()

        if pid is not None:
            self.state.pid = pid

    def mark_completed(self, return_code: int = 0) -> None:
        """Mark the task as completed with return code.

        Args:
            return_code: Process return code (default 0 for success)
        """
        import time

        self.state.status = "completed"
        self.state.return_code = return_code

        if not self.state.completed_at:
            self.state.completed_at = time.time_ns()

    def mark_failed(
        self, error: str | None = None, return_code: int | None = None
    ) -> None:
        """Mark the task as failed with optional error message.

        Args:
            error: Error message describing the failure
            return_code: Process return code if available
        """
        import time

        self.state.status = "failed"

        if error:
            self.state.error = error

        if return_code is not None:
            self.state.return_code = return_code

        if not self.state.completed_at:
            self.state.completed_at = time.time_ns()

    def mark_timeout(self, error: str | None = None) -> None:
        """Mark the task as timed out.

        Args:
            error: Optional timeout error message
        """
        import time

        self.state.status = "timeout"

        if error:
            self.state.error = error
        else:
            self.state.error = f"Task timed out after {self.spec.timeout} seconds"

        if not self.state.completed_at:
            self.state.completed_at = time.time_ns()

    def mark_cancelled(self, reason: str | None = None) -> None:
        """Mark the task as cancelled.

        Args:
            reason: Optional cancellation reason
        """
        import time

        self.state.status = "cancelled"

        if reason:
            self.state.error = f"Cancelled: {reason}"

        if not self.state.completed_at:
            self.state.completed_at = time.time_ns()

    def mark_killed(self, reason: str | None = None) -> None:
        """Mark the task as killed.

        Args:
            reason: Optional kill reason (e.g., "Memory limit exceeded")
        """
        import time

        self.state.status = "killed"

        if reason:
            self.state.error = f"Killed: {reason}"

        if not self.state.completed_at:
            self.state.completed_at = time.time_ns()

    # === Convenience Methods for Metrics Updates ===

    def update_metrics(
        self,
        time: float | None = None,
        memory: float | None = None,
        cpu: int | None = None,
        fds: int | None = None,
        net_connections: int | None = None,
    ) -> None:
        """Update current resource metrics and track maximums.

        Args:
            time: Runtime in seconds
            memory: Memory usage in MB
            cpu: CPU percentage (0-100)
            fds: Number of open file descriptors
            net_connections: Number of network connections
        """
        # Update current metrics
        if time is not None:
            self.state.time = time

        if memory is not None:
            self.state.memory = memory
            # Track maximum
            if self.state.max_memory is None or memory > self.state.max_memory:
                self.state.max_memory = memory

        if cpu is not None:
            self.state.cpu = cpu
            # Track maximum
            if self.state.max_cpu is None or cpu > self.state.max_cpu:
                self.state.max_cpu = cpu

        if fds is not None:
            self.state.fds = fds
            # Track maximum
            if self.state.max_fds is None or fds > self.state.max_fds:
                self.state.max_fds = fds

        if net_connections is not None:
            self.state.net_connections = net_connections
            # Track maximum
            if (
                self.state.max_net_connections is None
                or net_connections > self.state.max_net_connections
            ):
                self.state.max_net_connections = net_connections

    def check_limits_exceeded(self) -> tuple[bool, str | None]:
        """Check if any resource limits have been exceeded.

        Returns:
            Tuple of (exceeded, reason) where exceeded is True if any limit was exceeded
            and reason describes which limit was exceeded.
        """
        # Check memory limit
        if self.spec.limits.memory_mb and self.state.memory:
            if self.state.memory > self.spec.limits.memory_mb:
                return (
                    True,
                    f"Memory limit exceeded: {self.state.memory:.1f}MB > {self.spec.limits.memory_mb}MB",
                )

        # Check CPU limit (sustained usage)
        if self.spec.limits.cpu_percent and self.state.cpu:
            if self.state.cpu > self.spec.limits.cpu_percent:
                return (
                    True,
                    f"CPU limit exceeded: {self.state.cpu}% > {self.spec.limits.cpu_percent}%",
                )

        # Check file descriptor limit
        if self.spec.limits.max_fds and self.state.fds:
            if self.state.fds > self.spec.limits.max_fds:
                return (
                    True,
                    f"File descriptor limit exceeded: {self.state.fds} > {self.spec.limits.max_fds}",
                )

        # Check network connections limit
        if self.spec.limits.max_connections and self.state.net_connections:
            if self.state.net_connections > self.spec.limits.max_connections:
                return (
                    True,
                    f"Network connection limit exceeded: {self.state.net_connections} > {self.spec.limits.max_connections}",
                )

        return False, None

    # === Convenience Methods for Metadata Updates ===

    def update_metadata(self, updates: dict[str, Any]) -> None:
        """Update metadata dictionary with new key-value pairs.

        Args:
            updates: Dictionary of metadata updates to apply
        """
        if self.metadata is None:
            self.metadata = {}
        self.metadata.update(updates)

    def set_metadata(self, key: str, value: Any) -> None:
        """Set a single metadata key-value pair.

        Args:
            key: Metadata key
            value: Metadata value
        """
        if self.metadata is None:
            self.metadata = {}
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get a metadata value by key.

        Args:
            key: Metadata key to retrieve
            default: Default value if key not found

        Returns:
            The metadata value or default if not found
        """
        if self.metadata is None:
            return default
        return self.metadata.get(key, default)

    # === Convenience Methods for Queue Management ===

    def add_input_queue(self, name: str, queue_path: str) -> None:
        """Add an input queue to the IO specification.

        Args:
            name: Queue name (e.g., "data", "config")
            queue_path: Queue path (e.g., "T1234.data")
        """
        if self.io.inputs is None:
            self.io.inputs = {}
        self.io.inputs[name] = queue_path

    def add_output_queue(self, name: str, queue_path: str) -> None:
        """Add an output queue to the IO specification.

        Args:
            name: Queue name (e.g., "logs", "metrics")
            queue_path: Queue path (e.g., "T1234.logs")

        Raises:
            ValueError: If trying to override the required "outbox" queue
        """
        if name == "outbox" and "outbox" in self.io.outputs:
            if self.io.outputs["outbox"] != queue_path:
                raise ValueError("Cannot override required 'outbox' queue")

        if self.io.outputs is None:
            self.io.outputs = {}
        self.io.outputs[name] = queue_path

    def get_queue_path(self, queue_type: str, queue_name: str) -> str | None:
        """Get the path for a specific queue.

        Args:
            queue_type: Type of queue ("inputs", "outputs", or "control")
            queue_name: Name of the queue

        Returns:
            Queue path or None if not found
        """
        queue_section = getattr(self.io, queue_type, None)
        if queue_section is None:
            return None
        return (
            queue_section.get(queue_name) if isinstance(queue_section, dict) else None
        )

    # === Convenience Methods for Reporting ===

    def get_runtime_seconds(self) -> float | None:
        """Calculate runtime in seconds from timestamps.

        Returns:
            Runtime in seconds or None if not started
        """
        if not self.state.started_at:
            return None

        import time

        end_time = self.state.completed_at or time.time_ns()
        return (end_time - self.state.started_at) / 1_000_000_000

    def should_report(self, last_status: str | None = None) -> bool:
        """Check if the task should report based on reporting_interval setting.

        Args:
            last_status: The last reported status (for transition detection)

        Returns:
            True if the task should report its state
        """
        if self.spec.reporting_interval == "transition":
            # Report on status change
            return last_status != self.state.status
        elif self.spec.reporting_interval == "poll":
            # Always report (caller should handle polling interval)
            return True
        return False

    def to_log_dict(self) -> dict[str, Any]:
        """Convert TaskSpec to a dictionary suitable for logging.

        Returns:
            Dictionary with essential task information for logging
        """
        return {
            "tid": self.tid,
            "name": self.name,
            "status": self.state.status,
            "started_at": self.state.started_at,
            "completed_at": self.state.completed_at,
            "runtime_seconds": self.get_runtime_seconds(),
            "memory_mb": self.state.memory,
            "cpu_percent": self.state.cpu,
            "error": self.state.error,
            "metadata": self.metadata,
        }


def validate_taskspec(json_str: str) -> tuple[bool, dict[str, Any]]:
    """Validate a JSON-formatted TaskSpec.

    Args:
        json_str: JSON string containing TaskSpec data

    Returns:
        Tuple of (is_valid, errors) where:
        - is_valid: True if the TaskSpec is valid, False otherwise
        - errors: Dictionary of validation errors (empty if valid)
                 Keys are field paths, values are error messages
    """
    try:
        # First try to parse JSON
        try:
            json_module.loads(json_str)
        except json_module.JSONDecodeError as e:
            return False, {"_json": f"Invalid JSON: {e}"}

        # Then validate with Pydantic - it will enforce all strict requirements
        TaskSpec.model_validate_json(json_str)
        return True, {}
    except ValidationError as e:
        errors = {}
        for error in e.errors():
            # Build field path from loc tuple
            field_path = ".".join(str(x) for x in error["loc"]) if error["loc"] else ""
            errors[field_path] = error["msg"]
        return False, errors
    except Exception as e:
        # Handle other unexpected errors
        return False, {"_json": str(e)}
