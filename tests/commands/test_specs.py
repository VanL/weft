"""Tests for shared spec and pipeline reference resolution helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures.provider_cli_fixture import write_provider_cli_wrapper
from tests.taskspec.fixtures import (
    create_valid_function_taskspec,
    create_valid_provider_cli_agent_taskspec,
)
from weft.commands import specs as spec_cmd
from weft.commands.types import SpecMutationResult, SpecRecord, SpecValidationResult

pytestmark = pytest.mark.shared


def test_canonical_spec_commands_return_structured_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record = SpecRecord(
        spec_type="task",
        name="demo",
        path=tmp_path / "demo.json",
        source="stored",
    )
    validation = SpecValidationResult(valid=True, spec_type="task")
    resolved = SimpleNamespace(
        spec_type="task",
        name="demo",
        path=record.path,
        source="stored",
        payload={"name": "demo"},
    )

    monkeypatch.setattr(spec_cmd, "create_spec_record", lambda *args, **kwargs: record)
    monkeypatch.setattr(spec_cmd, "list_spec_records", lambda **kwargs: [record])
    monkeypatch.setattr(
        spec_cmd, "resolve_named_spec", lambda *args, **kwargs: resolved
    )
    monkeypatch.setattr(spec_cmd, "delete_spec", lambda *args, **kwargs: record.path)
    monkeypatch.setattr(
        spec_cmd, "validate_spec_source", lambda *args, **kwargs: validation
    )
    monkeypatch.setattr(
        spec_cmd, "generate_spec", lambda spec_type: {"type": spec_type}
    )

    assert spec_cmd.cmd_spec_create("demo", file=record.path) == SpecMutationResult(
        action="create", record=record
    )
    assert spec_cmd.cmd_spec_list() == (record,)
    assert spec_cmd.cmd_spec_show("demo") == SpecRecord(
        spec_type="task",
        name="demo",
        path=record.path,
        source="stored",
        payload={"name": "demo"},
    )
    assert spec_cmd.cmd_spec_delete("demo") == SpecMutationResult(
        action="delete", record=record
    )
    assert spec_cmd.cmd_spec_validate(record.path) is validation
    assert spec_cmd.cmd_spec_generate() == {"type": "task"}


def test_canonical_spec_commands_translate_invalid_type_to_typed_usage_error() -> None:
    from weft._exceptions import CommandUsageError

    with pytest.raises(CommandUsageError, match="Unknown spec type"):
        spec_cmd.cmd_spec_generate(type="unknown")


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
    assert (
        "Requested runner 'missing-runner' is not available"
        in (loaded.errors_by_stage["runner"]["runner"])
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
        "options": {"options": "--load-runner and --preflight only apply to task specs"}
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
    assert (
        "does not support explicit MCP server descriptors"
        in (result.errors_by_stage["tool_profile"]["tool_profile"])
    )


@pytest.mark.parametrize(
    ("validator_name", "stage", "field", "agent_runtime"),
    [
        (
            "_validate_taskspec_parameterization",
            "parameterization",
            "parameterization",
            None,
        ),
        ("_validate_taskspec_run_input", "run_input", "run_input", None),
        (
            "validate_taskspec_runner_environment",
            "environment_profile",
            "environment_profile",
            None,
        ),
        ("validate_taskspec_runner", "runner", "runner", None),
        (
            "validate_taskspec_agent_runtime",
            "agent_runtime",
            "agent_runtime",
            "llm",
        ),
        (
            "validate_taskspec_agent_tool_profile",
            "tool_profile",
            "tool_profile",
            "provider_cli",
        ),
    ],
)
def test_validate_spec_source_contains_open_validation_probe_failures_by_stage(
    monkeypatch: pytest.MonkeyPatch,
    validator_name: str,
    stage: str,
    field: str,
    agent_runtime: str | None,
) -> None:
    """Each extensible validation stage converts arbitrary failures locally."""
    payload = create_valid_function_taskspec().model_dump(mode="json")
    if agent_runtime is not None:
        payload["spec"] = {
            "type": "agent",
            "agent": {
                "runtime": agent_runtime,
                **(
                    {"authority_class": "general"}
                    if agent_runtime == "provider_cli"
                    else {}
                ),
                "runtime_config": (
                    {"provider": "codex", "executable": "unused-provider"}
                    if agent_runtime == "provider_cli"
                    else {"plugin_modules": ["unused.runtime"]}
                ),
            },
        }

    validator_names = (
        "_validate_taskspec_parameterization",
        "_validate_taskspec_run_input",
        "validate_taskspec_runner_environment",
        "validate_taskspec_runner",
        "validate_taskspec_agent_runtime",
        "validate_taskspec_agent_tool_profile",
    )
    for name in validator_names:
        monkeypatch.setattr(spec_cmd, name, lambda *_args, **_kwargs: None)

    failure_message = f"{stage} extension failed"

    def raise_extension_failure(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(failure_message)

    monkeypatch.setattr(spec_cmd, validator_name, raise_extension_failure)

    result = spec_cmd.validate_spec_source(payload, load_runner=True)

    assert result.valid is False
    assert result.payload == payload
    assert result.errors == [failure_message]
    assert result.errors_by_stage == {stage: {field: failure_message}}
    assert "Traceback" not in repr(result)


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
