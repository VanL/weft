"""Control-channel behaviour for Task implementations."""

from __future__ import annotations

import json

import pytest

from tests.tasks import sample_targets as targets  # noqa: F401
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import QUEUE_RESERVED_SUFFIX, WEFT_GLOBAL_LOG_QUEUE
from weft.core.manager import Manager
from weft.core.tasks import Consumer, Monitor, PipelineTask
from weft.core.tasks.base import TaskControlPolicy
from weft.core.tasks.consumer import Monitor as LegacyMonitor


def _read_all(queue):
    messages = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        messages.append(value)
    return messages


def test_pause_resume_control_flow(broker_env, unique_tid):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]
    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])

    ctrl_in.write("PAUSE")
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    all_responses = list(responses)
    assert any(r["command"] == "PAUSE" and r["status"] == "ack" for r in responses)

    inbox.write(json.dumps({"payload": "work"}))
    task.process_once()

    # Message should remain in inbox while paused
    assert inbox.peek_one() is not None
    assert outbox.read_one() is None

    ctrl_in.write("RESUME")
    task.process_once()
    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    all_responses.extend(responses)

    task.process_once()
    assert task._paused is False
    all_responses.extend(json.loads(msg) for msg in _read_all(ctrl_out))

    task.process_once()
    all_responses.extend(json.loads(msg) for msg in _read_all(ctrl_out))

    assert any(
        r["command"] == "RESUME" and r["status"] == "ack" for r in all_responses
    ), all_responses
    assert outbox.read_one() == "work"


def test_status_command_reports_state(broker_env, unique_tid):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]

    ctrl_in.write("STATUS")
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    status_response = next(r for r in responses if r["command"] == "STATUS")
    assert status_response["status"] == "ok"
    assert status_response["paused"] is False
    assert status_response["task_status"] == task.taskspec.state.status


def test_stop_command_sends_ack(broker_env, unique_tid):
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = task._ctrl_out_queue  # type: ignore[attr-defined]
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")

    reserved.write("work")

    ctrl_in.write("STOP")
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    stop_response = next(r for r in responses if r["command"] == "STOP")
    assert stop_response["status"] == "ack"
    assert task.should_stop is True


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

    ctrl_in.write("STOP")
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    stop_response = next(r for r in responses if r["command"] == "STOP")
    assert stop_response["status"] == "ack"
    assert task.taskspec.state.status == "completed"
    assert task.should_stop is True

    events = [json.loads(msg) for msg in _read_all(log_queue)]
    assert not any(event.get("event") == "control_stop" for event in events)


def test_stop_kill_overrides_declare_control_policy() -> None:
    overridden_classes = [Manager, PipelineTask, Monitor, LegacyMonitor]

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

    ctrl_in.write("PiNg")
    task.process_once()

    responses = [json.loads(msg) for msg in _read_all(ctrl_out)]
    ping_response = next(r for r in responses if r["command"] == "PING")
    assert ping_response["status"] == "ok"
    assert ping_response["message"] == "PONG"


@pytest.fixture
def unique_tid() -> str:
    import time

    return str(time.time_ns())
