"""Microsandbox runner plugin for Weft.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.1], [CC-3.2], [CC-3.4]
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/06-Resource_Management.md [RM-5]
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from simplebroker import BrokerTarget
from weft.core.agents.provider_cli.execution import (
    build_provider_cli_execution_result,
    prepare_provider_cli_execution,
)
from weft.core.agents.runtime import normalize_agent_work_item
from weft.core.runners import RunnerOutcome
from weft.core.runners.subprocess_runner import prepare_command_invocation
from weft.core.tasks.runner import AgentSession, CommandSession
from weft.core.taskspec import AgentSection
from weft.ext import (
    RunnerCapabilities,
    RunnerHandle,
    RunnerPlugin,
    RunnerRuntimeDescription,
)

from ._options import MicrosandboxOptions, parse_options, parse_options_from_payload
from ._runtime import (
    FileCopyBack,
    FileCopyIntoGuest,
    MicrosandboxRunResult,
    MicrosandboxRunSpec,
    MicrosandboxRuntime,
    MicrosandboxRuntimeError,
    MicrosandboxStarted,
    WorkspaceSpec,
)


class MicrosandboxRunner:
    """One-shot Microsandbox backend for command and provider_cli agent tasks."""

    def __init__(
        self,
        *,
        target_type: str,
        tid: str | None,
        process_target: str | None,
        agent: Mapping[str, Any] | None,
        args: Sequence[Any] | None,
        env: Mapping[str, str] | None,
        working_dir: str | None,
        timeout: float | None,
        limits: Any | None,
        runner_options: Mapping[str, Any] | None,
        bundle_root: str | None = None,
        runtime: Any | None = None,
    ) -> None:
        self._target_type = target_type
        self._tid = tid
        self._process_target = process_target
        self._agent_payload = dict(agent or {}) if agent is not None else None
        self._args = list(args or [])
        self._timeout = timeout
        self._bundle_root = bundle_root
        self._options = parse_options(
            target_type=target_type,
            agent=agent,
            runner_options=runner_options,
            env=env,
            working_dir=working_dir,
            limits=limits,
            persistent=False,
            interactive=False,
        )
        self._runtime = runtime or MicrosandboxRuntime()

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
        if self._options.mode == "agent":
            return self._run_agent(
                work_item,
                cancel_requested=cancel_requested,
                on_worker_started=on_worker_started,
                on_runtime_handle_started=on_runtime_handle_started,
                on_stdout_chunk=on_stdout_chunk,
                on_stderr_chunk=on_stderr_chunk,
            )
        return self._run_tool(
            work_item,
            cancel_requested=cancel_requested,
            on_worker_started=on_worker_started,
            on_runtime_handle_started=on_runtime_handle_started,
            on_stdout_chunk=on_stdout_chunk,
            on_stderr_chunk=on_stderr_chunk,
        )

    def start_session(self) -> CommandSession:
        raise ValueError("Microsandbox runner does not support interactive sessions")

    def start_agent_session(self) -> AgentSession:
        raise ValueError("Microsandbox runner does not support agent sessions")

    def _run_tool(
        self,
        work_item: Any,
        *,
        cancel_requested: Callable[[], bool] | None,
        on_worker_started: Callable[[int | None], None] | None,
        on_runtime_handle_started: Callable[[RunnerHandle], None] | None,
        on_stdout_chunk: Callable[[str, bool], None] | None,
        on_stderr_chunk: Callable[[str, bool], None] | None,
    ) -> RunnerOutcome:
        if self._process_target is None:
            raise ValueError("Microsandbox tool mode requires spec.process_target")
        command, stdin_data = prepare_command_invocation(
            self._process_target,
            work_item,
            args=self._args,
        )
        return self._execute(
            command=tuple(command),
            stdin_text=stdin_data,
            value_builder=lambda result: result.stdout,
            cancel_requested=cancel_requested,
            on_worker_started=on_worker_started,
            on_runtime_handle_started=on_runtime_handle_started,
            on_stdout_chunk=on_stdout_chunk,
            on_stderr_chunk=on_stderr_chunk,
        )

    def _run_agent(
        self,
        work_item: Any,
        *,
        cancel_requested: Callable[[], bool] | None,
        on_worker_started: Callable[[int | None], None] | None,
        on_runtime_handle_started: Callable[[RunnerHandle], None] | None,
        on_stdout_chunk: Callable[[str, bool], None] | None,
        on_stderr_chunk: Callable[[str, bool], None] | None,
    ) -> RunnerOutcome:
        if self._agent_payload is None:
            raise ValueError("Microsandbox agent mode requires spec.agent")
        if self._options.executable is None:
            raise ValueError("Microsandbox agent mode requires a guest executable")
        agent = AgentSection.model_validate(self._agent_payload)
        normalized_work_item = normalize_agent_work_item(agent, work_item)
        # Keep the provider tempdir under /tmp so host and guest paths match when
        # provider CLIs receive absolute paths such as Claude's --mcp-config.
        with tempfile.TemporaryDirectory(
            prefix="weft-microsandbox-provider-cli-",
            dir="/tmp",
        ) as tempdir:
            temp_path = Path(tempdir)
            prepared = prepare_provider_cli_execution(
                agent=agent,
                work_item=normalized_work_item,
                tid=self._tid,
                executable=self._options.executable,
                cwd=self._options.cwd,
                tempdir=temp_path,
                bundle_root=self._bundle_root,
            )
            copy_back: tuple[FileCopyBack, ...] = ()
            guest_dirs: tuple[str, ...] = (str(temp_path),)
            copy_into_guest = (
                FileCopyIntoGuest(
                    host_path=str(temp_path),
                    guest_path=str(temp_path),
                ),
            )
            if prepared.invocation.output_path is not None:
                copy_back = (
                    FileCopyBack(
                        guest_path=str(prepared.invocation.output_path),
                        host_path=str(prepared.invocation.output_path),
                    ),
                )

            def _build_agent_result(result: MicrosandboxRunResult) -> Any:
                completed = subprocess.CompletedProcess(
                    args=list(prepared.invocation.command),
                    returncode=result.exit_code if result.exit_code is not None else 1,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
                provider_result = prepared.provider.parse_result(
                    completed=completed,
                    invocation=prepared.invocation,
                )
                return build_provider_cli_execution_result(
                    agent=agent,
                    prepared=prepared,
                    work_item=normalized_work_item,
                    provider_result=provider_result,
                )

            return self._execute(
                command=tuple(prepared.invocation.command),
                stdin_text=prepared.invocation.stdin_text,
                extra_env=prepared.invocation.env,
                copy_into_guest=copy_into_guest,
                copy_back=copy_back,
                guest_dirs=guest_dirs,
                value_builder=_build_agent_result,
                cancel_requested=cancel_requested,
                on_worker_started=on_worker_started,
                on_runtime_handle_started=on_runtime_handle_started,
                on_stdout_chunk=on_stdout_chunk,
                on_stderr_chunk=on_stderr_chunk,
            )

    def _execute(
        self,
        *,
        command: tuple[str, ...],
        stdin_text: str | None,
        value_builder: Callable[[MicrosandboxRunResult], Any],
        cancel_requested: Callable[[], bool] | None,
        on_worker_started: Callable[[int | None], None] | None,
        on_runtime_handle_started: Callable[[RunnerHandle], None] | None,
        on_stdout_chunk: Callable[[str, bool], None] | None,
        on_stderr_chunk: Callable[[str, bool], None] | None,
        copy_back: tuple[FileCopyBack, ...] = (),
        copy_into_guest: tuple[FileCopyIntoGuest, ...] = (),
        guest_dirs: tuple[str, ...] = (),
        extra_env: Mapping[str, str] | None = None,
    ) -> RunnerOutcome:
        sandbox_name = _sandbox_name(
            self._tid, prefix=self._options.sandbox_name_prefix
        )
        started_at = time.monotonic()
        runtime_handle: RunnerHandle | None = None

        def _on_started(started: MicrosandboxStarted) -> None:
            nonlocal runtime_handle
            runtime_handle = _runtime_handle(
                options=self._options,
                sandbox_id=started.sandbox_id,
                sandbox_name=started.sandbox_name,
                host_pid=started.host_pid,
            )
            _safe_callback(on_worker_started, started.host_pid)
            if on_runtime_handle_started is not None:
                try:
                    on_runtime_handle_started(runtime_handle)
                except Exception:  # pragma: no cover - callback safety
                    pass

        try:
            result = self._runtime.run(
                MicrosandboxRunSpec(
                    name=sandbox_name,
                    image=self._options.image,
                    command=command,
                    env={**dict(self._options.env), **dict(extra_env or {})},
                    cwd=self._options.cwd,
                    network=self._options.network,
                    workspace=_workspace_spec(self._options),
                    mounts=self._options.mounts,
                    timeout_seconds=self._timeout,
                    stdin_text=stdin_text,
                    memory_mb=self._options.memory_mb,
                    cpus=self._options.cpus,
                    max_fds=self._options.max_fds,
                    guest_dirs=guest_dirs,
                    copy_into_guest=copy_into_guest,
                    copy_back=copy_back,
                    labels={"weft.runner": "microsandbox"},
                ),
                on_started=_on_started,
                cancel_requested=cancel_requested,
            )
        except MicrosandboxRuntimeError as exc:
            return RunnerOutcome(
                status="error",
                value=None,
                error=str(exc),
                stdout=None,
                stderr=str(exc),
                returncode=None,
                duration=time.monotonic() - started_at,
                runtime_handle=runtime_handle,
            )

        _emit_stream(result.stdout, on_stdout_chunk)
        _emit_stream(result.stderr, on_stderr_chunk)
        if runtime_handle is None:
            runtime_handle = _runtime_handle(
                options=self._options,
                sandbox_id=result.sandbox_id,
                sandbox_name=result.sandbox_name,
                host_pid=None,
            )

        if result.cancelled:
            return RunnerOutcome(
                status="cancelled",
                value=None,
                error=result.stderr or "Target execution cancelled",
                stdout=result.stdout or None,
                stderr=result.stderr or None,
                returncode=result.exit_code,
                duration=result.duration,
                runtime_handle=runtime_handle,
            )
        if result.timed_out:
            return RunnerOutcome(
                status="timeout",
                value=None,
                error=result.stderr or "Target execution timed out",
                stdout=result.stdout or None,
                stderr=result.stderr or None,
                returncode=result.exit_code,
                duration=result.duration,
                runtime_handle=runtime_handle,
            )
        if result.exit_code != 0:
            return RunnerOutcome(
                status="error",
                value=None,
                error=result.stderr or f"Target exited with status {result.exit_code}",
                stdout=result.stdout or None,
                stderr=result.stderr or None,
                returncode=result.exit_code,
                duration=result.duration,
                runtime_handle=runtime_handle,
            )
        try:
            value = value_builder(result)
        except Exception as exc:
            return RunnerOutcome(
                status="error",
                value=None,
                error=str(exc),
                stdout=result.stdout or None,
                stderr=result.stderr or None,
                returncode=result.exit_code,
                duration=result.duration,
                runtime_handle=runtime_handle,
            )
        return RunnerOutcome(
            status="ok",
            value=value,
            error=None,
            stdout=result.stdout or None,
            stderr=result.stderr or None,
            returncode=result.exit_code,
            duration=result.duration,
            runtime_handle=runtime_handle,
        )


class MicrosandboxRunnerPlugin:
    """Runner plugin for Microsandbox-backed one-shot tasks."""

    name = "microsandbox"
    capabilities = RunnerCapabilities(
        supported_types=("command", "agent"),
        supports_interactive=False,
        supports_persistent=False,
        supports_agent_sessions=False,
    )

    def check_version(self) -> None:
        MicrosandboxRuntime().check_importable()

    def validate_taskspec(
        self,
        taskspec_payload: Mapping[str, Any],
        *,
        preflight: bool = False,
    ) -> None:
        parse_options_from_payload(taskspec_payload)
        if preflight:
            MicrosandboxRuntime().check_preflight()

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
        bundle_root: str | None,
        persistent: bool,
        interactive: bool,
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> MicrosandboxRunner:
        del function_target, kwargs, monitor_class, monitor_interval, db_path, config
        if persistent:
            raise ValueError("Microsandbox runner does not support persistent tasks")
        if interactive:
            raise ValueError("Microsandbox runner does not support interactive tasks")
        return MicrosandboxRunner(
            target_type=target_type,
            tid=tid,
            process_target=process_target,
            agent=agent,
            args=args,
            env=env,
            working_dir=working_dir,
            timeout=timeout,
            limits=limits,
            runner_options=runner_options,
            bundle_root=bundle_root,
        )

    def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        return MicrosandboxRuntime().stop(handle.id, timeout=timeout)

    def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        return MicrosandboxRuntime().kill(handle.id, timeout=timeout)

    def describe(self, handle: RunnerHandle) -> RunnerRuntimeDescription | None:
        description = MicrosandboxRuntime().describe(handle.id)
        if description is None:
            return None
        metadata = {
            **dict(handle.observations),
            **dict(handle.metadata),
            **dict(description.metadata),
        }
        return RunnerRuntimeDescription(
            runner="microsandbox",
            id=description.sandbox_id,
            state=description.state,
            metadata=metadata,
        )


def _workspace_spec(options: MicrosandboxOptions) -> WorkspaceSpec:
    if options.workspace_mode == "none":
        return WorkspaceSpec(mode="none")
    return WorkspaceSpec(
        mode=options.workspace_mode,
        source=options.working_dir,
        target=options.cwd,
    )


def _runtime_handle(
    *,
    options: MicrosandboxOptions,
    sandbox_id: str,
    sandbox_name: str,
    host_pid: int | None,
) -> RunnerHandle:
    observations: dict[str, Any] = {"sandbox_name": sandbox_name}
    if host_pid is not None and host_pid > 0:
        observations["host_pids"] = [host_pid]
        observations["host_processes"] = [{"pid": host_pid, "create_time": None}]
    return RunnerHandle(
        runner="microsandbox",
        kind="sandboxed-process",
        id=sandbox_id,
        control={"authority": "runner"},
        observations=observations,
        metadata={
            "sandbox_id": sandbox_id,
            "sandbox_name": sandbox_name,
            "image": options.image,
            "mode": options.mode,
            "network": options.network,
            "workspace_mode": options.workspace_mode,
        },
    )


def _sandbox_name(tid: str | None, *, prefix: str | None) -> str:
    safe_prefix = prefix or "weft"
    safe_prefix = (
        "".join(
            char if char.isalnum() or char in {"-", "_"} else "-"
            for char in safe_prefix
        ).strip("-_")
        or "weft"
    )
    suffix = (
        tid[-8:] if isinstance(tid, str) and len(tid) >= 8 else uuid.uuid4().hex[:8]
    )
    return f"{safe_prefix}-{suffix}-{uuid.uuid4().hex[:8]}"[:120]


def _emit_stream(
    text: str,
    callback: Callable[[str, bool], None] | None,
) -> None:
    if text and callback is not None:
        try:
            callback(text, True)
        except Exception:  # pragma: no cover - callback safety
            pass


def _safe_callback(
    callback: Callable[[int | None], None] | None,
    value: int | None,
) -> None:
    if callback is None:
        return
    try:
        callback(value)
    except Exception:  # pragma: no cover - callback safety
        pass


_PLUGIN = MicrosandboxRunnerPlugin()


def get_runner_plugin() -> RunnerPlugin:
    return _PLUGIN


__all__ = ["MicrosandboxRunner", "MicrosandboxRunnerPlugin", "get_runner_plugin"]
