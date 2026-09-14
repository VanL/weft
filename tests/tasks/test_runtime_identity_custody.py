"""Worker identity custody contracts (Spec: [CC-3.2], [LIVENESS.R5])."""

from __future__ import annotations

import os
import signal
import time

import pytest

from tests.helpers.queue_payloads import terminal_envelopes
from tests.helpers.reactor_driver import drive_until
from tests.helpers.typing import BrokerEnv
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.core.task_state import task_state_queue_name
from weft.core.tasks import Consumer
from weft.core.tasks import base as base_module
from weft.core.taskspec import TaskSpec
from weft.ext import RunnerHandle
from weft.helpers import iter_queue_json_entries, process_create_time
from weft.liveness.analysis import analyze_liveness

pytestmark = pytest.mark.shared


@pytest.mark.parametrize("handler", [base_module.BaseTask, Consumer])
@pytest.mark.parametrize("kill", [False, True])
def test_completed_worker_releases_identity_and_idle_control(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    handler: type[base_module.BaseTask],
    kill: bool,
) -> None:
    if kill and not hasattr(signal, "SIGUSR1"):
        pytest.skip("SIGUSR1 is unavailable")
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    payload = make_function_taskspec(
        tid, "tests.tasks.sample_targets:echo_payload"
    ).model_dump(mode="json")
    payload["spec"]["persistent"] = True
    task = Consumer(db_path, TaskSpec.model_validate(payload))
    inbox = make_queue(task.taskspec.io.inputs["inbox"])
    log = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    mappings = make_queue(task_state_queue_name(tid))
    ctrl_out = make_queue(task.taskspec.io.control["ctrl_out"])
    try:
        inbox.write("hello")
        drive_until(
            lambda: any(
                row.get("event") == "work_item_completed"
                for row, _ in iter_queue_json_entries(log)
            ),
            bool,
            step=task.process_once,
            wait=task.wait_for_activity,
            timeout=10.0,
        )
        rows = [
            row
            for row, _ in iter_queue_json_entries(mappings)
            if row.get("full") == tid
        ]
        handle = RunnerHandle.from_dict(rows[-1]["runtime_handle"])
        assert handle.runner == "host"
        assert handle.kind == "process"
        assert handle.id == str(os.getpid())
        assert handle.control["authority"] == "host-pid"
        assert handle.scoped_host_processes() == (
            (os.getpid(), process_create_time(os.getpid())),
        )
        assert analyze_liveness(tid, rows[-1]).evidence == "live"
        assert not task._managed_pids
        assert task._runtime_handle is None
        calls = []
        monkeypatch.setattr(
            task,
            "_stop_registered_runtime_handle",
            lambda **kwargs: calls.append(kwargs),
        )
        monkeypatch.setattr(
            base_module,
            "terminate_verified_process_tree",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        handler.handle_termination_signal(
            task, signal.SIGUSR1 if kill else signal.SIGTERM
        )
        expected_status = "killed" if kill else "cancelled"
        assert task.taskspec.state.status == expected_status
        assert calls == []
        envelopes = terminal_envelopes(ctrl_out, tid=tid, source="task")
        assert len(envelopes) == 1
        assert envelopes[0]["status"] == expected_status
    finally:
        task.cleanup()
        inbox.close()
        log.close()
        mappings.close()
        ctrl_out.close()


def test_replacement_handle_preserves_registered_identity(
    broker_env: BrokerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, _ = broker_env
    task = Consumer(
        db_path,
        make_function_taskspec(
            str(time.time_ns()), "tests.tasks.sample_targets:echo_payload"
        ),
    )
    monkeypatch.setattr(base_module, "process_create_time", lambda pid: 111.0)
    try:
        task.register_managed_pid(123)
        monkeypatch.setattr(base_module, "process_create_time", lambda pid: 999.0)
        task.register_runtime_handle(
            RunnerHandle(
                runner="host",
                kind="process",
                id="456",
                control={"authority": "host-pid"},
                observations={
                    "host_pids": [456],
                    "host_processes": [{"pid": 456, "create_time": 222.0}],
                },
            )
        )
        assert task._runtime_handle is not None
        assert dict(task._runtime_handle.scoped_host_processes()) == {
            123: 111.0,
            456: 222.0,
        }
    finally:
        task.cleanup()
