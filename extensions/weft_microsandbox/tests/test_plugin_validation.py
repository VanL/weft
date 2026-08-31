"""Microsandbox plugin validation tests."""

from __future__ import annotations

import pytest

import weft_microsandbox
from weft.ext import RunnerHandle
from weft.liveness import registry as liveness_registry
from weft_microsandbox import plugin as plugin_module
from weft_microsandbox.plugin import MicrosandboxRunnerPlugin, get_runner_plugin

pytestmark = [pytest.mark.shared]


def test_package_root_does_not_export_runner_plugin_factory() -> None:
    assert not hasattr(weft_microsandbox, "get_runner_plugin")


def test_microsandbox_plugin_registers_its_entry_point_liveness_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(liveness_registry, "_runtime_liveness_probes", {})
    monkeypatch.setattr(plugin_module, "_liveness_probe_registered", False)

    get_runner_plugin()

    assert set(liveness_registry._runtime_liveness_probes) == {"microsandbox"}


def test_microsandbox_liveness_maps_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runtime:
        def __init__(self) -> None:
            self.state = "running"
            self.budgets: list[float] = []

        def liveness(self, sandbox_id: str, *, timeout: float) -> str:
            assert sandbox_id == "sandbox-123"
            self.budgets.append(timeout)
            if self.state == "running":
                return "live"
            if self.state == "missing":
                return "stale"
            return "unknown"

    runtime = Runtime()
    monkeypatch.setattr(plugin_module, "MicrosandboxRuntime", lambda: runtime)
    handle = RunnerHandle(
        runner="microsandbox",
        kind="sandboxed-process",
        id="sandbox-123",
        control={"authority": "runner"},
    )

    assert plugin_module._microsandbox_runtime_liveness(handle, 0.75) == "live"
    runtime.state = "missing"
    assert plugin_module._microsandbox_runtime_liveness(handle, 0.75) == "stale"
    runtime.state = "starting"
    assert plugin_module._microsandbox_runtime_liveness(handle, 0.75) == "unknown"
    assert runtime.budgets == [0.75, 0.75, 0.75]


def _payload(**spec_overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "type": "command",
        "process_target": "python",
        "persistent": False,
        "interactive": False,
        "runner": {
            "name": "microsandbox",
            "options": {"image": "python:3.12-alpine"},
        },
        "limits": {},
        "env": {},
    }
    spec.update(spec_overrides)
    return {"spec": spec}


def test_get_runner_plugin_exposes_conservative_capabilities() -> None:
    plugin = get_runner_plugin()

    assert plugin.name == "microsandbox"
    assert frozenset(plugin.capabilities.supported_types) == {"command", "agent"}
    assert plugin.capabilities.supports_interactive is False
    assert plugin.capabilities.supports_persistent is False
    assert plugin.capabilities.supports_agent_sessions is False


def test_validate_rejects_function_tasks() -> None:
    plugin = MicrosandboxRunnerPlugin()

    with pytest.raises(ValueError, match="supports only"):
        plugin.validate_taskspec(
            _payload(type="function", function_target="tests.tasks.sample:noop")
        )


def test_validate_rejects_interactive_tasks() -> None:
    plugin = MicrosandboxRunnerPlugin()

    with pytest.raises(ValueError, match="interactive"):
        plugin.validate_taskspec(_payload(interactive=True))


def test_validate_preflight_uses_runtime_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Runtime:
        def check_preflight(self) -> None:
            calls.append("preflight")

    import weft_microsandbox.plugin as plugin_module

    monkeypatch.setattr(plugin_module, "MicrosandboxRuntime", Runtime)
    plugin = MicrosandboxRunnerPlugin()

    plugin.validate_taskspec(_payload(), preflight=True)

    assert calls == ["preflight"]
