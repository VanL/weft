"""Tests for Manager functionality."""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import time
from pathlib import Path

import pytest

from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_STOP,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_CTRL_IN_QUEUE,
    WEFT_MANAGER_CTRL_OUT_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    load_config,
)
from weft.core.manager import ManagedChild, Manager
from weft.core.tasks import Consumer, PipelineEdgeTask, PipelineTask
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
    role: str | None = None,
    weft_context: str | None = None,
) -> TaskSpec:
    metadata = {"capabilities": ["tests.tasks.sample_targets:large_output"]}
    if idle_timeout is not None:
        metadata["idle_timeout"] = idle_timeout
    if role is not None:
        metadata["role"] = role
    return TaskSpec(
        tid=tid,
        name="manager",
        spec=SpecSection(
            type="function",
            function_target="weft.core.manager:Manager",
            timeout=None,
            weft_context=weft_context,
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


def write_autostart_fixture(
    root: Path,
    *,
    task_name: str,
    manifest_name: str,
    mode: str,
    max_restarts: int | None = None,
    backoff_seconds: float | None = None,
    duration: float = 0.0,
) -> tuple[Path, Path]:
    autostart_dir = root / "autostart"
    autostart_dir.mkdir()
    tasks_dir = root / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / f"{task_name}.json").write_text(
        json.dumps(
            {
                "name": task_name,
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:simulate_work",
                },
            }
        ),
        encoding="utf-8",
    )
    policy: dict[str, object] = {"mode": mode}
    if max_restarts is not None:
        policy["max_restarts"] = max_restarts
    if backoff_seconds is not None:
        policy["backoff_seconds"] = backoff_seconds
    manifest_path = autostart_dir / f"{manifest_name}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": manifest_name,
                "target": {"type": "task", "name": task_name},
                "policy": policy,
                "defaults": {"keyword_args": {"duration": duration}},
            }
        ),
        encoding="utf-8",
    )
    return autostart_dir, manifest_path


def test_manager_autostart_root_dir_uses_configured_weft_directory_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _ = broker_env
    monkeypatch.setenv("WEFT_DIRECTORY_NAME", ".engram")
    spec = make_manager_spec(unique_tid, weft_context=str(tmp_path / "project"))
    manager = Manager(db_path, spec, config=load_config())
    try:
        assert manager._autostart_root_dir() == (tmp_path / "project" / ".engram")
    finally:
        manager.stop(join=False)
        manager.cleanup()


def write_autostart_pipeline_fixture(
    root: Path,
    *,
    task_name: str,
    pipeline_name: str,
    manifest_name: str,
    mode: str,
    max_restarts: int | None = None,
    backoff_seconds: float | None = None,
    function_target: str = "tests.tasks.sample_targets:simulate_work",
    stage_defaults: dict[str, object] | None = None,
    manifest_input: object | None = None,
    task_bundle: bool = False,
    pipeline_bundle: bool = False,
) -> tuple[Path, Path]:
    autostart_dir = root / "autostart"
    autostart_dir.mkdir()
    tasks_dir = root / "tasks"
    tasks_dir.mkdir()
    pipelines_dir = root / "pipelines"
    pipelines_dir.mkdir()

    task_payload = {
        "name": task_name,
        "spec": {
            "type": "function",
            "function_target": function_target,
        },
    }
    if task_bundle:
        task_entry = tasks_dir / task_name / "taskspec.json"
        task_entry.parent.mkdir()
    else:
        task_entry = tasks_dir / f"{task_name}.json"
    task_entry.write_text(json.dumps(task_payload), encoding="utf-8")

    pipeline_payload = {
        "name": pipeline_name,
        "stages": [
            {
                "name": "stage-one",
                "task": task_name,
                **({"defaults": stage_defaults} if stage_defaults is not None else {}),
            }
        ],
    }
    if pipeline_bundle:
        pipeline_entry = pipelines_dir / pipeline_name / "pipeline.json"
        pipeline_entry.parent.mkdir()
    else:
        pipeline_entry = pipelines_dir / f"{pipeline_name}.json"
    pipeline_entry.write_text(json.dumps(pipeline_payload), encoding="utf-8")

    policy: dict[str, object] = {"mode": mode}
    if max_restarts is not None:
        policy["max_restarts"] = max_restarts
    if backoff_seconds is not None:
        policy["backoff_seconds"] = backoff_seconds

    manifest_payload: dict[str, object] = {
        "name": manifest_name,
        "target": {"type": "pipeline", "name": pipeline_name},
        "policy": policy,
    }
    if manifest_input is not None:
        manifest_payload["defaults"] = {"input": manifest_input}

    manifest_path = autostart_dir / f"{manifest_name}.json"
    manifest_path.write_text(
        json.dumps(manifest_payload),
        encoding="utf-8",
    )
    return autostart_dir, manifest_path


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
    registry_queue = make_queue(WEFT_MANAGERS_REGISTRY_QUEUE)
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


def test_manager_launches_consumer_when_no_internal_task_class_is_set(
    manager_setup,
) -> None:
    manager, _make_queue = manager_setup
    child_spec = manager._build_child_spec(make_child_spec(), int(time.time_ns()))
    assert child_spec is not None

    assert manager._resolve_child_task_class(child_spec) is Consumer


def test_manager_launches_pipeline_task_for_reserved_internal_class(
    manager_setup,
) -> None:
    manager, _make_queue = manager_setup
    child_spec = TaskSpec(
        tid=str(time.time_ns()),
        name="pipeline-child",
        spec=SpecSection(
            type="function",
            function_target="weft.core.tasks.pipeline:runtime",
        ),
        io=IOSection(
            inputs={"inbox": "P123.inbox"},
            outputs={"outbox": "P123.outbox"},
            control={"ctrl_in": "P123.ctrl_in", "ctrl_out": "P123.ctrl_out"},
        ),
        state=StateSection(),
        metadata={
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_PIPELINE
        },
    )

    assert manager._resolve_child_task_class(child_spec) is PipelineTask

    edge_spec = TaskSpec.model_validate(
        {
            **child_spec.model_dump(mode="json"),
            "metadata": {
                **child_spec.metadata,
                INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
            },
        }
    )
    assert manager._resolve_child_task_class(edge_spec) is PipelineEdgeTask


def test_manager_rejects_unknown_internal_task_class(manager_setup) -> None:
    manager, make_queue = manager_setup
    child_spec = TaskSpec(
        tid=str(time.time_ns()),
        name="bad-child",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
        ),
        io=IOSection(
            inputs={"inbox": "bad.inbox"},
            outputs={"outbox": "bad.outbox"},
            control={"ctrl_in": "bad.ctrl_in", "ctrl_out": "bad.ctrl_out"},
        ),
        state=StateSection(),
        metadata={INTERNAL_RUNTIME_TASK_CLASS_KEY: "mystery"},
    )

    launched = manager._launch_child_task(child_spec, None)

    assert launched is False
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "task_spawn_rejected" for event in events)


def test_manager_registry_entries(manager_setup) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_MANAGERS_REGISTRY_QUEUE)
    entries = [json.loads(item) for item in drain(registry_queue)]
    relevant = [entry for entry in entries if entry.get("tid") == manager.tid]
    assert len(relevant) == 1
    assert relevant[0]["status"] == "active"
    manager.cleanup()
    entries = [json.loads(item) for item in drain(registry_queue)]
    relevant = [entry for entry in entries if entry.get("tid") == manager.tid]
    assert len(relevant) == 1
    assert relevant[0]["status"] == "stopped"


def test_manager_unregister_registry_broker_error_is_best_effort(
    unique_tid: str,
) -> None:
    class _FailingRegistryQueue:
        def generate_timestamp(self) -> int:
            return 123

        def delete(self, *, message_id: int | None = None) -> bool:
            del message_id
            raise BrokerError("registry delete failed")

        def write(self, _payload: str) -> None:
            raise BrokerError("registry write failed")

    manager = object.__new__(Manager)
    manager._unregistered = False
    manager._registry_message_id = 456
    manager.tid = unique_tid
    manager.taskspec = make_manager_spec(
        unique_tid,
        f"manager.{unique_tid}.inbox",
        f"manager.{unique_tid}.ctrl_in",
        f"manager.{unique_tid}.ctrl_out",
    )
    manager._queue_names = {
        "inbox": f"manager.{unique_tid}.inbox",
        "ctrl_in": f"manager.{unique_tid}.ctrl_in",
        "ctrl_out": f"manager.{unique_tid}.ctrl_out",
        "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
        "reserved": f"T{unique_tid}.reserved",
    }
    manager._queue = lambda _name: _FailingRegistryQueue()
    manager._latest_registry_entry = lambda _queue, _tid: None

    manager._unregister_manager()

    assert manager._unregistered is True
    assert manager._registry_message_id is None


def test_manager_tid_mapping_forces_role_manager(broker_env, unique_tid) -> None:
    db_path, make_queue = broker_env
    spec = make_manager_spec(
        unique_tid,
        f"manager.{unique_tid}.inbox",
        f"manager.{unique_tid}.ctrl_in",
        f"manager.{unique_tid}.ctrl_out",
        role="task",
    )
    manager = Manager(db_path, spec)
    try:
        mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
        entries = [json.loads(item) for item in drain(mapping_queue)]
        relevant = [entry for entry in entries if entry.get("full") == manager.tid]
        assert relevant
        assert relevant[-1]["role"] == "manager"
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_manager_tid_mapping_defaults_role_manager(manager_setup) -> None:
    manager, make_queue = manager_setup
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    entries = [json.loads(item) for item in drain(mapping_queue)]
    relevant = [entry for entry in entries if entry.get("full") == manager.tid]
    assert relevant
    assert relevant[-1]["role"] == "manager"


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals required")
def test_manager_sigterm_drains_nonpersistent_children(manager_setup) -> None:
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

    start = time.time()
    while not manager._child_processes and time.time() - start < 5.0:
        manager.process_once()
        time.sleep(0.05)

    assert manager._child_processes, "child process should be running"
    _child_tid, child_info = next(iter(manager._child_processes.items()))
    assert child_info.process.is_alive()

    manager.handle_termination_signal(signal.SIGTERM)

    assert manager._draining is True
    assert manager.should_stop is False
    assert manager.taskspec.state.status == "running"

    deadline = time.time() + 5.0
    while time.time() < deadline and not manager.should_stop:
        manager.process_once()
        time.sleep(0.05)

    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"
    assert not _process_running(child_info.process.pid)

    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "task_signal_stop" for event in events)
    assert not any(event.get("event") == "task_signal_kill" for event in events)


@pytest.mark.skipif(
    os.name == "nt" or getattr(signal, "SIGUSR1", None) is None,
    reason="SIGUSR1 not available",
)
def test_manager_sigusr1_keeps_kill_semantics(manager_setup) -> None:
    """SIGUSR1 should stay on the immediate kill path and emit task_signal_kill."""

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

    start = time.time()
    while not manager._child_processes and time.time() - start < 5.0:
        manager.process_once()
        time.sleep(0.05)

    assert manager._child_processes, "child process should be running"
    _child_tid, child_info = next(iter(manager._child_processes.items()))

    manager.handle_termination_signal(signal.SIGUSR1)

    assert manager.should_stop is True
    assert manager.taskspec.state.status == "killed"
    assert not _process_running(child_info.process.pid)

    events = [json.loads(item) for item in drain(log_queue)]
    assert any(event.get("event") == "task_signal_kill" for event in events)


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

    deadline = time.time() + (8.0 if os.name == "nt" else 3.0)
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
    monkeypatch.setattr(
        manager, "_unregister_manager", lambda: unregister_calls.append(True)
    )

    yielded = manager._maybe_yield_leadership(force=True)

    assert yielded is True
    assert manager._draining is True
    assert manager.should_stop is False
    assert unregister_calls == [True]
    assert manager.taskspec.state.status == "running"

    events = [json.loads(item) for item in drain(log_queue)]
    yield_events = [
        event for event in events if event.get("event") == "manager_leadership_yielded"
    ]
    assert len(yield_events) == 1
    assert yield_events[0]["leader_tid"] == lower_leader_tid
    assert yield_events[0]["draining"] is True
    assert yield_events[0]["status"] == "running"

    manager._child_processes.clear()
    manager.process_once()

    assert manager.should_stop is True
    assert manager.taskspec.state.status == "cancelled"

    events = [json.loads(item) for item in drain(log_queue)]
    drained_events = [
        event for event in events if event.get("event") == "manager_leadership_drained"
    ]
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


def test_manager_leadership_ignores_noncanonical_lower_manager(
    manager_setup,
) -> None:
    manager, make_queue = manager_setup
    registry_queue = make_queue(WEFT_MANAGERS_REGISTRY_QUEUE)
    lower_tid = str(int(manager.tid) - 1)
    registry_queue.write(
        json.dumps(
            {
                "tid": lower_tid,
                "name": "legacy-manager",
                "status": "active",
                "pid": os.getpid(),
                "timestamp": registry_queue.generate_timestamp(),
                "inbox": "legacy.requests",
                "requests": "legacy.requests",
                "ctrl_in": "legacy.ctrl_in",
                "ctrl_out": "legacy.ctrl_out",
                "outbox": "legacy.outbox",
                "role": "manager",
            }
        )
    )

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

    autostart_dir, template_path = write_autostart_fixture(
        tmp_path,
        task_name="simulate",
        manifest_name="watcher",
        mode="once",
        duration=0.2,
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


def test_build_child_spec_propagates_unexpected_resolution_error(
    manager_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _make_queue = manager_setup

    def _unexpected_resolution_error(
        *args: object, **kwargs: object
    ) -> dict[str, object]:
        del args, kwargs
        raise RuntimeError("unexpected resolution bug")

    monkeypatch.setattr(
        "weft.core.manager.resolve_taskspec_payload",
        _unexpected_resolution_error,
    )

    with pytest.raises(RuntimeError, match="unexpected resolution bug"):
        manager._build_child_spec(make_child_spec(), int(time.time_ns()))


def test_manager_idle_timeout_force_refreshes_cached_broker_activity_before_shutdown(
    broker_env,
    unique_tid,
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
        previous_activity = time.time_ns() - 1_000_000_000
        broker_queue = manager._get_connected_queue()
        cached_timestamp = broker_queue.last_ts or 0
        manager._last_activity_ns = previous_activity
        manager._last_broker_timestamp = cached_timestamp
        manager._last_broker_probe_ns = time.time_ns()

        activity_queue = make_queue("manager.activity")
        activity_queue.write("ping")
        activity_timestamp = activity_queue.last_ts
        assert isinstance(activity_timestamp, int)
        assert activity_timestamp > cached_timestamp
        assert broker_queue.last_ts == cached_timestamp

        manager.process_once()

        assert manager.should_stop is False
        assert manager._last_broker_timestamp >= activity_timestamp
        assert manager._last_activity_ns > previous_activity
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

    autostart_dir, template_path = write_autostart_fixture(
        tmp_path,
        task_name="observer",
        manifest_name="observer",
        mode="once",
        duration=0.1,
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


def test_manager_autostart_prunes_deleted_manifest_state(
    tmp_path: Path,
    broker_env,
    unique_tid,
) -> None:
    db_path, _make_queue = broker_env
    autostart_dir = tmp_path / "autostart"
    autostart_dir.mkdir()

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    stale_source = str((autostart_dir / "deleted.json").resolve())
    try:
        manager._autostart_state[stale_source] = {
            "restarts": 2,
            "next_allowed_ns": time.time_ns(),
            "launched_once": True,
        }
        manager._autostart_launched.add(stale_source)

        manager._tick_autostart(force=True)

        assert stale_source not in manager._autostart_state
        assert stale_source not in manager._autostart_launched
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_restarts(
    tmp_path: Path, broker_env, unique_tid
) -> None:
    db_path, make_queue = broker_env

    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart",
        manifest_name="restart",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=0,
        duration=0.0,
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

    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart",
        manifest_name="restart",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=0,
        duration=0.0,
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


def test_manager_autostart_pipeline_target_launches_pipeline_run(
    tmp_path: Path,
    broker_env,
    unique_tid,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_pipeline_fixture(
        tmp_path,
        task_name="simulate-bundle",
        pipeline_name="autostart-pipeline",
        manifest_name="autostart-pipeline",
        mode="once",
        function_target="tests.tasks.sample_targets:echo_payload",
        manifest_input="autostart-pipeline",
        task_bundle=True,
        pipeline_bundle=True,
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    source = str(manifest_path.resolve())
    outbox_queue = None
    spawn_event: dict[str, object] | None = None
    result_payload = None
    try:
        deadline = time.time() + 8.0
        while time.time() < deadline and (
            spawn_event is None or result_payload is None
        ):
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    spawn_event = event
                    child_taskspec = event["child_taskspec"]
                    outbox_name = child_taskspec["io"]["outputs"]["outbox"]
                    outbox_queue = make_queue(outbox_name)
            if outbox_queue is not None and result_payload is None:
                raw = outbox_queue.read_one()
                if raw is not None:
                    try:
                        result_payload = json.loads(raw)
                    except json.JSONDecodeError:
                        result_payload = raw

        assert spawn_event is not None
        child_taskspec = spawn_event["child_taskspec"]
        assert child_taskspec["metadata"]["role"] == "pipeline"
        assert (
            child_taskspec["metadata"][INTERNAL_RUNTIME_TASK_CLASS_KEY]
            == INTERNAL_RUNTIME_TASK_CLASS_PIPELINE
        )
        assert result_payload == "autostart-pipeline"
    finally:
        manager.cleanup()


def test_manager_autostart_pipeline_ensure_restarts(
    tmp_path: Path,
    broker_env,
    unique_tid,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_pipeline_fixture(
        tmp_path,
        task_name="pipeline-restart-task",
        pipeline_name="pipeline-restart",
        manifest_name="pipeline-restart",
        mode="ensure",
        max_restarts=1,
        backoff_seconds=0,
        function_target="tests.tasks.sample_targets:echo_payload",
        manifest_input="restart-me",
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    source = str(manifest_path.resolve())
    spawn_events: list[dict[str, object]] = []
    try:
        deadline = time.time() + 8.0
        while len(spawn_events) < 2 and time.time() < deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    spawn_events.append(event)

        assert len(spawn_events) == 2
        assert manager._autostart_state[source]["restarts"] == 1
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_enqueue_failure_does_not_advance_state(
    tmp_path: Path,
    broker_env,
    unique_tid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="retry-on-failure",
        manifest_name="retry-on-failure",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=1.0,
        duration=0.0,
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = False
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    source = str(manifest_path.resolve())
    try:
        manager._autostart_enabled = True
        monkeypatch.setattr(
            manager,
            "_enqueue_autostart_request",
            lambda payload, inbox_message: False,
        )

        manager._tick_autostart(force=True)

        assert source not in manager._autostart_launched
        assert manager._autostart_state[source]["restarts"] == 0
        assert manager._autostart_state[source]["next_allowed_ns"] == 0
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_allows_one_restart_after_initial_launch(
    tmp_path: Path,
    broker_env,
    unique_tid,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart-limit",
        manifest_name="restart-limit",
        mode="ensure",
        max_restarts=1,
        backoff_seconds=0,
        duration=0.0,
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    source = str(manifest_path.resolve())
    spawn_events: list[dict[str, object]] = []
    try:
        deadline = time.time() + 8.0
        while len(spawn_events) < 2 and time.time() < deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    spawn_events.append(event)

        assert len(spawn_events) == 2
        assert manager._autostart_state[source]["restarts"] == 1

        extra_deadline = time.time() + 1.0
        while time.time() < extra_deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    spawn_events.append(event)

        assert len(spawn_events) == 2
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_applies_backoff_to_restart_only(
    tmp_path: Path,
    broker_env,
    unique_tid,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart-backoff",
        manifest_name="restart-backoff",
        mode="ensure",
        max_restarts=1,
        backoff_seconds=0.5,
        duration=0.0,
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    source = str(manifest_path.resolve())
    try:
        initial_spawn_events = []
        initial_deadline = time.time() + 2.0
        while not initial_spawn_events and time.time() < initial_deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    initial_spawn_events.append(event)
        assert len(initial_spawn_events) == 1

        early_deadline = time.time() + 0.3
        restart_events = []
        while time.time() < early_deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    restart_events.append(event)

        assert restart_events == []

        deadline = time.time() + 2.0
        while not restart_events and time.time() < deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    restart_events.append(event)

        assert len(restart_events) == 1
    finally:
        manager.cleanup()


def test_manager_autostart_ensure_restarts_after_abrupt_child_kill(
    tmp_path: Path,
    broker_env,
    unique_tid,
) -> None:
    db_path, make_queue = broker_env
    autostart_dir, manifest_path = write_autostart_fixture(
        tmp_path,
        task_name="restart-after-kill",
        manifest_name="restart-after-kill",
        mode="ensure",
        max_restarts=2,
        backoff_seconds=0,
        duration=10.0,
    )

    config = load_config()
    config["WEFT_AUTOSTART_TASKS"] = True
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    spec = make_manager_spec(unique_tid, idle_timeout=1.5)
    manager = Manager(db_path, spec, config=config)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    source = str(manifest_path.resolve())
    try:
        child_event: dict[str, object] | None = None
        spawn_events: list[dict[str, object]] = []
        seen_events: list[dict[str, object]] = []
        child_tid: str | None = None
        start = time.time()
        while child_event is None and time.time() - start < 5.0:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
                seen_events.append(event)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    spawn_events.append(event)
                    child_tid = str(event.get("child_tid"))
                if child_tid is not None:
                    child_event = next(
                        (
                            candidate
                            for candidate in seen_events
                            if candidate.get("tid") == child_tid
                        ),
                        None,
                    )

        assert manager._child_processes
        assert child_tid is not None
        child = next(iter(manager._child_processes.values())).process
        child_pid = child.pid
        assert isinstance(child_pid, int) and child_pid > 0
        live_deadline = time.time() + 2.0
        while not _process_running(child_pid) and time.time() < live_deadline:
            time.sleep(0.05)
        assert _process_running(child_pid) is True
        assert child_event is not None
        assert len(spawn_events) == 1

        child.kill()
        child.join(timeout=2.0)

        kill_deadline = time.time() + 2.0
        while _process_running(child_pid) and time.time() < kill_deadline:
            time.sleep(0.05)
        assert _process_running(child_pid) is False

        restart_deadline = time.time() + 5.0
        while len(spawn_events) < 2 and time.time() < restart_deadline:
            manager.process_once()
            time.sleep(0.05)
            for item in drain(log_queue):
                event = json.loads(item)
                if (
                    event.get("event") == "task_spawned"
                    and event.get("autostart_source") == source
                ):
                    spawn_events.append(event)

        assert len(spawn_events) >= 2
    finally:
        manager.cleanup()
