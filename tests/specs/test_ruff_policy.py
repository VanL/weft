"""Firing tests for repository Ruff policy [TS-3], [TS-3.1]."""

from __future__ import annotations

import importlib.util
import json
import re
import runpy
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
RULE_FIXTURE = ROOT / "tests" / "fixtures" / "ruff-enabled-rules.txt"
TESTING_STRATEGY_SPEC = ROOT / "docs" / "specifications" / "08-Testing_Strategy.md"
SUPPRESSION_REGISTRY = ROOT / "docs" / "ruff-suppression-registry.md"
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
RUFF_CHECK = "ruff check ."
SUPPRESSION_CHECK = "python bin/ruff_suppression_index.py --check"
FORMATTER_CHECK = (
    "ruff format --check weft tests integrations/weft_django "
    "extensions/weft_docker extensions/weft_macos_sandbox "
    "extensions/weft_microsandbox"
)
MYPY_CHECK = (
    "mypy weft bin integrations/weft_django/weft_django "
    "extensions/weft_docker/weft_docker "
    "extensions/weft_macos_sandbox/weft_macos_sandbox "
    "extensions/weft_microsandbox/weft_microsandbox "
    "--config-file pyproject.toml"
)
EXPECTED_GROUP_IDS = [
    *(f"RUFF-SUP-{number:03d}" for number in range(1, 60) if number not in {4, 15, 21}),
    *(
        f"RUFF-SUP-{number:03d}"
        for number in range(101, 127)
        if number not in {101, 102, 103, 110, 121}
    ),
    *(
        f"RUFF-SUP-{number:03d}"
        for number in range(201, 239)
        if number not in {207, 223}
    ),
    *(
        f"RUFF-SUP-{number:03d}"
        for number in range(239, 366)
        if number not in {282, 285, 293, 296, 297, 301, 302, 305, 306}
    ),
]
EXPECTED_GROUP_COUNT = 231
EXPECTED_DIRECTIVE_COUNT = 374
EXPECTED_C901_DIRECTIVE_COUNT = 140
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


def _activation_policy_config() -> tuple[dict[str, Any], dict[str, Any]]:
    ruff, lint = deepcopy(_ruff_config())
    if "select" in lint:
        lint["extend-select"] = lint.pop("select")
    return ruff, lint


def _ruff_settings(*, config: Path = PYPROJECT) -> dict[str, Any]:
    result = _ruff(
        "check",
        "--config",
        str(config),
        "--show-settings",
        "weft/__init__.py",
    )
    assert result.returncode == 0, result.stderr
    match = re.search(
        r"linter\.rules\.enabled = \[\n(?P<rules>.*?)\n\]",
        result.stdout,
        re.DOTALL,
    )
    assert match is not None, result.stdout
    target = re.search(
        r"^linter\.unresolved_target_version = (.+)$",
        result.stdout,
        flags=re.MULTILINE,
    )
    complexity = re.search(
        r"^linter\.mccabe\.max_complexity = (\d+)$",
        result.stdout,
        flags=re.MULTILINE,
    )
    preview = re.search(
        r"^linter\.preview = (\w+)$",
        result.stdout,
        flags=re.MULTILINE,
    )
    assert target is not None, result.stdout
    assert complexity is not None, result.stdout
    assert preview is not None, result.stdout
    return {
        "enabled": set(re.findall(r"\(([A-Z]+\d+)\)", match.group("rules"))),
        "target": target.group(1),
        "max-complexity": int(complexity.group(1)),
        "preview": preview.group(1),
    }


def _enabled_rules() -> set[str]:
    return _ruff_settings()["enabled"]


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


def _assert_ruff_policy(ruff: dict[str, Any], lint: dict[str, Any]) -> None:
    assert ruff["target-version"] == "py312"
    _assert_extensionless_policy(ruff["extend-include"])
    assert "select" not in lint
    assert lint["extend-select"] == REVIEWED_FAMILIES
    assert lint["ignore"] == GLOBAL_IGNORES
    assert lint["mccabe"] == {"max-complexity": MCCABE_MAX_COMPLEXITY}
    assert lint.get("preview", False) is False
    assert lint.get("per-file-ignores", {}) == {}


def _assert_enabled_rules(actual: set[str], expected: set[str]) -> None:
    assert actual == expected, {
        "missing": sorted(expected - actual),
        "unexpected": sorted(actual - expected),
    }


def _lint_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    return workflow.split("  lint:", 1)[1].split("  test-django-integration:", 1)[0]


def _assert_lint_job(job: str) -> None:
    compact = " ".join(job.split())

    assert RUFF_CHECK in job
    assert SUPPRESSION_CHECK in job
    assert FORMATTER_CHECK in compact
    assert MYPY_CHECK in compact
    assert job.index(RUFF_CHECK) < job.index(SUPPRESSION_CHECK)
    assert job.index(SUPPRESSION_CHECK) < job.index("ruff format --check")
    assert job.index("ruff format --check") < job.index("mypy weft")
    assert "ruff format --check ." not in job
    assert "--preview" not in job


def _write_ruff_config(
    tmp_path: Path,
    *,
    name: str,
    selection_key: str = "extend-select",
    ignores: Iterable[str] = GLOBAL_IGNORES,
    per_file_ignores: bool = False,
) -> Path:
    config_dir = tmp_path / name
    config_dir.mkdir()
    config = config_dir / "pyproject.toml"
    text = PYPROJECT.read_text(encoding="utf-8")
    text = re.sub(
        r"\n(?:select|extend-select) = \[\n",
        f"\n{selection_key} = [\n",
        text,
        count=1,
    )
    retained_ignores = set(ignores)
    for code in GLOBAL_IGNORES:
        if code not in retained_ignores:
            text = re.sub(
                rf'^\s*"{code}",.*\n',
                "",
                text,
                count=1,
                flags=re.MULTILINE,
            )
    if per_file_ignores:
        text += '\n[tool.ruff.lint.per-file-ignores]\n"probe.py" = ["F401"]\n'
    config.write_text(text, encoding="utf-8")
    return config


def _stdin_codes(config: Path, source: str, *, filename: str = "probe.py") -> set[str]:
    result = _ruff(
        "check",
        "--config",
        str(config),
        "--stdin-filename",
        filename,
        "--output-format",
        "json",
        "-",
        input_text=source,
    )
    assert result.returncode in {0, 1}, result.stderr
    return {diagnostic["code"] for diagnostic in json.loads(result.stdout)}


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


def test_ruff_extends_defaults_without_losing_legacy_families() -> None:
    """Stable defaults and retained families must be explicit repository policy."""

    ruff, lint = _ruff_config()
    _assert_ruff_policy(ruff, lint)


@pytest.mark.parametrize(
    "mutation",
    [
        "select",
        "missing-family",
        "extra-family",
        "target-version",
        "ignore-E501",
        "ignore-B008",
        "mccabe",
        "preview",
        "per-file-ignore",
    ],
)
def test_ruff_policy_guard_rejects_each_setting_mutation(mutation: str) -> None:
    ruff, lint = _activation_policy_config()
    _assert_ruff_policy(ruff, lint)

    if mutation == "select":
        lint["select"] = lint.pop("extend-select")
    elif mutation == "missing-family":
        lint["extend-select"] = REVIEWED_FAMILIES[:-1]
    elif mutation == "extra-family":
        lint["extend-select"] = [*REVIEWED_FAMILIES, "N"]
    elif mutation == "target-version":
        ruff["target-version"] = "py313"
    elif mutation == "ignore-E501":
        lint["ignore"] = ["B008"]
    elif mutation == "ignore-B008":
        lint["ignore"] = ["E501"]
    elif mutation == "mccabe":
        lint["mccabe"] = {"max-complexity": MCCABE_MAX_COMPLEXITY + 1}
    elif mutation == "preview":
        lint["preview"] = True
    elif mutation == "per-file-ignore":
        lint["per-file-ignores"] = {"tests/*.py": ["F401"]}
    else:  # pragma: no cover - parametrization is the closed mutation contract
        raise AssertionError(f"unknown mutation: {mutation}")

    with pytest.raises(AssertionError):
        _assert_ruff_policy(ruff, lint)


@pytest.mark.parametrize("omitted", EXTENSIONLESS_PYTHON)
def test_ruff_policy_guard_rejects_each_omitted_extensionless_path(
    omitted: str,
) -> None:
    ruff, lint = _activation_policy_config()
    _assert_ruff_policy(ruff, lint)
    ruff["extend-include"] = [path for path in EXTENSIONLESS_PYTHON if path != omitted]

    with pytest.raises(AssertionError, match=re.escape(omitted)):
        _assert_ruff_policy(ruff, lint)


def test_ruff_policy_guard_rejects_extensionless_bash_inclusion() -> None:
    ruff, lint = _activation_policy_config()
    _assert_ruff_policy(ruff, lint)
    ruff["extend-include"] = [*EXTENSIONLESS_PYTHON, "bin/mypy-check"]

    with pytest.raises(AssertionError, match="bin/mypy-check"):
        _assert_ruff_policy(ruff, lint)


def test_effective_ruff_rules_match_reviewed_inventory() -> None:
    expected = set(RULE_FIXTURE.read_text(encoding="utf-8").splitlines())
    assert expected
    _assert_enabled_rules(_enabled_rules(), expected)


def test_enabled_rule_inventory_guard_rejects_changed_code() -> None:
    expected = set(RULE_FIXTURE.read_text(encoding="utf-8").splitlines())
    changed = expected - {min(expected)}
    _assert_enabled_rules(expected, expected)

    with pytest.raises(AssertionError, match="missing"):
        _assert_enabled_rules(changed, expected)


def test_real_ruff_settings_match_repository_policy() -> None:
    expected = set(RULE_FIXTURE.read_text(encoding="utf-8").splitlines())
    settings = _ruff_settings()

    assert settings["target"] == "3.12"
    assert settings["max-complexity"] == MCCABE_MAX_COMPLEXITY
    assert settings["preview"] == "disabled"
    _assert_enabled_rules(settings["enabled"], expected)


def test_extensionless_python_inventory_is_exact() -> None:
    assert _tracked_extensionless_python() == set(EXTENSIONLESS_PYTHON)
    assert not {"bin/mypy-check", "bin/uv"} & set(EXTENSIONLESS_PYTHON)


def test_extensionless_policy_guard_fires_when_one_tool_is_omitted() -> None:
    incomplete = EXTENSIONLESS_PYTHON[:-1]
    with pytest.raises(AssertionError, match="pytest-worker-count"):
        _assert_extensionless_policy(incomplete)


def test_dom15_checker_entry_point_converts_internal_failure_to_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class CheckerFailure(Exception):
        pass

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _path, **_kwargs: (_ for _ in ()).throw(
            CheckerFailure("checker failed")
        ),
    )
    monkeypatch.setattr(sys, "argv", [str(ROOT / "bin/check-dom15-fixtures")])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(
            str(ROOT / "bin/check-dom15-fixtures"),
            run_name="__main__",
        )

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "check-dom15-fixtures: internal error: checker failed\n"
    assert captured.err == ""


def test_dom15_checker_entry_point_does_not_translate_fatal_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class CheckerFatal(BaseException):
        pass

    failure = CheckerFatal("fatal checker failure")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _path, **_kwargs: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(sys, "argv", [str(ROOT / "bin/check-dom15-fixtures")])

    with pytest.raises(CheckerFatal) as exc_info:
        runpy.run_path(
            str(ROOT / "bin/check-dom15-fixtures"),
            run_name="__main__",
        )

    assert exc_info.value is failure
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


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


def test_real_ruff_fires_default_and_retained_legacy_rules() -> None:
    probe = """\
def probe() -> None:
    try:
        raise ValueError
    except Exception:
        raise RuntimeError("probe")
"""
    result = _ruff(
        "check",
        "--config",
        str(PYPROJECT),
        "--stdin-filename",
        "probe.py",
        "--output-format",
        "json",
        "-",
        input_text=probe,
    )
    assert result.returncode == 1, result.stderr
    codes = {diagnostic["code"] for diagnostic in json.loads(result.stdout)}
    assert {"BLE001", "B904"} <= codes


def test_select_loses_default_sentinel_while_extend_select_keeps_both(
    tmp_path: Path,
) -> None:
    probe = """\
def probe() -> None:
    try:
        raise ValueError
    except Exception:
        raise RuntimeError("probe")
"""
    select_config = _write_ruff_config(
        tmp_path,
        name="select",
        selection_key="select",
    )
    extend_config = _write_ruff_config(tmp_path, name="extend")

    select_codes = _stdin_codes(select_config, probe)
    extend_codes = _stdin_codes(extend_config, probe)

    assert "BLE001" not in select_codes
    assert "B904" in select_codes
    assert {"BLE001", "B904"} <= extend_codes


@pytest.mark.parametrize(
    ("rule", "probe"),
    [
        ("E501", f'value = "{"x" * 100}"\n'),
        (
            "B008",
            """import datetime

def probe(
    value: datetime.datetime = datetime.datetime.now(),
) -> None:
    pass
""",
        ),
    ],
)
def test_global_ignore_semantics_fire_when_each_ignore_is_removed(
    tmp_path: Path,
    rule: str,
    probe: str,
) -> None:
    configured = _write_ruff_config(tmp_path, name="configured")
    removed = _write_ruff_config(
        tmp_path,
        name="removed",
        ignores=[code for code in GLOBAL_IGNORES if code != rule],
    )

    assert rule not in _stdin_codes(configured, probe)
    assert rule in _stdin_codes(removed, probe)


def test_real_per_file_ignore_hides_f401_but_policy_rejects_it(tmp_path: Path) -> None:
    configured = _write_ruff_config(tmp_path, name="configured")
    ignored = _write_ruff_config(
        tmp_path,
        name="ignored",
        per_file_ignores=True,
    )
    probe = "import os\n"

    assert "F401" in _stdin_codes(configured, probe)
    assert "F401" not in _stdin_codes(ignored, probe)

    candidate_ruff, candidate_lint = _activation_policy_config()
    _assert_ruff_policy(candidate_ruff, candidate_lint)
    candidate_lint["per-file-ignores"] = {"probe.py": ["F401"]}
    with pytest.raises(AssertionError):
        _assert_ruff_policy(candidate_ruff, candidate_lint)


def test_approved_suppressions_match_the_standalone_registry() -> None:
    tool = _load_suppression_tool()
    snapshot = tool.build_snapshot(
        ROOT,
        SUPPRESSION_REGISTRY.read_text(encoding="utf-8"),
    )

    assert EXPECTED_GROUP_COUNT == len(EXPECTED_GROUP_IDS)
    assert [group.group_id for group in snapshot.groups] == EXPECTED_GROUP_IDS
    assert len(snapshot.directives) == EXPECTED_DIRECTIVE_COUNT


def test_suppression_registry_is_not_part_of_required_reading() -> None:
    strategy_text = TESTING_STRATEGY_SPEC.read_text(encoding="utf-8")
    registry_text = SUPPRESSION_REGISTRY.read_text(encoding="utf-8")
    strategy_lines = strategy_text.splitlines()
    registry_lines = registry_text.splitlines()

    assert "../ruff-suppression-registry.md" in strategy_text
    assert "| Group | Rules | Approved cardinality |" not in strategy_text
    assert not any(
        line.startswith("Global raw-`noqa` inventory:") for line in strategy_lines
    )
    assert "<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->" not in strategy_text
    assert "| Group | Rules | Approved cardinality |" in registry_text
    assert any(
        line.startswith("Global raw-`noqa` inventory:") for line in registry_lines
    )
    assert "<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->" in registry_text

    required_reading = [
        ROOT / "AGENTS.md",
        ROOT / "docs" / "agent-context" / "context.index.yaml",
        *(ROOT / "docs" / "agent-context").rglob("*.md"),
    ]
    for path in required_reading:
        assert "ruff-suppression-registry.md" not in path.read_text(encoding="utf-8")


def test_suppression_checker_defaults_to_standalone_registry() -> None:
    tool = _load_suppression_tool()

    assert tool.DEFAULT_REGISTRY == "docs/ruff-suppression-registry.md"
    assert tool._parser().parse_args([]).registry == Path(
        "docs/ruff-suppression-registry.md"
    )
    assert tool._parser().parse_args(["--spec", "legacy.md"]).registry == Path(
        "legacy.md"
    )


def test_every_raw_c901_is_tagged_with_an_approved_group() -> None:
    diagnostics = _raw_c901_diagnostics()
    assert len(diagnostics) == EXPECTED_C901_DIRECTIVE_COUNT
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

    assert len(observed) == EXPECTED_C901_DIRECTIVE_COUNT


def test_ci_orders_complete_lint_suppression_format_and_type_gates() -> None:
    _assert_lint_job(_lint_job())


@pytest.mark.parametrize(
    "mutation",
    [
        "ruff-after-suppression",
        "suppression-after-format",
        "format-after-mypy",
        "formatter-dot",
        "formatter-missing-root",
        "preview",
    ],
)
def test_lint_job_guard_rejects_each_workflow_mutation(mutation: str) -> None:
    job = " ".join(_lint_job().split())
    _assert_lint_job(job)

    if mutation == "ruff-after-suppression":
        job = job.replace(RUFF_CHECK, "RUFF_PLACEHOLDER", 1)
        job = job.replace(SUPPRESSION_CHECK, RUFF_CHECK, 1)
        job = job.replace("RUFF_PLACEHOLDER", SUPPRESSION_CHECK, 1)
    elif mutation == "suppression-after-format":
        job = job.replace(SUPPRESSION_CHECK, "SUPPRESSION_PLACEHOLDER", 1)
        job = job.replace(FORMATTER_CHECK, SUPPRESSION_CHECK, 1)
        job = job.replace("SUPPRESSION_PLACEHOLDER", FORMATTER_CHECK, 1)
    elif mutation == "format-after-mypy":
        job = job.replace(FORMATTER_CHECK, "FORMATTER_PLACEHOLDER", 1)
        job = job.replace(MYPY_CHECK, FORMATTER_CHECK, 1)
        job = job.replace("FORMATTER_PLACEHOLDER", MYPY_CHECK, 1)
    elif mutation == "formatter-dot":
        job = job.replace(FORMATTER_CHECK, "ruff format --check .", 1)
    elif mutation == "formatter-missing-root":
        job = job.replace(" extensions/weft_microsandbox", "", 1)
    elif mutation == "preview":
        job = job.replace(RUFF_CHECK, f"{RUFF_CHECK} --preview", 1)
    else:  # pragma: no cover - parametrization is the closed mutation contract
        raise AssertionError(f"unknown mutation: {mutation}")

    with pytest.raises(AssertionError):
        _assert_lint_job(job)


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
