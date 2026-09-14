"""Static ownership checks for the private liveness subsystem."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

from weft._constants import (
    RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS,
    WEFT_TASK_STATE_QUEUE_PREFIX,
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
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, "weft.liveness")
            imports.update(f"{module}.{alias.name}" for alias in node.names)
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

    assert not {
        module
        for module in _imports(LIVENESS / "policy.py")
        if module.startswith("weft.core")
        and module != "weft.core.queue_window"
        and not module.startswith("weft.core.queue_window.")
    }


def test_task_state_exact_delete_has_one_task_executor() -> None:
    assert not any(
        queue.startswith(WEFT_TASK_STATE_QUEUE_PREFIX)
        for queue in RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS.values()
    )
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
                names & {"WEFT_TASK_STATE_QUEUE_PREFIX", "task_state_queue_name"}
                and attributes & {"delete", "delete_many", "delete_message_ids"}
                and path != allowed
            ):
                violations.append(f"{path.relative_to(ROOT)}::{node.name}")
    assert violations == []


@pytest.mark.parametrize(
    "source",
    ["from ..context import WeftContext", "from .. import core", "import simplebroker"],
)
def test_liveness_import_guard_resolves_forbidden_relative_imports(
    tmp_path: Path, source: str
) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")
    assert any(
        name.startswith(("weft.context", "weft.core", "simplebroker"))
        for name in _imports(probe)
    )


@pytest.mark.parametrize(
    ("source", "allowed"),
    [
        ("from weft.core import queue_window", True),
        ("from weft.core.queue_window import QueueWindowRow", True),
        ("from weft.core import task_state", False),
    ],
)
def test_liveness_policy_queue_window_exception_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, allowed: bool
) -> None:
    for name in ("__init__.py", "analysis.py", "host.py", "models.py", "registry.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    (tmp_path / "policy.py").write_text(source, encoding="utf-8")
    monkeypatch.setitem(globals(), "LIVENESS", tmp_path)

    if allowed:
        test_liveness_evidence_facade_is_core_extension_and_broker_free()
    else:
        with pytest.raises(AssertionError):
            test_liveness_evidence_facade_is_core_extension_and_broker_free()
