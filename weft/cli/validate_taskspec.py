"""CLI adapter for TaskSpec validation.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.3]
- docs/specifications/09-Implementation_Plan.md [IP-1], [IP-1.0]
- docs/specifications/10-CLI_Interface.md [CLI-1.4.1]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from weft.commands import specs as spec_cmd
from weft.commands.types import SpecValidationResult

console = Console()

_failure_headings = {
    "schema": "TaskSpec validation failed",
    "parameterization": "Parameterization validation failed",
    "run_input": "Run-input validation failed",
    "environment_profile": "Environment profile validation failed",
    "runner": "Runner validation failed",
    "agent_runtime": "Agent runtime validation failed",
    "tool_profile": "Tool profile validation failed",
}
_preflight_stage_order = (
    "environment_profile",
    "runner",
    "agent_runtime",
    "tool_profile",
)


def cmd_validate_taskspec(
    file_path: Path,
    *,
    load_runner: bool = False,
    preflight: bool = False,
) -> int:
    """Validate and render one TaskSpec source.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-1.4.1]
    """

    load_runner = load_runner or preflight
    resolved = _resolve_taskspec_source(file_path)
    if resolved is None:
        return 1
    resolved_path, bundle_root = resolved

    try:
        json_content = resolved_path.read_text()
    except OSError as exc:
        console.print(f"[red]Error reading file:[/red] {exc}")
        return 1

    result = spec_cmd.validate_task_spec_text(
        json_content,
        bundle_root=bundle_root,
        load_runner=load_runner,
        preflight=preflight,
    )
    failed_stage = next(iter(result.errors_by_stage), None)
    if failed_stage == "schema":
        _display_failure(result, failed_stage)
        return 1

    console.print("[green]✓[/green] TaskSpec is valid")
    _display_completed_preflight_stages(
        result,
        failed_stage=failed_stage,
        load_runner=load_runner,
        preflight=preflight,
    )
    if failed_stage is not None:
        _display_failure(result, failed_stage)
        return 1

    if result.payload is not None:
        _display_taskspec_summary(result.payload)
    return 0


def _resolve_taskspec_source(file_path: Path) -> tuple[Path, Path | None] | None:
    looks_like_explicit_path = (
        file_path.suffix == ".json"
        or file_path.is_absolute()
        or len(file_path.parts) > 1
    )
    if looks_like_explicit_path:
        if not file_path.exists():
            console.print(f"[red]Error:[/red] File not found: {file_path}")
            return None
        if file_path.is_dir():
            resolved_path = file_path / "taskspec.json"
            if not resolved_path.is_file():
                console.print(f"[red]Error:[/red] File not found: {resolved_path}")
                return None
            return resolved_path, file_path
        return (
            file_path,
            file_path.parent if file_path.name == "taskspec.json" else None,
        )

    try:
        resolved = spec_cmd.resolve_spec_reference(
            file_path,
            spec_type=spec_cmd.SPEC_TYPE_TASK,
        )
    except Exception as exc:  # pragma: no cover - validation command boundary
        console.print(f"[red]Error reading file:[/red] {exc}")
        return None
    return resolved.path, resolved.bundle_root


def _display_completed_preflight_stages(
    result: SpecValidationResult,
    *,
    failed_stage: str | None,
    load_runner: bool,
    preflight: bool,
) -> None:
    if not load_runner or result.payload is None:
        return

    if failed_stage is None:
        failure_index = len(_preflight_stage_order)
    elif failed_stage in _preflight_stage_order:
        failure_index = _preflight_stage_order.index(failed_stage)
    else:
        failure_index = 0
    is_agent = _is_agent(result.payload)
    supports_tool_profile = _agent_runtime(result.payload) == "provider_cli"
    labels = {
        "environment_profile": (
            "Environment profile preflight passed"
            if preflight
            else "Environment profile is available"
        ),
        "runner": "Runner preflight passed" if preflight else "Runner is available",
        "agent_runtime": (
            "Agent runtime preflight passed"
            if preflight
            else "Agent runtime is available"
        ),
        "tool_profile": (
            "Tool profile preflight passed"
            if preflight
            else "Tool profile is available"
        ),
    }
    for index, stage in enumerate(_preflight_stage_order):
        if index >= failure_index:
            break
        if stage == "agent_runtime" and not is_agent:
            continue
        if stage == "tool_profile" and not supports_tool_profile:
            continue
        console.print(f"[green]✓[/green] {labels[stage]}")


def _display_failure(result: SpecValidationResult, stage: str) -> None:
    heading = _failure_headings.get(stage, "TaskSpec validation failed")
    console.print(f"[red]✗[/red] {heading}\n")
    _display_validation_errors(result.errors_by_stage[stage])


def _is_agent(payload: dict[str, Any]) -> bool:
    spec = payload.get("spec")
    return isinstance(spec, dict) and spec.get("type") == "agent"


def _agent_runtime(payload: dict[str, Any]) -> str | None:
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return None
    agent = spec.get("agent")
    if not isinstance(agent, dict):
        return None
    runtime = agent.get("runtime")
    return runtime if isinstance(runtime, str) else None


def _display_taskspec_summary(data: dict[str, Any]) -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-103] exception
    """Display a summary of the validated TaskSpec."""
    table = Table(title="TaskSpec Summary", show_header=False)
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    table.add_row("TID", data.get("tid", "N/A"))
    table.add_row("Name", data.get("name", "N/A"))
    if "description" in data:
        table.add_row("Description", data["description"])

    spec = data.get("spec")
    if isinstance(spec, dict):
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
            table.add_row("Run input", str(run_input.get("adapter_ref", "N/A")))
        parameterization = spec.get("parameterization")
        if isinstance(parameterization, dict):
            table.add_row(
                "Parameterization",
                str(parameterization.get("adapter_ref", "N/A")),
            )

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
