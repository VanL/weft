"""Integration tests for the simplified Weft context helpers."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weft._constants import compile_config  # noqa: E402
from weft.context import build_context  # noqa: E402

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


def test_build_context_creates_structure(tmp_path: Path) -> None:
    """Building a context for a fresh directory materializes all assets."""
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    assert ctx.root == root.resolve()
    assert ctx.weft_dir == (root / ".weft").resolve()
    assert ctx.weft_dir.is_dir()
    assert ctx.outputs_dir.is_dir()
    assert ctx.logs_dir.is_dir()
    assert ctx.autostart_dir.is_dir()
    assert ctx.autostart_enabled is True
    assert ctx.config_path.is_file()

    metadata = json.loads(ctx.config_path.read_text(encoding="utf-8"))
    assert metadata["project_name"] == ctx.root.name

    queue = ctx.queue("context.test.queue")
    queue.write("payload")
    assert queue.read() == "payload"


def test_build_context_can_disable_autostart(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path / "disable-autostart")

    ctx = build_context(spec_context=root, autostart=False)

    assert ctx.autostart_enabled is False
    assert not ctx.autostart_dir.exists()


def test_build_context_uses_project_config_autostart_default(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path / "project-autostart-default")
    config_path = root / ".weft" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "project_name": root.name,
                "created": time.time_ns(),
                "autostart": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    ctx = build_context(spec_context=root)

    assert ctx.autostart_enabled is False
    assert not ctx.autostart_dir.exists()


def test_build_context_explicit_autostart_override_beats_project_default(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path / "project-autostart-override")
    config_path = root / ".weft" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "project_name": root.name,
                "created": time.time_ns(),
                "autostart": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    ctx = build_context(spec_context=root, autostart=True)

    assert ctx.autostart_enabled is True
    assert ctx.autostart_dir.is_dir()


def test_build_context_discovers_existing_project(tmp_path: Path) -> None:
    """Project databases are discovered via SimpleBroker's project scoping."""
    root = prepare_project_root(tmp_path)
    root_ctx = build_context(spec_context=root)
    nested_dir = tmp_path / "a" / "b" / "c"
    nested_dir.mkdir(parents=True)

    original_cwd = Path.cwd()
    try:
        os.chdir(nested_dir)
        discovered_ctx = build_context()
    finally:
        os.chdir(original_cwd)

    assert discovered_ctx.root == root_ctx.root
    assert discovered_ctx.database_path == root_ctx.database_path
    assert discovered_ctx.broker_target.target == root_ctx.broker_target.target
    assert discovered_ctx.discovered is True


def test_build_context_discovery_prefers_existing_sqlite_project_over_env_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Nested discovery should keep using the local sqlite project before env backends."""

    root = (tmp_path / "existing-project").resolve()
    root.mkdir(parents=True)
    root_ctx = build_context(spec_context=root)
    nested_dir = root / "nested" / "child"
    nested_dir.mkdir(parents=True)
    monkeypatch.setenv("WEFT_BACKEND", "postgres")
    monkeypatch.setenv("WEFT_BACKEND_TARGET", "postgresql://env-user@env-host/env-db")
    monkeypatch.setenv("WEFT_BACKEND_SCHEMA", "env_schema")

    original_cwd = Path.cwd()
    try:
        os.chdir(nested_dir)
        discovered_ctx = build_context(create_database=False)
    finally:
        os.chdir(original_cwd)

    assert discovered_ctx.root == root.resolve()
    assert discovered_ctx.backend_name == "sqlite"
    assert discovered_ctx.database_path == root_ctx.database_path
    assert discovered_ctx.broker_target.target == root_ctx.broker_target.target
    assert discovered_ctx.discovered is True


def test_environment_translation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """WEFT_* environment variables are mapped onto BROKER_* settings."""
    monkeypatch.setenv("WEFT_BUSY_TIMEOUT", "2500")
    monkeypatch.setenv("WEFT_PROJECT_SCOPE", "1")

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    assert ctx.broker_config["BROKER_BUSY_TIMEOUT"] == 2500
    assert isinstance(ctx.broker_config["BROKER_BUSY_TIMEOUT"], int)
    assert ctx.broker_config["BROKER_PROJECT_SCOPE"] is True
    assert ctx.broker_config["BROKER_AUTO_VACUUM_INTERVAL"] == 100
    assert isinstance(ctx.broker_config["BROKER_AUTO_VACUUM_INTERVAL"], int)


def test_build_context_uses_configured_weft_dir_when_broker_name_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Weft artifact directory is independent from broker db naming."""

    monkeypatch.setenv("WEFT_DIRECTORY_NAME", ".engram")
    monkeypatch.setenv("WEFT_DEFAULT_DB_NAME", ".custom/weft.db")

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    assert ctx.weft_dir == (tmp_path / ".engram").resolve()


def test_build_context_accepts_supplied_config_override(tmp_path: Path) -> None:
    """Embedded callers may override the Weft metadata directory in-process."""

    root = prepare_project_root(tmp_path)
    config = compile_config({"WEFT_DIRECTORY_NAME": ".engram"})

    ctx = build_context(spec_context=root, config=config)

    assert ctx.root == root.resolve()
    assert ctx.weft_dir == (root / ".engram").resolve()
    assert ctx.database_path == (root / ".engram" / "broker.db").resolve()


def test_build_context_discovers_existing_project_with_custom_weft_directory_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Discovery should honor the configured Weft metadata directory name."""

    monkeypatch.setenv("WEFT_DIRECTORY_NAME", ".engram")

    root = prepare_project_root(tmp_path)
    root_ctx = build_context(spec_context=root)
    nested_dir = tmp_path / "a" / "b" / "c"
    nested_dir.mkdir(parents=True)

    original_cwd = Path.cwd()
    try:
        os.chdir(nested_dir)
        discovered_ctx = build_context()
    finally:
        os.chdir(original_cwd)

    assert root_ctx.weft_dir == (root / ".engram").resolve()
    assert discovered_ctx.root == root_ctx.root
    assert discovered_ctx.weft_dir == root_ctx.weft_dir
    assert discovered_ctx.database_path == root_ctx.database_path


def test_project_config_recovers_from_corruption(tmp_path: Path) -> None:
    """A corrupt config file is replaced with a fresh default."""
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    ctx.config_path.write_text("not-json", encoding="utf-8")

    refreshed_ctx = build_context(spec_context=tmp_path)
    data = json.loads(refreshed_ctx.config_path.read_text(encoding="utf-8"))
    assert data["project_name"] == tmp_path.name
    assert "created" in data


def test_build_context_reports_weft_pg_install_hint_for_missing_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Postgres backend selection should point users at the Weft extra."""

    def _raise_missing_plugin(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(
            "Requested backend 'postgres' is not available. Install simplebroker-pg."
        )

    monkeypatch.setattr("weft.context.target_for_directory", _raise_missing_plugin)

    with pytest.raises(RuntimeError, match=r"uv add 'weft\[pg\]'"):
        build_context(spec_context=tmp_path)


def test_build_context_uses_project_sqlite_target_when_broker_file_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path / "sqlite-project")
    _write_broker_project_config(
        root,
        backend="sqlite",
        target=".custom/from-project.db",
    )
    monkeypatch.setenv("WEFT_DEFAULT_DB_NAME", ".env/from-env.db")

    ctx = build_context(spec_context=root, create_database=False)

    assert ctx.backend_name == "sqlite"
    assert ctx.database_path == (root / ".custom" / "from-project.db").resolve()


def test_build_context_uses_project_postgres_target_when_broker_file_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path / "postgres-project")
    _write_broker_project_config(
        root,
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

    ctx = build_context(spec_context=root, create_database=False)

    assert ctx.backend_name == "postgres"
    assert ctx.broker_target.target == "postgresql://toml-user@toml-host/toml-db"
    assert ctx.broker_target.backend_options == {"schema": "toml_schema"}


def test_build_context_discovery_uses_project_postgres_target_when_broker_file_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path / "discovered-postgres")
    _write_broker_project_config(
        root,
        backend="postgres",
        target="postgresql://toml-user@toml-host/toml-db",
        schema="toml_schema",
    )
    nested_dir = root / "a" / "b"
    nested_dir.mkdir(parents=True)
    monkeypatch.setenv("WEFT_BACKEND", "postgres")
    monkeypatch.setenv("WEFT_BACKEND_TARGET", "postgresql://env-user@env-host/env-db")
    monkeypatch.setenv("WEFT_BACKEND_SCHEMA", "env_schema")
    monkeypatch.setenv(
        "BROKER_BACKEND_TARGET",
        "postgresql://raw-user@raw-host/raw-db",
    )
    monkeypatch.setenv("BROKER_BACKEND_SCHEMA", "raw_schema")

    original_cwd = Path.cwd()
    try:
        os.chdir(nested_dir)
        ctx = build_context(create_database=False)
    finally:
        os.chdir(original_cwd)

    assert ctx.root == root.resolve()
    assert ctx.backend_name == "postgres"
    assert ctx.broker_target.target == "postgresql://toml-user@toml-host/toml-db"
    assert ctx.broker_target.backend_options == {"schema": "toml_schema"}
