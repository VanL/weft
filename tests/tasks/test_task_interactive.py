"""Tests for interactive command streaming support."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from simplebroker import Queue
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, WEFT_STREAMING_SESSIONS_QUEUE
from weft.core.tasks import Consumer
from weft.core.tasks.base import BaseTask
from weft.core.taskspec import (
    IOSection,
    LimitsSection,
    SpecSection,
    StateSection,
    TaskSpec,
)

INTERACTIVE_SCRIPT = str(
    (Path(__file__).resolve().parent / "interactive_echo.py").resolve()
)


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


def make_interactive_spec(
    tid: str,
    *,
    script_path: str | None = None,
    limits: LimitsSection | None = None,
    polling_interval: float = 1.0,
) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="interactive-task",
        spec=SpecSection(
            type="command",
            process_target=sys.executable,
            args=["-u", script_path or INTERACTIVE_SCRIPT],
            interactive=True,
            stream_output=True,
            cleanup_on_exit=True,
            polling_interval=polling_interval,
            limits=limits or LimitsSection(),
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


def _instrument_streaming_queue(monkeypatch):
    writes: list[dict[str, object]] = []
    deletes: list[int | None] = []
    original_queue = BaseTask._queue
    proxies: dict[int, Queue] = {}

    class QueueProxy:
        def __init__(self, delegate: Queue) -> None:
            self._delegate = delegate

        def write(self, message: str) -> None:
            writes.append(json.loads(message))
            return self._delegate.write(message)

        def delete(self, *args, **kwargs) -> None:
            message_id = kwargs.get("message_id")
            if message_id is None and args:
                message_id = args[0]
            deletes.append(message_id)
            return self._delegate.delete(*args, **kwargs)

        def __getattr__(self, attr: str):
            return getattr(self._delegate, attr)

    def instrument(self, name: str) -> Queue:
        queue = original_queue(self, name)
        if name != WEFT_STREAMING_SESSIONS_QUEUE:
            return queue
        proxy = proxies.get(id(queue))
        if proxy is None:
            proxy = QueueProxy(queue)
            proxies[id(queue)] = proxy
        return proxy

    monkeypatch.setattr(BaseTask, "_queue", instrument, raising=False)
    return writes, deletes


def _is_final_marker(raw: str) -> bool:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("type") == "stream"
        and payload.get("final") is True
        and payload.get("data") in ("", None)
    )


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
    # Allow process to exit and finalize
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
    assert task.should_stop is True
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


def test_interactive_close_sentinel_purged_on_cleanup(
    broker_env, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_interactive_spec(unique_tid)
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    inbox.write(json.dumps({"stdin": "hello\\n"}))
    _spin(task)

    inbox.write(json.dumps({"close": True}))
    _spin(task, iterations=40)

    outbox_before = outbox.peek_many(limit=50) or []
    ctrl_before = ctrl_out.peek_many(limit=50) or []
    assert any(_is_final_marker(msg) for msg in outbox_before)
    assert any(_is_final_marker(msg) for msg in ctrl_before)

    task.cleanup()

    outbox_after = outbox.peek_many(limit=50) or []
    ctrl_after = ctrl_out.peek_many(limit=50) or []
    assert not any(_is_final_marker(msg) for msg in outbox_after)
    assert not any(_is_final_marker(msg) for msg in ctrl_after)

    task.stop(join=False)


def test_interactive_streaming_session_records(
    monkeypatch, broker_env, unique_tid: str
) -> None:
    writes, deletes = _instrument_streaming_queue(monkeypatch)

    db_path, make_queue = broker_env
    spec = make_interactive_spec(unique_tid)
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])

    inbox.write(json.dumps({"stdin": "hello\\n"}))
    _spin(task)

    inbox.write(json.dumps({"close": True}))
    _spin(task, iterations=40)

    task.cleanup()

    assert writes, "expected streaming session entry"
    session = writes[0]
    assert session["tid"] == unique_tid
    assert session["mode"] == "interactive"
    assert session["queue"] == spec.io.outputs["outbox"]
    assert session["ctrl_queue"] == spec.io.control["ctrl_out"]
    assert session["session_id"].startswith(
        f"{unique_tid}:{spec.io.outputs['outbox']}:"
    )
    assert deletes, "expected streaming session deletion"


def test_interactive_limit_marks_killed_without_terminal_transition_error(
    tmp_path: Path, broker_env, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    script = tmp_path / "interactive_limit.py"
    script.write_text(
        """
from __future__ import annotations

import sys
import time


def main() -> None:
    sys.stdin.readline()
    _data = [b"x" * (1024 * 1024) for _ in range(20)]
    time.sleep(5)


if __name__ == "__main__":
    main()
""".strip()
        + "\n",
        encoding="utf-8",
    )

    spec = make_interactive_spec(
        unique_tid,
        script_path=str(script),
        limits=LimitsSection(memory_mb=5),
        polling_interval=0.05,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    _drain(log_queue)

    try:
        inbox.write(json.dumps({"stdin": "go\n"}))
        for _ in range(200):
            task.process_once()
            if task.taskspec.state.status in {
                "failed",
                "killed",
                "timeout",
                "cancelled",
                "completed",
            }:
                break
            time.sleep(0.05)

        for _ in range(5):
            task.process_once()

        events = [json.loads(e) for e in _drain(log_queue)]
        assert task.taskspec.state.status == "killed"
        assert any(event["event"] == "work_limit_violation" for event in events)
        assert not any(event["event"] == "work_failed" for event in events)
    finally:
        task.stop(join=False)
