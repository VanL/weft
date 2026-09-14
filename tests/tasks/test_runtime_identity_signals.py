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

from tests.helpers.typing import BrokerEnv
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.core.task_state import task_state_queue_name
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
    broker_env: BrokerEnv,
    worker_tree: tuple[subprocess.Popen[str], psutil.Process],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
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
        recorded_time: float | None = process.create_time()
        if identity == "mismatched":
            recorded_time = process.create_time() + 1000.0
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


@pytest.mark.parametrize("graceful", [True, False], ids=["stop", "kill"])
@pytest.mark.parametrize(
    "evidence", ["supplied_exact", "both_unknown", "conflicting_exact"]
)
def test_managed_identity_merge_preserves_explicit_evidence(
    broker_env: BrokerEnv,
    worker_tree: tuple[subprocess.Popen[str], psutil.Process],
    monkeypatch: pytest.MonkeyPatch,
    graceful: bool,
    evidence: str,
) -> None:
    """Publication and control share exact evidence without a second PID lookup."""
    worker, descendant = worker_tree
    exact = psutil.Process(worker.pid).create_time()
    recorded = exact if evidence == "conflicting_exact" else None
    supplied = None if evidence == "both_unknown" else exact
    if evidence == "conflicting_exact":
        supplied = exact + 1000.0
    expected = None if evidence == "both_unknown" else exact
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_function_taskspec(
            str(time.time_ns()), "tests.tasks.sample_targets:echo_payload"
        ),
    )
    mappings = make_queue(task_state_queue_name(task.tid))
    try:
        monkeypatch.setattr(base_module, "process_create_time", lambda pid: recorded)
        task.register_managed_pid(worker.pid)
        lookup = Mock(
            side_effect=AssertionError("recorded identities must not be re-observed")
        )
        monkeypatch.setattr(base_module, "process_create_time", lookup)
        task.register_runtime_handle(
            RunnerHandle(
                runner="host",
                kind="process",
                id=str(worker.pid),
                control={"authority": "host-pid"},
                observations={
                    "host_pids": [worker.pid],
                    "host_processes": [{"pid": worker.pid, "create_time": supplied}],
                },
            )
        )
        rows = [
            row
            for row, _ in iter_queue_json_entries(mappings)
            if row.get("full") == task.tid
        ]
        published = RunnerHandle.from_dict(rows[-1]["runtime_handle"])
        assert dict(published.scoped_host_processes()) == {worker.pid: expected}
        assert task._managed_pids[worker.pid] == expected
        task._stop_managed_runtime(timeout=0.2, graceful=graceful)
        lookup.assert_not_called()
        if expected is None:
            assert worker.poll() is None
            assert descendant.is_running()
        else:
            worker.wait(timeout=5)
            assert (
                not descendant.is_running()
                or descendant.status() == psutil.STATUS_ZOMBIE
            )
    finally:
        task.cleanup()
        mappings.close()


@pytest.mark.parametrize(
    "recorded,supplied,expected",
    [(None, 10.0, 10.0), (None, None, None), (10.0, 20.0, 10.0)],
)
def test_observation_merge_keeps_exact_evidence(
    monkeypatch: pytest.MonkeyPatch,
    recorded: float | None,
    supplied: float | None,
    expected: float | None,
) -> None:
    lookup = Mock(side_effect=AssertionError("must not re-observe recorded identity"))
    monkeypatch.setattr(base_module, "process_create_time", lookup)
    assert base_module._merge_host_process_observations(
        ((123, supplied),), {123}, {123: recorded}
    ) == [{"pid": 123, "create_time": expected}]
    lookup.assert_not_called()
