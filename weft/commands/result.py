"""Fetch task results from Weft queues."""

from __future__ import annotations

import json
import time
from typing import Any, cast

from simplebroker import Queue
from simplebroker.db import BrokerDB

from weft._constants import (
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.context import build_context

from .run import (
    _decode_result_payload,
    _handle_ctrl_stream,
    _poll_log_events,
    _process_outbox_message,
)


def _normalise_tid(raw_tid: str) -> str:
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


def _load_taskspec_payload(context, tid: str) -> dict[str, Any] | None:
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


def _queue_exists(context, queue_name: str) -> bool:
    with BrokerDB(str(context.database_path)) as db:
        try:
            queues = list(db.list_queues())
        except Exception:
            return False
    return any(name == queue_name for name, _count in queues)


def _collect_all_results(
    context,
    *,
    json_output: bool,
    show_stderr: bool,
) -> tuple[int, str | None]:
    with BrokerDB(str(context.database_path)) as db:
        try:
            queue_entries = list(db.list_queues())
        except Exception as exc:
            return 1, f"weft: failed to enumerate queues: {exc}"

    aggregated: list[dict[str, Any]] = []
    for name, _count in queue_entries:
        if not name.endswith(QUEUE_OUTBOX_SUFFIX):
            continue
        if not name.startswith("T"):
            continue
        tid = name.split(".", 1)[0][1:]
        queue = Queue(
            name,
            db_path=str(context.database_path),
            persistent=True,
            config=context.broker_config,
        )
        stream_buffer: list[str] = []
        while True:
            raw_item = queue.read_one()
            if raw_item is None:
                break
            payload = raw_item[0] if isinstance(raw_item, tuple) else raw_item
            final, value = _process_outbox_message(str(payload), stream_buffer)
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
    context,
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

        events, log_last_timestamp = _poll_log_events(
            log_queue,
            log_last_timestamp,
            tid,
        )
        for payload, _ts in events:
            event = payload.get("event")
            if event in {"work_failed", "work_timeout", "work_limit_violation"}:
                status = "timeout" if event == "work_timeout" else "failed"
                error_message = payload.get("error") or event.replace("_", " ")
                break
            if event == "task_cancelled":
                status = "failed"
                error_message = "task cancelled"
                break
        if status != "running":
            break

        if deadline is not None and time.monotonic() >= deadline:
            status = "timeout"
            error_message = (
                f"Timed out after {timeout} seconds waiting for task {tid}"
            )
            break

        time.sleep(0.05)

    return status, result_value, error_message


def cmd_result(
    *,
    tid: str | None,
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

    if tid is None and not stream:
        return 2, "weft result: task id required"

    if tid is None:
        return _collect_all_results(
            context,
            json_output=json_output,
            show_stderr=show_stderr,
        )

    try:
        full_tid = _normalise_tid(tid)
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
            payload = {"tid": full_tid, "status": status, "result": value}
            return 0, json.dumps(payload, ensure_ascii=False)
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
