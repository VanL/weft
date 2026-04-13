"""Public runner plugin contracts for Weft extensions.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.1], [CC-3.2]
- docs/specifications/02-TaskSpec.md [TS-1.3]
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from simplebroker import BrokerTarget
    from weft.core.runners.host import RunnerOutcome
    from weft.core.tasks.sessions import AgentSession, CommandSession


@dataclass(frozen=True, slots=True)
class RunnerHandle:
    """Opaque runtime handle persisted for task control and observability.

    Spec: docs/specifications/01-Core_Components.md [CC-3.2]
    """

    runner_name: str
    runtime_id: str
    host_pids: tuple[int, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        runner_name = self.runner_name.strip()
        runtime_id = self.runtime_id.strip()
        if not runner_name:
            raise ValueError("runner_name must be non-empty")
        if not runtime_id:
            raise ValueError("runtime_id must be non-empty")
        normalized_pids = tuple(
            sorted(
                {int(pid) for pid in self.host_pids if isinstance(pid, int) and pid > 0}
            )
        )
        metadata = dict(self.metadata)
        object.__setattr__(self, "runner_name", runner_name)
        object.__setattr__(self, "runtime_id", runtime_id)
        object.__setattr__(self, "host_pids", normalized_pids)
        object.__setattr__(self, "metadata", MappingProxyType(metadata))

    @property
    def primary_pid(self) -> int | None:
        return self.host_pids[0] if self.host_pids else None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation for queue payloads."""
        return {
            "runner_name": self.runner_name,
            "runtime_id": self.runtime_id,
            "host_pids": list(self.host_pids),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RunnerHandle:
        """Build a handle from persisted mapping payload data."""
        runner_name = payload.get("runner_name")
        runtime_id = payload.get("runtime_id")
        host_pids = payload.get("host_pids") or ()
        metadata = payload.get("metadata") or {}
        if not isinstance(runner_name, str) or not isinstance(runtime_id, str):
            raise ValueError("runner handle requires string runner_name and runtime_id")
        if not isinstance(host_pids, Sequence) or isinstance(host_pids, (str, bytes)):
            raise ValueError("runner handle host_pids must be a sequence")
        if not isinstance(metadata, Mapping):
            raise ValueError("runner handle metadata must be a mapping")
        return cls(
            runner_name=runner_name,
            runtime_id=runtime_id,
            host_pids=tuple(int(pid) for pid in host_pids if isinstance(pid, int)),
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

    runner_name: str
    runtime_id: str
    state: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runner_name": self.runner_name,
            "runtime_id": self.runtime_id,
            "state": self.state,
            "metadata": dict(self.metadata),
        }


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
        persistent: bool,
        interactive: bool,
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> TaskRunnerBackend: ...

    def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool: ...

    def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool: ...

    def describe(self, handle: RunnerHandle) -> RunnerRuntimeDescription | None: ...
