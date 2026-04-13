"""Command handlers for the Weft CLI.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.4.1]
- docs/specifications/01-Core_Components.md [CC-3.3]
- docs/specifications/02-TaskSpec.md [TS-1.3]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from weft.core.runner_validation import validate_taskspec_runner
from weft.core.taskspec import validate_taskspec

console = Console()


def cmd_validate_taskspec(
    file_path: Path,
    *,
    load_runner: bool = False,
    preflight: bool = False,
) -> int:
    """Validate a TaskSpec JSON file.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-1.4.1]

    Args:
        file_path: Path to the TaskSpec JSON file to validate.
        load_runner: Resolve the configured runner plugin.
        preflight: Run runner runtime-availability checks.
    """
    if preflight:
        load_runner = True

    # Check if file exists
    if not file_path.exists():
        console.print(f"[red]Error:[/red] File not found: {file_path}")
        return 1

    # Read the file
    try:
        json_content = file_path.read_text()
    except Exception as e:
        console.print(f"[red]Error reading file:[/red] {e}")
        return 1

    # Validate the TaskSpec
    is_valid, errors = validate_taskspec(json_content)

    if is_valid:
        console.print("[green]✓[/green] TaskSpec is valid")

        # Optionally show a preview of the parsed TaskSpec
        try:
            data = json.loads(json_content)
            if load_runner:
                validate_taskspec_runner(
                    data,
                    load_runner=load_runner,
                    preflight=preflight,
                )
                if preflight:
                    console.print("[green]✓[/green] Runner preflight passed")
                else:
                    console.print("[green]✓[/green] Runner is available")
            _display_taskspec_summary(data)
        except Exception as exc:
            if load_runner:
                console.print("[red]✗[/red] Runner validation failed\n")
                _display_validation_errors({"runner": str(exc)})
                return 1
    else:
        console.print("[red]✗[/red] TaskSpec validation failed\n")
        _display_validation_errors(errors)
        return 1
    return 0


def _display_taskspec_summary(data: dict[str, Any]) -> None:
    """Display a summary of the validated TaskSpec."""
    table = Table(title="TaskSpec Summary", show_header=False)
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    # Basic info
    table.add_row("TID", data.get("tid", "N/A"))
    table.add_row("Name", data.get("name", "N/A"))
    if "description" in data:
        table.add_row("Description", data["description"])

    # Spec info
    if "spec" in data:
        spec = data["spec"]
        table.add_row("Type", spec.get("type", "N/A"))
        runner = spec.get("runner") or {}
        if isinstance(runner, dict):
            table.add_row("Runner", str(runner.get("name", "host")))
        if spec.get("type") == "function":
            table.add_row("Function", spec.get("function_target", "N/A"))
        elif spec.get("type") == "command":
            target = spec.get("process_target")
            args = spec.get("args") or []
            if isinstance(target, str):
                command = (
                    " ".join([target, *[str(arg) for arg in args]]) if args else target
                )
                table.add_row("Command", command)
            else:
                table.add_row("Command", "N/A")
        elif spec.get("type") == "agent":
            agent = spec.get("agent") or {}
            if isinstance(agent, dict):
                table.add_row("Runtime", str(agent.get("runtime", "N/A")))
                table.add_row("Model", str(agent.get("model", "N/A")))
            else:
                table.add_row("Runtime", "N/A")
                table.add_row("Model", "N/A")

    console.print()
    console.print(table)


def _display_validation_errors(errors: dict[str, str]) -> None:
    """Display validation errors in a formatted table."""
    table = Table(title="Validation Errors", show_header=True)
    table.add_column("Field", style="yellow")
    table.add_column("Error", style="red")

    for field, error in errors.items():
        table.add_row(field, escape(error))

    console.print(table)
