"""Bounded control observers borrow persistent resources (Spec: [SB-0.4])."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import PIPELINE_RUNTIME_METADATA_KEY, WEFT_GLOBAL_LOG_QUEUE
from weft.commands import run as run_cmd
from weft.commands import tasks as task_cmd
from weft.commands.types import RunExecutionResult
from weft.context import WeftContext
from weft.core.monitor.collation import update_from_task_log_payload
from weft.core.monitor.store import open_monitor_store
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.task_state import task_state_queue_name
from weft.core.tasks import Consumer
from weft.core.taskspec import TaskSpec
from weft.helpers import tid_short_form

pytestmark = [pytest.mark.shared, pytest.mark.timeout(30)]


def _track_postgres_connections(
    ctx: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    if ctx.backend_name != "postgres":
        pytest.skip("Counts physical PostgreSQL connections")
    psycopg = pytest.importorskip("psycopg")
    original_connect = psycopg.Connection.connect.__func__
    connections: list[Any] = []

    def connect(cls: Any, *args: Any, **kwargs: Any) -> Any:
        connection = original_connect(cls, *args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(connect))
    monkeypatch.setattr(psycopg, "connect", psycopg.Connection.connect)
    return connections


@pytest.mark.parametrize("history", ["absent", "task", "pipeline", "future", "monitor"])
def test_control_observation_reuses_physical_connections(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    history: str,
) -> None:
    """Repeated real status reads open no physical connections after warmup."""
    ctx = weft_harness.context
    connections = _track_postgres_connections(ctx, monkeypatch)
    turns = 0
    warm_connections = 0
    repeated: list[int] = []
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as writer:
        tid = str(writer.generate_timestamp())
        if history == "future":
            tid = str(int(tid) + 1_000_000_000_000_000)
        spec = TaskSpec.model_validate(
            {
                "tid": tid,
                "name": "control-observation",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "persistent": True,
                },
                "io": {},
                "state": {},
            }
        )
        if history == "pipeline":
            spec.metadata["role"] = "pipeline"
        event = {
            "tid": tid,
            "event": "work_started",
            "status": "running",
            "taskspec": spec.model_dump(mode="json"),
        }
        if history not in {"absent", "monitor"}:
            writer.write(json.dumps(event))
        if history == "monitor":
            store = open_monitor_store(ctx, queue=writer)
            store.ensure_schema()
            update = update_from_task_log_payload(
                event, message_id=writer.generate_timestamp()
            )
            assert update is not None
            store.upsert_task_event(update)
        if history == "pipeline":
            with ctx.queue(f"P{tid}.status", persistent=True) as pipeline:
                pipeline.write(
                    json.dumps(
                        {
                            "type": "pipeline_status",
                            "pipeline_tid": tid,
                            "status": "running",
                            "timestamp": pipeline.generate_timestamp(),
                        }
                    )
                )
        with ctx.queue(f"T{tid}.ctrl_out", persistent=True) as terminal_writer:

            def wait(resources: Any, timeout: float | None) -> bool:
                nonlocal turns, warm_connections
                turns += 1
                if turns == 1:
                    warm_connections = len(connections)
                else:
                    repeated.append(len(connections) - warm_connections)
                if turns == 4:
                    terminal_writer.write(
                        json.dumps(
                            {
                                "type": "terminal",
                                "source": "task",
                                "tid": tid,
                                "status": "cancelled",
                            }
                        )
                    )
                return True

            monkeypatch.setattr(task_cmd._ControlSurfaceResources, "wait", wait)
            _entry, snapshot = task_cmd._await_control_surface(ctx, tid)
            assert snapshot is not None
            assert snapshot.status == "cancelled"
            assert turns == 4
    assert repeated == [0, 0, 0]
    assert connections
    assert all(connection.closed for connection in connections)


def test_control_observation_sees_external_metadata_state_and_route_changes(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Independent commits stay visible across polls and custom route changes."""
    ctx = weft_harness.context
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as writer:
        tid = str(writer.generate_timestamp())
    custom_ctrl = f"custom.{tid}.ctrl_out"
    custom_pipeline = f"custom.{tid}.status"
    payload = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "initial",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "persistent": True,
            },
            "io": {},
            "state": {},
        }
    ).model_dump(mode="json")

    def publish(turn: int) -> None:
        # This worker owns distinct connections, never the observer's queue lease.
        with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
            if turn < 3:
                payload["name"] = f"external-{turn}"
                payload["metadata"]["revision"] = turn
                if turn == 2:
                    payload["metadata"]["role"] = "pipeline"
                    payload["metadata"][PIPELINE_RUNTIME_METADATA_KEY] = {
                        "queues": {"status": custom_pipeline}
                    }
                    payload["io"]["control"]["ctrl_out"] = custom_ctrl
                log.write(
                    json.dumps(
                        {
                            "tid": tid,
                            "event": "work_started",
                            "status": "running",
                            "taskspec": payload,
                        }
                    )
                )
                with ctx.queue(task_state_queue_name(tid), persistent=True) as state:
                    state.write(
                        json.dumps(
                            {
                                "full": tid,
                                "short": tid_short_form(tid),
                                "revision": turn,
                            }
                        )
                    )
                    state.write("{malformed newest row")
                if turn == 2:
                    with ctx.queue(custom_pipeline, persistent=True) as pipeline:
                        pipeline.write(
                            json.dumps(
                                {
                                    "type": "pipeline_status",
                                    "pipeline_tid": tid,
                                    "status": "running",
                                    "activity": "external-progress",
                                    "timestamp": pipeline.generate_timestamp(),
                                }
                            )
                        )
            else:
                with ctx.queue(custom_ctrl, persistent=True) as ctrl:
                    ctrl.write(
                        json.dumps({"command": "KILL", "status": "ack", "tid": tid})
                    )
                    ctrl.write(
                        json.dumps(
                            {
                                "type": "terminal",
                                "source": "task",
                                "tid": tid,
                                "status": "cancelled",
                            }
                        )
                    )

    observed: list[task_cmd.system_cmd.TaskSnapshot | None] = []
    original_status = task_cmd._task_status

    def status(*args: Any, **kwargs: Any) -> Any:
        snapshot = original_status(*args, **kwargs)
        observed.append(snapshot)
        return snapshot

    turns = 0
    with ThreadPoolExecutor(max_workers=1) as producer:

        def wait(resources: Any, timeout: float | None) -> bool:
            nonlocal turns
            turns += 1
            assert turns <= 3
            producer.submit(publish, turns).result(timeout=10)
            return True

        monkeypatch.setattr(task_cmd, "_task_status", status)
        monkeypatch.setattr(task_cmd._ControlSurfaceResources, "wait", wait)
        entry, snapshot = task_cmd._await_control_surface(ctx, tid)
    assert entry is not None and entry["revision"] == 2
    assert snapshot is not None and snapshot.status == "cancelled"
    assert snapshot.name == "external-2"
    assert snapshot.metadata["revision"] == 2
    assert len(observed) == 3
    assert observed[0] is None
    assert observed[1] is not None and observed[1].name == "external-1"
    assert observed[2] is not None and observed[2].activity == "external-progress"


@pytest.mark.parametrize(
    "failure",
    [
        "none",
        "queue-2",
        "queue-3",
        "queue-4",
        "monitor-init",
        "monitor-close",
        "queue-close",
    ],
)
def test_control_resources_close_watcher_before_all_queues(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Partial acquisition and failed cleanup still release every owned lease."""
    ctx = weft_harness.context
    opened: list[Queue] = []
    closed: list[str] = []
    original_queue = WeftContext.queue
    original_monitor = task_cmd.QueueChangeMonitor

    def queue(self: WeftContext, name: str, *, persistent: bool = False) -> Queue:
        if failure == f"queue-{len(opened) + 1}":
            raise RuntimeError("injected acquisition")
        result = original_queue(self, name, persistent=persistent)
        original_close = result.close

        def close() -> None:
            closed.append(name)
            original_close()
            if failure == "queue-close" and name == "test.pipeline.status":
                raise RuntimeError("injected queue close")

        monkeypatch.setattr(result, "close", close)
        opened.append(result)
        return result

    def monitor(*args: Any, **kwargs: Any) -> QueueChangeMonitor:
        if failure == "monitor-init":
            raise RuntimeError("injected monitor construction")
        result = original_monitor(*args, **kwargs)
        original_close = result.close

        def close() -> None:
            closed.append("monitor")
            original_close()
            if failure == "monitor-close":
                raise RuntimeError("injected monitor close")

        monkeypatch.setattr(result, "close", close)
        return result

    monkeypatch.setattr(WeftContext, "queue", queue)
    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", monitor)

    if failure == "none":
        _close_control_resources_twice(ctx)
    else:
        with pytest.raises(RuntimeError, match="injected"):
            _close_control_resources_twice(ctx)
    expected = (
        ["monitor"] if failure in {"none", "monitor-close", "queue-close"} else []
    ) + [q.name for q in reversed(opened)]
    assert closed == expected


def _close_control_resources_twice(ctx: WeftContext) -> None:
    resources = task_cmd._ControlSurfaceResources(
        ctx,
        state_queue_name=task_state_queue_name("1789010695101849600"),
        ctrl_out_name="test.ctrl_out",
        pipeline_status_name="test.pipeline.status",
    )
    try:
        resources.close()
    finally:
        resources.close()


def test_terminal_snapshot_reuses_connections_and_observes_external_route(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queue wakes stay fresh and release their real PG connections."""
    ctx = weft_harness.context
    connections = _track_postgres_connections(ctx, monkeypatch)
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as writer:
        tid = str(writer.generate_timestamp())
    custom_ctrl = f"custom.{tid}.terminal"
    polls = 0
    warm_connections = 0
    repeated: list[int] = []

    def publish() -> None:
        payload = TaskSpec.model_validate(
            {
                "tid": tid,
                "name": "late-terminal",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "persistent": True,
                },
                "io": {"control": {"ctrl_out": custom_ctrl}},
                "state": {},
            }
        ).model_dump(mode="json")
        with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
            log.write(
                json.dumps(
                    {
                        "tid": tid,
                        "event": "work_started",
                        "status": "running",
                        "taskspec": payload,
                    }
                )
            )
        with ctx.queue(custom_ctrl, persistent=True) as ctrl:
            ctrl.write(
                json.dumps(
                    {
                        "type": "terminal",
                        "source": "task",
                        "tid": tid,
                        "status": "cancelled",
                    }
                )
            )

    with ThreadPoolExecutor(max_workers=1) as producer:

        def wake(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
            nonlocal polls, warm_connections
            polls += 1
            assert polls <= 4
            if polls == 1:
                warm_connections = len(connections)
            else:
                repeated.append(len(connections) - warm_connections)
            if polls == 4:
                producer.submit(publish).result(timeout=10)
            return True

        monkeypatch.setattr(QueueChangeMonitor, "wait", wake)
        snapshot = task_cmd.task_terminal_snapshot(
            tid, context=ctx, timeout=task_cmd.CONTROL_SURFACE_WAIT_TIMEOUT
        )
    assert snapshot.status == "cancelled"
    assert snapshot.source == "ctrl_out"
    assert len(snapshot.ack_targets) == 1
    assert snapshot.ack_targets[0].queue == custom_ctrl
    assert polls == 4
    assert repeated == [0, 0, 0]
    assert connections and all(connection.closed for connection in connections)
    with ctx.queue(custom_ctrl, persistent=True) as reader:
        assert reader.peek_one() is not None


@pytest.mark.parametrize("outcome", ["timeout", "read-error", "wait-error"])
def test_terminal_snapshot_closes_owned_queue_on_all_exits(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Expiry and read/wait exceptions release the single observation lease."""
    ctx = weft_harness.context
    original_queue = WeftContext.queue
    original_evidence = task_cmd.task_evidence.known_tid_evidence
    opened: list[Queue] = []
    closed: list[Queue] = []
    now = 0.0

    def queue(self: WeftContext, name: str, *, persistent: bool = False) -> Queue:
        result = original_queue(self, name, persistent=persistent)
        original_close = result.close

        def close() -> None:
            original_close()
            closed.append(result)

        monkeypatch.setattr(result, "close", close)
        opened.append(result)
        return result

    def evidence(*args: Any, **kwargs: Any) -> Any:
        result = original_evidence(*args, **kwargs)
        if outcome == "read-error":
            raise RuntimeError("injected read failure")
        return result

    def wait(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
        nonlocal now
        if outcome == "wait-error":
            raise RuntimeError("injected wait failure")
        now = task_cmd.CONTROL_SURFACE_WAIT_TIMEOUT + 1
        return False

    monkeypatch.setattr(WeftContext, "queue", queue)
    monkeypatch.setattr(task_cmd.task_evidence, "known_tid_evidence", evidence)
    monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
    monkeypatch.setattr(
        task_cmd,
        "time",
        SimpleNamespace(monotonic=lambda: now, time_ns=time.time_ns),
    )
    if outcome == "timeout":
        result = task_cmd.task_terminal_snapshot(
            "1789010695101849600",
            context=ctx,
            timeout=task_cmd.CONTROL_SURFACE_WAIT_TIMEOUT,
        )
        assert result.status == "unknown" and not result.terminal
    else:
        with pytest.raises(RuntimeError, match="injected"):
            task_cmd.task_terminal_snapshot(
                "1789010695101849600",
                context=ctx,
                timeout=task_cmd.CONTROL_SURFACE_WAIT_TIMEOUT,
            )
    assert {q.name for q in opened} == {
        WEFT_GLOBAL_LOG_QUEUE,
        task_state_queue_name("1789010695101849600"),
        "T1789010695101849600.outbox",
        "T1789010695101849600.ctrl_out",
    }
    assert len(closed) == len(opened)
    assert all(queue in closed for queue in opened)


def test_status_watch_reuses_connections_and_observes_external_updates(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each resumed public snapshot sees new commits without opening connections."""
    ctx = weft_harness.context
    connections = _track_postgres_connections(ctx, monkeypatch)
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as writer:
        tid = str(writer.generate_timestamp())
    payload = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "watched-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "persistent": True,
            },
            "io": {},
            "state": {},
        }
    ).model_dump(mode="json")

    def publish(revision: int) -> None:
        payload["metadata"]["revision"] = revision
        with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
            log.write(
                json.dumps(
                    {
                        "tid": tid,
                        "event": "work_started",
                        "status": "running",
                        "taskspec": payload,
                    }
                )
            )

    monkeypatch.setattr(QueueChangeMonitor, "wait", lambda *_args, **_kwargs: True)
    iterator = iter(
        task_cmd.watch_task_status(
            tid, context=ctx, timeout=task_cmd.CONTROL_SURFACE_WAIT_TIMEOUT
        )
    )
    repeated: list[int] = []
    with ThreadPoolExecutor(max_workers=1) as producer:
        try:
            for revision in range(4):
                producer.submit(publish, revision).result(timeout=10)
                before = len(connections)
                snapshot = next(iterator)
                assert snapshot.metadata["revision"] == revision
                assert snapshot.status == "running"
                if revision:
                    repeated.append(len(connections) - before)
        finally:
            close = getattr(iterator, "close", None)
            assert callable(close)
            close()
    assert repeated == [0, 0, 0]
    assert connections and all(connection.closed for connection in connections)


def _track_watch_resources(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> tuple[list[str], list[str]]:
    opened: list[str] = []
    closed: list[str] = []
    original_queue = WeftContext.queue

    class TrackedMonitor:
        def wait(self, timeout: float | None) -> bool:
            if failure == "wait":
                raise RuntimeError("injected wait failure")
            return False

        def close(self) -> None:
            closed.append("monitor")
            if failure == "monitor-close":
                raise RuntimeError("injected monitor close")

    def queue(self: WeftContext, name: str, *, persistent: bool = False) -> Queue:
        if failure == "state-construction" and opened:
            raise RuntimeError("injected state construction")
        result = original_queue(self, name, persistent=persistent)
        original_close = result.close

        def close() -> None:
            original_close()
            closed.append(name)

        monkeypatch.setattr(result, "close", close)
        opened.append(name)
        return result

    def monitor(*args: Any, **kwargs: Any) -> TrackedMonitor:
        if failure == "monitor-construction":
            raise RuntimeError("injected monitor construction")
        return TrackedMonitor()

    monkeypatch.setattr(WeftContext, "queue", queue)
    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", monitor)
    return opened, closed


@pytest.mark.parametrize("failure", ["state-construction", "monitor-construction"])
def test_status_watch_closes_partial_construction(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    opened, closed = _track_watch_resources(monkeypatch, failure)
    iterator = iter(
        task_cmd.watch_task_status("1789010695101849600", context=weft_harness.context)
    )
    with pytest.raises(RuntimeError, match="injected"):
        next(iterator)
    assert opened
    assert closed == list(reversed(opened))


@pytest.mark.parametrize(
    "outcome", ["close", "terminal", "timeout", "read", "wait", "monitor-close"]
)
def test_status_watch_closes_before_queues_on_each_exit(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    ctx = weft_harness.context
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        tid = str(log.generate_timestamp())
        status = "completed" if outcome == "terminal" else "running"
        spec = TaskSpec.model_validate(
            {
                "tid": tid,
                "name": "watch-exit",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "persistent": True,
                },
                "io": {},
                "state": {},
            }
        )
        log.write(
            json.dumps(
                {
                    "tid": tid,
                    "event": "work_completed"
                    if outcome == "terminal"
                    else "work_started",
                    "status": status,
                    "taskspec": spec.model_dump(mode="json"),
                }
            )
        )
    opened, closed = _track_watch_resources(monkeypatch, outcome)
    original_snapshot = task_cmd._task_snapshot

    def snapshot(*args: Any, **kwargs: Any) -> Any:
        result = original_snapshot(*args, **kwargs)
        if outcome == "read":
            raise RuntimeError("injected read failure")
        return result

    monkeypatch.setattr(task_cmd, "_task_snapshot", snapshot)
    iterator = iter(
        task_cmd.watch_task_status(
            tid, context=ctx, timeout=0 if outcome == "timeout" else None
        )
    )
    try:
        if outcome in {"read", "wait", "timeout", "monitor-close"}:
            expected = TimeoutError if outcome == "timeout" else RuntimeError
            with pytest.raises(expected):
                next(iterator)
                if outcome in {"wait", "timeout"}:
                    next(iterator)
                else:
                    close = getattr(iterator, "close", None)
                    assert callable(close)
                    close()
        else:
            assert next(iterator).status == status
            if outcome == "terminal":
                with pytest.raises(StopIteration):
                    next(iterator)
    finally:
        close = getattr(iterator, "close", None)
        assert callable(close)
        close()
    assert opened == [WEFT_GLOBAL_LOG_QUEUE, task_state_queue_name(tid)]
    assert closed == ["monitor", *reversed(opened)]


@pytest.mark.parametrize("log_wins", [False, True])
def test_interactive_stop_observer_borrows_log_queue(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    log_wins: bool,
) -> None:
    """The active STOP owner borrows Monitor SQL and selects real terminal proof."""
    ctx = weft_harness.context
    connections = _track_postgres_connections(ctx, monkeypatch)
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        tid = str(log.generate_timestamp())
        store = open_monitor_store(ctx, queue=log)
        store.ensure_schema()
    spec = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "interactive-loan",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "cleanup_on_exit": False,
                "enable_process_title": False,
            },
        }
    )
    consumer = Consumer(ctx.broker_target, spec, config=ctx.broker_config)
    with consumer.drive_scope():
        consumer._handle_stop_request(
            reason="STOP requested",
            event="control_stop",
            message_id=None,
            apply_reserved_policy=True,
        )
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        store = open_monitor_store(ctx, queue=log)
        for raw, message_id in list(log.peek_generator(with_timestamps=True)):
            payload = json.loads(raw)
            if payload.get("tid") != tid:
                continue
            update = update_from_task_log_payload(payload, message_id=message_id)
            assert update is not None
            store.upsert_task_event(update)
            if not log_wins and payload.get("status") == "cancelled":
                assert log.delete(message_id=message_id)
    # Retire the producer's ephemeral control surface in both source cases.
    # This isolates retained history versus retained Monitor terminal proof.
    names = [task_state_queue_name(tid), spec.io.control["ctrl_out"]]
    for name in names:
        with ctx.queue(name) as queue:
            for _body, message_id in list(queue.peek_generator(with_timestamps=True)):
                assert queue.delete(message_id=message_id)
    borrowed: list[int] = []
    sources: list[str] = []
    original_lookup = run_cmd.read_monitor_task_record
    original_snapshot = task_cmd._terminal_observation_snapshot

    def lookup(*args: Any, **kwargs: Any) -> Any:
        queue = kwargs.get("queue")
        assert queue is not None and queue.name == WEFT_GLOBAL_LOG_QUEUE
        before = len(connections)
        record = original_lookup(*args, **kwargs)
        borrowed.append(len(connections) - before)
        return record

    def snapshot(*args: Any, **kwargs: Any) -> Any:
        value = original_snapshot(*args, **kwargs)
        assert value.terminal and value.status == "cancelled"
        sources.append(value.source)
        return value

    monkeypatch.setattr(run_cmd, "read_monitor_task_record", lookup)
    monkeypatch.setattr(task_cmd, "_terminal_observation_snapshot", snapshot)
    session = run_cmd._LiveRunSession(
        RunExecutionResult(tid=tid), ctx, _accepted_taskspec=spec
    )
    try:
        run_cmd._stop_interactive_run_session(session)
    finally:
        session.close()
    assert borrowed == [0]
    assert sources == (["log_fallback"] if log_wins else ["monitor_store"])
    assert connections and all(connection.closed for connection in connections)


def test_control_observer_retains_shared_completion_facts_with_bounded_drain(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Control observation preserves every owner's completion and later arrivals."""
    ctx = weft_harness.context
    route = "test.shared.control-custody"
    specs: list[TaskSpec] = []
    with ctx.queue("test.control-custody.ids") as allocator:
        for persistent in (True, False):
            specs.append(
                TaskSpec.model_validate(
                    {
                        "tid": str(allocator.generate_timestamp()),
                        "name": "control-custody",
                        "spec": {
                            "type": "function",
                            "function_target": (
                                "tests.tasks.sample_targets:echo_payload"
                                if persistent
                                else "tests.tasks.sample_targets:fail_payload"
                            ),
                            "persistent": persistent,
                            "cleanup_on_exit": False,
                            "enable_process_title": False,
                        },
                        "io": {"control": {"ctrl_out": route}},
                    }
                )
            )
    tasks = [Consumer(ctx.broker_target, spec, config=ctx.config) for spec in specs]
    try:
        assert tasks[0].run_work_item("batch") == "batch"
        with pytest.raises(RuntimeError, match="intentional failure"):
            tasks[1].run_work_item("fail")
        with ctx.queue(route) as control:
            facts = list(control.peek_generator(with_timestamps=True))
            assert len(facts) == 2
            assert {json.loads(raw)["event"] for raw, _ in facts} == {
                "work_item_completed",
                "work_failed",
            }
            control.write(json.dumps({"command": "KILL", "status": "ack"}))
            control.write(
                json.dumps({"type": "stream", "stream": "stderr", "data": "quiet"})
            )
            with control.get_connection() as broker:
                broker_type = type(broker)
            original_peek = broker_type.peek_generator
            late: list[tuple[str, int]] = []

            def peek(db: Any, queue_name: str, **kwargs: Any) -> Any:
                if queue_name == route and not late:
                    raw = json.dumps({"command": "STOP", "status": "ack"})
                    late.append((raw, control.write(raw)))
                return original_peek(db, queue_name, **kwargs)

            monkeypatch.setattr(broker_type, "peek_generator", peek)
            capsys.readouterr()
            observation = task_cmd._observe_control_envelopes(
                control,
                tid=tasks[0].tid,
                taskspec_payload=specs[0].model_dump(mode="json"),
            )
            assert capsys.readouterr() == ("", "")
            assert observation.terminal_snapshot is None
            assert observation.kill_ack_observed_at is not None
            assert list(control.peek_generator(with_timestamps=True)) == facts + late
            assert len(late) == 1
    finally:
        for task in tasks:
            task.cleanup()
