"""CLI entry point for Weft."""

from pathlib import Path
from typing import Annotated

import typer

from ._constants import PROG_NAME, __version__
from .commands import handle_validate_taskspec

app = typer.Typer(
    name=PROG_NAME,
    help="Weft: The Multi-Agent Weaving Toolkit",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)


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
    handle_validate_taskspec(file)


if __name__ == "__main__":
    app()
