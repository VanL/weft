"""Public TaskSpec schema and submission-time materialization surface.

Spec references:
- docs/specifications/01-Core_Components.md [CC-1]
- docs/specifications/02-TaskSpec.md [TS-1], [TS-1.3]
"""

from __future__ import annotations

from .model import (
    AgentSection,
    AgentTemplateSection,
    AgentToolSection,
    FrozenDict,
    FrozenList,
    IOSection,
    LimitsSection,
    ParameterizationArgumentSection,
    ParameterizationSection,
    ReservedPolicy,
    RunInputArgumentSection,
    RunInputSection,
    RunnerSection,
    SpecSection,
    StateSection,
    TaskSpec,
    resolve_taskspec_payload,
    rewrite_tid_in_io,
    validate_taskspec,
)
from .parameterization import (
    SpecParameterizationRequest,
    invoke_parameterization_adapter,
    materialize_taskspec_template,
    parse_declared_parameterization_args,
    validate_parameterization_adapter,
    validate_parameterization_adapter_ref,
)
from .run_input import (
    RUN_COMMAND_RESERVED_OPTION_NAMES,
    ParsedDeclaredOptions,
    ensure_json_serializable_work_payload,
    invoke_run_input_adapter,
    normalize_declared_option_name,
    normalize_run_input_value,
    parse_declared_option_args,
    parse_declared_run_input_args,
    validate_run_input_adapter,
    validate_run_input_adapter_ref,
)
from .transport import (
    decode_taskspec_transport_payload,
    encode_taskspec_transport_payload,
    validate_taskspec_payload,
)

__all__ = [
    "RUN_COMMAND_RESERVED_OPTION_NAMES",
    "AgentSection",
    "AgentTemplateSection",
    "AgentToolSection",
    "FrozenDict",
    "FrozenList",
    "IOSection",
    "LimitsSection",
    "ParameterizationArgumentSection",
    "ParameterizationSection",
    "ParsedDeclaredOptions",
    "ReservedPolicy",
    "RunInputArgumentSection",
    "RunInputSection",
    "RunnerSection",
    "SpecParameterizationRequest",
    "SpecSection",
    "StateSection",
    "TaskSpec",
    "decode_taskspec_transport_payload",
    "encode_taskspec_transport_payload",
    "ensure_json_serializable_work_payload",
    "invoke_parameterization_adapter",
    "invoke_run_input_adapter",
    "materialize_taskspec_template",
    "normalize_declared_option_name",
    "normalize_run_input_value",
    "parse_declared_option_args",
    "parse_declared_parameterization_args",
    "parse_declared_run_input_args",
    "resolve_taskspec_payload",
    "rewrite_tid_in_io",
    "validate_parameterization_adapter",
    "validate_parameterization_adapter_ref",
    "validate_run_input_adapter",
    "validate_run_input_adapter_ref",
    "validate_taskspec",
    "validate_taskspec_payload",
]
