# Manager Hot-Loop Reduction Plan

Status: draft
Source specs: docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2]; docs/specifications/03-Manager_Architecture.md [MA-1.1], [MA-1.4], [MA-1.6a], [MA-1.7]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-6], [MF-7]; docs/specifications/07-System_Invariants.md [MANAGER.3], [MANAGER.8], [MANAGER.12], [MANAGER.15], [MANAGER.16], [OBS.13]
Superseded by: [`2026-05-15-task-reactor-and-evidence-worker-plan.md`](./2026-05-15-task-reactor-and-evidence-worker-plan.md)

## 1. Goal

Reduce manager CPU by making the manager reactive to the existing shared task
loop instead of running most supervisory work on the 50 ms manager turn. This
should also turn the existing Monitor-style shape into a repeatable task-loop
contract: `process_once()` performs one bounded turn, `next_wait_timeout()`
returns the next cheap local due timer, and `wait_for_activity()` blocks on
queue activity with that timeout as a ceiling.

The one intentional hot runtime poll should remain the SimpleBroker
activity-wait path used by `MultiQueueWatcher`; manager service convergence,
leadership proof, child terminal proof, registry replay, idle broker probes,
and similar supervisory checks should be due timers or reactions to concrete
progress. Those timers should feed the same loop through `next_wait_timeout()`,
not create independent loops, sleeps, or scanners.

This plan supersedes
[`2026-05-13-service-convergence-throttle-plan.md`](./2026-05-13-service-convergence-throttle-plan.md).
That draft correctly identified the one-second service-convergence cadence, but
the production profile shows a broader scheduling problem: pre-throttle
leadership probes, repeated tracked-service proof scans, repeated queue
open/close cycles, global task-log replay, and manager-level waits that can
probe the broker before delegating to the shared queue waiter. The revised
implementation should start by codifying the task-loop hook in `BaseTask`, then
make the Manager the first hard implementation of that contract.

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.1], [CC-2.2]: `MultiQueueWatcher` and `BaseTask` own the shared task
  loop, queue waiting, queue handles, and task-shaped control behavior.
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.1], [MA-1.4], [MA-1.6a], [MA-1.7]: public/internal spawn dispatch,
  manager registry heartbeat and leadership, manager-owned internal service
  convergence, and control/PING handling.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-3], [MF-6], [MF-7]: task-local control, manager spawn flow, and manager
  runtime state.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [MANAGER.3], [MANAGER.8], [MANAGER.12], [MANAGER.15], [MANAGER.16],
  [OBS.13]: service-owner rows, duplicate-manager convergence, atomic
  reservation authority, internal singleton convergence, and operational
  diagnostics not becoming lifecycle truth.

Related plans:

- [`2026-05-13-service-convergence-throttle-plan.md`](./2026-05-13-service-convergence-throttle-plan.md):
  older draft, superseded by this plan because it only covered the coarse
  convergence interval.
- [`2026-05-13-manager-liveness-and-leadership-robustness-plan.md`](./2026-05-13-manager-liveness-and-leadership-robustness-plan.md):
  completed manager liveness plan. This plan preserves its strong-live
  leadership rule while avoiding repeated proof work before the leadership
  interval.
- [`2026-05-11-manager-work-stealing-dispatch-plan.md`](./2026-05-11-manager-work-stealing-dispatch-plan.md):
  draft context for public spawn work-stealing. This plan preserves the core
  rule that broker reservation, not registry leadership, owns public spawn
  exclusivity.
- [`2026-05-10-manager-service-authority-boundary-hardening-plan.md`](./2026-05-10-manager-service-authority-boundary-hardening-plan.md):
  completed authority hardening for manager-owned services. This plan keeps
  service authority rules intact while changing when proof is collected.
- [`2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md`](./2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md):
  completed multi-queue waiter integration. This is the intended hot wait path
  to preserve.
- `TaskMonitorTask` in `weft/core/tasks/task_monitor.py`: current reference
  shape for a launched persistent task that uses `next_wait_timeout()` instead
  of the launcher's 50 ms fallback while waiting for its own due work.
- `HeartbeatTask` in `weft/core/tasks/heartbeat.py`: adjacent pattern to align
  later. It has the same due-timer idea, but currently hides it behind a private
  `_next_wait_timeout()` and an inner wait loop inside `process_once()`.

Guidance:

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

- `weft/core/tasks/base.py`: owner of the reusable task-loop contract. Add a
  default `next_wait_timeout()` hook and make `run_until_stopped()` honor it
  using the same semantics as the launcher.
- `weft/core/launcher.py`: already calls `next_wait_timeout()` for spawned task
  processes. Touch only if tests expose a mismatch with the `BaseTask` helper
  semantics.
- `weft/core/manager.py`: primary owner of manager `process_once`,
  leadership yield checks, managed service convergence, tracked-child evidence,
  internal/public spawn drains, manager registry heartbeat, and idle broker
  probes.
- `weft/core/tasks/task_monitor.py`: compatibility/reference owner only. Do not
  redesign Monitor in this slice; use it to keep the shared contract honest.
- `weft/core/tasks/heartbeat.py`: documentation/follow-up reference only unless
  a narrow test fixture needs to assert current divergence. Full Heartbeat
  alignment is a later slice.
- `weft/core/manager_services.py`: only if the reducer state needs a small
  field to remember evidence freshness. Do not move side effects into this pure
  reducer module.
- `weft/core/tasks/multiqueue_watcher.py`: only if a test proves manager code
  is bypassing the intended waiter contract. Do not redesign the watcher in
  this slice.
- `weft/_constants.py`: only for narrowly named cadence or cache constants.
  Prefer existing constants when they fit.
- `tests/core/test_manager.py`: primary regression tests for manager
  convergence, leadership yield cadence, tracked-service evidence, and hot-loop
  budgets.
- `tests/tasks/test_base_task_loop.py` or the nearest existing BaseTask test
  module: regression tests for `BaseTask.run_until_stopped()` honoring
  `next_wait_timeout()`.
- `tests/tasks/test_task_monitor.py`: only for compatibility assertions that
  the Monitor-style `next_wait_timeout()` contract still behaves as expected.
- `tests/tasks/test_multiqueue_watcher.py`: only if watcher semantics are
  touched.
- `docs/specifications/01-Core_Components.md`,
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/05-Message_Flow_and_State.md`, and
  `docs/specifications/07-System_Invariants.md`: update backlinks. Update
  [MA-1.6a] implementation notes and contract wording in the same slice if the
  final implementation changes terminal-proof, manager-wait, or
  leadership-check timing.
- `docs/lessons.md`: add a lesson if the implemented fix confirms a repeated
  pattern, such as proof work being placed before a rate gate.
- `docs/plans/README.md`: keep the plan index synchronized.

Read first:

- `weft/core/manager.py`:
  `Manager.process_once`, `Manager.wait_for_activity`,
  the new `Manager.next_wait_timeout` method to add,
  `Manager._maybe_yield_leadership`,
  `Manager._has_actionable_leadership_work`,
  `Manager._run_managed_service_convergence`,
  `Manager._managed_service_convergence_active_reasons`,
  `Manager._reconcile_managed_services`,
  `Manager._tick_managed_service`,
  `Manager._tracked_service_candidate`,
  `Manager._child_terminal_proof_visible`,
  `Manager._observed_service_candidates_by_key`,
  `Manager._pending_service_keys`,
  `Manager._drain_internal_spawn_requests`,
  `Manager._drain_public_spawn_requests`, and
  `Manager._update_idle_activity_from_broker`.
- `weft/core/manager_services.py`:
  `ManagedServiceState`, `ServiceCandidate`,
  `summarize_service_candidates`, and `reduce_managed_service_state`.
- `weft/core/service_convergence.py`:
  service-owner row parsing, latest-owner reduction, and prune planning.
- `weft/core/tasks/multiqueue_watcher.py`:
  `wait_for_activity`, `_has_pending_messages`, `_update_active_queues`, and
  `_drain_queue`.
- `weft/core/tasks/base.py`:
  `BaseTask.run_until_stopped`, `BaseTask.process_once`, and the new default
  `BaseTask.next_wait_timeout`.
- `weft/core/launcher.py`:
  `_task_process_entry` lines that already call `next_wait_timeout()`.
- `weft/core/tasks/task_monitor.py`:
  `TaskMonitorTask.next_wait_timeout` and `TaskMonitorTask.process_once` as the
  reference shape.
- `weft/core/tasks/heartbeat.py`:
  `HeartbeatTask.process_once`, `_next_wait_timeout`, and `_wait_for_activity`
  as the follow-up divergence to document.
- Existing tests in `tests/core/test_manager.py` around managed-service
  convergence intervals, active reasons, leadership yield, and manager
  service convergence without dispatch ownership.

Current production evidence:

- `weft-manager-1778802660.svg`: py-spy flamegraph for PID `2777243`.
- `weft-manager.strace`: syscall summary from the same investigation.
- `weft-manager-stacks.txt`: process header only, not useful for stack
  attribution.

The flamegraph showed samples in:

- `Manager.process_once`
- `_run_managed_service_convergence`
- `_reconcile_managed_services`
- `_tracked_service_candidate`
- `_child_terminal_proof_visible`
- `_maybe_yield_leadership`
- `_read_active_manager_records`
- `iter_queue_json_entries`
- SimpleBroker `peek_generator`, `peek_one`, `has_pending`, `get_connection`,
  and `close`

The syscall summary showed high churn in `openat`, `close`, `futex`,
`recvfrom`, `poll`, and related connection/thread activity. That pattern fits
repeated broker queue construction and history scanning better than a single
CPU-bound Python loop.

Comprehension questions before editing:

1. Which sources should wake the manager immediately, and which sources should
   only set the next due timeout for the same loop?
2. Which methods in `Manager.process_once` launch or drain real accepted work,
   and which methods only prove liveness or ownership?
3. Which evidence paths are allowed to replay append-only queues, and what
   cadence should bound each replay?
4. Why does public spawn dispatch stay correct even if leadership proof is
   delayed by a due timer?
5. When does child terminal proof have to win immediately, and when is it safe
   to wait for manager-local child reaping?
6. Which queue or process event should wake service convergence earlier than
   its ordinary interval?
7. Why did `Manager.wait_for_activity()` previously avoid backend activity
   waiters under Postgres load, and how does the new plan preserve that ceiling?
8. What does `TaskMonitorTask.next_wait_timeout()` already prove about the
   intended launcher contract, and which parts are Monitor-specific?
9. Why is `HeartbeatTask` not the shape to copy directly despite having the same
   due-timer concept?

## 4. Target Model

The target shape is a reusable task-loop contract, with Manager as the first
hard implementation and `TaskMonitorTask` as the compatibility reference:

1. `process_once()` drains ready work and handles local progress.
2. `next_wait_timeout()` computes the nearest due timer across manager-owned
   or task-owned supervisory work from cheap local state.
3. `wait_for_activity(timeout=...)` uses the shared `MultiQueueWatcher` wait
   path only under a hard timeout ceiling so queue activity can wake the
   manager early without extending manager supervision ticks under Postgres
   load.
4. The next turn handles whichever source became ready: queue activity, child
   progress, operator control, or an expired due timer.

`BaseTask` should own the existence of the hook, not Manager. The default
`BaseTask.next_wait_timeout()` should return `None` so existing task behavior
does not change by accident. `BaseTask.run_until_stopped()` should honor the
hook with the same semantics as `launcher._task_process_entry`: start from the
caller-provided fallback `poll_interval`, then replace it only when
`next_wait_timeout()` returns a non-`None` value.

`TaskMonitorTask` is the model for the public hook shape: it already exposes
`next_wait_timeout()` and keeps `process_once()` bounded. Manager should follow
that launcher-owned outer-loop shape. `HeartbeatTask` is deliberately not the
model to copy: it has the right due-timer concept, but it hides the hook as
`_next_wait_timeout()` and performs its own wait loop inside `process_once()`.
Heartbeat alignment should be a follow-up after the Manager proves the shared
contract under harder runtime constraints.

The manager loop should separate three categories of work:

- **Hot queue wait and drain**: `MultiQueueWatcher` and direct bounded spawn
  drains may run on every manager wake. This is where the SimpleBroker PRAGMA
  wait/poll belongs. Manager code should not add pre-wait broker scans that
  duplicate this path.
- **Progress-triggered manager work**: child reaping, accepted internal spawn
  drains, public spawn reservations, and operator control may bypass ordinary
  service proof intervals because they reflect concrete local state changes.
- **Supervisory proof work**: service liveness checks, service-registry
  replay, duplicate-service scans, manager leadership proof, PING fallback, and
  task-log terminal-proof replay must be bounded by due timers, per-turn memo,
  or explicit progress triggers. They must not run merely because the manager
  woke.

Desired steady-state behavior:

- Manager turn: runs when queue activity arrives, local process progress is
  observed, operator control arrives, or the nearest manager due timer expires.
  `MANAGER_POLL_INTERVAL` should not be the default cadence for all
  supervisory proof work.
- Queue activity wait: may spin inside SimpleBroker internals as designed.
- Active service convergence proof: about once per second.
- Stable service audit: about every five seconds.
- Leadership proof: due at the configured leadership interval, never more than
  once per manager turn.
- Global task-log scans from manager service convergence: zero in the ordinary
  live-child path. They are allowed only for targeted recovery, child-exit
  publication, or operator/diagnostic paths outside the hot loop.

The design should prefer event or progress sources over timers where a source
already exists. Queue messages wake through `MultiQueueWatcher`. Manager-local
accepted work sets local flags. Child exit should be handled through local
process evidence, and if an OS-level process sentinel can be integrated without
new dependencies or a second scheduler, it is preferred over a short polling
timer. If a timer remains because no event source exists, the code must name the
owner and the cadence.

Important counterargument: the current `Manager.wait_for_activity()` override
intentionally avoids letting backend activity waiters extend the supervision
tick under Postgres load. The implementation must not blindly delete that
rationale. The acceptable change is narrower: introduce manager due timers and
use the shared waiter only when the timeout is treated as a hard ceiling. If the
SimpleBroker backend waiter cannot be proven to honor the timeout under load,
Task 3 blocks and the plan must be revised instead of routing manager sleep
through that waiter.

## 5. Invariants And Constraints

- Do not slow STOP, KILL, PING, or STATUS handling. The manager must still
  drain its control queue promptly.
- Do not slow accepted public or internal spawn work. Atomic reservation stays
  the dispatch authority for public spawn requests.
- Do not add a second manager scheduler, thread, control plane, task-log
  scanner, or side-channel store.
- Do not add a new scheduler framework or due-timer abstraction in this slice.
  The reusable change is the hook contract, not a new runtime component.
- All manager timers must feed the existing task-loop contract through
  `next_wait_timeout()` and `wait_for_activity()`.
- `BaseTask.next_wait_timeout()` must default to `None`, preserving existing
  fallback poll behavior for task types that do not opt in.
- `BaseTask.run_until_stopped()` and `launcher._task_process_entry()` must use
  matching timeout semantics.
- Do not change queue names, TaskSpec schema, result payloads, public CLI
  output, or service-owner payload schema unless implementation proves a spec
  update is unavoidable.
- Preserve the existing durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Preserve manager-owned service authority rules. Caller-owned TaskSpec
  metadata alone must not become service authority.
- Preserve runtime-only semantics for `weft.state.*` queues.
- Preserve generator-based history reads where history replay is still
  correctness-critical. Do not replace them with fixed-limit peeks.
- Do not treat manager operational logs as lifecycle truth.
- Do not add dependencies.
- Use real broker-backed tests for queue and manager behavior. Mocking is
  acceptable for counting whether a hot proof method was called, but not as the
  only proof that spawn/control behavior remains correct.

Review gates:

- Self-driven fresh-eyes review is required before reporting the plan complete.
- Initial external plan review is required before implementation-ready signoff
  because this changes runtime scheduling and manager/service convergence
  behavior. Reviews have been completed for both the manager-reactor draft and
  the later BaseTask rework; see Sections 11 and 12.
- A second external review is required after implementation before completion.
- Stop and re-plan if the implementation starts changing service-owner schema,
  public status semantics, or TaskSpec resolution.

## 6. Out Of Scope

- Rewriting `weft status --json` or task-list history reconstruction.
- Replacing SimpleBroker wait primitives.
- Redesigning `weft.state.services`.
- Changing task-monitor cleanup policy.
- Adding a global profiler or continuous telemetry feature.
- Making service convergence intervals user-configurable.
- Broadly refactoring `Manager` into smaller classes.
- Fully refactoring `HeartbeatTask` to the shared contract. This plan should
  document the desired follow-up and avoid copying Heartbeat's inner wait loop
  into Manager.
- Extracting a general due-source registry, scheduler class, or timer heap
  helper. That may become useful later, but this slice should prove the simpler
  hook contract first.

## 7. Tasks

### Task 1: Codify The Shared Task-Loop Hook In BaseTask

Owner: `weft/core/tasks/base.py`; tests in the nearest BaseTask or task-loop
test module.

Add the minimal reusable contract before changing Manager behavior:

- Add `BaseTask.next_wait_timeout() -> float | None` returning `None`.
- Update `BaseTask.run_until_stopped()` to use the same timeout semantics as
  `launcher._task_process_entry`: start from `poll_interval`; if
  `next_wait_timeout()` returns a non-`None` value, use `max(0.0,
  float(candidate_timeout))`; then call `wait_for_activity(timeout=...)`.
- Preserve the existing skip behavior when `poll_interval <= 0` and
  `next_wait_timeout()` returns `None`. If the hook returns a non-`None` value,
  including `0.0`, call `wait_for_activity(timeout=...)` so the helper matches
  launcher semantics for opt-in reactive tasks.
- Do not change `BaseTask.process_once()` behavior.
- Grep for existing `next_wait_timeout` attributes or methods before editing.
  At the time this plan was written, `TaskMonitorTask` exposes the public hook,
  Heartbeat exposes only private `_next_wait_timeout()`, and Manager has no
  hook.
- Do not make the default hook account for poll reporting in this slice. A
  default `None` preserves existing behavior. If a later slice wants BaseTask to
  stretch waits by default, it must include `reporting_interval == "poll"` as a
  due source.
- Add a small test task subclass that returns `0.0`, a positive timeout, and
  `None` from `next_wait_timeout()` and proves `run_until_stopped()` passes the
  expected timeout to `wait_for_activity()`.
- Add or keep a compatibility assertion that `TaskMonitorTask.next_wait_timeout`
  remains a public hook consumed by the launcher.

Stop gate: if this requires changing queue semantics, control handling, or the
launcher loop, stop and re-plan. The BaseTask slice should only codify the hook
that already exists in the launcher.

Implementation note: once `BaseTask.next_wait_timeout()` exists, the launcher
could call the method directly for `BaseTask` instances, but this cleanup is not
required. Keeping the existing `getattr` path preserves the launcher's current
duck-typed behavior for non-BaseTask test doubles or future task-like objects.

### Task 2: Lock The Hot-Loop Budget With Tests

Owner: `tests/core/test_manager.py`.

Add tests before changing behavior where possible:

- A manager `next_wait_timeout()` test should prove the method is newly
  introduced, returns the nearest due source, and lets queue activity wake the
  manager before that timeout expires.
- `Manager.wait_for_activity()` should not do an unconditional broker
  pre-scan on every idle turn when the shared `MultiQueueWatcher` waiter can do
  the blocking wait.
- `Manager.wait_for_activity()` must return no later than the requested timeout
  in a backend-waiter failure or slow-wait simulation. This protects the
  existing Postgres-load supervision ceiling.
- A throttled `_run_managed_service_convergence()` call must return without
  calling `_internal_spawn_pending`, `_cleanup_children`,
  `_reconcile_managed_services`, or `_observed_service_candidates_by_key` when
  there is no local progress flag and the interval has not expired.
- Multiple `_maybe_yield_leadership()` calls inside one manager turn must not
  run `_has_actionable_leadership_work()` or `_read_active_manager_records()`
  more than the intended cadence requires.
- `_reconcile_managed_services()` must not call `_tracked_service_candidate()`
  repeatedly for the same service key in one pass.
- `_tracked_service_candidate()` must not call
  `_child_terminal_proof_visible()` for an obviously live manager-tracked child
  on the ordinary service convergence path.

Use narrow monkeypatch call counters for these tests, but also keep at least
one real broker-backed manager test that proves pending internal spawn work is
still drained immediately.

Stop gate: if a test needs broad mocks for queue reservation or child launch,
the plan is drifting. Use existing broker fixtures and manager helpers instead.

### Task 3: Put Manager Scheduling Behind The Core Reactive Loop

Owner: `weft/core/manager.py`.

After Task 1 codifies the hook in `BaseTask`, make the manager implement
`next_wait_timeout()` so the launcher's existing task loop becomes the only
manager scheduler.

Required behavior:

- Explicitly add `Manager.next_wait_timeout()`. It does not exist today; the
  launcher's `getattr(task, "next_wait_timeout", None)` hook is already ready
  to consume it.
- Compute the next timeout from manager-owned due sources:
  active service convergence (`MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS`,
  1.0s), stable service audit
  (`MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS`, 5.0s), leadership proof
  (`_leader_check_interval_ns`, currently 100 ms), manager registration refresh
  (`MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS`, 30s), idle broker probe
  (`_broker_probe_interval_ns`, currently 1.0s), idle shutdown deadline
  (`_last_activity_ns + idle_timeout`), autostart scan/backoff due time
  (`_autostart_last_scan_ns + _autostart_scan_interval_ns`, including
  `_schedule_autostart_rescan_at()` adjustments), public dispatch stall log
  throttle (`MANAGER_DISPATCH_STALL_LOG_INTERVAL_SECONDS`, 5.0s), and
  child/process liveness when no process-sentinel integration is landed.
- Queue activity should wake the manager through `MultiQueueWatcher` before the
  computed timeout expires, but the computed timeout remains a hard ceiling for
  supervision progress.
- `wait_for_activity()` should not perform a broad manager broker scan before
  entering the shared waiter. It may do cheap local stop/progress checks and may
  preserve the existing `manager_wait_immediate_wake` trace event through a
  narrow hook after the inherited pending precheck confirms work.
- Remove one duplicate precheck, not both. `MultiQueueWatcher.wait_for_activity`
  already calls `_has_pending_messages()` and marks the precheck flag; the
  manager should either become a thin wrapper around that behavior or delegate
  after proving the timeout ceiling above.
- Due timers should set local "ready" state or allow the next `process_once()`
  turn to run the relevant work. They should not run work inside
  `next_wait_timeout()` or `wait_for_activity()`.
- Idle broker activity checks must follow the same due-source model:
  `_update_idle_activity_from_broker(force=False)` may run on its one-second
  probe cadence, and the current forced check near the idle-shutdown decision
  should run only when the idle deadline is near or a concrete race-sensitive
  shutdown decision is being made. It should not be an every-turn broker scan.
- Keep `MANAGER_POLL_INTERVAL` unchanged for manager runtime call sites that
  pass a launcher fallback interval today. Once `Manager.next_wait_timeout()`
  exists, the manager should normally override that fallback with a due-source
  timeout. If `next_wait_timeout()` returns `None`, the launcher still falls
  back to `MANAGER_POLL_INTERVAL`; that path must be treated as exceptional, not
  the steady-state manager cadence.
- If child exit cannot be made event-driven in this slice, name the surviving
  poll cadence explicitly. Use the existing `MANAGER_CHILD_EXIT_POLL_INTERVAL`
  only for active child-exit observation and the existing shutdown-drain waits,
  not as a reason to run all supervisory proof work every 50 ms.

Verification:

- Targeted tests from Task 2.
- Existing launcher/task-loop tests must still pass.
- A real broker-backed manager test should show queued control or spawn work is
  processed before the longest due timer expires.

### Task 4: Move Expensive Checks Behind Due Gates

Owner: `weft/core/manager.py`.

Change `_run_managed_service_convergence()` so the ordinary throttled path can
return before broker reads. In particular:

- Split `_managed_service_convergence_active_reasons()` into cheap-local
  reasons and broker-backed reasons. Cheap-local reasons are:
  `_managed_internal_spawn_enqueued`,
  `_managed_service_duplicate_scan_pending`, manager-local service states with
  `spawn_pending`, internal service states with `active_tid is None`,
  `uncertain_attempts > 0`, and autostart scan/backoff due from
  `_autostart_last_scan_ns` and `_autostart_scan_interval_ns`.
- Do not call `_internal_spawn_pending()` before the interval gate. A pending
  internal-spawn queue message is handled by the direct
  `_drain_internal_spawn_requests()` call in `process_once()` and by queue
  activity waking the main loop. It becomes a broker-backed active reason only
  after the convergence due gate opens or `force=True` is in effect.
- On a throttled return, use the active interval when cheap-local active
  reasons exist and the stable-audit interval otherwise. Do not require a
  broker probe merely to decide which interval applies.
- Treat `_managed_internal_spawn_enqueued` as a local bypass flag because it is
  set by this manager when it writes internal work.
- Let the explicit `_drain_internal_spawn_requests()` call in
  `process_once()` remain the immediate progress authority for already queued
  internal spawn work.
- Keep `force=True` as an interval bypass for concrete progress, such as child
  exit cleanup.
- The closed set of interval bypasses is: startup/explicit `force=True`,
  `force=True` from `process_once()` after observed child exit,
  `_managed_internal_spawn_enqueued` set by this manager's own writes, and
  direct internal-spawn drains that already made local progress. Do not add new
  `force=True` callers without updating this plan and [MA-1.6a].

Expected shape:

- cheap local flags first
- interval gate second
- broker-backed active reasons and evidence only after the gate opens or
  `force=True`

Verification:

- Targeted tests from Task 2.
- Existing test `test_manager_convergence_drains_pending_internal_spawn_work`
  must still pass.
- Existing forced convergence tests must still pass.

### Task 5: Memoize Leadership Work Per Turn And Respect The Rate Gate First

Owner: `weft/core/manager.py`.

Change `_maybe_yield_leadership()` so repeated calls in one `process_once()`
turn do not repeatedly probe queues or replay manager registry state.

Required behavior:

- If `force=False` and the leadership interval has not elapsed, return before
  `_has_actionable_leadership_work()`.
- If the interval has elapsed, compute actionable leadership work at most once
  for that turn and reuse the answer for later calls in the same turn.
- Keep `force=True` as an explicit bypass for startup and tests.
- Do not weaken the rule that a manager must not voluntarily yield when it owns
  active reserved/internal/control work.

Implementation hint:

- Reset a small per-turn cache at the start of `process_once()` beside
  `_leader_probe_used_this_turn`.
- Cache only the result of hot proof predicates, not durable decisions or
  queue messages.
- Invalidate the per-turn cache whenever the manager drains accepted work,
  launches a child, observes child exit, or writes internal spawn work later in
  the same turn. A stale "no actionable work" answer must not let the manager
  yield after it just accepted responsibility for work.

Verification:

- Tests that call `_maybe_yield_leadership()` multiple times with a monkeypatch
  counter.
- Tests that pending internal spawn work still blocks yield when the leadership
  interval is due.
- Existing leadership yield/drain tests must still pass.

### Task 6: Stop Scanning Global Task Log For Live Tracked Children

Owner: `weft/core/manager.py`.

Refactor `_tracked_service_candidate()` and `_child_terminal_proof_visible()`
so ordinary convergence does not scan task-local `ctrl_out` and
`weft.log.tasks` for every live manager-tracked child.

Required behavior:

- If the manager has a tracked child process and `_child_has_exited(child)` is
  false, the ordinary service convergence path may use that tracked child as
  live local evidence without replaying the global task log.
- Terminal proof scanning remains available when the child is being reaped,
  when manager-authored terminal envelope publication needs to avoid
  duplication, or when a targeted recovery path explicitly asks for it.
- If task-owned terminal proof appears while the process is still unwinding,
  public status/result readers may still report terminal state. The manager
  service convergence hot path may wait for local child reap before restarting
  the singleton. This is the intended tradeoff: slightly slower singleton
  replacement beats global task-log scans every second or every 50 ms.

Spec update:

- Update [MA-1.6a] in the same implementation slice. The new contract sentence
  should say: terminal proof remains authoritative for public read models and
  targeted manager publication, but ordinary manager-tracked service
  convergence must not replay global task-log history while the child process
  is still locally live.

Verification:

- Unit test that an alive tracked child returns a live `ServiceCandidate`
  without calling `_child_terminal_proof_visible()`.
- Unit or integration test that an exited service child still records terminal
  service-owner evidence and schedules restart/backoff as today.
- Existing terminal-envelope duplicate-suppression tests must still pass.

### Task 7: Share Tracked-Service And Pending-Spawn Evidence Within One Pass

Owner: `weft/core/manager.py`.

Refactor `_reconcile_managed_services()` so it computes tracked service
candidates once per desired service key and passes those candidates through to
classification, observed registry evidence, and `_tick_managed_service()`.
Treat `_pending_service_keys()` the same way: it is an append-only spawn-inbox
history replay and must be computed at most once per convergence pass.

Required behavior:

- `_reconcile_managed_services()` owns the per-pass evidence snapshot.
- The per-pass evidence snapshot includes pending service keys from
  `_pending_service_keys(desired_keys)`, tracked child candidates, and any
  registry evidence needed for keys that require external proof.
- `_tick_managed_service()` should not call `_tracked_service_candidate()` when
  caller-provided tracked evidence exists.
- The reducer still receives a complete evidence tuple with pending, tracked,
  registry, PONG, and terminal/uncertain candidates as applicable.
- Do not move queue reads or process probes into `manager_services.py`.

Verification:

- Call-count test proving one tracked-service candidate lookup per desired key
  per pass.
- Call-count or broker-backed test proving one `_pending_service_keys()` replay
  per convergence pass, not one replay per service or tick.
- Existing duplicate-service and canonical-live reducer tests must still pass.

### Task 8: Bound Service-Registry And Pending-Spawn Replay

Owner: `weft/core/manager.py`; optional small helper in
`weft/core/service_convergence.py` only if reuse is clear.

Make `_observed_service_candidates_by_key()` read `weft.state.services` at most
once per convergence pass and avoid replaying it from multiple call sites.
Make `_pending_service_keys()` read spawn inbox histories at most once per
convergence pass and only after the service convergence due gate opens or
`force=True` applies.
If profiling after Tasks 3-7 still shows service-registry replay as material,
add a manager-local snapshot with a high-water timestamp similar in spirit to
the manager registry snapshot.

Rules:

- The first implementation should prefer per-pass sharing over a new snapshot.
- Add a high-water snapshot only if a targeted test or profile shows full
  service-registry or pending-spawn replay remains hot after per-pass sharing
  and due-gating.
- Snapshot state is runtime-local optimization only. It must not become
  lifecycle truth.
- Exact-delete pruning must still use actual message IDs from the registry
  read.

Verification:

- Test that one convergence pass performs one service-registry replay for all
  desired keys.
- Test that throttled convergence does not call `_pending_service_keys()`.
- Existing service-owner prune tests must still pass.

### Task 9: Keep Direct Spawn And Control Responsiveness Proved

Owner: `weft/core/manager.py`; tests in `tests/core/test_manager.py` and
existing CLI/serve coverage.

After the hot-loop reductions, prove the core runtime still responds:

- Manager control queue is drained before new spawn work.
- Pending internal spawn work drains in the same manager turn.
- Public spawn work still uses bounded direct reservation attempts after normal
  watcher scheduling.
- Child exit still triggers forced service convergence and restart/backoff
  where required.

Verification:

- Existing targeted manager tests.
- Add one real broker-backed test if the implementation changes ordering in
  `process_once()`.

### Task 10: Update Specs, Lessons, And Plan Status

Owner: docs.

Update:

- `docs/specifications/03-Manager_Architecture.md` [MA-1.6a] implementation
  mapping and related plan backlink.
- `docs/specifications/01-Core_Components.md`, `05-Message_Flow_and_State.md`,
  and `07-System_Invariants.md` related plan lists if the implementation
  touches their described behavior.
- `docs/lessons.md` if implementation confirms the repeated failure mode:
  proof work must not sit before cadence gates in manager hot paths.
- A follow-up plan or explicit plan note for Heartbeat alignment. The follow-up
  should convert `HeartbeatTask` from private `_next_wait_timeout()` plus inner
  wait loop to the shared public hook shape, unless implementation review finds
  a reason to keep it divergent.
- This plan's status to `completed` only after implementation, tests, and
  required implementation review finish.
- `docs/plans/README.md` plan row and count.

## 8. Testing And Verification

Run the smallest targeted set first:

```bash
./.venv/bin/python -m pytest tests/tasks/test_base_task_loop.py
./.venv/bin/python -m pytest tests/core/test_manager.py -k "convergence or leadership or terminal_proof"
```

Then expand to manager and service-adjacent tests:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_manager_services.py tests/core/test_service_convergence.py
./.venv/bin/python -m pytest tests/cli/test_cli_serve.py tests/commands/test_serve.py tests/commands/test_manager_commands.py
```

Before reporting implementation complete, run:

```bash
./.venv/bin/python -m pytest
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

Runtime proof:

- Repeat a 60 second `py-spy` capture against `weft manager serve` under a
  recorded workload equivalent to the one that produced
  `weft-manager-1778802660.svg`.
- Record the reproduction details next to the profile: exact command, relevant
  `WEFT_*` environment, broker target, whether TaskMonitor/autostart are
  enabled, number of live manager-owned services, and queue depths before and
  after the run. If the original production workload cannot be reproduced
  exactly, state that and compare against the closest idle-manager workload
  with heartbeat and TaskMonitor enabled.
- The new flamegraph should not show ordinary manager runtime dominated by
  `_child_terminal_proof_visible`, `_tracked_service_candidate`,
  `_read_active_manager_records`, `iter_queue_json_entries`, or SimpleBroker
  connection open/close churn.
- `strace -c` should show materially lower `openat`/`close` churn for the same
  workload. Exact thresholds are not required in this plan because workload and
  backend pressure vary; compare relative shape.

## 9. Rollout And Rollback

Rollout:

- Ship as ordinary code changes with no data migration and no queue schema
  change.
- Keep old and new managers compatible because service-owner rows, task logs,
  control envelopes, and spawn requests keep the same shape.
- A mixed deployment may have one older hot-loop manager and one newer manager.
  Atomic reservation still protects public spawn work.

Rollback:

- Reverting the code restores old polling/proof cadence without data cleanup.
- No rollback queue migration is needed.
- If runtime evidence shows slower singleton restart is unacceptable, revert
  Task 6 first. The other cadence reductions can still stand if tests pass.

## 10. Fresh-Eyes Self-Review

Review date: 2026-05-15.

Scope reviewed:

- Whether the plan answers the user's core point that only the broker waiter
  should be hot.
- Whether the plan makes the Monitor-style `next_wait_timeout()` shape a
  reusable `BaseTask` contract instead of a manager-only convention.
- Whether the plan makes the shared task loop the scheduling spine instead of
  layering several local throttles onto a 50 ms manager poll.
- Whether the plan protects task dispatch and control responsiveness.
- Whether a zero-context engineer could tell which code to change and which
  behavior must not change.
- Whether the plan hides a spec conflict around terminal proof.

Findings:

- P1: The previous draft made `Manager.next_wait_timeout()` the first visible
  hook, which would leave the reusable pattern implicit and inconsistent with
  `BaseTask.run_until_stopped()`. The plan now starts with a small BaseTask
  slice: default `next_wait_timeout() -> None` plus matching
  `run_until_stopped()` semantics.
- P1: Starting in BaseTask could accidentally change ordinary Consumer timing
  and poll reporting. The plan now forbids changing default behavior in this
  slice and records that any future default wait stretching must account for
  `reporting_interval == "poll"`.
- P2: `HeartbeatTask` has a similar due-timer concept, but copying its inner
  wait loop would undermine the reusable contract. The plan now uses
  `TaskMonitorTask` as the reference shape and keeps full Heartbeat alignment
  out of scope until after the Manager implementation proves the contract.
- P1: The earlier draft still treated the manager as a 50 ms loop with cheaper
  work hanging off it. That missed the stronger target: one reactive loop where
  `next_wait_timeout()` selects the next due source and `MultiQueueWatcher`
  remains the hot wait path. Task 3 now makes that scheduling change explicit.
- P1: The initial version risked saying "stop scanning terminal proof" without
  naming the semantics change. The plan now states the tradeoff explicitly:
  public read models may still honor terminal proof immediately, while ordinary
  manager-tracked service convergence may wait for local child reap instead of
  replaying global task history.
- P1: The initial version could have slowed internal spawn work by moving all
  convergence behind a one-second gate. The plan now keeps direct internal
  spawn drains and `force=True` child-exit convergence as immediate progress
  paths.
- P2: The initial version did not separate per-turn leadership memoization from
  service convergence cadence. The plan now has a dedicated task for
  `_maybe_yield_leadership()` because the profile shows leadership work as an
  adjacent hot path.
- P2: The plan still leaves the exact implementation shape for service-registry
  high-water snapshots conditional. That is intentional; per-pass sharing is
  lower risk and should land first unless profiling proves it insufficient.

Residual risk:

- This plan changes the timing of singleton replacement when task-owned
  terminal proof appears before process exit. That is a deliberate performance
  tradeoff; initial external review has been completed and implementation
  should receive another review before completion.

## 11. External Fresh-Eyes Review

Review date: 2026-05-15.

Reviewer availability:

- Claude Code 2.1.136: available.
- Gemini 0.40.0: available.
- Qwen 0.14.3: available.
- Codex CLI: installed wrapper, but unusable in this environment because the
  vendored native binary is missing.

Reviewer used: Claude Code, read-only prompt, different model family from the
plan author.

Result: the reviewer found blockers in the initial draft. The plan was updated
in response to every finding:

- Blocker: the plan ignored the existing `Manager.wait_for_activity()` rationale
  for bounding supervision ticks under Postgres load. Response: Section 4 and Task 3
  now preserve that rationale and require the shared waiter to obey a hard
  timeout ceiling before the manager can rely on it.
- Blocker: the plan created a circular dependency between computing active
  convergence reasons and the interval gate. Response: Task 4 now splits
  cheap-local active reasons from broker-backed active reasons and states which
  interval applies on a throttled return.
- Blocker: the terminal-proof timing change made [MA-1.6a] updates optional.
  Response: Task 6 now makes the spec update mandatory and provides the
  contract sentence to add.
- Major: `_pending_service_keys()` was missing even though it is also an
  append-only queue replay. Response: Tasks 7 and 8 now include pending-spawn
  evidence and throttled-return tests.
- Major: manager and `MultiQueueWatcher` prechecks could both run. Response:
  Task 3 now says to remove one duplicate precheck and preserve trace logging
  through a narrow hook if needed.
- Major: `Manager.next_wait_timeout()` does not exist and the due-source list
  was incomplete. Response: Task 3 now explicitly introduces the method and
  names the current due sources and cadences.
- Minor: runtime profiling lacked a reproduction recipe. Response: Section 8 now
  requires command, environment, broker target, service topology, and queue
  depth capture.
- Minor: idle broker probes were named but not tasked. Response: Task 3 now
  handles `_update_idle_activity_from_broker()` and the forced idle-shutdown
  probe.
- Minor: `MANAGER_POLL_INTERVAL` fallback semantics were undefined. Response:
  Task 3 now names the launcher fallback and says it must not be the
  steady-state manager cadence.
- Minor: `force=True` bypasses were too open-ended. Response: Task 4 now names
  the closed set of allowed interval bypasses.

External review should be rerun after implementation and before completion.

Follow-up review: Claude re-review was attempted after these fixes, but the API
returned a 529 overloaded error. Gemini 0.40.0 then reviewed the amended plan in
read-only plan mode and reported no remaining plan-level blockers. Gemini noted
four non-blocking implementation risks: leadership memoization must be
invalidated after accepted work, timer aggregation must handle seconds and
nanoseconds carefully, any remaining child-exit poll at
`MANAGER_CHILD_EXIT_POLL_INTERVAL` still wakes the manager while user-work
children are present, and the duplicate pending-message precheck must really be
removed.
Those risks are covered in Tasks 3, 5, and 9 and should be rechecked during
implementation review.

## 12. External Review After BaseTask Rework

Review date: 2026-05-15.

Reason: the plan was materially revised to start with a reusable `BaseTask`
contract inspired by `TaskMonitorTask`, while keeping Manager as the first
behavior-changing implementation.

Reviewer attempts:

- Claude Code: first attempt returned a 529 overloaded error.
- Qwen 0.14.3: unavailable for review because it required interactive OAuth
  authorization in this environment; the waiting process was stopped.
- Gemini 0.40.0: completed read-only fresh-eyes review.
- Claude Code: second attempt completed read-only fresh-eyes review.

Gemini result: no plan-level blockers. Gemini confirmed that starting in
`BaseTask` is the right place for the reusable contract, default behavior is
preserved by returning `None`, `TaskMonitorTask` is the right reference shape,
and full `HeartbeatTask` alignment is correctly out of scope. Gemini repeated
the implementation risks around leadership-cache invalidation, timer unit
handling, duplicate precheck removal, and active-child polling.

Claude result: no plan-level blockers. Claude verified these code facts:

- `launcher._task_process_entry` already consumes `next_wait_timeout()` via
  `getattr` and uses `max(0.0, float(candidate_timeout))`.
- `BaseTask.run_until_stopped()` currently ignores the hook, so adding the hook
  there is non-disruptive when the default returns `None`.
- `TaskMonitorTask.next_wait_timeout()` is public and has no inner wait loop.
- `HeartbeatTask` has an inner wait loop and only private
  `_next_wait_timeout()`, so deferring Heartbeat alignment is correct.
- `Manager` has no `next_wait_timeout()` today.
- `MultiQueueWatcher.wait_for_activity()` already marks the pending precheck,
  so the duplicate-precheck concern in Task 3 is real.

Accepted non-blocking review points:

- Task 1 now explicitly handles the `0.0` timeout case for
  `run_until_stopped()`.
- Task 1 now requires a grep for existing `next_wait_timeout` hooks before
  editing.
- Task 1 now notes that simplifying the launcher's `getattr` is optional and
  not required for this slice.
- Task 10 now requires a follow-up plan or explicit plan note for Heartbeat
  alignment before this plan is marked complete.

Fresh-eyes status: implementation-ready from both completed external reviews.
Implementation still requires the post-implementation review gate in Section 5.

## 13. Heartbeat Alignment Follow-Up Note

This implementation deliberately leaves `HeartbeatTask` unchanged. The
repeatable pattern should be applied there in a later slice by promoting its
private `_next_wait_timeout()` shape to the public `next_wait_timeout()` hook,
removing the inner wait loop from `HeartbeatTask.process_once()` where
possible, and letting the ordinary `BaseTask`/launcher loop own the blocking
wait. That follow-up should preserve heartbeat interval semantics and manager
supervision behavior; it should not copy manager-specific service convergence
state into Heartbeat.

## 14. Post-Implementation Review And Verification

Implementation review date: 2026-05-15.

Code changes completed:

- `BaseTask` now exposes a default public `next_wait_timeout()` hook and
  `run_until_stopped()` honors it while preserving the caller-provided fallback
  interval when the hook returns `None`.
- `Manager.next_wait_timeout()` aggregates manager due sources without broker
  reads, including service convergence, leadership proof, registry heartbeat,
  broker probe, idle deadline, autostart scan/backoff, stalled-control retry,
  dispatch-stall logging, and child-exit observation.
- Managed service convergence now checks cheap local active reasons before the
  interval gate and performs broker-backed proof only after the gate opens or
  an explicit bypass is in effect.
- Ordinary manager-tracked service convergence no longer scans task-log
  terminal proof for a locally live child. Targeted internal-service wrappers
  keep the ability to request terminal proof explicitly.
- `_reconcile_managed_services()` computes pending keys and tracked child
  evidence once per pass and passes that evidence into
  `reduce_managed_service_state()`.
- `_maybe_yield_leadership()` now respects the rate gate before actionable-work
  proof and reuses leadership proof within one manager turn until local progress
  invalidates it.
- The forced idle-shutdown broker probe now runs only when the idle deadline is
  actually reached.

Review results:

- First read-only implementation review by Claude Code found no correctness
  blockers, but flagged three missing plan-required tests: manager
  `next_wait_timeout()` due-source coverage, `Manager.wait_for_activity()`
  timeout/fallback coverage, and leadership per-turn memoization/invalidation
  coverage. It also noted the diagnostic-only removal of the
  `manager_wait_immediate_wake` trace event.
- Those three test gaps were fixed, and leadership turn caching was tightened
  so a long manager turn does not replay leadership proof unless local progress
  invalidates the cache.
- Follow-up read-only implementation review by Claude Code reported no
  blockers. It confirmed that F1-F3 were resolved and that scheduling only
  controls when evidence is collected while `reduce_managed_service_state`
  remains the lifecycle authority.

Verification run after the final implementation changes:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py -k "next_wait_timeout or wait_for_activity or leadership_yield"
./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_manager_services.py tests/core/test_service_convergence.py
./.venv/bin/python -m pytest
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py
```

Results:

- Focused hot-loop/manager tests: 13 passed.
- Manager/service suites: 157 passed.
- Full suite: 1516 passed, 2 skipped.
- Mypy: success, no issues in 157 source files.
- Ruff: all checks passed.
- Plan metadata tests: 4 passed.

Runtime proof status:

- A closest-local `py-spy` run was attempted with a fresh temp context,
  TaskMonitor/heartbeat enabled, autostart disabled, and `manager serve` in the
  foreground:

  ```bash
  WEFT_TASK_MONITOR_ENABLED=1 WEFT_AUTOSTART_TASKS=0 \
    py-spy record --duration 60 --rate 100 --format raw \
    --output /tmp/weft-manager-hot-loop-after.raw \
    -- ./.venv/bin/weft manager serve --context "$tmpdir" --level off
  ```

- The run did not produce a profile because local macOS `py-spy` reported:
  `This program requires root on OSX`.
- `strace` is not installed on this host. `dtruss` exists, but it normally
  requires elevated privileges on macOS and was not run under the current
  no-approval execution policy.
- Because the runtime profile and syscall-churn comparison could not be
  captured locally, this plan remains `draft`. It should be flipped to
  `completed` only after a production-equivalent `py-spy` or permitted local
  profile confirms that ordinary manager runtime is no longer dominated by
  `_child_terminal_proof_visible`, `_tracked_service_candidate`,
  `_read_active_manager_records`, `iter_queue_json_entries`, or queue
  open/close churn.
