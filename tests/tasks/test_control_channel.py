"""Control-channel behaviour for Task implementations."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import weft.core.tasks.base as base_task
from simplebroker import Queue
from tests.helpers.reactor_driver import drive_until
from tests.helpers.typing import BrokerEnv
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
from weft.core.task_state import task_state_queue_name
from weft.core.tasks import Consumer
from weft.core.tasks.multiqueue_watcher import QueueMessageContext, QueueMode
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec
from weft.ext import RunnerHandle, RunnerRuntimeDescription


def _read_all(queue: Queue) -> list[str]:
    messages = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        messages.append(value)
    return messages


def _has_control_reply_row(rows: list[dict[str, object]]) -> bool:
    return any("command" in row and "status" in row for row in rows)


def _newest_projection_event(queue: Queue) -> object:
    rows = [json.loads(raw) for raw in queue.peek_generator()]
    assert rows
    projection = rows[-1]["projection"]
    assert isinstance(projection, dict)
    return projection.get("event")


def _requester_queue(
    make_queue: Callable[[str], Queue],
    tid: str,
    label: str,
) -> tuple[str, Queue]:
    queue_name = f"T{tid}.{label}.ctrl_in"
    return queue_name, make_queue(queue_name)


def _drive_task_until(
    task: Consumer, predicate: Callable[[], bool], *, timeout: float = 5.0
) -> None:
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


def test_pause_resume_control_flow(broker_env: BrokerEnv, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue
    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    state_queue = make_queue(task_state_queue_name(unique_tid))

    ctrl_in.write(encode_control_message("PAUSE", request_id="pause-1"))
    task.process_once()

    assert _read_all(ctrl_out) == []
    events = [json.loads(msg).get("event") for msg in log_queue.peek_generator()]
    assert "control_pause" in events
    assert _newest_projection_event(state_queue) == "control_pause"

    inbox.write(json.dumps({"payload": "work"}))
    task.process_once()

    # Message should remain in inbox while paused
    assert inbox.peek_one() is not None
    assert outbox.read_one() is None

    ctrl_in.write(encode_control_message("RESUME", request_id="resume-1"))
    task.wait_for_activity(timeout=0.02)
    task.process_once()
    assert _read_all(ctrl_out) == []
    events = [json.loads(msg).get("event") for msg in log_queue.peek_generator()]
    assert "control_resume" in events
    assert _newest_projection_event(state_queue) == "control_resume"

    task.process_once()
    assert task._paused is False

    _drive_task_until(task, lambda: outbox.peek_one() is not None)
    assert not _has_control_reply_row(
        [json.loads(msg) for msg in ctrl_out.peek_generator()]
    )
    assert outbox.read_one() == "work"


def test_status_command_replies_to_reply_to(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue
    reply_name = f"T{int(unique_tid) + 1}.ctrl_in"
    replies = make_queue(reply_name)

    ctrl_in.write(
        encode_control_message("STATUS", request_id="status-1", reply_to=reply_name)
    )
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(replies)]
    assert len(responses) == 1
    status_response = responses[0]
    assert status_response["command"] == "STATUS"
    assert status_response["tid"] == unique_tid
    assert "reply_to" not in status_response
    assert _read_all(ctrl_out) == []
    assert status_response["status"] == "ok"
    assert status_response["paused"] is False
    assert status_response["request_id"] == "status-1"
    assert status_response["task_status"] == task.taskspec.state.status
    assert status_response["runner"]


@pytest.mark.parametrize(
    ("command", "expected_status"),
    [("STOP", "cancelled"), ("KILL", "killed")],
)
def test_stop_kill_command_publishes_terminal_and_writes_no_reply(
    broker_env: BrokerEnv,
    unique_tid: str,
    command: str,
    expected_status: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)

    task.taskspec.mark_started(pid=0)
    task.taskspec.mark_running(pid=0)
    reserved.write("work")

    request_id = f"{command.lower()}-1"
    ctrl_in.write(encode_control_message(command, request_id=request_id))
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    assert len(responses) == 1, responses
    assert not _has_control_reply_row(responses), responses
    terminal = responses[0]
    assert terminal["type"] == "terminal"
    assert terminal["source"] == "task"
    assert terminal["tid"] == unique_tid
    assert terminal["status"] == expected_status
    events = [json.loads(msg) for msg in _read_all(log_queue)]
    assert any(
        event.get("event") == f"control_{command.lower()}"
        and event.get("status") == expected_status
        for event in events
    ), events
    assert task.should_stop is True
    assert task.taskspec.state.status == expected_status


def test_late_stop_after_terminal_state_writes_nothing(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    _read_all(log_queue)

    task.taskspec.mark_running()
    task.taskspec.mark_completed(return_code=0)

    ctrl_in.write(encode_control_message("STOP"))
    task.process_once()

    assert _read_all(ctrl_out) == []
    assert task.taskspec.state.status == "completed"
    assert task.should_stop is True

    events = [json.loads(msg) for msg in _read_all(log_queue)]
    assert not any(event.get("event") == "control_stop" for event in events)


def test_ping_control_command_returns_pong(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue
    reply_to, replies = _requester_queue(make_queue, unique_tid, "basic-ping")

    ctrl_in.write(
        encode_control_message("PING", request_id="ping-1", reply_to=reply_to)
    )
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(replies)]
    ping_response = next(r for r in responses if r.get("command") == "PING")
    assert ping_response["status"] == "ok"
    assert ping_response["message"] == "PONG"
    assert ping_response["task_status"] == task.taskspec.state.status
    assert ping_response["paused"] is False
    assert ping_response["should_stop"] is False
    assert ping_response["runner"]
    assert ping_response["request_id"] == "ping-1"
    assert "reply_to" not in ping_response
    assert _read_all(ctrl_out) == []


def test_structured_ping_echoes_request_id_and_snapshot(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue
    request_id = "req-123"
    reply_to, replies = _requester_queue(make_queue, unique_tid, "structured-ping")

    ctrl_in.write(
        encode_control_message(
            "PING",
            request_id=request_id,
            reply_to=reply_to,
        )
    )
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(replies)]
    ping_response = next(r for r in responses if r.get("command") == "PING")
    assert ping_response["status"] == "ok"
    assert ping_response["message"] == "PONG"
    assert ping_response["request_id"] == request_id
    assert ping_response["task_status"] == task.taskspec.state.status
    assert ping_response["paused"] is False
    assert ping_response["should_stop"] is False
    assert ping_response["runner"]
    assert "reply_to" not in ping_response
    assert _read_all(ctrl_out) == []


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
    broker_env: BrokerEnv,
    unique_tid: str,
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
    ctrl_out = task._ctrl_out_queue
    reply_to, replies = _requester_queue(make_queue, unique_tid, "progress")

    try:
        invalid_id = int(ctrl_in.write(payload))
        progress_body = encode_control_message(
            "PING",
            request_id="progress",
            reply_to=reply_to,
        )
        progress_id = int(ctrl_in.write(progress_body))
        task.process_once()

        assert _read_all(ctrl_out) == []
        remaining = list(ctrl_in.peek_generator(with_timestamps=True))
        assert remaining == [(progress_body, progress_id)]
        assert invalid_id != progress_id

        _drive_task_until(task, lambda: replies.peek_one() is not None)

        progress_responses = [json.loads(msg) for msg in _read_all(replies)]
        assert any(
            response.get("message") == "PONG"
            and response.get("request_id") == "progress"
            for response in progress_responses
        )
        assert ctrl_in.peek_one() is None
        assert _read_all(ctrl_out) == []
    finally:
        task.cleanup()


def test_nonrequest_control_row_reaches_reply_hook_then_is_acked_once(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    observed: list[tuple[str, int, str]] = []
    acknowledgements: list[tuple[str, int]] = []
    original_ack = task._ack_control_message

    def capture_reply(
        message: str,
        timestamp: int,
        context: QueueMessageContext,
    ) -> None:
        observed.append((message, timestamp, context.queue_name))

    def capture_ack(queue_name: str, timestamp: int) -> None:
        acknowledgements.append((queue_name, timestamp))
        original_ack(queue_name, timestamp)

    monkeypatch.setattr(task, "_handle_control_reply", capture_reply)
    monkeypatch.setattr(task, "_ack_control_message", capture_ack)
    raw = json.dumps(
        {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "request_id": "reply-1",
            "tid": "target",
        }
    )
    message_id = int(ctrl_in.write(raw))

    task.process_once()

    assert observed == [(raw, message_id, spec.io.control["ctrl_in"])]
    assert acknowledgements == [(spec.io.control["ctrl_in"], message_id)]
    assert ctrl_in.peek_one() is None


def test_control_ack_survives_task_reconstruction_without_replay(
    broker_env: BrokerEnv,
    unique_tid: str,
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
    trap_reply_to, _trap_replies = _requester_queue(
        make_queue, unique_tid, "default-trap"
    )
    reply_to, replies = _requester_queue(make_queue, unique_tid, "restart")
    default_trap = encode_control_message(
        "PING",
        request_id="default-trap",
        reply_to=trap_reply_to,
    )
    default_ctrl_in.write(default_trap)
    first = Consumer(db_path, make_spec())
    try:
        ctrl_in.write(
            encode_control_message("PING", request_id="first", reply_to=reply_to)
        )
        first.process_once()
        assert ctrl_in.peek_one() is None
        raw_first_response = replies.read_one()
        assert raw_first_response is not None
        first_response = json.loads(raw_first_response)
        assert first_response["request_id"] == "first"
        assert default_ctrl_in.peek_one() == default_trap
        assert ctrl_out.peek_one() is None
        assert default_ctrl_out.peek_one() is None
    finally:
        first.cleanup()

    replacement = Consumer(db_path, make_spec())
    try:
        ctrl_in.write(
            encode_control_message("PING", request_id="barrier", reply_to=reply_to)
        )
        replacement.process_once()

        responses = [json.loads(msg) for msg in _read_all(replies)]
        assert [response.get("request_id") for response in responses] == ["barrier"]
        assert ctrl_in.peek_one() is None
        assert default_ctrl_in.peek_one() == default_trap
        assert ctrl_out.peek_one() is None
        assert default_ctrl_out.peek_one() is None
    finally:
        replacement.cleanup()


def test_task_can_register_pong_extension_provider(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue
    reply_to, replies = _requester_queue(make_queue, unique_tid, "extended")
    task.register_pong_extension_provider(
        lambda: {"queue_depth": 3, "notes": {"mode": "diagnostic"}}
    )

    ctrl_in.write(
        encode_control_message(
            "PING",
            request_id="extended-ping",
            reply_to=reply_to,
        )
    )
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(replies)]
    ping_response = next(r for r in responses if r.get("command") == "PING")
    assert ping_response["message"] == "PONG"
    assert ping_response[PONG_EXTENSION_KEY] == {
        "queue_depth": 3,
        "notes": {"mode": "diagnostic"},
    }
    assert ping_response["request_id"] == "extended-ping"
    assert ping_response["task_status"] == task.taskspec.state.status
    assert _read_all(ctrl_out) == []


def test_bad_pong_extension_provider_error_stays_nested(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue
    reply_to, replies = _requester_queue(make_queue, unique_tid, "bad-extension")
    task.register_pong_extension_provider(lambda: {"bad": {object()}})

    ctrl_in.write(
        encode_control_message(
            "PING",
            request_id="bad-extension",
            reply_to=reply_to,
        )
    )
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(replies)]
    ping_response = next(r for r in responses if r.get("command") == "PING")
    assert "error" in ping_response[PONG_EXTENSION_KEY]
    assert ping_response["message"] == "PONG"
    assert ping_response["task_status"] == task.taskspec.state.status
    assert _read_all(ctrl_out) == []


def test_pong_extension_provider_failure_is_local_but_fatal_exit_propagates(
    broker_env: BrokerEnv,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue
    reply_to, replies = _requester_queue(make_queue, unique_tid, "failing-extension")
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
            encode_control_message(
                "PING",
                request_id="failing-extension-provider",
                reply_to=reply_to,
            )
        )
        task.process_once()
        responses = [json.loads(message) for message in _read_all(replies)]
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
        assert _read_all(ctrl_out) == []

        task.register_pong_extension_provider(fail_fatal)
        with pytest.raises(FatalExtensionExit) as exc_info:
            task._pong_extension_fields()
        assert exc_info.value is fatal
    finally:
        task.cleanup()


def test_manager_ping_includes_manager_selection_fields(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
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
    ctrl_out = task._ctrl_out_queue
    request_id = "manager-probe-request"
    reply_to, replies = _requester_queue(make_queue, unique_tid, "manager-probe")

    try:
        ctrl_in.write(
            encode_control_message(
                "PING",
                request_id=request_id,
                reply_to=reply_to,
            )
        )
        task.process_once()

        responses = [json.loads(msg) for msg in _read_all(replies)]
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
        assert _read_all(ctrl_out) == []

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

        assert _read_all(ctrl_out) == []
        events = [
            json.loads(msg)
            for msg in make_queue(WEFT_GLOBAL_LOG_QUEUE).peek_generator()
        ]
        assert any(
            event.get("event") == "control_stop" and event.get("draining") is True
            for event in events
        ), events
    finally:
        task.cleanup()


def test_ping_includes_runtime_summary_from_runner_plugin(
    broker_env: BrokerEnv, unique_tid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    ctrl_out = task._ctrl_out_queue
    reply_to, replies = _requester_queue(make_queue, unique_tid, "runtime-summary")

    ctrl_in.write(
        encode_control_message(
            "PING",
            request_id="runtime-summary",
            reply_to=reply_to,
        )
    )
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(replies)]
    ping_response = next(r for r in responses if r.get("command") == "PING")
    assert ping_response["runtime"]["runner"] == "fake-runtime"
    assert ping_response["runtime"]["id"] == "runtime-1"
    assert ping_response["runtime"]["state"] == "running"
    assert ping_response["runtime"]["metadata"]["container_id"] == "abc123"
    assert ping_response["runtime"]["metadata"]["memory_usage_mb"] == 12.5
    assert _read_all(ctrl_out) == []


def test_paused_consumer_leaves_inbox_unreserved_and_resumes_on_next_turn(
    broker_env: BrokerEnv,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paused Consumer must not reserve/requeue inbox items and must re-probe on the turn after RESUME [CC-2.1], [CC-2.4], [WT-4.1]."""

    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    try:
        ctrl_in = make_queue(spec.io.control["ctrl_in"])
        inbox = make_queue(spec.io.inputs["inbox"])
        reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
        outbox = make_queue(spec.io.outputs["outbox"])
        reply_to, replies = _requester_queue(make_queue, unique_tid, "paused-probe")

        inbox_q = task._queues[spec.io.inputs["inbox"]].queue
        reserved_q = task._queues[f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}"].queue
        orig_inbox_move = inbox_q.move_one
        orig_reserved_move = reserved_q.move_one
        inbox_moves = 0
        reserved_moves = 0

        def count_inbox_move(*args: Any, **kwargs: Any) -> Any:
            nonlocal inbox_moves
            inbox_moves += 1
            return orig_inbox_move(*args, **kwargs)

        def count_reserved_move(*args: Any, **kwargs: Any) -> Any:
            nonlocal reserved_moves
            reserved_moves += 1
            return orig_reserved_move(*args, **kwargs)

        monkeypatch.setattr(inbox_q, "move_one", count_inbox_move)
        monkeypatch.setattr(reserved_q, "move_one", count_reserved_move)

        ctrl_in.write(encode_control_message("PAUSE", request_id="pause-1"))
        task.process_once()
        assert task._paused is True

        ctrl_in.write(
            encode_control_message(
                "PING", request_id="ping-paused-0", reply_to=reply_to
            )
        )
        task.wait_for_activity(timeout=0.02)
        task.process_once()

        pongs = [json.loads(msg) for msg in _read_all(replies)]
        assert len(pongs) == 1
        assert pongs[0]["request_id"] == "ping-paused-0"
        assert pongs[0]["paused"] is True

        inbox.write(json.dumps({"payload": "work"}))
        original_entry = inbox.peek_one(with_timestamps=True)
        assert original_entry is not None

        task._pending_messages_precheck_confirmed = True
        task.process_once()

        ctrl_in.write(
            encode_control_message(
                "PING", request_id="ping-paused-1", reply_to=reply_to
            )
        )
        task.wait_for_activity(timeout=0.02)
        task.process_once()

        pongs = [json.loads(msg) for msg in _read_all(replies)]
        assert len(pongs) == 1
        assert pongs[0]["request_id"] == "ping-paused-1"
        assert pongs[0]["paused"] is True
        assert inbox_moves == 0
        assert reserved_moves == 0
        assert reserved.peek_one() is None
        assert inbox.peek_one(with_timestamps=True) == original_entry

        ctrl_in.write(encode_control_message("RESUME", request_id="resume-1"))
        task.wait_for_activity(timeout=0.02)
        task.process_once()  # RESUME turn
        assert task._paused is False
        assert inbox.peek_one(with_timestamps=True) == original_entry

        # Delay the periodic inactive probe so only RESUME's immediate re-probe can activate inbox.
        task._next_inactive_probe_at = time.monotonic() + 60.0
        task.process_once()  # Immediate next turn reserves and starts work without advancing the clock
        assert inbox_moves == 1
        assert inbox.peek_one() is None

        _drive_task_until(task, lambda: outbox.peek_one() is not None)
        assert outbox.read_one() == "work"
    finally:
        task.cleanup()


def test_busy_consumer_leaves_second_item_in_inbox_and_processes_it_on_next_turn_after_completion(
    broker_env: BrokerEnv,
    unique_tid: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Busy Consumer must leave queued follow-up work in inbox and immediately re-probe after completion [CC-2.1], [WT-4.1]."""

    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:signal_ready_and_wait_for_release",
    )
    spec = spec.model_copy(
        update={"spec": spec.spec.model_copy(update={"persistent": True})}
    )
    task = Consumer(db_path, spec)
    try:
        ctrl_in = make_queue(spec.io.control["ctrl_in"])
        inbox = make_queue(spec.io.inputs["inbox"])
        reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
        outbox = make_queue(spec.io.outputs["outbox"])
        reply_to, replies = _requester_queue(make_queue, unique_tid, "busy-probe")

        ready_1 = tmp_path / "ready_1"
        release_1 = tmp_path / "release_1"
        ready_2 = tmp_path / "ready_2"
        release_2 = tmp_path / "release_2"

        inbox.write(
            json.dumps(
                {
                    "kwargs": {
                        "ready_path": str(ready_1),
                        "release_path": str(release_1),
                    }
                }
            )
        )
        task.process_once()
        assert task._active_work_in_flight is True
        drive_until(
            ready_1.exists,
            bool,
            step=lambda: None,
            wait=lambda timeout: time.sleep(min(timeout, 0.02)),
            timeout=5.0,
            wait_slice=0.02,
        )

        # Simulate an inactive-queue state while item 1 is running so item 2's
        # immediate start after item 1 completes depends on _handle_active_work_result.
        task._active_queues = []
        task._next_inactive_probe_at = time.monotonic() + 60.0

        inbox_q = task._queues[spec.io.inputs["inbox"]].queue
        reserved_q = task._queues[f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}"].queue
        orig_inbox_move = inbox_q.move_one
        orig_reserved_move = reserved_q.move_one
        inbox_moves = 0
        reserved_moves = 0

        def count_inbox_move(*args: Any, **kwargs: Any) -> Any:
            nonlocal inbox_moves
            inbox_moves += 1
            return orig_inbox_move(*args, **kwargs)

        def count_reserved_move(*args: Any, **kwargs: Any) -> Any:
            nonlocal reserved_moves
            reserved_moves += 1
            return orig_reserved_move(*args, **kwargs)

        monkeypatch.setattr(inbox_q, "move_one", count_inbox_move)
        monkeypatch.setattr(reserved_q, "move_one", count_reserved_move)

        inbox.write(
            json.dumps(
                {
                    "kwargs": {
                        "ready_path": str(ready_2),
                        "release_path": str(release_2),
                    }
                }
            )
        )
        second_entry = inbox.peek_one(with_timestamps=True)
        assert second_entry is not None

        ctrl_in.write(
            encode_control_message("PING", request_id="ping-busy-1", reply_to=reply_to)
        )
        task.process_once()

        pongs = [json.loads(msg) for msg in _read_all(replies)]
        assert len(pongs) == 1
        assert pongs[0]["request_id"] == "ping-busy-1"
        assert inbox_moves == 0
        assert reserved_moves == 0
        assert inbox.peek_one(with_timestamps=True) == second_entry

        # Release item 1 and wait for its worker result to arrive on the local queue.
        release_1.write_text("go", encoding="utf-8")
        drive_until(
            task._has_pending_worker_results,
            bool,
            step=lambda: None,
            wait=task.wait_for_activity,
            timeout=5.0,
            wait_slice=0.02,
        )
        task.process_once()  # Finalizes item 1 in _handle_active_work_result
        assert outbox.read_one() == "done"
        assert task._active_work_in_flight is False
        assert inbox.peek_one(with_timestamps=True) == second_entry

        # Next reactor turn must immediately probe and reserve item 2 without advancing the clock.
        task.process_once()
        assert inbox_moves == 1
        assert inbox.peek_one() is None
        assert task._active_work_in_flight is True

        release_2.write_text("go", encoding="utf-8")
        _drive_task_until(task, lambda: outbox.peek_one() is not None)
        assert outbox.read_one() == "done"
        assert reserved.peek_one() is None
    finally:
        for release_file in (
            tmp_path / "release_1",
            tmp_path / "release_2",
        ):
            release_file.write_text("go", encoding="utf-8")
        task.cleanup()


@pytest.fixture
def unique_tid() -> str:
    import time

    return str(time.time_ns())
