"""Shared CLI helpers for queue-stream and task-log assembly.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2]
- docs/specifications/05-Message_Flow_and_State.md [MF-2]
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
from typing import Any

from simplebroker import Queue
from weft.core.outbox import (
    DecodedOutboxValue,
    aggregate_public_outputs,
    decode_result_payload,
)
from weft.core.outbox import (
    process_outbox_message as _process_outbox_message,
)
from weft.helpers import iter_queue_json_entries

__all__ = [
    "DecodedOutboxValue",
    "aggregate_public_outputs",
    "collect_interactive_queue_output",
    "decode_result_payload",
    "drain_available_outbox_values",
    "handle_ctrl_stream",
    "poll_log_events",
    "process_outbox_message",
]


class _TyperCompat:
    """Tiny echo surface kept for test compatibility without importing Typer."""

    @staticmethod
    def echo(text: str = "", *, err: bool = False, nl: bool = True) -> None:
        stream = sys.stderr if err else sys.stdout
        if text:
            stream.write(text)
        if nl:
            stream.write("\n")
        stream.flush()


typer = _TyperCompat()


def _emit_text(text: str = "", *, err: bool = False, nl: bool = True) -> None:
    typer.echo(text, err=err, nl=nl)


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

    data = envelope.get("data", "")
    encoding = envelope.get("encoding", "text")
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


def drain_available_outbox_values(
    outbox_queue: Queue,
    stream_buffer: list[str],
    *,
    emit_stream: bool = True,
) -> tuple[list[DecodedOutboxValue], bool]:
    """Drain all currently available outbox messages without blocking."""

    values: list[DecodedOutboxValue] = []
    consumed_any = False
    while True:
        outbox_raw = outbox_queue.read_one()
        if outbox_raw is None:
            return values, consumed_any

        consumed_any = True
        payload = outbox_raw[0] if isinstance(outbox_raw, tuple) else outbox_raw
        final, value = process_outbox_message(
            str(payload),
            stream_buffer,
            emit_stream=emit_stream,
        )
        if final and value is not None:
            values.append(value)


def poll_log_events(
    log_queue: Queue,
    last_timestamp: int | None,
    target_tid: str,
) -> tuple[list[tuple[dict[str, Any], int]], int | None]:
    """Return target task-log events and the highest scanned log timestamp."""
    events: list[tuple[dict[str, Any], int]] = []
    max_scanned_timestamp: int | None = None
    for data, timestamp in iter_queue_json_entries(
        log_queue,
        since_timestamp=last_timestamp,
    ):
        if last_timestamp is not None and timestamp <= last_timestamp:
            continue
        if max_scanned_timestamp is None or timestamp > max_scanned_timestamp:
            max_scanned_timestamp = timestamp
        if data.get("tid") != target_tid:
            continue
        events.append((data, timestamp))

    return (
        events,
        max_scanned_timestamp if max_scanned_timestamp is not None else last_timestamp,
    )


def collect_interactive_queue_output(outbox_queue: Queue) -> list[str]:
    """Return textual stream content from an interactive outbox queue."""
    collected: list[str] = []
    for item in outbox_queue.peek_generator():
        payload_raw = item[0] if isinstance(item, tuple) else item
        try:
            payload_obj = json.loads(payload_raw)
        except json.JSONDecodeError:
            collected.append(str(payload_raw))
            continue

        if isinstance(payload_obj, dict) and payload_obj.get("type") == "stream":
            data = payload_obj.get("data", "")
            encoding = payload_obj.get("encoding", "text")
            if encoding == "base64":
                try:
                    chunk_bytes = base64.b64decode(data)
                    collected.append(chunk_bytes.decode("utf-8", errors="replace"))
                except (
                    binascii.Error,
                    ValueError,
                    UnicodeDecodeError,
                ):  # pragma: no cover - malformed base64 envelope
                    collected.append(str(data))
            else:
                collected.append(str(data))
            continue

        collected.append(json.dumps(payload_obj, ensure_ascii=False))

    return collected
