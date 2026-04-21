"""Tests for the public Python client surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.weft_harness import WeftTestHarness
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
    WeftClient,
    WeftError,
)
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
    assert WeftClient is not None
    assert WeftError is not None


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


def test_prepare_snapshots_payload_before_submission() -> None:
    with WeftTestHarness() as harness:
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
        client = WeftClient(path=harness.root)
        task = client.submit_command(["echo", "hello"])
        result = task.result(timeout=30.0)

        assert result.status == "completed"
        assert "hello" in (result.stdout or str(result.value))


def test_submit_spec_and_pipeline_references_return_tasks() -> None:
    with WeftTestHarness() as harness:
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

        assert spec_task.result(timeout=30.0).value == "stored"
        assert pipeline_task.result(timeout=30.0).value == "pipeline"


def test_task_snapshot_and_tasks_namespace_status_agree() -> None:
    with WeftTestHarness() as harness:
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
        client = WeftClient(path=harness.root)
        task = client.submit_command(["echo", "done"])
        events = list(task.follow())

        assert events[-1].event_type == "result"
        assert events[-1].payload["status"] == "completed"


def test_task_realtime_events_expose_browser_event_contract() -> None:
    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        task = client.submit_command(["echo", "done"])
        events = list(task.realtime_events())

        event_types = [event.event_type for event in events]
        assert "snapshot" in event_types
        assert "state" in event_types
        assert "result" in event_types
        assert event_types[-1] == "end"


def test_system_and_manager_namespaces_expose_shared_runtime_state() -> None:
    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        manager = client.managers.start()
        status_snapshot = client.system.status()
        manager_snapshot = client.managers.status(manager.tid)
        client.managers.stop(manager.tid)

        assert manager.tid
        assert manager_snapshot is not None
        assert any(item.tid == manager.tid for item in status_snapshot.managers)


def test_tasks_watch_yields_terminal_snapshot() -> None:
    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        task = client.submit_command(["echo", "watched"])
        snapshots = list(client.tasks.watch(task.tid))

        assert snapshots
        assert snapshots[-1].tid == task.tid
        assert snapshots[-1].status == "completed"


def test_task_stop_and_kill_delegate_through_shared_task_ops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object]] = []

    def _fake_stop(tid: str, *, context=None, context_path=None) -> None:
        calls.append(("stop", tid, context or context_path))

    def _fake_kill(tid: str, *, context=None, context_path=None) -> None:
        calls.append(("kill", tid, context or context_path))

    monkeypatch.setattr("weft.commands.tasks.stop_task", _fake_stop)
    monkeypatch.setattr("weft.commands.tasks.kill_task", _fake_kill)

    with WeftTestHarness() as harness:
        client = WeftClient(path=harness.root)
        task = client.task("1776000000000000001")

        task.stop()
        task.kill()

        assert calls == [
            ("stop", "1776000000000000001", client.context),
            ("kill", "1776000000000000001", client.context),
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
