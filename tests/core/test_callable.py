"""Tests for the managed callable utilities."""

from __future__ import annotations

import subprocess
import sys

import pytest

from weft.core.callable import ManagedProcessResult, make_callable


def test_make_callable_executes_command_and_collects_metrics(tmp_path):
    script = "import time; time.sleep(0.01); print('hello world')"
    runner = make_callable([sys.executable, "-c", script], cwd=tmp_path)

    result = runner()

    assert isinstance(result, ManagedProcessResult)
    assert result.success is True
    assert "hello world" in (result.stdout or "")
    assert result.stderr == ""
    assert result.duration >= 0
    assert tuple(result.command) == (sys.executable, "-c", script)


def test_make_callable_merges_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("EXISTING_VAR", "present")
    script = (
        "import os\n"
        "print(os.environ['EXISTING_VAR'])\n"
        "print(os.environ['INJECTED_VAR'])"
    )
    runner = make_callable(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={"INJECTED_VAR": "injected"},
    )

    result = runner()

    assert result.success
    lines = (result.stdout or "").splitlines()
    assert lines[0] == "present"
    assert lines[1] == "injected"


def test_make_callable_check_raises_on_failure(tmp_path):
    runner = make_callable(
        [sys.executable, "-c", "import sys; sys.exit(7)"],
        cwd=tmp_path,
    )

    with pytest.raises(subprocess.CalledProcessError):
        runner(check=True)
