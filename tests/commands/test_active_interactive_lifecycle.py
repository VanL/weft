"""Active interactive CLI shutdown and stream custody [CLI-1.1.1], [MF-5]."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from typer.testing import CliRunner

from simplebroker import Queue
from simplebroker.ext import BrokerError
from tests.helpers.reactor_driver import drive_until
from tests.helpers.weft_harness import WeftTestHarness
from tests.tasks.test_task_interactive import make_interactive_spec
from weft._constants import (
    INTERACTIVE_STOP_COMPLETION_TIMEOUT,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TASK_STATE_QUEUE_PREFIX,
)
from weft._exceptions import CommandExecutionError
from weft.cli import app as cli_app
from weft.cli import run as cli_run
from weft.commands import _task_history
from weft.commands import events as event_commands
from weft.commands import run as run_commands
from weft.commands import tasks as task_commands
from weft.commands.run import _LiveRunSession, _stop_interactive_run_session
from weft.commands.types import RunExecutionResult
from weft.context import WeftContext
from weft.core import task_evidence
from weft.core.control_messages import parse_control_request
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.tasks import Consumer
from weft.core.tasks.sessions import CommandSession
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared, pytest.mark.timeout(20)]


@contextmanager
def _live_session(
    harness: WeftTestHarness,
    *,
    script_path: str | None = None,
    inbox_name: str | None = None,
    probe: _IdleOutputFaultProbe | None = None,
) -> Iterator[_LiveRunSession]:
    with harness.context.queue(
        "test.active.interactive.tid", persistent=False
    ) as queue:
        tid = str(queue.generate_timestamp())
    session = _LiveRunSession(
        RunExecutionResult(tid=tid),
        harness.context,
        _accepted_taskspec=_interactive_spec(
            tid, script_path=script_path, inbox_name=inbox_name
        ),
    )
    try:
        yield session
    finally:
        if probe is not None and any(
            thread.is_alive() for thread in probe.output_threads
        ):
            # Unexpected test failure must still settle the real output owner.
            with _stop_peer(harness, tid) as consumer:
                _publish_stopped_peer(harness, consumer)
            for monitor in probe.idle_monitors:
                probe.original_wake(monitor)
            for thread in probe.output_threads:
                probe.original_join(thread, timeout=5)
                assert not thread.is_alive()
        session.close()


def _interactive_spec(
    tid: str, *, script_path: str | None = None, inbox_name: str | None = None
) -> TaskSpec:
    payload = make_interactive_spec(
        tid, script_path=script_path, enable_process_title=False
    ).model_dump(mode="json")
    payload["spec"]["cleanup_on_exit"] = False
    if inbox_name is not None:
        payload["io"]["inputs"]["inbox"] = inbox_name
    return TaskSpec.model_validate(payload)


def _retire_inline_runtime(harness: WeftTestHarness, tid: str) -> None:
    # The producer ran inline, so its PID remains live after task cleanup.
    # Retire that runtime claim through public exact deletes before observation.
    with harness.context.queue(
        f"{WEFT_TASK_STATE_QUEUE_PREFIX}{tid}", persistent=False
    ) as runtime:
        for _raw, message_id in list(runtime.peek_generator(with_timestamps=True)):
            assert runtime.delete(message_id=message_id)


@contextmanager
def _stop_peer(harness: WeftTestHarness, tid: str) -> Iterator[Consumer]:
    consumer = Consumer(
        harness.context.broker_target,
        _interactive_spec(tid),
        config=harness.context.broker_config,
    )
    with consumer.drive_scope():
        _retire_inline_runtime(harness, tid)
        yield consumer


def _publish_stopped_peer(harness: WeftTestHarness, consumer: Consumer) -> str:
    """Produce real cancellation without an ACK or a live inline runtime handle."""
    consumer._handle_stop_request(
        reason="STOP requested",
        event="control_stop",
        message_id=None,
        apply_reserved_policy=True,
    )
    _retire_inline_runtime(harness, consumer.tid)
    with harness.context.queue(f"T{consumer.tid}.ctrl_out", persistent=False) as reply:
        terminals = [
            raw
            for raw in reply.peek_generator()
            if (payload := json.loads(raw)).get("type") == "terminal"
            and payload.get("source") == "task"
            and payload.get("tid") == consumer.tid
            and payload.get("status") == "cancelled"
        ]
        assert terminals
        return terminals[-1]


def _complete_after_eof(
    harness: WeftTestHarness,
    tid: str,
    script: Path,
    inbox_name: str | None,
    *,
    observed_stderr: threading.Event | None = None,
) -> None:
    consumer = Consumer(
        harness.context.broker_target,
        _interactive_spec(tid, script_path=str(script), inbox_name=inbox_name),
        config=harness.context.broker_config,
    )
    with consumer.drive_scope():
        drive_until(
            lambda: consumer.taskspec.state.status,
            lambda status: status in {"completed", "failed"},
            step=consumer.process_once,
            wait=consumer.wait_for_activity,
            timeout=10,
        )
        if observed_stderr is not None:
            # ctrl_out is runtime-only and standard control queues are removed
            # at drive-scope exit. Keep actual published frames observable until
            # the real CLI renderer has read them, rather than racing cleanup.
            assert observed_stderr.wait(10)


class _IdleOutputFaultProbe:
    """Observe actual wake, read and teardown boundaries without replacing them."""

    def __init__(self) -> None:
        self.main_thread = threading.get_ident()
        self.idle = threading.Event()
        self.failed = threading.Event()
        self.cancelled = threading.Event()
        self.joining = threading.Event()
        self.idle_monitors: list[QueueChangeMonitor] = []
        self.output_threads: list[threading.Thread] = []
        self.post_cancel_reads: list[str] = []
        self.closed_on: list[tuple[str, int]] = []
        self.original_wait = QueueChangeMonitor.wait
        self.original_wake = QueueChangeMonitor.wake
        self.original_history = task_evidence.read_task_history
        self.original_close = Queue.close
        self.original_join = threading.Thread.join

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            QueueChangeMonitor,
            "wait",
            lambda monitor, timeout: self.wait(monitor, timeout),
        )
        monkeypatch.setattr(
            QueueChangeMonitor, "wake", lambda monitor: self.wake(monitor)
        )
        monkeypatch.setattr(Queue, "close", lambda queue: self.close(queue))
        monkeypatch.setattr(
            threading.Thread,
            "join",
            lambda thread, timeout=None: self.join(thread, timeout),
        )
        for owner in (task_evidence, _task_history, event_commands):
            monkeypatch.setattr(owner, "read_task_history", self.history)

    def wait(self, monitor: QueueChangeMonitor, timeout: float | None) -> bool:
        if threading.get_ident() != self.main_thread:
            if monitor not in self.idle_monitors:
                self.idle_monitors.append(monitor)
            if threading.current_thread() not in self.output_threads:
                self.output_threads.append(threading.current_thread())
            self.idle.set()
        return self.original_wait(monitor, timeout)

    def history(self, *args: Any, **kwargs: Any) -> Any:
        if self.cancelled.is_set() and threading.get_ident() != self.main_thread:
            self.post_cancel_reads.append("history")
        return self.original_history(*args, **kwargs)

    def wake(self, monitor: QueueChangeMonitor) -> None:
        if (
            self.failed.is_set()
            and threading.get_ident() == self.main_thread
            and monitor in self.idle_monitors
        ):
            self.cancelled.set()
        self.original_wake(monitor)

    def close(self, queue: Queue) -> None:
        self.closed_on.append((queue.name, threading.get_ident()))
        self.original_close(queue)

    def join(self, thread: threading.Thread, timeout: float | None) -> None:
        if thread in self.output_threads:
            self.joining.set()
        self.original_join(thread, timeout)


@pytest.mark.parametrize("quit_command", [":quit", ":exit"])
def test_active_cli_quit_sends_keyed_stop_and_keeps_terminal_proof(
    monkeypatch: pytest.MonkeyPatch,
    quit_command: str,
) -> None:
    """Exercise the active CLI, with a real broker peer replying without ACK."""
    original_write = Queue.write
    requests: list[str] = []
    probe = _IdleOutputFaultProbe()
    probe.install(monkeypatch)
    with (
        WeftTestHarness() as harness,
        _live_session(harness, probe=probe) as session,
        _stop_peer(harness, session.tid) as consumer,
    ):
        control_in = f"T{session.tid}.ctrl_in"
        terminal: list[str] = []

        def write(queue: Queue, message: str, **kwargs: Any) -> int:
            message_id = original_write(queue, message, **kwargs)
            if queue.name == control_in:
                requests.append(message)
                terminal.append(_publish_stopped_peer(harness, consumer))
            return message_id

        monkeypatch.setattr(Queue, "write", write)
        with create_pipe_input() as pipe:
            prompt: PromptSession[str] = PromptSession(input=pipe, output=DummyOutput())
            monkeypatch.setattr("prompt_toolkit.PromptSession", lambda: prompt)
            pipe.send_text(f"{quit_command}\n")
            cli_run.drive_interactive_session(session, None)
        parsed = [parse_control_request(raw) for raw in requests]
        assert len(parsed) == 1
        assert parsed[0] is not None
        assert parsed[0].command == "STOP"
        assert parsed[0].request_id
        with harness.context.queue(
            f"T{session.tid}.ctrl_out", persistent=False
        ) as reply:
            assert reply.peek_one() == terminal[0]


def test_active_cli_stop_failure_cancels_idle_output_before_join(
    monkeypatch: pytest.MonkeyPatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A synchronized peer keeps the old blocked join finite and exposes its reads."""
    probe = _IdleOutputFaultProbe()
    probe.install(monkeypatch)
    original_write = Queue.write
    with WeftTestHarness() as harness, _live_session(harness, probe=probe) as session:
        control_in = f"T{session.tid}.ctrl_in"

        def write(queue: Queue, message: str, **kwargs: Any) -> int:
            if queue.name == control_in:
                probe.failed.set()
                raise BrokerError("injected STOP write failure")
            return original_write(queue, message, **kwargs)

        monkeypatch.setattr(Queue, "write", write)
        with create_pipe_input() as pipe:
            prompt: PromptSession[str] = PromptSession(input=pipe, output=DummyOutput())
            monkeypatch.setattr("prompt_toolkit.PromptSession", lambda: prompt)

            ready = threading.Event()

            def peer() -> None:
                with _stop_peer(harness, session.tid) as consumer:
                    ready.set()
                    try:
                        assert probe.idle.wait(10)
                        pipe.send_text(":quit\n")
                        assert probe.failed.wait(10)
                        assert probe.joining.wait(10)
                    finally:
                        # This actual publication lets the old blocked join
                        # finish. A cancelled observer must never read it.
                        _publish_stopped_peer(harness, consumer)

            thread = threading.Thread(target=peer)
            thread.start()
            try:
                assert ready.wait(10)
                with pytest.raises(
                    CommandExecutionError, match="injected STOP write failure"
                ):
                    cli_run.drive_interactive_session(session, None)
            finally:
                probe.failed.set()
                thread.join(timeout=10)
                assert not thread.is_alive()
        assert probe.cancelled.is_set(), (
            "STOP failure must actively wake the idle observer"
        )
        assert not probe.post_cancel_reads
        assert any(
            name == WEFT_GLOBAL_LOG_QUEUE and owner != probe.main_thread
            for name, owner in probe.closed_on
        )
        assert thread_exception_guard == []


@pytest.mark.parametrize("inbox_name", [None, "accepted.interactive.input"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_active_cli_eof_closes_input_and_renders_final_frames_without_stop(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    thread_exception_guard: list[threading.ExceptHookArgs],
    inbox_name: str | None,
    exit_code: int,
) -> None:
    """EOF remains input closure and legitimate task completion, not forced STOP."""
    original_write = Queue.write
    controls: list[str] = []
    input_messages: list[str] = []
    input_closed = threading.Event()
    observed_stderr = threading.Event()
    original_echo = cli_run.typer.echo

    def echo(message: Any = None, **kwargs: Any) -> None:
        original_echo(message, **kwargs)
        if kwargs.get("err") and "final stderr\n" in str(message):
            observed_stderr.set()

    monkeypatch.setattr(cli_run.typer, "echo", echo)
    probe = _IdleOutputFaultProbe()
    probe.install(monkeypatch)
    script = tmp_path / "finish_on_eof.py"
    script.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('final stdout', flush=True)\n"
        "print('final stderr', file=sys.stderr, flush=True)\n"
        f"sys.exit({exit_code})\n"
    )
    with (
        WeftTestHarness() as harness,
        _live_session(
            harness, script_path=str(script), inbox_name=inbox_name, probe=probe
        ) as session,
    ):
        inbox = inbox_name or f"T{session.tid}.inbox"
        monkeypatch.setenv("BROKER_CACHE_MB", "not-an-integer")

        def write(queue: Queue, message: str, **kwargs: Any) -> int:
            message_id = original_write(queue, message, **kwargs)
            if queue.name == f"T{session.tid}.ctrl_in":
                controls.append(message)
            if queue.name == inbox:
                input_messages.append(message)
                if json.loads(message) == {"close": True}:
                    input_closed.set()
            return message_id

        monkeypatch.setattr(Queue, "write", write)

        def peer() -> None:
            assert input_closed.wait(10)
            _complete_after_eof(
                harness,
                session.tid,
                script,
                inbox_name,
                observed_stderr=observed_stderr,
            )

        thread = threading.Thread(target=peer)
        thread.start()
        try:
            with create_pipe_input() as pipe:
                prompt: PromptSession[str] = PromptSession(
                    input=pipe, output=DummyOutput()
                )
                monkeypatch.setattr("prompt_toolkit.PromptSession", lambda: prompt)
                pipe.send_bytes(b"\x04")
                cli_run.drive_interactive_session(session, None)
        finally:
            input_closed.set()
            observed_stderr.set()
            thread.join(timeout=10)
            assert not thread.is_alive()
        assert [json.loads(raw) for raw in input_messages] == [{"close": True}]
        assert controls == []
        assert thread_exception_guard == []
        assert task_commands.task_terminal_snapshot(
            session.tid, context=harness.context
        ).status == ("completed" if exit_code == 0 else "failed")
    captured = capsys.readouterr()
    assert "final stdout\n" in captured.out
    assert "final stderr\n" in captured.err


@pytest.mark.parametrize("ack_after", [None, 1.5])
def test_active_stop_waits_named_budget_and_accepts_only_own_ack(
    monkeypatch: pytest.MonkeyPatch, ack_after: float | None
) -> None:
    """Production ACKs settle only their request; KILL requires the actual budget."""
    clock = [0.0]
    sent: list[tuple[str, float]] = []
    waits: list[float | None] = []
    original_write = Queue.write
    monkeypatch.setattr(
        run_commands, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    with (
        WeftTestHarness() as harness,
        _live_session(harness) as session,
        _stop_peer(harness, session.tid) as consumer,
    ):
        own_key: list[str] = []

        def write(queue: Queue, raw: str, **kwargs: Any) -> int:
            message_id = original_write(queue, raw, **kwargs)
            if queue.name == f"T{session.tid}.ctrl_in":
                request = parse_control_request(raw)
                assert request is not None
                sent.append((request.command, clock[0]))
                if request.command == "STOP":
                    assert request.request_id is not None
                    own_key.append(request.request_id)
                    # An ordinary response for another request is valid but
                    # must not settle this STOP or be consumed by its observer.
                    consumer._send_control_response(
                        "STOP", "ack", request_id="another-request"
                    )
            return message_id

        def wait(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
            waits.append(timeout)
            assert timeout is not None
            clock[0] += timeout if ack_after is None else min(timeout, ack_after)
            if ack_after is not None:
                consumer._send_control_response("STOP", "ack", request_id=own_key[0])
            return True

        monkeypatch.setattr(Queue, "write", write)
        monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
        _stop_interactive_run_session(session)
        assert [command for command, _at in sent] == (
            ["STOP", "KILL"] if ack_after is None else ["STOP"]
        )
        assert waits == [INTERACTIVE_STOP_COMPLETION_TIMEOUT]
        if ack_after is None:
            assert sent[1][1] - sent[0][1] == INTERACTIVE_STOP_COMPLETION_TIMEOUT
        with harness.context.queue(f"T{session.tid}.ctrl_out") as replies:
            retained = [json.loads(raw) for raw in replies.peek_generator()]
        assert [reply["request_id"] for reply in retained] == ["another-request"]
        assert consumer.taskspec.state.status not in {"cancelled", "killed"}


@pytest.mark.parametrize(
    "invalid_case",
    ["missing_source", "unknown_source", "wrong_tid", "nonterminal_status"],
)
def test_active_stop_rejects_invalid_terminal_before_real_task_proof(
    monkeypatch: pytest.MonkeyPatch, invalid_case: str
) -> None:
    """Malformed terminal hints cannot finish the shared STOP observation."""
    waits: list[float | None] = []
    with (
        WeftTestHarness() as harness,
        _live_session(harness) as session,
        _stop_peer(harness, session.tid) as consumer,
    ):
        invalid: dict[str, Any] = {
            "type": "terminal",
            "source": "task",
            "tid": session.tid,
            "status": "completed",
        }
        if invalid_case == "missing_source":
            invalid.pop("source")
        elif invalid_case == "unknown_source":
            invalid["source"] = "observer"
        elif invalid_case == "wrong_tid":
            invalid["tid"] = str(int(session.tid) + 1)
        else:
            invalid["status"] = "running"
        with harness.context.queue(f"T{session.tid}.ctrl_out") as reply:
            reply.write(json.dumps(invalid))

        def wait(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
            waits.append(timeout)
            _publish_stopped_peer(harness, consumer)
            return True

        monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
        _stop_interactive_run_session(session)
        assert len(waits) == 1


@pytest.mark.parametrize("phase", ["before_prompt", "while_prompt_running"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_active_output_acquisition_failure_wakes_prompt_and_keeps_primary_error(
    monkeypatch: pytest.MonkeyPatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
    cleanup_fails: bool,
    phase: str,
) -> None:
    """The real output owner fails before prompt startup and disposes its leases."""
    main_thread = threading.get_ident()
    original_queue = WeftContext.queue
    original_close = Queue.close
    closed: list[tuple[str, int]] = []
    prompt_started = threading.Event()
    failure_processed = threading.Event()
    original_wake = cli_run._wake_interactive_prompt

    def wake(prompt: Any) -> None:
        failure_processed.set()
        original_wake(prompt)

    def acquire(context: WeftContext, name: str, *, persistent: bool = False) -> Queue:
        if threading.get_ident() != main_thread and name == f"T{session.tid}.ctrl_out":
            if phase == "while_prompt_running":
                assert prompt_started.wait(5)
            raise BrokerError("injected output acquisition failure")
        return original_queue(context, name, persistent=persistent)

    def close(queue: Queue) -> None:
        original_close(queue)
        closed.append((queue.name, threading.get_ident()))
        if cleanup_fails and threading.get_ident() != main_thread:
            raise RuntimeError("injected output cleanup failure")

    # The session retains its bound input-close callback at construction.
    # Install tracking before the actual queue ownership handoff.
    monkeypatch.setattr(Queue, "close", close)
    with WeftTestHarness() as harness, _live_session(harness) as session:
        monkeypatch.setattr(WeftContext, "queue", acquire)
        monkeypatch.setattr(cli_run, "_wake_interactive_prompt", wake)
        errors: list[Exception] = []
        original_exit = cli_app._command_exit

        def command_exit(exc: Exception) -> None:
            errors.append(exc)
            original_exit(exc)

        monkeypatch.setattr(cli_app, "_command_exit", command_exit)
        monkeypatch.setattr(
            cli_app.commands, "cmd_run", lambda *_args, **_kwargs: session
        )
        monkeypatch.setattr(cli_app, "read_run_stdin", lambda: None)
        with create_pipe_input() as pipe:
            prompt: PromptSession[str] = PromptSession(input=pipe, output=DummyOutput())
            _schedule_settlement_prompt(
                monkeypatch, prompt, phase, prompt_started, failure_processed
            )
            monkeypatch.setattr("prompt_toolkit.PromptSession", lambda: prompt)
            outcome = CliRunner().invoke(cli_app.app, ["run", "--interactive", "echo"])
        assert outcome.exit_code == 1
        assert len(errors) == 1
        assert isinstance(errors[0], CommandExecutionError)
        assert "injected output acquisition failure" in str(errors[0])
        assert session._closed, (
            "CLI must close its input/session before fixture cleanup"
        )
        assert any(owner != main_thread for _name, owner in closed)
        assert (f"T{session.tid}.inbox", main_thread) in closed
        if cleanup_fails:
            notes = getattr(errors[0].__cause__, "__notes__", ())
            assert any("injected output cleanup failure" in note for note in notes)
        assert thread_exception_guard == []


def _schedule_settlement_prompt(
    monkeypatch: pytest.MonkeyPatch,
    prompt: PromptSession[str],
    phase: str,
    prompt_started: threading.Event,
    settlement_processed: threading.Event,
) -> None:
    original_prompt = prompt.prompt

    def invoke(*args: Any, **kwargs: Any) -> Any:
        pre_run = kwargs.pop("pre_run", None)
        if phase == "before_prompt":
            assert settlement_processed.wait(5)

        def started() -> None:
            prompt_started.set()
            if pre_run is not None:
                pre_run()

        return original_prompt(*args, pre_run=started, **kwargs)

    monkeypatch.setattr(prompt, "prompt", invoke)


@pytest.mark.parametrize("phase", ["before_prompt", "while_prompt_running"])
def test_actual_consumer_completion_wakes_idle_active_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    thread_exception_guard: list[threading.ExceptHookArgs],
    phase: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The output owner completes both sides of the prompt startup boundary."""
    script = tmp_path / "complete_immediately.py"
    script.write_text("print('completed output', flush=True)\n")
    prompt_started = threading.Event()
    settlement_processed = threading.Event()
    original_wake = cli_run._wake_interactive_prompt
    probe = _IdleOutputFaultProbe()
    probe.install(monkeypatch)

    def wake(prompt: Any) -> None:
        settlement_processed.set()
        original_wake(prompt)

    monkeypatch.setattr(cli_run, "_wake_interactive_prompt", wake)
    with (
        WeftTestHarness() as harness,
        _live_session(harness, script_path=str(script), probe=probe) as session,
    ):
        session.send_input("start\n")

        def peer() -> None:
            if phase == "while_prompt_running":
                assert prompt_started.wait(5)
            _complete_after_eof(harness, session.tid, script, None)

        thread = threading.Thread(target=peer)
        thread.start()
        try:
            with create_pipe_input() as pipe:
                prompt: PromptSession[str] = PromptSession(
                    input=pipe, output=DummyOutput()
                )
                _schedule_settlement_prompt(
                    monkeypatch, prompt, phase, prompt_started, settlement_processed
                )
                monkeypatch.setattr("prompt_toolkit.PromptSession", lambda: prompt)
                cli_run.drive_interactive_session(session, None)
        finally:
            thread.join(timeout=10)
            assert not thread.is_alive()
        assert settlement_processed.is_set()
        assert (
            task_commands.task_terminal_snapshot(
                session.tid, context=harness.context
            ).status
            == "completed"
        )
        assert thread_exception_guard == []
    assert "completed output\n" in capsys.readouterr().out


def test_active_quit_stops_real_child_that_ignores_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Explicit quit sends STOP after stdin closure without premature KILL."""
    script = tmp_path / "ignore_eof.py"
    script.write_text(
        "import sys, time\n"
        "print('ready', flush=True)\n"
        "sys.stdin.read()\n"
        "while True: time.sleep(0.05)\n"
    )
    original_write = Queue.write
    controls: list[str] = []
    ready = threading.Event()
    stopped: list[bool] = []
    with (
        WeftTestHarness() as harness,
        _live_session(harness, script_path=str(script)) as session,
    ):

        def write(queue: Queue, raw: str, **kwargs: Any) -> int:
            message_id = original_write(queue, raw, **kwargs)
            if queue.name == f"T{session.tid}.ctrl_in":
                controls.append(raw)
            return message_id

        monkeypatch.setattr(Queue, "write", write)
        session.send_input("start\n")

        def peer() -> None:
            consumer = Consumer(
                harness.context.broker_target,
                _interactive_spec(session.tid, script_path=str(script)),
                config=harness.context.broker_config,
            )
            with (
                consumer.drive_scope(),
                harness.context.queue(f"T{session.tid}.outbox") as output,
            ):
                drive_until(
                    output.peek_one,
                    lambda raw: raw is not None and "ready" in raw,
                    step=consumer.process_once,
                    wait=consumer.wait_for_activity,
                    timeout=10,
                )
                child = consumer._interactive_session
                assert isinstance(child, CommandSession)
                ready.set()
                drive_until(
                    lambda: (
                        consumer.should_stop and consumer._interactive_session is None
                    ),
                    bool,
                    step=consumer.process_once,
                    wait=consumer.wait_for_activity,
                    timeout=15,
                )
                # STOP can signal the actual host process before its existing
                # waiter has reaped it. Verify physical death through one real
                # owned Popen exit wait, without treating ACK as reap proof.
                child._process.wait(timeout=INTERACTIVE_STOP_COMPLETION_TIMEOUT)
                stopped.append(not child.is_alive())

        thread = threading.Thread(target=peer)
        thread.start()
        try:
            assert ready.wait(10)
            with create_pipe_input() as pipe:
                prompt: PromptSession[str] = PromptSession(
                    input=pipe, output=DummyOutput()
                )
                monkeypatch.setattr("prompt_toolkit.PromptSession", lambda: prompt)
                pipe.send_text(":quit\n")
                cli_run.drive_interactive_session(session, None)
        finally:
            thread.join(timeout=15)
            assert not thread.is_alive()
        requests = [parse_control_request(raw) for raw in controls]
        assert [request.command for request in requests if request is not None] == [
            "STOP"
        ]
        assert stopped == [True]
        assert thread_exception_guard == []


def test_real_cli_wait_failure_keeps_primary_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual CLI ownership keeps acquisition failure ahead of input cleanup."""
    original_queue = WeftContext.queue
    original_close = Queue.close
    errors: list[Exception] = []
    owned_input: list[str] = []
    input_closes: list[str] = []
    original_exit = cli_app._command_exit

    def close(queue: Queue) -> None:
        original_close(queue)
        if queue.name in owned_input:
            input_closes.append(queue.name)
            raise RuntimeError("injected input cleanup failure")

    with WeftTestHarness() as harness, monkeypatch.context() as patch:
        # The session captures its bound input close at construction. Install
        # the real Queue.close fault before that ownership handoff.
        patch.setattr(Queue, "close", close)
        with _live_session(harness) as session:
            owned_input.append(f"T{session.tid}.inbox")

            def acquire(
                context: WeftContext, name: str, *, persistent: bool = False
            ) -> Queue:
                if name == f"T{session.tid}.ctrl_out":
                    raise BrokerError("injected wait acquisition failure")
                return original_queue(context, name, persistent=persistent)

            def command_exit(exc: Exception) -> None:
                errors.append(exc)
                original_exit(exc)

            patch.setattr(WeftContext, "queue", acquire)
            patch.setattr(cli_app, "_command_exit", command_exit)
            patch.setattr(
                cli_app.commands, "cmd_run", lambda *_args, **_kwargs: session
            )
            patch.setattr(cli_app, "read_run_stdin", lambda: None)
            outcome = CliRunner().invoke(cli_app.app, ["run", "echo"])
            assert input_closes == owned_input
            assert outcome.exit_code == 1
            assert len(errors) == 1
            assert isinstance(errors[0], CommandExecutionError)
            assert "injected wait acquisition failure" in str(errors[0])
            assert session._closed
            assert any(
                "injected input cleanup failure" in note
                for note in getattr(errors[0], "__notes__", ())
            )


def test_real_interactive_render_failure_keeps_primary_over_iterator_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual Consumer output and actual stream teardown preserve render failure."""
    script = tmp_path / "render_failure.py"
    script.write_text("import sys\nsys.stdin.read()\nprint('render me', flush=True)\n")
    original_close = Queue.close
    with (
        WeftTestHarness() as harness,
        _live_session(harness, script_path=str(script)) as session,
    ):
        session.close_input()
        _complete_after_eof(harness, session.tid, script, None)

        def echo(*_args: Any, **_kwargs: Any) -> None:
            raise OSError("injected render failure")

        def close(queue: Queue) -> None:
            original_close(queue)
            if queue.name == f"T{session.tid}.outbox":
                raise RuntimeError("injected iterator cleanup failure")

        with monkeypatch.context() as patch:
            patch.setattr(cli_run.typer, "echo", echo)
            patch.setattr(Queue, "close", close)
            with pytest.raises(OSError, match="injected render failure") as caught:
                cli_run._render_interactive_events(session.events())
        assert any(
            "injected iterator cleanup failure" in note
            for note in getattr(caught.value, "__notes__", ())
        )
