"""Tests for runner plugin resolution and error hints."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from weft._runner_plugins import require_runner_plugin
from weft.ext import RunnerCapabilities


@dataclass(slots=True)
class _FakeRunnerPlugin:
    name: str
    capabilities: RunnerCapabilities = RunnerCapabilities()

    def check_version(self) -> None:
        return None

    def validate_taskspec(self, taskspec_payload, *, preflight: bool = False) -> None:
        del taskspec_payload, preflight
        return None

    def create_runner(self, **kwargs):
        del kwargs
        return object()

    def stop(self, handle, *, timeout: float = 2.0) -> bool:
        del handle, timeout
        return True

    def kill(self, handle, *, timeout: float = 2.0) -> bool:
        del handle, timeout
        return True


class _FakeEntryPoint:
    def __init__(self, name: str, plugin: _FakeRunnerPlugin) -> None:
        self.name = name
        self._plugin = plugin

    def load(self):
        return lambda: self._plugin


class _FakeEntryPoints:
    def __init__(self, matches: list[_FakeEntryPoint]) -> None:
        self._matches = matches

    def select(self, *, group: str, name: str) -> list[_FakeEntryPoint]:
        if group != "weft.runners":
            return []
        return [entry for entry in self._matches if entry.name == name]


def test_require_runner_plugin_returns_builtin_host_plugin() -> None:
    plugin = require_runner_plugin("host")

    assert plugin.name == "host"


def test_require_runner_plugin_loads_entry_point_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _FakeRunnerPlugin(name="fake")
    fake_entry_points = _FakeEntryPoints([_FakeEntryPoint("fake", plugin)])

    monkeypatch.setattr(
        "weft._runner_plugins.metadata.entry_points",
        lambda: fake_entry_points,
    )

    loaded = require_runner_plugin("fake")

    assert loaded is plugin


def test_require_runner_plugin_rejects_mismatched_plugin_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _FakeRunnerPlugin(name="other")
    fake_entry_points = _FakeEntryPoints([_FakeEntryPoint("fake", plugin)])

    monkeypatch.setattr(
        "weft._runner_plugins.metadata.entry_points",
        lambda: fake_entry_points,
    )

    with pytest.raises(RuntimeError, match="mismatched name"):
        require_runner_plugin("fake")


def test_require_runner_plugin_missing_docker_has_install_hint() -> None:
    fake_entry_points = _FakeEntryPoints([])

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "weft._runner_plugins.metadata.entry_points",
        lambda: fake_entry_points,
    )
    try:
        with pytest.raises(RuntimeError, match=r"Install weft\[docker\]"):
            require_runner_plugin("docker")
    finally:
        monkeypatch.undo()
