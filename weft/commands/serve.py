"""Foreground manager command for supervisor-managed deployments.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-7]
- docs/specifications/10-CLI_Interface.md [CLI-1.1.2]
"""

from __future__ import annotations

from pathlib import Path

from weft.commands.manager import _serve_manager_foreground
from weft.context import build_context


def serve_command(*, context_path: Path | None = None) -> tuple[int, str | None]:
    context = build_context(context_path)
    return _serve_manager_foreground(context)


__all__ = ["serve_command"]
