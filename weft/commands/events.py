"""Shared task event iteration helpers for CLI and Python clients.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/09-Implementation_Plan.md [IP-1.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]
- docs/specifications/10-CLI_Interface.md [CLI-1.2]
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import asdict
from typing import Any

import weft.commands.tasks as task_ops
from simplebroker import Queue
from weft._constants import (
    TERMINAL_TASK_STATUSES,
    WEFT_COMPLETED_RESULT_GRACE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
    WRAPPER_LOST_ERROR,
)
from weft._exceptions import CommandTimeoutError
from weft.commands.types import TaskEvent
from weft.context import WeftContext
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.task_evidence import (
    TaskEvidenceSnapshot,
    peek_terminal_ctrl_out_evidence,
    queue_names_for_tid,
    task_local_terminal_evidence,
)
from weft.helpers import iter_queue_entries, iter_queue_json_entries

from ._result_wait import (
    append_public_value,
    terminal_error_message,
    terminal_status_from_event,
)
from ._streaming import aggregate_public_outputs, process_outbox_message
from .result import (
    _await_result_materialization,
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


def _terminal_payload_from_evidence(
    evidence: TaskEvidenceSnapshot,
    *,
    tid: str,
) -> dict[str, Any]:
    """Render shared terminal evidence as a realtime terminal event payload.

    The realtime iterator does not re-derive lifecycle meaning; it renders the
    verdict already produced by the shared classifier so every observation
    surface agrees on when a task has finished.

    Args:
        evidence: Terminal snapshot returned by the shared evidence helpers.
        tid: Normalized task identifier the iterator is following.

    Returns:
        A task-log shaped payload accepted by ``_state_payload``,
        ``terminal_status_from_event`` and ``terminal_error_message``.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-5]
    """

    payload: dict[str, Any] = {"tid": tid, "status": evidence.status}
    if evidence.error is not None:
        payload["error"] = evidence.error
    return payload


def _is_wrapper_lost_verdict(payload: dict[str, Any] | None) -> bool:
    """Whether a held terminal verdict is only the manager wrapper-lost failsafe.

    The manager writes ``wrapper_lost`` when no task proof was visible at write
    time, so a task-authored terminal envelope that becomes durable afterwards
    must still be able to replace it.

    Args:
        payload: Terminal event payload currently held by the iterator, if any.

    Returns:
        ``True`` when the held verdict carries the wrapper-lost error text.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-5]
    """

    if payload is None:
        return False
    status = terminal_status_from_event(payload)
    if status is None:
        return False
    return terminal_error_message(payload, status) == WRAPPER_LOST_ERROR


def _task_streams_output(taskspec_payload: dict[str, Any] | None) -> bool:
    """Whether a logged TaskSpec payload publishes its output as stream frames.

    Args:
        taskspec_payload: Logged TaskSpec payload for the observed task, if any.

    Returns:
        ``True`` when the task writes streaming frames to its outbox.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-5]
    """

    if not isinstance(taskspec_payload, dict):
        return False
    spec = taskspec_payload.get("spec")
    if not isinstance(spec, dict):
        return False
    return bool(spec.get("stream_output"))


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
    *,
    allow_outbox_completion: bool,
) -> TaskEvent | None:
    snapshot = task_ops.task_snapshot(
        normalized_tid,
        context=context,
    )
    if snapshot is None:
        return None
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
    resources = ExitStack()
    try:
        log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True)
        resources.callback(log_queue.close)
        monitor = QueueChangeMonitor([log_queue], config=context.config)
        resources.callback(monitor.close)
        last_timestamp: int | None = int(normalized_tid) - 1
        terminal_seen = False

        while True:
            saw_event = False
            max_scanned_timestamp: int | None = None
            since_timestamp = None if last_timestamp is None else last_timestamp + 1
            for payload, timestamp in iter_queue_json_entries(
                log_queue,
                since_timestamp=since_timestamp,
            ):
                if max_scanned_timestamp is None or timestamp > max_scanned_timestamp:
                    max_scanned_timestamp = timestamp
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

            if max_scanned_timestamp is not None:
                last_timestamp = max_scanned_timestamp
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
                wait_timeout = 0.1 if remaining is None else min(0.1, remaining)
                monitor.wait(wait_timeout)
    finally:
        resources.close()


def follow_task_events(
    context: WeftContext,
    tid: str,
    *,
    timeout: float | None = None,
) -> Iterator[TaskEvent]:
    """Yield raw events followed by one synthetic final result event."""

    normalized_tid = normalize_tid(tid)
    deadline = task_ops._deadline_from_timeout(timeout)
    event_timeout: TimeoutError | None = None
    try:
        yield from iter_task_events(
            context,
            normalized_tid,
            follow=True,
            timeout=timeout,
        )
    except TimeoutError as exc:
        event_timeout = exc

    remaining = task_ops._remaining_timeout(deadline)
    if remaining is not None and remaining <= 0:
        if event_timeout is None:
            _raise_follow_timeout(
                tid=normalized_tid,
                operation="result",
                timeout=timeout,
            )
        remaining = WEFT_COMPLETED_RESULT_GRACE_SECONDS
    try:
        result = await_task_result(
            context,
            normalized_tid,
            timeout=remaining,
        )
    except CommandTimeoutError as exc:
        if event_timeout is not None:
            raise event_timeout from exc
        if timeout is not None:
            _raise_follow_timeout(
                tid=normalized_tid,
                operation="result",
                timeout=timeout,
            )
        raise
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


def _open_realtime_routes(
    context: WeftContext,
    outbox_name: str,
    ctrl_out_name: str,
    log_queue: Queue,
) -> tuple[ExitStack, Queue, Queue, QueueChangeMonitor]:
    """Acquire a complete observation subscription, unwinding partial failures.

    Spec: docs/specifications/04-SimpleBroker_Integration.md [SB-0.4];
        docs/specifications/05-Message_Flow_and_State.md [MF-5].
    """
    with ExitStack() as resources:
        outbox = context.queue(outbox_name, persistent=True)
        resources.callback(outbox.close)
        control = context.queue(ctrl_out_name, persistent=True)
        resources.callback(control.close)
        monitor = QueueChangeMonitor(
            [outbox, control, log_queue], config=context.config
        )
        resources.callback(monitor.close)
        return resources.pop_all(), outbox, control, monitor


def iter_task_realtime_events(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-107] exception
    context: WeftContext,
    tid: str,
    *,
    follow: bool = True,
    cancel_event: Any | None = None,
    timeout: float | None = None,
) -> Iterator[TaskEvent]:
    """Yield read-only browser-oriented task events.

    The iterator never consumes result or stream queues. It peeks all queues so
    HTTP/SSE/WS diagnostics do not mutate the underlying task result surface.

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
    deadline = task_ops._deadline_from_timeout(timeout)
    materialization_timeout = 0.2 if follow else 0.0
    remaining = task_ops._remaining_timeout(deadline)
    if remaining is not None:
        materialization_timeout = min(materialization_timeout, remaining)
    materialized = _await_result_materialization(
        context,
        normalized_tid,
        timeout=materialization_timeout,
    )
    taskspec_payload = (
        materialized.taskspec_payload if materialized is not None else None
    )
    outbox_name, ctrl_out_name = queue_names_for_tid(
        normalized_tid,
        taskspec_payload,
    )

    snapshot_emitted = False
    snapshot_event = _task_snapshot_event(
        context, normalized_tid, allow_outbox_completion=taskspec_payload is not None
    )
    if snapshot_event is not None:
        if _is_cancelled(cancel_event):
            return
        yield snapshot_event
        snapshot_emitted = True

    resources = ExitStack()
    try:
        log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True)
        resources.callback(log_queue.close)
        route_resources, outbox_queue, ctrl_queue, monitor = _open_realtime_routes(
            context, outbox_name, ctrl_out_name, log_queue
        )
        resources.enter_context(route_resources)

        last_log_timestamp = (
            materialized.log_last_timestamp
            if materialized is not None
            else int(normalized_tid) - 1
        )
        last_outbox_timestamp: int | None = None
        last_ctrl_timestamp: int | None = None
        terminal_payload: dict[str, Any] | None = (
            materialized.terminal_event_payload if materialized is not None else None
        )
        terminal_observed_monotonic: float | None = (
            time.monotonic() if terminal_payload is not None else None
        )
        terminal_timestamp: int | None = (
            materialized.terminal_event_timestamp if materialized is not None else None
        )
        if (
            terminal_payload is None
            and materialized is not None
            and materialized.terminal_status is not None
        ):
            terminal_payload = {
                "tid": normalized_tid,
                "status": materialized.terminal_status,
            }
            if materialized.terminal_error_message is not None:
                terminal_payload["error"] = materialized.terminal_error_message
            terminal_timestamp = materialized.log_last_timestamp
        terminal_state_emitted = False
        if terminal_payload is None and snapshot_event is not None:
            snapshot_status = snapshot_event.payload.get("status")
            if snapshot_status in TERMINAL_TASK_STATUSES:
                terminal_payload = {
                    "tid": normalized_tid,
                    "status": snapshot_status,
                }
                # Carry the snapshot's error text so a startup verdict that is only
                # the manager wrapper-lost failsafe stays replaceable by a later
                # task-authored terminal envelope [MF-5].
                snapshot_error = snapshot_event.payload.get("error")
                if isinstance(snapshot_error, str) and snapshot_error:
                    terminal_payload["error"] = snapshot_error
                terminal_timestamp = snapshot_event.timestamp
                terminal_observed_monotonic = time.monotonic()

        # Terminal proof can land on the task-local queues after the startup
        # snapshot was taken. Re-run the shared classifier whenever those queues
        # change so realtime observation agrees with status and result [MF-5].
        evidence_scan_pending = True
        outbox_stream_frames_seen = False

        while not _is_cancelled(cancel_event):
            saw_event = False
            routes_changed = False

            outbox_since = (
                None if last_outbox_timestamp is None else last_outbox_timestamp + 1
            )
            for payload, timestamp in iter_queue_json_entries(
                outbox_queue,
                since_timestamp=outbox_since,
            ):
                last_outbox_timestamp = timestamp
                if payload.get("type") == "stream":
                    # Streaming output is observation, not completion proof, so
                    # it must never reach the one-shot outbox fallback [MF-5].
                    outbox_stream_frames_seen = True
                stream = payload.get("stream")
                if payload.get("type") != "stream" or stream not in {
                    "stdout",
                    "stderr",
                }:
                    evidence_scan_pending = True
                    continue
                saw_event = True
                if _is_cancelled(cancel_event):
                    return
                yield TaskEvent(
                    tid=normalized_tid,
                    event_type=str(stream),
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
                    evidence_scan_pending = True
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
                event_taskspec = payload.get("taskspec")
                if isinstance(event_taskspec, dict):
                    taskspec_payload = event_taskspec
                    evidence_scan_pending = True
                    new_outbox, new_control = queue_names_for_tid(
                        normalized_tid, taskspec_payload
                    )
                    if (new_outbox, new_control) != (outbox_name, ctrl_out_name):
                        replacement, new_outbox_queue, new_ctrl_queue, new_monitor = (
                            _open_realtime_routes(
                                context, new_outbox, new_control, log_queue
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
                        # Cursors and stream evidence belong to a queue, not the
                        # subscription. Replay only routes newly discovered here.
                        if new_outbox != outbox_name:
                            last_outbox_timestamp = None
                            outbox_stream_frames_seen = False
                        if new_control != ctrl_out_name:
                            last_ctrl_timestamp = None
                        outbox_name, ctrl_out_name = new_outbox, new_control
                        routes_changed = True
                if not snapshot_emitted:
                    snapshot_event = _task_snapshot_event(
                        context,
                        normalized_tid,
                        allow_outbox_completion=taskspec_payload is not None,
                    )
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
                    if terminal_observed_monotonic is None:
                        terminal_observed_monotonic = time.monotonic()
                    terminal_state_emitted = True

            if routes_changed:
                # Drain the new routes before terminal settlement or a finite
                # snapshot returns, including frames already durable at rebind.
                continue

            if evidence_scan_pending and (
                terminal_payload is None or _is_wrapper_lost_verdict(terminal_payload)
            ):
                evidence_scan_pending = False
                streams_output = outbox_stream_frames_seen or _task_streams_output(
                    taskspec_payload
                )
                evidence = (
                    peek_terminal_ctrl_out_evidence(
                        context,
                        tid=normalized_tid,
                        ctrl_out_name=ctrl_out_name,
                        taskspec_payload=taskspec_payload,
                    )
                    if streams_output or taskspec_payload is None
                    else task_local_terminal_evidence(
                        context,
                        tid=normalized_tid,
                        taskspec_payload=taskspec_payload,
                    )
                )
                if (
                    evidence is not None
                    and evidence.terminal
                    and (
                        terminal_payload is None
                        or evidence.classification != "wrapper_lost"
                    )
                ):
                    terminal_payload = _terminal_payload_from_evidence(
                        evidence,
                        tid=normalized_tid,
                    )
                    terminal_timestamp = evidence.observed_at
                    if terminal_observed_monotonic is None:
                        # Replacing a held verdict must not restart the grace.
                        terminal_observed_monotonic = time.monotonic()

            if terminal_payload is not None:
                if terminal_observed_monotonic is None:
                    terminal_observed_monotonic = time.monotonic()
                terminal_grace_remaining = (
                    terminal_observed_monotonic
                    + WEFT_COMPLETED_RESULT_GRACE_SECONDS
                    - time.monotonic()
                )
                if terminal_grace_remaining > 0 and not task_ops._deadline_expired(
                    deadline
                ):
                    remaining = task_ops._remaining_timeout(deadline)
                    wait_timeout = (
                        min(0.05, terminal_grace_remaining)
                        if remaining is None
                        else min(0.05, remaining, terminal_grace_remaining)
                    )
                    monitor.wait(wait_timeout)
                    continue
                terminal_status = (
                    terminal_status_from_event(terminal_payload) or "unknown"
                )
                if not terminal_state_emitted:
                    yield TaskEvent(
                        tid=normalized_tid,
                        event_type="state",
                        timestamp=terminal_timestamp or time.time_ns(),
                        payload=_state_payload(terminal_payload),
                    )
                    terminal_state_emitted = True
                if not snapshot_emitted:
                    snapshot_event = _task_snapshot_event(
                        context,
                        normalized_tid,
                        allow_outbox_completion=taskspec_payload is not None,
                    )
                    if snapshot_event is not None:
                        yield snapshot_event
                        snapshot_emitted = True
                result_value = _peek_result_value(context, outbox_name=outbox_name)
                if terminal_status == "completed" and result_value is None:
                    grace_deadline = (
                        time.monotonic() + WEFT_COMPLETED_RESULT_GRACE_SECONDS
                    )
                    while (
                        result_value is None
                        and time.monotonic() < grace_deadline
                        and not _is_cancelled(cancel_event)
                    ):
                        remaining = task_ops._remaining_timeout(deadline)
                        grace_remaining = max(0.0, grace_deadline - time.monotonic())
                        wait_timeout = (
                            min(0.05, grace_remaining)
                            if remaining is None
                            else min(0.05, remaining, grace_remaining)
                        )
                        monitor.wait(wait_timeout)
                        result_value = _peek_result_value(
                            context,
                            outbox_name=outbox_name,
                        )
                    if result_value is None and task_ops._deadline_expired(deadline):
                        _raise_follow_timeout(
                            tid=normalized_tid,
                            operation="realtime result",
                            timeout=timeout,
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
            if task_ops._deadline_expired(deadline):
                _raise_follow_timeout(
                    tid=normalized_tid,
                    operation="realtime events",
                    timeout=timeout,
                )
            if not saw_event:
                remaining = task_ops._remaining_timeout(deadline)
                wait_timeout = 0.1 if remaining is None else min(0.1, remaining)
                if monitor.wait(wait_timeout):
                    evidence_scan_pending = True
    finally:
        resources.close()
