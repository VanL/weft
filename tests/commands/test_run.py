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
import typer

import weft.commands._manager_bootstrap as manager_lifecycle
import weft.commands._spawn_submission as spawn_submission_cmd
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.commands import tasks as task_cmd
from weft.commands._spawn_submission import (
    SpawnSubmissionReconciliation,
    reconcile_submitted_spawn,
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


def _wait_for_task_snapshot(
    root: Path,
    tid: str,
    *,
    timeout: float = 10.0,
) -> task_cmd.status_cmd.TaskSnapshot:
    deadline = time.time() + timeout
    last_snapshot: task_cmd.status_cmd.TaskSnapshot | None = None
    while time.time() < deadline:
        snapshot = task_cmd.task_status(tid, context_path=root)
        if snapshot is not None:
            last_snapshot = snapshot
            return snapshot
        time.sleep(0.05)
    raise AssertionError(
        f"Timed out waiting for task snapshot for {tid}: "
        f"{last_snapshot.to_dict() if last_snapshot is not None else None}"
    )


def _wait_for_task_status(
    root: Path,
    tid: str,
    *,
    expected_status: str,
    timeout: float = 10.0,
) -> task_cmd.status_cmd.TaskSnapshot:
    deadline = time.time() + timeout
    last_snapshot: task_cmd.status_cmd.TaskSnapshot | None = None
    while time.time() < deadline:
        snapshot = task_cmd.task_status(tid, context_path=root)
        if snapshot is not None:
            last_snapshot = snapshot
            if snapshot.status == expected_status:
                return snapshot
        time.sleep(0.05)
    raise AssertionError(
        f"Timed out waiting for {expected_status} snapshot for {tid}: "
        f"{last_snapshot.to_dict() if last_snapshot is not None else None}"
    )


def _stop_active_manager(context) -> None:
    record = manager_lifecycle._select_active_manager(context)
    if record is None:
        return
    stopped, error = manager_lifecycle._stop_manager(
        context,
        record,
        timeout=5.0,
        stop_if_absent=True,
    )
    assert stopped, error


def _registry_view(
    *,
    active: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    records: dict[str, dict[str, Any]] | None = None,
) -> manager_lifecycle._ManagerRegistryView:
    return manager_lifecycle._ManagerRegistryView(
        records={} if records is None else records,
        active_manager=active,
        target_record=target,
    )


class _FakeQueueChangeMonitor:
    def __init__(self, queues, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.queue_names = [queue.name for queue in queues]
        self.wait_calls: list[float | None] = []

    def wait(self, timeout: float | None) -> bool:
        self.wait_calls.append(timeout)
        return False

    def close(self) -> None:
        return


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
        time.sleep(0.3)
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


def test_wait_for_task_completion_classifies_timeout_event_as_timeout(
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
                "status": "timeout",
                "event": "work_timeout",
                "error": "Target execution timed out",
            }
        )
    )

    status, result, error = _wait_for_task_completion(
        ctx,
        taskspec,
        json_output=False,
        verbose=False,
    )

    assert status == "timeout"
    assert result is None
    assert error == "Target execution timed out"


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
    def __init__(
        self,
        *,
        pid: int = 4242,
        poll_results: Sequence[int | None] | None = None,
        communicate_result: tuple[str, str] = ("", ""),
    ) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._poll_calls = 0
        self._poll_results = list(poll_results or [None, 0])
        self._communicate_result = communicate_result
        self.stdin = None

    def poll(self) -> int | None:
        if self._poll_calls < len(self._poll_results):
            result = self._poll_results[self._poll_calls]
        else:
            result = self._poll_results[-1]
        self._poll_calls += 1
        self.returncode = result
        return result

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        return self._communicate_result


def test_start_manager_does_not_terminate_competing_startup_manager(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[1])
    competing_record = {"tid": "1775619800000000000", "pid": 31337}
    launch = manager_lifecycle._DetachedManagerLaunch(
        pid=4242,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=fake_process,
    )

    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._build_manager_runtime_invocation",
        lambda context: manager_lifecycle._ManagerRuntimeInvocation(
            task_cls_path="weft.core.manager.Manager",
            tid="9" * 19,
            spec=_FakeManagerSpec(),
        ),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._launch_detached_manager",
        lambda context, invocation: launch,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )

    records = iter([competing_record, competing_record])
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, queue=None: (
            _registry_view(
                active=next(records),
            )
        ),
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


def test_start_manager_adopts_competing_manager_after_losing_pid_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[None, None, None])
    competing_record = {
        "tid": "1775619800000000000",
        "pid": 31337,
        "status": "active",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "role": "manager",
    }
    launch = manager_lifecycle._DetachedManagerLaunch(
        pid=4242,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=fake_process,
    )
    cleaned_paths: list[Path] = []

    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._build_manager_runtime_invocation",
        lambda context: manager_lifecycle._ManagerRuntimeInvocation(
            task_cls_path="weft.core.manager.Manager",
            tid="9" * 19,
            spec=_FakeManagerSpec(),
        ),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._launch_detached_manager",
        lambda context, invocation: launch,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )

    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, queue=None: (
            _registry_view()
        ),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._await_manager_start_settlement",
        lambda context, *, manager_tid, deadline: competing_record,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._is_pid_alive",
        lambda pid: False,
    )
    monotonic_values = iter([0.0, 0.0, 0.1, 0.2])
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.time.monotonic",
        lambda: next(monotonic_values, 1.0),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.time.sleep",
        lambda seconds: None,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._cleanup_startup_stderr",
        lambda path: cleaned_paths.append(path),
    )

    record, started_here, handle = _start_manager(ctx, verbose=False)

    assert record == competing_record
    assert started_here is False
    assert handle is None
    assert cleaned_paths == [launch.stderr_path]


def test_start_manager_builds_detached_launch_from_shared_runtime_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[None, None])
    invocation = manager_lifecycle._ManagerRuntimeInvocation(
        task_cls_path="weft.core.manager.Manager",
        tid="9" * 19,
        spec=_FakeManagerSpec(),
    )
    helper_calls: list[tuple[object, object]] = []
    launch_calls: list[tuple[object, object]] = []
    acked: list[object] = []
    launch = manager_lifecycle._DetachedManagerLaunch(
        pid=fake_process.pid,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=fake_process,
    )

    def _fake_build_invocation(context_arg, *, idle_timeout_override=None):
        helper_calls.append((context_arg, idle_timeout_override))
        return invocation

    def _fake_launch(context_arg, invocation_arg):
        launch_calls.append((context_arg, invocation_arg))
        return launch

    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._build_manager_runtime_invocation",
        _fake_build_invocation,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._launch_detached_manager",
        _fake_launch,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._acknowledge_manager_launch_success",
        lambda launch_arg: acked.append(launch_arg),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, queue=None: (
            _registry_view(
                active={
                    "tid": invocation.tid,
                    "pid": fake_process.pid,
                    "status": "active",
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                    "role": "manager",
                }
            )
        ),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._is_pid_alive",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._cleanup_startup_stderr",
        lambda path: None,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.time.sleep",
        lambda seconds: None,
    )

    record, started_here, handle = _start_manager(ctx, verbose=False)

    assert record["tid"] == invocation.tid
    assert record["pid"] == fake_process.pid
    assert started_here is True
    assert handle is None
    assert helper_calls == [(ctx, None)]
    assert launch_calls == [(ctx, invocation)]
    assert acked == [launch]


def test_start_manager_treats_post_proof_ack_failure_as_nonfatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[None, None, 0])
    invocation = manager_lifecycle._ManagerRuntimeInvocation(
        task_cls_path="weft.core.manager.Manager",
        tid="9" * 19,
        spec=_FakeManagerSpec(),
    )
    launch = manager_lifecycle._DetachedManagerLaunch(
        pid=fake_process.pid,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=fake_process,
    )
    warnings: list[str] = []

    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._build_manager_runtime_invocation",
        lambda context: invocation,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._launch_detached_manager",
        lambda context, invocation_arg: launch,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._acknowledge_manager_launch_success",
        lambda launch_arg: (_ for _ in ()).throw(
            RuntimeError("post-proof acknowledgement failed")
        ),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, queue=None: (
            _registry_view(
                active={
                    "tid": invocation.tid,
                    "pid": fake_process.pid,
                    "status": "active",
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                    "role": "manager",
                }
            )
        ),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._is_pid_alive",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.logger.warning",
        lambda message, *args, **kwargs: warnings.append(str(message)),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.time.sleep",
        lambda seconds: None,
    )

    record, started_here, handle = _start_manager(ctx, verbose=False)

    assert record["tid"] == invocation.tid
    assert record["pid"] == fake_process.pid
    assert started_here is True
    assert handle is None
    assert warnings


def test_start_manager_surfaces_detached_launch_stderr_when_manager_exits_early(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    stderr_path = root / ".weft" / "logs" / "manager-startup" / "manager.stderr"
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.write_text("boom on startup\ntraceback details\n", encoding="utf-8")
    fake_process = _FakePopen(
        poll_results=[10],
        communicate_result=(
            json.dumps({"event": "child_exit", "returncode": 23}) + "\n",
            "",
        ),
    )
    launch = manager_lifecycle._DetachedManagerLaunch(
        pid=4242,
        stderr_path=stderr_path,
        launcher_process=fake_process,
    )
    errors: list[str] = []

    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._build_manager_runtime_invocation",
        lambda context: manager_lifecycle._ManagerRuntimeInvocation(
            task_cls_path="weft.core.manager.Manager",
            tid="9" * 19,
            spec=_FakeManagerSpec(),
        ),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._launch_detached_manager",
        lambda context, invocation: launch,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, queue=None: (
            _registry_view()
        ),
    )
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap.typer.echo",
        lambda message, err=False: errors.append(message) if err else None,
    )

    with pytest.raises(typer.Exit) as exc_info:
        _start_manager(ctx, verbose=False)

    assert exc_info.value.exit_code == 1
    assert errors
    assert "return code 23" in errors[0]
    assert "traceback details" in errors[0]


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


def test_run_inline_no_wait_succeeds_when_post_proof_acknowledgement_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid: dict[str, str] = {}
    original_enqueue = _enqueue_taskspec

    def _recording_enqueue(*args: Any, **kwargs: Any) -> int:
        tid = original_enqueue(*args, **kwargs)
        submitted_tid["value"] = str(tid)
        return tid

    def _fail_ack_after_spawn(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        deadline = time.time() + 8.0
        while time.time() < deadline:
            tid = submitted_tid.get("value")
            if tid and task_cmd.task_status(tid, context_path=root) is not None:
                raise RuntimeError("synthetic late acknowledgement failure")
            time.sleep(0.05)
        raise RuntimeError("timed out waiting for spawned task visibility")

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)
    monkeypatch.setattr("weft.commands.run.stdin_is_tty", lambda: False)
    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _recording_enqueue)
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._acknowledge_manager_launch_success",
        _fail_ack_after_spawn,
    )
    monkeypatch.setattr("weft.commands.run.typer.echo", lambda *args, **kwargs: None)

    try:
        exit_code = _run_inline(
            command=(),
            function_target="tests.tasks.sample_targets:provide_payload",
            args=(),
            kwargs=(),
            env=(),
            name="provide-payload",
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
        tid = submitted_tid["value"]
        snapshot = _wait_for_task_status(root, tid, expected_status="completed")
        assert snapshot.tid == tid
    finally:
        _stop_active_manager(context)


def test_run_inline_wait_succeeds_when_post_proof_acknowledgement_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid: dict[str, str] = {}
    original_enqueue = _enqueue_taskspec

    def _recording_enqueue(*args: Any, **kwargs: Any) -> int:
        tid = original_enqueue(*args, **kwargs)
        submitted_tid["value"] = str(tid)
        return tid

    def _fail_ack_after_spawn(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        deadline = time.time() + 8.0
        while time.time() < deadline:
            tid = submitted_tid.get("value")
            if tid and task_cmd.task_status(tid, context_path=root) is not None:
                raise RuntimeError("synthetic late acknowledgement failure")
            time.sleep(0.05)
        raise RuntimeError("timed out waiting for spawned task visibility")

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)
    monkeypatch.setattr("weft.commands.run.stdin_is_tty", lambda: False)
    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _recording_enqueue)
    monkeypatch.setattr(
        "weft.commands._manager_bootstrap._acknowledge_manager_launch_success",
        _fail_ack_after_spawn,
    )
    monkeypatch.setattr("weft.commands.run.typer.echo", lambda *args, **kwargs: None)

    try:
        exit_code = _run_inline(
            command=(),
            function_target="tests.tasks.sample_targets:provide_payload",
            args=(),
            kwargs=(),
            env=(),
            name="provide-payload",
            interactive=False,
            stream_output=None,
            timeout=None,
            memory=None,
            cpu=None,
            tags=(),
            context_dir=root,
            wait=True,
            json_output=False,
            verbose=False,
            autostart_enabled=True,
        )

        assert exit_code == 0
        tid = submitted_tid["value"]
        snapshot = _wait_for_task_status(root, tid, expected_status="completed")
        assert snapshot.tid == tid
    finally:
        _stop_active_manager(context)


def test_delete_spawn_request_removes_queued_message(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)

    message_timestamp = _enqueue_taskspec(context, taskspec, None)

    deleted = _delete_spawn_request(context, message_timestamp)

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.read_one() is None
    finally:
        queue.close()
    assert deleted is True


def test_enqueue_taskspec_strips_reserved_internal_runtime_metadata_from_public_submission(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)
    taskspec.metadata[INTERNAL_RUNTIME_TASK_CLASS_KEY] = (
        INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
    )
    taskspec.metadata[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = (
        INTERNAL_HEARTBEAT_ENDPOINT_NAME
    )

    _enqueue_taskspec(context, taskspec, None)

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        raw_message = queue.read_one()
    finally:
        queue.close()

    assert isinstance(raw_message, str)
    payload = json.loads(raw_message)
    taskspec_payload = payload["taskspec"]
    assert INTERNAL_RUNTIME_TASK_CLASS_KEY not in taskspec_payload["metadata"]
    assert INTERNAL_RUNTIME_ENDPOINT_NAME_KEY not in taskspec_payload["metadata"]
    assert INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY not in payload
    assert INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY not in payload


def test_enqueue_taskspec_preserves_public_endpoint_name_on_public_submission(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)
    taskspec.metadata[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = "mayor"

    _enqueue_taskspec(context, taskspec, None)

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        raw_message = queue.read_one()
    finally:
        queue.close()

    assert isinstance(raw_message, str)
    payload = json.loads(raw_message)
    taskspec_payload = payload["taskspec"]
    assert taskspec_payload["metadata"][INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] == "mayor"
    assert INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY not in payload


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

    monkeypatch.setattr("weft.core.spawn_requests.Queue", _FakeQueue)

    deleted = _delete_spawn_request(context, 1775679597297004544)

    assert closed is True
    assert deleted is False


def test_reconcile_submitted_spawn_reports_queued_for_unclaimed_request(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)
    submitted_tid = str(_enqueue_taskspec(context, taskspec, None))

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.0)

    assert result == SpawnSubmissionReconciliation(
        outcome="queued",
        tid=submitted_tid,
    )


def test_reconcile_submitted_spawn_reports_reserved_for_claimed_request(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    manager_tid = "1776000000000000001"
    registry = context.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)
    try:
        registry.write(
            json.dumps(
                {
                    "tid": manager_tid,
                    "status": "active",
                    "pid": 424242,
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                    "role": "manager",
                }
            )
        )
    finally:
        registry.close()

    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)
    submitted_tid = str(_enqueue_taskspec(context, taskspec, None))
    spawn_queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        moved = spawn_queue.move_one(
            f"T{manager_tid}.reserved",
            exact_timestamp=int(submitted_tid),
            with_timestamps=False,
        )
    finally:
        spawn_queue.close()
    assert moved is not None

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.0)

    assert result == SpawnSubmissionReconciliation(
        outcome="reserved",
        tid=submitted_tid,
        reserved_queue=f"T{manager_tid}.reserved",
    )


def test_reconcile_submitted_spawn_reports_rejected_from_manager_log(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid = str(time.time_ns())
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        log_queue.write(
            json.dumps(
                {
                    "event": "task_spawn_rejected",
                    "tid": "1776000000000000002",
                    "status": "running",
                    "child_tid": submitted_tid,
                    "error": "manager rejected child task",
                }
            )
        )
    finally:
        log_queue.close()

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.0)

    assert result == SpawnSubmissionReconciliation(
        outcome="rejected",
        tid=submitted_tid,
        error="manager rejected child task",
    )


def test_reconcile_submitted_spawn_ignores_manager_fenced_requeued_event(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)
    submitted_tid = str(_enqueue_taskspec(context, taskspec, None))
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        log_queue.write(
            json.dumps(
                {
                    "event": "manager_spawn_fenced_requeued",
                    "tid": "1776000000000000002",
                    "status": "running",
                    "child_tid": submitted_tid,
                    "leader_tid": "1776000000000000001",
                    "reserved_queue": "T1776000000000000002.reserved",
                    "message_id": int(submitted_tid),
                }
            )
        )
    finally:
        log_queue.close()

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.0)

    assert result == SpawnSubmissionReconciliation(
        outcome="queued",
        tid=submitted_tid,
    )


@pytest.mark.parametrize(
    "event_name",
    ["manager_spawn_fenced_stranded", "manager_spawn_fence_suspended"],
)
def test_reconcile_submitted_spawn_ignores_manager_fence_reserved_diagnostics(
    tmp_path: Path,
    event_name: str,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    manager_tid = "1776000000000000001"
    registry = context.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)
    try:
        registry.write(
            json.dumps(
                {
                    "tid": manager_tid,
                    "status": "active",
                    "pid": 424242,
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                    "role": "manager",
                }
            )
        )
    finally:
        registry.close()

    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)
    submitted_tid = str(_enqueue_taskspec(context, taskspec, None))
    spawn_queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        moved = spawn_queue.move_one(
            f"T{manager_tid}.reserved",
            exact_timestamp=int(submitted_tid),
            with_timestamps=False,
        )
    finally:
        spawn_queue.close()
    assert moved is not None

    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        payload = {
            "event": event_name,
            "tid": "1776000000000000002",
            "status": "running",
            "child_tid": submitted_tid,
            "reserved_queue": f"T{manager_tid}.reserved",
            "message_id": int(submitted_tid),
        }
        if event_name == "manager_spawn_fence_suspended":
            payload["ownership_state"] = "unknown"
        else:
            payload["leader_tid"] = "1776000000000000001"
        log_queue.write(json.dumps(payload))
    finally:
        log_queue.close()

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.0)

    assert result == SpawnSubmissionReconciliation(
        outcome="reserved",
        tid=submitted_tid,
        reserved_queue=f"T{manager_tid}.reserved",
    )


def test_reconcile_submitted_spawn_uses_queue_monitor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid = str(time.time_ns())
    created_monitors: list[_FakeQueueChangeMonitor] = []
    results = iter(
        [
            SpawnSubmissionReconciliation(outcome="unknown", tid=submitted_tid),
            SpawnSubmissionReconciliation(outcome="queued", tid=submitted_tid),
        ]
    )

    def _fake_monitor(queues, *, config=None):
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(spawn_submission_cmd, "QueueChangeMonitor", _fake_monitor)
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_reconcile_submitted_spawn_once",
        lambda *_args, **_kwargs: next(results),
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_spawn_reconciliation_queue_specs",
        lambda _context: (
            (WEFT_TID_MAPPINGS_QUEUE, False),
            (WEFT_GLOBAL_LOG_QUEUE, False),
            (WEFT_SPAWN_REQUESTS_QUEUE, False),
            (WEFT_MANAGERS_REGISTRY_QUEUE, False),
        ),
    )

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.1)

    assert result == SpawnSubmissionReconciliation(
        outcome="queued",
        tid=submitted_tid,
    )
    assert len(created_monitors) == 1
    assert created_monitors[0].queue_names == [
        WEFT_TID_MAPPINGS_QUEUE,
        WEFT_GLOBAL_LOG_QUEUE,
        WEFT_SPAWN_REQUESTS_QUEUE,
        WEFT_MANAGERS_REGISTRY_QUEUE,
    ]
    assert created_monitors[0].wait_calls


def test_reconcile_submitted_spawn_rebuilds_monitor_when_reserved_queues_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid = str(time.time_ns())
    created_monitors: list[_FakeQueueChangeMonitor] = []
    results = iter(
        [
            SpawnSubmissionReconciliation(outcome="unknown", tid=submitted_tid),
            SpawnSubmissionReconciliation(outcome="unknown", tid=submitted_tid),
            SpawnSubmissionReconciliation(
                outcome="reserved",
                tid=submitted_tid,
                reserved_queue="T1776000000000000001.reserved",
            ),
        ]
    )
    queue_specs = iter(
        [
            (
                (WEFT_TID_MAPPINGS_QUEUE, False),
                (WEFT_GLOBAL_LOG_QUEUE, False),
                (WEFT_SPAWN_REQUESTS_QUEUE, False),
                (WEFT_MANAGERS_REGISTRY_QUEUE, False),
            ),
            (
                (WEFT_TID_MAPPINGS_QUEUE, False),
                (WEFT_GLOBAL_LOG_QUEUE, False),
                (WEFT_SPAWN_REQUESTS_QUEUE, False),
                (WEFT_MANAGERS_REGISTRY_QUEUE, False),
                ("T1776000000000000001.reserved", False),
            ),
            (
                (WEFT_TID_MAPPINGS_QUEUE, False),
                (WEFT_GLOBAL_LOG_QUEUE, False),
                (WEFT_SPAWN_REQUESTS_QUEUE, False),
                (WEFT_MANAGERS_REGISTRY_QUEUE, False),
                ("T1776000000000000001.reserved", False),
            ),
        ]
    )

    def _fake_monitor(queues, *, config=None):
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(spawn_submission_cmd, "QueueChangeMonitor", _fake_monitor)
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_reconcile_submitted_spawn_once",
        lambda *_args, **_kwargs: next(results),
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_spawn_reconciliation_queue_specs",
        lambda _context: next(queue_specs),
    )

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.1)

    assert result == SpawnSubmissionReconciliation(
        outcome="reserved",
        tid=submitted_tid,
        reserved_queue="T1776000000000000001.reserved",
    )
    assert len(created_monitors) == 2
    assert created_monitors[0].queue_names == [
        WEFT_TID_MAPPINGS_QUEUE,
        WEFT_GLOBAL_LOG_QUEUE,
        WEFT_SPAWN_REQUESTS_QUEUE,
        WEFT_MANAGERS_REGISTRY_QUEUE,
    ]
    assert created_monitors[1].queue_names == [
        WEFT_TID_MAPPINGS_QUEUE,
        WEFT_GLOBAL_LOG_QUEUE,
        WEFT_SPAWN_REQUESTS_QUEUE,
        WEFT_MANAGERS_REGISTRY_QUEUE,
        "T1776000000000000001.reserved",
    ]


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
        name=None,
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


def test_run_spec_via_manager_returns_timeout_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
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

    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context, *, submitted_tid, verbose: (None, False, None),
    )
    monkeypatch.setattr(
        "weft.commands.run._enqueue_taskspec",
        lambda context, taskspec, work_payload, seed_start_envelope=True: (
            1777000000000000000
        ),
    )
    monkeypatch.setattr(
        "weft.commands.run._wait_for_task_completion",
        lambda context, resolved_spec, *, json_output, verbose: (
            "timeout",
            None,
            "Timed out waiting for task",
        ),
    )
    monkeypatch.setattr("weft.commands.run.typer.echo", lambda *args, **kwargs: None)

    exit_code = _run_spec_via_manager(
        spec_path,
        name=None,
        verbose=False,
        wait=True,
        json_output=False,
        autostart_enabled=True,
        persistent_override=None,
    )

    assert exit_code == 124


def test_run_spec_via_manager_explicit_name_overrides_name_and_claims_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    spec_path = root / "persistent_named_task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "persistent": True,
                "weft_context": str(root),
            },
            "metadata": {},
        },
    )

    captured: dict[str, Any] = {}

    def _capture_enqueue(
        context_arg: Any,
        taskspec: TaskSpec,
        work_payload: Any,
        *,
        seed_start_envelope: bool = True,
        allow_internal_runtime: bool = False,
    ) -> int:
        del context_arg, work_payload, seed_start_envelope, allow_internal_runtime
        captured["name"] = taskspec.name
        captured["metadata"] = dict(taskspec.metadata)
        return 1777000000000000001

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context, *, submitted_tid, verbose: (None, False, None),
    )
    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _capture_enqueue)
    monkeypatch.setattr("weft.commands.run.typer.echo", lambda *args, **kwargs: None)

    exit_code = _run_spec_via_manager(
        spec_path,
        name="mayor",
        verbose=False,
        wait=False,
        json_output=False,
        autostart_enabled=True,
        persistent_override=None,
    )

    assert exit_code == 0
    assert captured["name"] == "mayor"
    assert captured["metadata"][INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] == "mayor"


def test_run_spec_via_manager_explicit_name_keeps_nonpersistent_tasks_label_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    spec_path = root / "named_task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(root),
            },
            "metadata": {},
        },
    )

    captured: dict[str, Any] = {}

    def _capture_enqueue(
        context_arg: Any,
        taskspec: TaskSpec,
        work_payload: Any,
        *,
        seed_start_envelope: bool = True,
        allow_internal_runtime: bool = False,
    ) -> int:
        del context_arg, work_payload, seed_start_envelope, allow_internal_runtime
        captured["name"] = taskspec.name
        captured["metadata"] = dict(taskspec.metadata)
        return 1777000000000000002

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context, *, submitted_tid, verbose: (None, False, None),
    )
    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _capture_enqueue)
    monkeypatch.setattr("weft.commands.run.typer.echo", lambda *args, **kwargs: None)

    exit_code = _run_spec_via_manager(
        spec_path,
        name="mayor",
        verbose=False,
        wait=False,
        json_output=False,
        autostart_enabled=True,
        persistent_override=None,
    )

    assert exit_code == 0
    assert captured["name"] == "mayor"
    assert INTERNAL_RUNTIME_ENDPOINT_NAME_KEY not in captured["metadata"]


@pytest.mark.parametrize("persistent", [False, True])
def test_run_spec_via_manager_rejects_reserved_internal_name_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    persistent: bool,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    spec_path = root / "named_task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(root),
                **({"persistent": True} if persistent else {}),
            },
            "metadata": {},
        },
    )

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)

    with pytest.raises(
        typer.BadParameter, match="reserved for internal runtime services"
    ):
        _run_spec_via_manager(
            spec_path,
            name="_weft.heartbeat",
            verbose=False,
            wait=False,
            json_output=False,
            autostart_enabled=True,
            persistent_override=None,
        )


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
            "stages": [{"name": "stage-one", "task": "stage-task"}],
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
            name=None,
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


def test_run_pipeline_explicit_name_overrides_pipeline_task_name(
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
            "stages": [{"name": "stage-one", "task": "stage-task"}],
        },
    )

    captured: dict[str, Any] = {}

    def _capture_enqueue(
        context_arg: Any,
        taskspec: TaskSpec,
        work_payload: Any,
        *,
        seed_start_envelope: bool = True,
        allow_internal_runtime: bool = False,
    ) -> int:
        del context_arg, work_payload, seed_start_envelope, allow_internal_runtime
        captured["name"] = taskspec.name
        captured["metadata"] = dict(taskspec.metadata)
        return 1777000000000000003

    monkeypatch.setattr("weft.commands.run._read_piped_stdin", lambda context: None)
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context, *, submitted_tid, verbose: (None, False, None),
    )
    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _capture_enqueue)
    monkeypatch.setattr("weft.commands.run.typer.echo", lambda *args, **kwargs: None)

    exit_code = _run_pipeline(
        pipeline_path,
        name="nightly",
        pipeline_input=None,
        context_dir=root,
        wait=False,
        json_output=False,
        verbose=False,
        autostart_enabled=True,
    )

    assert exit_code == 0
    assert captured["name"] == "nightly"
    assert INTERNAL_RUNTIME_ENDPOINT_NAME_KEY not in captured["metadata"]


def test_run_pipeline_without_input_does_not_inject_work_envelope_start(
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
                "function_target": "tests.tasks.sample_targets:provide_payload",
            },
            "metadata": {},
        },
    )
    _write_json(
        pipeline_path,
        {
            "name": "demo-pipeline",
            "stages": [{"name": "stage-one", "task": "stage-task"}],
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
    monkeypatch.setattr(
        "weft.commands.run._delete_spawn_request", lambda *args, **kwargs: None
    )

    with pytest.raises(RuntimeError, match="rollback could not be confirmed"):
        _run_pipeline(
            pipeline_path,
            name=None,
            pipeline_input=None,
            context_dir=root,
            wait=False,
            json_output=False,
            verbose=False,
            autostart_enabled=True,
        )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        queued = queue.read_one()
    finally:
        queue.close()
    assert queued is not None
    payload = json.loads(queued)
    assert payload["inbox_message"] is None


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
        registry = ctx.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)
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
    registry = ctx.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)
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
    registry = ctx.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)
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

    registry_reader = ctx.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)
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
            _registry_view(target={"tid": tid, "status": "active", "pid": 4321}),
            _registry_view(target={"tid": tid, "status": "stopped", "pid": 4321}),
            _registry_view(target={"tid": tid, "status": "stopped", "pid": 4321}),
            _registry_view(),
        ]
    )
    pid_states = iter([True, True, False, False])

    monkeypatch.setattr(manager_lifecycle, "_send_stop", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        manager_lifecycle,
        "QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        manager_lifecycle,
        "_registry_view",
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


def test_stop_manager_stop_if_absent_short_circuits_after_stop_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "17756224000000000055"
    sent: list[str] = []

    monkeypatch.setattr(
        manager_lifecycle,
        "_send_stop",
        lambda _context, target_tid, *, record=None: sent.append(target_tid),
    )
    monkeypatch.setattr(
        manager_lifecycle,
        "_await_manager_stop_confirmation",
        lambda *args, **kwargs: pytest.fail(
            "absent stop_if_absent path should not wait on registry confirmation"
        ),
    )

    stopped, message = manager_lifecycle._stop_manager(
        ctx,
        None,
        tid=tid,
        timeout=1.0,
        stop_if_absent=True,
    )

    assert stopped is True
    assert message is None
    assert sent == [tid]


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
        manager_lifecycle,
        "QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        manager_lifecycle,
        "_registry_view",
        lambda *args, **kwargs: _registry_view(),
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
