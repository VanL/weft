"""Internal provider registry for delegated CLI agent runtimes.

Spec references:
- docs/specifications/13A-Agent_Runtime_Planned.md [AR-A1], [AR-A3]
- docs/specifications/13-Agent_Runtime.md [AR-5]
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from weft.ext import AgentMCPServerDescriptor, AgentToolProfileResult

from .runtime_prep import build_gemini_session_environment
from .settings import load_provider_cli_project_settings


@dataclass(frozen=True, slots=True)
class ProviderCLIInvocation:
    """Prepared provider CLI invocation."""

    command: tuple[str, ...]
    stdin_text: str | None = None
    output_path: Path | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCLIResult:
    """Parsed one-shot provider CLI result."""

    output_text: str
    usage: dict[str, Any] | None = None


@dataclass(slots=True)
class ProviderCLISessionContext:
    """Provider-owned continuation state for a live delegated session."""

    tempdir: Path
    work_cwd: str
    process_cwd: str
    env: dict[str, str]
    session_id: str | None = None
    turn_index: int = 0


class ProviderCLIProvider(Protocol):
    """Internal contract for provider-specific CLI adapters."""

    name: str
    default_executable: str
    supports_bounded_authority: bool
    supported_workspace_access_modes: tuple[str, ...]
    supports_explicit_mcp_servers: bool

    def validate_authority(self, authority_class: str) -> None: ...

    def validate_options(self, options: Mapping[str, Any]) -> None: ...

    def resolve_options(
        self,
        *,
        authority_class: str,
        raw_options: Mapping[str, Any],
        tool_profile: AgentToolProfileResult,
    ) -> dict[str, Any]: ...

    def validate_model(self, model: str | None) -> None: ...

    def validate_tool_profile(
        self,
        tool_profile: AgentToolProfileResult,
        *,
        preflight: bool = False,
    ) -> None: ...

    def ensure_runtime_requirements(
        self,
        executable: str,
        *,
        command_class: Literal["execute", "session"],
    ) -> None: ...

    def build_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        tempdir: Path,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation: ...

    def create_session_context(
        self,
        *,
        executable: str,
        cwd: str,
        model: str | None,
        tempdir: Path,
    ) -> ProviderCLISessionContext: ...

    def build_session_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        session: ProviderCLISessionContext,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation: ...

    def parse_result(
        self,
        *,
        completed: subprocess.CompletedProcess[str],
        invocation: ProviderCLIInvocation,
    ) -> ProviderCLIResult: ...


class _BaseTextProvider:
    """Shared provider behavior for stdout-returning text CLIs."""

    name: str
    default_executable: str
    supports_model = False
    supports_bounded_authority = False
    supported_workspace_access_modes: tuple[str, ...] = ()
    supports_explicit_mcp_servers = False

    def validate_authority(self, authority_class: str) -> None:
        if authority_class not in {"bounded", "general"}:
            raise ValueError(f"Unsupported authority_class={authority_class!r}")
        if authority_class == "bounded" and not self.supports_bounded_authority:
            raise ValueError(f"{self.name} does not support authority_class='bounded'")

    def validate_options(self, options: Mapping[str, Any]) -> None:
        if options:
            keys = ", ".join(sorted(options))
            raise ValueError(f"{self.name} does not support provider options: {keys}")

    def resolve_options(
        self,
        *,
        authority_class: str,
        raw_options: Mapping[str, Any],
        tool_profile: AgentToolProfileResult,
    ) -> dict[str, Any]:
        self.validate_authority(authority_class)
        self.validate_options(raw_options)
        profile_options = dict(tool_profile.provider_options)
        self.validate_options(profile_options)
        merged = dict(raw_options)
        merged.update(profile_options)
        self.validate_options(merged)
        return merged

    def validate_model(self, model: str | None) -> None:
        if model is None:
            return
        if not self.supports_model:
            raise ValueError(f"{self.name} does not support spec.agent.model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("spec.agent.model must be a non-empty string")

    def ensure_runtime_requirements(
        self,
        executable: str,
        *,
        command_class: Literal["execute", "session"],
    ) -> None:
        del executable, command_class

    def validate_tool_profile(
        self,
        tool_profile: AgentToolProfileResult,
        *,
        preflight: bool = False,
    ) -> None:
        workspace_access = tool_profile.workspace_access
        if workspace_access is not None and (
            workspace_access not in self.supported_workspace_access_modes
        ):
            raise ValueError(
                f"{self.name} does not support workspace_access={workspace_access!r}"
            )
        if tool_profile.mcp_servers and not self.supports_explicit_mcp_servers:
            raise ValueError(
                f"{self.name} does not support explicit MCP server descriptors"
            )
        if preflight:
            for descriptor in tool_profile.mcp_servers:
                _resolve_stdio_mcp_command(descriptor)

    def parse_result(
        self,
        *,
        completed: subprocess.CompletedProcess[str],
        invocation: ProviderCLIInvocation,
    ) -> ProviderCLIResult:
        del invocation
        if completed.returncode != 0:
            detail = _compact_process_detail(completed)
            raise RuntimeError(f"{self.name} execution failed: {detail}")
        output_text = (completed.stdout or "").strip()
        if not output_text:
            detail = _compact_process_detail(completed)
            raise RuntimeError(
                f"{self.name} produced empty stdout" + (f": {detail}" if detail else "")
            )
        return ProviderCLIResult(output_text=output_text)

    def create_session_context(
        self,
        *,
        executable: str,
        cwd: str,
        model: str | None,
        tempdir: Path,
    ) -> ProviderCLISessionContext:
        del executable, model
        return ProviderCLISessionContext(
            tempdir=tempdir,
            work_cwd=cwd,
            process_cwd=cwd,
            env={},
        )

    def build_session_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        session: ProviderCLISessionContext,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        del (
            authority_class,
            executable,
            prompt,
            cwd,
            model,
            options,
            session,
            tool_profile,
        )
        raise RuntimeError(
            f"{self.name} does not implement provider_cli persistent sessions"
        )


class ClaudeCodeProvider(_BaseTextProvider):
    """Claude Code CLI provider adapter."""

    name = "claude_code"
    default_executable = "claude"
    supports_model = True
    supports_bounded_authority = True
    supported_workspace_access_modes = ("read-only",)
    supports_explicit_mcp_servers = True

    def validate_tool_profile(
        self,
        tool_profile: AgentToolProfileResult,
        *,
        preflight: bool = False,
    ) -> None:
        super().validate_tool_profile(tool_profile, preflight=preflight)
        if any(descriptor.cwd is not None for descriptor in tool_profile.mcp_servers):
            raise ValueError("claude_code does not support MCP server cwd overrides")

    def validate_options(self, options: Mapping[str, Any]) -> None:
        strict_value = options.get("strict_mcp_config")
        extra_keys = sorted(set(options) - {"strict_mcp_config"})
        if extra_keys:
            keys = ", ".join(extra_keys)
            raise ValueError(f"{self.name} does not support provider options: {keys}")
        if strict_value is not None and not isinstance(strict_value, bool):
            raise TypeError(
                f"{self.name} provider option 'strict_mcp_config' must be a boolean"
            )

    def resolve_options(
        self,
        *,
        authority_class: str,
        raw_options: Mapping[str, Any],
        tool_profile: AgentToolProfileResult,
    ) -> dict[str, Any]:
        merged = super().resolve_options(
            authority_class=authority_class,
            raw_options=raw_options,
            tool_profile=tool_profile,
        )
        if authority_class == "bounded":
            for source_name, options in (
                ("spec.agent.options", raw_options),
                ("tool profile", tool_profile.provider_options),
            ):
                if options.get("strict_mcp_config") is False:
                    raise ValueError(
                        f"{self.name} authority_class='bounded' does not allow "
                        f"{source_name} strict_mcp_config=false"
                    )
            merged["strict_mcp_config"] = True
        return merged

    def build_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        tempdir: Path,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        del cwd
        self.validate_model(model)
        self.validate_options(options)
        command = [executable]
        if model:
            command.extend(["--model", model])
        if authority_class == "bounded" or tool_profile.workspace_access == "read-only":
            command.extend(["--permission-mode", "plan"])
        elif tool_profile.workspace_access != "none":
            command.append("--dangerously-skip-permissions")
        if authority_class == "bounded" or tool_profile.mcp_servers:
            config_path = _write_claude_mcp_config(tempdir, tool_profile.mcp_servers)
            command.extend(["--mcp-config", str(config_path), "--strict-mcp-config"])
        elif options.get("strict_mcp_config"):
            command.append("--strict-mcp-config")
        command.extend(["-p", prompt])
        return ProviderCLIInvocation(command=tuple(command))

    def create_session_context(
        self,
        *,
        executable: str,
        cwd: str,
        model: str | None,
        tempdir: Path,
    ) -> ProviderCLISessionContext:
        del executable, model
        return ProviderCLISessionContext(
            tempdir=tempdir,
            work_cwd=cwd,
            process_cwd=cwd,
            env={},
            session_id=str(uuid4()),
        )

    def build_session_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        session: ProviderCLISessionContext,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        del cwd
        self.validate_model(model)
        self.validate_options(options)
        if session.session_id is None:
            raise RuntimeError("claude_code session is missing a session_id")
        command = [executable]
        if model:
            command.extend(["--model", model])
        if authority_class == "bounded" or tool_profile.workspace_access == "read-only":
            command.extend(["--permission-mode", "plan"])
        elif tool_profile.workspace_access != "none":
            command.append("--dangerously-skip-permissions")
        if authority_class == "bounded" or tool_profile.mcp_servers:
            config_path = _write_claude_mcp_config(
                session.tempdir,
                tool_profile.mcp_servers,
            )
            command.extend(["--mcp-config", str(config_path), "--strict-mcp-config"])
        elif options.get("strict_mcp_config"):
            command.append("--strict-mcp-config")
        if session.turn_index == 0:
            command.extend(["--session-id", session.session_id])
        else:
            command.extend(["--resume", session.session_id])
        command.extend(["-p", prompt])
        return ProviderCLIInvocation(
            command=tuple(command),
            env=session.env,
            cwd=session.process_cwd,
        )


class CodexProvider(_BaseTextProvider):
    """Codex CLI provider adapter for one-shot delegated execution."""

    name = "codex"
    default_executable = "codex"
    supports_model = True
    supports_bounded_authority = True
    supported_workspace_access_modes = ("read-only", "workspace-write")

    _STRING_OPTIONS = frozenset({"color", "local_provider", "profile", "sandbox"})
    _BOOL_OPTIONS = frozenset(
        {
            "dangerously_bypass_approvals_and_sandbox",
            "full_auto",
            "oss",
            "skip_git_repo_check",
        }
    )
    _LIST_OPTIONS = frozenset({"add_dirs", "config_overrides"})
    _ALLOWED_OPTIONS = _STRING_OPTIONS | _BOOL_OPTIONS | _LIST_OPTIONS

    def validate_options(self, options: Mapping[str, Any]) -> None:
        extra_keys = sorted(set(options) - self._ALLOWED_OPTIONS)
        if extra_keys:
            raise ValueError(
                f"Unsupported codex provider option(s): {', '.join(extra_keys)}"
            )

        for key in self._STRING_OPTIONS:
            value = options.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise TypeError(
                    f"codex provider option '{key}' must be a non-empty string"
                )

        for key in self._BOOL_OPTIONS:
            value = options.get(key)
            if value is None:
                continue
            if not isinstance(value, bool):
                raise TypeError(f"codex provider option '{key}' must be a boolean")

        for key in self._LIST_OPTIONS:
            value = options.get(key)
            if value is None:
                continue
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise TypeError(
                    f"codex provider option '{key}' must be a list of strings"
                )
            if not all(isinstance(item, str) and item for item in value):
                raise TypeError(
                    f"codex provider option '{key}' must be a list of strings"
                )

        sandbox = options.get("sandbox")
        if sandbox is not None and sandbox not in {
            "read-only",
            "workspace-write",
            "danger-full-access",
        }:
            raise ValueError(f"Unsupported codex sandbox value: {sandbox}")

        color = options.get("color")
        if color is not None and color not in {"always", "never", "auto"}:
            raise ValueError(f"Unsupported codex color value: {color}")

        local_provider = options.get("local_provider")
        if local_provider is not None and local_provider not in {"lmstudio", "ollama"}:
            raise ValueError(
                f"Unsupported codex local provider value: {local_provider}"
            )

    def resolve_options(
        self,
        *,
        authority_class: str,
        raw_options: Mapping[str, Any],
        tool_profile: AgentToolProfileResult,
    ) -> dict[str, Any]:
        merged = super().resolve_options(
            authority_class=authority_class,
            raw_options=raw_options,
            tool_profile=tool_profile,
        )
        raw_sandbox = raw_options.get("sandbox")
        profile_sandbox = tool_profile.provider_options.get("sandbox")
        workspace_access = tool_profile.workspace_access
        if (
            profile_sandbox is not None
            and raw_sandbox is not None
            and raw_sandbox != profile_sandbox
        ):
            raise ValueError(
                "spec.agent.options.sandbox conflicts with the resolved "
                "tool-profile sandbox"
            )
        if (
            workspace_access is not None
            and raw_sandbox is not None
            and raw_sandbox != workspace_access
        ):
            raise ValueError(
                "spec.agent.options.sandbox conflicts with the resolved "
                "tool-profile workspace_access"
            )
        if authority_class == "bounded":
            for source_name, sandbox in (
                ("spec.agent.options", raw_sandbox),
                ("tool profile", profile_sandbox),
            ):
                if sandbox not in {None, "read-only"}:
                    raise ValueError(
                        f"{self.name} authority_class='bounded' only allows "
                        f"{source_name} sandbox='read-only'"
                    )
            if raw_options.get("dangerously_bypass_approvals_and_sandbox"):
                raise ValueError(
                    "codex authority_class='bounded' does not allow "
                    "dangerously_bypass_approvals_and_sandbox"
                )
            if tool_profile.provider_options.get(
                "dangerously_bypass_approvals_and_sandbox"
            ):
                raise ValueError(
                    "codex authority_class='bounded' does not allow tool-profile "
                    "dangerously_bypass_approvals_and_sandbox"
                )
            if raw_options.get("full_auto"):
                raise ValueError(
                    "codex authority_class='bounded' does not allow full_auto"
                )
            if tool_profile.provider_options.get("full_auto"):
                raise ValueError(
                    "codex authority_class='bounded' does not allow tool-profile "
                    "full_auto"
                )
            merged["sandbox"] = "read-only"
        elif (
            workspace_access is not None or profile_sandbox is not None
        ) and raw_options.get("dangerously_bypass_approvals_and_sandbox"):
            raise ValueError(
                "spec.agent.options.dangerously_bypass_approvals_and_sandbox "
                "conflicts with the resolved tool profile"
            )
        return merged

    def build_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        tempdir: Path,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        self.validate_model(model)
        self.validate_options(options)
        output_path = tempdir / "last-message.txt"
        command = [executable, "exec"]
        if model:
            command.extend(["--model", model])
        profile = options.get("profile")
        if profile is not None:
            command.extend(["--profile", str(profile)])
        if options.get("oss"):
            command.append("--oss")
        local_provider = options.get("local_provider")
        if local_provider is not None:
            command.extend(["--local-provider", str(local_provider)])
        for config_value in options.get("config_overrides", ()):
            command.extend(["--config", str(config_value)])
        sandbox = (
            "read-only"
            if authority_class == "bounded"
            else tool_profile.workspace_access or options.get("sandbox")
        )
        if sandbox is not None:
            command.extend(["--sandbox", str(sandbox)])
        if options.get("full_auto"):
            command.append("--full-auto")
        if options.get("dangerously_bypass_approvals_and_sandbox"):
            command.append("--dangerously-bypass-approvals-and-sandbox")
        for add_dir in options.get("add_dirs", ()):
            command.extend(["--add-dir", str(add_dir)])
        if options.get("skip_git_repo_check") is True:
            command.append("--skip-git-repo-check")
        command.extend(
            [
                "--color",
                str(options.get("color", "never")),
                "-C",
                cwd,
                "-o",
                str(output_path),
                prompt,
            ]
        )
        return ProviderCLIInvocation(
            command=tuple(command),
            output_path=output_path,
        )

    def create_session_context(
        self,
        *,
        executable: str,
        cwd: str,
        model: str | None,
        tempdir: Path,
    ) -> ProviderCLISessionContext:
        del executable, model
        return ProviderCLISessionContext(
            tempdir=tempdir,
            work_cwd=cwd,
            process_cwd=str(tempdir),
            env={},
        )

    def build_session_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        session: ProviderCLISessionContext,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        self.validate_model(model)
        self.validate_options(options)
        output_path = session.tempdir / f"last-message-{session.turn_index + 1}.txt"
        command = [executable, "exec"]
        if session.turn_index > 0:
            command.extend(["resume", "--last"])
        if model:
            command.extend(["--model", model])
        profile = options.get("profile")
        if profile is not None:
            command.extend(["--profile", str(profile)])
        if options.get("oss"):
            command.append("--oss")
        local_provider = options.get("local_provider")
        if local_provider is not None:
            command.extend(["--local-provider", str(local_provider)])
        for config_value in options.get("config_overrides", ()):
            command.extend(["--config", str(config_value)])
        sandbox = (
            "read-only"
            if authority_class == "bounded"
            else tool_profile.workspace_access or options.get("sandbox")
        )
        if sandbox is not None:
            command.extend(["--sandbox", str(sandbox)])
        if options.get("full_auto"):
            command.append("--full-auto")
        if options.get("dangerously_bypass_approvals_and_sandbox"):
            command.append("--dangerously-bypass-approvals-and-sandbox")
        for add_dir in options.get("add_dirs", ()):
            command.extend(["--add-dir", str(add_dir)])
        if options.get("skip_git_repo_check") is True:
            command.append("--skip-git-repo-check")
        command.extend(
            [
                "--color",
                str(options.get("color", "never")),
                "-C",
                cwd,
                "-o",
                str(output_path),
                prompt,
            ]
        )
        return ProviderCLIInvocation(
            command=tuple(command),
            output_path=output_path,
            env=session.env,
            cwd=session.process_cwd,
        )

    def parse_result(
        self,
        *,
        completed: subprocess.CompletedProcess[str],
        invocation: ProviderCLIInvocation,
    ) -> ProviderCLIResult:
        if completed.returncode != 0:
            detail = _compact_process_detail(completed)
            raise RuntimeError(f"codex exec failed: {detail}")
        output_path = invocation.output_path
        if output_path is not None and output_path.exists():
            output_text = output_path.read_text(encoding="utf-8").strip()
            if output_text:
                return ProviderCLIResult(output_text=output_text)
        stdout_text = (completed.stdout or "").strip()
        if stdout_text:
            return ProviderCLIResult(output_text=stdout_text)
        detail = _compact_process_detail(completed)
        raise RuntimeError(
            "codex exec produced no output" + (f": {detail}" if detail else "")
        )


class GeminiProvider(_BaseTextProvider):
    """Gemini CLI provider adapter."""

    name = "gemini"
    default_executable = "gemini"
    supports_model = True
    supports_bounded_authority = True
    supported_workspace_access_modes = ("read-only",)

    def build_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        tempdir: Path,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        del cwd, options
        self.validate_model(model)
        command = [executable]
        if model:
            command.extend(["-m", model])
        if authority_class == "bounded" or tool_profile.workspace_access == "read-only":
            command.extend(["--approval-mode", "plan"])
        command.extend(["-p", prompt, "-y", "-o", "text"])
        return ProviderCLIInvocation(
            command=tuple(command),
            env=(
                build_gemini_session_environment(tempdir)
                if authority_class == "bounded"
                else None
            ),
        )

    def create_session_context(
        self,
        *,
        executable: str,
        cwd: str,
        model: str | None,
        tempdir: Path,
    ) -> ProviderCLISessionContext:
        del executable, model
        return ProviderCLISessionContext(
            tempdir=tempdir,
            work_cwd=cwd,
            process_cwd=cwd,
            env=build_gemini_session_environment(tempdir),
        )

    def build_session_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        session: ProviderCLISessionContext,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        del options
        self.validate_model(model)
        command = [executable]
        if session.turn_index > 0:
            command.extend(["--resume", "latest"])
        if model:
            command.extend(["-m", model])
        if authority_class == "bounded" or tool_profile.workspace_access == "read-only":
            command.extend(["--approval-mode", "plan"])
        command.extend(["-p", prompt, "-y", "-o", "text"])
        return ProviderCLIInvocation(
            command=tuple(command),
            env=session.env,
            cwd=session.process_cwd,
        )


class OpencodeProvider(_BaseTextProvider):
    """OpenCode CLI provider adapter."""

    name = "opencode"
    default_executable = "opencode"
    supports_model = True

    def validate_options(self, options: Mapping[str, Any]) -> None:
        pure_value = options.get("pure")
        extra_keys = sorted(set(options) - {"pure"})
        if extra_keys:
            keys = ", ".join(extra_keys)
            raise ValueError(f"{self.name} does not support provider options: {keys}")
        if pure_value is not None and not isinstance(pure_value, bool):
            raise TypeError(f"{self.name} provider option 'pure' must be a boolean")

    def ensure_runtime_requirements(
        self,
        executable: str,
        *,
        command_class: Literal["execute", "session"],
    ) -> None:
        del executable, command_class

    def build_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        tempdir: Path,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        del authority_class, tempdir, tool_profile
        self.validate_options(options)
        self.validate_model(model)
        command = [
            executable,
            "run",
            "--format",
            "json",
            "--dangerously-skip-permissions",
            "--dir",
            cwd,
        ]
        if options.get("pure"):
            command.append("--pure")
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        return ProviderCLIInvocation(command=tuple(command))

    def create_session_context(
        self,
        *,
        executable: str,
        cwd: str,
        model: str | None,
        tempdir: Path,
    ) -> ProviderCLISessionContext:
        del executable, model
        return ProviderCLISessionContext(
            tempdir=tempdir,
            work_cwd=cwd,
            process_cwd=str(tempdir),
            env={},
        )

    def build_session_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        session: ProviderCLISessionContext,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        del authority_class, tool_profile
        self.validate_options(options)
        self.validate_model(model)
        command = [executable, "run"]
        if session.turn_index > 0:
            command.append("--continue")
        command.extend(
            [
                "--format",
                "json",
                "--dangerously-skip-permissions",
                "--dir",
                cwd,
            ]
        )
        if options.get("pure"):
            command.append("--pure")
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        return ProviderCLIInvocation(
            command=tuple(command),
            env=session.env,
            cwd=session.process_cwd,
        )

    def parse_result(
        self,
        *,
        completed: subprocess.CompletedProcess[str],
        invocation: ProviderCLIInvocation,
    ) -> ProviderCLIResult:
        del invocation
        if completed.returncode != 0:
            stderr_text = (completed.stderr or "").lower()
            if "unsupported opencode command" in stderr_text:
                raise RuntimeError(
                    "opencode CLI does not support 'run'; install a version with "
                    "non-interactive run support"
                )
            detail = _compact_process_detail(completed)
            raise RuntimeError(f"{self.name} execution failed: {detail}")
        output_text = _parse_opencode_json_output(completed.stdout or "").strip()
        if not output_text:
            detail = _compact_process_detail(completed)
            raise RuntimeError(
                f"{self.name} produced no parseable text output"
                + (f": {detail}" if detail else "")
            )
        return ProviderCLIResult(output_text=output_text)


class QwenProvider(_BaseTextProvider):
    """Qwen CLI provider adapter."""

    name = "qwen"
    default_executable = "qwen"
    supports_model = True
    supports_bounded_authority = True
    supported_workspace_access_modes = ("read-only",)

    def validate_options(self, options: Mapping[str, Any]) -> None:
        disable_value = options.get("disable_extensions")
        extra_keys = sorted(set(options) - {"disable_extensions"})
        if extra_keys:
            keys = ", ".join(extra_keys)
            raise ValueError(f"{self.name} does not support provider options: {keys}")
        if disable_value is not None and not isinstance(disable_value, bool):
            raise TypeError(
                f"{self.name} provider option 'disable_extensions' must be a boolean"
            )

    def resolve_options(
        self,
        *,
        authority_class: str,
        raw_options: Mapping[str, Any],
        tool_profile: AgentToolProfileResult,
    ) -> dict[str, Any]:
        merged = super().resolve_options(
            authority_class=authority_class,
            raw_options=raw_options,
            tool_profile=tool_profile,
        )
        raw_disable = raw_options.get("disable_extensions")
        profile_disable = tool_profile.provider_options.get("disable_extensions")
        if profile_disable is True and raw_disable is False:
            raise ValueError(
                "spec.agent.options.disable_extensions conflicts with the "
                "resolved tool profile"
            )
        if authority_class == "bounded":
            for source_name, disable_value in (
                ("spec.agent.options", raw_disable),
                ("tool profile", profile_disable),
            ):
                if disable_value is False:
                    raise ValueError(
                        f"{self.name} authority_class='bounded' requires "
                        f"{source_name} disable_extensions=true"
                    )
            merged["disable_extensions"] = True
        return merged

    def build_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        tempdir: Path,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        del cwd, tempdir
        self.validate_model(model)
        self.validate_options(options)
        command = [executable, "-p", prompt, "-y", "-o", "text"]
        if model:
            command.extend(["-m", model])
        if authority_class == "bounded" or tool_profile.workspace_access == "read-only":
            command.extend(["--approval-mode", "plan"])
        if options.get("disable_extensions"):
            command.extend(["--extensions=", "--allowed-mcp-server-names="])
        return ProviderCLIInvocation(command=tuple(command))

    def create_session_context(
        self,
        *,
        executable: str,
        cwd: str,
        model: str | None,
        tempdir: Path,
    ) -> ProviderCLISessionContext:
        del executable, model
        return ProviderCLISessionContext(
            tempdir=tempdir,
            work_cwd=cwd,
            process_cwd=cwd,
            env={},
            session_id=str(uuid4()),
        )

    def build_session_invocation(
        self,
        *,
        executable: str,
        authority_class: str,
        prompt: str,
        cwd: str,
        model: str | None,
        options: Mapping[str, Any],
        session: ProviderCLISessionContext,
        tool_profile: AgentToolProfileResult,
    ) -> ProviderCLIInvocation:
        del cwd
        self.validate_model(model)
        self.validate_options(options)
        if session.session_id is None:
            raise RuntimeError("qwen session is missing a session_id")
        command = [executable]
        if session.turn_index == 0:
            command.extend(["--session-id", session.session_id, "--chat-recording"])
        else:
            command.extend(["--resume", session.session_id, "--chat-recording"])
        command.extend(["-p", prompt, "-y", "-o", "text"])
        if model:
            command.extend(["-m", model])
        if authority_class == "bounded" or tool_profile.workspace_access == "read-only":
            command.extend(["--approval-mode", "plan"])
        if options.get("disable_extensions"):
            command.extend(["--extensions=", "--allowed-mcp-server-names="])
        return ProviderCLIInvocation(
            command=tuple(command),
            env=session.env,
            cwd=session.process_cwd,
        )


_PROVIDERS: dict[str, ProviderCLIProvider] = {
    "claude_code": cast(ProviderCLIProvider, ClaudeCodeProvider()),
    "codex": cast(ProviderCLIProvider, CodexProvider()),
    "gemini": cast(ProviderCLIProvider, GeminiProvider()),
    "opencode": cast(ProviderCLIProvider, OpencodeProvider()),
    "qwen": cast(ProviderCLIProvider, QwenProvider()),
}


def get_provider_cli_provider(name: str) -> ProviderCLIProvider:
    """Return the registered provider adapter by stable name."""
    normalized = name.strip()
    try:
        return _PROVIDERS[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown provider_cli provider: {normalized}") from exc


def list_provider_cli_providers() -> tuple[ProviderCLIProvider, ...]:
    """Return all registered provider adapters in stable order."""
    return tuple(_PROVIDERS[name] for name in sorted(_PROVIDERS))


def resolve_provider_cli_executable(
    provider: ProviderCLIProvider,
    *,
    configured_executable: str | None = None,
    spec_context: str | None = None,
) -> str:
    """Resolve the concrete executable path for a provider CLI."""
    command = configured_executable.strip() if configured_executable else None
    if not command:
        settings = load_provider_cli_project_settings(
            provider.name,
            spec_context=spec_context,
        )
        command = settings.executable or provider.default_executable
    resolved = shutil.which(command)
    if resolved is None:
        raise RuntimeError(
            f"Unable to locate executable '{command}' for provider '{provider.name}'"
        )
    return resolved


def _parse_opencode_json_output(stdout: str) -> str:
    text_parts: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part")
        part_type = part.get("type") if isinstance(part, dict) else None
        text = part.get("text") if isinstance(part, dict) else None
        if isinstance(text, str) and (
            event.get("type") == "text" or part_type == "text"
        ):
            text_parts.append(text)
    return "".join(text_parts) if text_parts else stdout


def _compact_process_detail(completed: subprocess.CompletedProcess[str]) -> str:
    parts: list[str] = []
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if stdout:
        parts.append(f"stdout={stdout[:200]}")
    if stderr:
        parts.append(f"stderr={stderr[:200]}")
    return "; ".join(parts)


def _write_claude_mcp_config(
    tempdir: Path,
    descriptors: Sequence[AgentMCPServerDescriptor],
) -> Path:
    config_path = tempdir / "claude-mcp.json"
    payload = {
        "mcpServers": {
            descriptor.name: descriptor.to_claude_config() for descriptor in descriptors
        }
    }
    config_path.write_text(
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )
    return config_path


def _resolve_stdio_mcp_command(descriptor: AgentMCPServerDescriptor) -> str:
    command = descriptor.command
    candidate = Path(command).expanduser()
    if candidate.is_absolute():
        if not candidate.exists():
            raise RuntimeError(
                f"Unable to locate MCP server command '{descriptor.command}'"
            )
        return str(candidate)
    resolved = shutil.which(command)
    if resolved is None:
        raise RuntimeError(
            f"Unable to locate MCP server command '{descriptor.command}'"
        )
    return resolved


__all__ = [
    "ProviderCLIInvocation",
    "ProviderCLIProvider",
    "ProviderCLIResult",
    "ProviderCLISessionContext",
    "get_provider_cli_provider",
    "list_provider_cli_providers",
    "resolve_provider_cli_executable",
]
