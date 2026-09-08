"""Maintenance commands for SimpleBroker databases.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-6]
"""

from __future__ import annotations

from pathlib import Path

from simplebroker.ext import BrokerError
from weft._exceptions import CommandExecutionError
from weft.commands.types import SystemTidyResult
from weft.context import WeftContext, build_context

from ._boundary import typed_command_errors


def tidy_system(context: WeftContext) -> SystemTidyResult:
    """Run broker compaction and return the broker display target."""

    return cmd_system_tidy(context=context.root)


@typed_command_errors
def cmd_system_tidy(*, context: Path | None = None) -> SystemTidyResult:
    """Compact the active broker and return its display target.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    try:
        resolved = build_context(spec_context=context)
        with resolved.broker() as broker:
            broker.vacuum(compact=True)
    except (BrokerError, OSError, RuntimeError, ValueError) as exc:
        raise CommandExecutionError(str(exc)) from exc
    return SystemTidyResult(target=resolved.broker_display_target)


__all__ = ["cmd_system_tidy", "tidy_system"]
