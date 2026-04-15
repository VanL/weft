"""Black-box CLI tests for `weft spec validate --type task`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import run_cli  # re-exported for clarity
from tests.fixtures.provider_cli_fixture import (
    PROVIDER_FIXTURE_NAMES,
    write_provider_cli_wrapper,
)
from tests.taskspec.fixtures import (
    create_valid_agent_taskspec,
    create_valid_function_taskspec,
    create_valid_provider_cli_agent_taskspec,
)

pytestmark = [pytest.mark.shared]
_MODEL_PROVIDERS = frozenset({"claude_code", "codex", "gemini", "opencode", "qwen"})


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

    rc, out, err = run_cli("spec", "validate", "--type", "task", spec_path, cwd=workdir)

    assert rc != 0
    assert "TaskSpec validation failed" in out
    assert "outbox" in out


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

    rc, out, err = run_cli(
        "spec",
        "validate",
        "--type",
        "task",
        spec_path,
        "--preflight",
        cwd=workdir,
        env={"PROVIDER_CLI_FIXTURE_FAIL_PROBE": "1"},
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
        "\n".join(
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
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "def build_work_item(request):",
                "    return request.arguments.get('prompt', '')",
                "",
            ]
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
