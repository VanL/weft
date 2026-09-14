"""SQLite-only coverage for `weft init` default file-backed assumptions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import run_cli

pytestmark = [pytest.mark.sqlite_only]


def test_cli_init_rejects_empty_default_db_config(workdir: Path, weft_harness) -> None:
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
    assert "WEFT_DEFAULT_DB_NAME" in err
    assert "Traceback" not in err
    assert not (project_root / ".weft" / "broker.db").exists()


def test_cli_init_rejects_empty_default_db_with_configured_project_file(
    workdir: Path,
    weft_harness,
) -> None:
    project_root = workdir / "project-config"
    config_path = project_root / ".weft" / "broker.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        ('version = 1\nbackend = "sqlite"\ntarget = "broker.db"\n'),
        encoding="utf-8",
    )
    original_config = config_path.read_bytes()
    env = os.environ.copy()
    env["WEFT_DEFAULT_DB_NAME"] = ""

    rc, out, err = run_cli(
        "init",
        project_root,
        cwd=workdir,
        env=env,
        harness=weft_harness,
        prepare_root=False,
    )

    assert rc == 1
    assert out == ""
    assert "WEFT_DEFAULT_DB_NAME" in err
    assert "Traceback" not in err
    assert config_path.read_bytes() == original_config
    assert not (project_root / ".weft" / "broker.db").exists()
