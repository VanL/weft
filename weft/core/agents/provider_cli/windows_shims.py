"""Windows batch-shim helpers for delegated provider CLI runtimes.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-5]
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Final

from weft._constants import WINDOWS_CMD_SHIM_SUFFIXES

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r'"([^"]+)"|([^\s"]+)')


def resolve_windows_cmd_shim_command(command: tuple[str, ...]) -> tuple[str, ...]:
    """Rewrite a Windows batch shim to its underlying interpreter command.

    Provider CLIs on Windows are often installed as ``.cmd`` shims. Invoking
    those shims directly through ``subprocess`` is fragile because the shim
    re-parses argv with ``cmd.exe`` rules. When a simple launcher shape is
    detected, return the interpreter prefix plus the original user args so
    Python can invoke the real process directly.
    """

    if not command:
        return command

    shim_path = Path(command[0])
    if (
        shim_path.suffix.lower() not in WINDOWS_CMD_SHIM_SUFFIXES
        or not shim_path.is_file()
    ):
        return command

    prefix = _parse_cmd_shim_prefix(shim_path)
    if prefix is None:
        return command
    return (*prefix, *command[1:])


def _parse_cmd_shim_prefix(shim_path: Path) -> tuple[str, ...] | None:
    try:
        contents = shim_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        contents = shim_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line or "%*" not in line:
            continue
        tokens = _line_tokens(line)
        if len(tokens) < 2:
            continue
        interpreter = _expand_cmd_token(tokens[0], shim_path.parent)
        script = _expand_cmd_token(tokens[1], shim_path.parent)
        if _is_usable_prefix(interpreter, script):
            return (interpreter, script)
    return None


def _line_tokens(line: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(line):
        token = match.group(1) or match.group(2)
        if token:
            tokens.append(token)
    return tokens


def _expand_cmd_token(token: str, shim_dir: Path) -> str:
    cleaned = token.strip()
    if not cleaned:
        return cleaned

    lowered = cleaned.lower()
    if lowered.startswith("%~dp0"):
        suffix = cleaned[5:].lstrip("\\/")
        return str((shim_dir / Path(suffix.replace("\\", "/"))).resolve())
    if lowered in {"node", "node.exe", "python", "python.exe", "py", "py.exe"}:
        resolved = shutil.which(cleaned)
        if resolved is not None:
            return str(Path(resolved).resolve())
    candidate = Path(cleaned)
    if candidate.is_absolute():
        return str(candidate.resolve())
    if "/" in cleaned or "\\" in cleaned:
        return str((shim_dir / Path(cleaned.replace("\\", "/"))).resolve())
    return cleaned


def _is_usable_prefix(interpreter: str, script: str) -> bool:
    if not interpreter or not script:
        return False

    interpreter_path = Path(interpreter)
    if interpreter_path.is_absolute() and not interpreter_path.exists():
        return False

    script_path = Path(script)
    if script_path.is_absolute():
        return script_path.exists()

    return "/" in script or "\\" in script or script.endswith((".js", ".py"))


__all__ = ["resolve_windows_cmd_shim_command"]
