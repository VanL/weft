# Task Monitor Control Cleanup Worker Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], docs/specifications/05-Message_Flow_and_State.md [MF-5], docs/specifications/07-System_Invariants.md [OBS.13]
Superseded by: none

## 1. Goal

Move terminal task-local control queue cleanup out of the TaskMonitor reactor and into a TaskMonitor-owned maintenance worker lane. Production evidence from 0.9.49 showed the bounded cleanup slice was making progress but still blocked PING for minutes because each `Queue.delete()` closed a SimpleBroker connection and ran backend cleanup synchronously on the reactor. The fix is not just "start a thread"; it is a narrow contract change: the reactor remains responsible for control handling, heartbeat registration, scheduling, and cached PONG state, while one dedicated worker may delete terminal standard `T{tid}.ctrl_in` / `T{tid}.ctrl_out` queues and mark the corresponding Monitor-store rows complete.

## 2. Source Documents

- `docs/specifications/01-Core_Components.md` [CC-2.3]: TaskMonitor service behavior, control responsiveness, and maintenance work.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: task log and monitor collation behavior.
- `docs/specifications/07-System_Invariants.md` [OBS.13]: operational observability and cleanup diagnostics.
- `docs/plans/2026-05-19-task-monitor-bounded-control-cleanup-plan.md`: completed predecessor plan. It correctly made terminal control cleanup bounded, but production showed bounded synchronous queue deletes still block the reactor.
- `docs/agent-context/runbooks/writing-plans.md`: plan quality standard.
- `docs/agent-context/runbooks/hardening-plans.md`: required here because this introduces deferred cleanup and durable queue deletion from a worker lane.

## 3. Context And Key Files

Read first:

- `weft/core/tasks/base.py`: `TaskWorkerResult`, `_submit_worker_call()`, `_drain_worker_results()`, and `_handle_worker_result()`. The existing generic worker contract says workers are broker-free. Do not silently weaken that contract.
- `weft/core/monitor/task_monitor.py`: `TaskMonitorTask.process_once()`, `next_wait_timeout()`, `_run_monitor_store_cycle()`, `_run_terminal_control_cleanup_slice()`, `_delete_terminal_control_queues()`, `_handle_worker_result()`, `_control_snapshot_fields()`, and `_task_monitor_pong_extension()`.
- `weft/core/monitor/store.py`: `list_terminal_control_cleanup_ready_tasks()`, `mark_task_controls_deleted()`, and `mark_families_disposed()`.
- `weft/context.py`: `WeftContext.queue()` and broker configuration handling. Worker code needs fresh queue handles through the context, not queue objects borrowed from the reactor.
- `tests/tasks/test_task_monitor.py`: persistent monitor behavior tests and helpers.
- `tests/core/test_monitor_store.py`: Monitor-store SQL and persistence behavior.

Files to modify:

- `weft/_constants.py`: add a dedicated lane name for terminal control cleanup.
- `weft/core/monitor/task_monitor.py`: add the TaskMonitor-owned cleanup worker state, submission, result handling, PONG fields, and scheduling behavior.
- `docs/specifications/01-Core_Components.md`: document the worker ownership contract.
- `docs/specifications/05-Message_Flow_and_State.md`: document that terminal control cleanup is asynchronous relative to a monitor cycle.
- `docs/specifications/07-System_Invariants.md`: document PONG diagnostics and the liveness expectation.
- `tests/tasks/test_task_monitor.py`: add liveness, single-flight, durable cleanup, and retry/error tests.
- `docs/plans/README.md`: add this plan and update the plan count.

Do not modify:

- Do not move task-log ingestion or summary emission to a worker in this slice.
- Do not make the existing generic `BaseTask._submit_worker_call()` contract broker-owning for all tasks.
- Do not delete `T{tid}.inbox`, `T{tid}.outbox`, or `T{tid}.reserved` here.
- Do not change which task families are eligible for terminal control cleanup.
- Do not change the external log or task-log retention policy.

## 4. Invariants

- PING/STATUS/STOP/KILL must be serviced by the TaskMonitor reactor even when terminal control queue cleanup is slow.
- At most one terminal control cleanup worker may be active per TaskMonitor process.
- The new worker may only perform this durable effect set: select eligible terminal families from the Monitor table, delete standard terminal task-local control queues, mark `task_control_deleted_at_ns`, and mark the terminal family disposed. No other worker lane receives this authority.
- If the worker fails after deleting a queue but before marking the store, retry must be safe. Whole-queue `Queue.delete()` on a missing queue must be treated as effectively complete or non-fatal in the same spirit as current best-effort cleanup.
- Existing custom TaskMonitor processors remain broker-free.
- The reactor must expose cached diagnostics only. PONG must not run active cleanup queries.
- A terminal control cleanup backlog must drive catch-up scheduling without starving control handling.

## 5. Bite-Sized Tasks

### Task 1: Add The Worker Contract

Files:

- `weft/_constants.py`
- `weft/core/monitor/task_monitor.py`
- `docs/specifications/01-Core_Components.md`

Implement:

- Add `TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE = "task_monitor.control_cleanup"` beside `TASK_MONITOR_PROCESSOR_WORKER_LANE`.
- Add private dataclasses in `task_monitor.py`:
  - `_TaskControlCleanupWork`: cycle id or `now_ns`, batch limit, and any fields needed for deterministic result handling.
  - `_TaskControlCleanupWorkerResult`: wrap `_TaskControlCleanupResult` plus store status/checkpoint information if the worker refreshed it.
- Add `_control_cleanup_work_in_flight: _TaskControlCleanupWork | None` on `TaskMonitorTask`.
- Do not reuse `_processor_work_in_flight`; custom processors and terminal control cleanup are different contracts.

Testing first:

- Add a test that starts a cleanup worker and calls `process_once()` again while it is still blocked. The test should assert that no second control cleanup worker starts. Use a narrow injected blocking seam around `_delete_terminal_control_queues()` or a new worker hook. Keep broker queues and Monitor-store data real.

Stop gate:

- Stop if implementing this requires weakening `TaskWorkerResult` for all task workers. The intended design is a TaskMonitor-owned exception with a narrow lane name and spec text.

### Task 2: Submit Cleanup Work Instead Of Running It On The Reactor

Files:

- `weft/core/monitor/task_monitor.py`

Implement:

- Replace the synchronous `_run_terminal_control_cleanup_slice()` call in `_run_monitor_store_cycle()` with a non-blocking submit method, for example `_maybe_start_terminal_control_cleanup_worker(now_ns=now_ns)`.
- The submit method must:
  - return immediately if `_control_cleanup_work_in_flight` is not `None`;
  - create a work record;
  - set `_control_cleanup_work_in_flight`;
  - set activity to a value such as `"control_cleanup"` only as cached state, not by blocking;
  - submit a background thread whose worker opens or uses fresh broker/store handles suitable for a thread.
- Prefer using the existing worker result queue to wake the reactor, but keep the durable-effects exception documented at the TaskMonitor method. If `_submit_worker_call()` is reused, update its docstring to say the default contract is broker-free except for subclass-declared lanes. The stronger option is a small TaskMonitor-private submit wrapper with a docstring naming this lane's durable effects.
- The worker should run the current cleanup transaction: list eligible records, call `_delete_terminal_control_queues()` for each selected record, then mark successful TIDs with `mark_task_controls_deleted()` and `mark_families_disposed()`.
- The worker must not touch cached `_last_*` fields directly. It returns a result to the reactor.

Testing first:

- Use a real Monitor-store row for a terminal task and real `T{tid}.ctrl_in` / `T{tid}.ctrl_out` queues. Run a monitor cycle, then drive `process_once()` until the worker result has been applied. Assert:
  - both control queues are deleted;
  - the Monitor row has `task_control_deleted_at_ns`;
  - the family is disposed;
  - `T{tid}.inbox`, `T{tid}.outbox`, and `T{tid}.reserved` are not deleted by this path.

Stop gate:

- Stop if the implementation starts moving task-log ingestion or summary emission into the same worker. That is a different performance project.

### Task 3: Keep The Reactor Responsive While Work Is In Flight

Files:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Implement:

- `process_once()` must always drain worker results and service task-local queues before deciding whether to start or wait on maintenance work.
- If `_control_cleanup_work_in_flight` is not `None`, `process_once()` must not run another monitor-store cycle, but it must still:
  - service PING/STATUS/STOP/KILL;
  - service heartbeat wake messages;
  - update cached activity and poll reports.
- `next_wait_timeout()` must return a bounded wait while a control cleanup worker is active, just as it does for custom processor work.
- PONG and control snapshot fields must expose:
  - `control_cleanup_in_flight`;
  - last cleanup result counts;
  - last cleanup errors/warnings;
  - whether more cleanup is pending.

Testing first:

- Add a deterministic liveness test:
  - arrange an eligible terminal control cleanup record;
  - block the cleanup worker after it starts;
  - send a PING to the monitor control queue;
  - call `process_once()`;
  - assert a PONG is written without waiting for the cleanup worker to finish and includes `control_cleanup_in_flight: true`.
- This test may use a synchronization primitive around the cleanup hook, but it must keep the queue and control path real.

Stop gate:

- Stop if the test can only be written by mocking `BaseTask._drain_queue()` or the control response path. That would not prove the production bug is fixed.

### Task 4: Apply Worker Results Safely

Files:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Implement:

- Extend `_handle_worker_result()` to handle `TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE`.
- On success:
  - clear `_control_cleanup_work_in_flight`;
  - update cached `last_control_*` fields from the returned result;
  - increment or set `last_terminal_families_disposed` in the same way the synchronous path did;
  - set `_last_control_cleanup_pending`;
  - schedule catch-up promptly when `pending` is true.
- On worker error:
  - clear `_control_cleanup_work_in_flight`;
  - keep the Monitor-store rows eligible for retry;
  - update `_last_control_delete_errors`, `_last_error`, and `_last_processor_success` so PONG shows the problem;
  - schedule a bounded retry using the catch-up interval, not the normal 300-second interval.
- Do not allow a stale worker result to overwrite a newer in-flight work item. If `work is None`, ignore the result. If a work id is added, require it to match.

Testing first:

- Add an error-path test where the cleanup delete hook fails. Assert:
  - PONG reports the error after result handling;
  - the family is not marked disposed;
  - a later successful run can clean it.

Stop gate:

- Stop if worker errors become fatal task exceptions that kill the monitor. Cleanup failures should be visible and retried, not take down PING/heartbeat.

### Task 5: Keep Docs And Specs Accurate

Files:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/2026-05-19-task-monitor-control-cleanup-worker-plan.md`
- `docs/plans/README.md`

Implement:

- Update spec text to say terminal task-local control cleanup is asynchronous and may complete after the monitor cycle that emitted summaries.
- Update PONG/diagnostic expectations to include `control_cleanup_in_flight`.
- Mark this plan completed only after implementation and verification pass.
- Keep `docs/plans/README.md` status aligned with the plan metadata.

## 6. Test Plan

Run these targeted tests first:

```bash
uv run pytest tests/tasks/test_task_monitor.py tests/core/test_monitor_store.py tests/specs/test_plan_metadata.py tests/system/test_constants.py
```

Then run quality gates:

```bash
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft tests
```

Then run full backend gates:

```bash
uv run pytest
uv run bin/pytest-pg
```

Contract checks the tests must prove:

- PING is answered while terminal control cleanup is blocked.
- Only one terminal control cleanup worker is in flight at a time.
- Terminal standard `T{tid}.ctrl_in` / `T{tid}.ctrl_out` queues are deleted and Monitor-store rows are marked.
- Nonstandard or manager control queues are not deleted.
- Task-local inbox/outbox/reserved queues remain out of scope.
- Worker errors are visible in PONG and retryable.
- Existing custom processor worker tests still pass.

Do not over-mock:

- Do not mock SimpleBroker queue operations for the main cleanup success test.
- Do not mock Monitor-store selection or mark methods for the main cleanup success test.
- It is acceptable to inject a blocking hook or monkeypatch `_delete_terminal_control_queues()` in the liveness test because the test is proving reactor responsiveness under a slow durable effect, not queue deletion itself.

## 7. Rollout And Ops Verification

Expected post-deploy evidence:

- Manager and heartbeat PING remain responsive.
- TaskMonitor PING remains responsive while `control_cleanup_in_flight` may be true.
- TaskMonitor CPU may be elevated during backlog reduction, but the reactor should not become PING-dead.
- `T*.ctrl_out` queue counts should continue to fall.
- `T*.ctrl_in` should only fall for terminal families whose Monitor rows are eligible. It may remain mostly flat if most rows are from open/nonterminal families.
- PONG should show increasing `last_control_families_processed`, `last_control_queues_deleted`, and either `last_control_cleanup_pending=true` during backlog or false when caught up.

Rollback:

- Reverting this slice returns to synchronous bounded cleanup. The Monitor-store schema is unchanged, so rollback does not need data migration.
- The only behavior difference during rollback is PING liveness under large terminal control cleanup backlog.

## 8. Fresh-Eyes Review

Self-review findings:

- Risk: using the generic BaseTask worker lane without changing docs would violate the existing broker-free contract. The plan resolves this by requiring a TaskMonitor-specific durable-effects contract and a dedicated lane.
- Risk: only moving queue deletes to a worker while leaving Monitor-store marks on the reactor would still require cross-thread result detail and could leave the reactor doing many store writes. The plan keeps the delete-and-mark transaction inside the worker and returns a summary, which is the smallest coherent durable unit.
- Risk: PONG could hide that a worker is running. The plan adds explicit cached `control_cleanup_in_flight` diagnostics and forbids active PONG queries.
- Risk: this could drift into broader cleanup redesign. The out-of-scope list prevents changing task-log ingestion, summary emission, or deleting inbox/outbox/reserved queues in this slice.

This plan is implementation-ready for the narrow production regression it addresses.
