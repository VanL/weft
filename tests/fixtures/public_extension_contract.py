"""Public-only extension implementation probe. Spec: [PY-1]."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from simplebroker import BrokerTarget
from weft.client import AgentSection
from weft.ext import (
    AgentResolver,
    AgentResolverResult,
    AgentSessionProtocol,
    AgentToolProfile,
    AgentToolProfileResult,
    CommandSessionProtocol,
    NormalizedAgentWorkItem,
    ResourceMetrics,
    RunnerCapabilities,
    RunnerHandle,
    RunnerOutcome,
    RunnerPlugin,
    RunnerRuntimeDescription,
    SessionExecutionResult,
    TaskRunnerBackend,
)


class CommandSession:
    """A stream implementation without subprocess implementation imports."""

    pid: int | None = None
    handle: RunnerHandle | None = None
    last_metrics: ResourceMetrics | None = None

    def __init__(self) -> None:
        self.output: list[str] = []
        self.closed = False

    def send(self, data: str) -> None:
        self.output.append(data)

    def close_stdin(self) -> None:
        self.closed = True

    def poll_stdout(self) -> list[str]:
        result, self.output = self.output, []
        return result

    def poll_stderr(self) -> list[str]:
        return []

    def is_alive(self) -> bool:
        return not self.closed

    def returncode(self) -> int | None:
        return 0 if self.closed else None

    def terminate(self, *, deadline: float | None = None) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True

    def poll_limits(self) -> tuple[bool, str | None]:
        return True, None

    def stop_monitor(self) -> None:
        self.last_metrics = ResourceMetrics(memory_mb=1.0)


class AgentSession:
    """A persistent implementation using the public result envelope."""

    pid: int | None = None
    handle: RunnerHandle | None = None

    def __init__(self) -> None:
        self.closed = False

    def execute(
        self, work_item: Any, *, cancel_requested: Callable[[], bool] | None = None
    ) -> SessionExecutionResult:
        if self.closed:
            raise RuntimeError("closed")
        if cancel_requested is not None and cancel_requested():
            return SessionExecutionResult("cancelled", None, "cancelled")
        return SessionExecutionResult("ok", work_item, None)

    def close(self, *, deadline: float | None = None) -> None:
        self.closed = True


class Backend:
    """A structurally typed backend requiring no private imports."""

    def run(self, work_item: Any) -> RunnerOutcome:
        return RunnerOutcome("ok", work_item, None, None, None, 0, 0.0)

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
        if on_stdout_chunk is not None:
            on_stdout_chunk("streamed", True)
        return self.run(work_item)

    def start_session(self) -> CommandSession:
        return CommandSession()

    def start_agent_session(self) -> AgentSession:
        return AgentSession()


def resolve(
    *, agent: AgentSection, work_item: NormalizedAgentWorkItem, tid: str | None
) -> AgentResolverResult:
    return AgentResolverResult(str(work_item.content), instructions=agent.instructions)


def tool_profile(*, agent: AgentSection, tid: str | None) -> AgentToolProfileResult:
    return AgentToolProfileResult(instructions=agent.instructions)


backend: TaskRunnerBackend = Backend()
command_session: CommandSessionProtocol = CommandSession()
agent_session: AgentSessionProtocol = AgentSession()
resolver: AgentResolver = resolve
profile: AgentToolProfile = tool_profile


class Plugin:
    name = "public-test"
    capabilities = RunnerCapabilities()

    def check_version(self) -> None:
        pass

    def validate_taskspec(
        self,
        taskspec_payload: Mapping[str, Any],
        *,
        bundle_root: str | None = None,
        preflight: bool = False,
    ) -> None:
        pass

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
    ) -> TaskRunnerBackend:
        return Backend()

    def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        return False

    def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
        return False

    def describe(self, handle: RunnerHandle) -> RunnerRuntimeDescription | None:
        return None


plugin: RunnerPlugin = Plugin()
