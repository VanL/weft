"""Static ownership checks for the private liveness subsystem."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from weft._constants import (
    RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS,
    WEFT_TID_MAPPINGS_QUEUE,
)

pytestmark = [pytest.mark.shared]

ROOT = Path(__file__).resolve().parents[2]
WEFT = ROOT / "weft"
LIVENESS = WEFT / "liveness"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_liveness_evidence_facade_is_core_extension_and_broker_free() -> None:
    evidence_modules = {
        "__init__.py",
        "analysis.py",
        "host.py",
        "models.py",
        "registry.py",
    }
    forbidden_prefixes = (
        "simplebroker",
        "weft.context",
        "weft.core",
        "weft_docker",
        "weft_macos_sandbox",
        "weft_microsandbox",
    )
    for name in evidence_modules:
        imports = _imports(LIVENESS / name)
        assert not {
            module for module in imports if module.startswith(forbidden_prefixes)
        }, name

    assert {
        module
        for module in _imports(LIVENESS / "policy.py")
        if module.startswith("weft.core")
    } == {"weft.core.queue_window"}


def test_tid_mapping_exact_delete_has_one_task_executor() -> None:
    assert WEFT_TID_MAPPINGS_QUEUE not in RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS.values()
    violations: list[str] = []
    allowed = WEFT / "core" / "tasks" / "liveness_monitor.py"
    for path in WEFT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {
                child.id for child in ast.walk(node) if isinstance(child, ast.Name)
            }
            attributes = {
                child.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
            }
            if (
                "WEFT_TID_MAPPINGS_QUEUE" in names
                and "delete" in attributes
                and path != allowed
            ):
                violations.append(f"{path.relative_to(ROOT)}::{node.name}")
    assert violations == []


def test_old_liveness_owners_are_removed_without_shims() -> None:
    assert not (WEFT / "runtime_liveness.py").exists()
    assert not (WEFT / "core" / "monitor" / "policies" / "tid_mapping.py").exists()
