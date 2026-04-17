"""Weft context utilities built on SimpleBroker project scoping.

The vNext context design is intentionally small and predictable. Rather than
maintaining a parallel discovery system, Weft now delegates broker discovery to
SimpleBroker's public project API and keeps only the logic that is specific to
Weft (environment overrides, the Weft metadata directory, and project
metadata).

Spec references: docs/specifications/04-SimpleBroker_Integration.md [SB-0], [SB-0.4]
and docs/specifications/03-Manager_Architecture.md [MA-3].

Key behaviours
--------------
* **Environment translation** – We read WEFT_* variables via
  :func:`weft._constants.load_config`, which already maps supported values to
  the corresponding BROKER_* keys.  The returned configuration is embedded in
  the context and reused when constructing `simplebroker.Queue` instances.
* **Broker resolution** – When a task or CLI command does not specify
  `weft_context`, we consult SimpleBroker's public project-scoping API to
  locate an existing broker target. Auto-discovery inherits SimpleBroker's
  ordering: upward `.broker.toml`, then upward legacy sqlite discovery, then
  env-selected non-sqlite backend synthesis. If nothing is found we fall back
  to the current working directory and initialize a fresh default target there.
* **Explicit overrides** – If `weft_context` *is* provided we treat it as the
  authoritative project root, expand the path, and ask SimpleBroker for the
  default target rooted at that directory.
* **Ancillary structure** – Regardless of how the root is chosen we ensure the
  configured Weft metadata directory, its `outputs/` and `logs/` children, and
  a JSON metadata file exist. These directories belong to Weft and never
  influence SimpleBroker itself.

The module exposes a single dataclass, :class:`WeftContext`, plus two helper
functions:

``build_context(spec_context=None, create_dirs=True, create_database=True)``
    Build a new :class:`WeftContext` using either the supplied context override
    or the best project database discovered via SimpleBroker.

``get_context(spec_context=None)``
    Convenience wrapper that simply calls :func:`build_context` with default
    options.  No implicit caching is performed; callers should cache the result
    if they need to reuse it.

The resulting :class:`WeftContext` carries resolved paths, the merged Weft
configuration, the derived SimpleBroker configuration, and an opaque broker
target object that can be passed straight back to SimpleBroker. The optional
``database_path`` remains available only for file-backed broker operations.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simplebroker import (
    BrokerTarget,
    Queue,
    open_broker,
    resolve_broker_target,
    target_for_directory,
)
from weft._constants import (
    POSTGRES_BACKEND_INSTALL_HINT,
    POSTGRES_BACKEND_UNAVAILABLE,
    WEFT_AUTOSTART_DIRECTORY_NAME,
    WEFT_AUTOSTART_TASKS_DEFAULT,
    get_weft_directory_name,
    load_config,
)

__all__ = [
    "POSTGRES_BACKEND_INSTALL_HINT",
    "WeftContext",
    "build_context",
    "find_existing_weft_dir",
    "get_context",
    "normalize_backend_resolution_error",
    "resolve_context_broker_target",
    "update_project_config",
]


@dataclass(frozen=True)
class WeftContext:
    """Resolved context information for a Weft project (Spec: [SB-0], [SB-0.1], [MA-3])."""

    root: Path
    """Project root directory."""

    weft_dir: Path
    """Weft metadata directory used for project-scoped artefacts."""

    outputs_dir: Path
    """Directory for large-output spillover files."""

    logs_dir: Path
    """Directory for aggregated log files."""

    config_path: Path
    """Weft project metadata path containing project-local settings."""

    broker_target: BrokerTarget
    """Opaque SimpleBroker target to pass back into Queue/open_broker."""

    database_path: Path | None
    """Filesystem path for file-backed targets, else ``None``."""

    config: dict[str, Any]
    """Complete configuration dictionary (WEFT_* and BROKER_* keys)."""

    broker_config: dict[str, Any]
    """Subset of configuration containing only BROKER_* keys."""

    project_config: dict[str, Any]
    """Project metadata loaded from the Weft project config file (may be empty)."""

    discovered: bool
    """True if the database was found via upward project search."""

    autostart_dir: Path
    """Directory containing auto-start TaskSpec templates."""

    autostart_enabled: bool
    """True when auto-start templates should be considered during manager boot."""

    def queue(self, name: str, *, persistent: bool = False) -> Queue:
        """Create a SimpleBroker queue bound to this context's broker target."""
        return Queue(
            name,
            db_path=self.broker_target,
            persistent=persistent,
            config=self.broker_config,
        )

    @contextmanager
    def broker(self) -> Iterator[Any]:
        """Yield a backend-agnostic SimpleBroker connection bound to this context."""

        with open_broker(self.broker_target, config=self.broker_config) as broker:
            yield broker

    @property
    def broker_display_target(self) -> str:
        """Return a human-readable target string for logs and CLI output."""

        return self.broker_target.target

    @property
    def backend_name(self) -> str:
        """Return the resolved SimpleBroker backend name (Spec: [SB-0.4])."""

        return self.broker_target.backend_name

    @property
    def is_file_backed(self) -> bool:
        """Whether the resolved broker target is backed by a filesystem path."""

        return self.database_path is not None


def build_context(
    spec_context: str | os.PathLike[str] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
    create_dirs: bool = True,
    create_database: bool = True,
    autostart: bool | None = None,
) -> WeftContext:
    """Construct a :class:`WeftContext` for the requested directory.

    Args:
        spec_context: Optional override for the project root (equivalent to
            TaskSpec.spec.weft_context).  When omitted we discover an existing
            project by searching upward from :func:`Path.cwd`.
        config: Optional preloaded Weft config mapping. When omitted, the
            current process environment is loaded through `load_config()`.
        create_dirs: When True (default) ensure the configured Weft metadata
            directory plus `outputs/`, `logs/`, and (when enabled)
            `autostart/` directories exist.
        create_database: When True (default) ensure the configured broker
            target is initialized if it does not already exist.
        autostart: Optional override for enabling auto-start TaskSpecs. When
            ``None`` the value is taken from the configuration/environment.

    Returns:
        Fully-populated :class:`WeftContext`.

    Spec: [SB-0], [SB-0.1], [SB-0.4], [MA-3]
    """
    resolved_config = dict(config) if config is not None else dict(load_config())
    root, broker_target, discovered = _resolve_root_and_target(
        spec_context,
        resolved_config,
    )
    database_path = broker_target.target_path
    weft_dir_name = get_weft_directory_name(resolved_config)
    weft_dir = (root / weft_dir_name).resolve(strict=False)
    outputs_dir = weft_dir / "outputs"
    logs_dir = weft_dir / "logs"
    config_path = weft_dir / "config.json"
    autostart_dir = weft_dir / WEFT_AUTOSTART_DIRECTORY_NAME
    project_config = _load_project_config(config_path)
    project_autostart = _project_autostart_default(project_config)

    autostart_enabled = (
        autostart
        if autostart is not None
        else (
            project_autostart
            if project_autostart is not None
            else bool(
                resolved_config.get(
                    "WEFT_AUTOSTART_TASKS",
                    WEFT_AUTOSTART_TASKS_DEFAULT,
                )
            )
        )
    )
    resolved_config["WEFT_AUTOSTART_TASKS"] = autostart_enabled
    resolved_config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    broker_config = {
        key: value
        for key, value in resolved_config.items()
        if key.startswith("BROKER_")
    }

    if create_dirs:
        paths = {weft_dir, outputs_dir, logs_dir}
        if database_path is not None:
            paths.add(database_path.parent)
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
        if autostart_enabled:
            autostart_dir.mkdir(parents=True, exist_ok=True)

    if create_database:
        _ensure_database(broker_target, broker_config, database_path=database_path)

    return WeftContext(
        root=root,
        weft_dir=weft_dir,
        outputs_dir=outputs_dir,
        logs_dir=logs_dir,
        config_path=config_path,
        broker_target=broker_target,
        database_path=database_path,
        config=resolved_config,
        broker_config=broker_config,
        project_config=project_config,
        discovered=discovered,
        autostart_dir=autostart_dir,
        autostart_enabled=autostart_enabled,
    )


def get_context(
    spec_context: str | os.PathLike[str] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
) -> WeftContext:
    """Convenience wrapper around :func:`build_context` with default options (Spec: [SB-0])."""
    return build_context(spec_context=spec_context, config=config)


def resolve_context_broker_target(
    root: str | os.PathLike[str],
    *,
    config: Mapping[str, Any],
) -> BrokerTarget:
    """Resolve the broker target for an explicit project root.

    This delegates directly to SimpleBroker's explicit-root helper, which
    applies the authoritative project-config resolution contract for
    `.broker.toml`, env-selected backends, and SQLite fallback.
    """

    resolved_root = Path(root).expanduser().resolve()
    return target_for_directory(resolved_root, config=dict(config))


def find_existing_weft_dir(
    spec_context: str | os.PathLike[str] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
) -> Path | None:
    """Return the nearest existing Weft metadata directory without materializing state.

    Args:
        spec_context: Optional directory override to search from. When omitted,
            the current working directory is used.

    Returns:
        Path to the nearest existing Weft metadata directory, or ``None`` when the
        search root is not inside an initialized Weft project.

    Spec: [SB-0]
    """
    start = Path(spec_context) if spec_context is not None else Path.cwd()
    current = start.expanduser().resolve(strict=False)
    weft_dir_name = get_weft_directory_name(config)
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        weft_dir = candidate / weft_dir_name
        if weft_dir.is_dir():
            return weft_dir
    return None


def normalize_backend_resolution_error(exc: Exception) -> Exception:
    """Rewrite backend-plugin resolution failures into user-facing Weft guidance."""

    message = str(exc).strip()
    if message in {
        "Unknown backend plugin: postgres",
        POSTGRES_BACKEND_UNAVAILABLE,
    }:
        return RuntimeError(POSTGRES_BACKEND_INSTALL_HINT)
    return exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_root_and_target(
    spec_context: str | os.PathLike[str] | None,
    config: dict[str, Any],
) -> tuple[Path, BrokerTarget, bool]:
    """Determine the project root and broker target."""
    if spec_context is not None:
        root = Path(spec_context).expanduser().resolve()
        try:
            return root, resolve_context_broker_target(root, config=config), False
        except RuntimeError as exc:
            raise normalize_backend_resolution_error(exc) from exc

    start_dir = Path.cwd().resolve()
    try:
        discovered_target = resolve_broker_target(start_dir, config=config)
    except RuntimeError as exc:
        raise normalize_backend_resolution_error(exc) from exc
    if discovered_target is not None:
        root = discovered_target.project_root or start_dir
        return root, discovered_target, True

    root = start_dir
    try:
        return root, resolve_context_broker_target(root, config=config), False
    except RuntimeError as exc:
        raise normalize_backend_resolution_error(exc) from exc


def _ensure_database(
    broker_target: BrokerTarget,
    broker_config: dict[str, Any],
    *,
    database_path: Path | None,
) -> None:
    """Ensure the broker target exists."""
    if database_path is not None and database_path.exists():
        return

    if database_path is not None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
    with open_broker(broker_target, config=broker_config):
        pass


def _load_project_config(config_path: Path) -> dict[str, Any]:
    """Load project metadata from the Weft config file, creating defaults if missing (Spec: [SB-0])."""
    if not config_path.exists():
        payload = {
            "version": "1.0",
            "project_name": config_path.parent.parent.name,
            "created": time.time_ns(),
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    try:
        text = config_path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass

    # Corrupted or unreadable config; fall back to defaults
    fallback = {
        "version": "1.0",
        "project_name": config_path.parent.parent.name,
        "created": time.time_ns(),
        "notes": "Auto-generated after config parse failure",
    }
    config_path.write_text(json.dumps(fallback, indent=2), encoding="utf-8")
    return fallback


def _project_autostart_default(project_config: Mapping[str, Any]) -> bool | None:
    value = project_config.get("autostart")
    return value if isinstance(value, bool) else None


def update_project_config(
    config_path: Path,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist Weft-owned project metadata updates in the Weft config file.

    Spec: [SB-0], [CLI-1.1]
    """

    payload = _load_project_config(config_path)
    for key, value in updates.items():
        payload[key] = value
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
