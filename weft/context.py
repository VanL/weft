"""Weft context utilities built on SimpleBroker project scoping.

The vNext context design is intentionally small and predictable.  Rather than
maintaining a parallel discovery system, Weft now delegates database discovery
to SimpleBroker's `_find_project_database` helper and keeps only the logic that
is specific to Weft (environment overrides, `.weft/` ancillary directories, and
project metadata).

Spec references: docs/specifications/04-SimpleBroker_Integration.md [SB-0], [SB-0.4]
and docs/specifications/03-Worker_Architecture.md [WA-3].

Key behaviours
--------------
* **Environment translation** – We read WEFT_* variables via
  :func:`weft._constants.load_config`, which already maps supported values to
  the corresponding BROKER_* keys.  The returned configuration is embedded in
  the context and reused when constructing `simplebroker.Queue` instances.
* **Database resolution** – When a task or CLI command does not specify
  `weft_context`, we consult SimpleBroker's project-search helper to locate an
  existing database that matches the configured default filename (typically
  `.weft/broker.db`).  If nothing is found we fall back to the current working
  directory and initialize a fresh database there.
* **Explicit overrides** – If `weft_context` *is* provided we treat it as the
  authoritative project root, expand the path, and place the SimpleBroker
  database inside that directory using the configured relative filename.
* **Ancillary structure** – Regardless of how the root is chosen we ensure
  `.weft/`, `.weft/outputs/`, `.weft/logs/`, and a JSON metadata file exist.
  These directories belong to Weft and never influence SimpleBroker itself.

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
configuration, the derived SimpleBroker configuration, and the absolute path to
the backing SQLite database.  The :meth:`WeftContext.queue` helper constructs
`simplebroker.Queue` instances that are pre-configured with the correct
database path and configuration dictionary—no global environment state is
mutated in the process.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simplebroker import Queue
from simplebroker.db import BrokerDB
from simplebroker.helpers import _find_project_database
from weft._constants import (
    WEFT_AUTOSTART_DIRECTORY_NAME,
    WEFT_AUTOSTART_TASKS_DEFAULT,
    load_config,
)

__all__ = ["WeftContext", "build_context", "get_context"]


@dataclass(frozen=True)
class WeftContext:
    """Resolved context information for a Weft project (Spec: [SB-0], [SB-0.1], [WA-3])."""

    root: Path
    """Project root directory."""

    weft_dir: Path
    """.weft directory used for Weft-specific artefacts."""

    outputs_dir: Path
    """Directory for large-output spillover files."""

    logs_dir: Path
    """Directory for aggregated log files."""

    config_path: Path
    """.weft/config.json path containing project metadata."""

    database_path: Path
    """Absolute path to the SimpleBroker SQLite database."""

    config: dict[str, Any]
    """Complete configuration dictionary (WEFT_* and BROKER_* keys)."""

    broker_config: dict[str, Any]
    """Subset of configuration containing only BROKER_* keys."""

    project_config: dict[str, Any]
    """Project metadata loaded from .weft/config.json (may be empty)."""

    discovered: bool
    """True if the database was found via upward project search."""

    autostart_dir: Path
    """Directory containing auto-start TaskSpec templates."""

    autostart_enabled: bool
    """True when auto-start templates should be considered during manager boot."""

    def queue(self, name: str, *, persistent: bool = False) -> Queue:
        """Create a SimpleBroker queue bound to this context's database (Spec: [SB-0.1], [SB-0.4])."""
        return Queue(
            name,
            db_path=str(self.database_path),
            persistent=persistent,
            config=self.broker_config,
        )


def build_context(
    spec_context: str | os.PathLike[str] | None = None,
    *,
    create_dirs: bool = True,
    create_database: bool = True,
    autostart: bool | None = None,
) -> WeftContext:
    """Construct a :class:`WeftContext` for the requested directory.

    Args:
        spec_context: Optional override for the project root (equivalent to
            TaskSpec.spec.weft_context).  When omitted we discover an existing
            project by searching upward from :func:`Path.cwd`.
        create_dirs: When True (default) ensure `.weft/`, `outputs/`,
            `logs/`, and (when enabled) `autostart/` directories exist.
        create_database: When True (default) create the SQLite database if it
            does not already exist.
        autostart: Optional override for enabling auto-start TaskSpecs. When
            ``None`` the value is taken from the configuration/environment.

    Returns:
        Fully-populated :class:`WeftContext`.

    Spec: [SB-0], [SB-0.1], [SB-0.4], [WA-3]
    """
    config = dict(load_config())
    default_db_name = config.get("BROKER_DEFAULT_DB_NAME", ".weft/broker.db")
    root, database_path, discovered = _resolve_root_and_database(
        spec_context, default_db_name
    )
    weft_dir = _resolve_weft_dir(root, default_db_name)
    outputs_dir = weft_dir / "outputs"
    logs_dir = weft_dir / "logs"
    config_path = weft_dir / "config.json"
    autostart_dir = weft_dir / WEFT_AUTOSTART_DIRECTORY_NAME

    autostart_enabled = (
        autostart
        if autostart is not None
        else bool(config.get("WEFT_AUTOSTART_TASKS", WEFT_AUTOSTART_TASKS_DEFAULT))
    )
    config["WEFT_AUTOSTART_TASKS"] = autostart_enabled
    config["WEFT_AUTOSTART_DIR"] = str(autostart_dir)

    if create_dirs:
        for path in {weft_dir, outputs_dir, logs_dir, database_path.parent}:
            path.mkdir(parents=True, exist_ok=True)
        if autostart_enabled:
            autostart_dir.mkdir(parents=True, exist_ok=True)

    if create_database:
        _ensure_database(database_path)

    project_config = _load_project_config(config_path)

    broker_config = {
        key: value for key, value in config.items() if key.startswith("BROKER_")
    }

    return WeftContext(
        root=root,
        weft_dir=weft_dir,
        outputs_dir=outputs_dir,
        logs_dir=logs_dir,
        config_path=config_path,
        database_path=database_path,
        config=config,
        broker_config=broker_config,
        project_config=project_config,
        discovered=discovered,
        autostart_dir=autostart_dir,
        autostart_enabled=autostart_enabled,
    )


def get_context(spec_context: str | os.PathLike[str] | None = None) -> WeftContext:
    """Convenience wrapper around :func:`build_context` with default options (Spec: [SB-0])."""
    return build_context(spec_context=spec_context)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_root_and_database(
    spec_context: str | os.PathLike[str] | None, default_db_name: str
) -> tuple[Path, Path, bool]:
    """Determine the project root and absolute database path (Spec: [SB-0])."""
    default_rel = Path(default_db_name)

    if spec_context is not None:
        root = Path(spec_context).expanduser().resolve()
        database_path = _compute_database_path(root, default_rel)
        return root, database_path, False

    start_dir = Path.cwd().resolve()
    discovered = False

    try:
        found_db = _find_project_database(str(default_rel), start_dir)
    except ValueError:
        found_db = None

    if found_db:
        root = _root_from_database(found_db, default_rel)
        database_path = found_db.resolve(strict=False)
        discovered = True
    else:
        root = start_dir
        database_path = _compute_database_path(root, default_rel)

    return root, database_path, discovered


def _compute_database_path(root: Path, rel_db: Path) -> Path:
    """Compute the absolute database path for the given root (Spec: [SB-0.1])."""
    if rel_db.is_absolute():
        return rel_db.resolve(strict=False)
    return (root / rel_db).resolve(strict=False)


def _root_from_database(db_path: Path, rel_db: Path) -> Path:
    """Derive the project root from an absolute database path (Spec: [SB-0])."""
    if rel_db.is_absolute():
        return db_path.parent.resolve()

    root = db_path
    for _ in rel_db.parts:
        root = root.parent
    return root.resolve()


def _resolve_weft_dir(root: Path, default_db_name: str) -> Path:
    """Determine the .weft directory for the given root (Spec: [SB-0.1])."""
    rel_db = Path(default_db_name)

    if rel_db.is_absolute():
        return (root / ".weft").resolve(strict=False)

    if rel_db.parts and rel_db.parts[0].startswith("."):
        return (root / rel_db.parts[0]).resolve(strict=False)

    return (root / ".weft").resolve(strict=False)


def _ensure_database(database_path: Path) -> None:
    """Create the SQLite database if it does not already exist (Spec: [SB-0.1])."""
    if database_path.exists():
        return

    database_path.parent.mkdir(parents=True, exist_ok=True)
    with BrokerDB(str(database_path)):
        pass


def _load_project_config(config_path: Path) -> dict[str, Any]:
    """Load project metadata from .weft/config.json, creating defaults if missing (Spec: [SB-0])."""
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
