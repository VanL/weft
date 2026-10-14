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

Start with the conceptual account, then process. This list, root
`AGENTS.md`, and `context.index.yaml` `read_order` name the same
sequence element-for-element.

1. `../program-theory.md` — the conceptual account of Weft ("weftness"),
   including rejected alternatives; frames placement and refusal, and does not
   override winning contracts. Extend local depth with **module theory** on
   entry, not by bulking this file.
2. This file — `README.md` (the hub overview you are reading).
3. `decision-hierarchy.md`
4. `principles.md`
5. `engineering-principles.md`
6. Relevant runbook(s) in `runbooks/`
7. `lessons.md`
8. `../lessons.md` — required startup reading is the **Golden Rules section
   plus dated sections after the lessons watermark** (see `../coalescing.md`).
   Older sections are searchable reference material, not startup context.

Machine-readable order: `context.index.yaml` `read_order`.

Read-order compliance is a declared-claim floor, not a gate: when you produce
product-scope judgment — a plan, review, audit, or design opinion — declare
which of these surfaces you consulted. Plans do this via their
source-documents section; plan-free work declares it in its report. The
declaration is checked by review, like task classification ([DOM-15]), not
by tooling.

## Runbooks

- `external-skill-suites.md`: precedence and crosswalk for external skill
  suites (superpowers, gstack, Every's compound engineering)
- `runtime-and-context-patterns.md` — Context discovery, queue access,
  generator-based history reads, TaskSpec boundary rules, and runtime-only
  queue semantics.
- `testing-patterns.md` — Test harness selection, regression strategy, and
  common broker-heavy testing traps.
- `writing-plans.md` — How to write implementation plans that a zero-context
  engineer can execute safely, with exact spec-section traceability
  (including spec baseline, proposed spec delta, promotion slices, and
  status mechanisms).
- `hardening-plans.md` — How to tighten risky plans so they say what must not
  change, where the hidden couplings are, and how rollback/rollout will work.
- `review-loops-and-agent-bootstrap.md` — How to choose independent reviewers,
  run plan/work reviews, and feed findings back into the active plan.
- `adversarial-acceptance-probes.md` — Black-box invariant-floor probes for
  integration readiness: exit-code honesty, no user-facing tracebacks,
  per-task failure containment, and firing tests for declared contracts.
- `designing-agent-facing-interfaces.md` — Principles for designing APIs,
  CLIs, and docs that agents consume.

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
- When `docs/coalescing.md` shows a tripped threshold, report it and
  respond per `docs/coalescing.md` and `skills/coalescing/SKILL.md`: a
  checked-deferred line with derived counts, or a full sweep (its own unit
  of work) on user request, at twice the threshold, or at a completion
  boundary. The session-start check itself is read-only.
