# Agent Context Hub

This folder is the canonical shared context for coding agents working in this
repository (Codex, Claude, Cursor-style agents, and automation agents).

## Goals

- Keep one source of truth for durable execution standards.
- Reduce drift across agent-specific prompt files.
- Make planning, testing, and verification expectations explicit.

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
  engineer can execute safely.

## What Belongs Here

- Durable decision policies and quality bars.
- Reusable runbooks for recurring engineering workflows.
- Short pointers to the canonical lessons ledger.

## What Does Not Belong Here

- Product and architecture specs in `docs/specifications/`.
- One-off execution notes for a single change.
- Tool-specific syntax that is not generally reusable across agents.

## Maintenance Rules

- Keep files short, operational, and repo-specific.
- Prefer checklists over long prose.
- When a repeated mistake shows up, add a short lesson in `docs/lessons.md`
  and strengthen a runbook if the fix should become reusable guidance.
