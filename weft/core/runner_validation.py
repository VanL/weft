"""Shared validation helpers for runner-backed TaskSpecs.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.3]
- docs/specifications/02-TaskSpec.md [TS-1], [TS-1.3]
- docs/specifications/10-CLI_Interface.md [CLI-1.4.1]
- docs/specifications/06-Resource_Management.md [RM-5]
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from weft._runner_plugins import require_runner_plugin
from weft.ext import RunnerPlugin


def validate_taskspec_runner(
    taskspec_payload: Mapping[str, Any],
    *,
    load_runner: bool = False,
    preflight: bool = False,
) -> None:
    """Validate the configured runner for a TaskSpec payload.

    Args:
        taskspec_payload: Parsed TaskSpec payload.
        load_runner: Require that the referenced runner plugin can be resolved.
        preflight: Run environment/runtime availability checks.
    """

    if preflight:
        load_runner = True
    if not load_runner:
        return

    plugin = require_runner_plugin(runner_name_from_taskspec(taskspec_payload))
    plugin.check_version()
    validate_runner_capabilities(plugin, taskspec_payload)
    plugin.validate_taskspec(taskspec_payload, preflight=False)
    if preflight:
        plugin.validate_taskspec(taskspec_payload, preflight=True)


def validate_runner_capabilities(
    plugin: RunnerPlugin,
    taskspec_payload: Mapping[str, Any],
) -> None:
    """Validate that a runner plugin supports the TaskSpec execution shape."""

    spec = _require_mapping(taskspec_payload.get("spec"), name="spec")
    target_type = _require_text(spec.get("type"), name="spec.type")
    persistent = bool(spec.get("persistent", False))
    interactive = bool(spec.get("interactive", False))
    agent = spec.get("agent")
    requires_agent_session = False
    if isinstance(agent, Mapping):
        requires_agent_session = (
            target_type == "agent"
            and persistent
            and agent.get("conversation_scope") == "per_task"
        )

    capabilities = plugin.capabilities
    if target_type not in capabilities.supported_types:
        raise ValueError(
            f"Runner '{plugin.name}' does not support task type '{target_type}'"
        )
    if interactive and not capabilities.supports_interactive:
        raise ValueError(f"Runner '{plugin.name}' does not support interactive tasks")
    if persistent and not capabilities.supports_persistent:
        raise ValueError(f"Runner '{plugin.name}' does not support persistent tasks")
    if requires_agent_session and not capabilities.supports_agent_sessions:
        raise ValueError(f"Runner '{plugin.name}' does not support agent sessions")


def runner_name_from_taskspec(taskspec_payload: Mapping[str, Any]) -> str:
    """Return the normalized runner name from a TaskSpec payload."""

    spec = _require_mapping(taskspec_payload.get("spec"), name="spec")
    runner = spec.get("runner")
    if runner is None:
        return "host"
    runner_mapping = _require_mapping(runner, name="spec.runner")
    name = runner_mapping.get("name", "host")
    return _require_text(name, name="spec.runner.name")


def _require_mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must be a non-empty string")
    return cleaned
