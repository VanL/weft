# Manager Task-Spawned Retention Policy Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], docs/specifications/03-Manager_Architecture.md [MA-1], docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6], docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.3], [OBS.13.7], [OBS.13.10], [OBS.13.12], [OBS.17], docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1]
Superseded by: none

## 1. Goal

Add a narrow TaskMonitor cleanup subphase that trims retained manager-authored
`task_spawned` rows after they have been folded into the Monitor store, while
keeping the newest launch evidence for each manager TID. The policy exists to
bound `weft.log.tasks` and `weft_monitor_task_messages` when a live manager
spawns a large volume of child tasks. It must not close, retire, or mark the
live manager family as raw-deleted.

The implementation target is deliberately small: keep recent manager launch
records, delete older excess manager `task_spawned` raw rows and Monitor child
refs by exact message ID, and report the work under the existing
`task_log.retention` top-level policy. Do not change the manager producer
payload shape in this slice.

## 2. Source Documents

Source specs:

- `docs/specifications/01-Core_Components.md` [CC-2.3]: TaskMonitor is
  manager-supervised, owns operational collation and cleanup, and must keep
  cleanup effects bounded and exact.
- `docs/specifications/03-Manager_Architecture.md` [MA-1]: managers are tasks;
  successful child launch emits durable `task_spawned` evidence with child TID,
  child TaskSpec, and child PID.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: task-log evidence,
  Monitor-store collation, exact deletion, JSONL handoff, cached policy
  progress, and the requirement that cleanup not become lifecycle truth.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-6]: manager spawn
  flow and the two-phase internal service observability model.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.13.3],
  [OBS.13.7], [OBS.13.10], [OBS.13.12], [OBS.17]: Monitor state is derived,
  cleanup is exact and policy-selected, active work is protected, only the
  built-in worker lanes own cleanup effects, and the top-level cleanup policy
  identities stay fixed at five.
- `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1]: use the
  repo-managed environment, backend-aware real broker fixtures, and existing
  task monitor tests.

Guidance:

- `AGENTS.md`: read the design philosophy, constants rule, SimpleBroker
  boundary, and dirty-tree discipline.
- `docs/agent-context/README.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/lessons.md`, especially the lessons on prune path ownership,
  TaskMonitor worker lanes, raw-deleted invariants, manager `task_spawned`
  evidence, and avoiding mock-heavy broker tests.

Related plans:

- `docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`:
  completed table-backed task-log cleanup. This plan extends its table-backed
  model for one live-manager row-retention case.
- `docs/plans/2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md`:
  completed consolidation to exactly five top-level policy identities. This
  plan must preserve that result.
- `docs/plans/2026-05-30-task-monitor-mode-and-rotating-log-plan.md`:
  completed `jsonl_then_delete` mode. This plan must preserve
  durable-before-delete behavior for selected subjects in that mode.
- `docs/plans/2026-05-30-cleanup-progress-fifo-boundary-plan.md`: completed
  cleanup progress boundary fix. This plan must preserve base/waypoint/no-spin
  semantics.
- `docs/plans/2026-06-10-self-healing-runtime-maintenance-plan.md`: completed
  TaskMonitor self-maintenance. This plan must not hide the new work in the
  non-policy maintenance block.

Current production evidence that motivated the plan:

- On `mm-ops` on 2026-06-29, Postgres was CPU-bound while the TaskMonitor and
  manager were handling a very large Weft backlog.
- The dominant Monitor-store message group was one active manager TID with
  roughly 108,766 `task_spawned` rows and only a few manager lifecycle rows.
- Existing cleanup did not select those rows because the manager family was
  open and correctly protected from stale-open closure.
- The rows were not user payloads, child task logs, result rows, task-local
  queues, or manager control rows. They were repeated manager-authored launch
  hints.

## 3. Context And Key Files

Files to modify:

- `docs/plans/README.md`: add this draft plan to the index.
- `docs/specifications/01-Core_Components.md`: add a related-plan backlink
  during this planning change. During implementation, update the TaskMonitor
  implementation mapping only if ownership text changes.
- `docs/specifications/05-Message_Flow_and_State.md`: add a related-plan
  backlink now. During implementation, update [MF-5] and [MF-6] to describe
  manager `task_spawned` row compaction as a private subphase of
  `task_log.retention`.
- `docs/specifications/07-System_Invariants.md`: add a related-plan backlink
  now. During implementation, update [OBS.13.3] and [OBS.13.12] to state that
  manager `task_spawned` row trimming may delete selected child refs without
  marking the open manager family `raw_deleted_at_ns`, and that it still
  reports under `task_log.retention`.
- `weft/_constants.py`: add any internal constants. Do not add a new top-level
  policy name.
- `weft/core/monitor/sql.py`: add the Monitor-store selection query for
  manager `task_spawned` refs.
- `weft/core/monitor/store.py`: expose a small store method for the selection
  query and a small child-ref deletion helper that does not reconcile the
  parent to `raw_deleted_at_ns`.
- `weft/core/monitor/task_monitor.py`: invoke the subphase from the
  table-backed monitor cycle, perform exact broker deletes, hand off JSONL
  reports in `jsonl_then_delete`, update cached progress under
  `task_log.retention`, and preserve TaskMonitor reactor boundaries.
- `weft/core/monitor/lifetime_report.py` only if the existing raw-row report
  builder cannot express the compact per-event report cleanly. Prefer using
  `build_raw_row_lifetime_report()` first.
- `tests/core/test_monitor_sql.py`
- `tests/core/test_monitor_store.py`
- `tests/tasks/test_task_monitor.py`
- `tests/specs/test_plan_metadata.py` only if the metadata/index rule itself
  changes, which this plan should not require.

Files to read first:

- `weft/core/monitor/task_monitor.py`
  - `_run_monitor_store_cycle()`
  - `_ingest_retained_task_log_rows()`
  - `_delete_monitor_store_task_log_rows()`
  - `_repair_raw_deleted_task_message_refs()`
  - `_delete_exact_task_log_rows()`
  - `_handoff_lifetime_report()`
  - `_consolidate_task_monitor_policy_progress()`
- `weft/core/monitor/store.py`
  - `MonitorRawMessageRef`
  - `MonitorTaskCollationRecord.service_classification()`
  - `record_task_log_updates()`
  - `list_deletable_task_log_messages()`
  - `delete_task_messages_after_raw_delete()`
  - `reconcile_raw_deleted_for_tids()`
- `weft/core/monitor/sql.py`
  - `select_deletable_task_log_messages()`
  - `delete_task_messages()`
  - `reconcile_raw_deleted_tasks_for_tids()`
  - identifier and placeholder helpers
- `weft/core/monitor/collation.py`, especially how `event`, `status`, `name`,
  `role`, and TaskSpec summaries are captured from task-log payloads.
- `weft/core/monitor/lifetime_report.py`, especially
  `build_raw_row_lifetime_report()` and `stable_report_id()`.
- `weft/core/manager.py`, only to understand the producer shape around
  `_report_state_change(event="task_spawned", ...)`. Do not modify it in this
  slice.
- `weft/core/monitor/policies/dead_task.py`, specifically
  `_task_log_row_for_message_id_including_claimed()`, because the new report
  path needs exact raw row bodies by message ID and should use the same public
  SimpleBroker `peek_one(exact_timestamp=..., include_claimed=True)` pattern.
- `tests/tasks/test_task_monitor.py`, especially the table-backed delete,
  JSONL handoff, raw-deleted oracle, and manager control cleanup tests around
  existing Monitor-store invariants.

Current structure:

- `TaskMonitor._run_monitor_store_cycle()` is the table-backed cleanup
  orchestrator used by the built-in cycle worker.
- `_ingest_retained_task_log_rows()` folds visible retained task-log rows into
  the Monitor store. In `delete` mode it then deletes valid raw rows. In
  `jsonl_then_delete`, valid raw rows are not deleted until the owning family
  has accepted a lifetime-report handoff.
- `_delete_monitor_store_task_log_rows()` deletes raw rows for families proven
  deletable by Monitor state, then calls
  `store.delete_task_messages_after_raw_delete()`, which deletes child refs
  and reconciles the parent family to `raw_deleted_at_ns` when no child refs
  remain.
- `weft_monitor_task_messages` currently behaves as pending raw-message refs.
  For this plan, manager `task_spawned` trimming is the first explicit
  row-level compaction of an open service family, so it needs a helper that
  deletes selected child refs without implying the manager family is complete.
- Policy progress is consolidated by top-level policy name. Adding another
  `PolicyProgress(policy=TASK_MONITOR_POLICY_TASK_LOG_RETENTION, ...)` record
  is acceptable; adding `manager_task_spawned.retention` is not.

Comprehension checks before editing:

- Why does the existing stale-open service policy intentionally leave the live
  manager family open?
- Which method would incorrectly mark a manager collation `raw_deleted_at_ns`
  if all child refs disappeared?
- Why does deleting the raw broker row alone not solve the production problem?
- Which top-level policy identity must report this work, and why is a sixth
  identity forbidden by [OBS.13.12]?
- What has to happen before deletion in `jsonl_then_delete` mode?

## 4. Proposed Semantics

### 4.1 Scope

The new subphase selects only Monitor-store child refs where all of these are
true:

- The parent collation belongs to the current context.
- The parent collation identifies a manager by Weft-owned fields. Use
  `c.role = 'manager'` and keep `c.name = 'manager'` as compatibility
  evidence for older rows. Do not inspect domain metadata or child TaskSpec
  fields to classify a manager.
- The parent collation has `raw_deleted_at_ns IS NULL`.
- The child ref is from `weft.log.tasks`.
- The child ref event is exactly `task_spawned`.
- The child ref has not already been tombstoned or deleted.
- The row is older than the newest `N` `task_spawned` refs for that same
  manager TID, where `N` is the internal keep-recent default.

Rows that are never selected by this subphase:

- `task_initialized`, `task_started`, `task_spawning`, `manager_superseded`,
  terminal manager events, or any other manager lifecycle event.
- Child task lifecycle rows. Those have their own child TIDs and must remain
  governed by normal task-log retention.
- Any rows for non-manager service owners, heartbeat, TaskMonitor, autostarts,
  or user tasks.
- Any task-local queues, `weft.spawn.requests`, `weft.spawn.internal`,
  `weft.state.*`, `weft.manager.*`, or custom queues.
- Any row selected only by body substring, child TID, child TaskSpec, or
  domain-specific payload shape.

### 4.2 Recency Definition

Use a count-based recency rule: keep the newest manager `task_spawned` refs per
manager TID and trim older excess refs. The first implementation should use an
internal constant in `weft/_constants.py`, for example:

```python
TASK_MONITOR_MANAGER_TASK_SPAWNED_KEEP_RECENT_DEFAULT: Final[int] = 1000
"""Newest manager task_spawned refs retained per manager TID."""
```

Do not add a public environment variable in the first slice. If production
later proves that operators need to tune this without a release, add a
separate config/spec change. A public config knob is easy to add later; a
badly named or under-tested cleanup knob is hard to remove.

Do not gate this subphase on `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`. That
clock protects ordinary lifecycle retention and external lifetime output, but
it would fail the incident case when an active manager emits tens of thousands
of launch hints inside the retention window. The safety gate here is the
per-manager newest-N retention plus the fact that child task evidence remains
owned by the child task rows and TID mappings.

If this feels too aggressive during implementation, stop and re-evaluate
instead of adding an age gate silently. An age gate makes the plan materially
different because it no longer guarantees that a current producer storm can be
bounded.

### 4.3 Exact Delete And Monitor-Store Ref Cleanup

The raw broker row and the Monitor child ref must converge together:

1. Select a bounded batch of eligible manager `task_spawned` refs from the
   Monitor store.
2. In `jsonl_then_delete`, fetch the raw broker row body by exact message ID
   and hand off a compact lifetime report before attempting deletion.
3. Delete the exact broker rows through `apply_exact_prune_candidates()`.
4. Treat a row as reconciled when exact delete reports `deleted=True`, or when
   `reconcile_missing=True` proves the row is already absent without error.
5. Delete only those reconciled child refs from `weft_monitor_task_messages`.
6. Do not call `reconcile_raw_deleted_for_tids()` from this child-ref deletion
   helper. The manager parent family is still open.

Add a helper in `MonitorStore` for step 5. Suggested names:

- `delete_task_messages_after_event_trim(...)`
- or `delete_task_messages_without_raw_deleted_reconcile(...)`

The name must make the missing parent reconciliation explicit. Do not overload
`delete_task_messages_after_raw_delete()` with a boolean flag such as
`reconcile_parent=False`; flags on destructive cleanup helpers invite misuse.

The helper should return `MonitorStoreRetirementResult` or a tiny sibling
result type with at least:

- `message_rows_deleted`
- `affected_tids`

It must be idempotent. Passing already-deleted child ref IDs should delete
zero rows without raising.

### 4.4 JSONL Handoff In `jsonl_then_delete`

In `jsonl_then_delete`, each selected manager event whose raw row still exists
needs a durable handoff before deletion. Use
`build_raw_row_lifetime_report()` unless it cannot express the needed shape.

Required report properties:

- `source_policy`: `task_log.retention`
- `report_kind`: `manager_task_spawned_retained_event`
- `completeness`: `raw_row`
- `close_reason`: `manager_task_spawned_retention`
- `subject`: raw row queue and message ID, with manager TID if available
- `taskspec`: the manager TaskSpec if the existing raw-row builder extracts it
  from the row; do not add `child_taskspec` to the report observations.
- `observations`: include compact values only, such as
  `event="task_spawned"`, `manager_tid`, `child_tid` if present,
  `payload_size_bytes`, and `retained_newest_per_manager_tid`.

Do not emit one full manager-family lifetime report per trimmed row. That
would create misleading duplicate manager lifetime records and would preserve
too much of the producer bloat in a different file.

If a selected raw row is already absent before report handoff, there is no raw
body left to report. In that case, delete the Monitor child ref and count it as
`already_missing`. Do not block forever trying to report evidence that is no
longer present.

If report handoff fails for a row that still exists, leave that raw row and
its Monitor child ref intact, report a blocked progress row, and retry on a
later cycle.

### 4.5 Progress And Catch-Up

Report the work under `TASK_MONITOR_POLICY_TASK_LOG_RETENTION`. Do not add a
new top-level policy constant.

The new progress row should use:

- `policy=TASK_MONITOR_POLICY_TASK_LOG_RETENTION`
- `domain=WEFT_GLOBAL_LOG_QUEUE`
- `scanned`: number of manager `task_spawned` refs considered by the
  selection query, or the bounded selected-plus-extra count if exact source
  total is not cheap.
- `selected`: selected refs in this batch.
- `applied`: child refs removed from `weft_monitor_task_messages`.
- `waypoint_reached=True` when the query observed more than the batch limit.
- `base_reached=True` when no refs are eligible and no error occurred.
- `blocked_reason`: first report/delete/store error, if any.
- `reason_counts`: include
  `manager_task_spawned_retained_event`,
  `manager_task_spawned_already_missing`,
  `manager_task_spawned_reported`,
  `manager_task_spawned_raw_deleted`, and
  `manager_task_spawned_refs_deleted` as applicable.

The existing progress consolidation should fold this with the normal retained
ingest progress under `task_log.retention`. Tests must assert the final cached
PONG policy set remains a subset of `TASK_MONITOR_CLEANUP_POLICY_NAMES`.

### 4.6 Cycle Placement

Run the subphase from `_run_monitor_store_cycle()` when all of these are true:

- `runtime_cleanup_requested` is true.
- The Monitor store is available.
- `task_log_owner == "collated_store"`.
- The monitor is in `delete` or `jsonl_then_delete` mode.

Place it after `_ingest_retained_task_log_rows()` and before the
`completed_fifo_high_water` gate. It must not depend on high-water completion,
because the production case is a live open manager family that may never be
summary-ready.

Do not run it in `report_only` or `custom` modes. `report_only` must remain
non-destructive. `custom` processors do not own built-in destructive cleanup.

## 5. Invariants And Constraints

- Preserve the five top-level cleanup policy identities from [OBS.13.12].
- Do not mark the active manager collation `raw_deleted_at_ns`.
- Do not dispose, summarize, retire, or stale-close the active manager family
  merely because old `task_spawned` rows were trimmed.
- Do not delete manager control queues, manager outbox, spawn queues, task
  inboxes, task outboxes, task reserved queues, or runtime-state queues.
- Do not delete child task lifecycle rows. The child task owns its own TID and
  lifecycle evidence.
- Do not read or delete rows by broad body substring. Exact message IDs from
  Monitor store selection are the deletion authority.
- Do not use private SimpleBroker SQL to delete queue rows. Use the canonical
  exact-delete path.
- Do not add a new public CLI command, status field, or cleanup mode.
- Do not change `weft/core/manager.py` or shrink the `task_spawned` payload in
  this slice. That producer reduction is valid follow-up work, but mixing it
  with destructive cleanup makes both changes harder to test.
- Do not add a general event-compaction framework. This is one manager event
  with one incident-backed policy.
- Do not mock SimpleBroker queues or Monitor-store deletion in integration
  tests. Use `broker_env`, real queues, and a real `TaskMonitor`.
- Keep all production constants in `weft/_constants.py`.
- Keep SQL in `weft/core/monitor/sql.py`; use trusted identifier helpers and
  parameters for runtime values.
- Keep `TaskMonitor` reactor responsiveness intact. The built-in cycle worker
  may do this work; PONG/STATUS must use cached progress and must not scan the
  store live.

Stop and re-plan if any of these happens:

- The implementation wants to add a sixth top-level cleanup policy.
- The implementation needs to close, summarize, or mark `raw_deleted_at_ns` on
  a live manager to make tests pass.
- The implementation starts deleting any event other than manager
  `task_spawned`.
- The implementation cannot preserve `jsonl_then_delete` handoff before
  deleting a present raw row.
- The SQL requires backend-specific syntax that is not covered by both SQLite
  and Postgres tests.
- The plan starts turning into a producer-payload optimization, a generic
  event compaction system, or an ops-only one-off script.

## 6. Tasks

### 1. Add The Red Store Selection Tests

Outcome: prove the store can identify only old excess manager `task_spawned`
refs while keeping the newest refs per manager TID.

Files:

- Modify `tests/core/test_monitor_store.py`.
- Read `tests/core/test_monitor_store.py` helpers before adding new ones.

Test design:

- Use a real `MonitorStore` from `open_monitor_store()`.
- Insert Monitor updates with `store.record_task_log_updates()`, not raw SQL,
  unless the test is only validating SQL shape in `tests/core/test_monitor_sql.py`.
- Build a manager TaskSpec summary with `name="manager"` and
  `metadata={"role": "manager"}`.
- Create at least five manager `task_spawned` updates for one manager TID.
- Create manager lifecycle updates such as `task_started`.
- Create non-manager `task_spawned` rows and child task rows.
- Call the new store selection method with `keep_recent=2` and `limit=10`.
- Assert only the oldest three manager `task_spawned` refs are returned.
- Assert the newest two manager `task_spawned` refs are not returned.
- Assert manager lifecycle rows and non-manager rows are not returned.
- Add a multi-manager case: two manager TIDs each keep their newest two refs
  independently.

Red-green rule:

- Write the failing tests first. They should fail because the store selection
  method does not exist.

Do not:

- Mock `MonitorStore`.
- Build selection by body substring.
- Use ad hoc SQL in the test as the oracle.

Done signal:

- The new tests fail before implementation and pass after tasks 2 and 3.

### 2. Implement The Monitor SQL Selection

Outcome: add a backend-neutral query that returns eligible manager
`task_spawned` refs in deterministic order.

Files:

- Modify `weft/core/monitor/sql.py`.
- Modify `tests/core/test_monitor_sql.py` if there are existing SQL-builder
  tests for query text or parameterization.

Implementation details:

- Add a builder named like
  `select_manager_task_spawned_retention_refs(messages_table, collations_table)`.
- Use existing `identifier()` and `placeholders()` helpers for dynamic SQL.
- Use a window function to rank refs by newest message ID per
  `(context_key, tid)`.
- Predicate:
  - `m.context_key = ?`
  - `m.queue_name = ?`
  - `m.event = 'task_spawned'`
  - `m.deleted_at_ns IS NULL`
  - `c.raw_deleted_at_ns IS NULL`
  - `(c.role = 'manager' OR c.name = 'manager')`
- Keep newest refs by selecting `newest_rank > ?`.
- Order returned refs by `m.message_id ASC`.
- Limit by `?`.

Suggested SQL shape:

```sql
SELECT queue_name, message_id, tid
FROM (
  SELECT
    m.queue_name,
    m.message_id,
    m.tid,
    ROW_NUMBER() OVER (
      PARTITION BY m.context_key, m.tid
      ORDER BY m.message_id DESC
    ) AS newest_rank
  FROM ...
  WHERE ...
) ranked
WHERE newest_rank > ?
ORDER BY message_id
LIMIT ?
```

The query may include `scanned` or `has_more` only if the existing progress
pattern needs it. A simpler `limit + 1` caller pattern is preferred:
TaskMonitor asks for `batch_size + 1`, trims to `batch_size`, and treats the
extra row as waypoint evidence.

Stop and re-evaluate:

- If SQLite in CI does not support the window function, do not replace it with
  a broad Python full-table scan without measuring the production-shaped case.
  Prefer a backend-neutral query rewrite and add explicit tests.

Done signal:

- SQL builder tests pass, and task 1 store tests still fail only because the
  store wrapper is missing.

### 3. Add Store Wrapper And Row-Trim Deletion Helper

Outcome: expose the selection and child-ref deletion behavior without
reconciling the manager parent to `raw_deleted_at_ns`.

Files:

- Modify `weft/core/monitor/store.py`.
- Read the access-layer pattern around
  `list_deletable_task_log_messages()`,
  `task_message_refs_for_message_ids()`, and
  `delete_task_messages_after_raw_delete()` first.

Implementation details:

- Add an access-layer method that calls the new SQL builder and returns
  `tuple[MonitorRawMessageRef, ...]`.
- Add a public `MonitorStore` method named like
  `list_manager_task_spawned_retention_refs(...)` with parameters:
  - `limit: int`
  - `keep_recent: int`
- Validate `limit > 0` and `keep_recent > 0`; return `()` for non-positive
  limit and raise `ValueError` for non-positive keep-recent.
- Add a store helper named like
  `delete_task_messages_after_event_trim(message_ids, *, deleted_at_ns=None)`.
- The helper should:
  - chunk using `self._config.write_batch_size`;
  - find exact child refs for the chunk;
  - delete those exact child refs;
  - count affected TIDs;
  - not call `reconcile_raw_deleted_for_tids()`;
  - return a result with `message_rows_deleted` and `affected_tids`.

Tests:

- Extend `tests/core/test_monitor_store.py` to assert that after deleting
  selected manager `task_spawned` child refs:
  - old selected refs disappear from `weft_monitor_task_messages`;
  - unselected newest refs remain;
  - manager lifecycle refs remain;
  - `store.get_task(manager_tid).raw_deleted_at_ns is None`;
  - calling the helper a second time is idempotent.

Do not:

- Add a flag to `delete_task_messages_after_raw_delete()`.
- Physically delete the parent collation row.
- Mark `deleted_at_ns` tombstones for this path unless an existing store
  convention requires tombstones. Current physical child-ref deletion is enough
  and matches the raw-delete reconciliation path.

Done signal:

- Store tests pass without TaskMonitor wiring.

### 4. Add Exact Raw Row Fetching For Reports

Outcome: TaskMonitor can build compact raw-row reports for selected refs in
`jsonl_then_delete`.

Files:

- Prefer modifying `weft/core/monitor/task_monitor.py` with a small private
  helper, or move the exact-row helper from
  `weft/core/monitor/policies/dead_task.py` to a shared monitor helper if the
  duplication becomes real.
- Do not move broad dead-task policy code.

Implementation details:

- Add a helper like `_task_log_rows_for_message_refs_including_claimed(ctx,
  refs)` that returns a mapping of `message_id -> QueueWindowRow`.
- Use public SimpleBroker APIs:
  `broker.peek_one(WEFT_GLOBAL_LOG_QUEUE, exact_timestamp=message_id,
  with_timestamps=True, include_claimed=True)`.
- Do not use private SimpleBroker tables.
- Treat missing rows as already absent. They are eligible for child-ref
  deletion without a JSONL report because no new deletion will be attempted.
- Keep the helper bounded to the selected batch. Do not scan all task-log rows
  to recover bodies.

Tests:

- Cover the helper indirectly in the `jsonl_then_delete` integration test in
  task 6. A separate unit test is only needed if the helper has non-trivial
  behavior.

Done signal:

- The helper can fetch exact raw bodies for selected refs and does not scan the
  queue broadly.

### 5. Add The TaskMonitor Trim Subphase

Outcome: built-in destructive TaskMonitor cycles trim old manager
`task_spawned` rows and report progress under `task_log.retention`.

Files:

- Modify `weft/_constants.py`.
- Modify `weft/core/monitor/task_monitor.py`.

Implementation details:

- Add `TASK_MONITOR_MANAGER_TASK_SPAWNED_KEEP_RECENT_DEFAULT` to
  `weft/_constants.py`.
- Import it in `task_monitor.py`.
- Add a private method such as
  `_trim_manager_task_spawned_task_log_rows(store, *, now_ns)`.
- The method should:
  - ask the store for `batch_size + 1` refs;
  - trim to `batch_size`;
  - fetch raw rows for selected refs when `jsonl_then_delete` is active;
  - hand off compact reports for present raw rows before deletion;
  - call `apply_exact_prune_candidates()` with `MonitorRawMessageRef`
    candidates and `reconcile_missing=True`;
  - pass reconciled IDs to `store.delete_task_messages_after_event_trim()`;
  - update `_last_collation_store_error` on errors;
  - append a `PolicyProgress` row with
    `policy=TASK_MONITOR_POLICY_TASK_LOG_RETENTION`;
  - return a `MonitorStoreRetirementResult` or small result object so
    `_last_monitor_store_message_rows_deleted` can be updated.
- Call this method from `_run_monitor_store_cycle()` after
  `_ingest_retained_task_log_rows()` and before the
  `completed_fifo_high_water` block, only when `runtime_cleanup_requested`.

Red-green integration test:

- Add `test_task_monitor_trims_manager_task_spawned_rows_without_closing_manager_family`
  in `tests/tasks/test_task_monitor.py`.
- Use `broker_env` and a real `TaskMonitor`.
- Seed a real manager collation and raw `weft.log.tasks` rows:
  - manager start/lifecycle rows;
  - five manager `task_spawned` rows with raw broker message IDs;
  - one child task row for a child TID.
- Use a small keep-recent value by monkeypatching the TaskMonitor module
  constant or by testing the private method with a test-only argument if the
  implementation chooses that shape. Prefer monkeypatching a module constant
  over adding public config.
- For the delete-mode proof, call the new trim method directly after pre-seeding
  the store and raw queue. Do not drive a full `delete`-mode
  `_run_monitor_store_cycle()` for this assertion, because the existing normal
  retained-ingest path may delete valid raw rows first and mask whether the new
  manager trim path works.
- For the full-cycle production proof, use `jsonl_then_delete` in task 6. In
  that mode normal ingest retains valid raw rows until report handoff, so a
  real `_run_monitor_store_cycle(now_ns=..., task_log_owner="collated_store",
  start_control_cleanup=False)` can prove the new trim path in its intended
  incident shape.
- Assert:
  - old manager `task_spawned` raw rows are gone from `weft.log.tasks`;
  - newest manager `task_spawned` raw rows remain;
  - manager lifecycle raw rows remain;
  - child task raw rows remain;
  - old manager child refs are gone from `weft_monitor_task_messages`;
  - newest manager child refs remain;
  - `record.raw_deleted_at_ns is None`;
  - `record.disposition_at_ns is None`;
  - no manager control queue rows were touched;
  - cached policy progress contains only existing top-level policy names and
    includes the manager event reason counts under `task_log.retention`.

Do not:

- Assert only counters. The test must inspect real broker rows and real
  Monitor-store child refs.
- Mock `apply_exact_prune_candidates()`. This is an exact-delete proof.

Done signal:

- The integration test fails before the method exists and passes after the
  method is wired.

### 6. Preserve `jsonl_then_delete`

Outcome: production mode deletes present manager event rows only after durable
report handoff.

Files:

- Modify `weft/core/monitor/task_monitor.py`.
- Modify `tests/tasks/test_task_monitor.py`.

Test design:

- Add `test_task_monitor_jsonl_then_delete_reports_manager_task_spawned_before_trim`.
- Configure:
  - `WEFT_TASK_MONITOR_MODE=jsonl_then_delete`
  - `WEFT_LOG_TASKS_EXTERNAL_MODE=collated`
  - `WEFT_LOG_TASKS_EXTERNAL_PATH` to a temp file
  - `WEFT_TASK_MONITOR_LOG_SINK=none`
- Seed manager `task_spawned` rows as in task 5.
- Run the trim path.
- Assert:
  - selected old raw rows are deleted only after report handoff;
  - the external JSONL file or deferred store contains records with
    `record_type="task_lifetime_report"`;
  - each relevant report has
    `source_policy="task_log.retention"`,
    `report_kind="manager_task_spawned_retained_event"`, and
    `completeness="raw_row"`;
  - report observations contain compact manager/child IDs and payload size, not
    the full `child_taskspec`;
  - `record.raw_deleted_at_ns is None` for the manager parent.

Add a blocked-handoff test:

- Make deferred writes fail by monkeypatching the same narrow boundary used by
  existing JSONL tests.
- Assert present raw rows remain, Monitor child refs remain, and progress has a
  blocked reason.

Already-missing test:

- Remove one selected raw broker row before the trim path runs while leaving
  its Monitor child ref.
- Assert the child ref is deleted, no report is required for that missing raw
  row, and the manager parent remains open.

Done signal:

- Existing `jsonl_then_delete` tests still pass.
- New tests prove the manager-event path does not bypass durable-before-delete
  for present rows.

### 7. Update Specs And Documentation

Outcome: the normative docs describe the new behavior and link back to this
plan.

Files:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/README.md`

Required spec edits during implementation:

- In [MF-5], add a paragraph to the Monitor-store collation/deletion section:
  manager `task_spawned` row trimming is a private subphase of
  `task_log.retention`; it may delete exact older manager launch-event raw rows
  and Monitor child refs while keeping the manager family open.
- In [MF-6], clarify that `task_spawned` rows are durable launch hints while
  retained, but high-volume historical launch hints may be compacted after
  newer launch hints and child task evidence exist.
- In [OBS.13.3], clarify that the normal child-ref deletion helper reconciles
  parent raw deletion, but the manager event-trim helper intentionally does not
  because it is row-level compaction of an open manager service family.
- In [OBS.13.12], keep the five policy names unchanged and state that this
  subphase reports through `task_log.retention` reason counts.
- Add this plan to each touched spec's `Related Plans`.

Do not:

- Change CLI docs unless a public config knob is added. This plan says not to
  add one in the first slice.
- Mark this plan `completed` until implementation and tests land.

Done signal:

- `tests/specs/test_plan_metadata.py` passes.
- Spec text, plan text, and code ownership agree.

### 8. Verification Gates

Run from `/Users/van/Developer/weft` after sourcing the repo environment:

```bash
set -a; . ./.envrc; set +a
./.venv/bin/python -m pytest tests/core/test_monitor_sql.py tests/core/test_monitor_store.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -k "manager_task_spawned or jsonl_then_delete_reports_manager_task_spawned or raw_deleted" -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/ruff check weft tests/core/test_monitor_sql.py tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

Because this touches table-backed cleanup and SQL, also run the Postgres gate
before release:

```bash
set -a; . ./.envrc; set +a
bin/pytest-pg --all tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py -k "manager_task_spawned or jsonl_then_delete_reports_manager_task_spawned"
```

If the targeted Postgres command shape is not accepted by `bin/pytest-pg`,
run the documented backend-sensitive gate:

```bash
set -a; . ./.envrc; set +a
bin/pytest-pg --all
```

Release gate:

- Do not release with shortcuts that skip tests.
- Run the normal project release/test gate used by the current Weft workflow
  before deploying.

Production observation after deploy:

- `weft_monitor_task_messages` rows for the active manager TID should fall
  toward roughly `keep_recent + lifecycle rows`, not keep growing unbounded.
- `weft.log.tasks` old manager `task_spawned` raw rows should fall in the same
  bounded cycles.
- `policy_progress` should still show only the five top-level policy names.
- `task_log.retention` reason counts should include manager task-spawned trim
  counts while backlog exists.
- Active manager PING/status and child task execution should remain normal.
- Manager `raw_deleted_at_ns` should remain unset while the manager is live.

## 7. Rollback

Code rollback:

- Revert the TaskMonitor trim invocation, store methods, SQL builder, constant,
  tests, and spec edits.
- Existing Monitor-store rows remain valid because this plan adds no schema
  migration and no new table.

Runtime rollback:

- If deletion behavior is suspect after deploy, set
  `WEFT_TASK_MONITOR_MODE=report_only` or disable TaskMonitor while rolling
  back. That stops destructive cleanup but also stops this backlog reducer.
- If only `jsonl_then_delete` report handoff is blocked, fix the external log
  path or deferred-write failure first. Do not switch to `delete` mode in
  production without accepting the operational audit tradeoff.

Data compatibility:

- Old code can run after rollback. It will see fewer manager `task_spawned`
  child refs and raw rows, but the manager parent collation, newest launch
  hints, manager lifecycle refs, child task logs, and TID mappings remain.
- Since no schema changes are introduced, rollback does not require migration.

## 8. Out Of Scope

- Reducing the manager producer payload size. That should be a separate plan
  focused on `weft/core/manager.py` and the consumers of `child_taskspec`.
- Adding public configuration for manager launch-event keep-recent.
- Adding a generic event compaction framework.
- Closing or summarizing active manager families.
- Extending stale-open service-owner disposition.
- Adding an ops-only SQL cleanup script.
- Changing SimpleBroker queue APIs.
- Changing public CLI output or PONG schema beyond existing policy progress
  reason counts.

## 9. Review Plan

Self-review is mandatory and recorded below. External review is also required
before implementation because this plan changes destructive cleanup behavior,
uses Monitor-store SQL, and could be misimplemented while still looking
reasonable.

Recommended external review prompt:

```text
Read docs/plans/2026-06-29-manager-task-spawned-retention-policy-plan.md.
Also read docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6],
docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.12], [OBS.17],
and the intended files in weft/core/monitor/task_monitor.py,
weft/core/monitor/store.py, and weft/core/monitor/sql.py.

Do not implement. Look for errors, bad ideas, unsafe cleanup behavior, missed
tests, and latent ambiguities. Could a zero-context engineer implement this
correctly from the plan?
```

Implementation must not start until either external review is complete or the
owner explicitly accepts the risk of proceeding without it.

## 10. Fresh-Eyes Self-Review

Self-review pass 1 findings and fixes:

- Finding: the first design instinct was to add a new top-level policy such as
  `task_log.service_event_retention`. That violates [OBS.13.12]. Fix: this
  plan now makes the work a private subphase reported under
  `task_log.retention`.
- Finding: using `delete_task_messages_after_raw_delete()` would eventually
  mark a manager parent `raw_deleted_at_ns` if all child refs disappeared.
  Fix: the plan now requires a separate child-ref deletion helper that does not
  reconcile parent raw deletion, and tests must assert the manager parent stays
  open.
- Finding: gating on the normal 48-hour task-log retention period would not
  address an active producer storm. Fix: recency is now count-based, with an
  explicit stop-and-replan gate if someone wants to add an age gate.
- Finding: `jsonl_then_delete` could be accidentally bypassed because the
  manager family is never summary-ready. Fix: the plan now requires a compact
  per-event raw-row lifetime report before deleting present raw rows in
  `jsonl_then_delete`.
- Finding: tests could over-mock the exact-delete path. Fix: the plan now
  requires real broker and real Monitor-store integration tests and calls out
  what must be asserted in queue and store state.

Self-review pass 2 findings and fixes:

- Finding: the plan needed to say how to fetch exact raw bodies for JSONL
  reports without broad scans. Fix: task 4 now requires the public
  `peek_one(exact_timestamp=..., include_claimed=True)` pattern.
- Finding: the plan did not initially say what to do for already-missing raw
  rows. Fix: the JSONL and exact-delete sections now treat missing rows as
  already absent and allow child-ref cleanup without an impossible report.
- Finding: the test plan did not explicitly protect child task lifecycle rows.
  Fix: task 5 now requires a child task row in the integration fixture and an
  assertion that it remains.
- Finding: a public keep-recent env knob would make tests easy but add an
  unnecessary contract. Fix: the plan keeps the default as an internal constant
  and tells tests to monkeypatch the module constant or use store-level
  parameters.

Self-review pass 3 findings and fixes:

- Finding: the delete-mode integration guidance could accidentally drive the
  existing retained-ingest delete path instead of the new manager-event trim
  path. Fix: task 5 now requires a direct preseeded trim-method proof for
  delete mode and leaves the full-cycle proof to task 6 in `jsonl_then_delete`,
  which matches the production incident mode.

Residual risk:

- The SQL window-function query must be proven on both supported backends. If
  backend parity fails, the implementation must re-plan the selection strategy
  rather than weakening the policy into an unbounded Python scan.
- Count-only recency can delete launch hints that are young during extreme
  bursts. The retained newest-N launch hints, manager lifecycle refs, child
  task rows, and TID mappings are the intended safety boundary. If review
  rejects that boundary, the plan should return for an explicit product
  decision instead of drifting into an age-gated policy.
