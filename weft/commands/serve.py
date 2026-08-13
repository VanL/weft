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
from weft.context import build_context
from weft.core import manager_runtime

from ._boundary import typed_command_errors


def serve_command(
    *,
    context_path: Path | None = None,
    level: str | None = None,
    log_interval: float | None = None,
    replace: bool = False,
    replace_timeout: float = MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
) -> tuple[int, str | None]:
    overrides: dict[str, object] = {MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: True}
    if level is not None:
        overrides[WEFT_MANAGER_SERVE_LOG_LEVEL] = level
    if log_interval is not None:
        overrides[WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS] = log_interval
    config = load_config(overrides)
    context = build_context(context_path, config=config)
    if replace:
        replaced, message = manager_runtime.replace_active_manager(
            context,
            timeout=replace_timeout,
        )
        if not replaced:
            return 1, message or "Manager replacement failed"
    return manager_runtime.serve_manager_foreground(context)


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
        if replace:
            replaced, message = manager_runtime.replace_active_manager(
                resolved,
                timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
            )
            if not replaced:
                raise ManagerStartFailed(message or "Manager replacement failed")
        exit_code, message = manager_runtime.serve_manager_foreground(resolved)
    except ManagerStartFailed:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise CommandExecutionError(str(exc)) from exc
    if exit_code != 0:
        raise CommandExecutionError(message or "Manager foreground runtime failed")


__all__ = ["cmd_manager_serve", "serve_command"]
