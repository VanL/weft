"""Tests for the Docker runner extension package."""

from __future__ import annotations

import os

import pytest
from weft_docker import get_runner_plugin, plugin

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
