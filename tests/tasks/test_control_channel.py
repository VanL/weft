"""Control-channel behaviour for Task implementations."""

from __future__ import annotations

import json

import pytest

import weft.core.tasks.base as base_task
from tests.helpers.reactor_driver import drive_until
from tests.tasks import sample_targets as targets  # noqa: F401
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import (
    PONG_EXTENSION_KEY,
    QUEUE_RESERVED_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    load_config,
)
from weft.core.control_messages import ControlRequest, encode_control_message
from weft.core.manager import Manager
from weft.core.tasks import Consumer, Monitor, PipelineTask
from weft.core.tasks.base import TaskControlPolicy
from weft.core.tasks.multiqueue_watcher import QueueMessageContext, QueueMode
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec
from weft.ext import RunnerHandle, RunnerRuntimeDescription


def _read_all(queue):
    messages = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        messages.append(value)
    return messages


def _drive_task_until(task: Consumer, predicate, *, timeout: float = 5.0) -> None:
    drive_until(
        predicate,
        bool,
        step=task.process_once,
        wait=task.wait_for_activity,
        timeout=timeout,
        wait_slice=0.02,
        pending_work=(task._has_pending_worker_results,),
    )


def _make_manager_taskspec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="manager",
        spec=SpecSection(
            type="function",
            function_target="weft.core.manager:Manager",
            weft_context=".",
        ),
        io=IOSection(
            inputs={"inbox": WEFT_SPAWN_REQUESTS_QUEUE},
            outputs={"outbox": WEFT_MANAGER_OUTBOX_QUEUE},
            control={
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
        metadata={"role": "manager", "capabilities": []},
    )


def test_pause_resume_control_flow(broker_env, unique_tid):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]
    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])

    ctrl_in.write(encode_control_message("PAUSE", request_id="pause-1"))
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    all_responses = list(responses)
    assert any(
        r.get("command") == "PAUSE"
        and r["status"] == "ack"
        and r["request_id"] == "pause-1"
        for r in responses
    )

    inbox.write(json.dumps({"payload": "work"}))
    task.process_once()

    # Message should remain in inbox while paused
    assert inbox.peek_one() is not None
    assert outbox.read_one() is None

    ctrl_in.write(encode_control_message("RESUME", request_id="resume-1"))
    task.wait_for_activity(timeout=0.02)
    task.process_once()
    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    all_responses.extend(responses)

    task.process_once()
    assert task._paused is False
    all_responses.extend(json.loads(msg) for msg in _read_all(ctrl_out))

    _drive_task_until(task, lambda: outbox.peek_one() is not None)
    all_responses.extend(json.loads(msg) for msg in _read_all(ctrl_out))

    assert any(
        r.get("command") == "RESUME"
        and r["status"] == "ack"
        and r["request_id"] == "resume-1"
        for r in all_responses
    ), all_responses
    assert outbox.read_one() == "work"


def test_status_command_reports_state(broker_env, unique_tid):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]

    ctrl_in.write(encode_control_message("STATUS", request_id="status-1"))
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    status_response = next(r for r in responses if r.get("command") == "STATUS")
    assert status_response["status"] == "ok"
    assert status_response["paused"] is False
    assert status_response["request_id"] == "status-1"
    assert status_response["task_status"] == task.taskspec.state.status
    assert status_response["runner"]


@pytest.mark.parametrize(
    ("command", "expected_status"),
    [("STOP", "cancelled"), ("KILL", "killed")],
)
def test_stop_kill_command_sends_keyed_ack(
    broker_env,
    unique_tid,
    command: str,
    expected_status: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")

    task.taskspec.mark_started(pid=0)
    task.taskspec.mark_running(pid=0)
    reserved.write("work")

    request_id = f"{command.lower()}-1"
    ctrl_in.write(encode_control_message(command, request_id=request_id))
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    response = next(r for r in responses if r.get("command") == command)
    assert response["status"] == "ack"
    assert response["request_id"] == request_id
    assert task.should_stop is True
    assert task.taskspec.state.status == expected_status


def test_late_stop_after_terminal_state_acks_without_state_regression(
    broker_env, unique_tid
):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    _read_all(log_queue)

    task.taskspec.mark_running()
    task.taskspec.mark_completed(return_code=0)

    ctrl_in.write(encode_control_message("STOP"))
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    stop_response = next(r for r in responses if r.get("command") == "STOP")
    assert stop_response["status"] == "ack"
    assert task.taskspec.state.status == "completed"
    assert task.should_stop is True

    events = [json.loads(msg) for msg in _read_all(log_queue)]
    assert not any(event.get("event") == "control_stop" for event in events)


def test_stop_kill_overrides_declare_control_policy() -> None:
    overridden_classes = [Manager, PipelineTask, Monitor]

    for task_cls in overridden_classes:
        assert "_handle_control_command" in task_cls.__dict__
        assert isinstance(task_cls.control_policy, TaskControlPolicy)
        assert task_cls.control_policy.stop
        assert task_cls.control_policy.kill
        assert task_cls.control_policy.reserved_policy
        assert task_cls.control_policy.ack
        assert task_cls.control_policy.terminal_state

    assert Consumer.control_policy.stop == "deferred-while-active"


def test_ping_control_command_returns_pong(broker_env, unique_tid):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]

    ctrl_in.write(encode_control_message("PING"))
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    ping_response = next(r for r in responses if r.get("command") == "PING")
    assert ping_response["status"] == "ok"
    assert ping_response["message"] == "PONG"
    assert ping_response["task_status"] == task.taskspec.state.status
    assert ping_response["paused"] is False
    assert ping_response["should_stop"] is False
    assert ping_response["runner"]


def test_structured_ping_echoes_request_id_and_snapshot(broker_env, unique_tid):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]
    request_id = "req-123"

    ctrl_in.write(encode_control_message("PING", request_id=request_id))
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    ping_response = next(r for r in responses if r.get("command") == "PING")
    assert ping_response["status"] == "ok"
    assert ping_response["message"] == "PONG"
    assert ping_response["request_id"] == request_id
    assert ping_response["task_status"] == task.taskspec.state.status
    assert ping_response["paused"] is False
    assert ping_response["should_stop"] is False
    assert ping_response["runner"]


@pytest.mark.parametrize(
    "payload",
    [
        "PING",
        "null",
        "[]",
        "42",
        "true",
        json.dumps("PING"),
        json.dumps({"command": "ping"}),
        json.dumps({"command": "DANCE", "request_id": "unknown"}),
        json.dumps({"command": "PING", "extra": True}),
        json.dumps({"command": "PING", "request_id": "   "}),
    ],
)
def test_invalid_control_payload_is_acked_without_reply_and_does_not_block(
    broker_env,
    unique_tid,
    payload: str,
) -> None:
    """Port the reference invalid-payload progress contract to Weft [MF-3]."""
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]

    try:
        invalid_id = int(ctrl_in.write(payload))
        progress_body = encode_control_message("PING", request_id="progress")
        progress_id = int(ctrl_in.write(progress_body))
        task.process_once()

        assert _read_all(ctrl_out) == []
        remaining = list(ctrl_in.peek_generator(with_timestamps=True))
        assert remaining == [(progress_body, progress_id)]
        assert invalid_id != progress_id

        _drive_task_until(task, lambda: ctrl_out.peek_one() is not None)

        progress_responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
        assert any(
            response.get("message") == "PONG"
            and response.get("request_id") == "progress"
            for response in progress_responses
        )
        assert ctrl_in.peek_one() is None
    finally:
        task.cleanup()


def test_control_ack_survives_task_reconstruction_without_replay(
    broker_env,
    unique_tid,
) -> None:
    """A deleted control row is not replayed after task reconstruction [MF-3]."""
    db_path, make_queue = broker_env
    ctrl_in_name = f"custom.control.{unique_tid}.in"
    ctrl_out_name = f"custom.control.{unique_tid}.out"

    def make_spec() -> TaskSpec:
        return TaskSpec(
            tid=unique_tid,
            name="control-restart",
            spec=SpecSection(
                type="function",
                function_target="tests.tasks.sample_targets:echo_payload",
            ),
            io=IOSection(
                inputs={"inbox": f"T{unique_tid}.inbox"},
                outputs={"outbox": f"T{unique_tid}.outbox"},
                control={"ctrl_in": ctrl_in_name, "ctrl_out": ctrl_out_name},
            ),
            state=StateSection(),
        )

    ctrl_in = make_queue(ctrl_in_name)
    ctrl_out = make_queue(ctrl_out_name)
    default_ctrl_in = make_queue(f"T{unique_tid}.ctrl_in")
    default_ctrl_out = make_queue(f"T{unique_tid}.ctrl_out")
    default_trap = encode_control_message("PING", request_id="default-trap")
    default_ctrl_in.write(default_trap)
    first = Consumer(db_path, make_spec())
    try:
        ctrl_in.write(encode_control_message("PING", request_id="first"))
        first.process_once()
        assert ctrl_in.peek_one() is None
        first_response = json.loads(ctrl_out.read_one())
        assert first_response["request_id"] == "first"
        assert default_ctrl_in.peek_one() == default_trap
        assert default_ctrl_out.peek_one() is None
    finally:
        first.cleanup()

    replacement = Consumer(db_path, make_spec())
    try:
        ctrl_in.write(encode_control_message("PING", request_id="barrier"))
        replacement.process_once()

        responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
        assert [response.get("request_id") for response in responses] == ["barrier"]
        assert ctrl_in.peek_one() is None
        assert default_ctrl_in.peek_one() == default_trap
        assert default_ctrl_out.peek_one() is None
    finally:
        replacement.cleanup()


def test_task_can_register_pong_extension_provider(broker_env, unique_tid):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]
    task.register_pong_extension_provider(
        lambda: {"queue_depth": 3, "notes": {"mode": "diagnostic"}}
    )

    ctrl_in.write(encode_control_message("PING", request_id="extended-ping"))
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    ping_response = next(r for r in responses if r.get("command") == "PING")
    assert ping_response["message"] == "PONG"
    assert ping_response[PONG_EXTENSION_KEY] == {
        "queue_depth": 3,
        "notes": {"mode": "diagnostic"},
    }
    assert ping_response["request_id"] == "extended-ping"
    assert ping_response["task_status"] == task.taskspec.state.status


def test_bad_pong_extension_provider_error_stays_nested(broker_env, unique_tid):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]
    task.register_pong_extension_provider(lambda: {"bad": {object()}})

    ctrl_in.write(encode_control_message("PING"))
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    ping_response = next(r for r in responses if r.get("command") == "PING")
    assert "error" in ping_response[PONG_EXTENSION_KEY]
    assert ping_response["message"] == "PONG"
    assert ping_response["task_status"] == task.taskspec.state.status


def test_pong_extension_provider_failure_is_local_but_fatal_exit_propagates(
    broker_env,
    unique_tid,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]
    ordinary = LookupError("verbatim extension failure")

    def fail_ordinary() -> None:
        raise ordinary

    class FatalExtensionExit(BaseException):
        pass

    fatal = FatalExtensionExit("fatal extension exit")

    def fail_fatal() -> None:
        raise fatal

    try:
        task.register_pong_extension_provider(fail_ordinary)
        ctrl_in.write(
            encode_control_message("PING", request_id="failing-extension-provider")
        )
        task.process_once()
        responses = [json.loads(message) for message in _read_all(ctrl_out)]
        assert len(responses) == 1
        pong = responses[0]
        assert pong["command"] == "PING"
        assert pong["status"] == "ok"
        assert pong["message"] == "PONG"
        assert pong["tid"] == unique_tid
        assert pong["task_status"] == task.taskspec.state.status
        assert pong["paused"] is False
        assert pong["should_stop"] is False
        assert pong["runner"] == task.taskspec.spec.runner.name
        assert pong["request_id"] == "failing-extension-provider"
        assert pong[PONG_EXTENSION_KEY] == {"error": "verbatim extension failure"}

        task.register_pong_extension_provider(fail_fatal)
        with pytest.raises(FatalExtensionExit) as exc_info:
            task._pong_extension_fields()
        assert exc_info.value is fatal
    finally:
        task.cleanup()


def test_manager_ping_includes_manager_selection_fields(broker_env, unique_tid):
    db_path, make_queue = broker_env
    spec = _make_manager_taskspec(unique_tid)
    task = Manager(
        db_path,
        spec,
        config=load_config(
            {
                "WEFT_AUTOSTART_TASKS": False,
                "WEFT_TASK_MONITOR_ENABLED": False,
            }
        ),
    )

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]
    request_id = "manager-probe-request"

    try:
        ctrl_in.write(encode_control_message("PING", request_id=request_id))
        task.process_once()

        responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
        ping_response = next(r for r in responses if r.get("command") == "PING")
        assert ping_response["status"] == "ok"
        assert ping_response["message"] == "PONG"
        assert ping_response["request_id"] == request_id
        assert ping_response["role"] == "manager"
        assert ping_response["requests"] == WEFT_SPAWN_REQUESTS_QUEUE
        assert ping_response["ctrl_in"] == f"T{unique_tid}.ctrl_in"
        assert ping_response["ctrl_out"] == f"T{unique_tid}.ctrl_out"
        assert ping_response["outbox"] == WEFT_MANAGER_OUTBOX_QUEUE
        assert ping_response["weft_context"] == "."

        assert task._handle_control_command(
            ControlRequest("STOP", "manager-stop"),
            QueueMessageContext(
                queue_name=spec.io.control["ctrl_in"],
                queue=ctrl_in,
                mode=QueueMode.PEEK,
                timestamp=1,
            ),
        )
        assert task._draining is True
        assert task.should_stop is False
        assert task._control_snapshot_fields()["should_stop"] is True

        stop_responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
        stop_response = next(
            response for response in stop_responses if response.get("command") == "STOP"
        )
        assert stop_response["status"] == "ack"
        assert stop_response["request_id"] == "manager-stop"
    finally:
        task.cleanup()


def test_ping_includes_runtime_summary_from_runner_plugin(
    broker_env, unique_tid, monkeypatch
):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    task._runtime_handle = RunnerHandle(
        runner="fake-runtime",
        kind="container",
        id="runtime-1",
        control={"authority": "runner"},
        observations={"container_id": "abc123"},
    )

    class FakePlugin:
        def describe(self, handle: RunnerHandle) -> RunnerRuntimeDescription:
            assert handle.id == "runtime-1"
            return RunnerRuntimeDescription(
                runner="fake-runtime",
                id="runtime-1",
                state="running",
                metadata={"container_id": "abc123", "memory_usage_mb": 12.5},
            )

    def fake_require_runner_plugin(name: str) -> FakePlugin:
        assert name == "fake-runtime"
        return FakePlugin()

    monkeypatch.setattr(
        base_task,
        "require_runner_plugin",
        fake_require_runner_plugin,
    )
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]

    ctrl_in.write(encode_control_message("PING"))
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    ping_response = next(r for r in responses if r.get("command") == "PING")
    assert ping_response["runtime"]["runner"] == "fake-runtime"
    assert ping_response["runtime"]["id"] == "runtime-1"
    assert ping_response["runtime"]["state"] == "running"
    assert ping_response["runtime"]["metadata"]["container_id"] == "abc123"
    assert ping_response["runtime"]["metadata"]["memory_usage_mb"] == 12.5


@pytest.fixture
def unique_tid() -> str:
    import time

    return str(time.time_ns())
