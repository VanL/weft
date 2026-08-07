"""Microsandbox SDK adapter contract tests."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable
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


def test_timeout_classifier_uses_name_fallback_when_sdk_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExecTimeoutError(Exception):
        pass

    monkeypatch.setattr(
        _runtime,
        "_load_sdk",
        lambda: (_ for _ in ()).throw(
            _runtime.MicrosandboxRuntimeError("SDK unavailable")
        ),
    )

    assert _runtime._is_timeout_error(ExecTimeoutError()) is True


def test_timeout_classifier_uses_name_fallback_when_sdk_type_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExecTimeoutError(Exception):
        pass

    monkeypatch.setattr(_runtime, "_load_sdk", lambda: object())

    assert _runtime._is_timeout_error(ExecTimeoutError()) is True


def test_timeout_classifier_propagates_unexpected_sdk_lookup_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _runtime,
        "_load_sdk",
        lambda: (_ for _ in ()).throw(RuntimeError("SDK lookup defect")),
    )

    with pytest.raises(RuntimeError, match="SDK lookup defect"):
        _runtime._is_timeout_error(TimeoutError())


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


def test_sandbox_name_reports_lookup_failure_and_uses_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingNameSandbox:
        @staticmethod
        def name() -> str:
            raise RuntimeError("sensitive sandbox name failure")

    caplog.set_level(logging.WARNING, logger="weft_microsandbox._runtime")

    name = asyncio.run(
        _runtime._sandbox_name(FailingNameSandbox(), fallback="sensitive-fallback")
    )

    assert name == "sensitive-fallback"
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to read Microsandbox name; using fallback"
    ]
    assert caplog.records[0].exc_info is None
    assert "sensitive" not in caplog.text


def test_stop_reports_adapter_failure_and_returns_false(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Handle:
        @staticmethod
        async def stop(*, timeout: float) -> None:
            assert timeout == 1.25
            raise RuntimeError("sensitive stop failure")

    class SandboxAPI:
        @staticmethod
        async def get(sandbox_id: str) -> Handle:
            assert sandbox_id == "sensitive-sandbox-id"
            return Handle()

    class SDK:
        Sandbox = SandboxAPI

    monkeypatch.setattr(_runtime, "_load_sdk", lambda: SDK())
    caplog.set_level(logging.WARNING, logger="weft_microsandbox._runtime")

    stopped = asyncio.run(
        MicrosandboxRuntime()._stop_async("sensitive-sandbox-id", timeout=1.25)
    )

    assert stopped is False
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to stop Microsandbox"
    ]
    assert caplog.records[0].exc_info is None
    assert "sensitive" not in caplog.text


def test_kill_reports_adapter_lookup_failure_and_returns_false(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class SandboxAPI:
        @staticmethod
        async def get(sandbox_id: str) -> object:
            assert sandbox_id == "sensitive-sandbox-id"
            raise ValueError("sensitive kill lookup failure")

    class SDK:
        Sandbox = SandboxAPI

    monkeypatch.setattr(_runtime, "_load_sdk", lambda: SDK())
    caplog.set_level(logging.WARNING, logger="weft_microsandbox._runtime")

    killed = asyncio.run(
        MicrosandboxRuntime()._kill_async("sensitive-sandbox-id", timeout=1.25)
    )

    assert killed is False
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to kill Microsandbox"
    ]
    assert caplog.records[0].exc_info is None
    assert "sensitive" not in caplog.text


def test_describe_reports_adapter_failure_and_returns_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Handle:
        @staticmethod
        async def refresh() -> object:
            raise OSError("sensitive FFI refresh failure")

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
    assert description.sandbox_id == "sensitive-sandbox-id"
    assert description.state == "missing"
    assert description.metadata == {}
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to describe Microsandbox; treating it as missing"
    ]
    assert caplog.records[0].exc_info is None
    assert "sensitive" not in caplog.text


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


def test_copy_into_guest_continues_after_guest_mkdir_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("payload", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    class Fs:
        async def mkdir(self, path: str) -> None:
            calls.append(("mkdir", path))
            raise _runtime._load_sdk().FilesystemError("sensitive mkdir failure")

        async def copy_from_host(self, host_path: str, guest_path: str) -> None:
            calls.append(("copy", host_path, guest_path))

    class Sandbox:
        fs = Fs()

    caplog.set_level(logging.WARNING, logger="weft_microsandbox._runtime")

    asyncio.run(
        _runtime._copy_into_guest(
            Sandbox(),
            (FileCopyIntoGuest(str(source), "/existing/input.txt"),),
        )
    )

    assert calls == [
        ("mkdir", "/existing"),
        ("copy", str(source), "/existing/input.txt"),
    ]
    assert [record.getMessage() for record in caplog.records] == [
        "Microsandbox guest directory creation failed"
    ]
    assert caplog.records[0].exc_info is None
    assert "sensitive" not in caplog.text


def test_copy_into_guest_does_not_hide_unexpected_mkdir_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("payload", encoding="utf-8")

    class Fs:
        async def mkdir(self, path: str) -> None:
            del path
            raise RuntimeError("unexpected adapter failure")

        async def copy_from_host(self, host_path: str, guest_path: str) -> None:
            pytest.fail(f"unexpected copy: {host_path} -> {guest_path}")

    class Sandbox:
        fs = Fs()

    with pytest.raises(RuntimeError, match="^unexpected adapter failure$"):
        asyncio.run(
            _runtime._copy_into_guest(
                Sandbox(),
                (FileCopyIntoGuest(str(source), "/existing/input.txt"),),
            )
        )


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


def test_exec_with_cancel_reports_kill_failure_and_cancels_exec_task(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class KillFailureSandbox:
        kill_attempted = False
        exec_task: asyncio.Task[object] | None = None

        async def exec(self, *_args: object, **_kwargs: object) -> object:
            self.exec_task = asyncio.current_task()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def kill(self) -> None:
            self.kill_attempted = True
            raise RuntimeError("sensitive kill failure")

    async def _run() -> tuple[object | None, KillFailureSandbox]:
        sandbox = KillFailureSandbox()
        result = await _runtime._exec_with_cancel(
            object(),
            sandbox,
            MicrosandboxRunSpec(
                name="weft-cancel-kill-failure",
                image="python:3.12",
                command=("python", "-c", "print(1)"),
                env={},
                cwd="/",
                network="none",
                workspace=WorkspaceSpec(),
            ),
            cancel_requested=lambda: True,
        )
        return result, sandbox

    wait_for = asyncio.wait_for

    async def fast_wait_for(awaitable: Awaitable[Any], timeout: float) -> Any:
        assert timeout == 2.0
        return await wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(_runtime.asyncio, "wait_for", fast_wait_for)
    caplog.set_level(logging.WARNING, logger="weft_microsandbox._runtime")

    result, sandbox = asyncio.run(_run())

    assert result is None
    assert sandbox.kill_attempted is True
    assert sandbox.exec_task is not None
    assert sandbox.exec_task.done() is True
    assert sandbox.exec_task.cancelled() is True
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to kill Microsandbox during cancellation",
        "Microsandbox execution did not complete cleanly after cancellation",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_exec_with_cancel_reports_post_kill_provider_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ProviderFailureAfterKillSandbox:
        killed = False

        async def exec(self, *_args: object, **_kwargs: object) -> object:
            while not self.killed:
                await asyncio.sleep(0.01)
            raise RuntimeError("sensitive provider failure")

        async def kill(self) -> None:
            self.killed = True

    async def _run() -> object | None:
        return await _runtime._exec_with_cancel(
            object(),
            ProviderFailureAfterKillSandbox(),
            MicrosandboxRunSpec(
                name="weft-cancel-provider-failure",
                image="python:3.12",
                command=("python", "-c", "print(1)"),
                env={},
                cwd="/",
                network="none",
                workspace=WorkspaceSpec(),
            ),
            cancel_requested=lambda: True,
        )

    caplog.set_level(logging.WARNING, logger="weft_microsandbox._runtime")

    assert asyncio.run(_run()) is None
    assert [record.getMessage() for record in caplog.records] == [
        "Microsandbox execution did not complete cleanly after cancellation"
    ]
    assert caplog.records[0].exc_info is None
    assert "sensitive" not in caplog.text


def test_exec_with_cancel_propagates_outer_cancellation() -> None:
    class BlockingAfterKillSandbox:
        killed = False
        exec_task: asyncio.Task[object] | None = None

        async def exec(self, *_args: object, **_kwargs: object) -> object:
            self.exec_task = asyncio.current_task()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def kill(self) -> None:
            self.killed = True

    async def _run() -> bool:
        sandbox = BlockingAfterKillSandbox()
        outer_task = asyncio.create_task(
            _runtime._exec_with_cancel(
                object(),
                sandbox,
                MicrosandboxRunSpec(
                    name="weft-outer-cancel",
                    image="python:3.12",
                    command=("python", "-c", "print(1)"),
                    env={},
                    cwd="/",
                    network="none",
                    workspace=WorkspaceSpec(),
                ),
                cancel_requested=lambda: True,
            )
        )
        while not sandbox.killed:
            await asyncio.sleep(0.01)
        outer_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer_task
        assert sandbox.exec_task is not None
        return sandbox.exec_task.cancelled()

    assert asyncio.run(_run()) is True


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
