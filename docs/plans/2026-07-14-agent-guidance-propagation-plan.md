# Agent-Guidance Propagation Plan (2026-07-14 wave)

Status: completed
Source specs: none — process/guidance adoption from agent-guidance @ `2f7eff6`; no governing product spec (weft carries its operating model in runbooks and `docs/agent-context/`)
Superseded by: none

Class: 3+P (effective 5 treatment) per the newly adopted Task
Classification — material to future process. Hardening: N/A — no risky
trigger (docs and guidance only). Source content carried seven review
rounds in agent-guidance plus taut, backstitch, and engram adaptation
reviews; this repo's review is scoped to the adaptation.

## Goal

Adopt the 2026-07-14 wave — coalescing layer, task classification,
performative-overengineering review lens, external-skill-suites
crosswalk, and four skills — with weft-specific adaptation, and close
the 2026-07-02 fold's provenance debt (pinned: fold `5927481`, wave
`2f7eff6`).

## Weft Adaptations (the deep-divergence repo)

- **No DOM spec exists and none is created.** Normative homes are
  weft-idiomatic: Task Classification lands in
  `docs/agent-context/decision-hierarchy.md` (with the [DOM-15] anchor
  kept in its heading so `bin/check-dom15-fixtures` works, retargeted
  to that file); the coalescing model's normative home is
  `docs/coalescing.md` itself. No new spec file — this also avoids
  disturbing the backstitch-on-weft baseline (weft is that tool's
  external test corpus).
- **All operating-model references in copied material are name-based**,
  not [DOM-*] codes (verified: no unresolvable codes remain) — weft's
  principle numbering (§4.1 failing-test-first, §9 gates, §11 cohesion;
  coalesce-on-events lands as §12) and runbook inventory (no
  maintaining-traceability, writing-specs, or skills-lifecycle) differ
  from every other adopter. The retired-plan citation form is folded
  into writing-plans' lifecycle section; spec-writing references point
  at `docs/specifications/README.md`; skills-lifecycle references point
  at `skills/README.md` (created).
- **Plan metadata contract honored**: this plan carries the
  Status/Source specs/Superseded by block that
  `tests/specs/test_plan_metadata.py` enforces; the `Class:` line rides
  as prose per the new writing-plans rule.
- **Inventory**: weft keeps no permanent inventory file (review-loops
  §1); call-agent's probe recording is adapted to the session inventory
  note.
- **Dirty-tree discipline**: weft's in-flight WIP (README, bin/mypy-check,
  testing-patterns, four plans, a TODO deletion) is untouched; the four
  shared-dirty files I edit (AGENTS, engineering-principles,
  writing-plans, lessons) land via synthetic staged blobs carrying only
  propagation hunks.
- Review-loops' locally restructured text has no "surface errors"
  sentence; the review lens landed in both prompts only — accepted
  local variance.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Review Findings and Dispositions

Round 1 (grok, scoped, 2026-07-14): **BLOCKED** — three P1 groups, five
P2s, all accepted: (1) the blanket DOM-code normalization produced five
garbled sentences in the transplanted rules (rewritten by hand — a
lesson: blanket token replacement needs a readability pass, not just a
zero-leftovers assert); (2) one operational [DOM-11] remained (now a
review-loops reference); (3) skill retargets were incomplete, including
a phantom cite of the source repo's call-agent plan (all retargeted:
skills/README.md, docs/specifications/, session inventory note,
propagation-plan status cites). P2s: above→below, checker-comment
honesty, updated_at, Class-line wording reconciled with the three-key
plan-metadata contract, state-file derivation prominence (accepted via
the existing step-1 deferral rule). Round 2 (grok, scoped): all eight
fixes verified OK; one residual inventory path in call-agent caught and
fixed; sandbox-only test-spawn failures confirmed environmental (the
suite is 70/70 outside the sandbox). Strategic adaptation choices
(no DOM spec, decision-hierarchy home, state-file-as-normative-home,
massive-honest first-sweep deferral, citation form in writing-plans,
checker retarget) were endorsed as sound and non-performative.
