# Writing Implementation Plans

Plans must be detailed enough that a skilled developer with little or no Weft
context can implement them correctly without guesswork.

Write every plan as if the implementer is technically strong but:

- has zero context for the Weft codebase
- knows almost nothing about the local toolset or problem domain
- has questionable taste when the plan leaves room for judgment
- will make poor local design choices if the plan leaves room for them
- tends to over-mock tests unless the real production path is named
- does not know good test design unless the plan explains the observable
  behavior to prove
- and will follow ambiguous instructions literally

If the plan is ambiguous, assume the implementer will choose the wrong file,
the wrong abstraction, the wrong test seam, and a mock-heavy proof that hides
the real behavior.

For risky or boundary-crossing work, the plan is not review-ready or
implementation-ready until it also satisfies:

- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

Every plan requires a self-driven fresh-eyes review before the author reports
it as complete or implementation-ready. This review must look for latent
ambiguity, bad ideas, missing constraints, and whether a zero-context engineer
could implement the plan correctly. The plan author may perform this review,
but it must be a separate pass after the draft is written, with findings
recorded or explicitly reported.

For high-stakes or complicated plans, request an external reviewer in addition
to the self-review. External review is warranted when the work is risky,
crosses subsystems, affects runtime behavior, or could plausibly be
misimplemented while still looking reasonable. If the reviewer is a different
agent, expect the review to take 5-10 minutes and do not treat that delay as a
reason to skip it.

## Audience Assumptions

- Strong Python engineer, limited or zero Weft and SimpleBroker context.
- Unfamiliar with repo-specific code style and helper paths unless you point to
  them explicitly.
- Prone to over-abstracting or future-proofing if shared paths are not called
  out explicitly.
- Prone to over-mocking tests unless the production path is spelled out.
- Prone to treating tests as implementation probes unless the plan names the
  contract, fixture, and queue/process evidence to assert.
- Will follow the plan literally, including ambiguities.

## Planning Standard

Plans are executable documents, not rough notes. They are still non-normative.
They may be written during exploration, and later work may implement only part
of them or supersede them.

The rule is:

- specs define behavior
- plans explain one proposed path to implement or change that behavior
- the current approved plan may define the execution shape for the active
  change, but it does not overrule the specs on behavior

For spec-driven in-flight work, the plan may describe the intended spec delta
before the spec text is updated. That is temporary. Before the work is done,
the spec must be updated so the normative truth returns to
`docs/specifications/`.

If a plan and a spec disagree, fix the plan or the spec. Do not quietly treat
the plan as authoritative.

- Document everything the implementer needs to know to succeed on the first
  pass: source specs, files to touch, files to read first, local style rules,
  helpers to reuse, invariants to preserve, tests to write, and commands to
  run.
- State what must not change, not just what should be added.
- Break work into bite-sized, dependency-ordered tasks. Each task should be
  small enough to implement and verify independently.
- Prefer explicit local reuse over invention:
  DRY means reuse existing paths and helpers, not invent new abstractions.
- Apply YAGNI aggressively: do not future-proof, generalize, or refactor
  beyond the current spec and request.
- Prefer red-green TDD when the behavior can be expressed cleanly as a failing
  test first. If not, say why and name the smallest concrete proof that should
  replace it.
- Treat DRY, YAGNI, and red-green TDD as planning requirements, not slogans:
  name the existing helper to reuse, the abstraction not to add, and the
  failing test or equivalent proof for each behavior change.
- For risky work, write rollback and rollout notes early enough to shape the
  task breakdown.
- Required reading should describe the current structure and load-bearing path,
  not only list file names.
- Before reporting plan work as done, complete and report the required
  fresh-eyes review status. If external review is warranted but not yet
  complete, report the plan as draft/review-pending, not implementation-ready.

## When Hardening Is Mandatory

Treat plan hardening and independent review as mandatory when any of these are
true:

- execution touches the durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`
- the same core behavior must run through more than one context such as CLI,
  manager, worker, task, or watcher code
- a public contract, CLI shape, queue name, TaskSpec field, result payload, or
  persisted format is changing
- rollback depends on backward compatibility or rollout order
- the work introduces async/deferred processing, background jobs, temp-file
  lifecycle, or queue-cleanup concerns
- the blast radius crosses multiple documentation layers or execution
  boundaries
- the change includes a one-way door

## File Placement

- Put plans in `docs/plans/` — no other location (not `docs/superpowers/plans/`,
  not project subdirectories, not alongside specs).
- **Always** use a date-prefixed filename: `YYYY-MM-DD-<descriptive-name>.md`.
  The date is the creation date of the plan.
- Do not rename existing historical plans just to enforce a naming convention.
- Every plan must include the normalized metadata block immediately after the
  level-1 title, with these exact keys and order:

  ```text
  Status: draft
  Source specs: docs/specifications/... [REF] or None - reason
  Superseded by: none
  ```

- `Status` must be either `draft` or `completed`.
- `Superseded by` must be `none` or a valid relative Markdown link to an
  existing plan file.
- Every plan file in `docs/plans/` must have a matching row in
  `docs/plans/README.md`, and the row status must match the plan metadata.
  This is enforced by `tests/specs/test_plan_metadata.py`.
- If changing canonical agent-context guidance, update
  `docs/agent-context/context.index.yaml` when its timestamp, read order, or
  document inventory would otherwise become stale.

## Required Plan Sections

### 1. Goal

One short paragraph on what is changing and why.

### 2. Source Documents

Link the source spec(s) and any existing plan or README behavior that defines
the desired outcome.

When citing an older plan, say whether it is still fully intended, partially
landed, or exploratory if that status is not obvious from context. Do not make
the implementer infer normative behavior from a historical plan.

Also link the local guidance the implementer needs in order to match repo
standards, such as `AGENTS.md`, `docs/agent-context/principles.md`, or a
relevant existing plan/runbook, when those documents materially affect how the
work should be done.

Use exact spec files and section identifiers when they exist. Prefer:

```text
Source specs:
- docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-6]
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
```

Not:

```text
Source specs:
- docs/specifications/05-Message_Flow_and_State.md
- docs/specifications/10-CLI_Interface.md
```

If no spec exists, say so plainly:

```text
Source spec: None — bug fix / refactor / tooling change
```

### 3. Context and Key Files

For each task, list:

- files to modify
- files to read first
- code style or guidance documents to consult
- shared paths or helpers that must be reused
- what the important current files, queues, entry points, or contracts do today

Example:

```text
Files to modify:
  - weft/core/manager.py
  - tests/core/test_manager.py

Read first:
  - docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-6]
  - weft/core/tasks/base.py
  - tests/helpers/weft_harness.py

Style and guidance:
  - AGENTS.md
  - docs/agent-context/engineering-principles.md

Shared path — do not duplicate:
  - weft/core/tasks/runner.py handles the monitored subprocess boundary

Current structure:
  - weft/cli/run.py owns the manager launch/wait path today
```

Do not make the implementer infer the file list from prose later in the plan.
Spell it out.

For risky work, required reading should not stop at file paths. Add one or two
comprehension questions so the implementer can verify they understand the
load-bearing behavior before editing.

### 4. Invariants and Constraints

Call out the invariants that the change must preserve. At minimum, consider:

- TID format and immutability
- forward-only state transitions
- reserved queue policy
- `spec` / `io` immutability after TaskSpec creation
- spawn-based process behavior for broker-connected code
- runtime-only `weft.state.*` queues staying out of persistence features
- whether the change touches template TaskSpecs, resolved TaskSpecs, or both
- whether queue-history reads must use generators rather than fixed limits
- hidden couplings or state that crosses CLI, manager, worker, and queue
  boundaries
- which failures are fatal versus best-effort
- which auxiliary failures must not downgrade a successful core operation
- rollback compatibility that must hold during rollout
- one-way doors that need a higher verification bar

Also call out any review gates that matter for this slice, for example:

- no new execution path
- no drive-by refactor
- no new dependency
- no public CLI shape change
- no spec drift between touched docs and code
- no mock-heavy substitute for a real broker/process proof when the real path
  is practical
- self-driven fresh-eyes review completed before implementation
- external review required or not required, with the reason

### 4a. Deviation Log

Every plan that implements against a spec carries a `## Deviation Log`
section, empty at the start, appended to whenever implementation departs
from the recorded baseline (see the decision hierarchy: deviation is
legitimate; undeclared deviation is not). One row per departure:

```markdown
## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
```

The `Spec proposal` column holds the pointer to the spec-revision slice or
proposal that reconciles the deviation, or `pending` — it must not stay
`pending` past the plan's completion gate. An empty deviation log at the end
of a plan is a claim ("we built exactly what the baseline says"), and like
any claim it should survive a spot-check against the diff.

### 4b. Spec Baseline

Every plan that implements against a committed spec records where
implementation started:

```markdown
## Spec Baseline

- `abc123def` — docs/specifications/02-TaskSpec.md,
  docs/specifications/05-Message_Flow_and_State.md at plan authoring time
```

Rules:

- use the commit SHA when the spec is committed
- if the plan **revises** the spec, say so explicitly (`Plan type:
  implementation with spec revision`)
- after the **spec-promotion slice** (see §4d), record a **promotion
  baseline identifier** — where the proposed delta was applied to
  `docs/specifications/`. Use a commit SHA when that slice is committed;
  otherwise use diff base plus worktree state and the spec file diff (same
  spirit as the spec baseline). Mid-implementation compliance claims are
  against the promotion baseline, not the pre-promotion identifier. Do not
  require an intermediate commit before continuing when the user wants
  uncommitted review — require a recorded identifier and a rerunnable
  verification gate instead
- spec-authoring-only plans record the baseline they started from and the
  identifier after the spec lands

### 4c. Proposed Spec Delta

When a plan changes intended behavior, include exact proposed spec text for
review — not a summary. The active spec at the baseline identifier remains
the governing contract until promotion; the delta is the **review target**
and, after promotion, the **implementation target**.

```markdown
## Proposed Spec Delta

Promotion strategy (see §4d — pick one):

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/05-Message_Flow_and_State.md | A — in-file, text before link claims | [MF-2] paragraph after … |

### [MF-2] — insert after "…" paragraph

> (exact proposed markdown — replacement or insertion text)
```

Rules:

- inline exact sections when the delta is small; link
  `docs/plans/YYYY-MM-DD-<name>-spec-draft.md` when it is large
- every touched requirement must cite stable section codes such as `[MF-2]`
  or `[CLI-1.1.1]`
- name the **promotion strategy** (§4d) — not merely "add to spec"
- clarification-only deltas (behavior already matches code) still belong
  here so reviewers see the exact wording
- do not treat plan-only text as a second governing contract after
  promotion — once promoted, `docs/specifications/` is canonical

### 4d. Spec-Changing Work — Slice Order

**Owner:** plan author defines slice order; implementer follows it literally.
**Boundary:** applies when intended behavior in `docs/specifications/`
changes or new sections are added under it. **Verification:** each slice
names commands; the final gate includes traceability reconciliation (below).
**Required action:** pick a plan type; never implement against plan appendix
text while `docs/specifications/` still reflects the baseline.

#### Plan types

| Type | When | First slice |
|------|------|-------------|
| **Implementation** (default) | Behavior decided; code will cite spec paths | Spec-promotion slice (strategy below), then code |
| **Spec-authoring** | Harvest, clarification, merge — spec is the primary deliverable | Apply delta to `docs/specifications/`; no separate "promotion" task |
| **Exploration** | Intended behavior not yet decided | No implementation against a governing spec; spike only. When decided, open a new plan and promote first |

Exploration is not "park the spec in the plan." Once behavior is cited from
shipped code, the text must live in `docs/specifications/` using a named
promotion strategy so backstitch (and reviewers) can see it.

#### Default slice order (implementation with spec revision)

1. **Plan** — baseline, proposed delta (with promotion strategy), invariants,
   tasks, deviation log (empty)
2. **Independent review** — critiques plan **and** proposed delta
3. **Spec-promotion slice** — apply delta to `docs/specifications/` per the
   chosen strategy; update the spec's `## Plans` / `## Related Plans`
   backlink section; record the promotion baseline identifier
4. **Slices 1…N** — code, tests, implementation notes against the
   **promoted** spec
5. **Deviation handling** — if reality disagrees with promoted text:
   deviation log row, explicit spec edit slice, continue against revised spec
6. **Final slice: traceability reconciliation** — backlinks, implementation
   snapshot/status/mapping notes, lessons/runbooks; close link debt (below)

Do **not** make "copy appendix into spec" the **last** slice by default.
Promotion belongs early — before code cites new spec sections — so later
tasks are judged against one governing spec. Name the slice
**spec-promotion slice**; if the repo already assigns a meaning to
"slice 0" (for example an acceptance-probe suite), do not reuse that name.

#### Two status systems (do not conflate)

A spec change can be staged by **two different mechanisms**, and they must
not be conflated:

- **Prose `Status:` header or planned-companion prose** on a spec file —
  governs human/agent **adoption**: whether the spec may be implemented at
  all. backstitch does **not** read prose status.
- **Machine classification** — backstitch's per-file glob rungs
  (`planned_spec_globs`, `exploratory_spec_globs`). In weft, planned specs
  follow the `*A-*.md` filename convention in `docs/specifications/` (for
  example `03A-Manager_Architecture_Planned.md`). Classification governs
  what the scanner reports when shipped code cites the file
  (`CODE_REF_PLANNED_SPEC` / `CODE_REF_EXPLORATORY_SPEC` warnings).
  Classification is per-**file**: you cannot stage one paragraph of an
  active file by reclassifying the whole file without downgrading every
  section in it.

Plans must say which mechanism they use, and must not assume a
planned/exploratory classification exists for a new path without naming the
glob or filename-convention change that creates it.

#### Promotion strategies (pick one in the plan)

**A — In-file edit, text before link claims (default for paragraph edits):**
In the spec-promotion slice, land requirement text in the existing spec file
**without** claiming implementation links (`_Implementation mapping_` blocks,
"implemented by" pointers). Unlinked new text is at worst info-class debt to
backstitch (`SPEC_SECTION_UNMAPPED`). In a later slice, add the link claims,
the code, and the reciprocal `Spec:` backlink **together**. Slices between
promotion and that linking slice must not cite the new sections from code —
a code backlink to a section that claims no implementation yet is
warning-class debt (`SPEC_MAPPING_RECIPROCAL_MISSING`), and a code citation
of a section not yet landed in `docs/specifications/` is error-class
(`SPEC_SECTION_MISSING`).

**B — Atomic:** Land requirement text, link claims, code, and reciprocal
backlinks in **one** change. Promotion and implementation are the same
slice; use when the delta is small or the team prefers a single landing.

**C — New file under an in-flight classification:** Add a **new** spec file
classified planned/exploratory (in weft, a `*A-*.md` planned companion, or a
glob change if the path is new). Shipped code may cite it with
warning-class debt (`CODE_REF_PLANNED_SPEC`) until graduation. Use for
substantial new behavior, not a paragraph inside an existing active file.

**D — Spec-authoring / clarification only:** No code cites new behavior;
land text as active (or a prose status update for whole-spec adoption).
No link claims required unless reciprocity is already claimed.

Do **not** reclassify an existing active spec file as planned/exploratory
just to stage a single paragraph.

#### Two-PR / stacked-commit trap

If an active spec section **with** implementation-mapping claims lands
before the reciprocal code backlink, the repo carries reciprocity debt
(`CODE_BACKLINK_RECIPROCAL_MISSING`) between the two landings. A repository
holding a zero-warning backstitch gate cannot leave that debt on the main
branch. Mitigate with strategy A (no link claims until the code slice) or
strategy B (atomic), and say which in the plan.

#### Graduating an in-flight classification (heavy — not routine)

Moving a spec file from planned/exploratory to active is not a one-line
edit: narrowing a classification pattern affects every file matching it, and
weft's `*A-*.md` filename convention requires renaming the file, which
breaks path-qualified citations (`SPEC_FILE_MISSING`) until backlinks are
updated. Name graduation steps, citation updates, and verification in the
plan.

#### Final slice: traceability reconciliation

The last implementation slice is not "tidy prose." It closes the graph:

- complete `_Implementation mapping_` claims and reciprocal `Spec:`
  backlinks; clear warning-class debt
- graduate in-flight classifications only when strategy C was used and
  graduation steps are named
- rerun the backstitch (or other traceability/self-check) gate named in the
  plan, from the current state, and record the result. Where the repo
  declares that gate mandatory (for example a zero-error, zero-warning
  check), it is not waivable via a "residual-risk budget" — residual risk
  documents blockers, it does not redefine done
- update the promotion baseline identifier in plan closeout if the spec
  moved again

### 5. Tasks

Tasks should be dependency-ordered and normally executed in sequence unless the
plan explicitly says that two tasks are independent.

Use a numbered, dependency-ordered checklist. Each task should be small enough
to implement and verify independently.

For each task, include enough detail that the implementer does not need to
rediscover the approach. At minimum, cover:

- outcome of the task
- exact files to touch
- files/docs to read before editing
- local code style rules that matter for the touched files
- shared helpers or patterns to reuse
- abstractions, dependencies, or refactors that are explicitly not allowed
- tests to add or update
- stop-and-re-evaluate gates when the work starts drifting
- per-task verification or "done" signal

When relevant, also say:

- whether the task is wrapper logic or core work
- whether rollback depends on the task remaining backward-compatible
- whether the task touches a one-way door or rollout-sensitive edge

Be explicit about approach. Prefer:

```text
3. Extend `TaskRunner` to emit the new field.
   - Files to touch: `weft/core/tasks/runner.py`, `tests/tasks/test_runner.py`
   - Read first: `docs/specifications/01-Core_Components.md [CC-3]`
   - Update the existing result path in `weft/core/tasks/runner.py`
   - Reuse the current outbox writer; do not create a parallel emitter
   - Verify with `uv run pytest tests/tasks/test_runner.py -q`
```

Not:

```text
3. Add the new result behavior
```

Recommended task shape:

```text
2. Tighten manager startup registry validation.
   - Outcome: startup waits for the caller's own registry record and no longer
     fails under concurrent launch.
   - Files to touch:
     - weft/core/manager.py
     - tests/core/test_manager.py
   - Read first:
     - docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]
     - docs/specifications/07-System_Invariants.md [MANAGER.3], [MANAGER.4]
     - existing manager startup tests
   - Reuse:
     - existing registry payload helpers
     - existing launch/wait path in `weft/cli/run.py`
   - Constraints:
     - no second startup path
     - preserve one-manager-per-context behavior
   - Tests:
     - add a regression covering concurrent starters
     - update the existing success-path startup test
   - Stop if:
     - the change wants a second startup path
     - the registry contract needs to change beyond the plan
   - Done when:
     - the new regression passes
     - existing startup tests stay green
```

### 6. Testing Plan

Every plan must say what to test and how.

Include:

- which fixture or harness to use
- which test file(s) to update or add
- which commands to run
- what observable behavior should prove the change
- which invariants each test is protecting
- what should not be mocked
- what failure or regression should be proven red before the implementation
  when red-green TDD is practical
- what edge case would be tempting to skip and why it is in or out of scope

Weft-specific guidance:

- Use `WeftTestHarness` for CLI, manager, and lifecycle behavior.
- Use `broker_env` or real `Queue` instances for queue semantics.
- Avoid patch-heavy tests for core broker/process behavior.
- Prefer red-green TDD when the problem can be expressed cleanly as a failing
  test first.
- If red-green TDD is not practical, say what the smallest equivalent proof is
  and why the failing-test-first path is not worth it for this slice.
- If the change reads append-only queues such as `weft.log.tasks` or registry
  state, specify whether the implementation must use generator-based helpers.
- If the change depends on terminal completion, say whether verification should
  allow for the completion-event/outbox grace window.
- For risky work, name any post-deploy or runtime-observation signal that
  matters in addition to local tests.

Good test design guidance:

- Test observable behavior, not private implementation details, unless the
  private seam is the behavior under review.
- Prefer one realistic end-to-end test over several shallow mock-heavy tests
  for queue/process behavior.
- Mock only boundaries that are external, slow, or nondeterministic.
- Name the exact regression being protected so the implementer does not add a
  vague "happy path" test and stop there.
- When an implementer reaches for mocks around queues, process lifecycle,
  state transitions, reservations, or result delivery, the plan should make
  that a stop-and-re-evaluate moment unless the mock is limited to a truly
  external boundary.

### 7. Verification and Gates

List the exact commands to run and what success looks like.

Example:

```bash
uv run pytest tests/core/test_manager.py -q
uv run pytest tests/commands/test_run.py -q
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft
```

Every plan should distinguish:

- per-task verification, used while implementing bite-sized steps
- final gates, used before claiming the work is done

If only a narrow slice needs to run, say so explicitly. If full-suite gates are
required because the blast radius is large, say that too.

For rollout-sensitive work, also say:

- how success will be observed after deploy or runtime exercise
- what rollout sequencing matters
- what rollback path exists

### 8. Independent Review Loop

Every risky plan should say how independent review will happen.

At minimum, include:

- which other agent or agent family should review the plan
- which files and docs the reviewer should read — including
  **`## Proposed Spec Delta`** when the plan changes intended behavior
- the review prompt or stance
- how feedback will be handed back to the author

Recommended prompt:

> Read the plan at [path] and its `## Proposed Spec Delta` (if present),
> including the named promotion strategy. Carefully examine the plan, the
> proposed spec text, and the associated code. Look for errors, bad ideas, and
> latent ambiguities. Don't do any implementation, but answer carefully: Could
> you implement this confidently and correctly against the delta as promoted,
> if asked?

The author must then consider each review point explicitly and either update
the plan, explain why the current path is still the best choice, or record why
the point is out of scope.

### 9. Out of Scope

For larger changes, state what is explicitly not changing. This reduces scope
creep during implementation.

### 10. Fresh-Eyes Review

Before considering the plan complete, re-read it as if you are a new engineer
who knows Python but not Weft.

Check for:

- missing file paths
- ambiguous phrases like "update the flow" or "adjust the logic"
- unstated invariants
- missing test harness or verification commands
- tasks that are too large to review safely
- hidden assumptions about local style or tool behavior
- accidental drift away from the user's requested direction

Fix those gaps and re-read the plan again.

If tightening the plan would require changing the scope, architecture, or
direction in a material way, stop and report that instead of quietly rewriting
the task into something else.

## Plan Hardening Checklist

Use `hardening-plans.md` as the canonical full checklist for risky work. Do
not treat this section as a second competing source of truth.

Before treating a risky plan as review-ready, confirm that the canonical
hardening checklist has been applied and that the plan clearly includes:

- invariants and hidden couplings before the tasks
- anti-mocking guidance plus contract-focused proof
- current-structure context, rollback, rollout, and one-way doors
- required reading with comprehension questions when the boundary is complex

## Blockers Before Implementation

Do not start risky implementation work if the plan is missing any of these:

- invariants that say what must not change
- enough current-structure context to find the right edit point
- anti-mocking guidance for the important proof
- rollback or rollout notes when order or compatibility matters
- an independent review loop

For **spec-changing implementation** plans, also do not start **code**
slices until:

- `## Spec Baseline` and `## Proposed Spec Delta` exist
- independent review of the delta has completed
- the **spec-promotion slice** has landed in the worktree (or the plan is
  typed **spec-authoring**, where spec landing is the work)
- the promotion baseline **identifier** is recorded (commit SHA or diff base
  + worktree state — not necessarily a commit)
- the promotion **strategy** (A/B/C/D) and gate-preservation plan are
  explicit

## Backlink Rule

When a plan implements a spec in `docs/specifications/`, add a backlink in that
spec under `## Plans` or `## Related Plans`. Append to an existing section if
one already exists.

When the touched spec already contains nearby implementation notes such as
`_Implementation snapshot_`, `_Implementation status_`, or
`_Implementation mapping_`, update those notes in the same change so the spec
continues to describe current ownership and behavior accurately.

## Anti-Patterns

- "Update the manager" without naming the file, path, or invariant involved.
- Citing only a whole spec document when a smaller section/code reference is
  available.
- Assuming the implementer will know Weft-specific helpers, fixtures, or style
  rules without being told.
- Tasks that bundle several unrelated edits into one "step".
- "Add tests" without naming the harness, assertion, or command.
- Plans that rely on mocks for core queue/process behavior.
- Mocking Weft queue/process behavior when a real harness or broker-backed test
  is practical.
- A plan that leaves future-proofing or abstraction decisions up to the
  implementer.
- Over-scoping with unrelated cleanup or refactors.
- Introducing a second execution path without first proving the current path is
  insufficient.
- Risky work without an independent review loop.
- Spec-changing work without `## Proposed Spec Delta` or a promotion baseline
  identifier.
- Implementing code that cites spec paths before the spec-promotion slice
  lands.
- Treating plan appendix text as the governing contract after promotion.
- Reclassifying an active spec file as planned/exploratory to stage one
  section (classification is per-file).
- Landing actively-mapped spec sections before reciprocal code when the repo
  holds a zero-warning backstitch gate (use strategy A or B instead).
- Waiving a mandatory traceability gate via a "residual-risk budget".
