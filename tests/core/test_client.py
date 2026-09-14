"""Tests for the public Python client surface."""

from __future__ import annotations

import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import cast

import pytest

import weft._exceptions as exception_types
from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import (
    DEFAULT_TASK_COMPLETION_TIMEOUT,
    WeftTestHarness,
)
from tests.taskspec.fixtures import create_valid_provider_cli_agent_taskspec
from weft import commands
from weft._constants import (
    SUBMIT_OVERRIDE_NAMES,
    TASKSPEC_BUNDLE_ROOT_FIELD,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.client import (
    ControlRejected,
    InvalidTID,
    ManagerNotRunning,
    ManagerStartFailed,
    PreparedSubmission,
    SpecNotFound,
    Task,
    TaskEvent,
    TaskNotFound,
    TaskResult,
    TaskSnapshot,
    TaskTerminalSnapshot,
    WeftClient,
    WeftError,
    connect,
    normalize_taskspec_payload,
)
from weft.client._namespaces import (
    ManagersNamespace,
    QueueAliasesNamespace,
    QueuesNamespace,
    SpecsNamespace,
    SystemNamespace,
    TasksNamespace,
)
from weft.context import build_context
from weft.core.monitor.collation import MonitorTaskEventUpdate
from weft.core.monitor.store import open_monitor_store
from weft.core.task_state import task_state_queue_name
from weft.core.taskspec import TaskSpec
from weft.core.taskspec.transport import validate_taskspec_payload

pytestmark = [pytest.mark.shared]


def test_tasks_namespace_drops_inert_process_parameter() -> None:
    assert "include_process" not in inspect.signature(TasksNamespace.status).parameters
    assert "include_process" not in inspect.signature(TasksNamespace.watch).parameters


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _function_taskspec(
    root: Path,
    *,
    function_target: str = "tests.tasks.sample_targets:echo_payload",
    args: list[object] | None = None,
    kwargs: dict[str, object] | None = None,
) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "name": "client-task",
            "spec": {
                "type": "function",
                "function_target": function_target,
                "args": args or [],
                "keyword_args": kwargs or {},
                "weft_context": str(root),
            },
            "metadata": {},
        },
        context={"template": True, "auto_expand": False},
    )


def _assert_task_result_value(
    task: Task,
    harness: WeftTestHarness,
    expected: object,
) -> None:
    harness.register_tid(task.tid)
    result = task.result(timeout=DEFAULT_TASK_COMPLETION_TIMEOUT)
    if result.value == expected:
        return

    pytest.fail(
        "Task result mismatch:\n"
        f"  tid={task.tid}\n"
        f"  expected={expected!r}\n"
        f"  status={result.status!r}\n"
        f"  value={result.value!r}\n"
        f"  error={result.error!r}\n"
        f"{harness.dump_completion_timeout_state(task.tid)}"
    )


def test_public_names_are_importable() -> None:
    assert ControlRejected is not None
    assert InvalidTID is not None
    assert ManagerNotRunning is not None
    assert ManagerStartFailed is not None
    assert PreparedSubmission is not None
    assert SpecNotFound is not None
    assert Task is not None
    assert TaskEvent is not None
    assert TaskNotFound is not None
    assert TaskResult is not None
    assert TaskSnapshot is not None
    assert TaskTerminalSnapshot is not None
    assert WeftClient is not None
    assert WeftError is not None
    assert connect is not None


def test_public_exception_types_come_from_the_canonical_owner() -> None:
    public_types = (
        ControlRejected,
        InvalidTID,
        ManagerNotRunning,
        ManagerStartFailed,
        SpecNotFound,
        TaskNotFound,
        WeftError,
    )

    assert public_types == tuple(
        getattr(exception_types, exception_type.__name__)
        for exception_type in public_types
    )


CLIENT_API_PARITY_EXPECTATIONS = {
    "client": (
        WeftClient,
        {
            "from_context",
            "from_weft_context",
            "prepare",
            "prepare_pipeline",
            "prepare_spec",
            "submit",
            "submit_command",
            "submit_pipeline",
            "submit_spec",
            "task",
        },
    ),
    "task_handle": (
        Task,
        {
            "events",
            "follow",
            "kill",
            "ping",
            "realtime_events",
            "result",
            "snapshot",
            "stop",
            "terminal_snapshot",
        },
    ),
    "tasks": (
        TasksNamespace,
        {
            "ack_terminal_snapshot",
            "kill",
            "kill_many",
            "list",
            "ping",
            "resolve_tid",
            "stats",
            "status",
            "stop",
            "stop_many",
            "terminal_snapshot",
            "watch",
        },
    ),
    "queues": (
        QueuesNamespace,
        {
            "broadcast",
            "delete",
            "exists",
            "list",
            "move",
            "peek",
            "read",
            "resolve",
            "stats",
            "watch",
            "write",
            "write_endpoint",
        },
    ),
    "queue_aliases": (QueueAliasesNamespace, {"add", "list", "remove"}),
    "managers": (
        ManagersNamespace,
        {"list", "serve", "start", "status", "stop"},
    ),
    "specs": (
        SpecsNamespace,
        {"create", "delete", "generate", "list", "show", "validate"},
    ),
    "system": (
        SystemNamespace,
        {"builtins", "dump", "load", "status", "tidy"},
    ),
}

CLIENT_API_OMISSIONS = {
    "manager diagnostics": (
        "operator/debug output does not yet have a typed public result contract"
    ),
    "system task-monitor": (
        "foreground monitor scans are operator maintenance, not a stable library API"
    ),
    "system prune runtime-state": (
        "destructive/reporting maintenance remains CLI-only until scoped result "
        "types are promoted"
    ),
    "system prune retention": (
        "archive-producing cleanup is an operator workflow, not a task client "
        "capability"
    ),
}


def test_client_api_parity_guard_matches_current_public_matrix() -> None:
    missing: list[str] = []
    for group, (owner, methods) in CLIENT_API_PARITY_EXPECTATIONS.items():
        for method in sorted(methods):
            if not hasattr(owner, method):
                missing.append(f"{group}.{method}")

    assert not missing


@pytest.mark.parametrize(
    ("method_name", "command"),
    [("stop_many", "stop"), ("kill_many", "kill")],
)
def test_client_control_sweeps_delegate_to_structured_owner(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    command: str,
) -> None:
    client = WeftClient(path=Path.cwd())
    expected = object()
    observed: dict[str, object] = {}

    def fake_control(command_arg: str, tid: str | None, **kwargs: object):
        observed.update(command=command_arg, tid=tid, **kwargs)
        return expected

    monkeypatch.setattr("weft.commands.tasks._task_control_result", fake_control)

    result = getattr(client.tasks, method_name)(
        tids=("1777000000000000001",),
        all_tasks=False,
        pattern=None,
    )

    assert result is expected
    assert observed["command"] == command
    assert observed["tids"] == ("1777000000000000001",)
    assert observed["runtime_context"] is client.context


@pytest.mark.parametrize(
    ("method_name", "command"),
    [("stop_many", "stop"), ("kill_many", "kill")],
)
def test_client_control_sweep_without_scope_is_an_empty_noop(
    method_name: str,
    command: str,
) -> None:
    client = WeftClient(path=Path.cwd())

    result = getattr(client.tasks, method_name)()

    assert result == commands.TaskControlResult(
        command=command,
        requested=(),
        accepted=(),
        failures=(),
        snapshots=(),
    )


@pytest.mark.parametrize("method_name", ["stop_many", "kill_many"])
@pytest.mark.parametrize(
    "selector",
    [{"all_tasks": True}, {"pattern": "worker-*"}],
)
def test_client_control_sweep_preserves_non_tid_selector(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    selector: dict[str, object],
) -> None:
    client = WeftClient(path=Path.cwd())
    observed: dict[str, object] = {}

    def fake_control(command_arg: str, tid: str | None, **kwargs: object):
        observed.update(command=command_arg, tid=tid, **kwargs)
        return object()

    monkeypatch.setattr("weft.commands.tasks._task_control_result", fake_control)

    getattr(client.tasks, method_name)(**selector)

    assert observed["tids"] is None
    assert all(observed[key] == value for key, value in selector.items())


def test_client_api_omissions_are_explicitly_classified() -> None:
    assert CLIENT_API_OMISSIONS
    assert all(reason.strip() for reason in CLIENT_API_OMISSIONS.values())


def test_connect_resolves_context() -> None:
    with WeftTestHarness() as harness:
        client = connect(harness.root)

        assert isinstance(client, WeftClient)
        assert client.context.root.resolve() == harness.root.resolve()


def test_connect_resolves_context_from_path_keyword() -> None:
    with WeftTestHarness() as harness:
        client = connect(path=harness.root)

        assert isinstance(client, WeftClient)
        assert client.context.root.resolve() == harness.root.resolve()


def test_connect_rejects_ambiguous_context_arguments(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="either spec_context or path"):
        connect(tmp_path, path=tmp_path)


def test_submitted_task_is_not_public() -> None:
    import weft.client as client_mod

    assert not hasattr(client_mod, "SubmittedTask")


def test_legacy_forwarders_are_removed() -> None:
    assert not hasattr(WeftClient, "submit_taskspec")
    assert not hasattr(WeftClient, "submit_spec_reference")
    assert not hasattr(WeftClient, "submit_pipeline_reference")
    assert not hasattr(WeftClient, "status")
    assert not hasattr(WeftClient, "wait")
    assert not hasattr(WeftClient, "result")
    assert not hasattr(WeftClient, "stop")
    assert not hasattr(WeftClient, "kill")
    assert not hasattr(WeftClient, "events")


def test_submit_returns_task_with_completed_result() -> None:
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        client = WeftClient(path=harness.root)
        task = client.submit(
            _function_taskspec(
                harness.root,
                args=["hello"],
                kwargs={"suffix": "!"},
            )
        )
        harness.register_tid(task.tid)
        result = task.result(timeout=30.0)

        assert result.status == "completed"
        assert result.value == "hello!"
        assert task.tid.isdigit()
        assert len(task.tid) == 19


def test_task_terminal_snapshot_is_non_consuming() -> None:
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        client = WeftClient(path=harness.root)
        task = client.submit(
            _function_taskspec(
                harness.root,
                args=["hello"],
                kwargs={"suffix": "!"},
            )
        )
        snapshot = task.terminal_snapshot(timeout=30.0)

        assert snapshot.status == "completed"
        assert task.result(timeout=30.0).value == "hello!"


def test_task_terminal_snapshot_uses_monitor_store_terminal_fallback(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    store = open_monitor_store(context)
    store.ensure_schema()
    tid = "1779226615233825720"
    store.upsert_task_event(
        MonitorTaskEventUpdate(
            tid=tid,
            queue_name=WEFT_GLOBAL_LOG_QUEUE,
            message_id=int(tid) + 101,
            event="work_completed",
            status="completed",
            observed_at_ns=int(tid) + 101,
            name="client-retired-task",
            runner="host",
            terminal_seen=True,
            terminal_event="work_completed",
            terminal_status="completed",
            first_seen_at_ns=int(tid) - 10,
            last_seen_at_ns=int(tid) + 101,
            started_at_ns=int(tid) - 10,
            completed_at_ns=int(tid) + 101,
            taskspec_summary={
                "tid": tid,
                "name": "client-retired-task",
                "metadata": {"kind": "client-terminal-fallback"},
            },
            state={"status": "completed"},
            lifecycle={"event": "work_completed", "status": "completed"},
            resources={},
            diagnostics={},
            bookkeeping={},
        )
    )
    client = WeftClient(path=root)

    handle_snapshot = client.task(tid).terminal_snapshot()
    namespace_snapshot = client.tasks.terminal_snapshot(tid)

    assert handle_snapshot.status == "completed"
    assert handle_snapshot.source == "monitor_store"
    assert handle_snapshot.metadata["classification"] == "terminal_monitor_store"
    assert namespace_snapshot == handle_snapshot


@pytest.mark.timeout(15)
def test_task_terminal_snapshot_positive_timeout_returns_within_budget(
    tmp_path: Path,
) -> None:
    """A live task with no terminal evidence still honors the caller's budget.

    Verifies:
    - The public client bounds `terminal_snapshot(timeout=...)` on a real broker
    - Expiry yields a nonterminal snapshot instead of blocking or raising
    - The observation writes no task state
    """
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        log_queue.write(
            json.dumps(
                {
                    "event": "work_started",
                    "status": "running",
                    "tid": tid,
                    "taskspec": {
                        "tid": tid,
                        "name": "client-bounded-observation",
                        "spec": {
                            "type": "function",
                            "function_target": (
                                "tests.tasks.sample_targets:echo_payload"
                            ),
                            "runner": {"name": "host", "options": {}},
                        },
                        "io": {
                            "outputs": {"outbox": f"T{tid}.outbox"},
                            "control": {
                                "ctrl_in": f"T{tid}.ctrl_in",
                                "ctrl_out": f"T{tid}.ctrl_out",
                            },
                        },
                        "state": {
                            "status": "running",
                            "started_at": int(tid),
                            "completed_at": None,
                        },
                        "metadata": {},
                    },
                }
            )
        )
    finally:
        log_queue.close()
    client = connect(path=root, autostart=False)

    def _state_rows() -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        rows: list[list[tuple[str, int]]] = []
        for name in (WEFT_GLOBAL_LOG_QUEUE, task_state_queue_name(tid)):
            queue = context.queue(name, persistent=False)
            try:
                rows.append(list(queue.peek_many(1000, with_timestamps=True)))
            finally:
                queue.close()
        return rows[0], rows[1]

    log_before, mappings_before = _state_rows()
    started = time.monotonic()
    snapshot = client.task(tid).terminal_snapshot(timeout=0.05)
    elapsed = time.monotonic() - started

    # 0.05 s budget + two real broker reads + one poll interval + slack; the
    # mark.timeout above turns a regression into a failure, not a hang.
    assert elapsed < 1.5
    assert snapshot.terminal is False
    assert snapshot.status in {"running", "pending"}
    assert snapshot.ack_targets == ()
    # Non-consuming and non-publishing: no task-log or mapping row appended,
    # nothing consumed, no outbox row produced ([IP-1.1]).
    log_after, mappings_after = _state_rows()
    assert log_after == log_before
    assert mappings_after == mappings_before
    outbox = context.queue(f"T{tid}.outbox", persistent=False)
    try:
        assert outbox.peek_one() is None
    finally:
        outbox.close()


def test_prepare_snapshots_payload_before_submission() -> None:
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        client = WeftClient(path=harness.root)
        payload = {"value": "before"}
        prepared = client.prepare(
            _function_taskspec(harness.root),
            payload=payload,
        )
        payload["value"] = "after"

        task = prepared.submit()
        harness.register_tid(task.tid)
        result = task.result(timeout=30.0)

        assert prepared.name == "client-task"
        assert result.status == "completed"
        assert result.value == "{'value': 'before'}"


def test_submit_command_returns_task_with_completed_result() -> None:
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        client = WeftClient(path=harness.root)
        task = client.submit_command(["echo", "hello"])
        result = task.result(timeout=30.0)

        assert result.status == "completed"
        assert "hello" in (result.stdout or str(result.value))


def test_submit_spec_and_pipeline_references_return_tasks() -> None:
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        _write_json(
            harness.root / ".weft" / "tasks" / "stored-echo.json",
            {
                "name": "stored-echo",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
                "metadata": {},
            },
        )
        _write_json(
            harness.root / ".weft" / "tasks" / "pipeline-stage.json",
            {
                "name": "pipeline-stage",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
                "metadata": {},
            },
        )
        _write_json(
            harness.root / ".weft" / "pipelines" / "stored-pipeline.json",
            {
                "name": "stored-pipeline",
                "stages": [{"name": "only", "task": "pipeline-stage"}],
            },
        )
        client = WeftClient(path=harness.root)

        spec_task = client.submit_spec("stored-echo", payload="stored")
        pipeline_task = client.submit_pipeline("stored-pipeline", payload="pipeline")

        _assert_task_result_value(spec_task, harness, "stored")
        _assert_task_result_value(pipeline_task, harness, "pipeline")


def test_client_prepare_spec_translates_unknown_override() -> None:
    with WeftTestHarness() as harness:
        spec_path = harness.root / ".weft" / "tasks" / "stored-echo.json"
        _write_json(
            spec_path,
            {
                "name": "stored-echo",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            },
        )
        client = WeftClient(path=harness.root)

        with pytest.raises(
            exception_types.SubmissionValidationError,
            match="Unknown submit override",
        ):
            client.prepare_spec(spec_path, unknown_override=True)


def test_submitted_task_binds_materialized_runtime_context() -> None:
    with WeftTestHarness() as client_harness, WeftTestHarness() as runtime_harness:
        runtime_harness.ensure_foreground_manager()
        _write_json(
            client_harness.root / ".weft" / "tasks" / "remote-context.json",
            {
                "name": "remote-context",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "weft_context": str(runtime_harness.root),
                },
                "metadata": {},
            },
        )
        client = WeftClient(path=client_harness.root)

        task = client.submit_spec("remote-context", payload="remote")
        result = task.result(timeout=30.0)

        assert result.status == "completed"
        assert result.value == "remote"
        assert task.context is not None
        assert task.context.root == runtime_harness.context.root
        assert client.tasks.status(task.tid) is None


@pytest.mark.parametrize("surface", ["client", "cmd_run"])
@pytest.mark.parametrize("explicit_context", [False, True])
def test_spec_submission_uses_declared_runtime_broker_across_surfaces(
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    explicit_context: bool,
) -> None:
    with WeftTestHarness() as client_harness, WeftTestHarness() as runtime_harness:
        runtime_harness.ensure_foreground_manager()
        spec_path = client_harness.root / ".weft" / "tasks" / "remote-context.json"
        _write_json(
            spec_path,
            {
                "name": "remote-context",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "weft_context": str(runtime_harness.root),
                },
            },
        )
        with monkeypatch.context() as cwd_patch:
            cwd_patch.chdir(client_harness.root)

            if surface == "client":
                client = (
                    WeftClient(path=client_harness.root)
                    if explicit_context
                    else connect()
                )
                task = client.submit_spec(spec_path, payload="remote")
                task_result = task.result(timeout=30.0)
                tid = task.tid
                assert task.context is not None
                assert task.context.root == runtime_harness.context.root
            else:
                outcome = commands.cmd_run(
                    (),
                    spec=spec_path,
                    stdin_text="remote",
                    context=client_harness.root if explicit_context else None,
                    wait=True,
                )
                session = cast(commands.RunSession, outcome)
                try:
                    execution = session.wait()
                finally:
                    session.close()
                tid = execution.tid
                task_result = TaskResult(
                    tid=tid,
                    status=execution.status or "missing",
                    value=execution.result_value,
                    stdout=None,
                    stderr=None,
                    error=execution.error_message,
                )

            assert task_result.status == "completed"
            assert task_result.value == "remote"
            assert (
                commands.cmd_task_status(tid, context=runtime_harness.root).tid == tid
            )
            with pytest.raises(TaskNotFound):
                commands.cmd_task_status(tid, context=client_harness.root)


def test_task_result_propagates_wait_expiry_and_preserves_terminal_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = WeftClient(path=Path.cwd())
    task = Task(client, "1777000000000000001")
    terminal = TaskResult(
        tid=task.tid,
        status="timeout",
        value=None,
        stdout=None,
        stderr=None,
        error="task timed out",
    )
    monkeypatch.setattr(
        "weft.commands.result.await_task_result",
        lambda *_args, **_kwargs: terminal,
    )
    assert task.result(timeout=0.1) is terminal

    monkeypatch.setattr(
        "weft.commands.result.await_task_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            exception_types.CommandTimeoutError("wait expired")
        ),
    )
    with pytest.raises(exception_types.CommandTimeoutError, match="wait expired"):
        task.result(timeout=0.1)


def test_task_snapshot_and_tasks_namespace_status_agree() -> None:
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        client = WeftClient(path=harness.root)
        task = client.submit_command(["echo", "status"])
        result = task.result(timeout=30.0)
        task_snapshot = task.snapshot()
        namespace_snapshot = client.tasks.status(task.tid)

        assert result.status == "completed"
        assert task_snapshot is not None
        assert namespace_snapshot is not None
        assert task_snapshot.tid == namespace_snapshot.tid
        assert task_snapshot.status == namespace_snapshot.status == "completed"


def test_queue_alias_roundtrip() -> None:
    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        client.queues.aliases.add("my-alias", "test.queue")
        aliases = client.queues.aliases.list()
        client.queues.aliases.remove("my-alias")
        aliases_after_remove = client.queues.aliases.list()

        assert any(
            item.alias == "my-alias" and item.target == "test.queue" for item in aliases
        )
        assert all(item.alias != "my-alias" for item in aliases_after_remove)


def test_task_follow_ends_with_result_event() -> None:
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        client = WeftClient(path=harness.root)
        task = client.submit_command(["echo", "done"])
        events = list(task.follow(timeout=30.0))

        assert events[-1].event_type == "result"
        assert events[-1].payload["status"] == "completed"


def test_task_realtime_events_expose_browser_event_contract() -> None:
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        client = WeftClient(path=harness.root)
        task = client.submit_command(["echo", "done"])
        events = list(task.realtime_events(timeout=30.0))

        event_types = [event.event_type for event in events]
        assert "snapshot" in event_types
        assert "state" in event_types
        assert "result" in event_types
        assert event_types[-1] == "end"


def test_system_and_manager_namespaces_expose_shared_runtime_state() -> None:
    with WeftTestHarness() as harness:
        record = harness.ensure_foreground_manager()
        client = WeftClient(path=harness.root)
        manager_tid = str(record["tid"])
        status_snapshot = client.system.status()
        manager_snapshot = client.managers.status(manager_tid)

        assert manager_tid
        assert manager_snapshot is not None
        system_manager = next(
            item for item in status_snapshot.managers if item.tid == manager_tid
        )
        assert system_manager == manager_snapshot


def test_tasks_watch_yields_terminal_snapshot() -> None:
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        client = WeftClient(path=harness.root)
        task = client.submit_command(["echo", "watched"])
        snapshots = list(client.tasks.watch(task.tid, timeout=30.0))

        assert snapshots
        assert snapshots[-1].tid == task.tid
        assert snapshots[-1].status == "completed"


def test_task_stop_and_kill_delegate_through_shared_task_ops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object, float | None]] = []

    def _fake_stop(tid: str, *, context=None, context_path=None) -> None:
        calls.append(("stop", tid, context or context_path, None))

    def _fake_kill(tid: str, *, context=None, context_path=None) -> None:
        calls.append(("kill", tid, context or context_path, None))

    def _fake_ping(
        tid: str,
        *,
        timeout: float,
        context=None,
        context_path=None,
    ) -> dict[str, object]:
        calls.append(("ping", tid, context or context_path, timeout))
        return {"timed_out": False, "error": None, "observed_at": 123, "pong": {}}

    monkeypatch.setattr("weft.commands.tasks.stop_task", _fake_stop)
    monkeypatch.setattr("weft.commands.tasks.kill_task", _fake_kill)
    monkeypatch.setattr("weft.commands.tasks.task_ping", _fake_ping)

    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        task = client.task("1776000000000000001")

        task.stop()
        task.kill()
        assert task.ping(timeout=1.25)["observed_at"] == 123
        assert client.tasks.ping(task.tid, timeout=2.5)["observed_at"] == 123

        assert calls == [
            ("stop", "1776000000000000001", client.context, None),
            ("kill", "1776000000000000001", client.context, None),
            ("ping", "1776000000000000001", client.context, 1.25),
            ("ping", "1776000000000000001", client.context, 2.5),
        ]


def test_specs_namespace_create_validate_and_delete_roundtrip() -> None:
    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        payload = {
            "name": "stored-via-client",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        }

        record = client.specs.create("stored-via-client", payload)
        validation = client.specs.validate(payload)
        shown = client.specs.show("stored-via-client")
        deleted = client.specs.delete("stored-via-client")

        assert record.name == "stored-via-client"
        assert validation.valid is True
        assert shown["name"] == "stored-via-client"
        assert deleted == record.path
        assert not record.path.exists()


def test_specs_namespace_preflight_reports_missing_agent_runtime() -> None:
    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        taskspec = create_valid_provider_cli_agent_taskspec(
            executable="/nonexistent/provider-cli",
        )

        validation = client.specs.validate(
            taskspec.model_dump(mode="json"),
            preflight=True,
        )

        assert validation.valid is False
        assert validation.warnings == []
        assert (
            "Unable to locate executable"
            in validation.errors_by_stage["agent_runtime"]["agent_runtime"]
        )


def test_specs_namespace_validate_uses_bound_client_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        relative_path = Path(".weft/tasks/validate-me.json")
        _write_json(
            harness.root / relative_path,
            {
                "name": "validate-me",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
                "metadata": {},
            },
        )

        with monkeypatch.context() as cwd_patch:
            cwd_patch.chdir(tmp_path)
            validation = client.specs.validate(relative_path)

        assert validation.valid is True
        assert validation.payload is not None
        assert validation.payload["name"] == "validate-me"


def test_system_dump_load_and_tidy_are_available() -> None:
    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        client.queues.write("client.dump.queue", "message")

        export_path = client.system.dump()
        load_result = client.system.load(input_file=export_path, dry_run=True)
        tidy_result = client.system.tidy()

        assert export_path.exists()
        assert load_result.message
        assert tidy_result.target


def test_prepare_snapshots_payload_without_starting_runtime() -> None:
    """Snapshot ownership is settled at prepare, independently of scheduling."""
    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        payload = {"value": ["before"]}
        prepared = client.prepare(_function_taskspec(harness.root), payload=payload)
        payload["value"].append("after")
        # PreparedSubmission owns this request before any runtime is launched.
        assert prepared._request.payload == {"value": ["before"]}
        assert harness._list_active_manager_records() == []


def _declared_taskspec(*, working_dir: str = "/tmp") -> dict[str, object]:
    """Template declaring a non-default value for every overridable field.

    A default-valued template cannot prove `None`-retention, so every path the
    `None` matrix asserts already carries a distinguishable value here.
    """

    return {
        "name": "declared",
        "spec": {
            "type": "function",
            "function_target": "os:getcwd",
            "args": [],
            "keyword_args": {},
            "env": {"DECLARED": "1"},
            "working_dir": working_dir,
            "stream_output": True,
            "timeout": 30.0,
            "limits": {"memory_mb": 256, "cpu_percent": 50},
            "runner": {"name": "host", "options": {"declared": True}},
        },
        "metadata": {
            "description": "declared description",
            "tags": ["declared"],
            "declared_key": "declared",
        },
    }


_DECLARED_OVERRIDE_PATHS: dict[str, tuple[str, ...]] = {
    "name": ("name",),
    "description": ("metadata", "description"),
    "tags": ("metadata", "tags"),
    "env": ("spec", "env"),
    "working_dir": ("spec", "working_dir"),
    "stream_output": ("spec", "stream_output"),
    "timeout": ("spec", "timeout"),
    "memory_mb": ("spec", "limits", "memory_mb"),
    "cpu_percent": ("spec", "limits", "cpu_percent"),
    "runner": ("spec", "runner", "name"),
    "runner_options": ("spec", "runner", "options"),
    "metadata": ("metadata", "declared_key"),
}

_DECLARED_OVERRIDE_VALUES: dict[str, object] = {
    "name": "declared",
    "description": "declared description",
    "tags": ["declared"],
    "env": {"DECLARED": "1"},
    "working_dir": "/tmp",
    "stream_output": True,
    "timeout": 30.0,
    "memory_mb": 256,
    "cpu_percent": 50,
    "runner": "host",
    "runner_options": {"declared": True},
    "metadata": "declared",
}


def _at_path(payload: object, path: tuple[str, ...]) -> object:
    current = payload
    for key in path:
        assert isinstance(current, dict)
        current = current[key]
    return current


@pytest.mark.parametrize("override_name", sorted(SUBMIT_OVERRIDE_NAMES))
def test_normalize_taskspec_payload_ignores_none_for_every_override(
    override_name: str,
) -> None:
    """An explicit `None` override never clears a declared value.

    Verifies:
    - `normalize_taskspec_payload(spec, <name>=None)` equals the plain dump
    - The declared value is still present at that field's own path
    """

    baseline = normalize_taskspec_payload(_declared_taskspec())
    with_none = normalize_taskspec_payload(
        _declared_taskspec(),
        **{override_name: None},
    )

    assert with_none == baseline
    path = _DECLARED_OVERRIDE_PATHS[override_name]
    assert _at_path(with_none, path) == _DECLARED_OVERRIDE_VALUES[override_name]


@pytest.mark.parametrize(
    ("override_name", "value", "expected"),
    [
        ("name", "renamed", "renamed"),
        ("description", "d", "d"),
        ("tags", ("a", "b"), ["a", "b"]),
        ("env", {"K": "v"}, {"DECLARED": "1", "K": "v"}),
        ("stream_output", False, False),
        ("timeout", 5.0, 5.0),
        ("memory_mb", 512, 512),
        ("cpu_percent", 25, 25),
        ("runner", "host", "host"),
        ("runner_options", {"x": 1}, {"declared": True, "x": 1}),
        ("metadata", {"k": "v"}, "declared"),
    ],
)
def test_normalize_taskspec_payload_applies_every_override(
    override_name: str,
    value: object,
    expected: object,
) -> None:
    """Each public override name lands at its own path with core merge rules.

    Verifies:
    - Scalar overrides replace; mapping overrides merge with declared entries
    - `metadata` merges rather than replacing the declared metadata section
    """

    exported = normalize_taskspec_payload(
        _declared_taskspec(),
        **{override_name: value},
    )

    assert _at_path(exported, _DECLARED_OVERRIDE_PATHS[override_name]) == expected
    if override_name == "metadata":
        assert exported["metadata"]["k"] == "v"


def test_normalize_taskspec_payload_applies_working_dir_override(
    tmp_path: Path,
) -> None:
    """`working_dir` is an ordinary override taking a real path value."""

    exported = normalize_taskspec_payload(
        _declared_taskspec(),
        working_dir=str(tmp_path),
    )

    assert exported["spec"]["working_dir"] == str(tmp_path)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod read-only directory semantics differ on Windows",
)
def test_normalize_taskspec_payload_is_pure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The seam builds no context, reads no config, opens no broker, writes nothing.

    Verifies:
    - Tripwires on `build_context`, `resolve_runtime_config`, and `open_broker` at their
      use-site bindings (by-name imports make the defining module the wrong
      patch target) never fire
    - No file appears in a read-only cwd and no new `.weft` anywhere up the chain
    - Overrides still apply and a `None` override keeps the declared value
    """

    for key in [name for name in os.environ if name.startswith("WEFT")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("forbidden on the export path")

    monkeypatch.setattr("weft.client._client.build_context", _forbidden)
    monkeypatch.setattr("weft.context.resolve_runtime_config", _forbidden)
    monkeypatch.setattr("weft.context.open_broker", _forbidden)

    parents = [tmp_path, *tmp_path.parents]
    weft_dirs_before = {parent for parent in parents if (parent / ".weft").exists()}

    tmp_path.chmod(0o500)
    try:
        exported = normalize_taskspec_payload(
            _declared_taskspec(),
            timeout=None,
            name="renamed",
        )
    finally:
        tmp_path.chmod(0o700)

    assert list(tmp_path.iterdir()) == []
    weft_dirs_after = {parent for parent in parents if (parent / ".weft").exists()}
    assert weft_dirs_after == weft_dirs_before
    assert exported["name"] == "renamed"
    assert exported["spec"]["timeout"] == 30.0


def test_normalize_taskspec_payload_is_a_fresh_copy() -> None:
    """Every call returns an independent JSON-serializable template mapping.

    Verifies:
    - Two calls are equal but not identical, and mutation does not leak
    - The result is JSON-serializable and carries `tid: None`
    """

    first = normalize_taskspec_payload(_declared_taskspec())
    second = normalize_taskspec_payload(_declared_taskspec())

    assert first == second
    assert first is not second

    first["spec"]["timeout"] = 999.0
    third = normalize_taskspec_payload(_declared_taskspec())

    assert third["spec"]["timeout"] == 30.0
    assert json.dumps(first)
    assert first["tid"] is None


def test_normalize_taskspec_payload_omits_top_level_bundle_root(
    tmp_path: Path,
) -> None:
    """Bundle provenance is stripped; nested caller metadata is untouched.

    Verifies:
    - A bundle-rooted TaskSpec is accepted and its provenance is absent
    - The top-level-only guarantee leaves a nested marker key intact
    """

    rooted = validate_taskspec_payload(
        _declared_taskspec(),
        bundle_root=tmp_path,
        template=True,
    )
    assert rooted.get_bundle_root() is not None

    assert TASKSPEC_BUNDLE_ROOT_FIELD not in normalize_taskspec_payload(rooted)

    nested_template = _declared_taskspec()
    metadata_section = nested_template["metadata"]
    assert isinstance(metadata_section, dict)
    metadata_section["nested"] = {TASKSPEC_BUNDLE_ROOT_FIELD: "x"}
    exported = normalize_taskspec_payload(nested_template)

    assert TASKSPEC_BUNDLE_ROOT_FIELD not in exported
    assert exported["metadata"]["nested"][TASKSPEC_BUNDLE_ROOT_FIELD] == "x"


def test_normalize_taskspec_payload_raises_like_prepare() -> None:
    """The seam raises exactly what `prepare(...)` raises for the same inputs.

    Verifies:
    - Unknown names, the submission-only `wait`, and `payload` raise TypeError
    - Schema-invalid values and reserved `_weft.` names raise ValueError
    """

    with pytest.raises(TypeError, match="Unknown submit override"):
        normalize_taskspec_payload(_declared_taskspec(), unknown_override=True)
    with pytest.raises(ValueError):
        normalize_taskspec_payload(_declared_taskspec(), memory_mb=0)
    with pytest.raises(ValueError, match="reserved"):
        normalize_taskspec_payload(_declared_taskspec(), name="_weft.x")
    with pytest.raises(TypeError, match="Unknown submit override"):
        normalize_taskspec_payload(_declared_taskspec(), wait=True)
    with pytest.raises(TypeError, match=r"Unknown submit override\(s\): payload"):
        normalize_taskspec_payload(_declared_taskspec(), payload={"x": 1})


def test_normalize_taskspec_payload_matches_submitted_definition() -> None:
    """The exported definition is what `submit(...)` would write.

    Verifies:
    - Submitting the exported dict yields the same name and metadata as
      submitting the template with the same overrides
    - The proof runs through a real broker and manager, not a stub
    """

    overrides: dict[str, object] = {
        "name": "renamed",
        "timeout": 7.5,
        "metadata": {"k": "v"},
    }
    with WeftTestHarness() as harness:
        harness.ensure_foreground_manager()
        client = WeftClient(path=harness.root)

        exported = normalize_taskspec_payload(
            _function_taskspec(harness.root),
            **overrides,
        )
        exported_task = client.submit(exported)
        exported_task.result(timeout=DEFAULT_TASK_COMPLETION_TIMEOUT)

        direct_task = client.submit(_function_taskspec(harness.root), **overrides)
        direct_task.result(timeout=DEFAULT_TASK_COMPLETION_TIMEOUT)

        exported_snapshot = client.tasks.status(exported_task.tid)
        direct_snapshot = client.tasks.status(direct_task.tid)

        assert exported_snapshot is not None
        assert direct_snapshot is not None
        assert exported_snapshot.name == "renamed"
        assert exported_snapshot.metadata["k"] == "v"
        assert direct_snapshot.name == exported_snapshot.name
        assert direct_snapshot.metadata["k"] == exported_snapshot.metadata["k"]


def test_client_prepare_and_submit_raise_raw_type_error_for_unknown_override() -> None:
    """`prepare`/`submit` do not translate an unknown override name ([PY-3])."""

    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        spec = _function_taskspec(harness.root)

        with pytest.raises(TypeError, match="Unknown submit override"):
            client.prepare(spec, unknown_override=True)
        with pytest.raises(TypeError, match="Unknown submit override"):
            client.submit(spec, unknown_override=True)


def test_client_pipeline_and_command_raise_raw_type_error_for_unknown_override() -> (
    None
):
    """`prepare_pipeline`/`submit_pipeline`/`submit_command` raise raw TypeError."""

    with WeftTestHarness() as harness:
        _write_json(
            harness.root / ".weft" / "tasks" / "pipeline-stage.json",
            {
                "name": "pipeline-stage",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
                "metadata": {},
            },
        )
        _write_json(
            harness.root / ".weft" / "pipelines" / "stored-pipeline.json",
            {
                "name": "stored-pipeline",
                "stages": [{"name": "only", "task": "pipeline-stage"}],
            },
        )
        client = WeftClient(path=harness.root)

        with pytest.raises(TypeError, match="Unknown submit override"):
            client.prepare_pipeline("stored-pipeline", unknown_override=True)
        with pytest.raises(TypeError, match="Unknown submit override"):
            client.submit_pipeline("stored-pipeline", unknown_override=True)
        with pytest.raises(TypeError, match="Unknown submit override"):
            client.submit_command(["true"], unknown_override=True)


def test_client_prepare_spec_translates_invalid_override_value() -> None:
    """`prepare_spec`/`submit_spec` translate a schema-invalid value ([PY-3])."""

    with WeftTestHarness() as harness:
        spec_path = harness.root / ".weft" / "tasks" / "stored-echo.json"
        _write_json(
            spec_path,
            {
                "name": "stored-echo",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            },
        )
        client = WeftClient(path=harness.root)

        with pytest.raises(exception_types.SubmissionValidationError):
            client.prepare_spec(spec_path, memory_mb=0)
        with pytest.raises(exception_types.SubmissionValidationError):
            client.submit_spec(spec_path, memory_mb=0)
