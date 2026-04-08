"""Tests for the interactive stream client used by the CLI."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from weft._constants import load_config
from weft.commands.interactive import InteractiveStreamClient
from weft.core.tasks import Consumer
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

INTERACTIVE_SCRIPT = str(
    (Path(__file__).resolve().parents[1] / "tasks" / "interactive_echo.py").resolve()
)


def _make_interactive_spec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="interactive-test",
        spec=SpecSection(
            type="command",
            process_target=sys.executable,
            args=["-u", INTERACTIVE_SCRIPT],
            interactive=True,
            stream_output=True,
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )


def _spin(task: Consumer, iterations: int = 20, delay: float = 0.05) -> None:
    for _ in range(iterations):
        task.process_once()
        time.sleep(delay)


def test_interactive_client_streams_and_completes(broker_env) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    spec = _make_interactive_spec(tid)
    task = Consumer(db_path, spec)

    # Ensure queues exist for watcher setup
    make_queue(spec.io.inputs["inbox"])
    make_queue(spec.io.outputs["outbox"])
    make_queue(spec.io.control["ctrl_out"])

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    state_statuses: list[str] = []

    config = load_config()
    client = InteractiveStreamClient(
        db_path=db_path,
        config=config,
        tid=tid,
        inbox=spec.io.inputs["inbox"],
        outbox=spec.io.outputs["outbox"],
        ctrl_out=spec.io.control["ctrl_out"],
        on_stdout=lambda chunk, _final: stdout_chunks.append(chunk),
        on_stderr=lambda chunk, _final: stderr_chunks.append(chunk),
        on_state=lambda event: state_statuses.append(str(event.get("status", ""))),
    )

    client.start()
    try:
        client.send_input("hello\n")
        _spin(task, iterations=8)

        client.send_input("quit\n")
        client.close_input()
        _spin(task, iterations=40)

        assert client.wait(timeout=5.0), "client did not signal completion"
    finally:
        client.stop()
        task.stop(join=False)

    combined_stdout = "".join(stdout_chunks)
    assert "echo: hello" in combined_stdout
    assert "goodbye" in combined_stdout
    assert "completed" in state_statuses
    assert client.status == "completed"
    assert client.error is None
    assert not [chunk for chunk in stderr_chunks if chunk.strip()]


def test_interactive_client_observes_terminal_ctrl_out_envelope(broker_env) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    spec = _make_interactive_spec(tid)

    make_queue(spec.io.inputs["inbox"])
    make_queue(spec.io.outputs["outbox"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    state_statuses: list[str] = []

    config = load_config()
    client = InteractiveStreamClient(
        db_path=db_path,
        config=config,
        tid=tid,
        inbox=spec.io.inputs["inbox"],
        outbox=spec.io.outputs["outbox"],
        ctrl_out=spec.io.control["ctrl_out"],
        on_state=lambda event: state_statuses.append(str(event.get("status", ""))),
    )

    client.start()
    try:
        ctrl_out.write(
            json.dumps(
                {
                    "type": "terminal",
                    "tid": tid,
                    "status": "completed",
                    "event": "work_completed",
                }
            )
        )

        assert client.wait(timeout=5.0), "client did not observe terminal envelope"
    finally:
        client.stop()

    assert "completed" in state_statuses
    assert client.status == "completed"


def test_interactive_client_failure_overrides_stdout_final(broker_env) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    spec = _make_interactive_spec(tid)

    outbox = make_queue(spec.io.outputs["outbox"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    config = load_config()
    client = InteractiveStreamClient(
        db_path=db_path,
        config=config,
        tid=tid,
        inbox=spec.io.inputs["inbox"],
        outbox=spec.io.outputs["outbox"],
        ctrl_out=spec.io.control["ctrl_out"],
    )

    client.start()
    try:
        outbox.write(
            json.dumps(
                {
                    "type": "stream",
                    "stream": "stdout",
                    "chunk": 0,
                    "final": True,
                    "encoding": "text",
                    "data": "",
                }
            )
        )
        assert not client.wait(timeout=0.1), (
            "stdout final marker should not be terminal"
        )

        ctrl_out.write(
            json.dumps(
                {
                    "type": "terminal",
                    "tid": tid,
                    "status": "failed",
                    "event": "work_failed",
                    "error": "boom",
                }
            )
        )

        assert client.wait(timeout=5.0), "client did not observe failed terminal event"
    finally:
        client.stop()

    assert client.status == "failed"
    assert client.error == "boom"


def test_interactive_client_waits_for_control_response(broker_env) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    spec = _make_interactive_spec(tid)

    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    config = load_config()
    client = InteractiveStreamClient(
        db_path=db_path,
        config=config,
        tid=tid,
        inbox=spec.io.inputs["inbox"],
        outbox=spec.io.outputs["outbox"],
        ctrl_out=spec.io.control["ctrl_out"],
    )

    client.start()
    try:
        ctrl_out.write(
            json.dumps(
                {
                    "command": "STOP",
                    "status": "ack",
                    "tid": tid,
                    "timestamp": time.time_ns(),
                }
            )
        )
        response = client.wait_for_control_response("STOP", status="ack", timeout=5.0)
    finally:
        client.stop()

    assert response is not None
    assert response["command"] == "STOP"
    assert response["status"] == "ack"


def test_interactive_client_control_stop_is_terminal(broker_env) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    spec = _make_interactive_spec(tid)

    make_queue(spec.io.outputs["outbox"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    state_statuses: list[str] = []

    config = load_config()
    client = InteractiveStreamClient(
        db_path=db_path,
        config=config,
        tid=tid,
        inbox=spec.io.inputs["inbox"],
        outbox=spec.io.outputs["outbox"],
        ctrl_out=spec.io.control["ctrl_out"],
        on_state=lambda event: state_statuses.append(str(event.get("status", ""))),
    )

    client.start()
    try:
        ctrl_out.write(
            json.dumps(
                {
                    "type": "terminal",
                    "tid": tid,
                    "status": "cancelled",
                    "event": "control_stop",
                    "error": "Task cancelled",
                }
            )
        )

        assert client.wait(timeout=5.0), "client did not observe control_stop"
    finally:
        client.stop()

    assert "cancelled" in state_statuses
    assert client.status == "cancelled"
    assert client.error == "Task cancelled"
