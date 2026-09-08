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
    MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS,
    WEFT_MANAGER_SERVE_LOG_LEVEL,
    load_config,
)
from weft._exceptions import CommandExecutionError, ManagerStartFailed
from weft.context import WeftContext, build_context
from weft.core import manager_runtime

from ._boundary import typed_command_errors


@typed_command_errors
def cmd_manager_serve(
    *,
    context: Path | None = None,
    level: str | None = None,
    log_interval: float | None = None,
    replace: bool = False,
) -> None:
    """Run the canonical manager in the foreground without process output.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    overrides: dict[str, object] = {MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: True}
    if level is not None:
        overrides[WEFT_MANAGER_SERVE_LOG_LEVEL] = level
    if log_interval is not None:
        overrides[WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS] = log_interval
    try:
        resolved = build_context(context, config=load_config(overrides))
    except (OSError, RuntimeError, ValueError) as exc:
        raise CommandExecutionError(str(exc)) from exc
    _serve_manager_context(resolved, replace=replace)


@typed_command_errors
def _serve_manager_context(context: WeftContext, *, replace: bool = False) -> None:
    """Serve with the caller's resolved broker and configuration [PY-2]."""
    try:
        if replace:
            replaced, message = manager_runtime.replace_active_manager(
                context,
                timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
            )
            if not replaced:
                raise ManagerStartFailed(message or "Manager replacement failed")
        exit_code, message = manager_runtime.serve_manager_foreground(context)
    except ManagerStartFailed:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise CommandExecutionError(str(exc)) from exc
    if exit_code != 0:
        raise CommandExecutionError(message or "Manager foreground runtime failed")


__all__ = ["cmd_manager_serve"]
