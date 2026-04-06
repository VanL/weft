"""Agent execution tests through TaskRunner and Consumer."""

from __future__ import annotations

import json

import pytest

from tests.fixtures.llm_test_models import TEST_MODEL_ID
from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
)
from weft.core.tasks import Consumer
from weft.core.tasks.runner import TaskRunner
from weft.core.taskspec import (
    IOSection,
    LimitsSection,
    ReservedPolicy,
    SpecSection,
    StateSection,
    TaskSpec,
)


@pytest.fixture
def unique_tid() -> str:
    import time

    return str(time.time_ns())


def _agent_spec_payload(*, tools: tuple[dict[str, object], ...] = (), **overrides):
    payload = {
        "runtime": "llm",
        "model": TEST_MODEL_ID,
        "runtime_config": {"plugin_modules": ["tests.fixtures.llm_test_models"]},
        "tools": tools,
    }
    payload.update(overrides)
    return payload


def make_agent_taskspec(
    tid: str,
    *,
    persistent: bool = False,
    tools: tuple[dict[str, object], ...] = (),
    reserved_error: ReservedPolicy = ReservedPolicy.KEEP,
    agent_overrides: dict[str, object] | None = None,
) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="task-agent",
        spec=SpecSection(
            type="agent",
            persistent=persistent,
            timeout=30.0,
            reserved_policy_on_error=reserved_error,
            agent=_agent_spec_payload(
                tools=tools,
                **(agent_overrides or {}),
            ),
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


def make_agent_runner(
    *,
    tid: str = "123",
    timeout: float = 5.0,
    limits: LimitsSection | None = None,
    tools: tuple[dict[str, object], ...] = (),
) -> TaskRunner:
    return TaskRunner(
        target_type="agent",
        tid=tid,
        function_target=None,
        process_target=None,
        agent=_agent_spec_payload(tools=tools),
        args=None,
        kwargs=None,
        env={},
        working_dir=None,
        timeout=timeout,
        limits=limits,
        monitor_class=(
            "weft.core.resource_monitor.ResourceMonitor" if limits else None
        ),
        monitor_interval=0.05,
    )


def test_task_runner_executes_agent_successfully() -> None:
    runner = make_agent_runner()

    outcome = runner.run("hello")

    assert outcome.ok
    assert outcome.value.aggregate_public_output() == "text:hello"


def test_task_runner_agent_timeout() -> None:
    runner = make_agent_runner(
        timeout=0.2,
        tools=(
            {
                "name": "run_task",
                "kind": "python",
                "ref": "tests.tasks.process_target:run_task",
            },
        ),
    )

    outcome = runner.run('tool_json: {"duration": 2, "result": "slow"}')

    assert outcome.status == "timeout"
    assert outcome.error == "Target execution timed out"


def test_task_runner_agent_limit_violation() -> None:
    pytest.importorskip("psutil")
    runner = make_agent_runner(
        timeout=5.0,
        limits=LimitsSection(memory_mb=1),
        tools=(
            {
                "name": "run_task",
                "kind": "python",
                "ref": "tests.tasks.process_target:run_task",
            },
        ),
    )

    outcome = runner.run(
        'tool_json: {"memory_mb": 10, "duration": 2, "result": "slow"}'
    )

    assert outcome.status == "limit"
    assert outcome.metrics is not None


def test_task_runner_agent_can_be_cancelled() -> None:
    cancel_after_start = False

    def on_worker_started(_pid: int | None) -> None:
        nonlocal cancel_after_start
        cancel_after_start = True

    runner = make_agent_runner(
        timeout=10.0,
        tools=(
            {
                "name": "run_task",
                "kind": "python",
                "ref": "tests.tasks.process_target:run_task",
            },
        ),
    )

    outcome = runner.run_with_hooks(
        'tool_json: {"duration": 5, "result": "slow"}',
        cancel_requested=lambda: cancel_after_start,
        on_worker_started=on_worker_started,
    )

    assert outcome.status == "cancelled"
    assert outcome.error == "Target execution cancelled"


def test_consumer_processes_agent_and_writes_outbox(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(db_path, make_agent_taskspec(unique_tid))

    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    inbox.write("hello")

    task._drain_queue()

    result = outbox.read_one()
    assert result == "text:hello"
    assert reserved.peek_one() is None
    assert task.taskspec.state.status == "completed"


def test_consumer_creates_and_exercises_agent_task_from_payload(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    taskspec = TaskSpec.model_validate(
        {
            "tid": unique_tid,
            "name": "payload-agent-task",
            "spec": {
                "type": "agent",
                "timeout": 30.0,
                "agent": {
                    "runtime": "llm",
                    "model": TEST_MODEL_ID,
                    "runtime_config": {
                        "plugin_modules": ["tests.fixtures.llm_test_models"],
                    },
                },
            },
            "io": {
                "inputs": {"inbox": f"T{unique_tid}.inbox"},
                "outputs": {"outbox": f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}"},
                "control": {
                    "ctrl_in": f"T{unique_tid}.{QUEUE_CTRL_IN_SUFFIX}",
                    "ctrl_out": f"T{unique_tid}.ctrl_out",
                },
            },
            "state": {},
            "metadata": {"request_id": "prompt-smoke-check"},
        }
    )
    task = Consumer(db_path, taskspec)

    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")
    inbox.write("hello")

    task._drain_queue()

    result = outbox.read_one()
    assert result == "text:hello"
    assert task.taskspec.state.status == "completed"


def test_consumer_persistent_agent_per_message_processes_multiple_messages(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            persistent=True,
            agent_overrides={
                "output_mode": "json",
                "instructions": "persistent instructions",
                "options": {"temperature": 0.3},
            },
        ),
    )

    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")

    inbox.write("inspect_json:first")
    task._drain_queue()
    first = json.loads(outbox.read_one())

    assert first["task"] == "first"
    assert first["system"] == "persistent instructions"
    assert first["temperature"] == 0.3
    assert first["history"] == []
    assert task.taskspec.state.status == "running"

    inbox.write("inspect_json:second")
    task._drain_queue()
    second = json.loads(outbox.read_one())

    assert second["task"] == "second"
    assert second["system"] == "persistent instructions"
    assert second["temperature"] == 0.3
    assert second["history"] == []
    assert task.taskspec.state.status == "running"

    task.cleanup()


def test_consumer_persistent_agent_per_task_continues_conversation(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            persistent=True,
            agent_overrides={"conversation_scope": "per_task"},
        ),
    )

    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")

    inbox.write("hello")
    task._drain_queue()
    first = outbox.read_one()

    assert first == "text:hello"
    assert task.taskspec.state.status == "running"

    inbox.write("__history__")
    task._drain_queue()
    second = outbox.read_one()

    assert second == "history:hello"
    assert task.taskspec.state.status == "running"

    task.cleanup()


def test_consumer_agent_messages_output_writes_json_messages(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            agent_overrides={"output_mode": "messages"},
        ),
    )

    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")
    inbox.write("hello")

    task._drain_queue()

    result = json.loads(outbox.read_one())
    assert result == {"role": "assistant", "content": "text:hello"}
    assert task.taskspec.state.status == "completed"


def test_consumer_applies_reserved_policy_on_agent_failure(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            reserved_error=ReservedPolicy.KEEP,
            agent_overrides={
                "runtime_config": {
                    "plugin_modules": ["tests.tasks.sample_targets"],
                },
            },
        ),
    )

    inbox = make_queue(f"T{unique_tid}.inbox")
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    inbox.write("hello")

    task._drain_queue()

    assert reserved.peek_one() is not None
    assert task.taskspec.state.status == "failed"
