"""Project initialisation command."""

from __future__ import annotations

import sys
from pathlib import Path

from simplebroker.commands import cmd_init as sb_cmd_init
from weft._constants import EXIT_ERROR, EXIT_SUCCESS, load_config
from weft.context import build_context


def cmd_init(directory: Path | None = None, *, quiet: bool = False) -> int:
    """Initialize a Weft project rooted at *directory*.

    Returns the SimpleBroker exit code.  When successful the project structure
    (``.weft/`` directories, config metadata, database) is ensured.
    """
    config = load_config()
    default_db_name = config.get("BROKER_DEFAULT_DB_NAME")
    if not default_db_name:
        if not quiet:
            print(
                "weft: BROKER_DEFAULT_DB_NAME not set in global config; cannot initialize project",
                file=sys.stderr,
            )
        return EXIT_ERROR

    root = Path(directory or Path.cwd()).expanduser().resolve()

    db_path = Path(default_db_name)
    if not db_path.is_absolute():
        db_path = (root / db_path).resolve()

    try:
        result = int(sb_cmd_init(str(db_path), quiet=quiet))
    except Exception as exc:  # pragma: no cover - defensive
        if not quiet:
            print(
                f"weft: failed to initialize SimpleBroker database: {exc}",
                file=sys.stderr,
            )
        return 1

    if result != 0:
        return result

    build_context(spec_context=root, create_dirs=True, create_database=False)

    if not quiet:
        print(f"Initialized Weft project in {root}")

    return EXIT_SUCCESS
