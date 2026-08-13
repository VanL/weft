"""Project initialisation command.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md (Project Context and Directory Scoping)
- docs/specifications/10-CLI_Interface.md [CLI-1.1] (init)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from simplebroker.commands import cmd_init as sb_cmd_init
from weft._constants import (
    WEFT_BROKER_PROJECT_CONFIG_FILENAME,
    load_config,
)
from weft._exceptions import CommandExecutionError
from weft.commands._boundary import typed_command_errors
from weft.commands.types import InitResult
from weft.context import (
    build_context,
    normalize_backend_resolution_error,
    resolve_context_broker_target,
    update_project_config,
)


def _project_config_path(root: Path, config: Mapping[str, Any]) -> Path:
    """Return the configured SimpleBroker project config path for a Weft root."""

    path_prefix = Path(str(config.get("BROKER_PROJECT_CONFIG_PATH", "")))
    config_name = Path(
        str(
            config.get(
                "BROKER_PROJECT_CONFIG_NAME",
                WEFT_BROKER_PROJECT_CONFIG_FILENAME,
            )
        )
    )
    if path_prefix.is_absolute():
        return path_prefix / config_name
    return root / path_prefix / config_name


def _tighten_existing_project_broker_config(path: Path) -> None:
    """Restrict an existing Weft-owned SimpleBroker project config to 0600."""

    if not path.is_file():
        return
    os.chmod(path, 0o600)


@typed_command_errors
def cmd_init(
    directory: Path | None = None,
    *,
    autostart: bool = True,
) -> InitResult:
    """Initialize a Weft project rooted at *directory*.

    Returns a structured project-initialization outcome.

    Spec: [SB-0] (Project Context and Directory Scoping)
    """
    config = load_config()
    root = Path(directory or Path.cwd()).expanduser().resolve()
    backend_name = str(config.get("BROKER_BACKEND", "sqlite")).strip().lower()
    project_broker_config_path = _project_config_path(root, config)
    if (
        backend_name == "sqlite"
        and not config.get("BROKER_DEFAULT_DB_NAME")
        and not project_broker_config_path.is_file()
    ):
        raise CommandExecutionError(
            "BROKER_DEFAULT_DB_NAME not set in global config; cannot initialize project"
        )
    created = not project_broker_config_path.is_file()
    try:
        _tighten_existing_project_broker_config(project_broker_config_path)
        broker_target = resolve_context_broker_target(root, config=config)
        result = int(sb_cmd_init(broker_target, quiet=True))
    except Exception as exc:
        friendly_exc = normalize_backend_resolution_error(exc)
        raise CommandExecutionError(
            f"failed to initialize SimpleBroker database: {friendly_exc}"
        ) from exc

    if result != 0:
        raise CommandExecutionError(
            f"SimpleBroker initialization failed with exit code {result}"
        )

    context = build_context(
        spec_context=root,
        config=config,
        create_dirs=True,
        create_database=False,
        autostart=autostart,
    )
    update_project_config(context.config_path, {"autostart": autostart})

    return InitResult(root=root, config_path=context.config_path, created=created)
