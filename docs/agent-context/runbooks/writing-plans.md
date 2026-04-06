# Writing Implementation Plans

Plans must be detailed enough that a skilled developer with little or no Weft
context can implement them correctly without guesswork.

## Audience Assumptions

- Strong Python engineer, limited Weft or SimpleBroker context.
- Prone to over-abstracting if shared paths are not called out explicitly.
- Prone to over-mocking tests unless the production path is spelled out.
- Will follow the plan literally, including ambiguities.

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

If no spec exists, say so plainly:

```text
Source spec: None — bug fix / refactor / tooling change
```

### 3. Context and Key Files

For each task, list:

- Files to modify.
- Files to read first.
- Shared paths or helpers that must be reused.

Example:

```text
Files to modify:
  - weft/core/manager.py
  - tests/core/test_manager.py

Read first:
  - docs/specifications/05-Message_Flow_and_State.md
  - weft/core/tasks/base.py
  - tests/helpers/weft_harness.py

Shared path — do not duplicate:
  - weft/core/tasks/runner.py handles the monitored subprocess boundary
```

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

### 5. Tasks

Use a numbered, dependency-ordered checklist. Each task should be small enough
to implement and verify independently.

Be explicit about approach. Prefer:

```text
3. Extend `TaskRunner` to emit the new field.
   - Update the existing result path in `weft/core/tasks/runner.py`
   - Reuse the current outbox writer; do not create a parallel emitter
```

Not:

```text
3. Add the new result behavior
```

### 6. Testing Plan

Every plan must say what to test and how.

Include:

- which fixture or harness to use
- which test file(s) to update or add
- which commands to run
- what observable behavior should prove the change

Weft-specific guidance:

- Use `WeftTestHarness` for CLI, manager, and lifecycle behavior.
- Use `broker_env` or real `Queue` instances for queue semantics.
- Avoid patch-heavy tests for core broker/process behavior.
- Prefer red-green TDD when the problem can be expressed cleanly as a failing
  test first.
- If the change reads append-only queues such as `weft.log.tasks` or registry
  state, specify whether the implementation must use generator-based helpers.
- If the change depends on terminal completion, say whether verification should
  allow for the completion-event/outbox grace window.

### 7. Verification

List the exact commands to run and what success looks like.

Example:

```bash
uv run pytest tests/core/test_manager.py -q
uv run pytest tests/commands/test_run.py -q
uv run mypy weft
uv run ruff check weft
```

### 8. Out of Scope

For larger changes, state what is explicitly not changing. This reduces scope
creep during implementation.

## Backlink Rule

When a plan implements a spec in `docs/specifications/`, add a backlink in that
spec under `## Plans` or `## Related Plans`. Append to an existing section if
one already exists.

## Anti-Patterns

- "Update the manager" without naming the file, path, or invariant involved.
- "Add tests" without naming the harness, assertion, or command.
- Plans that rely on mocks for core queue/process behavior.
- Over-scoping with unrelated cleanup or refactors.
- Introducing a second execution path without first proving the current path is
  insufficient.
