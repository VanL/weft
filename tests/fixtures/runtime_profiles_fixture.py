"""Fixture environment and tool profiles for Phase 3 delegated-runtime tests."""

from __future__ import annotations

import sys
from pathlib import Path

from tests.fixtures.mcp_stdio_fixture import fixture_server_script_path
from weft.ext import (
    AgentMCPServerDescriptor,
    AgentToolProfileResult,
    RunnerEnvironmentProfileResult,
)


def host_environment_profile(
    *,
    target_type: str,
    runner_name: str,
    runner_options,
    env,
    working_dir,
    tid,
):  # noqa: ANN001
    del target_type, runner_options
    if runner_name != "host":
        raise ValueError("host_environment_profile requires runner_name='host'")
    return RunnerEnvironmentProfileResult(
        env={"WEFT_ENV_PROFILE": "host-default"},
        working_dir=working_dir,
        metadata={"profile": "host", "tid": tid, "existing_env": dict(env)},
    )


def docker_image_environment_profile(
    *,
    target_type: str,
    runner_name: str,
    runner_options,
    env,
    working_dir,
    tid,
):  # noqa: ANN001
    del target_type, runner_options, env
    if runner_name != "docker":
        raise ValueError(
            "docker_image_environment_profile requires runner_name='docker'"
        )
    return RunnerEnvironmentProfileResult(
        runner_options={
            "image": "python:3.13-alpine",
            "container_workdir": "/workspace",
        },
        env={"WEFT_ENV_PROFILE": "docker-image"},
        working_dir=working_dir,
        metadata={"profile": "docker-image", "tid": tid},
    )


def docker_build_environment_profile(
    *,
    target_type: str,
    runner_name: str,
    runner_options,
    env,
    working_dir,
    tid,
):  # noqa: ANN001
    del target_type, runner_options, env
    if runner_name != "docker":
        raise ValueError(
            "docker_build_environment_profile requires runner_name='docker'"
        )
    if working_dir is None:
        raise ValueError("docker_build_environment_profile requires working_dir")
    root = Path(working_dir)
    return RunnerEnvironmentProfileResult(
        runner_options={
            "build": {
                "context": str(root),
                "dockerfile": str(root / "Dockerfile"),
            },
            "container_workdir": "/workspace",
            "mounts": [
                {
                    "source": str(root),
                    "target": "/workspace",
                    "read_only": True,
                }
            ],
        },
        env={"WEFT_ENV_PROFILE": "docker-build"},
        working_dir=working_dir,
        metadata={"profile": "docker-build", "tid": tid},
    )


def macos_sandbox_environment_profile(
    *,
    target_type: str,
    runner_name: str,
    runner_options,
    env,
    working_dir,
    tid,
):  # noqa: ANN001
    del target_type, runner_options, env
    if runner_name != "macos-sandbox":
        raise ValueError(
            "macos_sandbox_environment_profile requires runner_name='macos-sandbox'"
        )
    if working_dir is None:
        raise ValueError("macos_sandbox_environment_profile requires working_dir")
    root = Path(working_dir)
    return RunnerEnvironmentProfileResult(
        runner_options={"profile": str(root / "allow-default.sb")},
        env={"WEFT_ENV_PROFILE": "macos-sandbox"},
        working_dir=working_dir,
        metadata={"profile": "macos-sandbox", "tid": tid},
    )


def invalid_environment_profile(
    *,
    target_type: str,
    runner_name: str,
    runner_options,
    env,
    working_dir,
    tid,
):  # noqa: ANN001
    del target_type, runner_name, runner_options, env, working_dir, tid
    return "nope"


def structured_tool_profile(*, agent, tid):  # noqa: ANN001
    provider_name = str(agent.runtime_config.get("provider", "")).strip()
    workspace_access = None
    if provider_name in {"claude_code", "codex", "gemini", "qwen"}:
        workspace_access = "read-only"
    return AgentToolProfileResult(
        instructions="structured profile instructions",
        workspace_access=workspace_access,
        metadata={"profile": "structured", "provider": provider_name, "tid": tid},
    )


def claude_mcp_tool_profile(*, agent, tid):  # noqa: ANN001
    del agent
    server = AgentMCPServerDescriptor(
        name="fixture-server",
        command="python",
        args=("-c", "print('fixture-mcp')"),
        env={"FIXTURE_MCP_ENV": "1"},
    )
    return AgentToolProfileResult(
        instructions="claude mcp profile instructions",
        workspace_access="read-only",
        mcp_servers=(server,),
        metadata={"profile": "claude-mcp", "tid": tid},
    )


def claude_stdio_mcp_tool_profile(*, agent, tid):  # noqa: ANN001
    script_path = str(fixture_server_script_path())
    runtime_script = agent.runtime_config.get("mcp_server_script")
    if isinstance(runtime_script, str) and runtime_script.strip():
        script_path = runtime_script.strip()
    server = AgentMCPServerDescriptor(
        name="fixture-stdio-server",
        command=str(Path(sys.executable)),
        args=(script_path,),
    )
    return AgentToolProfileResult(
        instructions="claude stdio mcp profile instructions",
        mcp_servers=(server,),
        metadata={"profile": "claude-stdio-mcp", "tid": tid},
    )


def unsupported_mcp_tool_profile(*, agent, tid):  # noqa: ANN001
    del agent
    server = AgentMCPServerDescriptor(
        name="fixture-server",
        command="python",
        args=("-c", "print('fixture-mcp')"),
    )
    return AgentToolProfileResult(
        mcp_servers=(server,),
        metadata={"profile": "unsupported-mcp", "tid": tid},
    )
