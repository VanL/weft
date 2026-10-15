"""Shared task event iteration helpers for CLI and Python clients.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/09-Implementation_Plan.md [IP-1.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]
- docs/specifications/10-CLI_Interface.md [CLI-1.2]
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import asdict
from typing import Any

import weft.commands.tasks as task_ops
from simplebroker import Queue
from weft._constants import (
    TASK_HISTORY_MAX_MESSAGE_ID,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.commands.types import TaskEvent, TaskSnapshot
from weft.context import WeftContext
from weft.core.outbox import decode_result_payload
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.queue_window import (
    iter_broker_queue_entries,
    iter_broker_queue_json_entries,
)
from weft.core.task_evidence import (
    TaskEvidenceSnapshot,
    read_task_history,
    terminal_status_from_event,
)
from weft.core.task_state import task_state_queue_name
from weft.core.taskspec import TaskSpec
from weft.helpers import tid_short_form

from ._boundary import typed_command_errors
from ._resources import command_resource_scope
from ._result_wait import (
    ResultMaterialization,
    _accepted_result_materialization,
    _acquire_result_observation,
    _classify_result_observation,
    _CollectedResult,
    _result_observation_names,
    _result_stream_payload,
    append_public_value,
    observation_cancelled,
    result_observation,
)
from ._streaming import aggregate_public_outputs, process_outbox_message
from .result import _iter_task_result, _task_result_event
from .submission import normalize_tid


def _task_event_type(payload: dict[str, object]) -> str:
    event = payload.get("event")
    if isinstance(event, str) and event:
        return event
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    return "task_event"


def _raise_follow_timeout(
    *,
    tid: str,
    operation: str,
    timeout: float | None,
) -> None:
    raise TimeoutError(
        f"Timed out after {timeout} seconds waiting for task {tid} {operation}"
    )


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


def _peek_result_value(rows: Sequence[tuple[str, int]]) -> tuple[Any, bool]:
    """Assemble a read-only output window, including JSON null (Spec: [MF-5])."""
    stream_buffer: list[str] = []
    result_values: list[Any] = []
    for raw_payload, _timestamp in rows:
        final, value = process_outbox_message(
            raw_payload,
            stream_buffer,
            emit_stream=False,
        )
        if final and value is not None:
            append_public_value(result_values, value, show_stderr=False)
    return aggregate_public_outputs(result_values), bool(result_values)


def _task_snapshot_event(
    context: WeftContext,
    normalized_tid: str,
    *,
    allow_outbox_completion: bool,
    broker: Any | None = None,
    accepted_taskspec_payload: dict[str, Any] | None = None,
    evidence: TaskEvidenceSnapshot | None = None,
) -> TaskEvent | None:
    record = task_ops.system_cmd.collect_known_tid_record(
        context, normalized_tid, broker=broker
    )
    internal = record.snapshot if record is not None else None
    if evidence is not None and evidence.terminal:
        internal = task_ops._task_snapshot_from_live_pong(
            normalized_tid,
            evidence,
            base_snapshot=internal,
            taskspec_payload=accepted_taskspec_payload,
        )
    snapshot = (
        task_ops._public_snapshot(internal, taskspec_payload=accepted_taskspec_payload)
        if internal is not None
        else None
    )
    if snapshot is None:
        snapshot = TaskSnapshot(
            tid=normalized_tid,
            tid_short=tid_short_form(normalized_tid),
            name=normalized_tid,
            status="unknown",
            return_code=None,
            started_at=None,
            completed_at=None,
            error=None,
            runtime_handle=None,
            metadata={},
            event="observation_metadata_unavailable",
            reconciliation={"classification": "observation_metadata_unavailable"},
        )
    if (
        not allow_outbox_completion
        and snapshot.reconciliation is not None
        and snapshot.reconciliation.get("classification") == "result_without_terminal"
    ):
        # Unknown task type cannot turn a work-item value into task completion.
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


@typed_command_errors
def iter_task_events(
    context: WeftContext,
    tid: str,
    *,
    follow: bool = False,
    timeout: float | None = None,
) -> Iterator[TaskEvent]:
    """Yield raw lifecycle events for one task."""

    normalized_tid = normalize_tid(tid)
    deadline = task_ops._deadline_from_timeout(timeout)
    with result_observation(context, [WEFT_GLOBAL_LOG_QUEUE]) as observation:
        queues, monitor = observation
        log_queue = queues[WEFT_GLOBAL_LOG_QUEUE]
        last_timestamp: int | None = None
        terminal_seen = False

        while True:
            with log_queue.get_connection() as broker:
                rows, last_timestamp = read_task_history(
                    context,
                    normalized_tid,
                    broker=broker,
                    after_timestamp=last_timestamp,
                )
            saw_event = bool(rows)
            for payload, timestamp in rows:
                yield TaskEvent(
                    tid=normalized_tid,
                    event_type=_task_event_type(payload),
                    timestamp=timestamp,
                    payload=payload,
                )
                if terminal_status_from_event(payload) in TERMINAL_TASK_STATUSES:
                    terminal_seen = True

            if terminal_seen or not follow:
                return
            if task_ops._deadline_expired(deadline):
                _raise_follow_timeout(
                    tid=normalized_tid,
                    operation="events",
                    timeout=timeout,
                )
            if not saw_event:
                remaining = task_ops._remaining_timeout(deadline)
                monitor.wait(remaining)


@typed_command_errors
def follow_task_events(
    context: WeftContext,
    tid: str,
    *,
    timeout: float | None = None,
    _accepted_taskspec: TaskSpec | None = None,
) -> Iterator[TaskEvent]:
    """Yield raw events followed by one synthetic final result event."""

    normalized_tid = normalize_tid(tid)
    for item in _iter_task_result(
        context,
        normalized_tid,
        timeout=timeout,
        _accepted_taskspec=_accepted_taskspec,
        _follow_lifecycle=True,
    ):
        if isinstance(item, TaskEvent):
            yield item
        else:
            yield _task_result_event(item)


def _terminal_evidence_identity(
    evidence: TaskEvidenceSnapshot | None,
) -> tuple[str, str, str, int | None, str | None] | None:
    """Compare semantic proof without renewing diagnostic observation clocks [MF-5]."""
    if evidence is None:
        return None
    return (
        evidence.source,
        evidence.classification,
        evidence.status,
        evidence.completed_at,
        evidence.error,
    )


def _open_realtime_routes(
    context: WeftContext,
    outbox_name: str,
    ctrl_out_name: str,
    log_queue: Queue,
    *,
    cancel_event: Any | None = None,
    queue_names: Sequence[str] | None = None,
) -> tuple[ExitStack, Queue, Queue, QueueChangeMonitor]:
    """Acquire a complete observation subscription, unwinding partial failures.

    The caller must keep the context's existing session open throughout.

    Spec: docs/specifications/04-SimpleBroker_Integration.md [SB-0.4];
        docs/specifications/05-Message_Flow_and_State.md [MF-5].
    """
    with command_resource_scope() as resources:
        # Borrow the existing session, but give each subscription its own log
        # handle: overlapping fallback watchers each lend a distinct stop Event.
        watched_log = context.queue(log_queue.name, persistent=True)
        resources.callback(watched_log.close)
        queues, monitor = resources.enter_context(
            result_observation(
                context,
                queue_names
                if queue_names is not None
                else [outbox_name, ctrl_out_name],
                borrowed_queues=[watched_log],
                cancel_event=cancel_event,
            )
        )
        return resources.pop_all(), queues[outbox_name], queues[ctrl_out_name], monitor


@typed_command_errors
def iter_task_realtime_events(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-107] exception
    context: WeftContext,
    tid: str,
    *,
    follow: bool = True,
    cancel_event: Any | None = None,
    timeout: float | None = None,
    _accepted_taskspec: TaskSpec | None = None,
) -> Iterator[TaskEvent]:
    """Yield read-only browser-oriented task events.

    The iterator never consumes result or stream queues. It peeks all queues so
    HTTP/SSE/WS diagnostics do not mutate the underlying task result surface.

    Cancellation is checked before reads and after activity/deadline wakes.
    An ordinary `is_set()` flag cannot wake an idle indefinite wait; supply a
    timeout to bound that wait. The existing Django iterator owner also lends
    a private direct-wake registration for prompt idle disconnect cleanup.

    Completion is decided by the shared task evidence classification rather than
    by a private priority table: terminal task-log rows end the stream, and so
    does task-local terminal proof (typed terminal ctrl_out, or eligible final
    one-shot outbox evidence) that only becomes visible after the startup
    snapshot. Streaming outbox frames are observation, never completion, so
    they are never offered to the final one-shot outbox fallback, and a held
    manager ``wrapper_lost`` verdict still yields to task-authored terminal
    proof that arrives during the terminal grace.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-5];
    docs/specifications/09-Implementation_Plan.md [IP-1.1]
    """

    normalized_tid = normalize_tid(tid)
    if observation_cancelled(cancel_event):
        return
    deadline = task_ops._deadline_from_timeout(timeout) if follow else time.monotonic()
    materialized = (
        _accepted_result_materialization(_accepted_taskspec)
        if _accepted_taskspec is not None
        else ResultMaterialization(None)
    )
    ctrl_out_name = (
        materialized.config.ctrl_out
        if materialized.config
        else f"T{normalized_tid}.ctrl_out"
    )
    outbox_name = materialized.config.outbox if materialized.config else ctrl_out_name
    snapshot_emitted = False
    collected = _CollectedResult(deadline=deadline, scope="task")
    last_outbox_timestamp: int | None = None
    last_ctrl_timestamp: int | None = None
    outbox_stream_frames_seen = False

    with command_resource_scope() as resources:
        resources.enter_context(context.session())
        log_queue = context.queue(
            task_state_queue_name(normalized_tid), persistent=True
        )
        resources.callback(log_queue.close)
        watched_names = _result_observation_names(normalized_tid, materialized)
        route_resources, outbox_queue, ctrl_queue, monitor = _open_realtime_routes(
            context,
            outbox_name,
            ctrl_out_name,
            log_queue,
            cancel_event=cancel_event,
            queue_names=watched_names,
        )
        resources.enter_context(route_resources)

        while not observation_cancelled(cancel_event):
            now = time.monotonic()
            with log_queue.get_connection() as broker:
                materialized, _fresh = _acquire_result_observation(
                    context, normalized_tid, materialized, log_queue, broker, now=now
                )
            new_names = _result_observation_names(normalized_tid, materialized)
            new_control = (
                materialized.config.ctrl_out if materialized.config else ctrl_out_name
            )
            new_outbox = (
                materialized.config.outbox if materialized.config else new_control
            )
            if new_names != watched_names:
                replacement, new_outbox_queue, new_ctrl_queue, new_monitor = (
                    _open_realtime_routes(
                        context,
                        new_outbox,
                        new_control,
                        log_queue,
                        cancel_event=cancel_event,
                        queue_names=new_names,
                    )
                )
                resources.enter_context(replacement)
                route_resources.close()
                route_resources = replacement
                outbox_queue, ctrl_queue, monitor = (
                    new_outbox_queue,
                    new_ctrl_queue,
                    new_monitor,
                )
                if new_outbox != outbox_name:
                    last_outbox_timestamp = None
                    outbox_stream_frames_seen = False
                    collected = _CollectedResult(deadline=deadline, scope="task")
                if new_control != ctrl_out_name:
                    last_ctrl_timestamp = None
                outbox_name, ctrl_out_name = new_outbox, new_control
                watched_names = new_names
            if observation_cancelled(cancel_event):
                return

            taskspec_payload = materialized.taskspec_payload
            streams_output = outbox_stream_frames_seen or bool(
                materialized.config and materialized.config.stream_output
            )
            with log_queue.get_connection() as broker:
                if not snapshot_emitted:
                    materialized = _classify_result_observation(
                        context,
                        normalized_tid,
                        materialized,
                        log_queue,
                        broker,
                        now=time.monotonic(),
                        allow_outbox_completion=not streams_output,
                    )
                    snapshot = _task_snapshot_event(
                        context,
                        normalized_tid,
                        allow_outbox_completion=(
                            materialized.config is not None
                            and materialized.config.mode == "one_shot"
                            and not streams_output
                        ),
                        broker=broker,
                        accepted_taskspec_payload=taskspec_payload,
                        evidence=materialized.terminal_evidence,
                    )
                else:
                    snapshot = None
            if observation_cancelled(cancel_event):
                return
            yielded = False
            if snapshot is not None:
                snapshot_emitted = True
                yielded = True
                yield snapshot
                if observation_cancelled(cancel_event):
                    return

            output_rows: list[tuple[str, int]] = []
            for queue, cursor, kind in (
                [
                    (outbox_queue, last_outbox_timestamp, "outbox"),
                    (ctrl_queue, last_ctrl_timestamp, "control"),
                ]
                if materialized.config is not None
                else []
            ):
                with log_queue.get_connection() as broker:
                    horizon: int | None = broker.latest_pending_timestamp(queue.name)
                    before = (
                        None
                        if horizon == TASK_HISTORY_MAX_MESSAGE_ID
                        else horizon + 1
                        if horizon is not None
                        else None
                    )
                    # Materialize a bounded window before yielding to a caller.
                    # A suspended iterator owns frames, never a broker operation.
                    if kind == "outbox":
                        output_rows = (
                            list(
                                iter_broker_queue_entries(
                                    broker,
                                    queue.name,
                                    before_timestamp=before,
                                    strict=True,
                                )
                            )
                            if horizon is not None
                            else []
                        )
                        rows = [
                            (payload, timestamp)
                            for body, timestamp in output_rows
                            if (cursor is None or timestamp > cursor)
                            and isinstance(payload := decode_result_payload(body), dict)
                        ]
                    else:
                        rows = (
                            list(
                                iter_broker_queue_json_entries(
                                    broker,
                                    queue.name,
                                    since_timestamp=cursor,
                                    before_timestamp=before,
                                    strict=True,
                                )
                            )
                            if horizon is not None
                            else []
                        )
                for payload, timestamp in rows:
                    if observation_cancelled(cancel_event):
                        return
                    if kind == "outbox":
                        last_outbox_timestamp = timestamp
                        if payload.get("type") == "stream":
                            outbox_stream_frames_seen = True
                    else:
                        last_ctrl_timestamp = timestamp
                    stream = payload.get("stream")
                    if payload.get("type") != "stream" or stream not in {
                        "stdout",
                        "stderr",
                    }:
                        continue
                    if kind == "control" and stream != "stderr":
                        continue
                    yielded = True
                    yield TaskEvent(
                        tid=normalized_tid,
                        event_type=str(stream),
                        timestamp=timestamp,
                        payload=_result_stream_payload(payload),
                    )

            if yielded:
                # A suspended iterator allows writers to commit. Inspect the
                # next suffix and drain new frames before deciding from that loan.
                continue
            if observation_cancelled(cancel_event):
                return
            with log_queue.get_connection() as broker:
                value, final_output_seen = _peek_result_value(output_rows)
                if observation_cancelled(cancel_event):
                    return
                terminal_before_drain = _terminal_evidence_identity(
                    materialized.terminal_evidence
                )
                materialized = _classify_result_observation(
                    context,
                    normalized_tid,
                    materialized,
                    log_queue,
                    broker,
                    now=time.monotonic(),
                    allow_outbox_completion=(
                        not outbox_stream_frames_seen
                        and not bool(
                            materialized.config and materialized.config.stream_output
                        )
                    ),
                )
            if observation_cancelled(cancel_event):
                return
            if (
                _terminal_evidence_identity(materialized.terminal_evidence)
                != terminal_before_drain
            ):
                # Local terminal proof may arrive after the preceding stream
                # peek. Take its fresh last drain before settling that verdict.
                continue
            now = time.monotonic()
            collected.observe_readonly(
                materialized,
                final_output_seen=final_output_seen,
                stream_framed=(
                    outbox_stream_frames_seen
                    or bool(materialized.config and materialized.config.stream_output)
                ),
                now=now,
            )
            terminal = materialized.terminal_evidence
            decision = collected.settle(materialized, now=now)
            if decision.action == "result":
                status = decision.status or "unknown"
                stamp = terminal.observed_at if terminal is not None else None
                payload = {"tid": normalized_tid, "status": status}
                if decision.error is not None:
                    payload["error"] = decision.error
                yield TaskEvent(
                    tid=normalized_tid,
                    event_type="state",
                    timestamp=stamp or time.time_ns(),
                    payload=_state_payload(payload),
                )
                if observation_cancelled(cancel_event):
                    return
                yield TaskEvent(
                    tid=normalized_tid,
                    event_type="result",
                    timestamp=stamp or time.time_ns(),
                    payload={"status": status, "value": value, "error": decision.error},
                )
                if observation_cancelled(cancel_event):
                    return
                yield TaskEvent(
                    tid=normalized_tid,
                    event_type="end",
                    timestamp=time.time_ns(),
                    payload={"status": status},
                )
                return
            if not follow:
                return
            if decision.action == "timeout":
                _raise_follow_timeout(
                    tid=normalized_tid, operation="realtime events", timeout=timeout
                )
            if observation_cancelled(cancel_event):
                return
            wait_until = decision.wait_until
            monitor.wait(
                None if wait_until is None else max(0.0, wait_until - time.monotonic())
            )
