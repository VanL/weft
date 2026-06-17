"""Opt-in smoke tests for the real Microsandbox runtime."""

from __future__ import annotations

import os

import pytest

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
