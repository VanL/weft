"""Shared task event iteration helpers for CLI and Python clients.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]
- docs/specifications/13C-Using_Weft_With_Django.md [DJ-2.1], [DJ-2.2],
  [DJ-12.1], [DJ-12.3]
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

from weft._constants import (
    TERMINAL_TASK_STATUSES,
    WEFT_COMPLETED_RESULT_GRACE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.commands import tasks as task_ops
from weft.commands.types import TaskEvent
from weft.context import WeftContext
from weft.core.queue_wait import QueueChangeMonitor
from weft.helpers import iter_queue_entries, iter_queue_json_entries

from ._result_wait import (
    append_public_value,
    terminal_error_message,
    terminal_status_from_event,
)
from ._streaming import aggregate_public_outputs, process_outbox_message
from .result import (
    _await_result_materialization,
    _queue_names_for_tid,
    await_task_result,
)
from .submission import normalize_tid


def _task_event_type(payload: dict[str, object]) -> str:
    event = payload.get("event")
    if isinstance(event, str) and event:
        return event
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    return "task_event"


def _is_cancelled(cancel_event: Any | None) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


def _state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in ("status", "event", "activity", "waiting_on", "error"):
        value = payload.get(key)
        if value is not None:
            normalized[key] = value

    taskspec = payload.get("taskspec")
    if isinstance(taskspec, dict):
        state = taskspec.get("state")
        if isinstance(state, dict):
            if "status" not in normalized and isinstance(state.get("status"), str):
                normalized["status"] = state["status"]
            if "error" not in normalized and isinstance(state.get("error"), str):
                normalized["error"] = state["error"]
            return_code = state.get("return_code")
            if isinstance(return_code, int):
                normalized["return_code"] = return_code

    return normalized


def _stream_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "data": str(payload.get("data", "")),
        "final": bool(payload.get("final")),
    }
    chunk = payload.get("chunk")
    if isinstance(chunk, int):
        normalized["chunk"] = chunk
    encoding = payload.get("encoding")
    if isinstance(encoding, str) and encoding:
        normalized["encoding"] = encoding
    return normalized


def _peek_result_value(
    context: WeftContext,
    *,
    outbox_name: str,
) -> Any | None:
    queue = context.queue(outbox_name, persistent=True)
    stream_buffer: list[str] = []
    result_values: list[Any] = []
    try:
        for raw_payload, _timestamp in iter_queue_entries(queue):
            final, value = process_outbox_message(
                raw_payload,
                stream_buffer,
                emit_stream=False,
            )
            if final and value is not None:
                append_public_value(result_values, value, show_stderr=False)
        return aggregate_public_outputs(result_values)
    finally:
        queue.close()


def _task_snapshot_event(
    context: WeftContext,
    normalized_tid: str,
) -> TaskEvent | None:
    snapshot = task_ops.task_snapshot(
        normalized_tid,
        context=context,
    )
    if snapshot is None:
        return None
    snapshot_timestamp = (
        snapshot.last_timestamp
        or snapshot.started_at
        or snapshot.completed_at
        or int(normalized_tid)
    )
    return TaskEvent(
        tid=normalized_tid,
        event_type="snapshot",
        timestamp=snapshot_timestamp,
        payload=asdict(snapshot),
    )


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


def iter_task_realtime_events(
    context: WeftContext,
    tid: str,
    *,
    follow: bool = True,
    cancel_event: Any | None = None,
) -> Iterator[TaskEvent]:
    """Yield read-only browser-oriented task events.

    The iterator never consumes result or stream queues. It peeks all queues so
    HTTP/SSE/WS diagnostics do not mutate the underlying task result surface.
    """

    normalized_tid = normalize_tid(tid)
    materialized = _await_result_materialization(
        context,
        normalized_tid,
        timeout=0.2 if follow else 0.0,
    )
    taskspec_payload = (
        materialized.taskspec_payload if materialized is not None else None
    )
    outbox_name, ctrl_out_name = _queue_names_for_tid(normalized_tid, taskspec_payload)

    snapshot_emitted = False
    snapshot_event = _task_snapshot_event(context, normalized_tid)
    if snapshot_event is not None:
        if _is_cancelled(cancel_event):
            return
        yield snapshot_event
        snapshot_emitted = True

    outbox_queue = context.queue(outbox_name, persistent=True)
    ctrl_queue = context.queue(ctrl_out_name, persistent=False)
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    monitor = QueueChangeMonitor(
        [outbox_queue, ctrl_queue, log_queue],
        config=context.config,
    )

    last_log_timestamp = (
        materialized.log_last_timestamp
        if materialized is not None
        else int(normalized_tid) - 1
    )
    last_outbox_timestamp: int | None = None
    last_ctrl_timestamp: int | None = None
    terminal_payload: dict[str, Any] | None = None
    terminal_timestamp: int | None = None

    try:
        while not _is_cancelled(cancel_event):
            saw_event = False

            outbox_since = (
                None if last_outbox_timestamp is None else last_outbox_timestamp + 1
            )
            for payload, timestamp in iter_queue_json_entries(
                outbox_queue,
                since_timestamp=outbox_since,
            ):
                last_outbox_timestamp = timestamp
                if payload.get("type") != "stream" or payload.get("stream") != "stdout":
                    continue
                saw_event = True
                if _is_cancelled(cancel_event):
                    return
                yield TaskEvent(
                    tid=normalized_tid,
                    event_type="stdout",
                    timestamp=timestamp,
                    payload=_stream_payload(payload),
                )

            ctrl_since = (
                None if last_ctrl_timestamp is None else last_ctrl_timestamp + 1
            )
            for payload, timestamp in iter_queue_json_entries(
                ctrl_queue,
                since_timestamp=ctrl_since,
            ):
                last_ctrl_timestamp = timestamp
                if payload.get("type") != "stream" or payload.get("stream") != "stderr":
                    continue
                saw_event = True
                if _is_cancelled(cancel_event):
                    return
                yield TaskEvent(
                    tid=normalized_tid,
                    event_type="stderr",
                    timestamp=timestamp,
                    payload=_stream_payload(payload),
                )

            log_since = None if last_log_timestamp is None else last_log_timestamp + 1
            for payload, timestamp in iter_queue_json_entries(
                log_queue,
                since_timestamp=log_since,
            ):
                if payload.get("tid") != normalized_tid:
                    continue
                last_log_timestamp = timestamp
                saw_event = True
                if _is_cancelled(cancel_event):
                    return
                if not snapshot_emitted:
                    snapshot_event = _task_snapshot_event(context, normalized_tid)
                    if snapshot_event is not None:
                        yield snapshot_event
                        snapshot_emitted = True
                yield TaskEvent(
                    tid=normalized_tid,
                    event_type="state",
                    timestamp=timestamp,
                    payload=_state_payload(payload),
                )
                status = terminal_status_from_event(payload)
                if status in TERMINAL_TASK_STATUSES:
                    terminal_payload = payload
                    terminal_timestamp = timestamp

            if terminal_payload is not None:
                terminal_status = (
                    terminal_status_from_event(terminal_payload) or "unknown"
                )
                if not snapshot_emitted:
                    snapshot_event = _task_snapshot_event(context, normalized_tid)
                    if snapshot_event is not None:
                        yield snapshot_event
                        snapshot_emitted = True
                result_value = _peek_result_value(context, outbox_name=outbox_name)
                if terminal_status == "completed" and result_value is None:
                    deadline = time.monotonic() + WEFT_COMPLETED_RESULT_GRACE_SECONDS
                    while (
                        result_value is None
                        and time.monotonic() < deadline
                        and not _is_cancelled(cancel_event)
                    ):
                        monitor.wait(0.05)
                        result_value = _peek_result_value(
                            context,
                            outbox_name=outbox_name,
                        )
                if _is_cancelled(cancel_event):
                    return
                yield TaskEvent(
                    tid=normalized_tid,
                    event_type="result",
                    timestamp=terminal_timestamp or time.time_ns(),
                    payload={
                        "status": terminal_status,
                        "value": result_value,
                        "error": terminal_error_message(
                            terminal_payload, terminal_status
                        ),
                    },
                )
                yield TaskEvent(
                    tid=normalized_tid,
                    event_type="end",
                    timestamp=time.time_ns(),
                    payload={"status": terminal_status},
                )
                return

            if not follow:
                return
            if not saw_event:
                monitor.wait(0.1)
    finally:
        monitor.close()
        outbox_queue.close()
        ctrl_queue.close()
        log_queue.close()
