"""Behavior tests for the generated Ruff suppression index [TS-3], [TS-3.1]."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECK_COMMAND = "./.venv/bin/python bin/ruff_suppression_index.py --check"
WRITE_COMMAND = "./.venv/bin/python bin/ruff_suppression_index.py --write"
pytestmark = [pytest.mark.shared]


def _load_tool() -> ModuleType:
    path = ROOT / "bin" / "ruff_suppression_index.py"
    spec = importlib.util.spec_from_file_location("weft_ruff_suppression_index", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load suppression tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ruff_suppression_index = _load_tool()
repository_path = ruff_suppression_index.repository_path


def test_repository_path_uses_posix_separators() -> None:
    assert repository_path(PureWindowsPath("tests", "test_probe.py")) == (
        "tests/test_probe.py"
    )


def _write_fixture(repo: Path) -> Path:
    (repo / "pyproject.toml").write_text(
        """\
[tool.ruff.lint]
select = ["BLE001"]
""",
        encoding="utf-8",
    )
    (repo / "probe.py").write_text(
        """\
def contain_failure(callback):
    try:
        callback()
    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-001] exception
        return None
""",
        encoding="utf-8",
    )
    spec = repo / "policy.md"
    spec.write_text(
        """\
# Policy

### Approved Ruff Suppression Registry [TS-3.1]

| Group | Rules | Approved cardinality | Protected invariant | Real proof | Rejected alternatives | Approval |
|-------|-------|----------------------|---------------------|------------|-----------------------|----------|
| `RUFF-SUP-001` | `BLE001` | `1` directive; raw: `BLE001=1` | Callback failures stay contained. | Real fixture execution. | Narrow types miss callback failures. | approved |

Global raw-`noqa` inventory: `BLE001=1`

<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->
| Group | Locations | Directives | Raw diagnostics |
|-------|-----------|-----------:|-----------------|
| `RUFF-SUP-001` | `probe.py::stale_symbol` | 1 | `BLE001=1` |
<!-- END GENERATED RUFF SUPPRESSION INDEX -->

## Next section
Human-owned suffix.
""",
        encoding="utf-8",
    )
    return spec


def _run_tool(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "ruff_suppression_index.py"),
            "--repo-root",
            str(repo),
            "--registry",
            "policy.md",
            *args,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_write_repairs_a_stale_index_and_check_then_passes(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    original = spec.read_text(encoding="utf-8")

    stale = _run_tool(tmp_path, "--check")

    assert stale.returncode == 1
    assert "stale" in stale.stderr.lower()
    assert spec.read_text(encoding="utf-8") == original

    written = _run_tool(tmp_path, "--write")

    assert written.returncode == 0, written.stderr
    updated = spec.read_text(encoding="utf-8")
    assert "`probe.py::contain_failure`" in updated
    assert "`probe.py::stale_symbol`" not in updated
    assert (
        updated.split("<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->", 1)[0]
        == (original.split("<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->", 1)[0])
    )
    assert (
        updated.split("<!-- END GENERATED RUFF SUPPRESSION INDEX -->", 1)[1]
        == (original.split("<!-- END GENERATED RUFF SUPPRESSION INDEX -->", 1)[1])
    )

    current = _run_tool(tmp_path, "--check")

    assert current.returncode == 0, current.stderr
    before_second_write = spec.read_bytes()
    second_write = _run_tool(tmp_path, "--write")
    assert second_write.returncode == 0, second_write.stderr
    assert spec.read_bytes() == before_second_write


def test_deprecated_spec_option_targets_the_registry(tmp_path: Path) -> None:
    registry = _write_fixture(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "ruff_suppression_index.py"),
            "--repo-root",
            str(tmp_path),
            "--spec",
            "policy.md",
            "--write",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "`probe.py::contain_failure`" in registry.read_text(encoding="utf-8")


def test_write_cannot_approve_growth_in_an_existing_group(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    _run_tool(tmp_path, "--write")
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "Global raw-`noqa` inventory: `BLE001=1`",
            "Global raw-`noqa` inventory: `BLE001=2`",
        ),
        encoding="utf-8",
    )
    original = spec.read_text(encoding="utf-8")
    with (tmp_path / "probe.py").open("a", encoding="utf-8") as source:
        source.write(
            """\

def contain_another_failure(callback):
    try:
        callback()
    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-001] exception
        return None
"""
        )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "approved cardinality is 1" in result.stderr
    assert spec.read_text(encoding="utf-8") == original


def test_non_registry_noqa_changes_only_the_global_inventory(
    tmp_path: Path,
) -> None:
    spec = _write_fixture(tmp_path)
    assert _run_tool(tmp_path, "--write").returncode == 0
    original = spec.read_text(encoding="utf-8")
    (tmp_path / "local_reason.py").write_text(
        """\
def capture_test_outcome(callback):
    try:
        callback()
    except Exception:  # noqa: BLE001 - test captures the callback outcome
        return None
""",
        encoding="utf-8",
    )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "global raw-noqa inventory changed" in result.stderr
    assert "`BLE001=2`" in result.stderr
    assert spec.read_text(encoding="utf-8") == original


def test_unknown_source_group_fails_without_writing(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    original = spec.read_text(encoding="utf-8")
    source = tmp_path / "probe.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "[RUFF-SUP-001]",
            "[RUFF-SUP-999]",
        ),
        encoding="utf-8",
    )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "unknown group RUFF-SUP-999" in result.stderr
    assert "Traceback" not in result.stderr
    assert spec.read_text(encoding="utf-8") == original


def test_duplicate_human_group_fails_before_scanning(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    original = spec.read_text(encoding="utf-8")
    row = next(
        line for line in original.splitlines() if line.startswith("| `RUFF-SUP-001`")
    )
    spec.write_text(original.replace(f"{row}\n", f"{row}\n{row}\n"), encoding="utf-8")
    malformed = spec.read_text(encoding="utf-8")

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "duplicate human suppression group" in result.stderr
    assert "Traceback" not in result.stderr
    assert spec.read_text(encoding="utf-8") == malformed


def test_duplicate_generated_marker_fails_without_writing(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    malformed = spec.read_text(encoding="utf-8").replace(
        "<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->",
        "<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->\n"
        "<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->",
    )
    spec.write_text(malformed, encoding="utf-8")

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "expected exactly one" in result.stderr
    assert "Traceback" not in result.stderr
    assert spec.read_text(encoding="utf-8") == malformed


def test_marker_like_string_literal_is_inert(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    source = tmp_path / "probe.py"
    source.write_text(
        source.read_text(encoding="utf-8")
        + '\nMARKER_EXAMPLE = "# noqa: BLE001 approved [TS-3.1] '
        '[RUFF-SUP-999] exception"\n',
        encoding="utf-8",
    )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 0, result.stderr
    assert "RUFF-SUP-999" not in spec.read_text(encoding="utf-8")


def test_unreadable_source_is_exit_two_and_never_partially_writes(
    tmp_path: Path,
) -> None:
    spec = _write_fixture(tmp_path)
    original_spec = spec.read_text(encoding="utf-8")
    source = tmp_path / "probe.py"
    valid_source = source.read_bytes()
    source.write_bytes(b'# coding: ascii\nVALUE = "\xc3\xa9"\n')

    failed = _run_tool(tmp_path, "--write")

    assert failed.returncode == 2
    assert "probe.py" in failed.stderr
    assert "Traceback" not in failed.stderr
    assert spec.read_text(encoding="utf-8") == original_spec

    source.write_bytes(valid_source)
    repaired = _run_tool(tmp_path, "--write")

    assert repaired.returncode == 0, repaired.stderr


def test_raw_diagnostic_multiplicity_is_preserved(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """\
[tool.ruff.lint]
select = ["PYI036"]
""",
        encoding="utf-8",
    )
    (tmp_path / "probe.py").write_text(
        """\
from typing import Any


class Context:
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:  # noqa: PYI036 approved [TS-3.1] [RUFF-SUP-001] exception
        return None
""",
        encoding="utf-8",
    )
    spec = tmp_path / "policy.md"
    spec.write_text(
        """\
### Approved Ruff Suppression Registry [TS-3.1]

| Group | Rules | Approved cardinality | Protected invariant | Real proof | Rejected alternatives | Approval |
|-------|-------|----------------------|---------------------|------------|-----------------------|----------|
| `RUFF-SUP-001` | `PYI036` | `1` directive; raw: `PYI036=3` | Permissive annotations remain compatible. | Real Ruff execution. | Narrow annotations break callers. | approved |

Global raw-`noqa` inventory: `PYI036=3`

<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->
| Group | Locations | Directives | Raw diagnostics |
|-------|-----------|-----------:|-----------------|
| `RUFF-SUP-001` | `probe.py::stale_symbol` | 1 | `PYI036=1` |
<!-- END GENERATED RUFF SUPPRESSION INDEX -->
""",
        encoding="utf-8",
    )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 0, result.stderr
    generated = spec.read_text(encoding="utf-8")
    # Class-qualified: a bare "__exit__" would collide across classes.
    assert "`probe.py::Context.__exit__`" in generated
    assert "`PYI036=3`" in generated


def test_malformed_source_marker_fails_closed(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    original = spec.read_text(encoding="utf-8")
    source = tmp_path / "probe.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "[RUFF-SUP-001] exception",
            "[RUFF-SUP-001] exception trailing-junk",
        ),
        encoding="utf-8",
    )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "malformed approved suppression" in result.stderr
    assert spec.read_text(encoding="utf-8") == original


def test_write_preserves_crlf_and_non_ascii_bytes_outside_markers(
    tmp_path: Path,
) -> None:
    spec = _write_fixture(tmp_path)
    original = spec.read_bytes().replace(b"\r\n", b"\n")
    original = original.replace(b"Human-owned suffix.", "café".encode())
    original = original.replace(b"\n", b"\r\n")
    spec.write_bytes(original)
    begin = b"<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->"
    end = b"<!-- END GENERATED RUFF SUPPRESSION INDEX -->"

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 0, result.stderr
    updated = spec.read_bytes()
    assert updated.split(begin, 1)[0] == original.split(begin, 1)[0]
    assert updated.split(end, 1)[1] == original.split(end, 1)[1]
    generated = updated.split(begin, 1)[1].split(end, 1)[0]
    assert b"\n" not in generated.replace(b"\r\n", b"")


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_fenced_heading_and_markers_are_inert(tmp_path: Path, fence: str) -> None:
    spec = _write_fixture(tmp_path)
    text = spec.read_text(encoding="utf-8")
    example = f"""\
{fence}markdown
### Approved Ruff Suppression Registry [TS-3.1]
<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->
<!-- END GENERATED RUFF SUPPRESSION INDEX -->
{fence}

"""
    spec.write_text(
        text.replace("# Policy\n\n", f"# Policy\n\n{example}"), encoding="utf-8"
    )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 0, result.stderr
    assert spec.read_text(encoding="utf-8").startswith(f"# Policy\n\n{example}")


def test_symbol_attribution_covers_module_decorator_and_nested_function(
    tmp_path: Path,
) -> None:
    source = tmp_path / "symbols.py"
    source.write_text(
        """\
# noqa: F401 approved [TS-3.1] [RUFF-SUP-001] exception
def marker(function):
    return function

@marker  # noqa: F401 approved [TS-3.1] [RUFF-SUP-002] exception
def decorated():
    return None

def outer():
    def inner():
        return None  # noqa: F401 approved [TS-3.1] [RUFF-SUP-003] exception
    return inner
""",
        encoding="utf-8",
    )

    directives = ruff_suppression_index.scan_source_directives(
        tmp_path,
        [source],
    )

    assert [(item.group_id, item.symbol) for item in directives] == [
        ("RUFF-SUP-001", "<module>"),
        ("RUFF-SUP-002", "decorated"),
        ("RUFF-SUP-003", "outer"),
    ]


def test_syntactically_invalid_source_is_an_unverifiable_exit_two(
    tmp_path: Path,
) -> None:
    spec = _write_fixture(tmp_path)
    original = spec.read_bytes()
    with (tmp_path / "probe.py").open("a", encoding="utf-8") as source:
        source.write("\ndef broken()\n    return None\n")

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 2
    assert "probe.py" in result.stderr
    # The tool's own diagnostic, not CPython's phrasing: the SyntaxError text
    # differs between the tokenize and ast parse paths and across versions.
    assert "could not read python source" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert spec.read_bytes() == original


def test_registry_terms_in_an_ordinary_prose_comment_are_inert(
    tmp_path: Path,
) -> None:
    spec = _write_fixture(tmp_path)
    source = tmp_path / "probe.py"
    source.write_text(
        source.read_text(encoding="utf-8")
        + "\n# Documentation discusses approved [TS-3.1] and RUFF-SUP-001.\n",
        encoding="utf-8",
    )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 0, result.stderr
    assert "`probe.py::contain_failure`" in spec.read_text(encoding="utf-8")


def test_human_group_without_a_live_directive_fails(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    text = spec.read_text(encoding="utf-8")
    row = next(
        line for line in text.splitlines() if line.startswith("| `RUFF-SUP-001`")
    )
    empty_group = row.replace("RUFF-SUP-001", "RUFF-SUP-002")
    spec.write_text(
        text.replace(f"{row}\n", f"{row}\n{empty_group}\n"), encoding="utf-8"
    )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "RUFF-SUP-002 has no live source directives" in result.stderr


def test_missing_generated_marker_fails_without_writing(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    malformed = spec.read_text(encoding="utf-8").replace(
        "<!-- END GENERATED RUFF SUPPRESSION INDEX -->",
        "",
    )
    spec.write_text(malformed, encoding="utf-8")

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "expected exactly one" in result.stderr
    assert spec.read_text(encoding="utf-8") == malformed


def test_replacement_failure_leaves_the_spec_and_no_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = _write_fixture(tmp_path)
    original = spec.read_bytes()

    def fail_replace(source: Path, destination: Path) -> None:
        with Path(source).open("ab"):
            pass
        raise OSError(f"refused replacement of {destination}")

    monkeypatch.setattr(ruff_suppression_index.os, "replace", fail_replace)

    exit_code = ruff_suppression_index.main(
        [
            "--repo-root",
            str(tmp_path),
            "--registry",
            "policy.md",
            "--write",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "refused replacement" in captured.err
    assert "Traceback" not in captured.err
    assert spec.read_bytes() == original
    assert not list(tmp_path.glob(".policy.md.*.tmp"))


def test_repository_root_with_spaces_is_supported(tmp_path: Path) -> None:
    repo = tmp_path / "repository with spaces"
    repo.mkdir()
    _write_fixture(repo)

    result = _run_tool(repo, "--write")

    assert result.returncode == 0, result.stderr
    assert _run_tool(repo, "--check").returncode == 0


def test_non_python_file_in_ruff_discovery_is_not_tokenized(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    with (tmp_path / "pyproject.toml").open("a", encoding="utf-8") as config:
        config.write(
            '\n[tool.fixture]\nmarker_example = "# noqa: BLE001 approved [TS-3.1] '
            '[RUFF-SUP-999] exception"\n'
        )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("| `BLE001` |", "| `BLE001` trailing |", "malformed approved rules"),
        (
            "raw: `BLE001=1` |",
            "raw: `BLE001=1` trailing |",
            "malformed or non-canonical raw cardinality",
        ),
    ],
)
def test_human_policy_cells_require_exact_grammar(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    spec = _write_fixture(tmp_path)
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert message in result.stderr


def test_source_code_outside_the_group_rule_set_fails(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        """\
[tool.ruff.lint]
select = ["BLE001", "F841"]
""",
        encoding="utf-8",
    )
    source = tmp_path / "probe.py"
    source.write_text(
        source.read_text(encoding="utf-8")
        .replace("except Exception:", "except Exception as unused:")
        .replace("# noqa: BLE001 ", "# noqa: BLE001, F841 "),
        encoding="utf-8",
    )
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "Global raw-`noqa` inventory: `BLE001=1`",
            "Global raw-`noqa` inventory: `BLE001=1`, `F841=1`",
        ),
        encoding="utf-8",
    )

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "RUFF-SUP-001 does not approve F841" in result.stderr


def test_markdown_unsafe_source_path_fails_before_rendering(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    (tmp_path / "probe.py").rename(tmp_path / "probe`unsafe.py")
    original = spec.read_bytes()

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "cannot be represented in the Markdown index" in result.stderr
    assert spec.read_bytes() == original


def test_duplicate_source_code_fails_closed(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    source = tmp_path / "probe.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "# noqa: BLE001 ",
            "# noqa: BLE001, BLE001 ",
        ),
        encoding="utf-8",
    )
    original = spec.read_bytes()

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "duplicate noqa code" in result.stderr
    assert spec.read_bytes() == original


def test_missing_spec_is_a_clean_exit_two(tmp_path: Path) -> None:
    result = _run_tool(tmp_path, "--check")

    assert result.returncode == 2
    assert "could not read" in result.stderr
    assert "policy.md" in result.stderr
    assert "Traceback" not in result.stderr


def test_reversed_generated_markers_fail_without_writing(tmp_path: Path) -> None:
    spec = _write_fixture(tmp_path)
    text = spec.read_text(encoding="utf-8")
    begin = "<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->"
    end = "<!-- END GENERATED RUFF SUPPRESSION INDEX -->"
    malformed = (
        text.replace(begin, "TEMP MARKER")
        .replace(end, begin)
        .replace(
            "TEMP MARKER",
            end,
        )
    )
    spec.write_text(malformed, encoding="utf-8")

    result = _run_tool(tmp_path, "--write")

    assert result.returncode == 1
    assert "markers are reversed" in result.stderr
    assert spec.read_text(encoding="utf-8") == malformed


def _write_untagged_c901_fixture(repo: Path) -> Path:
    """Write a current registry plus one globally counted, unregistered C901."""

    (repo / "pyproject.toml").write_text(
        """\
[tool.ruff.lint]
select = ["C901"]

[tool.ruff.lint.mccabe]
max-complexity = 10
""",
        encoding="utf-8",
    )
    branches = "\n".join(
        f"    if value == {index}:\n        return {index}" for index in range(11)
    )
    (repo / "probe.py").write_text(
        (
            "def approved(value):  # noqa: C901 approved [TS-3.1] "
            "[RUFF-SUP-001] exception\n"
            f"{branches}\n"
            "    return -1\n\n"
            "def unregistered(value):  # noqa: C901\n"
            f"{branches}\n"
            "    return -1\n"
        ),
        encoding="utf-8",
    )
    spec = repo / "policy.md"
    spec.write_text(
        """\
### Approved Ruff Suppression Registry [TS-3.1]

| Group | Rules | Approved cardinality | Protected invariant | Real proof | Rejected alternatives | Approval |
|---|---|---|---|---|---|---|
| `RUFF-SUP-001` | `C901` | `1` directive; raw: `C901=1` | Branch order stays local. | Fixture. | Split obscures order. | approved |

Global raw-`noqa` inventory: `C901=2`

<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->
| Group | Locations | Directives | Raw diagnostics |
|---|---|---:|---|
| `RUFF-SUP-001` | `probe.py::approved` | 1 | `C901=1` |
<!-- END GENERATED RUFF SUPPRESSION INDEX -->
""",
        encoding="utf-8",
    )
    return spec


@pytest.mark.parametrize("mode", ["--check", "--write"])
def test_untagged_c901_fails_even_when_global_inventory_matches(
    tmp_path: Path,
    mode: str,
) -> None:
    spec = _write_untagged_c901_fixture(tmp_path)
    original = spec.read_bytes()

    result = _run_tool(tmp_path, mode)

    assert result.returncode == 1
    assert "untagged C901" in result.stderr
    assert "Traceback" not in result.stderr
    assert spec.read_bytes() == original


def test_normal_ruff_valid_non_list_json_is_tool_failure_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Wrong-shaped normal-Ruff JSON is an anticipated tool failure."""

    spec = _write_fixture(tmp_path)
    original = spec.read_bytes()

    def fake_ruff(
        repo_root: Path,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        if "--show-files" in args:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=f"{repo_root / 'probe.py'}\n",
                stderr="",
            )
        assert args == ("check", "--output-format", "json", ".")
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="null",
            stderr="",
        )

    monkeypatch.setattr(ruff_suppression_index, "_run_ruff", fake_ruff)

    returncode = ruff_suppression_index.main(
        [
            "--repo-root",
            str(tmp_path),
            "--registry",
            str(spec),
            "--write",
        ]
    )

    captured = capsys.readouterr()
    assert returncode == 2
    assert "normal Ruff check returned malformed JSON" in captured.err
    assert "Traceback" not in captured.err
    assert spec.read_bytes() == original


@pytest.mark.parametrize(
    ("raw_result", "message"),
    [
        ((1, "not-json", ""), "Ruff raw audit returned invalid JSON"),
        ((1, "null", ""), "Ruff raw audit returned a non-list JSON payload"),
        ((1, "[null]", ""), "Ruff raw audit returned a malformed diagnostic"),
        ((2, "[]", "raw failed"), "Ruff raw audit failed: raw failed"),
    ],
)
def test_raw_ruff_failures_are_clean_exit_two_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raw_result: tuple[int, str, str],
    message: str,
) -> None:
    spec = _write_fixture(tmp_path)
    original = spec.read_bytes()

    def fake_ruff(
        repo_root: Path,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        if "--show-files" in args:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=f"{repo_root / 'probe.py'}\n",
                stderr="",
            )
        if "--ignore-noqa" not in args:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="[]",
                stderr="",
            )
        returncode, stdout, stderr = raw_result
        return subprocess.CompletedProcess(
            args=args,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    monkeypatch.setattr(ruff_suppression_index, "_run_ruff", fake_ruff)

    returncode = ruff_suppression_index.main(
        ["--repo-root", str(tmp_path), "--registry", str(spec), "--write"]
    )

    captured = capsys.readouterr()
    assert returncode == 2
    assert message in captured.err
    assert "Traceback" not in captured.err
    assert spec.read_bytes() == original


def test_ruff_discovery_failure_is_clean_exit_two_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = _write_fixture(tmp_path)
    original = spec.read_bytes()

    monkeypatch.setattr(
        ruff_suppression_index,
        "_run_ruff",
        lambda _repo_root, *args: subprocess.CompletedProcess(
            args=args,
            returncode=2,
            stdout="",
            stderr="discovery failed",
        ),
    )

    returncode = ruff_suppression_index.main(
        ["--repo-root", str(tmp_path), "--registry", str(spec), "--write"]
    )

    captured = capsys.readouterr()
    assert returncode == 2
    assert "Ruff file discovery failed: discovery failed" in captured.err
    assert "Traceback" not in captured.err
    assert spec.read_bytes() == original


def test_ruff_invocation_failure_is_clean_exit_two_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = _write_fixture(tmp_path)
    original = spec.read_bytes()

    def fail_to_run(*_args: object, **_kwargs: object) -> None:
        raise OSError("ruff unavailable")

    monkeypatch.setattr(ruff_suppression_index.subprocess, "run", fail_to_run)

    returncode = ruff_suppression_index.main(
        ["--repo-root", str(tmp_path), "--registry", str(spec), "--write"]
    )

    captured = capsys.readouterr()
    assert returncode == 2
    assert "could not run Ruff: ruff unavailable" in captured.err
    assert "Traceback" not in captured.err
    assert spec.read_bytes() == original
