"""Microsandbox runner control hook tests."""

from __future__ import annotations

import pytest

from weft.ext import RunnerHandle
from weft_microsandbox._runtime import MicrosandboxDescription
from weft_microsandbox.plugin import MicrosandboxRunnerPlugin

pytestmark = [pytest.mark.shared]


class ControlRuntime:
    stopped: list[tuple[str, float]] = []
    killed: list[tuple[str, float]] = []

    def stop(self, sandbox_id: str, *, timeout: float = 2.0) -> bool:
        self.stopped.append((sandbox_id, timeout))
        return True

    def kill(self, sandbox_id: str, *, timeout: float = 2.0) -> bool:
        self.killed.append((sandbox_id, timeout))
        return True

    def describe(self, sandbox_id: str) -> MicrosandboxDescription:
        return MicrosandboxDescription(
            sandbox_id=sandbox_id,
            state="running",
            metadata={"from_runtime": True},
        )


def _handle() -> RunnerHandle:
    return RunnerHandle(
        runner="microsandbox",
        kind="sandboxed-process",
        id="sandbox-123",
        control={"authority": "runner"},
        observations={"sandbox_name": "sandbox-123"},
        metadata={"image": "alpine"},
    )


def test_stop_and_kill_use_handle_id(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = ControlRuntime()
    import weft_microsandbox.plugin as plugin_module

    monkeypatch.setattr(plugin_module, "MicrosandboxRuntime", lambda: runtime)
    plugin = MicrosandboxRunnerPlugin()

    assert plugin.stop(_handle(), timeout=3.0) is True
    assert plugin.kill(_handle(), timeout=4.0) is True
    assert runtime.stopped == [("sandbox-123", 3.0)]
    assert runtime.killed == [("sandbox-123", 4.0)]


def test_describe_merges_handle_and_runtime_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ControlRuntime()
    import weft_microsandbox.plugin as plugin_module

    monkeypatch.setattr(plugin_module, "MicrosandboxRuntime", lambda: runtime)
    plugin = MicrosandboxRunnerPlugin()

    description = plugin.describe(_handle())

    assert description is not None
    assert description.id == "sandbox-123"
    assert description.state == "running"
    assert description.metadata == {
        "sandbox_name": "sandbox-123",
        "image": "alpine",
        "from_runtime": True,
    }
