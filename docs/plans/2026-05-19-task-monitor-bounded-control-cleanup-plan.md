# Task Monitor Bounded Control Cleanup Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4], [SB-0.4a]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
Superseded by: none

## 1. Goal

Fix the supervised `TaskMonitorTask` terminal runtime-control cleanup path so a
large backlog of old `T{tid}.ctrl_in` and `T{tid}.ctrl_out` queues converges
without monopolizing the monitor reactor. The immediate change is narrow:
summary emission must not inline long task-local control cleanup, and retained
terminal task-local control queues should be deleted as whole runtime queues
once the Monitor table proves the task family is retained, terminal, and
summary-safe. The monitor must continue answering PING/STATUS between bounded
maintenance slices.

This plan intentionally does not rewrite the monitor into a full
manager-style multi-threaded service. It adopts the same architectural
principle first: the reactor stays responsive, and heavyweight maintenance is
bounded and resumable. If a bounded whole-queue cleanup slice still blocks
PING in ops, stop and write a follow-up plan to move monitor-owned destructive
maintenance into a worker lane with an explicit broker-effects contract.

## 2. Source Documents

- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]:
  `TaskMonitorTask` owns persistent monitor scheduling, cached diagnostics,
  Monitor-store writes, and operational cleanup. The spec currently says the
  reactor owns broker effects, so do not move built-in destructive cleanup to a
  worker thread in this slice without first changing that contract.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4], [SB-0.4a]:
  queue rows are SimpleBroker data. Use public SimpleBroker queue APIs for
  queue deletion; use the Monitor store access layer only for Monitor-owned
  tables.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]:
  `weft.log.tasks` retention, Monitor-owned collation, summary disposition,
  and task-local runtime queue cleanup live here.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]:
  TaskMonitor output is operational only; PONG must be cached/lightweight; and
  cleanup may delete only explicit supported rows or queues.
- `docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`:
  active draft that defines Monitor-table-driven retained task-log cleanup.
  This plan tightens the terminal control-queue disposition portion after ops
  showed the current implementation still blocks the reactor.
- `docs/plans/2026-05-19-monitor-loop-batched-cleanup-plan.md`: completed
  batch repair for retained raw task-log ingestion. Keep that work; this plan
  addresses the next bottleneck exposed after that repair.
- `../simplebroker/simplebroker/sbqueue.py`: public `Queue.delete()`,
  `Queue.delete_many(...)`, and queue stats semantics. Read before changing
  queue deletion.
- `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/_sql.py`:
  confirms exact-ID delete behavior for Postgres. Do not copy private SQL into
  Weft for this slice.
- `AGENTS.md`, `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`, and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  local planning and review guidance.

The specs already allow retained terminal disposition to remove standard
task-local `T{tid}.ctrl_in` and `T{tid}.ctrl_out` runtime evidence. The spec
must be refreshed during implementation to say this cleanup is bounded and
whole-queue for terminal task-local controls.

## 3. Context And Key Files

Read first:

- `weft/core/monitor/task_monitor.py`
  - `TaskMonitorTask.process_once`: reactor scheduling and control draining.
  - `TaskMonitorTask._run_monitor_store_cycle`: retained task-log ingest,
    summary emission, and current control cleanup sequencing.
  - `TaskMonitorTask._emit_monitor_store_summaries`: currently emits summaries
    and then calls `_delete_terminal_control_queues()` inline for each terminal
    family.
  - `TaskMonitorTask._delete_terminal_control_queues`: currently opens each
    standard task-local control queue, peeks visible rows, calls
    `delete_many(...)`, and stats before/after.
  - `TaskMonitorTask._finish_monitor_cycle`: cached PONG and serve-log stats.
- `weft/core/monitor/store.py`
  - `MonitorStore.list_summary_ready_tasks`: current ready-family query wrapper.
  - `MonitorStore.mark_summaries_emitted`
  - `MonitorStore.mark_task_controls_deleted`
  - `MonitorStore.mark_families_disposed`
- `weft/core/monitor/sql.py`
  - `select_summary_ready_terminal_tasks`
  - `mark_task_control_deleted`
  - `mark_family_disposed`
  - SQL builder style and trusted identifier helpers.
- `weft/core/monitor/runtime.py`
  - `TaskMonitorRuntimeConfig.control_queue_delete_limit`.
- `weft/_constants.py`
  - `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT_DEFAULT` and env parsing.
- `weft/core/pruning/apply.py`
  - Canonical exact-message delete helper. This slice should not route
    task-local control whole-queue deletion through exact-message pruning.
- `tests/tasks/test_task_monitor.py`
  - Existing integration tests for terminal control cleanup and manager
    exclusion.
- `tests/core/test_monitor_store.py`
  - Monitor table readiness and marker tests.
- `tests/core/test_monitor_sql.py`
  - SQL builder tests.
- `tests/specs/test_plan_metadata.py`
  - Plan metadata and README index enforcement.

Current load-bearing behavior:

- The monitor watches its own inbox and `ctrl_in` at
  `TaskMonitorTask._build_queue_configs()`. It does not watch `weft.log.tasks`
  directly.
- `process_once()` drains worker results, emits config once, drains local
  queues, registers heartbeat, and then runs `_run_monitor_cycle()` when due.
  While `_run_monitor_cycle()` is running, PING/STATUS cannot be serviced.
- `_run_monitor_store_cycle()` runs retained FIFO ingest. If the FIFO pass
  reaches a completed high-water mark, it calls `_emit_monitor_store_summaries`.
- `_emit_monitor_store_summaries()` currently does too much. It emits summary
  output and then performs destructive terminal control cleanup inline before
  marking the family disposed.
- `_delete_terminal_control_queues()` currently deletes only visible rows.
  Claimed `ctrl_in`/`ctrl_out` rows remain. It also does per-family queue stats
  and row enumeration, which is exactly the shape that became CPU-hot in ops.
- Ops evidence from `0.9.48` showed:
  - manager PONG healthy and CPU near idle;
  - heartbeat PONG healthy and CPU near idle;
  - task monitor PING timed out for the whole 25-minute window;
  - `py-spy` placed the monitor in
    `_delete_terminal_control_queues -> Queue.delete_many`;
  - `T*.ctrl_out` decreased only about 26 rows/minute;
  - `T*.ctrl_in` did not decrease and grew by the monitor PING attempts.

Comprehension checks before editing:

- Which method currently blocks the monitor reactor while deleting
  task-local control queues?
- Why is `T{tid}.ctrl_in` safe to delete only after retained terminal family
  proof, but not by global age alone?
- Why must manager/global/custom control queues remain untouched even when
  they look old?
- Why is `Queue.delete()` acceptable for standard terminal task-local control
  queues, but not for `T{tid}.outbox`, `T{tid}.inbox`, or `T{tid}.reserved`?
- Which PONG fields are cached and why must PONG not query queues or Monitor
  tables while answering?

## 4. Invariants And Constraints

- Do not create a second task lifecycle truth source. Monitor tables and
  summaries remain operational cleanup state only.
- Do not change public task status/result reconstruction to read Monitor
  tables.
- Do not change queue names.
- Do not change TaskSpec immutability, TID format, or forward-only task state
  transitions.
- Do not delete active task-local control queues. Terminal control cleanup is
  allowed only for retained terminal Monitor families after any required
  summary emission has succeeded.
- Do not delete manager/global/custom control queues. Only standard
  `T{tid}.ctrl_in` and `T{tid}.ctrl_out` queues whose TaskSpec summary exactly
  names those standard queues are eligible.
- Do not delete `T{tid}.inbox`, `T{tid}.outbox`, or `T{tid}.reserved` in this
  slice.
- Do not probe reserved queues. Successful terminal cleanup does not need
  reserved-queue evidence.
- Use public SimpleBroker queue APIs for queue deletion. Do not query or delete
  from the SimpleBroker `messages` table directly in Weft.
- Keep Monitor-owned SQL in `weft/core/monitor/sql.py` and table access in
  `weft/core/monitor/store.py`.
- Keep production constants and env parsing in `weft/_constants.py`.
- PONG must remain cached and lightweight. It must not scan queues, open
  external logs, query Monitor tables, or trigger cleanup.
- Tests for queue deletion must use real broker-backed queues. Do not mock
  SimpleBroker for the primary behavior proof.
- This plan changes destructive cleanup behavior. Implementation is not
  complete until the touched specs are updated and an external plan/work review
  is run or explicitly waived by the maintainer.

## 5. Design

### 5.1 Split Summary Emission From Control Cleanup

`_emit_monitor_store_summaries()` should only:

1. fetch summary-ready families;
2. emit required summaries;
3. mark summaries emitted;
4. classify and dispose non-terminal suspected families that do not require
   task-local control cleanup.

It must stop deleting terminal task-local control queues inline. Terminal
families with `summary_emitted_at_ns` set and `task_control_deleted_at_ns` null
remain pending for a separate bounded control-cleanup slice.

Reason: summary output is a logical family event; control cleanup is physical
runtime queue maintenance. Coupling them is what made one monitor cycle
unbounded.

### 5.2 Add A Bounded Terminal Control Cleanup Slice

Add a new monitor-store query and TaskMonitor method:

- `MonitorStore.list_terminal_control_cleanup_ready_tasks(...)`
- `TaskMonitorTask._run_terminal_control_cleanup_slice(...)`

Eligibility:

- `context_key` matches this Weft context;
- `terminal_seen = 1`;
- `summary_emitted_at_ns IS NOT NULL`;
- `task_control_deleted_at_ns IS NULL`;
- `disposition_at_ns IS NULL`;
- family high-water (`last_message_id` or equivalent existing readiness field)
  is older than `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`;
- limit is bounded by `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT`.

Execution:

1. Fetch at most the configured limit of ready terminal families.
2. For each family, compute `_standard_task_control_queue_names(record)`.
3. If the family is manager/custom/nonstandard, do not delete any queue, but
   mark control cleanup complete so the family can be disposed and does not
   loop forever.
4. If the family has standard task-local controls, delete the whole
   `T{tid}.ctrl_in` queue and whole `T{tid}.ctrl_out` queue through
   `Queue.delete()`.
5. Mark successful families with `task_control_deleted_at_ns`.
6. Mark the same successful terminal families disposed with
   `disposition_reason = "terminal"`.
7. Return cached stats for PONG and serve logs.

The slice must check both item count and a small internal wall-clock budget
between families. Use a production constant in `weft/_constants.py`, not a
module-local magic number. The first implementation should keep the budget
conservative, for example a few hundred milliseconds, because a second cycle
can run after `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS`.

Do not overfit the budget to ops. The invariant is not "finish the whole
backlog in one cycle." The invariant is "one cycle must return control to the
reactor quickly."

### 5.3 Use Whole-Queue Delete For Terminal Runtime Controls

For retained terminal standard task-local controls, use `Queue.delete()` rather
than `peek_generator()` plus `delete_many(...)`.

Why this is correct:

- `T{tid}.ctrl_in` and `T{tid}.ctrl_out` are runtime control surfaces.
- Once the task family is retained and terminal, no future task execution
  should consume those control rows.
- The Monitor table has already retained the task lifecycle summary.
- Existing specs explicitly exclude inbox/outbox/reserved from this default
  cleanup but allow standard task-local control cleanup.

Why this is better operationally:

- It deletes both visible and claimed rows.
- It avoids enumerating every control message ID in Python.
- It avoids per-row exact-delete batches for runtime state we no longer need.

Diagnostics:

- `Queue.delete()` returns a boolean, not an exact row count. Do not make exact
  row count a correctness requirement.
- If useful, take `queue.stats().total` before deletion as an estimated row
  count for PONG, but treat it as operational telemetry only.
- Prefer reporting `control_families_processed`, `control_families_disposed`,
  `control_queues_deleted`, `control_rows_estimated_deleted`, and errors.

### 5.4 Reactor Versus Worker Threads

Do not move built-in control cleanup to a secondary worker in this slice.

Steelman for workerization:

- It matches the manager pattern: reactor owns liveness and control, workers do
  slow work.
- It would protect PING even if one queue delete stalls.
- It gives a cleaner long-term contract for monitor maintenance.

Why not now:

- The current spec says the TaskMonitor reactor owns Monitor-store writes,
  cached diagnostics, and broker effects.
- Moving destructive broker effects to a worker is a real contract change.
- A worker would need independent broker connections, restart semantics,
  duplicate-work protection, and a result application protocol.
- The production root cause can be addressed with bounded whole-queue slices
  without changing thread ownership.

Stop gate:

- If a bounded whole-queue slice still makes monitor PING time out in ops, stop
  and write a follow-up plan for a monitor maintenance worker lane. That plan
  must update [CC-2.3], define which broker effects workers may perform, and
  prove worker cancellation/retry behavior with real broker tests.

## 6. Tasks

### Task 1: Write Red Tests For Split And Whole-Queue Semantics

Files to modify:

- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_store.py`
- `tests/core/test_monitor_sql.py`

Red tests:

1. Add a test that creates a retained terminal task family with standard
   `T{tid}.ctrl_in` and `T{tid}.ctrl_out` queues containing both visible and
   claimed rows. Run the monitor cleanup slice and assert:
   - both control queues have `stats().total == 0`;
   - `T{tid}.inbox`, `T{tid}.outbox`, and `T{tid}.reserved` remain unchanged;
   - the Monitor record has `summary_emitted_at_ns`,
     `task_control_deleted_at_ns`, `disposition_reason == "terminal"`, and
     `disposition_at_ns`.
2. Add a test with more ready terminal families than
   `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT`. Assert one `process_once()`
   disposes only the bounded number and leaves the rest eligible for a later
   turn. This test should use real Monitor store rows and real queues, not a
   mocked queue object.
3. Add a test that manager-role and custom-control families are preserved:
   - their `ctrl_in` and `ctrl_out` queue rows remain;
   - the family is still marked control-cleaned and disposed so it does not
     spin forever.
4. Update or replace
   `test_task_monitor_terminal_disposition_deletes_task_control_queues_only`
   so it no longer asserts exact deleted row count as the core contract.
   Assert queue emptiness and Monitor disposition instead.

Do not mock:

- `Queue.delete()`
- `Queue.stats()`
- Monitor store writes
- `TaskMonitorTask.process_once()` for the main integration proof

Acceptable narrow mocks:

- heartbeat registration, as existing tests already do with
  `upsert_heartbeat`;
- an injected failure seam if a specific error-path test needs to force one
  queue delete failure.

Gate:

- The new tests must fail against the current implementation because current
  cleanup deletes only visible control rows and runs terminal control cleanup
  inline with summary disposition.

### Task 2: Add Store Query For Control Cleanup Readiness

Files to modify:

- `weft/core/monitor/sql.py`
- `weft/core/monitor/store.py`
- `tests/core/test_monitor_sql.py`
- `tests/core/test_monitor_store.py`

Implementation:

1. Add `select_terminal_control_cleanup_ready_tasks(...)` in
   `weft/core/monitor/sql.py`.
2. Add `_MonitorTableAccess.list_terminal_control_cleanup_ready_tasks(...)`.
3. Add `MonitorStore.list_terminal_control_cleanup_ready_tasks(...)`.
4. Order by `last_message_id, tid`.
5. Filter on:
   - `terminal_seen = 1`;
   - `summary_emitted_at_ns IS NOT NULL`;
   - `task_control_deleted_at_ns IS NULL`;
   - `disposition_at_ns IS NULL`;
   - `last_message_id <= retention_cutoff`.

Do not:

- add physical Monitor-row deletion;
- read SimpleBroker queues from the store;
- put TaskMonitor-specific runtime decisions in SQL beyond selecting ready
  Monitor rows.

Tests:

- SQL test verifies the query contains the readiness predicates and uses
  placeholders for cutoff/limit.
- Store test verifies that a terminal summary-emitted family appears, a family
  without summary emission does not, a disposed family does not, and a too-young
  family does not.

### Task 3: Add Whole-Queue Control Cleanup Helper

Files to modify:

- `weft/core/monitor/task_monitor.py`
- `weft/_constants.py`
- `weft/core/monitor/runtime.py`
- `tests/tasks/test_task_monitor.py`
- `tests/system/test_constants.py` if a new production constant is added and
  the constants policy requires classification.

Implementation:

1. Replace the visible-row scan in `_delete_terminal_control_queues()` with
   whole-queue deletion for standard terminal task-local control queues.
2. Keep `_standard_task_control_queue_names(record)` as the eligibility guard.
3. Use `ctx.queue(queue_name, persistent=False)` and `queue.delete()` for each
   standard control queue.
4. Delete `ctrl_in` and `ctrl_out`. Do not prioritize one over the other.
5. Treat absent/empty queues as success.
6. Return an expanded `_TaskControlCleanupResult` with at least:
   - families_processed;
   - families_disposed;
   - queues_deleted;
   - rows_estimated_deleted;
   - skipped_nonstandard;
   - errors;
   - warnings.
7. Update PONG summary output to expose these cached fields.

Counting rule:

- Do not require exact deleted row counts. If `queue.stats().total` is read
  before delete, call it `rows_estimated_deleted`.
- If reading stats becomes a measurable hotspot, remove the row estimate
  before adding private SQL. Queue cleanup correctness is queue emptiness plus
  Monitor disposition, not exact telemetry.

Config:

- Keep `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT` as the per-cycle family
  or queue-pair cap for this cleanup. Update its docstring and runtime config
  docs accordingly.
- Add one internal cycle budget constant in `_constants.py`, for example
  `TASK_MONITOR_CONTROL_CLEANUP_SLICE_SECONDS_DEFAULT`, if tests and ops
  evidence show count-only bounding is insufficient. Do not add a public env
  variable unless there is a demonstrated tuning need.

### Task 4: Split Summary Emission And Control Disposition

Files to modify:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Implementation:

1. Change `_emit_monitor_store_summaries()` so it no longer calls
   `_delete_terminal_control_queues()`.
2. For terminal families, only emit/mark summaries in this method.
3. For non-terminal suspected/stale families, keep summary emission and
   disposition in this method because no terminal task-local control cleanup is
   needed.
4. Add `_run_terminal_control_cleanup_slice(store, now_ns=...)`.
5. Call the new slice from `_run_monitor_store_cycle()` after summary emission
   when:
   - retained FIFO ingest reached completed high-water;
   - processor is `delete`;
   - task-log owner is `collated_store`.
6. The cleanup slice should mark `task_control_deleted_at_ns` and terminal
   disposition in coalesced batches after successful queue deletion or
   nonstandard skip.
7. If a queue delete fails for one family, record the error, skip disposition
   for that family, and continue to the next family until the slice budget or
   limit is exhausted.

Important sequencing:

- External summary emit failure must still block terminal disposition. This
  follows from requiring `summary_emitted_at_ns IS NOT NULL` in the new ready
  query.
- A terminal family with nonstandard controls should not block forever. Mark
  it control-cleaned and disposed without deleting those queues.

### Task 5: Update Cached Diagnostics And Serve Logs

Files to modify:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Implementation:

1. Add cached fields for terminal control cleanup:
   - `last_control_families_processed`
   - `last_control_families_disposed`
   - `last_control_queues_deleted`
   - `last_control_rows_estimated_deleted`
   - `last_control_nonstandard_skipped`
   - existing errors/warnings
2. Include these fields in:
   - extended PONG under `task_monitor.last_cycle`;
   - serve-log cycle fields.
3. Preserve existing `control_rows_deleted` only if it can be mapped honestly
   to estimated rows. If not, rename in PONG to avoid implying exact deletion.
   If a rename is made, update specs and tests in the same commit.

PONG invariant:

- PONG reads only cached values. It must not call the Monitor store or inspect
  queues.

### Task 6: Spec And Documentation Updates

Files to modify:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/00-Quick_Reference.md` only if a config description
  changes
- `docs/plans/README.md`

Required updates:

1. In [CC-2.3], state that built-in terminal control cleanup is a bounded
   maintenance slice on the reactor, not inline summary work.
2. In [MF-5], state that retained terminal standard task-local control cleanup
   deletes whole `T{tid}.ctrl_in` and `T{tid}.ctrl_out` queues after summary
   emission succeeds.
3. In [OBS.13], keep the exclusion of manager/global/custom controls and
   inbox/outbox/reserved. Add that queue-wide delete is allowed only for
   standard terminal task-local control queues.
4. Update implementation mapping notes so future agents know:
   - summary emission lives in `_emit_monitor_store_summaries`;
   - control cleanup readiness lives in `MonitorStore`;
   - whole-queue cleanup lives in the TaskMonitor runtime boundary.
5. Add this plan to `docs/plans/README.md` with status `draft`.

### Task 7: Verification

Run in this order:

1. Targeted tests:

   ```bash
   uv run pytest tests/core/test_monitor_sql.py tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py
   ```

2. Spec and policy tests:

   ```bash
   uv run pytest tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py tests/system/test_constants.py
   ```

3. Type and lint:

   ```bash
   uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
   uv run ruff check weft tests
   ```

4. Broader regression:

   ```bash
   uv run pytest
   uv run bin/pytest-pg
   ```

Do not substitute mocks for these tests. If Postgres-only behavior differs,
add a Postgres-backed test or use the existing `pytest-pg` suite rather than
assuming SQLite behavior is enough.

## 7. Rollout And Ops Verification

After deploy, check:

- `weft manager list --json`: exactly one active manager.
- `weft.state.services`: exactly one active `_weft.service.task_monitor` and
  one active `_weft.service.heartbeat`.
- `weft task ping <manager_tid>`: responds.
- `weft task ping <heartbeat_tid>`: responds.
- `weft task ping <task_monitor_tid>`: should respond between cleanup slices.
  A single transient timeout during an active delete query is tolerable; a
  sustained 25-minute timeout is not.
- `ps`: manager and heartbeat near idle; monitor may use CPU while catching up
  but should not stay pinned near one full core without PING response.
- Queue trends over 10-25 minutes:
  - `T*.ctrl_in` decreases, not grows except for new live control traffic;
  - `T*.ctrl_out` decreases;
  - total queue count decreases;
  - `weft.log.tasks` remains bounded by retention/new workload;
  - Monitor PONG cached stats show control families processed and disposed.

Expected healthy catch-up shape:

- The first deployment window may still show elevated monitor CPU.
- PING should succeed between slices.
- `T*.ctrl_in` and `T*.ctrl_out` should both fall materially faster than the
  pre-fix rate of roughly 26 `ctrl_out` rows/minute.
- Monitor table `task_control_deleted_at_ns` and `disposition_at_ns` should
  advance during catch-up, not stay flat until a huge pass finishes.

Rollback:

- This is a destructive cleanup change. Rollback cannot restore deleted
  terminal task-local control queues.
- Rollback to the prior release should remain safe for live tasks because this
  plan only deletes retained terminal standard task-local control queues.
- If ops shows wrong queues being deleted, stop the monitor service or disable
  TaskMonitor cleanup via config before redeploying.

## 8. Out Of Scope

- Do not add a generic SimpleBroker `delete_queues(...)` API in this slice.
  That may be a follow-up optimization if public `Queue.delete()` per queue is
  still too slow.
- Do not move built-in TaskMonitor cleanup to a worker thread in this slice.
- Do not delete `T{tid}.inbox`, `T{tid}.outbox`, or `T{tid}.reserved`.
- Do not physically purge Monitor table rows.
- Do not change external task-log JSONL schema.
- Do not change manager supervision or service convergence.
- Do not add a new public CLI command.

## 9. Stop And Re-Plan Triggers

Stop implementation and revise the plan if any of these happen:

- implementing bounded cleanup requires private SQL against SimpleBroker
  queue tables;
- queue-wide deletion would touch any queue outside standard
  `T{tid}.ctrl_in` / `T{tid}.ctrl_out`;
- tests need broad mocks to pass;
- PONG still cannot be serviced after one bounded slice in an integration test;
- exact task result/outbox behavior starts depending on Monitor table state;
- a worker-thread design becomes necessary to meet liveness. That is a larger
  contract change and needs its own plan/spec update.

## 10. Fresh-Eyes Self-Review

Findings from a separate review pass:

1. The first draft risked over-generalizing into a manager-style worker
   rewrite. That would be a larger contract change because the current spec
   says the TaskMonitor reactor owns broker effects. The plan now makes worker
   migration a stop-gated follow-up, not part of this slice.
2. The first draft treated row counts as if they were exact after queue-wide
   delete. `Queue.delete()` returns boolean, so exact row counts are not a
   reliable contract. The plan now uses queue emptiness and Monitor disposition
   as correctness proof, with row counts only as optional estimates.
3. The first draft did not explicitly handle manager/custom controls. The plan
   now requires preserving those queues while still marking the family
   control-cleaned/disposed so the monitor cannot spin forever.
4. The first draft did not say how external summary failures block cleanup.
   The plan now requires `summary_emitted_at_ns IS NOT NULL` in the cleanup
   readiness query.

Residual risk:

- Public `Queue.delete()` performs one queue delete at a time. If ops still
  shows long single-query stalls or too-slow convergence, the next correct move
  is a SimpleBroker public multi-queue delete API, not private Weft SQL.
- Implementation must still be verified under ops pressure because this plan
  changes destructive cleanup behavior and runtime liveness. The local
  correctness gate is queue emptiness plus Monitor disposition for retained
  terminal standard task-local controls; the production gate is responsive
  TaskMonitor PING while `T*.ctrl_in` and `T*.ctrl_out` decrease.
