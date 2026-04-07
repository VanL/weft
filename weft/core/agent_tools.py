"""Agent tool resolution helpers.

This module resolves Weft-level agent tool descriptors into backend-neutral
callable tool objects. It intentionally knows nothing about queues or
backend-specific tool classes.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-2.2]
- docs/specifications/02-TaskSpec.md [TS-1]
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import BaseModel, create_model

from weft.core.taskspec import AgentToolSection


@dataclass(frozen=True, slots=True)
class ResolvedAgentTool:
    """Backend-neutral resolved agent tool."""

    name: str
    ref: str
    description: str | None
    input_schema: dict[str, Any]
    implementation: Callable[..., Any]
    approval_required: bool = False
    config: dict[str, Any] = field(default_factory=dict)


def resolve_agent_tools(
    tools: Sequence[AgentToolSection],
    *,
    allow: Sequence[str] | None = None,
    deny: Sequence[str] | None = None,
) -> list[ResolvedAgentTool]:
    """Resolve configured tools and apply per-work-item allow/deny overrides.

    Spec: [AR-2.2], [AR-3.1]
    """
    tools_by_name = {tool.name: tool for tool in tools}
    _validate_override_names("allow", allow, tools_by_name)
    _validate_override_names("deny", deny, tools_by_name)

    allowed_names = set(allow) if allow is not None else set(tools_by_name)
    denied_names = set(deny or ())
    resolved: list[ResolvedAgentTool] = []

    for tool in tools:
        if tool.name not in allowed_names:
            continue
        if tool.name in denied_names:
            continue
        resolved.append(resolve_agent_tool(tool))
    return resolved


def resolve_agent_tool(tool: AgentToolSection) -> ResolvedAgentTool:
    """Resolve a single tool descriptor to a callable tool object."""
    if tool.kind != "python":
        raise ValueError(f"Unsupported agent tool kind: {tool.kind}")

    implementation = _import_tool_callable(tool.ref)
    description = tool.description or inspect.getdoc(implementation)
    input_schema = tool.args_schema or _build_input_schema(implementation)
    return ResolvedAgentTool(
        name=tool.name,
        ref=tool.ref,
        description=description,
        input_schema=input_schema,
        implementation=implementation,
        approval_required=tool.approval_required,
        config=dict(tool.config),
    )


def _validate_override_names(
    label: str,
    names: Sequence[str] | None,
    tools_by_name: dict[str, AgentToolSection],
) -> None:
    if names is None:
        return
    unknown = [name for name in names if name not in tools_by_name]
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown tool name(s) in {label}: {joined}")


def _import_tool_callable(ref: str) -> Callable[..., Any]:
    try:
        module_name, func_name = ref.rsplit(":", 1)
    except ValueError as exc:  # pragma: no cover - input validation
        raise ValueError(
            f"Invalid python tool reference {ref!r}; expected 'module:function'"
        ) from exc

    module = importlib.import_module(module_name)
    implementation = cast(Callable[..., Any], getattr(module, func_name))
    if not callable(implementation):
        raise TypeError(f"Resolved tool reference is not callable: {ref}")
    return implementation


def _build_input_schema(implementation: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(implementation)
    fields: dict[str, tuple[Any, Any]] = {}

    for param in signature.parameters.values():
        if param.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            continue
        annotation = param.annotation
        if annotation is inspect._empty:
            annotation = Any
        default = param.default
        if default is inspect._empty:
            default = ...
        fields[param.name] = (annotation, default)

    model = create_model(  # type: ignore[call-overload]
        f"{implementation.__name__.title()}Input",
        __base__=BaseModel,
        **fields,
    )
    return cast(dict[str, Any], model.model_json_schema())


__all__ = ["ResolvedAgentTool", "resolve_agent_tool", "resolve_agent_tools"]
