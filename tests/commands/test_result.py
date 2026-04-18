"""Tests for result helpers and terminal task reporting."""

from __future__ import annotations

import json
import threading
import time

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands import _result_wait as result_wait
from weft.commands import result as result_cmd
from weft.commands.result import (
    _await_single_result,
    _load_taskspec_payload,
    await_one_shot_result,
    cmd_result,
)
from weft.context import build_context

pytestmark = [pytest.mark.shared]

RESULT_WAIT_TIMEOUT = 2.0


def _capture_stream_echo(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    rendered: list[str] = []

    def _fake_echo(message: object = "", **kwargs: object) -> None:
        rendered.append(
            f"{message}{'' if kwargs.get('nl', True) is False else chr(10)}"
        )

    monkeypatch.setattr("weft.commands._streaming.typer.echo", _fake_echo)
    return rendered


def test_load_taskspec_payload_reads_full_log_history(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("weft.log.tasks", persistent=True)

    target_tid = str(time.time_ns())
    for index in range(2_050):
        tid = target_tid if index == 2_049 else str(1_700_000_000_000_000_000 + index)
        queue.write(
            json.dumps(
                {
                    "tid": tid,
                    "status": "completed",
                    "event": "work_completed",
                    "taskspec": {
                        "tid": tid,
                        "name": f"task-{index}",
                        "state": {"status": "completed"},
                        "io": {"outputs": {"outbox": f"T{tid}.outbox"}},
                        "metadata": {},
                    },
                }
            )
        )

    taskspec = _load_taskspec_payload(ctx, target_tid)

    assert taskspec is not None
    assert taskspec["tid"] == target_tid


def test_load_taskspec_payload_closes_log_queue() -> None:
    tid = "1844674407370955199"

    class FakeQueue:
        def __init__(self) -> None:
            self.closed = False

        def peek_generator(
            self,
            *,
            with_timestamps: bool = False,
            since_timestamp: int | None = None,
        ):
            assert with_timestamps is True
            assert since_timestamp == int(tid) - 1
            payload = json.dumps(
                {
                    "tid": tid,
                    "taskspec": {
                        "tid": tid,
                        "name": "closed-queue-task",
                        "state": {"status": "running"},
                    },
                }
            )
            return iter([(payload, int(tid))])

        def close(self) -> None:
            self.closed = True

    queue = FakeQueue()

    class FakeContext:
        def queue(self, name: str, *, persistent: bool = False) -> FakeQueue:
            assert name == WEFT_GLOBAL_LOG_QUEUE
            assert persistent is False
            return queue

    taskspec = _load_taskspec_payload(FakeContext(), tid)

    assert taskspec is not None
    assert taskspec["tid"] == tid
    assert queue.closed is True


def test_cmd_result_reports_failed_task_without_outbox(tmp_path) -> None:
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

    exit_code, payload = cmd_result(
        tid=tid,
        all_results=False,
        peek=False,
        timeout=0.1,
        stream=False,
        json_output=False,
        show_stderr=False,
        context_path=str(root),
    )

    assert exit_code == 1
    assert payload == "intentional failure"


def test_await_single_result_reads_outbox_after_completion_event(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)

    log_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "completed",
                "event": "work_completed",
            }
        )
    )

    writer = threading.Thread(
        target=lambda: (time.sleep(0.05), outbox_queue.write("hello")),
        daemon=True,
    )
    writer.start()
    try:
        status, result, error = _await_single_result(
            ctx,
            tid,
            timeout=RESULT_WAIT_TIMEOUT,
            show_stderr=False,
        )
    finally:
        writer.join(timeout=RESULT_WAIT_TIMEOUT)

    assert status == "completed"
    assert result == "hello"
    assert error is None


def test_await_one_shot_result_reads_outbox_after_completion_event(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)

    log_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "completed",
                "event": "work_completed",
            }
        )
    )

    writer = threading.Thread(
        target=lambda: (time.sleep(0.05), outbox_queue.write("hello")),
        daemon=True,
    )
    writer.start()
    try:
        status, result, error = await_one_shot_result(
            ctx,
            tid,
            outbox_name=f"T{tid}.outbox",
            ctrl_out_name=None,
            timeout=RESULT_WAIT_TIMEOUT,
            show_stderr=False,
        )
    finally:
        writer.join(timeout=RESULT_WAIT_TIMEOUT)

    assert status == "completed"
    assert result == "hello"
    assert error is None


def test_await_one_shot_result_accepts_prewritten_outbox_when_log_event_is_missed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox_name = f"T{tid}.outbox"
    outbox_queue = ctx.queue(outbox_name, persistent=True)
    outbox_queue.write(json.dumps({"stdout": "out", "stderr": "err"}))
    monkeypatch.setattr(
        result_wait,
        "poll_log_events",
        lambda log_queue, last_timestamp, target_tid: ([], last_timestamp),
    )

    try:
        status, result, error = await_one_shot_result(
            ctx,
            tid,
            outbox_name=outbox_name,
            ctrl_out_name=f"T{tid}.ctrl_out",
            timeout=RESULT_WAIT_TIMEOUT,
            show_stderr=True,
        )
    finally:
        outbox_queue.close()

    assert status == "completed"
    assert result == "err"
    assert error is None


def test_await_one_shot_result_does_not_infer_completion_from_ambiguous_outbox(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox_name = f"T{tid}.outbox"
    outbox_queue = ctx.queue(outbox_name, persistent=True)
    outbox_queue.write("hello")
    outbox_queue.write("world")
    monkeypatch.setattr(
        result_wait,
        "poll_log_events",
        lambda log_queue, last_timestamp, target_tid: ([], last_timestamp),
    )

    try:
        status, result, error = await_one_shot_result(
            ctx,
            tid,
            outbox_name=outbox_name,
            ctrl_out_name=f"T{tid}.ctrl_out",
            timeout=0.3,
            show_stderr=False,
        )
    finally:
        outbox_queue.close()

    assert status == "timeout"
    assert result is None
    assert error == f"Timed out after 0.3 seconds waiting for task {tid}"


def test_await_single_result_aggregates_multiple_outbox_messages(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)

    log_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "completed",
                "event": "work_completed",
            }
        )
    )

    def _write_outputs() -> None:
        time.sleep(0.05)
        outbox_queue.write("hello")
        outbox_queue.write("world")

    writer = threading.Thread(target=_write_outputs, daemon=True)
    writer.start()
    try:
        status, result, error = _await_single_result(
            ctx,
            tid,
            timeout=RESULT_WAIT_TIMEOUT,
            show_stderr=False,
        )
    finally:
        writer.join(timeout=RESULT_WAIT_TIMEOUT)

    assert status == "completed"
    assert result == ["hello", "world"]
    assert error is None


def test_await_single_result_classifies_timeout_event_as_timeout(tmp_path) -> None:
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

    status, result, error = _await_single_result(
        ctx,
        tid,
        timeout=RESULT_WAIT_TIMEOUT,
        show_stderr=False,
    )

    assert status == "timeout"
    assert result is None
    assert error == "Target execution timed out"


def test_await_single_result_persistent_returns_one_work_item_batch(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)

    taskspec_payload = {
        "tid": tid,
        "name": "persistent-agent",
        "spec": {"type": "agent", "persistent": True},
        "io": {
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {"ctrl_out": f"T{tid}.ctrl_out"},
        },
        "state": {"status": "running"},
        "metadata": {},
    }

    outbox_queue.write("first")
    outbox_queue.write("second")
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
    outbox_queue.write("third")
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

    first_status, first_result, first_error = _await_single_result(
        ctx,
        tid,
        timeout=RESULT_WAIT_TIMEOUT,
        show_stderr=False,
    )
    second_status, second_result, second_error = _await_single_result(
        ctx,
        tid,
        timeout=RESULT_WAIT_TIMEOUT,
        show_stderr=False,
    )

    assert first_status == "completed"
    assert first_result == ["first", "second"]
    assert first_error is None
    assert second_status == "completed"
    assert second_result == "third"
    assert second_error is None


def test_await_single_result_stream_mode_emits_chunks_without_replay(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)
    rendered = _capture_stream_echo(monkeypatch)

    log_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "completed",
                "event": "work_completed",
            }
        )
    )

    writes_triggered = 0

    class _LateStreamMonitor:
        def __init__(self, _queues, *, config=None) -> None:
            del config

        def wait(self, timeout: float | None) -> bool:
            del timeout
            nonlocal writes_triggered
            writes_triggered += 1
            if writes_triggered == 1:
                outbox_queue.write(
                    json.dumps(
                        {
                            "type": "stream",
                            "stream": "stdout",
                            "chunk": 0,
                            "final": False,
                            "encoding": "text",
                            "data": "hello ",
                        }
                    )
                )
                outbox_queue.write(
                    json.dumps(
                        {
                            "type": "stream",
                            "stream": "stdout",
                            "chunk": 1,
                            "final": True,
                            "encoding": "text",
                            "data": "world",
                        }
                    )
                )
            return False

        def close(self) -> None:
            return

    monkeypatch.setattr(result_wait, "QueueChangeMonitor", _LateStreamMonitor)

    status, result, error = _await_single_result(
        ctx,
        tid,
        timeout=RESULT_WAIT_TIMEOUT,
        show_stderr=False,
        emit_stream=True,
    )

    assert status == "completed"
    assert result is None
    assert error is None
    assert "".join(rendered) == "hello world\n"


def test_await_single_result_persistent_stream_mode_keeps_next_batch(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)
    rendered = _capture_stream_echo(monkeypatch)

    taskspec_payload = {
        "tid": tid,
        "name": "persistent-agent",
        "spec": {"type": "agent", "persistent": True},
        "io": {
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {"ctrl_out": f"T{tid}.ctrl_out"},
        },
        "state": {"status": "running"},
        "metadata": {},
    }

    outbox_queue.write(
        json.dumps(
            {
                "type": "stream",
                "stream": "stdout",
                "chunk": 0,
                "final": False,
                "encoding": "text",
                "data": "first ",
            }
        )
    )
    outbox_queue.write(
        json.dumps(
            {
                "type": "stream",
                "stream": "stdout",
                "chunk": 1,
                "final": True,
                "encoding": "text",
                "data": "batch",
            }
        )
    )
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
    outbox_queue.write("third")
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

    first_status, first_result, first_error = _await_single_result(
        ctx,
        tid,
        timeout=RESULT_WAIT_TIMEOUT,
        show_stderr=False,
        emit_stream=True,
    )
    second_status, second_result, second_error = _await_single_result(
        ctx,
        tid,
        timeout=RESULT_WAIT_TIMEOUT,
        show_stderr=False,
        emit_stream=True,
    )

    assert first_status == "completed"
    assert first_result is None
    assert first_error is None
    assert "".join(rendered) == "first batch\n"
    assert second_status == "completed"
    assert second_result == "third"
    assert second_error is None


def test_cmd_result_waits_for_custom_result_channels_to_materialize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    outbox_queue = ctx.queue("late.custom.outbox", persistent=True)

    taskspec_payload = {
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
        def __init__(self, _queues, *, config=None) -> None:
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
            return False

        def close(self) -> None:
            return

    monkeypatch.setattr(result_cmd, "QueueChangeMonitor", _WakeMonitor)
    try:
        exit_code, payload = cmd_result(
            tid=tid,
            all_results=False,
            peek=False,
            timeout=RESULT_WAIT_TIMEOUT,
            stream=False,
            json_output=False,
            show_stderr=False,
            context_path=str(root),
        )
    finally:
        outbox_queue.close()
        log_queue.close()

    assert exit_code == 0
    assert payload == "hello"


def test_await_single_result_reuses_materialized_batch_boundary_state(
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox_queue = ctx.queue("late.custom.outbox", persistent=True)

    taskspec_payload = {
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

    outbox_queue.write("hello")
    outbox_item = outbox_queue.peek_one(with_timestamps=True)
    assert isinstance(outbox_item, tuple)
    _payload, outbox_timestamp = outbox_item

    try:
        status, result, error = _await_single_result(
            ctx,
            tid=tid,
            timeout=0.1,
            show_stderr=False,
            taskspec_payload=taskspec_payload,
            outbox_name="late.custom.outbox",
            ctrl_out_name="late.custom.ctrl_out",
            initial_log_last_timestamp=123,
            initial_batch_boundary_timestamps=(outbox_timestamp,),
        )
    finally:
        outbox_queue.close()

    assert status == "completed"
    assert result == "hello"
    assert error is None


def test_cmd_result_rejects_stream_json_combination(tmp_path) -> None:
    root = prepare_project_root(tmp_path)

    exit_code, payload = cmd_result(
        tid="123",
        all_results=False,
        peek=False,
        timeout=RESULT_WAIT_TIMEOUT,
        stream=True,
        json_output=True,
        show_stderr=False,
        context_path=str(root),
    )

    assert exit_code == 2
    assert payload == "weft result: --stream cannot be used with --json"


def test_cmd_result_stream_preserves_error_payload_selection(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)

    outbox_queue.write(json.dumps({"stdout": "out", "stderr": "err"}))
    monkeypatch.setattr(result_cmd, "_queue_names_exist", lambda *_args: False)

    def _no_log_events(_queue, last_timestamp, _tid):
        return [], last_timestamp

    monkeypatch.setattr(result_cmd, "poll_log_events", _no_log_events)

    exit_code, payload = cmd_result(
        tid=tid,
        all_results=False,
        peek=False,
        timeout=0.0,
        stream=True,
        json_output=False,
        show_stderr=True,
        context_path=str(root),
    )

    assert exit_code == 0
    assert payload == "err"


def test_cmd_result_stream_succeeds_when_queue_enumeration_lags(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)

    log_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "completed",
                "event": "work_completed",
            }
        )
    )
    outbox_queue.write(json.dumps({"stdout": "out", "stderr": "err"}))
    monkeypatch.setattr(result_cmd, "_queue_names_exist", lambda *args, **kwargs: False)

    exit_code, payload = cmd_result(
        tid=tid,
        all_results=False,
        peek=False,
        timeout=RESULT_WAIT_TIMEOUT,
        stream=True,
        json_output=False,
        show_stderr=True,
        context_path=str(root),
    )

    assert exit_code == 0
    assert payload == "err"


def test_await_single_result_reuses_materialized_completion_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = _capture_stream_echo(monkeypatch)
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)
    outbox_queue.write(json.dumps({"stdout": "out", "stderr": "err"}))

    status, value, error_message = _await_single_result(
        ctx,
        tid,
        timeout=0.1,
        show_stderr=True,
        emit_stream=False,
        taskspec_payload=None,
        outbox_name=f"T{tid}.outbox",
        ctrl_out_name=f"T{tid}.ctrl_out",
        initial_log_last_timestamp=123,
        initial_terminal_status="completed",
        initial_terminal_error_message=None,
    )

    assert status == "completed"
    assert value == "err"
    assert error_message is None
    assert rendered == []


def test_cmd_result_passes_materialized_state_to_result_wait(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    tid = str(time.time_ns())
    captured: dict[str, object] = {}

    materialized = result_cmd.ResultMaterialization(
        taskspec_payload=None,
        outbox_name=f"T{tid}.outbox",
        ctrl_out_name=f"T{tid}.ctrl_out",
        log_last_timestamp=123,
        terminal_status="completed",
        terminal_error_message=None,
        batch_boundary_timestamps=(456,),
    )

    monkeypatch.setattr(
        result_cmd.time,
        "monotonic",
        lambda: 100.0,
    )
    monkeypatch.setattr(
        result_cmd,
        "_await_result_materialization",
        lambda *args, **kwargs: materialized,
    )
    monkeypatch.setattr(
        result_cmd,
        "_await_single_result",
        lambda *args, **kwargs: captured.update(kwargs) or ("completed", "err", None),
    )

    exit_code, payload = cmd_result(
        tid=tid,
        all_results=False,
        peek=False,
        timeout=RESULT_WAIT_TIMEOUT,
        stream=True,
        json_output=False,
        show_stderr=True,
        context_path=str(root),
    )

    assert exit_code == 0
    assert payload == "err"
    assert captured["taskspec_payload"] is None
    assert captured["outbox_name"] == materialized.outbox_name
    assert captured["ctrl_out_name"] == materialized.ctrl_out_name
    assert captured["initial_log_last_timestamp"] == materialized.log_last_timestamp
    assert captured["initial_terminal_status"] == materialized.terminal_status
    assert (
        captured["initial_terminal_error_message"]
        == materialized.terminal_error_message
    )
    assert (
        captured["initial_batch_boundary_timestamps"]
        == materialized.batch_boundary_timestamps
    )


def test_result_reads_pipeline_outbox_by_pipeline_tid(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
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
    log_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "completed",
                "event": "work_completed",
                "taskspec": {
                    "tid": tid,
                    "name": "demo-pipeline",
                    "io": {
                        "outputs": {"outbox": f"P{tid}.outbox"},
                        "control": {"ctrl_out": f"P{tid}.ctrl_out"},
                    },
                    "state": {
                        "status": "completed",
                        "started_at": time.time_ns() - 10,
                        "completed_at": time.time_ns(),
                    },
                    "metadata": {
                        "role": "pipeline",
                        "_weft_pipeline_runtime": {
                            "queues": {"status": f"P{tid}.status"},
                        },
                    },
                },
            }
        )
    )

    exit_code, payload = cmd_result(
        tid=tid,
        all_results=False,
        peek=False,
        timeout=RESULT_WAIT_TIMEOUT,
        stream=False,
        json_output=False,
        show_stderr=False,
        context_path=str(root),
    )

    assert exit_code == 0
    assert payload == "pipeline-result"
    retained_status = status_queue.peek_one()
    assert retained_status is not None
    assert json.loads(retained_status)["type"] == "pipeline_status"
