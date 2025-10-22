"""Maintenance commands for SimpleBroker databases."""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

from weft.context import build_context
from weft.helpers import simplebroker_vacuum, sqlite_wal_checkpoint_truncate


def cmd_tidy(context_path: Path | None = None) -> Tuple[int, str | None]:
    """Run simplebroker vacuum and WAL checkpoint for the active context."""

    context = build_context(spec_context=context_path)
    database_path = context.database_path

    simplebroker_vacuum(database_path, config=context.broker_config)
    sqlite_wal_checkpoint_truncate(database_path)

    return 0, f"Tidied {database_path}"


__all__ = ["cmd_tidy"]
