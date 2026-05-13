"""Import-light Weft bootstrap for early environment-file loading.

This module intentionally avoids importing the full Typer app, context
resolution, or configuration helpers until after ``WEFT_ENV_FILE`` has been
applied.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-0.3], [CLI-5]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from weft._constants import WEFT_ENV_FILE_ENV

_env_key_pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class EnvFileError(ValueError):
    """Raised when a bootstrap env file cannot be loaded safely."""

    def __init__(self, path: Path, message: str, *, line_number: int | None = None):
        location = f"{path}"
        if line_number is not None:
            location = f"{location}:{line_number}"
        super().__init__(f"{WEFT_ENV_FILE_ENV} {location}: {message}")
        self.path = path
        self.line_number = line_number


def parse_env_file(text: str, *, path: Path) -> dict[str, str]:
    """Parse Weft's narrow dotenv-style bootstrap format.

    Supported lines are blank lines, full-line comments, optional ``export``,
    and ``KEY=VALUE`` assignments with unquoted, single-quoted, or
    double-quoted values. The parser deliberately does not evaluate shell
    syntax, interpolate variables, process includes, or accept multiline
    values.
    """

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()

        if "=" not in stripped:
            raise EnvFileError(
                path,
                "expected KEY=VALUE assignment",
                line_number=line_number,
            )

        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not _env_key_pattern.fullmatch(key):
            raise EnvFileError(
                path,
                f"invalid environment variable name {key!r}",
                line_number=line_number,
            )

        values[key] = _parse_env_value(raw_value.strip(), path=path, line_number=line_number)
    return values


def apply_env_file(path_value: str) -> None:
    """Load *path_value* into missing ``os.environ`` keys."""

    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve(strict=False)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EnvFileError(path, "file does not exist") from exc
    except OSError as exc:
        raise EnvFileError(path, f"unable to read file: {exc.strerror or exc}") from exc
    for key, value in parse_env_file(text, path=path).items():
        os.environ.setdefault(key, value)


def main() -> int | None:
    """Apply ``WEFT_ENV_FILE`` before importing and invoking the CLI app."""

    env_file = os.environ.get(WEFT_ENV_FILE_ENV)
    if env_file is not None and env_file.strip():
        try:
            apply_env_file(env_file)
        except EnvFileError as exc:
            sys.stderr.write(f"{exc}\n")
            return 2

    from weft.cli.app import app

    app()
    return None


def _parse_env_value(raw_value: str, *, path: Path, line_number: int) -> str:
    if raw_value == "":
        return ""
    quote = raw_value[0]
    if quote not in {"'", '"'}:
        return raw_value
    if len(raw_value) < 2 or raw_value[-1] != quote:
        raise EnvFileError(
            path,
            "quoted value must end with the matching quote",
            line_number=line_number,
        )
    value = raw_value[1:-1]
    return value


__all__ = [
    "EnvFileError",
    "WEFT_ENV_FILE_ENV",
    "apply_env_file",
    "main",
    "parse_env_file",
]
