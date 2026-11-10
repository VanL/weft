"""Tests for plan-corpus metadata hygiene.

Spec references:
- docs/program-theory.md [WT-0], [WT-7], [REV-WT-007]
- docs/agent-context/decision-hierarchy.md [DOM-15]
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLANS_DIR = Path(__file__).resolve().parents[2] / "docs" / "plans"
README_PATH = PLANS_DIR / "README.md"
WT7_GATE_CUTOFF_DATE = "2026-11-10"
WT7_REQUIRED_PARTS = (
    "Verdict",
    "Rests on",
    "Refuses",
    "Predicts",
    "Fits worst",
)
ALLOWED_STATUSES = frozenset(
    {
        "completed",
        "draft",
        "promoted",
        "withdrawn",
    }
)
_BARE_PLACEHOLDERS = frozenset(
    {
        "n/a",
        "na",
        "none",
        "not yet run",
        "pending",
        "tbd",
        "todo",
    }
)
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
_CLASS_DECL_RE = re.compile(
    r"^(?:(?:[-*]|\d+\.)\s+)?(?:([*_`]{1,2})Class(?:\1:|:\1)|Class:)\s*(.*)$"
)
_CLASS_TOKEN_RE = re.compile(r"^([0-5])(\+P)?$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_QUOTED_SPAN_RE = re.compile(r"`[^`]*`|\"[^\"]*\"|'[^']*'")
_UNQUOTED_PLACEHOLDER_RE = re.compile(
    r"(?i)\b(?:tbd|todo|not yet run|pending execution)\b"
)
_LIST_PREFIX_RE = re.compile(r"^(?:[-*]|\d+\.)\s+")
_FINAL_APPROVE_LINE_RE = re.compile(
    r"^(?:(?:[-*]|\d+\.)\s+)?[*_`]*Final Completion Review Verdict[*_`]*:[*_`]*\s*"
    r"[*_`]*APPROVE[*_`]*\s*(?:\(([^)\n]*)\))?\s*$"
)
_REVIEWER_FAMILY_LINE_RE = re.compile(
    r"^(?:(?:[-*]|\d+\.)\s+)?[*_`]*Reviewer family[^:\n]*[*_`]*:[*_`]*\s*(.*)$",
    re.IGNORECASE,
)
_NEGATIVE_TOKEN_RE = re.compile(r"(?i)\b(?:REVISE|BLOCKED|FAIL)\b")
_VERDICT_LABEL_PREFIX = (
    r"(?:(?:[-*]|\d+\.)\s+)?"
    r"(?![*_`]*(?:the|a|an|this|that|these|those|when|if|while|because|although|since)\b)"
    r"[*_`]*[A-Za-z0-9 ()\[\]./_#*+\u2013\u2014-]{0,60}\bverdict\b[*_`]*:[*_`]*"
)
_SINGLE_LINE_NEG_VERDICT_RE = re.compile(
    rf"(?i)^{_VERDICT_LABEL_PREFIX}\s*[*_`]*\b(?:REVISE|BLOCKED|FAIL)\b"
)
_WRAPPED_VERDICT_HEADER_RE = re.compile(rf"(?i)^{_VERDICT_LABEL_PREFIX}\s*$")
_WRAPPED_NEG_VALUE_RE = re.compile(
    r"(?i)^(?:(?:[-*]|\d+\.)\s+)?[*_`]*\b(?:REVISE|BLOCKED|FAIL)\b"
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


def _prose_lines_outside_fences(lines: list[str]) -> list[str]:
    prose: list[str] = []
    open_fence: str | None = None
    for line in lines:
        match = _FENCE_RE.match(line)
        if match is not None:
            marker = match.group(1)
            if open_fence is None:
                open_fence = marker
                continue
            if marker[0] == open_fence[0] and len(marker) >= len(open_fence):
                open_fence = None
                continue
        if open_fence is None:
            prose.append(line)
    return prose


def _normalize_item_text(raw: str) -> str:
    without_list = _LIST_PREFIX_RE.sub("", raw.strip())
    return without_list.strip(" \t*_`\"'.").strip()


def _is_placeholder_value(raw: str) -> bool:
    normalized = _normalize_item_text(raw)
    if not normalized or normalized.lower() in _BARE_PLACEHOLDERS:
        return True
    unquoted = _QUOTED_SPAN_RE.sub(" ", raw)
    return _UNQUOTED_PLACEHOLDER_RE.search(unquoted) is not None


def _is_effective_class5(path: Path, prose_lines: list[str]) -> bool:
    effective_five = False
    saw_class = False
    for line in prose_lines:
        decl = _CLASS_DECL_RE.match(line.strip())
        if decl is None:
            continue
        saw_class = True
        raw_value = decl.group(2).strip()
        first_word = (
            re.split(r"[\s(\u2013\u2014]+", raw_value, maxsplit=1)[0]
            if raw_value
            else ""
        )
        cleaned_token = first_word.lstrip("*_`\"'").rstrip("*_`\"',;.:")
        parsed = _CLASS_TOKEN_RE.match(cleaned_token)
        assert parsed is not None, (
            f"{path} has unrecognized Class declaration: {line.strip()!r}"
        )
        if int(parsed.group(1)) >= 5 or parsed.group(2) == "+P":
            effective_five = True
    assert saw_class, f"{path} is missing a Class declaration"
    return effective_five


def _slice_heading_range(
    prose_lines: list[str],
    *,
    prefix: str,
    pattern: str,
) -> tuple[int, int] | None:
    compiled = re.compile(pattern, re.IGNORECASE)
    start_idx: int | None = None
    for idx, line in enumerate(prose_lines):
        if line.startswith(prefix) and not line.startswith(prefix + "#"):
            if start_idx is not None:
                return start_idx, idx
            if compiled.search(line):
                start_idx = idx
    return None if start_idx is None else (start_idx, len(prose_lines))


def _match_part_header(
    stripped: str,
    part_patterns: dict[str, re.Pattern[str]],
) -> tuple[str, str] | None:
    for part, pattern in part_patterns.items():
        match = pattern.match(stripped)
        if match is not None:
            return part, match.group(1).strip()
    return None


def _extract_wt7_part_items(fit_lines: list[str]) -> dict[str, list[str]]:
    items: dict[str, list[str]] = {}
    active_part: str | None = None
    part_patterns = {
        part: re.compile(
            rf"^(?:[-*]|\d+\.)\s+[*_`]*{re.escape(part)}[*_`]*:[*_`]*\s*(.*)$",
            re.IGNORECASE,
        )
        for part in WT7_REQUIRED_PARTS
    }
    for line in fit_lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            active_part = None
            continue
        matched = _match_part_header(stripped, part_patterns)
        if matched is not None:
            active_part, remainder = matched
            items.setdefault(active_part, [])
            if remainder:
                items[active_part].append(remainder)
            continue
        if active_part is not None and line.startswith(("  ", "\t")):
            if stripped:
                items[active_part].append(stripped)
        elif _LIST_PREFIX_RE.match(stripped):
            active_part = None
    return items


def _assert_non_placeholder_wt7_fit(path: Path, review_lines: list[str]) -> None:
    fit_range = _slice_heading_range(
        review_lines,
        prefix="### ",
        pattern=r"Theory Fit|Fit Account",
    )
    target_lines = (
        review_lines[fit_range[0] : fit_range[1]]
        if fit_range is not None
        else review_lines
    )
    for line in target_lines:
        unquoted_line = _QUOTED_SPAN_RE.sub(" ", line)
        assert not _UNQUOTED_PLACEHOLDER_RE.search(unquoted_line), (
            f"{path} contains placeholder text in its [WT-7] Theory Fit section: {line!r}"
        )
    part_items = _extract_wt7_part_items(target_lines)
    for part in WT7_REQUIRED_PARTS:
        entries = part_items.get(part, [])
        non_empty = [entry for entry in entries if _normalize_item_text(entry)]
        assert non_empty, f"{path} is missing [WT-7] Theory Fit part '{part}'"
        for entry in non_empty:
            assert not _is_placeholder_value(entry), (
                f"{path} has placeholder/pending value for [WT-7] part '{part}': {entry!r}"
            )


def _enclosing_subsection(prose_lines: list[str], target_idx: int) -> list[str]:
    start = 0
    for idx in range(target_idx, -1, -1):
        if prose_lines[idx].startswith(("## ", "### ")):
            start = idx
            break
    end = len(prose_lines)
    for idx in range(target_idx + 1, len(prose_lines)):
        if prose_lines[idx].startswith(("## ", "### ")):
            end = idx
            break
    return prose_lines[start:end]


def _has_trailing_negative_verdict(lines_after_approve: list[str]) -> str | None:
    awaiting_wrapped_verdict = False
    for line in lines_after_approve:
        stripped = line.strip()
        if not stripped:
            awaiting_wrapped_verdict = False
            continue
        if _SINGLE_LINE_NEG_VERDICT_RE.search(stripped) is not None:
            return stripped
        if awaiting_wrapped_verdict and _WRAPPED_NEG_VALUE_RE.match(stripped):
            return stripped
        awaiting_wrapped_verdict = (
            _WRAPPED_VERDICT_HEADER_RE.search(stripped) is not None
        )
    return None


def _assert_completion_review_verdict(
    path: Path,
    prose_lines: list[str],
    review_range: tuple[int, int],
) -> None:
    approve_indices: list[int] = []
    for idx in range(review_range[0], len(prose_lines)):
        match = _FINAL_APPROVE_LINE_RE.match(prose_lines[idx].strip())
        if match is not None:
            paren_note = match.group(1) or ""
            if _NEGATIVE_TOKEN_RE.search(paren_note) is None:
                approve_indices.append(idx)
    assert approve_indices, (
        f"{path} is missing a standalone 'Final Completion Review Verdict: APPROVE' line"
    )
    last_approve_idx = approve_indices[-1]
    subsection = _enclosing_subsection(prose_lines, last_approve_idx)
    valid_reviewers = [
        match.group(1)
        for line in subsection
        if (match := _REVIEWER_FAMILY_LINE_RE.match(line.strip())) is not None
        and not _is_placeholder_value(match.group(1))
    ]
    assert valid_reviewers, (
        f"{path} is missing non-placeholder 'Reviewer family' metadata in the completion review subsection"
    )
    trailing = _has_trailing_negative_verdict(prose_lines[last_approve_idx + 1 :])
    assert trailing is None, (
        f"{path} has trailing REVISE/BLOCKED/FAIL verdict after Final Completion Review Verdict: APPROVE: {trailing!r}"
    )


def validate_plan_wt7_completion_gate(path: Path) -> None:
    """Enforce [REV-WT-007] forward-only [WT-7] & completion-verdict gate."""
    date_match = _DATE_PREFIX_RE.match(path.name)
    if date_match is None or date_match.group(1) < WT7_GATE_CUTOFF_DATE:
        return
    metadata = _metadata_block(path)
    if metadata["Status"] != "completed":
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    prose_lines = _prose_lines_outside_fences(lines)
    if not _is_effective_class5(path, prose_lines):
        return
    review_range = _slice_heading_range(
        prose_lines,
        prefix="## ",
        pattern=r"Review",
    )
    assert review_range is not None, f"{path} is missing a top-level Review section"
    review_lines = prose_lines[review_range[0] : review_range[1]]
    _assert_non_placeholder_wt7_fit(path, review_lines)
    _assert_completion_review_verdict(path, prose_lines, review_range)


def test_plan_readme_exists_and_defines_status_taxonomy() -> None:
    readme_text = README_PATH.read_text(encoding="utf-8")

    assert "## Status Taxonomy" in readme_text
    assert "## Status Index" in readme_text


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


def test_every_completed_plan_satisfies_wt7_completion_gate() -> None:
    for path in _plan_files():
        validate_plan_wt7_completion_gate(path)


def _write_synthetic_plan(
    tmp_path: Path,
    filename: str,
    *,
    status: str = "completed",
    plan_class: str = "5",
    fit_section: str | None = None,
    review_section: str | None = None,
) -> Path:
    default_fit = (
        "### 8.1 Author's `[WT-7]` Theory Fit Account\n\n"
        "- **Verdict**: Fits.\n"
        "- **Rests on**:\n"
        "  - `[WT-2a.2]` queue truth.\n"
        "- **Refuses**:\n"
        "  - `[ALT-WT-020]` sidecar reads.\n"
        "- **Predicts**:\n"
        "  - Pending inbox messages remain untouched while queue-only reads succeed.\n"
        "- **Fits worst**:\n"
        "  - `[WT-4.4]` latency margin.\n"
    )
    default_review = (
        "### 8.3 Cross-Family Plan Review Log\n\n"
        "- **Reviewer family**: OpenAI Codex CLI (`codex exec -s read-only`)\n"
        "- Final Completion Review Verdict: APPROVE\n"
    )
    body = (
        f"# Synthetic Plan\n\n"
        f"Status: {status}\n"
        f"Source specs: `docs/program-theory.md` ([WT-7])\n"
        f"Superseded by: none\n\n"
        f"Class: {plan_class}\n\n"
        f"## 8. Independent Review Loop\n\n"
        f"{default_fit if fit_section is None else fit_section}\n"
        f"{default_review if review_section is None else review_section}\n"
    )
    plan_path = tmp_path / filename
    plan_path.write_text(body, encoding="utf-8")
    return plan_path


def test_wt7_completion_gate_exempts_pre_cutoff_and_non_completed_plans(
    tmp_path: Path,
) -> None:
    pre_cutoff = _write_synthetic_plan(
        tmp_path,
        "2026-11-09-legacy-class5-plan.md",
        status="completed",
        plan_class="5",
        fit_section="### 8.1 Theory Fit\n\n- Other-family blind account: not yet run\n",
        review_section="### 8.3 Review\n\n- Plan Verdict: APPROVE\n",
    )
    validate_plan_wt7_completion_gate(pre_cutoff)

    forward_draft = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-draft-class5-plan.md",
        status="draft",
        plan_class="5",
        fit_section="### 8.1 Theory Fit\n\n- Pending.\n",
        review_section="### 8.3 Review\n\n- Pending.\n",
    )
    validate_plan_wt7_completion_gate(forward_draft)

    forward_class3 = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-ordinary-class3-plan.md",
        status="completed",
        plan_class="3",
        fit_section="",
        review_section="### 8.3 Review\n\n- Verdict: PASS\n",
    )
    validate_plan_wt7_completion_gate(forward_class3)


@pytest.mark.parametrize("plan_class", ["5", "5+P", "3+P", "4+P"])
def test_wt7_completion_gate_accepts_valid_effective_class5_plans(
    tmp_path: Path,
    plan_class: str,
) -> None:
    valid = _write_synthetic_plan(
        tmp_path,
        f"2026-11-10-valid-{plan_class.replace('+', 'plus')}-plan.md",
        status="completed",
        plan_class=plan_class,
    )
    validate_plan_wt7_completion_gate(valid)


def test_wt7_completion_gate_rejects_missing_parts_placeholders_and_missing_verdict(
    tmp_path: Path,
) -> None:
    missing_refuses = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-missing-refuses.md",
        fit_section=(
            "### 8.1 Theory Fit\n\n"
            "- **Verdict**: Fits.\n"
            "- **Rests on**: `[WT-2a.2]`\n"
            "- **Predicts**: Works.\n"
            "- **Fits worst**: `[WT-4]`\n"
        ),
    )
    with pytest.raises(AssertionError, match="Refuses"):
        validate_plan_wt7_completion_gate(missing_refuses)

    placeholder_fit = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-placeholder-fit.md",
        plan_class="3+P",
        fit_section=(
            "### 8.1 Theory Fit\n\n"
            "- **Verdict**: Fits.\n"
            "- **Rests on**: `[WT-2a.2]`\n"
            "- **Refuses**: `[ALT-WT-020]`\n"
            "- **Predicts**: Other-family blind account: not yet run\n"
            "- **Fits worst**: `[WT-4]`\n"
        ),
    )
    with pytest.raises(AssertionError, match="placeholder"):
        validate_plan_wt7_completion_gate(placeholder_fit)

    bare_pending_part = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-bare-pending-part.md",
        plan_class="4+P",
        fit_section=(
            "### 8.1 Theory Fit\n\n"
            "- **Verdict**: Fits.\n"
            "- **Rests on**: `[WT-2a.2]`\n"
            "- **Refuses**: `[ALT-WT-020]`\n"
            "- **Predicts**: *Pending.*\n"
            "- **Fits worst**: `[WT-4]`\n"
        ),
    )
    with pytest.raises(AssertionError, match="pending"):
        validate_plan_wt7_completion_gate(bare_pending_part)

    plan_approve_only = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-plan-approve-only.md",
        review_section=(
            "### 8.3 Review\n\n"
            "- **Reviewer family**: OpenAI Codex CLI\n"
            "- Plan Review Verdict: APPROVE\n"
        ),
    )
    with pytest.raises(
        AssertionError, match="Final Completion Review Verdict: APPROVE"
    ):
        validate_plan_wt7_completion_gate(plan_approve_only)

    missing_reviewer = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-missing-reviewer.md",
        review_section=(
            "### 8.3 Review\n\n- Final Completion Review Verdict: APPROVE\n"
        ),
    )
    with pytest.raises(AssertionError, match="Reviewer family"):
        validate_plan_wt7_completion_gate(missing_reviewer)

    trailing_revise = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-trailing-revise.md",
        review_section=(
            "### 8.3 Review\n\n"
            "- **Reviewer family**: OpenAI Codex CLI\n"
            "- Final Completion Review Verdict: APPROVE\n"
            "- Round 2 Verdict: BLOCKED\n"
        ),
    )
    with pytest.raises(AssertionError, match="trailing"):
        validate_plan_wt7_completion_gate(trailing_revise)


def test_wt7_completion_gate_hardened_parsing_edge_cases(tmp_path: Path) -> None:
    # 1. Plan 4's own prose (which quotes `"not yet run"` inside explanatory
    # Predicts prose) must pass when flipped to completed with valid approval.
    plan4_text = (
        PLANS_DIR / "2026-11-10-program-theory-fit-cadence-and-gate-plan.md"
    ).read_text(encoding="utf-8")
    completed_plan4 = tmp_path / "2026-11-10-plan4-sim.md"
    completed_plan4.write_text(
        plan4_text.replace("Status: draft", "Status: completed", 1)
        + "\n### 8.4 Completion Review\n\n"
        + "- **Reviewer family**: OpenAI Codex CLI\n"
        + "- Final Completion Review Verdict: APPROVE\n",
        encoding="utf-8",
    )
    validate_plan_wt7_completion_gate(completed_plan4)

    # 2. Colon-inside-bold empty field (`- **Verdict:**`) and numbered `1. pending` must fail,
    # while backtick-formatted `- \`Verdict\`: Fits.` must pass.
    empty_bold_colon = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-empty-bold-colon.md",
        fit_section=(
            "### 8.1 Theory Fit\n\n"
            "- **Verdict:**\n"
            "- **Rests on:** `[WT-2a.2]`\n"
            "- **Refuses:** `[ALT-WT-020]`\n"
            "- **Predicts:** Works.\n"
            "- **Fits worst:** `[WT-4]`\n"
        ),
    )
    with pytest.raises(AssertionError, match="Verdict"):
        validate_plan_wt7_completion_gate(empty_bold_colon)

    numbered_pending = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-numbered-pending.md",
        fit_section=(
            "### 8.1 Theory Fit\n\n"
            "- **Verdict**: Fits.\n"
            "- **Rests on**: `[WT-2a.2]`\n"
            "- **Refuses**: `[ALT-WT-020]`\n"
            "- **Predicts**:\n"
            "  1. pending\n"
            "- **Fits worst**: `[WT-4]`\n"
        ),
    )
    with pytest.raises(AssertionError, match="pending"):
        validate_plan_wt7_completion_gate(numbered_pending)

    backtick_labels = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-backtick-labels.md",
        fit_section=(
            "### 8.1 Theory Fit\n\n"
            "- `Verdict`: Fits.\n"
            "- `Rests on`: `[WT-2a.2]`\n"
            "- `Refuses`: `[ALT-WT-020]`\n"
            "- `Predicts`: Works.\n"
            "- `Fits worst`: `[WT-4]`\n"
        ),
    )
    validate_plan_wt7_completion_gate(backtick_labels)

    # 3. Backtick `Class: \`3+P\`` and escalated `Class: 3` -> `Class: 5+P` must trigger gate.
    backtick_class = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-backtick-class.md",
        plan_class="`3+P`",
        review_section="### 8.3 Review\n\n- Verdict: PASS\n",
    )
    with pytest.raises(AssertionError):
        validate_plan_wt7_completion_gate(backtick_class)

    escalated_class = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-escalated-class.md",
        plan_class="3\nClass: 5+P (escalated)",
        review_section="### 8.3 Review\n\n- Verdict: PASS\n",
    )
    with pytest.raises(AssertionError):
        validate_plan_wt7_completion_gate(escalated_class)

    # 4. Reviewer family must belong to the completion-review subsection and be non-empty.
    wrong_subsection_reviewer = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-wrong-sub-reviewer.md",
        review_section=(
            "### 8.3 Plan Review\n\n"
            "- **Reviewer family**: OpenAI Codex CLI\n\n"
            "### 8.4 Completion Review\n\n"
            "- Final Completion Review Verdict: APPROVE\n"
        ),
    )
    with pytest.raises(AssertionError, match="Reviewer family"):
        validate_plan_wt7_completion_gate(wrong_subsection_reviewer)

    empty_reviewer_bold = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-empty-reviewer-bold.md",
        review_section=(
            "### 8.3 Completion Review\n\n"
            "- **Reviewer family:**\n"
            "- Final Completion Review Verdict: APPROVE\n"
        ),
    )
    with pytest.raises(AssertionError, match="Reviewer family"):
        validate_plan_wt7_completion_gate(empty_reviewer_bold)

    # 5. Narrative mention, same-line REVISE, wrapped REVISE, and later H2 Review REVISE must fail.
    narrative_mention = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-narrative-mention.md",
        review_section=(
            "### 8.3 Completion Review\n\n"
            "- **Reviewer family**: OpenAI Codex CLI\n"
            "- Note: Final Completion Review Verdict: APPROVE has not been recorded.\n"
        ),
    )
    with pytest.raises(
        AssertionError, match="Final Completion Review Verdict: APPROVE"
    ):
        validate_plan_wt7_completion_gate(narrative_mention)

    same_line_revise = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-same-line-revise.md",
        review_section=(
            "### 8.3 Completion Review\n\n"
            "- **Reviewer family**: OpenAI Codex CLI\n"
            "- Final Completion Review Verdict: APPROVE / Verdict: REVISE\n"
        ),
    )
    with pytest.raises(AssertionError):
        validate_plan_wt7_completion_gate(same_line_revise)

    wrapped_revise = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-wrapped-revise.md",
        review_section=(
            "### 8.3 Completion Review\n\n"
            "- **Reviewer family**: OpenAI Codex CLI\n"
            "- Final Completion Review Verdict: APPROVE\n"
            "- Verdict:\n"
            "  REVISE\n"
        ),
    )
    with pytest.raises(AssertionError, match="trailing"):
        validate_plan_wt7_completion_gate(wrapped_revise)

    later_h2_revise = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-later-h2-revise.md",
        review_section=(
            "### 8.3 Completion Review\n\n"
            "- **Reviewer family**: OpenAI Codex CLI\n"
            "- Final Completion Review Verdict: APPROVE\n\n"
            "## Pre-landing Review\n\n"
            "- Verdict: REVISE\n"
        ),
    )
    with pytest.raises(AssertionError, match="trailing"):
        validate_plan_wt7_completion_gate(later_h2_revise)

    # 6. Malformed/empty Class:, wrapped `- **Verdict:**\n REVISE`, and narrative
    # backtick mention in Verification prose.
    for bad_class in ("", "3+Q", "3+Pfoo"):
        malformed_class = _write_synthetic_plan(
            tmp_path,
            f"2026-11-10-bad-class-{bad_class or 'empty'}.md",
            plan_class=bad_class,
        )
        with pytest.raises(AssertionError, match="unrecognized Class"):
            validate_plan_wt7_completion_gate(malformed_class)

    bold_wrapped_revise = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-bold-wrapped-revise.md",
        review_section=(
            "### 8.3 Completion Review\n\n"
            "- **Reviewer family**: OpenAI Codex CLI\n"
            "- Final Completion Review Verdict: APPROVE\n"
            "- **Verdict:**\n"
            "  REVISE\n"
        ),
    )
    with pytest.raises(AssertionError, match="trailing"):
        validate_plan_wt7_completion_gate(bold_wrapped_revise)

    narrative_backtick_in_later_section = _write_synthetic_plan(
        tmp_path,
        "2026-11-10-narrative-backtick-later.md",
        review_section=(
            "### 8.3 Completion Review\n\n"
            "- **Reviewer family**: OpenAI Codex CLI\n"
            "- Final Completion Review Verdict: APPROVE\n\n"
            "## 9. Verification Notes\n\n"
            "The negative fixture contains `Verdict: FAIL` and is correctly rejected.\n"
        ),
    )
    validate_plan_wt7_completion_gate(narrative_backtick_in_later_section)

    # 7. Punctuated declaration labels (`Round 2 (completion) Verdict: REVISE`,
    # `Round 2.1 Verdict: BLOCKED`) must fail, and markup+comma Class (`**5+P**, ...`,
    # `` `5+P`, ... ``) must parse cleanly and enforce the gate.
    for idx, neg_line in enumerate(
        (
            "- Round 2 (completion) Verdict: REVISE",
            "- Round 2.1 Verdict: BLOCKED",
            "- **Round 2.1 (completion) Verdict:**\n  REVISE",
        )
    ):
        punctuated_neg = _write_synthetic_plan(
            tmp_path,
            f"2026-11-10-punctuated-neg-{idx}.md",
            review_section=(
                "### 8.3 Completion Review\n\n"
                "- **Reviewer family**: OpenAI Codex CLI\n"
                "- Final Completion Review Verdict: APPROVE\n"
                f"{neg_line}\n"
            ),
        )
        with pytest.raises(AssertionError, match="trailing"):
            validate_plan_wt7_completion_gate(punctuated_neg)

    for idx, valid_marked_class in enumerate(
        ("**5+P**, process change", "`5+P`, process change", "`3+P`; governance")
    ):
        marked_comma_plan = _write_synthetic_plan(
            tmp_path,
            f"2026-11-10-marked-comma-{idx}.md",
            plan_class=valid_marked_class,
        )
        validate_plan_wt7_completion_gate(marked_comma_plan)
        marked_comma_missing_verdict = _write_synthetic_plan(
            tmp_path,
            f"2026-11-10-marked-comma-fail-{idx}.md",
            plan_class=valid_marked_class,
            review_section="### 8.3 Completion Review\n\n- Verdict: PASS\n",
        )
        with pytest.raises(
            AssertionError, match="Final Completion Review Verdict: APPROVE"
        ):
            validate_plan_wt7_completion_gate(marked_comma_missing_verdict)

    # 8. Unbulleted bold/italic Class labels (`**Class:** 5+P`, `*Class:* 5+P`) and
    # star-bulleted `* **Class:** 5+P` must not misparse leading `*` as a list marker.
    for idx, class_line in enumerate(
        ("**Class:** 5+P", "*Class:* 5+P", "* **Class:** 5+P", "**Class**: **5+P**, ok")
    ):
        bold_label_plan = tmp_path / f"2026-11-10-bold-class-label-{idx}.md"
        bold_label_plan.write_text(
            "# Synthetic Plan\n\n"
            "Status: completed\n"
            "Source specs: `docs/program-theory.md` ([WT-7])\n"
            "Superseded by: none\n\n"
            f"{class_line}\n\n"
            "## 8. Independent Review Loop\n\n"
            "### 8.1 Theory Fit\n\n"
            "- **Verdict**: Fits.\n"
            "- **Rests on**: `[WT-2a.2]`\n"
            "- **Refuses**: `[ALT-WT-020]`\n"
            "- **Predicts**: Works.\n"
            "- **Fits worst**: `[WT-4]`\n\n"
            "### 8.3 Completion Review\n\n"
            "**Reviewer family:** OpenAI Codex CLI\n"
            "**Final Completion Review Verdict:** APPROVE\n",
            encoding="utf-8",
        )
        validate_plan_wt7_completion_gate(bold_label_plan)
