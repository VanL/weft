"""Typer entry point for the current Weft CLI surface.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-0], [CLI-0.3], [CLI-1.1], [CLI-1.2]
- docs/specifications/11-CLI_Architecture_Crosswalk.md [CLI-X0], [CLI-X1]
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, cast

import typer

from simplebroker import format_message_id
from weft import commands
from weft._constants import (
    MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    PROG_NAME,
    __version__,
    get_weft_directory_name,
)
from weft.cli import validate_taskspec as validate_cli

from .run import (
    consume_run_session,
    drive_interactive_session,
    read_run_stdin,
    render_run_description,
    render_run_result,
)

app = typer.Typer(
    name=PROG_NAME,
    help="Weft: the durable task substrate for agent systems",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)

queue_app = typer.Typer(help="Queue passthrough operations")
manager_app = typer.Typer(help="Manager lifecycle management")
spec_app = typer.Typer(help="Spec management")
task_app = typer.Typer(help="Task management")
system_app = typer.Typer(help="System maintenance")
default_export_path_help = f"{get_weft_directory_name()}/weft_export.jsonl"


def _command_exit(exc: Exception, *, usage_code: int = 2) -> None:
    """Render one typed command failure and exit with the CLI mapping."""
    typer.echo(str(exc), err=True)
    if isinstance(exc, commands.CommandTimeoutError):
        raise typer.Exit(code=124) from exc
    if isinstance(exc, commands.CommandUsageError):
        raise typer.Exit(code=usage_code) from exc
    if isinstance(
        exc, (commands.InvalidTID, commands.TaskNotFound, commands.SpecNotFound)
    ):
        raise typer.Exit(code=2) from exc
    raise typer.Exit(code=1) from exc


def _queue_command_exit(exc: Exception, *, json_output: bool = False) -> None:
    """Preserve queue-specific input-error rendering."""
    if (
        json_output
        and isinstance(exc, commands.CommandUsageError)
        and "message ID" in str(exc)
    ):
        typer.echo(
            json.dumps(
                {
                    "error": "INVALID_MESSAGE_ID",
                    "message": "invalid message ID: expected exactly 19 digits within range",
                    "retryable": False,
                }
            ),
            err=True,
        )
        raise typer.Exit(code=1) from exc
    _command_exit(exc, usage_code=1)


def _jsonable(value: Any) -> Any:
    """Convert command dataclasses and paths into JSON-safe adapter values."""

    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _render_status_snapshot(
    snapshot: commands.SystemStatusSnapshot,
    *,
    json_output: bool,
) -> None:
    """Render one structured root-status outcome."""

    if json_output:
        typer.echo(json.dumps(_jsonable(snapshot), ensure_ascii=False))
        return
    broker = snapshot.broker
    lines = [
        f"total_messages: {broker.get('total_messages', 0)}",
        f"last_timestamp: {broker.get('last_timestamp', 0)}",
        f"db_size: {broker.get('db_size', 0)} bytes",
    ]
    if snapshot.managers:
        lines.append("Managers:")
        for manager in snapshot.managers:
            lines.extend(
                (
                    f"  - tid: {manager.tid}",
                    f"    role: {manager.role or 'manager'}",
                    f"    status: {manager.status}",
                    "    runtime: "
                    + (
                        json.dumps(manager.runtime_handle, sort_keys=True)
                        if manager.runtime_handle is not None
                        else "n/a"
                    ),
                    f"    requests: {manager.requests or ''}",
                    f"    outbox: {manager.outbox or ''}",
                    f"    timestamp: {manager.timestamp or 0}",
                )
            )
    else:
        lines.append("Managers: none registered")
    if snapshot.services:
        lines.append("Services:")
        for service in snapshot.services:
            parts = [f"  {service.name:<18}", f"{service.status:<10}"]
            if service.tid is not None:
                parts.append(f"tid={service.tid}")
            parts.append(f"evidence={service.evidence}")
            if service.queue is not None:
                parts.append(f"queue={service.queue}")
            lines.append(" ".join(parts))
    else:
        lines.append("Services: none")
    if snapshot.tasks:
        lines.extend(
            (
                "Tasks:",
                "  {:<19} {:<10} {:<12} {:<14} {:<20} {:<20} {:<10} {}".format(
                    "TID",
                    "STATUS",
                    "ACTIVITY",
                    "RUNNER",
                    "NAME",
                    "STARTED",
                    "DURATION",
                    "EVENT",
                ),
            )
        )
        for task in snapshot.tasks:
            duration = (
                "-"
                if task.duration_seconds is None
                else f"{task.duration_seconds:.1f}s"
            )
            lines.append(
                f"  {task.tid:<19} {task.status:<10} {(task.activity or '-'):<12} "
                f"{(task.runner or '-'):<14} {task.name[:20]:<20} "
                f"{task.started_at or '-'!s:<20} {duration:<10} {task.event}"
            )
    else:
        lines.append("Tasks: none")
    typer.echo("\n".join(lines))


def _render_task_result(
    result: commands.TaskResult,
    *,
    json_output: bool,
    error_stream: bool,
) -> int:
    """Render one canonical result using the CLI's exit policy."""

    value = (
        result.stderr if error_stream and result.stderr is not None else result.value
    )
    if result.status == "completed":
        if json_output:
            typer.echo(
                json.dumps(
                    {"tid": result.tid, "status": result.status, "result": value},
                    ensure_ascii=False,
                )
            )
        elif isinstance(value, (dict, list)):
            typer.echo(json.dumps(value, ensure_ascii=False))
        elif value is not None:
            typer.echo(str(value))
        return 0
    message = result.error or f"weft result: task {result.tid} failed"
    typer.echo(message, err=True)
    return 124 if result.status == "timeout" else 1


def _manager_json_record(snapshot: commands.ManagerSnapshot) -> dict[str, Any]:
    """Project one manager snapshot without absent optional fields."""

    payload = {
        key: value for key, value in asdict(snapshot).items() if value is not None
    }
    if snapshot.timestamp is not None:
        payload["timestamp"] = format_message_id(snapshot.timestamp)
    return payload


def _prune_summary(result: commands.SystemPruneResult, family: str) -> Any:
    """Select an existing family summary from structured prune detail."""

    def project(detail: Any, *, retention: bool) -> dict[str, Any]:
        keys = (
            "schema_version",
            "record_type",
            "run_id",
            "family",
            "dry_run",
            "force",
            "queues_scanned",
            "records_scanned",
            "candidates",
            "archived",
            "deleted",
            "failed",
            "candidate_class_counts",
            "classification_counts",
            "errors",
            "warnings",
        )
        return {
            key: detail[key]
            for key in keys
            if key in detail
            and (
                retention
                or key
                not in {
                    "family",
                    "force",
                    "archived",
                    "candidate_class_counts",
                    "warnings",
                }
            )
        }

    if family == "all":
        return {
            "runtime_state": project(result.details["runtime_state"], retention=False),
            "retention": project(result.details["retention"], retention=True),
        }
    return project(
        result.details["runtime_state"]
        if family == "runtime-state"
        else result.details["retention"],
        retention=family != "runtime-state",
    )


def _render_prune_plain(result: commands.SystemPruneResult, family: str) -> str:
    """Render established human prune summaries from structured detail."""

    def render_one(detail: Any, *, retention: bool) -> str:
        mode = "dry-run" if detail["dry_run"] else "apply"
        if retention and detail.get("force") and not detail["dry_run"]:
            mode = "force apply"
        label = "Retention" if retention else "Runtime state"
        archived = f", archived {detail['archived']}" if retention else ""
        lines = [
            (
                f"{label} prune {mode}: scanned {detail['records_scanned']} records, "
                f"found {detail['candidates']} candidates{archived}, "
                f"deleted {detail['deleted']}, failed {detail['failed']}."
            )
        ]
        lines.extend(f"warning: {warning}" for warning in detail.get("warnings", ()))
        applied = {
            (item["queue"], item["message_id"]): item
            for item in detail.get("applied_candidates", ())
        }
        for candidate in detail.get("candidates_detail", ()):
            visible = applied.get(
                (candidate["queue"], candidate["message_id"]),
                candidate,
            )
            if retention:
                state = (
                    "report-only"
                    if candidate["report_only"] and not detail.get("force")
                    else "kept"
                )
                if visible["applied"]:
                    state = "deleted"
                elif visible["error"]:
                    state = f"error: {visible['error']}"
                protections = (
                    f" overrides={','.join(candidate['overridden_protections'])}"
                    if candidate["overridden_protections"]
                    else ""
                )
                lines.append(
                    f"{candidate['queue']} {candidate['message_id']} "
                    f"{candidate['candidate_class']} {candidate['tid']} "
                    f"{state}{protections}"
                )
            else:
                state = "report-only" if candidate["report_only"] else "kept"
                if not detail["dry_run"] and visible["applied"]:
                    state = "deleted"
                elif visible["error"]:
                    state = f"error: {visible['error']}"
                lines.append(
                    f"{candidate['queue']} {candidate['message_id']} "
                    f"{candidate['classification']} {candidate['key']} {state}"
                )
        return "\n".join(lines)

    if family == "all":
        return (
            "Runtime state:\n"
            + render_one(result.details["runtime_state"], retention=False)
            + "\nRetention:\n"
            + render_one(result.details["retention"], retention=True)
        )
    detail = (
        result.details["runtime_state"]
        if family == "runtime-state"
        else result.details["retention"]
    )
    return render_one(detail, retention=family != "runtime-state")


def _stdin_message(message: str | None) -> str:
    """Decode the CLI's implicit-stdin message convention."""
    if message is None or message == "-":
        return str(typer.get_text_stream("stdin").read())
    return message


def _queue_entry_text(
    entry: commands.QueueEntry, *, timestamps: bool, json_output: bool
) -> str:
    if json_output:
        return json.dumps(
            {"message": entry.message, "timestamp": format_message_id(entry.timestamp)},
            ensure_ascii=False,
        )
    if timestamps:
        return f"{entry.timestamp}\t{entry.message}"
    return str(entry.message)


def _emit_queue_entries(
    entries: tuple[commands.QueueEntry, ...],
    *,
    timestamps: bool,
    json_output: bool,
) -> None:
    for entry in entries:
        typer.echo(
            _queue_entry_text(entry, timestamps=timestamps, json_output=json_output)
        )
    if not entries:
        raise typer.Exit(code=2)


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
    after: Annotated[
        str | None,
        typer.Option(
            "--after",
            help="Only return messages newer than timestamp",
        ),
    ] = None,
    before: Annotated[
        str | None,
        typer.Option("--before", help="Only return messages older than timestamp"),
    ] = None,
) -> None:
    try:
        entries = commands.cmd_queue_read(
            name,
            all=all_messages,
            message=message_id,
            after=after,
            before=before,
        )
    except (commands.CommandError, commands.InvalidTID, commands.TaskNotFound) as exc:
        _queue_command_exit(exc, json_output=json_output)
    _emit_queue_entries(entries, timestamps=timestamps, json_output=json_output)


@queue_app.command("write")
def queue_write(
    name_or_message: Annotated[
        str | None,
        typer.Argument(help="Queue name, or message when --endpoint is used"),
    ] = None,
    message: Annotated[
        str | None, typer.Argument(help="Message to write (omit or use '-' for stdin)")
    ] = None,
    endpoint: Annotated[
        str | None,
        typer.Option("--endpoint", help="Named endpoint to resolve and write to"),
    ] = None,
) -> None:
    if endpoint is None:
        if name_or_message is None:
            raise typer.BadParameter(
                "Provide a queue name or use --endpoint",
                param_hint="name_or_message",
            )
        queue_name = name_or_message
        payload = _stdin_message(message)
    else:
        if message is not None:
            raise typer.BadParameter(
                "When using --endpoint, provide at most one positional message",
                param_hint="message",
            )
        queue_name = _stdin_message(name_or_message)
        payload = None

    try:
        commands.cmd_queue_write(queue_name, payload, endpoint=endpoint)
    except commands.CommandError as exc:
        _command_exit(exc, usage_code=1)


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
    after: Annotated[
        str | None,
        typer.Option(
            "--after",
            help="Only return messages newer than timestamp",
        ),
    ] = None,
    before: Annotated[
        str | None,
        typer.Option("--before", help="Only return messages older than timestamp"),
    ] = None,
) -> None:
    try:
        entries = commands.cmd_queue_peek(
            name,
            all=all_messages,
            message=message_id,
            after=after,
            before=before,
        )
    except commands.CommandError as exc:
        _queue_command_exit(exc, json_output=json_output)
    _emit_queue_entries(entries, timestamps=timestamps, json_output=json_output)


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
    after: Annotated[
        str | None,
        typer.Option(
            "--after",
            help="Only move messages newer than timestamp",
        ),
    ] = None,
    before: Annotated[
        str | None,
        typer.Option("--before", help="Only move messages older than timestamp"),
    ] = None,
) -> None:
    try:
        result = commands.cmd_queue_move(
            source,
            destination,
            limit=limit,
            all=all_messages,
            message=message_id,
            after=after,
            before=before,
        )
    except commands.CommandError as exc:
        _queue_command_exit(exc, json_output=json_output)
    if not result.entries:
        raise typer.Exit(code=2)
    if limit is not None:
        typer.echo(
            f"Moved {result.moved_count} messages from {source} to {destination}"
        )
    if limit is None or json_output or timestamps:
        for entry in result.entries:
            typer.echo(
                _queue_entry_text(entry, timestamps=timestamps, json_output=json_output)
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
    endpoints: Annotated[
        bool,
        typer.Option("--endpoints", help="List canonical named task endpoints"),
    ] = False,
    pattern: Annotated[
        str | None,
        typer.Option(
            "--pattern",
            "-p",
            help="fnmatch-style pattern limiting queues in the result",
        ),
    ] = None,
    prefix: Annotated[
        str | None,
        typer.Option(
            "--prefix",
            help="Literal prefix limiting queues in the result",
        ),
    ] = None,
) -> None:
    try:
        rows = commands.cmd_queue_list(
            stats=stats,
            endpoints=endpoints,
            pattern=pattern,
            prefix=prefix,
        )
    except commands.CommandError as exc:
        _command_exit(exc, usage_code=1)
    _render_queue_list(rows, stats=stats, endpoints=endpoints, json_output=json_output)


def _render_queue_list(
    rows: tuple[commands.QueueInfo, ...] | tuple[commands.EndpointResolution, ...],
    *,
    stats: bool,
    endpoints: bool,
    json_output: bool,
) -> None:
    """Render structured queue or endpoint rows."""
    if endpoints:
        if json_output:
            typer.echo(json.dumps([asdict(row) for row in rows], ensure_ascii=False))
        else:
            for row in rows:
                line = f"{row.name}\t{row.tid}\t{row.inbox}"
                if row.live_candidates > 1:
                    line += f"\t({row.live_candidates} live claims)"
                typer.echo(line)
        return
    for row in rows:
        if json_output:
            payload: dict[str, Any] = {"queue": row.name}
            if stats:
                payload.update(
                    pending=row.messages,
                    total=row.total_messages,
                    claimed=row.claimed_messages,
                )
            typer.echo(json.dumps(payload, ensure_ascii=False))
        elif stats:
            if row.messages != row.total_messages:
                typer.echo(
                    f"{row.name}: {row.messages} "
                    f"({row.total_messages} total, {row.claimed_messages} claimed)"
                )
            else:
                typer.echo(f"{row.name}: {row.messages}")
        else:
            typer.echo(row.name)


@queue_app.command("exists")
def queue_exists(
    name: Annotated[str, typer.Argument(help="Queue name to check")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
) -> None:
    try:
        exists = commands.cmd_queue_exists(name)
    except commands.CommandError as exc:
        _command_exit(exc, usage_code=1)
    if json_output:
        typer.echo(json.dumps({"queue": name, "exists": exists}))
    if not exists:
        raise typer.Exit(code=2)


@queue_app.command("stats")
def queue_stats(
    name: Annotated[str, typer.Argument(help="Queue name to inspect")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
) -> None:
    try:
        info = commands.cmd_queue_stats(name)
    except commands.CommandError as exc:
        _command_exit(exc, usage_code=1)
    payload = {
        "queue": info.name,
        "pending": info.messages,
        "claimed": info.claimed_messages or 0,
        "total": info.total_messages
        if info.total_messages is not None
        else info.messages,
        "exists": True,
    }
    if json_output:
        typer.echo(json.dumps(payload))
    elif info.messages != payload["total"]:
        typer.echo(
            f"{info.name}: {info.messages} "
            f"({payload['total']} total, {payload['claimed']} claimed)"
        )
    else:
        typer.echo(f"{info.name}: {info.messages}")


@queue_app.command("resolve")
def queue_resolve(
    endpoint_name: Annotated[str, typer.Argument(help="Named endpoint to resolve")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output endpoint details as JSON"),
    ] = False,
) -> None:
    try:
        resolved = commands.cmd_queue_resolve(endpoint_name)
    except commands.CommandError as exc:
        if isinstance(exc, commands.CommandExecutionError) and str(exc).startswith(
            "No active endpoint"
        ):
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
        _command_exit(exc, usage_code=1)
    if json_output:
        typer.echo(json.dumps(asdict(resolved), ensure_ascii=False))
        return
    for field in ("name", "tid", "status", "inbox", "outbox", "ctrl_in", "ctrl_out"):
        typer.echo(f"{field}: {getattr(resolved, field)}")
    typer.echo(f"registered_at: {format_message_id(resolved.registered_at)}")
    typer.echo(f"last_seen: {format_message_id(resolved.last_seen)}")
    typer.echo(f"live_candidates: {resolved.live_candidates}")
    if resolved.metadata:
        typer.echo(f"metadata: {json.dumps(resolved.metadata, ensure_ascii=False)}")


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
    after: Annotated[
        str | None,
        typer.Option(
            "--after",
            help="Start watching after timestamp",
        ),
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
    try:
        stream = commands.cmd_queue_watch(
            name,
            limit=limit,
            interval=interval,
            peek=peek,
            after=after,
            move=move_to,
        )
    except commands.CommandError as exc:
        _command_exit(exc, usage_code=1)
    if not quiet:
        mode = "peek" if peek else "consume"
        if move_to:
            mode = f"move to {move_to}"
        typer.echo(f"Watching queue '{name}' ({mode} mode)...", err=True)
    try:
        for entry in stream:
            typer.echo(
                _queue_entry_text(entry, timestamps=timestamps, json_output=json_output)
            )
    except commands.CommandError as exc:
        _command_exit(exc, usage_code=1)
    finally:
        stream.close()


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
    try:
        commands.cmd_queue_delete(
            name,
            all=all_queues,
            message=message_id,
        )
    except commands.CommandError as exc:
        if all_queues and message_id is not None:
            typer.echo("--message cannot be used with --all", err=True)
            raise typer.Exit(code=1) from exc
        _command_exit(exc, usage_code=1)


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
    try:
        commands.cmd_queue_broadcast(_stdin_message(message), pattern=pattern)
    except commands.CommandError as exc:
        _command_exit(exc, usage_code=1)


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
    try:
        commands.cmd_queue_alias_add(alias, target)
    except commands.CommandError as exc:
        _command_exit(exc, usage_code=1)


@alias_app.command("list")
def alias_list(
    target: Annotated[
        str | None,
        typer.Option("--target", "-t", help="Show aliases for specific target queue"),
    ] = None,
) -> None:
    try:
        records = commands.cmd_queue_alias_list(target=target)
    except commands.CommandError as exc:
        _command_exit(exc, usage_code=1)
    for record in records:
        typer.echo(f"{record.alias} -> {record.target}")
    if target is not None and not records:
        raise typer.Exit(code=2)


@alias_app.command("remove")
def alias_remove(
    alias: Annotated[str, typer.Argument(help="Alias name to remove")],
) -> None:
    try:
        commands.cmd_queue_alias_remove(alias)
    except commands.CommandError as exc:
        _command_exit(exc, usage_code=1)


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
    Weft: the durable task substrate for agent systems.

    Durable task execution on SimpleBroker queues: persistent managers,
    multiprocess isolation, and comprehensive observability.
    """


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
        result = commands.cmd_spec_create(
            name,
            file=file,
            type=spec_type,
            context=context_dir,
            force=force,
        )
    except (commands.CommandError, commands.SpecNotFound) as exc:
        _command_exit(exc)
    typer.echo(str(result.record.path))


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
        specs = commands.cmd_spec_list(type=spec_type, context=context_dir)
    except commands.CommandError as exc:
        _command_exit(exc)
    if json_output:
        typer.echo(
            json.dumps(
                [
                    {
                        "type": item.spec_type,
                        "name": item.name,
                        "path": str(item.path),
                        "source": item.source,
                    }
                    for item in specs
                ],
                ensure_ascii=False,
            )
        )
        return
    if not specs:
        typer.echo("No specs found")
        return
    for item in specs:
        if item.source == "builtin":
            typer.echo(f"{item.spec_type}: {item.name} (builtin)")
            continue
        typer.echo(f"{item.spec_type}: {item.name}")


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
        record = commands.cmd_spec_show(name, type=spec_type, context=context_dir)
    except (commands.CommandError, commands.SpecNotFound) as exc:
        _command_exit(exc)
    typer.echo(json.dumps(record.payload, ensure_ascii=False, indent=2))


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
        result = commands.cmd_spec_delete(name, type=spec_type, context=context_dir)
    except (commands.CommandError, commands.SpecNotFound) as exc:
        _command_exit(exc)
    typer.echo(f"Deleted {result.record.path}")


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
    validation_file = file / "taskspec.json" if file.is_dir() else file
    try:
        result = commands.cmd_spec_validate(
            validation_file,
            type=spec_type,
            load_runner=load_runner,
            preflight=preflight,
        )
    except (commands.CommandError, commands.SpecNotFound) as exc:
        if spec_type == "task":
            if not validation_file.exists():
                validate_cli.console.print(
                    f"[red]Error:[/red] File not found: {validation_file}"
                )
            else:
                validate_cli.console.print(
                    "[red]✗[/red] TaskSpec validation failed\n\n"
                    f"[cyan]_json[/cyan]: {exc}"
                )
            raise typer.Exit(code=1) from exc
        _command_exit(exc)
    _render_spec_validation(
        result,
        load_runner=load_runner,
        preflight=preflight,
    )


def _render_spec_validation(
    result: commands.SpecValidationResult,
    *,
    load_runner: bool,
    preflight: bool,
) -> None:
    """Render the structured validation result using the established CLI UX."""
    if result.spec_type == "task":
        failed_stage = next(iter(result.errors_by_stage), None)
        if failed_stage == "schema":
            validate_cli._display_failure(result, failed_stage)
            raise typer.Exit(code=1)
        validate_cli.console.print("[green]✓[/green] TaskSpec is valid")
        validate_cli._display_completed_preflight_stages(
            result,
            failed_stage=failed_stage,
            load_runner=load_runner or preflight,
            preflight=preflight,
        )
        if failed_stage is not None:
            validate_cli._display_failure(result, failed_stage)
            raise typer.Exit(code=1)
        if result.payload is not None:
            validate_cli._display_taskspec_summary(dict(result.payload))
        return
    if result.valid:
        typer.echo("Spec is valid")
        return
    if load_runner or preflight:
        typer.echo("--load-runner and --preflight only apply to task specs", err=True)
        raise typer.Exit(code=2)
    typer.echo("Spec validation failed")
    for stage_errors in result.errors_by_stage.values():
        for field, error in stage_errors.items():
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
        payload = commands.cmd_spec_generate(type=spec_type)
    except commands.CommandError as exc:
        _command_exit(exc)
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
    try:
        snapshots = commands.cmd_task_list(
            status=status_filter,
            all=include_terminal,
            context=context_dir,
        )
    except commands.CommandError as exc:
        _command_exit(exc)
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
            json.dumps(
                [_task_snapshot_payload(snap) for snap in snapshots],
                ensure_ascii=False,
            )
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


def _task_snapshot_payload(snapshot: commands.TaskSnapshot) -> dict[str, Any]:
    """Project a structured task snapshot into the established CLI JSON shape."""
    payload = asdict(snapshot)
    if isinstance(snapshot.last_timestamp, int):
        payload["last_timestamp"] = format_message_id(snapshot.last_timestamp)
    reconciliation = payload.get("reconciliation")
    if isinstance(reconciliation, dict):
        observed_at = reconciliation.get("observed_at")
        if isinstance(observed_at, int):
            reconciliation["observed_at"] = format_message_id(observed_at)
    for field in ("host_pids", "managed_pids", "live_managed_pids"):
        if payload.get(field) is not None:
            payload[field] = list(payload[field])
    return payload


def _task_status_plain_lines(
    status_payload: dict[str, Any],
    *,
    include_process: bool,
) -> list[str]:
    """Project the public task-status payload into ordered plain-text lines."""
    lines = [
        (
            f"{status_payload['tid']} {status_payload['status']} "
            f"{status_payload.get('runner') or '-'} {status_payload['name']} "
            f"({status_payload['event']})"
        )
    ]
    activity = status_payload.get("activity")
    if activity:
        lines.append(f"activity: {activity}")
    waiting_on = status_payload.get("waiting_on")
    if waiting_on:
        lines.append(f"waiting_on: {waiting_on}")
    raw_diagnostics = status_payload.get("runner_diagnostics")
    diagnostics = _runner_diagnostics_text(
        raw_diagnostics if isinstance(raw_diagnostics, dict) else None
    )
    if diagnostics is not None and status_payload["status"] in {
        "failed",
        "timeout",
        "killed",
    }:
        lines.append(f"runner_diagnostics: {diagnostics}")
    if include_process:
        managed = status_payload.get("managed_pids")
        live_managed = status_payload.get("live_managed_pids")
        lines.append(f"host_pids: {managed} live_host_pids: {live_managed}")
    return lines


def _runner_diagnostics_text(diagnostics: dict[str, Any] | None) -> str | None:
    """Format structured runner diagnostics for the terminal."""
    if not diagnostics:
        return None
    parts = [
        f"{key}={diagnostics[key]}"
        for key in ("phase", "pid", "exitcode", "alive", "last_handshake")
        if diagnostics.get(key) is not None
    ]
    message = diagnostics.get("message")
    if isinstance(message, str) and message:
        parts.append(f"message={message}")
    return ", ".join(parts) if parts else None


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
    ping: Annotated[
        bool,
        typer.Option(
            "--ping",
            help="Send a keyed PING and use the matched PONG as current-state proof",
        ),
    ] = False,
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
) -> None:
    try:
        snapshot = commands.cmd_task_status(
            tid,
            process=process,
            watch=watch,
            ping=ping,
            context=context_dir,
        )
    except (commands.CommandError, commands.InvalidTID, commands.TaskNotFound) as exc:
        if isinstance(exc, commands.InvalidTID):
            typer.echo(f"Task {tid} not found", err=True)
            raise typer.Exit(code=2) from exc
        _command_exit(exc)
    if watch:
        stream = cast(commands.CommandStream[commands.TaskEvent], snapshot)
        try:
            for event in stream:
                if json_output:
                    typer.echo(json.dumps(asdict(event), ensure_ascii=False))
                else:
                    typer.echo(
                        f"{event.timestamp}\t{event.event_type}\t{event.payload}"
                    )
        except commands.CommandError as exc:
            _command_exit(exc)
        finally:
            stream.close()
        return
    task_snapshot = cast(commands.TaskSnapshot, snapshot)
    status_payload = _task_snapshot_payload(task_snapshot)
    if process:
        status_payload.update(
            {
                "host_pids": list(task_snapshot.host_pids or ()),
                "managed_pids": list(task_snapshot.managed_pids or ()),
                "live_managed_pids": list(task_snapshot.live_managed_pids or ()),
            }
        )
    if json_output:
        typer.echo(json.dumps(status_payload, ensure_ascii=False))
        return
    for line in _task_status_plain_lines(
        status_payload,
        include_process=process,
    ):
        typer.echo(line)


@task_app.command("ping")
def task_ping(
    tid: Annotated[str, typer.Argument(help="Task ID or short ID")],
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Seconds to wait for a matching PONG"),
    ] = 10.0,
    context_dir: Annotated[
        Path | None,
        typer.Option("--context", help="Project root (defaults to auto-discovery)"),
    ] = None,
) -> None:
    try:
        result = commands.cmd_task_ping(tid, timeout=timeout, context=context_dir)
    except (commands.CommandError, commands.InvalidTID, commands.TaskNotFound) as exc:
        _command_exit(exc)
    json_payload = {
        "timed_out": result.timed_out,
        "error": result.error,
        "observed_at": result.observed_at,
        "pong": result.pong,
    }
    observed_at = json_payload.get("observed_at")
    if isinstance(observed_at, int) and not isinstance(observed_at, bool):
        json_payload["observed_at"] = format_message_id(observed_at)
    typer.echo(json.dumps(json_payload, ensure_ascii=False, indent=2, sort_keys=True))


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
    try:
        result = commands.cmd_task_stop(
            tid, all=all_tasks, pattern=pattern, context=context_dir
        )
    except (commands.CommandError, commands.InvalidTID, commands.TaskNotFound) as exc:
        _command_exit(exc)
    typer.echo(f"Stopped {len(result.accepted)} task(s)")


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
    try:
        result = commands.cmd_task_kill(
            tid, all=all_tasks, pattern=pattern, context=context_dir
        )
    except (commands.CommandError, commands.InvalidTID, commands.TaskNotFound) as exc:
        _command_exit(exc)
    typer.echo(f"Killed {len(result.accepted)} process(es)")


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
    try:
        result = commands.cmd_task_tid(
            tid, pid=pid, reverse=reverse, context=context_dir
        )
    except (commands.CommandError, commands.InvalidTID, commands.TaskNotFound) as exc:
        _command_exit(exc)
    typer.echo(result[-10:] if reverse is not None else result)


@app.command("init")
def init(
    directory: Annotated[
        Path,
        typer.Argument(
            help="Directory where the project should be initialized",
            exists=False,
            file_okay=False,
            dir_okay=True,
            metavar="DIRECTORY",
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

    try:
        result = commands.cmd_init(directory, autostart=autostart)
    except commands.CommandError as exc:
        if not quiet:
            typer.echo(f"weft: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not quiet:
        typer.echo(f"Initialized Weft project in {result.root}")


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
    try:
        result = commands.cmd_system_tidy(context=context)
    except commands.WeftError as exc:
        _command_exit(exc)
    typer.echo(f"Tidied {result.target}")


@system_app.command("task-monitor")
def task_monitor(
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Run the task monitor against a project root"),
    ] = None,
    once: Annotated[
        bool,
        typer.Option(
            "--once/--follow",
            help="Scan once or follow for future task-log entries",
        ),
    ] = True,
    sink: Annotated[
        str,
        typer.Option("--sink", help="Task monitor sink: stdout or disk"),
    ] = "stdout",
    log_dir: Annotated[
        Path | None,
        typer.Option("--log-dir", help="Directory for disk JSONL task-monitor files"),
    ] = None,
    checkpoint: Annotated[
        Path | None,
        typer.Option("--checkpoint", help="Task monitor checkpoint path"),
    ] = None,
    no_checkpoint: Annotated[
        bool,
        typer.Option("--no-checkpoint", help="Do not read or write checkpoint state"),
    ] = False,
    since: Annotated[
        int | None,
        typer.Option("--since", help="Start after this task-log timestamp"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximum task-log events to process"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit final command summary as JSON for disk sink"),
    ] = False,
) -> None:
    """Scan task evidence and emit non-destructive JSONL."""

    try:
        if sink == "stdout" and json_output:
            raise commands.CommandExecutionError(
                "--json cannot be combined with --sink stdout"
            )
        result = commands.cmd_system_task_monitor(
            context=context,
            follow=not once,
            sink=cast(Any, sink),
            log_dir=log_dir,
            checkpoint=checkpoint,
            no_checkpoint=no_checkpoint,
            since=since,
            limit=limit,
        )
    except commands.WeftError as exc:
        _command_exit(exc)
    if not once:
        for summary in result:
            typer.echo(
                json.dumps(dict(summary.record), ensure_ascii=False, sort_keys=True)
            )
        return
    if sink == "stdout":
        for record in result.records:
            typer.echo(
                json.dumps(dict(record.record), ensure_ascii=False, sort_keys=True)
            )
    elif json_output:
        typer.echo(
            json.dumps(
                {
                    "records_written": result.records_written,
                    "events_scanned": result.events_scanned,
                    "tids_seen": result.tids_seen,
                    "summaries_emitted": result.summaries_emitted,
                    "checkpoint_timestamp": (
                        format_message_id(result.checkpoint_timestamp)
                        if result.checkpoint_timestamp is not None
                        else None
                    ),
                    "log_path": str(result.log_path) if result.log_path else None,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        typer.echo(
            f"Task monitor wrote {result.records_written} record(s) "
            f"to {result.log_path}"
        )


@system_app.command("prune")
def prune(
    family: Annotated[
        str,
        typer.Option(
            "--family",
            help=(
                "Prune family: runtime-state, task-local, task-log, retention, or all"
            ),
        ),
    ],
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Run pruning against a specific project root"),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply/--dry-run",
            help="Delete selected prune candidates or only report candidates",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Enable aggressive retention cleanup in apply mode",
        ),
    ] = False,
    queues: Annotated[
        list[str] | None,
        typer.Option(
            "--queue",
            help=(
                "Runtime queue group to scan: tid-mappings, managers, streaming, "
                "endpoints, pipelines, or all. Repeatable."
            ),
        ),
    ] = None,
    min_age: Annotated[
        float,
        typer.Option("--min-age", help="Minimum row age in seconds before pruning"),
    ] = 3600.0,
    keep_recent_per_key: Annotated[
        int,
        typer.Option(
            "--keep-recent-per-key",
            help="Newest rows to preserve for each logical runtime-state key",
        ),
    ] = 1,
    keep_recent_per_task: Annotated[
        int,
        typer.Option(
            "--keep-recent-per-task",
            help="Newest lifecycle-log rows to preserve for each task",
        ),
    ] = 1,
    tasks: Annotated[
        list[str] | None,
        typer.Option("--task", help="Task TID to include in retention pruning"),
    ] = None,
    retention_classes: Annotated[
        list[str] | None,
        typer.Option(
            "--retention-class",
            help="Retention candidate class to include. Repeatable.",
        ),
    ] = None,
    archive: Annotated[
        Path | None,
        typer.Option("--archive", help="Write retention prune archive JSONL"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximum candidates to report or apply"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one JSON summary object"),
    ] = False,
    report: Annotated[
        Path | None,
        typer.Option("--report", help="Write a JSONL prune report"),
    ] = None,
) -> None:
    """Prune stale Weft broker rows.

    For ``--family task-local``, ``task-log``, or ``retention`` (retention
    pruning), note that under the default Monitor collation mode
    (``delete``), most task-log evidence is already deleted at ingest once
    a family is collated (spec: [OBS.13.3]). This command principally
    matters for collation-off deployments or for task-local queues that
    collation does not cover.
    """

    try:
        result = commands.cmd_system_prune(
            family=family,
            context=context,
            apply=apply,
            force=force,
            queue=tuple(queues or ()),
            task=tuple(tasks or ()),
            retention_class=tuple(retention_classes or ()),
            min_age=min_age,
            keep_recent_per_key=keep_recent_per_key,
            keep_recent_per_task=keep_recent_per_task,
            limit=limit,
            archive=archive,
            report=report,
        )
    except commands.WeftError as exc:
        _command_exit(exc, usage_code=1)
    summary = _prune_summary(result, family)
    errors = (
        [
            *summary.get("errors", ()),
            *summary.get("warnings", ()),
        ]
        if family != "all"
        else [
            error
            for detail in summary.values()
            for error in (*detail.get("errors", ()), *detail.get("warnings", ()))
        ]
    )
    typer.echo(
        json.dumps(summary, sort_keys=True)
        if json_output
        else _render_prune_plain(result, family)
    )
    if errors:
        typer.echo("\n".join(errors), err=True)
    if result.failed or errors:
        raise typer.Exit(code=1)


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

    try:
        outcome = commands.cmd_status(
            all=all_tasks,
            status=status_filter,
            watch=watch,
            interval=interval,
            context=context_dir,
        )
        if watch:
            stream_outcome = cast(commands.CommandStream[commands.TaskEvent], outcome)
            try:
                for event in stream_outcome:
                    if json_output:
                        typer.echo(json.dumps(_jsonable(event), ensure_ascii=False))
                    else:
                        payload = event.payload
                        name = payload.get("name") or event.tid
                        status_value = payload.get("status") or "unknown"
                        typer.echo(
                            f"{format_message_id(event.timestamp)} {event.tid:<19} "
                            f"{status_value:<10} {event.event_type:<16} {name}"
                        )
            finally:
                stream_outcome.close()
        else:
            _render_status_snapshot(
                cast(commands.SystemStatusSnapshot, outcome),
                json_output=json_output,
            )
    except KeyboardInterrupt:
        return
    except commands.WeftError as exc:
        _command_exit(exc)


@app.command("result")
def result_command(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-368] exception
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

    if stream and json_output:
        typer.echo("weft result: --stream cannot be used with --json", err=True)
        raise typer.Exit(code=2)
    try:
        outcome = commands.cmd_result(
            tid,
            all=all_results,
            peek=peek,
            timeout=timeout,
            stream=stream,
            context=context_dir,
        )
        if stream:
            event_stream = cast(commands.CommandStream[commands.TaskEvent], outcome)
            try:
                for event in event_stream:
                    payload = event.payload
                    if event.event_type in {"stdout", "stderr"}:
                        chunk = payload.get("data")
                    elif event.event_type == "result":
                        chunk = (
                            payload.get("stderr")
                            if error_stream and payload.get("stderr") is not None
                            else payload.get("value")
                        )
                        if error_stream and isinstance(chunk, dict):
                            chunk = chunk.get("stderr", chunk)
                    else:
                        chunk = None
                    if chunk is not None:
                        typer.echo(
                            json.dumps(chunk, ensure_ascii=False)
                            if isinstance(chunk, (dict, list))
                            else str(chunk),
                            nl=False,
                        )
            finally:
                event_stream.close()
            return
        if all_results:
            results = cast(tuple[commands.TaskResult, ...], outcome)
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "results": [
                                {
                                    "tid": result.tid,
                                    "result": result.value,
                                }
                                for result in results
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                for result in results:
                    value = (
                        result.stderr
                        if error_stream and result.stderr is not None
                        else result.value
                    )
                    typer.echo(f"{result.tid}: {value}")
            return
        exit_code = _render_task_result(
            cast(commands.TaskResult, outcome),
            json_output=json_output,
            error_stream=error_stream,
        )
    except commands.WeftError as exc:
        _command_exit(exc)
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
        typer.Option(
            "--name",
            help=(
                "Explicit task name. For persistent runs, also claims the named "
                "runtime endpoint"
            ),
            metavar="TEXT",
        ),
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
    if help_flag and spec is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)

    raw_command_tokens = list(command or ())
    command_tokens = [] if spec is not None else raw_command_tokens
    if interactive and json_output:
        typer.echo("--json is not supported together with --interactive", err=True)
        raise typer.Exit(code=2)
    try:
        stdin_text = None if help_flag else read_run_stdin()
        outcome = commands.cmd_run(
            command_tokens,
            spec_args=raw_command_tokens if spec is not None else (),
            spec=spec,
            pipeline=pipeline,
            input=pipeline_input,
            function=function,
            arg=tuple(arg or ()),
            kw=tuple(kw or ()),
            env=tuple(env or ()),
            name=name,
            interactive=interactive,
            stream_output=stream_output,
            timeout=timeout,
            memory=memory,
            cpu=cpu,
            tag=tuple(tag or ()),
            context=context_dir,
            wait=wait,
            continuous=continuous,
            autostart=autostart,
            describe=help_flag,
            run_input_stdin_text=stdin_text if spec is not None else None,
            work_input_text=stdin_text if spec is None else None,
        )
    except (commands.WeftError, ValueError) as exc:
        if type(exc).__name__ == "RunResolutionError":
            raise typer.Exit(code=2) from exc
        _command_exit(exc)
    if help_flag:
        render_run_description(ctx, cast(commands.RunSpecDescription, outcome))
        raise typer.Exit(code=0)
    if interactive and wait:
        drive_interactive_session(cast(commands.RunSession, outcome), stdin_text)
    execution = (
        consume_run_session(cast(commands.RunSession, outcome))
        if wait
        else cast(commands.RunExecutionResult, outcome)
    )
    exit_code = render_run_result(
        execution,
        wait=wait,
        json_output=json_output,
        verbose=verbose,
        suppress_result_value=interactive,
    )
    raise typer.Exit(code=exit_code)


@manager_app.command("start")
def manager_start_command(
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            help="Send STOP to the active manager before starting a replacement",
        ),
    ] = False,
) -> None:
    try:
        result = commands.cmd_manager_start(context=context, replace=replace)
    except commands.WeftError as exc:
        _command_exit(exc, usage_code=1)
    if result.started_here:
        typer.echo(f"Started manager {result.tid}")
    else:
        typer.echo(f"Manager {result.tid} already running")


@manager_app.command("serve")
def manager_serve_command(
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
    level: Annotated[
        str | None,
        typer.Option(
            "--level",
            help="Operational log level: off, info, debug, or trace",
        ),
    ] = None,
    log_interval: Annotated[
        float | None,
        typer.Option(
            "--log-interval",
            help="Seconds between repeated operational log events",
        ),
    ] = None,
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            help="Send STOP to the active manager before serving a replacement",
        ),
    ] = False,
) -> None:
    """Run the canonical manager in the foreground."""

    try:
        commands.cmd_manager_serve(
            context=context,
            level=level,
            log_interval=log_interval,
            replace=replace,
        )
    except commands.WeftError as exc:
        _command_exit(exc, usage_code=1)


@manager_app.command("stop")
def manager_stop_command(
    tid: Annotated[
        str | None,
        typer.Argument(help="Manager TID. Defaults to the active manager."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Force terminate the manager process"),
    ] = False,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Seconds to wait for graceful stop"),
    ] = MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
) -> None:
    try:
        commands.cmd_manager_stop(
            tid,
            force=force,
            timeout=timeout,
            context=context,
        )
    except commands.WeftError as exc:
        _command_exit(exc, usage_code=1)


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
    diagnostic: Annotated[
        bool,
        typer.Option(
            "--diagnostic",
            help="Show explicit registry liveness and canonical-owner diagnostics",
        ),
    ] = False,
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
) -> None:
    try:
        records = commands.cmd_manager_list(
            all=include_stopped,
            diagnostic=diagnostic,
            context=context,
        )
    except commands.WeftError as exc:
        _command_exit(exc, usage_code=1)
    if json_output:
        typer.echo(
            json.dumps([_manager_json_record(record) for record in records], indent=2)
        )
    elif not records:
        typer.echo("No registered managers")
    elif diagnostic:
        lines = ["TID        STATUS    LIVE      CANONICAL  PROOF               NAME"]
        for record in sorted(records, key=lambda item: item.tid):
            canonical = "yes" if record.canonical is True else "no"
            lines.append(
                f"{record.tid}  {record.status:<9} "
                f"{(record.liveness or 'unknown'):<9} {canonical:<10} "
                f"{(record.proof_source or ''):<19} {record.name}"
            )
        typer.echo("\n".join(lines))
    else:
        lines = ["TID        STATUS    NAME"]
        lines.extend(
            f"{record.tid}  {record.status:<9} {record.name}"
            for record in sorted(records, key=lambda item: item.tid)
        )
        typer.echo("\n".join(lines))


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
    try:
        result = commands.cmd_manager_status(tid, context=context)
    except commands.WeftError as exc:
        _command_exit(exc, usage_code=1)
    if json_output:
        typer.echo(json.dumps(_manager_json_record(result), indent=2))
        return
    lines = [
        f"Manager {result.tid}",
        f"Name: {result.name}",
        f"Status: {result.status}",
    ]
    if result.runtime_handle is not None:
        lines.append(f"Runtime: {json.dumps(result.runtime_handle, sort_keys=True)}")
    typer.echo("\n".join(lines))


@system_app.command("dump")
def dump_command(
    output: Annotated[
        str | None,
        typer.Option(
            "--output",
            "-o",
            help=f"Output file path (default: {default_export_path_help})",
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
    try:
        result = commands.cmd_system_dump(output=output, context=context_dir)
    except commands.WeftError as exc:
        _command_exit(exc, usage_code=1)
    message = f"Exported {result.messages} messages from {result.queues} queues"
    if result.aliases:
        message += f" and {result.aliases} aliases"
    if result.omitted_claimed_messages:
        message += (
            f"; omitted {result.omitted_claimed_messages} claimed messages from "
            f"{result.omitted_claimed_queues} queues"
        )
    typer.echo(f"{message} to {result.path}")


@system_app.command("builtins")
def system_builtins_command(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output builtin inventory as JSON"),
    ] = False,
) -> None:
    """List the builtin TaskSpecs shipped with Weft."""

    records = commands.cmd_system_builtins()
    if json_output:
        typer.echo(
            json.dumps(
                [
                    {
                        "type": "task",
                        "name": record.name,
                        "description": record.description,
                        "category": record.category,
                        "function_target": record.function_target,
                        "supported_platforms": list(record.supported_platforms),
                        "path": str(record.path),
                        "source": record.source,
                    }
                    for record in records
                ],
                ensure_ascii=False,
            )
        )
        return
    for index, record in enumerate(records):
        if index:
            typer.echo()
        typer.echo(f"task: {record.name}")
        if record.category:
            typer.echo(f"  Category: {record.category}")
        if record.description:
            typer.echo(f"  Description: {record.description}")
        if record.function_target:
            typer.echo(f"  Target: {record.function_target}")
        if record.supported_platforms:
            typer.echo(f"  Platforms: {', '.join(record.supported_platforms)}")


@system_app.command("load")
def load_command(
    input_file: Annotated[
        str | None,
        typer.Option(
            "--input",
            help=f"Input file path (default: {default_export_path_help})",
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
    try:
        result = commands.cmd_system_load(
            input=input_file,
            dry_run=dry_run,
            context=context_dir,
        )
    except commands.WeftError as exc:
        _command_exit(exc)
    typer.echo(result.message)


app.add_typer(queue_app, name="queue")
app.add_typer(manager_app, name="manager")
app.add_typer(spec_app, name="spec")
app.add_typer(task_app, name="task")
app.add_typer(system_app, name="system")


if __name__ == "__main__":
    app()
