"""Runner facade for task execution backends.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3], [CC-3.1], [CC-3.3]
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1]
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from simplebroker import BrokerTarget
from weft._runner_plugins import require_runner_plugin
from weft.core.runner_validation import validate_runner_capabilities
from weft.core.runners import RunnerOutcome
from weft.ext import RunnerHandle

from .sessions import AgentSession, CommandSession


class TaskRunner:
    """Dispatch task execution to the configured runner plugin.

    Spec: docs/specifications/01-Core_Components.md [CC-3], [CC-3.1]
    """

    def __init__(
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
        runner_name: str = "host",
        runner_options: Mapping[str, Any] | None = None,
        persistent: bool = False,
        interactive: bool = False,
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._target_type = target_type
        self._persistent = persistent
        self._interactive = interactive
        self._runner_name = runner_name.strip() or "host"
        self._runner_options = dict(runner_options or {})
        self._taskspec_payload = _build_runner_validation_payload(
            target_type=target_type,
            tid=tid,
            function_target=function_target,
            process_target=process_target,
            agent=agent,
            args=args,
            kwargs=kwargs,
            env=env,
            working_dir=working_dir,
            timeout=timeout,
            limits=limits,
            monitor_class=monitor_class,
            monitor_interval=monitor_interval,
            runner_name=self._runner_name,
            runner_options=self._runner_options,
            persistent=persistent,
            interactive=interactive,
        )

        plugin = require_runner_plugin(self._runner_name)
        plugin.check_version()
        validate_runner_capabilities(plugin, self._taskspec_payload)
        plugin.validate_taskspec(self._taskspec_payload, preflight=False)

        self._plugin = plugin
        self._backend = plugin.create_runner(
            target_type=target_type,
            tid=tid,
            function_target=function_target,
            process_target=process_target,
            agent=agent,
            args=args,
            kwargs=kwargs,
            env=env,
            working_dir=working_dir,
            timeout=timeout,
            limits=limits,
            monitor_class=monitor_class,
            monitor_interval=monitor_interval,
            runner_options=self._runner_options,
            persistent=persistent,
            interactive=interactive,
            db_path=db_path,
            config=config,
        )

    def run(self, work_item: Any) -> RunnerOutcome:
        """Execute *work_item* through the configured runner backend.

        Spec: [CC-3]
        """
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
        """Execute *work_item* with optional lifecycle hooks.

        Spec: [CC-3], [RM-5.1]
        """
        self._plugin.validate_taskspec(self._taskspec_payload, preflight=True)
        kwargs: dict[str, Any] = {
            "cancel_requested": cancel_requested,
            "on_worker_started": on_worker_started,
            "on_runtime_handle_started": on_runtime_handle_started,
        }
        if self.supports_stream_callbacks():
            kwargs["on_stdout_chunk"] = on_stdout_chunk
            kwargs["on_stderr_chunk"] = on_stderr_chunk
        return self._backend.run_with_hooks(
            work_item,
            **kwargs,
        )

    def supports_stream_callbacks(self) -> bool:
        """Return whether the backend accepts live stdout/stderr callbacks."""
        parameters = inspect.signature(self._backend.run_with_hooks).parameters
        return "on_stdout_chunk" in parameters and "on_stderr_chunk" in parameters

    def start_session(self) -> CommandSession:
        """Start an interactive command session for the configured runner."""
        self._plugin.validate_taskspec(self._taskspec_payload, preflight=True)
        return self._backend.start_session()

    def start_agent_session(self) -> AgentSession:
        """Start a long-lived agent session for the configured runner."""
        self._plugin.validate_taskspec(self._taskspec_payload, preflight=True)
        return self._backend.start_agent_session()


def _build_runner_validation_payload(
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
    runner_name: str,
    runner_options: Mapping[str, Any] | None,
    persistent: bool,
    interactive: bool,
) -> dict[str, Any]:
    limits_payload = limits
    dump_model = getattr(limits, "model_dump", None)
    if callable(dump_model):
        limits_payload = dump_model(mode="python")

    agent_payload = dict(agent) if agent is not None else None
    env_payload = dict(env or {})

    return {
        "tid": tid,
        "spec": {
            "type": target_type,
            "function_target": function_target,
            "process_target": process_target,
            "agent": agent_payload,
            "args": list(args or []),
            "keyword_args": dict(kwargs or {}),
            "env": env_payload,
            "working_dir": working_dir,
            "timeout": timeout,
            "limits": limits_payload,
            "monitor_class": monitor_class,
            "polling_interval": monitor_interval,
            "interactive": interactive,
            "persistent": persistent,
            "runner": {
                "name": runner_name,
                "options": dict(runner_options or {}),
            },
        },
    }


__all__ = ["AgentSession", "CommandSession", "RunnerOutcome", "TaskRunner"]
