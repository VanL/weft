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
from tests.helpers.test_backend import active_test_backend
from tests.helpers.typing import BrokerEnv
from tests.helpers.weft_harness import WeftTestHarness
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
pytest_plugins = ["tests.helpers.pytest_pg_runtime"]


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


@pytest.fixture
def startup_pg_manager(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[Manager, WeftTestHarness]]:
    """Isolate physical PG capacity before constructing its broker or Manager."""
    if active_test_backend() == "postgres":
        request.getfixturevalue("isolated_postgres_server")
    with WeftTestHarness() as harness:
        target = harness.context.broker_target
        with harness.context.queue("startup-window-ids", persistent=True) as queue:
            tid = str(queue.generate_timestamp())
        task = Manager(
            target,
            make_manager_spec(tid, idle_timeout=0.0),
            config=load_config(
                {
                    "WEFT_ADMISSION_MAX_CONNECTIONS": 300,
                    "WEFT_ADMISSION_RESERVE_FRACTION": 0.1,
                    "WEFT_TASK_MONITOR_ENABLED": False,
                    "WEFT_LIVENESS_MONITOR_ENABLED": False,
                    "WEFT_HEARTBEAT_ENABLED": False,
                    "WEFT_AUTOSTART_TASKS": False,
                }
            ),
        )
        try:
            if active_test_backend() == "postgres":
                # PG accounting is optional and must not load on SQLite runs.
                from simplebroker_pg import get_connection_usage

                assert get_connection_usage(task._get_connected_queue())["total"] < 135
            yield task, harness
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
        assert not manager._startup_feedback
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
    run_after_release: bool = False,
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
        if run_after_release:
            task.run()
    finally:
        task.cleanup()


def _join_waiting_processes(processes: list[BaseProcess]) -> None:
    """Close this fixture's real process producers before broker teardown."""
    for process in processes:
        process.join(timeout=30.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=10.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=10.0)


def _release_waiting_tasks(
    publications: dict[str, multiprocessing.synchronize.Event],
    releases: dict[str, multiprocessing.synchronize.Event],
) -> None:
    for event in (*publications.values(), *releases.values()):
        event.set()


def _check_rejected_publication_keeps_startup_window(
    manager: Manager, tids: list[str], reserved: Queue
) -> None:
    """One real invalid publication advances only its own startup cursor."""
    manager._ensure_wait_strategy_started()
    records = dict(manager._startup_feedback)
    state = manager._queue(f"weft.state.tasks.{tids[0]}")
    resident = manager._scheduler_residents[tids[0]]
    launched_ns = resident["launched_ns"]
    # Hold this TID's absence fallback as well as periodic maintenance. Invalid
    # publication reconciliation must not race a legitimate one-second fallback.
    resident["launched_ns"] = time.time_ns() + 120_000_000_000
    try:
        invalid_id = state.write("{}")
        drive_until(
            lambda: records[tids[0]].publication_id,
            lambda stamp: stamp == invalid_id,
            step=manager.process_once,
            wait=manager.wait_for_activity,
            timeout=30.0,
            wait_slice=30.0,
        )
        manager.process_once()
    finally:
        resident["launched_ns"] = launched_ns
    assert set(manager._startup_feedback) == set(tids)
    assert records[tids[0]].publication_id == invalid_id
    for tid in tids:
        assert manager._startup_feedback[tid] is records[tid]
        assert records[tid].queue is not None
        assert reserved.peek_one(exact_timestamp=int(tid)) is not None
    assert all(records[tid].publication_id is None for tid in tids[1:])


def _check_missing_publication_settles_only_one_startup(
    manager: Manager, tid: str, reserved: Queue
) -> None:
    """Fallback releases observation, not its custody or another TID's interest."""
    manager._ensure_wait_strategy_started()
    records = dict(manager._startup_feedback)
    interests = {other_tid: record.queue for other_tid, record in records.items()}
    manager._scheduler_residents[tid]["launched_ns"] = time.time_ns() - int(
        2 * MANAGER_ADMISSION_RECHECK_SECONDS * 1_000_000_000
    )
    # Reconcile this exact owner boundary. Enabling full periodic maintenance
    # would also age out the other intentionally delayed startup publications.
    with manager._get_connected_queue().get_connection() as broker:
        manager._advance_launch_custody(tid, None, broker=broker)
    assert set(manager._startup_feedback) == set(records) - {tid}
    assert records[tid].queue is None
    assert reserved.peek_one(exact_timestamp=int(tid)) is not None
    for other_tid in set(records) - {tid}:
        assert manager._startup_feedback[other_tid] is records[other_tid]
        assert records[other_tid].queue is interests[other_tid]
        assert interests[other_tid] is not None
        assert reserved.peek_one(exact_timestamp=int(other_tid)) is not None


def test_startup_window_overlaps_but_bounds_unobserved_processes(
    startup_pg_manager: tuple[Manager, WeftTestHarness],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A receipt wakes the next launch; later feedback frees only its own slot.

    Fence before Consumer construction so neither attributed broker connections
    nor task-state publication can settle startup before the intended release.
    """
    manager, harness = startup_pg_manager
    window = 4 if active_test_backend() == "postgres" else 1
    manager._refresh_scheduler_evidence()
    source = manager._queue(manager._queue_names["inbox"])
    reserved = manager._queue(manager._queue_names["reserved"])
    body = json.dumps(
        {
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:provide_payload",
            }
        }
    )
    request_ids = [int(source.write(body)) for _ in range(window + 1)]
    tids = [str(request_id) for request_id in request_ids]
    for tid in tids:
        harness.register_tid(tid)
    spawn = multiprocessing.get_context("spawn")
    releases = {tid: spawn.Event() for tid in tids}
    publications = {tid: spawn.Event() for tid in tids}
    started = {tid: spawn.Event() for tid in tids}
    ready = {tid: spawn.Event() for tid in tids}
    processes: list[BaseProcess] = []

    def launch(
        _task_cls: type[Any], _target: Any, child_spec: TaskSpec, **kwargs: Any
    ) -> BaseProcess:
        tid = child_spec.tid
        assert tid is not None
        process = spawn.Process(
            target=_publish_waiting_task,
            args=(
                child_spec.model_dump(mode="json"),
                kwargs["runtime_identity"].to_dict(),
                ready[tid],
                releases[tid],
                publications[tid],
                started[tid],
                False,
                True,
            ),
        )
        processes.append(process)
        process.start()
        assert process.pid is not None
        harness.register_pid(process.pid)
        assert started[tid].wait(timeout=30.0)
        return process

    def settled_receipts(count: int) -> bool:
        return (
            all(started[tid].is_set() for tid in tids[:count])
            and all(tid in manager._child_processes for tid in tids[:count])
            and not manager._active_child_launches
        )

    def drive_to_receipts(count: int, *, reason: str) -> None:
        try:
            drive_until(
                lambda: settled_receipts(count),
                bool,
                step=manager.process_once,
                wait=manager.wait_for_activity,
                timeout=30.0,
                wait_slice=30.0,
            )
        except AssertionError as exc:
            raise AssertionError(
                f"{reason}: received={list(manager._child_processes)}"
            ) from exc

    monkeypatch.setattr(manager_mod, "launch_task_process", launch)
    try:
        # No periodic tick may substitute for receipt-to-next-turn notification.
        manager._admission_refresh_after = time.monotonic() + 120.0
        manager._next_inactive_probe_at = time.monotonic() + 120.0
        drive_to_receipts(1, reason="first startup did not reach its receipt barrier")
        drive_to_receipts(
            min(window, 2),
            reason="second startup did not progress before first feedback",
        )
        assert not ready[tids[0]].is_set()
        drive_to_receipts(window, reason="startup window did not fill")
        # The worker has returned every receipt. Give dispatch complete turns
        # while all allowed producers remain fenced before publication.
        for _ in range(5):
            manager.process_once()
            manager.wait_for_activity(timeout=0.0)
        assert not manager._queue_has_pending(source), "full window remained actionable"
        assert not manager._active_child_launches, "startup selected beyond the window"
        assert not started[tids[window]].is_set(), "startup exceeded the window"
        assert source.peek_one(exact_timestamp=request_ids[window]) is not None
        assert reserved.peek_one(exact_timestamp=request_ids[window]) is None
        if window > 1:
            _check_rejected_publication_keeps_startup_window(
                manager, tids[:window], reserved
            )

        siblings = {
            tid: (manager._startup_feedback[tid], manager._startup_feedback[tid].queue)
            for tid in tids[: window - 1]
        }
        publications[tids[window - 1]].set()
        assert ready[tids[window - 1]].wait(timeout=30.0)
        drive_to_receipts(
            window + 1, reason="out-of-order feedback did not free a startup slot"
        )
        assert reserved.peek_one(exact_timestamp=request_ids[window - 1]) is None
        for request_id in request_ids[: window - 1]:
            assert reserved.peek_one(exact_timestamp=request_id) is not None
        assert source.peek_one(exact_timestamp=request_ids[window]) is None
        assert reserved.peek_one(exact_timestamp=request_ids[window]) is not None
        assert set(manager._startup_feedback) == (
            set(tids[:window]) - {tids[window - 1]} | {tids[window]}
        )
        assert all(
            manager._startup_feedback[tid] is record
            and record.queue is interest
            and interest is not None
            for tid, (record, interest) in siblings.items()
        )
        if window > 1:
            _check_missing_publication_settles_only_one_startup(
                manager, tids[0], reserved
            )

        _release_waiting_tasks(publications, releases)
        outboxes = [manager._queue(f"T{tid}.outbox") for tid in tids]
        drive_until(
            lambda: [queue.peek_one() for queue in outboxes],
            lambda results: all(body is not None for body in results),
            step=manager.process_once,
            wait=manager.wait_for_activity,
            timeout=60.0,
        )
        assert [json.loads(queue.read_one() or "null") for queue in outboxes] == [
            {"data": "payload"}
        ] * (window + 1)
    finally:
        _release_waiting_tasks(publications, releases)
        _join_waiting_processes(processes)


def test_startup_handoff_reactivates_pending_public_queue(manager: Manager) -> None:
    """Startup evidence makes known backlog runnable without rediscovery."""
    manager._refresh_scheduler_evidence()
    source = manager._queue(manager._queue_names["inbox"])
    reserved = manager._queue_names["reserved"]
    first_id = int(source.write(request_body()))
    assert source.move_one(reserved, exact_timestamp=first_id) is not None
    spec = manager._build_child_spec(json.loads(request_body()), first_id)
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
        first_id,
        identity,
    )
    manager._checkpoint_admission_launch(request)
    next_id = int(source.write(request_body()))
    manager._remove_inactive_queues({source.name})
    manager._pending_messages_precheck_confirmed = False
    manager._next_inactive_probe_at = time.monotonic() + 60.0
    spawn = multiprocessing.get_context("spawn")
    ready, release = spawn.Event(), spawn.Event()
    process = spawn.Process(
        target=_publish_waiting_task,
        args=(spec.model_dump(mode="json"), identity.to_dict(), ready, release),
    )
    try:
        process.start()
        assert ready.wait(timeout=30.0)
        manager._checkpoint_spawned_admission(
            _ManagerChildLaunchResult(request=request, process=process)
        )
        manager._admission_refresh_after = 0.0
        manager._refresh_scheduler_evidence()
        assert not manager._startup_feedback
        manager.process_once()
        assert source.peek_one(exact_timestamp=next_id) is None
        assert manager._queue(reserved).peek_one(exact_timestamp=next_id) is not None
    finally:
        release.set()
        process.join(timeout=30.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=30.0)


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
    # Disable periodic reconciliation before launch so the test isolates the
    # notification path even when process startup takes longer than the real
    # missing-publication fallback interval under CI load.
    manager._admission_refresh_after = time.monotonic() + 60.0
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
        assert str(request_id) in manager._startup_feedback
        feedback = manager._startup_feedback[str(request_id)]
        state_name = f"weft.state.tasks.{request_id}"
        if not publish_before_interest:
            manager._ensure_wait_strategy_started()
            assert feedback.queue is not None and feedback.queue.name == state_name
        else:
            assert feedback.queue is None
        assert state_name not in manager._queues
        if invalid_first:
            # An incomplete fact must not disarm the later task publication.
            # Observe its real notification before releasing the constructor.
            invalid_id = manager._queue(state_name).write("{}")
            drive_until(
                lambda: feedback.publication_id,
                lambda stamp: stamp == invalid_id,
                step=manager.process_once,
                wait=manager.wait_for_activity,
                timeout=0.5,
            )
            manager.process_once()
            assert str(request_id) in manager._startup_feedback
            assert feedback.queue is not None and feedback.queue.name == state_name
        before_publish.set()
        assert ready.wait(timeout=30.0)
        drive_until(
            lambda: str(request_id) in manager._startup_feedback,
            lambda pending: not pending,
            step=manager.process_once,
            wait=manager.wait_for_activity,
            timeout=0.5,
        )
        assert processes[0].is_alive()
        assert feedback.queue is None
        assert str(request_id) not in manager._startup_feedback
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
        assert not manager._startup_feedback
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
            assert str(parent_id) in manager._startup_feedback
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
                    assert str(parent_id) in manager._startup_feedback
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
                assert not manager._startup_feedback
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
