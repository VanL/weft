"""Command handlers for the Weft CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from weft.core.taskspec import validate_taskspec

console = Console()


def cmd_validate_taskspec(file_path: Path) -> None:
    """Validate a TaskSpec JSON file.

    Args:
        file_path: Path to the TaskSpec JSON file to validate
    """
    # Check if file exists
    if not file_path.exists():
        console.print(f"[red]Error:[/red] File not found: {file_path}")
        sys.exit(1)

    # Read the file
    try:
        json_content = file_path.read_text()
    except Exception as e:
        console.print(f"[red]Error reading file:[/red] {e}")
        sys.exit(1)

    # Validate the TaskSpec
    is_valid, errors = validate_taskspec(json_content)

    if is_valid:
        console.print("[green]✓[/green] TaskSpec is valid")

        # Optionally show a preview of the parsed TaskSpec
        try:
            data = json.loads(json_content)
            _display_taskspec_summary(data)
        except Exception:
            pass  # Silent fail on preview
    else:
        console.print("[red]✗[/red] TaskSpec validation failed\n")
        _display_validation_errors(errors)
        sys.exit(1)


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
        if spec.get("type") == "function":
            table.add_row("Function", spec.get("function_target", "N/A"))
        elif spec.get("type") == "command":
            target = spec.get("process_target", [])
            table.add_row("Command", " ".join(target) if target else "N/A")

    console.print()
    console.print(table)


def _display_validation_errors(errors: dict[str, str]) -> None:
    """Display validation errors in a formatted table."""
    table = Table(title="Validation Errors", show_header=True)
    table.add_column("Field", style="yellow")
    table.add_column("Error", style="red")

    for field, error in errors.items():
        table.add_row(field, error)

    console.print(table)
