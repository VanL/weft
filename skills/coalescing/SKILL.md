# Coalescing Sweep

Status: Active — governed by the coalescing model in
`docs/coalescing.md` (adopted from agent-guidance [DOM-14]). The
session-start trigger check is read-only; sweeps run only as authorized
units of work. (Adopted from agent-guidance @ 2f7eff6 via
`docs/plans/2026-07-14-agent-guidance-propagation-plan.md`.)

## Purpose

Run the compounding layer's maintenance pass: distill cold lesson entries
into golden rules and runbook amendments, harvest and retire completed
plans, promote recurring workflows to skills, and (in the guidance repo)
fold cross-repo lessons upward. Keeps the always-read documentation tier
small and hot while git history holds everything raw.

## When To Use

- The session-start trigger check is **read-only**: derive the counts,
  compare to `checked_through` state, and report a trip to the user in one
  sentence. Do not write anything — not even a checked-deferred line —
  outside an authorized maintenance task. Guidance cannot broaden the
  authority of the user's current request.
- A sweep (including recording a checked-deferred entry) is its own unit
  of work, run when the user asks for it or agrees to it at a natural
  completion boundary (a plan just closed, a release just shipped). One
  boundary is standing: a repo that just adopted this layer via
  propagation runs its first sweep as part of the propagation unit. A
  count at **twice** its threshold escalates the report to a strong
  recommendation — still not self-authorization.
- A trip is only news when it is new: if the counts are unchanged since
  the recorded `checked_through` state and its reconsideration condition
  has not fired, do not re-report every session.
- Do NOT use mid-task as a tidy-up reflex, and do NOT stretch "related
  work" to dodge the report: reporting costs one sentence.

## Governing Spec References

- `docs/coalescing.md` (this repo's normative state file) and the Task
  Classification section of `docs/agent-context/decision-hierarchy.md`.
  Source model: agent-guidance [DOM-14] @ 2f7eff6.

## Read First

- `docs/coalescing.md` — thresholds, watermarks, run log (this repo's state)
- `docs/lessons.md` — the ledger being distilled
- `docs/plans/README.md` — plan status index and retired-plans ledger
- `docs/agent-context/runbooks/writing-plans.md` — plan lifecycle and the
  harvest gate
- `docs/agent-context/runbooks/writing-plans.md` — the retired-plan
  citation form lives in its Plan Lifecycle section here

## Blast Radius

- `docs/lessons.md` (entries folded), `docs/lessons.md` Golden Rules and
  `docs/agent-context/engineering-principles.md` (rules added or edited)
- `docs/plans/*.md` (files deleted on retirement) and every spec
  `## Related Plans` section that backlinks a retired plan
- `docs/plans/README.md` (status index, retired ledger)
- `skills/` (new or updated skills on promotion)
- The repository traceability gate: rerun backstitch (or the repo's
  equivalent) after any retirement — deleted paths must not leave dead
  path claims.

## Workflow

### 1. Derive the trigger counts (never trust a stored number)

Read the watermarks in `docs/coalescing.md`, then compute. The state
file owns the repo-local ledger format: when it declares a derivation
command, use that command — the bullet grep below is the default for
dated-bullet ledgers only.

- Lessons past watermark — dated entries newer than the lessons watermark:

  ```bash
  grep -E '^- 20[0-9]{2}-[0-9]{2}-[0-9]{2}:' docs/lessons.md
  ```

  Count the lines with dates after the watermark date.

- Completed-unretired plans — derivation chain, in order:
  1. rows in the `docs/plans/README.md` status index with status
     `completed` or `superseded`, no `exemplar` marker, and no matching
     line in the Retired Plans ledger;
  2. if no status index exists, `Status:` headers inside the plan files;
  3. if neither exists, the tier is **not derivable** — record
     "plans tier blocked: no status source" in the run log and move on.
     Never guess plan status from file age or filename.

- Skill candidates — recurring workflow themes across lesson entries and
  review dispositions. This count is an **attention signal, not a
  mechanical gate**: theme identity is a judgment call (see step 4 for
  what counts as one theme). Use grep to gather candidates, judgment to
  cluster them.

Compare each count to the declared threshold. If none is tripped and you
were not explicitly asked to sweep, stop — record nothing.

### 2. Lessons tier: distill, then retire

At sweep start, pin the source: `source_sha` is a commit that verifiably
contains the raw material about to be folded — check with
`git show <source_sha>:docs/lessons.md`. If the entries exist only in the
worktree, there is no valid source yet: the destructive phase is blocked
until the raw state is committed (or the sweep stays additive-only).

For each tripped or requested fold:

1. Cluster candidate entries by theme. **Skip anything hot**: younger than
   the age floor in `docs/coalescing.md`, cited by an active plan, or part
   of a theme that is still accumulating. Semantic boundaries decide the
   cluster; the threshold only decided that you looked.
2. **Dedup before drafting.** Check each cluster against the existing
   Golden Rules and engineering-principles sections: entries that are
   already distilled fold as a pointer ("distilled as Golden Rule N"),
   never as a duplicate rule. Duplicate distillation is a defect.
3. For each genuinely new cold cluster of 3+ entries, draft the
   distillation: a new or amended Golden Rule, an engineering-principles
   section, or a runbook amendment. **Verify the rule against the
   citations** — check what the entries actually said, not what memory
   says they said; memory-drafted rules overclaim. When a fold changes an
   existing rule's meaning, annotate it in place —
   `(revised YYYY-MM-DD; was: <gist>)` — so citations to the rule stay
   interpretable across history.
4. Write the distillation and its fold cue in the surviving text:
   `(distilled from N entries, YYYY-MM-DD..YYYY-MM-DD, source <source_sha>)`.
   The cue names the pre-fold commit that contains the raw material — never
   the fold commit, which cannot contain its own hash and does not contain
   the deleted entries. The fold commit may be added to the run log after
   it exists, as metadata.
5. **Destructive phase — only with landing authorization.** Only after the
   distillation is written, its links resolve, and `source_sha` is
   verified, delete the folded raw entries and advance the lessons
   watermark. In an uncommitted-review session, stop after step 4:
   present the drafts and candidates, delete nothing, advance nothing.
6. Decay evidence is multi-signal: absence of citation alone never
   justifies a fold — agents follow rules without citing them. Weigh
   recent incidents, test coverage, review recurrence, last validation,
   and importance class. Golden rules and safety invariants are exempt
   from automated decay entirely (importance floor); they change only by
   explicit revision, supersession, or deprecation, always with the
   `(revised YYYY-MM-DD; was: <gist>)` marker. An uncited cold entry with
   no rule potential may be folded to a one-line summary rather than a
   rule.

### 3. Plans tier: harvest gate, then soft-retire

For each completed or superseded plan:

0. Skip plans marked `exemplar` in the status index — they are exempt
   until the index note says their exemplar role has been superseded.
1. Run the harvest gate — all four must pass, none waivable:
   - deviation log closed (no `pending` spec proposals)
   - durable rationale absorbed into the governing spec or implementation
     doc, or explicitly judged not durable (say so in the ledger line)
   - lessons extracted where applicable
   - every spec backlink converted to the retired citation form:
     `- retired: <plan-name> — source <source_sha>; see docs/plans/README.md`
2. Superseded plans: confirm the successor names what it inherits before
   retiring the predecessor. If it does not, fix the successor first.
3. **Soft-retire only** — the sweep never deletes plan files. Flip the
   index status to `retired-pending`, convert the backlinks, and add the
   ledger line to `docs/plans/README.md` Retired Plans: plan, dates,
   one-sentence outcome, what absorbed it, source SHA (a commit verifiably
   containing the plan file).
4. Physical deletion is a dedicated follow-up change, made only after a
   second agent or the user re-verifies the harvest gate for each
   `retired-pending` plan. Never soft-retire and delete in the same
   change.
5. A plan that fails the gate stays in the tree at its current status;
   note the blocking item in the run log if the threshold keeps nagging.

### 4. Promotion tier: runbooks and skills

- A workflow theme with repeated citations across plans or sessions becomes
  a skill (`skills/<name>/SKILL.md`, template section order) per
  `skills/README.md` (repo-root).
- What counts as one theme is a judgment call with a narrow definition:
  same workflow, same failure surface, same fix shape. Three entries about
  deferred-work lifecycle cleanup are one theme. Three entries that all
  mention tests but describe unrelated failure modes (a weak assertion, a
  flaky wait, a missing fixture path) are three themes — do not cluster on
  a shared keyword.
- A rule that applies to almost every change strengthens a runbook instead.
- Presence in the always-read context is NOT promotion evidence; only
  explicit citation in work products counts.

### 5. Guidance repo only: fold-up

When a distilled rule generalizes beyond one repository, propose it to the
guidance repo's ledger/principles with SHA-pinned provenance (repo, source
SHA, date range). Independence check first: two repos exhibiting the same
rule counts as fold-up evidence only when the incidents or adaptations are
independent — two descendants of one inherited/bootstrapped rule are one
lineage, not two data points. Sibling repos re-sync from the guidance
repo's committed SHA — never from a working tree.

### 6. Close the run

1. Append one run-log line per tier touched to `docs/coalescing.md`:
   date, source SHA, and the claim ("folded 6 lesson entries → Golden Rule
   14; soft-retired 2 plans; deferred plans tier — deviation log open on
   X"). The fold commit may be added as metadata once it exists.
2. If nothing was foldable, record the deferral with real state:
   `checked_through` (date + SHA), the derived counts, the reason ("all
   entries within age floor"), and a reconsideration condition ("recount
   when 5 more entries land" / "when plan X closes"). This is what stops
   an unchanged count from re-nagging every session.
3. Advance watermarks only in the destructive phase (landing-authorized).
   An additive-only session leaves watermarks untouched and says so in
   its run-log line.
4. Rerun the repo's traceability gate and record the result in the run-log
   line.
5. Commit per the session's authorization; if the sweep stays uncommitted,
   it must have been additive-only (see step 2 of the lessons tier).

## Output Standard

When the sweep is done, these exist and are verifiable:

- run-log line(s) in `docs/coalescing.md` whose claims survive a diff
  spot-check of the fold commit
- watermarks advanced to match
- every fold cue resolves: `git show <source_sha>:<path>` contains the
  folded material — verified, not assumed
- traceability gate rerun from current state, result recorded
- no raw material deleted without its distillation already in the tree
  (two-phase order visible in the diff or commit sequence)

## Maintenance Notes

- If a threshold repeatedly trips with nothing foldable, the threshold or
  age floor is miscalibrated — adjust it in `docs/coalescing.md` and note
  why in the run log.
- If an agent is ever unable to recover folded material from a cue, that is
  a broken-summary incident: record a lesson and strengthen the cue format
  here.
- If the harvest gate keeps blocking on the same item class, the gap is
  upstream (plans closing with open deviation logs) — fix the completion
  gate usage, not the sweep.
- When an executable `coalesce-check` script exists, replace step 1's
  manual derivation with it and keep the commands here as the fallback.
