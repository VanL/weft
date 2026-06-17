"""Microsandbox command runner tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from weft.core.tasks.runner import TaskRunner
from weft.ext import RunnerHandle
from weft_microsandbox._runtime import MicrosandboxRunResult, MicrosandboxRunSpec
from weft_microsandbox.plugin import MicrosandboxRunner, get_runner_plugin

pytestmark = [pytest.mark.shared]


class FakeRuntime:
    last_spec: MicrosandboxRunSpec | None = None

    def check_importable(self) -> None:
        return None

    def run(
        self,
        spec: MicrosandboxRunSpec,
        *,
        on_started: Callable[[object], None] | None = None,
    ) -> MicrosandboxRunResult:
        FakeRuntime.last_spec = spec
        if on_started is not None:
            from weft_microsandbox._runtime import MicrosandboxStarted

            on_started(MicrosandboxStarted("sandbox-1", "sandbox-1"))
        return MicrosandboxRunResult(
            sandbox_id="sandbox-1",
            sandbox_name="sandbox-1",
            exit_code=0,
            stdout="hello\n",
            stderr="",
            timed_out=False,
            duration=0.01,
        )


def test_command_runner_builds_guest_command_and_handle() -> None:
    handles: list[RunnerHandle] = []
    runner = MicrosandboxRunner(
        target_type="command",
        tid="1234567890123456789",
        process_target="python",
        agent=None,
        args=["-c", "print('base')"],
        env={"A": "B"},
        working_dir=None,
        timeout=3.0,
        limits=None,
        runner_options={"image": "python:3.12", "cwd": "/"},
        runtime=FakeRuntime(),
    )

    outcome = runner.run_with_hooks(
        {"args": ["-c", "print('item')"]},
        on_runtime_handle_started=handles.append,
    )

    assert outcome.status == "ok"
    assert outcome.value == "hello\n"
    assert outcome.runtime_handle is not None
    assert outcome.runtime_handle.id == "sandbox-1"
    assert outcome.runtime_handle.control == {"authority": "runner"}
    assert outcome.runtime_handle.metadata["network"] == "none"
    assert handles == [outcome.runtime_handle]
    assert FakeRuntime.last_spec is not None
    assert FakeRuntime.last_spec.command == (
        "python",
        "-c",
        "print('base')",
        "-c",
        "print('item')",
    )
    assert FakeRuntime.last_spec.network == "none"


def test_task_runner_uses_microsandbox_plugin_with_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import weft.core.tasks.runner as task_runner_module
    import weft_microsandbox.plugin as plugin_module

    monkeypatch.setattr(plugin_module, "MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr(
        task_runner_module,
        "require_runner_plugin",
        lambda _name: get_runner_plugin(),
    )
    runner = TaskRunner(
        target_type="command",
        tid="1234567890123456789",
        function_target=None,
        process_target="echo",
        agent=None,
        args=[],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=None,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
        runner_name="microsandbox",
        runner_options={"image": "alpine"},
    )

    outcome = runner.run("ignored")

    assert outcome.status == "ok"
    assert outcome.value == "hello\n"


def test_command_runner_maps_nonzero_exit_to_error() -> None:
    class ErrorRuntime(FakeRuntime):
        def run(
            self,
            spec: MicrosandboxRunSpec,
            *,
            on_started: Callable[[object], None] | None = None,
        ) -> MicrosandboxRunResult:
            del spec, on_started
            return MicrosandboxRunResult(
                sandbox_id="sandbox-1",
                sandbox_name="sandbox-1",
                exit_code=2,
                stdout="",
                stderr="bad",
                timed_out=False,
                duration=0.01,
            )

    runner = MicrosandboxRunner(
        target_type="command",
        tid=None,
        process_target="false",
        agent=None,
        args=[],
        env={},
        working_dir=None,
        timeout=None,
        limits=None,
        runner_options={"image": "alpine"},
        runtime=ErrorRuntime(),
    )

    outcome = runner.run(None)

    assert outcome.status == "error"
    assert outcome.error == "bad"
    assert outcome.stderr == "bad"
