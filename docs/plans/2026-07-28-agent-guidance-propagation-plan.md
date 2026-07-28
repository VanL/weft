# Agent-Guidance Propagation Plan (2026-07-28 delta wave)

Status: completed
Source specs: none — process/guidance adoption from agent-guidance @ `51626db`; no governing product spec (weft carries its operating model in runbooks, `docs/agent-context/`, and `docs/coalescing.md`)
Superseded by: none

Class: 3+P. Per this repo's `+P` semantics
(`docs/agent-context/decision-hierarchy.md`, Task Classification), `+P`
combines as `max(base, 5)` plus a pre-landing different-family review —
so this is 5-equivalent treatment. Hardening: N/A — documentation,
guidance, and two read-only checker scripts; no execution-path,
contract, persistence, or rollout trigger fires. Source content already
carried the hub's own review rounds; this repo's review is scoped to
the adaptation (see §Review Findings and Dispositions for its
disposition under this session's fences).

## Goal

Land the agent-guidance delta since weft's last pin (the 2026-07-17
wave, sourced from hub `b248e1c`). Hub delta = `b248e1c`..`51626db`,
plus two fold-up items that were still in the hub's working tree at
extraction time (see §Deviation Log D1). Adapt each payload item to
weft's idiomatic homes: no development-operating-model spec (name-map
into runbooks, `docs/coalescing.md`, and `docs/agent-context/`);
dated-H2 lessons ledger with a theme-cluster fold unit; a plan-metadata
contract enforced by `tests/specs/test_plan_metadata.py` whose status
vocabulary is weft's own and test-closed.

## Source Pin

- Hub pin: agent-guidance `51626db` ("Land the guidance gates: the
  corpus checks its own claims"), verified with
  `git -C ../agent-guidance rev-parse --short HEAD` at extraction time.
- Fold-up items 1b/1c/1d and item 2 below were extracted from the hub's
  **working tree** at that same HEAD; they were uncommitted when this
  wave was cut. Their upstream substance is taut's commit `3706d73`,
  which is quoted as foreign provenance in the landed text. See D1.
- Extraction method: `git show 51626db:<path>` for committed payloads
  (end-state, never an intermediate commit's diff); direct worktree
  read for the two fold-up items.

## Payload Checklist (grep-verifiable)

1. **`skills/coalescing/SKILL.md`** — four edits to weft's localized copy:
   1. **(a) Cue portability** (hub `51626db`, committed): a fold cue must
      resolve in published history where a mirror exists; unpublished pins
      say `local-only pin` in the run log. Lands in the source-pinning
      paragraph of the lessons tier (step 2). Grep: `local-only pin`.
   2. **(b) Repair-in-sweep** (hub worktree; foreign source taut
      `3706d73`): an authorized sweep is maintenance as well as
      compaction — inspect the surfaces before trusting a count, repair
      in-wave under the three-part test, defer with evidence gap + owner
      + reconsideration condition. Lands at the head of step 1. Grep:
      `Inspect and repair the coalescing surfaces first`.
   3. **(c) Structured-index derivation clause** (hub worktree): where a
      repo's plan index is structured and gated, the gate runs first and
      the count never falls back past it. Lands in step 1's plans
      derivation chain, item 1. Grep: `structured and gated`.
   4. **(d) Purpose line**: repair is named as part of the sweep; the
      always-read tier is kept "small, accurate, and hot". Grep:
      `small, accurate, and hot`.
2. **`docs/agent-context/runbooks/writing-plans.md`** — Plan Lifecycle
   gains the closed-status-vocabulary bullet, adapted to weft (see
   §Weft Adaptations A4). Grep: `The status vocabulary is closed`.
3. **`AGENTS.md`** — the harness-scoping sentence after the two
   uppercase overrides (hub `51626db`, committed). Grep:
   `Harness-enforced controls are outside the hierarchy`.
4. **NEW `bin/check-doc-paths`** (hub `51626db`, committed; adapted):
   every backticked repo-relative path claim in weft's guidance
   surfaces must resolve. Registered in engineering-principles §9.
   Grep: `check-doc-paths`.
5. **NEW `bin/coalesce-check`** (hub `51626db`, committed; adapted):
   evidence trail for the coalescing layer — derives the lessons count
   with weft's declared command, verifies every run-log SHA and
   `git show <sha>:<path>` cue locally and in siblings, and reports
   `local-only pin` for SHAs absent from `origin/main`. Registered in
   `docs/coalescing.md`. Grep: `coalesce-check`.
6. **Registration and bookkeeping**: engineering-principles §9 names
   `bin/check-doc-paths`; `docs/coalescing.md` Verification line names
   `bin/coalesce-check`; `docs/plans/README.md` index row + file count
   for this plan.

Out of scope (hub-native / not payload for weft):
`bin/bootstrap-agent-guidance` changes and its `--scaffold` mode (the
bootstrap script is hub-only and never propagated); `LICENSE`
relicensing (hub's own ownership decision); hub `docs/lessons.md`,
`docs/coalescing.md`, `docs/plans/` and `docs/implementation/`
bookkeeping (hub-local records); `skills/propagate-guidance/SKILL.md`
(explicitly hub-native, never scaffolded or copied).

## Weft Adaptations (name-map)

- **A1 — No operating-model spec.** Hub content that would cite
  [DOM-N] lands in weft-idiomatic homes instead: the coalescing skill
  and `docs/coalescing.md` for sweep rules, the writing-plans runbook
  for lifecycle rules, `docs/agent-context/engineering-principles.md`
  for gate registration. Sections are cited by name, not by hub number.
- **A2 — Cue portability names weft as the motivating case.** The
  2026-07-28 field audit found weft's coalescing cues true locally but
  unverifiable from the published mirrors; the adapted paragraph says
  so in place rather than describing an anonymous incident. Weft has a
  live `origin` remote, so `bin/coalesce-check`'s publication check is
  live here, not skipped.
- **A3 — Structured-index clause recognizes weft's existing
  mechanism.** The hub clause asks for a structured, gated status index.
  Weft already has one and it is stronger than a prose index: plan
  status lives in a normalized three-key metadata block, the vocabulary
  is closed to `completed` and `draft`, and
  `tests/specs/test_plan_metadata.py` fails on an unknown status, a
  missing or misordered metadata block, an unindexed plan file, a
  missing index row, a stale index status, or a `Superseded by` pointer
  to a missing file. The adapted clause **names weft's contract as
  satisfying the rule** and states the derivation consequence (the gate
  runs before the count; a failure blocks derivation until repaired or
  explicitly deferred). It does not demand a new index.
- **A4 — `status-review` is recorded as a proposal, not adopted.**
  Weft's status set has no ambiguity state, so the hub's quarantine
  concept is genuinely new content here. Adding it would require
  editing `ALLOWED_STATUSES` in `tests/specs/test_plan_metadata.py` and
  weft's `## Status Taxonomy`. Per this wave's fences the test contract
  is not changed by a propagation; the landed bullet therefore states
  weft's actual closed vocabulary and carries the quarantine as an
  explicit open proposal for owner decision (see §Open Proposals P1).
- **A5 — Repair-in-sweep is quoted as foreign.** Weft is not a lineage
  for this doctrine; the block names taut's commit `3706d73` and the
  hub's owner-direction fold-up so the text reads as adopted, not
  locally derived.
- **A6 — `bin/check-doc-paths` scan surfaces are weft's layout.** Hub
  scans `docs/agent-context`, `docs/specs`, `skills` plus `AGENTS.md`,
  `docs/README.md`, `docs/coalescing.md`. Weft has no `docs/specs` and
  no `docs/README.md`; its normative surfaces are
  `docs/agent-context/`, `docs/specifications/`, `skills/`, plus
  `AGENTS.md` and `docs/coalescing.md`. The claim pattern is widened to
  `docs|skills|bin|tests` because weft's guidance cites `bin/` tools and
  `tests/specs/` gates by path. `docs/lessons.md` and `docs/plans/`
  stay excluded for the hub's stated reason: lessons cite foreign and
  historical paths, and plans cite paths that may retire.
  The `--scaffold` mode is dropped — it drives the hub-only bootstrap
  script, which weft does not carry.
- **A7 — `bin/coalesce-check` lessons derivation is weft's declared
  command.** `docs/coalescing.md` declares dated **H2 sections**
  (`grep -cE '^## 20[0-9]{2}-' docs/lessons.md`), not the hub's dated
  bullets; the tool uses weft's pattern. The sibling list is rewritten
  from weft's vantage (`agent-guidance`, `mm`, `taut`, `backstitch`,
  `engram`, `simplebroker`) so cross-repo cues in the run log — which
  cite agent-guidance and mm SHAs — resolve.
- **A8 — Skill Status-line provenance.** The coalescing skill's Status
  line cites **this** plan and the hub pin, never a hub plan path;
  foreign plans and commits are named, not path-linked.

## Open Proposals (owner decision, not landed)

- **P1 — `status-review` quarantine.** The hub's Plan Lifecycle
  standard defines `status-review` as a conservative quarantine for
  plans whose evidence cannot distinguish active from completed: it
  never counts as completed and never silently ages into it. Weft's
  vocabulary (`completed`, `draft`) has no such state. Adopting it
  requires three coordinated edits — `ALLOWED_STATUSES` in
  `tests/specs/test_plan_metadata.py`, the `## Status Taxonomy` section
  of `docs/plans/README.md`, and the coalescing skill's plans-tier
  count (quarantined rows must be excluded from the completed count).
  Not done here: a propagation does not change a receiving repo's test
  contract. Relevant context for the decision: weft carries 150
  `completed` plans and an empty retired ledger, so a bulk retirement
  campaign is the exact situation the quarantine exists for.
- **P2 — Vocabulary drift in inherited text.** Three inherited
  passages use hub status tokens weft's contract does not allow, so a
  literal execution of any of them would fail
  `tests/specs/test_plan_metadata.py`:
  1. `skills/coalescing/SKILL.md` step 1 counts index rows with status
     `completed` **or `superseded`**;
  2. the same skill's step 3 flips retiring plans to `retired-pending`;
  3. `docs/agent-context/runbooks/writing-plans.md` opens its Plan
     Lifecycle section with "Plans move through: `draft` → `active` →
     `completed` or `superseded` → `retired`", and its later bullets
     also use `retired-pending` and `exemplar`.

  Weft's allowed set is `completed` and `draft`. Item 3 now sits
  immediately above the closed-vocabulary bullet this wave lands, which
  makes the contradiction visible where before it was diffuse. Not
  repaired here: the fix is a vocabulary decision (extend the test
  contract to cover the lifecycle states weft actually needs, or
  restate all three passages in weft's two tokens), not an
  evidence-determined correction, and it is entangled with P1 — both
  are the same question about how many states weft's plan lifecycle
  really has. Candidate for the next authorized sweep under the
  repair-in-sweep doctrine this wave lands, or for a dedicated owner
  decision alongside P1.

- **P3 — Eight dangling path claims, and whether `check-doc-paths`
  should block.** First run in weft (2026-07-28) exits 1 on eight
  pre-existing claims, none introduced by this wave:

  | Claim | Cited in |
  |---|---|
  | `tests/acceptance/` | `docs/agent-context/runbooks/adversarial-acceptance-probes.md:22` |
  | `tests/property/` | `docs/agent-context/runbooks/testing-patterns.md:96` |
  | `docs/superpowers/plans/` | `docs/agent-context/runbooks/writing-plans.md:138` |
  | `tests/integration/` | `docs/specifications/08-Testing_Strategy.md:101` |
  | `tests/performance/` | `docs/specifications/08-Testing_Strategy.md:104` |
  | `tests/property/` | `docs/specifications/08-Testing_Strategy.md:107` |
  | `tests/integration/` | `docs/specifications/08A-Testing_Strategy_Planned.md:13` |
  | `tests/cli/test_cli_mycommand.py` | `AGENTS.md:736` |

  Per the propagation discipline, a pre-existing failure in the
  receiving repo's surfaces is theirs — noted, not "fixed" inside a
  propagation. These are also not uniformly rot: `08A` is a
  *planned*-strategy spec by name, and several test directories are
  aspirational structure rather than false current-state claims, so the
  honest reading is that weft's corpus has a legitimate class of
  forward-looking path claims the hub's gate model does not
  distinguish. The tool is therefore landed **advisory, not blocking**
  (so recorded in engineering-principles §9). The owner decision: triage
  each claim as rot (fix the prose), planned (mark it so the gate can
  exclude it — e.g. a `planned:` prefix or an explicit allowlist with a
  reason per entry), or real-and-missing (create the directory), then
  promote the tool to a required gate. Deliberately not solved by a
  silent baseline-suppression list, which would hide future rot behind
  today's exceptions.

## Deviation Log

- **D1 — Two payload items were extracted from the hub's working tree,
  not a commit.** `skills/propagate-guidance/SKILL.md` step 0 requires
  pinning a committed source. At extraction time the hub's HEAD was
  `51626db` and items 1b, 1c, 1d, and 2 were uncommitted worktree
  changes to `skills/coalescing/SKILL.md` and
  `docs/agent-context/runbooks/writing-plans.md`. The wave brief
  directed taking them anyway and noting it. Consequence: those four
  edits carry no verifiable hub cue. Mitigation — their upstream
  substance is taut's commit `3706d73`, which **is** committed and is
  named in the landed text; the hub's own commit for these items, once
  it exists, supersedes this note as their cue. Status: open until the
  hub commits them.
- **D2 — No independent adaptation review was run.** This wave's fences
  prohibit subagents, so the different-family scoped review that `+P`
  requires could not be dispatched. The review obligation is not
  waived — it is deferred and owed before this wave is considered
  reviewed. Recorded in §Review Findings and Dispositions.
- **D3 — No git writes.** This wave's fences prohibit staging,
  committing, and pinning. The landing steps in §Landing Notes are
  written as instructions for whoever commits; no commit SHA is
  recorded in this plan and no run-log line claims one.

## Review Findings and Dispositions

| # | Finding | Disposition |
|---|---------|-------------|
| — | Scoped review run 2026-07-28 (grok, read-only, §4a-form brief): **no blocker**. Verified: placement of the four skill edits; the vocabulary bullet's fidelity to weft's real (test-enforced) vocabulary; the scripts' weft constants; P1/P2 correctly held as proposals | Accepted as run |
| F1 | The e42762c polish ("is never a retirement candidate") was missing from the status-review paraphrase — the expected worktree-extraction residue (D1) | **Applied at landing**; every fold-up passage now matches the committed hub end-state, closing D1. SIBLINGS already carried agent-guidance (independent convergence with hub cec5666) |
| — | The tool's first run mechanically confirmed the field audit: fold cue `1c997978` and the 2026-07-17 landing `c6c1dd86` are local-only pins, unverifiable from published history | Recorded; publication discipline is the owner's open decision |

## Landing Notes (instructions, not state)

- Stage by explicit path list; never `git add -A`. Expected list:
  `skills/coalescing/SKILL.md`,
  `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/engineering-principles.md`, `AGENTS.md`,
  `docs/coalescing.md`, `bin/check-doc-paths`, `bin/coalesce-check`,
  `docs/plans/2026-07-28-agent-guidance-propagation-plan.md`,
  `docs/plans/README.md`.
- Gate before staging: `uv run python -m pytest tests/specs/ -q` must be
  green (baseline for this wave: 70 passed), and both new scripts must
  exit 0.
- Both scripts need the executable bit (`chmod +x`) before commit; they
  were written with it in this session, verify with `git ls-files -s`.
- After the wave commit, add the run-log line to `docs/coalescing.md`
  naming the landed SHA — the pin cannot live in the commit it names.
- Nothing in this wave folds material, advances a watermark, or changes
  a threshold. The plans-tier deferral counts in `docs/coalescing.md`
  are unchanged by this wave except that the plan corpus grows by this
  file; recount at the next sweep.

## Sweep

The sweep-after-propagation standing rule applies to a repo's **first**
adoption of the coalescing layer; weft adopted it on 2026-07-14 and has
run sweeps since. This wave therefore carries no first-sweep
obligation. It does hand the next sweep three concrete inputs: the
repair-in-sweep doctrine (now in the skill), `bin/coalesce-check` as
the step-1 derivation tool the skill's Maintenance Notes already
anticipated, and proposals P1 and P2.
