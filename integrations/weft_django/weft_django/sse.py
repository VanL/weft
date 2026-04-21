"""Server-Sent Event helpers for read-only task diagnostics."""

from __future__ import annotations

import json
from typing import Any

from django.http import StreamingHttpResponse

from weft_django.client import get_core_client
from weft_django.realtime import iter_task_event_payloads


def _serialize_event(event_type: str, payload: dict[str, Any]) -> bytes:
    return (
        f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode()


def event_stream(tid: str) -> Any:
    client = get_core_client()
    task = client.task(tid)
    for payload in iter_task_event_payloads(task, follow=True):
        yield _serialize_event(str(payload["event_type"]), payload)


def sse_response(tid: str) -> StreamingHttpResponse:
    response = StreamingHttpResponse(
        event_stream(tid),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    return response
