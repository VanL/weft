"""Signal authority and registration identity (Spec: [CC-3.2], [LIVENESS.R5])."""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from unittest.mock import Mock

import psutil
import pytest

from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.core.tasks import Consumer
from weft.core.tasks import base as base_module
from weft.core.tasks.base import BaseTask
from weft.ext import RunnerHandle
from weft.helpers import iter_queue_json_entries

pytestmark = pytest.mark.shared


@pytest.fixture
def worker_tree() -> Iterator[tuple[subprocess.Popen[str], psutil.Process]]:
    # The child announces readiness before its parent publishes its PID. This
    # proves the descendant exists before any control path runs.
    child_code = "import time; print('ready', flush=True); time.sleep(120)"
    parent_code = (
        "import subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}], "
        "stdout=subprocess.PIPE, text=True); "
        "assert child.stdout.readline().strip() == 'ready'; "
        "print(child.pid, flush=True); time.sleep(120)"
    )
    worker = subprocess.Popen(
        [sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True
    )
    descendant = None
    try:
        assert worker.stdout is not None
        descendant = psutil.Process(int(worker.stdout.readline().strip()))
        yield worker, descendant
    finally:
        # Owned process objects remain the cleanup authority even for identities
        # deliberately made unknown or mismatched by this test.
        if descendant is not None:
            try:
                descendant.kill()
            except psutil.NoSuchProcess:
                pass
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=5)
        if worker.stdout is not None:
            worker.stdout.close()


@pytest.mark.parametrize("handler", [BaseTask, Consumer], ids=["base", "consumer"])
@pytest.mark.parametrize("graceful", [True, False], ids=["stop", "kill"])
@pytest.mark.parametrize(
    "identity", ["matching", "mismatched", "unknown", "runner", "external"]
)
def test_signal_respects_worker_identity_and_authority(
    broker_env,
    worker_tree,
    monkeypatch,
    caplog,
    handler: type[BaseTask],
    graceful: bool,
    identity: str,
) -> None:
    """Only a matching host identity authorizes direct worker-tree control."""
    if not graceful and not hasattr(signal, "SIGUSR1"):
        pytest.skip("SIGUSR1 is unavailable on this platform")
    worker, descendant = worker_tree
    task = None
    try:
        process = psutil.Process(worker.pid)
        recorded_time = process.create_time()
        if identity == "mismatched":
            recorded_time += 1000.0
        elif identity == "unknown":
            recorded_time = None
        authority = {
            "runner": "runner",
            "external": "external-supervisor",
        }.get(identity, "host-pid")
        db_path, make_queue = broker_env
        task = Consumer(
            db_path,
            make_function_taskspec(
                str(time.time_ns()), "tests.tasks.sample_targets:echo_payload"
            ),
        )
        task.taskspec.mark_running()
        task.register_runtime_handle(
            RunnerHandle(
                runner="host" if authority == "host-pid" else "fixture-runner",
                kind="process",
                id=str(worker.pid),
                control={"authority": authority},
                observations={
                    "host_pids": [worker.pid],
                    "host_processes": [
                        {"pid": worker.pid, "create_time": recorded_time}
                    ],
                },
            )
        )
        task.register_managed_pid(worker.pid)
        plugin = Mock()
        monkeypatch.setattr(base_module, "require_runner_plugin", lambda name: plugin)
        direct_control = Mock(wraps=base_module.terminate_verified_process_tree)
        monkeypatch.setattr(
            base_module, "terminate_verified_process_tree", direct_control
        )
        caplog.set_level(logging.WARNING)
        signum = signal.SIGTERM if graceful else signal.SIGUSR1
        handler.handle_termination_signal(task, signum)
        expected_status = "cancelled" if graceful else "killed"
        assert task.taskspec.state.status == expected_status
        assert task.should_stop
        events = [
            row
            for row, _ in iter_queue_json_entries(make_queue(WEFT_GLOBAL_LOG_QUEUE))
            if row.get("tid") == task.tid
        ]
        assert any(
            row.get("event") == ("task_signal_stop" if graceful else "task_signal_kill")
            for row in events
        )
        if identity == "matching":
            worker.wait(timeout=5)
            _, alive = psutil.wait_procs([descendant], timeout=5)
            assert all(proc.status() == psutil.STATUS_ZOMBIE for proc in alive)
            assert direct_control.call_count == 1
        else:
            assert worker.poll() is None
            assert descendant.is_running()
            assert descendant.status() != psutil.STATUS_ZOMBIE
        if identity in {"mismatched", "unknown"}:
            assert any(
                str(worker.pid) in record.getMessage()
                and record.levelno >= logging.WARNING
                for record in caplog.records
            )
        if identity == "runner":
            expected = plugin.stop if graceful else plugin.kill
            other = plugin.kill if graceful else plugin.stop
            expected.assert_called_once()
            other.assert_not_called()
            direct_control.assert_not_called()
        else:
            plugin.stop.assert_not_called()
            plugin.kill.assert_not_called()
        if identity == "external":
            direct_control.assert_not_called()
    finally:
        if task is not None:
            task.cleanup()
