"""Task execution tests covering reservation flow and control handling."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

from simplebroker import Queue
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
    process_target: list[str],
    *,
    cleanup_on_exit: bool = False,
    reserved_stop: ReservedPolicy = ReservedPolicy.KEEP,
    reserved_error: ReservedPolicy = ReservedPolicy.KEEP,
) -> TaskSpec:
    """Create a TaskSpec for command execution with explicit queue mappings."""
    return TaskSpec(
        tid=tid,
        name="task-command",
        spec=SpecSection(
            type="command",
            process_target=process_target,
            cleanup_on_exit=cleanup_on_exit,
            reserved_policy_on_stop=reserved_stop,
            reserved_policy_on_error=reserved_error,
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
        [sys.executable, "-c", "import sys; sys.exit(2)"],
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
    assert ctrl_out.peek_one() is None


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


def test_task_run_until_stopped(broker_env, unique_tid: str) -> None:
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

    assert outbox.read_one() == "indef!"
    assert task.should_stop is True


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
        [sys.executable, PROCESS_SCRIPT],
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
        [sys.executable, PROCESS_SCRIPT, "--output-size", "1024"],
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
