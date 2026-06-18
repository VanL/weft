"""Microsandbox command runner tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from weft.ext import RunnerHandle
from weft_microsandbox._runtime import MicrosandboxRunResult, MicrosandboxRunSpec
from weft_microsandbox.plugin import MicrosandboxRunner

pytestmark = [pytest.mark.shared]


class RecordingRuntime:
    last_spec: MicrosandboxRunSpec | None = None

    def check_importable(self) -> None:
        return None

    def run(
        self,
        spec: MicrosandboxRunSpec,
        *,
        on_started: Callable[[object], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> MicrosandboxRunResult:
        del cancel_requested
        RecordingRuntime.last_spec = spec
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
        runtime=RecordingRuntime(),
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
    assert RecordingRuntime.last_spec is not None
    assert RecordingRuntime.last_spec.command == (
        "python",
        "-c",
        "print('base')",
        "-c",
        "print('item')",
    )
    assert RecordingRuntime.last_spec.network == "none"


def test_command_runner_maps_nonzero_exit_to_error() -> None:
    class ErrorRuntime(RecordingRuntime):
        def run(
            self,
            spec: MicrosandboxRunSpec,
            *,
            on_started: Callable[[object], None] | None = None,
            cancel_requested: Callable[[], bool] | None = None,
        ) -> MicrosandboxRunResult:
            del spec, on_started, cancel_requested
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


def test_command_runner_maps_cancelled_runtime_result() -> None:
    class CancelRuntime(RecordingRuntime):
        def run(
            self,
            spec: MicrosandboxRunSpec,
            *,
            on_started: Callable[[object], None] | None = None,
            cancel_requested: Callable[[], bool] | None = None,
        ) -> MicrosandboxRunResult:
            del spec, on_started
            assert cancel_requested is not None
            assert cancel_requested() is True
            return MicrosandboxRunResult(
                sandbox_id="sandbox-1",
                sandbox_name="sandbox-1",
                exit_code=None,
                stdout="",
                stderr="Target execution cancelled",
                timed_out=False,
                duration=0.01,
                cancelled=True,
            )

    runner = MicrosandboxRunner(
        target_type="command",
        tid=None,
        process_target="sleep",
        agent=None,
        args=["10"],
        env={},
        working_dir=None,
        timeout=None,
        limits=None,
        runner_options={"image": "alpine"},
        runtime=CancelRuntime(),
    )

    outcome = runner.run_with_hooks(None, cancel_requested=lambda: True)

    assert outcome.status == "cancelled"
    assert outcome.error == "Target execution cancelled"
