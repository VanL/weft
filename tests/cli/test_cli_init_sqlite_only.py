"""SQLite-only coverage for `weft init` default file-backed assumptions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import run_cli

pytestmark = [pytest.mark.sqlite_only]


def test_cli_init_missing_default_db_config(workdir: Path, weft_harness) -> None:
    project_root = workdir / "no-default"
    env = os.environ.copy()
    env["WEFT_DEFAULT_DB_NAME"] = ""

    rc, out, err = run_cli(
        "init",
        project_root,
        cwd=workdir,
        env=env,
        harness=weft_harness,
    )

    assert rc == 1
    assert out == ""
    assert "cannot initialize project" in err
