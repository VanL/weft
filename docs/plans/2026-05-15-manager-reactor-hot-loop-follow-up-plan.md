# Manager Reactor Hot-Loop Follow-Up Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2]; docs/specifications/03-Manager_Architecture.md [MA-1.1], [MA-1.4], [MA-1.5], [MA-1.6a], [MA-1.7]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-6], [MF-7]; docs/specifications/07-System_Invariants.md [MANAGER.8], [MANAGER.12], [MANAGER.15], [MANAGER.16], [OBS.13]
Superseded by: none

## 1. Goal

Reduce the remaining manager CPU hot loop after the reactor refactor by making
ordinary idle manager turns cheap and reactive to the shared
`MultiQueueWatcher` wait path. The narrow changes are: cache process-stable
task context data through the shared `BaseTask` boundary, reorder leadership
yield so expensive local actionable-work proof runs only after a lower live
leader is known, skip idle broker probes when idle timeout is disabled, and
bound pending-service proof so built-in persistent services do not scan
unreserved public spawn backlog during normal convergence. Leadership also
observes self-owned `superseded` manager rows from `weft.state.services`
without consulting spawn queues.

The intended steady state is not "the manager never wakes." Service
convergence and leadership still run on their approximately one-second due
timers, and queue activity still wakes the manager promptly. The intended
steady state is that those due turns mostly consult process-local state and
targeted queues; repeated context discovery, broad public spawn-backlog scans,
and unneeded broker timestamp probes should disappear from idle profiles.

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.1], [CC-2.2]: `MultiQueueWatcher` owns the backend activity wait seam,
  and `BaseTask` owns the shared `process_once()` / `next_wait_timeout()`
  task-loop contract.
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.1], [MA-1.4], [MA-1.5], [MA-1.6a], [MA-1.7]: spawn reservation,
  manager registry and leadership, idle timeout, internal-service
  convergence, and manager control.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-3], [MF-6], [MF-7]: task-local control, manager spawn flow, and manager
  runtime state queues.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [MANAGER.8], [MANAGER.12], [MANAGER.15], [MANAGER.16], [OBS.13]:
  duplicate-manager convergence, atomic reservation authority, internal
  singleton convergence, PONG evidence, and operational diagnostics not
  becoming lifecycle truth.

Related plans:

- [`2026-05-15-task-reactor-and-evidence-worker-plan.md`](./2026-05-15-task-reactor-and-evidence-worker-plan.md):
  completed. It moved long-lived task work to the shared reactor/worker-result
  pattern and removed the old service-terminal-proof heat. This follow-up must
  preserve that contract.
- [`2026-05-15-manager-hot-loop-reduction-plan.md`](./2026-05-15-manager-hot-loop-reduction-plan.md):
  superseded draft. It is useful historical context for the original
  flamegraph, but do not implement from it.
- [`2026-05-13-manager-liveness-and-leadership-robustness-plan.md`](./2026-05-13-manager-liveness-and-leadership-robustness-plan.md):
  completed. The follow-up must preserve strong live authority before
  voluntary leadership yield.
- [`2026-05-10-control-and-service-convergence-state-machine-plan.md`](./2026-05-10-control-and-service-convergence-state-machine-plan.md):
  completed. Service lifecycle decisions must still flow through
  `reduce_managed_service_state`; scheduling and caching only affect when
  evidence is collected.
- [`2026-05-10-manager-service-authority-boundary-hardening-plan.md`](./2026-05-10-manager-service-authority-boundary-hardening-plan.md):
  completed. Caller-owned TaskSpec metadata must not become singleton service
  authority.
- [`2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md`](./2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md):
  completed. The shared waiter remains the intended blocking seam.

Profile evidence:

- `weft-manager-1300351.svg`: 60-second `py-spy` profile after the reactor
  refactor. The old child-terminal-proof heat is gone. New Weft-visible hot
  paths are `Manager.process_once`, `_maybe_yield_leadership`,
  `_has_actionable_leadership_work`, `_read_active_manager_records`,
  `_update_manager_registry_snapshot`, `_manager_context` / `build_context`,
  `_update_idle_activity_from_broker`, and `_pending_service_keys`.
- `weft-manager-1300351-py-spy.out.txt`: instantaneous sample showing the main
  thread often blocked in the SimpleBroker multi-queue wait path and broker
  pool/listener threads idle. Treat it as supporting evidence only; the SVG is
  the better aggregate signal.

Guidance documents:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

## 3. Context And Key Files

Files to modify:

- `weft/core/manager.py`: primary implementation owner. The targeted functions
  are `Manager.__init__`, `_manager_context`, `_register_manager`,
  `_update_manager_registry_snapshot`, `_maybe_yield_leadership`,
  `_pending_service_keys`, `_run_managed_service_convergence`, and the idle
  section of `process_once`.
- `weft/core/tasks/base.py`: shared owner for process-stable task context
  snapshots. Add the reusable helper here so Manager, TaskMonitor, and
  Heartbeat do not each decide how to rebuild or cache `WeftContext`.
- `weft/core/tasks/task_monitor.py`: replace `_monitor_context()` rebuilding
  with the shared BaseTask context helper.
- `weft/core/tasks/heartbeat.py`: align the existing cached `_context` with the
  shared helper instead of maintaining a third pattern.
- `tests/core/test_manager.py`: primary regression location for leadership
  proof ordering, context caching, idle probe gating, and pending-service
  scan boundaries. Use real broker-backed queues where practical.
- `tests/tasks/test_task_monitor.py` or the closest existing TaskMonitor task
  test file: add a small regression that the persistent monitor does not rebuild
  context each cycle.
- `tests/tasks/test_heartbeat.py`: update or add a narrow assertion only if the
  Heartbeat alignment changes construction behavior.
- `tests/core/test_manager_services.py` or the closest existing manager
  service test file: add or extend service-convergence tests if the
  `_pending_service_keys` split does not fit cleanly in `test_manager.py`.
- `docs/specifications/01-Core_Components.md`,
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/05-Message_Flow_and_State.md`, and
  `docs/specifications/07-System_Invariants.md`: keep related-plan backlinks
  and nearby implementation mapping current.
- `docs/lessons.md`: add a short durable lesson after implementation if the
  code review confirms context rebuilds in persistent services were a
  repeated source of hot-loop work.

Read first:

- `weft/core/manager.py` around `Manager.__init__`,
  `_maybe_yield_leadership`, `_has_actionable_leadership_work`,
  `_update_manager_registry_snapshot`, `_pending_service_keys`,
  `_manager_context`, `_run_managed_service_convergence`, and
  `process_once`.
- `weft/core/tasks/base.py` around `BaseTask.__init__`, `_queue()`, and
  shutdown/cleanup helpers, so the context cache stays separate from existing
  persistent Queue lifecycle.
- `weft/context.py` around `build_context()`, `WeftContext.queue()`, and
  `_ensure_database()`. The key PG detail is that `build_context()` with
  `create_database=True` calls `open_broker(...)` whenever `database_path` is
  `None`, so repeated builds churn Postgres runner/pool state even though the
  returned `WeftContext` itself is data-only.
- `../simplebroker/simplebroker/sbqueue.py` and
  `../simplebroker/simplebroker/_broker_session.py` around persistent Queue
  session sharing. The task context cache must not replace those lifecycle
  rules.
- `weft/core/tasks/multiqueue_watcher.py` around `wait_for_activity` and
  `_update_active_queues`, but do not edit it in the first slice.
- `weft/core/manager_services.py` and existing reducer tests before touching
  service-convergence evidence. Side effects must stay in `Manager`; the
  reducer remains pure.
- Existing tests for manager leadership, manager service convergence, and
  reactor timing before adding new tests.

Current structure:

- `WeftContext` is a frozen metadata object: paths, config, broker target, and
  helper methods. Holding a `WeftContext` does not by itself hold a Queue,
  `DBConnection`, Postgres runner, pool, listener, or checked-out connection.
  Connections are created by `context.queue(...)`, `context.broker()`, or direct
  SimpleBroker Queue construction.
- `build_context()` is not pure. With default `create_database=True`, it may
  create directories and call `_ensure_database()`. For existing SQLite targets
  this usually returns before opening the broker because `database_path` exists.
  For Postgres, `database_path` is `None`, so `_ensure_database()` opens and
  closes a broker connection every time. Under the PG backend that means runner
  and pool churn, not merely Python dataclass churn.
- `Manager` is a persistent `BaseTask`. Its effective `WeftContext` is derived
  from `taskspec.spec.weft_context`, manager config, and the resolved
  `BrokerTarget`. Those inputs are process-stable after `__init__`. Rebuilding
  the context on every registry or leadership path is a short-lived-process
  habit leaking into the long-lived manager loop.
- `TaskMonitor` has the same pattern in `_monitor_context()` and calls it
  during heartbeat registration/cancellation and monitor cycles. Heartbeat
  already caches a context in `__init__`, but it does so locally instead of
  through a shared task pattern.
- `_maybe_yield_leadership()` currently proves local actionable work before it
  reads the active manager records. That is backwards for the normal
  single-manager case: if no lower live leader exists, local actionable-work
  proof cannot affect the yield decision.
- `_update_idle_activity_from_broker()` peeks manager-owned input queues before
  `process_once()` checks whether idle timeout is disabled. For `weft manager
  serve`, an idle timeout of `0` or less should mean no idle-shutdown broker
  timestamp probing on ordinary turns.
- `_pending_service_keys()` starts with manager-local active child-launch
  evidence, then scans every spawn inbox queue returned by
  `_spawn_inbox_queue_names()`. That includes unreserved public spawn backlog.
  Built-in internal services normally need internal pending evidence, not a
  public backlog replay. This optimization depends on the current invariant
  that built-in heartbeat and TaskMonitor services are added only for the
  canonical public manager inbox, which also attaches `weft.spawn.internal`.
- Autostart services are different from built-ins: they currently enqueue on
  the public manager inbox, and their `spawn_pending` state is process-local.
  A manager restart between enqueue and consume loses that process-local flag,
  so autostart pending evidence must keep a due-gated public pending-request
  read unless a separate durable launch-accounting change replaces it.
- `MultiQueueWatcher.wait_for_activity()` still does a pending precheck before
  using the backend waiter. That path is correctness-sensitive and is the
  intended hot seam. This plan treats watcher precheck optimization as a
  post-slice decision, not a first edit.

Comprehension questions before editing:

- In `_maybe_yield_leadership`, which evidence can authorize voluntary
  shutdown or leadership drain? If the answer is not "a positively live lower
  canonical manager plus no owned work that must stay with this manager," do
  not edit leadership code yet.
- Which values in `WeftContext` are immutable for a running manager process,
  and which liveness values must not be cached? Context and manager service key
  are cacheable; registry rows, process liveness proof, PONGs, queue pending
  state, and runtime handles are not cacheable truth.
- Why is caching `WeftContext` safe but caching `context.queue(...)` results in
  the context helper unsafe? The answer should mention that Queue handles own
  persistent SimpleBroker session leases, while `WeftContext` only describes
  how to create them.
- For service convergence, which pending work belongs to manager-owned
  internal singleton launch and which is unrelated public backlog? If that
  distinction is unclear, do not touch `_pending_service_keys`.

## 4. Invariants And Constraints

- No queue names, payload shapes, TID semantics, TaskSpec schema, public CLI
  output shape, or persisted record formats change in this slice.
- The durable spine stays `TaskSpec -> Manager -> Consumer -> TaskRunner ->
  queues/state log`. Do not add a second manager dispatch or service-launch
  path.
- Atomic queue reservation remains public spawn dispatch authority. Registry
  leadership is advisory for public dispatch and authoritative only for
  duplicate-manager convergence under the existing rules.
- Manager leadership yield still requires strong live lower-manager evidence.
  Unknown external-supervisor rows and pending PING probes remain unknown, not
  authority to yield.
- A manager with persistent user children must not voluntarily yield and
  abandon them. Reordering proof must preserve that rule.
- Service lifecycle decisions must still go through
  `reduce_managed_service_state`. Scheduling can avoid unnecessary evidence
  collection, but it must not move lifecycle decisions into timer code.
- Runtime-only queues stay runtime-only. Do not make `weft.state.*` queues part
  of dump/load or add new persistence.
- Append-only history reads, where still needed, must keep using generator
  helpers such as `iter_queue_json_entries`; do not replace them with
  fixed-limit `peek_many(...)` correctness reads.
- Do not cache liveness, registry selection, PONG proof, queue pending state,
  runtime process status, or task-log terminal proof as durable truth. Only
  cache process-stable context and derived service-key values.
- Do not cache Queue objects, `open_broker()` context managers, DBConnection
  objects, Postgres runners, PG pools, activity waiters, or checked-out
  connections in the new context helper. Existing Queue caching in `BaseTask`
  remains the queue-handle lifecycle owner.
- The shared task context helper must call `build_context(...,
  create_database=False)` or an equivalent no-broker-open path. A running task
  already has a resolved broker target from `MultiQueueWatcher`; rebuilding
  context must not initialize or validate the backend on every task-owned use.
- Do not add a process-global `build_context()` cache. CLI and command
  surfaces should continue to rebuild context when they need fresh environment
  or project config. This slice is for long-lived task instances whose launch
  config and broker target are fixed.
- Do not remove `MultiQueueWatcher` pending prechecks in the first slice. A
  missed pending message is worse than a hot profile. Watcher changes require a
  separate proof that the backend waiter cannot strand already-pending work.
- Built-in internal services may use internal-only pending proof only while
  `_internal_spawn_queue_attached()` is true. If a scoped-inbox manager lacks
  `weft.spawn.internal`, it must not silently use an empty internal-only scan as
  proof that no built-in pending request exists.
- Autostart services must retain restart-safe pending evidence for public
  requests. Process-local `spawn_pending` is not enough after manager restart.
- No new dependencies. No module reorganization. No drive-by style cleanup.
- Tests should use real broker-backed queues for manager scheduling and
  service evidence. Mock only narrow nondeterministic seams such as time or a
  single method call count used to prove ordering.

Stop and re-plan if:

- The implementation needs to cache or infer liveness to get the desired CPU
  improvement.
- The implementation starts changing SimpleBroker semantics or public queue
  contracts.
- `_pending_service_keys` cannot be split without weakening duplicate-service
  prevention for autostarts or built-ins.
- The intended test becomes "mock the whole manager loop." Use a smaller real
  broker path or revise the implementation seam.

## 5. Tasks

### Task 1: Add Regression Tests First

Add focused tests before changing production code. Prefer one test per
behavior so failures point at the exact hot-loop regression.

Required tests:

- Leadership self/no-lower-leader path: after the leader check interval has
  elapsed, a manager that is leader or sees no active lower manager must not
  call `_has_actionable_leadership_work`. Patch only the narrow methods needed
  to count calls and feed `_read_active_manager_records`; keep manager
  construction and queue setup real.
- Leadership lower-leader path: if a lower live manager is present,
  `_maybe_yield_leadership()` must then check actionable work before yielding.
  Add cases for "actionable work blocks yield" and "no actionable work yields
  as before."
- Leadership superseded-row path: if `weft.state.services` contains a fresh
  self-owned manager `superseded` row, `_maybe_yield_leadership()` must begin
  superseded shutdown from registry evidence and must not inspect public or
  internal spawn queues to prove that fact.
- Internal spawn progress after leadership reorder: when no lower leader
  exists, skipping `_has_actionable_leadership_work()` must not strand newly
  arrived internal spawn work. Prove a pending internal spawn request is still
  observed and drained through `_managed_internal_spawn_enqueued`, the enqueue
  precheck mark, or the shared watcher/drain path on the next manager turn.
- Shared task context cache: repeated calls through Manager registry helpers
  and TaskMonitor cycle helpers must not call `build_context()` repeatedly after
  task construction. Patch the actual shared-helper import point with a counter
  before task creation and assert a bounded call count after several hot helper
  calls.
- PG no-broker-open guard: the shared task context helper must build the
  cached `WeftContext` without invoking `open_broker(...)`. A narrow unit test
  can patch `weft.context.open_broker` or assert `build_context` receives
  `create_database=False`; the point is to prevent Postgres runner/pool churn,
  not just reduce Python object allocation.
- Idle timeout disabled: with `_idle_timeout <= 0`, an ordinary
  `process_once()` turn must not call `_update_idle_activity_from_broker`.
  Keep enough of the real manager turn to prove the check ordering rather than
  only calling the private idle block.
- Built-in service pending proof: when reconciling built-in internal services,
  pending-service detection must use active child launches and the internal
  spawn queue only when `_internal_spawn_queue_attached()` is true. Add a
  scoped-inbox or forced-detached-internal-queue case proving the code does not
  silently treat "no internal queue" as "no built-in pending work."
- Autostart compatibility and restart safety: put an autostart-shaped managed
  service payload in the public spawn queue, clear or recreate manager-local
  `spawn_pending` state, and assert autostart pending detection still sees the
  public request on the next autostart-including convergence due turn. This
  proves manager restart between enqueue and consume does not create duplicate
  autostart launches.

Stop after this task if the tests cannot express the behavior without broad
mocking. That means the implementation seam is probably wrong.

### Task 2: Add A BaseTask-Owned Process-Stable Context Snapshot

In `BaseTask`, add a shared process-stable context helper and migrate Manager,
TaskMonitor, and Heartbeat to it. This is the right abstraction boundary:
`BaseTask` already owns the resolved broker target, launch config, queue
wiring, and long-lived task lifetime.

Implementation shape:

- Add a private BaseTask helper such as `_build_task_context()` that calls
  `build_context(spec_context=self.taskspec.spec.weft_context,
  config=self._config, create_database=False)` and then, when `self._db_path`
  is a `BrokerTarget`, returns a `replace(...)`d context whose
  `broker_target`, `database_path`, and `broker_config` match the task's actual
  resolved broker target.
- Add a cached accessor such as `_task_context()` or `_context` that builds the
  context once per task instance. The accessor returns the same `WeftContext`
  object on later calls.
- Do not store Queue objects on the context cache. Callers that need queues
  should continue using `self._queue(...)` for task-owned queues, or
  `self._task_context().queue(..., persistent=...)` only where the existing code
  intentionally creates an auxiliary Queue with explicit lifetime.
- Make `Manager._manager_context()` delegate to the shared cached task context.
  Cache the derived `manager_service_key(...)` on Manager because it is a
  manager-specific service-registry key.
- Make `TaskMonitor._monitor_context()` delegate to the shared cached task
  context.
- Make `HeartbeatTask` use the shared cached task context instead of calling
  `build_context()` directly in its constructor.
- Keep runtime handles uncached unless the existing code already caches a
  specific process-local runtime identity. The profile problem is context and
  PG initialization churn, not liveness.

The cache must be per process only. It must not be serialized, stored in a
queue, or used across manager restarts. If a future feature allows live context
mutation, that feature must add an explicit invalidation path; do not design
that abstraction in this slice.

For PG specifically, the helper must avoid the default `create_database=True`
path because `database_path is None` for non-file backends and `_ensure_database`
will otherwise open and close broker state each time. The running task's
`_db_path` is already the authority for the target; the context helper should
describe that target, not re-validate it in a hot path.

### Task 3: Reorder Leadership Yield Proof

Change `_maybe_yield_leadership()` so it reads active manager records and
computes `leader_tid` before checking local actionable work.

Required ordering:

1. Honor the existing rate gate and same-turn gate.
2. Read active manager records from `weft.state.services`.
3. If the services-registry view contains a fresh self-owned `superseded`
   manager row, begin superseded shutdown from that registry proof and return
   without checking spawn queues.
4. If active records are unknown, emit the existing unknown ownership decision
   and return without actionable-work scanning.
5. If there is no leader or this manager is the leader, emit the existing
   ownership decision and return without actionable-work scanning.
6. Only when a different lower leader is positively known, check local
   actionable work using the existing force/per-turn cache semantics.
7. If actionable work exists, do not yield.
8. If no blocking work exists, continue the existing persistent-child,
   non-persistent drain, and no-child yield behavior.

Do not weaken the existing self-turn cache. `process_once()` calls
`_maybe_yield_leadership()` multiple times; one active-registry read per due
turn is enough.

This reorder intentionally trades some active-registry reads during locally
busy turns for avoiding repeated local actionable-work scans in the normal
single-manager idle case. Keep `manager_registry_snapshot` serve-log output
rate-limited at least as tightly as today, and add a regression or log-surface
inspection if the implementation changes the rate-limit key or severity.

Skipping `_has_actionable_leadership_work()` in self/no-lower-leader turns also
skips its `_mark_pending_messages_prechecked()` side effect. That is acceptable
only because internal service enqueues already mark pending work, and ordinary
incoming queue activity still flows through `MultiQueueWatcher.wait_for_activity`
and the manager drain paths. The internal-spawn progress test in Task 1 is the
guard for this side effect.

### Task 4: Gate Idle Broker Timestamp Probes

Move the `self._idle_timeout <= 0` check before
`_update_idle_activity_from_broker()` in `process_once()`.

Required behavior:

- When idle timeout is disabled, ordinary manager turns must not peek
  manager-owned input queues only for idle accounting.
- When idle timeout is enabled, preserve the existing behavior, including the
  forced broker-activity refresh immediately before idle shutdown.
- Active user children, manager-owned work, and autostart ensure obligations
  must still keep an idle-timeout-enabled manager alive.

### Task 5: Bound Pending-Service Proof By Service Source

Split `_pending_service_keys()` so manager-owned built-in services do not scan
the public spawn queue during ordinary convergence.

Implementation options, in preferred order:

1. Add a narrow parameter such as `queue_names` or `include_public_spawn` to
   `_pending_service_keys`. The default must preserve current behavior by
   scanning all spawn inbox queues returned by `_spawn_inbox_queue_names()`.
   Optimized call sites must opt in to a narrower queue set.
2. Split the `_reconcile_managed_services()` pending proof by service group
   before building the reducer snapshot: built-in heartbeat/TaskMonitor
   services use active child-launch evidence plus `weft.spawn.internal` only,
   and only when `_internal_spawn_queue_attached()` is true.
3. Autostart services keep active child-launch evidence plus due-gated public
   pending-request evidence, because they enqueue to the public manager inbox
   and process-local `spawn_pending` is lost across manager restart.
4. If the code wants a larger abstraction for "service source," stop and
   re-plan. That is likely future-proofing beyond this hot-loop fix.

Keep the reducer evidence shape the same: pending service keys still enter the
snapshot as pending evidence and still flow through
`reduce_managed_service_state`. The change is only which queues are scanned on
which due paths.

The plan does not authorize removing public pending evidence for autostarts.
That would require a separate durable launch-accounting change or a proof that
duplicate autostart launches cannot occur across manager restart.

### Task 6: Remove Duplicate Internal Pending Probes Inside One Convergence Turn

In `_run_managed_service_convergence()`, reuse the `internal_spawn_pending`
value already computed at the start of the convergence turn where possible.
When `_reconcile_managed_services()` enqueues new internal work during that
same pass, use `_managed_internal_spawn_enqueued` / `service_request_enqueued`
as the fresh signal instead of immediately probing the broker again.

The goal is small: avoid redundant `has_pending()` probes in the same turn.
Do not change the bounded pass count or the rule that internal spawn work can
bypass stable-service throttling.

Across multiple convergence passes, keep the rule explicit:

- The first pass may use the top-of-turn `internal_spawn_pending` value.
- If a pass drains internal spawn work and the drain count is lower than
  `MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES`, treat local pending as false for
  the next pass unless the same manager enqueued more work.
- If the drain count reaches `MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES`, a
  later pass may re-probe because the queue may still contain more accepted
  internal work.
- If no drain occurs and no new service request was enqueued by this manager,
  do not probe again inside the same convergence turn just to rediscover the
  same false value.

### Task 7: Re-profile Before Touching MultiQueueWatcher

After Tasks 2 through 6 pass tests, re-profile the same production or
production-like idle manager workload for at least 60 seconds.

Expected profile changes:

- `_manager_context` / `build_context` should be absent or visible only at
  startup boundaries. Heartbeat registration and TaskMonitor cycles should use
  the cached task context and should not rebuild context.
- `_has_actionable_leadership_work` should be absent from single-manager idle
  turns and from turns where no lower live leader exists.
- `_update_idle_activity_from_broker` and `_read_broker_timestamp` should be
  absent when idle timeout is disabled.
- `_pending_service_keys` should no longer show public pending-request scans for
  built-in service convergence.
- Remaining `MultiQueueWatcher.wait_for_activity` and `Queue.has_pending`
  samples should be interpreted as the shared wait seam unless they are still
  dominating CPU after the manager-owned proof work is gone.

Expected magnitude:

- The combined visible samples for `_manager_context` / `build_context`,
  `TaskMonitor._monitor_context` / `build_context`,
  `_update_idle_activity_from_broker`, and `_pending_service_keys` should fall
  to near-zero in idle `weft manager serve` profiles when idle timeout is
  disabled and no autostart public pending work exists.
- Under PG, the implementation should also reduce short-lived Postgres runner
  and pool creation attributable to context rebuilds. If pool churn remains,
  inspect for direct `build_context(create_database=True)` calls from
  long-lived task methods before moving on to watcher changes.
- `_has_actionable_leadership_work` should no longer appear in self-leader idle
  turns. If it remains visible without a lower leader, the reorder failed.
- Total manager CPU should drop materially versus `weft-manager-1300351.svg`.
  Do not claim the slice fixed the hot loop if the named paths shrink but total
  CPU stays within noise; in that case, open the watcher/SimpleBroker follow-up
  with the new profile.

Only if the watcher pending precheck remains a top CPU source after these
changes should a separate plan investigate changing `MultiQueueWatcher`.
That separate plan must first prove SimpleBroker backend waiter behavior for
already-pending messages on SQLite and Postgres. Do not remove the precheck in
this slice.

### Task 8: Documentation Updates

When implementation lands, update nearby spec implementation notes:

- `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-1.5],
  [MA-1.6a]: mention cached manager context/service key, leadership proof
  ordering, idle probe gating, and bounded pending-service sources. Also make
  [MA-1.5] explicit that `idle_timeout <= 0` disables idle shutdown and
  therefore disables idle-only broker timestamp probes.
- `docs/specifications/01-Core_Components.md` [CC-2.1], [CC-2.2]: document
  that `BaseTask` owns a process-stable `WeftContext` snapshot for long-lived
  task code, separate from Queue/session ownership. If watcher behavior remains
  unchanged, state that this slice preserved the shared wait contract. If a
  later watcher plan is created, backlink that plan separately.
- `docs/specifications/05-Message_Flow_and_State.md` and
  `docs/specifications/07-System_Invariants.md`: keep related-plan backlinks
  current if manager queue flow or invariants are touched.
- `docs/lessons.md`: add a durable lesson if review confirms that long-lived
  manager/service processes were rebuilding short-lived process context inside
  hot paths.

## 6. Verification

Run focused tests first:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_manager.py -k "leadership or context or idle or pending_service"
```

Then run the relevant manager/service suites. Adjust file names to the actual
test placement, but keep the scope manager-focused before expanding:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_manager_services.py
./.venv/bin/python -m pytest tests/commands/test_manager_commands.py tests/commands/test_system_commands.py
```

Run the standard repo checks before calling the slice done:

```bash
./.venv/bin/python -m pytest
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py
```

Runtime proof:

- Re-run `py-spy` against an idle persistent `weft manager serve` process under
  the same backend and workload that produced `weft-manager-1300351.svg`.
- Capture at least one 60-second profile and one text stack sample.
- Compare the named hot paths in Task 7. If total CPU remains high but the heat
  has moved almost entirely into SimpleBroker PG waiter/pending precheck code,
  do not hide that under this plan. Open the separate watcher/SimpleBroker
  investigation plan with that evidence.
- Confirm normal behavior with a real submission while the profiled manager is
  running: `weft run --no-wait ...`, `weft task status <tid>`, and
  `weft result <tid>` should still prove spawn, state, and result visibility.

Profile artifacts are diagnostic inputs, not source docs. Do not commit new
root-level `weft-manager-*.svg` or `weft-manager-*.out.txt` files unless the
user explicitly asks to preserve them in the repository. If durable evidence is
needed, summarize the profile in the plan or implementation notes and keep the
raw artifacts out of the commit.

## 7. Rollout And Rollback

Rollout is code-only. There are no queue schema changes, no payload migrations,
and no mixed-version compatibility requirements beyond the existing manager
registry contract.

Rollback is a normal code revert. The slices can also be reverted separately:

- Context memoization can be reverted without touching queue data because it is
  process-local only.
- Leadership proof ordering can be reverted if duplicate-manager drain
  behavior regresses.
- Idle probe gating can be reverted if idle-timeout-enabled managers miss a
  final activity refresh. Disabled-timeout managers should not depend on this
  probe.
- Pending-service source splitting can be reverted if it causes duplicate
  built-in services or missed autostart pending evidence.

Post-rollout watch signals:

- manager CPU on idle persistent services
- rate of manager registry writes and PING/PONG probes
- duplicate heartbeat or TaskMonitor singleton launches
- public spawn latency after `weft run --no-wait`
- manager STOP/KILL responsiveness

## 8. Out Of Scope

- No SimpleBroker backend change in this slice.
- No public CLI or client API change.
- No new service state-machine framework. The existing reducer remains the
  reducer.
- No broad manager.py decomposition.
- No global task-log cursor redesign.
- No watcher precheck removal without a separate backend correctness plan.

## 9. Fresh-Eyes Self-Review

Review status: completed during plan authoring.

Findings:

- P1: The first draft risk was treating `MultiQueueWatcher.has_pending()` heat
  as directly fixable in this slice. That is unsafe because the precheck is a
  correctness guard for already-pending messages. The plan now gates watcher
  changes behind a post-slice profile and a separate SimpleBroker waiter proof.
- P1: The first draft risk was saying "cache context" without naming what must
  not be cached. The plan now limits caching to process-stable `WeftContext`
  and manager service key, and explicitly forbids caching liveness, PONG,
  registry, runtime, or queue pending truth.
- P2: The service-pending section could have been misread as removing public
  pending evidence for autostarts. The plan now separates built-in internal
  services from autostarts and requires an autostart compatibility test if that
  path changes.

Residual risk:

- `_pending_service_keys` may have call-site coupling that is not obvious from
  the hot path alone. The tests in Task 1 are the gate: if built-in and
  autostart pending evidence cannot be separated narrowly, stop and revise the
  plan instead of adding a broad abstraction.

## 10. External Review

Review status: completed with Claude CLI in read-only plan mode.

Findings and responses:

- P1: Built-in pending-source split depended on the canonical manager inbox and
  attached internal spawn queue, but the draft did not state that invariant.
  Updated Sections 3, 4, and Task 5 to make `_internal_spawn_queue_attached()`
  an explicit guard and added a regression requirement for scoped/non-attached
  managers.
- P1: Autostart public pending evidence cannot be dropped because autostart
  `spawn_pending` is process-local and manager restart can lose it. Updated
  Sections 3, 4, Task 1, and Task 5 to keep due-gated public pending-request
  evidence for autostarts and require a restart-safety regression.
- P1: Leadership reorder can increase active-registry reads and serve-log
  emissions during locally busy turns. Updated Task 3 to call out the tradeoff
  and preserve log rate limiting.
- P1: Skipping `_has_actionable_leadership_work()` also skips
  `_mark_pending_messages_prechecked()`. Updated Task 1 and Task 3 with the
  required internal-spawn progress regression and the remaining wake paths.
- P2/P3: Clarified context-cache initialization order, Task 6 multi-pass
  pending-probe behavior, `_pending_service_keys` default semantics,
  `idle_timeout <= 0` spec update, profile artifact cleanup, and the expected
  magnitude of the CPU win.

Follow-up context-scope check:

- User review raised whether context rebuilding should be handled more
  generally and whether PG changes the constraints. Code review confirmed
  `WeftContext` is data-only, but `build_context(create_database=True)` can
  open broker state. For PG, `database_path is None`, so repeated builds can
  repeatedly create and tear down Postgres runner/pool state. Updated the plan
  to move the cache to `BaseTask`, to use a no-broker-open context build path
  for long-lived tasks, and to keep Queue/connection/session lifetime with the
  existing Queue and BaseTask queue-cache owners.

Implementation readiness after updates: ready for implementation, with Task 5
still treated as the highest-risk slice and gated by the built-in/autostart
pending-evidence tests.
