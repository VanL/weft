# Agent-Theory Propagation Plan (2026-10-14 delta wave)

Status: completed
Source specs: none — process/guidance adoption from agent-theory @ `8f41a9d`; no governing product spec (weft carries its operating model in runbooks, `docs/agent-context/`, and `docs/coalescing.md`)
Superseded by: none

Class: 3+P. Per this repo's `+P` semantics
(`docs/agent-context/decision-hierarchy.md`, Task Classification `[DOM-15]`),
`+P` combines as `max(base, 5)` plus pre-landing review — so this wave is
treated as Class 5-equivalent process maintenance. Hardening: N/A —
documentation, guidance, skills, and three read-only gate scripts (`bin/`); no
runtime execution path, queue contract, persistence schema, or rollout
trigger fires.

## Goal

Land the `agent-theory` delta since Weft's last propagation pin (`e42762c`,
2026-07-28), covering both the August 2026 wave (`e42762c..bc684fe`: program
theory integration, `skills/crystallize-program-theory/SKILL.md`, coalescing
archive-phase rules, and `[DOM-15]` maintenance classification) and the October
2026 wave (`bc684fe..8f41a9d`: Tier-1 cross-family vs Tier-2 native-subagent
review hierarchy, empirical premise/falsifiability review checks, `fixed in
plan` body sync, `RECONSIDERATION REQUESTED` escalation, release stop-gates,
test-design rules 7–10 and failure patterns 11–12, static complexity (`C901`)
visibility-signal rules, and hardened `bin/` gates with `--self-test`).

## Source Pin

- Hub repository: `agent-theory` at commit `8f41a9d` (`8f41a9d` verified via
  `git -C /Users/van/Developer/agent-theory rev-parse --short HEAD`).
- Previous Weft propagation pin: `e42762c` (landed in Weft `4e69784a` via
  `docs/plans/2026-07-28-agent-guidance-propagation-plan.md`, plus the
  2026-08-06 program-theory adoption in Weft `265a37ff` and the 2026-10-14
  contention stop-condition slice in Weft `5ac21258`).
- Extraction rule: all hub payloads were read from the end-state at
  `git show 8f41a9d:<path>`, never from intermediate commits.

## Payload Checklist (grep-verifiable)

1. **`AGENTS.md`**:
   - Scoped subagent authorization to research, exploration, supervised
     implementation slices, or Tier-2 fallback reviews after recorded Tier-1
     unavailability (`skills/call-agent/SKILL.md` and
     `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`), plus
     delegated RED test transcript verification.
   - 8-item read order synchronized element-for-element with
     `docs/agent-context/context.index.yaml` and `docs/agent-context/README.md`,
     plus the `docs/program-theory.md` product-scope *judgment* paragraph and
     progressive-disclosure **Module theory** note.
   - `### Session-start coalescing check (read-only)` section referencing
     `docs/coalescing.md` and `bin/coalesce-check`.
   - Project Conventions and Handoff Protocol updated with Tier-1/Tier-2 review
     rules and Class ≥3 `docs/plans/README.md` status-index row creation and
     completion-flip gates.
2. **`docs/agent-context/context.index.yaml` & `docs/agent-context/README.md`**:
   - 8-item `read_order` aligned across `AGENTS.md`, `context.index.yaml`, and
     `docs/agent-context/README.md` (with `docs/agent-context/README.md` at #2
     and startup note on `docs/lessons.md`).
   - Declared-claim floor paragraph (`Read-order compliance is a declared-claim
     floor, not a gate...`), `designing-agent-facing-interfaces.md` in the
     Runbooks list, and coalescing threshold maintenance note.
3. **`docs/agent-context/decision-hierarchy.md`**:
   - `## Trusted Base for Normative Guidance` section before
     `## Classify Before the Preflight`.
   - `[DOM-15]` rules and fixture rows for Class-2 operational coalescing sweeps
     versus `Class 3+P (effective 5)` durable-guidance promotion sweeps, plus
     human-owner gate on durable-guidance promotions.
   - Program-theory preflight bullet (`docs/program-theory.md`) and three-bullet
     Completion Gate rules (`Status documents are claims, not evidence`, `Stale
     open markers are not proof that work is unshipped`, `Counts and
     completeness words must be verified by command`).
4. **`docs/agent-context/principles.md`**:
   - `**Report surprise; do not absorb it.**` under Collaboration Standards.
   - Negative lifecycle labels (`unused`, `dead`, `orphaned`, `stale`,
     `superseded`) full-tree grep requirement under Change Hygiene.
5. **`docs/agent-context/engineering-principles.md`** (preserving Weft's §1–§12
   numbering):
   - §3 explicit configuration/context threading and non-default wrapper test.
   - §4 module-owned clock/platform helper seams (`_monotonic`, `_sleep`,
     `_getpid`) instead of mutating shared stdlib modules (`time`, `os`, `sys`).
   - §4.1 release-lineage migration fixtures and independent wire literals
     (`f(x) == f(x)` tautology prevention), plus Rule 5 paragraph placement.
   - §4.2 full-repo search (`examples/`, scripts, templates, vendored/bootstrap
     consumers) before declaring anything unused/dead.
   - §4.3 repeated contention/timeout/retry-budget tuning as a theory-possession
     stop condition.
   - §5 5-item read order starting with program theory, and §6 program-theory
     traceability bullets.
   - §7.5 `Use Independent Review to Reduce Author Blindness` (Tier-1 vs Tier-2
     `same-family fallback (degraded)`, 3 empirical checks: `Causal premise in
     code`, `Falsifiable proof`, `Empirical identifiers by command`, `fixed in
     plan` body sync, `RECONSIDERATION REQUESTED`, verifier error).
   - §9 mechanical count/completeness verification, `bin/check-doc-paths`
     passing status + `--self-test`, and `**Gates do not gate gates.**`
   - §11 static complexity (`C901`) visibility-signal discipline, governed
     suppression registry via `docs/specifications/08-Testing_Strategy.md`
     `[TS-3.1]`, zero governance self-suppression, and real-config linter
     probes.
   - `## Warning Signs` expanded with contention padding, same-family subagent
     review, and complexity-driven file/function splitting.
6. **Runbooks (`docs/agent-context/runbooks/`)**:
   - `hardening-plans.md`: §1 standing invariants and refused-alternatives
     (`docs/program-theory.md` `[ALT-WT-*]`,
     `docs/specifications/07-System_Invariants.md`) plus **difference register**
     for simplification/consolidation/extraction; §13 product cleanup contract
     and liveness preconditions; §14 binding comprehension mechanics reference;
     §15 `Release Plans Carry Stop-Gates` (including stale-remote guard and
     failure-path verification).
   - `review-loops-and-agent-bootstrap.md`: Tier 1 / Tier 2 (`same-family
     fallback (degraded)`) / Tier 3 (`Review: unavailable — <reason>`) reviewer
     hierarchy, timeout calibration, `**Existence-check first.**`,
     `**Premise- and falsifiability-check second.**`, updated Planning and
     Scoped Change prompts, `fixed in plan` body sync, `RECONSIDERATION
     REQUESTED`, `**Guidance surfaces are reviewable and blockable.**`, and
     `## Audit-Response Protocol (large or systemic audits)`.
   - `testing-patterns.md`: Test Design Rules 7–10; Pattern 11 (`The Test
     Neutralizes the Hostile Default It Claims to Cover`); Pattern 12 (`Negative
     Timing Windows, Side-Effecting Polls, and Stdlib Clock Patches`); updated
     Verification Pattern (delegated RED transcript assertion check, P1
     single-line mutation proof, exact full CI roots).
   - `writing-plans.md`: Planning Standard existence/count/falsified-premise
     rules; §3 comprehension-question binding teeth; §4c exact paragraph/clause
     scope and removed-clause hazard audit; §8 Reviewer family / invocation tier
     metadata and `fixed in plan` body sync; §10 performative overengineering;
     Plan Lifecycle status-token purity, `Reviewed baseline:` + `## Revision
     Log`, in-place demotion of superseded decisions, Class ≥3 Status Index
     closeout gate, `tests/` grep before backlink retirement, git-backed
     archive deletion, and retained-ref reachability for `<source_sha>`.
7. **Skills (`skills/`) & `docs/coalescing.md`**:
   - `skills/call-agent/SKILL.md`: Tier-2 native-subagent prohibition without two
     logged Tier-1 failures; causal-premise and falsifiable-test stance; brief
     element `(7) standing constraints and refused alternatives in force`
     (`RECONSIDERATION REQUESTED`).
   - `skills/coalescing/SKILL.md`: synced to `agent-theory` @ `8f41a9d`
     (Class-2 archive sweep vs `Class 3+P` durable-guidance promotion gate,
     unindexed-plans derivation, one-sentence session-start report rules,
     3-tier distillation verification, archive phase, version-qualified tool
     facts, full eligible plan audit, `tests/` backlink grep, noncontiguous
     lesson fold records, and `[REV-AT-004]` discipline-generalization rule).
   - `skills/debugging/SKILL.md`: step 5 boundary-straddling and P1 mutation
     proof; step 6 contention stop-condition (`[WT-7]`, `[ALT-WT-017]`,
     `[TS-0]`).
   - `skills/brainstorming-to-plan/SKILL.md`: step 1 four-part admission test
     for rejected alternatives.
   - `skills/crystallize-program-theory/SKILL.md`: new skill adopted and
     adapted to Weft's `docs/program-theory.md` (`[WT-*]` / `[ALT-WT-*]`) and
     `docs/specifications/`.
   - `docs/coalescing.md`: archive-phase classification note, `Unindexed`
     threshold row, session-start report conditions, `## Reporting Cues
     (non-gating)` (`Apparatus share`), `### Noncontiguous Lesson Fold Records`
     for `1c997978`, and 2026-10-14 propagation run-log entry.
8. **Executable gates (`bin/`)**:
   - `bin/check-doc-paths`: CommonMark `_prose_lines()` fence stripping,
     `DECLARED_FUTURE_ARTIFACTS` stale/unused fail-closed checks, and
     `--self-test` (9 probes against Weft's `docs/specifications/` layout).
   - `bin/check-dom15-fixtures`: CommonMark fence stripping, clause-aware
     `NEGATIVE_FACT_PATTERNS` (including Weft's local negative-fact phrasing)
     and `TRIGGER_FIRE_RE`, and expanded `--self-test` (11 mutations + 7 fence
     probes + invocation-error probe).
   - `bin/coalesce-check`: word-bounded `FOREIGN_MARKER_RE`,
     `resolve_remote_ref()`, `retired_plan_cues()`, and loud shallow-clone skip,
     preserving Weft's `LESSON_RE` (`^## 20[0-9]{2}-`) and governed `# noqa:
     C901 approved [TS-3.1] [RUFF-SUP-224] exception` on `main()`.

## Weft Adaptations and Invariants Preserved

- **Specification and guidance paths**: Weft uses `docs/specifications/` (no
  `docs/specs/` or `docs/implementation/03-agent-inventory.md`). Every
  backticked path in scanned guidance surfaces resolves under
  `bin/check-doc-paths`.
- **Section numbering**: `docs/agent-context/engineering-principles.md`
  preserves Weft's §1–§12 numbering (adding §7.5 for Independent Review) and
  `docs/agent-context/runbooks/testing-patterns.md` preserves Weft's Patterns
  1–10 (adding Patterns 11 and 12).
- **Ruff and Suppression Registry contract**: `bin/check-doc-paths`,
  `bin/check-dom15-fixtures`, and `bin/coalesce-check` conform to
  `tests/specs/test_ruff_policy.py` and `docs/ruff-suppression-registry.md`
  (`RUFF-SUP-224` on `bin/coalesce-check:main`, `RUFF-SUP-357` on
  `bin/check-dom15-fixtures`, and McCabe complexity `<= 10` on all other
  functions).

## Dirty-Tree Invariant

This wave was prepared alongside 17 pre-existing dirty WIP files from the
in-flight supervisor/monitor worktree. None of these 17 files were touched or
reverted:

- `docs/plans/2026-10-13-supervisor-terminal-projection.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `tests/commands/test_task_evidence.py`
- `tests/core/monitor/test_lifetime_report.py`
- `tests/core/test_monitor_collation.py`
- `tests/core/test_monitor_external_log.py`
- `tests/core/test_monitor_store.py`
- `tests/tasks/test_task_monitor.py`
- `weft/core/monitor/collation.py`
- `weft/core/monitor/external_log.py`
- `weft/core/monitor/lifetime_report.py`
- `weft/core/monitor/sql.py`
- `weft/core/monitor/store.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/task_evidence.py`

## Deviation Log

| Source ref | Planned behavior | Actual behavior | Rationale | Follow-up |
|------------|------------------|-----------------|-----------|-----------|
| `agent-theory` `bin/check-dom15-fixtures` & `bin/coalesce-check` | Direct copy of hub scripts | Extracted helper functions (`_fence_marker`, `_closes_fence`, `_record_row` in `bin/check-dom15-fixtures`; `_retired_table_lines`, `_parse_retired_row` in `bin/coalesce-check`) while keeping identical behavior and self-tests | Weft enforces `MCCABE_MAX_COMPLEXITY = 10` and exact 1-to-1 suppression matching with `docs/ruff-suppression-registry.md` across `bin/` scripts via `tests/specs/test_ruff_policy.py` | None — behavior and `--self-test` probes match hub 100% |

## Review Findings and Dispositions

- **Author fresh-eyes review**: Completed across all 21 touched/created files.
  Verified that no foreign `docs/specs/...` or `docs/plans/...` paths appear in
  backticks inside scanned guidance surfaces, all `bin/` scripts pass Ruff,
  `bin/check-doc-paths --self-test` and `bin/check-dom15-fixtures --self-test`
  pass, `bin/coalesce-check` passes live execution, and
  `tests/specs/test_plan_metadata.py` and `tests/specs/test_spec_hygiene.py`
  pass.
- **External review (`codex` Tier-1 CLI scoped adaptation review)**:
  - **F1 (`docs/agent-context/engineering-principles.md:401`, Blocking):**
    Clarify that `zero self-suppressions` on governance tools means a tool may
    never exempt or suppress its own policy check on itself, while approved
    `[TS-3.1]` lint exceptions (`RUFF-SUP-224`, `RUFF-SUP-357`) remain governed
    by the suppression-index gate. **Disposition:** Fixed in tree.
  - **F2 (`docs/coalescing.md:126`, Blocking):** Move the `2026-10-14`
    propagation `Run Log` row out of the wave commit into the separate step-7
    pin commit while keeping the preamble and `### Noncontiguous Lesson Fold
    Records` in the wave commit. **Disposition:** Fixed in tree.
  - **F3 (`docs/plans/2026-10-14-agent-theory-delta-wave-plan.md:216`,
    Non-blocking):** Distinguish `check-doc-paths --self-test` and
    `check-dom15-fixtures --self-test` from `coalesce-check` live execution.
    **Disposition:** Fixed in plan and run-log text.
  - **F4 (`docs/plans/2026-10-14-agent-theory-delta-wave-plan.md:214,237`,
    Non-blocking):** Update wave file count from `19` to `21` (19 modified
    tracked files + 2 new files). **Disposition:** Fixed in plan.

## Verification and Gates

```bash
python3 bin/check-doc-paths
python3 bin/check-doc-paths --self-test
python3 bin/check-dom15-fixtures --self-test
python3 bin/check-dom15-fixtures
python3 bin/coalesce-check
./.venv/bin/ruff check bin/check-doc-paths bin/check-dom15-fixtures bin/coalesce-check
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
```

## Landing Notes

Stage only the 21 files belonging to this propagation wave (explicit file-list
staging) so the 17 pre-existing monitor/supervisor WIP files in the working tree
remain untouched.
