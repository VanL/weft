"""Tests for task stop/kill helpers against launched task processes."""

from __future__ import annotations

import json
import time
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.commands import tasks as task_cmd
from weft.context import build_context
from weft.core import (
    IOSection,
    SpecSection,
    StateSection,
    TaskSpec,
    launch_task_process,
)
from weft.core.tasks import Consumer
from weft.helpers import kill_process_tree

pytestmark = [pytest.mark.shared]


def _make_taskspec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="task-func",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:simulate_work",
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )


def _wait_for_worker_pid(parent_pid: int, timeout: float = 5.0) -> int | None:
    psutil = pytest.importorskip("psutil")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            parent = psutil.Process(parent_pid)
        except psutil.Error:
            return None
        children = parent.children(recursive=True)
        if children:
            return children[0].pid
        time.sleep(0.05)
    return None


def _wait_for_process_exit(
    pid: int,
    *,
    process: BaseProcess | None = None,
    timeout: float = 5.0,
) -> bool:
    psutil = pytest.importorskip("psutil")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process is not None:
            process.join(timeout=0.05)
            if not process.is_alive():
                return True
        try:
            ps_process = psutil.Process(pid)
        except psutil.Error:
            return True
        if not ps_process.is_running() or ps_process.status() == psutil.STATUS_ZOMBIE:
            return True
        time.sleep(0.05)
    return False


def _launch_running_task(tmp_path) -> tuple[TaskSpec, BaseProcess, int]:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    spec = _make_taskspec(tid)
    process = launch_task_process(
        Consumer,
        ctx.broker_target,
        spec,
        config=ctx.config,
    )
    inbox = ctx.queue(spec.io.inputs["inbox"], persistent=True)
    inbox.write(json.dumps({"kwargs": {"duration": 5.0}}))
    worker_pid = _wait_for_worker_pid(process.pid)
    assert worker_pid is not None
    return spec, process, worker_pid


def test_stop_tasks_terminates_active_process_tree(tmp_path) -> None:
    spec, process, worker_pid = _launch_running_task(tmp_path)
    try:
        stopped = task_cmd.stop_tasks([spec.tid], context_path=tmp_path)
        assert stopped == 1
        assert _wait_for_process_exit(process.pid, process=process)
        assert _wait_for_process_exit(worker_pid)
    finally:
        kill_process_tree(process.pid)
        kill_process_tree(worker_pid)


def test_kill_tasks_terminates_active_process_tree(tmp_path) -> None:
    spec, process, worker_pid = _launch_running_task(tmp_path)
    try:
        killed = task_cmd.kill_tasks([spec.tid], context_path=tmp_path)
        assert killed >= 1
        assert _wait_for_process_exit(process.pid, process=process)
        assert _wait_for_process_exit(worker_pid)
    finally:
        kill_process_tree(process.pid)
        kill_process_tree(worker_pid)


def test_stop_tasks_uses_runner_handle_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    calls: list[tuple[str, dict[str, Any], float]] = []

    class FakeRunnerPlugin:
        def stop(self, handle, *, timeout: float = 2.0) -> bool:
            calls.append(("stop", handle.to_dict(), timeout))
            return True

    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "pid": 11111,
                "task_pid": 11111,
                "caller_pid": 22222,
                "managed_pids": [33333],
                "runner": "fake",
                "runtime_handle": {
                    "runner_name": "fake",
                    "runtime_id": "runtime-123",
                    "host_pids": [33333],
                    "metadata": {"scope": "test"},
                },
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(
        task_cmd,
        "terminate_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not fall back to direct PID stop")
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert calls == [
        (
            "stop",
            {
                "runner_name": "fake",
                "runtime_id": "runtime-123",
                "host_pids": [33333],
                "metadata": {"scope": "test"},
            },
            0.2,
        )
    ]
    assert ctrl_queue.read_one() == "STOP"


def test_stop_tasks_prefers_task_process_over_runner_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    terminate_calls: list[tuple[int, float, bool]] = []
    plugin_calls: list[tuple[str, dict[str, Any], float]] = []

    class FakeRunnerPlugin:
        def stop(self, handle, *, timeout: float = 2.0) -> bool:
            plugin_calls.append(("stop", handle.to_dict(), timeout))
            return True

    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "pid": 11111,
                "task_pid": 11111,
                "caller_pid": 22222,
                "managed_pids": [33333],
                "runner": "fake",
                "runtime_handle": {
                    "runner_name": "fake",
                    "runtime_id": "runtime-123",
                    "host_pids": [33333],
                    "metadata": {"scope": "test"},
                },
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(task_cmd, "_pid_exists", lambda pid: pid == 11111)
    monkeypatch.setattr(
        task_cmd,
        "terminate_process_tree",
        lambda pid, timeout=0.2, kill_after=True: terminate_calls.append(
            (pid, timeout, kill_after)
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert terminate_calls == [(11111, 0.2, False), (11111, 0.2, False)]
    assert plugin_calls == []
    assert ctrl_queue.read_one() == "STOP"


def test_kill_tasks_uses_runner_handle_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    calls: list[tuple[str, dict[str, Any], float]] = []

    class FakeRunnerPlugin:
        def kill(self, handle, *, timeout: float = 2.0) -> bool:
            calls.append(("kill", handle.to_dict(), timeout))
            return True

    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "pid": 11111,
                "task_pid": 11111,
                "caller_pid": 22222,
                "managed_pids": [33333],
                "runner": "fake",
                "runtime_handle": {
                    "runner_name": "fake",
                    "runtime_id": "runtime-123",
                    "host_pids": [33333],
                    "metadata": {"scope": "test"},
                },
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(
        task_cmd,
        "kill_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not fall back to direct PID kill")
        ),
    )

    killed = task_cmd.kill_tasks([tid], context_path=root)

    assert killed == 1
    assert calls == [
        (
            "kill",
            {
                "runner_name": "fake",
                "runtime_id": "runtime-123",
                "host_pids": [33333],
                "metadata": {"scope": "test"},
            },
            0.2,
        )
    ]


def test_stop_tasks_does_not_force_terminal_consumer_for_external_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "pid": 11111,
                "task_pid": 11111,
                "caller_pid": 22222,
                "managed_pids": [],
                "runner": "docker",
                "runtime_handle": {
                    "runner_name": "docker",
                    "runtime_id": "runtime-123",
                    "host_pids": [],
                    "metadata": {"image": "python:3.13-alpine"},
                },
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *args, **kwargs: task_cmd.status_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid[-10:],
            name="docker-task",
            status="cancelled",
            event="control_stop",
            started_at=None,
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="docker",
            runtime_handle={
                "runner_name": "docker",
                "runtime_id": "runtime-123",
                "host_pids": [],
                "metadata": {"image": "python:3.13-alpine"},
            },
            runtime={
                "runner_name": "docker",
                "runtime_id": "runtime-123",
                "state": "missing",
                "metadata": {"image": "python:3.13-alpine"},
            },
            metadata={},
        ),
    )
    monkeypatch.setattr(
        task_cmd,
        "terminate_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("external runners must not force-stop the consumer PID")
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert ctrl_queue.read_one() == "STOP"


def test_stop_tasks_does_not_force_stop_consumer_without_runner_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "pid": 11111,
                "task_pid": 11111,
                "caller_pid": 22222,
                "managed_pids": [],
                "runner": "host",
                "runtime_handle": None,
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *args, **kwargs: task_cmd.status_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid[-10:],
            name="host-task",
            status="running",
            event="work_started",
            started_at=None,
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="host",
            runtime_handle=None,
            runtime=None,
            metadata={},
        ),
    )
    monkeypatch.setattr(
        task_cmd,
        "terminate_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("graceful stop must not terminate the consumer PID")
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert ctrl_queue.read_one() == "STOP"


def test_kill_tasks_does_not_force_terminal_consumer_for_external_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "pid": 11111,
                "task_pid": 11111,
                "caller_pid": 22222,
                "managed_pids": [],
                "runner": "macos-sandbox",
                "runtime_handle": {
                    "runner_name": "macos-sandbox",
                    "runtime_id": "runtime-123",
                    "host_pids": [33333],
                    "metadata": {"profile": "allow-default.sb"},
                },
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *args, **kwargs: task_cmd.status_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid[-10:],
            name="sandbox-task",
            status="killed",
            event="control_kill",
            started_at=None,
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="macos-sandbox",
            runtime_handle={
                "runner_name": "macos-sandbox",
                "runtime_id": "runtime-123",
                "host_pids": [33333],
                "metadata": {"profile": "allow-default.sb"},
            },
            runtime={
                "runner_name": "macos-sandbox",
                "runtime_id": "runtime-123",
                "state": "missing",
                "metadata": {"profile": "allow-default.sb"},
            },
            metadata={},
        ),
    )
    monkeypatch.setattr(
        task_cmd,
        "kill_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("external runners must not force-kill the consumer PID")
        ),
    )

    killed = task_cmd.kill_tasks([tid], context_path=root)

    assert killed == 1
