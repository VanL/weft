"""Shared helpers for one-shot task result waiting.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.3], [IMPL.1]
"""

from __future__ import annotations

import time
from typing import Any

from simplebroker import Queue
from weft._constants import (
    WEFT_COMPLETED_RESULT_GRACE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.context import WeftContext

from ._streaming import (
    DecodedOutboxValue,
    aggregate_public_outputs,
    drain_available_outbox_values,
    handle_ctrl_stream,
    poll_log_events,
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


def terminal_status_from_event(payload: dict[str, Any]) -> str | None:
    """Return the public terminal status represented by a log event."""
    if payload.get("event") == "task_activity":
        return None
    status = payload.get("status")
    if not isinstance(status, str):
        taskspec = payload.get("taskspec")
        if isinstance(taskspec, dict):
            state = taskspec.get("state")
            if isinstance(state, dict):
                state_status = state.get("status")
                if isinstance(state_status, str):
                    status = state_status
    if status in {"completed", "failed", "timeout", "cancelled", "killed"}:
        return status
    return None


def terminal_error_message(payload: dict[str, Any], status: str) -> str | None:
    """Return the best available public error string for a terminal event."""
    error = payload.get("error")
    if isinstance(error, str) and error:
        return error
    taskspec = payload.get("taskspec")
    if isinstance(taskspec, dict):
        state = taskspec.get("state")
        if isinstance(state, dict):
            state_error = state.get("error")
            if isinstance(state_error, str) and state_error:
                return state_error
    if status == "cancelled":
        return "task cancelled"
    if status == "killed":
        return "task killed"
    return None


def await_one_shot_result(
    context: WeftContext,
    tid: str,
    *,
    outbox_name: str,
    ctrl_out_name: str | None,
    timeout: float | None,
    show_stderr: bool,
    emit_stream: bool = False,
) -> tuple[str, Any | None, str | None]:
    """Wait for a one-shot task to publish a terminal result."""
    outbox_queue = Queue(
        outbox_name,
        db_path=context.broker_target,
        persistent=True,
        config=context.broker_config,
    )
    ctrl_queue = (
        Queue(
            ctrl_out_name,
            db_path=context.broker_target,
            persistent=False,
            config=context.broker_config,
        )
        if ctrl_out_name
        else None
    )
    log_queue = Queue(
        WEFT_GLOBAL_LOG_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.broker_config,
    )

    log_last_timestamp: int | None = None
    stream_buffer: list[str] = []
    status = "running"
    result_values: list[Any] = []
    result_value: Any | None = None
    error_message: str | None = None
    completed_at: float | None = None

    deadline = None
    if timeout is not None and timeout > 0:
        deadline = time.monotonic() + timeout

    try:
        while True:
            while True:
                if ctrl_queue is None:
                    break
                ctrl_raw = ctrl_queue.read_one()
                if ctrl_raw is None:
                    break
                ctrl_payload = ctrl_raw[0] if isinstance(ctrl_raw, tuple) else ctrl_raw
                handle_ctrl_stream(str(ctrl_payload))

            ready_values, drained_outbox = drain_available_outbox_values(
                outbox_queue,
                stream_buffer,
                emit_stream=emit_stream,
            )
            for output in ready_values:
                append_public_value(
                    result_values,
                    output,
                    show_stderr=show_stderr,
                )
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

            if completed_at is not None and (
                time.monotonic() - completed_at >= WEFT_COMPLETED_RESULT_GRACE_SECONDS
            ):
                late_values, _ = drain_available_outbox_values(
                    outbox_queue,
                    stream_buffer,
                    emit_stream=emit_stream,
                )
                for output in late_values:
                    append_public_value(
                        result_values,
                        output,
                        show_stderr=show_stderr,
                    )
                result_value = aggregate_public_outputs(result_values)
                status = "completed"
                break

            if deadline is not None and time.monotonic() >= deadline:
                status = "timeout"
                error_message = (
                    f"Timed out after {timeout} seconds waiting for task {tid}"
                )
                break

            time.sleep(0.05)
    finally:
        outbox_queue.close()
        if ctrl_queue is not None:
            ctrl_queue.close()
        log_queue.close()

    return status, result_value, error_message
