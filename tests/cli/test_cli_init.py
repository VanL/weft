"""Black-box coverage for `weft init`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import run_cli
from tests.helpers.test_backend import (
    cleanup_postgres_schema_for_root,
    postgres_env_overrides_for_root,
)
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def test_cli_init_creates_project(workdir: Path, weft_harness) -> None:
    project_root = workdir / "project"

    rc, out, err = run_cli(
        "init",
        project_root,
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert "Initialized Weft project" in out
    assert err == ""

    weft_dir = project_root / ".weft"
    assert weft_dir.is_dir()
    project_context = build_context(spec_context=project_root)
    queue = project_context.queue("init.shared.queue", persistent=True)
    queue.write("payload")
    assert queue.read() == "payload"
    assert (weft_dir / "outputs").is_dir()
    assert (weft_dir / "logs").is_dir()
    assert (weft_dir / "config.json").is_file()


def test_cli_init_quiet_suppresses_output(workdir: Path, weft_harness) -> None:
    project_root = workdir / "quiet-project"

    rc, out, err = run_cli(
        "init",
        project_root,
        "--quiet",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == ""
    assert err == ""


def test_cli_init_existing_project_returns_success(workdir: Path, weft_harness) -> None:
    project_root = workdir / "existing"
    project_root.mkdir()

    rc_first, _, _ = run_cli(
        "init",
        project_root,
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc_first == 0

    rc_second, out_second, err_second = run_cli(
        "init",
        project_root,
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc_second == 0
    assert "Initialized Weft project" in out_second
    assert err_second == ""


@pytest.mark.skipif(
    os.environ.get("BROKER_TEST_BACKEND") != "postgres",
    reason="env-only Postgres init only applies to the PG-backed test run",
)
def test_cli_init_supports_env_only_postgres_configuration(
    monkeypatch: pytest.MonkeyPatch,
    workdir: Path,
    weft_harness,
) -> None:
    project_root = workdir / "env-only-postgres"
    env = os.environ.copy()
    env.update(postgres_env_overrides_for_root(project_root, env=env))

    for key, value in env.items():
        if key.startswith("WEFT_BACKEND") or key in {
            "WEFT_DEFAULT_DB_NAME",
            "BROKER_TEST_BACKEND",
            "SIMPLEBROKER_PG_TEST_DSN",
        }:
            monkeypatch.setenv(key, value)

    try:
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
        assert not (project_root / ".simplebroker.toml").exists()

        project_context = build_context(spec_context=project_root)
        queue = project_context.queue("init.env_only_pg.queue", persistent=True)
        queue.write("payload")
        assert queue.read() == "payload"
    finally:
        cleanup_postgres_schema_for_root(project_root, env=env)
