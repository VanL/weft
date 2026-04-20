"""Architecture guardrails for the final cli/commands/core/client split."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "weft"
DJANGO_INTEGRATION_ROOT = REPO_ROOT / "integrations" / "weft_django" / "weft_django"
ALLOWED_ANYWHERE = (
    "weft._constants",
    "weft._exceptions",
    "weft.context",
    "weft.helpers",
)

pytestmark = [pytest.mark.shared]


@dataclass(frozen=True, slots=True)
class ImportEdge:
    source_module: str
    target_module: str
    path: Path
    lineno: int


def _module_name(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _current_package(module_name: str, path: Path) -> str:
    if path.name == "__init__.py":
        return module_name
    return module_name.rsplit(".", 1)[0]


def _resolve_import(
    *,
    current_package: str,
    level: int,
    module: str | None,
) -> str:
    if level == 0:
        return module or ""

    package_parts = current_package.split(".")
    base_parts = package_parts[: len(package_parts) - level + 1]
    if module:
        return ".".join(base_parts + module.split("."))
    return ".".join(base_parts)


def _iter_import_edges(root: Path) -> list[ImportEdge]:
    edges: list[ImportEdge] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source_module = _module_name(path)
        current_package = _current_package(source_module, path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    edges.append(
                        ImportEdge(
                            source_module=source_module,
                            target_module=alias.name,
                            path=path,
                            lineno=node.lineno,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                edges.append(
                    ImportEdge(
                        source_module=source_module,
                        target_module=_resolve_import(
                            current_package=current_package,
                            level=node.level,
                            module=node.module,
                        ),
                        path=path,
                        lineno=node.lineno,
                    )
                )
    return edges


def _is_allowed_anywhere(module_name: str) -> bool:
    return any(
        module_name == allowed or module_name.startswith(f"{allowed}.")
        for allowed in ALLOWED_ANYWHERE
    )


def _is_module_or_child(module_name: str, prefix: str) -> bool:
    return module_name == prefix or module_name.startswith(f"{prefix}.")


def test_transitional_core_ops_package_is_deleted() -> None:
    assert not (PACKAGE_ROOT / "core" / "ops").exists()


def test_transitional_core_types_module_is_deleted() -> None:
    assert not (PACKAGE_ROOT / "core" / "types.py").exists()


def test_manager_lifecycle_mirror_module_is_deleted() -> None:
    assert not (PACKAGE_ROOT / "commands" / "_manager_lifecycle.py").exists()


def test_dead_command_handlers_module_is_deleted() -> None:
    assert not (PACKAGE_ROOT / "commands" / "handlers.py").exists()


def test_run_support_mirror_module_is_deleted() -> None:
    assert not (PACKAGE_ROOT / "commands" / "_run_support.py").exists()


def test_internal_import_boundaries() -> None:
    violations: list[str] = []
    typer_violations: list[str] = []

    for edge in _iter_import_edges(PACKAGE_ROOT):
        target = edge.target_module
        if not target:
            continue

        if target == "typer" or target.startswith("typer."):
            if not _is_module_or_child(edge.source_module, "weft.cli"):
                typer_violations.append(
                    f"{edge.path}:{edge.lineno} imports {target} from {edge.source_module}"
                )
            continue

        if not target.startswith("weft"):
            continue
        if _is_allowed_anywhere(target):
            continue

        source = edge.source_module
        if _is_module_or_child(source, "weft.core"):
            if any(
                _is_module_or_child(target, forbidden)
                for forbidden in ("weft.commands", "weft.cli", "weft.client")
            ):
                violations.append(
                    f"{edge.path}:{edge.lineno} {source} -> {target} is forbidden"
                )
            continue

        if _is_module_or_child(source, "weft.commands"):
            if any(
                _is_module_or_child(target, forbidden)
                for forbidden in ("weft.cli", "weft.client")
            ):
                violations.append(
                    f"{edge.path}:{edge.lineno} {source} -> {target} is forbidden"
                )
            continue

        if _is_module_or_child(source, "weft.cli"):
            if _is_module_or_child(target, "weft.core"):
                violations.append(
                    f"{edge.path}:{edge.lineno} {source} -> {target} is forbidden"
                )
            continue

        if _is_module_or_child(source, "weft.client"):
            if _is_module_or_child(target, "weft.core"):
                violations.append(
                    f"{edge.path}:{edge.lineno} {source} -> {target} is forbidden"
                )

    assert not violations, "\n".join(violations)
    assert not typer_violations, "\n".join(typer_violations)


def test_django_integration_import_boundaries() -> None:
    violations: list[str] = []

    for edge in _iter_import_edges(DJANGO_INTEGRATION_ROOT):
        target = edge.target_module
        if not target or not (target == "weft" or target.startswith("weft.")):
            continue
        if _is_allowed_anywhere(target):
            continue
        if _is_module_or_child(target, "weft.client") or _is_module_or_child(
            target, "weft.ext"
        ):
            continue

        violations.append(
            f"{edge.path}:{edge.lineno} {edge.source_module} -> {target} is forbidden"
        )

    assert not violations, "\n".join(violations)
