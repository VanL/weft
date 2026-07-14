# Decision Hierarchy

Use this order whenever instructions or context seem inconsistent:

1. Explicit user instruction in the current thread.
2. Safety and repository constraints:
   dirty-tree discipline, no destructive commands, do-not-revert-others.
3. Task source-of-truth documents:
   relevant spec files, invariants, and user-facing README behavior when
   applicable.
4. Canonical repo context in `docs/agent-context/`.
5. Relevant plans in `docs/plans/` as execution guidance. Plans are
   non-normative for product behavior, but the current approved plan may be
   authoritative for scope, sequencing, rollback, and review expectations for
   the active slice. Plans may also reflect exploration, partial
   implementation, or superseded thinking.
6. Root agent files such as `AGENTS.md` and `CLAUDE.md`.
7. Existing code patterns.
8. Agent inference.

Normative rule:

- Specs are the source of truth for Weft behavior.
- Plans are execution aids. They should cite specs, and the current approved
  plan may control how the active slice is carried out, but plans do not
  override specs on product behavior and may be stale.
- For in-flight spec changes, the current approved plan may describe the
  intended delta before the spec edit lands. The slice is not complete until
  the spec is updated and becomes the steady-state source of truth again.

## Classify Before the Preflight

Before the repository preflight or first edit — after explicit user
instructions and safety constraints, which always rank higher —
declare the task class per the Task
Classification section below ([DOM-15]). The
class decides whether the full preflight below applies (classes 3+)
or the abbreviated four-item record defined in the class-2 row
suffices (classes 1–2 use their commit message or handoff report).
The class is a claim; escalators are one-way and declared.

## Task Classification [DOM-15]

Every unit of work is classified before the repository preflight or
first edit. The unit of work is the whole requested outcome; slices
inherit the unit's minimum class. Classification scales planning
artifacts and review machinery; it never scales the verification floor —
evidence lines, completion claims backed by reruns from current state,
firing tests for touched enumerable contracts, failing-test-first with
its named exit (§4.1 in this repo), declared deviations,
formatter ownership, no agent self-attribution, and dirty-tree
discipline apply identically at every class.

The class is the **highest trigger that fires**, judged by what the
change requires — not by what the author chooses to produce:

| Class | Fires when | Planning artifact | Review |
|-------|-----------|-------------------|--------|
| 0 — Read-only | Nothing in the repository changes | None | None; claims cite evidence and distinguish verified from inferred |
| 1 — Trivial | A change with no observable behavior change and no normative doc force (typos, comments, link repairs, formatting) | Classification line plus what/why/verification, recorded in the commit message — or in the handoff report when the work is left uncommitted for review | None |
| 2 — Small | Observable behavior changes but **conforms to existing intended behavior**, evidenced by something independently inspectable — a governing spec section, an explicit user requirement in the session, or an existing contract test. Author inference is not intent evidence; without it, the class is 3. Also requires: reversible, and **no non-trivial or risky trigger fires** | The abbreviated preflight, pre-edit: (1) outcome checklist, (2) the intent evidence or `Source spec: None — <reason>`, (3) invariants that must not move, (4) the planned verification command. The observed result is appended at completion. Recorded in the commit/PR description or handoff report | Author fresh-eyes |
| 3 — Standard | Any **non-trivial trigger** (multi-surface work, new behavior under an existing spec, a reusable workflow, or zero-context ambiguity) | Full dated plan per `runbooks/writing-plans.md`, status-index row, deviation log | Independent review of the plan **and** of the completed work (per `runbooks/review-loops-and-agent-bootstrap.md`) |
| 4 — Risky | Any **risky trigger** per `runbooks/writing-plans.md` "When Hardening Is Mandatory" | Class 3 plus the hardening-plans checklist | Class 3 plus review before implementation begins |
| 5 — Spec-changing | **materiality requires a spec change** (intended behavior, a governing boundary, or how future work is planned/implemented/reviewed/verified changes) (whether or not one has been drafted), or any normative spec text is edited — including clarification-only edits, which use promotion strategy D per `writing-plans.md` §4c | Class 3 plus spec baseline, exact proposed delta, named promotion strategy; the hardening-plans checklist **only if a risky trigger also fires** — otherwise declare `hardening: N/A — no risky trigger` | Class 3 reviews plus independent review of the delta before the spec-promotion slice; review-before-implementation when hardening applies |
| +P — Process-changing (modifier, not a class) | The change is material to how future work is **planned, implemented, reviewed, or verified** — regardless of which surface hosts it. A non-material edit to a skill or runbook (a typo, a link fix) is not +P; a material process change hiding in an "implementation" doc is | Declared as `Class N+P`; effective requirements are `max(N, 5)`'s | Effective class's review plus pre-landing review, different agent family preferred |

Rules:

- the review and verification floors accumulate; planning artifacts
  **subsume**: a higher-class plan replaces the lower-class records, it
  does not add to them (a class-3 plan is the planning record — no
  separate class-2 preflight note is owed). The hardening-plans
  checklist is required by the class-4 trigger, never by inheritance:
  class-5 work with no risky trigger declares `hardening: N/A —
  no risky trigger` instead of writing empty rollback sections. Risk
  (the hardening trigger list) and materiality (the spec-change test)
  are different axes; they combine when both fire
- class-3 independent review may return a short structured brief —
  goal, class claim, invariants, verification, top risks. The brief is
  an **output** form only: the reviewer still receives the canonical
  inputs (governing spec, plan, touched files) and the disposition loop
  still runs in full. Classes 4 and 5 keep the full output bar. Author
  fresh-eyes substitutes for independent review only when no second
  agent is available, with the limitation disclosed — at every class
- classification is a one-line declared claim citing its trigger
  reasoning ("Class 2: restores spec section XYZ-3 intent, reversible, no risky or
  non-trivial trigger"); an undeclared class on non-read-only work fails the
  completion gate
- escalators are one-way and declared mid-flight: when any non-trivial or risky
  trigger or material discovery fires during work, the class
  rises to that trigger's class at that moment. The engineering
  warning signs (a second path appearing, rollback becoming
  undescribable) are not triggers of their own — they force
  re-classification against the same trigger lists. Silent
  continuation at the old class is the violation, not the escalation
- `+P` is a modifier: it combines with the base class as
  `max(base, 5)` plus the pre-landing different-family review; there
  is exactly one declaration format, `Class N+P`
- classes 1–2 keep their record in the commit history (or the handoff
  report when uncommitted) — git is the ledger for small work, which
  also keeps `docs/plans/` free of coalescing harvest debt
- when classification is genuinely uncertain after reading the trigger
  lists, ask once, narrowly

Classification fixtures. This table is [DOM-15]'s enumerable contract
(the "Enumerable Contracts Get Executable Gates" principle, §9 in this repo) and carries an executable gate: a
repository adopting this section ships a structural checker that fails
when a fixture names an unknown class, a class or the `+P` modifier
has no fixture, a class-1/2 fixture omits its negative-trigger facts,
or the cumulative-requirements rule is absent (this repository:
`bin/check-dom15-fixtures`, exit nonzero on violation). Semantic
classification of real tasks remains judgment, verified by the
declared-claim line and by review; repositories with test harnesses
additionally encode these fixtures as firing tests over their own
tooling. Fixture rows state their trigger facts explicitly — class
follows from the stated facts, never from file topology. Edits to
Edits to the trigger lists update these fixtures in the same change: the
checker enforces presence, review enforces meaning.

| Fixture (trigger facts stated) | Class |
|---------|-------|
| Answer an architecture question; survey a repo — nothing changes | 0 |
| Fix a spelling error; repair a broken doc link — no behavior change, no normative force, no trigger fires | 1 |
| Behavior-preserving refactor, one module, following the established pattern — given: no non-trivial or risky trigger fires (in particular, no zero-context ambiguity) | 1 |
| Behavior-preserving refactor across two modules with unclear ownership — zero-context ambiguity, a non-trivial trigger, fires | 3 |
| Bug fix restoring validation that a cited spec section requires — the cited section is the intent evidence; reversible; given: no trigger fires | 2 |
| Same fix, but no spec, no stated user requirement, no contract test — intent evidence absent | 3 |
| Fix spanning a producer and a consumer — given: the two sides are distinct major surfaces, so a non-trivial trigger fires | 3 |
| Same shape, but both sides live inside one module — reversible, spec-cited intent, and no other trigger fires | 2 |
| Implement an already-specified CLI flag — CLI shape changes (risky) | 4 |
| Introduce background or deferred processing whose intended behavior an existing spec already governs — a risky trigger fires; no spec change is required | 4 |
| Clarify normative spec wording, behavior unchanged — normative spec text edited; no risky trigger, so `hardening: N/A` | 5 (strategy D) |
| New feature whose intended behavior is undocumented and material — a spec is required first | 5 |
| Materially change a skill, runbook, or gate — material to future process; base class 3 | Class 3+P (effective 5) |
| Typo fix inside a skill file — not material | 1 |
| Class-2 fix discovers a storage-format edit is needed — a risky trigger fires mid-flight | Escalate to 4 at that moment, declared |

Owner: the agent starting the work declares the class; any reviewer
may challenge it. Boundary: every unit of work from promotion of this
section forward; explicit user instructions and safety constraints
still rank above classification in the decision hierarchy.
Verification: the declared class line plus the class-required
artifacts existing; new classification guidance checked against the
fixture table. Required action: declare the class before the first
edit; escalate loudly the moment a trigger fires.


## Required Preflight Before Edits

- List the requested outcomes as a checklist.
- Identify the source-of-truth files for the task.
- Record a baseline identifier for the governing spec in the plan, so
  "compliant" always means compliant with a fixed, named object: the commit
  SHA when the spec is committed; otherwise the file path plus worktree
  state and diff base; or an explicit "this change revises the spec itself".
- When the plan includes a `## Proposed Spec Delta`, record both the **spec
  baseline** (at plan start) and, after the **spec-promotion slice**, the
  **promotion baseline identifier** (after the delta is applied to
  `docs/specifications/`). Use a commit SHA when committed; otherwise diff
  base plus worktree state. Mid-implementation claims are against the
  promotion baseline — not plan appendix text and not the pre-promotion
  identifier.
- Call out invariants that must not move.
- Record assumptions that could change correctness.
- Decide which commands can run in parallel and which must run in sequence.

## Conflict Handling

- If user correction conflicts with agent inference, stop and re-derive.
- If specs and code disagree, follow the hierarchy above and call out the
  mismatch.
- Never edit the governing spec silently while claiming compliance with its
  previous version. If implementation reveals the spec must change: pause,
  record the deviation in the plan's deviation log (see
  `runbooks/writing-plans.md`), update the spec as an explicit spec-revision
  slice, then implement against the revised spec — this is how "the spec must
  be updated before the work is done" and "deviation is declared" coexist.
  Spec-authoring tasks (merges, harvests, revisions) edit specs in
  `docs/specifications/` as their primary work product and follow the
  conventions and traceability rules in `docs/specifications/README.md`; they
  still record the baseline they started from for downstream implementers.
- **Spec-changing implementation** (see `runbooks/writing-plans.md`
  §4b–4d): the plan carries exact proposed sections for review; the
  **spec-promotion slice** (early, not last) applies them to
  `docs/specifications/` per a named strategy (A/B/C/D). Do not implement
  code that cites spec paths against plan-only text while
  `docs/specifications/` still reflects the baseline. **Exploration**
  (behavior undecided) is not implementation against a governing spec — when
  behavior is decided, promote to `docs/specifications/` first, then build.
  Prose status ("Proposed", planned-companion prose) and backstitch's machine
  classification (`planned_spec_globs` / `exploratory_spec_globs`) are
  different mechanisms — see §4d.
- **Single governing contract:** after promotion, `docs/specifications/` is
  canonical. The plan's delta is historical review material, not a parallel
  source of truth.
- If uncertainty remains on a high-impact change, ask once and narrowly.

## Completion Gate

Every requested item should have at least one evidence line:

- file path and what changed
- command executed and result
- observed queue, task-state, or CLI behavior

A status document — a ledger, an execution log, a prior "passing" note, your
own earlier summary — is a claim, not evidence. Every "done", "passing", or
"ship-ready" assertion must cite a command rerun from the current state, not
a document that says so.

For spec-changing work, the final completion gate includes **traceability
reconciliation**: the promoted spec in `docs/specifications/`, plan,
implementation notes, and code form a closed chain, and any traceability or
self-check command named in the plan (for weft, the backstitch gate) has been
rerun from the current state. Where the repo declares that gate mandatory
(for example a zero-error, zero-warning check), it is not waivable. Clearing
reciprocal backlink debt (`CODE_BACKLINK_RECIPROCAL_MISSING`) and completing
classification graduation (when strategy C was used) are part of done, not
optional cleanup.
