"""Microsandbox provider_cli agent runner tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from weft.core.agents.runtime import AgentExecutionResult
from weft_microsandbox import plugin as plugin_module
from weft_microsandbox._runtime import (
    MicrosandboxRunResult,
    MicrosandboxRunSpec,
)
from weft_microsandbox.plugin import MicrosandboxRunner

pytestmark = [pytest.mark.shared]


class StdoutAgentRuntime:
    last_spec: MicrosandboxRunSpec | None = None

    def run(
        self,
        spec: MicrosandboxRunSpec,
        *,
        on_started: Callable[[object], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> MicrosandboxRunResult:
        del cancel_requested
        StdoutAgentRuntime.last_spec = spec
        if on_started is not None:
            from weft_microsandbox._runtime import MicrosandboxStarted

            on_started(MicrosandboxStarted("agent-sandbox", "agent-sandbox"))
        return MicrosandboxRunResult(
            sandbox_id="agent-sandbox",
            sandbox_name="agent-sandbox",
            exit_code=0,
            stdout="agent answer\n",
            stderr="",
            timed_out=False,
            duration=0.01,
        )


class EmptyRuntime:
    def run(
        self,
        spec: MicrosandboxRunSpec,
        *,
        on_started: Callable[[object], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> MicrosandboxRunResult:
        del spec, on_started, cancel_requested
        return MicrosandboxRunResult(
            sandbox_id="agent-sandbox",
            sandbox_name="agent-sandbox",
            exit_code=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration=0.01,
        )


def _agent(provider: str) -> dict[str, object]:
    return {
        "runtime": "provider_cli",
        "authority_class": "general",
        "conversation_scope": "per_message",
        "instructions": "answer tersely",
        "runtime_config": {"provider": provider},
    }


def _capture_provider_tempdir(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    captured: list[Path] = []
    original_prepare = plugin_module.prepare_provider_cli_execution

    def capture_tempdir(**kwargs: object) -> object:
        tempdir = kwargs["tempdir"]
        assert isinstance(tempdir, Path)
        captured.append(tempdir)
        return original_prepare(**kwargs)

    monkeypatch.setattr(
        plugin_module,
        "prepare_provider_cli_execution",
        capture_tempdir,
    )
    return captured


def test_agent_runner_uses_provider_cli_parser_from_guest_stdout() -> None:
    runner = MicrosandboxRunner(
        target_type="agent",
        tid="1234567890123456789",
        process_target=None,
        agent=_agent("gemini"),
        args=[],
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        runner_options={
            "image": "agent:latest",
            "executable": "gemini",
            "cwd": "/workspace",
        },
        runtime=StdoutAgentRuntime(),
    )

    outcome = runner.run("question")

    assert outcome.status == "ok"
    assert isinstance(outcome.value, AgentExecutionResult)
    assert outcome.value.outputs == ("agent answer",)
    assert outcome.value.metadata["provider"] == "gemini"
    assert StdoutAgentRuntime.last_spec is not None
    assert StdoutAgentRuntime.last_spec.command[0] == "gemini"
    assert StdoutAgentRuntime.last_spec.cwd == "/workspace"


def test_agent_runner_converts_result_builder_failure_to_error_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_tempdirs = _capture_provider_tempdir(monkeypatch)

    def fail_result_builder(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("provider result contains sensitive detail")

    monkeypatch.setattr(
        plugin_module,
        "build_provider_cli_execution_result",
        fail_result_builder,
    )
    runner = MicrosandboxRunner(
        target_type="agent",
        tid="1234567890123456789",
        process_target=None,
        agent=_agent("gemini"),
        args=[],
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        runner_options={
            "image": "agent:latest",
            "executable": "gemini",
            "cwd": "/workspace",
        },
        runtime=StdoutAgentRuntime(),
    )

    outcome = runner.run("question")

    assert outcome.status == "error"
    assert outcome.value is None
    assert outcome.error == "provider result contains sensitive detail"
    assert outcome.stdout == "agent answer\n"
    assert outcome.stderr is None
    assert outcome.returncode == 0
    assert outcome.runtime_handle is not None
    assert outcome.runtime_handle.id == "agent-sandbox"
    assert len(provider_tempdirs) == 1
    assert not provider_tempdirs[0].exists()


def test_agent_runner_converts_provider_parser_failure_to_error_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_tempdirs: list[Path] = []
    original_prepare = plugin_module.prepare_provider_cli_execution

    def prepare_with_failing_parser(**kwargs: object) -> object:
        tempdir = kwargs["tempdir"]
        assert isinstance(tempdir, Path)
        provider_tempdirs.append(tempdir)
        prepared = original_prepare(**kwargs)

        def fail_parser(**_parser_kwargs: object) -> object:
            raise ValueError("provider parser contains sensitive detail")

        return replace(
            prepared,
            provider=SimpleNamespace(parse_result=fail_parser),
        )

    monkeypatch.setattr(
        plugin_module,
        "prepare_provider_cli_execution",
        prepare_with_failing_parser,
    )
    runner = MicrosandboxRunner(
        target_type="agent",
        tid="1234567890123456789",
        process_target=None,
        agent=_agent("gemini"),
        args=[],
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        runner_options={
            "image": "agent:latest",
            "executable": "gemini",
            "cwd": "/workspace",
        },
        runtime=StdoutAgentRuntime(),
    )

    outcome = runner.run("question")

    assert outcome.status == "error"
    assert outcome.value is None
    assert outcome.error == "provider parser contains sensitive detail"
    assert outcome.stdout == "agent answer\n"
    assert outcome.stderr is None
    assert outcome.returncode == 0
    assert outcome.runtime_handle is not None
    assert outcome.runtime_handle.id == "agent-sandbox"
    assert len(provider_tempdirs) == 1
    assert not provider_tempdirs[0].exists()


def test_agent_runner_does_not_contain_fatal_result_builder_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FatalResultBuilderSignal(BaseException):
        pass

    signal = FatalResultBuilderSignal("stop now")
    provider_tempdirs = _capture_provider_tempdir(monkeypatch)

    def fail_result_builder(*_args: object, **_kwargs: object) -> object:
        raise signal

    monkeypatch.setattr(
        plugin_module,
        "build_provider_cli_execution_result",
        fail_result_builder,
    )
    runner = MicrosandboxRunner(
        target_type="agent",
        tid="1234567890123456789",
        process_target=None,
        agent=_agent("gemini"),
        args=[],
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        runner_options={
            "image": "agent:latest",
            "executable": "gemini",
            "cwd": "/workspace",
        },
        runtime=StdoutAgentRuntime(),
    )

    with pytest.raises(FatalResultBuilderSignal) as exc_info:
        runner.run("question")

    assert exc_info.value is signal
    assert len(provider_tempdirs) == 1
    assert not provider_tempdirs[0].exists()


def test_agent_runner_copies_provider_output_file_before_parse() -> None:
    class FileOutputRuntime:
        def run(
            self,
            spec: MicrosandboxRunSpec,
            *,
            on_started: Callable[[object], None] | None = None,
            cancel_requested: Callable[[], bool] | None = None,
        ) -> MicrosandboxRunResult:
            del on_started, cancel_requested
            assert spec.copy_back
            Path(spec.copy_back[0].host_path).write_text(
                "copied codex answer\n",
                encoding="utf-8",
            )
            return MicrosandboxRunResult(
                sandbox_id="agent-sandbox",
                sandbox_name="agent-sandbox",
                exit_code=0,
                stdout="",
                stderr="",
                timed_out=False,
                duration=0.01,
            )

    runner = MicrosandboxRunner(
        target_type="agent",
        tid="1234567890123456789",
        process_target=None,
        agent=_agent("codex"),
        args=[],
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        runner_options={
            "image": "agent:latest",
            "executable": "codex",
            "cwd": "/workspace",
        },
        runtime=FileOutputRuntime(),
    )

    outcome = runner.run("question")

    assert outcome.status == "ok"
    assert isinstance(outcome.value, AgentExecutionResult)
    assert outcome.value.outputs == ("copied codex answer",)


def test_bounded_claude_agent_copies_generated_mcp_config_into_guest() -> None:
    class BoundedRuntime:
        def run(
            self,
            spec: MicrosandboxRunSpec,
            *,
            on_started: Callable[[object], None] | None = None,
            cancel_requested: Callable[[], bool] | None = None,
        ) -> MicrosandboxRunResult:
            del on_started, cancel_requested
            assert "--mcp-config" in spec.command
            config_path = Path(spec.command[spec.command.index("--mcp-config") + 1])
            assert config_path.name == "claude-mcp.json"
            assert config_path.exists()
            assert spec.copy_into_guest
            copied_root = Path(spec.copy_into_guest[0].host_path)
            assert config_path.is_relative_to(copied_root)
            assert (
                spec.copy_into_guest[0].guest_path == spec.copy_into_guest[0].host_path
            )
            return MicrosandboxRunResult(
                sandbox_id="agent-sandbox",
                sandbox_name="agent-sandbox",
                exit_code=0,
                stdout="bounded answer\n",
                stderr="",
                timed_out=False,
                duration=0.01,
            )

    runner = MicrosandboxRunner(
        target_type="agent",
        tid="1234567890123456789",
        process_target=None,
        agent={**_agent("claude_code"), "authority_class": "bounded"},
        args=[],
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        runner_options={
            "image": "agent:latest",
            "executable": "claude",
            "cwd": "/workspace",
        },
        runtime=BoundedRuntime(),
    )

    outcome = runner.run("question")

    assert outcome.status == "ok"
    assert isinstance(outcome.value, AgentExecutionResult)
    assert outcome.value.outputs == ("bounded answer",)


def test_agent_runner_passes_explicit_and_provider_env_to_guest() -> None:
    class EnvRuntime(StdoutAgentRuntime):
        pass

    runner = MicrosandboxRunner(
        target_type="agent",
        tid="1234567890123456789",
        process_target=None,
        agent={**_agent("gemini"), "authority_class": "bounded"},
        args=[],
        env={"CALLER_ONLY": "1", "GEMINI_API_KEY": "from-spec"},
        working_dir=None,
        timeout=5.0,
        limits=None,
        runner_options={
            "image": "agent:latest",
            "executable": "gemini",
            "cwd": "/workspace",
        },
        runtime=EnvRuntime(),
    )

    outcome = runner.run("question")

    assert outcome.status == "ok"
    assert EnvRuntime.last_spec is not None
    assert EnvRuntime.last_spec.env["CALLER_ONLY"] == "1"
    assert EnvRuntime.last_spec.env["GEMINI_API_KEY"] == "from-spec"
    assert "HOME" in EnvRuntime.last_spec.env
    assert "USERPROFILE" in EnvRuntime.last_spec.env


def test_agent_runner_malformed_provider_output_fails() -> None:
    runner = MicrosandboxRunner(
        target_type="agent",
        tid=None,
        process_target=None,
        agent=_agent("gemini"),
        args=[],
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        runner_options={
            "image": "agent:latest",
            "executable": "gemini",
            "cwd": "/workspace",
        },
        runtime=EmptyRuntime(),
    )

    outcome = runner.run("question")

    assert outcome.status == "error"
    assert "produced empty stdout" in (outcome.error or "")
