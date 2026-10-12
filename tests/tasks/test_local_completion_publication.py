"""Completion facts use the existing lifecycle path and survive task cleanup."""

from __future__ import annotations

import json
import multiprocessing
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.synchronize import Event
from typing import Any

import pytest

from simplebroker import BrokerTarget
from tests.helpers.typing import BrokerEnv
from tests.helpers.weft_harness import WeftTestHarness
from tests.tasks.test_task_execution import (
    make_command_taskspec,
    make_function_taskspec,
)
from weft._constants import TERMINAL_TASK_STATUSES, WEFT_GLOBAL_LOG_QUEUE
from weft.commands import tasks as task_cmd
from weft.core.launcher import _install_signal_handlers
from weft.core.tasks import Consumer
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared]


def _run_consumer_with_blocked_cancellation_publication(
    target: BrokerTarget,
    spec_json: str,
    ready: Event,
    completion_started: Event,
    release_completion: Event,
) -> None:
    """Delay the real broker transaction in a real controllable task process."""
    task = Consumer(target, TaskSpec.model_validate_json(spec_json))
    with task._queue(WEFT_GLOBAL_LOG_QUEUE).get_connection() as broker:
        broker_type = type(broker)
    real_broadcast = broker_type.broadcast

    def broadcast(db: Any, message: str, **kwargs: Any) -> int:
        payload = json.loads(message)
        if payload.get("type") == "terminal" and payload.get("status") == "cancelled":
            completion_started.set()
            if not release_completion.wait(15):
                raise RuntimeError("blocked completion transaction was not released")
        return real_broadcast(db, message, **kwargs)

    with pytest.MonkeyPatch.context() as blocked_broker:
        blocked_broker.setattr(broker_type, "broadcast", broadcast)
        _install_signal_handlers(task)
        ready.set()
        task.run_until_stopped()


def test_stop_cannot_see_terminal_status_before_completion_publication(
    broker_env: BrokerEnv,
    weft_harness: WeftTestHarness,
) -> None:
    """A cancellation command cannot kill its wrapper during completion I/O."""
    target, make_queue = broker_env
    tid = str(time.time_ns())
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
        cleanup_on_exit=True,
        weft_context=str(weft_harness.root),
    )
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    completion_started = context.Event()
    release_completion = context.Event()
    process = context.Process(
        target=_run_consumer_with_blocked_cancellation_publication,
        args=(
            target,
            spec.model_dump_json(),
            ready,
            completion_started,
            release_completion,
        ),
    )
    process.start()
    assert process.pid is not None
    weft_harness.register_pid(process.pid, tid)
    try:
        assert ready.wait(10)
        with ThreadPoolExecutor(max_workers=1) as executor:
            stopping = executor.submit(
                task_cmd.stop_task, tid, context=weft_harness.context
            )
            try:
                assert completion_started.wait(10)
                status = task_cmd._task_status(tid, context=weft_harness.context)
                assert status is not None
                assert status.status not in TERMINAL_TASK_STATUSES
                assert process.is_alive()
            finally:
                release_completion.set()
            stopping.result(timeout=10)
        process.join(timeout=10)
        assert not process.is_alive()
        facts = [
            json.loads(body)
            for body in make_queue(spec.io.control["ctrl_out"]).peek_generator()
        ]
        assert any(
            fact.get("type") == "terminal"
            and fact.get("source") == "task"
            and fact.get("status") == "cancelled"
            for fact in facts
        )
    finally:
        release_completion.set()
        if process.is_alive():
            process.kill()
        process.join(timeout=10)
        process.close()


@pytest.mark.parametrize("persistent", [False, True])
def test_completion_is_the_same_retained_fact_at_both_addresses(
    broker_env: BrokerEnv, persistent: bool
) -> None:
    db_path, make_queue = broker_env
    unique_tid = str(time.time_ns())
    spec = make_command_taskspec(
        unique_tid, sys.executable, args=["-c", "pass"], persistent=persistent
    )
    task = Consumer(db_path, spec)
    local = make_queue(spec.io.control["ctrl_out"])
    global_log = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    try:
        task.taskspec.mark_running()
        task._finalize_message(None, 0, None, None, initial_transition=True)
        expected_event = "work_item_completed" if persistent else "work_completed"
        global_facts = [
            raw
            for raw in global_log.peek_generator()
            if json.loads(raw).get("event") == expected_event
        ]
        local_facts = [
            raw
            for raw in local.peek_generator()
            if json.loads(raw).get("event") == expected_event
        ]
        assert len(global_facts) == 1
        assert local_facts == global_facts
        payload = json.loads(local_facts[0])
        assert (payload.get("type") == "terminal") is (not persistent)
        if not persistent:
            assert payload["source"] == "task"
            assert payload["return_code"] == 0
        task.cleanup()
        assert list(local.peek_generator()) == local_facts
    finally:
        task.cleanup()
