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

import weft.commands._manager_bootstrap as manager_lifecycle
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.commands.run import (
    _build_manager_spec,
    _collect_interactive_queue_output,
    _delete_spawn_request,
    _enqueue_taskspec,
    _run_inline,
    _run_pipeline,
    _run_spec_via_manager,
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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

    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._generate_tid",
        lambda context: "9" * 19,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._build_manager_spec",
        lambda context, tid: _FakeManagerSpec(),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.serialize_broker_target",
        lambda target: "{}",
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    records = iter([competing_record, competing_record])
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._select_active_manager",
        lambda context: next(records),
    )

    terminated = False

    def _unexpected_terminate(*args: Any, **kwargs: Any) -> None:
        nonlocal terminated
        terminated = True

    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._terminate_manager_process",
        _unexpected_terminate,
    )

    record, started_here, handle = _start_manager(ctx, verbose=False)

    assert record == competing_record
    assert started_here is False
    assert handle is None
    assert terminated is False


def test_run_inline_enqueues_task_before_ensuring_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    calls: list[str] = []

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)
    monkeypatch.setattr("weft.commands.run.stdin_is_tty", lambda: False)
    monkeypatch.setattr("weft.commands.run.typer.echo", lambda *args, **kwargs: None)

    def _fake_enqueue(context_arg, taskspec, work_payload):
        calls.append("enqueue")
        return 1775679597297004544

    def _fake_ensure(context_arg, *, verbose):
        calls.append("ensure")
        return (
            {"tid": "1775679596841701376", "ctrl_in": "Tmanager.ctrl_in"},
            False,
            None,
        )

    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _fake_enqueue)
    monkeypatch.setattr("weft.commands.run._ensure_manager", _fake_ensure)

    exit_code = _run_inline(
        command=(),
        function_target="tests.tasks.sample_targets:echo_payload",
        args=(),
        kwargs=(),
        env=(),
        name=None,
        interactive=False,
        stream_output=None,
        timeout=None,
        memory=None,
        cpu=None,
        tags=(),
        context_dir=root,
        wait=False,
        json_output=False,
        verbose=False,
        autostart_enabled=True,
    )

    assert exit_code == 0
    assert calls == ["enqueue", "ensure"]


def test_delete_spawn_request_removes_queued_message(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)

    message_timestamp = _enqueue_taskspec(context, taskspec, None)

    _delete_spawn_request(context, message_timestamp)

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.read_one() is None
    finally:
        queue.close()


def test_delete_spawn_request_swallows_delete_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    closed = False

    class _FakeQueue:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def delete(self, *, message_id: int) -> None:
            del message_id
            raise RuntimeError("delete failed")

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr("weft.commands.run.Queue", _FakeQueue)

    _delete_spawn_request(context, 1775679597297004544)

    assert closed is True


def test_run_inline_deletes_spawn_request_when_ensure_manager_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)
    monkeypatch.setattr("weft.commands.run.stdin_is_tty", lambda: False)
    monkeypatch.setattr("weft.commands.run.typer.echo", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager",
        lambda context_arg, *, verbose: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    exit_code = _run_inline(
        command=(),
        function_target="tests.tasks.sample_targets:echo_payload",
        args=(),
        kwargs=(),
        env=(),
        name=None,
        interactive=False,
        stream_output=None,
        timeout=None,
        memory=None,
        cpu=None,
        tags=(),
        context_dir=root,
        wait=False,
        json_output=False,
        verbose=False,
        autostart_enabled=True,
    )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.read_one() is None
    finally:
        queue.close()
    assert exit_code == 1


def test_run_spec_via_manager_deletes_spawn_request_when_ensure_manager_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    spec_path = root / "task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)
    monkeypatch.setattr("weft.commands.run.typer.echo", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager",
        lambda context_arg, *, verbose: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    exit_code = _run_spec_via_manager(
        spec_path,
        verbose=False,
        wait=False,
        json_output=False,
        autostart_enabled=True,
        persistent_override=None,
    )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.read_one() is None
    finally:
        queue.close()
    assert exit_code == 1


def test_run_pipeline_deletes_spawn_request_when_ensure_manager_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    task_spec_path = context.weft_dir / "tasks" / "stage-task.json"
    pipeline_path = root / "pipeline.json"
    _write_json(
        task_spec_path,
        {
            "name": "stage-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )
    _write_json(
        pipeline_path,
        {
            "name": "demo-pipeline",
            "stages": [{"task": "stage-task"}],
        },
    )

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager",
        lambda context_arg, *, verbose: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        _run_pipeline(
            pipeline_path,
            pipeline_input=None,
            context_dir=root,
            wait=True,
            json_output=False,
            verbose=False,
            autostart_enabled=True,
        )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.read_one() is None
    finally:
        queue.close()


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
        process.wait(timeout=2.0)
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


def test_select_active_manager_ignores_noncanonical_request_queue(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_WORKERS_REGISTRY_QUEUE, persistent=False)
    try:
        registry.write(
            json.dumps(
                {
                    "tid": "1775622400000000002",
                    "name": "manager",
                    "status": "active",
                    "pid": os.getpid(),
                    "timestamp": registry.generate_timestamp(),
                    "inbox": "custom.manager.requests",
                    "requests": "custom.manager.requests",
                    "ctrl_in": "custom.manager.ctrl_in",
                    "ctrl_out": "custom.manager.ctrl_out",
                    "outbox": "custom.manager.outbox",
                    "role": "manager",
                }
            )
        )
    finally:
        registry.close()

    assert _select_active_manager(ctx) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_list_manager_records_prunes_dead_active_and_preserves_stopped_history(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_WORKERS_REGISTRY_QUEUE, persistent=False)
    dead_tid = "1775622400000000003"
    stopped_tid = "1775622400000000004"

    dead_process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        dead_process.wait(timeout=2.0)
        registry.write(
            json.dumps(
                {
                    "tid": dead_tid,
                    "name": "dead-manager",
                    "status": "active",
                    "pid": dead_process.pid,
                    "timestamp": registry.generate_timestamp(),
                    "requests": "custom.manager.requests",
                    "ctrl_in": "custom.manager.ctrl_in",
                    "ctrl_out": "custom.manager.ctrl_out",
                    "outbox": "custom.manager.outbox",
                    "role": "manager",
                }
            )
        )
        registry.write(
            json.dumps(
                {
                    "tid": stopped_tid,
                    "name": "stopped-manager",
                    "status": "stopped",
                    "pid": dead_process.pid,
                    "timestamp": registry.generate_timestamp(),
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                    "ctrl_in": f"T{stopped_tid}.ctrl_in",
                    "ctrl_out": f"T{stopped_tid}.ctrl_out",
                    "outbox": "weft.manager.outbox",
                    "role": "manager",
                }
            )
        )
    finally:
        registry.close()

    first = manager_lifecycle._list_manager_records(
        ctx,
        include_stopped=True,
        canonical_only=False,
    )
    second = manager_lifecycle._list_manager_records(
        ctx,
        include_stopped=True,
        canonical_only=False,
    )

    assert {record["tid"] for record in first} == {stopped_tid}
    assert {record["tid"] for record in second} == {stopped_tid}

    registry_reader = ctx.queue(WEFT_WORKERS_REGISTRY_QUEUE, persistent=False)
    try:
        entries = [
            payload
            for payload, _timestamp in manager_lifecycle.iter_queue_json_entries(
                registry_reader
            )
        ]
    finally:
        registry_reader.close()

    assert [entry["tid"] for entry in entries] == [stopped_tid]


def test_stop_manager_waits_for_pid_exit_after_stopped_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000005"
    seen_pids: list[int | None] = []

    responses = iter(
        [
            {"tid": tid, "status": "active", "pid": 4321},
            {"tid": tid, "status": "stopped", "pid": 4321},
            {"tid": tid, "status": "stopped", "pid": 4321},
            None,
        ]
    )
    pid_states = iter([True, True, False, False])

    monkeypatch.setattr(manager_lifecycle, "_send_stop", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        manager_lifecycle,
        "_manager_record",
        lambda *args, **kwargs: next(responses),
    )

    def fake_pid_alive(pid: int | None) -> bool:
        seen_pids.append(pid)
        return next(pid_states)

    monkeypatch.setattr(manager_lifecycle, "_is_pid_alive", fake_pid_alive)

    stopped, message = manager_lifecycle._stop_manager(
        ctx,
        None,
        tid=tid,
        timeout=1.0,
        stop_if_absent=True,
    )

    assert stopped is True
    assert message is None
    assert seen_pids.count(4321) >= 2


def test_stop_manager_force_prefers_process_tree_kill_when_pid_known(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000006"
    killed: list[tuple[int, float]] = []

    monkeypatch.setattr(manager_lifecycle, "_send_stop", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        manager_lifecycle, "_manager_record", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        manager_lifecycle, "_lookup_manager_pid", lambda *args, **kwargs: 8765
    )
    monkeypatch.setattr(manager_lifecycle, "_is_pid_alive", lambda pid: pid == 8765)

    def fake_terminate_process_tree(
        pid: int, *, timeout: float, kill_after: bool = True
    ):
        killed.append((pid, timeout))
        return {pid}

    monkeypatch.setattr(
        manager_lifecycle,
        "terminate_process_tree",
        fake_terminate_process_tree,
    )

    stopped, message = manager_lifecycle._stop_manager(
        ctx,
        None,
        tid=tid,
        timeout=0.0,
        force=True,
    )

    assert stopped is True
    assert message is None
    assert killed == [(8765, 0.0)]
