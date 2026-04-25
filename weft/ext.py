"""Public extension contracts for Weft.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.1], [CC-3.2]
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/13-Agent_Runtime.md [AR-5]
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from simplebroker import BrokerTarget
    from weft.core.agents.runtime import NormalizedAgentWorkItem
    from weft.core.runners.host import RunnerOutcome
    from weft.core.tasks.sessions import AgentSession, CommandSession
    from weft.core.taskspec import AgentSection


@dataclass(frozen=True, slots=True)
class RunnerHandle:
    """Opaque runtime handle persisted for task control and observability.

    Spec: docs/specifications/01-Core_Components.md [CC-3.2]
    """

    runner: str
    kind: Literal["process", "container", "sandboxed-process", "supervised-process"]
    id: str
    control: Mapping[str, Any] = field(default_factory=dict)
    observations: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        runner = self.runner.strip()
        runtime_id = self.id.strip()
        if not runner:
            raise ValueError("runner must be non-empty")
        if self.kind not in {
            "process",
            "container",
            "sandboxed-process",
            "supervised-process",
        }:
            raise ValueError(f"unsupported runtime kind {self.kind!r}")
        if not runtime_id:
            raise ValueError("id must be non-empty")
        if not isinstance(self.control, Mapping):
            raise ValueError("runner handle control must be a mapping")
        control = dict(self.control)
        authority = control.get("authority")
        if not isinstance(authority, str) or not authority.strip():
            raise ValueError("runner handle control.authority must be non-empty")
        control["authority"] = authority.strip()
        if not isinstance(self.observations, Mapping):
            raise ValueError("runner handle observations must be a mapping")
        observations = dict(self.observations)
        host_pids = observations.get("host_pids")
        if host_pids is not None:
            if not isinstance(host_pids, Sequence) or isinstance(
                host_pids, (str, bytes)
            ):
                raise ValueError(
                    "runner handle observations.host_pids must be a sequence"
                )
            observations["host_pids"] = sorted(
                {int(pid) for pid in host_pids if isinstance(pid, int) and pid > 0}
            )
        metadata = dict(self.metadata)
        object.__setattr__(self, "runner", runner)
        object.__setattr__(self, "id", runtime_id)
        object.__setattr__(self, "control", MappingProxyType(control))
        object.__setattr__(self, "observations", MappingProxyType(observations))
        object.__setattr__(self, "metadata", MappingProxyType(metadata))

    def scoped_host_pids(self) -> tuple[int, ...]:
        """Return host-scoped PIDs observed for this runtime."""
        host_pids = self.observations.get("host_pids")
        if not isinstance(host_pids, Sequence) or isinstance(host_pids, (str, bytes)):
            return ()
        return tuple(pid for pid in host_pids if isinstance(pid, int) and pid > 0)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation for queue payloads."""
        return {
            "runner": self.runner,
            "kind": self.kind,
            "id": self.id,
            "control": dict(self.control),
            "observations": dict(self.observations),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RunnerHandle:
        """Build a handle from persisted mapping payload data."""
        legacy_keys = {"runner_name", "runtime_id", "host_pids"}.intersection(payload)
        if legacy_keys:
            raise ValueError(
                "runner handle uses legacy keys: " + ", ".join(sorted(legacy_keys))
            )
        runner = payload.get("runner")
        kind = payload.get("kind")
        runtime_id = payload.get("id")
        control = payload.get("control") or {}
        observations = payload.get("observations") or {}
        metadata = payload.get("metadata") or {}
        if (
            not isinstance(runner, str)
            or not isinstance(kind, str)
            or not isinstance(runtime_id, str)
        ):
            raise ValueError("runner handle requires string runner, kind, and id")
        if not isinstance(control, Mapping):
            raise ValueError("runner handle control must be a mapping")
        if not isinstance(observations, Mapping):
            raise ValueError("runner handle observations must be a mapping")
        if not isinstance(metadata, Mapping):
            raise ValueError("runner handle metadata must be a mapping")
        return cls(
            runner=runner,
            kind=kind,  # type: ignore[arg-type]
            id=runtime_id,
            control=dict(control),
            observations=dict(observations),
            metadata=dict(metadata),
        )


@dataclass(frozen=True, slots=True)
class RunnerCapabilities:
    """Capability flags exposed by a runner plugin."""

    supported_types: tuple[str, ...] = ("function", "command", "agent")
    supports_interactive: bool = True
    supports_persistent: bool = True
    supports_agent_sessions: bool = True


@dataclass(frozen=True, slots=True)
class RunnerRuntimeDescription:
    """Inspectable runtime metadata for status and observability surfaces."""

    runner: str
    id: str
    state: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runner": self.runner,
            "id": self.id,
            "state": self.state,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class AgentResolverResult:
    """Resolver output for delegated agent runtimes.

    Spec: docs/specifications/13-Agent_Runtime.md [AR-5]
    """

    prompt: str
    instructions: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        prompt = self.prompt.strip()
        if not prompt:
            raise ValueError("prompt must be non-empty")
        metadata = MappingProxyType(dict(self.metadata))
        artifacts = tuple(MappingProxyType(dict(item)) for item in self.artifacts)
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "artifacts", artifacts)


@dataclass(frozen=True, slots=True)
class AgentToolProfileResult:
    """Tool-profile output for delegated agent runtimes.

    Spec: docs/specifications/13-Agent_Runtime.md [AR-5]
    """

    instructions: str | None = None
    provider_options: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    workspace_access: str | None = None
    mcp_servers: tuple[AgentMCPServerDescriptor, ...] = ()

    def __post_init__(self) -> None:
        workspace_access = self.workspace_access
        if workspace_access is not None and workspace_access not in {
            "none",
            "read-only",
            "workspace-write",
        }:
            raise ValueError(
                "workspace_access must be one of "
                "'none', 'read-only', or 'workspace-write'"
            )
        object.__setattr__(
            self, "provider_options", MappingProxyType(dict(self.provider_options))
        )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "workspace_access", workspace_access)
        object.__setattr__(self, "mcp_servers", tuple(self.mcp_servers))


@dataclass(frozen=True, slots=True)
class AgentMCPServerDescriptor:
    """Structured stdio MCP server descriptor for delegated runtimes.

    Spec: docs/specifications/13-Agent_Runtime.md [AR-5]
    """

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        command = self.command.strip()
        if not name:
            raise ValueError("MCP server name must be non-empty")
        if not command:
            raise ValueError("MCP server command must be non-empty")
        args = tuple(str(arg) for arg in self.args)
        env = {str(key): str(value) for key, value in dict(self.env).items()}
        cwd = (
            self.cwd.strip() if isinstance(self.cwd, str) and self.cwd.strip() else None
        )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "args", args)
        object.__setattr__(self, "env", MappingProxyType(env))
        object.__setattr__(self, "cwd", cwd)

    def to_claude_config(self) -> dict[str, Any]:
        """Return the standardized stdio MCP config payload."""
        return {
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
        }


@dataclass(frozen=True, slots=True)
class RunnerEnvironmentProfileResult:
    """Environment-profile output for runner-scoped execution.

    Spec: docs/specifications/02-TaskSpec.md [TS-1.3]
    """

    runner_options: Mapping[str, Any] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    working_dir: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "runner_options",
            MappingProxyType(dict(self.runner_options)),
        )
        object.__setattr__(
            self,
            "env",
            MappingProxyType(
                {str(key): str(value) for key, value in dict(self.env).items()}
            ),
        )
        working_dir = self.working_dir
        if working_dir is not None:
            working_dir = working_dir.strip()
            if not working_dir:
                raise ValueError("working_dir must be a non-empty string when provided")
        object.__setattr__(self, "working_dir", working_dir)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class AgentResolver(Protocol):
    """Public callable contract for delegated-runtime resolvers."""

    def __call__(
        self,
        *,
        agent: AgentSection,
        work_item: NormalizedAgentWorkItem,
        tid: str | None,
    ) -> AgentResolverResult: ...


class AgentToolProfile(Protocol):
    """Public callable contract for delegated-runtime tool profiles."""

    def __call__(
        self,
        *,
        agent: AgentSection,
        tid: str | None,
    ) -> AgentToolProfileResult: ...


class RunnerEnvironmentProfile(Protocol):
    """Public callable contract for runner environment profiles."""

    def __call__(
        self,
        *,
        target_type: str,
        runner_name: str,
        runner_options: Mapping[str, Any],
        env: Mapping[str, str],
        working_dir: str | None,
        tid: str | None,
    ) -> RunnerEnvironmentProfileResult: ...


@runtime_checkable
class TaskRunnerBackend(Protocol):
    """Execution backend produced by a runner plugin."""

    def run(self, work_item: Any) -> RunnerOutcome: ...

    def run_with_hooks(
        self,
        work_item: Any,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_worker_started: Callable[[int | None], None] | None = None,
        on_runtime_handle_started: Callable[[RunnerHandle], None] | None = None,
        on_stdout_chunk: Callable[[str, bool], None] | None = None,
        on_stderr_chunk: Callable[[str, bool], None] | None = None,
    ) -> RunnerOutcome: ...

    def start_session(self) -> CommandSession: ...

    def start_agent_session(self) -> AgentSession: ...


class RunnerPlugin(Protocol):
    """Public contract for runner plugins.

    Spec: docs/specifications/01-Core_Components.md [CC-3.1]
    """

    name: str
    capabilities: RunnerCapabilities

    def check_version(self) -> None: ...

    def validate_taskspec(
        self,
        taskspec_payload: Mapping[str, Any],
        *,
        preflight: bool = False,
    ) -> None: ...

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
    ) -> TaskRunnerBackend: ...

    def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool: ...

    def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool: ...

    def describe(self, handle: RunnerHandle) -> RunnerRuntimeDescription | None: ...
