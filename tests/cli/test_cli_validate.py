"""Black-box CLI tests for `weft spec validate --type task`."""

from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from tests.conftest import run_cli  # re-exported for clarity
from tests.fixtures.provider_cli_fixture import (
    PROVIDER_FIXTURE_NAMES,
    write_provider_cli_wrapper,
)
from tests.taskspec.fixtures import (
    create_valid_agent_taskspec,
    create_valid_command_taskspec,
    create_valid_function_taskspec,
    create_valid_provider_cli_agent_taskspec,
)
from weft.cli import validate_taskspec as validate_taskspec_cmd

pytestmark = [pytest.mark.shared]
_MODEL_PROVIDERS = frozenset({"claude_code", "codex", "gemini", "opencode", "qwen"})


class NamedSpecResolutionFailure(Exception):
    """Ordinary failure from the named-spec resolution boundary."""


class NamedSpecResolutionSignal(BaseException):
    """Fatal signal that named-spec resolution must not contain."""


def write_taskspec(path: Path, spec: Any) -> None:
    if isinstance(spec, dict):
        path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return
    path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")


def test_validate_taskspec_success(workdir):
    """Valid TaskSpec should pass validation via CLI."""
    taskspec = create_valid_function_taskspec()
    spec_path = workdir / "taskspec.json"
    write_taskspec(spec_path, taskspec)

    rc, out, err = run_cli("spec", "validate", "--type", "task", spec_path, cwd=workdir)

    assert rc == 0
    assert "TaskSpec is valid" in out
    assert err == ""


def test_validate_taskspec_failure(workdir):
    """Invalid TaskSpec should fail validation and report errors."""
    taskspec = create_valid_function_taskspec()
    payload = taskspec.model_dump(mode="json")
    # Write an invalid payload rather than mutating a frozen resolved TaskSpec.
    payload["io"]["outputs"].pop("outbox", None)

    spec_path = workdir / "invalid_taskspec.json"
    write_taskspec(spec_path, payload)

    rc, out, _err = run_cli(
        "spec", "validate", "--type", "task", spec_path, cwd=workdir
    )

    assert rc != 0
    assert "TaskSpec validation failed" in out
    assert "outbox" in out


def test_validate_taskspec_rejects_unknown_top_level_field_without_traceback(
    workdir: Path,
) -> None:
    payload = create_valid_function_taskspec().model_dump(mode="json")
    payload["unsupported"] = True
    spec_path = workdir / "unknown-top-level.json"
    write_taskspec(spec_path, payload)

    rc, out, err = run_cli("spec", "validate", "--type", "task", spec_path, cwd=workdir)

    assert rc == 1
    assert "TaskSpec validation failed" in out
    assert "unsupported" in out
    assert "Traceback" not in out
    assert err == ""


def test_validate_taskspec_rejects_omitted_provider_authority_without_traceback(
    workdir: Path,
) -> None:
    spec_path = workdir / "missing-provider-authority.json"
    write_taskspec(
        spec_path,
        {
            "name": "missing-provider-authority",
            "spec": {
                "type": "agent",
                "agent": {
                    "runtime": "provider_cli",
                    "runtime_config": {"provider": "codex"},
                },
            },
        },
    )

    rc, out, err = run_cli("spec", "validate", "--type", "task", spec_path, cwd=workdir)

    assert rc == 1
    assert "TaskSpec validation failed" in out
    assert "explicit authority_class" in out
    assert "Traceback" not in out
    assert err == ""


@pytest.mark.parametrize("approval_required", [True, False])
def test_validate_taskspec_rejects_removed_approval_field_without_traceback(
    workdir: Path,
    approval_required: bool,
) -> None:
    spec_path = workdir / f"approval-{str(approval_required).lower()}.json"
    write_taskspec(
        spec_path,
        {
            "name": "removed-approval-field",
            "spec": {
                "type": "agent",
                "agent": {
                    "runtime": "llm",
                    "tools": [
                        {
                            "name": "echo",
                            "kind": "python",
                            "ref": "tests.tasks.sample_targets:echo_payload",
                            "approval_required": approval_required,
                        }
                    ],
                },
            },
        },
    )

    rc, out, err = run_cli("spec", "validate", "--type", "task", spec_path, cwd=workdir)

    assert rc == 1
    assert "TaskSpec validation failed" in out
    assert "approval_required" in out
    assert "Traceback" not in out
    assert err == ""


def test_validate_taskspec_missing_explicit_file_preserves_exit_contract(
    workdir,
) -> None:
    missing = workdir / "missing-taskspec.json"
    env = os.environ.copy()
    env.update({"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"})

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        missing,
        cwd=workdir,
        env=env,
    )

    assert rc == 1
    assert out == f"Error: File not found: {missing}"
    assert err == ""


def test_validate_taskspec_reports_ordinary_named_spec_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named-spec resolver failures become the command's existing read error."""

    output = StringIO()
    monkeypatch.setattr(
        validate_taskspec_cmd,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )

    def fail_resolution(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise NamedSpecResolutionFailure("resolver detail")

    monkeypatch.setattr(
        validate_taskspec_cmd.spec_cmd,
        "resolve_spec_reference",
        fail_resolution,
    )

    assert validate_taskspec_cmd._resolve_taskspec_source(Path("stored-task")) is None
    assert output.getvalue() == "Error reading file: resolver detail\n"


def test_validate_taskspec_propagates_fatal_named_spec_resolution_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command boundary contains ordinary failures, not BaseException signals."""

    output = StringIO()
    monkeypatch.setattr(
        validate_taskspec_cmd,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    signal = NamedSpecResolutionSignal()

    def fail_resolution(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise signal

    monkeypatch.setattr(
        validate_taskspec_cmd.spec_cmd,
        "resolve_spec_reference",
        fail_resolution,
    )

    with pytest.raises(NamedSpecResolutionSignal) as exc_info:
        validate_taskspec_cmd._resolve_taskspec_source(Path("stored-task"))

    assert exc_info.value is signal
    assert output.getvalue() == ""


@pytest.mark.parametrize("option", ["--load-runner", "--preflight"])
def test_validate_pipeline_rejects_task_only_option(workdir, option: str) -> None:
    path = workdir / "pipeline.json"
    write_taskspec(
        path,
        {
            "name": "pipeline",
            "stages": [{"name": "only", "task": "stage1"}],
        },
    )

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "pipeline",
        path,
        option,
        cwd=workdir,
    )

    assert rc == 2
    assert out == ""
    assert err == "--load-runner and --preflight only apply to task specs"


def test_validate_taskspec_agent_summary(workdir):
    taskspec = create_valid_agent_taskspec()
    spec_path = workdir / "agent_taskspec.json"
    write_taskspec(spec_path, taskspec)

    rc, out, err = run_cli("spec", "validate", "--type", "task", spec_path, cwd=workdir)

    assert rc == 0
    assert "TaskSpec is valid" in out
    assert "agent" in out
    assert "llm" in out
    assert "weft-test-agent-model" in out
    assert err == ""


def test_validate_taskspec_command_summary_preserves_fields_and_order(workdir) -> None:
    """The validation summary keeps its user-facing command field sequence."""
    taskspec = create_valid_command_taskspec(name="ordered-command")
    payload = taskspec.model_dump(mode="json")
    payload["description"] = "summary ordering proof"
    spec_path = workdir / "ordered_command_taskspec.json"
    write_taskspec(spec_path, payload)

    rc, out, err = run_cli("spec", "validate", "--type", "task", spec_path, cwd=workdir)

    assert rc == 0
    expected_fragments = (
        "TaskSpec Summary",
        "TID",
        taskspec.tid,
        "Name",
        "ordered-command",
        "Description",
        "summary ordering proof",
        "Type",
        "command",
        "Runner",
        "host",
        "Command",
        "echo hello",
    )
    cursor = 0
    for fragment in expected_fragments:
        cursor = out.index(fragment, cursor) + len(fragment)
    assert err == ""


def test_validate_taskspec_preflight_host_runner(workdir):
    taskspec = create_valid_function_taskspec()
    spec_path = workdir / "host_taskspec.json"
    write_taskspec(spec_path, taskspec)

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        "--preflight",
        cwd=workdir,
    )

    assert rc == 0
    assert "TaskSpec is valid" in out
    assert "Environment profile preflight passed" in out
    assert "Runner preflight passed" in out
    assert err == ""


def test_validate_taskspec_load_runner_missing_plugin(workdir):
    taskspec = create_valid_function_taskspec()
    payload = taskspec.model_dump(mode="json")
    payload["spec"]["runner"] = {
        "name": "missing-runner",
        "options": {},
    }
    spec_path = workdir / "missing_runner_taskspec.json"
    write_taskspec(spec_path, payload)

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        "--load-runner",
        cwd=workdir,
    )

    assert rc == 1
    assert "Runner validation failed" in out
    assert "Requested runner 'missing-runner' is not available." in out
    assert err == ""


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_validate_taskspec_load_runner_provider_cli_runtime(
    workdir, provider_name: str
):
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider=provider_name,
        executable=str(write_provider_cli_wrapper(workdir, provider_name)),
        model="fixture-model" if provider_name in _MODEL_PROVIDERS else None,
    )
    spec_path = workdir / f"provider_cli_{provider_name}.json"
    write_taskspec(spec_path, taskspec)

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        "--load-runner",
        cwd=workdir,
    )

    assert rc == 0
    assert "Runner is available" in out
    assert "Agent runtime is available" in out
    assert err == ""


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_validate_taskspec_preflight_persistent_provider_cli_runtime(
    workdir,
    provider_name: str,
) -> None:
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider=provider_name,
        executable=str(write_provider_cli_wrapper(workdir, provider_name)),
        model="fixture-model" if provider_name in _MODEL_PROVIDERS else None,
        persistent=True,
        conversation_scope="per_task",
    )
    spec_path = workdir / f"provider_cli_persistent_{provider_name}.json"
    write_taskspec(spec_path, taskspec)

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        "--preflight",
        cwd=workdir,
    )

    assert rc == 0
    assert "Runner preflight passed" in out
    assert "Agent runtime preflight passed" in out
    assert err == ""


def test_validate_taskspec_preflight_provider_cli_does_not_probe_subprocess(workdir):
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider="codex",
        executable=str(write_provider_cli_wrapper(workdir, "codex")),
    )
    spec_path = workdir / "provider_cli_no_probe.json"
    write_taskspec(spec_path, taskspec)
    env = os.environ.copy()
    env["PROVIDER_CLI_FIXTURE_FAIL_PROBE"] = "1"

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        "--preflight",
        cwd=workdir,
        env=env,
    )

    assert rc == 0
    assert "Agent runtime preflight passed" in out
    assert err == ""


def test_validate_taskspec_preflight_provider_cli_missing_executable(workdir):
    taskspec = create_valid_provider_cli_agent_taskspec(
        executable="/nonexistent/provider-cli",
    )
    spec_path = workdir / "provider_cli_missing_exec.json"
    write_taskspec(spec_path, taskspec)

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        "--preflight",
        cwd=workdir,
    )

    assert rc == 1
    assert "validation failed" in out.lower()
    assert "Unable to locate executable" in out
    assert err == ""


def test_validate_taskspec_load_runner_environment_profile(workdir):
    taskspec = create_valid_function_taskspec()
    payload = taskspec.model_dump(mode="json")
    payload["spec"]["runner"] = {
        "name": "host",
        "options": {},
        "environment_profile_ref": (
            "tests.fixtures.runtime_profiles_fixture:host_environment_profile"
        ),
    }
    spec_path = workdir / "host_env_profile_taskspec.json"
    write_taskspec(spec_path, payload)

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        "--load-runner",
        cwd=workdir,
    )

    assert rc == 0
    assert "Environment profile is available" in out
    assert "Runner is available" in out
    assert err == ""


def test_validate_taskspec_missing_environment_profile_reports_correct_layer(workdir):
    taskspec = create_valid_function_taskspec()
    payload = taskspec.model_dump(mode="json")
    payload["spec"]["runner"] = {
        "name": "host",
        "options": {},
        "environment_profile_ref": "tests.fixtures.runtime_profiles_fixture:missing_profile",
    }
    spec_path = workdir / "missing_env_profile_taskspec.json"
    write_taskspec(spec_path, payload)

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        "--load-runner",
        cwd=workdir,
    )

    assert rc == 1
    assert "Environment profile validation failed" in out
    assert "missing_profile" in out
    assert err == ""


def test_validate_taskspec_tool_profile_reports_correct_layer(workdir):
    taskspec = create_valid_provider_cli_agent_taskspec(
        provider="codex",
        executable=str(write_provider_cli_wrapper(workdir, "codex")),
    )
    payload = taskspec.model_dump(mode="json")
    payload["spec"]["agent"]["runtime_config"]["tool_profile_ref"] = (
        "tests.fixtures.runtime_profiles_fixture:unsupported_mcp_tool_profile"
    )
    spec_path = workdir / "provider_cli_unsupported_tool_profile.json"
    write_taskspec(spec_path, payload)

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        "--preflight",
        cwd=workdir,
    )

    assert rc == 1
    assert "Tool profile validation failed" in out
    assert "does not support explicit MCP server descriptors" in out
    assert err == ""


def test_validate_taskspec_bundle_directory_loads_bundle_local_environment_profile(
    workdir,
) -> None:
    bundle_dir = workdir / "bundle-task"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        "\n".join(  # noqa: FLY002 approved [TS-3.1] [RUFF-SUP-239] exception
            [
                "from weft.ext import RunnerEnvironmentProfileResult",
                "",
                "def bundle_environment_profile(**kwargs):",
                "    del kwargs",
                "    return RunnerEnvironmentProfileResult(",
                "        env={'WEFT_ENV_PROFILE': 'bundle-validate'},",
                "        metadata={'profile': 'bundle-validate'},",
                "    )",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_taskspec(
        bundle_dir / "taskspec.json",
        {
            "tid": "1760000000000000201",
            "name": "bundle-validate",
            "version": "1.0",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "runner": {
                    "name": "host",
                    "options": {},
                    "environment_profile_ref": "helper_module:bundle_environment_profile",
                },
            },
            "io": {
                "inputs": {"inbox": "bundle_validate.inbox"},
                "outputs": {"outbox": "bundle_validate.outbox"},
                "control": {
                    "ctrl_in": "bundle_validate.ctrl_in",
                    "ctrl_out": "bundle_validate.ctrl_out",
                },
            },
            "state": {"status": "created"},
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        bundle_dir,
        "--load-runner",
        cwd=workdir,
    )

    assert rc == 0
    assert "Environment profile is available" in out
    assert "Runner is available" in out
    assert err == ""


def test_validate_taskspec_run_input_bundle_adapter(workdir) -> None:
    bundle_dir = workdir / "run_input_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        (
            "from __future__ import annotations\n"
            "\n"
            "def build_work_item(request):\n"
            "    return request.arguments.get('prompt', '')\n"
        ),
        encoding="utf-8",
    )
    write_taskspec(
        bundle_dir / "taskspec.json",
        {
            "name": "run-input-bundle",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "run_input": {
                    "adapter_ref": "helper_module:build_work_item",
                    "arguments": {
                        "prompt": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        bundle_dir,
        cwd=workdir,
    )

    assert rc == 0
    assert "TaskSpec is valid" in out
    assert "helper_module:build_work_item" in out
    assert err == ""


def test_validate_taskspec_run_input_builtin_adapter(workdir) -> None:
    spec_path = workdir / "run_input_builtin.json"
    write_taskspec(
        spec_path,
        {
            "name": "run-input-builtin",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:json_payload",
                "run_input": {
                    "adapter_ref": "weft.builtins.run_input:arguments_payload",
                    "arguments": {
                        "case_id": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        cwd=workdir,
    )

    assert rc == 0
    assert "TaskSpec is valid" in out
    assert "weft.builtins.run_input:arguments_payload" in out
    assert err == ""


def test_validate_taskspec_run_input_missing_adapter_ref_fails(workdir) -> None:
    bundle_dir = workdir / "invalid_run_input_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    write_taskspec(
        bundle_dir / "taskspec.json",
        {
            "name": "invalid-run-input-bundle",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "run_input": {
                    "adapter_ref": "helper_module:missing",
                    "arguments": {
                        "prompt": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        bundle_dir,
        cwd=workdir,
    )

    assert rc == 1
    assert "Run-input validation failed" in out
    assert "helper_module" in out
    assert err == ""


def test_validate_taskspec_adapter_failure_short_circuits_preflight(workdir) -> None:
    bundle_dir = workdir / "invalid_run_input_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    write_taskspec(
        bundle_dir / "taskspec.json",
        {
            "name": "invalid-run-input-bundle",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "run_input": {
                    "adapter_ref": "helper_module:missing",
                    "arguments": {
                        "prompt": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
            },
            "metadata": {},
        },
    )
    env = os.environ.copy()
    env.update({"COLUMNS": "100", "NO_COLOR": "1", "TERM": "dumb"})

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        bundle_dir,
        "--preflight",
        cwd=workdir,
        env=env,
    )

    assert rc == 1
    assert out.startswith(
        "✓ TaskSpec is valid\n"
        "✗ Run-input validation failed\n\n"
        "               Validation Errors               \n"
    )
    assert "run_input" in out
    assert "No module named 'helper_module'" in out
    assert "Environment profile preflight passed" not in out
    assert "Runner preflight passed" not in out
    assert err == ""
