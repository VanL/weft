"""Black-box CLI tests for `weft validate-taskspec`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import run_cli  # re-exported for clarity
from tests.taskspec.fixtures import (
    create_valid_agent_taskspec,
    create_valid_function_taskspec,
)

pytestmark = [pytest.mark.shared]


def write_taskspec(path: Path, spec: Any) -> None:
    if isinstance(spec, dict):
        path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return
    path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")


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
    payload = taskspec.model_dump(mode="json")
    # Write an invalid payload rather than mutating a frozen resolved TaskSpec.
    payload["io"]["outputs"].pop("outbox", None)

    spec_path = workdir / "invalid_taskspec.json"
    write_taskspec(spec_path, payload)

    rc, out, err = run_cli("validate-taskspec", spec_path, cwd=workdir)

    assert rc != 0
    assert "TaskSpec validation failed" in out
    assert "outbox" in out


def test_validate_taskspec_agent_summary(workdir):
    taskspec = create_valid_agent_taskspec()
    spec_path = workdir / "agent_taskspec.json"
    write_taskspec(spec_path, taskspec)

    rc, out, err = run_cli("validate-taskspec", spec_path, cwd=workdir)

    assert rc == 0
    assert "TaskSpec is valid" in out
    assert "agent" in out
    assert "llm" in out
    assert "weft-test-agent-model" in out
    assert err == ""
