# Coalescing State

Status: Active — adopted from agent-guidance @ `2f7eff6` ([DOM-14]
there) via `docs/plans/2026-07-14-agent-guidance-propagation-plan.md`.
This file is the normative home of the coalescing model in this
repository (weft carries no development-operating-model spec; runbooks
and this file own process).

Owner: any agent that observes a tripped threshold at session start.
Boundary: lessons, plans, and skill/runbook promotion. Specs and
implementation notes are living documents and are never coalesced.
Verification: the run log below plus `tests/specs/` doc gates. Required
action: the session-start check is **read-only**; all writes happen only
inside an authorized maintenance task (`skills/coalescing/SKILL.md`).

**Local derivation commands** (this file owns the repo-local format):
- Lessons: dated H2 sections — `grep -cE '^## 20[0-9]{2}-' docs/lessons.md`
  (sections after the watermark date).
- Plans: metadata `Status:` headers (the plan-metadata contract enforced
  by `tests/specs/test_plan_metadata.py`) — completed plans with no
  retired-ledger line.

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
watermark. This is the concrete evidence for the "denominate the trigger in
the fold unit" hypothesis (see the 2026-07-15 run-log line).

## Deferral State

| Tier | Checked through (date, SHA) | Counts at check | Reason deferred | Reconsider when |
|------|------------------------------|-----------------|-----------------|-----------------|
| Lessons | 2026-07-15, `1c997978` | 53 dated sections (was 57; first thematic tranche of 4 folded 2026-07-15) — still tripped; ~46 past the age floor (latest date < 2026-06-15) | Partial by design: the ledger is thematic-clustered across dates, not chronological, so it is worked one cold cluster at a time. Remaining cold clusters (manager leadership/liveness/convergence; cleanup/monitor policies; broker/exception boundaries; completion/result/terminal-proof grace; TaskSpec/runner boundaries) each need per-section dedup against Golden Rules 1–13 and citation checks against 149 plans — folding under time pressure destroys evidence | A dedicated sweep session is authorized; recount when the next cold cluster is worked |
| Plans | 2026-07-15, `1c997978` | 149 completed plans (sharpened from ~140) across 153 plan files (4 draft); retired ledger empty | Bulk retirement needs exemplar tagging decisions and per-plan harvest gates; deferred to dedicated sessions, oldest-first. This session retired nothing (out of scope) | A dedicated sweep session is authorized |
| Promotion | 2026-07-14, first sweep | not derived | Derive at a future sweep | — |

## Run Log

| Date | Tier(s) | Source SHA | Claim |
|------|---------|------------|-------|
| 2026-07-14 | all | first sweep (checked-deferred; nothing folded) | Layer adopted from agent-guidance `2f7eff6`; first sweep ran in the same unit per the sweep-after-propagation rule. Honest verdict: both lessons (57 sections) and plans (~140 completed) trip their thresholds massively — this repo is the coalescing layer's real workload. Deferred with explicit reasons above; bulk sessions are the reconsideration path. No watermark advanced. |
| 2026-07-15 | lessons | `1c997978` (pre-fold HEAD; contains the raw sections) | First authorized bulk session — one bounded thematic tranche. Folded 4 cold test-timing sections (Interactive TTY Tests, CLI Harness Timeouts, Spawn-Heavy Timeout Tests, Cancelled Task Teardown; 2026-04-07..04-09, all uncited by any plan/spec) → `runbooks/testing-patterns.md` Pattern 10 "Lifecycle Tests Assert the Wrong Boundary Under Load", preserving concrete failure modes (Windows SQLite handle / DB releasability, zombie-as-exited, kill-boundary vs startup-budget, TID-mapping cleanup boundary, one-teardown-path-at-a-time). Raw entries deleted from the ledger; `## Fold Records` index added; fold cue resolves via `git show 1c997978:docs/lessons.md`. Dated sections 57→53. Watermark NOT advanced (thematic fold, not chronological prefix — see Watermarks note). Doc-gate tests `tests/specs/test_plan_metadata.py` + `test_spec_hygiene.py` pass. Plans tier: recount only (149 completed / 153 files), retired nothing. Additive-to-runbook + destructive-to-ledger performed in the working tree; orchestrator reviews and lands the commit. |
