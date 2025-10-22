"""Black-box CLI tests for `weft validate-taskspec`."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import run_cli  # re-exported for clarity
from tests.taskspec.fixtures import create_valid_function_taskspec


def write_taskspec(path: Path, spec) -> None:
    path.write_text(spec.model_dump_json(indent=2))


def test_validate_taskspec_success(workdir):
    """Valid TaskSpec should pass validation via CLI."""
    taskspec = create_valid_function_taskspec()
    spec_path = workdir / "taskspec.json"
    write_taskspec(spec_path, taskspec)

    rc, out, err = run_cli("validate-taskspec", spec_path, cwd=workdir)

    assert rc == 0
    assert "TaskSpec is valid" in out
    assert err == ""


def test_validate_taskspec_failure(workdir):
    """Invalid TaskSpec should fail validation and report errors."""
    taskspec = create_valid_function_taskspec()
    # Remove required outbox to trigger validation failure
    taskspec.io.outputs.pop("outbox", None)

    spec_path = workdir / "invalid_taskspec.json"
    write_taskspec(spec_path, taskspec)

    rc, out, err = run_cli("validate-taskspec", spec_path, cwd=workdir)

    assert rc != 0
    assert "TaskSpec validation failed" in out
    assert "outbox" in out
