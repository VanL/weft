"""Docker-backed one-shot provider_cli agent runner.

Spec references:
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import weft._constants as weft_constants
from simplebroker import BrokerTarget
from weft.core.agents.provider_cli.container_runtime import (
    ProviderContainerRuntimeResolution,
    ResolvedProviderContainerMount,
    resolve_provider_container_runtime,
)
from weft.core.agents.provider_cli.execution import (
    build_provider_cli_execution_result,
    prepare_provider_cli_execution,
    resolve_provider_cli,
    runtime_config_str,
)
from weft.core.agents.provider_cli.runtime_prep import (
    PreparedProviderContainerRuntime,
    prepare_provider_container_runtime,
)
from weft.core.agents.runtime import normalize_agent_work_item
from weft.core.runners import RunnerOutcome
from weft.core.tasks.runner import AgentSession, CommandSession
from weft.core.taskspec import AgentSection
from weft.ext import RunnerHandle

from ._sdk import docker_client, load_docker_sdk, wait_for_container_runtime_start
from .agent_images import ensure_agent_image


class DockerProviderCLIRunner:
    """One-shot delegated provider_cli runner backed by a Docker container."""

    def __init__(
        self,
        *,
        tid: str | None,
        agent: Mapping[str, Any] | None,
        env: Mapping[str, str] | None,
        working_dir: str | None,
        timeout: float | None,
        limits: Any | None,
        monitor_interval: float | None,
        runner_options: Mapping[str, Any] | None,
        bundle_root: str | None = None,
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        del db_path, config
        if agent is None:
            raise ValueError("Docker provider_cli runner requires spec.agent")
        self._tid = tid
        self._agent_payload = dict(agent)
        self._env = {str(key): str(value) for key, value in dict(env or {}).items()}
        self._working_dir = working_dir
        self._timeout = timeout
        self._limits = limits
        self._monitor_interval = monitor_interval or 0.1
        self._bundle_root = bundle_root
        options = dict(runner_options or {})
        self._container_workdir = (
            str(options["container_workdir"])
            if options.get("container_workdir") is not None
            else None
        )
        self._mount_workdir = bool(options.get("mount_workdir", True))
        self._network = _normalize_optional_text(
            options.get("network"),
            name="spec.runner.options.network",
        )
        self._mounts = _normalize_mounts(
            options.get("mounts"),
            name="spec.runner.options.mounts",
        )
        self._work_item_mounts = _normalize_work_item_mounts(
            options.get("work_item_mounts"),
            name="spec.runner.options.work_item_mounts",
        )
        _validate_mount_target_conflicts(
            self._mounts,
            self._work_item_mounts,
        )

    def run(self, work_item: Any) -> RunnerOutcome:
        return self.run_with_hooks(work_item)

    def run_with_hooks(
        self,
        work_item: Any,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_worker_started: Callable[[int | None], None] | None = None,
        on_runtime_handle_started: Callable[[RunnerHandle], None] | None = None,
        on_stdout_chunk: Callable[[str, bool], None] | None = None,
        on_stderr_chunk: Callable[[str, bool], None] | None = None,
    ) -> RunnerOutcome:
        start = time.monotonic()
        agent = AgentSection.model_validate(self._agent_payload)
        resolved_work_item_mounts = _resolve_work_item_mounts(
            self._work_item_mounts,
            work_item,
            name="spec.runner.options.work_item_mounts",
        )
        normalized_work_item = normalize_agent_work_item(agent, work_item)
        provider = resolve_provider_cli(agent)
        runtime_requirements = self._resolve_container_runtime(
            provider.name,
            work_item_mounts=resolved_work_item_mounts,
        )
        image_result = ensure_agent_image(provider.name)
        host_workdir, container_workdir = self._working_dir_mapping()
        container_name = _container_name(self._tid)
        docker = load_docker_sdk()
        with tempfile.TemporaryDirectory(prefix="weft-docker-provider-cli-") as tempdir:
            runtime_tempdir = Path(tempdir).resolve()
            prepared_runtime = prepare_provider_container_runtime(
                provider.name,
                runtime_requirements,
                temp_root=runtime_tempdir,
            )
            mounts = self._build_mounts(
                docker,
                host_workdir=host_workdir,
                container_workdir=container_workdir,
                runtime_mounts=(
                    *runtime_requirements.mounts,
                    *prepared_runtime.mounts,
                ),
                work_item_mounts=resolved_work_item_mounts,
            )
            mounts.append(
                docker.types.Mount(
                    target=str(runtime_tempdir),
                    source=str(runtime_tempdir),
                    type="bind",
                    read_only=False,
                )
            )
            executable = self._container_executable(
                agent,
                default_executable=image_result.recipe.default_executable,
                runtime_mounts=runtime_requirements.mounts,
            )
            prepared = prepare_provider_cli_execution(
                agent=agent,
                work_item=normalized_work_item,
                tid=self._tid,
                executable=executable,
                cwd=container_workdir,
                tempdir=runtime_tempdir,
                bundle_root=self._bundle_root,
            )
            if prepared.invocation.stdin_text is not None:
                raise ValueError(
                    "Docker-backed provider_cli execution does not support stdin-based invocations"
                )

            create_kwargs = {
                "name": container_name,
                "detach": True,
                "stdin_open": False,
                "tty": False,
                "working_dir": prepared.invocation.cwd,
                "environment": self._container_env(
                    runtime_requirements,
                    prepared_runtime,
                    prepared.invocation.env,
                ),
                "mounts": mounts,
                "labels": {
                    "weft.runner": "docker",
                    "weft.task.provider": provider.name,
                    "weft.agent.image.cache_key": image_result.cache_key,
                },
            }
            network_mode = self._network_mode()
            if network_mode is not None:
                create_kwargs["network_mode"] = network_mode
            mem_limit = _limit_int(self._limits, "memory_mb")
            if mem_limit is not None:
                create_kwargs["mem_limit"] = f"{mem_limit}m"
            nano_cpus = _docker_nano_cpus(self._limits)
            if nano_cpus is not None:
                create_kwargs["nano_cpus"] = nano_cpus
            max_fds = _limit_int(self._limits, "max_fds")
            if max_fds is not None:
                create_kwargs["ulimits"] = [
                    docker.types.Ulimit(name="nofile", soft=max_fds, hard=max_fds)
                ]

            container = None
            stdout_text = ""
            stderr_text = ""
            runtime_handle: RunnerHandle | None = None
            try:
                with docker_client() as client:
                    container = client.containers.create(
                        image_result.image,
                        list(prepared.invocation.command),
                        **create_kwargs,
                    )
                    container.start()
                    wait_for_container_runtime_start(
                        container,
                        timeout=weft_constants.DOCKER_CONTAINER_LOOKUP_TIMEOUT,
                        interval=weft_constants.DOCKER_CONTAINER_LOOKUP_INTERVAL,
                    )
                    runtime_handle = RunnerHandle(
                        runner="docker",
                        kind="container",
                        id=container.name,
                        control={"authority": "runner"},
                        observations={
                            "container_id": container.id,
                            "container_name": container.name,
                        },
                        metadata={
                            "image": image_result.image,
                            "provider": provider.name,
                            "agent_image_cache_key": image_result.cache_key,
                        },
                    )
                    if on_worker_started is not None:
                        try:
                            on_worker_started(None)
                        except Exception:  # pragma: no cover - callback safety
                            pass
                    if on_runtime_handle_started is not None:
                        try:
                            on_runtime_handle_started(runtime_handle)
                        except Exception:  # pragma: no cover - callback safety
                            pass

                    terminal_status: str | None = None
                    terminal_error: str | None = None
                    while True:
                        container.reload()
                        state = container.attrs.get("State") or {}
                        status = state.get("Status")
                        if status in {"created", "running", "restarting"}:
                            if cancel_requested is not None and cancel_requested():
                                container.kill()
                                terminal_status = "killed"
                                terminal_error = "Target execution was killed"
                                break
                            if (
                                self._timeout is not None
                                and time.monotonic() - start > self._timeout
                            ):
                                container.stop(timeout=2)
                                terminal_status = "timeout"
                                terminal_error = "Target execution timed out"
                                break
                            time.sleep(self._monitor_interval)
                            continue
                        break

                    container.reload()
                    state = container.attrs.get("State") or {}
                    exit_code = state.get("ExitCode")
                    oom_killed = bool(state.get("OOMKilled"))
                    stdout_text = _decode_logs(
                        container.logs(stdout=True, stderr=False)
                    )
                    stderr_text = _decode_logs(
                        container.logs(stdout=False, stderr=True)
                    )
                    if stdout_text and on_stdout_chunk is not None:
                        try:
                            on_stdout_chunk(stdout_text, True)
                        except Exception:  # pragma: no cover - callback safety
                            pass
                    if stderr_text and on_stderr_chunk is not None:
                        try:
                            on_stderr_chunk(stderr_text, True)
                        except Exception:  # pragma: no cover - callback safety
                            pass

                    if terminal_status is not None:
                        outcome = RunnerOutcome(
                            status=terminal_status,
                            value=None,
                            error=terminal_error,
                            stdout=stdout_text or None,
                            stderr=stderr_text or None,
                            returncode=_coerce_int(exit_code),
                            duration=time.monotonic() - start,
                            runtime_handle=runtime_handle,
                        )
                    else:
                        completed = subprocess.CompletedProcess(
                            args=list(prepared.invocation.command),
                            returncode=_coerce_int(exit_code) or 0,
                            stdout=stdout_text,
                            stderr=stderr_text,
                        )
                        try:
                            provider_result = prepared.provider.parse_result(
                                completed=completed,
                                invocation=prepared.invocation,
                            )
                            value = build_provider_cli_execution_result(
                                agent=agent,
                                prepared=prepared,
                                work_item=normalized_work_item,
                                provider_result=provider_result,
                            )
                            outcome = RunnerOutcome(
                                status="ok",
                                value=value,
                                error=None,
                                stdout=stdout_text or None,
                                stderr=stderr_text or None,
                                returncode=completed.returncode,
                                duration=time.monotonic() - start,
                                runtime_handle=runtime_handle,
                            )
                        except Exception as exc:
                            outcome = RunnerOutcome(
                                status="error",
                                value=None,
                                error=str(exc),
                                stdout=stdout_text or None,
                                stderr=stderr_text or None,
                                returncode=completed.returncode,
                                duration=time.monotonic() - start,
                                runtime_handle=runtime_handle,
                            )

                    if oom_killed:
                        memory_limit = _limit_int(self._limits, "memory_mb")
                        outcome.status = "limit"
                        if memory_limit is None:
                            outcome.error = (
                                "Container exceeded its configured memory limit"
                            )
                        else:
                            outcome.error = (
                                f"Container exceeded memory limit of {memory_limit}MB"
                            )
                    return outcome
            finally:
                if container is not None:
                    try:
                        container.remove(force=True)
                    except Exception:  # pragma: no cover - best effort cleanup
                        pass

    def start_session(self) -> CommandSession:
        raise ValueError("Docker runner does not support interactive sessions")

    def start_agent_session(self) -> AgentSession:
        raise ValueError("Docker runner does not support agent sessions")

    def _working_dir_mapping(self) -> tuple[str | None, str]:
        if self._working_dir:
            host_workdir = str(Path(self._working_dir).expanduser().resolve())
            container_workdir = self._container_workdir or host_workdir
            return host_workdir, container_workdir
        return None, self._container_workdir or "/"

    def _build_mounts(
        self,
        docker: Any,
        *,
        host_workdir: str | None,
        container_workdir: str,
        runtime_mounts: Sequence[ResolvedProviderContainerMount],
        work_item_mounts: Sequence[Mapping[str, Any]],
    ) -> list[Any]:
        mounts: list[Any] = []
        if self._mount_workdir and host_workdir is not None:
            mounts.append(
                docker.types.Mount(
                    target=container_workdir,
                    source=host_workdir,
                    type="bind",
                    read_only=False,
                )
            )
        for runtime_mount in runtime_mounts:
            mounts.append(
                docker.types.Mount(
                    target=runtime_mount.target,
                    source=str(runtime_mount.source),
                    type="bind",
                    read_only=runtime_mount.read_only,
                )
            )
        for work_item_mount in work_item_mounts:
            mounts.append(
                docker.types.Mount(
                    target=str(work_item_mount["target"]),
                    source=str(work_item_mount["source"]),
                    type="bind",
                    read_only=bool(work_item_mount["read_only"]),
                )
            )
        for explicit_mount in self._mounts:
            mounts.append(
                docker.types.Mount(
                    target=str(explicit_mount["target"]),
                    source=str(explicit_mount["source"]),
                    type="bind",
                    read_only=bool(explicit_mount["read_only"]),
                )
            )
        return mounts

    def _container_executable(
        self,
        agent: AgentSection,
        *,
        default_executable: str | None,
        runtime_mounts: Sequence[ResolvedProviderContainerMount],
    ) -> str:
        configured = runtime_config_str(agent, "executable")
        if configured is None:
            if default_executable is None:
                raise ValueError(
                    "Docker-backed provider_cli execution requires either a recipe "
                    "default executable or spec.agent.runtime_config.executable"
                )
            return default_executable
        return self._translate_host_path(configured, runtime_mounts=runtime_mounts)

    def _translate_host_path(
        self,
        path: str,
        *,
        runtime_mounts: Sequence[ResolvedProviderContainerMount],
    ) -> str:
        source_path = Path(path).expanduser()
        if not source_path.is_absolute():
            return path
        resolved = source_path.resolve()
        for host_source, target in self._path_mappings(runtime_mounts=runtime_mounts):
            if resolved == host_source:
                return target
            if host_source.is_dir():
                try:
                    relative = resolved.relative_to(host_source)
                except ValueError:
                    continue
                return str(Path(target) / relative)
        return str(resolved)

    def _path_mappings(
        self,
        *,
        runtime_mounts: Sequence[ResolvedProviderContainerMount],
    ) -> list[tuple[Path, str]]:
        mappings: list[tuple[Path, str]] = []
        if self._mount_workdir and self._working_dir:
            host_workdir = Path(self._working_dir).expanduser().resolve()
            mappings.append(
                (host_workdir, self._container_workdir or str(host_workdir))
            )
        for runtime_mount in runtime_mounts:
            mappings.append((runtime_mount.source.resolve(), runtime_mount.target))
        for explicit_mount in self._mounts:
            mappings.append(
                (
                    Path(str(explicit_mount["source"])).expanduser().resolve(),
                    str(explicit_mount["target"]),
                )
            )
        mappings.sort(key=lambda item: len(str(item[0])), reverse=True)
        return mappings

    def _container_env(
        self,
        runtime_requirements: ProviderContainerRuntimeResolution,
        prepared_runtime: PreparedProviderContainerRuntime,
        invocation_env: Mapping[str, str] | None,
    ) -> dict[str, str]:
        env = dict(runtime_requirements.env)
        env.update(prepared_runtime.env)
        env.update(self._env)
        if invocation_env:
            env.update({str(key): str(value) for key, value in invocation_env.items()})
        return env

    def _network_mode(self) -> str | None:
        if self._network is not None:
            return self._network
        if _limit_value(self._limits, "max_connections") == 0:
            return "none"
        return None

    def _resolve_container_runtime(
        self,
        provider_name: str,
        *,
        work_item_mounts: Sequence[Mapping[str, Any]],
    ) -> ProviderContainerRuntimeResolution:
        return resolve_provider_container_runtime(
            provider_name,
            task_env=self._env,
            working_dir=self._working_dir,
            explicit_mounts=[*self._mounts, *work_item_mounts],
        )


def _container_name(tid: str | None) -> str:
    suffix = (
        tid[-8:] if isinstance(tid, str) and len(tid) >= 8 else uuid.uuid4().hex[:8]
    )
    return f"weft-agent-{suffix}-{uuid.uuid4().hex[:8]}"


def _decode_logs(payload: bytes | str | None) -> str:
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return payload


def _docker_nano_cpus(limits: Any | None) -> int | None:
    cpu_percent = _limit_int(limits, "cpu_percent")
    if cpu_percent is None:
        return None
    host_cpus = max(os.cpu_count() or 1, 1)
    cpus = max((cpu_percent / 100.0) * host_cpus, 0.01)
    return max(int(cpus * 1_000_000_000), 1)


def _coerce_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _limit_int(limits: Any | None, name: str) -> int | None:
    value = _limit_value(limits, name)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _limit_value(limits: Any | None, name: str) -> Any | None:
    if limits is None:
        return None
    if isinstance(limits, Mapping):
        return limits.get(name)
    return getattr(limits, name, None)


def _normalize_mounts(value: object, *, name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a list of mount objects")
    mounts: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{name}[{index}] must be an object")
        source = _normalize_required_text(
            item.get("source"),
            name=f"{name}[{index}].source",
        )
        target = _normalize_required_text(
            item.get("target"),
            name=f"{name}[{index}].target",
        )
        read_only = bool(item.get("read_only", True))
        mounts.append(
            {
                "source": str(Path(source).expanduser().resolve()),
                "target": target,
                "read_only": read_only,
            }
        )
    return mounts


def _normalize_work_item_mounts(value: object, *, name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a list of mount objects")

    mounts: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{name}[{index}] must be an object")

        extra_keys = sorted(
            set(item) - {"source_path_ref", "target", "read_only", "required", "kind"}
        )
        if extra_keys:
            raise ValueError(
                f"{name}[{index}] has unsupported field(s): {', '.join(extra_keys)}"
            )

        source_path_ref = item.get("source_path_ref")
        if not isinstance(source_path_ref, str) or not source_path_ref.strip():
            raise ValueError(
                f"{name}[{index}].source_path_ref must be a non-empty string"
            )
        target = item.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ValueError(f"{name}[{index}].target must be a non-empty string")

        read_only = item.get("read_only", True)
        if not isinstance(read_only, bool):
            raise ValueError(f"{name}[{index}].read_only must be a boolean")
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise ValueError(f"{name}[{index}].required must be a boolean")

        kind = item.get("kind", "any")
        if kind not in {"any", "file", "dir"}:
            raise ValueError(
                f"{name}[{index}].kind must be one of 'any', 'file', or 'dir'"
            )

        mounts.append(
            {
                "source_path_ref": source_path_ref.strip(),
                "target": target.strip(),
                "read_only": read_only,
                "required": required,
                "kind": kind,
            }
        )
    return mounts


def _validate_mount_target_conflicts(
    explicit_mounts: Sequence[Mapping[str, Any]],
    work_item_mounts: Sequence[Mapping[str, Any]],
) -> None:
    explicit_targets = {
        str(mount.get("target")).strip()
        for mount in explicit_mounts
        if isinstance(mount.get("target"), str) and str(mount.get("target")).strip()
    }
    work_item_targets = {
        str(mount.get("target")).strip()
        for mount in work_item_mounts
        if isinstance(mount.get("target"), str) and str(mount.get("target")).strip()
    }
    conflicts = sorted(explicit_targets & work_item_targets)
    if conflicts:
        raise ValueError(
            "spec.runner.options.mounts and spec.runner.options.work_item_mounts "
            "cannot target the same container path(s): " + ", ".join(conflicts)
        )


def _resolve_work_item_mounts(
    mount_specs: Sequence[Mapping[str, Any]],
    work_item: Any,
    *,
    name: str,
) -> list[dict[str, Any]]:
    if not mount_specs:
        return []

    work_item_mapping = work_item if isinstance(work_item, Mapping) else None
    resolved: list[dict[str, Any]] = []
    for index, mount_spec in enumerate(mount_specs):
        source_path_ref = str(mount_spec["source_path_ref"])
        value = (
            _resolve_work_item_value(work_item_mapping, source_path_ref)
            if work_item_mapping is not None
            else _WORK_ITEM_MISSING
        )
        if value is _WORK_ITEM_MISSING:
            if bool(mount_spec["required"]):
                raise ValueError(
                    f"{name}[{index}] requires work item field {source_path_ref!r}"
                )
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"{name}[{index}] work item field {source_path_ref!r} must resolve "
                "to a non-empty string path"
            )

        source = Path(value).expanduser()
        if not source.is_absolute():
            raise ValueError(
                f"{name}[{index}] work item field {source_path_ref!r} must resolve "
                "to an absolute path"
            )
        resolved_source = source.resolve()
        kind = str(mount_spec["kind"])
        if kind == "file":
            if not resolved_source.is_file():
                raise ValueError(
                    f"{name}[{index}] expected file path for {source_path_ref!r}: "
                    f"{resolved_source}"
                )
        elif kind == "dir":
            if not resolved_source.is_dir():
                raise ValueError(
                    f"{name}[{index}] expected directory path for "
                    f"{source_path_ref!r}: {resolved_source}"
                )
        elif not resolved_source.exists():
            raise ValueError(
                f"{name}[{index}] path from {source_path_ref!r} does not exist: "
                f"{resolved_source}"
            )

        resolved.append(
            {
                "source": str(resolved_source),
                "target": str(mount_spec["target"]),
                "read_only": bool(mount_spec["read_only"]),
            }
        )
    return resolved


_WORK_ITEM_MISSING = object()


def _resolve_work_item_value(
    work_item: Mapping[str, Any] | None,
    dotted_path: str,
) -> object:
    if work_item is None:
        return _WORK_ITEM_MISSING
    current: object = work_item
    for segment in dotted_path.split("."):
        if not segment:
            return _WORK_ITEM_MISSING
        if not isinstance(current, Mapping):
            return _WORK_ITEM_MISSING
        current = current.get(segment, _WORK_ITEM_MISSING)
        if current is _WORK_ITEM_MISSING:
            return _WORK_ITEM_MISSING
    return current


def _normalize_optional_text(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must be a non-empty string when provided")
    return cleaned


def _normalize_required_text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must be a non-empty string")
    return cleaned
