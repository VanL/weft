"""A relocated SQLite project skips foreign retained identities [MA-1.8]."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from tests.conftest import run_cli

pytestmark = [pytest.mark.sqlite_only]


def test_run_succeeds_in_a_relocated_copy_of_a_used_project(tmp_path: Path) -> None:
    original, relocated = tmp_path / "original", tmp_path / "relocated"
    original.mkdir()
    try:
        for args in [
            ("init",),
            ("run", "--", sys.executable, "-c", "print('x')"),
            ("system", "stop"),
        ]:
            rc, out, err = run_cli(*args, cwd=original, stdin="")
            assert rc == 0, (out, err)
        shutil.copytree(original, relocated)
        rc, out, err = run_cli(
            "run",
            "--",
            sys.executable,
            "-c",
            "print('y')",
            cwd=relocated,
            stdin="",
            timeout=40,
        )
        assert rc == 0 and "y" in out, (rc, out, err)
    finally:
        for root in (original, relocated):
            if root.exists():
                run_cli("system", "stop", cwd=root, stdin="")
