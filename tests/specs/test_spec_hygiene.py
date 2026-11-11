"""High-signal hygiene checks for spec/code traceability."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer
import typer.core

from weft.cli.app import app

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "docs" / "specifications"
QUICK_REFERENCE_PATH = SPEC_DIR / "00-Quick_Reference.md"

pytestmark = [pytest.mark.shared]


def test_normative_specs_do_not_encode_plan_status_labels() -> None:
    """Plan status belongs in plan metadata, not normative spec prose."""

    banned = re.compile(
        r"\b(?:active|draft|completed)\s+(?:[\w-]+\s+){0,5}plan\b|"
        r"\bsuperseded\s+draft\b",
        re.IGNORECASE,
    )
    violations: list[str] = []
    for path in sorted(SPEC_DIR.glob("*.md")):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if banned.search(line):
                violations.append(
                    f"{path.relative_to(REPO_ROOT)}:{line_number}: {line}"
                )

    assert not violations, "status-sensitive plan wording in specs:\n" + "\n".join(
        violations
    )


def test_shipped_python_code_does_not_cite_exploratory_django_spec() -> None:
    """Current shipped code should cite current specs, not framework proposals."""

    violations: list[str] = []
    for root_name in ("weft", "extensions"):
        root = REPO_ROOT / root_name
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "13C-Using_Weft_With_Django.md" in text:
                violations.append(path.relative_to(REPO_ROOT).as_posix())

    assert not violations, (
        "shipped Python code cites exploratory Django spec:\n" + "\n".join(violations)
    )


def _expand_documented_command_token(token: str) -> set[str]:
    parts = token.strip().split()
    if not parts:
        return set()
    if len(parts) == 1:
        return {parts[0]}
    prefix = " ".join(parts[:-1])
    leaves = [leaf.strip() for leaf in parts[-1].split("/") if leaf.strip()]
    return {f"{prefix} {leaf}" for leaf in leaves}


def _parse_quick_reference_cli_surface(text: str) -> dict[str, set[str]]:
    section_match = re.search(
        r"^## CLI Surface\s*$([\s\S]*?)(?=^## |\Z)",
        text,
        re.MULTILINE,
    )
    assert section_match is not None, "00-Quick_Reference.md is missing ## CLI Surface"
    rows: dict[str, set[str]] = {}
    for line in section_match.group(1).splitlines():
        stripped = line.strip()
        if not stripped.startswith("| `weft"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        assert len(cells) == 2, f"unexpected CLI Surface row: {line}"
        surface = cells[0].strip("`").strip()
        commands: set[str] = set()
        for token in re.findall(r"`([^`]+)`", cells[1]):
            commands |= _expand_documented_command_token(token)
        rows[surface] = commands
    return rows


def _collect_nested_typer_commands(
    group: typer.core.TyperGroup,
    prefix: str = "",
) -> set[str]:
    collected: set[str] = set()
    for name, subcommand in group.commands.items():
        qualified = f"{prefix} {name}".strip()
        if isinstance(subcommand, typer.core.TyperGroup):
            collected |= _collect_nested_typer_commands(subcommand, qualified)
        else:
            collected.add(qualified)
    return collected


def _expected_cli_surface(root_command: typer.core.TyperGroup) -> dict[str, set[str]]:
    expected: dict[str, set[str]] = {"weft": set(root_command.commands)}
    for name, subcommand in root_command.commands.items():
        if isinstance(subcommand, typer.core.TyperGroup):
            expected[f"weft {name}"] = _collect_nested_typer_commands(subcommand)
    return expected


def test_quick_reference_cli_surface_matches_typer_app() -> None:
    """00-Quick_Reference.md ## CLI Surface must list every shipped CLI verb."""

    documented = _parse_quick_reference_cli_surface(
        QUICK_REFERENCE_PATH.read_text(encoding="utf-8")
    )
    root_command = typer.main.get_command(app)
    assert isinstance(root_command, typer.core.TyperGroup)

    assert documented == _expected_cli_surface(root_command)


def test_quick_reference_cli_surface_detects_nested_subcommand_drift() -> None:
    """Nested subcommands like `weft queue alias remove` must be checked, not collapsed."""

    spec_text = QUICK_REFERENCE_PATH.read_text(encoding="utf-8")
    documented = _parse_quick_reference_cli_surface(spec_text)
    mutated_text = spec_text.replace("`alias add/list/remove`", "`alias add/list`", 1)
    assert mutated_text != spec_text
    assert _parse_quick_reference_cli_surface(mutated_text) != documented

    root_command = typer.main.get_command(app)
    assert isinstance(root_command, typer.core.TyperGroup)
    queue_group = root_command.commands["queue"]
    assert isinstance(queue_group, typer.core.TyperGroup)
    alias_group = queue_group.commands["alias"]
    assert isinstance(alias_group, typer.core.TyperGroup)
    original_alias_commands = dict(alias_group.commands)
    try:
        alias_group.commands = {
            key: value
            for key, value in original_alias_commands.items()
            if key != "remove"
        }
        assert _expected_cli_surface(root_command) != documented
    finally:
        alias_group.commands = original_alias_commands
