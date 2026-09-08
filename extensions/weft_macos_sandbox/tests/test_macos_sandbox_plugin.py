"""Tests for the macOS sandbox runner extension package."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import psutil
import pytest
import weft_macos_sandbox
from weft_macos_sandbox import plugin
from weft_macos_sandbox.plugin import get_runner_plugin

from weft.liveness import registry as liveness_registry
from weft.liveness.models import HostProcessObservation

pytestmark = [pytest.mark.shared]


def test_package_root_does_not_export_runner_plugin_factory() -> None:
    assert not hasattr(weft_macos_sandbox, "get_runner_plugin")


def test_macos_sandbox_plugin_registers_its_entry_point_liveness_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(liveness_registry, "_runtime_liveness_probes", {})

    get_runner_plugin()

    assert set(liveness_registry._runtime_liveness_probes) == {"macos-sandbox"}


def test_macos_sandbox_liveness_uses_scoped_pid_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = plugin.RunnerHandle(
        runner="macos-sandbox",
        kind="sandboxed-process",
        id="4321",
        control={"authority": "runner"},
        observations={
            "host_processes": [{"pid": 4321, "create_time": 123.456}],
        },
    )
    monkeypatch.setattr(
        plugin,
        "inspect_host_process",
        lambda pid, create_time: HostProcessObservation(
            "live" if pid == 4321 and create_time == 123.456 else "stale",
            "test",
        ),
    )

    assert plugin._macos_sandbox_runtime_liveness(handle, 0.75) == "live"

    monkeypatch.setattr(
        plugin,
        "inspect_host_process",
        lambda pid, create_time: HostProcessObservation("stale", "test"),
    )
    assert plugin._macos_sandbox_runtime_liveness(handle, 0.75) == "stale"


def test_macos_sandbox_liveness_is_unknown_without_scoped_identity() -> None:
    handle = plugin.RunnerHandle(
        runner="macos-sandbox",
        kind="sandboxed-process",
        id="4321",
        control={"authority": "runner"},
        observations={},
    )

    assert plugin._macos_sandbox_runtime_liveness(handle, 0.75) == "unknown"


def test_macos_sandbox_liveness_rejects_non_finite_process_identity() -> None:
    handle = plugin.RunnerHandle(
        runner="macos-sandbox",
        kind="sandboxed-process",
        id="4321",
        control={"authority": "runner"},
        observations={
            "host_processes": [{"pid": 4321, "create_time": float("nan")}],
        },
    )

    assert plugin._macos_sandbox_runtime_liveness(handle, 0.75) == "unknown"


def test_runner_constructor_does_not_accept_broker_context() -> None:
    parameters = inspect.signature(plugin.MacOSSandboxRunner).parameters

    assert "db_path" not in parameters
    assert "config" not in parameters


def test_macos_sandbox_runner_publishes_runner_authority_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = tmp_path / "sandbox.sb"
    profile.write_text("(version 1)\n(allow default)\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    class FakeProcess:
        pid = 4321

    def fake_run_monitored_subprocess(**kwargs: Any) -> plugin.RunnerOutcome:
        runtime_handle = kwargs["runtime_handle"]
        captured["runtime_handle"] = runtime_handle
        return plugin.RunnerOutcome(
            status="ok",
            value=None,
            error=None,
            stdout="",
            stderr="",
            returncode=0,
            duration=0.0,
            runtime_handle=runtime_handle,
        )

    monkeypatch.setattr(
        plugin.subprocess, "Popen", lambda *args, **kwargs: FakeProcess()
    )
    monkeypatch.setattr(plugin, "process_create_time", lambda pid: 123.456)
    monkeypatch.setattr(
        plugin, "run_monitored_subprocess", fake_run_monitored_subprocess
    )

    runner = plugin.MacOSSandboxRunner(
        process_target="python3",
        args=["-c", "print('hello')"],
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.01,
        options=plugin._parse_options({"profile": str(profile)}),
    )

    outcome = runner.run_with_hooks({})

    handle = outcome.runtime_handle
    assert handle is captured["runtime_handle"]
    assert handle.control["authority"] == "runner"
    assert handle.observations["host_pids"] == [4321]
    assert handle.observations["host_processes"] == [
        {"pid": 4321, "create_time": 123.456}
    ]


def test_macos_sandbox_plugin_uses_pid_identity_for_control_and_describe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner_plugin = get_runner_plugin()
    handle = plugin.RunnerHandle(
        runner="macos-sandbox",
        kind="sandboxed-process",
        id="4321",
        control={"authority": "runner"},
        observations={
            "host_pids": [4321],
            "host_processes": [{"pid": 4321, "create_time": 123.456}],
        },
        metadata={"profile": "sandbox.sb"},
    )
    stopped: list[int] = []
    killed: list[int] = []

    monkeypatch.setattr(
        plugin,
        "_host_pid_matches",
        lambda pid, create_time: pid == 4321 and create_time == 123.456,
    )
    monkeypatch.setattr(
        plugin,
        "terminate_process_tree",
        lambda pid, **kwargs: stopped.append(pid),
    )
    monkeypatch.setattr(
        plugin,
        "kill_process_tree",
        lambda pid, **kwargs: killed.append(pid),
    )

    description = runner_plugin.describe(handle)

    assert description is not None
    assert description.state == "running"
    assert description.metadata["profile"] == "sandbox.sb"
    assert runner_plugin.stop(handle) is True
    assert runner_plugin.kill(handle) is True
    assert stopped == [4321]
    assert killed == [4321]


def test_macos_sandbox_plugin_rejects_reused_pid_for_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner_plugin = get_runner_plugin()
    handle = plugin.RunnerHandle(
        runner="macos-sandbox",
        kind="sandboxed-process",
        id="4321",
        control={"authority": "runner"},
        observations={
            "host_pids": [4321],
            "host_processes": [{"pid": 4321, "create_time": 123.456}],
        },
    )

    monkeypatch.setattr(plugin, "_host_pid_matches", lambda pid, create_time: False)
    monkeypatch.setattr(
        plugin,
        "terminate_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("stale PID must not be stopped")
        ),
    )
    monkeypatch.setattr(
        plugin,
        "kill_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("stale PID must not be killed")
        ),
    )

    description = runner_plugin.describe(handle)

    assert description is not None
    assert description.state == "missing"
    assert runner_plugin.stop(handle) is False
    assert runner_plugin.kill(handle) is False


def test_macos_sandbox_runner_requires_profile() -> None:
    plugin = get_runner_plugin()

    with pytest.raises(ValueError, match="requires spec.runner.options.profile"):
        plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    "runner": {"name": "macos-sandbox", "options": {}},
                }
            }
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"spec": []}, "spec must be an object"),
        (
            {"spec": {"type": "command", "runner": []}},
            "spec.runner must be an object",
        ),
        (
            {
                "spec": {
                    "type": "command",
                    "runner": {"name": "macos-sandbox", "options": []},
                }
            },
            "spec.runner.options must be an object",
        ),
    ],
)
def test_macos_sandbox_validation_rejects_non_object_sections_as_value_error(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        get_runner_plugin().validate_taskspec(payload)
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == message
    assert exc_info.value.__cause__ is None


def test_macos_sandbox_runner_preflight_checks_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin = get_runner_plugin()
    profile = tmp_path / "sandbox.sb"
    profile.write_text("(version 1)\n(allow default)\n", encoding="utf-8")
    monkeypatch.setattr("weft_macos_sandbox.plugin.sys.platform", "darwin")
    monkeypatch.setattr("weft_macos_sandbox.plugin.shutil.which", lambda name: None)

    with pytest.raises(ValueError, match="sandbox-exec"):
        plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    "runner": {
                        "name": "macos-sandbox",
                        "options": {"profile": str(profile)},
                    },
                }
            },
            preflight=True,
        )


def test_sandbox_child_env_is_allowlisted_not_inherited(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = tmp_path / "sandbox.sb"
    profile.write_text("(version 1)\n(allow default)\n", encoding="utf-8")
    monkeypatch.setenv("WEFT_TEST_SECRET", "leak-me")
    monkeypatch.setenv("WEFT_TEST_OPTIN", "forwarded")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    captured: dict[str, Any] = {}

    class FakeProcess:
        pid = 4321

    def fake_run_monitored_subprocess(**kwargs: Any) -> plugin.RunnerOutcome:
        return plugin.RunnerOutcome(
            status="ok",
            value=None,
            error=None,
            stdout="",
            stderr="",
            returncode=0,
            duration=0.0,
            runtime_handle=kwargs["runtime_handle"],
        )

    def fake_popen(argv: Any, **kwargs: Any) -> Any:
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(plugin.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(plugin, "process_create_time", lambda pid: 123.456)
    monkeypatch.setattr(
        plugin, "run_monitored_subprocess", fake_run_monitored_subprocess
    )

    runner = plugin.MacOSSandboxRunner(
        process_target="python3",
        args=[],
        env={"SPEC_VAR": "from-spec"},
        working_dir=None,
        timeout=None,
        limits=None,
        monitor_class=None,
        monitor_interval=None,
        options=plugin._parse_options(
            {
                "profile": str(profile),
                "env_passthrough": ["WEFT_TEST_OPTIN"],
            }
        ),
    )
    runner.run_with_hooks({})

    env = captured["env"]
    assert env["SPEC_VAR"] == "from-spec"
    assert env["WEFT_TEST_OPTIN"] == "forwarded"
    assert env["PATH"] == "/usr/bin:/bin"
    assert "WEFT_TEST_SECRET" not in env


def test_sandbox_env_passthrough_must_be_string_list(tmp_path: Path) -> None:
    profile = tmp_path / "sandbox.sb"
    profile.write_text("(version 1)\n(allow default)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="env_passthrough"):
        plugin.MacOSSandboxRunner(
            process_target="python3",
            args=[],
            env={},
            working_dir=None,
            timeout=None,
            limits=None,
            monitor_class=None,
            monitor_interval=None,
            options=plugin._parse_options(
                {"profile": str(profile), "env_passthrough": "oops"}
            ),
        )


def test_factory_restores_replaced_liveness_registry(monkeypatch) -> None:
    monkeypatch.setattr(liveness_registry, "_runtime_liveness_probes", {})
    get_runner_plugin()
    liveness_registry.register_runtime_liveness_probe(
        "macos-sandbox", lambda handle, budget: "unknown"
    )
    replaced = liveness_registry._runtime_liveness_probes["macos-sandbox"]
    get_runner_plugin()
    assert liveness_registry._runtime_liveness_probes["macos-sandbox"] is not replaced


@pytest.mark.parametrize(
    "evidence,expected",
    [("matching", "live"), ("mismatched", "stale"), ("malformed", "unknown")],
)
def test_alias_routed_macos_liveness_uses_real_identity(
    monkeypatch, evidence, expected
) -> None:
    monkeypatch.setattr(liveness_registry, "_runtime_liveness_probes", {})
    liveness_registry.register_runtime_liveness_probe(
        "macos-sandbox", plugin._macos_sandbox_runtime_liveness
    )
    process = psutil.Process()
    observations = {
        "liveness_provider": "macos-sandbox",
        "host_processes": [
            {
                "pid": process.pid,
                "create_time": process.create_time() if evidence == "matching" else 1.0,
            }
        ],
    }
    if evidence == "malformed":
        observations["host_processes"] = [{"pid": process.pid}]
    handle = plugin.RunnerHandle(
        runner="alias",
        control={"authority": "runner"},
        kind="process",
        id=str(process.pid),
        observations=observations,
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


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"profile": ""},
        {"profile": "profile.sb", "env_passthrough": "bad"},
        {"profile": "profile.sb", "env_passthrough": [""]},
    ],
)
def test_macos_option_rejections_match_validate_create_sequence(options):
    runner_plugin = get_runner_plugin()
    with pytest.raises(ValueError) as validated:
        runner_plugin.validate_taskspec(
            {"spec": {"type": "command", "runner": {"options": options}}}
        )
    with pytest.raises(ValueError) as created:
        _create_command_runner_for_validation(runner_plugin, options)
    assert str(created.value) == str(validated.value)


@pytest.mark.parametrize("capability", ["persistent", "interactive"])
def test_macos_preserves_direct_create_capability_behavior(capability):
    runner_plugin = get_runner_plugin()
    options = {"profile": "profile.sb"}
    with pytest.raises(ValueError, match=capability):
        runner_plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    capability: True,
                    "runner": {"options": options},
                }
            }
        )
    assert (
        _create_command_runner_for_validation(
            runner_plugin, options, **{capability: True}
        )
        is not None
    )
