"""Tests for the Docker runner extension package."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from weft_docker import _sdk as docker_sdk
from weft_docker import get_runner_plugin, plugin

import weft.runtime_liveness as runtime_liveness
from weft.ext import RunnerHandle, RunnerRuntimeDescription

pytestmark = [pytest.mark.shared]


def _write_profile(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


class _FakeDockerClient:
    def __init__(self, *, missing_networks: set[str] | None = None) -> None:
        self.ping_count = 0
        self.network_gets: list[str] = []
        self._missing_networks = missing_networks or set()
        self.networks = self

    def ping(self) -> None:
        self.ping_count += 1

    def get(self, network: str) -> object:
        self.network_gets.append(network)
        if network in self._missing_networks:
            raise _FakeDockerNotFound(network)
        return object()


class _FakeDockerNotFound(Exception):
    pass


class _FakeDockerModule:
    class errors:
        NotFound = _FakeDockerNotFound


@contextmanager
def _fake_docker_client_context(client: object) -> Iterator[object]:
    yield client


def _docker_manager_handle() -> RunnerHandle:
    return RunnerHandle(
        runner="manager-supervisor",
        kind="supervised-process",
        id="docker:container123",
        control={"authority": "external-supervisor"},
        observations={
            "container_runtime": "docker",
            "container_id": "container123",
        },
        metadata={},
    )


class _FakeContainer:
    def __init__(self, *, running: bool | None, status: str | None = None) -> None:
        state: dict[str, object] = {}
        if running is not None:
            state["Running"] = running
        if status is not None:
            state["Status"] = status
        self.attrs = {"State": state}

    def reload(self) -> None:
        return None


@contextmanager
def _fake_docker_client() -> Iterator[object]:
    yield object()


def test_docker_plugin_registers_runtime_liveness_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_liveness, "_runtime_liveness_probes", {})
    monkeypatch.setattr(plugin, "_liveness_probes_registered", False)

    get_runner_plugin()

    assert "docker" in runtime_liveness._runtime_liveness_probes
    assert "manager-supervisor" in runtime_liveness._runtime_liveness_probes


def test_docker_runtime_liveness_reports_running_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin, "_docker_client", lambda timeout=10: _fake_docker_client()
    )
    monkeypatch.setattr(
        plugin,
        "_lookup_container",
        lambda client, runtime_id, *, fallback_id=None: _FakeContainer(running=True),
    )

    assert plugin._docker_runtime_liveness(_docker_manager_handle()) == "live"


def test_docker_runtime_liveness_reports_missing_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin, "_docker_client", lambda timeout=10: _fake_docker_client()
    )
    monkeypatch.setattr(
        plugin,
        "_lookup_container",
        lambda client, runtime_id, *, fallback_id=None: None,
    )

    assert plugin._docker_runtime_liveness(_docker_manager_handle()) == "stale"


def test_docker_runtime_liveness_reports_stopped_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin, "_docker_client", lambda timeout=10: _fake_docker_client()
    )
    monkeypatch.setattr(
        plugin,
        "_lookup_container",
        lambda client, runtime_id, *, fallback_id=None: _FakeContainer(running=False),
    )

    assert plugin._docker_runtime_liveness(_docker_manager_handle()) == "stale"


def test_docker_runtime_liveness_reports_unknown_when_docker_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def failing_client(*, timeout: int = 10) -> Iterator[object]:
        del timeout
        raise RuntimeError("docker unavailable")
        yield object()

    monkeypatch.setattr(plugin, "_docker_client", failing_client)

    assert plugin._docker_runtime_liveness(_docker_manager_handle()) == "unknown"


def test_docker_runtime_liveness_ignores_non_docker_handle() -> None:
    handle = RunnerHandle(
        runner="manager-supervisor",
        kind="supervised-process",
        id="container123",
        control={"authority": "external-supervisor"},
        observations={},
        metadata={},
    )

    assert plugin._docker_runtime_liveness(handle) == "unknown"


def test_docker_runner_accepts_docker_enforced_limits_and_rejects_unsupported_ones() -> (
    None
):
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    plugin = get_runner_plugin()

    plugin.validate_taskspec(
        {
            "spec": {
                "type": "command",
                "runner": {
                    "name": "docker",
                    "options": {"image": "busybox:latest"},
                },
                "limits": {
                    "memory_mb": 128,
                    "cpu_percent": 50,
                    "max_fds": 64,
                    "max_connections": 0,
                },
            }
        }
    )

    with pytest.raises(ValueError, match="max_connections"):
        plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    "runner": {
                        "name": "docker",
                        "options": {"image": "busybox:latest"},
                    },
                    "limits": {"max_connections": 3},
                }
            }
        )


def test_docker_runner_preflight_requires_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    plugin = get_runner_plugin()
    monkeypatch.setattr("weft_docker.plugin.shutil.which", lambda name: None)

    with pytest.raises(ValueError, match="Docker binary"):
        plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    "runner": {
                        "name": "docker",
                        "options": {"image": "busybox:latest"},
                    },
                }
            },
            preflight=True,
        )


def test_docker_runner_accepts_one_shot_provider_cli_agent_with_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    runner_plugin = get_runner_plugin()
    monkeypatch.setattr(
        plugin,
        "get_agent_image_recipe",
        lambda provider_name: SimpleNamespace(
            provider=provider_name,
            default_executable="codex",
        ),
    )

    class FakeClient:
        def ping(self) -> None:
            return None

    @contextmanager
    def fake_docker_client(*, timeout: int = 10) -> Iterator[FakeClient]:
        del timeout
        yield FakeClient()

    monkeypatch.setattr(plugin, "_docker_client", fake_docker_client)

    runner_plugin.validate_taskspec(
        {
            "spec": {
                "type": "agent",
                "runner": {
                    "name": "docker",
                    "options": {
                        "mounts": [
                            {
                                "source": "/tmp",
                                "target": "/workspace",
                                "read_only": False,
                            }
                        ],
                        "work_item_mounts": [
                            {
                                "source_path_ref": "metadata.document_path",
                                "target": "/tmp/runtime-document.md",
                                "read_only": True,
                                "kind": "file",
                            }
                        ],
                        "network": "none",
                    },
                },
                "agent": {
                    "runtime": "provider_cli",
                    "authority_class": "general",
                    "conversation_scope": "per_message",
                    "runtime_config": {"provider": "codex"},
                },
            }
        },
        preflight=True,
    )


def test_docker_runner_rejects_command_work_item_mounts() -> None:
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    runner_plugin = get_runner_plugin()

    with pytest.raises(ValueError, match="work_item_mounts"):
        runner_plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    "runner": {
                        "name": "docker",
                        "options": {
                            "image": "busybox:latest",
                            "work_item_mounts": [
                                {
                                    "source_path_ref": "metadata.document_path",
                                    "target": "/tmp/runtime-document.md",
                                }
                            ],
                        },
                    },
                }
            }
        )


def test_docker_runner_validation_accepts_command_container_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    runner_plugin = get_runner_plugin()
    profile_file = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops]
        image = "busybox:latest"
        network = "project_ops"
        """,
    )
    fake_client = _FakeDockerClient()
    monkeypatch.setattr(
        plugin,
        "_docker_client",
        lambda timeout=10: _fake_docker_client_context(fake_client),
    )
    monkeypatch.setattr(plugin, "_resolve_docker_binary", lambda value: value)

    runner_plugin.validate_taskspec(
        {
            "spec": {
                "type": "command",
                "process_target": "python3",
                "runner": {
                    "name": "docker",
                    "options": {
                        "container_profile": "ops",
                        "container_profile_file": str(profile_file),
                    },
                },
            }
        }
    )

    assert fake_client.ping_count == 0
    assert fake_client.network_gets == []

    runner_plugin.validate_taskspec(
        {
            "spec": {
                "type": "command",
                "process_target": "python3",
                "runner": {
                    "name": "docker",
                    "options": {
                        "container_profile": "ops",
                        "container_profile_file": str(profile_file),
                    },
                },
            }
        },
        preflight=True,
    )

    assert fake_client.ping_count == 1
    assert fake_client.network_gets == ["project_ops"]


def test_docker_runner_profile_image_build_conflict_names_profile(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    runner_plugin = get_runner_plugin()
    profile_file = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        version = 1

        [profiles.broken]
        image = "busybox:latest"
        build = { context = "." }
        """,
    )

    with pytest.raises(ValueError) as exc_info:
        runner_plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    "process_target": "python3",
                    "runner": {
                        "name": "docker",
                        "options": {
                            "container_profile": "broken",
                            "container_profile_file": str(profile_file),
                        },
                    },
                }
            }
        )

    assert str(exc_info.value) == (
        f"Docker profile 'broken' (from {profile_file.resolve()}) cannot specify "
        "both 'image' and 'build'"
    )


def test_docker_runner_rejects_container_profile_for_agent_tasks() -> None:
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    runner_plugin = get_runner_plugin()

    with pytest.raises(ValueError, match="container_profile.*command"):
        runner_plugin.validate_taskspec(
            {
                "spec": {
                    "type": "agent",
                    "runner": {
                        "name": "docker",
                        "options": {"container_profile": "ops"},
                    },
                    "agent": {
                        "runtime": "provider_cli",
                        "authority_class": "general",
                        "conversation_scope": "per_message",
                        "runtime_config": {"provider": "codex"},
                    },
                }
            }
        )


def test_docker_runner_create_runner_materializes_container_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_file = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops]
        image = "busybox:latest"
        network = "project_ops"
        mount_workdir = false
        container_workdir = "/app/project"
        env_from_host = ["OPTIONAL_TOKEN"]

        [profiles.ops.env]
        SERVICE_URL = "https://internal-service:8443"
        """,
    )
    monkeypatch.setenv("OPTIONAL_TOKEN", "from-host")

    runner = plugin.DockerRunnerPlugin().create_runner(
        target_type="command",
        tid="1844674407370955161",
        function_target=None,
        process_target="python3",
        agent=None,
        args=["-c", "print('ok')"],
        kwargs=None,
        env={"SERVICE_URL": "https://explicit.example.test"},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
        runner_options={
            "container_profile": "ops",
            "container_profile_file": str(profile_file),
            "docker_args": ["--pull=never"],
        },
        bundle_root=None,
        persistent=False,
        interactive=False,
    )

    assert isinstance(runner, plugin.DockerCommandRunner)
    assert runner._image == "busybox:latest"
    assert runner._network == "project_ops"
    assert runner._mount_workdir is False
    assert runner._container_workdir == "/app/project"
    assert runner._docker_args == ["--pull=never"]
    assert runner._env == {
        "SERVICE_URL": "https://explicit.example.test",
        "OPTIONAL_TOKEN": "from-host",
    }


def test_docker_runner_preflight_checks_profile_network_only_during_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    runner_plugin = get_runner_plugin()
    profile_file = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops]
        image = "busybox:latest"
        network = "missing_network"
        """,
    )
    fake_client = _FakeDockerClient(missing_networks={"missing_network"})
    monkeypatch.setattr(plugin, "_load_docker_sdk", lambda: _FakeDockerModule)
    monkeypatch.setattr(plugin, "_resolve_docker_binary", lambda value: value)
    monkeypatch.setattr(
        plugin,
        "_docker_client",
        lambda timeout=10: _fake_docker_client_context(fake_client),
    )
    payload = {
        "spec": {
            "type": "command",
            "process_target": "python3",
            "runner": {
                "name": "docker",
                "options": {
                    "container_profile": "ops",
                    "container_profile_file": str(profile_file),
                },
            },
        }
    }

    runner_plugin.validate_taskspec(payload, preflight=False)

    assert fake_client.network_gets == []

    with pytest.raises(ValueError, match="Docker network does not exist"):
        runner_plugin.validate_taskspec(payload, preflight=True)

    assert fake_client.network_gets == ["missing_network"]


def test_docker_runner_rejects_conflicting_agent_mount_targets() -> None:
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    runner_plugin = get_runner_plugin()

    with pytest.raises(ValueError, match="/tmp/runtime-document.md"):
        runner_plugin.validate_taskspec(
            {
                "spec": {
                    "type": "agent",
                    "runner": {
                        "name": "docker",
                        "options": {
                            "mounts": [
                                {
                                    "source": "/tmp",
                                    "target": "/tmp/runtime-document.md",
                                }
                            ],
                            "work_item_mounts": [
                                {
                                    "source_path_ref": "metadata.document_path",
                                    "target": "/tmp/runtime-document.md",
                                }
                            ],
                        },
                    },
                    "agent": {
                        "runtime": "provider_cli",
                        "authority_class": "general",
                        "conversation_scope": "per_message",
                        "runtime_config": {"provider": "codex"},
                    },
                }
            }
        )


def test_docker_runner_rejects_agent_provider_without_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    runner_plugin = get_runner_plugin()
    monkeypatch.setattr(plugin, "get_agent_image_recipe", lambda provider_name: None)

    with pytest.raises(ValueError, match="No Docker-backed agent image recipe"):
        runner_plugin.validate_taskspec(
            {
                "spec": {
                    "type": "agent",
                    "runner": {"name": "docker", "options": {}},
                    "agent": {
                        "runtime": "provider_cli",
                        "authority_class": "general",
                        "conversation_scope": "per_message",
                        "runtime_config": {"provider": "claude_code"},
                    },
                }
            }
        )


def test_docker_runner_rejects_agent_provider_without_runtime_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    runner_plugin = get_runner_plugin()
    monkeypatch.setattr(
        plugin,
        "get_provider_container_runtime_descriptor",
        lambda provider_name: None,
    )

    with pytest.raises(
        ValueError,
        match="No Docker-backed provider container runtime descriptor",
    ):
        runner_plugin.validate_taskspec(
            {
                "spec": {
                    "type": "agent",
                    "runner": {"name": "docker", "options": {}},
                    "agent": {
                        "runtime": "provider_cli",
                        "authority_class": "general",
                        "conversation_scope": "per_message",
                        "runtime_config": {"provider": "codex"},
                    },
                }
            }
        )


def test_docker_runner_is_unsupported_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = get_runner_plugin()
    monkeypatch.setattr("weft_docker.plugin.os.name", "nt")

    with pytest.raises(ValueError, match="Linux and macOS"):
        plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    "runner": {
                        "name": "docker",
                        "options": {"image": "busybox:latest"},
                    },
                }
            }
        )


def test_describe_runtime_falls_back_to_container_id_when_name_lookup_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeNotFound(Exception):
        pass

    class FakeDocker:
        class errors:
            NotFound = FakeNotFound

    class FakeContainer:
        id = "container-123"
        attrs = {
            "Name": "/weft-test",
            "Config": {"Image": "busybox:latest"},
            "State": {
                "Status": "running",
                "OOMKilled": False,
                "ExitCode": 0,
                "Pid": 42,
                "StartedAt": "2026-04-08T00:00:00Z",
                "FinishedAt": "",
                "Error": "",
            },
            "HostConfig": {"NetworkMode": "default"},
        }

        def reload(self) -> None:
            return None

        def stats(self, *, stream: bool = False) -> dict[str, object]:
            assert stream is False
            return {}

    class FakeContainers:
        def get(self, runtime_id: str) -> FakeContainer:
            if runtime_id == "container-123":
                return FakeContainer()
            raise FakeNotFound()

    class FakeClient:
        containers = FakeContainers()

    monkeypatch.setattr(plugin, "_load_docker_sdk", lambda: FakeDocker)

    description = plugin._describe_runtime(  # pyright: ignore[reportPrivateUsage]
        FakeClient(),
        runtime_id="weft-runtime-name",
        base_metadata={"container_id": "container-123", "image": "busybox:latest"},
    )

    assert description.state == "running"
    assert description.metadata["container_id"] == "container-123"
    assert description.metadata["container_name"] == "weft-test"


def test_describe_runtime_falls_back_to_container_list_when_name_get_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeNotFound(Exception):
        pass

    class FakeDocker:
        class errors:
            NotFound = FakeNotFound

    class FakeContainer:
        id = "container-456"
        name = "weft-runtime-name"
        attrs = {
            "Name": "/weft-runtime-name",
            "Config": {"Image": "busybox:latest"},
            "State": {
                "Status": "running",
                "OOMKilled": False,
                "ExitCode": 0,
                "Pid": 77,
                "StartedAt": "2026-04-08T00:00:00Z",
                "FinishedAt": "",
                "Error": "",
            },
            "HostConfig": {"NetworkMode": "default"},
        }

        def reload(self) -> None:
            return None

        def stats(self, *, stream: bool = False) -> dict[str, object]:
            assert stream is False
            return {}

    class FakeContainers:
        def get(self, runtime_id: str) -> FakeContainer:
            raise FakeNotFound()

        def list(
            self,
            *,
            all: bool = False,
            filters: dict[str, str] | None = None,
        ) -> list[FakeContainer]:
            assert all is True
            assert filters == {"name": "weft-runtime-name"}
            return [FakeContainer()]

    class FakeClient:
        containers = FakeContainers()

    monkeypatch.setattr(plugin, "_load_docker_sdk", lambda: FakeDocker)

    description = plugin._describe_runtime(  # pyright: ignore[reportPrivateUsage]
        FakeClient(),
        runtime_id="weft-runtime-name",
        base_metadata={"image": "busybox:latest"},
    )

    assert description.state == "running"
    assert description.metadata["container_id"] == "container-456"
    assert description.metadata["container_name"] == "weft-runtime-name"


def test_command_runner_waits_for_container_to_leave_created_before_runtime_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    states: list[str] = ["created", "running"]

    class FakeProcess:
        pid = 4321
        stdout = object()
        stderr = object()

        def poll(self) -> int | None:
            return None

    class FakeContainer:
        id = "container-789"
        attrs: dict[str, Any] = {
            "Name": "/weft-test",
            "Config": {"Image": "busybox:latest"},
            "State": {"Status": "created"},
        }

        def reload(self) -> None:
            current = states.pop(0) if len(states) > 1 else states[0]
            self.attrs["State"]["Status"] = current

    fake_container = FakeContainer()
    callback_state: dict[str, str] = {}

    @contextmanager
    def fake_docker_client() -> Iterator[object]:
        yield object()

    def fake_run_monitored_subprocess(**kwargs: Any) -> Any:
        on_runtime_handle_started = kwargs["on_runtime_handle_started"]
        runtime_handle = kwargs["runtime_handle"]
        if on_runtime_handle_started is not None:
            on_runtime_handle_started(runtime_handle)
        callback_state["status"] = fake_container.attrs["State"]["Status"]
        return plugin.RunnerOutcome(
            status="ok",
            value=None,
            error=None,
            stdout=None,
            stderr=None,
            returncode=0,
            duration=0.0,
            runtime_handle=runtime_handle,
        )

    monkeypatch.setattr(
        plugin.subprocess, "Popen", lambda *args, **kwargs: FakeProcess()
    )
    monkeypatch.setattr(plugin, "_resolve_docker_binary", lambda value: value)
    monkeypatch.setattr(
        plugin.DockerCommandRunner,
        "_ensure_image",
        lambda self, executable: "busybox:latest",
    )
    monkeypatch.setattr(plugin, "_docker_client", fake_docker_client)
    monkeypatch.setattr(
        plugin,
        "_wait_for_container",
        lambda client, runtime_id, process: fake_container,
    )
    monkeypatch.setattr(
        plugin, "run_monitored_subprocess", fake_run_monitored_subprocess
    )
    monkeypatch.setattr(
        plugin,
        "_describe_runtime",
        lambda client, runtime_id, base_metadata: RunnerRuntimeDescription(
            runner="docker",
            id=runtime_id,
            state="running",
            metadata=dict(base_metadata),
        ),
    )
    monkeypatch.setattr(plugin, "_remove_container", lambda client, runtime_id: None)

    runner = plugin.DockerCommandRunner(
        tid="1234567890",
        process_target="python3",
        args=["-c", "print('hello')"],
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.01,
        runner_options={"image": "busybox:latest"},
    )

    outcome = runner.run_with_hooks({})

    assert outcome.status == "ok"
    assert callback_state["status"] == "running"


def test_command_runner_uses_container_workdir_without_mounting_host_workdir(
    tmp_path: Path,
) -> None:
    runner = plugin.DockerCommandRunner(
        tid="1234567890",
        process_target="python3",
        args=["-c", "print('hello')"],
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.01,
        runner_options={
            "image": "busybox:latest",
            "mount_workdir": False,
            "container_workdir": "/app/project",
        },
    )

    command, stdin_data = runner._build_docker_command(
        {},
        "weft-workdir-test",
        executable="docker",
        image="busybox:latest",
    )

    assert stdin_data is None
    assert "--workdir" in command
    assert command[command.index("--workdir") + 1] == "/app/project"
    assert "--volume" not in command


def test_command_runner_avoids_duplicate_workdir_when_mounting_host_workdir(
    tmp_path: Path,
) -> None:
    runner = plugin.DockerCommandRunner(
        tid="1234567890",
        process_target="python3",
        args=["-c", "print('hello')"],
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.01,
        runner_options={
            "image": "busybox:latest",
            "mount_workdir": True,
            "container_workdir": "/app/project",
        },
    )

    command, _stdin_data = runner._build_docker_command(
        {},
        "weft-workdir-test",
        executable="docker",
        image="busybox:latest",
    )

    assert command.count("--workdir") == 1
    assert command[command.index("--workdir") + 1] == "/app/project"
    assert "--volume" in command
    assert f"{tmp_path.resolve()}:/app/project" in command


def test_wait_for_container_runtime_start_fails_when_created_state_sticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}

    class FakeContainer:
        id = "container-789"
        name = "weft-stuck"
        attrs: dict[str, Any] = {"State": {"Status": "created"}}

        def reload(self) -> None:
            return None

    def monotonic() -> float:
        return clock["now"]

    def sleep(duration: float) -> None:
        clock["now"] += duration

    monkeypatch.setattr(docker_sdk.time, "monotonic", monotonic)
    monkeypatch.setattr(docker_sdk.time, "sleep", sleep)

    with pytest.raises(TimeoutError, match="weft-stuck"):
        docker_sdk.wait_for_container_runtime_start(
            FakeContainer(),
            timeout=0.2,
            interval=0.05,
        )

    assert clock["now"] >= 0.2


def test_command_runner_cleans_up_container_when_runtime_start_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeProcess:
        pid = 4321
        stdout = object()
        stderr = object()

        def __init__(self) -> None:
            self.killed = False
            self.wait_timeout: float | None = None

        def poll(self) -> int | None:
            return 137 if self.killed else None

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float | None = None) -> int:
            self.wait_timeout = timeout
            return 137

    class FakeContainer:
        id = "container-789"
        attrs: dict[str, Any] = {
            "Name": "/weft-test",
            "Config": {"Image": "busybox:latest"},
            "State": {"Status": "created"},
        }

        def reload(self) -> None:
            return None

    fake_process = FakeProcess()
    fake_container = FakeContainer()
    fake_client = object()
    removed: list[tuple[object, str]] = []

    @contextmanager
    def fake_docker_client() -> Iterator[object]:
        yield fake_client

    monkeypatch.setattr(
        plugin.subprocess, "Popen", lambda *args, **kwargs: fake_process
    )
    monkeypatch.setattr(plugin, "_resolve_docker_binary", lambda value: value)
    monkeypatch.setattr(
        plugin.DockerCommandRunner,
        "_ensure_image",
        lambda self, executable: "busybox:latest",
    )
    monkeypatch.setattr(plugin, "_container_name", lambda tid: "weft-cleanup-test")
    monkeypatch.setattr(plugin, "_docker_client", fake_docker_client)
    monkeypatch.setattr(
        plugin,
        "_wait_for_container",
        lambda client, runtime_id, process: fake_container,
    )
    monkeypatch.setattr(
        plugin,
        "wait_for_container_runtime_start",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("startup failed")),
    )
    monkeypatch.setattr(
        plugin,
        "_remove_container",
        lambda client, runtime_id: removed.append((client, runtime_id)),
    )

    runner = plugin.DockerCommandRunner(
        tid="1234567890",
        process_target="python3",
        args=["-c", "print('hello')"],
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.01,
        runner_options={"image": "busybox:latest"},
    )

    with pytest.raises(RuntimeError, match="startup failed"):
        runner.run_with_hooks({})

    assert fake_process.killed is True
    assert fake_process.wait_timeout == 1.0
    assert removed == [(fake_client, "weft-cleanup-test")]
