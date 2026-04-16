"""Tests for plan-corpus metadata hygiene."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLANS_DIR = Path(__file__).resolve().parents[2] / "docs" / "plans"
README_PATH = PLANS_DIR / "README.md"
ALLOWED_STATUSES = frozenset(
    {"active", "proposed", "completed", "roadmap", "audit-log", "findings"}
)

pytestmark = [pytest.mark.shared]


def _plan_files() -> list[Path]:
    return sorted(path for path in PLANS_DIR.glob("*.md") if path.name != "README.md")


def _metadata_block(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    title_index = next(
        (index for index, line in enumerate(lines) if line.startswith("# ")),
        None,
    )
    assert title_index is not None, f"{path} is missing a level-1 title"

    metadata_lines: list[str] = []
    for line in lines[title_index + 1 :]:
        if not line.strip():
            if metadata_lines:
                break
            continue
        metadata_lines.append(line.strip())
        if len(metadata_lines) == 3:
            break

    assert len(metadata_lines) == 3, f"{path} is missing the normalized metadata block"

    keys = [line.split(":", 1)[0] for line in metadata_lines]
    assert keys == ["Status", "Source specs", "Superseded by"], (
        f"{path} metadata keys are out of order: {keys}"
    )

    return {
        key: value.strip()
        for key, value in (line.split(":", 1) for line in metadata_lines)
    }


def test_plan_readme_exists_and_defines_status_taxonomy() -> None:
    readme_text = README_PATH.read_text(encoding="utf-8")

    assert "## Status Taxonomy" in readme_text
    assert "## Index" in readme_text


def test_every_plan_has_normalized_metadata() -> None:
    for path in _plan_files():
        metadata = _metadata_block(path)
        assert metadata["Status"] in ALLOWED_STATUSES
        assert metadata["Source specs"]
        superseded_by = metadata["Superseded by"]
        if superseded_by != "none":
            match = re.search(r"\./([^) ]+\.md)", superseded_by)
            assert match is not None, f"{path} has an invalid Superseded by value"
            target = PLANS_DIR / match.group(1)
            assert target.exists(), f"{path} points to a missing superseding plan"


def test_plan_readme_indexes_every_plan_file() -> None:
    readme_text = README_PATH.read_text(encoding="utf-8")
    linked_files = set(re.findall(r"\]\(\./([^)]+\.md)\)", readme_text))

    expected = {path.name for path in _plan_files()}
    assert expected.issubset(linked_files), (
        f"Plan README is missing index entries for: {sorted(expected - linked_files)}"
    )


def test_plan_readme_statuses_match_plan_metadata() -> None:
    readme_text = README_PATH.read_text(encoding="utf-8")
    readme_lines = readme_text.splitlines()

    for path in _plan_files():
        metadata = _metadata_block(path)
        row = next(
            (
                line
                for line in readme_lines
                if line.startswith("| ") and f"[`{path.name}`](./{path.name})" in line
            ),
            None,
        )
        assert row is not None, f"Plan README row for {path.name} is missing"
        assert f"| `{metadata['Status']}` |" in row, (
            f"Plan README row for {path.name} is missing or has a stale status"
        )
