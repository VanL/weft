# Coalescing State

Status: Active — adopted from agent-guidance @ `2f7eff6` ([DOM-14]
there) via `docs/plans/2026-07-14-agent-guidance-propagation-plan.md`.
This file is the normative home of the coalescing model in this
repository (weft carries no development-operating-model spec; runbooks
and this file own process).

Owner: any agent that observes a tripped threshold at session start.
Boundary: lessons, plans, and skill/runbook promotion. Specs and
implementation notes are living documents and are never coalesced.
Verification: the run log below, `bin/coalesce-check`, plus
`tests/specs/` doc gates. Required action: the session-start check is
**read-only**; all writes happen only inside an authorized maintenance
task (`skills/coalescing/SKILL.md`).

**Local derivation commands** (this file owns the repo-local format):
- Lessons: dated H2 sections — `grep -cE '^## 20[0-9]{2}-' docs/lessons.md`
  (sections after the watermark date).
- Plans: metadata `Status:` headers (the plan-metadata contract enforced
  by `tests/specs/test_plan_metadata.py`) — completed plans with no
  retired-ledger line.

**Executable check.** `bin/coalesce-check` implements the lessons
derivation above and audits this file's retrieval contract: it resolves
every backticked SHA (here, then in sibling repos), checks every
`git show <sha>:<path>` cue, and reports pins that are not in
`origin/main` as `local-only pin`. This file remains the spec — when
the two disagree, the declared commands win and the script is the
defect. Exit 1 means a cue is unretrievable anywhere, which is a broken
retrieval contract; local-only pins are reported but not fatal, because
publication is the owner's call. Adopted 2026-07-28 from agent-guidance
@ `51626db` via
`docs/plans/2026-07-28-agent-guidance-propagation-plan.md`; weft is the
motivating case — the 2026-07-28 field audit found this file's cues
true locally but unverifiable from the published mirror, and the tool's
first run here confirmed it (two local-only pins).

**Triggers are event-derived and denominated in this repo's fold unit.**
Counts are computed from the ledger and the current tree, never stored,
and count only cold, unfolded material — entries within the age floor or
already folded are not eligible. The progress model must match the fold
unit: this repo's fold unit is the **theme cluster** (not a date prefix),
so its progress is tracked by the `## Fold Records` index in
`docs/lessons.md`, not a date watermark — a date cursor would falsely
claim older unfolded material behind it was folded (see the Watermarks
note below). Adopted via
`docs/plans/2026-07-17-agent-guidance-propagation-plan.md` from
agent-guidance @ `b248e1c`; two independent lineages — mm's per-section
recalibration and weft's own date-cursor failure — established the rule.

## Thresholds

| Tier | Trigger (derived count) | Threshold | Age floor |
|------|------------------------|-----------|-----------|
| Lessons | dated H2 sections after the lessons watermark | 15 | 30 days, never sections cited by an active plan or in a still-accumulating theme |
| Plans | completed plans with no retired-ledger line and no exemplar tag | 25 | none — the harvest gate and two-step retirement are the guards |
| Promotion | distinct citations of the same workflow theme since the promotion watermark | 3 | n/a |

## Watermarks

| Tier | Distilled through | Source SHA |
|------|-------------------|------------|
| Lessons | (none — deliberately not advanced; see note) | — |
| Plans | (none) | — |
| Promotion | (none) | — |

Note on the lessons watermark: it stays at **(none)** even after the
2026-07-15 tranche. A single date-cursor watermark assumes chronological
prefix folding, but this ledger's fold unit is a **theme cluster scattered
across dates** — folding the 2026-04-07..04-09 test-timing cluster leaves
older unfolded sections (2026-04-03, 04-04, 04-06) behind it, so advancing a
date cursor would falsely claim they were folded. Honest accounting here comes
from removing folded sections from the ledger (the dated-section count drops)
plus the `## Fold Records` index in `docs/lessons.md`, not from a date
watermark. This is the concrete evidence behind the fold-unit-denomination
rule now stated in the model preamble above: weft's confirmed date-cursor
failure is one of that rule's two independent lineages (see the 2026-07-15
run-log line).

## Deferral State

| Tier | Checked through (date, SHA) | Counts at check | Reason deferred | Reconsider when |
|------|------------------------------|-----------------|-----------------|-----------------|
| Lessons | 2026-07-15, `1c997978` | 53 dated sections (was 57; first thematic tranche of 4 folded 2026-07-15) — still tripped; ~46 past the age floor (latest date < 2026-06-15) | Partial by design: the ledger is thematic-clustered across dates, not chronological, so it is worked one cold cluster at a time. Remaining cold clusters (manager leadership/liveness/convergence; cleanup/monitor policies; broker/exception boundaries; completion/result/terminal-proof grace; TaskSpec/runner boundaries) each need per-section dedup against Golden Rules 1–13 and citation checks against 149 plans — folding under time pressure destroys evidence | A dedicated sweep session is authorized; recount when the next cold cluster is worked |
| Plans | 2026-07-15, `1c997978` | 149 completed plans (sharpened from ~140) across 153 plan files (4 draft); retired ledger empty | Bulk retirement needs exemplar tagging decisions and per-plan harvest gates; deferred to dedicated sessions, oldest-first. This session retired nothing (out of scope) | A dedicated sweep session is authorized |
| Promotion | 2026-07-14, first sweep | not derived | Derive at a future sweep | — |

## Run Log

| Date | Tier(s) | Source SHA | Claim |
|------|---------|------------|-------|
| 2026-07-28 | — (gate correction; nothing folded) | — | **`coalesce-check` no longer probes the filesystem for sibling repositories** (corrected upstream in agent-theory and propagated). The old `SIBLING_ROOT = REPO_ROOT.parent` hardcoded a checkout layout no document declared, and reported SHAs resolvable only in a neighbouring working copy as *verified* — laundering a local-only claim into a green check, defeating the cue-portability rule the tool enforces. Now: own SHAs verified locally and against this repo's published remote; unresolvable SHAs reported as **foreign claims** naming the repository they cite (informational, never a verdict); an unresolvable SHA naming no repository is a genuine failure. `COALESCE_SIBLING_ROOT` is opt-in local convenience, off by default. |
| 2026-07-28 | — (upstream rename; nothing folded) | — | The guidance hub was renamed `agent-guidance` → `agent-theory` (it names a discipline — theory-building for agent-assisted development — not an artifact of instructions). `bin/coalesce-check`'s sibling list was repointed so hub SHA claims resolve again. Existing provenance lines, run-log rows, and plan filenames naming `agent-guidance` refer to that same upstream repository under its former name and are left as written; git commit messages likewise retain it. |
| 2026-07-28 | — (propagation; nothing folded) | source agent-guidance @ `e42762c`; landed `4e69784a` | Delta wave per `docs/plans/2026-07-28-agent-guidance-propagation-plan.md`: cue-portability rule (this repo is the motivating case), repair-in-sweep (taut `3706d73`, foreign), structured-index clause (weft's test-enforced metadata contract satisfies it), harness scoping sentence, both executable gates. First coalesce-check run: 53 H2 sections; local-only pins `1c997978`, `c6c1dd86` — the destructive fold's own retrieval cue is unverifiable from published history (owner publication decision). Scoped review no blocker; e42762c polish applied at landing. No thresholds, watermarks, or folds touched. |
| 2026-07-17 | — (propagation; nothing folded) | source agent-guidance @ `b248e1c`; landed `c6c1dd86` | Delta wave per `docs/plans/2026-07-17-agent-guidance-propagation-plan.md`: fold-unit trigger rule added to this file's normative preamble (weft is the rule's confirming second lineage; Watermarks note reframed hypothesis→settled); six coalescing-skill refinements; designing-agent-facing-interfaces runbook + interface-review skill adopted with weft name-maps; writing-plans and review-loops wave content; call-agent brief standard. Scoped review no blocker, zero findings; doc gates 70/70. Plans-deferral file-count recount deferred to the next sweep per landing notes. No thresholds, watermarks, or folds touched. |
| 2026-07-15 | lessons | `1c997978` (pre-fold HEAD; contains the raw sections) | First authorized bulk session — one bounded thematic tranche. Folded 4 cold test-timing sections (Interactive TTY Tests, CLI Harness Timeouts, Spawn-Heavy Timeout Tests, Cancelled Task Teardown; 2026-04-07..04-09, all uncited by any plan/spec) → `runbooks/testing-patterns.md` Pattern 10 "Lifecycle Tests Assert the Wrong Boundary Under Load", preserving concrete failure modes (Windows SQLite handle / DB releasability, zombie-as-exited, kill-boundary vs startup-budget, TID-mapping cleanup boundary, one-teardown-path-at-a-time). Raw entries deleted from the ledger; `## Fold Records` index added; fold cue resolves via `git show 1c997978:docs/lessons.md`. Dated sections 57→53. Watermark NOT advanced (thematic fold, not chronological prefix — see Watermarks note). Doc-gate tests `tests/specs/test_plan_metadata.py` + `test_spec_hygiene.py` pass. Plans tier: recount only (149 completed / 153 files), retired nothing. Additive-to-runbook + destructive-to-ledger performed in the working tree; orchestrator reviews and lands the commit. Cross-repo hypothesis evidence (recorded 2026-07-15 to close a gap — the fold commit message claimed this line carried it and it did not): (a) fold-unit-denominated triggers CONFIRMED — weft's fold unit is the theme cluster; a date cursor cannot track thematic folds; (b) framework-fact expiry check RUN, true-negative — SimpleBroker facts re-verified against the 2026-07-14 pin bump (`57255b83`, simplebroker 5.3.2→5.3.3) and all hold, zero expiries fired. Note: per the guidance repo's 2026-07-15 review, a true-negative validates the check, not the phenomenon — it does not count as fold-up lineage for the decay rule. |
| 2026-07-14 | all | first sweep (checked-deferred; nothing folded) | Layer adopted from agent-guidance `2f7eff6`; first sweep ran in the same unit per the sweep-after-propagation rule. Honest verdict: both lessons (57 sections) and plans (~140 completed) trip their thresholds massively — this repo is the coalescing layer's real workload. Deferred with explicit reasons above; bulk sessions are the reconsideration path. No watermark advanced. |
