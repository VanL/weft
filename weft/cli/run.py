"""Input decoding and output formatting for ``weft run``.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/11-CLI_Architecture_Crosswalk.md [CLI-X1]
- docs/specifications/14-Python_API_Surfaces.md [PY-2], [PY-4]
"""

from __future__ import annotations

import base64
import json
import threading

import typer

from weft._constants import load_config
from weft.commands import (
    CommandStream,
    RunExecutionResult,
    RunSession,
    RunSpecDescription,
    TaskEvent,
)
from weft.helpers import (
    read_limited_stdin,
    resolve_broker_max_message_size,
    stdin_is_tty,
)


def read_run_stdin() -> str | None:
    """Decode bounded piped stdin before entering the command layer."""

    if stdin_is_tty():
        return None
    maximum = resolve_broker_max_message_size(load_config())
    value = read_limited_stdin(maximum)
    return value if value else None


def render_run_description(
    ctx: typer.Context,
    description: RunSpecDescription,
) -> None:
    """Render dynamic spec metadata after the canonical describe call."""

    usage = description.usage
    marker = "\n\nSpec Help:"
    suffix = usage[usage.index(marker) :] if marker in usage else f"\n\n{usage}"
    typer.echo(f"{ctx.get_help()}{suffix}")


def render_run_result(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-367] exception
    execution: RunExecutionResult,
    *,
    wait: bool,
    json_output: bool,
    verbose: bool,
    suppress_result_value: bool = False,
) -> int:
    """Format one structured run outcome using the historical CLI bytes."""

    if verbose and execution.manager_started_payload is not None:
        typer.echo(json.dumps(execution.manager_started_payload, ensure_ascii=False))
    if verbose and execution.submitted_payload is not None:
        typer.echo(json.dumps(execution.submitted_payload, indent=2))

    if not wait:
        if json_output:
            typer.echo(
                json.dumps(
                    {"tid": execution.tid, "status": "queued"}, ensure_ascii=False
                )
            )
        else:
            typer.echo(execution.tid)
        return 0

    status = execution.status
    result_value = execution.result_value
    error_message = execution.error_message
    if status == "completed":
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "tid": execution.tid,
                        "status": status,
                        "result": result_value,
                    },
                    ensure_ascii=False,
                )
            )
        elif suppress_result_value:
            pass
        elif isinstance(result_value, (dict, list)):
            typer.echo(json.dumps(result_value, ensure_ascii=False))
        elif result_value not in (None, ""):
            typer.echo(str(result_value))
        return 0

    display_error = error_message
    if status == "cancelled":
        display_error = "Task cancelled"
    elif status == "killed":
        display_error = "Task killed"
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "tid": execution.tid,
                    "status": status,
                    "error": display_error,
                },
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(f"{execution.error_prefix}: {display_error}", err=True)
    return 124 if status == "timeout" else 1


def consume_run_session(session: RunSession) -> RunExecutionResult:
    """Wait for and close the session returned by the command API."""

    try:
        return session.wait()
    finally:
        session.close()


def _render_interactive_events(events: CommandStream[TaskEvent]) -> None:
    """Render structured stdout/stderr events without consuming result queues."""

    try:
        for event in events:
            if event.event_type not in {"stdout", "stderr"}:
                continue
            data = str(event.payload.get("data", ""))
            if event.payload.get("encoding") == "base64":
                data = base64.b64decode(data).decode("utf-8", errors="replace")
            if data:
                typer.echo(data, err=event.event_type == "stderr", nl=False)
    finally:
        events.close()


def drive_interactive_session(session: RunSession, stdin_text: str | None) -> None:
    """Drive terminal input while rendering the session's structured events."""

    if stdin_text is not None:
        _render_interactive_events(session.events())
        return
    try:
        # Optional dependency is imported only for the interactive terminal mode.
        from prompt_toolkit import PromptSession
    except ImportError as exc:  # pragma: no cover - optional dependency guard
        raise RuntimeError("prompt_toolkit is required for interactive mode") from exc

    def _stream_output() -> None:
        _render_interactive_events(session.events())

    output_thread = threading.Thread(target=_stream_output, daemon=True)
    output_thread.start()
    prompt: PromptSession[str] = PromptSession()
    try:
        while True:
            try:
                line = prompt.prompt("weft> ")
            except EOFError:
                session.close_input()
                break
            except KeyboardInterrupt:
                continue
            if line.strip() in {":quit", ":exit"}:
                session.stop()
                break
            session.send_input(line if line.endswith("\n") else f"{line}\n")
    finally:
        output_thread.join()


__all__ = [
    "consume_run_session",
    "drive_interactive_session",
    "read_run_stdin",
    "render_run_description",
    "render_run_result",
]
