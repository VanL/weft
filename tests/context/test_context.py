"""Integration tests for the simplified Weft context helpers."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from simplebroker import BrokerTarget, Config
from tests.helpers.test_backend import prepare_project_root

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weft._constants import (  # noqa: E402
    WEFT_CONFIG_DEFAULTS,
    load_config,
    resolve_runtime_config,
)
from weft.context import WeftContext, build_context, service_context_key  # noqa: E402

context_module = sys.modules["weft.context"]

pytestmark = [pytest.mark.shared]


def test_context_exposes_only_build_context_constructor() -> None:
    assert "get_context" not in context_module.__dict__
    assert "get_context" not in context_module.__all__


def _write_broker_project_config(
    root: Path,
    *,
    backend: str,
    target: str,
    schema: str | None = None,
    config_dir: str = ".weft",
    config_name: str = "broker.toml",
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
    config_path = root / config_dir / config_name
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def _clear_backend_part_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for prefix in ("WEFT", "BROKER"):
        for suffix in ("HOST", "PORT", "USER", "PASSWORD", "DATABASE"):
            monkeypatch.delenv(f"{prefix}_BACKEND_{suffix}", raising=False)


def test_service_context_key_strips_non_file_backend_password(tmp_path: Path) -> None:
    target = BrokerTarget(
        backend_name="postgres",
        target="postgresql://weft:s3cr3t@example.test:5432/weft",
        backend_options={"schema": "weft_state"},
    )
    config = load_config()
    ctx = WeftContext(
        root=tmp_path,
        weft_dir=tmp_path / ".weft",
        outputs_dir=tmp_path / ".weft" / "outputs",
        logs_dir=tmp_path / ".weft" / "logs",
        config_path=tmp_path / ".weft" / "config.json",
        broker_target=target,
        database_path=None,
        config=config,
        broker_config=config,
        project_config={},
        discovered=True,
        autostart_dir=tmp_path / ".weft" / "autostart",
        autostart_enabled=True,
    )

    key = service_context_key(ctx)

    assert key.startswith("postgres:")
    assert "s3cr3t" not in key
    assert "example.test" not in key


def test_broker_display_target_redacts_non_file_backend_password(
    tmp_path: Path,
) -> None:
    target = BrokerTarget(
        backend_name="postgres",
        target="postgresql://weft:s3cr3t@example.test:5432/weft",
        backend_options={"schema": "weft_state"},
    )
    config = load_config()
    ctx = WeftContext(
        root=tmp_path,
        weft_dir=tmp_path / ".weft",
        outputs_dir=tmp_path / ".weft" / "outputs",
        logs_dir=tmp_path / ".weft" / "logs",
        config_path=tmp_path / ".weft" / "config.json",
        broker_target=target,
        database_path=None,
        config=config,
        broker_config=config,
        project_config={},
        discovered=True,
        autostart_dir=tmp_path / ".weft" / "autostart",
        autostart_enabled=True,
    )

    assert ctx.broker_target.target == "postgresql://weft:s3cr3t@example.test:5432/weft"
    assert ctx.broker_display_target == "postgresql://weft:***@example.test:5432/weft"


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
    """Project databases are discovered via Weft-scoped SimpleBroker config."""
    root = prepare_project_root(tmp_path)
    root_ctx = build_context(spec_context=root)
    if root_ctx.database_path is not None:
        _write_broker_project_config(
            root,
            backend="sqlite",
            target=root_ctx.database_path.name,
        )
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


def test_build_context_does_not_discover_legacy_sqlite_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A parent SQLite file does not claim a nested working directory."""

    root = (tmp_path / "existing-project").resolve()
    root.mkdir(parents=True)
    _clear_backend_part_env(monkeypatch)
    monkeypatch.delenv("WEFT_BACKEND", raising=False)
    monkeypatch.delenv("WEFT_BACKEND_TARGET", raising=False)
    monkeypatch.delenv("BROKER_BACKEND", raising=False)
    monkeypatch.delenv("BROKER_BACKEND_TARGET", raising=False)
    root_ctx = build_context(spec_context=root)
    nested_dir = root / "nested" / "child"
    nested_dir.mkdir(parents=True)
    original_cwd = Path.cwd()
    try:
        os.chdir(nested_dir)
        discovered_ctx = build_context(create_database=False)
    finally:
        os.chdir(original_cwd)

    assert discovered_ctx.root == nested_dir.resolve()
    assert discovered_ctx.backend_name == "sqlite"
    assert (
        discovered_ctx.database_path == (nested_dir / ".weft" / "broker.db").resolve()
    )
    assert discovered_ctx.broker_target.target != root_ctx.broker_target.target
    assert discovered_ctx.discovered is False


def test_build_context_discovered_config_beats_env_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = (tmp_path / "configured-project").resolve()
    root.mkdir(parents=True)
    _write_broker_project_config(
        root,
        backend="sqlite",
        target="broker.db",
    )
    nested_dir = root / "nested" / "child"
    nested_dir.mkdir(parents=True)
    _clear_backend_part_env(monkeypatch)
    monkeypatch.setenv("WEFT_BACKEND", "postgres")
    monkeypatch.setenv("WEFT_BACKEND_TARGET", "postgresql://env-user@env-host/env-db")
    monkeypatch.setenv("WEFT_BACKEND_SCHEMA", "env_schema")

    original_cwd = Path.cwd()
    try:
        os.chdir(nested_dir)
        discovered_ctx = build_context(create_database=False)
    finally:
        os.chdir(original_cwd)

    assert discovered_ctx.root == root
    assert discovered_ctx.backend_name == "sqlite"
    assert discovered_ctx.database_path == (root / ".weft" / "broker.db").resolve()
    assert discovered_ctx.discovered is True


def test_build_context_absolute_broker_config_keeps_working_directory_as_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_root = (tmp_path / "external-config").resolve()
    broker_config_path = _write_broker_project_config(
        config_root,
        backend="sqlite",
        target="broker.db",
        config_dir="config",
    )
    working_directory = (tmp_path / "working-directory").resolve()
    working_directory.mkdir()
    config = load_config(
        {
            "WEFT_PROJECT_CONFIG_PATH": str(broker_config_path.parent),
            "WEFT_PROJECT_CONFIG_NAME": broker_config_path.name,
        }
    )
    monkeypatch.chdir(working_directory)

    ctx = build_context(
        config=config,
        create_dirs=False,
        create_database=False,
    )

    assert ctx.root == working_directory
    assert ctx.weft_dir == working_directory / ".weft"
    assert ctx.database_path == (broker_config_path.parent / "broker.db").resolve()
    assert ctx.broker_target.config_path == broker_config_path
    assert ctx.broker_target.project_root == working_directory
    assert ctx.discovered is True


def test_environment_resolves_canonical_broker_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """WEFT_* environment variables resolve to typed unprefixed Config values."""
    monkeypatch.setenv("WEFT_BUSY_TIMEOUT", "2500")
    monkeypatch.setenv("WEFT_PROJECT_SCOPE", "1")

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    assert ctx.broker_config["BUSY_TIMEOUT"] == 2500
    assert isinstance(ctx.broker_config["BUSY_TIMEOUT"], int)
    assert ctx.broker_config["PROJECT_SCOPE"] is True
    assert ctx.broker_config["PROJECT_CONFIG_PATH"] == ".weft"
    assert ctx.broker_config["PROJECT_CONFIG_NAME"] == "broker.toml"
    assert ctx.broker_config["AUTO_VACUUM_INTERVAL"] == 100
    assert isinstance(ctx.broker_config["AUTO_VACUUM_INTERVAL"], int)
    assert isinstance(ctx.broker_config, Config)


def test_context_queue_ignores_invalid_ambient_broker_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The Config snapshot survives target, Queue, and broker boundaries."""

    monkeypatch.setenv("BROKER_CACHE_MB", "not-an-integer")
    monkeypatch.setenv("BROKER_DEFAULT_DB_NAME", "../unsafe.db")
    root = prepare_project_root(tmp_path)

    ctx = build_context(spec_context=root)
    queue = ctx.queue("isolated", persistent=True)
    try:
        queue.write("works")
        assert queue.peek_one() == "works"
    finally:
        queue.close()

    with ctx.broker() as broker:
        assert broker.peek_one("isolated", with_timestamps=False) == "works"


def test_project_discovery_ignores_invalid_ambient_broker_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Automatic project discovery receives the same resolved Config."""

    root = prepare_project_root(tmp_path)
    initial = build_context(spec_context=root)
    if initial.database_path is not None:
        _write_broker_project_config(
            root,
            backend="sqlite",
            target=initial.database_path.name,
        )
    monkeypatch.chdir(root)
    monkeypatch.setenv("BROKER_PROJECT_CONFIG_PATH", "../unsafe")
    monkeypatch.setenv("BROKER_CACHE_MB", "not-an-integer")

    ctx = build_context(create_dirs=False, create_database=False)

    assert ctx.root == root.resolve()
    assert ctx.discovered is True


def test_build_context_uses_configured_weft_dir_when_broker_name_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Weft artifact directory is independent from broker db naming."""

    monkeypatch.setenv("WEFT_DIRECTORY_NAME", ".engram")
    monkeypatch.setenv("WEFT_DEFAULT_DB_NAME", ".custom/weft.db")

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    assert ctx.weft_dir == (tmp_path / ".engram").resolve()


def test_build_context_defaults_logs_dir_under_weft_dir(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    assert ctx.logs_dir == (root / ".weft" / "logs").resolve()
    assert ctx.logs_dir.is_dir()


def test_build_context_uses_relative_logs_dir_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("WEFT_LOGS_DIR", "var/weft-logs")
    root = prepare_project_root(tmp_path)

    ctx = build_context(spec_context=root)

    assert ctx.logs_dir == (root / "var" / "weft-logs").resolve()
    assert ctx.logs_dir.is_dir()


def test_build_context_uses_absolute_logs_dir_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_root = tmp_path / "external-logs"
    monkeypatch.setenv("WEFT_LOGS_DIR", str(log_root))
    root = prepare_project_root(tmp_path / "project")

    ctx = build_context(spec_context=root)

    assert ctx.logs_dir == log_root.resolve()
    assert ctx.logs_dir.is_dir()


def test_build_context_accepts_supplied_config_override(tmp_path: Path) -> None:
    """Embedded callers may override the Weft metadata directory in-process."""

    root = prepare_project_root(tmp_path)
    config = load_config({"WEFT_DIRECTORY_NAME": ".engram"})

    ctx = build_context(spec_context=root, config=config, create_database=False)

    assert ctx.root == root.resolve()
    assert ctx.weft_dir == (root / ".engram").resolve()
    assert config["DEFAULT_DB_NAME"] == ".engram/broker.db"
    if ctx.is_file_backed:
        assert ctx.database_path == (root / ".engram" / "broker.db").resolve()
    else:
        assert ctx.database_path is None


def test_build_context_discovers_existing_project_with_custom_weft_directory_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Discovery should honor the configured Weft metadata directory name."""

    monkeypatch.setenv("WEFT_DIRECTORY_NAME", ".engram")

    root = prepare_project_root(tmp_path)
    root_ctx = build_context(spec_context=root)
    if root_ctx.database_path is not None:
        _write_broker_project_config(
            root,
            backend="sqlite",
            target=root_ctx.database_path.name,
            config_dir=".engram",
        )
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


def test_build_context_discovers_custom_weft_directory_project_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("WEFT_DIRECTORY_NAME", ".engram")

    root = prepare_project_root(tmp_path / "custom-config-project")
    _write_broker_project_config(
        root,
        backend="sqlite",
        target="custom.db",
        config_dir=".engram",
    )
    nested_dir = root / "a" / "b"
    nested_dir.mkdir(parents=True)

    original_cwd = Path.cwd()
    try:
        os.chdir(nested_dir)
        ctx = build_context(create_database=False)
    finally:
        os.chdir(original_cwd)

    assert ctx.root == root.resolve()
    assert ctx.weft_dir == (root / ".engram").resolve()
    assert ctx.database_path == (root / ".engram" / "custom.db").resolve()


@pytest.mark.parametrize("content", ["not-json", "[]", "null"])
def test_project_config_rejects_invalid_existing_content_without_modifying_it(
    tmp_path: Path,
    content: str,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    original = content.encode()
    ctx.config_path.write_bytes(original)

    with pytest.raises(ValueError, match=r"config\.json"):
        build_context(spec_context=tmp_path)

    assert ctx.config_path.read_bytes() == original


def test_project_config_rejects_unreadable_existing_file_without_modifying_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    original = ctx.config_path.read_bytes()
    original_read_text = Path.read_text

    def unreadable_config(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if self == ctx.config_path:
            raise PermissionError("config is unreadable")
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", unreadable_config)

    with pytest.raises(ValueError, match=r"config\.json"):
        build_context(spec_context=tmp_path)

    assert ctx.config_path.read_bytes() == original


def test_build_context_preserves_backend_install_error_for_missing_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Postgres backend selection should preserve public backend guidance."""

    def _raise_missing_plugin(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(
            "Requested backend 'postgres' is not available. Install simplebroker-pg."
        )

    monkeypatch.setattr("weft.context.target_for_directory", _raise_missing_plugin)

    with pytest.raises(RuntimeError) as exc_info:
        build_context(spec_context=tmp_path)

    assert "postgres" in str(exc_info.value)
    assert "simplebroker-pg" in str(exc_info.value)
    assert "weft[pg]" not in str(exc_info.value)


def test_build_context_uses_weft_scoped_project_sqlite_target_when_config_exists(
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
    assert (
        ctx.database_path == (root / ".weft" / ".custom" / "from-project.db").resolve()
    )


def test_build_context_uses_weft_scoped_project_postgres_target_when_config_exists(
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
    _clear_backend_part_env(monkeypatch)
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


def test_build_context_discovery_uses_weft_scoped_project_postgres_target(
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
    _clear_backend_part_env(monkeypatch)
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


def test_build_context_ignores_root_simplebroker_config_without_weft_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = (tmp_path / "root-simplebroker-config").resolve()
    root.mkdir(parents=True)
    monkeypatch.delenv("WEFT_BACKEND", raising=False)
    monkeypatch.delenv("WEFT_BACKEND_TARGET", raising=False)
    monkeypatch.delenv("BROKER_BACKEND", raising=False)
    monkeypatch.delenv("BROKER_BACKEND_TARGET", raising=False)
    _write_broker_project_config(
        root,
        backend="sqlite",
        target="root-owned.db",
        config_dir=".",
        config_name=".broker.toml",
    )
    nested = root / "nested"
    nested.mkdir()

    original_cwd = Path.cwd()
    try:
        os.chdir(nested)
        ctx = build_context(create_database=False)
    finally:
        os.chdir(original_cwd)

    assert ctx.backend_name == "sqlite"
    assert ctx.root == nested
    assert ctx.database_path == (nested / ".weft" / "broker.db").resolve()
    assert ctx.broker_target.config_path is None
    assert ctx.discovered is False


def test_build_context_empty_config_ignores_root_simplebroker_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = (tmp_path / "root-simplebroker-config").resolve()
    root.mkdir(parents=True)
    _clear_backend_part_env(monkeypatch)
    monkeypatch.delenv("WEFT_BACKEND", raising=False)
    monkeypatch.delenv("WEFT_BACKEND_TARGET", raising=False)
    monkeypatch.delenv("BROKER_BACKEND", raising=False)
    monkeypatch.delenv("BROKER_BACKEND_TARGET", raising=False)
    _write_broker_project_config(
        root,
        backend="sqlite",
        target="root-owned.db",
        config_dir=".",
        config_name=".broker.toml",
    )
    nested = root / "nested"
    nested.mkdir()

    monkeypatch.chdir(nested)
    ctx = build_context(
        config=load_config({}),
        create_dirs=False,
        create_database=False,
    )

    assert ctx.root == nested
    assert ctx.database_path == (nested / ".weft" / "broker.db").resolve()
    assert ctx.broker_target.config_path is None
    assert ctx.discovered is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_build_context_creates_owner_only_metadata_dirs(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root, autostart=True)
    assert stat.S_IMODE(ctx.weft_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(ctx.outputs_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(ctx.autostart_dir.stat().st_mode) == 0o700
    # Decision under test: the DEFAULT logs dir is protected by its 0700
    # parent rather than its own mode (custom WEFT_LOGS_DIR locations keep
    # caller-owned modes). Pin the placement premise that protection rests on:
    assert ctx.logs_dir == ctx.weft_dir / "logs"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_build_context_tightens_preexisting_loose_weft_dir(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    loose = root / ".weft"
    loose.mkdir(exist_ok=True)
    os.chmod(loose, 0o775)
    build_context(spec_context=root)
    assert stat.S_IMODE(loose.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    ("metadata_dir", "config_dir", "config_name"),
    [
        (".weft", ".weft", "broker.toml"),
        (".engram", ".engram", "broker.toml"),
        (".engram", "configuration", "selected.toml"),
        (".engram", "", "targets/broker.toml"),
    ],
)
@pytest.mark.parametrize("project_scope", [True, False])
def test_build_context_fallback_discovers_its_project_instead_of_cwd_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata_dir: str,
    config_dir: str,
    config_name: str,
    project_scope: bool,
) -> None:
    """The fallback is a discovery anchor, not an explicit project root."""
    project = tmp_path / "fallback-project"
    fallback = project / "nested" / "app"
    fallback.mkdir(parents=True)
    expected_config = _write_broker_project_config(
        project,
        backend="sqlite",
        target="fallback.db",
        config_dir=config_dir,
        config_name=config_name,
    )
    unrelated = tmp_path / "cwd-project"
    _write_broker_project_config(
        unrelated,
        backend="sqlite",
        target="unrelated.db",
        config_dir=config_dir,
        config_name=config_name,
    )
    monkeypatch.chdir(unrelated)

    ctx = build_context(
        fallback_root=fallback,
        config=resolve_runtime_config(
            {
                "DIRECTORY_NAME": metadata_dir,
                "PROJECT_CONFIG_PATH": config_dir,
                "PROJECT_CONFIG_NAME": config_name,
                "PROJECT_SCOPE": project_scope,
            }
        ),
        create_database=False,
    )

    assert ctx.root == project.resolve()
    assert ctx.weft_dir == project.resolve() / metadata_dir
    assert ctx.broker_target.config_path == expected_config.resolve()
    assert ctx.database_path == expected_config.parent.resolve() / "fallback.db"
    assert ctx.discovered is True
    assert not (unrelated / metadata_dir / "config.json").exists()
    assert not (fallback / metadata_dir).exists()


def test_build_context_uses_config_context_without_ancestor_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolved context is an explicit root even inside another project."""
    project = tmp_path / "parent-project"
    _write_broker_project_config(project, backend="sqlite", target="ancestor.db")
    configured_root = project / "explicit-child"
    configured_root.mkdir()
    unrelated = tmp_path / "cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    ctx = build_context(
        config=resolve_runtime_config({"CONTEXT": str(configured_root)}),
        create_database=False,
    )

    assert ctx.root == configured_root.resolve()
    assert ctx.database_path == configured_root.resolve() / ".weft" / "broker.db"
    assert ctx.broker_target.config_path is None
    assert ctx.discovered is False


@pytest.mark.parametrize("explicit", [True, False])
def test_build_context_root_overrides_beat_fallback_and_ambient_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    """The supplied snapshot controls selection even after the environment changes."""
    argument_root = tmp_path / "argument"
    configured_root = tmp_path / "configured"
    fallback = tmp_path / "fallback"
    ambient_root = tmp_path / "ambient"
    for root in (argument_root, configured_root, fallback, ambient_root):
        root.mkdir()
    snapshot = resolve_runtime_config({"CONTEXT": str(configured_root), "CACHE_MB": 17})
    monkeypatch.setenv("WEFT_CONTEXT", str(ambient_root))
    monkeypatch.setenv("WEFT_CACHE_MB", "not-an-integer")
    monkeypatch.setenv("WEFT_TASK_MONITOR_BATCH_SIZE", "not-an-integer")
    monkeypatch.chdir(ambient_root)

    ctx = build_context(
        argument_root if explicit else None,
        fallback_root=fallback,
        config=snapshot,
        create_database=False,
    )

    expected_root = argument_root if explicit else configured_root
    assert ctx.root == expected_root.resolve()
    assert ctx.weft_dir == expected_root.resolve() / ".weft"
    assert ctx.database_path == ctx.weft_dir / "broker.db"
    assert ctx.config["CACHE_MB"] == 17
    assert ctx.config["CONTEXT"] == str(configured_root)
    assert snapshot["CONTEXT"] == str(configured_root)
    assert not (fallback / ".weft").exists()
    assert not (ambient_root / ".weft").exists()


@pytest.mark.parametrize("context_value", [None, ""])
@pytest.mark.parametrize("use_fallback", [True, False])
def test_build_context_absent_context_uses_fallback_or_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context_value: str | None,
    use_fallback: bool,
) -> None:
    """Empty context text has the same root policy as an omitted context."""
    cwd = tmp_path / "cwd"
    fallback = tmp_path / "fallback"
    cwd.mkdir()
    fallback.mkdir()
    monkeypatch.chdir(cwd)

    ctx = build_context(
        fallback_root=fallback if use_fallback else None,
        config=resolve_runtime_config({"CONTEXT": context_value}),
        create_database=False,
    )

    expected_root = fallback if use_fallback else cwd
    assert ctx.root == expected_root.resolve()
    assert ctx.database_path == expected_root.resolve() / ".weft" / "broker.db"
    assert ctx.discovered is False


def test_build_context_snapshot_without_context_ignores_ambient_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Config created before CONTEXT existed remains an authoritative snapshot."""
    values = dict(resolve_runtime_config({"CACHE_MB": 17}))
    values.pop("CONTEXT", None)
    snapshot = Config(
        values,
        prefix="WEFT",
        defaults={
            name: field
            for name, field in WEFT_CONFIG_DEFAULTS.items()
            if name != "CONTEXT"
        },
    )
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    monkeypatch.setenv("WEFT_CONTEXT", str(tmp_path / "wrong-root"))
    monkeypatch.setenv("WEFT_CACHE_MB", "invalid")

    ctx = build_context(fallback_root=fallback, config=snapshot, create_database=False)

    assert ctx.root == fallback.resolve()
    assert ctx.config["CACHE_MB"] == 17
    assert "CONTEXT" not in snapshot
    assert not (tmp_path / "wrong-root").exists()


@pytest.mark.parametrize("spelling", ["relative", "home", "spaces", "whitespace"])
def test_build_context_resolves_config_context_path_at_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    """Config retains path text; construction resolves it without stripping spaces."""
    load_cwd = tmp_path / "load-cwd"
    build_cwd = tmp_path / "build-cwd"
    home = tmp_path / "home"
    for directory in (load_cwd, build_cwd, home):
        directory.mkdir()
    path_text = {
        "relative": "relative-project",
        "home": "~/home-project",
        "spaces": " project with spaces ",
        "whitespace": " ",
    }[spelling]
    expected_root = (
        home / "home-project" if spelling == "home" else build_cwd / path_text
    )
    monkeypatch.chdir(load_cwd)
    monkeypatch.setenv("HOME", str(home))
    snapshot = resolve_runtime_config({"CONTEXT": path_text})
    assert snapshot["CONTEXT"] == path_text
    assert list(load_cwd.iterdir()) == []
    assert list(home.iterdir()) == []
    monkeypatch.chdir(build_cwd)

    ctx = build_context(config=snapshot, create_database=False)

    assert ctx.root == expected_root.resolve()
    assert ctx.weft_dir == expected_root.resolve() / ".weft"
    assert snapshot["CONTEXT"] == path_text


def test_build_context_empty_explicit_argument_still_selects_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The explicit argument uses non-None precedence, unlike empty Config text."""
    monkeypatch.chdir(tmp_path)

    ctx = build_context(
        "",
        fallback_root=tmp_path / "fallback",
        config=resolve_runtime_config({"CONTEXT": str(tmp_path / "configured")}),
        create_database=False,
    )

    assert ctx.root == tmp_path.resolve()
    assert ctx.discovered is False
    assert not (tmp_path / "fallback").exists()
    assert not (tmp_path / "configured").exists()


def test_build_context_fallback_retains_root_with_absolute_broker_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An external broker config chooses the target while the fallback owns artifacts."""
    config_path = _write_broker_project_config(
        tmp_path / "external", backend="sqlite", target="external.db"
    )
    fallback = tmp_path / "application"
    fallback.mkdir()
    unrelated = tmp_path / "cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    ctx = build_context(
        fallback_root=fallback,
        config=resolve_runtime_config({"PROJECT_CONFIG_PATH": str(config_path.parent)}),
        create_database=False,
    )

    assert ctx.root == fallback.resolve()
    assert ctx.weft_dir == fallback.resolve() / ".weft"
    assert ctx.broker_target.project_root == fallback.resolve()
    assert ctx.broker_target.config_path == config_path.resolve()
    assert ctx.database_path == config_path.parent.resolve() / "external.db"
    assert ctx.discovered is True
    assert not (unrelated / ".weft").exists()


@pytest.mark.parametrize("split_connection", [False, True])
@pytest.mark.parametrize("project_config", [False, True])
def test_build_context_fallback_keeps_root_with_postgres_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    split_connection: bool,
    project_config: bool,
) -> None:
    """Backend inputs select the broker, never the artifact root or discovery anchor."""
    fallback = tmp_path / "application"
    fallback.mkdir()
    unrelated = tmp_path / "cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    config_values: dict[str, object] = {
        "BACKEND": "postgres",
        "BACKEND_SCHEMA": "configured_schema",
    }
    if split_connection:
        config_values.update(
            BACKEND_HOST="db.example.test",
            BACKEND_PORT=5544,
            BACKEND_USER="context_user",
            BACKEND_DATABASE="context_database",
        )
        expected_target = (
            "postgresql://context_user@db.example.test:5544/context_database"
        )
    else:
        expected_target = "postgresql://context_user@db.example.test/context_database"
        config_values["BACKEND_TARGET"] = expected_target
    if project_config:
        _write_broker_project_config(
            fallback, backend="sqlite", target="project-wins.db"
        )

    ctx = build_context(
        fallback_root=fallback,
        config=resolve_runtime_config(config_values),
        create_database=False,
    )

    assert ctx.root == fallback.resolve()
    assert ctx.weft_dir == fallback.resolve() / ".weft"
    if project_config:
        assert ctx.backend_name == "sqlite"
        assert ctx.database_path == ctx.weft_dir / "project-wins.db"
        assert not ctx.database_path.exists()
    else:
        assert ctx.backend_name == "postgres"
        assert ctx.broker_target.target == expected_target
        assert ctx.broker_target.backend_options == {"schema": "configured_schema"}
        assert ctx.database_path is None
    assert not (unrelated / ".weft").exists()
