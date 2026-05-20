# Monitor Reactor Worker Refactor Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4], [SB-0.4a]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
Superseded by: none

## 1. Goal

Refactor `TaskMonitorTask` so its main loop has the same shape as the
manager: a small reactor that owns control handling, heartbeat registration,
scheduling, cached state, and worker-result commits, with all potentially
long-running cleanup and Monitor-store work executed in bounded worker lanes.
The immediate production failure is not just queue cleanup throughput. It is
that `PING` and other control messages become unresponsive while the monitor
reactor performs store ingestion, exact deletion, and cleanup work
synchronously.

This plan replaces that execution shape. It also fixes the two known
amplifiers discovered during ops review: valid task-log ingestion is not
bounded by the configured batch size, and monitor-store raw deletion can fall
back to per-row `Queue.delete(...)` calls even when idempotent reconciliation
is sufficient.

## 2. Source Documents

- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]:
  `TaskMonitorTask` is a specialized task type and manager-supervised
  internal service. It must remain PING responsive and must expose cached
  operational diagnostics.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4], [SB-0.4a]:
  Weft must use `WeftContext`, public SimpleBroker queue APIs, and
  Weft-owned Monitor tables. The monitor may create and maintain its own
  table, but must not bypass SimpleBroker queue semantics with private queue
  SQL.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: task lifecycle
  evidence flows through `weft.log.tasks`, the Monitor-owned collation table,
  exact raw-message deletion, runtime queue cleanup, and cached PONG
  diagnostics.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]:
  Monitor cleanup is operational evidence cleanup, not audit retention.
  Destructive cleanup must remain bounded, observable, retry-safe, and based
  on public queue APIs.
- `docs/plans/2026-05-20-monitor-fair-cleanup-scheduling-plan.md`:
  completed predecessor plan. It improved fairness within the current cleanup
  model, but production showed the deeper issue remains: heavyweight monitor
  work can still run on the reactor and block PING.
- `docs/plans/2026-05-19-task-monitor-control-cleanup-worker-plan.md`:
  completed predecessor plan that moved terminal control cleanup to a worker.
  Reuse its ownership pattern, but apply it consistently to Monitor-store
  ingestion and exact raw task-log deletion.
- `docs/plans/2026-05-16-monitor-store-hardening-and-layering-plan.md`:
  completed Monitor-store layering plan. Keep monitor persistence inside
  `weft/core/monitor/` and keep SQL construction in the existing monitor SQL
  layer.
- `docs/plans/2026-05-15-task-reactor-and-evidence-worker-plan.md`:
  manager/task reactor precedent. Read this for the broader pattern, but use
  `weft/core/manager.py` and `weft/core/tasks/base.py` as the source code to
  copy from.
- `../simplebroker/simplebroker/sbqueue.py`: public `Queue.delete(...)` and
  `Queue.delete_many(...)` behavior.
- `../simplebroker/simplebroker/db.py` and `../simplebroker/simplebroker/_sql.py`:
  connection and SQL-construction patterns. Use these as reference only when
  editing Monitor-table SQL, not as permission to issue private queue SQL from
  Weft.
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/hardening-plans.md`: required because this
  plan changes background cleanup, deferred processing, destructive queue
  behavior, and runtime responsiveness.

Production evidence motivating this plan:

- During the 0.9.52 ops deploy, `weft.log.tasks` eventually dropped from
  about 23k rows to about 1.7k rows, but `TaskMonitorTask` PING timed out for
  multiple samples while cleanup ran.
- `py-spy` showed the task monitor main thread inside
  `_run_monitor_store_cycle()`, `_ingest_retained_task_log_rows()`,
  `MonitorStore.record_task_log_updates()`,
  `_delete_monitor_store_task_log_rows()`, and
  `apply_exact_prune_candidates()`.
- One hot sample showed per-row `Queue.delete(...)` connection/plugin setup
  under the exact-delete fallback path.
- Manager and heartbeat were mostly responsive. The pathological shape was
  localized to the task monitor.

## 3. Context And Key Files

Read these files before editing:

- `weft/core/manager.py`
  - `process_once()`: the reactor turn. It drains worker results, handles
    control and manager bookkeeping, schedules bounded work, then returns.
  - `_launch_child_task()`, `_submit_worker_call(...)`,
    `_run_child_launch_worker(...)`, and `_handle_child_launch_result(...)`:
    the concrete reactor/worker/result-commit model to copy.
  - `_drain_spawn_requests_from_queue(...)`: the queue-drain example. It
    limits work per turn and stops when the worker lane is occupied.
- `weft/core/tasks/base.py`
  - `_submit_worker_call(...)`, `_drain_worker_results()`,
    `_publish_worker_result(...)`, and `_handle_worker_result(...)`: the
    shared worker contract.
  - Important constraint: generic task workers are broker-free by default.
    If TaskMonitor workers own broker work, document that as a narrow
    TaskMonitor-owned lane instead of weakening the whole BaseTask contract.
- `weft/core/monitor/task_monitor.py`
  - `TaskMonitorTask.process_once()`: currently drains control and then can
    call `_run_monitor_cycle()` synchronously.
  - `_run_monitor_cycle()`: currently owns store work, custom processor
    dispatch, built-in cleanup bookkeeping, and result finalization.
  - `_run_monitor_store_cycle(...)`: currently does retained task-log ingest,
    summary emission, exact task-log deletion, and runtime cleanup scheduling.
  - `_run_task_monitor_cleanup_cycle(...)`: currently runs cleanup-policy
    scanning and deletion synchronously for built-in processors.
  - `_run_raw_external_task_log_cycle(...)`: currently emits/deletes raw
    task-log rows when raw external logging owns the task log.
  - `_ingest_retained_task_log_rows(...)`: currently limits malformed
    selected rows by `batch_size`, but valid updates can grow to the whole
    scanner window.
  - `_delete_monitor_store_task_log_rows(...)`: currently calls
    `apply_exact_prune_candidates(...)` and can retry with
    `exact_status=True`.
  - `_maybe_start_terminal_control_cleanup_worker(...)`,
    `_submit_terminal_control_cleanup_worker(...)`, and
    `_run_terminal_control_cleanup_worker(...)`: existing TaskMonitor worker
    pattern to reuse and generalize.
  - `_control_snapshot_fields()` and `_task_monitor_pong_extension()`:
    PONG must remain cached and cheap.
- `weft/core/monitor/store.py`
  - `record_task_log_updates(...)`, `list_deletable_task_log_messages(...)`,
    `mark_messages_deleted(...)`, `list_terminal_control_cleanup_ready_tasks(...)`,
    `mark_task_controls_deleted(...)`, and `mark_families_disposed(...)`.
  - Keep Monitor-table access behind this access class. Do not scatter table
    SQL across `task_monitor.py`.
- `weft/core/monitor/sql.py`
  - SQL construction for Monitor tables. Follow this module's local style and
    SimpleBroker's `_sql` construction patterns if SQL changes are needed.
- `weft/core/monitor/task_log_scanner.py`
  - `GeneratorTaskLogScanner.scan_window(...)`: use generator-backed queue
    reads. Do not replace this with ad hoc fixed SQL scans.
- `weft/core/pruning/apply.py`
  - `apply_exact_prune_candidates(...)`: grouped exact delete helper. This is
    the right layer for shared exact-delete semantics if we need a
    "reconcile missing IDs as complete" option.
- Tests:
  - `tests/tasks/test_task_monitor.py`: primary monitor service behavior,
    PING, worker, and schedule tests.
  - `tests/core/test_task_monitor_cleanup.py`: cleanup-policy and
    broker-backed cleanup tests.
  - `tests/core/test_monitor_store.py` and `tests/core/test_monitor_sql.py`:
    Monitor table and SQL behavior.
  - `tests/core/test_pruning*.py` if present: exact-prune helper tests.
  - `tests/helpers/weft_harness.py`: use only for CLI/process-level proofs.
    Prefer direct `broker_env` queues for monitor internals.

Files expected to change:

- `weft/_constants.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/store.py` only if result or selector support is missing
- `weft/core/monitor/sql.py` only if store support needs new SQL
- `weft/core/pruning/apply.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_task_monitor_cleanup.py`
- `tests/core/test_monitor_store.py` or `tests/core/test_monitor_sql.py`
- `tests/core/test_pruning*.py` if the exact-delete helper changes
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/04-SimpleBroker_Integration.md` only if Monitor-store
  or SimpleBroker API ownership wording changes
- `docs/specifications/00-Quick_Reference.md` only if PONG fields or config
  documentation changes
- `docs/plans/README.md`

Comprehension checks before editing:

1. In the manager, which method starts a worker and which method commits the
   worker result back to manager state?
2. In the current task monitor, which synchronous call path can prevent
   `_drain_queue()` from seeing a PING?
3. Why does a bounded cleanup loop still block PING if the bounded work runs
   inside `process_once()`?
4. Why is it safe for monitor-store raw deletion to reconcile missing
   `weft.log.tasks` message IDs as complete, but not safe to silently ignore a
   broker delete exception?
5. Which layer owns Monitor-table SQL? Which layer owns queue deletion?

## 4. Invariants And Boundaries

Preserve these invariants:

- The monitor reactor must remain responsive to `PING`, `STATUS`, `STOP`, and
  `KILL` while cleanup workers are active.
- `process_once()` must not perform work whose duration scales with
  `weft.log.tasks`, Monitor-table backlog, task-local runtime queue count, or
  SimpleBroker connection cleanup.
- PONG must remain lightweight. It may only report cached state already held
  by the task monitor. It must not scan queues, query the Monitor table, open
  an external log, or validate filesystem targets.
- Keep the existing destructive cleanup gate:
  `WEFT_TASK_MONITOR_PROCESSOR=delete`. Do not add new env gates or new
  policy switches for this refactor.
- Use public SimpleBroker APIs only:
  - `Queue.delete_many(...)` for exact message IDs.
  - `Queue.delete(...)` for whole runtime queues.
  - `broker.list_queue_stats(...)` for queue estimates.
- Do not issue private SQL against SimpleBroker queue tables from Weft.
- Monitor-table SQL remains in `weft/core/monitor/sql.py` and is accessed
  through `MonitorStore`.
- The manager remains the owner of service supervision. Do not move monitor
  scheduling into the manager.
- Do not change task execution semantics, TaskSpec schema, queue names, result
  delivery, manager election, or heartbeat public behavior.
- Do not add compatibility aliases or shims. This repo has no external task
  monitor specs.
- Do not create one worker per queue. Use a small number of named lanes.
- Do not create a general-purpose background job framework. Reuse the
  BaseTask worker-result pattern and keep TaskMonitor-specific authority
  local to `weft/core/monitor/task_monitor.py`.
- Worker threads must not mutate `_last_*`, `_monitor_store_status`, schedule
  fields, heartbeat fields, or TaskSpec state directly. Workers return result
  dataclasses; the reactor commits cached state.
- Worker threads must use fresh context/store/queue handles. Do not share
  reactor-owned queue objects across threads.
- If a worker fails after making a destructive effect, retry must be
  idempotent. Missing exact message IDs and already-deleted whole queues must
  be treated differently from broker exceptions.
- Runtime queue cleanup stays operational. It may delete stale
  `T*.ctrl_in`, `T*.ctrl_out`, and policy-approved `T*.reserved` queues, but
  this plan does not add deletion of `T*.inbox` or `T*.outbox`.

Fatal versus best-effort failures:

- Fatal for the worker result: corrupt Monitor-store schema, invalid
  Monitor-store write that prevents knowing what was ingested, or broker
  exceptions during required exact deletion.
- Best effort with cached diagnostics: external log emission problems,
  runtime queue cleanup failures for already-terminal families, and transient
  heartbeat registration errors.
- Never let auxiliary cleanup failure stop manager, heartbeat, or normal task
  execution.

Stop-and-re-evaluate gates:

- Stop if the implementation wants private SimpleBroker queue SQL.
- Stop if the implementation adds a second monitor cleanup mode or env gate.
- Stop if tests mock SimpleBroker queues for the main cleanup proof.
- Stop if worker code mutates monitor cached fields directly.
- Stop if the implementation needs a Monitor-table schema migration. This
  refactor should be execution-structure only.
- Stop if the work starts redesigning retention semantics. Retention policy is
  not the problem in this slice.

## 5. Desired Architecture

Target shape:

```text
TaskMonitorTask.process_once()
  drain finished worker results
  handle control queue messages
  ensure heartbeat registration
  if monitor disabled: publish cached disabled state and return
  if a worker lane is in flight: publish cached in-flight state and return
  if cycle due: start at most one bounded worker unit and return
  else: publish cached waiting state and return
```

The monitor should have explicit worker lanes:

- `task_monitor.builtin_cycle`: owns all built-in monitor cycle work that can
  scale with queue or Monitor-store backlog. This includes retained
  `weft.log.tasks` scan, Monitor table updates, summary/disposition, exact raw
  task-log deletion, raw external task-log emission/deletion, legacy cleanup
  policy scanning when Monitor collation is disabled, and the decision that
  follow-up runtime cleanup is needed.
- Existing custom processor lane: keep as-is for non-built-in processors.
- Existing runtime cleanup lane: owns terminal control and reserved queue
  deletion. This lane may run after the built-in cycle worker reports ready
  work.

The built-in cycle lane is the main change. It should be a bounded worker
unit, not a synchronous call from `process_once()`. Do not leave
`raw_external` or `cleanup_policy` as synchronous loopholes just because they
do not always use the Monitor store.

Worker result model:

- Add TaskMonitor-private dataclasses in `task_monitor.py`, for example:
  - `_TaskMonitorBuiltinCycleWork`
  - `_TaskMonitorBuiltinCycleWorkerResult`
  - `_TaskMonitorBuiltinCycleStats`
- The worker receives only serializable or thread-safe inputs: context root or
  resolved config, relevant numeric limits, task log owner, `now_ns`, and
  external log status needed to build the sink if applicable.
- The worker opens fresh `WeftContext`, `MonitorStore`, queues, and external
  log sink handles as needed.
- The worker returns stats, errors, warnings, checkpoint/status, and flags such
  as `runtime_cleanup_ready` and `catchup_pending`.
- The reactor applies the result to `_last_*`, `_monitor_store_status`,
  `_last_error`, `_next_cycle_due_monotonic`, and PONG-visible cached fields.

Bounded work rules:

- Ingestion must stop after `batch_size` selected rows total, where selected
  means either malformed/deletable row or valid row written to the Monitor
  table. The current code only limits `selected_rows`, which excludes valid
  updates. That is the known bug.
- `task_log_scan_limit` may remain a scan-window cap, but it must not imply
  unbounded writes in one worker unit.
- Raw task-log exact deletion must delete at most `batch_size` Monitor-store
  refs per worker unit unless an existing config already provides a smaller
  limit.
- Raw external task-log emission/deletion and cleanup-policy scanning must
  obey the same bounded-worker rule. They can reuse the existing built-in
  cleanup functions inside the worker; they must not run synchronously on the
  reactor.
- Runtime queue cleanup must remain a separate bounded worker lane. Do not
  fold runtime queue deletion into the built-in cycle worker.
- If backlog remains after a worker result, schedule catch-up using the
  existing catch-up interval.

Exact-delete reconciliation rule:

- For monitor-store raw task-log cleanup, if `Queue.delete_many(...)`
  completes without exception, any requested IDs that were not deleted in that
  call are already absent or unavailable to this cleanup pass. The monitor may
  reconcile those IDs as complete in the Monitor table.
- Do not retry with `exact_status=True` for this monitor-store path. That
  fallback causes per-row connection churn and blocks progress under backlog.
- Keep strict per-row status available for callers that truly need it. Add an
  explicit option such as `reconcile_missing=True` to
  `apply_exact_prune_candidates(...)`, or create a narrowly named helper for
  monitor-store reconciliation. Do not silently change every caller's
  semantics.
- Broker exceptions are not reconciliation. If `delete_many(...)` raises,
  return errors and leave those Monitor-store refs unmarked so retry can occur.

## 6. Task Breakdown

### Task 1: Lock Down Reactor Responsiveness With A Red Test

Outcome: prove the current bad shape before refactoring. A PING must be
answered while a built-in monitor cycle worker is in flight.

Files to touch:

- `tests/tasks/test_task_monitor.py`

Read first:

- `weft/core/monitor/task_monitor.py::process_once`
- Existing PING and worker tests in `tests/tasks/test_task_monitor.py`
- `weft/core/tasks/base.py::_submit_worker_call`

Test design:

- Use `broker_env` and a real `TaskMonitorTask`. Do not mock `Queue`.
- Arrange a monitor with enabled cleanup and a blocking built-in cycle worker
  seam. The seam may monkeypatch only the worker function or worker submit
  callback so the thread waits on a `threading.Event`.
- Start a due monitor cycle by calling `process_once()`.
- Write a real `PING` control message to the monitor inbox.
- Call `process_once()` again while the worker is still blocked.
- Assert the monitor writes a PONG to `ctrl_out` using cached state.
- Assert the PONG includes an in-flight field for the built-in worker, such as
  `monitor_builtin_cycle_in_flight: true`.
- Release the worker and drive `process_once()` until the worker result is
  committed.

Constraints:

- Do not assert private thread object identity.
- Do not sleep for correctness. Use events and short polling helpers.
- Do not use `WeftTestHarness` unless the direct task instance cannot prove
  the control behavior.

Done when:

- The new test fails on the current synchronous built-in cycle path or fails
  until the in-flight PONG behavior exists.

Stop if:

- The only possible test is a broad mock of the whole monitor. That means the
  seam is wrong. Add a narrow worker-hook seam instead.

### Task 2: Add A Built-In Cycle Worker Lane And In-Flight State

Outcome: `TaskMonitorTask.process_once()` can start built-in monitor work and
return immediately, like the manager starts child-launch work and returns.

Files to touch:

- `weft/_constants.py`
- `weft/core/monitor/task_monitor.py`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`

Implementation:

- Add a named lane constant in `_constants.py`, for example
  `TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE = "task_monitor.builtin_cycle"`.
- Add TaskMonitor-private dataclasses in `task_monitor.py`:
  - `_TaskMonitorBuiltinCycleWork`
  - `_TaskMonitorBuiltinCycleWorkerResult`
  - `_TaskMonitorBuiltinCycleStats`
- Add `self._builtin_cycle_work_in_flight`.
- Add `_maybe_start_builtin_cycle_worker(...)`.
- Add `_submit_builtin_cycle_worker(...)`.
- Add `_run_builtin_cycle_worker(...)`.
- Add `_handle_builtin_cycle_worker_result(...)`.
- Extend `_handle_worker_result(...)` if needed, following the existing
  terminal-control cleanup worker result pattern.
- Update `process_once()` so it:
  - drains worker results first;
  - drains controls before scheduling work;
  - if any worker lane is in flight, sets cached activity and returns;
  - starts at most one due built-in cycle worker and returns;
  - never calls `_run_monitor_store_cycle(...)`,
    `_run_task_monitor_cleanup_cycle(...)`, or
    `_run_raw_external_task_log_cycle(...)` synchronously.

Reuse:

- Follow `weft/core/manager.py::_launch_child_task` and
  `_handle_child_launch_result` for shape.
- Follow the existing terminal control cleanup worker code for local naming
  and cached PONG fields.

Constraints:

- Worker code must not mutate `self._last_*` directly.
- Worker code must use fresh context/store/queue handles.
- Do not put external log sink objects in the worker dataclass if they are not
  thread-safe. Pass config needed to construct a sink inside the worker.
- Do not move runtime control/reserved cleanup into the built-in worker.

Tests:

- Complete the Task 1 PING test.
- Add a single-flight test: when `_builtin_cycle_work_in_flight` is set,
  another due cycle must not start another built-in worker.

Done when:

- PING is answered during built-in monitor work.
- A due monitor cycle starts a worker and returns without doing built-in
  monitor work on the reactor.

Stop if:

- The implementation needs to alter BaseTask worker semantics for all tasks.
  Keep the broker-owning worker authority local and documented for
  TaskMonitor.

### Task 3: Extract Built-In Cycle Core Into A Worker-Safe Function

Outcome: existing built-in monitor behavior is preserved, but it runs in the
built-in worker and returns a result object for the reactor to commit.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/store.py` only if a missing store method prevents a
  clean worker result
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_store.py` only if store methods change

Implementation:

- Refactor the built-in portions of `_run_monitor_cycle(...)` into a
  worker-safe core. Accept explicit inputs and return
  `_TaskMonitorBuiltinCycleWorkerResult`.
- The core should perform the current sequence for every built-in mode:
  1. ensure/open Monitor store;
  2. ingest retained `weft.log.tasks`;
  3. if the FIFO high-water is complete, emit summaries/dispositions;
  4. if processor is `delete` and task-log owner is `collated_store`, delete
     raw task-log refs;
  5. if task-log owner is `raw_external`, emit/delete raw external task-log
     rows through the existing raw path;
  6. if task-log owner is `cleanup_policy`, run the existing cleanup-policy
     scan in the worker;
  7. report whether runtime cleanup should be scheduled.
- Keep the current operational meaning of stats:
  - rows scanned;
  - valid rows ingested;
  - malformed rows deleted;
  - tasks updated;
  - terminal tasks seen;
  - summaries emitted;
- raw message refs reconciled;
  - raw external rows emitted/deleted when that mode is active;
  - cleanup-policy rows scanned/selected/deleted when that mode is active;
  - errors and warnings.
- The worker result should include `MonitorStoreStatus` fields needed for
  PONG, including schema version and checkpoint.
- The reactor commit method should update `_last_*` fields in one place.

Constraints:

- Do not leave a synchronous `_run_monitor_store_cycle(...)`,
  `_run_task_monitor_cleanup_cycle(...)`, or
  `_run_raw_external_task_log_cycle(...)` path callable from `process_once()`.
- Do not duplicate built-in cycle logic between reactor and worker.
- Do not broaden exception catches beyond the existing monitor boundary
  categories. If a new catch is needed, explain why it is a task boundary
  catch.

Tests:

- Existing Monitor-store, raw external, and cleanup-policy behavior tests must
  still pass.
- Add a worker-result commit test that verifies cached PONG fields reflect the
  worker result after commit and not before.
- Add an error-result test where the worker returns a store error and the
  reactor records cached error state without stopping the monitor.

Done when:

- There is one built-in cycle core path.
- The reactor only schedules and commits.

Stop if:

- You find you need to copy most of `_run_monitor_store_cycle(...)`,
  `_run_task_monitor_cleanup_cycle(...)`, or
  `_run_raw_external_task_log_cycle(...)` into a second method. Extract shared
  core instead.

### Task 4: Fix The Ingestion Batch Bound

Outcome: one built-in worker unit cannot write or delete an unbounded
number of valid task-log rows just because they are well-formed.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py` or `tests/core/test_task_monitor_cleanup.py`

Implementation:

- In the retained task-log ingest loop, count selected rows as:
  - malformed rows selected for deletion;
  - valid rows selected for `MonitorStore.record_task_log_updates(...)`;
  - rows that parse but are treated as malformed by the current policy.
- Stop when selected row count reaches `batch_size`.
- Preserve `task_log_scan_limit` as a maximum scan-window guard.
- Preserve checkpoint semantics:
  - write checkpoint only after store writes and deletes succeed;
  - checkpoint should advance to the last selected message ID, not the last
    scanned-but-unprocessed message ID.
- Preserve `completed_fifo_high_water` semantics. It should be true only when
  the scan reaches the high water with no errors and no batch/scan stop.

Testing:

- Use a real `weft.log.tasks` queue and real Monitor store.
- Insert more valid task-log rows than `batch_size`.
- Run one built-in worker/core unit through the retained-log path.
- Assert:
  - no more than `batch_size` valid rows are written to the Monitor table;
  - checkpoint advances only to the last processed row;
  - `completed_fifo_high_water` is false;
  - cached stats expose the batch stop reason.
- Add a mixed malformed-plus-valid case. Total selected rows, not just
  malformed rows, must respect `batch_size`.

Done when:

- The test fails before the fix and passes after.

Stop if:

- The implementation wants to use SQL `LIMIT` directly against the queue
  backend. The scanner path must stay generator-based.

### Task 5: Make Monitor-Store Exact Deletion Idempotent Without Per-Row Fallback

Outcome: monitor-store raw task-log deletion uses `Queue.delete_many(...)` and
reconciles missing IDs safely, without falling back to per-row
`Queue.delete(...)` under normal partial-delete conditions.

Files to touch:

- `weft/core/pruning/apply.py`
- `weft/core/monitor/task_monitor.py`
- `tests/core/test_task_monitor_cleanup.py`
- `tests/core/test_pruning*.py` if present, or add a focused test file near
  existing pruning tests

Implementation:

- Add an explicit option to `apply_exact_prune_candidates(...)`, for example
  `reconcile_missing: bool = False`.
- Default remains strict current behavior for other callers.
- When `reconcile_missing=True` and `delete_many(...)` returns without
  exception:
  - report every non-report-only candidate as applied with `error=None`;
  - include actual deleted count in an optional stats field only if the caller
    has a place for it. Do not force all result types to change just for this.
- When `delete_many(...)` raises, report errors and do not reconcile.
- Update `_delete_monitor_store_task_log_rows(...)` to call the helper with
  `reconcile_missing=True` and remove the `exact_status=True` retry.
- Mark Monitor-store message refs deleted only for reconciled IDs.

Testing:

- With a real queue, create three message IDs, delete one before calling the
  helper, then call helper with all three IDs and `reconcile_missing=True`.
  Assert all three are reconciled and no errors are returned.
- Add a monitor-store test:
  - create Monitor-store refs for one present `weft.log.tasks` row and one
    already-missing row;
  - call the monitor raw-delete path;
  - assert the present row is gone and both Monitor-store message refs are
    marked deleted.
- Add a strict-mode test, if not already covered, showing default behavior has
  not silently changed.

What not to mock:

- Do not mock SimpleBroker queue deletion for the success path. Use real queue
  rows.
- It is acceptable to monkeypatch `Queue.delete_many(...)` to raise for the
  error-path test, because broker exceptions are hard to force portably.

Done when:

- The monitor raw-delete path never calls exact per-row fallback for partial
  batch deletes.

Stop if:

- You cannot express the new semantics without changing every pruning caller.
  Add a separate monitor-specific helper instead.

### Task 6: Preserve And Report Runtime Cleanup As A Separate Lane

Outcome: terminal control and reserved queue cleanup remain workerized,
bounded, and scheduled after built-in cycle results, but no longer block the
reactor path.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_task_monitor_cleanup.py`

Implementation:

- Keep the existing terminal control cleanup worker lane.
- Start runtime cleanup only from the reactor after a built-in cycle worker result
  says the high-water/disposition state is ready.
- If runtime cleanup is already in flight, do not start another runtime cleanup
  worker. The built-in worker may still be allowed to run in a later turn
  only if the design explicitly proves the two workers do not contend on the
  same Monitor rows. The conservative first implementation should allow at
  most one cleanup-affecting monitor worker at a time.
- Keep existing reserved cleanup policy and safety checks.
- Keep `T*.ctrl_in`, `T*.ctrl_out`, and eligible `T*.reserved` cleanup
  counters cached.
- Keep PONG fields for:
  - `monitor_builtin_cycle_in_flight`;
  - `control_cleanup_in_flight`;
  - last built-in cycle stats;
  - last runtime cleanup stats;
  - catch-up pending.

Testing:

- Existing terminal-control cleanup tests must still pass.
- Add a scheduling test:
  - built-in cycle worker returns `runtime_cleanup_ready=True`;
  - reactor starts runtime cleanup on a later turn;
  - PING is still answered while runtime cleanup is in flight.
- Add a no-overlap test:
  - with runtime cleanup in flight, due cycle should not start a second
    cleanup-affecting worker unless a later implementation explicitly proves
    safe parallelism.

Done when:

- Heavy runtime cleanup cannot block PING, and built-in cycle work cannot start
  conflicting runtime cleanup directly from a worker thread.

Stop if:

- The implementation wants to run store ingestion and runtime queue deletion
  concurrently against the same Monitor table without a proof. Keep it
  single-flight first.

### Task 7: Keep Scheduling Deterministic And Catch-Up Explicit

Outcome: the monitor repeats bounded work until backlog is drained without
spinning hot and without hiding pending work.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Implementation:

- Worker results should include a `catchup_pending` or equivalent flag when:
  - ingest hit `batch_size`;
  - ingest hit `task_log_scan_limit`;
  - raw deletion had more refs remaining;
  - summary/disposition had more ready families;
  - runtime cleanup had pending work;
  - errors require retry.
- The reactor should use the existing catch-up interval to schedule the next
  cycle when pending work exists.
- Avoid a zero-delay tight loop. If ops needs faster catch-up, use existing
  config, not a new hidden busy loop.
- `next_wait_timeout()` should continue to account for worker result
  notifications and scheduled cycles.

Testing:

- Add a test where more rows exist than one worker unit can process. Assert
  first result sets pending and schedules catch-up.
- Add a test where no backlog remains. Assert next cycle is scheduled at the
  normal interval, not catch-up interval.
- Add a PONG test verifying schedule fields are cached and cheap.

Done when:

- A bounded worker unit makes progress, reports whether more work remains, and
  does not spin hot when idle.

Stop if:

- You need a new scheduling subsystem. Reuse the current monitor schedule
  fields.

### Task 8: Update Specs And Implementation Notes

Outcome: specs describe the new ownership boundary clearly enough that future
changes do not put heavyweight work back on the reactor.

Files to touch:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/04-SimpleBroker_Integration.md` only if wording about
  Monitor-store or public SimpleBroker APIs needs tightening
- `docs/specifications/00-Quick_Reference.md` only if PONG fields or constants
  are documented there

Required spec updates:

- State that `TaskMonitorTask.process_once()` is a reactor and must not do
  unbounded Monitor-store, queue scan, or delete work.
- State that retained task-log ingestion, summary/disposition, and raw
  deletion run through a bounded TaskMonitor worker lane.
- State that raw external task-log emission/deletion and cleanup-policy
  scanning also run through the bounded built-in worker lane.
- State that runtime queue cleanup remains a separate bounded worker lane.
- State that PONG reports cached worker state and never performs live
  diagnostic scans.
- Add or update plan backlinks to this plan in the touched specs'
  `Related Plans` sections.

Done when:

- Spec implementation notes and code docstrings point to the same owner
  functions and worker lanes.

Stop if:

- The spec update starts changing retention semantics. That is out of scope.

### Task 9: Clean Up Dead Or Superseded Monitor Paths

Outcome: one live execution path remains for built-in monitor cleanup.

Files to inspect and possibly touch:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/__init__.py`
- `tests/core/test_task_monitor_cleanup.py`
- `tests/tasks/test_task_monitor.py`

Implementation:

- Remove or simplify dead synchronous helper paths after the worker core is
  live.
- Keep reusable policy/data helpers in their existing module if still used.
- Do not leave a compatibility shim for the old monitor task path.
- Do not delete `weft/core/monitor/cleanup.py` unless `rg` proves no live code
  imports it and the tests confirm the behavior moved. If it still owns policy
  helpers, keep it and update references.

Testing:

- Run `rg "_run_monitor_store_cycle|_run_task_monitor_cleanup_cycle|_run_raw_external_task_log_cycle|_run_builtin_cycle_worker|cleanup.py|apply_exact_prune_candidates" weft tests`.
- Confirm the only built-in monitor path is reactor -> worker -> result
  commit.

Done when:

- There is no callable synchronous built-in cleanup path from `process_once()`.

Stop if:

- Removing a helper requires rewriting policy semantics. Keep the helper and
  rename/document it instead.

## 7. Testing Plan

Use real broker-backed queues for all queue semantics. Mock only narrow
boundaries that are external, slow, or hard to force deterministically, such
as a worker thread block or a simulated broker exception.

Required regression tests:

- PING during built-in cycle worker in flight.
- PING during runtime cleanup worker in flight.
- Single-flight behavior for built-in cycle worker.
- Single-flight behavior for runtime cleanup worker.
- Valid retained task-log ingestion respects `batch_size`.
- Mixed valid and malformed retained task-log ingestion respects total
  selected-row `batch_size`.
- Raw external mode does not run queue scan, external emission, or exact
  deletion on the reactor.
- Cleanup-policy mode does not run queue scan or deletion on the reactor.
- Monitor-store raw deletion reconciles mixed present/missing message IDs
  without exact per-row fallback.
- Monitor-store raw deletion leaves refs unmarked when `delete_many(...)`
  raises.
- Worker result commits cached PONG fields only on the reactor.
- Catch-up scheduling is set when a worker unit leaves backlog.
- Idle scheduling uses the normal interval when backlog is drained.

Recommended test commands while implementing:

```bash
uv run pytest tests/tasks/test_task_monitor.py -q
uv run pytest tests/core/test_task_monitor_cleanup.py -q
uv run pytest tests/core/test_monitor_store.py tests/core/test_monitor_sql.py -q
uv run pytest tests/specs/test_plan_metadata.py -q
```

Postgres-specific gates for this change:

```bash
uv run bin/pytest-pg tests/tasks/test_task_monitor.py -q
uv run bin/pytest-pg tests/core/test_task_monitor_cleanup.py -q
```

Final gates before claiming implementation done:

```bash
uv run pytest
uv run bin/pytest-pg
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft tests
```

What not to mock:

- Do not mock `Queue.delete_many(...)` for successful exact deletion.
- Do not mock `Queue.delete(...)` for successful runtime queue deletion.
- Do not mock Monitor-store writes for the main collation proof.
- Do not mock PING/PONG queue flow. Use real monitor control queues.

Acceptable mocks or monkeypatches:

- A worker function that waits on `threading.Event` to prove reactor
  responsiveness.
- A clock function local to `TaskMonitorTask` if a no-sleep scheduling test
  needs deterministic time.
- A broker method raising an exception for an error-path test only.

## 8. Rollout And Rollback

Rollout:

- Ship as one Weft release after local SQLite and Postgres gates pass.
- No database schema migration is expected.
- No queue-name, TaskSpec, CLI, or public result format change is expected.
- Existing running monitors will be replaced by the normal deploy/restart
  flow.

Post-deploy observation:

- `weft task ping <task-monitor-tid>` should answer while cleanup is active.
- PONG should show built-in-cycle or runtime-cleanup in-flight status from cached
  fields.
- `weft.log.tasks` should decline or remain bounded without PING timeouts.
- `T*.ctrl_in`, `T*.ctrl_out`, and eligible `T*.reserved` counts should
  decline over repeated cycles.
- TaskMonitor CPU may be elevated while backlog drains, but the manager and
  heartbeat should remain low and responsive.
- If CPU stays high while PONG reports no in-flight work and no pending
  backlog, that is a regression.

Rollback:

- Roll back to the previous Weft release if the new monitor cannot start or
  breaks normal task execution.
- Because this plan should not change Monitor-table schema, rollback should
  not require table migration.
- If raw task-log refs were marked deleted by the new idempotent path, old code
  may not revisit them. That is acceptable because the underlying raw queue
  rows were either deleted or already absent.
- If rollback leaves cleanup slower, ops can keep Weft stable and revisit
  performance. Do not manually mutate queues as part of rollback unless an
  explicit remediation runbook authorizes it.

One-way doors:

- Destructive queue deletion is already part of the monitor `delete`
  processor. This plan does not introduce a new destructive class.
- The exact-delete reconciliation semantic is a one-way decision for
  Monitor-store message refs. The test suite must prove it only reconciles
  missing IDs after `delete_many(...)` succeeds without exception.

## 9. Independent Review Loop

This plan is risky enough to require independent review before implementation.
Use a reviewer who did not author the plan, preferably a different agent
family when available.

Review prompt:

> Read
> `docs/plans/2026-05-20-monitor-reactor-worker-refactor-plan.md` and the
> current code in `weft/core/monitor/task_monitor.py`,
> `weft/core/tasks/base.py`, `weft/core/manager.py`,
> `weft/core/monitor/store.py`, and `weft/core/pruning/apply.py`. Do not
> implement. Look for errors, bad ideas, missing tests, unclear ownership,
> unsafe cleanup semantics, and ways a zero-context engineer could implement
> the plan incorrectly. Answer whether you could implement it confidently and
> what must be fixed first.

The plan author must either update the plan for each valid finding or record
why the finding is out of scope.

## 10. Out Of Scope

- No new monitor cleanup policy gates or env switches.
- No retention-policy redesign.
- No deletion of `T*.inbox` or `T*.outbox`.
- No manager service-reconciler changes.
- No SimpleBroker private queue SQL from Weft.
- No Monitor-table schema migration unless implementation discovers a hard
  blocker, in which case stop and write a new plan.
- No generalized thread pool or background job framework.
- No external logging behavior change.
- No attempt to solve all SimpleBroker compaction behavior in Weft.

## 11. Fresh-Eyes Review

Fresh-eyes pass 1 found three latent ambiguities:

- The first draft could be read as allowing concurrent built-in cycle and runtime
  cleanup workers. That is too risky because both touch Monitor-store state.
  The plan now says the conservative first implementation should allow at most
  one cleanup-affecting monitor worker at a time unless safe parallelism is
  explicitly proven.
- The exact-delete fix could have silently changed all pruning callers. The
  plan now requires an explicit `reconcile_missing` option or a
  monitor-specific helper, with strict default behavior preserved.
- The ingestion batch fix originally said "limit selected rows" without
  defining selected. The plan now defines selected as malformed/deletable rows
  plus valid rows written to the Monitor table.
- A later self-review found that the first draft only workerized the
  Monitor-store path, leaving `raw_external` and `cleanup_policy` as possible
  synchronous loopholes. The plan now makes the lane a built-in cycle worker
  and explicitly includes those paths.

Fresh-eyes pass 2 checked for drift from the requested direction. The plan
still targets the discussed architecture: the monitor should work like the
manager, with a thin reactor and bounded workers. It does not turn into a
retention redesign, a new policy system, or a SimpleBroker replacement.

Fresh-eyes pass 3 checked whether a zero-context engineer could implement this
without guessing. The plan now names the exact files, source functions, tests,
public APIs, no-mock boundaries, stop gates, rollout signals, and rollback
expectations. It is implementation-ready after independent review.
