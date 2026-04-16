"""Tests for command-layer queue construction boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMANDS_DIR = REPO_ROOT / "weft" / "commands"
DIRECT_QUEUE_ALLOWLIST_REASONS = {
    "weft/commands/interactive.py": (
        "InteractiveStreamClient owns its task-local inbox queue and does not "
        "currently accept a WeftContext."
    ),
}

pytestmark = [pytest.mark.shared]


def _direct_queue_callers() -> set[str]:
    callers: set[str] = set()
    for path in sorted(COMMANDS_DIR.rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Queue"
            for node in ast.walk(module)
        ):
            callers.add(path.relative_to(REPO_ROOT).as_posix())
    return callers


def test_command_layer_direct_queue_construction_is_explicitly_bounded() -> None:
    assert _direct_queue_callers() == set(DIRECT_QUEUE_ALLOWLIST_REASONS)


def test_command_layer_queue_allowlist_entries_have_reasons() -> None:
    assert DIRECT_QUEUE_ALLOWLIST_REASONS
    assert all(reason.strip() for reason in DIRECT_QUEUE_ALLOWLIST_REASONS.values())
