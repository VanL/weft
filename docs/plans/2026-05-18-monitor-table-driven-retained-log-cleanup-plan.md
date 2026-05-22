# Monitor Table Driven Retained Log Cleanup Plan

Status: draft
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
Superseded by: none

## 1. Goal

Replace the supervised TaskMonitor's task-log cleanup authority with one
monitor-table-driven path. `weft.log.tasks` becomes a retained FIFO input
stream: once a row is older than `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`, the
monitor folds it into the durable Monitor table and then deletes that exact raw
log row. The Monitor table then owns task-family disposition, summary emission,
and terminal task-local runtime queue cleanup. This should make cleanup
converge under pathological histories without repeatedly scanning the same old
open task families.

This plan intentionally tightens the user-facing proposal in one place:
terminal task families should be marked disposed before they are physically
purged from the Monitor table. Immediate physical deletion of the Monitor row
can recreate duplicate summaries if late task-log rows for the same TID age in
on a later cycle. A later compacting purge may remove disposed rows after a
separate safety horizon, but the first implementation should prefer a compact
disposed row over duplicate lifecycle output.

## 2. Source Documents

- `docs/specifications/00-Quick_Reference.md`: queue names,
  `WEFT_TASK_MONITOR_*` and `WEFT_LOG_TASKS_*` configuration, and the
  distinction between queues and Monitor-owned operational tables.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: `weft.log.tasks`
  as runtime lifecycle evidence, TaskMonitor cycles, Monitor-owned collation
  tables, external task-log output, and exact deletion.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17]: operational monitor state, exact-message cleanup, runtime-state
  pruning, and retention protections.
- `docs/plans/2026-05-16-monitor-durable-collation-store-plan.md`: completed
  plan that introduced the Monitor-owned store.
- `docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`:
  completed plan that added external raw/collated logging and table-backed
  deletion. This new plan changes the cleanup sequencing: in collated mode,
  durable Monitor table ingestion is enough to delete the raw task-log row
  before final family summary emission, because the summary can be retried from
  the table.
- `docs/plans/2026-05-18-monitor-cleanup-reserved-hot-path-plan.md`: completed
  hot-path fix that kept ordinary task-log cleanup away from reserved queue
  probes. Preserve that boundary.
- `../simplebroker/simplebroker/sbqueue.py`: public `Queue.delete_many(...)`
  and `Queue.delete(message_id=...)` APIs. Use these public APIs through Weft's
  existing exact-delete helper; do not drop to private SimpleBroker SQL for
  queue deletion.
- `docs/agent-context/runbooks/writing-plans.md`: required zero-context plan
  standard.
- `docs/agent-context/runbooks/hardening-plans.md`: required because this
  changes destructive cleanup, persistence semantics, and runtime queues.
- `docs/agent-context/runbooks/testing-patterns.md`: required because tests
  must use real broker-backed queues instead of mocking SimpleBroker behavior.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  because this is a high-risk cleanup and persistence contract change.

The current specs still describe the older split between bounded FIFO cleanup
policies and separate durable table collation. This plan is an intended spec
delta. Implementation is not done until the specs above describe the new
single cleanup authority.

## 3. Context And Key Files

Read first:

- `AGENTS.md`: project philosophy, constants rule, SimpleBroker boundary, and
  real-broker test expectations.
- `weft/_constants.py`: all production constants and env/config defaults must
  live here. Do not add module-local production defaults.
- `weft/core/monitor/task_monitor.py`: owns the persistent TaskMonitor
  reactor, cycle scheduling, cached PONG diagnostics, monitor-store cycle,
  summary emission, and current table-backed deletion gate.
- `weft/core/monitor/store.py`: owns Monitor table schema, schema
  idempotency, checkpoints, summary rows, child raw-message rows, and the small
  access layer.
- `weft/core/monitor/sql.py`: owns SQL construction and trusted identifier
  validation for Monitor tables. Follow its SimpleBroker-style SQL builder
  pattern.
- `weft/core/monitor/collation.py`: pure broker-free reducer from task-log row
  payloads to Monitor task updates.
- `weft/core/monitor/task_log_scanner.py`: current generator-backed
  non-consuming task-log scanner. It is useful as an input reader, but family
  grouping should no longer be the cleanup authority.
- `weft/core/monitor/cleanup.py`: current bounded cleanup policy runner. After
  this plan, it should no longer own completed-family task-log deletion in the
  supervised monitor path.
- `weft/core/pruning/apply.py`: canonical exact-message delete helper. All
  queue deletion stays on this path or a tiny adapter around this path.
- `weft/core/queue_window.py`: current neutral decoded row primitives.
- `tests/core/test_monitor_store.py`: Monitor table schema and store behavior.
- `tests/core/test_monitor_collation.py`: pure collation reducer behavior.
- `tests/core/test_task_monitor_cleanup.py`: current cleanup policy tests.
- `tests/tasks/test_task_monitor.py`: TaskMonitor integration, PONG, external
  log, and table-backed deletion tests.
- `tests/helpers/weft_harness.py`: use for CLI/integration proof when needed.

Files to modify:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/README.md`
- `weft/_constants.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/store.py`
- `weft/core/monitor/sql.py`
- `weft/core/monitor/collation.py`
- `weft/core/monitor/task_log_scanner.py`
- `weft/core/monitor/cleanup.py`
- `weft/core/pruning/apply.py` only if the exact-delete adapter cannot express
  multi-queue task-local control cleanup cleanly
- `tests/core/test_monitor_store.py`
- `tests/core/test_monitor_collation.py`
- `tests/core/test_task_monitor_cleanup.py`
- `tests/tasks/test_task_monitor.py`
- `tests/specs/test_plan_metadata.py` only if metadata/index rules themselves
  change, which this plan should not require

Files not to modify unless a failing test proves it is required:

- `weft/core/manager.py`: the manager supervises the monitor. It must not own
  monitor table cleanup decisions.
- `weft/commands/result.py` and `weft/commands/status.py`: public status and
  result reconstruction must not start reading Monitor tables.
- SimpleBroker internals under `../simplebroker`: this plan should use the
  dependency's public exact-delete APIs, not change SimpleBroker.

Comprehension checks before editing:

- Which method currently chooses `"cleanup_policy"` versus
  `"collated_store"` task-log deletion ownership?
- Which current query makes terminal summary readiness depend on terminal
  timestamp rather than the family high-water mark?
- Which method deletes exact broker message IDs today, and why must raw
  task-log deletion keep that helper?
- Which PONG fields are cached from the last cycle, and why must PONG not scan
  queues or query the Monitor table while answering `PING`?
- Which task-local queues are runtime-only control evidence, and which are
  user-visible payload/result evidence?

## 4. Proposed Semantics

### 4.1 Retained FIFO Ingestion

The supervised monitor processes `weft.log.tasks` in FIFO order by visible
message ID.

For each visible row:

1. If the row is malformed, delete the exact raw row and continue. Malformed
   task-log rows do not create Monitor table entries.
2. If the row is well-formed but its message ID is newer than the retention
   cutoff, stop the FIFO ingestion pass. The row stays in `weft.log.tasks`.
3. If the row is well-formed and older than the retention cutoff, reduce it
   into the Monitor table by TID, record the raw message ID in
   `weft_monitor_task_messages`, commit that store update, then delete that
   exact raw row from `weft.log.tasks`.
4. Repeat until the queue is empty, the first too-young row is encountered, the
   per-cycle batch limit is reached, or a bounded wall-clock cycle budget is
   reached.

The ordering is non-negotiable: table update first, raw queue delete second.
If table update succeeds and queue delete fails, the same raw row may be
processed again next cycle; store upserts must stay idempotent. If queue delete
would happen before table update, lifecycle evidence can be lost, so do not do
that.

This is not a distributed exactly-once guarantee. The intended operational
contract is: every eligible visible raw row should leave `weft.log.tasks` after
its first successful durable fold into the Monitor table. Replay after a
delete failure is acceptable and must not duplicate table state.

Previously claimed `weft.log.tasks` rows are a legacy cleanup concern, not part
of visible FIFO ingestion. The new path should keep a bounded policy for
claimed task-log rows because SimpleBroker hides claimed rows from ordinary
FIFO reads. That policy may delete claimed `weft.log.tasks` rows by exact ID
using public SimpleBroker delete APIs, should report its own cached PONG
counts, and must not be treated as task-family evidence. It exists to clear
old rows that were already claimed by prior cleanup code or interrupted
workers. Before implementing this subpath, verify that the installed
SimpleBroker public API exposes claimed message IDs without private SQL. If it
does not, stop and add the missing public API in SimpleBroker first; do not
special-case the broker message table from Weft.

### 4.2 Monitor Table As Cleanup Authority

The Monitor table becomes the task-family state machine for cleanup and
summary disposition. The FIFO stream pass owns raw row ingestion only. It does
not decide that a task family is complete by scanning a bounded window for
matching rows.

Family disposition must use the table's high-water evidence:

- Terminal family ready: `terminal_seen = true` and `last_seen_at_ns <=
  retention_cutoff`.
- Suspected inactive family ready for classification: `terminal_seen = false`,
  the newest known message is older than retention, and newest known message
  age is at least `3 * effective_reporting_interval_seconds`.
- Young or incomplete family: keep.

Table disposition may run only after the FIFO ingestion pass has reached a
completed high-water mark for old visible rows. A pass reaches that mark only
when it stops because the queue is empty or because it encountered the first
too-young visible row. If the pass stops because of `batch_limit`,
`time_budget`, store-write failure, or queue-delete failure, do not run
terminal disposition or suspect classification in that cycle. This prevents
disposing a family whose later old rows are still visible later in
`weft.log.tasks` but have not yet been folded into the Monitor table.

Do not use `COALESCE(completed_at_ns, terminal_message_id, last_seen_at_ns,
last_message_id)` for terminal readiness. That can close a family based on a
terminal event while later rows for the same TID are still younger than
retention. The table disposition gate must use `last_seen_at_ns` or
`last_message_id` as the family high-water mark.

### 4.3 Terminal Disposition

For each terminal family ready by table state and allowed by the completed
FIFO high-water gate:

1. Emit the collated external task-log summary if external collated logging is
   configured.
2. If summary emission fails, record cached PONG diagnostics and leave the
   family undisposed so the summary can be retried. Raw `weft.log.tasks` rows
   may already be gone because their evidence is preserved in the Monitor
   table.
3. Delete exact task-local runtime control rows from `T{tid}.ctrl_in` and
   `T{tid}.ctrl_out` using public SimpleBroker exact-delete APIs through a
   Weft helper. This is allowed only when the recorded TaskSpec control queues
   are the standard per-task names for that TID and the task role is not
   `manager`. Do not delete `weft.manager.ctrl_in`,
   `weft.manager.ctrl_out`, custom global control queues, or any control queue
   whose name does not exactly match the task-local `T{tid}.*` pattern.
4. Mark the Monitor family as disposed with `disposition_reason="terminal"`,
   `disposition_at_ns`, and `task_control_deleted_at_ns` when applicable.
5. Do not physically delete the Monitor family row in this first slice. Keep a
   compact disposed row as a tombstone against late retained rows for the same
   TID. A later table-compaction task may physically purge disposed rows after
   a separate safety horizon and review.

Control queues are safe here because `ctrl_in` and `ctrl_out` are runtime
control evidence. They are not task result payloads. Deleting their old rows
after terminal family disposition directly addresses stale `T*.ctrl_in` and
`T*.ctrl_out` buildup without weakening result recovery.

### 4.4 Suspected Inactive Disposition

For each open family that is old enough to be suspected inactive and allowed
by the completed FIFO high-water gate:

1. Mark the family with `suspect_reason`, `suspect_at_ns`, and cached summary
   fields.
2. Emit an operational summary if collated external logging is configured and
   the summary sink is healthy.
3. Do not delete task-local queues by default.
4. Do not treat suspect classification as public lifecycle truth.
5. Do not physically purge the Monitor table row by default.

Suspected inactive is a classification, not a destructive cleanup policy. If a
future policy wants to delete raw rows or task-local queues for suspected
inactive tasks, it must be explicit and separately reviewed.

If a task family has no usable reporting interval, do not leave it ambiguous
forever. Add a separate non-destructive classification for `stale_open` after
an explicit hard maximum age, configured by a new constant/env var. That
classification may emit an operational summary and keep a compact Monitor row,
but it must not delete task-local queues and must not become public lifecycle
truth. Choose a conservative default and document it in `_constants.py` and
the Quick Reference.

### 4.5 Queue Retention Classes

This plan includes these default destructive actions:

- `weft.log.tasks`: delete eligible malformed rows; fold eligible valid rows
  into the Monitor table, then delete the exact raw row.
- claimed `weft.log.tasks`: delete exact claimed rows as a bounded legacy
  cleanup class because they cannot be consumed by the visible FIFO ingester.
- `T{tid}.ctrl_in`: delete exact rows when the terminal family is disposed.
- `T{tid}.ctrl_out`: delete exact rows when the terminal family is disposed.
- `weft.state.tid_mappings`: keep existing malformed and older-than runtime
  state cleanup, but do not mix it with task-log family disposition.

This plan explicitly does not add default deletion for:

- `T{tid}.outbox`: user-visible result data. It needs an explicit result
  retention policy, not log retention.
- `T{tid}.inbox`: user/work input. It needs task-type-aware retention.
- `T{tid}.reserved`: recovery-sensitive work. Completed terminal proof may
  justify a later bounded policy, but this slice must not add default reserved
  cleanup.
- `weft.state.services` and `weft.state.managers`: manager/service reducer
  cleanup owns those. Do not fold them into task-family cleanup.

### 4.6 Raw External Mode

Raw external mode keeps the existing fail-closed ordering: emit the raw JSONL
record first, then delete the exact raw `weft.log.tasks` row. Raw mode does
not write Monitor collation rows, does not dispose task families, and does not
delete task-local control queues. It may reuse the same FIFO age gate and
catch-up scheduler, but it must not silently switch into the collated table
path. If raw external emit fails, keep the affected raw task-log row visible
and report the cached error through PONG.

### 4.7 Store-Unavailable Behavior

When the Monitor store is unavailable, the supervised monitor must not delete
well-formed `weft.log.tasks` rows in the background. It should still delete
malformed task-log rows if their policy does not require the store, and it may
still prune unrelated runtime-state queues such as `weft.state.tid_mappings`.
PONG must report that valid task-log cleanup is blocked by store
unavailability.

The operational escape hatch is foreground operator maintenance, not an
implicit background fallback. Preserve and document an explicit foreground
`weft system prune --family task-log` path, or add a separate `weft system`
repair/rebuild command before removing the old window-based foreground
capability. Do not re-enable the old family-window cleanup path as an implicit
TaskMonitor fallback; that would recreate two cleanup authorities.

### 4.8 Catch-Up Scheduling

The monitor must not wait the full heartbeat interval between backlog-clearing
cycles when it hit a batch or cycle-time limit and the oldest visible
`weft.log.tasks` row is still older than retention.

Add a bounded catch-up cadence:

- If a cycle stops because of `batch_limit` or `time_budget` and old rows
  remain, schedule the next monitor cycle after a short configurable catch-up
  delay.
- The catch-up delay must not create a busy loop. It should route through the
  existing task wait/heartbeat/reactor timing mechanism, not a private loop
  inside `process_once()`.
- Control messages must still get service between cleanup chunks.
- PONG must expose whether the monitor is in catch-up mode and why the last
  cycle stopped.

### 4.9 Monitor Table Disposition Columns

Use one explicit column model so implementation does not invent overlapping
meanings:

- `summary_emitted_at_ns`: set after the required external collated summary
  has been emitted, or after the monitor records that no external summary is
  required. This is about operational output only.
- `disposition_reason`: final table-family disposition. Valid values in this
  slice are `terminal`, `suspected_inactive`, and `stale_open`.
- `disposition_at_ns`: set when `disposition_reason` is set.
- `suspect_reason`: optional detail for non-terminal dispositions, such as
  `missing_terminal_after_reporting_gap` or `missing_reporting_interval_after_hard_age`.
- `suspect_at_ns`: set when a non-terminal family is classified.
- `task_control_deleted_at_ns`: set only after eligible standard task-local
  `ctrl_in` and `ctrl_out` cleanup has completed or been determined
  inapplicable. If summary emission succeeds but control deletion fails, keep
  `disposition_at_ns` unset so a later cycle retries the family from table
  state.

Do not use `raw_deleted_at_ns` as a family disposition marker. It may continue
to mean "all currently known raw child message rows are deleted" if the
implementation keeps it for compatibility, but terminal/suspect disposition
queries must not depend on it.

## 5. Invariants And Constraints

- `weft.log.tasks` remains runtime lifecycle evidence while retained. The
  Monitor table is operational derived state, not public lifecycle or result
  truth.
- Public status and result reconstruction must not depend on Monitor table
  rows.
- Raw row deletion must be exact-message based.
- Store writes happen before queue deletes.
- PONG reports cached diagnostics only. It must not scan queues, open external
  log files, query Monitor tables, or recompute cleanup while answering PING.
- `TaskMonitor` owns orchestration. Generic pruning policies own reusable
  row policies. The Monitor store owns table state. Do not create a second
  task-log cleanup path.
- No private SimpleBroker SQL for queue deletion. Use public
  `Queue.delete_many(...)`, `Queue.delete(message_id=...)`, or Weft's existing
  exact-delete wrapper around those APIs.
- Keep all constants and env defaults in `weft/_constants.py`.
- Keep schema migrations additive and idempotent. The monitor may create,
  verify, and migrate only its own Monitor tables in an already initialized
  broker database.
- SQLite and Postgres behavior must be tested. SQLite contention and Postgres
  exact-delete behavior are both part of the contract.
- `report_only` remains non-destructive. It may update cached diagnostics, but
  it must not delete queue rows or mark destructive disposition fields.
- `delete` is the destructive built-in processor. It may delete only the
  explicit candidate classes named in this plan.
- External collated log failure blocks terminal family disposition and
  task-local control cleanup for that family, but it does not require
  resurrecting already ingested raw `weft.log.tasks` rows.
- Terminal and suspect disposition must not run after a partial FIFO pass.
  Partial means the pass stopped at `batch_limit`, `time_budget`, store write
  error, or queue delete error.
- Manager and global control queues are never task-local cleanup targets.
- Do not add a new ORM, plugin registry, or broad cleanup framework. This is a
  targeted monitor-store and TaskMonitor orchestration change.

Stop and re-plan if:

- implementation starts making status/result read from Monitor tables;
- task-log deletion still has two live owners in one monitor cycle;
- the code needs to scan `T{tid}.reserved` for ordinary terminal completed
  cleanup;
- tests start mocking SimpleBroker queues for queue deletion or FIFO behavior;
- the plan starts deleting `T{tid}.outbox`, `T{tid}.inbox`, or
  `T{tid}.reserved` by default;
- a destructive path cannot name the exact message IDs and the policy that
  selected them.

## 6. Task Breakdown

### Task 1: Update Specs Before Code

Outcome: make the normative docs match the intended contract so code does not
implement a plan-only behavior.

Files to touch:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`

Required actions:

- Replace the old supervised task-log cleanup description with retained FIFO
  ingestion into the Monitor table.
- State that the Monitor table is the cleanup authority for task families.
- State that table disposition uses family high-water evidence, not the first
  terminal row.
- State that terminal/suspect disposition can run only after a completed FIFO
  high-water pass, not after a batch-limited or time-limited partial pass.
- State that collated external summary failure blocks family disposition, but
  does not require keeping raw task-log rows once durable table ingestion
  succeeded.
- State that raw external mode keeps emit-before-delete semantics and does not
  write Monitor collation rows.
- State that store unavailability blocks background deletion of well-formed
  task-log rows and leaves foreground operator prune/repair as the escape
  hatch.
- State that terminal disposition may delete `T{tid}.ctrl_in` and
  `T{tid}.ctrl_out`, but default cleanup does not delete `T{tid}.outbox`,
  `T{tid}.inbox`, or `T{tid}.reserved`.
- Backlink this plan from the relevant spec section.

Tests:

- `uv run pytest tests/specs -q`

Stop gate:

- If the spec text cannot state the distinction between raw row ingestion and
  family disposition clearly, stop. The implementation will otherwise recreate
  split-brain cleanup.

### Task 2: Extend The Monitor Store Schema Additively

Outcome: durable table rows can distinguish raw ingestion, terminal
disposition, suspect classification, control-queue cleanup, and eventual
physical purge.

Files to touch:

- `weft/_constants.py`
- `weft/core/monitor/sql.py`
- `weft/core/monitor/store.py`
- `tests/core/test_monitor_store.py`

Required actions:

- Add a new Monitor schema version constant in `weft/_constants.py`.
- Add additive columns to `weft_monitor_task_collations`, with an idempotent
  migration for existing tables:
  - `disposition_reason`
  - `disposition_at_ns`
  - `suspect_at_ns`
  - `task_control_deleted_at_ns`
  - optional `purge_after_ns` only if the implementation includes a disabled
    physical-purge helper in this slice
- Keep `weft_monitor_task_messages.deleted_at_ns` as the per-raw-row deletion
  marker.
- Do not reinterpret `raw_deleted_at_ns` as "task family is closed." If it is
  retained, document it as "all currently known raw child message rows are
  deleted" and avoid using it as a terminal disposition gate.
- Add store methods with explicit names:
  - `record_retained_task_log_row(...)` or a batched equivalent that upserts
    the family and child message row in one transaction.
  - `mark_raw_message_deleted(...)` or a batched equivalent.
  - `list_terminal_families_ready_for_disposition(...)`.
  - `list_suspected_inactive_families(...)`.
  - `mark_family_disposed(...)`.
  - `mark_family_suspected(...)`.
  - `mark_task_control_deleted(...)`.
- Implement the column semantics exactly as described in section 4.9. In
  particular, `summary_emitted_at_ns` is not the same thing as
  `disposition_at_ns`, and `suspect_reason` is detail, not the primary
  disposition enum.
- Ensure every SQL query uses the existing `monitor_sql.identifier(...)`,
  `identifier_list(...)`, and placeholder helpers. Do not concatenate
  untrusted identifiers.

Tests:

- Red test first: terminal readiness must not select a family when a later
  non-terminal row has `last_seen_at_ns` newer than retention.
- Migration/idempotency test: creating schema v1-ish tables and running
  `ensure_schema()` twice results in the new columns and current schema
  version.
- Replay test: ingesting the same raw message twice does not duplicate
  messages or regress terminal state.
- Suspect test: open task with known reporting interval is classified only
  after retention plus `3x` interval.
- Stale-open test: open task with no usable reporting interval is classified
  only after the configured hard maximum age, and remains non-destructive.
- Suspect non-delete test: suspected rows are not returned by the default
  terminal deletion/disposition query.
- Partial-failure retry test: a family with `summary_emitted_at_ns` set but
  failed control cleanup remains selectable for disposition retry until
  `task_control_deleted_at_ns` and `disposition_at_ns` are set.

Verification:

- `uv run pytest tests/core/test_monitor_store.py -q`
- `uv run bin/pytest-pg tests/core/test_monitor_store.py`

### Task 3: Build The Retained FIFO Ingestion Loop

Outcome: old `weft.log.tasks` rows are folded into the Monitor table and then
deleted once, instead of repeatedly grouped in a bounded FIFO window.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/task_log_scanner.py`
- `weft/core/monitor/collation.py`
- `weft/core/pruning/apply.py` only if a tiny exact-delete adapter is needed
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_task_monitor_cleanup.py`

Required actions:

- Add an internal result type for retained FIFO ingestion diagnostics. It
  should include at least:
  - rows scanned
  - malformed rows deleted
  - valid rows ingested
  - raw rows deleted
  - claimed rows deleted
  - store write failures
  - queue delete failures
  - stop reason
  - oldest too-young row age if known
  - `completed_fifo_high_water`: true only when the pass stopped on `empty`
    or `first_too_young`
- Read `weft.log.tasks` using generator-backed FIFO iteration. Do not use a
  fixed `peek_many` as the correctness path.
- Decode each row. Use message ID age for retention gating.
- Delete eligible malformed rows through exact delete and continue.
- For eligible valid rows, call the pure reducer, write the Monitor store, and
  only then delete that exact raw row.
- Add a bounded claimed-row cleanup subpass for `weft.log.tasks` using public
  SimpleBroker APIs. Keep it separate from family collation. It should not
  require Monitor table evidence because claimed task-log rows are already
  invisible to the FIFO ingester and cannot contribute to new table state.
  If the public API cannot enumerate claimed message IDs, stop and add that
  API in SimpleBroker before continuing.
- Mark the child message row deleted only after broker deletion succeeds.
- If store write fails, keep the raw row and stop or record an error according
  to the bounded cycle policy. Do not delete that raw row.
- If queue delete fails after store write, leave the raw row visible and rely
  on idempotent replay. Record cached diagnostics.
- Remove the durable Monitor checkpoint as the cleanup source of truth for
  raw-row deletion. A checkpoint may remain as an observation metric, but the
  raw queue itself is the cleanup cursor.
- Keep custom processor candidate scanning separate. This plan changes the
  built-in supervised cleanup path, not the custom read-only processor
  snapshot contract.

Tests:

- Old malformed row is deleted and the next old valid row is still processed.
- Valid old start row updates the store and is removed from `weft.log.tasks`.
- First young valid row stops the FIFO pass and remains visible.
- A pass that stops on batch or time limit reports
  `completed_fifo_high_water=false`.
- A pass that stops on empty queue or first too-young row reports
  `completed_fifo_high_water=true`.
- A later old row is not skipped because an earlier old open-start TID exists.
- Previously claimed task-log rows are deleted by exact ID and reported
  separately from FIFO-ingested rows.
- Store write failure leaves the raw row visible.
- Queue delete failure leaves the raw row visible and replay-safe.
- `report_only` does not delete raw rows or mark child messages deleted.

Verification:

- `uv run pytest tests/tasks/test_task_monitor.py -q`
- `uv run pytest tests/core/test_task_monitor_cleanup.py -q`
- `uv run bin/pytest-pg tests/tasks/test_task_monitor.py`

### Task 4: Move Terminal Family Disposition To Table State

Outcome: terminal task families are summarized and disposed from Monitor table
state, not from a FIFO scan window.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/store.py`
- `weft/core/monitor/sql.py`
- `weft/core/monitor/external_log.py` only if external summary status needs
  a small new field
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_store.py`

Required actions:

- Replace the current table delete condition with a terminal-family
  disposition query that requires:
  - terminal evidence;
  - no previous disposition;
  - family high-water mark older than retention.
- Run terminal and suspect disposition only when the current FIFO ingestion
  result has `completed_fifo_high_water=true`. If the FIFO pass stopped on
  batch limit, time budget, store write error, or queue delete error, skip
  table disposition for the cycle and set catch-up diagnostics.
- Emit collated external summaries from table state when configured.
- If external summary emit succeeds or no external summary is required, mark
  `summary_emitted_at_ns` and `disposition_at_ns`.
- If external summary emit fails, leave disposition unset and report cached
  error state.
- Do not return suspected-inactive families from the default raw-delete or
  terminal-disposition query.
- Ensure `summary_emitted_at_ns` no longer gates raw-row ingestion deletion.
  Raw rows are deleted after durable ingestion; summary emission gates family
  disposition and control-queue cleanup.

Tests:

- Terminal family is not disposed until `last_seen_at_ns` is older than
  retention, even if `terminal_message_id` is older.
- Terminal family is not disposed in a cycle where FIFO ingestion stopped on
  `batch_limit` with later old rows still visible for the same TID.
- External collated summary failure keeps family undisposed and leaves control
  queues intact.
- External collated summary success marks disposition from the table.
- Suspected-inactive family is classified but not disposed as terminal and not
  selected for control queue cleanup.

Verification:

- `uv run pytest tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py -q`

### Task 5: Add Terminal Control-Queue Cleanup

Outcome: terminal disposed task families clean stale `T{tid}.ctrl_in` and
`T{tid}.ctrl_out` runtime rows without touching result or recovery queues.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/store.py`
- `weft/core/pruning/apply.py` or a new small helper under
  `weft/core/monitor/` if the existing exact-delete helper needs an adapter
- `tests/tasks/test_task_monitor.py`

Required actions:

- Implement a helper that deletes exact visible and claimed rows from:
  - `T{tid}.ctrl_in`
  - `T{tid}.ctrl_out`
- Use public SimpleBroker queue APIs. Prefer `Queue.delete_many(...)` after
  collecting message IDs from a generator. Do not issue direct SQL against the
  broker message table.
- Before collecting claimed control rows, verify that SimpleBroker exposes a
  public claimed-row iterator or exact-ID collection API. If it does not, add
  that API in SimpleBroker first or skip claimed control rows with explicit
  PONG diagnostics; do not reach into private SQL from Weft.
- Do not delete the queue object/table directly. Empty-queue table compaction
  remains `weft system tidy` or backend maintenance.
- Validate that the TID is task-shaped before constructing queue names.
- Delete control queues only when the recorded TaskSpec control queues are
  exactly `T{tid}.ctrl_in` and `T{tid}.ctrl_out`, and the recorded role is not
  `manager`. Never delete `weft.manager.ctrl_in`, `weft.manager.ctrl_out`, or
  custom/global control queues through terminal task-family cleanup.
- Cap rows per control queue per cycle to avoid one pathological queue
  starving other cleanup.
- Mark `task_control_deleted_at_ns` only after deletes have been attempted and
  record counts/errors in cached PONG.

Tests:

- Terminal disposed family removes old `ctrl_in` and `ctrl_out` rows.
- It does not delete `outbox`, `inbox`, or `reserved`.
- A terminal manager-family row or custom/global control queue does not delete
  manager/global control queues.
- If control queue delete fails, summary emission remains recorded but
  `disposition_at_ns` stays unset, PONG reports the control cleanup error, and
  the helper retries later.
- Claimed control rows are eligible for deletion by exact ID using the
  SimpleBroker public delete API.

Verification:

- `uv run pytest tests/tasks/test_task_monitor.py -q`
- `uv run bin/pytest-pg tests/tasks/test_task_monitor.py`

### Task 6: Disable The Old Task-Log Family Cleanup Owner

Outcome: the supervised monitor has one live task-log cleanup owner.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/cleanup.py`
- `tests/core/test_task_monitor_cleanup.py`
- `tests/tasks/test_task_monitor.py`

Required actions:

- Change `_task_log_deletion_owner()` so the normal `delete` processor uses
  the retained FIFO + Monitor table path when collation store is enabled and
  available.
- Preserve raw external mode as a separate owner: raw mode emits each eligible
  raw task-log row to the configured external log before exact deletion and
  does not write Monitor collation rows or clean task-local control queues.
- Do not fall back to bounded family-window deletion for well-formed task-log
  rows when the store is unavailable. If the store is unavailable, leave
  well-formed task-log rows in place and report the cached error.
- Keep malformed task-log deletion in the retained FIFO path.
- Keep `weft.state.tid_mappings` cleanup in `run_task_monitor_cleanup`.
- Remove or quarantine completed-family and terminal-without-start task-log
  deletion from the supervised default path. If helper functions remain for
  foreground/operator tooling, name that boundary clearly and test that the
  supervised monitor does not call them.
- Before removing helpers, audit `weft system prune --family task-log` and
  document which foreground operator path remains available when the Monitor
  store is unavailable. Do not delete the only documented emergency cleanup
  path.
- Preserve `report_only` as non-destructive.

Tests:

- A scan window dominated by old open-start families still drains old valid
  rows into the table and deletes raw rows. There should be no repeated
  selection of the same old open family.
- When the store is unavailable, valid task-log rows are not deleted by the old
  fallback family policy.
- Raw external mode still emits before deleting raw rows and does not write
  Monitor table rows.
- Policy stats/PONG make clear that task-log cleanup was table-driven or
  skipped due to store unavailability.

Verification:

- `uv run pytest tests/core/test_task_monitor_cleanup.py tests/tasks/test_task_monitor.py -q`

### Task 7: Add Catch-Up Mode Without A Busy Loop

Outcome: ops backlogs shrink on deployment time scales without making the
manager or monitor CPU-hot.

Files to touch:

- `weft/_constants.py`
- `weft/core/monitor/runtime.py`
- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Required actions:

- Add a small config knob only if existing scheduling state cannot express the
  behavior. Use concrete constants in `weft/_constants.py`:
  `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS` and
  `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS_DEFAULT`.
  Start with a conservative default such as `2.0` seconds unless tests or ops
  evidence justify a faster cadence.
- Add `WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS` and
  `WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS_DEFAULT` for non-destructive
  `stale_open` classification of families that have no usable reporting
  interval. Choose a default that is clearly longer than
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`.
- Add a named control cleanup per-queue cap, for example
  `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT` and a default, rather than
  hard-coding a number inside `TaskMonitor`.
- If a cleanup cycle stops because it hit the batch or time budget while old
  eligible rows remain, set a cached catch-up flag and schedule the next
  cycle after the catch-up interval.
- Implement catch-up through the normal task wait mechanism, such as
  `next_wait_timeout()` / wake state. Do not add a private while-loop inside
  `process_once()`.
- Ensure task-local control still gets a turn between cleanup cycles.
- Clear catch-up mode when the cycle stops on `first_too_young`, `empty`,
  or an error that should not be retried hot.
- Add a throughput note to the spec or plan-driven implementation comments:
  expected steady catch-up throughput is roughly `batch_size /
  catchup_interval` retained raw rows per second before external sink overhead.
  Operators can temporarily increase `WEFT_TASK_MONITOR_BATCH_SIZE` or reduce
  catch-up interval for backlog recovery, while watching CPU and PONG stop
  reasons.

Tests:

- A cycle that hits batch limit reports catch-up pending and a short next wait.
- A cycle that stops at first young row reports no catch-up pending.
- PING is still answered while catch-up is pending.

Verification:

- `uv run pytest tests/tasks/test_task_monitor.py -q`

### Task 8: Extend Cached PONG Diagnostics

Outcome: operators can tell whether the monitor is ingesting, disposing,
classifying, or blocked without triggering active work from PING.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`

Required cached fields:

- FIFO ingestion:
  - scanned
  - malformed_deleted
  - valid_ingested
  - raw_deleted
  - claimed_deleted
  - stop_reason
  - completed_fifo_high_water
  - oldest_too_young_age_seconds
  - store_write_errors
  - raw_delete_errors
- Table disposition:
  - terminal_ready
  - terminal_disposed
  - suspect_ready
  - suspect_classified
  - stale_open_classified
  - external_summary_blocked
  - control_rows_deleted
  - control_delete_errors
- Scheduling:
  - catchup_pending
  - next_cycle_due_in_seconds
  - last_cycle_stop_reason

Tests:

- PING returns cached fields and does not call the FIFO ingester, store query,
  external log validation, or control queue cleanup while answering.
- PONG distinguishes "ran and selected zero" from "did not run because store
  unavailable."
- Existing PONG fields remain stable where possible. If a field is renamed or
  split, update the spec and tests in the same task so operators can map old
  output to new output.

Verification:

- `uv run pytest tests/tasks/test_task_monitor.py -q`

### Task 9: Backfill And Ops-Dump Regression Tests

Outcome: the plan is proven against the failure shape we saw on ops.

Files to touch:

- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_store.py`
- a fixture/helper under `tests/helpers/` only if needed for compact
  synthetic backlog setup

Required tests:

- Synthetic backlog with:
  - thousands of old open-start rows;
  - old completed families interleaved later;
  - newer rows at the FIFO boundary.
  The monitor should ingest and delete old raw rows until the first young row,
  not repeatedly rescan the old open families.
- Terminal family with late post-terminal row:
  - terminal row older than retention;
  - later same-TID row younger than retention.
  The family must not be disposed until the later row ages and is ingested.
- Partial FIFO pass:
  - terminal row for TID appears before the batch limit;
  - later old row for the same TID appears after the batch limit.
  The family must not be disposed or control-cleaned until a later completed
  FIFO high-water pass.
- Disposed tombstone replay:
  - after disposition, a duplicate old raw row for the same TID is replayed.
  The store must not emit a second summary.
- External log failure:
  - raw rows are already safely folded/deleted;
  - terminal disposition stays blocked and retryable from table state.
- Control cleanup:
  - terminal disposition deletes `ctrl_in`/`ctrl_out`;
  - `outbox`, `inbox`, and `reserved` remain intact.
- Manager/control exclusion:
  - terminal manager or custom-control task families never delete
    `weft.manager.ctrl_in`, `weft.manager.ctrl_out`, or custom global control
    queues.
- Store unavailable:
  - malformed task-log cleanup may continue;
  - valid well-formed task-log rows stay visible;
  - PONG reports the store error and blocked valid cleanup.
- Concurrent monitors:
  - two TaskMonitor instances against the same SQLite context do not emit
    duplicate external summaries for the same terminal family. If this cannot
    be guaranteed in the first slice, the plan must say so and leave external
    summary emission disabled until a transactional claim/mark is implemented.
- Performance smoke:
  - a store with a large number of disposed tombstone rows still selects ready
    terminal/suspect families using indexed predicates. If the query plan is
    backend-specific, keep the test as a bounded timing smoke rather than an
    exact query-plan assertion.
- Claimed row setup:
  - use real SimpleBroker public APIs or a real worker/consumer that claims and
    exits. Do not mock claimed rows. If the public API cannot create or list
    claimed rows in tests, stop and add the SimpleBroker API before testing the
    claimed cleanup path.

Use real broker queues. Do not mock SimpleBroker queues or delete calls.

Verification:

- `uv run pytest tests/tasks/test_task_monitor.py tests/core/test_monitor_store.py -q`
- `uv run bin/pytest-pg tests/tasks/test_task_monitor.py tests/core/test_monitor_store.py`

### Task 10: Remove Or Reclassify Superseded Code

Outcome: there is one live code path for supervised task-log cleanup.

Files to touch:

- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/task_log_scanner.py`
- `tests/core/test_task_monitor_cleanup.py`
- `docs/specifications/05-Message_Flow_and_State.md`

Required actions:

- First audit the public foreground prune behavior:
  - `weft system prune --family task-log`
  - `weft system prune --family retention`
  - any command-layer tests under `tests/commands/` that assert task-log
    retention behavior.
  Record which path remains the emergency operator cleanup path when the
  Monitor store is unavailable.
- Remove code that only exists to do completed-family task-log deletion from a
  bounded FIFO window if no foreground/operator path still uses it.
- If a helper remains for explicit foreground tooling, rename or document it
  so a future implementer cannot mistake it for the supervised monitor path.
- Specific supervised-path cleanup helpers to audit include `_task_log_candidates`,
  complete lifecycle candidate selection, terminal-without-start selection,
  broad old-without-start deletion, and any call that wires those helpers into
  `TaskMonitor`.
- Keep generic malformed and older-than policies only where they are still
  used by runtime-state cleanup or the new FIFO ingestion path.
- Update tests so they prove the new path, not the removed path.

Stop gate:

- If deleting the helper would make `weft system prune` lose a documented
  public behavior, stop and split foreground prune preservation into a
  separate task. Do not silently remove public operator tooling.

Verification:

- `uv run pytest tests/core/test_task_monitor_cleanup.py tests/commands -q`

### Task 11: Full Verification And Rollout Notes

Outcome: implementation has local and deployment evidence.

Required local commands:

- `uv run pytest tests/core/test_monitor_store.py tests/core/test_monitor_collation.py tests/core/test_task_monitor_cleanup.py tests/tasks/test_task_monitor.py -q`
- `uv run bin/pytest-pg tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py`
- `uv run pytest tests/specs -q`
- `uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
- `uv run ruff check weft`

Recommended broader commands before release:

- `uv run pytest`
- `uv run bin/pytest-pg`

Post-deploy ops evidence:

- `weft task ping <task-monitor-tid>` should show:
  - retained FIFO rows scanned;
  - raw rows ingested;
  - raw rows deleted;
  - terminal families disposed;
  - control rows deleted;
  - catch-up pending while backlog remains;
  - no store error.
- Direct queue counts should show:
  - `weft.log.tasks` total decreasing while old rows exist;
  - `T*.ctrl_in` and `T*.ctrl_out` decreasing after terminal dispositions;
  - `T*.outbox`, `T*.inbox`, and `T*.reserved` not dropping from this policy.
- `py-spy` should show the monitor working during catch-up chunks and then
  returning to `wait_for_activity`, not a hot private loop.

Rollback:

- Keep `report_only` non-destructive as an immediate operator escape hatch.
- If destructive task-log ingestion misbehaves, set the monitor processor to
  `report_only` or disable TaskMonitor until fixed. Do not re-enable the old
  family-window task-log cleanup path as an implicit fallback; that reintroduces
  split-brain cleanup.
- Additive Monitor table columns are safe to leave in place on rollback.

## 7. Review Plan

Self-review is mandatory after drafting and before implementation.

External review is also required before implementation because this plan
changes destructive cleanup semantics, Monitor-owned persistence semantics,
task-local runtime queue cleanup, and deployment observability. The reviewer
should be asked:

- Can you implement this confidently from the plan?
- Does the plan leave any ambiguity about what owns raw-row deletion?
- Does the tombstone/disposition choice prevent duplicate summaries without
  creating unbounded table growth?
- Are the tests real enough to catch SQLite and Postgres contention issues?

External review has been run once against this plan and the current monitor
implementation. The review found several implementation-blocking ambiguities
in the first draft:

- Family disposition could run after a partial FIFO pass, which could close a
  TID before all retained rows for that TID had been ingested.
- Store-unavailable behavior did not name a safe operator path and risked
  silently falling back to the old bounded FIFO cleanup authority.
- Claimed `weft.log.tasks` rows were not addressed, even though visible FIFO
  iteration cannot see them.
- Open tasks without a usable `reporting_interval` could remain in ambiguous
  table state forever.
- Terminal cleanup did not explicitly exclude manager and custom/global
  control queues.
- Raw external mode semantics were under-specified after the table-driven
  change.
- Monitor table disposition fields had overlapping possible meanings.
- External summary emission under concurrent monitors needed either a
  transactional once-only guarantee or a documented first-slice limitation.

This revision incorporates those findings by adding the completed high-water
gate, store-unavailable fail-closed behavior, claimed-row API stop gate,
`stale_open` classification, explicit control-queue eligibility checks, raw
external mode semantics, table-field definitions, and concurrency tests. A
final implementation review must verify that the code follows those revised
constraints, not the looser first draft.

## 8. Fresh-Eyes Self-Review

Findings from the first self-review:

- The user proposal said to physically delete the Monitor row at terminal
  evidence. That is unsafe when later rows for the same TID have not yet aged
  into the FIFO ingestion pass. The plan now marks terminal families disposed
  and leaves a compact tombstone row. Physical purge is explicitly not part of
  this first slice.
- The current spec says external logging must happen before deleting affected
  raw task-log rows. The proposed stream model intentionally changes that for
  collated mode: durable Monitor ingestion must happen before raw deletion,
  while external collated summary emission gates family disposition. The plan
  now calls this out as a spec delta instead of hiding it.
- The initial idea risked using terminal timestamp as the family-close clock.
  That repeats the current bug class. The plan now requires family high-water
  gating via `last_seen_at_ns` / `last_message_id`.
- The stale queue counts could tempt a broad `T*.reserved`, `T*.inbox`, and
  `T*.outbox` sweeper. The plan now limits this slice to `ctrl_in` and
  `ctrl_out`, and explicitly leaves result/work/recovery queues alone.
- Catch-up cadence was missing from the first mental model. Without it, a
  large backlog would shrink only once per heartbeat interval. The plan now
  includes bounded catch-up scheduling and PONG evidence.

Residual risk:

- Keeping disposed Monitor rows as tombstones may grow one compact row per
  task. That is deliberately smaller and safer than keeping raw lifecycle
  queues large. A physical table purge should be a later policy after the
  retained FIFO path proves stable on ops.
- Claimed-row cleanup depends on SimpleBroker exposing enough public API to
  enumerate or address claimed rows by message ID. If that API is missing,
  this plan deliberately stops rather than bypassing SimpleBroker's layer.
- The completed high-water gate means a large old backlog may delay terminal
  summary/control cleanup until the FIFO pass reaches `empty` or
  `first_too_young`. That is the right tradeoff for correctness. Catch-up
  scheduling is what makes the delay operationally tolerable.
- Concurrent monitor safety is still a real implementation hazard. The code
  must make external summary marking transactional enough to prevent duplicate
  summaries, or keep that feature disabled until the transactional claim/mark
  path exists.
