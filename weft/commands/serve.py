"""Foreground manager command for supervisor-managed deployments.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-7]
- docs/specifications/10-CLI_Interface.md [CLI-1.1.2]
"""

from __future__ import annotations

from pathlib import Path

from weft._constants import (
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS,
    WEFT_MANAGER_SERVE_LOG_LEVEL,
    load_config,
)
from weft.commands.manager import _serve_manager_foreground
from weft.context import build_context


def serve_command(
    *,
    context_path: Path | None = None,
    level: str | None = None,
    log_interval: float | None = None,
) -> tuple[int, str | None]:
    overrides: dict[str, object] = {MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: True}
    if level is not None:
        overrides[WEFT_MANAGER_SERVE_LOG_LEVEL] = level
    if log_interval is not None:
        overrides[WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS] = log_interval
    config = load_config(overrides)
    context = build_context(context_path, config=config)
    return _serve_manager_foreground(context)


__all__ = ["serve_command"]
