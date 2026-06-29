"""Tests for the public Python client surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import (
    DEFAULT_TASK_COMPLETION_TIMEOUT,
    WeftTestHarness,
)
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.client import (
    ControlRejected,
    InvalidTID,
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
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared]


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
        assert any(item.tid == manager_tid for item in status_snapshot.managers)


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
