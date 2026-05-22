# Monitor Terminal Retirement And Runtime Queue Cleanup Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4], [SB-0.4a]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
Superseded by: none

## 1. Goal

Fix the remaining production backlog after the TaskMonitor worker split. The
monitor must retire terminal `weft.log.tasks` families without waiting for the
general raw-log retention period, must delete stale task-local control queues
for already disposed families, and must add reserved-queue cleanup to the
existing TaskMonitor `delete` processor/runtime-cleanup worker. Per the
implementation correction, this slice does not add a new cleanup switch; the first
reserved cleanup behavior is `delete` through the existing monitor cleanup
structure.

This is a destructive cleanup change. It must be implemented as small,
observable policy changes with real broker tests, cached PONG diagnostics, and
no private SimpleBroker SQL.

## 2. Source Documents

- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]:
  `TaskMonitor` owns operational monitoring, retained lifecycle summaries,
  and monitor-owned cleanup. Base task queue ownership and reserved policy
  semantics still matter for what cleanup may delete.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4], [SB-0.4a]:
  use `WeftContext` and public SimpleBroker queue APIs. Monitor tables are
  allowed derived operational tables, but queue rows remain SimpleBroker data.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: task-log
  lifecycle evidence, Monitor collation, retention, task-local queue cleanup,
  and reserved queue boundaries.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]:
  operational cleanup evidence, exact deletion, PONG diagnostics, and cleanup
  safety boundaries.
- `docs/plans/2026-05-16-monitor-durable-collation-store-plan.md`: completed
  Monitor table introduction.
- `docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`:
  completed external logging and retained raw-log policy foundation.
- `docs/plans/2026-05-19-task-monitor-bounded-control-cleanup-plan.md`:
  completed bounded terminal control cleanup. This plan fixes the eligibility
  gap it left behind.
- `docs/plans/2026-05-19-task-monitor-control-cleanup-worker-plan.md`:
  completed worker split. Keep that reactor/worker ownership model.
- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`, and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  because this is destructive background cleanup with rollout risk.

Production evidence motivating this plan:

- `weft.log.tasks` had about `17.7k` visible rows. Only about `19` were older
  than the 48h retention period, but about `10.4k` visible rows belonged to
  terminal Monitor families. Retention is blocking terminal retirement.
- `T*.ctrl_in` had about `56k` rows; `T*.ctrl_out` had about `22k` rows.
  Thousands of distinct TIDs were already `disposed` in the Monitor table but
  still had `task_control_deleted_at_ns IS NULL`, so the current control
  cleanup selector skips them forever.
- `T*.reserved` had about `6.3k` rows. Most were old no-Monitor rows; the
  monitor-known subset was mostly failed/timeout/cancelled rows with
  `reserved_probe_needed=1`. Reserved cleanup needs its own policy instead of
  piggybacking on terminal control cleanup.

## 3. Context And Key Files

Read first:

- `weft/core/monitor/task_monitor.py`
  - `_run_monitor_cycle()`: top-level monitor cycle orchestration.
  - `_ingest_retained_task_log_rows()`: current retention-gated FIFO ingest and
    raw delete path. This is the main task-log policy to change.
  - `_emit_monitor_store_summaries()`: emits compact summaries and marks
    families.
  - `_delete_monitor_store_task_log_rows()`: deletes exact raw task-log rows
    proven by Monitor state.
  - `_run_terminal_control_cleanup_slice()`: current control cleanup worker
    selector and apply loop.
  - `_task_monitor_pong_extension()` and `_control_snapshot_fields()`: cached
    diagnostics exposed by PONG.
- `weft/core/monitor/store.py`
  - `list_summary_ready_tasks()`
  - `list_terminal_control_cleanup_ready_tasks()`
  - `list_deletable_task_log_messages()`
  - `mark_task_controls_deleted()`
  - `mark_families_disposed()`
  - `record_task_log_updates()`
- `weft/core/monitor/sql.py`
  - selector SQL for summary readiness, raw row deletion, and control cleanup.
  - Keep all dynamic SQL in this module and validate code-owned identifiers.
- `weft/core/pruning/apply.py`
  - canonical exact-message delete helper. Use it for exact row deletes.
- `weft/core/service_convergence.py`
  - `collect_service_owner_records()` and service-owner parsing. Reuse these
    if the cleanup policy needs active service TID guards.
- `weft/core/monitor/cleanup.py`
  - legacy/composable cleanup runner. Do not revive the old task-log family
    window path as the primary supervised task-log cleanup authority.
- `weft/helpers/__init__.py`
  - `iter_queue_entries()` generator helper. Queue history reads must stay
    generator-based.
- `tests/tasks/test_task_monitor.py`
  - real broker tests for monitor cycles, PONG, external logging, terminal
    control cleanup, and worker behavior.
- `tests/core/test_monitor_store.py`
  - Monitor-store SQL and persistence tests.
- `tests/system/test_constants.py`
  - production constants must live in `weft/_constants.py`.

Files to modify:

- `weft/_constants.py`
  - Add any new policy names and config keys here.
- `weft/core/monitor/task_monitor.py`
  - Change policy orchestration and cached diagnostics.
- `weft/core/monitor/store.py`
  - Add durable selectors and access methods for terminal raw retirement,
    disposed control cleanup, and reserved cleanup readiness.
- `weft/core/monitor/sql.py`
  - Add the SQL builders behind those store access methods.
- `weft/core/monitor/task_log_scanner.py` or `weft/core/monitor/collation.py`
  - Only if the task-log selection shape needs a pure helper; do not bury
    broker effects here.
- `tests/tasks/test_task_monitor.py`
  - Add end-to-end monitor behavior tests with real queues.
- `tests/core/test_monitor_store.py`
  - Add selector and migration tests.
- `tests/system/test_constants.py`
  - Add config/default assertions for new constants.
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/00-Quick_Reference.md`
- `docs/plans/README.md`

Do not modify:

- Do not change task execution, state transition, or result reconstruction
  semantics.
- Do not change public CLI output shape.
- Do not add a second monitor table outside the existing Monitor-store access
  layer.
- Do not use private SimpleBroker SQL to delete queue rows.
- Do not delete manager/global/custom control queues.
- Do not delete active task-local queues just because their names match
  `T*.ctrl_in`, `T*.ctrl_out`, or `T*.reserved`.

Comprehension checks before editing:

- Which value is the queue API timestamp/message id used by
  `iter_queue_entries()` and the Monitor table, and why should tests avoid
  assuming SQL `order_id` is that value?
- What currently makes a Monitor family eligible for terminal control cleanup,
  and why does `disposition_at_ns IS NULL` skip old disposed families forever?
- Which reserved queue rows are recovery-sensitive, and what proof makes them
  safe for the existing TaskMonitor `delete` processor?

## 4. Invariants And Constraints

- TIDs stay immutable and are parsed from queue names only after validating the
  exact `T{tid}.suffix` pattern.
- State transitions remain forward-only. Cleanup may mark Monitor operational
  rows, but it must not rewrite task lifecycle truth.
- `weft.log.tasks` remains runtime lifecycle evidence while retained, not
  audit or legal-retention evidence.
- Monitor table rows are derived operational state. They may drive cleanup,
  but they are not result authority.
- External summary logging is fail-closed for raw deletion. If a configured
  external sink cannot write a terminal summary, keep the raw task-log family.
- PONG must remain cached and lightweight. It may expose the last policy
  results and backlog hints; it must not scan queues or query the store while
  answering PING.
- All queue deletion must use public SimpleBroker APIs through `WeftContext`.
  Whole-queue delete is acceptable only after the policy proves the entire
  task-local queue is safe to retire.
- Exact task-log raw row deletion must keep using
  `weft/core/pruning/apply.py`.
- Runtime-state `weft.state.*` queues stay separate from task-local cleanup.
- The monitor reactor must remain responsive. Long destructive queue cleanup
  belongs in the existing TaskMonitor cleanup worker lane or another explicit
  monitor-owned worker lane, not inline in PING/control handling.
- Active manager, heartbeat, task monitor, autostart, and user task queues must
  be protected from cleanup. For nonterminal/disposed cleanup, check active
  service-owner evidence before whole-queue deletion.
- No broad mocking. Use real broker-backed queues and real Monitor-store
  tables in tests unless the boundary is external logging.

Out of scope:

- Redesigning SimpleBroker queue storage.
- Adding archive/restore semantics for reserved queues.
- Making `weft system prune` share every new policy in this slice. It may reuse
  pure helpers later, but the immediate owner is the supervised TaskMonitor.
- Deleting `T{tid}.inbox` or `T{tid}.outbox` by default. Those need separate
  policy decisions.
- Changing default task reserved-policy semantics.

External review step:

- Required before implementation is declared ready. This plan adds destructive
  background deletion for reserved queues and changes task-log retention
  semantics. A second reviewer should specifically challenge active-task
  protection and fail-closed external logging behavior.

## 5. Bite-Sized Tasks

### Task 1: Write The Spec Delta First

Files:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/00-Quick_Reference.md`

Required changes:

- Split task-log retention into two concepts:
  - terminal-family raw retirement: terminal families may be summarized and
    raw-deleted without waiting for `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`;
  - open/incomplete raw retention: open families and incomplete evidence still
    use the configured retention/stale-open policies.
- Update the Monitor cleanup contract so terminal or disposed task-local
  `T{tid}.ctrl_in` and `T{tid}.ctrl_out` queues may be deleted after the
  Monitor family is no longer active and `task_control_deleted_at_ns` is unset.
  Do not require `disposition_at_ns IS NULL`.
- Add reserved queue cleanup to the existing TaskMonitor `delete` processor and
  runtime-cleanup worker:
  - candidate selection is Monitor-owned;
  - the first action is delete through the existing worker path;
  - active/ambiguous work is protected;
  - terminal/disposed/raw-deleted monitor rows and stale no-monitor rows are
    eligible when age/liveness guards pass.
- Define PONG diagnostics for each policy: terminal raw rows deleted, terminal
  families retired, disposed control families processed, reserved queues
  deleted/skipped, and worker errors.

Stop and re-evaluate if:

- The spec starts treating Monitor summaries as public task status truth.
- The reserved policy cannot state what protects active work.

### Task 2: Reuse Existing Config And Policy Structures

Files:

- `weft/_constants.py`
- `docs/specifications/00-Quick_Reference.md`

Do not add new cleanup switches for this slice. Reuse:

- `WEFT_TASK_MONITOR_PROCESSOR=delete` as the destructive monitor switch.
- `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` for open-family and stale
  no-monitor reserved age.
- `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT` as the bounded runtime-queue
  cleanup family limit.

Terminal task-log retirement is a semantic fix in the existing collated-store
path: terminal families do not wait for the general open-family retention
period once the monitor has reached a complete FIFO high-water pass.

Testing first:

- Extend monitor/store tests for terminal retirement under a long general
  retention period, disposed-family control cleanup, and reserved cleanup via
  the existing delete processor.

Stop and re-evaluate if:

- The implementation adds a second reserved cleanup switch or bypasses the
  existing TaskMonitor processor/worker contract.

### Task 3: Make Terminal Task-Log Retirement Independent Of General Retention

Files:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/store.py`
- `weft/core/monitor/sql.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_store.py`

Current problem:

- `_ingest_retained_task_log_rows()` stops at the first row younger than
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`.
- As a result, terminal families younger than 48h are left in
  `weft.log.tasks`, even when a compact Monitor summary would be sufficient.

Implementation shape:

1. Rename or replace `_ingest_retained_task_log_rows()` with a name that
   reflects the new contract, such as `_ingest_task_log_rows()`.
2. Scan visible `weft.log.tasks` rows in FIFO order using
   `GeneratorTaskLogScanner`; keep `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT` as
   scan depth and `WEFT_TASK_MONITOR_BATCH_SIZE` as processing cap.
3. Delete malformed rows immediately through exact-message deletion.
4. Upsert valid rows into the Monitor table regardless of general retention
   age, up to the per-cycle batch cap.
5. Do not raw-delete open/incomplete valid rows just because they were
   ingested.
6. After a valid terminal family is known and any required summary has emitted,
   delete that family's exact known raw `weft.log.tasks` rows through
   `store.list_deletable_task_log_messages(...)` and
   `apply_exact_prune_candidates(...)`.
7. General raw-log retention remains available for open or incomplete rows,
   but it must no longer block terminal family retirement.
8. External log behavior remains fail-closed: when
   `WEFT_LOG_TASKS_EXTERNAL_ENABLED` is enabled and emit fails, do not mark
   summary emitted and do not delete raw rows for that family.

Store/SQL changes:

- Add a selector that returns deletable raw task-log message refs for terminal
  summarized families without applying the general retention cutoff.
- If needed, add a selector for open/incomplete rows that still uses
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`; keep it separate from terminal
  deletion.
- Keep selector names explicit, for example
  `select_deletable_terminal_task_log_messages`.

Tests:

- Red test: with `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS=172800`, create a
  young complete task-log family. One monitor cycle should ingest and emit the
  terminal summary, and a subsequent deletion step should remove those exact
  `weft.log.tasks` rows.
- Red test: a young open family is ingested but its raw rows remain visible.
- Red test: external summary sink failure leaves terminal raw rows visible and
  records blocked deletion diagnostics.
- Red test: malformed rows are deleted regardless of age.
- Red test: PONG reports terminal-family raw deletion counts separately from
  open-retention deletion counts.

Stop and re-evaluate if:

- The implementation deletes raw rows before the Monitor table has recorded
  their message IDs.
- The implementation treats `task_activity` with terminal-looking `status` as
  terminal proof if the existing terminal reducer says not to.

### Task 4: Fix Disposed Control Queue Cleanup Eligibility

Files:

- `weft/core/monitor/store.py`
- `weft/core/monitor/sql.py`
- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_store.py`

Current problem:

- `select_terminal_control_cleanup_ready_tasks()` requires
  `disposition_at_ns IS NULL`.
- Production has thousands of `ctrl_in`/`ctrl_out` queues whose families are
  already disposed and therefore skipped forever.

Implementation shape:

1. Rename the selector to reflect the broadened contract, for example
   `list_task_control_cleanup_ready_tasks()`.
2. Select records where:
   - `summary_emitted_at_ns IS NOT NULL`;
   - `task_control_deleted_at_ns IS NULL`;
   - terminal rows have crossed the terminal high-water or
     `disposition_at_ns IS NOT NULL`.
3. Do not require `disposition_at_ns IS NULL`.
4. Before deleting a nonterminal disposed family, protect active services:
   gather latest service-owner rows with
   `collect_service_owner_records()` from `weft.state.services`, reduce to live
   owners, and skip any TID that is currently an active/draining service owner.
   For terminal rows this guard should still be harmless and may run for both
   cases for simplicity.
5. Reuse `_standard_task_control_queue_names()`. Only delete exact standard
   `T{tid}.ctrl_in` and `T{tid}.ctrl_out`.
6. Mark `task_control_deleted_at_ns` after successful queue deletion. Mark
   family disposition only if it was not already set.

Tests:

- Red test: a disposed nonterminal Monitor record with standard control queues
  is selected and deleted.
- Red test: an already disposed record is not skipped by the selector.
- Red test: a live active service-owner TID with control queues is skipped even
  if a stale Monitor row says disposed.
- Red test: a nonstandard control queue in `taskspec_summary.io.control` is
  skipped and marked with a diagnostic, preserving current manager/global/custom
  control safety.
- Existing terminal control cleanup tests must still pass.

Stop and re-evaluate if:

- The implementation deletes control queues based only on queue-name age
  without Monitor-store proof or active-service protection.

### Task 5: Add Reserved Queue Candidate Selection And Resolver Interface

Files:

- `weft/core/monitor/runtime_queue_cleanup.py` or
  `weft/core/monitor/task_monitor.py` if the new module would be too small.
- `weft/core/monitor/store.py`
- `weft/core/monitor/sql.py`
- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_store.py`

Required shape:

- Implement reserved cleanup as a bounded selector plus direct delete action
  inside the existing TaskMonitor runtime-cleanup worker. Do not add a new
  resolver callback or config switch in this slice. Keep the implementation close
  to the existing terminal-control cleanup structure: gather readiness facts,
  apply liveness guards, delete through SimpleBroker, then publish cached PONG
  counters.

Candidate classes for this slice:

1. `reserved_monitor_disposed`
   - `T{tid}.reserved` exists.
   - Monitor row exists.
   - `disposition_at_ns IS NOT NULL`.
   - TID is not an active service owner.
2. `reserved_terminal_or_raw_deleted`
   - Monitor row exists.
   - `terminal_seen = 1`.
   - `summary_emitted_at_ns IS NOT NULL` or `raw_deleted_at_ns IS NOT NULL`.
   - TID is not active.
   - This covers successful and failure-like terminal rows once task-log
     lifecycle evidence has been retired.
3. `reserved_no_monitor_stale`
   - No Monitor row exists.
   - TID parsed from queue name is older than
     `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`.
   - No active service owner exists for that TID.
   - No current `weft.state.tid_mappings` row indicates a live or recent task
     for that TID. If the code cannot check this cheaply with existing helpers,
     classify as keep and add the missing helper in the next task instead of
     guessing.

Do not select:

- reserved queue for a currently active Consumer;
- reserved queue for active manager, heartbeat, or task monitor;
- malformed non-`T{tid}.reserved` queues;
- fresh no-monitor reserved queues;
- queues whose TID cannot be parsed as digits.

Apply behavior:

- Use whole-queue `Queue.delete()` only after the selector classifies the
  queue as safe.
  Whole-queue delete is allowed here because the policy is about retiring the
  entire task-local reserved queue, including visible and claimed rows, after
  safety proof.
- For no-monitor rows, record only cached cycle stats and operational logs; do
  not create a fake Monitor family.

Tests:

- Red test: disposed Monitor record plus `T{tid}.reserved` is deleted by the
  existing delete processor.
- Red test: terminal Monitor record with summary/raw deletion proof deletes a
  stale `T{tid}.reserved`.
- Red test: old no-monitor reserved queue is deleted only after the stale
  threshold and no active evidence.
- Red test: fresh no-monitor reserved queue is kept.
- Red test: active service-owner TID is kept even if a stale/disposed Monitor
  row exists.
- Use real broker queues.

Stop and re-evaluate if:

- The candidate selector needs private SQL against SimpleBroker tables to find
  queues. Prefer SimpleBroker public queue listing or existing Weft queue
  helpers. If public APIs cannot provide bounded discovery, pause and decide
  whether the missing capability belongs in SimpleBroker.
- The implementation cannot cheaply protect active work. Do not ship a
  reserved delete policy without active-work protection.

### Task 6: Wire Reserved Cleanup Into The Monitor Cycle

Files:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/runtime_queue_cleanup.py` if created
- `tests/tasks/test_task_monitor.py`

Implementation shape:

- Run reserved cleanup after terminal raw task-log deletion and control queue
  cleanup scheduling. It should not block PING.
- Run it through the same monitor maintenance worker pattern as control
  cleanup. Do not run a large reserved sweep inline on the reactor.
- Respect `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT`.
- Preserve single-flight behavior per lane.
- Set catch-up scheduling when reserved cleanup hits its limit or has pending
  work.
- Add cached PONG fields:
  - `last_reserved_families_processed`
  - `last_reserved_queues_deleted`
  - `last_reserved_rows_estimated_deleted`
  - `last_reserved_skipped_active`
  - `last_reserved_skipped_not_ready`
  - `last_reserved_rows_deleted`

Tests:

- PING remains responsive while reserved cleanup worker is blocked.
- A worker error is cached and retried on a later cycle.
- Cleanup limit causes pending/catch-up state rather than a hot loop.

Stop and re-evaluate if:

- The monitor starts using one thread per reserved queue. Work must be batched
  in one bounded worker pass.

### Task 7: Update Operational Diagnostics And Serve Logs

Files:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`
- docs touched in Task 1

Required diagnostics:

- PONG must show, from cached variables only:
  - whether terminal raw retirement ran;
  - how many terminal families were summarized and raw-deleted;
  - whether raw deletion was blocked by external logging;
  - how many disposed/terminal control families were selected and deleted;
  - how many reserved queues were processed, deleted, and skipped;
  - why cleanup skipped candidates, with bounded reason counts.
- Existing cached cleanup stats should distinguish the major runtime cleanup
  outcomes without introducing a new policy switch:
  - `task_log.retire_terminal_family`
  - `task_log.keep_open_family`
  - `task_control.delete_disposed_or_terminal`
  - reserved queues deleted
  - reserved queues skipped because active
  - reserved queues skipped because not ready

Do not make PONG compute these values live.

Tests:

- A monitor cycle that performs each policy updates PONG fields.
- A second PING without another cycle returns the same cached values and does
  not scan queues. Use an injected scanner/store spy if needed, but keep the
  main cleanup behavior test on real queues.

### Task 8: Regression Test Against Ops-Like Backlog Shape

Files:

- `tests/tasks/test_task_monitor.py`

Build one synthetic ops-like test that creates:

- several terminal task-log families younger than 48h;
- one open family younger than 48h;
- one already disposed Monitor row with `ctrl_in`/`ctrl_out` queues and
  `task_control_deleted_at_ns IS NULL`;
- one old no-monitor reserved queue;
- one active service-owner TID with a reserved/control queue that must be kept.

Run the monitor in `delete` mode and assert:

- terminal families are summarized and raw task-log rows are removed;
- open family raw rows remain;
- disposed control queues are removed;
- old no-monitor reserved queue is deleted by the existing delete processor;
- active service-owner queues remain;
- PONG exposes the policy results without scanning.

This test should use real broker queues and the real Monitor store. It may use
a small config override for retention/age thresholds.

### Task 9: Verification Commands

Run in this order:

```bash
uv run pytest tests/system/test_constants.py
uv run pytest tests/core/test_monitor_store.py
uv run pytest tests/tasks/test_task_monitor.py
uv run pytest tests/core/test_task_monitoring.py
uv run pytest
uv run bin/pytest-pg
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft tests bin/pytest-pg
git diff --check
```

If the full suites are too slow during iteration, run the targeted tests after
each task and run the full verification checks before reporting implementation
complete.

## 6. Rollout And Rollback

Rollout:

- Ship with cached PONG diagnostics enabled by default.
- Keep the monitor processor in `delete` mode on ops.
- Watch for:
  - `weft.log.tasks` dropping from the terminal-family backlog, not merely
    claimed rows moving around;
  - `T*.ctrl_in` and `T*.ctrl_out` dropping sharply as already disposed
    families are picked up;
  - `T*.reserved` dropping according to cached reserved cleanup stats;
  - manager, heartbeat, and task monitor PING remaining responsive;
  - task monitor CPU staying bounded except during short cleanup bursts.

Expected ops evidence after deployment:

- `weft task ping <task-monitor-tid>` should show nonzero
  `terminal_raw_deleted` or equivalent terminal-retirement stats until the
  backlog clears.
- `ready_control=0` should no longer coexist with thousands of disposed
  no-control cleanup rows.
- Reserved cleanup should report candidate classes separately so an operator
  can tell whether deletes came from terminal, disposed, or no-monitor stale
  queues.

Rollback:

- Set `WEFT_TASK_MONITOR_PROCESSOR=report_only` to stop destructive monitor
  cleanup.
- The Monitor schema changes must be additive. Older code may ignore new
  columns.

One-way doors:

- Deleting reserved queues is destructive. The existing delete processor must
  not run reserved cleanup on active or ambiguous work.
- Deleting raw task-log rows is destructive. Do not do it before Monitor
  summary/external-output requirements are satisfied.

## 7. Fresh-Eyes Self-Review

Review pass completed while drafting.

Findings and fixes applied:

- Initial wording risked saying "retire all task-log rows regardless of time."
  Corrected to "retire terminal families regardless of general raw-log
  retention; keep open/incomplete rows under separate policies."
- Control cleanup needed to include already disposed families, but that creates
  active-service risk for stale-open misclassification. Added explicit
  service-owner active TID guard.
- Reserved cleanup could have been folded into terminal control cleanup with a
  new resolver-like switch. Corrected per implementation guidance: it now uses the
  existing TaskMonitor delete processor and runtime-cleanup worker instead of a
  new switch.
- No-monitor reserved queues are tempting to delete by age alone. Added
  active-service and tid-mapping guards; if those guards cannot be implemented
  cheaply, the plan requires keeping the candidate rather than guessing.
- PONG diagnostics were initially too vague. Added policy-specific cached
  fields and reason counts.

External review remains required before implementation starts or before this
plan is marked implementation-ready.
