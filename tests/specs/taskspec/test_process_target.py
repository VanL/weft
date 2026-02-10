"""Spec checks for command targets in TaskSpec (TS-1)."""

from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from weft.core.targets import execute_command_target
from weft.core.taskspec import SpecSection


def test_process_target_requires_string() -> None:
    with pytest.raises(ValidationError):
        SpecSection(type="command", process_target=["echo"])  # type: ignore[arg-type]


def test_command_args_appended_to_process_target(tmp_path) -> None:
    script = "import sys; print(' '.join(sys.argv[1:]))"
    completed = execute_command_target(
        sys.executable,
        {"args": ["extra"]},
        args=["-c", script, "base"],
        working_dir=str(tmp_path),
    )
    assert completed.stdout.strip() == "base extra"
