"""Fixture helpers for delegated provider CLI runtime tests."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from tests.fixtures.mcp_stdio_fixture import call_fixture_tool
from weft.core.agents.runtime import content_to_prompt_text
from weft.ext import AgentResolverResult, AgentToolProfileResult

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_FIXTURE_NAMES = (
    "claude_code",
    "codex",
    "gemini",
    "opencode",
    "qwen",
)
_PROVIDER_BINARIES = {
    "claude_code": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "opencode": "opencode",
    "qwen": "qwen",
}


def resolve_operator_question(*, agent, work_item, tid):  # noqa: ANN001
    del agent
    return AgentResolverResult(
        prompt=f"resolved:{content_to_prompt_text(work_item.content)}",
        instructions="resolver instructions",
        metadata={"resolver": "fixture", "tid": tid},
        artifacts=({"kind": "fixture_artifact", "id": "artifact-1"},),
    )


def provider_tool_profile(*, agent, tid):  # noqa: ANN001
    provider_name = str(agent.runtime_config.get("provider", "")).strip()
    provider_options: dict[str, Any] = {}
    if provider_name == "claude_code":
        provider_options["strict_mcp_config"] = True
    elif provider_name == "codex":
        provider_options["sandbox"] = "read-only"
        provider_options["skip_git_repo_check"] = True
    elif provider_name == "opencode":
        provider_options["pure"] = True
    elif provider_name == "qwen":
        provider_options["disable_extensions"] = True

    return AgentToolProfileResult(
        instructions="profile instructions",
        provider_options=provider_options,
        metadata={"profile": "fixture", "provider": provider_name, "tid": tid},
    )


def invalid_resolver(*, agent, work_item, tid):  # noqa: ANN001
    del agent, work_item, tid
    return "nope"


def invalid_tool_profile(*, agent, tid):  # noqa: ANN001
    del agent, tid
    return "nope"


def provider_binary_name(provider_name: str) -> str:
    """Return the simulated binary name for a provider."""
    try:
        return _PROVIDER_BINARIES[provider_name]
    except KeyError as exc:  # pragma: no cover - test setup guard
        raise ValueError(f"Unknown fixture provider: {provider_name}") from exc


def write_provider_cli_wrapper(root: Path, provider_name: str = "codex") -> Path:
    """Write a small executable wrapper for a specific fixture provider."""
    binary_name = provider_binary_name(provider_name)
    root.mkdir(parents=True, exist_ok=True)
    launcher = root / f"{binary_name}_fixture_launcher.py"
    launcher.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import sys",
                f"sys.path.insert(0, {str(REPO_ROOT)!r})",
                "from tests.fixtures.provider_cli_fixture import main",
                "",
                "if __name__ == '__main__':",
                f"    raise SystemExit(main(sys.argv[1:], provider_name={provider_name!r}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    if os.name == "nt":
        wrapper = root / f"{binary_name}.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{launcher}" %*\r\n',
            encoding="utf-8",
        )
        return wrapper

    wrapper = root / binary_name
    wrapper.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{launcher}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def main(argv: list[str], *, provider_name: str) -> int:
    """Run one simulated provider CLI."""
    if argv == ["--version"]:
        if os.environ.get("PROVIDER_CLI_FIXTURE_FAIL_PROBE") == "1":
            print("probe failed", file=sys.stderr)
            return 1
        print(f"{provider_binary_name(provider_name)}-fixture 1.0")
        return 0

    if provider_name == "claude_code":
        return _run_claude(argv)
    if provider_name == "codex":
        return _run_codex(argv)
    if provider_name == "gemini":
        return _run_gemini_or_qwen(argv, provider_name=provider_name)
    if provider_name == "opencode":
        return _run_opencode(argv)
    if provider_name == "qwen":
        return _run_gemini_or_qwen(argv, provider_name=provider_name)

    print("unsupported fixture provider", file=sys.stderr)
    return 2


def _run_claude(argv: list[str]) -> int:
    options: dict[str, Any] = {}
    prompt: str | None = None
    model: str | None = None
    session_id: str | None = None
    use_latest = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--model":
            model = argv[i + 1]
            i += 2
            continue
        if arg == "--dangerously-skip-permissions":
            options["dangerously_skip_permissions"] = True
            i += 1
            continue
        if arg == "--strict-mcp-config":
            options["strict_mcp_config"] = True
            i += 1
            continue
        if arg == "--permission-mode":
            options["permission_mode"] = argv[i + 1]
            i += 2
            continue
        if arg == "--mcp-config":
            options["mcp_servers"] = _load_claude_mcp_servers(argv[i + 1])
            i += 2
            continue
        if arg in {"-c", "--continue"}:
            use_latest = True
            i += 1
            continue
        if arg in {"-r", "--resume"}:
            session_id = argv[i + 1]
            i += 2
            continue
        if arg == "--session-id":
            session_id = argv[i + 1]
            i += 2
            continue
        if arg == "-p":
            prompt = argv[i + 1]
            i += 2
            continue
        print(f"unsupported claude arg: {arg}", file=sys.stderr)
        return 2

    if prompt is None:
        print("missing prompt", file=sys.stderr)
        return 2
    return _write_plain_text_result(
        provider_name="claude_code",
        prompt=prompt,
        model=model,
        options=options,
        session_key=session_id,
        use_latest=use_latest,
    )


def _run_codex(argv: list[str]) -> int:
    if not argv or argv[0] != "exec":
        print("unsupported codex command", file=sys.stderr)
        return 2
    output_path: Path | None = None
    cwd = os.getcwd()
    model: str | None = None
    options: dict[str, Any] = {
        "add_dirs": [],
        "config_overrides": [],
    }
    prompt: str | None = None
    session_id: str | None = None
    use_latest = False
    i = 1
    if i < len(argv) and argv[i] == "resume":
        i += 1
        if i < len(argv) and argv[i] == "--last":
            use_latest = True
            i += 1
        elif i < len(argv) and not argv[i].startswith("-"):
            session_id = argv[i]
            i += 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--model":
            model = argv[i + 1]
            i += 2
            continue
        if arg in {"-o", "--output-last-message"}:
            output_path = Path(argv[i + 1])
            i += 2
            continue
        if arg in {"-C", "--cd"}:
            cwd = argv[i + 1]
            i += 2
            continue
        if arg == "--color":
            options["color"] = argv[i + 1]
            i += 2
            continue
        if arg == "--sandbox":
            options["sandbox"] = argv[i + 1]
            i += 2
            continue
        if arg == "--profile":
            options["profile"] = argv[i + 1]
            i += 2
            continue
        if arg == "--local-provider":
            options["local_provider"] = argv[i + 1]
            i += 2
            continue
        if arg == "--add-dir":
            options["add_dirs"].append(argv[i + 1])
            i += 2
            continue
        if arg in {"-c", "--config"}:
            options["config_overrides"].append(argv[i + 1])
            i += 2
            continue
        if arg == "--skip-git-repo-check":
            options["skip_git_repo_check"] = True
            i += 1
            continue
        if arg == "--full-auto":
            options["full_auto"] = True
            i += 1
            continue
        if arg == "--oss":
            options["oss"] = True
            i += 1
            continue
        if arg == "--dangerously-bypass-approvals-and-sandbox":
            options["dangerously_bypass_approvals_and_sandbox"] = True
            i += 1
            continue
        prompt = arg
        i += 1

    if output_path is None:
        print("missing output path", file=sys.stderr)
        return 2
    if prompt is None:
        print("missing prompt", file=sys.stderr)
        return 2
    result = _execute_fixture_request(
        provider_name="codex",
        prompt=prompt,
        model=model,
        options=options,
        cwd=cwd,
        session_key=session_id,
        use_latest=use_latest,
    )
    if isinstance(result, int):
        return result
    output_path.write_text(result, encoding="utf-8")
    print("fixture exec ok")
    return 0


def _run_gemini_or_qwen(argv: list[str], *, provider_name: str) -> int:
    model: str | None = None
    prompt: str | None = None
    options: dict[str, Any] = {}
    session_id: str | None = None
    use_latest = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "-m":
            model = argv[i + 1]
            i += 2
            continue
        if arg == "-p":
            prompt = argv[i + 1]
            i += 2
            continue
        if provider_name == "gemini" and arg == "--resume":
            session_id = argv[i + 1]
            use_latest = session_id == "latest"
            i += 2
            continue
        if arg == "--approval-mode":
            options["approval_mode"] = argv[i + 1]
            i += 2
            continue
        if provider_name == "gemini" and arg == "--allowed-mcp-server-names":
            options["allowed_mcp_server_names"] = argv[i + 1]
            i += 2
            continue
        if provider_name == "gemini" and arg == "--include-directories":
            options.setdefault("include_directories", []).append(argv[i + 1])
            i += 2
            continue
        if arg == "-y":
            options["yes"] = True
            i += 1
            continue
        if arg == "-o":
            options["output_mode"] = argv[i + 1]
            i += 2
            continue
        if provider_name == "qwen" and arg == "--session-id":
            session_id = argv[i + 1]
            i += 2
            continue
        if provider_name == "qwen" and arg in {"-c", "--continue"}:
            use_latest = True
            i += 1
            continue
        if provider_name == "qwen" and arg == "--resume":
            session_id = argv[i + 1]
            i += 2
            continue
        if provider_name == "qwen" and arg == "--chat-recording":
            options["chat_recording"] = True
            i += 1
            continue
        if provider_name == "qwen" and arg == "--extensions=":
            options["extensions"] = ""
            i += 1
            continue
        if provider_name == "qwen" and arg == "--allowed-mcp-server-names=":
            options["allowed_mcp_server_names"] = ""
            i += 1
            continue
        if provider_name == "qwen" and arg == "--allowed-mcp-server-names":
            options["allowed_mcp_server_names"] = argv[i + 1]
            i += 2
            continue
        if provider_name == "qwen" and arg in {"--include-directories", "--add-dir"}:
            options.setdefault("include_directories", []).append(argv[i + 1])
            i += 2
            continue
        print(f"unsupported {provider_name} arg: {arg}", file=sys.stderr)
        return 2

    if prompt is None:
        print("missing prompt", file=sys.stderr)
        return 2
    return _write_plain_text_result(
        provider_name=provider_name,
        prompt=prompt,
        model=model,
        options=options,
        session_key=session_id,
        use_latest=use_latest,
    )


def _run_opencode(argv: list[str]) -> int:
    if argv[:2] == ["run", "--help"]:
        if os.environ.get("PROVIDER_CLI_FIXTURE_OPENCODE_NO_RUN") == "1":
            print("legacy opencode help")
            return 0
        print("Usage: opencode run [message]")
        return 0
    if not argv or argv[0] != "run":
        print("unsupported opencode command", file=sys.stderr)
        return 2
    if os.environ.get("PROVIDER_CLI_FIXTURE_OPENCODE_NO_RUN") == "1":
        print("unsupported opencode command", file=sys.stderr)
        return 2

    prompt: str | None = None
    model: str | None = None
    cwd = os.getcwd()
    options: dict[str, Any] = {}
    session_id: str | None = None
    use_latest = False
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--format":
            options["format"] = argv[i + 1]
            i += 2
            continue
        if arg in {"-c", "--continue"}:
            use_latest = True
            i += 1
            continue
        if arg in {"-s", "--session"}:
            session_id = argv[i + 1]
            i += 2
            continue
        if arg == "--dangerously-skip-permissions":
            options["dangerously_skip_permissions"] = True
            i += 1
            continue
        if arg == "--dir":
            cwd = argv[i + 1]
            i += 2
            continue
        if arg == "--model":
            model = argv[i + 1]
            i += 2
            continue
        if arg == "--pure":
            options["pure"] = True
            i += 1
            continue
        prompt = arg
        i += 1

    if prompt is None:
        print("missing prompt", file=sys.stderr)
        return 2

    output_text = _execute_fixture_request(
        provider_name="opencode",
        prompt=prompt,
        model=model,
        options=options,
        cwd=cwd,
        session_key=session_id,
        use_latest=use_latest,
    )
    if isinstance(output_text, int):
        return output_text
    events = [
        {"type": "text", "part": {"type": "text", "text": output_text}},
    ]
    for event in events:
        print(json.dumps(event, sort_keys=True))
    return 0


def _write_plain_text_result(
    *,
    provider_name: str,
    prompt: str,
    model: str | None,
    options: dict[str, Any],
    session_key: str | None = None,
    use_latest: bool = False,
) -> int:
    output_text = _execute_fixture_request(
        provider_name=provider_name,
        prompt=prompt,
        model=model,
        options=options,
        session_key=session_key,
        use_latest=use_latest,
    )
    if isinstance(output_text, int):
        return output_text
    print(output_text)
    return 0


def _execute_fixture_request(
    *,
    provider_name: str,
    prompt: str,
    model: str | None,
    options: dict[str, Any],
    cwd: str | None = None,
    session_key: str | None = None,
    use_latest: bool = False,
) -> str | int:
    directive_text = _normalize_directive_text(prompt)
    sleep_result = _apply_sleep_directive(directive_text)
    if isinstance(sleep_result, int):
        return sleep_result
    directive_text = sleep_result
    if directive_text.startswith("fail:"):
        print(
            directive_text.split(":", 1)[1].strip() or "fixture exec failed",
            file=sys.stderr,
        )
        return 1

    turn_index = 1
    remembered: str | None = None
    mcp_result: str | None = None
    if session_key is not None or use_latest or directive_text.startswith("remember:"):
        session_payload, resolved_session_key = _load_or_create_session_state(
            provider_name=provider_name,
            session_key=session_key,
            use_latest=use_latest,
        )
        turn_index = int(session_payload.get("turn_index", 0)) + 1
        remembered_value = session_payload.get("remembered")
        remembered = remembered_value if isinstance(remembered_value, str) else None
        if directive_text.startswith("remember:"):
            remembered = directive_text.split(":", 1)[1].strip() or None
            session_payload["remembered"] = remembered
        session_payload["turn_index"] = turn_index
        _store_session_state(
            provider_name=provider_name,
            session_key=resolved_session_key,
            payload=session_payload,
        )
    elif directive_text.startswith("remember:"):
        remembered = directive_text.split(":", 1)[1].strip() or None

    if directive_text.startswith("use_mcp:"):
        token = directive_text.split(":", 1)[1].strip()
        mcp_servers = options.get("mcp_servers")
        if not isinstance(mcp_servers, dict) or not mcp_servers:
            print(
                "fixture request asked for MCP without configured servers",
                file=sys.stderr,
            )
            return 2
        first_server = next(
            (config for config in mcp_servers.values() if isinstance(config, dict)),
            None,
        )
        if first_server is None:
            print("fixture request has no usable MCP server config", file=sys.stderr)
            return 2
        try:
            mcp_result = call_fixture_tool(first_server, token=token)
        except Exception as exc:
            print(f"fixture MCP call failed: {exc}", file=sys.stderr)
            return 1

    payload = {
        "provider": provider_name,
        "model": model,
        "cwd": cwd or os.getcwd(),
        "env_value": os.environ.get("WEFT_PROVIDER_FIXTURE_ENV"),
        "prompt": prompt,
        "options": options,
        "turn_index": turn_index,
        "remembered": remembered,
        "mcp_result": mcp_result,
    }
    return json.dumps(payload, sort_keys=True)


def _normalize_directive_text(prompt: str) -> str:
    directive_text = prompt.strip()
    paragraphs = [part.strip() for part in prompt.split("\n\n") if part.strip()]
    if paragraphs:
        directive_text = paragraphs[-1]
    if directive_text.startswith("resolved:"):
        directive_text = directive_text.removeprefix("resolved:").strip()
    return directive_text


def _apply_sleep_directive(directive_text: str) -> str | int:
    normalized = directive_text
    if normalized.startswith("sleep:"):
        line, _, remainder = normalized.partition("\n")
        try:
            time.sleep(float(line.split(":", 1)[1]))
        except ValueError:
            print("invalid sleep request", file=sys.stderr)
            return 2
        if remainder:
            normalized = remainder
        else:
            normalized = ""
    return normalized


def _load_claude_mcp_servers(raw_value: str) -> dict[str, Any]:
    candidate = Path(raw_value)
    if candidate.exists():
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    else:
        payload = json.loads(raw_value)
    if not isinstance(payload, dict):
        raise ValueError("Claude MCP config must be a JSON object")
    servers = payload.get("mcpServers") or {}
    if not isinstance(servers, dict):
        raise ValueError("Claude MCP config must contain mcpServers")
    return servers


def _load_or_create_session_state(
    *,
    provider_name: str,
    session_key: str | None,
    use_latest: bool,
) -> tuple[dict[str, Any], str]:
    resolved_session_key = session_key
    root = _session_state_root(provider_name)
    latest_path = root / "latest.txt"
    if use_latest:
        if latest_path.exists():
            resolved_session_key = (
                latest_path.read_text(encoding="utf-8").strip() or None
            )
        if resolved_session_key is None:
            resolved_session_key = "latest"
    if resolved_session_key is None:
        resolved_session_key = "default"

    payload: dict[str, Any] = {}
    session_path = _session_state_path(provider_name, resolved_session_key)
    if session_path.exists():
        payload = json.loads(session_path.read_text(encoding="utf-8"))

    latest_path.write_text(f"{resolved_session_key}\n", encoding="utf-8")
    return payload, resolved_session_key


def _store_session_state(
    *,
    provider_name: str,
    session_key: str,
    payload: dict[str, Any],
) -> None:
    session_path = _session_state_path(provider_name, session_key)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        f"{json.dumps(payload, sort_keys=True)}\n",
        encoding="utf-8",
    )


def _session_state_root(provider_name: str) -> Path:
    if provider_name == "codex":
        base = Path(os.environ.get("CODEX_HOME") or os.getcwd())
    elif provider_name in {"gemini", "qwen", "claude_code"}:
        base = Path(os.environ.get("HOME") or os.getcwd())
    else:
        base = Path(os.getcwd())
    root = base / ".weft-provider-cli-fixture"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_state_path(provider_name: str, session_key: str) -> Path:
    return _session_state_root(provider_name) / provider_name / f"{session_key}.json"
