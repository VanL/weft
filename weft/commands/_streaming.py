"""Shared CLI helpers for queue-stream and task-log assembly.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2]
- docs/specifications/05-Message_Flow_and_State.md [MF-2]
"""

from __future__ import annotations

import base64
import json
import sys
from dataclasses import dataclass
from typing import Any

from simplebroker import Queue
from weft.helpers import iter_queue_json_entries


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


@dataclass(frozen=True, slots=True)
class DecodedOutboxValue:
    """One caller-facing outbox value plus whether it was emitted live already."""

    value: Any
    emitted: bool = False


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
        except Exception:
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
            except Exception:
                text = ""
        else:
            text = str(data)
        if text:
            if emit_stream:
                _emit_text(text, err=stream_name == "stderr", nl=False)
            stream_buffer.append(text)
        if envelope.get("final"):
            if emit_stream:
                _emit_text(err=stream_name == "stderr")
            value = "".join(stream_buffer)
            if envelope.get("result_transform") == "strip":
                value = value.strip()
            stream_buffer.clear()
            return True, DecodedOutboxValue(value, emitted=emit_stream)
        return False, None

    return True, DecodedOutboxValue(envelope)


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


def aggregate_public_outputs(values: list[Any]) -> Any:
    """Collapse collected public outputs into the CLI-facing shape."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return list(values)


def poll_log_events(
    log_queue: Queue,
    last_timestamp: int | None,
    target_tid: str,
) -> tuple[list[tuple[dict[str, Any], int]], int | None]:
    """Return new task-log events for ``target_tid`` since ``last_timestamp``."""
    events: list[tuple[dict[str, Any], int]] = []
    for data, timestamp in iter_queue_json_entries(
        log_queue,
        since_timestamp=last_timestamp,
    ):
        if last_timestamp is not None and timestamp <= last_timestamp:
            continue
        if data.get("tid") != target_tid:
            continue
        events.append((data, timestamp))

    if events:
        last_timestamp = events[-1][1]
    return events, last_timestamp


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
                except Exception:
                    collected.append(str(data))
            else:
                collected.append(str(data))
            continue

        collected.append(json.dumps(payload_obj, ensure_ascii=False))

    return collected


def decode_result_payload(raw: str) -> Any:
    """Decode a non-stream result payload for public output."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
