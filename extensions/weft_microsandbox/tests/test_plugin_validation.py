"""Microsandbox plugin validation tests."""

from __future__ import annotations

import pytest

from weft_microsandbox.plugin import MicrosandboxRunnerPlugin, get_runner_plugin

pytestmark = [pytest.mark.shared]


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
    assert plugin.capabilities.supported_types == ("command", "agent")
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
