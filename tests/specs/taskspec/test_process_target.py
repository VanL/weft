"""Spec checks for command targets in TaskSpec (TS-1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from weft.core.targets import execute_command_target
from weft.core.taskspec import SpecSection


def test_process_target_requires_string() -> None:
    with pytest.raises(ValidationError):
        SpecSection(type="command", process_target=["echo"])


def test_spec_section_rejects_shell_as_unsupported_field() -> None:
    """The TaskSpec boundary has no shell field and rejects one explicitly.

    Spec: program theory [WT-2a.10]; CLAUDE.md section 4.11 ("reject unsupported
    fields explicitly").
    """

    with pytest.raises(ValidationError) as excinfo:
        SpecSection(type="command", process_target="echo", shell=True)  # type: ignore[call-arg]
    assert [(err["type"], err["loc"]) for err in excinfo.value.errors()] == [
        ("extra_forbidden", ("shell",))
    ]


def test_command_args_appended_to_process_target(tmp_path: Path) -> None:
    script = "import sys; print(' '.join(sys.argv[1:]))"
    completed = execute_command_target(
        sys.executable,
        {"args": ["extra"]},
        args=["-c", script, "base"],
        working_dir=str(tmp_path),
    )
    assert completed.stdout.strip() == "base extra"
