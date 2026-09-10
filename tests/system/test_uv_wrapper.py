"""Repo-local uv dispatch preserves child command discovery [TS-0]."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.shared,
    pytest.mark.skipif(os.name == "nt", reason="Bash wrapper"),
]


def test_uv_wrapper_preserves_repo_bin_for_child_commands(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    bin_dir = repo / "bin"
    bin_dir.mkdir(parents=True)
    wrapper = bin_dir / "uv"
    shutil.copy2(Path(__file__).resolve().parents[2] / "bin" / "uv", wrapper)
    tools = tmp_path / "tools"
    tools.mkdir()
    actual_uv = tools / "uv"
    actual_uv.write_text('#!/bin/sh\nshift\nexec "$@"\n', encoding="utf-8")
    actual_uv.chmod(0o755)
    child = bin_dir / "repo-command"
    child.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$UV_PROJECT_ENVIRONMENT" "$1" "$PATH"\n',
        encoding="utf-8",
    )
    child.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(bin_dir), str(tools), env["PATH"]])
    result = subprocess.run(
        [str(wrapper), "run", "repo-command", "argument with spaces"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        str(repo / ".venv"),
        "argument with spaces",
        env["PATH"],
    ]
