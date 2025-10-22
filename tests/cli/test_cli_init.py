"""Black-box coverage for `weft init`."""

from __future__ import annotations

import os

from tests.conftest import run_cli


def test_cli_init_creates_project(workdir):
    project_root = workdir / "project"

    rc, out, err = run_cli("init", project_root, cwd=workdir)

    assert rc == 0
    assert "Initialised Weft project" in out
    assert err == ""

    weft_dir = project_root / ".weft"
    assert weft_dir.is_dir()
    assert (weft_dir / "broker.db").exists()
    assert (weft_dir / "outputs").is_dir()
    assert (weft_dir / "logs").is_dir()
    assert (weft_dir / "config.json").is_file()


def test_cli_init_quiet_suppresses_output(workdir):
    project_root = workdir / "quiet-project"

    rc, out, err = run_cli("init", project_root, "--quiet", cwd=workdir)

    assert rc == 0
    assert out == ""
    assert err == ""


def test_cli_init_existing_project_returns_success(workdir):
    project_root = workdir / "existing"
    project_root.mkdir()

    rc_first, _, _ = run_cli("init", project_root, cwd=workdir)
    assert rc_first == 0

    rc_second, out_second, err_second = run_cli("init", project_root, cwd=workdir)
    assert rc_second == 0
    assert "Initialised Weft project" in out_second
    assert err_second == ""


def test_cli_init_missing_default_db_config(workdir):
    project_root = workdir / "no-default"
    env = os.environ.copy()
    env["WEFT_DEFAULT_DB_NAME"] = ""

    rc, out, err = run_cli("init", project_root, cwd=workdir, env=env)

    assert rc == 1
    assert out == ""
    assert "cannot initialise project" in err
