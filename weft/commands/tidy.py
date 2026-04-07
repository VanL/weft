"""Maintenance commands for SimpleBroker databases.

Spec references:
- docs/specifications/10-CLI_Interface.md (system tidy)
"""

from __future__ import annotations

from pathlib import Path

from weft.context import build_context


def cmd_tidy(context_path: Path | None = None) -> tuple[int, str | None]:
    """Run backend-native broker compaction for the active context."""

    context = build_context(spec_context=context_path)
    with context.broker() as broker:
        broker.vacuum(compact=True)

    return 0, f"Tidied {context.broker_display_target}"


__all__ = ["cmd_tidy"]
