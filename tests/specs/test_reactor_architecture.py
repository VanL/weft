"""Architecture gates for the watcher-centered task reactor.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2.1]
- docs/specifications/07-System_Invariants.md [IMPL.10]
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.shared]

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PATHS = (
    REPO_ROOT / "weft/_constants.py",
    REPO_ROOT / "weft/core/control_probe.py",
    REPO_ROOT / "weft/core/tasks",
    REPO_ROOT / "weft/core/monitor/task_monitor.py",
    REPO_ROOT / "weft/core/manager.py",
    REPO_ROOT / "weft/core/launcher.py",
)
RETIRED_REACTOR_CAPS = {
    "TASK_PROCESS_POLL_INTERVAL",
    "MANAGER_POLL_INTERVAL",
    "MANAGER_CHILD_EXIT_POLL_INTERVAL",
    "MANAGER_FALLBACK_POLL_INTERVAL_SECONDS",
    "CONTROL_PING_POLL_INTERVAL_SECONDS",
    "MANAGER_LEADERSHIP_PING_TIMEOUT_SECONDS",
    "MANAGED_SERVICE_PING_TIMEOUT_SECONDS",
}


def _runtime_python_files() -> list[Path]:
    files: list[Path] = []
    for path in RUNTIME_PATHS:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        else:
            files.append(path)
    return files


def test_task_ping_responder_is_owned_by_base_task() -> None:
    """All task types inherit one PING recognition and routing path."""

    owners: set[tuple[str, str]] = set()
    for path in (REPO_ROOT / "weft/core/tasks").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for class_node in (
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        ):
            for method in class_node.body:
                if not (
                    isinstance(method, ast.FunctionDef)
                    and method.name == "_handle_control_command"
                ):
                    continue
                if any(
                    isinstance(node, ast.Name) and node.id == "CONTROL_PING"
                    for node in ast.walk(method)
                ):
                    owners.add(
                        (
                            str(path.relative_to(REPO_ROOT)),
                            f"{class_node.name}.{method.name}",
                        )
                    )

    assert owners == {("weft/core/tasks/base.py", "BaseTask._handle_control_command")}


def test_retired_task_reactor_caps_do_not_return() -> None:
    """Named source-adapter bounds must not become task-reactor schedulers."""

    found: set[tuple[str, str]] = set()
    for path in _runtime_python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in RETIRED_REACTOR_CAPS:
                found.add((str(path.relative_to(REPO_ROOT)), node.id))

    assert found == set()
