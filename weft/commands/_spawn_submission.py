"""Submission reconciliation for queue-first spawn requests.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-2], [MA-3]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-6], [MF-7]
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Final, Literal

from simplebroker import Queue
from weft._constants import (
    CONTROL_SURFACE_WAIT_INTERVAL,
    QUEUE_CHILDREN_RESERVED_SUFFIX,
    QUEUE_INTERNAL_RESERVED_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    SPAWN_SUBMISSION_RECONCILIATION_TIMEOUT,
    WEFT_CHILD_SPAWN_REQUESTS_QUEUE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import WeftContext
from weft.core import manager_runtime
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.queue_window import iter_broker_queue_json_entries, queue_broker
from weft.core.task_evidence import TASK_HISTORY_MAX_MESSAGE_ID
from weft.core.task_state import read_task_state_snapshot, task_state_queue_name
from weft.helpers import closing_queue_iterator
from weft.helpers.message_ids import is_task_tid

_spawn_reconciliation_static_queue_specs: Final[tuple[tuple[str, bool], ...]] = (
    (WEFT_GLOBAL_LOG_QUEUE, True),
    (WEFT_SPAWN_REQUESTS_QUEUE, True),
    (WEFT_CHILD_SPAWN_REQUESTS_QUEUE, True),
    (WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE, True),
    (WEFT_SERVICES_REGISTRY_QUEUE, True),
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
    broker: Any | None = None,
) -> bool:
    with queue_broker(context, queue_name, broker=broker) as db:
        return (
            db.peek_one(
                queue_name,
                exact_timestamp=message_timestamp,
                with_timestamps=True,
            )
            is not None
        )


def _mapping_exists_for_tid(
    context: WeftContext, tid: str, *, broker: Any | None = None
) -> bool:
    """Use valid task-local runtime evidence for spawn proof [MF-6]."""

    return read_task_state_snapshot(context, tid, broker=broker) is not None


@dataclass(slots=True)
class _SpawnLogObservation:
    """One fallback observer's completely inspected range and folded proof [MF-1]."""

    inspected_through: int | None = None
    task_seen: bool = False
    latest_child_event: tuple[int, str, str | None] | None = None


def _inspect_task_log_for_tid(
    context: WeftContext,
    tid: str,
    *,
    broker: Any | None = None,
    observation: _SpawnLogObservation | None = None,
) -> SpawnSubmissionReconciliation | None:
    observation = observation if observation is not None else _SpawnLogObservation()
    since = (
        observation.inspected_through
        if observation.inspected_through is not None
        else int(tid) - 1
    )
    with queue_broker(context, WEFT_GLOBAL_LOG_QUEUE, broker=broker) as db:
        horizon = db.latest_pending_timestamp(WEFT_GLOBAL_LOG_QUEUE)
        if horizon is not None and horizon > since:
            # Commit progress only after the complete strict range succeeds.
            task_seen = observation.task_seen
            latest_child_event = observation.latest_child_event
            with closing_queue_iterator(
                iter_broker_queue_json_entries(
                    db,
                    WEFT_GLOBAL_LOG_QUEUE,
                    since_timestamp=since,
                    before_timestamp=(
                        None if horizon == TASK_HISTORY_MAX_MESSAGE_ID else horizon + 1
                    ),
                    strict=True,
                )
            ) as entries:
                for payload, timestamp in entries:
                    if payload.get("tid") == tid:
                        task_seen = True
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
                            error if isinstance(error, str) else None,
                        )
            observation.inspected_through = horizon
            observation.task_seen = task_seen
            observation.latest_child_event = latest_child_event

    if observation.task_seen:
        return SpawnSubmissionReconciliation(outcome="spawned", tid=tid)
    if observation.latest_child_event is None:
        return None
    _timestamp, event, error = observation.latest_child_event
    if event == "task_spawned":
        return SpawnSubmissionReconciliation(outcome="spawned", tid=tid)
    return SpawnSubmissionReconciliation(outcome="rejected", tid=tid, error=error)


def _find_reserved_spawn_request_queue(
    context: WeftContext,
    *,
    message_timestamp: int,
    broker: Any | None = None,
) -> str | None:
    for reserved_queue in _reserved_spawn_request_queue_names(context, broker=broker):
        if _queue_contains_exact_message(
            context,
            reserved_queue,
            message_timestamp=message_timestamp,
            broker=broker,
        ):
            return reserved_queue
    return None


def _reserved_spawn_request_queue_names(
    context: WeftContext, *, broker: Any | None = None
) -> tuple[str, ...]:
    queue_names: list[str] = []
    for record in manager_runtime.list_manager_records(
        context,
        include_stopped=True,
        canonical_only=True,
        prune_stale=False,
        broker=broker,
    ):
        manager_tid = record.get("tid")
        if not isinstance(manager_tid, str) or not manager_tid:
            continue
        queue_names.extend(
            f"T{manager_tid}.{suffix}"
            for suffix in (
                QUEUE_RESERVED_SUFFIX,
                QUEUE_CHILDREN_RESERVED_SUFFIX,
                QUEUE_INTERNAL_RESERVED_SUFFIX,
            )
        )
    return tuple(sorted(set(queue_names)))


def _spawn_reconciliation_queue_specs(
    context: WeftContext,
    tid: str,
    *,
    broker: Any | None = None,
) -> tuple[tuple[str, bool], ...]:
    return (
        (((task_state_queue_name(tid), True),) if is_task_tid(tid) else ())
        + _spawn_reconciliation_static_queue_specs
        + tuple(
            (queue_name, True)
            for queue_name in _reserved_spawn_request_queue_names(
                context, broker=broker
            )
        )
    )


@contextmanager
def _open_spawn_reconciliation_monitor(
    context: WeftContext,
    queue_specs: tuple[tuple[str, bool], ...],
) -> Iterator[tuple[list[Queue], QueueChangeMonitor]]:
    """Close the watcher before every queue, including construction failure."""
    with ExitStack() as resources:
        queues = [
            resources.enter_context(context.queue(queue_name, persistent=persistent))
            for queue_name, persistent in queue_specs
        ]
        monitor = QueueChangeMonitor(queues, config=context.config)
        resources.callback(monitor.close)
        yield queues, monitor


def _reconcile_submitted_spawn_once(
    context: WeftContext,
    tid: str,
    *,
    broker: Any | None = None,
    observation: _SpawnLogObservation | None = None,
) -> SpawnSubmissionReconciliation:
    message_timestamp = int(tid)

    if _mapping_exists_for_tid(context, tid, broker=broker):
        return SpawnSubmissionReconciliation(outcome="spawned", tid=tid)

    log_result = _inspect_task_log_for_tid(
        context, tid, broker=broker, observation=observation
    )
    if log_result is not None:
        return log_result

    for source_queue in (
        WEFT_SPAWN_REQUESTS_QUEUE,
        WEFT_CHILD_SPAWN_REQUESTS_QUEUE,
        WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    ):
        if _queue_contains_exact_message(
            context,
            source_queue,
            message_timestamp=message_timestamp,
            broker=broker,
        ):
            return SpawnSubmissionReconciliation(outcome="queued", tid=tid)

    reserved_queue = _find_reserved_spawn_request_queue(
        context,
        message_timestamp=message_timestamp,
        broker=broker,
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
    reserved_is_terminal: bool = True,
    queued_is_terminal: bool = True,
) -> SpawnSubmissionReconciliation:
    """Classify a previously submitted spawn request using durable state only.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-6]
    """

    deadline = time.monotonic() + max(timeout, 0.0)
    last_reserved: SpawnSubmissionReconciliation | None = None
    observation = _SpawnLogObservation()
    with (
        context.session(),
        context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log_queue,
        ExitStack() as monitor_resources,
    ):
        with log_queue.get_connection() as broker:
            queue_specs = _spawn_reconciliation_queue_specs(context, tid, broker=broker)
        _monitor_queues, monitor = monitor_resources.enter_context(
            _open_spawn_reconciliation_monitor(context, queue_specs)
        )
        while True:
            with log_queue.get_connection() as broker:
                result = _reconcile_submitted_spawn_once(
                    context, tid, broker=broker, observation=observation
                )
            if result.outcome != "unknown":
                if result.outcome == "reserved" and not reserved_is_terminal:
                    last_reserved = result
                elif result.outcome == "queued" and not queued_is_terminal:
                    pass
                else:
                    return result

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if last_reserved is not None:
                    return last_reserved
                return result

            with log_queue.get_connection() as broker:
                current_specs = _spawn_reconciliation_queue_specs(
                    context, tid, broker=broker
                )
            if current_specs != queue_specs:
                monitor_resources.close()
                queue_specs = current_specs
                _monitor_queues, monitor = monitor_resources.enter_context(
                    _open_spawn_reconciliation_monitor(context, queue_specs)
                )
                continue

            monitor.wait(min(remaining, max(poll_interval, 0.0)))
