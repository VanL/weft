# Agent Context Hub

This folder is the canonical shared context for coding agents working in this
repository (Codex, Claude, Cursor-style agents, and automation agents).

## Goals

- Keep one source of truth for durable execution standards.
- Keep the normative boundary clear: specs are authoritative for behavior;
  plans are non-normative working documents, though the current approved plan
  may guide the active slice.
- Reduce drift across agent-specific prompt files.
- Make planning, testing, review, and verification expectations explicit.
- Make risky-work boundaries, rollback assumptions, and agent-usable guidance
  visible in the normal read path.
- Keep spec, plan, and code traceability bidirectional.

## Read Order

1. `decision-hierarchy.md`
2. `principles.md`
3. `engineering-principles.md`
4. Relevant runbook(s) in `runbooks/`
5. `lessons.md` and `../lessons.md`

## Runbooks

- `runtime-and-context-patterns.md` — Context discovery, queue access,
  generator-based history reads, TaskSpec boundary rules, and runtime-only
  queue semantics.
- `testing-patterns.md` — Test harness selection, regression strategy, and
  common broker-heavy testing traps.
- `writing-plans.md` — How to write implementation plans that a zero-context
  engineer can execute safely, with exact spec-section traceability.
- `hardening-plans.md` — How to tighten risky plans so they say what must not
  change, where the hidden couplings are, and how rollback/rollout will work.
- `review-loops-and-agent-bootstrap.md` — How to choose independent reviewers,
  run plan/work reviews, and feed findings back into the active plan.

## What Belongs Here

- Durable decision policies and quality bars.
- Reusable runbooks for recurring engineering workflows.
- `context.index.yaml` as the machine-readable index for this hub.
- Short pointers to the canonical lessons ledger.

## What Does Not Belong Here

- Product and architecture specs in `docs/specifications/`.
- One-off execution notes for a single change.
- Tool-specific syntax that is not generally reusable across agents.

## Maintenance Rules

- Keep files short, operational, and repo-specific.
- Prefer checklists over long prose.
- Keep the hierarchy explicit:
  `docs/specifications/` is normative; `docs/plans/` may be exploratory,
  partially implemented, or stale.
- Keep `context.index.yaml` synchronized with the real read order and runbook
  inventory.
- When a repeated mistake shows up, add a short lesson in `docs/lessons.md`
  and strengthen a runbook if the fix should become reusable guidance.
- If a plan fails at a boundary or a reviewer cannot implement it confidently,
  tighten the planning runbooks instead of treating the miss as one-off noise.
