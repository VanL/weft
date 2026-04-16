# Plan-Writing Guidance Hardening Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## Goal

Strengthen the plan-writing guidance so implementation plans are executable by a
skilled engineer with zero Weft context, weak calibration to local style, and a
tendency to over-abstract or over-mock tests unless the plan makes the correct
path explicit. The updated guidance should force plans to name exact spec
sections, exact files, style references, invariants, tests, and verification
gates, while keeping tasks small and reviewable.

## Audience Assumptions

Assume the implementer:

- is a strong Python engineer,
- knows almost nothing about Weft, SimpleBroker, or the local toolchain,
- does not naturally know good local taste for abstraction or test design,
- will choose mock-heavy tests unless the plan directs them to the real broker
  or process path,
- and will follow ambiguous instructions literally.

This plan is complete only if that implementer could execute it without needing
clarifying questions.

## Source Documents

Source spec: None — repository process and documentation-traceability change

Read for current behavior:

- `docs/agent-context/decision-hierarchy.md`
- `AGENTS.md`
- `docs/agent-context/README.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/specifications/README.md`

## Context and Key Files

Files to modify:

- `docs/agent-context/runbooks/writing-plans.md`
- `docs/plans/2026-04-07-spec-plan-code-traceability-plan.md`

Read first:

- `docs/agent-context/decision-hierarchy.md`
- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/specifications/README.md`

Style and guidance to preserve:

- `AGENTS.md` house style and docstring rules
- `docs/agent-context/principles.md` document-traceability rules
- `docs/agent-context/engineering-principles.md` guidance against second code
  paths and mock-heavy lifecycle tests

## Invariants and Constraints

- `docs/specifications/` remains the canonical source of truth for intended
  system behavior.
- The change is process-only; do not alter runtime behavior, CLI behavior, or
  tests.
- Keep the guidance concentrated in the runbook. Do not spread a second,
  overlapping planning policy across more files in this slice.
- Traceability rules must stay concise and operational, not turn into a second
  specification.
- The workflow should stay bidirectional:
  `spec section <-> plan <-> code module/function`.
- If no feature spec exists for a change, the workflow must still allow an
  explicit `Source spec: None` case rather than forcing fake references.
- The guidance must reinforce DRY, YAGNI, and red-green TDD without encouraging
  cargo-cult wording.
- The warning from the user is binding: if review reveals that tightening the
  planning process would materially change the requested direction, stop and
  report that rather than silently expanding scope.

## Tasks

1. Expand the audience assumptions in the runbook.
   - Outcome: the runbook explicitly assumes a zero-context implementer with
     weak calibration to Weft style and test design.
   - Files to touch:
     - `docs/agent-context/runbooks/writing-plans.md`
   - Read first:
     - `AGENTS.md`
     - `docs/agent-context/principles.md`
     - `docs/agent-context/engineering-principles.md`
   - Reuse:
     - traceability wording already present in:
       - `AGENTS.md`
       - `docs/agent-context/principles.md`
       - `docs/specifications/README.md`
   - Constraints:
     - keep the wording direct and operational
     - do not add motivational or fluffy prose
   - Tests / checks:
     - inspect the updated intro and audience section for zero-context,
       over-abstraction, and over-mocking assumptions
   - Done when:
     - the runbook makes those assumptions explicit without needing outside
       interpretation

2. Strengthen the required plan sections so a plan names exact inputs.
   - Outcome: the runbook requires source specs by exact section/code, exact
     files to touch, style references, helpers to reuse, invariants, and review
     gates.
   - Files to touch:
     - `docs/agent-context/runbooks/writing-plans.md`
   - Read first:
     - current `Source Documents`, `Context and Key Files`, and
       `Invariants and Constraints` sections in the runbook
   - Reuse:
     - existing “Source spec: None” convention
     - existing backlink rule
   - Constraints:
     - do not force fake spec references when none exist
     - do not move this process guidance into a new file
   - Tests / checks:
     - inspect the examples in the runbook and confirm they model exact
       section-level references rather than broad doc references
   - Done when:
     - a plan author following the runbook would know to name precise docs,
       files, and invariants up front

3. Make bite-sized task design mandatory.
   - Outcome: the runbook requires plans to break work into small,
     dependency-ordered tasks with per-task files, reuse guidance, tests, and
     done signals.
   - Files to touch:
     - `docs/agent-context/runbooks/writing-plans.md`
   - Read first:
     - the current `Tasks` section
   - Reuse:
     - the existing “prefer this, not that” structure
   - Constraints:
     - keep tasks implementable and reviewable
     - do not replace concrete examples with abstract advice
   - Tests / checks:
     - add or refine a task-shape example that shows outcome, files, read-first
       docs, reuse path, tests, and done condition
   - Done when:
     - the runbook no longer permits “update the logic” style steps

4. Strengthen testing guidance toward real behavior and away from mocks.
   - Outcome: the runbook explicitly requires plans to specify what to test, how
     to test it, which invariants the tests protect, what should not be mocked,
     and when red-green TDD should be used.
   - Files to touch:
     - `docs/agent-context/runbooks/writing-plans.md`
   - Read first:
     - the current `Testing Plan` and `Verification` sections
     - `docs/agent-context/engineering-principles.md`
   - Reuse:
     - existing guidance around `WeftTestHarness`, `broker_env`, and real queue
       tests
   - Constraints:
     - do not pretend every docs/process change needs runtime tests
     - do require exact verification commands and success signals
   - Tests / checks:
     - inspect the final testing section for explicit mention of invariants,
       harnesses, red-green TDD, and anti-mocking guidance
   - Done when:
     - a plan author following the runbook would know how to name both the
       regression and the test seam

5. Add a fresh-eyes review loop and drift guard.
   - Outcome: the runbook tells the plan author to re-read the plan as a new
     engineer, remove ambiguities, and stop if fixing ambiguities would push the
     work in a materially different direction than requested.
   - Files to touch:
     - `docs/agent-context/runbooks/writing-plans.md`
     - `docs/plans/2026-04-07-spec-plan-code-traceability-plan.md`
   - Read first:
     - `docs/agent-context/runbooks/writing-plans.md`
     - `docs/plans/2026-04-07-spec-plan-code-traceability-plan.md`
   - Reuse:
     - existing “Out of Scope” and anti-pattern structure
   - Constraints:
     - the review loop must be specific enough to execute, not just “review it”
   - Tests / checks:
     - re-read this plan after drafting and fix any ambiguous file list,
       missing gate, or scope drift
   - Done when:
     - the runbook and this plan both contain a concrete fresh-eyes review step

6. Review this plan and tighten it until it is executable without follow-up.
   - Outcome: this plan itself models the runbook behavior it is introducing.
   - Files to touch:
     - `docs/plans/2026-04-07-spec-plan-code-traceability-plan.md`
   - Read first:
     - the entire updated runbook draft
     - the entire plan draft
   - Constraints:
     - if review reveals a materially different direction, stop and report that
       instead of editing toward a new project
   - Tests / checks:
     - verify there are no missing file paths, no vague “update” steps, no
       missing success signals, and no hidden assumptions about local tooling
   - Done when:
     - a fresh reader could execute the plan with no ambiguity

## Engineering Approach

- DRY: reuse existing runbook structure and examples rather than creating a new
  parallel planning guide.
- YAGNI: limit this slice to `writing-plans.md` plus the plan file itself unless
  review uncovers a direct contradiction that must be resolved.
- Red-green TDD: for docs/process work, use a review-first analogue:
  identify specific ambiguity or omission, fix it, then re-read to ensure the
  concrete failure mode is gone.
- No drive-by cleanup: do not rewrite other guidance files here unless the
  change is necessary to avoid contradiction.

## Testing Plan

This is a docs-only change. Verification is by inspection and document-quality
gates, not runtime behavior.

Check:

- the plan states `Source spec: None` explicitly
- the runbook assumes a zero-context implementer and calls out weak local taste
  and weak test design as planning hazards
- the runbook requires exact spec-section references, exact files to touch, and
  code-style references
- the runbook requires bite-sized tasks with per-task verification or done
  signals
- the runbook explicitly names DRY, YAGNI, red-green TDD, invariants, and
  anti-mocking guidance
- the runbook includes a concrete fresh-eyes review loop and a stop-and-report
  drift warning

## Verification

Run:

```bash
git diff --check
rg -n "zero context|over-mock|DRY|YAGNI|red-green|Fresh-Eyes Review|what should not be mocked|bite-sized" docs/agent-context/runbooks/writing-plans.md docs/plans/spec-plan-code-traceability-plan.md
```

Success looks like:

- no whitespace or merge-marker issues
- the runbook contains the stronger planning requirements in the expected
  sections
- this plan reflects those requirements instead of staying at a vague outline

## Review Pass

After drafting the first version of this plan:

- read it top to bottom without editing
- mark every place where a zero-context implementer could ask “which file?”,
  “which test?”, “which invariant?”, or “what counts as done?”
- fix those gaps
- repeat once more to confirm the fixes did not introduce scope drift or a new
  ambiguous phrase

If this review suggests a materially different direction than “tighten the
plan-writing guidance”, stop and report that instead of silently widening the
change.

## Out of Scope

- rewriting `AGENTS.md`, `docs/specifications/README.md`, or other guidance
  files unless a direct contradiction blocks this change
- auditing all existing plans for compliance with the stronger runbook
- changing runtime code, tests, or the spec corpus beyond wording needed for
  this planning-guidance slice
