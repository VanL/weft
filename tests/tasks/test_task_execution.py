"""Task execution tests covering reservation flow and control handling."""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.queue_payloads import terminal_envelopes
from tests.tasks import (
    sample_targets as targets,  # noqa: F401 - ensure module importable
)
from weft._constants import (
    CONTROL_PING,
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
from weft.core.tasks import base as base_module
from weft.core.tasks import consumer as consumer_module
from weft.core.tasks.base import BaseTask, TaskWorkerResult
from weft.core.tasks.multiqueue_watcher import QueueMessageContext
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


class ReactorTestTask(BaseTask):
    """Small concrete task for BaseTask reactor worker tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.worker_results: list[TaskWorkerResult] = []
        self.worker_result_thread_ids: list[int] = []
        super().__init__(*args, **kwargs)

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        return {
            self._queue_names["inbox"]: self._read_queue_config(
                self._handle_work_message
            ),
        }

    def _handle_work_message(
        self,
        message: str,
        timestamp: int,
        context: QueueMessageContext,
    ) -> None:
        del message, timestamp, context

    def _handle_worker_result(self, result: TaskWorkerResult) -> None:
        if result.error is not None:
            super()._handle_worker_result(result)
        self.worker_results.append(result)
        self.worker_result_thread_ids.append(threading.get_ident())


class ErrorRecordingReactorTestTask(ReactorTestTask):
    """Concrete task that records worker errors instead of raising them."""

    def _handle_worker_result(self, result: TaskWorkerResult) -> None:
        self.worker_results.append(result)
        self.worker_result_thread_ids.append(threading.get_ident())


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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

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

    def fake_run_task_for_reactor(_work_item: object) -> tuple[RunnerOutcome, bool]:
        return (
            RunnerOutcome(
                status="cancelled",
                value=None,
                error="Target execution cancelled",
                stdout=None,
                stderr=None,
                returncode=None,
                duration=0.0,
            ),
            False,
        )

    monkeypatch.setattr(task, "_run_task_for_reactor", fake_run_task_for_reactor)
    task._defer_active_control(CONTROL_STOP, int(unique_tid) + 1)

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

    def fake_run_task_for_reactor(_work_item: object) -> tuple[RunnerOutcome, bool]:
        return (
            RunnerOutcome(
                status="error",
                value=None,
                error="runner boom",
                stdout=None,
                stderr=None,
                returncode=None,
                duration=0.0,
                diagnostics=diagnostics,
            ),
            False,
        )

    monkeypatch.setattr(task, "_run_task_for_reactor", fake_run_task_for_reactor)

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

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

    task.process_once()

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


def _drive_consumer_until(
    task: Consumer,
    predicate: Callable[[], bool],
    *,
    timeout: float = 15.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task.process_once()
        if predicate():
            return
        task.wait_for_activity(timeout=0.02)
    raise AssertionError(
        "Consumer did not reach expected state before timeout "
        f"(status={task.taskspec.state.status!r}, "
        f"should_stop={task.should_stop!r}, "
        f"worker_activity={task._has_worker_activity()!r})"
    )


def test_consumer_reactor_responds_to_ping_while_command_work_is_active(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    command_duration = 2.0
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[
            PROCESS_SCRIPT,
            "--duration",
            str(command_duration),
            "--result",
            "reactor-done",
        ],
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    outbox = make_queue(spec.io.outputs["outbox"])
    inbox.write(json.dumps({"args": []}))

    started_at = time.monotonic()
    task.process_once()
    elapsed = time.monotonic() - started_at

    assert elapsed < command_duration * 0.75
    assert task.taskspec.state.status == "running"
    assert task._has_worker_activity() is True
    assert outbox.read_one() is None

    ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "during"}))
    task.process_once()

    responses = [json.loads(msg) for msg in drain_queue(ctrl_out)]
    pong = next(response for response in responses if response["command"] == "PING")
    assert pong["request_id"] == "during"
    assert pong["message"] == "PONG"
    assert pong["task_status"] == "running"

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
        timeout=5.0,
    )

    assert outbox.read_one() == "reactor-done"


def test_consumer_reactor_stop_cancels_active_command_on_main_thread(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT, "--duration", "1.5", "--result", "should-not-finish"],
        reserved_stop=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    outbox = make_queue(spec.io.outputs["outbox"])
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    inbox.write(json.dumps({"args": []}))

    task.process_once()
    assert task.taskspec.state.status == "running"

    ctrl_in.write(CONTROL_STOP)
    task.process_once()
    assert task.should_stop is True

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "cancelled",
        timeout=5.0,
    )

    responses = [json.loads(msg) for msg in drain_queue(ctrl_out)]
    stop_response = next(
        response for response in responses if response.get("command") == "STOP"
    )
    assert stop_response["status"] == "ack"
    assert outbox.read_one() is None
    assert reserved.has_pending() is False


def test_consumer_worker_constructs_runner_without_broker_context(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(unique_tid, sys.executable)
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    captured_context: list[tuple[Any, Any]] = []

    class FakeTaskRunner:
        def __init__(self, **kwargs: Any) -> None:
            captured_context.append((kwargs.get("db_path"), kwargs.get("config")))

        def supports_stream_callbacks(self) -> bool:
            return False

        def run_with_hooks(
            self,
            work_item: Any,
            **_kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            return RunnerOutcome(
                status="ok",
                value="broker-free",
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", FakeTaskRunner)
    inbox.write(json.dumps({"args": []}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    assert captured_context == [(None, None)]
    assert outbox.read_one() == "broker-free"


def test_consumer_active_wait_activity_ignores_reserved_work_queue(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(unique_tid, sys.executable)
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    worker_started = threading.Event()
    release_worker = threading.Event()

    class BlockingTaskRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def supports_stream_callbacks(self) -> bool:
            return False

        def run_with_hooks(
            self,
            work_item: Any,
            **_kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            worker_started.set()
            release_worker.wait(timeout=2.0)
            return RunnerOutcome(
                status="ok",
                value="done",
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", BlockingTaskRunner)
    inbox.write(json.dumps({"args": []}))

    try:
        task.process_once()
        assert worker_started.wait(timeout=2.0)
        assert reserved.has_pending() is True
        assert task._has_pending_messages() is False

        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "active"}))
        assert task._has_pending_messages() is True
    finally:
        release_worker.set()

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )


def test_consumer_active_control_gets_turn_while_stream_events_remain(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_QUEUE_MAXSIZE", 16)
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN", 2)
    spec = make_command_taskspec(unique_tid, sys.executable, stream_output=True)
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    release_worker = threading.Event()

    class StreamingTaskRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def supports_stream_callbacks(self) -> bool:
            return True

        def run_with_hooks(
            self,
            work_item: Any,
            **kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            on_stdout_chunk = kwargs["on_stdout_chunk"]
            for index in range(8):
                on_stdout_chunk(f"chunk-{index}", False)
            release_worker.wait(timeout=2.0)
            return RunnerOutcome(
                status="ok",
                value="stream-done",
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", StreamingTaskRunner)
    inbox.write(json.dumps({"args": []}))

    try:
        task.process_once()
        deadline = time.monotonic() + 2.0
        while task._worker_result_queue.qsize() < 6 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert task._worker_result_queue.qsize() >= 6

        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "stream"}))
        task.process_once()

        responses = [json.loads(msg) for msg in drain_queue(ctrl_out)]
        pong = next(response for response in responses if response["command"] == "PING")
        assert pong["request_id"] == "stream"
        assert pong["message"] == "PONG"
        assert task._has_pending_worker_results() is True
    finally:
        release_worker.set()

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )


def test_base_task_applies_worker_result_on_main_thread(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    main_thread_id = threading.get_ident()
    worker_started = threading.Event()

    def worker_body() -> dict[str, int]:
        worker_started.set()
        return {"thread_id": threading.get_ident()}

    task._submit_worker_call("unit", worker_body)
    assert worker_started.wait(timeout=2.0)

    deadline = time.monotonic() + 2.0
    while not task.worker_results and time.monotonic() < deadline:
        task.process_once()
        if not task.worker_results:
            task.wait_for_activity(timeout=0.01)

    assert task.worker_results
    result = task.worker_results[0]
    assert result.lane == "unit"
    assert result.error is None
    assert result.value["thread_id"] != main_thread_id
    assert task.worker_result_thread_ids == [main_thread_id]


def test_base_task_worker_lane_applies_result_on_main_thread(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    main_thread_id = threading.get_ident()
    worker_started = threading.Event()

    def worker_body() -> dict[str, int]:
        worker_started.set()
        return {"thread_id": threading.get_ident()}

    task._submit_worker_lane("unit", worker_body)
    assert worker_started.wait(timeout=2.0)

    deadline = time.monotonic() + 2.0
    while not task.worker_results and time.monotonic() < deadline:
        task.process_once()
        if not task.worker_results:
            task.wait_for_activity(timeout=0.01)

    assert task.worker_results
    result = task.worker_results[0]
    assert result.lane == "unit"
    assert result.error is None
    assert result.value["thread_id"] != main_thread_id
    assert task.worker_result_thread_ids == [main_thread_id]


def test_base_task_worker_lane_delivers_errors_on_main_thread(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ErrorRecordingReactorTestTask(db_path, spec)
    main_thread_id = threading.get_ident()
    worker_started = threading.Event()

    def worker_body() -> None:
        worker_started.set()
        raise ValueError("lane failed")

    task._submit_worker_lane("unit", worker_body)
    assert worker_started.wait(timeout=2.0)

    deadline = time.monotonic() + 2.0
    while not task.worker_results and time.monotonic() < deadline:
        task.process_once()
        if not task.worker_results:
            task.wait_for_activity(timeout=0.01)

    assert task.worker_results
    result = task.worker_results[0]
    assert result.lane == "unit"
    assert isinstance(result.error, ValueError)
    assert str(result.error) == "lane failed"
    assert task.worker_result_thread_ids == [main_thread_id]


def test_base_task_wait_for_activity_caps_wait_while_worker_is_active(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    worker_started = threading.Event()
    release_worker = threading.Event()
    monkeypatch.setattr(base_module, "TASK_REACTOR_WAKEUP_MAX_SECONDS", 0.02)

    def worker_body() -> str:
        worker_started.set()
        release_worker.wait(timeout=2.0)
        return "done"

    task._submit_worker_call("blocked", worker_body)
    assert worker_started.wait(timeout=2.0)

    started_at = time.monotonic()
    task.wait_for_activity(timeout=5.0)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.5
    assert task.worker_results == []

    release_worker.set()
    deadline = time.monotonic() + 2.0
    while not task.worker_results and time.monotonic() < deadline:
        task.process_once()
        if not task.worker_results:
            task.wait_for_activity(timeout=0.01)

    assert task.worker_results[0].value == "done"


def test_base_task_worker_result_drain_is_budgeted(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_QUEUE_MAXSIZE", 10)
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN", 2)
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)

    for value in range(5):
        assert task._publish_worker_result("unit", value=value) is True

    assert task._drain_worker_results() == 2
    assert [result.value for result in task.worker_results] == [0, 1]
    assert task._has_pending_worker_results() is True

    assert task._drain_worker_results() == 2
    assert [result.value for result in task.worker_results] == [0, 1, 2, 3]
    assert task._has_pending_worker_results() is True

    assert task._drain_worker_results() == 1
    assert [result.value for result in task.worker_results] == [0, 1, 2, 3, 4]
    assert task._has_pending_worker_results() is False


def test_base_task_process_once_spends_one_worker_result_budget_per_turn(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_QUEUE_MAXSIZE", 10)
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN", 2)
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)

    for value in range(5):
        assert task._publish_worker_result("unit", value=value) is True

    task.process_once()
    assert [result.value for result in task.worker_results] == [0, 1]
    assert task._has_pending_worker_results() is True

    task.process_once()
    assert [result.value for result in task.worker_results] == [0, 1, 2, 3]
    assert task._has_pending_worker_results() is True

    task.process_once()
    assert [result.value for result in task.worker_results] == [0, 1, 2, 3, 4]
    assert task._has_pending_worker_results() is False


def test_base_task_worker_result_queue_backpressures_when_full(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_QUEUE_MAXSIZE", 1)
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN", 1)
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    publisher_finished = threading.Event()

    assert task._publish_worker_result("unit", value="first") is True

    def publish_second() -> None:
        if task._publish_worker_result("unit", value="second"):
            publisher_finished.set()

    publisher = threading.Thread(target=publish_second, daemon=True)
    publisher.start()

    try:
        time.sleep(0.05)
        assert publisher_finished.is_set() is False

        assert task._drain_worker_results() == 1
        assert publisher_finished.wait(timeout=1.0)
        publisher.join(timeout=1.0)

        assert task._drain_worker_results() == 1
        assert [result.value for result in task.worker_results] == [
            "first",
            "second",
        ]
    finally:
        task._worker_stopping.set()


def test_base_task_cleanup_stops_worker_lane(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    worker_started = threading.Event()
    release_worker = threading.Event()

    def worker_body() -> str:
        worker_started.set()
        release_worker.wait(timeout=2.0)
        return "done"

    task._submit_worker_call("blocked", worker_body)
    assert worker_started.wait(timeout=2.0)

    release_worker.set()
    task.cleanup()

    assert not task._has_active_worker_threads()


def test_base_task_worker_error_is_raised_on_main_thread(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    worker_started = threading.Event()

    def worker_body() -> str:
        worker_started.set()
        raise ValueError("worker boom")

    task._submit_worker_call("failing", worker_body)
    assert worker_started.wait(timeout=2.0)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not task._has_pending_worker_results():
        task.wait_for_activity(timeout=0.01)

    with pytest.raises(RuntimeError, match="Task worker lane 'failing' failed") as exc:
        task.process_once()

    assert isinstance(exc.value.__cause__, ValueError)


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

    task.process_once()

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    outbox = make_queue(spec.io.outputs["outbox"])
    message = outbox.read_one()
    assert message is not None
    envelope = json.loads(message)
    assert envelope["type"] == "stream"
    assert envelope["final"] is True
    decoded = base64.b64decode(envelope["data"]).decode("utf-8")
    assert decoded == "payload"


def test_live_command_streaming_persists_stderr_after_control_cleanup(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[
            "-c",
            "import sys; print('stdout-line'); print('stderr-line', file=sys.stderr)",
        ],
        stream_output=True,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )
    task.cleanup()

    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    outbox = make_queue(spec.io.outputs["outbox"])
    messages = [json.loads(message) for message in outbox.peek_generator()]
    assert any(
        message.get("stream") == "stderr" and message.get("data") == "stderr-line\n"
        for message in messages
    )
    assert ctrl_out.stats().total == 0


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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )
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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    assert make_queue(spec.io.outputs["outbox"]).has_pending() is True


def test_task_cleanup_removes_standard_control_queues_after_success(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT, "--result", "payload"],
        cleanup_on_exit=False,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    outbox = make_queue(spec.io.outputs["outbox"])
    inbox.write(json.dumps({"args": []}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )
    ctrl_in.write(CONTROL_PING)

    assert ctrl_in.stats().total == 1
    assert ctrl_out.stats().total > 0

    task.cleanup()

    assert ctrl_in.stats().total == 0
    assert ctrl_out.stats().total == 0
    assert outbox.has_pending() is True


def test_task_cleanup_removes_standard_control_queues_after_stop(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        cleanup_on_exit=False,
    )
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    ctrl_in.write(CONTROL_STOP)

    task.process_once()

    assert task.should_stop is True
    assert ctrl_out.stats().total > 0

    task.cleanup()

    assert ctrl_in.stats().total == 0
    assert ctrl_out.stats().total == 0


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

    task.process_once()

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

    task.process_once()

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

    task.process_once()

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

    task.process_once()

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

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

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    assert reserved.has_pending() is False
    assert inbox.read_one() is not None
    assert task.taskspec.state.status == "failed"
