"""Fault contracts for bounded family maintenance and launch custody [MA-1.8].

Broker writes and the clock are external fault seams. The real Manager owns
classification, checkpoint publication, reservation decisions, and retries.
"""

from __future__ import annotations

import json
import multiprocessing
import multiprocessing.synchronize
import time
from collections.abc import Iterator
from multiprocessing.process import BaseProcess
from types import SimpleNamespace
from typing import Any

import pytest

import weft.core.manager as manager_mod
import weft.core.tasks.base as base_mod
from simplebroker import Queue
from simplebroker.ext import BrokerError
from tests.core.test_manager import make_manager_spec
from tests.helpers.reactor_driver import drive_until
from tests.helpers.typing import BrokerEnv
from weft._constants import (
    MANAGER_ADMISSION_RECHECK_SECONDS,
    WEFT_FAMILY_PENDING_PREFIX,
    load_config,
)
from weft.core.family_scheduling import FamilyScheduler
from weft.core.manager import (
    Manager,
    _ManagerChildLaunchRequest,
    _ManagerChildLaunchResult,
)
from weft.core.runtime_identity import (
    RuntimeIdentity,
    install_runtime_identity,
    runtime_identity_for_task,
    submission_lineage,
)
from weft.core.tasks.consumer import Consumer
from weft.core.taskspec import TaskSpec

pytestmark = pytest.mark.shared


@pytest.fixture
def manager(broker_env: BrokerEnv) -> Iterator[Manager]:
    target, queues = broker_env
    tid = str(queues("maintenance-ids").generate_timestamp())
    task = Manager(
        target,
        make_manager_spec(tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_ADMISSION_MAX_CONNECTIONS": 0,
                "WEFT_TASK_MONITOR_ENABLED": False,
                "WEFT_LIVENESS_MONITOR_ENABLED": False,
                "WEFT_HEARTBEAT_ENABLED": False,
                "WEFT_AUTOSTART_TASKS": False,
            }
        ),
    )
    try:
        yield task
    finally:
        task.stop(join=False)
        task.cleanup()


def request_body() -> str:
    return json.dumps(
        {
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:large_output",
            }
        }
    )


def test_unfinished_bootstrap_uses_evidence_retry_without_expired_repair_timer(
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.monotonic()
    manager._admission_refresh_after = now + MANAGER_ADMISSION_RECHECK_SECONDS
    with monkeypatch.context() as patch:
        patch.setattr(manager_mod.time, "monotonic", lambda: now)
        timeouts = manager._admission_retry_timeouts(now_ns=time.time_ns())
        assert timeouts and min(timeouts) == MANAGER_ADMISSION_RECHECK_SECONDS


def test_failed_family_index_write_waits_then_retries_accepted_request(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._refresh_scheduler_evidence()
    source = manager._queue(manager._queue_names["inbox"])
    request_id = int(source.write(request_body()))
    clock = [time.monotonic()]
    attempts: list[str] = []
    with manager._get_connected_queue().get_connection() as broker:
        broker_type = type(broker)
    original_write = broker_type.write

    def fail_index_write(self: Any, queue_name: str, *args: Any, **kwargs: Any) -> Any:
        if queue_name.startswith(WEFT_FAMILY_PENDING_PREFIX):
            attempts.append(queue_name)
            raise BrokerError("injected pending-index write failure")
        return original_write(self, queue_name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(
            manager_mod,
            "time",
            SimpleNamespace(
                monotonic=lambda: clock[0],
                time_ns=time.time_ns,
                sleep=time.sleep,
                time=time.time,
            ),
        )
        patch.setattr(broker_type, "write", fail_index_write)
        manager._classify_family_requests()
        assert attempts
        assert source.peek_one(exact_timestamp=request_id) is not None
        timeouts = manager._admission_retry_timeouts(now_ns=time.time_ns())
        assert timeouts and min(timeouts) > 0
        count = len(attempts)
        manager._classify_family_requests()
        assert len(attempts) == count
        clock[0] += MANAGER_ADMISSION_RECHECK_SECONDS
        patch.setattr(broker_type, "write", original_write)
        manager._classify_family_requests()
        assert manager._family_scheduler.pending(request_id) is not None


def test_undurable_internal_launch_blocks_ordinary_reservation_until_checkpoint(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._refresh_scheduler_evidence()
    source = manager._queue(manager._queue_names["inbox"])
    ordinary_id = int(source.write(request_body()))
    manager._classify_family_requests()
    internal = manager._queue(manager._queue_names["internal_inbox"])
    internal_id = int(internal.write(request_body()))
    reserved = manager._queue_names["internal_reserved"]
    assert internal.move_one(reserved, exact_timestamp=internal_id) is not None
    spec = manager._build_child_spec(json.loads(request_body()), internal_id)
    assert spec is not None
    identity = runtime_identity_for_task(
        spec, manager._db_path, internal=True, config=manager._manager_context().config
    )
    request = _ManagerChildLaunchRequest(
        spec,
        Consumer,
        None,
        None,
        None,
        True,
        internal.name,
        reserved,
        internal_id,
        identity,
    )
    manager._checkpoint_admission_launch(request)
    original_write = Queue.write
    state_name = f"weft.state.tasks.{manager.tid}"

    def fail_checkpoint_write(self: Queue, *args: Any, **kwargs: Any) -> Any:
        if self.name == state_name:
            raise BrokerError("injected Manager checkpoint write failure")
        return original_write(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Queue, "write", fail_checkpoint_write)
        manager._checkpoint_spawned_admission(
            _ManagerChildLaunchResult(
                request, multiprocessing.current_process(), time.time_ns()
            )
        )
        assert manager._startup_feedback_tid is None
        assert not manager._process_queue_message(source.name, set())
        assert source.peek_one(exact_timestamp=ordinary_id) is not None
        assert (
            manager._queue(manager._queue_names["reserved"]).peek_one(
                exact_timestamp=ordinary_id
            )
            is None
        )
    assert manager._publish_scheduler_checkpoint()
    assert manager._queue_has_pending(source)


@pytest.mark.parametrize("restore", [True, False])
def test_valid_source_backed_hint_survives_restore_and_repair(
    manager: Manager, restore: bool
) -> None:
    manager._refresh_scheduler_evidence()
    source = manager._queue(manager._queue_names["inbox"])
    request_id = int(source.write(request_body()))
    manager._classify_family_requests()
    request = manager._family_scheduler.pending(request_id)
    assert request is not None
    manager._family_scheduler = FamilyScheduler()
    with manager._get_connected_queue().get_connection() as broker:
        if restore:
            manager._restore_family_pending(broker)
        else:
            manager._repair_family_pending(broker)
        assert manager._family_scheduler.pending(request_id) == request
        assert (
            broker.peek_one(
                request.queue_name, exact_timestamp=request.label_message_id
            )
            is not None
        )


def _publish_waiting_task(
    spec_payload: dict[str, Any],
    identity_payload: dict[str, Any],
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
    before_publish: multiprocessing.synchronize.Event | None = None,
    started: multiprocessing.synchronize.Event | None = None,
    unknown_create_time: bool = False,
) -> None:
    if started is not None:
        started.set()
    if before_publish is not None and not before_publish.wait(timeout=60.0):
        raise TimeoutError("waiting task publication was not released")
    identity = RuntimeIdentity.from_mapping(identity_payload)
    install_runtime_identity(identity)
    assert identity.broker_target is not None
    if unknown_create_time:
        # External OS identity probe fails inside the actual spawned Consumer.
        base_mod.process_create_time = lambda pid: None
    task = Consumer(
        identity.broker_target,
        TaskSpec.model_validate(spec_payload),
        config=identity.config,
    )
    try:
        ready.set()
        if not release.wait(timeout=60.0):
            raise TimeoutError("waiting task fixture was not released")
    finally:
        task.cleanup()


@pytest.mark.parametrize(
    "persistent,invalid_first,publish_before_interest",
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, False, True),
    ],
)
def test_positive_startup_snapshot_wakes_admission_without_maintenance_tick(
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
    persistent: bool,
    invalid_first: bool,
    publish_before_interest: bool,
) -> None:
    """A real delayed snapshot releases startup before the absence fallback.

    Hold periodic reconciliation beyond the observation deadline. The real
    publication and reactor notification must own admission and exact handoff.
    """
    manager._refresh_scheduler_evidence()
    source = manager._queue(manager._queue_names["inbox"])
    payload = json.loads(request_body())
    payload["spec"]["persistent"] = persistent
    request_id = int(source.write(json.dumps(payload)))
    reserved = manager._queue_names["reserved"]
    assert source.move_one(reserved, exact_timestamp=request_id) is not None
    spec = manager._build_child_spec(payload, request_id)
    assert spec is not None
    spawn = multiprocessing.get_context("spawn")
    started, before_publish = spawn.Event(), spawn.Event()
    ready, release = spawn.Event(), spawn.Event()
    processes: list[BaseProcess] = []

    def launch(
        _task_cls: type[Any], _target: Any, child_spec: TaskSpec, **kwargs: Any
    ) -> BaseProcess:
        process = spawn.Process(
            target=_publish_waiting_task,
            args=(
                child_spec.model_dump(mode="json"),
                kwargs["runtime_identity"].to_dict(),
                ready,
                release,
                before_publish,
                started,
            ),
        )
        processes.append(process)
        process.start()
        # Finish fixture import before returning the actual process receipt;
        # publication stays held until the observer is established.
        assert started.wait(timeout=30.0)
        return process

    monkeypatch.setattr(manager_mod, "launch_task_process", launch)
    try:
        assert manager._launch_child_task(
            spec,
            None,
            source_queue=source.name,
            reserved_queue=reserved,
            message_timestamp=request_id,
        )
        drive_until(
            lambda: str(request_id) in manager._child_processes,
            bool,
            step=manager.process_once,
            wait=manager.wait_for_activity,
            timeout=30.0,
        )
        assert manager._startup_feedback_tid == str(request_id)
        manager._admission_refresh_after = time.monotonic() + 60.0
        state_name = f"weft.state.tasks.{request_id}"
        if not publish_before_interest:
            manager._ensure_wait_strategy_started()
            assert manager._startup_notification_tid == str(request_id)
        else:
            assert manager._startup_notification_queue is None
        assert state_name not in manager._queues
        if invalid_first:
            # An incomplete fact must not disarm the later task publication.
            # Observe its real notification before releasing the constructor.
            invalid_id = manager._queue(state_name).write("{}")
            drive_until(
                lambda: manager._startup_publication_id,
                lambda stamp: stamp == invalid_id,
                step=manager.process_once,
                wait=manager.wait_for_activity,
                timeout=0.5,
            )
            manager.process_once()
            assert manager._startup_feedback_tid == str(request_id)
            assert manager._startup_notification_tid == str(request_id)
        before_publish.set()
        assert ready.wait(timeout=30.0)
        drive_until(
            lambda: manager._startup_feedback_tid,
            lambda tid: tid is None,
            step=manager.process_once,
            wait=manager.wait_for_activity,
            timeout=0.5,
        )
        assert processes[0].is_alive()
        assert manager._startup_notification_queue is None
        assert manager._startup_notification_tid is None
        assert manager._queue(state_name).has_pending()
        assert state_name not in manager._queues
        assert manager._queue(reserved).peek_one(exact_timestamp=request_id) is None
        assert manager._family_scheduler.frontiers == (
            () if persistent else ((str(request_id),),)
        )
        # Settled notification interest must not interfere with a later lost
        # launch result. Recovery retains exact custody and never replays it.
        parent_identity = runtime_identity_for_task(
            spec, manager._db_path, config=manager._manager_context().config
        )
        child_payload = json.loads(request_body())
        child_payload["submitted_by"] = submission_lineage(parent_identity)
        child_id = int(source.write(json.dumps(child_payload)))
        assert source.move_one(reserved, exact_timestamp=child_id) is not None
        child_spec = manager._build_child_spec(child_payload, child_id)
        assert child_spec is not None
        child_identity = runtime_identity_for_task(
            child_spec,
            manager._db_path,
            submitted_by=child_payload["submitted_by"],
            config=manager._manager_context().config,
        )
        child_request = _ManagerChildLaunchRequest(
            child_spec,
            Consumer,
            None,
            None,
            None,
            True,
            source.name,
            reserved,
            child_id,
            child_identity,
        )
        manager._checkpoint_admission_launch(child_request)
        child_ready = spawn.Event()
        child_process = spawn.Process(
            target=_publish_waiting_task,
            args=(
                child_spec.model_dump(mode="json"),
                child_identity.to_dict(),
                child_ready,
                release,
            ),
        )
        processes.append(child_process)
        child_process.start()
        assert child_ready.wait(timeout=30.0)
        manager._active_child_launches[str(child_id)] = child_request
        manager._child_launch_started_ns[str(child_id)] = (
            time.time_ns() - 10_000_000_000
        )
        manager._drain_worker_results()
        assert manager._stale_child_launch_timeouts(now_ns=time.time_ns()) == [0.0]
        assert manager._retry_stale_child_launches()
        assert str(child_id) in manager._family_scheduler.resident_tids
        assert manager._queue(reserved).peek_one(exact_timestamp=child_id) is None
        assert manager._startup_feedback_tid is None
    finally:
        before_publish.set()
        release.set()
        for process in processes:
            process.join(timeout=30.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=30.0)


@pytest.mark.parametrize(
    "late_snapshot,unknown_create_time", [(False, False), (True, False), (True, True)]
)
def test_missing_launch_result_retains_finite_path_and_late_commit_is_idempotent(
    manager: Manager,
    late_snapshot: bool,
    unknown_create_time: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager._refresh_scheduler_evidence()
    source = manager._queue(manager._queue_names["inbox"])
    parent_id = int(source.write(request_body()))
    reserved = manager._queue_names["reserved"]
    assert source.move_one(reserved, exact_timestamp=parent_id) is not None
    spec = manager._build_child_spec(json.loads(request_body()), parent_id)
    assert spec is not None
    identity = runtime_identity_for_task(
        spec, manager._db_path, config=manager._manager_context().config
    )
    request = _ManagerChildLaunchRequest(
        spec,
        Consumer,
        None,
        None,
        None,
        True,
        source.name,
        reserved,
        parent_id,
        identity,
    )
    manager._checkpoint_admission_launch(request)
    manager._active_child_launches[str(parent_id)] = request
    manager._child_launch_started_ns[str(parent_id)] = time.time_ns() - 10_000_000_000
    spawn = multiprocessing.get_context("spawn")
    ready, release = spawn.Event(), spawn.Event()
    before_publish, started = spawn.Event(), spawn.Event()
    process = spawn.Process(
        target=_publish_waiting_task,
        args=(
            spec.model_dump(mode="json"),
            identity.to_dict(),
            ready,
            release,
            before_publish if late_snapshot else None,
            started,
            unknown_create_time,
        ),
    )
    try:
        process.start()
        assert started.wait(timeout=30.0)
        if not late_snapshot:
            assert ready.wait(timeout=30.0)
        assert manager._retry_stale_child_launches()
        if late_snapshot:
            assert manager._family_scheduler.frontiers == ()
            assert manager._startup_feedback_tid == str(parent_id)
            before_publish.set()
            assert ready.wait(timeout=30.0)
            manager._admission_refresh_after = 0.0
            if unknown_create_time:
                with manager._get_connected_queue().get_connection() as broker:
                    broker_type = type(broker)
                original_write = broker_type.write

                def fail_checkpoint(
                    self: Any, queue_name: str, *args: Any, **kwargs: Any
                ) -> Any:
                    if queue_name == f"weft.state.tasks.{manager.tid}":
                        raise BrokerError("injected post-receipt checkpoint failure")
                    return original_write(self, queue_name, *args, **kwargs)

                with monkeypatch.context() as patch:
                    patch.setattr(broker_type, "write", fail_checkpoint)
                    manager._refresh_scheduler_evidence()
                    assert str(parent_id) in manager._scheduler_undurable
                    assert manager._startup_feedback_tid == str(parent_id)
                manager._admission_refresh_after = 0.0
                manager._refresh_scheduler_evidence()
                # Publication releases durability first; the next bounded
                # observation releases startup after its fallback interval.
                future_ns = time.time_ns() + 10_000_000_000
                with monkeypatch.context() as patch:
                    patch.setattr(manager_mod.time, "time_ns", lambda: future_ns)
                    manager._admission_refresh_after = 0.0
                    manager._refresh_scheduler_evidence()
                assert str(parent_id) not in manager._scheduler_undurable
                assert manager._startup_feedback_tid is None
                assert (
                    manager._queue(reserved).peek_one(exact_timestamp=parent_id)
                    is not None
                )
            else:
                manager._refresh_scheduler_evidence()
        assert manager._family_scheduler.frontiers == ((str(parent_id),),)
        independent_id = int(source.write(request_body()))
        manager._classify_family_requests()
        independent = manager._peek_family_candidate()
        assert independent is not None and independent.request_id == independent_id
        child_payload = json.loads(request_body())
        child_payload["submitted_by"] = submission_lineage(identity)
        child_id = int(source.write(json.dumps(child_payload)))
        manager._classify_family_requests()
        candidate = manager._peek_family_candidate()
        assert candidate is not None and candidate.request_id == child_id
        if unknown_create_time:
            return
        result = _ManagerChildLaunchResult(request, process, time.time_ns())
        manager._handle_child_launch_result(result)
        manager._handle_child_launch_result(result)
        assert manager._family_scheduler.frontiers == ((str(parent_id),),)
        assert manager._child_processes[str(parent_id)].process is process
    finally:
        before_publish.set()
        release.set()
        if process.pid is not None:
            process.join(timeout=30.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=30.0)
        if str(parent_id) not in manager._child_processes:
            process.close()
        # An adopted handle remains owned by Manager fixture cleanup.


@pytest.mark.parametrize("operation", ["fetch", "restore", "repair"])
def test_consumed_primary_row_cannot_keep_a_cached_spawn_candidate(
    manager: Manager, operation: str
) -> None:
    manager._refresh_scheduler_evidence()
    source = manager._queue(manager._queue_names["inbox"])
    body = request_body()
    request_id = int(source.write(body))
    manager._classify_family_requests()
    request = manager._family_scheduler.pending(request_id)
    assert request is not None
    assert source.read_one() == body
    # SimpleBroker claims are consumed rows awaiting physical deletion, not
    # leases that a scheduler must wait to acquire.
    assert source.peek_one(exact_timestamp=request_id, include_claimed=True) == body
    if operation == "fetch":
        assert manager._fetch_next_message(manager._queues[source.name]) is None
    else:
        manager._family_scheduler = FamilyScheduler()
        with manager._get_connected_queue().get_connection() as broker:
            if operation == "restore":
                manager._restore_family_pending(broker)
            else:
                manager._repair_family_pending(broker)
    assert manager._family_scheduler.pending(request_id) is None
    assert (
        manager._queue(request.queue_name).peek_one(
            exact_timestamp=request.label_message_id
        )
        is None
    )
    assert manager._peek_family_candidate() is None
    assert not manager._queue_has_pending(source)
