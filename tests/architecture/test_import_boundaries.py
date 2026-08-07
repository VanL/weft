"""Architecture guardrails for the final cli/commands/core/client split."""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

import simplebroker
import simplebroker.ext as simplebroker_ext
from simplebroker import commands as simplebroker_commands

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "weft"
DJANGO_INTEGRATION_ROOT = REPO_ROOT / "integrations" / "weft_django" / "weft_django"
SIMPLEBROKER_CONSUMER_ROOTS = (
    PACKAGE_ROOT,
    REPO_ROOT / "tests",
    REPO_ROOT / "integrations",
    REPO_ROOT / "extensions",
    REPO_ROOT / "bin",
)
ALLOWED_ANYWHERE = (
    "weft._constants",
    "weft._exceptions",
    "weft.context",
    "weft.helpers",
)
MONITOR_DERIVED_STATUS_IMPORT = (
    "weft.commands.tasks",
    "weft.core.monitor.store",
)
MONITOR_AUTHORITY_TARGETS = (
    "weft.core.monitor.collation",
    "weft.core.monitor.sql",
    "weft.core.monitor.store",
)
RESULT_AUTHORITY_SOURCES = (
    "weft.commands._result_wait",
    "weft.commands.result",
)

pytestmark = [pytest.mark.shared]

ROOT_EXPORTS = {
    "__version__",
    "PROG_NAME",
    "Task",
    "TaskEvent",
    "TaskResult",
    "TaskSnapshot",
    "WeftClient",
    "debug_print",
    "send_log",
    "log_debug",
    "log_info",
    "log_warning",
    "log_error",
}
ROOT_LAZY_OWNERS = {
    "Task": ("weft.client", "Task"),
    "TaskEvent": ("weft.client", "TaskEvent"),
    "TaskResult": ("weft.client", "TaskResult"),
    "TaskSnapshot": ("weft.client", "TaskSnapshot"),
    "WeftClient": ("weft.client", "WeftClient"),
    "debug_print": ("weft.helpers", "debug_print"),
    "send_log": ("weft.helpers", "send_log"),
    "log_debug": ("weft.helpers", "log_debug"),
    "log_info": ("weft.helpers", "log_info"),
    "log_warning": ("weft.helpers", "log_warning"),
    "log_error": ("weft.helpers", "log_error"),
}
COMMAND_EXPORTS = {
    "cmd_init",
    "cmd_result",
    "serve_command",
    "cmd_status",
    "cmd_tidy",
    "manager",
}
COMMAND_LAZY_OWNERS = {
    "cmd_init": ("weft.commands.init", "cmd_init"),
    "cmd_result": ("weft.commands.result", "cmd_result"),
    "serve_command": ("weft.commands.serve", "serve_command"),
    "cmd_status": ("weft.commands.status", "cmd_status"),
    "cmd_tidy": ("weft.commands.tidy", "cmd_tidy"),
}
CORE_EXPORTS = {
    "Consumer",
    "Observer",
    "SelectiveConsumer",
    "Monitor",
    "TaskRunner",
    "Manager",
    "launch_task_process",
    "ResourceMonitor",
    "PsutilResourceMonitor",
    "BaseResourceMonitor",
    "make_callable",
    "ManagedProcessResult",
    "decode_work_message",
    "prepare_call_arguments",
    "execute_function_target",
    "execute_command_target",
    "serialize_result",
    "TaskSpec",
    "SpecSection",
    "LimitsSection",
    "RunnerSection",
    "IOSection",
    "StateSection",
    "validate_taskspec",
    "format_tid",
    "parse_tid",
}
CORE_LAZY_OWNERS = {
    "Consumer": ("weft.core.tasks", "Consumer"),
    "Observer": ("weft.core.tasks", "Observer"),
    "SelectiveConsumer": ("weft.core.tasks", "SelectiveConsumer"),
    "Monitor": ("weft.core.tasks", "Monitor"),
    "TaskRunner": ("weft.core.tasks.runner", "TaskRunner"),
    "Manager": ("weft.core.manager", "Manager"),
    "launch_task_process": ("weft.core.launcher", "launch_task_process"),
    "ResourceMonitor": ("weft.core.resource_monitor", "ResourceMonitor"),
    "PsutilResourceMonitor": (
        "weft.core.resource_monitor",
        "PsutilResourceMonitor",
    ),
    "BaseResourceMonitor": ("weft.core.resource_monitor", "BaseResourceMonitor"),
    "make_callable": ("weft.core.callable", "make_callable"),
    "ManagedProcessResult": ("weft.core.callable", "ManagedProcessResult"),
    "decode_work_message": ("weft.core.targets", "decode_work_message"),
    "prepare_call_arguments": ("weft.core.targets", "prepare_call_arguments"),
    "execute_function_target": ("weft.core.targets", "execute_function_target"),
    "execute_command_target": ("weft.core.targets", "execute_command_target"),
    "serialize_result": ("weft.core.targets", "serialize_result"),
    "TaskSpec": ("weft.core.taskspec", "TaskSpec"),
    "SpecSection": ("weft.core.taskspec", "SpecSection"),
    "LimitsSection": ("weft.core.taskspec", "LimitsSection"),
    "RunnerSection": ("weft.core.taskspec", "RunnerSection"),
    "IOSection": ("weft.core.taskspec", "IOSection"),
    "StateSection": ("weft.core.taskspec", "StateSection"),
    "validate_taskspec": ("weft.core.taskspec", "validate_taskspec"),
    "format_tid": ("weft.helpers", "format_tid"),
    "parse_tid": ("weft.helpers", "parse_tid"),
}


@dataclass(frozen=True, slots=True)
class ImportEdge:
    source_module: str
    target_module: str
    syntactic_target: str
    scope: Literal["module", "function"]
    type_checking: bool
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


def _parse_import_edges(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-210] exception
    source: str,
    *,
    source_module: str,
    current_package: str,
    path: Path,
    known_modules: set[str],
) -> list[ImportEdge]:
    edges: list[ImportEdge] = []

    class ImportVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.function_depth = 0
            self.type_checking_depth = 0

        def _append(
            self,
            *,
            target: str,
            syntactic_target: str,
            lineno: int,
        ) -> None:
            edges.append(
                ImportEdge(
                    source_module=source_module,
                    target_module=target,
                    syntactic_target=syntactic_target,
                    scope="function" if self.function_depth else "module",
                    type_checking=bool(self.type_checking_depth),
                    path=path,
                    lineno=lineno,
                )
            )

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                self._append(
                    target=alias.name,
                    syntactic_target=alias.name,
                    lineno=node.lineno,
                )

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            base = _resolve_import(
                current_package=current_package,
                level=node.level,
                module=node.module,
            )
            for alias in node.names:
                child = f"{base}.{alias.name}" if base else alias.name
                target = child if child in known_modules else base
                self._append(
                    target=target,
                    syntactic_target=base,
                    lineno=node.lineno,
                )

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.function_depth += 1
            self.generic_visit(node)
            self.function_depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node: ast.Lambda) -> None:
            self.function_depth += 1
            self.generic_visit(node)
            self.function_depth -= 1

        def visit_If(self, node: ast.If) -> None:
            is_type_checking = (
                isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
            ) or (
                isinstance(node.test, ast.Attribute)
                and isinstance(node.test.value, ast.Name)
                and node.test.value.id == "typing"
                and node.test.attr == "TYPE_CHECKING"
            )
            if is_type_checking:
                self.type_checking_depth += 1
                for child in node.body:
                    self.visit(child)
                self.type_checking_depth -= 1
                for child in node.orelse:
                    self.visit(child)
                return
            self.generic_visit(node)

    ImportVisitor().visit(ast.parse(source, filename=str(path)))
    return edges


def _iter_import_edges(root: Path) -> list[ImportEdge]:
    known_modules = {
        _module_name(path)
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    edges: list[ImportEdge] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source_module = _module_name(path)
        edges.extend(
            _parse_import_edges(
                path.read_text(encoding="utf-8"),
                source_module=source_module,
                current_package=_current_package(source_module, path),
                path=path,
                known_modules=known_modules,
            )
        )
    return edges


def _strongly_connected_components(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-227] exception
    edges: list[tuple[str, str]],
) -> list[tuple[str, ...]]:
    graph: dict[str, set[str]] = {}
    for source, target in edges:
        graph.setdefault(source, set()).add(target)
        graph.setdefault(target, set())

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in graph[node]:
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])

        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while True:
            target = stack.pop()
            on_stack.remove(target)
            component.append(target)
            if target == node:
                break
        if len(component) > 1 or node in graph[node]:
            components.append(tuple(sorted(component)))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return sorted(components)


def _fresh_import_modules(statement: str) -> set[str]:
    script = f"import json, sys\n{statement}\nprint(json.dumps(sorted(sys.modules)))\n"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(json.loads(completed.stdout))


def _own_facade_violations(
    edges: list[ImportEdge],
    *,
    facade: str,
) -> list[str]:
    return [
        f"{edge.path}:{edge.lineno} {edge.source_module} -> {edge.syntactic_target}"
        for edge in edges
        if edge.source_module != facade
        and _is_module_or_child(edge.source_module, facade)
        and edge.syntactic_target == facade
    ]


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


def test_internal_import_boundaries() -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-228] exception
    violations: list[str] = []
    rich_imports_seen: list[str] = []
    rich_violations: list[str] = []
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

        if target == "rich" or target.startswith("rich."):
            rich_imports_seen.append(f"{edge.path}:{edge.lineno}")
            if not _is_module_or_child(edge.source_module, "weft.cli"):
                rich_violations.append(
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

        if _is_module_or_child(source, "weft.client") and _is_module_or_child(
            target, "weft.core"
        ):
            violations.append(
                f"{edge.path}:{edge.lineno} {source} -> {target} is forbidden"
            )

    assert not violations, "\n".join(violations)
    assert rich_imports_seen, "Rich boundary guard has no positive import fixture"
    assert not rich_violations, "\n".join(rich_violations)
    assert not typer_violations, "\n".join(typer_violations)


def test_monitor_tables_are_not_result_or_client_authority() -> None:
    """Monitor tables are derived status evidence, not result/client authority."""

    violations: list[str] = []

    for edge in _iter_import_edges(PACKAGE_ROOT):
        source = edge.source_module
        target = edge.target_module
        if not target:
            continue

        if (source, target) == MONITOR_DERIVED_STATUS_IMPORT:
            # Derived status fallback after raw task-log retirement; this is
            # command-layer status reconstruction, not result authority.
            continue

        if not any(
            _is_module_or_child(target, prefix) for prefix in MONITOR_AUTHORITY_TARGETS
        ):
            continue

        if source in RESULT_AUTHORITY_SOURCES or _is_module_or_child(
            source, "weft.client"
        ):
            violations.append(
                f"{edge.path}:{edge.lineno} {source} -> {target} is forbidden"
            )

    assert not violations, "\n".join(violations)


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


def test_no_private_simplebroker_reaches() -> None:
    """weft must use only public simplebroker surface.

    Guards both private-module imports (simplebroker._x) and dynamic
    attribute reaches (getattr(obj, "_retrieve") / obj._runner style) that
    the sidecar and include_claimed migrations eliminated.
    """
    offenders: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (
            "simplebroker._",
            'getattr(broker, "_',
            "broker._runner",
            "broker._retrieve",
        ):
            if needle in text:
                offenders.append(f"{path}: {needle}")
    assert offenders == []


def _simplebroker_surface_violations(source: str, *, filename: str) -> list[str]:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-229] exception
    surface_exports = {
        "simplebroker": set(simplebroker.__all__) | {"commands"},
        "simplebroker.commands": set(simplebroker_commands.__all__),
        "simplebroker.ext": set(simplebroker_ext.__all__),
    }
    violations: list[str] = []
    tree = ast.parse(source, filename=filename)
    module_aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                module = imported.name
                if module != "simplebroker" and not module.startswith("simplebroker."):
                    continue
                if module not in surface_exports:
                    violations.append(
                        f"{filename}:{node.lineno} imports unsupported {module}"
                    )
                    continue
                if module != "simplebroker" and imported.asname is None:
                    violations.append(
                        f"{filename}:{node.lineno} imports {module} without "
                        "an explicit alias"
                    )
                    continue
                module_aliases[imported.asname or "simplebroker"] = module
            continue

        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if module != "simplebroker" and not module.startswith("simplebroker."):
            continue
        if module not in surface_exports:
            violations.append(f"{filename}:{node.lineno} imports unsupported {module}")
            continue

        for imported in node.names:
            if imported.name not in surface_exports[module]:
                violations.append(
                    f"{filename}:{node.lineno} imports non-exported "
                    f"{module}.{imported.name}"
                )
                continue
            if module == "simplebroker" and imported.name == "commands":
                module_aliases[imported.asname or imported.name] = (
                    "simplebroker.commands"
                )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        module = module_aliases.get(node.value.id)
        if module is None or node.attr == "__all__":
            continue
        if node.attr not in surface_exports[module]:
            violations.append(
                f"{filename}:{node.lineno} reaches non-exported {module}.{node.attr}"
            )

    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Name)
            or node.func.id != "getattr"
            or len(node.args) < 2
            or not isinstance(node.args[0], ast.Name)
            or not isinstance(node.args[1], ast.Constant)
            or not isinstance(node.args[1].value, str)
        ):
            continue
        module = module_aliases.get(node.args[0].id)
        attribute = node.args[1].value
        if module is None or attribute in surface_exports[module]:
            continue
        violations.append(
            f"{filename}:{node.lineno} reaches non-exported "
            f"{module}.{attribute} via getattr"
        )

    return violations


@pytest.mark.parametrize(
    ("source", "expected_fragment"),
    [
        ("import simplebroker.ext as ext\next.BrokerError\n", None),
        (
            "import simplebroker.commands\nsimplebroker.commands.MAX_MESSAGE_SIZE\n",
            "without an explicit alias",
        ),
        (
            "import simplebroker.commands as commands\ncommands.MAX_MESSAGE_SIZE\n",
            "reaches non-exported simplebroker.commands.MAX_MESSAGE_SIZE",
        ),
        (
            (
                "from simplebroker import commands\ngetattr(commands, "
                '"MAX_MESSAGE_SIZE")\n'
            ),
            "via getattr",
        ),
    ],
)
def test_simplebroker_surface_guard_fires_for_import_forms(
    source: str,
    expected_fragment: str | None,
) -> None:
    violations = _simplebroker_surface_violations(source, filename="synthetic.py")

    if expected_fragment is None:
        assert violations == []
    else:
        assert any(expected_fragment in violation for violation in violations)


def test_weft_uses_only_supported_simplebroker_surfaces() -> None:
    """Weft consumers stay on root, ext, and command-layer public exports."""

    violations: list[str] = []

    for root in SIMPLEBROKER_CONSUMER_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            violations.extend(
                _simplebroker_surface_violations(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                )
            )

    assert not violations, "\n".join(violations)


def test_weft_has_no_eager_import_cycles() -> None:
    """Module-scope runtime imports must form an acyclic package graph."""

    edges = [
        (edge.source_module, edge.target_module)
        for edge in _iter_import_edges(PACKAGE_ROOT)
        if edge.scope == "module"
        and not edge.type_checking
        and edge.target_module.startswith("weft.")
    ]
    assert _strongly_connected_components(edges) == []


def test_runner_runtime_imports_have_no_cycles() -> None:
    """Runner modules must not use deferred runtime imports to hide a cycle."""

    runner_prefix = "weft.core.runners"
    edges = [
        (edge.source_module, edge.target_module)
        for edge in _iter_import_edges(PACKAGE_ROOT / "core" / "runners")
        if not edge.type_checking
        and _is_module_or_child(edge.source_module, runner_prefix)
        and _is_module_or_child(edge.target_module, runner_prefix)
    ]
    assert _strongly_connected_components(edges) == []


@pytest.mark.parametrize(
    ("source_module", "current_package", "source", "facade", "child"),
    [
        (
            "weft.commands.result",
            "weft.commands",
            "from . import task_evidence\n",
            "weft.commands",
            "weft.commands.task_evidence",
        ),
        (
            "weft.commands.run",
            "weft.commands",
            "from weft.commands import specs\n",
            "weft.commands",
            "weft.commands.specs",
        ),
        (
            "weft.core.monitor.runtime",
            "weft.core.monitor",
            "from weft.core import task_evidence\n",
            "weft.core",
            "weft.core.task_evidence",
        ),
    ],
)
def test_restored_facade_backedges_are_detected(
    source_module: str,
    current_package: str,
    source: str,
    facade: str,
    child: str,
) -> None:
    edges = _parse_import_edges(
        source,
        source_module=source_module,
        current_package=current_package,
        path=Path("synthetic.py"),
        known_modules={facade, child},
    )

    assert edges[0].target_module == child
    assert edges[0].syntactic_target == facade
    assert _own_facade_violations(edges, facade=facade)


def test_restored_runner_function_import_cycle_is_detected() -> None:
    known_modules = {
        "weft.core.runners.host",
        "weft.core.runners.subprocess_runner",
    }
    host_edges = _parse_import_edges(
        "from weft.core.runners import subprocess_runner\n",
        source_module="weft.core.runners.host",
        current_package="weft.core.runners",
        path=Path("host.py"),
        known_modules=known_modules,
    )
    subprocess_edges = _parse_import_edges(
        "def run():\n    from weft.core.runners.host import RunnerOutcome\n",
        source_module="weft.core.runners.subprocess_runner",
        current_package="weft.core.runners",
        path=Path("subprocess_runner.py"),
        known_modules=known_modules,
    )

    assert subprocess_edges[0].scope == "function"
    assert _strongly_connected_components(
        [
            (edge.source_module, edge.target_module)
            for edge in host_edges + subprocess_edges
            if not edge.type_checking
        ]
    ) == [
        (
            "weft.core.runners.host",
            "weft.core.runners.subprocess_runner",
        )
    ]


def test_type_checking_runner_backedge_does_not_create_runtime_cycle() -> None:
    known_modules = {
        "weft.core.runners.host",
        "weft.core.runners.subprocess_runner",
    }
    host_edges = _parse_import_edges(
        "from weft.core.runners import subprocess_runner\n",
        source_module="weft.core.runners.host",
        current_package="weft.core.runners",
        path=Path("host.py"),
        known_modules=known_modules,
    )
    subprocess_edges = _parse_import_edges(
        "if TYPE_CHECKING:\n    from weft.core.runners.host import RunnerOutcome\n",
        source_module="weft.core.runners.subprocess_runner",
        current_package="weft.core.runners",
        path=Path("subprocess_runner.py"),
        known_modules=known_modules,
    )

    assert subprocess_edges[0].type_checking
    assert (
        _strongly_connected_components(
            [
                (edge.source_module, edge.target_module)
                for edge in host_edges + subprocess_edges
                if not edge.type_checking
            ]
        )
        == []
    )


@pytest.mark.parametrize("facade", ["weft.commands", "weft.core"])
def test_leaf_modules_do_not_import_their_own_package_facade(facade: str) -> None:
    assert (
        _own_facade_violations(
            _iter_import_edges(PACKAGE_ROOT),
            facade=facade,
        )
        == []
    )


def test_root_constants_import_does_not_initialize_upper_layers() -> None:
    modules = _fresh_import_modules("import weft._constants")
    forbidden = ("weft.client", "weft.commands", "weft.core", "rich")
    assert not {
        module
        for module in modules
        if any(_is_module_or_child(module, prefix) for prefix in forbidden)
    }


def test_commands_specs_import_does_not_initialize_sibling_capabilities() -> None:
    modules = _fresh_import_modules("import weft.commands.specs")
    forbidden = (
        "weft.commands.result",
        "weft.commands.status",
        "weft.commands.system",
        "weft.cli.validate_taskspec",
        "rich",
    )
    assert not {
        module
        for module in modules
        if any(_is_module_or_child(module, prefix) for prefix in forbidden)
    }


def test_core_task_evidence_import_does_not_initialize_manager_or_monitor() -> None:
    modules = _fresh_import_modules("import weft.core.task_evidence")
    forbidden = ("weft.core.manager", "weft.core.monitor.task_monitor")
    assert not {
        module
        for module in modules
        if any(_is_module_or_child(module, prefix) for prefix in forbidden)
    }


def test_agents_import_does_not_register_or_load_builtin_backends() -> None:
    modules = _fresh_import_modules(
        "import weft.core.agents\n"
        "from weft.core.agents.runtime import get_agent_runtime\n"
        "try:\n"
        "    get_agent_runtime('llm')\n"
        "except ValueError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('llm registered during package import')"
    )
    assert "weft.core.agents.backends.llm" not in modules
    assert "weft.core.agents.backends.provider_cli" not in modules


def test_host_import_registers_builtin_agent_backends() -> None:
    modules = _fresh_import_modules(
        "import weft.core.runners.host\n"
        "from weft.core.agents.runtime import get_agent_runtime\n"
        "assert get_agent_runtime('llm') is not None\n"
        "assert get_agent_runtime('provider_cli') is not None"
    )
    assert "weft.core.agents.backends.llm" in modules
    assert "weft.core.agents.backends.provider_cli" in modules


def test_public_facade_exports_keep_identity() -> None:
    from weft import Task as root_task
    from weft.client import Task as client_task
    from weft.commands import cmd_result
    from weft.commands.result import cmd_result as leaf_cmd_result
    from weft.core import Manager
    from weft.core.manager import Manager as leaf_manager
    from weft.core.runners import RunnerOutcome
    from weft.core.runners.host import RunnerOutcome as host_runner_outcome
    from weft.core.runners.outcome import RunnerOutcome as leaf_runner_outcome
    from weft.core.tasks.runner import RunnerOutcome as task_runner_outcome

    assert root_task is client_task
    assert cmd_result is leaf_cmd_result
    assert Manager is leaf_manager
    assert RunnerOutcome is leaf_runner_outcome
    assert host_runner_outcome is leaf_runner_outcome
    assert task_runner_outcome is leaf_runner_outcome


def test_agent_backend_package_exports_keep_identity() -> None:
    from weft.core.agents import backends
    from weft.core.agents.backends.llm import LLMBackend
    from weft.core.agents.backends.provider_cli import ProviderCLIBackend

    assert backends.LLMBackend is LLMBackend
    assert backends.ProviderCLIBackend is ProviderCLIBackend
    namespace: dict[str, object] = {}
    exec("from weft.core.agents.backends import *", namespace)  # noqa: S102 approved [TS-3.1] [RUFF-SUP-254] exception
    assert namespace["LLMBackend"] is LLMBackend
    assert namespace["ProviderCLIBackend"] is ProviderCLIBackend


@pytest.mark.parametrize(
    ("facade_name", "expected_exports", "owners"),
    [
        ("weft", ROOT_EXPORTS, ROOT_LAZY_OWNERS),
        ("weft.commands", COMMAND_EXPORTS, COMMAND_LAZY_OWNERS),
        ("weft.core", CORE_EXPORTS, CORE_LAZY_OWNERS),
    ],
)
def test_lazy_facades_preserve_inventory_identity_and_cache(
    facade_name: str,
    expected_exports: set[str],
    owners: dict[str, tuple[str, str]],
) -> None:
    facade = importlib.import_module(facade_name)

    assert set(facade.__all__) == expected_exports
    assert expected_exports <= set(dir(facade))
    for name, (owner_name, owner_attribute) in owners.items():
        expected = getattr(importlib.import_module(owner_name), owner_attribute)
        first = getattr(facade, name)
        second = getattr(facade, name)
        assert first is expected
        assert second is first
        assert facade.__dict__[name] is first


def test_commands_manager_export_supports_attribute_from_and_star_imports() -> None:
    commands = importlib.import_module("weft.commands")
    manager_module = importlib.import_module("weft.commands.manager")

    assert commands.manager is manager_module
    namespace: dict[str, object] = {}
    exec("from weft.commands import manager", namespace)  # noqa: S102 approved [TS-3.1] [RUFF-SUP-254] exception
    assert namespace["manager"] is manager_module

    namespace = {}
    exec("from weft.commands import *", namespace)  # noqa: S102 approved [TS-3.1] [RUFF-SUP-254] exception
    assert namespace["manager"] is manager_module


@pytest.mark.parametrize("facade_name", ["weft", "weft.commands", "weft.core"])
def test_lazy_facades_reject_unknown_attributes(facade_name: str) -> None:
    facade = importlib.import_module(facade_name)
    with pytest.raises(AttributeError, match="not_a_public_export"):
        facade.__getattr__("not_a_public_export")
