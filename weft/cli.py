"""Typer entry point for the current Weft CLI surface.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-0], [CLI-0.3], [CLI-1.1]
- docs/specifications/11-CLI_Architecture_Crosswalk.md [CLI-X0], [CLI-X1]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from ._constants import PROG_NAME, __version__
from .commands import cmd_init, cmd_status, cmd_tidy
from .commands import manager as manager_cmd
from .commands import queue as queue_cmd
from .commands import serve as serve_cmd
from .commands import specs as spec_cmd
from .commands import status as status_cmd
from .commands import tasks as task_cmd
from .commands.builtins import cmd_system_builtins
from .commands.dump import cmd_dump
from .commands.load import cmd_load
from .commands.result import cmd_result
from .commands.run import cmd_run, render_spec_aware_run_help
from .commands.validate_taskspec import cmd_validate_taskspec

app = typer.Typer(
    name=PROG_NAME,
    help="Weft: The Multi-Agent Weaving Toolkit",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)

queue_app = typer.Typer(help="Queue passthrough operations")
manager_app = typer.Typer(help="Manager lifecycle management")
spec_app = typer.Typer(help="Spec management")
task_app = typer.Typer(help="Task management")
system_app = typer.Typer(help="System maintenance")


def _emit_queue_result(result: tuple[int, str, str]) -> None:
    """Echo stdout/stderr from queue helpers, then exit."""
    exit_code, stdout, stderr = result
    if stdout:
        typer.echo(stdout)
    if stderr:
        typer.echo(stderr, err=True)
    raise typer.Exit(code=exit_code)


@queue_app.command("read")
def queue_read(
    name: Annotated[str, typer.Argument(help="Queue name to read from")],
    all_messages: Annotated[
        bool,
        typer.Option("--all", help="Read all messages from the queue"),
    ] = False,
    timestamps: Annotated[
        bool,
        typer.Option("--timestamps", help="Include timestamps in output"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
    message_id: Annotated[
        str | None,
        typer.Option("--message", "-m", help="Read specific message by ID"),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Only return messages newer than timestamp"),
    ] = None,
) -> None:
    _emit_queue_result(
        queue_cmd.read_command(
            name,
            all_messages=all_messages,
            with_timestamps=timestamps,
            json_output=json_output,
            message_id=message_id,
            since=since,
        )
    )


@queue_app.command("write")
def queue_write(
    name: Annotated[str, typer.Argument(help="Queue name to write to")],
    message: str | None = typer.Argument(
        None, help="Message to write (omit or use '-' for stdin)"
    ),
) -> None:
    _emit_queue_result(queue_cmd.write_command(name, message))


@queue_app.command("peek")
def queue_peek(
    name: Annotated[str, typer.Argument(help="Queue name to peek")],
    all_messages: Annotated[
        bool,
        typer.Option("--all", help="Peek all messages without removing"),
    ] = False,
    timestamps: Annotated[
        bool,
        typer.Option("--timestamps", help="Include timestamps in output"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
    message_id: Annotated[
        str | None,
        typer.Option("--message", "-m", help="Peek specific message by ID"),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Only return messages newer than timestamp"),
    ] = None,
) -> None:
    _emit_queue_result(
        queue_cmd.peek_command(
            name,
            all_messages=all_messages,
            with_timestamps=timestamps,
            json_output=json_output,
            message_id=message_id,
            since=since,
        )
    )


@queue_app.command("move")
def queue_move(
    source: Annotated[str, typer.Argument(help="Source queue name")],
    destination: Annotated[str, typer.Argument(help="Destination queue name")],
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", help="Maximum number of messages to move"),
    ] = None,
    all_messages: Annotated[
        bool,
        typer.Option("--all", help="Move all available messages"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output moved messages as JSON"),
    ] = False,
    timestamps: Annotated[
        bool,
        typer.Option("--timestamps", help="Include timestamps in output"),
    ] = False,
    message_id: Annotated[
        str | None,
        typer.Option("--message", "-m", help="Move specific message by ID"),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Only move messages newer than timestamp"),
    ] = None,
) -> None:
    _emit_queue_result(
        queue_cmd.move_command(
            source,
            destination,
            limit=limit,
            all_messages=all_messages,
            json_output=json_output,
            with_timestamps=timestamps,
            message_id=message_id,
            since=since,
        )
    )


@queue_app.command("list")
def queue_list(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output queue information as JSON"),
    ] = False,
    stats: Annotated[
        bool,
        typer.Option("--stats", help="Include claimed message statistics"),
    ] = False,
    pattern: Annotated[
        str | None,
        typer.Option(
            "--pattern",
            "-p",
            help="fnmatch-style pattern limiting queues in the result",
        ),
    ] = None,
) -> None:
    _emit_queue_result(
        queue_cmd.list_command(
            json_output=json_output,
            stats=stats,
            pattern=pattern,
        )
    )


@queue_app.command("watch")
def queue_watch(
    name: Annotated[str, typer.Argument(help="Queue name to watch")],
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", help="Stop after this many messages"),
    ] = None,
    interval: Annotated[
        float,
        typer.Option("--interval", help="Polling interval in seconds"),
    ] = 0.5,
    timestamps: Annotated[
        bool,
        typer.Option("--timestamps", help="Include timestamps in output"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output each message as JSON"),
    ] = False,
    peek: Annotated[
        bool,
        typer.Option("--peek", help="Monitor without consuming messages"),
    ] = False,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Start watching after timestamp"),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Suppress startup message"),
    ] = False,
    move_to: Annotated[
        str | None,
        typer.Option("--move", help="Drain messages into another queue"),
    ] = None,
) -> None:
    if peek and move_to is not None:
        raise typer.BadParameter("--peek cannot be used together with --move")

    _emit_queue_result(
        queue_cmd.watch_command(
            name,
            limit=limit,
            interval=interval,
            with_timestamps=timestamps,
            json_output=json_output,
            peek=peek,
            since=since,
            quiet=quiet,
            move_to=move_to,
        )
    )


@queue_app.command("delete")
def queue_delete(
    name: Annotated[
        str | None,
        typer.Argument(help="Queue to delete", show_default=False),
    ] = None,
    all_queues: Annotated[
        bool,
        typer.Option("--all", help="Delete all queues"),
    ] = False,
    message_id: Annotated[
        str | None,
        typer.Option("--message", "-m", help="Delete specific message by ID"),
    ] = None,
) -> None:
    if not all_queues and name is None:
        raise typer.BadParameter("Provide a queue name or use --all", param_hint="name")

    _emit_queue_result(
        queue_cmd.delete_command(
            name,
            delete_all=all_queues,
            message_id=message_id,
        )
    )


@queue_app.command("broadcast")
def queue_broadcast(
    message: Annotated[
        str | None,
        typer.Argument(help="Message to broadcast (omit or use '-' for stdin)"),
    ] = None,
    pattern: Annotated[
        str | None,
        typer.Option(
            "--pattern", "-p", help="fnmatch-style pattern to limit target queues"
        ),
    ] = None,
) -> None:
    _emit_queue_result(queue_cmd.broadcast_command(message, pattern=pattern))


# Alias commands
alias_app = typer.Typer(help="Queue alias management")
queue_app.add_typer(alias_app, name="alias")


@alias_app.command("add")
def alias_add(
    alias: Annotated[str, typer.Argument(help="Alias name")],
    target: Annotated[str, typer.Argument(help="Target queue name")],
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress confirmation output"),
    ] = False,
) -> None:
    _emit_queue_result(queue_cmd.alias_add_command(alias, target, quiet=quiet))


@alias_app.command("list")
def alias_list(
    target: Annotated[
        str | None,
        typer.Option("--target", "-t", help="Show aliases for specific target queue"),
    ] = None,
) -> None:
    _emit_queue_result(queue_cmd.alias_list_command(target=target))


@alias_app.command("remove")
def alias_remove(
    alias: Annotated[str, typer.Argument(help="Alias name to remove")],
) -> None:
    _emit_queue_result(queue_cmd.alias_remove_command(alias))


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"{PROG_NAME} {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-v",
            help="Show version and exit",
            callback=version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """
    Weft: The Multi-Agent Weaving Toolkit

    A Python tool for orchestrating multi-agent workflows.
    """
    pass


@spec_app.command("create")
def spec_create(
    name: Annotated[str, typer.Argument(help="Spec name")],
    file: Annotated[Path, typer.Option("--file", "-f", help="Spec JSON file")],
    spec_type: Annotated[
        str,
        typer.Option("--type", help="Spec type: task or pipeline"),
    ] = "task",
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite existing spec"),
    ] = False,
) -> None:
    try:
        normalized = spec_cmd.normalize_spec_type(spec_type)
        dest = spec_cmd.create_spec(
            name,
            spec_type=normalized,
            source_path=file,
            context_path=context_dir,
            force=force,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(str(dest))


@spec_app.command("list")
def spec_list(
    spec_type: Annotated[
        str | None,
        typer.Option("--type", help="Filter by spec type (task or pipeline)"),
    ] = None,
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
) -> None:
    try:
        normalized = spec_cmd.normalize_spec_type(spec_type) if spec_type else None
        specs = spec_cmd.list_specs(spec_type=normalized, context_path=context_dir)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if json_output:
        typer.echo(json.dumps(specs, ensure_ascii=False))
        return
    if not specs:
        typer.echo("No specs found")
        return
    for item in specs:
        source = item.get("source")
        if source == spec_cmd.SPEC_SOURCE_BUILTIN:
            typer.echo(f"{item['type']}: {item['name']} (builtin)")
            continue
        typer.echo(f"{item['type']}: {item['name']}")


@spec_app.command("show")
def spec_show(
    name: Annotated[str, typer.Argument(help="Spec name")],
    spec_type: Annotated[
        str | None,
        typer.Option("--type", help="Spec type (task or pipeline)"),
    ] = None,
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
) -> None:
    try:
        normalized = spec_cmd.normalize_spec_type(spec_type) if spec_type else None
        _kind, _path, payload = spec_cmd.load_spec(
            name, spec_type=normalized, context_path=context_dir
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@spec_app.command("delete")
def spec_delete(
    name: Annotated[str, typer.Argument(help="Spec name")],
    spec_type: Annotated[
        str | None,
        typer.Option("--type", help="Spec type (task or pipeline)"),
    ] = None,
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
) -> None:
    try:
        normalized = spec_cmd.normalize_spec_type(spec_type) if spec_type else None
        path = spec_cmd.delete_spec(
            name, spec_type=normalized, context_path=context_dir
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Deleted {path}")


@spec_app.command("validate")
def spec_validate(
    file: Annotated[Path, typer.Argument(help="Spec JSON file")],
    spec_type: Annotated[
        str | None,
        typer.Option("--type", help="Spec type (task or pipeline)"),
    ] = None,
    load_runner: Annotated[
        bool,
        typer.Option(
            "--load-runner",
            help="Require that the configured task runner plugin can be loaded",
        ),
    ] = False,
    preflight: Annotated[
        bool,
        typer.Option(
            "--preflight",
            help="Verify the configured task runner runtime is available",
        ),
    ] = False,
) -> None:
    try:
        normalized = (
            spec_cmd.normalize_spec_type(spec_type)
            if spec_type
            else spec_cmd.infer_spec_type(file)
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if normalized == spec_cmd.SPEC_TYPE_TASK:
        exit_code = cmd_validate_taskspec(
            file,
            load_runner=load_runner,
            preflight=preflight,
        )
        raise typer.Exit(code=exit_code)
    if load_runner or preflight:
        typer.echo(
            "--load-runner and --preflight only apply to task specs",
            err=True,
        )
        raise typer.Exit(code=2)
    ok, errors = spec_cmd.validate_spec(file, spec_type=normalized)
    if ok:
        typer.echo("Spec is valid")
        return
    typer.echo("Spec validation failed")
    for field, error in errors.items():
        typer.echo(f"- {field}: {error}")
    raise typer.Exit(code=2)


@spec_app.command("generate")
def spec_generate(
    spec_type: Annotated[
        str,
        typer.Option("--type", help="Spec type (task or pipeline)"),
    ] = "task",
) -> None:
    try:
        normalized = spec_cmd.normalize_spec_type(spec_type)
        payload = spec_cmd.generate_spec(normalized)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@task_app.command("list")
def task_list(
    status_filter: Annotated[
        str | None,
        typer.Option("--status", help="Filter by task status"),
    ] = None,
    include_terminal: Annotated[
        bool,
        typer.Option("--all", help="Include completed/terminal tasks"),
    ] = False,
    stats: Annotated[
        bool,
        typer.Option("--stats", help="Summarize counts by status"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
) -> None:
    snapshots = task_cmd.list_tasks(
        status_filter=status_filter,
        include_terminal=include_terminal,
        context_path=context_dir,
    )
    if stats:
        counts: dict[str, int] = {}
        for snap in snapshots:
            counts[snap.status] = counts.get(snap.status, 0) + 1
        if json_output:
            typer.echo(json.dumps(counts, ensure_ascii=False))
        else:
            for status, count in sorted(counts.items()):
                typer.echo(f"{status}: {count}")
        return

    if json_output:
        typer.echo(
            json.dumps([snap.to_dict() for snap in snapshots], ensure_ascii=False)
        )
        return
    if not snapshots:
        typer.echo("Tasks: none")
        return
    for snap in snapshots:
        activity = f" [{snap.activity}]" if snap.activity else ""
        typer.echo(
            f"{snap.tid} {snap.status} {snap.runner or '-'} {snap.name}{activity}"
        )


@task_app.command("status")
def task_status(
    tid: Annotated[str, typer.Argument(help="Task ID or short ID")],
    process: Annotated[
        bool,
        typer.Option("--process", help="Include process identifiers"),
    ] = False,
    watch: Annotated[
        bool,
        typer.Option("--watch", help="Stream task state updates"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
) -> None:
    if watch:
        exit_code, watch_payload = cmd_status(
            tid=tid,
            include_terminal=True,
            json_output=json_output,
            watch=True,
            spec_context=context_dir,
        )
        if watch_payload:
            typer.echo(watch_payload)
        raise typer.Exit(code=exit_code)

    snapshot = task_cmd.task_status(tid, context_path=context_dir)
    if snapshot is None:
        typer.echo(f"Task {tid} not found", err=True)
        raise typer.Exit(code=2)
    status_payload: dict[str, Any] = snapshot.to_dict()
    if process:
        ctx = task_cmd._resolve_context(context_dir)
        mapping = task_cmd.mapping_for_tid(ctx, snapshot.tid) or {}
        pid = mapping.get("pid") or mapping.get("task_pid")
        managed_pids = mapping.get("managed_pids") or []
        live_managed_pids = [
            managed_pid
            for managed_pid in managed_pids
            if isinstance(managed_pid, int) and status_cmd._pid_alive(managed_pid)
        ]
        status_payload["pid"] = pid
        status_payload["pid_alive"] = (
            status_cmd._pid_alive(pid) if isinstance(pid, int) else False
        )
        status_payload["managed_pids"] = managed_pids
        status_payload["live_managed_pids"] = live_managed_pids
    if json_output:
        typer.echo(json.dumps(status_payload, ensure_ascii=False))
        return
    typer.echo(
        f"{snapshot.tid} {snapshot.status} {snapshot.runner or '-'} "
        f"{snapshot.name} ({snapshot.event})"
    )
    if snapshot.activity:
        typer.echo(f"activity: {snapshot.activity}")
    if snapshot.waiting_on:
        typer.echo(f"waiting_on: {snapshot.waiting_on}")
    if process:
        pid = status_payload.get("pid")
        managed = status_payload.get("managed_pids")
        pid_state = "live" if status_payload.get("pid_alive") else "dead"
        live_managed = status_payload.get("live_managed_pids")
        typer.echo(
            f"pid: {pid} ({pid_state}) managed_pids: {managed} "
            f"live_managed_pids: {live_managed}"
        )


@task_app.command("stop")
def task_stop(
    tid: Annotated[
        str | None, typer.Argument(help="Task ID", show_default=False)
    ] = None,
    all_tasks: Annotated[
        bool,
        typer.Option("--all", help="Stop all active tasks"),
    ] = False,
    pattern: Annotated[
        str | None,
        typer.Option("--pattern", help="Stop tasks matching name pattern"),
    ] = None,
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
) -> None:
    tids: list[str] = []
    if all_tasks or pattern:
        snapshots = task_cmd.list_tasks(
            include_terminal=False, context_path=context_dir
        )
        tids = task_cmd.filter_tids_by_pattern(snapshots, pattern or "")
    elif tid:
        tids = [tid]
    else:
        typer.echo("Provide a task id or use --all", err=True)
        raise typer.Exit(code=2)

    count = task_cmd.stop_tasks(tids, context_path=context_dir)
    typer.echo(f"Stopped {count} task(s)")


@task_app.command("kill")
def task_kill(
    tid: Annotated[
        str | None, typer.Argument(help="Task ID", show_default=False)
    ] = None,
    all_tasks: Annotated[
        bool,
        typer.Option("--all", help="Kill all active tasks"),
    ] = False,
    pattern: Annotated[
        str | None,
        typer.Option("--pattern", help="Kill tasks matching name pattern"),
    ] = None,
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
) -> None:
    tids: list[str] = []
    if all_tasks or pattern:
        snapshots = task_cmd.list_tasks(
            include_terminal=False, context_path=context_dir
        )
        tids = task_cmd.filter_tids_by_pattern(snapshots, pattern or "")
    elif tid:
        tids = [tid]
    else:
        typer.echo("Provide a task id or use --all", err=True)
        raise typer.Exit(code=2)

    count = task_cmd.kill_tasks(tids, context_path=context_dir)
    typer.echo(f"Killed {count} process(es)")


@task_app.command("tid")
def task_tid(
    tid: Annotated[
        str | None, typer.Argument(help="Short or full TID", show_default=False)
    ] = None,
    pid: Annotated[
        int | None,
        typer.Option("--pid", help="Lookup TID for a PID"),
    ] = None,
    reverse: Annotated[
        str | None,
        typer.Option("--reverse", help="Return short TID for a full TID"),
    ] = None,
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
) -> None:
    result = task_cmd.task_tid(
        tid=tid,
        pid=pid,
        reverse=reverse,
        context_path=context_dir,
    )
    if not result:
        typer.echo("No matching TID found", err=True)
        raise typer.Exit(code=2)
    typer.echo(result)


@app.command("init")
def init(
    directory: Annotated[
        Path,
        typer.Argument(
            help="Directory where the project should be initialized",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            resolve_path=True,
        ),
    ] = Path.cwd(),
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress informational output"),
    ] = False,
    autostart: Annotated[
        bool,
        typer.Option(
            "--autostart/--no-autostart",
            help="Create the autostart directory and enable auto-start tasks",
        ),
    ] = True,
) -> None:
    """Initialize a new Weft project."""

    exit_code = cmd_init(directory, quiet=quiet, autostart=autostart)
    raise typer.Exit(code=exit_code)


@system_app.command("tidy")
def tidy(
    context: Annotated[
        Path | None,
        typer.Option(
            "--context", help="Run maintenance against a specific project root"
        ),
    ] = None,
) -> None:
    """Run backend-native SimpleBroker compaction for the active context."""
    exit_code, payload = cmd_tidy(context)
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@app.command("status")
def status_command(
    all_tasks: Annotated[
        bool,
        typer.Option("--all", help="Include completed/terminal tasks in the summary"),
    ] = False,
    status_filter: Annotated[
        str | None,
        typer.Option("--status", help="Filter tasks by status"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit status information as JSON"),
    ] = False,
    watch: Annotated[
        bool,
        typer.Option("--watch", help="Stream task events as they occur"),
    ] = False,
    interval: Annotated[
        float,
        typer.Option("--interval", help="Polling interval for --watch in seconds"),
    ] = 1.0,
    context_dir: Annotated[
        Path | None,
        typer.Option(
            "--context",
            help="Directory to treat as the Weft context (defaults to discovery)",
        ),
    ] = None,
) -> None:
    """Display task, manager, and broker status information."""

    exit_code, payload = cmd_status(
        tid=None,
        include_terminal=all_tasks,
        status_filter=status_filter,
        json_output=json_output,
        watch=watch,
        watch_interval=interval,
        spec_context=context_dir,
    )

    if payload:
        typer.echo(payload, err=exit_code != 0)
    raise typer.Exit(code=exit_code)


@app.command("result")
def result_command(
    tid: Annotated[
        str | None,
        typer.Argument(help="Task ID to fetch the result for"),
    ] = None,
    all_results: Annotated[
        bool,
        typer.Option("--all", help="Fetch completed results for all tasks"),
    ] = False,
    peek: Annotated[
        bool,
        typer.Option(
            "--peek",
            help="Inspect results without consuming them (requires --all)",
        ),
    ] = False,
    timeout: Annotated[
        float | None,
        typer.Option("--timeout", help="Maximum seconds to wait for completion"),
    ] = None,
    stream: Annotated[
        bool,
        typer.Option(
            "--stream",
            help="Stream incremental output events instead of waiting for completion",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit structured JSON output"),
    ] = False,
    error_stream: Annotated[
        bool,
        typer.Option(
            "--error",
            help="Show stderr instead of stdout when both are present",
        ),
    ] = False,
    context_dir: Annotated[
        Path | None,
        typer.Option(
            "--context",
            help="Directory to treat as the Weft context (defaults to discovery)",
        ),
    ] = None,
) -> None:
    """Fetch the result payload for a completed task."""

    exit_code, payload = cmd_result(
        tid=tid,
        all_results=all_results,
        peek=peek,
        timeout=timeout,
        stream=stream,
        json_output=json_output,
        show_stderr=error_stream,
        context_path=str(context_dir) if context_dir else None,
    )
    if payload:
        typer.echo(payload, err=exit_code != 0)
    raise typer.Exit(code=exit_code)


@app.command(
    "run",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
)
def run_command(
    ctx: typer.Context,
    command: Annotated[
        list[str] | None,
        typer.Argument(
            help="Command to execute (omit when using --function or --spec)",
            show_default=False,
        ),
    ] = None,
    spec: Annotated[
        str | None,
        typer.Option(
            "--spec",
            help="Execute a task spec by stored name or JSON path",
            metavar="NAME|PATH",
        ),
    ] = None,
    pipeline: Annotated[
        str | None,
        typer.Option(
            "--pipeline",
            "-p",
            help="Execute a pipeline by stored name or JSON path",
            metavar="NAME|PATH",
        ),
    ] = None,
    pipeline_input: Annotated[
        str | None,
        typer.Option("--input", help="Initial payload for pipelines"),
    ] = None,
    function: Annotated[
        str | None,
        typer.Option("--function", help="Python callable to execute (module:func)"),
    ] = None,
    arg: Annotated[
        list[str] | None,
        typer.Option(
            "--arg", help="Positional argument for --function", metavar="VALUE"
        ),
    ] = None,
    kw: Annotated[
        list[str] | None,
        typer.Option(
            "--kw", help="Keyword argument in key=value form", metavar="KEY=VALUE"
        ),
    ] = None,
    env: Annotated[
        list[str] | None,
        typer.Option(
            "--env", help="Environment variable KEY=VALUE", metavar="KEY=VALUE"
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Explicit task name"),
    ] = None,
    interactive: Annotated[
        bool,
        typer.Option(
            "-i",
            "--interactive/--non-interactive",
            help="Enable interactive stdin/stdout streaming for commands",
        ),
    ] = False,
    stream_output: Annotated[
        bool | None,
        typer.Option(
            "--stream-output/--no-stream-output",
            help="Stream stdout/stderr to queues instead of single message",
        ),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option("--timeout", help="Execution timeout in seconds"),
    ] = None,
    memory: Annotated[
        int | None,
        typer.Option("--memory", help="Memory limit in MB"),
    ] = None,
    cpu: Annotated[
        int | None,
        typer.Option("--cpu", help="CPU limit percentage (1-100)"),
    ] = None,
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Attach metadata tag", metavar="TAG"),
    ] = None,
    context_dir: Annotated[
        Path | None,
        typer.Option(
            "--context",
            help="Directory to treat as the Weft context (defaults to discovery)",
        ),
    ] = None,
    wait: Annotated[
        bool,
        typer.Option("--wait/--no-wait", help="Wait for task completion"),
    ] = True,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON result"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show detailed output"),
    ] = False,
    monitor: Annotated[
        bool,
        typer.Option(
            "--monitor",
            help="Run TaskSpec in monitor mode (with --spec)",
            hidden=True,
        ),
    ] = False,
    continuous: Annotated[
        bool | None,
        typer.Option(
            "--continuous/--once", help="Continuously process messages for --spec"
        ),
    ] = None,
    autostart: Annotated[
        bool,
        typer.Option(
            "--autostart/--no-autostart",
            help="Enable or disable auto-start tasks for this invocation",
        ),
    ] = True,
    help_flag: Annotated[
        bool,
        typer.Option(
            "--help",
            is_eager=True,
            help="Show this message and exit.",
        ),
    ] = False,
) -> None:
    """Execute a command, function, or TaskSpec via the TaskSpec runner surface.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-1.1.1],
    docs/specifications/02-TaskSpec.md [TS-1.3]
    """
    if help_flag:
        if spec is not None:
            typer.echo(
                render_spec_aware_run_help(
                    ctx,
                    spec=spec,
                    context_dir=context_dir,
                )
            )
        else:
            typer.echo(ctx.get_help())
        raise typer.Exit(code=0)

    raw_command_tokens = list(command or ())
    command_tokens = [] if spec is not None else raw_command_tokens
    exit_code = cmd_run(
        command_tokens,
        spec_run_args=raw_command_tokens if spec is not None else [],
        spec=spec,
        pipeline=pipeline,
        pipeline_input=pipeline_input,
        function=function,
        args=list(arg or ()),
        kwargs=list(kw or ()),
        env=list(env or ()),
        name=name,
        interactive=interactive,
        stream_output=stream_output,
        timeout=timeout,
        memory=memory,
        cpu=cpu,
        tags=list(tag or ()),
        context_dir=context_dir,
        wait=wait,
        json_output=json_output,
        verbose=verbose,
        monitor=monitor,
        persistent_override=continuous,
        autostart_enabled=autostart,
    )
    raise typer.Exit(code=exit_code)


@manager_app.command("start")
def manager_start_command(
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
) -> None:
    exit_code, payload = manager_cmd.start_command(context_path=context)
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@manager_app.command("serve")
def manager_serve_command(
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
) -> None:
    """Run the canonical manager in the foreground."""

    exit_code, payload = serve_cmd.serve_command(context_path=context)
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@manager_app.command("stop")
def manager_stop_command(
    tid: Annotated[str, typer.Argument(help="Manager TID")],
    force: Annotated[
        bool,
        typer.Option("--force", help="Force terminate the manager process"),
    ] = False,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Seconds to wait for graceful stop"),
    ] = 5.0,
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
) -> None:
    exit_code, payload = manager_cmd.stop_command(
        tid=tid, force=force, timeout=timeout, context_path=context
    )
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@manager_app.command("list")
def manager_list_command(
    include_stopped: Annotated[
        bool,
        typer.Option("--all", help="Include stopped managers"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output manager statuses as JSON"),
    ] = False,
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
) -> None:
    exit_code, payload = manager_cmd.list_command(
        json_output=json_output,
        include_stopped=include_stopped,
        context_path=context,
    )
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@manager_app.command("status")
def manager_status_command(
    tid: Annotated[str, typer.Argument(help="Manager TID")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output manager status as JSON"),
    ] = False,
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
) -> None:
    exit_code, payload = manager_cmd.status_command(
        tid=tid, json_output=json_output, context_path=context
    )
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@system_app.command("dump")
def dump_command(
    output: Annotated[
        str | None,
        typer.Option(
            "--output", "-o", help="Output file path (default: .weft/weft_export.jsonl)"
        ),
    ] = None,
    context_dir: Annotated[
        Path | None,
        typer.Option(
            "--context",
            help="Directory to treat as the Weft context (defaults to discovery)",
        ),
    ] = None,
) -> None:
    """Export database state to JSONL format."""
    exit_code, payload = cmd_dump(
        output=output, context_path=str(context_dir) if context_dir else None
    )
    if payload:
        typer.echo(payload, err=exit_code != 0)
    raise typer.Exit(code=exit_code)


@system_app.command("builtins")
def system_builtins_command(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output builtin inventory as JSON"),
    ] = False,
) -> None:
    """List the builtin TaskSpecs shipped with Weft."""

    exit_code, payload = cmd_system_builtins(json_output=json_output)
    if payload:
        typer.echo(payload, err=exit_code != 0)
    raise typer.Exit(code=exit_code)


@system_app.command("load")
def load_command(
    input_file: Annotated[
        str | None,
        typer.Option(
            "--input", help="Input file path (default: .weft/weft_export.jsonl)"
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Preview what would be imported without making changes"
        ),
    ] = False,
    context_dir: Annotated[
        Path | None,
        typer.Option(
            "--context",
            help="Directory to treat as the Weft context (defaults to discovery)",
        ),
    ] = None,
) -> None:
    """Import database state from JSONL format."""
    exit_code, payload = cmd_load(
        input_file=input_file,
        dry_run=dry_run,
        context_path=str(context_dir) if context_dir else None,
    )
    if payload:
        typer.echo(payload, err=exit_code != 0)
    raise typer.Exit(code=exit_code)


app.add_typer(queue_app, name="queue")
app.add_typer(manager_app, name="manager")
app.add_typer(spec_app, name="spec")
app.add_typer(task_app, name="task")
app.add_typer(system_app, name="system")


if __name__ == "__main__":
    app()
