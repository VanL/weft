"""Focused tests for Docker provider runtime resolution integration."""

from __future__ import annotations

from pathlib import Path

import pytest
from weft_docker.agent_runner import DockerProviderCLIRunner

from weft.core.agents.provider_cli.container_runtime import (
    ProviderContainerRuntimeDescriptor,
    ProviderContainerRuntimeResolution,
    ResolvedProviderContainerMount,
)
from weft.core.agents.provider_cli.runtime_prep import PreparedProviderContainerRuntime

pytestmark = [pytest.mark.shared]


def test_docker_agent_runner_container_env_prefers_explicit_task_env() -> None:
    runner = DockerProviderCLIRunner(
        tid="123",
        agent={
            "runtime": "provider_cli",
            "authority_class": "general",
            "conversation_scope": "per_message",
            "runtime_config": {"provider": "codex"},
        },
        env={"OPENAI_API_KEY": "explicit", "EXPLICIT_ONLY": "1"},
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_interval=0.05,
        runner_options={},
    )
    resolution = ProviderContainerRuntimeResolution(
        descriptor=ProviderContainerRuntimeDescriptor(version="v2", provider="codex"),
        env={"OPENAI_API_KEY": "from-host", "HOST_ONLY": "1"},
    )

    container_env = runner._container_env(  # pyright: ignore[reportPrivateUsage]
        resolution,
        PreparedProviderContainerRuntime(),
        {"INVOCATION_ONLY": "1"},
    )

    assert container_env == {
        "OPENAI_API_KEY": "explicit",
        "HOST_ONLY": "1",
        "EXPLICIT_ONLY": "1",
        "INVOCATION_ONLY": "1",
    }


def test_docker_agent_runner_path_mappings_include_runtime_mounts(
    tmp_path: Path,
) -> None:
    runtime_mount = ResolvedProviderContainerMount(
        name="codex_home",
        source=(tmp_path / ".codex").resolve(),
        target="/root/.codex",
        read_only=True,
    )
    runtime_mount.source.mkdir()
    runner = DockerProviderCLIRunner(
        tid="123",
        agent={
            "runtime": "provider_cli",
            "authority_class": "general",
            "conversation_scope": "per_message",
            "runtime_config": {"provider": "codex"},
        },
        env={},
        working_dir=str(tmp_path / "workspace"),
        timeout=5.0,
        limits=None,
        monitor_interval=0.05,
        runner_options={"mount_workdir": False},
    )

    mappings = runner._path_mappings(  # pyright: ignore[reportPrivateUsage]
        runtime_mounts=(runtime_mount,),
    )

    assert mappings == [(runtime_mount.source, "/root/.codex")]
