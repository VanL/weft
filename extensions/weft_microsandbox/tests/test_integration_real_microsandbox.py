"""Opt-in smoke tests for the real Microsandbox runtime."""

from __future__ import annotations

import os

import pytest

from weft.core.tasks.runner import TaskRunner
from weft.ext import RunnerHandle
from weft_microsandbox._runtime import (
    MicrosandboxRunSpec,
    MicrosandboxRuntime,
    MicrosandboxRuntimeError,
    WorkspaceSpec,
)

pytestmark = [pytest.mark.shared, pytest.mark.slow]


def _runtime_or_skip() -> MicrosandboxRuntime:
    if os.environ.get("WEFT_MICROSANDBOX_TEST_ENABLE") != "1":
        pytest.skip(
            "set WEFT_MICROSANDBOX_TEST_ENABLE=1 to run real Microsandbox tests"
        )
    runtime = MicrosandboxRuntime()
    try:
        runtime.check_preflight()
    except MicrosandboxRuntimeError as exc:
        pytest.skip(f"Microsandbox runtime unavailable: {exc}")
    return runtime


def _image() -> str:
    return os.environ.get("WEFT_MICROSANDBOX_TEST_IMAGE", "python:3.12-alpine")


def test_real_microsandbox_prints_hello() -> None:
    runtime = _runtime_or_skip()

    result = runtime.run(
        MicrosandboxRunSpec(
            name="weft-smoke-hello",
            image=_image(),
            command=("python", "-c", "print('hello')"),
            env={},
            cwd="/",
            network="none",
            workspace=WorkspaceSpec(),
            timeout_seconds=10.0,
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"


def test_real_microsandbox_network_none_blocks_outbound_socket() -> None:
    runtime = _runtime_or_skip()

    result = runtime.run(
        MicrosandboxRunSpec(
            name="weft-smoke-network-none",
            image=_image(),
            command=(
                "python",
                "-c",
                (
                    "import socket; "
                    "s=socket.socket(); s.settimeout(2); "
                    "s.connect(('1.1.1.1', 443))"
                ),
            ),
            env={},
            cwd="/",
            network="none",
            workspace=WorkspaceSpec(),
            timeout_seconds=5.0,
        )
    )

    assert result.exit_code != 0 or result.timed_out


def test_real_task_runner_lifecycle_uses_microsandbox_plugin() -> None:
    _runtime_or_skip()
    handles: list[RunnerHandle] = []
    stdout_chunks: list[str] = []
    runner = TaskRunner(
        target_type="command",
        tid="1234567890123456789",
        function_target=None,
        process_target="python",
        agent=None,
        args=["-c", "print('task-runner-hello')"],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=10.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
        runner_name="microsandbox",
        runner_options={"image": _image(), "cwd": "/"},
    )

    outcome = runner.run_with_hooks(
        None,
        on_runtime_handle_started=handles.append,
        on_stdout_chunk=lambda chunk, _final: stdout_chunks.append(chunk),
    )

    assert outcome.status == "ok"
    assert isinstance(outcome.value, str)
    assert outcome.value.strip() == "task-runner-hello"
    assert stdout_chunks
    assert handles
    assert handles[0].runner == "microsandbox"
    assert handles[0].control == {"authority": "runner"}


def test_real_task_runner_cooperative_cancel_interrupts_guest_exec() -> None:
    _runtime_or_skip()
    runner = TaskRunner(
        target_type="command",
        tid="1234567890123456789",
        function_target=None,
        process_target="python",
        agent=None,
        args=["-c", "import time; time.sleep(30)"],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=60.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
        runner_name="microsandbox",
        runner_options={"image": _image(), "cwd": "/"},
    )

    outcome = runner.run_with_hooks(None, cancel_requested=lambda: True)

    assert outcome.status == "cancelled"
