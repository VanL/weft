# Self-Healing Runtime Maintenance Plan

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17], [MANAGER.8]; docs/specifications/01-Core_Components.md [CC-2.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.2], [SB-0.3]
Superseded by: none

## Goal

Make the Weft runtime clean up after itself, and fix the defects that
currently prevent it from doing so. This plan was triggered by a 2026-06-10
production investigation of a Postgres-backed deployment (weft 0.9.75,
simplebroker 4.3.0, simplebroker-pg 2.2.0) that found five concrete problems.
The repo owner's directive: rather than scheduling external cron jobs for
`weft system tidy` / `weft system prune`, harden the runtime so the
TaskMonitor owns this maintenance by default ("self-healing").

The five problems, with the production evidence that proves each:

1. **Exact-message-ID deletion silently no-ops (the big one).** In
   `jsonl_then_delete` mode the TaskMonitor's post-terminal lifecycle runs
   (summaries, control-queue cleanup) and then marks families
   `raw_deleted_at_ns` — but the raw `weft.log.tasks` rows are never
   deleted. Production counters: 8,732 raw rows (every row since manager
   start, ~1,300/day, zero trimmed), 8,748 store refs still present (in
   healthy operation refs are hard-DELETEd as families retire; note that
   `selected_for_delete_at_ns` is a writer-less legacy column, so its NULLs
   are uninformative — the probative fact is the refs' presence),
   2,068/2,071 families marked raw-deleted while their rows demonstrably
   exist, and a live STATUS probe showing `last_error = null` and
   `last_collation_store_error = null` — completion without errors,
   deletion without effect. The same silent no-op explains 6,109
   accumulated superseded tid-mapping rows that the monitor's per-cycle
   apply path should clear within ~40 minutes. Whole-queue deletes
   (`delete_from_queues`, used by control-queue cleanup) demonstrably work;
   only the exact-message-ID path (`apply_exact_prune_candidates`) is
   inert. Prime suspect: exact-ID candidates resolving as "missing" against
   the Postgres backend, with `reconcile_missing=True` then treating
   not-deleted rows as already-gone (refs removed, family marked, broker
   row orphaned). Note 0.9.75 shipped "Align broker exact insert API"
   (commit `3e04985`) touching exactly this surface.
2. **Families are marked raw-deleted vacuously.** Freshly terminal families
   were observed marked `raw_deleted_at_ns` at ~1 minute old, before their
   rows were ingested, with zero refs and raw rows still present. The
   marker (`reconcile_raw_deleted_tasks*` SQL) sets the flag whenever
   `NOT EXISTS (refs)`, which is satisfiable by "refs not written yet" and
   by "refs wrongly removed", not only by "refs deleted after broker
   deletion".
3. **Dead-incarnation cleanup is unreachable outside `delete` mode.** The
   spec-intended path (MF-5: stale/superseded service-owner disposition →
   `T{tid}.ctrl_in`/`ctrl_out` cleanup) never fires in `jsonl_then_delete`
   because the only disposition writer is gated on
   `self._monitor_config.mode == "delete"`
   (`weft/core/monitor/task_monitor.py` `_run_monitor_store_cycle`,
   `apply_disposition=...`). Production: a manager killed on June 4 left
   168 PONGs in `T1780096788952363008.ctrl_out`, invisible to every
   cleaner, plus one permanently unretirable collation row per restart.
4. **PONG replies have no retirement owner.** `send_keyed_ping_probe`
   (`weft/core/control_probe.py:138-205`) peeks and never consumes, by
   documented design; every ensure-manager liveness probe of a live manager
   permanently adds one PONG to `T{tid}.ctrl_out` (production: 193 on the
   live manager from two bounded probe windows, cadence matching the
   15-minute submission crons). The manager-internal probes delete matched
   pongs but orphan late/abandoned ones
   (`weft/core/manager.py:2165-2177`, `:2318-2328`).
5. **Claimed rows are never physically deleted.** The task-monitor's inbox
   held 1,835 `task_monitor_wakeup` messages, all `claimed=true` (consumed),
   accumulating 288/day for the manager's lifetime. SimpleBroker defers
   physical deletion to vacuum, whose only automatic trigger (every 100
   writes per connection, then claimed ≥ 10% of the WHOLE schema or
   > 10,000) is structurally unreachable in a schema dominated by retained
   unclaimed history. Weft never calls `vacuum()` outside the manual
   `weft system tidy` (`weft/commands/_tidy_support.py:14-21`).

Workstreams: **A** fixes 1+2 (repro-first), **B** fixes 3, **C** fixes 4,
**D** adds the self-healing maintenance slice that addresses 5 and makes
runtime-state pruning automatic. A is strictly first; B, C, D are mutually
independent after A.

Owner decisions already made (2026-06-10, recorded so the implementer does
not relitigate them): maintenance is **TaskMonitor-owned and default-on**
(opt-out via config), pong cleanup is **probe-layer consume + sweep** (no
ctrl_out TTL policy), and the live-system diagnostic budget was one STATUS
probe (already spent; its result is embedded above — do not probe
production again from this plan).

## Source Documents

Read in this order before starting:

- `CLAUDE.md` §1.1 (philosophy), §4 (house style), §5 (commands),
  §8 (boundaries). `AGENTS.md` is identical.
- `docs/agent-context/decision-hierarchy.md`,
  `docs/agent-context/principles.md`,
  `docs/agent-context/engineering-principles.md` (especially §2 "Queues Are
  the Canonical State", §4 real-broker tests, and the Secondary Rule
  "generator-based queue history reads").
- `docs/agent-context/runbooks/testing-patterns.md` and
  `docs/agent-context/runbooks/hardening-plans.md` — this plan changes
  cleanup lifecycles and deletion paths; it is risky by the repo's own
  trigger list.
- `docs/lessons.md` — the 2026-05-30 "Cleanup Progress Boundaries" lesson
  (FIFO `first_*_too_young` stop reasons) and the 2026-05-31 "Monitor
  Checkpoints And Status" lesson are directly adjacent to this work.

Governing specs:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-3] (control
  protocol, PONG payload; currently assigns no reply-retirement owner —
  this plan changes that) and [MF-5] (monitor collation/cleanup lifecycle,
  stale-service-owner dispositions, "Cleanup Boundary" section).
- `docs/specifications/07-System_Invariants.md` [OBS.13.x] (monitor
  cleanup ownership and exclusions), [OBS.16]/[OBS.17] (prune family
  boundaries and force semantics), [MANAGER.8] (keyed PONG liveness).
- `docs/specifications/01-Core_Components.md` [CC-2.4] (task-local control
  rounds).
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.2], [SB-0.3]
  (message IDs are durable ordered IDs Weft relies on — the exact-ID
  delete contract sits on this boundary).

Related plans (context, not normative):
`docs/plans/2026-05-28-stale-service-owner-runtime-cleanup-plan.md` (the
feature WS-B repairs), `docs/plans/2026-05-31-task-monitor-orphan-log-and-status-reconciliation-plan.md`,
and `docs/plans/2026-06-09-evaluation-findings-remediation-plan.md`
(separate, still-open remediation plan; no file overlap except
`weft/core/manager.py`, where that plan does not touch the probe paths).

## Production Evidence Pack (re-runnable)

The implementer cannot reach the production host and does not need to. For
traceability, these were the decisive read-only queries (Postgres schema
`weft`, project `/app/governance`); re-run them post-deploy to verify
healing (see Rollout):

```sql
-- raw rows by day (was: uniform ~1,300/day since manager start)
select to_timestamp(ts/1e9)::date d, count(*) from weft.messages
 where queue='weft.log.tasks' group by 1 order by 1;
-- refs never selected (was: 8741 / 0 / 0)
select count(*), count(selected_for_delete_at_ns), count(deleted_at_ns)
  from weft.weft_monitor_task_messages;
-- families marked raw-deleted while rows exist (was: 2071 / 2068)
select count(*), count(raw_deleted_at_ns) from weft.weft_monitor_task_collations;
-- the dead manager's orphaned pongs (was: 168)
select count(*) from weft.messages where queue='T1780096788952363008.ctrl_out';
-- claimed-but-undeleted rows anywhere (wakeup inbox was: 1835 claimed)
select queue, count(*) filter (where claimed) as claimed, count(*) as total
  from weft.messages group by queue having count(*) filter (where claimed) > 0
 order by 2 desc limit 5;
```

## Invariants and Constraints

What must NOT change:

- **Queues are the only source of truth.** No new state stores. The Monitor
  store remains a retention/audit read model, never task-state authority
  ([OBS.2], [OBS.13]).
- **Exact-ID-only destructive deletes.** The monitor must never delete
  broker rows except by exact message ID or by whole-queue deletion of
  proven-terminal/dead task-local queues (MF-5 "must not delete ...
  candidates without exact message IDs"). Nothing in this plan may
  introduce pattern- or age-based deletion of `weft.log.tasks` rows in
  `jsonl_then_delete` mode.
- **`raw_deleted_at_ns` may only mean "the broker rows for this family are
  actually gone."** This plan makes that an enforced invariant (WS-A); no
  task may weaken it back to "we believe they are gone."
- **Reserved-queue policy, TID immutability, forward-only state transitions**
  — untouched.
- **Manager control rows stay off-limits to generic cleanup** while the
  manager is live ([OBS.13.7], MF-5). WS-C retires *probe replies at the
  probe layer*; it does not let any cleaner iterate a live manager's
  ctrl_out.
- **Layer boundary with SimpleBroker.** If WS-A's repro shows the exact-ID
  no-op lives inside simplebroker-pg (not in weft's candidate plumbing),
  the fix belongs in SimpleBroker/simplebroker-pg — stop and report with
  the failing repro rather than working around it in weft
  (CLAUDE.md §1.1 "Respect the layer boundary").
- **No second execution or cleanup path.** WS-D extends the existing
  monitor builtin cycle; it must not spawn a new service, thread, or
  process for maintenance.
- **Single-reader control replies (new, WS-C):** the prober that sends a
  keyed PING owns the matched PONG's lifecycle. Other ctrl_out readers
  (terminal-envelope scanners) must remain unaffected — they ignore
  non-terminal shapes today and must continue to.

Review gates:

- Red repro before any deletion-path change (WS-A is repro-first by
  construction; do not reorder).
- Real-broker tests only for queue/lifecycle behavior; the PG-marked suite
  must exercise WS-A (this defect is invisible on a green SQLite-only run
  by hypothesis — see Task A1).
- No mocking of `apply_exact_prune_candidates`, broker reads/deletes, the
  Monitor store, or vacuum. `monkeypatch` only for env/config and time
  injection.
- Every task one commit; fresh-eyes self-review recorded; independent
  zero-context review before implementation (section at the end).

## File Map

- WS-A: `weft/core/pruning/apply.py`, `weft/core/monitor/task_monitor.py`,
  `weft/core/monitor/store.py`, `weft/core/monitor/sql.py`, possibly
  `weft/helpers/__init__.py` (exact-ID candidate plumbing) and
  `bin/pytest-pg` (schema-variant support if needed, per A1); tests: new
  `tests/core/test_pruning_apply.py` (A1) and extensions to
  `tests/tasks/test_task_monitor.py` (A2/A4/A5); spec 05 notes.
- WS-B: `weft/core/monitor/task_monitor.py` (one gate),
  `tests/tasks/test_task_monitor.py` (extend), spec 05 [MF-5] note.
- WS-C: `weft/core/control_probe.py`, `weft/core/manager.py`,
  `tests/core/test_control_probe.py` (extend or create),
  `tests/core/test_manager.py` (extend), spec 05 [MF-3] text,
  spec 07 [MANAGER.8] note.
- WS-D: `weft/core/monitor/task_monitor.py`, `weft/_constants.py`,
  `weft/core/monitor/runtime.py` (`TaskMonitorRuntimeConfig.from_config` —
  the typed config home), `weft/core/pruning/runtime.py` (reuse via
  `run_runtime_prune_for_context`; also fix its now-stale module docstring
  in D3), `weft/core/monitor/cleanup.py` (docstring only, D3); tests
  extend `tests/tasks/test_task_monitor.py` (real-broker fixtures live
  there); spec 07 [OBS.13.10] extension; spec 05 "Cleanup Boundary"
  update.
- Close-out: `CHANGELOG.md` (Unreleased), `docs/lessons.md` (one entry),
  `docs/plans/README.md` (this plan's row already exists; status flip on
  completion).

Environment setup and verification commands are identical to
`docs/plans/2026-06-09-evaluation-findings-remediation-plan.md` "Current
Repo State" (source `.envrc`, `uv sync --all-extras`, use `./.venv/bin/*`),
including its dirty-tree rules: the working tree may carry in-flight
status-reconciliation changes (`weft/commands/system.py`,
`tests/commands/test_status.py`) — leave them alone — and if this plan
file and its `docs/plans/README.md` row are not yet committed, commit them
first so task commits stay single-purpose. The Postgres-marked suite runs
via `bin/pytest-pg` (read its header before first use; it auto-provisions
a Dockerized Postgres). Baseline gates before starting: fast suite, mypy,
ruff — all green.

---

## Workstream A — Exact-row deletion correctness (repro-first)

**Critical context before A1 (verified 2026-06-10 during plan review):** the
closest existing lifecycle test —
`tests/tasks/test_task_monitor.py::test_task_monitor_jsonl_then_delete_emits_lifetime_report_before_delete`
(line ~1972) — already drives the full real `jsonl_then_delete` lifecycle,
asserts raw-row deletion, and **passes under `bin/pytest-pg`** with
production's exact package versions (simplebroker 4.3.0, simplebroker-pg
2.2.0). The defect therefore does NOT reproduce under the naive harness;
it is configuration-sensitive. The most conspicuous config delta:
production sets a custom Postgres schema (`backend_options.schema = weft`
via broker.toml / `WEFT_BACKEND_SCHEMA`), while `bin/pytest-pg` strips
`BROKER_BACKEND_SCHEMA` from the test environment (bin/pytest-pg:202-216).
A1/A2 below are therefore written as an **escalating-fidelity ladder**, and
"green everywhere" has an explicit terminal action. Do not skip rungs.

**Second fidelity clue (verified 2026-06-10):** production's
heartbeat/monitor service `ctrl_out` queues are EMPTY even though the
manager probes them every cycle and deletes matched pongs — via
`_delete_exact_probe_message`, which uses the SINGLE-message
`queue.delete(message_id=...)` API (`weft/core/manager.py`). Meanwhile the
broken monitor deletions route through `apply_exact_prune_candidates`,
which has BOTH a single-delete branch (`weft/core/pruning/apply.py:96`)
and a `delete_many` BATCH branch (apply.py:111). Single exact deletes
therefore demonstrably work in production under the custom schema; the
prime suspect is the batch branch. A1's matrix must parametrize delete
shape (single vs `delete_many`) alongside backend and schema; the
strongest red candidate is batch + Postgres + custom schema.

**Upstream-stop sequencing (binds the whole plan):** if A1/A2 end at the
SimpleBroker-layer stop condition (defect inside simplebroker/simplebroker-pg),
the other workstreams proceed as follows while the upstream fix is
pending: **B proceeds** (dispositions and control-queue cleanup use the
whole-queue `delete_from_queues` path, which production proves works) —
except its family-retirement convergence assertions, which depend on
working raw deletion and move behind A; **C proceeds** (consume/sweep
uses the single `queue.delete(message_id=...)` shape, proven working);
**D1 vacuum proceeds** (claimed-row deletion is backend-internal, not
exact-ID); **D2 pauses** until the backend fix lands and A1's matrix is
green, because its apply path is exactly the surface under suspicion.

### Task A1: Red repro — exact-ID delete against both backends and configs

- Outcome: a failing test that demonstrates `apply_exact_prune_candidates`
  (or the layer beneath it) reporting success/missing while the broker row
  survives, on at least one backend.
- Files: new test in `tests/core/test_pruning_apply.py` (create if absent;
  classify `pytest.mark.shared` AND register in `tests/conftest.py`
  `_SHARED_MODULES` — the audit-policy test requires it for `tests/core`).
- Read first: `weft/core/pruning/apply.py` (the
  `apply_exact_prune_candidates` contract: candidate shape, `report_only`,
  `reconcile_missing`, what `result.deleted`/`result.error` mean);
  `weft/helpers/__init__.py` `iter_queue_entries` (how message IDs are
  produced by scans); the simplebroker `Queue`/`delete` exact-ID API in
  `./.venv/lib/python3.14/site-packages/simplebroker/sbqueue.py` (the
  `delete_many`/exact-ID surface) and the pg plugin's implementation in
  `site-packages/simplebroker_pg/plugin.py`.
- The test, in outline (write real code following the `broker_env` patterns
  in `tests/commands/test_runtime_prune.py` and
  `tests/core/test_task_monitor_cleanup.py` — there are no
  `tests/core/test_pruning*` modules; do not invent one elsewhere):
  1. Write 3 messages to a real queue; collect their message IDs via the
     same read path production uses (generator peek with timestamps — NOT a
     hand-built ID).
  2. Build prune candidates exactly as
     `weft/core/monitor/task_monitor.py:3851-3856` does (the candidates
     are `MonitorRawMessageRef`, `weft/core/monitor/store.py:390-397`;
     `reconcile_missing=True`).
  3. Apply. Assert: every result has `deleted is True`; broker queue is
     empty afterward. Note the known trap this guards:
     `weft/core/pruning/apply.py:122-128` marks ALL candidates
     `deleted=True` after any non-raising `delete_many`, and SimpleBroker's
     `delete_many` ignores missing IDs without raising — so an ID-shape
     mismatch is silently absorbed.
  4. Parametrize THREE axes: backend (SQLite / Postgres via the `shared`
     marker + `bin/pytest-pg`); on Postgres a **custom schema** variant;
     and **delete shape** — one candidate (exercises the single
     `queue.delete(message_id=...)` branch, apply.py:96) vs three-plus
     candidates (exercises the `delete_many` batch branch, apply.py:111).
     Production evidence says single deletes work under the custom schema
     (the probe-cleanup path keeps service ctrl_out queues empty), so the
     strongest red candidate is batch + Postgres + custom schema. For the schema variant, first read how simplebroker-pg
     consumes `backend_options.schema` (broker.toml) and how
     `bin/pytest-pg` builds its env (`_build_test_env`, which strips
     `BROKER_BACKEND_SCHEMA` — bin/pytest-pg:202-216); configure the test's
     broker target with a non-default schema in-test rather than via the
     stripped env var if that is what it takes. If the harness genuinely
     cannot express a custom schema without modification, extending
     `bin/pytest-pg` minimally IS in scope for this task — say so in the
     commit.
- Expected: red on at least the Postgres+custom-schema variant. If red on
  more variants, better — proceed to A3 with the matrix recorded. If green
  on ALL variants, do not improvise: proceed to A2's ladder.
- Stop if: the failure is inside `simplebroker_pg` itself (e.g., its
  exact-ID delete mismatches the IDs its own peek returns). That is a
  SimpleBroker-layer bug: capture the minimal repro as a standalone script,
  report to the owner, and pin the weft-side expectation with an `xfail`
  carrying the upstream reference — do not fork backend behavior in weft.
- Commit policy: keep this red test uncommitted until Task A3's fix turns
  it green, then commit test and fix together in A3's commit. The only
  exception is the simplebroker-layer stop condition above, where the test
  is committed as `xfail` with the upstream reference in its reason string.

### Task A2: Red repro — full jsonl_then_delete lifecycle

- Outcome: a failing integration test driving the real TaskMonitor cycle
  in `jsonl_then_delete` mode that asserts the three invariants production
  violates.
- Files: extend `tests/tasks/test_task_monitor.py` (NOT
  `tests/core/monitor/` — that directory holds pure dataclass/builder unit
  tests with no broker or TaskMonitor; the real-broker fixtures live in
  `tests/tasks/test_task_monitor.py`: `broker_env` +
  `TaskMonitor(db_path, make_task_monitor_taskspec(...), config=...)` +
  `process_once()` / `drive_task_monitor_until_idle()` at :108).
- Read first: the exemplar to mirror is
  `test_task_monitor_jsonl_then_delete_emits_lifetime_report_before_delete`
  (:1972-2032) — it already proves the happy path; your job is to add the
  production-fidelity variants it lacks. Also read
  `weft/core/monitor/task_monitor.py` `_run_monitor_store_cycle` (lifecycle
  order: ingest → high-water → summaries → delete → repair → tombstones)
  and `_delete_monitor_store_task_log_rows`.
- Escalating-fidelity ladder (add variants in this order; stop at first
  red): (1) custom Postgres schema (per A1's mechanism); (2) multi-row
  families plus a monitor restart mid-family so pre-checkpoint recovery
  participates (production restarted June 4; the lifecycle first reached
  high-water June 8); (3) Postgres server-version parity (capture
  production's `SELECT version()` from the owner and match the container
  tag); (4) concurrent writer load during the delete cycle. If ALL rungs
  stay green on both backends: STOP and report the full matrix to the
  owner — the defect is then environment-specific in a way the plan's
  fidelity ladder cannot reach, the correct next step is owner-assisted
  live diagnosis (e.g., a second authorized STATUS probe or a
  staging clone), and manufacturing a fix without a red repro is forbidden
  (no fixes without root cause).
- The test: seed a real `weft.log.tasks` with a complete terminal task
  family (spawned/started/completed events — copy event payload shapes
  from an existing test), configure mode `jsonl_then_delete` with a tmp
  JSONL sink, run enough monitor cycles for the lifecycle to pass
  high-water and process the family, then assert ALL of:
  1. The raw broker rows for the family are deleted from `weft.log.tasks`.
  2. The family's collation row has `raw_deleted_at_ns` set ONLY at/after
     the cycle in which the rows were actually deleted (assert the broker
     was empty-for-tid when the mark appeared — practically: after the
     final cycle, `raw_deleted_at_ns` set AND rows gone; and a mid-test
     checkpoint asserts the mark is NOT set while rows still exist).
  3. The store refs for the family are removed, and
     `weft_monitor_deferred_writes` is empty.
- Expected red shape (one or more): rows survive while the mark is set
  (production's exact signature). Run on both backends as in A1.
- Stop if: you cannot get the lifecycle past high-water in a test without
  reaching into privates beyond the cycle entry point — read how existing
  tests drive cycles first; if none do, the smallest acceptable seam is
  calling `_run_monitor_store_cycle` directly with a constructed monitor
  (existing tests do reach comparable privates; mirror, don't invent).

### Task A3: Fix the silent no-op

- Outcome: A1 and A2 green on both backends; deletion actually deletes.
- Files: determined by A1/A2's verdict. Candidate defect sites, in
  likelihood order, each with the check to perform:
  1. **ID-shape mismatch at the weft↔backend seam**: the message IDs the
     monitor stores as refs vs the IDs the backend's exact delete expects
     (`weft/core/pruning/apply.py` candidate → `delete_many` argument
     path; compare with what `insert_messages`/peek return after 0.9.75's
     exact-insert alignment, commit `3e04985`). Fix: normalize at ONE
     boundary (engineering-principles §3), with a regression test asserting
     a peeked ID round-trips through exact delete on both backends.
  2. **`reconcile_missing` semantics**: `task_monitor.py:3857-3866` treats
     `result.error is None and not result.candidate.report_only` as
     reconciled even when `deleted is False`. Fix: only rows that were
     actually deleted OR positively verified missing
     (the apply layer must distinguish "missing" from "not attempted";
     extend `apply.py`'s result with an explicit `missing` field rather
     than inferring) may enter `reconciled_ids`.
  3. **Vacuous reconcile marking** — fixed structurally in A4 regardless.
- Constraints: minimal diff; do not refactor the lifecycle order; do not
  add retries (a failed exact delete must surface in
  `_last_collation_store_error` / policy progress, which the STATUS
  surface already reports — production showed null errors precisely
  because no error was raised; after this fix, a genuine failure must
  raise/record, never silently reconcile).
- Done when: A1+A2 green on SQLite and Postgres; full fast suite green;
  `./.venv/bin/python -m pytest tests/core/monitor tests/tasks/test_task_monitor.py -q` green.
- Commit: `Fix exact-row deletion no-op in monitor lifecycle` (include the
  A1/A2 tests in this commit).

### Task A4: Enforce the raw_deleted invariant at the marker

- Outcome (stated at the strength the implementation can actually deliver):
  a family may be marked `raw_deleted_at_ns` only when ALL of: (i) its
  known refs were verifiably deleted (per-row `deleted is True`, via A3's
  filtering — no missing-reconciled rows); (ii) the family is
  summary-eligible (terminal AND `summary_emitted_at_ns` set, per A5's
  mode invariant); and (iii) the marking cycle completed ingest high-water
  (`completed_fifo_high_water` true — the checkpoint reached the live
  head, so no unrecorded rows for the family can hide behind it). A
  direct broker-absence check per tid was considered and rejected as the
  RUNTIME mechanism — `weft.log.tasks` has no per-tid index at the broker
  layer, so it would be a full body-scan per family — but it IS the
  required assertion method in this task's TESTS, which verify by
  scanning the real queue.
- Files: `weft/core/monitor/store.py` (`delete_task_messages_after_raw_delete`,
  `prune_deleted_task_message_tombstones` — the only reconcile callers,
  store.py:2066/2104), `weft/core/monitor/sql.py`
  (`reconcile_raw_deleted_tasks*`), tests in A2's module.
- Read first: the reconcile SQL (`NOT EXISTS refs` predicate, sql.py:739-771)
  and the callers' transaction shape.
- Design reality check (so this is not built twice): the practical
  implementation of "only mark verified-deleted families" is the CALLER
  filtering which message_ids it passes to
  `delete_task_messages_after_raw_delete` — which is the same change as
  Task A3's fix candidate #2 viewed from the marker side. Implement the
  filtering once in A3; Task A4 then contributes (a) the marker-side tests
  below and (b) one additional guard inside
  `delete_task_messages_after_raw_delete`: reconcile only the tids whose
  refs were actually deleted in that chunk's store transaction (note the
  broker deletes and the store ref-deletes are SEPARATE transactions —
  apply.py's broker txn vs store.py:2059-2069's store txn — so "same
  transaction" can only refer to the store side). A per-tid
  `max(chunk ref ids) >= c.last_message_id` strengthening is approximately
  right only because the global ascending `ORDER BY message_id` puts a
  family's highest ids in its last chunk; if you reach for it, document
  that ordering dependency in the guard's docstring. If any of this turns
  out to require schema additions, STOP and propose to the owner instead.
- Tests (red first against the pre-A4 code by temporarily reverting A3's
  reconcile change if needed — say so in the test docstring if the red
  state is only reachable pre-A3): (a) a family with un-ingested rows is
  never marked; (b) a family whose exact delete reported missing-but-present
  is never marked; (c) a cycle that did NOT complete high-water (e.g.,
  scan limit reached) marks nothing, even for families whose refs were
  deleted that cycle. Each test's final assertion scans the real
  `weft.log.tasks` queue for the family's rows (the broker-absence check
  lives in tests, per the Outcome note).
- Commit: `Guard raw-deleted marking against vacuous reconciliation`

### Task A5: Orphan recovery heals pre-existing damage

- Outcome: deployments already carrying marked-but-undeleted families
  (production: 2,068 families, ~8.7k raw rows) converge to clean state
  automatically after upgrade — this is the self-healing path for existing
  damage; no manual SQL.
- Files: `weft/core/monitor/task_monitor.py` (the orphan-recovery slice —
  `list_raw_deleted_task_log_recovery_tids` consumers around
  task_monitor.py:4028-4233 and `_last_orphan_task_log_recovery`),
  `weft/core/monitor/sql.py:571-600`; tests in A2's module.
- Read first: the marked-WITH-refs combination is already covered —
  `_repair_raw_deleted_task_message_refs` (task_monitor.py:3889, invoked
  every destructive lifecycle pass at :2023-2025, fed by
  `select_raw_deleted_task_message_refs` sql.py:552-568) re-drives marked
  families' refs through the exact-delete path, so once A3 makes deletion
  real, production's damage converges WITHOUT new recovery code. The
  marked-with-NO-refs orphan slice (`_recover_orphan_task_log_rows`,
  task_monitor.py:3954; selection sql.py:571 requires `NOT EXISTS refs`)
  is disjoint in SELECTION DOMAIN only — it shares the same audit gap:
  its eligibility condition is an OR (`terminal_seen = 1 OR suspect_reason
  IS NOT NULL OR disposition_at_ns IS NOT NULL OR summary_emitted_at_ns IS
  NOT NULL`, sql.py:585-590), so a terminal-but-unsummarized vacuously
  marked family qualifies and its raw rows can be deleted before any JSONL
  export. This task is therefore: (a) the convergence tests below, and
  (b) ONE mode-level invariant applied to BOTH paths: **in
  `jsonl_then_delete`, no raw task-log row of a family may be deleted
  unless that family's `summary_emitted_at_ns` is set** (the main path
  already enforces this via `require_summary`, sql.py:504-506). Implement
  by adding the summary condition in jsonl mode to (b1) the repair
  selection (`select_raw_deleted_task_message_refs`, sql.py:552-568) and
  (b2) the orphan-recovery selection (sql.py:571-599) — for both,
  unsummarized marked families are terminal, so the normal summary stage
  picks them up first and the deletion path proceeds on a later pass.
  Do not instead make the recovery slices emit summaries themselves —
  summary emission has one owner (`_emit_monitor_store_summaries`); adding
  a second emitter is the kind of dual-path the invariants forbid.
- Tests (the deployment simulation, red first where reachable): construct
  the production state shape directly — terminal family, summary emitted,
  collation marked `raw_deleted_at_ns`, refs present, raw broker rows
  present — run monitor cycles, assert full convergence (rows deleted,
  refs deleted) within a bounded number of cycles, then idempotence
  (further cycles are no-ops). Second case (the audit-promise pin, REPAIR
  path): a marked family WITH refs but WITHOUT a summary first receives
  its summary/JSONL export, and only then loses its raw rows — assert the
  JSONL sink received the family before the rows disappeared. Third case
  (the audit-promise pin, ORPHAN path): a marked, terminal, unsummarized
  family with NO refs and raw rows present — same assertion: summary/JSONL
  first, rows deleted only on a later pass.
- Commit: `Converge marked-but-undeleted task-log families safely`

### Task A6: Spec + ledger for WS-A

- Update `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: the
  `_Implementation mapping_` notes for the lifecycle gain one sentence
  stating the enforced `raw_deleted_at_ns` invariant and the recovery
  slice's coverage of marked-with-refs families. Add this plan to
  `## Related Plans` (format: backtick-path link, matching existing rows).
- Add a dated entry to `docs/lessons.md`: failure mode ("a completion
  marker whose writer can be satisfied vacuously will eventually lie;
  deletion markers must be coupled to verified deletion in the same
  transaction") and the rule, linking [MF-5] and this plan.
- Run `./.venv/bin/python -m pytest tests/specs/ -q`; commit
  `Document raw-deleted invariant and recovery in MF-5`.

## Workstream B — Dead-incarnation cleanup in all destructive modes

### Task B1: Red test — superseded manager in jsonl_then_delete

- Files: extend `tests/tasks/test_task_monitor.py` (find the existing
  stale-service-owner disposition tests — grep `stale_service_owner` — and
  mirror their SETUP only). **Trap, do not fall in it:** the existing
  delete-mode test (`test_task_monitor_disposes_old_stale_service_owner_collation`,
  :3926) calls `task._emit_monitor_store_summaries(..., apply_disposition=True)`
  directly, passing the flag explicitly — which bypasses the very
  `mode == "delete"` gate this workstream fixes. A literal mirror would be
  green before the fix and prove nothing. Your test must drive the real
  entry point — `process_once()` / the cycle wrapper that reaches
  `_run_monitor_store_cycle` — so the gate is actually exercised.
- The test: same scenario as the existing delete-mode disposition test but
  with mode `jsonl_then_delete` and the real cycle entry point; assert the
  superseded owner's family gets
  `disposition_reason` in `{stale_service_owner, superseded_service_owner}`,
  its `T{tid}.ctrl_in`/`ctrl_out` queues are deleted, and the family
  becomes retirable. Expected: red (disposition never written).

### Task B2: Fix the gate

- Files: `weft/core/monitor/task_monitor.py` — in
  `_run_monitor_store_cycle`, change
  `apply_disposition=(self._monitor_config.mode == "delete" and task_log_owner == "collated_store")`
  to use `self._destructive_mode_enabled()` instead of the literal mode
  check. Read the disposition consumer chain first
  (`_emit_monitor_store_summaries` → `_run_terminal_control_cleanup_slice`
  → `stale_service_owner_runtime_queue_cleanup_plan` in
  `weft/core/monitor/policies/runtime_control.py:247`) to confirm no other
  mode gate blocks downstream — if you find a second literal
  `mode == "delete"` on this chain, fix it the same way and add it to the
  test's assertions; if you find one with a comment explaining why
  jsonl_then_delete is excluded, STOP and report (none was found in the
  2026-06-10 investigation, but verify).
- Done when: B1 green on both backends; the existing delete-mode
  disposition tests stay green; fast suite green.
- Spec: [MF-5] already specifies this behavior — update only the
  `_Implementation mapping_` note if it names the mode restriction; add the
  plan backlink (shared with A6's edit; do both in whichever lands second).
- Commit: `Enable service-owner dispositions in all destructive monitor modes`

## Workstream C — PONG retirement at the probe layer

### Task C1: Red test — keyed probe consumes its matched pong

- Files: `tests/core/test_control_probe.py` (extend if present; else
  create, classify, register per A1's note). Real broker; a fake responder
  is acceptable ONLY as a queue-writer (write a pong payload built with the
  real shape into the real ctrl_out queue) — do not mock the probe
  internals.
- Test: send a keyed probe at a queue pair where a matching pong (same
  `request_id`) is written; assert probe reports matched AND the pong row
  is gone from ctrl_out afterward. Second case: a NON-matching pong and a
  terminal-envelope-shaped message in the same queue survive untouched
  (the single-reader rule must not eat other readers' messages). Expected:
  red on the first assertion (probe is peek-only today).

### Task C2: Implement consume + sweep

- Files: `weft/core/control_probe.py` (`send_keyed_ping_probe`),
  `weft/core/manager.py` (`_advance_manager_pong_probe` abandonment at
  :2165-2177 and timeout at :2318-2328; `_advance_service_pong_probe`
  equivalents around :4584-4746).
- Design:
  - `send_keyed_ping_probe`: on match, delete the matched message by exact
    message ID (the peek generator already yields timestamps/IDs — reuse
    them; do NOT switch the read to claim semantics, which would claim
    unrelated messages). On timeout, perform one final sweep pass deleting
    any row matching this probe's `request_id` before returning (covers
    the pong-arrived-after-last-peek window). Update the docstring's
    "without consuming output" contract to the new single-reader contract.
  - Manager probes: on abandonment and on timeout-completion, sweep the
    target ctrl_out for the pending probe's `request_id` and delete
    matches (mirror the existing `_delete_exact_probe_message` call used
    on the matched path, manager.py:2314).
  - Residual: a pong arriving after the final sweep still leaks one row;
    WS-B's dead-incarnation cleanup and task-exit purge bound its lifetime.
    State this in the [MF-3] spec text (C3) — it is a documented residue,
    not a silent one.
- Tests: C1 green; manager-side red-green in `tests/core/test_manager.py`
  mirroring the existing pong-probe tests (grep `_pong_dispatch_proof` /
  `pong_probe` for the existing fixtures): abandoned probe leaves ctrl_out
  empty when the pong arrived pre-abandonment.
- Constraint: `weft/core/manager.py` is also touched by the open
  2026-06-09 remediation plan (different regions). Coordinate by keeping
  this change strictly inside the probe-advance functions.
- Commit: `Retire matched and abandoned keyed pongs at the probe layer`

### Task C3: Spec updates for the reply-retirement contract

- `docs/specifications/05-Message_Flow_and_State.md` [MF-3]: add the
  single-reader rule — keyed control replies are owned and retired by the
  prober (matched: deleted on match; unmatched: swept at timeout/abandon;
  truly-late replies are bounded by task-exit purge and terminal/dead-TID
  cleanup). `docs/specifications/07-System_Invariants.md` [MANAGER.8]:
  one sentence that pong-proof probes retire their replies. Backlinks per
  A6 format. `./.venv/bin/python -m pytest tests/specs/ -q`; commit
  `Specify keyed-reply retirement ownership`.

## Workstream D — TaskMonitor-owned self-maintenance (default-on)

### Task D1: Periodic backend vacuum from the monitor cycle

- Outcome: claimed rows get physically deleted on a bounded cadence with
  no operator action; the 1,835-row wakeup backlog class disappears.
- Files: `weft/_constants.py` (new constants),
  `weft/core/monitor/runtime.py` (typed config),
  `weft/core/monitor/task_monitor.py` (builtin cycle); tests extend
  `tests/tasks/test_task_monitor.py` (where the real-broker monitor
  fixtures live).
- Constants (mirror house style — Final + docstring):

```python
WEFT_TASK_MONITOR_MAINTENANCE_ENABLED_DEFAULT: Final[bool] = True
"""Self-maintenance (backend vacuum + runtime-state prune) in the monitor.

Default-on by owner decision (2026-06-10): deployments should not need
external cron jobs for routine broker hygiene. Opt out with
WEFT_TASK_MONITOR_MAINTENANCE=0.
"""

WEFT_TASK_MONITOR_MAINTENANCE_INTERVAL_SECONDS: Final[float] = 3600.0
"""Minimum seconds between monitor self-maintenance passes (vacuum + prune).

A wall-clock deadline, not a cycle count: monitor cycles run every 2s
during catch-up (WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS_DEFAULT), so
cycle counting would make the cadence load-dependent. Vacuum deletes
claimed rows; SimpleBroker's own auto-vacuum threshold is schema-global
and unreachable in schemas dominated by retained unclaimed history, so the
monitor invokes it explicitly. The PG backend's vacuum is advisory-locked
and safe while writers are active.
"""
```

  Wire `WEFT_TASK_MONITOR_MAINTENANCE` through `load_config()` following
  the existing `WEFT_TASK_MONITOR_*` parsing block in `_constants.py`
  (grep `WEFT_TASK_MONITOR_MODE` there for the pattern, including the
  override-type validation), AND through the typed
  `TaskMonitorRuntimeConfig.from_config` in `weft/core/monitor/runtime.py`
  — that is where monitor config fields live (house pattern; the file is
  in this workstream's File Map).
- Implementation: cadence via the existing monotonic next-due-deadline
  pattern — mirror `_next_runtime_cleanup_queue_discovery_due_monotonic`
  (`weft/core/monitor/task_monitor.py:1968`); there is NO cycle counter in
  the monitor and this task must not add one. When due, call the broker
  vacuum via the context (`with self._monitor_context().broker() as
  broker: broker.vacuum()` — confirm the surface by reading
  `weft/commands/_tidy_support.py:14-21`, the existing caller; do not pass
  `compact=True` — compaction stays with the operator command).
- **STATUS/policy contract (binding for D1 and D2):** spec 05 fixes the
  monitor's reporting at exactly five top-level cleanup policies and
  forbids new `policy_progress[*].policy` identities
  (05-Message_Flow_and_State.md:1037 area — read it). Maintenance
  therefore reports through a NEW top-level non-policy STATUS block, not
  through policy_progress:

```text
"maintenance": {
  "last_run_at_ns": <int|null>,
  "vacuum_ok": <bool|null>,
  "runtime_prune": {"candidates": N, "deleted": N, "partial_batches": N},
  "last_error": <str|null>
}
```

  Failures are best-effort: record to `maintenance.last_error`, never fail
  the cycle (mirror how `_last_collation_store_error` is cached and
  surfaced). The rejected alternative — a sixth policy identity plus a
  spec change to the five-policy contract — was considered and declined:
  maintenance is not a queue-scan policy with scan/select/base semantics,
  and external consumers parse policy_progress by the closed identity set.
  D3 documents this block in spec 05's STATUS/diagnostics section.
- Test (red first): real broker; write+claim rows on a queue (use
  `read_one` like production); run cycles past the maintenance deadline
  with time injection (existing monitor tests inject `now_ns` — mirror);
  assert physical row count reaches zero and the STATUS payload contains
  the `maintenance` block with `vacuum_ok` true and `last_run_at_ns` set —
  and does NOT contain any new `policy_progress[*].policy` identity.
  Parametrize both backends.
- Commit: `Vacuum claimed rows from the monitor maintenance slice`

### Task D2: Runtime-state auto-prune in the maintenance slice

- Outcome: superseded runtime-state rows in the groups the monitor does
  NOT already own — explicitly `managers`, `services`, `streaming`,
  `endpoints`, `pipelines`, and explicitly EXCLUDING `tid-mappings`
  (already pruned every cycle by the monitor's own policy at a different
  min-age, `weft/core/monitor/cleanup.py:71-77`; running both would
  double-scan the same queue) — are pruned automatically with the same
  conservative rules as `weft system prune --family runtime-state`
  defaults.
- Files: `weft/core/monitor/task_monitor.py`,
  `weft/core/pruning/runtime.py` (reuse, read-only), tests in D1's module.
- Read first: the reuse seam is `run_runtime_prune_for_context(ctx, config)`
  (`weft/core/pruning/runtime.py:198`) — note the planners are NOT pure
  functions (they perform broker reads through ctx); what makes them safe
  to reuse is that their apply path already routes through the
  exact-ID `apply_exact_prune_candidates` WS-A fixes. Defaults to
  preserve: `min_age=3600`, `keep_recent_per_key=1`. Spec [OBS.16]
  (runtime-state pruning never touches task-local/log/manager queues).
- Implementation: on the D1 deadline (same enabled flag), call
  `run_runtime_prune_for_context` with an explicit queue-group config for
  the five groups above. Concurrency note: an operator running
  `weft system prune` concurrently is benign (exact-ID deletes are
  idempotent), but the partial-batch outcome
  (`apply.py:143-154`, "batch delete removed N of M exact rows") is
  informational: increment `maintenance.runtime_prune.partial_batches`
  (D1's STATUS block), never write it to `maintenance.last_error`, and
  never surface it as a policy_progress identity. **Sequencing dependency:
  if WS-A ended at the upstream stop condition, this task PAUSES until the
  backend fix lands and A1's matrix is green** (see "Upstream-stop
  sequencing" in WS-A) — D1's vacuum half proceeds regardless.
- Test (red first): seed superseded service-owner registry rows older than
  min-age plus one fresh row per key; run maintenance; assert superseded
  rows deleted, newest-per-key retained, and TWO decoys survive: a
  task-local `T{tid}.ctrl_out` row, AND a live owner's streaming/endpoint
  row whose owner has a fresh tid-mapping (the streaming classifier treats
  "no log status AND no tid-mapping" as prunable — `owner_has_no_live_proof`
  in `weft/core/pruning/runtime.py` — and WS-A makes log trimming real, so
  live-owner protection now rests on the newest mapping surviving; pin it).
- Commit: `Auto-apply conservative runtime-state pruning in the monitor`

### Task D3: Spec, config docs, and ledger for WS-D

- `docs/specifications/07-System_Invariants.md` [OBS.13.10]: extend the
  monitor's cleanup-ownership statement with the maintenance slice
  (vacuum + runtime-state prune, default-on, config opt-out, cadence
  constant named). Fix the two module docstrings WS-D makes stale:
  `weft/core/pruning/runtime.py` ("the manager-supervised TaskMonitor uses
  a separate cleanup runner") and the equivalent line in
  `weft/core/monitor/cleanup.py` — both must describe the new shared
  ownership. `docs/specifications/05-Message_Flow_and_State.md`
  "Cleanup Boundary": replace "task-owned unless an operator explicitly
  invokes foreground system pruning" with the three-owner model
  (task-owned exit purge; monitor-owned lifecycle + maintenance; operator
  commands for force/compaction); in the same spec's STATUS/diagnostics
  section, document the new top-level `maintenance` block (field names per
  D1) and state explicitly that the five-policy `policy_progress` contract
  is unchanged — maintenance is a non-policy block by design. README: one
  paragraph under the
  task-monitor section documenting `WEFT_TASK_MONITOR_MAINTENANCE` and
  that no external cron is needed. Backlinks; `tests/specs/` green.
- CHANGELOG `## Unreleased`: entries for A (fixed silent no-op +
  self-healing recovery), B (dispositions in all destructive modes), C
  (pong retirement), D (default-on maintenance; note the behavior change:
  upgraded deployments will begin deleting claimed rows and superseded
  runtime-state rows automatically).
- Commit: `Document monitor self-maintenance ownership`

## Testing Plan (summary)

| Task | Proof | Must NOT mock |
|------|-------|---------------|
| A1 | red: exact-ID delete no-op on PG; green after A3 | apply path, broker |
| A2 | red: marks-without-deletion lifecycle; green after A3/A4 | monitor store, broker, cycle |
| A4 | red: vacuous mark cases | reconcile SQL path |
| A5 | red: production-state simulation converges | recovery slice |
| B1 | red: no disposition in jsonl_then_delete | disposition chain |
| C1/C2 | red: pong survives probe; green: matched+swept, bystanders intact | probe, broker |
| D1 | red: claimed rows persist; green: vacuumed on cadence | vacuum, broker |
| D2 | red: superseded rows persist; green: pruned, newest kept, decoy safe | planners, apply path |

Cross-cutting: every new test module needs `pytestmark =
[pytest.mark.shared]`; additionally, new TOP-LEVEL `tests/core/test_*.py`
modules (Task A1's `tests/core/test_pruning_apply.py`) must be registered
in `tests/conftest.py` `_SHARED_MODULES` — the audit-policy test enforces
the registry only for that top level (subdirectory modules need the marker
only). WS-A/B/D tests must run under the PG matrix
(`bin/pytest-pg`) — the headline defect is invisible on SQLite by
hypothesis; if A1 proves it backend-independent, note that and continue.
Time-dependent assertions use injected `now_ns`, never sleeps.

## Verification and Gates

Per-task commands inline. Final gates:

```bash
./.venv/bin/python -m pytest -q
bin/pytest-pg tests/core/test_pruning_apply.py tests/tasks/test_task_monitor.py
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

(`bin/pytest-pg` accepts positional targets only — no `-q`; it
auto-provisions a Dockerized Postgres.)

Rollout (governance/ops): deploy the new release; no config change needed
(`jsonl_then_delete` stays). Within ~24h, re-run the Production Evidence
Pack queries and expect: raw `weft.log.tasks` rows converging to the
retention window instead of monotonic growth; the
`weft_monitor_task_messages` working set shrinking toward
in-flight-families-only (do NOT use `selected_for_delete_at_ns` as a
signal — it is a writer-less legacy column and stays 0 in healthy
operation); marked-family count consistent with actually-deleted rows;
the dead manager's 168-pong queue empty (disposition path); claimed
wakeup rows ≈ one maintenance-interval's worth; tid-mappings ≈
steady-state. Rollback: each workstream reverts independently. One-way
door, stated honestly: A's repair path deletes broker rows of families
that were already MARKED, and for vacuously-marked families those rows
were never exported to JSONL — the A5 summary-guard exists precisely so
nothing is deleted before its JSONL export; reviewers should check that
guard first. Post-deploy observation owner: the repo owner
(single-operator deployment).

## Independent Review Loop

External review required (cleanup lifecycles, one-way deletes, default-on
behavior change). Reviewer: different agent family when available, else a
fresh zero-context session. Reviewer reads: this plan; CLAUDE.md §1.1/§4;
spec 05 [MF-3]/[MF-5] and 07 [OBS.13]/[OBS.16]/[OBS.17];
`weft/core/monitor/task_monitor.py` (`_run_monitor_store_cycle`,
`_delete_monitor_store_task_log_rows`), `weft/core/monitor/store.py`
(reconcile callers), `weft/core/control_probe.py`, and
`weft/core/pruning/apply.py`. Prompt: the standard runbook prompt
("Could you implement this confidently and correctly if asked?").
Dispositions recorded below.

## Out of Scope

- **High-water catch-up acceleration.** The 4-day post-restart stall before
  the lifecycle first reached high-water is real but separate; this plan
  only ensures the lifecycle is correct once it runs. Diagnosing the
  catch-up dynamics (scan-limit vs cadence) is a follow-up.
- **ctrl_out TTL backstop** — declined by owner (probe-layer only).
- **SimpleBroker global vacuum-threshold redesign** — optional upstream
  improvement; D1 makes it moot for weft deployments. If A1 hits the
  simplebroker-pg stop condition, that upstream fix becomes its own work.
- **The 2026-06-09 evaluation remediation plan** — still open,
  independent; do not fold its tasks in here.
- **Manager-registry interpretation consolidation and double-fork
  containment** — unchanged from that plan's out-of-scope list.

## Fresh-Eyes Review Record

- **2026-06-10, author pass 1** (post-draft): fixed A1's self-contradictory
  commit guidance and the malformed final query in the Evidence Pack.
- **2026-06-10, independent zero-context review** (fresh agent; verified
  every cited file:line against the repo AND empirically ran the closest
  existing lifecycle test under `bin/pytest-pg`). 14 findings, all
  addressed in this revision. The blocker reshaped Workstream A: the
  existing jsonl_then_delete lifecycle test PASSES on Postgres with
  production's exact package versions, so the expected easy red was
  removed, the production/test schema delta (`BROKER_BACKEND_SCHEMA=weft`
  vs the harness stripping it, bin/pytest-pg:202-216) became the primary
  repro axis, and A1/A2 became an escalating-fidelity ladder with an
  explicit all-green stop-and-report terminal (no fixes without a red
  repro). Other corrections: B1 now warns that the existing disposition
  test bypasses the gate under fix (apply_disposition passed explicitly)
  and must drive the real cycle entry point; A2/D-test placement moved to
  `tests/tasks/test_task_monitor.py` (tests/core/monitor/ holds only
  dataclass unit tests); A1's mirror modules corrected to ones that exist;
  the `-q` flag removed from the bin/pytest-pg gate (unsupported); the
  rollout signal replaced (`selected_for_delete_at_ns` is a writer-less
  legacy column — also reframed in the Goal evidence); D1 cadence switched
  from cycle-counting (no counter exists; catch-up cycles run every 2s) to
  the house monotonic-deadline pattern with the typed config home
  (`TaskMonitorRuntimeConfig.from_config`) added to the File Map; D2 scoped
  to the five non-tid-mapping queue groups via the real reuse seam
  (`run_runtime_prune_for_context`) with the partial-batch outcome treated
  as informational; A4 collapsed into A3's caller-side filtering plus
  marker-side tests, with the chunk-ordering caveat documented; A5
  redirected to the already-existing repair slice plus the real gap it
  found (no summary condition on repair selection — the jsonl audit
  promise), with the rollback claim corrected accordingly; live-owner
  streaming/endpoint decoy added to D2's test; stale module docstrings
  folded into D3; the registration rule softened to top-level tests/core
  only; dirty-tree/commit-the-plan-first preflight added; minor line-number
  drift corrected.
- Reviewer's closing verdict: "qualified no" as drafted, implementable
  after the Finding-1 revision — which this revision incorporates.
- **2026-06-10, repo-owner review** (4 findings, all verified against the
  code and addressed):
  [P1] A5's JSONL guard covered only the repair path while the
  orphan-recovery selection's OR-condition (sql.py:585-590) admits
  terminal-but-unsummarized marked families — fixed by promoting the guard
  to a mode-level invariant ("in jsonl_then_delete, no raw row deletion
  without summary_emitted_at_ns") applied to BOTH selections, with a third
  convergence test pinning the orphan path, and an explicit prohibition on
  recovery slices emitting summaries themselves.
  [P1] WS-D's STATUS reporting was ambiguous against spec 05's closed
  five-policy `policy_progress` contract — fixed by defining a top-level
  non-policy `maintenance` block (field schema in D1), recording the
  rejected sixth-policy alternative, asserting the no-new-identity rule in
  D1's test, and adding the spec documentation to D3.
  [P2] The upstream stop condition lacked cross-workstream sequencing —
  added "Upstream-stop sequencing" to WS-A: B proceeds minus
  retirement-convergence assertions, C proceeds (single-delete shape,
  proven in production), D1 vacuum proceeds (backend-internal), D2 pauses;
  verification of this finding also produced a new fidelity clue, now in
  WS-A's critical context and A1's matrix: production's empty service
  ctrl_out queues prove single `queue.delete(message_id=...)` works under
  the custom schema, narrowing the prime suspect to the `delete_many`
  batch branch (apply.py:96 vs :111).
  [P2] A4's outcome overpromised relative to its ref-based guard — restated
  at deliverable strength (verified per-row deletion + summary eligibility
  + completed high-water), with the per-tid broker-absence check rejected
  as a runtime mechanism (no tid index over log bodies) but required as
  the tests' assertion method, and a third test case covering
  non-high-water cycles.
  The owner's same-day ops re-check confirmed the plan premise is stable
  (0.9.75 / simplebroker 4.3.0 / simplebroker-pg 2.2.0 / schema `weft` /
  jsonl_then_delete; 8,809 raw rows, 8,793 refs, 2,068/2,071 marked
  families, 168 dead-manager pongs, 1,854 claimed inbox rows).
- External different-family review (per the loop section): not yet run;
  the plan stays `draft` until the owner triggers or waives it.
