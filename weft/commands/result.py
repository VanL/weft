"""Fetch task results from Weft queues.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.2] (result)
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, cast

from simplebroker import Queue
from weft._constants import (
    FAILURE_LIKE_TASK_STATUSES,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_COMPLETED_RESULT_GRACE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.helpers import iter_queue_json_entries

from ._queue_wait import QueueChangeMonitor
from ._result_wait import (
    append_public_value,
    await_one_shot_result,
    effective_result_surface_wait_interval,
    terminal_error_message,
    terminal_status_from_event,
)
from ._streaming import (
    DecodedOutboxValue,
    aggregate_public_outputs,
    drain_available_outbox_values,
    handle_ctrl_stream,
    poll_log_events,
    process_outbox_message,
)
from ._task_history import (
    is_pipeline_taskspec_payload,
    load_latest_taskspec_payload,
)


@dataclass(frozen=True, slots=True)
class ResultMaterialization:
    """Resolved result queues plus any terminal log state already observed."""

    taskspec_payload: dict[str, Any] | None
    outbox_name: str
    ctrl_out_name: str
    log_last_timestamp: int | None = None
    terminal_status: str | None = None
    terminal_error_message: str | None = None


def _normalize_tid(raw_tid: str) -> str:
    candidate = raw_tid.strip()
    if not candidate:
        raise ValueError("empty TID")
    if candidate.startswith("T"):
        candidate = candidate[1:]
    if not candidate.isdigit():
        raise ValueError(f"invalid task id '{raw_tid}'")
    return candidate


def _queue_names_for_tid(
    tid: str, taskspec_payload: dict[str, Any] | None
) -> tuple[str, str]:
    outbox = None
    ctrl_out = None
    if taskspec_payload:
        io_section = cast(dict[str, Any], taskspec_payload.get("io") or {})
        outputs = cast(dict[str, str], io_section.get("outputs") or {})
        control = cast(dict[str, str], io_section.get("control") or {})
        outbox = outputs.get("outbox")
        ctrl_out = control.get("ctrl_out")
    prefix = f"T{tid}."
    if not outbox:
        outbox = f"{prefix}{QUEUE_OUTBOX_SUFFIX}"
    if not ctrl_out:
        ctrl_out = f"{prefix}{QUEUE_CTRL_OUT_SUFFIX}"
    return outbox, ctrl_out


def _load_taskspec_payload(context: WeftContext, tid: str) -> dict[str, Any] | None:
    return load_latest_taskspec_payload(context, tid)


def _queue_exists(context: WeftContext, queue_name: str) -> bool:
    with context.broker() as db:
        try:
            queues = list(db.list_queues())
        except Exception:
            return False
    return any(name == queue_name for name, _count in queues)


def _queue_names_exist(context: WeftContext, *queue_names: str) -> bool:
    """Return ``True`` when any named queue currently exists."""

    wanted = {name for name in queue_names if name}
    if not wanted:
        return False

    with context.broker() as db:
        try:
            queues = list(db.list_queues())
        except Exception:
            return False
    return any(name in wanted for name, _count in queues)


def _await_result_materialization(
    context: WeftContext,
    tid: str,
    *,
    timeout: float | None,
) -> ResultMaterialization | None:
    """Wait for taskspec metadata or result queues to become visible.

    For stored specs with custom outbox/control queue names, ``weft result`` can
    race the manager on backends where task initialization becomes visible after
    the caller already has a TID. When a timeout is supplied, treat that window
    as part of the overall result wait instead of failing immediately.
    """

    deadline = None
    if timeout is not None and timeout > 0:
        deadline = time.monotonic() + timeout
    poll_interval = effective_result_surface_wait_interval(timeout)
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    monitor = QueueChangeMonitor([log_queue], config=context.config)
    log_last_timestamp: int | None = None

    try:
        while True:
            taskspec_payload = _load_taskspec_payload(context, tid)
            outbox_name, ctrl_out_name = _queue_names_for_tid(tid, taskspec_payload)
            if taskspec_payload is not None or _queue_names_exist(
                context, outbox_name, ctrl_out_name
            ):
                return ResultMaterialization(
                    taskspec_payload=taskspec_payload,
                    outbox_name=outbox_name,
                    ctrl_out_name=ctrl_out_name,
                    log_last_timestamp=log_last_timestamp,
                )

            events, log_last_timestamp = poll_log_events(
                log_queue,
                log_last_timestamp,
                tid,
            )
            if events:
                terminal_status: str | None = None
                terminal_message: str | None = None
                for event_payload, _timestamp in reversed(events):
                    event_status = terminal_status_from_event(event_payload)
                    if event_status is None:
                        continue
                    terminal_status = event_status
                    terminal_message = terminal_error_message(
                        event_payload,
                        event_status,
                    )
                    break
                for event_payload, _timestamp in reversed(events):
                    event_taskspec = event_payload.get("taskspec")
                    if isinstance(event_taskspec, dict):
                        outbox_name, ctrl_out_name = _queue_names_for_tid(
                            tid,
                            event_taskspec,
                        )
                        return ResultMaterialization(
                            taskspec_payload=event_taskspec,
                            outbox_name=outbox_name,
                            ctrl_out_name=ctrl_out_name,
                            log_last_timestamp=log_last_timestamp,
                            terminal_status=terminal_status,
                            terminal_error_message=terminal_message,
                        )
                return ResultMaterialization(
                    taskspec_payload=taskspec_payload,
                    outbox_name=outbox_name,
                    ctrl_out_name=ctrl_out_name,
                    log_last_timestamp=log_last_timestamp,
                    terminal_status=terminal_status,
                    terminal_error_message=terminal_message,
                )

            if deadline is None:
                return None
            wait_timeout = max(0.0, deadline - time.monotonic())
            if wait_timeout <= 0:
                # For the default ``T{tid}`` result surface, the caller already
                # knows which queues to read. If queue enumeration or the task
                # log lags until the timeout boundary, hand those default names
                # to the one-shot waiter instead of failing before it can drain
                # a result that is already present.
                return ResultMaterialization(
                    taskspec_payload=taskspec_payload,
                    outbox_name=outbox_name,
                    ctrl_out_name=ctrl_out_name,
                    log_last_timestamp=log_last_timestamp,
                )
            wait_timeout = min(wait_timeout, poll_interval)
            monitor.wait(wait_timeout)
    finally:
        monitor.close()
        log_queue.close()


def _active_streaming_queues(context: WeftContext) -> set[str]:
    """Return outbox names currently marked as streaming (Spec: [CC-2.4])."""
    queue = context.queue(WEFT_STREAMING_SESSIONS_QUEUE, persistent=False)
    active: set[str] = set()
    for payload, _timestamp in iter_queue_json_entries(queue):
        queue_name = payload.get("queue")
        if isinstance(queue_name, str):
            active.add(queue_name)
    return active


def _iter_queue_messages(queue: Queue, *, peek: bool) -> Iterator[str]:
    if peek:
        for peek_item in queue.peek_generator():
            if isinstance(peek_item, tuple):
                yield str(peek_item[0])
            else:
                yield str(peek_item)
    else:
        while True:
            next_item = queue.read_one()
            if next_item is None:
                break
            if isinstance(next_item, tuple):
                yield str(next_item[0])
            else:
                yield str(next_item)


def _is_persistent_task(taskspec_payload: dict[str, Any] | None) -> bool:
    """Return ``True`` when the loaded TaskSpec payload is persistent."""
    if not isinstance(taskspec_payload, dict):
        return False
    spec_section = taskspec_payload.get("spec")
    if not isinstance(spec_section, dict):
        return False
    return bool(spec_section.get("persistent"))


def _drain_outbox_until_timestamp(
    queue: Queue,
    *,
    boundary_timestamp: int,
    stream_buffer: list[str],
    emit_stream: bool,
) -> list[DecodedOutboxValue]:
    """Consume final outbox payloads whose timestamps fall at or before a boundary."""
    values: list[DecodedOutboxValue] = []
    while True:
        next_item = queue.peek_one(with_timestamps=True)
        if next_item is None:
            break
        if not isinstance(next_item, tuple):
            break
        payload, timestamp = next_item
        if timestamp > boundary_timestamp:
            break
        consumed = queue.read_one(exact_timestamp=timestamp)
        if consumed is None:
            continue
        final, value = process_outbox_message(
            str(consumed),
            stream_buffer,
            emit_stream=emit_stream,
        )
        if final and value is not None:
            values.append(value)
    return values


def _collect_all_results(
    context: WeftContext,
    *,
    json_output: bool,
    show_stderr: bool,
    peek_only: bool,
) -> tuple[int, str | None]:
    """Aggregate results from completed task outboxes (Spec: [CLI-1.1.1])."""
    with context.broker() as db:
        try:
            queue_stats = db.get_queue_stats()
        except Exception as exc:
            return 1, f"weft: failed to enumerate queues: {exc}"

    outbox_names = [
        name
        for name, _unclaimed, _total in queue_stats
        if fnmatchcase(name, f"T*.{QUEUE_OUTBOX_SUFFIX}")
    ]

    streaming = _active_streaming_queues(context)

    aggregated: list[dict[str, Any]] = []
    for name in outbox_names:
        if name in streaming:
            continue
        tid = name.split(".", 1)[0][1:]
        queue = context.queue(name, persistent=True)
        try:
            stream_buffer: list[str] = []
            result_values: list[Any] = []
            for payload in _iter_queue_messages(queue, peek=peek_only):
                final, value = process_outbox_message(
                    payload,
                    stream_buffer,
                    emit_stream=False,
                )
                if not final or value is None:
                    continue
                append_public_value(result_values, value, show_stderr=show_stderr)
            rendered = aggregate_public_outputs(result_values)
            if rendered is not None:
                aggregated.append({"tid": tid, "result": rendered})
        finally:
            queue.close()

    if json_output:
        return 0, json.dumps({"results": aggregated}, ensure_ascii=False)

    if not aggregated:
        return 0, ""

    lines = [f"{item['tid']}: {item['result']}" for item in aggregated]
    return 0, "\n".join(lines)


def _await_single_result(
    context: WeftContext,
    tid: str,
    *,
    timeout: float | None,
    show_stderr: bool,
    emit_stream: bool = False,
    taskspec_payload: dict[str, Any] | None = None,
    outbox_name: str | None = None,
    ctrl_out_name: str | None = None,
    initial_log_last_timestamp: int | None = None,
    initial_terminal_status: str | None = None,
    initial_terminal_error_message: str | None = None,
) -> tuple[str, Any | None, str | None]:
    if taskspec_payload is None:
        taskspec_payload = _load_taskspec_payload(context, tid)
    is_persistent = _is_persistent_task(taskspec_payload)
    if outbox_name is None or ctrl_out_name is None:
        outbox_name, ctrl_out_name = _queue_names_for_tid(tid, taskspec_payload)
    ctrl_out_for_wait = (
        None if is_pipeline_taskspec_payload(taskspec_payload) else ctrl_out_name
    )

    if not is_persistent:
        return await_one_shot_result(
            context,
            tid,
            outbox_name=outbox_name,
            ctrl_out_name=ctrl_out_for_wait,
            timeout=timeout,
            show_stderr=show_stderr,
            emit_stream=emit_stream,
            initial_log_last_timestamp=initial_log_last_timestamp,
            initial_terminal_status=initial_terminal_status,
            initial_error_message=initial_terminal_error_message,
        )

    outbox_queue = context.queue(outbox_name, persistent=True)
    ctrl_queue = context.queue(ctrl_out_name, persistent=False)
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    monitor = QueueChangeMonitor(
        [outbox_queue, ctrl_queue, log_queue],
        config=context.config,
    )

    log_last_timestamp: int | None = initial_log_last_timestamp
    stream_buffer: list[str] = []
    status = "running"
    result_values: list[Any] = []
    result_value: Any | None = None
    error_message: str | None = initial_terminal_error_message
    completed_at: float | None = (
        time.monotonic() if initial_terminal_status == "completed" else None
    )
    first_pending_timestamp: int | None = None
    boundary_timestamp: int | None = None
    boundary_seen_at: float | None = None
    pending_completion_timestamps: list[int] = []
    materialized_completed = initial_terminal_status == "completed"
    if initial_terminal_status is not None and initial_terminal_status != "completed":
        status = initial_terminal_status

    deadline = None
    if timeout is not None and timeout > 0:
        deadline = time.monotonic() + timeout
    poll_interval = effective_result_surface_wait_interval(timeout)

    try:
        while True:
            while True:
                ctrl_raw = ctrl_queue.read_one()
                if ctrl_raw is None:
                    break
                ctrl_payload = ctrl_raw[0] if isinstance(ctrl_raw, tuple) else ctrl_raw
                handle_ctrl_stream(str(ctrl_payload))

            if is_persistent:
                peeked = outbox_queue.peek_one(with_timestamps=True)
                if (
                    peeked is not None
                    and isinstance(peeked, tuple)
                    and first_pending_timestamp is None
                ):
                    _payload, first_pending_timestamp = peeked
                    for completion_timestamp in pending_completion_timestamps:
                        if completion_timestamp >= first_pending_timestamp:
                            boundary_timestamp = completion_timestamp
                            boundary_seen_at = time.monotonic()
                            break

            events: list[tuple[dict[str, Any], int]]
            events, log_last_timestamp = poll_log_events(
                log_queue,
                log_last_timestamp,
                tid,
            )
            for event_payload, _ts in events:
                if is_persistent and event_payload.get("event") in {
                    "work_item_completed",
                    "work_completed",
                }:
                    pending_completion_timestamps.append(_ts)
                    if (
                        first_pending_timestamp is not None
                        and _ts >= first_pending_timestamp
                        and boundary_timestamp is None
                    ):
                        boundary_timestamp = _ts
                        boundary_seen_at = time.monotonic()
                    if event_payload.get("event") == "work_item_completed":
                        continue
                event_status = terminal_status_from_event(event_payload)
                if event_status is None:
                    continue
                if (
                    is_persistent
                    and first_pending_timestamp is not None
                    and _ts >= first_pending_timestamp
                    and boundary_timestamp is None
                ):
                    boundary_timestamp = _ts
                    boundary_seen_at = time.monotonic()
                if event_status == "completed":
                    if completed_at is None:
                        completed_at = time.monotonic()
                    continue
                status = event_status
                error_message = terminal_error_message(event_payload, event_status)
            if status != "running":
                break

            if is_persistent and boundary_timestamp is not None:
                drained_outputs = _drain_outbox_until_timestamp(
                    outbox_queue,
                    boundary_timestamp=boundary_timestamp,
                    stream_buffer=stream_buffer,
                    emit_stream=emit_stream,
                )
                for output in drained_outputs:
                    append_public_value(
                        result_values,
                        output,
                        show_stderr=show_stderr,
                    )
                if materialized_completed and result_values:
                    result_value = aggregate_public_outputs(result_values)
                    status = "completed"
                    break
                if (
                    drained_outputs
                    or result_values
                    or (
                        boundary_seen_at is not None
                        and time.monotonic() - boundary_seen_at
                        >= WEFT_COMPLETED_RESULT_GRACE_SECONDS
                    )
                ):
                    result_value = aggregate_public_outputs(result_values)
                    status = "completed"
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

            wait_timeout: float | None = None
            if deadline is not None:
                wait_timeout = max(0.0, deadline - time.monotonic())
            if boundary_timestamp is not None and boundary_seen_at is not None:
                grace_remaining = max(
                    0.0,
                    WEFT_COMPLETED_RESULT_GRACE_SECONDS
                    - (time.monotonic() - boundary_seen_at),
                )
                wait_timeout = (
                    grace_remaining
                    if wait_timeout is None
                    else min(wait_timeout, grace_remaining)
                )
            if completed_at is not None:
                completion_remaining = max(
                    0.0,
                    WEFT_COMPLETED_RESULT_GRACE_SECONDS
                    - (time.monotonic() - completed_at),
                )
                wait_timeout = (
                    completion_remaining
                    if wait_timeout is None
                    else min(wait_timeout, completion_remaining)
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
        ctrl_queue.close()
        log_queue.close()

    return status, result_value, error_message


def cmd_result(
    *,
    tid: str | None,
    all_results: bool,
    peek: bool,
    timeout: float | None,
    stream: bool,
    json_output: bool,
    show_stderr: bool,
    context_path: str | None,
) -> tuple[int, str | None]:
    try:
        context = build_context(spec_context=context_path)
    except Exception as exc:
        return 1, f"weft: failed to resolve context: {exc}"

    if all_results:
        if tid is not None:
            return 2, "weft result: task id not expected with --all"
        if stream:
            return 2, "weft result: --stream cannot be used with --all"
        if timeout:
            return 2, "weft result: --timeout is not supported with --all"
        exit_code, payload = _collect_all_results(
            context,
            json_output=json_output,
            show_stderr=show_stderr,
            peek_only=peek,
        )
        return exit_code, payload

    if peek:
        return 2, "weft result: --peek requires --all"

    if tid is None:
        return 2, "weft result: task id required"

    if stream and json_output:
        return 2, "weft result: --stream cannot be used with --json"

    try:
        full_tid = _normalize_tid(tid)
    except ValueError as exc:
        return 2, f"weft result: {exc}"

    start_monotonic = time.monotonic()
    materialized = _await_result_materialization(
        context,
        full_tid,
        timeout=timeout,
    )
    if materialized is None:
        if timeout is not None and timeout > 0:
            return 124, f"Timed out after {timeout} seconds waiting for task {full_tid}"
        return 2, f"weft result: no outbox queue for task {full_tid}"

    remaining_timeout = timeout
    if timeout is not None and timeout > 0:
        elapsed = time.monotonic() - start_monotonic
        remaining_timeout = max(0.0, timeout - elapsed)

    status, value, error_message = _await_single_result(
        context,
        full_tid,
        timeout=remaining_timeout,
        show_stderr=show_stderr,
        emit_stream=stream,
        taskspec_payload=materialized.taskspec_payload,
        outbox_name=materialized.outbox_name,
        ctrl_out_name=materialized.ctrl_out_name,
        initial_log_last_timestamp=materialized.log_last_timestamp,
        initial_terminal_status=materialized.terminal_status,
        initial_terminal_error_message=materialized.terminal_error_message,
    )

    if status == "completed":
        if json_output:
            json_payload = {"tid": full_tid, "status": status, "result": value}
            return 0, json.dumps(json_payload, ensure_ascii=False)
        if value is None:
            return 0, ""
        if isinstance(value, (dict, list)):
            return 0, json.dumps(value, ensure_ascii=False)
        return 0, str(value)

    if status == "timeout":
        return 124, error_message or (
            f"weft result: timed out waiting for task {full_tid}"
        )

    if status in FAILURE_LIKE_TASK_STATUSES:
        message = error_message or f"weft result: task {full_tid} failed"
        return 1, message

    return 2, f"weft result: task {full_tid} not found"


__all__ = ["cmd_result"]
