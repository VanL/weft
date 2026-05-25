# Monitor Dead-Task Catch-Up Convergence Plan

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.3]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
Superseded by: none

## 1. Goal

Fix the TaskMonitor false catch-up loop observed on ops. The immediate bug is
that `runtime.dead_tid_cleanup` can scan many task-local queues, select no
actionable work, count only retention-deferred queues, and still report a
bounded waypoint. That makes the monitor wake on the catch-up cadence and burn
CPU proving the same no-op state repeatedly. After this fix, deferred-only
dead-task cleanup is base-for-now, not catch-up work.

This plan does not lower task-log retention, redesign the monitor store, change
public task status/result behavior, or add a new cleanup mode. It tightens the
existing dead-task runtime cleanup selection and progress contract.

## 2. Source Documents

Read these before editing code:

- `AGENTS.md`: repo philosophy, SimpleBroker boundary, constants rule, and
  real-broker test expectations.
- `docs/agent-context/runbooks/writing-plans.md`: required plan structure.
- `docs/agent-context/runbooks/hardening-plans.md`: mandatory here because
  this changes destructive cleanup scheduling and runtime-only queue handling.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.3]: Weft must use
  public SimpleBroker queue APIs for broker queue operations.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: TaskMonitor
  cleanup, Monitor-store collation, runtime queue cleanup, and cached PONG
  diagnostics.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17]: Monitor state is operational, cleanup is exact and bounded, and
  deferred-only cleanup must not keep catch-up pending.
- `docs/plans/2026-05-24-monitor-policy-progress-contract-plan.md`: completed
  progress vocabulary. This plan fixes one policy that still violates it.
- `docs/plans/2026-05-23-monitor-cleanup-policy-convergence-plan.md`:
  completed convergence work. This plan closes the ops-discovered gap in
  dead-task actionability and catch-up scheduling.
- `docs/plans/2026-05-22-monitor-policy-modules-and-dead-task-cleanup-plan.md`:
  completed policy split that introduced the dead-task selector.

Comprehension checks before editing:

- Which TaskMonitor worker lane runs `runtime.dead_tid_cleanup`, and how does
  that worker set `_last_catchup_pending`?
- Which queue suffixes are immediately stale for a proven-dead TID, and which
  suffixes remain retention-gated?
- Why is a young `T{tid}.outbox` queue evidence of future work, not actionable
  work now?
- Why must PONG report cached policy progress instead of scanning queues live?

## 3. Current Evidence

Ops evidence from Weft `0.9.62` showed the monitor service was responsive but
the worker process was CPU-hot during scheduled cleanup bursts:

- Manager, heartbeat, and task-monitor PING all responded in about
  `1.2-1.4s`.
- py-spy showed the monitor main thread idle while worker threads ran
  `task_monitor.control_cleanup` and `task_monitor.builtin_cycle`.
- Hot stacks were mostly SimpleBroker setup/open/close work around no-op
  cleanup checks.
- Monitor PONG showed:
  - `policy: runtime.dead_tid_cleanup`
  - `scanned: 1729`
  - `selected: 0`
  - `deferred: 41`
  - `waypoint_reached: true`
  - `base_reached: false`
  - `last_catchup_pending: true`
  - next cycle due in roughly 2 seconds
- `weft.messages` had many `T*.outbox` queues; the oldest outbox was about
  `172557` seconds old, just under the `172800` second task-log retention
  period. Those queues were not eligible for dead-task outbox cleanup yet.

The correct interpretation is: there was no actionable dead-task cleanup at
that moment. The policy should have reported deferred retention work and base
for now, then slept until the normal monitor interval or until new wake input
arrived.

## 4. Context And Key Files

Files to modify:

- `weft/core/monitor/policies/runtime_control.py`
  - Primary fix. Make dead-task selection actionability-aware before deadline,
    limit, and Monitor-store lookups.
- `weft/core/monitor/task_monitor.py`
  - Adjust dead-task cleanup progress handling only if the selector result
    needs clearer cached PONG fields or reason counts.
- `weft/core/monitor/progress.py`
  - Do not change unless a validation rule is missing. The existing
    `PolicyProgress` contract should be enough.
- `docs/specifications/05-Message_Flow_and_State.md`
  - Clarify dead-task deferred-only base semantics if current text is not
    precise enough after the implementation.
- `docs/specifications/07-System_Invariants.md`
  - Keep OBS.13/OBS.17 aligned with the fixed selector and catch-up behavior.
- Tests:
  - `tests/core/monitor/policies/test_runtime_control.py`
  - `tests/core/monitor/policies/test_dead_task.py`
  - `tests/tasks/test_task_monitor.py`

Files to read but not casually modify:

- `weft/core/monitor/policies/dead_task.py`
  - Defines strict task-local queue-name parsing and the dead-task queue
    cleanup plan.
- `weft/core/monitor/store.py`
  - Defines `MonitorTaskCollationRecord` and `get_task()` behavior. The
    selector should avoid calling `task_record()` for obviously deferred-only
    families.
- `weft/core/monitor/task_monitor.py`
  - `TaskMonitor._run_dead_task_cleanup_slice()` turns selector output into
    worker result and cached progress.
- `tests/tasks/test_task_monitor.py`
  - Existing runtime cleanup regression tests live here.

Do not modify:

- `weft/core/manager.py`: manager supervision is not the owner of this bug.
- `weft/commands/status.py`, `weft/commands/result.py`: Monitor progress is
  operational only and must not become public lifecycle truth.
- `../simplebroker`: use the installed public APIs and Weft's existing helper
  paths.

## 5. Invariants And Constraints

Preserve these invariants:

- Task-local runtime cleanup remains exact and bounded.
- No cleanup path may delete active-owner queues.
- Dead-task cleanup must not delete manager/global/custom queues.
- `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and `T{tid}.inbox` are actionable for a
  proven-dead TID after the stale/dead minimum age.
- `T{tid}.outbox` and `T{tid}.reserved` are actionable only after task-log
  retention unless another explicit policy has terminal/reserved proof.
- A Monitor collation row for a TID means terminal/reserved cleanup owns that
  family; name-derived dead-task cleanup must skip it.
- Deferred-only work is base-for-now. It must not set
  `waypoint_reached=True`, `pending=True`, or catch-up cadence.
- PONG must remain cached. Do not query queues or Monitor tables while
  answering PING.

Do not add:

- a new cleanup mode
- a new config gate
- direct SQL against `weft.messages`
- a second runtime cleanup scheduler
- a lower default retention period
- a broad monitor-store refactor

## 6. Required Semantics

For `runtime.dead_tid_cleanup`, define the policy conditions this way:

- **Actionable candidate**: a non-active, old-enough, standard TID with at
  least one currently deletable queue according to `dead_task_queue_cleanup_plan`.
  This includes stale `ctrl_in`, `ctrl_out`, and `inbox` queues after the
  dead-task minimum age, plus `outbox` and `reserved` only after task-log
  retention.
- **Deferred candidate**: a standard TID that is old enough for dead-task
  consideration but only has retention-gated queues that are not old enough
  yet, or is skipped because live ownership/Monitor ownership means another
  policy owns it.
- **Base for now**: all discovered TIDs are inactive-but-deferred,
  active-owned, too young, Monitor-owned, or absent. There is no actionable
  deletion at the current `now_ns`.
- **Waypoint**: the selector or worker stopped before exhausting actionable
  candidates because it hit a configured limit or deadline. A deferred-only
  scan must not be a waypoint.

The selector must determine actionability before it evaluates the per-slice
deadline. Deadline checks are useful around potentially expensive actionable
work; they are harmful when they make a large deferred-only set look like
urgent work.

## 7. Tasks

1. Add red tests for deferred-only dead-task selection.
   - Files to touch:
     - `tests/core/monitor/policies/test_runtime_control.py`
   - Read first:
     - `weft/core/monitor/policies/runtime_control.py`
     - `weft/core/monitor/policies/dead_task.py`
   - Test shape:
     - Build many standard `T{tid}.outbox` queue names whose TIDs are older
       than `TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS` but younger
       than `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`.
     - Pass an empty `active_tids` set.
     - Pass a `task_record` callable that raises if called. Deferred-only
       outbox work should not require a Monitor-store lookup.
     - Pass a `deadline_reached` callable that raises if called before any
       actionable candidate exists.
   - Expected assertions:
     - `selection.tids == ()`
     - `selection.deferred_retention > 0`
     - `selection.pending is False`
     - `selection.deadline_hit is False`
   - Stop if this test requires a real broker. This is a pure selector
     contract and should stay pure.

2. Make dead-task selector actionability-aware.
   - Files to touch:
     - `weft/core/monitor/policies/runtime_control.py`
   - Reuse:
     - `standard_task_queue_identity(...)`
     - `dead_task_queue_cleanup_plan(...)`
     - `standard_dead_task_retention_queue_names(...)`
     - `is_old_enough(...)`
   - Implementation approach:
     - Group discovered standard queue names by TID and suffix.
     - Iterate TIDs oldest first.
     - Skip live TIDs and too-young TIDs as today.
     - If a TID has only retention-gated suffixes and is not retention
       eligible, increment `deferred_retention` and continue without calling
       `task_record()` or `deadline_reached()`.
     - Build the per-TID cleanup plan and intersect it with actual queue names.
     - Only after at least one currently deletable queue exists should the
       selector check limit, check deadline, and call `task_record(tid)`.
     - If `task_record(tid)` returns a Monitor row, increment
       `skipped_monitor_records` and continue; terminal/reserved cleanup owns
       that family.
     - Set `pending=True` only when there are more actionable candidates than
       the limit or deadline permits.
   - Do not:
     - introduce a new class hierarchy
     - change retention defaults
     - issue broker SQL
   - Done when:
     - the red test from Task 1 passes
     - existing selector tests still pass

3. Fix dead-task worker progress and catch-up scheduling.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - possibly `tests/tasks/test_task_monitor.py`
   - Current issue:
     - `_run_dead_task_cleanup_slice()` treats selector waypoint/pending as
       catch-up work even when no queue was selected or deleted.
   - Implementation approach:
     - Ensure `dead_pending` is false when the selector selected no TIDs and
       only reported deferred retention, live skips, too-young skips, or
       Monitor-record skips.
     - Keep `waypoint_reached=True` when the worker actually selected
       actionable TIDs and hit a limit/deadline before exhausting them.
     - Keep `base_reached=True` for deferred-only results.
     - Include `deferred_retention`, `skipped_monitor_records`, `skipped_live`,
       and `skipped_too_young` in `reason_counts` so PONG explains why no
       deletion happened.
   - Tests:
     - Add a TaskMonitor-level regression with broker-backed young outbox-only
       queues. Run `_run_dead_task_cleanup_slice()` and assert:
       - `cleanup.pending is False`
       - `cleanup.dead_tids_deferred_retention > 0`
       - final `PolicyProgress.base_reached is True`
       - final `PolicyProgress.waypoint_reached is False`
     - Add a scheduling assertion through `_handle_control_cleanup_worker_result`
       or the smallest existing worker-result seam showing that this progress
       does not set `_last_catchup_pending`.
   - Stop if the test starts mocking SimpleBroker queue behavior. Use
     `broker_env` queues.

4. Preserve catch-up for real actionable backlog.
   - Files to touch:
     - `tests/tasks/test_task_monitor.py`
     - possibly `tests/core/monitor/policies/test_runtime_control.py`
   - Test shape:
     - Create more actionable stale `ctrl_in` or `inbox` queue families than
       the cleanup family limit.
     - Use real broker-backed queues.
     - Run one dead-task cleanup slice.
   - Expected assertions:
     - at least one family is processed
     - remaining actionable work is reported with `pending=True` or
       `waypoint_reached=True`
     - catch-up remains true after applying the worker result
   - This protects the counterargument: we must not make the monitor too quiet
     when real cleanup backlog remains.

5. Tighten PONG/debuggability for deferred-only runtime cleanup.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
   - Implementation approach:
     - Prefer existing `last_policy_progress` rather than adding new top-level
       fields.
     - If a top-level cached field is needed, add only fields already present
       in `_TaskControlCleanupResult.to_summary()`. Do not compute anything
       live for PONG.
   - Expected PONG after the fix in the ops shape:
     - `runtime.dead_tid_cleanup.selected == 0`
     - `runtime.dead_tid_cleanup.deferred > 0`
     - `runtime.dead_tid_cleanup.base_reached == true`
     - `runtime.dead_tid_cleanup.waypoint_reached == false`
     - `last_catchup_pending == false`

6. Update specs and traceability.
   - Files to touch:
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
   - Required spec text:
     - Dead-task cleanup must classify retention-deferred outbox/reserved-only
       families as base-for-now.
     - Dead-task cleanup must decide actionability before using a selection
       deadline.
     - Deferred-only runtime cleanup must not set catch-up cadence.
   - Do not describe this plan as normative. The spec should describe the
     behavior; the plan should remain an implementation path.

## 8. Testing Plan

Use red-green TDD for the selector bug. The red test is practical and should
fail on the current implementation because the selector can call deadline and
set pending while only deferred outbox/reserved queues exist.

Test files:

- `tests/core/monitor/policies/test_runtime_control.py`
  - Pure selector tests for actionability and deferred-only base.
- `tests/core/monitor/policies/test_dead_task.py`
  - Keep existing name parsing and cleanup-plan tests green.
- `tests/tasks/test_task_monitor.py`
  - Real broker-backed integration tests for worker result and catch-up
    scheduling.

Do not mock:

- broker queue creation
- queue names
- SimpleBroker delete results
- TaskMonitor worker-result scheduling, unless the test is specifically
  asserting a tiny result-application seam

Mocking is acceptable for:

- `deadline_reached()` in pure selector tests
- `task_record()` in pure selector tests, especially to prove it is not called
  for deferred-only families
- heartbeat registration, matching existing TaskMonitor tests

Required assertions:

- Deferred-only outbox/reserved families are counted but do not set pending.
- Actionable stale control/inbox families still set pending when bounded.
- Monitor-owned TIDs are skipped by dead-task cleanup.
- The monitor does not switch to catch-up cadence after a deferred-only
  dead-task slice.
- PONG exposes enough cached progress to explain the decision without live
  computation.

## 9. Verification And Gates

Per-task commands:

```bash
uv run pytest tests/core/monitor/policies/test_runtime_control.py -q
uv run pytest tests/core/monitor/policies/test_dead_task.py -q
uv run pytest tests/tasks/test_task_monitor.py -q
```

Final focused gates:

```bash
uv run pytest tests/core/monitor/policies/test_runtime_control.py tests/core/monitor/policies/test_dead_task.py tests/tasks/test_task_monitor.py -q
uv run pytest tests/core/test_monitor_sql.py tests/core/test_monitor_store.py tests/specs/test_plan_metadata.py -q
uv run mypy weft/core/monitor
uv run ruff check weft/core/monitor tests/core/monitor tests/tasks/test_task_monitor.py
```

Run the full suite before release if this lands with any other monitor-worker
or service scheduling changes:

```bash
uv run pytest
uv run bin/pytest-pg
```

Post-deploy success signal on ops:

- Monitor PING remains responsive.
- Worker CPU is quiet between normal monitor wakes.
- PONG for the deferred-only state shows `last_catchup_pending=false`.
- `runtime.dead_tid_cleanup` shows `selected=0`, `deferred>0`,
  `base_reached=true`, and `waypoint_reached=false`.
- When the oldest outbox crosses the retention threshold, cleanup may become
  active again, but then it should select and delete work rather than spin on
  selected-zero cycles.

## 10. Rollout And Rollback

Rollout:

- This is a code-only behavior fix. No schema migration and no queue-name
  contract change are required.
- It is safe for old queue data because it makes selection stricter and less
  eager. It does not delete any class of queue that was previously protected.

Rollback:

- Reverting this change restores the hot catch-up behavior but does not create
  incompatible persisted state.
- If rollback happens after some eligible cleanup deletes rows, those deletes
  are normal policy effects and cannot be undone. The fix must therefore keep
  the existing exact-delete protections and real-broker tests.

## 11. Out Of Scope

- Lowering `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`.
- Changing `WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS`.
- Adding a new Monitor table or schema version.
- Reworking service supervision or manager wakeup cadence.
- Replacing public SimpleBroker queue APIs with SQL against `weft.messages`.
- Changing external logging modes.
- Deleting young `T{tid}.outbox` or `T{tid}.reserved` queues.

## 12. Independent Review Loop

This plan is risky because it changes autonomous destructive cleanup scheduling.
Before implementation, request an independent engineering review, preferably
with the `plan-eng-review` skill.

Suggested prompt:

> Read `docs/plans/2026-05-25-monitor-dead-task-catchup-convergence-plan.md`
> and the relevant code in `weft/core/monitor/policies/runtime_control.py` and
> `weft/core/monitor/task_monitor.py`. Do not implement. Check whether the
> plan proves selection correctness, convergence, boundedness, and PONG
> observability. Look for ambiguities that could make the monitor either too
> hot or too quiet.

Review feedback must either be incorporated into this plan or explicitly
rejected with a reason before implementation.

## 13. Fresh-Eyes Review

Self-review completed after drafting:

- The plan names the exact bug: deferred-only dead-task cleanup reports a
  waypoint and keeps catch-up pending.
- The plan does not drift into lowering retention or deleting young outbox
  queues.
- The implementation tasks name the exact files and test seams.
- The proof standard covers selection correctness, convergence, and
  boundedness.
- The plan keeps PONG cached and avoids live debug computation.
- The plan keeps SimpleBroker broker-table SQL out of Weft.
- The remaining risk is that implementation could make the monitor too quiet
  when real actionable cleanup remains. Task 4 is included specifically to
  guard that countercase.
