# Monitor Collation Table Retirement Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4], [SB-0.4a]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]; docs/specifications/08-Testing_Strategy.md
Superseded by: none

## 1. Goal

Fix the Monitor collation-store deletion model so Monitor-managed rows are
physically retired instead of accumulating queue-like tombstones.

The specific design error is in `weft_monitor_task_messages`: it currently
acts too much like a shadow `weft.log.tasks` ledger. After the Monitor folds a
raw task-log row into `weft_monitor_task_collations` and deletes the exact raw
broker row, the child row in `weft_monitor_task_messages` is marked with
`deleted_at_ns`. That records that the raw broker row was handled, but it
leaves a permanent child-row tombstone. On ops, this produced about 190k rows
in `weft_monitor_task_messages`, with about 185k carrying `deleted_at_ns`.
The same production database showed `weft_monitor_task_collations` as the
larger table by disk footprint, so the parent rows also need an explicit
physical-retirement policy rather than an implicit "disposed rows can stay
forever" policy.

That is the wrong lifetime. The Monitor table is not a queue, not audit
storage, and not a permanent per-message history. It is a durable collation
workspace owned by the Monitor. Once the Monitor has finished the work that a
row exists to support, that row should be gone.

The target behavior:

- `weft_monitor_task_messages` stores only pending raw-message references that
  are still needed for exact raw broker deletion or retry.
- When exact raw broker deletion is known to have succeeded, or the raw broker
  row is already gone and reconciliation proves there is no more raw deletion
  work for that message, the corresponding `weft_monitor_task_messages` row is
  physically deleted.
- `weft_monitor_task_collations` remains the compact family-level state while
  the Monitor still needs it for summary emission, stale-open classification,
  task-local runtime cleanup, or retry bookkeeping.
- After a family has no child message rows left and its family-level work is
  complete, the parent `weft_monitor_task_collations` row is physically
  deleted too.
- No new policy gates are added. This is not a new optional behavior; it is the
  correct implementation of the existing `delete` processor and Monitor-store
  lifecycle.
- The steady-state growth envelope is bounded by active/open task families,
  retryable raw-delete refs, old-row backfill that is actively being drained,
  and families still awaiting summary or runtime cleanup. It must not be
  proportional to lifetime task-log messages or lifetime completed tasks.

This plan is intentionally narrow. It does not redesign task-log retention,
runtime queue policies, outbox/inbox retention, external log formats, manager
supervision, or SimpleBroker queue storage.

## 2. Source Documents

Read these before editing:

- `docs/specifications/00-Quick_Reference.md`
  - Current quick reference says `weft_monitor_task_messages` is a
    Monitor-owned operational table, not a queue. Update it if nearby wording
    implies queue-like row retention.
- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]
  - `TaskMonitorTask` is an internal service. It may maintain Monitor-owned
    operational tables but must not become task lifecycle or result authority.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4], [SB-0.4a]
  - Weft must access broker queues through public SimpleBroker APIs and
    `WeftContext`. Monitor-owned SQL tables are allowed, but only for Monitor
    state. Do not issue private SQL against SimpleBroker queue tables.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
  - This is the main normative section for retained task-log collation,
    Monitor-owned tables, exact raw-message deletion, summary/disposition, and
    cached PONG diagnostics. It currently describes recording raw message IDs
    before deleting broker rows; it must be amended to state that child rows
    are temporary pending refs and are physically deleted after raw deletion is
    reconciled.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]
  - Monitor cleanup is operational cleanup, not lifecycle truth or audit
    retention. Destructive cleanup must remain bounded, explicit, retry-safe,
    and must not delete ambiguous user payloads or active work.
- `docs/specifications/08-Testing_Strategy.md`
  - Use real broker and process tests for queue behavior. Do not hide broker
    semantics behind broad mocks.
- `docs/plans/2026-05-16-monitor-durable-collation-store-plan.md`
  - Original table concept. It established Monitor-owned collation state, but
    did not define row retirement tightly enough.
- `docs/plans/2026-05-16-monitor-store-hardening-and-layering-plan.md`
  - Store layering and SimpleBroker SQL-pattern guidance. Keep SQL builders in
    `weft/core/monitor/sql.py` and access methods in `weft/core/monitor/store.py`.
- `docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`
  - Historical draft context. It explicitly allowed a compact disposed
    Monitor row to remain as a tombstone against late rows. This plan rejects
    that steady-state behavior for the active slice: disposition fields may be
    temporary coordination markers, but completed Monitor rows must be
    physically retired once the required Monitor work is done. If these plans
    disagree on Monitor table retention, implement this plan and update the
    specs so the no-tombstone rule is normative.
- `docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`
  - Completed runtime cleanup context. Reuse its terminal control and reserved
    cleanup readiness signals; do not invent a second runtime cleanup policy
    just to make parent collation rows disappear.
- `docs/plans/2026-05-20-monitor-reactor-worker-refactor-plan.md`
  - Current monitor reactor/worker shape. Do not undo it. This plan changes
    what the worker does to Monitor tables, not whether work runs on the
    reactor.
- `../simplebroker/simplebroker/_sql/`
  - SQL construction examples. Follow the pattern: code-owned identifiers are
    validated, runtime values are parameters, placeholder counts are generated
    by helper functions, and backend-specific quoting is hidden behind helpers.
- `../simplebroker/simplebroker/sbqueue.py`
  - Public `Queue.delete(...)` and `Queue.delete_many(...)` behavior. Broker
    raw-row deletion must keep using public queue APIs or existing Weft helpers
    that wrap those APIs.

Required plan/runbook references:

- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

This work is high-risk enough to require review before implementation lands:
it changes destructive cleanup, Monitor persistence, retry behavior, and
production backlog recovery.

## 3. Current State

This section describes the pre-fix tombstone model that produced the ops
symptom. If an implementation branch already contains some of the new method
names from this plan, do not reintroduce the old names to match this section.
Use the target semantics, tests, and verification gates as the authority for
whether the branch is complete.

Current relevant schema in `weft/core/monitor/sql.py`:

- `weft_monitor_task_collations`
  - One compact row per `(context_key, tid)`.
  - Tracks family summary state such as `terminal_seen`,
    `summary_emitted_at_ns`, `raw_deleted_at_ns`, `disposition_at_ns`, and
    `task_control_deleted_at_ns`.
- `weft_monitor_task_messages`
  - One row per raw task-log message incorporated into the Monitor table.
  - Columns include `selected_for_delete_at_ns` and `deleted_at_ns`.
  - Primary key is `(context_key, tid, message_id)`.

Current relevant code path:

- `weft/core/monitor/task_monitor.py`
  - `_ingest_retained_task_log_rows(...)` scans retained `weft.log.tasks`
    rows, passes valid rows into `MonitorStore.record_task_log_updates(...)`,
    and deletes malformed rows directly from the broker.
  - `_delete_monitor_store_task_log_rows(...)` selects exact raw message refs
    from the Monitor store, deletes those raw broker rows, then calls
    `MonitorStore.mark_messages_deleted(...)`.
  - The current name says "mark", and the implementation matches it: the
    Monitor child rows remain in SQL with `deleted_at_ns`.
- `weft/core/monitor/store.py`
  - `MonitorStore.record_task_log_updates(...)` upserts parent family state and
    child message refs.
  - `MonitorStore.mark_messages_deleted(...)` looks up affected TIDs, sets
    `deleted_at_ns` on child rows, and reconciles `raw_deleted_at_ns` on parent
    rows once no undeleted child rows remain.
  - `MonitorStoreAccess.has_undeleted_messages(...)` and raw-deleted
    reconciliation queries define "done" as "all children have deleted_at_ns".
- `weft/core/monitor/sql.py`
  - `mark_message_deleted(...)`, `mark_messages_deleted(...)`,
    `select_remaining_undeleted_message(...)`,
    `reconcile_raw_deleted_tasks(...)`, and
    `reconcile_raw_deleted_tasks_for_tids(...)` all encode the tombstone model.

The production symptom:

- Ops had a large `weft_monitor_task_messages` table where nearly all rows
  were tombstones. That increased table scans, indexes, update cost, and mental
  load. It also made PONG and database inspection misleading: "deleted" in the
  Monitor table meant "broker row handled", not "Monitor row gone".
- Ops also showed `weft_monitor_task_collations` as a major space consumer.
  A parent row with `disposition_at_ns` set is still a retained Monitor row.
  Treat it as necessary only while it gates summary retry or task-local
  runtime cleanup; after that, retire it physically.

## 4. Target Semantics

The implementing engineer must use these exact semantics. Do not invent a
second lifecycle.

### 4.1 Child Message Row Lifetime

`weft_monitor_task_messages` rows are temporary pending raw-message refs.

A child row may exist only while the Monitor still needs to prove or retry
exact raw broker deletion for that message. Once the raw broker deletion is
known to be complete for that message, physically delete the child row.

Allowed child-row states after this change:

- Present: raw message ref is still pending deletion or retry.
- Absent: raw message ref has been handled, or the family has been fully
  retired.

Disallowed child-row state after this change:

- Present only to remember that deletion already happened.

Do not use `deleted_at_ns` as an active state machine. Existing databases may
have that column and old tombstone rows. The implementation may leave the
column in place for additive migration safety, but new code must stop writing
it and must remove old rows that have it set.

The column name may remain in schema version 1 because dropping a column is a
separate compatibility and migration decision. Keeping the column is not
permission to keep using it as a tombstone state.

### 4.2 Parent Collation Row Lifetime

`weft_monitor_task_collations` rows are compact family-level Monitor state.
They may outlive child message rows only while family-level work remains.

Keep the parent row while any of these are true:

- raw child message refs still exist for that TID;
- raw deletion has not been reconciled;
- a terminal or suspected-dead family still needs summary emission;
- task-local runtime cleanup still needs the family row to decide whether
  standard `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, or stale `T{tid}.reserved`
  cleanup is eligible;
- the family is open and not yet past the stale-open policy;
- the family is ambiguous and the current policy says to keep it.

Retire the parent row only after all of these are true:

- no `weft_monitor_task_messages` rows remain for `(context_key, tid)`;
- `raw_deleted_at_ns IS NOT NULL`;
- if summary/disposition policy applies, `summary_emitted_at_ns IS NOT NULL`
  and `disposition_at_ns IS NOT NULL`;
- if the family has standard task-control cleanup obligations,
  `task_control_deleted_at_ns IS NOT NULL`;
- the family is not active work and does not require reserved-queue probing
  under the current runtime cleanup policy.

If a condition is ambiguous in code, keep the parent row and report a cached
warning. Do not delete parent rows speculatively.

Do not treat `disposition_at_ns` itself as the final retention mechanism. It
is a coordination marker that lets the next cleanup step know summary and
runtime-cleanup work crossed a boundary. Once all required boundaries are
crossed, physical parent-row deletion is the desired steady state.

### 4.2.1 Expected Growth Envelope

After this plan lands and a catch-up period completes, Monitor tables should
grow with current operational backlog, not with historical throughput.

Expected steady-state occupants:

- `weft_monitor_task_messages`: only raw task-log message refs waiting for
  exact broker deletion or retry. Old rows with `deleted_at_ns IS NOT NULL`
  should drain to zero and new code should not create more.
- `weft_monitor_task_collations`: task families that are still open, still
  ambiguous, still waiting for summary emission, still waiting for terminal
  control/reserved cleanup, or still needed because a raw-delete retry is
  pending.
- `weft_monitor_meta`: small store metadata only.

Unexpected steady-state occupants:

- child message rows for raw broker rows already deleted;
- parent collation rows where raw deletion, summary, disposition, and
  task-local cleanup are all complete;
- rows kept solely to remember that Monitor cleanup once happened.

If ops still sees table size grow roughly with total lifetime task-log volume
after catch-up, this plan has not solved the problem.

### 4.3 Existing Tombstone Backfill

The implementation must handle databases created by prior releases.

If a `weft_monitor_task_messages` row already has `deleted_at_ns IS NOT NULL`,
it represents an already-handled raw broker row under the old model. The new
store cleanup must physically remove these rows in bounded batches. After
removing old tombstones, reconcile the affected parent TIDs exactly as if the
new code had deleted the child rows itself.

This is required for ops recovery. Do not treat it as optional migration
cleanup.

### 4.4 No New Policy Gates

Do not add a new environment variable such as
`WEFT_MONITOR_TABLE_RETIREMENT_ENABLED`.

The Monitor already has processor selection and collation-store enablement.
This fix is part of the existing table-backed `delete` path. New knobs would
make the broken behavior easier to accidentally keep deployed.

### 4.5 Layer Boundary

Use SQL only for Monitor-owned tables:

- `weft_monitor_meta`
- `weft_monitor_task_collations`
- `weft_monitor_task_messages`

Use public SimpleBroker queue APIs, or existing Weft pruning helpers built on
those APIs, for `weft.log.tasks` and task-local queues.

Do not add private SQL against SimpleBroker queue tables in Weft.

### 4.6 Ops Inspection Boundary

The implementation should not need a live ops SSH session to decide the code
shape. The bug report already provides the production symptom, and the local
code path shows the row-retention model. Use ops only for read-only validation
before or after rollout:

- row counts by table;
- count of `weft_monitor_task_messages.deleted_at_ns IS NOT NULL`;
- count of parent rows that satisfy the conservative retirement predicate;
- TaskMonitor cached PONG counters after one or more cleanup cycles.

Do not run destructive SQL directly on ops as part of this plan. The product
fix must be the Monitor cleanup path itself, with tests and rollout signals.

## 5. Files To Modify

Primary implementation files:

- `weft/core/monitor/sql.py`
  - Replace active tombstone-oriented SQL builders with physical delete and
    retirement builders.
  - Add bounded cleanup SQL for old `deleted_at_ns IS NOT NULL` child rows.
  - Add parent-retirement selection/deletion SQL.
  - Keep every runtime value parameterized.
- `weft/core/monitor/store.py`
  - Encapsulate all Monitor-table changes in access methods.
  - Rename public methods so callers cannot confuse "mark" with physical
    deletion.
  - Preserve transaction boundaries around multi-step child delete plus parent
    reconciliation.
  - Add stats objects or extend existing stats so PONG can report how many
    child tombstones were pruned and how many parent families were retired.
- `weft/core/monitor/task_monitor.py`
  - Update built-in cycle workers to call the new store methods.
  - Commit cached stats after workers return.
  - Keep PONG cheap: do not query Monitor tables while answering PING.
- `weft/core/monitor/runtime.py` if cached PONG/runtime data classes live
  there after the reactor-worker refactor.
  - Add cached fields only if needed for diagnostics.
- `weft/_constants.py`
  - Do not add new gates. Touch only if existing stats labels or config names
    need docstring clarification.

Specification and docs files:

- `docs/specifications/00-Quick_Reference.md`
  - Clarify that Monitor-owned tables are operational workspace tables, and
    child message rows are temporary pending refs.
- `docs/specifications/01-Core_Components.md`
  - Update Monitor implementation mapping if it currently implies durable
    child-message tombstones.
- `docs/specifications/04-SimpleBroker_Integration.md`
  - Keep or add a note that Monitor table SQL is allowed only for
    Monitor-owned tables; broker queues still use public SimpleBroker APIs.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
  - Amend the retained task-log collation text to describe physical child-row
    deletion and parent-row retirement conditions.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]
  - Add the invariant that Monitor-table deletion means physical table-row
    deletion, not a queue-like claim/tombstone state.

Tests:

- `tests/core/test_monitor_sql.py`
  - SQL builder tests for physical child-row deletion, old tombstone pruning,
    affected-TID selection, parent raw-deleted reconciliation with no remaining
    children, and parent retirement selection/deletion.
- `tests/core/test_monitor_store.py`
  - Store behavior tests against real SQLite Monitor tables.
- `tests/tasks/test_task_monitor.py`
  - End-to-end TaskMonitor tests with real broker queues and real Monitor
    store access.
- `tests/core/test_task_monitor_cleanup.py`
  - Adjust only if helper names or stats structures change.
- `tests/specs/test_plan_metadata.py`
  - The plan README index must include this plan.
- Shared test-audit metadata if adding or moving test modules.

Do not add broad mocks around `Queue`, `BrokerTarget`, or `MonitorStore`.
Narrow monkeypatches are acceptable only for failure injection where the real
failure is hard to create deterministically, such as forcing an external log
write failure.

## 6. Implementation Tasks

### Task 1: Red Test For Physical Child Deletion

Owner: Monitor store.

Write a failing test in `tests/core/test_monitor_store.py` before editing
store code.

Test shape:

1. Create a `MonitorStore` against a real temporary SQLite context.
2. Record a task family with at least two task-log messages using
   `record_task_log_updates(...)`: one start-ish row and one terminal row.
3. Call the current raw-deletion completion path for one message.
4. Assert the table still has the other child row and parent
   `raw_deleted_at_ns` is still `None`.
5. Call the raw-deletion completion path for the second message.
6. Assert:
   - `weft_monitor_task_messages` has zero rows for that TID;
   - parent `raw_deleted_at_ns` is set;
   - no child row remains with `deleted_at_ns IS NOT NULL`.

The test should fail against current code because child rows are updated, not
deleted.

Implementation detail:

- Add a small test helper that counts rows from Monitor-owned tables through
  the test database connection. Keep it local to the test file.
- Do not use `weft queue *` helpers for Monitor tables. They are not queues.

### Task 2: Replace Child Tombstone SQL With Physical Delete SQL

Owner: Monitor SQL and store access.

Edit `weft/core/monitor/sql.py`:

- Add a builder equivalent to:
  - `delete_task_messages(messages_table: str, message_id_count: int) -> str`
  - Deletes rows by `context_key` and exact `message_id IN (...)`.
- Add a builder for old tombstones:
  - `select_deleted_task_message_refs(messages_table: str) -> str`
  - It must return bounded `(tid, message_id)` rows where
    `deleted_at_ns IS NOT NULL`, ordered by `message_id`.
  - Keep it portable across SQLite and Postgres. Prefer
    `SELECT tid, message_id ... LIMIT ?`, then
    `DELETE ... WHERE message_id IN (...)`, then reconcile affected TIDs.
    Do not use `RETURNING` unless the existing runner abstraction already
    supports it cleanly across both backends.
- Replace or deprecate active callers of:
  - `mark_message_deleted(...)`
  - `mark_messages_deleted(...)`
  - `select_remaining_undeleted_message(...)`
- Add a builder for checking any remaining child rows:
  - `select_remaining_task_message(messages_table: str) -> str`
  - It should check existence of any row for `(context_key, tid)`, not
    `deleted_at_ns IS NULL`.
- Update reconciliation SQL:
  - `raw_deleted_at_ns` should be set when no child rows remain for a parent,
    not when all child rows are tombstoned.
  - Use `NOT EXISTS (SELECT 1 FROM messages ... WHERE same context/tid)`.

Style requirements:

- Dynamic table/index names must pass through `identifier(...)`.
- Placeholder lists must use the existing `placeholders(...)` helper.
- Runtime values must stay parameters.
- Keep function names explicit. Prefer `delete_task_messages(...)` over
  generic `delete_messages(...)` in SQL builder names.

### Task 3: Replace Store Access Methods And Preserve Transactions

Owner: `weft/core/monitor/store.py`.

Add or replace access-layer methods:

- `delete_task_messages(message_ids: Sequence[int]) -> set[str]`
  - Returns affected TIDs.
  - Finds affected TIDs before physical deletion, inside the same write
    transaction as the delete.
- `prune_deleted_task_message_tombstones(limit: int, pruned_at_ns: int) -> set[str]`
  - Bounded cleanup for old rows where `deleted_at_ns IS NOT NULL`.
  - Returns affected TIDs for reconciliation.
  - Name should make clear this is a migration/recovery cleanup path.
- `has_task_messages(tid: str) -> bool`
  - Replaces `has_undeleted_messages(...)`.
- `reconcile_raw_deleted_for_tids(tids: Sequence[str], deleted_at_ns: int) -> None`
  - Uses the new no-child-row semantics.

Rename the public store method:

- Replace `MonitorStore.mark_messages_deleted(...)` with
  `MonitorStore.delete_task_messages_after_raw_delete(...)` or a similarly
  explicit name.
- Update all callers. Do not leave an alias with the old name unless a test or
  internal call still requires it during the same patch; if a temporary helper
  is needed during refactor, remove it before finishing.

Transaction requirements:

- For each chunk:
  1. begin write transaction using the existing store transaction helper;
  2. select affected TIDs from child rows;
  3. physically delete child rows;
  4. reconcile `raw_deleted_at_ns` for affected TIDs;
  5. commit.
- If any step fails, rollback the chunk and leave rows available for retry.
- Do not delete parent rows in this method. Parent retirement is a separate
  operation after summary/disposition/runtime cleanup conditions are met.

Stats:

- Return or update a small typed result containing:
  - child message rows deleted;
  - old tombstone rows pruned;
  - affected TID count;
  - parent rows reconciled if cheaply available.
- If exact parent reconciliation count is hard to get portably, do not fake it.
  Report only what the code can know.

### Task 4: Add Parent Family Retirement

Owner: Monitor store.

Add a bounded parent-retirement method in `MonitorStore`:

- Suggested public method:
  - `retire_completed_collation_families(limit: int, retired_at_ns: int) -> MonitorFamilyRetirementResult`
- Suggested access-layer methods:
  - `list_retirable_task_collations(limit: int) -> tuple[str, ...]`
  - `delete_task_collations(tids: Sequence[str]) -> int`

Retirement conditions:

- `raw_deleted_at_ns IS NOT NULL`;
- no child message rows remain for the TID;
- one of:
  - terminal/suspect family has `summary_emitted_at_ns IS NOT NULL`,
    `disposition_at_ns IS NOT NULL`, and `task_control_deleted_at_ns IS NOT NULL`;
  - the family has no standard task-control cleanup obligation under the
    current schema and policy, and the code can prove that from the stored
    taskspec summary;
  - future explicit policy says no summary/control cleanup is required.

For this implementation, prefer the conservative rule:

`raw_deleted_at_ns IS NOT NULL`
and `summary_emitted_at_ns IS NOT NULL`
and `disposition_at_ns IS NOT NULL`
and `task_control_deleted_at_ns IS NOT NULL`
and no child rows remain.

That conservative rule is acceptable because it fixes unbounded child-table
tombstones without risking loss of parent rows needed for control cleanup.

Do not try to solve nonstandard `T*.inbox`, `T*.outbox`, or user-payload
retention in this plan. Parent retirement can broaden later if the specs define
those cases.

### Task 5: Add Existing Tombstone Cleanup To The Built-In Worker Cycle

Owner: TaskMonitor worker orchestration.

Update `weft/core/monitor/task_monitor.py` so the built-in delete cycle runs a
bounded Monitor-store retirement slice after raw task-log deletion/reconcile.

Order for each built-in cycle:

1. Delete malformed `weft.log.tasks` rows before collation, as current policy
   already does.
2. Ingest retained valid task-log rows into Monitor-owned collation tables.
3. Delete exact raw broker rows selected by Monitor-store proof.
4. Physically delete the corresponding child message rows and reconcile parent
   `raw_deleted_at_ns`.
5. Prune old child-message tombstones left by previous releases.
6. Emit/dispose summaries according to existing policy.
7. Run task-control and reserved runtime cleanup according to existing policy.
8. Retire completed parent collation rows conservatively.

The exact position of steps 5 and 8 matters:

- Old child tombstones must be removed after the store is available and inside
  bounded worker work, not in schema setup or PONG.
- Parent retirement must run after summary/control cleanup, not before.

Cached PONG diagnostics:

- Add cached counters under existing task-monitor PONG stats if the data
  structure already has a place for cleanup/store counters:
  - `monitor_store_message_rows_deleted`
  - `monitor_store_message_tombstones_pruned`
  - `monitor_store_families_retired`
- Do not query SQL while answering PING.
- If adding new names creates churn, prefer one nested cached object such as
  `monitor_store_retirement` with these fields.

### Task 6: Update TaskMonitor Tests End To End

Owner: TaskMonitor tests.

Add or update tests in `tests/tasks/test_task_monitor.py` using real broker
queues:

1. `test_task_monitor_deletes_monitor_message_rows_after_raw_task_log_delete`
   - Arrange a retained terminal task family in `weft.log.tasks`.
   - Run the built-in delete cycle.
   - Assert raw broker rows are gone.
   - Assert `weft_monitor_task_messages` has no rows for that TID after raw
     deletion reconciliation.
   - Assert parent row remains if control cleanup has not run.
2. `test_task_monitor_prunes_old_monitor_message_tombstones`
   - Seed Monitor tables with a parent row and child rows carrying
     `deleted_at_ns`.
   - Run one worker cycle.
   - Assert tombstone child rows are physically gone and parent
     `raw_deleted_at_ns` is reconciled when no child rows remain.
3. `test_task_monitor_retires_parent_after_summary_and_control_cleanup`
   - Arrange a terminal family old enough for summary and runtime cleanup.
   - Run enough bounded cycles to emit summary, delete raw rows, delete control
     queues, and retire parent.
   - Assert parent and child Monitor rows are gone.
   - Assert this does not delete unrelated active families.
4. `test_task_monitor_keeps_parent_when_control_cleanup_not_complete`
   - Arrange a terminal family where raw child rows are gone but
     `task_control_deleted_at_ns` is still `NULL`.
   - Run parent retirement.
   - Assert parent remains.

Avoid timing-only tests. Use old synthetic TIDs/message IDs and explicit
retention config so eligibility is deterministic.

### Task 7: Update Store And SQL Unit Tests

Owner: Monitor store and SQL tests.

Update `tests/core/test_monitor_sql.py`:

- Existing tests that assert `"deleted_at_ns IS NULL"` in raw-deleted
  reconciliation queries must change to assert no-child-row semantics.
- Add tests that `delete_task_messages(...)` uses `DELETE FROM`, not `UPDATE`.
- Add tests that parent-retirement query includes:
  - `raw_deleted_at_ns IS NOT NULL`;
  - `summary_emitted_at_ns IS NOT NULL`;
  - `disposition_at_ns IS NOT NULL`;
  - `task_control_deleted_at_ns IS NOT NULL`;
  - `NOT EXISTS` against child rows.

Update `tests/core/test_monitor_store.py`:

- Replace tests named around `mark_messages_deleted` with names around
  physical deletion and reconciliation.
- Add an idempotency test:
  - If a raw broker row was already gone but a child monitor ref remains,
    calling the store delete/reconcile method still physically removes the
    child ref and reconciles the parent.
  - This covers crash/retry between broker deletion and Monitor-store update.
- Add a chunking test using more message IDs than `write_batch_size`.
  Assert all child rows are physically gone and all affected parents reconcile.

Testing style:

- Use real SQLite-backed Monitor tables.
- Do not mock SQL runner behavior except in narrow SQL-builder tests.
- Keep helper functions small and local.

### Task 8: Update Specs And Traceability

Owner: docs and spec traceability.

Update these docs in the same patch as the implementation:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
  - Replace wording that implies `weft_monitor_task_messages` keeps
    incorporated raw message IDs after deletion.
  - State that child rows are temporary pending refs.
  - State that physical child deletion follows broker raw deletion
    reconciliation.
  - State conservative parent-retirement conditions.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]
  - Add or tighten invariant: Monitor-owned table cleanup physically deletes
    finished Monitor rows; no queue-like claim/tombstone mechanism is used for
    the Monitor collation tables.
- `docs/specifications/04-SimpleBroker_Integration.md`
  - Confirm the layer boundary: Monitor table SQL is fine for Monitor-owned
    tables; queue rows still go through SimpleBroker public APIs.
- `docs/specifications/00-Quick_Reference.md`
  - Add one sentence to the Monitor table note if needed.

Update module docstrings or function docstrings:

- `weft/core/monitor/sql.py`
  - New SQL builders should reference [MF-5] and [OBS.13] where they own a
    spec boundary.
- `weft/core/monitor/store.py`
  - Public methods that delete child rows or retire parents need `Spec:`
    references.
- `weft/core/monitor/task_monitor.py`
  - Worker cycle comments should describe the order of raw deletion,
    child-row deletion, tombstone pruning, and parent retirement.

Do not leave the plan as the only explanation. Specs are normative; plans are
execution context.

### Task 9: Verification Gates

Run the smallest gates first, then expand.

Required local gates:

```bash
uv run pytest tests/core/test_monitor_sql.py -q
uv run pytest tests/core/test_monitor_store.py -q
uv run pytest tests/tasks/test_task_monitor.py -q
uv run pytest tests/core/test_task_monitor_cleanup.py -q
uv run pytest tests/specs/test_plan_metadata.py -q
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft tests/core/test_monitor_sql.py tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py
```

Required Postgres gate because the bug was observed in ops/Postgres:

```bash
uv run bin/pytest-pg tests/core/test_monitor_store.py -q
uv run bin/pytest-pg tests/tasks/test_task_monitor.py -q
```

Expanded gate before marking complete:

```bash
uv run pytest
uv run bin/pytest-pg
```

If the full gates are too slow during development, record the smaller passing
gates and the reason the full gate was deferred. Do not claim completion
without either running the full gates or explicitly documenting the gap.

## 7. Invariants To Protect

- Monitor-owned tables are operational derived state only. They are not task
  lifecycle truth, result authority, audit storage, or queue storage.
- Raw `weft.log.tasks` rows must be folded into parent collation state before
  the raw broker rows are deleted.
- Exact broker deletes must remain exact. Delete only specific message IDs
  selected by supported policies.
- In collated mode, durable Monitor ingestion is the gate for raw task-log
  deletion. External summary failure must keep parent state available for
  retry and must block disposition/parent retirement, but it must not require
  already deleted raw rows to be resurrected. In raw external mode, preserve
  emit-before-delete behavior. Do not weaken existing fail-closed behavior.
- A crash between broker delete and Monitor-store child-row deletion must be
  retry-safe. On retry, the Monitor must be able to remove stale child refs and
  reconcile parent state.
- Parent collation rows must not be retired while they are still needed for
  summary emission, stale-open classification, task-control cleanup, or
  reserved cleanup.
- The TaskMonitor reactor must stay responsive. Do not move cleanup SQL back
  onto the reactor.
- PONG must use cached state only. No SQL queries, queue scans, or cleanup work
  while answering `PING`.
- Do not add new env gates or compatibility aliases to keep the old tombstone
  behavior.
- Do not drop Monitor-table columns in a way that breaks existing deployed
  databases. Additive migration is acceptable; active code should simply stop
  using obsolete columns.

## 8. Out Of Scope

These are real issues, but they are not part of this plan:

- Redefining `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`.
- Changing external task-summary log formats.
- Creating a permanent task-history database.
- Deleting `T{tid}.inbox` or `T{tid}.outbox` rows.
- Broadening stale reserved-queue policy beyond the policies already in the
  repo.
- Rewriting SimpleBroker storage or using private queue SQL from Weft.
- Multi-monitor leasing, sharding, or active-active Monitor workers.
- Dropping old Monitor-table columns in a non-additive migration.

If implementation starts drifting into these areas, stop and write a separate
plan or return to the user. Do not smuggle a broader cleanup redesign into
this fix.

## 9. Rollout And Ops Expectations

After deployment to ops, expected evidence:

- `weft_monitor_task_messages` total rows should fall quickly from tombstone
  backlog toward the number of pending raw task-log refs.
- Rows with `deleted_at_ns IS NOT NULL` should trend to zero and stay near
  zero. New code should not create more of them.
- `weft.log.tasks` should still be governed by retained task-log policy; this
  plan does not alone guarantee it drops to near zero.
- `weft_monitor_task_collations` should not fall as fast as child rows. It
  should fall only when summary/disposition/runtime cleanup conditions are
  complete.
- TaskMonitor PING should remain responsive because all pruning and retirement
  work remains in worker lanes.
- Monitor CPU may be elevated during tombstone cleanup but should trend down
  as child-table scans shrink.

Suggested read-only ops checks after deploy:

```sql
SELECT count(*) FROM weft_monitor_task_messages;
SELECT count(*) FROM weft_monitor_task_messages WHERE deleted_at_ns IS NOT NULL;
SELECT count(*) FROM weft_monitor_task_collations;
SELECT count(*) FROM weft_monitor_task_collations c
WHERE c.raw_deleted_at_ns IS NOT NULL
  AND c.summary_emitted_at_ns IS NOT NULL
  AND c.disposition_at_ns IS NOT NULL
  AND c.task_control_deleted_at_ns IS NOT NULL;
```

Use `weft task ping <monitor_tid>` to verify cached counters. Do not rely on
PONG to actively scan or count tables.

## 10. Independent Review Loop

This plan changes destructive cleanup, Monitor-owned persistence, retry
behavior, and production backlog recovery. It is not implementation-ready until
an independent reviewer has checked it against the code and specs.

Recommended reviewer stance:

> Read `docs/plans/2026-05-20-monitor-collation-table-retirement-plan.md`,
> `docs/specifications/05-Message_Flow_and_State.md` [MF-5],
> `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17],
> `weft/core/monitor/sql.py`, `weft/core/monitor/store.py`, and
> `weft/core/monitor/task_monitor.py`. Look for errors, bad ideas, destructive
> cleanup hazards, stale assumptions, and latent ambiguities. Do not implement
> anything. Answer whether a zero-context skilled engineer could implement the
> plan confidently and correctly.

The reviewer should specifically challenge:

- whether the parent-retirement predicate is conservative enough to avoid
  deleting rows still needed for summary retry, task-local control cleanup, or
  reserved cleanup;
- whether the plan fully rejects Monitor tombstones rather than merely
  renaming them;
- whether any step would tempt an implementer into private SQL against
  SimpleBroker queue tables;
- whether tests prove physical deletion with real broker/store paths instead
  of mocks;
- whether ops rollout signals can distinguish "backlog draining" from "still
  accumulating state."

Feedback handling:

- accepted findings must be patched into this plan before implementation;
- rejected findings must get a short written reason in the implementation
  handoff or review notes;
- if the reviewer cannot implement the plan confidently, treat that as a
  blocker and revise the plan before coding continues.

## 11. Fresh-Eyes Review

Self-review findings and fixes:

- Finding: The first draft risked deleting parent collation rows immediately
  after child rows were removed. That would lose state needed for terminal
  summary retry and task-control cleanup.
  - Fix: Parent retirement is now a separate conservative step. It requires no
    child rows, raw deletion reconciled, summary emitted, disposition set, and
    task-control cleanup marked complete.
- Finding: The plan initially treated old `deleted_at_ns` rows as a schema
  cleanup detail. In production they are the large backlog.
  - Fix: Added a required bounded tombstone-pruning path and explicit ops
    expectations.
- Finding: The plan could have been misread as permission to drop
  `deleted_at_ns` immediately.
  - Fix: It now says active code must stop using the column, but additive
    migration safety takes precedence. Removing obsolete columns is out of
    scope.
- Finding: The relationship to SimpleBroker could be misapplied by using SQL
  for queue rows because SQL is allowed for Monitor tables.
  - Fix: Added a layer-boundary section and repeated the rule in invariants:
    SQL only for Monitor-owned tables; queues use public SimpleBroker APIs.
- Finding: Parent retirement for nonstandard queues is underspecified.
  - Fix: This plan deliberately uses the conservative standard-control
    completion condition and leaves outbox/inbox/nonstandard broadening out of
    scope.
- Finding: The first tombstone-cleanup wording selected only TIDs, which is
  insufficient for physical deletion and could push an implementer toward a
  broad per-TID delete.
  - Fix: The plan now requires bounded `(tid, message_id)` refs and exact
    message-ID deletion before affected-TID reconciliation.
- Finding: The initial PONG counter names could be confused with raw
  `weft.log.tasks` rows.
  - Fix: Counter names now include `monitor_store_` to make clear they describe
    Monitor-owned SQL table cleanup, not broker queue deletion.
- Finding: The plan did not explicitly address the older table-driven cleanup
  draft that allowed compact disposed Monitor rows to remain as tombstones.
  - Fix: Added that plan to source documents and made this plan supersede that
    steady-state assumption for Monitor table retention.
- Finding: The goal focused on `weft_monitor_task_messages` even though the
  bug report showed `weft_monitor_task_collations` using more disk.
  - Fix: Added parent-table production evidence, explicit parent retirement
    language, and a steady-state growth envelope.
- Finding: The plan did not say when ops SSH is useful, which could lead an
  implementer to debug production manually or apply SQL outside the product
  path.
  - Fix: Added an ops inspection boundary: ops is for read-only validation, not
    manual destructive cleanup.

Residual risk:

- This is still a destructive cleanup path. An external review is required
  before implementation lands. The reviewer should inspect this plan against
  `weft/core/monitor/store.py`, `weft/core/monitor/sql.py`,
  `weft/core/monitor/task_monitor.py`, [MF-5], and [OBS.13]/[OBS.17], then
  answer whether they could implement it confidently and correctly.
