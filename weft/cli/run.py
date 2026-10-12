"""Input decoding and output formatting for ``weft run``.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/11-CLI_Architecture_Crosswalk.md [CLI-X1]
- docs/specifications/14-Python_API_Surfaces.md [PY-2], [PY-4]
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, cast

import typer

from weft._constants import INTERACTIVE_OUTPUT_DRAIN_TIMEOUT, load_config
from weft.commands import (
    CommandStream,
    RunExecutionResult,
    RunSession,
    RunSpecDescription,
    TaskEvent,
)
from weft.commands._resources import command_resource_scope
from weft.commands._streaming import decode_stream_text
from weft.commands.run import _LiveRunSession, _stop_interactive_run_session
from weft.helpers import (
    read_limited_stdin,
    resolve_broker_max_message_size,
    stdin_is_tty,
)

logger = logging.getLogger(__name__)


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
    if execution.availability_warning is not None:
        typer.echo(execution.availability_warning, err=True)

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


def consume_run_session(
    session: RunSession,
    *,
    interactive: bool = False,
    stdin_text: str | None = None,
) -> RunExecutionResult:
    """Own CLI input/output driving, waiting and session cleanup [CLI-1.1.1]."""

    with command_resource_scope() as resources:
        resources.callback(session.close)
        if interactive:
            drive_interactive_session(session, stdin_text)
        return session.wait()


def _render_interactive_events(events: CommandStream[TaskEvent]) -> None:
    """Render structured stdout/stderr events without consuming result queues."""

    with command_resource_scope() as resources:
        resources.callback(events.close)
        for event in events:
            if event.event_type not in {"stdout", "stderr"}:
                continue
            data = decode_stream_text(event.payload)
            if data:
                typer.echo(data, err=event.event_type == "stderr", nl=False)


def _wake_interactive_prompt(prompt: Any) -> None:
    """Wake only the prompt UI when its existing output owner settles."""
    loop = prompt.app.loop
    if loop is not None and not loop.is_closed():
        try:
            loop.call_soon_threadsafe(_exit_settled_prompt, prompt)
        except RuntimeError:
            logger.debug("Interactive prompt loop already closed", exc_info=True)


def _exit_settled_prompt(prompt: Any) -> None:
    if prompt.app.is_running and not prompt.app.is_done:
        prompt.app.exit(result=None)


def _close_interactive_output(
    session: _LiveRunSession,
    output_thread: threading.Thread,
    *,
    drain: bool,
) -> None:
    """Allow one best-effort final-frame drain, then wake owner-thread closure."""
    if drain:
        # Frames published beyond this existing deadline can be missed.
        output_thread.join(timeout=INTERACTIVE_OUTPUT_DRAIN_TIMEOUT)
    session._cancel_output()
    output_thread.join()


def _stream_interactive_output(
    session: RunSession,
    prompt: Any,
    done: threading.Event,
    errors: list[BaseException],
) -> None:
    try:
        _render_interactive_events(session.events())
    except BaseException as exc:  # Transfer worker failure to its waiting caller.
        logger.debug("Interactive output failed", exc_info=True)
        errors.append(exc)
    finally:
        done.set()
        _wake_interactive_prompt(prompt)


def _prompt_interactive_input(prompt: Any, done: threading.Event) -> str | None:
    def before_prompt() -> None:
        if done.is_set():
            _exit_settled_prompt(prompt)

    return cast(str | None, prompt.prompt("weft> ", pre_run=before_prompt))


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

    prompt: PromptSession[str] = PromptSession()
    done = threading.Event()
    errors: list[BaseException] = []
    live_session = cast(_LiveRunSession, session)

    output_thread = threading.Thread(
        target=_stream_interactive_output,
        args=(session, prompt, done, errors),
        daemon=True,
    )
    drain = False
    with command_resource_scope() as resources:
        output_thread.start()
        resources.callback(
            lambda: _close_interactive_output(live_session, output_thread, drain=drain)
        )
        while True:
            try:
                line = _prompt_interactive_input(prompt, done)
            except EOFError:
                session.close_input()
                # EOF closes input and retains the legitimate result wait.
                output_thread.join()
                break
            except KeyboardInterrupt:
                continue
            if line is None:
                break
            if line.strip() in {":quit", ":exit"}:
                _stop_interactive_run_session(live_session)
                drain = True
                break
            session.send_input(line if line.endswith("\n") else f"{line}\n")
    if errors:
        raise errors[0]


__all__ = [
    "consume_run_session",
    "drive_interactive_session",
    "read_run_stdin",
    "render_run_description",
    "render_run_result",
]
