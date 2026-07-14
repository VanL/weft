# Brainstorming to Plan

Status: Active (adopted from agent-guidance @ 2f7eff6 via
`docs/plans/2026-07-14-agent-guidance-propagation-plan.md`). Bridges open-ended exploration (e.g.
superpowers:brainstorming, a design conversation, a grilling session) to
this repository's planning contract. Self-sufficient without any external
suite.

## Purpose

Exploration produces decisions; this skill turns them into the artifacts
the operating model can execute: a spec change when intended behavior
moved, and a dated plan when work is non-trivial. Ideas that stay in
conversation die in conversation.

## When To Use

- A brainstorm, design discussion, or requirements exploration has just
  produced direction and the next step is building.
- Do NOT use mid-exploration — premature planning narrows a brainstorm.
  Use it at the moment "what to build" stops moving.

## Governing Spec References

- `docs/agent-context/runbooks/writing-plans.md` (planning standard)
  and `docs/specifications/README.md` (spec corpus rules)
- `docs/agent-context/runbooks/writing-plans.md` (plan shape; §4b–4d for
  spec-changing work; exploration plan type)
- `docs/specifications/README.md` (this repo's spec-writing home)

## Read First

- The governing spec for the affected area, if one exists
- `docs/plans/README.md` status index (is there already an active plan
  touching this area?)

## Blast Radius

- `docs/plans/` (new dated plan), possibly `docs/specifications/` (spec delta),
  and the plan status index

## Workflow

1. **Harvest the decisions.** From the exploration, write down: what was
   decided, what was explicitly rejected (and why), and what remains
   open. Rejected alternatives are load-bearing — they stop the next
   session from relitigating.
2. **Classify the outcome:**
   - **Behavior decided, spec must change** → the plan is spec-changing:
     it needs `## Spec Baseline` and `## Proposed Spec Delta` with a
     promotion strategy, and the sequence is fixed: independent review of
     the exact delta → spec-promotion slice → record the promotion
     baseline identifier → then code (writing-plans §4b–4d). The spec
     tree, not the brainstorm, becomes the source of truth.
   - **Behavior decided, governed by an existing spec that does not
     change** → ordinary plan that **cites the governing spec
     sections and records the baseline** — an unchanged spec is still the
     contract, not an absent one.
   - **Behavior decided, no governing spec exists and none is warranted**
     → ordinary plan; say `Source spec: None — <reason>` plainly.
     If the behavior is material, a spec is warranted — write
     one first.
   - **Behavior still undecided** → exploration plan type: spike only,
     no implementation against a governing spec. When it firms up,
     return here.
   - **Trivial** → no plan; just do it with normal verification.
     Triviality is judged by the decision-hierarchy Task Classification's
     class-1/2 definitions — no intended-behavior
     change, no boundary crossed, no reusable workflow introduced, a
     zero-context engineer would not guess wrong — never by file count. A
     one-file spec or contract edit is not trivial.
3. **Write the dated plan** per `writing-plans.md`: goal, sources,
   invariants before tasks, open questions carried as explicit
   assumptions or blockers — never silently dropped.
4. **Route the leftovers.** Open questions that block: into the plan as
   blockers. Open questions that don't block: into a named
   `## Assumptions and Open Questions` section in the plan, each with an
   owner and the condition that resolves or reopens it — not into
   out-of-scope (which offers no follow-up) and not into lessons (which
   are for resolved, reusable rules). Ideas rejected for reasons that
   will recur: one line in `docs/lessons.md`.
5. **Add the plan to the status index** in `docs/plans/README.md`.

## Output Standard

- A dated plan in `docs/plans/` (or an explicit "trivial, no plan"
  decision), indexed
- Decisions, rejections, and open questions all captured in a durable
  surface — nothing load-bearing left only in conversation
- Spec impact classified explicitly, with the §4b–4d machinery engaged
  when behavior moved

## Maintenance Notes

- If plans repeatedly arrive missing the same exploration output (e.g.
  rejected alternatives), strengthen step 1's harvest list.
- If step 2's classification keeps getting argued, the boundary belongs
  in `writing-plans.md`, not here.
