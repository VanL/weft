"""Shared CLI helpers for result payload and control-stream rendering.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2]
- docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-5]
"""

from __future__ import annotations

import json
import sys

from weft.core.outbox import (
    DecodedOutboxValue,
    aggregate_public_outputs,
    decode_result_payload,
    decode_stream_text,
)
from weft.core.outbox import (
    process_outbox_message as _process_outbox_message,
)

__all__ = [
    "DecodedOutboxValue",
    "aggregate_public_outputs",
    "decode_result_payload",
    "decode_stream_text",
    "handle_ctrl_stream",
    "process_outbox_message",
]


def _emit_text(text: str = "", *, err: bool = False, nl: bool = True) -> None:
    stream = sys.stderr if err else sys.stdout
    if text:
        stream.write(text)
    if nl:
        stream.write("\n")
    stream.flush()


def handle_ctrl_stream(raw: str) -> None:
    """Render a ctrl_out stream/control payload for the CLI."""
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        _emit_text(raw, err=True)
        return

    if not isinstance(envelope, dict):
        _emit_text(str(envelope), err=True)
        return

    text = decode_stream_text(envelope)

    is_stderr = envelope.get("stream") == "stderr"
    if text:
        _emit_text(text, err=is_stderr, nl=False)
        if envelope.get("final"):
            _emit_text(err=is_stderr)


def process_outbox_message(
    raw: str,
    stream_buffer: list[str],
    *,
    emit_stream: bool = True,
) -> tuple[bool, DecodedOutboxValue | None]:
    """Decode one outbox payload, accumulating stream fragments when needed."""
    return _process_outbox_message(
        raw,
        stream_buffer,
        emit_stream=emit_stream,
        emit_text=lambda text, err, nl: _emit_text(text, err=err, nl=nl),
    )
