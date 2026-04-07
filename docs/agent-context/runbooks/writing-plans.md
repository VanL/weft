# Writing Implementation Plans

Plans must be detailed enough that a skilled developer with little or no Weft
context can implement them correctly without guesswork.

Write every plan as if the implementer is technically strong but:

- has zero context for the Weft codebase
- knows almost nothing about the local toolset or problem domain
- will make poor local design choices if the plan leaves room for them
- tends to over-mock tests unless the real production path is named
- and will follow ambiguous instructions literally

If the plan is ambiguous, assume the implementer will choose the wrong file,
the wrong abstraction, and the wrong test seam.

For risky or boundary-crossing work, the plan is not review-ready or
implementation-ready until it also satisfies:

- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

## Audience Assumptions

- Strong Python engineer, limited or zero Weft and SimpleBroker context.
- Unfamiliar with repo-specific code style and helper paths unless you point to
  them explicitly.
- Prone to over-abstracting or future-proofing if shared paths are not called
  out explicitly.
- Prone to over-mocking tests unless the production path is spelled out.
- Will follow the plan literally, including ambiguities.

## Planning Standard

Plans are executable documents, not rough notes.

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
- For risky work, write rollback and rollout notes early enough to shape the
  task breakdown.
- Required reading should describe the current structure and load-bearing path,
  not only list file names.

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

- Put plans in `docs/plans/`.
- Prefer descriptive filenames.
- A date prefix is fine for multi-iteration work, but do not rename existing
  historical plans just to enforce a naming convention.

## Required Plan Sections

### 1. Goal

One short paragraph on what is changing and why.

### 2. Source Documents

Link the source spec(s) and any existing plan or README behavior that defines
the desired outcome.

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
  - weft/commands/run.py owns the manager launch/wait path today
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
- shared helpers or patterns to reuse
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
     - docs/specifications/03-Worker_Architecture.md [WA-1], [WA-3]
     - docs/specifications/07-System_Invariants.md [WORKER.3], [WORKER.4]
     - existing manager startup tests
   - Reuse:
     - existing registry payload helpers
     - existing launch/wait path in `weft/commands/run.py`
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

### 7. Verification and Gates

List the exact commands to run and what success looks like.

Example:

```bash
uv run pytest tests/core/test_manager.py -q
uv run pytest tests/commands/test_run.py -q
uv run mypy weft
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
- which files and docs the reviewer should read
- the review prompt or stance
- how feedback will be handed back to the author

Recommended prompt:

> Read the plan at [path]. Carefully examine the plan and the associated code.
> Look for errors, bad ideas, and latent ambiguities. Don't do any
> implementation, but answer carefully: Could you implement this confidently and
> correctly if asked?

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
