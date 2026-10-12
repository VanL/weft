"""Fetch task results through the canonical consuming observation.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/10-CLI_Interface.md [CLI-1.2.2] (result)
- docs/specifications/14-Python_API_Surfaces.md [PY-2]
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from weft._constants import QUEUE_OUTBOX_SUFFIX, WEFT_STREAMING_SESSIONS_QUEUE
from weft._exceptions import (
    CommandError,
    CommandExecutionError,
    CommandUsageError,
    InvalidTID,
    TaskNotFound,
)
from weft.commands.types import CommandStream, TaskEvent, TaskResult
from weft.context import WeftContext, build_context
from weft.core import task_evidence
from weft.core.taskspec import TaskSpec
from weft.helpers import closing_queue_iterator, iter_queue_json_entries

from ._boundary import typed_command_errors
from ._result_wait import (
    ResultMaterialization,
    _accepted_result_materialization,
    _iter_consuming_result,
    append_public_value,
)
from ._streaming import aggregate_public_outputs, process_outbox_message


def _normalize_tid(raw_tid: str) -> str:
    candidate = raw_tid.strip()
    if not candidate:
        raise ValueError("empty TID")
    candidate = candidate.removeprefix("T")
    if not candidate.isdigit():
        raise ValueError(f"invalid task id '{raw_tid}'")
    return candidate


def _active_streaming_queues(context: WeftContext) -> set[str]:
    """Return outbox names currently marked as streaming (Spec: [CC-2.4])."""
    queue = context.queue(WEFT_STREAMING_SESSIONS_QUEUE, persistent=False)
    active: set[str] = set()
    for payload, _timestamp in iter_queue_json_entries(queue):
        queue_name = payload.get("queue")
        if isinstance(queue_name, str):
            active.add(queue_name)
    return active


@typed_command_errors
def _iter_task_result(
    context: WeftContext,
    tid: str,
    *,
    timeout: float | None = None,
    show_stderr: bool = False,
    emit_stream: bool = False,
    include_stream_events: bool = False,
    _accepted_taskspec: TaskSpec | None = None,
    _follow_lifecycle: bool = False,
) -> Iterator[TaskEvent | TaskResult]:
    """Yield raw activity and one final result within the original budget.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-5];
    docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """
    normalized = _normalize_tid(tid)
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    if _accepted_taskspec is not None:
        materialized = _accepted_result_materialization(_accepted_taskspec)
    else:
        outbox, control = task_evidence.queue_names_for_tid(normalized, None)
        materialized = ResultMaterialization(None, outbox, control)
    yield from _iter_consuming_result(
        context,
        normalized,
        materialized=materialized,
        deadline=deadline,
        timeout=timeout,
        show_stderr=show_stderr,
        emit_stream=emit_stream,
        include_stream_events=include_stream_events,
        discover_history=_accepted_taskspec is None,
        follow_lifecycle=_follow_lifecycle,
    )


@typed_command_errors
def await_task_result(
    context: WeftContext,
    tid: str,
    *,
    timeout: float | None = None,
    show_stderr: bool = False,
    emit_stream: bool = False,
    _accepted_taskspec: TaskSpec | None = None,
) -> TaskResult:
    """Drain the shared consuming iterator and return its final result.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-5];
    docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """
    source = _iter_task_result(
        context,
        tid,
        timeout=timeout,
        show_stderr=show_stderr,
        emit_stream=emit_stream,
        _accepted_taskspec=_accepted_taskspec,
    )
    with closing_queue_iterator(source) as rows:
        for row in rows:
            if isinstance(row, TaskResult):
                return row
    raise CommandExecutionError("Result collection ended without an outcome")


def _read_outbox_task_result(
    context: WeftContext,
    *,
    name: str,
    tid: str,
    peek: bool,
) -> TaskResult | None:
    """Inspect one outbox once and consume only an observed complete result."""

    queue = context.queue(name, persistent=True)
    try:
        stream_buffer: list[str] = []
        values: list[Any] = []
        observed_timestamps: list[int] = []
        complete_prefix_length = 0
        iterator = queue.peek_generator(with_timestamps=True)
        with closing_queue_iterator(iterator) as rows:
            for entry in rows:
                if isinstance(entry, tuple) and len(entry) == 2:
                    payload, timestamp = entry
                    observed_timestamps.append(int(timestamp))
                else:
                    payload = entry
                final, decoded = process_outbox_message(
                    str(payload),
                    stream_buffer,
                    emit_stream=False,
                )
                if final and decoded is not None:
                    append_public_value(values, decoded, show_stderr=False)
                    complete_prefix_length = len(observed_timestamps)
        value = aggregate_public_outputs(values)
        if value is not None and not peek:
            for timestamp in observed_timestamps[:complete_prefix_length]:
                queue.read_one(exact_timestamp=timestamp)
    except Exception as exc:
        raise CommandExecutionError(
            f"failed to inspect result for task {tid}: {exc}"
        ) from exc
    finally:
        queue.close()
    if value is None:
        return None
    stdout, stderr = task_evidence.split_stdio(value)
    return TaskResult(
        tid=tid,
        status="completed",
        value=value,
        stdout=stdout,
        stderr=stderr,
        error=None,
    )


def _collect_all_task_results(
    context: WeftContext,
    *,
    peek: bool,
) -> tuple[TaskResult, ...]:
    """Collect structured results from non-streaming task outboxes."""

    try:
        with context.broker() as db:
            queue_names = db.list_queues(pattern=f"T*.{QUEUE_OUTBOX_SUFFIX}")
        streaming = _active_streaming_queues(context)
    except Exception as exc:
        raise CommandExecutionError(f"failed to enumerate task results: {exc}") from exc

    results: list[TaskResult] = []
    for raw_name in queue_names:
        name = str(raw_name)
        if name in streaming:
            continue
        tid = name.split(".", 1)[0][1:]
        result = _read_outbox_task_result(
            context,
            name=name,
            tid=tid,
            peek=peek,
        )
        if result is not None:
            results.append(result)
    return tuple(results)


def _task_result_event(result: TaskResult) -> TaskEvent:
    """Project a settled result without changing its outcome [MF-5], [PY-2]."""
    return TaskEvent(
        result.tid,
        "result",
        time.time_ns(),
        {
            "status": result.status,
            "value": result.value,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": result.error,
        },
    )


def _result_task_event_stream(
    context: WeftContext,
    tid: str,
    *,
    timeout: float | None,
) -> CommandStream[TaskEvent]:
    """Project stream frames and the final outcome from one collector [MF-5]."""
    source = _iter_task_result(
        context, tid, timeout=timeout, emit_stream=True, include_stream_events=True
    )

    def _stream() -> Iterator[TaskEvent]:
        with closing_queue_iterator(source) as rows:
            for row in rows:
                if isinstance(row, TaskEvent):
                    yield row
                else:
                    _require_available_result(row)
                    yield _task_result_event(row)

    return cast(CommandStream[TaskEvent], _stream())


def _validate_public_result_request(
    *,
    tid: str | None,
    all_results: bool,
    peek: bool,
    timeout: float | None,
    stream: bool,
) -> None:
    """Validate canonical result modes before opening a context."""

    if all_results and tid is not None:
        raise CommandUsageError("task id cannot be used with all")
    if all_results and stream:
        raise CommandUsageError("stream cannot be used with all")
    if all_results and timeout is not None:
        raise CommandUsageError("timeout is not supported with all")
    if not all_results and peek:
        raise CommandUsageError("peek requires all")
    if not all_results and tid is None:
        raise CommandUsageError("task id is required")
    if timeout is not None and timeout < 0:
        raise CommandUsageError("timeout must be non-negative")


def _public_result_tid(tid: str | None) -> str:
    """Normalize the already-required result TID or raise its public error."""

    try:
        return _normalize_tid(cast(str, tid))
    except ValueError as exc:
        raise InvalidTID(str(exc)) from exc


def _require_available_result(result: TaskResult) -> TaskResult:
    """Translate absence/wait statuses while preserving task terminal outcomes."""

    if result.status == "missing":
        raise TaskNotFound(result.error or f"task {result.tid} not found")
    return result


@typed_command_errors
def cmd_result(
    tid: str | None = None,
    *,
    all: bool = False,
    peek: bool = False,
    timeout: float | None = None,
    stream: bool = False,
    context: str | Path | None = None,
) -> TaskResult | tuple[TaskResult, ...] | CommandStream[TaskEvent]:
    """Return one result, all completed results, or a structured result stream.

    Args:
        tid: Task ID for single-result and stream modes.
        all: Collect completed results across task outboxes.
        peek: Inspect all results without consuming them.
        timeout: Maximum wait for a single result or stream.
        stream: Return a closable structured event stream.
        context: Optional project context path.

    Returns:
        One task result, a tuple in all mode, or an event stream in stream mode.

    Raises:
        CommandUsageError: If modes conflict or required input is absent.
        InvalidTID: If ``tid`` is malformed.
        TaskNotFound: If the requested task result surface is absent.
        CommandTimeoutError: If the requested wait reaches ``timeout``.
        CommandExecutionError: If context or result collection fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    _validate_public_result_request(
        tid=tid,
        all_results=all,
        peek=peek,
        timeout=timeout,
        stream=stream,
    )

    try:
        resolved_context = build_context(spec_context=context)
    except Exception as exc:
        raise CommandExecutionError(f"failed to resolve result context: {exc}") from exc

    if all:
        return _collect_all_task_results(resolved_context, peek=peek)

    normalized_tid = _public_result_tid(tid)
    if stream:
        return _result_task_event_stream(
            resolved_context,
            normalized_tid,
            timeout=timeout,
        )
    try:
        result = await_task_result(
            resolved_context,
            normalized_tid,
            timeout=timeout,
            show_stderr=False,
            emit_stream=False,
        )
    except Exception as exc:
        if isinstance(exc, CommandError):
            raise
        raise CommandExecutionError(f"failed to collect task result: {exc}") from exc
    return _require_available_result(result)


__all__ = ["await_task_result", "cmd_result"]
