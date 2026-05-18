# Reactive Task Loop Hot-Probe Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2]; docs/specifications/03-Manager_Architecture.md [MA-1.4], [MA-1.6a]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3], [MF-6]; docs/specifications/07-System_Invariants.md [QUEUE.4], [QUEUE.5], [MANAGER.15], [MANAGER.16]
Superseded by: none

## 1. Goal

Restore the intended reactive task-loop contract for long-lived Weft tasks,
starting with the production-hot Manager. A task should wake only for
backend-reported activity on watched queues or for a bounded time-based due
timer. Under the Postgres backend, this means ordinary idle waits block on the
SimpleBroker LISTEN/NOTIFY waiter, and local timer wakeups do not perform
synchronous `SELECT EXISTS` queue probes. Periodic discovery probes remain
allowed, but they must be explicit, time-bounded, and slow enough that an idle
manager cannot drive thousands of empty database reads per second.

The plan fixes the known root causes:

- foreground `weft manager serve` with `idle_timeout=0` schedules a stale
  broker-probe timer forever
- `MultiQueueWatcher.wait_for_activity(timeout=0)` polls all queues instead of
  returning immediately for a local due timer
- Weft ignores the boolean returned by SimpleBroker's native PG waiter
- `BaseTask` forces broad inactive-queue probing every drain via
  `check_interval=1`
- manager `.reserved` queues are treated as ordinary watched queues even when
  no reservation is known to exist

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.1], [CC-2.2]: `MultiQueueWatcher` owns queue fan-in and the shared
  task-loop wait seam; `BaseTask` owns the task reactor contract.
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.4], [MA-1.6a]: manager registry/leadership and managed internal
  services must advance on bounded timers plus queue activity, not through
  unbounded empty queue polling.
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
  [SB-0.4]: PG-backed watchers should share a process-local backend activity
  waiter across watched queues.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-6]: reservation, control queues, and manager spawn flow
  must keep existing semantics while scheduling changes.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [QUEUE.4], [QUEUE.5], [MANAGER.15], [MANAGER.16]: reservation authority and
  manager service convergence rules stay intact.

Related plans:

- [`2026-05-15-task-reactor-and-evidence-worker-plan.md`](./2026-05-15-task-reactor-and-evidence-worker-plan.md):
  completed. It introduced the shared reactor shape; this plan tightens the
  wake/readiness boundary that remained poll-shaped.
- [`2026-05-15-manager-reactor-hot-loop-follow-up-plan.md`](./2026-05-15-manager-reactor-hot-loop-follow-up-plan.md):
  completed. It fixed context churn and several manager-specific hot paths, but
  the new ops profile shows the watcher wait contract still permits empty SQL
  probe loops.
- [`2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md`](./2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md):
  completed. The SimpleBroker multi-queue waiter remains the backend wait
  primitive; do not replace it.

Observed ops evidence, 2026-05-18:

- `py-spy` on manager PID `2186862` showed `wait_for_activity(timeout=0)` in
  `MultiQueueWatcher._has_pending_messages()` issuing
  `SELECT EXISTS ... WHERE queue = 'weft.spawn.requests' AND claimed = FALSE`.
- A separate `py-spy` sample showed `_update_active_queues()` probing
  `T1779137169282412544.reserved`.
- Postgres `pg_stat_database` and `pg_stat_user_tables` counters over five
  seconds showed about 12,550 committed read transactions and about 12,565
  `messages` index scans, with no inserts, updates, or deletes.
- Queue state showed no `weft.spawn.*` rows and no manager reserved rows, so
  the SQL load was empty-probe churn rather than real backlog.

Guidance documents:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

## 3. Context And Key Files

Files to modify:

- `weft/core/tasks/multiqueue_watcher.py`: primary owner of wake reason,
  native waiter result handling, inactive-queue discovery cadence, and
  active-queue refresh.
- `weft/core/tasks/base.py`: shared task owner of the watcher configuration
  used by `Consumer`, `Manager`, `TaskMonitorTask`, and `HeartbeatTask`.
  Remove or replace the effective "probe every drain" behavior.
- `weft/core/manager.py`: manager-specific fixes for idle broker-probe timer
  scheduling, reserved-queue activity filtering, and public/internal spawn
  drains that currently call `_update_active_queues()` speculatively.
- `tests/tasks/test_multiqueue_watcher.py`: core regression tests for
  zero-timeout wait behavior, native waiter activity hints, and bounded
  inactive-queue discovery.
- `tests/core/test_manager.py`: manager regression tests for `idle_timeout=0`,
  foreground-serve behavior, reserved queues not participating in idle
  discovery, and public spawn drains requiring activity evidence.
- `tests/tasks/test_task_execution.py`: add or update shared `BaseTask`
  run-loop tests only if changing the `run_until_stopped()` / launcher wait
  contract.
- `weft/core/launcher.py`: touch only if a fix must change how
  `_task_process_entry()` treats `next_wait_timeout() == 0.0`.
- `docs/specifications/01-Core_Components.md`,
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/04-SimpleBroker_Integration.md`,
  `docs/specifications/05-Message_Flow_and_State.md`, and
  `docs/specifications/07-System_Invariants.md`: update related-plan
  backlinks and nearby implementation notes when implementation lands.
- `docs/lessons.md`: add a lesson after implementation if the fix confirms a
  reusable failure mode not already captured.

Read first:

- `weft/core/tasks/multiqueue_watcher.py` around
  `_ensure_multi_activity_waiter()`, `wait_for_activity()`,
  `_has_pending_messages()`, `_update_active_queues()`,
  `_process_queue_message()`, `_pending_non_peek_priorities()`, and
  `_drain_spawn_requests_from_queue()` call sites.
- `weft/core/tasks/base.py` around `BaseTask.__init__`,
  `run_until_stopped()`, `next_wait_timeout()`, `process_once()`, and
  `wait_for_activity()`.
- `weft/core/launcher.py` around `_task_process_entry()` because manager serve
  uses that loop rather than `BaseTask.run_until_stopped()`.
- `weft/core/manager.py` around `Manager.__init__`,
  `_read_broker_timestamp()`, `next_wait_timeout()`, `wait_for_activity()`,
  `process_once()`, `_build_queue_configs()`, `_has_actionable_leadership_work()`,
  `_manager_owned_work_pending()`, `_drain_public_spawn_requests()`, and
  `_drain_internal_spawn_requests()`.
- `../simplebroker/simplebroker/watcher.py` around
  `PollingStrategy.wait_for_activity()` and the activity-hint helpers.
- `../simplebroker/simplebroker/sbqueue.py` around
  `create_activity_waiter_for_queues(...)`.
- `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/runner.py`
  around `PostgresMultiQueueActivityWaiter.wait()`.

Current structure:

- SimpleBroker's PG extension already supplies one process-local shared
  LISTEN/NOTIFY listener per DSN/schema. `PostgresMultiQueueActivityWaiter`
  returns `True` when a watched queue notification is observed and `False` on
  timeout. It does not expose the specific queue name to Weft.
- `MultiQueueWatcher.wait_for_activity()` currently performs a synchronous
  `_has_pending_messages()` precheck for `timeout <= 0` and again before any
  positive-timeout native wait. That turns both due timers and ordinary sleeps
  into SQL queue probes.
- `MultiQueueWatcher.wait_for_activity()` currently ignores the return value
  from `waiter.wait(timeout)`. A real NOTIFY does not directly mark the next
  drain as activity-confirmed.
- `MultiQueueWatcher._update_active_queues()` refreshes active queues by
  calling `_queue_has_pending()` on existing active queues and by broad-probing
  every configured queue whenever `_check_counter % _check_interval == 0`.
- `BaseTask.__init__` sets `check_interval=1`, so every task built on
  `BaseTask` broad-probes inactive queues on every drain unless the watcher is
  changed.
- Manager registers its public spawn inbox, control queue, public reserved
  queue, internal spawn inbox, and internal reserved queue as watcher queues.
  The `.reserved` queues are recovery/diagnostic surfaces, not ordinary fresh
  work sources when no reservation is known.
- `weft manager serve` forces `idle_timeout=0.0`. `Manager.__init__` still
  calls `_read_broker_timestamp(force=True)`, setting `_last_broker_probe_ns`.
  `Manager.next_wait_timeout()` then schedules the broker-probe interval
  whenever `_last_broker_probe_ns > 0`, even when idle timeout is disabled.
  Since `process_once()` returns before idle tracking when `_idle_timeout <= 0`,
  that timer stays overdue forever.

Comprehension questions before editing:

- What is the difference between a local timer due and queue activity? A local
  timer authorizes local scheduled work; it does not by itself authorize
  synchronous `has_pending()` probes across watched queues.
- What does a PG `waiter.wait(timeout) == True` prove? It proves some watched
  queue may have changed. It is a hint to run a normal queue-drain discovery
  pass, not ownership of a specific message.
- When can Weft broad-probe inactive queues? Only after a native activity hint
  or on an explicit bounded periodic discovery timer. Turn count alone is not a
  valid clock.
- Why are manager `.reserved` queues different from inbox/control queues?
  Reserved rows are in-flight or recovery state for messages already claimed by
  this manager. They are not ordinary external work arrivals and should not
  wake an idle manager unless a local in-flight reservation path has already
  identified them as relevant.
- Which layer should be patched first? Patch Weft first. SimpleBroker should be
  changed only if Weft needs a feature the waiter does not currently expose,
  such as returning the notified queue name. The current failure does not
  require that feature.

## 4. Invariants And Constraints

- No queue names, payload formats, TID semantics, TaskSpec schema, CLI output
  shape, or persisted record formats change in this slice.
- The durable spine stays `TaskSpec -> Manager -> Consumer -> TaskRunner ->
  queues/state log`.
- Atomic reservation remains the authority for public spawn dispatch.
  NOTIFY is only a wake hint.
- A missed queue notification must not permanently strand work. Periodic
  inactive-queue discovery remains required, but it must be time-based and
  bounded.
- `next_wait_timeout() == 0.0` means "scheduled local work is due now." It must
  not mean "poll all watched queues now."
- If a native waiter is available and no local timer is due, the normal wait
  path should block on that waiter with the supplied timeout. It should not
  run a pre-wait SQL scan on every loop.
- Fallback behavior for backends with no native waiter may poll, but the poll
  rate must be tied to a configured timeout/backoff, not a tight zero-timeout
  loop.
- Manager service convergence still uses `reduce_managed_service_state`; this
  plan changes scheduling and evidence collection, not reducer semantics.
- Manager control queues remain promptly responsive. `ctrl_in` remains a
  watched queue and still participates in native waiter wakeups.
- A manager with in-flight child launch work or persistent user children must
  still honor existing shutdown/yield rules.
- Existing Queue handle ownership remains in `BaseTask` and `MultiQueueWatcher`.
  Do not create new queue handles in hot paths to avoid the probes.
- Do not patch SimpleBroker first unless a Weft test proves the PG waiter
  cannot express the required behavior. Current evidence points to Weft
  misusing the waiter result.
- Do not add a new dependency or a separate event loop framework.

Rollback:

- This is runtime scheduling only. Rollback is a code revert; no data
  migration or cleanup is required.
- The implementation must preserve compatibility with old queued messages and
  old manager/task records because it does not change any queue payload.
- If post-deploy work is stranded, rollback to the previous scheduling behavior
  should make the manager poll again. That is an acceptable emergency rollback
  even though CPU will regress.

Rollout:

- Ship Weft changes before any optional SimpleBroker enhancements.
- Deploy to one PG-backed ops environment first.
- Observe manager CPU, Postgres backend CPU, `pg_stat_database.xact_commit`,
  `pg_stat_user_tables.idx_scan` for `messages`, and py-spy stacks.
- Expected steady-state signal: manager and its paired Postgres backend are
  near idle between one-second/five-second scheduled checks; py-spy should show
  waiting on the PG activity waiter or local timed wait, not repeated
  `Queue.has_pending()`.

## 5. Tasks

1. Add failing regressions for zero-timeout waits and native activity hints.
   - Outcome: the current hot-loop behavior is captured before implementation.
   - Files to touch:
     - `tests/tasks/test_multiqueue_watcher.py`
   - Read first:
     - `weft/core/tasks/multiqueue_watcher.py`
     - `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/runner.py`
   - Tests:
     - Add a test where `wait_for_activity(timeout=0)` must not call
       `_has_pending_messages()` or `Queue.has_pending()`. Use a subclass or
       monkeypatch that raises on `_has_pending_messages()`; keep real broker
       queue construction for the watcher.
     - Add a test where a fake native waiter returns `True` and the next drain
       probes inactive queues exactly once and processes a real pending message.
     - Add a test where a fake native waiter returns `False` and the next drain
       does not broad-probe inactive empty queues before the periodic discovery
       deadline.
   - Constraints:
     - Do not mock SimpleBroker queue semantics. Mock only the waiter object or
       watcher private hook needed to simulate a native wake.
   - Done when:
     - At least one new test fails on the current implementation for the same
       reason observed in ops.

2. Fix `MultiQueueWatcher.wait_for_activity()` wake-reason handling.
   - Outcome: local timer waits do not poll queues, and native waiter activity
     is carried into the next drain.
   - Files to touch:
     - `weft/core/tasks/multiqueue_watcher.py`
   - Required action:
     - For `timeout is None` or `timeout <= 0`, return without calling
       `_has_pending_messages()`. This path is a local due-timer boundary, not
       queue discovery.
     - For native waiters, call `waiter.wait(timeout)` and, if it returns
       `True`, set `_pending_messages_precheck_confirmed = True`.
     - If `waiter.wait(timeout)` returns `False`, leave
       `_pending_messages_precheck_confirmed` unchanged.
     - Remove the unconditional pre-wait `_has_pending_messages()` scan from
       the native-wait path. If a missed-notify recovery check is still needed,
       implement it through the explicit periodic discovery timer in task 3.
   - Stop if:
     - The fix starts consuming messages in `wait_for_activity()`.
     - The fix needs queue names from the PG waiter; that is a new
       SimpleBroker feature and should be planned separately.
   - Done when:
     - The task 1 waiter tests pass.

3. Replace turn-count broad probing with a bounded time-based discovery pass.
   - Outcome: inactive queues are rediscovered after native activity or on a
     slow periodic timer, not every task turn.
   - Files to touch:
     - `weft/core/tasks/multiqueue_watcher.py`
     - `weft/core/tasks/base.py` only if constructor arguments need renaming
       or a new default cadence
     - `weft/_constants.py` only if an existing constant cannot express the
       cadence
   - Required action:
     - Introduce a monotonic timestamp such as `_next_inactive_probe_at` or
       `_last_inactive_probe_monotonic` in `MultiQueueWatcher`.
     - Define an inactive discovery interval. A conservative initial value is
       1.0 second for persistent tasks; do not use per-turn counters as the
       clock.
     - `_update_active_queues()` may broad-probe inactive queues only when
       `_pending_messages_precheck_confirmed` is true or the discovery timer is
       due.
     - After a broad probe, clear `_pending_messages_precheck_confirmed` and
       schedule the next discovery deadline.
     - Keep active-queue follow-up checks after actual message processing, but
       avoid probing inactive queues on every drain.
     - Respect `_queue_counts_as_wait_activity(config)` when choosing inactive
       queues to probe for fresh work. A queue excluded from wait activity
       should not be rediscovered as fresh work by the broad probe.
   - Constraints:
     - Do not remove periodic discovery entirely; missed notifications and
       fallback backends still need a safety net.
     - Do not set the periodic discovery interval below one second without new
       evidence.
   - Tests:
     - Add/adjust watcher tests proving an inactive queue is not probed before
       the discovery deadline.
     - Add/adjust watcher tests proving an inactive queue is discovered after
       the deadline even without native activity.
     - Keep or adapt the existing "wake precheck forces inactive queue probe"
       test so it is tied to a native activity hint rather than
       `wait_for_activity(timeout=0)`.
   - Done when:
     - Watcher tests prove no tight empty-probe loop is possible from repeated
       local due turns.

4. Fix manager idle broker-probe scheduling when idle timeout is disabled.
   - Outcome: foreground `weft manager serve` no longer creates a permanent
     overdue broker-probe timer.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Required action:
     - Do not schedule the `_last_broker_probe_ns + _broker_probe_interval_ns`
       timeout in `Manager.next_wait_timeout()` unless `_idle_timeout > 0`.
     - Prefer also avoiding the initial `_read_broker_timestamp(force=True)`
       in `Manager.__init__` when `_idle_timeout <= 0`; set
       `_last_broker_timestamp = 0` and `_last_broker_probe_ns = 0` for that
       mode.
     - Keep idle-timeout behavior unchanged when `_idle_timeout > 0`.
   - Tests:
     - Add a manager test where `idle_timeout=0.0`, all other timers are pushed
       into the future, `_last_broker_probe_ns` is stale, and
       `next_wait_timeout()` must not return `0.0`.
     - Add a companion test where `idle_timeout > 0` still schedules the broker
       probe deadline.
   - Done when:
     - The local reproduction from the ops root-cause investigation no longer
       returns zero for the stale broker-probe case.

5. Stop treating manager reserved queues as ordinary fresh work sources.
   - Outcome: manager `.reserved` queues are inspected only by explicit
     reservation/recovery paths, not by ordinary idle queue discovery.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Required action:
     - Override `_queue_counts_as_wait_activity()` in `Manager` so
       `T{manager}.reserved` and `T{manager}.internal_reserved` do not count as
       ordinary wait activity.
     - Keep `ctrl_in`, `weft.spawn.requests`, and `weft.spawn.internal` as
       ordinary activity queues.
     - Preserve explicit reserved checks in `_has_actionable_leadership_work()`,
       `_manager_owned_work_pending()`, reserved-policy cleanup, and exact
       spawn-request recovery paths. Those are targeted correctness checks, not
       background discovery.
   - Tests:
     - Add a manager test where broad discovery is due and empty reserved queues
       raise if probed; the manager should not touch them in ordinary inactive
       discovery.
     - Add or preserve a test proving an explicitly pending reserved message is
       still detected by the ownership/recovery path that is supposed to inspect
       it.
   - Stop if:
     - The implementation tries to remove reserved queues from TaskSpec queue
       naming or from operator recovery surfaces.
   - Done when:
     - py-spy's `_update_active_queues()` -> `T{manager}.reserved.has_pending`
       stack is impossible in ordinary idle discovery.

6. Make public spawn draining require activity evidence.
   - Outcome: the manager does not attempt empty `move_one()` or related
     public spawn drain work unless `weft.spawn.requests` was activated by
     native activity, periodic discovery, or explicit prechecked evidence.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Required action:
     - In `_drain_spawn_requests_from_queue()`, do not call
       `_update_active_queues()` unconditionally on each iteration if no wake
       or periodic discovery is due.
     - Use the watcher active set as the gate: if the queue is not active, do
       not attempt a move.
     - Preserve internal spawn immediacy after `_managed_internal_spawn_enqueued`
       writes to `weft.spawn.internal`; that local enqueue should mark
       pending evidence so the same manager turn can drain internal work under
       [MANAGER.16].
   - Tests:
     - Keep `test_manager_does_not_probe_inactive_public_spawn_queue` and
       strengthen it if needed so the manager does not attempt an empty public
       drain without evidence.
     - Keep `test_manager_pending_precheck_activates_public_spawn_queue` but
       update it to use native/periodic activity evidence if the precheck
       mechanics change.
   - Done when:
     - Empty `weft.spawn.requests` is not probed on every idle manager turn.

7. Update specs, lessons, and plan status after implementation.
   - Outcome: behavior and docs remain traceable.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/lessons.md`
     - this plan and `docs/plans/README.md`
   - Required action:
     - Add implementation notes stating that task waits are reactive to native
       activity and bounded periodic discovery, not zero-timeout queue polling.
     - Add or update related-plan backlinks.
     - Mark this plan `completed` only after code, tests, and docs land.
   - Done when:
     - `tests/specs/test_plan_metadata.py` passes.

## 6. Testing Plan

Use red-green TDD for the core regressions because the current failure is easy
to express locally.

Primary tests:

- `tests/tasks/test_multiqueue_watcher.py`
  - `wait_for_activity(timeout=0)` must not call pending scans.
  - native waiter `True` must mark the next inactive discovery pass.
  - native waiter `False` must not mark activity.
  - inactive queue discovery must be time-bounded, not turn-count bounded.
- `tests/core/test_manager.py`
  - `idle_timeout=0.0` must not schedule or preserve an overdue broker-probe
    timeout.
  - `idle_timeout>0` must keep existing idle-probe behavior.
  - manager reserved queues must not be probed by ordinary idle discovery.
  - public spawn drains must require active/evidence state.
  - internal spawn work created by service convergence must still drain in the
    same manager turn.

Test approach:

- Use `broker_env` and real SimpleBroker queues for queue semantics.
- Mock only native waiter return values, monotonic/time functions, or narrow
  private hooks where the private hook is the contract under review.
- Do not mock `Queue.move_one`, reservation semantics, TaskSpec state
  transitions, or manager child-launch commits in tests that claim to prove
  durable behavior.
- For tests that need to prove "no empty probe," use a subclass or monkeypatch
  that raises on `_has_pending_messages()` or `_queue_has_pending()` for the
  forbidden queue. Keep the surrounding watcher/manager using real queues.
- Add one targeted test that reproduces the stale broker-probe zero timeout
  without sleeping.

Post-deploy proof:

- On the PG-backed ops host, sample manager and Postgres CPU with `top`.
- Run `py-spy dump --pid <manager-pid>` and confirm idle stacks are in
  SimpleBroker PG waiter/local timed wait or scheduled manager work, not
  repeated `Queue.has_pending()`.
- Sample Postgres counters for five seconds:
  `pg_stat_database.xact_commit` and `pg_stat_user_tables.idx_scan` for
  `messages` should no longer climb by thousands while queues are empty.
- Confirm `weft.spawn.requests`, `weft.spawn.internal`, and manager reserved
  queues still process real submitted work.

## 7. Verification And Gates

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
```

Final local gates:

```bash
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/core/test_manager.py tests/tasks/test_task_execution.py tests/tasks/test_heartbeat.py tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/ruff check weft tests docs
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
git diff --check
```

If the implementation touches `weft/core/launcher.py`, also run:

```bash
./.venv/bin/python -m pytest tests/specs/message_flow tests/cli/test_cli_serve.py tests/commands/test_run.py -q
```

Completion gate:

- The new red tests fail before the relevant implementation and pass after it.
- No task or manager test relies on mocked queue semantics for the core proof.
- Docs and plan metadata tests pass.
- Ops follow-up shows the manager no longer drives high empty-read transaction
  rates against Postgres while queues are empty.

## 8. Independent Review Loop

External review is required because this changes shared task scheduling and can
silently strand work if implemented incorrectly.

Reviewer prompt:

> Read `docs/plans/2026-05-18-reactive-task-loop-hot-probe-plan.md` and the
> associated code in `weft/core/tasks/multiqueue_watcher.py`,
> `weft/core/tasks/base.py`, `weft/core/manager.py`, and
> `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/runner.py`.
> Look for errors, bad ideas, missing invariants, and latent ambiguities. Do
> not implement anything. Could you implement this confidently and correctly if
> asked?

Preferred reviewer:

- A different agent family through the available local agent runner, if
  available. If no external reviewer is available, keep the plan in
  review-pending state and record that limitation.

Feedback handling:

- Treat "could strand work" or "unclear wake proof" findings as blockers.
- Update this plan for accepted findings before implementation.
- If disagreeing with a finding, record the reason in the plan's fresh-eyes
  review section or in the implementation handoff.

## 9. Out Of Scope

- Changing SimpleBroker's PG waiter API to expose the exact notified queue.
  That can be considered later, but this failure does not require it.
- Redesigning reservation policy, queue naming, TaskSpec schema, or manager
  registry payloads.
- Removing periodic discovery entirely.
- Making `.reserved` queue recovery automatic or changing operator recovery
  commands.
- Retuning Postgres indexes or database configuration as the primary fix.
  The root issue is excessive empty reads.
- Rewriting `MultiQueueWatcher` into an asyncio/event-loop framework.
- Broad cleanup of historical manager service-convergence plans.

## 10. Fresh-Eyes Review

Self-review status: completed on 2026-05-18 after drafting.

Findings:

- The first draft risked over-correcting by removing inactive queue discovery
  entirely. The plan now explicitly keeps a time-based periodic discovery
  pass to protect missed notifications and fallback backends.
- The first draft did not separate local timer due from queue activity clearly
  enough. The plan now states that `next_wait_timeout() == 0.0` must not
  authorize queue polling.
- The first draft could have been read as "reserved queues never get checked."
  The plan now keeps explicit reserved checks for recovery, reserved policy,
  and ownership proof, while excluding them from ordinary idle discovery.
- The plan still has one residual risk: existing tests may rely on
  `wait_for_activity(timeout=0)` as a manual precheck helper. Those tests
  should be rewritten to express a native activity hint or a due periodic
  discovery pass. Do not preserve the old helper behavior just for tests; it
  is the production bug.

External review status: external review unavailable before implementation.
Attempted external review on 2026-05-18 through the local agent runner, but no
independent review was returned:

- `claude_code`: timed out at the tool boundary after 120 seconds without a
  returned job id.
- `codex`: failed because the configured Codex binary was missing from the
  provider path.
- `gemini`: refused to run because the workspace was not trusted by the Gemini
  CLI in this execution context.
- `qwen`: timed out at the tool boundary after 120 seconds.

The user explicitly asked to implement per plan after these attempts. The
implementation proceeded without an external review result; the missing review
is a residual process gap, not a runtime blocker.

## 11. Implementation Notes

Completed on 2026-05-18.

- `MultiQueueWatcher.wait_for_activity(timeout=0)` now returns immediately
  without scanning queues.
- Native multi-queue activity waiters now set a pending discovery hint when
  `waiter.wait(timeout)` returns `True`.
- Backends without a native waiter keep a bounded positive-timeout pending
  precheck as the portable polling fallback.
- Inactive queue discovery is time-bounded by
  `TASK_INACTIVE_QUEUE_DISCOVERY_INTERVAL_SECONDS` instead of driven by
  per-turn counters.
- Manager foreground service mode with `idle_timeout=0` skips initial and
  scheduled broker timestamp probes.
- Manager reserved queues are excluded from ordinary wait/discovery activity
  and are activated only by explicit reserved-work proof paths.
- Public spawn draining now asks the watcher for activity state once before a
  bounded drain, instead of refreshing active queues on every drain iteration.
