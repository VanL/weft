"""Firing tests for the task process launcher's service working directory."""

from __future__ import annotations

import time
from pathlib import Path

import psutil
import pytest

from tests.helpers.weft_harness import WeftTestHarness
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


def test_launched_service_process_runs_in_its_context_root(
    weft_harness: WeftTestHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A service launched from another directory sits in its context root.

    The entry wiring, observed on the real process: the Manager runs in this
    process with a working directory outside the context, so without the
    change of directory at process entry the service would inherit that one.
    """

    elsewhere = tmp_path / "launcher-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    weft_harness.ensure_foreground_manager()

    def service_pids() -> list[int]:
        live = weft_harness._live_internal_service_processes()
        return [pid for processes in live.values() for pid, _created in processes]

    def working_directories() -> list[Path]:
        return [Path(psutil.Process(pid).cwd()).resolve() for pid in service_pids()]

    root = weft_harness.root.resolve()
    assert elsewhere.resolve() != root
    # The Manager records a service's pid at launch, before the spawned
    # process has reached its entry, so the directory settles a moment later.
    deadline = time.monotonic() + 30.0
    observed = working_directories()
    while (not observed or set(observed) != {root}) and time.monotonic() < deadline:
        time.sleep(0.05)
        observed = working_directories()
    assert observed, "no internal service process appeared"
    assert set(observed) == {root}
