"""Server-Sent Event helpers for read-only task diagnostics."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import Future
from typing import Any

from django.http import StreamingHttpResponse

from weft_django.client import get_core_client
from weft_django.realtime import AsyncIteratorOwner, iter_task_event_payloads

logger = logging.getLogger(__name__)


def _serialize_event(event_type: str, payload: dict[str, Any]) -> bytes:
    return (
        f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode()


def event_stream(
    tid: str,
    *,
    cancel_event: threading.Event | None = None,
) -> Iterator[bytes]:
    client = get_core_client()
    task = client.task(tid)
    payloads = iter_task_event_payloads(
        task,
        follow=True,
        cancel_event=cancel_event,
    )
    try:
        for payload in payloads:
            yield _serialize_event(str(payload["event_type"]), payload)
    finally:
        close = getattr(payloads, "close", None)
        if callable(close):
            close()


class _AsyncSSEStream(AsyncIterator[bytes]):
    """ASGI response body backed by one serialized iterator owner."""

    def __init__(self, tid: str) -> None:
        self._cancel_event = threading.Event()
        self._close_reporter_attached = False
        self._owner = AsyncIteratorOwner(
            lambda: event_stream(tid, cancel_event=self._cancel_event),
            cancel_event=self._cancel_event,
        )

    def __aiter__(self) -> _AsyncSSEStream:
        return self

    async def __anext__(self) -> bytes:
        return await self._owner.__anext__()

    async def aclose(self) -> None:
        await self._owner.aclose()

    def close(self) -> None:
        """Let Django response teardown request owner-thread cleanup."""

        future = self._owner.request_close()
        if future is not None and not self._close_reporter_attached:
            self._close_reporter_attached = True
            future.add_done_callback(self._report_close_failure)

    @staticmethod
    def _report_close_failure(future: Future[None]) -> None:
        try:
            future.result()
        except BaseException as exc:  # pragma: no cover - response-close diagnostic
            logger.error(
                "Django SSE stream cleanup failed",
                exc_info=(type(exc), exc, exc.__traceback__),
            )


def sse_response(tid: str, *, asynchronous: bool = False) -> StreamingHttpResponse:
    stream: Any = _AsyncSSEStream(tid) if asynchronous else event_stream(tid)
    response = StreamingHttpResponse(
        stream,
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    return response
