"""Fetch task results from Weft queues.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.2] (result)
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from fnmatch import fnmatchcase
from typing import Any, cast

from simplebroker import Queue
from weft._constants import (
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_COMPLETED_RESULT_GRACE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.helpers import iter_queue_json_entries

from ._streaming import (
    aggregate_public_outputs,
    handle_ctrl_stream,
    poll_log_events,
    process_outbox_message,
)


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
    log_queue = Queue(
        WEFT_GLOBAL_LOG_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.broker_config,
    )
    latest_taskspec: dict[str, Any] | None = None
    for payload, _timestamp in iter_queue_json_entries(log_queue):
        if payload.get("tid") != tid:
            continue
        taskspec = payload.get("taskspec")
        if isinstance(taskspec, dict):
            latest_taskspec = taskspec
    return latest_taskspec


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
) -> tuple[dict[str, Any] | None, str, str] | None:
    """Wait for taskspec metadata or result queues to become visible.

    For stored specs with custom outbox/control queue names, ``weft result`` can
    race the manager on backends where task initialization becomes visible after
    the caller already has a TID. When a timeout is supplied, treat that window
    as part of the overall result wait instead of failing immediately.
    """

    deadline = None
    if timeout is not None and timeout > 0:
        deadline = time.monotonic() + timeout

    while True:
        taskspec_payload = _load_taskspec_payload(context, tid)
        outbox_name, ctrl_out_name = _queue_names_for_tid(tid, taskspec_payload)
        if taskspec_payload is not None or _queue_names_exist(
            context, outbox_name, ctrl_out_name
        ):
            return taskspec_payload, outbox_name, ctrl_out_name

        if deadline is None:
            return None
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def _active_streaming_queues(context: WeftContext) -> set[str]:
    """Return outbox names currently marked as streaming (Spec: [CC-2.4])."""
    queue = Queue(
        WEFT_STREAMING_SESSIONS_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.broker_config,
    )
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


def _append_public_value(
    values: list[Any],
    value: Any,
    *,
    show_stderr: bool,
) -> None:
    """Append a caller-facing value after optional stderr selection."""
    if show_stderr and isinstance(value, dict) and "stderr" in value:
        values.append(value.get("stderr") or "")
        return
    values.append(value)


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
    show_stderr: bool,
) -> list[Any]:
    """Consume final outbox payloads whose timestamps fall at or before a boundary."""
    values: list[Any] = []
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
            emit_stream=False,
        )
        if final:
            _append_public_value(values, value, show_stderr=show_stderr)
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
        queue = Queue(
            name,
            db_path=context.broker_target,
            persistent=True,
            config=context.broker_config,
        )
        try:
            stream_buffer: list[str] = []
            result_values: list[Any] = []
            for payload in _iter_queue_messages(queue, peek=peek_only):
                final, value = process_outbox_message(
                    payload,
                    stream_buffer,
                    emit_stream=False,
                )
                if not final:
                    continue
                _append_public_value(result_values, value, show_stderr=show_stderr)
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
) -> tuple[str, Any | None, str | None]:
    taskspec_payload = _load_taskspec_payload(context, tid)
    is_persistent = _is_persistent_task(taskspec_payload)
    outbox_name, ctrl_out_name = _queue_names_for_tid(tid, taskspec_payload)

    outbox_queue = Queue(
        outbox_name,
        db_path=context.broker_target,
        persistent=True,
        config=context.broker_config,
    )
    ctrl_queue = Queue(
        ctrl_out_name,
        db_path=context.broker_target,
        persistent=False,
        config=context.broker_config,
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
    first_pending_timestamp: int | None = None
    boundary_timestamp: int | None = None
    boundary_seen_at: float | None = None
    pending_completion_timestamps: list[int] = []

    deadline = None
    if timeout is not None and timeout > 0:
        deadline = time.monotonic() + timeout

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
            else:
                outbox_raw = outbox_queue.read_one()
                if outbox_raw is not None:
                    payload = (
                        outbox_raw[0] if isinstance(outbox_raw, tuple) else outbox_raw
                    )
                    final, value = process_outbox_message(
                        str(payload),
                        stream_buffer,
                        emit_stream=False,
                    )
                    if final:
                        _append_public_value(
                            result_values,
                            value,
                            show_stderr=show_stderr,
                        )
                        continue

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
                event_status = _terminal_status_from_event(event_payload)
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
                error_message = _terminal_error_message(event_payload, event_status)
            if status != "running":
                break

            if is_persistent and boundary_timestamp is not None:
                result_values.extend(
                    _drain_outbox_until_timestamp(
                        outbox_queue,
                        boundary_timestamp=boundary_timestamp,
                        stream_buffer=stream_buffer,
                        show_stderr=show_stderr,
                    )
                )
                if result_values or (
                    boundary_seen_at is not None
                    and time.monotonic() - boundary_seen_at
                    >= WEFT_COMPLETED_RESULT_GRACE_SECONDS
                ):
                    result_value = aggregate_public_outputs(result_values)
                    status = "completed"
                    break

            if completed_at is not None and (
                time.monotonic() - completed_at >= WEFT_COMPLETED_RESULT_GRACE_SECONDS
            ):
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

    if status in {"failed", "killed", "cancelled"}:
        message = error_message or f"weft result: task {full_tid} failed"
        return 1, message

    return 2, f"weft result: task {full_tid} not found"


def _terminal_status_from_event(payload: dict[str, Any]) -> str | None:
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


def _terminal_error_message(payload: dict[str, Any], status: str) -> str | None:
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


__all__ = ["cmd_result"]
