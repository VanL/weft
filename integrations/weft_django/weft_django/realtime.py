"""Shared realtime payload helpers for Django transports."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from weft.client import Task, TaskEvent


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
    for event in task.realtime_events(follow=follow, cancel_event=cancel_event):
        yield task_event_payload(event)
