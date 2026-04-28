"""TaskSpec definition for Weft tasks using Pydantic.

Implements the configuration and state structure described in
docs/specifications/01-Core_Components.md [CC-1] and
docs/specifications/02-TaskSpec.md [TS-1], [TS-1.3].
"""

from __future__ import annotations

import copy
import json as json_module
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, NoReturn, Self, SupportsIndex

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from weft._constants import (
    DEFAULT_CLEANUP_ON_EXIT,
    DEFAULT_ENABLE_PROCESS_TITLE,
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
    TASKSPEC_BUNDLE_ROOT_FIELD,
    TASKSPEC_TID_LENGTH,
    TASKSPEC_VERSION,
)

from .parameterization import validate_parameterization_adapter_ref
from .run_input import (
    RUN_COMMAND_RESERVED_OPTION_NAMES,
    normalize_declared_option_name,
    validate_run_input_adapter_ref,
)


class FrozenList(list):
    """List subclass that rejects in-place mutation."""

    def _immutable(self, operation: str) -> NoReturn:
        raise TypeError(f"Cannot {operation} immutable FrozenList")

    def append(self, object: Any) -> None:  # noqa: A003
        self._immutable("append to")

    def extend(self, iterable: Iterable[Any]) -> None:
        self._immutable("extend")

    def insert(self, index: SupportsIndex, object: Any) -> None:  # noqa: A003
        self._immutable("insert into")

    def pop(self, index: SupportsIndex = -1) -> Any:
        self._immutable("pop from")

    def remove(self, value: Any) -> None:
        self._immutable("remove from")

    def clear(self) -> None:
        self._immutable("clear")

    def reverse(self) -> None:
        self._immutable("reverse")

    def sort(self, *args: Any, **kwargs: Any) -> None:
        self._immutable("sort")

    def __setitem__(self, index: SupportsIndex | slice, value: Any) -> None:
        self._immutable("assign into")

    def __delitem__(self, index: SupportsIndex | slice) -> None:
        self._immutable("delete from")

    def __iadd__(self, other: Iterable[Any]) -> Self:  # type: ignore[misc]
        self._immutable("extend")

    def __imul__(self, other: SupportsIndex) -> Self:
        self._immutable("repeat")


class FrozenDict(dict):
    """Dict subclass that rejects in-place mutation."""

    def _immutable(self, operation: str) -> NoReturn:
        raise TypeError(f"Cannot {operation} immutable FrozenDict")

    def __setitem__(self, key: Any, value: Any) -> None:
        self._immutable("assign into")

    def __delitem__(self, key: Any) -> None:
        self._immutable("delete from")

    def clear(self) -> None:
        self._immutable("clear")

    def pop(self, key: Any, default: Any = None) -> Any:
        self._immutable("pop from")

    def popitem(self) -> tuple[Any, Any]:
        self._immutable("pop from")

    def setdefault(self, key: Any, default: Any = None) -> Any:
        self._immutable("set default on")

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._immutable("update")

    def __ior__(  # type: ignore[override,misc]
        self,
        other: Mapping[Any, Any] | Iterable[tuple[Any, Any]],
    ) -> Self:
        self._immutable("update")


def _freeze_container_value(value: Any) -> Any:
    """Recursively freeze container values used by immutable TaskSpec sections."""
    if isinstance(value, FrozenDict | FrozenList):
        return value
    if isinstance(value, dict):
        return FrozenDict(
            {key: _freeze_container_value(nested) for key, nested in value.items()}
        )
    if isinstance(value, list):
        return FrozenList([_freeze_container_value(item) for item in value])
    if isinstance(value, tuple):
        return tuple(_freeze_container_value(item) for item in value)
    return value


def rewrite_tid_in_io(io_section: dict[str, Any], old_tid: str, new_tid: str) -> None:
    """Rewrite default queue names that embed an old TID prefix.

    Spec: [TS-1] (TID assignment and io queue naming).
    """
    if not old_tid or not new_tid or old_tid == new_tid:
        return

    old_prefix = f"T{old_tid}."
    new_prefix = f"T{new_tid}."
    for key in ("inputs", "outputs", "control"):
        section = io_section.get(key)
        if not isinstance(section, dict):
            continue
        for name, value in list(section.items()):
            if isinstance(value, str) and old_prefix in value:
                section[name] = value.replace(old_prefix, new_prefix)


def resolve_taskspec_payload(
    payload: Mapping[str, Any],
    *,
    tid: str | None = None,
    inherited_weft_context: str | None = None,
) -> dict[str, Any]:
    """Return a fully resolved TaskSpec payload without mutating the input.

    Expands templates into resolved TaskSpecs by populating tid, io queues,
    state, metadata, limits defaults, and runner defaults.

    Spec: [TS-1] (Templates vs runtime-expanded specs).
    """
    candidate = copy.deepcopy(dict(payload))
    original_tid = candidate.get("tid")
    if tid is not None:
        candidate["tid"] = tid

    resolved_tid = candidate.get("tid")
    if not isinstance(resolved_tid, str) or not resolved_tid:
        raise ValueError("resolved TaskSpec payload requires a tid")

    candidate.setdefault("version", TASKSPEC_VERSION)
    if candidate.get("state") is None:
        candidate["state"] = {}
    if candidate.get("metadata") is None:
        candidate["metadata"] = {}
    candidate.setdefault("state", {})
    candidate.setdefault("metadata", {})

    io_section = candidate.get("io")
    if io_section is None:
        io_section = {}
        candidate["io"] = io_section

    if (
        isinstance(io_section, dict)
        and isinstance(original_tid, str)
        and original_tid != resolved_tid
    ):
        rewrite_tid_in_io(io_section, original_tid, resolved_tid)

    if isinstance(io_section, dict):
        inputs = io_section.get("inputs")
        if inputs is None:
            inputs = {}
            io_section["inputs"] = inputs
        outputs = io_section.get("outputs")
        if outputs is None:
            outputs = {}
            io_section["outputs"] = outputs
        control = io_section.get("control")
        if control is None:
            control = {}
            io_section["control"] = control

        if isinstance(inputs, dict):
            inputs.setdefault(
                QUEUE_INBOX_SUFFIX, f"T{resolved_tid}.{QUEUE_INBOX_SUFFIX}"
            )
        if isinstance(outputs, dict):
            outputs.setdefault(
                QUEUE_OUTBOX_SUFFIX, f"T{resolved_tid}.{QUEUE_OUTBOX_SUFFIX}"
            )
        if isinstance(control, dict):
            control.setdefault(
                QUEUE_CTRL_IN_SUFFIX, f"T{resolved_tid}.{QUEUE_CTRL_IN_SUFFIX}"
            )
            control.setdefault(
                QUEUE_CTRL_OUT_SUFFIX, f"T{resolved_tid}.{QUEUE_CTRL_OUT_SUFFIX}"
            )

    spec_section = candidate.get("spec")
    if isinstance(spec_section, dict):
        runner = spec_section.get("runner")
        if runner is None:
            runner = {}
            spec_section["runner"] = runner
        if isinstance(runner, dict):
            runner.setdefault("name", "host")
            if runner.get("options") is None:
                runner["options"] = {}
        if inherited_weft_context and not spec_section.get("weft_context"):
            spec_section["weft_context"] = inherited_weft_context
        if spec_section.get("args") is None:
            spec_section["args"] = []
        if spec_section.get("keyword_args") is None:
            spec_section["keyword_args"] = {}
        if "timeout" not in spec_section:
            spec_section["timeout"] = DEFAULT_TIMEOUT
        if spec_section.get("env") is None:
            spec_section["env"] = {}
        if "working_dir" not in spec_section:
            spec_section["working_dir"] = None

        limits = spec_section.get("limits")
        if limits is None:
            limits = {}
            spec_section["limits"] = limits
        if isinstance(limits, dict):
            if limits.get("memory_mb") is None:
                limits["memory_mb"] = DEFAULT_MEMORY_MB

    return candidate


def normalize_taskspec_bundle_root(value: object) -> str | None:
    """Normalize an internal bundle-root value when present."""
    if value is None:
        return None
    if isinstance(value, Path):
        return str(value.resolve())
    if not isinstance(value, str):
        raise TypeError(
            f"{TASKSPEC_BUNDLE_ROOT_FIELD} must be a string path when present"
        )
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{TASKSPEC_BUNDLE_ROOT_FIELD} must not be empty")
    return str(Path(cleaned).expanduser().resolve())


def bundle_root_from_taskspec_payload(payload: Mapping[str, Any]) -> str | None:
    """Return the internal bundle root recorded on a TaskSpec payload."""
    return normalize_taskspec_bundle_root(payload.get(TASKSPEC_BUNDLE_ROOT_FIELD))


def apply_bundle_root_to_taskspec_payload(
    payload: dict[str, Any],
    bundle_root: str | Path | None,
) -> dict[str, Any]:
    """Attach an internal bundle root to a TaskSpec payload when present."""
    normalized = normalize_taskspec_bundle_root(bundle_root)
    if normalized is not None:
        payload[TASKSPEC_BUNDLE_ROOT_FIELD] = normalized
    return payload


class ReservedPolicy(StrEnum):
    """Reserved queue policy options (Spec: [TS-1.1])."""

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


class RunnerSection(BaseModel):
    """Runner selection for task execution (Spec: [CC-1], [TS-1], [TS-1.3])."""

    name: str = Field("host", min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)
    environment_profile_ref: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("runner.name must be a non-empty string")
        return normalized

    @field_validator("options")
    @classmethod
    def validate_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json_module.dumps(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("runner.options must be JSON-serializable") from exc
        return value

    @field_validator("environment_profile_ref")
    @classmethod
    def validate_environment_profile_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "runner.environment_profile_ref must be a non-empty string"
            )
        return normalized

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
                f"Cannot modify field '{name}' on frozen RunnerSection. "
                "RunnerSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        """Mark this instance as frozen."""
        object.__setattr__(self, "options", _freeze_container_value(self.options))
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)


class RunInputArgumentSection(BaseModel):
    """Declared CLI argument accepted by ``weft run --spec``."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["string", "path"] = "string"
    required: bool = False
    help: str | None = None

    @field_validator("help")
    @classmethod
    def validate_help(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

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
        if name.startswith("_"):
            super().__setattr__(name, value)
            return
        if getattr(self, "_frozen", False) and not getattr(
            self, "_allow_mutation", False
        ):
            raise AttributeError(
                f"Cannot modify field '{name}' on frozen RunInputArgumentSection. "
                "RunInputArgumentSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)


class ParameterizationArgumentSection(RunInputArgumentSection):
    """Declared CLI argument accepted by ``spec.parameterization``."""

    default: str | None = None
    choices: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("default")
    @classmethod
    def validate_default(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("default must be a non-empty string when provided")
        return normalized

    @field_validator("choices")
    @classmethod
    def validate_choices(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = item.strip()
            if not candidate:
                raise ValueError("choices entries must be non-empty strings")
            if candidate in seen:
                raise ValueError("choices entries must not contain duplicates")
            seen.add(candidate)
            normalized.append(candidate)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_default_with_choices(self) -> ParameterizationArgumentSection:
        if (
            self.default is not None
            and self.choices
            and self.default not in self.choices
        ):
            raise ValueError("default must be one of the declared choices")
        return self


class RunInputStdinSection(BaseModel):
    """Declared stdin contract for ``weft run --spec``."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    required: bool = False
    help: str | None = None

    @field_validator("help")
    @classmethod
    def validate_help(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

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
        if name.startswith("_"):
            super().__setattr__(name, value)
            return
        if getattr(self, "_frozen", False) and not getattr(
            self, "_allow_mutation", False
        ):
            raise AttributeError(
                f"Cannot modify field '{name}' on frozen RunInputStdinSection. "
                "RunInputStdinSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)


def _validate_declared_argument_names(
    arguments: Mapping[str, Any],
    *,
    section_name: str,
) -> dict[str, str]:
    seen_options: dict[str, str] = {}
    for name in arguments:
        if not name.isidentifier():
            raise ValueError(f"{section_name}.arguments keys must be valid identifiers")
        option_name = normalize_declared_option_name(name)
        if (
            option_name.startswith("-")
            or option_name.endswith("-")
            or "--" in option_name
        ):
            raise ValueError(
                f"{section_name} argument names must normalize cleanly to "
                "long option names"
            )
        if option_name in RUN_COMMAND_RESERVED_OPTION_NAMES:
            raise ValueError(
                f"{section_name} argument '{name}' collides with reserved "
                f"`weft run` option '--{option_name}'"
            )
        previous = seen_options.get(option_name)
        if previous is not None:
            raise ValueError(
                f"{section_name} argument names collide after '_' to '-' "
                f"normalization: '{previous}' and '{name}'"
            )
        seen_options[option_name] = name
    return seen_options


class RunInputSection(BaseModel):
    """Submission-time input shaping for ``weft run --spec``."""

    model_config = ConfigDict(extra="forbid")

    adapter_ref: str
    arguments: dict[str, RunInputArgumentSection] = Field(default_factory=dict)
    stdin: RunInputStdinSection | None = None

    @field_validator("adapter_ref")
    @classmethod
    def validate_adapter_ref(cls, value: str) -> str:
        return validate_run_input_adapter_ref(value)

    @model_validator(mode="after")
    def validate_argument_names(self) -> RunInputSection:
        _validate_declared_argument_names(
            self.arguments,
            section_name="spec.run_input",
        )
        return self

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        object.__setattr__(self, "_allow_mutation", False)

    @contextmanager
    def _mutations_allowed(self) -> Iterator[None]:
        object.__setattr__(self, "_allow_mutation", True)
        try:
            with ExitStack() as stack:
                for argument in self.arguments.values():
                    if hasattr(argument, "_mutations_allowed"):
                        stack.enter_context(argument._mutations_allowed())
                if self.stdin is not None and hasattr(self.stdin, "_mutations_allowed"):
                    stack.enter_context(self.stdin._mutations_allowed())
                yield
        finally:
            object.__setattr__(self, "_allow_mutation", False)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            super().__setattr__(name, value)
            return
        if getattr(self, "_frozen", False) and not getattr(
            self, "_allow_mutation", False
        ):
            raise AttributeError(
                f"Cannot modify field '{name}' on frozen RunInputSection. "
                "RunInputSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        for argument in self.arguments.values():
            if hasattr(argument, "_freeze"):
                argument._freeze()
        if self.stdin is not None and hasattr(self.stdin, "_freeze"):
            self.stdin._freeze()
        object.__setattr__(self, "arguments", _freeze_container_value(self.arguments))
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)


class ParameterizationSection(BaseModel):
    """Submission-time TaskSpec materialization for ``weft run --spec``."""

    model_config = ConfigDict(extra="forbid")

    adapter_ref: str
    arguments: dict[str, ParameterizationArgumentSection] = Field(default_factory=dict)

    @field_validator("adapter_ref")
    @classmethod
    def validate_adapter_ref(cls, value: str) -> str:
        return validate_parameterization_adapter_ref(value)

    @model_validator(mode="after")
    def validate_argument_names(self) -> ParameterizationSection:
        _validate_declared_argument_names(
            self.arguments,
            section_name="spec.parameterization",
        )
        return self

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        object.__setattr__(self, "_allow_mutation", False)

    @contextmanager
    def _mutations_allowed(self) -> Iterator[None]:
        object.__setattr__(self, "_allow_mutation", True)
        try:
            with ExitStack() as stack:
                for argument in self.arguments.values():
                    if hasattr(argument, "_mutations_allowed"):
                        stack.enter_context(argument._mutations_allowed())
                yield
        finally:
            object.__setattr__(self, "_allow_mutation", False)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            super().__setattr__(name, value)
            return
        if getattr(self, "_frozen", False) and not getattr(
            self, "_allow_mutation", False
        ):
            raise AttributeError(
                f"Cannot modify field '{name}' on frozen ParameterizationSection. "
                "ParameterizationSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        for argument in self.arguments.values():
            if hasattr(argument, "_freeze"):
                argument._freeze()
        object.__setattr__(self, "arguments", _freeze_container_value(self.arguments))
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)


class AgentToolSection(BaseModel):
    """Agent tool descriptor for MVP agent runtime support (Spec: [AR-2.2])."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    kind: Literal["python"]
    ref: str = Field(..., min_length=1)
    description: str | None = None
    args_schema: dict[str, Any] | None = None
    approval_required: bool = False
    config: dict[str, Any] = Field(default_factory=dict)

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
                f"Cannot modify field '{name}' on frozen AgentToolSection. "
                "AgentToolSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        """Mark this instance as frozen."""
        object.__setattr__(
            self,
            "args_schema",
            _freeze_container_value(self.args_schema) if self.args_schema else None,
        )
        object.__setattr__(self, "config", _freeze_container_value(self.config))
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)


class AgentTemplateSection(BaseModel):
    """Named static prompt template for an agent task (Spec: [AR-2.2])."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(..., min_length=1)
    instructions: str | None = None

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
                f"Cannot modify field '{name}' on frozen AgentTemplateSection. "
                "AgentTemplateSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        """Mark this instance as frozen."""
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)


class AgentSection(BaseModel):
    """Agent runtime configuration for agent tasks (Spec: [AR-2.2])."""

    model_config = ConfigDict(extra="forbid")

    runtime: str = Field(..., min_length=1)
    authority_class: Literal["bounded", "general"] | None = None
    model: str | None = None
    instructions: str | None = None
    templates: dict[str, AgentTemplateSection] = Field(default_factory=dict)
    tools: tuple[AgentToolSection, ...] = Field(default_factory=tuple)
    output_mode: Literal["text", "json", "messages"] = "text"
    output_schema: dict[str, Any] | str | None = None
    max_turns: int = Field(20, gt=0)
    options: dict[str, Any] = Field(default_factory=dict)
    conversation_scope: Literal["per_message", "per_task"] = "per_message"
    runtime_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_runtime_constraints(self) -> AgentSection:
        """Validate runtime-specific schema constraints (Spec: [AR-2.2])."""
        if self.output_schema is not None and self.output_mode != "json":
            raise ValueError("output_schema is only allowed when output_mode is 'json'")
        if self.runtime == "llm" and self.authority_class == "general":
            raise ValueError("llm only supports authority_class='bounded'")
        if self.runtime == "provider_cli":
            provider = self.runtime_config.get("provider")
            if not isinstance(provider, str) or not provider.strip():
                raise ValueError(
                    "provider_cli requires spec.agent.runtime_config.provider"
                )
            for key in ("executable", "resolver_ref", "tool_profile_ref"):
                value = self.runtime_config.get(key)
                if value is not None and (
                    not isinstance(value, str) or not value.strip()
                ):
                    raise ValueError(
                        f"spec.agent.runtime_config.{key} must be a non-empty string"
                    )
            if self.output_mode != "text":
                raise ValueError("provider_cli only supports output_mode='text'")
            if self.output_schema is not None:
                raise ValueError("provider_cli does not support output_schema")
            if self.conversation_scope not in {"per_message", "per_task"}:
                raise ValueError(
                    "provider_cli only supports conversation_scope values "
                    "'per_message' and 'per_task'"
                )
            if self.tools:
                raise ValueError(
                    "provider_cli does not support spec.agent.tools in Phase 2"
                )
        return self

    @property
    def resolved_authority_class(self) -> Literal["bounded", "general"]:
        """Return the effective authority class for this agent runtime."""
        if self.authority_class is not None:
            return self.authority_class
        if self.runtime == "provider_cli":
            return "general"
        return "bounded"

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        object.__setattr__(self, "_allow_mutation", False)

    @contextmanager
    def _mutations_allowed(self) -> Iterator[None]:
        object.__setattr__(self, "_allow_mutation", True)
        try:
            with ExitStack() as stack:
                for tool in self.tools:
                    if hasattr(tool, "_mutations_allowed"):
                        stack.enter_context(tool._mutations_allowed())
                for template in self.templates.values():
                    if hasattr(template, "_mutations_allowed"):
                        stack.enter_context(template._mutations_allowed())
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
                f"Cannot modify field '{name}' on frozen AgentSection. "
                "AgentSection is immutable after TaskSpec creation."
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        """Mark this instance as frozen."""
        for tool in self.tools:
            if hasattr(tool, "_freeze"):
                tool._freeze()
        for template in self.templates.values():
            if hasattr(template, "_freeze"):
                template._freeze()
        object.__setattr__(self, "templates", _freeze_container_value(self.templates))
        object.__setattr__(self, "options", _freeze_container_value(self.options))
        object.__setattr__(
            self, "runtime_config", _freeze_container_value(self.runtime_config)
        )
        if isinstance(self.output_schema, dict | list | tuple):
            object.__setattr__(
                self, "output_schema", _freeze_container_value(self.output_schema)
            )
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)


class SpecSection(BaseModel):
    """Execution configuration (Spec: [CC-1], [TS-1])."""

    type: Literal["function", "command", "agent"]
    persistent: bool = False
    function_target: str | None = None
    # Spec: process_target is a single executable path; args are appended. [TS-1]
    process_target: str | None = Field(default=None, min_length=1)
    agent: AgentSection | None = None
    parameterization: ParameterizationSection | None = None
    run_input: RunInputSection | None = None
    args: list[Any] = Field(default_factory=list)
    keyword_args: dict[str, Any] = Field(default_factory=dict)
    timeout: float | None = None
    runner: RunnerSection = Field(default_factory=RunnerSection)

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
        """Ensure either function_target or process_target is provided based on type.

        Spec: [TS-1] (Target semantics).
        """
        if self.type == "function" and not self.function_target:
            raise ValueError("function_target is required when type is 'function'")
        if self.type == "command" and not self.process_target:
            raise ValueError("process_target is required when type is 'command'")
        if self.type == "agent" and self.agent is None:
            raise ValueError("agent is required when type is 'agent'")
        if self.type == "function" and self.process_target:
            raise ValueError("process_target should not be set when type is 'function'")
        if self.type == "command" and self.function_target:
            raise ValueError("function_target should not be set when type is 'command'")
        if self.type == "function" and self.agent is not None:
            raise ValueError("agent should not be set when type is 'function'")
        if self.type == "command" and self.agent is not None:
            raise ValueError("agent should not be set when type is 'command'")
        if self.type == "agent" and self.function_target:
            raise ValueError("function_target should not be set when type is 'agent'")
        if self.type == "agent" and self.process_target:
            raise ValueError("process_target should not be set when type is 'agent'")
        if self.type == "agent" and self.interactive:
            raise ValueError("interactive is not supported when type is 'agent'")
        if (
            self.type == "agent"
            and self.agent is not None
            and self.agent.conversation_scope == "per_task"
            and not self.persistent
        ):
            raise ValueError(
                "conversation_scope='per_task' requires spec.persistent=true"
            )
        if (
            self.type == "agent"
            and self.agent is not None
            and self.agent.runtime == "provider_cli"
            and self.persistent
            and self.agent.conversation_scope != "per_task"
        ):
            raise ValueError(
                "provider_cli persistent tasks require conversation_scope='per_task'"
            )
        return self

    @model_validator(mode="after")
    def validate_declared_option_collisions(self) -> SpecSection:
        if self.parameterization is None or self.run_input is None:
            return self
        parameterization_names = _validate_declared_argument_names(
            self.parameterization.arguments,
            section_name="spec.parameterization",
        )
        run_input_names = _validate_declared_argument_names(
            self.run_input.arguments,
            section_name="spec.run_input",
        )
        for option_name, parameter_name in parameterization_names.items():
            run_input_name = run_input_names.get(option_name)
            if run_input_name is not None:
                raise ValueError(
                    "spec.parameterization and spec.run_input argument names "
                    "collide after '_' to '-' normalization: "
                    f"'{parameter_name}' and '{run_input_name}'"
                )
        return self

    @field_validator("process_target")
    @classmethod
    def validate_process_target(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not isinstance(value, str):
            raise TypeError("process_target must be a string")
        if not value.strip():
            raise ValueError("process_target must be a non-empty string")
        return value

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        object.__setattr__(self, "_allow_mutation", False)

    @contextmanager
    def _mutations_allowed(self) -> Iterator[None]:
        object.__setattr__(self, "_allow_mutation", True)
        try:
            with ExitStack() as stack:
                if hasattr(self, "limits") and hasattr(
                    self.limits, "_mutations_allowed"
                ):
                    stack.enter_context(self.limits._mutations_allowed())
                if hasattr(self, "runner") and hasattr(
                    self.runner, "_mutations_allowed"
                ):
                    stack.enter_context(self.runner._mutations_allowed())
                if self.agent is not None and hasattr(self.agent, "_mutations_allowed"):
                    stack.enter_context(self.agent._mutations_allowed())
                if self.parameterization is not None and hasattr(
                    self.parameterization, "_mutations_allowed"
                ):
                    stack.enter_context(self.parameterization._mutations_allowed())
                if self.run_input is not None and hasattr(
                    self.run_input, "_mutations_allowed"
                ):
                    stack.enter_context(self.run_input._mutations_allowed())
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
        if hasattr(self.limits, "_freeze"):
            self.limits._freeze()
        if hasattr(self.runner, "_freeze"):
            self.runner._freeze()
        if self.agent is not None and hasattr(self.agent, "_freeze"):
            self.agent._freeze()
        if self.parameterization is not None and hasattr(
            self.parameterization, "_freeze"
        ):
            self.parameterization._freeze()
        if self.run_input is not None and hasattr(self.run_input, "_freeze"):
            self.run_input._freeze()
        object.__setattr__(self, "args", _freeze_container_value(self.args))
        object.__setattr__(
            self,
            "keyword_args",
            _freeze_container_value(self.keyword_args),
        )
        if self.env is not None:
            object.__setattr__(self, "env", _freeze_container_value(self.env))
        object.__setattr__(self, "_frozen", True)
        object.__setattr__(self, "_allow_mutation", False)


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

        Spec: [TS-1] (io section requirements).
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
        object.__setattr__(self, "inputs", _freeze_container_value(self.inputs))
        object.__setattr__(self, "outputs", _freeze_container_value(self.outputs))
        object.__setattr__(self, "control", _freeze_container_value(self.control))
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
    peak_memory: float | None = Field(None, ge=0, description="Peak memory in MB")
    peak_cpu: int | None = Field(None, ge=0, le=100, description="Peak CPU percentage")
    peak_fds: int | None = Field(None, ge=0)
    peak_net_connections: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def validate_state_consistency(self) -> StateSection:
        """Ensure state fields are consistent and transitions are valid.

        Spec: [TS-1] (state section), [MF-5] (state transitions).
        """

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
        auto_expand: If True (default), resolves defaults before model
            construction for non-template TaskSpecs. Set to False to skip
            resolved-task expansion.
    """

    tid: str | None = Field(
        None,
        description=f"{TASKSPEC_TID_LENGTH}-digit timestamp ID (None for templates)",
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
    _bundle_root: str | None = PrivateAttr(default=None)

    @field_validator("tid")
    @classmethod
    def validate_tid(cls, v: str | None, info: ValidationInfo) -> str | None:
        """Validate TID is a {TASKSPEC_TID_LENGTH}-digit SimpleBroker timestamp.

        Validates:
        1. Exactly 19 digits
        2. Represents a valid nanosecond timestamp
        3. Within reasonable bounds (not too far in future/past)
        """
        import time

        if v is None or v == "":
            if info.context and info.context.get("template"):
                return v
            raise ValueError("tid is required for resolved TaskSpec")

        if not v.isdigit() or len(v) != TASKSPEC_TID_LENGTH:
            raise ValueError(f"tid must be exactly {TASKSPEC_TID_LENGTH} digits")

        # Convert to integer for timestamp validation
        try:
            tid_int = int(v)
        except ValueError as e:
            raise ValueError("tid must be a valid integer") from e

        # Validate timestamp is within reasonable bounds.
        # SimpleBroker uses a hybrid timestamp format compatible with time.time_ns().
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

    @model_validator(mode="before")
    @classmethod
    def prepare_payload(cls, data: Any, info: ValidationInfo) -> Any:
        """Resolve defaults before model construction for resolved TaskSpecs.

        Spec: [TS-1] (Templates vs runtime-expanded specs).
        """
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return data

        context = info.context or {}
        auto_expand = context.get("auto_expand", True)
        if context.get("template") or not auto_expand:
            return copy.deepcopy(dict(data))

        return resolve_taskspec_payload(
            data,
            tid=context.get("resolved_tid"),
            inherited_weft_context=context.get("inherited_weft_context"),
        )

    def model_post_init(self, __context: Any) -> None:
        """Called after the model is initialized.

        Enforces partial immutability after initialization.
        """
        self._consume_bundle_root()
        # Enforce partial immutability after initialization
        self._freeze_spec()

    def _consume_bundle_root(self) -> None:
        extra = getattr(self, "__pydantic_extra__", None)
        if not isinstance(extra, dict):
            return
        if TASKSPEC_BUNDLE_ROOT_FIELD not in extra:
            return
        self._bundle_root = normalize_taskspec_bundle_root(
            extra.pop(TASKSPEC_BUNDLE_ROOT_FIELD)
        )

    def set_bundle_root(self, bundle_root: str | Path | None) -> None:
        """Record the bundle root that owns this TaskSpec, if any."""
        self._bundle_root = normalize_taskspec_bundle_root(bundle_root)

    def get_bundle_root(self) -> str | None:
        """Return the bundle root that owns this TaskSpec, if any."""
        return self._bundle_root

    def is_template(self) -> bool:
        """Return True if this TaskSpec is a template (tid is unset)."""
        return self.tid in (None, "")

    def _freeze_spec(self) -> None:
        """Make spec and io sections immutable after TaskSpec creation.

        This implements the partial immutability design pattern where:
        - tid, spec and io sections become frozen after initialization
        - state and metadata remain mutable for runtime updates

        Spec: [TS-0] (partial immutability), [TS-1].
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
        if self.is_template():
            raise ValueError("cannot derive default queues from template TaskSpec")
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
        """Compatibility shim for older code paths.

        Resolved TaskSpecs are expanded before construction. This method remains
        only as an idempotent compatibility shim for existing callers.
        """
        if self.is_template():
            raise ValueError("cannot apply defaults to a template TaskSpec")
        self._validate_strict_requirements()

    def _validate_strict_requirements(self) -> None:
        """Validate that this TaskSpec meets all REQUIRED fields per the specification.

        Raises:
            ValueError: If any required fields are missing or invalid
        """
        errors = []

        if self.is_template():
            return

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
            elif self.spec.type == "agent" and self.spec.agent is None:
                errors.append("spec.agent is required when type is 'agent'")

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
                f"TaskSpec validation failed after resolution: {'; '.join(errors)}"
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

        Spec: [MF-5], [STATE.1]

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
            "created": {"spawning", "failed", "cancelled"},
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
        self.set_status("spawning")

        if pid is not None:
            self.state.pid = pid

    def mark_running(self, pid: int | None = None) -> None:
        """Mark the task as running with optional PID.

        Args:
            pid: Process ID of the running task
        """
        if self.state.status == "created":
            self.set_status("spawning")
        self.set_status("running")

        if pid is not None:
            self.state.pid = pid

    def mark_completed(self, return_code: int = 0) -> None:
        """Mark the task as completed with return code.

        Args:
            return_code: Process return code (default 0 for success)
        """
        self.set_status("completed")
        self.state.return_code = return_code

    def mark_failed(
        self, error: str | None = None, return_code: int | None = None
    ) -> None:
        """Mark the task as failed with optional error message.

        Args:
            error: Error message describing the failure
            return_code: Process return code if available
        """
        self.set_status("failed", error=error)
        if return_code is not None:
            self.state.return_code = return_code

    def mark_timeout(self, error: str | None = None) -> None:
        """Mark the task as timed out.

        Args:
            error: Optional timeout error message
        """
        # Spec: docs/specifications/06-Resource_Management.md#error-handling
        if error is None:
            error = f"Task timed out after {self.spec.timeout} seconds"
        self.set_status("timeout", error=error)
        self.state.return_code = 124

    def mark_cancelled(self, reason: str | None = None) -> None:
        """Mark the task as cancelled.

        Args:
            reason: Optional cancellation reason
        """
        error = f"Cancelled: {reason}" if reason else None
        self.set_status("cancelled", error=error)

    def mark_killed(self, reason: str | None = None) -> None:
        """Mark the task as killed.

        Args:
            reason: Optional kill reason (e.g., "Memory limit exceeded")
        """
        error = f"Killed: {reason}" if reason else None
        self.set_status("killed", error=error)

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
            if self.state.peak_memory is None or memory > self.state.peak_memory:
                self.state.peak_memory = memory

        if cpu is not None:
            self.state.cpu = cpu
            # Track maximum
            if self.state.peak_cpu is None or cpu > self.state.peak_cpu:
                self.state.peak_cpu = cpu

        if fds is not None:
            self.state.fds = fds
            # Track maximum
            if self.state.peak_fds is None or fds > self.state.peak_fds:
                self.state.peak_fds = fds

        if net_connections is not None:
            self.state.net_connections = net_connections
            # Track maximum
            if (
                self.state.peak_net_connections is None
                or net_connections > self.state.peak_net_connections
            ):
                self.state.peak_net_connections = net_connections

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

        # Then validate with Pydantic - templates are permitted
        TaskSpec.model_validate_json(
            json_str, context={"template": True, "auto_expand": False}
        )
        return True, {}
    except ValidationError as e:
        errors = {}
        for error in e.errors():
            # Build field path from loc tuple
            field_path = ".".join(str(x) for x in error["loc"]) if error["loc"] else ""
            errors[field_path] = error["msg"]
        return False, errors
