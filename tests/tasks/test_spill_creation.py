"""OS-exclusive spill creation preserves caller-owned files [MF-2.1], [CTX.5]."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import weft.core.tasks.liveness_monitor as liveness_module
from simplebroker import BrokerTarget
from tests.helpers.typing import BrokerEnv
from tests.tasks.test_task_monitor import _maintenance_for
from tests.taskspec import fixtures
from weft._constants import load_config
from weft.core.monitor.task_monitor import TaskMonitor, make_task_monitor_taskspec
from weft.core.tasks import Consumer
from weft.core.tasks.liveness_monitor import LivenessMonitor
from weft.core.taskspec import TaskSpec
from weft.liveness.host import inspect_host_process

pytestmark = [pytest.mark.shared]


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


def _spill_in_process(
    target: BrokerTarget, spec: dict[str, Any], result: str, content: bytes
) -> None:
    task = Consumer(target, TaskSpec.model_validate(spec))
    try:
        reference = task._spill_large_output(content)
        Path(result).write_text(json.dumps(reference))
        task.taskspec.set_status("spawning")
        task.taskspec.mark_running()
        task.taskspec.mark_completed(return_code=0)
        task._report_state_change(event="work_completed")
    finally:
        task.cleanup()


def test_concurrent_processes_exclusively_create_numeric_spills(
    broker_env: BrokerEnv, unique_tid: str, tmp_path: Path
) -> None:
    target, _ = broker_env
    spec = fixtures.create_minimal_taskspec(tid=unique_tid)
    spec = spec.model_copy(
        update={"spec": spec.spec.model_copy(update={"weft_context": str(tmp_path)})}
    )
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_spill_in_process,
            args=(
                target,
                spec.model_dump(mode="json"),
                str(tmp_path / f"ref-{index}"),
                content,
            ),
        )
        for index, content in enumerate((b"one\nline two\n", b"two\nline two\n"))
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        paths = [
            Path(json.loads((tmp_path / f"ref-{index}").read_text())["path"])
            for index in range(2)
        ]
        assert {path.name for path in paths} == {"1.dat", "2.dat"}
        assert [path.read_bytes() for path in paths] == [
            b"one\nline two\n",
            b"two\nline two\n",
        ]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=20)
            process.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process umask")
def test_spill_creation_honors_shared_access_umask(
    broker_env: BrokerEnv, unique_tid: str, tmp_path: Path
) -> None:
    target, _ = broker_env
    spec = fixtures.create_minimal_taskspec(tid=unique_tid)
    spec = spec.model_copy(
        update={"spec": spec.spec.model_copy(update={"weft_context": str(tmp_path)})}
    )
    task = Consumer(target, spec)
    original = os.umask(0o002)
    try:
        reference = task._spill_large_output(b"shared result")
        assert stat.S_IMODE(Path(reference["path"]).stat().st_mode) == 0o664
    finally:
        os.umask(original)
        task.cleanup()


@pytest.mark.parametrize("contextual", [False, True])
def test_spill_survives_dead_queue_retirement_and_monitor_restart(
    broker_env: BrokerEnv,
    unique_tid: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contextual: bool,
) -> None:
    """A real exited producer's file outlives its queues and both monitors."""
    target, make_queue = broker_env
    spec = fixtures.create_minimal_taskspec(tid=unique_tid)
    if contextual:
        spec = spec.model_copy(
            update={
                "spec": spec.spec.model_copy(update={"weft_context": str(tmp_path)})
            }
        )
    process = multiprocessing.get_context("spawn").Process(
        target=_spill_in_process,
        args=(
            target,
            spec.model_dump(mode="json"),
            str(tmp_path / "reference"),
            b"retained\nbytes",
        ),
    )
    process.start()
    process.join(timeout=20)
    try:
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=20)
        process.close()
    reference = json.loads((tmp_path / "reference").read_text())
    path = Path(reference["path"])
    outbox = make_queue(f"T{unique_tid}.outbox")
    outbox.write(json.dumps(reference))
    future = time.time_ns() + 10 * 86400 * 1_000_000_000
    monkeypatch.setattr(
        liveness_module,
        "time",
        SimpleNamespace(time_ns=lambda: future, monotonic=time.monotonic),
    )
    monitor_spec = fixtures.create_minimal_taskspec(tid=str(future + 1))
    liveness = LivenessMonitor(target, monitor_spec)
    try:
        liveness._reconcile_mapping_rows(full=True)
        row = liveness._latest_rows[unique_tid]
        handle = row.payload["runtime_handle"]
        identity = handle["observations"]["host_processes"][0]
        assert (
            inspect_host_process(identity["pid"], identity["create_time"]).evidence
            == "stale"
        )
        assert liveness._retire_mapping_row(row)
    finally:
        liveness.cleanup()
    config = load_config(
        {
            "WEFT_TASK_MONITOR_MODE": "jsonl_then_delete",
            "WEFT_TASK_MONITOR_LOG_SINK": "none",
        }
    )
    for index in range(2):
        monitor = TaskMonitor(
            target, make_task_monitor_taskspec(str(future + 2 + index)), config=config
        )
        try:
            with _maintenance_for(monitor) as worker:
                worker._run_monitor_store_cycle(
                    now_ns=future, task_log_owner="collated_store"
                )
                store = worker._ensure_monitor_store()
                assert store is not None
                with worker._connection() as broker:
                    names = set(broker.list_queues())
                result = worker._delete_dead_task_control_queues(
                    unique_tid,
                    store=store,
                    existing_queue_names=names,
                    active_tids=set(),
                    now_ns=future,
                )
                assert result.errors == ()
        finally:
            monitor.cleanup()
        assert outbox.peek_one() is None
        assert path.read_bytes() == b"retained\nbytes"

    restarted = LivenessMonitor(
        target, fixtures.create_minimal_taskspec(tid=str(future + 10))
    )
    try:
        restarted._reconcile_mapping_rows(full=True)
        assert unique_tid not in restarted._latest_rows
        assert path.read_bytes() == b"retained\nbytes"
    finally:
        restarted.cleanup()
        # Tests are the caller and explicitly retire their temporary artifact.
        path.unlink(missing_ok=True)
        path.parent.rmdir()
