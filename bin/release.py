#!/usr/bin/env python3
"""Repo-local release helper for Weft maintainers."""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
PYPROJECT_PATH: Final[Path] = PROJECT_ROOT / "pyproject.toml"
CONSTANTS_PATH: Final[Path] = PROJECT_ROOT / "weft" / "_constants.py"
RELEASE_GATE_WORKFLOW: Final[str] = ".github/workflows/release-gate.yml"
VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d+\.\d+\.\d+$")
PYPROJECT_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'(?m)^version = "([^"]+)"$'
)
CONSTANTS_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'(?m)^__version__:\s*Final\[str\]\s*=\s*"([^"]+)"$'
)

PRECHECK_COMMANDS: Final[tuple[tuple[str, ...], ...]] = (
    (
        "uv",
        "run",
        "pytest",
        "-v",
        "--tb=short",
        "-m",
        "not slow",
        "--override-ini=addopts=-ra -q --strict-markers -n auto --dist loadgroup",
    ),
    ("uv", "run", "bin/pytest-pg", "--all"),
    ("uv", "run", "ruff", "check", "weft"),
    ("uv", "run", "ruff", "format", "--check", "weft"),
    ("uv", "run", "mypy", "weft", "--config-file", "pyproject.toml"),
)
POSTUPDATE_COMMANDS: Final[tuple[tuple[str, ...], ...]] = (
    ("uv", "run", "pytest", "tests/system/test_constants.py", "-q"),
    ("uv", "build"),
)


def validate_version(version: str) -> str:
    """Validate the explicit release version."""
    normalized = version.strip()
    if not VERSION_PATTERN.fullmatch(normalized):
        raise ValueError("Version must use X.Y.Z format, for example: 0.1.1")
    return normalized


def _extract_version(
    path: Path,
    pattern: re.Pattern[str],
    *,
    label: str,
) -> str:
    text = path.read_text(encoding="utf-8")
    match = pattern.search(text)
    if match is None:
        raise RuntimeError(f"Could not find version in {label}: {path}")
    return match.group(1)


def read_current_version(
    *,
    pyproject_path: Path = PYPROJECT_PATH,
    constants_path: Path = CONSTANTS_PATH,
) -> str:
    """Read and verify the current repo version."""
    pyproject_version = _extract_version(
        pyproject_path,
        PYPROJECT_VERSION_PATTERN,
        label="pyproject.toml",
    )
    constants_version = _extract_version(
        constants_path,
        CONSTANTS_VERSION_PATTERN,
        label="weft/_constants.py",
    )
    if pyproject_version != constants_version:
        raise RuntimeError(
            "Version mismatch between pyproject.toml "
            f"({pyproject_version}) and weft/_constants.py ({constants_version})"
        )
    return pyproject_version


def _replace_version(
    text: str,
    pattern: re.Pattern[str],
    version: str,
    *,
    label: str,
) -> str:
    updated_text, count = pattern.subn(
        lambda match: match.group(0).replace(match.group(1), version),
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"Expected one version assignment in {label}, found {count}")
    return updated_text


def write_version_files(
    version: str,
    *,
    pyproject_path: Path = PYPROJECT_PATH,
    constants_path: Path = CONSTANTS_PATH,
) -> None:
    """Update the canonical version files together."""
    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    constants_text = constants_path.read_text(encoding="utf-8")

    updated_pyproject = _replace_version(
        pyproject_text,
        PYPROJECT_VERSION_PATTERN,
        version,
        label="pyproject.toml",
    )
    updated_constants = _replace_version(
        constants_text,
        CONSTANTS_VERSION_PATTERN,
        version,
        label="weft/_constants.py",
    )

    pyproject_path.write_text(updated_pyproject, encoding="utf-8")
    constants_path.write_text(updated_constants, encoding="utf-8")


def _format_command(command: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run_command(command: tuple[str, ...], *, dry_run: bool = False) -> None:
    """Run a command from the repo root, printing it first."""
    print(f"$ {_format_command(command)}")
    if dry_run:
        return
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def is_dirty_worktree() -> bool:
    """Return True when git reports local modifications."""
    result = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def _require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required command not found on PATH: {name}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a local Weft release")
    parser.add_argument(
        "--version",
        required=True,
        help="Explicit release version in X.Y.Z format",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Deprecated compatibility flag. GitHub Releases are now created by "
            "the tag-push workflow after SQLite and Postgres tests pass."
        ),
    )
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip preflight test/lint/type-check commands",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without modifying files or running commands",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    target_version = validate_version(args.version)
    current_version = read_current_version()
    dirty = is_dirty_worktree()
    tag_name = f"v{target_version}"

    print(f"current: {current_version}")
    print(f"target:  {target_version}")

    if target_version == current_version:
        raise RuntimeError(
            f"Target version already matches current version: {target_version}"
        )

    if args.dry_run:
        if dirty:
            print("dry-run: working tree is dirty; a real release would fail")
        if args.publish:
            print(
                "--publish is ignored: "
                f"{RELEASE_GATE_WORKFLOW} creates the GitHub Release after "
                "the pushed tag passes SQLite and Postgres tests"
            )
        if not args.skip_checks:
            for command in PRECHECK_COMMANDS:
                run_command(command, dry_run=True)
        print(
            "dry-run: would update "
            f"{PYPROJECT_PATH.relative_to(PROJECT_ROOT)} and "
            f"{CONSTANTS_PATH.relative_to(PROJECT_ROOT)}"
        )
        for command in POSTUPDATE_COMMANDS:
            run_command(command, dry_run=True)
        for command in (
            ("git", "add", "pyproject.toml", "weft/_constants.py"),
            ("git", "commit", "-m", f"Release {target_version}"),
            ("git", "tag", tag_name),
            ("git", "push"),
            ("git", "push", "origin", tag_name),
        ):
            run_command(command, dry_run=True)
        print(
            "dry-run: next step is to wait for "
            f"{RELEASE_GATE_WORKFLOW} to run on {tag_name}; it will create the "
            "GitHub Release after the SQLite and Postgres suites pass"
        )
        return 0

    if dirty:
        raise RuntimeError("Working tree must be clean before release.")

    _require_command("uv")
    if args.publish:
        print(
            "--publish is ignored: "
            f"{RELEASE_GATE_WORKFLOW} creates the GitHub Release after the "
            "pushed tag passes SQLite and Postgres tests"
        )

    if not args.skip_checks:
        for command in PRECHECK_COMMANDS:
            run_command(command)

    write_version_files(target_version)
    print(
        "Updated version files: "
        f"{PYPROJECT_PATH.relative_to(PROJECT_ROOT)}, "
        f"{CONSTANTS_PATH.relative_to(PROJECT_ROOT)}"
    )

    for command in POSTUPDATE_COMMANDS:
        run_command(command)

    for command in (
        ("git", "add", "pyproject.toml", "weft/_constants.py"),
        ("git", "commit", "-m", f"Release {target_version}"),
        ("git", "tag", tag_name),
        ("git", "push"),
        ("git", "push", "origin", tag_name),
    ):
        run_command(command)

    print(
        "Next step: wait for "
        f"{RELEASE_GATE_WORKFLOW} to run on {tag_name}; it will create the "
        "GitHub Release after the SQLite and Postgres suites pass"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
