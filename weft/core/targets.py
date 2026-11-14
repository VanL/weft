"""Shared helpers for executing TaskSpec targets.

The :class:`Consumer` task (and future task variants) need to execute either
Python callables or external processes based on the TaskSpec configuration.
This module centralizes the decoding logic so multiple task types can reuse the
same behaviour without duplicating code.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3], [CC-3], [CC-3.5]
- docs/specifications/02-TaskSpec.md [TS-1]
- docs/specifications/05-Message_Flow_and_State.md [MF-2]
- docs/specifications/12-Pipeline_Composition_and_UX.md [PL-4.1]
- docs/specifications/13-Agent_Runtime.md [AR-4], [AR-5]
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from weft._constants import WORK_ENVELOPE_START
from weft.core.agents.runtime import AgentExecutionResult
from weft.core.imports import import_callable_ref
from weft.core.outbox import is_command_output

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedOutput:
    """Exact public output text and its direct-call interpretation [MF-2]."""

    format: Literal["text", "json"]
    text: str


@dataclass(frozen=True, slots=True)
class PreparedTaskResult:
    """Application-free result prepared before private handoff [CC-3.5]."""

    kind: Literal["value", "agent"]
    outputs: tuple[PreparedOutput, ...]
    agent_execution: dict[str, Any] | None


def decode_work_message(message: str) -> Any:
    """Deserialize work, unwrapping exactly valid command output as raw text.

    Nonmatching objects remain ordinary input, including malformed transport.
    Spec: [CC-2.3], [MF-2], [PL-4.1].
    """
    if not message:
        return {}
    try:
        payload = json.loads(message)
        if payload == WORK_ENVELOPE_START:
            return {}
        if is_command_output(payload):
            return payload["result"]
        return payload
    except json.JSONDecodeError:
        if message == WORK_ENVELOPE_START:
            return {}
        return message


def prepare_call_arguments(
    spec_args: Sequence[Any] | None,
    spec_kwargs: Mapping[str, Any] | None,
    work_item: Any,
) -> tuple[list[Any], dict[str, Any]]:
    """Merge TaskSpec arguments with overrides supplied in the work item.

    Plain JSON objects are treated as the callable payload unless they use the
    explicit function-work envelope keys ``args``, ``kwargs``, or ``payload``.
    The empty mapping is structural and means "no payload" so kwargs-only
    function targets do not receive a synthetic positional ``{}``.

    Spec: [CC-3], [TS-1]
    """
    args = list(spec_args or [])
    kwargs = dict(spec_kwargs or {})

    if isinstance(work_item, dict):
        has_envelope_keys = any(
            key in work_item for key in ("args", "kwargs", "payload")
        )
        if "args" in work_item:
            args = list(work_item["args"])
        elif "payload" in work_item and not args:
            args = [work_item["payload"]]

        if "kwargs" in work_item and isinstance(work_item["kwargs"], dict):
            kwargs.update(work_item["kwargs"])

        if "payload" in work_item and "input" not in kwargs and not args:
            args = [work_item["payload"]]
        elif not has_envelope_keys and not args and work_item:
            args = [work_item]
    elif work_item is not None and not args:
        args = [work_item]

    return args, kwargs


def execute_function_target(
    function_target: str,
    work_item: Any,
    *,
    args: Sequence[Any] | None = None,
    kwargs: Mapping[str, Any] | None = None,
    bundle_root: str | None = None,
) -> Any:
    """Import and execute the function referenced by ``module:function`` (Spec: [CC-3], [TS-1])."""
    func = import_callable_ref(function_target, bundle_root=bundle_root)

    call_args, call_kwargs = prepare_call_arguments(args, kwargs, work_item)
    return func(*call_args, **call_kwargs)


def execute_command_target(
    process_target: str,
    work_item: Any,
    *,
    args: Sequence[Any] | None = None,
    env: Mapping[str, str] | None = None,
    working_dir: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute an external command described by the TaskSpec (Spec: [CC-3], [TS-1], [RM-5])."""
    command = [process_target]
    if args:
        command.extend(str(item) for item in args)
    stdin_data = None

    if isinstance(work_item, dict):
        extra_args = work_item.get("args")
        if isinstance(extra_args, Iterable):
            command.extend(str(item) for item in extra_args)
        stdin_data = work_item.get("stdin")
    elif isinstance(work_item, str) and work_item:
        stdin_data = work_item

    env_vars = os.environ.copy()
    if env:
        env_vars.update(env)

    return subprocess.run(
        command,
        input=stdin_data,
        capture_output=True,
        text=True,
        cwd=working_dir or None,
        env=env_vars,
        timeout=timeout,
        check=False,
    )


def prepare_output(value: Any) -> PreparedOutput:
    """Convert one public output using whole-value text fallback [MF-2]."""
    if isinstance(value, str):
        return PreparedOutput(format="text", text=value)
    try:
        return PreparedOutput(format="json", text=json.dumps(value))
    except TypeError:
        return PreparedOutput(format="text", text=str(value))


def serialize_result(result: Any) -> str:
    """Serialize execution results, favouring JSON where possible (Spec: [CC-3], [MF-5])."""
    return prepare_output(result).text


def prepare_agent_execution(result: AgentExecutionResult) -> dict[str, Any]:
    """Detach best-effort agent metadata after checking raw identity [AR-5]."""
    for name in ("runtime", "output_mode", "status"):
        value = getattr(result, name)
        if not isinstance(value, str) or not value:
            raise TypeError(f"agent result {name} must be a nonempty string")
    if result.model is not None and not isinstance(result.model, str):
        raise TypeError("agent result model must be a string or null")

    identity = {
        "runtime": result.runtime,
        "model": result.model,
        "output_mode": result.output_mode,
        "status": result.status,
    }
    payload = {
        **identity,
        "metadata": result.metadata,
        "usage": result.usage,
        "tool_trace": list(result.tool_trace),
        "artifacts": list(result.artifacts),
    }
    try:
        serialized = json.loads(json.dumps(payload, default=str))
        if isinstance(serialized, dict):
            return serialized
    except (TypeError, ValueError, OverflowError):
        logger.debug("Failed to serialize agent execution payload", exc_info=True)
    return identity


def prepare_task_result(value: Any) -> PreparedTaskResult:
    """Prepare outputs and metadata once before handoff or publication.

    Host workers call this in their execution context. Consumer also adapts raw
    values from non-host plugins through this owner. Spec: [CC-3.5], [AR-4].
    """
    if isinstance(value, PreparedTaskResult):
        return value
    if isinstance(value, AgentExecutionResult):
        return PreparedTaskResult(
            kind="agent",
            outputs=tuple(prepare_output(output) for output in value.outputs),
            agent_execution=prepare_agent_execution(value),
        )
    return PreparedTaskResult(
        kind="value", outputs=(prepare_output(value),), agent_execution=None
    )


def prepared_result_to_payload(result: PreparedTaskResult) -> dict[str, Any]:
    """Encode prepared text and the detached metadata snapshot [CC-3.5]."""
    return {
        "kind": result.kind,
        "outputs": [
            {"format": output.format, "text": output.text} for output in result.outputs
        ],
        "agent_execution_json": (
            json.dumps(result.agent_execution)
            if result.agent_execution is not None
            else None
        ),
    }


def prepared_result_from_payload(payload: Any) -> PreparedTaskResult:
    """Validate private structure without interpreting public output text.

    Metadata is parsed because the parent publishes it as a mapping. Output
    text stays opaque until an actual direct caller needs a projection.
    Spec: [CC-3.5], [MF-2], [AR-5].
    """
    if not isinstance(payload, dict) or set(payload) != {
        "kind",
        "outputs",
        "agent_execution_json",
    }:
        raise TypeError("prepared result must have exactly the protocol fields")
    kind = payload["kind"]
    if kind not in ("value", "agent"):
        raise ValueError("prepared result kind must be value or agent")
    raw_outputs = payload["outputs"]
    if not isinstance(raw_outputs, list):
        raise TypeError("prepared result outputs must be a list")
    outputs = tuple(_prepared_output_from_payload(output) for output in raw_outputs)
    metadata_text = payload["agent_execution_json"]
    if kind == "value":
        if len(outputs) != 1 or metadata_text is not None:
            raise ValueError("prepared value requires one output and null metadata")
        return PreparedTaskResult(kind="value", outputs=outputs, agent_execution=None)
    if not isinstance(metadata_text, str):
        raise TypeError("prepared agent metadata must be JSON text")
    agent_execution = json.loads(metadata_text)
    if not isinstance(agent_execution, dict):
        raise TypeError("prepared agent metadata must decode to an object")
    return PreparedTaskResult(
        kind="agent", outputs=outputs, agent_execution=agent_execution
    )


def _prepared_output_from_payload(output: Any) -> PreparedOutput:
    """Validate one tagged output while keeping its text opaque [CC-3.5]."""
    if not isinstance(output, dict) or set(output) != {"format", "text"}:
        raise TypeError("prepared output must have exactly format and text")
    output_format = output["format"]
    if output_format not in ("text", "json"):
        raise ValueError("prepared output format must be text or json")
    if not isinstance(output["text"], str):
        raise TypeError("prepared output text must be a string")
    return PreparedOutput(format=output_format, text=output["text"])


def project_task_result(result: PreparedTaskResult) -> Any:
    """Project prepared values for the private direct-call API only [MF-2]."""
    outputs = tuple(
        json.loads(output.text) if output.format == "json" else output.text
        for output in result.outputs
    )
    if result.kind == "value":
        return outputs[0]
    metadata = result.agent_execution
    if metadata is None:
        raise ValueError("prepared agent result requires metadata")
    return AgentExecutionResult(
        runtime=metadata["runtime"],
        model=metadata["model"],
        output_mode=metadata["output_mode"],
        outputs=outputs,
        metadata=metadata.get("metadata", {}),
        status=metadata["status"],
        usage=metadata.get("usage"),
        tool_trace=tuple(metadata.get("tool_trace", ())),
        artifacts=tuple(metadata.get("artifacts", ())),
    )


__all__ = [
    "PreparedOutput",
    "PreparedTaskResult",
    "decode_work_message",
    "execute_command_target",
    "execute_function_target",
    "prepare_agent_execution",
    "prepare_call_arguments",
    "prepare_output",
    "prepare_task_result",
    "prepared_result_from_payload",
    "prepared_result_to_payload",
    "project_task_result",
    "serialize_result",
]
