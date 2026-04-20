"""Shared task event iteration helpers for CLI and Python clients.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]
- docs/specifications/13C-Using_Weft_With_Django.md [DJ-2.1], [DJ-2.2]
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from weft._constants import (
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.commands.types import TaskEvent
from weft.context import WeftContext
from weft.core.queue_wait import QueueChangeMonitor
from weft.helpers import iter_queue_json_entries

from ._result_wait import terminal_status_from_event
from .result import await_task_result
from .submission import normalize_tid


def _task_event_type(payload: dict[str, object]) -> str:
    event = payload.get("event")
    if isinstance(event, str) and event:
        return event
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    return "task_event"


def iter_task_events(
    context: WeftContext,
    tid: str,
    *,
    follow: bool = False,
) -> Iterator[TaskEvent]:
    """Yield raw lifecycle events for one task."""

    normalized_tid = normalize_tid(tid)
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    monitor = QueueChangeMonitor([log_queue], config=context.config)
    last_timestamp: int | None = int(normalized_tid) - 1
    terminal_seen = False

    try:
        while True:
            saw_event = False
            since_timestamp = None if last_timestamp is None else last_timestamp + 1
            for payload, timestamp in iter_queue_json_entries(
                log_queue,
                since_timestamp=since_timestamp,
            ):
                if payload.get("tid") != normalized_tid:
                    continue
                last_timestamp = timestamp
                saw_event = True
                event = TaskEvent(
                    tid=normalized_tid,
                    event_type=_task_event_type(payload),
                    timestamp=timestamp,
                    payload=payload,
                )
                yield event
                status = terminal_status_from_event(payload)
                if status in TERMINAL_TASK_STATUSES:
                    terminal_seen = True

            if terminal_seen or not follow:
                return
            if not saw_event:
                monitor.wait(0.1)
    finally:
        monitor.close()
        log_queue.close()


def follow_task_events(
    context: WeftContext,
    tid: str,
) -> Iterator[TaskEvent]:
    """Yield raw events followed by one synthetic final result event."""

    normalized_tid = normalize_tid(tid)
    yield from iter_task_events(context, normalized_tid, follow=True)

    result = await_task_result(
        context,
        normalized_tid,
    )
    yield TaskEvent(
        tid=normalized_tid,
        event_type="result",
        timestamp=time.time_ns(),
        payload={
            "status": result.status,
            "value": result.value,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": result.error,
        },
    )
