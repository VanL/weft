"""Shared helpers for one-shot task result waiting.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.3], [IMPL.1]
"""

from __future__ import annotations

import time
from typing import Any

from weft._constants import (
    RESULT_SURFACE_WAIT_INTERVAL,
    WEFT_COMPLETED_RESULT_GRACE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
    WRAPPER_LOST_ERROR,
)
from weft.context import WeftContext
from weft.core.queue_wait import QueueChangeMonitor

from ._streaming import (
    DecodedOutboxValue,
    aggregate_public_outputs,
    drain_available_outbox_values,
    handle_ctrl_stream,
    poll_log_events,
)
from .task_evidence import (
    coerce_terminal_envelope,
    select_terminal_envelope,
    terminal_error_message,
    terminal_status_from_event,
)


def append_public_value(
    values: list[Any],
    output: DecodedOutboxValue,
    *,
    show_stderr: bool,
) -> None:
    """Append a caller-facing value after optional stderr selection."""
    if output.emitted:
        return
    value = output.value
    if show_stderr and isinstance(value, dict) and "stderr" in value:
        values.append(value.get("stderr") or "")
        return
    values.append(value)


def effective_result_surface_wait_interval(timeout: float | None) -> float:
    """Return a poll interval that preserves useful turns inside short timeouts.

    ``weft result`` can spend part of a caller timeout budget materializing
    custom queue names before it can wait on the actual result surface. A fixed
    100ms poll slice is cheap for long waits, but it is too coarse when the
    caller timeout is small because two polling phases can consume most of the
    budget before the next turn. Keep the default ceiling for normal waits,
    while scaling down to roughly ten turns across short budgets.
    """

    if timeout is None or timeout <= 0:
        return RESULT_SURFACE_WAIT_INTERVAL
    return min(RESULT_SURFACE_WAIT_INTERVAL, max(0.01, timeout / 10.0))


def _is_manager_wrapper_lost_envelope(payload: dict[str, Any]) -> bool:
    """Return whether *payload* is the manager's fallback wrapper-lost proof."""

    return (
        payload.get("source") == "manager"
        and payload.get("error") == WRAPPER_LOST_ERROR
    )


def drain_ctrl_out_stream_messages(
    ctrl_queue: Any,
    *,
    tid: str,
) -> list[tuple[dict[str, Any], int]]:
    """Render non-terminal ctrl_out messages while retaining terminal proof.

    Terminal ctrl_out envelopes are a task-local lifecycle evidence surface. The
    result waiter may observe them, but it must not consume them as ordinary
    stream/control output because later status surfaces may need the same proof
    if the global task log races or is unavailable.

    Returns ``(envelope, message_id)`` candidates so callers can apply the shared
    source-precedence selection (Spec: [MF-5]); the broker ``message_id`` is the
    timestamp the selector uses for same-source latest-wins.
    """

    terminal_candidates: list[tuple[dict[str, Any], int]] = []
    for entry in ctrl_queue.peek_generator(with_timestamps=True):
        if not isinstance(entry, tuple) or len(entry) < 2:
            continue
        ctrl_payload, message_id = entry[0], entry[1]
        raw = str(ctrl_payload)
        terminal_envelope = coerce_terminal_envelope(raw, tid=tid)
        if terminal_envelope is not None:
            terminal_candidates.append((terminal_envelope, int(message_id)))
            continue
        handle_ctrl_stream(raw)
        ctrl_queue.delete(message_id=message_id)
    return terminal_candidates


def await_one_shot_result(
    context: WeftContext,
    tid: str,
    *,
    outbox_name: str,
    ctrl_out_name: str | None,
    timeout: float | None,
    show_stderr: bool,
    emit_stream: bool = False,
    initial_log_last_timestamp: int | None = None,
    initial_terminal_status: str | None = None,
    initial_error_message: str | None = None,
) -> tuple[str, Any | None, str | None]:
    """Wait for a one-shot task to publish a terminal result."""
    outbox_queue = context.queue(outbox_name, persistent=True)
    ctrl_queue = (
        context.queue(ctrl_out_name, persistent=False) if ctrl_out_name else None
    )
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    monitor = QueueChangeMonitor(
        [queue for queue in (outbox_queue, ctrl_queue, log_queue) if queue is not None],
        config=context.config,
    )

    log_last_timestamp: int | None = initial_log_last_timestamp
    stream_buffer: list[str] = []
    status = "running"
    result_values: list[Any] = []
    result_value: Any | None = None
    error_message: str | None = initial_error_message
    pending_wrapper_lost_error: str | None = None
    emitted_result_seen = False
    completed_at: float | None = (
        time.monotonic() if initial_terminal_status == "completed" else None
    )
    single_result_seen_at: float | None = None
    materialized_completed = initial_terminal_status == "completed"
    if initial_terminal_status is not None and initial_terminal_status != "completed":
        status = initial_terminal_status

    deadline = None
    if timeout is not None:
        deadline = time.monotonic() + max(0.0, timeout)
    poll_interval = effective_result_surface_wait_interval(timeout)

    try:
        while True:
            while True:
                if ctrl_queue is None:
                    break
                terminal_candidates = drain_ctrl_out_stream_messages(
                    ctrl_queue,
                    tid=tid,
                )
                selected = select_terminal_envelope(terminal_candidates)
                if selected is not None:
                    terminal_envelope = selected[0]
                    event_status = terminal_status_from_event(terminal_envelope)
                    if event_status is not None and _is_manager_wrapper_lost_envelope(
                        terminal_envelope
                    ):
                        pending_wrapper_lost_error = terminal_error_message(
                            terminal_envelope,
                            event_status,
                        )
                    elif event_status == "completed":
                        if completed_at is None:
                            completed_at = time.monotonic()
                    elif event_status is not None:
                        status = event_status
                        error_message = terminal_error_message(
                            terminal_envelope,
                            event_status,
                        )
                break

            ready_values, drained_outbox = drain_available_outbox_values(
                outbox_queue,
                stream_buffer,
                emit_stream=emit_stream,
            )
            if ready_values:
                if (
                    single_result_seen_at is None
                    and not result_values
                    and len(ready_values) == 1
                ):
                    single_result_seen_at = time.monotonic()
                else:
                    single_result_seen_at = None
            for output in ready_values:
                if output.emitted:
                    emitted_result_seen = True
                append_public_value(
                    result_values,
                    output,
                    show_stderr=show_stderr,
                )
            if pending_wrapper_lost_error is not None and (
                result_values or emitted_result_seen
            ):
                result_value = aggregate_public_outputs(result_values)
                status = "completed"
                break
            if materialized_completed and (result_values or emitted_result_seen):
                result_value = aggregate_public_outputs(result_values)
                status = "completed"
                break
            if drained_outbox:
                continue

            events, log_last_timestamp = poll_log_events(
                log_queue,
                log_last_timestamp,
                tid,
            )
            for event_payload, _ts in events:
                event_status = terminal_status_from_event(event_payload)
                if event_status is None:
                    continue
                if event_status == "completed":
                    if completed_at is None:
                        completed_at = time.monotonic()
                    continue
                status = event_status
                error_message = terminal_error_message(event_payload, event_status)
                break

            if status != "running":
                break

            if pending_wrapper_lost_error is not None:
                status = "failed"
                error_message = pending_wrapper_lost_error
                break

            if completed_at is not None and (
                time.monotonic() - completed_at >= WEFT_COMPLETED_RESULT_GRACE_SECONDS
            ):
                late_values, _ = drain_available_outbox_values(
                    outbox_queue,
                    stream_buffer,
                    emit_stream=emit_stream,
                )
                for output in late_values:
                    if output.emitted:
                        emitted_result_seen = True
                    append_public_value(
                        result_values,
                        output,
                        show_stderr=show_stderr,
                    )
                result_value = aggregate_public_outputs(result_values)
                status = "completed"
                break

            # If one final result payload is already visible, the caller
            # already has an unambiguous public result shape. Treat a quiet
            # grace window the same way we treat a late outbox after completion
            # when the terminal log event races or never becomes observable.
            if (
                completed_at is None
                and single_result_seen_at is not None
                and not stream_buffer
                and (result_values or emitted_result_seen)
                and time.monotonic() - single_result_seen_at
                >= WEFT_COMPLETED_RESULT_GRACE_SECONDS
            ):
                result_value = aggregate_public_outputs(result_values)
                status = "completed"
                break

            if deadline is not None and time.monotonic() >= deadline:
                if (
                    single_result_seen_at is not None
                    and (result_values or emitted_result_seen)
                    and not stream_buffer
                ):
                    result_value = aggregate_public_outputs(result_values)
                    status = "completed"
                    break
                status = "timeout"
                error_message = (
                    f"Timed out after {timeout} seconds waiting for task {tid}"
                )
                break

            wait_timeout: float | None = None
            if deadline is not None:
                wait_timeout = max(0.0, deadline - time.monotonic())
            if completed_at is not None:
                grace_remaining = max(
                    0.0,
                    WEFT_COMPLETED_RESULT_GRACE_SECONDS
                    - (time.monotonic() - completed_at),
                )
                wait_timeout = (
                    grace_remaining
                    if wait_timeout is None
                    else min(wait_timeout, grace_remaining)
                )
            if completed_at is None and single_result_seen_at is not None:
                output_grace_remaining = max(
                    0.0,
                    WEFT_COMPLETED_RESULT_GRACE_SECONDS
                    - (time.monotonic() - single_result_seen_at),
                )
                wait_timeout = (
                    output_grace_remaining
                    if wait_timeout is None
                    else min(wait_timeout, output_grace_remaining)
                )
            wait_timeout = (
                poll_interval
                if wait_timeout is None
                else min(wait_timeout, poll_interval)
            )
            monitor.wait(wait_timeout)
    finally:
        monitor.close()
        outbox_queue.close()
        if ctrl_queue is not None:
            ctrl_queue.close()
        log_queue.close()

    return status, result_value, error_message
