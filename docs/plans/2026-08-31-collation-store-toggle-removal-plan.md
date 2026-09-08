# Collation Store Toggle Removal Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.3]; docs/specifications/01-Core_Components.md [CC-2.3]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4a]; docs/specifications/00-Quick_Reference.md (environment table)
Superseded by: none

Class: 5 — removes normative text describing the disabled-store mode and
the `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED` environment row; deletes a
cleanup execution path (queue-cleanup lifecycle → risky trigger, so the
hardening checklist applies). Plan type: implementation with spec
revision. Promotion strategy: **A** for [MF-5]; **D** for the Quick
Reference row. Program ledger:
[2026-08-31-guard-and-custody-simplification-plan.md](./2026-08-31-guard-and-custody-simplification-plan.md).

## 1. Goal

The window-scan task-log cleanup engine (`weft/core/monitor/cleanup.py`,
`policies/task_log.py`, `policies/reserved.py`, `task_log_collation.py`,
and the family-selection half of `task_log_scanner.py`) is the monitor's
original 2026-05-07 deleter, kept after the durable collation store landed
(2026-05-16) as rollback insurance behind `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED=0`.
Verified on 2026-08-31:

- Under defaults it is invoked every cycle with
  `task_log_cleanup_enabled=False` (task_monitor.py:5834) and does nothing
  but emit a `skipped_owner` stat.
- With the store disabled, `_run_monitor_store_cycle` returns `False`
  (:2460–2462), which is the only thing that ever sets
  `runtime_cleanup_ready = True` (:5265), which is the only gate on the
  runtime cleanup worker (:5594–5597). So **no terminal-control, reserved,
  or dead-TID cleanup runs at all** in that mode; only the old engine's
  task-log retention and the maintenance pass do. No behavioral test
  covers the mode (`tests/core/test_task_monitoring.py:645` is config
  parsing).
- If the store is *unavailable* (not disabled), [MF-5] already says rows
  "remain visible rather than falling back to the old family-window
  deleter" — the engine is not failover.
- `report_only` is the spec-named non-destructive override ([MF-5]: "does
  not delete, reserve, move, prune, reap, acknowledge, or unclaim rows");
  in it collation continues (`store.record_task_log_updates` runs
  unconditionally; the checkpoint advances) and only deletion stops. The
  earlier rollout gate `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED` was already
  removed in favor of it (`_constants.py:3323–3325`).

Van, 2026-08-31: the TaskMonitor's purpose is collation and deletion; a
mode in which that does not happen is not a design to keep. Remove the
toggle; the store is always on; remove the engine.

## 2. Source Documents

- 05 [MF-5] lines ~1424–1428: "Disabling
  `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED` leaves the tables in place
  and removes them from the monitor cycle. … `report_only` is the
  non-destructive override. If the store is unavailable, well-formed
  task-log rows remain visible rather than falling back to the old
  family-window deleter."
- 05 [MF-5] lines ~366–370: PONG `extended.task_monitor` includes "cached
  queue-level cleanup stats and cached policy-level cleanup stats under
  `cleanup_policy_stats`" for built-in processors.
- 00-Quick_Reference line ~194: the env-var row.
- 07 [OBS.13]: reserved/dead-TID cleanup ownership (unchanged).
- Origin plan (historical): [2026-05-16-monitor-durable-collation-store-plan.md](./2026-05-16-monitor-durable-collation-store-plan.md)
  (its rollback section is what this plan retires).

## 3. Context and Key Files

Delete entirely (engine-only, verified by symbol grep):
- `weft/core/monitor/cleanup.py` (398 lines; exports consumed only by
  `task_monitor.py`)
- `weft/core/monitor/policies/task_log.py` (557; consumed only by
  `cleanup.py`; contains a second `decode_task_log_row` :489 that has
  drifted from the scanner's :196 — no malformed classification)
- `weft/core/monitor/policies/reserved.py` (173; reachable only from tests)
- `weft/core/monitor/task_log_collation.py` (149; `CollatedMessageGroup`/`is_terminal_task_log`
  used only inside the scanner's family-selection half;
  `collate_next_task_log_group` used only by a test)
- In `weft/core/monitor/task_log_scanner.py`: `select_task_log_family_groups`
  (:225), `TaskLogFamilySelection` (:89), `TaskLogSkippedFamilySummary`
  (:65), private helpers :306–462. **Keep** `TaskLogScanWindow` (:39),
  `GeneratorTaskLogScanner.scan_window` (:148), `decode_task_log_row`
  (:196) — live in store ingest (:2588), pre-checkpoint recovery (:2772),
  raw external emission (:6022). `TaskLogScanBackend` (:128): delete if no
  consumer remains after the engine goes (grep).
- `weft/_constants.py`: `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED_DEFAULT`
  (:1134), loader (:2994–2996), override rule (:3255) → replace with an
  `_OverrideRule(kind=_OverrideKind.REMOVED, removed_message="WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED was removed; the collation store is always enabled — use WEFT_TASK_MONITOR_MODE=report_only to disable destructive cleanup")`
  (pattern at :3323–3325); `TASK_MONITOR_TASK_LOG_CLEANUP_SKIPPED_OWNER`.
- `weft/core/monitor/task_monitor.py`: `_task_log_deletion_owner`
  (:2349–2360 → `raw_external` if raw external, else `collated_store`;
  verify the `custom` mode path via `_custom_processor_enabled` is
  unaffected), `_ensure_monitor_store` disabled branch (:2365–2370),
  config field `collation_store_enabled` (:1390, :1652 diagnostics),
  `_run_builtin_monitor_processor_cycle` / `_run_task_monitor_cleanup_cycle`
  (:5830–5960 — the engine invocation, `pre_apply_reporter` wiring, and
  `_report_cleanup_candidates_for_jsonl` :5969 whose only caller is that
  dead wiring), `_last_cleanup_queue_stats`/`_last_cleanup_policy_stats`
  (PONG fields — see delta).
- Tests: `tests/core/test_task_monitor_cleanup.py` (1,186 lines — the
  engine's test file), `tests/core/monitor/policies/test_cleanup_progress_boundaries.py`,
  `tests/core/test_task_monitoring.py:645`, `tests/tasks/test_task_monitor.py`
  PONG assertions at :1897, :1935, :7912, :7938,
  `test_task_monitor_cleanup_report_only_keeps_selected_rows` (engine-only).

Read first: [MF-5] cleanup ownership; `_task_log_deletion_owner`;
`_run_builtin_cycle_worker_local` (:5236–5283). Comprehension check: name
the one function whose return value gates the runtime cleanup worker, and
say what it returns when the store is disabled.

## 4. Invariants and Constraints

- Store-path behavior in `delete`, `jsonl_then_delete`, `report_only`, and
  `raw_external` is **unchanged**; this plan removes only what runs when
  the store is off, plus dead wiring.
- Malformed task-log rows: handled on the store path (task_monitor.py:2597–2697,
  `report_kind="malformed_task_log"`) — no gap.
- Claimed task-log rows: the store ingest reads via `iter_queue_entries` →
  `peek_generator(...)` without `include_claimed`, so they are not
  ingested (exact-ref reads such as
  `_task_log_rows_for_message_refs_including_claimed` ~:4573 do read
  claimed rows, so say "not ingested", never "never seen"). Their
  physical removal is SimpleBroker vacuum's job — auto-vacuum
  (`BROKER_AUTO_VACUUM=1` default, threshold-gated), the monitor
  maintenance pass (off under `WEFT_TASK_MONITOR_MAINTENANCE=0`; manual
  vacuum is lock-gated best-effort), or `weft system tidy`; the claimed
  delete has no age predicate. **Not a capability loss in any shipping configuration** (Van 2026-09-04: no diminution is acceptable — checked): the engine's claimed-row delete (`test_task_monitor_cleanup_deletes_claimed_task_log_before_collation`) ran only under `cleanup_policy` ownership, i.e. only with the store opted out; with the store on — the default since it landed — claimed rows were already vacuum's job. Removing the opt-out removes that mode's deleter with the mode. State this in the [MF-5] delta; no code change.
- PONG diagnostics: `_last_cleanup_queue_stats`/`_last_cleanup_policy_stats`
  (and `_last_prune_records_scanned`) are **also populated by
  `raw_external` mode** (`_append_raw_external_stats`, ~:6114–6154) —
  review B1 corrected the first draft's "permanently empty" claim. The
  fields and the [MF-5] PONG sentence **stay**; only the engine's
  `skipped_owner` rows disappear. Consequence: the assertions at
  `tests/tasks/test_task_monitor.py:~1687` and `~1896` (truthy queue stats
  in `delete`/`report_only` with the store on) are truthy today only
  because of that `skipped_owner` row and flip to `== []`.
- Replacement result for `_run_builtin_monitor_processor_cycle` (review
  S4): today `report_only`/non-raw return the engine's empty result
  (`success=True, processed=0, deleted=0, reported=0`) and collated+apply
  merges `cleanup.*` counters. After removal the base result is that
  zero-valued result; task 3 pins `last_processed`, `last_deleted`,
  `last_reported`, `last_processor_success` per mode, not only rows.
- "Available" means opened and verified per [SB-0.4a]; a store failing
  verification is *unavailable*, not disabled, and then — same
  `runtime_cleanup_ready` trace — no task-local runtime cleanup runs
  either. `MonitorStoreStatus.enabled` and the PONG `collation_store_enabled`
  / `collation_store.enabled` fields become constant-True: drop them
  (spec-neutral; no spec names them).
- No new abstraction; no new mode.

Review gates: external review before promotion and before
implementation (Class 5 + risky trigger).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `bcea628e` — 05, 07, 00-Quick_Reference at plan authoring time
  (2026-08-31). Promotion baseline identifier: recorded after task 1.

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/05-Message_Flow_and_State.md | A | [MF-5] ~1424–1428 replace (exact text below); ~1385–1391 replace (exact text below); ~580–582 replace (exact text below); ~1244–1248 reconcile with [OBS.13.3] (exact text below) |
| docs/specifications/01-Core_Components.md | D | [CC-2.3] ~483–487 replace (exact text below) |
| docs/specifications/00-Quick_Reference.md | D | remove env row ~194 |

(The PONG sentence at [MF-5] ~368–370 is **not** changed — `raw_external`
ownership still populates those fields. Every delta below is exact
replacement prose, per strategy A; nothing is deferred to task 1.)

### [MF-5] — replace "Disabling `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED` … old family-window deleter."

> The Monitor collation store is always enabled; there is no disabled
> mode. Retained `weft.log.tasks` deletion has exactly two owners, selected
> by configuration, not by `WEFT_TASK_MONITOR_MODE`: when
> `WEFT_LOG_TASKS_EXTERNAL_ENABLED=true` with
> `WEFT_LOG_TASKS_EXTERNAL_MODE=raw`, raw rows are emitted and deleted
> without table ingest (`raw_external` ownership); otherwise the collation
> store owns them. Under collated ownership the supervised `delete` mode
> deletes retained collated raw rows when the store is available;
> `report_only` is the non-destructive override and continues table ingest
> and checkpoint advance without deletion. Under `raw_external` ownership
> `report_only` performs no ingest and advances no checkpoint. "Available"
> means opened and verified per [SB-0.4a]; under collated ownership, if the store is unavailable,
> well-formed task-log rows remain visible, no task-local runtime cleanup
> runs, and there is no fallback deleter. Claimed `weft.log.tasks` rows are
> outside Monitor collation: they are not ingested, and their physical
> removal is SimpleBroker vacuum's job — auto-vacuum, the monitor
> maintenance pass, or `weft system tidy` — none of which is
> unconditional.

### [MF-5] ~1385–1391 — replace the bullet beginning "the manager-supervised `TaskMonitor` reports and deletes through the TaskMonitor-owned cleanup runner in `weft/core/monitor/cleanup.py` …"

> - the manager-supervised `TaskMonitor` deletes through two owners only:
>   retained `weft.log.tasks` cleanup is orchestrated by
>   `weft/core/monitor/task_monitor.py` with durable collation in
>   `weft/core/monitor/store.py`, and task-local runtime queue cleanup runs
>   in the TaskMonitor-owned runtime cleanup slices
>   (`weft/core/monitor/policies/runtime_control.py`). Runtime-state
>   pruning is the separate maintenance pass through
>   `weft/core/pruning/runtime.py`. All destructive paths still use the
>   canonical exact-delete helper shared with foreground `weft system
>   prune`.

### [MF-5] ~580–582 — replace "Built-in cleanup still runs runtime-state policies such as `weft.state.tid_mappings`, but it no longer uses bounded task-log family windows as the supervised task-log deletion authority."

> Runtime-state pruning runs in the monitor's maintenance pass
> (`weft.state.tid_mappings` is owned by LivenessMonitor); the Monitor
> collation store is the supervised task-log deletion authority.

### [MF-5] ~1244–1248 — reconcile with [OBS.13.3] (verified against `_ingest_retained_task_log_rows`: in `delete` mode `rows_to_delete = tuple(selected_rows)` — every folded row is exact-deleted after the fold; only `jsonl_then_delete` narrows deletion to malformed rows until the family lifetime report is handed off). Replace "folds valid rows into the Monitor table, and then deletes exact raw rows only after the table proves a terminal or classified-stale family has been summarized."

> folds valid rows into the Monitor table and, in `delete` mode, then
> deletes those exact raw rows once the fold is durably recorded
> ([OBS.13.3]); in `jsonl_then_delete` mode valid raw rows are retained
> until the family's `task_lifetime_report` has been handed off, and only
> malformed rows are deleted at fold time.

### [CC-2.3] ~483–487 — replace "Policy run result values share the internal result type `weft/core/monitor/policies/task_log.py::CleanupPolicyRun`; private helper phases must not create additional policy identities."

> Private helper phases must not create additional policy identities.

(The pinning test `test_cleanup_policy_run_is_owned_by_its_only_consumer`
goes with the deleted module.)

### 00-Quick_Reference — remove the `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED` row.

## 5. Tasks

1. **Independent review** of the plan and deltas (§8) — before promotion (runbook order: plan → delta review → promotion); the Codex and Claude rounds recorded below satisfy this for the current text, and any later revision re-enters it.
2. **Spec-promotion slice** (A/D); backlinks; identifier. Verify
   `tests/specs/`.
3. **Characterization first.** `raw_external` is an *ownership outcome*
   of the external-logging flags, not a mode, so pin the matrix over
   configuration tuples: `WEFT_TASK_MONITOR_MODE` ∈ {delete, report_only,
   jsonl_then_delete} × external ∈ {off, collated, raw} × store ∈
   {available, unavailable} under collated ownership. Raw external ownership
   bypasses the store, so its store state is not applicable. For each reachable tuple assert: rows
   ingested, rows deleted, lifetime reports emitted, checkpoint advanced,
   runtime-cleanup worker scheduled, and `last_processed`/`last_deleted`/
   `last_reported`/`last_processor_success`. The `report_only + raw`
   tuple (no ingest, no checkpoint advance) and the collated store-unavailable
   tuples (no deletion, no runtime cleanup) are part of the promoted
   contract and must be in the matrix. Identical before and after.
4. **Remove the toggle.** Constants, override rule (REMOVED with message)
   **and** the process-environment rejection site — `_load_weft_env_vars.removed_task_monitor_env`
   (`_constants.py:~2824`) is a second, independent implementation; without
   the entry there an exported `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED=0`
   is silently ignored and the store silently enabled — test both the
   environment form and the in-process override form; config field
   (`weft/core/monitor/runtime.py` :212/:337–342/:153–177 and task_monitor
   :1390/:1652); `_ensure_monitor_store` disabled branch;
   `_task_log_deletion_owner` collapse (verify `custom` mode is unaffected —
   `_run_monitor_cycle` already runs the store cycle non-destructively
   there); `MonitorStoreStatus.enabled` (`weft/core/monitor/store.py:106`,
   twelve constructor sites across `store.py` and `task_monitor.py`, the
   `to_summary()` key, and the PONG assertion at
   `tests/tasks/test_task_monitor.py:~2020`); `tests/core/test_task_monitoring.py:645`
   becomes a "removed env var is rejected with the message" test.
   **One-way persisted effect (state it in the release note):** a
   deployment that ran with the store disabled will, on its first `delete`
   cycle after upgrade, create/verify the Monitor tables and exact-delete
   visible raw task-log rows after ingest; reverting the code does not
   restore those rows. Give operators an upgrade check: any process still
   exporting the removed variable fails fast at `load_config()`.
5. **Remove the engine — as four sub-slices, each its own commit** (review:
   one diff would hide a missed worker-clone field or processor counter):
   5a engine and engine-only test retirement; 5b monitor-cycle replacement
   plus the task-3 characterization matrix; 5c scanner/status/config
   cleanup; 5d documentation, ruff registry, and generated-index
   reconciliation. Delete the four modules and the scanner's
   family-selection half; delete `_run_task_monitor_cleanup_cycle`'s
   engine invocation, `pre_apply_reporter` wiring,
   `_report_cleanup_candidates_for_jsonl`, and the `SKIPPED_OWNER`
   constant (the PONG stats fields **stay** — raw mode populates them).
   Replacement result for `_run_builtin_monitor_processor_cycle`: the
   zero-valued base result (`success=True, processed=0, deleted=0,
   reported=0`) that `report_only`/non-raw return today.
   Test triage rule (review S1): a test file goes only if it imports
   **engine modules only**; `tests/core/test_task_monitor_cleanup.py:98–260`
   (`test_apply_exact_prune_candidates_*` ×4,
   `test_malformed_policy_selects_only_explicitly_malformed_rows`,
   `test_older_than_policy_skips_claimed_rows_and_stops_at_young_fifo_row`)
   test the live `weft/core/pruning/apply.py`/`policies.py` and **move**
   to `tests/core/test_pruning_apply.py`. Also in scope (review S2):
   `weft/core/monitor/runtime.py` (`TaskMonitorRuntimeConfig.collation_store_enabled`
   :212, loader :337–342, `_validate_jsonl_then_delete_config` :153/:173–177);
   `tests/core/test_task_monitoring.py:357, :617, :659` beyond :645;
   `tests/core/test_task_log_scanner.py` (all three tests call
   `select_task_log_family_groups`) and its `tests/conftest.py:131`
   entry; `_constants.py` `_WORKER_SNAPSHOT_OPTIONAL_CALLABLE_FIELDS`/`_WORKER_SNAPSHOT_EXPECTED_FIELDS`
   (:1189, :1211, :1264 name `_run_task_monitor_cleanup_cycle` and the
   stats attrs; `tests/tasks/test_task_monitor.py:290–294` asserts
   parity); `tests/system/test_constants.py:121–122` (`normalizer_only`)
   and :687 (removed-env parametrization — the REMOVED rule test lives
   here; the :645 test expects the error from `from_config`, the REMOVED
   rule raises from `load_config`); `docs/ruff-suppression-registry.md`
   RUFF-SUP-023 and RUFF-SUP-025 rows retired and the index regenerated
   (`bin/ruff_suppression_index.py --write`); the slow-cycle hook test
   `tests/tasks/test_task_monitor.py:~7575` and the PING-must-not-clean
   tripwire `:~7918` monkeypatch `_run_task_monitor_cleanup_cycle`/`run_task_monitor_cleanup`
   — give them new hooks (`_run_monitor_store_cycle` /
   `GeneratorTaskLogScanner.scan_window`), do not just edit assertions.
   Grep gates — scoped to `weft/`, `tests/`, `docs/specifications/`,
   `docs/ruff-suppression-registry.md`, and `bin/` (historical plans under
   `docs/plans/` legitimately keep these names): zero references to
   `run_task_monitor_cleanup`, `TaskMonitorCleanupConfig`,
   `task_log_collation`, `policies.task_log`, `policies.reserved`,
   `select_task_log_family_groups`.
   Release note: one CHANGELOG line. Do **not** edit the completed origin
   plan (completed plans are immutable at closure per the runbook); the
   new spec text, this plan, and the CHANGELOG record the retirement.
6. **Traceability reconciliation.** [MF-5] implementation-mapping notes
   that name `cleanup.py`/`task_log_collation.py` (grep the specs for both
   file names) are updated; deviation log closed; gates rerun.

Stop if: any store-path test depends on an engine symbol for something
other than the scanner's live half — that is a hidden sharing the grep
missed; report it.

## 6. Testing Plan

Real monitor cycles against `broker_env` (existing fixtures). The
characterization matrix in task 3 is the proof that nothing on the live
path changed. Optional (owner call, not in scope): a test that
`report_only` reports task-local runtime candidates — today it does not,
because the runtime worker is gated on destructive mode; [MF-5] does not
require it.

## 7. Verification and Gates

Per task: `tests/tasks/test_task_monitor.py`, `tests/core/test_task_monitoring.py`,
`tests/specs/`. Final: full suite + mypy + ruff. The code/config change
can be reverted, but forced store adoption can ingest and exact-delete
raw task-log rows; reverting code does not restore them. Apply the
upgrade check and release note in task 4. Rejecting the removed env var
is the configuration-boundary effect, not the only persisted consequence.

## 8. Independent Review Loop

Different agent family. Read [MF-5], `_task_log_deletion_owner`,
`_run_builtin_cycle_worker_local`, and the engine's `run_task_monitor_cleanup`
loop. Stance: find any configuration or mode in which the engine is the
live deleter *with the store enabled* (which would make removal a
behavior change), and any store-path consumer of the deleted modules the
symbol grep missed.

## 9. Out of Scope

`report_only` reporting task-local candidates; monitor-store schema or
verification (spec-mandated, left alone); reserved cleanup rules.

## 10. Fresh-Eyes Review

Author pass 2026-08-31: corrected the earlier "disabled = no task-log
deletion" framing after verifying disabled mode already disables all
task-local cleanup; corrected the removable-module set after finding the
scanner is shared; added the claimed-row and PONG-field consequences so
the implementer does not discover them mid-slice.

## Review Record (append-only)

**2026-08-31 — independent pre-promotion review of the deltas (Claude-family subagent; the cross-family review §8 asks for has NOT yet been run).** The
ownership trace was confirmed clean (no configuration makes the engine
the live deleter with the store enabled; "no task-local cleanup with the
store disabled" verified exactly). Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — PONG stats fields are populated by `raw_external` mode too; the draft delta would have deleted live diagnostics | Applied: fields and the [MF-5] PONG sentence kept; the two flipping assertions named |
| B2 — three spec sentences still name `cleanup.py`/`CleanupPolicyRun` as live | Applied: added to the delta table with strategy A/D corrections |
| S1 — test-triage rule would delete six live pruning tests | Applied: rule changed to "imports engine modules only"; the six tests move |
| S2 — inventory gaps (`monitor/runtime.py` config, extra tests, scanner tests + conftest, constants ledgers, `test_constants.py` REMOVED-rule site, ruff registry rows, two monkeypatch hooks) | Applied to task 5 |
| S3 — claimed-row sentence over-attributed to the maintenance pass; engine's claimed-row *deletion* is a lost capability | Applied: delta wording and accepted-loss statement |
| S4 — replacement processor result unspecified | Applied: zero-valued base result; per-mode counters pinned in task 3 |
| S5 — no release note; env var was the documented rollback lever | Applied: CHANGELOG line + origin-plan note |
| Notes — "available" per [SB-0.4a]; constant-True status fields dropped; silent misconfiguration (`EXTERNAL_ENABLED=1, MODE=collated, STORE=0`) is fixed by removal | Applied / recorded |

**2026-08-31 — Codex (cross-model) pre-promotion review.** Verdict: not
promotable as first written; core engine-removal decision supportable;
custody trace confirmed. Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — `report_only` contract false under `raw_external` ownership (no ingest, no checkpoint) | Applied: delta rewritten around the two ownership outcomes; matrix over config tuples |
| B2 — claimed-row sentence promised cleanup operators can disable | Applied: delta names auto-vacuum / maintenance / tidy and says none is unconditional |
| B3 — [MF-5] ~1244 contradicts [OBS.13.3]; code follows OBS.13.3 in `delete` mode | Applied: verified in `_ingest_retained_task_log_rows`; exact reconciliation text added distinguishing `delete` from `jsonl_then_delete` |
| B4 — deltas not exact; literal [CC-2.3] edit would break prose | Applied: every delta is now exact replacement text; [CC-2.3] added to Source specs |
| S1 — second env-var rejection site (`removed_task_monitor_env`) | Applied to task 4 with both-form tests |
| S2 — `MonitorStoreStatus.enabled` inventory | Applied (file, 12 constructor sites, PONG assertion) |
| S3 — rollback understated; forced store adoption is a one-way persisted effect | Applied: stated with an upgrade check |
| S4 — matrix treated `raw_external` as a mode | Applied: config-tuple matrix incl. unavailable store |
| S5 — task 5 too large | Applied: four sub-slices |
| S6 — grep gate scope | Applied |
| S7 — do not edit the completed origin plan | Applied; supersedes the Claude round's S5 suggestion |

**2026-09-07 — review against code/spec baseline `bcea628e`; plan correction.**

| Finding | Disposition |
|---------|-------------|
| F3 — §7 called rollback fully revertable and claimed environment-variable rejection was its only persisted effect, contradicting task 4 | Accepted: §7 now carries task 4's existing one-way raw-row deletion qualification. No new behavior or owner decision is introduced. |

Evidence checked: `_task_log_deletion_owner` selects the store when it
is enabled; `_run_builtin_cycle_worker_local` runs store ingest before
the built-in processor. The retirement plan's task 4 already identifies
the resulting raw-row deletion on forced adoption. The current
`test_task_monitor_raw_external_logs_and_deletes_without_store` passed
when rerun; raw ownership continues to bypass store ingest. This
revision changes plan wording only and does not claim implementation or
a final-review pass.

## Implementation Record (2026-09-07)

Class 5, hardened. Isolated work begins atop plans 4/5 pending gates; commits
remain ordered and one per plan, overriding internal sub-slice commits.
Independent pre-promotion review passed after correcting the fail-closed scope:
store unavailability applies only to collated ownership. Raw external ownership
bypasses the store entirely; it continues raw emission/deletion with runtime
cleanup disabled. Its matrix store state is N/A, not an unavailable-store
fail-closed cell. Report-only raw performs no ingest/checkpoint advance.
Both removed-key boundaries must fail fast; no fallback engine or schema
change. Strategy A/D spec promotion precedes source edits.

Characterization completed before source removal: 15 parameterized cells
(12 reachable mode/ownership/store combinations and three existing config
rejections) passed unchanged afterward. jsonl_then_delete requires collated
external reporting, so off/raw combinations remain rejected. The matrix
checks durable broker/store effects, checkpoint, report counts, runtime
cleanup authorization and all processor counters. Existing custom, PONG,
raw, clone and control-responsiveness cases also pass.

All six live pruning tests moved intact from the mixed engine test file;
engine-only cases retired. The retained scanner definitions are AST-identical
and have non-consuming window/decode coverage. Retiring the sole engine
reporter also left build_candidate_lifetime_report and two helpers unused;
that exclusive chain and its two tests retired after whole-tree caller checks.
Live collation/raw/inferred lifetime report builders are unchanged. Root
independent review of that deletion passed.

The new removed-key test failed in all four environment/override × 0/1 cells
before implementation and passes afterward with the exact migration message.
157 config/runtime tests pass. Full Ruff/mypy pass (187 source files).
Independent same-family completed review passed main Monitor/engine/config
behavior and the real effects matrix; no live store-path dependency remained.
SUP023 retired; SUP025 remains because it protects live ingest/recovery.
Inventory is 209 groups, 343 directives, 130 C901 directives. Full ordered
root gate and metadata reconciliation remain before commit.

Final root verification: 4,535 passed, 16 skipped in the full suite including
slow tests (`pytest -m '' -n 2 -vv`). Ruff, full mypy (187 source files),
and suppression reconciliation passed. Skips are 11 opt-in live-provider
cases and five PostgreSQL-only cases under SQLite.

The first full run found one test still asserting the retired store-status
`enabled` attribute. It now checks that the exported summary omits that field,
retaining every availability, schema, checkpoint and backend-error assertion.
All 124 Monitor-store tests passed before the clean full rerun. Independent
read-only integration review found no reverted plan 5 work: raw ownership
bypass and unavailable-store failure behavior remain intact; retained scanner
definitions are unchanged and retired engine callers are absent. No source
or test edits occurred during the final full gate.
