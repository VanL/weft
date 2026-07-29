"""Tests for shared spec and pipeline reference resolution helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.provider_cli_fixture import write_provider_cli_wrapper
from tests.taskspec.fixtures import (
    create_valid_function_taskspec,
    create_valid_provider_cli_agent_taskspec,
)
from weft.commands import specs as spec_cmd

pytestmark = pytest.mark.shared


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_validate_task_spec_text_reports_malformed_json_as_schema_error() -> None:
    result = spec_cmd.validate_task_spec_text("{not-json")

    assert result.valid is False
    assert result.payload is None
    assert list(result.errors_by_stage) == ["schema"]
    assert "_json" in result.errors_by_stage["schema"]
    assert result.errors == list(result.errors_by_stage["schema"].values())


def test_validate_spec_source_load_runner_reports_missing_plugin() -> None:
    payload = create_valid_function_taskspec().model_dump(mode="json")
    payload["spec"]["runner"] = {"name": "missing-runner", "options": {}}

    schema_only = spec_cmd.validate_spec_source(payload)
    loaded = spec_cmd.validate_spec_source(payload, load_runner=True)

    assert schema_only.valid is True
    assert loaded.valid is False
    assert "Requested runner 'missing-runner' is not available" in (
        loaded.errors_by_stage["runner"]["runner"]
    )


@pytest.mark.parametrize("option", ["load_runner", "preflight"])
def test_validate_spec_source_rejects_task_options_for_pipeline(option: str) -> None:
    payload = {
        "name": "pipeline",
        "stages": [{"name": "only", "task": "stage1"}],
    }

    result = spec_cmd.validate_spec_source(payload, **{option: True})

    assert result.valid is False
    assert result.errors_by_stage == {
        "options": {
            "options": "--load-runner and --preflight only apply to task specs"
        }
    }


def test_validate_spec_source_preserves_malformed_path_exception(
    tmp_path: Path,
) -> None:
    path = tmp_path / "taskspec.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to read JSON"):
        spec_cmd.validate_spec_source(path)


@pytest.mark.parametrize(
    ("section", "stage"),
    [
        (
            {
                "parameterization": {
                    "adapter_ref": "missing_adapter:build",
                    "arguments": {},
                }
            },
            "parameterization",
        ),
        (
            {
                "run_input": {
                    "adapter_ref": "missing_adapter:build",
                    "arguments": {},
                }
            },
            "run_input",
        ),
    ],
)
def test_validate_spec_source_reports_adapter_stage(
    section: dict[str, object],
    stage: str,
) -> None:
    payload = create_valid_function_taskspec().model_dump(mode="json")
    payload["spec"].update(section)

    result = spec_cmd.validate_spec_source(payload)

    assert result.valid is False
    assert list(result.errors_by_stage) == [stage]
    assert stage in result.errors_by_stage[stage]


def test_validate_spec_source_reports_environment_profile_stage() -> None:
    payload = create_valid_function_taskspec().model_dump(mode="json")
    payload["spec"]["runner"] = {
        "name": "host",
        "options": {},
        "environment_profile_ref": "tests.fixtures.runtime_profiles_fixture:missing",
    }

    result = spec_cmd.validate_spec_source(payload, load_runner=True)

    assert result.valid is False
    assert list(result.errors_by_stage) == ["environment_profile"]


def test_validate_spec_source_preflight_accepts_host_runner() -> None:
    payload = create_valid_function_taskspec().model_dump(mode="json")

    result = spec_cmd.validate_spec_source(payload, preflight=True)

    assert result.valid is True
    assert result.errors_by_stage == {}


def test_validate_spec_source_reports_tool_profile_stage(tmp_path: Path) -> None:
    payload = create_valid_provider_cli_agent_taskspec(
        provider="codex",
        executable=str(write_provider_cli_wrapper(tmp_path, "codex")),
    ).model_dump(mode="json")
    payload["spec"]["agent"]["runtime_config"]["tool_profile_ref"] = (
        "tests.fixtures.runtime_profiles_fixture:unsupported_mcp_tool_profile"
    )

    result = spec_cmd.validate_spec_source(payload, preflight=True)

    assert result.valid is False
    assert list(result.errors_by_stage) == ["tool_profile"]
    assert "does not support explicit MCP server descriptors" in (
        result.errors_by_stage["tool_profile"]["tool_profile"]
    )


def test_validate_spec_source_uses_bundle_root_without_mutating_payload(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "helper_module.py").write_text(
        "def build_work_item(request):\n    return request\n",
        encoding="utf-8",
    )
    payload = {
        "name": "bundle-task",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "run_input": {
                "adapter_ref": "helper_module:build_work_item",
                "arguments": {},
            },
        },
        "metadata": {},
    }
    path = bundle / "taskspec.json"
    _write_json(path, payload)

    result = spec_cmd.validate_spec_source(path)

    assert result.valid is True
    assert result.payload == payload
    assert payload == json.loads(path.read_text(encoding="utf-8"))


def test_validate_spec_source_preserves_all_schema_rows_and_payload() -> None:
    payload = {"name": "", "spec": {"type": "function"}}

    result = spec_cmd.validate_spec_source(payload)

    assert result.valid is False
    assert result.payload == payload
    assert len(result.errors_by_stage["schema"]) >= 2
    assert result.errors == list(result.errors_by_stage["schema"].values())


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
