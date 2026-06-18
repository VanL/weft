"""Option parsing for the Microsandbox runner.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.1], [CC-3.3]
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/06-Resource_Management.md [RM-5]
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from weft.core.agents.provider_cli.execution import runtime_config_str
from weft.core.taskspec import AgentSection

MicrosandboxMode = Literal["tool", "agent"]
MicrosandboxNetwork = Literal["none", "allow"]
WorkspaceMode = Literal[
    "none",
    "copy",
    "mount-read-only",
    "mount-read-write",
]
_microsandbox_runner_option_keys: frozenset[str] = frozenset(
    {
        "mode",
        "image",
        "executable",
        "network",
        "workspace_mode",
        "mounts",
        "cwd",
        "sandbox_name_prefix",
    }
)


@dataclass(frozen=True, slots=True)
class MicrosandboxMount:
    """Host path mounted into the guest."""

    source: str
    target: str
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class MicrosandboxOptions:
    """Normalized Microsandbox runner options."""

    mode: MicrosandboxMode
    image: str
    executable: str | None
    network: MicrosandboxNetwork
    workspace_mode: WorkspaceMode
    mounts: tuple[MicrosandboxMount, ...]
    cwd: str
    sandbox_name_prefix: str | None
    env: Mapping[str, str]
    working_dir: str | None
    memory_mb: int | None
    cpus: float | None
    max_fds: int | None

    @property
    def uses_workspace_copy(self) -> bool:
        return self.workspace_mode == "copy"

    @property
    def uses_workspace_mount(self) -> bool:
        return self.workspace_mode in {"mount-read-only", "mount-read-write"}

    @property
    def workspace_read_only(self) -> bool:
        return self.workspace_mode == "mount-read-only"


def parse_options_from_payload(
    taskspec_payload: Mapping[str, Any],
) -> MicrosandboxOptions:
    """Normalize Microsandbox options from a validation payload."""

    spec = _require_mapping(taskspec_payload.get("spec"), name="spec")
    target_type = _required_text(spec.get("type"), name="spec.type")
    runner = _require_mapping(spec.get("runner"), name="spec.runner")
    runner_options = _require_mapping(
        runner.get("options"),
        name="spec.runner.options",
    )
    agent_payload = (
        _require_mapping(spec.get("agent"), name="spec.agent")
        if target_type == "agent"
        else None
    )
    return parse_options(
        target_type=target_type,
        agent=agent_payload,
        runner_options=runner_options,
        env=_mapping_of_strings(spec.get("env") or {}, name="spec.env"),
        working_dir=_optional_text(spec.get("working_dir"), name="spec.working_dir"),
        limits=spec.get("limits"),
        persistent=bool(spec.get("persistent", False)),
        interactive=bool(spec.get("interactive", False)),
    )


def parse_options(
    *,
    target_type: str,
    agent: Mapping[str, Any] | None,
    runner_options: Mapping[str, Any] | None,
    env: Mapping[str, str] | None,
    working_dir: str | None,
    limits: Any | None,
    persistent: bool,
    interactive: bool,
) -> MicrosandboxOptions:
    """Normalize Microsandbox runner options."""

    options = dict(runner_options or {})
    extra_keys = sorted(set(options) - _microsandbox_runner_option_keys)
    if extra_keys:
        raise ValueError(
            "Microsandbox runner option(s) are unsupported: "
            + ", ".join(f"spec.runner.options.{key}" for key in extra_keys)
        )

    if interactive:
        raise ValueError("Microsandbox runner does not support interactive tasks")
    if persistent:
        raise ValueError("Microsandbox runner does not support persistent tasks")

    mode = _normalize_mode(options.get("mode"), target_type=target_type)
    image = _required_text(options.get("image"), name="spec.runner.options.image")
    executable = _optional_text(
        options.get("executable"),
        name="spec.runner.options.executable",
    )
    if mode == "tool" and target_type != "command":
        raise ValueError("Microsandbox mode='tool' requires spec.type='command'")
    if mode == "agent":
        if target_type != "agent":
            raise ValueError("Microsandbox mode='agent' requires spec.type='agent'")
        if executable is None:
            raise ValueError(
                "Microsandbox agent mode requires spec.runner.options.executable"
            )
        _validate_agent(agent)
    elif target_type != "command":
        raise ValueError(
            "Microsandbox runner supports only command tasks and one-shot "
            "provider_cli agent tasks"
        )

    max_connections = _limit_value(limits, "max_connections")
    if max_connections not in (None, 0, 0.0):
        raise ValueError(
            "Microsandbox runner supports spec.limits.max_connections only when "
            "it is 0 (mapped to network isolation)"
        )
    network = _normalize_network(options.get("network"))
    if network == "allow" and max_connections == 0:
        raise ValueError(
            "spec.runner.options.network='allow' conflicts with "
            "spec.limits.max_connections=0"
        )

    workspace_mode = _normalize_workspace_mode(options.get("workspace_mode"))
    cwd = _optional_text(options.get("cwd"), name="spec.runner.options.cwd") or "/"
    if workspace_mode != "none":
        if not working_dir:
            raise ValueError("Microsandbox workspace modes require spec.working_dir")
        if cwd == "/":
            raise ValueError(
                "Microsandbox workspace modes require spec.runner.options.cwd"
            )

    return MicrosandboxOptions(
        mode=mode,
        image=image,
        executable=executable,
        network=network,
        workspace_mode=workspace_mode,
        mounts=_normalize_mounts(options.get("mounts")),
        cwd=cwd,
        sandbox_name_prefix=_optional_text(
            options.get("sandbox_name_prefix"),
            name="spec.runner.options.sandbox_name_prefix",
        ),
        env={str(key): str(value) for key, value in dict(env or {}).items()},
        working_dir=working_dir,
        memory_mb=_limit_int(limits, "memory_mb"),
        cpus=_cpu_count_from_percent(_limit_int(limits, "cpu_percent")),
        max_fds=_limit_int(limits, "max_fds"),
    )


def _validate_agent(agent_payload: Mapping[str, Any] | None) -> None:
    if agent_payload is None:
        raise ValueError("Microsandbox agent mode requires spec.agent")
    agent = AgentSection.model_validate(agent_payload)
    if agent.runtime != "provider_cli":
        raise ValueError(
            "Microsandbox agent mode currently supports only "
            "spec.agent.runtime='provider_cli'"
        )
    if agent.conversation_scope != "per_message":
        raise ValueError(
            "Microsandbox agent mode requires "
            "spec.agent.conversation_scope='per_message'"
        )
    runtime_config_str(agent, "provider")


def _normalize_mode(value: object, *, target_type: str) -> MicrosandboxMode:
    if value is None:
        if target_type == "command":
            return "tool"
        if target_type == "agent":
            return "agent"
        raise ValueError(
            "Microsandbox runner supports only spec.type='command' and "
            "spec.type='agent'"
        )
    if not isinstance(value, str):
        raise ValueError("spec.runner.options.mode must be 'tool' or 'agent'")
    normalized = value.strip()
    if normalized not in {"tool", "agent"}:
        raise ValueError("spec.runner.options.mode must be 'tool' or 'agent'")
    return normalized  # type: ignore[return-value]


def _normalize_network(value: object) -> MicrosandboxNetwork:
    if value is None:
        return "none"
    if not isinstance(value, str):
        raise ValueError("spec.runner.options.network must be 'none' or 'allow'")
    normalized = value.strip()
    if normalized not in {"none", "allow"}:
        raise ValueError("spec.runner.options.network must be 'none' or 'allow'")
    return normalized  # type: ignore[return-value]


def _normalize_workspace_mode(value: object) -> WorkspaceMode:
    if value is None:
        return "none"
    if not isinstance(value, str):
        raise ValueError("spec.runner.options.workspace_mode must be a string")
    normalized = value.strip()
    if normalized not in {
        "none",
        "copy",
        "mount-read-only",
        "mount-read-write",
    }:
        raise ValueError(
            "spec.runner.options.workspace_mode must be one of 'none', 'copy', "
            "'mount-read-only', or 'mount-read-write'"
        )
    return normalized  # type: ignore[return-value]


def _normalize_mounts(value: object) -> tuple[MicrosandboxMount, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("spec.runner.options.mounts must be a list of mount objects")
    mounts: list[MicrosandboxMount] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"spec.runner.options.mounts[{index}] must be an object")
        source = _required_text(
            item.get("source"),
            name=f"spec.runner.options.mounts[{index}].source",
        )
        target = _required_text(
            item.get("target"),
            name=f"spec.runner.options.mounts[{index}].target",
        )
        read_only = item.get("read_only", True)
        if not isinstance(read_only, bool):
            raise ValueError(
                f"spec.runner.options.mounts[{index}].read_only must be a boolean"
            )
        mounts.append(
            MicrosandboxMount(
                source=str(Path(source).expanduser().resolve()),
                target=target,
                read_only=read_only,
            )
        )
    return tuple(mounts)


def _mapping_of_strings(value: object, *, name: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return {str(key): str(item) for key, item in value.items()}


def _require_mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _required_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name=name)


def _limit_value(limits: Any | None, name: str) -> Any | None:
    if limits is None:
        return None
    if isinstance(limits, Mapping):
        return limits.get(name)
    return getattr(limits, name, None)


def _limit_int(limits: Any | None, name: str) -> int | None:
    value = _limit_value(limits, name)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _cpu_count_from_percent(cpu_percent: int | None) -> float | None:
    if cpu_percent is None:
        return None
    host_cpus = max(os.cpu_count() or 1, 1)
    return max((cpu_percent / 100.0) * host_cpus, 0.01)


__all__ = [
    "MicrosandboxMount",
    "MicrosandboxNetwork",
    "MicrosandboxOptions",
    "MicrosandboxMode",
    "WorkspaceMode",
    "parse_options",
    "parse_options_from_payload",
]
