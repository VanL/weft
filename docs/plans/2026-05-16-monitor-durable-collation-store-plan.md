# Monitor Durable Collation Store Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
Superseded by: none

## 1. Goal

Add a Monitor-owned durable collation store so `TaskMonitor` can build
task lifecycle summaries incrementally from `weft.log.tasks`, survive monitor
restarts, emit information-rich operational records, and delete exact raw
runtime-evidence rows only after durable collation has made that deletion
safe. The store is a derived operational read model owned by the Monitor. It
must never become task lifecycle truth, result authority, queue truth, or a
replacement for SimpleBroker queue semantics.

This plan is intentionally narrower than a general database layer. The Monitor
may create, verify, and migrate only its own Monitor tables inside an already
configured and initialized Weft broker database. The Monitor does not create
the Weft database, discover a new database target, provision Postgres, or own
SimpleBroker setup. Database setup remains `weft init`, context resolution, or
the existing task/manager bootstrap contract.

## 2. Source Documents

- `docs/specifications/00-Quick_Reference.md`: user-visible
  `WEFT_TASK_MONITOR_*` configuration. Update this if the implementation adds
  store toggles, schema version diagnostics, or changes the meaning of existing
  monitor cleanup knobs.
- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]: task
  ownership and monitoring ownership. The Monitor store must be operational
  metadata owned by `TaskMonitor`, not public lifecycle state.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]: backend
  neutral context resolution. The Monitor store must use the resolved
  `WeftContext` / `BrokerTarget`; it must not parse database URLs or special
  case file paths outside a small store adapter.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: `weft.log.tasks`
  as runtime lifecycle evidence, exact-message deletion, TaskMonitor cleanup,
  and cached PONG diagnostics.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]:
  operational evidence, exact cleanup candidates, and one-way-door cleanup
  protections.
- `docs/plans/2026-05-12-bounded-task-monitor-cleanup-policy-plan.md`:
  completed cleanup policy work. This plan keeps the explicit-policy and
  exact-delete requirements, but changes the durable collation mechanism.
- `docs/plans/2026-05-12-task-monitor-cleanup-composition-refactor-plan.md`:
  completed runner/policy/action split. Preserve that split.
- `docs/plans/2026-05-15-swappable-task-log-family-scanner-plan.md`: active
  family-scanner work. This plan supersedes the need to keep all collation
  state in memory, but it should reuse the scanner as the first backfill/input
  source.
- `docs/agent-context/runbooks/writing-plans.md`: zero-context plan standard.
- `docs/agent-context/runbooks/hardening-plans.md`: required because this
  introduces new persistence and destructive cleanup sequencing.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  because this crosses persistence, cleanup, task monitoring, and deployment
  observability.

Existing specs currently describe bounded queue-window and family-scanner
cleanup. This plan is an intended spec delta. Before implementation is called
done, the specs above must say that table-backed Monitor collation is a
derived operational facility and that raw task-log deletion may use durable
Monitor collation records as proof.

## 3. Current Context And Key Files

Read first:

- `AGENTS.md`: project philosophy, house style, and the warning that specs are
  normative while plans are execution aids.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: current
  task-log, cleanup, and PONG contracts.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]: context and
  backend-neutral broker-target rules.
- `weft/context.py`: `WeftContext`, `build_context(..., create_database=...)`,
  and `WeftContext.broker()`.
- `weft/core/tasks/base.py`: `_build_task_context()` already calls
  `build_context(..., create_database=False)` for task runtimes. Preserve this
  database-setup boundary.
- `weft/core/tasks/task_monitor.py`: persistent Monitor task, reactor loop,
  heartbeat wakeups, cached PONG diagnostics, and cleanup-cycle invocation.
- `weft/core/monitor/cleanup.py`: current bounded cleanup
  orchestration.
- `weft/core/monitor/task_log_scanner.py`: current generator-backed family scanner.
- `weft/core/monitor/task_log_collation.py`: current task-log lifecycle grouping
  helpers, if present in the working tree.
- `weft/core/pruning/`: exact-delete helper and reusable row policies.
- `tests/helpers/weft_harness.py`: integration-style task and manager harness.

Files to modify:

- `docs/specifications/00-Quick_Reference.md`
  - Add or update Monitor store configuration only when the implementation
    actually introduces config.
- `docs/specifications/01-Core_Components.md`
  - Add the Monitor store ownership boundary and implementation mapping.
- `docs/specifications/04-SimpleBroker_Integration.md`
  - Add the rule that Weft-owned non-queue operational tables must use the
    resolved broker target and must not bypass context resolution.
- `docs/specifications/05-Message_Flow_and_State.md`
  - Define table-backed collation, checkpointing, summary emission, and
    exact raw-row deletion semantics.
- `docs/specifications/07-System_Invariants.md`
  - Add invariants for derived Monitor tables, PONG diagnostics, and deletion
    proof.
- `weft/_constants.py`
  - Add all production constants and environment variable defaults here.
    Do not put defaults in the store, reducer, or task modules.
- `weft/core/monitor/store.py` (new)
  - Own the Monitor table schema, idempotent schema creation, backend-specific
    SQL differences, checkpoint reads/writes, collation upserts, and exact
    message-id bookkeeping.
  - Start as one cohesive module. Split into a package only if the code
    becomes clearly too large after SQLite and Postgres paths are implemented.
- `weft/core/monitor/collation.py` (new)
  - Own pure event-to-collation reduction. This file must not open queues,
    delete broker rows, emit logs, or create tables.
- `weft/core/tasks/task_monitor.py`
  - Initialize and cache Monitor store state, drive collation cycles, update
    cached PONG diagnostics, and keep task reaction on the main reactor path.
- `weft/core/monitor/cleanup.py`
  - Integrate table-backed proof into cleanup without duplicating deletion
    code. Keep bounded policy cleanup as fallback and as the first backfill
    input source.
- `weft/core/pruning/`
  - Reuse exact-delete helpers. Add only tiny adapters if Monitor table rows
    need to call the existing exact-delete path.
- `tests/core/test_monitor_store.py` (new)
  - Store schema and idempotency tests.
- `tests/core/test_monitor_collation.py` (new)
  - Pure reducer tests.
- `tests/tasks/test_task_monitor.py`
  - Persistent Monitor startup, PONG, degradation, and cycle behavior.
- `tests/core/test_task_monitor_cleanup.py`
  - Cleanup integration and exact-delete sequencing.
- `tests/system/test_constants.py`
  - Update if new constants or env vars are added.

Files not to modify unless a test proves it is required:

- `weft/core/manager.py`
  - The manager supervises the Monitor as a child service. It must not create
    Monitor tables or read Monitor collation rows.
- `weft/commands/result.py` and `weft/commands/status.py`
  - Status/result reconstruction must not depend on the Monitor store in this
    slice. They may continue to use retained runtime evidence and existing
    reconstruction paths.
- SimpleBroker internals
  - Do not reach into private SimpleBroker modules. Use public broker/context
    APIs where possible. If public APIs are insufficient, isolate the backend
    SQL in `weft/core/monitor/store.py` and leave a clear comment explaining
    the public API gap.

## 4. Invariants And Constraints

- `weft.log.tasks` remains the runtime lifecycle evidence stream. The Monitor
  store is derived from it.
- The Monitor store is operational state. It is not legal, forensic, audit, or
  result evidence.
- Public status and result behavior must not depend on the Monitor store.
- Raw task-log row deletion must use exact message IDs only.
- A raw task-log row must not be deleted because a task "looks old" if the
  deletion proof is ambiguous.
- Successful terminal task lifecycles do not need reserved-queue probes. The
  reserved queue matters for failure-like evidence or suspected abrupt loss,
  not for a completed task that has terminal lifecycle proof.
- The Monitor may create, verify, and migrate only its own Monitor tables.
  It must not create or initialize the broker database itself. Task runtime
  context construction must keep `create_database=False`.
- The store must work with SQLite and Postgres. Backend differences belong in
  the store module, not in `TaskMonitor`.
- The store must be idempotent under replay. Reprocessing the same task-log
  message must not duplicate message IDs or regress a terminal summary.
- PONG must stay lightweight. It may report cached store/collation diagnostics
  from the last cycle; it must not query the store or scan queues while
  answering PING.
- The first production rollout must be non-destructive or easy to disable.
  Raw deletion from table-backed proofs is the one-way-door part and needs a
  separate gate.
- Do not add a generic ORM, migration framework, or general Weft database
  abstraction. This is a Monitor-owned store with a narrow contract.

Stop and re-evaluate if any of these happen:

- implementation starts parsing Postgres URLs or SQLite paths outside
  `weft/context.py` or `weft/core/monitor/store.py`
- status/result code wants to read Monitor collation rows
- raw message IDs are stored only as an unbounded JSON array instead of a
  normalized child table
- tests mock SimpleBroker queues for behavior that can be tested with real
  SQLite or Postgres brokers
- a broad "cleanup service" abstraction appears before the Monitor table path
  is working
- a deletion path cannot name the exact policy, proof record, and message IDs
  it is deleting

## 5. Proposed Architecture

The Monitor gets a small durable read model:

```text
weft.log.tasks
  -> generator/family scanner
  -> monitor collation reducer
  -> Monitor-owned DB tables
  -> optional operational summary sink
  -> exact raw-row delete through existing pruning helper
```

The table is not a queue. It should not be exposed through `weft queue *`, and
it should not use queue names. It lives beside SimpleBroker tables because
Weft owns the database, but it is owned by `TaskMonitor`, not by
SimpleBroker.

The initial table set should be:

- `weft_monitor_meta`
  - Stores schema version, per-queue checkpoints, and small store-level
    operational values.
- `weft_monitor_task_collations`
  - One row per context and TID. Stores the latest durable task lifecycle
    summary and Monitor bookkeeping.
- `weft_monitor_task_messages`
  - One row per raw task-log message that has been incorporated into a
    collation record. This supports exact raw-row deletion without storing an
    unbounded message-ID array in a JSON column.

Use a context key so one database can safely hold more than one Weft context
when a deployment does that. Reuse the existing context identity helper if one
already exists. If none exists, add a small helper near existing context
display/digest code in `weft/context.py` or a private helper in
`weft/core/monitor/store.py`. Do not expose a new public context ID API unless
the specs require it.

Suggested schema, version 1:

```text
weft_monitor_meta
  key text primary key
  value_json text not null
  updated_at_ns bigint not null

weft_monitor_task_collations
  context_key text not null
  tid text not null
  name text null
  runner text null
  parent_tid text null
  role text null
  status text null
  terminal_seen boolean not null default false
  terminal_event text null
  terminal_status text null
  return_code integer null
  first_message_id bigint not null
  last_message_id bigint not null
  first_seen_at_ns bigint null
  last_seen_at_ns bigint null
  started_at_ns bigint null
  completed_at_ns bigint null
  taskspec_summary_json text not null
  state_json text not null
  lifecycle_json text not null
  resources_json text not null
  diagnostics_json text not null
  bookkeeping_json text not null
  reserved_probe_needed boolean not null default false
  summary_emitted_at_ns bigint null
  raw_deleted_at_ns bigint null
  suspect_reason text null
  updated_at_ns bigint not null
  primary key (context_key, tid)

weft_monitor_task_messages
  context_key text not null
  tid text not null
  queue_name text not null
  message_id bigint not null
  event text null
  status text null
  observed_at_ns bigint null
  selected_for_delete_at_ns bigint null
  deleted_at_ns bigint null
  primary key (context_key, tid, message_id)
```

Indexes:

- `(context_key, terminal_seen, raw_deleted_at_ns, completed_at_ns)`
- `(context_key, last_seen_at_ns)`
- `(context_key, reserved_probe_needed, last_seen_at_ns)`
- `weft_monitor_task_messages(context_key, tid)`
- `weft_monitor_task_messages(context_key, deleted_at_ns, message_id)`

The exact column names may change during implementation, but the model must
preserve these ideas: one task summary row, normalized raw message IDs, cached
checkpoint metadata, and explicit deletion/summarization bookkeeping.

## 6. Bite-Sized Tasks

### Task 1: Update The Specs Before Code

Files:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`

Red test first:

- Run `uv run pytest tests/specs/test_plan_metadata.py -q` after adding the
  plan backlink rows. It should pass once the plan index and metadata are
  correct.

Required spec changes:

- Define the Monitor store as a derived operational facility.
- State that the Monitor may create only its own tables in an already
  initialized Weft database.
- State that Monitor table creation is idempotent and versioned.
- State that PONG reports cached table/collation diagnostics only.
- State that successful completed task lifecycles do not require reserved
  queue probes.
- State that table-backed deletion must delete exact raw message IDs only
  after durable collation and optional summary emission have succeeded.
- Add implementation mappings to `weft/core/monitor/store.py`,
  `weft/core/monitor/collation.py`, and `weft/core/tasks/task_monitor.py`.

Stop gate:

- If the spec text starts saying the Monitor store is lifecycle truth, stop.
  That is the wrong direction.

### Task 2: Add Constants And Config

Files:

- `weft/_constants.py`
- `tests/system/test_constants.py`
- `docs/specifications/00-Quick_Reference.md`

Add only the constants needed for the first implementation:

- Monitor table names:
  - `WEFT_MONITOR_META_TABLE`
  - `WEFT_MONITOR_TASK_COLLATIONS_TABLE`
  - `WEFT_MONITOR_TASK_MESSAGES_TABLE`
- schema version:
  - `WEFT_MONITOR_SCHEMA_VERSION`
- feature toggle:
  - `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED`
  - default should be true once tests prove the store is safe, but the code
    must also support disabling it operationally
- rollout gate:
  - `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED`
  - default false for the first production release unless the implementation
    and review explicitly choose otherwise

Do not add knobs for every future policy. If a setting is not needed to ship
the first durable collation store, leave it out.

Tests:

- Add or update system constant tests so production constants live in
  `weft/_constants.py`.
- Add config parsing tests only if new env parsing has non-trivial validation.

### Task 3: Build The Monitor Store Module

Files:

- `weft/core/monitor/store.py`
- `tests/core/test_monitor_store.py`

Required public surface inside the module:

- `MonitorStoreError`
- `MonitorStoreUnavailable`
- `MonitorStoreConfig`
- `MonitorStoreStatus`
- `MonitorTaskEventUpdate`
- `open_monitor_store(context: WeftContext, *, config: Mapping[str, Any])`
- `MonitorStore.ensure_schema()`
- `MonitorStore.get_checkpoint(queue_name: str) -> int | None`
- `MonitorStore.set_checkpoint(queue_name: str, message_id: int) -> None`
- `MonitorStore.upsert_task_event(update: MonitorTaskEventUpdate) -> None`
- `MonitorStore.list_deletable_task_log_messages(limit: int) -> tuple[...]`
- `MonitorStore.mark_messages_deleted(message_ids: Sequence[int]) -> None`
- `MonitorStore.close()`

Keep the surface small. If the implementation wants a generic repository,
generic migration runner, or generic database abstraction, stop and remove it.

Implementation rules:

- Use `WeftContext.broker()` or the resolved `BrokerTarget` and broker config.
- Treat an already spawned `TaskMonitor` as proof that the broker database
  and SimpleBroker runtime schema already exist. The store may open a
  connection to that target to create Monitor tables, but it must not perform
  project discovery, database creation, or non-Monitor schema setup.
- Do not call `build_context(..., create_database=True)` from Monitor code.
- Do not parse DSNs or duplicate context discovery.
- Keep backend-specific SQL in this module only.
- Use idempotent `CREATE TABLE IF NOT EXISTS`.
- Record schema version in `weft_monitor_meta`.
- If an existing schema has a higher version than the code supports, raise
  `MonitorStoreUnavailable` and fail closed.
- If an existing schema has a lower version, apply explicit additive
  migrations only. No destructive migration in this plan.
- Use transactions around a batch of upserts and message bookkeeping.
- Store raw message IDs in `weft_monitor_task_messages`, not in JSON.

Tests:

- SQLite: schema creation is idempotent.
- SQLite: schema version is recorded.
- SQLite: unsupported newer schema version disables the store without deleting
  anything.
- SQLite: checkpoint read/write is idempotent.
- SQLite: upserting the same message twice does not duplicate child message
  rows.
- SQLite: a terminal update does not regress to non-terminal after replaying
  an older non-terminal event.
- Postgres: same tests through `uv run bin/pytest-pg`.

Avoid:

- Mocking SQL runners for the main tests. Use real SQLite and Postgres broker
  contexts.
- Reusing SimpleBroker queue tables as Monitor tables.

### Task 4: Build The Pure Collation Reducer

Files:

- `weft/core/monitor/collation.py`
- `tests/core/test_monitor_collation.py`

Required behavior:

- Accept decoded `weft.log.tasks` rows from the scanner/window path.
- Extract TID, event, status, timestamp, message ID, and any embedded
  `taskspec`.
- Start the summary from the task-log `taskspec` payload when available.
- Preserve a summary of all TaskSpec sections:
  - `tid`, `version`, `name`, `description`
  - `spec` fields that define what was called
  - `io` queue names
  - `state`
  - `metadata`
- Respect existing task-log redaction. Do not rehydrate redacted fields from
  another source.
- Merge mutable state forward.
- Track resource peaks and runtime diagnostics when present.
- Mark terminal state for terminal lifecycle events.
- Set `reserved_probe_needed=False` for successful completed lifecycles.
- Set `reserved_probe_needed=True` only for failure-like terminal events or
  later suspected-dead classifications.
- Return a typed update object that the store can persist.

The reducer must not:

- open a queue
- delete rows
- emit JSONL
- read task-local reserved queues
- call PONG/control code
- mutate a live `TaskSpec`

Tests:

- A `task_initialized -> work_started -> work_completed` sequence produces a
  terminal completed summary with no reserved probe needed.
- A `work_failed` or `work_timeout` sequence sets failure-like terminal state
  and marks `reserved_probe_needed`.
- Replaying the same event is idempotent.
- A later resource peak wins over an earlier lower peak.
- Malformed JSON is rejected before reducer entry or classified as malformed
  by the existing cleanup policy; the reducer should not silently accept it.

### Task 5: Wire Store Initialization Into TaskMonitor

Files:

- `weft/core/tasks/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Required behavior:

- `TaskMonitor` initializes the Monitor store only when the store feature
  is enabled.
- Initialization happens after task context is available and before the first
  table-backed collation cycle.
- Initialization calls only Monitor table schema creation/verification.
- Initialization must not create the broker database. The existing task
  context path already uses `create_database=False`; preserve and test that.
- If store initialization fails, the Monitor records a cached store error in
  PONG and continues with the existing non-table cleanup path if that path is
  configured. It must not silently pretend table-backed deletion is active.
- PONG includes cached fields only:
  - `collation_store_enabled`
  - `collation_store_available`
  - `collation_schema_version`
  - `collation_checkpoint`
  - `last_collation_rows_processed`
  - `last_collation_tasks_updated`
  - `last_collation_terminal_tasks`
  - `last_collation_messages_marked_deleted`
  - `last_collation_store_error`

Tests:

- Persistent Monitor PONG reports store availability after startup.
- PING response does not trigger a store query. Use a tiny test seam or counter
  around the cached status provider, not a broad mock of the store.
- If the store is unavailable, PONG reports unavailable and cleanup remains
  non-destructive.
- TaskMonitor task context construction does not create a database. If a test
  monkeypatches `build_context`, assert `create_database=False`.

### Task 6: Add Bounded Backfill From The Existing Scanner

Files:

- `weft/core/tasks/task_monitor.py`
- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/task_log_scanner.py`
- `weft/core/monitor/store.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_task_monitor_cleanup.py`

Required behavior:

- On each Monitor cycle, when the store is available, read from
  `weft.log.tasks` starting after the stored checkpoint.
- Use the current generator/family scanner as the first input implementation.
- Apply a bounded batch size and scan limit. Do not run an unbounded catch-up
  in the task reactor.
- Persist collation updates and child message IDs in one transaction per batch.
- Advance the checkpoint only after the transaction succeeds.
- If the transaction fails, do not advance the checkpoint.
- The next cycle may replay the same rows, and replay must be safe.

Important distinction:

- The scanner is the input source, not the durable state.
- The store is the durable state.
- The exact-delete helper is the destructive action.

Tests:

- A batch with two complete tasks updates two collation rows and advances the
  checkpoint.
- A simulated store failure leaves the checkpoint unchanged and does not delete
  raw task-log rows.
- Replaying the same batch after a failure does not duplicate message rows.
- A long-open service TID at the head of the log does not prevent completed
  later TIDs from being collated when the scanner can see them.

### Task 7: Add Summary Emission As A Separate Action

Files:

- `weft/core/tasks/task_monitor.py`
- `weft/core/monitor/store.py`
- `weft/core/monitor/collation.py`
- maybe `weft/core/tasks/task_monitor_logging.py` if a new small helper is
  needed
- `tests/tasks/test_task_monitor.py`

Required behavior:

- Summary emission reads terminal collation rows that have not yet been
  summarized.
- The first implementation may emit to the existing `stdout`/disk monitor log
  sink, using the existing `WEFT_TASK_MONITOR_LOG_SINK` semantics.
- Summary emission marks `summary_emitted_at_ns` only after the sink write
  succeeds.
- If the sink fails, do not mark the summary emitted and do not delete raw
  rows for that task.
- The summary payload should include the TaskSpec summary, final state,
  resource summary, diagnostics summary, and message count.
- Keep the summary compact. Do not dump every raw task-log row into the
  summary.

Tests:

- A completed task summary is emitted once.
- Re-running the cycle does not emit the same summary again.
- If the sink fails, no raw rows are deleted.

If implementing summary emission would make the first slice too large, split
here. Ship schema, reducer, backfill, and PONG first with deletion disabled.

### Task 8: Add Table-Backed Exact Raw Deletion

Files:

- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/store.py`
- `weft/core/pruning/`
- `tests/core/test_task_monitor_cleanup.py`
- `tests/tasks/test_task_monitor.py`

Prerequisites:

- Store schema is available.
- Collation rows are terminal.
- Message IDs are present in `weft_monitor_task_messages`.
- Summary emission has succeeded when the configured processor requires it.
- `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED` is enabled.

Required behavior:

- Select deletable raw task-log message IDs from the Monitor store.
- Delete through the existing exact-delete helper.
- Mark message rows deleted only after delete succeeds.
- Mark the task collation `raw_deleted_at_ns` only when all known raw message
  rows for the task are deleted.
- Do not probe `T{tid}.reserved` for completed successful tasks.
- For failure-like tasks, leave `reserved_probe_needed=True` and defer reserved
  queue handling to a later explicit policy unless this slice implements and
  tests that policy.

Tests:

- Completed successful task: summary emitted, raw message IDs deleted, no
  reserved queue probe.
- Failure-like task: raw log rows may be summarized, but reserved probe is
  not performed by the successful-completion path.
- Delete failure leaves Monitor table rows marked not deleted.
- Delete replay is idempotent.

Stop gate:

- If implementation wants to delete broad old task-log rows from the table
  without exact message IDs, stop. That violates the cleanup invariant.

### Task 9: Add Suspected-Dead Classification Later, Not In The First Slice

Files for later:

- `weft/core/monitor/collation.py`
- `weft/core/monitor/store.py`
- `tests/core/test_monitor_collation.py`

Do not implement suspected-dead classification in the first table slice unless
the earlier tasks are complete and reviewed.

Future rule:

- A task with start evidence and no terminal evidence may become suspected
  dead only after a configurable inactivity threshold, probably based on a
  multiple of the task reporting interval.
- Only suspected-dead or failure-like tasks should require reserved queue
  investigation.
- The suspected-dead policy must be explicit in PONG stats and tests.

This is intentionally deferred because it is recovery-sensitive and easy to
misuse as cleanup proof.

### Task 10: Documentation And Operational Runbook

Files:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/lessons.md` if implementation finds a reusable lesson

Document:

- what Monitor tables exist
- how to disable table-backed collation
- how to disable table-backed deletion
- what PONG fields prove
- what PONG fields do not prove
- what success looks like on ops:
  - store available
  - checkpoint advancing
  - rows processed per cycle
  - terminal tasks summarized
  - raw task-log deletions only after table deletion gate is enabled

Do not document Monitor tables as public API.

## 7. Test Design Guidance

Use red-green TDD whenever practical:

- Write store tests before store code.
- Write reducer tests before reducer code.
- Write PONG tests before adding cached fields.
- Write one deletion sequencing test before enabling deletion.

Prefer real broker-backed tests:

- Use SQLite-backed contexts for fast tests.
- Use `uv run bin/pytest-pg` for Postgres coverage.
- Use `WeftTestHarness` only when process lifecycle is part of the contract.
- Do not mock SimpleBroker queues for scan, checkpoint, or delete behavior.

What can be mocked:

- A failing summary sink, because the sink failure is the external boundary.
- A store unavailable error, if the test is specifically about TaskMonitor
  degradation and a real schema-version failure would make the test noisy.

What must not be mocked:

- exact message ID deletion
- queue history reads
- checkpoint advancement after durable write
- task-log event payload shape
- PONG cached output shape

Required test files and intent:

- `tests/core/test_monitor_store.py`
  - idempotent schema
  - schema versioning
  - checkpointing
  - replay-safe upsert
  - raw message ID child rows
  - SQLite and Postgres behavior
- `tests/core/test_monitor_collation.py`
  - pure event reduction
  - TaskSpec summary capture
  - terminal state
  - resource/diagnostics merge
  - reserved-probe flag semantics
- `tests/tasks/test_task_monitor.py`
  - Monitor startup
  - store degraded mode
  - cached PONG diagnostics
  - no database creation by Monitor
- `tests/core/test_task_monitor_cleanup.py`
  - table-backed exact delete
  - no reserved probe for successful completed tasks
  - failure-safe delete sequencing
- `tests/system/test_constants.py`
  - all new constants live in `weft/_constants.py`

## 8. Verification Commands

Run the smallest relevant tests after each task, then expand:

```bash
uv run pytest tests/core/test_monitor_store.py -q
uv run pytest tests/core/test_monitor_collation.py -q
uv run pytest tests/tasks/test_task_monitor.py -q
uv run pytest tests/core/test_task_monitor_cleanup.py -q
uv run pytest tests/system/test_constants.py tests/specs/test_plan_metadata.py -q
```

Run Postgres coverage before claiming the persistence work is complete:

```bash
uv run bin/pytest-pg tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py tests/core/test_task_monitor_cleanup.py -q
```

Run type and lint checks before completion:

```bash
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft tests/core/test_monitor_store.py tests/core/test_monitor_collation.py tests/tasks/test_task_monitor.py tests/core/test_task_monitor_cleanup.py
```

Run broader tests before landing:

```bash
uv run pytest
uv run bin/pytest-pg
```

## 9. Rollout And Rollback

Rollout should be staged:

1. Ship schema, store initialization, reducer, checkpointing, and PONG
   diagnostics with table-backed deletion disabled.
2. Deploy and verify on ops that:
   - Monitor stays alive
   - `collation_store_available=true`
   - checkpoint advances across cycles
   - task rows and message rows increase during backfill
   - PONG does not time out
   - no raw deletion occurs when table deletion is disabled
3. Enable summary emission. Verify summaries are emitted once and
   `summary_emitted_at_ns` advances.
4. Enable table-backed exact raw deletion only after review and ops evidence.
   Verify raw `weft.log.tasks` decreases in bounded batches and deleted
   message rows are marked in the Monitor table.

Rollback:

- Disable table-backed collation with
  `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED=0`.
- Disable table-backed deletion with
  `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED=0`.
- Leave Monitor tables in place. They are derived operational state and do not
  affect task execution when disabled.
- Do not drop tables as part of normal rollback.
- Existing scanner/fallback cleanup remains available.

One-way doors:

- Raw task-log deletion is the one-way door.
- Schema creation is not a meaningful one-way door if it is additive and inert
  when disabled.
- Summary emission is operational output and may duplicate on rollback only if
  checkpoints are removed; that is acceptable and must be documented.

## 10. Independent Review Loop

This plan changes persistence, cleanup, Monitor behavior, and ops diagnostics.
It requires external review before implementation begins.

Recommended review prompt:

```text
Read docs/plans/2026-05-16-monitor-durable-collation-store-plan.md,
docs/specifications/05-Message_Flow_and_State.md [MF-5],
docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17],
weft/core/tasks/task_monitor.py, weft/core/monitor/cleanup.py,
weft/core/monitor/task_log_scanner.py, and weft/context.py.

Look for errors, bad ideas, and latent ambiguities. Do not implement.
Could you implement this confidently and correctly if asked?
```

Review must specifically check:

- database ownership boundary
- status/result non-dependence on Monitor table
- exact-delete proof
- Postgres and SQLite behavior
- PONG lightweight behavior
- whether successful completed tasks avoid reserved probes
- whether rollout can be disabled without dropping tables

## 11. Out Of Scope

- General Weft ORM or database abstraction.
- SimpleBroker schema changes.
- Public CLI for querying Monitor tables.
- Changing status/result reconstruction to use Monitor tables.
- Legal, forensic, or audit retention guarantees.
- Dropping old queues or compacting SimpleBroker tables directly.
- Suspected-dead task recovery beyond recording explicit future hooks.
- Reserved queue cleanup for failure-like tasks unless it receives a separate
  testable policy slice.
- Changing manager service supervision.

## 12. Fresh-Eyes Self-Review

Findings from the review pass:

- High severity: The first draft allowed Monitor initialization to sound like
  database setup. That was wrong. The plan now states in the goal, invariants,
  Task 5, and rollout notes that Monitor depends on an already initialized
  Weft database and may create only its own tables.
- High severity: The first draft risked storing raw message IDs as a JSON list
  in the task summary row. That would make exact deletion and idempotent replay
  fragile. The plan now requires a normalized
  `weft_monitor_task_messages` child table.
- Medium severity: The first draft made deletion sound like part of the first
  implementation slice. That is risky. The plan now stages schema/collation,
  summary emission, and table-backed deletion behind separate rollout gates.
- Medium severity: The first draft did not clearly say status/result must not
  depend on the store. The plan now states that invariant and lists status and
  result files as not-to-touch.
- Medium severity: The first draft did not distinguish successful terminal
  tasks from failure-like tasks for reserved queue probes. The plan now makes
  "no reserved probe for successful completed tasks" a reducer invariant and a
  cleanup integration test.
- Residual risk: backend-neutral SQL depends on what SimpleBroker exposes
  publicly for raw SQL execution. The plan contains a narrow fallback: keep any
  backend-specific SQL inside `weft/core/monitor/store.py` only. External
  review should verify that this boundary is acceptable before implementation.

After these fixes, the implemented slice has landed with the Monitor table path
behind the configured rollout gates. A separate independent review is still a
good release gate because the slice touches persistence and cleanup.
