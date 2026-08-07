"""Validate and regenerate the repository's Ruff suppression location index.

Repository commands:

    ./.venv/bin/python bin/ruff_suppression_index.py --check
    ./.venv/bin/python bin/ruff_suppression_index.py --write

Registry: docs/ruff-suppression-registry.md
Spec: docs/specifications/08-Testing_Strategy.md [TS-3], [TS-3.1]
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tokenize
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath

REGISTRY_HEADING = "### Approved Ruff Suppression Registry [TS-3.1]"
BEGIN_MARKER = "<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->"
END_MARKER = "<!-- END GENERATED RUFF SUPPRESSION INDEX -->"
DEFAULT_REGISTRY = "docs/ruff-suppression-registry.md"
GLOBAL_INVENTORY_PREFIX = "Global raw-`noqa` inventory:"

_GROUP_PATTERN = r"RUFF-SUP-\d{3}"
_SOURCE_MARKER = re.compile(
    r"(?:^|[ \t])# noqa:[ \t]+"
    r"(?P<codes>[A-Z]+\d+(?:[ \t]*,[ \t]*[A-Z]+\d+)*)[ \t]+"
    r"approved \[TS-3\.1\] "
    rf"\[(?P<group>{_GROUP_PATTERN})\] exception[ \t]*$"
)
_GROUP_CELL = re.compile(rf"`(?P<group>{_GROUP_PATTERN})`")
_RULE_CODE = re.compile(r"`(?P<code>[A-Z]+\d+)`")
_RULES_CELL = re.compile(r"`[A-Z]+\d+`(?:,\s*`[A-Z]+\d+`)*")
_APPROVED_CARDINALITY = re.compile(
    r"`(?P<directives>\d+)` directives?; raw:\s*(?P<raw>.+)"
)
_RAW_COUNT = re.compile(r"`(?P<code>[A-Z]+\d+)=(?P<count>\d+)`")
_FENCE_OPEN = re.compile(r"(?P<fence>`{3,}|~{3,})")
_SECTION_END = re.compile(r"#{1,4}\s")


class PolicyMismatch(Exception):
    """The repository does not satisfy the suppression policy."""


class ToolFailure(Exception):
    """The tool could not obtain or write trustworthy evidence."""


@dataclass(frozen=True)
class HumanGroup:
    """One human-approved suppression group."""

    group_id: str
    rules: frozenset[str]
    approved_directives: int
    approved_raw: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class SourceDirective:
    """One approved local Ruff directive.

    ``line`` stays the internal identity used to match raw diagnostics and to
    report errors. ``symbol`` is what the derived index renders: a line number
    moves whenever anything above it changes, so a line-keyed index churns on
    edits that alter no suppression and stays silent when a suppression
    migrates between functions at a constant count.
    """

    path: str
    line: int
    symbol: str
    codes: frozenset[str]
    group_id: str


@dataclass(frozen=True)
class RawDiagnostic:
    """One Ruff diagnostic before local suppression."""

    path: str
    line: int
    code: str


@dataclass(frozen=True)
class SuppressionSnapshot:
    """The reconciled evidence needed to render and audit the index."""

    groups: tuple[HumanGroup, ...]
    directives: tuple[SourceDirective, ...]
    raw_diagnostics: tuple[RawDiagnostic, ...]
    rendered_index: str


@dataclass(frozen=True)
class RegistryLayout:
    """Line-aware boundaries for the live, unfenced registry section."""

    human_lines: tuple[str, ...]
    begin_end: int
    end_start: int
    newline: str


def repository_path(path: PurePath) -> str:
    """Return a stable repository-relative path spelling."""

    return path.as_posix()


def _index_path(path: Path, repo_root: Path) -> str:
    relative = repository_path(path.relative_to(repo_root))
    if any(character in relative for character in ("`", "|", "\n", "\r")):
        raise PolicyMismatch(
            f"{relative!r} cannot be represented in the Markdown index"
        )
    return relative


def _table_cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _line_content(raw_line: str) -> str:
    return raw_line.removesuffix("\n").removesuffix("\r")


def _outside_fence_lines(text: str) -> list[tuple[str, int, int]]:
    lines: list[tuple[str, int, int]] = []
    offset = 0
    fence_character: str | None = None
    fence_width = 0
    for raw_line in text.splitlines(keepends=True):
        content = _line_content(raw_line)
        stripped = content.lstrip()
        if fence_character is None:
            opening = _FENCE_OPEN.match(stripped)
            if opening is not None:
                fence = opening.group("fence")
                fence_character = fence[0]
                fence_width = len(fence)
            else:
                lines.append((content, offset, offset + len(content)))
        else:
            closing = re.fullmatch(
                rf"{re.escape(fence_character)}{{{fence_width},}}[ \t]*",
                stripped,
            )
            if closing is not None:
                fence_character = None
                fence_width = 0
        offset += len(raw_line)
    if fence_character is not None:
        raise PolicyMismatch("unclosed Markdown fence in suppression registry")
    return lines


def _registry_layout(text: str) -> RegistryLayout:
    active_lines = _outside_fence_lines(text)
    heading_lines = [line for line in active_lines if line[0] == REGISTRY_HEADING]
    if len(heading_lines) != 1:
        raise PolicyMismatch(f"expected exactly one {REGISTRY_HEADING!r} heading")
    _, _, heading_end = heading_lines[0]

    section_end = len(text)
    for content, start, _ in active_lines:
        if start > heading_end and _SECTION_END.match(content):
            section_end = start
            break
    section_lines = [
        line for line in active_lines if heading_end < line[1] < section_end
    ]
    begin_lines = [line for line in section_lines if line[0] == BEGIN_MARKER]
    end_lines = [line for line in section_lines if line[0] == END_MARKER]
    if len(begin_lines) != 1:
        raise PolicyMismatch(f"expected exactly one {BEGIN_MARKER!r} marker")
    if len(end_lines) != 1:
        raise PolicyMismatch(f"expected exactly one {END_MARKER!r} marker")
    _, begin_start, begin_end = begin_lines[0]
    _, end_start, _ = end_lines[0]
    if begin_start >= end_start:
        raise PolicyMismatch("generated suppression index markers are reversed")

    human_lines = tuple(
        content for content, start, _ in section_lines if start < begin_start
    )
    marker_line_end = text.find("\n", begin_end)
    newline = (
        "\r\n"
        if marker_line_end > begin_end and text[marker_line_end - 1] == "\r"
        else "\n"
    )
    return RegistryLayout(human_lines, begin_end, end_start, newline)


def _table_rows(layout: RegistryLayout) -> tuple[str, ...]:
    header = (
        "| Group | Rules | Approved cardinality | Protected invariant | "
        "Real proof | Rejected alternatives | Approval |"
    )
    header_indexes = [
        index for index, line in enumerate(layout.human_lines) if line == header
    ]
    if len(header_indexes) != 1:
        raise PolicyMismatch("expected exactly one human suppression table")
    index = header_indexes[0] + 1
    if index >= len(layout.human_lines):
        raise PolicyMismatch("human suppression table is missing its separator row")
    separator = _table_cells(layout.human_lines[index])
    if len(separator) != 7 or any(
        re.fullmatch(r":?-{3,}:?", cell) is None for cell in separator
    ):
        raise PolicyMismatch("human suppression table is missing its separator row")
    rows: list[str] = []
    for line in layout.human_lines[index + 1 :]:
        if not line.startswith("|"):
            break
        rows.append(line)
    if not rows:
        raise PolicyMismatch("human suppression registry is empty")
    return tuple(rows)


def _parse_approved_raw(
    group_id: str, cardinality_text: str
) -> tuple[int, Counter[str]]:
    cardinality = _APPROVED_CARDINALITY.fullmatch(cardinality_text)
    if cardinality is None:
        raise PolicyMismatch(f"malformed approved cardinality for {group_id}")
    approved_directives = int(cardinality.group("directives"))
    approved_raw = Counter(
        {
            code: int(count)
            for code, count in _RAW_COUNT.findall(cardinality.group("raw"))
        }
    )
    if _raw_text(approved_raw) != cardinality.group("raw"):
        raise PolicyMismatch(
            f"malformed or non-canonical raw cardinality for {group_id}"
        )
    if approved_directives < 1 or not approved_raw:
        raise PolicyMismatch(f"{group_id} must approve live suppression evidence")
    return approved_directives, approved_raw


def _parse_human_group(row: str) -> HumanGroup:
    cells = _table_cells(row)
    if len(cells) != 7:
        raise PolicyMismatch("malformed human suppression table row")
    group_match = _GROUP_CELL.fullmatch(cells[0])
    if group_match is None:
        raise PolicyMismatch(f"malformed suppression group cell: {cells[0]}")
    group_id = group_match.group("group")
    if _RULES_CELL.fullmatch(cells[1]) is None:
        raise PolicyMismatch(f"malformed approved rules for {group_id}")
    if any(not cell for cell in cells[3:]):
        raise PolicyMismatch(f"{group_id} has an empty human-owned rationale cell")

    rule_codes = _RULE_CODE.findall(cells[1])
    if len(rule_codes) != len(set(rule_codes)):
        raise PolicyMismatch(f"duplicate approved rule for {group_id}")
    rules = frozenset(rule_codes)
    approved_directives, approved_raw = _parse_approved_raw(group_id, cells[2])
    if set(approved_raw) != rules:
        raise PolicyMismatch(
            f"{group_id} raw cardinality codes do not match its approved rules"
        )
    return HumanGroup(
        group_id=group_id,
        rules=rules,
        approved_directives=approved_directives,
        approved_raw=tuple(sorted(approved_raw.items())),
    )


def parse_human_groups(registry_text: str) -> tuple[HumanGroup, ...]:
    """Parse only the human-owned registry table before the generated marker."""

    groups: list[HumanGroup] = []
    seen: set[str] = set()
    for row in _table_rows(_registry_layout(registry_text)):
        group = _parse_human_group(row)
        if group.group_id in seen:
            raise PolicyMismatch(f"duplicate human suppression group: {group.group_id}")
        seen.add(group.group_id)
        groups.append(group)
    return tuple(groups)


def parse_global_inventory(registry_text: str) -> Counter[str]:
    """Parse the human-owned inventory of every raw locally suppressed finding."""

    layout = _registry_layout(registry_text)
    inventory_lines = [
        line for line in layout.human_lines if line.startswith(GLOBAL_INVENTORY_PREFIX)
    ]
    if len(inventory_lines) != 1:
        raise PolicyMismatch("expected exactly one global raw-noqa inventory")
    inventory_text = inventory_lines[0].removeprefix(GLOBAL_INVENTORY_PREFIX).strip()
    items = _RAW_COUNT.findall(inventory_text)
    inventory = Counter({code: int(count) for code, count in items})
    rendered = ", ".join(
        f"`{code}={count}`" for code, count in sorted(inventory.items())
    )
    if not inventory or rendered != inventory_text:
        raise PolicyMismatch("malformed or non-canonical global raw-noqa inventory")
    return inventory


def _run_ruff(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["ruff", *args],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ToolFailure(f"could not run Ruff: {exc}") from exc


def _resolve_ruff_path(repo_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = repo_root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ToolFailure(
            f"Ruff reported a file outside the repository: {path}"
        ) from exc
    _index_path(resolved, repo_root)
    return resolved


def discover_python_files(repo_root: Path) -> tuple[Path, ...]:
    """Use Ruff as the sole owner of the repository's Python file inventory."""

    result = _run_ruff(repo_root, "check", "--show-files", ".")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise ToolFailure(f"Ruff file discovery failed: {detail}")
    files: list[Path] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        resolved = _resolve_ruff_path(repo_root, line)
        if resolved.suffix in {".py", ".pyi"}:
            files.append(resolved)
            continue
        if resolved.suffix:
            continue
        try:
            with resolved.open("rb") as source:
                first_line = source.readline()
        except OSError as exc:
            display = repository_path(resolved.relative_to(repo_root))
            raise ToolFailure(
                f"{display}: could not read discovered source: {exc}"
            ) from exc
        if first_line.startswith(b"#!") and b"python" in first_line.lower():
            files.append(resolved)
    return tuple(sorted(set(files)))


MODULE_SYMBOL = "<module>"


def _symbol_spans(tree: ast.Module) -> list[tuple[int, int, str]]:
    """Return (start, end, qualified_name) for every outermost function.

    Nested functions are deliberately not recorded: their lines fall inside the
    enclosing function's span, so a directive inside a closure is attributed to
    the function a reviewer would actually look at rather than to
    ``outer.<locals>.inner``. Names are class-qualified because bare names
    collide -- ``db.py`` alone has six ``__init__`` and four ``close``.
    """

    spans: list[tuple[int, int, str]] = []

    def walk(node: ast.AST, classes: tuple[str, ...], in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, (*classes, child.name), in_function)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if not in_function:
                    # Decorators sit above `def`; a directive on one belongs to
                    # the function it decorates.
                    start = min(
                        [child.lineno, *(d.lineno for d in child.decorator_list)]
                    )
                    end = child.end_lineno or child.lineno
                    spans.append((start, end, ".".join((*classes, child.name))))
                walk(child, classes, True)
            else:
                walk(child, classes, in_function)

    walk(tree, (), False)
    return spans


def _resolve_symbol(spans: Sequence[tuple[int, int, str]], line: int) -> str:
    """Attribute a line to its enclosing outermost function, else the module."""
    for start, end, name in spans:
        if start <= line <= end:
            return name
    return MODULE_SYMBOL


def scan_source_directives(
    repo_root: Path,
    paths: Sequence[Path],
) -> tuple[SourceDirective, ...]:
    """Read approved markers from Python comment tokens only."""

    directives: list[SourceDirective] = []
    for path in paths:
        try:
            with tokenize.open(path) as source:
                text = source.read()
                spans = _symbol_spans(ast.parse(text))
                comments = (
                    token
                    for token in tokenize.generate_tokens(io.StringIO(text).readline)
                    if token.type == tokenize.COMMENT
                )
                for comment in comments:
                    has_registry_pointer = "# noqa:" in comment.string and (
                        "approved [TS-3.1]" in comment.string
                        or "RUFF-SUP-" in comment.string
                    )
                    matches = list(_SOURCE_MARKER.finditer(comment.string))
                    if not has_registry_pointer and not matches:
                        continue
                    relative = repository_path(path.relative_to(repo_root))
                    if len(matches) != 1:
                        raise PolicyMismatch(
                            f"{relative}:{comment.start[0]}: malformed approved suppression"
                        )
                    match = matches[0]
                    codes = tuple(
                        code.strip() for code in match.group("codes").split(",")
                    )
                    if len(codes) != len(set(codes)):
                        raise PolicyMismatch(
                            f"{relative}:{comment.start[0]}: duplicate noqa code"
                        )
                    directives.append(
                        SourceDirective(
                            path=relative,
                            line=comment.start[0],
                            symbol=_resolve_symbol(spans, comment.start[0]),
                            codes=frozenset(codes),
                            group_id=match.group("group"),
                        )
                    )
        except PolicyMismatch:
            raise
        except (OSError, SyntaxError, UnicodeError, tokenize.TokenError) as exc:
            try:
                relative = repository_path(path.relative_to(repo_root))
            except ValueError:
                relative = str(path)
            raise ToolFailure(
                f"{relative}: could not read Python source: {exc}"
            ) from exc
    return tuple(
        sorted(directives, key=lambda item: (item.group_id, item.path, item.line))
    )


def _parse_ruff_json(
    repo_root: Path,
    result: subprocess.CompletedProcess[str],
) -> tuple[RawDiagnostic, ...]:
    if result.returncode not in {0, 1}:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise ToolFailure(f"Ruff raw audit failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ToolFailure("Ruff raw audit returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise ToolFailure("Ruff raw audit returned a non-list JSON payload")

    diagnostics: list[RawDiagnostic] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ToolFailure("Ruff raw audit returned a malformed diagnostic")
        try:
            path = _resolve_ruff_path(repo_root, str(item["filename"])).relative_to(
                repo_root
            )
            code = item["code"]
            line = item["noqa_row"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolFailure("Ruff raw audit returned a malformed diagnostic") from exc
        if not isinstance(code, str) or not isinstance(line, int):
            raise ToolFailure("Ruff raw audit returned a malformed diagnostic")
        diagnostics.append(RawDiagnostic(repository_path(path), line, code))
    return tuple(
        sorted(diagnostics, key=lambda item: (item.path, item.line, item.code))
    )


def collect_raw_diagnostics(repo_root: Path) -> tuple[RawDiagnostic, ...]:
    """Return all Ruff diagnostics with local suppressions disabled."""

    result = _run_ruff(
        repo_root,
        "check",
        "--ignore-noqa",
        "--output-format",
        "json",
        ".",
    )
    return _parse_ruff_json(repo_root, result)


def _require_normal_ruff(repo_root: Path) -> None:
    result = _run_ruff(repo_root, "check", "--output-format", "json", ".")
    if result.returncode == 0:
        return
    if result.returncode == 1:
        try:
            diagnostics = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ToolFailure("normal Ruff check returned invalid JSON") from exc
        if not isinstance(diagnostics, list) or any(
            not isinstance(diagnostic, dict) for diagnostic in diagnostics
        ):
            raise ToolFailure("normal Ruff check returned malformed JSON")
        for diagnostic in diagnostics:
            code = diagnostic.get("code")
            message = str(diagnostic.get("message", "invalid syntax"))
            if code not in {None, "invalid-syntax"}:
                continue
            try:
                filename = _resolve_ruff_path(
                    repo_root,
                    str(diagnostic.get("filename", "<unknown>")),
                )
                display = repository_path(filename.relative_to(repo_root))
            except ToolFailure:
                display = str(diagnostic.get("filename", "<unknown>"))
            raise ToolFailure(f"{display}: invalid syntax: {message}")
        detail = result.stdout.strip() or result.stderr.strip() or "unknown finding"
        raise PolicyMismatch(f"normal Ruff check is not clean: {detail}")
    detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
    raise ToolFailure(f"normal Ruff check failed: {detail}")


def _raw_text(counts: Counter[str]) -> str:
    return ", ".join(f"`{code}={count}`" for code, count in sorted(counts.items()))


def _render_locations(directives: Sequence[SourceDirective]) -> str:
    """Render one entry per distinct site, keyed by qualified symbol.

    Deduplicated: several directives inside one symbol render once. Counts
    stay in the human-owned per-group cardinality column, so this reads as a
    set of sites and a suppression moving between symbols shows as a -/+ pair.
    """
    sites = {(directive.path, directive.symbol) for directive in directives}
    return "; ".join(f"`{path}::{symbol}`" for path, symbol in sorted(sites))


def _index_directives(
    groups: Sequence[HumanGroup],
    directives: Sequence[SourceDirective],
) -> dict[str, list[SourceDirective]]:
    group_by_id = {group.group_id: group for group in groups}
    by_group: dict[str, list[SourceDirective]] = defaultdict(list)
    seen_locations: set[tuple[str, int]] = set()
    for directive in directives:
        group = group_by_id.get(directive.group_id)
        if group is None:
            raise PolicyMismatch(
                f"{directive.path}:{directive.line}: unknown group {directive.group_id}"
            )
        if not directive.codes <= group.rules:
            unexpected = ", ".join(sorted(directive.codes - group.rules))
            raise PolicyMismatch(
                f"{directive.path}:{directive.line}: {directive.group_id} "
                f"does not approve {unexpected}"
            )
        location = (directive.path, directive.line)
        if location in seen_locations:
            raise PolicyMismatch(
                f"{directive.path}:{directive.line}: duplicate approved directive"
            )
        seen_locations.add(location)
        by_group[directive.group_id].append(directive)
    return by_group


def _raw_by_location(
    diagnostics: Sequence[RawDiagnostic],
) -> dict[tuple[str, int], Counter[str]]:
    by_location: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    for diagnostic in diagnostics:
        by_location[(diagnostic.path, diagnostic.line)][diagnostic.code] += 1
    return by_location


def _validate_c901_registration(
    directives: Sequence[SourceDirective],
    diagnostics: Sequence[RawDiagnostic],
) -> None:
    """Require every raw C901 finding to have one TS-3.1 source owner."""

    owners = Counter(
        (directive.path, directive.line, "C901")
        for directive in directives
        if "C901" in directive.codes
    )
    for diagnostic in diagnostics:
        if diagnostic.code != "C901":
            continue
        key = (diagnostic.path, diagnostic.line, diagnostic.code)
        owner_count = owners[key]
        if owner_count == 0:
            raise PolicyMismatch(
                f"{diagnostic.path}:{diagnostic.line}: untagged C901 diagnostic"
            )
        if owner_count != 1:
            raise PolicyMismatch(
                f"{diagnostic.path}:{diagnostic.line}: C901 diagnostic has "
                f"{owner_count} approved source owners"
            )


def _validate_group(
    group: HumanGroup,
    directives: Sequence[SourceDirective],
    raw_by_location: dict[tuple[str, int], Counter[str]],
) -> Counter[str]:
    if not directives:
        raise PolicyMismatch(f"{group.group_id} has no live source directives")
    if len(directives) != group.approved_directives:
        raise PolicyMismatch(
            f"{group.group_id} has {len(directives)} directives; "
            f"approved cardinality is {group.approved_directives}"
        )
    used_rules = set().union(*(directive.codes for directive in directives))
    if used_rules != group.rules:
        raise PolicyMismatch(
            f"{group.group_id} live directive rules do not match its approved rules"
        )

    group_raw: Counter[str] = Counter()
    for directive in directives:
        actual_raw = raw_by_location[(directive.path, directive.line)]
        if set(actual_raw) != directive.codes:
            raise PolicyMismatch(
                f"{directive.path}:{directive.line}: noqa codes "
                "do not match raw Ruff diagnostics"
            )
        group_raw.update(actual_raw)
    approved_raw = Counter(dict(group.approved_raw))
    if group_raw != approved_raw:
        raise PolicyMismatch(
            f"{group.group_id} raw diagnostics {_raw_text(group_raw)}; "
            f"approved cardinality is {_raw_text(approved_raw)}"
        )
    return group_raw


def reconcile(
    groups: Sequence[HumanGroup],
    directives: Sequence[SourceDirective],
    raw_diagnostics: Sequence[RawDiagnostic],
) -> str:
    """Validate the complete semantic graph and render its generated table."""

    directives_by_group = _index_directives(groups, directives)
    _validate_c901_registration(directives, raw_diagnostics)
    raw_locations = _raw_by_location(raw_diagnostics)
    rows = [
        "| Group | Locations | Directives | Raw diagnostics |",
        "|-------|-----------|-----------:|-----------------|",
    ]
    for group in sorted(groups, key=lambda item: item.group_id):
        group_directives = directives_by_group[group.group_id]
        group_raw = _validate_group(group, group_directives, raw_locations)
        rows.append(
            f"| `{group.group_id}` | {_render_locations(group_directives)} | "
            f"{len(group_directives)} | {_raw_text(group_raw)} |"
        )
    return "\n".join(rows)


def build_snapshot(repo_root: Path, registry_text: str) -> SuppressionSnapshot:
    """Collect and reconcile all evidence without mutating the repository."""

    groups = parse_human_groups(registry_text)
    global_inventory = parse_global_inventory(registry_text)
    paths = discover_python_files(repo_root)
    directives = scan_source_directives(repo_root, paths)
    _require_normal_ruff(repo_root)
    raw_diagnostics = collect_raw_diagnostics(repo_root)
    actual_global_inventory = Counter(diagnostic.code for diagnostic in raw_diagnostics)
    if actual_global_inventory != global_inventory:
        raise PolicyMismatch(
            "global raw-noqa inventory changed: "
            f"actual {_raw_text(actual_global_inventory)}; "
            f"approved {_raw_text(global_inventory)}"
        )
    rendered_index = reconcile(groups, directives, raw_diagnostics)
    return SuppressionSnapshot(groups, directives, raw_diagnostics, rendered_index)


def render_registry(registry_text: str, rendered_index: str) -> str:
    """Replace only the uniquely delimited generated index."""

    layout = _registry_layout(registry_text)
    index = rendered_index.replace("\n", layout.newline)
    return (
        f"{registry_text[: layout.begin_end]}{layout.newline}"
        f"{index}{layout.newline}{registry_text[layout.end_start :]}"
    )


def _atomic_replace(path: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise ToolFailure(f"could not replace {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def run(*, repo_root: Path, registry: Path, write: bool) -> SuppressionSnapshot:
    """Check or regenerate one repository suppression index."""

    root = repo_root.resolve()
    registry_path = registry if registry.is_absolute() else root / registry
    try:
        original = registry_path.read_bytes()
        registry_text = original.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ToolFailure(f"could not read {registry_path}: {exc}") from exc

    snapshot = build_snapshot(root, registry_text)
    updated = render_registry(registry_text, snapshot.rendered_index)
    updated_bytes = updated.encode("utf-8")
    if updated_bytes == original:
        return snapshot
    if not write:
        raise PolicyMismatch(
            f"{repository_path(registry_path)}: generated Ruff suppression index is stale"
        )
    _atomic_replace(registry_path, updated_bytes)
    return snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or regenerate the Ruff suppression location index."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate without writing")
    mode.add_argument("--write", action="store_true", help="regenerate the index")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--registry",
        "--spec",
        type=Path,
        default=Path(DEFAULT_REGISTRY),
        dest="registry",
        help="path to the standalone suppression registry (--spec is deprecated)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run(repo_root=args.repo_root, registry=args.registry, write=args.write)
    except PolicyMismatch as exc:
        print(f"ruff-suppression-index: {exc}", file=sys.stderr)
        return 1
    except ToolFailure as exc:
        print(f"ruff-suppression-index: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
