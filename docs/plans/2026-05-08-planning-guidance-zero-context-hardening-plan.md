# Planning Guidance Zero-Context Hardening Plan

Status: completed
Source specs: None - repo planning guidance / agent workflow update
Superseded by: none

## 1. Goal

Tighten Weft's planning guidance so every implementation plan is usable by a
skilled engineer who has zero Weft context, weak local taste, and a tendency to
over-mock tests. The guidance should require complete file-level instructions,
repo style context, bite-sized tasks, explicit invariants and gates, and test
plans that prefer red-green TDD and observable behavior over mock-heavy probes.

## 2. Source Documents

There is no product behavior spec for this change. The governing documents are
agent workflow docs:

- `AGENTS.md`, especially Project Conventions, Design Philosophy, How to Work,
  Test Design, and Handoff Protocol.
- `docs/agent-context/README.md`.
- `docs/agent-context/decision-hierarchy.md`.
- `docs/agent-context/principles.md`.
- `docs/agent-context/engineering-principles.md`.
- `docs/agent-context/runbooks/writing-plans.md`.
- `docs/agent-context/runbooks/hardening-plans.md`.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.
- `docs/agent-context/runbooks/testing-patterns.md`.
- `docs/plans/README.md`.

No spec backlink is required because this is guidance-only and does not change
Weft runtime behavior.

Placement note: `docs/plans/README.md` normally says process-only plans should
not remain in the curated plan corpus. This file is intentionally here because
the current user instruction explicitly requested the full plan in `docs/plans/`
for this guidance update. During future curation, treat it as the execution
record for repo tooling/guidance that still exists, not as a product-behavior
spec.

## 3. Context and Key Files

Files to modify:

- `docs/agent-context/runbooks/writing-plans.md`
  - Main planning runbook. It already required zero-context plans, file lists,
    invariants, anti-mocking guidance, DRY, YAGNI, red-green TDD, and fresh-eyes
    review. The update should strengthen the assumptions around questionable
    judgment, poor test design, normalized metadata, and indexing without
    duplicating the whole runbook.
- `docs/plans/2026-05-08-planning-guidance-zero-context-hardening-plan.md`
  - Execution record for this guidance update.
- `docs/plans/README.md`
  - Plan corpus index. Add this plan and update the plan count.
- `docs/agent-context/context.index.yaml`
  - Agent-context hub index. The runbook inventory is unchanged, but the
    `updated_at` field should reflect that canonical guidance changed.

Files to read first:

- `docs/agent-context/runbooks/writing-plans.md`
  - Current source of truth for plan authoring.
- `docs/agent-context/runbooks/testing-patterns.md`
  - Current source of truth for real harness selection and anti-over-mocking
    guidance.
- `docs/agent-context/runbooks/hardening-plans.md`
  - Current source of truth for risky-plan hardening.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
  - Current source of truth for independent review loops.
- `docs/plans/README.md`
  - Index format and plan status taxonomy.

Style and guidance:

- Keep guidance operational and repo-specific.
- Avoid duplicating long sections that already exist in another runbook.
- Use ASCII only.
- Do not add new process tooling, new templates, or new enforcement scripts for
  this slice.
- Preserve the existing decision hierarchy: specs define product behavior;
  plans guide execution.

Current structure:

```text
AGENTS.md
  -> docs/agent-context/README.md
       -> decision-hierarchy.md
       -> principles.md
       -> engineering-principles.md
       -> runbooks/writing-plans.md
       -> runbooks/testing-patterns.md
       -> runbooks/hardening-plans.md
       -> runbooks/review-loops-and-agent-bootstrap.md
```

The planning guidance already covers most of the requested behavior. The core
risk is over-editing and creating a second, competing planning standard.

## 4. Invariants and Constraints

- Specs remain the source of truth for product behavior. This plan must not
  imply that planning docs can override `docs/specifications/`.
- Plans stay non-normative for product behavior, except that a current approved
  plan may guide active-slice execution.
- `docs/agent-context/runbooks/writing-plans.md` remains the canonical planning
  runbook. Do not create a competing plan template elsewhere.
- Testing guidance must continue to prefer `WeftTestHarness`, `broker_env`, and
  real queue/process paths for lifecycle behavior.
- Do not introduce new dependencies, scripts, CI gates, or repo config.
- Keep the change documentation-only. No runtime, CLI, queue, or test behavior
  changes are in scope.
- Keep the diff small enough that future agents can read the strengthened rules
  without scanning a duplicated essay.

Review gates:

- The updated guidance must explicitly include zero context, questionable
  judgment/taste, weak test design, DRY, YAGNI, red-green TDD, bite-sized tasks,
  file-level implementation instructions, invariants, gates, and anti-mocking
  expectations.
- The updated guidance must explicitly include the normalized plan metadata
  block and `docs/plans/README.md` indexing requirements enforced by
  `tests/specs/test_plan_metadata.py`.
- The plan must be indexed in `docs/plans/README.md`.
- Fresh-eyes review must find no latent ambiguity that would let an implementer
  choose the wrong file, abstraction, or test seam.
- `/plan-eng-review` from gstack must be applied. Any accepted findings must be
  reflected in the docs before completion.

## 5. Tasks

1. Confirm the current planning guidance and identify only real gaps.
   - Outcome: know whether the repo needs a rewrite or a targeted hardening.
   - Files to read:
     - `docs/agent-context/runbooks/writing-plans.md`
     - `docs/agent-context/runbooks/testing-patterns.md`
     - `docs/agent-context/runbooks/hardening-plans.md`
     - `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
   - Done when:
     - The existing coverage and missing emphasis are clear.
   - Stop if:
     - The guidance already fully covers the requested behavior and a new plan
       would only duplicate it. In that case, report that instead of creating
       process noise.

2. Strengthen `writing-plans.md` in place.
   - Outcome: the canonical planning runbook explicitly assumes questionable
     local judgment and weak test design, and it requires concrete proof around
     DRY, YAGNI, red-green TDD, anti-mocking guidance, metadata, and indexing.
   - Files to touch:
     - `docs/agent-context/runbooks/writing-plans.md`
   - Approach:
     - Add short bullets to the existing Audience Assumptions, Planning
       Standard, File Placement, Tasks, and Testing Plan sections.
     - Reuse existing sections. Do not add a competing template.
   - Tests:
     - Documentation-only; no unit tests.
   - Done when:
     - A zero-context reader sees exactly what to document in each plan and
       where mock-heavy tests are unacceptable.
   - Stop if:
     - The edit starts changing planning hierarchy or product behavior.

3. Write this execution plan.
   - Outcome: the planning guidance update itself is documented in
     `docs/plans/` with the same standards it asks future plans to meet.
   - Files to touch:
     - `docs/plans/2026-05-08-planning-guidance-zero-context-hardening-plan.md`
   - Approach:
     - State that no product spec exists.
     - List exact files, invariants, tasks, test guidance, fresh-eyes review,
       and review loop.
     - Use the exact normalized metadata key `Source specs`, not a singular
       variant.
   - Tests:
     - Documentation-only; verify by reading the plan against
       `writing-plans.md`.
   - Done when:
     - The plan is implementable as written and does not send an implementer
       into runtime code.

4. Update the plan corpus index.
   - Outcome: `docs/plans/README.md` includes this plan and the correct count.
   - Files to touch:
     - `docs/plans/README.md`
   - Approach:
     - Increment the count by one.
     - Add a dated row near the other `2026-05-08` plan.
   - Done when:
     - The index row links to this file and marks the status `completed`.

5. Update the agent-context index timestamp.
   - Outcome: `docs/agent-context/context.index.yaml` reflects that canonical
     agent guidance changed on this date.
   - Files to touch:
     - `docs/agent-context/context.index.yaml`
   - Approach:
     - Update only `updated_at`; do not change read order or document inventory
       because no runbook was added or removed.
   - Done when:
     - `updated_at` is `2026-05-08`.

6. Perform fresh-eyes review and fix issues.
   - Outcome: the plan and runbook can be used by a skilled but cold engineer
     without hidden assumptions.
   - Review questions:
     - Does every task name exact files to touch?
     - Is it clear that this is documentation-only?
     - Is there any phrase like "tighten guidance" without saying where and
       how?
     - Are tests, verification, invariants, and gates named?
     - Could an implementer wrongly add scripts, config, CI, or runtime code?
   - Done when:
     - Any ambiguity found in the first pass is fixed, and a second pass finds
       no blocker.

7. Run gstack `plan-eng-review` and apply accepted feedback.
   - Outcome: an engineering-plan review has challenged scope, architecture,
     code quality, tests, and performance.
   - Files/docs for review:
     - This plan.
     - `docs/agent-context/runbooks/writing-plans.md`.
     - `docs/agent-context/runbooks/testing-patterns.md`.
     - `docs/plans/README.md`.
   - Review stance:
     - Look for poor choices and latent ambiguities.
     - Ask whether a cold implementer could apply the guidance correctly as
       written.
   - Done when:
     - Findings are either fixed or rejected with a documented reason.

8. Decide whether a second independent agent review is required.
   - Outcome: avoid unnecessary review theater while still following the repo
     risk policy.
   - Risk evaluation:
     - This change is documentation-only and does not alter runtime behavior,
       public CLI shape, queue contracts, TaskSpec fields, persistence, or CI.
     - It does affect durable agent guidance, so it deserves the requested
       self-review and gstack engineering review.
     - It is not a high-risk implementation under `hardening-plans.md` because
       it does not touch execution, contracts, persistence, cleanup, or a
       one-way door.
   - Required action:
     - If review uncovers a material scope change or guidance conflict, reassess
       risk and run a second agent review.
     - If the change remains documentation-only and aligned with existing
       guidance, record that a second provider review is not required.

## 6. Testing Plan

This is a documentation-only update. No Python test should be added because no
runtime behavior changes.

Verification still needs concrete checks:

- Read `docs/agent-context/runbooks/writing-plans.md` and confirm it explicitly
  covers:
  - zero-context implementers
  - questionable taste / weak local judgment
  - weak test design and over-mocking risk
  - exact files to touch and read first
  - local style guidance
  - DRY through existing helpers, not new abstractions
  - YAGNI and no future-proofing
  - red-green TDD or a named equivalent proof
  - bite-sized tasks
  - invariants and review gates
  - exact `Status`, `Source specs`, and `Superseded by` metadata
  - `docs/plans/README.md` indexing with matching status
- Read this plan and confirm it follows the updated guidance.
- Run a text search for the new phrases to verify the guidance landed in the
  canonical runbook:

```bash
rg -n "questionable|over-mock|red-green|YAGNI|DRY|test seam" docs/agent-context/runbooks/writing-plans.md
rg -n "Status: draft|Source specs:|Superseded by:|test_plan_metadata|docs/plans/README.md" docs/agent-context/runbooks/writing-plans.md
```

Final documentation gates:

```bash
git diff -- docs/agent-context/runbooks/writing-plans.md docs/agent-context/context.index.yaml docs/plans/2026-05-08-planning-guidance-zero-context-hardening-plan.md docs/plans/README.md
rg -n "planning-guidance-zero-context-hardening" docs/plans/README.md
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

No `pytest`, `mypy`, or `ruff` run is required for this slice. Running them
would not verify the changed behavior. The plan metadata guardrail is relevant
and should pass.

## 7. Verification and Gates

Per-task verification:

- Task 1: source docs read and scope confirmed.
- Task 2: `writing-plans.md` strengthened without duplicating another runbook.
- Task 3: plan created under `docs/plans/` with exact file and gate guidance.
- Task 4: plan count and index row updated.
- Task 5: agent-context index timestamp updated without changing inventory.
- Task 6: fresh-eyes review completed twice, with fixes applied before the
  second pass.
- Task 7: gstack engineering review completed and feedback considered.
- Task 8: risk decision recorded.

Final gates:

- `rg -n "questionable|over-mock|red-green|YAGNI|DRY|test seam" docs/agent-context/runbooks/writing-plans.md`
- `rg -n "Status: draft|Source specs:|Superseded by:|test_plan_metadata|docs/plans/README.md" docs/agent-context/runbooks/writing-plans.md`
- `rg -n "planning-guidance-zero-context-hardening" docs/plans/README.md`
- `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`
- Manual fresh-eyes review against `docs/agent-context/runbooks/writing-plans.md`
  section 10.
- gstack `plan-eng-review` pass with no unresolved blocker.

## 8. Independent Review Loop

Use gstack `plan-eng-review` after the first fresh-eyes pass.

Review prompt:

> Read `docs/plans/2026-05-08-planning-guidance-zero-context-hardening-plan.md`
> and `docs/agent-context/runbooks/writing-plans.md`. Look for errors, bad
> ideas, missing gates, and latent ambiguities. Do not implement anything.
> Could a skilled engineer with zero Weft context apply this guidance
> confidently and correctly as written?

Feedback handling:

- Accepted findings must be patched into either this plan or
  `writing-plans.md`.
- Rejected findings must be recorded in the final handoff with the reason.
- If the review finds that the plan implies a materially different direction,
  stop and report rather than rewriting the change into something broader.

Second-agent review:

- Not required unless the risk evaluation changes. The current slice is
  documentation-only and avoids all high-risk triggers in
  `docs/agent-context/runbooks/hardening-plans.md`.

## 9. Out of Scope

- Runtime, CLI, queue, manager, TaskSpec, or test behavior changes.
- New enforcement scripts, markdown linters, CI checks, or templates.
- Rewriting `hardening-plans.md`, `testing-patterns.md`, or
  `review-loops-and-agent-bootstrap.md` beyond what this slice requires.
- Re-indexing or curating historical plans beyond adding this one row.
- Changing the decision hierarchy between specs, plans, and code.

## 10. Fresh-Eyes Review

First pass findings:

- The original runbook already covered most requested behavior, so a rewrite
  would create duplication and process drift. Fix: strengthen the existing
  sections in place and document that choice here.
- `docs/plans/README.md` normally warns against retaining process-only plans,
  while the current task explicitly asks for this plan in `docs/plans/`. Fix:
  add a placement note so future curation can preserve or remove it knowingly
  instead of treating it as an accidental index entry.
- `docs/agent-context/context.index.yaml` was not listed at first. Fix: update
  its `updated_at` field and include it in this plan, while leaving read order
  and runbook inventory untouched.
- Plan metadata and indexing guardrails were implied by `docs/plans/README.md`
  but not explicit in the planning guidance, and this plan initially used
  `Source spec` instead of the required `Source specs`. Fix: add the exact
  metadata block and index rules to `writing-plans.md`, correct this plan's
  metadata, and run `tests/specs/test_plan_metadata.py`.
- "Test design" could still be read as "add tests somewhere." Fix: require the
  contract, fixture, queue/process evidence, red proof, and what not to mock.
- "Questionable taste" was implied but not explicit. Fix: add it directly to
  the audience assumptions.

Second pass findings:

- The plan is explicit that the change is documentation-only.
- Every task names files, approach, tests or doc verification, done signal, and
  stop conditions.
- The risk decision has a clear trigger for escalation if review changes the
  scope.

No remaining ambiguity requires changing direction.
