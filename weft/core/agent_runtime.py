"""Backend-neutral agent runtime surface.

This module normalizes agent work envelopes, resolves configured tools, and
dispatches execution to a registered runtime adapter. It intentionally stays
outside queue semantics.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-2.2], [AR-4.1]
- docs/specifications/02-TaskSpec.md [TS-1]
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from weft.core.agent_templates import render_agent_template
from weft.core.agent_tools import ResolvedAgentTool, resolve_agent_tools
from weft.core.taskspec import AgentSection


@dataclass(frozen=True, slots=True)
class NormalizedAgentMessage:
    """Structured message preserved at the core agent boundary."""

    role: str
    content: Any


@dataclass(frozen=True, slots=True)
class NormalizedAgentWorkItem:
    """Normalized one-shot agent work request."""

    content: str | tuple[str | NormalizedAgentMessage, ...]
    instructions: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_allow: tuple[str, ...] | None = None
    tool_deny: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class AgentExecutionResult:
    """Internal result returned by agent runtime adapters."""

    runtime: str
    model: str | None
    output_mode: str
    outputs: tuple[Any, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "completed"
    usage: dict[str, Any] | None = None
    tool_trace: tuple[dict[str, Any], ...] = ()
    artifacts: tuple[dict[str, Any], ...] = ()

    def aggregate_public_output(self) -> Any:
        """Return the caller-facing result shape for this execution."""
        if not self.outputs:
            return None
        if len(self.outputs) == 1:
            return self.outputs[0]
        return list(self.outputs)

    def to_private_payload(self) -> dict[str, Any]:
        """Return a JSON-friendly payload for the private session protocol."""
        payload: dict[str, Any] = {
            "runtime": self.runtime,
            "model": self.model,
            "output_mode": self.output_mode,
            "outputs": list(self.outputs),
            "metadata": self.metadata,
            "status": self.status,
            "tool_trace": list(self.tool_trace),
            "artifacts": list(self.artifacts),
        }
        if self.usage is not None:
            payload["usage"] = self.usage
        return payload

    @classmethod
    def from_private_payload(cls, payload: Mapping[str, Any]) -> AgentExecutionResult:
        """Build an execution result from a private session payload."""
        outputs = payload.get("outputs")
        if isinstance(outputs, Sequence) and not isinstance(outputs, (str, bytes)):
            normalized_outputs = tuple(outputs)
        else:
            raise TypeError("agent session result payload must include list outputs")

        metadata = payload.get("metadata")
        if not isinstance(metadata, Mapping):
            raise TypeError("agent session result payload metadata must be an object")

        tool_trace = payload.get("tool_trace", ())
        if isinstance(tool_trace, Sequence) and not isinstance(
            tool_trace, (str, bytes)
        ):
            normalized_tool_trace = tuple(
                item for item in tool_trace if isinstance(item, Mapping)
            )
        else:
            normalized_tool_trace = ()

        artifacts = payload.get("artifacts", ())
        if isinstance(artifacts, Sequence) and not isinstance(artifacts, (str, bytes)):
            normalized_artifacts = tuple(
                item for item in artifacts if isinstance(item, Mapping)
            )
        else:
            normalized_artifacts = ()

        usage = payload.get("usage")
        if usage is not None and not isinstance(usage, Mapping):
            raise TypeError("agent session result payload usage must be an object")

        runtime = payload.get("runtime")
        if not isinstance(runtime, str) or not runtime:
            raise TypeError("agent session result payload runtime must be a string")

        model = payload.get("model")
        if model is not None and not isinstance(model, str):
            raise TypeError("agent session result payload model must be a string")

        output_mode = payload.get("output_mode")
        if not isinstance(output_mode, str) or not output_mode:
            raise TypeError("agent session result payload output_mode must be a string")

        status = payload.get("status", "completed")
        if not isinstance(status, str) or not status:
            raise TypeError("agent session result payload status must be a string")

        return cls(
            runtime=runtime,
            model=model,
            output_mode=output_mode,
            outputs=normalized_outputs,
            metadata=dict(metadata),
            status=status,
            usage=dict(usage) if isinstance(usage, Mapping) else None,
            tool_trace=tuple(dict(item) for item in normalized_tool_trace),
            artifacts=tuple(dict(item) for item in normalized_artifacts),
        )


class AgentRuntimeAdapter(Protocol):
    """Protocol implemented by concrete agent runtime backends."""

    def execute(
        self,
        *,
        agent: AgentSection,
        work_item: NormalizedAgentWorkItem,
        tools: Sequence[ResolvedAgentTool],
        tid: str | None,
    ) -> AgentExecutionResult:
        """Execute a normalized agent work item."""


class AgentRuntimeSession(Protocol):
    """Protocol implemented by long-lived runtime sessions."""

    def execute(self, work_item: NormalizedAgentWorkItem) -> AgentExecutionResult:
        """Execute one normalized work item against the live session."""

    def close(self) -> None:
        """Release any session-scoped resources."""


_RUNTIME_REGISTRY: dict[str, AgentRuntimeAdapter] = {}


def register_agent_runtime(name: str, adapter: AgentRuntimeAdapter) -> None:
    """Register a runtime adapter under a stable name."""
    if not name:
        raise ValueError("runtime name must be non-empty")
    if name in _RUNTIME_REGISTRY:
        raise ValueError(f"Agent runtime already registered: {name}")
    _RUNTIME_REGISTRY[name] = adapter


def get_agent_runtime(name: str) -> AgentRuntimeAdapter:
    """Resolve a previously registered runtime adapter."""
    try:
        return _RUNTIME_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"Unknown agent runtime: {name}") from exc


def clear_agent_runtime_registry() -> None:
    """Clear registered runtimes. Intended for isolated tests."""
    _RUNTIME_REGISTRY.clear()


def normalize_agent_work_item(
    agent: AgentSection,
    work_item: Any,
) -> NormalizedAgentWorkItem:
    """Normalize supported work envelope shapes to a stable request object."""
    if isinstance(work_item, str):
        return NormalizedAgentWorkItem(
            content=work_item,
            instructions=agent.instructions,
        )

    if not isinstance(work_item, Mapping):
        raise TypeError("Agent work item must be a string or JSON object")

    supported_keys = {
        "task",
        "messages",
        "template",
        "template_args",
        "metadata",
        "tool_overrides",
    }
    extra_keys = sorted(set(work_item) - supported_keys)
    if extra_keys:
        raise ValueError(
            f"Unsupported agent work item field(s): {', '.join(extra_keys)}"
        )

    metadata = _normalize_metadata(work_item.get("metadata"))
    tool_allow, tool_deny = _normalize_tool_overrides(work_item.get("tool_overrides"))
    content, instructions = _normalize_content_and_instructions(agent, work_item)

    return NormalizedAgentWorkItem(
        content=content,
        instructions=instructions,
        metadata=metadata,
        tool_allow=tool_allow,
        tool_deny=tool_deny,
    )


def execute_agent_target(
    agent: AgentSection,
    work_item: Any,
    *,
    tid: str | None = None,
) -> AgentExecutionResult:
    """Execute agent work against the registered runtime backend."""
    normalized = normalize_agent_work_item(agent, work_item)
    tools = resolve_agent_tools(
        agent.tools,
        allow=normalized.tool_allow,
        deny=normalized.tool_deny,
    )
    runtime = get_agent_runtime(agent.runtime)
    return runtime.execute(
        agent=agent,
        work_item=normalized,
        tools=tools,
        tid=tid,
    )


def start_agent_runtime_session(
    agent: AgentSection,
    *,
    tid: str | None = None,
) -> AgentRuntimeSession:
    """Start a long-lived runtime session for a persistent agent task."""
    runtime = get_agent_runtime(agent.runtime)
    start_session = getattr(runtime, "start_session", None)
    if not callable(start_session):
        raise ValueError(
            f"Runtime does not support persistent sessions: {agent.runtime}"
        )
    return cast(AgentRuntimeSession, start_session(agent=agent, tid=tid))


def _normalize_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("Agent work item field 'metadata' must be an object")
    return dict(value)


def _normalize_tool_overrides(
    value: Any,
) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    if value is None:
        return None, None
    if not isinstance(value, Mapping):
        raise TypeError("Agent work item field 'tool_overrides' must be an object")

    extra_keys = sorted(set(value) - {"allow", "deny"})
    if extra_keys:
        raise ValueError(
            "Unsupported tool_overrides field(s): " + ", ".join(extra_keys)
        )

    allow = _normalize_name_list("tool_overrides.allow", value.get("allow"))
    deny = _normalize_name_list("tool_overrides.deny", value.get("deny"))
    return allow, deny


def _normalize_content_and_instructions(
    agent: AgentSection,
    work_item: Mapping[str, Any],
) -> tuple[str | tuple[str | NormalizedAgentMessage, ...], str | None]:
    present_keys = [
        key
        for key in ("task", "messages", "template")
        if work_item.get(key) is not None
    ]
    if len(present_keys) != 1:
        raise ValueError(
            "Agent work item must include exactly one of 'task', 'messages', or 'template'"
        )

    if "template_args" in work_item and work_item.get("template") is None:
        raise ValueError(
            "Agent work item field 'template_args' requires a 'template' field"
        )

    if "task" in present_keys:
        task = work_item.get("task")
        if not isinstance(task, str):
            raise TypeError("Agent work item field 'task' must be a string")
        return task, agent.instructions

    if "messages" in present_keys:
        return _normalize_messages(work_item.get("messages")), agent.instructions

    template_name = work_item.get("template")
    if not isinstance(template_name, str) or not template_name:
        raise TypeError("Agent work item field 'template' must be a string")
    try:
        template = agent.templates[template_name]
    except KeyError as exc:
        raise ValueError(f"Unknown agent template: {template_name}") from exc

    template_args = work_item.get("template_args")
    if template_args is not None and not isinstance(template_args, Mapping):
        raise TypeError("Agent work item field 'template_args' must be an object")

    rendered = render_agent_template(template, template_args)
    instructions = (
        template.instructions
        if template.instructions is not None
        else agent.instructions
    )
    return rendered, instructions


def _normalize_name_list(label: str, value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise TypeError(f"Agent work item field '{label}' must be a list of strings")
    names = tuple(value)
    if not all(isinstance(item, str) and item for item in names):
        raise TypeError(f"Agent work item field '{label}' must be a list of strings")
    return names


def _normalize_messages(messages: Any) -> tuple[str | NormalizedAgentMessage, ...]:
    if isinstance(messages, str) or not isinstance(messages, Sequence):
        raise TypeError("Agent work item field 'messages' must be a list")

    normalized: list[str | NormalizedAgentMessage] = []
    for item in messages:
        if isinstance(item, str):
            normalized.append(item)
            continue
        if not isinstance(item, Mapping):
            raise TypeError(
                "Agent message items must be strings or objects with role/content"
            )

        extra_keys = sorted(set(item) - {"role", "content"})
        if extra_keys:
            raise ValueError("Unsupported message field(s): " + ", ".join(extra_keys))

        role = item.get("role")
        if not isinstance(role, str) or not role:
            raise TypeError("Agent message field 'role' must be a string")
        if "content" not in item:
            raise TypeError("Agent message object must include 'content'")
        normalized.append(
            NormalizedAgentMessage(
                role=role,
                content=item.get("content"),
            )
        )

    return tuple(normalized)


__all__ = [
    "AgentExecutionResult",
    "AgentRuntimeAdapter",
    "AgentRuntimeSession",
    "NormalizedAgentMessage",
    "NormalizedAgentWorkItem",
    "clear_agent_runtime_registry",
    "execute_agent_target",
    "get_agent_runtime",
    "normalize_agent_work_item",
    "register_agent_runtime",
    "start_agent_runtime_session",
]
