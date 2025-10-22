"""CLI entry point for Weft."""

from pathlib import Path
from typing import Annotated

import typer

from ._constants import PROG_NAME, __version__
from .commands import cmd_init, cmd_status, cmd_validate_taskspec
from .commands import queue as queue_cmd
from .commands import worker as worker_cmd
from .commands.run import cmd_run

app = typer.Typer(
    name=PROG_NAME,
    help="Weft: The Multi-Agent Weaving Toolkit",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)

queue_app = typer.Typer(help="Queue passthrough operations")
worker_app = typer.Typer(help="Worker lifecycle management")


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
) -> None:
    exit_code, payload = queue_cmd.read_command(
        name,
        all_messages=all_messages,
        with_timestamps=timestamps,
        json_output=json_output,
    )
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@queue_app.command("write")
def queue_write(
    name: Annotated[str, typer.Argument(help="Queue name to write to")],
    message: str | None = typer.Argument(None, help="Message to write"),
) -> None:
    try:
        queue_cmd.write_command(name, message)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    raise typer.Exit(code=0)


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
) -> None:
    exit_code, payload = queue_cmd.peek_command(
        name,
        all_messages=all_messages,
        with_timestamps=timestamps,
        json_output=json_output,
    )
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@queue_app.command("move")
def queue_move(
    source: Annotated[str, typer.Argument(help="Source queue name")],
    destination: Annotated[str, typer.Argument(help="Destination queue name")],
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", help="Maximum number of messages to move"),
    ] = None,
) -> None:
    exit_code, payload = queue_cmd.move_command(source, destination, limit=limit)
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@queue_app.command("list")
def queue_list(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output queue information as JSON"),
    ] = False,
) -> None:
    exit_code, payload = queue_cmd.list_command(json_output=json_output)
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


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
) -> None:
    for line in queue_cmd.watch_command(
        name,
        interval=interval,
        max_messages=limit,
        with_timestamps=timestamps,
        json_output=json_output,
    ):
        typer.echo(line)
    raise typer.Exit(code=0)


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


@app.command("validate-taskspec")
def validate_taskspec(
    file: Annotated[
        Path,
        typer.Argument(
            help="Path to the TaskSpec JSON file to validate",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ],
) -> None:
    """Validate a TaskSpec JSON file."""
    cmd_validate_taskspec(file)


@app.command("init")
def init(
    directory: Annotated[
        Path,
        typer.Argument(
            help="Directory where the project should be initialised",
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
) -> None:
    """Initialise a new Weft project."""

    exit_code = cmd_init(directory, quiet=quiet)
    raise typer.Exit(code=exit_code)


@app.command("status")
def status_command(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit broker status as JSON"),
    ] = False,
    context_dir: Annotated[
        Path | None,
        typer.Option(
            "--context",
            help="Directory to treat as the Weft context (defaults to discovery)",
        ),
    ] = None,
) -> None:
    """Display the underlying SimpleBroker status metrics."""

    exit_code, payload = cmd_status(
        json_output=json_output,
        spec_context=context_dir,
    )

    if payload:
        typer.echo(payload, err=exit_code != 0)
    raise typer.Exit(code=exit_code)


@app.command("run")
def run_command(
    command: Annotated[
        list[str] | None,
        typer.Argument(
            help="Command to execute (omit when using --function or --spec)",
            show_default=False,
        ),
    ] = None,
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            help="Execute an existing TaskSpec JSON file",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
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
        typer.Option("--monitor", help="Run TaskSpec in monitor mode (with --spec)"),
    ] = False,
    continuous: Annotated[
        bool,
        typer.Option(
            "--continuous/--once", help="Continuously process messages for --spec"
        ),
    ] = False,
) -> None:
    """Execute a command, function, or TaskSpec using the built-in runner."""

    exit_code = cmd_run(
        command or [],
        spec=spec,
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
        once=not continuous,
    )
    raise typer.Exit(code=exit_code)


@worker_app.command("start")
def worker_start_command(
    taskspec: Annotated[
        Path,
        typer.Argument(
            help="Path to worker TaskSpec JSON",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    foreground: Annotated[
        bool,
        typer.Option("--foreground", help="Run the worker in the foreground"),
    ] = False,
) -> None:
    exit_code, payload = worker_cmd.start_command(taskspec, foreground=foreground)
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@worker_app.command("stop")
def worker_stop_command(
    tid: Annotated[str, typer.Argument(help="Worker TID")],
    force: Annotated[
        bool,
        typer.Option("--force", help="Force terminate the worker process"),
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
    exit_code, payload = worker_cmd.stop_command(
        tid=tid, force=force, timeout=timeout, context_path=context
    )
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@worker_app.command("list")
def worker_list_command(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output worker statuses as JSON"),
    ] = False,
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
) -> None:
    exit_code, payload = worker_cmd.list_command(
        json_output=json_output, context_path=context
    )
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


@worker_app.command("status")
def worker_status_command(
    tid: Annotated[str, typer.Argument(help="Worker TID")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output worker status as JSON"),
    ] = False,
    context: Annotated[
        Path | None,
        typer.Option("--context", help="Weft project directory"),
    ] = None,
) -> None:
    exit_code, payload = worker_cmd.status_command(
        tid=tid, json_output=json_output, context_path=context
    )
    if payload:
        typer.echo(payload)
    raise typer.Exit(code=exit_code)


app.add_typer(queue_app, name="queue")
app.add_typer(worker_app, name="worker")


if __name__ == "__main__":
    app()
