"""Tests for the interactive stream client used by the CLI."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from weft._constants import WEFT_GLOBAL_LOG_QUEUE, load_config
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
            process_target=[sys.executable, "-u", INTERACTIVE_SCRIPT],
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
    make_queue(WEFT_GLOBAL_LOG_QUEUE)

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    state_events: list[str] = []

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
        on_state=lambda event: state_events.append(event.get("event", "")),
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
    assert "work_completed" in state_events
    assert client.status == "completed"
    assert client.error is None
    assert not [chunk for chunk in stderr_chunks if chunk.strip()]
