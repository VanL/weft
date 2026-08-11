"""Maintenance commands for SimpleBroker databases.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-6]
"""

from __future__ import annotations

from pathlib import Path

from weft.commands.types import SystemTidyResult
from weft.context import WeftContext, build_context


def cmd_tidy(context_path: Path | None = None) -> tuple[int, str | None]:
    """Run backend-native broker compaction for the active context."""

    context = build_context(spec_context=context_path)
    with context.broker() as broker:
        broker.vacuum(compact=True)

    return 0, f"Tidied {context.broker_display_target}"


def tidy_system(context: WeftContext) -> SystemTidyResult:
    """Run broker compaction and return the broker display target."""

    exit_code, message = cmd_tidy(context.root)
    if exit_code != 0:
        raise RuntimeError(message or "weft tidy failed")
    return SystemTidyResult(target=context.broker_display_target)


__all__ = ["cmd_tidy", "tidy_system"]
