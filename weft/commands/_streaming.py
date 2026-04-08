"""Shared CLI helpers for queue-stream and task-log assembly.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2]
- docs/specifications/05-Message_Flow_and_State.md [MF-2]
"""

from __future__ import annotations

import base64
import json
from typing import Any

import typer

from simplebroker import Queue
from weft.helpers import iter_queue_json_entries


def handle_ctrl_stream(raw: str) -> None:
    """Render a ctrl_out stream/control payload for the CLI."""
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        typer.echo(raw, err=True)
        return

    if not isinstance(envelope, dict):
        typer.echo(str(envelope), err=True)
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
        typer.echo(text, err=is_stderr, nl=False)
        if envelope.get("final"):
            typer.echo(err=is_stderr)


def process_outbox_message(
    raw: str,
    stream_buffer: list[str],
    *,
    emit_stream: bool = True,
) -> tuple[bool, Any]:
    """Decode one outbox payload, accumulating stream fragments when needed."""
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return True, decode_result_payload(raw)

    if isinstance(envelope, dict) and envelope.get("type") == "stream":
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
                typer.echo(text, nl=False)
            stream_buffer.append(text)
        if envelope.get("final"):
            if emit_stream:
                typer.echo()
            value = "".join(stream_buffer)
            stream_buffer.clear()
            return True, value
        return False, None

    return True, envelope


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
