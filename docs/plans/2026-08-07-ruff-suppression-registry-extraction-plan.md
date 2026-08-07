# Ruff Suppression Registry Extraction Plan

Status: completed
Source specs: docs/specifications/08-Testing_Strategy.md [TS-3], [TS-3.1]
Superseded by: none

Class: 5+P. This edits normative [TS-3.1] ownership and changes where future
suppression work is recorded and verified. It does not change approved
suppressions or runtime behavior.

Plan type: implementation with spec revision. Promotion strategy: B — atomic.
The small ownership clarification, reciprocal plan link, checker target, and
policy tests land as one change so the implementation mapping never points to
a missing ledger. Hardening: N/A — no risky trigger.

## Spec Baseline

- `5ea1f2e4a9d1e1d685fd558eb01324afb0e57ebf` —
  `docs/specifications/08-Testing_Strategy.md` [TS-3], [TS-3.1] before the
  extraction.
- Promotion baseline: `5ea1f2e4a9d1e1d685fd558eb01324afb0e57ebf`
  plus the current worktree diff for Spec 08 and this plan. The atomic slice is
  not complete until both land together.

## Proposed Spec Delta

Promotion strategy B replaces the in-spec ledger body under [TS-3.1] with
normative text that:

- keeps the section's suppression-approval ownership, directive and table
  grammar, inventory grammar, generated-index contract, refusal modes, exit
  codes, and byte-preservation contract in Spec 08;
- assigns only the human rows, aggregate inventory, and generated index to
  `docs/ruff-suppression-registry.md`;
- declares that operational ledger non-normative and not required reading;
- retargets the implementation mapping to the standalone registry and adds
  this plan to `## Related Plans`.

Exact [TS-3] implementation-mapping replacement:

```markdown
`bin/ruff_suppression_index.py` parses the standalone [TS-3.1 operational
registry](../ruff-suppression-registry.md), invokes normal and raw Ruff,
enforces C901 registration completeness and cardinality, and checks or
atomically rewrites only the generated index;
```

Exact `## Related Plans` insertion:

```markdown
- [`docs/plans/2026-08-07-ruff-suppression-registry-extraction-plan.md`](../plans/2026-08-07-ruff-suppression-registry-extraction-plan.md)
```

Exact replacement for [TS-3.1]:

```markdown
### Approved Ruff Suppression Registry [TS-3.1]

This section governs approved local exceptions to [TS-3]. A plan may propose or
review a candidate, but it must not become the lasting source of truth for an
adopted exception. The approved exception records, global raw-diagnostic
inventory, and generated symbol index live in the standalone
[Ruff Suppression Registry](../ruff-suppression-registry.md). That operational
ledger is not normative and is not required reading.

_Implementation mapping_: approved local source directives across `weft/`,
`tests/`, `bin/`, `integrations/`, and `extensions/` point to the standalone
registry; `bin/ruff_suppression_index.py` reconciles those directives with the
human rows, raw Ruff findings, and generated symbol index; and
`tests/specs/test_ruff_policy.py` plus
`tests/specs/test_ruff_suppression_index.py` prove the live policy and
check/write failure boundaries.

Owner: the standalone registry owns each stable suppression group,
human-reviewed rationale, and approved cardinality. The local directive owns
rule codes and the stable group pointer. The generated index owns only derived
paths, qualified symbols, actual directive counts, and raw-diagnostic counts.
Boundary: only the rules, cardinality, invariant, and locations covered by the
approved group. Verification: the named real proof, `ruff check .`, and
`./.venv/bin/python bin/ruff_suppression_index.py --check`. Required action:
obtain explicit review before adding, regrouping, growing, or shrinking a
suppression; update the human row, cardinality, and source pointer together;
then regenerate only the delimited derived index with
`./.venv/bin/python bin/ruff_suppression_index.py --write`.

The approved local form is
`# noqa: <codes> approved [TS-3.1] [RUFF-SUP-NNN] exception`. The stable group
points to the single durable full reason; source comments do not duplicate it.
Group IDs are unique and match `RUFF-SUP-[0-9]{3}`. Every group has at least
one live source directive. Human rows contain `Group`, `Rules`, `Approved
cardinality`, `Protected invariant`, `Real proof`, `Rejected alternatives`,
and `Approval`.

The standalone registry also owns one lexically sorted
``Global raw-`noqa` inventory:`` line containing backticked `CODE=count`
entries for every diagnostic exposed by `--ignore-noqa`. The backticks around
`noqa` are part of the canonical parser grammar. This aggregate is a tripwire,
not a second identity registry.

The generated index is enclosed by unique begin/end markers. It renders one
deduplicated `path::qualified_symbol` site per group, sorted by group ID and
site. A symbol is the outermost enclosing function, qualified by class names,
or `<module>`; decorator lines belong to their function. Physical line remains
the internal identity for matching Ruff diagnostics, duplicate detection, and
error messages. Content outside the generated markers is human-owned and must
remain byte-for-byte unchanged during regeneration.

The repository tool must refuse to write if normal Ruff is unclean, a source
or registry marker is malformed, a group is unknown or empty, a rule or
cardinality differs, a raw diagnostic does not match its directive, the global
inventory differs, any raw `C901` diagnostic lacks a tagged approved directive
at the same `noqa_row`, or discovered Python source is unreadable or
syntactically invalid. Policy mismatches exit 1. Anticipated invocation,
decoding, Ruff, and atomic-replacement failures exit 2 with a one-line
diagnostic and no traceback. Both classes leave the standalone registry
byte-for-byte unchanged. Unexpected programming defects retain a traceback as
bug evidence.
```

## Objective

Move the complete approved Ruff suppression registry, global raw inventory,
and generated index out of required-reading Spec 08 into
`docs/ruff-suppression-registry.md`. Keep only the normative suppression policy
and a concise pointer in Spec 08. The standalone registry must not be added to
any required-reading list.

## Context and Key Files

- `docs/specifications/08-Testing_Strategy.md` remains the normative owner for
  [TS-3] and [TS-3.1].
- `docs/ruff-suppression-registry.md` becomes the optional operational ledger.
- `bin/ruff_suppression_index.py` owns check/write reconciliation and the
  canonical `--registry` target.
- `tests/specs/test_ruff_policy.py` proves policy ownership, exact live counts,
  parser defaults, and required-reading exclusion.
- `tests/specs/test_ruff_suppression_index.py` proves checker failure, write,
  byte-preservation, and deprecated-option behavior.

## Invariants

- All 234 human suppression rows, the global raw inventory, and the complete
  generated marker block remain byte-for-byte identical to their baseline
  content.
- Spec 08 retains every normative approval, grammar, refusal, exit-code, and
  byte-preservation rule.
- `--registry` defaults to `docs/ruff-suppression-registry.md`; `--spec` is only
  a deprecated spelling for the same input.
- No runtime code, approved suppression, raw Ruff finding, or suppression
  cardinality changes.
- The new ledger is absent from `AGENTS.md`, the machine-readable context
  index, and all required agent-context Markdown.

## Out of Scope

- Adding, removing, regrouping, or reapproving a Ruff suppression.
- Changing Ruff rule selection, CI order, runtime behavior, or public APIs.
- Repairing unrelated documentation-path claims or unrelated worktree edits.

## Implementation

1. Mechanically move the human registry and generated index without changing
   any group, cardinality, rationale, inventory, or generated row.
2. Retarget `bin/ruff_suppression_index.py` and policy tests from the testing
   spec to the standalone registry. Rename the checker input option and local
   terminology from spec to registry where it owns the file path. Make
   `--registry` canonical while retaining `--spec` as a deprecated compatibility
   spelling for existing repository automation.
3. Run the checker write/check path, Ruff, focused policy/tool tests, plan
   metadata, and a required-reading reference audit. Obtain a clean Python
   review focused on locality and comprehensibility before completion.

## Acceptance

- Spec 08 contains no suppression table, global raw inventory, or generated
  index.
- The standalone registry contains the exact live 234 groups and 377 source
  directives and reconciles without regeneration drift.
- Default checker commands target the standalone registry.
- No `AGENTS.md` or `docs/agent-context/` required-reading list names the
  standalone registry.

## Testing Plan and Gates

Run from the repository environment:

1. `./.venv/bin/python bin/ruff_suppression_index.py --write` followed by
   `--check`.
2. `./.venv/bin/python -m pytest -q -n 0 tests/specs/test_ruff_policy.py
   tests/specs/test_ruff_suppression_index.py tests/specs/test_plan_metadata.py`.
3. `./.venv/bin/ruff check .` and the repository formatter check from
   `AGENTS.md`.
4. `./.venv/bin/mypy bin/ruff_suppression_index.py --config-file
   pyproject.toml` and `git diff --check`.
5. Compare the baseline Spec 08 table header through the generated end marker
   with the new registry and assert no diff; report exact group/directive
   counts.
6. Search `AGENTS.md`, `docs/agent-context/context.index.yaml`, and
   `docs/agent-context/**/*.md` for the registry basename.

`bin/check-doc-paths` is informational for this slice because its eight current
failures are pre-existing examples and planned-directory claims; this change
must add no new failure.

## Independent Review Log

- Initial clean review: `NET NEGATIVE`. It found that normative TS-3.1 policy
  had moved into the non-normative ledger, the plan was misclassified, two
  checker terms were stale, and extraction tests were incomplete.
- Rework: restored all normative policy to Spec 08; classified the plan 5+P;
  added baseline, strategy B, exact delta, reciprocal backlink, registry
  terminology, deprecated-alias behavior, and stronger extraction tests.
- Clean re-review: implementation `NET POSITIVE`. It found only completion
  evidence gaps: this plan lacked the full Class-3 foundation, and the
  required-reading test omitted `context.index.yaml`. Both are corrected in
  this revision.
- Final closure review: `PASS`; both completion evidence gaps are closed.

## Fresh-Eyes Review

The clean re-review found the file boundary net positive for locality and
comprehensibility: required readers retain the complete governing policy while
the 234-row operational ledger becomes opt-in and directly linked. No source
refactor was queued for rework.

## Completion Evidence

- Checker write/check: passed with 234 groups and 377 directives.
- Focused policy, checker, and plan-metadata suite: 88 passed.
- Ruff, formatter, checker mypy, and `git diff --check`: passed.
- Baseline ledger records through the generated end marker: byte-for-byte
  identical after extraction.
- Required-reading audit: no registry basename in `AGENTS.md`,
  `context.index.yaml`, or `docs/agent-context/**/*.md`.
- `bin/check-doc-paths`: eight pre-existing unrelated claims remain; this slice
  adds none.

## Deviation Log

None. The implementation preserves the baseline policy and changes only the
ledger's storage ownership and checker path.
