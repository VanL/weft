# Agent-Guidance Propagation Plan (2026-07-17 delta wave)

Status: completed
Source specs: none — process/guidance adoption from agent-guidance @ `b248e1c`; no governing product spec (weft carries its operating model in runbooks, `docs/agent-context/`, and `docs/coalescing.md`)
Superseded by: none

Class: highest doc class (5-equivalent treatment) per this repo's
adoption discipline — spec-equivalent normative text lands, so it gets
the full independent-adaptation-review treatment. Hardening: N/A — docs
and guidance only, no risky trigger. Source content already carried the
hub's own review rounds; this repo's review is scoped to the adaptation.

## Goal

Land the agent-guidance delta since weft's last pin (the 2026-07-14 wave,
weft `1e2e16d`, and its 2026-07-15 coalescing tranche, weft `8382a4d5`).
Hub delta = commits `cc7ab30`..`b248e1c`. Adapt each payload item to
weft's idiomatic homes (no DOM spec; named review-loops sections;
theme-cluster fold unit with a Fold Records index; the plan-metadata
contract in `tests/specs/test_plan_metadata.py`).

## Payload Checklist (grep-verifiable)

1. **[DOM-14] trigger-bullet content** (hub `30c8b04`: fold-unit-denominated
   triggers, declared progress model) → `docs/coalescing.md` (weft's
   normative home for coalescing rules). Weft already declares the fold
   unit (theme cluster) and the Fold Records progress model in its
   Watermarks note; the wave promotes that from hypothesis-framing to a
   settled normative rule. Small by design — verify, don't duplicate.
2. **`skills/coalescing/SKILL.md`** ← hub `cc7ab30`'s six refinements
   (fold-unit trigger denomination; three-tier fold verification;
   adjacent-examples-are-claims; framework-fact expiry; catch-all section
   check; collision-aware landing), integrated into the local copy,
   preserving weft's local derivation-command deferrals.
3. **NEW `docs/agent-context/runbooks/designing-agent-facing-interfaces.md`**
   from `b248e1c`; provenance localized; registered in
   `docs/agent-context/context.index.yaml`.
4. **NEW `skills/interface-review/SKILL.md`** from `b248e1c`; provenance
   localized; skill self-registers by directory (weft keeps no central
   skill-registry table).
5. **`docs/agent-context/runbooks/writing-plans.md`**: the
   "approval attaches to reviewed text" lifecycle bullet (hub `fafd874`;
   mm's lifecycle-rollback hardening plan quoted by name as foreign) +
   the "plans record evidence, never transient state" planning-standard
   bullet (hub `b248e1c`).
6. **`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`**:
   the two-question PASS/BLOCKED plan-review prompt + block-must-trace
   note (Planning Review Prompt section); the scoped-change template +
   round-2 variant (new Scoped Change Review Prompt section); the verdict
   vocabulary (Review Output Standard section). Net final hub state after
   `6052289` reverts the `BLOCKED/CLEAR` overreach: plan reviews keep
   `PASS`/`BLOCKED`.
7. **`skills/call-agent/SKILL.md`**: step-2 brief standard + scoped-change
   template pointer + verdict phrasing (final hub state after `6052289`:
   `PASS/BLOCKED` for plan reviews, `no blocker`/`blocker: F<ids>` for
   scoped-change).

Out of scope (hub-native / not payload): `bin/bootstrap-agent-guidance`
changes (hub `fc23eae`, `8c504fd`) — the bootstrap script is hub-only and
never propagated. Hub run-log/repo-map/plan bookkeeping in `cc7ab30`,
`30c8b04`, `763a0e9`, `fafd874` (hub's own `docs/coalescing.md`,
`docs/implementation/02-repository-map.md`, `docs/plans/*`) stays in the
hub; weft's equivalents are `docs/coalescing.md` run log (this plan) and
`context.index.yaml`.

## Weft Adaptations (name-map)

- **No DOM spec, none created.** [DOM-14]'s promoted trigger bullet lands
  in `docs/coalescing.md` (weft's normative coalescing home). [DOM-10]
  (verification-evidence bar) name-maps to `AGENTS.md` Definition of Done;
  [DOM-14] (promotion provenance) name-maps to `docs/coalescing.md`.
- **Engineering-principles numbering is weft-local.** Hub
  "engineering-principles §12 (Enumerable Contracts Get Executable Gates)"
  → weft **§9** (same title). Hub "engineering-principles §2, Canonicalize
  at Boundaries" → weft **§3** (Canonicalize at Boundaries, Then Stay
  Strict). Hub [DOM-14] coalesce-on-events → weft **§12** (Coalesce on
  Events) / `docs/coalescing.md`.
- **Review-loops uses named sections, not numbers.** Hub §4 → weft
  "Planning Review Prompt"; hub §4a → new "Scoped Change Review Prompt"
  (placed before the Feedback Loop); hub §6 verdict vocabulary → weft
  "Review Output Standard". Weft's existing `call-agent` uses `§`-shorthand
  pointing at the named runbook sections — a pre-existing local convention,
  matched (not "fixed") here.
- **Runbook registry** is `docs/agent-context/context.index.yaml`
  (weft has no maintaining-traceability/writing-specs runbooks); the new
  runbook is registered there with role `operational_runbook`.
- **Skills self-register by directory** — weft keeps no central skill
  table. `skills/interface-review/` presence is the registration;
  `skills/README.md` is generic guidance, unchanged.
- **Provenance localized**: new-file Status/provenance lines cite this
  plan + source `b248e1c`; hub plans (the agent-facing-interfaces runbook
  plan, the interface-review promotion plan, mm's lifecycle-rollback
  hardening plan) are quoted by name as foreign, never as resolvable
  weft paths.
- **fafd874's "see `docs/coalescing.md`" pointer** (to the hub's fold-up
  candidate slate) is dropped in weft's copy — weft's `docs/coalescing.md`
  does not track mm's fold-up candidates. The mm incident is kept as the
  cited single-lineage evidence.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Review Findings and Dispositions

Round 1 (grok, read-only, 2026-07-17; §4a-form brief, scoped to the
adaptation): **no blocker, zero findings.** Verified: hub `b248e1c`
end-state fidelity (PASS/BLOCKED; BLOCKED/CLEAR appears only as
historical narrative); the coalescing.md normative addition coherent
with the existing theme-cluster/Fold Records declaration; all name-map
targets resolve (§9 Enumerable Contracts, §3 Canonicalize, [DOM-10] →
AGENTS.md Definition of Done); six refinements once each with local
deferrals kept; provenance foreign-quoted; doc gates 8/8.

Accepted observations (no change this wave): the call-agent lineage
sentence omitted (non-normative provenance); the checklist's
"quoted by name" phrasing describes intent while the landed bullet
cites the incident and mechanisms rather than the foreign filename;
the coalescing.md plans-deferral count recount is scheduled by the
landing notes, not payload scope.

## Landing Notes (instructions, not state)

Land by explicit file-list staging against the payload checklist above;
run `uv run python -m pytest tests/specs/ -q` (plan-metadata and
spec-hygiene doc gates) before committing. Run weft's first delta-wave
coalescing sweep in the same unit per the standing sweep-after-propagation
rule; record its run-log line in `docs/coalescing.md`.
