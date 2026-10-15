# Crystallize Program Theory

Status: Active — adopted from agent-theory @ `8f41a9d` and updated to
`296a7cd` via `docs/plans/2026-10-14-agent-theory-delta-wave-plan.md`.

## Purpose

Distill the live mental model of the program — its core concepts, desired feel
("taste up front"), authority and ownership boundaries, key invariants, refused
alternatives, and open uncertainties — into `docs/program-theory.md` and, where
warranted, local module theory notes. This skill turns scattered explanations
from plans, reviews, specs, and conversations into a concise, citation-backed
account that guides product-scope judgment and prevents design drift.

## When To Use

- After a major architectural slice, design exploration, or audit that clarified
  or changed the system's conceptual model, ownership boundaries, or refused
  alternatives.
- When an agent or reviewer misjudges product scope because `docs/program-theory.md`
  was silent or stale on a boundary that specs and code already assume.
- During an authorized coalescing sweep when cold lessons or retired plans carry
  durable architectural rationale that belongs in the program theory rather than
  a process rule.
- Do NOT use mid-task without authorization when the edit would change normative
  product scope or reverse a standing refused alternative (`[ALT-WT-*]`): surface
  `RECONSIDERATION REQUESTED` for human decision first.

## Governing References

- `docs/program-theory.md` (this repository's canonical program theory)
- `docs/agent-context/decision-hierarchy.md` (authority order, `[DOM-15]`
  classification, and preflight check)
- `docs/agent-context/runbooks/writing-plans.md` (§4b–4d for spec-changing work)
- `docs/specifications/README.md` (specification corpus structure and traceability)

## Read First

- `docs/program-theory.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/specifications/README.md`
- the governing files in `docs/specifications/` and active or recently completed
  plans in `docs/plans/` for the touched boundary

## Blast Radius

- `docs/program-theory.md` (`[WT-*]` and `[ALT-WT-*]` sections)
- module-level docstrings or local `README.md` theory notes in `weft/` when a
  subsystem boundary needs progressive-disclosure context
- `docs/specifications/` when crystallizing the theory reveals an undocumented
  product contract that requires a spec delta

## Workflow

1. **Identify the conceptual gap or drift.** State in one sentence what question
   about system purpose, desired feel ("taste up front" — what makes a surface
   feel native to Weft vs. bolted on), authority, ownership, or refused design
   shapes a reader cannot currently answer from `docs/program-theory.md` alone.
2. **Gather the evidence from the live tree.** Read the governing spec sections
   in `docs/specifications/`, the load-bearing modules in `weft/`, and the plans
   or review logs that established the boundary. Every claim in
   `docs/program-theory.md` must trace to shipped code, a governing spec, or an
   explicit owner decision — never to unverified inference.
3. **Apply the four-part admission test to refused alternatives (`[ALT-WT-*]`).**
   Record a rejected or refused alternative when at least one holds:
   - **Likely recurrence:** future sessions or reviewers are likely to propose it
     again as an "obvious" simplification or feature.
   - **Material investigation cost:** re-evaluating why it fails costs significant
     time or experiment budget.
   - **Hidden constraint exposed:** the refusal rests on a non-obvious invariant
     (for example, spawn-only multiprocess safety, queue-truth vs auxiliary cache,
     or SQLite/Windows handle lifetime).
   - **Harm from blind retry:** trying the alternative silently corrupts state,
     weakens an invariant, or pulls Weft above the substrate layer.
   For each admitted `[ALT-WT-*]` entry, state what was refused, the concrete
   mechanism that makes it wrong here, and what condition (if any) would justify
   reopening it.
4. **Keep the theory distinct from the spec and implementation narration.**
   - `docs/program-theory.md` explains *why the system is shaped this way*, what
     using it should feel like (taste up front, not big design up front —
     `[REV-AT-005]`), what each boundary owns, and what it refuses to become.
   - `docs/specifications/` owns exact wire formats, state tables, CLI flags, and
     normative behavior.
   - Module docstrings own local realization details. If a subsystem has a non-obvious
     local model (for example, `weft/core/manager.py` or
     `weft/core/monitor/task_monitor.py`), add a concise module-theory note at
     the top of that module pointing back to the governing `[WT-*]` and spec IDs.
5. **Verify identifiers and run gates.** Check that every cited file path,
   symbol, spec code, and `[WT-*]` / `[ALT-WT-*]` identifier resolves, and run
   `bin/check-doc-paths` and `tests/specs/test_spec_hygiene.py`.

## Output Standard

- `docs/program-theory.md` updated with concise, code- and spec-grounded `[WT-*]`
  or `[ALT-WT-*]` entries
- Any new or revised refused alternative states its mechanism and reopening
  condition
- All backticked repository paths and spec references pass `bin/check-doc-paths`
  and `tests/specs/`

## Maintenance Notes

- If `docs/program-theory.md` starts duplicating field-by-field spec tables,
  prune the duplication back to the conceptual boundary and link the spec
  section instead.
- If a review or debugging session repeatedly hits the same unstated assumption
  in a subsystem, add a module-theory header to that module and link it from
  `docs/program-theory.md`.
