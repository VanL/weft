"""Fetch task results from Weft queues."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from fnmatch import fnmatchcase
from typing import Any, cast

from simplebroker import Queue
from simplebroker.db import BrokerDB
from weft._constants import (
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
)
from weft.context import WeftContext, build_context

from .run import (
    _handle_ctrl_stream,
    _poll_log_events,
    _process_outbox_message,
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
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )
    try:
        records = cast(
            list[tuple[str, int]] | None,
            log_queue.peek_many(limit=2048, with_timestamps=True),
        )
    except Exception:
        records = None

    if not records:
        return None

    for entry, _timestamp in reversed(records):
        try:
            payload = cast(dict[str, Any], json.loads(entry))
        except json.JSONDecodeError:
            continue
        if payload.get("tid") != tid:
            continue
        taskspec = payload.get("taskspec")
        if isinstance(taskspec, dict):
            return taskspec
    return None


def _queue_exists(context: WeftContext, queue_name: str) -> bool:
    with BrokerDB(str(context.database_path)) as db:
        try:
            queues = list(db.list_queues())
        except Exception:
            return False
    return any(name == queue_name for name, _count in queues)


def _active_streaming_queues(context: WeftContext) -> set[str]:
    """Return outbox names currently marked as streaming (Spec: [CC-2.4])."""
    queue = Queue(
        WEFT_STREAMING_SESSIONS_QUEUE,
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )
    try:
        entries = queue.peek_many(limit=512)
    except Exception:
        return set()
    if not entries:
        return set()

    active: set[str] = set()
    for entry in entries:
        body = entry[0] if isinstance(entry, tuple) else entry
        try:
            payload = json.loads(body)
        except (TypeError, json.JSONDecodeError):
            continue
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


def _collect_all_results(
    context: WeftContext,
    *,
    json_output: bool,
    show_stderr: bool,
    peek_only: bool,
) -> tuple[int, str | None]:
    """Aggregate results from completed task outboxes (Spec: [CLI-1.1.1])."""
    with BrokerDB(str(context.database_path)) as db:
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
            db_path=str(context.database_path),
            persistent=True,
            config=context.broker_config,
        )
        stream_buffer: list[str] = []
        for payload in _iter_queue_messages(queue, peek=peek_only):
            final, value = _process_outbox_message(payload, stream_buffer)
            if not final:
                continue
            if show_stderr and isinstance(value, dict) and "stderr" in value:
                rendered = value.get("stderr") or ""
            else:
                rendered = value
            aggregated.append({"tid": tid, "result": rendered})

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
    outbox_name, ctrl_out_name = _queue_names_for_tid(tid, taskspec_payload)

    outbox_queue = Queue(
        outbox_name,
        db_path=str(context.database_path),
        persistent=True,
        config=context.broker_config,
    )
    ctrl_queue = Queue(
        ctrl_out_name,
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )
    log_queue = Queue(
        WEFT_GLOBAL_LOG_QUEUE,
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )

    log_last_timestamp: int | None = None
    stream_buffer: list[str] = []
    status = "running"
    result_value: Any | None = None
    error_message: str | None = None

    deadline = None
    if timeout is not None and timeout > 0:
        deadline = time.monotonic() + timeout

    while True:
        while True:
            ctrl_raw = ctrl_queue.read_one()
            if ctrl_raw is None:
                break
            ctrl_payload = ctrl_raw[0] if isinstance(ctrl_raw, tuple) else ctrl_raw
            _handle_ctrl_stream(str(ctrl_payload))

        outbox_raw = outbox_queue.read_one()
        if outbox_raw is not None:
            payload = outbox_raw[0] if isinstance(outbox_raw, tuple) else outbox_raw
            final, value = _process_outbox_message(str(payload), stream_buffer)
            if final:
                if show_stderr and isinstance(value, dict) and "stderr" in value:
                    result_value = value.get("stderr")
                else:
                    result_value = value
                status = "completed"
                break
            continue

        events: list[tuple[dict[str, Any], int]]
        events, log_last_timestamp = _poll_log_events(
            log_queue,
            log_last_timestamp,
            tid,
        )
        for event_payload, _ts in events:
            event = event_payload.get("event")
            if event in {"work_failed", "work_timeout", "work_limit_violation"}:
                status = "timeout" if event == "work_timeout" else "failed"
                error_message = event_payload.get("error") or event.replace("_", " ")
                break
            if event == "task_cancelled":
                status = "failed"
                error_message = "task cancelled"
                break
        if status != "running":
            break

        if deadline is not None and time.monotonic() >= deadline:
            status = "timeout"
            error_message = f"Timed out after {timeout} seconds waiting for task {tid}"
            break

        time.sleep(0.05)

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

    outbox_name, _ = _queue_names_for_tid(
        full_tid, _load_taskspec_payload(context, full_tid)
    )
    if not _queue_exists(context, outbox_name):
        return 2, f"weft result: no outbox queue for task {full_tid}"

    status, value, error_message = _await_single_result(
        context,
        full_tid,
        timeout=timeout,
        show_stderr=show_stderr,
    )

    if status == "completed":
        if json_output:
            json_payload = {"tid": full_tid, "status": status, "result": value}
            return 0, json.dumps(json_payload, ensure_ascii=False)
        if value is None:
            return 0, ""
        return 0, str(value)

    if status == "timeout":
        return 124, error_message or (
            f"weft result: timed out waiting for task {full_tid}"
        )

    if status == "failed":
        message = error_message or f"weft result: task {full_tid} failed"
        return 1, message

    return 2, f"weft result: task {full_tid} not found"


__all__ = ["cmd_result"]
