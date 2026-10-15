"""Public `weft.commands.cmd_run` contract tests [PY-2]."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft import commands
from weft.commands import _result_wait as result_wait
from weft.commands import run as run_commands
from weft.commands import tasks as task_commands
from weft.commands.run import _LiveRunSession
from weft.commands.types import RunExecutionResult, RunSpecDescription, TaskResult
from weft.context import WeftContext, build_context
from weft.core.manager_runtime import ManagerEnsureResult
from weft.core.taskspec import TaskSpec

pytestmark = pytest.mark.shared


def _submission_spec(tid: str, context: WeftContext) -> TaskSpec:
    """Build accepted configuration for execution-boundary fixtures."""
    return TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "fixture-run",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(context.root),
            },
        }
    )


@pytest.mark.parametrize("collection", ["wait", "events"])
def test_run_session_collects_custom_output_without_child_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collection: str,
) -> None:
    """Accepted run config routes collection before a child TaskSpec exists [MF-5]."""
    project = prepare_project_root(tmp_path / "project")
    context = build_context(project)
    spec_path = project / "custom-output.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "custom-output",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "weft_context": str(project),
                },
                "io": {
                    "outputs": {"outbox": "run.custom.result"},
                    "control": {
                        "ctrl_in": "run.custom.ctrl_in",
                        "ctrl_out": "run.custom.ctrl_out",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    # Hold the external manager boundary. Submission, spec shaping, and
    # observation remain real; no child has published its initialization event.
    monkeypatch.setattr(
        run_commands,
        "_ensure_manager_after_submission",
        lambda *_args, **_kwargs: ManagerEnsureResult(
            outcome="not_needed",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="test_manager_held",
        ),
    )
    session = commands.cmd_run((), spec=spec_path, context=project, wait=True)
    assert isinstance(session, _LiveRunSession)
    try:
        with context.queue("run.custom.result", persistent=True) as outbox:
            outbox.write(json.dumps({"briefing": "ready"}))
        with context.queue("run.custom.ctrl_out", persistent=True) as control:
            control.write(
                json.dumps(
                    {
                        "type": "terminal",
                        "source": "task",
                        "tid": session.tid,
                        "status": "completed",
                    }
                )
            )

        def refuse_discovery(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("accepted run must not rediscover its specification")

        # This forbidden operation is part of MF-5, rather than an incidental
        # call count. It also prevents a missing route from hanging the events case.
        monkeypatch.setattr(
            result_wait.task_evidence, "discover_task_observation", refuse_discovery
        )
        if collection == "wait":
            result = session.wait(timeout=0.0)
            assert result.tid == session.tid
            assert result.status == "completed"
            assert result.result_value == {"briefing": "ready"}
        else:
            events = list(session.events())
            result_event = next(
                event for event in events if event.event_type == "result"
            )
            assert result_event.tid == session.tid
            assert result_event.payload["status"] == "completed"
            assert result_event.payload["value"] == {"briefing": "ready"}
            assert events[-1].event_type == "end"
    finally:
        session.close()


def test_cmd_run_has_the_cli_derived_public_signature() -> None:
    """The Python command reads like the CLI and excludes presentation flags."""

    parameters = inspect.signature(commands.cmd_run).parameters

    assert tuple(parameters) == (
        "command",
        "spec_args",
        "spec",
        "pipeline",
        "input",
        "function",
        "arg",
        "kw",
        "env",
        "name",
        "interactive",
        "stream_output",
        "timeout",
        "memory",
        "cpu",
        "tag",
        "context",
        "wait",
        "continuous",
        "autostart",
        "describe",
        "stdin_text",
    )
    assert "json_output" not in parameters
    assert "verbose" not in parameters


def test_cmd_run_describe_returns_structured_spec_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Description mode resolves metadata and never enters execution."""

    declaration = type(
        "Declaration",
        (),
        {
            "type": "str",
            "required": True,
            "default": None,
            "choices": (),
            "help": "Target name",
            "model_dump": lambda self, **_kwargs: {
                "type": "str",
                "required": True,
                "help": "Target name",
            },
        },
    )()
    taskspec = type(
        "TaskSpecDouble",
        (),
        {
            "name": "demo",
            "description": "Demo task",
            "spec": type(
                "SpecDouble",
                (),
                {
                    "parameterization": type(
                        "Parameterization", (), {"arguments": {"target": declaration}}
                    )(),
                    "run_input": None,
                },
            )(),
        },
    )()
    monkeypatch.setattr(
        "weft.commands.run._load_taskspec_reference",
        lambda *_args, **_kwargs: taskspec,
    )
    monkeypatch.setattr(
        "weft.commands.run.execute_run",
        lambda *_args, **_kwargs: pytest.fail("description mode submitted work"),
    )

    result = commands.cmd_run((), spec="demo", describe=True)

    assert isinstance(result, RunSpecDescription)
    assert result.reference == "demo"
    assert result.arguments == (
        {
            "name": "target",
            "type": "str",
            "required": True,
            "help": "Target name",
        },
    )
    assert result.stdin is None
    assert "Spec Help: demo" in result.usage


def test_cmd_run_no_wait_returns_execution_result_without_reading_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public seam forwards only caller-supplied input to the engine."""

    observed: dict[str, Any] = {}

    def fake_execute(command: tuple[str, ...], **kwargs: Any) -> RunExecutionResult:
        observed.update(kwargs)
        return RunExecutionResult(tid="123")

    monkeypatch.setattr("weft.commands.run.execute_run", fake_execute)

    result = commands.cmd_run(
        ("worker",),
        wait=False,
        stdin_text="payload",
        context=Path("project"),
    )

    assert isinstance(result, RunExecutionResult)
    assert result.tid == "123"
    assert observed["stdin_text"] == "payload"
    assert observed["context_dir"] == Path("project")
    assert observed["json_output"] is False
    assert observed["verbose"] is False


def test_cmd_run_wait_returns_a_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Wait mode returns before result collection and exposes a live session."""

    execution = RunExecutionResult(
        tid="1780000000000000001",
        error_prefix="Custom failure",
        submitted_payload={"tid": "1780000000000000001", "status": "submitted"},
        manager_started_payload={"tid": "manager", "status": "active"},
    )
    context = build_context(str(tmp_path))

    def fake_execute(*_args: Any, **kwargs: Any) -> RunExecutionResult:
        assert "wait" not in kwargs
        assert kwargs["session_wait_requested"] is True
        kwargs["on_submitted"](
            "1780000000000000001",
            context,
            _submission_spec("1780000000000000001", context),
        )
        return execution

    monkeypatch.setattr("weft.commands.run.execute_run", fake_execute)
    monkeypatch.setattr(
        "weft.commands.run.await_task_result",
        lambda *_args, **_kwargs: TaskResult(
            tid="1780000000000000001",
            status="completed",
            value="ok",
            stdout=None,
            stderr=None,
            error=None,
        ),
    )

    session = commands.cmd_run(("worker",), wait=True)

    try:
        assert session.tid == "1780000000000000001"
        expected = RunExecutionResult(
            tid="1780000000000000001",
            status="completed",
            result_value="ok",
            error_prefix="Custom failure",
            submitted_payload={"tid": "1780000000000000001", "status": "submitted"},
            manager_started_payload={"tid": "manager", "status": "active"},
        )
        assert session.wait() == expected
        assert session.wait() is session.wait()
    finally:
        session.close()
        session.close()


def test_run_session_wait_propagates_deadline_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = build_context(str(tmp_path))
    execution = RunExecutionResult(tid="1780000000000000001")

    def fake_execute(*_args: Any, **kwargs: Any) -> RunExecutionResult:
        kwargs["on_submitted"](
            "1780000000000000001",
            context,
            _submission_spec("1780000000000000001", context),
        )
        return execution

    monkeypatch.setattr("weft.commands.run.execute_run", fake_execute)
    monkeypatch.setattr(
        "weft.commands.run.await_task_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            commands.CommandTimeoutError("wait expired")
        ),
    )

    session = commands.cmd_run(("worker",), wait=True)
    try:
        with pytest.raises(commands.CommandTimeoutError, match="wait expired"):
            session.wait()
    finally:
        session.close()


def test_run_session_wait_preserves_terminal_task_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = build_context(str(tmp_path))

    def fake_execute(*_args: Any, **kwargs: Any) -> RunExecutionResult:
        kwargs["on_submitted"](
            "1780000000000000001",
            context,
            _submission_spec("1780000000000000001", context),
        )
        return RunExecutionResult(
            tid="1780000000000000001", error_prefix="Target failed"
        )

    monkeypatch.setattr("weft.commands.run.execute_run", fake_execute)
    monkeypatch.setattr(
        "weft.commands.run.await_task_result",
        lambda *_args, **_kwargs: TaskResult(
            tid="1780000000000000001",
            status="timeout",
            value=None,
            stdout=None,
            stderr=None,
            error="target timed out",
        ),
    )

    session = commands.cmd_run(("worker",), wait=True)

    try:
        assert session.wait() == RunExecutionResult(
            tid="1780000000000000001",
            status="timeout",
            error_message="target timed out",
            error_prefix="Target failed",
        )
    finally:
        session.close()


def test_run_session_stop_controls_on_the_session_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`RunSession.stop` keeps the submitted context instead of rebuilding [PY-2]."""

    context = build_context(prepare_project_root(tmp_path / "project"))
    session = _LiveRunSession(RunExecutionResult(tid="1" * 19), context)
    seen: list[WeftContext] = []

    def refuse_rebuild(*_args: Any, **_kwargs: Any) -> WeftContext:
        raise AssertionError("stop must not resolve a fresh context")

    def record_stop(tid: str, *, context: WeftContext | None = None) -> None:
        assert context is not None
        seen.append(context)

    monkeypatch.setattr(task_commands, "build_context", refuse_rebuild)
    monkeypatch.setattr(task_commands, "stop_task", record_stop)

    result = session.stop()

    assert [id(entry) for entry in seen] == [id(context)]
    assert result.command == "stop"
    assert result.accepted == ("1" * 19,)
