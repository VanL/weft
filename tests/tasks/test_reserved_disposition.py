"""Reserved rows have one disposition attempt, preserving failed operations [QUEUE.6]."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

import pytest

from simplebroker import BrokerTarget, Queue
from tests.helpers.queue_payloads import terminal_envelopes
from tests.tasks.test_task_execution import (
    _drive_consumer_until,
    make_function_taskspec,
)
from weft._constants import CONTROL_STOP
from weft.core.control_messages import encode_control_message
from weft.core.tasks import Consumer
from weft.core.taskspec import ReservedPolicy, TaskSpec


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


@pytest.mark.parametrize("outcome", ["success", "error"])
@pytest.mark.parametrize("delete_fails", [False, True])
def test_consumer_preserves_reserved_residue_after_single_disposition(
    broker_env: tuple[BrokerTarget, Callable[[str], Queue]],
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outcome: str,
    delete_fails: bool,
) -> None:
    db_path, queue = broker_env
    target = "echo_payload" if outcome == "success" else "fail_payload"
    spec = make_function_taskspec(
        unique_tid,
        f"tests.tasks.sample_targets:{target}",
        reserved_error=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)
    reserved = queue(f"T{unique_tid}.reserved")
    residue_id = reserved.write("older unrelated residue")
    delete_attempts: list[int] = []
    original_delete = reserved.delete

    def delete_once(*, message_id: int) -> bool:
        delete_attempts.append(message_id)
        if delete_fails and len(delete_attempts) == 1:
            raise OSError("injected exact delete failure")
        return original_delete(message_id=message_id)

    monkeypatch.setattr(reserved, "delete", delete_once)
    monkeypatch.setattr(task, "_get_reserved_queue", lambda: reserved)
    queue(spec.io.inputs["inbox"]).write(json.dumps({"args": ["payload"]}))
    status = "completed" if outcome == "success" else "failed"
    try:
        with caplog.at_level(logging.WARNING):
            _drive_consumer_until(task, lambda: task.taskspec.state.status == status)
        rows = list(reserved.peek_generator(with_timestamps=True))
        assert ("older unrelated residue", residue_id) in rows
        assert len(rows) == (2 if delete_fails else 1)
        assert len(delete_attempts) == 1
        if delete_fails:
            expected_warning = (
                "Failed to acknowledge reserved message"
                if outcome == "success"
                else "Failed to clear reserved message"
            )
            assert expected_warning in caplog.text
        terminals = terminal_envelopes(
            queue(spec.io.control["ctrl_out"]), tid=unique_tid, source="task"
        )
        assert len(terminals) == 1
        assert terminals[0]["status"] == status
        if outcome == "success":
            assert queue(spec.io.outputs["outbox"]).read_one() == "payload"
    finally:
        task.cleanup()


@pytest.mark.parametrize("field", ["stop", "error"])
@pytest.mark.parametrize("fake_manager_role", [False, True])
def test_consumer_rejects_requeue_even_with_manager_metadata(
    broker_env: tuple[BrokerTarget, Callable[[str], Queue]],
    unique_tid: str,
    field: str,
    fake_manager_role: bool,
) -> None:
    db_path, _queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.REQUEUE
        if field == "stop"
        else ReservedPolicy.KEEP,
        reserved_error=ReservedPolicy.REQUEUE
        if field == "error"
        else ReservedPolicy.KEEP,
    )
    if fake_manager_role:
        spec.metadata["role"] = "manager"
    with pytest.raises(ValueError, match="keep.*clear"):
        Consumer(db_path, spec)


def test_independent_idle_stop_may_clear_prior_failed_ack_residue(
    broker_env: tuple[BrokerTarget, Callable[[str], Queue]],
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later bulk CLEAR is a new disposition, not a retry of successful work."""
    db_path, queue = broker_env
    payload = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.CLEAR,
    ).model_dump()
    payload["spec"]["persistent"] = True
    spec = TaskSpec.model_validate(payload)
    task = Consumer(db_path, spec)
    reserved = queue(f"T{unique_tid}.reserved")
    outbox = queue(spec.io.outputs["outbox"])
    ctrl_out = queue(spec.io.control["ctrl_out"])
    delete_attempts: list[int] = []
    original_delete = reserved.delete

    def fail_first_delete(*, message_id: int) -> bool:
        delete_attempts.append(message_id)
        if len(delete_attempts) == 1:
            raise OSError("injected acknowledgement failure")
        return original_delete(message_id=message_id)

    monkeypatch.setattr(reserved, "delete", fail_first_delete)
    monkeypatch.setattr(task, "_get_reserved_queue", lambda: reserved)
    try:
        queue(spec.io.inputs["inbox"]).write(json.dumps({"args": ["payload"]}))
        _drive_consumer_until(task, outbox.has_pending)
        assert outbox.read_one() == "payload"
        assert task.taskspec.state.status == "running"
        assert reserved.has_pending()
        assert len(delete_attempts) == 1
        assert task._active_message_timestamp is None

        queue(spec.io.control["ctrl_in"]).write(encode_control_message(CONTROL_STOP))
        _drive_consumer_until(task, lambda: task.taskspec.state.status == "cancelled")
        assert not reserved.has_pending()
        assert len(delete_attempts) == 1
        responses = [json.loads(body) for body in ctrl_out.peek_generator()]
        assert sum(row.get("type") == "terminal" for row in responses) == 1
        assert any(
            row.get("command") == "STOP" and row.get("status") == "ack"
            for row in responses
        )
    finally:
        task.cleanup()
