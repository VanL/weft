"""Tests for pipeline models and first-class runtime compilation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.commands import specs as spec_cmd
from weft.context import build_context
from weft.core.pipelines import (
    PIPELINE_PLACEHOLDER_TARGET,
    compile_linear_pipeline,
    load_pipeline_spec_payload,
    pipeline_public_queues,
    validate_pipeline_spec_payload,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _task_payload(
    *,
    function_target: str = "tests.tasks.sample_targets:echo_payload",
    persistent: bool = False,
    interactive: bool = False,
    stream_output: bool = False,
) -> dict[str, Any]:
    return {
        "name": "task",
        "spec": {
            "type": "function",
            "function_target": function_target,
            "persistent": persistent,
            "interactive": interactive,
            "stream_output": stream_output,
        },
        "metadata": {},
    }


def _load_task(root: Path, name: str) -> dict[str, Any]:
    kind, _path, payload = spec_cmd.load_spec(
        name,
        spec_type=spec_cmd.SPEC_TYPE_TASK,
        context_path=root,
    )
    assert kind == spec_cmd.SPEC_TYPE_TASK
    return payload


def test_pipeline_spec_requires_non_empty_stages() -> None:
    valid, errors = validate_pipeline_spec_payload({"name": "pipe", "stages": []})

    assert valid is False
    assert errors


def test_pipeline_spec_rejects_duplicate_stage_names() -> None:
    valid, errors = validate_pipeline_spec_payload(
        {
            "name": "pipe",
            "stages": [
                {"name": "dup", "task": "a"},
                {"name": "dup", "task": "b"},
            ],
        }
    )

    assert valid is False
    assert "pipeline" in errors or any(
        "duplicate" in str(value) for value in errors.values()
    )


def test_pipeline_spec_requires_task_reference() -> None:
    valid, errors = validate_pipeline_spec_payload(
        {"name": "pipe", "stages": [{"name": "one"}]}
    )

    assert valid is False
    assert errors


def test_validate_spec_uses_shared_pipeline_validator(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.json"
    _write_json(path, {"name": "pipe", "stages": [{"name": "dup", "task": ""}]})

    valid, errors = spec_cmd.validate_spec(path, spec_type=spec_cmd.SPEC_TYPE_PIPELINE)

    assert valid is False
    assert errors


def test_pipeline_compiler_builds_public_p_queues(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "stage1.json", _task_payload())
    pipeline = load_pipeline_spec_payload(
        {"name": "pipe", "stages": [{"name": "one", "task": "stage1"}]}
    )

    compiled = compile_linear_pipeline(
        pipeline,
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
        source_ref="pipe",
    )

    queues = pipeline_public_queues(compiled.pipeline_tid)
    assert compiled.runtime.queues == queues
    assert compiled.runtime.queues.status == f"P{compiled.pipeline_tid}.status"
    pipeline_payload = compiled.pipeline_taskspec.model_dump(mode="json")
    assert pipeline_payload["io"]["inputs"]["inbox"] == queues.inbox
    assert pipeline_payload["io"]["outputs"]["outbox"] == queues.outbox
    assert pipeline_payload["metadata"]["_weft_runtime_task_class"] == "pipeline"
    assert (
        pipeline_payload["metadata"]["_weft_pipeline_runtime"]["pipeline_tid"]
        == compiled.pipeline_tid
    )
    assert pipeline_payload["spec"]["function_target"] == PIPELINE_PLACEHOLDER_TARGET
    assert pipeline_payload["spec"]["weft_context"] == str(ctx.root)


def test_pipeline_compiler_assigns_stable_child_tids_by_stage_and_edge_name(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "first.json", _task_payload())
    _write_json(ctx.weft_dir / "tasks" / "second.json", _task_payload())
    pipeline = load_pipeline_spec_payload(
        {
            "name": "pipe",
            "stages": [
                {"name": "first", "task": "first"},
                {"name": "second", "task": "second"},
            ],
        }
    )

    compiled = compile_linear_pipeline(
        pipeline,
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )

    stage_tids = {stage.name: stage.tid for stage in compiled.runtime.stages}
    edge_tids = {edge.name: edge.tid for edge in compiled.runtime.edges}
    assert set(stage_tids) == {"first", "second"}
    assert set(edge_tids) == {
        "pipeline-to-first",
        "first-to-second",
        "second-to-pipeline",
    }
    assert len(set(stage_tids.values()) | set(edge_tids.values())) == 5
    for stage in compiled.runtime.stages:
        assert stage.taskspec["metadata"]["parent_tid"] == compiled.pipeline_tid
        assert stage.taskspec["metadata"]["role"] == "pipeline_stage"
        assert stage.taskspec["spec"]["weft_context"] == str(ctx.root)
    for edge in compiled.runtime.edges:
        assert edge.taskspec["metadata"]["parent_tid"] == compiled.pipeline_tid
        assert edge.taskspec["metadata"]["role"] == "pipeline_edge"
        assert edge.taskspec["spec"]["weft_context"] == str(ctx.root)


def test_pipeline_compiler_overwrites_stage_input_queue_with_edge_binding(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(
        ctx.weft_dir / "tasks" / "stage1.json",
        {
            "name": "stage1",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "io": {
                "inputs": {"inbox": "custom.stage1.inbox"},
                "outputs": {"outbox": "custom.stage1.outbox"},
                "control": {
                    "ctrl_in": "custom.stage1.ctrl_in",
                    "ctrl_out": "custom.stage1.ctrl_out",
                },
            },
            "metadata": {},
        },
    )
    pipeline = load_pipeline_spec_payload(
        {"name": "pipe", "stages": [{"name": "one", "task": "stage1"}]}
    )

    compiled = compile_linear_pipeline(
        pipeline,
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )

    stage = compiled.runtime.stages[0]
    assert stage.input_queue == f"P{compiled.pipeline_tid}.pipeline-to-one"
    assert stage.taskspec["io"]["inputs"]["inbox"] == stage.input_queue
    assert stage.taskspec["io"]["outputs"]["outbox"] == "custom.stage1.outbox"
    assert stage.taskspec["io"]["control"]["ctrl_in"] == "custom.stage1.ctrl_in"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_task_payload(persistent=True), "persistent"),
        (_task_payload(interactive=True), "interactive"),
        (_task_payload(stream_output=True), "streaming"),
    ],
)
def test_pipeline_compiler_rejects_incompatible_stage_task(
    tmp_path: Path,
    payload: dict[str, Any],
    message: str,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "bad.json", payload)
    pipeline = load_pipeline_spec_payload(
        {"name": "pipe", "stages": [{"name": "bad", "task": "bad"}]}
    )

    with pytest.raises(ValueError, match=message):
        compile_linear_pipeline(
            pipeline,
            context=ctx,
            task_loader=lambda name: _load_task(root, name),
        )


def test_pipeline_compiler_rejects_nested_pipeline_stage_task(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(
        ctx.weft_dir / "tasks" / "nested.json",
        {
            "name": "nested-pipeline",
            "spec": {
                "type": "function",
                "function_target": PIPELINE_PLACEHOLDER_TARGET,
            },
            "metadata": {
                "role": "pipeline",
                "_weft_runtime_task_class": "pipeline",
            },
        },
    )
    pipeline = load_pipeline_spec_payload(
        {"name": "pipe", "stages": [{"name": "nested", "task": "nested"}]}
    )

    with pytest.raises(ValueError, match="pipeline task"):
        compile_linear_pipeline(
            pipeline,
            context=ctx,
            task_loader=lambda name: _load_task(root, name),
        )
