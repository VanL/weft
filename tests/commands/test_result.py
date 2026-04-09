"""Tests for result helpers and terminal task reporting."""

from __future__ import annotations

import json
import threading
import time

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands.result import (
    _await_single_result,
    _load_taskspec_payload,
    cmd_result,
)
from weft.context import build_context

pytestmark = [pytest.mark.shared]


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
            timeout=1.0,
            show_stderr=False,
        )
    finally:
        writer.join(timeout=1.0)

    assert status == "completed"
    assert result == "hello"
    assert error is None


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
            timeout=1.0,
            show_stderr=False,
        )
    finally:
        writer.join(timeout=1.0)

    assert status == "completed"
    assert result == ["hello", "world"]
    assert error is None


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
        timeout=1.0,
        show_stderr=False,
    )
    second_status, second_result, second_error = _await_single_result(
        ctx,
        tid,
        timeout=1.0,
        show_stderr=False,
    )

    assert first_status == "completed"
    assert first_result == ["first", "second"]
    assert first_error is None
    assert second_status == "completed"
    assert second_result == "third"
    assert second_error is None


def test_cmd_result_waits_for_custom_result_channels_to_materialize(tmp_path) -> None:
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

    def writer() -> None:
        time.sleep(0.05)
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
        time.sleep(0.05)
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

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        exit_code, payload = cmd_result(
            tid=tid,
            all_results=False,
            peek=False,
            timeout=1.0,
            stream=False,
            json_output=False,
            show_stderr=False,
            context_path=str(root),
        )
    finally:
        thread.join(timeout=1.0)
        outbox_queue.close()
        log_queue.close()

    assert exit_code == 0
    assert payload == "hello"
