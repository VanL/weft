"""Tests for the Docker runner extension package."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from weft_docker import get_runner_plugin, plugin

from weft.ext import RunnerRuntimeDescription

pytestmark = [pytest.mark.shared]


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
            runner_name="docker",
            runtime_id=runtime_id,
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
