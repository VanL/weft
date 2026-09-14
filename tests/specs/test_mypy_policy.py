"""Behavioral probes for test typing (Spec: 08-Testing_Strategy.md [TS-3])."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.shared]
ROOT = Path(__file__).resolve().parents[2]


def test_mypy_discovers_tests_and_checks_definitions_and_bodies(tmp_path: Path) -> None:
    """A directory scan must accept typed tests and reject actual type errors."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("", encoding="utf-8")
    probe = tests / "test_probe.py"
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        str(ROOT / "pyproject.toml"),
        "--cache-dir",
        str(tmp_path / "cache"),
        "tests",
    ]
    probe.write_text("def test_typed(value: int) -> None:\n    assert value > 0\n")
    accepted = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    probe.write_text(
        "def test_untyped(value):\n    assert value\n\n"
        "def test_bad_body(value: int) -> None:\n    value = 'wrong'\n\n"
        "def fixture() -> int:\n    return 'wrong'\n",
        encoding="utf-8",
    )
    rejected = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False
    )
    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    for error in ("no-untyped-def", "assignment", "return-value"):
        assert f"[{error}]" in rejected.stdout, rejected.stdout
    assert "tests/test_probe.py" in rejected.stdout.replace("\\", "/")


@pytest.mark.skipif(os.name == "nt", reason="The local mypy wrapper is Bash")
def test_local_mypy_wrapper_checks_tests_and_preserves_failure(tmp_path: Path) -> None:
    """Execute the wrapper with a recording interpreter, including failure exit."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "mypy-check"
    shutil.copy2(ROOT / "bin" / "mypy-check", wrapper)
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 23\n', encoding="utf-8")
    python.chmod(0o755)
    result = subprocess.run(
        [str(wrapper)], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 23
    arguments = result.stdout.splitlines()
    assert arguments[:2] == ["-m", "mypy"]
    assert str(tmp_path / "tests") in arguments
    assert f"--config-file={tmp_path / 'pyproject.toml'}" in arguments
