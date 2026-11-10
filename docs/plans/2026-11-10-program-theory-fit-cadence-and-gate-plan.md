# Program Theory `[REV-WT-007]`: Forward-Only Mechanical `[WT-7]` Plan Gate and `[ALT-WT-004]` Reconciliation Plan

Status: completed
Source specs: `docs/program-theory.md` ([WT-0], [WT-2a.6], [ALT-WT-004], [ALT-WT-018], [WT-7], [WT-9], [REV-WT-006], [REV-WT-007]); `docs/agent-context/decision-hierarchy.md` ([DOM-15]); `docs/specifications/08-Testing_Strategy.md` ([TS-1])
Superseded by: none

Class: 5+P (`program-theory revision` per `docs/program-theory.md` header + `+P` verification-process update across `tests/specs/test_plan_metadata.py`, `docs/agent-context/runbooks/writing-plans.md`, and `skills/crystallize-program-theory/SKILL.md`).

## 1. Goal

Implement Owner Decision #2 (`2026-11-10`: *"Option 2A, but only going forward"*): revise `docs/program-theory.md` (`[REV-WT-007]`) so ordinary effective class-5 plans (`Class: 5`, `Class: 5+P`, and any `Class: N+P` per `[DOM-15]`) dated `>= 2026-11-10` carry the author's five-part `[WT-7]` `Theory Fit` account audited in the mandatory cross-family review pass and mechanically gated at `Status: completed` by `tests/specs/test_plan_metadata.py` (with zero retroactive gating or backfilling of `< 2026-11-10` plans), while preserving the blind two-factor fit test and `[WT-0]` theory-versus-code audit-table refresh at `[REV-WT-*]` theory revisions and releases; simultaneously reconcile `[ALT-WT-004]`'s `TaskMonitor` deadline wording with its inherited `ServiceTask` owner-row refresh and idle-exit deadlines.

## 2. Source Documents

- `docs/program-theory.md` (`[WT-0]`, `[WT-2a.6]`, `[ALT-WT-004]`, `[ALT-WT-018]`, `[WT-7]`, `[WT-9]`, `[REV-WT-006]`)
- `docs/agent-context/decision-hierarchy.md` (`[DOM-15]` — `+P` process-changing modifier gives `Class N+P` effective requirements `max(N, 5)`)
- `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (baseline `[WT-0]` audit table)
- `docs/plans/2026-11-05-program-theory-behaviour-and-refusal-revision-plan.md` (`[REV-WT-005]` / `[REV-WT-006]` exemplar and audit-table migration)
- `docs/agent-context/runbooks/writing-plans.md`, `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`, `skills/crystallize-program-theory/SKILL.md`
- `tests/specs/test_plan_metadata.py`

## 3. Context, Pressure & Key Files

- **Empirical pressure from the 2026-11-10 deep-dive review (`dbbf5526`)**:
  1. Several Class-5 plans completed between `2026-11-07` and `2026-11-09` performed genuine `[ALT-WT-*]` checks and cross-family reviews (and often wrote the 5-part author fit account) while closing with `"Other-family blind account: not yet run"` because `tests/specs/test_plan_metadata.py` had no mechanical completion check and running a second separate cross-family CLI invocation plus a 46-row audit table copy on every routine Class-5 bug/spec slice duplicated the mandatory review pass.
  2. Conversely, the 2026-11-10 review of the pre-split remediation plan proved why `[WT-7]` fit verification matters: an un-audited fit section miscited `[WT-1]`, `[WT-2a.5]`, `[WT-2a.9]`, and `[WT-2a.11]` by ID without checking their text.
  3. `[ALT-WT-004]` (`docs/program-theory.md:607`) still says *"TaskMonitor's only clock is its cycle deadline through `next_wait_timeout()`"*, whereas `ServiceTask` (`weft/core/tasks/service.py` and `05-Message_Flow_and_State.md:831`) gives `TaskMonitor` two additional local monotonic deadlines through `next_wait_timeout()` (owner-row refresh and idle-exit), still with zero heartbeat message registration.
- **Owner Decision #2 (`2026-11-10`)**:
  - *"Maybe just have a doc checker on the plans that ensure it is present and filled in?"* -> *"Option 2A, but only going forward."*
  - Explicit trade-off stated in `[WT-0]`, `[WT-7]`, and `[REV-WT-007]`: single-pass cross-family review of the author's `[WT-7]` account does **not** reproduce blind derivation (because the reviewer sees the plan and author's account), so `[REV-WT-007]` names it **"Theory fit check"** for ordinary effective class-5 plans (`Class: 5` or `Class: N+P`) dated `>= 2026-11-10` while retaining the **blind two-factor fit test** (fresh-context other-family account from `docs/program-theory.md` + boundary diff alone) and `[WT-0]` audit-table refresh at every `[REV-WT-*]` theory revision (including **this** plan!) and at every release.

## 4. Invariants and Constraints

- **Hardening**: `N/A — no risky trigger` (theory/runbook authoring and plan-corpus metadata validation; zero runtime execution-path or persistence-lifecycle changes).
- **Strictly forward-only (`date_prefix >= "2026-11-10"`)**: Zero edits to and zero test failures on historical plans dated `< 2026-11-10`.
- **Effective Class-5 coverage (`[DOM-15]`)**: Per `docs/agent-context/decision-hierarchy.md` (`[DOM-15]`), `+P` elevates `Class N+P` (`3+P`, `4+P`, `5+P`) to effective class `max(N, 5) == 5`. Both the theory text and `tests/specs/test_plan_metadata.py` apply the gate to every plan whose declared `Class:` is `5` or carries `+P` (`Class: 5`, `Class: 5+P`, `Class: 3+P`, `Class: 4+P`, etc.).
- **Completion-verdict specificity**: `tests/specs/test_plan_metadata.py` must require an explicit `Final Completion Review Verdict: APPROVE` marker (with reviewer family/invocation metadata and no subsequent `REVISE` / `BLOCKED` verdict line after it) when an effective class-5 plan dated `>= 2026-11-10` has `Status: completed`, so a pre-implementation plan `APPROVE` cannot satisfy the post-implementation completion gate!
- **Active `[REV-WT-006]` compliance for this plan**: Because this plan itself revises `docs/program-theory.md`, it executes the full blind two-factor `[WT-7]` fit test (§8.2) AND carries the `[WT-0]` audit-table refresh (§6).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- **Baseline Commit / Tag**: `dbbf5526` (`v0.9.124` on `main`, `2026-11-10`)
- **Plan Type**: `theory-authoring with mechanical verification gate`
- **Promotion Strategy**: **Strategy B (atomic single commit)** across `docs/program-theory.md`, `docs/agent-context/runbooks/writing-plans.md`, `docs/agent-context/context.index.yaml`, `skills/crystallize-program-theory/SKILL.md`, and `tests/specs/test_plan_metadata.py`.
- **Promotion Baseline Identifier**: `dbbf5526 + Plan 4 worktree (2026-11-10)`

## Proposed Theory Delta

| File | Strategy | Sections touched |
|------|----------|------------------|
| `docs/program-theory.md` | B — atomic | Header `Verification` & `Governing process` (`[WT-0]`); `[ALT-WT-004]` Current consequence; `[WT-7]` two-tier fit check & blind two-factor fit test; `[WT-9]` `[REV-WT-007]` |

### 1. `docs/program-theory.md` Header `Verification` (`lines 13-27`)

- **Exact pre-change target**: Starts with `record; endorsement is not a demonstration of possession. Two-factor fit` and ends with `document-versus-tree agreement, not possession.`

Replacement (`docs/program-theory.md`):
```markdown
record; endorsement is not a demonstration of possession. Theory fit check and
two-factor fit test: for ordinary effective class-5 plans (`Class: 5` or any
`Class: N+P` per `[DOM-15]`) dated `>= 2026-11-10`
(forward-only; earlier plans are not retroactively gated), the author writes
the five-part [WT-7] `Theory Fit` account (`Verdict`, `Rests on`, `Refuses`,
`Predicts`, `Fits worst`), the mandatory cross-family reviewer audits that
account against this document's cited entries and the completed diff in the
same review pass, and `tests/specs/test_plan_metadata.py` rejects `completed`
status unless all five parts are non-placeholder and an explicit
`Final Completion Review Verdict: APPROVE` (with no trailing `REVISE` or
`BLOCKED`) is recorded. At each `[REV-WT-*]` revision to this document, and once per release
across that release's changes, the blind two-factor fit test runs: an agent of
another family writes a second [WT-7] account from this document and the
boundary diff alone without seeing the author's, and their divergence is
classified as document deficiency, change deficiency, or agent reasoning, with
this document changed only for a document deficiency. Transfer log: a review proposal of a [WT-6] alternative that does not
cite the entry's recorded reopening condition is logged against that entry
(see [WT-7]). Theory-versus-code audit table: at each `[REV-WT-*]` theory
revision or release, the table that maps each [WT-2a] invariant and [WT-4]
principle to its contract and its evidence in the tree as aligned, drifted
(naming the plan that closes it), or theory revision; unchanged rows carry
forward by reference and touched claims are re-audited. The table measures
document-versus-tree agreement, not possession.
```

### 2. `docs/program-theory.md` Header `Governing process` (`lines 36-41`)

- **Exact pre-change target**: Starts with `Governing process: Theory revisions are class-5 plans with one cross-family` and ends with `operating-model specification for the same purpose.`

Replacement (`docs/program-theory.md`):
```markdown
Governing process: Theory revisions are class-5 plans with one cross-family
review round (a second only after a block or a scope change), the blind
two-factor fit test and the theory-versus-code audit refresh at each
`[REV-WT-*]` theory revision or release (with the cross-family audited [WT-7]
`Theory Fit` check mechanically gated on effective class-5 plans dated
`>= 2026-11-10`, forward-only), and the Replacement-sync gate in
`bin/check-doc-paths` for promoted plans. SimpleBroker uses `[DOM-16]` in its
operating-model specification for the same purpose.
```

### 3. `docs/program-theory.md` `[ALT-WT-004]` Current Consequence (`lines 607-612`)

- **Exact pre-change target**: Starts with `- Current consequence: TaskMonitor's only clock is its cycle deadline` and ends with ``  deletes that queue (this supersedes the `da5fa0f6` gate).``

Replacement (`docs/program-theory.md`):
```markdown
- Current consequence: TaskMonitor keeps its own monotonic deadlines through
  `next_wait_timeout()` (its cycle deadline plus the inherited ServiceTask
  owner-row refresh and idle-exit deadlines) and registers no heartbeat
  message. The heartbeat
  service is a standing internal service of the canonical Manager ([MA-1] item 7)
  that idles with no timer when nothing is due; it is the only reader and
  deleter of `weft.state.heartbeats`, and the Manager neither reads nor
  deletes that queue (this supersedes the `da5fa0f6` gate).
```

### 4. `docs/program-theory.md` `[WT-7]` Fit Check & Blind Two-Factor Test (`lines 1032-1054`)

- **Exact pre-change target**: Starts with `- **Two-factor fit test:** At each class-5 change, and once per release` and ends with `  touched claims.`

Replacement (`docs/program-theory.md`):
```markdown
- **Theory fit check and blind two-factor fit test:** All fit accounts use one
  five-part structure: a verdict (fits, does not fit, or fits with strain); the
  sections the change rests on; what it refuses and the entry that refuses it;
  what it predicts that could prove it wrong; and the sentence that fits worst.
  An account that names no refusal, no falsifiable prediction, and no tension is
  a failed account; it is recorded as failed and supports no conclusion.
  For ordinary effective class-5 plans (`Class: 5` or any `Class: N+P` per
  `[DOM-15]`) dated `>= 2026-11-10` (forward-only; earlier plans
  are never retroactively gated or backfilled), the author writes that five-part
  `Theory Fit` account in the plan, the mandatory cross-family plan/code
  reviewer audits it against this document's cited entries and the completed
  diff in the same review pass (catching ID-only miscitations without claiming
  blind derivation), and `tests/specs/test_plan_metadata.py` enforces
  non-placeholder completion plus an explicit
  `Final Completion Review Verdict: APPROVE` (with no subsequent `REVISE` or
  `BLOCKED`) when the plan's status is `completed`.
  At each `[REV-WT-*]` revision to this document, and once per release across
  that release's changes, the blind two-factor fit test runs over one fixed
  change boundary and one theory version: the author writes the first account,
  and an agent of another family writes the second in a fresh context whose only
  inputs are this document and the diff of that boundary, excluding plan files,
  review logs, and the author's account. Where two valid accounts agree, the
  theory transfers what the author intended. Where they diverge, the author
  proposes a classification for each divergence: a document deficiency, a change
  deficiency, or agent reasoning; this document changes only for a document
  deficiency. A divergence the author attributes to the other agent's reasoning
  is recorded as contested, not resolved. Both accounts and the classifications
  go in the plan's review log; the owner reads the comparison and any contested
  item when they choose. Refresh the `[WT-0]` **theory-versus-code audit table**
  at each `[REV-WT-*]` theory revision and at each release, carrying unchanged
  rows forward by reference and re-auditing touched claims.
```

### 5. `docs/program-theory.md` `[WT-9]` `[REV-WT-007]` Append (`lines 1324-1332`)

- **Exact pre-change target**: Starts with `- Evidence:` (inside `### [REV-WT-006]`) and ends with `## Sources`

Replacement (`docs/program-theory.md`):
```markdown
- Evidence:
  - contemporaneous: `docs/plans/2026-11-05-program-theory-behaviour-and-refusal-revision-plan.md`,
    Review Log (fit test trials 1 and 2, and the revision rows)
  - owner-recalled: owner session 2026-11-05 ("Perhaps it is a two-factor
    test: One from the author and one from an other-family model, where they
    each describe how the change does or does not fit with the theory";
    "Yes, we adopt it, and the fix")

### [REV-WT-007] Forward-only mechanical [WT-7] plan gate and two-tier fit cadence

- Current account: Effective class-5 plans (`Class: 5` or any `Class: N+P` per
  `[DOM-15]`) dated `>= 2026-11-10` (forward-only; plans dated `< 2026-11-10`
  are exempt) carry the author's five-part `[WT-7]` `Theory Fit` account audited
  by the mandatory cross-family reviewer against the cited theory text and
  completed diff in the same review pass, and `tests/specs/test_plan_metadata.py`
  mechanically blocks `Status: completed` unless all five parts are
  non-placeholder and an explicit `Final Completion Review Verdict: APPROVE`
  (with no trailing `REVISE` or `BLOCKED`) is recorded. Like
  `bin/check-doc-paths` in `Governing process`, this plan-corpus completeness
  check enforces standing `[WT-0]` record integrity (detecting omitted fit
  accounts at completion, while semantic correctness remains the cross-family
  reviewer's job) rather than pinning dependency versions, CI step order, or
  release-script internals (`[ALT-WT-018]`). Because an audited single pass sees
  the author's framing and checks citation/scope fidelity rather than blind
  transfer, the blind two-factor fit test (fresh-context other-family account
  from this document and the boundary diff alone) and the `[WT-0]`
  theory-versus-code audit table refresh remain mandatory at each `[REV-WT-*]`
  revision to `docs/program-theory.md` and once per release. `[ALT-WT-004]` also
  states accurately that `TaskMonitor` keeps its own monotonic deadlines through
  `next_wait_timeout()` (its cycle deadline plus its inherited `ServiceTask`
  owner-row refresh and idle-exit deadlines) while registering no heartbeat
  message.
- Supersedes: Requiring a separate blind second-factor CLI invocation and full
  audit-table refresh on every routine class-5 plan with no mechanical
  plan-completion check, and `[ALT-WT-004]`'s wording that `TaskMonitor`'s cycle
  deadline was its only `next_wait_timeout()` deadline.
- Pressure: The 2026-11-10 deep-dive review at `dbbf5526` (`v0.9.124`) found
  both that Nov 7–9 class-5 plans left `"Other-family blind account: not yet
  run"` at completion because nothing checked completion mechanically, and that
  an unaudited fit section can cite `[WT-*]` IDs whose text says something else.
- Evidence:
  - contemporaneous: `docs/plans/2026-11-10-program-theory-fit-cadence-and-gate-plan.md`
  - owner-recalled: owner session 2026-11-10 ("Maybe just have a doc checker on
    the plans that ensure it is present and filled in?"; "Option 2A, but only
    going forward")

## Sources
```

## 5. Tasks

1. **Task 1.1: Add Forward-Only Mechanical `[WT-7]` & Final-Completion-Verdict Gate to `tests/specs/test_plan_metadata.py`**
   - **Outcome**: For any plan file in `docs/plans/` whose filename starts with `YYYY-MM-DD- >= "2026-11-10"`, declares effective Class 5 per `[DOM-15]` (`Class: 5`, `Class: 5+P`, or any `+P` plan such as `Class: 3+P` / `Class: 4+P`), and has `Status: completed`:
     1. Require all five `[WT-7]` `Theory Fit` parts (`Verdict`, `Rests on`, `Refuses`, `Predicts`, `Fits worst`) to be present with non-empty, non-placeholder prose (rejecting `TBD`, `TODO`, `not yet run`, `pending`).
     2. Require an explicit `Final Completion Review Verdict: APPROVE` line in the review section accompanied by reviewer metadata (`Reviewer family`), with zero `REVISE` / `BLOCKED` verdict lines appearing after it.
     3. Plans dated `< "2026-11-10"`, plans with effective class `< 5` (plain `Class: 3` or `Class: 4` without `+P`), and plans in `draft` / `promoted` / `withdrawn` status are unaffected.
   - **Files to touch**: `tests/specs/test_plan_metadata.py`.
   - **RED -> GREEN unit tests**: Add synthetic `tmp_path` helper tests in `tests/specs/test_plan_metadata.py` proving: (a) a `< 2026-11-10` completed Class-5 plan without `Final Completion Review Verdict: APPROVE` passes; (b) a `>= 2026-11-10` completed `Class: 5` or `Class: 3+P` plan missing any of the 5 `[WT-7]` fields, containing `"not yet run"`, missing reviewer metadata, having only a pre-implementation plan `APPROVE`, or having `REVISE` / `BLOCKED` after `Final Completion Review Verdict: APPROVE` fails; (c) a `>= 2026-11-10` completed `Class: 5` and `Class: 3+P` plan with all 5 fields + reviewer metadata + `Final Completion Review Verdict: APPROVE` passes.

2. **Task 1.2: Apply `[REV-WT-007]` & `[ALT-WT-004]` Edits and Synchronize Runbook / Skill Guidance**
   - **Outcome**: Apply all five Replacement blocks in `docs/program-theory.md`; update `docs/agent-context/runbooks/writing-plans.md` (§8 Independent Review Loop & Plan Lifecycle) to document the five-part `[WT-7]` `Theory Fit` check + `Final Completion Review Verdict: APPROVE` marker on effective class-5 plans (`Class: 5` or `Class: N+P`) dated `>= 2026-11-10` and `updated_at: 2026-11-10` in `docs/agent-context/context.index.yaml`; update `skills/crystallize-program-theory/SKILL.md` (`lines 289-297`) so the `[WT-0]` audit-table refresh trigger matches `[REV-WT-007]` (`at each [REV-WT-*] theory revision and at each release`).
   - **Files to touch**: `docs/program-theory.md`, `docs/agent-context/runbooks/writing-plans.md`, `docs/agent-context/context.index.yaml`, `skills/crystallize-program-theory/SKILL.md`.
   - **Done when**: `pytest tests/specs/ -q`, `bin/check-doc-paths`, `bin/check-dom15-fixtures`, and `bin/coalesce-check` all pass.

## 6. `[WT-0]` Theory-Versus-Code Audit Table Refresh (`dbbf5526`)

Unchanged structural and behavioural rows carry forward by reference from `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` and `docs/plans/2026-11-05-program-theory-behaviour-and-refusal-revision-plan.md` §6. Re-audited claims from the 2026-11-10 deep-dive probes at `dbbf5526`:

| Claim | Contract | Evidence at `dbbf5526` | Verdict | Closing plan |
|-------|----------|------------------------|---------|--------------|
| `[WT-2a.2]` Queues are the only durable truth; `MonitorStore` is private; custom queues externally owned | `[OBS.13.1]`, `[OBS.13.9]`, `[IP-1]` | Under `WEFT_TASK_MONITOR_MODE=report_only` (or `--family task-local`), `retention.py::_task_local_candidates` archives/deletes external messages in shared custom outboxes; `09-Implementation_Plan.md` & `weft_django/README.md` falsely claim `terminal_snapshot` reads `MonitorStore`. | drifted (code in `retention.py`; prose in `09` & `weft_django/README.md`) | `docs/plans/2026-11-10-deep-dive-reachable-bug-remediation-plan.md` (code); `docs/plans/2026-11-10-extension-typing-and-spec-sync-plan.md` (prose) |
| `[WT-2a.4]` Configuration is frozen at creation | `[AR-2.1]`, `[AR-6]`, `[PL-2.7]` | `CodexProvider` drops `--sandbox` and `-C` on `turn_index > 0` and shares `resume --last`; `_validate_stage_template` silently ignores `spec.parameterization` / `spec.run_input`. | drifted (code) | `docs/plans/2026-11-10-agent-pipeline-and-extension-bug-remediation-plan.md` |
| `[WT-2a.9]` One current contract | `[MA-1.6a]`, `[AR-5]` | Aligned at `dbbf5526`; preserved by replacing `force_kill_pids` outright with `force_kill_processes` (Plan 1) and updating `AgentRuntimeAdapter`, `LLMBackend`, and `ProviderCLIBackend` together for `spec_context` (Plan 2). | aligned | `docs/plans/2026-11-10-deep-dive-reachable-bug-remediation-plan.md`; `docs/plans/2026-11-10-agent-pipeline-and-extension-bug-remediation-plan.md` |
| `[WT-2a.13]` & `[WT-4.3]` Death needs proof; availability and death differ | `[OBS.12a]`, `[LIVENESS.R3]`, `[LIVENESS.R5]` | `_stop_resolved_tasks` increments accepted count on `report_unknown`; `_runtime_handle_force_kill_pids` and `_kill_duplicate_service_pid` allow `SIGKILL` without verified `(pid, create_time)`. | drifted (code) | `docs/plans/2026-11-10-deep-dive-reachable-bug-remediation-plan.md` |
| `[WT-2a.14]` Submission never blocks | `[MA-1.4]`, `[MA-3]`, `[MF-5]` | Empirical probe (`drain_trial.sh`, 3/3 trials): `weft run` submitted `0.2s` after `weft manager stop` hung `90s` while `STOP` finished in `0.6s` (`_begin_shutdown_drain` left `status="active"`, and pre-spawn waiters never re-checked `weft.state.services`). | drifted (measured) | `docs/plans/2026-11-10-deep-dive-reachable-bug-remediation-plan.md` |
| `[WT-4.2]` One owner for each resource | `[CC-3.1]`, `[CC-3.2]` | `terminate_process_tree` calls `psutil.wait_procs` on `Popen` roots (`ECHILD -> returncode=0`), mislabeling interactive `Consumer` cleanup as `completed(0)`. | drifted (measured) | `docs/plans/2026-11-10-deep-dive-reachable-bug-remediation-plan.md` |
| `[ALT-WT-004]` `TaskMonitor` local deadlines via `next_wait_timeout()` | `[MA-3]`, `[MF-6]` | Code is aligned (registers no heartbeat message), but `[ALT-WT-004]` text omitted inherited `ServiceTask` owner-row refresh and idle-exit deadlines. | theory revision (this plan) | this plan |
| `[WT-0]` & `[WT-7]` Fit-check cadence and mechanical plan gate | `[WT-0]`, `[WT-7]`, `[REV-WT-007]` | Nov 7–9 Class-5 plans closed with `"Other-family blind account: not yet run"`; unaudited fit citations drifted from theory text. | theory revision (this plan) | this plan |

## 7. Verification and Gates

```bash
./.venv/bin/python -m pytest tests/specs/ -q
./.venv/bin/ruff check tests/specs/test_plan_metadata.py
./bin/mypy-check
./.venv/bin/python bin/check-doc-paths --self-test && ./.venv/bin/python bin/check-doc-paths
./.venv/bin/python bin/check-dom15-fixtures
./.venv/bin/python bin/coalesce-check
```

## 8. Independent Review Loop

### 8.1 Author's `[WT-7]` Theory Fit Account

- **Verdict**: Fits with strain (resolved explicitly as `[REV-WT-007]`).
- **Rests on**:
  - `[WT-0]` (*Repeating this document from memory is not possession; endorsement is not a demonstration of possession*): enforces non-placeholder five-part `[WT-7]` field presence and completion-approval recording mechanically via `tests/specs/test_plan_metadata.py` across all effective class-5 plans (`Class: 5` and `Class: N+P` per `[DOM-15]`), while semantic citation auditing remains the mandatory cross-family reviewer's responsibility and blind two-factor transfer testing remains at `[REV-WT-*]` theory revisions and releases.
  - `[WT-2a.6]` & `[ALT-WT-004]` (heartbeat service vs local monotonic reactor deadlines): aligns `[ALT-WT-004]`'s Current consequence with `ServiceTask.next_wait_timeout()`'s owner-row refresh and idle-exit deadlines without registering any heartbeat message.
- **Refuses**:
  - Retroactively rewriting or backfilling historical `< 2026-11-10` plans to fabricate post-hoc fit accounts (refused by `writing-plans.md` plan-immutability rules and Owner Decision #2 forward-only scope).
  - Dropping the blind two-factor fit test completely: `[REV-WT-007]` retains blind fresh-context derivation at every `[REV-WT-*]` theory revision and release rather than pretending a non-blind review pass measures blind transfer.
- **Predicts**:
  - Any effective class-5 plan (`Class: 5`, `Class: 5+P`, or `Class: 3+P`) dated `>= 2026-11-10` flipped to `Status: completed` with `"not yet run"`, a missing `[WT-7]` bullet, missing reviewer metadata, or only a pre-implementation `APPROVE` fails `pytest tests/specs/test_plan_metadata.py`; all 67 pre-`2026-11-10` plans continue passing `test_plan_metadata.py` untouched.
- **Fits worst**:
  - Two explicit tensions: (1) in `[REV-WT-006]`, moving ordinary effective class-5 plans from per-plan blind two-factor derivation to a cross-family audited single pass creates an interval between releases where shared author-reviewer framing escapes fresh-context blind challenge; and (2) `[ALT-WT-018]` warns that *"The test encodes process, not correctness"* — reconciled in `[REV-WT-007]` because `tests/specs/test_plan_metadata.py` (like `bin/check-doc-paths` in `Governing process`) checks standing `[WT-0]` plan-record completeness rather than pinning dependency versions, CI step order, or release-script internals, while semantic accuracy remains audited by the cross-family reviewer.

### 8.2 Blind Other-Family `[WT-7]` Fit Account & Divergence Classification

- **Reviewer family & isolated invocation (`2026-11-10`)**: OpenAI Codex CLI (`codex exec --skip-git-repo-check -s read-only -C /tmp/weft-wt7-blind-6cb1b97f/p4 -c 'model_reasoning_effort="high"'`, thread `01a1c763-b875-78a2-9761-f9984be149d0`), given **only** `program-theory.md` and `boundary.diff`.
- **Blind account summary**:
  - *Verdict*: `fits with strain` (valid 5-part account).
  - *Rests on*: `[WT-0]` (process must improve orientation, error detection, or human understanding), `[WT-7]` (five-part account, independent derivation at theory revisions and releases, divergence classification), `[WT-9]` (explicit supersession via `[REV-WT-007]`), and `[WT-2a.6]` / `[ALT-WT-004]` (multiple explicit monotonic deadlines sharing one arbiter without heartbeat messages).
  - *Refuses*: Treating an account without refusal, falsifiable prediction, or tension as evidence of fit (`[WT-7]`), using heartbeat messages as `TaskMonitor`'s clock (`[ALT-WT-004]`), and confusing an informed citation audit with blind derivation (`[WT-0]`).
  - *Predicts*: Completed class-5 fixtures dated `>= 2026-11-10` fail validation when required fields or reviewer metadata are missing or when `APPROVE` is followed by `REVISE`/`BLOCKED`, while `< 2026-11-10` fixtures remain exempt; irrelevant `[WT-*]` IDs are challenged by the cross-family reviewer; theory revisions and releases carry blind accounts and refreshed audit tables; `TaskMonitor` honors cycle, owner-refresh, and idle-exit deadlines without heartbeat registration.
  - *Fits worst*: `[ALT-WT-018]` (*"The test encodes process, not correctness"*): `test_plan_metadata.py` enforces document fields, reviewer metadata, and approval ordering, and the initial `[REV-WT-007]` draft did not explicitly reconcile that standing plan-completeness check with `[ALT-WT-018]`; separately noted the interval between releases where blind checks are deferred.
- **Divergence classification**:
  - **Document deficiency resolved in `[REV-WT-007]` (§4c.5)**: Added an explicit sentence in `[REV-WT-007]` reconciling `tests/specs/test_plan_metadata.py` (alongside `bin/check-doc-paths`) with `[ALT-WT-018]` — `test_plan_metadata.py` enforces standing `[WT-0]` plan-record completeness (catching omitted fit accounts at completion while semantic audit belongs to the cross-family reviewer) and does not pin dependency versions, CI step order, or release-script internals.

### 8.3 Cross-Family Plan Review Log

- **Post-split Round 1 (`2026-11-10`)**:
  - **Reviewer family & invocation**: OpenAI Codex CLI (`codex exec -s read-only -C /Users/van/Developer/weft -c 'model_reasoning_effort="high"'`, thread `01a1c763-c263-7b31-96a5-17058e503b35`).
  - **Verdict**: `BLOCKED` (`F7` affecting Plan 4).
  - **Disposition (verified against `docs/agent-context/decision-hierarchy.md:76` `[DOM-15]` and applied in place)**:
    - `F7` (`[P2]`, Task 1.1 and `[REV-WT-007]` keyed on literal `Class: 5` / `5+P` rather than effective Class 5 including `Class: 3+P` and `Class: 4+P` per `[DOM-15]`): **Accept (`fixed in plan`)** — updated §1, §2, §4, §4c Replacement blocks 1, 2, 4, and 5 (`docs/program-theory.md`), and Tasks 1.1 & 1.2 to cover all effective class-5 plans (`Class: 5` or any `Class: N+P`), including explicit synthetic `Class: 3+P` RED/GREEN unit tests in `tests/specs/test_plan_metadata.py`.
- **Post-split Round 2 Scoped Verification (`2026-11-10`)**:
  - **Reviewer family & invocation**: OpenAI Codex CLI (`codex exec -C /Users/van/Developer/weft resume -c 'sandbox_mode="read-only"' -c 'model_reasoning_effort="high"' 01a1c763-c263-7b31-96a5-17058e503b35`).
  - **Verdict**: `PASS` for Plan 4 (`F7` effective Class-5 `[DOM-15]` coverage, `[ALT-WT-018]` reconciliation in `[REV-WT-007]`, and §8.1/§8.2 `[WT-7]` fit accounts verified aligned with `docs/program-theory.md`; suite-wide `Verdict: PASS` confirmed in Round 3).

### 8.4 Post-Implementation Completion Review

- **Reviewer family**: OpenAI Codex CLI (`codex exec -s read-only -C /Users/van/Developer/weft -c 'model_reasoning_effort="high"'`, thread `01a1c7b1-c8b4-72e2-97dd-9582816527c6`)
- **Round 1 (`2026-11-10`)**: Returned `REVISE` with 6 parser/account findings (`#1`–`#5` `[P2]`, `#6` `[P3]`), all reproduced RED and fixed:
  - `#1` (quoted `"not yet run"` inside Plan 4 §8.1 explanatory prose triggered placeholder failure): **Accept (`fixed`)** — strip quoted/backtick spans before unquoted placeholder search while still rejecting bare quoted/unquoted placeholder values (`"not yet run"`, `pending`, `TBD`, `TODO`); verified on Plan 4's own markdown in `test_wt7_completion_gate_hardened_parsing_edge_cases`.
  - `#2` (colon-inside-bold `- **Verdict:**` empty fields, numbered `1. pending`, and backtick field labels): **Accept (`fixed`)** — normalized `[*_`]*{label}[*_`]*:[*_`]*` and stripped list prefixes + inline markup before checking emptiness/placeholders.
  - `#3` (backtick `Class: \`3+P\`` and multi-line escalated `Class: 3` -> `Class: 5+P` bypassed `_is_effective_class5`): **Accept (`fixed`)** — scan all `Class:` declarations, strip inline markup, and fail closed on unrecognized `Class:` syntax.
  - `#4` (`Reviewer family` not bound to completion-review subsection; empty `- **Reviewer family:**` passed): **Accept (`fixed`)** — require normalized non-placeholder `Reviewer family` inside `_enclosing_subsection(prose_lines, last_approve_idx)`.
  - `#5` (narrative mention of `Final Completion Review Verdict: APPROVE`, same-line `/ Verdict: REVISE`, wrapped two-line `Verdict:\n REVISE`, and later H2 `## Pre-landing Review` `REVISE` bypassed check): **Accept (`fixed`)** — require standalone `Final Completion Review Verdict: APPROVE` line and scan all subsequent document prose lines for both single-line and two-line wrapped negative verdicts.
  - `#6` (§8.1 `Rests on` overstated mechanical enforcement of citation auditing): **Accept (`fixed`)** — clarified in §8.1 that `test_plan_metadata.py` enforces field presence and completion-approval recording while semantic citation auditing remains the cross-family reviewer's responsibility.
- **Rounds 2–4 (`2026-11-10`)**: Scoped follow-up passes on thread `01a1c7b1-c8b4-72e2-97dd-9582816527c6` caught and verified fixes (`RED -> GREEN` in `test_wt7_completion_gate_hardened_parsing_edge_cases`) for empty/malformed `Class:` fail-closed validation, bold-formatted wrapped two-line negative verdicts (`- **Verdict:**\n  REVISE`), narrative inline-code mentions (`\`Verdict: FAIL\``) outside review sections, punctuated declaration labels (`- Round 2 (completion) Verdict: REVISE`, `- Round 2.1 Verdict: BLOCKED`), inline-markup-plus-comma `Class:` declarations (`Class: **5+P**, ...`), and unbulleted `**Class:** 5+P` whitespace-required list-marker parsing.
- **Round 5 (`2026-11-10`)**: Scoped verification on thread `01a1c7b1-c8b4-72e2-97dd-9582816527c6` verified all parser edge cases, Ruff, and `pytest tests/specs/test_plan_metadata.py` clean with zero open findings.
- Final Completion Review Verdict: APPROVE

## 9. Out of Scope

- Runtime code or product specification bug fixes (covered by Plans 1–3).
- Retroactive edits to plans dated `< 2026-11-10`.

## 10. Fresh-Eyes Review

- Completed post-split author fresh-eyes pass and Round-1/2 disposition audit (`2026-11-10`): verified effective Class-5 (`Class: 5` + `Class: N+P`) semantics against `[DOM-15]`, reconciled `[REV-WT-007]` explicitly with `[ALT-WT-018]`, verified all five `docs/program-theory.md` `Replacement (...)` spans byte-for-byte against `dbbf5526`, and synchronized §6's `[WT-0]` audit table with Plans 1–3.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
|------|----------|---------|-------------------|---------------------|
| 2026-11-10 | `dbbf5526` (post-split R1–R2) | §1, §2, §4, §4c, Tasks 1.1 & 1.2, §6, §8 | Resolved Codex Round-1 `F7` (`[DOM-15]` effective Class-5 `+P` coverage), reconciled `[REV-WT-007]` with `[ALT-WT-018]`, and recorded blind `[WT-7]` fit account (`01a1c763-b875-78a2-9761-f9984be149d0`). | Completed (`Verdict: PASS` on `01a1c763-c263-7b31-96a5-17058e503b35`) |
