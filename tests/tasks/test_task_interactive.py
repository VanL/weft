"""Tests for interactive command streaming support."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.core.tasks import Consumer
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

INTERACTIVE_SCRIPT = str(
    (Path(__file__).resolve().parent / "interactive_echo.py").resolve()
)


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


def make_interactive_spec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="interactive-task",
        spec=SpecSection(
            type="command",
            process_target=[sys.executable, "-u", INTERACTIVE_SCRIPT],
            interactive=True,
            stream_output=True,
            cleanup_on_exit=True,
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )


def _drain(queue) -> list[str]:
    items: list[str] = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        items.append(value)
    return items


def _spin(task: Consumer, iterations: int = 10, delay: float = 0.05) -> None:
    for _ in range(iterations):
        task.process_once()
        time.sleep(delay)


def test_interactive_command_streams_output(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_interactive_spec(unique_tid)
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    _drain(log_queue)

    inbox.write(json.dumps({"stdin": "hello\n"}))
    _spin(task)

    inbox.write(json.dumps({"stdin": "quit\n"}))
    # Allow process to exit and finalise
    _spin(task, iterations=30)

    stdout_messages = []
    while True:
        msg = outbox.read_one()
        if msg is None:
            break
        stdout_messages.append(json.loads(msg))

    combined_stdout = "".join(
        m.get("data", "") for m in stdout_messages if m.get("stream") == "stdout"
    )
    assert "echo: hello\n" in combined_stdout
    # Final envelope should be marked final
    assert stdout_messages[-1]["final"] is True

    # Ensure stderr stream closes cleanly
    ctrl_messages = []
    while True:
        msg = ctrl_out.read_one()
        if msg is None:
            break
        ctrl_messages.append(json.loads(msg))
    if ctrl_messages:
        assert ctrl_messages[-1]["final"] is True

    events = [json.loads(e) for e in _drain(log_queue)]
    assert any(event["event"] == "work_completed" for event in events)
    assert task.taskspec.state.status == "completed"
    task.stop(join=False)


def test_interactive_command_stop_cancels(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_interactive_spec(unique_tid)
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    outbox = make_queue(spec.io.outputs["outbox"])

    inbox.write(json.dumps({"stdin": "first\n"}))
    _spin(task)

    ctrl_in.write("STOP")
    _spin(task, iterations=20)

    final_messages = []
    while True:
        msg = outbox.read_one()
        if msg is None:
            break
        final_messages.append(json.loads(msg))

    assert final_messages
    assert final_messages[-1]["final"] is True
    assert task.taskspec.state.status == "cancelled"
    task.stop(join=False)
