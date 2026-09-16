"""Shared realtime payload helpers and async stream ownership.

Spec references:
- docs/specifications/13C-Using_Weft_With_Django.md [DJ-12.1], [DJ-12.2]
- docs/specifications/14-Python_API_Surfaces.md [PY-2]
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from weft.client import Task, TaskEvent

_STREAM_END = object()


def task_event_payload(event: TaskEvent) -> dict[str, Any]:
    return {
        "tid": event.tid,
        "event_type": event.event_type,
        "timestamp": event.timestamp,
        "payload": event.payload,
    }


def iter_task_event_payloads(
    task: Task,
    *,
    follow: bool = True,
    cancel_event: Any | None = None,
) -> Iterator[dict[str, Any]]:
    events = task.realtime_events(follow=follow, cancel_event=cancel_event)
    try:
        for event in events:
            yield task_event_payload(event)
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()


class AsyncIteratorOwner[T]:
    """Run one blocking iterator's complete lifetime on one worker thread.

    The factory is lazy, so closing before the first advance allocates no worker.
    Closure is queued behind any active advance and therefore never calls
    ``generator.close()`` concurrently with ``next()``.
    """

    def __init__(
        self,
        factory: Callable[[], Iterator[T]],
        *,
        cancel_event: threading.Event,
    ) -> None:
        self._factory = factory
        self._cancel_event = cancel_event
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._iterator: Iterator[T] | None = None
        self._advance_future: Future[T | object] | None = None
        self._close_future: Future[None] | None = None
        self._closed = False

    def __aiter__(self) -> AsyncIteratorOwner[T]:
        return self

    async def __anext__(self) -> T:
        future = self._submit_advance()
        result = await asyncio.shield(asyncio.wrap_future(future))
        if result is _STREAM_END:
            await self.aclose()
            raise StopAsyncIteration
        return result  # type: ignore[return-value]

    def _submit_advance(self) -> Future[T | object]:
        with self._lock:
            if self._closed:
                future: Future[T | object] = Future()
                future.set_result(_STREAM_END)
                return future
            if self._advance_future is not None and not self._advance_future.done():
                raise RuntimeError("realtime iterator advance is already active")
            executor = self._executor
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="weft-django-stream",
                )
                self._executor = executor
            future = executor.submit(self._advance_on_owner)
            self._advance_future = future
            return future

    def _advance_on_owner(self) -> T | object:
        iterator = self._iterator
        if iterator is None:
            iterator = self._factory()
            self._iterator = iterator
        try:
            return next(iterator)
        except StopIteration:
            return _STREAM_END

    def request_close(self) -> Future[None] | None:
        """Request serialized close and return its caller-owned future."""

        self._cancel_event.set()
        with self._lock:
            if self._close_future is not None:
                return self._close_future
            self._closed = True
            executor = self._executor
            if executor is None:
                return None
            future = executor.submit(self._close_on_owner)
            self._close_future = future
            executor.shutdown(wait=False)
            return future

    async def aclose(self) -> None:
        """Wait asynchronously for owner-thread iterator cleanup."""

        future = self.request_close()
        if future is not None:
            await asyncio.shield(asyncio.wrap_future(future))

    def _close_on_owner(self) -> None:
        iterator = self._iterator
        self._iterator = None
        if iterator is None:
            return
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
