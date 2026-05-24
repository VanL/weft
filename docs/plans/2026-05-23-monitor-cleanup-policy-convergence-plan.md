# Monitor Cleanup Policy Convergence Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.3]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
Superseded by: none

## 1. Goal

Fix the TaskMonitor cleanup policies so they select the right work, converge to
a cheap "nothing actionable" state, and stay bounded under the pathological
histories observed on ops. The core fixes are: make dead-task selection
actionability-aware, prevent Monitor-store bookkeeping from claiming raw broker
deletion unless raw broker rows are actually gone, recover raw `weft.log.tasks`
rows that were orphaned by older Monitor-store state, and prove each cleanup
policy with real broker-backed tests rather than mock-heavy implementation
checks.

This is not a new monitor architecture plan. It assumes the current
TaskMonitor reactor/worker split and the Monitor-owned store remain in place.

## 2. Source Documents

Read these before editing code:

- `AGENTS.md`: repo philosophy, SimpleBroker boundary, constants rule, and
  real-broker test expectations.
- `docs/agent-context/README.md`,
  `docs/agent-context/decision-hierarchy.md`,
  `docs/agent-context/principles.md`, and
  `docs/agent-context/engineering-principles.md`: shared engineering rules.
- `docs/agent-context/runbooks/writing-plans.md`: plan structure and
  zero-context requirements.
- `docs/agent-context/runbooks/hardening-plans.md`: mandatory here because
  this changes destructive cleanup, durable Monitor-store state, and
  background worker convergence.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  independent review workflow for this risky slice.
- `docs/agent-context/runbooks/testing-patterns.md`: test harness guidance.
- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]:
  TaskMonitor is a supervised service task. Its reactor stays responsive, and
  cleanup work happens in worker lanes with cached PONG diagnostics.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.3]:
  Weft uses public SimpleBroker queue APIs for queue operations and does not
  issue ad hoc SQL against broker queue tables.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: retained
  task-log cleanup, Monitor-store collation, raw exact deletion, summary,
  disposition, runtime queue cleanup, and PONG diagnostics.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17]: operational nature of Monitor state, exact cleanup boundaries,
  runtime cleanup, and retention cleanup.
- `docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`:
  draft plan that introduced the table-driven direction. It is useful context,
  but this plan is stricter about proof and fixes the gaps exposed on ops.
- `docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`:
  completed terminal runtime cleanup plan. Keep the terminal cleanup safety
  model.
- `docs/plans/2026-05-20-monitor-collation-table-retirement-plan.md`:
  completed plan that made Monitor parent rows physically retire. This plan
  corrects when retirement is safe.
- `docs/plans/2026-05-22-monitor-policy-modules-and-dead-task-cleanup-plan.md`:
  completed plan that added dead-task policy modules. This plan tightens that
  policy so selected TIDs must have work that is actionable now.
- `docs/plans/2026-05-23-monitor-cleanup-executor-plan.md`: completed mixed
  executor plan. The ops follow-up supersedes its mixed-executor assumption for
  runtime cleanup: terminal, reserved, and dead-TID cleanup now run as discrete
  worker-thread slices launched by the TaskMonitor reactor.

Comprehension checks before editing:

- Which method checkpoints retained `weft.log.tasks` ingestion, and under what
  conditions can it advance beyond rows that were not ingested or deleted?
- Which Monitor-store method currently reconciles `raw_deleted_at_ns`, and
  what does it actually prove today?
- Which runtime cleanup selector can pick dead TIDs that have no eligible queue
  deletion work now?
- Which paths may use public SimpleBroker queue delete APIs, and which paths
  must not drop into broker table SQL?

## 3. Current Evidence And Failure Modes

Ops evidence from the 0.9.58 investigation showed services were responsive but
cleanup had stalled around a large `weft.log.tasks` tail:

- `weft.log.tasks` had roughly 53k rows, all unclaimed.
- `weft_monitor_task_messages` had roughly 3.5k rows, none selected or
  deleted.
- `weft_monitor_task_collations` had roughly 2.1k parent rows. Most terminal
  rows had summary and `raw_deleted_at_ns`, but many raw broker rows still
  existed.
- Two old TaskMonitor TIDs accounted for almost all remaining task-log rows:
  one with about 45k rows and one with about 4.7k rows. Their collation rows
  were terminal and had `raw_deleted_at_ns`, but the raw broker rows remained.
- `store_deletable_refs = 0` and `retirable_families = 0`, so the ordinary
  table-backed raw deletion path could no longer see those broker rows.
- The runtime cleanup worker repeatedly spent time in dead-task log coalescing
  and SimpleBroker connection setup. Many selected dead TIDs were outbox-only
  or otherwise retention-deferred, so they had no cleanup action available in
  the current cycle.

The code-level failure modes to fix are:

- `_ingest_retained_task_log_rows()` skips rows whose `update.tid == self.tid`
  but may later checkpoint beyond them. A skipped raw row then becomes
  invisible to FIFO ingestion.
- `MonitorStore.delete_task_messages_after_raw_delete()` physically removes
  child refs and reconciles `raw_deleted_at_ns` based on child-ref absence.
  That proves only "tracked child refs are gone", not "raw broker rows for
  this TID are gone".
- Store selectors for deletable task-log refs require
  `c.raw_deleted_at_ns IS NULL`, so a parent row incorrectly marked raw-deleted
  prevents later store-backed deletion from finding orphan raw broker rows.
- `select_dead_task_tids_from_queue_names()` selects historical TIDs before
  applying the per-TID queue cleanup plan. The runtime cleanup slice can spend
  cycles on TIDs with no eligible queue names now, then mark the dead-task tail
  as still pending.
- `_coalesce_and_delete_dead_task_log_rows_for_tids()` runs for every
  successful dead-TID cleanup result, even if no queue was deleted and the TID
  only represented retention-deferred/no-op work.

## 4. Context And Key Files

Files to modify:

- `weft/core/monitor/task_monitor.py`
  - `_ingest_retained_task_log_rows()`
  - `_run_monitor_store_cycle()`
  - `_run_terminal_control_cleanup_slice()`
  - `_coalesce_and_delete_dead_task_log_rows_for_tids()`
  - cached PONG state assembly
- `weft/core/monitor/store.py`
  - raw deletion reconciliation
  - parent retirement eligibility
  - selectors for orphan recovery and policy stats
- `weft/core/monitor/sql.py`
  - SQL construction for Monitor-owned tables only
  - use the existing `identifier(...)` and `placeholders(...)` helpers
- `weft/core/monitor/policies/dead_task.py`
  - actionability-aware dead-TID selection
  - dead-task log coalesce eligibility
- `weft/core/monitor/policies/task_log.py` or a new focused module under
  `weft/core/monitor/policies/`
  - recovery policy for raw task-log rows that have terminal/disposed store
    evidence but no child refs
- `weft/core/monitor/runtime.py`
  - only if cached PONG stats need one new config-derived field
- `weft/_constants.py`
  - only if an existing constant cannot express the required bound. Prefer
    existing `WEFT_TASK_MONITOR_BATCH_SIZE`,
    `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT`, and runtime cleanup limits.
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/README.md`
- `tests/core/monitor/policies/test_dead_task.py`
- `tests/core/test_monitor_store.py`
- `tests/core/test_task_monitor_cleanup.py`
- `tests/tasks/test_task_monitor.py`
- `tests/specs/test_plan_metadata.py` only if the metadata policy itself
  changes. This plan should not require that.

Files to read but not casually modify:

- `weft/core/pruning/apply.py`: canonical exact-message delete application.
  Reuse this or its existing call path for raw `weft.log.tasks` rows.
- `weft/core/monitor/task_log_scanner.py`: generator-backed task-log scanning.
  Keep FIFO/high-water semantics here.
- `weft/core/monitor/collation.py`: pure reducer from task-log rows to
  Monitor-store updates.
- `tests/helpers/weft_harness.py`: use for CLI/service tests, not pure policy
  tests.
- `../simplebroker/simplebroker/_sql.py` and adjacent SimpleBroker SQL access
  examples: follow the safe identifier/placeholder construction pattern when
  touching Monitor-owned SQL.

Do not modify:

- `weft/core/manager.py`: manager supervision is not the cleanup policy owner.
- `weft/commands/status.py`, `weft/commands/result.py`: public status/result
  reconstruction must not start reading Monitor-owned tables.
- `../simplebroker` internals: this plan should use the installed dependency's
  public API and the Monitor-owned access layer.

## 5. Invariants And Proof Standard

The implementation must preserve these invariants:

- `weft.log.tasks` remains runtime lifecycle evidence while retained. It is
  not audit evidence, result authority, or public lifecycle truth.
- Monitor-owned tables are derived operational state only. They must not
  change public status/result reconstruction.
- The Monitor may delete exact broker rows only through supported cleanup
  policies and public SimpleBroker queue APIs.
- The Monitor reactor must not scan queues, query the Monitor store, or delete
  rows while answering PING/STATUS/STOP/KILL. PONG remains cached.
- `raw_deleted_at_ns` must mean raw broker rows represented by that Monitor
  family are gone or have been proven absent. It must not mean only "child refs
  were removed from `weft_monitor_task_messages`".
- A checkpoint must never advance past a visible raw `weft.log.tasks` row that
  was neither ingested for durable collation nor exact-deleted under an
  explicit policy.
- Terminal task families with complete high-water evidence do not need to wait
  for the general task-log retention period before raw lifecycle rows are
  exact-deleted.
- `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and `T{tid}.inbox` are stale after a
  terminal or proven-dead owner. `T{tid}.outbox` and `T{tid}.reserved` stay
  retention-gated unless the reserved policy explicitly selects them.
- Dead-task cleanup must not delete active owners, manager/global/custom
  control queues, or user payload queues.

The proof bar is higher than "tests pass". Each policy must have:

- **Selection correctness proof**: a test or small proof table showing every
  selected row, TID, or queue satisfies that policy's predicate, and a
  near-miss fixture showing non-selected data is left alone.
- **Convergence proof**: a test showing repeated cycles strictly reduce a
  named backlog measure until no actionable work remains, then the next cycle
  reports no work without redoing expensive scans.
- **Boundedness proof**: a test or instrumentation assertion showing the policy
  obeys configured batch/scan/worker limits and cannot turn one cycle into an
  unbounded per-TID scan.
- **Idempotence proof**: a retry test showing already-deleted or already-absent
  rows/queues do not cause errors, duplicate summaries, or stuck pending flags.
- **Observability proof**: PONG or cached cycle stats must report why a policy
  selected zero rows, deferred rows, or stopped early, without performing work
  during PING.

Use real broker-backed queues for these proofs. Mocking SimpleBroker queue
semantics, Monitor-store SQL behavior, checkpoint writes, or raw exact delete
results is a stop-and-re-evaluate signal unless the test is a narrow pure
function test in `weft/core/monitor/policies/`.

The implementation must make these proof surfaces explicit in code or cached
cycle stats:

- `source_seen`: how many rows, TIDs, families, or queues the policy
  considered before filtering.
- `selected`: how many entries were selected for an effect in this cycle.
- `deleted` or `processed`: how many selected entries had their intended effect
  completed.
- `deferred`: how many entries matched the broad policy domain but were not
  actionable yet, such as retention-gated outbox queues.
- `blocked_reason_counts`: why entries could not be selected or completed,
  for example active owner, retention not reached, missing raw absence proof,
  external summary failure, scan limit, batch limit, or worker deadline.
- `pending`: whether more actionable work remains now. This must not be true
  merely because deferred future work exists.

A cleanup cycle has converged only when every policy's actionable measure is
zero and all remaining entries are either outside the policy domain, explicitly
deferred with a future eligibility condition, or blocked by a named retryable
error. A policy that reports `selected = 0` without a reason is not debuggable
enough for this slice.

Named convergence measures:

- `M_task_log_fifo_unhandled`: in `delete` processor plus collated-store mode,
  visible `weft.log.tasks` rows at or before the retained FIFO checkpoint that
  have not been ingested, malformed-deleted, or explicitly deleted. This must
  always be zero.
- `M_task_log_child_refs`: `weft_monitor_task_messages` rows eligible for exact
  raw deletion retry.
- `M_task_log_orphans`: raw broker rows for terminal/disposed Monitor families
  where child refs are missing or parent raw state is inconsistent.
- `M_terminal_runtime_queues`: eligible standard `ctrl_in`, `ctrl_out`, and
  `inbox` queues for terminal families.
- `M_dead_task_actionable_queues`: eligible standard stale queues for
  proven-dead TIDs after active-owner and retention filters.
- `M_retirable_families`: Monitor parent rows with all required gates complete.

For each measure, a successful cycle must either strictly decrease the measure,
hit a configured bound and report catch-up pending, or report zero actionable
work. A cycle that repeatedly scans the same source and neither decreases a
measure nor reports a retryable blocker is a failed convergence proof.

## 6. Policy Matrix

This table defines the expected cleanup policy behavior and proof obligations.

| Policy | Selection source | Correct selection | Convergence target | Bound |
| --- | --- | --- | --- | --- |
| `tid_mapping.delete_malformed` | `weft.state.tid_mappings` rows | malformed Weft-owned runtime-state rows only | malformed row count reaches zero | batch/scan limit |
| `tid_mapping.delete_older_than` | `weft.state.tid_mappings` rows | mapping timestamp older than configured cleanup age | old mapping count reaches zero, first too-young row stops cheap | batch/scan limit |
| `task_log.ingest_retained_fifo` | visible `weft.log.tasks` FIFO window | every checkpointed raw row is ingested or explicitly deleted | checkpoint advances only with durable ingestion/delete | task-log scan and batch limits |
| `task_log.delete_malformed` | visible `weft.log.tasks` FIFO window | malformed Weft lifecycle rows only | malformed row count reaches zero | task-log scan and batch limits |
| `task_log.delete_tracked_refs` | Monitor child refs joined to terminal/suspect parent rows | exact message IDs recorded in `weft_monitor_task_messages` | child refs deleted, parent raw state reconciled only when raw rows gone | batch limit |
| `task_log.recover_orphan_terminal_rows` | terminal/disposed Monitor parent rows plus broker message search | raw broker rows for terminal TIDs with missing child refs or inconsistent raw state | orphan rows for selected TIDs reach zero, then policy reports no orphan work | capped TID count and per-TID message search limit |
| `summary.disposition` | Monitor parent rows at complete high-water | terminal/suspect families with summary requirements met | summary/disposition pending reaches zero | batch limit |
| `runtime.terminal_control_cleanup` | Monitor parent rows ready for terminal cleanup | terminal families with stale standard control/inbox queues | eligible control/inbox queues gone and parent marked | runtime cleanup family/worker/deadline limits |
| `runtime.reserved_cleanup` | standard `T*.reserved` queues plus Monitor proof | retention or failure policy says reserved rows are safe | eligible reserved queues gone | runtime cleanup family/worker/deadline limits |
| `runtime.dead_task_cleanup` | standard task-local queue names minus active TIDs | only TIDs with at least one queue eligible now | actionable stale queue count reaches zero; retention-deferred count is reported but not pending | runtime cleanup family/worker/deadline limits |
| `monitor_store.retire_families` | Monitor parent rows | raw deleted, summary emitted, disposition set, control cleanup done, no child refs | retirable family count reaches zero | batch limit |

Do not add a second policy runner. Keep runtime cleanup policy in
`weft/core/monitor/policies/runtime_control.py`, and keep `TaskMonitor` as the
reactor/worker launcher plus broker/store effects boundary. Tighten the
existing Monitor-store and runtime cleanup paths so these policies are
observable and independently testable.

## 7. Tasks

1. Add regression fixtures that reproduce the ops failure modes.
   - Outcome: the current code fails tests that describe the exact stalled
     states, before implementation starts.
   - Files to touch:
     - `tests/tasks/test_task_monitor.py`
     - `tests/core/test_monitor_store.py`
     - `tests/core/monitor/policies/test_dead_task.py`
   - Read first:
     - `weft/core/monitor/task_monitor.py::_ingest_retained_task_log_rows`
     - `weft/core/monitor/store.py::delete_task_messages_after_raw_delete`
     - `weft/core/monitor/policies/dead_task.py`
   - Required tests:
     - A retained task-log FIFO test where rows for the running monitor TID
       appear before later rows. The monitor must not checkpoint past the
       earlier rows unless those rows are ingested or exact-deleted.
     - A Monitor-store test with a terminal parent row marked raw-deleted, no
       child refs, and raw broker rows still present for the same TID. The
       recovery policy must find and delete those raw rows.
     - A dead-task selection test with many old TIDs whose only queues are
       retention-deferred `outbox` queues. They must not become actionable
       cleanup jobs and must not set the runtime cleanup pending flag.
     - A dead-task selection test with a mix of `ctrl_in`, `ctrl_out`, `inbox`,
       `outbox`, and `reserved` queues. It must select only queues eligible
       now and report retention-deferred queues separately.
   - Do not mock:
     - broker-backed queues
     - exact message deletion
     - Monitor-store SQL state
   - Stop if:
     - the only way to express a test is by patching private methods instead
       of seeding real queues/store rows. That means the seam is wrong.
   - Done when:
     - at least one new test fails on current code for each observed failure
       class.

2. Add or normalize per-policy decision accounting.
   - Outcome: every cleanup policy can explain selection, non-selection,
     convergence, and bounds in cached cycle stats.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `weft/core/monitor/store.py`
     - `weft/core/monitor/policies/dead_task.py`
     - `tests/tasks/test_task_monitor.py`
   - Required behavior:
     - Reuse existing result dataclasses where possible. Do not create a
       second cleanup runner or a generic framework that hides policy-specific
       decisions.
     - Each policy result must expose the fields named in the proof standard:
       source seen, selected, processed/deleted, deferred, blocker reason
       counts, pending, and stop reason.
     - Existing PONG fields may remain, but the richer policy stats must make
       it possible to answer "why did this policy not fire?" from cached data.
   - Selection proof:
     - Add tests for a policy with zero selected work and mixed blockers. The
       cached stats must identify each blocker instead of omitting the policy.
   - Convergence proof:
     - A test must show `pending` is false when only deferred future work
       remains.
   - Boundedness proof:
     - A test must show `pending` is true when a configured limit, scan limit,
       or worker deadline prevented processing remaining actionable work.
   - Stop if:
     - the accounting layer starts owning policy decisions. It should report
       decisions made by policies, not decide for them.

3. Fix retained task-log ingestion so checkpoint progress proves row handling.
   - Outcome: `_ingest_retained_task_log_rows()` cannot skip a visible raw row
     and later checkpoint beyond it.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
   - Required behavior:
     - Remove the silent `if update.tid == self.tid: continue` skip, or replace
       it with an explicit policy that ingests and deletes the row without
       emitting a recursive external summary. The final behavior must satisfy:
       every row counted toward checkpoint advancement is durably handled.
     - If the implementation keeps any row-skipping concept, skipped rows must
       become a stop reason and must prevent checkpoint advancement past the
       skipped row. Do not choose this unless a test proves it does not stall a
       live monitor on its own rows.
     - The PONG/cycle result should include a cached count such as
       `retained_task_log_ingest.skipped_unhandled_rows`, and this count should
       be zero in normal delete mode.
   - Selection proof:
     - A test asserts the set of checkpointed message IDs is a subset of
       `ingested_ids union deleted_malformed_ids union explicitly_deleted_ids`.
   - Convergence proof:
     - Seed a FIFO window containing service rows from old and current monitor
       TIDs. Run cycles until the window empties. The test must show the raw
       queue count decreases each cycle until zero or until a configured scan
       limit is hit, then continues on the next cycle.
   - Boundedness proof:
     - The cycle processes at most `batch_size` selected rows and scans at most
       `task_log_scan_limit`.
   - Stop if:
     - the fix starts special-casing service task TIDs in multiple places.
       Service suppression belongs in lifecycle reporting, not as a cleanup
       hole.

4. Correct `raw_deleted_at_ns` semantics.
   - Outcome: parent `raw_deleted_at_ns` means raw broker rows for that family
     are gone or have been proven absent, not merely that child refs were
     deleted.
   - Files to touch:
     - `weft/core/monitor/store.py`
     - `weft/core/monitor/sql.py`
     - `tests/core/test_monitor_store.py`
   - Required behavior:
     - Stop reconciling `raw_deleted_at_ns` from child-ref absence alone.
     - Keep physical deletion of child refs after exact raw delete succeeds.
     - Reconcile parent raw deletion only through a method whose name makes
       the proof clear, for example `mark_raw_deleted_for_tids_proven_absent`
       or `reconcile_raw_deleted_after_broker_absence_probe`.
     - If a new internal column is needed to preserve retry bookkeeping, add
       it additively and document it in [MF-5] and [OBS.13]. Do not overload
       `raw_deleted_at_ns` with two meanings.
   - Selection proof:
     - A unit/integration test must show a parent with no child refs but with
       raw broker rows is not considered raw-deleted.
   - Convergence proof:
     - After exact broker deletion of all known refs and a broker absence proof
       for the TID, the parent becomes raw-deleted and can retire once other
       gates are complete.
   - Boundedness proof:
     - Absence proof may be per selected TID, but selected TIDs must be capped
       by the existing batch/runtime cleanup limits.
   - Stop if:
     - the implementation wants to query or delete SimpleBroker message tables
       directly. Use public queue APIs and Monitor-owned SQL only.

5. Add an orphan raw task-log recovery policy.
   - Outcome: inconsistent historical Monitor-store rows no longer strand raw
     broker rows forever.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `weft/core/monitor/store.py`
     - `weft/core/monitor/sql.py`
     - `weft/core/monitor/policies/task_log.py` or a focused new policy module
     - `tests/tasks/test_task_monitor.py`
     - `tests/core/test_monitor_store.py`
   - Required behavior:
     - Select a bounded set of terminal, disposed, or summary-emitted TIDs
       whose Monitor-store state says there should be no raw work left, but
       whose raw broker rows may still exist.
     - For each selected TID, use the existing public broker search path
       (`find_message_ids` or the current wrapper around it) to find exact
       `weft.log.tasks` message IDs. Filter decoded rows by exact TID before
       deleting.
     - Delete exact raw rows through the same exact prune path used by retained
       task-log deletion.
     - Update the Monitor parent only after exact raw deletion or raw absence
       is proven.
     - Report separate cached stats for `tids_examined`, `raw_rows_found`,
       `raw_rows_deleted`, `tids_proven_absent`, `tids_still_pending`, and
       errors.
   - Selection proof:
     - A fixture with two TIDs must delete only rows whose decoded `tid` field
       exactly matches the selected TID. A row whose body merely contains the
       TID string in another field must survive.
   - Convergence proof:
     - Repeated cycles on the ops-shaped fixture must reduce raw rows for the
       selected terminal TIDs to zero, then the next cycle must report
       `raw_rows_found = 0` and `tids_still_pending = 0`.
   - Boundedness proof:
     - Each cycle may inspect only a capped number of TIDs and message IDs.
       The cap must come from existing monitor batch/scan/runtime limits unless
       a new constant is justified in `_constants.py`.
   - Stop if:
     - this starts replacing FIFO ingestion as the main path for ordinary rows.
       This is a recovery path for inconsistent or legacy Monitor-store state.

6. Make dead-task selection actionability-aware.
   - Outcome: dead-task cleanup jobs are created only for TIDs with at least
     one eligible queue deletion action now.
   - Files to touch:
     - `weft/core/monitor/policies/dead_task.py`
     - `weft/core/monitor/task_monitor.py`
     - `tests/core/monitor/policies/test_dead_task.py`
     - `tests/tasks/test_task_monitor.py`
   - Required behavior:
     - Derive historical TIDs from standard task-local queues and subtract
       live TIDs as today.
     - For each dead TID, call `dead_task_queue_cleanup_plan(...)`.
     - Intersect the plan's eligible queue names with the current queue-name
       snapshot.
     - Select the TID only if the intersection is non-empty.
     - Count retention-deferred TIDs separately when the only matching queues
       are `outbox` or `reserved` that are not old enough.
     - `dead_tids_pending` must reflect actionable dead-TID work only. It must
       not stay true only because retention-deferred queues exist.
   - Selection proof:
     - Tests must include active TIDs, too-young TIDs, retention-deferred
       outbox-only TIDs, stale control queues, stale inbox queues, and
       nonstandard/custom queues.
   - Convergence proof:
     - Repeated cleanup cycles over a mixed fixture must delete all actionable
       standard stale queues and then report no actionable dead-TID cleanup
       pending.
   - Boundedness proof:
     - Selection must remain bounded by the runtime cleanup family limit and
       worker deadline. If queue-name enumeration is large, the selector must
       still do only one queue-name snapshot per runtime cleanup slice.
   - Stop if:
     - the implementation re-parses queue names differently in
       `task_monitor.py` and runtime cleanup policy code. Runtime cleanup
       selection belongs in `weft/core/monitor/policies/runtime_control.py`,
       with lower-level standard queue-name helpers reused rather than
       reimplemented.

7. Stop dead-task log coalescing from runtime cleanup results.
   - Outcome: the expensive `find_message_ids` dead-task log coalesce path runs
     only in the bounded orphan recovery path, not from terminal, reserved, or
     dead-TID runtime cleanup slices.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `weft/core/monitor/policies/dead_task.py`
     - `tests/tasks/test_task_monitor.py`
   - Required behavior:
     - A dead-TID runtime cleanup job must not trigger
       `_coalesce_and_delete_dead_task_log_rows_for_tids()`.
     - A dead-TID job that only observes already-absent queues must be
       idempotent success for queue cleanup, but it must not imply raw-log work.
     - If task-log coalescing remains needed for recovery, route it through the
       orphan recovery policy from Task 5 with its own stats and caps.
   - Selection proof:
     - A test must prove `find_message_ids` is not called for retention-deferred
       or already-clean dead TIDs. This is one place a narrow spy is acceptable
       because the behavior under proof is "do not call the expensive broker
       search path".
   - Convergence proof:
     - With only retention-deferred dead TIDs left, repeated cycles must remain
       cheap and report no actionable cleanup pending.
   - Boundedness proof:
     - No runtime cleanup result may fan out into an unbounded per-TID raw-log
       search after the executor has already applied its family limit.

8. Tighten completed-family raw deletion and retirement.
   - Outcome: terminal/completed task families, including old Monitor service
     task families, are summarized/disposed and have raw task-log rows deleted
     without waiting for the general retention period.
   - Files to touch:
     - `weft/core/monitor/store.py`
     - `weft/core/monitor/sql.py`
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
   - Required behavior:
     - Terminal families at a completed FIFO high-water are eligible for
       summary/disposition and raw deletion even when the terminal event is
       newer than `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`.
     - Open families remain governed by retention and stale-open policy.
     - Parent retirement still requires raw deletion proof, summary emission,
       disposition, task-local control cleanup, and no child refs.
   - Selection proof:
     - A terminal Monitor-family fixture with thousands of `task_activity`
       rows and a terminal record must be selected for raw deletion once the
       FIFO high-water is complete.
   - Convergence proof:
     - The same fixture must shrink `weft.log.tasks` across cycles until that
       family has no raw rows and the parent row is retired or waiting only on
       a named remaining gate.
   - Boundedness proof:
     - The family may need multiple cycles, but each cycle must respect
       `batch_size`, `task_log_scan_limit`, and runtime cleanup limits.
   - Stop if:
     - terminal cleanup starts deleting `T{tid}.outbox` before the retention
       gate. This task is about lifecycle log rows and standard stale control
       queues, not result payload retention.

9. Add cached policy diagnostics to PONG.
   - Outcome: ops can tell which policy did no work and why without running
     live scans during PING.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `weft/core/monitor/runtime.py` only if a typed result model needs a new
       field
     - `tests/tasks/test_task_monitor.py`
   - Required cached fields:
     - retained FIFO rows scanned, selected, ingested, malformed-deleted,
       raw-deleted, checkpoint written, stop reason
     - tracked-ref deletion rows selected/deleted and parent raw-state marks
     - orphan recovery TIDs examined, rows found/deleted, proven-absent TIDs,
       pending TIDs, and stop reason
     - dead-task TIDs discovered, active skipped, too-young skipped,
       retention-deferred, actionable selected, queues deleted, pending
     - terminal-control cleanup families selected/processed and queues deleted
     - store family retirement selected/retired and blocked reason counts from
       cached worker results. If a blocker count would require a live PING-time
       scan, do not compute it during PING; report
       `not_computed_no_live_scan` in cached policy stats instead.
   - Selection proof:
     - Tests should inspect PONG after a cleanup cycle and verify zero-selected
       policies appear with concrete reasons, not as missing stats.
   - Convergence proof:
     - A "nothing actionable" fixture must show the policy stats settle to
       zero selected/pending without errors.
   - Boundedness proof:
     - PING tests must prove PONG does not query the Monitor store or scan
       queues. A narrow spy around store/queue open during PING is acceptable
       because PONG no-work is the contract.

10. Update specs and implementation notes.
   - Outcome: specs describe the corrected cleanup semantics before the work is
     considered done.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
   - Required updates:
     - Say plainly that `raw_deleted_at_ns` means raw broker absence has been
       proven, not just child-ref deletion.
     - Document the orphan recovery policy as a bounded recovery path for
       inconsistent Monitor-store state.
     - Document that dead-task selection is actionability-aware and
       retention-deferred queues are not runtime cleanup pending work.
     - Keep the public-status boundary: Monitor tables remain operational only.
   - Stop if:
     - spec text starts defining Monitor tables as public lifecycle or result
       authority.

11. Run an independent review after implementation and before final gates.
    - Outcome: a second pass checks that the code really proves selection
      correctness, convergence, and boundedness.
    - Reviewer prompt:
      > Read this plan and the implementation diff. Focus on TaskMonitor
      > cleanup selection, convergence, boundedness, and SimpleBroker layer
      > boundaries. Could this still stall on ops with terminal service
      > task-log rows, retention-deferred dead TIDs, or inconsistent
      > Monitor-store raw state? Identify concrete missing tests or unsafe
      > deletes. Do not implement fixes during review.
    - Required reviewer inputs:
      - `weft/core/monitor/task_monitor.py`
      - `weft/core/monitor/store.py`
      - `weft/core/monitor/sql.py`
      - `weft/core/monitor/policies/dead_task.py`
      - relevant tests added by this plan
      - [MF-5], [OBS.13], [OBS.16], [OBS.17]
    - The author must either fix each review finding or record why it is out
      of scope before claiming the implementation is complete.

## 8. Testing Plan

Use real broker-backed tests for queue, deletion, and store behavior. Prefer
`broker_env` and real `Queue` instances for focused tests, and
`WeftTestHarness` only when the behavior needs the CLI or real service
supervision. Do not mock SimpleBroker reservation, queue enumeration, exact
delete, task-log scan, checkpoint, or Monitor-store writes.

Required tests by invariant:

- Checkpoint proof:
  - Seed ordered `weft.log.tasks` rows where an old service/monitor row would
    previously be skipped.
  - Run one or more monitor cycles.
  - Assert every row at or before the stored checkpoint was ingested or
    explicitly deleted.
- Raw deletion proof:
  - Seed terminal parent rows, child refs, and raw broker rows.
  - Delete child refs after exact broker delete and assert parent raw-deleted
    state changes only when raw broker absence is proven.
  - Seed inconsistent legacy state with `raw_deleted_at_ns` present but raw
    broker rows still present. Assert orphan recovery clears rows and repairs
    parent state.
- Terminal family proof:
  - Seed a completed service task family with many activity rows and a terminal
    event.
  - Assert terminal raw rows are deleted over bounded cycles without waiting
    for general retention.
- Dead-task selection proof:
  - Use pure policy tests for queue-name parsing and eligibility.
  - Use integration tests for runtime cleanup pending flags and real queue
    deletion.
  - Include active TIDs, custom queues, retention-deferred outbox/reserved,
    stale ctrl/inbox, already-absent queues, and mixed fixtures.
- Boundedness proof:
  - Configure small `batch_size`, `task_log_scan_limit`, and runtime cleanup
    limits.
  - Assert no cycle processes more than the configured counts.
  - Assert repeated cycles continue progress rather than expanding work.
- PONG proof:
  - Assert PONG includes cached per-policy stats after a cleanup cycle.
  - Assert PONG itself does not open broker/store handles or recompute policy
    candidates.

Use red-green TDD where practical:

- Write the checkpoint orphan test before changing ingestion.
- Write the `raw_deleted_at_ns` semantic test before changing store
  reconciliation.
- Write the dead-task no-op pending test before changing selection.
- Write the orphan recovery fixture before adding the recovery policy.

If a test cannot be written red first, the implementer must explain in the
commit or PR notes why and provide the smallest equivalent proof. "It was hard
to mock" is not a valid reason; use real queues instead.

## 9. Verification And Gates

Per-task commands while implementing:

```bash
uv run pytest tests/core/monitor/policies/test_dead_task.py -q
uv run pytest tests/core/test_monitor_store.py -q
uv run pytest tests/tasks/test_task_monitor.py -q
uv run pytest tests/core/test_task_monitor_cleanup.py -q
```

Postgres parity for affected behavior:

```bash
uv run bin/pytest-pg tests/core/test_monitor_store.py -q
uv run bin/pytest-pg tests/tasks/test_task_monitor.py -q
```

Final gates before claiming done:

```bash
uv run pytest
uv run bin/pytest-pg
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft tests
uv run pytest tests/specs/test_plan_metadata.py -q
```

Operational success after deploy:

- `weft task ping <monitor_tid>` remains responsive. PONG shows cleanup worker
  state from cached data only.
- `weft.log.tasks` raw row count drops for terminal service families until the
  remaining rows are open/young or otherwise explained by cached policy stats.
- `store_deletable_refs`, orphan recovery pending, and terminal raw rows all
  converge to zero or to a named bounded remaining gate.
- Dead-task cleanup no longer reports persistent pending work when only
  retention-deferred outbox/reserved queues remain.
- Monitor worker CPU may be active during backlog recovery, but repeated
  "nothing actionable" cycles become cheap: no per-TID raw-log coalesce scan,
  no persistent SimpleBroker connection churn, and stable PING latency.

## 10. Rollout And Rollback

Rollout sequencing:

1. Land tests first, proving current failure modes.
2. Land ingestion/checkpoint and raw-deletion semantic fixes.
3. Land orphan raw-log recovery.
4. Land dead-task actionability and no-op coalesce suppression.
5. Land PONG diagnostics and spec updates.

Rollback:

- The implementation must stay backward-compatible with existing Monitor-store
  tables. Schema changes must be additive and idempotent.
- If the orphan recovery policy misbehaves, it should be possible to disable
  progress by selecting `report_only` or rolling back Weft without corrupting
  public task status/result data, because Monitor tables are operational only.
- Exact raw broker deletes are one-way. That one-way door is allowed only for
  rows selected by explicit policy predicates with real tests for near misses.
- Do not add a manual SQL cleanup requirement for ops. The monitor should
  self-heal old inconsistent state.

## 11. Out Of Scope

- Changing public task status/result reconstruction to use Monitor tables.
- Changing the default retention period.
- Adding a new external logging mode.
- Redesigning the monitor reactor/worker architecture.
- Replacing SimpleBroker queue APIs with direct broker message-table SQL.
- Deleting custom task-local queues, manager/global control queues, or user
  payload queues.
- Deleting `T{tid}.outbox` before its retention gate.
- Changing CLI shape or public TaskSpec schema.

## 12. Fresh-Eyes Review

Review pass completed while writing this plan:

- The original fix direction could have overused the dead-task coalesce path.
  The plan now separates ordinary retained FIFO ingestion, store-backed exact
  deletion, and orphan recovery. Dead-task cleanup is no longer the general
  task-log deletion authority.
- The plan now requires an explicit proof for `raw_deleted_at_ns`. Child-ref
  absence is not accepted as proof.
- The plan now treats retention-deferred dead-TID queues as non-actionable for
  pending/convergence purposes. They can be counted and reported, but they
  must not keep the runtime cleanup worker hot.
- The testing section now requires selection correctness, convergence,
  boundedness, idempotence, and observability proof for each changed policy.
- The plan now names concrete convergence measures and requires each policy to
  report source counts, selected counts, deferred work, blockers, stop reasons,
  and whether pending work is actionable now.
- The plan stays within the existing monitor structure. If implementation
  reveals that the current worker model cannot express these bounds, stop and
  write a separate architecture plan instead of expanding this one.

This plan should receive independent engineering review before implementation
because it changes destructive cleanup selection and Monitor-store semantics.
