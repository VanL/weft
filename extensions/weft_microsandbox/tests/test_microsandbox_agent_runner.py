"""Microsandbox provider_cli agent runner tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from weft.core.agents.runtime import AgentExecutionResult
from weft_microsandbox._runtime import MicrosandboxRunResult, MicrosandboxRunSpec
from weft_microsandbox.plugin import MicrosandboxRunner

pytestmark = [pytest.mark.shared]


class StdoutAgentRuntime:
    last_spec: MicrosandboxRunSpec | None = None

    def run(
        self,
        spec: MicrosandboxRunSpec,
        *,
        on_started: Callable[[object], None] | None = None,
    ) -> MicrosandboxRunResult:
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
    ) -> MicrosandboxRunResult:
        del spec, on_started
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
        "conversation_scope": "per_message",
        "instructions": "answer tersely",
        "runtime_config": {"provider": provider},
    }


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


def test_agent_runner_copies_provider_output_file_before_parse() -> None:
    class FileOutputRuntime:
        def run(
            self,
            spec: MicrosandboxRunSpec,
            *,
            on_started: Callable[[object], None] | None = None,
        ) -> MicrosandboxRunResult:
            del on_started
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
