"""Public observation of accepted submission custody [MF-5], [PY-2]."""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

from simplebroker.ext import BrokerError
from tests.conftest import run_cli
from tests.core.test_manager import make_manager_spec
from tests.helpers.task_observation import write_task_observation
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_SPAWN_REQUESTS_QUEUE
from weft._exceptions import CommandExecutionError
from weft.client import WeftClient
from weft.commands import submission
from weft.commands import tasks as task_commands
from weft.commands.types import (
    CommandStream,
    TaskEvent,
    TaskSnapshot,
    TaskTerminalSnapshot,
)
from weft.core.manager import Manager
from weft.core.manager_runtime import ManagerEnsureResult
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.service_convergence import build_manager_service_payload
from weft.core.task_state import task_state_queue_name

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize("explicit_tid", [None, "1777000000000000821"])
def test_fresh_client_observes_accepted_request_before_task_publication(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    explicit_tid: str | None,
) -> None:
    """Accepted custody survives submitter closure without consuming the request.

    Only manager startup is held: public submission, broker persistence, and
    both observation owners are real. Existing retained-configuration tests do
    not prove that a fresh client can observe acceptance before publication.
    """
    monkeypatch.setattr(
        submission,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: ManagerEnsureResult(
            outcome="not_needed",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="test_manager_held",
        ),
    )
    context = weft_harness.context
    spec: dict[str, Any] = {
        "name": "unpublished-submission",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    if explicit_tid is not None:
        spec["tid"] = explicit_tid
    with WeftClient(context=context) as submitter:
        task = submitter.submit(spec, payload="retained work")
        tid = task.tid
        weft_harness.register_tid(tid)
        retained_point = task.snapshot()
        assert retained_point is not None and retained_point.status == "queued"
        assert task.terminal_snapshot().status == "queued"
    if explicit_tid is not None:
        assert tid == explicit_tid
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as requests:
        committed = requests.peek_one(exact_timestamp=int(tid))
        assert committed is not None
        with WeftClient(context=context) as observer:
            point = observer.tasks.status(tid)
            terminal = observer.tasks.terminal_snapshot(tid, timeout=0.0)
        assert requests.peek_one(exact_timestamp=int(tid)) == committed
    assert terminal.ack_targets == ()
    assert terminal.terminal is False
    assert point is not None
    assert (point.status, terminal.status) == ("queued", "queued")
    assert terminal.source == "spawn_queue"


def _manager_record(
    context: Any,
    owner: str,
    status: Literal["active", "draining", "stopped", "superseded"] = "stopped",
) -> None:
    payload = build_manager_service_payload(
        context=context,
        tid=owner,
        name="manager",
        status=status,
        queues={"requests": WEFT_SPAWN_REQUESTS_QUEUE},
        runtime_handle={},
    )
    with context.queue("weft.state.services") as registry:
        registry.write(json.dumps(payload))


def _publish(context: Any, tid: str, status: str = "running") -> None:
    write_task_observation(
        context,
        {
            "tid": tid,
            "event": "work_completed" if status == "completed" else "task_started",
            "taskspec": {
                "tid": tid,
                "name": "fresh published name",
                "spec": {"type": "function"},
                "state": {
                    "status": status,
                    "started_at": 123,
                    "completed_at": 456 if status == "completed" else None,
                },
                "metadata": {"phase": "fresh"},
                "io": {"outputs": {"outbox": "fresh.custom.outbox"}},
            },
        },
    )


@pytest.mark.parametrize("lane", ["ordinary", "internal"])
@pytest.mark.parametrize("reserved", [False, True])
def test_public_custody_shapes_across_both_lanes_and_retained_owners(
    weft_harness: WeftTestHarness,
    lane: str,
    reserved: bool,
) -> None:
    """Full public shapes expose custody held by an older stopped manager."""
    context = weft_harness.context
    owner = "1776000000000000001"
    source = WEFT_SPAWN_REQUESTS_QUEUE if lane == "ordinary" else "weft.spawn.internal"
    route = f"T{owner}.{'reserved' if lane == 'ordinary' else 'internal_reserved'}"
    body = json.dumps(
        {"taskspec": {"name": "must not leak", "metadata": {"secret": "private"}}}
    )
    with context.queue(source) as queue:
        tid = str(queue.write(body))
        if reserved:
            assert queue.move_one(route, exact_timestamp=int(tid)) is not None
    _manager_record(context, owner)
    _manager_record(context, "1776000000000000002", "active")
    with WeftClient(context=context) as client:
        point = client.tasks.status(tid)
        fresh_handle = client.task(tid).snapshot()
        terminal = client.task(tid).terminal_snapshot()
    status = "reserved" if reserved else "queued"
    classification = f"submission_{status}"
    assert point is not None and point == fresh_handle
    assert (point.tid, point.name, point.status, point.event) == (
        tid,
        tid,
        status,
        classification,
    )
    assert point.metadata == {}
    assert point.last_timestamp == 0
    assert point.reconciliation == {"classification": classification}
    assert (
        point.return_code,
        point.started_at,
        point.completed_at,
        point.error,
        point.runtime_handle,
        point.runner,
        point.runtime,
    ) == (None,) * 7
    assert (
        terminal.tid,
        terminal.status,
        terminal.source,
        terminal.terminal,
        terminal.observed_at,
        terminal.ack_targets,
    ) == (
        tid,
        status,
        "manager_reserved" if reserved else "spawn_queue",
        False,
        None,
        (),
    )
    assert terminal.metadata == {"classification": classification}
    assert (
        terminal.value,
        terminal.stdout,
        terminal.stderr,
        terminal.error,
        terminal.return_code,
    ) == (None,) * 5
    with context.queue(route if reserved else source) as queue:
        assert queue.peek_one(exact_timestamp=int(tid)) == body


@pytest.mark.parametrize("status", ["running", "completed"])
def test_task_publication_wins_over_retained_reservation(
    weft_harness: WeftTestHarness,
    status: str,
) -> None:
    context = weft_harness.context
    owner = "1776000000000000001"
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as source:
        tid = str(source.write("retained"))
        source.move_one(f"T{owner}.reserved", exact_timestamp=int(tid))
    _manager_record(context, owner)
    _publish(context, tid, status)
    with WeftClient(context=context) as client:
        point = client.tasks.status(tid)
        terminal = client.tasks.terminal_snapshot(tid)
    assert point is not None and point.status == status
    assert point.metadata == {"phase": "fresh"}
    assert terminal.status == status
    assert terminal.terminal is (status == "completed")


@pytest.mark.parametrize("adapter", ["status", "terminal"])
@pytest.mark.parametrize("custody_present", [False, True])
def test_publication_at_custody_read_refreshes_projection_and_routes(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    adapter: str,
    custody_present: bool,
) -> None:
    """A real source peek acts as the handoff barrier before task refresh."""
    context = weft_harness.context
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as source:
        tid = str(source.write("accepted"))
        if not custody_present:
            source.delete(message_id=int(tid))
    with context.broker() as broker:
        broker_type = type(broker)
    original = broker_type.peek_one
    published = False

    def peek(db: Any, queue: str, **kwargs: Any) -> Any:
        nonlocal published
        answer = original(db, queue, **kwargs)
        if (
            queue == WEFT_SPAWN_REQUESTS_QUEUE
            and kwargs.get("exact_timestamp") == int(tid)
            and not published
        ):
            published = True
            _publish(context, tid)
            db.write("fresh.custom.outbox", json.dumps({"fresh": "result"}))
        return answer

    monkeypatch.setattr(broker_type, "peek_one", peek)
    with WeftClient(context=context) as client:
        snapshot = (
            client.tasks.status(tid)
            if adapter == "status"
            else client.tasks.terminal_snapshot(tid)
        )
    assert published
    assert snapshot is not None and snapshot.status == "completed"
    if adapter == "status":
        assert isinstance(snapshot, TaskSnapshot)
        assert snapshot.name == "fresh published name"
        assert snapshot.started_at == 123
        assert snapshot.metadata == {"phase": "fresh"}
    else:
        assert isinstance(snapshot, TaskTerminalSnapshot)
        assert snapshot.source == "outbox"
        assert snapshot.value == {"fresh": "result"}


@pytest.mark.parametrize("adapter", ["status", "terminal"])
def test_malformed_task_state_tail_allows_submission_custody(
    weft_harness: WeftTestHarness,
    adapter: str,
) -> None:
    context = weft_harness.context
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as source:
        tid = str(source.write("accepted"))
    with context.queue(task_state_queue_name(tid)) as state:
        state.write("malformed json")
        state.write(json.dumps({"full": "another task", "projection": {}}))
    with WeftClient(context=context) as client:
        snapshot = (
            client.tasks.status(tid)
            if adapter == "status"
            else client.tasks.terminal_snapshot(tid)
        )
    assert snapshot is not None and snapshot.status == "queued"


@pytest.mark.parametrize("lane", ["ordinary", "internal"])
@pytest.mark.parametrize("adapter", ["status", "terminal"])
def test_source_to_manager_reservation_handoff_uses_real_reserve_owner(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
    adapter: str,
) -> None:
    """Move at the source-read barrier through Manager's actual reserve path."""
    context = weft_harness.context
    owner = "1776000000000000001"
    manager = Manager(
        context.broker_target,
        make_manager_spec(owner, weft_context=str(context.root)),
        config=context.config,
    )
    source = WEFT_SPAWN_REQUESTS_QUEUE if lane == "ordinary" else "weft.spawn.internal"
    route = f"T{owner}.{'reserved' if lane == 'ordinary' else 'internal_reserved'}"
    body = json.dumps(
        {
            "taskspec": {
                "name": "held child",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            }
        }
    )
    try:
        with context.queue(source) as queue:
            tid = str(queue.write(body))
        _manager_record(context, owner)
        with context.broker() as broker:
            if lane == "ordinary":
                manager._index_family_request(body, int(tid), broker=broker)
            broker_type = type(broker)
        original = broker_type.peek_one
        moved = False

        def peek(db: Any, queue_name: str, **kwargs: Any) -> Any:
            nonlocal moved
            if (
                queue_name == source
                and kwargs.get("exact_timestamp") == int(tid)
                and not moved
            ):
                moved = True
                reserved = manager._fetch_next_message(manager._queues[source])
                assert reserved == (body, int(tid))
            return original(db, queue_name, **kwargs)

        monkeypatch.setattr(broker_type, "peek_one", peek)
        with WeftClient(context=context) as client:
            snapshot = (
                client.tasks.status(tid)
                if adapter == "status"
                else client.tasks.terminal_snapshot(tid)
            )
        assert moved
        assert snapshot is not None and snapshot.status == "reserved"
        with context.queue(route) as queue:
            assert queue.peek_one(exact_timestamp=int(tid)) == body
    finally:
        manager.cleanup()


@pytest.mark.parametrize("completion", [False, True])
def test_positive_terminal_wait_defers_custody_until_deadline(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    completion: bool,
) -> None:
    """An actual local publication wakes the wait; held custody waits its budget."""
    context = weft_harness.context
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as source:
        tid = str(source.write("accepted"))
    clock = [0.0]
    monkeypatch.setattr(
        task_commands,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0], time_ns=time.time_ns),
    )
    waits: list[float | None] = []

    def wait(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
        waits.append(timeout)
        assert timeout is not None and 0 < timeout <= 5.0
        if completion:
            clock[0] = 1.0
            _publish(context, tid, "completed")
            return True
        clock[0] += timeout
        return False

    monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
    with context.broker() as broker:
        broker_type = type(broker)
    original = broker_type.peek_one
    custody_reads: list[float] = []

    def peek(db: Any, queue: str, **kwargs: Any) -> Any:
        if queue in {WEFT_SPAWN_REQUESTS_QUEUE, "weft.spawn.internal"}:
            assert clock[0] >= 5.0, "terminal wait acquired custody before deadline"
            custody_reads.append(clock[0])
        return original(db, queue, **kwargs)

    monkeypatch.setattr(broker_type, "peek_one", peek)
    with WeftClient(context=context) as client:
        snapshot = client.tasks.terminal_snapshot(tid, timeout=5.0)
    assert waits
    assert snapshot.status == ("completed" if completion else "queued")
    assert bool(custody_reads) is (not completion)


@pytest.mark.parametrize("metadata", ["invalid_projection", "stale_valid"])
def test_valid_task_state_keeps_its_interpretation_despite_queued_custody(
    weft_harness: WeftTestHarness,
    metadata: str,
) -> None:
    """A valid row owns projection diagnostics even when runtime proof is old."""
    context = weft_harness.context
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as source:
        tid = str(source.generate_timestamp())
    _publish(context, tid)
    with context.broker() as broker:
        rows = list(broker.peek_generator(task_state_queue_name(tid)))
        payload = json.loads(rows[-1][0])
        broker.delete_message_ids(task_state_queue_name(tid), [row[1] for row in rows])
        if metadata == "invalid_projection":
            payload["projection"] = {"version": 999, "status": "completed"}
        broker.insert_messages(
            [
                (
                    task_state_queue_name(tid),
                    json.dumps(payload),
                    int(tid) - 10_000_000_000_000,
                )
            ]
        )
    with WeftClient(context=context) as client:
        before_point = client.tasks.status(tid)
        before_terminal = client.tasks.terminal_snapshot(tid)
        with context.broker() as broker:
            broker.insert_messages([(WEFT_SPAWN_REQUESTS_QUEUE, "accepted", int(tid))])
        after_point = client.tasks.status(tid)
        after_terminal = client.tasks.terminal_snapshot(tid)
    assert before_point is not None and after_point is not None
    assert (after_point.status, after_point.event, after_point.metadata) == (
        before_point.status,
        before_point.event,
        before_point.metadata,
    )
    assert after_point.status == (
        "unknown" if metadata == "invalid_projection" else "running"
    )
    assert after_terminal.status == before_terminal.status
    assert after_terminal.source == before_terminal.source
    if metadata == "invalid_projection":
        assert after_point.reconciliation is not None
        assert (
            after_point.reconciliation["classification"]
            == "task_state_projection_invalid"
        )


@pytest.mark.parametrize(
    "surface",
    [
        "status",
        "terminal_stop",
        "controllable",
        "control_wait",
        "python_watch",
        "cli_watch",
    ],
)
def test_internal_control_and_watch_paths_do_not_acquire_submission_custody(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    """A queued request cannot add registry acquisition to internal wait loops."""
    context = weft_harness.context
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as queue:
        tid = str(queue.write("accepted"))
    with context.broker() as broker:
        broker_type = type(broker)
    original_peek = broker_type.peek_one
    original_scan = broker_type.peek_generator

    def forbid(queue: str) -> None:
        assert queue not in {
            WEFT_SPAWN_REQUESTS_QUEUE,
            "weft.spawn.internal",
            "weft.state.services",
        }, "internal loop acquired custody"
        assert not queue.endswith((".reserved", ".internal_reserved"))

    def peek(db: Any, queue: str, **kwargs: Any) -> Any:
        forbid(queue)
        return original_peek(db, queue, **kwargs)

    def scan(db: Any, queue: str, **kwargs: Any) -> Any:
        forbid(queue)
        return original_scan(db, queue, **kwargs)

    monkeypatch.setattr(broker_type, "peek_one", peek)
    monkeypatch.setattr(broker_type, "peek_generator", scan)
    if surface == "status":
        snapshot = task_commands._task_snapshot(tid, context=context)
        assert snapshot is not None and snapshot.status == "unknown"
    elif surface == "terminal_stop":
        with context.broker() as broker:
            terminal = task_commands._terminal_observation_snapshot(
                context, tid, broker=broker
            )
        assert terminal.status == "unknown"
    elif surface == "controllable":
        controllable = task_commands._require_controllable_task(context, tid, tid)
        assert controllable is not None and controllable.status == "unknown"
    elif surface == "control_wait":
        with pytest.raises(CommandExecutionError, match="observation metadata"):
            task_commands._await_control_surface(context, tid, timeout=0.0)
    elif surface == "python_watch":
        iterator = iter(
            task_commands.watch_task_status(tid, context=context, timeout=0.0)
        )
        try:
            assert next(iterator).status == "unknown"
            with pytest.raises(TimeoutError):
                next(iterator)
        finally:
            cast(Generator[Any, None, None], iterator).close()
    else:
        stream = task_commands.cmd_task_status(tid, watch=True, context=context.root)
        cast(CommandStream[TaskEvent], stream).close()


def test_cli_point_status_renders_queued_custody(
    weft_harness: WeftTestHarness,
) -> None:
    """The real subprocess CLI delegates full-TID point status to custody."""
    context = weft_harness.context
    body = json.dumps({"taskspec": {"name": "queued for CLI"}})
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as source:
        tid = str(source.write(body))
    code, output, error = run_cli(
        "task", "status", tid, "--json", cwd=context.root, harness=weft_harness
    )
    assert code == 0, error
    snapshot = json.loads(output)
    assert snapshot["tid"] == tid
    assert snapshot["status"] == "queued"
    assert snapshot["event"] == "submission_queued"
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as source:
        assert source.peek_one(exact_timestamp=int(tid)) == body


def test_public_custody_uses_exact_id_in_resolved_context(
    weft_harness: WeftTestHarness,
) -> None:
    """Another context's same ID and this context's matching body are not custody."""
    context = weft_harness.context
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as source:
        tid = str(source.generate_timestamp())
        distractor_id = source.write(json.dumps({"tid": tid, "payload": tid}))
    with WeftTestHarness() as other:
        with other.context.broker() as broker:
            broker.insert_messages(
                [(WEFT_SPAWN_REQUESTS_QUEUE, "accepted elsewhere", int(tid))]
            )
        with (
            WeftClient(context=context) as client,
            WeftClient(context=other.context) as elsewhere,
        ):
            point = client.tasks.status(tid)
            terminal = client.tasks.terminal_snapshot(tid)
            other_point = elsewhere.tasks.status(tid)
            other_terminal = elsewhere.tasks.terminal_snapshot(tid)
        assert point is not None and point.status == "unknown"
        assert terminal.status == "unknown"
        assert other_point is not None and other_point.status == "queued"
        assert other_terminal.status == "queued"
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as source:
        assert source.peek_one(exact_timestamp=distractor_id) == json.dumps(
            {"tid": tid, "payload": tid}
        )


@pytest.mark.parametrize("surface", ["status", "handle", "terminal"])
@pytest.mark.parametrize("failure_at", ["source", "registry", "reserved"])
def test_public_custody_read_failures_remain_typed(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    failure_at: str,
) -> None:
    """New auxiliary I/O cannot leak raw broker errors through public point APIs."""
    context = weft_harness.context
    owner, tid = "1778000000000000001", "1779000000000000001"
    _manager_record(context, owner)
    reserved = f"T{owner}.reserved"
    with context.broker() as broker:
        broker_type = type(broker)
    peek, scan = broker_type.peek_one, broker_type.peek_generator

    def checked_peek(db: Any, name: str, **kwargs: Any) -> Any:
        if name == {"source": WEFT_SPAWN_REQUESTS_QUEUE, "reserved": reserved}.get(
            failure_at
        ):
            raise BrokerError("custody read unavailable")
        return peek(db, name, **kwargs)

    def checked_scan(db: Any, name: str, **kwargs: Any) -> Any:
        if failure_at == "registry" and name == "weft.state.services":
            raise BrokerError("custody registry unavailable")
        return scan(db, name, **kwargs)

    monkeypatch.setattr(broker_type, "peek_one", checked_peek)
    monkeypatch.setattr(broker_type, "peek_generator", checked_scan)
    with WeftClient(context=context) as client, pytest.raises(CommandExecutionError):
        if surface == "status":
            client.tasks.status(tid)
        elif surface == "handle":
            client.task(tid).snapshot()
        else:
            client.tasks.terminal_snapshot(tid)


@pytest.mark.parametrize("tid", ["9999999999999999999", "123", "1" * 20, "0" * 19])
def test_custody_is_not_probed_for_noncanonical_or_out_of_range_identity(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    tid: str,
) -> None:
    """Custody enriches canonical accepted IDs without widening old normalization."""
    context = weft_harness.context
    with context.broker() as broker:
        broker_type = type(broker)
    peek, scan = broker_type.peek_one, broker_type.peek_generator

    def checked_peek(db: Any, name: str, **kwargs: Any) -> Any:
        assert name not in {"weft.spawn.requests", "weft.spawn.internal"}
        return peek(db, name, **kwargs)

    def checked_scan(db: Any, name: str, **kwargs: Any) -> Any:
        assert name != "weft.state.services"
        return scan(db, name, **kwargs)

    monkeypatch.setattr(broker_type, "peek_one", checked_peek)
    monkeypatch.setattr(broker_type, "peek_generator", checked_scan)
    with WeftClient(context=context) as client:
        status = client.tasks.status(tid)
        terminal = client.tasks.terminal_snapshot(tid)
    assert status is None or status.status == "unknown"
    assert terminal.status == "unknown" and terminal.terminal is False
