"""Tests for the macOS sandbox runner extension package."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from weft_macos_sandbox import get_runner_plugin, plugin

pytestmark = [pytest.mark.shared]


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
        runner_options={"profile": str(profile)},
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
        runner_options={
            "profile": str(profile),
            "env_passthrough": ["WEFT_TEST_OPTIN"],
        },
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
            runner_options={"profile": str(profile), "env_passthrough": "oops"},
        )
