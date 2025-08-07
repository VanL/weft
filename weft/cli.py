"""CLI entry point for Weft."""

from typing import Annotated

import typer

from ._constants import PROG_NAME, __version__

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


if __name__ == "__main__":
    app()
