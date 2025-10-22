"""Tests for the Debugger interactive task."""

from __future__ import annotations

import json

import pytest

from weft.core.tasks.debugger import Debugger
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec


def make_debugger_spec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="debugger",
        spec=SpecSection(
            type="command",
            process_target=["debugger"],
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


def _enqueue_command(context, queue_name: str, command: dict[str, str]) -> None:
    queue = context(queue_name)
    payload = {
        "stdin": json.dumps(command),
        "close": True,
    }
    queue.write(json.dumps(payload))


@pytest.fixture
def tid() -> str:
    import time

    return str(time.time_ns())


def test_debugger_ping(broker_env, tid) -> None:
    db_path, make_queue = broker_env
    spec = make_debugger_spec(tid)
    dbg = Debugger(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])

    inbox.write(json.dumps({"stdin": json.dumps({"command": "ping"}), "close": True}))

    dbg.process_once()
    dbg._interactive_flush_outputs()

    message = outbox.read_one()
    assert message is not None
    envelope = json.loads(message)
    payload = json.loads(envelope["data"])
    assert payload["command"] == "ping"
    assert payload["response"] == "pong"
    dbg.cleanup()


def test_debugger_info(broker_env, tid) -> None:
    db_path, make_queue = broker_env
    spec = make_debugger_spec(tid)
    dbg = Debugger(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])

    inbox.write(json.dumps({"stdin": json.dumps({"command": "info"}), "close": True}))

    dbg.process_once()
    dbg._interactive_flush_outputs()

    message = outbox.read_one()
    assert message is not None
    envelope = json.loads(message)
    payload = json.loads(envelope["data"])
    assert payload["command"] == "info"
    assert payload["tid"] == tid
    assert payload["name"] == "debugger"
    dbg.cleanup()
