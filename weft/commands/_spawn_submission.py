"""Submission reconciliation for queue-first spawn requests.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-2], [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-6], [MF-7]
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final, Literal

from simplebroker import Queue
from weft._constants import (
    CONTROL_SURFACE_WAIT_INTERVAL,
    QUEUE_RESERVED_SUFFIX,
    SPAWN_SUBMISSION_RECONCILIATION_TIMEOUT,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext
from weft.helpers import iter_queue_json_entries

from ._manager_bootstrap import _list_manager_records
from ._queue_wait import QueueChangeMonitor

_SPAWN_RECONCILIATION_STATIC_QUEUE_SPECS: Final[tuple[tuple[str, bool], ...]] = (
    (WEFT_TID_MAPPINGS_QUEUE, False),
    (WEFT_GLOBAL_LOG_QUEUE, False),
    (WEFT_SPAWN_REQUESTS_QUEUE, False),
    (WEFT_MANAGERS_REGISTRY_QUEUE, False),
)


@dataclass(frozen=True, slots=True)
class SpawnSubmissionReconciliation:
    """Durable submission outcome for a previously enqueued spawn request.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-6]
    """

    outcome: Literal["spawned", "rejected", "queued", "reserved", "unknown"]
    tid: str
    error: str | None = None
    reserved_queue: str | None = None


def _queue_contains_exact_message(
    context: WeftContext,
    queue_name: str,
    *,
    message_timestamp: int,
    persistent: bool = False,
) -> bool:
    queue = context.queue(queue_name, persistent=persistent)
    try:
        return (
            queue.peek_one(
                exact_timestamp=message_timestamp,
                with_timestamps=True,
            )
            is not None
        )
    finally:
        queue.close()


def _mapping_exists_for_tid(context: WeftContext, tid: str) -> bool:
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    try:
        for payload, _timestamp in iter_queue_json_entries(
            queue,
            since_timestamp=int(tid) - 1,
        ):
            if payload.get("full") == tid:
                return True
        return False
    finally:
        queue.close()


def _inspect_task_log_for_tid(
    context: WeftContext,
    tid: str,
) -> SpawnSubmissionReconciliation | None:
    queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        latest_child_event: tuple[int, str, str | None] | None = None
        for payload, timestamp in iter_queue_json_entries(
            queue,
            since_timestamp=int(tid) - 1,
        ):
            if payload.get("tid") == tid:
                return SpawnSubmissionReconciliation(outcome="spawned", tid=tid)
            if payload.get("child_tid") != tid:
                continue
            event = payload.get("event")
            if event not in {"task_spawned", "task_spawn_rejected"}:
                continue
            error = payload.get("error")
            if latest_child_event is None or timestamp >= latest_child_event[0]:
                latest_child_event = (
                    timestamp,
                    str(event),
                    str(error) if isinstance(error, str) else None,
                )

        if latest_child_event is None:
            return None
        _timestamp, event, error = latest_child_event
        if event == "task_spawned":
            return SpawnSubmissionReconciliation(outcome="spawned", tid=tid)
        return SpawnSubmissionReconciliation(
            outcome="rejected",
            tid=tid,
            error=error,
        )
    finally:
        queue.close()


def _find_reserved_spawn_request_queue(
    context: WeftContext,
    *,
    message_timestamp: int,
) -> str | None:
    for record in _list_manager_records(
        context,
        include_stopped=True,
        canonical_only=True,
        prune_stale=False,
    ):
        manager_tid = record.get("tid")
        if not isinstance(manager_tid, str) or not manager_tid:
            continue
        reserved_queue = f"T{manager_tid}.{QUEUE_RESERVED_SUFFIX}"
        if _queue_contains_exact_message(
            context,
            reserved_queue,
            message_timestamp=message_timestamp,
        ):
            return reserved_queue
    return None


def _reserved_spawn_request_queue_names(context: WeftContext) -> tuple[str, ...]:
    queue_names: list[str] = []
    for record in _list_manager_records(
        context,
        include_stopped=True,
        canonical_only=True,
        prune_stale=False,
    ):
        manager_tid = record.get("tid")
        if not isinstance(manager_tid, str) or not manager_tid:
            continue
        queue_names.append(f"T{manager_tid}.{QUEUE_RESERVED_SUFFIX}")
    return tuple(sorted(set(queue_names)))


def _spawn_reconciliation_queue_specs(
    context: WeftContext,
) -> tuple[tuple[str, bool], ...]:
    return _SPAWN_RECONCILIATION_STATIC_QUEUE_SPECS + tuple(
        (queue_name, False)
        for queue_name in _reserved_spawn_request_queue_names(context)
    )


def _open_spawn_reconciliation_monitor(
    context: WeftContext,
    queue_specs: tuple[tuple[str, bool], ...],
) -> tuple[list[Queue], QueueChangeMonitor]:
    queues = [
        context.queue(queue_name, persistent=persistent)
        for queue_name, persistent in queue_specs
    ]
    return queues, QueueChangeMonitor(queues, config=context.config)


def _reconcile_submitted_spawn_once(
    context: WeftContext,
    tid: str,
) -> SpawnSubmissionReconciliation:
    message_timestamp = int(tid)

    if _mapping_exists_for_tid(context, tid):
        return SpawnSubmissionReconciliation(outcome="spawned", tid=tid)

    log_result = _inspect_task_log_for_tid(context, tid)
    if log_result is not None:
        return log_result

    if _queue_contains_exact_message(
        context,
        WEFT_SPAWN_REQUESTS_QUEUE,
        message_timestamp=message_timestamp,
    ):
        return SpawnSubmissionReconciliation(outcome="queued", tid=tid)

    reserved_queue = _find_reserved_spawn_request_queue(
        context,
        message_timestamp=message_timestamp,
    )
    if reserved_queue is not None:
        return SpawnSubmissionReconciliation(
            outcome="reserved",
            tid=tid,
            reserved_queue=reserved_queue,
        )

    return SpawnSubmissionReconciliation(outcome="unknown", tid=tid)


def reconcile_submitted_spawn(
    context: WeftContext,
    tid: str,
    *,
    timeout: float = SPAWN_SUBMISSION_RECONCILIATION_TIMEOUT,
    poll_interval: float = CONTROL_SURFACE_WAIT_INTERVAL,
) -> SpawnSubmissionReconciliation:
    """Classify a previously submitted spawn request using durable state only.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-6]
    """

    deadline = time.monotonic() + max(timeout, 0.0)
    queue_specs = _spawn_reconciliation_queue_specs(context)
    monitor_queues, monitor = _open_spawn_reconciliation_monitor(context, queue_specs)
    try:
        while True:
            result = _reconcile_submitted_spawn_once(context, tid)
            if result.outcome != "unknown":
                return result

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return result

            current_specs = _spawn_reconciliation_queue_specs(context)
            if current_specs != queue_specs:
                monitor.close()
                for queue in monitor_queues:
                    queue.close()
                queue_specs = current_specs
                monitor_queues, monitor = _open_spawn_reconciliation_monitor(
                    context,
                    queue_specs,
                )
                continue

            monitor.wait(min(remaining, max(poll_interval, 0.0)))
    finally:
        monitor.close()
        for queue in monitor_queues:
            queue.close()
