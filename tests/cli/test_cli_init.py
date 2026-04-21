"""Black-box coverage for `weft init`."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.conftest import run_cli
from tests.helpers.test_backend import (
    cleanup_postgres_schema_for_root,
    postgres_env_overrides_for_root,
)
from weft.commands.init import cmd_init
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def _write_broker_project_config(
    root: Path,
    *,
    backend: str,
    target: str,
    schema: str | None = None,
) -> Path:
    lines = [
        "version = 1",
        f'backend = "{backend}"',
        f'target = "{target}"',
        "",
    ]
    if schema is not None:
        lines.extend(
            [
                "[backend_options]",
                f'schema = "{schema}"',
                "",
            ]
        )
    config_path = root / ".broker.toml"
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


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


def test_cli_init_honors_weft_directory_name_env(
    monkeypatch: pytest.MonkeyPatch,
    workdir: Path,
    weft_harness,
) -> None:
    project_root = workdir / "custom-weft-dir-project"
    env = os.environ.copy()
    env["WEFT_DIRECTORY_NAME"] = ".engram"
    monkeypatch.setenv("WEFT_DIRECTORY_NAME", ".engram")

    rc, out, err = run_cli(
        "init",
        project_root,
        cwd=workdir,
        env=env,
        harness=weft_harness,
    )

    assert rc == 0
    assert "Initialized Weft project" in out
    assert err == ""

    weft_dir = project_root / ".engram"
    assert weft_dir.is_dir()

    project_context = build_context(spec_context=project_root)
    assert project_context.weft_dir == weft_dir.resolve()
    assert (weft_dir / "outputs").is_dir()
    assert (weft_dir / "logs").is_dir()
    assert (weft_dir / "config.json").is_file()


def test_cmd_init_accepts_in_process_config_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Embedded callers can initialize without mutating process env."""

    project_root = tmp_path / "embedded-project"
    monkeypatch.delenv("WEFT_DIRECTORY_NAME", raising=False)

    rc = cmd_init(
        project_root,
        quiet=True,
        overrides={"WEFT_DIRECTORY_NAME": ".engram"},
    )

    assert rc == 0
    assert os.environ.get("WEFT_DIRECTORY_NAME") is None
    weft_dir = project_root / ".engram"
    assert weft_dir.is_dir()
    assert not (project_root / ".weft").exists()
    assert (weft_dir / "outputs").is_dir()
    assert (weft_dir / "logs").is_dir()
    assert (weft_dir / "config.json").is_file()


def test_cli_init_defaults_to_current_directory(workdir: Path, weft_harness) -> None:
    project_root = workdir / "cwd-project"
    project_root.mkdir()

    rc, out, err = run_cli(
        "init",
        cwd=project_root,
        harness=weft_harness,
        prepare_root=False,
    )

    assert rc == 0
    assert str(project_root.resolve()) in out
    assert err == ""
    assert (project_root / ".weft").is_dir()


def test_cli_init_help_describes_positional_directory(
    workdir: Path, weft_harness
) -> None:
    rc, out, err = run_cli(
        "init",
        "--help",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    assert "[DIRECTORY]" in out
    assert "Directory where the project should be initialized" in out
    assert "[default:" in out
    assert "--context" not in out


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


def test_cli_init_no_autostart_persists_project_default(
    workdir: Path,
    weft_harness,
) -> None:
    project_root = workdir / "no-autostart-project"

    rc, out, err = run_cli(
        "init",
        project_root,
        "--no-autostart",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert "Initialized Weft project" in out
    assert err == ""

    project_context = build_context(spec_context=project_root)
    assert project_context.autostart_enabled is False
    assert not project_context.autostart_dir.exists()

    config = json.loads(project_context.config_path.read_text(encoding="utf-8"))
    assert config["autostart"] is False


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
        assert not (project_root / ".broker.toml").exists()

        project_context = build_context(spec_context=project_root)
        queue = project_context.queue("init.env_only_pg.queue", persistent=True)
        queue.write("payload")
        assert queue.read() == "payload"
    finally:
        cleanup_postgres_schema_for_root(project_root, env=env)


def test_cmd_init_reports_weft_pg_install_hint_for_missing_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Init should print a Weft-specific install hint when PG support is absent."""

    def _raise_missing_plugin(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(
            "Requested backend 'postgres' is not available. Install simplebroker-pg."
        )

    monkeypatch.setattr(
        "weft.commands.init.resolve_context_broker_target", _raise_missing_plugin
    )
    monkeypatch.setattr(
        "weft.commands.init.load_config", lambda: {"BROKER_BACKEND": "postgres"}
    )

    rc = cmd_init(tmp_path, quiet=False)
    captured = capsys.readouterr()

    assert rc == 1
    assert "uv add 'weft[pg]'" in captured.err
    assert "simplebroker-pg" in captured.err


def test_cmd_init_prefers_project_postgres_target_over_env_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_broker_project_config(
        project_root,
        backend="postgres",
        target="postgresql://toml-user@toml-host/toml-db",
        schema="toml_schema",
    )
    monkeypatch.setenv("WEFT_BACKEND", "postgres")
    monkeypatch.setenv("WEFT_BACKEND_TARGET", "postgresql://env-user@env-host/env-db")
    monkeypatch.setenv("WEFT_BACKEND_SCHEMA", "env_schema")
    monkeypatch.setenv(
        "BROKER_BACKEND_TARGET",
        "postgresql://raw-user@raw-host/raw-db",
    )
    monkeypatch.setenv("BROKER_BACKEND_SCHEMA", "raw_schema")

    captured: dict[str, object] = {}

    def _fake_init(target, quiet=False):  # type: ignore[no-untyped-def]
        captured["target"] = target
        captured["quiet"] = quiet
        return 0

    monkeypatch.setattr("weft.commands.init.sb_cmd_init", _fake_init)

    rc = cmd_init(project_root, quiet=True)

    assert rc == 0
    broker_target = captured["target"]
    assert broker_target.backend_name == "postgres"
    assert broker_target.target == "postgresql://toml-user@toml-host/toml-db"
    assert broker_target.backend_options == {"schema": "toml_schema"}
