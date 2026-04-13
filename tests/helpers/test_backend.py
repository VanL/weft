"""Backend-aware test helpers for provisioning Weft project roots."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

from simplebroker.ext import get_backend_plugin

PROJECT_CONFIG_FILENAME = ".simplebroker.toml"
POSTGRES_TEST_BACKEND = "postgres"
_PREPARED_POSTGRES_ROOTS: set[tuple[str, str, str]] = set()


def active_test_backend(env: Mapping[str, str] | None = None) -> str:
    """Return the backend name selected for the current test run."""

    if env and env.get("BROKER_TEST_BACKEND"):
        return env["BROKER_TEST_BACKEND"]
    return os.environ.get("BROKER_TEST_BACKEND", "sqlite")


def postgres_test_dsn(env: Mapping[str, str] | None = None) -> str | None:
    """Return the configured Postgres DSN for PG-backed tests."""

    if env and env.get("SIMPLEBROKER_PG_TEST_DSN"):
        return env["SIMPLEBROKER_PG_TEST_DSN"]
    return os.environ.get("SIMPLEBROKER_PG_TEST_DSN")


def _postgres_schema_name(root: Path) -> str:
    """Derive a stable schema name from an absolute project root."""

    digest = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"weft_pytest_{digest}"


def postgres_schema_for_root(root: Path) -> str:
    """Return the deterministic Postgres schema name for a project root."""

    return _postgres_schema_name(root)


def _project_config_path(root: Path) -> Path:
    return root / PROJECT_CONFIG_FILENAME


def postgres_env_overrides_for_root(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return env overrides for an env-only Postgres-backed Weft project root."""

    dsn = postgres_test_dsn(env)
    if not dsn:
        raise RuntimeError(
            "BROKER_TEST_BACKEND=postgres requires SIMPLEBROKER_PG_TEST_DSN"
        )

    return {
        "BROKER_TEST_BACKEND": POSTGRES_TEST_BACKEND,
        "SIMPLEBROKER_PG_TEST_DSN": dsn,
        "WEFT_BACKEND": POSTGRES_TEST_BACKEND,
        "WEFT_BACKEND_TARGET": dsn,
        "WEFT_BACKEND_SCHEMA": postgres_schema_for_root(root),
        "WEFT_DEFAULT_DB_NAME": "",
    }


def _write_postgres_project_config(root: Path, *, dsn: str, schema: str) -> Path:
    """Write a per-root Postgres project config understood by SimpleBroker."""

    config_path = _project_config_path(root)
    config_path.write_text(
        "\n".join(
            [
                "version = 1",
                'backend = "postgres"',
                f'target = "{dsn}"',
                "",
                "[backend_options]",
                f'schema = "{schema}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _load_postgres_schema(config_path: Path) -> str | None:
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None

    if data.get("backend") != POSTGRES_TEST_BACKEND:
        return None

    backend_options = data.get("backend_options", {})
    if not isinstance(backend_options, dict):
        return None

    schema = backend_options.get("schema")
    if not isinstance(schema, str) or not schema.strip():
        return None
    return schema.strip()


def prepare_project_root(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Prepare an arbitrary project root for the active test backend."""

    resolved_root = root.expanduser().resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)

    if active_test_backend(env) != POSTGRES_TEST_BACKEND:
        return resolved_root

    dsn = postgres_test_dsn(env)
    if not dsn:
        raise RuntimeError(
            "BROKER_TEST_BACKEND=postgres requires SIMPLEBROKER_PG_TEST_DSN"
        )

    config_path = _project_config_path(resolved_root)
    schema = _load_postgres_schema(config_path)
    if schema is None:
        schema = _postgres_schema_name(resolved_root)
        _write_postgres_project_config(resolved_root, dsn=dsn, schema=schema)

    cache_key = (str(resolved_root), dsn, schema)
    if cache_key in _PREPARED_POSTGRES_ROOTS:
        return resolved_root

    get_backend_plugin(POSTGRES_TEST_BACKEND).initialize_target(
        dsn,
        backend_options={"schema": schema},
    )
    _PREPARED_POSTGRES_ROOTS.add(cache_key)
    return resolved_root


def cleanup_prepared_roots(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    """Drop any Postgres schemas provisioned under *root* for a test."""

    if active_test_backend(env) != POSTGRES_TEST_BACKEND:
        return

    dsn = postgres_test_dsn(env)
    if not dsn:
        return

    plugin = get_backend_plugin(POSTGRES_TEST_BACKEND)
    resolved_root = root.expanduser().resolve()
    cleaned_schemas: set[str] = set()
    for config_path in root.rglob(PROJECT_CONFIG_FILENAME):
        schema = _load_postgres_schema(config_path)
        if schema is None or schema in cleaned_schemas:
            continue
        try:
            plugin.cleanup_target(dsn, backend_options={"schema": schema})
        except Exception:
            continue
        cleaned_schemas.add(schema)

    stale_keys = [
        key
        for key in _PREPARED_POSTGRES_ROOTS
        if Path(key[0]).is_relative_to(resolved_root)
    ]
    for key in stale_keys:
        _PREPARED_POSTGRES_ROOTS.discard(key)


def cleanup_postgres_schema_for_root(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    """Drop the deterministic Postgres schema for a root used without project config."""

    if active_test_backend(env) != POSTGRES_TEST_BACKEND:
        return

    dsn = postgres_test_dsn(env)
    if not dsn:
        return

    resolved_root = root.expanduser().resolve()
    schema = postgres_schema_for_root(resolved_root)
    plugin = get_backend_plugin(POSTGRES_TEST_BACKEND)
    try:
        plugin.cleanup_target(
            dsn,
            backend_options={"schema": schema},
        )
    except Exception:
        return
    _PREPARED_POSTGRES_ROOTS.discard((str(resolved_root), dsn, schema))


def _resolve_cli_path(value: str, cwd: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (cwd / path).resolve()


def _taskspec_context_root(path: Path) -> Path | None:
    """Return the explicit ``weft_context`` embedded in a TaskSpec file."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return None

    value = spec.get("weft_context")
    if not isinstance(value, str) or not value.strip():
        return None
    return _resolve_cli_path(value, path.parent)


def cli_context_root(args: Sequence[object], cwd: Path) -> Path:
    """Resolve the project root a CLI invocation will operate against in tests."""

    arg_list = [str(arg) for arg in args]

    for index, arg in enumerate(arg_list):
        if arg.startswith("--context="):
            return _resolve_cli_path(arg.split("=", 1)[1], cwd)
        if arg == "--context" and index + 1 < len(arg_list):
            return _resolve_cli_path(arg_list[index + 1], cwd)

    if len(arg_list) >= 3 and arg_list[0] == "manager" and arg_list[1] == "start":
        spec_path = _resolve_cli_path(arg_list[2], cwd)
        spec_context = _taskspec_context_root(spec_path)
        if spec_context is not None:
            return spec_context

    if arg_list and arg_list[0] == "run":
        for index, arg in enumerate(arg_list[1:], start=1):
            if arg.startswith("--spec="):
                spec_path = _resolve_cli_path(arg.split("=", 1)[1], cwd)
                spec_context = _taskspec_context_root(spec_path)
                if spec_context is not None:
                    return spec_context
                break
            if arg == "--spec" and index + 1 < len(arg_list):
                spec_path = _resolve_cli_path(arg_list[index + 1], cwd)
                spec_context = _taskspec_context_root(spec_path)
                if spec_context is not None:
                    return spec_context
                break

    if arg_list and arg_list[0] == "init":
        for arg in arg_list[1:]:
            if arg.startswith("-"):
                continue
            return _resolve_cli_path(arg, cwd)

    return cwd.resolve()


def prepare_cli_root(
    args: Sequence[object],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Prepare the root used by a CLI subprocess for the active backend."""

    return prepare_project_root(cli_context_root(args, cwd), env=env)


__all__ = [
    "PROJECT_CONFIG_FILENAME",
    "POSTGRES_TEST_BACKEND",
    "active_test_backend",
    "cleanup_postgres_schema_for_root",
    "cleanup_prepared_roots",
    "cli_context_root",
    "postgres_env_overrides_for_root",
    "postgres_schema_for_root",
    "postgres_test_dsn",
    "prepare_cli_root",
    "prepare_project_root",
]
