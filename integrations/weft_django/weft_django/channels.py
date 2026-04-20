"""Optional Channels integration for Weft task diagnostics."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

try:
    from channels.generic.websocket import AsyncJsonWebsocketConsumer
except ImportError as exc:  # pragma: no cover - import guard
    raise ImproperlyConfigured(
        "Channels support requires installing weft-django with the 'channels' extra"
    ) from exc


class TaskEventsConsumer(AsyncJsonWebsocketConsumer):  # pragma: no cover - optional
    async def connect(self) -> None:
        await self.accept()
        await self.send_json(
            {
                "event_type": "end",
                "payload": {
                    "detail": "Use the default SSE transport in v1 unless the project provides a custom Channels consumer."
                },
            }
        )
        await self.close()
