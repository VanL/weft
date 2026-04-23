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


def test_cli_init_allows_configured_project_file_without_default_db(
    workdir: Path,
    weft_harness,
) -> None:
    project_root = workdir / "project-config"
    config_path = project_root / ".weft" / "broker.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "\n".join(
            [
                "version = 1",
                'backend = "sqlite"',
                'target = "broker.db"',
                "",
            ]
        ),
        encoding="utf-8",
    )
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

    assert rc == 0
    assert "Initialized Weft project" in out
    assert err == ""
    assert (project_root / ".weft" / "broker.db").is_file()
