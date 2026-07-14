"""Agent execution tests through TaskRunner and Consumer."""

from __future__ import annotations

import json
import os
import shutil
import time
from typing import Final

import pytest

from simplebroker import Queue
from tests.fixtures.llm_test_models import TEST_MODEL_ID
from tests.fixtures.mcp_stdio_fixture import fixture_server_script_path
from tests.fixtures.provider_cli_fixture import (
    PROVIDER_FIXTURE_NAMES,
    write_provider_cli_wrapper,
)
from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.core.agents.provider_cli.registry import get_provider_cli_provider
from weft.core.agents.runtime import AgentExecutionResult
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

_MODEL_PROVIDERS = frozenset({"claude_code", "codex", "gemini", "opencode", "qwen"})
_LIVE_PROVIDER_TIMEOUT_SECONDS: Final[float] = 120.0
"""Per-turn wait budget for authenticated provider CLI calls."""

_LIVE_PROVIDER_FAILURE_STATES: Final[frozenset[str]] = frozenset(
    {"failed", "timeout", "cancelled", "killed"}
)
_LIVE_PROVIDER_DEFAULT_MODELS: Final[dict[str, str]] = {
    "qwen": "z-ai/glm-4.5-air",
}


def _drive_consumer_until(
    task: Consumer,
    predicate,
    *,
    timeout: float = 30.0,
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


def _drive_live_provider_until_output(task: Consumer, outbox: Queue) -> None:
    """Wait for live output and surface terminal provider errors immediately."""

    _drive_consumer_until(
        task,
        lambda: (
            outbox.peek_one() is not None
            or task.taskspec.state.status in _LIVE_PROVIDER_FAILURE_STATES
        ),
        timeout=_LIVE_PROVIDER_TIMEOUT_SECONDS,
    )
    if task.taskspec.state.status in _LIVE_PROVIDER_FAILURE_STATES:
        pytest.fail(
            f"Live provider task {task.taskspec.state.status}: "
            f"{task.taskspec.state.error or 'no error detail'}"
        )


def test_consumer_agent_execution_payload_falls_back_on_circular_metadata() -> None:
    metadata: dict[str, object] = {}
    metadata["self"] = metadata
    result = AgentExecutionResult(
        runtime="llm",
        model="test-model",
        output_mode="text",
        outputs=("hello",),
        metadata=metadata,
    )

    payload = Consumer._build_agent_execution_payload(result)

    assert payload == {
        "runtime": "llm",
        "model": "test-model",
        "output_mode": "text",
        "status": "completed",
    }


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


def _provider_model(provider_name: str) -> str | None:
    if provider_name in _MODEL_PROVIDERS:
        return "fixture-model"
    return None


def _provider_agent_overrides(
    *,
    provider_name: str,
    executable: str | None,
    include_profiles: bool = True,
) -> dict[str, object]:
    runtime_config: dict[str, object] = {
        "provider": provider_name,
    }
    if executable is not None:
        runtime_config["executable"] = executable
    if include_profiles:
        runtime_config["resolver_ref"] = (
            "tests.fixtures.provider_cli_fixture:resolve_operator_question"
        )
        runtime_config["tool_profile_ref"] = (
            "tests.fixtures.provider_cli_fixture:provider_tool_profile"
        )

    return {
        "runtime": "provider_cli",
        "model": _provider_model(provider_name),
        "instructions": "base instructions",
        "runtime_config": runtime_config,
    }


def _live_provider_agent_overrides(
    *,
    provider_name: str,
    executable: str,
) -> dict[str, object]:
    """Return live-test overrides for an isolated temporary working directory."""

    overrides = _provider_agent_overrides(
        provider_name=provider_name,
        executable=executable,
        include_profiles=False,
    )
    model_env = f"WEFT_LIVE_PROVIDER_CLI_MODEL_{provider_name.upper()}"
    overrides["model"] = os.environ.get(
        model_env, _LIVE_PROVIDER_DEFAULT_MODELS.get(provider_name)
    )
    if provider_name == "codex":
        overrides["options"] = {"skip_git_repo_check": True}
    return overrides


def test_live_provider_agent_overrides_allow_codex_in_isolated_workdir() -> None:
    """The live Codex smoke should opt out of its git-repository requirement."""

    overrides = _live_provider_agent_overrides(
        provider_name="codex",
        executable="/tools/codex",
    )

    assert overrides["options"] == {"skip_git_repo_check": True}


def test_live_provider_agent_overrides_pin_qwen_model_by_default() -> None:
    """The live Qwen smoke should not depend on mutable account defaults."""

    overrides = _live_provider_agent_overrides(
        provider_name="qwen",
        executable="/tools/qwen",
    )

    assert overrides["model"] == "z-ai/glm-4.5-air"


def test_live_provider_agent_overrides_accept_provider_model_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators can override live provider smoke models without editing tests."""

    monkeypatch.setenv("WEFT_LIVE_PROVIDER_CLI_MODEL_QWEN", "custom/qwen-model")

    overrides = _live_provider_agent_overrides(
        provider_name="qwen",
        executable="/tools/qwen",
    )

    assert overrides["model"] == "custom/qwen-model"


def make_agent_taskspec(
    tid: str,
    *,
    persistent: bool = False,
    timeout: float = 30.0,
    tools: tuple[dict[str, object], ...] = (),
    reserved_error: ReservedPolicy = ReservedPolicy.KEEP,
    env: dict[str, str] | None = None,
    working_dir: str | None = None,
    agent_overrides: dict[str, object] | None = None,
) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="task-agent",
        spec=SpecSection(
            type="agent",
            persistent=persistent,
            timeout=timeout,
            reserved_policy_on_error=reserved_error,
            env=env,
            working_dir=working_dir,
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
    timeout: float = 20.0,
    limits: LimitsSection | None = None,
    tools: tuple[dict[str, object], ...] = (),
    env: dict[str, str] | None = None,
    working_dir: str | None = None,
    agent_overrides: dict[str, object] | None = None,
) -> TaskRunner:
    return TaskRunner(
        target_type="agent",
        tid=tid,
        function_target=None,
        process_target=None,
        agent=_agent_spec_payload(tools=tools, **(agent_overrides or {})),
        args=None,
        kwargs=None,
        env=env or {},
        working_dir=working_dir,
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


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_task_runner_executes_provider_cli_agent_successfully(
    tmp_path,
    provider_name: str,
) -> None:
    runner = make_agent_runner(
        env={"WEFT_PROVIDER_FIXTURE_ENV": "fixture-env"},
        working_dir=str(tmp_path),
        agent_overrides=_provider_agent_overrides(
            provider_name=provider_name,
            executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
        ),
    )

    outcome = runner.run("hello")

    assert outcome.ok
    payload = json.loads(outcome.value.aggregate_public_output())
    assert payload["provider"] == provider_name
    assert payload["cwd"] == str(tmp_path)
    assert payload["env_value"] == "fixture-env"
    assert payload["prompt"] == (
        "base instructions\n\n"
        "resolver instructions\n\n"
        "profile instructions\n\n"
        "resolved:hello"
    )
    if provider_name in {"gemini", "qwen"}:
        assert "yes" not in payload["options"]


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_task_runner_executes_provider_cli_agent_with_structured_tool_profile(
    tmp_path,
    provider_name: str,
) -> None:
    overrides = _provider_agent_overrides(
        provider_name=provider_name,
        executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
    )
    runtime_config = dict(overrides["runtime_config"])
    runtime_config["tool_profile_ref"] = (
        "tests.fixtures.runtime_profiles_fixture:structured_tool_profile"
    )
    overrides["runtime_config"] = runtime_config
    runner = make_agent_runner(
        working_dir=str(tmp_path),
        agent_overrides=overrides,
    )

    outcome = runner.run("hello")

    assert outcome.ok
    payload = json.loads(outcome.value.aggregate_public_output())
    if provider_name == "codex":
        assert payload["options"]["sandbox"] == "read-only"
    elif provider_name == "claude_code":
        assert payload["options"]["permission_mode"] == "plan"
    elif provider_name == "gemini":
        assert payload["options"]["approval_mode"] == "plan"
        assert payload["options"]["skip_trust"] is True
        assert "yes" not in payload["options"]
    elif provider_name == "qwen":
        assert payload["options"]["approval_mode"] == "plan"
        assert "yes" not in payload["options"]


@pytest.mark.parametrize(
    ("provider_name", "expected_option", "expected_value"),
    (
        ("claude_code", "permission_mode", "plan"),
        ("codex", "sandbox", "read-only"),
        ("gemini", "approval_mode", "plan"),
        ("qwen", "approval_mode", "plan"),
    ),
)
def test_task_runner_executes_explicit_bounded_provider_cli_agent_successfully(
    tmp_path,
    provider_name: str,
    expected_option: str,
    expected_value: str,
) -> None:
    overrides = _provider_agent_overrides(
        provider_name=provider_name,
        executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
        include_profiles=False,
    )
    overrides["authority_class"] = "bounded"
    runner = make_agent_runner(
        working_dir=str(tmp_path),
        agent_overrides=overrides,
    )

    outcome = runner.run("hello")

    assert outcome.ok
    payload = json.loads(outcome.value.aggregate_public_output())
    assert payload["provider"] == provider_name
    assert payload["options"][expected_option] == expected_value
    if provider_name == "gemini":
        assert payload["options"]["skip_trust"] is True
        assert "yes" not in payload["options"]
    if provider_name == "qwen":
        assert "yes" not in payload["options"]
        assert payload["options"]["extensions"] == ""
        assert payload["options"]["allowed_mcp_server_names"] == ""


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


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_task_runner_provider_cli_timeout(tmp_path, provider_name: str) -> None:
    runner = make_agent_runner(
        timeout=0.2,
        agent_overrides=_provider_agent_overrides(
            provider_name=provider_name,
            executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
            include_profiles=False,
        ),
    )

    outcome = runner.run("sleep:2")

    assert outcome.status == "timeout"
    assert outcome.error == "Target execution timed out"


def test_task_runner_construction_does_not_probe_provider_cli_startup(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_CLI_FIXTURE_FAIL_PROBE", "1")

    make_agent_runner(
        agent_overrides=_provider_agent_overrides(
            provider_name="codex",
            executable=str(write_provider_cli_wrapper(tmp_path, "codex")),
            include_profiles=False,
        ),
    )


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

    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)

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

    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)

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
    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)
    first = json.loads(outbox.read_one())

    assert first["task"] == "first"
    assert first["system"] == "persistent instructions"
    assert first["temperature"] == 0.3
    assert first["history"] == []
    assert task.taskspec.state.status == "running"

    inbox.write("inspect_json:second")
    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)
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
    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)
    first = outbox.read_one()

    assert first == "text:hello"
    assert task.taskspec.state.status == "running"

    inbox.write("__history__")
    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)
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

    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)

    result = json.loads(outbox.read_one())
    assert result == {"role": "assistant", "content": "text:hello"}
    assert task.taskspec.state.status == "completed"


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_consumer_processes_provider_cli_and_logs_agent_execution(
    broker_env,
    queue_factory,
    unique_tid: str,
    tmp_path,
    provider_name: str,
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            env={"WEFT_PROVIDER_FIXTURE_ENV": "fixture-env"},
            working_dir=str(tmp_path),
            agent_overrides=_provider_agent_overrides(
                provider_name=provider_name,
                executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
            ),
        ),
    )

    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")
    log_queue = queue_factory(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    inbox.write("hello")

    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)

    payload = json.loads(outbox.read_one())
    assert payload["provider"] == provider_name
    assert payload["cwd"] == str(tmp_path)
    assert payload["env_value"] == "fixture-env"
    assert task.taskspec.state.status == "completed"

    events = [
        json.loads(message)
        for message, _ts in (log_queue.peek_many(limit=50, with_timestamps=True) or [])
    ]
    completed = next(
        event
        for event in events
        if event.get("tid") == unique_tid and event.get("event") == "work_completed"
    )
    assert completed["agent_execution"]["runtime"] == "provider_cli"
    assert completed["agent_execution"]["metadata"]["provider"] == provider_name
    assert completed["agent_execution"]["artifacts"] == [
        {"kind": "fixture_artifact", "id": "artifact-1"}
    ]


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_consumer_persistent_provider_cli_per_task_continues_conversation(
    broker_env,
    unique_tid: str,
    tmp_path,
    provider_name: str,
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            persistent=True,
            env={"WEFT_PROVIDER_FIXTURE_ENV": "fixture-env"},
            working_dir=str(tmp_path),
            agent_overrides={
                **_provider_agent_overrides(
                    provider_name=provider_name,
                    executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
                ),
                "conversation_scope": "per_task",
            },
        ),
    )

    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")

    inbox.write("remember:phase2-token")
    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)
    first = json.loads(outbox.read_one())

    assert first["provider"] == provider_name
    assert first["turn_index"] == 1
    assert task.taskspec.state.status == "running"

    inbox.write("recall")
    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)
    second = json.loads(outbox.read_one())

    assert second["provider"] == provider_name
    assert second["turn_index"] == 2
    assert second["remembered"] == "phase2-token"
    assert second["cwd"] == str(tmp_path)
    assert second["env_value"] == "fixture-env"
    assert task.taskspec.state.status == "running"

    task.cleanup()


def test_consumer_processes_provider_cli_with_explicit_mcp_tool_profile(
    broker_env,
    unique_tid: str,
    tmp_path,
) -> None:
    db_path, make_queue = broker_env
    agent_overrides = _provider_agent_overrides(
        provider_name="claude_code",
        executable=str(write_provider_cli_wrapper(tmp_path, "claude_code")),
        include_profiles=False,
    )
    runtime_config = dict(agent_overrides["runtime_config"])
    runtime_config["tool_profile_ref"] = (
        "tests.fixtures.runtime_profiles_fixture:claude_stdio_mcp_tool_profile"
    )
    runtime_config["mcp_server_script"] = str(fixture_server_script_path())
    call_marker = tmp_path / "mcp-call-marker"
    runtime_config["mcp_call_marker"] = str(call_marker)
    agent_overrides["runtime_config"] = runtime_config
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            working_dir=str(tmp_path),
            agent_overrides=agent_overrides,
        ),
    )

    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")
    inbox.write("use_mcp:phase3-mcp-token")

    _drive_consumer_until(task, lambda: outbox.peek_one() is not None)

    payload = json.loads(outbox.read_one())
    assert payload["provider"] == "claude_code"
    assert payload["mcp_result"] == "phase3-mcp-token"
    assert call_marker.read_text(encoding="utf-8") == "phase3-mcp-token"
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

    _drive_consumer_until(task, lambda: task.taskspec.state.status == "failed")

    assert reserved.peek_one() is not None
    assert task.taskspec.state.status == "failed"


@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_consumer_applies_reserved_policy_on_provider_cli_failure(
    broker_env,
    unique_tid: str,
    tmp_path,
    provider_name: str,
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            reserved_error=ReservedPolicy.KEEP,
            agent_overrides=_provider_agent_overrides(
                provider_name=provider_name,
                executable=str(write_provider_cli_wrapper(tmp_path, provider_name)),
                include_profiles=False,
            ),
        ),
    )

    inbox = make_queue(f"T{unique_tid}.inbox")
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    inbox.write("fail:provider failed")

    _drive_consumer_until(task, lambda: task.taskspec.state.status == "failed")

    assert reserved.peek_one() is not None
    assert task.taskspec.state.status == "failed"


def _require_live_provider_executable(
    provider_name: str,
    *,
    require_mcp: bool = False,
) -> str:
    """Return a selected live provider executable or skip before selection."""

    if os.environ.get("WEFT_RUN_LIVE_PROVIDER_CLI_TESTS") != "1":
        pytest.skip("set WEFT_RUN_LIVE_PROVIDER_CLI_TESTS=1 to run live provider tests")
    if require_mcp and os.environ.get("WEFT_RUN_LIVE_PROVIDER_CLI_MCP_TESTS") != "1":
        pytest.skip(
            "set WEFT_RUN_LIVE_PROVIDER_CLI_MCP_TESTS=1 to run live provider MCP tests"
        )

    target_list = {
        entry.strip()
        for entry in os.environ.get("WEFT_LIVE_PROVIDER_CLI_TARGETS", "").split(",")
        if entry.strip()
    }
    if provider_name not in target_list:
        pytest.skip(
            f"add {provider_name} to WEFT_LIVE_PROVIDER_CLI_TARGETS to run its live smoke"
        )

    provider = get_provider_cli_provider(provider_name)
    executable = shutil.which(provider.default_executable)
    if executable is None:
        pytest.fail(
            f"{provider_name} executable is not installed: "
            f"{provider.default_executable}"
        )
    return executable


def test_live_provider_selection_fails_when_selected_executable_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A selected live provider must fail instead of producing a green skip."""

    monkeypatch.setenv("WEFT_RUN_LIVE_PROVIDER_CLI_TESTS", "1")
    monkeypatch.setenv("WEFT_LIVE_PROVIDER_CLI_TARGETS", "codex")
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(
        pytest.fail.Exception, match="codex executable is not installed"
    ):
        _require_live_provider_executable("codex")


@pytest.mark.slow
@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_consumer_live_provider_cli_smoke(
    broker_env,
    unique_tid: str,
    tmp_path,
    provider_name: str,
) -> None:
    executable = _require_live_provider_executable(provider_name)

    agent_overrides = _live_provider_agent_overrides(
        provider_name=provider_name,
        executable=executable,
    )

    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            timeout=_LIVE_PROVIDER_TIMEOUT_SECONDS,
            working_dir=str(tmp_path),
            agent_overrides=agent_overrides,
        ),
    )

    token = f"WEFT_PROVIDER_LIVE_SMOKE_{provider_name.upper()}"
    prompt = f"Reply with exactly this token and nothing else: {token}"

    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")
    inbox.write(prompt)

    _drive_live_provider_until_output(task, outbox)

    output = outbox.read_one()
    assert isinstance(output, str)
    assert token in output.upper()
    assert task.taskspec.state.status == "completed"


@pytest.mark.slow
@pytest.mark.parametrize("provider_name", PROVIDER_FIXTURE_NAMES)
def test_consumer_live_provider_cli_persistent_smoke(
    broker_env,
    unique_tid: str,
    tmp_path,
    provider_name: str,
) -> None:
    executable = _require_live_provider_executable(provider_name)

    agent_overrides = _live_provider_agent_overrides(
        provider_name=provider_name,
        executable=executable,
    )
    agent_overrides["conversation_scope"] = "per_task"

    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            persistent=True,
            timeout=_LIVE_PROVIDER_TIMEOUT_SECONDS,
            working_dir=str(tmp_path),
            agent_overrides=agent_overrides,
        ),
    )

    remember_token = f"WEFT_PROVIDER_PERSISTENT_{provider_name.upper()}"
    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")

    inbox.write(
        "Remember this exact token for the rest of this conversation: "
        f"{remember_token}. Reply with exactly OK."
    )
    _drive_live_provider_until_output(task, outbox)

    first_output = outbox.read_one()
    assert isinstance(first_output, str)
    assert task.taskspec.state.status == "running"

    inbox.write("Reply with exactly the token I asked you to remember earlier.")
    _drive_live_provider_until_output(task, outbox)

    second_output = outbox.read_one()
    assert isinstance(second_output, str)
    assert remember_token in second_output.upper()
    assert task.taskspec.state.status == "running"

    task.cleanup()


@pytest.mark.slow
def test_consumer_live_provider_cli_mcp_smoke(
    broker_env,
    unique_tid: str,
    tmp_path,
) -> None:
    executable = _require_live_provider_executable(
        "claude_code",
        require_mcp=True,
    )

    agent_overrides = _live_provider_agent_overrides(
        provider_name="claude_code",
        executable=executable,
    )
    runtime_config = dict(agent_overrides["runtime_config"])
    runtime_config["tool_profile_ref"] = (
        "tests.fixtures.runtime_profiles_fixture:claude_stdio_mcp_tool_profile"
    )
    runtime_config["mcp_server_script"] = str(fixture_server_script_path())
    call_marker = tmp_path / "mcp-call-marker"
    runtime_config["mcp_call_marker"] = str(call_marker)
    agent_overrides["runtime_config"] = runtime_config

    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_agent_taskspec(
            unique_tid,
            timeout=_LIVE_PROVIDER_TIMEOUT_SECONDS,
            working_dir=str(tmp_path),
            agent_overrides=agent_overrides,
        ),
    )

    token = "WEFT_PROVIDER_LIVE_MCP_CLAUDE"
    prompt = (
        "Use the MCP tool return_token once with token "
        f'"{token}". Reply with exactly the tool text and nothing else.'
    )
    inbox = make_queue(f"T{unique_tid}.inbox")
    outbox = make_queue(f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}")
    inbox.write(prompt)

    _drive_live_provider_until_output(task, outbox)

    output = outbox.read_one()
    assert isinstance(output, str)
    assert token in output.upper()
    assert call_marker.read_text(encoding="utf-8") == token
    assert task.taskspec.state.status == "completed"
