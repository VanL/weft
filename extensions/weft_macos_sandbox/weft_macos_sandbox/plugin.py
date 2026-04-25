"""macOS sandbox runner plugin for Weft.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.1], [CC-3.2], [CC-3.4]
- docs/specifications/02-TaskSpec.md [TS-1.3]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from simplebroker import BrokerTarget
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
from weft.helpers import kill_process_tree, terminate_process_tree


class MacOSSandboxRunner:
    """One-shot command runner that wraps commands with sandbox-exec."""

    def __init__(
        self,
        *,
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
        if not isinstance(process_target, str) or not process_target.strip():
            raise ValueError("macOS sandbox runner requires spec.process_target")

        options = dict(runner_options or {})
        profile = options.get("profile")
        if not isinstance(profile, str) or not profile.strip():
            raise ValueError(
                "macOS sandbox runner requires spec.runner.options.profile"
            )

        self._process_target = process_target.strip()
        self._args = list(args or [])
        self._env = {str(key): str(value) for key, value in dict(env or {}).items()}
        self._working_dir = working_dir
        self._timeout = timeout
        self._limits = limits
        self._monitor_class = monitor_class
        self._monitor_interval = monitor_interval or 1.0
        self._profile = str(Path(profile).expanduser())
        self._sandbox_binary = str(options.get("sandbox_binary") or "sandbox-exec")
        self._db_path = db_path
        self._config = config

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
        command, stdin_data = prepare_command_invocation(
            self._process_target,
            work_item,
            args=self._args,
        )
        env_vars = os.environ.copy()
        env_vars.update(self._env)
        process = subprocess.Popen(
            [self._sandbox_binary, "-f", self._profile, *command],
            stdin=subprocess.PIPE if stdin_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self._working_dir or None,
            env=env_vars,
        )

        def _stop_runtime() -> None:
            terminate_process_tree(process.pid or -1, timeout=0.2)

        def _kill_runtime() -> None:
            kill_process_tree(process.pid or -1, timeout=0.2)

        runtime_handle = RunnerHandle(
            runner="macos-sandbox",
            kind="sandboxed-process",
            id=str(process.pid),
            control={"authority": "host-pid"},
            observations={
                "host_pids": [process.pid] if process.pid is not None else [],
                "sandbox_profile": self._profile,
            },
            metadata={"profile": self._profile},
        )
        return run_monitored_subprocess(
            process=process,
            stdin_data=stdin_data,
            timeout=self._timeout,
            limits=self._limits,
            monitor_class=self._monitor_class,
            monitor_interval=self._monitor_interval,
            monitor=None,
            db_path=self._db_path,
            config=self._config,
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

    def start_session(self) -> CommandSession:
        raise ValueError("macOS sandbox runner does not support interactive sessions")

    def start_agent_session(self) -> AgentSession:
        raise ValueError("macOS sandbox runner does not support agent sessions")


class MacOSSandboxRunnerPlugin:
    """Runner plugin for macOS sandboxed one-shot command tasks."""

    name = "macos-sandbox"
    capabilities = RunnerCapabilities(
        supported_types=("command",),
        supports_interactive=False,
        supports_persistent=False,
        supports_agent_sessions=False,
    )

    def check_version(self) -> None:
        return None

    def validate_taskspec(
        self,
        taskspec_payload: Mapping[str, Any],
        *,
        preflight: bool = False,
    ) -> None:
        spec = _require_mapping(taskspec_payload.get("spec"), name="spec")
        if spec.get("type") != "command":
            raise ValueError("macOS sandbox runner supports only spec.type='command'")
        if bool(spec.get("interactive", False)):
            raise ValueError("macOS sandbox runner does not support interactive tasks")
        if bool(spec.get("persistent", False)):
            raise ValueError("macOS sandbox runner does not support persistent tasks")

        runner = _require_mapping(spec.get("runner"), name="spec.runner")
        options = _require_mapping(runner.get("options"), name="spec.runner.options")
        profile = options.get("profile")
        if not isinstance(profile, str) or not profile.strip():
            raise ValueError(
                "macOS sandbox runner requires spec.runner.options.profile"
            )

        if preflight:
            if sys.platform != "darwin":
                raise ValueError("macOS sandbox runner is available only on macOS")
            executable = shutil.which(
                str(options.get("sandbox_binary") or "sandbox-exec")
            )
            if executable is None:
                raise ValueError("sandbox-exec is not available on PATH")
            profile_path = Path(profile).expanduser()
            if not profile_path.exists():
                raise ValueError(f"Sandbox profile does not exist: {profile_path}")

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
    ) -> MacOSSandboxRunner:
        del (
            target_type,
            tid,
            function_target,
            agent,
            kwargs,
            bundle_root,
            persistent,
            interactive,
        )
        return MacOSSandboxRunner(
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
        host_pids = handle.scoped_host_pids()
        if not host_pids:
            return False
        for pid in host_pids:
            terminate_process_tree(pid, timeout=timeout, kill_after=False)
        return True

    def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        host_pids = handle.scoped_host_pids()
        if not host_pids:
            return False
        for pid in host_pids:
            kill_process_tree(pid, timeout=timeout)
        return True

    def describe(self, handle: RunnerHandle) -> RunnerRuntimeDescription | None:
        state = "missing"
        for pid in handle.scoped_host_pids():
            if _pid_exists(pid):
                state = "running"
                break
        return RunnerRuntimeDescription(
            runner="macos-sandbox",
            id=handle.id,
            state=state,
            metadata=dict(handle.metadata),
        )


_PLUGIN = MacOSSandboxRunnerPlugin()


def get_runner_plugin() -> RunnerPlugin:
    return _PLUGIN


def _require_mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
