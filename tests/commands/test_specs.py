"""Tests for shared spec and pipeline reference resolution helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from weft.commands import specs as spec_cmd

pytestmark = pytest.mark.shared


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_resolve_named_spec_supports_task_bundle(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".weft" / "tasks" / "bundle-task" / "taskspec.json",
        {
            "name": "bundle-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )

    resolved = spec_cmd.resolve_named_spec(
        "bundle-task",
        spec_type=spec_cmd.SPEC_TYPE_TASK,
        context_path=tmp_path,
    )

    assert resolved.name == "bundle-task"
    assert resolved.source == spec_cmd.SPEC_SOURCE_STORED
    assert resolved.path.as_posix().endswith(".weft/tasks/bundle-task/taskspec.json")
    assert resolved.bundle_root == tmp_path / ".weft" / "tasks" / "bundle-task"


def test_resolve_named_spec_supports_pipeline_bundle(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".weft" / "pipelines" / "bundle-pipeline" / "pipeline.json",
        {
            "name": "bundle-pipeline",
            "stages": [{"name": "only", "task": "stage1"}],
        },
    )

    resolved = spec_cmd.resolve_named_spec(
        "bundle-pipeline",
        spec_type=spec_cmd.SPEC_TYPE_PIPELINE,
        context_path=tmp_path,
    )

    assert resolved.name == "bundle-pipeline"
    assert resolved.source == spec_cmd.SPEC_SOURCE_STORED
    assert resolved.path.as_posix().endswith(
        ".weft/pipelines/bundle-pipeline/pipeline.json"
    )
    assert resolved.bundle_root == tmp_path / ".weft" / "pipelines" / "bundle-pipeline"


def test_resolve_spec_reference_accepts_task_bundle_directory_path(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle-task"
    _write_json(
        bundle_dir / "taskspec.json",
        {
            "name": "bundle-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )

    resolved = spec_cmd.resolve_spec_reference(
        bundle_dir,
        spec_type=spec_cmd.SPEC_TYPE_TASK,
        context_path=tmp_path,
    )

    assert resolved.name == "bundle-task"
    assert resolved.source == spec_cmd.SPEC_SOURCE_FILE
    assert resolved.path == bundle_dir / "taskspec.json"
    assert resolved.bundle_root == bundle_dir


def test_resolve_spec_reference_accepts_pipeline_bundle_directory_path(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle-pipeline"
    _write_json(
        bundle_dir / "pipeline.json",
        {
            "name": "bundle-pipeline",
            "stages": [{"name": "only", "task": "stage1"}],
        },
    )

    resolved = spec_cmd.resolve_spec_reference(
        bundle_dir,
        spec_type=spec_cmd.SPEC_TYPE_PIPELINE,
        context_path=tmp_path,
    )

    assert resolved.name == "bundle-pipeline"
    assert resolved.source == spec_cmd.SPEC_SOURCE_FILE
    assert resolved.path == bundle_dir / "pipeline.json"
    assert resolved.bundle_root == bundle_dir


def test_resolve_named_spec_prefers_flat_file_over_bundle(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".weft" / "tasks" / "same-name.json",
        {
            "name": "flat",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {"shape": "file"},
        },
    )
    _write_json(
        tmp_path / ".weft" / "tasks" / "same-name" / "taskspec.json",
        {
            "name": "bundle",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:simulate_work",
            },
            "metadata": {"shape": "bundle"},
        },
    )

    resolved = spec_cmd.resolve_named_spec(
        "same-name",
        spec_type=spec_cmd.SPEC_TYPE_TASK,
        context_path=tmp_path,
    )

    assert resolved.path.as_posix().endswith(".weft/tasks/same-name.json")
    assert resolved.payload["metadata"]["shape"] == "file"


def test_list_specs_includes_bundle_entries(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".weft" / "tasks" / "bundle-task" / "taskspec.json",
        {
            "name": "bundle-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )
    _write_json(
        tmp_path / ".weft" / "pipelines" / "bundle-pipeline" / "pipeline.json",
        {
            "name": "bundle-pipeline",
            "stages": [{"name": "only", "task": "bundle-task"}],
        },
    )

    listed = spec_cmd.list_specs(context_path=tmp_path)

    assert {
        "type": spec_cmd.SPEC_TYPE_TASK,
        "name": "bundle-task",
        "path": str(tmp_path / ".weft" / "tasks" / "bundle-task" / "taskspec.json"),
        "source": spec_cmd.SPEC_SOURCE_STORED,
    } in listed
    assert {
        "type": spec_cmd.SPEC_TYPE_PIPELINE,
        "name": "bundle-pipeline",
        "path": str(
            tmp_path / ".weft" / "pipelines" / "bundle-pipeline" / "pipeline.json"
        ),
        "source": spec_cmd.SPEC_SOURCE_STORED,
    } in listed
