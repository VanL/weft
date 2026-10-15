"""Tests for result helpers and terminal task reporting."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self, TypedDict

import pytest
from psycopg_pool import PoolTimeout

from simplebroker import Config, Queue
from tests.helpers.task_observation import write_task_observation
from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft._exceptions import (
    CommandExecutionError,
    CommandTimeoutError,
    CommandUsageError,
    TaskNotFound,
)
from weft.commands import _result_wait as result_wait
from weft.commands import events as events_cmd
from weft.commands import result as result_cmd
from weft.commands._streaming import (
    handle_ctrl_stream,
    process_outbox_message,
)
from weft.commands.result import (
    await_task_result,
    cmd_result,
)
from weft.commands.types import TaskEvent, TaskResult
from weft.context import WeftContext, build_context
from weft.core import task_evidence
from weft.core.task_state import task_state_queue_name
from weft.core.tasks.consumer import Consumer
from weft.core.taskspec import TaskSpec
from weft.helpers import closing_queue_iterator, iter_queue_json_entries


class _ResultOptions(TypedDict, total=False):
    tid: str
    all: bool
    peek: bool
    timeout: float
    stream: bool


pytestmark = [pytest.mark.shared]

RESULT_WAIT_TIMEOUT = 2.0
RESULT_MATERIALIZATION_TEST_TIMEOUT = 15.0
"""Backend-tolerant timeout for tests that validate eventual queue discovery."""


def test_public_cmd_result_returns_structured_single_and_all_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = object()
    completed = TaskResult(
        tid="1779600000000000001",
        status="completed",
        value="done",
        stdout=None,
        stderr=None,
        error=None,
    )
    monkeypatch.setattr(result_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        result_cmd, "await_task_result", lambda *_args, **_kwargs: completed
    )
    monkeypatch.setattr(
        result_cmd, "_collect_all_task_results", lambda *_args, **_kwargs: (completed,)
    )

    assert result_cmd.cmd_result("1779600000000000001") is completed
    assert result_cmd.cmd_result(all=True) == (completed,)


def test_collect_all_results_consumes_each_outbox_in_one_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    outbox.write(json.dumps({"value": "first"}))
    outbox.write(json.dumps({"value": "second"}))
    context_type = type(ctx)
    original_queue = context_type.queue
    counts = {"inspections": 0, "exact_reads": 0}

    class QueueSpy:
        def __init__(self, queue: Queue) -> None:
            self.queue = queue

        def peek_generator(self, **kwargs: Any) -> Iterator[str | tuple[str, int]]:
            counts["inspections"] += 1
            return self.queue.peek_generator(**kwargs)

        def read_one(self, **kwargs: Any) -> str | tuple[str, int] | None:
            counts["exact_reads"] += 1
            value = self.queue.read_one(**kwargs)
            assert value is None or isinstance(value, (str, tuple))
            return value

        def close(self) -> None:
            self.queue.close()

    def queue_for(self: WeftContext, name: str, **kwargs: Any) -> QueueSpy | Queue:
        queue = original_queue(self, name, **kwargs)
        return QueueSpy(queue) if self is ctx and name == f"T{tid}.outbox" else queue

    monkeypatch.setattr(context_type, "queue", queue_for)
    results = result_cmd._collect_all_task_results(ctx, peek=False)

    assert len(results) == 1
    assert results[0].tid == tid
    assert counts == {"inspections": 1, "exact_reads": 2}
    assert outbox.peek_one() is None
    outbox.close()


def test_collect_all_results_leaves_partial_stream_chunks_for_running_task(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    partial = json.dumps({"type": "stream", "data": "still running"})
    outbox.write(partial)

    assert result_cmd._collect_all_task_results(ctx, peek=False) == ()
    assert outbox.peek_one() == partial
    outbox.close()


def test_collect_all_results_consumes_only_complete_stream_prefix(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    complete = json.dumps(
        {"type": "stream", "stream": "stdout", "data": "done", "final": True}
    )
    partial = json.dumps(
        {"type": "stream", "stream": "stdout", "data": "next", "final": False}
    )
    outbox.write(complete)
    outbox.write(partial)

    results = result_cmd._collect_all_task_results(ctx, peek=False)

    assert len(results) == 1
    assert results[0].value == "done"
    assert outbox.peek_one() == partial
    outbox.close()


def test_public_cmd_result_returns_structured_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = object()
    event = TaskEvent(
        tid="1779600000000000001",
        event_type="stdout",
        timestamp=1779600000000000002,
        payload={"data": "chunk"},
    )
    monkeypatch.setattr(result_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        result_cmd,
        "_result_task_event_stream",
        lambda *_args, **_kwargs: iter((event,)),
    )

    stream = result_cmd.cmd_result("1779600000000000001", stream=True)
    assert not isinstance(stream, (TaskResult, tuple))
    assert tuple(stream) == (event,)


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: result_cmd.cmd_result(),
        lambda: result_cmd.cmd_result("1779600000000000001", all=True),
        lambda: result_cmd.cmd_result(all=True, stream=True),
        lambda: result_cmd.cmd_result("1779600000000000001", peek=True),
        lambda: result_cmd.cmd_result(all=True, timeout=1.0),
    ],
)
def test_public_cmd_result_rejects_invalid_mode_combinations(
    invoke: Callable[[], object],
) -> None:
    with pytest.raises(CommandUsageError):
        invoke()


def test_public_cmd_result_translates_missing_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(result_cmd, "build_context", lambda spec_context=None: object())
    monkeypatch.setattr(
        result_cmd,
        "await_task_result",
        lambda *_args, **_kwargs: TaskResult(
            tid="1779600000000000001",
            status="missing",
            value=None,
            stdout=None,
            stderr=None,
            error="missing",
        ),
    )

    with pytest.raises(TaskNotFound, match="missing"):
        result_cmd.cmd_result("1779600000000000001", timeout=0.01)


def test_public_cmd_result_preserves_terminal_timeout_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_timeout = TaskResult(
        tid="1779600000000000001",
        status="timeout",
        value=None,
        stdout=None,
        stderr=None,
        error="target timed out",
    )
    monkeypatch.setattr(result_cmd, "build_context", lambda spec_context=None: object())
    monkeypatch.setattr(
        result_cmd,
        "await_task_result",
        lambda *_args, **_kwargs: terminal_timeout,
    )

    assert (
        result_cmd.cmd_result("1779600000000000001", timeout=0.01) is terminal_timeout
    )


def test_public_cmd_result_is_silent_and_translates_context_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = OSError("context unavailable")

    def fail_context(*_args: object, **_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(result_cmd, "build_context", fail_context)

    with pytest.raises(
        CommandExecutionError, match="failed to resolve result context"
    ) as caught:
        result_cmd.cmd_result("1779600000000000001")
    assert caught.value.__cause__ is failure
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


class ResultContextFailure(Exception):
    """Ordinary context/backend failure at the result command boundary."""


class ResultContextSignal(BaseException):
    """Fatal signal that the result command boundary must not contain."""


def test_collect_all_results_reports_backend_queue_enumeration_failure() -> None:
    error = PoolTimeout("connection pool unavailable")

    class FailingBroker:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def list_queues(self, *, pattern: str) -> list[str]:
            assert pattern == "T*.outbox"
            raise error

    class FakeContext:
        def broker(self) -> FailingBroker:
            return FailingBroker()

    with pytest.raises(
        CommandExecutionError, match="failed to enumerate task results"
    ) as exc_info:
        result_cmd._collect_all_task_results(FakeContext(), peek=False)  # type: ignore[arg-type]
    assert exc_info.value.__cause__ is error


@pytest.mark.parametrize("raw_tid", ["123", "T123", " T123 "])
def test_result_tid_normalization_removes_at_most_one_prefix(raw_tid: str) -> None:
    assert result_cmd._normalize_tid(raw_tid) == "123"


def test_cmd_result_reports_ordinary_context_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extension-defined context failures retain the command's error shape."""

    def fail_context(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ResultContextFailure("context detail")

    monkeypatch.setattr(result_cmd, "build_context", fail_context)

    with pytest.raises(
        CommandExecutionError, match="failed to resolve result context: context detail"
    ):
        cmd_result("1777000000000000789", context="ignored")


def test_cmd_result_propagates_fatal_context_resolution_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Result contains ordinary context failures, not BaseException signals."""

    signal = ResultContextSignal()

    def fail_context(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise signal

    monkeypatch.setattr(result_cmd, "build_context", fail_context)

    with pytest.raises(ResultContextSignal) as exc_info:
        cmd_result(
            tid="1777000000000000789", peek=False, timeout=None, context="ignored"
        )

    assert exc_info.value is signal


def _write_task_log_event(queue: Queue, tid: str, event: str, status: str) -> None:
    queue.write(
        json.dumps(
            {
                "tid": tid,
                "event": event,
                "status": status,
                "taskspec": {
                    "tid": tid,
                    "name": f"task-{tid[-4:]}",
                    "state": {"status": status},
                    "metadata": {},
                },
            }
        )
    )


def _one_shot_taskspec_payload(tid: str) -> dict[str, object]:
    return {
        "tid": tid,
        "name": f"task-{tid[-4:]}",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "runner": {"name": "host", "options": {}},
        },
        "io": {
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {"ctrl_out": f"T{tid}.ctrl_out"},
        },
        "state": {"status": "running", "started_at": time.time_ns()},
        "metadata": {},
    }


@contextmanager
def _result_consumer(
    context: WeftContext,
    tid: str,
    *,
    persistent: bool = False,
    stream_output: bool = False,
    function_target: str = "tests.tasks.sample_targets:echo_payload",
) -> Iterator[Consumer]:
    """Own real task publications and worker cleanup for result contracts."""
    payload = _one_shot_taskspec_payload(tid)
    payload["spec"] = {
        "type": "function",
        "function_target": function_target,
        "persistent": persistent,
        "stream_output": stream_output,
        "cleanup_on_exit": False,
        "enable_process_title": False,
        "weft_context": str(context.root),
    }
    consumer = Consumer(
        context.broker_target, TaskSpec.model_validate(payload), config=context.config
    )
    try:
        yield consumer
    finally:
        consumer.cleanup()


def _retire_result_proof(
    context: WeftContext,
    spec: TaskSpec,
    *,
    local_terminal: bool = False,
) -> None:
    """Retire a real producer's log; optionally retain only its result surface."""
    assert spec.tid is not None
    with context.queue(WEFT_GLOBAL_LOG_QUEUE) as log, log.get_connection() as broker:
        rows, _horizon = task_evidence.read_task_history(
            context, spec.tid, broker=broker
        )
        broker.delete_message_ids(
            WEFT_GLOBAL_LOG_QUEUE, [message_id for _payload, message_id in rows]
        )
    if local_terminal:
        for name in (spec.io.control["ctrl_out"], task_state_queue_name(spec.tid)):
            with context.queue(name) as queue:
                with closing_queue_iterator(
                    queue.peek_generator(with_timestamps=True)
                ) as entries:
                    message_ids = [message_id for _raw, message_id in entries]
                for message_id in message_ids:
                    assert queue.delete(message_id=message_id)


def _log_timestamps_by_tid(queue: Queue) -> dict[str, list[int]]:
    timestamps: dict[str, list[int]] = {}
    for payload, timestamp in iter_queue_json_entries(queue):
        tid = payload.get("tid")
        if isinstance(tid, str):
            timestamps.setdefault(tid, []).append(timestamp)
    return timestamps


def _publish_observation_fixtures(ctx: WeftContext, tid: str) -> None:
    """Give observer fixtures task-owned snapshots and local completion rows."""
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
        events = [
            payload
            for payload, _stamp in iter_queue_json_entries(log)
            if payload.get("tid") == tid
        ]
    latest_payload: dict[str, Any] = dict(_one_shot_taskspec_payload(tid))
    for event in events:
        payload = event.get("taskspec")
        if not isinstance(payload, dict):
            payload = latest_payload
        latest_payload = payload
        event = {**event, "taskspec": payload}
        state = payload.setdefault("state", {})
        if isinstance(state, dict):
            for key in ("status", "error"):
                if key in event:
                    state[key] = event[key]
        event.setdefault("event", "task_initialized")
        write_task_observation(ctx, event)
        if event["event"] in {
            "work_item_completed",
            "work_completed",
            "work_failed",
            "work_timeout",
        }:
            terminal = {**event, "type": "task_complete", "source": "task"}
            io = payload.get("io", {})
            name = (
                io.get("control", {}).get("ctrl_out", f"T{tid}.ctrl_out")
                if isinstance(io, dict)
                else f"T{tid}.ctrl_out"
            )
            with ctx.queue(name) as control:
                control.write(json.dumps(terminal))


def _capture_stream_echo(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    rendered: list[str] = []

    def _fake_echo(message: object = "", **kwargs: object) -> None:
        rendered.append(
            f"{message}{'' if kwargs.get('nl', True) is False else chr(10)}"
        )

    monkeypatch.setattr("weft.commands._streaming._emit_text", _fake_echo)
    return rendered


def test_handle_ctrl_stream_handles_malformed_base64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = _capture_stream_echo(monkeypatch)

    handle_ctrl_stream(
        json.dumps(
            {
                "type": "stream",
                "stream": "stdout",
                "encoding": "base64",
                "data": "%%%not-base64%%%",
            }
        )
    )

    assert rendered == []


def test_handle_ctrl_stream_preserves_stderr_and_final_newline_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[tuple[str, bool, bool]] = []

    def _fake_emit(
        text: str = "",
        *,
        err: bool = False,
        nl: bool = True,
    ) -> None:
        emitted.append((text, err, nl))

    monkeypatch.setattr("weft.commands._streaming._emit_text", _fake_emit)

    handle_ctrl_stream(
        json.dumps(
            {
                "type": "stream",
                "stream": "stderr",
                "encoding": "text",
                "data": "problem",
                "final": True,
            }
        )
    )

    assert emitted == [("problem", True, False), ("", True, True)]


def test_process_outbox_message_handles_malformed_base64() -> None:
    stream_buffer: list[str] = []

    final, value = process_outbox_message(
        json.dumps(
            {
                "type": "stream",
                "stream": "stdout",
                "encoding": "base64",
                "data": "%%%not-base64%%%",
                "final": True,
            }
        ),
        stream_buffer,
        emit_stream=False,
    )

    assert final is True
    assert value is not None
    assert value.value == ""


def test_consuming_stream_preserves_malformed_base64_transport(tmp_path: Path) -> None:
    context = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    spec = TaskSpec.model_validate(_one_shot_taskspec_payload(tid))
    with context.queue(spec.io.outputs["outbox"]) as outbox:
        outbox.write(
            json.dumps(
                {
                    "type": "stream",
                    "stream": "stdout",
                    "encoding": "base64",
                    "data": "%%%not-base64%%%",
                    "final": False,
                }
            )
        )
    source = result_cmd._iter_task_result(
        context,
        tid,
        timeout=0,
        include_stream_events=True,
        _accepted_taskspec=spec,
    )
    with closing_queue_iterator(source):
        frame = next(source)
        assert isinstance(frame, TaskEvent)
        assert frame.event_type == "stdout"
        assert frame.payload["data"] == "%%%not-base64%%%"


def test_task_history_reader_advances_cursor_over_unrelated_events(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    target_tid = str(time.time_ns())
    unrelated_tid = str(int(target_tid) + 1)

    _write_task_log_event(log_queue, target_tid, "task_started", "running")
    _write_task_log_event(log_queue, unrelated_tid, "task_started", "running")
    _write_task_log_event(log_queue, unrelated_tid, "work_completed", "completed")
    timestamps = _log_timestamps_by_tid(log_queue)
    target_timestamp = timestamps[target_tid][0]
    highest_timestamp = max(
        timestamp for values in timestamps.values() for timestamp in values
    )

    events, cursor = task_evidence.read_task_history(ctx, target_tid)

    assert [payload["tid"] for payload, _timestamp in events] == [target_tid]
    assert events[0][1] == target_timestamp
    assert cursor == highest_timestamp

    later_events, later_cursor = task_evidence.read_task_history(
        ctx, target_tid, after_timestamp=cursor
    )

    assert later_events == []
    assert later_cursor == cursor


def test_task_history_reader_advances_cursor_when_no_target_events(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    target_tid = str(time.time_ns())
    unrelated_tid = str(int(target_tid) + 1)

    _write_task_log_event(log_queue, unrelated_tid, "task_started", "running")
    _write_task_log_event(log_queue, unrelated_tid, "work_completed", "completed")
    timestamps = _log_timestamps_by_tid(log_queue)
    highest_timestamp = max(
        timestamp for values in timestamps.values() for timestamp in values
    )

    events, cursor = task_evidence.read_task_history(ctx, target_tid)

    assert events == []
    assert cursor == highest_timestamp


def test_iter_task_events_follow_advances_cursor_over_unrelated_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    target_tid = str(time.time_ns())
    unrelated_tid = str(int(target_tid) + 1)

    _write_task_log_event(log_queue, target_tid, "task_started", "running")
    _write_task_log_event(log_queue, unrelated_tid, "task_started", "running")
    _write_task_log_event(log_queue, unrelated_tid, "work_completed", "completed")
    timestamps = _log_timestamps_by_tid(log_queue)
    highest_unrelated_timestamp = max(timestamps[unrelated_tid])

    class _StopAfterSecondScan(Exception):
        pass

    class _NoSleepMonitor:
        def __init__(
            self, queues: Sequence[Queue], *, config: Config | None = None
        ) -> None:
            del queues, config

        def wait(self, timeout: float | None) -> bool:
            del timeout
            return False

        def close(self) -> None:
            return

    real_read = events_cmd.read_task_history
    since_timestamps: list[int | None] = []

    def recording_read(
        context: WeftContext,
        tid: str,
        *,
        broker: Any,
        after_timestamp: int | None = None,
    ) -> tuple[list[tuple[dict[str, Any], int]], int | None]:
        since_timestamps.append(after_timestamp)
        if len(since_timestamps) == 2:
            raise _StopAfterSecondScan
        return real_read(context, tid, broker=broker, after_timestamp=after_timestamp)

    monkeypatch.setattr(result_wait, "QueueChangeMonitor", _NoSleepMonitor)
    monkeypatch.setattr(events_cmd, "read_task_history", recording_read)

    event_iter = events_cmd.iter_task_events(
        ctx,
        target_tid,
        follow=True,
        timeout=10.0,
    )
    try:
        first_event = next(event_iter)
        assert first_event.tid == target_tid
        assert first_event.event_type == "task_started"
        with pytest.raises(CommandExecutionError) as caught:
            next(event_iter)
        assert isinstance(caught.value.__cause__, _StopAfterSecondScan)
    finally:
        close = getattr(event_iter, "close", None)
        assert callable(close)
        close()

    assert since_timestamps == [None, highest_unrelated_timestamp]


def test_iter_task_events_non_follow_yields_only_target_events(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    target_tid = str(time.time_ns())
    unrelated_tid = str(int(target_tid) + 1)

    _write_task_log_event(log_queue, target_tid, "task_started", "running")
    _write_task_log_event(log_queue, unrelated_tid, "task_started", "running")
    _write_task_log_event(log_queue, target_tid, "work_completed", "completed")

    events = list(events_cmd.iter_task_events(ctx, target_tid, follow=False))

    assert [event.event_type for event in events] == [
        "task_started",
        "work_completed",
    ]
    assert {event.tid for event in events} == {target_tid}


def test_result_acquisition_keeps_malformed_io_unavailable(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec_payload: dict[str, object] = {
        "tid": tid,
        "name": "malformed-io",
        "spec": {"type": "function"},
        "io": "not-a-dict",
        "state": {"status": "running"},
        "metadata": {},
    }

    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE) as log_queue:
        log_queue.write(json.dumps({"tid": tid, "taskspec": taskspec_payload}))

    materialized = result_wait.ResultMaterialization(None)
    with (
        ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log,
        log.get_connection() as broker,
    ):
        materialized, _rows = result_wait._acquire_result_observation(
            ctx, tid, materialized, log, broker, now=time.monotonic()
        )
    assert materialized.config is None


def test_iter_task_realtime_events_falls_back_on_malformed_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    payload = {
        "tid": tid,
        "spec": {"type": "function"},
        "io": "not-a-dict",
        "state": {"status": "running"},
        "metadata": {},
    }
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
        log.write(json.dumps({"tid": tid, "taskspec": payload}))
    events = list(events_cmd.iter_task_realtime_events(ctx, tid, follow=False))
    assert all(event.event_type != "result" for event in events)


def test_cmd_result_reports_failed_task_without_outbox(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue("weft.log.tasks", persistent=True)
    log_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "failed",
                "event": "work_failed",
                "error": "intentional failure",
                "taskspec": {
                    "tid": tid,
                    "name": "failed-task",
                    "io": {
                        "outputs": {"outbox": f"T{tid}.outbox"},
                        "control": {"ctrl_out": f"T{tid}.ctrl_out"},
                    },
                    "state": {
                        "status": "failed",
                        "started_at": time.time_ns() - 10,
                        "completed_at": time.time_ns(),
                        "error": "intentional failure",
                    },
                    "metadata": {},
                },
            }
        )
    )

    _publish_observation_fixtures(ctx, tid)
    result = cmd_result(tid=tid, peek=False, timeout=0.1, context=str(root))
    assert isinstance(result, TaskResult)

    assert result.status == "failed"
    assert result.error == "intentional failure"


def test_cmd_result_zero_timeout_reports_materialization_timeout(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    tid = str(time.time_ns())

    with pytest.raises(
        CommandTimeoutError, match=f"Timed out after 0.0 seconds waiting for task {tid}"
    ):
        cmd_result(tid, timeout=0.0, context=root)


def test_await_task_result_zero_timeout_reports_materialization_timeout(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())

    with pytest.raises(
        CommandTimeoutError,
        match=f"Timed out after 0.0 seconds waiting for task {tid}",
    ):
        await_task_result(ctx, tid, timeout=0.0)


def test_cmd_result_reports_claimed_outbox_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec_payload = _one_shot_taskspec_payload(tid)
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)
    monkeypatch.setattr(task_evidence, "STATUS_RUNTIMELESS_STALE_AFTER_SECONDS", -1.0)

    def fail_result_wait(*args: object, **kwargs: object) -> tuple[str, None, str]:
        raise AssertionError("claimed result blockage should not enter result wait")

    monkeypatch.setattr(result_wait.QueueChangeMonitor, "wait", fail_result_wait)
    try:
        log_queue.write(
            json.dumps(
                {
                    "tid": tid,
                    "status": "running",
                    "event": "work_started",
                    "taskspec": taskspec_payload,
                }
            )
        )
        outbox_queue.write(json.dumps({"ok": True}))
        assert outbox_queue.read_one() is not None

        _publish_observation_fixtures(ctx, tid)
        result = cmd_result(
            tid=tid, peek=False, timeout=RESULT_WAIT_TIMEOUT, context=str(root)
        )
        assert isinstance(result, TaskResult)
    finally:
        outbox_queue.close()
        log_queue.close()

    assert result.tid == tid
    assert result.status == "failed"
    assert result.value is None
    assert result.error is not None
    assert "claimed" in result.error
    assert result.reconciliation is not None
    assert result.reconciliation["classification"] == (
        "claimed_result_without_terminal"
    )


def test_await_task_result_reads_outbox_after_completion_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real terminal publication can precede visible output at the reader."""
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    original_wait = result_wait.QueueChangeMonitor.wait
    published = False
    with _result_consumer(ctx, tid) as consumer, ctx.queue(f"T{tid}.outbox") as outbox:
        assert consumer.run_work_item({"args": ["hello"]}) == "hello"
        pending = outbox.read_one()
        assert pending is not None

        def wait(monitor: Any, timeout: float | None) -> bool:
            nonlocal published
            if not published:
                outbox.write(pending)
                published = True
            return original_wait(monitor, timeout)

        monkeypatch.setattr(result_wait.QueueChangeMonitor, "wait", wait)
        observed = await_task_result(ctx, tid, timeout=RESULT_WAIT_TIMEOUT)
    assert published
    assert observed.status == "completed"
    assert observed.value == "hello"
    assert observed.error is None


def test_await_task_result_returns_visible_one_shot_result_at_deadline(
    tmp_path: Path,
) -> None:
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with _result_consumer(ctx, tid) as consumer:
        assert consumer.run_work_item({"args": ["ready"]}) == "ready"
        _retire_result_proof(ctx, consumer.taskspec, local_terminal=True)
        observed = await_task_result(
            ctx,
            tid,
            timeout=0.0,
            _accepted_taskspec=consumer.taskspec,
        )
    assert observed.status == "completed"
    assert observed.value == "ready"
    assert observed.error is None


def test_await_task_result_zero_timeout_does_not_wait_on_partial_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    spec = TaskSpec.model_validate(_one_shot_taskspec_payload(tid))

    def wait(_monitor: Any, timeout: float | None) -> bool:
        raise AssertionError(f"unexpected wait with timeout={timeout!r}")

    monkeypatch.setattr(result_wait.QueueChangeMonitor, "wait", wait)
    with ctx.queue(spec.io.outputs["outbox"]) as outbox:
        outbox.write(
            json.dumps(
                {
                    "type": "stream",
                    "stream": "stdout",
                    "data": "first\n",
                    "final": False,
                }
            )
        )
        with pytest.raises(CommandTimeoutError, match="Timed out after 0.0 seconds"):
            await_task_result(ctx, tid, timeout=0.0, _accepted_taskspec=spec)


def test_await_task_result_retains_terminal_ctrl_out_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    monkeypatch.setattr(result_wait, "WEFT_COMPLETED_RESULT_GRACE_SECONDS", 0.0)
    with _result_consumer(ctx, tid) as consumer:
        assert consumer.run_work_item({"args": ["hello"]}) == "hello"
        _retire_result_proof(ctx, consumer.taskspec)
        with ctx.queue(consumer.taskspec.io.control["ctrl_out"]) as control:
            terminal = control.peek_one()
            assert terminal is not None
            observed = await_task_result(
                ctx,
                tid,
                timeout=RESULT_WAIT_TIMEOUT,
                _accepted_taskspec=consumer.taskspec,
            )
            assert control.peek_one() == terminal
    assert observed.status == "completed"
    assert observed.value == "hello"
    assert observed.error is None


def test_await_task_result_prefers_task_completed_over_manager_wrapper_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Later manager inference cannot override a real task-owned terminal."""
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    monkeypatch.setattr(result_wait, "WEFT_COMPLETED_RESULT_GRACE_SECONDS", 0.0)
    with _result_consumer(ctx, tid) as consumer:
        assert consumer.run_work_item({"args": ["hello"]}) == "hello"
        _retire_result_proof(ctx, consumer.taskspec)
        with ctx.queue(consumer.taskspec.io.control["ctrl_out"]) as control:
            control.write(
                json.dumps(
                    {
                        "type": "terminal",
                        "source": "manager",
                        "tid": tid,
                        "status": "failed",
                        "error": task_evidence.WRAPPER_LOST_ERROR,
                        "return_code": 1,
                        "timestamp": time.time_ns(),
                    }
                )
            )
        observed = await_task_result(
            ctx, tid, timeout=RESULT_WAIT_TIMEOUT, _accepted_taskspec=consumer.taskspec
        )
    assert observed.status == "completed"
    assert observed.value == "hello"
    assert observed.error is None


def test_await_task_result_preserves_output_with_manager_wrapper_lost_failure(
    tmp_path: Path,
) -> None:
    """MF-5 typed terminal failure keeps acquired output for diagnostics."""
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with _result_consumer(ctx, tid) as consumer:
        assert consumer.run_work_item({"args": ["hello"]}) == "hello"
        _retire_result_proof(ctx, consumer.taskspec, local_terminal=True)
        with ctx.queue(consumer.taskspec.io.control["ctrl_out"]) as control:
            control.write(
                json.dumps(
                    {
                        "type": "terminal",
                        "source": "manager",
                        "tid": tid,
                        "status": "failed",
                        "error": task_evidence.WRAPPER_LOST_ERROR,
                        "return_code": 0,
                        "timestamp": time.time_ns(),
                    }
                )
            )
        observed = await_task_result(
            ctx, tid, timeout=RESULT_WAIT_TIMEOUT, _accepted_taskspec=consumer.taskspec
        )
    assert observed.status == "failed"
    assert observed.value == "hello"
    assert observed.error == task_evidence.WRAPPER_LOST_ERROR


def test_await_task_result_accepts_prewritten_outbox_when_log_event_is_missed(
    tmp_path: Path,
) -> None:
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with _result_consumer(
        ctx, tid, function_target="tests.tasks.sample_targets:provide_stdio"
    ) as consumer:
        assert consumer.run_work_item(
            {"kwargs": {"stdout": "out", "stderr": "err"}}
        ) == {"stdout": "out", "stderr": "err"}
        _retire_result_proof(ctx, consumer.taskspec, local_terminal=True)
        observed = await_task_result(
            ctx,
            tid,
            timeout=RESULT_WAIT_TIMEOUT,
            show_stderr=True,
            _accepted_taskspec=consumer.taskspec,
        )
    assert observed.status == "completed"
    assert observed.value == "err"
    assert observed.error is None


def test_await_task_result_accepts_single_primitive_outbox_when_log_event_is_missed(
    tmp_path: Path,
) -> None:
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with _result_consumer(ctx, tid) as consumer:
        assert consumer.run_work_item({"args": ["hello"]}) == "hello"
        _retire_result_proof(ctx, consumer.taskspec, local_terminal=True)
        observed = await_task_result(
            ctx, tid, timeout=RESULT_WAIT_TIMEOUT, _accepted_taskspec=consumer.taskspec
        )
    assert observed.status == "completed"
    assert observed.value == "hello"
    assert observed.error is None


def test_await_task_result_counts_emitted_final_with_real_terminal_proof(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emitted final presence survives an empty buffered value and retired log."""
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with _result_consumer(ctx, tid, stream_output=True) as consumer:
        assert consumer.run_work_item({"args": ["hello"]}) == "hello"
        _retire_result_proof(ctx, consumer.taskspec)
        observed = await_task_result(
            ctx,
            tid,
            timeout=RESULT_WAIT_TIMEOUT,
            emit_stream=True,
            _accepted_taskspec=consumer.taskspec,
        )
    assert observed.status == "completed"
    assert observed.value is None
    assert observed.error is None
    assert capsys.readouterr().out == "hello\n"


def test_await_task_result_does_not_infer_completion_from_ambiguous_outbox(
    tmp_path: Path,
) -> None:
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with _result_consumer(ctx, tid) as consumer, ctx.queue(f"T{tid}.outbox") as outbox:
        assert consumer.run_work_item({"args": ["hello"]}) == "hello"
        outbox.write("world")
        _retire_result_proof(ctx, consumer.taskspec, local_terminal=True)
        with pytest.raises(CommandTimeoutError, match="Timed out after 0.3 seconds"):
            await_task_result(
                ctx, tid, timeout=0.3, _accepted_taskspec=consumer.taskspec
            )


def test_await_task_result_returns_terminal_task_timeout_as_result(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    log_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "timeout",
                "event": "work_timeout",
                "error": "Target execution timed out",
            }
        )
    )

    _publish_observation_fixtures(ctx, tid)
    result = await_task_result(ctx, tid, timeout=RESULT_WAIT_TIMEOUT)

    assert result.status == "timeout"
    assert result.error == "Target execution timed out"


def test_await_task_result_aggregates_multiple_outbox_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    original_wait = result_wait.QueueChangeMonitor.wait
    published = False
    with _result_consumer(ctx, tid) as consumer, ctx.queue(f"T{tid}.outbox") as outbox:
        assert consumer.run_work_item({"args": ["hello"]}) == "hello"
        pending = outbox.read_one()
        assert pending is not None

        def wait(monitor: Any, timeout: float | None) -> bool:
            nonlocal published
            if not published:
                outbox.write(pending)
                outbox.write("world")
                published = True
            return original_wait(monitor, timeout)

        monkeypatch.setattr(result_wait.QueueChangeMonitor, "wait", wait)
        observed = await_task_result(ctx, tid, timeout=RESULT_WAIT_TIMEOUT)
    assert published
    assert observed.status == "completed"
    assert observed.value == ["hello", "world"]
    assert observed.error is None


def test_await_task_result_persistent_returns_one_work_item_batch(
    tmp_path: Path,
) -> None:
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with (
        _result_consumer(ctx, tid, persistent=True) as consumer,
        ctx.queue(f"T{tid}.outbox") as outbox,
    ):
        outbox.write("first")
        assert consumer.run_work_item({"args": ["second"]}) == "second"
        assert consumer.run_work_item({"args": ["third"]}) == "third"
        first = await_task_result(ctx, tid, timeout=RESULT_WAIT_TIMEOUT)
        assert outbox.peek_one() == "third"
        second = await_task_result(ctx, tid, timeout=RESULT_WAIT_TIMEOUT)
    assert first.status == second.status == "completed"
    assert first.value == ["first", "second"]
    assert second.value == "third"
    assert first.error is second.error is None


@pytest.mark.parametrize("emit_stream", [False, True], ids=["buffered", "rendering"])
def test_await_task_result_persistent_returns_quiet_visible_output_without_boundary(
    tmp_path: Path,
    emit_stream: bool,
) -> None:
    """Live rendering flags retain the same ordinary persistent quiet fallback."""
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with _result_consumer(ctx, tid, persistent=True) as consumer:
        assert consumer.run_work_item({"args": ["ready"]}) == "ready"
        _retire_result_proof(ctx, consumer.taskspec)
        observed = await_task_result(
            ctx,
            tid,
            timeout=RESULT_WAIT_TIMEOUT,
            emit_stream=emit_stream,
            _accepted_taskspec=consumer.taskspec,
        )
    assert observed.status == "completed"
    assert observed.value == "ready"
    assert observed.error is None


def test_await_task_result_stream_mode_emits_chunks_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Late chunks are rendered once after a real completion publication."""
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    original_wait = result_wait.QueueChangeMonitor.wait
    publications = 0
    with (
        _result_consumer(ctx, tid, stream_output=True) as consumer,
        ctx.queue(f"T{tid}.outbox") as outbox,
    ):
        assert consumer.run_work_item({"args": ["hello world"]}) == "hello world"
        assert outbox.read_one() is not None

        def wait(monitor: Any, timeout: float | None) -> bool:
            nonlocal publications
            if publications == 0:
                publications += 1
                for chunk, text, final in [(0, "hello ", False), (1, "world", True)]:
                    outbox.write(
                        json.dumps(
                            {
                                "type": "stream",
                                "stream": "stdout",
                                "chunk": chunk,
                                "final": final,
                                "encoding": "text",
                                "data": text,
                            }
                        )
                    )
            return original_wait(monitor, timeout)

        monkeypatch.setattr(result_wait.QueueChangeMonitor, "wait", wait)
        observed = await_task_result(
            ctx, tid, timeout=RESULT_WAIT_TIMEOUT, emit_stream=True
        )
    assert publications == 1
    assert observed.status == "completed"
    assert observed.value is None
    assert observed.error is None
    assert capsys.readouterr().out == "hello world\n"


def test_await_task_result_persistent_stream_mode_keeps_next_batch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctx = build_context(prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with (
        _result_consumer(ctx, tid, persistent=True, stream_output=True) as consumer,
        ctx.queue(f"T{tid}.outbox") as outbox,
    ):
        assert consumer.run_work_item({"args": ["first batch"]}) == "first batch"
        assert consumer.run_work_item({"args": ["third"]}) == "third"
        first = await_task_result(
            ctx, tid, timeout=RESULT_WAIT_TIMEOUT, emit_stream=True
        )
        assert capsys.readouterr().out == "first batch\n"
        assert outbox.peek_one() is not None
        second = await_task_result(
            ctx, tid, timeout=RESULT_WAIT_TIMEOUT, emit_stream=True
        )
        assert capsys.readouterr().out == "third\n"
    assert first.status == second.status == "completed"
    assert first.value is second.value is None
    assert first.error is second.error is None


def test_cmd_result_waits_for_custom_result_channels_to_materialize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    outbox_queue = ctx.queue("late.custom.outbox", persistent=True)
    default_outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)
    default_ctrl_queue = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    default_outbox_queue.close()
    default_ctrl_queue.close()

    taskspec_payload: dict[str, object] = {
        "tid": tid,
        "name": "late-custom-result",
        "spec": {"type": "function", "persistent": True},
        "io": {
            "outputs": {"outbox": "late.custom.outbox"},
            "control": {"ctrl_out": "late.custom.ctrl_out"},
        },
        "state": {"status": "running"},
        "metadata": {},
    }

    class _WakeMonitor:
        def __init__(
            self, _queues: Sequence[Queue], *, config: Config | None = None
        ) -> None:
            del config

        def wait(self, timeout: float | None) -> bool:
            del timeout
            log_queue.write(
                json.dumps(
                    {
                        "tid": tid,
                        "status": "running",
                        "event": "task_initialized",
                        "taskspec": taskspec_payload,
                    }
                )
            )
            outbox_queue.write("hello")
            log_queue.write(
                json.dumps(
                    {
                        "tid": tid,
                        "status": "running",
                        "event": "work_item_completed",
                        "taskspec": taskspec_payload,
                    }
                )
            )
            _publish_observation_fixtures(ctx, tid)
            return False

        def close(self) -> None:
            return

    monkeypatch.setattr(result_wait, "QueueChangeMonitor", _WakeMonitor)
    try:
        result = cmd_result(
            tid=tid,
            peek=False,
            timeout=RESULT_MATERIALIZATION_TEST_TIMEOUT,
            context=str(root),
        )
        assert isinstance(result, TaskResult)
    finally:
        outbox_queue.close()
        log_queue.close()

    assert result.status == "completed"
    assert result.value == "hello"


def test_await_task_result_tolerates_retained_boundary_before_output(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    outbox_queue = ctx.queue("late-polled-skewed.custom.outbox", persistent=True)

    taskspec_payload: dict[str, object] = {
        "tid": tid,
        "name": "late-polled-skewed-custom-result",
        "spec": {"type": "function", "persistent": True},
        "io": {
            "outputs": {"outbox": "late-polled-skewed.custom.outbox"},
            "control": {"ctrl_out": "late-polled-skewed.custom.ctrl_out"},
        },
        "state": {"status": "running"},
        "metadata": {},
    }

    log_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "running",
                "event": "work_item_completed",
                "taskspec": taskspec_payload,
            }
        )
    )
    outbox_queue.write("hello")

    _publish_observation_fixtures(ctx, tid)
    try:
        observed = await_task_result(ctx, tid, timeout=RESULT_WAIT_TIMEOUT)
    finally:
        outbox_queue.close()
        log_queue.close()

    assert observed.status == "completed"
    assert observed.value == "hello"
    assert observed.error is None


@pytest.mark.parametrize(
    "options,expected",
    [
        (
            {"tid": "123", "all": True, "peek": True, "timeout": 1.0, "stream": True},
            "task id cannot be used with all",
        ),
        (
            {"all": True, "peek": True, "timeout": 1.0, "stream": True},
            "stream cannot be used with all",
        ),
        (
            {"all": True, "peek": True, "timeout": 1.0},
            "timeout is not supported with all",
        ),
        ({"peek": True, "timeout": 1.0, "stream": True}, "peek requires all"),
        ({"timeout": 1.0, "stream": True}, "task id is required"),
    ],
)
def test_cmd_result_reports_conflicting_options_in_precedence_order(
    options: _ResultOptions, expected: str
) -> None:
    with pytest.raises(CommandUsageError, match=expected):
        cmd_result(**options)


def test_await_task_result_stream_preserves_error_payload_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)

    outbox_queue.write(json.dumps({"stdout": "out", "stderr": "err"}))
    result = await_task_result(
        ctx,
        tid,
        timeout=0.0,
        show_stderr=True,
        emit_stream=True,
        _accepted_taskspec=TaskSpec.model_validate(_one_shot_taskspec_payload(tid)),
    )

    assert result.status == "completed"
    assert result.value == "err"


def test_result_waits_for_taskspec_after_activity_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec_payload: dict[str, object] = {
        "tid": tid,
        "name": "late-custom-result",
        "spec": {"type": "function", "persistent": True},
        "io": {
            "outputs": {"outbox": "late.custom.outbox"},
            "control": {"ctrl_out": "late.custom.ctrl_out"},
        },
        "state": {"status": "running"},
        "metadata": {},
    }
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        log.write(
            json.dumps({"tid": tid, "status": "running", "event": "task_activity"})
        )

    class _DrivenMonitor:
        def __init__(
            self, _queues: Sequence[Queue], *, config: Config | None = None
        ) -> None:
            del config
            self.waits = 0

        def wait(self, timeout: float | None) -> bool:
            del timeout
            self.waits += 1
            assert self.waits == 1
            with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
                log.write(json.dumps({"tid": tid, "taskspec": taskspec_payload}))
            with ctx.queue("late.custom.outbox") as outbox:
                outbox.write("late-result")
            with ctx.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
                log.write(
                    json.dumps(
                        {
                            "tid": tid,
                            "event": "work_item_completed",
                            "status": "running",
                        }
                    )
                )
            _publish_observation_fixtures(ctx, tid)
            return True

        def close(self) -> None:
            return

    monkeypatch.setattr(result_wait, "QueueChangeMonitor", _DrivenMonitor)

    result = await_task_result(ctx, tid, timeout=RESULT_WAIT_TIMEOUT)
    assert result.status == "completed"
    assert result.value == "late-result"


def test_result_requires_pipeline_lifecycle_before_consuming_output(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox_queue = ctx.queue(f"P{tid}.outbox", persistent=True)
    status_queue = ctx.queue(f"P{tid}.status", persistent=True)

    outbox_queue.write("pipeline-result")
    status_queue.write(
        json.dumps(
            {
                "type": "pipeline_status",
                "pipeline_tid": tid,
                "status": "running",
            }
        )
    )

    with pytest.raises(CommandTimeoutError):
        cmd_result(tid=tid, peek=False, timeout=0, context=str(root))
    assert outbox_queue.peek_one() == "pipeline-result"
    retained_status = status_queue.peek_one()
    assert retained_status is not None
    assert json.loads(retained_status)["type"] == "pipeline_status"
