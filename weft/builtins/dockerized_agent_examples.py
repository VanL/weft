"""Shared support code for Docker-backed delegated agent example builtins.

Spec references:
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from weft._constants import (
    CLAUDE_DOCKER_SANDBOX_ENV,
    CLAUDE_KEYCHAIN_SERVICE,
    CLAUDE_PORTABLE_AUTH_ENV_NAMES,
    DOCKERIZED_AGENT_CONTAINER_DOC_PATH,
)
from weft.core.taskspec.run_input import SpecRunInputRequest
from weft.ext import RunnerEnvironmentProfileResult


def dockerized_agent_environment_profile(
    *,
    target_type: str,
    runner_name: str,
    runner_options: dict[str, Any],
    env: dict[str, str],
    working_dir: str | None,
    tid: str | None,
) -> RunnerEnvironmentProfileResult:
    """Materialize runner inputs for the shipped Docker-backed agent examples."""
    del target_type, runner_options, working_dir
    if runner_name != "docker":
        raise ValueError(
            "dockerized_agent_environment_profile requires runner_name='docker'"
        )

    return RunnerEnvironmentProfileResult(
        runner_options={
            "container_workdir": "/tmp",
            "mount_workdir": False,
            "work_item_mounts": [
                {
                    "source_path_ref": "metadata.document_path",
                    "target": DOCKERIZED_AGENT_CONTAINER_DOC_PATH,
                    "read_only": True,
                    "required": False,
                    "kind": "file",
                }
            ],
        },
        env=dict(env),
        metadata={
            "builtin": "dockerized-agent",
            "container_doc_path": DOCKERIZED_AGENT_CONTAINER_DOC_PATH,
            "tid": tid,
        },
    )


def claude_code_dockerized_agent_environment_profile(
    *,
    target_type: str,
    runner_name: str,
    runner_options: dict[str, Any],
    env: dict[str, str],
    working_dir: str | None,
    tid: str | None,
    platform: str | None = None,
    home_dir: Path | None = None,
    oauth_token_reader: Callable[[], str] | None = None,
) -> RunnerEnvironmentProfileResult:
    """Materialize runner inputs for the shipped Claude Docker example.

    On macOS, when no portable auth is already available, this profile performs
    an explicit runtime-only Keychain lookup and injects
    ``CLAUDE_CODE_OAUTH_TOKEN`` for this task run only. Validation paths also
    materialize runner environment profiles, so Keychain lookup is deferred
    until a real task run with a concrete TID is starting.
    """
    base = dockerized_agent_environment_profile(
        target_type=target_type,
        runner_name=runner_name,
        runner_options=runner_options,
        env=env,
        working_dir=working_dir,
        tid=tid,
    )
    injected_env, auth_mode = _resolve_claude_code_example_auth_env(
        env=base.env,
        host_env=os.environ,
        platform=sys.platform if platform is None else platform,
        home_dir=Path.home() if home_dir is None else home_dir,
        oauth_token_reader=oauth_token_reader,
        tid=tid,
    )
    merged_env = dict(base.env)
    merged_env.update(CLAUDE_DOCKER_SANDBOX_ENV)
    merged_env.update(injected_env)
    metadata = dict(base.metadata)
    metadata.update(
        {
            "provider": "claude_code",
            "auth_mode": auth_mode,
            "auth_env_injected": bool(injected_env),
        }
    )
    return RunnerEnvironmentProfileResult(
        runner_options=base.runner_options,
        env=merged_env,
        metadata=metadata,
    )


def _resolve_claude_code_example_auth_env(
    *,
    env: Mapping[str, str],
    host_env: Mapping[str, str],
    platform: str,
    home_dir: Path,
    oauth_token_reader: Callable[[], str] | None,
    tid: str | None,
) -> tuple[dict[str, str], str]:
    if _has_portable_claude_auth(env) or _has_portable_claude_auth(host_env):
        return {}, "explicit_env"
    if (home_dir / ".claude" / ".credentials.json").exists():
        return {}, "mounted_credentials_file"
    if tid is None:
        return {}, "runtime_deferred"
    if platform != "darwin":
        return {}, "runtime_defaults"
    reader = (
        _default_claude_code_oauth_token_reader
        if oauth_token_reader is None
        else oauth_token_reader
    )
    try:
        token = reader().strip()
    except Exception as exc:  # pragma: no cover - keychain reader boundary
        raise ValueError(
            "dockerized-agent could not acquire Claude Code "
            "runtime auth from macOS Keychain. Set CLAUDE_CODE_OAUTH_TOKEN, "
            "ANTHROPIC_AUTH_TOKEN, or ANTHROPIC_API_KEY explicitly, or create "
            "~/.claude/.credentials.json."
        ) from exc
    if not token:
        raise ValueError(
            "dockerized-agent acquired an empty Claude Code "
            "OAuth token from macOS Keychain."
        )
    return {"CLAUDE_CODE_OAUTH_TOKEN": token}, "keychain_oauth_token"


def _has_portable_claude_auth(env: Mapping[str, str]) -> bool:
    return any(
        isinstance(env.get(name), str) and str(env.get(name)).strip()
        for name in CLAUDE_PORTABLE_AUTH_ENV_NAMES
    )


def _default_claude_code_oauth_token_reader() -> str:
    stderr_lines: list[str] = []
    commands: list[list[str]] = []
    for account in _claude_keychain_account_candidates(os.environ):
        commands.append(
            [
                "security",
                "find-generic-password",
                "-s",
                CLAUDE_KEYCHAIN_SERVICE,
                "-a",
                account,
                "-w",
            ]
        )
    commands.append(
        ["security", "find-generic-password", "-s", CLAUDE_KEYCHAIN_SERVICE, "-w"]
    )
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise ValueError(
                "Unable to execute macOS 'security' command for Claude Code auth lookup"
            ) from exc
        stdout = completed.stdout.strip()
        if completed.returncode == 0 and stdout:
            return _extract_claude_code_oauth_token(stdout)
        stderr = (completed.stderr or "").strip()
        if stderr:
            stderr_lines.append(stderr.splitlines()[0])
    detail = (
        stderr_lines[-1]
        if stderr_lines
        else "No matching Claude Code Keychain credential was found"
    )
    raise ValueError(detail)


def _claude_keychain_account_candidates(host_env: Mapping[str, str]) -> tuple[str, ...]:
    seen: set[str] = set()
    candidates: list[str] = []
    for raw_candidate in (
        host_env.get("USER"),
        host_env.get("LOGNAME"),
        getpass.getuser(),
        "Claude",
    ):
        if not isinstance(raw_candidate, str):
            continue
        candidate = raw_candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return tuple(candidates)


def _extract_claude_code_oauth_token(raw_value: str) -> str:
    stripped = raw_value.strip()
    if not stripped:
        raise ValueError("Claude Code Keychain credential was empty")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    for path in (
        ("claudeAiOauth", "accessToken"),
        ("oauth", "accessToken"),
        ("accessToken",),
        ("oauthToken",),
        ("token",),
    ):
        current: Any = payload
        for key in path:
            if not isinstance(current, Mapping):
                current = None
                break
            current = current.get(key)
        if isinstance(current, str) and current.strip():
            return current.strip()
    raise ValueError(
        "Claude Code Keychain credential did not contain an OAuth access token"
    )


def dockerized_agent_run_input(
    request: SpecRunInputRequest,
) -> dict[str, Any]:
    """Build the public agent work item for shipped Docker-backed examples."""
    prompt = request.arguments["prompt"]
    document_path = request.arguments.get("document")
    stdin_text = request.stdin_text

    if document_path and stdin_text is not None:
        raise ValueError("Provide either --document or stdin, not both.")
    if document_path:
        return {
            "template": "explain_mounted",
            "template_args": {
                "prompt": prompt,
                "document_target_path": DOCKERIZED_AGENT_CONTAINER_DOC_PATH,
            },
            "metadata": {"document_path": document_path},
        }
    if stdin_text is not None:
        return {
            "template": "explain_inline",
            "template_args": {
                "prompt": prompt,
                "document_text": stdin_text,
            },
        }
    raise ValueError("Provide a document with --document or piped stdin.")


__all__ = [
    "claude_code_dockerized_agent_environment_profile",
    "dockerized_agent_environment_profile",
    "dockerized_agent_run_input",
]
