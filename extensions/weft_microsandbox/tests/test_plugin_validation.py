"""Microsandbox plugin validation tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import weft_microsandbox
from weft.ext import RunnerHandle
from weft.liveness import registry as liveness_registry
from weft_microsandbox import _runtime
from weft_microsandbox import plugin as plugin_module
from weft_microsandbox.plugin import MicrosandboxRunnerPlugin, get_runner_plugin

pytestmark = [pytest.mark.shared]


def test_package_root_does_not_export_runner_plugin_factory() -> None:
    assert not hasattr(weft_microsandbox, "get_runner_plugin")


def test_microsandbox_plugin_registers_its_entry_point_liveness_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(liveness_registry, "_runtime_liveness_probes", {})

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


def test_factory_restores_replaced_liveness_registry(monkeypatch) -> None:
    monkeypatch.setattr(liveness_registry, "_runtime_liveness_probes", {})
    get_runner_plugin()
    liveness_registry.register_runtime_liveness_probe(
        "microsandbox", lambda handle, budget: "unknown"
    )
    replaced = liveness_registry._runtime_liveness_probes["microsandbox"]
    get_runner_plugin()
    assert liveness_registry._runtime_liveness_probes["microsandbox"] is not replaced


@pytest.mark.parametrize(
    "state,expected", [("running", "live"), ("stopped", "stale"), (None, "unknown")]
)
def test_alias_routed_microsandbox_liveness_classifies_sdk_evidence(
    monkeypatch, state, expected
) -> None:
    class Sandbox:
        @staticmethod
        async def get(sandbox_id):
            assert sandbox_id == "alias-sandbox"
            return Sandbox()

        async def refresh(self):
            return SimpleNamespace(status=state)

    monkeypatch.setattr(_runtime, "_load_sdk", lambda: SimpleNamespace(Sandbox=Sandbox))
    monkeypatch.setattr(liveness_registry, "_runtime_liveness_probes", {})
    liveness_registry.register_runtime_liveness_probe(
        "microsandbox", plugin_module._microsandbox_runtime_liveness
    )
    handle = RunnerHandle(
        runner="alias",
        control={"authority": "runner"},
        kind="sandboxed-process",
        id="alias-sandbox",
        observations={"liveness_provider": "microsandbox"},
    )
    assert liveness_registry.runtime_liveness_from_registered_probe(handle) == expected


def _create_command_runner_for_validation(runner_plugin, options, **overrides):
    kwargs = {
        "target_type": "command",
        "tid": "1770000000000000001",
        "function_target": None,
        "process_target": "echo",
        "agent": None,
        "args": [],
        "kwargs": {},
        "env": {},
        "working_dir": None,
        "timeout": None,
        "limits": None,
        "monitor_class": None,
        "monitor_interval": None,
        "runner_options": options,
        "bundle_root": None,
        "persistent": False,
        "interactive": False,
    }
    kwargs.update(overrides)
    return runner_plugin.create_runner(**kwargs)


@pytest.mark.parametrize("capability", ["persistent", "interactive"])
def test_microsandbox_preserves_both_capability_checks(capability):
    runner_plugin = get_runner_plugin()
    with pytest.raises(ValueError, match=capability):
        runner_plugin.validate_taskspec(_payload(**{capability: True}))
    with pytest.raises(ValueError, match=capability):
        _create_command_runner_for_validation(
            runner_plugin, {"image": "python:3.12-alpine"}, **{capability: True}
        )
