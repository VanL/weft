"""Pipeline models, validation, and first-class linear runtime compilation.

Spec references:
- docs/specifications/12-Pipeline_Composition_and_UX.md [PL-1], [PL-2], [PL-2.7], [PL-3.2], [PL-4.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-4], [MF-6]
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from weft._constants import (
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
    PIPELINE_EDGE_RUNTIME_METADATA_KEY,
    PIPELINE_OWNER_METADATA_KEY,
    PIPELINE_RUNTIME_METADATA_KEY,
    PIPELINE_STATUS_QUEUE_SUFFIX,
)
from weft.context import WeftContext
from weft.core.spawn_requests import generate_spawn_request_timestamp
from weft.core.taskspec import (
    TaskSpec,
    apply_bundle_root_to_taskspec_payload,
    resolve_taskspec_payload,
)

SUPPORTED_STAGE_DEFAULT_KEYS = frozenset({"input", "args", "keyword_args", "env"})
PIPELINE_PLACEHOLDER_TARGET = "weft.core.tasks.pipeline:runtime"


class PipelineStageDefaults(BaseModel):
    """Supported default overrides for one compiled pipeline stage."""

    model_config = ConfigDict(extra="forbid")

    input: Any = None
    args: list[Any] = Field(default_factory=list)
    keyword_args: dict[str, Any] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)


class PipelineIOOverrides(BaseModel):
    """Explicit queue overrides for expert pipeline routing."""

    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    control: dict[str, str] = Field(default_factory=dict)


class PipelineStage(BaseModel):
    """One authored stage in a linear pipeline."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    task: str = Field(..., min_length=1)
    defaults: PipelineStageDefaults | None = None
    io_overrides: PipelineIOOverrides | None = None

    @model_validator(mode="after")
    def validate_names(self) -> PipelineStage:
        self.name = self.name.strip()
        self.task = self.task.strip()
        if not self.name:
            raise ValueError("stage.name must be a non-empty string")
        if not self.task:
            raise ValueError("stage.task must be a non-empty string")
        return self


class PipelineSpec(BaseModel):
    """Stored pipeline definition."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    stages: list[PipelineStage]

    @model_validator(mode="after")
    def validate_stages(self) -> PipelineSpec:
        if not self.stages:
            raise ValueError("pipeline spec must include a non-empty stages list")
        seen: set[str] = set()
        for stage in self.stages:
            if stage.name in seen:
                raise ValueError(f"duplicate stage name '{stage.name}'")
            seen.add(stage.name)
        return self


class PipelineQueues(BaseModel):
    """Concrete public queue bindings for one pipeline run."""

    model_config = ConfigDict(extra="forbid")

    inbox: str
    outbox: str
    ctrl_in: str
    ctrl_out: str
    status: str
    events: str


class PipelineOwnerConfig(BaseModel):
    """Compile-time metadata for child-to-pipeline owner events."""

    model_config = ConfigDict(extra="forbid")

    pipeline_tid: str
    events_queue: str
    role: str
    stage_name: str | None = None
    edge_name: str | None = None


class CompiledPipelineStage(BaseModel):
    """Runtime stage binding and child identity for one compiled stage."""

    model_config = ConfigDict(extra="forbid")

    name: str
    task: str
    tid: str
    input_queue: str
    outbox_queue: str
    ctrl_in_queue: str
    ctrl_out_queue: str
    taskspec: dict[str, Any]


class CompiledPipelineEdge(BaseModel):
    """Runtime edge binding and child identity for one compiled edge."""

    model_config = ConfigDict(extra="forbid")

    name: str
    tid: str
    source_kind: str
    source_queue: str
    target_queue: str
    upstream_stage: str | None = None
    upstream_tid: str | None = None
    downstream_stage: str | None = None
    downstream_tid: str | None = None
    override_input: Any = None
    emits_pipeline_result: bool = False
    taskspec: dict[str, Any]


class PipelineRuntimePlan(BaseModel):
    """Precompiled runtime plan stored on the internal pipeline task."""

    model_config = ConfigDict(extra="forbid")

    pipeline_name: str
    pipeline_tid: str
    source_ref: str | None = None
    queues: PipelineQueues
    stages: list[CompiledPipelineStage]
    edges: list[CompiledPipelineEdge]


@dataclass(frozen=True)
class CompiledPipelineRun:
    """Concrete compiled pipeline submission artifact."""

    pipeline_tid: str
    pipeline_taskspec: TaskSpec
    runtime: PipelineRuntimePlan
    bootstrap_input_fallback: Any = None


def pipeline_queue_name(pipeline_tid: str, suffix: str) -> str:
    return f"P{pipeline_tid}.{suffix}"


def pipeline_public_queues(pipeline_tid: str) -> PipelineQueues:
    return PipelineQueues(
        inbox=pipeline_queue_name(pipeline_tid, "inbox"),
        outbox=pipeline_queue_name(pipeline_tid, "outbox"),
        ctrl_in=pipeline_queue_name(pipeline_tid, "ctrl_in"),
        ctrl_out=pipeline_queue_name(pipeline_tid, "ctrl_out"),
        status=pipeline_queue_name(pipeline_tid, PIPELINE_STATUS_QUEUE_SUFFIX),
        events=pipeline_queue_name(pipeline_tid, "events"),
    )


def generate_pipeline_example() -> dict[str, Any]:
    return {
        "name": "example-pipeline",
        "stages": [
            {
                "name": "stage-1",
                "task": "task-name",
            }
        ],
    }


def validate_pipeline_spec_payload(
    payload: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    try:
        PipelineSpec.model_validate(dict(payload))
    except ValidationError as exc:
        errors: dict[str, Any] = {}
        for error in exc.errors():
            path = ".".join(str(part) for part in error.get("loc", ()))
            errors[path or "pipeline"] = error.get("msg", "invalid pipeline spec")
        return False, errors
    except ValueError as exc:
        return False, {"pipeline": str(exc)}
    return True, {}


def load_pipeline_spec_payload(payload: Mapping[str, Any]) -> PipelineSpec:
    return PipelineSpec.model_validate(dict(payload))


def _merge_stage_defaults(
    taskspec_payload: Mapping[str, Any],
    stage: PipelineStage,
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(taskspec_payload))
    candidate.pop("tid", None)
    candidate.setdefault("spec", {})
    candidate.setdefault("metadata", {})

    spec_section = candidate.get("spec")
    if not isinstance(spec_section, dict):
        spec_section = {}
        candidate["spec"] = spec_section

    defaults = stage.defaults
    if defaults is not None:
        if defaults.args:
            spec_section.setdefault("args", [])
            if isinstance(spec_section["args"], list):
                spec_section["args"].extend(defaults.args)
        if defaults.keyword_args:
            spec_section.setdefault("keyword_args", {})
            if isinstance(spec_section["keyword_args"], dict):
                spec_section["keyword_args"].update(defaults.keyword_args)
        if defaults.env:
            spec_section.setdefault("env", {})
            if isinstance(spec_section["env"], dict):
                spec_section["env"].update(defaults.env)

    if stage.io_overrides is not None:
        candidate.setdefault("io", {})
        io_section = candidate.get("io")
        if isinstance(io_section, dict):
            for key in ("inputs", "outputs", "control"):
                overrides = getattr(stage.io_overrides, key)
                if overrides:
                    io_section.setdefault(key, {})
                    if isinstance(io_section[key], dict):
                        io_section[key].update(overrides)

    return candidate


def _validate_stage_template(stage: PipelineStage, payload: Mapping[str, Any]) -> None:
    """Validate that a stored stage target is pipeline-compatible.

    Spec: [PL-2.3], [PL-2.7]
    """
    spec_section = payload.get("spec")
    if not isinstance(spec_section, Mapping):
        raise ValueError(f"stage '{stage.name}' is missing a valid spec section")
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}

    runtime_class = metadata.get(INTERNAL_RUNTIME_TASK_CLASS_KEY)
    role = metadata.get("role")
    if runtime_class == INTERNAL_RUNTIME_TASK_CLASS_PIPELINE or role == "pipeline":
        raise ValueError(f"stage '{stage.name}' cannot reference a pipeline task")
    if (
        runtime_class == INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE
        or role == "pipeline_edge"
    ):
        raise ValueError(
            f"stage '{stage.name}' cannot reference an internal pipeline edge"
        )

    if bool(spec_section.get("persistent")):
        raise ValueError(f"stage '{stage.name}' cannot reference a persistent task")
    if bool(spec_section.get("interactive")):
        raise ValueError(f"stage '{stage.name}' cannot reference an interactive task")
    if bool(spec_section.get("stream_output")):
        raise ValueError(f"stage '{stage.name}' cannot use streaming output")


def _stage_owner_config(
    *,
    pipeline_tid: str,
    events_queue: str,
    stage_name: str,
) -> dict[str, Any]:
    return PipelineOwnerConfig(
        pipeline_tid=pipeline_tid,
        events_queue=events_queue,
        role="pipeline_stage",
        stage_name=stage_name,
    ).model_dump(mode="json")


def _edge_owner_config(
    *,
    pipeline_tid: str,
    events_queue: str,
    edge_name: str,
) -> dict[str, Any]:
    return PipelineOwnerConfig(
        pipeline_tid=pipeline_tid,
        events_queue=events_queue,
        role="pipeline_edge",
        edge_name=edge_name,
    ).model_dump(mode="json")


def _build_internal_taskspec_payload(
    *,
    tid: str,
    name: str,
    role: str,
    public_queues: Mapping[str, str],
    runtime_key: str,
    runtime_payload: dict[str, Any],
    internal_task_class: str,
    parent_tid: str | None = None,
    owner_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "role": role,
        runtime_key: runtime_payload,
        INTERNAL_RUNTIME_TASK_CLASS_KEY: internal_task_class,
    }
    if parent_tid is not None:
        metadata["parent_tid"] = parent_tid
    if owner_payload is not None:
        metadata[PIPELINE_OWNER_METADATA_KEY] = owner_payload

    return {
        "tid": tid,
        "name": name,
        "spec": {
            "type": "function",
            "function_target": PIPELINE_PLACEHOLDER_TARGET,
        },
        "io": {
            "inputs": {"inbox": public_queues["inbox"]},
            "outputs": {"outbox": public_queues["outbox"]},
            "control": {
                "ctrl_in": public_queues["ctrl_in"],
                "ctrl_out": public_queues["ctrl_out"],
            },
        },
        "metadata": metadata,
    }


def compile_linear_pipeline(
    pipeline: PipelineSpec,
    *,
    context: WeftContext,
    task_loader: Callable[[str], dict[str, Any]],
    source_ref: str | None = None,
) -> CompiledPipelineRun:
    """Compile a validated pipeline into first-class runtime-owned child tasks."""

    pipeline_tid = str(
        generate_spawn_request_timestamp(
            context.broker_target,
            config=context.broker_config,
        )
    )
    queues = pipeline_public_queues(pipeline_tid)
    inherited_weft_context = str(context.root)

    stage_records: list[CompiledPipelineStage] = []
    edge_records: list[CompiledPipelineEdge] = []
    stage_specs_for_runtime: list[dict[str, Any]] = []
    edge_specs_for_runtime: list[dict[str, Any]] = []

    pipeline_name = pipeline.name or f"pipeline-{pipeline_tid[-10:]}"
    previous_stage: CompiledPipelineStage | None = None
    bootstrap_input_fallback = (
        pipeline.stages[0].defaults.input
        if pipeline.stages[0].defaults is not None
        else None
    )

    for stage in pipeline.stages:
        loaded_payload = task_loader(stage.task)
        _validate_stage_template(stage, loaded_payload)
        merged_payload = _merge_stage_defaults(loaded_payload, stage)
        stage_tid = str(
            generate_spawn_request_timestamp(
                context.broker_target,
                config=context.broker_config,
            )
        )

        if previous_stage is None:
            edge_name = f"pipeline-to-{stage.name}"
            input_queue = pipeline_queue_name(pipeline_tid, edge_name)
        else:
            edge_name = f"{previous_stage.name}-to-{stage.name}"
            input_queue = pipeline_queue_name(pipeline_tid, edge_name)

        stage_payload = copy.deepcopy(merged_payload)
        stage_payload.setdefault("io", {})
        io_section = stage_payload.get("io")
        if not isinstance(io_section, dict):
            io_section = {}
            stage_payload["io"] = io_section
        io_section.setdefault("inputs", {})
        if isinstance(io_section["inputs"], dict):
            io_section["inputs"]["inbox"] = input_queue
        stage_payload.setdefault("metadata", {})
        if isinstance(stage_payload["metadata"], dict):
            stage_payload["metadata"]["parent_tid"] = pipeline_tid
            stage_payload["metadata"]["role"] = "pipeline_stage"
            stage_payload["metadata"][PIPELINE_OWNER_METADATA_KEY] = (
                _stage_owner_config(
                    pipeline_tid=pipeline_tid,
                    events_queue=queues.events,
                    stage_name=stage.name,
                )
            )
        resolved_stage_payload = resolve_taskspec_payload(
            stage_payload,
            tid=stage_tid,
            inherited_weft_context=inherited_weft_context,
        )
        stage_taskspec = TaskSpec.model_validate(
            resolved_stage_payload,
            context={"auto_expand": False},
        )
        stage_record = CompiledPipelineStage(
            name=stage.name,
            task=stage.task,
            tid=stage_tid,
            input_queue=input_queue,
            outbox_queue=stage_taskspec.io.outputs["outbox"],
            ctrl_in_queue=stage_taskspec.io.control["ctrl_in"],
            ctrl_out_queue=stage_taskspec.io.control["ctrl_out"],
            taskspec=apply_bundle_root_to_taskspec_payload(
                stage_taskspec.model_dump(mode="json"),
                stage_taskspec.get_bundle_root(),
            ),
        )
        stage_records.append(stage_record)
        stage_specs_for_runtime.append(stage_record.taskspec)

        source_queue = (
            queues.inbox if previous_stage is None else previous_stage.outbox_queue
        )
        source_kind = "pipeline_input" if previous_stage is None else "stage_output"
        edge_tid = str(
            generate_spawn_request_timestamp(
                context.broker_target,
                config=context.broker_config,
            )
        )
        edge_runtime_payload = {
            "pipeline_tid": pipeline_tid,
            "edge_name": edge_name,
            "source_kind": source_kind,
            "source_queue": source_queue,
            "target_queue": input_queue,
            "events_queue": queues.events,
            "upstream_stage": previous_stage.name
            if previous_stage is not None
            else None,
            "upstream_tid": previous_stage.tid if previous_stage is not None else None,
            "downstream_stage": stage.name,
            "downstream_tid": stage_tid,
            "override_input": (
                stage.defaults.input
                if stage.defaults is not None
                and "input" in stage.defaults.model_fields_set
                else None
            ),
            "emits_pipeline_result": False,
        }
        edge_payload = _build_internal_taskspec_payload(
            tid=edge_tid,
            name=edge_name,
            role="pipeline_edge",
            public_queues={
                "inbox": source_queue,
                "outbox": input_queue,
                "ctrl_in": f"T{edge_tid}.ctrl_in",
                "ctrl_out": f"T{edge_tid}.ctrl_out",
            },
            runtime_key=PIPELINE_EDGE_RUNTIME_METADATA_KEY,
            runtime_payload=edge_runtime_payload,
            internal_task_class=INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
            parent_tid=pipeline_tid,
            owner_payload=_edge_owner_config(
                pipeline_tid=pipeline_tid,
                events_queue=queues.events,
                edge_name=edge_name,
            ),
        )
        edge_taskspec = TaskSpec.model_validate(
            resolve_taskspec_payload(
                edge_payload,
                tid=edge_tid,
                inherited_weft_context=inherited_weft_context,
            ),
            context={"auto_expand": False},
        )
        edge_record = CompiledPipelineEdge(
            name=edge_name,
            tid=edge_tid,
            source_kind=source_kind,
            source_queue=source_queue,
            target_queue=input_queue,
            upstream_stage=previous_stage.name if previous_stage is not None else None,
            upstream_tid=previous_stage.tid if previous_stage is not None else None,
            downstream_stage=stage.name,
            downstream_tid=stage_tid,
            override_input=edge_runtime_payload["override_input"],
            emits_pipeline_result=False,
            taskspec=edge_taskspec.model_dump(mode="json"),
        )
        edge_records.append(edge_record)
        edge_specs_for_runtime.append(edge_record.taskspec)
        previous_stage = stage_record

    assert previous_stage is not None
    exit_edge_name = f"{previous_stage.name}-to-pipeline"
    exit_edge_tid = str(
        generate_spawn_request_timestamp(
            context.broker_target,
            config=context.broker_config,
        )
    )
    exit_edge_runtime_payload = {
        "pipeline_tid": pipeline_tid,
        "edge_name": exit_edge_name,
        "source_kind": "stage_output",
        "source_queue": previous_stage.outbox_queue,
        "target_queue": queues.outbox,
        "events_queue": queues.events,
        "upstream_stage": previous_stage.name,
        "upstream_tid": previous_stage.tid,
        "downstream_stage": None,
        "downstream_tid": None,
        "override_input": None,
        "emits_pipeline_result": True,
    }
    exit_edge_payload = _build_internal_taskspec_payload(
        tid=exit_edge_tid,
        name=exit_edge_name,
        role="pipeline_edge",
        public_queues={
            "inbox": previous_stage.outbox_queue,
            "outbox": queues.outbox,
            "ctrl_in": f"T{exit_edge_tid}.ctrl_in",
            "ctrl_out": f"T{exit_edge_tid}.ctrl_out",
        },
        runtime_key=PIPELINE_EDGE_RUNTIME_METADATA_KEY,
        runtime_payload=exit_edge_runtime_payload,
        internal_task_class=INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
        parent_tid=pipeline_tid,
        owner_payload=_edge_owner_config(
            pipeline_tid=pipeline_tid,
            events_queue=queues.events,
            edge_name=exit_edge_name,
        ),
    )
    exit_edge_taskspec = TaskSpec.model_validate(
        resolve_taskspec_payload(
            exit_edge_payload,
            tid=exit_edge_tid,
            inherited_weft_context=inherited_weft_context,
        ),
        context={"auto_expand": False},
    )
    exit_edge_record = CompiledPipelineEdge(
        name=exit_edge_name,
        tid=exit_edge_tid,
        source_kind="stage_output",
        source_queue=previous_stage.outbox_queue,
        target_queue=queues.outbox,
        upstream_stage=previous_stage.name,
        upstream_tid=previous_stage.tid,
        downstream_stage=None,
        downstream_tid=None,
        override_input=None,
        emits_pipeline_result=True,
        taskspec=exit_edge_taskspec.model_dump(mode="json"),
    )
    edge_records.append(exit_edge_record)
    edge_specs_for_runtime.append(exit_edge_record.taskspec)

    runtime_plan = PipelineRuntimePlan(
        pipeline_name=pipeline_name,
        pipeline_tid=pipeline_tid,
        source_ref=source_ref,
        queues=queues,
        stages=stage_records,
        edges=edge_records,
    )
    pipeline_payload = _build_internal_taskspec_payload(
        tid=pipeline_tid,
        name=pipeline_name,
        role="pipeline",
        public_queues=queues.model_dump(mode="json"),
        runtime_key=PIPELINE_RUNTIME_METADATA_KEY,
        runtime_payload={
            **runtime_plan.model_dump(mode="json"),
            "stage_taskspecs": stage_specs_for_runtime,
            "edge_taskspecs": edge_specs_for_runtime,
        },
        internal_task_class=INTERNAL_RUNTIME_TASK_CLASS_PIPELINE,
    )
    pipeline_taskspec = TaskSpec.model_validate(
        resolve_taskspec_payload(
            pipeline_payload,
            tid=pipeline_tid,
            inherited_weft_context=inherited_weft_context,
        ),
        context={"auto_expand": False},
    )
    return CompiledPipelineRun(
        pipeline_tid=pipeline_tid,
        pipeline_taskspec=pipeline_taskspec,
        runtime=runtime_plan,
        bootstrap_input_fallback=bootstrap_input_fallback,
    )
