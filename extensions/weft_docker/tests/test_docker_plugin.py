"""Tests for the Docker runner extension package."""

from __future__ import annotations

import os

import pytest
from weft_docker import get_runner_plugin

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
