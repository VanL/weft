"""High-signal hygiene checks for spec/code traceability."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "docs" / "specifications"

pytestmark = [pytest.mark.shared]


def test_monitor_justification_remains_in_system_invariants() -> None:
    """OBS.13 should retain the explicit monitor rationale."""

    text = (SPEC_DIR / "07-System_Invariants.md").read_text(encoding="utf-8")

    assert "Dealing with processes can be messy" in text


def test_obs13_is_decomposed_into_sub_invariants() -> None:
    """OBS.13 should stay readable as a decomposed monitor contract."""

    text = (SPEC_DIR / "07-System_Invariants.md").read_text(encoding="utf-8")

    assert "**OBS.13**" in text
    assert "**OBS.13.1**" in text, (
        "OBS.13 must be decomposed into sub-invariants (OBS.13.1, ...)"
    )


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
