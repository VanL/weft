"""Optional Channels integration for Weft task diagnostics."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from weft_django.client import get_core_client
from weft_django.conf import get_authz_callable, get_realtime_transport
from weft_django.realtime import AsyncIteratorOwner, iter_task_event_payloads

logger = logging.getLogger(__name__)

try:
    from channels.generic.websocket import AsyncJsonWebsocketConsumer
    from django.urls import re_path
except ImportError as exc:  # pragma: no cover - import guard
    raise ImproperlyConfigured(
        "Channels support requires installing weft-django with the 'channels' extra"
    ) from exc


@dataclass(frozen=True, slots=True)
class ScopeAuthRequest:
    """Small request-like adapter for Channels scope authorization."""

    scope: dict[str, Any]

    @property
    def user(self) -> Any:
        return self.scope.get("user")

    @property
    def session(self) -> Any:
        return self.scope.get("session")

    @property
    def path(self) -> str:
        return str(self.scope.get("path", ""))

    @property
    def headers(self) -> Any:
        return self.scope.get("headers", [])


def _authorize_scope(scope: dict[str, Any], tid: str, action: str) -> bool:
    authz = get_authz_callable(required=True)
    assert authz is not None
    return bool(authz(ScopeAuthRequest(scope), tid, action))


class TaskEventsConsumer(AsyncJsonWebsocketConsumer):  # pragma: no cover - optional
    _stream_task: asyncio.Task[None] | None = None
    _stream_cancel: threading.Event | None = None

    async def connect(self) -> None:
        if get_realtime_transport() != "channels":
            await self.close(code=4404)
            return

        tid = str(self.scope.get("url_route", {}).get("kwargs", {}).get("tid", ""))
        if not _authorize_scope(self.scope, tid, "stream"):
            await self.close(code=4403)
            return

        try:
            task = get_core_client().task(tid)
        except ValueError:
            await self.close(code=4404)
            return
        if task.snapshot() is None:
            await self.close(code=4404)
            return

        await self.accept()
        cancel_event = threading.Event()
        self._stream_cancel = cancel_event
        self._stream_task = asyncio.create_task(self._stream_events(task, cancel_event))

    async def disconnect(self, close_code: int) -> None:
        del close_code
        cancel_event = self._stream_cancel
        if cancel_event is not None:
            cancel_event.set()
        stream_task = self._stream_task
        if stream_task is None:
            return
        if stream_task.done():
            await stream_task
            return
        done, _pending = await asyncio.wait({stream_task}, timeout=1.0)
        if done:
            await stream_task
            return

        # Cancellation enters _stream_events' serialized owner cleanup. Do not
        # wait for that cleanup here: an active blocking advance can outlive the
        # disconnect deadline, and generator.close() must remain queued behind it.
        stream_task.cancel()
        stream_task.add_done_callback(self._report_stream_completion)

    @staticmethod
    def _report_stream_completion(stream_task: asyncio.Task[None]) -> None:
        try:
            stream_task.result()
        except asyncio.CancelledError:
            return
        except BaseException as exc:  # pragma: no cover - async diagnostic boundary
            logger.error(
                "Django realtime stream cleanup failed after disconnect",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    async def _stream_events(self, task: Any, cancel_event: threading.Event) -> None:
        owner = AsyncIteratorOwner(
            lambda: iter_task_event_payloads(
                task,
                follow=True,
                cancel_event=cancel_event,
            ),
            cancel_event=cancel_event,
        )
        normal_end = False
        body_error: BaseException | None = None
        try:
            while not cancel_event.is_set():
                try:
                    payload = await owner.__anext__()
                except StopAsyncIteration:
                    normal_end = True
                    break
                await self.send_json(payload)
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            cancel_event.set()
            try:
                await owner.aclose()
            except BaseException as cleanup_error:
                if body_error is None or isinstance(body_error, asyncio.CancelledError):
                    raise
                body_error.add_note(
                    f"Realtime stream cleanup failed: {cleanup_error!r}"
                )

        if normal_end:
            await self.close()


websocket_urlpatterns = [
    re_path(r"^ws/weft/tasks/(?P<tid>[^/]+)/$", TaskEventsConsumer.as_asgi()),
]
