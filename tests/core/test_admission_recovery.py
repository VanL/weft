"""Admission recovery and process-death authority (Spec: [MA-1.8], [MF-5])."""

from __future__ import annotations

import json
import multiprocessing
import queue
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from multiprocessing.process import BaseProcess
from types import FrameType
from typing import Any

import psutil
import pytest

import weft.core.manager as manager_module
from tests.helpers.typing import TaskFactory
from tests.helpers.weft_harness import WeftTestHarness
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import (
    MANAGER_ADMISSION_REFRESH_BATCH_SIZE,
    WEFT_CHILD_SPAWN_REQUESTS_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    resolve_runtime_config,
)
from weft.core.manager import (
    Manager,
    _ManagerChildLaunchRequest,
    _ManagerChildLaunchResult,
)
from weft.core.manager_runtime import build_manager_spec
from weft.core.runtime_identity import (
    AdmissionRecord,
    canonical_runtime_context,
    connection_owner_label,
)
from weft.core.spawn_requests import submit_spawn_request
from weft.core.task_state import read_task_state_snapshot, task_state_queue_name
from weft.core.tasks import Consumer
from weft.core.tasks.service import ServiceWorkerContext
from weft.helpers import process_create_time

pytestmark = [pytest.mark.shared, pytest.mark.timeout(45)]


@pytest.fixture
def recovery_manager(
    weft_harness: WeftTestHarness, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Manager]:
    """Keep actual Manager/broker custody; substitute the external PG catalog."""
    context = weft_harness.context
    with context.queue("admission-fixture-ids", persistent=True) as ids:
        tid = str(ids.generate_timestamp())
    config = resolve_runtime_config(
        {
            **dict(context.config),
            "ADMISSION_MAX_CONNECTIONS": 64,
            "ADMISSION_TASK_CONNECTION_BUDGET": 8,
            "TASK_MONITOR_ENABLED": False,
            "LIVENESS_MONITOR_ENABLED": False,
            "AUTOSTART_TASKS": False,
        }
    )
    manager = Manager(
        context.broker_target,
        build_manager_spec(context, tid, idle_timeout_override=0.0),
        config=config,
    )
    monkeypatch.setattr(manager, "_admission_backend_name", lambda: "postgres")
    monkeypatch.setattr(
        manager, "_read_postgres_connection_usage", lambda: {"total": 3, "by_owner": {}}
    )
    try:
        yield manager
    finally:
        manager.stop(join=False)
        manager.cleanup()


def _record(manager: Manager, tid: str) -> AdmissionRecord:
    return AdmissionRecord(
        11, connection_owner_label(tid, canonical_runtime_context(manager._db_path))
    )


def _write_snapshot(harness: WeftTestHarness, payload: dict[str, Any]) -> None:
    with harness.context.queue(
        task_state_queue_name(payload["full"]), persistent=True
    ) as state:
        state.write(json.dumps(payload))


def _pending_launch_snapshot(
    manager: Manager, harness: WeftTestHarness, tid: str
) -> AdmissionRecord:
    """Represent an accepted Manager launch before any child publication."""
    record = _record(manager, tid)
    payload = manager._build_tid_state_payload()
    payload["admission_launches"] = {
        tid: {
            "admission": record.to_dict(),
            "hostname": socket.gethostname(),
            "host_processes": [],
            "task_evidence": False,
        }
    }
    _write_snapshot(harness, payload)
    return record


@pytest.fixture
def older_manager(
    recovery_manager: Manager, weft_harness: WeftTestHarness
) -> Iterator[Manager]:
    """Provide an actual earlier Manager owner without starting its dispatch loop."""
    context = weft_harness.context
    older = Manager(
        context.broker_target,
        build_manager_spec(
            context, str(int(recovery_manager.tid) - 1), idle_timeout_override=0.0
        ),
        config=recovery_manager._weft_config,
    )
    try:
        yield older
    finally:
        older.stop(join=False)
        older.cleanup()


def _historical_spawn(
    manager: Manager, older: Manager, harness: WeftTestHarness
) -> str:
    """Retain a real queued request and its actual Manager-authored log shape."""
    context = harness.context
    tid = str(
        submit_spawn_request(
            context.broker_target,
            taskspec={
                "name": "historical-child",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            },
            work_payload="payload",
            config=context.config,
            inherited_weft_context=str(context.root),
        )
    )
    child = make_function_taskspec(
        tid, "tests.tasks.sample_targets:echo_payload", weft_context=str(context.root)
    )
    older._report_state_change(
        event="task_spawned",
        child_tid=tid,
        child_taskspec=child.model_dump(mode="json"),
        admission=_record(manager, tid).to_dict(),
        submitter_tid=None,
    )
    return tid


@pytest.mark.parametrize("role_tag", [None, "manager"])
def test_consumer_reservations_cannot_restore_historical_launch_debt(
    recovery_manager: Manager,
    older_manager: Manager,
    weft_harness: WeftTestHarness,
    task_factory: TaskFactory,
    role_tag: str | None,
) -> None:
    """An ordinary retained task input is not an unacknowledged Manager launch.

    Initial consumer inputs may have the task TID as their message ID. A caller
    role tag must not let such a reservation resurrect a previously retired
    connection obligation from historical task_spawned evidence.
    """
    tid = _historical_spawn(recovery_manager, older_manager, weft_harness)
    spec = make_function_taskspec(tid, "tests.tasks.sample_targets:echo_payload")
    if role_tag is not None:
        spec.metadata["role"] = role_tag
    task = task_factory(spec)
    snapshot = task._build_tid_state_payload()
    _write_snapshot(weft_harness, snapshot)
    with weft_harness.context.queue(
        WEFT_SPAWN_REQUESTS_QUEUE, persistent=True
    ) as source:
        assert source.move(f"T{tid}.reserved", message_id=tid) is not None
    assert recovery_manager._observe_admission_usage() == 3
    with weft_harness.context.queue(f"T{tid}.reserved", persistent=True) as reserved:
        assert reserved.peek_one(exact_timestamp=tid) is not None


@pytest.mark.parametrize(
    "reserved_role", ["reserved", "children_reserved", "internal_reserved"]
)
def test_older_manager_reservations_preserve_unknown_launch_debt(
    recovery_manager: Manager,
    older_manager: Manager,
    weft_harness: WeftTestHarness,
    reserved_role: str,
) -> None:
    """All Manager launch lanes retain accepted work after ownership rows age out."""
    context = weft_harness.context
    tid = _historical_spawn(recovery_manager, older_manager, weft_harness)
    reserved_name = older_manager._queue_names[reserved_role]
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as source:
        assert source.move(reserved_name, message_id=tid) is not None
    # Runtime mappings and service-owner rows may retire before retained launch
    # evidence. The actual task_spawned reporter TID still identifies custody.
    with context.queue(
        task_state_queue_name(older_manager.tid), persistent=True
    ) as state:
        state.delete()
    with context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=True) as registry:
        registry.delete()
    assert recovery_manager._observe_admission_usage() == 14
    with context.queue(reserved_name, persistent=True) as reserved:
        assert reserved.peek_one(exact_timestamp=tid) is not None


@pytest.mark.parametrize(
    "source_name",
    [
        WEFT_SPAWN_REQUESTS_QUEUE,
        WEFT_CHILD_SPAWN_REQUESTS_QUEUE,
        WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    ],
)
def test_unreserved_backlog_does_not_restore_historical_launch_debt(
    recovery_manager: Manager,
    older_manager: Manager,
    weft_harness: WeftTestHarness,
    source_name: str,
) -> None:
    """Historical launch evidence cannot charge a request which is only queued."""
    context = weft_harness.context
    tid = _historical_spawn(recovery_manager, older_manager, weft_harness)
    if source_name != WEFT_SPAWN_REQUESTS_QUEUE:
        with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as source:
            assert source.move(source_name, message_id=tid) is not None
    assert recovery_manager._observe_admission_usage() == 3
    with context.queue(source_name, persistent=True) as source:
        assert source.peek_one(exact_timestamp=tid) is not None


def test_reporting_manager_process_is_not_child_admission_debt(
    recovery_manager: Manager,
    older_manager: Manager,
    weft_harness: WeftTestHarness,
    task_factory: TaskFactory,
) -> None:
    """A live launch reporter cannot pin the budget of its proven-dead child.

    A registered reporting runtime_handle appears in task_spawned as Manager
    lifecycle context. Child-specific admission evidence must not adopt that
    handle as another process owned by the child.
    """
    older_manager.register_runtime_handle(older_manager._manager_runtime_handle())
    tid = _historical_spawn(recovery_manager, older_manager, weft_harness)
    with weft_harness.context.queue(
        WEFT_SPAWN_REQUESTS_QUEUE, persistent=True
    ) as source:
        assert (
            source.move(older_manager._queue_names["reserved"], message_id=tid)
            is not None
        )
    task = task_factory(
        make_function_taskspec(tid, "tests.tasks.sample_targets:echo_payload")
    )
    with (
        _os_process_identity(dead=True) as wrapper,
        _os_process_identity(dead=True) as worker,
    ):
        snapshot = task._build_tid_state_payload()
        snapshot.update(
            admission=_record(recovery_manager, tid).to_dict(),
            host_processes=[wrapper, worker],
            hostname=socket.gethostname(),
            terminal=True,
            runtime_handle=None,
        )
        _write_snapshot(weft_harness, snapshot)
        assert recovery_manager._observe_admission_usage() == 3


@contextmanager
def _os_process_identity(*, dead: bool) -> Iterator[dict[str, Any]]:
    """Own a real process and retain exact identity after an explicit wait."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        created = process_create_time(process.pid)
        assert created is not None
        identity = {"pid": process.pid, "create_time": created}
        assert process.stdin is not None
        if dead:
            process.stdin.close()
            process.wait(timeout=10.0)
        yield identity
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10.0)


@pytest.mark.parametrize(
    "evidence", ["unknown", "foreign-host", "live-worker", "dead-tree"]
)
@pytest.mark.parametrize("terminal", [False, True])
def test_recovered_task_budget_requires_exact_local_whole_tree_death(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    task_factory: TaskFactory,
    monkeypatch: pytest.MonkeyPatch,
    evidence: str,
    terminal: bool,
) -> None:
    """Shared process judgment releases crashes, not live or unknown workers.

    Dead processes cannot publish terminal snapshots after a crash. Conversely,
    a terminal hint must not override unknown process evidence for admission.
    """
    manager = recovery_manager
    tid = str(int(manager.tid) + 1)
    task = task_factory(
        make_function_taskspec(tid, "tests.tasks.sample_targets:echo_payload")
    )
    with (
        _os_process_identity(dead=True) as wrapper,
        _os_process_identity(dead=evidence in {"foreign-host", "dead-tree"}) as worker,
    ):
        # Start from the actual task snapshot shape. These historical PG and
        # process observations are inputs to recovery, never a second reducer.
        snapshot = task._build_tid_state_payload()
        snapshot.update(
            admission=_record(manager, tid).to_dict(),
            host_processes=[wrapper, worker],
            hostname="another-host.invalid"
            if evidence == "foreign-host"
            else socket.gethostname(),
            terminal=terminal,
            runtime_handle=None,
        )
        _write_snapshot(weft_harness, snapshot)
        original_process = psutil.Process

        def inspect_process(pid: int | None = None) -> psutil.Process:
            if evidence == "unknown" and pid == worker["pid"]:
                raise psutil.AccessDenied(pid)
            return original_process(pid)

        with monkeypatch.context() as os_fault:
            os_fault.setattr(psutil, "Process", inspect_process)
            assert manager._observe_admission_usage() == (
                3 if evidence == "dead-tree" else 14
            )


@contextmanager
def _snapshot_reads() -> Iterator[list[str]]:
    """Count actual state-reader calls without replacing broker or Manager code."""
    reads: list[str] = []
    original = sys.getprofile()

    def observe(frame: FrameType, event: str, _arg: Any) -> None:
        if event == "call" and frame.f_code is read_task_state_snapshot.__code__:
            reads.append(frame.f_locals["tid"])

    sys.setprofile(observe)
    try:
        yield reads
    finally:
        sys.setprofile(original)


def test_admission_decisions_do_not_reread_task_snapshots(
    recovery_manager: Manager, weft_harness: WeftTestHarness
) -> None:
    """Candidate count must not multiply snapshot I/O for unchanged tasks."""
    manager = recovery_manager
    _pending_launch_snapshot(manager, weft_harness, str(int(manager.tid) + 1))
    assert manager._observe_admission_usage() == 14  # one-time bootstrap
    with _snapshot_reads() as reads:
        for _ in range(5):
            assert manager._admission_allows_queue(manager._queue_names["inbox"])
    assert reads == [], "admission reread existing task state for each candidate"


def test_admission_maintenance_is_bounded_and_round_robin(
    recovery_manager: Manager,
) -> None:
    """Only a bounded slice is read per tick, and the tail is not starved."""
    manager = recovery_manager
    assert manager._observe_admission_usage() == 3
    batch_size = MANAGER_ADMISSION_REFRESH_BATCH_SIZE
    tids = [str(int(manager.tid) + i + 1) for i in range(batch_size * 2 + 1)]
    for tid in tids:
        manager._checkpoint_admission_launch(
            _launch_request(manager, tid, _record(manager, tid))
        )
    with _snapshot_reads() as reads:
        for batch in range(2):
            manager._admission_refresh_after = 0.0  # make the owned timer due
            manager._refresh_postgres_admission_evidence()
            assert (
                reads[batch * batch_size :]
                == tids[batch * batch_size : (batch + 1) * batch_size]
            )
            manager._refresh_postgres_admission_evidence()
            assert len(reads) == (batch + 1) * batch_size
        manager._admission_refresh_after = 0.0
        manager._refresh_postgres_admission_evidence()
        assert reads[2 * batch_size] == tids[-1]
    assert manager._observe_admission_usage() == 3 + 11 * len(tids)


@pytest.mark.parametrize("enabled", [False, True])
def test_idle_admission_maintenance_has_a_wake_deadline(
    recovery_manager: Manager, enabled: bool
) -> None:
    """Idle and gate-disabled managers must still reconcile retained budgets."""
    manager = recovery_manager
    manager._admission_max_connections = 64 if enabled else 0
    assert manager._observe_admission_usage() == 3
    tid = str(int(manager.tid) + 1)
    manager._checkpoint_admission_launch(
        _launch_request(manager, tid, _record(manager, tid))
    )
    manager._admission_refresh_after = 0.0
    assert manager._admission_retry_timeouts(now_ns=time.time_ns()) == [0.0]
    manager._refresh_postgres_admission_evidence()
    assert manager._admission_refresh_after > time.monotonic()


@pytest.mark.parametrize("first_observer", ["maintenance", "reap"])
def test_failed_refresh_stays_closed_until_that_record_is_repaired(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    task_factory: TaskFactory,
    first_observer: str,
) -> None:
    """A different successful read/reap cannot conceal corrupt admission custody."""
    manager = recovery_manager
    assert manager._observe_admission_usage() == 3
    tids = [
        str(int(manager.tid) + i + 1)
        for i in range(MANAGER_ADMISSION_REFRESH_BATCH_SIZE + 2)
    ]
    for tid in tids:
        manager._checkpoint_admission_launch(
            _launch_request(manager, tid, _record(manager, tid))
        )
    task = task_factory(
        make_function_taskspec(tids[1], "tests.tasks.sample_targets:echo_payload")
    )
    broken = task._build_tid_state_payload()
    broken.update(admission=[], runtime_handle=None, host_processes=[])
    _write_snapshot(weft_harness, broken)
    if first_observer == "reap":
        manager._record_reaped_admission(tids[1])
        assert manager._observe_admission_usage() is None
        assert next(iter(manager._admission_launches)) == tids[1]
    for _ in range(2):
        manager._admission_refresh_after = 0.0
        manager._refresh_postgres_admission_evidence()
        assert next(iter(manager._admission_launches)) == tids[1]
        with _snapshot_reads() as reads:
            for tid in tids:
                manager._record_reaped_admission(tid)
        assert reads == []
        assert next(iter(manager._admission_launches)) == tids[1]
        assert manager._observe_admission_usage() is None
    broken["admission"] = _record(manager, tids[1]).to_dict()
    _write_snapshot(weft_harness, broken)
    manager._admission_refresh_after = 0.0
    manager._refresh_postgres_admission_evidence()
    assert manager._observe_admission_usage() == 3 + 11 * len(tids)


def test_reaped_crash_releases_cached_identity_without_task_state(
    recovery_manager: Manager, weft_harness: WeftTestHarness
) -> None:
    """A crash before child publication still has the Manager's exact launch PID."""
    manager = recovery_manager
    tid = str(int(manager.tid) + 1)
    _pending_launch_snapshot(manager, weft_harness, tid)
    assert manager._observe_admission_usage() == 14
    with _os_process_identity(dead=True) as wrapper:
        manager._admission_launches[tid]["host_processes"] = [wrapper]
        manager._record_reaped_admission(tid)
        assert manager._observe_admission_usage() == 3


def test_recovery_preserves_pending_launch_and_original_budget(
    recovery_manager: Manager, weft_harness: WeftTestHarness
) -> None:
    """A Manager-owned launch obligation survives without a child state queue."""
    manager = recovery_manager
    tid = str(int(manager.tid) + 1)
    _pending_launch_snapshot(manager, weft_harness, tid)
    assert manager._observe_admission_usage() == 14
    # The replacement configuration budget is eight; the recorded eleven still
    # governs this older launch, including repeated ordinary refreshes.
    assert manager._observe_admission_usage() == 14


@pytest.mark.parametrize(
    "malformation", ["owner", "record", "process-list", "missing-pid"]
)
def test_corrupt_admission_evidence_fails_closed(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    task_factory: TaskFactory,
    malformation: str,
) -> None:
    """Invalid runtime custody cannot become zero prospective connection cost."""
    manager = recovery_manager
    tid = str(int(manager.tid) + 1)
    task = task_factory(
        make_function_taskspec(tid, "tests.tasks.sample_targets:echo_payload")
    )
    snapshot = task._build_tid_state_payload()
    snapshot.update(
        admission=_record(manager, tid).to_dict(),
        host_processes=[],
        runtime_handle=None,
    )
    if malformation == "owner":
        snapshot["admission"]["connection_owner"] = "wrong-owner"
    elif malformation == "record":
        snapshot["admission"] = []
    elif malformation == "process-list":
        snapshot["host_processes"] = "not-a-process-list"
    else:
        snapshot["host_processes"] = [{"create_time": 1234.0}]
    _write_snapshot(weft_harness, snapshot)
    assert manager._observe_admission_usage() is None


def _launch_request(
    manager: Manager, tid: str, record: AdmissionRecord
) -> _ManagerChildLaunchRequest:
    return _ManagerChildLaunchRequest(
        child_spec=make_function_taskspec(
            tid, "tests.tasks.sample_targets:echo_payload"
        ),
        task_cls=Consumer,
        internal_role=None,
        service_key=None,
        autostart_source=None,
        detach_stdio=True,
        admission=record,
    )


def test_failed_launch_before_process_creation_releases_pending_budget(
    recovery_manager: Manager, weft_harness: WeftTestHarness
) -> None:
    """An authoritative pre-launch failure releases its prospective obligation."""
    manager = recovery_manager
    tid = str(int(manager.tid) + 1)
    record = _pending_launch_snapshot(manager, weft_harness, tid)
    assert manager._observe_admission_usage() == 14
    manager._handle_child_launch_result(
        _ManagerChildLaunchResult(
            request=_launch_request(manager, tid, record),
            error=OSError("process creation failed before launch"),
        )
    )
    assert manager._observe_admission_usage() == 3


def test_postlaunch_cleanup_failure_retains_surviving_process_budget(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup errors keep an already-started process identity and its debt."""
    manager = recovery_manager
    tid = str(int(manager.tid) + 1)
    record = _pending_launch_snapshot(manager, weft_harness, tid)
    assert manager._observe_admission_usage() == 14
    request = _launch_request(manager, tid, record)
    stop = threading.Event()
    items: queue.Queue[Any] = queue.Queue()
    items.put(request)
    context = ServiceWorkerContext(
        name="child-launch",
        request_id=tid,
        worker_index=0,
        input_queue=items,
        stop_event=stop,
        publish=lambda _event: True,
    )
    process = multiprocessing.get_context("spawn").Process(
        target=time.sleep, args=(30.0,)
    )
    process.start()
    try:

        def return_started_process(*_args: Any, **_kwargs: Any) -> BaseProcess:
            stop.set()
            return process

        # Only process-launch/OS control boundaries are substituted. The actual
        # launch service and its result handler must preserve the live identity.
        with monkeypatch.context() as os_fault:
            os_fault.setattr(
                manager_module, "launch_task_process", return_started_process
            )
            os_fault.setattr(
                manager_module, "kill_process_tree", lambda *_args, **_kwargs: set()
            )
            os_fault.setattr(process, "kill", lambda: None)
            result = manager._run_child_launch_service_worker(context)
            assert result is not None and result.error is not None
            manager._handle_child_launch_result(result)
            assert process.is_alive()
            assert manager._observe_admission_usage() == 14
    finally:
        if process.is_alive():
            process.terminate()
        process.join(10.0)
        if process.is_alive():
            process.kill()
            process.join(10.0)
        process.close()


def test_wrapper_checkpoint_preserves_previously_observed_live_worker(
    recovery_manager: Manager,
    weft_harness: WeftTestHarness,
    task_factory: TaskFactory,
) -> None:
    """Async launch commit cannot replace previously published worker custody.

    A fast child can publish its terminal snapshot before the launch-result
    handler checkpoints the wrapper. The retained whole-tree evidence must
    survive that later wrapper checkpoint, even if its task queue then retires.
    """
    manager = recovery_manager
    tid = str(int(manager.tid) + 1)
    record = _pending_launch_snapshot(manager, weft_harness, tid)
    context = multiprocessing.get_context("spawn")
    release = context.Event()
    wrapper = context.Process(target=release.wait)
    wrapper.start()
    assert wrapper.pid is not None
    weft_harness.register_pid(wrapper.pid, tid)
    try:
        wrapper_created = process_create_time(wrapper.pid)
        assert wrapper_created is not None
        release.set()
        wrapper.join(10.0)
        assert not wrapper.is_alive()
        assert wrapper.exitcode == 0
        task = task_factory(
            make_function_taskspec(tid, "tests.tasks.sample_targets:echo_payload")
        )
        with _os_process_identity(dead=False) as worker:
            snapshot = task._build_tid_state_payload()
            snapshot.update(
                admission=record.to_dict(),
                host_processes=[
                    {"pid": wrapper.pid, "create_time": wrapper_created},
                    worker,
                ],
                hostname=socket.gethostname(),
                terminal=True,
                runtime_handle=None,
            )
            _write_snapshot(weft_harness, snapshot)
            assert manager._observe_admission_usage() == 14
            manager._checkpoint_spawned_admission(
                _ManagerChildLaunchResult(
                    request=_launch_request(manager, tid, record),
                    process=wrapper,
                )
            )
            with weft_harness.context.queue(
                task_state_queue_name(tid), persistent=True
            ) as state:
                state.delete()
            assert manager._observe_admission_usage() == 14
            with weft_harness.context.queue(
                task_state_queue_name(manager.tid), persistent=True
            ) as state:
                snapshots = [json.loads(row) for row in state.peek_generator()]
            retained = snapshots[-1]["admission_launches"][tid]["host_processes"]
            assert worker in retained
    finally:
        release.set()
        if wrapper.is_alive():
            wrapper.terminate()
        wrapper.join(10.0)
        wrapper.close()
