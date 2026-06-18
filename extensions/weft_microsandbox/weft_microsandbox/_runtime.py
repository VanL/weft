"""Thin adapter around the Microsandbox Python SDK.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.1], [CC-3.2], [CC-3.4]
- docs/specifications/06-Resource_Management.md [RM-5]
"""

from __future__ import annotations

import asyncio
import inspect
import platform
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ._options import MicrosandboxMount, MicrosandboxNetwork, WorkspaceMode


class MicrosandboxRuntimeError(RuntimeError):
    """Raised when the Microsandbox runtime boundary fails."""


@dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    """Workspace copy or mount policy for a sandbox."""

    mode: WorkspaceMode = "none"
    source: str | None = None
    target: str | None = None


@dataclass(frozen=True, slots=True)
class FileCopyBack:
    """Guest file copied back to the same host-side parser boundary."""

    guest_path: str
    host_path: str


@dataclass(frozen=True, slots=True)
class FileCopyIntoGuest:
    """Host path copied into the guest before command execution."""

    host_path: str
    guest_path: str


@dataclass(frozen=True, slots=True)
class MicrosandboxRunSpec:
    """Normalized sandbox execution request."""

    name: str
    image: str
    command: tuple[str, ...]
    env: Mapping[str, str]
    cwd: str
    network: MicrosandboxNetwork
    workspace: WorkspaceSpec
    mounts: tuple[MicrosandboxMount, ...] = ()
    timeout_seconds: float | None = None
    stdin_text: str | None = None
    memory_mb: int | None = None
    cpus: float | None = None
    max_fds: int | None = None
    guest_dirs: tuple[str, ...] = ()
    copy_into_guest: tuple[FileCopyIntoGuest, ...] = ()
    copy_back: tuple[FileCopyBack, ...] = ()
    labels: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class MicrosandboxStarted:
    """Sandbox identity available after creation."""

    sandbox_id: str
    sandbox_name: str
    host_pid: int | None = None


@dataclass(frozen=True, slots=True)
class MicrosandboxRunResult:
    """Collected sandbox execution result."""

    sandbox_id: str
    sandbox_name: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration: float
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class MicrosandboxDescription:
    """Runtime description from Microsandbox."""

    sandbox_id: str
    state: str | None
    metadata: Mapping[str, Any]


class MicrosandboxRuntime:
    """Synchronous facade over Microsandbox's async SDK."""

    def run(
        self,
        spec: MicrosandboxRunSpec,
        *,
        on_started: Callable[[MicrosandboxStarted], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> MicrosandboxRunResult:
        return asyncio.run(
            self._run_async(
                spec,
                on_started=on_started,
                cancel_requested=cancel_requested,
            )
        )

    def stop(self, sandbox_id: str, *, timeout: float = 2.0) -> bool:
        return asyncio.run(self._stop_async(sandbox_id, timeout=timeout))

    def kill(self, sandbox_id: str, *, timeout: float = 2.0) -> bool:
        return asyncio.run(self._kill_async(sandbox_id, timeout=timeout))

    def describe(self, sandbox_id: str) -> MicrosandboxDescription | None:
        return asyncio.run(self._describe_async(sandbox_id))

    def check_importable(self) -> None:
        _load_sdk()

    def check_preflight(self) -> None:
        sdk = _load_sdk()
        _validate_platform()
        is_installed = getattr(sdk, "is_installed", None)
        if callable(is_installed) and not bool(is_installed()):
            raise MicrosandboxRuntimeError("Microsandbox runtime is not installed")

    async def _run_async(
        self,
        spec: MicrosandboxRunSpec,
        *,
        on_started: Callable[[MicrosandboxStarted], None] | None,
        cancel_requested: Callable[[], bool] | None,
    ) -> MicrosandboxRunResult:
        if not spec.command:
            raise MicrosandboxRuntimeError("Microsandbox command must be non-empty")
        sdk = _load_sdk()
        started = time.monotonic()
        sandbox = None
        try:
            sandbox = await sdk.Sandbox.create(
                spec.name,
                image=spec.image,
                network=_network_config(sdk, spec.network),
                volumes=_volume_config(sdk, spec),
                replace=True,
                labels=dict(spec.labels or {}),
                **_resource_create_kwargs(spec),
            )
            sandbox_name = await _sandbox_name(sandbox, fallback=spec.name)
            if on_started is not None:
                on_started(
                    MicrosandboxStarted(
                        sandbox_id=sandbox_name,
                        sandbox_name=sandbox_name,
                    )
                )
            await _prepare_guest_filesystem(sandbox, spec)
            await _copy_into_guest(sandbox, spec.copy_into_guest)
            output = await _exec_with_cancel(
                sdk,
                sandbox,
                spec,
                cancel_requested=cancel_requested,
            )
            if output is None:
                return MicrosandboxRunResult(
                    sandbox_id=sandbox_name,
                    sandbox_name=sandbox_name,
                    exit_code=None,
                    stdout="",
                    stderr="Target execution cancelled",
                    timed_out=False,
                    duration=time.monotonic() - started,
                    cancelled=True,
                )
            await _copy_back_files(sandbox, spec.copy_back)
            return MicrosandboxRunResult(
                sandbox_id=sandbox_name,
                sandbox_name=sandbox_name,
                exit_code=int(output.exit_code),
                stdout=str(output.stdout_text),
                stderr=str(output.stderr_text),
                timed_out=False,
                duration=time.monotonic() - started,
            )
        except Exception as exc:
            if _is_timeout_error(exc):
                sandbox_name = await _sandbox_name(sandbox, fallback=spec.name)
                return MicrosandboxRunResult(
                    sandbox_id=sandbox_name,
                    sandbox_name=sandbox_name,
                    exit_code=None,
                    stdout="",
                    stderr=str(exc),
                    timed_out=True,
                    duration=time.monotonic() - started,
                )
            raise MicrosandboxRuntimeError(str(exc)) from exc
        finally:
            if sandbox is not None:
                await _cleanup_sandbox(sdk, sandbox, spec.name)

    async def _stop_async(self, sandbox_id: str, *, timeout: float) -> bool:
        sdk = _load_sdk()
        try:
            handle = await sdk.Sandbox.get(sandbox_id)
            await handle.stop(timeout=timeout)
        except Exception:
            return False
        return True

    async def _kill_async(self, sandbox_id: str, *, timeout: float) -> bool:
        del timeout
        sdk = _load_sdk()
        try:
            handle = await sdk.Sandbox.get(sandbox_id)
            await handle.kill()
        except Exception:
            return False
        return True

    async def _describe_async(
        self,
        sandbox_id: str,
    ) -> MicrosandboxDescription | None:
        sdk = _load_sdk()
        try:
            handle = await sdk.Sandbox.get(sandbox_id)
            refreshed = await handle.refresh()
        except Exception:
            return MicrosandboxDescription(
                sandbox_id=sandbox_id,
                state="missing",
                metadata={},
            )
        metadata: dict[str, Any] = {
            "sandbox_name": getattr(refreshed, "name", sandbox_id),
            "created_at": getattr(refreshed, "created_at", None),
            "updated_at": getattr(refreshed, "updated_at", None),
        }
        config = getattr(refreshed, "config", None)
        if callable(config):
            try:
                metadata["config"] = config()
            except Exception:  # pragma: no cover - best-effort SDK metadata
                pass
        return MicrosandboxDescription(
            sandbox_id=sandbox_id,
            state=str(getattr(refreshed, "status", "")) or None,
            metadata=metadata,
        )


def _load_sdk() -> Any:
    try:
        import microsandbox
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise MicrosandboxRuntimeError(
            "Microsandbox SDK is not installed. Install weft-microsandbox."
        ) from exc
    return microsandbox


def _validate_platform() -> None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return
    if system == "linux" and machine in {"x86_64", "amd64", "aarch64", "arm64"}:
        return
    raise MicrosandboxRuntimeError(
        "Microsandbox supports Linux x86_64/aarch64 or macOS Apple Silicon"
    )


def _network_config(sdk: Any, network: MicrosandboxNetwork) -> Any:
    if network == "none":
        return sdk.Network.none()
    return sdk.Network.allow_all()


def _volume_config(sdk: Any, spec: MicrosandboxRunSpec) -> dict[str, Any]:
    volumes: dict[str, Any] = {}
    for mount in spec.mounts:
        volumes[mount.target] = sdk.Volume.bind(
            mount.source,
            readonly=mount.read_only,
        )
    if spec.workspace.mode in {"mount-read-only", "mount-read-write"}:
        if spec.workspace.source is None or spec.workspace.target is None:
            raise MicrosandboxRuntimeError("workspace mount requires source and target")
        volumes[spec.workspace.target] = sdk.Volume.bind(
            spec.workspace.source,
            readonly=spec.workspace.mode == "mount-read-only",
        )
    return volumes


async def _prepare_guest_filesystem(
    sandbox: Any,
    spec: MicrosandboxRunSpec,
) -> None:
    for path in spec.guest_dirs:
        await _mkdir_guest(sandbox, path)
    if spec.workspace.mode == "copy":
        if spec.workspace.source is None or spec.workspace.target is None:
            raise MicrosandboxRuntimeError("workspace copy requires source and target")
        await sandbox.fs.copy_from_host(spec.workspace.source, spec.workspace.target)


async def _copy_back_files(
    sandbox: Any,
    copy_back: Sequence[FileCopyBack],
) -> None:
    for item in copy_back:
        try:
            await sandbox.fs.copy_to_host(item.guest_path, item.host_path)
        except Exception:
            pass


async def _copy_into_guest(
    sandbox: Any,
    copy_into_guest: Sequence[FileCopyIntoGuest],
) -> None:
    for item in copy_into_guest:
        host_path = Path(item.host_path)
        if not host_path.is_dir():
            await _mkdir_guest(sandbox, str(PurePosixPath(item.guest_path).parent))
            await sandbox.fs.copy_from_host(item.host_path, item.guest_path)
            continue
        await _mkdir_guest(sandbox, item.guest_path)
        for child in host_path.rglob("*"):
            guest_path = str(
                PurePosixPath(item.guest_path)
                / PurePosixPath(*child.relative_to(host_path).parts)
            )
            if child.is_dir():
                await _mkdir_guest(sandbox, guest_path)
            else:
                await _mkdir_guest(sandbox, str(PurePosixPath(guest_path).parent))
                await sandbox.fs.copy_from_host(str(child), guest_path)


async def _mkdir_guest(sandbox: Any, path: str) -> None:
    if path in {"", "."}:
        return
    try:
        await sandbox.fs.mkdir(path)
    except Exception:
        pass


def _resource_create_kwargs(spec: MicrosandboxRunSpec) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if spec.memory_mb is not None:
        kwargs["memory"] = spec.memory_mb
    if spec.cpus is not None:
        kwargs["cpus"] = spec.cpus
    return kwargs


def _rlimits(sdk: Any, spec: MicrosandboxRunSpec) -> list[Any] | None:
    rlimits: list[Any] = []
    if spec.max_fds is not None:
        rlimits.append(sdk.Rlimit.nofile(spec.max_fds))
    return rlimits or None


async def _exec_with_cancel(
    sdk: Any,
    sandbox: Any,
    spec: MicrosandboxRunSpec,
    *,
    cancel_requested: Callable[[], bool] | None,
) -> Any | None:
    exec_task = asyncio.create_task(
        sandbox.exec(
            spec.command[0],
            list(spec.command[1:]),
            cwd=spec.cwd,
            env=dict(spec.env),
            timeout=spec.timeout_seconds,
            stdin=spec.stdin_text,
            rlimits=_rlimits(sdk, spec),
        )
    )
    while True:
        done, _pending = await asyncio.wait({exec_task}, timeout=0.05)
        if done:
            try:
                return exec_task.result()
            except asyncio.CancelledError:
                if cancel_requested is not None and cancel_requested():
                    return None
                raise
        if cancel_requested is None or not cancel_requested():
            continue
        try:
            await sandbox.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(exec_task, timeout=2.0)
            return None
        except asyncio.CancelledError:
            return None
        except Exception:
            exec_task.cancel()
            return None


async def _sandbox_name(sandbox: Any | None, *, fallback: str) -> str:
    if sandbox is None:
        return fallback
    try:
        value = getattr(sandbox, "name", None)
        if callable(value):
            value = value()
        if inspect.isawaitable(value):
            value = await value
    except Exception:
        return fallback
    text = str(value).strip()
    return text or fallback


async def _cleanup_sandbox(sdk: Any, sandbox: Any, name: str) -> None:
    try:
        await sandbox.stop(timeout=2.0)
    except Exception:
        pass
    try:
        await sdk.Sandbox.remove(name)
    except Exception:
        pass


def _is_timeout_error(exc: BaseException) -> bool:
    try:
        timeout_error = _load_sdk().ExecTimeoutError
    except Exception:  # pragma: no cover - import already checked on real path
        return exc.__class__.__name__ == "ExecTimeoutError"
    return isinstance(exc, timeout_error)


__all__ = [
    "FileCopyBack",
    "FileCopyIntoGuest",
    "MicrosandboxDescription",
    "MicrosandboxRunResult",
    "MicrosandboxRunSpec",
    "MicrosandboxRuntime",
    "MicrosandboxRuntimeError",
    "MicrosandboxStarted",
    "WorkspaceSpec",
]
