"""Microsandbox SDK adapter contract tests."""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
from typing import Any

import pytest

from weft_microsandbox import _runtime
from weft_microsandbox._options import MicrosandboxMount
from weft_microsandbox._runtime import (
    FileCopyBack,
    FileCopyIntoGuest,
    MicrosandboxRunSpec,
    MicrosandboxRuntime,
    WorkspaceSpec,
)

pytestmark = [pytest.mark.shared]


def _sdk() -> Any:
    return pytest.importorskip("microsandbox")


def test_installed_sdk_exposes_adapter_api_surface() -> None:
    sdk = _sdk()

    sandbox_create = inspect.signature(sdk.Sandbox.create)
    sandbox_get = inspect.signature(sdk.Sandbox.get)
    sandbox_remove = inspect.signature(sdk.Sandbox.remove)
    volume_bind = inspect.signature(sdk.Volume.bind)
    rlimit_nofile = inspect.signature(sdk.Rlimit.nofile)

    assert "name" in sandbox_create.parameters
    assert any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in sandbox_create.parameters.values()
    )
    assert tuple(sandbox_get.parameters) == ("name",)
    assert tuple(sandbox_remove.parameters) == ("name",)
    assert "path" in volume_bind.parameters
    assert "readonly" in volume_bind.parameters
    assert "limit" in rlimit_nofile.parameters
    assert callable(sdk.Network.none)
    assert callable(sdk.Network.allow_all)
    assert callable(getattr(sdk, "is_installed", None))


def test_sandbox_name_handles_current_sdk_attribute_shape() -> None:
    class AttributeNameSandbox:
        name = "sandbox-attribute"

    assert (
        asyncio.run(_runtime._sandbox_name(AttributeNameSandbox(), fallback="fallback"))
        == "sandbox-attribute"
    )


def test_sandbox_name_handles_legacy_async_method_shape() -> None:
    class AsyncMethodNameSandbox:
        async def name(self) -> str:
            return "sandbox-method"

    assert (
        asyncio.run(
            _runtime._sandbox_name(AsyncMethodNameSandbox(), fallback="fallback")
        )
        == "sandbox-method"
    )


def test_describe_reports_optional_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Refreshed:
        name = "sandbox-name"
        status = "running"
        created_at = "created"
        updated_at = "updated"

        @staticmethod
        def config() -> object:
            raise RuntimeError("sensitive configuration failure")

    class Handle:
        @staticmethod
        async def refresh() -> Refreshed:
            return Refreshed()

    class SandboxAPI:
        @staticmethod
        async def get(sandbox_id: str) -> Handle:
            assert sandbox_id == "sensitive-sandbox-id"
            return Handle()

    class SDK:
        Sandbox = SandboxAPI

    monkeypatch.setattr(_runtime, "_load_sdk", lambda: SDK())
    caplog.set_level(logging.WARNING, logger="weft_microsandbox._runtime")

    description = asyncio.run(
        MicrosandboxRuntime()._describe_async("sensitive-sandbox-id")
    )

    assert description is not None
    assert description.state == "running"
    assert description.metadata == {
        "sandbox_name": "sandbox-name",
        "created_at": "created",
        "updated_at": "updated",
    }
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to read Microsandbox configuration metadata"
    ]
    assert caplog.records[0].exc_info is None
    assert "sensitive" not in caplog.text


def test_runtime_builds_network_volume_and_rlimit_from_real_sdk(tmp_path: Path) -> None:
    sdk = _sdk()
    source = tmp_path / "input"
    source.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec = MicrosandboxRunSpec(
        name="weft-test",
        image="python:3.12",
        command=("python", "-c", "print(1)"),
        env={"A": "B"},
        cwd="/work",
        network="none",
        workspace=WorkspaceSpec(
            mode="mount-read-only",
            source=str(workspace),
            target="/work",
        ),
        mounts=(MicrosandboxMount(str(source), "/input", True),),
        max_fds=64,
    )

    network = _runtime._network_config(sdk, spec.network)
    volumes = _runtime._volume_config(sdk, spec)
    rlimits = _runtime._rlimits(sdk, spec)

    assert isinstance(network, sdk.Network)
    assert network.policy == "none"
    assert set(volumes) == {"/input", "/work"}
    assert volumes["/input"].bind == str(source)
    assert volumes["/input"].readonly is True
    assert volumes["/work"].bind == str(workspace)
    assert volumes["/work"].readonly is True
    assert rlimits is not None
    assert len(rlimits) == 1
    assert isinstance(rlimits[0], sdk.Rlimit)
    assert rlimits[0].soft == 64
    assert rlimits[0].hard == 64


def test_runtime_import_check_uses_installed_sdk() -> None:
    _sdk()

    MicrosandboxRuntime().check_importable()


def test_run_spec_can_request_host_paths_copied_into_guest(tmp_path: Path) -> None:
    source = tmp_path / "provider-inputs"
    source.mkdir()
    copy = FileCopyIntoGuest(host_path=str(source), guest_path="/tmp/provider-inputs")
    spec = MicrosandboxRunSpec(
        name="weft-copy-contract",
        image="python:3.12",
        command=("python", "-c", "print(1)"),
        env={},
        cwd="/",
        network="none",
        workspace=WorkspaceSpec(),
        copy_into_guest=(copy,),
    )

    assert spec.copy_into_guest == (copy,)


def test_copy_into_guest_recursively_copies_directory_contents(tmp_path: Path) -> None:
    host_root = tmp_path / "provider-inputs"
    nested = host_root / "nested"
    nested.mkdir(parents=True)
    config = host_root / "claude-mcp.json"
    config.write_text("{}", encoding="utf-8")
    nested_file = nested / "tool.json"
    nested_file.write_text("{}", encoding="utf-8")

    class Fs:
        def __init__(self) -> None:
            self.mkdirs: list[str] = []
            self.copied: list[tuple[str, str]] = []

        async def mkdir(self, path: str) -> None:
            self.mkdirs.append(path)

        async def copy_from_host(self, host_path: str, guest_path: str) -> None:
            self.copied.append((host_path, guest_path))

    first_fs = Fs()
    second_fs = Fs()
    assert first_fs.mkdirs is not second_fs.mkdirs
    assert first_fs.copied is not second_fs.copied

    class Sandbox:
        fs = Fs()

    sandbox = Sandbox()

    asyncio.run(
        _runtime._copy_into_guest(
            sandbox,
            (
                FileCopyIntoGuest(
                    host_path=str(host_root),
                    guest_path="/tmp/weft-provider",
                ),
            ),
        )
    )

    assert "/tmp/weft-provider" in sandbox.fs.mkdirs
    assert "/tmp/weft-provider/nested" in sandbox.fs.mkdirs
    assert sorted(sandbox.fs.copied) == [
        (str(config), "/tmp/weft-provider/claude-mcp.json"),
        (str(nested_file), "/tmp/weft-provider/nested/tool.json"),
    ]


def test_copy_back_reports_failure_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[tuple[str, str]] = []

    class Fs:
        async def copy_to_host(self, guest_path: str, host_path: str) -> None:
            calls.append((guest_path, host_path))
            if guest_path == "/sensitive/first":
                raise RuntimeError("sensitive copy-back failure")

    class Sandbox:
        fs = Fs()

    copy_back = (
        FileCopyBack("/sensitive/first", "/host/sensitive-first"),
        FileCopyBack("/sensitive/second", "/host/sensitive-second"),
    )
    caplog.set_level(logging.WARNING, logger="weft_microsandbox._runtime")

    asyncio.run(_runtime._copy_back_files(Sandbox(), copy_back))

    assert calls == [
        ("/sensitive/first", "/host/sensitive-first"),
        ("/sensitive/second", "/host/sensitive-second"),
    ]
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to copy Microsandbox output to the host"
    ]
    assert caplog.records[0].exc_info is None
    assert "sensitive" not in caplog.text


def test_exec_with_cancel_maps_cancelled_sdk_exec_task() -> None:
    class CancelOnKillSandbox:
        killed = False
        exec_task: asyncio.Task[object] | None = None

        async def exec(self, *_args: object, **_kwargs: object) -> object:
            self.exec_task = asyncio.current_task()
            while True:
                await asyncio.sleep(60.0)

        async def kill(self) -> None:
            self.killed = True
            assert self.exec_task is not None
            self.exec_task.cancel()

    async def _run() -> tuple[object | None, bool]:
        sandbox = CancelOnKillSandbox()
        result = await _runtime._exec_with_cancel(
            object(),
            sandbox,
            MicrosandboxRunSpec(
                name="weft-cancel",
                image="python:3.12",
                command=("python", "-c", "print(1)"),
                env={},
                cwd="/",
                network="none",
                workspace=WorkspaceSpec(),
            ),
            cancel_requested=lambda: True,
        )
        return result, sandbox.killed

    result, killed = asyncio.run(_run())

    assert result is None
    assert killed is True


def test_exec_with_cancel_maps_post_kill_exec_output_to_cancelled() -> None:
    class CompleteAfterKillSandbox:
        killed = False

        async def exec(self, *_args: object, **_kwargs: object) -> object:
            while not self.killed:
                await asyncio.sleep(0.01)
            return object()

        async def kill(self) -> None:
            self.killed = True

    async def _run() -> tuple[object | None, bool]:
        sandbox = CompleteAfterKillSandbox()
        result = await _runtime._exec_with_cancel(
            object(),
            sandbox,
            MicrosandboxRunSpec(
                name="weft-cancel-output",
                image="python:3.12",
                command=("python", "-c", "print(1)"),
                env={},
                cwd="/",
                network="none",
                workspace=WorkspaceSpec(),
            ),
            cancel_requested=lambda: True,
        )
        return result, sandbox.killed

    result, killed = asyncio.run(_run())

    assert result is None
    assert killed is True


def test_cleanup_sandbox_reports_each_failure_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    class Sandbox:
        async def stop(self, *, timeout: float) -> None:
            assert timeout == 2.0
            calls.append("stop")
            raise RuntimeError("sensitive stop failure")

    class SandboxAPI:
        @staticmethod
        async def remove(name: str) -> None:
            assert name == "sensitive-sandbox-name"
            calls.append("remove")
            raise RuntimeError("sensitive remove failure")

    class SDK:
        Sandbox = SandboxAPI

    caplog.set_level(logging.WARNING, logger="weft_microsandbox._runtime")

    asyncio.run(_runtime._cleanup_sandbox(SDK(), Sandbox(), "sensitive-sandbox-name"))

    assert calls == ["stop", "remove"]
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to stop Microsandbox during cleanup",
        "Failed to remove Microsandbox during cleanup",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text
