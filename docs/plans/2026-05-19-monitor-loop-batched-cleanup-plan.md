# Monitor Loop Batched Cleanup Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4a]
Superseded by: none

## 1. Goal

Fix the supervised `TaskMonitorTask` cleanup loop introduced in `0.9.47` so
the Monitor applies retained `weft.log.tasks` cleanup as a bounded batch policy,
not as thousands of per-row mini-transactions. The behavior should preserve the
new Monitor-table authority from the retained-log plan, but the execution shape
must be batch-oriented, truthful about exact-delete outcomes, and responsive to
PING/STATUS control traffic while catching up a large backlog.

## 2. Source Documents

- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: source of truth
  for `weft.log.tasks`, Monitor-owned collation tables, retained FIFO cleanup,
  external logging, exact deletion, and PONG diagnostics.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]: source of
  truth for cleanup boundaries, exact-message deletion, runtime evidence, and
  retention protections.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4a]: SQL access
  and SimpleBroker layer boundary. Use SimpleBroker queue APIs for queue rows;
  use the Monitor store access layer for Monitor-owned tables.
- `docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`:
  active retained-log plan. Keep its semantics, but replace the row-by-row
  execution shape that landed in `0.9.47`.
- `docs/plans/2026-05-18-monitor-cleanup-reserved-hot-path-plan.md`:
  completed plan that kept ordinary cleanup away from reserved-queue probes.
  Do not reintroduce reserved probes into the hot task-log cleanup path.
- `../simplebroker/simplebroker/sbqueue.py` and
  `../simplebroker/simplebroker/db.py`: public `Queue.delete_many(...)`,
  `Queue.peek_generator(...)`, and delete semantics. Read before changing exact
  delete behavior.
- `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/_sql.py`:
  Postgres exact-delete SQL. Verify that claimed and unclaimed exact IDs are
  eligible for physical delete.
- `AGENTS.md`, `docs/agent-context/runbooks/writing-plans.md`, and
  `docs/agent-context/runbooks/hardening-plans.md`: required local planning,
  testing, and boundary guidance.

The current specs describe the desired behavior more accurately than the
current `0.9.47` implementation. This plan is a repair slice for that
implementation.

## 3. Context And Key Files

Read first:

- `weft/core/monitor/task_monitor.py`
  - `TaskMonitorTask._run_monitor_store_cycle`
  - `TaskMonitorTask._ingest_retained_task_log_rows`
  - `TaskMonitorTask._delete_exact_task_log_rows`
  - `TaskMonitorTask._emit_monitor_store_summaries`
  - `TaskMonitorTask._run_builtin_monitor_processor_cycle`
- `weft/core/monitor/store.py`
  - `MonitorStore.record_task_log_updates`
  - `MonitorStore.mark_messages_deleted`
  - `_MonitorTableAccess.mark_message_deleted`
  - `_MonitorTableAccess.reconcile_raw_deleted`
- `weft/core/monitor/sql.py`
  - `mark_message_deleted`
  - `reconcile_raw_deleted_tasks`
  - `select_summary_ready_terminal_tasks`
  - `mark_family_disposed`
- `weft/core/pruning/apply.py`
  - `apply_exact_prune_candidates`
  - `_queue_is_persistent`
- `weft/core/monitor/task_log_scanner.py`
  - `GeneratorTaskLogScanner.scan_window`
- `weft/helpers/__init__.py`
  - `iter_queue_entries`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_store.py`
- `tests/core/test_monitor_sql.py`

Current load-bearing behavior:

- `TaskMonitorTask._task_log_deletion_owner()` returns `collated_store` when
  Monitor table collation is enabled. In the ops deployment this is the default
  owner for `weft.log.tasks` cleanup.
- `GeneratorTaskLogScanner.scan_window()` already materializes a bounded
  visible queue window before deletion begins. Do not blame generator mutation
  until evidence shows otherwise. The primary bug is below the scanner.
- `_ingest_retained_task_log_rows()` currently loops one decoded task-log row at
  a time. For every valid old row it calls:
  - `store.record_task_log_updates(..., (update,), ...)`
  - `_delete_exact_task_log_rows((row.raw,))`
  - `store.mark_messages_deleted((row.raw.message_id,), ...)`
- `MonitorStore.record_task_log_updates()` currently fetches, merges, and
  upserts the task collation row once per task-log event. Repeated events for
  one TID cause repeated Monitor row updates inside the same logical cycle.
- `MonitorStore.mark_messages_deleted()` currently loops `mark_message_deleted`
  once per message ID, then runs broad raw-deleted reconciliation across the
  entire context.
- `apply_exact_prune_candidates()` currently reports missing-row deletes as
  `error=None, deleted=False`, but `_delete_exact_task_log_rows()` counts
  `error is None` as success. This can mark Monitor rows deleted even when
  `delete_many()` deleted zero broker rows.

Comprehension questions before editing:

- Which function is allowed to delete raw `weft.log.tasks` rows in the
  `collated_store` path?
- Which table owns durable task-family state after raw log rows are deleted?
- Which operation must happen first for a valid retained row: Monitor-table
  fold or broker-row delete?
- What does `delete_many()` return when an exact ID is already absent, and how
  should the caller decide whether to mark that Monitor row deleted?

## 4. Observed Production Failure

After deploying `0.9.47` to ops:

- Manager process was no longer CPU-hot.
- Spawn queues were drained.
- The active TaskMonitor child was CPU-hot around 85-90 percent.
- `weft task ping <task-monitor-tid>` timed out while the monitor was cleaning.
- `py-spy` showed the hot stack in:

```text
apply_exact_prune_candidates
_delete_exact_task_log_rows
_ingest_retained_task_log_rows
_run_monitor_store_cycle
```

and separately in:

```text
mark_messages_deleted
_ingest_retained_task_log_rows
```

Direct database evidence showed slow progress:

- `weft.log.tasks` decreased by only a few dozen rows over minutes.
- `weft_monitor_task_messages` had more than 170k rows, with only a small
  number marked deleted.
- Many terminal Monitor families existed, but `disposition_at_ns` and
  `task_control_deleted_at_ns` remained unset.

Root cause:

The retained-log policy is being applied row by row. The code has the right
authority model but the wrong execution model. It repeatedly opens broker
resources, starts SQL transactions, updates one Monitor row, deletes one broker
message, marks one child message row, and reconciles parent rows. At ops scale
this starves control handling and cannot shrink the backlog fast enough.

## 5. Invariants And Constraints

- Preserve one task-log deletion owner per cycle. Do not re-enable old
  task-log cleanup policies when `task_log_owner == "collated_store"`.
- Preserve retained FIFO semantics: valid raw `weft.log.tasks` rows older than
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` are folded into the Monitor table
  before broker deletion.
- Preserve fail-closed external logging semantics. If external collated logging
  is configured and fails, do not mark the task family disposed.
- Preserve exact-message deletion only. No queue-wide delete in this slice.
- Preserve SimpleBroker layering. Queue rows are deleted through public queue
  APIs. Monitor-owned tables are modified through `weft/core/monitor/store.py`
  and SQL helpers in `weft/core/monitor/sql.py`.
- Do not probe `T{tid}.reserved` in the retained task-log hot path.
- Do not delete task-local `T{tid}.inbox`, `T{tid}.outbox`, or
  `T{tid}.reserved` in this slice.
- Do not treat `delete_many() == 0` as a successful per-row delete.
- Checkpoints must advance only after all retained rows through that checkpoint
  have been durably handled according to policy.
- PING/STATUS must not be starved by a huge cleanup backlog. The monitor may do
  bounded catch-up, but each main-loop cleanup turn must remain bounded.
- Tests must use real broker-backed queues for cleanup behavior. Mock only
  narrow error seams such as forcing `delete_many()` failure.

## 6. Design

### 6.1 Retained-Log Cycle Shape

Replace the row-by-row retained ingest loop with a staged batch:

1. Scan a bounded visible window from `weft.log.tasks`.
2. Walk the window in FIFO order and build a contiguous retained prefix:
   - stop at the first valid too-young row;
   - stop at the configured delete batch size;
   - stop at the scan limit;
   - classify malformed or unsupported rows for exact delete;
   - convert valid retained task-log rows into `MonitorTaskEventUpdate`
     objects.
3. If valid updates exist, call `store.record_task_log_updates(...)` with the
   whole update chunk, not one update at a time.
4. After the Monitor store write succeeds, exact-delete the selected raw
   broker rows in queue-grouped batches.
5. Mark only actually deleted or verified-absent raw message IDs deleted in the
   Monitor table.
6. Advance the durable checkpoint only to the last row in the contiguous prefix
   when store write, broker delete, and Monitor delete marking all succeeded.

Malformed or unsupported rows should use the same exact-delete batch path as
valid rows, but they do not require a Monitor task update before deletion.

### 6.2 Store Update Shape

`MonitorStore.record_task_log_updates()` should remain the one public access
method for folding task-log events into the Monitor table, but internally it
must avoid one Monitor collation upsert per event when many events belong to
the same TID.

Within each configured write chunk:

1. Preserve FIFO order.
2. Group updates by TID while retaining each TID's ordered event list.
3. Fetch the existing collation row once per TID.
4. Fold all updates for that TID in memory with `_merge_record(...)`.
5. Upsert the collation row once per TID.
6. Insert or upsert all task-message child rows in the same transaction.

Do not invent a second store implementation. Keep the access layer in
`weft/core/monitor/store.py`.

### 6.3 Exact Delete Accounting

Fix the delete contract before relying on new batching.

`apply_exact_prune_candidates()` must not allow callers to confuse
`error is None` with "this exact row is gone." The result must distinguish:

- deleted now;
- already absent and verified absent;
- not deleted and still present;
- failed because the delete or verification errored;
- indeterminate partial batch.

The smallest compatible path is:

- keep the public function name;
- continue returning caller-shaped results;
- set `deleted=True` only when the exact row was deleted or verified absent
  after an idempotent exact-delete attempt;
- set `deleted=False` when the row remains present or the batch result is
  indeterminate;
- include an error string for true failures.

Then update `TaskMonitorTask._delete_exact_task_log_rows()` to return the exact
IDs that are gone, not only a count. Monitor table state must be updated only
for those IDs.

If verifying absent rows with the public SimpleBroker API proves impossible for
claimed rows, stop and revise. Do not drop to private SQL. For visible
`weft.log.tasks` rows scanned by `peek_generator`, `peek_generator` with
`exact_timestamp` or `Queue.peek(message_id=...)` should be enough to verify
post-delete absence.

### 6.4 Monitor Delete Marking

`MonitorStore.mark_messages_deleted()` must batch both child-row updates and
parent raw-deleted reconciliation:

- Add SQL helper for:

```sql
UPDATE weft_monitor_task_messages
SET deleted_at_ns = ?
WHERE context_key = ?
  AND message_id IN (...)
```

- Add SQL helper for affected-TID-only reconciliation:

```sql
UPDATE weft_monitor_task_collations AS c
SET raw_deleted_at_ns = ?, updated_at_ns = ?
WHERE c.context_key = ?
  AND c.tid IN (...)
  AND c.raw_deleted_at_ns IS NULL
  AND EXISTS (...)
  AND NOT EXISTS (...)
```

Use the existing `tids_for_message_ids()` idea, but do it in a way that avoids
a broad context scan after each row. It is acceptable to run one affected-TID
lookup per chunk. It is not acceptable to run broad reconciliation once per
message.

### 6.5 Main Loop Responsiveness

The built-in cleanup path currently runs on the monitor reactor thread. This is
acceptable only if one `process_once()` turn is bounded. The first fix should
avoid a larger thread/worker redesign by making the cleanup work efficient and
bounded.

Add or enforce a time/size budget:

- `WEFT_TASK_MONITOR_BATCH_SIZE` remains the max rows selected for deletion in
  one cycle.
- `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT` remains the max rows scanned in one
  cycle.
- Do not allow summary disposition to run if retained FIFO ingestion stopped at
  `batch_limit` or scan limit.
- Keep catch-up interval behavior: if backlog remains, schedule the next cycle
  on `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS`.
- If tests or ops evidence show one cycle still blocks control longer than a
  few seconds after batching, stop and plan a worker-lane move for built-in
  cleanup. Do not quietly add sleeps.

### 6.6 Summary And Control Queue Disposition

This is the second slice after raw-log batching is correct.

When high water is complete:

- keep `store.list_summary_ready_tasks(...)` as the selection API;
- emit external collated summaries before disposition;
- batch `mark_summary_emitted` for summaries that successfully emitted;
- batch `mark_family_disposed` for rows that completed summary and any
  required terminal control cleanup;
- keep task-local control queue deletion exact and bounded;
- do not broaden terminal cleanup beyond `ctrl_in` and `ctrl_out`.

It is acceptable for control queue deletion to remain per task in the first
batching slice, because the observed hot stack is raw-log deletion and Monitor
message marking. Do not spend the first fix on `ctrl_in` and `ctrl_out` unless
raw-log catch-up has already become healthy.

## 7. Bite-Sized Implementation Tasks

### Task 1: Add Failing Tests For Delete Truthfulness

Files:

- `tests/core/test_pruning_apply.py` or an existing pruning test module if one
  already owns exact-delete helper behavior.
- `tests/tasks/test_task_monitor.py`.

Tests:

- Create a real queue and an exact prune candidate for a missing message ID.
  Assert the applied result is not counted as deleted.
- Create a real `weft.log.tasks` row, delete it once, then retry the same exact
  delete path. Assert the retry does not produce a false "deleted now" count
  that would inflate Monitor stats.
- Add a TaskMonitor-level test where `_delete_exact_task_log_rows()` receives a
  row that is already absent. Assert the returned deleted-ID set does not cause
  `store.mark_messages_deleted()` to mark that ID unless absence has been
  explicitly verified and accepted as idempotent success.

Do not mock `Queue.delete_many()`. Use real queues. If an error path is needed,
monkeypatch only a narrow queue method after at least one real-queue test
exists.

### Task 2: Fix Exact Delete Result Semantics

Files:

- `weft/core/pruning/apply.py`
- `weft/core/monitor/task_monitor.py`
- Existing tests from Task 1.

Implementation:

- Change `apply_exact_prune_candidates()` so caller-shaped results get
  `deleted=True` only when the exact row is gone according to the defined
  delete contract.
- For full batch success, mark every candidate deleted.
- For full zero-delete or partial-delete cases, verify remaining exact IDs by
  public queue API before deciding whether each candidate is gone.
- For verification failure, return `deleted=False` with an error.
- Update `_delete_exact_task_log_rows()` to return an object such as:

```python
@dataclass(frozen=True, slots=True)
class _ExactTaskLogDeleteResult:
    deleted_ids: tuple[int, ...]
    errors: tuple[str, ...]
```

- Stop counting `error is None` as success.

Stop gate:

- If this requires private SimpleBroker SQL to verify rows, stop and revise the
  plan. The fix must stay on public queue APIs unless we explicitly add a
  SimpleBroker API first.

### Task 3: Batch Retained FIFO Ingestion In TaskMonitor

Files:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Implementation:

- Rewrite `_ingest_retained_task_log_rows()` into staged collection and batch
  application.
- Keep scanning read-only.
- Build a contiguous selected prefix and track:
  - `scanned`
  - `malformed_selected`
  - `unsupported_selected`
  - `valid_updates`
  - `selected_raw_rows`
  - `last_selected_message_id`
  - `stop_reason`
- Call `store.record_task_log_updates(...)` once per update chunk, not per row.
- Delete selected raw rows once per chunk, not per row.
- Call `store.mark_messages_deleted(...)` once per deleted-ID chunk, not per
  row.
- Set checkpoint only if every selected row through the checkpoint was handled
  successfully.

Required tests:

- Seed at least 25 old retained `weft.log.tasks` rows with repeated TIDs. Run
  one `TaskMonitorTask.process_once()`. Assert:
  - the log queue shrinks by the selected count;
  - Monitor task-message rows are marked deleted for the selected IDs;
  - checkpoint advances to the last selected row;
  - PONG cached counters report the selected/deleted counts.
- Instrument the real `MonitorStore.record_task_log_updates()` method with a
  delegating wrapper and assert it is called by chunk, not once per row. This
  is an implementation-performance contract; keep all queue and SQL behavior
  real.
- Force delete failure with a narrow monkeypatch and assert checkpoint does not
  advance past failed deletion.

### Task 4: Batch Monitor Store Folding By TID

Files:

- `weft/core/monitor/store.py`
- `weft/core/monitor/sql.py` only if needed for batch child-row upsert.
- `tests/core/test_monitor_store.py`

Implementation:

- In `MonitorStore.record_task_log_updates()`, group updates by TID within each
  chunk.
- Fetch each existing task record once per TID per chunk.
- Merge all updates for that TID in memory, preserving event order.
- Upsert the parent collation row once per TID.
- Upsert child message rows inside the same transaction.
- Preserve `tasks_updated`, `terminal_tasks`, and checkpoint semantics.

Required tests:

- Use real SQLite-backed Monitor store.
- Feed many updates for one TID and another update for a second TID.
- Assert final merged record has the right first/last message IDs, terminal
  state, resources, and task-message child rows.
- Add a targeted instrumentation test that proves repeated events for one TID
  do not call `fetch_task` and `upsert_record` once per event. This is allowed
  because the production SQL path remains real.

### Task 5: Batch Deleted-Message Marking And Affected-TID Reconciliation

Files:

- `weft/core/monitor/store.py`
- `weft/core/monitor/sql.py`
- `tests/core/test_monitor_store.py`
- `tests/core/test_monitor_sql.py`

Implementation:

- Add SQL builder for batch message deleted marking using `IN (...)`
  placeholders.
- Add SQL builder for raw-deleted reconciliation limited to affected TIDs.
- Update `MonitorStore.mark_messages_deleted()` to:
  - chunk message IDs;
  - look up affected TIDs for each chunk;
  - update child messages in one statement per chunk;
  - reconcile only affected TIDs;
  - commit once per chunk.
- Preserve idempotency for already marked message IDs.

Required tests:

- Existing `test_monitor_store_mark_messages_deleted_reconciles_parent` should
  still pass.
- Add a test with two TIDs where deleting all rows for one TID marks only that
  TID's `raw_deleted_at_ns`.
- Add a SQL-shape test that `reconcile_raw_deleted_tasks_for_tids` includes a
  TID predicate and does not scan every collation row in the context.

### Task 6: Preserve Loop Responsiveness And Catch-Up Semantics

Files:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`
- Possibly `tests/cli/test_cli_serve.py` if a process-level check is needed.

Implementation:

- Keep each cleanup turn bounded by selected row count and scan limit.
- Keep catch-up scheduling when `stop_reason` is `batch_limit` or
  `task_log_scan_limit_reached`.
- Ensure `_finish_monitor_cycle()` sets activity back to `waiting` after each
  bounded turn.
- Do not run summary disposition unless retained FIFO ingestion reached high
  water.

Required tests:

- Seed more rows than `WEFT_TASK_MONITOR_BATCH_SIZE`; run one cycle; assert:
  - only one batch is deleted;
  - `catchup_pending` is true;
  - next interval uses catch-up interval;
  - activity returns to `waiting`.
- Then run repeated cycles until high water and assert summary disposition
  happens only after high water.

### Task 7: Batch Summary And Disposition Where Safe

Files:

- `weft/core/monitor/store.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/sql.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_store.py`

Implementation:

- Add batch store methods for:
  - marking summaries emitted;
  - marking task control cleanup complete;
  - marking families disposed.
- Keep external logging per task summary, because each write can fail
  independently.
- Accumulate only successfully emitted summaries for batch marking.
- Keep control queue deletion exact and bounded. Do not broaden it to inbox,
  outbox, or reserved queues.

Required tests:

- Existing external logging failure test must still prove no disposition when
  external logging fails.
- Add a test with multiple terminal families ready in one cycle and assert all
  are disposed with one batch store call after successful summary handling.

### Task 8: Update PONG And Operational Evidence

Files:

- `weft/core/monitor/task_monitor.py`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/00-Quick_Reference.md`
- `tests/tasks/test_task_monitor.py`

Implementation:

- Keep PONG lightweight. It must report cached counters only.
- Add or verify cached counters for:
  - retained rows scanned;
  - raw rows selected;
  - store update chunks;
  - exact delete chunks;
  - Monitor deleted-mark chunks;
  - deleted IDs;
  - missing/verified-absent IDs;
  - checkpoint advanced or blocked;
  - catch-up pending.
- Do not calculate live database counts inside PONG.

Required tests:

- Run a cleanup cycle and PING the task through its control path.
- Assert PONG includes the cached batch counters and does not perform a fresh
  cleanup or DB scan.

## 8. Rollout And Rollback

Rollout:

1. Ship the batching fix with existing config defaults.
2. Deploy to ops with `WEFT_TASK_MONITOR_PROCESSOR=delete` unchanged.
3. Verify the active manager is not CPU-hot.
4. Verify the active TaskMonitor PING responds during catch-up.
5. Watch `weft.log.tasks`, total rows, Monitor task-message deleted count, and
   terminal family disposition count over 5-10 minutes.

Expected ops evidence after deploy:

- `weft.log.tasks` should drop by roughly `WEFT_TASK_MONITOR_BATCH_SIZE` per
  catch-up cycle while old retained rows remain.
- `weft_monitor_task_messages.deleted_at_ns IS NOT NULL` should rise in
  matching batches.
- `weft_monitor_task_collations.raw_deleted_at_ns IS NOT NULL` should rise as
  families finish raw-row deletion.
- TaskMonitor PING should return during catch-up and report cached batch
  counters.
- CPU may spike briefly during a batch, but should not stay pinned for minutes
  while deleting only a handful of rows.

Rollback:

- Set `WEFT_TASK_MONITOR_PROCESSOR=report_only` to stop destructive cleanup
  while preserving observation.
- If the manager remains healthy but TaskMonitor is hot, disable
  `WEFT_TASK_MONITOR_ENABLED` temporarily and restart the manager service.
- No schema rollback is required for this slice if only additive store methods
  and SQL helpers are added. Do not remove Monitor tables in rollback.

## 9. Out Of Scope

- Adding private SQL queue deletion in Weft.
- Deleting `T{tid}.inbox`, `T{tid}.outbox`, or `T{tid}.reserved`.
- Reworking SimpleBroker's public API for claimed-row enumeration.
- Replacing the Monitor table design.
- Moving all built-in cleanup to a worker lane unless batching still leaves
  PING/STATUS starved.
- Queue-wide delete or global SimpleBroker vacuum as the normal Monitor policy.

## 10. Verification Commands

Run the smallest tests first:

```bash
uv run pytest tests/core/test_monitor_store.py tests/core/test_monitor_sql.py tests/tasks/test_task_monitor.py -q
```

Then run Postgres-backed targeted tests:

```bash
uv run bin/pytest-pg tests/core/test_monitor_store.py tests/core/test_monitor_sql.py tests/tasks/test_task_monitor.py
```

Then run quality gates:

```bash
uv run ruff check weft tests/core/test_monitor_store.py tests/core/test_monitor_sql.py tests/tasks/test_task_monitor.py
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run pytest
```

If Postgres differs from SQLite, prefer fixing the SQL/access-layer abstraction
over special-casing TaskMonitor behavior by backend.

## 11. Stop-And-Reevaluate Gates

Stop and revise the plan if:

- the implementation wants to call private SimpleBroker SQL for queue rows;
- tests mock away broker-backed queue deletion;
- checkpoint advancement becomes independent of exact-delete success;
- a second task-log deletion path appears for `collated_store`;
- batching requires changing public queue names, TaskSpec schema, or CLI
  behavior;
- one cleanup turn still blocks PING/STATUS under a realistic backlog after
  batching;
- Monitor table writes become backend-specific outside `store.py` and `sql.py`.

## 12. Fresh-Eyes Self-Review

Review findings:

- The first draft overemphasized generator offset mutation. Reading the code
  showed `GeneratorTaskLogScanner.scan_window()` materializes the window before
  deletion, so the primary fix must target per-row writes/deletes and incorrect
  delete accounting.
- The first draft treated `apply_exact_prune_candidates()` as already reliable
  because it batches by queue. That is false for this caller: `_ingest_retained`
  passes one row at a time, and `_delete_exact_task_log_rows()` counts
  `error is None` as success. The plan now makes delete truthfulness Task 1 and
  Task 2.
- The first draft wanted to optimize summary disposition immediately. Ops
  evidence points first at retained raw-log deletion and Monitor deleted-row
  marking, so summary batching is now a later task.
- The plan keeps the Monitor-table authority from the previous plan. It does
  not redirect cleanup back to the old window/family policies, so it stays
  aligned with the discussed direction.

External review is warranted before implementation because this is destructive
cleanup and persisted Monitor state. If no reviewer is available, the
implementer should at least run the stop gates above after Tasks 2, 3, and 5.
