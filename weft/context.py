"""Weft context utilities built on SimpleBroker project scoping.

The vNext context design is intentionally small and predictable. Rather than
maintaining a parallel discovery system, Weft now delegates broker discovery to
SimpleBroker's public project API and keeps only the logic that is specific to
Weft (environment overrides, `.weft/` ancillary directories, and project
metadata).

Spec references: docs/specifications/04-SimpleBroker_Integration.md [SB-0], [SB-0.4]
and docs/specifications/03-Worker_Architecture.md [WA-3].

Key behaviours
--------------
* **Environment translation** – We read WEFT_* variables via
  :func:`weft._constants.load_config`, which already maps supported values to
  the corresponding BROKER_* keys.  The returned configuration is embedded in
  the context and reused when constructing `simplebroker.Queue` instances.
* **Broker resolution** – When a task or CLI command does not specify
  `weft_context`, we consult SimpleBroker's public project-scoping API to
  locate an existing broker target. If nothing is found we fall back to the
  current working directory and initialize a fresh default target there.
* **Explicit overrides** – If `weft_context` *is* provided we treat it as the
  authoritative project root, expand the path, and ask SimpleBroker for the
  default target rooted at that directory.
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
configuration, the derived SimpleBroker configuration, and an opaque broker
target object that can be passed straight back to SimpleBroker. The optional
``database_path`` remains available only for file-backed broker operations.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
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

    broker_target: BrokerTarget
    """Opaque SimpleBroker target to pass back into Queue/open_broker."""

    database_path: Path | None
    """Filesystem path for file-backed targets, else ``None``."""

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
        create_database: When True (default) ensure the configured broker
            target is initialized if it does not already exist.
        autostart: Optional override for enabling auto-start TaskSpecs. When
            ``None`` the value is taken from the configuration/environment.

    Returns:
        Fully-populated :class:`WeftContext`.

    Spec: [SB-0], [SB-0.1], [SB-0.4], [WA-3]
    """
    config = dict(load_config())
    root, broker_target, discovered = _resolve_root_and_target(spec_context, config)
    database_path = broker_target.target_path
    weft_dir = (root / ".weft").resolve(strict=False)
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

    broker_config = {
        key: value for key, value in config.items() if key.startswith("BROKER_")
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

    project_config = _load_project_config(config_path)

    return WeftContext(
        root=root,
        weft_dir=weft_dir,
        outputs_dir=outputs_dir,
        logs_dir=logs_dir,
        config_path=config_path,
        broker_target=broker_target,
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


def _resolve_root_and_target(
    spec_context: str | os.PathLike[str] | None,
    config: dict[str, Any],
) -> tuple[Path, BrokerTarget, bool]:
    """Determine the project root and broker target."""
    if spec_context is not None:
        root = Path(spec_context).expanduser().resolve()
        return root, target_for_directory(root, config=config), False

    start_dir = Path.cwd().resolve()
    discovered_target = resolve_broker_target(start_dir, config=config)
    if discovered_target is not None:
        root = discovered_target.project_root or start_dir
        return root, discovered_target, True

    root = start_dir
    return root, target_for_directory(root, config=config), False


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
