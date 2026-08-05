"""Firing tests for repository Ruff policy [TS-3], [TS-3.1]."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
RULE_FIXTURE = ROOT / "tests" / "fixtures" / "ruff-enabled-rules.txt"
STATIC_ANALYSIS_SPEC = ROOT / "docs" / "specifications" / "08-Testing_Strategy.md"
SUPPRESSION_TOOL = ROOT / "bin" / "ruff_suppression_index.py"
WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"

REVIEWED_FAMILIES = ["E", "W", "F", "I", "B", "C901", "C4", "UP"]
GLOBAL_IGNORES = ["E501", "B008"]
EXTENSIONLESS_PYTHON = [
    "bin/check-doc-paths",
    "bin/check-dom15-fixtures",
    "bin/coalesce-check",
    "bin/pytest-live-providers",
    "bin/pytest-pg",
    "bin/pytest-worker-count",
]
MCCABE_MAX_COMPLEXITY = 10
EXPECTED_GROUP_IDS = [
    *(f"RUFF-SUP-{number:03d}" for number in range(1, 60) if number != 21),
    *(
        f"RUFF-SUP-{number:03d}"
        for number in range(101, 127)
        if number not in {101, 102, 103, 110}
    ),
    *(f"RUFF-SUP-{number:03d}" for number in range(201, 239)),
]
EXPECTED_GROUP_COUNT = 118
EXPECTED_DIRECTIVE_COUNT = 147
TAGGED_C901 = re.compile(
    r"#\s*noqa:\s*[^#\n]*\bC901\b[^#\n]*"
    r"approved\s+\[TS-3\.1\]\s+\[RUFF-SUP-(\d{3})\]\s+exception\b"
)

pytestmark = [pytest.mark.shared]


def _ruff_binary() -> str:
    managed = ROOT / ".venv" / "bin" / "ruff"
    if managed.is_file():
        return str(managed)
    executable = shutil.which("ruff")
    assert executable is not None, "repo test environment does not provide Ruff"
    return executable


def _ruff(
    *args: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_ruff_binary(), *args],
        cwd=ROOT,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def _ruff_config() -> tuple[dict[str, Any], dict[str, Any]]:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    ruff = project["tool"]["ruff"]
    return ruff, ruff["lint"]


def _enabled_rules() -> set[str]:
    result = _ruff("check", "--show-settings", "weft/__init__.py")
    assert result.returncode == 0, result.stderr
    match = re.search(
        r"linter\.rules\.enabled = \[\n(?P<rules>.*?)\n\]",
        result.stdout,
        re.DOTALL,
    )
    assert match is not None, result.stdout
    return set(re.findall(r"\(([A-Z]+\d+)\)", match.group("rules")))


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return [ROOT / raw.decode() for raw in result.stdout.split(b"\0") if raw]


def _is_python_shebang(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            first_line = handle.readline()
    except (FileNotFoundError, IsADirectoryError):
        return False
    return first_line.startswith(b"#!") and b"python" in first_line.lower()


def _tracked_python_files() -> set[Path]:
    paths: set[Path] = set()
    for path in _tracked_files():
        if path.suffix in {".py", ".pyi"} or _is_python_shebang(path):
            paths.add(path.resolve())
    return paths


def _tracked_extensionless_python() -> set[str]:
    return {
        path.relative_to(ROOT).as_posix()
        for path in _tracked_files()
        if not path.suffix and _is_python_shebang(path)
    }


def _ruff_discovered_files() -> set[Path]:
    result = _ruff("check", "--show-files", ".")
    assert result.returncode == 0, result.stderr
    return {Path(line).resolve() for line in result.stdout.splitlines() if line}


def _assert_extensionless_policy(configured: Iterable[str]) -> None:
    actual = set(configured)
    expected = set(EXTENSIONLESS_PYTHON)
    assert actual == expected, {
        "missing": sorted(expected - actual),
        "unexpected": sorted(actual - expected),
    }


def _lint_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    return workflow.split("  lint:", 1)[1].split("  test-django-integration:", 1)[0]


def _load_suppression_tool() -> ModuleType:
    assert SUPPRESSION_TOOL.is_file(), f"missing suppression tool: {SUPPRESSION_TOOL}"
    spec = importlib.util.spec_from_file_location(
        "weft_ruff_suppression_index_policy_test",
        SUPPRESSION_TOOL,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _raw_c901_diagnostics() -> list[dict[str, Any]]:
    result = _ruff(
        "check",
        "--select",
        "C901",
        "--ignore-noqa",
        "--output-format",
        "json",
        ".",
    )
    assert result.returncode == 1, result.stderr
    diagnostics = json.loads(result.stdout)
    assert isinstance(diagnostics, list)
    return diagnostics


def test_ruff_complexity_policy_is_configured() -> None:
    """C901 and its threshold must be explicit repository policy."""

    ruff, lint = _ruff_config()

    assert ruff["extend-include"] == EXTENSIONLESS_PYTHON
    assert lint["select"] == REVIEWED_FAMILIES
    assert lint["ignore"] == GLOBAL_IGNORES
    assert lint["mccabe"] == {"max-complexity": MCCABE_MAX_COMPLEXITY}
    assert lint.get("preview", False) is False
    assert lint.get("per-file-ignores", {}) == {}


def test_effective_ruff_rules_match_reviewed_inventory() -> None:
    expected = set(RULE_FIXTURE.read_text(encoding="utf-8").splitlines())
    assert expected
    assert _enabled_rules() == expected


def test_extensionless_python_inventory_is_exact() -> None:
    assert _tracked_extensionless_python() == set(EXTENSIONLESS_PYTHON)
    assert not {"bin/mypy-check", "bin/uv"} & set(EXTENSIONLESS_PYTHON)


def test_extensionless_policy_guard_fires_when_one_tool_is_omitted() -> None:
    incomplete = EXTENSIONLESS_PYTHON[:-1]
    with pytest.raises(AssertionError, match="pytest-worker-count"):
        _assert_extensionless_policy(incomplete)


def test_ruff_discovers_every_tracked_python_file() -> None:
    expected = _tracked_python_files()
    discovered = _ruff_discovered_files()
    assert expected <= discovered, sorted(
        str(path.relative_to(ROOT)) for path in expected - discovered
    )


def test_ruff_discovery_excludes_tracked_extensionless_bash_tools() -> None:
    discovered = _ruff_discovered_files()
    bash_tools = {ROOT / "bin" / "mypy-check", ROOT / "bin" / "uv"}

    assert not {path.resolve() for path in bash_tools} & discovered


def test_configured_complexity_boundary_fires_at_eleven() -> None:
    def probe(complexity: int) -> str:
        branches = "\n".join(
            f"    if value == {branch}:\n        return {branch}"
            for branch in range(1, complexity)
        )
        return (
            f"def complexity_{complexity}(value: int) -> int:\n"
            f"{branches}\n"
            "    return 0\n"
        )

    result = _ruff(
        "check",
        "--config",
        str(PYPROJECT),
        "--output-format",
        "json",
        "--stdin-filename",
        "complexity_probe.py",
        "-",
        input_text=probe(10) + "\n" + probe(11),
    )
    assert result.returncode == 1, result.stderr
    diagnostics = json.loads(result.stdout)
    assert len(diagnostics) == 1
    assert diagnostics[0]["code"] == "C901"
    assert "`complexity_11` is too complex (11 > 10)" in diagnostics[0]["message"]


def test_approved_suppressions_match_the_spec_registry() -> None:
    tool = _load_suppression_tool()
    snapshot = tool.build_snapshot(
        ROOT,
        STATIC_ANALYSIS_SPEC.read_text(encoding="utf-8"),
    )

    assert EXPECTED_GROUP_COUNT == len(EXPECTED_GROUP_IDS)
    assert [group.group_id for group in snapshot.groups] == EXPECTED_GROUP_IDS
    assert len(snapshot.directives) == EXPECTED_DIRECTIVE_COUNT


def test_every_raw_c901_is_tagged_with_an_approved_group() -> None:
    diagnostics = _raw_c901_diagnostics()
    assert len(diagnostics) == EXPECTED_DIRECTIVE_COUNT
    approved = set(EXPECTED_GROUP_IDS)
    observed: list[str] = []

    for diagnostic in diagnostics:
        assert diagnostic["code"] == "C901"
        source_path = Path(diagnostic["filename"])
        row = diagnostic.get("noqa_row")
        assert isinstance(row, int), diagnostic
        source_line = source_path.read_text(encoding="utf-8").splitlines()[row - 1]
        match = TAGGED_C901.search(source_line)
        assert match is not None, f"untagged C901 at {source_path}:{row}"
        group_id = f"RUFF-SUP-{match.group(1)}"
        assert group_id in approved, f"unknown C901 group at {source_path}:{row}"
        observed.append(group_id)

    assert len(observed) == EXPECTED_DIRECTIVE_COUNT


def test_ci_orders_complete_lint_suppression_format_and_type_gates() -> None:
    lint_job = _lint_job()
    ruff_check = "ruff check ."
    suppression_check = "python bin/ruff_suppression_index.py --check"
    formatter = (
        "ruff format --check weft tests integrations/weft_django "
        "extensions/weft_docker extensions/weft_macos_sandbox "
        "extensions/weft_microsandbox"
    )
    mypy = (
        "mypy weft bin integrations/weft_django/weft_django "
        "extensions/weft_docker/weft_docker "
        "extensions/weft_macos_sandbox/weft_macos_sandbox "
        "extensions/weft_microsandbox/weft_microsandbox "
        "--config-file pyproject.toml"
    )
    compact = " ".join(lint_job.split())

    assert ruff_check in lint_job
    assert suppression_check in lint_job
    assert formatter in compact
    assert mypy in compact
    assert lint_job.index(ruff_check) < lint_job.index(suppression_check)
    assert lint_job.index(suppression_check) < lint_job.index("ruff format --check")
    assert lint_job.index("ruff format --check") < lint_job.index("mypy weft")
    assert "ruff format --check ." not in lint_job
    assert "--preview" not in lint_job


def test_real_repository_ruff_is_clean() -> None:
    result = _ruff("check", ".")
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_suppression_index_check_is_clean() -> None:
    result = subprocess.run(
        [sys.executable, str(SUPPRESSION_TOOL), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
