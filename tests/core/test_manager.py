"""Tests for Manager functionality."""

from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path

import pytest

from weft._constants import (
    CONTROL_STOP,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_CTRL_IN_QUEUE,
    WEFT_MANAGER_CTRL_OUT_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
    load_config,
)
from weft.core.manager import ManagedChild, Manager
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


def drain(queue):
    items = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        items.append(value)
    return items


def make_manager_spec(
    tid: str,
    inbox: str = WEFT_SPAWN_REQUESTS_QUEUE,
    ctrl_in: str = WEFT_MANAGER_CTRL_IN_QUEUE,
    ctrl_out: str = WEFT_MANAGER_CTRL_OUT_QUEUE,
    *,
    idle_timeout: float | None = None,
) -> TaskSpec:
    metadata = {"capabilities": ["tests.tasks.sample_targets:large_output"]}
    if idle_timeout is not None:
        metadata["idle_timeout"] = idle_timeout
    return TaskSpec(
        tid=tid,
        name="manager",
        spec=SpecSection(
            type="function",
            function_target="weft.core.manager:Manager",
            timeout=None,
        ),
        io=IOSection(
            inputs={"inbox": inbox},
            outputs={"outbox": WEFT_MANAGER_OUTBOX_QUEUE},
            control={
                "ctrl_in": ctrl_in,
                "ctrl_out": ctrl_out,
            },
        ),
        state=StateSection(),
        metadata=metadata,
    )


def make_child_spec(size: int = 2 * 1024 * 1024) -> dict[str, object]:
    return {
        "name": "child",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:large_output",
            "output_size_limit_mb": 1,
        },
        "inbox_message": {"kwargs": {"size": size}},
    }


@pytest.fixture
def manager_setup(broker_env, unique_tid):
    db_path, make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(unique_tid, inbox, ctrl_in, ctrl_out)
    manager = Manager(db_path, spec)
    yield manager, make_queue
    manager.stop(join=False)
    manager.cleanup()


def wait_for_children(manager: Manager, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while manager._child_processes and time.time() < deadline:
        manager._cleanup_children()
        time.sleep(0.05)


def _process_running(pid: int) -> bool:
    psutil = pytest.importorskip("psutil")
    try:
        process = psutil.Process(pid)
    except psutil.Error:
        return False
    return process.is_running() and process.status() != psutil.STATUS_ZOMBIE


def test_manager_spawns_child(manager_setup) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    registry_queue = make_queue(WEFT_WORKERS_REGISTRY_QUEUE)
    drain(log_queue)
    drain(registry_queue)

    inbox_queue.write(json.dumps(make_child_spec()))

    manager.process_once()
    wait_for_children(manager)

    # gather log events to find child tid
    events = [json.loads(item) for item in drain(log_queue)]
    spawn_events = [e for e in events if e["event"] == "task_spawned"]
    assert spawn_events, "Expected task_spawned event"
    spawn_event = spawn_events[-1]
    child_tid = spawn_event["child_tid"]
    child_taskspec = spawn_event["child_taskspec"]
    outbox_name = child_taskspec["io"]["outputs"].get("outbox", f"T{child_tid}.outbox")
    result_queue = make_queue(outbox_name)
    raw_reference = None
    deadline = time.time() + 5.0
    while time.time() < deadline:
        raw_reference = result_queue.read_one()
        if raw_reference is not None:
            break
        time.sleep(0.05)

    if raw_reference is None:
        child_events = [e for e in events if e.get("tid") == child_tid]
        pytest.fail(f"No output message; child events: {child_events}")

    reference = json.loads(raw_reference)
    assert reference["type"] == "large_output"


def test_manager_registry_entries(manager_setup) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_WORKERS_REGISTRY_QUEUE)
    entries = [json.loads(item) for item in drain(registry_queue)]
    relevant = [entry for entry in entries if entry.get("tid") == manager.tid]
    assert len(relevant) == 1
    assert relevant[0]["status"] == "active"
    manager.cleanup()
    entries = [json.loads(item) for item in drain(registry_queue)]
    relevant = [entry for entry in entries if entry.get("tid") == manager.tid]
    assert len(relevant) == 1
    assert relevant[0]["status"] == "stopped"


def test_manager_cleanup_sends_stop_to_children(manager_setup) -> None:
    pytest.importorskip("psutil")
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    inbox_queue.write(
        json.dumps(
            {
                "name": "long-running",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                    "keyword_args": {"duration": 5.0},
                },
            }
        )
    )

    manager.process_once()
    start = time.time()
    while not manager._child_processes and time.time() - start < 5.0:
        manager.process_once()
        time.sleep(0.05)

    assert manager._child_processes, "child process should be running"
    child_tid, child_info = next(iter(manager._child_processes.items()))
    ctrl_queue = make_queue(child_info.ctrl_queue or f"T{child_tid}.ctrl_in")
    assert child_info.process.is_alive()

    manager.cleanup()

    messages: list[str] = []
    while True:
        raw = ctrl_queue.read_one()
        if raw is None:
            break
        messages.append(raw)

    assert messages == [] or any(message == CONTROL_STOP for message in messages)
    assert not _process_running(child_info.process.pid)


def test_manager_cleanup_terminates_worker_descendants(manager_setup) -> None:
    psutil = pytest.importorskip("psutil")
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])

    inbox_queue.write(
        json.dumps(
            {
                "name": "long-running",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                    "keyword_args": {"duration": 5.0},
                },
            }
        )
    )

    start = time.time()
    while not manager._child_processes and time.time() - start < 5.0:
        manager.process_once()
        time.sleep(0.05)

    assert manager._child_processes, "child process should be running"
    child_tid, child_info = next(iter(manager._child_processes.items()))
    worker_pid: int | None = None
    deadline = time.time() + 5.0
    while time.time() < deadline and worker_pid is None:
        try:
            child_process = psutil.Process(child_info.process.pid)
        except psutil.Error:
            break
        descendants = child_process.children(recursive=True)
        if descendants:
            worker_pid = descendants[0].pid
            break
        time.sleep(0.05)

    assert worker_pid is not None, f"expected worker descendant for {child_tid}"

    manager.cleanup()

    deadline = time.time() + 5.0
    while time.time() < deadline:
        root_alive = _process_running(child_info.process.pid)
        worker_alive = psutil.pid_exists(worker_pid)
        if not root_alive and not worker_alive:
            break
        time.sleep(0.05)

    assert not _process_running(child_info.process.pid)
    assert not psutil.pid_exists(worker_pid)


def test_manager_stop_command_drains_nonpersistent_children(manager_setup) -> None:
    pytest.importorskip("psutil")
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    ctrl_in_queue = make_queue(manager._queue_names["ctrl_in"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    inbox_queue.write(
        json.dumps(
            {
                "name": "long-running",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                    "keyword_args": {"duration": 5.0},
                },
            }
        )
    )

    start = time.time()
    while not manager._child_processes and time.time() - start < 5.0:
        manager.process_once()
        time.sleep(0.05)

    assert manager._child_processes, "child process should be running"
    _child_tid, child_info = next(iter(manager._child_processes.items()))
    assert child_info.process.is_alive()

    ctrl_in_queue.write(CONTROL_STOP)

    deadline = time.time() + 2.0
    while time.time() < deadline and not manager.should_stop:
        manager.process_once()
        time.sleep(0.05)

    assert manager.should_stop
    assert manager.taskspec.state.status == "cancelled"
    assert not _process_running(child_info.process.pid)

    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "control_stop" for event in events)
    assert any(event.get("event") == "manager_stop_drained" for event in events)


def test_manager_stop_command_does_not_launch_new_children_after_stop(
    manager_setup,
) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    ctrl_in_queue = make_queue(manager._queue_names["ctrl_in"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    payload = {
        "name": "queued-stop",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:simulate_work",
            "keyword_args": {"duration": 0.5, "result": "ok"},
        },
    }
    inbox_queue.write(json.dumps(payload))
    inbox_queue.write(json.dumps(payload))

    manager.process_once()
    assert len(manager._child_processes) == 1

    ctrl_in_queue.write(CONTROL_STOP)

    deadline = time.time() + 3.0
    max_children_seen = len(manager._child_processes)
    while time.time() < deadline and not manager.should_stop:
        manager.process_once()
        max_children_seen = max(max_children_seen, len(manager._child_processes))
        time.sleep(0.05)

    assert manager.should_stop is True
    assert max_children_seen == 1

    events = [json.loads(item) for item in drain(log_queue)]
    spawn_events = [event for event in events if event.get("event") == "task_spawned"]
    assert len(spawn_events) == 1
    assert any(event.get("event") == "control_stop" for event in events)


def test_manager_drain_reissues_stop_for_child_added_after_stop(
    manager_setup,
) -> None:
    manager, make_queue = manager_setup
    ctrl_queue_name = "manager.late-child.ctrl_in"
    ctrl_queue = make_queue(ctrl_queue_name)

    class FakeProcess:
        pid = 424245
        exitcode = None

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

    manager._begin_graceful_shutdown(message_id=None)
    manager._child_processes["late-child"] = ManagedChild(
        process=FakeProcess(),
        ctrl_queue=ctrl_queue_name,
        persistent=False,
    )

    manager.process_once()

    assert ctrl_queue.read_one() == CONTROL_STOP


def test_manager_stop_mid_handler_keeps_reserved_work_unlaunched(
    manager_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    ctrl_in_queue = make_queue(manager._queue_names["ctrl_in"])
    reserved_queue = make_queue(manager._queue_names["reserved"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    payload = {
        "name": "queued-stop",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:simulate_work",
            "keyword_args": {"duration": 0.5, "result": "ok"},
        },
    }
    inbox_queue.write(json.dumps(payload))

    original_build_child_spec = manager._build_child_spec

    def inject_stop(payload: dict[str, object], timestamp: int) -> TaskSpec | None:
        child_spec = original_build_child_spec(payload, timestamp)
        ctrl_in_queue.write(CONTROL_STOP)
        return child_spec

    monkeypatch.setattr(manager, "_build_child_spec", inject_stop)

    manager.process_once()

    assert manager._child_processes == {}
    assert reserved_queue.peek_one() is not None
    assert manager.taskspec.state.status == "cancelled"

    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "control_stop" for event in events)
    assert not any(event.get("event") == "task_spawned" for event in events)


def test_manager_leadership_yield_drains_nonpersistent_children(
    manager_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, make_queue = manager_setup
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    class FakeProcess:
        pid = 424243
        exitcode = None

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

    manager._child_processes["child"] = ManagedChild(
        process=FakeProcess(),
        ctrl_queue=None,
        persistent=False,
    )

    lower_leader_tid = str(int(manager.tid) - 1)
    unregister_calls: list[bool] = []

    monkeypatch.setattr(manager, "_leader_tid", lambda: lower_leader_tid)
    monkeypatch.setattr(manager, "_unregister_worker", lambda: unregister_calls.append(True))

    yielded = manager._maybe_yield_leadership(force=True)

    assert yielded is True
    assert manager._draining is True
    assert manager.should_stop is False
    assert unregister_calls == [True]
    assert manager.taskspec.state.status == "running"

    events = [json.loads(item) for item in drain(log_queue)]
    yield_events = [event for event in events if event.get("event") == "manager_leadership_yielded"]
    assert len(yield_events) == 1
    assert yield_events[0]["leader_tid"] == lower_leader_tid
    assert yield_events[0]["draining"] is True
    assert yield_events[0]["status"] == "running"

    manager._child_processes.clear()
    manager.process_once()

    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"

    events = [json.loads(item) for item in drain(log_queue)]
    drained_events = [event for event in events if event.get("event") == "manager_leadership_drained"]
    assert len(drained_events) == 1
    assert drained_events[0]["status"] == "cancelled"


def test_manager_leadership_yield_waits_while_persistent_children_exist(
    manager_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _make_queue = manager_setup

    class FakeProcess:
        pid = 424244
        exitcode = None

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            return None

    manager._child_processes["child"] = ManagedChild(
        process=FakeProcess(),
        ctrl_queue=None,
        persistent=True,
    )

    lower_leader_tid = str(int(manager.tid) - 1)
    monkeypatch.setattr(manager, "_leader_tid", lambda: lower_leader_tid)

    yielded = manager._maybe_yield_leadership(force=True)

    assert yielded is False
    assert manager._draining is False
    assert manager.should_stop is False
    assert manager.taskspec.state.status == "running"


def test_cleanup_children_reaps_os_dead_child_without_mapping_scan(
    manager_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _make_queue = manager_setup

    class FakeProcess:
        pid = 424242
        exitcode = None

        def __init__(self) -> None:
            self.join_calls: list[float] = []

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(0.0 if timeout is None else float(timeout))

    fake_process = FakeProcess()
    manager._child_processes["child"] = ManagedChild(
        process=fake_process,
        ctrl_queue=None,
        persistent=False,
    )

    monkeypatch.setattr(manager, "_pid_alive", lambda pid: False)

    def _unexpected_mapping_scan(_tid: str) -> set[int]:
        raise AssertionError("normal dead-child cleanup should not scan tid mappings")

    monkeypatch.setattr(manager, "_managed_pids_for_child", _unexpected_mapping_scan)

    manager._cleanup_children()

    assert manager._child_processes == {}
    assert fake_process.join_calls == [0.0, 0.1]


def test_manager_autostart_templates(tmp_path: Path, broker_env, unique_tid) -> None:
    db_path, make_queue = broker_env

    autostart_dir = tmp_path / "autostart"
    autostart_dir.mkdir()
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "simulate.json").write_text(
        json.dumps(
            {
                "name": "autostart-simulate",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                },
                "metadata": {"tags": ["autostart"]},
            }
        ),
        encoding="utf-8",
    )
    template_path = autostart_dir / "watcher.json"
    template_path.write_text(
        json.dumps(
            {
                "name": "autostart-simulate",
                "target": {"type": "task", "name": "simulate"},
                "policy": {"mode": "once"},
                "defaults": {"keyword_args": {"duration": 0.2}},
            }
        ),
        encoding="utf-8",
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(unique_tid, inbox, ctrl_in, ctrl_out)

    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    try:
        start = time.time()
        while not manager._child_processes and time.time() - start < 5.0:
            manager.process_once()
            time.sleep(0.05)

        assert manager._child_processes, "expected autostart child"
        child_info = next(iter(manager._child_processes.values()))
        assert child_info.autostart_source == str(template_path.resolve())

        events = [json.loads(item) for item in drain(log_queue)]
        assert any(
            event.get("autostart_source") == str(template_path.resolve())
            for event in events
        ), "autostart launch should be logged"
    finally:
        manager.cleanup()


def test_manager_idle_shutdown(broker_env, unique_tid) -> None:
    db_path, make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(
        unique_tid,
        inbox,
        ctrl_in,
        ctrl_out,
        idle_timeout=0.2,
    )
    manager = Manager(db_path, spec)
    try:
        start = time.time()
        while not manager.should_stop and time.time() - start < 2.0:
            manager.process_once()
            time.sleep(0.05)
        assert manager.should_stop is True
        assert manager.taskspec.state.status == "completed"
    finally:
        manager.cleanup()


def test_manager_overrides_supplied_tid(manager_setup, unique_tid) -> None:
    manager, make_queue = manager_setup
    inbox_queue = make_queue(manager._queue_names["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)

    supplied_tid = unique_tid
    child_spec = {
        "tid": supplied_tid,
        "name": "child-explicit",
        "version": "1.0",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:provide_payload",
        },
        "io": {
            "inputs": {"inbox": f"T{supplied_tid}.inbox"},
            "outputs": {"outbox": f"T{supplied_tid}.outbox"},
            "control": {
                "ctrl_in": f"T{supplied_tid}.ctrl_in",
                "ctrl_out": f"T{supplied_tid}.ctrl_out",
            },
        },
        "state": {},
        "metadata": {},
    }

    inbox_queue.write(json.dumps({"taskspec": child_spec}))
    message_id = getattr(inbox_queue, "last_ts", None)
    assert isinstance(message_id, int)

    manager.process_once()
    wait_for_children(manager)

    events = [json.loads(item) for item in drain(log_queue)]
    spawn_events = [e for e in events if e["event"] == "task_spawned"]
    assert spawn_events, "Expected spawn event"
    assert spawn_events[-1]["child_tid"] == str(message_id)
    assert spawn_events[-1]["child_taskspec"]["tid"] == str(message_id)


def test_manager_idle_timeout_waits_for_active_child_to_finish(
    broker_env, unique_tid
) -> None:
    db_path, make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(
        unique_tid,
        inbox,
        ctrl_in,
        ctrl_out,
        idle_timeout=0.2,
    )
    manager = Manager(db_path, spec)
    try:
        inbox_queue = make_queue(manager._queue_names["inbox"])
        inbox_queue.write(
            json.dumps(
                {
                        "spec": {
                            "type": "function",
                            "function_target": "tests.tasks.sample_targets:simulate_work",
                            "keyword_args": {"duration": 0.5},
                        },
                    }
                )
            )

        start = time.time()
        while not manager._child_processes and time.time() - start < 2.0:
            manager.process_once()
            time.sleep(0.05)
        assert manager._child_processes

        start = time.time()
        while time.time() - start < 0.35:
            manager.process_once()
            time.sleep(0.05)
        assert manager.should_stop is False
        assert manager._child_processes

        start = time.time()
        while manager._child_processes and time.time() - start < 3.0:
            manager.process_once()
            time.sleep(0.05)
        assert not manager._child_processes

        start = time.time()
        while not manager.should_stop and time.time() - start < 2.0:
            manager.process_once()
            time.sleep(0.05)
        assert manager.should_stop is True
    finally:
        manager.cleanup()


def test_manager_idle_timeout_does_not_kill_persistent_child(
    broker_env, unique_tid
) -> None:
    db_path, make_queue = broker_env
    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(
        unique_tid,
        inbox,
        ctrl_in,
        ctrl_out,
        idle_timeout=0.2,
    )
    manager = Manager(db_path, spec)
    try:
        inbox_queue = make_queue(manager._queue_names["inbox"])
        child_tid = str(int(unique_tid) + 1)
        inbox_queue.write(
            json.dumps(
                {
                    "taskspec": {
                        "tid": child_tid,
                        "name": "persistent-child",
                        "version": "1.0",
                        "spec": {
                            "type": "function",
                            "persistent": True,
                            "function_target": "tests.tasks.sample_targets:echo_payload",
                        },
                        "io": {
                            "inputs": {"inbox": f"T{child_tid}.inbox"},
                            "outputs": {"outbox": f"T{child_tid}.outbox"},
                            "control": {
                                "ctrl_in": f"T{child_tid}.ctrl_in",
                                "ctrl_out": f"T{child_tid}.ctrl_out",
                            },
                        },
                        "state": {},
                        "metadata": {},
                    },
                    "inbox_message": None,
                }
            )
        )

        start = time.time()
        while not manager._child_processes and time.time() - start < 2.0:
            manager.process_once()
            time.sleep(0.05)
        assert manager._child_processes

        time.sleep(0.4)
        for _ in range(5):
            manager.process_once()
            time.sleep(0.05)

        assert manager.should_stop is False
        assert manager._child_processes
    finally:
        manager.cleanup()


def test_manager_autostart_skips_active_templates(
    tmp_path: Path, broker_env, unique_tid
) -> None:
    db_path, make_queue = broker_env

    autostart_dir = tmp_path / "autostart"
    autostart_dir.mkdir()
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "observer.json").write_text(
        json.dumps(
            {
                "name": "autostart-observer",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                },
            }
        ),
        encoding="utf-8",
    )
    template_path = autostart_dir / "observer.json"
    template_path.write_text(
        json.dumps(
            {
                "name": "autostart-observer",
                "target": {"type": "task", "name": "observer"},
                "policy": {"mode": "once"},
                "defaults": {"keyword_args": {"duration": 0.1}},
            }
        ),
        encoding="utf-8",
    )

    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    log_queue.write(
        json.dumps(
            {
                "event": "task_spawned",
                "status": "running",
                "taskspec": {
                    "metadata": {
                        "autostart_source": str(template_path.resolve()),
                    }
                },
            }
        )
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    inbox = f"manager.{unique_tid}.inbox"
    ctrl_in = f"manager.{unique_tid}.ctrl_in"
    ctrl_out = f"manager.{unique_tid}.ctrl_out"
    spec = make_manager_spec(unique_tid, inbox, ctrl_in, ctrl_out)

    manager = Manager(db_path, spec, config=config)
    try:
        manager.process_once()
        assert not manager._child_processes
        assert not manager._autostart_launched
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_restarts(
    tmp_path: Path, broker_env, unique_tid
) -> None:
    db_path, make_queue = broker_env

    autostart_dir = tmp_path / "autostart"
    autostart_dir.mkdir()
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "restart.json").write_text(
        json.dumps(
            {
                "name": "autostart-restart",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                },
            }
        ),
        encoding="utf-8",
    )
    manifest_path = autostart_dir / "restart.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "autostart-restart",
                "target": {"type": "task", "name": "restart"},
                "policy": {"mode": "ensure", "max_restarts": 2, "backoff_seconds": 0},
                "defaults": {"keyword_args": {"duration": 0.0}},
            }
        ),
        encoding="utf-8",
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain(log_queue)
    try:
        spawn_events: list[dict[str, object]] = []
        deadline = time.time() + 8.0
        while len(spawn_events) < 2 and time.time() < deadline:
            manager.process_once()
            time.sleep(0.05)
            events = [json.loads(item) for item in drain(log_queue)]
            spawn_events.extend(
                event
                for event in events
                if event.get("event") == "task_spawned"
                and event.get("autostart_source") == str(manifest_path.resolve())
            )
        assert len(spawn_events) >= 2
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_restarts_after_child_exit_without_scan_wait(
    tmp_path: Path, broker_env, unique_tid
) -> None:
    db_path, make_queue = broker_env

    autostart_dir = tmp_path / "autostart"
    autostart_dir.mkdir()
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "restart.json").write_text(
        json.dumps(
            {
                "name": "autostart-restart",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                },
            }
        ),
        encoding="utf-8",
    )
    manifest_path = autostart_dir / "restart.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "autostart-restart",
                "target": {"type": "task", "name": "restart"},
                "policy": {"mode": "ensure", "max_restarts": 2, "backoff_seconds": 0},
                "defaults": {"keyword_args": {"duration": 0.0}},
            }
        ),
        encoding="utf-8",
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    try:
        ctx = multiprocessing.get_context("spawn")
        child = ctx.Process(target=time.sleep, args=(0.0,))
        child.start()
        child.join(timeout=2.0)
        assert child.is_alive() is False

        manager._child_processes["child"] = ManagedChild(
            process=child,
            ctrl_queue=None,
            persistent=False,
            autostart_source=str(manifest_path.resolve()),
        )
        manager._autostart_last_scan_ns = 123_456_789

        manager._cleanup_children()

        assert manager._child_processes == {}
        assert manager._autostart_last_scan_ns == 0
    finally:
        manager.cleanup()
