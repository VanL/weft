"""Shared outbox payload decoding helpers.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-2.1], [MF-5]
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DecodedOutboxValue:
    """One decoded value, its stream origin, and whether it was emitted live."""

    value: Any
    emitted: bool = False
    streamed: bool = False
    return_code: int | None = None
    error: str | None = None
    command_stdout: bool = False


OutboxTextEmitter = Callable[[str, bool, bool], None]


def is_large_output_reference(value: Any) -> bool:
    """Recognize spill transport structurally, without filesystem access [MF-2.1]."""
    return (
        isinstance(value, dict)
        and value.get("type") == "large_output"
        and isinstance(value.get("path"), str)
        and type(value.get("size")) is int
        and value["size"] >= 0
        and isinstance(value.get("sha256"), str)
        and re.fullmatch(r"[0-9a-fA-F]{64}", value["sha256"]) is not None
        and (
            "hostname" not in value
            or isinstance(value["hostname"], str)
            and bool(value["hostname"])
        )
    )


def is_command_output(value: Any) -> bool:
    """Recognize exactly valid success transport for either queue boundary [MF-2]."""
    return (
        isinstance(value, dict)
        and set(value) == {"type", "result"}
        and value["type"] == "command_output"
        and (
            isinstance(value["result"], str)
            or is_large_output_reference(value["result"])
        )
    )


def decode_result_payload(raw: str) -> Any:
    """Decode a non-stream result payload for public output."""

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def decode_stream_text(envelope: Mapping[str, Any]) -> str:
    """Decode shared text/base64 stream transport, retaining malformed policy [MF-2]."""
    data = envelope.get("data", "")
    if envelope.get("encoding", "text") == "base64":
        try:
            return base64.b64decode(data).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return ""
    return str(data)


def process_outbox_message(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-029] exception
    raw: str,
    stream_buffer: list[str],
    *,
    emit_stream: bool = True,
    emit_text: OutboxTextEmitter | None = None,
    emit_command_stdout: Callable[[str], None] | None = None,
) -> tuple[bool, DecodedOutboxValue | None]:
    """Decode one outbox payload, accumulating stream fragments when needed.

    The optional ``emit_text`` callback keeps CLI rendering above the core
    layer. Core callers should pass ``emit_stream=False``.
    """

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return True, DecodedOutboxValue(decode_result_payload(raw))

    if isinstance(envelope, dict) and envelope.get("type") == "command_output":
        if not is_command_output(envelope):
            raise ValueError("Malformed command_output envelope")
        return True, DecodedOutboxValue(envelope["result"], command_stdout=True)

    if isinstance(envelope, dict) and envelope.get("type") == "failed_output":
        if (
            set(envelope) != {"type", "return_code", "error", "result"}
            or type(envelope["return_code"]) is not int
            or envelope["return_code"] == 0
            or not isinstance(envelope["error"], str)
        ):
            raise ValueError("Malformed failed_output envelope")
        return True, DecodedOutboxValue(
            envelope["result"],
            return_code=envelope["return_code"],
            error=envelope["error"],
            command_stdout=True,
        )

    if isinstance(envelope, dict) and envelope.get("type") == "stream":
        stream_name = str(envelope.get("stream") or "stdout")
        text = decode_stream_text(envelope)
        is_stderr = stream_name == "stderr"
        command_stdout = not is_stderr and envelope.get("command_stdout") is True
        if text and command_stdout and emit_stream and emit_command_stdout is not None:
            emit_command_stdout(text)
            stream_buffer.append(text)
        elif text and is_stderr:
            if emit_stream and emit_text is not None:
                emit_text(text, is_stderr, False)
        elif text:
            if emit_stream and emit_text is not None:
                emit_text(text, is_stderr, False)
            stream_buffer.append(text)
        if envelope.get("final"):
            if emit_stream and emit_text is not None:
                emit_text("", is_stderr, not command_stdout)
            if is_stderr:
                return False, None
            value = "".join(stream_buffer)
            stream_buffer.clear()
            return True, DecodedOutboxValue(
                value, emitted=emit_stream, streamed=True, command_stdout=command_stdout
            )
        return False, None

    return True, DecodedOutboxValue(envelope)


def aggregate_public_outputs(values: list[Any]) -> Any:
    """Collapse collected public outputs into the caller-facing shape."""

    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return list(values)


__all__ = [
    "DecodedOutboxValue",
    "OutboxTextEmitter",
    "aggregate_public_outputs",
    "decode_result_payload",
    "decode_stream_text",
    "is_command_output",
    "is_large_output_reference",
    "process_outbox_message",
]
