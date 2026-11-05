# Program Theory Revision: Published Refusals, Wake Predicates, Observable Quiet, and Behaviour Probes

Status: promoted
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]
Superseded by: none

Class: 5+P. A theory revision is class 5 by the header of
`docs/program-theory.md` ("revised only through a class-5 plan"). It is +P
because one change adds a verification measure that alters how each release
is verified. Hardening: N/A — no risky trigger. The plan edits documents
only; no code, queue name, CLI shape, or persisted format changes. Plan
type: theory-authoring with one clarification-only spec sentence (promotion
strategy D). One-way doors: none; every edit is a text revert.

This plan lands before the Manager and output repair plan that follows the
2026-11-05 deep-dive review. The owner set that order on 2026-11-05: "I
agree with the theory changes, and those should be made first." The repair
plan then cites the new theory sentences as its acceptance tests ([WT-0]:
plans move specs and code toward the theory).

## 1. Goal

Add to `docs/program-theory.md` the four statements whose absence let the
2026-11-05 review's defects conform to the written theory, and remove one
passage that sits below the theory's altitude:

- A. A new identity invariant: a standing refusal to dispatch is a published
  fact, it is scoped to the record that causes it, and it names what ends it.
- B. A new principle: a wake predicate reads facts and guarantees progress.
- C. A restated principle: quiet is defined by what an operator can observe.
- D. A fifth verification measure, the behaviour probe, and its falsifier.
- F. Implementation detail moved out of [ALT-WT-016].

Proposal E from the same discussion (promote inspectability to an invariant)
is not adopted. The owner keeps it a design goal.

## 2. Source Documents

- `docs/program-theory.md` [WT-0] (direction rules), [WT-2a], [WT-4.1],
  [WT-4.3], [WT-6] [ALT-WT-016], [WT-7], [WT-9]. This plan revises them.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8]. One
  clarification sentence is added (change F). The behaviour sentences of
  [MA-1.8] are not edited here; see section 4.
- `skills/crystallize-program-theory/SKILL.md` steps 6 to 8 (decision
  records, confirmation, identifier checks).
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.
- Evidence: the 2026-11-05 deep-dive review at commit `44d7f900`. Its
  measurements are restated in section 6 so this plan stands alone.

## 3. Context and Key Files

Files to modify:

- `docs/program-theory.md`
- `docs/specifications/03-Manager_Architecture.md` (one sentence and one
  `## Related Plans` bullet)
- `docs/plans/README.md` (index row; status flips)
- `docs/agent-context/context.index.yaml` only if it carries a timestamp or
  description of the theory that this revision makes stale (Task 6)

Read first:

- `docs/program-theory.md` in full. The header's Verification paragraph
  (lines 10 to 27), the [WT-2a] list (ends at [WT-2a.14]), the [WT-4.1] and
  [WT-4.3] tables, [ALT-WT-016], [WT-7], and [REV-WT-004] are the edit
  points.
- `weft/core/family_scheduling.py` `_compact` and `_compact_up` (for change
  F's rule-versus-code check).

Current structure:

- The header names four verification measures. All four measure documents
  or reasoning. None measures a running system.
- [WT-4.3] row "A quiet task does nothing" is stated in turns and wakes. A
  Manager that wakes promptly and discards the wake satisfies its wording.
- [WT-2a] states safety invariants (exactly once, forward only, death needs
  proof). It has no statement about a Manager that declines to dispatch.
- [ALT-WT-016] "Current consequence" carries a compaction threshold and a
  lookup cost that belong to the specification.

## 4. Invariants and Constraints

- The fourteen existing invariants, every [ALT-WT-*] disposition, and the
  [WT-0] direction rules keep their text. The only edit inside an existing
  [ALT-WT-*] entry is the removal in change F, which changes no disposition.
- No specification behaviour sentence is edited. Adopting change A makes one
  [MA-1.8] rule drift from the theory ("It blocks further ordinary dispatch
  until a matching positive task-owned snapshot or durable scoped receipt
  resolves the ambiguity"). That sentence, and the code that implements it,
  stay as they are until the repair plan promotes its own delta. This plan
  records the drift in the audit table and does nothing else about it.
- The accepted wording is used verbatim (owner, 2026-11-05: "accepted as
  is"), with three additions the owner has not yet seen, each marked in the
  delta: the attribution "(Owner, 2026-11-05.)" on change A; the existing
  clause "the strategy, not Weft, owns the pass length and burst behavior
  ([CC-2.2.1])" carried forward into change C; and the Evidence-column
  attributions on B and C. A reviewer finding that proposes different
  wording is reported to the owner, not applied.
- No new tooling, gate, or script. The behaviour probes are procedures
  recorded in the audit table.
- Review gates: fresh-eyes self-review; one cross-family round before
  promotion (class 5+P); owner-keyed possession probe and audit-table
  refresh at completion ([WT-7]).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `44d7f900` — `docs/program-theory.md` and
  `docs/specifications/03-Manager_Architecture.md` at plan authoring time.
  Plan type: theory-authoring; this change revises the theory itself.
- Promotion baseline identifier: `44d7f900` plus the worktree edits to
  `docs/program-theory.md` and `docs/specifications/03-Manager_Architecture.md`
  made on 2026-11-05 under Tasks 2 and 3 (uncommitted at promotion).

## Proposed Theory Delta

Promotion strategy:

| File | Strategy | Sections touched |
|------|----------|------------------|
| `docs/program-theory.md` | D — authoring; text lands as active | header Verification; [WT-2a]; [WT-4.1]; [WT-4.3]; [ALT-WT-016]; [WT-7]; [WT-9] |
| `docs/specifications/03-Manager_Architecture.md` | D — clarification only; the code already does this | [MA-1.8], after the paragraph that ends "on each dispatch or exit."; `## Related Plans` |

### A. [WT-2a] — insert after the [WT-2a.14] bullet

Replacement (`docs/program-theory.md`):

```text
- **[WT-2a.15] A standing refusal is a fact, and it is scoped.** When a
  Manager declines to dispatch accepted work for longer than one maintenance
  pass, the refusal, its reason, and the records that cause it are published
  as a queue fact that an ordinary status read shows. A refusal caused by
  one record holds that record; it does not hold unrelated work unless a
  measured resource says it must. Every standing refusal names the evidence
  or the operator action that ends it. (Owner, 2026-11-05.)
```

### B. [WT-4.1] — insert one table row after the row "Keep an identity you already have"

Replacement (`docs/program-theory.md`):

```text
| A wake predicate reads facts and guarantees progress | A predicate that decides whether queued work grants a turn is answered from queue rows, never from memory that only a turn can update. It is true whenever actionable work is present, and true only when the granted turn can act on it, so it neither loses a wake nor spins. Local completions and deadlines remain arbiter inputs ([WT-2a.6]). | Owner, 2026-11-05; 2026-11-05 review: the Manager's inbox predicate read the in-memory scheduler and dropped every new request until a periodic turn | [CC-2.1]; [MA-1.8] |
```

### C. [WT-4.3] — replace the row that opens "| A quiet task does nothing |"

Current text (verbatim, one line):

> | A quiet task does nothing | When no event arrives and no deadline is due, a reactor sleeps without running turns. First wake after quiet is bounded by one pass of SimpleBroker's polling strategy, followed by burst mode; the strategy, not Weft, owns the pass length and burst behavior ([CC-2.2.1]). | Owner, 2026-09-17 | [CC-2.2.1] |

Replacement (`docs/program-theory.md`):

```text
| A quiet task costs nothing an operator can see | When no event arrives and no deadline is due, a reactor runs no turns and publishes no deadline that is already due. A task that is only supervising (waiting on a child, a session, or a timer in the future) is quiet in this sense. Idle and supervising cost is bounded by the strategy's own polling pass and is measured, not inferred. A new input is acted on within one strategy pass; the strategy, not Weft, owns the pass length and burst behavior ([CC-2.2.1]). | Owner, 2026-09-17; owner, 2026-11-05; 2026-11-05 review | [CC-2.2.1] |
```

Carried forward: no turns while quiet; one strategy pass as the bound; the
strategy owns pass length and burst behaviour. Added: no already-due
deadline; supervising counts as quiet; cost is measured; the bound applies
to acting on an input, not only to waking. Audited: the row "One reactor,
one arbiter, a closed set of inputs" and [ALT-WT-002] still read correctly
beside the new wording. [WT-2] item 4 ("a quiet task runs zero turns") is
unchanged and is implied by the new row.

### D1. Header — Verification paragraph

Replace "Verification: Four measures" with "Verification: Five measures",
and insert after the sentence that ends "document-versus-tree agreement, not
possession.":

Replacement (`docs/program-theory.md`):

```text
Behaviour probe: each [WT-4.3] row and each behavioural [WT-2a] invariant
names one black-box probe against a running system (for example: CPU
consumed while one task runs; delay from an idle Manager to launch; dispatch
after a Manager kill). The probes run at each release and their numbers go
in the audit table beside the row. A row with no probe is marked unmeasured,
not aligned.
```

### D2. [WT-7] — insert one bullet after the bullet "Event-driven against absence"

Replacement (`docs/program-theory.md`):

```text
- **Aligned by reading, false by running:** If a behavioural claim is marked
  aligned in the audit table and a thirty-second measurement contradicts it,
  the audit method failed, not only the code. Add the probe before repairing
  the code.
```

### F. [ALT-WT-016] "Current consequence" — remove implementation detail

Replace the span that opens "The live frontier contracts through the same
contracted-pointer idea:" and closes "proportional to the lineage it
publishes." with:

Replacement (`docs/program-theory.md`):

```text
  `.pending.<parent_tid>` directly as children are dispatched. The live
  frontier contracts through the same contracted-pointer idea at amortized
  logarithmic cost per exit, and only the Manager checkpoint write remains
  proportional to the lineage it publishes ([MA-1.8]).
```

The block's first line is the existing line that precedes the span; it is
shown so the block matches the file and is not inserted a second time.
Removed from the theory: the twice-the-registered-count compaction trigger,
the charge-to-exits clause, and the path-compression lookup cost. Disposition, reason, and reopening
condition are untouched.

Spec side, inserted as its own paragraph after the [MA-1.8] paragraph that
ends "graph or dictionary scan on each dispatch or exit.":

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
The frontier's contracted-pointer table and each pending bucket compact when their physical entries exceed twice their live entries.
```

Rule-versus-code check (done at authoring): `weft/core/family_scheduling.py`
line 162 (`self.physical_count > 2 * self.count`) and line 380
(`len(self._up) > 2 * len(self._live_children)`). The path-compression
phrase is not moved: no code text was found that states it, so it is dropped
from the theory and not asserted in the spec (Task 3 stop gate).

### G. [WT-9] — append the revision record

Replacement (`docs/program-theory.md`):

```text
### [REV-WT-005] Published refusals, fact-reading wake predicates, observable quiet, and behaviour probes

- Current account: [WT-2a.15] makes a standing refusal to dispatch a
  published, scoped fact that names what ends it. [WT-4.1] requires a wake
  predicate to read queue facts and to guarantee progress. [WT-4.3] defines
  quiet by what an operator can observe, covers supervising tasks, and
  bounds acting on a new input. The header and [WT-7] add the behaviour
  probe as a fifth verification measure. [ALT-WT-016] no longer carries
  compaction mechanics.
- Supersedes: a theory precise about safety and silent about a Manager that
  declines to dispatch; a quiet rule stated in turns and wakes; and four
  verification measures that all checked documents or reasoning.
- Pressure: the 2026-11-05 deep-dive review at `44d7f900`. A Manager used a
  full core for as long as any user task ran, and an idle Manager acted on a
  new request only at its next periodic turn; both had shipped since at
  least 0.9.105 and both rows were marked aligned by reading. One Manager
  kill in the launch window stopped all dispatch for the context with no
  published reason.
- Evidence:
  - contemporaneous: `docs/plans/2026-11-05-program-theory-behaviour-and-refusal-revision-plan.md`,
    section 6 (measurements and audit rows)
  - owner-recalled: owner session 2026-11-05 ("I agree with the theory
    changes, and those should be made first"; "No E, otherwise good and
    accepted as is")
```

### Spans audited and unchanged

- [REV-WT-004] says "[WT-2a] states fourteen identity invariants". It is a
  dated record of that revision and stays as written.
- [WT-4.4] row "Observable with standard tools" stays a design goal
  (proposal E not adopted).
- `AGENTS.md` and `CLAUDE.md` section 3 list a quick form of five
  invariants and point to [WT-2a] for the rest. [WT-2a.15] does not belong
  in that quick list; no edit.

## 5. Tasks

0. Preconditions.
   - Re-verify every anchor in the delta against the current tree: the
     [WT-2a.14] bullet, the two table rows, the header sentence, the
     [ALT-WT-016] span, the [WT-7] bullet, the [MA-1.8] paragraph end. Other
     sessions edit this tree; if an anchor moved, stop and re-derive it.
   - Done when: each anchor is quoted with its current line number in the
     Execution log.

1. Independent review (before any edit to the theory).
   - One Tier-1 cross-family round through `skills/call-agent/SKILL.md`,
     Planning Review Prompt, with this plan and `docs/program-theory.md`.
     Declare the accepted-as-is wording as an explicitly accepted risk the
     reviewer does not re-litigate; wording objections go to the owner as
     observations.
   - Record reviewer, command, findings, and dispositions in the Review Log.
   - Stop if: the reviewer finds that a new sentence contradicts an existing
     invariant or [ALT-WT-*] entry. That is a `RECONSIDERATION REQUESTED`
     for the owner, not an edit.

2. Apply changes A, B, C, D1, D2, F (theory side), and G to
   `docs/program-theory.md`.
   - Use exact-string edits. Do not reflow untouched lines.
   - Do not check out, restore, or stash the file; other sessions may hold
     uncommitted work in this tree.
   - Done when: each Replacement block for `docs/program-theory.md` is
     contained in the file.

3. Apply change F's spec sentence and add the `## Related Plans` bullet to
   `docs/specifications/03-Manager_Architecture.md`.
   - Bullet text: a link to this plan and the words "moves frontier and
     bucket compaction mechanics from program theory [ALT-WT-016] into
     [MA-1.8]; no behaviour change."
   - Stop if: reading `weft/core/family_scheduling.py` shows the "twice"
     rule no longer holds at either site. Report it; do not write a spec
     sentence the code does not satisfy.

4. Set this plan to `promoted` (file metadata and the index row together)
   and record the promotion baseline identifier.
   - Order matters: the gate checks every Replacement block, including the
     spec block from Task 3, so promotion follows both edits (round-1 F1).
   - Verify: `bin/check-doc-paths` passes with the plan promoted.

5. Record the audit-table refresh from section 6 as final, and migrate the
   carried-forward rows to the new measure (round-1 F2).
   - Rows about documents or structure carry forward by reference to
     `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`.
   - Every carried-forward row whose claim is a [WT-4.3] row or a
     behavioural [WT-2a] invariant ([WT-2a.3], [WT-2a.7], [WT-2a.10],
     [WT-2a.13], [WT-2a.14]) gets one of two outcomes, written into the
     migration table in section 6: a named probe with the numbers from a
     run, or the verdict `unmeasured`. No such row stays `aligned` on
     reading alone. This step adds no tooling and repairs nothing.
   - The [WT-4.1] and [WT-4.3] rows name
     `docs/plans/2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md`
     as their closing plan. The [WT-2a.15] row names none: that plan defers
     scoped dispatch and the published refusal, so completing it leaves the
     invariant violated. The row stays drifted until a plan that carries
     both is named. If the repair plan is split or renamed, update the rows.

6. Entry wiring check.
   - Open `docs/agent-context/context.index.yaml`. If it carries a
     timestamp or a description of the theory that is now stale, update it.
     If not, record "no change needed" in the Execution log.

7. Completion gate ([WT-7]).
   - The owner writes a possession-probe key (one feature to place, one
     category error to refuse, one predicted defect class). An agent that
     did not author this plan answers from the revised theory alone. Record
     key and result in the Review Log.
   - Set Status and the index row to `completed`.

## 6. Audit Table Refresh and Behaviour Probes

Touched claims only. Measurements are from the 2026-11-05 review on SQLite,
macOS, default configuration, commit `44d7f900`.

| Claim | Contract | Evidence in the tree | Verdict | Closing plan |
|-------|----------|----------------------|---------|--------------|
| [WT-2a.15] standing refusal is published and scoped | [MA-1.8]; MANAGER.11 | After a Manager SIGKILL between the pre-launch checkpoint and process creation, a later `weft run` stayed undispatched for more than three minutes; status showed the Manager `active` and the task `queued`. `weft/core/manager.py::Manager._recover_scheduler_paths` adds the launch to `_scheduler_undurable`, which gates all ordinary dispatch. [MA-1.8] states that rule. | drifted (code and spec) | not yet named: the repair plan defers scoped dispatch and the published refusal to owner decisions, so it does not close this row |
| [WT-4.1] wake predicate reads facts | [CC-2.1]; [MA-1.8] | `Manager._queue_has_pending` answers for the request queue from the in-memory scheduler. Launch delay for a lone submission to an idle Manager: 17 to 949 ms (mean about 470); 10 to 29 ms with the predicate consulting the queue. | drifted (code) | `docs/plans/2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md` |
| [WT-4.3] quiet costs nothing observable | [CC-2.2.1]; [MA-1] item 5 | Manager CPU with one `sleep` task running: 9.95 s in 10 s. Idle: 0.9%. `Manager.next_wait_timeout` publishes the idle broker-probe deadline while user children exist and the turn never re-arms it. Present in 0.9.105. | drifted (code) | `docs/plans/2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md` |
| Header and [WT-7] behaviour probe | this document | No probe existed; both rows above were marked aligned in the 2026-11-02 table. | theory revision (this plan) | this plan |
| [ALT-WT-016] | [MA-1.8] | Mechanics now stated in the spec only. | aligned | n/a |

Probes named by this revision (procedures, run against a fresh project with
stdin redirected from `/dev/null`):

| Row | Probe | Pass bound |
|-----|-------|------------|
| [WT-4.3] supervising cost | Start one `weft run --no-wait` task that sleeps 20 s. Read the Manager's CPU time (`ps -o time= -p <pid>`) twice, 10 s apart. | under 1 s of CPU in 10 s |
| [WT-4.3] acting on a new input | With a warm idle Manager, submit one task after at least 2 s of quiet; delay is the `task_spawned` row's timestamp minus the request's message ID. Repeat 12 times. | every delay within one strategy pass plus launch; record the numbers |
| [WT-2a.15] published refusal | SIGKILL the Manager between the pre-launch checkpoint and process creation; submit a second task. | the second task dispatches, and status shows the held launch and its reason |

The bounds are recorded numbers, not gates. The repair plan owns the
regression tests.

Migration of carried-forward behavioural rows (Task 5 completes this table
against the 2026-11-02 audit table's row identifiers; the verdicts below are
the starting state):

| Claim | Probe | Verdict at this revision |
|-------|-------|--------------------------|
| [WT-4.3] one reactor, one arbiter | none named | unmeasured |
| [WT-4.3] quiet costs nothing observable | the two [WT-4.3] probes above | drifted (measured) |
| [WT-4.3] readiness-driven supervision | none named | unmeasured |
| [WT-4.3] bounded per-turn stream work | none named | unmeasured |
| [WT-4.3] availability and death differ | none named | unmeasured |
| [WT-4.3] ask who owns this lifetime | none named | unmeasured |
| [WT-2a.3] forward-only lifecycle | STOP and KILL raced against short tasks; count transition violations | unmeasured until rerun and recorded |
| [WT-2a.7] exactly once per reservation | Manager kill around launch under a burst; count tasks run more than once | unmeasured until rerun and recorded |
| [WT-2a.10] exit code carries the outcome | success, failure, timeout, and not-found invocations; compare exit codes | unmeasured until rerun and recorded |
| [WT-2a.13] independent lifetimes | Manager SIGKILL with a task running; task completes | unmeasured until rerun and recorded |
| [WT-2a.14] submission never blocks | submit while dispatch is refused; submission returns | unmeasured until rerun and recorded |
| [WT-2a.15] published refusal | the [WT-2a.15] probe above | drifted (measured) |

The review of 2026-11-05 exercised several of the [WT-2a] probes, but its
numbers were not recorded per row, so they do not count here.

## 7. Verification

```bash
./.venv/bin/python -m pytest tests/specs -q -n 2
bin/check-doc-paths
bin/check-dom15-fixtures
bin/coalesce-check
grep -n "WT-2a.15\|REV-WT-005\|Five measures\|Aligned by reading" docs/program-theory.md
```

Success: the doc gates pass; the grep shows one hit for each of the four
strings (two for `WT-2a.15`: the invariant and the revision record). No
product test is affected, because no code changes.

## 8. Rollback

Revert the edits to `docs/program-theory.md` and the one spec paragraph, and
set this plan back to `draft`. Nothing else depends on them until the repair
plan cites them.

## 9. Out of Scope

- Any code change. The three drifted rows are closed by the repair plan.
- The [MA-1.8] behaviour sentence on ambiguous launches. The repair plan
  carries that delta, with the owner's rulings of 2026-11-05.
- Proposal E (inspectability as an invariant). Not adopted.
- Restoring process titles for internal services, the moved-directory
  bootstrap rule, and the documentation of distinct contexts. Owner rulings
  of 2026-11-05; they belong to the repair plan.
- A probe script or release gate. The owner may ask for one later.

## 10. Owner Decisions

Recorded, 2026-11-05 (owner session, deep-dive review side thread):

1. Changes A, B, C, D, and F are accepted with the wording proposed in that
   session: "No E, otherwise good and accepted as is".
2. Change A includes its second sentence, which makes the [MA-1.8]
   whole-context block drift from the theory. The owner asked what the
   sentence would change, was shown the spec text and the consequence, and
   then accepted the set.
3. Order: "I agree with the theory changes, and those should be made first."

Open: none.

## Review Log

- Author family: Claude (Fable 5.1). Plan drafted 2026-11-05 from wording
  the owner accepted the same day.
- Self-driven fresh-eyes review: done 2026-11-05, a separate pass after
  drafting, four corrections. (1) The audit rows named no closing plan; the
  repair plan file now exists and is named. (2) The spec sentence for change
  F claimed the compaction work is charged to exits at both sites; only the
  twice-live trigger was verified at both, so the clause was dropped. (3)
  Change F's block begins with an existing line; a note now says so, to
  prevent a duplicate insert. (4) Three small additions to the accepted
  wording were not disclosed; section 4 now lists them. Residual risk: the
  probe bounds in section 6 are from one machine under load.
- Cross-family round 1 (2026-11-05). Reviewer: codex-cli 0.161.0,
  `codex exec -s read-only` with high reasoning effort, pointed at the plan
  file (not embedded). Availability notes: qwen 0.17.1 returned API 404 on
  its default model and API 401 with `meta/muse-spark-1.3-contributor` and
  no OpenRouter mapping; the owner then directed "Use codex or agy". The
  tree was unchanged by the reviewer; one unrelated file
  (`skills/call-agent/SKILL.md`) was modified by another session during the
  run. Verdict: **BLOCKED**, criterion (a). The reviewer confirmed that the
  cited paths, symbols, and anchors resolve, that the quoted quiet row
  matches character for character, that change F's threshold holds at both
  code sites, and that it found no contradiction with the standing
  constraints and no new altitude problem.

  | ID | Severity | Finding | Disposition |
  |----|----------|---------|-------------|
  | F1 | P2 | Promotion ran before the spec Replacement block was inserted, so the gate would fail | Accept; fixed in plan. The spec edit is now Task 3 and promotion is Task 4 |
  | F2 | P2 | The new measure marks rows without probes unmeasured, but Task 5 carried older behavioural rows forward as aligned | Accept; fixed in plan. Task 5 now migrates those rows and section 6 carries the migration table |
  | O1 | observation | Recording comprehension answers adds little for a document-only change | Accept; the questions and the Task 0 step are removed |
  | O2 | observation | CPU and latency figures are author-reported, not rerun by the reviewer | No change; section 6 says where they come from |

- Cross-family round 2 (2026-11-05), scoped to F1, F2 and the O1 removal.
  Reviewer: codex-cli 0.161.0 as in round 1; tree unchanged by it.
  Verdict: **PASS**, no new defects. It verified the Task 3 and Task 4
  order and its renumbered references, that the migration table covers the
  six [WT-4.3] rows and the named behavioural invariants without carrying an
  unprobed row as aligned, and that the comprehension step is gone.
- Review state: complete for implementation. Task 1 is satisfied by rounds
  1 and 2; the next step is Task 2.
- Possession probe: pending (Task 7).

## Execution log

- 2026-11-05: plan drafted. Anchors read at `44d7f900`: [WT-2a.14] bullet at
  lines 368 to 374; [WT-4.1] row "Keep an identity you already have" at line
  434; [WT-4.3] row "A quiet task does nothing" at line 452; header
  Verification at lines 10 to 27; [ALT-WT-016] span at lines 812 to 818;
  [WT-7] bullet "Event-driven against absence" at lines 1057 to 1059;
  [MA-1.8] paragraph end at line 541 of the Manager spec.
- 2026-11-05: Tasks 0 to 4 done. Anchors re-verified before editing; none
  had moved. Task 2 applied A, B, C, D1, D2, F and G to
  `docs/program-theory.md` by exact-string insertion (no reflow); Task 3
  added the [MA-1.8] compaction sentence and the `## Related Plans` bullet.
  Task 4 set `promoted`. Gates from the current tree: `bin/check-doc-paths`
  OK (all eight Replacement blocks contained in their targets);
  `tests/specs` passed; `bin/check-dom15-fixtures` OK; `bin/coalesce-check`
  all cues resolve.
- Task 5: the migration table in section 6 is the recorded state. No
  behavioural probe was rerun for this revision, so every carried-forward
  behavioural row stays `unmeasured` until a release run records its
  numbers.
- Task 6: no change needed. `docs/agent-context/context.index.yaml` carries
  read order and document inventory, neither of which this revision
  changes; its `updated_at` was not advanced by the 2026-11-04 theory edit
  (`60d758d0`) either.
- Remaining: Task 7 (owner-written possession-probe key, a non-author
  agent's answers, then `completed`). Nothing is committed.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
|------|----------|---------|-------------------|---------------------|
| 2026-11-05 | `7e9c6106` | Change B ([WT-4.1] wake-predicate row) | Owner-approved fix ("Make that change", 2026-11-05) after the two-factor fit trial. The other-family account showed the row stated only a soundness condition ("true only when ..."), which does not prevent the lost wake that motivated it, and that "answered from queue rows" contradicted [WT-2a.6] local inputs. The row now states completeness ("true whenever actionable work is present"), scopes itself to predicates over queued work, and names local completions and deadlines as arbiter inputs. | No cross-family round; the fix applies the reviewer's own finding and is re-checked by the repeated fit trial |
| 2026-11-05 | `90e1de3f` | Section 6 [WT-2a.15] row; Task 5 | An outside review of the repair plan (relayed by the owner) found that the repair plan defers scoped dispatch (its D1) and the published refusal (its D5), so it cannot close [WT-2a.15]. The row now names no closing plan and stays drifted. | No |
