"""Shared outbox payload decoding helpers.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-5]
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DecodedOutboxValue:
    """One caller-facing outbox value plus whether it was emitted live already."""

    value: Any
    emitted: bool = False


OutboxTextEmitter = Callable[[str, bool, bool], None]


def decode_result_payload(raw: str) -> Any:
    """Decode a non-stream result payload for public output."""

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def process_outbox_message(
    raw: str,
    stream_buffer: list[str],
    *,
    emit_stream: bool = True,
    emit_text: OutboxTextEmitter | None = None,
) -> tuple[bool, DecodedOutboxValue | None]:
    """Decode one outbox payload, accumulating stream fragments when needed.

    The optional ``emit_text`` callback keeps CLI rendering above the core
    layer. Core callers should pass ``emit_stream=False``.
    """

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return True, DecodedOutboxValue(decode_result_payload(raw))

    if isinstance(envelope, dict) and envelope.get("type") == "stream":
        stream_name = str(envelope.get("stream") or "stdout")
        encoding = envelope.get("encoding", "text")
        data = envelope.get("data", "")
        if encoding == "base64":
            try:
                chunk = base64.b64decode(data)
                text = chunk.decode("utf-8", errors="replace")
            except (
                binascii.Error,
                ValueError,
                UnicodeDecodeError,
            ):  # pragma: no cover - malformed base64 envelope
                text = ""
        else:
            text = str(data)
        is_stderr = stream_name == "stderr"
        if text:
            if emit_stream and emit_text is not None:
                emit_text(text, is_stderr, False)
            stream_buffer.append(text)
        if envelope.get("final"):
            if emit_stream and emit_text is not None:
                emit_text("", is_stderr, True)
            value = "".join(stream_buffer)
            if envelope.get("result_transform") == "strip":
                value = value.strip()
            stream_buffer.clear()
            return True, DecodedOutboxValue(value, emitted=emit_stream)
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
    "process_outbox_message",
]
