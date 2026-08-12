"""Public `weft.commands.cmd_run` contract tests [PY-2]."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from weft import commands
from weft.commands.types import RunExecutionResult, RunSpecDescription, TaskResult
from weft.context import build_context

pytestmark = pytest.mark.shared


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
        "run_input_stdin_text",
        "work_input_text",
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
        work_input_text="payload",
        context=Path("project"),
    )

    assert isinstance(result, RunExecutionResult)
    assert result.tid == "123"
    assert observed["work_input_text"] == "payload"
    assert observed["context_dir"] == Path("project")
    assert observed["json_output"] is False
    assert observed["verbose"] is False


def test_cmd_run_wait_returns_a_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Wait mode returns before result collection and exposes a live session."""

    execution = RunExecutionResult(tid="123")
    context = build_context(str(tmp_path))

    def fake_execute(*_args: Any, **kwargs: Any) -> RunExecutionResult:
        assert kwargs["wait"] is False
        kwargs["on_submitted"]("123", context)
        return execution

    monkeypatch.setattr("weft.commands.run.execute_run", fake_execute)
    monkeypatch.setattr(
        "weft.commands.run.await_task_result",
        lambda *_args, **_kwargs: TaskResult(
            tid="123",
            status="completed",
            value="ok",
            stdout=None,
            stderr=None,
            error=None,
        ),
    )

    session = commands.cmd_run(("worker",), wait=True)

    assert session.tid == "123"
    expected = RunExecutionResult(tid="123", status="completed", result_value="ok")
    assert session.wait() == expected
    assert session.wait() is session.wait()
    session.close()
    session.close()
