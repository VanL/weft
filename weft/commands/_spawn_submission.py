"""Submission reconciliation for queue-first spawn requests.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-2], [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-6], [MF-7]
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from simplebroker import Queue
from weft._constants import (
    QUEUE_RESERVED_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.commands._manager_bootstrap import _list_manager_records
from weft.context import WeftContext
from weft.helpers import iter_queue_json_entries


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
    queue = Queue(
        queue_name,
        db_path=context.broker_target,
        persistent=persistent,
        config=context.broker_config,
    )
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
    queue = Queue(
        WEFT_TID_MAPPINGS_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.broker_config,
    )
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
    queue = Queue(
        WEFT_GLOBAL_LOG_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.broker_config,
    )
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
    timeout: float = 1.0,
    poll_interval: float = 0.05,
) -> SpawnSubmissionReconciliation:
    """Classify a previously submitted spawn request using durable state only.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-6]
    """

    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        result = _reconcile_submitted_spawn_once(context, tid)
        if result.outcome != "unknown" or time.monotonic() >= deadline:
            return result
        time.sleep(poll_interval)
