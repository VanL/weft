"""Server-Sent Event helpers for read-only task diagnostics."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from django.http import StreamingHttpResponse

from weft_django.client import get_core_client


def _serialize_event(event_type: str, payload: dict[str, Any]) -> bytes:
    return (
        f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode()


def event_stream(tid: str) -> Any:
    client = get_core_client()
    task = client.task(tid)
    snapshot = task.snapshot()
    if snapshot is not None:
        yield _serialize_event(
            "snapshot",
            {
                "tid": tid,
                "event_type": "snapshot",
                "timestamp": snapshot.started_at or snapshot.completed_at or 0,
                "payload": asdict(snapshot),
            },
        )
    for event in task.follow():
        event_type = event.event_type
        if event_type not in {"result"}:
            event_type = "state"
        yield _serialize_event(
            event_type,
            {
                "tid": event.tid,
                "event_type": event_type,
                "timestamp": event.timestamp,
                "payload": event.payload,
            },
        )
    yield _serialize_event(
        "end",
        {
            "tid": tid,
            "event_type": "end",
            "timestamp": 0,
            "payload": {},
        },
    )


def sse_response(tid: str) -> StreamingHttpResponse:
    return StreamingHttpResponse(
        event_stream(tid),
        content_type="text/event-stream",
    )
