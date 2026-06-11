# Service Disposition Window And Retirement Pacing Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]; the PolicyProgress catch-up contract in weft/core/monitor/progress.py (code-owned, mirrored in spec 05's policy-progress paragraph); Task C cites no spec (test-tooling hygiene)
Superseded by: none

## Goal

Address the two issues identified in the 2026-06-11 production health
examination of a Postgres-backed weft 0.9.80 deployment, after the
investigation falsified the original "scale the budgets" framing:

1. **Issue A — dead-service disposition timing and evidence.** The two
   services that died at the 2026-06-10 18:36 manager restart (manager TID
   `1780549254800650240` with 7,282 retained task-log rows, heartbeat
   `1780549255084601344` with 1,954) remain unsummarized and undisposed
   ~18 hours later, while their May-29 predecessor was cleaned. Code reading
   during planning already resolved the two leading theories most of the
   way: (a) the maintenance prune of superseded service-owner registry rows
   does NOT destroy disposition evidence — candidate discovery and
   service-key derivation come from the collation's own metadata
   (`_stale_service_owner_key`, weft/core/monitor/task_monitor.py), and the
   proof helper's final clause accepts a different live owner for the same
   key as standalone proof (`_stale_service_owner_proved`: with the old row
   pruned, `same_owner == []` falls through to the live-different-owner
   disjunct); (b) the candidate selection is age-gated by the retention
   window (`list_stale_service_owner_candidates` →
   `_retention_cutoff_ns(now_ns, retention_seconds)`,
   weft/core/monitor/store.py:1043-1071, default 48h), so freshly dead
   services are intentionally not disposable until ~48h after their last
   event. This group therefore PINS both behaviors with regression tests
   (red only if a theory is wrong after all) and fixes only what a red
   reveals. The expected outcome is green pins plus a documented ops
   timeline, not a code fix.
2. **Issue B — pipeline pacing for fully-processed families.** Production
   retires ~2 families/cycle while ~2,06x collations are
   marked+summarized+control-deleted. The lifecycle retirement call already
   runs at `limit=batch_size` (5,000) with waypoint wiring
   (task_monitor.py:2105-2123), so the limiter is NOT the retirement budget:
   ~2,06x families fail the retirable predicate each cycle. The binding
   stage is upstream — leading candidates are the retirable retention
   cutoff (`COALESCE(completed_at_ns, last_seen_at_ns, last_message_id) <=
   now - 48h`) and the reserved-probe gate (`reserved_probe_needed = 1` /
   `reserved_cleanup_checked_at_ns IS NULL`, satisfied by the
   reserved-cleanup slice which is deadline-bounded at
   `TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS = 1.0`). This group first
   identifies the binding stage deterministically at scale, then makes that
   stage honor the PolicyProgress catch-up contract: a bounded stop with
   eligible work remaining must report `waypoint_reached=True`
   (weft/core/monitor/progress.py:20-38 defines exactly this), which
   engages the existing 2-second catch-up cadence
   (task_monitor.py:4587-4595) until the backlog drains. **Budget scaling
   is explicitly rejected** (YAGNI): with catch-up engaged, even a flat
   2-families-per-slice rate converges a 2,000-family backlog in ~35
   minutes, and the owner has confirmed the catch-up load profile is
   acceptable on degraded installations.

Plus one small rider the investigation earned: **Task C** — guard the test
helpers against the body-substring TID matching that produced this
investigation's false-positive oracle (see docs/lessons.md, 2026-06-11).

Owner decisions already made (do not relitigate): catch-up heat is
acceptable; budget scaling is out; returning with falsified premises is the
expected behavior if this plan's own grounding fails (docs/lessons.md
2026-06-11, principles.md Collaboration Standards).

## Source Documents

Read in order before starting:

- `CLAUDE.md` §1.1 (philosophy — especially "Don't infer — read"), §4
  (house style), §5 (commands). `AGENTS.md` is identical.
- `docs/agent-context/principles.md` and
  `docs/agent-context/engineering-principles.md` (real-broker tests; no
  mock-heavy proofs; boundary validation).
- `docs/lessons.md` — the 2026-05-30 "Cleanup Progress Boundaries" entry
  (the FIFO too-young guardrail this plan must not break) and the
  2026-06-11 "Return With Falsified Premises" entry (the oracle corollary
  Task C implements).
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: the
  stale-service-owner disposition paragraph ("the Monitor may record a
  `stale_service_owner` or `superseded_service_owner` disposition ... only
  after same-service registry evidence proves the row stale: either the
  same owner has a latest non-live owner row, **or a different live owner
  exists for the same service key**") and the policy-progress paragraph
  (five policies; waypoint/base/blocked semantics).
- `weft/core/monitor/progress.py:20-38` — the `PolicyProgress` docstring is
  the binding contract for Task Group B: "``waypoint_reached`` means the
  policy yielded at a bounded stop and should be considered for catch-up
  cadence. ``base_reached`` means there is no eligible work for this policy
  at the current point in time."
- `docs/plans/2026-06-10-self-healing-runtime-maintenance-plan.md` — the
  predecessor plan; its WS-B built the disposition machinery Group A pins,
  and an interaction risk between its maintenance pruning and
  evidence-consuming classifiers was flagged during its review (the
  concern Group A1 turns into a permanent pin).

## Production Evidence Pack (corrected, re-runnable)

Read-only psql against schema `weft` on the deployment. **Never match TIDs
by body substring** (`position(tid in body)` produced this investigation's
false positives — service event bodies embed other tasks' TIDs); join
through the Monitor store refs instead:

```sql
-- Per-family retained raw rows, via refs (the honest oracle):
select m.tid, count(*) from weft.weft_monitor_task_messages m
 where m.deleted_at_ns is null group by m.tid order by 2 desc limit 5;
-- 2026-06-11 actuals: 1780549254800650240=7282 (dead manager),
-- 1780549255084601344=1954 (dead heartbeat), then the three live services.

-- Retirable-predicate failures (which condition blocks retirement).
-- NOTE the reserved arm is a DISJUNCTION in the real predicate
-- (sql.py: `reserved_probe_needed = 0 OR reserved_cleanup_checked_at_ns
-- IS NOT NULL`), so only probe-needed families count as reserved-blocked:
select count(*) filter (where raw_deleted_at_ns is null) as unmarked,
       count(*) filter (where summary_emitted_at_ns is null) as unsummarized,
       count(*) filter (where disposition_at_ns is null) as undisposed,
       count(*) filter (where task_control_deleted_at_ns is null) as control_pending,
       count(*) filter (where reserved_probe_needed = 1
                          and reserved_cleanup_checked_at_ns is null) as reserved_blocked,
       count(*) from weft.weft_monitor_task_collations;
```

2026-06-11 state: 2,066 collations (2,061 marked+summarized), refs 10,031
all belonging to the five service families, cron-task families fully clean
(rows+refs deleted), `weft.log.tasks` ≈ 10k rows that are almost entirely
the two dead services' event streams plus live-service events.

## Invariants and Constraints

- **The FIFO too-young guardrail (must not move):** age-gated stops
  (`first_*_too_young`, retention windows) keep reporting `base_reached` —
  ineligible work must never waypoint, or catch-up spins forever
  (docs/lessons.md 2026-05-30;
  tests/core/monitor/policies/test_cleanup_progress_boundaries.py pins it).
  Group B may waypoint only *budget*-deferred work that is eligible right
  now.
- **The five-policy contract:** no new `policy_progress[*].policy`
  identities (spec 05; pinned by the 0.9.80 D1 test). Group B changes flag
  values on existing records only.
- **Summary/audit gates stay:** in `jsonl_then_delete`, no raw row deletion
  before the family's `summary_emitted_at_ns` (0.9.80 A5). The dead
  services' 9,236 rows must NOT be deleted by anything this plan adds until
  their families are disposed+summarized through the existing path.
- **Disposition evidence rules (MF-5):** proof requires same-service
  registry evidence; this plan must not weaken the ambiguous-row
  protections (ambiguous rows stay open).
- **Wall-clock staleness over a FIXED message id is the correct domain**
  for dead-family age gates (established in the 0.9.80 clock-domain
  analysis: the cross-domain bug applies to gates chasing a *moving* ID
  head, not to observed staleness of a dead row). Group A2 pins this rather
  than "fixing" it.
- No new dependencies, no new execution paths, no schema changes. Real
  broker + real TaskMonitor in tests; `monkeypatch` only for env/config,
  the established `_monitor_monotonic` clock seam, and module constants the
  suite already patches (e.g. `RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS`).
  Mocking the store, broker, or cycle internals is a stop-and-re-evaluate
  moment.
- Commits: one per task on the current branch (same execution policy as
  the 2026-06-09/10 plans: local commits authorized, no push). The working
  tree may carry unrelated in-flight changes — leave
  `weft/commands/system.py` and `tests/commands/test_status.py` alone, and
  commit this plan file + `docs/plans/README.md` + the
  `docs/lessons.md`/`docs/agent-context/principles.md` edits (already in
  the tree from plan authoring) as the preflight commit.

## File Map

- Group A: tests only in `tests/tasks/test_task_monitor.py` (A1, A2); A3
  is conditional with candidate files
  `weft/core/monitor/task_monitor.py` / `weft/core/monitor/store.py`.
- Group B: `tests/tasks/test_task_monitor.py` (B1, B3);
  `weft/core/monitor/task_monitor.py` (B2 — slice-result→progress
  mappings); possibly `weft/core/monitor/cleanup.py` or
  `weft/core/monitor/policies/*.py` if B1 lands the binding stage there;
  `tests/core/monitor/policies/test_cleanup_progress_boundaries.py`
  (guardrail extension).
- Task C: `tests/tasks/test_task_monitor.py` (the oracle helper added by
  the 0.9.80 work, `_assert_raw_deleted_oracle` near line 5890).
- Close-out: `CHANGELOG.md` (Unreleased), spec 05 implementation-mapping
  note + backlink (B2 only, if it changes when catch-up engages),
  `docs/plans/README.md` status flip.

Environment setup, baseline gates, and verification binaries are identical
to `docs/plans/2026-06-09-evaluation-findings-remediation-plan.md` "Current
Repo State" (source `.envrc`, `uv sync --all-extras`, `./.venv/bin/*`;
Postgres suite via `bin/pytest-pg` with positional args only). Baseline:
fast suite, mypy, ruff all green before starting.

---

## Task Group A — disposition evidence and window pins

### Task A1: Regression pin — disposition after a real maintenance prune

- Context that changes this task's shape (verified 2026-06-11 during plan
  authoring): the existing WS-B test
  `test_task_monitor_jsonl_then_delete_disposes_stale_service_owner`
  ALREADY seeds only the live different-owner registry row (its
  `weft.state.services` write is a single `status: ACTIVE` record for
  `active_tid`; no superseded old-owner row exists in the fixture at all),
  so the "live-row-only proof" pin already exists and passes. What is NOT
  pinned is the production SEQUENCE: the monitor's own maintenance pass
  pruning the registry's services group BEFORE disposition runs.
- Outcome: a committed test proving disposition still fires after a real
  `run_runtime_prune_for_context` apply pass over the services group has
  pruned the registry to newest-per-key (the state ~17 maintenance passes
  produced in production before the disposition window opened). Expected
  GREEN; if RED, the maintenance↔evidence interaction is real and A3
  activates.
- Files: `tests/tasks/test_task_monitor.py`.
- Read first: the WS-B test named above (your test reuses its ENTIRE setup
  and drive); `weft/core/monitor/task_monitor.py`
  `_stale_service_owner_proved` and `_stale_service_owner_key`;
  how the 0.9.80 D2 maintenance test fabricates superseded owner rows
  (`_maintenance_service_owner_payload`, tests/tasks/test_task_monitor.py
  ~line 7124). Invoke the prune by calling
  `run_runtime_prune_for_context` DIRECTLY with a `RuntimePruneConfig`
  whose queue group covers services, `apply=True`, and — critical —
  `min_age_seconds=0.0` passed EXPLICITLY: the 3600s default binds into
  the dataclass at class-definition time, so monkeypatching the constant
  does nothing, and without the override your freshly seeded rows are
  never candidates and the step fails for a reason that looks like the
  theory being wrong. (Do NOT try to reuse the monitor's own maintenance
  entry point here — it only runs inside a drive, after disposition would
  already have been attempted; the direct call is the only way to get the
  production sequence "prune first, dispose later". Direct-call precedent:
  tests/commands/test_runtime_prune.py:132-139.)
- [ ] **Step A1.1: Write the test.** Clone the WS-B test as
  `test_stale_service_owner_disposes_after_maintenance_prune`. Two setup
  additions before the drive: (1) seed BOTH a superseded old-owner row
  (same service key, `owner_tid` = the stale tid, a non-live status) AND
  the live different-owner row into `weft.state.services`, so the prune
  has something to eat; (2) run the runtime-state prune apply pass for the
  services group (per the read-first findings) and assert the superseded
  row is gone and the live row survives BEFORE driving the monitor. Then
  the original's drive and assertions unchanged: disposition via the JSONL
  `terminal_runtime_cleanup` report's `lifetime.close_reason`, ctrl queues
  deleted, family retired.
- [ ] **Step A1.2: Run it.**

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -k after_maintenance_prune -q
```

  Expected: PASS (this is a pin, not a red-green fix). If it FAILS: record
  the failure output and jump to Task A3 with this as the red repro.
  Either way, do not modify production code in this task.
- [ ] **Step A1.3: Run the disposition neighborhood and commit.**

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -k "stale_service_owner or disposes" -q
git add tests/tasks/test_task_monitor.py
git commit -m "Pin stale-owner disposition after registry maintenance pruning"
```

### Task A2: Window pin — fresh-dead services dispose only after the window

- Outcome: a committed test pinning the timeline: a dead service family is
  NOT a disposition candidate while younger than the retention window, and
  IS disposed once `now_ns` passes the window — documenting that the
  production June-4 services clearing ~48h after death is design, not a
  stall.
- Files: `tests/tasks/test_task_monitor.py`.
- Read first: `weft/core/monitor/store.py:1043-1071`
  (`list_stale_service_owner_candidates` and `_retention_cutoff_ns` — note
  the cutoff compares against the collation's fixed position, which is the
  legitimate wall-clock-staleness domain per the Invariants section);
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS_DEFAULT` in
  `weft/_constants.py` (172800.0 = 48h).
- [ ] **Step A2.1: Write the test.** Same setup as A1's test, with one
  config difference that is the entire point: the existing WS-B fixture
  sets `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS: "0.000001"`, which
  disables the candidate window and is why production's timing was never
  pinned — your test sets it to a real window, `"3600"`. **Time-control
  mechanism (important — there is NO `now_ns` injection at the cycle entry
  point: `process_once()` takes no time argument and `_run_monitor_cycle`
  hard-codes `time.time_ns()`):** use BACKDATED message ids instead. The
  WS-B fixture already passes `message_id=int(tid)` to
  `store.record_task_log_updates`, and the candidate window compares the
  collation's `last_message_id` against `now - retention` — so seed TWO
  stale service families in one test, one with a tid/message-id backdated
  ~600 seconds (inside the window) and one backdated ~2 hours (outside
  it), then run the ONE real `drive_task_monitor_until` drive under the
  actual wall clock. Assert: the 2-hour family is disposed and its ctrl
  queues deleted; the 600-second family has no disposition
  (`store.get_task(tid).disposition_at_ns is None`) and its ctrl queues
  SURVIVE. Backdated 19-digit ids are constructed like the existing
  fixtures' literal tids (e.g., `str(time.time_ns() - lag_seconds *
  10**9)`); give the two families distinct service keys is NOT possible
  for two managers — use one manager-role family and one
  heartbeat-role family (metadata `{"role": "heartbeat_service"}` — see
  `_stale_service_owner_key`'s role fallbacks) with a live different-owner
  registry row seeded per key.
- [ ] **Step A2.2: Run; expect PASS on both phases.** If phase 2 FAILS
  (never disposes even past the window), that is a real red — record and
  go to A3. If phase 1 fails (disposes early), the window gate is broken
  in the other direction — also A3.

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -k retention_window -q
```

  (Name the test
  `test_stale_service_owner_disposes_only_after_retention_window`; the
  `-k retention_window` filter is a contiguous substring of that name.)
- [ ] **Step A2.3: Commit.**

```bash
git add tests/tasks/test_task_monitor.py
git commit -m "Pin the stale-owner disposition retention window timeline"
```

### Task A3 (conditional): fix what A1/A2 turned red

- Run ONLY if A1 or A2 produced a red. Outcome: minimal fix making the red
  pin green without weakening MF-5's ambiguous-row protections.
- Candidate sites by failure shape: proof failure with live-row-only →
  `_stale_service_owner_proved` (task_monitor.py; the final disjunct) or
  `_latest_service_owner_records` (registry read missing the live row);
  never-disposes-past-window → `list_stale_service_owner_candidates` /
  `_retention_cutoff_ns` (store.py) or the candidate-to-ready filtering in
  `_stale_service_owner_summary_ready_tasks`; disposes-early →
  the cutoff comparison's column choice.
- Constraints: do not relax the proof to dispose ambiguous rows; do not
  change the retention default; the fix must keep the A1/A2 pins AND the
  existing WS-B disposition tests green on both backends.
- Stop if: the fix wants to read evidence the maintenance prune deletes —
  that re-opens the evidence-retention design question and goes back to
  the owner with the red test as the exhibit, per
  `docs/agent-context/principles.md` (return with evidence).
- Commit: `Fix stale-owner disposition <failure shape>` — substitute the
  concrete failure A1/A2 named (e.g. "proof under pruned registry",
  "window never opens") for `<failure shape>`; the red test lands in the
  same commit.

## Task Group B — retirement pacing and the catch-up contract

### Task B1: Identify the binding stage at scale (characterization, red-ish)

- Outcome: a deterministic test that fabricates ~120 fully-processed
  families in the production shape and reports, via assertions, which
  predicate blocks their retirement in one cycle — converting the
  production mystery (2/cycle) into a named stage.
- Files: `tests/tasks/test_task_monitor.py`.
- Read first: `weft/core/monitor/sql.py`
  `select_retirable_task_collations` — the full predicate; note
  especially that the reserved arm is a DISJUNCTION
  (`reserved_probe_needed = 0 OR reserved_cleanup_checked_at_ns IS NOT
  NULL`, sql.py ~858-861), and `reserved_probe_needed` is set only for
  terminal NON-completed families
  (`weft/core/monitor/collation.py:149`: `terminal_seen and
  terminal_status != STATUS_COMPLETED`). Then
  `store.retire_completed_collation_families` (store.py:2158) and its
  THREE callers (task_monitor.py:2105 lifecycle with `limit=batch_size`,
  :3416 terminal-control slice and :3634 reserved slice, both with
  `limit=control_limit`); the reserved-cleanup slice
  (`_run_reserved_cleanup_slice`, called with explicit `now_ns` at
  ~:3261) and the per-slice family limit
  (`_runtime_cleanup_family_limit` = min(1000, 50) = 50).
- Fabrication recipe (real store API + real drives, no mocks, no clock
  control): seed ~120 terminal families as `weft.log.tasks` writes BEFORE
  the first cycle, with two properties that matter:
  (1) **status mix** — ~half `completed` and ~half `failed`, because
  completed families have `reserved_probe_needed = 0` and trivially pass
  the reserved arm; only failed/killed families can exhibit the
  reserved-probe gate at all;
  (2) **backdated COMPLETION TIMESTAMPS, not message ids** — real queue
  writes assign broker message ids at write time, so ids cannot be
  backdated through the queue API (only direct
  `store.record_task_log_updates(message_id=...)` fabrication controls
  them, and B1 needs REAL broker rows for the full pipeline). Instead,
  exploit the retirable cutoff's coalesce order —
  `COALESCE(completed_at_ns, last_seen_at_ns, last_message_id)` picks
  `completed_at_ns` FIRST — by seeding event payloads whose
  `state.completed_at` (and `started_at`) are
  `time.time_ns() - 3 * 86400 * 10**9`-style values. Ingest carries them
  into the collation's `completed_at_ns`, every family lands past the
  retention cutoff under the real wall clock, and full
  `drive_task_monitor_until` drives work without any id control.
  Template tests to mirror: `_seed_terminal_family_backlog`
  (tests/tasks/test_task_monitor.py ~5977, extended with the failed-status
  variant) and the oracle-checked rung tests (~6425-6489).
  **Config traps (both will silently misname the binding stage):** the
  module's autouse fixture monkeypatches
  `TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS` to 30.0 — re-patch it to
  the production 1.0 inside this test so the slice deadline can actually
  bind; and use production-like `WEFT_TASK_MONITOR_BATCH_SIZE` (5000) and
  scan limits, NOT the template's `BATCH_SIZE: 10`, or ingest/summary
  become the artificial bottleneck. Since 120 > the 50-family slice
  limit, expect `family_limit_hit` waypoints from the slices — that is
  signal, not noise.
  Per cycle, count `families_retired` and, for still-unretired families,
  which predicate arm is unsatisfied. `store.get_task(tid)` exposes the
  COLUMN arms (existing tests read it this way), but the retirable SQL has
  one arm `get_task` cannot see: `NOT EXISTS` live child refs in
  `weft_monitor_task_messages`. The breakdown MUST also report
  refs-present per family — count it through the store the way the 0.9.80
  helpers do (`_ingest_live_rows_without_deletion` and the A5 convergence
  tests touch ref state; mirror their accessor) — or the characterization
  will blame retention/reserved/control columns when refs are the real
  blocker. Group per the corrected Evidence Pack query's logic (reserved
  arm conditioned on `reserved_probe_needed`) plus the refs arm and the
  coalesce-cutoff arm.
- [ ] **Step B1.1** Write
  `test_retirement_backlog_identifies_binding_stage` exactly as above,
  with a final assertion block that asserts the per-predicate failure
  counts and is INTENTIONALLY strict: assert that after N cycles (pick
  N=3) either all 120 are retired OR the test fails printing the
  per-predicate breakdown. The expected first run FAILS with the
  breakdown — that failure message is the characterization deliverable.
- [ ] **Step B1.2** Run on SQLite, then Postgres:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -k binding_stage -q
bin/pytest-pg tests/tasks/test_task_monitor.py
```

  Record the breakdown in the commit message and proceed to B2 with the
  named stage. If everything retires in 3 cycles on both backends (no
  red), STOP: the production limiter is environmental, not logical —
  report back with the matrix instead of guessing (return-with-evidence
  rule), and leave the test as a green scale pin.
- [ ] **Step B1.3** Do not commit yet — B2 lands this test (red→green)
  with its fix, unless B1.2 ended green (then commit standalone:
  `Pin retirement convergence at backlog scale`).

### Task B2: Waypoint conformance for the binding stage

- Outcome: the stage B1 named honors the PolicyProgress contract — a
  bounded stop (deadline, family limit, partial batch) with eligible work
  remaining reports `waypoint_reached=True`, so
  `progress_requires_catchup` (weft/core/monitor/progress.py:100) engages
  the existing 2s catch-up cadence until the backlog drains. B1's test
  goes green with N=3 cycles replaced by "cycles until catch-up clears,
  bounded".
- Files: `weft/core/monitor/task_monitor.py` (the slice-result→
  PolicyProgress mapping for the named stage); possibly
  `weft/core/monitor/policies/*.py` if the flag originates there.
- Pre-satisfied inventory (verified 2026-06-11 — most of the contract
  already holds; expect B2 to be a SMALL change or a no-op): the
  terminal-control slice already sets `waypoint_reached = family_limit_hit
  or deadline_hit` (task_monitor.py ~3452) and the reserved slice sets
  `waypoint_reached = monitor_family_limit_hit or selection.pending or
  deadline_hit` (~3681-3683); worker completions ALSO engage catch-up
  directly (`_last_catchup_pending = self._last_catchup_pending or
  (cleanup.success and progress_requires_catchup(...))`, ~4959-4966),
  independent of the end-of-cycle path. The only mapping site matching the
  violation shape is the lifecycle retirement record
  (`waypoint_reached = families_retired >= batch_size`, ~2120-2123) — and
  you may only touch it if B1 proved retirement itself binds. **If it does
  bind, the fix is NOT just a flag mapping:**
  `retire_completed_collation_families` returns only `families_retired`
  (store.py ~2197), so the caller cannot distinguish "drained" from
  "eligible work remains" on a partial batch. The conformant fix extends
  the store with the repo's established limit+1 idiom (mirror
  `list_deletable_task_log_messages`'s `limit=batch_size + 1` /
  `more_refs = len(refs) > batch` pattern): either have the retirement
  method select limit+1 candidates and report a `more_retirable` boolean
  alongside the count, or add a sibling peek query — a store+sql change
  with its own unit coverage, landed in the same commit as the flag
  change.
- **No-violation exit (mandatory branch):** if B1's named stage already
  waypoints correctly (likely, per the inventory) — or B1 named the
  retention window, which must NOT waypoint per the FIFO guardrail — then
  STOP: report the finding with the B1 breakdown as the exhibit, commit
  B1's test standalone as a green scale pin, and SKIP Step B2.5's commit
  and B3's `### Changed` entry. Do not manufacture a flag change to have
  something to ship (return-with-evidence rule,
  `docs/agent-context/principles.md`).
- **Error guardrail (second anti-spin case):** never wire
  `waypoint_reached` to anything containing `bool(errors)` — the slice
  `terminal_pending` includes errors, and the PolicyProgress contract says
  blocked "is neither base nor a catch-up signal by itself"; waypointing
  on errors means catch-up spins at 2s against a persistent failure.
- Guardrails (write these FIRST, red against any naive implementation;
  they SPLIT across two files because the boundaries module is a pure
  policy-function unit module with no TaskMonitor or broker): (1) the
  flag-half — a backlog entirely inside an age window produces
  `base_reached=True, waypoint_reached=False` — goes in
  `tests/core/monitor/policies/test_cleanup_progress_boundaries.py` IF the
  named stage's progress is built by a `policies/*.py` function, else
  alongside the catch-up tests; (2) the catch-up half — too-young backlog
  AND error-blocked backlog each leave `_last_catchup_pending` False after
  a full drive — goes in `tests/tasks/test_task_monitor.py` (it needs a
  real TaskMonitor). This is the 2026-05-30 lesson's anti-spin protection
  plus the error case from the section above.
- [ ] **Step B2.1** Write the guardrail test (red if your fix is naive,
  green if the eligible/ineligible distinction is right).
- [ ] **Step B2.2** Apply the minimal flag fix at the named stage.
- [ ] **Step B2.3** B1's test green (with catch-up assertions added:
  `_last_catchup_pending` True while eligible work remains, False after
  convergence — assert the cached attribute the way existing tests do
  rather than the private `_next_cycle_due_monotonic` deadline);
  guardrail green; full module green; PG run green.

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py tests/core/monitor/policies/ -q
bin/pytest-pg tests/tasks/test_task_monitor.py
./.venv/bin/mypy weft/core && ./.venv/bin/ruff check weft
```

- [ ] **Step B2.4** Spec note: if (and only if) the change alters when
  catch-up engages for an existing policy, append one sentence to spec
  05's policy-progress paragraph's `_Implementation mapping_` note naming
  the conformant stages, and add this plan to 05's `## Related Plans`
  (backtick-path link format). Run `./.venv/bin/python -m pytest
  tests/specs/ -q`.
- [ ] **Step B2.5** Commit:
  `Engage catch-up for budget-deferred <stage> work` — substitute the
  stage B1's breakdown named (e.g. "reserved-probe") for `<stage>`;
  tests + fix + spec note land together.

### Task B3: CHANGELOG and close-out

- [ ] Add to `CHANGELOG.md` `## Unreleased` (create the heading if a
  release consumed the previous one): one `### Fixed` entry if A3
  activated; one `### Changed` entry for B2 — "monitor catch-up cadence
  now engages for budget-deferred backlogs in the <stage> stage
  (substitute B1's named stage); flat per-slice budgets unchanged —
  backlog convergence drops from days to minutes".
- [ ] Flip this plan's `Status: draft` to `completed`, update its
  `docs/plans/README.md` row, run
  `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`,
  commit `Close out disposition-window and retirement-pacing plan`.

## Task C — oracle hygiene in the test helpers

- Outcome: the scale/lifecycle test helpers cannot repeat the
  body-substring TID false positive.
- Files: `tests/tasks/test_task_monitor.py` — the
  `_assert_raw_deleted_oracle` helper (near line 5890, added 2026-06-10).
- [ ] **Step C.1** Read the helper. If it locates a family's raw rows by
  substring-matching the tid anywhere in the row body, change it to parse
  each row's JSON and compare the top-level `"tid"` field exactly (rows in
  `weft.log.tasks` are JSON objects with a `tid` key; malformed rows —
  `json.JSONDecodeError` or missing key — are not the family's rows and
  are skipped). If it already parses exactly, add a one-line comment
  pointing at docs/lessons.md 2026-06-11 ("do not weaken this to substring
  matching") and move on.
- [ ] **Step C.2** Run the helper's consumers and commit. Expectation
  (verified during plan review by two independent reviewers): the helper's
  matcher `_broker_rows_by_tid` ALREADY parses JSON and compares the
  top-level `tid` exactly, so the comment-only branch is the likely
  outcome — the commit message below reflects that; use the
  "Harden..." wording only if you actually changed matching logic:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -k "oracle or lifecycle or clock_lag" -q
git add tests/tasks/test_task_monitor.py
git commit -m "Document the oracle's exact TID-matching guard"
```

## Testing Plan (summary)

| Task | Proof | Must NOT mock |
|------|-------|---------------|
| A1 | pin (expected green): disposition with live-row-only registry | registry, store, cycle |
| A2 | pin: no disposition inside window, disposition after (backdated ids via store fabrication, per its template test) | the window gate |
| A3 | conditional red→green from A1/A2 | per A-group constraints |
| B1 | characterization: per-predicate breakdown at 120-family scale | store predicates, broker |
| B2 | red→green: guardrail (too-young stays base, no catch-up) + backlog drains under catch-up | progress/catchup chain |
| C | helper hardening; existing consumers stay green | n/a |

Cross-cutting: real broker + real TaskMonitor everywhere; time is
controlled by BACKDATED tids/message-ids plus the established
`_monitor_monotonic` monkeypatch — there is no `now_ns` injection at the
cycle entry point, and nothing may patch `time.time_ns`; zero sleeps;
every test asserts
observable state (store columns via the real session, queue contents,
STATUS-cached fields), never patched internals; new test modules are not
expected (everything extends existing classified modules — if you do
create one under `tests/core/`, register it in `tests/conftest.py`
`_SHARED_MODULES`). PG matrix runs are mandatory for B1/B2 (pacing
behavior is the production complaint and production is Postgres).

## Verification and Gates

Per-task commands inline. Final gates:

```bash
./.venv/bin/python -m pytest -q
bin/pytest-pg tests/tasks/test_task_monitor.py tests/core/monitor
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

Rollout: library change, no deploy sequencing. Post-deploy observation on
the production host: within an hour of upgrade, `_last_catchup_pending`
visible via `weft task ping <monitor-tid>` while the retirable backlog
drains, then collations converging toward live+retention-window counts;
the June-4 dead services dispose ~48h after their death regardless of this
plan (A2's documented timeline). Rollback: revert the B2 commit; the pins
(A1/A2/C) are tests only.

## Independent Review Loop

External review recommended (B2 touches catch-up engagement — the
spin-risk surface the 2026-05-30 lesson guards). Reviewer: different agent
family when available, else a fresh zero-context session. Reviewer reads:
this plan; `weft/core/monitor/progress.py`; the
`_stale_service_owner_*` functions and `list_stale_service_owner_candidates`;
`select_retirable_task_collations`; the 2026-05-30 and 2026-06-11 lessons.
Prompt: the standard runbook prompt ("Could you implement this confidently
and correctly if asked?"). Dispositions recorded below.

## Out of Scope

- **Live-service event-stream growth.** The dead manager retained 7,282 of
  its own observability events (~1,150/day); under `jsonl_then_delete`
  every long-lived service accrues its full event stream until death +
  window + disposition. Whether live service families deserve an
  intra-life trimming story is a product decision for the owner — flagged,
  not planned.
- **Budget scaling** — rejected above (YAGNI; catch-up conformance
  suffices; owner accepted the catch-up load profile).
- **The retention-window length itself** (48h default) — A2 documents it;
  changing it is configuration, not code.
- **Production deployment/upgrade mechanics** — owned by the deployment's
  own cron/deploy tooling.

## Fresh-Eyes Review Record

- **2026-06-11, author pass 1** (separate re-read after drafting): found
  that the existing WS-B disposition test already seeds only the live
  different-owner registry row AND disables the candidate window with
  `RETENTION_PERIOD_SECONDS: "0.000001"` — so the originally drafted A1
  ("remove the old row") was impossible and redundant. A1 was re-aimed at
  the untested production sequence (real maintenance prune before
  disposition) and A2 gained the explicit real-window override. Also
  resolved the `<stage>`/`<failure shape>` commit-message tokens into
  explicit substitution instructions.
- **2026-06-11, independent zero-context review** (fresh agent; every
  file:line claim verified, the WS-B template test executed, the metadata
  test run). 11 findings, all addressed: two blockers — (1) the plan
  named a `now_ns` injection seam at the cycle entry point that does not
  exist (`process_once` takes no time argument); fixed by switching A2 to
  backdated message-ids with two families in one real drive, and B1 to
  backdated seeding with full real drives, both built on the suite's
  actual seams; (2) B1's completed-only family recipe could never exhibit
  the reserved-probe gate (`reserved_probe_needed` is set only for
  terminal non-completed families); fixed with a completed/failed status
  mix. Should-fixes: B2 gained the pre-satisfied inventory (terminal and
  reserved slices already waypoint on bounded stops; worker completions
  already engage catch-up), a mandatory no-violation exit that skips the
  commit and CHANGELOG entry, and an error-blocked anti-spin guardrail;
  A2.2's `-k` filter corrected to a real substring; the retirable
  predicate corrected to its actual disjunctive reserved arm in both the
  read-first text and the Evidence Pack query; A1's direct-prune call
  gained the explicit `min_age_seconds=0.0` (dataclass default binds at
  class definition; monkeypatching the constant does nothing) and lost
  the dead-end maintenance-entry-point branch. Nits: retirement has THREE
  callers (the reserved slice also retires); the B2 guardrail split
  across the policies unit module and the TaskMonitor module; the
  unverifiable "Finding 13" citation reworded. Reviewer's verdict as
  drafted: no; after these revisions the named gaps are closed.
- **2026-06-11, external cross-family review (OpenAI Codex, owner-triggered,
  read-only sandbox, ~2.6M tokens):** verdict "not confidently as written —
  close, but Group B has enough ambiguity to produce misleading tests or
  the wrong fix"; A1 implementable, A2 implementable after wording cleanup,
  Task C confirmed already-exact (comment-only branch expected). Five
  findings, all verified and incorporated: (1) B1 mixed incompatible
  seeding paths — real queue writes cannot carry backdated message ids;
  resolved by backdating `state.completed_at` payloads instead (the
  retirable cutoff coalesces `completed_at_ns` first), keeping real broker
  rows and real drives; (2) B2's retirement-binding branch underspecified
  the real fix — `retire_completed_collation_families` returns only a
  count, so a partial batch cannot signal remaining eligible work; the
  plan now requires the store limit+1 idiom (`more_retirable`) as a
  store+sql change with unit coverage; (3) B1's predicate breakdown would
  have missed the `NOT EXISTS` live-refs arm that `get_task` cannot see —
  the breakdown now must include refs-present and the coalesce-cutoff
  arms; (4) Task C's commit message overstated the expected comment-only
  change — reworded; (5) a stale "injected clock" cell in the testing
  table — corrected to the store-fabrication mechanism. Codex's process
  note that the plan cites CLAUDE.md rather than AGENTS.md was considered
  and not adopted (the repo states the files are identical and the plan
  says so). This completes the plan's external review gate.
