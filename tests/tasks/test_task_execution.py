"""Task execution tests covering reservation flow and control handling."""

from __future__ import annotations

import base64
import json
import sys
import threading
from pathlib import Path

import pytest

from simplebroker import Queue
from tests.helpers.queue_payloads import terminal_envelopes
from tests.tasks import (
    sample_targets as targets,  # noqa: F401 - ensure module importable
)
from weft._constants import (
    CONTROL_STOP,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
)
from weft.core import launcher as launcher_module
from weft.core.launcher import _request_parent_loss_shutdown, _task_process_entry
from weft.core.runners import RunnerOutcome
from weft.core.tasks import Consumer
from weft.core.tasks.base import BaseTask
from weft.core.taskspec import (
    IOSection,
    ReservedPolicy,
    SpecSection,
    StateSection,
    TaskSpec,
)

PROCESS_SCRIPT = str((Path(__file__).resolve().parent / "process_target.py").resolve())
_launcher_wait_calls: list[float | None] = []
_launcher_process_calls = 0


class LauncherWaitTask:
    def __init__(
        self,
        _db_path: object,
        taskspec: TaskSpec,
        *,
        config: dict[str, object] | None = None,
    ) -> None:
        del config
        self.taskspec = taskspec
        self.should_stop = False
        self.process_calls = 0
        self.taskspec.mark_started(pid=0)
        self.taskspec.mark_running(pid=0)

    def process_once(self) -> None:
        global _launcher_process_calls
        self.process_calls += 1
        _launcher_process_calls = self.process_calls
        if self.process_calls >= 2:
            self.taskspec.mark_completed(return_code=0)

    def wait_for_activity(self, timeout: float | None) -> None:
        _launcher_wait_calls.append(timeout)

    def stop(self, *, join: bool = True) -> None:
        del join
        self.should_stop = True


class LauncherTerminalTask(LauncherWaitTask):
    def process_once(self) -> None:
        global _launcher_process_calls
        self.process_calls += 1
        _launcher_process_calls = self.process_calls
        self.taskspec.mark_completed(return_code=0)


def drain_queue(queue) -> list[str]:
    messages: list[str] = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        messages.append(value)
    return messages


def make_function_taskspec(
    tid: str,
    function_target: str,
    *,
    cleanup_on_exit: bool = False,
    reserved_stop: ReservedPolicy = ReservedPolicy.KEEP,
    reserved_error: ReservedPolicy = ReservedPolicy.KEEP,
    weft_context: str | None = None,
    output_size_limit_mb: float | None = None,
    stream_output: bool | None = None,
) -> TaskSpec:
    """Create a TaskSpec for function execution with explicit queue mappings."""
    return TaskSpec(
        tid=tid,
        name="task-func",
        spec=SpecSection(
            type="function",
            function_target=function_target,
            cleanup_on_exit=cleanup_on_exit,
            reserved_policy_on_stop=reserved_stop,
            reserved_policy_on_error=reserved_error,
            weft_context=weft_context,
            output_size_limit_mb=output_size_limit_mb,
            stream_output=stream_output if stream_output is not None else False,
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"},
            control={
                "ctrl_in": f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )


def make_command_taskspec(
    tid: str,
    process_target: str,
    *,
    args: list[str] | None = None,
    cleanup_on_exit: bool = False,
    reserved_stop: ReservedPolicy = ReservedPolicy.KEEP,
    reserved_error: ReservedPolicy = ReservedPolicy.KEEP,
    persistent: bool = False,
    stream_output: bool | None = None,
) -> TaskSpec:
    """Create a TaskSpec for command execution with explicit queue mappings."""
    return TaskSpec(
        tid=tid,
        name="task-command",
        spec=SpecSection(
            type="command",
            process_target=process_target,
            args=list(args or []),
            cleanup_on_exit=cleanup_on_exit,
            reserved_policy_on_stop=reserved_stop,
            reserved_policy_on_error=reserved_error,
            persistent=persistent,
            stream_output=stream_output if stream_output is not None else False,
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"},
            control={
                "ctrl_in": f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )


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


@pytest.fixture
def unique_tid() -> str:
    """Generate a unique-ish TID for tests."""
    import time

    return str(time.time_ns())


def test_task_processes_function_target_and_writes_outbox(
    broker_env, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    inbox_name = spec.io.inputs["inbox"]
    reserved_name = f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}"
    outbox_name = spec.io.outputs["outbox"]

    inbox = make_queue(inbox_name)
    inbox.write(json.dumps({"args": ["payload"], "kwargs": {"suffix": "!"}}))

    task._drain_queue()

    outbox = make_queue(outbox_name)
    result = outbox.read_one()
    assert result == "payload!"

    reserved_queue = make_queue(reserved_name)
    assert reserved_queue.peek_one() is None
    assert task.taskspec.state.status == "completed"
    assert task.taskspec.state.return_code == 0
    events = [json.loads(msg) for msg in drain_queue(log_queue)]
    assert any(event["event"] == "work_completed" for event in events)

    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    envelopes = terminal_envelopes(ctrl_out, tid=unique_tid, source="task")
    assert len(envelopes) == 1
    terminal = envelopes[0]
    assert terminal["status"] == "completed"
    assert terminal["return_code"] == 0


def test_run_work_item_executes_payload(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)

    result = task.run_work_item({"args": ["direct"], "kwargs": {"suffix": "!"}})

    assert result == "direct!"
    outbox = make_queue(spec.io.outputs["outbox"])
    assert outbox.read_one() == "direct!"
    task.cleanup()


def test_run_work_item_spills_large_output(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:large_output",
        output_size_limit_mb=1,
    )
    task = Consumer(db_path, spec)

    size = 2 * 1024 * 1024
    result = task.run_work_item({"kwargs": {"size": size}})

    assert isinstance(result, str)
    assert len(result) == size

    outbox = make_queue(spec.io.outputs["outbox"])
    reference = json.loads(outbox.read_one())
    assert reference["type"] == "large_output"
    task.cleanup()


def test_run_work_item_deferred_stop_without_active_message_preserves_reserved_queue(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    reserved.write("job")

    def fake_run_task(_work_item: object) -> RunnerOutcome:
        task._defer_active_control(CONTROL_STOP, int(unique_tid) + 1)
        return RunnerOutcome(
            status="cancelled",
            value=None,
            error="Target execution cancelled",
            stdout=None,
            stderr=None,
            returncode=None,
            duration=0.0,
        )

    monkeypatch.setattr(task, "_run_task", fake_run_task)

    with pytest.raises(RuntimeError, match="STOP command received"):
        task.run_work_item({"args": ["direct"]})

    assert task.taskspec.state.status == "cancelled"
    assert reserved.read_one() == "job"
    assert make_queue(spec.io.control["ctrl_out"]).read_one() is not None
    task.cleanup()


def test_runner_error_diagnostics_are_written_to_terminal_task_log(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)
    diagnostics = {
        "phase": "execute",
        "runner": "host",
        "target_type": "function",
        "message": "runner boom",
    }

    def fake_run_task(_work_item: object) -> RunnerOutcome:
        return RunnerOutcome(
            status="error",
            value=None,
            error="runner boom",
            stdout=None,
            stderr=None,
            returncode=None,
            duration=0.0,
            diagnostics=diagnostics,
        )

    monkeypatch.setattr(task, "_run_task", fake_run_task)

    with pytest.raises(RuntimeError, match="runner boom"):
        task.run_work_item({"args": ["direct"]})

    events = [json.loads(msg) for msg in drain_queue(log_queue)]
    failed = next(event for event in events if event["event"] == "work_failed")
    assert failed["runner_diagnostics"] == diagnostics
    task.cleanup()


def test_task_failure_leaves_message_in_reserved(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:fail_payload",
        reserved_error=ReservedPolicy.KEEP,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    outbox = make_queue(spec.io.outputs["outbox"])
    assert outbox.read_one() is None

    reserved_queue = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    peeked = reserved_queue.peek_one(with_timestamps=True)
    assert peeked is not None
    assert peeked[0] is not None
    assert task.taskspec.state.status == "failed"


def test_start_token_cleared_on_failure(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=["-c", "import sys; sys.exit(2)"],
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    reserved_name = f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}"
    reserved = make_queue(reserved_name)
    outbox = make_queue(spec.io.outputs["outbox"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    inbox.write(json.dumps({}))

    task._drain_queue()

    assert task.taskspec.state.status == "failed"
    assert reserved.peek_one() is None
    assert outbox.peek_one() is None
    terminal_raw = ctrl_out.peek_one()
    assert terminal_raw is not None
    terminal = json.loads(terminal_raw)
    assert terminal["type"] == "terminal"
    assert terminal["source"] == "task"
    assert terminal["tid"] == unique_tid
    assert terminal["status"] == "failed"


def test_task_handles_stop_control_message(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.KEEP,
    )
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task._drain_queue()

    assert task.should_stop is True
    assert task.taskspec.state.status == "cancelled"
    assert ctrl_in.read_one() is None


def test_task_run_until_stopped_honors_pending_stop_control(
    broker_env, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])

    inbox.write(json.dumps({"args": ["indef"], "kwargs": {"suffix": "!"}}))
    ctrl_in.write(CONTROL_STOP)

    task.run_until_stopped(poll_interval=0.0, max_iterations=5)

    assert outbox.read_one() is None
    assert task.should_stop is True
    assert task.taskspec.state.status == "cancelled"


def test_task_run_until_stopped_waits_through_activity_seam(
    broker_env,
    unique_tid: str,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    wait_calls: list[float | None] = []

    def fake_wait(timeout: float | None) -> None:
        wait_calls.append(timeout)
        task.should_stop = True

    monkeypatch.setattr(task, "wait_for_activity", fake_wait)

    task.run_until_stopped(poll_interval=0.25, max_iterations=5)

    assert wait_calls == [0.25]


def test_task_run_until_stopped_uses_next_wait_timeout(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    wait_calls: list[float | None] = []

    def fake_wait(timeout: float | None) -> None:
        wait_calls.append(timeout)
        task.should_stop = True

    monkeypatch.setattr(task, "next_wait_timeout", lambda: 0.75)
    monkeypatch.setattr(task, "wait_for_activity", fake_wait)

    task.run_until_stopped(poll_interval=0.25, max_iterations=5)

    assert wait_calls == [0.75]


def test_task_run_until_stopped_waits_for_zero_next_timeout(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    wait_calls: list[float | None] = []

    def fake_wait(timeout: float | None) -> None:
        wait_calls.append(timeout)
        task.should_stop = True

    monkeypatch.setattr(task, "next_wait_timeout", lambda: 0.0)
    monkeypatch.setattr(task, "wait_for_activity", fake_wait)

    task.run_until_stopped(poll_interval=0.0, max_iterations=5)

    assert wait_calls == [0.0]


def test_task_process_entry_waits_through_activity_seam(
    broker_env,
    unique_tid: str,
) -> None:
    global _launcher_process_calls
    db_path, _make_queue = broker_env
    _launcher_wait_calls.clear()
    _launcher_process_calls = 0
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )

    _task_process_entry(
        f"{LauncherWaitTask.__module__}.{LauncherWaitTask.__qualname__}",
        db_path,
        spec.model_dump_json(),
        None,
        0.125,
    )

    assert _launcher_process_calls == 2
    assert _launcher_wait_calls == [0.125]


def test_task_process_entry_does_not_wait_after_terminal_turn(
    broker_env,
    unique_tid: str,
) -> None:
    global _launcher_process_calls
    db_path, _make_queue = broker_env
    _launcher_wait_calls.clear()
    _launcher_process_calls = 0
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )

    _task_process_entry(
        f"{LauncherTerminalTask.__module__}.{LauncherTerminalTask.__qualname__}",
        db_path,
        spec.model_dump_json(),
        None,
        0.125,
    )

    assert _launcher_process_calls == 1
    assert _launcher_wait_calls == []


def test_task_process_entry_uses_normal_return_for_windows_hard_exit(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global _launcher_process_calls
    db_path, _make_queue = broker_env
    _launcher_wait_calls.clear()
    _launcher_process_calls = 0
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )

    def _unexpected_exit(_status: int) -> None:
        raise AssertionError("Windows task-process entries must not use os._exit")

    monkeypatch.setattr(launcher_module.os, "name", "nt")
    monkeypatch.setattr(launcher_module.os, "_exit", _unexpected_exit)

    _task_process_entry(
        f"{LauncherTerminalTask.__module__}.{LauncherTerminalTask.__qualname__}",
        db_path,
        spec.model_dump_json(),
        None,
        0.125,
        True,
    )

    assert _launcher_process_calls == 1


def test_parent_loss_shutdown_wakes_task_stop_event() -> None:
    class ParentLossTask:
        def __init__(self) -> None:
            self._stop_event = threading.Event()
            self.signals: list[int] = []

        def handle_termination_signal(self, signum: int) -> None:
            self.signals.append(signum)

    task = ParentLossTask()

    _request_parent_loss_shutdown(task)

    assert task.signals
    assert task._stop_event.is_set()


def test_task_ignores_unknown_control_message(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write("UNKNOWN")

    task._drain_queue()

    assert task.should_stop is False
    assert task.taskspec.state.status == "created"
    assert ctrl_in.read_one() is None


def test_command_target_executes_process(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT],
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    inbox.write(json.dumps({"args": ["--result", "command-done"]}))

    task._drain_queue()

    assert outbox.read_one() == "command-done"


def test_large_output_spills_to_disk(tmp_path, broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    context_root = tmp_path / "project"

    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:large_output",
        cleanup_on_exit=False,
        weft_context=str(context_root),
        output_size_limit_mb=1,
    )

    task = Consumer(db_path, spec)

    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    inbox = make_queue(spec.io.inputs["inbox"])
    output_size = 2 * 1024 * 1024
    inbox.write(json.dumps({"kwargs": {"size": output_size}}))

    task._drain_queue()

    outbox = make_queue(spec.io.outputs["outbox"])
    reference = json.loads(outbox.read_one())
    assert reference["type"] == "large_output"
    output_path = Path(reference["path"])
    assert output_path.exists()
    assert output_path.read_bytes() == b"x" * output_size
    assert reference["size"] == output_size

    events = [json.loads(msg) for msg in drain_queue(log_queue)]
    assert any(event["event"] == "output_spilled" for event in events)


def test_large_output_spills_to_custom_weft_directory_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    context_root = tmp_path / "project"
    monkeypatch.setenv("WEFT_DIRECTORY_NAME", ".engram")

    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:large_output",
        cleanup_on_exit=False,
        weft_context=str(context_root),
        output_size_limit_mb=1,
    )

    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    output_size = 2 * 1024 * 1024
    inbox.write(json.dumps({"kwargs": {"size": output_size}}))

    task._drain_queue()

    outbox = make_queue(spec.io.outputs["outbox"])
    reference = json.loads(outbox.read_one())
    expected_path = (
        Path(context_root) / ".engram" / "outputs" / unique_tid / "output.dat"
    )

    assert Path(reference["path"]) == expected_path
    assert expected_path.exists()
    assert expected_path.read_bytes() == b"x" * output_size


def test_large_output_cleanup_on_exit(tmp_path, broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    context_root = tmp_path / "project"

    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:large_output",
        cleanup_on_exit=True,
        weft_context=str(context_root),
        output_size_limit_mb=1,
    )

    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"kwargs": {"size": 2 * 1024 * 1024}}))

    task._drain_queue()

    expected_path = Path(context_root) / ".weft" / "outputs" / unique_tid / "output.dat"
    assert not expected_path.exists()


def test_stream_output_writes_chunks(tmp_path, broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    context_root = tmp_path / "project"

    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:large_output",
        weft_context=str(context_root),
        output_size_limit_mb=1,
        stream_output=True,
    )

    task = Consumer(db_path, spec)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    inbox = make_queue(spec.io.inputs["inbox"])
    output_size = 2 * 1024 * 1024
    inbox.write(json.dumps({"kwargs": {"size": output_size}}))

    task._drain_queue()

    outbox = make_queue(spec.io.outputs["outbox"])
    chunks: list[bytes] = []
    messages: list[dict] = []
    while True:
        message = outbox.read_one()
        if message is None:
            break
        envelope = json.loads(message)
        messages.append(envelope)
        assert envelope["type"] == "stream"
        chunk_bytes = base64.b64decode(envelope["data"])
        chunks.append(chunk_bytes)
        if envelope["final"]:
            break

    assert len(messages) >= 2
    assert messages[-1]["final"] is True
    reconstructed = b"".join(chunks)
    assert reconstructed == b"x" * output_size

    events = [json.loads(msg) for msg in drain_queue(log_queue)]
    completed = next(event for event in events if event["event"] == "work_completed")
    assert completed["result_bytes"] == output_size


def test_stream_output_small_payload_single_chunk(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        stream_output=True,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    outbox = make_queue(spec.io.outputs["outbox"])
    message = outbox.read_one()
    assert message is not None
    envelope = json.loads(message)
    assert envelope["type"] == "stream"
    assert envelope["final"] is True
    decoded = base64.b64decode(envelope["data"]).decode("utf-8")
    assert decoded == "payload"


def test_streaming_session_records_and_clears(
    monkeypatch, broker_env, unique_tid: str
) -> None:
    writes, deletes = _instrument_streaming_queue(monkeypatch)

    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        stream_output=True,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()
    task.cleanup()

    assert writes, "expected streaming session entry"
    session = writes[0]
    assert session["tid"] == unique_tid
    assert session["mode"] == "stream"
    assert session["queue"] == spec.io.outputs["outbox"]
    assert session["session_id"].startswith(
        f"{unique_tid}:{spec.io.outputs['outbox']}:"
    )
    assert deletes, "expected streaming session deletion"


def test_persistent_live_command_streaming_clears_session_before_waiting(
    monkeypatch,
    broker_env,
    unique_tid: str,
) -> None:
    writes, deletes = _instrument_streaming_queue(monkeypatch)

    db_path, _make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT, "--result", "streamed"],
        persistent=True,
        stream_output=True,
    )
    task = Consumer(db_path, spec)

    try:
        result = task.run_work_item({})
        assert result == "streamed"
        assert task.taskspec.state.status == "running"
        assert writes, "expected streaming session entry"
        assert deletes, "expected streaming session deletion before returning"
    finally:
        task.cleanup()


def test_persistent_work_item_success_does_not_emit_terminal_envelope(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT, "--result", "persistent-result"],
        persistent=True,
    )
    task = Consumer(db_path, spec)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    try:
        result = task.run_work_item({})

        assert result == "persistent-result"
        assert task.taskspec.state.status == "running"
        events = [json.loads(msg) for msg in drain_queue(log_queue)]
        assert any(event["event"] == "work_item_completed" for event in events)
        ctrl_out = make_queue(spec.io.control["ctrl_out"])
        assert terminal_envelopes(ctrl_out, tid=unique_tid, source="task") == []
    finally:
        task.cleanup()


def test_cleanup_on_exit_removes_output_queue(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        cleanup_on_exit=True,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    make_queue(spec.io.outputs["outbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    assert make_queue(spec.io.outputs["outbox"]).has_pending() is True


def test_cleanup_on_exit_process_target(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT, "--output-size", "1024"],
        cleanup_on_exit=True,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    make_queue(spec.io.outputs["outbox"])
    inbox.write(json.dumps({"args": []}))

    task._drain_queue()

    assert make_queue(spec.io.outputs["outbox"]).has_pending() is True


def test_reserved_policy_keep_on_stop(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.KEEP,
    )
    task = Consumer(db_path, spec)

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    reserved.write("job")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task._drain_queue()

    assert reserved.has_pending() is True


def test_reserved_policy_clear_on_stop(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    reserved.write("job")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task._drain_queue()

    assert reserved.has_pending() is False


def test_reserved_policy_requeue_on_stop(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.REQUEUE,
    )
    task = Consumer(db_path, spec)

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    reserved.write("job")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task._drain_queue()

    inbox = make_queue(spec.io.inputs["inbox"])
    assert inbox.read_one() == "job"
    assert reserved.has_pending() is False


def test_stop_with_default_cleanup_preserves_reserved_when_keep(
    broker_env, unique_tid: str
) -> None:
    """cleanup_on_exit=True must not override the KEEP reserved policy on STOP."""
    db_path, make_queue = broker_env
    spec = TaskSpec(
        tid=unique_tid,
        name="task-default-cleanup",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
            cleanup_on_exit=True,
            reserved_policy_on_stop=ReservedPolicy.KEEP,
        ),
        io=IOSection(
            inputs={"inbox": f"T{unique_tid}.inbox"},
            outputs={"outbox": f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}"},
            control={
                "ctrl_in": f"T{unique_tid}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"T{unique_tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )
    task = Consumer(db_path, spec)

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    reserved.write("job")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task._drain_queue()

    assert task.should_stop is True
    assert reserved.has_pending() is True


def test_reserved_policy_keep_on_error(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:fail_payload",
        reserved_error=ReservedPolicy.KEEP,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    assert reserved.has_pending() is True
    assert task.taskspec.state.status == "failed"


def test_reserved_policy_clear_on_error(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:fail_payload",
        reserved_error=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    assert reserved.has_pending() is False
    assert task.taskspec.state.status == "failed"


def test_reserved_policy_requeue_on_error(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:fail_payload",
        reserved_error=ReservedPolicy.REQUEUE,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    assert reserved.has_pending() is False
    assert inbox.read_one() is not None
    assert task.taskspec.state.status == "failed"
