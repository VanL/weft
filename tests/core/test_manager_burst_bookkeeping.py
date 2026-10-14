"""Manager burst bookkeeping and durable post-exit custody [MA-1.6a]."""

from __future__ import annotations

import json
import multiprocessing
import time
from collections.abc import Iterator
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, cast

import pytest

import weft.core.manager as manager_mod
from simplebroker import Queue
from tests.core.test_manager import make_manager_spec, write_autostart_fixture
from tests.helpers.typing import BrokerEnv
from weft._constants import (
    MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS,
    MANAGER_SERVE_LOG_CHILD_LIMIT,
    WEFT_GLOBAL_LOG_QUEUE,
    load_config,
)
from weft.core.manager import ManagedChild, Manager
from weft.core.manager_services import ManagedServiceSpec
from weft.core.runtime_identity import runtime_identity_for_task
from weft.core.tasks import Consumer
from weft.core.taskspec import ReservedPolicy
from weft.helpers import closing_queue_iterator

pytestmark = pytest.mark.shared


@pytest.fixture
def burst_manager(
    broker_env: BrokerEnv, request: pytest.FixtureRequest
) -> Iterator[Manager]:
    db_path, _make_queue = broker_env
    parameter = getattr(request, "param", ReservedPolicy.KEEP)
    policy = parameter if isinstance(parameter, ReservedPolicy) else ReservedPolicy.KEEP
    frontier_limit = parameter if isinstance(parameter, int) else 5
    manager = Manager(
        db_path,
        make_manager_spec(
            str(time.time_ns()),
            idle_timeout=0,
            reserved_policy_on_error=policy,
        ),
        config=load_config(
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_LIVENESS_MONITOR_ENABLED": "0",
                "WEFT_AUTOSTART_TASKS": "0",
                "WEFT_SCHEDULER_MAX_FRONTIERS": str(frontier_limit),
            }
        ),
    )
    try:
        yield manager
    finally:
        manager.cleanup()


@pytest.fixture
def autostart_service(burst_manager: Manager, tmp_path: Path) -> ManagedServiceSpec:
    directory, _manifest = write_autostart_fixture(
        tmp_path,
        task_name="burst-service",
        manifest_name="burst-service",
        mode="ensure",
        duration=30,
    )
    burst_manager._autostart_enabled = True
    burst_manager._autostart_dir = directory
    return burst_manager._desired_autostart_services(force=True)[0]


@pytest.mark.parametrize("shutdown", [False, True])
def test_exited_child_keeps_reservation_until_failsafe_commits(
    burst_manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
    shutdown: bool,
) -> None:
    """A real killed wrapper retains custody across a broker publication outage."""
    manager = burst_manager
    reserved = manager._queue(manager._queue_names["reserved"])
    timestamp = reserved.write(
        json.dumps(
            {
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                }
            }
        )
    )
    spec = manager._build_child_spec(json.loads(str(reserved.peek_one())), timestamp)
    assert spec is not None
    request = manager_mod._ManagerChildLaunchRequest(
        child_spec=spec,
        task_cls=Consumer,
        internal_role=None,
        service_key=None,
        autostart_source=None,
        detach_stdio=True,
        source_queue=manager._queue_names["inbox"],
        reserved_queue=reserved.name,
        message_timestamp=timestamp,
        runtime_identity=runtime_identity_for_task(
            spec, manager._db_path, config=manager._manager_context().config
        ),
    )
    manager._checkpoint_admission_launch(request)
    result = manager._run_child_launch_worker(request)
    process = result.process
    assert process is not None
    real_delete = Queue.delete

    def failed_ack(queue: Queue, **kwargs: Any) -> Any:
        if queue.name == reserved.name:
            raise RuntimeError("named initial spawn acknowledgement outage")
        return real_delete(queue, **kwargs)

    with monkeypatch.context() as ack_failure:
        ack_failure.setattr(Queue, "delete", failed_ack)
        manager._handle_child_launch_result(result)
    process.kill()
    process.join(timeout=10)
    assert not process.is_alive()
    tid = str(timestamp)
    ctrl_out = spec.io.control["ctrl_out"]
    with manager._get_connected_queue().get_connection() as broker:
        broker_type = type(broker)
    real_write = broker_type.write
    blocked = True

    def write(db: Any, queue: str, body: str, **kwargs: Any) -> int:
        if blocked and queue == ctrl_out:
            raise RuntimeError("named failsafe publication outage")
        return real_write(db, queue, body, **kwargs)

    monkeypatch.setattr(broker_type, "write", write)
    try:
        assert manager._cleanup_children() is False
        child = manager._child_processes[tid]
        assert reserved.peek_one(exact_timestamp=timestamp) is not None
        assert child.terminal_retry_after_ns > time.time_ns()
        timeouts = manager._manager_child_timeouts(now_ns=time.time_ns())
        assert timeouts and min(timeouts) > 0
        assert max(timeouts) <= MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS
        if shutdown:
            started = time.monotonic()
            manager._terminate_children(started + 0.1)
            assert time.monotonic() - started < 1
            assert reserved.peek_one(exact_timestamp=timestamp) is not None
            return
        blocked = False
        child.terminal_retry_after_ns = time.time_ns() - 1
        assert manager._cleanup_children() is True
        assert tid not in manager._child_processes
        assert reserved.peek_one(exact_timestamp=timestamp) is None
        rows = list(manager._queue(ctrl_out).peek_generator())
        assert any(json.loads(body).get("source") == "manager" for body in rows)
    finally:
        blocked = False


@pytest.mark.parametrize("read_failure", [False, True])
def test_manager_child_proof_does_not_search_global_history(
    burst_manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
    read_failure: bool,
) -> None:
    manager = burst_manager
    process = multiprocessing.get_context("spawn").Process(target=time.time)
    process.start()
    process.join(timeout=10)
    assert not process.is_alive()
    tid = str(time.time_ns())
    child = ManagedChild(process, None, ctrl_out_queue=f"T{tid}.ctrl_out")
    global_reads = 0
    with manager._get_connected_queue().get_connection() as broker:
        broker_type = type(broker)
    real_peek = broker_type.peek_generator

    def peek(db: Any, queue: str, **kwargs: Any) -> Any:
        nonlocal global_reads
        global_reads += int(queue == WEFT_GLOBAL_LOG_QUEUE)
        if read_failure and queue == child.ctrl_out_queue:
            raise RuntimeError("named local proof read outage")
        return real_peek(db, queue, **kwargs)

    monkeypatch.setattr(broker_type, "peek_generator", peek)
    try:
        proof = manager._child_terminal_proof_visible(tid, child)
        assert proof is (None if read_failure else False)
        assert global_reads == 0
        assert manager._write_manager_terminal_envelope(tid, child) is True
        assert global_reads == 0
    finally:
        process.close()


@pytest.mark.parametrize("backlog", [100, 200, 300])
def test_known_autostart_request_uses_exact_confirmation_through_moves(
    burst_manager: Manager,
    autostart_service: ManagedServiceSpec,
    monkeypatch: pytest.MonkeyPatch,
    backlog: int,
) -> None:
    manager = burst_manager
    public = manager._queue(manager._queue_names["inbox"])
    for _ in range(backlog):
        public.write(json.dumps({"unrelated": True}))
    assert manager._enqueue_managed_service_request(autostart_service)
    scans = 0
    exact_reads = 0
    real_peek = Queue.peek_generator
    real_peek_one = Queue.peek_one

    def peek(queue: Queue, **kwargs: Any) -> Any:
        nonlocal scans
        scans += 1
        return real_peek(queue, **kwargs)

    monkeypatch.setattr(Queue, "peek_generator", peek)

    def peek_one(queue: Queue, **kwargs: Any) -> Any:
        nonlocal exact_reads
        exact_reads += 1
        assert kwargs.get("exact_timestamp") is not None
        return real_peek_one(queue, **kwargs)

    monkeypatch.setattr(Queue, "peek_one", peek_one)
    state = manager._service_state(autostart_service.key)
    assert state.pending_request_id is not None
    request_id = state.pending_request_id
    reserved = manager._queue(manager._queue_names["reserved"])
    assert manager._pending_service_keys({autostart_service.key}) == {
        autostart_service.key
    }
    public.move_one(reserved.name, exact_timestamp=request_id)
    assert manager._pending_service_keys({autostart_service.key}) == {
        autostart_service.key
    }
    reserved.move_one(public.name, exact_timestamp=request_id)
    assert manager._pending_service_keys({autostart_service.key}) == {
        autostart_service.key
    }
    assert scans == 0
    assert exact_reads <= 12  # Three confirmations, at most four attached queues.


@pytest.mark.parametrize("failure", ["open", "iteration", "exact"])
def test_failed_pending_recovery_preserves_request_hint(
    burst_manager: Manager,
    autostart_service: ManagedServiceSpec,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    manager = burst_manager
    assert manager._enqueue_managed_service_request(autostart_service)
    state = manager._service_state(autostart_service.key)
    request_id = state.pending_request_id
    assert request_id is not None
    manager._queue(manager._queue_names["inbox"]).delete(message_id=request_id)

    public = manager._queue(manager._queue_names["inbox"])
    public.write(json.dumps({"unrelated": True}))
    real_peek = Queue.peek_generator

    def failed_read(queue: Queue, **kwargs: Any) -> Any:
        if queue.name != public.name:
            return real_peek(queue, **kwargs)
        if failure == "open":
            raise RuntimeError("named recovery open outage")

        def interrupted() -> Iterator[Any]:
            iterator = real_peek(queue, **kwargs)
            with closing_queue_iterator(iterator) as rows:
                for entry in rows:
                    yield entry
                    break
                raise RuntimeError("named recovery iteration outage")

        return interrupted()

    if failure == "exact":

        def failed_exact(_queue: Queue, **_kwargs: Any) -> Any:
            raise RuntimeError("named recovery exact outage")

        monkeypatch.setattr(Queue, "peek_one", failed_exact)
    else:
        monkeypatch.setattr(Queue, "peek_generator", failed_read)
    with pytest.raises(RuntimeError, match=f"{failure} outage"):
        manager._pending_service_keys({autostart_service.key})
    assert state.pending_request_id == request_id


def test_service_enqueue_failure_does_not_install_request_hint(
    burst_manager: Manager,
    autostart_service: ManagedServiceSpec,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = burst_manager
    public_name = manager._queue_names["inbox"]
    real_write = Queue.write

    def failed_write(queue: Queue, body: str, **kwargs: Any) -> int:
        if queue.name == public_name:
            raise RuntimeError("named service enqueue outage")
        return real_write(queue, body, **kwargs)

    monkeypatch.setattr(Queue, "write", failed_write)
    assert manager._enqueue_managed_service_request(autostart_service) is False
    state = manager._service_state(autostart_service.key)
    assert state.pending_request_id is None
    assert state.spawn_pending is False


@pytest.mark.parametrize("burst_manager", [ReservedPolicy.CLEAR], indirect=True)
@pytest.mark.parametrize("newer_request", [False, True])
@pytest.mark.parametrize("delete_failure", [False, True])
def test_failed_service_launch_clears_only_disposed_matching_request_hint(
    burst_manager: Manager,
    autostart_service: ManagedServiceSpec,
    monkeypatch: pytest.MonkeyPatch,
    newer_request: bool,
    delete_failure: bool,
) -> None:
    manager = burst_manager
    public = manager._queue(manager._queue_names["inbox"])
    reserved = manager._queue(manager._queue_names["reserved"])
    assert manager._enqueue_managed_service_request(autostart_service)
    state = manager._service_state(autostart_service.key)
    request_id = state.pending_request_id
    assert request_id is not None
    body = public.move_one(reserved.name, exact_timestamp=request_id)
    spec = manager._build_child_spec(json.loads(str(body)), request_id)
    assert spec is not None
    request = manager_mod._ManagerChildLaunchRequest(
        child_spec=spec,
        task_cls=Consumer,
        internal_role=None,
        service_key=autostart_service.key,
        autostart_source=autostart_service.autostart_source,
        detach_stdio=True,
        source_queue=public.name,
        reserved_queue=reserved.name,
        message_timestamp=request_id,
    )

    def failed_launch(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("named process launch outage")

    monkeypatch.setattr(manager_mod, "launch_task_process", failed_launch)
    result = manager._run_child_launch_worker(request)
    assert result.error is not None
    if newer_request:
        assert manager._enqueue_managed_service_request(autostart_service)
    retained_hint = state.pending_request_id
    if delete_failure:
        real_delete = Queue.delete

        def failed_delete(queue: Queue, **kwargs: Any) -> Any:
            if queue.name == reserved.name:
                raise RuntimeError("named rejected spawn disposition outage")
            return real_delete(queue, **kwargs)

        monkeypatch.setattr(Queue, "delete", failed_delete)
    manager._handle_child_launch_result(result)
    assert state.pending_request_id == (
        retained_hint if newer_request or delete_failure else None
    )
    assert (reserved.peek_one(exact_timestamp=request_id) is not None) is delete_failure


def test_autostart_convergence_skips_backlog_for_inflight_and_live_child(
    burst_manager: Manager,
    autostart_service: ManagedServiceSpec,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = burst_manager
    public = manager._queue(manager._queue_names["inbox"])
    reserved = manager._queue(manager._queue_names["reserved"])
    assert manager._enqueue_managed_service_request(autostart_service)
    state = manager._service_state(autostart_service.key)
    request_id = state.pending_request_id
    assert request_id is not None
    body = public.move_one(reserved.name, exact_timestamp=request_id)
    spec = manager._build_child_spec(json.loads(str(body)), request_id)
    assert spec is not None
    assert manager._launch_child_task(
        spec,
        {},
        service_key=autostart_service.key,
        autostart_source=autostart_service.autostart_source,
        source_queue=public.name,
        reserved_queue=reserved.name,
        message_timestamp=request_id,
    )
    for _ in range(300):
        public.write(json.dumps({"unrelated": True}))
    scans = 0
    real_peek = Queue.peek_generator
    pending_queues = {
        *manager._spawn_inbox_queue_names(),
        *(pair[0] for pair in manager._spawn_reserved_queue_pairs()),
    }

    def peek(queue: Queue, **kwargs: Any) -> Any:
        nonlocal scans
        scans += int(queue.name in pending_queues)
        return real_peek(queue, **kwargs)

    monkeypatch.setattr(Queue, "peek_generator", peek)
    manager._tick_autostart(force=True)
    assert scans == 0
    assert state.pending_request_id == request_id
    manager._drain_active_child_launches_for_cleanup(time.monotonic() + 10)
    child = manager._child_processes[str(request_id)]
    try:
        assert child.process.is_alive()
        assert state.pending_request_id is None
        manager._tick_autostart(force=True)
        assert scans == 0
        assert state.active_tid == str(request_id)
    finally:
        if child.process.is_alive():
            child.process.kill()
        child.process.join(timeout=10)
        manager._cleanup_children()


def test_pending_recovery_replaces_deleted_reference_and_rejects_untrusted_row(
    burst_manager: Manager,
    autostart_service: ManagedServiceSpec,
) -> None:
    manager = burst_manager
    public = manager._queue(manager._queue_names["inbox"])
    assert manager._enqueue_managed_service_request(autostart_service)
    state = manager._service_state(autostart_service.key)
    first = state.pending_request_id
    assert first is not None
    second = public.write(json.dumps(autostart_service.spawn_payload))
    public.delete(message_id=first)
    assert manager._pending_service_keys({autostart_service.key}) == {
        autostart_service.key
    }
    assert state.pending_request_id == second
    # Administrative deletion/replacement cannot make an ID alone authority.
    public.delete(message_id=second)
    untrusted = dict(autostart_service.spawn_payload)
    untrusted["taskspec"] = dict(untrusted["taskspec"])
    untrusted["taskspec"]["metadata"] = {}
    state.pending_request_id = public.write(json.dumps(untrusted))
    assert manager._pending_service_keys({autostart_service.key}) == set()
    assert state.pending_request_id is None


@pytest.mark.parametrize(
    "burst_manager,population",
    [(100, 100), (200, 200), (300, 300)],
    indirect=["burst_manager"],
)
def test_child_commit_reads_only_new_sentinel(
    burst_manager: Manager,
    population: int,
) -> None:
    """Registration work is counted; actual exit tests cover process semantics."""
    manager = burst_manager
    reads = 0

    class CountedProcess:
        pid = None

        def __init__(self, descriptor: int) -> None:
            self.descriptor = descriptor

        @property
        def sentinel(self) -> int:
            nonlocal reads
            reads += 1
            return self.descriptor

        def is_alive(self) -> bool:
            return True

    pipes = []
    try:
        for _ in range(population):
            reader, writer = multiprocessing.Pipe(duplex=False)
            pipes.append((reader, writer))
            spec = manager._build_child_spec(
                {
                    "spec": {
                        "type": "function",
                        "function_target": "tests.tasks.sample_targets:echo_payload",
                    }
                },
                time.time_ns(),
            )
            assert spec is not None
            request = manager_mod._ManagerChildLaunchRequest(
                child_spec=spec,
                task_cls=Consumer,
                internal_role=None,
                service_key=None,
                autostart_source=None,
                detach_stdio=True,
                runtime_identity=runtime_identity_for_task(
                    spec, manager._db_path, config=manager._manager_context().config
                ),
            )
            manager._checkpoint_admission_launch(request)
            manager._commit_child_launch_success(
                manager_mod._ManagerChildLaunchResult(
                    request=request,
                    process=cast(BaseProcess, CountedProcess(reader.fileno())),
                )
            )
        assert reads == population
        summaries = manager._child_summaries_for_log()
        assert [summary["tid"] for summary in summaries[:-1]] == sorted(
            manager._child_processes
        )[:MANAGER_SERVE_LOG_CHILD_LIMIT]
        assert summaries[-1] == {
            "truncated_count": population - MANAGER_SERVE_LOG_CHILD_LIMIT
        }
    finally:
        manager._child_processes.clear()
        manager._child_sentinel_adapter.close(timeout=2)
        for reader, writer in pipes:
            reader.close()
            writer.close()


def test_sentinel_registration_rejects_replaced_and_removed_identities(
    burst_manager: Manager,
) -> None:
    manager = burst_manager
    adapter = manager._child_sentinel_adapter
    context = multiprocessing.get_context("spawn")
    release = context.Event()
    exited = context.Process(target=time.time)
    replacement = context.Process(target=release.wait)
    exited.start()
    replacement.start()
    try:
        adapter.add("child", exited.sentinel)
        assert adapter.wait(10)
        # Leave the first result queued while the reactor's current identity
        # changes. The queued old exit must not mark the replacement exited.
        manager._child_processes["child"] = ManagedChild(replacement, None)
        adapter.add("child", replacement.sentinel)
        assert manager._drain_child_sentinel_events() == 0
        assert manager._child_processes["child"].sentinel_observed_ns == 0
        adapter.remove("child")
        release.set()
        replacement.join(timeout=10)
        assert not replacement.is_alive()
        adapter.wait(0.05)
        assert adapter.drain() == []
        adapter.add("replacement", replacement.sentinel)
        assert adapter.wait(10)
        result = adapter.drain()
        assert len(result) == 1
        assert isinstance(result[0], manager_mod._ManagerChildSentinelEvent)
        assert result[0].tid == "replacement"
        adapter.add("replacement", replacement.sentinel)
        assert adapter.wait(0.05) is False
    finally:
        manager._child_processes.pop("child", None)
        for process in (exited, replacement):
            if process.is_alive():
                process.kill()
            process.join(timeout=10)
        adapter.close(timeout=1)
        exited.close()
        replacement.close()
