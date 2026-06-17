"""Microsandbox SDK adapter tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from weft_microsandbox import _runtime
from weft_microsandbox._options import MicrosandboxMount
from weft_microsandbox._runtime import (
    FileCopyBack,
    MicrosandboxRunSpec,
    MicrosandboxRuntime,
    WorkspaceSpec,
)

pytestmark = [pytest.mark.shared]


@dataclass
class _FakeOutput:
    exit_code: int = 0
    stdout_text: str = "ok"
    stderr_text: str = ""


class _FakeFs:
    def __init__(self) -> None:
        self.copied_from_host: list[tuple[str, str]] = []
        self.copied_to_host: list[tuple[str, str]] = []
        self.mkdirs: list[str] = []

    async def mkdir(self, path: str) -> None:
        self.mkdirs.append(path)

    async def copy_from_host(self, host_path: str, guest_path: str) -> None:
        self.copied_from_host.append((host_path, guest_path))

    async def copy_to_host(self, guest_path: str, host_path: str) -> None:
        self.copied_to_host.append((guest_path, host_path))


class _FakeSandbox:
    created_kwargs: dict[str, Any] = {}
    created_name = ""
    last_instance: _FakeSandbox | None = None
    removed: list[str] = []

    def __init__(self, name: str) -> None:
        self._name = name
        self.fs = _FakeFs()
        self.exec_calls: list[dict[str, Any]] = []
        self.stopped = False
        _FakeSandbox.last_instance = self

    @classmethod
    async def create(cls, name: str, **kwargs: Any) -> _FakeSandbox:
        cls.created_name = name
        cls.created_kwargs = kwargs
        return cls(name)

    @classmethod
    async def remove(cls, name: str) -> None:
        cls.removed.append(name)

    async def name(self) -> str:
        return self._name

    async def exec(
        self,
        cmd: str,
        args: list[str],
        **kwargs: Any,
    ) -> _FakeOutput:
        self.exec_calls.append({"cmd": cmd, "args": args, **kwargs})
        return _FakeOutput()

    async def stop(self, timeout: float | None = None) -> None:
        del timeout
        self.stopped = True


class _FakeNetwork:
    @staticmethod
    def none() -> str:
        return "network:none"

    @staticmethod
    def allow_all() -> str:
        return "network:allow"


class _FakeVolume:
    @staticmethod
    def bind(path: str, *, readonly: bool) -> tuple[str, bool]:
        return (path, readonly)


class _FakeRlimit:
    @staticmethod
    def nofile(limit: int) -> tuple[str, int]:
        return ("nofile", limit)


class _FakeSDK:
    Sandbox = _FakeSandbox
    Network = _FakeNetwork
    Volume = _FakeVolume
    Rlimit = _FakeRlimit


def test_runtime_passes_network_mounts_limits_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "_load_sdk", lambda: _FakeSDK)
    started: list[str] = []

    result = MicrosandboxRuntime().run(
        MicrosandboxRunSpec(
            name="weft-test",
            image="python:3.12",
            command=("python", "-c", "print(1)"),
            env={"A": "B"},
            cwd="/work",
            network="none",
            workspace=WorkspaceSpec(
                mode="copy",
                source="/host/work",
                target="/work",
            ),
            mounts=(MicrosandboxMount("/host/input", "/input", True),),
            timeout_seconds=5.0,
            stdin_text="stdin",
            memory_mb=512,
            cpus=1.5,
            max_fds=64,
            guest_dirs=("/tmp/weft",),
            copy_back=(FileCopyBack("/tmp/weft/out.txt", "/host/out.txt"),),
        ),
        on_started=lambda event: started.append(event.sandbox_name),
    )

    assert result.stdout == "ok"
    assert started == ["weft-test"]
    assert _FakeSandbox.created_kwargs["image"] == "python:3.12"
    assert _FakeSandbox.created_kwargs["network"] == "network:none"
    assert _FakeSandbox.created_kwargs["memory"] == 512
    assert _FakeSandbox.created_kwargs["cpus"] == 1.5
    assert _FakeSandbox.created_kwargs["volumes"] == {"/input": ("/host/input", True)}
    sandbox = _FakeSandbox.last_instance
    assert sandbox is not None
    assert sandbox.fs.mkdirs == ["/tmp/weft"]
    assert sandbox.fs.copied_from_host == [("/host/work", "/work")]
    assert sandbox.fs.copied_to_host == [("/tmp/weft/out.txt", "/host/out.txt")]
    assert sandbox.exec_calls == [
        {
            "cmd": "python",
            "args": ["-c", "print(1)"],
            "cwd": "/work",
            "env": {"A": "B"},
            "timeout": 5.0,
            "stdin": "stdin",
            "rlimits": [("nofile", 64)],
        }
    ]
    assert sandbox.stopped is True
    assert "weft-test" in _FakeSandbox.removed
