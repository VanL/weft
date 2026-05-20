# Monitor Fair Cleanup Scheduling Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4], [SB-0.4a]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
Superseded by: none

## 1. Goal

Make TaskMonitor cleanup fair, bounded, and progress-visible after observing
ops behavior from the 0.9.51 deployment. The monitor currently stays PING
responsive, but one long task-local runtime cleanup worker can monopolize the
cleanup lane for tens of minutes. During that time `T*.ctrl_in` drops quickly,
but `weft.log.tasks`, `T*.reserved`, and later cleanup phases do not make
visible progress. This plan keeps the existing TaskMonitor structures and
public SimpleBroker APIs, but changes each cleanup pass into small resumable
slices that interleave task-log deletion, control queue deletion, and reserved
queue deletion.

Do not add new cleanup policy switches or env gates in this slice. Reuse the
existing TaskMonitor processor, catch-up scheduling, batch, scan, and runtime
cleanup settings.

## 2. Source Documents

- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]:
  `TaskMonitorTask` owns operational monitoring, retained lifecycle summaries,
  heartbeat/PING responsiveness, and monitor-owned cleanup.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4], [SB-0.4a]:
  Weft must use `WeftContext`, public SimpleBroker queue APIs, and the Monitor
  table as a Weft-owned derived table. Do not issue private queue SQL from
  Weft.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: task-log
  collation, exact deletion, runtime queue cleanup, PONG diagnostics, and
  cleanup ownership.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]: cleanup
  safety, operational evidence boundaries, exact deletion, and runtime-only
  queue behavior.
- `docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`:
  completed plan that added terminal task-log retirement and reserved cleanup
  to the existing monitor `delete` processor. This plan keeps that direction
  but fixes fairness and progress behavior observed after deployment.
- `docs/plans/2026-05-19-task-monitor-control-cleanup-worker-plan.md`:
  completed worker split. Keep the reactor responsive and keep destructive
  broker work off the reactor thread.
- `docs/plans/2026-05-19-task-monitor-bounded-control-cleanup-plan.md`:
  completed bounded task-local control cleanup. Reuse its safety model, but do
  not let a single worker slice monopolize the monitor.
- `../simplebroker/README.md` delete section and
  `../simplebroker/simplebroker/sbqueue.py`: SimpleBroker 3.7 provides public
  `Queue.delete()` and `Queue.delete_many(...)`. Use these, not private SQL.
- `../simplebroker/docs/plans/2026-05-20-delete-from-queues-plan.md`: future
  SimpleBroker set-based queue delete plan. Do not depend on it for this Weft
  slice unless the API is already released and present in the installed
  dependency.
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/hardening-plans.md`: required because this is
  destructive background cleanup.

Production evidence motivating this plan:

- 0.9.51 on ops dropped total queue rows from about `121,319` to `78,645` in
  about 25 minutes.
- Nearly all reduction came from `T*.ctrl_in`, which dropped from about
  `53,433` to `11,994`.
- `T*.ctrl_out` only dropped from about `22,335` to `20,916`.
- `T*.reserved` remained flat at about `6,334`.
- `weft.log.tasks` grew from about `18,106` to `18,254` while the monitor was
  otherwise making cleanup progress.
- `weft task ping <task-monitor-tid>` remained responsive with
  `activity=control_cleanup` and `control_cleanup_in_flight=True`.
- `py-spy` showed the hot thread in
  `weft/core/monitor/task_monitor.py::_delete_terminal_control_queues`, inside
  public SimpleBroker `Queue.delete()` / `Queue.stats()` / connection setup and
  cleanup. The manager was not hot.
- Direct MonitorStore inspection showed deletable task-log refs existed while
  PONG still reported `last_collation_messages_marked_deleted=0`. That must be
  tested and fixed, not explained away.

## 3. Context And Key Files

Read these before editing:

- `weft/core/monitor/task_monitor.py`
  - `process_once()`: reactor turn. It drains worker results, handles controls,
    and currently returns early while the control-cleanup worker is in flight.
  - `_run_monitor_cycle()`: one cleanup cycle. It calls Monitor-store collation,
    built-in cleanup, and processor result assembly.
  - `_run_monitor_store_cycle()`: ingests `weft.log.tasks`, emits summaries,
    deletes proven raw task-log rows, and starts runtime cleanup.
  - `_delete_monitor_store_task_log_rows()`: deletes exact raw `weft.log.tasks`
    rows using `apply_exact_prune_candidates`.
  - `_maybe_start_terminal_control_cleanup_worker()`,
    `_run_terminal_control_cleanup_worker()`, and
    `_run_terminal_control_cleanup_slice()`: current long runtime queue cleanup
    path.
  - `_delete_terminal_control_queues()` and
    `_delete_runtime_reserved_queues()`: current queue-wide delete actions.
  - `_task_monitor_pong_extension()` and `_control_snapshot_fields()`: cached
    PONG diagnostics.
- `weft/core/monitor/store.py`
  - `list_deletable_task_log_messages()`
  - `mark_messages_deleted()`
  - `list_terminal_control_cleanup_ready_tasks()`
  - `mark_task_controls_deleted()`
  - `mark_families_disposed()`
- `weft/core/monitor/sql.py`
  - Monitor table selectors only. Keep dynamic SQL here and use identifier
    helpers.
- `weft/core/pruning/apply.py`
  - `apply_exact_prune_candidates()` groups exact message deletes by queue and
    uses public `Queue.delete_many(...)`.
  - This helper currently cannot report per-row success if SimpleBroker returns
    a partial batch delete count. Tests must cover this before changing it.
- `tests/tasks/test_task_monitor.py`
  - Primary task-monitor behavior tests. Add real broker-backed tests here.
- `tests/core/test_monitor_store.py`, `tests/core/test_monitor_sql.py`, and
  `tests/core/test_pruning_*` if present:
  - Use these for store and exact-prune helper behavior.
- `tests/helpers/weft_harness.py`
  - Use this only for process-level CLI/manager tests. Do not use it when a
    direct `broker_env` queue test proves the behavior more simply.

Comprehension checks before editing:

1. In `process_once()`, why does a live `_control_cleanup_work_in_flight`
   prevent a new `_run_monitor_cycle()`?
2. Which function actually deletes raw `weft.log.tasks` rows after Monitor
   summary emission?
3. Which public SimpleBroker APIs are allowed for exact message deletion and
   whole-queue deletion?
4. Why must PONG use cached stats instead of scanning queues live?

## 4. Invariants And Boundaries

Preserve these invariants:

- No new cleanup policy gate, env switch, CLI flag, or alternate monitor mode
  in this slice.
- Keep `WEFT_TASK_MONITOR_PROCESSOR=delete` as the destructive cleanup switch.
- Keep `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT` as the operator cap for
  runtime queue cleanup. It may be interpreted as a per-slice maximum, but do
  not add a new env var for fairness.
- Keep `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS` as the catch-up scheduling
  mechanism when backlog remains.
- Keep PONG lightweight. It must only report cached stats from completed work.
  It must not query the Monitor store, call `list_queue_stats`, or scan queues.
- Use public SimpleBroker APIs:
  - `Queue.delete_many(...)` for exact raw message IDs.
  - `Queue.delete()` for whole task-local runtime queues.
  - `broker.list_queue_stats(...)` for queue totals and estimates.
- Do not use private SimpleBroker SQL from Weft.
- Do not change task execution, manager election, TaskSpec schema, queue names,
  or task result semantics.
- Do not delete `T*.inbox` or `T*.outbox` in this slice.
- Do not delete active runtime queues. Keep service-owner and live host-process
  guards.
- Runtime cleanup failure must not stop the manager, heartbeat, or task
  execution. It should surface in cached diagnostics and reschedule catch-up.

Hidden coupling to respect:

- The monitor reactor is the only path that handles PING/STATUS/STOP/KILL.
  Cleanup work can move to a worker, but cached state must only be committed
  by the reactor after a worker result returns.
- A long-running worker keeps the reactor responsive but blocks new monitor
  cycles. Fairness therefore depends on short worker slices, not live PONG
  scans.
- Raw task-log deletion currently happens before starting runtime cleanup in
  `_run_monitor_store_cycle()`. If runtime cleanup monopolizes future cycles,
  raw deletion cannot catch up.
- Exact deletion and Monitor table reconciliation are tied. Mark Monitor rows
  deleted only after broker deletion is proven or explicitly idempotent by
  tests.

## 5. Desired Design

The monitor cycle should become a fair cleanup scheduler using the existing
processor and worker structures:

1. Collate a bounded FIFO prefix of `weft.log.tasks`.
2. Emit summaries for ready terminal or stale families.
3. Delete a bounded slice of proven raw `weft.log.tasks` refs.
4. Start one short runtime cleanup worker slice.
5. The worker deletes a bounded slice of eligible control queues and reserved
   queues, then returns.
6. The reactor commits cached stats and schedules catch-up if any backlog
   remains.
7. Repeat via `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS` until backlog is
   drained.

Runtime cleanup slice bounds:

- Family count bound: use `min(WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT,
  internal_fairness_cap)`.
- Wall-clock bound: stop between families when a small monotonic-time budget is
  exceeded.
- The internal fairness cap and time budget must be constants in
  `weft/_constants.py`, not env vars. They are implementation safeguards, not
  policy switches.
- A single `Queue.delete()` call cannot be interrupted once started. That is
  acceptable. Check the wall clock before starting the next queue or family.

Fairness inside the worker:

- Do not let control cleanup consume the whole slice forever when reserved
  cleanup also has ready work.
- Use a small per-category budget inside the existing runtime cleanup worker:
  first control, then reserved, each bounded. The exact split can be simple:
  for example half the slice for control and half for reserved, with unused
  budget transferable after both categories get a chance.
- Keep this local. Do not create one thread per queue. Do not create a new
  queue scheduler abstraction.

SimpleBroker efficiency:

- Do not call `Queue.stats()` immediately before `Queue.delete()` when
  `broker.list_queue_stats(...)` already supplied the total row estimate.
- Prefer one `list_queue_stats(...)` call per runtime cleanup slice for
  estimates.
- Continue to call `Queue.delete()` for whole-queue delete unless an already
  released SimpleBroker public API provides safe set-based queue deletion.
- If a future `broker.delete_from_queues(...)` is available in the installed
  dependency, evaluate it behind a small adapter inside
  `weft/core/monitor/task_monitor.py`, but do not require it for this slice.

Raw task-log deletion:

- Raw deletion should run before each runtime cleanup worker slice.
- If `store.list_deletable_task_log_messages(...)` returns refs, a monitor
  cycle should either delete and mark some of them or expose a cached error
  explaining why not.
- Add a red test for mixed present/missing exact refs. If
  `apply_exact_prune_candidates()` cannot safely report partial results, fix
  the helper or add a monitor-specific exact deletion path that preserves
  idempotent missing-row behavior without private SQL.

## 6. Task Breakdown

### Task 1: Lock Down Current Fairness Failure With Tests

Files:

- `tests/tasks/test_task_monitor.py`
- `weft/core/monitor/task_monitor.py` only if test scaffolding needs a clock
  injection seam. Prefer existing monkeypatching over new architecture.

Write tests first.

Test 1: worker in flight must not make PONG timeout.

- Use real broker queues and `TaskMonitorTask`.
- Arrange a monitor with a synthetic in-flight runtime cleanup worker. If a
  real slow queue delete is too hard to force, monkeypatch only the worker
  submit function to hold a thread on an event; do not mock queue behavior for
  cleanup correctness.
- Send `PING`.
- Assert PONG returns from cached state with:
  - `status == "ok"`
  - `control_cleanup_in_flight is True`
  - no live queue scan in the PING path.

Test 2: one runtime cleanup slice must complete quickly and update cached
stats.

- Create several terminal/disposed Monitor records with `T{tid}.ctrl_in` and
  `T{tid}.ctrl_out` queues.
- Set `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT` higher than the internal
  fairness cap.
- Run one monitor cycle and drive until the worker result is committed.
- Assert only the slice-bounded number of families was processed.
- Assert `last_control_*` cached counters reflect that slice.
- Assert `last_catchup_pending is True` or equivalent schedule state indicates
  remaining work.

Stop and re-evaluate if the test needs to mock `Queue.delete()` to prove the
main behavior. The main proof must use real queue deletion.

### Task 2: Add Internal Runtime Cleanup Slice Bounds

Files:

- `weft/_constants.py`
- `weft/core/monitor/task_monitor.py`
- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`

Implementation requirements:

- Add internal constants in `weft/_constants.py`, for example:
  - `TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT`
  - `TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS`
- These are not env vars. Do not add them to `load_config()`.
- In `_run_terminal_control_cleanup_slice()`, compute:
  - configured cap from `self._monitor_config.control_queue_delete_limit`
  - effective cap as `min(configured_cap, internal_fairness_cap)`
  - deadline as `time.monotonic() + internal_seconds`
- Stop between families when either cap or deadline is reached.
- Return `pending=True` whenever:
  - more ready control records exist than the slice processed;
  - reserved cleanup had remaining ready work;
  - the deadline was reached;
  - errors occurred.
- Use the existing catch-up scheduling path. Do not add a new scheduler.

Testing:

- Red test from Task 1 should pass.
- Add a test where 3 eligible families exist and the internal cap is monkey-
  patched to 1. One cycle deletes one family and marks catch-up pending; a
  second cycle deletes another.
- Add a test where the wall-clock budget expires after one family. Use a
  monkeypatched monotonic function if needed. Do not sleep in tests.

Stop and re-evaluate if implementing the time budget requires passing clock
objects through many unrelated classes. Keep the clock seam local to
TaskMonitor.

### Task 3: Interleave Control And Reserved Runtime Cleanup

Files:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Implementation requirements:

- Keep one existing runtime cleanup worker lane. Do not add one thread per
  queue or one worker per policy.
- Within one runtime cleanup slice:
  - run a bounded control cleanup sub-slice;
  - run a bounded reserved cleanup sub-slice;
  - let each category get some budget when it has ready work.
- Preserve active runtime guards from `_active_runtime_tids()`.
- Preserve manager/custom queue exclusions from `_standard_task_control_queue_names()`.
- Use `broker.list_queue_stats(pattern="T*.reserved")` once per reserved
  sub-slice.
- For control queues, avoid per-queue `stats()` calls when a queue stats
  snapshot can provide row estimates. If that is not straightforward, at least
  remove `stats()` before `delete()` for zero-row estimates and document why.
- Keep cached PONG fields:
  - `last_control_families_processed`
  - `last_control_queues_deleted`
  - `last_control_rows_deleted`
  - `last_reserved_families_processed`
  - `last_reserved_queues_deleted`
  - `last_reserved_rows_deleted`
  - `last_control_cleanup_pending`

Testing:

- Real broker test with both eligible control queues and an eligible
  `T{tid}.reserved` queue.
- Set internal cap low enough that the first slice cannot drain everything.
- Assert after one committed worker result:
  - at least one control family was processed;
  - at least one reserved queue was processed if reserved work existed;
  - pending remains true when backlog remains.
- Add a guard test that an active service-owner reserved queue is still kept.
  This test already exists or should be extended, not duplicated.

Stop and re-evaluate if the code starts to need a generic policy runner. The
current need is a small fair runtime cleanup slice, not a new framework.

### Task 4: Make Raw Task-Log Deletion Progress Before Runtime Cleanup

Files:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/store.py` if selector limits need adjustment
- `tests/tasks/test_task_monitor.py`

Implementation requirements:

- `_run_monitor_store_cycle()` must attempt `_delete_monitor_store_task_log_rows()`
  before starting a runtime cleanup worker whenever:
  - processor is `delete`;
  - task-log owner is `collated_store`;
  - Monitor store has deletable refs.
- If runtime cleanup is pending from a previous slice, the next monitor cycle
  must still get a chance to delete a bounded raw task-log slice before
  launching another runtime worker slice.
- Do not scan queues from PONG. PONG should only show the last committed raw
  deletion count and errors.
- If no raw rows are deleted while refs exist, cache the exact error or reason
  in `last_collation_store_error`.

Testing:

- Create a terminal task-log family, run one cycle, and assert:
  - `summary_emitted_at_ns is not None`;
  - at least one raw message is deleted from `weft.log.tasks`;
  - `last_collation_messages_marked_deleted > 0`;
  - runtime cleanup pending does not prevent the raw deletion count from being
    visible after the cycle.
- Create many eligible control cleanup families plus one terminal task-log
  family. Assert one cycle still deletes raw task-log rows before or alongside
  starting runtime cleanup.

Stop and re-evaluate if this requires moving raw task-log deletion into the
runtime cleanup worker. Raw exact message deletion belongs with Monitor-store
collation, not task-local runtime queue cleanup.

### Task 5: Fix Exact Delete Partial-Result Handling If Needed

Files:

- `weft/core/pruning/apply.py`
- `tests/core/test_pruning_apply.py` or a new focused test file if none exists
- `tests/tasks/test_task_monitor.py`

Why this task exists:

- Ops showed `store.list_deletable_task_log_messages(...)` could return refs
  while PONG still showed `last_collation_messages_marked_deleted=0`.
- One plausible cause is partial `Queue.delete_many(...)` results. The helper
  currently cannot report which rows were deleted if SimpleBroker returns a
  partial count, so monitor reconciliation may make no progress.

Red test:

- Use a real broker queue.
- Write three messages.
- Build exact prune candidates for the three real message IDs plus one missing
  message ID in the same queue.
- Call `apply_exact_prune_candidates()`.
- Required behavior:
  - real existing messages are removed or clearly marked as deleted;
  - missing IDs are idempotent no-ops, not fatal cycle blockers;
  - no private SQL is used.

Implementation options, in order:

1. If SimpleBroker has a public API that returns per-ID delete results, use it.
2. If not, add an optional exact-safe mode to `apply_exact_prune_candidates()`
   that deletes IDs individually with `Queue.delete(message_id=...)` when the
   caller needs per-row accounting.
3. Use exact-safe mode only for Monitor raw task-log deletion if changing the
   generic helper would hurt existing bulk pruning behavior.

Do not mark Monitor messages deleted unless the broker delete was proven for
that exact ID or the test establishes missing IDs are safe to reconcile as
already gone.

Testing:

- Unit-level real broker test for the helper.
- TaskMonitor test where one Monitor message ref is stale/missing and another
  is present; the present row must still be deleted and marked.
- Existing task-monitor cleanup tests must still pass.

Stop and re-evaluate if the code starts treating all missing IDs as success
without proving that this cannot hide a queue-name mismatch.

### Task 6: Improve Cached Progress Diagnostics

Files:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`
- `docs/specifications/05-Message_Flow_and_State.md`

Implementation requirements:

- PONG must remain cached-only.
- Add or refine cached fields only if they are directly useful for ops:
  - runtime cleanup slice started/completed count;
  - runtime cleanup slice pending reason, bounded string enum;
  - raw task-log deletion attempts and deleted count;
  - raw task-log deletion error, if any;
  - runtime cleanup deadline hit boolean;
  - runtime cleanup family cap hit boolean.
- Do not include unbounded lists of TIDs or queue names in PONG.
- Do not make PONG query the Monitor table or call `list_queue_stats`.

Testing:

- Run a cycle with pending runtime cleanup and assert PONG reports the cached
  pending/deadline/cap state after the worker result is committed.
- Send a second PING without another cycle and assert values are unchanged.
- Use a spy only to prove PONG does not call live queue scans. Keep cleanup
  behavior itself on real queues.

Stop and re-evaluate if diagnostics become a second state machine. PONG should
report the scheduler, not drive it.

### Task 7: Update Specs And Plan Traceability

Files:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/README.md`

Required doc updates:

- State that TaskMonitor cleanup is fair-sliced:
  - raw task-log exact deletion;
  - task-local control queue deletion;
  - eligible reserved queue deletion.
- State that large runtime cleanup backlog must not prevent PING and must not
  indefinitely starve raw task-log deletion.
- State that `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT` is an operator cap,
  not a guarantee that one worker will process that many families in one
  uninterrupted run.
- Keep the no-new-policy-switch decision explicit.
- Add this plan to related plan sections where the touched specs list related
  plans.

Testing:

- `uv run pytest tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py -q`

## 7. Test Strategy

Use red-green TDD. Write the failing behavior test before each code change.

Primary tests:

- `uv run pytest tests/tasks/test_task_monitor.py -q`
- `uv run pytest tests/core/test_monitor_store.py tests/core/test_monitor_sql.py -q`
- Exact pruning helper tests, either existing or new:
  `uv run pytest tests/core/test_pruning_apply.py -q` if that file exists, or
  the new test file added in Task 5.

Regression tests to include:

- PING responds while runtime cleanup worker is in flight.
- Runtime cleanup worker processes at most one fair slice per committed result.
- Runtime cleanup records pending when backlog remains.
- Control and reserved cleanup both make progress when both are ready.
- Active reserved queues are not deleted.
- Raw task-log deletion makes progress even with runtime cleanup backlog.
- Exact delete partial/missing-ID behavior does not stall all present rows.
- PONG remains cached-only.

What not to mock:

- Do not mock `Queue.delete()` for the main deletion behavior tests.
- Do not mock `Queue.delete_many()` for exact raw deletion behavior tests.
- Do not mock MonitorStore for integration-level task-monitor tests.
- Do not mock service-owner liveness guards when testing active reserved queue
  protection.

What may be monkeypatched:

- `upsert_heartbeat`, to keep tests from depending on real heartbeat service
  timing.
- internal fairness constants, to force small slices.
- a local monotonic clock seam, to test deadline behavior without sleeps.
- worker submit/hold behavior only for PING responsiveness tests, not for
  cleanup correctness.

Full verification before completion:

- `uv run ruff check weft tests`
- `uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
- `uv run pytest tests/tasks/test_task_monitor.py tests/core/test_monitor_store.py tests/core/test_monitor_sql.py -q`
- `uv run pytest tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py -q`
- Run broader suites when feasible:
  - `uv run pytest`
  - `uv run bin/pytest-pg`

If full suites are not run, the final implementation report must say so
plainly and name the residual risk.

## 8. Deployment Observation Plan

After deploying the implementation, watch ops for at least 25 minutes.

Sample every 5 minutes:

- total queue count and total row count;
- grouped totals for:
  - `weft.log.tasks`
  - `T*.ctrl_in`
  - `T*.ctrl_out`
  - `T*.reserved`
  - `T*.inbox`
  - `T*.outbox`
  - `weft.state.tid_mappings`
  - `weft.state.services`
- manager, heartbeat, and task-monitor liveness;
- process CPU for manager, heartbeat, and task-monitor;
- TaskMonitor PONG fields:
  - `activity`
  - `control_cleanup_in_flight`
  - raw task-log rows marked deleted
  - control families/queues/rows deleted
  - reserved families/queues/rows deleted
  - pending/catch-up state
  - cached errors/warnings.

Expected healthy behavior:

- Manager remains low CPU.
- Heartbeat remains low CPU.
- TaskMonitor may be moderately CPU-active during cleanup, but should not stay
  pinned for one long worker slice.
- PING remains responsive.
- `T*.ctrl_in` and `T*.ctrl_out` both drop over time.
- `weft.log.tasks` drops when deletable refs exist, or PONG reports a clear
  raw-deletion error.
- `T*.reserved` drops when eligible stale reserved queues exist.
- Cached counters update every small slice, not only after tens of minutes.

Use `sudo /app/.local/bin/py-spy dump -p <pid>` if CPU is high for more than
one sample. The expected hot path after this change should be short bursts in
cleanup, not a long-lived worker stuck in queue delete/setup for an entire
monitoring window.

## 9. Rollback

Rollback is operationally simple because this plan must not add new persisted
contracts or new env switches.

- Set `WEFT_TASK_MONITOR_PROCESSOR=report_only` to stop destructive cleanup.
- Roll back to the previous Weft release if the monitor starts deleting active
  runtime queues, PING times out, or task execution is affected.
- Monitor table rows are additive operational state. Do not drop Monitor
  tables during rollback.
- Queue deletes are one-way. If a bad build deletes the wrong runtime queues,
  rollback stops further damage but cannot restore deleted runtime evidence.

## 10. Out Of Scope

- No new cleanup processor mode.
- No new env vars or policy switches.
- No manager election, heartbeat, TaskSpec, or task execution changes.
- No cleanup of `T*.inbox` or `T*.outbox`.
- No private SimpleBroker SQL from Weft.
- No dependency on unreleased SimpleBroker APIs.
- No broad TaskMonitor module reorganization.
- No new generic cleanup framework.

## 11. Stop And Re-Evaluate Conditions

Stop and revise the plan or implementation if:

- the fix wants a second TaskMonitor cleanup execution path;
- the code starts adding policy gates or env switches;
- tests need to mock away broker queue deletion to pass;
- raw task-log deletion still reports zero while store refs exist and no
  cached error explains why;
- the implementation would mark Monitor raw rows deleted without proving exact
  broker deletion or a tested idempotent missing-row contract;
- active service-owner or live host-process guards become weaker;
- PONG starts scanning queues live;
- the worker still runs for more than one catch-up interval under a testable
  backlog shape.

## 12. Fresh-Eyes Self-Review

Review completed after drafting.

Findings and fixes:

- Initial draft risked solving only runtime control cleanup fairness. Fixed by
  adding a separate raw task-log deletion task because ops showed deletable
  Monitor refs with zero raw rows marked deleted.
- Initial draft was too open-ended about SimpleBroker set-based queue delete.
  Fixed by making current public APIs the required path and treating future
  `delete_from_queues(...)` as optional only if already released.
- Initial draft mentioned new cleanup knobs. Fixed by requiring internal
  constants only, no env vars or policy switches.
- Initial draft could have led to one worker per policy. Fixed by requiring one
  existing runtime cleanup worker lane with fair sub-slices.
- Initial draft did not explicitly protect active reserved queues. Fixed by
  naming service-owner and live host-process guards as invariants and tests.
- Initial draft did not say what would prove success on ops. Fixed by adding a
  25-minute deployment observation plan with expected grouped queue movement.

External review:

- This is destructive background cleanup and should receive an independent
  engineering review before implementation. The review should focus on
  fairness, exact deletion accounting, and active runtime queue protection.
