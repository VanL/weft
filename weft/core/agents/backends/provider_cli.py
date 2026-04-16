"""Delegated provider CLI agent runtime adapter.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-5]
- docs/specifications/13A-Agent_Runtime_Planned.md [AR-A1], [AR-A3]
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from weft.core.taskspec import AgentSection

from ..provider_cli.execution import (
    ProviderCLIPreparedExecution,
    build_provider_cli_execution_result,
    compose_provider_cli_prompt,
    prepare_provider_cli_execution,
    resolve_provider_cli,
    runtime_config_str,
    validate_authority_tool_profile,
)
from ..provider_cli.registry import (
    ProviderCLIProvider,
    resolve_provider_cli_executable,
)
from ..provider_cli.settings import record_provider_cli_health
from ..provider_cli.windows_shims import resolve_windows_cmd_shim_command
from ..resolution import (
    load_agent_resolver,
    load_agent_tool_profile,
    resolve_agent_prompt,
    resolve_agent_tool_profile,
)
from ..runtime import AgentExecutionResult, NormalizedAgentWorkItem
from ..tools import ResolvedAgentTool


class ProviderCLIBackend:
    """Delegated runtime backed by an external provider CLI."""

    def validate(
        self,
        *,
        agent: AgentSection,
        preflight: bool = False,
        bundle_root: str | None = None,
    ) -> None:
        provider = resolve_provider_cli(agent)
        provider.validate_authority(agent.resolved_authority_class)
        provider.validate_model(agent.model)
        provider.validate_options(dict(agent.options))
        load_agent_resolver(agent, bundle_root=bundle_root)
        if preflight:
            self._resolve_executable(agent, spec_context=str(Path.cwd()))

    def validate_tool_profile(
        self,
        *,
        agent: AgentSection,
        tid: str | None = None,
        preflight: bool = False,
        bundle_root: str | None = None,
    ) -> None:
        provider = resolve_provider_cli(agent)
        tool_profile = resolve_agent_tool_profile(
            agent,
            tid=tid,
            bundle_root=bundle_root,
        )
        validate_authority_tool_profile(agent, tool_profile)
        provider.validate_tool_profile(tool_profile, preflight=preflight)
        provider.resolve_options(
            authority_class=agent.resolved_authority_class,
            raw_options=dict(agent.options),
            tool_profile=tool_profile,
        )
        load_agent_tool_profile(agent, bundle_root=bundle_root)

    def execute(
        self,
        *,
        agent: AgentSection,
        work_item: NormalizedAgentWorkItem,
        tools: Sequence[ResolvedAgentTool],
        tid: str | None,
        bundle_root: str | None = None,
    ) -> AgentExecutionResult:
        del tools
        self.validate(agent=agent, preflight=False, bundle_root=bundle_root)
        self.validate_tool_profile(
            agent=agent,
            tid=tid,
            preflight=True,
            bundle_root=bundle_root,
        )

        provider = resolve_provider_cli(agent)
        cwd = str(Path.cwd())
        executable = self._resolve_executable(agent, spec_context=cwd)

        with tempfile.TemporaryDirectory(prefix="weft-provider-cli-") as tempdir:
            prepared = prepare_provider_cli_execution(
                agent=agent,
                work_item=work_item,
                tid=tid,
                executable=executable,
                cwd=cwd,
                tempdir=Path(tempdir),
                bundle_root=bundle_root,
            )
            provider_result = self._run_provider_invocation(
                provider=provider,
                invocation=prepared.invocation,
                executable=executable,
                command_class="execute",
            )
        return build_provider_cli_execution_result(
            agent=agent,
            prepared=prepared,
            work_item=work_item,
            provider_result=provider_result,
            session_metadata=None,
        )

    def start_session(
        self,
        *,
        agent: AgentSection,
        tid: str | None,
        bundle_root: str | None = None,
    ) -> ProviderCLIBackendSession:
        self.validate(agent=agent, preflight=False, bundle_root=bundle_root)
        self.validate_tool_profile(
            agent=agent,
            tid=tid,
            preflight=True,
            bundle_root=bundle_root,
        )
        provider = resolve_provider_cli(agent)
        cwd = str(Path.cwd())
        executable = self._resolve_executable(agent, spec_context=cwd)
        return ProviderCLIBackendSession(
            backend=self,
            agent=agent,
            provider=provider,
            executable=executable,
            tid=tid,
            bundle_root=bundle_root,
        )

    @staticmethod
    def _merge_provider_options(
        agent: AgentSection,
        tool_profile: Any,
        provider: ProviderCLIProvider,
    ) -> dict[str, Any]:
        return provider.resolve_options(
            authority_class=agent.resolved_authority_class,
            raw_options=dict(agent.options),
            tool_profile=tool_profile,
        )

    def _resolve_executable(
        self,
        agent: AgentSection,
        *,
        spec_context: str | None,
    ) -> str:
        provider = resolve_provider_cli(agent)
        return resolve_provider_cli_executable(
            provider,
            configured_executable=runtime_config_str(agent, "executable"),
            spec_context=spec_context,
        )

    def _prepare_execution(
        self,
        *,
        agent: AgentSection,
        work_item: NormalizedAgentWorkItem,
        tid: str | None,
        bundle_root: str | None = None,
    ) -> ProviderCLIExecution:
        resolver_result = resolve_agent_prompt(
            agent,
            work_item,
            tid=tid,
            bundle_root=bundle_root,
        )
        tool_profile = resolve_agent_tool_profile(
            agent,
            tid=tid,
            bundle_root=bundle_root,
        )
        provider = resolve_provider_cli(agent)
        validate_authority_tool_profile(agent, tool_profile)
        provider_options = self._merge_provider_options(agent, tool_profile, provider)
        provider.validate_tool_profile(tool_profile, preflight=False)
        provider.validate_model(agent.model)
        prompt = compose_provider_cli_prompt(
            work_item.instructions,
            resolver_result.instructions,
            tool_profile.instructions,
            resolver_result.prompt,
        )
        return ProviderCLIExecution(
            authority_class=agent.resolved_authority_class,
            prompt=prompt,
            provider_options=provider_options,
            resolver_result=resolver_result,
            tool_profile=tool_profile,
        )

    def _run_provider_invocation(
        self,
        *,
        provider: ProviderCLIProvider,
        invocation: Any,
        executable: str,
        command_class: Literal["execute", "session"],
    ) -> Any:
        env = os.environ.copy()
        if invocation.env:
            env.update(invocation.env)
        spec_context = invocation.cwd or str(Path.cwd())
        try:
            command = tuple(invocation.command)
            if os.name == "nt":
                command = resolve_windows_cmd_shim_command(command)
            completed = subprocess.run(
                list(command),
                input=invocation.stdin_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                cwd=invocation.cwd,
                env=env,
            )
        except OSError as exc:
            self._record_provider_health(
                provider=provider,
                executable=executable,
                command_class=command_class,
                spec_context=spec_context,
                status="failure",
                detail=str(exc),
            )
            raise RuntimeError(
                "Unable to execute provider_cli command "
                f"'{invocation.command[0]}': {exc}"
            ) from exc
        try:
            provider_result = provider.parse_result(
                completed=completed,
                invocation=invocation,
            )
        except Exception as exc:
            self._record_provider_health(
                provider=provider,
                executable=executable,
                command_class=command_class,
                spec_context=spec_context,
                status="failure",
                detail=str(exc),
            )
            raise
        self._record_provider_health(
            provider=provider,
            executable=executable,
            command_class=command_class,
            spec_context=spec_context,
            status="success",
            detail=None,
        )
        return provider_result

    @staticmethod
    def _record_provider_health(
        *,
        provider: ProviderCLIProvider,
        executable: str,
        command_class: Literal["execute", "session"],
        spec_context: str | None,
        status: str,
        detail: str | None,
    ) -> None:
        record_provider_cli_health(
            provider.name,
            executable=executable,
            command_class=command_class,
            status=status,
            detail=detail,
            spec_context=spec_context,
        )


@dataclass(frozen=True, slots=True)
class ProviderCLIExecution:
    """Resolved one-turn execution inputs for a provider_cli request."""

    authority_class: str
    prompt: str
    provider_options: dict[str, Any]
    resolver_result: Any
    tool_profile: Any


class ProviderCLIBackendSession:
    """Persistent delegated session backed by provider-native continuation."""

    def __init__(
        self,
        *,
        backend: ProviderCLIBackend,
        agent: AgentSection,
        provider: ProviderCLIProvider,
        executable: str,
        tid: str | None,
        bundle_root: str | None,
    ) -> None:
        self._backend = backend
        self._agent = agent
        self._provider = provider
        self._executable = executable
        self._tid = tid
        self._bundle_root = bundle_root
        self._tempdir = tempfile.TemporaryDirectory(prefix="weft-provider-cli-session-")
        self._session = provider.create_session_context(
            executable=executable,
            cwd=str(Path.cwd()),
            model=agent.model,
            tempdir=Path(self._tempdir.name),
        )

    def execute(self, work_item: NormalizedAgentWorkItem) -> AgentExecutionResult:
        execution = self._backend._prepare_execution(
            agent=self._agent,
            work_item=work_item,
            tid=self._tid,
            bundle_root=self._bundle_root,
        )
        invocation = self._provider.build_session_invocation(
            executable=self._executable,
            authority_class=execution.authority_class,
            prompt=execution.prompt,
            cwd=self._session.work_cwd,
            model=self._agent.model,
            options=execution.provider_options,
            session=self._session,
            tool_profile=execution.tool_profile,
        )
        provider_result = self._backend._run_provider_invocation(
            provider=self._provider,
            invocation=invocation,
            executable=self._executable,
            command_class="session",
        )
        self._session.turn_index += 1
        return build_provider_cli_execution_result(
            agent=self._agent,
            prepared=ProviderCLIPreparedExecution(
                provider=self._provider,
                executable=self._executable,
                authority_class=execution.authority_class,
                invocation=invocation,
                provider_options=execution.provider_options,
                resolver_result=execution.resolver_result,
                tool_profile=execution.tool_profile,
            ),
            work_item=work_item,
            provider_result=provider_result,
            session_metadata={
                "provider_session_id": self._session.session_id,
                "turn_index": self._session.turn_index,
            },
        )

    def close(self) -> None:
        self._tempdir.cleanup()
