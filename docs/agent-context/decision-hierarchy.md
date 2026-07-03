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
