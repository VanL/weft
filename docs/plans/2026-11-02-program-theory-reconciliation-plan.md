# Program Theory Reconciliation: Hub Propagation, Four-Way Balance, and Adoption

Status: completed
Source specs: None — theory and guidance revision. Reads docs/specifications/07-System_Invariants.md [IMMUT.1]–[IMMUT.4], [STATE.1]–[STATE.2], [QUEUE.1]–[QUEUE.6], [EXEC.1], [OBS.2], [OBS.4], [OBS.13], [IMPL.1], [IMPL.5], [IMPL.6], [IMPL.8], [IMPL.10], [IMPL.11] as the contract counterpart of the identity invariants it records; changes none of them.
Superseded by: none

Class: 5+P. Revises `docs/program-theory.md` (a class-5 change by that
document's own Required action line) and materially changes
`skills/crystallize-program-theory/SKILL.md` and
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` (+P).
Hardening: N/A — documentation, theory, and guidance only; no runtime
execution path, queue contract, persisted format, or rollout trigger fires.
Plan type: spec-authoring / theory revision (promotion strategy D — no code
cites new behavior).

This is plan 1 of the 2026-11 cleanup and reconciliation series. Plans 2–12
(reactor edges, control-reply routing, family-scheduler
rebuilds, TID display encoding, and the rest) are not yet written. They are
referenced below by series number only; each will be its own dated plan with
its own index row. Nothing in those plans may start until this plan's Slice
C audit table names the drift row that justifies it.

## 1. Goal

Bring `docs/program-theory.md` from "Draft, first version, not yet adopted"
to an adopted account that satisfies the four-way balance recorded in the
agent-theory hub: general enough that the implementation changes in plans
2–12 remain measurable against it, specific enough to decide what belongs,
carrying the identity invariants of the product (which, in the hub's
scheme at `e6ac579`, include the whole-shape choices that shape the entire
program), and carrying two-part negative knowledge (what Weft is not; paths
tried and discarded). The deliverable is one theory, one audit table that classifies
every theory claim against the current specs and code, and the adoption
status change. The theory does not bend to the code: where the code lags
the ideal, the audit row says "drifted" and names the cleanup plan that
closes it.

Three kinds of theory-versus-code mismatch exist, and this plan treats them
differently:

1. **The ideal is right and the code lags.** Not a theory edit. The audit
   row says `drifted` and names the plan. The theory sentence becomes that
   plan's acceptance test.
2. **The sentence is at the wrong altitude.** Lift it to the invariant it
   was standing in for; the number, path, or helper name moves to the spec
   or the implementation note. Record it in the Deviation Log as an
   altitude correction, not a theory change.
3. **An unresolved identity question.** Only these are owner forks. They
   are listed in §9 and are decided by the owner, not by this plan.

Module-theory policy for Weft is outside this plan: the hub's module-theory
clause and module-divergence falsifier propagate as hub text, and whether Weft adopts or
refuses module theories is the owner's call, made separately if and when it
is needed.

## 2. Source Documents

Hub source (read from the landed commit, never from an uncommitted tree):

- `../agent-theory/docs/specs/02-agent-theory-and-program-theory.md`
  [AT-REF-2] "The four-way balance of a written program theory" and
  [AT-REF-3] surfaces table plus the directional triad.
- `../agent-theory/docs/program-theory.md` [AT-THEORY-0] "How theory,
  specs, and plans relate" (four rules), [AT-THEORY-2.1] module-theory
  clause, [AT-THEORY-4] items 6 and 9, [AT-THEORY-6] falsifiers "Altitude
  collapse" and "Module theory used to excuse a divergent subsystem" and the
  possession-check Habit line, and [REV-AT-006].
- `../agent-theory/docs/specs/02-agent-theory-and-program-theory.md` also
  "Extending the model with module theory" and the "Three directional rules"
  with their Note.
- `../agent-theory/skills/crystallize-program-theory/SKILL.md` spine
  questions Q6 (two-part negative knowledge) and Q8 (identity invariants),
  §3 extension shape (module theory by human decision only), §7 confirm step
  and the theory-versus-code audit table.
- `../agent-theory/docs/specs/01-development-documentation-operating-model.md`
  [DOM-2] module-theory paragraph, [DOM-3] directional framing, and [DOM-11]
  one-scoped-round rule (which now names `bin/check-doc-paths` for promoted
  `Replacement:` blocks);
  `../agent-theory/docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
  §4a Round-2 variant;
  `../agent-theory/docs/agent-context/runbooks/writing-plans.md` §4c
  Replacement-sync paragraph; `../agent-theory/AGENTS.md` read-order item 1
  and the Module theory paragraph.
- `../agent-theory/bin/check-doc-paths` `_plan_replacement_sync_failures`
  with `REPLACEMENT_LABEL_RE`, `HEADING_PATH_RE`, `SKIPPED_PLAN_STATUSES`,
  `_load_plan_index_statuses`, and its self-test probes.
- The hub plan that lands these:
  `../agent-theory/docs/plans/2026-11-01-program-theory-four-part-balance-plan.md`.

Source pin: hub commit `e6ac579` ("Update agent theory to better define
roles") lands the hub plan above. The hub's last propagation into Weft was
`296a7cd` (`docs/plans/2026-10-14-agent-theory-delta-wave-plan.md`); the
delta to propagate is `296a7cd..e6ac579` restricted to the files listed in
Slice A. All hub text in this plan is read at `e6ac579`
(`git -C ../agent-theory show e6ac579:<path>`).

Weft sources:

- `docs/program-theory.md` — the whole file. Read [WT-0] through [WT-9]
  before editing anything.
- `docs/specifications/07-System_Invariants.md` — the contract invariants
  cited in the metadata block; the identity-invariant section points at
  these, never restates them.
- `docs/specifications/00-Overview_and_Architecture.md`, Product Boundary.
- The 2026-11-01 deep-dive synthesis and its seven sweeps (conceptual unity,
  reactor timing, Python craft, surfaces and layering, and the others). They
  are session scratch, not repository files. Their findings enter this plan
  only through the audit table rows in Slice C, where each row must cite a
  spec code, a `[WT-*]` or `[ALT-WT-*]` identifier, or a repository path
  that resolves today. A finding that cannot be re-derived from the tree is
  dropped, not carried on the sweep's authority.
- Prior possession probes: `docs/plans/2026-10-13-supervisor-terminal-projection.md`
  (line "Program-theory possession probe [WT-7]") and
  `docs/plans/2026-10-14-task-monitor-read-separation-and-simplification-plan.md`
  ("Theory possession probe [WT-7]"). Two runs in total since the probe was
  defined. The 2026-10-13 record says the probe was "independently
  answered"; the 2026-10-14 record attributes the placement, the refusal,
  and the bug prediction to "the reviewer". Neither record shows a
  preregistered key, who wrote the expected answers, or what the answers
  were graded against. That evidence gap is why Slice C rebuilds the probe
  with an owner-written key.

Guidance the implementer must follow:

- `docs/agent-context/decision-hierarchy.md` ([DOM-15] classification and
  the Required Preflight).
- `docs/agent-context/runbooks/writing-plans.md` §4b–§4d (strategy D).
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` and
  `skills/call-agent/SKILL.md` for the single cross-family review round.
- `skills/crystallize-program-theory/SKILL.md` — the Weft copy is the
  August 2026 version and lacks the spine questions and §7 confirm step.
  Slice A stages the hub version and it lands at task 13 with the rest of
  the propagated text; until then Slices B and C work from this plan's
  §4c outline and the hub sections cited above.

## 3. Context and Key Files

Files to modify:

- `docs/program-theory.md`
- `skills/crystallize-program-theory/SKILL.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/agent-context/runbooks/writing-plans.md` (§4c: the hub's
  Replacement-sync paragraph)
- `bin/check-doc-paths` (port of the hub's `_plan_replacement_sync_failures`
  check and its self-test probes from `e6ac579`, as the lint-clean variant in
  §4a; conditional on §9 fork 3)
- `AGENTS.md` (the read-order item 1 clause, the **Module theory**
  paragraph, and the §3 "NEVER break" list). `CLAUDE.md` is a symlink to
  `AGENTS.md`, so it follows the one edit and is not edited separately.
- `docs/agent-context/README.md` (read-order item 1: the same clause swap
  as `AGENTS.md`, nothing else)
- `docs/agent-context/context.index.yaml` (`updated_at` bump only; it has
  no skill entries and no per-entry timestamps, and the runbook inventory
  does not change)
- `docs/plans/README.md` (this plan's row; the file count line; the
  `## Index` heading renamed to `## Status Index`, which the ported gate
  reads; under fork 3 option (a), the `promoted` status in the taxonomy)
- `tests/specs/test_plan_metadata.py` (the heading rename: line 61 asserts
  `"## Index" in readme_text`; under fork 3 option (a), `promoted` added
  to `ALLOWED_STATUSES`; see task 6a)
- `CHANGELOG.md` (one entry)

Files to read first:

- `docs/program-theory.md` [WT-0] (surfaces table and the owner's
  2026-10-09 directional rule), [WT-2] (six-item mental model), [WT-3]
  (ownership table), [WT-4] (four principle tables with Evidence and
  Contract columns), [WT-5], [WT-6] ([ALT-WT-001]–[ALT-WT-021]), [WT-7]
  (falsifiers, including the possession probe), [WT-8], [WT-9]
  ([REV-WT-001]–[REV-WT-003]).
- `docs/specifications/07-System_Invariants.md` Immutability, State
  Machine, Queue, Execution, and Implementation invariants.

Current structure the implementer must understand:

- Weft's [WT-0] already carries the owner's directional rule in the
  owner's words: "The specs describe what we have. The theory describes
  what we are building toward. We are not trying to reify bad decisions."
  The hub's triad is a generalization of that sentence. Slice A drafts the
  hub's four [AT-THEORY-0] rules (at `e6ac579`) to follow it, and task 13
  applies them; they do not replace the owner's words, which stay first.
- Weft's theory has no identity-invariant section. Identity is scattered
  across `CLAUDE.md` §3 ("NEVER break"), [WT-2] items 1–6, [WT-4] rows, and
  spec 07. Slice B creates the section and points the others at it.
- Weft's [WT-4] rows mix whole-shape choices (one reactor; queues as the
  only durable truth; no compatibility code) with judgment principles (the
  expert Python developer test; no over-armoring). The hub's negative
  knowledge is now two-part negative knowledge (what Weft is not; paths
  tried and discarded); whole-shape choices are not a third part but belong with
  the identity invariants ("the choices that shape the entire program"), so
  the [WT-4] rows that are whole-shape choices are reconciled against
  [WT-2a] (task 8).
- Weft's [WT-6] entries each state a mechanism and a reopening condition,
  but do not say whether the alternative was tried and discarded or refused
  on boundary grounds. The hub's taxonomy needs that tag.
- Weft has no `writing-specs.md` runbook; the hub's change to that file
  has no Weft counterpart and is not propagated.
- Weft's `[WT-0]` Verification line names "owner review and one
  independent semantic review" plus a possession probe "one time for each
  release or each class-5 plan". Possession in Naur's sense is held by
  people, not documents; a document can be tested only for whether it
  transfers the three abilities (explain how the program maps onto the
  world, explain why each part is as it is, modify it coherently). The
  rewritten line in §4c separates what measures the document (the audit
  table) from what measures transfer (an owner-keyed probe and logged
  re-proposals) and from owner endorsement (sentence-level ratification,
  which is endorsement, not a demonstration of possession).

Comprehension questions (answer in the execution log before the first edit;
a wrong answer is a stop-and-re-read blocker):

1. Under the owner's 2026-10-09 rule, when the spec sanctions a pull-scan
   of `T{tid}.ctrl_out` for STOP acks and the theory says "push, never
   pull-scan", which document changes and through what mechanism?
   *Expected: the spec, through a planned change (a cleanup-series plan);
   the theory sentence stays and becomes that plan's acceptance test.*
2. Which [WT-7] entry already exists for "an agent proposes a timer or a
   lane to fix a local symptom", and why must Slice B not duplicate it
   when adding the altitude-collapse falsifier?
   *Expected: "Blunt local mechanism vs. classical CS model"; altitude
   collapse is about the theory text's altitude, not about proposals, so
   it is a distinct falsifier and the two cross-reference rather than
   merge.*
3. What does the hub's module-divergence check say to do before a Weft
   subsystem gets a module theory?
   *Expected: check whether the subsystem has invented competing concepts
   and should be simplified back under the product theory before any
   module theory is written; a module theory is a human owner decision,
   only for a weakly coupled module with module-only theory-level
   invariants. Whether Weft ever adopts a module theory is the owner's call
   and outside this plan.*

## 4. Invariants and Constraints

Must not change:

- No sentence in `docs/specifications/` changes in this plan. Drift between
  a spec and the theory is recorded in the audit table and closed by a
  later plan, never patched here.
- No `[ALT-WT-*]` entry is removed or weakened. Tagging an entry as "tried
  and discarded" or "boundary refusal" adds a classification; it does not
  change the refusal or its reopening condition.
- The owner's quoted words in [WT-0], [WT-2], [WT-4], and [WT-5] keep their
  original wording.
- Existing identifiers `[WT-0]`–`[WT-9]`, `[ALT-WT-001]`–`[ALT-WT-021]`,
  `[REV-WT-001]`–`[REV-WT-003]` keep their meaning. New material gets new
  identifiers; nothing is renumbered.
- Theory text names contracts by spec code, plans by date and name, and
  owners by module name in the [WT-3] owner column. It does not name file
  paths under `weft/`, private attribute names, or measured millisecond
  figures. Where such a reference exists today it is lifted in Slice B
  (mismatch kind 2) and the concrete fact moves to the spec's
  implementation mapping or stays in the code. The rule covers evidence
  lines as well as claims: an Evidence cell or an `[ALT-WT-*]` mechanism
  paragraph cites spec codes, dated plan names, and module names, never a
  file path, a line number, or a private attribute. Task 10 therefore also
  covers [WT-0], [ALT-WT-002], [ALT-WT-004], [ALT-WT-008], [ALT-WT-021],
  and [REV-WT-001], which the first drafting pass found carrying paths,
  private names, or millisecond figures and left for this rule to decide.
  Commit hashes and `docs/` paths to dated plans or lessons are allowed in
  Evidence lines: they are durable pointers of the same kind as dated plan
  names, and they pass `bin/check-doc-paths`. The `docs/` paths in the
  Evidence lines of [ALT-WT-017], [REV-WT-002], [REV-WT-003], and the staged
  [REV-WT-004] stub are therefore not altitude failures and need no block.
  The clause extends to `docs/specifications/` file paths (the canonical
  name of a spec file, used beside its section code) and to sibling-repo
  theory paths such as `../simplebroker/docs/program-theory.md`, which the
  theory's Sources section already uses; so the spec paths in [ALT-WT-017]
  and [REV-WT-002] and the stub's `../agent-theory/` paths are not altitude
  failures either (the former altitude row 20, which lifted that form in
  [REV-WT-001], was removed as unnecessary work after round 5). What the clause excludes is
  unchanged: `weft/` source paths, line numbers, private attributes, and
  measured millisecond figures.
- Hub text propagated in Slice A is taken from the landed hub commit with
  path adaptation only (hub docs/specs/ becomes Weft
  `docs/specifications/`; hub `[AT-*]` codes become the corresponding Weft
  `[WT-*]` codes). Rewording the hub's sentences is out of scope for this
  plan; if the implementer believes a hub sentence is wrong for Weft, the
  disagreement goes to §9 as a fork, not into the file.
- The one-scoped-round review rule propagated in Slice A governs this
  plan's own review: one cross-family round on the plan and the proposed
  theory delta together; a second round only after `BLOCKED` or a scope
  change.

Review gates for this slice:

- No new execution path, no runtime code change, no dependency change.
  The `bin/check-doc-paths` port (task 6a) and the one-line
  `tests/specs/test_plan_metadata.py` heading edit are repository tooling,
  not runtime code.
- No spec drift: after every slice, the baseline diff
  `git diff --stat ec50bd57 -- docs/specifications/` is empty.
  `tests/specs/test_spec_hygiene.py` and `bin/check-doc-paths` must also
  pass, but they check status wording and path claims, not that the specs
  are unchanged.
- Altitude presence check (after task 10 and after task 13; see those
  tasks): `grep -nE 'weft/|self\._|[0-9]+ ms' docs/program-theory.md`. This
  is a presence check, not proof of altitude. `bin/check-doc-paths` cannot
  enforce the altitude rule: it scans only `docs/agent-context/`,
  `docs/specifications/`, `skills/`, `AGENTS.md`, and `docs/coalescing.md`
  (`SCAN_DIRS` and `SCAN_FILES` in `bin/check-doc-paths`), so it excludes
  `docs/program-theory.md` and `CLAUDE.md`.
- Every count in this plan and in the audit table comes from a command
  output recorded in the execution log.
- Self-driven fresh-eyes review after the draft, recorded in §8.
- External review: required, one round, different agent family, on the
  plan plus the Slice B theory delta before Slice B is applied.
- Owner ratification: required for the identity-invariant list, the
  task 8 reconciliation of the twelve whole-shape [WT-4] rows against
  [WT-2a], and the §9 forks. The plan is
  not `completed` until the owner has ratified those three and the status
  line in `docs/program-theory.md` reads adopted.

## 4a. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [WT-0] | [WT-0] friction paragraph as at baseline | Lifted per the §4 altitude rule (altitude row 10) | Evidence lines cite spec codes, dated plans, module names, and commits, never `weft/` paths, private names, or measured figures | n/a — theory altitude |
| [ALT-WT-002] | [ALT-WT-002] Candidate as at baseline | Lifted per the §4 altitude rule (altitude row 11) | Evidence lines cite spec codes, dated plans, module names, and commits, never `weft/` paths, private names, or measured figures | n/a — theory altitude |
| [ALT-WT-002] | [ALT-WT-002] Current consequence as at baseline | Lifted per the §4 altitude rule (altitude row 12) | Evidence lines cite spec codes, dated plans, module names, and commits, never `weft/` paths, private names, or measured figures | n/a — theory altitude |
| [ALT-WT-004] | [ALT-WT-004] Current consequence as at baseline | Lifted per the §4 altitude rule (altitude row 13) | Evidence lines cite spec codes, dated plans, module names, and commits, never `weft/` paths, private names, or measured figures | n/a — theory altitude |
| [ALT-WT-008] | [ALT-WT-008] Current consequence as at baseline | Lifted per the §4 altitude rule (altitude row 14) | Evidence lines cite spec codes, dated plans, module names, and commits, never `weft/` paths, private names, or measured figures | n/a — theory altitude |
| [ALT-WT-021] | [ALT-WT-021] Candidate as at baseline | Lifted per the §4 altitude rule (altitude row 15) | Evidence lines cite spec codes, dated plans, module names, and commits, never `weft/` paths, private names, or measured figures | n/a — theory altitude |
| [ALT-WT-021] | [ALT-WT-021] Reason as at baseline | Lifted per the §4 altitude rule (altitude row 16) | Evidence lines cite spec codes, dated plans, module names, and commits, never `weft/` paths, private names, or measured figures | n/a — theory altitude |
| [ALT-WT-021] | [ALT-WT-021] Evidence as at baseline | Lifted per the §4 altitude rule (altitude row 17) | Evidence lines cite spec codes, dated plans, module names, and commits, never `weft/` paths, private names, or measured figures | n/a — theory altitude |
| [REV-WT-001] | [REV-WT-001] Current account as at baseline | Lifted per the §4 altitude rule (altitude row 18) | Evidence lines cite spec codes, dated plans, module names, and commits, never `weft/` paths, private names, or measured figures | n/a — theory altitude |
| [REV-WT-001] | [REV-WT-001] Supersedes as at baseline | Lifted per the §4 altitude rule (altitude row 19) | Evidence lines cite spec codes, dated plans, module names, and commits, never `weft/` paths, private names, or measured figures | n/a — theory altitude |
| `bin/check-doc-paths` (task 6a) | The hub's `_plan_replacement_sync_failures` check and probes ported as verbatim hub code from `e6ac579` | The behavior-identical, lint-clean version, split into helpers that reuse Weft's `_fence_marker` and `_closes_fence` | The verbatim code fails Weft's mandatory ruff C901 (complexity 26 > 10) and one mypy `[index]` error | n/a — tooling |
| `skills/crystallize-program-theory/SKILL.md` (task 2) | Whole-file replacement with the hub skill, path adaptation only | Hub structure with Weft-specific sections retained: the four-part `[ALT-WT-*]` admission test, the gate-run step, the coalescing and `RECONSIDERATION REQUESTED` When To Use bullets, and the Weft Output Standard | They are Weft process content with no hub conflict | n/a |
| [WT-2a.6] | One reactor per task | One arbiter per reactor, composable | Taut's reactor over workspace reactors shows the invariant holds per owner and nests | n/a |
| [WT-2a.13] | The endorsed broad sentence: "Manager drain, exit, or supersession changes who takes future work and never kills or moves running tasks" | Implicit lifecycle changes (supersession, idle exit, crash) keep the never-kills clause; a system STOP is the Manager's responsibility to stop, suspend, or kill active tasks, then internal services, then itself (owner wording, 2026-11-02) | [MA-3]'s explicit-stop drain (spec 03 near line 801) and `tests/core/test_manager.py::test_manager_stop_command_drains_nonpersistent_children` (round 6 F19) | Owner decides (§9 item 5) |
| [ALT-WT-021] | Evidence unchanged | Commit provenance added: `540707a3` stays as the measurement baseline the PR #48 repair plan names; `4c490043` (introduced the polling adapter) and `d4381877` (replaced it) are added | `540707a3` is the recorded baseline snapshot but does not itself introduce or remove the adapter; the two added commits carry that provenance (round 5 observation) | n/a |

(Altitude corrections under mismatch kind 2 are logged here with the
`[WT-*]` identifier in the first column and `n/a — theory altitude` in the
last.)

## 4b. Spec Baseline

- Weft: `ec50bd57` — `docs/program-theory.md` and
  `docs/specifications/07-System_Invariants.md` at plan authoring time.
- Hub: `296a7cd` is the last propagated pin; the hub pin for Slice A is
  `e6ac579` (recorded in §2).

Promotion baseline identifier: `ec50bd57` plus the §3 file list applied in the working tree on 2026-11-02 (tasks 13 to 17); land by committing that file list and this plan as one change, and the landing commit is the one that carries this line at `Status: completed`.

(The line reads `pending` until task 13 lands; task 13 replaces it with the
commit that lands the theory text. `bin/check-doc-paths` reads any other
value as promoted.)

## 4c. Proposed Theory Delta

Promotion strategy D for every file. The exact text is drafted in Slice B
and attached to this plan under `## Proposed Theory Delta (drafted)` before
the review round; the review target is that drafted text, not this
outline. The outline below fixes scope so the draft cannot widen.

`docs/program-theory.md`:

1. Header: `Status:` changes to adopted wording at the end of Slice C;
   `Governing process:` names this plan's review-and-ratify path. The
   `Verification:` line is replaced by four parts, each naming what it
   measures:
   - *Owner endorsement:* the owner ratifies each sentence of [WT-2a],
     [WT-4], [WT-5], and [WT-6] as their own, once at adoption and again
     whenever a `[REV-WT-*]` record is added. Sentence-level ratification
     is endorsement; it does not demonstrate possession.
   - *Transfer (probe):* at each release or Class-5 completion (the hub's
     [AT-THEORY-6] possession-check trigger), a possession probe with an
     owner-written answer key (the hypothetical feature, the category
     error, and the predicted bug class are fixed before the probe runs),
     executed by an agent that did not author the plan, graded against
     the key. The result is recorded in the plan. On FAIL, the failure is
     classified after disposition as a document deficiency, a key fault,
     or agent reasoning, and a document deficiency (the sentence the
     document lacked) is recorded only when the evidence supports that
     diagnosis. A self-graded probe is not a probe.
   - *Transfer (natural experiment):* any review or plan in which an agent
     proposes an alternative recorded in [WT-6] without citing that
     entry's recorded reopening condition is logged as a transfer failure
     against that entry, per the [WT-7] falsifier "Repeated rejected
     proposals as a transfer failure". A proposal that cites a reopening
     condition that has fired is a legitimate reopening under [WT-6], not
     a transfer failure. No ritual, no schedule; the log is the review's
     disposition row.
   - *Measurability (audit table):* at each release or Class-5
     completion, alongside the probe, the theory-versus-code audit table
     from Slice C is refreshed: unchanged rows are carried forward by
     reference, and the claims and boundaries the release or plan touched
     are re-audited ([AT-REF-2], hub `e6ac579`). The table measures
     document-versus-tree agreement, not possession, and is labelled as
     such.
2. [WT-0]: the owner's 2026-10-09 quotation stays first. After it, add the
   hub's four [AT-THEORY-0] rules at `e6ac579`, with path adaptation and a
   `[REV-WT-004]` pointer:
   1. if the spec or the code is pulling away from the theory, the
      presumption is that the spec or code is not properly moored in the
      theory; if the code and the spec disagree, the presumption is that
      the code has a bug; when a spec clause itself is stale, or experience
      building and operating the code suggests a better path, a plan fixes
      the spec itself;
   2. never mistake a current spec or plan for "what has been decided" and
      justify it when it falls short of the theory; everything, the theory
      included (`[REV-WT-*]`), can be revised;
   3. respecting active contracts during an edit is about change order, not
      permanence: do not silently break an active spec clause mid-edit; use
      a plan and its spec-promotion slice to move the spec and code toward
      the theory;
   4. process exists to keep theory and specs aligned while working at
      agent speed; a process rule that does not improve orientation, error
      detection, or human understanding is cut.
   Beside the rules, the hub spec 02 clause that specs describe what is in
   the code now, and that a spec leads the code only across an active
   plan's spec-promotion slice or when a bug violates the contract. The
   surfaces table's Program theory and Plans rows take the hub's revised
   wording (spec 02 surfaces table at `e6ac579`).
3. New section `## Identity invariants [WT-2a]` between [WT-2] and [WT-3]:
   the invariants that would still measure a replacement implementation.
   Candidate list for Slice B to confirm against the tree, each with its
   contract counterpart:
   - one invertible task identity: every unit of work has a TID that is a
     SimpleBroker hybrid timestamp, and every rendering of it is lossless
     ([IMMUT.3]; the display encoding is plan 12's subject);
   - queues are the only durable truth; process memory holds hints; a
     task's private working state serves only that task
     ([QUEUE.1]–[QUEUE.6], [OBS.1], [OBS.2]; [ALT-WT-020]);
   - forward-only lifecycle ([STATE.1], [STATE.2]);
   - `spec` and `io` frozen after creation ([IMMUT.1], [IMMUT.2]);
   - spawn isolation for every Weft runtime process ([IMPL.5], [IMPL.6]);
   - one reactor, one drive-owning thread per task, with TaskMonitor's
     maintenance worker as the single named exception ([IMPL.8],
     [IMPL.10], [IMPL.11]);
   - SimpleBroker owns queue mechanics; Weft never reimplements them
     ([WT-5], [SB-0.1], [SB-0.4]);
   - single operator trust domain; the OS is the boundary ([WT-5]);
   - no compatibility code for changed contracts ([ALT-WT-012]);
   - Weft is a Unix citizen: argv not shell, stdin is data, exit codes are
     the status API ([CLI-1.1.1]; `CLAUDE.md` §4.11).
   `CLAUDE.md` §3 and `AGENTS.md` §3 point at [WT-2a] instead of
   restating; their reserved-queue-policy and process-title lines are
   contract rules, not identity rules, and point at spec 07 instead.
4. [WT-4]: no `Kind` column is added. The twelve rows the Slice B draft
   marked whole-shape are reconciled against [WT-2a] (task 8): each either
   becomes (or already is) an identity invariant in [WT-2a], and its [WT-4]
   row cites that invariant in its Contract cell, or stays a principle and
   the whole-shape mark is dropped. Judgment rows stay as principles,
   unchanged. The hub's negative knowledge is two-part negative knowledge
   (what Weft is not; paths tried and discarded); whole-shape choices are
   part of the identity invariants, not a third part.
5. [WT-5]: unchanged except a cross-reference to [WT-2a] and to the
   `[ALT-WT-*]` tags.
6. [WT-6]: each entry gains a one-line tag `Kind: tried and discarded` or
   `Kind: boundary refusal`. The existing `Reconsider when:` line is the
   entry's reopening condition and is not duplicated; no `Reopen when:`
   line is added. The six `boundary refusal` entries also gain a
   cross-reference to [WT-5], since boundary refusals are "what Weft is
   not" in the hub's two-part scheme. Two entries ([ALT-WT-003],
   [ALT-WT-010]) carry a proposed edit to their existing `Reconsider when:`
   line, flagged for the owner. The stale implementation reference in
   [ALT-WT-015] is lifted (mismatch kind 2).
7. [WT-7]: add "Altitude collapse" (both sides, hub wording) and "Module
   theory used to excuse a divergent subsystem" (hub wording at
   `e6ac579`), each cross-referencing the existing "Blunt local mechanism"
   and "Repeated rejected proposals" entries.
8. [WT-9]: add `[REV-WT-004]` recording this plan: current account,
   supersedes (the draft status, scattered identity, untagged negative
   knowledge), pressure (the 2026-11-01 deep dive and the hub's
   `[REV-AT-006]`), evidence (this plan, the audit table, the review log).

`skills/crystallize-program-theory/SKILL.md`: takes the hub's landed
structure, path-adapted, with the Weft-specific sections retained (§4a
row; task 2). The Weft-specific Blast Radius bullet about module
docstrings stays; the Maintenance Note that invites module-theory
headers is replaced by the hub's module-divergence check (§3 "Choose
extension shape": module theory by human decision only, for a weakly
coupled module with module-only theory-level invariants).

`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: the
Round-2 variant paragraph takes the hub wording at `e6ac579`, which keeps
the one-scoped-round rule and now also names `bin/check-doc-paths` for
promoted `Replacement:` blocks.

`docs/agent-context/runbooks/writing-plans.md` §4c: the last rule ("do not
treat plan-only text as a second governing contract after promotion")
gains the hub's Replacement-sync paragraph at `e6ac579`, path-adapted.

`bin/check-doc-paths`: gains the hub's `_plan_replacement_sync_failures`
check and its self-test probes from `e6ac579` (task 6a).

`AGENTS.md` (and `CLAUDE.md`, a symlink to it): in read-order item 1 only
the clause "frames interpretation and placement, and does not override
winning contracts" changes, to the hub's `e6ac579` clause for that item,
path-adapted; the **Module theory** paragraph takes the hub's `AGENTS.md`
wording at `e6ac579` (task 4) and a pointer to the [WT-7]
module-divergence falsifier. `docs/agent-context/README.md` read-order
item 1 takes the same clause swap.

## 5. Tasks

### Slice A — Propagate the hub delta into Weft

1. Confirm the hub source pin recorded in §2 (`e6ac579`).
   - Verify: `git -C ../agent-theory log --oneline -1 -- docs/plans/2026-11-01-program-theory-four-part-balance-plan.md`
     prints `e6ac579 Update agent theory to better define roles`; paste the
     output. Stop if the file is untracked: the hub change is not landed
     and Slice A does not start. If the command prints a later SHA, the
     hub moved after this pin: stop and re-pin before Slice A.
2. Rewrite `skills/crystallize-program-theory/SKILL.md` to the hub
   structure, path-adapted, with the Weft-specific sections retained: the
   four-part `[ALT-WT-*]` admission test, the gate-run step, the coalescing
   and `RECONSIDERATION REQUESTED` When To Use bullets, and the Weft Output
   Standard. They are Weft process content with no hub conflict (§4a row).
   - Read first: both copies side by side; the Weft Blast Radius and
     Governing References sections.
   - Keep Weft paths (`docs/specifications/`, `[WT-*]`, `[ALT-WT-*]`).
   - The staged block in the appendix is the hub text; the retained
     sections are listed beside it and merged at task 13, and the block is
     rewritten to the merged text so it stays contained in the file.
   - Verify (after task 13): `bin/check-doc-paths` exit 0; the file contains the strings
     `theory-level`, `theory-versus-code`, and `weakly coupled` (grep, one
     hit each or more; all three exist in the hub skill at `e6ac579`).
3. Replace the review-loops runbook Round-2 variant paragraph and its
   quoted prompt with the hub wording at `e6ac579` (the one-scoped-round
   rule, which now also names `bin/check-doc-paths` to confirm promoted
   `Replacement:` fenced blocks still match their target files).
   - Verify (after task 13): `grep -c 'never to re-check' docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
     prints `1` (the hub wraps the phrase across two lines, so the grep is
     for its opening words), and `grep -n 'bin/check-doc-paths' docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
     prints a line inside the Round-2 variant paragraph.
4. In `AGENTS.md`, as hub text with path adaptation only. Edit
   `AGENTS.md` once: `CLAUDE.md` is a symlink to it and follows.
   (a) In read-order item 1, replace only the clause "frames
   interpretation and placement, and does not override winning contracts"
   with the hub's `e6ac579` `AGENTS.md` clause for that item (frames
   interpretation, placement, and the direction in which plans move specs
   and code; do not silently break active contracts mid-edit; do not treat
   an existing spec or plan as final when it pulls away from the theory),
   with `[AT-REF-3]` adapted to `[WT-0]`. Keep Weft's "weftness"
   description; do not import the hub's Stub and Class-5 sentences.
   In `docs/agent-context/README.md` read-order item 1, make the same
   clause swap (its clause reads "frames placement and refusal, and does
   not override winning contracts"); nothing else in that file changes.
   (b) the **Module theory** paragraph's
   introductory clause becomes the hub's `AGENTS.md` wording at `e6ac579`
   (human owner decision; weakly coupled module; module-only theory-level
   invariants; not an excuse to diverge):

   > **Module theory** is how this repository's conceptual model is **extended**
   > when a human owner decides a weakly coupled module has theory-level invariants
   > that apply only to that module (not a second global constitution, and not an
   > excuse for a module to diverge from a single whole-program theory):

   The hub paragraph carries no pointer to the falsifier; none is added
   here (the falsifier lands in [WT-7] through task 5's staged block).
   - Verify (after task 13): `grep -c 'does not override winning contracts' AGENTS.md docs/agent-context/README.md`
     prints `0` for both; `test -L CLAUDE.md && readlink CLAUDE.md`
     prints `AGENTS.md`.
5. Draft the [WT-0] four-rule text (the owner's 2026-10-09 quotation
   first, then the hub's four [AT-THEORY-0] rules at `e6ac579` and the
   spec 02 clause that a spec leads the code only across an active plan's
   spec-promotion slice or when a bug violates the contract; §4c item 2),
   the surfaces-table wording, the two [WT-7] falsifiers ("Altitude
   collapse" and "Module theory used to excuse a divergent subsystem"),
   and the `[REV-WT-004]` stub into this plan's
   `## Proposed Theory Delta (drafted)` appendix, under the sub-heading
   `### Slice A propagated text`. Apply nothing to
   `docs/program-theory.md` in Slice A: all theory text, Slice A and
   Slice B alike, is applied once, at task 13, after the review in task 12.
   Each drafted theory item is a labelled Replacement block in the form
   the appendix intro states. Tasks 2, 3, 4, 6, and 6a are likewise
   prepared in Slice A and applied at task 13: the path-adapted skill and
   the `bin/check-doc-paths` port are staged as diffs against the hub pin,
   and the runbook (review-loops and writing-plans), `CLAUDE.md`/`AGENTS.md`,
   plan-index heading, test edit, and context-index text is staged under
   the same appendix sub-heading. They cannot land earlier because the
   skill cites `[REV-WT-004]` and the Module theory clause cites the [WT-7]
   module-divergence falsifier, neither of which exists before task 13.
   Their `Verify (after task 13)` lines run once at task 13.
   - Verify: `git diff --stat ec50bd57 -- docs/program-theory.md` is empty
     at the end of Slice A; the appendix sub-heading
     `### Slice A propagated text` holds the four drafted items.
6. Bump `updated_at` in `docs/agent-context/context.index.yaml`; that is
   the only change it needs (it has no skill entries and no per-entry
   timestamps, and the runbook inventory does not change).
   Stage the hub's `writing-plans.md` §4c Replacement-sync paragraph
   (path-adapted: `docs/specifications/` is canonical; lifecycle status is
   read from `docs/plans/README.md`) for the same task 13 application.
6a. Port the hub's plan Replacement-sync gate into `bin/check-doc-paths`.
   §9 fork 3 was decided by the owner on 2026-11-02: option (a), adapt.
   This task therefore lands as written here plus the row-format and
   `promoted`-status adaptations, staged in the appendix beside the hub
   port and applied at task 13. The port is the lint-clean variant (§4a
   row).
   - Read first: the hub's `bin/check-doc-paths` at `e6ac579`
     (`_plan_replacement_sync_failures`, `_load_plan_index_statuses`,
     `_is_plan_promoted`, `REPLACEMENT_LABEL_RE`, `HEADING_PATH_RE`,
     `SKIPPED_PLAN_STATUSES`, `INDEX_ROW_RE`, `PLAN_STATUS_RE`,
     `PROMOTION_BASELINE_RE`, and the plan-replacement self-test probes)
     and the Weft script, which is typed and has its own `_prose_lines`
     and fence helpers.
   - Port the check into `check_tree` and its self-test probes into
     `self_test`, keeping Weft's typing and helper names.
   - The gate reads the `## Status Index` section of `docs/plans/README.md`.
     Rename the Weft index heading `## Index` to `## Status Index` in the
     same slice. `tests/specs/test_plan_metadata.py` does assert the
     heading: `test_plan_readme_exists_and_defines_status_taxonomy`
     (line 61) has `assert "## Index" in readme_text`, which the rename
     breaks (`"## Status Index"` does not contain the substring). The test
     edit is that line becoming `assert "## Status Index" in readme_text`.
   - Port gaps found while reading both trees (2026-11-02), recorded as
     §9 fork 3 and settled by the owner on 2026-11-02 (adapt):
     (i) the hub's `INDEX_ROW_RE` expects the plan filename as the bare
     first cell and the status word as the second cell, while Weft rows
     put a Markdown link to the file in the first cell, the title in the
     second, and the backticked status in the third; unadapted, the regex matches no Weft row, every Weft plan reads as
     missing from the index, and any Weft plan carrying a labelled
     Replacement block fails with "plan with Replacement block is missing
     from `docs/plans/README.md` `## Status Index`" whatever its status;
     (ii) Weft's status taxonomy is `draft` and `completed` only
     (`ALLOWED_STATUSES` in `tests/specs/test_plan_metadata.py`), and both
     are in `SKIPPED_PLAN_STATUSES`, so with the row format adapted the
     gate skips every Weft plan; checking this plan's blocks at task 13
     needs an active promoted status the taxonomy, the test, and the
     index row do not have today; (iii) `_is_plan_promoted` reads any
     non-pending `Promotion baseline identifier:` value as promoted, and
     §4b's line therefore reads `pending` until task 13 lands.
   - Verify (after task 13): `bin/check-doc-paths --self-test` exits 0;
     `bin/check-doc-paths` exits 0;
     `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`
     passes.

### Slice B — Reconcile the theory under the four-way balance

7. Draft `[WT-2a]` identity invariants from the candidate list in §4c.
   - For each candidate, locate the contract counterpart and confirm it
     holds in the tree today (a test or spec section that fires). A
     candidate with no counterpart and no firing evidence is not an
     identity invariant yet; move it to §9 as a fork.
   - Verify: every spec code cited in [WT-2a] resolves (`grep -c` per code
     against `docs/specifications/`), recorded in the execution log.
8. Reconcile the twelve [WT-4] rows the Slice B draft marked
   whole-shape against [WT-2a]. The `Kind` column is NOT applied to
   [WT-4]: the hub's negative knowledge is two-part negative knowledge
   (what Weft is not; paths tried and discarded), and whole-shape choices
   are part of the identity invariants ("the choices that shape the entire
   program", hub spec 02 at `e6ac579`).
   - Drafted 2026-11-02 and updated after the owner's endorsement the same
     day; see appendix sub-section "[WT-4] whole-shape reconciliation
     (task 8)": ten rows cite [WT-2a.n], and two rows ("One path on all
     platforms" and "Specs are what is") stay principles with their
     whole-shape marks dropped.
   - Input: the twelve rows marked `whole-shape` in the appendix section
     "[WT-4] whole-shape rows to reconcile against [WT-2a]" (the draft's
     rule: whole-shape if changing it would make Weft a different system
     or invalidate more than one other row).
   - For each of the twelve, one of two outcomes: either it becomes (or
     already is) an identity invariant in [WT-2a] (a new item meets
     task 7's admission rule: a contract counterpart and firing evidence),
     and the [WT-4] row cites that `[WT-2a.n]` in its Contract cell; or it
     stays a principle and the whole-shape mark is dropped. Record each
     outcome in the working table; none is left silent.
   - The eighteen judgment rows stay as principles, unchanged.
   - Verify (after task 13): the [WT-4] principle row count is 31, the
     30 at baseline plus the "Observable with standard tools" row added by
     the owner-endorsement additions (the row-count command in the
     execution log, rerun);
     `grep -c '| Kind |' docs/program-theory.md` prints `0`; every
     `[WT-2a.n]` cited in a [WT-4] Contract cell is an identifier that
     exists in [WT-2a] (grep, pasted).
9. Tag every [WT-6] entry with a `Kind:` line (`tried and discarded` or
   `boundary refusal`) and verify that its existing `Reconsider when:`
   line still holds. Add only the `Kind:` line; do not add a second
   `Reopen when:` line. The six `boundary refusal` entries also gain a
   cross-reference to [WT-5] in their `Kind:` line, since boundary
   refusals are "what Weft is not" in the hub's two-part scheme. The proposed edits to the
   existing `Reconsider when:` lines of [ALT-WT-003] and [ALT-WT-010] are
   applied only if the owner accepts them.
   - Verify (identifier sets, not counts): the set of `[ALT-WT-*]` headings
     equals the set of entries carrying a `Kind:` line:
     `diff <(grep -oE '^### \[ALT-WT-[0-9]+\]' docs/program-theory.md | sed 's/^### //' | sort) <(awk '/^### \[ALT-WT-/{id=$2} /^- Kind:/{print id}' docs/program-theory.md | sort)`
     is empty, and `grep -c '^- Reopen when:' docs/program-theory.md` is
     `0`. Paste both outputs.
10. Altitude pass over [WT-2], [WT-3], [WT-4], [WT-8].
    - Drafted 2026-11-02; see appendix sub-sections "Altitude pass"
      (rows 1 to 20) and "Altitude pass extension blocks".
    - Apply the altitude-collapse falsifier per sentence in both
      directions. Too concrete: a `weft/` path, a private attribute, a
      measured figure (the "~100 ms" in the "A quiet task does nothing"
      row is one). Too abstract: a sentence two incompatible designs could
      both cite. Lift or sharpen; log each change in §4a.
    - [WT-3] coverage: for each concept the 2026-11-01 deep dive found
      without an ownership row (runner plugins, agent sessions,
      pipelines, endpoints, streaming, service registry and convergence,
      heartbeat, retention, interactive), apply one test: does a
      possessor of the theory need this row to judge whether a feature
      belongs? If yes, add the row with owner and contract. If no, add
      one line under the table naming the concept and the existing rows
      it composes from. Every one of the nine gets one of the two
      outcomes; none is left silent.
    - Required outputs beyond [WT-2], [WT-3], [WT-4], [WT-8]: drafted
      replacement text, in the same pass and under the §4 altitude rule,
      for each of [WT-0], [ALT-WT-002], [ALT-WT-004], [ALT-WT-008],
      [ALT-WT-021], and [REV-WT-001]. Each of the six gets replacement
      text in the appendix or a Deviation Log row naming the span kept
      as an allowed owner quotation; none is left silent.
      Attached 2026-11-02: eleven rows (10 to 20) across the six sections
      in the appendix "Altitude pass" table, with their Replacement blocks
      under "Altitude pass extension blocks"; no owner quotation was among
      the hits, so no allowed-quotation Deviation Log row is needed.
    - Verify (altitude presence check, not proof):
      `grep -nE 'weft/|self\._|[0-9]+ ms' docs/program-theory.md` is
      run and pasted. The theory file is not yet edited at task 10, so
      every hit must map either to a drafted replacement in the appendix
      or to a Deviation Log row listing it as allowed (owner quotation).
11. Attach the drafted delta to this plan under
    `## Proposed Theory Delta (drafted)` and run the self-driven
    fresh-eyes review (§8).
12. Cross-family review, one round, on the plan and drafted delta
    together, per `skills/call-agent/SKILL.md`. Apply dispositions; log in
    §8. A second round only after `BLOCKED` or a scope change.
13. Apply the reviewed delta to `docs/program-theory.md`. This is the
    single point where theory text is applied: the Slice A propagated text
    (task 5) and the Slice B delta (tasks 7 to 10) land together, after
    the review in task 12. The staged non-theory Slice A text (tasks 2,
    3, 4, 6, 6a) lands in the same change. Under §9 fork 3 option (a),
    this plan's index row moves to `promoted` so the ported gate checks
    every labelled Replacement block against its target file, and task 17
    flips it to `completed`; under option (b) or (c) no gate checks the
    blocks.
    - Verify: `tests/specs/test_spec_hygiene.py`, `bin/check-doc-paths`,
      and `tests/specs/test_plan_metadata.py` pass;
      `git diff --stat ec50bd57 -- docs/specifications/` is empty.
    - Verify (altitude presence check, not proof):
      `grep -nE 'weft/|self\._|[0-9]+ ms' docs/program-theory.md`
      prints only the lines the Deviation Log lists as allowed (owner
      quotations). Paste the output.

### Slice C — Audit table, possession probe, adoption

14. (Task 14 done 2026-11-02: rows re-keyed, drift list confirmed through rounds 5 to 8.) Produce the theory-versus-code audit table as a section of this plan.
    - One row per [WT-2a] invariant and per [WT-4] principle. Columns:
      identifier; claim in one clause; contract (spec code); evidence in
      the tree (a path, test, or spec section that resolves today);
      classification `aligned` / `drifted` / `theory revision`; for
      `drifted`, the cleanup-series plan number that closes it; for
      `theory revision`, the §9 fork number.
    - Known `drifted` candidates the row must confirm or reject from the
      tree: the pull-scanned STOP/KILL acks against "push, never
      pull-scan" ([MF-3]); the 50 ms timer floors on reactor edges against
      "Timers exist only for clock work or absence detection"; the
      two-clock heartbeat in spec 05 against "One reactor, one arbiter";
      the family-scheduler rebuild on retire against [ALT-WT-016]; the
      lossy TID short form against the lossless-identity invariant.
    - Known `theory revision` candidate: Manager supersession's one-way
      drain against [ALT-WT-007] (fork 1 in §9), carried as its own row
      keyed `[ALT-WT-007]`.
    - Verify (identifier sets, not counts): the set of [WT-4] principle
      names equals the set of principle names in the audit's `4.x-y`
      rows:
      `diff <(awk '/^## Design principles \[WT-4\]/{f=1} /^## What Weft is not/{f=0} f && /^\| / && !/^\| Principle/ && !/^\|---/' docs/program-theory.md | awk -F'|' '{gsub(/^ +| +$/,"",$2); print $2}' | sort) <(awk -F'|' '/^\| 4\.[1-4]-[a-z] /{s=$2; sub(/^ *4\.[1-4]-[a-z] /,"",s); gsub(/ +$/,"",s); print s}' docs/plans/2026-11-02-program-theory-reconciliation-plan.md | sort)`
      is empty; and the set of `[WT-2a.n]` identifiers in
      `docs/program-theory.md` equals the set of invariant identifiers in
      the audit table after the task 17 renumbering. Paste both outputs.
15. (Task 15 done 2026-11-02: PASS, three of three (review log).) Run the possession probe on this plan under the rewritten rule (this
    plan's Class-5 completion is a trigger under §4c item 1).
    - The owner writes the key first: one hypothetical feature and where
      it belongs, one category error and the [WT-5] or [WT-6] entry that
      refuses it, and the class of bug an audit of a named subsystem
      should find. The key is recorded in §8 before the probe runs.
    - A fresh agent of a different family, which has not read this plan,
      answers from `docs/program-theory.md` alone.
    - Grade against the key. Record PASS or FAIL in §8. On FAIL, classify
      the failure after disposition as a document deficiency, a key
      fault, or agent reasoning, and record a document deficiency (the
      sentence the document lacked) only when the evidence supports it.
      A FAIL stops adoption; where the classification is document
      deficiency, the theory is sharpened at that sentence; the probe is
      rerun once with a new key.
16. (Done 2026-11-02; blocks under the appendix sub-heading "Task 16".) The `[WT-0]` Verification line rewrite (§4c item 1, which defines the
    theory-versus-code audit table the staged [WT-7] possession-probe
    bullet cites) is drafted in Slice B as the Replacement block under the
    appendix sub-heading "[WT-0] Verification line" and applied at task 13
    with the rest of the theory text, so the [WT-7] citation and its
    definition land together. This task covers only [WT-7] and the
    revision record. Record in `[REV-WT-004]` why: the probe ran twice in its first eight weeks;
    neither record shows a preregistered key, who wrote the expected
    answers, or what the answers were graded against; and no release ran
    it. The trigger stays aligned to the hub (each release or Class-5
    completion), now with an owner-written key and an audit-table refresh
    beside it. Replace the [WT-7] "Possession probe" bullet with the
    keyed-probe wording (owner-written key, non-author agent, each release
    or Class-5 completion, the audit-table refresh carrying unchanged rows
    forward by reference and re-auditing touched claims), so [WT-7] and
    the header name the same trigger. Add the transfer-failure log
    rule to the [WT-7] "Repeated rejected proposals" entry so a reviewer
    knows where to write the row; the rule logs a proposal only when it
    does not cite the entry's recorded reopening condition.
17. (Task 17 done 2026-11-02: see the "Task 17" appendix blocks; landing commit pending the owner.) Flip the theory header to adopted wording, complete `[REV-WT-004]`,
    add the `CHANGELOG.md` entry, and flip this plan's index row and
    `Status:` to `completed` in the same change.
    - Verify: `git log -1` shows the landing commit; all gates in §6 exit 0.

## 6. Verification

Run after every slice; all must exit 0:

```bash
. ./.envrc && ./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py tests/specs/test_plan_metadata.py -q
```

```bash
bin/check-doc-paths
```

```bash
bin/coalesce-check
```

Grep gates named per task in §5 are pasted into the execution log with
their output.

## 7. Rollback

Documentation only. Rollback is `git revert` of the landing commit for the
affected slice. No runtime state, queue, or persisted format is touched, so
no rollout order or compatibility window exists.

## 8. Review Log

- Self-driven fresh-eyes review (plan author, 2026-11-02, after the
  Slice B and Slice C drafts were attached): done. Findings and
  dispositions:
  1. Slice A task 4 as first written only added the hub's escape clause,
     which still permits module theories and contradicts the new [WT-4]
     row. Fixed in plan: task 4 now adds the Weft position sentence.
     [Withdrawn 2026-11-02 under the owner's scope direction: the Weft
     position sentence and its row are out of scope; task 4 adds the hub
     escape clause only.]
  2. The new row's contract, the [WT-7] module-divergence falsifier, is
     drafted in Slice A task 5 (appendix sub-heading
     `### Slice A propagated text`) and applied at task 13 together with
     the [WT-4] row from task 8, so the row never lands before its
     contract. No theory text is applied before the task 12 review.
     [The row was withdrawn 2026-11-02 under the owner's scope direction;
     the falsifier still lands at task 13 as hub text.]
  3. The altitude rule was silent on evidence lines and on sections
     outside [WT-2]/[WT-3]/[WT-4]/[WT-8]. Fixed in plan: §4 now states
     the rule covers evidence lines and task 10 covers the six further
     sections the drafting pass listed.
  4. Two sentences in [WT-2] and [WT-4] ("every CLI requester mints a
     TID"; "push, never pull-scan") disagree with the code. Disposition:
     drift, not altitude; the audit table carries them as rows 4.1-a and
     4.1-b for cleanup plan 3. The theory sentences stand.
  5. The two drafts disagreed on evidence for [WT-2a.2] (queues as the
     only durable truth) and [WT-2a.5] (spawn isolation). Resolved by the
     cross-family review from the tree; not an owner fork. [OBS.2] states
     the no-second-lifecycle-store rule (`07-System_Invariants.md` line
     313: "Weft does not use a separate state database for task
     lifecycle"). [IMPL.5] and [IMPL.6] state the spawn contract (lines
     825 and 827), and production code uses spawn contexts:
     `grep -n 'get_context("spawn")' weft/core/launcher.py weft/core/runners/host.py`
     → `weft/core/launcher.py:202` and `weft/core/runners/host.py:465`.
     No test asserts the start method directly; that is an
     evidence-strength note, not an admission blocker. Both invariants
     stay in [WT-2a], and the item is removed from fork 2.
  6. `CLAUDE.md` §3 carries two contract lines (reserved-queue policy,
     process-title format) that must not be repointed at [WT-2a]. Fixed
     in plan: §4c item 3.
- Cross-family review round 1: codex (0.161.0), 2026-11-02, on the plan
  and the drafted delta together. Verdict: BLOCKED on question 1 (the plan
  could not be implemented confidently without choosing between
  conflicting instructions). Findings F1–F8, all accepted. Dispositions:

  | Finding | Severity | Disposition | Where applied (section or task) |
  |---------|----------|-------------|---------------------------------|
  | F1 Identity sentences broaden three recorded boundaries | P1 | Accepted. [WT-2a.2] keeps the [ALT-WT-020] private-state boundary and cites [OBS.2] beside [OBS.1]; [WT-2a.5] covers Weft runtime processes created by spawn-context multiprocessing, not native subprocesses; [WT-2a.6] names TaskMonitor's maintenance worker as the single [IMPL.8]/[IMPL.11] exception; [WT-2a.9] points at [IMPL.1] instead of the literal code; altitude-pass row 4 names canonical resolution under one owner, not a module | Appendix "[WT-2a] Identity invariants" (evidence table and replacement markdown); appendix "Altitude pass" row 4; §4c item 3 candidate list |
  | F2 Delta does not implement the operative scope | P2 | Accepted. Out-of-scope note replaced by the §4 rule; six sections listed as required task 10 outputs; [ALT-WT-015] Candidate and Reason lifted; §3 file list covers the §3 "NEVER break" list (a module-theory file entry added here was withdrawn from scope on 2026-11-02); task 16 replaces the [WT-7] "Possession probe" bullet | Appendix "Altitude pass" and execution log; appendix "[ALT-WT-015]"; §3 Files to modify; tasks 4, 10, 16 |
  | F3 Theory applied before its review gate | P2 | Accepted. Task 5 drafts Slice A theory text into the appendix and applies nothing; task 13 is the single application point; review-log item 2 reworded | Tasks 5, 13; §3 context; §9 hub-wording fork (the hub resolved it at `e6ac579` with the two-part presumption, and the fork was deleted); §8 item 2; appendix "Slice A propagated text" |
  | F4 "Both self-graded" unsupported | P2 | Accepted. Replaced by the narrower evidence gap: no record shows a preregistered key, who wrote the expected answers, or what the answers were graded against | §2 prior probes; task 16 |
  | F5 Verification treats signals as diagnoses | P2 | Accepted. Natural experiment logs only proposals that do not cite a fired reopening condition; probe FAIL classified after disposition; "Possession (owner)" renamed "Owner endorsement" | §4c item 1; §3 context; tasks 15, 16; §8 probe and endorsement lines |
  | F6 Misplaced classification and false test reference | P2 | Accepted. 4.3-f kept aligned; new `[ALT-WT-007]` row carries the one-way drain as theory revision, fork 1; [WT-2a.6] evidence points at `tests/tasks/test_task_execution.py`; candidate numbering note added | Appendix audit table, summary counts, "Rows that need the owner" item 1; appendix [WT-2a] evidence table |
  | F7 Gates pass when their claim is false | P2 | Accepted. Baseline diff for specs; altitude presence check after tasks 10 and 13; whole-block module comparison; task 8 arithmetic corrected; identifier-set coverage for tasks 9 and 14 | §4 Review gates; tasks 4, 8, 9, 10, 13, 14 |
  | F8 "One central theory" whole-shape not demonstrated | P2 | Superseded by owner direction 2026-11-02: module-theory policy is out of scope for this plan; the row, audit row 4.4-l, and fork 4 are removed. | §1 Goal; §3 question 3; §4c item 4; tasks 4, 5, 8; appendix [WT-4] table, rationale, tally, and counts; audit table and summary counts |

  Reviewer altitude note on [WT-6] tags (no second `Reopen when:` line)
  and the fork 2 evidence item were applied with the findings: task 9,
  §4c item 6, appendix "[WT-6] tags", §9 fork 2, §8 item 5.
- Self-review after dispositions (plan author, 2026-11-02): one follow-on
  defect found and fixed. (a) Slice A tasks 2 to 4 would have landed
  pointers to `[REV-WT-004]` and the [WT-7] module-divergence falsifier before
  task 13 created them; all Slice A text is now staged and applied at
  task 13.
- Cross-family review round 2 (codex 0.161.0, 2026-11-02, scoped to the
  F1, F2, F3 fixes and the self-review follow-ups): PASS. F1, F2, F3
  closed; no new blocking defect. Two non-actionable observations (stale
  sentences in §2 and in the delta appendix describing the pre-fix task 4
  and skill sequencing) fixed in the same pass. The review loop is closed
  under the one-scoped-round rule; the next gate is the owner's. The 4.4-l
  row that round 2 verified was removed afterwards under the owner's scope
  direction, which narrows rather than changes the verified instructions.
- Scope change after review (2026-11-02): hub revised at e6ac579 after
  round 2; plan realigned (items 1 to 7 of this pass). Under the
  one-scoped-round rule a further scoped round is warranted for scope
  changes: Cross-family review round 3 (scoped to the hub-alignment
  changes and the Slice A staged text): codex 0.161.0, 2026-11-02, FAIL
  on F9 and F10, both P2, both accepted and applied. F9: fork 3 option (a)
  under-stated the synchronization (header and index row must change
  together; `promoted` must enter every vocabulary clause; the ported
  fixtures must use Weft-shaped rows) and option (b) is not inert as
  copied; fork 3 rewritten. F10: task 16 asked for the [WT-0] audit-table
  definition at task 16 while the staged [WT-7] bullet cites it at task
  13; the Verification line is now drafted as a Replacement block in the
  appendix and applied at task 13, task 16 narrowed. Round 3 also
  confirmed the four hub comparisons, all 41 fences, all Slice A anchors,
  and the three fork 3 gaps. Two stale Kind-column mentions fixed.
  Slice A staged from e6ac579 and merged (2026-11-02); staging dry-run on a scratch tree: every block contained in its target, both gates exit 0, one-word mutation fails as expected; the gate port gaps are fork 3.
- Cross-family review round 4 (codex 0.161.0, 2026-11-02, scoped to the
  F9 and F10 fixes): PASS, no open findings, no new defect. The review
  loop is closed; every remaining gate is the owner's (forks 1 to 3, the
  transfer-probe key, endorsement of [WT-2a] and the task 8 reconciliation).
- Owner decision, fork 1 (2026-11-02): one-way drain intended; [ALT-WT-007]
  restated as a Slice B Replacement block; plan 4 withdrawn; no code change.
  Scope narrows rather than changes, so no further review round is owed.
- Cross-family review round 5 (codex 0.161.0, 2026-11-02, scoped to the
  text drafted after round 4: the restated [ALT-WT-007], the fork 2
  invariant text, the task 8 rows and §9 item 4 candidates, the eleven
  altitude blocks and evidence correction, the adapted gate and vocabulary
  blocks, the §4 clause): PASS, no open findings. Two observations applied:
  the [ALT-WT-021] correction now adds provenance beside the baseline
  commit instead of replacing it; altitude row 20 and its block are
  removed as optional under the extended §4 clause.
- Owner decision, fork 3 (2026-11-02): adapt the gate port (option (a));
  task 6a unconditional; the adaptation is staged beside the hub port.
- Owner decision, fork 2 (2026-11-02): trust domain admitted as
  [WT-2a.10]; lossless TID rendering admitted into [WT-2a.1] as a new
  decision, plan 12 scope set to remove the lossy encoding, its tests, and
  the search code; shell rule admitted into [WT-2a.9] verbatim. Ten
  invariants drafted, none forked. Additions to the drafted delta are
  owner-authored text, so no further review round is owed.
- Task 8 and task 10 extension drafted (2026-11-02): five rows cite [WT-2a.n]; four candidate invariants raised as §9 item 4; eleven altitude rows across six sections; one evidence correction ([ALT-WT-021]) logged in §4a.
  §9 item 4 is an owner gate; no review round is owed for owner-decision
  text, but the eleven altitude blocks and the evidence correction are new
  drafted text and are covered by the next scoped round if one is run.
- Owner endorsement (2026-11-02): fourteen identity invariants endorsed as modified by discussion ([WT-2a.5] and [WT-2a.6] restated one notch up, .6 made composable after reviewing Taut [MCP-8]; [WT-2a.7] exactly-once-per-reservation added; [WT-2a.11] trimmed with the SimpleBroker model moved to [WT-5]; candidates .12 and .13 admitted, .14 trimmed and admitted, 'one architecture on every platform' refused; 'observable with standard tools' recorded as a principle with a falsifier; the TaskMonitor example was dropped at round 6 as unnecessary). Restated and new sentences are agent-drafted and fall to a scoped round; the decisions are the owner's.
- Cross-family review round 6 (codex 0.161.0, 2026-11-02, scoped to the endorsed-set drafting): FAIL on F13 to F19, all P2, all accepted and applied (F13 parent-state clause; F14 reservation lifetime per [EXEC.1]/[MF-2]; F15 acceptance versus launch admission; F16 TaskMonitor example removed; F17 input origins; F18 I-12 qualifier broadened; F19 [WT-2a.13] provisionally narrowed, §9 item 5 for the owner). Round 7 (codex 0.161.0, 2026-11-02, scoped to these fixes and the STOP
  rule text): F13 to F19 verified, including the code reading behind the
  I-13 ordering drift; FAIL only on F20 (P2), two audit-summary sentences
  still describing the narrowing as provisional and plan 4 as unable to
  start. F20 applied and verified locally by grep (no "provisional" or
  "cannot start" claim about plan 4 remains); as a text-sync defect with
  no change of scope, it closes without a further round, and the ported
  Replacement-sync gate is the standing control for this class of drift
  once this plan is promoted.
- Cross-family review round 8 (codex 0.161.0, 2026-11-02, scoped to the
  task 16 blocks, the task 13 additions, the probe log, and the two
  relabelled blocks): FAIL only on F21 (P2): the [WT-5] cross-reference
  overstated that the six boundary-refusal entries follow from [WT-5].
  Applied: neutral cross-reference ("Boundary refusals are tagged ... in
  [WT-6]") in the theory and the block. Round 8 also confirmed the probe's
  STOP coverage question is real (the broadcast visits only the local child
  table; recovery adopts earlier tasks into scheduler evidence without
  adding them to it) and belongs beside plan 4 as an open question; the
  relabelled blocks are appropriate as historical. F21 is a one-clause
  text fix verified by the gate, so the loop closes here.
- Owner decision, §9 item 5 (2026-11-02): system STOP is the Manager's
  responsibility to stop active tasks, then internal services, then itself;
  [WT-2a.13] carries the rule; the code broadcasts STOP to tasks and
  services together, so row I-13 is drifted on ordering and plan 4 is
  re-scoped to STOP ordering.
- Closure (2026-11-02): tasks 14 to 17 done; the theory header reads Active, adopted; [REV-WT-004] is complete; the audit rows are re-keyed; the CHANGELOG entry is added; this plan's status and index row read `completed`. The landing commit is the owner's.
- Transfer probe key (recorded 2026-11-02, before the probe). The owner
  chose three items from a fourteen-item bank drafted by the plan 1 author
  (held outside the repository; the drafting agent is excluded from taking
  the probe) and confirmed the expected answers:
  1. Placement: "A `weft top` daemon that keeps a live index of task status
     for fast display." Expected: a stateless CLI read model over the
     queues is fine; a daemon with its own index is a second control plane
     ([WT-5]; principle "Observable with standard tools"; [WT-2a.2]); place
     it as a view, never a store.
  2. Category error: "Cache task status in a Manager-local table so
     `weft task list` is fast." Expected refusal: [WT-2a.2] queues are the
     only durable truth; [ALT-WT-010] no new cache subsystem; [ALT-WT-019]
     queues are not relational tables; the correct move is a read model over
     queues or faster peeks.
  3. Bug-class prediction: "Audit the Manager's STOP drain." Expected: tasks
     and internal services signalled together, with no
     tasks-then-services-then-self ordering ([WT-2a.13], second sentence).
  Grading rule: an answer passes when it reaches the expected placement,
  refusal, or prediction and cites at least one of the expected entries or
  an entry that entails it; a different but defensible entry is a key fault,
  not a document deficiency.
- Transfer probe result (2026-11-02): PASS, three of three. Taker: codex
  0.161.0, read-only sandbox, a different family from the plan author and
  the bank drafter; its command log shows it read `docs/program-theory.md`
  and nothing else. Graded against the key by the plan author:
  1. Placement: an optional CLI view deriving status from queues, with any
     display cache disposable and nothing depending on it; a daemon with
     its own index refused as a second control plane. Cited [WT-2a.2],
     [WT-5], [WT-7], "Observable with standard tools". PASS.
  2. Category error: refused; a Manager-local status table would promote
     one process's private state into a lifecycle store that detached
     tasks outlive. Cited [WT-2a.2], [ALT-WT-010], [ALT-WT-015],
     [ALT-WT-020]; [ALT-WT-019] not cited but entailed. Correct move:
     queue-based discovery over existing primitives, measured on both
     backends. PASS.
  3. Prediction: "incomplete or misordered system shutdown: the Manager
     exits while detached active tasks remain, or stops its internal
     services before settling those tasks", citing [WT-2a.13]'s second
     sentence, [ALT-WT-007], [ALT-WT-015]. PASS, and sharper than the key:
     it predicts a coverage defect (the Manager's local child table is not
     the whole system, so detached tasks launched by an earlier Manager
     are not signalled) beside the ordering defect the audit recorded.
     Recorded as a scope note for cleanup plan 4 and on audit row I-13.
  No document deficiency found; no key fault; the theory transferred.
- Owner endorsement (sentence-level ratification, not a demonstration of
  possession) of [WT-2a], the task 8 reconciliation of the twelve
  whole-shape [WT-4] rows against [WT-2a], and the §9 forks: done
  2026-11-02 for the decisions (the fourteen-entry set, the forks, and the
  reconciliation outcomes). Confirmation of the applied sentences in
  `docs/program-theory.md` happens at task 17, after task 13 lands them.

## 9. Owner forks (not decided by this plan)

1. **Manager supersession. Resolved by the owner, 2026-11-02.** The
   one-way drain is the intended behavior. In the owner's words: "What
   should happen is that A continues draining and exits. The next call
   that requires a manager should see that there is no active manager
   and should start one (manager C), which becomes the new primary
   manager." The 2026-05-20 lesson behind [ALT-WT-007] established only
   that a superseded Manager must not cancel user work (a non-stopping
   drain) and that one condition gets one policy; it never required a
   drain that resumes. The leadership-yield drain revalidates because it
   acts on self-observed, possibly stale leader proof; the superseded
   drain acts on a row a live peer just wrote. Those are two conditions,
   so two policies do not violate the entry's principle. Consequence:
   [ALT-WT-007] is restated (drafted in the appendix [WT-6] section and
   recorded in `[REV-WT-004]`), the spec's shutdown-authority clause in
   [MA-1.4] is not drift, no code changes, and cleanup plan 4 loses its
   supersession scope (it is re-scoped to system STOP ordering under §9
   item 5). Direction note for the record: under
   [MA-1.4] the lowest TID wins automatic convergence, so the preexisting
   Manager writes the row and the new one drains; the operator
   `--replace` path runs the other way. Both end as the owner describes.
2. **Identity-invariant candidates. Resolved by the owner, 2026-11-02.**
   (a) The single operator trust domain is kept and admitted as
   [WT-2a.10] (renumbered [WT-2a.11] at the 2026-11-02 endorsement, §9
   item 4), worded on SimpleBroker's security model (trusted by
   placement, checked for shape, intent never inspected); the spec-level
   adaptation of SimpleBroker's security section into spec 00 goes to
   cleanup plan 10. (b) "Every rendering of the TID is lossless" is admitted
   into [WT-2a.1] as a new decision made in this session; the lossy
   encoding, its tests, and the associated search code are removed by
   cleanup plan 12, whose scope now reads: retire [OBS.5] and the
   ten-digit short form, the candidate-discovery and ambiguity code, and
   the liveness host's title regex, replacing them with a lossless
   rendering inside the process-title budget. The refusal "Weft has no lossy
   short form and no search that resolves an ambiguous one" is no longer
   part of [WT-2a.1]; cleanup plan 12 records it as a new [ALT-WT-*] entry
   when the old encoding is removed. (c) "Weft does not support
   shell=True. The only way someone could get a shell is if they construct
   a task with a command line that creates a shell." Admitted into
   [WT-2a.9] (renumbered [WT-2a.10] at the 2026-11-02 endorsement) verbatim; the refusal test goes to cleanup plan 11. The
   appendix "Fork 2 candidates" section records the same.
3. **Porting the hub's Replacement-sync gate. Resolved by the owner,
   2026-11-02: adapt (option (a)).** The three gaps the staging found
   (hub index-row regex expects a bare filename then a status cell; Weft's
   taxonomy is `draft` and `completed` only and the gate skips both; any
   non-pending promotion baseline reads as promoted) close through the
   adaptation: `INDEX_ROW_RE` learns Weft's row shape (a Markdown link to
   the file, a title, a backticked status, a superseded-by cell); a third
   status `promoted` enters `ALLOWED_STATUSES` in
   `tests/specs/test_plan_metadata.py`, the README Status Taxonomy, and
   both clauses of `docs/agent-context/runbooks/writing-plans.md` that
   allow only `draft` or `completed`; the ported self-test fixtures use
   Weft-shaped rows; at task 13 this plan's header `Status:` and its index
   row move to `promoted` together (the metadata test requires agreement)
   until task 17 flips both to `completed`; §4b's promotion baseline stays
   `pending` until task 13. The adapted port is staged in the appendix
   beside the hub version with its own dry run. The gate then checks this
   plan's Replacement blocks from task 13 to task 17.
4. **Candidate identity invariants from the task 8 reconciliation. Resolved
   by the owner, 2026-11-02.** The owner endorsed fourteen identity
   invariants ([WT-2a.1] to [WT-2a.14]), as modified by discussion. Outcome
   for each candidate below (the candidate labels in (a) to (d) are the
   pre-endorsement numbers, kept as the record):
   - (a) admitted as [WT-2a.12], text verbatim; it covers "Everything is a
     task" and "It is just a message".
   - (b) trimmed and admitted as [WT-2a.14] "Submission never blocks"
     (spawn-request queue unblocked, no cost while waiting, only a running
     process consumes capacity; [MF-1]); the admission-policy clause and the
     [MA-1.8] citation are dropped. It covers "Queued work costs nothing;
     live processes cost".
   - (c) admitted as [WT-2a.13], text verbatim; it covers "Availability and
     death are different questions" and "Ask who owns this lifetime".
   - (d) refused as an invariant; "One path on all platforms" stays a
     principle and its whole-shape mark is dropped.

   The endorsement also restated two drafted invariants one notch up:
   [WT-2a.5] becomes "Process isolation" (a fresh process image and its own
   broker state; no handle, connection, or descriptor crosses a process
   boundary), and [WT-2a.6] becomes "One arbiter per reactor; reactors
   compose by messages", made composable after the owner reviewed Taut's
   reactor over workspace reactors ([TAUT-8.5], [MCP-8]); the TaskMonitor
   example was dropped at round 6 as unnecessary. It added [WT-2a.7] "Exactly once per reservation" ([EXEC.1],
   [QUEUE.4] to [QUEUE.6]). It trimmed [WT-2a.11] (the trust domain, formerly
   .10) to the trust statement, and the SimpleBroker-model sentences move
   to [WT-5] (appendix "Owner endorsement additions"). Former .7, .8, .9,
   and .10 are now .8, .9, .10, and .11. "Observable with standard tools"
   was judged a design goal, not an identity invariant, and is recorded as
   a [WT-4] principle with a [WT-7] falsifier (same appendix section). The
   [WT-2a.1] wording note under (a) is resolved: the endorsed text reads
   "minted from the broker's timestamp generator, used as the spawn-request
   message ID for spawned work".

   The candidate evidence below is kept as the record. The candidate text is
   in unlabelled `text` fences, so the Replacement-sync gate never treats it
   as landing text.

   (a) **[WT-2a.11] Everything is a task, and every protocol is a message.**
   Covers "Everything is a task" (line 232) and "It is just a message"
   (line 233).

   ```text
   - **[WT-2a.11] Everything is a task, and every protocol is a message.** Any
     Weft code that exchanges facts with a task does so as a task: it holds a
     TID from SimpleBroker's hybrid-timestamp generator and receives replies on
     its own `T{tid}.ctrl_in`. A protocol is an ordinary message on an ordinary
     queue, pushed to a queue its recipient already watches; no caller has a
     privileged side-channel ([MF-3]). (Owner, 2026-09-17: "In weft, everything
     is a task" and "There is no code without a task identity.")
   ```

   - Contract: [MF-3] (`docs/specifications/05-Message_Flow_and_State.md`
     line 190 heading); line 205, "`reply_to` is the requester's own
     `ctrl_in` queue"; line 315, "PING/PONG uses no target-`ctrl_out` scan".
   - Test evidence:
     `tests/core/test_manager_runtime_connections.py::test_borrowed_manager_probe_routes_reply_and_retires_requester_queue`;
     `tests/tasks/test_pipeline_runtime.py::test_pipeline_task_routes_pong_to_requester_control_queue`.
   - Why [WT-2a.1] does not already cover it: a short-lived CLI requester
     mints its TID from the same generator, but no spawn-request row is ever
     written for it (`weft/core/control_probe.py:266`,
     `requester_tid = generate_spawn_request_timestamp(`; line 271,
     `reply_queue_name = f"T{requester_tid}.{QUEUE_CTRL_IN_SUFFIX}"`).
   - Caveat: the push half holds for PING/PONG only. Spec 05 line 213 keeps
     "non-PONG task-local replies" on `ctrl_out`; that is the drift of
     comprehension answer 1 (mismatch kind 1, a cleanup plan), not a reason
     to weaken the candidate.
   - [WT-2a.1] wording note: its phrase "minted as its spawn-request message
     ID" does not fit CLI requesters, which mint a TID from the same
     generator without writing a spawn request. Proposed one-clause fix, for
     the owner to accept with [WT-2a.11] or independently: "minted from the
     broker's timestamp generator, as the spawn-request message ID for
     spawned work".

   (b) **[WT-2a.12] Queue-first submission; only live processes cost.**
   Covers "Queued work costs nothing; live processes cost" (line 245).

   ```text
   - **[WT-2a.12] Queue-first submission; only live processes cost.** Every
     submission, root or child, enters one spawn-request queue unblocked and
     carries no prospective resource charge while it waits. Admission gates
     only process creation, on measured pressure, never on predeclared per-task
     budgets or child counts ([MF-1], [MA-1.8]). (Owner, 2026-10-14: "Tasks
     sitting waiting to be spawned do not consume processes. Only live
     processes do")
   ```

   - Contract: [MF-1] (spec 05 line 68 heading) and [MA-1.8] (18 bracketed
     occurrences in `docs/specifications/`).
   - Test evidence:
     `tests/core/test_connection_admission.py::test_launch_uses_measured_capacity_without_task_budget`
     (docstring: "The final unreserved slot permits a launch; only actual
     usage counts");
     `tests/core/test_connection_admission.py::test_removed_connection_budget_is_an_actionable_configuration_error`;
     `tests/core/test_spawn_requests.py::test_runtime_origin_keeps_same_context_child_on_primary_with_lineage`
     (the child row is on `weft.spawn.requests` and absent from
     `weft.spawn.children`).
   - Caveat: no test found asserts the "unblocked" half (submission
     succeeds while admission is closed); a grep of test names for
     `unbounded`, `prospective`, `charge`, `budget`, or `submit...capacity`
     found admission-side tests only.

   (c) **[WT-2a.13] Independent lifetimes; death needs proof.** Covers
   "Availability and death are different questions" (line 259) and "Ask who
   owns this lifetime" (line 260), which are one topology choice: children
   are detached from the Manager's lifetime ([WT-2] item 3), so the
   Manager's own view cannot prove a child dead.

   ```text
   - **[WT-2a.13] Independent lifetimes; death needs proof.** A shared Manager,
     each task it launches, and each waiting client have independent
     lifetimes: Manager drain, exit, or supersession changes who takes future
     work and never kills or moves running tasks. Because no lifetime contains
     another, absence is never proof of death: a missing snapshot, an expired
     window, or a failed probe is `unknown`, and task-owned terminal proof
     outranks any supervisor's inference ([MA-1.6a], [MA-3], [MF-5],
     LIVENESS.R3 to LIVENESS.R7; [PY-2]).
   ```

   - Contract: [MA-1.6a] (3 occurrences), [MA-3] (3), [MF-5] (33),
     [LIVENESS.R3] (5), [LIVENESS.R7] (2), [PY-2] (4); bracketed `grep -rF`
     counts against `docs/specifications/`.
   - Test evidence:
     `tests/core/test_manager.py::test_manager_superseded_self_record_drains_children_without_stopping`
     (patches `_signal_children_to_stop` to `pytest.fail("superseded manager
     must not stop launched user work")`);
     `tests/tasks/test_liveness_monitor.py::test_probe_worker_reports_foreign_host_as_attempted_unknown`;
     `tests/core/test_monitor_store.py::test_monitor_store_task_owned_control_and_log_supersede_manager`
     (docstring: "Older task-owned proof wins").
   - Caveat: `tests/core/test_manager.py` also has
     `test_manager_stop_command_drains_nonpersistent_children` (line 8318);
     what "nonpersistent" covers was not checked, so it is neither evidence
     nor counter-evidence. Slice C should check whether an explicit
     `manager stop` signals user tasks.
   - If endorsed, the "Ask who owns this lifetime" row (with altitude row 7
     applied) reads:

   ```text
   | Ask who owns this lifetime | A shared Manager, a child task launched detached from the Manager's lifetime, and a waiting client have independent lifetimes. Manager supersession changes who takes future work; it never kills or moves running children. | Owner, 2026-09-08 | [MA-1], [MA-3]; `14-Python_API_Surfaces.md` [PY-2]; [WT-2a.13] |
   ```

   (d) **[WT-2a.14] One architecture on every platform.** Covers "One path
   on all platforms" (line 274).

   ```text
   - **[WT-2a.14] One architecture on every platform.** Windows, macOS, and
     Linux run the same lifecycle, reactor, and cleanup architecture; a
     platform difference lives only in a narrow adapter leaf behind an opaque
     interface, such as the platform readiness adapter ([RM-5.2]).
   ```

   - Contract: [RM-5.2] (`docs/specifications/06-Resource_Management.md`
     line 223; lines 232–233 say pipe I/O "is owned by an opaque platform
     readiness adapter behind the supervisor reactor").
   - Test evidence: no test asserts platform parity directly. The suite runs
     on all three OSes (`.github/workflows/test.yml` line 28,
     `os: [ubuntu-latest, macos-latest, windows-latest]`).
   - Caveat: the counterpart is weaker than the other candidates'. [RM-5.2]
     covers subprocess supervision only, and the row's current Contract cell
     names a plan (`2026-09-10` process-title plan), not a spec. If the
     evidence is too thin for task 7's admission rule, the row takes
     outcome (ii).
5. **[WT-2a.13] and system STOP. Resolved by the owner, 2026-11-02.**
   The endorsed broad sentence ("Manager drain, exit, or supersession ...
   never kills or moves running tasks") was contradicted for an explicit
   operator stop by [MA-3] (spec 03 near line 801) and by
   `tests/core/test_manager.py::test_manager_stop_command_drains_nonpersistent_children`.
   The owner stated the rule: "STOP on the whole weft system means that it
   is the manager's responsibility to (try to) stop/suspend/kill any active
   tasks, then internal services, then the manager itself." The invariant
   now carries that rule as its second sentence, and the implicit cases
   (supersession, idle exit, crash) keep the never-kills clause. Reading
   the code against the rule: managed internal services are children in
   the same table that the STOP broadcast iterates
   (`weft/core/manager.py::Manager._signal_children_to_stop` over
   `_child_processes`, services included as supervision-only children), so
   a STOP signals tasks and internal services together and then exits; no
   tasks-then-services-then-self ordering exists, and [MA-3] does not
   order services either. That is drift of kind 1 (the theory is right, the
   spec and code lag): audit row I-13 is aligned for the implicit-lifecycle
   sentence and drifted for the STOP-ordering sentence, and cleanup plan 4,
   withdrawn as a supersession plan under fork 1, is re-scoped to "system
   STOP ordering: active tasks, then internal services, then the Manager",
   covering [MA-3] and the drain path. The 2026-11-02 possession probe
   added a second question for the same plan: whether a system STOP must
   reach active tasks outside this Manager's child table (detached tasks
   launched by an earlier Manager), since the owner's rule says "any
   active tasks".

## Proposed Theory Delta (drafted)

Drafted by the Slice B implementation pass on 2026-11-02 from the plan's §4c outline. This is the review target for the cross-family round. Nothing below is applied to `docs/program-theory.md` until task 13.

Block format (realigned 2026-11-02 to the hub gate at `e6ac579`): every
ready-to-paste block in this appendix is a label line that reads exactly
``Replacement (`docs/program-theory.md`):`` at column zero, immediately
followed by a fenced block (```` ```markdown ````, or `~~~markdown` where
the block itself contains a triple-backtick fence). Each block holds a
verbatim span that will exist in `docs/program-theory.md` after task 13,
so the ported `bin/check-doc-paths` gate (task 6a) can check containment.
Spans that change text inside a single table row are not blocks; the
altitude-pass table locates them by span. Working tables (the [WT-4]
whole-shape rows, the evidence and rationale tables) carry no label. The
gate checks these blocks once the adapted port lands (fork 3, decided adapt on 2026-11-02) and,
once the index row reads `promoted`.

### [WT-0] Verification line (Slice B, §4c item 1)

Replaces the two header lines that open "Verification: Owner review and one
independent semantic review." and end "for each release or each class-5
plan." Drafted 2026-11-02 after round 3 (F10) so the audit-table definition
lands in the same edit as the staged [WT-7] bullet that cites it.

Replacement (`docs/program-theory.md`):
```markdown
Verification: Four measures, each named for what it measures. Owner
endorsement: the owner can defend each sentence of [WT-2a], [WT-4], [WT-5],
and [WT-6] as their own, confirmed at adoption and at each [REV-WT-*]
record; endorsement is not a demonstration of possession. Transfer probe:
at each release or class-5 completion, a possession probe with an
owner-written key (the feature to place, the category error to refuse, and
the predicted bug class are fixed before the probe runs), executed by an
agent that did not author the plan and graded against the key; on FAIL,
classify after disposition as document deficiency, key fault, or agent
reasoning, and record a document deficiency only when the evidence supports
it. Transfer log: a review proposal of a [WT-6] alternative that does not
cite the entry's recorded reopening condition is logged against that entry
(see [WT-7]). Theory-versus-code audit table: at each release or class-5
completion, the table that maps each [WT-2a] invariant and [WT-4] principle
to its contract and its evidence in the tree as aligned, drifted (naming
the plan that closes it), or theory revision; unchanged rows carry forward
by reference and touched claims are re-audited. The table measures
document-versus-tree agreement, not possession.
```

### Slice A propagated text

Plan: `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`, Slice A
(tasks 2–6), applied at task 13. Source pin: agent-theory `e6ac579` ("Update
agent theory to better define roles"); base `296a7cd`. Every hub sentence below
was read with `git -C ../agent-theory show e6ac579:<path>`. Nothing in the
Weft repository was edited; nothing was committed.

Path adaptations applied (the only edits to hub text): docs/specs/ →
`docs/specifications/`; `[AT-REF-2]`, `[AT-REF-3]`, `[AT-REF-4]` (primer
pointers) → `[WT-0]`; bare `[AT-THEORY-6]` → `[WT-7]`; `[REV-AT-006]` →
`[REV-WT-004]`; `[REV-*]` → `[REV-WT-*]`; `MODULE-THEORY.md` unchanged.
References the hub sentence itself attributes to "the `agent-theory` hub's ..."
(`[AT-THEORY-2.1]`, `[AT-THEORY-6]`, `[AT-THEORY-8]`) are left as hub citations,
because they are true cross-repository references. `[REV-AT-003]` and
`[REV-AT-005]` stay, as the current Weft skill already cites them. Line wrapping
was re-flowed only where Weft indentation differs (the AGENTS.md read-order item);
two trailing-whitespace characters in hub `[AT-THEORY-6]` were dropped.

Dry run: every Replacement block below was applied to a scratch copy of the
Weft tree (`work/weft-tree/`). Each block is contained verbatim in its target
after application, and both the current and the extended `bin/check-doc-paths`
exit 0 on that copy (see "Verification run"). `work/` and `<scratch>` in
this text name the dry run's session scratch directory; the dry-run artifacts
are session scratch and are not repository files.

#### Slice A propagated text — docs/program-theory.md

##### a. [WT-0] directional rules (inserted after the owner's rule)

Span replaced: the paragraph from "The owner stated the rule governing the first
two rows on 2026-10-09" to "the theory does not bend to ratify an accidental or
flawed implementation." That paragraph is kept verbatim as the first paragraph
of the block, and the new text follows it directly (before the `[^naur]`
footnote definition).

Adaptation note: the hub `[AT-THEORY-0]` lead sentence ("The primer
(`[AT-REF-3]`) defines the directional triad across documents (*...*).") points
at the primer, which Weft does not have. Following the primer → `[WT-0]` rule, the
primer's triad (hub spec 02 §"Document surfaces", the three bullets including
the clause that a spec leads the code only across an active plan's
spec-promotion slice or when a bug violates the contract) is placed inline. The
hub sentence's second clause, "Four rules to help keep things aligned
(`[REV-AT-006]`):", follows verbatim. The spec 02 parenthetical "(see
`[AT-THEORY-0]` in `docs/program-theory.md`)" is dropped because inside [WT-0] it would point at itself.

Replacement (`docs/program-theory.md`):
```markdown
The owner stated the rule governing the first two rows on 2026-10-09: "The
specs describe what we have. The theory describes what we are building toward.
We are not trying to reify bad decisions." If a specification contradicts the
theory, the specification is revised through a planned change; the theory does
not bend to ratify an accidental or flawed implementation.

Three directional rules govern how program theory, specs, and plans interact:

- **The program theory is the ideal** — what the software is aiming to be.
- **Specs describe what is currently in the code** — the concrete behavioral
  contract for the current implementation (not an unbuilt wishlist; a spec leads
  the code only across an active plan's spec-promotion slice or when a bug
  violates the contract).
- **Plans morph the code (and the specs) closer to the ideal.**

Four rules to help keep things aligned (`[REV-WT-004]`):

1. **If the specification or code is pulling away from the theory, the
   presumption is that the specs or code are not properly moored in the
   theory.** If the code and the spec disagree, then the presumption is that the
   code has a bug. When a spec clause itself is stale or when experience
   building and operating the code suggests a better path, then use a plan to
   fix the spec itself.
2. **Never mistake a current spec or plan for "what has been decided" and try
   to justify it when it falls short of the theory.** Everything—including the
   theory itself (`[REV-WT-*]`)—can be revised as implementation and experience
   teach us more.
3. **Respecting active contracts during an edit is about change order, not
   permanence.** Do not silently break an active spec clause mid-edit; use a
   plan (and its spec-promotion slice) to move the spec and code toward the
   theory.
4. **Process exists to keep theory and specs aligned while working at agent
   speed.** Any process rule that does not improve orientation, error detection,
   or human understanding should be cut.
```

##### a. [WT-0] surfaces table (two rows revised)

Span replaced: the whole table from "| Surface | Question it answers |" to the
"| Lessons (`docs/lessons.md`) |" row. Only the Program theory and Plans answer
cells change. Both take the hub spec 02 `[AT-REF-3]` wording verbatim. The
first-column labels stay as Weft has them. The block holds the full table so
that the containment check covers both rows in context.

Replacement (`docs/program-theory.md`):
```markdown
| Surface | Question it answers |
|---------|---------------------|
| Program theory (this file) | What is the conceptual **ideal** of this system or module? What is its feel/idiom, core composable operations, and **identity invariants** (the choices that shape the entire program)? Why do these concepts, boundaries, non-goals, and discarded paths exist? |
| Specifications (`docs/specifications/`) | What is the exact intended behavior now? |
| Plans (`docs/plans/`) | How are we morphing the code and specs closer to the program theory, and which alternatives were rejected or revised? |
| Code and tests | How is the behavior realized, and what executable evidence fires against each claim? |
| Lessons (`docs/lessons.md`) | What durable rule did a surprise or repeated failure establish? |
```

##### b. [WT-7] possession-probe habit (audit-table clause only)

Span replaced: the bullet from "- **Possession probe:** One time for each
release or class-5 plan" to "A failed probe means the account or its possession
has drifted." Only one sentence is added: the hub habit clause ("refresh the
`[AT-REF-2]` **theory-versus-code audit table** at each release or Class-5
completion"), adapted to `[WT-0]`. Plan 1 task 16 rewrites the rest.
Note: [WT-0] does not yet define the audit table. Slice B/C must add that
definition at task 13, or the citation points at nothing.

Superseded at task 16 (the span was applied at task 13 and then rewritten; the current text is under "Task 16"), former Replacement (`docs/program-theory.md`):
```markdown
- **Possession probe:** One time for each release or class-5 plan, ask an
  agent to place a hypothetical feature, refuse a category error, and
  predict the class of bug an audit will find. Record the result in the
  plan or in the lessons. Refresh the `[WT-0]` **theory-versus-code audit
  table** at each release or Class-5 completion. A failed probe means the
  account or its possession has drifted.
```

##### b. [WT-7] two new falsifiers (last two bullets)

Inserted after the current last [WT-7] bullet, which ends "consume-on-dispatch
buckets) while keeping one durable source of truth.", and before
`## Founding continuity [WT-8]`. The text is hub `[AT-THEORY-6]` wording verbatim
with `[REV-AT-006]` → `[REV-WT-004]`. Cross-references to "Blunt local mechanism"
and "Repeated rejected proposals" (plan §4c item 7) are not hub text and are not
added. If they are wanted, they are Slice B drafting.

Replacement (`docs/program-theory.md`):
```markdown
- **Altitude collapse (unfalsifiable aphorisms or implementation lock-in):** A
  written program theory fails when its claims sit at the wrong level of
  abstraction (`[REV-WT-004]`):
  - *Too abstract (aphorism list):* If the file reads like generic engineering
    proverbs without concrete concept ownership, identity invariants, composable
    operations, or discarded paths—so that two incompatible designs can both
    cite a sentence as support—it cannot place features or refuse bad fits.
  - *Too concrete (implementation lock-in):* If an internal refactor that
    preserves the product's problem, feel, boundaries, and identity invariants
    repeatedly invalidates `docs/program-theory.md`, the file has drifted into
    an implementation note or behavioral spec.
- **Module theory used to excuse a divergent subsystem:** Reaching for a
  `MODULE-THEORY.md` can be a symptom that a package has invented its own
  competing concepts, command grammar, or lifecycle rules instead of composing
  from the product's core operations (`[REV-WT-004]`). Before granting a
  subsystem its own theory file, check whether the subsystem should instead be
  simplified back into the product's model; a module theory is warranted only
  when local depth genuinely specializes the product theory rather than carving
  out an exemption from it.
  Extending the project theory to include one or more module theories is
  ultimately the human's decision. Module theories should not be used to justify
  variances from the program theory when one theory really should apply to the
  whole program. Module theories should only be used when a specific module is
  weakly coupled to the rest of the program and has theory-level invariants that
  should only apply to that module.
```

##### c. [WT-9] `[REV-WT-004]` record stub

Inserted after the last line of `[REV-WT-003]` ("primitives and
auto-disappearing SimpleBroker queues") and before `## Sources`. The shape
follows `[REV-WT-003]`: Current account / Supersedes / Pressure / Evidence.
Weft uses "Pressure:", where the hub's "Why it changed (pressure)" label differs. This record is
Weft text, not hub text. The Slice B lines are marked "filled at task 17".

Superseded at task 16 (the span was applied at task 13 and then rewritten; the current text is under "Task 16"), former Replacement (`docs/program-theory.md`):
```markdown
### [REV-WT-004] Four-way balance, directional triad, and module-divergence falsifier

- Current account: [WT-0] states the directional triad (the program theory is
  the ideal; specs describe what is currently in the code; plans morph the
  code and specs toward the ideal) and four alignment rules beside the
  owner's 2026-10-09 rule. [WT-7] adds the "Altitude collapse" and "Module
  theory used to excuse a divergent subsystem" falsifiers, and the possession
  probe refreshes the theory-versus-code audit table. This part was
  propagated from agent-theory @ `e6ac579` (`[REV-AT-006]`) by
  `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` Slice A.
  Slice B account (identity invariants, the [WT-4] whole-shape reconciliation, [WT-6] kinds, the
  Verification line): filled at task 17.
- Supersedes: A surfaces table in which plans answered "What change was made
  or proposed", a read-order entry saying the theory "does not override
  winning contracts", and a module-theory rule that admitted module theory
  whenever depth was local. Slice B supersessions: filled at task 17.
- Pressure: The hub's `[REV-AT-006]`, whose pressure came from Weft's
  2026-10-29..2026-11-01 theory crystallization and audit, where "theory
  never overrides specs" was used to defend existing specs against the
  theory. Slice B pressure: filled at task 17.
- Evidence:
  - contemporaneous: `../agent-theory/docs/program-theory.md` `[REV-AT-006]`
    and `../agent-theory/docs/specs/02-agent-theory-and-program-theory.md`
    at `e6ac579`
  - contemporaneous: `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`
    Slice A
  - Slice B and Slice C evidence (audit table, review log): filled at task 17.
```

#### Slice A propagated text — skills/crystallize-program-theory/SKILL.md

Span replaced: the whole file. The text is the hub skill at `e6ac579`,
path-adapted. The adaptation diff against the hub file is in "Verification run"
(`skill-vs-hub.diff`). The outer fence is `~~~` because the skill contains triple-backtick
blocks.

Weft-specific lines kept:
- the `Status:` provenance line, extended with the `e6ac579` /
  `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` step;
- Governing References: all four Weft bullets (`docs/program-theory.md`,
  `docs/agent-context/decision-hierarchy.md`,
  `docs/agent-context/runbooks/writing-plans.md`,
  `docs/specifications/README.md`), followed by the hub's "Hub meta-theory"
  bullet;
- Blast Radius: all three Weft bullets (`[WT-*]`/`[ALT-WT-*]` sections; module
  docstrings / local `README.md` theory notes in `weft/`; `docs/specifications/`
  spec deltas), placed after the three hub bullets.
- Retained Weft sections, to be merged at task 13 into the hub structure in
  the block below (decided 2026-11-02; §4a deviation row for this file).
  They are Weft process content with no hub conflict. Current Weft headings
  and lead lines:
  - `## When To Use`: the bullet "During an authorized coalescing sweep when
    cold lessons or retired plans carry durable architectural rationale ..."
    and the bullet "Do NOT use mid-task without authorization ... surface
    `RECONSIDERATION REQUESTED` for human decision first."
  - `## Workflow` step 3: "**Apply the four-part admission test to refused
    alternatives (`[ALT-WT-*]`).**" (likely recurrence; material
    investigation cost; hidden constraint exposed; harm from blind retry).
  - `## Workflow` step 5: "**Verify identifiers and run gates.**"
    (`bin/check-doc-paths` and `tests/specs/test_spec_hygiene.py`).
  - `## Output Standard`: the three Weft bullets (concise, code- and
    spec-grounded `[WT-*]` or `[ALT-WT-*]` entries; mechanism and reopening
    condition; paths and spec references pass `bin/check-doc-paths` and
    `tests/specs/`).
  Because the merged sections sit inside the hub structure, the block below
  is not contiguous in the merged file. At task 13 it is rewritten to the
  merged file text (or split at the merge points) so that every block stays
  contained in the target.

Hub lines with no Weft counterpart (omitted or trimmed):
- "Definitional primer: `../agent-theory/docs/specs/02-agent-theory-and-program-theory.md`".
  Weft has no primer; the Weft `docs/program-theory.md` bullet replaces it.
- "Operating model: `../agent-theory/docs/specs/01-development-documentation-operating-model.md`".
  Weft has no DOM spec; `decision-hierarchy.md` is the Weft carrier.
- "Spec writing: `../agent-theory/docs/agent-context/runbooks/writing-specs.md`". Weft has no
  writing-specs runbook, and keeping the line would fail `bin/check-doc-paths`.
- When Not To Use: " / writing-specs runbook" trimmed from "(use specs /
  writing-specs runbook)".
- §3 table: "or implementation doc (docs/implementation/)" omitted. Weft has
  no docs/implementation/, and the backticked path would fail
  `bin/check-doc-paths`, since `skills/` is scanned.

Weft-specific content in the current skill that the hub structure replaces:
Read First entries 2–4; Workflow steps 1, 2, and 4; and both Weft Maintenance
Notes. The second Maintenance Note ("add a module-theory header") is the one
plan §4c says to replace. The admission test, the gate-run step, the two When
To Use bullets, and the Weft Output Standard are retained (list above), so no
§9 fork is needed for them.

Plan task 2 verify strings, checked against this staged file: `theory-level`,
`theory-versus-code`, and `weakly coupled` each return at least one hit. (The
earlier strings `Identity invariants` and `escaped the product's law` returned
0 hits against the hub at `e6ac579` and were replaced in task 2 on
2026-11-02.)

Replacement (`skills/crystallize-program-theory/SKILL.md`):
~~~markdown
# Crystallize Program Theory

Status: Active — adopted from agent-theory @ `8f41a9d`, updated to
`296a7cd` via `docs/plans/2026-10-14-agent-theory-delta-wave-plan.md`, and
updated to `e6ac579` via
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md`.

## Purpose

Interview the product owner relentlessly until a theory file can be written:

- **Product theory** — `docs/program-theory.md` (apex shared language), or
- **Module theory** — a human-decided **extension** of that model for one
  weakly coupled module that has theory-level invariants applying only to that
  module (typical path: `<module>/MODULE-THEORY.md`).

Module theory is not a lesser document, and it is not a routine split for every
subsystem: use it only when the human owner decides a module is weakly coupled
to the rest of the program and carries module-only theory-level invariants. See
`docs/program-theory.md` `[WT-0]` (and the
`agent-theory` hub's `[AT-THEORY-2.1]` and `[AT-THEORY-6]`).

The result is a transfer surface for agents and humans—not a requirements dump
and not a copy of the agent-theory hub's own theory.

Inspired by interview-style “grill” workflows: one decision at a time, owner
owns the answers, environment facts are looked up rather than asked.

## When To Use

- A consumer repo still has the bootstrap **program-theory stub** (product).
- Product identity is only in chat (“X-ness”) or a long README residual.
- Agents repeatedly propose category errors or re-litigate non-goals.
- **Extending the model (human decision):** a module is weakly coupled to the
  rest of the program and has theory-level invariants, ownership boundaries, or
  non-goals that apply only to that module—write **module theory** instead of
  bulking the root file.
- Before class-5 identity work without a written conceptual account.
- During an authorized coalescing sweep when cold lessons or retired plans carry
  durable architectural rationale that belongs in the program theory rather than
  a process rule.
- Do NOT use mid-task without authorization when the edit would change normative
  product scope or reverse a standing refused alternative (`[ALT-WT-*]`): surface
  `RECONSIDERATION REQUESTED` for human decision first.

## When Not To Use

- Editing the **`agent-theory` hub's** meta-theory of agent-theory itself (edit
  the hub's `docs/program-theory.md` under normal review rather than running a
  product-owner interview).
- Pure process changes with no product-identity impact.
- Writing exact behavioral contracts (use specs).

## Governing References

- `docs/program-theory.md` (this repository's canonical program theory)
- `docs/agent-context/decision-hierarchy.md` (authority order, `[DOM-15]`
  classification, and preflight check)
- `docs/agent-context/runbooks/writing-plans.md` (§4b–4d for spec-changing work)
- `docs/specifications/README.md` (specification corpus structure and traceability)
- Hub meta-theory (in the `agent-theory` hub repository): `[AT-THEORY-0]`–`[AT-THEORY-8]`

## Read First

1. Existing `docs/program-theory.md` (hub account, stub, or draft).
2. Product entry (README, agent kernel if any, product-context if any).
3. Any module path the user named for subsystem theory.
4. Recent plans or lessons that show identity friction.

## Blast Radius

- Creates or replaces `docs/program-theory.md` (product) or a module
  `MODULE-THEORY.md` / agreed path.
- May require AGENTS.md / `context.index.yaml` read-order updates so theory
  loads at the right scope.
- Does **not** silently change product specs; identity changes may imply
  later class-5 contract work.
- `docs/program-theory.md` (`[WT-*]` and `[ALT-WT-*]` sections)
- module-level docstrings or local `README.md` theory notes in `weft/` when a
  subsystem boundary needs progressive-disclosure context
- `docs/specifications/` when crystallizing the theory reveals an undocumented
  product contract that requires a spec delta

## Workflow

### 1. Frame

State that:

- Program theory is Naur-style working understanding externalized as a
  **current account**.
- Possession stays practical; the file is a transfer surface.
- Hub agent-theory text must not stand as product identity.
- **Module theory is how we extend the product model** when depth is local
  (`[WT-0]` / hub `[AT-THEORY-2.1]`)—not optional color commentary.
- You will ask **one question at a time**, give a **recommended answer**
  when useful, and wait for the owner.

Decide **scope up front** with the owner: product apex, module extension, or
both (thin product + module). Do not draft the full document until shared
understanding on the spine—or they explicitly ask for a draft from answers so far.

### 2. Interview (one question at a time)

This skill owns the interview spine (referenced by the `agent-theory` hub's
`[AT-THEORY-8]`). For each item:

1. Ask the question.
2. Offer a short recommended direction grounded in repo evidence when possible.
3. Wait for the owner's decision.
4. Record the answer; note open dependencies.
5. Only then proceed to the next question.

If a **fact** is in the repo, look it up. **Decisions** stay with the owner.

Walk branches: if non-goals depend on topology ownership, resolve topology
before finalizing non-goals.

#### Spine questions

1. **Problem world** — What affairs of the world does this program handle?
   For whom? What fails if it does not exist?
2. **Desired feel & compositional idiom** — What should using and extending it
   feel like? What are the core composable operations new work must build on
   rather than inventing side doors?
3. **Whole-program metaphor** — In three to seven bullets, what is the
   mental model (not the file tree)?
4. **Core concepts** — Name each concept, its meaning, and its **owner**
   (this product / a module / the user / another system).
5. **Matching surface & taste anchors** — How do CLI, API, UI, or docs express
   the same model without inventing a second product? What is the command/API
   grammar (e.g., top-level reflex verbs vs. `noun verb`, projected domain
   nouns, concrete taste-anchor examples)?
6. **Negative knowledge (two parts)** —
   - *What the system is not:* What will competent people propose that you will
     refuse on boundary or identity grounds, and why?
   - *Tried and discarded paths:* What plausible designs or fixes were already
     tried (or investigated) and discarded, and why?
   - For both, what observable condition would make you reconsider?
7. **Generative rule** — Is there one question that organizes a large part
   of the design (as control locality might organize a taxonomy)?
8. **Identity (theory-level) invariants & delivery / safety honesty** — Which
   invariants and architectural choices are core to the identity of the product
   and shape the entire program—and why do the boundaries sit where they
   do—such that if they changed, the program would start to become something
   else, and if the internal implementation were replaced tomorrow, the new
   implementation would still be measured against them? Where can work be lost,
   duplicated, or weakly authenticated? Name the guarantee narrowly.
9. **Tensions** — What live tensions would falsify the account if they
   worsened?
10. **Extension by module theory (human decision)** — Is any module weakly
    coupled enough to the rest of the program, with theory-level invariants
    that apply only to that module, to warrant its own `MODULE-THEORY.md`? Which
    path owns that file?
11. **Possession test** — What surprises (bugs or bad agent paths) would
    force a theory revision rather than a local patch?
12. **Replacement check** — If this were only a README restatement, what
    judgment would still be missing?

Stop when the owner can **place a feature**, **refuse a category error**,
and **predict a failure mode** without re-deriving the product from scratch.

### 3. Choose extension shape

| Situation | Action |
|-----------|--------|
| No product theory yet | Crystallize **product** `docs/program-theory.md` first |
| Product theory exists; a weakly coupled module has module-only theory-level invariants and the human owner wants a module theory | **Extend** with module theory; do not bulk product theory |
| Answers mix product-wide theory and a weakly coupled module's own theory | Split (with human owner agreement): thin product apex + module file(s) |
| Content is routine subsystem depth or exact rules only | Stop; write or update a **contract** (`docs/specifications/`), not theory |

Extending a project theory with a module theory is ultimately the human's
decision (`[WT-7]`). Module theories must not be used to justify
variances from the program theory when one theory should apply to the whole
program; use a module theory only when a specific module is weakly coupled to
the rest of the program and has theory-level invariants that apply only to that
module. When a module theory is genuinely warranted, require: ownership table,
generative model or equivalent, local non-goals, required action on entry, “no
sibling module theory required,” and promotion path for any product-scope ALT
parked there.

### 4. Draft

**Product** — write `docs/program-theory.md` with at least:

- Status / Owner / Boundary / Verification / Required action
- What program theory means (short; link Naur)
- Purpose and desired feel; whole-program mental model
- Core concepts, ownership, and surface idiom / taste anchors (command or API
  grammar + composable operations to build on — *taste up front*, while leaving
  internal storage schemas to vertical implementation slices; `[REV-AT-005]`)
- Identity (theory-level) invariants — the choices that shape the entire program
  (general enough that changing implementations remain measurable against them,
  specific enough to beat aphorisms; `[REV-WT-004]`)
- Principles; two-part negative knowledge (what the system is not, and paths
  tried and discarded); falsifiers
- How the model is extended (pointer to module theory convention)
- Revisions/ALT if decided; related contracts

Choose references on two tests: **accurately represented** and
**load-bearing**. A load-bearing reference contributes a concept,
distinction, refusal, or vocabulary the theory actually uses. Make
that contribution explicit where it would otherwise be unclear; omit
ornamental citations. Treat any register effect as a drafting
hypothesis, never as evidence that the theory or its outputs are
correct ([REV-AT-003]).

**Module** — write `<module>/MODULE-THEORY.md` (or agreed path) with at least:

- Status / Owner / Boundary / Verification / Required action (“read on entry
  to … before changing …”)
- Specializes: product program theory (link)
- What this document is / is not (contracts vs theory)
- Ownership table (owner / consumer / propagator)
- What the module owns; what it is not
- Generative model or design consequences
- Tensions and falsifiers; REV/ALT records
- Related contracts; promotion notes for product-scope items

Use stable section codes if the repo already uses them (`[THEORY-n]`,
`[MODULE-n]`, etc.).

### 5. Wire entry (if missing)

- **Product:** theory early in `context.index.yaml` `read_order` for
  product-scope repository work.
- **Module:** document entry in product theory and/or AGENTS (“when touching
  `path/…`, read `…/MODULE-THEORY.md`”). **Do not** put every module theory
  on global session start.

Do not force full process read order onto product-use-only paths.

### 6. On refuse or revise — write a decision record

If the interview **refuses or defers** a competent proposal, or **changes** the
working model, append a filled ALT or REV using the field templates in this
skill. Do this **when it happens**, not as a quota of empty records.

**Apply the four-part admission test to refused alternatives (`[ALT-WT-*]`).**
Record a rejected or refused alternative when at least one holds:

- **Likely recurrence:** future sessions or reviewers are likely to propose it
  again as an "obvious" simplification or feature.
- **Material investigation cost:** re-evaluating why it fails costs significant
  time or experiment budget.
- **Hidden constraint exposed:** the refusal rests on a non-obvious invariant
  (for example, spawn-only multiprocess safety, queue-truth vs auxiliary cache,
  or SQLite/Windows handle lifetime).
- **Harm from blind retry:** trying the alternative silently corrupts state,
  weakens an invariant, or pulls Weft above the substrate layer.

For each admitted `[ALT-WT-*]` entry, state what was refused, the concrete
mechanism that makes it wrong here, and what condition (if any) would justify
reopening it.

Do not create empty `[ALT-001]` placeholders or decision-case directories.

### 7. Confirm

Ask the owner to test possession:

- Place one hypothetical feature.
- Refuse one category error.
- Name one surprise that would revise the theory.

For a **product apex**, also walk the fuller owner's checklist — the nine
understandings the founding dialogue named (origin: the hub's lore record
of the 2026-07-29 backstitch README dialogue). The owner should be able to
state, without reading the draft back:

1. what concepts exist;
2. what each concept means;
3. which component owns each behavior;
4. why the boundaries are where they are;
5. which alternatives were rejected and why;
6. what must remain invariant;
7. what the system explicitly does not do;
8. how a proposed change propagates through the rest of the model;
9. what evidence would show the current understanding is wrong.

Any item the owner cannot answer marks a section that is not yet theory:
interview further there, or keep the account's Status at `Draft` and record
the gap. Verify that Q6 (refusals and discarded paths) and Q8 (identity /
theory-level invariants—the choices that shape the entire program across
re-implementation) produced concrete answers for items 4, 5, and 6 rather than
aphorisms or implementation-only mechanics; if either is thin, probe it
explicitly before marking the account `Active`.

When crystallizing or revising theory for a repository that already has
shipping code or specs—and again at each release or Class-5 completion alongside
the `[WT-7]` possession check—the product owner (or authoring agent for
owner confirmation) produces or refreshes a **theory-versus-code (and
theory-versus-spec) audit table** in the plan or `docs/lessons.md`: map each
theory claim and identity invariant against current specs and code (carrying
forward unchanged rows by reference on subsequent refreshes), marking each row
as aligned, drifted (to be morphed via a plan), or evidence that the theory
itself needs revision (`[WT-0]`).

Revise the draft until they confirm.

### 8. Verify identifiers and run gates

Check that every cited file path, symbol, spec code, and `[WT-*]` /
`[ALT-WT-*]` identifier resolves, and run `bin/check-doc-paths` and
`tests/specs/test_spec_hygiene.py`.

## Decision record templates

### ALT (rejected / deferred / …)

```markdown
### [ALT-<SCOPE>-<NNN>] Short title

Disposition: rejected | deferred | adopted | superseded | invalidated
Owner: <decision owner>
Governs: <theory section, module theory, or contract reference>
Source record: none | <plan path> | <commit or issue>
Candidate: <what was proposed>
Why plausible: <steelman>
Evidence:
- contemporaneous | owner-recalled | inferred | unknown: <source>
Reason: <why this disposition>
Current consequence: <what work must do now>
Reconsider when: <observable condition — not vibes>
Promoted to: none | <id if moved upstairs or into a contract>
```

### REV (theory change)

```markdown
### [REV-<SCOPE>-<NNN>] Short title

Current account: <revised theory — put current first>
Supersedes: <short prior account; do not let it compete with current>
Pressure: <what made the prior account inadequate>
Evidence:
- contemporaneous | owner-recalled | inferred | unknown: <source>
```

## Output Standard

- Written theory file at the agreed path.
- Explicit non-goals and ownership table.
- Entry-order note or patch so the file is loadable by agents.
- Any **real** ALT/REV from the interview filled in full; zero empty slots.
- List of follow-ups (spec promotions, parked ALTs) without implementing them
  unless asked.
- `docs/program-theory.md` updated with concise, code- and spec-grounded `[WT-*]`
  or `[ALT-WT-*]` entries
- Any new or revised refused alternative states its mechanism and reopening
  condition
- All backticked repository paths and spec references pass `bin/check-doc-paths`
  and `tests/specs/`

## Anti-patterns

- Copying the hub's agent-theory program-theory into a product repo as-is.
- Stuffing module depth into product theory “so agents see it,” instead of
  **extending** with module theory and entry wiring.
- Writing module theory that requires three sibling theories to place work
  (broken ownership table).
- Turning the interview into a full architecture dump or API catalog.
- Asking multiple independent questions in one turn.
- Acting on implementation before shared understanding is confirmed.
- Treating the written file as proof of possession without the placement/refusal
  test.
- Pre-creating empty ALT/REV records or decision directories “for later.”

## Module-theory drafting checklist

When extending via module theory, require:

1. File next to owning code (conventional `MODULE-THEORY.md`) or an agreed path
   documented from product theory / AGENTS.
2. Metadata: Status, Owner, Boundary, Verification, Required action
   (“read on entry to … before changing …”).
3. Ownership table (owner / consumer / propagator). Two modules claiming
   owner of the same concept is a gate failure.
4. Generative model or design consequences; local non-goals.
5. Point exact rules at contracts/fixtures; do not duplicate them as theory.
6. Local ALT/REV when real; park product-scope ALTs for promotion.
7. Wire entry: agents working in that tree read module theory **after** product
   theory (or overview) and **before** changing the module — not on every
   global session start.
8. Actionable without reading sibling modules.

## Maintenance Notes

- This skill is the sole home for the interview spine, ALT/REV field templates,
  module drafting checklist, and entry-wiring procedure ([AT-THEORY-8] by
  reference). Do not re-expand those into hub `docs/program-theory.md`.
- If interviews always need the same extra domain questions, promote them into
  this skill.
- If consumers keep shipping without replacing the stub, strengthen bootstrap
  messaging and AGENTS required-action language.
~~~

#### Slice A propagated text — docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md

Span replaced: lines 196–201, from "Round-2 variant (after dispositions — never
before):" to "they are closed by their disposition rows. Verdict: PASS / FAIL."
The text is hub `e6ac579` wording verbatim. Note: the plan task 3 verify
(`grep 'never to re-check non-blocking edits'`) returns 0 against hub wording,
because the hub wraps the phrase across lines ("never to re-check" / "non-blocking
edits"). `grep -c 'never to re-check'` returns 1 on the dry-run copy.

Replacement (`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`):
```markdown
Round-2 variant (only when Round 1 returned `BLOCKED` / `blocker: F<ids>` or
when scope/invariants/authority changed after review — never to re-check
non-blocking edits after `PASS` or `no blocker`; the author verifies non-blocking
edits locally and runs `bin/check-doc-paths` to confirm promoted `Replacement:`
fenced blocks still match their target files):

> Round-2 verification, scoped ONLY to these accepted blocking findings and
> their fixes: [IDs, one line each]. Verify each fix; report any NEW defect the
> fixes introduced. Do not revisit declined or out-of-scope findings —
> they are closed by their disposition rows. Verdict: PASS / FAIL.
```

#### Slice A propagated text — docs/agent-context/runbooks/writing-plans.md

Span replaced: the §4c bullet "- do not treat plan-only text as a second
governing contract after promotion — once promoted, `docs/specifications/` is
canonical" (lines 376–377). The hub extension clause is appended after
"is canonical", with Weft's existing `docs/specifications/` adaptation kept in
place of the hub's "the spec tree". Caveat: Weft's status taxonomy has only
`draft` and `completed`, and the gate skips both, so in Weft the check this
bullet describes never fires (see the bin/check-doc-paths section and §9 fork 3).

Replacement (`docs/agent-context/runbooks/writing-plans.md`):
```markdown
- do not treat plan-only text as a second governing contract after
  promotion — once promoted, `docs/specifications/` is canonical; in active
  plans that have promoted their delta (recorded a non-pending `Promotion
  Baseline Identifier` or `promoted` status; lifecycle status read from
  `docs/plans/README.md`), any `Replacement (`<path>`):` (or heading-scoped
  `Replacement:`) fenced block is checked by `bin/check-doc-paths` for text
  containment in the target file so the plan's fenced delta and the promoted
  target file do not drift apart during review dispositions
```

#### Slice A propagated text — CLAUDE.md and AGENTS.md

`CLAUDE.md` is a symlink to `AGENTS.md` (`CLAUDE.md -> AGENTS.md`), so one edit to
`AGENTS.md` gives both paths byte-identical text. The plan's task-4 awk diff is
empty on the dry-run copy. Apply the edit to `AGENTS.md` only. Editing both would
apply it twice through the link.

##### Read-order item 1

Span replaced: only the clause "frames interpretation and placement, and does
not override winning contracts" (lines 40–41). The Weft lead ("the conceptual
account of Weft ("weftness") and its rejected alternatives") is kept unchanged.
The clause becomes the hub `AGENTS.md` clause for item 1 at `e6ac579` (frames
interpretation, placement, and the direction in which plans move specs and
code; do not silently break active contracts mid-edit; do not treat an existing
spec or plan as final when it pulls away from the theory), with `[AT-REF-3]` →
`[WT-0]`. The hub's lead ("conceptual identity of **this repository**"), its
Stub sentences, and its Class-5 sentence are not propagated (see "Not
propagated"). The block holds the whole item so that the containment check
covers the swapped clause in context.

Replacement (`AGENTS.md`):
```markdown
  1. `docs/program-theory.md` — the conceptual account of Weft
     ("weftness") and its rejected alternatives; frames interpretation,
     placement, and the direction in which plans move specs and code
     (`[WT-0]`; do **not** silently break active contracts mid-edit, and do
     **not** treat an existing spec or plan as final when it pulls away from
     the theory)
```

##### **Module theory** paragraph

Span replaced: lines 55–63, from "**Module theory** is how this repository's
conceptual model is **extended**" to "module theory instead
(`skills/crystallize-program-theory/SKILL.md`)." The text is hub `AGENTS.md`
wording with docs/specs/ → `docs/specifications/`. Two parts are omitted or
replaced: "or docs/implementation/", because Weft has none and the gate scans
`AGENTS.md`; and the hub's "Rules and checklist: ..." tail, whose two targets
are hub docs/specs/ files, replaced by Weft's existing
`(skills/crystallize-program-theory/SKILL.md)` pointer. The plan §4c wording
("only after confirming the subsystem genuinely specializes the product's model
rather than escaping its law" plus a [WT-7] pointer) does not exist in the hub.
The hub clause staged here is "when a human owner decides a weakly coupled
module has theory-level invariants ... not an excuse for a module to diverge
from a single whole-program theory".

Replacement (`AGENTS.md`):
```markdown
**Module theory** is how this repository's conceptual model is **extended**
when a human owner decides a weakly coupled module has theory-level invariants
that apply only to that module (not a second global constitution, and not an
excuse for a module to diverge from a single whole-program theory):

- On **entry** to a module that has theory (conventional name
  `MODULE-THEORY.md` next to that code, or a path named from product theory),
  read it before changing that module's public shape, rules, or ownership.
- Do **not** load every module theory at session start.
- Do **not** bulk module-only depth into `docs/program-theory.md`; put routine
  subsystem depth in `docs/specifications/`, and use module theory only by
  human decision for weakly coupled modules with module-only theory-level
  invariants (`skills/crystallize-program-theory/SKILL.md`).
```

#### Slice A propagated text — docs/agent-context/README.md

##### Read-order item 1

Span replaced: only the clause "frames placement and refusal, and does not
override winning contracts" (lines 25–26). The same clause swap as `AGENTS.md`
read-order item 1, with `[AT-REF-3]` → `[WT-0]`. The Weft lead and the closing
module-theory sentence are kept unchanged. This file is outside the hub
delta; it is staged because task 4 carries the swap (decided 2026-11-02).

Replacement (`docs/agent-context/README.md`):
```markdown
1. `../program-theory.md` — the conceptual account of Weft ("weftness"),
   including rejected alternatives; frames interpretation, placement, and the
   direction in which plans move specs and code (`[WT-0]`; do **not** silently
   break active contracts mid-edit, and do **not** treat an existing spec or
   plan as final when it pulls away from the theory). Extend local depth with
   **module theory** on entry, not by bulking this file.
```

#### Slice A propagated text — bin/check-doc-paths

Produced by applying the hub hunks to a scratch copy of Weft's gate
(`work/check-doc-paths`). The changes: the regex constants inserted before
`NON_CLAIM`; `_load_plan_index_statuses`, `_is_plan_promoted`, and
`_plan_replacement_sync_failures` inserted before `check_tree`; the
`check_tree` call site plus the "failure(s)" message; the `plan_probes`
self-test block with docs/specs/ → `docs/specifications/` and hub `build(` →
Weft `_build_probe_tree(`; and the new self-test success line. Weft's
`SCAN_DIRS`/`SCAN_FILES`, `CLAIM_RE` (with `tests`), and docstring are kept
unchanged.

##### Verbatim hub hunks (as instructed)

```diff
--- a/bin/check-doc-paths
+++ b/bin/check-doc-paths
@@ -43,6 +43,26 @@
 # A claim is a single-line backticked token that looks like a repo path.
 CLAIM_RE = re.compile(r"`((?:docs|skills|bin|tests)/[A-Za-z0-9_./-]+)`")
 
+# Plan Replacement-block sync checks for active promoted plans.
+PLAN_STATUS_RE = re.compile(r"^Status:\s*([A-Za-z-]+)(.*)$", re.IGNORECASE)
+INDEX_ROW_RE = re.compile(
+    r"^\|\s*`?([0-9]{4}-[0-9]{2}-[0-9]{2}-[A-Za-z0-9_-]+\.md)`?\s*\|\s*([A-Za-z-]+)(.*?)\|\s*$"
+)
+PROMOTION_BASELINE_RE = re.compile(
+    r"^(?:[-*]\s*)?(?:\*\*Promotion [Bb]aseline(?: [Ii]dentifier)?:\*\*|Promotion [Bb]aseline(?: [Ii]dentifier)?:)\s*(.+)$"
+)
+UNPROMOTED_VALUE_RE = re.compile(
+    r"^`?(?:pending|none|tbd|n/a|not yet promoted)\b", re.IGNORECASE
+)
+PROMOTED_NOTE_RE = re.compile(
+    r"\b(?:promoted|spec-promotion slice applied)\b", re.IGNORECASE
+)
+HEADING_PATH_RE = re.compile(r"^#{1,6}\s+.*`([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)`")
+REPLACEMENT_LABEL_RE = re.compile(
+    r"^Replacement(?:\s*\(`([A-Za-z0-9_./-]+)`\))?:\s*$"
+)
+SKIPPED_PLAN_STATUSES = {"draft", "completed", "superseded"}
+
 # Suffixes/patterns that are templates or globs, not claims.
 NON_CLAIM = re.compile(r"[*{}<>]|YYYY|<[a-z]|\.\.\.")
 
@@ -149,6 +169,164 @@
     return failures, claimed_allowlisted
 
 
+def _load_plan_index_statuses(
+    plans_dir: Path,
+) -> dict[str, tuple[str, str]] | None:
+    """Return {plan_filename: (status_word, rest_of_cell)} from ## Status Index in docs/plans/README.md, or None if README.md is absent."""
+    readme = plans_dir / "README.md"
+    if not readme.is_file():
+        return None
+    statuses: dict[str, tuple[str, str]] = {}
+    in_status_index = False
+    for line in readme.read_text(errors="replace").splitlines():
+        stripped = line.strip()
+        if stripped.startswith("## "):
+            in_status_index = stripped == "## Status Index"
+            continue
+        if not in_status_index:
+            continue
+        m = INDEX_ROW_RE.match(stripped)
+        if m:
+            statuses[m.group(1)] = (m.group(2).lower(), m.group(3))
+    return statuses
+
+
+def _is_plan_promoted(
+    lines: list[str],
+    status_word: str | None,
+    status_tail: str,
+) -> bool:
+    """Return True when an active plan has promoted its spec delta (§4b/§4d)."""
+    if status_word == "promoted" or PROMOTED_NOTE_RE.search(status_tail):
+        return True
+    for _, line in _prose_lines("\n".join(lines)):
+        m_prom = PROMOTION_BASELINE_RE.match(line.strip())
+        if m_prom:
+            val = m_prom.group(1).strip()
+            if val and not UNPROMOTED_VALUE_RE.match(val):
+                return True
+    return False
+
+
+def _plan_replacement_sync_failures(root: Path) -> list[str]:
+    """Verify every Replacement: fenced block in an active promoted plan appears in its target file."""
+    plans_dir = root / "docs" / "plans"
+    if not plans_dir.is_dir():
+        return []
+    index_statuses = _load_plan_index_statuses(plans_dir)
+    failures: list[str] = []
+    root_resolved = root.resolve()
+    for plan in sorted(plans_dir.glob("*.md")):
+        if plan.name == "README.md":
+            continue
+        raw_text = plan.read_text(errors="replace")
+        lines = raw_text.splitlines()
+        status_word: str | None = None
+        status_tail = ""
+        if index_statuses is not None:
+            if plan.name not in index_statuses:
+                if any(
+                    REPLACEMENT_LABEL_RE.match(line)
+                    for _, line in _prose_lines(raw_text)
+                ):
+                    failures.append(
+                        f"{plan.relative_to(root)}:1: plan with Replacement block is missing from `docs/plans/README.md` `## Status Index`"
+                    )
+                continue
+            status_word, status_tail = index_statuses[plan.name]
+        else:
+            for line in lines[:25]:
+                m_status = PLAN_STATUS_RE.match(line)
+                if m_status:
+                    status_word = m_status.group(1).lower()
+                    status_tail = m_status.group(2)
+                    break
+        if status_word in SKIPPED_PLAN_STATUSES:
+            continue
+        if not _is_plan_promoted(lines, status_word, status_tail):
+            continue
+
+        fence: tuple[str, int] | None = None
+        heading_target: str | None = None
+        pending: tuple[int, str | None] | None = None
+        active_block: tuple[int, str | None, list[str]] | None = None
+
+        for lineno, line in enumerate(lines, start=1):
+            marker: tuple[str, int, str] | None = None
+            leading_spaces = len(line) - len(line.lstrip(" "))
+            stripped = line[leading_spaces:]
+            if leading_spaces <= 3 and stripped.startswith(("```", "~~~")):
+                char = stripped[0]
+                length = len(stripped) - len(stripped.lstrip(char))
+                marker = (char, length, stripped[length:])
+            if fence is None and marker is not None:
+                fence = (marker[0], marker[1])
+                if pending is not None:
+                    active_block = (pending[0], pending[1], [])
+                    pending = None
+                continue
+            if fence is not None:
+                if (
+                    marker is not None
+                    and marker[0] == fence[0]
+                    and marker[1] >= fence[1]
+                    and not marker[2].strip()
+                ):
+                    fence = None
+                    if active_block is not None:
+                        rep_line, target_rel, block_lines = active_block
+                        active_block = None
+                        if not target_rel:
+                            failures.append(
+                                f"{plan.relative_to(root)}:{rep_line}: "
+                                "Replacement block has no backticked target path"
+                            )
+                            continue
+                        target_path = (root / target_rel).resolve()
+                        if (
+                            not target_path.is_relative_to(root_resolved)
+                            or not target_path.is_file()
+                        ):
+                            failures.append(
+                                f"{plan.relative_to(root)}:{rep_line}: "
+                                f"Replacement target `{target_rel}` does not exist"
+                            )
+                            continue
+                        snippet = "\n".join(block_lines)
+                        target_text = target_path.read_text(errors="replace")
+                        if not snippet or snippet not in target_text:
+                            failures.append(
+                                f"{plan.relative_to(root)}:{rep_line}: "
+                                f"Replacement block for `{target_rel}` does not match target file"
+                            )
+                elif active_block is not None:
+                    active_block[2].append(line)
+                continue
+
+            if pending is not None and line.strip():
+                failures.append(
+                    f"{plan.relative_to(root)}:{pending[0]}: "
+                    "Replacement label must be followed immediately by a fenced code block"
+                )
+                pending = None
+
+            m_heading = HEADING_PATH_RE.match(line)
+            if m_heading:
+                heading_target = m_heading.group(1)
+                continue
+            m_rep = REPLACEMENT_LABEL_RE.match(line)
+            if m_rep:
+                pending = (lineno, m_rep.group(1) or heading_target)
+
+        if pending is not None or active_block is not None:
+            unfinished_line = pending[0] if pending is not None else active_block[0]
+            failures.append(
+                f"{plan.relative_to(root)}:{unfinished_line}: "
+                "Replacement label has no closed fenced code block"
+            )
+    return failures
+
+
 def check_tree(
     root: Path,
     *,
@@ -166,8 +344,9 @@
         "remove the unused suppression entry"
         for unused in sorted(active_declared - claimed_allowlisted)
     )
+    failures.extend(_plan_replacement_sync_failures(root))
     if failures:
-        print(f"check-doc-paths: {len(failures)} dangling path claim(s):")
+        print(f"check-doc-paths: {len(failures)} failure(s):")
         for fail in failures:
             print(f"  {fail}")
         return 1
@@ -281,11 +460,103 @@
                 (root / symlink[0]).symlink_to(symlink[1])
             if quiet_check(root, declared) != expected:
                 failures.append(name)
+
+    plan_probes = [
+        (
+            "active promoted plan Replacement block matching target file was wrongly flagged",
+            "Status: active\nPromotion Baseline Identifier: `296a7cd` + worktree\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nexact spec line\n```\n",
+            None,
+            "header\nexact spec line\nfooter\n",
+            0,
+        ),
+        (
+            "active promoted plan Replacement block diverging from target file passed",
+            "Status: active\nPromotion Baseline Identifier: `296a7cd` + worktree\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nstale plan text\n```\n",
+            None,
+            "header\nupdated spec text\nfooter\n",
+            1,
+        ),
+        (
+            "active promoted plan heading-scoped Replacement block diverging from target file passed",
+            "Status: active — promoted\n\n### Delta 1 — `docs/specifications/01-sample.md`\n\nReplacement:\n```markdown\nstale text\n```\n",
+            None,
+            "header\nupdated text\n",
+            1,
+        ),
+        (
+            "active unpromoted plan Replacement block was wrongly flagged before promotion",
+            "Status: active\nPromotion Baseline Identifier: pending\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nunpromoted text\n```\n",
+            "| 2026-11-02-sample-plan.md | active |\n",
+            "baseline text\n",
+            0,
+        ),
+        (
+            "draft plan unpromoted Replacement block was wrongly flagged",
+            "Status: draft\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nunpromoted text\n```\n",
+            "| 2026-11-02-sample-plan.md | draft |\n",
+            "baseline text\n",
+            0,
+        ),
+        (
+            "completed index row overrode stale active header and skipped historical Replacement block",
+            "Status: active — promoted\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nolder historical text\n```\n",
+            "| 2026-11-02-sample-plan.md | completed — landed |\n",
+            "later evolved text\n",
+            0,
+        ),
+        (
+            "active promoted index row overrode stale draft header and caught diverging Replacement block",
+            "Status: draft\nPromotion Baseline Identifier: `296a7cd`\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nstale text\n```\n",
+            "| 2026-11-02-sample-plan.md | active — promoted |\n",
+            "updated text\n",
+            1,
+        ),
+        (
+            "unindexed plan with Replacement block when README exists failed instead of falling back to Status header",
+            "Status: completed\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nexact text\n```\n",
+            "",
+            "exact text\n",
+            1,
+        ),
+        (
+            "row outside Status Index section was ignored and did not mark active promoted plan completed",
+            "Status: active — promoted\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nstale text\n```\n",
+            "RAW:# Plans\n\n## Example\n\n| 2026-11-02-sample-plan.md | completed |\n\n## Status Index\n\n| Plan | Status |\n|------|--------|\n| 2026-11-02-sample-plan.md | active — promoted |\n",
+            "updated text\n",
+            1,
+        ),
+        (
+            "active promoted plan Replacement label without fenced block passed",
+            "Status: active — promoted\n\nReplacement (`docs/specifications/01-sample.md`):\nnot a fence\n",
+            None,
+            "not a fence\n",
+            1,
+        ),
+    ]
+    for name, plan_text, index_row, spec_text, expected in plan_probes:
+        with tempfile.TemporaryDirectory() as td:
+            root = _build_probe_tree(td, spec_text=spec_text)
+            plans_dir = root / "docs" / "plans"
+            plans_dir.mkdir(parents=True, exist_ok=True)
+            if index_row is not None:
+                readme_content = (
+                    index_row[4:]
+                    if index_row.startswith("RAW:")
+                    else "# Plans\n\n## Status Index\n\n| Plan | Status |\n|------|--------|\n"
+                    + index_row
+                )
+                (plans_dir / "README.md").write_text(readme_content)
+            (plans_dir / "2026-11-02-sample-plan.md").write_text(plan_text)
+            if quiet_check(root, set()) != expected:
+                failures.append(name)
+
     if failures:
         for f in failures:
             print(f"check-doc-paths self-test FAIL: {f}")
         return 1
-    print("check-doc-paths self-test: all claim, fence, and allowlist probes pass")
+    print(
+        "check-doc-paths self-test: all claim, fence, allowlist, and plan-replacement sync probes pass"
+    )
     return 0
 
 
```

The verbatim version passes its self-test and the Weft tree, but fails two
Weft gates that cover this file:
- `ruff check .`: `pyproject.toml` `extend-include` lists `bin/check-doc-paths`,
  and C901 flags `_plan_replacement_sync_failures` (complexity 26 > 10). Weft's
  gate already splits the hub's inline fence logic into `_fence_marker` /
  `_closes_fence` for this reason.
- mypy (when run on the file): `pending[0] if pending is not None else
  active_block[0]` gives `[index]`, "Value of type ... | None is not indexable".

```text
C901 `_plan_replacement_sync_failures` is too complex (26 > 10)
   --> <scratch>/check_doc_paths_scratch.py:211:5
    |
...
<scratch>/check_doc_paths_scratch.py:322: error: Value of type "tuple[int, str | None, list[str]] | None" is not indexable  [index]
Found 1 error in 1 file (checked 1 source file)
```

##### Lint-clean Weft variant (recommended for task 13)

The behavior is the same; the hub function is split into `_plan_status`,
`_replacement_block_failure`, `_scan_prose_label_line`, and
`_plan_replacement_block_failures`, which reuse Weft's `_fence_marker` /
`_closes_fence`. It passes `--self-test`, the Weft tree, `ruff check`,
`ruff format --check`, and mypy. On the four README scenarios below it produces
the same failures as the verbatim version. This is a deviation from verbatim
hub code; it is adopted and logged in plan §4a.

```diff
--- a/bin/check-doc-paths
+++ b/bin/check-doc-paths
@@ -43,6 +43,24 @@
 # A claim is a single-line backticked token that looks like a repo path.
 CLAIM_RE = re.compile(r"`((?:docs|skills|bin|tests)/[A-Za-z0-9_./-]+)`")
 
+# Plan Replacement-block sync checks for active promoted plans.
+PLAN_STATUS_RE = re.compile(r"^Status:\s*([A-Za-z-]+)(.*)$", re.IGNORECASE)
+INDEX_ROW_RE = re.compile(
+    r"^\|\s*`?([0-9]{4}-[0-9]{2}-[0-9]{2}-[A-Za-z0-9_-]+\.md)`?\s*\|\s*([A-Za-z-]+)(.*?)\|\s*$"
+)
+PROMOTION_BASELINE_RE = re.compile(
+    r"^(?:[-*]\s*)?(?:\*\*Promotion [Bb]aseline(?: [Ii]dentifier)?:\*\*|Promotion [Bb]aseline(?: [Ii]dentifier)?:)\s*(.+)$"
+)
+UNPROMOTED_VALUE_RE = re.compile(
+    r"^`?(?:pending|none|tbd|n/a|not yet promoted)\b", re.IGNORECASE
+)
+PROMOTED_NOTE_RE = re.compile(
+    r"\b(?:promoted|spec-promotion slice applied)\b", re.IGNORECASE
+)
+HEADING_PATH_RE = re.compile(r"^#{1,6}\s+.*`([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)`")
+REPLACEMENT_LABEL_RE = re.compile(r"^Replacement(?:\s*\(`([A-Za-z0-9_./-]+)`\))?:\s*$")
+SKIPPED_PLAN_STATUSES = {"draft", "completed", "superseded"}
+
 # Suffixes/patterns that are templates or globs, not claims.
 NON_CLAIM = re.compile(r"[*{}<>]|YYYY|<[a-z]|\.\.\.")
 
@@ -147,8 +165,191 @@
             _scan_file_claims(root, f, declared_future, claimed_allowlisted)
         )
     return failures, claimed_allowlisted
+
+
+def _load_plan_index_statuses(
+    plans_dir: Path,
+) -> dict[str, tuple[str, str]] | None:
+    """Return {plan_filename: (status_word, rest_of_cell)} from ## Status Index in docs/plans/README.md, or None if README.md is absent."""
+    readme = plans_dir / "README.md"
+    if not readme.is_file():
+        return None
+    statuses: dict[str, tuple[str, str]] = {}
+    in_status_index = False
+    for line in readme.read_text(errors="replace").splitlines():
+        stripped = line.strip()
+        if stripped.startswith("## "):
+            in_status_index = stripped == "## Status Index"
+            continue
+        if not in_status_index:
+            continue
+        m = INDEX_ROW_RE.match(stripped)
+        if m:
+            statuses[m.group(1)] = (m.group(2).lower(), m.group(3))
+    return statuses
+
+
+def _is_plan_promoted(
+    lines: list[str],
+    status_word: str | None,
+    status_tail: str,
+) -> bool:
+    """Return True when an active plan has promoted its spec delta (§4b/§4d)."""
+    if status_word == "promoted" or PROMOTED_NOTE_RE.search(status_tail):
+        return True
+    for _, line in _prose_lines("\n".join(lines)):
+        m_prom = PROMOTION_BASELINE_RE.match(line.strip())
+        if m_prom:
+            val = m_prom.group(1).strip()
+            if val and not UNPROMOTED_VALUE_RE.match(val):
+                return True
+    return False
+
+
+def _plan_status(
+    root: Path,
+    plan: Path,
+    raw_text: str,
+    index_statuses: dict[str, tuple[str, str]] | None,
+    failures: list[str],
+) -> tuple[str | None, str] | None:
+    """Return (status_word, status_tail) for a plan, or None to skip it."""
+    if index_statuses is not None:
+        if plan.name in index_statuses:
+            return index_statuses[plan.name]
+        if any(REPLACEMENT_LABEL_RE.match(line) for _, line in _prose_lines(raw_text)):
+            failures.append(
+                f"{plan.relative_to(root)}:1: plan with Replacement block is missing from `docs/plans/README.md` `## Status Index`"
+            )
+        return None
+    for line in raw_text.splitlines()[:25]:
+        m_status = PLAN_STATUS_RE.match(line)
+        if m_status:
+            return m_status.group(1).lower(), m_status.group(2)
+    return None, ""
+
+
+def _replacement_block_failure(
+    root: Path,
+    plan: Path,
+    rep_line: int,
+    target_rel: str | None,
+    block_lines: list[str],
+) -> str | None:
+    """Return a failure line when a closed Replacement block is not in its target."""
+    if not target_rel:
+        return (
+            f"{plan.relative_to(root)}:{rep_line}: "
+            "Replacement block has no backticked target path"
+        )
+    target_path = (root / target_rel).resolve()
+    if not target_path.is_relative_to(root.resolve()) or not target_path.is_file():
+        return (
+            f"{plan.relative_to(root)}:{rep_line}: "
+            f"Replacement target `{target_rel}` does not exist"
+        )
+    snippet = "\n".join(block_lines)
+    target_text = target_path.read_text(errors="replace")
+    if not snippet or snippet not in target_text:
+        return (
+            f"{plan.relative_to(root)}:{rep_line}: "
+            f"Replacement block for `{target_rel}` does not match target file"
+        )
+    return None
+
+
+def _scan_prose_label_line(
+    root: Path,
+    plan: Path,
+    lineno: int,
+    line: str,
+    pending: tuple[int, str | None] | None,
+    heading_target: str | None,
+    failures: list[str],
+) -> tuple[tuple[int, str | None] | None, str | None]:
+    """Track heading targets and Replacement labels on one prose line."""
+    if pending is not None and line.strip():
+        failures.append(
+            f"{plan.relative_to(root)}:{pending[0]}: "
+            "Replacement label must be followed immediately by a fenced code block"
+        )
+        pending = None
+    m_heading = HEADING_PATH_RE.match(line)
+    if m_heading:
+        return pending, m_heading.group(1)
+    m_rep = REPLACEMENT_LABEL_RE.match(line)
+    if m_rep:
+        pending = (lineno, m_rep.group(1) or heading_target)
+    return pending, heading_target
+
+
+def _plan_replacement_block_failures(
+    root: Path, plan: Path, lines: list[str]
+) -> list[str]:
+    """Check every Replacement: fenced block in one promoted plan."""
+    failures: list[str] = []
+    fence: tuple[str, int] | None = None
+    heading_target: str | None = None
+    pending: tuple[int, str | None] | None = None
+    active_block: tuple[int, str | None, list[str]] | None = None
+
+    for lineno, line in enumerate(lines, start=1):
+        marker = _fence_marker(line)
+        if fence is None and marker is not None:
+            fence = (marker[0], marker[1])
+            if pending is not None:
+                active_block = (pending[0], pending[1], [])
+                pending = None
+            continue
+        if fence is not None:
+            if _closes_fence(marker, fence):
+                fence = None
+                if active_block is not None:
+                    failure = _replacement_block_failure(root, plan, *active_block)
+                    active_block = None
+                    if failure is not None:
+                        failures.append(failure)
+            elif active_block is not None:
+                active_block[2].append(line)
+            continue
 
+        pending, heading_target = _scan_prose_label_line(
+            root, plan, lineno, line, pending, heading_target, failures
+        )
 
+    unfinished = pending if pending is not None else active_block
+    if unfinished is not None:
+        failures.append(
+            f"{plan.relative_to(root)}:{unfinished[0]}: "
+            "Replacement label has no closed fenced code block"
+        )
+    return failures
+
+
+def _plan_replacement_sync_failures(root: Path) -> list[str]:
+    """Verify every Replacement: fenced block in an active promoted plan appears in its target file."""
+    plans_dir = root / "docs" / "plans"
+    if not plans_dir.is_dir():
+        return []
+    index_statuses = _load_plan_index_statuses(plans_dir)
+    failures: list[str] = []
+    for plan in sorted(plans_dir.glob("*.md")):
+        if plan.name == "README.md":
+            continue
+        raw_text = plan.read_text(errors="replace")
+        status = _plan_status(root, plan, raw_text, index_statuses, failures)
+        if status is None:
+            continue
+        status_word, status_tail = status
+        if status_word in SKIPPED_PLAN_STATUSES:
+            continue
+        lines = raw_text.splitlines()
+        if not _is_plan_promoted(lines, status_word, status_tail):
+            continue
+        failures.extend(_plan_replacement_block_failures(root, plan, lines))
+    return failures
+
+
 def check_tree(
     root: Path,
     *,
@@ -166,8 +367,9 @@
         "remove the unused suppression entry"
         for unused in sorted(active_declared - claimed_allowlisted)
     )
+    failures.extend(_plan_replacement_sync_failures(root))
     if failures:
-        print(f"check-doc-paths: {len(failures)} dangling path claim(s):")
+        print(f"check-doc-paths: {len(failures)} failure(s):")
         for fail in failures:
             print(f"  {fail}")
         return 1
@@ -281,11 +483,103 @@
                 (root / symlink[0]).symlink_to(symlink[1])
             if quiet_check(root, declared) != expected:
                 failures.append(name)
+
+    plan_probes = [
+        (
+            "active promoted plan Replacement block matching target file was wrongly flagged",
+            "Status: active\nPromotion Baseline Identifier: `296a7cd` + worktree\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nexact spec line\n```\n",
+            None,
+            "header\nexact spec line\nfooter\n",
+            0,
+        ),
+        (
+            "active promoted plan Replacement block diverging from target file passed",
+            "Status: active\nPromotion Baseline Identifier: `296a7cd` + worktree\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nstale plan text\n```\n",
+            None,
+            "header\nupdated spec text\nfooter\n",
+            1,
+        ),
+        (
+            "active promoted plan heading-scoped Replacement block diverging from target file passed",
+            "Status: active — promoted\n\n### Delta 1 — `docs/specifications/01-sample.md`\n\nReplacement:\n```markdown\nstale text\n```\n",
+            None,
+            "header\nupdated text\n",
+            1,
+        ),
+        (
+            "active unpromoted plan Replacement block was wrongly flagged before promotion",
+            "Status: active\nPromotion Baseline Identifier: pending\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nunpromoted text\n```\n",
+            "| 2026-11-02-sample-plan.md | active |\n",
+            "baseline text\n",
+            0,
+        ),
+        (
+            "draft plan unpromoted Replacement block was wrongly flagged",
+            "Status: draft\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nunpromoted text\n```\n",
+            "| 2026-11-02-sample-plan.md | draft |\n",
+            "baseline text\n",
+            0,
+        ),
+        (
+            "completed index row overrode stale active header and skipped historical Replacement block",
+            "Status: active — promoted\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nolder historical text\n```\n",
+            "| 2026-11-02-sample-plan.md | completed — landed |\n",
+            "later evolved text\n",
+            0,
+        ),
+        (
+            "active promoted index row overrode stale draft header and caught diverging Replacement block",
+            "Status: draft\nPromotion Baseline Identifier: `296a7cd`\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nstale text\n```\n",
+            "| 2026-11-02-sample-plan.md | active — promoted |\n",
+            "updated text\n",
+            1,
+        ),
+        (
+            "unindexed plan with Replacement block when README exists failed instead of falling back to Status header",
+            "Status: completed\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nexact text\n```\n",
+            "",
+            "exact text\n",
+            1,
+        ),
+        (
+            "row outside Status Index section was ignored and did not mark active promoted plan completed",
+            "Status: active — promoted\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nstale text\n```\n",
+            "RAW:# Plans\n\n## Example\n\n| 2026-11-02-sample-plan.md | completed |\n\n## Status Index\n\n| Plan | Status |\n|------|--------|\n| 2026-11-02-sample-plan.md | active — promoted |\n",
+            "updated text\n",
+            1,
+        ),
+        (
+            "active promoted plan Replacement label without fenced block passed",
+            "Status: active — promoted\n\nReplacement (`docs/specifications/01-sample.md`):\nnot a fence\n",
+            None,
+            "not a fence\n",
+            1,
+        ),
+    ]
+    for name, plan_text, index_row, spec_text, expected in plan_probes:
+        with tempfile.TemporaryDirectory() as td:
+            root = _build_probe_tree(td, spec_text=spec_text)
+            plans_dir = root / "docs" / "plans"
+            plans_dir.mkdir(parents=True, exist_ok=True)
+            if index_row is not None:
+                readme_content = (
+                    index_row[4:]
+                    if index_row.startswith("RAW:")
+                    else "# Plans\n\n## Status Index\n\n| Plan | Status |\n|------|--------|\n"
+                    + index_row
+                )
+                (plans_dir / "README.md").write_text(readme_content)
+            (plans_dir / "2026-11-02-sample-plan.md").write_text(plan_text)
+            if quiet_check(root, set()) != expected:
+                failures.append(name)
+
     if failures:
         for f in failures:
             print(f"check-doc-paths self-test FAIL: {f}")
         return 1
-    print("check-doc-paths self-test: all claim, fence, and allowlist probes pass")
+    print(
+        "check-doc-paths self-test: all claim, fence, allowlist, and plan-replacement sync probes pass"
+    )
     return 0
 
 
```

##### Self-test and tree run (verbatim scratch copy)

```text
$ python3 <scratch>/check-doc-paths --self-test
check-doc-paths self-test: all claim, fence, allowlist, and plan-replacement sync probes pass
exit=0
$ python3 <scratch>/check-doc-paths --root /Users/van/Developer/weft   (cwd = Weft root)
check-doc-paths: OK (/Users/van/Developer/weft)
exit=0
$ python3 <scratch>/check-doc-paths   (cwd = Weft root, no --root)
check-doc-paths: OK (<scratch>)
exit=0
```

The third run (no `--root`) scans the scratch copy's own parent directory
(`REPO_ROOT` is relative to the script), so it says nothing about Weft. The
`--root` run is the meaningful one.

##### `## Index` → `## Status Index` and the metadata test

- `tests/specs/test_plan_metadata.py:61` asserts `"## Index" in readme_text`.
  `"## Index"` is not a substring of `"## Status Index"`, so renaming the heading
  fails `test_plan_readme_exists_and_defines_status_taxonomy`. **The rename
  conflicts with the test.** The test must change in the same commit, or the
  heading must stay.
- The rename alone does not make the gate read Weft statuses. Hub
  `INDEX_ROW_RE` expects `| <file>.md | <status> ... |` (the filename in the first
  cell, optionally backticked, and the status in the second cell). Weft rows are
  `| [`<file>.md`](./<file>.md) | <Title> | `<status>` | <superseded> |`, so
  no Weft row matches (checked: `INDEX_ROW_RE.match(<reconciliation row>)` is
  `None`). When `docs/plans/README.md` exists, the index map is `{}` with or without the
  rename. Every plan that carries a `Replacement (`...`):` label then fails with
  "plan with Replacement block is missing from `docs/plans/README.md` `## Status
  Index`".
- Consequence for this plan: once task 13 lands the extended gate, the moment
  any Replacement block from this file is pasted into
  `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`, `bin/check-doc-paths`
  exits 1. Probe results (scratch tree containing the Weft README and a `draft` plan
  with one Replacement block):
  - A (README as-is, `## Index`): 1 failure, "missing from ... Status Index".
  - B (heading renamed): the same failure.
  - C (no README; header `Status: draft`): OK, because draft is skipped.
  - D (no README; `Status: active` plus the plan's current line "Promotion
    baseline identifier: recorded at the close of Slice C"): the plan is treated
    as **promoted**, because "recorded ..." does not match `UNPROMOTED_VALUE_RE`
    (`pending|none|tbd|n/a|not yet promoted`), and the block is checked and fails.
    The plan's §4b wording would read as promoted under any non-skipped status.
    Write `pending` until promotion.
- Even with the row format fixed, Weft's taxonomy has only `draft` and
  `completed`, and the gate skips both (`SKIPPED_PLAN_STATUSES`). The sync check
  therefore never fires on a Weft plan unless Weft adopts a non-skipped active
  status such as `active` / `promoted`. That is an owner decision (it also
  changes `ALLOWED_STATUSES` in the metadata test), recorded as §9 fork 3.
  Options as staged (fork 3 records the decided wording): (1) teach `INDEX_ROW_RE` Weft's row shape and add an `active`
  status; (2) keep the gate extension but accept that it is inert in Weft;
  (3) do not propagate this gate hunk.

#### Adapted port of the gate (fork 3, decided adapt 2026-11-02; task 6a)

Staged on 2026-11-02 from the lint-clean variant (§4a). Adaptations: `INDEX_ROW_RE` reads Weft's row shape only (link whose target matches the backticked filename, title, backticked status with optional qualifier, superseded-by cell); the heading is `## Status Index`; `SKIPPED_PLAN_STATUSES` and promotion detection are the hub's; the hub's ten self-test probes are kept with their README rows switched to Weft's shape, plus seven Weft probes (promoted matching 0, promoted diverging 1, draft 0, completed 0, unindexed 1, bare hub-shape row not read 1, row under `## Index` not read 1). Verified: `--self-test` exit 0; ruff check, ruff format, and mypy clean from the Weft root; four mutations each fail the expected probes; against the current tree the gate exits 1 with one failure, this plan missing from `## Status Index`, because the heading is still `## Index`, so the port, the heading rename, and the test edit land in one change at task 13; dry run on a scratch copy of the tree with the heading renamed, `promoted` in the taxonomy, this plan's row and header at `promoted`, and every Replacement block applied: 42 of 42 contained, exit 0, metadata test 4 passed, one-word mutation gives exactly one failure. The gate port itself is staged as a whole-file diff under a plain label, not a `Replacement` label, because diff text never matches its target and would fail the gate's own check once this plan is promoted. The `promoted` definitions in the vocabulary blocks are Weft wording, not hub text.


Prepared 2026-11-02 for `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`
task 6a (fork 3 option (a), adapt), applied at task 13. Each Replacement block
holds the exact post-change span. Every block below was taken from the scratch
dry-run tree after application, and the adapted gate finds each one contained
in its target there.

##### Staged text — bin/check-doc-paths

Whole-file unified diff against Weft's current `bin/check-doc-paths`
(`ec50bd57`). It is a ```` ```diff ```` block under a plain label rather than
a `Replacement (...)` label on purpose: a labelled block is checked for
containment once the plan is `promoted`, and diff text (with its `+`/`-`
prefixes) is never contained in the target, so a `Replacement` label here
would make the gate fail on its own port.

Whole-file diff (`bin/check-doc-paths`):

```diff
--- a/bin/check-doc-paths
+++ b/bin/check-doc-paths
@@ -13,12 +13,22 @@
 bin/ tools and tests/specs/ gates weft's guidance cites by path, and
 the hub-only --scaffold mode is dropped.
 
+The plan Replacement-sync check is ported from agent-theory @ e6ac579
+via docs/plans/2026-11-02-program-theory-reconciliation-plan.md (task
+6a, fork 3 option (a)). Weft-local adaptations: the `## Status Index`
+section of docs/plans/README.md is read in weft's row shape (a link to
+the plan file, a title, a backticked status, a superseded-by cell), and
+the check fires for a `promoted` plan; `draft`, `completed`, and
+`superseded` plans are skipped.
+
 Modes:
   check-doc-paths [--root DIR]   scan a tree (default: this repo)
   check-doc-paths --self-test    firing tests for the claim scan, fenced
-                                 block stripping, and DECLARED_FUTURE_ARTIFACTS
+                                 block stripping, DECLARED_FUTURE_ARTIFACTS,
+                                 and the plan Replacement-sync check
 
-Exit 0 when every claim resolves; exit 1 listing the danglers.
+Exit 0 when every claim resolves and every promoted plan's Replacement
+blocks are contained in their targets; exit 1 listing the failures.
 """
 
 from __future__ import annotations
@@ -43,6 +53,27 @@
 # A claim is a single-line backticked token that looks like a repo path.
 CLAIM_RE = re.compile(r"`((?:docs|skills|bin|tests)/[A-Za-z0-9_./-]+)`")
 
+# Plan Replacement-block sync checks for active promoted plans.
+PLAN_STATUS_RE = re.compile(r"^Status:\s*([A-Za-z-]+)(.*)$", re.IGNORECASE)
+# Weft index rows: | [`<plan>.md`](./<plan>.md) | <title> | `<status>`[ qualifier] |
+# <superseded by> |. The hub's bare `| <plan>.md | <status> |` shape is not read.
+INDEX_ROW_RE = re.compile(
+    r"^\|\s*\[`?([0-9]{4}-[0-9]{2}-[0-9]{2}-[A-Za-z0-9_-]+\.md)`?\]\(\./\1\)\s*"
+    r"\|[^|]*\|\s*`?([A-Za-z-]+)`?([^|]*)\|[^|]*\|\s*$"
+)
+PROMOTION_BASELINE_RE = re.compile(
+    r"^(?:[-*]\s*)?(?:\*\*Promotion [Bb]aseline(?: [Ii]dentifier)?:\*\*|Promotion [Bb]aseline(?: [Ii]dentifier)?:)\s*(.+)$"
+)
+UNPROMOTED_VALUE_RE = re.compile(
+    r"^`?(?:pending|none|tbd|n/a|not yet promoted)\b", re.IGNORECASE
+)
+PROMOTED_NOTE_RE = re.compile(
+    r"\b(?:promoted|spec-promotion slice applied)\b", re.IGNORECASE
+)
+HEADING_PATH_RE = re.compile(r"^#{1,6}\s+.*`([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)`")
+REPLACEMENT_LABEL_RE = re.compile(r"^Replacement(?:\s*\(`([A-Za-z0-9_./-]+)`\))?:\s*$")
+SKIPPED_PLAN_STATUSES = {"draft", "completed", "superseded"}
+
 # Suffixes/patterns that are templates or globs, not claims.
 NON_CLAIM = re.compile(r"[*{}<>]|YYYY|<[a-z]|\.\.\.")
 
@@ -149,6 +180,189 @@
     return failures, claimed_allowlisted
 
 
+def _load_plan_index_statuses(
+    plans_dir: Path,
+) -> dict[str, tuple[str, str]] | None:
+    """Return {plan_filename: (status_word, rest_of_cell)} from ## Status Index in docs/plans/README.md, or None if README.md is absent."""
+    readme = plans_dir / "README.md"
+    if not readme.is_file():
+        return None
+    statuses: dict[str, tuple[str, str]] = {}
+    in_status_index = False
+    for line in readme.read_text(errors="replace").splitlines():
+        stripped = line.strip()
+        if stripped.startswith("## "):
+            in_status_index = stripped == "## Status Index"
+            continue
+        if not in_status_index:
+            continue
+        m = INDEX_ROW_RE.match(stripped)
+        if m:
+            statuses[m.group(1)] = (m.group(2).lower(), m.group(3))
+    return statuses
+
+
+def _is_plan_promoted(
+    lines: list[str],
+    status_word: str | None,
+    status_tail: str,
+) -> bool:
+    """Return True when an active plan has promoted its spec delta (§4b/§4d)."""
+    if status_word == "promoted" or PROMOTED_NOTE_RE.search(status_tail):
+        return True
+    for _, line in _prose_lines("\n".join(lines)):
+        m_prom = PROMOTION_BASELINE_RE.match(line.strip())
+        if m_prom:
+            val = m_prom.group(1).strip()
+            if val and not UNPROMOTED_VALUE_RE.match(val):
+                return True
+    return False
+
+
+def _plan_status(
+    root: Path,
+    plan: Path,
+    raw_text: str,
+    index_statuses: dict[str, tuple[str, str]] | None,
+    failures: list[str],
+) -> tuple[str | None, str] | None:
+    """Return (status_word, status_tail) for a plan, or None to skip it."""
+    if index_statuses is not None:
+        if plan.name in index_statuses:
+            return index_statuses[plan.name]
+        if any(REPLACEMENT_LABEL_RE.match(line) for _, line in _prose_lines(raw_text)):
+            failures.append(
+                f"{plan.relative_to(root)}:1: plan with Replacement block is missing from `docs/plans/README.md` `## Status Index`"
+            )
+        return None
+    for line in raw_text.splitlines()[:25]:
+        m_status = PLAN_STATUS_RE.match(line)
+        if m_status:
+            return m_status.group(1).lower(), m_status.group(2)
+    return None, ""
+
+
+def _replacement_block_failure(
+    root: Path,
+    plan: Path,
+    rep_line: int,
+    target_rel: str | None,
+    block_lines: list[str],
+) -> str | None:
+    """Return a failure line when a closed Replacement block is not in its target."""
+    if not target_rel:
+        return (
+            f"{plan.relative_to(root)}:{rep_line}: "
+            "Replacement block has no backticked target path"
+        )
+    target_path = (root / target_rel).resolve()
+    if not target_path.is_relative_to(root.resolve()) or not target_path.is_file():
+        return (
+            f"{plan.relative_to(root)}:{rep_line}: "
+            f"Replacement target `{target_rel}` does not exist"
+        )
+    snippet = "\n".join(block_lines)
+    target_text = target_path.read_text(errors="replace")
+    if not snippet or snippet not in target_text:
+        return (
+            f"{plan.relative_to(root)}:{rep_line}: "
+            f"Replacement block for `{target_rel}` does not match target file"
+        )
+    return None
+
+
+def _scan_prose_label_line(
+    root: Path,
+    plan: Path,
+    lineno: int,
+    line: str,
+    pending: tuple[int, str | None] | None,
+    heading_target: str | None,
+    failures: list[str],
+) -> tuple[tuple[int, str | None] | None, str | None]:
+    """Track heading targets and Replacement labels on one prose line."""
+    if pending is not None and line.strip():
+        failures.append(
+            f"{plan.relative_to(root)}:{pending[0]}: "
+            "Replacement label must be followed immediately by a fenced code block"
+        )
+        pending = None
+    m_heading = HEADING_PATH_RE.match(line)
+    if m_heading:
+        return pending, m_heading.group(1)
+    m_rep = REPLACEMENT_LABEL_RE.match(line)
+    if m_rep:
+        pending = (lineno, m_rep.group(1) or heading_target)
+    return pending, heading_target
+
+
+def _plan_replacement_block_failures(
+    root: Path, plan: Path, lines: list[str]
+) -> list[str]:
+    """Check every Replacement: fenced block in one promoted plan."""
+    failures: list[str] = []
+    fence: tuple[str, int] | None = None
+    heading_target: str | None = None
+    pending: tuple[int, str | None] | None = None
+    active_block: tuple[int, str | None, list[str]] | None = None
+
+    for lineno, line in enumerate(lines, start=1):
+        marker = _fence_marker(line)
+        if fence is None and marker is not None:
+            fence = (marker[0], marker[1])
+            if pending is not None:
+                active_block = (pending[0], pending[1], [])
+                pending = None
+            continue
+        if fence is not None:
+            if _closes_fence(marker, fence):
+                fence = None
+                if active_block is not None:
+                    failure = _replacement_block_failure(root, plan, *active_block)
+                    active_block = None
+                    if failure is not None:
+                        failures.append(failure)
+            elif active_block is not None:
+                active_block[2].append(line)
+            continue
+
+        pending, heading_target = _scan_prose_label_line(
+            root, plan, lineno, line, pending, heading_target, failures
+        )
+
+    unfinished = pending if pending is not None else active_block
+    if unfinished is not None:
+        failures.append(
+            f"{plan.relative_to(root)}:{unfinished[0]}: "
+            "Replacement label has no closed fenced code block"
+        )
+    return failures
+
+
+def _plan_replacement_sync_failures(root: Path) -> list[str]:
+    """Verify every Replacement: fenced block in an active promoted plan appears in its target file."""
+    plans_dir = root / "docs" / "plans"
+    if not plans_dir.is_dir():
+        return []
+    index_statuses = _load_plan_index_statuses(plans_dir)
+    failures: list[str] = []
+    for plan in sorted(plans_dir.glob("*.md")):
+        if plan.name == "README.md":
+            continue
+        raw_text = plan.read_text(errors="replace")
+        status = _plan_status(root, plan, raw_text, index_statuses, failures)
+        if status is None:
+            continue
+        status_word, status_tail = status
+        if status_word in SKIPPED_PLAN_STATUSES:
+            continue
+        lines = raw_text.splitlines()
+        if not _is_plan_promoted(lines, status_word, status_tail):
+            continue
+        failures.extend(_plan_replacement_block_failures(root, plan, lines))
+    return failures
+
+
 def check_tree(
     root: Path,
     *,
@@ -166,8 +380,9 @@
         "remove the unused suppression entry"
         for unused in sorted(active_declared - claimed_allowlisted)
     )
+    failures.extend(_plan_replacement_sync_failures(root))
     if failures:
-        print(f"check-doc-paths: {len(failures)} dangling path claim(s):")
+        print(f"check-doc-paths: {len(failures)} failure(s):")
         for fail in failures:
             print(f"  {fail}")
         return 1
@@ -191,6 +406,18 @@
     return root
 
 
+PROBE_PLAN = "2026-11-02-sample-plan.md"
+PROBE_INDEX_HEADER = (
+    "# Plans\n\n## Status Index\n\n"
+    "| File | Title | Status | Superseded by |\n| --- | --- | --- | --- |\n"
+)
+
+
+def _probe_row(status_cell: str, plan: str = PROBE_PLAN) -> str:
+    """Return one weft-shaped `## Status Index` row for a self-test probe."""
+    return f"| [`{plan}`](./{plan}) | Sample plan | {status_cell} | none |\n"
+
+
 def self_test() -> int:
     """Firing tests for claim scanning, fenced blocks, and allowlist honesty."""
 
@@ -281,11 +508,163 @@
                 (root / symlink[0]).symlink_to(symlink[1])
             if quiet_check(root, declared) != expected:
                 failures.append(name)
+
+    spec_rel = "docs/specifications/01-sample.md"
+    weft_block = f"Replacement (`{spec_rel}`):\n```markdown\n"
+    plan_probes: list[tuple[str, str, str | None, str, int]] = [
+        (
+            "active promoted plan Replacement block matching target file was wrongly flagged",
+            "Status: active\nPromotion Baseline Identifier: `296a7cd` + worktree\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nexact spec line\n```\n",
+            None,
+            "header\nexact spec line\nfooter\n",
+            0,
+        ),
+        (
+            "active promoted plan Replacement block diverging from target file passed",
+            "Status: active\nPromotion Baseline Identifier: `296a7cd` + worktree\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nstale plan text\n```\n",
+            None,
+            "header\nupdated spec text\nfooter\n",
+            1,
+        ),
+        (
+            "active promoted plan heading-scoped Replacement block diverging from target file passed",
+            "Status: active — promoted\n\n### Delta 1 — `docs/specifications/01-sample.md`\n\nReplacement:\n```markdown\nstale text\n```\n",
+            None,
+            "header\nupdated text\n",
+            1,
+        ),
+        (
+            "active unpromoted plan Replacement block was wrongly flagged before promotion",
+            "Status: active\nPromotion Baseline Identifier: pending\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nunpromoted text\n```\n",
+            _probe_row("`active`"),
+            "baseline text\n",
+            0,
+        ),
+        (
+            "draft plan unpromoted Replacement block was wrongly flagged",
+            "Status: draft\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nunpromoted text\n```\n",
+            _probe_row("`draft`"),
+            "baseline text\n",
+            0,
+        ),
+        (
+            "completed index row overrode stale active header and skipped historical Replacement block",
+            "Status: active — promoted\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nolder historical text\n```\n",
+            _probe_row("`completed` — landed"),
+            "later evolved text\n",
+            0,
+        ),
+        (
+            "active promoted index row overrode stale draft header and caught diverging Replacement block",
+            "Status: draft\nPromotion Baseline Identifier: `296a7cd`\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nstale text\n```\n",
+            _probe_row("`active` — promoted"),
+            "updated text\n",
+            1,
+        ),
+        (
+            "unindexed plan with Replacement block when README exists failed instead of falling back to Status header",
+            "Status: completed\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nexact text\n```\n",
+            "",
+            "exact text\n",
+            1,
+        ),
+        (
+            "row outside Status Index section was ignored and did not mark active promoted plan completed",
+            "Status: active — promoted\n\nReplacement (`docs/specifications/01-sample.md`):\n```markdown\nstale text\n```\n",
+            "RAW:# Plans\n\n## Example\n\n"
+            + _probe_row("`completed`")
+            + "\n"
+            + PROBE_INDEX_HEADER
+            + _probe_row("`active` — promoted"),
+            "updated text\n",
+            1,
+        ),
+        (
+            "active promoted plan Replacement label without fenced block passed",
+            "Status: active — promoted\n\nReplacement (`docs/specifications/01-sample.md`):\nnot a fence\n",
+            None,
+            "not a fence\n",
+            1,
+        ),
+        (
+            "promoted weft row with a matching Replacement block was wrongly flagged",
+            "Status: promoted\n\nPromotion baseline identifier: pending\n\n"
+            + weft_block
+            + "exact spec line\n```\n",
+            _probe_row("`promoted`"),
+            "header\nexact spec line\nfooter\n",
+            0,
+        ),
+        (
+            "promoted weft row with a diverging Replacement block passed",
+            "Status: promoted\n\nPromotion baseline identifier: pending\n\n"
+            + weft_block
+            + "stale plan text\n```\n",
+            _probe_row("`promoted`"),
+            "header\nupdated spec text\nfooter\n",
+            1,
+        ),
+        (
+            "draft weft row with a diverging Replacement block was wrongly flagged",
+            "Status: draft\n\n" + weft_block + "stale plan text\n```\n",
+            _probe_row("`draft`"),
+            "header\nupdated spec text\nfooter\n",
+            0,
+        ),
+        (
+            "completed weft row with a diverging Replacement block was wrongly flagged",
+            "Status: completed\n\n" + weft_block + "older historical text\n```\n",
+            _probe_row("`completed`"),
+            "later evolved text\n",
+            0,
+        ),
+        (
+            "plan with a Replacement block absent from a weft Status Index passed",
+            "Status: promoted\n\n" + weft_block + "exact spec line\n```\n",
+            _probe_row("`promoted`", "2026-11-01-other-plan.md"),
+            "exact spec line\n",
+            1,
+        ),
+        (
+            "hub bare-shape row was read as indexing the plan in a weft README",
+            "Status: promoted\n\n" + weft_block + "exact spec line\n```\n",
+            f"| {PROBE_PLAN} | promoted |\n",
+            "exact spec line\n",
+            1,
+        ),
+        (
+            "row under the old ## Index heading was read as the Status Index",
+            "Status: promoted\n\n" + weft_block + "exact spec line\n```\n",
+            "RAW:# Plans\n\n## Index\n\n"
+            + "| File | Title | Status | Superseded by |\n| --- | --- | --- | --- |\n"
+            + _probe_row("`promoted`"),
+            "exact spec line\n",
+            1,
+        ),
+    ]
+    for name, plan_text, index_row, spec_text, expected in plan_probes:
+        with tempfile.TemporaryDirectory() as td:
+            root = _build_probe_tree(td, spec_text=spec_text)
+            plans_dir = root / "docs" / "plans"
+            plans_dir.mkdir(parents=True, exist_ok=True)
+            if index_row is not None:
+                readme_content = (
+                    index_row[4:]
+                    if index_row.startswith("RAW:")
+                    else PROBE_INDEX_HEADER + index_row
+                )
+                (plans_dir / "README.md").write_text(readme_content)
+            (plans_dir / PROBE_PLAN).write_text(plan_text)
+            if quiet_check(root, set()) != expected:
+                failures.append(name)
+
     if failures:
         for f in failures:
             print(f"check-doc-paths self-test FAIL: {f}")
         return 1
-    print("check-doc-paths self-test: all claim, fence, and allowlist probes pass")
+    print(
+        "check-doc-paths self-test: all claim, fence, allowlist, and plan-replacement sync probes pass"
+    )
     return 0
 
 
```

Probe disposition: the hub's ten plan-replacement probes are all kept. The
four that need no README (header fallback) are unchanged apart from the
docs/specs/ → `docs/specifications/` path adaptation. The six that write
a README have their rows adapted to Weft's shape (built by `_probe_row`,
header `| File | Title | Status | Superseded by |`); their status words and
expectations are unchanged. Seven Weft probes are added: promoted row with a
matching block (0), promoted row with a diverging block (1), draft row (0),
completed row (0), unindexed plan with a block (1), a hub bare-shape row is
not read as indexing the plan (1), and a row under the old `## Index`
heading is not read (1). The parser reads Weft rows only; the hub's bare
`| <plan>.md | <status> |` shape is deliberately not accepted.

##### Staged text — tests/specs/test_plan_metadata.py

The status set gains `promoted`:

Replacement (`tests/specs/test_plan_metadata.py`):
```python
ALLOWED_STATUSES = frozenset(
    {
        "completed",
        "draft",
        "promoted",
    }
)
```

The heading assertion follows the rename (line 61):

Replacement (`tests/specs/test_plan_metadata.py`):
```python
def test_plan_readme_exists_and_defines_status_taxonomy() -> None:
    readme_text = README_PATH.read_text(encoding="utf-8")

    assert "## Status Taxonomy" in readme_text
    assert "## Status Index" in readme_text
```

##### Staged text — docs/plans/README.md

The `promoted` taxonomy entry and the `## Index` → `## Status Index` rename.
The plan rows are not touched here; at task 13 this plan's row and header
`Status:` move to `promoted` together (fork 3).

Replacement (`docs/plans/README.md`):
```markdown
## Status Taxonomy

- `completed`: the planned slice landed and still describes current behavior or tooling.
- `draft`: the plan is an active implementation slice or meta-plan that has
  not landed yet, or a retained historical draft whose `Superseded by` column
  points to the successor plan.
- `promoted`: the plan is an active slice whose labelled Replacement blocks
  have been applied to their target files; while the plan is `promoted`,
  `bin/check-doc-paths` checks every labelled Replacement block for text
  containment in its target. The plan moves to `completed` when it closes.

## Status Index

| File | Title | Status | Superseded by |
```

##### Staged text — docs/agent-context/runbooks/writing-plans.md

The metadata rule (the clause that read "`Status` must be either `draft` or
`completed`."):

Replacement (`docs/agent-context/runbooks/writing-plans.md`):
```markdown
- `Status` must be one of `draft`, `promoted`, or `completed`. `promoted`
  marks an active plan whose labelled Replacement blocks have been applied
  to their target files; it lasts until the plan closes as `completed`.
```

The lifecycle vocabulary clause (the clause that read "valid statuses are
exactly `completed` and `draft`"); the rest of the bullet is unchanged:

Replacement (`docs/agent-context/runbooks/writing-plans.md`):
```markdown
- **The status vocabulary is closed, and the index is gated.** This
  repo's valid statuses are exactly `completed`, `draft`, and `promoted`,
  declared in the `## Status Taxonomy` section of `docs/plans/README.md`
  and enforced by `tests/specs/test_plan_metadata.py`: every plan carries a
```

#### Not propagated

| Hub hunk (`296a7cd..e6ac579`) | Reason |
|---|---|
| `docs/program-theory.md` heading rename "How theory relates to specs and process" → "How theory, specs, and plans relate" | No Weft counterpart heading. Weft carries the rules inside [WT-0] and gets no new heading. |
| `docs/program-theory.md` [AT-THEORY-0] lead sentence "The primer (`[AT-REF-3]`) defines the directional triad ..." | Replaced by the inline spec 02 triad (no primer in Weft; see section 1a). Its second clause is kept. |
| `docs/program-theory.md` [AT-THEORY-1] / [AT-THEORY-2] emphasis edits (`**this repository**` → plain; `**local**`/`**global**` → italics) | Hub-only prose; no Weft counterpart sentence. |
| `docs/program-theory.md` [AT-THEORY-2.1] module-theory paragraph, blockquote, and table row "When is it written?" | Weft's theory has no module-theory section. The Weft carrier is the AGENTS.md **Module theory** paragraph (staged in section 5) and the skill (staged in section 2). |
| `docs/program-theory.md` [AT-THEORY-4] item 6 (identity invariants and two-part negative knowledge) and item 9 (extend by scope only for weakly coupled modules) | Item 6 is superseded by plan 1 Slice B ([WT-2a], the task 8 reconciliation of whole-shape rows against [WT-2a], [WT-6] Kind tags). Item 9 has no Weft counterpart principle. |
| `docs/program-theory.md` `[REV-AT-006]` record | Hub record. Weft gets its own `[REV-WT-004]` stub (section 1c). |
| `../agent-theory/docs/specs/02-…` "The four-way balance of a written program theory" section (incl. PEP 20 contrast) | No Weft primer. The audit-table concept enters through the skill §7 and the [WT-7] habit. The four-way balance itself is Slice B's subject (superseded by plan 1 Slice B). |
| `../agent-theory/docs/specs/02-…` "Extending the model with module theory" paragraph | No Weft primer. The same rule is carried by the AGENTS.md and skill text. |
| `../agent-theory/docs/specs/02-…` surfaces table Specifications row ("contract invariants are in the code right now") and Program-theory/Plans first-column labels | The brief and plan §4c item 2 scope the change to two answer cells. The Specifications-row change would revise Weft's "exact intended behavior now" wording, so it is flagged for the plan author rather than staged. |
| `../agent-theory/docs/specs/02-…` "Note:" items 1–2 after the triad | Duplicate the four rules staged in [WT-0] (already present after application). |
| `../agent-theory/docs/specs/02-…` and `../agent-theory/docs/specs/01-…` Related Plans lines | Hub plan backlinks; no Weft counterpart. |
| `../agent-theory/docs/specs/01-…` [DOM-2] program-theory and module-theory paragraphs, [DOM-3] startup paragraph | No Weft spec counterpart (Weft has no DOM spec). The same sentences reach Weft only through AGENTS.md (staged). Weft's `docs/agent-context/README.md` read-order item 1 still says "does not override winning contracts". That file is outside the Slice A hub delta; the clause swap is now part of task 4 and is staged in the `docs/agent-context/README.md` section above. |
| `../agent-theory/docs/specs/01-…` [DOM-11] review-loop closing clause ("do not run follow-up external review rounds merely to re-verify non-blocking edits") | Per the mapping, [DOM-11] → the Weft review-loops runbook. That runbook carries the rule in the Round-2 variant (staged in section 3), and the DOM sentence itself has no Weft carrier sentence. |
| `README.md` documentation table rows (Program theory; Product specs; Plans) | Weft's README has no such table. The wording is used for the [WT-0] table only where spec 02 matched (section 1a). |
| `AGENTS.md` read-order Stub/Draft sentences ("If Status is `Stub` ... Product-scope Class 5 work requires at least a current Draft account ...") | Weft's AGENTS item 1 never carried them (Weft's theory is Draft, not a Stub). Adding them would be a new Weft rule, not a propagation. |
| `AGENTS.md` "Rules and checklist: `../agent-theory/docs/specs/01-…` (`[DOM-2]`), `../agent-theory/docs/specs/02-…` (`[AT-REF-4]`)" | Targets do not exist in Weft and would fail the gate. Weft's skill pointer is kept. |
| `AGENTS.md`/skill "or docs/implementation/" | Weft has no docs/implementation/, and the gate scans both surfaces. |
| skill "Spec writing: `../agent-theory/docs/agent-context/runbooks/writing-specs.md`" and "Operating model"/"Definitional primer" lines | No Weft counterpart (see section 2). |
| `bin/check-doc-paths` hub docstring / `--scaffold` (unchanged in this delta) and hub `SCAN_DIRS`/`SCAN_FILES` | Not part of the delta, or kept as Weft's per the brief. |
| `CHANGELOG.md`, `docs/plans/README.md`, hub plan file | Outside the brief's file list. Hub housekeeping. |

#### Verification run

Commands and outputs (abridged where noted):

```text
$ git -C /Users/van/Developer/agent-theory log --oneline -3 e6ac579
e6ac579 Update agent theory to better define roles
296a7cd Add whole-plan review lenses, promote test-audit skill, and clarify taste-up-front boundary
b6f2141 Back-port 2026-10-14 propagation wave adaptation refinements

$ git -C /Users/van/Developer/agent-theory diff --stat 296a7cd..e6ac579 -- <9 brief files>
 AGENTS.md                                          |  22 +-
 README.md                                          |   6 +-
 bin/check-doc-paths                                | 275 ++++++++++++++++++++-
 .../runbooks/review-loops-and-agent-bootstrap.md   |  12 +-
 docs/agent-context/runbooks/writing-plans.md       |   8 +-
 docs/program-theory.md                             | 142 ++++++++---
 ...01-development-documentation-operating-model.md |  31 ++-
 docs/specs/02-agent-theory-and-program-theory.md   | 106 +++++++-
 skills/crystallize-program-theory/SKILL.md         |  86 +++++--
 9 files changed, 589 insertions(+), 99 deletions(-)

$ ls -la CLAUDE.md
lrwxrwxr-x@ 1 van  staff  9 Feb 10  2026 CLAUDE.md -> AGENTS.md

$ grep hits on the hub skill at e6ac579 (plan task 2/3 verify strings)
Identity invariants: 0   theory-versus-code: 1   escaped the product's law: 0
never to re-check non-blocking edits (runbook, single line): 0

$ ./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -p no:cacheprovider -n0
....                                                                     [100%]
4 passed in 0.06s
$ python3 -c "print('## Index' in '## Status Index')"
False

$ python3 bin/check-doc-paths            (current Weft gate, live tree)
check-doc-paths: OK (/Users/van/Developer/weft)
$ python3 bin/check-doc-paths --self-test
check-doc-paths self-test: all claim, fence, and allowlist probes pass
$ python3 <scratch>/check-doc-paths --self-test
check-doc-paths self-test: all claim, fence, allowlist, and plan-replacement sync probes pass
exit=0
$ python3 <scratch>/check-doc-paths --root /Users/van/Developer/weft   (cwd = Weft root)
check-doc-paths: OK (/Users/van/Developer/weft)
exit=0
$ python3 <scratch>/check-doc-paths   (cwd = Weft root, no --root)
check-doc-paths: OK (<scratch>)
exit=0

$ python3 <scratch>/check-doc-paths.weft-lint --self-test
check-doc-paths self-test: all claim, fence, allowlist, and plan-replacement sync probes pass
$ python3 <scratch>/check-doc-paths.weft-lint --root /Users/van/Developer/weft
check-doc-paths: OK (/Users/van/Developer/weft)
$ ruff check / ruff format --check / mypy on the lint variant
All checks passed! / 1 file already formatted (after one ruff format pass) / Success: no issues found in 1 source file

$ dry run of task 13 on work/weft-tree (rsync of docs skills bin tests AGENTS.md + CLAUDE.md symlink; skill replaced; simulate.py applied every block)
contained a1_wt0_rules.md in docs/program-theory.md: True
contained a2_wt0_table.md in docs/program-theory.md: True
contained b1_wt7_probe.md in docs/program-theory.md: True
contained b2_wt7_falsifiers.md in docs/program-theory.md: True
contained c_rev004.md in docs/program-theory.md: True
contained r_roundtwo.md in docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md: True
contained w_writingplans.md in docs/agent-context/runbooks/writing-plans.md: True
contained e1_readorder.md in AGENTS.md: True
contained e2_module.md in AGENTS.md: True
CLAUDE.md == AGENTS.md: True
check-doc-paths: OK (<work>/weft-tree)          (current gate)   exit=0
check-doc-paths: OK (<work>/weft-tree)          (extended gate)  exit=0
altitude presence grep hits: 14 before, 14 after (no new hits; existing ones are Slice B task 10's)
task-4 awk diff CLAUDE.md vs AGENTS.md: empty
grep -c 'never to re-check' review-loops runbook: 1
no file under docs/specifications/ touched

$ end-to-end: this file copied to work/weft-tree/docs/plans/2026-11-02-zz-staged.md
  with header "Status: active — promoted" (README moved aside so the header is read),
  extended gate run, failures naming the copy:
check-doc-paths.weft-lint: 0      check-doc-paths (verbatim): 0
  negative control (one word changed in block a1):
  docs/plans/2026-11-02-zz-staged.md:49: Replacement block for `docs/program-theory.md` does not match target file
```

The dry-run artifacts named in this section (the verbatim and lint-clean gate
copies, the gate diffs, the staged skill and its diff against the hub, the
per-block files, the simulation script, and the scratch tree `work/weft-tree/`)
are session scratch and are not repository files.

`docs/agent-context/context.index.yaml` (task 6): it has no skill entries and no
per-entry timestamps. The runbook entries carry only `path`/`role`, and no
inventory changes, so only `updated_at: 2026-10-15` would bump at task 13.

### Proposed Theory Delta (drafted) — Slice B, tasks 7 to 10

Plan: `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`.
Baseline: Weft `ec50bd57`, `docs/program-theory.md` as committed (868 lines).
Status: draft review target. Nothing here has been applied to the repository.
Every labelled Replacement block below is ready to paste into
`docs/program-theory.md`; in-row table spans are located by quoting their
opening and closing words.

### Execution log

#### Comprehension answers (written before the first edit)

1. The specification changes, through a planned change (a cleanup-series
   plan that rewrites [MF-3]'s `ctrl_out` acknowledgement clause and the code
   behind it). The theory sentence "push, never pull-scan" stays as it is and
   becomes that plan's acceptance test. Under the owner's 2026-10-09 rule the
   theory does not bend to ratify the current spec. This is mismatch kind 1
   (the code lags), not a theory edit.
2. The existing [WT-7] entry is **"Blunt local mechanism vs. classical CS
   model"**. It is about *proposals*: an agent invents a lane, a timer, a
   guessed charge, or an unconsumed table to patch a symptom. Altitude
   collapse is about the *theory text itself* sitting at the wrong height,
   either too concrete (paths, figures) or too abstract (two designs could
   cite it). The two are distinct falsifiers. Slice B must cross-reference
   them, not merge them or restate one inside the other.
3. Before a subsystem gets a module theory, check whether it has escaped the
   product's law. That means it has invented competing concepts, a private
   ontology, or a second control plane. If so, simplify it back under the
   product theory instead of writing it a theory. This repository's recorded
   position (owner, 2026-11-01) is one central theory and no module theories.
   That position goes into [WT-4] as a whole-shape row whose contract is the
   [WT-7] module-divergence falsifier.
   [Historical answer. The position it cites was withdrawn from this plan's
   scope by the owner on 2026-11-02; question 3 now asks only about the
   hub's module-divergence check (named at `e6ac579`).]

These match the expected answers in plan §3. No re-read was needed.

#### Counts (command and output)

[WT-4] principle rows before (baseline file):

```text
$ awk '/^## Design principles \[WT-4\]/{f=1} /^## What Weft is not \[WT-5\]/{f=0} f' docs/program-theory.md | grep '^| ' | grep -v '^| Principle |' | grep -vc '^|---'
30
$ awk '/^### 4\./{s=$2} /^## What Weft is not/{exit} s && /^\| / && !/^\| Principle \|/ && !/^\|---/{c[s]++} END{for(k in c) print k, c[k]}' docs/program-theory.md | sort
4.1 7
4.2 6
4.3 6
4.4 11
```

[WT-4] principle rows after, recounted on 2026-11-02 after the owner's
scope direction withdrew the module-theory row, over the replacement tables
extracted from this plan file:

```text
$ awk '/^#### 4\.1 Ontology and Queue Mechanics/{f=1} f && /^```$/{exit} f' docs/plans/2026-11-02-program-theory-reconciliation-plan.md > wt4.md
$ grep '^| ' wt4.md | grep -v '^| Principle |' | grep -vc '^|---'
30
$ awk '/^#### 4\./{s=$2} s && /^\| / && !/^\| Principle \|/ && !/^\|---/{c[s]++} END{for(k in c) print k, c[k]}' wt4.md | sort
4.1 7
4.2 6
4.3 6
4.4 11
$ grep -c '^| [^|]* | whole-shape |' wt4.md
12
$ grep -c '^| [^|]* | judgment |' wt4.md
18
$ grep -c '<!-- altitude -->' wt4.md
3
```

[Historical record of the Slice B draft. After the 2026-11-02 realignment
to hub `e6ac579`, the Kind column is not applied; task 8 reconciles the
twelve whole-shape rows against [WT-2a] instead.]

The principle row count was unchanged at 30 by the Kind-column draft (a column, not rows); after the 2026-11-02 owner-endorsement additions it is 31, the added row being "Observable with standard tools" (audit row 4.4-l). The Kind column adds a column,
not rows. Every existing cell is byte-identical except the three cells marked
`<!-- altitude -->`. To check this, strip the Kind column from the draft
tables and diff against the baseline:

```text
$ awk '/^### 4\.1/{f=1} /^## What Weft is not/{f=0} f' docs/program-theory.md > wt4_orig.md
$ sed -E 's/^#### /### /; s/^(\| [^|]+) \| (whole-shape|judgment|Kind) \| /\1 | /; s/^\|-----------\|------\|/|-----------|/' wt4.md | diff wt4_orig.md - | grep -E '^[0-9]'
29c29   (A quiet task does nothing — altitude)
33c33   (Ask who owns this lifetime — altitude)
48c48   (The client mirrors the CLI — altitude)
```

[WT-6] tags (recounted after the round-1 disposition: only `Kind:` lines
are added; no `Reopen when:` line), over the tags section extracted from
this plan file:

```text
$ awk '/^### \[WT-6\] tags/{f=1} /^#### \[ALT-WT-015\] stale/{f=0} f' docs/plans/2026-11-02-program-theory-reconciliation-plan.md > alt.md
$ grep -c '^### \[ALT-WT-' docs/program-theory.md
21
$ grep -c '^- Reconsider when:' docs/program-theory.md
21
$ grep -c '^- Kind:' alt.md
21
$ grep -c '^- Reopen when:' alt.md
0
$ grep -c '^- Kind: tried and discarded' alt.md
15
$ grep -c '^- Kind: boundary refusal' alt.md
6
$ grep -c '^- Proposed edit to the existing `Reconsider when:` line' alt.md
2
$ diff <(grep -oE '^### \[ALT-WT-[0-9]+\]' docs/program-theory.md | sed 's/^### //' | sort) <(awk '/^##### \[ALT-WT-/{id=$2} /^- Kind:/{print id}' alt.md | sort) && echo sets-match
sets-match
```

After the delta is applied, the task 9 gate compares identifier sets (the
set of `[ALT-WT-*]` headings equals the set of entries carrying a `Kind:`
line) and checks that `grep -c '^- Reopen when:' docs/program-theory.md` is
`0`. Today `- Kind:` appears nowhere in the file
(`grep -c '^- Kind:' docs/program-theory.md` → `0`).

Reopening conditions: all 21 entries already carry a `Reconsider when:` line,
and that line stays the single reopening condition. No entry needed a newly
written condition. Two entries carry a proposed edit to the existing
`Reconsider when:` line that tightens it without weakening it, flagged for
the owner:

- [ALT-WT-003] adds "pushed to a queue the requester already watches, never
  found by scanning the target's queues". This spells out "follows the same
  shape" in the [WT-4] "It is just a message" wording. It does make the
  STOP/KILL acknowledgement scan a drift candidate for Slice C, which plan §5
  task 14 already expects.
- [ALT-WT-010] adds "measured on both SQLite and PostgreSQL", taken from the
  [WT-4] "Measure; do not bake in inefficiency" row.

#### Spec codes cited in this draft (resolution check)

Every spec code in this draft was extracted mechanically, plus the four codes implied by the [QUEUE.1]–[QUEUE.6] range, and counted against `docs/specifications/`. The match is anchored, so `IMPL.1` does not match `IMPL.10`. A count of 1 or more means the code resolves.

```text
$ grep -oE '\[(IMMUT|STATE|QUEUE|OBS|IMPL|LIVENESS|MANAGER)\.[0-9A-Za-z.]+\]|\b(IMMUT|STATE|QUEUE|OBS|IMPL|MANAGER)\.[0-9]+[a-z]?\b|LIVENESS\.R[0-9]+|\[(SB|CLI|CC|TS|AR|MA|MF|PL|PY|RM)-[0-9][0-9A-Za-z.]*\]' delta-draft.md | tr -d '[]' | sed 's/\.$//' | sort -u   # + QUEUE.2-5
$ while read c; do pat=$(printf '%s' "$c" | sed 's/\./\\./g'); grep -rhoE "(\[|\*\*|[^A-Za-z0-9.-])${pat}([^0-9A-Za-z.]|\.[^0-9A-Za-z]|$)" docs/specifications/ | wc -l; done
AR-0.0       1
AR-5         1
AR-6         1
AR-8         1
AR-9         1
CC-2.2.1     2
CC-2.4       5
CC-2.4.1     2
CC-2.5       3
CC-3.1       2
CC-3.2       3
CC-3.4       2
CLI-1.1.1    1
CLI-1.2.3    2
IMMUT.1      1
IMMUT.2      1
IMMUT.3      1
IMMUT.4      1
IMPL.1       1
IMPL.10      3
IMPL.11      5
IMPL.5       1
IMPL.6       1
IMPL.8       2
LIVENESS.R3  7
MA-1         4
MA-1.6       1
MA-1.6a      3
MA-1.8       18
MA-3         3
MANAGER.3    2
MF-1         3
MF-3         4
MF-3.1       2
MF-3.2       6
MF-4         2
MF-5         34
OBS.1        1
OBS.13       2
OBS.15       1
OBS.16       1
OBS.17       1
OBS.2        1
OBS.4        2
OBS.5        4
OBS.6        11
OBS.7        2
PL-1.1       1
PY-1         6
PY-2         4
QUEUE.1      1
QUEUE.2      1
QUEUE.2a     1
QUEUE.3      1
QUEUE.4      1
QUEUE.5      2
QUEUE.6      5
QUEUE.7      4
QUEUE.8      6
RM-5.2       2
SB-0.1       1
SB-0.2       19
SB-0.4       7
SB-0.4a      4
STATE.1      1
STATE.2      1
TS-1         3
TS-1.3       1
TS-1.4       1
$ awk '$2==0' resolve.txt
(no output: all 69 codes resolve)
```

Theory identifiers cited ([WT-0]–[WT-9], [ALT-WT-001]–[ALT-WT-021], [REV-WT-001]–[REV-WT-003]) resolve in `docs/program-theory.md` (`grep -c '^### \[ALT-WT-' docs/program-theory.md` → 21; `grep -c '\[WT-[0-9]\]' docs/program-theory.md` → 14). New identifiers introduced: [WT-2a], [WT-2a.1]–[WT-2a.9] (and [WT-2a.10] only if Fork 2 item 1 is admitted). `grep -c 'WT-2a' docs/program-theory.md` → 0, so no collision.

#### Out-of-scope observations (not changed by this draft)

- Altitude scope: the plan body §4 rule governs evidence lines and
  sections outside [WT-2]/[WT-3]/[WT-4]/[WT-8]; see the "Altitude pass"
  section below.
- Three [WT-2]/[WT-4] statements disagree with the tree in a way that is
  drift (kind 1), not altitude (kind 2), so they are not rewritten here.
  They belong in the Slice C audit table:
  - "Even a short-lived CLI requester (`weft status`, `PING`, result waiter)
    mints a TID" ([WT-2] item 1, [WT-4] "Everything is a task"). In the
    tree, STOP/KILL requesters mint `uuid4().hex` request IDs, at
    `weft/commands/run.py` lines 529 and 550 (`grep -n 'uuid4' weft/commands/run.py`).
  - "push, never pull-scan" ([WT-4] "It is just a message") versus the
    STOP-ack scan in `weft/commands/run.py::_matching_stop_ack`.
  - "updates the bucket's contracted scheduling-parent pointer in $O(1)$"
    ([ALT-WT-016]) versus the meld-on-retire in
    `weft/core/family_scheduling.py`. Classify this one in Slice C; it is
    outside this draft's altitude scope.
- The module-theory policy question was withdrawn from this plan's scope
  by the owner on 2026-11-02; the hub module-theory clause propagates as hub text
  only.
- CLAUDE.md/AGENTS.md section 3 "NEVER break" also lists "Reserved queue
  policy must be honored" and "Process titles must be shell-safe". These are
  contract invariants ([QUEUE.6], [OBS.4], [OBS.7]), not identity
  invariants. When §3 is repointed at [WT-2a], those two lines should point
  at spec 07, not at [WT-2a].

#### Round 6 wording checks (F14, F19)

- [EXEC.1] exact wording (`docs/specifications/07-System_Invariants.md`
  lines 260 to 261): "each work item is executed exactly once per
  successful reservation path". The contract says "path", so [WT-2a.7] and
  audit row I-11 use "per successful reservation path".
- [MF-2] crash residue (`docs/specifications/05-Message_Flow_and_State.md`
  line 166): "crash leaves the message in reserved state for explicit
  operator recovery; no policy is applied at restart." [WT-2a.7] cites it
  for the crash case.
- [MA-3] explicit stop (`docs/specifications/03-Manager_Architecture.md`
  lines 801 to 805): STOP, `SIGTERM`, and `SIGINT` enter one drain path that
  broadcasts STOP to children and escalates; fired by
  `tests/core/test_manager.py::test_manager_stop_command_drains_nonpersistent_children`
  (line 8318). This is the counter-evidence that led to the owner's
  2026-11-02 STOP rule in [WT-2a.13] (§9 item 5).

### [WT-2a] Identity invariants (new section, insert between [WT-2] and [WT-3])

Insertion point: after the [WT-2] item 6 bullet that ends "not the agent
planner or policy engine." (or its altitude replacement below), and before
the heading `## Core concepts and ownership [WT-3]`.

Evidence table for the reviewer. It is not pasted into the theory. Its IDs
are the pre-endorsement draft numbering; the owner's 2026-11-02 endorsement
renumbered the list to fourteen (old .7, .8, .9, and the trust-domain row
are now .8, .9, .10, and .11; .7 and .12 to .14 are new). The audit table
carries the evidence for the endorsed numbering, with its map note.

| ID | Candidate (plan §4c item 3) | Contract counterpart | Firing evidence in the tree today |
|----|-----------------------------|----------------------|-----------------------------------|
| [WT-2a.1] | One invertible task identity | [IMMUT.3]; `04-SimpleBroker_Integration.md` [SB-0.2] | `tests/specs/manager_architecture/test_tid_correlation.py::test_spawn_request_timestamp_matches_tid`. Covers minting and preservation only. The "every rendering is lossless" clause has no firing evidence and is a known drift candidate (plan 12); see Fork 2 candidates. |
| [WT-2a.2] | Queues are the only durable truth | [QUEUE.1]–[QUEUE.6], [OBS.1], [OBS.2] | `tests/commands/test_retained_result_history.py::test_task_snapshot_preserves_custom_result_after_raw_history_retirement` (ordinary `result` and realtime events succeed with every `MonitorStore` read patched to fail) |
| [WT-2a.3] | Forward-only lifecycle | [STATE.1], [STATE.2] | `tests/specs/taskspec/test_state_transitions.py::test_terminal_state_is_immutable` |
| [WT-2a.4] | `spec` and `io` frozen after creation | [IMMUT.1], [IMMUT.2], [IMMUT.4] | `tests/taskspec/test_taskspec.py::TestPartialImmutability::test_spec_assignment_blocked` and `::test_io_assignment_blocked` |
| [WT-2a.5] | Spawn isolation for every Weft runtime process | [IMPL.5], [IMPL.6] | [IMPL.5]: `tests/core/test_client.py::test_client_fork_reset_uses_child_bounded_state` (child closes inherited broker state and opens a fresh session). [IMPL.6]: production spawn contexts at `weft/core/launcher.py:202` and `weft/core/runners/host.py:465` (`grep -n 'get_context("spawn")' weft/core/launcher.py weft/core/runners/host.py`); no test asserts the start method (`grep -rn 'get_start_method\|start_method' tests/` → no output). Native commands run through the subprocess runner are ordinary subprocesses and outside this invariant. |
| [WT-2a.6] | One reactor, one drive-owning thread per task | [IMPL.8], [IMPL.10], [IMPL.11] | `tests/tasks/test_task_execution.py::test_base_task_process_once_rejects_a_second_drive_thread_before_policy` (line 191; `grep -n 'def test_base_task_process_once_rejects' tests/tasks/test_task_execution.py` → `191:`). The [IMPL.10] mapping also lists firing tests in `tests/tasks/test_task_execution.py` and `tests/core/test_manager.py`. The TaskMonitor maintenance exception is [IMPL.8]'s "declared exception" and [IMPL.11]. |
| [WT-2a.7] | SimpleBroker owns queue mechanics | [WT-5]; [SB-0.1], [SB-0.4] | Spec section with implementation mapping: `04-SimpleBroker_Integration.md` [SB-0.1] ("Weft queue commands delegate to SimpleBroker rather than reimplementing queue semantics"), with the file-level `_Implementation mapping_`. Also `tests/specs/test_command_queue_seam.py::test_command_layer_direct_queue_construction_is_explicitly_bounded`. |
| [WT-2a.8] | No compatibility code for changed contracts | [ALT-WT-012]; `00-Overview_and_Architecture.md`, Single current contract | `tests/tasks/test_runner.py::test_runner_handle_rejects_legacy_shape`; also `tests/commands/test_dump_load.py::test_cmd_load_rejects_legacy_weft_dump_format` |
| [WT-2a.9] | Weft is a Unix citizen | [CLI-1.1.1]; `02-TaskSpec.md` [TS-1] ("Command tasks build argv as `[process_target] + args`", mapped to the targets module); [IMPL.1] | `tests/specs/taskspec/test_process_target.py::test_command_args_appended_to_process_target` (argv construction); `tests/cli/test_cli_run.py::test_cli_run_reads_stdin`; `tests/cli/test_cli_result.py::test_result_terminal_timeout_uses_exit_124`. The negative half ("never through a shell") has no firing test. The rejection exists only as code (`weft/commands/submission.py` raises `NotImplementedError` for `shell=True`; `grep -rn 'shell=True' tests/` → no output). See Fork 2 candidates. |
| — | Single operator trust domain | [WT-5]; `00-Overview_and_Architecture.md` "Observability and Security" | None found. Moved to Fork 2 candidates. |

Replacement markdown to insert (one block, the whole section):

Replacement (`docs/program-theory.md`):
```markdown
## Identity invariants [WT-2a]

These are the properties that make a program Weft. Each one would still
measure a replacement implementation written from scratch: if a rewrite
broke one, the rewrite would be a different system, however well it passed
today's tests. This section states each invariant once, at that altitude.
The exact contract text, its edge cases, and its implementation mapping live
in `07-System_Invariants.md` and the other specifications named in brackets.
This section points at them and does not restate them. `CLAUDE.md` §3 and
`AGENTS.md` §3 point here for identity and to spec 07 for the remaining
contract invariants. A proposal that breaks an invariant here is a class-5
theory revision, not a spec edit.

- **[WT-2a.1] One task identity, rendered losslessly.** Every unit of work
  has exactly one identity: a SimpleBroker hybrid timestamp minted from the
  broker's timestamp generator, used as the spawn-request message ID for
  spawned work, and carried unchanged for the task's whole life. Every
  rendering of it, in a queue name, a process title, a log line, or a CLI
  argument, decodes back to the same TID ([IMMUT.3], [SB-0.2]). (Owner
  decision, 2026-11-02; the ten-character form in [OBS.5] is retired by the
  TID display-encoding plan.)
- **[WT-2a.2] Queues are the only durable truth.** A Weft lifecycle or
  coordination fact is durable only as a SimpleBroker queue row; process
  memory holds hints. A task's private working state exists only to do
  that task's own job ([ALT-WT-020]), and no Weft execution, control,
  status, or result path depends on it. [OBS.2] states that Weft keeps no
  separate lifecycle store, alongside [OBS.1] ([QUEUE.1]–[QUEUE.6]).
- **[WT-2a.3] Forward-only lifecycle.** A task's lifecycle moves only
  forward, and a terminal state never returns to a non-terminal one
  ([STATE.1], [STATE.2]).
- **[WT-2a.4] Frozen execution definition.** What a task runs (`spec`) and
  how it is wired to queues (`io`) are fixed when the TaskSpec is created;
  only runtime state and metadata change afterward ([IMMUT.1], [IMMUT.2],
  [IMMUT.4]).
- **[WT-2a.5] Process isolation.** Every Weft runtime process starts from a
  fresh process image and opens its own broker state; no broker handle,
  connection, or descriptor crosses a process boundary ([IMPL.5], [IMPL.6]).
- **[WT-2a.6] One arbiter per reactor; reactors compose by messages.** Every
  reactor owner has exactly one arbiter that waits on a closed set of inputs
  and commits that owner's broker effects. A reactor may own child reactors.
  Parent and child exchange only immutable controls downward and immutable
  events upward over declared channels, each waking the other's arbiter, and
  no broker handle, waiter, or mutable state crosses an owner boundary. A
  task is the root owner of its reactor tree, and a parent never holds a
  child's private handles or mutable domain state; it reads only the facts
  the child publishes (its events, its queue rows, its state snapshots) and
  the lifecycle evidence the parent itself owns ([IMPL.8], [IMPL.10],
  [IMPL.11]; sibling
  realization: Taut [TAUT-8.5] and [MCP-8] over the shared
  `MultiQueueWatcher` copy). (Owner, 2026-11-02, after reviewing Taut's
  reactor over workspace reactors.)
- **[WT-2a.7] Exactly once per reservation.** A work item executes exactly
  once per successful reservation path; an unsettled reserved row persists
  independently of the process that took it until it is acknowledged or
  explicitly dispositioned, and its disposition when execution does not
  complete is the task's declared policy, keep or clear, never a guess
  ([EXEC.1], [QUEUE.4] to [QUEUE.6]; crash residue awaits explicit recovery,
  [MF-2]).
- **[WT-2a.8] SimpleBroker owns queue mechanics.** Message identity,
  ordering, atomic moves, delivery claims, and storage transactions belong
  to SimpleBroker; a queue-level need is met in SimpleBroker, never
  reimplemented in Weft, and SimpleBroker never depends on Weft ([WT-5],
  [SB-0.1], [SB-0.4]).
- **[WT-2a.9] One current contract.** Each Weft contract has exactly one
  current shape; a change updates every Weft-owned producer and consumer
  together, with at most a bounded forward migration for durable old data
  ([ALT-WT-012]; `00-Overview_and_Architecture.md`, Single current
  contract).
- **[WT-2a.10] A Unix citizen.** Weft behaves as a Unix tool: a command
  runs as an argv list, never through a shell; piped stdin reaches the task
  as data; and the exit code carries the outcome, with the code mapping
  fixed in [IMPL.1] ([CLI-1.1.1], [TS-1]). The owner stated the shell rule
  on 2026-11-02: "Weft does not support shell=True. The only way someone
  could get a shell is if they construct a task with a command line that
  creates a shell."
- **[WT-2a.11] One operator trust domain.** Weft trusts whatever the
  operating system let the current user place or reach; the operating
  system, filesystem permissions, and database grants decide who can. Weft
  validates shape and recognizes its own artifacts for correctness and
  portability, never as a trust boundary, and adds no in-band authentication
  or sanitizer that would imply one ([WT-5], [ALT-WT-014]; owner decision,
  2026-11-02).
- **[WT-2a.12] Everything is a task, and every protocol is a message.** Any
  Weft code that exchanges facts with a task does so as a task: it holds a
  TID from SimpleBroker's hybrid-timestamp generator and receives replies on
  its own `T{tid}.ctrl_in`. A protocol is an ordinary message on an ordinary
  queue, pushed to a queue its recipient already watches; no caller has a
  privileged side-channel ([MF-3]). (Owner, 2026-09-17: "In weft, everything
  is a task" and "There is no code without a task identity.")
- **[WT-2a.13] Independent lifetimes; death needs proof.** A shared Manager,
  each task it launches, and each waiting client have independent
  lifetimes: Manager supersession, idle exit, or crash changes who takes
  future work and never kills or moves running tasks. A system STOP is
  different: it is the Manager's responsibility to stop, suspend, or kill
  active tasks, then its internal services, then itself (owner,
  2026-11-02). Because no lifetime contains
  another, absence is never proof of death: a missing snapshot, an expired
  window, or a failed probe is `unknown`, and task-owned terminal proof
  outranks any supervisor's inference ([MA-1.6a], [MA-3], [MF-5],
  LIVENESS.R3 to LIVENESS.R7; [PY-2]).
- **[WT-2a.14] Submission never blocks.** Every submission, root or child,
  is accepted into the spawn-request queue without waiting for launch
  admission and costs nothing while it waits; only a running process
  consumes capacity ([MF-1]). Readiness work after acceptance, such as
  observing Manager availability or starting a Manager, is separate from
  acceptance and may take time. (Owner, 2026-10-14: "Tasks sitting waiting
  to be spawned do not consume processes. Only live processes do")
```

### [WT-4] whole-shape rows to reconcile against [WT-2a]

Note (2026-11-02, realignment to hub `e6ac579`): the `Kind` column itself
is not applied to [WT-4]. The hub's negative knowledge is two-part (what
Weft is not; paths tried and discarded), and whole-shape choices belong
with the identity invariants. This section is kept as the working
classification for task 8: the twelve rows marked `whole-shape` below are
the input to the reconciliation against [WT-2a] (each becomes, or already
is, a [WT-2a] invariant cited from its row's Contract cell, or stays a
principle with the mark dropped), and the eighteen `judgment` rows stay as
principles unchanged. The two drafted blocks in this section are the
superseded Slice B draft, kept as a record and unlabelled; only the
altitude cells they carried (rows 6 to 8 of the altitude pass) land, by
span, in the existing [WT-4] table. Task 8's output is the next sub-section.

Not applied (superseded Slice B draft): the draft replaced the span from
the heading `### 4.1 Ontology and Queue Mechanics` through the last 4.4
table row, which ends "| `docs/agent-context/decision-hierarchy.md` |",
immediately before `## What Weft is not [WT-5]`, and inserted this
sentence after the existing [WT-4] preamble sentence that ends 'names the
governing specification or engineering rule.':

```text
The "Kind" column separates whole-shape choices from judgment principles. A
row is `whole-shape` if changing it would make Weft a different system or
would invalidate more than one other row; otherwise it is `judgment`.
Whole-shape rows are part of Weft's negative knowledge alongside [WT-5] and
[WT-6].
```

Working table for task 8 (classification from the Slice B draft; the
outcome column is filled by task 8):

| Row | Kind | Why | Task 8 outcome |
|-----|------|-----|----------------|
| Everything is a task | whole-shape | Founding line ([WT-8]); every 4.1 row and the Manager-is-a-task model depend on it. | cites [WT-2a.12] |
| It is just a message | whole-shape | A privileged reply protocol would invalidate "Everything is a task" and "Address a fact to its observers". | cites [WT-2a.12] |
| Queues carry facts; processes carry hints | whole-shape | Identity invariant [WT-2a.2]; reversing it would invalidate the task-internal, consume-to-disappear, and Availability rows. | cites [WT-2a.2] |
| Task-internal state is private | judgment | A boundary refinement of the row above; reversing it breaks that one row. | stays a principle, unchanged |
| Consume-to-disappear queues | judgment | How Weft uses SimpleBroker queues; reversing it breaks [ALT-WT-019], not another [WT-4] row. | stays a principle, unchanged |
| Address a fact to its observers | judgment | An algorithm choice (multicast over shared-log scan). | stays a principle, unchanged |
| Keep an identity you already have | judgment | Local discipline. | stays a principle, unchanged |
| Classical CS models over blunt ad-hoc fixes | judgment | A method of reasoning, applied case by case. | stays a principle, unchanged |
| Queued work costs nothing; live processes cost | whole-shape | Queue-first unbounded submission is what makes Weft a queue substrate rather than a pool; reversing it would invalidate measured admission and the [WT-3] Admission row and would bring back [ALT-WT-001]. | cites [WT-2a.14] |
| Use the primitives you have | judgment | Local discipline. | stays a principle, unchanged |
| Push a concern down when it belongs lower | whole-shape | The SimpleBroker layer boundary ([WT-2a.8]). | cites [WT-2a.8] |
| Process concurrency, short transactions | whole-shape | Process isolation ([WT-2a.5]); threads would invalidate the reactor, liveness, and lifetime rows. | cites [WT-2a.5] |
| One owner for each resource | judgment | A discipline that each subsystem applies. | stays a principle, unchanged |
| One reactor, one arbiter, a closed set of inputs | whole-shape | Identity invariant [WT-2a.6]; the next three rows depend on it. | cites [WT-2a.6] |
| A quiet task does nothing | judgment | A consequence of the reactor row. | stays a principle, unchanged |
| Subprocess and stream supervision is readiness-driven | judgment | Applies the reactor row to runners. | stays a principle, unchanged |
| Bounded per-turn stream work and backpressure | judgment | Local flow-control rule. | stays a principle, unchanged |
| Availability and death are different questions | whole-shape | A timeout-means-dead model would invalidate custody, "Ask who owns this lifetime", and [WT-2] item 6's kernel-sentinel ownership. | cites [WT-2a.13] |
| Ask who owns this lifetime | whole-shape | Detached children surviving the Manager is a topology choice ([WT-2] item 3); reversing it would invalidate the runtime-state bridge and supersession. | cites [WT-2a.13] |
| As few concepts as possible | judgment | A test applied to each proposal. | stays a principle, unchanged |
| Shared function, shared code | judgment | Code-level discipline. | stays a principle, unchanged |
| The expert Python developer test | judgment | Named by the plan as judgment. | stays a principle, unchanged |
| No over-armoring | judgment | Named by the plan as judgment. | stays a principle, unchanged |
| Measure; do not bake in inefficiency | judgment | Evidence discipline. | stays a principle, unchanged |
| Tests prove correctness, nothing else | judgment | Test discipline. | stays a principle, unchanged |
| No capability diminution | judgment | Review rule for simplifications. | stays a principle, unchanged |
| No compatibility code | whole-shape | Named by the plan as whole-shape; identity invariant [WT-2a.9]. | cites [WT-2a.9] |
| One path on all platforms | whole-shape | Per-platform architectures would invalidate the reactor row and the readiness-supervision row. | stays a principle; whole-shape mark dropped (candidate refused, §9 item 4) |
| The client mirrors the CLI | judgment | A surface rule; reversing it breaks only "Shared function, shared code". | stays a principle, unchanged |
| Specs are what is; theory is where we go | whole-shape | Reversing it would make the theory ratify the code and would void [WT-0]'s directional rule and every `drifted` audit row. | stays a principle; whole-shape mark dropped |

Tally: whole-shape 12, judgment 18, total 30.

Not applied (superseded Slice B draft of the Kind-column tables, kept as a
record):

```text
#### 4.1 Ontology and Queue Mechanics

| Principle | Kind | Design consequence | Evidence | Contract |
|-----------|------|--------------------|----------|----------|
| Everything is a task | whole-shape | No caller gets a privileged side-channel. Even a short-lived CLI requester mints a TID and receives control replies on `T{tid}.ctrl_in`. | Owner, 2026-09-17 | [MF-3]; `2026-09-18` PONG plan |
| It is just a message | whole-shape | Protocols are ordinary messages on ordinary queues. Replies are pushed to a queue the requester's reactor already watches—push, never pull-scan. | Owner, 2026-09-17: "this should not be a special case. It is just a message." | [MF-3] |
| Queues carry facts; processes carry hints | whole-shape | No in-memory lifecycle database. Detached task lineage and process evidence live in `weft.state.tasks.<tid>`; local Manager tables are caches/hints rebuilt from queues. | Owner, 2026-09-23; `2026-10-12` burst plan | OBS.1, OBS.6; [MF-5] |
| Task-internal state is private; Weft never depends on it | judgment | An individual task may maintain its own internal state (`TaskMonitor` uses `broker.sidecar()` tables to collate and export `weft.log.tasks` before pruning), but Weft's execution, control, status, and result paths must not read or depend on a task's internal state. | Owner, 2026-10-14 and 2026-10-15; `2026-10-14-task-monitor-read-separation-and-simplification-plan.md` | [OBS.13], [SB-0.4a] |
| Consume-to-disappear queues, not relational tables | judgment | SimpleBroker queues auto-create on write and auto-disappear when emptied. Consume tokens as work is dispatched (`delete`/`move_one`) instead of accumulating unconsumed event logs that require tombstones and manual GC sweeps. | Owner & architectural review, 2026-10-14 | [SB-0.1]; `2026-10-14` family scheduling plan |
| Address a fact to its observers | judgment | Terminal completion is broadcast atomically to `weft.log.tasks` and `T{tid}.ctrl_out` so task waiters watch only their target's queue instead of scanning global history in $O(W \cdot E)$. | `2026-10-12` burst plan; owner, 2026-10-09 | [MF-3], [MF-5], OBS.1 |
| Keep an identity you already have | judgment | An owner that wrote a row retains the returned ID; a caller that created a TaskSpec derives queue names from its TID rather than scanning logs to rediscover it. | Owner, 2026-10-09: "We don't need to re-discover it from the taskspec that we just created" | [MF-5]; [SB-0.2] |

#### 4.2 Classical Algorithms and Resource Control

| Principle | Kind | Design consequence | Evidence | Contract |
|-----------|------|--------------------|----------|----------|
| Classical CS models over blunt ad-hoc fixes | judgment | Start from the well-studied CS formulation (reactor demultiplexing, work-conserving DFS/work-sharing, measured admission control, two-phase custody handoff) and realize it with existing primitives rather than inventing lanes, heuristics, or magic budgets. | Owner, 2026-10-14 | [MA-1.8]; `2026-10-14` family scheduling plan |
| Queued work costs nothing; live processes cost | whole-shape | Submission is always queue-first and unblocked. Admission gates process creation using measured pressure (`C - R - U`), never prospective per-task connection debt or predeclared child counts. | Owner, 2026-10-14: "Tasks sitting waiting to be spawned do not consume processes. Only live processes do" | [MF-1]; [MA-1.8] |
| Use the primitives you have | judgment | Compose labels, bounded queues, FIFO/LIFO reads, atomic exact-ID moves, and `weft.state.tasks.*` snapshots before proposing new infrastructure. | Owner, 2026-10-14; 2026-09-17: "We already have the primitives we need in the multiqueue watcher" | [SB-0.1], [SB-0.4] |
| Push a concern down when it belongs lower | whole-shape | Connection attribution belongs in SimpleBroker because SimpleBroker opens connections; admission and scheduling policy belong in Weft. SimpleBroker never depends on Weft. | Owner, 2026-10-06, 2026-10-13, 2026-09-17 | [SB-0.4] |
| Process concurrency, short transactions | whole-shape | Weft isolates work in OS processes (`spawn` context), not worker threads, and keeps broker transactions short. On PostgreSQL the scarce resource is connection count. | Owner, 2026-09-15 | [SB-0.4] |
| One owner for each resource | judgment | Each `BrokerSession`, queue lease, file descriptor, `weft.state.tasks.*` deleter (`LivenessMonitor`), and worker result has a single explicit owner. | Owner, 2026-09-15 | [SB-0.4]; [CC-2.2.1]; QUEUE.7, QUEUE.8; LIVENESS.R3 |

#### 4.3 Reactor, Liveness, and Process Topology

| Principle | Kind | Design consequence | Evidence | Contract |
|-----------|------|--------------------|----------|----------|
| One reactor, one arbiter, a closed set of inputs | whole-shape | Every task waits at one `PollingStrategy` arbiter driven by backend hints/notifications, local `notify_activity()` events, OS sentinel adapters, and explicit deadlines—never defensive polling wait-caps. | Owner, 2026-09-17: "we are all event driven, with just a limited number of types of events" | [CC-2.2.1]; IMPL.8 to IMPL.11 |
| A quiet task does nothing | judgment | When no event arrives and no deadline is due, a reactor sleeps without running turns. First wake after quiet is bounded by one pass of SimpleBroker's polling strategy, followed by burst mode; the strategy, not Weft, owns the pass length and burst behavior ([CC-2.2.1]). <!-- altitude --> | Owner, 2026-09-17 | [CC-2.2.1] |
| Subprocess and stream supervision is readiness-driven, not timer-polled | judgment | When a runner supervises child stdio pipes alongside timeouts and resource limits, the platform readiness adapter drives wakeups; the supervision timeout is only the protective ceiling until the next deadline. Post-exit stream drain stops immediately when the kernel pipe reports EOF or empty rather than sleeping out a fixed drain window when an inherited grandchild descriptor remains open, and is otherwise bounded by the private drain budget while a descendant keeps writing. | Owner, 2026-10-29 | `06-Resource_Management.md` [RM-5.2]; `01-Core_Components.md` [CC-2.5], [CC-3.4] |
| Bounded per-turn stream work and backpressure over unbounded buffering | judgment | Active stream drainage reads a bounded chunk budget per turn so output floods cannot starve timeout, cancellation, or resource-limit checks, and slow downstream sinks apply OS pipe backpressure to the child rather than growing parent memory without bound. At process termination, remaining kernel pipe bytes are captured into memory before slow sink delivery. | Owner, 2026-10-29 | `06-Resource_Management.md` [RM-5.2]; `01-Core_Components.md` [CC-2.5] |
| Availability and death are different questions | whole-shape | A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death. Task-owned terminal proof wins over a Manager `wrapper_lost` record, and reservation/checkpoint custody survives until task-snapshot handoff or positive settlement. | Owner, 2026-09-08; `2026-09-17` discovery plan; `2026-10-14` family scheduling plan | [MA-1.6a], [MA-3], [MF-5], LIVENESS.R3–R7 |
| Ask who owns this lifetime | whole-shape | A shared Manager, a child task launched detached from the Manager's lifetime, and a waiting client have independent lifetimes. Manager supersession changes who takes future work; it never kills or moves running children. <!-- altitude --> | Owner, 2026-09-08 | [MA-1], [MA-3]; `14-Python_API_Surfaces.md` [PY-2] |

#### 4.4 Simplicity, Evolution, and Verification

| Principle | Kind | Design consequence | Evidence | Contract |
|-----------|------|--------------------|----------|----------|
| As few concepts as possible | judgment | Changes consolidate onto existing concepts rather than introducing a second spawn lane, state store, event bus, scheduler daemon, or cache subsystem. | Owner, 2026-09-21: "We want to have as few concepts as possible"; 2026-10-14 | `00-Overview_and_Architecture.md`, Product Boundary |
| Shared function, shared code | judgment | Two code paths for one responsibility is a design defect. A subclass must justify why the shared base path cannot serve. | Owner, 2026-10-09 and 2026-09-21 | Engineering principles 1 and 4.2 |
| The expert Python developer test | judgment | A design must look clean, direct, and idiomatic to an expert Python systems engineer—e.g., constructing a helper with explicit immutable inputs rather than shallow-copying and mutating a live Task instance. | Owner, 2026-10-09, 2026-10-14, 2026-09-21 | `2026-09-22` MaintenanceWorker plan |
| No over-armoring | judgment | Engineer for real execution and failure boundaries, not impossible or adversary-invented states. Best-effort paths try, catch, and log diagnostics; operator-precondition violations belong in documentation, not in-band heuristics. | Owner, 2026-09-07, 2026-09-15, 2026-10-13 | Engineering principles, Secondary Rules |
| Measure; do not bake in inefficiency | judgment | Performance claims require measurements on both SQLite and PostgreSQL (including broker-operation counts across completion-to-next-launch intervals). | Owner, 2026-09-11, 2026-09-17, 2026-10-09 | `2026-10-12` burst plan; `2026-10-14` family scheduling plan |
| Tests prove correctness, nothing else | judgment | Tests verify observable behavior and state-machine transitions with real queues and processes—never pinned dependency versions, CI script order, or reduced concurrency (`-n 0`) to mask load bugs. | Owner, 2026-09-16, 2026-10-06, 2026-09-10, 2026-09-15 | `08-Testing_Strategy.md`; engineering principle 4 |
| No capability diminution | judgment | Simplifications must preserve observable capabilities and latency bounds; each hardening plan maintains an explicit difference register. | Owner, 2026-09-07; review rule in the 2026-09-08 plans | Hardening runbook |
| No compatibility code | whole-shape | Contract changes update all producers and consumers in one coordinated release with a bounded forward migration for durable queue data—no permanent dual decoders or silent fallback lanes. | Owner, 2026-09-17: "No need for backwards compat" | `00-Overview_and_Architecture.md`, Single current contract |
| One path on all platforms | whole-shape | Windows, macOS, and Linux share the same lifecycle and cleanup architecture; platform-specific branches are narrow adapter leaves. | Owner, 2026-10-07, 2026-09-09 | `2026-09-10` process-title plan |
| The client mirrors the CLI | judgment | The Python client and the CLI expose the same mental model and verbs over one shared command-capability layer that both adapters call. <!-- altitude --> | Owner, 2026-09-23 | `11-CLI_Architecture_Crosswalk.md`; [PY-1] |
| Specs are what is; theory is where we go | whole-shape | When a spec reflects a flawed design, revise the spec through a class-5 plan; plans record execution status, while specs state intended behavior. | Owner, 2026-10-09 | `docs/agent-context/decision-hierarchy.md` |

```

### [WT-4] whole-shape reconciliation (task 8)

Drafted 2026-11-02 from the working table above. Rule applied (task 8): each
of the twelve whole-shape rows either cites an existing `[WT-2a.n]` in its
Contract cell (outcome (i)), or stays a principle with the whole-shape mark
dropped (outcome (ii)). The owner endorsed fourteen invariants ([WT-2a.1] to
[WT-2a.14]) on 2026-11-02 (§9 item 4): candidates .12, .13, and .14 were
admitted, and "One architecture on every platform" was refused, so its row
takes outcome (ii). Citations use the endorsed numbering (former .7 and .8
are now .8 and .9). The whole-shape mark lives only in the working table, so
dropping it changes no theory text. Line numbers below are
`docs/program-theory.md` at `ec50bd57`.

Row inputs, counted from the working table (command and output):

```text
$ awk '/^Working table for task 8/{f=1} /^Tally:/{f=0} f && /^\| /' docs/plans/2026-11-02-program-theory-reconciliation-plan.md | grep -v '^| Row \|^|---' | awk -F'|' '{gsub(/^ +| +$/,"",$3); print $3}' | sort | uniq -c
  18 judgment
  12 whole-shape
```

Judgment rows: 18, all untouched. No judgment row's text or Contract cell
changes in this task (altitude rows 6 and 8 still land in "A quiet task does
nothing" and "The client mirrors the CLI"; that is task 10 work).

#### Summary table

| Row | Outcome | Invariant cited |
|-----|---------|-----------------|
| Everything is a task | (i) Contract cell cites | [WT-2a.12] |
| It is just a message | (i) Contract cell cites | [WT-2a.12] |
| Queues carry facts; processes carry hints | (i) Contract cell cites | [WT-2a.2] |
| Queued work costs nothing; live processes cost | (i) Contract cell cites | [WT-2a.14] |
| Push a concern down when it belongs lower | (i) Contract cell cites | [WT-2a.8] |
| Process concurrency, short transactions | (i) Contract cell cites | [WT-2a.5] (identity clause only) |
| One reactor, one arbiter, a closed set of inputs | (i) Contract cell cites; Design-consequence cell gains one clause | [WT-2a.6] (restated) |
| Availability and death are different questions | (i) Contract cell cites | [WT-2a.13] |
| Ask who owns this lifetime | (i) Contract cell cites, with altitude row 7 applied | [WT-2a.13] |
| No compatibility code | (i) Contract cell cites | [WT-2a.9] |
| One path on all platforms | (ii) stays a principle; whole-shape mark dropped | none: candidate "One architecture on every platform" refused as an invariant by the owner, 2026-11-02 (§9 item 4 (d)) |
| Specs are what is; theory is where we go | (ii) stays a principle; whole-shape mark dropped | none: a documentation-governance rule (Contract cell `docs/agent-context/decision-hierarchy.md`, already the owner's 2026-10-09 rule in [WT-0]) that measures documents, not an implementation, so it has no counterpart in `docs/specifications/` and no firing test. No block is needed: the working table's mark is the only place it lived. |

Tally: 10 rows cite an invariant, 2 rows stay principles (outcome (ii)),
total 12. The [WT-4] principle-row count stays at 30, because no row is added
or removed by task 8; the "Observable with standard tools" row added by the
owner's endorsement is a separate addition (appendix "Owner endorsement
additions"), which makes 31 after task 13.

#### Outcome (i): ten rows cite a [WT-2a.n]

Each block is the full post-change table row. The edit is the appended
`[WT-2a.n]` in the Contract cell; every other cell is verbatim from
`docs/program-theory.md`, with two exceptions: "One reactor, one arbiter, a
closed set of inputs" also gains one Design-consequence clause, and "Ask who
owns this lifetime" carries altitude row 7. Rows 1 to 5 were drafted before
the endorsement and are renumbered to the endorsed list; rows 6 to 10 follow
from §9 item 4.

1. **Queues carry facts; processes carry hints** (line 234) instantiates
   [WT-2a.2], "Queues are the only durable truth": durable lifecycle facts
   are queue rows and process memory holds hints.

Replacement (`docs/program-theory.md`):
```markdown
| Queues carry facts; processes carry hints | No in-memory lifecycle database. Detached task lineage and process evidence live in `weft.state.tasks.<tid>`; local Manager tables are caches/hints rebuilt from queues. | Owner, 2026-09-23; `2026-10-12` burst plan | OBS.1, OBS.6; [MF-5]; [WT-2a.2] |
```

2. **Push a concern down when it belongs lower** (line 247) instantiates
   [WT-2a.8] (formerly .7), "SimpleBroker owns queue mechanics" ("a queue-level need is
   met in SimpleBroker, never reimplemented in Weft, and SimpleBroker never
   depends on Weft"); the row names the same layer boundary for connection
   attribution.

Replacement (`docs/program-theory.md`):
```markdown
| Push a concern down when it belongs lower | Connection attribution belongs in SimpleBroker because SimpleBroker opens connections; admission and scheduling policy belong in Weft. SimpleBroker never depends on Weft. | Owner, 2026-10-06, 2026-10-13, 2026-09-17 | [SB-0.4]; [WT-2a.8] |
```

3. **Process concurrency, short transactions** (line 248) instantiates
   [WT-2a.5], "Process isolation" (restated from "Spawn isolation"). The row's first sentence ("Weft isolates
   work in OS processes (`spawn` context), not worker threads") is the
   invariant; the remaining clauses (short broker transactions; on
   PostgreSQL the scarce resource is connection count) are judgment that
   rides along. The citation covers the identity clause only.

Replacement (`docs/program-theory.md`):
```markdown
| Process concurrency, short transactions | Weft isolates work in OS processes (`spawn` context), not worker threads, and keeps broker transactions short. On PostgreSQL the scarce resource is connection count. | Owner, 2026-09-15 | [SB-0.4]; [WT-2a.5] |
```

4. **One reactor, one arbiter, a closed set of inputs** (line 255)
   instantiates the restated [WT-2a.6], "One arbiter per reactor; reactors
   compose by messages". The principle names today's realization; the
   Design-consequence cell gains the owner's clause saying so.

Replacement (`docs/program-theory.md`):
```markdown
| One reactor, one arbiter, a closed set of inputs | Every task waits at one `PollingStrategy` arbiter driven by backend hints/notifications, local `notify_activity()` events, OS sentinel adapters, and explicit deadlines—never defensive polling wait-caps. The principle names today's realization (`PollingStrategy` and the four input origins); the identity statement is [WT-2a.6]. | Owner, 2026-09-17: "we are all event driven, with just a limited number of types of events" | [CC-2.2.1]; IMPL.8 to IMPL.11; [WT-2a.6] |
```

5. **No compatibility code** (line 273) instantiates [WT-2a.9] (formerly
   .8), "One current contract", which the working table already names and which cites the
   same overview section.

Replacement (`docs/program-theory.md`):
```markdown
| No compatibility code | Contract changes update all producers and consumers in one coordinated release with a bounded forward migration for durable queue data—no permanent dual decoders or silent fallback lanes. | Owner, 2026-09-17: "No need for backwards compat" | `00-Overview_and_Architecture.md`, Single current contract; [WT-2a.9] |
```

6. **Everything is a task** (line 232) instantiates [WT-2a.12], "Everything
   is a task, and every protocol is a message".

Replacement (`docs/program-theory.md`):
```markdown
| Everything is a task | No caller gets a privileged side-channel. Even a short-lived CLI requester mints a TID and receives control replies on `T{tid}.ctrl_in`. | Owner, 2026-09-17 | [MF-3]; `2026-09-18` PONG plan; [WT-2a.12] |
```

7. **It is just a message** (line 233) instantiates the protocol half of
   [WT-2a.12].

Replacement (`docs/program-theory.md`):
```markdown
| It is just a message | Protocols are ordinary messages on ordinary queues. Replies are pushed to a queue the requester's reactor already watches—push, never pull-scan. | Owner, 2026-09-17: "this should not be a special case. It is just a message." | [MF-3]; [WT-2a.12] |
```

8. **Queued work costs nothing; live processes cost** (line 245)
   instantiates [WT-2a.14], "Submission never blocks". The admission clause
   of the row stays principle-level text; the invariant covers the
   unblocked, cost-free submission.

Replacement (`docs/program-theory.md`):
```markdown
| Queued work costs nothing; live processes cost | Submission is always queue-first and unblocked. Admission gates process creation using measured pressure (`C - R - U`), never prospective per-task connection debt or predeclared child counts. | Owner, 2026-10-14: "Tasks sitting waiting to be spawned do not consume processes. Only live processes do" | [MF-1]; [MA-1.8]; [WT-2a.14] |
```

9. **Availability and death are different questions** (line 259)
   instantiates the "death needs proof" half of [WT-2a.13].

Replacement (`docs/program-theory.md`):
```markdown
| Availability and death are different questions | A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death. Task-owned terminal proof wins over a Manager `wrapper_lost` record, and reservation/checkpoint custody survives until task-snapshot handoff or positive settlement. | Owner, 2026-09-08; `2026-09-17` discovery plan; `2026-10-14` family scheduling plan | [MA-1.6a], [MA-3], [MF-5], LIVENESS.R3–R7; [WT-2a.13] |
```

10. **Ask who owns this lifetime** (line 260) instantiates the "independent
    lifetimes" half of [WT-2a.13]. The block carries altitude row 7
    (`stop_with_parent=False` lifted), as §9 item 4 (c) drafted it.

Replacement (`docs/program-theory.md`):
```markdown
| Ask who owns this lifetime | A shared Manager, a child task launched detached from the Manager's lifetime, and a waiting client have independent lifetimes. Manager supersession changes who takes future work; it never kills or moves running children. | Owner, 2026-09-08 | [MA-1], [MA-3]; `14-Python_API_Surfaces.md` [PY-2]; [WT-2a.13] |
```

#### Outcome (ii): two rows stay principles

"One path on all platforms" (line 274) stays a principle; the owner refused
the candidate "One architecture on every platform" as an invariant on
2026-11-02 (§9 item 4 (d)). Its whole-shape mark is dropped; the row text is
unchanged, so it needs no block. "Specs are what is; theory is where we go"
is unchanged from the first draft (mark dropped; no block).

#### Verification: every [WT-2a.n] cited exists in the drafted block

Rerun 2026-11-02 over the endorsed block (fourteen identifiers, once each):

```text
$ awk '/^## Identity invariants \[WT-2a\]/{f=1} f&&/^```$/{exit} f' docs/plans/2026-11-02-program-theory-reconciliation-plan.md | grep -oE '^- \*\*\[WT-2a\.[0-9]+\]' | sort -t. -k2 -n | uniq -c
   1 - **[WT-2a.1]
   1 - **[WT-2a.2]
   1 - **[WT-2a.3]
   1 - **[WT-2a.4]
   1 - **[WT-2a.5]
   1 - **[WT-2a.6]
   1 - **[WT-2a.7]
   1 - **[WT-2a.8]
   1 - **[WT-2a.9]
   1 - **[WT-2a.10]
   1 - **[WT-2a.11]
   1 - **[WT-2a.12]
   1 - **[WT-2a.13]
   1 - **[WT-2a.14]
```

The outcome (i) blocks cite [WT-2a.2], [WT-2a.5], [WT-2a.6], [WT-2a.8],
[WT-2a.9], [WT-2a.12], [WT-2a.13], and [WT-2a.14], all present in the block
above (the Contract-cell check is pasted below).

```text
$ awk '/^#### Outcome \(i\): ten rows/{f=1} /^#### Outcome \(ii\)/{f=0} f && /^\| /' docs/plans/2026-11-02-program-theory-reconciliation-plan.md | awk -F'|' '{print $5}' | grep -oE '\[WT-2a\.[0-9]+\]' | sort -t. -k2 -n | uniq -c
   1 [WT-2a.2]
   1 [WT-2a.5]
   1 [WT-2a.6]
   1 [WT-2a.8]
   1 [WT-2a.9]
   2 [WT-2a.12]
   2 [WT-2a.13]
   1 [WT-2a.14]
$ for c in EXEC.1 QUEUE.4 QUEUE.5 QUEUE.6 OBS.4 OBS.13; do printf "%s: " $c; grep -c "\*\*$c\*\*\|\[$c\]" docs/specifications/07-System_Invariants.md; done
EXEC.1: 1
QUEUE.4: 1
QUEUE.5: 1
QUEUE.6: 2
OBS.4: 1
OBS.13: 2
$ grep -rln '\[MCP-8\]' ../taut/docs/specs; grep -rln 'TAUT-8.5' ../taut/docs/specs
../taut/docs/specs/05-taut-mcp.md
../taut/docs/specs/product-section-registry.md
../taut/docs/specs/10-taut-tui.md
../taut/docs/specs/04-summon.md
```

Ten rows cite eight distinct invariants, every one present in the block. The
new spec codes ([EXEC.1], [QUEUE.4] to [QUEUE.6], [OBS.4], [OBS.13]) resolve
in spec 07; [TAUT-8.5] and [MCP-8] are sibling Taut codes and resolve in
`../taut/docs/specs/`, not in Weft's specifications.

Test-name existence check for the §9 item 4 evidence (each count is
`grep -c "def <name>(" <file>`):

```text
tests/core/test_manager_runtime_connections.py::test_borrowed_manager_probe_routes_reply_and_retires_requester_queue: 1
tests/tasks/test_pipeline_runtime.py::test_pipeline_task_routes_pong_to_requester_control_queue: 1
tests/core/test_connection_admission.py::test_launch_uses_measured_capacity_without_task_budget: 1
tests/core/test_spawn_requests.py::test_runtime_origin_keeps_same_context_child_on_primary_with_lineage: 1
tests/core/test_manager.py::test_manager_superseded_self_record_drains_children_without_stopping: 1
tests/tasks/test_liveness_monitor.py::test_probe_worker_reports_foreign_host_as_attempted_unknown: 1
tests/core/test_monitor_store.py::test_monitor_store_task_owned_control_and_log_supersede_manager: 1
```

### Owner endorsement additions (2026-11-02): [WT-4] 4.4, [WT-5], [WT-7]

Three blocks that follow from the owner's endorsement of the fourteen
identity invariants (§9 item 4). Each is an insertion; the span it follows is
quoted, and every quoted span is verbatim from `docs/program-theory.md` at
`ec50bd57` and is not changed by any other block in this appendix. (A fourth
block, the [WT-2] item 2 TaskMonitor maintenance-worker example, was dropped
at round 6 as unnecessary; the restated [WT-2a.6] does not need it.)

[WT-4] table 4.4. A new principle row, appended after the current last 4.4
row, which reads "| Specs are what is; theory is where we go | When a spec
reflects a flawed design, ... | Owner, 2026-10-09 |
`docs/agent-context/decision-hierarchy.md` |", and before
`## What Weft is not [WT-5]`. [OBS.4] is the process-title invariant in spec
07 (`grep -n 'OBS.4' docs/specifications/07-System_Invariants.md` →
`317:### Process title format [OBS.4]`); `CLAUDE.md` §9 is "Key Design
Decisions" ("Why process titles?"). The [WT-4] principle-row count becomes 31.

Replacement (`docs/program-theory.md`):
```markdown
| Observable with standard tools | Process titles and queue peeks are the operator interface; Weft never needs a daemon, index, or query service of its own to be inspected. | Owner, 2026-11-02 (a design goal, not an identity invariant); `CLAUDE.md` §9 | [WT-5] (no second control plane); [OBS.4] |
```

[WT-5]. The SimpleBroker-model sentences trimmed from [WT-2a.11]. Inserted
as a new paragraph after the paragraph ending "on 2026-09-17 that a
hand-crafted `PING` "is not going to be a threat we are going to worry
about"." and before `## Negative knowledge: rejected alternatives [WT-6]`.

Replacement (`docs/program-theory.md`):
```markdown
This is SimpleBroker's model, adopted: inputs that choose where the boundary
sits are trusted by placement, inputs that shape names inside it are checked
for shape, and intent is never inspected (sibling account: the security
section of SimpleBroker's configuration guide). Resource limits and sandbox
runners bound what a task may do; they do not decide whether a caller is
trusted.
```

[WT-7]. The falsifier for "Observable with standard tools". Inserted after
the "Simplicity against ambition" bullet, which ends "feature requires a
second control plane or depends on a task's internal sidecar state, the
theory or the feature is wrong.", and before the "Event-driven against
absence" bullet.

Replacement (`docs/program-theory.md`):
```markdown
- **Inspection needs Weft-only tooling:** If inspecting a running Weft
  requires a Weft-specific daemon or query service, or shows state that no
  queue peek can reach, a second control plane has appeared. Task-private
  working state ([WT-2a.2]) is outside this test; Weft's operational state is
  not.
```

### Task 13 additions (2026-11-02)

Two items §4c named without a block; drafted and applied at task 13 in the same change, blocks added so the gate covers them.

AGENTS.md §3 "NEVER break" list repointed at [WT-2a] for identity and at spec 07 for the two contract rules (§4c item 3). Replaces the span from "**NEVER break**:" through the `spec` and `io` bullet.

Replacement (`AGENTS.md`):
```markdown
**NEVER break** (identity invariants are stated once in
`docs/program-theory.md` [WT-2a]; the exact contract text lives in
`docs/specifications/07-System_Invariants.md`; this list is the quick form):
- TID is a 64-bit SimpleBroker hybrid timestamp (nanosecond magnitude with the low 12 bits holding the logical counter), format-compatible with `time.time_ns()` and typically 19 digits; immutable after creation ([WT-2a.1]; [IMMUT.3])
- State transitions are forward-only (created → spawning → running → completed/failed/timeout/cancelled/killed) ([WT-2a.3]; [STATE.1], [STATE.2])
- Reserved queue policy must be honored (keep/clear for tasks; the manager spawn lane may requeue) — a contract rule, spec 07 [QUEUE.6] and [EXEC.1] ([WT-2a.7])
- Process titles must be shell-safe: `weft-{context_short}-{tid_short}:{name}:{status}[:details]` — a contract rule, spec 07 [OBS.4]
- `spec` and `io` sections are immutable after TaskSpec creation ([WT-2a.4]; [IMMUT.1], [IMMUT.2])
```

[WT-5] cross-reference to [WT-2a.11] and the boundary-refusal tags (§4c item 5). Inserted after the sentence ending "they do not decide whether a caller is trusted."

Replacement (`docs/program-theory.md`):
```markdown
runners bound what a task may do; they do not decide whether a caller is
trusted. The identity statement is [WT-2a.11]. Boundary refusals are tagged
`Kind: boundary refusal` in [WT-6].
```

### Task 16 (2026-11-02)

Three blocks: the keyed possession-probe bullet (replaces the [WT-7] "Possession probe" bullet from "- **Possession probe:**" through "has drifted."), the transfer-failure log rule (appended to the [WT-7] "Repeated rejected proposals" bullet after "accepting the proposal."), and the verification-pressure sentence in [REV-WT-004] (inserted before "Slice B pressure: filled at task 17.").

Replacement (`docs/program-theory.md`):
```markdown
- **Possession probe, keyed:** At each release or class-5 completion, the
  owner writes the key first: one hypothetical feature and where it belongs,
  one category error and the entry that refuses it, and the class of defect
  an audit of a named subsystem should find. An agent that did not author
  the plan, from a different family where one is available, answers from
  this document alone and is graded against the key. The key and the result
  go in the plan's review log; a drawn key item is then spent. On FAIL,
  classify after disposition as a document deficiency, a key fault, or
  agent reasoning, and change this document only for a document deficiency.
  A self-graded probe is not a probe. Refresh the `[WT-0]`
  **theory-versus-code audit table** at the same trigger, carrying unchanged
  rows forward by reference and re-auditing touched claims.
```

Replacement (`docs/program-theory.md`):
```markdown
- **Repeated rejected proposals as a transfer failure:** A repeated proposal
  for a lane, a cache, a compatibility shim, or an unconsumed queue table is
  evidence that a principle in `[WT-4]` or an `[ALT-WT-*]` record is not clear
  enough. Sharpen the theory rather than accepting the proposal. Log rule: a
  review proposal that matches a `[WT-6]` entry and does not cite that
  entry's recorded reopening condition is written up in the review's
  disposition row as a transfer failure against the entry; a proposal that
  cites a fired reopening condition is a legitimate reopening, not a failure.
```

Replacement (`docs/program-theory.md`):
```markdown
  theory. Verification pressure: the possession probe ran twice in its first
  eight weeks; neither record shows a preregistered key, who wrote the
  expected answers, or what they were graded against, and no release ran
  it; the keyed form above replaces it, with the audit table on the same
  trigger. Slice B pressure: the 2026-11-01 deep dive found the theory at
  mixed altitude (source paths, private names, millisecond figures), nine
  concepts without ownership rows, and three places where a specification
  sanctioned what the theory refused; the owner's four-way standard
  (general enough to measure a re-implementation, specific enough to decide
  what belongs, identity invariants, negative knowledge) set the bar.
```

### Task 17 (2026-11-02)

Header status and governing-process lines (replace the former "Status: Draft, first version, not yet adopted" line and the "Governing process:" paragraph), and the four [REV-WT-004] fills (each replaces its "filled at task 17" placeholder).

Replacement (`docs/program-theory.md`):
```markdown
Status: Active, adopted 2026-11-02 through
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` ([REV-WT-004]);
revised only through a class-5 plan
```

Replacement (`docs/program-theory.md`):
```markdown
Governing process: Theory revisions are class-5 plans with one cross-family
review round (a second only after a block or a scope change), the keyed
possession probe and the theory-versus-code audit refresh at each release or
class-5 completion ([WT-7]), and the Replacement-sync gate in
`bin/check-doc-paths` for promoted plans. SimpleBroker uses `[DOM-16]` in its
operating-model specification for the same purpose.
```

Replacement (`docs/program-theory.md`):
```markdown
  Verification line): [WT-2a] states fourteen identity invariants the owner
  endorsed on 2026-11-02, generalized where Taut's reactor over workspace
  reactors showed an invariant holds per owner; [WT-4] rows cite the
  invariant they instantiate; every [WT-6] entry carries a Kind tag and
  [ALT-WT-007] is restated; the [WT-0] Verification line names four
  measures; the [WT-7] probe is keyed.
```

Replacement (`docs/program-theory.md`):
```markdown
  whenever depth was local. Slice B supersessions: identity scattered across
  [WT-2], [WT-4], and the agent entry files; a self-graded possession probe;
  the reversibility claim in [ALT-WT-007]; a Kind column on [WT-4]; and
  "one reactor per task", now one arbiter per reactor with composition by
  messages.
```

Replacement (`docs/program-theory.md`):
```markdown
  trigger. Slice B pressure: the 2026-11-01 deep dive found the theory at
  mixed altitude (source paths, private names, millisecond figures), nine
  concepts without ownership rows, and three places where a specification
  sanctioned what the theory refused; the owner's four-way standard
  (general enough to measure a re-implementation, specific enough to decide
  what belongs, identity invariants, negative knowledge) set the bar.
```

Replacement (`docs/program-theory.md`):
```markdown
  - contemporaneous: the plan's theory-versus-code audit table (46 rows: 37
    aligned, 8 drifted plus one ordering half, 1 theory revision), eight
    cross-family review rounds, and the 2026-11-02 keyed possession probe
    (PASS, three of three) in the plan's review log.
```

### [WT-6] tags

Each `Kind:` line below is inserted after the blank line that follows the
entry's `### [ALT-WT-NNN] ...` heading and before its `- Disposition:`
bullet. Only the `Kind:` line is added. The existing `- Reconsider when:`
bullet is the entry's reopening condition and is not duplicated; no
`Reopen when:` line is added. Two entries, [ALT-WT-003] and [ALT-WT-010],
carry a proposed edit to their existing `Reconsider when:` line, flagged
for the owner and applied only if the owner accepts it. No refusal is
weakened.

Each entry below has one labelled Replacement block: the entry heading
line as it stands in `docs/program-theory.md`, the blank line, and the
`Kind:` line beneath it. For [ALT-WT-003] and [ALT-WT-010] the block runs
on through the edited `Reconsider when:` line, with the intervening entry
text verbatim; if the owner declines either edit, that block's
`Reconsider when:` line reverts to the current line before task 13. The
six `boundary refusal` lines end with "See [WT-5]." because boundary
refusals are "what Weft is not" in the hub's two-part scheme.

##### [ALT-WT-001] A child lane and per-task connection budgets

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-001] A child lane and per-task connection budgets

- Kind: tried and discarded (the 0.9.110 child-lane experiment and `2026-10-13-connection-aware-child-admission-plan.md`, superseded; see [REV-WT-003])
```

##### [ALT-WT-002] Fixed polling caps as a fallback

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-002] Fixed polling caps as a fallback

- Kind: tried and discarded (the caps were the earlier implementation and were removed by `2026-09-17-watcher-reactor-restoration-plan.md`; see [REV-WT-001])
```

##### [ALT-WT-003] A PONG found by a poll of the target's `ctrl_out`

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-003] A PONG found by a poll of the target's `ctrl_out`

- Kind: tried and discarded (the earlier PING path, replaced by `2026-09-18-event-routed-manager-pong-plan.md`)
- Disposition: rejected
- Candidate: The requester writes a PING, then scans the target's `ctrl_out`
  for the reply on a short timer.
- Why plausible: `ctrl_out` already existed and the target already wrote
  there.
- Reason: Pull, not push. The reply was not an event for the requester's
  reactor. On PostgreSQL the requester's listener did not watch that queue.
  The owner, 2026-09-17: "A PING always has a requester and a target. One
  code path."
- Current consequence: A PING names the requester's own `ctrl_in` as
  `reply_to`. The target writes one PONG there. The requester's existing
  control handler recognizes it.
- Reconsider when: Never. A new control reply follows the same shape: it is
  pushed to a queue the requester already watches, never found by scanning
  the target's queues.
```

- Proposed edit to the existing `Reconsider when:` line (flagged for the owner): "Never. A new control reply follows the same shape: it is pushed to a queue the requester already watches, never found by scanning the target's queues." Current line: "Never. A new control reply follows the same shape."

##### [ALT-WT-004] A heartbeat as the TaskMonitor clock

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-004] A heartbeat as the TaskMonitor clock

- Kind: tried and discarded (built and still registered today; decided by the owner on 2026-09-08; removal plan `2026-09-08-service-wake-model-and-heartbeat-decoupling-plan.md` is a draft)
```

##### [ALT-WT-005] A shallow copy of a task as a maintenance worker

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-005] A shallow copy of a task as a maintenance worker

- Kind: tried and discarded (built in TaskMonitor and replaced by `2026-09-22-task-monitor-maintenance-worker-plan.md`)
```

##### [ALT-WT-006] Task-level REQUEUE as a reserved-queue policy

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-006] Task-level REQUEUE as a reserved-queue policy

- Kind: tried and discarded (shipped as a task policy and removed by `2026-08-31-reserved-disposition-and-requeue-removal-plan.md`)
```

##### [ALT-WT-007] restated (fork 1 resolved 2026-11-02)

This block replaces the whole entry, from its heading through its Evidence
line, not only the heading. The rejected alternatives are now the two the
evidence supports; the one-way drain moves from candidate to consequence.

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-007] A stopping drain, or a resuming drain, on supersession

- Kind: tried and discarded (the stopping drain shipped and cancelled user work during concurrent bootstrap; see the `2026-05-20` lesson in `docs/lessons.md`)
- Disposition: rejected
- Candidate: (a) When a Manager observed its own `superseded` row, it reused
  the ordinary STOP drain and stopped its children. (b) A superseded Manager
  that had entered its drain resumed dispatch if the replacement Manager
  disappeared.
- Why plausible: (a) One drain path is simpler than two. (b) A vanished
  replacement leaves no active Manager for a window.
- Reason: (a) The STOP drain cancelled user tasks during concurrent
  bootstrap. A supersession notice changes who takes future work; it does
  not move or stop existing work. (b) The superseded Manager never holds
  better information than the row it read, and the gap is closed by the
  next submission: the ensure-manager path sees no active Manager and
  starts a fresh one. The leadership-yield drain revalidates and may resume
  because it acts on self-observed, possibly stale leader proof; the
  superseded drain acts on a row a live peer just wrote. Those are two
  conditions with one policy each, not one condition with two.
- Current consequence: A `superseded` row starts a one-way, non-stopping
  drain: no new intake, owned children run to completion, then the Manager
  exits. Explicit operator replacement (`--replace`) and automatic
  convergence share that drain. The owner stated the rule on 2026-11-02:
  "What should happen is that A continues draining and exits. The next
  call that requires a manager should see that there is no active manager
  and should start one (manager C), which becomes the new primary manager."
- Reconsider when: a submission path exists that cannot start a Manager
  when none is active, or a superseded Manager is shown to hold evidence
  the registry row lacks.
- Evidence: Owner dialogue, 2026-09-08 and 2026-11-02; [MA-1.4], [MA-3];
  `2026-05-20` lesson in `docs/lessons.md`; `[REV-WT-004]`.
```

##### [ALT-WT-008] A Manager reuse flag

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-008] A Manager reuse flag

- Kind: tried and discarded (the flag shipped and is inert since the RunSession migration; decided by the owner on 2026-09-08; retirement plan `2026-09-08-manager-reuse-flag-retirement-and-run-lifecycle-plan.md` is a draft)
```

##### [ALT-WT-009] Every waiter scans the shared task log for completion

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-009] Every waiter scans the shared task log for completion

- Kind: tried and discarded (the earlier waiter design, replaced by `2026-10-12-burst-submission-complexity-plan.md`; see [REV-WT-002])
```

##### [ALT-WT-010] A new scheduler, event bus, index, or cache subsystem

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-010] A new scheduler, event bus, index, or cache subsystem

- Kind: boundary refusal (refused as a second lifecycle database; the entry records that the fixes "were applications of the existing theory, not new machinery", so no subsystem was built) See [WT-5].
- Disposition: rejected
- Candidate: A general subsystem to make repeated discovery cheap.
- Why plausible: An index or a cache removes repeated work.
- Reason: A cache that becomes startup truth is a second lifecycle database.
  The fixes were applications of the existing theory, not new machinery.
- Current consequence: No heaps, counters, or registries exist only to claim
  constant cost. In-memory state stays a hint.
- Reconsider when: A cost measured on both SQLite and PostgreSQL remains
  after every existing primitive is used correctly, and the owner accepts a
  named new concept.
```

- Proposed edit to the existing `Reconsider when:` line (flagged for the owner): "A cost measured on both SQLite and PostgreSQL remains after every existing primitive is used correctly, and the owner accepts a named new concept." Current line: "A measured cost remains after every existing primitive is used correctly, and the owner accepts a named new concept."

##### [ALT-WT-011] Hidden preflight on the ordinary execution path

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-011] Hidden preflight on the ordinary execution path

- Kind: boundary refusal (product boundary: a hidden probe pulls Weft into agent management; `00-Overview_and_Architecture.md`, Product Boundary) See [WT-5].
```

##### [ALT-WT-012] Backward-compatibility code for changed contracts

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-012] Backward-compatibility code for changed contracts

- Kind: boundary refusal (identity: single current contract; owner decisions 2026-09-08 and 2026-09-17) See [WT-5].
```

##### [ALT-WT-013] Line count as a complexity measure

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-013] Line count as a complexity measure

- Kind: boundary refusal (a generic review heuristic refused on cohesion grounds; `CLAUDE.md` section 1.1, engineering principle 11) See [WT-5].
```

##### [ALT-WT-014] An input sanitizer as a security boundary

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-014] An input sanitizer as a security boundary

- Kind: boundary refusal (trust-domain and argv-only identity; no such helper was built) See [WT-5].
```

##### [ALT-WT-015] Manager-local child tracking or OS `ppid` as the liveness/lineage source

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-015] Manager-local child tracking or OS `ppid` as the liveness/lineage source

- Kind: boundary refusal (layer and identity grounds: detached children outlive their Manager by design, [MA-1], [MA-3]; owner 2026-09-23 and 2026-10-14) See [WT-5].
```

##### [ALT-WT-016] Re-derive the task graph on each dispatch or bulk-move pending tokens on parent exit

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-016] Re-derive the task graph on each dispatch or bulk-move pending tokens on parent exit

- Kind: tried and discarded (investigated in `2026-10-14-measured-admission-family-scheduling.md` and refused on its O(N) completion-to-next-launch cost, owner 2026-10-14)
```

##### [ALT-WT-017] Reduced test concurrency as an acceptance gate

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-017] Reduced test concurrency as an acceptance gate

- Kind: tried and discarded (lowered concurrency was the practice until the rule was revised on 2026-10-14; owner 2026-09-09 and 2026-09-15)
```

##### [ALT-WT-018] Tests that pin process or dependency versions

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-018] Tests that pin process or dependency versions

- Kind: tried and discarded (such tests existed and were deleted or retargeted; owner 2026-10-06 and 2026-09-16)
```

##### [ALT-WT-019] Using SimpleBroker queues as unconsumed relational tables or event logs

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-019] Using SimpleBroker queues as unconsumed relational tables or event logs

- Kind: tried and discarded (the initial replacement draft in `2026-10-14-measured-admission-family-scheduling.md`, refused in owner and architectural review on 2026-10-14; see [REV-WT-003])
```

##### [ALT-WT-020] Reading task-internal state (`MonitorStore`) from Weft status or result paths, or forcing stream-collation state into queues

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-020] Reading task-internal state (`MonitorStore`) from Weft status or result paths, or forcing stream-collation state into queues

- Kind: tried and discarded (status and result paths read `MonitorStore` until `2026-10-14-task-monitor-read-separation-and-simplification-plan.md` removed it)
```

##### [ALT-WT-021] Non-blocking pipe polling loops in subprocess runners

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-021] Non-blocking pipe polling loops in subprocess runners

- Kind: tried and discarded (the polling adapter shipped and was measured and replaced; commits `4c490043` and `d4381877`, `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md`)
```

#### [ALT-WT-015] stale implementation reference (mismatch kind 2)

The stale reference names `weft/commands/tasks.py` as the home of
`read_task_state_snapshot`. In the tree today the function is defined in the
core task-state module. `weft/commands/tasks.py` only imports and calls it:

```text
$ grep -rn 'def read_task_state_snapshot' weft/
weft/core/task_state.py:54:def read_task_state_snapshot(
$ grep -n 'read_task_state_snapshot' weft/commands/tasks.py | head -3
74:    read_task_state_snapshot,
145:    row = read_task_state_snapshot(ctx, full, broker=broker)
558:    selected = read_task_state_snapshot(ctx, tid)
```

The task-state module's docstring cites [MF-5], [OBS.6], and [LIVENESS.R3],
and says "These helpers never delete state; LivenessMonitor owns retirement."
[MF-5] names the module as the home of the shared namespace reads
(`05-Message_Flow_and_State.md` line 1928). The lift therefore names the
contract and the owning layer, not the path.

Replace the span that opens "- Current consequence: Task status and recovery
read `weft.state.tasks.*`" and closes "deleter of `weft.state.tasks.*`." with:

Replacement (`docs/program-theory.md`):
```markdown
- Current consequence: Task status and recovery read `weft.state.tasks.*`
  through the one shared newest-valid snapshot reader that core owns under
  [OBS.6] and [MF-5]; that reader never deletes. A terminal event alone never
  proves process exit, and `LivenessMonitor` is the sole component deleter of
  `weft.state.tasks.*` ([LIVENESS.R3]).
```

The entry's Candidate and Reason lines carry a private attribute
(`self._child_processes`) and a launcher-local argument
(`stop_with_parent=False`). Under the plan body §4 rule they are lifted in
the same replacement. Replace the span that opens "- Candidate: Use the
Manager's in-memory" and closes "belongs to." with:

Replacement (`docs/program-theory.md`):
```markdown
- Candidate: Use the Manager's in-memory child table or OS parent-PID trees
  to decide whether a task is alive and which branch it belongs to.
```

Replace the span that opens "- Reason: Submitted children are never forked"
and closes "survive Manager drain, idle exit, crash, or replacement by design
([MA-1], [MA-3])." with:

Replacement (`docs/program-theory.md`):
```markdown
- Reason: Submitted children are never forked by their submitting parent task,
  and ordinary user tasks are launched detached from the Manager's lifetime,
  so they survive Manager drain, idle exit, crash, or replacement by design
  ([MA-1], [MA-3]).
```

The rest of the Reason line (the local-hint sentence and the
`weft.state.tasks.<tid>` bridge sentence) names queues and spec vocabulary
only and stays unchanged.

### Altitude pass

Scope: [WT-2], [WT-3], [WT-4], and [WT-8], walked sentence by sentence. Each
row is one atomic change and one later Deviation Log row (`[WT-*]` in the
first column, `n/a — theory altitude` in the last). The plan body §4 rule
governs evidence lines and the further sections, and task 10 covers [WT-0],
[ALT-WT-002], [ALT-WT-004], [ALT-WT-008], [ALT-WT-021], and [REV-WT-001],
with their replacement text drafted in the same pass as task 10.

To find the too-concrete candidates, the walk ran:

```text
$ awk 'NR>=104 && NR<=276' docs/program-theory.md | grep -n 'weft/\|self\._\|\b_[a-z_]\+\b\| ms\b\|~[0-9]'
38:   its OS `ppid` tree or `self._child_processes` map, `weft.state.tasks.<tid>`
153:| A quiet task does nothing | ... one SimpleBroker pass (~100 ms), followed b...
172:| The client mirrors the CLI | ... over shared `weft/commands/` capabilities. ...
$ grep -n 'stop_with_parent' docs/program-theory.md
138:   are launched detached from the Manager (`stop_with_parent=False`) so running
260:| Ask who owns this lifetime | A shared Manager, a detached child task (`stop_with_parent=False`), ...
539:  and ordinary user tasks are launched with `stop_with_parent=False` so they
$ awk 'NR>=761 && NR<=775' docs/program-theory.md | grep -n 'weft/\|self\._\| ms\b\|~[0-9]'
(no output)
```

`stop_with_parent` is a local name in the launcher (`weft/core/launcher.py:153`).
It is not a TaskSpec field or a spec term: `grep -rn 'stop_with_parent' docs/specifications/`
returns no output. Line 539 is in [ALT-WT-015]; its lift is the [ALT-WT-015]
Reason replacement above. Public SimpleBroker
names (`PollingStrategy`, `BrokerSession`, `broker.sidecar()`, `move_one`),
the public `notify_activity()` / `next_wait_timeout()` hooks, queue names,
and spec vocabulary (`wrapper_lost`, `(pid, create_time)`) are allowed and
are not flagged.

| # | Identifier | Original span (opening … closing words) | Replacement | Direction |
|---|------------|------------------------------------------|-------------|-----------|
| 1 | [WT-2] item 3 | "Because neither the submitting parent … or `self._child_processes` map," | "Because neither the submitting parent nor a replacement Manager holds the child in its OS `ppid` tree or its in-memory child table," | concrete->lifted |
| 2 | [WT-2] item 3 | "Furthermore, ordinary user tasks are launched … (`stop_with_parent=False`) so running" | "Furthermore, ordinary user tasks are launched detached from the Manager's lifetime ([MA-3]) so running" | concrete->lifted |
| 3 | [WT-2] item 6, last bullet | "- **Higher-level intelligence** sits above Weft; … not the agent planner or policy engine." | "- **Higher-level intelligence** sits above Weft. Weft runs bounded agent work as ordinary tasks through its core agent runtime; it does not plan or orchestrate agents, and it does not own provider accounts, provider health, or approval policy ([AR-0.0], [AR-8])." | abstract->sharpened. The original sentence could be cited both for moving the agent runtime out of core, which contradicts [AR-0.0], and for adding orchestration on top of it, which contradicts [AR-8]. |
| 4 | [WT-3] TaskSpec row | "\| TaskSpec \| Frozen execution specification … \| `02-TaskSpec.md` [TS-1]; IMMUT.1, IMMUT.2 \|" | "\| TaskSpec \| Frozen execution specification (`spec`, `io`) plus forward-only runtime state (`state`) \| Canonical payload resolution under one owner fixes `spec` and `io`; the task that owns the TaskSpec publishes `state` \| `02-TaskSpec.md` [TS-1], [TS-1.4]; IMMUT.1, IMMUT.2 \|" | abstract->sharpened. Old owner "The resolution and task lifecycle path" fits both a single resolver and several. Spec 02 line 276 maps payload resolution to one resolver in the model module; [TS-1.4] owns state-versus-metadata. |
| 5 | [WT-3] Evidence row | "\| Evidence \| Queue row, scoped … \| [MF-5]; `07-System_Invariants.md` LIVENESS.R3–R7 \|" | "\| Evidence \| Queue row, scoped `(pid, create_time)` process identity, or runtime handle supporting a liveness or custody claim \| One broker-free liveness policy (`weft.liveness`) classifies it; every reader applies that policy rather than its own classifier \| [MF-5]; `07-System_Invariants.md` LIVENESS.R3–R7 \|" | abstract->sharpened. Old owner "The reader classifying it under liveness policy" fits both one shared policy and per-reader classifiers. The spec 07 Implementation Invariants mapping names `weft/liveness/` as the broker-free evidence and mapping-policy reducers, and the task-state module's docstring says "Read policy remains in the broker-free liveness policy module". The 2026-11-01 sweep also found evidence shapes in two other packages. If this row is adopted, Slice C should rate it, probably as `drifted`. |
| 6 | [WT-4] 4.3 "A quiet task does nothing" | "First wake after quiet is bounded … (~100 ms), followed by burst mode." | "First wake after quiet is bounded by one pass of SimpleBroker's polling strategy, followed by burst mode; the strategy, not Weft, owns the pass length and burst behavior ([CC-2.2.1])." | concrete->lifted. [CC-2.2.1] says "The strategy owns SQLite `data_version`, burst and backoff behavior". The figure stays in SimpleBroker. |
| 7 | [WT-4] 4.3 "Ask who owns this lifetime" | "A shared Manager, a detached child task (`stop_with_parent=False`), and a waiting client" | "A shared Manager, a child task launched detached from the Manager's lifetime, and a waiting client" | concrete->lifted |
| 8 | [WT-4] 4.4 "The client mirrors the CLI" | "The Python client and the CLI … over shared `weft/commands/` capabilities." | "The Python client and the CLI expose the same mental model and verbs over one shared command-capability layer that both adapters call." | concrete->lifted (the contract is `11-CLI_Architecture_Crosswalk.md` and [PY-1], already in the Contract cell) |
| 9 | [WT-8] "Specs are the contract" row, Evolved cell | "The theory now sits above … a wrong spec changes" | "The theory now sits above the specs for design judgment; a spec that contradicts the theory changes through a planned change, and the theory sentence becomes that plan's acceptance test ([WT-0])" | abstract->sharpened. "a wrong spec changes" fits both an ad-hoc spec patch and a planned change. The replacement uses the [WT-0] owner rule and plan §1 mismatch kind 1. It deliberately does not restate the hub's [AT-THEORY-0] rules, which [WT-0] carries; the former §9 fork on the hub's sequencing wording is closed, because the hub resolved it at `e6ac579` with the two-part presumption. |
| 10 | [WT-0] friction paragraph | "charging a guessed resource budget per task, clamping … 50 ms polling cap, splitting a cohesive state machine by line count, or using" | "charging a guessed resource budget per task, clamping a reactor loop with a fixed polling cap, splitting a cohesive state machine by line count, or using" | concrete->lifted. The figure is the [ALT-WT-002] candidate's own value and is lifted there too (row 11). |
| 11 | [ALT-WT-002] Candidate | "- Candidate: Wait caps of 50 ms and 25 ms … still made progress." | "- Candidate: Short fixed wait caps on worker, signal, parent-loss, and PONG discovery, so a reactor that missed an event still made progress." | concrete->lifted |
| 12 | [ALT-WT-002] Current consequence | "- Current consequence: The first wake after quiet is one SimpleBroker pass, … burst mode." | "- Current consequence: The first wake after quiet is one pass of SimpleBroker's polling strategy. After that the strategy is in burst mode; the strategy, not Weft, owns the pass length ([CC-2.2.1])." | concrete->lifted (same lift as altitude row 6) |
| 13 | [ALT-WT-004] Current consequence | "plan is a draft. TaskMonitor still registers with the heartbeat … TaskMonitor is enabled." | "plan is a draft. TaskMonitor still registers with the heartbeat service. The landed part is commit `da5fa0f6`: the Manager wants the heartbeat service only when TaskMonitor is enabled." | concrete->lifted (drops a `weft/` path and a private attribute; the module is already named as TaskMonitor) |
| 14 | [ALT-WT-008] Current consequence | "- Current consequence: The constant is still in `weft/_constants.py` … or starts one." | "- Current consequence: The constant is still defined in Weft's constants module and is always `True`. The retirement plan is a draft. `weft run` finds a Manager or starts one." | concrete->lifted |
| 15 | [ALT-WT-021] Candidate | "polling them in `10 ms` (100 Hz) helper threads … do not hang `TextIOWrapper`." | "polling them on a fixed short interval in helper threads so pipe `close()` and post-exit grandchild pipe leaks do not hang `TextIOWrapper`." | concrete->lifted |
| 16 | [ALT-WT-021] Reason | "- Reason: Wastes ~200–300 wakeups/sec and hundreds of context switches per idle subprocess, couples … buffers child" | "- Reason: Spends wakeups and context switches on every idle subprocess at a fixed polling rate, couples output latency to a polling quantum, buffers child" | concrete->lifted (the measured rates stay in the 2026-10-29 plan) |
| 17 | [ALT-WT-021] Evidence | "- Evidence: Owner, 2026-10-29; commit `4c490043` … `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md`." | "- Evidence: Owner, 2026-10-29; commit `4c490043` (adapter introduction) and commit `d4381877` (adapter replacement); `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md`, which records the polling adapter's measured idle-child cost." | concrete->lifted (drops the private class name `_PipeAdapter` and the ms/Hz/count figures), plus one evidence correction that is not altitude: `540707a3` becomes `d4381877` (§4a row; see the extension blocks). |
| 18 | [REV-WT-001] Current account | "adapters, and explicit service deadlines). A quiet task … 25 ms/50 ms polling caps." | "adapters, and explicit service deadlines). A quiet task does zero turns and uses no defensive fixed polling caps." | concrete->lifted |
| 19 | [REV-WT-001] Supersedes | "- Supersedes: Earlier watcher and control paths clamped … noticed on the next tick." | "- Supersedes: Earlier watcher and control paths clamped wait timeouts with short fixed polling caps ([ALT-WT-002]) so missed local transitions would still be noticed on the next tick." | concrete->lifted. The cross-reference keeps the link to the record of the rejected caps. |

All nine rows are pasted in place by span. Rows 6, 7, and 8 edit cells of
the existing [WT-4] table; the Kind-column draft that also carried them
(cells marked `<!-- altitude -->`) is not applied. Rows 1, 2, and 3 change
text that spans more than one line in `docs/program-theory.md`, so each
also has a labelled Replacement block below. Rows 4 to 9 change text
inside a single table row and need no block.

Row 2 ([WT-2] item 3, the detached-launch sentence):

Replacement (`docs/program-theory.md`):
```markdown
   $B$ in an isolated `spawn`-context process. Furthermore, ordinary user tasks
   are launched detached from the Manager's lifetime ([MA-3]) so running
   tasks survive Manager drain, idle timeout, crash, or supersession. Because
```

Row 1 ([WT-2] item 3, the in-memory child table sentence):

Replacement (`docs/program-theory.md`):
```markdown
   tasks survive Manager drain, idle timeout, crash, or supersession. Because
   neither the submitting parent nor a replacement Manager holds the child in
   its OS `ppid` tree or its in-memory child table, `weft.state.tasks.<tid>`
```

Row 3 ([WT-2] item 6, last bullet):

Replacement (`docs/program-theory.md`):
```markdown
   - **Higher-level intelligence** sits above Weft. Weft runs bounded agent
     work as ordinary tasks through its core agent runtime; it does not plan
     or orchestrate agents, and it does not own provider accounts, provider
     health, or approval policy ([AR-0.0], [AR-8]).
```

Sentences walked and left unchanged (so the reviewer can see the walk was
complete):

- [WT-2] items 1, 2, 4, 5, and the remaining bullets of item 6.
- Every [WT-3] row other than TaskSpec and Evidence.
- Every [WT-4] row other than rows 6 to 8.
- [WT-8]'s opening paragraph and its other three rows.

Two drift findings in [WT-2] item 1 and [WT-4] are listed under Execution
log, Out-of-scope observations, and are not altitude.

One more [WT-8] sentence is a candidate the reviewer may want to raise. It
is not changed here because no tree evidence settles it: "Every principle in
[WT-4] is an application of that line." With the whole-shape/judgment classification, several
judgment rows and several whole-shape rows are not applications of the
founding line (for example "One path on all platforms" and "Specs are what
is"). The owner may prefer "Every 4.1 row is an application of that line".

#### Altitude pass extension blocks (sections [WT-0], [ALT-WT-002], [ALT-WT-004], [ALT-WT-008], [ALT-WT-021], [REV-WT-001])

Drafted 2026-11-02 for task 10's six further sections, under the plan body §4
rule (with its clarification: commit hashes and `docs/` paths to dated plans
or lessons are allowed in Evidence lines). Module names, queue names, and
public SimpleBroker, stdlib, and OS API names (`notify_activity()`,
`next_wait_timeout()`, `TextIOWrapper`, `Popen.wait()`, `PeekNamedPipe`,
`CancelSynchronousIo`, pidfd, kqueue) are allowed, following the precedent
above. Owner quotations are untouched. Rows 10 to 20 are in the table above;
each replaces text that spans more than one line, so each has a labelled
block, the exact post-change span.

Verification. The six sections were selected by a heading-range awk script
(session scratch, not a repository file) and grepped with a pattern wider
than the plan's presence check, because the evidence-line clause also
forbids private names (`` `_X ``), measured figures (`~N`, `N Hz`), and
`docs/` paths:

```text
$ awk -f sections.awk docs/program-theory.md | grep -E 'weft/[A-Za-z_/]+\.py|weft/|self\._|[0-9]+ ms|`_[A-Za-z]|docs/|~[0-9]|[0-9]+ Hz'
WT-0: 53: 50 ms polling cap, splitting a cohesive state machine by line count, or using
WT-0: 65: `../simplebroker/docs/program-theory.md` ("simplebrokerness"), and Taut has
WT-0: 66: `../taut/docs/program-theory.md` ("tautness").
WT-0: 73: | Specifications (`docs/specifications/`) | What is the exact intended behavior now? |
WT-0: 74: | Plans (`docs/plans/`) | What change was made or proposed, under what evidence and premises? |
WT-0: 76: | Lessons (`docs/lessons.md`) | What durable rule did a surprise or repeated failure establish? |
ALT-WT-002: 341: - Candidate: Wait caps of 50 ms and 25 ms on worker, signal, parent-loss, and
ALT-WT-002: 348:   about 100 ms. After that the strategy is in burst mode.
ALT-WT-004: 382:   (`weft/core/monitor/task_monitor.py`, `_heartbeat_registered`). The landed
ALT-WT-008: 443: - Current consequence: The constant is still in `weft/_constants.py` and is
ALT-WT-021: 682:   polling them in `10 ms` (100 Hz) helper threads so pipe `close()` and
ALT-WT-021: 687: - Reason: Wastes ~200–300 wakeups/sec and hundreds of context switches per
ALT-WT-021: 708:   commit `540707a3` (`_PipeAdapter` 100 Hz polling measured at ~890 context
ALT-WT-021: 709:   switches and ~35 ms CPU per 3 s idle child on macOS);
REV-WT-001: 787:   uses no defensive 25 ms/50 ms polling caps.
REV-WT-001: 789:   short 25 ms and 50 ms polling caps so missed local transitions would still be
REV-WT-001: 795:   - contemporaneous: `docs/plans/2026-09-17-watcher-reactor-restoration-plan.md`
REV-WT-001: 796:     and `docs/plans/2026-09-18-event-routed-manager-pong-plan.md`
REV-WT-001: 797:   - contemporaneous: `docs/specifications/01-Core_Components.md` `[CC-2.2.1]`
REV-WT-001: 798:     and `docs/specifications/07-System_Invariants.md` `IMPL.8`–`IMPL.11`
```

Kept hits: [WT-0] lines 65–66 name the sibling-repository theories in prose
(not under `weft/`, not an evidence line); lines 73, 74, and 76 are the
surfaces table's directory labels, the surface's identity rather than
evidence. Every other hit is covered by rows 10 to 20. Facts re-checked
before lifting, so no lift removes a true fact without a home:
`grep -rn '_heartbeat_registered' weft/` returns ten hits, all in
`weft/core/monitor/task_monitor.py` (TaskMonitor still registers);
`grep -n 'WEFT_MANAGER_REUSE_ENABLED' weft/_constants.py` returns
`1827:WEFT_MANAGER_REUSE_ENABLED: Final[bool] = True`; `git log -1 da5fa0f6`
is "fix: desire the heartbeat service only for its TaskMonitor dependent";
the ~890 context switches and ~35 ms CPU per 3 s idle child are recorded in
`docs/plans/2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` (lines
30, 158, 360); [CC-2.2.1] (`docs/specifications/01-Core_Components.md` line
193) says the strategy "owns SQLite `data_version`, burst and backoff
behavior". A dry run applied all eleven blocks to a scratch copy of
`docs/program-theory.md` (each original span matched exactly once); in the
result the six sections give no output for `weft/|self\._|[0-9]+ ms`, the
wider pattern hits only the kept surfaces-table lines 73 and 74, and every
`Reconsider when:` line is unchanged. The too-abstract direction was walked
sentence by sentence and flags nothing. [ALT-WT-021] Current consequence
stays at OS-primitive altitude (pidfd, kqueue, overlapped I/O,
`PeekNamedPipe`/`CancelSynchronousIo`): none is a path, private name, or
figure; moving it to contract altitude ([RM-5.2]) would be an owner choice,
not this rule.

Evidence correction (row 17; not altitude). The draft found that the
[ALT-WT-021] Evidence line cites only `540707a3`, which is the measurement
baseline the PR #48 repair plan names but neither introduces nor removes the
adapter; the two commits that do are added beside it (round 5 confirmed
`540707a3` is the recorded baseline). Verified 2026-11-02:

```text
$ git -C /Users/van/Developer/weft log --oneline -1 540707a3
540707a3 Fix PostgreSQL startup readiness and schema preflight
$ git -C /Users/van/Developer/weft log --oneline -1 4c490043
4c490043 Repair command lifetime ownership and bound pending heap retention
$ git -C /Users/van/Developer/weft log --oneline -1 d4381877
d4381877 Replace subprocess polling with bounded readiness-driven ownership
$ git -C /Users/van/Developer/weft log -S'_PipeAdapter' --oneline
d4381877 Replace subprocess polling with bounded readiness-driven ownership
784d6b7a Preserve captured command output and retire serviced retry deadlines
4c490043 Repair command lifetime ownership and bound pending heap retention
```

`4c490043` introduced the polling adapter and `d4381877` replaced it. The row
17 block cites both and keeps everything else in the line; the drafted
[ALT-WT-021] `Kind:` line in "[WT-6] tags" is corrected the same way. Logged
in §4a.

Each of rows 10 to 20 needs one §4a Deviation Log row at task 13 (the
identifier in the first column, `n/a — theory altitude` in the last). No
owner quotation was among the hits, so no "allowed owner quotation" row is
needed for these six sections.

| Section | Rows |
|---------|------|
| [WT-0] | 1 (row 10) |
| [ALT-WT-002] | 2 (rows 11, 12) |
| [ALT-WT-004] | 1 (row 13) |
| [ALT-WT-008] | 1 (row 14) |
| [ALT-WT-021] | 3 (rows 15, 16, 17) |
| [REV-WT-001] | 3 (rows 18, 19, 20) |
| Total | 11 |

Row 10 ([WT-0]):

Replacement (`docs/program-theory.md`):
```markdown
charging a guessed resource budget per task, clamping a reactor loop with a
fixed polling cap, splitting a cohesive state machine by line count, or using
```

Row 11 ([ALT-WT-002] Candidate):

Replacement (`docs/program-theory.md`):
```markdown
- Candidate: Short fixed wait caps on worker, signal, parent-loss, and PONG
  discovery, so a reactor that missed an event still made progress.
```

Row 12 ([ALT-WT-002] Current consequence):

Replacement (`docs/program-theory.md`):
```markdown
- Current consequence: The first wake after quiet is one pass of
  SimpleBroker's polling strategy. After that the strategy is in burst mode;
  the strategy, not Weft, owns the pass length ([CC-2.2.1]).
```

Row 13 ([ALT-WT-004] Current consequence; the span starts at the line that
opens "plan is a draft."):

Replacement (`docs/program-theory.md`):
```markdown
  plan is a draft. TaskMonitor still registers with the heartbeat service.
  The landed part is commit `da5fa0f6`: the Manager wants the heartbeat
  service only when TaskMonitor is enabled.
```

Row 14 ([ALT-WT-008] Current consequence):

Replacement (`docs/program-theory.md`):
```markdown
- Current consequence: The constant is still defined in Weft's constants
  module and is always `True`. The retirement plan is a draft. `weft run`
  finds a Manager or starts one.
```

Row 15 ([ALT-WT-021] Candidate; the span starts at the second line of the
bullet):

Replacement (`docs/program-theory.md`):
```markdown
  polling them on a fixed short interval in helper threads so pipe `close()`
  and post-exit grandchild pipe leaks do not hang `TextIOWrapper`.
```

Row 16 ([ALT-WT-021] Reason; first two lines of the bullet, the remaining two
lines unchanged):

Replacement (`docs/program-theory.md`):
```markdown
- Reason: Spends wakeups and context switches on every idle subprocess at a
  fixed polling rate, couples output latency to a polling quantum, buffers child
```

Row 17 ([ALT-WT-021] Evidence; altitude lift plus the commit correction):

Replacement (`docs/program-theory.md`):
```markdown
- Evidence: Owner, 2026-10-29; measurement baseline commit `540707a3`, with
  commit `4c490043` (adapter introduction) and commit `d4381877` (adapter
  replacement); `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md`,
  which records the polling adapter's measured idle-child cost.
```

Row 18 ([REV-WT-001] Current account, last two lines):

Replacement (`docs/program-theory.md`):
```markdown
  adapters, and explicit service deadlines). A quiet task does zero turns and
  uses no defensive fixed polling caps.
```

Row 19 ([REV-WT-001] Supersedes):

Replacement (`docs/program-theory.md`):
```markdown
- Supersedes: Earlier watcher and control paths clamped wait timeouts with
  short fixed polling caps ([ALT-WT-002]) so missed local transitions would
  still be noticed on the next tick.
```

### [WT-3] coverage

Test applied to each concept: does a possessor of the theory need this row
to judge whether a feature belongs?

| Concept | Outcome | Reason | Tree evidence (re-derived) |
|---------|---------|--------|----------------------------|
| Runner plugins | yes | "Should this runtime mechanic live in core or in a runner?" is a frequent placement question, and [CC-3.1] answers it ("plugins own runtime-specific execution details"). Without a row, the theory gives no owner for limited runners, which the Product Boundary admits. | `weft/ext.py:553` `class RunnerPlugin(Protocol)`, `weft/ext.py:63` `class RunnerHandle`; `weft/_runner_plugins.py:41` `get_runner_plugin`; `01-Core_Components.md` [CC-3.1] line 947, [CC-3.2] line 984; `02-TaskSpec.md` [TS-1.3] line 480 |
| Agent sessions | yes | Agent feature fit is the most contested placement question. [AR-0.0] puts the agent runtime in core while refusing provider lifecycle and orchestration, and [AR-8] refuses "a second durable state system for conversations". Without a row, a possessor cannot tell a core agent feature from scope creep. | `weft/core/agents/` (runtime, backends, provider_cli); `weft/core/tasks/sessions.py` (`AgentSession`, per [AR-9]); `13-Agent_Runtime.md` [AR-0.0] line 17, [AR-5] line 434, [AR-6] line 463, [AR-8] line 839 |
| Pipelines | no | [PL-1.1] makes a pipeline run a task with its own TID, queues, and terminal state, and its stages "remain ordinary tasks". [WT-5] already refuses the DAG/join compiler. Composition needs no owner of its own. | `12-Pipeline_Composition_and_UX.md` [PL-1.1] line 133; `05-Message_Flow_and_State.md` [MF-4] line 518 |
| Endpoints | no | A named endpoint is a runtime-state record published by the long-lived task that owns it ([CC-2.4.1]: "one thin discovery primitive"). It is the Runtime-state queue row applied to names. | `weft/core/endpoints.py`; `01-Core_Components.md` [CC-2.4.1] line 823; `05-Message_Flow_and_State.md` [MF-3.1] line 394 |
| Streaming | no | Streaming markers are "runtime-only ownership hints" in `weft.state.streaming` over a task's own outbox. This is Runtime-state queue plus Task. | `01-Core_Components.md` lines 778–781; `05-Message_Flow_and_State.md` line 173; `00-Quick_Reference.md` line 41 |
| Service registry and convergence | yes | "Should this be a managed service, who restarts it, and which of two live owners wins?" is decided here. The Manager row says "spawns and supervises" but names neither the registry nor the convergence owner, and [ALT-WT-007] refuses a convergence alternative with no row to attach to. | `weft/_constants.py:1396` `WEFT_SERVICES_REGISTRY_QUEUE = "weft.state.services"`; `weft/core/manager_services.py:60` `ManagedServiceState` (the sole launch/restart/backoff state owner per [MA-1.6]); `weft/core/service_convergence.py` ("Shared convergence primitives for runtime-owned services"); `03-Manager_Architecture.md` [MA-1.6], [MA-1.6a] lines 572–573; `07-System_Invariants.md` MANAGER.3 line 996, OBS.15 |
| Heartbeat | no | The heartbeat is a service task that emits periodic messages to consumers that register ([MF-3.2]: "task-shaped"). [ALT-WT-004] already refuses it as a clock. | `weft/core/tasks/heartbeat.py`, `weft/core/heartbeat.py`; `05-Message_Flow_and_State.md` [MF-3.2] line 441 |
| Retention | no | Retention is answered by existing rows. Runtime queues are consumed or owned by the single deleter (Runtime-state queue row, `LivenessMonitor`). Global-log retention pruning is TaskMonitor's task-internal work (Task-internal working state row). Explicit pruning is bounded by OBS.16 and OBS.17. | `weft/core/pruning/` (`retention.py`, `runtime.py`); `07-System_Invariants.md` OBS.16 line 672, OBS.17 line 676, LIVENESS.R3 line 697 |
| Interactive | no | Interactive mode is queue-mediated line IO in a task over a runner session ([CLI-1.1.1]: "`--interactive` is queue-mediated line IO, not PTY emulation"). It composes from Task, Source adapter, and Platform readiness adapter. | `weft/core/tasks/interactive.py`; `10-CLI_Interface.md` lines 164, 299–304; `01-Core_Components.md` [CC-3.1] (interactive runner activity callback) |

Tally: yes 3, no 6.

Rows to append to the [WT-3] table, after the row that opens "| Evidence |",
in the existing format:

Replacement (`docs/program-theory.md`):
```markdown
| Runner plugin | A named runtime backend that executes a task's work behind an opaque runtime handle; core sees ordinary runner options and the handle, never the runtime's internals | The runner plugin surface (`weft.ext` contracts, resolution in `weft._runner_plugins`); each plugin owns its runtime-specific execution | `01-Core_Components.md` [CC-3.1], [CC-3.2]; `02-TaskSpec.md` [TS-1.3] |
| Agent runtime and session | Agent-backed work run as an ordinary task: the agent TaskSpec section, the runtime adapters, and a per-task persistent conversation in a dedicated session subprocess; no second durable conversation store | The agent runtime modules (`weft.core.agents`); the owning task's session (`weft.core.tasks.sessions`) for persistent scope | `13-Agent_Runtime.md` [AR-0.0], [AR-5], [AR-6], [AR-8] |
| Service registry | `weft.state.services` runtime records naming the live owner of each Manager and managed service, plus the convergence policy that selects one live owner among duplicates | The Manager (`weft.core.manager_services` for managed-service state, `weft.core.service_convergence` for convergence) | `03-Manager_Architecture.md` [MA-1.6], [MA-1.6a]; `07-System_Invariants.md` MANAGER.3, OBS.15; [ALT-WT-007] |
```

The Service registry row says "the convergence policy". It does not say
"one reversible convergence policy", so that it does not prejudge §9 fork 1.

Lines to add directly under the [WT-3] table:

Replacement (`docs/program-theory.md`):
```markdown
Concepts that compose from the rows above and need no row of their own:

- **Pipeline** is a task whose stages are ordinary tasks (Task, Control
  message, Completion fact; [PL-1.1]). [WT-5] refuses the DAG/join compiler.
- **Endpoint** is a name record in `weft.state.endpoints` published by the
  task that owns the name (Runtime-state queue, Task; [CC-2.4.1]).
- **Streaming** markers in `weft.state.streaming` are ownership hints over a
  task's own outbox (Runtime-state queue, Task; [CC-2.4]).
- **Heartbeat** is a service task that emits messages to registered
  consumers, never a clock (Task, Reactor and wake arbiter; [MF-3.2],
  [ALT-WT-004]).
- **Retention** is consumption, the single task-state deleter, and
  TaskMonitor's private pruning (Runtime-state queue, Task-internal working
  state; OBS.16, OBS.17, LIVENESS.R3).
- **Interactive** mode is queue-mediated line IO over a runner session
  (Task, Source adapter, Platform readiness adapter; [CLI-1.1.1]).
```

### Fork 2 candidates (resolved by the owner, 2026-11-02)

The three candidates that Task 7 could not tie to both a contract and
firing evidence were decided by the owner on 2026-11-02 and are now in the
[WT-2a] text above:

1. **Single operator trust domain: admitted** as [WT-2a.10] (now
   [WT-2a.11]) on owner
   authority ("We keep the single operator trust domain"), worded on
   SimpleBroker's security model at the owner's direction ("Adopt or adapt
   what simplebroker has for security", 2026-11-02): trusted by placement,
   checked for shape, intent never inspected. Its evidence is the absence
   of authentication fields in the strict control envelope and the absence
   of any sanitizer, which is what a scoping invariant looks like in a
   tree; no positive test can fire it. The spec-level counterpart, adapting
   SimpleBroker's "General security considerations" into the Observability
   and Security section of `docs/specifications/00-Overview_and_Architecture.md`,
   is scoped to cleanup plan 10 (docs and broker boundary).
2. **Lossless TID rendering: admitted, as a new decision**, into
   [WT-2a.1]. The owner's words: "This is a new decision: every rendering
   of the TID is lossless. We decided this in this session. The old lossy
   encoding is going away, as should its tests, associated search code,
   etc." Because the invariant is new, audit row I-1's `drifted` status is
   created by this decision rather than discovered; [OBS.5], the ten-digit
   short form, its tests, the candidate-discovery and ambiguity-resolution
   code, and the liveness host's title regex are retired by cleanup plan
   12 (TID display encoding). `[REV-WT-004]` records the clause as new.
3. **Never through a shell: admitted** into [WT-2a.9] (now [WT-2a.10]) with the owner's
   statement quoted. The one-line test that the submission path rejects
   `shell=True` is carried by cleanup plan 11.

The drafted count was ten identity invariants admitted and none forked.
After the owner's 2026-11-02 endorsement (§9 item 4), the count is fourteen
identity invariants ([WT-2a.1] to [WT-2a.14]), none forked.

### Slice C, Task 14: theory-versus-code audit table (draft)

Plan: `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`, §5 task 14.
Baseline: Weft `ec50bd57` (working tree with the uncommitted plan file). Installed
SimpleBroker 8.7.2 and simplebroker-pg 4.7.2 (`.venv/lib/python3.14/site-packages`).
This table measures how far the document and the tree agree. It does not measure
possession. The identity-invariant rows use the ten §4c item 3 candidates. Another
agent is drafting [WT-2a] at the same time, so the final list may be shorter, and
rows I-1 to I-10 were re-keyed to the landed identifiers at task 14 (2026-11-02); the map below is the record.

### Execution log

#### Comprehension questions (plan §3), answered before the first edit

1. *My answer:* The spec changes, through a planned cleanup-series change (here
   plan 3, control-reply routing). The theory sentence ("push, never pull-scan")
   stays as written and becomes that plan's acceptance test. Under the owner's
   2026-10-09 rule the theory does not bend to ratify the spec.
   *Expected:* the spec, through a cleanup-series plan; the theory sentence stays
   and becomes the acceptance test. **Match.**
2. *My answer:* The existing entry is "Blunt local mechanism vs. classical CS model".
   It is about *proposals*: an author invents a lane, timer, or table without naming
   the CS problem. Altitude collapse is about the *theory text's own altitude*: a
   sentence too concrete (path, figure) or too abstract (two incompatible designs
   could cite it). They are different falsifiers, so they cross-reference each other
   and stay separate entries.
   *Expected:* same. **Match.**
3. *My answer:* Before a subsystem gets a module theory, check whether it has
   escaped the product's law, meaning it invented competing concepts. If it has,
   simplify it back under the product theory instead of giving it its own theory.
   The repository's position (owner, 2026-11-01) is one central theory and no
   module theories. Slice B records that as a whole-shape choice with a falsifier.
   *Expected:* same. **Match.**
   [Historical answer. The position it cites was withdrawn from this plan's
   scope by the owner on 2026-11-02; question 3 now asks only about the
   hub's module-divergence check (named at `e6ac579`).]

#### Row-count command (principle rows across [WT-4] tables 4.1 to 4.4)

```
$ awk '/^### 4\.1/{f=1} /^## What Weft is not/{f=0} f && /^\| / && !/^\| Principle/ && !/^\|---/' docs/program-theory.md | wc -l
      30
```

Invariant candidates (plan §4c item 3): 10. Expected table rows: 10 + 30 = 40.
The command output after the table records the actual count.

#### Spec-code resolution (codes cited in the Contract column)

```
$ for c in IMMUT.1 IMMUT.2 IMMUT.3 STATE.1 STATE.2 QUEUE.1 QUEUE.6 QUEUE.7 QUEUE.8 OBS.1 OBS.6 OBS.13 IMPL.5 IMPL.6 IMPL.8 IMPL.10 IMPL.11 LIVENESS.R3 LIVENESS.R7; do printf "%s: " $c; grep -c "\*\*$c\*\*\|\[$c\]" docs/specifications/07-System_Invariants.md; done
IMMUT.1: 1  IMMUT.2: 1  IMMUT.3: 1  STATE.1: 1  STATE.2: 1  QUEUE.1: 1  QUEUE.6: 2  QUEUE.7: 1
QUEUE.8: 6  OBS.1: 1  OBS.6: 3  OBS.13: 2  IMPL.5: 1  IMPL.6: 1  IMPL.8: 1  IMPL.10: 1
IMPL.11: 2  LIVENESS.R3: 2  LIVENESS.R7: 1
```

Bracketed codes SB-0.1, SB-0.2, SB-0.4, SB-0.4a, CLI-1.1.1, MF-1, MF-3, MF-3.2,
MF-5, MA-1, MA-1.4, MA-1.6a, MA-1.8, MA-3, CC-2.2.1, CC-2.5, CC-3.4, RM-5.2, PY-1,
PY-2, TS-0 each resolve to at least one file under `docs/specifications/`
(`grep -rl "\[<code>\]" docs/specifications/`). `00-Overview_and_Architecture.md` has
no section codes, so it is cited by heading: "Product Boundary", "Runtime Layers"
(the "Single current contract" paragraph), and "Observability and Security".

#### Proof for drifted and theory-revision evidence (ls and grep, pasted)

```
$ ls weft/commands/run.py weft/commands/tasks.py weft/core/tasks/sessions.py weft/core/tasks/interactive.py weft/core/tasks/service.py weft/core/family_scheduling.py weft/helpers/__init__.py weft/liveness/host.py weft/core/monitor/task_monitor.py weft/core/manager.py weft/client/_namespaces.py tests/core/test_client.py tests/commands/test_active_interactive_lifecycle.py tests/tasks/test_control_channel.py tests/system/test_short_tid.py tests/core/test_family_scheduling.py tests/tasks/test_task_monitor.py tests/core/test_manager.py docs/specifications/05-Message_Flow_and_State.md docs/specifications/07-System_Invariants.md docs/specifications/03-Manager_Architecture.md docs/specifications/14-Python_API_Surfaces.md
(all 22 paths listed; none missing)

weft/commands/run.py:486:def _matching_stop_ack(queue: Queue, tid: str, request_id: str) -> bool:
weft/commands/run.py:503:def _stop_interactive_run_session(session: _LiveRunSession) -> None:
weft/commands/tasks.py:1803:def _observe_control_envelopes(
weft/commands/tasks.py:1901:def _await_control_surface(
weft/commands/tasks.py:1975:                    interval=CONTROL_SURFACE_WAIT_INTERVAL,
weft/core/tasks/sessions.py:734:        deadlines = [now + ACTIVE_CONTROL_POLL_INTERVAL]
weft/core/tasks/interactive.py:374:            time.sleep(INTERACTIVE_OUTPUT_DRAIN_POLL_INTERVAL)
weft/core/tasks/interactive.py:530:            time.sleep(min(INTERACTIVE_STOP_POLL_INTERVAL, remaining))
weft/core/tasks/service.py:70:                    timeout=LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS
weft/core/tasks/service.py:488:                    wait_timeout = LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS
weft/core/family_scheduling.py:386:    def retire(self, tid: str) -> None:
weft/core/family_scheduling.py:419:            self.finish_recovery()
weft/core/family_scheduling.py:421:    def finish_recovery(self) -> None:
weft/helpers/__init__.py:132:def tid_short_form(tid: str) -> str:
weft/helpers/__init__.py:146:    return f"{(grain + counter * 2_441_406) % 10**10:0{TASKSPEC_TID_SHORT_LENGTH}d}"
weft/liveness/host.py:50:        short_tid = re.escape(tid_short_form(expected_tid))
weft/core/monitor/task_monitor.py:5714:    def _ensure_heartbeat_registered(self) -> None:
weft/core/manager.py:4874:    def _begin_superseded_shutdown(self) -> None:
weft/core/manager.py:4891:            self._drain_leader_tid = None
docs/specifications/07-System_Invariants.md:343:  The form is not unique: a short form matching more than one full TID is
docs/specifications/05-Message_Flow_and_State.md:496:- the supervised `TaskMonitor` uses heartbeat registrations for periodic
docs/specifications/05-Message_Flow_and_State.md:213:- `ctrl_out` carries non-PONG task-local replies and terminal notifications.
docs/specifications/05-Message_Flow_and_State.md:317:- non-PONG keyed control replies remain owned and retired by the prober that
docs/specifications/05-Message_Flow_and_State.md:334:  handoff detail, but public acknowledgement remains the post-unwind
tests/core/test_client.py:235:    "system": (
tests/core/test_client.py:236-        SystemNamespace,
tests/core/test_client.py:237-        {"builtins", "dump", "load", "status", "tidy"},
tests/core/test_family_scheduling.py:189:def test_parent_exit_contracts_pending_bucket_and_exact_removal_survives_merge() -> (
tests/tasks/test_control_channel.py:162:def test_stop_kill_command_sends_keyed_ack(
tests/commands/test_active_interactive_lifecycle.py:430:def test_active_stop_waits_named_budget_and_accepts_only_own_ack(
tests/system/test_short_tid.py:52:def test_short_form_folds_counter_and_wraps(counter: int, expected: str) -> None:
tests/core/test_client.py:242:def test_client_api_parity_guard_matches_current_public_matrix() -> None:
tests/core/test_manager.py:9765:def test_manager_superseded_self_record_drains_children_without_stopping(
tests/tasks/test_task_monitor.py:10118:def test_task_monitor_heartbeat_failure_records_health_but_still_cycles(
```

The path-existence check for every evidence cell, aligned rows included, runs over
the finished table and is pasted after it.

#### Candidate checks the plan required me to confirm or reject from the tree

1. **Pull-scanned STOP/KILL acks. Confirmed, drifted (plan 3).**
   `_stop_interactive_run_session` (weft/commands/run.py:503) writes a keyed STOP
   with no `reply_to` to the target's `ctrl_in`. It then waits on a
   `QueueChangeMonitor` over the *target's* `ctrl_out` and calls
   `_matching_stop_ack`, which peeks every row of that queue for a matching
   `request_id`. `_observe_control_envelopes` (weft/commands/tasks.py:1803) scans a
   bounded `ctrl_out` range for terminal envelopes and KILL acks. It also
   exact-deletes ordinary control rows that other requesters may own. Neither
   requester mints a TID or watches its own `ctrl_in`. Spec 05 [MF-3] sanctions the
   scan in three sentences: "`ctrl_out` carries non-PONG task-local replies and
   terminal notifications" (line 213); "non-PONG keyed control replies remain owned
   and retired by the prober that issued the `request_id`: an exact matching reply
   is deleted on match, and the prober may sweep rows bearing its own request ID"
   (line 317); and "public acknowledgement remains the post-unwind `ctrl_out`
   reply" (line 334). The spec lags the theory, so the row is drifted, not a theory
   revision. Note: the requester does wake on queue activity rather than on a
   blind timer, so this is a pull-scan of a foreign queue, not a timer poll. The
   pull is the drift.
2. **About 50 ms timer floors on reactor edges. Confirmed at four sites and
   rejected at three.** For each site, the event that cannot arrive:
   - `weft/liveness/host.py`: **no timer at this site.** The file holds only exact
     identity inspection and the TID-short title regex. The pointer is wrong for
     timers. It is relevant to row I-1.
   - `weft/core/launcher.py:82-101` `_start_parent_loss_watcher`: a 50 ms floor and
     200 ms ceiling in a dedicated source-adapter thread. Absent event named in the
     docstring of `PARENT_LOSS_WAKE_INTERVAL_FLOOR` (weft/_constants.py:702): "Parent-process
     death has no portable notification". **Aligned.** (Linux `PR_SET_PDEATHSIG`
     or a parent pidfd, and macOS kqueue `NOTE_EXIT`, could be narrow platform
     leaves, but the absence is named, so by the rule this site is aligned.)
   - `weft/core/tasks/sessions.py:725-739` `_terminal_handoff_wait_seconds`: the
     agent-session result loop waits on the response pipe with a 50 ms
     `ACTIVE_CONTROL_POLL_INTERVAL` ceiling, so that it re-polls `is_alive()` and
     the `cancel_requested` predicate. No absent event is named. Producer exit has
     a kernel sentinel that could join the same wait, and cancellation is a
     predicate, not an event. **Drifted (plan 2).**
   - `weft/core/tasks/interactive.py:364-374` `_interactive_drain_remaining`
     (`time.sleep` 10 ms until both stream-closed flags are set) and `:511-530`
     `_interactive_shutdown` (`time.sleep` up to 50 ms while `session.is_alive()`).
     Pipe EOF and process exit are both readiness or sentinel events. No absent
     event is named. **Drifted (plan 2).**
   - `weft/core/tasks/service.py:64-75` `iter_items` (50 ms `input_queue.get`
     timeout, to re-check `stop_event`/`task_stop_event`) and `:484-496` (50 ms
     `_worker_result_event.wait` cap, to re-check `_has_active_worker_threads`).
     These are local sources that could post the existing `_service_worker_stop`
     sentinel or a worker-exit event. Per [ALT-WT-002] "add the adapter, not a
     cap". **Drifted (plan 2).**
   - `weft/core/queue_wait.py:104-112` `QueueChangeMonitor._run_multi_waiter`: a
     1 s chunk (`QUEUE_CHANGE_MONITOR_WAITER_TIMEOUT_SECONDS`), not 50 ms. The
     waiter is event-driven and the chunk only re-checks `_stop_event`, which is
     already passed to `create_activity_waiter_for_queues`. This is a bounded chunk,
     not a reactor floor. **Aligned** (minor, not assigned).
   - CLI caller of the monitor, `weft/commands/tasks.py:1901-1987`
     `_await_control_surface`: every wait is capped at
     `CONTROL_SURFACE_WAIT_INTERVAL` = 0.05 s (weft/_constants.py:723, documented
     as a "Polling interval"), although every input is a watched queue. No absent
     event is named. **Drifted (plan 2).**
   - Not in the pointer list but found by grep: `MANAGER_CHILD_SENTINEL_WINDOWS_WAIT_SLICE_SECONDS`
     = 0.05 (weft/_constants.py:594) names its absent capability (the 63-handle
     Windows wait limit). **Aligned.**
3. **Two-clock heartbeat. Confirmed, drifted (plan 6).** Spec 05 [MF-3.2] line
   496: "the supervised `TaskMonitor` uses heartbeat registrations for periodic
   wake messages to its own `T{tid}.inbox`; if registration is temporarily
   unavailable, the monitor records operational health and falls back to its
   bounded local interval". The code matches the spec:
   `TaskMonitor._ensure_heartbeat_registered` is at
   weft/core/monitor/task_monitor.py:5714, and `next_wait_timeout` is at :4949. So
   TaskMonitor has two clocks, which [ALT-WT-004] and "One reactor, one arbiter"
   refuse. The spec lags the theory.
4. **Family-scheduler rebuild. Confirmed with a narrowed scope, drifted
   (plan 5).** `FamilyScheduler.retire` (weft/core/family_scheduling.py:386) does
   not touch pending tokens. It melds the exited parent's `_PendingIndex` into the
   grandparent in O(log n) through a meldable heap with union-find `owner()`, so
   [ALT-WT-016]'s refusal of O(N) token moves holds. But it rebuilds *every*
   frontier stack: it filters all stacks, sorts them, and runs the O(S²·L) prefix
   check. `recover(..., defer=False)` (called from weft/core/manager.py:2301 and
   :2388 on snapshot handoff and reconciliation) calls `finish_recovery`, which
   rebuilds every resident's path. So the in-memory pointer update that
   [ALT-WT-016] "Current consequence" calls $O(1)$ is a full rebuild over live
   residents. The cost scales with live frontier size, not backlog size. The theory
   claim is more precise than the code.
5. **Lossy TID short form. Confirmed, drifted (plan 12), if candidate I-1 is
   admitted.** `tid_short_form` folds to `(grain + counter·2_441_406) mod 10¹⁰`
   (weft/helpers/__init__.py:146). `TASKSPEC_TID_SHORT_LENGTH = 10`
   (weft/_constants.py:84). Spec 07 OBS.5 line 343 sanctions the loss: "The form is
   not unique". `weft/liveness/host.py:50-56` confirms a live process title by
   matching that non-unique short form, so a title match is weaker evidence than
   its reason code `identity_match_title_match` suggests (the `(pid,
   create_time)` match has already happened, so the impact is limited).
6. **Manager supersession drain. Confirmed one-way, theory revision (fork 1).**
   `_begin_superseded_shutdown` (weft/core/manager.py:4874) sets
   `_unregistered = True` and, with live user children, `_draining = True` with
   `_drain_leader_tid = None`. The only resume path,
   `_resume_leadership_after_drain_loss` (:8643), runs only when a
   `_drain_leader_tid` is set and has become invalid (:8676-8688). A superseded
   drain has none, so it never resumes. It is one-way for *automatic* proactive
   supersession too (spec 03 [MA-1.4] lines 776-790: a lower-TID manager may write
   a `superseded` row for a fresher higher-TID manager). The leadership-yield
   drain, by contrast, is reversible (tests/core/test_manager.py::test_manager_leadership_drain_resumes_when_leader_proof_disappears).
   Spec [MA-1.4] (lines 178-182) sanctions a self-owned `superseded` row as
   "shutdown authority". The tree does not show the drain is reversible, so the
   row stays `theory revision`.
7. **"~100 ms" SimpleBroker pass. A design default, not a measurement:
   aligned (altitude).** In installed SimpleBroker 8.7.2, the figure is
   `DEFAULT_MAX_INTERVAL: Final[float] = 0.1` (simplebroker/_constants.py:462).
   It is the ceiling of `PollingStrategy._calculate_base_delay`'s linear backoff
   after `DEFAULT_INITIAL_CHECKS = 100` zero-delay checks
   (simplebroker/watcher.py:1613-1627). On the SQLite polling path the backoff
   sleeps in 20 ms chunks and re-checks `PRAGMA data_version` between them
   (watcher.py:1458-1475), so a write during the backoff is seen within about one
   chunk, not 100 ms. With a native activity waiter (PostgreSQL LISTEN), the wait
   returns on notification. No measurement backs "~100 ms". It is a configured
   upper bound restated as a figure, which is the altitude-collapse case the plan
   names in task 10. The principle (quiet means zero turns) stays right.

### Audit table

Endorsed numbering (2026-11-02): candidate rows I-1 to I-10 map to the
endorsed [WT-2a] numbering as I-1→.1, I-2→.2, I-3→.3, I-4→.4, I-5→.5,
I-6→.6, I-7→.8, I-8→.11, I-9→.9, I-10→.10 (each row's claim checked against
the endorsed invariant before writing this map; I-5 and I-6 carry the
pre-restatement wording, "spawn isolation" and "one reactor per task", which
the restated .5 and .6 lift one notch). Rows I-11 to I-14 were added for the
endorsed invariants with no candidate row: I-11→.7, I-12→.12, I-13→.13,
I-14→.14.

Classification key: `aligned`; `aligned (altitude)` (the sentence is right but
carries a figure, path, or private name; the Slice B agent lifts it); `drifted`
(the theory is right and the code or a spec lags; the plan number closes it);
`theory revision` (an unresolved identity question; §9 fork). A trailing
`(unsettled)` means the tree did not settle the classification. Those rows are
listed under "Rows that need the owner".

| Identifier | Claim (one clause) | Contract (spec code) | Evidence in tree | Classification | Plan / fork |
|---|---|---|---|---|---|
| I-1 → [WT-2a.1] | Every unit of work has one hybrid-timestamp TID and every rendering of it is lossless | IMMUT.3; OBS.5; [SB-0.2] | `tests/specs/manager_architecture/test_tid_correlation.py::test_spawn_request_timestamp_matches_tid` (TID = spawn-request ID holds); `weft/helpers/__init__.py::tid_short_form` (mod 10¹⁰ fold); `docs/specifications/07-System_Invariants.md` OBS.5 "The form is not unique"; `weft/liveness/host.py::inspect_host_process` matches titles by short form; `tests/system/test_short_tid.py::test_short_form_folds_counter_and_wraps` | drifted (invariant new on 2026-11-02; drift created by the decision, not discovered) | 12 TID display encoding: retire [OBS.5], the ten-digit short form, its tests, the candidate-discovery and ambiguity code, and the liveness title regex |
| I-2 → [WT-2a.2] | Queues are the only durable truth; process memory holds hints | QUEUE.1–QUEUE.6 (queue structure only); OBS.1; OBS.2 (no separate lifecycle store); OBS.6 | `weft/core/manager.py` bootstrap recovery rebuilds `FamilyScheduler` from `weft.state.tasks.*` evidence (lines 2615–2629); `tests/commands/test_retained_result_history.py::test_task_snapshot_preserves_custom_result_after_raw_history_retirement` (forbids `MonitorStore` reads) | aligned (OBS.2 states the invariant; settled in round-1 review, §8 item 5) | — |
| I-3 → [WT-2a.3] | Lifecycle is forward-only | STATE.1; STATE.2 | `tests/specs/taskspec/test_state_transitions.py::test_terminal_state_is_immutable`; `tests/core/test_state_machines.py::test_terminal_state_with_outgoing_transition_fails_by_default` | aligned | — |
| I-4 → [WT-2a.4] | `spec` and `io` are frozen after creation | IMMUT.1; IMMUT.2 | `tests/taskspec/test_taskspec.py::TestPartialImmutability::test_frozen_nested_sections_reject_assignment` | aligned | — |
| I-5 → [WT-2a.5] | Weft runtime processes use spawn isolation, never fork | IMPL.5; IMPL.6 | `weft/core/launcher.py` line 202 `multiprocessing.get_context("spawn")`; `weft/core/runners/host.py` line 465 (code only; no test asserting the spawn context was found by grep) | aligned (IMPL.5/IMPL.6 state the contract; the missing start-method test is an evidence-strength note; settled in round-1 review, §8 item 5) | — |
| I-6 → [WT-2a.6] | One reactor and one drive-owning thread per task | IMPL.8; IMPL.10 | `tests/tasks/test_task_execution.py::test_base_task_process_once_rejects_a_second_drive_thread_before_policy`; `tests/specs/test_reactor_architecture.py::test_retired_task_reactor_caps_do_not_return` | aligned (timer-floor drift is carried by rows 4.3-a and 4.3-c) | — |
| I-7 → [WT-2a.8] | SimpleBroker owns queue mechanics; Weft never reimplements them | [SB-0.1]; [SB-0.4]; [WT-5] | `weft/core/queue_wait.py::QueueChangeMonitor` uses `simplebroker.create_activity_waiter_for_queues`; `weft/core/runtime_identity.py` binds connection owner through `simplebroker_pg` | aligned | — |
| I-8 → [WT-2a.11] | One operator trust domain; the OS is the boundary | [WT-5]; `00-Overview_and_Architecture.md` "Observability and Security" | `weft/commands/submission.py` line 975–976 rejects `shell=True`; `CLAUDE.md` §4.11 | aligned (admitted on owner authority 2026-11-02 as [WT-2a.10]) | — |
| I-9 → [WT-2a.9] | No compatibility code for changed contracts | [ALT-WT-012]; `00-Overview_and_Architecture.md` "Runtime Layers" (Single current contract) | `weft/core/service_convergence.py::discard_v1_service_registry_rows` (bounded forward migration); `weft/commands/_spawn_submission.py::LegacyChildMigration` (explicit operator cohort, [MF-6.1]) | aligned | — |
| I-10 → [WT-2a.10] | Weft is a Unix citizen: argv not shell, stdin is data, exit codes are the status API | [CLI-1.1.1] | `weft/commands/submission.py` line 975–976; `tests/cli/test_cli_run.py::test_cli_run_wait_returns_timeout_exit_code`; `tests/cli/test_cli_result.py::test_result_stream_preserves_real_task_failure_exit_status` | aligned | — |
| I-11 ([WT-2a.7]) | A work item executes exactly once per successful reservation path; an unsettled reserved row persists independently of the process that took it until it is acknowledged or explicitly dispositioned; its disposition when execution does not complete is the declared keep or clear policy; crash residue awaits explicit recovery | EXEC.1; QUEUE.4–QUEUE.6; [MF-2] (crash residue) | `tests/tasks/test_task_execution.py::test_reserved_policy_keep_on_error`; `tests/tasks/test_task_execution.py::test_reserved_policy_clear_on_error`; `tests/tasks/test_task_execution.py::test_reserved_policy_keep_on_stop` (declared policy applied at the terminal transition; no test restarts a process with a reservation in flight, an evidence-strength note; claim cell synchronized to [EXEC.1]'s "reservation path" wording at round 6 F14, no new test needed) | aligned | — |
| I-12 ([WT-2a.12]) | Everything that exchanges facts with a task is a task with its own `ctrl_in`; protocols are ordinary messages pushed to a watched queue | [MF-3] (spec 05 line 213 keeps non-PONG replies on `ctrl_out`) | `tests/core/test_manager_runtime_connections.py::test_borrowed_manager_probe_routes_reply_and_retires_requester_queue`; `tests/tasks/test_pipeline_runtime.py::test_pipeline_task_routes_pong_to_requester_control_queue` (PING/PONG holds); `weft/commands/run.py::_matching_stop_ack` (STOP ack pull-scan) | drifted for requester identity (STOP/KILL requesters mint no task TID), reply ownership (replies land on the target's queues, not the requester's `ctrl_in`), and push routing (acks are pull-scanned); see rows 4.1-a and 4.1-b; cleanup plan 3 | 3 control-reply routing |
| I-13 ([WT-2a.13]) | Manager, tasks, and clients have independent lifetimes; absence is never proof of death | [MA-1.6a]; [MA-3]; [MF-5]; LIVENESS.R3–R7; [PY-2] | `tests/core/test_manager.py::test_manager_superseded_self_record_drains_children_without_stopping`; `tests/tasks/test_liveness_monitor.py::test_probe_worker_reports_foreign_host_as_attempted_unknown`; `tests/core/test_monitor_store.py::test_monitor_store_task_owned_control_and_log_supersede_manager`; counter-evidence for the broad sentence: `tests/core/test_manager.py::test_manager_stop_command_drains_nonpersistent_children` (an explicit STOP ends a running non-persistent child: `assert not _process_running(child_info.process.pid)`), per [MA-3] (spec 03 near line 801: STOP, SIGTERM, and SIGINT drains broadcast STOP, then escalate to SIGTERM and reap) | aligned for the implicit-lifecycle sentence; drifted for the system-STOP ordering sentence (STOP broadcasts to tasks and internal services together; [MA-3] orders neither) — owner rule 2026-11-02 | 4 system STOP ordering (re-scoped, §9 item 5); probe 2026-11-02 adds a coverage question: a STOP signals only this Manager's child table, not detached tasks from an earlier Manager |
| I-14 ([WT-2a.14]) | Every submission is accepted into the spawn-request queue without waiting for launch admission; only a running process consumes capacity; readiness work after acceptance is separate | [MF-1] | `tests/core/test_connection_admission.py::test_launch_uses_measured_capacity_without_task_budget`; `tests/core/test_spawn_requests.py::test_runtime_origin_keeps_same_context_child_on_primary_with_lineage` (these establish routing and admission policy, not a nonblocking call return; the call itself observes availability and may start a Manager, `weft/commands/submission.py` lines 451 and 616) | aligned (no test asserts acceptance while admission is closed) | — |
| 4.1-a Everything is a task | Even a short-lived CLI requester mints a TID and receives replies on its own `ctrl_in` | [MF-3] | `tests/core/test_control_probe.py::test_send_keyed_ping_probe_routes_pong_to_ephemeral_ctrl_in` (PING honors it); `weft/commands/run.py::_stop_interactive_run_session` and `weft/commands/tasks.py::_await_control_surface` (STOP/KILL requesters mint no TID, no `reply_to`) | drifted | 3 control-reply routing |
| 4.1-b It is just a message | Replies are pushed to a queue the requester already watches; push, never pull-scan | [MF-3] (lines 213, 317, 334 sanction the scan) | `weft/commands/run.py::_matching_stop_ack`; `weft/commands/tasks.py::_observe_control_envelopes`; `tests/commands/test_active_interactive_lifecycle.py::test_active_stop_waits_named_budget_and_accepts_only_own_ack`; `tests/tasks/test_control_channel.py::test_stop_kill_command_sends_keyed_ack` | drifted (spec lags theory) | 3 control-reply routing |
| 4.1-c Queues carry facts; processes carry hints | No in-memory lifecycle database; Manager tables are hints rebuilt from queues | OBS.1; OBS.6; [MF-5] | `weft/core/manager.py` lines 2615–2629 (scheduler rebuilt from `weft.state.tasks.*`); `weft/core/family_scheduling.py::FamilyScheduler` ("No method performs broker I/O") | aligned (altitude: row names `weft.state.tasks.<tid>` queue; acceptable as technical name) | — |
| 4.1-d Task-internal state is private | Weft paths never read a task's internal state | [OBS.13]; [SB-0.4a] | `tests/commands/test_retained_result_history.py::test_task_snapshot_preserves_custom_result_after_raw_history_retirement` | aligned (altitude: names `TaskMonitor`, `broker.sidecar()`) | — |
| 4.1-e Consume-to-disappear queues | Pending tokens are consumed on dispatch; no tombstones or GC sweeps | [SB-0.1]; [MF-5] | `tests/core/test_manager_family_maintenance.py::test_consumed_primary_row_cannot_keep_a_cached_spawn_candidate`; `tests/core/test_family_scheduling.py::test_parent_exit_contracts_pending_bucket_and_exact_removal_survives_merge` | aligned | — |
| 4.1-f Address a fact to its observers | Completion is broadcast atomically to the task log and `ctrl_out` | [MF-3]; [MF-5]; OBS.1 | `tests/tasks/test_local_completion_publication.py::test_completion_is_the_same_retained_fact_at_both_addresses` | aligned | — |
| 4.1-g Keep an identity you already have | A caller derives queue names from the TID it created rather than rediscovering it | [MF-5]; [SB-0.2] | `weft/commands/run.py::_stop_interactive_run_session` derives config from `_accepted_taskspec` (falls back to lookup only when absent); `tests/specs/manager_architecture/test_tid_correlation.py::test_spawn_request_timestamp_matches_tid` | aligned | — |
| 4.2-a Classical CS models over blunt ad-hoc fixes | Realize the textbook model (DFS/work-sharing, measured admission, custody) with existing primitives | [MA-1.8] | `weft/core/family_scheduling.py::FamilyScheduler.retire` and `::finish_recovery` (full frontier rebuild where [ALT-WT-016] claims an $O(1)$ pointer update); `tests/core/test_family_dispatch_scaling.py::test_completion_to_next_real_launch_does_not_scan_sibling_backlog` (dispatch side holds) | drifted (retire/recovery path only) | 5 family-scheduler incremental retire |
| 4.2-b Queued work costs nothing; live processes cost | Admission gates process creation on measured pressure, never on queued work | [MF-1]; [MA-1.8] | `tests/core/test_connection_admission.py::test_launch_uses_measured_capacity_without_task_budget`; `tests/core/test_connection_admission.py::test_removed_connection_budget_is_an_actionable_configuration_error` | aligned | — |
| 4.2-c Use the primitives you have | Compose labels, queues, moves, and snapshots before new infrastructure | [SB-0.1]; [SB-0.4] | `weft/core/queue_wait.py::QueueChangeMonitor` (built on SimpleBroker activity waiters) | aligned (judgment; no counter-evidence found) | — |
| 4.2-d Push a concern down when it belongs lower | Connection attribution lives in SimpleBroker; policy stays in Weft | [SB-0.4] | `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4] (`simplebroker_pg.bind_connection_owner`, line 332); `weft/core/runtime_identity.py` | aligned | — |
| 4.2-e Process concurrency, short transactions | Work is isolated in spawn-context OS processes | [SB-0.4] | `weft/core/launcher.py` line 202 (`get_context("spawn")`) | aligned | — |
| 4.2-f One owner for each resource | Each session, lease, deleter, and worker result has one owner | [SB-0.4]; [CC-2.2.1]; QUEUE.7; QUEUE.8; LIVENESS.R3 | `weft/core/tasks/liveness_monitor.py`; `tests/tasks/test_liveness_monitor.py::test_unknown_timeout_deletes_only_exact_mapping_after_completed_probe` | aligned (sole-deleter exclusivity not exhaustively proven by grep) | — |
| 4.3-a One reactor, one arbiter, a closed set of inputs | Every wait is at one arbiter; never defensive polling caps | [CC-2.2.1]; IMPL.8–IMPL.11; [MF-3.2] (two-clock sentence, line 496) | `weft/core/tasks/service.py::ServiceWorkerContext.iter_items` and the stop-drain loop (50 ms caps, lines 70, 488); `weft/commands/tasks.py::_await_control_surface` (`CONTROL_SURFACE_WAIT_INTERVAL` cap, line 1975); `weft/core/monitor/task_monitor.py::TaskMonitor._ensure_heartbeat_registered` (heartbeat clock beside `next_wait_timeout`); `tests/tasks/test_task_monitor.py::test_task_monitor_heartbeat_failure_records_health_but_still_cycles`; counter-site aligned: `weft/core/launcher.py::_start_parent_loss_watcher` | drifted | 2 reactor edges and timer floors; 6 heartbeat clock consolidation |
| 4.3-b A quiet task does nothing | A quiet reactor runs zero turns; first wake after quiet is one broker pass | [CC-2.2.1] | `.venv/lib/python3.14/site-packages/simplebroker/_constants.py` line 462 (`DEFAULT_MAX_INTERVAL = 0.1`); `.venv/lib/python3.14/site-packages/simplebroker/watcher.py::PollingStrategy` (20 ms data_version chunks; native waiter returns on notify) | aligned (altitude: "~100 ms" is SimpleBroker's configured backoff ceiling, not a measurement) | — |
| 4.3-c Subprocess and stream supervision is readiness-driven | Runner supervision waits on readiness; the timeout is only a ceiling | [RM-5.2]; [CC-2.5]; [CC-3.4] | `weft/core/runners/subprocess_runner.py` (adapter holds); `tests/core/test_subprocess_runner.py::test_root_exit_with_inherited_pipe_and_no_deadlines_completes_within_drain_budget`; drift: `weft/core/tasks/interactive.py::InteractiveTaskMixin` methods `_interactive_drain_remaining` (10 ms sleep, line 374) and `_interactive_shutdown` (50 ms sleep, line 530); `weft/core/tasks/sessions.py::AgentSession._terminal_handoff_wait_seconds` (50 ms ceiling, line 734) | drifted (interactive and agent-session paths; the runner path is aligned) | 2 reactor edges and timer floors |
| 4.3-d Bounded per-turn stream work and backpressure | Bounded chunk budget per turn; OS pipe backpressure over unbounded buffering | [RM-5.2]; [CC-2.5] | `tests/core/test_subprocess_runner.py::test_input_backpressure_cannot_block_supervision`; `tests/core/test_subprocess_runner.py::test_stream_queue_drain_yields_with_continuing_production` | aligned | — |
| 4.3-e Availability and death are different questions | Missing evidence is `unknown`, never proof of death | [MA-1.6a]; [MA-3]; [MF-5]; LIVENESS.R3–R7 | `tests/liveness/test_analysis.py::test_invalid_or_missing_handle_fails_toward_unknown_unless_terminal`; `tests/commands/test_result_settlement_policy.py::test_wrapper_lost_holds_only_original_grace_and_output_cannot_override_it` | aligned | — |
| 4.3-f Ask who owns this lifetime | Supersession changes who takes future work; it never kills or moves running children | [MA-1]; [MA-1.4]; [MA-3]; [PY-2] | `tests/core/test_manager.py::test_manager_superseded_self_record_drains_children_without_stopping` (children clause holds) | aligned (the one-way drain is carried by the `[ALT-WT-007]` row) | — |
| 4.4-a As few concepts as possible | Consolidate onto existing concepts; no second lane, store, bus, or daemon | `00-Overview_and_Architecture.md` "Product Boundary" | `tests/core/test_connection_admission.py::test_retired_frontier_setting_is_an_actionable_configuration_error` (retired family cap stays retired) | aligned (judgment) | — |
| 4.4-b Shared function, shared code | Two code paths for one responsibility is a defect | Engineering principles 1 and 4.2 (`docs/agent-context/engineering-principles.md`) | `tests/specs/test_reactor_architecture.py::test_task_ping_responder_is_owned_by_base_task`; `tests/architecture/test_import_boundaries.py::test_cli_verb_names_are_a_bijection_with_command_exports` | aligned | — |
| 4.4-c The expert Python developer test | Designs look clean and idiomatic to an expert Python engineer | `docs/plans/2026-09-22-task-monitor-maintenance-worker-plan.md` (plan, not spec) | `weft/core/monitor/task_monitor.py::MaintenanceWorker` (holds); counter-evidence: `weft/commands/run.py` lines 438–524, three function-level imports of private names (`_task_control_result`, `_terminal_observation_snapshot`, `_require_observation_config`) from `weft/commands/tasks.py` to dodge a tasks→system→run cycle, outside `weft/core/deferred.py` | aligned (unsettled) | 8 Python craft and deferred imports, if the owner rules drift |
| 4.4-d No over-armoring | Engineer for real failure boundaries; best-effort paths try, catch, and log | Engineering principles, Secondary Rules (`docs/agent-context/engineering-principles.md` line 247) | `weft/core/queue_wait.py::QueueChangeMonitor._close_waiter` (catch, log, continue) | aligned (judgment; no sweep done) | — |
| 4.4-e Measure; do not bake in inefficiency | Performance claims need measurements on SQLite and PostgreSQL | `docs/plans/2026-10-12-burst-submission-complexity-plan.md` (plan) | `tests/core/test_family_dispatch_scaling.py::test_completion_to_next_real_launch_does_not_scan_sibling_backlog` | aligned | — |
| 4.4-f Tests prove correctness, nothing else | Tests verify behavior; never pin versions, CI order, or reduced concurrency | [TS-0] (`08-Testing_Strategy.md`) | `pyproject.toml` line 140 (`-n logical --maxprocesses 12`); counter-candidates: `tests/system/test_release_script.py::test_resolve_target_version_requires_new_version_after_publication`, `tests/system/test_release_workflow.py::test_github_release_uploads_only_python_distributions` | aligned (unsettled: whether release-script tests are "release script detail" per [ALT-WT-018]) | 9 test-suite structure, if the owner rules drift |
| 4.4-g No capability diminution | Simplifications preserve capabilities and latency; keep a difference register | Hardening runbook (`docs/agent-context/runbooks/hardening-plans.md` line 83) | `docs/agent-context/runbooks/hardening-plans.md` (difference-register rule) | aligned (process rule; no code counterpart) | — |
| 4.4-h No compatibility code | One coordinated release; bounded forward migration only | `00-Overview_and_Architecture.md` "Runtime Layers" (Single current contract) | `weft/core/service_convergence.py::discard_v1_service_registry_rows`; `tests/core/test_connection_admission.py::test_removed_connection_budget_is_an_actionable_configuration_error` | aligned | — |
| 4.4-i One path on all platforms | Shared lifecycle architecture; platform branches are narrow leaves | `docs/specifications/06-Resource_Management.md` [RM-5.2] | `weft/core/runners/subprocess_runner.py` (POSIX/Windows adapter leaves); `weft/_constants.py` line 594 `MANAGER_CHILD_SENTINEL_WINDOWS_WAIT_SLICE_SECONDS` (named 63-handle limit) | aligned | — |
| 4.4-j The client mirrors the CLI | The client and the CLI expose the same verbs over shared `weft/commands/` capabilities | [PY-1]; [PY-2]; `11-CLI_Architecture_Crosswalk.md` | `tests/core/test_client.py::test_client_api_parity_guard_matches_current_public_matrix` (system namespace pins `builtins, dump, load, status, tidy`); `weft/commands/__init__.py` exports `cmd_system_prune`, `cmd_system_task_monitor` with no `WeftClient` counterpart in `weft/client/_namespaces.py::SystemNamespace` | drifted | 11 CLI and client surface parity |
| 4.4-k Specs are what is; theory is where we go | A flawed spec is revised through a class-5 plan | `docs/agent-context/decision-hierarchy.md` | `docs/agent-context/decision-hierarchy.md` (lines 34, 120, 178 route theory conflicts) | aligned | — |
| 4.4-l Observable with standard tools | Process titles and queue peeks are the operator interface; no Weft-only daemon, index, or query service is needed to inspect a running Weft | [WT-5] (no second control plane); [OBS.4] | `tests/tasks/test_task_observability.py::test_process_titles_update`; `tests/cli/test_cli_queue.py::test_queue_peek_preserves_message`; `weft status` and `weft task list` read queues only ([WT-2a.2]) | aligned (added 2026-11-02 with the owner-endorsement row; falsifier lands in [WT-7] at task 13) | — |
| [ALT-WT-007] A one-way drain on supersession | Automatic supersession keeps one reversible convergence policy; explicit operator replacement stays distinct and binding | [MA-1.4] (lines 178–182 and 776–790 sanction a one-way drain); [MA-3] | `weft/core/manager.py::Manager._begin_superseded_shutdown` (one-way intake drain; the pre-revision [ALT-WT-007] refused it for automatic convergence) | theory revision (decided 2026-11-02: entry restated, no code change, plan 4 withdrawn) | fork 1, resolved |

#### Row-count verification

```
$ grep -cE '^\| (I-[0-9]+|4\.[1-4]-[a-k]) ' audit-table.md
40
```

Rows: 10 invariant candidates + 30 principles = 40. Matches the expected count.

Recount after the round-1 F6 disposition (new `[ALT-WT-007]` row), over this
plan file:

```
$ grep -cE '^\| (I-[0-9]+|4\.[1-4]-[a-k]|\[ALT-WT-[0-9]+\]) ' docs/plans/2026-11-02-program-theory-reconciliation-plan.md
41
```

Rows: 10 invariant candidates + 30 principles + 1 [WT-6] anchor row
(`[ALT-WT-007]`) = 41. Task 14's gate compares identifier sets, not this
count.

#### Evidence-path existence check (all rows)

```
$ grep -E '^\| (I-[0-9]+|4\.[1-4]-[a-k]) ' audit-table.md | awk -F'|' '{print $5}' | grep -oE '`[^`]+`' | tr -d '`' | sed 's/::.*//' | grep -E '^(weft|tests|docs|\.venv|pyproject|CLAUDE)' | sed 's/ .*//' | sort -u > paths.txt
$ while read p; do [ -e "$p" ] && echo "OK $p" || echo "MISSING $p"; done < paths.txt
OK for all 59 file paths (weft/, tests/, docs/, .venv simplebroker, pyproject.toml, CLAUDE.md).
One "MISSING weft.state.tasks.*" is a queue name the extractor caught, not a file path.

$ (symbol check: for each `path::symbol`, grep -E "(def|class) <last segment>" path)
OK for all 49 module-level symbols and test functions. The six Class.method references
(FamilyScheduler.retire, Manager._begin_superseded_shutdown,
TaskMonitor._ensure_heartbeat_registered, QueueChangeMonitor._close_waiter,
ServiceWorkerContext.iter_items, AgentSession._terminal_handoff_wait_seconds) were
verified separately:
weft/core/family_scheduling.py:386:    def retire(self, tid: str) -> None:
weft/core/manager.py:554:class Manager(ServiceTask):   ...:4874:    def _begin_superseded_shutdown(self) -> None:
weft/core/monitor/task_monitor.py:4461:class TaskMonitor(ServiceTask):   ...:5714:    def _ensure_heartbeat_registered(self) -> None:
weft/core/queue_wait.py:185:    def _close_waiter(waiter: Any) -> None:
weft/core/tasks/service.py:46:class ServiceWorkerContext:   ...:64:    def iter_items(self) -> Iterator[Any]:
weft/core/tasks/sessions.py:219:class AgentSession:   ...:725:    def _terminal_handoff_wait_seconds(
```

### Summary counts

Recounted 2026-11-02 after the round 6 dispositions (F13 to F19; rows I-11
to I-14 and 4.4-l present), with the existing command, over this plan file:

```
$ grep -E '^\| (I-[0-9]+|4\.[1-4]-[a-l]|\[ALT-WT-[0-9]+\]) ' docs/plans/2026-11-02-program-theory-reconciliation-plan.md | awk -F'|' '{c=$6; gsub(/^ +| +$/,"",c); if (c ~ /^theory revision/) k="theory revision"; else if (c ~ /^drifted/) k="drifted"; else if (c ~ /^aligned/) k="aligned"; else k="OTHER"; n[k]++; if (c ~ /unsettled/) u++; if (c ~ /altitude/) a++} END {for (k in n) print k": "n[k]; print "of which flagged (unsettled): "u; print "of which aligned (altitude): "a}' | sort
OTHER: 5
aligned: 37
drifted: 8
of which aligned (altitude): 3
of which flagged (unsettled): 2
theory revision: 1
```

The five `OTHER` lines are not audit rows: they are the §4a Deviation Log
row keyed `[ALT-WT-021]` and the four `[ALT-WT-*]` rows of the altitude
extension tally, which the command's identifier pattern also matches
(`grep -c` of the same pattern prints 51 = 46 audit rows + 5). The new §4a
row for F19 is keyed `[WT-2a.13]`, which the pattern does not match.

- **aligned: 37.** Three of these are `aligned (altitude)` (4.1-c, 4.1-d, 4.3-b), and
  two are flagged `(unsettled)` (4.4-c, 4.4-f). New: I-11, I-13, I-14, 4.4-l.
  I-13 counts here because its classification cell opens "aligned" (the
  implicit-lifecycle sentence); the same row is drifted on the system-STOP
  ordering sentence the owner decided on 2026-11-02 (§9 item 5), and that
  half is assigned to cleanup plan 4 (STOP ordering).
- **drifted: 8** by the counting command, plus I-13's ordering half. I-1 →
  plan 12; I-12, 4.1-a, 4.1-b → plan 3; 4.2-a → plan 5; 4.3-a → plans 2
  and 6; 4.3-c → plan 2; 4.4-j → plan 11; I-13 (ordering) → plan 4.
- **theory revision: 1.** `[ALT-WT-007]` → fork 1.
- Total 46 = 14 invariant rows (I-1 to I-14) + 31 principles (30 at baseline
  plus 4.4-l, "Observable with standard tools", whose theory row lands at
  task 13 from the appendix "Owner endorsement additions") + 1 [WT-6] anchor
  row.

Cleanup-series coverage: plans 2, 3, 5, 6, 11, 12 each have at least one drift row.
Plan 4 is re-scoped to system STOP ordering (§9 item 5; row I-13); its supersession scope was withdrawn when fork 1 resolved as a theory revision on 2026-11-02. Plans 8 and 9 have only unsettled
candidate rows (4.4-c, 4.4-f). Plans 7 and 10 have no row in this table: concept
ownership is a [WT-3] coverage question for Slice B task 10, and the one broker-boundary
observation (item 6 below) is unverified. Under the plan's rule that no cleanup plan
starts without a drift row, plan 4 may start on row I-13's ordering half, and plans 7, 8,
9, and 10 cannot start on this table as it stands.

### Rows that need the owner

1. **[ALT-WT-007]: resolved by the owner on 2026-11-02 (§9 fork 1).** The
   one-way drain is intended; the entry is restated in the appendix [WT-6]
   section and recorded in `[REV-WT-004]`; the spec clauses are not drift;
   cleanup plan 4 is withdrawn. Row 4.3-f stays aligned.
2. **I-2 and I-5: settled, not owner rows.** The round-1 review settled both
   from the tree ([OBS.2] states the no-second-lifecycle-store rule; [IMPL.5]
   and [IMPL.6] state the spawn contract, with spawn contexts at
   `weft/core/launcher.py:202` and `weft/core/runners/host.py:465`). See §8
   item 5.
3. **I-1 (lossless TID rendering): conditional.** The row is `drifted` (plan 12)
   only if the owner admits "every rendering is lossless" into [WT-2a]. Spec OBS.5
   currently declares the short form non-unique by design. If the owner does not
   admit the lossless clause, the row is aligned and plan 12 has no drift row to
   cite.
4. **4.4-c Expert Python test: unsettled.** Three late imports of private
   functions across `weft/commands/` modules carry the required cycle comment, so
   they are legal under `CLAUDE.md` §4.2. Whether a cross-module private-name cycle
   fails the expert-developer test is a judgment the tree cannot make. If the owner
   rules it drift, it goes to plan 8.
5. **4.4-f Tests prove correctness: unsettled.** `tests/system/test_release_script.py`
   and `tests/system/test_release_workflow.py` test the release tooling. Whether
   that counts as "release script detail" under [ALT-WT-018], or as correctness
   of shipped code under `bin/`, needs the owner. If it is drift, it goes to plan 9.
6. **4.3-b A quiet task does nothing: one open observation, not a
   classification.** With a native activity waiter, SimpleBroker's
   `PollingStrategy` returns from `wait_for_activity` every
   `max(1.0, 10 × max_interval)` s through its native idle poll
   (simplebroker/watcher.py:1386, :1447-1449). I did not establish from the tree
   whether that return makes a Weft `MultiQueueWatcher` run a turn on a quiet
   PostgreSQL task. Missing: a trace or test on PostgreSQL. If it does, "quiet
   runs zero turns" is drifted on PostgreSQL (plan 2 or plan 10).
7. **4.3-a launcher parent-loss watcher: aligned by rule, flagged.** The absence is
   named ("no portable notification"), so the site is aligned. Platform-specific
   parent-death notifications exist (Linux `PR_SET_PDEATHSIG`/pidfd, macOS kqueue
   `NOTE_EXIT`). Whether "no portable notification" justifies a timer under "one
   path on all platforms with narrow adapter leaves" is the owner's call.
8. **[WT-2a.1] wording for requesters (owner decision, §9 item 4).**
   [WT-2a.1]'s phrase "minted as its spawn-request message ID" does not fit
   CLI requesters, which mint a TID from the same generator without writing
   a spawn request. Proposed one-clause fix: "minted from the broker's
   timestamp generator, as the spawn-request message ID for spawned work",
   to accept with candidate [WT-2a.11] or independently. The four candidate
   invariants [WT-2a.11] to [WT-2a.14] are §9 item 4. [Resolved by the
   owner, 2026-11-02: the endorsed [WT-2a.1] adopts the clause; §9 item 4.]
