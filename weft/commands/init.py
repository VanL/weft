"""Project initialisation command.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md (Project Context and Directory Scoping)
- docs/specifications/10-CLI_Interface.md [CLI-1.1] (init)
"""

from __future__ import annotations

import sys
from pathlib import Path

from simplebroker import target_for_directory
from simplebroker.commands import cmd_init as sb_cmd_init
from weft._constants import EXIT_ERROR, EXIT_SUCCESS, load_config
from weft.context import build_context, normalize_backend_resolution_error


def cmd_init(
    directory: Path | None = None, *, quiet: bool = False, autostart: bool = True
) -> int:
    """Initialize a Weft project rooted at *directory*.

    Returns the SimpleBroker exit code.  When successful the project structure
    (``.weft/`` directories, config metadata, database) is ensured.

    Spec: [SB-0] (Project Context and Directory Scoping)
    """
    config = load_config()
    root = Path(directory or Path.cwd()).expanduser().resolve()
    backend_name = str(config.get("BROKER_BACKEND", "sqlite")).strip().lower()
    if (
        backend_name == "sqlite"
        and not config.get("BROKER_DEFAULT_DB_NAME")
        and not (root / ".simplebroker.toml").is_file()
    ):
        if not quiet:
            print(
                "weft: BROKER_DEFAULT_DB_NAME not set in global config; cannot initialize project",
                file=sys.stderr,
            )
        return EXIT_ERROR
    try:
        broker_target = target_for_directory(root, config=config)
        result = int(sb_cmd_init(broker_target, quiet=quiet))
    except Exception as exc:  # pragma: no cover - defensive
        friendly_exc = normalize_backend_resolution_error(exc)
        if not quiet:
            print(
                f"weft: failed to initialize SimpleBroker database: {friendly_exc}",
                file=sys.stderr,
            )
        return 1

    if result != 0:
        return result

    build_context(
        spec_context=root,
        create_dirs=True,
        create_database=False,
        autostart=autostart,
    )

    if not quiet:
        print(f"Initialized Weft project in {root}")

    return EXIT_SUCCESS
