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
| Lessons | (none — first sweep below) | — |
| Plans | (none — first sweep below) | — |
| Promotion | (none) | — |

## Deferral State

| Tier | Checked through (date, SHA) | Counts at check | Reason deferred | Reconsider when |
|------|------------------------------|-----------------|-----------------|-----------------|
| Lessons | 2026-07-14, first sweep | 57 dated sections past (no) watermark — tripped; ~50 are past the age floor | First sweep conservative by declaration: a bulk fold of a 67KB ledger requires a dedicated authorized session with per-section dedup against the 10 Golden Rules and citation checks against 152 plans — folding under time pressure is how evidence gets destroyed | A dedicated sweep session is authorized |
| Plans | 2026-07-14, first sweep | ~140 completed plans, no retired ledger entries — tripped | Bulk retirement needs exemplar tagging decisions and per-plan harvest gates; deferred to dedicated sessions, oldest-first | A dedicated sweep session is authorized |
| Promotion | 2026-07-14, first sweep | not derived | Derive at a future sweep | — |

## Run Log

| Date | Tier(s) | Source SHA | Claim |
|------|---------|------------|-------|
| 2026-07-14 | all | first sweep (checked-deferred; nothing folded) | Layer adopted from agent-guidance `2f7eff6`; first sweep ran in the same unit per the sweep-after-propagation rule. Honest verdict: both lessons (57 sections) and plans (~140 completed) trip their thresholds massively — this repo is the coalescing layer's real workload. Deferred with explicit reasons above; bulk sessions are the reconsideration path. No watermark advanced. |
