"""Firing tests for the task process launcher's service working directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft._constants import (
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
)
from weft.core.launcher import service_working_directory
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = pytest.mark.shared


def _spec(
    *, weft_context: str | None, internal: bool, tid: str = "1793850000000000000"
) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="probe",
        spec=SpecSection(
            type="function",
            function_target="weft.tasks:noop",
            weft_context=weft_context,
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
        metadata={
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
        }
        if internal
        else {},
    )


def test_internal_service_runs_in_its_own_context_root(tmp_path: Path) -> None:
    """A service's cwd is its context root, not its launcher's directory.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1] item 7. Named
    mutation: inheriting the launcher's cwd let a service launched from inside
    another test context's directory pin that directory on Windows.
    """

    assert (
        service_working_directory(_spec(weft_context=str(tmp_path), internal=True))
        == tmp_path
    )


def test_user_work_keeps_the_inherited_working_directory(tmp_path: Path) -> None:
    assert (
        service_working_directory(_spec(weft_context=str(tmp_path), internal=False))
        is None
    )


def test_service_without_a_usable_context_root_keeps_the_inherited_cwd(
    tmp_path: Path,
) -> None:
    assert service_working_directory(_spec(weft_context=None, internal=True)) is None
    assert service_working_directory(_spec(weft_context=".weft", internal=True)) is None
    assert (
        service_working_directory(
            _spec(weft_context=str(tmp_path / "missing"), internal=True)
        )
        is None
    )
