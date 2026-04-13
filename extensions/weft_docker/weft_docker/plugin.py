"""Docker runner plugin for Weft.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.1], [CC-3.2], [CC-3.4]
- docs/specifications/02-TaskSpec.md [TS-1.3]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from simplebroker import BrokerTarget
from weft.core.resource_monitor import ResourceMetrics
from weft.core.runners import RunnerOutcome
from weft.core.runners.subprocess_runner import (
    prepare_command_invocation,
    run_monitored_subprocess,
)
from weft.core.tasks.runner import AgentSession, CommandSession
from weft.ext import (
    RunnerCapabilities,
    RunnerHandle,
    RunnerPlugin,
    RunnerRuntimeDescription,
)

_CONTAINER_LOOKUP_TIMEOUT = 2.0
_CONTAINER_LOOKUP_INTERVAL = 0.05


class DockerContainerMonitor:
    """Collect Docker-native metrics for a running container."""

    def __init__(
        self,
        *,
        runtime_id: str,
        limits: Any | None,
        image: str,
    ) -> None:
        self._runtime_id = runtime_id
        self._limits = limits
        self._image = image
        self._client: Any | None = None
        self._last_metrics: ResourceMetrics | None = None

    def start(self, pid: int) -> None:
        del pid
        self._client = _docker_client_from_env()

    def stop(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.close()

    def snapshot(self) -> ResourceMetrics:
        container = self._get_container()
        if container is None:
            raise RuntimeError(f"Docker container '{self._runtime_id}' is not running")
        stats = container.stats(stream=False)
        metrics = _stats_to_metrics(stats)
        self._last_metrics = metrics
        return metrics

    def last_metrics(self) -> ResourceMetrics | None:
        return self._last_metrics

    def check_limits(self) -> tuple[bool, str | None]:
        metrics = self.snapshot()
        memory_limit = _limit_int(self._limits, "memory_mb")
        if memory_limit is not None and metrics.memory_mb > memory_limit:
            return False, f"Container exceeded memory limit of {memory_limit}MB"
        return True, None

    def _get_container(self) -> Any | None:
        client = self._client
        if client is None:
            raise RuntimeError("Docker monitor has not been started")
        return _lookup_container(client, self._runtime_id)


class DockerCommandRunner:
    """One-shot command runner that executes inside Docker."""

    def __init__(
        self,
        *,
        tid: str | None,
        process_target: str | None,
        args: Sequence[Any] | None,
        env: Mapping[str, str] | None,
        working_dir: str | None,
        timeout: float | None,
        limits: Any | None,
        monitor_class: str | None,
        monitor_interval: float | None,
        runner_options: Mapping[str, Any] | None,
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        del db_path, config, monitor_class
        if not isinstance(process_target, str) or not process_target.strip():
            raise ValueError("Docker runner requires spec.process_target")

        options = dict(runner_options or {})
        image = options.get("image")
        if not isinstance(image, str) or not image.strip():
            raise ValueError("Docker runner requires spec.runner.options.image")

        self._tid = tid
        self._process_target = process_target.strip()
        self._args = list(args or [])
        self._env = {str(key): str(value) for key, value in dict(env or {}).items()}
        self._working_dir = working_dir
        self._timeout = timeout
        self._limits = limits
        self._monitor_interval = monitor_interval or 1.0
        self._image = image.strip()
        self._docker_binary = str(options.get("docker_binary") or "docker")
        self._docker_args = _string_list(
            options.get("docker_args"),
            name="spec.runner.options.docker_args",
        )
        self._container_workdir = (
            str(options["container_workdir"])
            if options.get("container_workdir") is not None
            else None
        )
        self._mount_workdir = bool(options.get("mount_workdir", True))

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
        executable = _resolve_docker_binary(self._docker_binary)
        container_name = _container_name(self._tid)
        command, stdin_data = self._build_docker_command(
            work_item,
            container_name,
            executable=executable,
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if stdin_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=os.environ.copy(),
        )

        with _docker_client() as client:
            container = _wait_for_container(
                client,
                runtime_id=container_name,
                process=process,
            )
            runtime_handle = _runtime_handle_for_container(
                container_name=container_name,
                image=self._image,
                docker_binary=self._docker_binary,
                container=container,
            )
            monitor = DockerContainerMonitor(
                runtime_id=container_name,
                limits=self._limits,
                image=self._image,
            )

            def _stop_runtime() -> None:
                _docker_stop(client, container_name, timeout=2.0)

            def _kill_runtime() -> None:
                _docker_kill(client, container_name)

            outcome = run_monitored_subprocess(
                process=process,
                stdin_data=stdin_data,
                timeout=self._timeout,
                limits=self._limits,
                monitor_class=None,
                monitor_interval=self._monitor_interval,
                monitor=monitor,
                db_path=None,
                config=None,
                runtime_handle=runtime_handle,
                cancel_requested=cancel_requested,
                on_worker_started=on_worker_started,
                on_runtime_handle_started=on_runtime_handle_started,
                on_stdout_chunk=on_stdout_chunk,
                on_stderr_chunk=on_stderr_chunk,
                stop_runtime=_stop_runtime,
                kill_runtime=_kill_runtime,
                worker_pid=process.pid,
            )

            final_description = _describe_runtime(
                client,
                runtime_id=container_name,
                base_metadata=dict(runtime_handle.metadata),
            )
            outcome = _apply_terminal_state(
                outcome,
                final_description=final_description,
                limits=self._limits,
            )

            updated_handle = _handle_with_runtime_metadata(
                runtime_handle,
                final_description,
            )
            outcome.runtime_handle = updated_handle

            _remove_container(client, container_name)
            return outcome

    def start_session(self) -> CommandSession:
        raise ValueError("Docker runner does not support interactive sessions")

    def start_agent_session(self) -> AgentSession:
        raise ValueError("Docker runner does not support agent sessions")

    def _build_docker_command(
        self,
        work_item: Any,
        container_name: str,
        *,
        executable: str,
    ) -> tuple[list[str], str | None]:
        inner_command, stdin_data = prepare_command_invocation(
            self._process_target,
            work_item,
            args=self._args,
        )
        docker_command = [
            executable,
            "run",
            "--name",
            container_name,
            "-i",
            *self._docker_args,
        ]

        memory_limit_mb = _limit_int(self._limits, "memory_mb")
        if memory_limit_mb is not None:
            docker_command.extend(["--memory", f"{memory_limit_mb}m"])

        cpu_percent = _limit_int(self._limits, "cpu_percent")
        if cpu_percent is not None:
            host_cpus = max(os.cpu_count() or 1, 1)
            docker_cpus = max((cpu_percent / 100.0) * host_cpus, 0.01)
            docker_command.extend(["--cpus", f"{docker_cpus:.2f}"])

        max_fds = _limit_int(self._limits, "max_fds")
        if max_fds is not None:
            docker_command.extend(["--ulimit", f"nofile={max_fds}:{max_fds}"])

        max_connections = _limit_value(self._limits, "max_connections")
        if max_connections == 0:
            docker_command.extend(["--network", "none"])

        if self._mount_workdir and self._working_dir:
            host_workdir = str(Path(self._working_dir).expanduser().resolve())
            container_workdir = self._container_workdir or host_workdir
            docker_command.extend(
                [
                    "--volume",
                    f"{host_workdir}:{container_workdir}",
                    "--workdir",
                    container_workdir,
                ]
            )

        for key, value in sorted(self._env.items()):
            docker_command.extend(["--env", f"{key}={value}"])

        docker_command.extend([self._image, *inner_command])
        return docker_command, stdin_data


class DockerRunnerPlugin:
    """Runner plugin for Docker-backed one-shot command tasks."""

    name = "docker"
    capabilities = RunnerCapabilities(
        supported_types=("command",),
        supports_interactive=False,
        supports_persistent=False,
        supports_agent_sessions=False,
    )

    def check_version(self) -> None:
        _load_docker_sdk()

    def validate_taskspec(
        self,
        taskspec_payload: Mapping[str, Any],
        *,
        preflight: bool = False,
    ) -> None:
        if os.name == "nt":
            raise ValueError(
                "Docker runner is currently supported only on Linux and macOS"
            )
        spec = _require_mapping(taskspec_payload.get("spec"), name="spec")
        if spec.get("type") != "command":
            raise ValueError("Docker runner supports only spec.type='command'")
        if bool(spec.get("interactive", False)):
            raise ValueError("Docker runner does not support interactive tasks")
        if bool(spec.get("persistent", False)):
            raise ValueError("Docker runner does not support persistent tasks")

        runner = _require_mapping(spec.get("runner"), name="spec.runner")
        options = _require_mapping(runner.get("options"), name="spec.runner.options")
        image = options.get("image")
        if not isinstance(image, str) or not image.strip():
            raise ValueError("Docker runner requires spec.runner.options.image")
        docker_args = _string_list(
            options.get("docker_args"),
            name="spec.runner.options.docker_args",
        )
        _validate_extra_docker_args(docker_args)

        limits = spec.get("limits")
        if isinstance(limits, Mapping):
            max_connections = limits.get("max_connections")
            if max_connections not in (None, 0, 0.0):
                raise ValueError(
                    "Docker runner supports spec.limits.max_connections only when "
                    "it is 0 (mapped to Docker network isolation)"
                )

        if preflight:
            docker_binary = str(options.get("docker_binary") or "docker")
            _resolve_docker_binary(docker_binary)
            with _docker_client(timeout=5) as client:
                client.ping()

    def create_runner(
        self,
        *,
        target_type: str,
        tid: str | None,
        function_target: str | None,
        process_target: str | None,
        agent: Mapping[str, Any] | None,
        args: Sequence[Any] | None,
        kwargs: Mapping[str, Any] | None,
        env: Mapping[str, str] | None,
        working_dir: str | None,
        timeout: float | None,
        limits: Any | None,
        monitor_class: str | None,
        monitor_interval: float | None,
        runner_options: Mapping[str, Any] | None,
        persistent: bool,
        interactive: bool,
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> DockerCommandRunner:
        del target_type, function_target, agent, kwargs, persistent, interactive
        if os.name == "nt":
            raise ValueError(
                "Docker runner is currently supported only on Linux and macOS"
            )
        return DockerCommandRunner(
            tid=tid,
            process_target=process_target,
            args=args,
            env=env,
            working_dir=working_dir,
            timeout=timeout,
            limits=limits,
            monitor_class=monitor_class,
            monitor_interval=monitor_interval,
            runner_options=runner_options,
            db_path=db_path,
            config=config,
        )

    def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        with _docker_client() as client:
            return _docker_stop(client, handle.runtime_id, timeout=timeout)

    def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        del timeout
        with _docker_client() as client:
            return _docker_kill(client, handle.runtime_id)

    def describe(self, handle: RunnerHandle) -> RunnerRuntimeDescription | None:
        with _docker_client() as client:
            return _describe_runtime(
                client,
                runtime_id=handle.runtime_id,
                base_metadata=dict(handle.metadata),
            )


_PLUGIN = DockerRunnerPlugin()


def get_runner_plugin() -> RunnerPlugin:
    return _PLUGIN


def _container_name(tid: str | None) -> str:
    suffix = (
        tid[-8:] if isinstance(tid, str) and len(tid) >= 8 else uuid.uuid4().hex[:8]
    )
    return f"weft-{suffix}-{uuid.uuid4().hex[:8]}"


def _apply_terminal_state(
    outcome: RunnerOutcome,
    *,
    final_description: RunnerRuntimeDescription | None,
    limits: Any | None,
) -> RunnerOutcome:
    metadata = dict(final_description.metadata) if final_description is not None else {}
    oom_killed = bool(metadata.get("oom_killed"))
    if not oom_killed:
        if outcome.metrics is None:
            outcome.metrics = _metrics_from_runtime_metadata(metadata)
        return outcome

    memory_limit = _limit_int(limits, "memory_mb")
    if memory_limit is None:
        error = "Container exceeded its configured memory limit"
    else:
        error = f"Container exceeded memory limit of {memory_limit}MB"

    outcome.status = "limit"
    outcome.error = error
    if outcome.metrics is None:
        outcome.metrics = _metrics_from_runtime_metadata(metadata)
    return outcome


def _metrics_from_runtime_metadata(
    metadata: Mapping[str, Any],
) -> ResourceMetrics | None:
    memory_usage = metadata.get("memory_usage_mb")
    cpu_percent = metadata.get("cpu_percent")
    memory_mb_value: float | None = None
    if isinstance(memory_usage, (int, float)):
        memory_mb_value = float(memory_usage)
    cpu_percent_value: float | None = None
    if isinstance(cpu_percent, (int, float)):
        cpu_percent_value = float(cpu_percent)
    if memory_mb_value is None and cpu_percent_value is None:
        return None
    return ResourceMetrics(
        timestamp=time.time_ns(),
        memory_mb=memory_mb_value or 0.0,
        cpu_percent=cpu_percent_value or 0.0,
        open_files=0,
        connections=0,
    )


def _handle_with_runtime_metadata(
    handle: RunnerHandle,
    description: RunnerRuntimeDescription | None,
) -> RunnerHandle:
    metadata = dict(handle.metadata)
    if description is not None:
        metadata.update(description.metadata)
    return RunnerHandle(
        runner_name=handle.runner_name,
        runtime_id=handle.runtime_id,
        host_pids=handle.host_pids,
        metadata=metadata,
    )


def _runtime_handle_for_container(
    *,
    container_name: str,
    image: str,
    docker_binary: str,
    container: Any | None,
) -> RunnerHandle:
    metadata: dict[str, Any] = {
        "container_name": container_name,
        "docker_binary": docker_binary,
        "image": image,
    }
    if container is not None:
        metadata["container_id"] = container.id
    return RunnerHandle(
        runner_name="docker",
        runtime_id=container_name,
        metadata=metadata,
    )


def _describe_runtime(
    client: Any,
    *,
    runtime_id: str,
    base_metadata: Mapping[str, Any],
) -> RunnerRuntimeDescription:
    metadata = dict(base_metadata)
    container_id = metadata.get("container_id")
    container = _lookup_container(
        client,
        runtime_id,
        fallback_id=container_id if isinstance(container_id, str) else None,
    )
    if container is None:
        return RunnerRuntimeDescription(
            runner_name="docker",
            runtime_id=runtime_id,
            state="missing",
            metadata=metadata,
        )

    container.reload()
    attrs = container.attrs
    state_payload = attrs.get("State") if isinstance(attrs, Mapping) else {}
    if not isinstance(state_payload, Mapping):
        state_payload = {}

    metadata["container_id"] = container.id
    metadata["container_name"] = attrs.get("Name", "").lstrip("/") or runtime_id
    image = _image_name_from_attrs(attrs)
    if image:
        metadata["image"] = image

    state = state_payload.get("Status")
    if isinstance(state, str):
        metadata["status"] = state
    metadata["oom_killed"] = bool(state_payload.get("OOMKilled"))
    metadata["exit_code"] = state_payload.get("ExitCode")
    host_pid = state_payload.get("Pid")
    if isinstance(host_pid, int) and host_pid > 0:
        metadata["host_pid"] = host_pid
    started_at = state_payload.get("StartedAt")
    if isinstance(started_at, str) and started_at:
        metadata["started_at"] = started_at
    finished_at = state_payload.get("FinishedAt")
    if isinstance(finished_at, str) and finished_at:
        metadata["finished_at"] = finished_at
    error = state_payload.get("Error")
    if isinstance(error, str) and error:
        metadata["engine_error"] = error
    host_config = attrs.get("HostConfig") if isinstance(attrs, Mapping) else {}
    if isinstance(host_config, Mapping):
        network_mode = host_config.get("NetworkMode")
        if isinstance(network_mode, str) and network_mode:
            metadata["network_mode"] = network_mode

    stats_metadata = _docker_stats_metadata(container.stats(stream=False))
    metadata.update(stats_metadata)

    return RunnerRuntimeDescription(
        runner_name="docker",
        runtime_id=runtime_id,
        state=state if isinstance(state, str) else "unknown",
        metadata=metadata,
    )


def _image_name_from_attrs(attrs: Mapping[str, Any]) -> str | None:
    config = attrs.get("Config")
    if not isinstance(config, Mapping):
        return None
    image = config.get("Image")
    return image if isinstance(image, str) and image else None


def _docker_stats_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    metrics = _stats_to_metrics(payload)
    metadata["cpu_percent"] = round(metrics.cpu_percent, 2)
    metadata["memory_usage_mb"] = round(metrics.memory_mb, 3)

    memory_limit = _memory_limit_mb(payload)
    if memory_limit is not None:
        metadata["memory_limit_mb"] = memory_limit

    pids = _pids_current(payload)
    if pids is not None:
        metadata["pids"] = pids

    network_io = _network_io_bytes(payload)
    if network_io is not None:
        metadata["network_io_bytes"] = network_io

    block_io = _block_io_bytes(payload)
    if block_io is not None:
        metadata["block_io_bytes"] = block_io

    return metadata


def _stats_to_metrics(payload: Mapping[str, Any]) -> ResourceMetrics:
    return ResourceMetrics(
        timestamp=time.time_ns(),
        memory_mb=_memory_usage_mb(payload),
        cpu_percent=_cpu_percent(payload),
        open_files=0,
        connections=0,
    )


def _memory_usage_mb(payload: Mapping[str, Any]) -> float:
    memory_stats = payload.get("memory_stats")
    if not isinstance(memory_stats, Mapping):
        return 0.0
    usage = memory_stats.get("usage")
    if not isinstance(usage, (int, float)):
        return 0.0
    return round(float(usage) / (1024 * 1024), 3)


def _memory_limit_mb(payload: Mapping[str, Any]) -> float | None:
    memory_stats = payload.get("memory_stats")
    if not isinstance(memory_stats, Mapping):
        return None
    limit = memory_stats.get("limit")
    if not isinstance(limit, (int, float)) or limit <= 0:
        return None
    return round(float(limit) / (1024 * 1024), 3)


def _cpu_percent(payload: Mapping[str, Any]) -> float:
    cpu_stats = payload.get("cpu_stats")
    precpu_stats = payload.get("precpu_stats")
    if not isinstance(cpu_stats, Mapping) or not isinstance(precpu_stats, Mapping):
        return 0.0

    cpu_usage = cpu_stats.get("cpu_usage")
    precpu_usage = precpu_stats.get("cpu_usage")
    if not isinstance(cpu_usage, Mapping) or not isinstance(precpu_usage, Mapping):
        return 0.0

    total_usage = cpu_usage.get("total_usage")
    previous_total = precpu_usage.get("total_usage")
    system_usage = cpu_stats.get("system_cpu_usage")
    previous_system = precpu_stats.get("system_cpu_usage")
    if not isinstance(total_usage, (int, float)):
        return 0.0
    if not isinstance(previous_total, (int, float)):
        return 0.0
    if not isinstance(system_usage, (int, float)):
        return 0.0
    if not isinstance(previous_system, (int, float)):
        return 0.0

    total_usage_value = float(total_usage)
    previous_total_value = float(previous_total)
    system_usage_value = float(system_usage)
    previous_system_value = float(previous_system)

    cpu_delta = total_usage_value - previous_total_value
    system_delta = system_usage_value - previous_system_value
    if cpu_delta <= 0 or system_delta <= 0:
        return 0.0

    online_cpus = cpu_stats.get("online_cpus")
    if not isinstance(online_cpus, int) or online_cpus <= 0:
        percpu_usage = cpu_usage.get("percpu_usage")
        if isinstance(percpu_usage, Sequence) and not isinstance(
            percpu_usage, (str, bytes)
        ):
            online_cpus = max(len(percpu_usage), 1)
        else:
            online_cpus = 1

    return round((cpu_delta / system_delta) * float(online_cpus) * 100.0, 2)


def _pids_current(payload: Mapping[str, Any]) -> int | None:
    pids_stats = payload.get("pids_stats")
    if not isinstance(pids_stats, Mapping):
        return None
    current = pids_stats.get("current")
    if isinstance(current, int) and current >= 0:
        return current
    return None


def _network_io_bytes(payload: Mapping[str, Any]) -> dict[str, int] | None:
    networks = payload.get("networks")
    if not isinstance(networks, Mapping):
        return None
    rx_total = 0
    tx_total = 0
    seen = False
    for value in networks.values():
        if not isinstance(value, Mapping):
            continue
        rx_bytes = value.get("rx_bytes")
        tx_bytes = value.get("tx_bytes")
        if isinstance(rx_bytes, int) and rx_bytes >= 0:
            rx_total += rx_bytes
            seen = True
        if isinstance(tx_bytes, int) and tx_bytes >= 0:
            tx_total += tx_bytes
            seen = True
    if not seen:
        return None
    return {"rx": rx_total, "tx": tx_total}


def _block_io_bytes(payload: Mapping[str, Any]) -> dict[str, int] | None:
    blkio_stats = payload.get("blkio_stats")
    if not isinstance(blkio_stats, Mapping):
        return None
    entries = blkio_stats.get("io_service_bytes_recursive")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return None
    read_total = 0
    write_total = 0
    seen = False
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        op = entry.get("op")
        value = entry.get("value")
        if not isinstance(op, str) or not isinstance(value, int):
            continue
        normalized = op.lower()
        if normalized == "read":
            read_total += value
            seen = True
        elif normalized == "write":
            write_total += value
            seen = True
    if not seen:
        return None
    return {"read": read_total, "write": write_total}


def _lookup_container(
    client: Any,
    runtime_id: str,
    *,
    fallback_id: str | None = None,
) -> Any | None:
    docker = _load_docker_sdk()

    def _get(identifier: str) -> Any | None:
        try:
            return client.containers.get(identifier)
        except docker.errors.NotFound:
            return None

    container = _get(runtime_id)
    if container is not None:
        return container

    if isinstance(fallback_id, str) and fallback_id and fallback_id != runtime_id:
        container = _get(fallback_id)
        if container is not None:
            return container

    list_method = getattr(client.containers, "list", None)
    if not callable(list_method):
        return None
    try:
        candidates = list_method(all=True, filters={"name": runtime_id})
    except Exception:  # pragma: no cover - defensive Docker API fallback
        return None
    for candidate in candidates:
        attrs = getattr(candidate, "attrs", None)
        if isinstance(attrs, Mapping):
            name = attrs.get("Name")
            if isinstance(name, str) and name.lstrip("/") == runtime_id:
                return candidate
        candidate_name = getattr(candidate, "name", None)
        if isinstance(candidate_name, str) and candidate_name == runtime_id:
            return candidate
    return candidates[0] if candidates else None


def _wait_for_container(
    client: Any,
    *,
    runtime_id: str,
    process: subprocess.Popen[str],
) -> Any | None:
    deadline = time.monotonic() + _CONTAINER_LOOKUP_TIMEOUT
    while time.monotonic() < deadline:
        container = _lookup_container(client, runtime_id)
        if container is not None:
            return container
        if process.poll() is not None:
            break
        time.sleep(_CONTAINER_LOOKUP_INTERVAL)
    return _lookup_container(client, runtime_id)


def _docker_stop(client: Any, runtime_id: str, *, timeout: float | None) -> bool:
    container = _lookup_container(client, runtime_id)
    if container is None:
        return False
    try:
        stop_timeout = max(int(timeout or 2.0), 1)
        container.stop(timeout=stop_timeout)
    except Exception:  # pragma: no cover - Docker daemon edge conditions
        return False
    return True


def _docker_kill(client: Any, runtime_id: str) -> bool:
    container = _lookup_container(client, runtime_id)
    if container is None:
        return False
    try:
        container.kill()
    except Exception:  # pragma: no cover - Docker daemon edge conditions
        return False
    return True


def _remove_container(client: Any, runtime_id: str) -> None:
    container = _lookup_container(client, runtime_id)
    if container is None:
        return
    try:
        container.remove(force=True)
    except Exception:  # pragma: no cover - best-effort cleanup
        return


@contextmanager
def _docker_client(*, timeout: int = 10) -> Iterator[Any]:
    client = _docker_client_from_env(timeout=timeout)
    try:
        yield client
    finally:
        client.close()


def _docker_client_from_env(*, timeout: int = 10) -> Any:
    docker = _load_docker_sdk()
    return docker.from_env(version="auto", timeout=timeout)


def _load_docker_sdk() -> Any:
    try:
        import docker
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "Docker runner requires the Docker SDK for Python. Install weft[docker]."
        ) from exc
    return docker


def _resolve_docker_binary(docker_binary: str) -> str:
    executable = shutil.which(docker_binary)
    if executable is None:
        raise ValueError(f"Docker binary '{docker_binary}' is not available on PATH")
    return executable


def _validate_extra_docker_args(args: Sequence[str]) -> None:
    reserved = {
        "--cpus",
        "--env",
        "--interactive",
        "--memory",
        "--name",
        "--network",
        "--rm",
        "--ulimit",
        "--volume",
        "--workdir",
        "-e",
        "-i",
        "-v",
        "-w",
    }
    for arg in args:
        if arg in reserved:
            raise ValueError(
                f"Docker runner option '{arg}' is managed by TaskSpec fields and "
                "cannot be passed through spec.runner.options.docker_args"
            )


def _require_mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _string_list(value: object, *, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a list of strings")
    return [str(item) for item in value]


def _limit_int(limits: Any | None, field_name: str) -> int | None:
    value = _limit_value(limits, field_name)
    if isinstance(value, int) and value > 0:
        return value
    return None


def _limit_value(limits: Any | None, field_name: str) -> Any | None:
    if limits is None:
        return None
    return getattr(limits, field_name, None)
