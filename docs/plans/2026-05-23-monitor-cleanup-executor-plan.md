# Monitor Cleanup Executor Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
Superseded by: none

## 1. Goal

Move TaskMonitor runtime cleanup from effectively one cleanup category at a
time to a bounded mixed cleanup executor. The executor should allow up to three
cleanup jobs in flight, let terminal-control, reserved, and dead-TID cleanup
all make progress in the same runtime cleanup slice, and keep every broker and
Monitor-store effect off the TaskMonitor reactor thread.

This plan is a follow-up to the completed dead-task cleanup and fair-cleanup
plans. It does not change queue eligibility policy. It changes how already
selected cleanup work is dispatched and bounded.

## 2. Source Documents

Read these before editing code:

- `AGENTS.md`: repo rules, style, and the requirement to keep plans in
  `docs/plans/`.
- `docs/agent-context/README.md`,
  `docs/agent-context/decision-hierarchy.md`,
  `docs/agent-context/principles.md`, and
  `docs/agent-context/engineering-principles.md`: agent context and local
  engineering standards.
- `docs/agent-context/runbooks/writing-plans.md`: plan quality bar.
- `docs/agent-context/runbooks/hardening-plans.md`: mandatory because this
  slice changes destructive cleanup and background worker scheduling.
- `docs/agent-context/runbooks/testing-patterns.md`: test harness selection
  and what not to mock.
- `docs/specifications/01-Core_Components.md` [CC-2.3]: `TaskMonitor` is a
  specialized task over `BaseTask`; the reactor handles controls and worker
  results.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: TaskMonitor
  cleanup, retained task-log deletion, runtime queue cleanup, PONG diagnostics,
  and cleanup ownership.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17]: cleanup safety, runtime-state pruning boundaries, retention
  pruning boundaries, and exact deletion requirements.
- `docs/plans/2026-05-22-monitor-policy-modules-and-dead-task-cleanup-plan.md`:
  completed policy-module and dead-task cleanup plan. Keep its policy
  decisions intact.
- `docs/plans/2026-05-20-monitor-fair-cleanup-scheduling-plan.md`: completed
  fair slicing plan. This plan strengthens its runtime worker model.
- `docs/plans/2026-05-20-monitor-reactor-worker-refactor-plan.md`: completed
  reactor/worker split. Preserve the reactor boundary.
- `docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`:
  completed terminal runtime cleanup plan. Reuse its safety model.
- SimpleBroker 3.8.3 docs/source for public APIs used here. Check the installed
  package or sibling repo for `list_queues`, `delete_from_queues`,
  `find_message_ids`, and any public exact-message retrieval API before
  touching dead-task task-log coalescing.

The motivating ops evidence is that `control_cleanup_pending` and
`reserved_probe_pending` can drain while `weft.log.tasks` keeps growing and
Postgres remains hot on queue scans. The current code can still spend a
runtime cleanup slice on one cleanup class, and the dead-TID path is explicitly
limited by `TASK_MONITOR_DEAD_TASK_CLEANUP_WORKERS = 1`. We need a capped
mixed executor, not independent unbounded workers.

## 3. Current Code Map

Files to read first:

- `weft/core/monitor/task_monitor.py`
  - `process_once()` is the reactor turn. It must stay responsive to
    PING/STATUS/STOP/KILL and must not run broker/store cleanup work.
  - `_maybe_start_builtin_cycle_worker()` and `_run_builtin_cycle_worker()`
    run retained task-log ingestion and built-in cleanup in a worker lane.
  - `_maybe_start_terminal_control_cleanup_worker()` and
    `_run_terminal_control_cleanup_worker()` start the runtime cleanup worker.
  - `_run_terminal_control_cleanup_slice()` currently selects terminal
    control, reserved, and dead-TID work, then processes them mostly in a
    fixed sequence.
  - `_run_dead_task_control_cleanup_workers()` already has a local
    `queue.Queue[str]` and worker-count constant, but the constant is `1` and
    only dead-TID work uses it.
  - `_queue_name_snapshot()` uses public broker `list_queues(...)`; this must
    remain a worker-only operation.
  - `_handle_builtin_cycle_worker_result()` and
    `_handle_control_cleanup_worker_result()` are reactor-side result commits.
    They may update cached stats, but they must not delete queues, scan queue
    names, query the Monitor store, or coalesce task-log rows.
- `weft/core/monitor/policies/dead_task.py`
  - Owns standard task-local queue identity parsing.
  - Owns dead-TID queue eligibility:
    `ctrl_in`, `ctrl_out`, and `inbox` are stale immediately after the TID is
    proven dead; `outbox` and `reserved` are retention-gated.
  - Owns the task-log coalesce lookup helper. Keep this policy module as the
    policy source. Do not reimplement its queue naming rules in
    `task_monitor.py`.
- `weft/core/monitor/policies/runtime_control.py`,
  `weft/core/monitor/policies/reserved.py`, and
  `weft/core/monitor/policies/types.py`
  - Existing cleanup policy package. Reuse it instead of adding policy logic
    to a new executor module.
- `weft/core/monitor/store.py`
  - Owns Monitor-store tables, ready-task selectors, mark methods, and
    retirement helpers.
  - Treat store writes as serialized until a deliberate follow-up proves
    thread safety.
- `weft/core/monitor/runtime.py`
  - Owns `TaskMonitorRuntimeConfig`. Add a cleanup worker cap here only if the
    implementation chooses an operator-tunable cap.
- `weft/_constants.py`
  - Owns constants and env parsing. Any new worker cap constant or env parser
    belongs here.
- `tests/tasks/test_task_monitor.py`
  - Primary runtime cleanup tests. Use real broker-backed queues and a real
    `MonitorStore` here.
- `tests/core/monitor/policies/test_dead_task.py`
  - Policy-only tests for dead-task queue selection.
- `tests/core/test_task_monitor_cleanup.py`
  - Built-in cleanup policy runner tests.

Files likely to add:

- `weft/core/monitor/cleanup_executor.py`
  - Pure executor/scheduler helpers only. This module may define job/result
    dataclasses and a small bounded runner. It must not know queue naming
    policy.
- `tests/core/monitor/test_cleanup_executor.py`
  - Pure scheduler tests that do not need a broker.

Comprehension checks before editing:

1. Which functions run on the TaskMonitor reactor thread, and which functions
   run on worker threads?
2. Why is `T{tid}.outbox` retention-gated for dead TIDs while
   `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and `T{tid}.inbox` are not?
3. Which current code path coalesces and exact-deletes `weft.log.tasks` rows
   for a dead TID?
4. Where are PONG cleanup stats cached, and why must PONG not call
   `list_queues(...)` or open the Monitor store?
5. Which public SimpleBroker APIs are allowed for queue-name discovery,
   whole-queue deletion, and exact message deletion?

## 4. Non-Negotiable Invariants

- The TaskMonitor reactor is the reactor. It schedules workers, drains worker
  results, handles controls, and commits cached stats. It does not scan queue
  names, delete queues, query the Monitor store, coalesce task-log rows, or
  call broker delete APIs.
- Durable cleanup work always happens in a worker thread. If code in
  `_handle_*_worker_result(...)` starts doing broker/store work, stop.
- Cleanup policy belongs in `weft/core/monitor/policies/`. The executor may
  schedule jobs selected by policy, but it must not duplicate task-local queue
  naming, retention, live-TID, or dead-TID eligibility rules.
- Do not create one permanent worker per cleanup type. The design is one
  bounded executor with a total cleanup worker cap.
- The initial cap is three cleanup workers total for a runtime cleanup epoch.
  Count the existing TaskMonitor cleanup service-lane thread as one cleanup
  worker. The service-lane worker may run one job inline and start at most two
  helper threads, or it may use an equivalent implementation that never exceeds
  three cleanup worker threads for the epoch.
- If an operator-tunable cap is added, default it to `3`, reject values below
  `1`, and clamp or reject values above `3`. Setting it to `1` is the rollback
  path to serial cleanup. This is an execution safety knob, not a new cleanup
  policy.
- `task_log_store` work is serialized in this slice. Do not run multiple
  Monitor-store writers at once. Do not run two dead-task task-log coalesce
  jobs concurrently.
- `queue_namespace` discovery is at most one shared queue-name snapshot per
  runtime cleanup epoch. Workers receive the immutable snapshot. They do not
  each call `list_queues(...)`.
- `queue_namespace` discovery must also have a cadence. If the last runtime
  cleanup result found no queue-discovered work and no errors, do not take a
  fresh task-local queue snapshot again on every catch-up tick. Snapshot again
  when store-selected work requires runtime cleanup, when the normal monitor
  interval is due, or when the previous runtime cleanup result reported
  pending queue-discovered work.
- `task_queue_delete` jobs may run concurrently up to the worker cap. They
  must use public SimpleBroker whole-queue delete APIs, normally
  `broker.delete_from_queues(...)`.
- Retained `weft.log.tasks` deletion remains exact-message-ID deletion through
  `weft/core/pruning/apply.py` and Monitor-store reconciliation helpers.
- Do not use direct queue-table SQL from Weft production code. Direct SQL is
  acceptable only for ops investigation and isolated test setup where the test
  says why the production API is not the subject under test.
- Keep PONG/status cached. New executor stats may be exposed only after a
  worker result returns.
- Missing queues are success for cleanup. A delete attempt against
  `T{tid}.ctrl_in` and `T{tid}.ctrl_out` should be idempotent even if one or
  both queues are already absent.
- Live TIDs and manager/service owners stay protected. If a live-TID guard
  becomes expensive, optimize the shared snapshot; do not remove the guard.
- Cleanup failure is best-effort operational failure. It should set cached
  errors and reschedule catch-up; it must not stop Manager, Heartbeat, or task
  execution.
- `report_only` remains non-destructive.

## 5. Desired Executor Model

Use a small executor inside the existing TaskMonitor runtime cleanup worker.
Do not make a new service process. Do not move cleanup into Manager.

The runtime cleanup worker should execute one cleanup epoch:

1. Open worker-local `MonitorStore` and broker handles as needed.
2. Select cleanup candidates using existing policy modules and store selectors.
3. Decide whether task-local queue discovery is due. If no store-selected
   runtime work exists and queue discovery is not due, return a no-op result
   without calling `list_queues(...)`.
4. Build exactly one task-local queue-name snapshot for the epoch when
   discovery is due or store-selected runtime work needs queue names.
5. Convert selected work into typed cleanup jobs.
6. Run mixed queue-delete jobs with a total cap of three cleanup worker threads
   for the epoch.
7. Aggregate queue-delete results in the worker, not the reactor.
8. Serialize Monitor-store marks, task-log coalescing, exact task-log deletion,
   and family retirement in the same worker after queue-delete jobs complete.
9. Return one result object to the reactor.
10. Let the reactor commit cached counters and schedule the next wake.

The executor should understand resource tags, not queue policy:

| Resource tag | May run concurrently? | Notes |
| --- | --- | --- |
| `task_queue_delete` | Yes, up to worker cap | Whole-queue deletes for terminal control, reserved, dead-TID stale queues, and retention-gated dead-TID outbox/reserved queues. |
| `queue_namespace` | No repeated concurrent jobs | One shared snapshot per epoch. Prefer doing this before dispatch rather than as a queued job. |
| `monitor_store_write` | No | Store marks and family retirement stay serialized in the worker coordinator. |
| `task_log_store` | No | Dead-TID coalesce, summary emission, and exact task-log delete stay serialized. |

The simplest acceptable implementation is:

- Add `cleanup_executor.py` with a small pure scheduler that can run a list of
  callable jobs under a worker cap and return ordered results.
- In `_run_terminal_control_cleanup_slice()`, keep candidate selection in the
  worker, build a mixed job list, run it through the executor, then perform
  serialized store/log follow-up in that same worker.
- Keep `_handle_control_cleanup_worker_result()` as a cached-stat commit only.

Do not build a generic task system, plugin registry, retry framework,
dependency graph, or async event loop. This is a local bounded executor for
TaskMonitor cleanup.

## 6. Work Selection And Fairness Rules

The executor must not pick only one cleanup type while other cleanup types are
ready.

For each runtime cleanup epoch:

- Compute `control_limit` as today:
  `min(config.control_queue_delete_limit, TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT)`.
- Keep the existing soft deadline:
  `TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS`.
- Build candidate buckets:
  - terminal control cleanup records from
    `store.list_terminal_control_cleanup_ready_tasks(...)`
  - reserved queue cleanup candidates selected by the existing reserved policy
  - dead TIDs selected by `select_dead_task_tids_from_queue_names(...)`
- If more than one bucket has work and the limit is at least the number of
  non-empty buckets, schedule at least one job from each non-empty bucket.
- Fill the remaining budget oldest-first by TID across candidate buckets.
  Preserve current oldest-first behavior within dead-TID cleanup.
- Do not let retention-only outbox/reserved work permanently starve stale
  `ctrl_in`, `ctrl_out`, or `inbox` cleanup for other dead TIDs.
- If a job is not started because the family limit or deadline was hit, count
  it as pending and schedule catch-up.
- A single broker delete call does not need to be interruptible. Check the
  deadline before starting the next job.

Dead-TID queue cleanup remains per TID:

- For each dead TID, use `dead_task_queue_cleanup_plan(...)`.
- Delete `ctrl_in`, `ctrl_out`, and `inbox` immediately.
- Include `outbox` and `reserved` only when retention-eligible.
- Mark control cleanup for the TID only after the queue delete job succeeds.
- Coalesce and exact-delete `weft.log.tasks` rows for successful dead TIDs in
  the serialized task-log/store phase of the same worker epoch.

Terminal-control cleanup remains per Monitor-store record:

- Delete only standard task-local control queues for the record.
- Skip nonstandard/custom control queue records.
- Mark `task_control_deleted_at_ns` and family disposition only after the
  delete job succeeds or the queue absence is proven idempotent.

Reserved cleanup may batch queue names:

- Keep batching if it reduces broker overhead and stays inside the family
  limit.
- If batching makes fairness hard, split reserved cleanup into small batches
  but do not create one thread per queue.

## 7. Bite-Sized Implementation Tasks

### Task 1: Establish Baseline And Red Tests

Files:

- `tests/tasks/test_task_monitor.py`
- `tests/core/monitor/test_cleanup_executor.py` (new)
- `tests/core/monitor/policies/test_dead_task.py`

Actions:

1. Run the current focused baseline:

   ```bash
   . ./.envrc
   ./.venv/bin/python -m pytest tests/core/monitor/policies/test_dead_task.py -q
   ./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
   ./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
   ```

2. Add a pure failing scheduler test that proves a mixed ready set starts jobs
   from more than one cleanup kind when `max_workers=3`.
3. Add a pure failing scheduler test that proves the executor never exceeds
   the configured worker cap.
4. Add a task-monitor regression test using `broker_env` and real queues:
   arrange terminal-control, reserved, and dead-TID cleanup candidates, run one
   runtime cleanup slice with a limit of at least three, and assert all three
   cleanup kinds make progress in the same slice.

Test design rules:

- Do not mock `simplebroker.Queue`.
- It is acceptable to monkeypatch `broker.delete_from_queues(...)` or a narrow
  job callable to block on a `threading.Event` when the test is specifically
  about concurrency limits.
- Assert queue-visible effects with real queues where cleanup semantics are
  under test.
- Assert scheduler behavior with pure callables where no broker behavior is
  being tested.

Stop gate:

- If the first red tests need a broad fake TaskMonitor, stop and narrow the
  seam. The plan is to test the scheduler purely and cleanup behavior with
  real broker-backed queues, not to build a mock monitor.

### Task 2: Add A Local Cleanup Executor Module

Files:

- `weft/core/monitor/cleanup_executor.py` (new)
- `tests/core/monitor/test_cleanup_executor.py` (new)

Implement:

- A frozen `CleanupJob` dataclass with:
  - `kind: Literal["terminal_control", "reserved", "dead_tid"]`
  - `identity: str`
  - `resource: Literal["task_queue_delete"]`
  - `run: Callable[[], CleanupJobResult]`
- A frozen `CleanupJobResult` dataclass with:
  - the job identity and kind
  - success flag
  - counter fields needed by `_TaskControlCleanupResult`
  - warnings and errors
  - optional follow-up marks, such as successful dead TIDs and successful
    terminal-control TIDs
- A bounded runner function, for example:

  ```python
  def run_cleanup_jobs(
      jobs: Sequence[CleanupJob],
      *,
      max_workers: int,
      deadline_reached: Callable[[], bool],
  ) -> CleanupExecutorResult:
      ...
  ```

Rules:

- Keep this module policy-free. It must not import
  `weft.core.monitor.policies.dead_task`.
- Keep the module broker-free. It runs callables; it does not open broker
  handles itself.
- Count the calling cleanup worker as one worker. With `max_workers=3`, the
  implementation may run one job inline plus at most two helper threads.
- Preserve deterministic result aggregation by returning results sorted by the
  original job index, not by completion timing.
- Use `queue.Queue[CleanupJob]` or a small `ThreadPoolExecutor`; if using
  `ThreadPoolExecutor`, account for the existing service-lane thread so the
  total cleanup worker count still stays at three.
- Do not add retries here. Retry is catch-up via the next monitor cycle.

Tests:

- Mixed job kinds are executed in one call.
- Maximum observed concurrent jobs never exceeds the cap.
- Results are deterministic even when jobs finish out of order.
- Deadline reached before job start leaves unstarted jobs pending.
- Job exceptions become result errors and do not kill the executor thread.

### Task 3: Add A Runtime Cleanup Worker Cap

Files:

- `weft/_constants.py`
- `weft/core/monitor/runtime.py`
- `tests/core/test_task_monitoring.py` or the existing config tests that cover
  `load_config(...)` and `TaskMonitorRuntimeConfig.from_config(...)`

Preferred implementation:

- Replace `TASK_MONITOR_DEAD_TASK_CLEANUP_WORKERS` with a broader
  `WEFT_TASK_MONITOR_CLEANUP_WORKERS_DEFAULT = 3`.
- Add config key `WEFT_TASK_MONITOR_CLEANUP_WORKERS`.
- Add `cleanup_workers: int` to `TaskMonitorRuntimeConfig`.
- Validate `1 <= cleanup_workers <= 3`.
- Use `cleanup_workers=1` as the serial rollback mode.

If adding a new env key becomes a broad config/docs change, use an internal
constant `TASK_MONITOR_CLEANUP_WORKERS: Final[int] = 3` for this slice and
record that rollback requires code revert. The env-config approach is better
for ops safety, but do not let config plumbing consume the whole change.

Tests:

- Default config yields `cleanup_workers == 3`.
- Explicit `WEFT_TASK_MONITOR_CLEANUP_WORKERS=1` yields serial mode.
- `0` and `4` fail with a clear `ValueError`.
- Existing monitor config tests still pass.

Stop gate:

- Do not create multiple knobs such as per-kind worker counts. That is how the
  implementation drifts into a scheduler framework.

### Task 4: Convert Runtime Cleanup Selection Into Mixed Jobs

Files:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/dead_task.py` only if a missing policy helper is
  genuinely needed
- `tests/tasks/test_task_monitor.py`

Actions:

1. Keep `_run_terminal_control_cleanup_slice()` as the worker-owned cleanup
   epoch.
2. Keep one queue-name snapshot for all task-local suffixes:
   `inbox`, `outbox`, `ctrl_in`, `ctrl_out`, and `reserved`.
3. Add a queue-discovery due check before `_queue_name_snapshot()`. A runtime
   cleanup slice may skip queue discovery only when there is no store-selected
   runtime work, no prior queue-discovered pending work, and no normal-interval
   discovery due.
4. Keep one active-TID snapshot for the epoch.
5. Change `_delete_dead_task_control_queues(...)` or its replacement job
   callable to accept the active-TID snapshot. Do not call
   `_active_runtime_tids()` once per TID.
6. Build candidate buckets for terminal-control records, reserved candidates,
   and dead-TID candidates.
7. Select a mixed job list using the fairness rules in section 6.
8. Run the job list through `run_cleanup_jobs(...)`.
9. Aggregate job counters into `_TaskControlCleanupResult`.
10. Keep all Monitor-store marks and dead-TID task-log coalescing serialized in
   `_run_terminal_control_cleanup_slice()` after queue-delete jobs return.

Rules:

- Do not call `_queue_name_snapshot()` from inside individual job callables.
- Do not call `_active_runtime_tids()` once per TID.
- Do not move `dead_task_queue_cleanup_plan(...)` logic into
  `task_monitor.py`.
- Do not mutate `self._last_*` fields in helper job threads. They return
  result objects only.
- Do not put `MonitorStore` handles into helper job threads. The coordinator
  worker owns store access.

Tests:

- Existing dead-task cleanup tests still pass.
- Existing terminal-control and reserved cleanup tests still pass.
- New mixed-slice test proves terminal-control, reserved, and dead-TID cleanup
  can all make progress in one slice when all three are ready and the limit is
  at least three.
- A cap test blocks delete calls and asserts no more than the configured number
  of cleanup jobs are running.
- A deadline test proves unstarted jobs are counted pending and catch-up is
  scheduled.
- A snapshot-cadence test proves `_queue_name_snapshot()` is not called on a
  catch-up tick after a no-work queue-discovery result unless store-selected
  runtime work or pending queue-discovered work makes discovery due again.

### Task 5: Serialize Dead-TID Task-Log Coalescing And Exact Delete

Files:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/dead_task.py`
- `tests/tasks/test_task_monitor.py`

Actions:

1. Keep `_coalesce_and_delete_dead_task_log_rows_for_tids(...)` in the cleanup
   worker coordinator after successful dead-TID queue deletes.
2. Confirm the coalesce path uses the released SimpleBroker API for finding
   matching `weft.log.tasks` rows. If SimpleBroker 3.8.3 exposes a public
   exact message fetch API, use it through `policies/dead_task.py` and remove
   any new need for private broker access.
3. Keep exact row deletion through `apply_exact_prune_candidates(...)` and
   `store.delete_task_messages_after_raw_delete(...)`.
4. Keep summary emission and family disposition serialized with Monitor-store
   writes.

Tests:

- Existing `test_task_monitor_dead_task_cleanup_coalesces_and_deletes_task_log_refs`
  must still prove the coalesce API is called and the matching
  `weft.log.tasks` row is deleted.
- Add a test with two dead TIDs and `cleanup_workers=3`: queue deletes may
  overlap, but task-log coalescing must be observed as serialized. Use a narrow
  monkeypatch around the coalesce helper to record entry/exit order; do not
  mock broker queue semantics.
- Add or preserve a test where one dead-TID coalesce fails. The queue delete
  counters should report the successful delete, the error should be cached,
  and catch-up should remain pending.

Stop gate:

- If implementing this requires direct SQL against `weft.messages`, stop and
  inspect the SimpleBroker 3.8.3 API. This project boundary matters.

### Task 6: Keep PONG And Result Commit Cached

Files:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Actions:

1. Add cached executor stats to `_TaskControlCleanupResult` only if they help
   ops answer whether the worker cap is doing what was intended. Useful fields:
   - `cleanup_workers_configured`
   - `cleanup_jobs_started`
   - `cleanup_jobs_completed`
   - `cleanup_jobs_by_kind`
   - `cleanup_jobs_pending`
2. Commit those stats in `_handle_control_cleanup_worker_result(...)`.
3. Expose them through the existing PONG extension and cycle log from cached
   fields only.
4. Ensure PONG can respond while cleanup jobs are blocked.

Tests:

- Existing PONG responsiveness test remains.
- Add a regression test that monkeypatches `_queue_name_snapshot()` to raise if
  called during PING. Start a cleanup worker, send PING, and assert PONG uses
  cached stats without queue-name scans.
- Add a test that cached worker-cap stats appear after a cleanup worker result.

Stop gate:

- If a PONG implementation wants to compute live counts, stop. PONG is not a
  diagnostics scan endpoint.

### Task 7: Update Specs And Docs With The Landed Shape

Files:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/2026-05-23-monitor-cleanup-executor-plan.md`
- `docs/plans/README.md`
- `docs/lessons.md` only if the implementation exposes a repeated durable
  lesson

Actions:

- Add backlinks from touched specs to this plan.
- After implementation, update [MF-5] and [OBS.13] text if the actual landed
  worker model differs from current spec wording.
- When the slice lands and tests pass, change this plan status from `draft` to
  `completed` and update `docs/plans/README.md` status.

Do not mark this plan completed when only the plan exists.

## 8. Test And Verification Gates

Run the smallest relevant tests after each task, then expand:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/monitor/test_cleanup_executor.py -q
./.venv/bin/python -m pytest tests/core/monitor/policies/test_dead_task.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/ruff check weft tests/core/monitor tests/tasks/test_task_monitor.py
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

Behavioral invariants to assert:

- With `cleanup_workers=3`, at most three cleanup worker threads are active for
  a runtime cleanup epoch, including the service-lane worker.
- With `cleanup_workers=1`, cleanup is serial and all existing behavior still
  passes.
- A mixed ready set starts cleanup jobs from multiple categories in one epoch.
- One shared queue-name snapshot is used per epoch.
- Queue-name snapshots are not repeated on every catch-up tick after a no-work
  queue-discovery result.
- PONG does not scan queues or open Monitor store.
- Dead-TID cleanup deletes `ctrl_in`, `ctrl_out`, and `inbox` without waiting
  for retention.
- Dead-TID cleanup deletes `outbox` and `reserved` only after retention.
- Dead-TID task-log coalescing uses the broker API and exact-deletes matching
  `weft.log.tasks` rows after summary/store handling.
- Store writes are serialized.
- Cleanup failures set cached errors and keep catch-up pending.

What not to mock:

- Do not mock `simplebroker.Queue` for queue cleanup semantics.
- Do not mock Monitor-store methods when testing summary/mark/delete behavior.
  Use a real store in the test broker database.
- Do not mock TaskMonitor process control for PONG responsiveness. Use the real
  task object and real control queues.

What may be monkeypatched:

- `broker.delete_from_queues(...)` may be wrapped in a test to block and count
  concurrency.
- The coalesce helper may be wrapped to record call order.
- `upsert_heartbeat(...)` may be no-op patched in direct TaskMonitor unit
  tests, as existing tests do.

## 9. Rollout And Ops Verification

Before deployment:

- Confirm SimpleBroker dependency is at least `3.8.3`.
- Confirm `WEFT_TASK_MONITOR_CLEANUP_WORKERS` default is `3` if the env knob
  was implemented.
- Confirm the release notes mention the new cleanup executor and serial
  rollback setting if present.

After deployment on ops, inspect:

- PING latency for Manager, Heartbeat, and TaskMonitor stays healthy.
- TaskMonitor PONG shows cleanup worker cap and mixed job progress from cached
  fields.
- `control_cleanup_pending` and reserved/dead-TID pending counts decline
  without one category monopolizing whole periods.
- `weft.log.tasks` checkpoint advances; it should not remain pinned while
  rows grow.
- `weft.log.tasks` row count eventually declines after high-water and
  retention conditions are satisfied.
- `task_ctrl_in`, `task_ctrl_out`, and `task_inbox` queues trend toward zero
  for dead TIDs.
- `task_outbox` and `task_reserved` decline only for retention-eligible dead
  TIDs.
- Postgres hot SQL no longer shows repeated global queue-name scans from
  cleanup workers when pending work is zero.

If Postgres gets hotter after rollout:

- First set `WEFT_TASK_MONITOR_CLEANUP_WORKERS=1` if the env knob exists.
- If no env knob exists, roll back the release.
- Do not disable TaskMonitor entirely unless Manager responsiveness is at risk
  or cleanup errors are corrupting operational state.

## 10. Out Of Scope

- No change to queue names.
- No change to task lifecycle state transitions.
- No change to Manager supervision.
- No new cleanup policy for custom task-local queues.
- No retention-policy redesign.
- No direct SQL cleanup from Weft production code.
- No new public CLI command.
- No async framework or process pool.
- No unbounded per-TID threads.
- No attempt to make MonitorStore generally thread-safe in this slice.

## 11. Stop-And-Replan Conditions

Stop and revise this plan if any of these happen:

- The implementation starts moving policy decisions from
  `weft/core/monitor/policies/` into the executor.
- The reactor thread starts doing broker or Monitor-store cleanup work.
- The worker cap becomes per-kind instead of total.
- The code creates one permanent worker per cleanup type.
- PONG starts scanning queues, opening stores, or computing live cleanup
  counts.
- Store writes become parallel without a separate proof and review.
- Dead-TID task-log deletion switches to direct SQL.
- Tests require broad fake brokers or fake Monitor stores to pass.
- Rollback cannot be done by setting the worker cap to one or reverting the
  single executor slice.

## 12. Self-Review Notes

Fresh-eyes pass 1:

- Ambiguity found: "three workers" could mean three helper threads plus the
  existing service-lane worker. That would create four cleanup threads. Fixed
  by requiring the service-lane worker to count toward the cap.
- Ambiguity found: "mixed cleanup workers" could be read as one permanent
  thread per cleanup type. Fixed by naming one bounded executor and explicitly
  forbidding per-type permanent workers.
- Ambiguity found: the executor could absorb policy rules. Fixed by making the
  executor policy-free and keeping queue identity/eligibility in
  `weft/core/monitor/policies/`.
- Risk found: concurrent Monitor-store writes could corrupt or thrash the
  store. Fixed by serializing `monitor_store_write` and `task_log_store` work
  in this slice.
- Risk found: worker tests could over-mock queues and miss broker behavior.
  Fixed by separating pure scheduler tests from real broker-backed cleanup
  tests.

Fresh-eyes pass 2:

- The plan still matched the requested direction: capped total workers, mixed
  cleanup types, worker-only durable work, and no separate sweeper. It did not
  drift into an async rewrite or a policy redesign.
- Gap found: the first mixed-slice test only required two cleanup kinds even
  when all three were arranged. Fixed by requiring all three kinds to progress
  when all are ready and the limit allows it.
- Gap found: one snapshot per epoch still permits expensive snapshots every
  catch-up tick when no queue-discovered work exists. Fixed by adding an
  explicit queue-discovery due check and regression test.
- Remaining risk: external independent review is still warranted after landing
  because this is destructive cleanup with concurrency. The implemented slice
  matches this plan and passed the focused cleanup, lint, and type gates, so
  the plan status is `completed`.
