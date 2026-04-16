# Agent Context Hardening Uplift

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## Goal

Port the context-doc improvements that fit Weft's current documentation model:
stronger hardening requirements for risky plans, explicit independent-review
loops, clearer agent-usability rules, and tighter session-start guidance for
plan/spec/code traceability.

## Source Documents

Source spec: None — repository process and documentation uplift

Read first:

- `AGENTS.md`
- `docs/agent-context/README.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/specifications/README.md`

## Context and Key Files

Files to modify:

- `AGENTS.md`
- `docs/agent-context/README.md`
- `docs/agent-context/context.index.yaml`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/specifications/README.md`
- `docs/lessons.md`

Files to add:

- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/plans/2026-04-07-agent-context-hardening-uplift.md`

Reuse:

- keep `writing-plans.md` as the primary operational planning entry point
- keep deeper risky-work rewrite criteria in a companion hardening runbook
- preserve Weft's repo-specific queue, TaskSpec, and runtime-boundary rules

## Invariants and Constraints

- This is a docs/process change only; do not alter runtime behavior or tests.
- `docs/specifications/` remains the source of truth for intended Weft
  behavior.
- The new rules should strengthen the current planning workflow, not replace it
  with a more generic repo shape.
- The changes must stay compatible with the current spec-plan-code traceability
  conventions already used in Weft.

## Tasks

Tasks are dependency-ordered and should be completed in sequence.

Implementation status on 2026-04-07:

- tasks 1-4 are complete in the current working tree
- task 5 is the review-and-closeout step for this plan

1. Tighten the root and session-start guidance.
   - Files to touch:
     - `AGENTS.md`
     - `docs/agent-context/README.md`
     - `docs/agent-context/context.index.yaml`
     - `docs/agent-context/principles.md`
     - `docs/agent-context/engineering-principles.md`
   - Outcome:
     - the stronger planning/review expectations are visible in the normal read
       path
   - Tests:
     - confirm `AGENTS.md` mentions the new runbooks, agent-usability rule,
       and independent-review expectation
     - confirm `docs/agent-context/README.md` lists the new runbooks and the
       `context.index.yaml` role
     - confirm `docs/agent-context/principles.md` and
       `engineering-principles.md` surface the new risky-work language

2. Add reusable runbooks for risky-plan hardening and independent review loops.
   - Files to add:
     - `docs/agent-context/runbooks/hardening-plans.md`
     - `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
   - Outcome:
     - risky-work hardening and review workflows live in canonical runbooks
   - Tests:
     - confirm `hardening-plans.md` has mandatory-trigger guidance, explicit
       checklists, and rollback/rollout expectations
     - confirm `review-loops-and-agent-bootstrap.md` has the reviewer bootstrap
       rule, the canonical review prompt, and the feedback loop

3. Strengthen the plan-writing runbook to use the new runbooks as hard gates
   for risky work.
   - Files to touch:
     - `docs/agent-context/runbooks/writing-plans.md`
   - Outcome:
     - plans explicitly name what must not change, when hardening is mandatory,
       and how review feedback must be handled
   - Tests:
     - confirm `writing-plans.md` includes hardening triggers, anti-mocking
       guidance, rollback/rollout expectations, comprehension questions, and an
       independent review loop
     - confirm `writing-plans.md` treats `hardening-plans.md` as the canonical
       full checklist

4. Update the documentation indexes and lessons.
   - Files to touch:
     - `docs/specifications/README.md`
     - `docs/lessons.md`
   - Outcome:
     - the new planning/review expectations are discoverable and preserved as
       durable rules
   - Tests:
     - confirm `context.index.yaml` registers the new runbooks
     - confirm `docs/specifications/README.md` points risky plans at the new
       runbooks
     - confirm `docs/lessons.md` records short reusable planning lessons

5. Run an independent review on the touched guidance slice and answer the
   findings.
   - Files in scope:
     - all touched files above
   - Outcome:
     - the guidance itself is reviewed before completion
   - Tests:
     - record accepted and rejected findings in this plan

## Testing Plan

This is a docs-only change. Verification is by inspection and grep-based
document-quality gates.

Checks:

- the new runbooks exist and are indexed
- `writing-plans.md` points to hardening/review requirements on risky work
- `AGENTS.md`, `principles.md`, and `engineering-principles.md` surface
  agent-usability and independent-review rules
- `docs/specifications/README.md` still aligns with the strengthened planning
  contract

## Verification and Gates

Run:

```bash
find docs/agent-context/runbooks docs/plans -maxdepth 1 -type f | sort
rg -n "hardening-plans|review-loops-and-agent-bootstrap|agent usability|independent review|what must not change|rollback|rollout|what should not be mocked|comprehension question" AGENTS.md docs
rg -n " +$|\t+$|^<<<<<<<|^=======|^>>>>>>>" AGENTS.md docs || true
```

Success looks like:

- the new runbooks appear in the expected indexes
- the strengthened planning/review language appears in the intended files
- the whitespace and merge-marker check prints no matches

## Independent Review Loop

Run an independent review after the root/session-start docs and planning
runbooks form one coherent slice.

Review prompt:

> Read the plan at [path]. Carefully examine the plan and the associated code.
> Look for errors, bad ideas, and latent ambiguities. Don't do any
> implementation, but answer carefully: Could you implement this confidently and
> correctly if asked?

Feedback handling:

- update the docs for accepted findings
- record no-change decisions for rejected findings
- treat a reviewer confidence failure as a blocker

Review result:

- 2026-04-07 Claude review said the uplift could be implemented confidently
  and correctly, with non-blocking findings about duplicate hardening
  checklists, the undocumented `context.index.yaml` role, a missing
  `decision-hierarchy.md` entry in this plan's read-first list, overly vague
  per-task checks, the abbreviated risky-work trigger list in `AGENTS.md`, and
  the ambiguous meaning of "bootstrap" in the review runbook title. Accepted
  and fixed in the touched docs and in this plan.
- The reviewer also noted low-risk duplication between
  `engineering-principles.md` and `hardening-plans.md`; that duplication is
  intentional so the boundary-first rule appears in the normal read path, and
  the runbook remains the deeper canonical checklist.
- No blocker findings remain after the review-driven edits.

## Out of Scope

- changing runtime code or tests
- adding a separate operating-model spec file
- introducing a repo-wide skills system beyond existing Weft docs and runtime
  concepts
