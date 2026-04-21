"""Typer adapter for `weft run`.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/11-CLI_Architecture_Crosswalk.md [CLI-X1]
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import typer

from weft.commands import run as run_cmd


def _translate_run_error(exc: Exception) -> Exception:
    if isinstance(exc, run_cmd.RunUsageError):
        return typer.BadParameter(str(exc), param_hint=exc.param_hint)
    if isinstance(exc, run_cmd.RunResolutionError):
        return typer.Exit(code=2)
    return exc


def render_spec_aware_run_help(
    ctx: typer.Context,
    *,
    spec: str | Path,
    context_dir: Path | None,
) -> str:
    try:
        return run_cmd.render_spec_aware_run_help(
            ctx.get_help(),
            spec=spec,
            context_dir=context_dir,
        )
    except Exception as exc:
        raise _translate_run_error(exc) from exc


def cmd_run(
    command: Sequence[str],
    *,
    spec_run_args: Sequence[str],
    spec: str | Path | None,
    pipeline: str | Path | None,
    pipeline_input: str | None,
    function: str | None,
    args: Sequence[str],
    kwargs: Sequence[str],
    env: Sequence[str],
    name: str | None,
    interactive: bool,
    stream_output: bool | None,
    timeout: float | None,
    memory: int | None,
    cpu: int | None,
    tags: Sequence[str],
    context_dir: Path | None,
    wait: bool,
    json_output: bool,
    verbose: bool,
    monitor: bool,
    persistent_override: bool | None,
    autostart_enabled: bool,
) -> int:
    try:
        execution = run_cmd.execute_run(
            command,
            spec_run_args=spec_run_args,
            spec=spec,
            pipeline=pipeline,
            pipeline_input=pipeline_input,
            function=function,
            args=args,
            kwargs=kwargs,
            env=env,
            name=name,
            interactive=interactive,
            stream_output=stream_output,
            timeout=timeout,
            memory=memory,
            cpu=cpu,
            tags=tags,
            context_dir=context_dir,
            wait=wait,
            json_output=json_output,
            verbose=verbose,
            monitor=monitor,
            persistent_override=persistent_override,
            autostart_enabled=autostart_enabled,
        )
        return run_cmd.render_run_execution_result(
            execution,
            wait=wait,
            json_output=json_output,
            verbose=verbose,
        )
    except Exception as exc:
        raise _translate_run_error(exc) from exc


__all__ = ["cmd_run", "render_spec_aware_run_help"]
