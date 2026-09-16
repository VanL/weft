"""Tests for interactive command streaming support."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from simplebroker import Queue
from tests.helpers.reactor_driver import drive_until
from tests.helpers.typing import BrokerEnv
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import (
    QUEUE_RESERVED_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
)
from weft.core.control_messages import ControlRequest, encode_control_message
from weft.core.task_evidence import coerce_terminal_envelope
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


def _drain(queue: Queue) -> list[str]:
    items: list[str] = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        items.append(value)
    return items


def _drive_interactive(task: Consumer, ready: Callable[[], bool]) -> None:
    drive_until(
        ready,
        bool,
        step=task.process_once,
        wait=task.wait_for_activity,
        timeout=10.0,
        diagnostics=lambda: task.taskspec.state.status,
    )


def _finished(task: Consumer) -> bool:
    return task.should_stop and task._interactive_session is None


def _instrument_streaming_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, object]], list[int | str]]:
    writes: list[dict[str, object]] = []
    deletes: list[int | str] = []
    original_queue = BaseTask._queue
    proxies: dict[int, QueueProxy] = {}

    class QueueProxy:
        def __init__(self, delegate: Queue) -> None:
            self._delegate = delegate

        def write(self, message: str) -> int:
            writes.append(json.loads(message))
            return self._delegate.write(message)

        def delete(self, message_id: int | str) -> bool:
            deletes.append(message_id)
            return self._delegate.delete(message_id=message_id)

        def __getattr__(self, attr: str) -> object:
            return getattr(self._delegate, attr)

    def instrument(self: BaseTask, name: str) -> Queue:
        queue = original_queue(self, name)
        if name != WEFT_STREAMING_SESSIONS_QUEUE:
            return queue
        proxy = proxies.get(id(queue))
        if proxy is None:
            proxy = QueueProxy(queue)
            proxies[id(queue)] = proxy
        return cast(
            Queue, proxy
        )  # Queue facade delegates all operations except recorded writes/deletes.

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


def test_interactive_command_streams_output(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_interactive_spec(unique_tid)
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    _drain(log_queue)

    inbox.write(json.dumps({"stdin": "hello\n"}))
    _drive_interactive(task, lambda: task._interactive_session is not None)

    inbox.write(json.dumps({"stdin": "quit\n"}))
    # Allow process to exit and finalize
    _drive_interactive(task, lambda: _finished(task))

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
    assert any(
        message.get("type") == "stream"
        and message.get("stream") == "stderr"
        and message.get("final") is True
        for message in ctrl_messages
    )
    terminal = next(
        message
        for message in ctrl_messages
        if message.get("type") == "terminal" and message.get("status") == "completed"
    )
    assert terminal["source"] == "task"
    assert terminal["tid"] == unique_tid
    assert isinstance(terminal["timestamp"], int)
    assert coerce_terminal_envelope(json.dumps(terminal), tid=unique_tid) == terminal

    events = [json.loads(e) for e in _drain(log_queue)]
    assert any(event["event"] == "work_completed" for event in events)
    assert task.taskspec.state.status == "completed"
    assert task.should_stop is True
    task.stop(join=False)


class _TerminalWriteQueue:
    """Keep queue behavior real while failing one terminal write before commit."""

    def __init__(self, delegate: Queue, *, fail_once: bool) -> None:
        self.delegate = delegate
        self.fail_once = fail_once
        self.terminal_attempts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def write(self, message: str) -> int:
        if json.loads(message).get("type") == "terminal":
            self.terminal_attempts += 1
            if self.fail_once and self.terminal_attempts == 1:
                raise RuntimeError("injected terminal write failure")
        return self.delegate.write(message)


@pytest.mark.parametrize("command", ["STOP", "KILL"])
@pytest.mark.parametrize("started", [False, True], ids=["before-input", "active"])
@pytest.mark.parametrize("fail_once", [False, True], ids=["write-ok", "write-retry"])
def test_interactive_command_control_unwinds_before_terminal_and_ack(
    broker_env: BrokerEnv, unique_tid: str, command: str, started: bool, fail_once: bool
) -> None:
    db_path, make_queue = broker_env
    spec = make_interactive_spec(unique_tid)
    task = Consumer(db_path, spec)
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    writer = _TerminalWriteQueue(task._ctrl_out_queue, fail_once=fail_once)
    task._ctrl_out_queue = cast(Queue, writer)
    session = None
    try:
        if started:
            make_queue(spec.io.inputs["inbox"]).write(json.dumps({"stdin": "first\n"}))
            drive_until(
                lambda: task._interactive_session,
                lambda value: value is not None,
                step=task.process_once,
                wait=task.wait_for_activity,
                timeout=10,
            )
            session = task._interactive_session
        else:
            # Exercise a launched task process before its first input, not an
            # unlaunched created spec (created -> killed is intentionally invalid).
            task.taskspec.mark_started()
        request_id = f"interactive-{command.lower()}"
        make_queue(spec.io.control["ctrl_in"]).write(
            encode_control_message(command, request_id=request_id)
        )
        drive_until(
            lambda: task.should_stop,
            bool,
            step=task.process_once,
            wait=task.wait_for_activity,
            timeout=10,
        )
        rows = [
            (json.loads(raw), timestamp)
            for raw, timestamp in ctrl_out.peek_generator(with_timestamps=True)
        ]
        terminals = [
            (row, timestamp) for row, timestamp in rows if row.get("type") == "terminal"
        ]
        assert len(terminals) == 1
        terminal, terminal_timestamp = terminals[0]
        expected_status = "cancelled" if command == "STOP" else "killed"
        assert terminal["status"] == expected_status
        assert terminal["source"] == "task"
        assert terminal["tid"] == unique_tid
        assert set(terminal) == {
            "type",
            "source",
            "tid",
            "status",
            "timestamp",
            "error",
        }
        acknowledgements = [
            (row, timestamp)
            for row, timestamp in rows
            if row.get("command") == command and row.get("status") == "ack"
        ]
        assert len(acknowledgements) == 1
        assert acknowledgements[0][0]["request_id"] == request_id
        assert terminal_timestamp < acknowledgements[0][1]
        assert writer.terminal_attempts == (2 if fail_once else 1)
        if session is not None:
            assert not session.is_alive()
            assert task._interactive_session is None
            stderr_finals = [
                timestamp
                for row, timestamp in rows
                if row.get("stream") == "stderr" and row.get("final")
            ]
            stdout_finals = [
                timestamp
                for raw, timestamp in make_queue(
                    spec.io.outputs["outbox"]
                ).peek_generator(with_timestamps=True)
                if _is_final_marker(raw)
            ]
            assert len(stderr_finals) == len(stdout_finals) == 1
            assert stderr_finals[0] < terminal_timestamp
            assert stdout_finals[0] < terminal_timestamp
        # The status, not a new emission ledger, prevents a repeated command
        # from re-emitting terminal proof after the session has been released.
        task._interactive_handle_control(
            ControlRequest(command=command, request_id="repeat")
        )
        assert (
            sum(
                json.loads(raw).get("type") == "terminal"
                for raw in ctrl_out.peek_generator()
            )
            == 1
        )
    finally:
        task.cleanup()


@pytest.mark.parametrize("fail_once", [False, True])
def test_interactive_session_start_failure_uses_canonical_terminal_writer(
    broker_env: BrokerEnv, tmp_path: Path, unique_tid: str, fail_once: bool
) -> None:
    db_path, make_queue = broker_env
    payload = make_interactive_spec(unique_tid).model_dump(mode="json")
    payload["spec"]["working_dir"] = str(tmp_path / "missing-directory")
    task = Consumer(db_path, TaskSpec.model_validate(payload))
    ctrl_out = make_queue(task.taskspec.io.control["ctrl_out"])
    writer = _TerminalWriteQueue(task._ctrl_out_queue, fail_once=fail_once)
    task._ctrl_out_queue = cast(Queue, writer)
    ordinary = Consumer(
        db_path,
        make_function_taskspec(
            str(time.time_ns()), "tests.tasks.sample_targets:echo_payload"
        ),
    )
    try:
        with pytest.raises(OSError):
            task._interactive_ensure_session(1)
        assert task.taskspec.state.status == "failed"
        ordinary.taskspec.mark_failed(error=task.taskspec.state.error)
        ordinary._send_terminal_envelope()
        expected = next(
            json.loads(raw)
            for raw in make_queue(
                ordinary.taskspec.io.control["ctrl_out"]
            ).peek_generator()
            if json.loads(raw).get("type") == "terminal"
        )
        task._interactive_shutdown()
        task._interactive_handle_control(
            ControlRequest(command="STOP", request_id="after-start-failure")
        )
        terminals = [
            json.loads(raw)
            for raw in ctrl_out.peek_generator()
            if json.loads(raw).get("type") == "terminal"
        ]
        assert len(terminals) == 1
        assert set(terminals[0]) == set(expected)
        assert terminals[0]["status"] == "failed"
        assert terminals[0]["tid"] == unique_tid
        assert writer.terminal_attempts == (2 if fail_once else 1)
    finally:
        task.cleanup()
        ordinary.cleanup()


def test_interactive_command_routes_stderr_and_reports_failure(
    tmp_path: Path, broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    script = tmp_path / "interactive_failure.py"
    script.write_text(
        """
from __future__ import annotations

import sys


def main() -> int:
    sys.stdin.readline()
    sys.stdout.write("stdout-before-fail\\n")
    sys.stdout.flush()
    sys.stderr.write("stderr-before-fail\\n")
    sys.stderr.flush()
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
""".strip()
        + "\n",
        encoding="utf-8",
    )

    spec = make_interactive_spec(unique_tid, script_path=str(script))
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    _drain(log_queue)

    try:
        inbox.write(json.dumps({"stdin": "go\n"}))
        _drive_interactive(task, lambda: _finished(task))

        stdout_messages = [json.loads(msg) for msg in _drain(outbox)]
        ctrl_messages = [json.loads(msg) for msg in _drain(ctrl_out)]
        events = [json.loads(msg) for msg in _drain(log_queue)]

        stdout_combined = "".join(
            message.get("data", "")
            for message in stdout_messages
            if message.get("stream") == "stdout"
        )
        stderr_combined = "".join(
            message.get("data", "")
            for message in ctrl_messages
            if message.get("stream") == "stderr"
        )

        assert "stdout-before-fail" in stdout_combined
        assert "stderr-before-fail" in stderr_combined
        assert stdout_messages[-1]["final"] is True
        assert any(
            message.get("type") == "stream"
            and message.get("stream") == "stderr"
            and message.get("final") is True
            for message in ctrl_messages
        )
        assert any(
            message.get("type") == "terminal" and message.get("status") == "failed"
            for message in ctrl_messages
        )
        assert task.taskspec.state.status == "failed"
        assert any(event["event"] == "work_failed" for event in events)
        assert not any(event["event"] == "work_completed" for event in events)
    finally:
        task.stop(join=False)


def test_interactive_control_commands_report_live_status(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_interactive_spec(unique_tid)
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    try:
        inbox.write(json.dumps({"stdin": "hello\n"}))
        _drive_interactive(task, lambda: task._interactive_session is not None)

        ctrl_in.write(encode_control_message("STATUS"))
        ctrl_in.write(encode_control_message("PING"))
        responses: list[dict[str, object]] = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            task.process_once()
            task.wait_for_activity(timeout=0.02)
            responses = [json.loads(msg) for msg in ctrl_out.peek_generator()]
            if any(r.get("command") == "STATUS" for r in responses) and any(
                r.get("command") == "PING" for r in responses
            ):
                break

        status_response = next(r for r in responses if r.get("command") == "STATUS")
        ping_response = next(r for r in responses if r.get("command") == "PING")

        assert status_response["status"] == "ok"
        assert status_response["task_status"] == "running"
        assert ping_response["status"] == "ok"
        assert ping_response["message"] == "PONG"
    finally:
        inbox.write(json.dumps({"stdin": "quit\n"}))
        _drive_interactive(task, lambda: _finished(task))
        task.stop(join=False)


def test_interactive_late_input_is_dropped_after_completion(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_interactive_spec(unique_tid)
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")

    try:
        inbox.write(json.dumps({"stdin": "hello\n"}))
        _drive_interactive(task, lambda: task._interactive_session is not None)
        inbox.write(json.dumps({"stdin": "quit\n"}))
        _drive_interactive(task, lambda: _finished(task))

        baseline_messages = [json.loads(msg) for msg in _drain(outbox)]
        baseline_stdout = "".join(
            message.get("data", "")
            for message in baseline_messages
            if message.get("stream") == "stdout"
        )
        assert "goodbye" in baseline_stdout
        assert task.taskspec.state.status == "completed"

        inbox.write(json.dumps({"stdin": "after\n"}))
        task.process_once()

        late_messages = [json.loads(msg) for msg in _drain(outbox)]
        late_stdout = "".join(
            message.get("data", "")
            for message in late_messages
            if message.get("stream") == "stdout"
        )
        assert "after" not in late_stdout
        assert reserved.peek_one() is None
        assert task.taskspec.state.status == "completed"
    finally:
        task.stop(join=False)


def test_interactive_close_sentinel_purged_on_cleanup(
    broker_env: BrokerEnv, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_interactive_spec(unique_tid)
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    inbox.write(json.dumps({"stdin": "hello\\n"}))
    _drive_interactive(task, lambda: task._interactive_session is not None)

    inbox.write(json.dumps({"close": True}))
    _drive_interactive(task, lambda: _finished(task))

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
    monkeypatch: pytest.MonkeyPatch, broker_env: BrokerEnv, unique_tid: str
) -> None:
    writes, deletes = _instrument_streaming_queue(monkeypatch)

    db_path, make_queue = broker_env
    spec = make_interactive_spec(unique_tid)
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])

    inbox.write(json.dumps({"stdin": "hello\\n"}))
    _drive_interactive(task, lambda: task._interactive_session is not None)

    inbox.write(json.dumps({"close": True}))
    _drive_interactive(task, lambda: _finished(task))

    task.cleanup()

    assert writes, "expected streaming session entry"
    session = writes[0]
    assert session["tid"] == unique_tid
    assert session["mode"] == "interactive"
    assert session["queue"] == spec.io.outputs["outbox"]
    assert session["ctrl_queue"] == spec.io.control["ctrl_out"]
    assert isinstance(session["session_id"], str)
    assert session["session_id"].startswith(
        f"{unique_tid}:{spec.io.outputs['outbox']}:"
    )
    assert deletes, "expected streaming session deletion"


def test_interactive_limit_marks_killed_without_terminal_transition_error(
    tmp_path: Path, broker_env: BrokerEnv, unique_tid: str
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
