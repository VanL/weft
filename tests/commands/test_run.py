"""Tests for `weft run` wait helpers and interactive result assembly."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, WEFT_WORKERS_REGISTRY_QUEUE
from weft.commands.run import (
    _build_manager_spec,
    _collect_interactive_queue_output,
    _select_active_manager,
    _start_manager,
    _wait_for_task_completion,
)
from weft.context import build_context
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.shared]


def _make_taskspec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="wait-task",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
        metadata={},
    )


def test_wait_for_task_completion_reads_outbox_after_completion_event(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)

    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    log_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "completed",
                "event": "work_completed",
            }
        )
    )

    def _write_output() -> None:
        time.sleep(0.05)
        outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)
        try:
            outbox_queue.write("hello")
        finally:
            outbox_queue.close()

    writer = threading.Thread(target=_write_output, daemon=True)
    writer.start()
    try:
        status, result, error = _wait_for_task_completion(
            ctx,
            taskspec,
            json_output=False,
            verbose=False,
        )
    finally:
        writer.join(timeout=1.0)

    assert status == "completed"
    assert result == "hello"
    assert error is None


def test_wait_for_task_completion_aggregates_multiple_outbox_messages(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)

    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
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
        outbox_queue = ctx.queue(f"T{tid}.outbox", persistent=True)
        try:
            outbox_queue.write("hello")
            outbox_queue.write("world")
        finally:
            outbox_queue.close()
        time.sleep(0.05)

    writer = threading.Thread(target=_write_outputs, daemon=True)
    writer.start()
    try:
        status, result, error = _wait_for_task_completion(
            ctx,
            taskspec,
            json_output=False,
            verbose=False,
        )
    finally:
        writer.join(timeout=1.0)

    assert status == "completed"
    assert result == ["hello", "world"]
    assert error is None


class _FakePeekQueue:
    def __init__(self, records: Sequence[object]) -> None:
        self._records = records

    def peek_generator(self) -> Iterator[object]:
        yield from self._records


def test_collect_interactive_queue_output_reads_beyond_fixed_window() -> None:
    records = [
        (
            json.dumps(
                {
                    "type": "stream",
                    "stream": "stdout",
                    "chunk": index,
                    "final": False,
                    "encoding": "text",
                    "data": f"chunk-{index}\n",
                }
            ),
            index,
        )
        for index in range(600)
    ]
    queue = _FakePeekQueue(records)

    collected = _collect_interactive_queue_output(queue)  # type: ignore[arg-type]

    assert len(collected) == 600
    assert collected[0] == "chunk-0\n"
    assert collected[-1] == "chunk-599\n"


class _FakeManagerSpec:
    def model_dump_json(self) -> str:
        return "{}"


class _FakePopen:
    def __init__(self) -> None:
        self.pid = 4242
        self._poll_calls = 0

    def poll(self) -> int | None:
        self._poll_calls += 1
        return 0 if self._poll_calls >= 2 else None


def test_start_manager_does_not_terminate_competing_startup_manager(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen()
    competing_record = {"tid": "1775619800000000000", "pid": 31337}

    monkeypatch.setattr("weft.commands.run._generate_tid", lambda context: "9" * 19)
    monkeypatch.setattr(
        "weft.commands.run._build_manager_spec",
        lambda context, tid: _FakeManagerSpec(),
    )
    monkeypatch.setattr(
        "weft.commands.run.serialize_broker_target",
        lambda target: "{}",
    )
    monkeypatch.setattr(
        "weft.commands.run.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    records = iter([competing_record, competing_record])
    monkeypatch.setattr(
        "weft.commands.run._select_active_manager",
        lambda context: next(records),
    )

    terminated = False

    def _unexpected_terminate(*args: Any, **kwargs: Any) -> None:
        nonlocal terminated
        terminated = True

    monkeypatch.setattr(
        "weft.commands.run._terminate_manager_process",
        _unexpected_terminate,
    )

    record, started_here, handle = _start_manager(ctx, verbose=False)

    assert record == competing_record
    assert started_here is False
    assert handle is None
    assert terminated is False


def test_build_manager_spec_uses_tid_scoped_control_queues(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000001"

    spec = _build_manager_spec(ctx, tid)

    assert spec.io.control["ctrl_in"] == f"T{tid}.ctrl_in"
    assert spec.io.control["ctrl_out"] == f"T{tid}.ctrl_out"


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_select_active_manager_ignores_zombie_registry_pid(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        time.sleep(0.1)
        registry = ctx.queue(WEFT_WORKERS_REGISTRY_QUEUE, persistent=False)
        try:
            registry.write(
                json.dumps(
                    {
                        "tid": "1775622400000000001",
                        "name": "manager",
                        "status": "active",
                        "pid": process.pid,
                        "timestamp": registry.generate_timestamp(),
                        "inbox": "weft.spawn.requests",
                        "requests": "weft.spawn.requests",
                        "ctrl_in": "weft.manager.ctrl_in",
                        "ctrl_out": "weft.manager.ctrl_out",
                        "outbox": "weft.manager.outbox",
                        "role": "manager",
                    }
                )
            )
        finally:
            registry.close()

        assert _select_active_manager(ctx) is None
    finally:
        process.wait()
