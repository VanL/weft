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

from weft.commands import specs as spec_cmd
from weft.core.agents.validation import (
    validate_taskspec_agent_runtime,
    validate_taskspec_agent_tool_profile,
)
from weft.core.runner_validation import (
    validate_taskspec_runner,
    validate_taskspec_runner_environment,
)
from weft.core.spec_parameterization import validate_parameterization_adapter
from weft.core.spec_run_input import validate_run_input_adapter
from weft.core.taskspec import (
    apply_bundle_root_to_taskspec_payload,
    bundle_root_from_taskspec_payload,
    validate_taskspec,
)

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

    looks_like_explicit_path = (
        file_path.suffix == ".json"
        or file_path.is_absolute()
        or len(file_path.parts) > 1
    )
    bundle_root: Path | None
    if looks_like_explicit_path:
        if not file_path.exists():
            console.print(f"[red]Error:[/red] File not found: {file_path}")
            return 1
        if file_path.is_dir():
            resolved_path = file_path / "taskspec.json"
            if not resolved_path.is_file():
                console.print(f"[red]Error:[/red] File not found: {resolved_path}")
                return 1
            bundle_root = file_path
        else:
            resolved_path = file_path
            bundle_root = (
                file_path.parent if file_path.name == "taskspec.json" else None
            )
    else:
        try:
            resolved = spec_cmd.resolve_spec_reference(
                file_path,
                spec_type=spec_cmd.SPEC_TYPE_TASK,
            )
        except Exception as e:
            console.print(f"[red]Error reading file:[/red] {e}")
            return 1
        resolved_path = resolved.path
        bundle_root = resolved.bundle_root

    try:
        json_content = resolved_path.read_text()
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
            if isinstance(data, dict):
                apply_bundle_root_to_taskspec_payload(data, bundle_root)
                try:
                    _validate_taskspec_parameterization(data)
                except Exception as exc:
                    console.print("[red]✗[/red] Parameterization validation failed\n")
                    _display_validation_errors({"parameterization": str(exc)})
                    return 1
                try:
                    _validate_taskspec_run_input(data)
                except Exception as exc:
                    console.print("[red]✗[/red] Run-input validation failed\n")
                    _display_validation_errors({"run_input": str(exc)})
                    return 1
            if load_runner:
                is_agent = data.get("spec", {}).get("type") == "agent"
                agent_runtime_name = None
                if is_agent:
                    agent_payload = data.get("spec", {}).get("agent", {})
                    if isinstance(agent_payload, dict):
                        agent_runtime_name = agent_payload.get("runtime")
                supports_tool_profile = agent_runtime_name == "provider_cli"
                try:
                    validate_taskspec_runner_environment(data)
                except Exception as exc:
                    console.print(
                        "[red]✗[/red] Environment profile validation failed\n"
                    )
                    _display_validation_errors({"environment_profile": str(exc)})
                    return 1

                if load_runner:
                    if preflight:
                        console.print(
                            "[green]✓[/green] Environment profile preflight passed"
                        )
                    else:
                        console.print(
                            "[green]✓[/green] Environment profile is available"
                        )

                try:
                    validate_taskspec_runner(
                        data,
                        load_runner=load_runner,
                        preflight=preflight,
                    )
                except Exception as exc:
                    console.print("[red]✗[/red] Runner validation failed\n")
                    _display_validation_errors({"runner": str(exc)})
                    return 1

                if preflight:
                    console.print("[green]✓[/green] Runner preflight passed")
                else:
                    console.print("[green]✓[/green] Runner is available")

                try:
                    validate_taskspec_agent_runtime(
                        data,
                        load_runtime=load_runner,
                        preflight=preflight,
                    )
                except Exception as exc:
                    if is_agent:
                        console.print("[red]✗[/red] Agent runtime validation failed\n")
                        _display_validation_errors({"agent_runtime": str(exc)})
                        return 1

                if is_agent:
                    if preflight:
                        console.print("[green]✓[/green] Agent runtime preflight passed")
                    else:
                        console.print("[green]✓[/green] Agent runtime is available")
                if supports_tool_profile:
                    try:
                        validate_taskspec_agent_tool_profile(
                            data,
                            load_runtime=load_runner,
                            preflight=preflight,
                        )
                    except Exception as exc:
                        console.print("[red]✗[/red] Tool profile validation failed\n")
                        _display_validation_errors({"tool_profile": str(exc)})
                        return 1

                    if preflight:
                        console.print("[green]✓[/green] Tool profile preflight passed")
                    else:
                        console.print("[green]✓[/green] Tool profile is available")
            _display_taskspec_summary(data)
        except Exception as exc:
            console.print("[red]✗[/red] TaskSpec validation failed\n")
            _display_validation_errors({"taskspec": str(exc)})
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
        run_input = spec.get("run_input")
        if isinstance(run_input, dict):
            adapter_ref = run_input.get("adapter_ref", "N/A")
            table.add_row("Run input", str(adapter_ref))
        parameterization = spec.get("parameterization")
        if isinstance(parameterization, dict):
            adapter_ref = parameterization.get("adapter_ref", "N/A")
            table.add_row("Parameterization", str(adapter_ref))

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


def _validate_taskspec_run_input(data: dict[str, Any]) -> None:
    """Validate the optional spec.run_input adapter ref."""
    run_input = data.get("spec", {}).get("run_input")
    if not isinstance(run_input, dict):
        return
    adapter_ref = run_input.get("adapter_ref")
    if not isinstance(adapter_ref, str):
        return
    validate_run_input_adapter(
        adapter_ref,
        bundle_root=bundle_root_from_taskspec_payload(data),
    )


def _validate_taskspec_parameterization(data: dict[str, Any]) -> None:
    """Validate the optional spec.parameterization adapter ref."""
    parameterization = data.get("spec", {}).get("parameterization")
    if not isinstance(parameterization, dict):
        return
    adapter_ref = parameterization.get("adapter_ref")
    if not isinstance(adapter_ref, str):
        return
    validate_parameterization_adapter(
        adapter_ref,
        bundle_root=bundle_root_from_taskspec_payload(data),
    )
