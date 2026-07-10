# Reference Reactor Safety Hardening Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2], [CC-2.3], [CC-2.5]; docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3], [MF-3.2], [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [QUEUE.1], [QUEUE.2], [QUEUE.4], [QUEUE.6], [IMPL.8], [IMPL.9], [OBS.13.10]
Superseded by: none

## 1. Goal

Harden Weft's shared task reactor against construction-fixed queue-role aliasing,
multi-threaded driving, duplicated run-loop policy, premature resource closure,
and leaked or shared TaskMonitor worker resources. The target is the safety shape
demonstrated by SimpleBroker's `examples/reference_reactor.py`, adapted to Weft's
existing reserve-queue delivery model, payload-directed Heartbeat egress, and
durable execution spine. This plan does not implement the changes. It defines the
spec promotion, red-green slices, exact edit points, tests, review gates, and
traceability work required before an implementer may call the change complete.

Plan type: umbrella implementation plan with spec revision and two mandatory
landable units.

## 2. Requested Outcomes

The implementation is complete only when all of these outcomes hold:

1. `BaseTask` rejects unsafe overlaps among construction-fixed reactor roles and
   fixed support routes before opening a queue, creating a broker database,
   publishing lifecycle state, or starting a worker. The inventory includes
   BaseTask's global support queues and subtype lanes/routes such as Manager
   internal spawn/services-registry queues and Pipeline
   event/status/state/source/target/spawn routes, with only named, tested semantic
   aliases allowed. Payload-directed Heartbeat destinations remain a
   separately validated runtime egress contract, not construction-fixed topology.
2. A `BaseTask` instance has one reactor-drive owner. A second thread entering a
   turn, drive loop, or `wait_for_activity()` fails before it can perform queue,
   waiter, TaskSpec, or lifecycle effects.
3. `BaseTask.process_once()` is the non-overridden public template. Concrete task
   policy lives behind a protected turn hook so every task family receives the
   ownership check. `BaseTask.wait_for_activity()` is likewise a non-overridden
   public template with a protected owned-wait hook, so Manager/test subclasses
   cannot bypass the same owner check.
4. `BaseTask.run_until_stopped()` owns the process, wait, and finalization loop.
   `weft/core/launcher.py` delegates to it instead of maintaining a parallel loop.
5. Stop and cleanup ordering is explicit and idempotent. Reactor-owned queue
   handles are never closed while startup is pending or another thread is driving
   a turn or loop. One absolute deadline bounds Weft-owned joins and drain waits;
   Manager active-launch drain and child escalation consume that same budget;
   cleanup failures cannot strand the task in `FINALIZING` or skip base cleanup.
6. Background, foreground, manual, exceptional, and self-stop paths all reach the
   same finalizer.
7. Existing bounded worker-result backpressure and control responsiveness remain
   intact.
8. TaskMonitor maintenance workers use worker-owned broker, store, configuration,
   TaskSpec snapshot, and external-sink facade resources, close them in `finally`,
   and never share the reactor's sink counters or lifecycle. Same-path external-log
   clients share one process-local writer/rotation owner by design so independent
   rotating handlers cannot corrupt or block each other.
9. Specs, plan, implementation mapping notes, code docstrings, and firing tests form
   a reciprocal traceability chain.

## 3. Source Documents

### Reference implementation

- `../simplebroker/examples/reference_reactor.py`, at SimpleBroker commit
  `37ee5e6f600828e8d23f76349258a84c1efd8d31`:
  - `BaseReactor.process_once()` claims one driving thread before work.
  - `BaseReactor.run_until_stopped()` owns process, wait, retry, and finalization.
  - `BaseReactor.stop()` requests stop first, joins the driver and workers, and
    closes resources only after no other drive thread is alive.
  - reactor roles are fixed and pairwise distinct before durable setup.
  - worker inputs/results are in-process values; persistent handles remain with
    their owner.
- `../simplebroker/examples/tests/test_reference_reactor.py`:
  - real SQLite tests for role collisions, single-owner turns, external stop,
    manual-drive stop, background wakeup, backlog fairness, and replay behavior.
- `../simplebroker/README.md`, "Reactor example (advanced)": the public ownership,
  topology, delivery, and replay caveats.
- `../simplebroker/docs/plans/2026-07-09-review-findings-remediation-plan.md`:
  rationale for fixed topology, route pinning, first-turn replay, bounded results,
  real-broker tests, and rejected framework expansion.
- `docs/plans/2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md`:
  original Weft multi-queue waiter design, including generation/signature
  invalidation on `add_queue()`/`remove_queue()` and the intended rebuild on the
  next direct wait. Section 6.5 records the remaining background-strategy gap.

### Governing Weft specifications

- `docs/specifications/01-Core_Components.md`:
  - [CC-2.1] owns multi-queue scheduling and its wait seam.
  - [CC-2.2] owns BaseTask queue wiring, control, worker-result plumbing, cleanup,
    and shared task-loop entry points.
  - [CC-2.3] names `Consumer`, `Manager`, `HeartbeatTask`, `PipelineTask`, and
    `TaskMonitor` as concrete task policies.
  - [CC-2.5] defines the shared task execution flow and launcher relationship.
- `docs/specifications/05-Message_Flow_and_State.md`:
  - [MF-2] deliberately uses reserve/move semantics. A crash leaves work in
    `reserved` for explicit recovery.
  - [MF-3] requires the main task thread to own broker-visible control effects.
  - [MF-3.2] defines Heartbeat's runtime registration-to-destination egress.
  - [MF-5] defines TaskMonitor's operational read model and deferred external-log
    outbox.
  - [MF-6] requires the Manager reactor to retain broker-effect ownership while
    launch workers run broker-free.
- `docs/specifications/07-System_Invariants.md`:
  - [QUEUE.1], [QUEUE.2], [QUEUE.4], and [QUEUE.6] govern task queue roles,
    reservation, and recovery.
  - [IMPL.8] makes the main task thread the reactor owner.
  - [IMPL.9] requires broker-free ordinary workers and bounded result channels.
  - [OBS.13.10] grants only the named TaskMonitor maintenance lanes fresh
    broker/store authority and requires control responsiveness.

### Required repo guidance

- `AGENTS.md`, especially sections 1.1, 4, 5.1, 5.2, 7, and 8.
- `docs/agent-context/decision-hierarchy.md`.
- `docs/agent-context/engineering-principles.md`, especially sections 1, 2, 4,
  4.1, 6, 7, 9, and 11.
- `docs/agent-context/runbooks/writing-plans.md`.
- `docs/agent-context/runbooks/hardening-plans.md`.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.
- `docs/agent-context/runbooks/testing-patterns.md`.
- `docs/lessons.md`, especially 2026-04-04 PG parity, 2026-04-07 STOP pollers,
  2026-05-15 manager hot loops, 2026-05-31 broker finalizers, and 2026-07-06
  cohesion over file size.

## 4. Spec Baseline

- Governing Weft spec baseline:
  `2b02032f895ade32efa601f2b3ae0d59751dacfb` for
  `docs/specifications/01-Core_Components.md`,
  `docs/specifications/05-Message_Flow_and_State.md`, and
  `docs/specifications/07-System_Invariants.md`.
- The Weft worktree was dirty at plan authoring time in unrelated files:
  `docs/agent-context/engineering-principles.md`, `docs/lessons.md`,
  `pyproject.toml`, and `uv.lock`. Those changes belong to the user and must not
  be reverted, reformatted, or folded into this implementation.
- Reference baseline:
  SimpleBroker commit `37ee5e6f600828e8d23f76349258a84c1efd8d31`.
- Promotion baseline: the strategy-A text was promoted in the uncommitted
  implementation worktree based on
  `2b02032f895ade32efa601f2b3ae0d59751dacfb`. The promotion diff is confined to
  this plan plus `docs/specifications/01-Core_Components.md` and
  `docs/specifications/05-Message_Flow_and_State.md`; the pre-existing plan
  backlinks in all three governing specs were preserved. The Phase 1 plan/spec
  hygiene gate passed before production-code edits. Strategy-B [QUEUE.7] was
  then added atomically with the topology implementation and its firing tests.
  [IMPL.10] and [IMPL.11] were promoted with their implementation mappings in
  the 2026-07-09 resolution pass (section 13.1 item R-5), atomically with the
  R-1/R-2 code corrections and their firing tests.

## 5. Current Structure and Findings

### 5.1 Current owners

| Concern | Current owner | Important current behavior |
| --- | --- | --- |
| Multi-queue scheduling | `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher` | Supports dynamic `add_queue()` and `remove_queue()` and does not enforce task-role topology. Its public `wait_for_activity()` creates/resets waiter state and changes scheduling hints, so it is a drive-owned operation for BaseTask even though standalone watchers remain dynamic. |
| Shared task mechanics | `weft/core/tasks/base.py::BaseTask` | Resolves queue names, caches queue handles, owns worker-result plumbing, cleanup, `process_once()`, and `run_until_stopped()`. It also opens fixed global support routes for lifecycle logs, TID mappings, streaming sessions, and endpoint registration outside the five local task roles. |
| Spawned process loop | `weft/core/launcher.py::_task_process_entry` | Reimplements process, terminal-state, worker-activity, due-time, wait, and finalization decisions instead of delegating to `BaseTask.run_until_stopped()`. |
| Consumer policy | `weft/core/tasks/consumer.py::Consumer.process_once` | Handles active worker/control interleaving and otherwise calls `super().process_once()`. |
| Manager policy | `weft/core/manager.py::Manager.process_once` | Runs manager reconciliation, control, child launch, and then conditionally calls `super().process_once()`. |
| Heartbeat policy | `weft/core/tasks/heartbeat.py::HeartbeatTask.process_once` | Reimplements the bounded turn directly and does not call `BaseTask.process_once()`. Registration payloads supply `destination_queue` at runtime, so these outputs cannot truthfully be declared fixed at construction. |
| Pipeline policy | `weft/core/tasks/pipeline.py::PipelineTask.process_once` | Performs bootstrap, then delegates to `BaseTask.process_once()`. |
| Forwarding monitor topology | `weft/core/tasks/monitor.py::Monitor` | Does not override `process_once()`, but adds a configurable reserve destination named `downstream_queue`; its intentional default is the task's canonical `outbox`. This is distinct from the persistent operational `TaskMonitor`. |
| Operational TaskMonitor policy | `weft/core/monitor/task_monitor.py::TaskMonitor.process_once` | Reimplements termination, result drain, queue drain, due scheduling, and reporting. |
| Generic workers | `BaseTask._submit_worker_lane`, `ServiceTask` | Bounded local result queue and bounded per-turn drain already exist. |
| TaskMonitor maintenance workers | `TaskMonitor._worker_local_monitor_clone`, `_run_builtin_cycle_worker`, `_run_terminal_control_cleanup_worker` | Allowed to open fresh resources, but the shallow built-in clone retains watcher/lifecycle handles, mutable `taskspec`/`_config`, and the same global logger identity; runtime cleanup executes on the live monitor, can use its sink/mutate cached external status, and does not close its store explicitly. Distinct `RotatingFileHandler` instances for one path would not share a rollover lock and are not a safe replacement. Typed runtime-cleanup results currently cannot return those diagnostics or cleanup failures. |
| Task-specific shutdown policy | `Consumer.stop`/`cleanup`, `ServiceTask.cleanup`, `Manager.cleanup`, `TaskMonitor.cleanup` | Concrete cleanup currently runs outside a shared lifecycle gate. A BaseTask-only stop fix would still permit subclass resources to close or mutate while another thread owns a turn. |

### 5.2 Confirmed differences from the reference

| Reference property | Weft today | Finding and required direction |
| --- | --- | --- |
| Pairwise-distinct fixed roles | `BaseTask` accepts overlaps; dict construction can silently collapse handlers. Manager and Pipeline add functional lanes beyond the five canonical names, and BaseTask has fixed global support routes outside that set. It also inherits dynamic task topology from `MultiQueueWatcher`. | Confirmed defect. Validate a subtype-extensible construction-fixed role/support-route inventory before `_build_queue_configs()`/`super().__init__()`, allow only explicit semantic aliases, and seal mutation at BaseTask while preserving dynamic `MultiQueueWatcher` for non-task callers. Do not misclassify payload-directed Heartbeat destinations as fixed topology. |
| One turn owner | No ownership field or claim exists. Every `process_once()` override is callable from any thread. | Confirmed defect. Put the claim in a non-overridden BaseTask template before policy code. |
| One drive owner includes wait | `BaseTask.wait_for_activity()` can be called by a foreign thread and mutates the multi-queue waiter and pending-state hints. | Confirmed defect. Guard the BaseTask wait entry before `_ensure_multi_activity_waiter()` while leaving the standalone MultiQueueWatcher API and polling fallback unchanged; section 6.5 separately defers native rebind after dynamic membership changes. |
| Dynamic native-waiter membership | `MultiQueueWatcher.add_queue()`/`remove_queue()` increment a generation and detach/close the stale waiter. A later direct `wait_for_activity()` rebuilds it, but an already-running inherited background strategy is not rebound to the new waiter. | Confirmed deferred defect. Polling fallback still discovers the new queue, but it cannot wake through PostgreSQL's fixed-set native waiter. BaseTask topology is sealed in this plan; owner-thread rebuild/rebind for standalone dynamic watchers is specified and deferred in section 6.5. |
| One process/wait/stop loop | `BaseTask.run_until_stopped()` and `_task_process_entry()` contain parallel loop logic. `BaseWatcher.run_forever()` is a third path that bypasses BaseTask turn policy. | Confirmed design gap. Make BaseTask the deep module and the launcher a thin adapter. |
| Stop request is separate from close | `BaseTask.stop()` decides cleanup using only SimpleBroker's weak `_thread` reference. A manual drive thread or standalone owned wait is invisible and may have its queues closed underneath it. Raw `Thread.is_alive()` is also false before `Thread.start()` and remains true for an idle long-lived manual owner. | Confirmed defect. Track explicit startup-pending, turn-active, wait-active, and drive-loop-active state. Cleanup authority follows active/pending drive work, not raw thread liveness. |
| Finalization on every drive exit | Direct `run_until_stopped()` has no `finally`; a max-iteration return or exception leaves cached queue handles open. | Confirmed defect by probe. Finalize through one idempotent path. |
| Local worker completion wakes the reactor | Weft already caps waits at `TASK_REACTOR_WAKEUP_MAX_SECONDS` while workers are active and uses a local event. | Substantially present. Preserve and test it; do not invent another event plane unless a red test proves the current cap misses a required wake. |
| Bounded result queue and drain | Present in `BaseTask`; service workers reuse it. | Keep unchanged except for template-hook renames. |
| Control remains live during worker backlog | Consumer, Manager, and TaskMonitor explicitly interleave control and bounded result work. | Keep. Regression tests must prove the refactor does not move control behind worker completion. |
| Worker-owned resources | Ordinary Weft workers are contractually broker-free. TaskMonitor maintenance lanes are the named exception. | Partially present. Fix shared `ExternalTaskLogSink` identity and missing explicit `MonitorStore`/clone cleanup. |
| Peek/checkpoint input plus durable exact-ID output replay | Weft uses reserve queues and explicit reserved recovery instead. | Do not port. A sidecar output ledger would create a second durable truth and materially change [MF-2]/[QUEUE.4]/[QUEUE.6]. |
| Fixed route replay | Not applicable to ordinary Weft result delivery. TaskMonitor's deferred external-log outbox already records report identity in Monitor-owned storage. | Do not add a general route-pinning layer in this plan. Preserve the existing TaskMonitor deferred-write contract. |

### 5.3 Reproduced evidence

The following read-only probe used a real temporary SQLite broker and current
production classes:

- `Consumer` accepted `inbox`, `outbox`, `ctrl_in`, and `ctrl_out` all named
  `same`; its watched queue map collapsed to `same` plus the reserved queue.
- `Consumer` accepted `weft.log.tasks` as its inbox while BaseTask also used that
  same queue for lifecycle output; construction reused one handle and the task's
  own initialization event became pending input.
- a `ReactorTestTask` ran one turn on a child thread and a later turn on the main
  thread without an exception.
- after `run_until_stopped(poll_interval=0.0, max_iterations=1)`, the task still
  held eight entries in `_queue_cache`.

These are behavior findings, not inferred style concerns.

## 6. Invariants and Constraints

### 6.1 Invariants that must not move

- Preserve the canonical durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Preserve Weft's reserve semantics. Do not add a sidecar input checkpoint or a
  general output outbox to ordinary tasks.
- Preserve forward-only TaskSpec lifecycle transitions and immutable `tid`, `spec`,
  and `io` sections.
- Preserve queue names and payload shapes for valid existing tasks.
- Preserve `ReservedPolicy.KEEP`, `REQUEUE`, and `CLEAR` behavior exactly.
- Preserve spawn-based child creation. Never pass live queue handles across a
  process boundary.
- Preserve bounded worker-result queue size and the single per-turn result budget.
- Preserve control priority and control responsiveness while worker lanes are active.
- Preserve TaskMonitor's narrow broker/store worker exception. Do not grant broker
  authority to ordinary BaseTask or ServiceTask workers.
- Preserve the standalone `MultiQueueWatcher.add_queue()`/`remove_queue()` API and
  bounded polling correctness. Do not overclaim native-waiter parity after runtime
  membership changes: owner-thread waiter rebuild/rebind is the deferred defect in
  section 6.5. Only BaseTask's construction-fixed task-owned roles and fixed support
  routes are sealed. Heartbeat's runtime `destination_queue` remains
  payload-directed egress under [MF-3.2], outside the construction-time role matrix.
- Preserve SimpleBroker as the broker wait owner. Do not add a second polling thread
  or condition-variable framework.
- Preserve public `BaseTask.process_once()` and `run_until_stopped()` names. This is
  an internal-policy refactor, not a caller-facing rename.

### 6.2 Hidden couplings

- `process_once()` is overridden in five concrete runtime files. Renaming only some
  overrides would silently skip task-specific policy or the ownership claim.
- `cleanup()` is overridden by Consumer, ServiceTask, Manager, and TaskMonitor;
  Consumer also overrides `stop()`. Leaving any public override in place would let
  task-specific resource work bypass the live-driver safety gate. These methods
  must migrate atomically to one subtype-only protected cleanup hook, while the
  private finalizer always invokes private BaseTask cleanup afterward, just as turn
  policy moves behind the public `process_once()` template.
- `Manager.process_once()` and `Consumer.process_once()` intentionally call the base
  turn only in selected states. The protected hook must preserve those exact call
  points.
- `_task_process_entry()` performs parent-loss checks before each current turn. Loop
  consolidation must retain parent-loss delivery on the main reactor thread without
  doing broker work from the watcher thread.
- Manager has a separate `_pending_termination_signal`, SIGUSR1 priority rule, and
  graceful `_begin_shutdown_drain()` path. Loop consolidation must move Manager to
  a shared pending-source snapshot plus a protected owner-thread policy hook; it
  must not route parent loss through generic task cancellation or leave Manager's
  current override blind to the BaseTask parent-loss flag.
- `note_termination_signal()` is safe in a Python signal handler because it performs
  only plain in-memory assignment. Do not make that method call `Event.set()`, queue
  methods, a strategy wake, or another lock-taking primitive.
- BaseWatcher's inherited SIGINT handler calls lock-taking `stop()` and raises from
  the signal frame. BaseTask `run_forever()` must install a task-owned handler that
  only calls `note_termination_signal()`.
- Parent loss is reported by an ordinary watcher thread and must use a separate
  pending flag, not overwrite `_pending_termination_signum`. The owner resolves a
  pending real signal and parent loss together, with KILL-class SIGUSR1 taking
  priority over parent-loss STOP.
- `BaseTask.stop()` is also used as explicit resource cleanup after tests and normal
  completion. It must not itself publish a cancelled TaskSpec transition.
- TaskMonitor's worker-local shallow copy shares every field not explicitly replaced.
  That includes `_queue_obj`, `_queues[*].queue`, strategy/waiter, stop/lifecycle
  state, mutable `taskspec`, mutable `_config`, and `_external_task_log_sink`, even
  though the method's contract says worker-local resources.
- Distinct `ExternalTaskLogSink` objects for the same path/TID currently select the
  same process-global logger and clear its handlers. Giving each owner a separate
  `RotatingFileHandler` is also unsafe because handler locks do not serialize
  same-path rollover and another open handler can block rename on Windows. Resource
  design must separate worker-local counters from one process-local path writer.
- `_run_terminal_control_cleanup_worker()` is bound to the live TaskMonitor. Its
  `jsonl_then_delete` path can call `_handoff_lifetime_report()` and mutate live
  external/deferred fields from the worker. Its result envelope lacks the
  diagnostics needed for owner-thread application.
- TaskMonitor built-in work must capture diagnostics before closing its worker-local
  resources. Cleanup must not erase the result snapshot first.
- `MonitorStore.close()` is required for backend parity. SQLite may hide a missed
  close; Postgres can leave pool threads alive.
- `BaseWatcher.start()` calls `run_forever()`. Without a BaseTask override, it uses
  SimpleBroker's watcher loop rather than the task reactor loop.
- A backend-native multi-queue waiter captures queue names at creation. Current
  `MultiQueueWatcher` generation/signature tracking rebuilds on a later direct
  `wait_for_activity()`, but the inherited background loop calls
  `PollingStrategy.wait_for_activity()` after one `_start_strategy()` attachment.
  Detaching the stale waiter during `add_queue()`/`remove_queue()` therefore leaves
  the running strategy on polling fallback; rebuilding/rebinding it safely is an
  owner-thread topology lifecycle, not another direct waiter call.
- inherited `BaseWatcher.stop()` uses a weak `_thread` reference. It cannot see a
  manual `run_until_stopped()` thread, and when called from the current background
  thread it deliberately leaves strategy cleanup for a later caller. The BaseTask
  finalizer cannot treat that inherited method as a complete lifecycle primitive.

### 6.3 Fatal and best-effort failures

- Fatal:
  - queue-role collision at task construction;
  - second-thread reactor turn/loop or foreign BaseTask wait entry;
  - unexpected task policy exception on the owning drive thread;
- Retryable/failed-cycle TaskMonitor outcomes:
  - Monitor-store open/operation failure in a maintenance lane;
  - external-log emission/deferred-write failure;
  - invalid built-in or control-cleanup worker result shape.
  These remain typed failed/pending operational results applied by the owner;
  preserve `test_task_monitor_terminal_control_cleanup_worker_error_is_retryable`.
- Finalization errors:
  - finalization attempts every ordered cleanup phase even after one phase fails;
  - subtype cleanup failure cannot skip private BaseTask queue/waiter/strategy and
    thread-local cleanup;
  - the first failure is retained as the primary diagnostic and later failures are
    logged as secondary context; public `stop()`/`cleanup()` keep their `None`
    compatibility and do not retry a partially completed close;
  - lifecycle always leaves `FINALIZING` for `CLOSED`, with a private immutable
    cleanup-error snapshot for tests/diagnostics. A direct drive-loop policy
    exception remains the propagated primary exception; cleanup failures are
    attached/logged rather than replacing it.
- Retryable TaskMonitor worker-close errors:
  - worker-local store/sink/queue close failure converts an otherwise successful
    maintenance result into the existing typed failed/pending outcome after all
    close attempts; the owner must never merge it as success.
- Best effort:
  - optional TaskMonitor external-log health probing, as already specified.
- A stop timeout is not permission to close live-driver resources. Log the timeout,
  leave resources owned by the live driver, and let the driver's `finally` close
  them when it exits. One absolute deadline bounds Weft-owned joins and drain waits;
  it does not claim to preempt an operating-system or backend `close()` call already
  in progress.

### 6.4 Stop-and-re-evaluate gates

Stop and update this plan before implementation continues if any of these occur:

- a valid current TaskSpec needs an intentional alias not listed by its subtype
  hook (record the alias and firing contract before widening it);
- a payload-directed route other than Heartbeat requires dynamic egress, or a
  Heartbeat destination must legally target its own watched/reserved/control lane
  or the reserved `weft.` system queue namespace;
- a production task class cannot be expressed through the protected turn hook;
- launcher loop consolidation requires changing result payloads, state transitions,
  or reserved policy;
- a change starts modifying SimpleBroker watcher internals instead of the Weft task
  seam;
- a general-purpose executor, event bus, queue factory, or reactor framework appears;
- tests need mocked Queue objects to prove topology, shutdown, or control behavior;
- TaskMonitor worker isolation requires redesigning retention or cleanup policy;
- a same-path external-log implementation would create more than one live rotating
  handler or cannot release the shared path writer after its final client closes;
- a traceability mapping would need to cite spec text that has not been promoted.

### 6.5 Deferred standalone dynamic-waiter rebuild/rebind

**Issue.** SimpleBroker's optional native waiter is created from a fixed queue-name
tuple. Weft already increments `_queue_generation`, clears the waiter signature,
detaches/closes the stale waiter on `MultiQueueWatcher.add_queue()` or
`remove_queue()`, and rebuilds it when direct `wait_for_activity()` next calls
`_ensure_multi_activity_waiter()`. That is sufficient for direct/manual waits.

It is incomplete for an already-running background watcher. The inherited
SimpleBroker loop attaches one waiter during `_start_strategy()` and then keeps the
strategy as the sole wait path. A runtime membership change detaches the old waiter,
but no owner-thread step builds the new fixed set and rebinds it to the running
strategy. The strategy falls back to bounded polling, so messages are eventually
discovered; the precise defect is loss of native PostgreSQL wakeup and its latency
benefit, not permanent message invisibility. Closing/resetting the old waiter from a
foreign mutation thread can also overlap an owner-thread wait and must not be the
long-term synchronization model.

**Proposed fix in a separate plan/slice.** Keep `PollingStrategy` as the only
background wait path and treat topology mutation as owner-thread work:

1. A foreign `add_queue()`/`remove_queue()` request validates and records a pending
   topology operation plus a new generation; it does not close, rebuild, or bind a
   native waiter itself.
2. The strategy receives a local topology-change hint through a supported wake seam
   and returns control to the watcher owner. Do not add a second polling thread or
   wait concurrently on the native waiter from Weft.
3. Between wait/drain turns, the owner applies pending membership changes, closes
   the old waiter exactly once, builds a waiter from the exact current
   `_activity_wait_configs()` signature, and atomically rebinds it to the strategy.
4. If SimpleBroker 5.2.x has no supported atomic replace/rebind seam, add that seam
   at the SimpleBroker strategy layer first. Do not reach into backend-specific
   PostgreSQL waiter internals from Weft and do not restart the whole watcher as a
   substitute.
5. Rebuild failure debug-logs and leaves the strategy on the existing bounded
   polling fallback. A later generation change or explicit retry may attempt a new
   bind; no queue delivery semantics change.
6. Stop, topology mutation, waiter replacement, and final close share one owner
   ordering so no waiter is waited, replaced, or closed concurrently.

Required acceptance coverage for that follow-up:

- a background watcher starts with a native/fake waiter for set `{A, B}`;
- a foreign-thread add request is applied on the watcher owner, closes the old
  waiter once, and rebinds a new waiter for exactly `{A, B, C}`;
- a write to `C` wakes through the new native waiter before the polling fallback;
- removing `B` produces exactly `{A, C}`, and later writes to `B` do not native-wake
  the watcher;
- rapid add/remove generations coalesce or apply deterministically without leaking
  waiters;
- mutation racing stop cannot bind after strategy/finalizer close;
- rebuild failure retains polling correctness and a later successful rebind;
- a PostgreSQL-backed integration test proves real LISTEN/NOTIFY membership, not
  only fake-waiter call order.

**Deferral decision.** This work is outside Unit A and Unit B. BaseTask explicitly
rejects runtime topology mutation, and repository search currently finds no
production `add_queue()`/`remove_queue()` caller; the only uses are tests, while the
interactive command constructs a static `MultiQueueWatcher`. Folding a mutable
topology command lane and strategy replacement API into the BaseTask lifecycle
change would expand the state machine without serving a current production path.

Promote this follow-up to a prerequisite instead of deferring it if any of these
become true before implementation lands:

- BaseTask or another production reactor needs runtime membership changes;
- a production standalone watcher begins calling `add_queue()`/`remove_queue()`;
- PostgreSQL native-wakeup latency after membership changes becomes an explicit
  contract or SLO;
- polling fallback is removed or can no longer guarantee bounded discovery; or
- the current foreign-thread waiter close is reproduced as a wait/close failure.

## 7. Proposed Spec Delta

Promotion uses two strategies. The new [CC-2.2.1] section and the [MF-2]
clarification use **A, in-file text before link claims**. Phase 1 adds those
requirements and plan backlinks without claiming that the new reactor contract is
already implemented. [QUEUE.7], [IMPL.10], and [IMPL.11] sit below existing section-level
implementation mappings, so landing those invariant bullets early would create a
false implementation claim. They therefore use **B, atomic** in the code slices
that make each invariant true. Phase 2 adds [QUEUE.7] with topology code and firing
tests; Phase 4 adds [IMPL.10] after both the owner template and lifecycle loop are
implemented; Phase 5 adds [IMPL.11] after both maintenance worker paths are
isolated. Final reciprocal [CC-2.2.1] mapping claims and code citations land in
Phase 6, after the full section is true. Do not cite [CC-2.2.1], [QUEUE.7], or
[IMPL.10]/[IMPL.11] from shipped code before the corresponding strategy says to do so.

| Spec file | Strategy | Sections touched |
| --- | --- | --- |
| `docs/specifications/01-Core_Components.md` | A | Add [CC-2.2.1]; revise [CC-2.5] launcher paragraph |
| `docs/specifications/07-System_Invariants.md` | B | Add [QUEUE.7] in Phase 2, [IMPL.10] in Phase 4, and [IMPL.11] in Phase 5, each atomically with its firing code/tests |
| `docs/specifications/05-Message_Flow_and_State.md` | A | Add a clarification under [MF-2] that reservation delivery does not change; revise [MF-3.2] to separate runtime Heartbeat egress from construction topology and reject self/system-namespace routes |

### 7.1 `docs/specifications/01-Core_Components.md`

Insert after [CC-2.2] and before [CC-2.3]:

> ### 2.2.1 Reactor Ownership and Lifecycle [CC-2.2.1]
>
> _Implementation status_: promoted target requirement; implementation mapping is
> intentionally deferred until this plan's topology, lifecycle, and TaskMonitor
> isolation slices are complete.
>
> `BaseTask` is the single owner of the public task-reactor interface.
> `process_once()` claims and verifies one driving thread before it invokes a
> concrete task's protected turn policy. After ownership is claimed, a different
> thread entering `process_once()`, the drive loop, `wait_for_activity()`, or a
> drive-owned topology mutation must fail before that entry point performs queue,
> waiter, TaskSpec, lifecycle, or worker-result effects. This is a reactor-drive
> ownership rule, not a claim that every read-only diagnostic helper is
> thread-confined. Concrete task classes customize the protected turn hook and
> optional protected owned-wait hook and must not replace public `process_once()`
> or `wait_for_activity()`; class creation rejects such an override. The strong
> `Thread` object is identity evidence; explicit
> startup-pending, turn-active, wait-active, and drive-loop-active state
> determines whether work can still touch reactor resources. The public wait
> publishes wait-active under the lifecycle lock and clears it in `finally`,
> where a pending standalone stop can finalize after the protected wait unwinds.
> Numeric thread ident and raw
> `Thread.is_alive()` are diagnostic only.
>
> A live task reactor has fixed, declared construction topology. BaseTask declares
> `inbox`, `reserved`, `outbox`, `ctrl_in`, and `ctrl_out`, plus its fixed support
> routes for lifecycle logs, TID mappings, streaming sessions, and endpoint
> registration. A task subtype extends that inventory for every additional
> construction-fixed watched lane, reserve destination, support route, or durable
> route whose aliasing would change the task's meaning. Role names are pairwise
> distinct unless that subtype explicitly names and tests an intentional semantic
> alias, such as PipelineEdge `source_queue == inbox` and
> `target_queue == outbox`, or Monitor's default `downstream == outbox`.
> Validation occurs before `_build_queue_configs()`, any queue handle, broker file,
> lifecycle write, endpoint claim, or worker thread. Payload-directed Heartbeat
> destinations are runtime egress under [MF-3.2], not construction topology; their
> ingestion validator rejects self-routing into the Heartbeat task's own inbox,
> reserved, or control lanes and rejects the reserved `weft.` system queue
> namespace, while ordinary destination task queues remain valid. Standalone
> `MultiQueueWatcher` instances
> may still add or remove queues; `BaseTask` instances may not.
>
> `run_until_stopped()` owns the process, wait, and finalization loop for every
> task family. Background `run_forever()` and the spawned-task launcher delegate
> to that loop. Stop first records intent and wakes the loop. Finalization joins an
> external drive thread when requested, stops and joins local workers, and drains
> or terminates Manager children within one absolute remaining deadline, then
> closes reactor-owned resources exactly once
> only after no startup, turn, wait, or drive loop can still touch them. A timed-out
> active driver or standalone waiter retains its resources until its own finalizer runs; an idle manual
> owner's still-live application thread does not block finalization. For
> compatibility, `stop()` retains its `None` return and BaseWatcher-style
> bounded-wait contract; a timeout logs a warning and return is not proof of
> resource closure. The deadline bounds Weft-owned waits, not an OS/backend close
> already in progress. Callers that require joined completion must observe the
> drive thread or `is_running()` state.
>
> `stop()` and `cleanup()` are non-overridden public lifecycle templates.
> Concrete task families contribute ordered task-specific shutdown through a
> protected cleanup hook that receives the absolute deadline for any join/drain
> wait. The private finalizer invokes that hook, captures any failure, and always
> runs private BaseTask queue, waiter, strategy, thread-local, and weakref-finalizer
> cleanup afterward. It attempts all cleanup phases, records the first failure and
> logs later failures, then leaves `FINALIZING` for `CLOSED`; partial cleanup is not
> retried. A task-specific failure therefore cannot bypass shared cleanup.
>
> Worker-result delivery remains bounded and in-process. Ordinary task workers
> do not own Weft broker/store handles. The named TaskMonitor maintenance lanes
> retain their [OBS.13.10] exception. Their TaskSpec/config snapshots, queue/store
> handles, sink facades, lifecycle, and counters are worker-owned and close in that
> worker's `finally` path before the result is considered complete. Same-path sink
> facades intentionally lease one process-local writer/rotation owner; this is the
> only shared external-log object and it owns no task lifecycle or per-worker
> counters. A close failure yields a typed failed/pending maintenance result, never
> a successful result.

In [CC-2.5], replace the sentence beginning
"`weft/core/launcher.py` honors `next_wait_timeout()`" with:

> `weft/core/launcher.py` constructs the task, installs process-level signal and
> parent-loss adapters, and delegates execution to
> `BaseTask.run_until_stopped()`. `BaseTask` alone evaluates terminal state,
> pending worker activity, `next_wait_timeout()`, queue/local activity waiting,
> and finalization. The launcher must not maintain a parallel task-loop policy.

### 7.2 `docs/specifications/07-System_Invariants.md`

Add after [QUEUE.6]:

> - **QUEUE.7**: a live task declares every construction-fixed reactor role and
>   fixed support route that it watches, reserves into, or uses for durable task or
>   runtime output. The five BaseTask roles (`inbox`, `reserved`, `outbox`,
>   `ctrl_in`, `ctrl_out`), BaseTask support routes, and subtype-fixed roles are
>   pairwise distinct for the task lifetime, except for explicit, subtype-owned
>   semantic aliases named in code and firing tests. Runtime construction rejects
>   every other collision before building queue configs or opening/mutating broker
>   state. Payload-directed Heartbeat destinations are governed separately by
>   [MF-3.2] ingestion validation and are not construction-fixed roles. This does
>   does not remove the standalone `MultiQueueWatcher` dynamic-topology API; native
>   waiter rebuild/rebind after membership changes remains the documented section
>   6.5 deferral.

Add after [IMPL.9]:

> - **IMPL.10**: every task reactor instance has exactly one drive-owning thread.
>   `BaseTask.process_once()` enforces ownership before concrete turn policy;
>   `BaseTask.wait_for_activity()` verifies the same owner before waiter effects;
>   `BaseTask.run_until_stopped()` is the only task process/wait/finalize loop; and
>   stop never closes reactor-owned resources while startup, a turn, or a drive
>   loop can still touch them. Background, foreground, exceptional, and manual
>   exits converge on one idempotent finalizer with one absolute wait deadline for
>   driver/worker joins and Manager launch-drain/child-termination escalation.
>   Cleanup failures cannot strand `FINALIZING` or skip private base cleanup.
>   Public `wait_for_activity()`, `stop()`, and `cleanup()` cannot be overridden;
>   task-specific waiting and shutdown extend protected owned-wait and
>   deadline-aware cleanup hooks.

Add after [IMPL.10], atomically with Phase 5:

> - **IMPL.11**: TaskMonitor maintenance workers close every worker-owned queue,
>   Monitor store, TaskSpec/config snapshot, and external-sink facade in `finally`
>   and never share watcher, lifecycle, queue, store, sink counters, or mutable task
>   state with the reactor. Same-path sink facades lease one process-local
>   writer/rotation owner so only one live rotating handler exists per resolved
>   path. Built-in and runtime-cleanup results return frozen typed diagnostics only
>   after worker resources close; close failures produce failed/pending results;
>   cumulative external/deferred status is merged on the reactor thread.

### 7.3 `docs/specifications/05-Message_Flow_and_State.md`

Add after the current [MF-2] rules:

> Reactor ownership hardening does not replace Weft's reservation contract with
> peek/checkpoint delivery or a second durable output ledger. A crash still leaves
> the active input in `T{tid}.reserved` for the explicit [QUEUE.6] recovery policy.
> The SimpleBroker reference reactor's sidecar outbox is specific to its
> peek/checkpoint example and is not part of ordinary Weft task delivery.

Under [MF-3.2], add to the current Heartbeat rules:

> Heartbeat `destination_queue` remains payload-directed runtime egress, not a
> construction-fixed reactor role. Upsert validation rejects a destination equal to
> the Heartbeat task's own inbox, reserved, or control lanes and rejects BaseTask's
> reserved `weft.` system queue namespace before storing the registration. An
> ordinary destination task queue remains valid. Rejection uses
> the existing invalid-request lifecycle event and reserved-on-error policy and must
> not block a later valid mutation.

### 7.4 Final implementation mapping text

Do not add these claims in Phase 1. Add them with reciprocal code/test citations in
the strategy-B slice or Phase 6 named below, updating symbol names only if the
reviewed implementation uses an explicitly recorded deviation.

Under [CC-2.2.1] in `docs/specifications/01-Core_Components.md`:

> _Implementation mapping_: `weft/core/tasks/base.py::BaseTask` owns the declared
> role inventory, public turn/lifecycle templates, drive-owner state machine, and
> once-only watcher/resource finalizer. `weft/core/launcher.py::_task_process_entry`
> is the process/signal/parent-loss adapter. Consumer, Manager, HeartbeatTask,
> PipelineTask/PipelineEdgeTask, ServiceTask, Monitor, and TaskMonitor contribute
> protected turn, topology, or cleanup policy without replacing the templates.
> TaskMonitor worker isolation and typed diagnostic return live in
> `weft/core/monitor/task_monitor.py`; worker-local sink counters and the
> single-handler-per-path writer lease live in
> `weft/core/monitor/external_log.py`. Firing coverage is in
> `tests/tasks/test_task_execution.py`, `tests/core/test_manager.py`,
> `tests/tasks/test_pipeline_runtime.py`, `tests/tasks/test_task_observer_behavior.py`,
> `tests/tasks/test_signal_deferral.py`, and `tests/tasks/test_task_monitor.py`.

Under [QUEUE.7] in `docs/specifications/07-System_Invariants.md`:

> _Implementation mapping_: `BaseTask._reactor_queue_roles()`, its fixed support
> route inventory, and `_allowed_reactor_queue_aliases()` define and validate
> construction topology before queue configuration. Manager, PipelineTask,
> PipelineEdgeTask, and Monitor extend the inventory for subtype lanes and named
> semantic aliases, including Manager's services registry and Pipeline's status,
> state-registry, and internal-spawn routes. Heartbeat validates payload-directed
> destinations against the reserved `weft.` namespace at message ingestion under
> [MF-3.2]. The canonical, support-route, subtype, and Heartbeat
> dynamic-egress matrices in the task/manager/pipeline/observer/heartbeat test files
> fire this invariant.

Under [IMPL.10] in `docs/specifications/07-System_Invariants.md`:

> _Implementation mapping_: `BaseTask.process_once()`, `wait_for_activity()`,
> `run_until_stopped()`, `run_forever()`, `stop()`, `cleanup()`, and
> `_finalize_task_once()` enforce drive ownership, deadline propagation, and
> lifecycle ordering; `_task_process_entry` delegates once. Protected cleanup hooks
> in Consumer, ServiceTask, Manager, and TaskMonitor receive the absolute deadline;
> the private BaseTask cleanup phase always runs afterward. Manager's protected
> termination-policy hook preserves graceful drain and SIGUSR1 priority; Manager
> launch drain, child-exit polling, joins, and process-tree escalation consume the
> same absolute cleanup deadline. Lifecycle, signal, multi-child deadline,
> wait-owner, and cleanup-failure tests in the mapped files fire the invariant.

Under [IMPL.11] in `docs/specifications/07-System_Invariants.md`:

> _Implementation mapping_: TaskMonitor built-in and runtime-cleanup worker-context
> creation, snapshot/close helpers, typed results, and owner-thread diagnostic merge
> live in `weft/core/monitor/task_monitor.py`; the single-handler-per-path writer
> lease and worker-local sink counters live in
> `weft/core/monitor/external_log.py`. The two-cycle isolation, same-path rotation,
> jsonl-then-delete, retryable body/close failure, close-order, and live-control tests
> in `tests/tasks/test_task_monitor.py`,
> `tests/core/test_monitor_external_log.py`, and
> `tests/core/test_monitor_store.py` fire this invariant.

Under [MF-3.2] in `docs/specifications/05-Message_Flow_and_State.md`:

> _Implementation mapping_: `HeartbeatTask._handle_work_message()` validates
> payload-directed destinations against its resolved task-local roles and the
> reserved `weft.` system queue namespace before storing a registration;
> `_emit_due_registrations()` retains owner-thread writes to valid ordinary
> destination queues. Dynamic-egress rejection/progress coverage lives in
> `tests/tasks/test_heartbeat.py`.

## 8. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| [CC-2.2.1] / Phase 5 red test 1 | TaskMonitor worker snapshot copies the TaskSpec via `taskspec.model_copy(deep=True)`. | Shell copy plus deep-copied `state`/`metadata`; the frozen `spec`/`io` interiors are shared by identity, including nested `spec.args`/`spec.keyword_args` containers. | `FrozenDict` rejects `deepcopy`; `spec`/`io` are immutable by system invariant, so identity sharing of frozen interiors is safe. Accepted 2026-07-09 in-flight review. | None. When [IMPL.11] is promoted, its mapping must say "frozen `spec`/`io` interiors are intentionally identity-shared" rather than claiming a full deep copy. |
| [IMPL.10] / Phase 4 stop sequence steps 7-8 | Waiter reset and strategy close happen before the task-specific cleanup hook. | The private finalizer runs the subtype cleanup hook first; waiter reset and strategy close remain inside private base cleanup, which always runs afterward and still precedes shared queue closes. | Independent review traced no corruption path: activity callbacks cannot race a queue close because strategy close still precedes queue closes, and `MultiQueueWatcher.stop()` is never used as the mechanism. Subtype resources are not watcher-fed. Accepted 2026-07-09 in-flight review. | None. [IMPL.10] text in section 7.2 does not encode step ordering; do not add step-7-first wording to its mapping. |
| [CC-2.2.1] / [IMPL.10] cleanup authority | Three active/pending flags (`start_pending`, `turn_active`, `drive_loop_active`) were treated as exhaustive. | `wait_active` is a fourth lifecycle-lock-protected authority flag; standalone public wait publishes it before blocking and owns pending-stop finalization after unwind. | A standalone owned wait can touch the strategy/waiter without a turn or loop being active. The three-flag model allowed a foreign stop to close those resources underneath it. Corrected in the completed-work review and fired by `test_base_task_foreign_stop_defers_cleanup_during_owned_standalone_wait`. | Promoted text and [IMPL.10] now explicitly include wait-active authority. |
| [IMPL.10] / R-15 watcher queue leases | Close every `QueueRuntimeConfig.queue` lease from BaseTask finalization. | The review's primary-queue premise was incorrect: `_queue()` identity-caches the primary watcher queue, so existing cached-handle cleanup closes it. Additional subtype watcher facades held only in `_queues` retain SimpleBroker-owned finalizers until process teardown. | On SimpleBroker 5.2.0 SQLite, explicitly releasing all duplicate child-process leases reproducibly invalidates the parent process's still-live WAL generation. Section 13.2 retains the exact Weft patch, command, control, and failure output; the sibling SimpleBroker WAL-generation plan and red tests support the queue-layer mechanism. | This is not an accepted product deviation. R-15 remains externally blocked and clearance-gating until SimpleBroker's cross-process SQLite regression passes with every watcher facade explicitly closed; do not add a Weft keepalive-lease registry. |
| Section 12.3 Backstitch delta gate | Introduce zero touched-section/file diagnostics and clear every diagnostic for the four new codes. | Accept the executed delta of 69 `SPEC_MAPPING_RECIPROCAL_MISSING` warnings and 3 `SPEC_SECTION_UNMAPPED` infos for [CC-2.2.1], [QUEUE.7], [IMPL.10], and [IMPL.11], with zero new diagnostics outside those codes. | Backstitch v1 cannot attach `_Implementation mapping_` blocks to invariant bullets; the same diagnostic shape is pre-existing across invariant sections. Parser work or restructuring the invariants spec is out of scope. This is an accepted traceability deviation, not a passing zero-warning gate. | No behavior-spec change. Retain the saved before/after reports and the infrastructure blocker until the tooling/configuration can express these mappings. |

The implementation owner must append a row before knowingly departing from the
promoted delta. `pending` is not allowed in the final column when the plan is marked
completed.

## 9. Rollout, Rollback, and One-Way Doors

### Rollout

Land the work in the dependency order below. Do not deploy a partial rename where
some task classes override `process_once()` and others implement the protected hook.
That slice must be atomic across BaseTask and all five concrete task policies.

This is an umbrella plan with two mandatory landable implementation units. Do not
put both units in one review diff:

```text
Unit A: reference acceptance tests -> fixed topology -> public turn/wait template
        -> lifecycle/deadline/Manager termination -> core traceability
                                                    |
                                                    v
Unit B: TaskMonitor snapshots -> path-writer lease -> worker close results
        -> TaskMonitor traceability
```

- **Unit A, core reactor:** Phases 0-4 plus the corresponding Phase 6 mapping and
  documentation. Phase 2B may land as an acceptance-only commit before the core
  runtime edits.
- **Unit B, TaskMonitor resources:** Phase 5 plus its corresponding Phase 6 mapping
  and documentation. It starts only after Unit A's lifecycle fields and cleanup
  hook signature are stable.
- Run an independent completed-work review after each unit. Unit B must not be used
  to delay or obscure a Unit A lifecycle blocker.

**Dependency and parallelization table:**

| Workstream | Modules touched | Depends on |
| --- | --- | --- |
| Reference acceptance ports (Phase 2B) | `tests/tasks/`, `tests/core/` | Phase 0 baseline |
| Core topology and Heartbeat egress (Phase 2) | `weft/core/tasks/`, `weft/core/manager.py`, specs/tests | Phase 1 promotion |
| Public turn template (Phase 3) | task policies, task/manager tests | Phase 2 |
| Drive/lifecycle/Manager termination (Phase 4) | BaseTask, launcher, task policies, manager, lifecycle tests | Phase 3 |
| TaskMonitor resource isolation (Phase 5) | monitor implementation and monitor tests | Phase 4 lifecycle fields/hook signature |
| Reciprocal traceability (Phase 6) | specs, touched code/tests, plan/changelog | each owning unit |

- **Lane R:** Phase 2B acceptance tests may run in parallel with early Unit A spec
  preparation after Phase 0. If a port exposes a production defect, stop parallel
  work and route the defect into its owning sequential phase.
- **Lane A:** Phases 1 -> 2 -> 3 -> 4 -> Unit A traceability are sequential because
  they share BaseTask/task policy modules and progressively establish the contract.
- **Lane B:** Phase 5 -> Unit B traceability is sequential and starts only after
  Lane A lands.
- **Conflict flag:** Phase 3/4 and Phase 5 both touch TaskMonitor policy/lifecycle
  seams. Do not implement them in parallel worktrees. The acceptance-only Lane R
  may touch `tests/tasks/test_task_monitor.py`; coordinate that file or land Lane R
  first to avoid a mechanical merge conflict.

1. Record Phase 0 runtime, spec, dirty-tree, and existing-test-equivalence baselines.
2. Promote the strategy-A spec delta and record the promotion baseline.
3. Add construction-topology/support-route validation and Heartbeat dynamic-egress
   validation with [QUEUE.7].
4. Port/adapt the applicable reference control, budget, and ordering tests in
   Phase 2B before deeper lifecycle changes.
5. Add the BaseTask public template and migrate every concrete turn policy in one
   slice.
6. Consolidate the launcher/background loop, drive-owned wait, Manager termination
   policy, absolute cleanup deadline, and stop/finalization lifecycle with [IMPL.10].
7. Harden TaskMonitor worker resource isolation with [IMPL.11].
8. Reconcile mappings, code backlinks, docs, lessons, and traceability gates.

The changes do not alter persisted payloads or schemas. Old and new processes may
share a broker during a rolling source deployment because queue names, payloads,
reservation policy, and lifecycle events remain unchanged. Do not mix old and new
code inside one process.

One narrow runtime edge changes intentionally: a task constructed with an already
terminal TaskSpec or a pre-set stop event receives no unconditional first launcher
turn. Pending signal/parent-loss application is the exception. These are shutdown
or invalid-reentry states; add release notes only if embedders currently depend on
the old unconditional launcher turn.

### Rollback

Rollback is a source revert of the complete Unit A or Unit B implementation slice.
Do not revert half of a public-template/cleanup migration. No data migration
is required. Reserved work remains recoverable by the existing policy. If rollback
occurs after topology validation rejects a formerly accepted aliased TaskSpec, fix
the TaskSpec instead of treating the alias as a compatibility format; aliasing is an
invalid runtime shape, not durable data.

### One-way doors

- No new persisted schema or message shape is introduced.
- Queue deletion remains governed by existing task/TaskMonitor policies.
- The behaviors that become intentionally unavailable are driving/waiting one task
  from multiple threads, aliasing a construction-fixed task role with another role
  or protected support route, and registering a Heartbeat destination into its own
  watched/reserved/control lanes or the reserved `weft.` system namespace. These
  are unsafe states rather than supported compatibility modes.
- A third-party `BaseTask` subclass that overrides public `process_once()`,
  `wait_for_activity()`, `stop()`, or `cleanup()` will fail at class creation and
  must move its policy to `_process_reactor_turn()`,
  `_wait_for_reactor_activity()`, or `_cleanup_task_resources()`. This deliberate
  extension compatibility break is the enforcement mechanism, not merely a mypy
  hint. Direct BaseTask wait callers must first establish the drive owner. Document
  both changes in release notes if BaseTask subclassing/embedding is supported
  outside this repository.

### Post-deploy signals

- no increase in task startup failures except explicit queue-role collision errors;
- no `RuntimeError` for second-thread ownership in normal production paths;
- task process exit leaves no live worker thread, Monitor-store pool thread, or open
  external TaskMonitor sink;
- the process-local external-log writer registry returns to its pre-task size after
  final sink close, with at most one rotating handler per live path;
- STOP/PING latency remains within current task-reactor bounds while workers run;
- reserved queue and terminal state behavior remains unchanged.

## 10. Dependency-Ordered Implementation Tasks

### Phase 0: Establish and record the baseline

**Outcome:** distinguish current failures and user changes from regressions caused by
this work.

**Read first:** every source in sections 3 and 5, plus the complete current bodies of
`BaseTask`, `MultiQueueWatcher`, `_task_process_entry`, all five concrete turn
overrides, `ServiceTask`, and TaskMonitor's worker clone/worker functions.

**Comprehension checks:**

1. Which thread currently writes a Consumer result and applies reserved policy?
2. Why does `BaseTask.stop()` fail to see a manual thread that calls
   `run_until_stopped()`?
3. Which parent-loss operation must remain plain in-memory, and which thread must
   publish the terminal state?
4. Which TaskMonitor workers may open broker/store handles, and why does that
   exception not generalize to ordinary service workers?
5. Why is the reference sidecar outbox wrong for Weft's ordinary reserve path?
6. Which BaseTask global support queues can alias a local role today, and why is a
   Heartbeat `destination_queue` not part of the construction-fixed role map?
7. Which state changes occur inside BaseTask `wait_for_activity()`, and where must
   owner verification happen to precede them?
8. Why do `start_pending`, `turn_active`, `wait_active`, and `drive_loop_active`
   determine cleanup authority more accurately than `Thread.is_alive()`?
9. Which Manager owner-thread hook must preserve graceful drain and SIGUSR1 priority
   after pending-source consolidation?
10. Why must same-path external-log clients share one rotating writer while keeping
    TaskMonitor counters and lifecycle worker-local?

**Commands:**

```bash
. ./.envrc
git status --short
git rev-parse HEAD
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q
./.venv/bin/python -m pytest tests/tasks/test_service_task.py -q
./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py -q
./.venv/bin/python -m pytest tests/tasks/test_pipeline_runtime.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -k 'reactor or worker' -q
```

Record failures before editing. Do not label one pre-existing without rerunning it
against the baseline commit or otherwise producing evidence.

Before accepting any “already covered” reference-ledger row, read the cited test
body beside the reference test and run the exact nodes below. Confirm the assertions,
not the names, prove first-driven replay timing, identity/idempotence, transient
retry, cross-instance restart, and Weft's reservation-based error outcome. If an
assertion is only adjacent, reclassify the row as an adapted port in Phase 2B.

```bash
./.venv/bin/python -m pytest \
  tests/tasks/test_control_channel.py::test_ping_control_command_returns_pong \
  tests/tasks/test_task_execution.py::test_consumer_reactor_responds_to_ping_while_command_work_is_active \
  tests/tasks/test_task_execution.py::test_consumer_active_control_gets_turn_while_stream_events_remain \
  tests/tasks/test_task_monitor.py::test_task_monitor_jsonl_then_delete_defers_external_failure_and_deletes \
  tests/tasks/test_task_monitor.py::test_task_monitor_jsonl_then_delete_flushes_accumulated_deferred_reports \
  tests/core/test_monitor_store.py::test_monitor_store_deferred_writes_are_bounded_outbox_rows \
  tests/tasks/test_task_execution.py::test_base_task_worker_lane_delivers_errors_on_main_thread \
  tests/tasks/test_task_execution.py::test_base_task_worker_error_is_raised_on_main_thread \
  tests/tasks/test_task_execution.py::test_task_failure_leaves_message_in_reserved \
  -vv
```

**Stop if:** the baseline shows a valid role alias or a task class outside the five
known policy owners.

### Phase 1: Promote the reviewed spec delta

**Outcome:** put the reviewed requirements in the normative specs before shipped
code cites them.

**Files to change:**

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- this plan, to record the promotion baseline

**Required action:**

1. Apply only the strategy-A text from sections 7.1 and 7.3 unless review has
   approved an explicit revision. Phase 2 and Phase 4 own the strategy-B invariant
   bullets from section 7.2.
2. Add this plan to each touched spec's `Related Plans` or implementation-plan
   backlink list. The plan-authoring worktree already contains those three
   backlinks; confirm and preserve them rather than adding duplicates.
3. Do not add new implementation mapping claims yet.
4. Record the promotion baseline as the original commit plus exact worktree diff,
   unless the user asks for an intermediate commit.
5. Run spec and plan hygiene tests.

**Verification:**

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py -q
git diff --check -- docs/specifications docs/plans
```

**Stop if:** promoted wording would require changing reservation delivery, TaskSpec
payloads, or TaskMonitor cleanup policy.

### Phase 2: Reject unsafe task topology before side effects

**Outcome:** every live task declares its complete construction-fixed reactor and
support-route inventory; unsafe aliases fail before queue-config dict construction,
while the small set of intentional semantic aliases remain valid. Standalone
`MultiQueueWatcher` retains its dynamic API and polling fallback, subject to the
native-waiter rebind deferral in section 6.5. Heartbeat's payload-directed
destinations remain dynamic and receive a separate ingestion-time safety check.

**Red tests first:** `tests/tasks/test_task_execution.py`.

Add a parameterized real-broker test covering every pair among the five base
roles:

- `inbox`
- `reserved`
- `outbox`
- `ctrl_in`
- `ctrl_out`

For each pair:

1. build a real resolved `TaskSpec` whose two selected roles resolve to the same
   queue name;
2. construct `ReactorTestTask` or `Consumer` against a `tmp_path` database;
3. assert `ValueError` names both roles and the duplicate queue;
4. assert the database path was not created;
5. assert no worker thread with the task's TID short suffix exists.

Also parameterize empty-string/whitespace-only values for every configurable role
that does not fall back to a canonical default. Reject them with the role name
before queue construction. This is ordinary role-name validation, not a claim that
every Weft task must have a reference-style non-empty `input_queues` collection.

Add a focused test that calls `BaseTask.add_queue()` and `remove_queue()` after a
valid construction and expects a clear fixed-topology error. Keep the existing
`MultiQueueWatcher` direct-wait dynamic queue test unchanged and green, but cite it
only as API/invalidation/polling evidence, not background native-rebind proof.

Add a fixed support-route collision matrix. BaseTask supplies semantic support
roles for `WEFT_GLOBAL_LOG_QUEUE`, `WEFT_TID_MAPPINGS_QUEUE`,
`WEFT_STREAMING_SESSIONS_QUEUE`, and `WEFT_ENDPOINTS_REGISTRY_QUEUE`, even when a
handle is opened lazily. Collide each of the five configurable local roles with
each support route and assert rejection before database creation. The error must
name the local role, support role, and queue. This fires the reproduced
`inbox == weft.log.tasks` self-input defect.

Add subtype tests before production edits:

1. Manager: when the canonical manager inbox attaches
   `WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE` and its derived internal reserved lane,
   collide each extra role with each configurable base role. Assert the error
   names both semantic roles and occurs before database creation. This catches
   the current dict-overwrite case where custom `ctrl_in` equals the internal
   spawn inbox. Also declare `WEFT_SERVICES_REGISTRY_QUEUE` as Manager's fixed
   `services_registry` support route and collide it with every configurable base
   and internal role.
2. PipelineTask: declare `runtime.queues.events`, `runtime.queues.status`,
   `WEFT_PIPELINES_STATE_QUEUE`, and `WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE` as the
   fixed `events`, `status`, `pipeline_registry`, and `internal_spawn_requests`
   roles/routes. Also declare every precompiled stage/edge `ctrl_in` destination
   used by `_broadcast_control()` under a deterministic semantic role. Collide each
   fixed route with every base role and with one another. At minimum,
   the `events == ctrl_in`, `status == outbox`, and
   `pipeline_registry == internal_spawn_requests` cases must prove no handler or
   durable route can be silently overwritten/misdirected.
3. PipelineEdgeTask: declare `source_queue`, `target_queue`, and `events_queue`.
   Prove the compiled contract's intentional `source_queue == inbox` and
   `target_queue == outbox` aliases remain valid, then reject each non-allowed
   cross-role collision, especially target/events against inbox, reserved, and
   control.
4. `weft/core/tasks/monitor.py::Monitor` (not operational TaskMonitor): prove
   the default/custom `downstream_queue == outbox` alias remains valid, then
   reject downstream equal to inbox or ctrl_in. Do not reject an intentional
   downstream alias merely to make the matrix pairwise.
5. HeartbeatTask: prove an ordinary runtime destination such as another task's
   `T{tid}.inbox` is accepted and receives the due write. Then reject registration
   destinations equal to Heartbeat's own inbox, reserved, ctrl-in, or ctrl-out and
   parameterize representative names from every reserved system family:
   `weft.log.tasks`, `weft.state.services`, `weft.state.pipelines`,
   `weft.spawn.requests`, `weft.spawn.internal`, `weft.manager.ctrl_in`,
   `weft.manager.ctrl_out`, `weft.manager.outbox`, and an unassigned future-style
   name such as `weft.future_reserved`. Assert the shared
   `WEFT_QUEUE_NAMESPACE_PREFIX == "weft."` rule rejects the full prefix rather
   than maintaining a partial constant list. Invalid registration follows the
   existing lifecycle plus reserved-on-error policy, is not inserted into
   `_registrations`, and a later valid registration progresses. Do not add the
   dynamic destination to `_reactor_queue_roles()`.

Before freezing the matrices, run a fixed-route inventory over every BaseTask
subtype in scope. Inspect `_build_queue_configs()`, `_queue(...)` calls, and helpers
that open/write a queue without `_queue` (including `submit_spawn_request`). Record
each construction-fixed watched/reserved/output/support route in the implementation
notes and either add it to `_reactor_queue_roles()`/`_reactor_support_routes()` or
state why it is payload-directed and name its separate validator. The Manager
services route and all four Pipeline routes above are minimum required findings,
not a closed list that permits another fixed route to be omitted.

The implementation-start inventory found two non-obvious routes beyond that
minimum list: BaseTask's construction-fixed pipeline-owner `events_queue` metadata,
used by pipeline-owned Consumers and edges, and PipelineTask's precompiled child
control destinations. Both participate in construction validation. PipelineEdge
explicitly allows its owner-event route to alias its existing `events` role; no
other new alias is permitted. Blank owner-event routes and malformed embedded edge
controls fail before broker creation rather than being omitted from the inventory.

Run the new tests and observe them fail before production edits.

**Production files:**

- `weft/core/tasks/base.py`
- `weft/_constants.py`, adding the reserved `WEFT_QUEUE_NAMESPACE_PREFIX`
- `weft/core/manager.py`
- `weft/core/tasks/pipeline.py`
- `weft/core/tasks/monitor.py`
- `weft/core/tasks/heartbeat.py`
- `weft/core/monitor/task_monitor.py`, for its fixed service-registry support route
- `docs/specifications/07-System_Invariants.md`, adding [QUEUE.7] in the same slice
- tests only; do not edit `weft/core/tasks/multiqueue_watcher.py` unless a red test
  proves a BaseTask-only override cannot work

**Implementation detail:**

- Add protected `_reactor_queue_roles()` returning an ordinary
  `dict[str, str]`, protected `_reactor_support_routes()` returning the fixed
  BaseTask/subtype support routes, plus protected
  `_allowed_reactor_queue_aliases()` returning a set of unordered role-name pairs.
  BaseTask supplies the five base roles and its four fixed support routes; Manager,
  PipelineTask, PipelineEdgeTask, and Monitor extend the maps and own their narrow
  allowed-alias declarations. Manager includes the services registry; PipelineTask
  includes status, state registry, and internal spawn output. Do not add a public
  topology class, role registry, enum framework, or queue factory.
- After `_resolve_queue_names()` and before `_build_queue_configs()` or
  `super().__init__()`, call one private BaseTask validator over those hooks.
  Validate non-empty resolved names and every disallowed same-name pair. Do not
  validate only caller-provided `TaskSpec.io`, because it omits derived and
  runtime roles.
- Every construction-fixed role used as a key by `_build_queue_configs()` or as a
  fixed reserve/support/durable route with dangerous alias semantics must appear in
  a hook. Keep a nearby blast-radius comment in each subtype so a future lane cannot
  bypass the inventory silently. Do not use this sentence to pull payload-directed
  Heartbeat destinations into construction topology.
- Protect the canonical BaseTask role and support-route keys themselves: a subtype
  may extend but not replace their semantic keys, and role/support maps may not
  silently overwrite one another during validation.
- Add a narrow Heartbeat destination validator at mutation ingestion. It compares
  the requested destination against the resolved task-local
  watched/reserved/control roles and `WEFT_QUEUE_NAMESPACE_PREFIX` before storing
  the registration. It does not cache/open the destination, invent a general
  dynamic-route registry, or restrict ordinary `T{tid}.*`/application destination
  queues.
- Override `add_queue()` and `remove_queue()` on BaseTask with explicit errors.
- Do not add a queue factory, role enum, alias migration, or compatibility fallback.

**Focused gate:**

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py \
  -k 'queue_role or fixed_topology' -vv
./.venv/bin/python -m pytest tests/core/test_manager.py \
  tests/tasks/test_pipeline_runtime.py tests/tasks/test_task_observer_behavior.py \
  tests/tasks/test_heartbeat.py \
  -k 'queue_role or topology or alias' -vv
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py -q
```

### Phase 2B: Port the applicable reference control, budget, and ordering tests

**Outcome:** the reference suite is used as an acceptance source, not merely as a
design example. Every applicable test that is not already an exact Weft regression
gets a real-broker Weft port before the deeper lifecycle refactor.

**Files to change:**

- `tests/tasks/test_control_channel.py`
- `tests/tasks/test_task_execution.py`
- `tests/core/test_monitor_store.py`
- `tests/tasks/test_task_monitor.py`
- production code only if a port exposes a spec-confirmed defect

**Tests to port or adapt:**

1. Parameterize valid JSON non-object control payloads (`null`, list, number,
   boolean, and JSON string). Each must receive the existing structured unknown or
   invalid response, be exact-acknowledged from `ctrl_in`, and allow a later keyed
   PING to complete. Preserve plain unquoted `PING` support.
2. Send both an empty-command object and a named unknown command such as `DANCE`,
   each with `request_id`; assert the unknown response echoes the key, exact-acks
   the input, and a later command progresses.
3. Adapt the reference restart checkpoint test to Weft's delete/ack contract. Use
   custom control queue names so normal standard-queue cleanup does not erase the
   evidence. Process one keyed PING, stop the first task externally, construct a
   second task on the same broker, send a later keyed PING as the progress barrier,
   and assert the old input was not reprocessed. Do not use a fixed sleep.
4. Add a real Consumer two-message test for per-task single inflight order. Hold the
   first worker at a narrow event seam, assert only the first row is reserved and the
   second remains in inbox, release it, then assert outputs and reservation advance
   in source order.
5. Add an end-to-end TaskMonitor deferred-flush budget test. Seed at least three
   durable lifetime reports, configure the existing flush batch size to one, drive
   one cycle, and assert exactly one external row is emitted/marked while two remain
   pending. Drive later cycles and assert one additional durable row per cycle in
   source order. A focused MonitorStore `limit=1` query test may support this, but
   is not equivalent evidence by itself. Weft's store does not use the reference's
   `N + 1` sentinel, so do not invent one.
6. Strengthen the restart replay test to prove first-driven timing. Seed a deferred
   row, construct the replacement TaskMonitor, and assert before its first turn that
   the external file is absent/unchanged and the row remains pending. Call one
   public `process_once()`, then assert the row is emitted and marked/deleted under
   the configured policy.
7. Add a same-instance transient retry test. Make the real sink's first publication
   attempt fail through its narrow filesystem/emit seam, keep the same TaskMonitor
   alive, restore the seam, drive a later cycle, and assert that instance retries
   and clears the deferred row. Reconstructing a second TaskMonitor does not satisfy
   this row.
8. Keep the cross-instance crash/restart recovery test explicit and green. Preserve
   report-id upsert coverage as a MonitorStore contract, but do not call it exact
   broker-ID replay.

**TDD rule:** run each newly copied/adapted test before production edits. If it is
already green because Weft has the guarantee, retain it as the ported regression and
record "green on baseline" in the implementation notes. Red-green TDD does not
require breaking correct code to manufacture a red result.

**Focused gate:**

```bash
./.venv/bin/python -m pytest tests/tasks/test_control_channel.py -vv
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py \
  -k 'single_inflight or source_order' -vv
./.venv/bin/python -m pytest tests/core/test_monitor_store.py \
  -k deferred_writes -vv
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py \
  -k 'first_driven or same_instance_retry or deferred_flush_budget or restart' -vv
```

**Stop if:** matching a reference assertion would require replacing Weft's
reserve/delete contract with peek/checkpoint delivery. Classify that assertion as
not applicable in section 11.3 instead of silently changing Weft.

### Phase 3: Make `process_once()` the enforced ownership template

**Outcome:** the ownership check cannot be skipped by an in-repo concrete task
policy.

**This slice is atomic. Files to change together:**

- `weft/core/tasks/base.py`
- `weft/core/tasks/consumer.py`
- `weft/core/tasks/heartbeat.py`
- `weft/core/tasks/pipeline.py`
- `weft/core/manager.py`
- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_execution.py`
- `tests/helpers/weft_harness.py` only if its task fixtures or cleanup assumptions
  need migration after the repository-wide override audit
- nearest existing turn-order tests in:
  - `tests/core/test_manager.py`
  - `tests/tasks/test_heartbeat.py`
  - `tests/tasks/test_pipeline_runtime.py`
  - `tests/tasks/test_task_monitor.py`

**Red tests first:**

Before test 1, run:

```bash
rg -n 'class .*\(.*BaseTask|def (process_once|wait_for_activity|stop|cleanup)\(' weft tests extensions
```

Inventory production, extension, and test-only subclasses. The current
`ReactorTestTask` overrides `wait_for_activity()` as a test seam; Phase 4 must rename
that override to the protected owned-wait hook. Manager's public wait override is a
documenting pass-through and must be deleted in Phase 4. If the audit finds another
helper/subclass override, add its exact file to the owning atomic slice and migrate
it. Do not assume the production-only five-name list is the whole repository.

1. Same-thread repeated calls remain valid.
2. A turn completed on thread A followed by a call on thread B raises before the
   concrete policy counter, queue state, worker result list, and task state change.
3. Two simultaneous callers coordinated by `threading.Barrier` result in one owner
   and one deterministic ownership error. Use events/barriers, not `sleep()`, to
   create overlap.
4. A worker result is applied on the owner thread and cannot be drained by another
   thread.
5. Existing Manager, Consumer, Heartbeat, Pipeline, and TaskMonitor turn-order tests
   stay green.
6. Defining a subclass that overrides public `process_once()` fails at class
   creation with the forbidden method named. Phase 4 extends the same guard to
   `stop()` and `cleanup()` after their atomic migration.
7. For each concrete policy, set `should_stop` while a worker result is queued and
   ordinary queue input remains. One turn applies the bounded worker result and
   poll report but performs no new queue dispatch.

**Implementation detail:**

- Add drive-owner lock, diagnostic ident, and authoritative strong thread fields
  during BaseTask construction. Compare the `Thread` object, not only an ident that
  Python may recycle.
- Make `BaseTask.process_once()` the only public implementation. Mark it `@final`
  for type-check enforcement.
- Add a narrow `BaseTask.__init_subclass__()` guard for public `process_once()`.
  During Phase 4, extend the same guard to `wait_for_activity()`, `stop()`, and
  `cleanup()` after their in-tree/test overrides are migrated. This rejects
  third-party subclasses that replace the safety boundaries. Do not use
  descriptors, wrapper decorators, or general method registries.
- The public method claims/verifies ownership, processes a pending termination
  source once, then either calls protected `_process_reactor_turn()` for normal
  policy or `_process_stopping_reactor_turn()` when `should_stop` is true. The
  stopping hook's default drains the existing bounded worker-result budget and
  emits the poll report without dispatching a queue. Concrete policies should not
  duplicate this decision unless a red test proves task-specific stopping work.
- Move BaseTask's current turn body into the default protected hook.
- Rename all five concrete `process_once()` overrides to
  `_process_reactor_turn()` and preserve their bodies/call ordering. Where a
  concrete implementation currently calls `super().process_once()`, it must call
  `super()._process_reactor_turn()` at the same semantic point.
- Remove duplicated `_process_pending_termination_signal()` calls from concrete
  turn hooks after the template owns one polymorphic pending-termination call.
  During this slice, preserve Manager's existing override and separate pending
  field behind that call; do not delete them until Phase 4 atomically introduces
  the shared pending-source snapshot and Manager policy hook. Audit every early
  `should_stop` return so it cannot strand a queued worker result; the public
  stopping hook is the single answer.
- Keep the guard local and explicit. Phase 3 starts with `process_once()`; Phase 4
  completes the four-name guard for process/wait/stop/cleanup. It is not a plugin
  framework.
- Keep existing spec citations during this partial slice. Phase 6 adds reciprocal
  [CC-2.2.1] mapping and code citations only after the full section, including
  lifecycle/finalization, is implemented.

**Structural audit:**

```bash
rg -n '^    def process_once' weft/core
```

Expected result: only `weft/core/tasks/base.py` defines the public method.

**Focused gates:**

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py \
  -k 'reactor_owner or main_thread or worker_result' -vv
./.venv/bin/python -m pytest tests/core/test_manager.py \
  tests/tasks/test_heartbeat.py tests/tasks/test_pipeline_runtime.py \
  tests/tasks/test_task_monitor.py -q
```

**Stop if:** any policy needs broker effects before the ownership template. That is
evidence the hook boundary is wrong.

### Phase 4: Consolidate drive, wait, stop, and finalization

**Outcome:** one BaseTask loop and one shutdown path cover spawned, direct,
background, exceptional, and manual driving.

Execute Phase 4 as these dependency-ordered red-green sub-slices. Keep the final
landing atomic if needed for template compatibility, but do not implement the whole
phase before running a focused test:

1. **4A, startup registration:** explicit `STARTING` state before start,
   start-failure rollback, stop/start interlock, manual-owner then start rejection.
2. **4B, loop truth:** parameterized truth-table rows, strategy start once,
   exception/max-iteration finalizer entry.
3. **4C, stop/join/deadline:** external join, join-false, one absolute timeout,
   active-turn self-stop, idle-manual-owner finalization, cleanup failure, repeated
   stop.
4. **4D, polymorphic cleanup:** migrate Consumer, ServiceTask, Manager, and
   TaskMonitor hooks together; prove Manager launch-drain and subtype ordering.
5. **4E, watcher release adapter:** waiter, strategy, thread-local, weakref
   finalizer, and `_running_event` tests.
6. **4F, launcher adapter:** delegate once and remove duplicated loop decisions.
7. **4G, signals/parent loss:** task-owned SIGINT deferral, shared pending-source
   snapshot, Manager graceful-drain policy, KILL priority, and bounded wait proof.

**Files to change:**

- `weft/core/tasks/base.py`
- `weft/core/launcher.py`
- `weft/core/tasks/consumer.py`
- `weft/core/tasks/service.py`
- `weft/core/tasks/interactive.py`
- `weft/core/tasks/sessions.py`
- `weft/core/manager.py`
- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_execution.py`
- `tests/tasks/test_service_task.py`
- `tests/core/test_manager.py`
- `tests/tasks/test_task_monitor.py`
- `tests/tasks/test_signal_deferral.py`
- `tests/tasks/test_multiqueue_watcher.py`
- `tests/test_harness_registration.py` only if a real process-level cleanup
  regression belongs there

Reuse the existing `PARENT_LOSS_WAKE_INTERVAL_CEILING` from
`weft/_constants.py`; do not add or retune a timing constant in this change.

**Red tests first:**

1. `run_until_stopped(max_iterations=1)` finalizes once and closes cached handles.
2. An exception from `_process_reactor_turn()` propagates and still finalizes once.
3. `start()`/background drive uses the BaseTask loop, not BaseWatcher's `_drain_queue`
   loop; capture `threading.excepthook` so unexpected thread exceptions fail.
4. External `stop(join=True)` waits for a manually created drive thread before
   cleanup closes queues.
5. `stop(join=False)` while another drive thread is active requests stop but leaves
   resources open; the drive thread's `finally` closes them after exit.
6. Calling `stop()` twice does not re-open or re-close task queues and does not
   duplicate endpoint/control cleanup.
7. A stop deadline that expires while the drive thread remains alive does not close
   the queues, logs the timeout, and retains the existing `None` return. Release the
   test barrier, join/observe `is_running() == false`, and then assert finalization.
8. `_task_process_entry()` calls the task's `run_until_stopped()` exactly once and
   does not separately call `process_once()` or `wait_for_activity()`.
9. Parent loss records termination from the watcher thread, is observed by the
   bounded parent-watch wait, and publishes cancellation only when the owner
   processes the pending flag.
10. Existing signal-deferral tests still prove the Python signal handler performs
    only the plain in-memory `note_termination_signal()` call.
11. The polling strategy starts once on the drive owner and closes once on every
    exit; the inherited weakref finalizer is detached after explicit close.
12. `is_running()` stays true through final cleanup and becomes false only after
    strategy, thread-local, and task resources are closed.
13. Consumer interactive/session cleanup, ServiceTask group shutdown, Manager
    launch drain/child cleanup, and TaskMonitor sink cleanup do not run on an
    external caller while a turn is blocked. Each still runs once after unwind in
    its current relative order.
14. Background startup registers the intended drive thread before `Thread.start()`.
    State is `STARTING` at that barrier. A concurrent stop cannot finalize before
    `Thread.start()` succeeds/fails and the driver either enters or is rolled back;
    a start failure restores `NEW` without a stale owner.
15. Owner-thread `stop()` or `cleanup()` during an active turn records stop only.
    `process_once()` finalizes after that manual turn unwinds when no drive loop owns
    the later `finally`.
16. Starting background drive after a manual owner was claimed fails before the new
    thread is launched; stop after `CLOSED` remains idempotent.
17. Direct main-thread `run_forever()` receives SIGINT through the task-owned plain
    recording handler; no stop lock or broker operation occurs in the signal frame.
18. Pending SIGUSR1 plus parent loss resolves as KILL, and parent loss during a long
    wait is processed within `PARENT_LOSS_WAKE_INTERVAL_CEILING` without claiming an
    immediate native-wait wake that the current waiter cannot provide.
19. A same-thread `process_once()` after `CLOSED` raises before touching a closed
    queue; a rejected second-owner call leaves the established owner's lifecycle
    state unchanged.
20. `test_base_task_worker_result_wakes_background_reactor`: arm a background task
    with a long caller timeout and blocked real worker, release the worker, and
    prove its local result is applied well before that timeout through the existing
    bounded worker-result wake/cap path.
21. `test_base_task_input_activity_wakes_background_reactor`: arm a long background
    wait, wait for an explicit event from a delegating real-waiter wrapper proving
    the driver entered the actual `wait()`, write through an independent real Queue,
    and prove the handler runs before the timeout through the multi-queue waiter.
    This is distinct from the standalone MultiQueueWatcher wake test.
22. The worker-result wake test uses the same entered-real-wait event before it
    releases the worker. A fast worker completion before the driver blocks does not
    satisfy the regression.
23. After a standalone `process_once()` completes on a still-live application/main
    thread, a foreign `stop()` sees no startup, turn, wait, or drive loop active and
    finalizes once. Raw owner-thread liveness must not defer cleanup forever.
24. A foreign call to BaseTask `wait_for_activity()` before ownership or after a
    different thread owns the reactor raises before `_ensure_multi_activity_waiter`,
    pending-state hints, or queue access. The owner can still wait normally;
    standalone MultiQueueWatcher's public wait remains unchanged. Defining a
    BaseTask subclass that overrides public `wait_for_activity()` fails at class
    creation. Manager's pass-through override is gone, and ReactorTestTask records
    waits through the protected hook instead.
25. One absolute deadline per finalizer/caller is threaded through driver join,
    ordinary worker joins, Manager active-launch drain, both initial child-exit
    waits, and every per-child join/process-tree escalation timeout. Instrument
    monotonic time plus barriers with at least three live children to prove no
    helper starts a fresh relative budget and total wait does not multiply by child
    count. At deadline, issue only the documented non-waiting best-effort kill rung,
    retain surviving PID diagnostics, and continue through cleanup steps that do
    not wait on active work. If an external caller times out and the active driver
    later owns finalization, that later background finalizer gets one new default
    cleanup deadline; it is not part of the returned caller's bounded wait.
26. A task-specific cleanup hook that raises still leads to private BaseTask waiter,
    strategy, queue, thread-local, and weakref-finalizer cleanup, then `CLOSED` with
    one retained primary cleanup error. Repeated stop does not retry or duplicate
    the failed hook. If a turn exception is already propagating, it remains primary.
27. Manager parent loss with live children enters the existing graceful shutdown
    drain and does not generic-cancel immediately. A simultaneous pending SIGUSR1
    wins over parent loss and follows Manager's current KILL-class child termination
    behavior. Both broker-visible outcomes occur only on the owner thread.
28. A Manager child-launch worker released after the caller's cleanup deadline
    rechecks shutdown after `launch_task_process()` returns and reaps the newly
    created process instead of attempting to publish an untracked late result.

**Canonical loop truth table:** replace both current loops with these decisions.
The table intentionally prefers safe no-new-work behavior for pre-stopped/terminal
instances over the launcher's current unconditional first turn; record that narrow
edge change in rollout notes. Evaluate top-to-bottom, first matching row wins;
`FINALIZING`/`CLOSED` always outranks a signal that arrives after loop exit.

```text
background start: NEW -> STARTING -> DRIVING -------------------------+
                         | start fails -> NEW                         |
manual first turn: NEW -----------------> DRIVING                     |
                                                                     v
stop request:       DRIVING -> STOP_REQUESTED -> FINALIZING -> CLOSED
                       ^             |                    |
                       |             +-- active work -----+
                       |                 owns later finalizer
                       +-- idle manual owner does not block caller finalization

Authority to touch reactor resources:
  STARTING or turn_active or wait_active or drive_loop_active
    = defer to intended/active driver
  owner Thread merely alive, with all four false = safe for caller finalization
```

| State at top of loop | Required action |
| --- | --- |
| lifecycle `FINALIZING`/`CLOSED` | Run no turn; return through the once-only finalizer. |
| lifecycle `STARTING` on intended background thread | Atomically enter `DRIVING`, clear startup-pending, mark drive-loop-active, then evaluate the next row. |
| pending real signal or parent loss | Run one owner turn to apply it, even if a stop flag is also set; then re-evaluate. |
| watcher stop set, no pending termination source | Run no turn; finalize. |
| TaskSpec already terminal | Run no turn; finalize, even if a worker is still alive. |
| `should_stop`, no active worker and no queued worker result | Run no turn; finalize. |
| `should_stop` with active worker or queued result | Run bounded turns that drain/apply worker results but dispatch no new queue work; finalize when worker activity ends. |
| normal with queued worker result | Run a turn immediately, then re-evaluate before waiting. |
| normal with active worker but no result | Run the existing bounded turn, then use the existing worker-active wait cap. |
| normal idle | Run one turn, compute `next_wait_timeout()`, and wait once. |
| terminal status reached during a turn | Do not start another turn; finalizer stops remaining workers. |
| `max_iterations` reached | Do not start another turn; finalize even if work remains. This remains a test/embedding escape hatch, not normal production completion. |

Add one parameterized test per meaningful row. Assert queue effects, applied worker
results, turn count, and finalization count rather than only a private predicate.

**Implementation detail:**

- Keep `run_until_stopped(poll_interval, max_iterations)` as the interface. Its loop
  owns:
  - terminal and `should_stop` checks;
  - pending worker activity;
  - one `process_once()` call;
  - `next_wait_timeout()` calculation;
  - `wait_for_activity()`;
  - a `finally` that enters the idempotent cleanup wrapper.
- Make BaseTask `wait_for_activity()` a `@final` public template with a first-line
  owner verification before `_ensure_multi_activity_waiter()` or pending-state
  hints, then delegate to protected `_wait_for_reactor_activity(timeout)`. Move the
  current BaseTask worker-cap plus `super().wait_for_activity()` body into that
  protected hook. It does not claim an owner from an unowned thread; direct
  embedders establish ownership through `process_once()` or
  `run_until_stopped()` first. Delete Manager's redundant public pass-through
  override and rename ReactorTestTask's wait recorder to the protected hook. Extend
  the runtime class guard to this fourth public template. Leave
  MultiQueueWatcher's method unchanged for standalone callers.
- Claim/verify the strong drive owner at the first line of
  `run_until_stopped()`, before strategy startup, terminal inspection, or any
  blocking operation.
- Implement one named lifecycle state machine in BaseTask. Background start follows
  `NEW -> STARTING -> DRIVING`; normal stop follows
  `DRIVING -> STOP_REQUESTED -> FINALIZING -> CLOSED`; also allow
  `NEW -> FINALIZING` for never-driven cleanup and `DRIVING -> FINALIZING` for
  bounded/exceptional loop exit. Track `start_pending`, `turn_active`,
  `wait_active`, and `drive_loop_active` under the dedicated task-lifecycle lock.
  A rejected second
  owner raises without mutating the established owner's state. Do not reuse
  SimpleBroker's `_stop_lock`, and never hold the task lock while dispatching,
  waiting, joining, closing a handle, or invoking a task-specific cleanup hook.
  Calling `Thread.start()` while publishing/rolling back `STARTING` is the narrow
  allowed lock-held operation; the child may block briefly on the same lock before
  entering `DRIVING`.
- Start the inherited polling strategy at most once on the drive owner before the
  first turn. Do not wrap task turns in BaseWatcher's retry loop; existing Weft
  exception policy remains unchanged. SimpleBroker's `_start_strategy()` re-checks
  the stop event and raises the public `StopWatching` sentinel; a stop request that
  lands between the template's `should_stop` read and strategy startup must not let
  `StopWatching` escape the public turn template. Catch it at the strategy-start
  seam and run that turn as the stopping turn (resolution of the 2026-07-09 review
  blocker; fired by
  `test_base_task_process_entry_publishes_turn_active_with_owner_claim`).
- Override `BaseTask.run_forever()` so `BaseWatcher.start()` reaches the same task
  loop and signal-handler context. Preserve BaseWatcher's `_running_event` set/clear
  contract and thread-local cleanup semantics; background callers may observe that
  event even though the task policy loop changes.
- Override the background start/run-in-thread seam narrowly enough to create the
  `Thread`, set it as intended owner, enter `STARTING`, and call `Thread.start()`
  while the lifecycle lock prevents stop from observing a half-published outcome.
  If start raises, restore `NEW` and clear the intended owner before releasing the
  lock. After success, leave `start_pending` true until the new thread acquires the
  lock and atomically enters `DRIVING`/`drive_loop_active`; a stop that wins that
  race records intent and, if joining, waits outside the lock for the driver to own
  finalization. Do not decide safety from pre-start `Thread.is_alive()`.
- Reduce `_task_process_entry()` to construction, process signal/parent-loss adapter
  setup, one `run_until_stopped()` call, and hard-exit handling.
- Override the BaseTask SIGINT handler used by `run_forever()` so it only calls
  plain `note_termination_signal()` and returns. It must not call `stop()`, set an
  Event, notify a strategy, write a queue, or raise from the signal frame.
- Give ordinary-thread parent loss its own pending flag/method. It must not reuse or
  overwrite `_pending_termination_signum`. BaseTask takes and clears both pending
  sources on the owner turn, resolves KILL-class SIGUSR1 before parent-loss/ordinary
  STOP, and calls one protected `_apply_termination_request(...)` policy hook.
  Ordinary tasks retain generic terminal handling. Manager removes its separate
  `_pending_termination_signal` application seam and overrides the protected policy
  hook: KILL preserves current immediate child termination plus base KILL handling;
  STOP/parent loss enters current `_begin_shutdown_drain()` and preserves graceful
  child drain. Signal recording remains plain in-memory assignment. Do not claim
  that `_strategy.notify_activity()` interrupts `MultiQueueWatcher`'s directly
  owned waiter; instead cap foreground-parent-watch waits at
  `PARENT_LOSS_WAKE_INTERVAL_CEILING`, then recheck the pending parent flag.
- Make Manager child teardown deadline-aware end to end. Change
  `_drain_active_child_launches_for_cleanup(deadline)`,
  `_terminate_children(deadline)`, and `_wait_for_children_to_exit(deadline)` to
  accept an absolute monotonic deadline; no method creates a relative timeout.
  Every `Process.join()`, `terminate_process_tree()`, and child-exit polling rung
  caps its timeout to `max(0, deadline - time.monotonic())`. Iterate children under
  one shared budget rather than allocating the current 0.2/0.5/2/1/0.2-second
  sequence per child. Once exhausted, perform only non-waiting best-effort
  `Process.kill()` for still-live direct children, record surviving child/PID
  diagnostics, and proceed without another join/tree wait. The double-wait
  prohibition must not remove within-budget SIGKILL escalation for descendants:
  `terminate_process_tree(..., kill_after=False)` everywhere leaves a
  TERM-trapping grandchild alive forever even when budget remains. While budget
  remains, either split the remaining time across the TERM-wait and KILL rungs
  (for example `timeout=remaining / 2, kill_after=True`) or follow the TERM pass
  with a non-waiting SIGKILL sweep over the descendant PID snapshot. Fire it with
  a test whose child spawns a SIGTERM-trapping subprocess and assert the tree is
  gone within the caller deadline (open 2026-07-09 review finding R-1). Update every non-cleanup
  `_terminate_children()` caller, including Manager KILL policy, to compute one
  explicit caller-owned absolute deadline before entry; the callee never invents
  one.
- Thread the same absolute cleanup deadline through Consumer interactive/session
  shutdown and ServiceTask worker-group sentinel/join waits. Cleanup-only calls to
  `InteractiveTaskMixin._interactive_shutdown()`, `CommandSession.terminate()`,
  `AgentSession.close()`/`terminate()`, and `_stop_service_worker()` must cap every
  internal wait by remaining time. Ordinary runtime calls may keep their existing
  relative defaults. Manager's exited-child cleanup joins must likewise consume
  the caller deadline. Process-tree escalation must not use a helper mode that can
  wait twice for the same remaining budget.
- Make public `stop()` and `cleanup()` `@final`. Add a private
  `_finalize_task_once(deadline)`; a protected
  `_cleanup_task_resources(deadline)` hook for subtype resources; and a private
  `_cleanup_base_task_resources(deadline)` containing the current BaseTask cleanup
  body. Atomically rename the public cleanup overrides in Consumer, ServiceTask,
  Manager, and TaskMonitor to subtype-only protected hooks. Preserve their current
  task-specific ordering, but remove the BaseTask `super().cleanup()` dependency:
  the private finalizer calls the subtype hook, captures any failure, and always
  calls private base cleanup afterward. Delete Consumer's public `stop()` override;
  its interactive/session shutdown belongs in its subtype hook.
  `BaseTask._cleanup_task_resources()` is a no-op extension point. A subclass of a
  policy-bearing class such as ServiceTask still calls
  `super()._cleanup_task_resources(deadline)` to preserve task-specific inheritance
  order, but reaching BaseTask through that chain is not how shared queues close.
- Route both public methods through one private request/join/finalize adapter. It
  computes one absolute monotonic deadline for that caller. If the caller becomes
  finalizer, worker joins, Manager launch drain, and every deadline-aware subtype
  hook receive only that deadline and calculate remaining time. `cleanup()` uses
  the existing default two-second budget; it is not a second close path. If an
  active driver owns later finalization, the caller returns at its deadline and the
  driver creates one default cleanup deadline when its loop/template `finally`
  begins. No helper inside either finalizer may start a fresh relative timeout. If
  startup/turn/wait/drive-loop activity remains, both public methods return without
  closing and the responsible driver finalizes later.
- Track `turn_active`, `wait_active`, and `drive_loop_active` under the lifecycle
  lock. An
  owner-thread stop/cleanup inside the protected turn cannot finalize. When a
  standalone manual `process_once()` unwinds with stop requested and no drive loop
  active, its template `finally` owns finalization; a loop-driven turn leaves it to
  the loop `finally`. After a completed standalone turn without stop, the strong
  owner identity remains authoritative for future turns, but its still-live Thread
  does not block a foreign finalizer because no startup, turn, wait, or drive loop is
  active.
- Publish `wait_active` before entering the protected standalone wait and clear it
  in the public wait template's `finally`. A foreign stop must defer cleanup while
  that flag is set; after the wait unwinds, the owner finalizes there when stop is
  pending and no drive loop is active.
- TaskMonitor's `_worker_lane_snapshot_only` branch remains a protected-hook
  input to a dedicated `_close_worker_local_resources()` helper. A shallow
  worker copy must never call public `cleanup()` or `stop()`, because it shares
  lifecycle fields such as the live reactor's stop event and drive-owner state.
  Phase 5 strengthens what that worker-only helper owns.
- Stop sequence:
  1. record watcher stop intent and wake the strategy;
  2. compute one absolute deadline for this caller/finalizer;
  3. snapshot owner plus
     `start_pending`/`turn_active`/`wait_active`/`drive_loop_active` under the task
     lock, then release the lock;
  4. if requested and external while any active/pending flag is true, join the
     intended/active thread only within remaining time; join-false returns;
  5. recheck the active/pending flags under the lock. If any remain, log timeout as
     applicable and return without cleanup. Do not substitute `Thread.is_alive()`;
  6. otherwise enter `_finalize_task_once(deadline)`; the current owner may do this
     only from template/loop `finally`, after its last turn has unwound. A driver
     deferred by an earlier external caller computes its own one default deadline at
     that point;
  7. reset the multi-queue waiter and close the inherited strategy so no activity
     callback can race resource close;
  8. call the deadline-aware subtype cleanup hook. Capture its first failure, then
     always run private BaseTask cleanup. Base cleanup stops/joins ordinary worker
     lanes using remaining time before closing shared queues; move the old waiter
     reset out of base cleanup to avoid duplicate ownership;
  9. clean inherited thread-local broker connections and detach the inherited
     weakref finalizer;
  10. in a final state-update block, retain the immutable cleanup-error snapshot,
      clear `_running_event`, and mark `CLOSED` even when cleanup failed.
- Do not call `MultiQueueWatcher.stop()`/`BaseWatcher.stop()` as the join or
  finalization mechanism. `MultiQueueWatcher.stop()` resets the waiter before it
  knows about a manual driver; `BaseWatcher.stop()` holds `_stop_lock` across join,
  sees only weak `_thread`, and intentionally skips strategy/finalizer cleanup
  while the current background thread is alive. Use their protected resources in
  the explicit BaseTask finalizer without editing SimpleBroker internals.
- Self-stop never joins the current thread.
- Do not catch and suppress policy exceptions in `run_until_stopped()`.
- Do not add a destructor or finalizer that performs broker I/O.
- Add [IMPL.10] from section 7.2, its firing-test references, and the implementation
  mapping update atomically at the end of this slice, once both Phase 3 ownership and
  Phase 4 lifecycle behavior are true.

**Focused gates:**

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py \
  tests/tasks/test_signal_deferral.py tests/tasks/test_multiqueue_watcher.py \
  -k 'run_until or stop or owner or wait_for_activity or parent_loss or signal or deadline or cleanup_error' -vv
./.venv/bin/python -m pytest tests/core/test_manager.py \
  -k 'parent_loss or signal or cleanup or deadline' -vv
./.venv/bin/python -m pytest tests/tasks/test_terminal_event_retry.py -q
rg -n '^    def (stop|cleanup)' \
  weft/core/tasks/base.py weft/core/tasks/consumer.py weft/core/tasks/service.py \
  weft/core/manager.py weft/core/monitor/task_monitor.py
rg -n '^    def wait_for_activity' weft tests extensions
```

Expected structural result: only BaseTask defines public `stop()` and `cleanup()`;
the four concrete task families define `_cleanup_task_resources()` where needed.
Only MultiQueueWatcher and BaseTask define public `wait_for_activity()` in
production; task-specific/test wait policy uses `_wait_for_reactor_activity()`.

### Phase 5: Isolate and close TaskMonitor maintenance resources

**Outcome:** the only broker-capable worker exception is still bounded, but its live
handles are truly worker-owned and have deterministic lifetime.

Execute Phase 5 as four red-green sub-slices:

1. **5A, path-writer ownership:** one refcounted writer/rotating handler per resolved
   path, worker-local sink facades/counters, and owner-delta merge over two real
   cycles.
2. **5B, built-in worker isolation:** sever the complete live-resource inventory,
   close in `finally`, return/apply frozen diagnostics.
3. **5C, runtime-cleanup isolation:** run terminal/reserved/dead-TID cleanup on the
   isolated worker, extend its typed result, and preserve retryable failure.
4. **5D, integrated safety:** real control responsiveness while each worker is
   blocked, store/sink/queue close order and close failures, same-path rotation,
   and cumulative diagnostics.

**Files to change:**

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/external_log.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_external_log.py`
- `tests/core/test_monitor_store.py` only if the existing store close seam lacks a
  usable real-resource assertion

**Red tests first:**

1. Configure a TaskMonitor with a real external log path under `tmp_path`. Build a
   worker-local monitor and assert, by walking `vars()` against an explicit
   shareable-field allowlist, identity-disjoint values for every reachable live
   resource: mutable `taskspec`, mutable `_config`, external sink facade/counters,
   MonitorStore, managed queue handles, `_queue_obj`, `_queues[*].queue`, strategy,
   multi-queue waiter, stop/running events, thread locals, lifecycle/worker/service
   locks, result queue, drive owner, and weakref finalizer. The worker uses
   `taskspec.model_copy(deep=True)` plus `copy.deepcopy(_config)` for broker config.
   BrokerTarget, immutable scalars, function/class objects, and frozen work
   values may be shared. The only intentional mutable shared object is the
   refcounted same-path writer described below; it owns no task lifecycle/counters.
2. Close the worker-local resources and prove the reactor sink still emits to the
   real file. Do not mock the sink, logger, handler, or filesystem.
3. Run two consecutive built-in worker cycles. Assert worker sink facades/counters
   are distinct, both lease the same path writer while concurrently live, exactly
   one rotating handler exists for that path, result publication occurs after
   sink/store/queue close on success and exception, and owner-applied
   `total_emitted`/blocked/deferred diagnostics accumulate rather than reset. Assert
   the process-global logger registry grows by at most one path-derived writer name,
   not by owner or cycle, and the writer registry entry disappears after the last
   sink closes.
4. Exercise terminal-control cleanup in real `jsonl_then_delete` mode. Hold the
   worker at the lifetime-report handoff seam and assert its sink is distinct and
   the live TaskMonitor's external/deferred fields remain unchanged. Release it;
   assert the typed result is published only after worker sink/store/queues close,
   then the owner applies its returned diagnostics and preserves cumulative counts.
5. Repeat the runtime-cleanup test with external emission failure. Assert durable
   deferral remains retryable, the typed result is pending/failed rather than a task
   crash, and a later worker can flush/continue under the existing policy.
6. Wrap a real `MonitorStore` with a close-recording delegating adapter only at the
   store factory seam. Assert built-in and terminal/reserved/dead-TID cleanup stores
   close on success and failure. Do not replace broker/store behavior with an
   in-memory fake.
7. While either worker kind is held with an event, send real PING/STATUS through
   real queues and prove the live reactor uses cached diagnostics and remains
   responsive.
8. Assert worker-local close leaves the live reactor stop event, lifecycle state,
   drive owner, queue cache, strategy/waiter, and external sink usable. This private
   identity evidence must be paired with the later real PING and file emit.
9. Force rotation with reactor, built-in, and runtime-cleanup sink facades live for
   the same path. Use a barrier for simultaneous first acquire and assert the
   registry still creates one writer/handler. Serialize emits through the shared
   writer, verify complete JSONL rows across active/rotated files, and assert only
   that handler performs rollover. Run on the platform's real filesystem; do not
   mock handler locks or `doRollover()`.
10. Make each worker-local close target fail in turn through a delegating real
    resource: store, sink facade lease release, and queue. Assert close attempts
    continue for later resources, the published typed result is failed/pending, no
    success diagnostics are merged, and a later cycle remains retryable. A close
    failure must not escape as an untyped worker crash or report success.

**Implementation detail:**

- Keep current TaskMonitor cleanup policy, exact-delete semantics, retention, and
  deferred-write schema. Result envelopes may grow only to carry the diagnostics
  needed for owner-thread application.
- Keep the private shallow policy snapshot only if an explicit allowlist says what
  may remain shared: immutable scalars, frozen runtime values, BrokerTarget,
  function/class objects, and the intentional path-writer lease only. Every other
  mutable field outside that allowlist must be
  copied, replaced, or cleared, and the `vars()` identity test must fail when a new
  mutable TaskMonitor/BaseTask field is added without a decision. This is the
  blast-radius guard for future fields and avoids a larger policy-method extraction.
  `_worker_local_monitor_clone()` must replace or clear
  at least `taskspec` via `model_copy(deep=True)`, `_config` via `copy.deepcopy`,
  `_queue_obj`, `_queues`, `_queue_cache`, `_ctrl_out_queue_obj`,
  `_monitor_store`, `_strategy`, `_multi_activity_waiter`, `_thread`, `_thread_local`,
  `_stop_lock`, stop/running events, BaseTask lifecycle state/locks/owner, generic
  worker result state, service-worker registries/locks, endpoint/session fields, and
  the external sink. If a worker path needs another live field, add it to this
  inventory and test before continuing. Do not fall back to “copy then hope.”
- Factor one sink-construction helper used by reactor startup and both worker paths.
  A worker gets a fresh sink facade or `None`; it never retains the reactor's sink
  object or counters.
- Replace handler-per-sink construction with one private process-local writer
  registry keyed by resolved path. Each entry owns exactly one logger,
  `_RaisingRotatingFileHandler`, and lock, plus a refcounted acquire/release seam.
  One module-level registry lock serializes lookup/create/refcount/remove so two
  simultaneous first acquires cannot create two writers. Reactor and worker sink
  facades lease that writer but retain independent health and counter state. All
  emit, flush, rollover, and final handler close operations run under the writer
  lock. Closing one sink releases only its lease; the final
  release detaches/closes the handler and deletes the registry entry in `finally`,
  even if flush/close raises, then returns that error to the worker close result.
  Sink-facade close and lease release are idempotent; a later acquire after a failed
  final close creates a fresh writer rather than reusing a poisoned entry. Never
  call `handlers.clear()` on a logger with a live lease. Do not create owner- or
  cycle-specific rotating handlers.
- Treat each sink's counters as per-owner deltas. Add one TaskMonitor merge helper
  that adds worker emitted/blocked totals to the reactor's cached aggregate while
  taking the latest health/error/timestamp and deferred status. Ensure later
  `_refresh_external_task_log_status()` cannot overwrite accumulated worker totals
  with the mostly-idle reactor sink's local counters. The merge is complete only
  when it also updates the `_deferred_task_log_pending`/`_last_error`/
  `_last_flush_at` backing fields and runs the same health-transition event and
  tid-mapping re-registration as the builtin `_apply_cached_diagnostics()` path:
  merging only the cached status object is reverted by the next refresh, which
  rebuilds status from the backing fields via `with_deferred(...)`. The
  control-cleanup worker path currently merges asymmetrically; fix it and add a
  regression test proving a control-cleanup worker's deferred status survives a
  later `_refresh_external_task_log_status()` and emits the health transition
  (open 2026-07-09 review finding R-2).
- Extend worker-local cleanup to close its worker-owned sink facade/lease as well as
  store/queues. It attempts every close and returns an immutable ordered close-error
  tuple instead of failing fast.
- Extract the current `_worker_lane_snapshot_only` cleanup branch into a private
  `_close_worker_local_resources()` helper. It must not call the public BaseTask
  lifecycle methods and must never set, join, or finalize the live reactor.
- In `_run_builtin_cycle_worker()`:
  1. create the isolated worker local;
  2. run the bounded local cycle;
  3. capture the frozen diagnostics/result;
  4. call `worker._close_worker_local_resources()` in `finally` and capture every
     close error;
  5. if close errors exist, replace any success with the typed retryable
     failed/pending result and attach the primary/secondary close diagnostics;
  6. return the captured result only after cleanup.
- Run `_run_terminal_control_cleanup_worker()` against the same kind of isolated
  worker-local monitor, not the live reactor. Split a local worker-body helper if
  needed so the service entry does not recurse. Hold the opened `MonitorStore` in a
  local and close the store plus worker-local sink/queues in `finally` for every
  terminal, reserved, dead-TID, and exception branch.
- Extend `_TaskControlCleanupWorkerResult` with the focused external/deferred status
  or counter deltas and close diagnostics required by the merge helper. Apply them
  only in `_handle_control_cleanup_worker_result()` on the owner thread. Invalid
  envelopes remain existing retryable pending results.
- Do not let a maintenance worker answer control messages.
- Add [IMPL.11], its mapping from section 7.4, reciprocal code/test citations,
  and firing references atomically only after sub-slices 5A-5D are green.

**Focused gates:**

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py \
  -k 'worker_local or external_log or control_cleanup or ping' -vv
./.venv/bin/python -m pytest \
  tests/tasks/test_task_monitor.py::test_task_monitor_terminal_control_cleanup_worker_error_is_retryable \
  -vv
./.venv/bin/python -m pytest tests/core/test_monitor_external_log.py -q
./.venv/bin/python -m pytest tests/core/test_monitor_store.py -q
```

**Stop if:** safe isolation requires copying the whole TaskMonitor into a new public
module or changing cleanup policy. Record that as a separate design problem rather
than expanding this slice.

### Phase 6: Reconcile docs and reciprocal traceability

**Outcome:** specs, plan, code, and tests describe one current contract.

**Files to change:**

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `weft/core/tasks/base.py`
- `weft/core/launcher.py`
- all concrete protected-turn files from Phase 3
- `weft/core/tasks/heartbeat.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/external_log.py`
- tests that fire [QUEUE.7], [CC-2.2.1], [IMPL.10], and [IMPL.11]
- `docs/lessons.md` only if implementation exposes a reusable correction not already
  covered by the proposed contract
- `CHANGELOG.md`, documenting the protected-hook migration for external BaseTask
  subclasses and the pre-terminal/pre-stopped first-turn edge
- this plan and `docs/plans/README.md`

**Required action:**

1. Replace the temporary [CC-2.2.1] `_Implementation status_` note with the final
   `_Implementation mapping_`, naming exact files and symbols.
2. Update [CC-2.2], [CC-2.3], [CC-2.5], [IMPL.8], [IMPL.9], and [OBS.13.10]
   implementation snapshots where ownership wording changed.
3. Add reciprocal full-path `Spec:` docstrings to the BaseTask template, launcher
   adapter, concrete protected hooks, and TaskMonitor resource helpers.
4. Add spec references to the firing tests for [QUEUE.7], [IMPL.10], and
   [IMPL.11].
5. Run link, plan metadata, spec hygiene, and traceability checks from the current
   worktree.
6. Reconcile every deviation-log row.
7. Mark the plan `completed` only after implementation, full verification, and
   independent completed-work review. If the user wants uncommitted review, leave it
   `draft` and report that status instead.
8. Search README/docs/extensions for documented BaseTask subclassing and launcher
   embedding. Regardless of hits, add a concise changelog compatibility note:
   external overrides of public
   `process_once()`/`wait_for_activity()`/`stop()`/`cleanup()` must migrate to the
   protected hooks; direct BaseTask `wait_for_activity()` now requires an
   established drive owner; protected Weft support queues and Heartbeat
   watched/reserved/control self-routes are rejected; and pre-terminal/pre-stopped
   tasks no longer receive an
   unconditional first turn. Record the search command/result in the execution log.
9. Reconcile and review Unit A mappings before Unit B begins. Unit B may update the
   same spec sections only for [IMPL.11]/TaskMonitor mapping; it must not rewrite a
   settled Unit A lifecycle contract without reopening Unit A review.

## 11. Testing Plan

### 11.1 Test philosophy

- Use real `TaskSpec`, `Consumer`/`BaseTask` subclasses, real temporary SQLite
  broker files, real `Queue` objects, real worker threads, and real temp external-log
  files.
- A small test subclass is allowed to expose a barrier inside the protected turn or
  make cleanup countable. It must retain real BaseTask queue wiring and stop logic.
- Monkeypatch only true seams:
  - a worker function to hold it with `threading.Event`;
  - a delegating real-store wrapper to observe `close()`;
  - a delegating real activity waiter that sets an entered-wait event and then calls
    the real wait implementation;
  - delegating real-resource close wrappers that raise after/before their real close
    as required to prove cleanup-error precedence;
  - a clock only where an existing due-time test already owns time.
- Do not mock `Queue`, `MultiQueueWatcher`, `BaseWatcher`, the worker-result queue,
  `MonitorStore` behavior, or `_task_process_entry` as a whole.
- Do not use fixed sleeps to prove ordering or absence. Use barriers, events, joins,
  later control replies, and bounded polling.
- Capture `threading.excepthook` in background-drive tests so an exception cannot be
  lost in test output.
- Test drive helpers must claim reactor ownership through public `process_once()`
  before any public `wait_for_activity()` call. A helper that waits first passes
  only when worker results arrive before the wait is reached — a load-dependent
  flake, not a green test. Two pre-contract helpers were fixed this way in the
  2026-07-09 review (service drain helper; worker-error wait loop).
- Never monkeypatch attributes of the global `os` module (`os.name`, `os._exit`).
  `module.os` aliases the one global module, so `setattr(plugin_module.os, "name",
  "nt")` changes the whole process: pathlib then mints `WindowsPath` objects on
  POSIX whose derived operations raise, killing any concurrent lazy import
  (`docker -> requests -> certifi`) mid-exec and leaving a half-initialized module
  in `sys.modules` for every later test in that worker. Platform simulation must
  patch the consuming module's `os` binding with a delegating shim
  (`monkeypatch.setattr(target_module, "os", shim)`), as the three fixed
  windows-simulation tests now do.
- Assert observable outcomes first: construction failure before DB creation, queue
  contents, task state, live thread state, handle closure, control replies, and
  resource independence. Private fields may support ownership assertions because
  the BaseTask lifecycle seam itself is the contract under test.

**Execution and test coverage map:** every branch below requires the named real
broker/thread/filesystem firing test before its slice is green.

```text
CONSTRUCTION
TaskSpec -> resolve local roles + fixed support routes -> validate -> open queues
             | collision/empty/protected route ----------> reject before DB [IMPLEMENTED 2026-07-09]
Heartbeat payload -> dynamic destination validator
             | ordinary task queue ----------------------> register/emit [IMPLEMENTED 2026-07-09]
             + self/support route ------------------------> reject, later input progresses [IMPLEMENTED 2026-07-09]

DRIVE
start() -> STARTING -> thread enters DRIVING -> turn -> real wait -> next turn
   | start raises ----------> NEW, owner cleared [IMPLEMENTED 2026-07-09]
   | concurrent stop -------> driver owns close; caller cannot close early [IMPLEMENTED 2026-07-09]
   | stop races first-turn strategy start -> stopping turn, StopWatching never
     escapes the public template [IMPLEMENTED 2026-07-09, review fix R-0]
   | foreign turn/wait -----> reject before queue/waiter effects [IMPLEMENTED 2026-07-09]
   | input/worker wake -----> entered-wait barrier, then prompt owner turn [IMPLEMENTED 2026-07-09]

STOP / FINALIZE
stop -> one absolute deadline -> join active driver -> subtype cleanup -> base cleanup
   | idle manual owner -----> caller finalizes despite owner Thread alive [IMPLEMENTED 2026-07-09]
   | deadline expires ------> no new wait budget, diagnostic retained [IMPLEMENTED 2026-07-09]
   | subtype cleanup raises -> base cleanup still runs -> CLOSED with error [IMPLEMENTED 2026-07-09]
   | turn already raised ---> turn error remains primary [IMPLEMENTED 2026-07-09]
   | Manager parent loss ---> graceful drain [IMPLEMENTED 2026-07-09]
   + Manager SIGUSR1 + loss -> KILL wins [IMPLEMENTED 2026-07-09]

TASKMONITOR WORKER
reactor -> deep TaskSpec/config snapshot -> worker-local store/queues/sink facade
        -> shared per-path writer -> capture result -> close all -> owner merge
   | body fails ------------> typed retryable failure after close [IMPLEMENTED 2026-07-09]
   | close fails -----------> typed retryable failure, no success merge [IMPLEMENTED 2026-07-09]
   | concurrent same path --> one rotating handler, complete JSONL rows [IMPLEMENTED 2026-07-09]
   | deferred backlog ------> one durable row per configured cycle [IMPLEMENTED 2026-07-09]
   | first construction ----> no flush until first driven turn [IMPLEMENTED 2026-07-09]
   + transient publish -----> same instance retries later [IMPLEMENTED 2026-07-09]
   | control-cleanup merge -> deferred backing fields + health transition parity
     with the builtin path [IMPLEMENTED 2026-07-09]
```

### 11.2 Required firing tests

| Contract | Required proof |
| --- | --- |
| Declared construction topology [QUEUE.7] | Five-base-role matrix, BaseTask fixed-support-route matrix, subtype-extra-role matrices, explicit allowed-alias tests, and Heartbeat dynamic-egress accept/reject tests. |
| Fixed BaseTask topology [QUEUE.7] | BaseTask add/remove rejection and unchanged direct-wait MultiQueueWatcher invalidation/rebuild test; background native rebind is explicitly deferred in section 6.5. |
| One owner [IMPL.10] | sequential cross-thread, simultaneous turn barrier, and foreign `wait_for_activity()` pre-effect rejection tests. |
| Public templates [CC-2.2.1] | all concrete policy suites plus four-method override audit/class-creation tests and mypy `@final` enforcement for process/wait/stop/cleanup. |
| One drive loop [CC-2.5] | launcher test proves one `run_until_stopped()` call and no wrapper-owned turns/waits. |
| Safe stop [IMPL.10] | `STARTING` barrier, external join, join-false, one absolute deadline across driver/workers/Manager multi-child teardown, idle-manual-owner stop, self-stop, repeated stop, cleanup failure, exception, and max-iteration tests. |
| Signal safety [MF-3] | existing signal deferral, generic parent-loss owner transition, Manager graceful parent-loss drain, and simultaneous SIGUSR1 priority tests. |
| Worker backpressure [IMPL.9] | existing bounded queue/drain tests remain green. |
| Control responsiveness [MF-3], [OBS.13.10] | Consumer, Manager, and TaskMonitor real control replies while worker is blocked. |
| TaskMonitor worker resources [IMPL.11] | disjoint watcher/lifecycle/TaskSpec/config/queue/store/sink-facade resources; one rotating handler per path; same-path rotation; built-in and runtime-cleanup close/body failure; typed diagnostics after close; two-cycle cumulative counters. |
| Reserve semantics unchanged [MF-2], [QUEUE.6] | existing success/error/STOP reserved-policy tests and crash/recovery tests remain green. |

### 11.3 Complete reference-test applicability ledger

Source inventory: all 25 tests in
`../simplebroker/examples/tests/test_reference_reactor.py` at reference commit
`37ee5e6f600828e8d23f76349258a84c1efd8d31`. No reference test may disappear from
implementation review without one of the classifications below.

| Reference test | Weft classification | Required Weft proof or reason |
| --- | --- | --- |
| `test_reactor_rejects_overlapping_queue_roles` | Direct plus subtype-adapted port, Phase 2 | Parameterized five-role matrix plus Manager/Pipeline/Monitor extra-role and allowed-alias proofs before DB/resource creation. |
| `test_reactor_rejects_empty_input_queues` | Not applicable | Some valid Weft task policies are control/event driven and do not watch an ordinary inbox in the same way as the concrete reference `Reactor`. Weft requires resolved roles and non-empty task-specific queue configs, not reference-style `input_queues`. |
| `test_base_reactor_centralizes_process_wait_stop_loop` | Direct port, Phase 4 | BaseTask test proves one turn, wait, and finalizer path; launcher delegates once. |
| `test_worker_result_event_wakes_background_reactor` | Direct port, Phase 4 | Add `tests/tasks/test_task_execution.py::test_base_task_worker_result_wakes_background_reactor` with a blocked worker, long caller timeout, and explicit event proving the real waiter was entered before worker release. |
| `test_input_activity_wakes_background_reactor` | Adapted port, Phase 4 | Preserve `tests/tasks/test_multiqueue_watcher.py::test_background_watcher_path_uses_multi_queue_waiter` and add `test_base_task_input_activity_wakes_background_reactor` with an entered-real-wait event after `run_forever()` changes. |
| `test_reactor_turns_have_single_thread_owner` | Direct port, Phase 3 | Sequential and simultaneous cross-thread owner tests. |
| `test_reactor_rejects_dynamic_queue_mutators` | Direct port, Phase 2 | BaseTask rejects add/remove; standalone MultiQueueWatcher retains dynamic API and polling fallback. Section 6.5 does not claim background native-waiter rebind. |
| `test_stop_during_startup_waits_for_drive_thread_before_closing` | Direct port, Phase 4 | Barrier between `STARTING` publication and driver entry; external stop cannot clean based on pre-start `is_alive() == false`. |
| `test_stop_waits_for_manual_drive_thread_before_closing_queues` | Direct port, Phase 4 | Manual drive thread remains visible through the strong owner reference. |
| `test_manual_drive_thread_self_closes_after_external_stop_join_false` | Direct port, Phase 4 | External join-false request plus drive-thread `finally` closes exactly once. |
| `test_control_lane_is_peek_checkpointed_not_consumed` | Not applicable as written | Weft intentionally peeks then exact-acks/deletes `ctrl_in`; control rows are runtime commands, not retained checkpoint history. Phase 2B ports the no-reprocessing outcome under delete/ack semantics. |
| `test_non_object_control_payload_returns_error_and_lane_progresses` | Adapted port, Phase 2B | Parameterized non-object JSON, response, exact ack, and later keyed-PING barrier. |
| `test_plain_text_control_command_remains_supported` | Already covered, retain | `tests/tasks/test_control_channel.py::test_ping_control_command_returns_pong`; Phase 2B runs it beside JSON-shape ports. |
| `test_unknown_object_control_command_returns_error` | Adapted port, Phase 2B | Empty-command and named unknown (`DANCE`) object responses must echo `request_id`, ack, and allow later progress. |
| `test_checkpointed_control_is_not_reprocessed_after_restart` | Adapted port, Phase 2B | Custom control queues plus task restart prove exact-acked input is not handled twice; later keyed PING is the barrier. |
| `test_pending_output_replay_waits_for_first_driven_turn` | Adapted port, Phase 2B | Strengthen the TaskMonitor restart test with pre-turn assertions that construction leaves the external file absent/unchanged and the durable row pending, then prove one public driven turn flushes it. Ordinary Weft task outputs use reservation, not replay. |
| `test_pending_output_rejects_configured_route_drift` | Not applicable | TaskMonitor external-log path is explicitly restart-scoped and may change on restart; ordinary task outbox routing comes from immutable TaskSpec for that task instance. This plan must not invent reference-style route pinning. |
| `test_pending_output_drain_budget_fetches_one_backlog_sentinel` | Adapted port, Phase 2B | Add a real TaskMonitor three-row, batch-size-one test against `_flush_deferred_lifetime_reports`; prove one durable flush per driven cycle and the remainder stays pending. A MonitorStore `limit=1` unit test is supporting evidence only. No sentinel is added where the implementation does not use one. |
| `test_existing_output_exact_id_replay_marks_written_without_duplicate` | Not applicable | Weft ordinary outbox delivery does not promise exact broker-ID replay, and TaskMonitor `report_id` upsert is a different database identity contract. Retain that store test under its own spec, but do not claim reference equivalence or add a second durable truth. |
| `test_pending_output_retries_in_process_after_transient_publish_failure` | Adapted port, Phase 2B | Add a fail-once publication test that keeps the same TaskMonitor instance alive and proves a later driven cycle retries and clears the durable row. Cross-instance reconstruction is not equivalent. |
| `test_output_backlog_blocks_new_input_but_not_control_lane` | Split applicability | **New-input blocking is not applicable:** TaskMonitor durably defers failed external output and intentionally continues bounded inbox work; blocking all new input would reduce monitor/control availability and is not a Weft contract. **Control responsiveness is adapted:** retain Consumer control tests and add TaskMonitor PING/STATUS while maintenance backlog is held in Phase 5. |
| `test_crash_after_result_record_replays_pending_output_on_restart` | Already covered in TaskMonitor form | The external-failure test stops the first TaskMonitor with a durable deferred row, creates a second instance on the same DB, and flushes it. Keep the cross-instance assertions explicit. |
| `test_processor_error_publishes_error_envelope_and_advances_checkpoint` | Adapted, already covered | `test_base_task_worker_lane_delivers_errors_on_main_thread`, `test_base_task_worker_error_is_raised_on_main_thread`, and `test_task_failure_leaves_message_in_reserved` prove owner-thread error handling and Weft's explicit recovery state. No input checkpoint is added. |
| `test_non_json_processor_result_publishes_error_and_advances_checkpoint` | Not applicable as written | Weft's result surface uses `serialize_result()` rather than a required JSON result envelope. Non-JSON Python values follow that serializer contract; failure recovery is reservation based. Do not turn arbitrary values into reference error envelopes. |
| `test_per_queue_single_inflight_preserves_source_order` | Adapted port, Phase 2B | Real two-message Consumer test holds the first worker, proves one reserved item, then verifies source/output order. |

Implementation closeout must update the table only if a cited existing test is
renamed or a port lands under a different exact name. A change from "applicable" to
"not applicable" requires independent reviewer approval and a contract reason.

### 11.4 Tempting but excluded tests

- Do not add an exact-ID output replay test to Weft. That would test a delivery model
  this plan explicitly rejects.
- Do not assert exact shutdown timing below the existing named constants. Assert
  bounded completion or deliberate non-closure behind a barrier.
- Do not use a mocked queue call list as evidence of "no side effects". Assert the
  temp database does not exist and no task worker thread started.
- Do not add a property-based live broker/thread lifecycle test. Use explicit
  interleavings; Hypothesis fixtures do not reset per generated example.

### 11.5 Production failure-mode matrix

| Path and realistic failure | Firing test | Planned handling | Observable outcome |
| --- | --- | --- | --- |
| A local role aliases `weft.log.tasks`, so lifecycle output becomes task input. | Fixed support-route collision matrix with no DB creation. | Reject during resolved construction validation. | `ValueError` names both roles and queue before side effects. |
| Heartbeat payload routes into its own inbox/control or the reserved `weft.` namespace. | Dynamic-egress reject/progress matrix. | Reject mutation before registration; existing reserved-on-error policy remains live. | Invalid-registration lifecycle/error evidence and later valid progress. |
| Stop arrives after `STARTING` publication but before the thread runs. | Deterministic start/stop barrier. | Active startup authority defers close to the driver or start rollback. | No early close; stop timeout warning only if the driver cannot finish. |
| A long-lived manual owner completed its turn, then another thread stops it. | Idle-manual-owner finalization test. | No active flags means caller may finalize despite owner Thread liveness. | Handles close once; later owner call raises `CLOSED`. |
| Foreign thread calls BaseTask wait and resets waiter state. | Foreign wait pre-effect test. | Owner verification before waiter/queue access. | Deterministic ownership exception, no hidden scheduling mutation. |
| Manager cleanup waits per active launch/child and multiplies timeout by child count. | Absolute-deadline active-launch plus three-child teardown test. | Every poll/join/tree escalation consumes one shared remaining deadline; after expiry only non-waiting direct-child kill remains. | Warning/private finalization diagnostic with surviving PIDs; no fresh or per-child timeout budget. |
| Subtype cleanup raises before BaseTask queue cleanup. | Raising-hook finalizer test. | Capture primary failure, always run base cleanup, mark `CLOSED`, no retry. | Logged cleanup error plus closed observable resources. |
| Manager receives parent loss while children are live. | Manager parent-loss and SIGUSR1-priority tests. | Owner-thread policy enters graceful drain; SIGUSR1 wins as KILL. | Existing drain/terminal lifecycle events, never silent generic cancellation. |
| Reactor and workers rotate the same external JSONL path. | Same-path real-filesystem rotation test. | One locked rotating handler per resolved path with refcounted leases. | Complete JSONL rows and bounded writer registry. |
| TaskMonitor body succeeds but worker-local close fails. | Per-resource close-failure matrix. | Attempt all closes; replace success with typed failed/pending result. | Owner sees retryable diagnostics and does not merge false success. |
| Deferred backlog is flushed without a cycle budget. | Three-row, batch-size-one driven-cycle test. | Reuse configured TaskMonitor batch budget. | One durable output per cycle; remainder stays queryable. |
| Constructor accidentally replays deferred output. | First-driven replay test. | Replay only from public owner turn. | No file/store change before first turn; deterministic post-turn flush. |

No row may ship with neither a firing test nor observable error/diagnostic handling.

## 12. Verification and Gates

### 12.1 Per-slice gates

Use the focused commands listed under each phase. Run red before production edits,
green after the smallest implementation, then the nearest whole test file.

### 12.2 Final runtime gates

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py \
  tests/tasks/test_service_task.py tests/tasks/test_heartbeat.py \
  tests/tasks/test_pipeline_runtime.py tests/tasks/test_signal_deferral.py \
  tests/tasks/test_terminal_event_retry.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py \
  tests/core/test_monitor_external_log.py tests/core/test_monitor_store.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
./.venv/bin/ruff format --check weft tests
```

If the full suite is too slow for an intermediate slice, defer only the full suite,
not the focused test or static checks. The full suite remains a final gate.

### 12.3 Documentation and traceability gates

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py -q
rg -n '^    def process_once' weft/core
rg -n 'CC-2\.2\.1|QUEUE\.7|IMPL\.10|IMPL\.11' \
  docs/specifications weft tests docs/plans/2026-07-09-reference-reactor-safety-hardening-plan.md
git diff --check
```

Backstitch authoring probe:

```bash
uv --project ../backstitch run backstitch check \
  --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --format json
```

If `../backstitch` is absent in an implementer's checkout, record the missing
sibling as a traceability-tooling blocker, still run the repository's plan/spec
hygiene tests and reciprocal `rg` audit, and do not claim the Backstitch comparison
passed. Do not install or vendor Backstitch in this reactor change.

At plan authoring time, the full Weft corpus had pre-existing Backstitch debt and no
repo-local Backstitch configuration. The explicit probe reported 24 errors and 866
warnings. That baseline is not caused by this plan and must not be hidden. The
section 8 accepted-deviation row formally replaces the original zero-new/clear-all
requirements for this execution. The implementation must:

1. save the before and after JSON reports outside the repository;
2. introduce zero new diagnostics outside [CC-2.2.1], [QUEUE.7], [IMPL.10], and
   [IMPL.11];
3. classify and record every new diagnostic within those four codes, including why
   it cannot be corrected without out-of-scope tooling or spec-structure work;
4. run the repo-declared zero-error/zero-warning command if a Weft Backstitch config
   lands before implementation completes;
5. stop and report the infrastructure blocker rather than claim the global
   zero-warning gate passed if no such configured gate exists.

Do not add Backstitch configuration, suppressions, or a dependency in this reactor
change. That would be unrelated project-configuration scope and could mask existing
debt.

**2026-07-09 executed comparison.** Before (baseline commit, clean worktree):
24 errors / 866 warnings / 590 infos. After (full implementation):
24 errors / 932 warnings / 579 infos; 17 pre-existing diagnostics cleared. Every
net-new diagnostic (69 `SPEC_MAPPING_RECIPROCAL_MISSING` warnings and 3
`SPEC_SECTION_UNMAPPED` infos) is the structural Backstitch v1 limitation that
`_Implementation mapping_` blocks cannot attach to `**ID**:` invariant bullets —
the identical debt shape carried by every pre-existing invariant (413 baseline
warnings of the same class across QUEUE.1-6, IMPL.1-9, OBS.*, MANAGER.*). Zero
net-new diagnostics exist outside the four new reference codes. Both JSON
reports were saved outside the repository. Clearing the invariant-bullet class
requires a Backstitch parser change or an invariants-file restructure, both out
of scope here; this is the recorded infrastructure blocker, not a passed
zero-warning gate.

### 12.4 Diff and dirty-tree gate

```bash
git status --short
git diff --stat
git diff -- docs/specifications docs/plans weft/core tests/tasks tests/core
```

Confirm user-owned changes in `docs/agent-context/engineering-principles.md`,
`docs/lessons.md`, `pyproject.toml`, and `uv.lock` were preserved unless the user
separately authorized overlap.

## 13. Independent Review Loop

This plan requires independent plan review before Phase 1 because it changes core
runtime scheduling, shutdown, task topology, and normative specs. It also requires a
completed-work review after Phase 5 and again after traceability reconciliation if
the final diff changes materially.

Reviewer inputs:

- this complete plan, including section 7;
- all governing specs in section 3 at the recorded baseline;
- `weft/core/tasks/base.py`;
- `weft/core/tasks/multiqueue_watcher.py`;
- `weft/core/launcher.py`;
- all five concrete current `process_once()` implementations;
- `weft/core/tasks/service.py`;
- TaskMonitor worker-local clone and worker resource paths;
- the SimpleBroker reference reactor and tests.

Plan review prompt:

> Read the complete plan and its Proposed Spec Delta, including promotion strategy
> A. Inspect the cited current code and reference reactor. Look for errors, unsafe
> tradeoffs, hidden contract changes, incomplete role/stop interleavings, overbroad
> abstraction, weak tests, and missing reciprocal traceability. Do not implement.
> Could a zero-context engineer implement every phase confidently and correctly?
> List findings by severity and name the exact section to change.

The author must reproduce each finding, respond explicitly, and revise the plan or
record why the current path is safer. Review claims are not facts until checked
against current code.

### Plan-review record

| Reviewer | Finding | Disposition |
| --- | --- | --- |
| Independent source review | Inherited BaseWatcher stop can hold its lock across join and skip current-owner strategy/finalizer cleanup. | Fixed with a task lifecycle lock, explicit `STARTING` state, active-work flags, no superclass join/finalize delegation, and exact waiter/strategy/thread-local/finalizer tests. |
| Independent source review | Consumer/ServiceTask/Manager/TaskMonitor public cleanup overrides bypass a BaseTask-only guard and Manager ordering is load-bearing. | Fixed with one enforced protected cleanup chain and subtype ordering tests. |
| Independent source review | Runtime cleanup, not only the built-in TaskMonitor worker, can use the live sink and mutate cached state. | Fixed with two isolated worker paths, a typed runtime-cleanup diagnostic result, and jsonl-then-delete close/order tests. |
| Independent source review | Five canonical queue roles omit Manager internal, Pipeline event/source/target, and forwarding Monitor downstream roles. | Partially fixed with subtype role hooks; the later implementation audit extended the correction to BaseTask support routes and Heartbeat dynamic egress. |
| Independent source review | Ownership wording overclaimed arbitrary methods; `@final` alone has no runtime force; startup/self-stop/signal/loop exit states were incomplete. | Fixed by narrowing to drive entry points, adding the class guard plus owner-guarded wait, strong Thread identity, explicit `STARTING`/turn/wait/loop authority, a task-owned SIGINT handler, separate parent-loss state, and a loop truth table. |
| Independent source review | TaskMonitor failures classified fatal despite existing retryable policy; worker shallow copies retain more than the sink. | Fixed with retryable typed results and a default-fail identity audit now explicitly covering mutable TaskSpec/config, lifecycle, queue/store, and sink-facade state. |
| Independent source review | Strategy-A mapping timing and phase size were ambiguous. | Fixed with exact final mapping text, separate strategy-B [IMPL.10]/[IMPL.11] slices, and 4A-4G/5A-5D red-green sub-slices. |
| Claude adversarial review | [IMPL.10] claimed Phase 5 worker isolation in Phase 4. | Fixed by splitting TaskMonitor isolation into [IMPL.11], promoted atomically in Phase 5. |
| Claude adversarial review | `Monitor` could be mistaken for operational `TaskMonitor`; Phase 3 referenced Phase 4 lifecycle state. | Fixed by naming `weft/core/tasks/monitor.py::Monitor` and moving finalized-state behavior entirely to Phase 4. |
| Claude adversarial review | Existing-ledger equivalence and worker/input wake ports lacked executable gates. | Partially fixed by adding named tests; the later ledger audit reclassified weak equivalence rows and added entered-real-wait barriers to both wake tests. |
| Claude adversarial review | Per-cycle logger names would leak through Python's global registry; stop timeout and embedder impact were unspecified. | Fixed with one refcounted path-derived writer/logger, one rotating handler per path, registry-release proof, preserved `None` return contract, and mandatory changelog/embedder audit. |
| Claude adversarial review | Test-only subclasses and future shallow-copy fields could bypass guards. | Fixed with a repository-wide subclass override audit and a default-fail mutable-field sharing allowlist test. |
| Claude post-correction confidence pass | Historical pass reported no blockers after asking for loop-row precedence and two omitted exact existing-test nodes. | Superseded by the later implementation/reference audits below; it is not readiness evidence. The minor loop/status corrections remain retained. |
| 2026-07-09 implementation audit | Stop used `Thread.is_alive()` in both unsafe directions: false before start, true after an idle standalone turn. | Fixed in the plan with `STARTING`, start/stop interlock, active-work authority, and an idle-manual-owner regression. |
| 2026-07-09 implementation audit | Fixed-role wording exceeded the inventory and contradicted Heartbeat runtime destinations; BaseTask/global/subtype support queues could alias local roles. | Fixed by narrowing [QUEUE.7] to construction topology/support routes, inventorying BaseTask plus Manager/Pipeline fixed routes, and giving Heartbeat a reserved-`weft.` dynamic-egress validator/test matrix. |
| 2026-07-09 implementation audit | Manager's separate signal state/graceful drain had no parent-loss migration path. | Fixed with a shared owner-thread pending-source snapshot, protected termination-policy hook, Manager graceful-drain override, and SIGUSR1-priority tests. |
| 2026-07-09 implementation audit | Stop timeout did not bound worker/Manager drains and cleanup failure could strand `FINALIZING` or skip base cleanup. | Fixed with one absolute deadline, deadline-aware hooks, private always-run base cleanup, retained cleanup diagnostics, and deadline/raising-hook tests. |
| 2026-07-09 implementation audit | Public BaseTask `wait_for_activity()` mutated waiter state outside owner enforcement. | Fixed with a first-line owner verification and foreign-wait pre-effect tests; standalone MultiQueueWatcher API/polling behavior remains, with native dynamic rebind separately deferred in section 6.5. |
| 2026-07-09 implementation audit | TaskMonitor snapshot omitted mutable `taskspec`/`_config`; separate same-path rotating handlers would race or block rollover. | Fixed with explicit deep snapshots, worker-local sink facades/counters, one refcounted writer/handler per path, rotation tests, and close-error result semantics. |
| 2026-07-09 reference-ledger audit | First-turn replay, per-cycle flush budget, same-process retry, exact-ID replay, and backlog fairness were misclassified or weakly mapped. | Fixed by adding exact adapted ports, marking exact broker-ID replay not applicable, and splitting backlog input-blocking from applicable control responsiveness. |
| 2026-07-09 scope audit | Core lifecycle and TaskMonitor resource isolation in one review diff created avoidable blast radius. | Fixed by making this an umbrella plan with mandatory Unit A/Unit B landings and an acceptance-only parallel lane. |
| 2026-07-09 implementation-start audit | The absolute cleanup deadline omitted interactive/session waits, ServiceTask sentinel retries, Manager exited-child joins, and a late child-launch result could become untracked after cleanup timed out. | Fixed in execution scope by adding deadline-aware cleanup-only paths in `interactive.py`/`sessions.py`/ServiceTask/Manager and a post-launch shutdown recheck that self-reaps a late-created child. |
| Revised-plan independent review | Fixed-route inventory still omitted Manager services and Pipeline status/state/spawn routes; Heartbeat's partial support list left other `weft.*` globals reachable. | Fixed with a mandatory repo-wide fixed-route inventory, exact Manager/Pipeline route hooks/matrices, and one reserved `weft.` namespace rule for Heartbeat runtime egress. |
| Revised-plan independent review | Manager child teardown retained fresh initial and per-child wait budgets outside the absolute cleanup deadline. | Fixed by threading the deadline through active-launch drain, child-exit polling, every join/tree escalation, and all `_terminate_children()` call sites, plus a three-child non-multiplication test. |
| Revised-plan independent review | Manager's existing public `wait_for_activity()` override could bypass a BaseTask-only guard. | Fixed by making wait a fourth final public template, adding a protected owned-wait hook, deleting/migrating Manager and test overrides, and extending override audits/class-creation tests. |
| Revised-plan closure verification | Re-read the corrected topology, deadline, and wait-template sections against current code. | **CLEAR:** all three remaining blockers are closed; no new blocker found. |
| User follow-up from analogous Taut finding | A native PostgreSQL waiter captures a fixed queue set; standalone background `MultiQueueWatcher` mutation detaches the stale waiter but does not owner-rebuild/rebind the running strategy. | Confirmed and documented in section 6.5 with the owner-thread strategy-rebind design, firing tests, promotion triggers, and explicit deferral from Unit A/Unit B. Polling fallback prevents permanent invisibility. |
| 2026-07-09 in-flight implementation review | Four-agent independent review of the uncommitted Unit A + Unit B worktree, plus live verification (full suite, stress runs, import-corruption tracing). Found one production blocker, two majors, one traceability violation, missing plan-mandated tests, two unrecorded deviations, and four sources of test indeterminacy. | Tracked item-by-item in section 13.1. Blocker R-0 and the three test-indeterminacy sources were fixed in-tree during the review; the remainder gate Unit A/Unit B close. Phase status: Phases 0-2B complete, Phase 3 substantially complete, Phases 4/5 code ~90-95% with spec promotion pending, Phase 6 not started. |
| 2026-07-10 completed-work review | Fresh plan-to-tree review reopened lifecycle authority, complete resource close, session deadline/tree escalation, MRO enforcement, TaskMonitor failure semantics/snapshot classification, Manager descendant diagnostics, and traceability closeout. | Tracked as R-14 through R-21. R-14 and R-16 through R-20 are fixed in tree; R-15 remains open and externally blocked on the retained SimpleBroker SQLite lifecycle reproducer; R-21 is a formal accepted tooling deviation and remains explicitly not a passed Backstitch gate. Current verification and independent closeout must be rerun before engineering clearance. |

### 13.1 In-flight review resolution tracker (2026-07-09)

Every row must reach a terminal status (`resolved` or `accepted`) before its owning
unit's completed-work review. Open rows are blocking gates, not suggestions.

| ID | Finding | Owner | Required resolution | Status |
| --- | --- | --- | --- | --- |
| R-0 | First-turn stop race let SimpleBroker `StopWatching` escape public `process_once()`; its firing test failed 10/10 solo. | Unit A / Phase 4 | Catch public `StopWatching` at the strategy-start seam and run the turn as a stopping turn (Phase 4 bullet updated). | **resolved in tree 2026-07-09** — `_ensure_task_strategy_started()` returns False on `StopWatching`; template routes to `_process_stopping_reactor_turn()`; 10/10 stress green. |
| R-1 | Manager teardown passes `kill_after=False` on every `terminate_process_tree()` call, dropping within-budget SIGKILL escalation for TERM-resistant descendants (`weft/core/manager.py:3433/3450/1101/3505`). | Unit A / Phase 4 | Split-budget KILL escalation or non-waiting SIGKILL sweep over the descendant snapshot, plus a SIGTERM-trapping-descendant firing test (Phase 4 bullet updated). | **resolved 2026-07-09** — split-budget `timeout=remaining/2, kill_after=True` on the direct-child tree, managed-pid, and late-launch-reap rungs; `_signal_children_to_stop`'s SIGTERM-only rung is intentionally unchanged (hard kill belongs to `_terminate_children`). Fired by `test_manager_terminate_children_kills_sigterm_trapping_managed_pid` and `test_manager_terminate_children_kills_sigterm_trapping_descendant_tree` (real TERM-trapping processes). Survivor diagnostics fixed alongside (R-13): cleanly exited children are no longer recorded and `kill_issued` reflects the actual kill. |
| R-2 | Control-cleanup worker merge updates the cached external status but not the `_deferred_task_log_*` backing fields and skips the health-transition/tid-mapping notification; the next refresh reverts worker deferred status (`task_monitor.py:5549` vs `:1204`). | Unit B / Phase 5 | Backing-field update + notification parity with `_apply_cached_diagnostics()`, plus a survives-refresh regression test (Phase 5 bullet updated). | **resolved 2026-07-09** — shared `_apply_worker_external_task_log_status()` owner helper used by both the builtin and control-cleanup result paths; fired by `test_task_monitor_control_cleanup_deferred_status_survives_refresh`. |
| R-3 | Manager parent-loss-alone graceful drain (red test 27 first half) has no firing test; `"Parent process exited"` appears in zero tests. | Unit A / Phase 4 | Add the test: `note_parent_loss()` with live children enters `_begin_shutdown_drain()` (`_draining is True`), never generic cancellation. | **resolved 2026-07-09** — `test_manager_parent_loss_enters_graceful_drain_with_live_children` proves drain entry, the "Parent process exited" reason, STOP delivery to a live child's ctrl queue, and no child termination or terminal manager state on that turn. |
| R-4 | `threading.excepthook` capture is absent from every background-drive test (section 11.1 hard requirement; Phase 4 red test 3). | Unit A / Phase 4 | Add capture to the background-drive/wake/interlock tests so a driver-thread exception fails the test. | **resolved 2026-07-09** — `thread_exception_guard` fixture (captures `threading.excepthook`, asserts empty on teardown) applied to the seven background-drive/wake/start-failure/interlock tests in `tests/tasks/test_task_execution.py`. |
| R-5 | [IMPL.10]/[IMPL.11] are implemented but not promoted in `07-System_Invariants.md`, while code/tests already cite [IMPL.11] and [CC-2.2.1] (`task_monitor.py:974`, `test_task_monitor.py:186/284`, `test_monitor_external_log.py:116/278`) — violating section 7's strategy-B rule and the section 6.4 gate. | Unit A ([IMPL.10]) / Unit B ([IMPL.11]) | Promote both bullets with their section 7.4 mappings atomically with each slice's close (the code appears ready), or strip the five premature citations until promotion. | **resolved 2026-07-09** — [IMPL.10] and [IMPL.11] promoted in `07-System_Invariants.md` with implementation mappings; [CC-2.2.1]'s temporary status note replaced by the final mapping; CC-2.2/CC-2.3/CC-2.5 ownership wording refreshed; reciprocal `Spec:` docstrings added to the BaseTask templates, launcher adapter, concrete turn hooks, and external-log writer lease; firing tests carry module-level spec refs. |
| R-6 | Two pre-contract test helpers called public `wait_for_activity()` without claiming ownership; load-dependent flakes (`test_service_task.py` drain helper; `test_task_execution.py` worker-error wait loop). | Unit A / tests | Claim ownership via `process_once()` first (section 11.1 rule added). | **resolved in tree 2026-07-09** — 15/10-run stress green. |
| R-7 | Global `os.name = "nt"` monkeypatching in three main-suite tests poisoned concurrent lazy imports (pathlib WindowsPath on POSIX -> half-initialized `requests` in `sys.modules`) and produced spurious "docker unavailable" skips. | tests | Module-local delegating `os` shim; never patch the global module (section 11.1 rule added). | **resolved in tree 2026-07-09** — parity/launcher/release tests fixed; two consecutive full-suite runs with `-rs` show zero docker skips. `extensions/weft_docker/tests/test_docker_plugin.py:658` (extension suite, not in main `testpaths`) still needs the same one-line fix. |
| R-8 | Explicit stop-twice idempotence (Phase 4 red test 6) is only implicitly exercised; no assertion that a second stop does not re-close queues or duplicate endpoint/control cleanup. | Unit A / Phase 4 | Add the explicit assertion test. | **resolved 2026-07-09** — `test_base_task_repeated_stop_does_not_duplicate_cleanup` counts subtype and base cleanup phases across repeated `stop()`/`cleanup()`. |
| R-9 | Phase 3 red test 7 per-policy stopping matrix covers Consumer only; Manager/Heartbeat/Pipeline/TaskMonitor rely on the shared BaseTask stopping hook untested per policy. | Unit A / Phase 3 | Add the per-policy stop-with-pending-work cases or record a reasoned acceptance here. | **resolved 2026-07-09 (structural acceptance)** — no production family overrides `_process_stopping_reactor_turn`; `test_stopping_turn_policy_is_shared_by_all_concrete_task_families` locks the shared policy in and forces per-family behavioral coverage onto any future override; Consumer retains the behavioral stop-with-pending-work coverage. |
| R-10 | Unrecorded deviations: worker TaskSpec shell-copy vs `model_copy(deep=True)`; finalizer subtype-hook-before-waiter/strategy ordering vs stop-sequence steps 7-8. | plan | Record in section 8 with rationale. | **resolved 2026-07-09** — section 8 rows added; both accepted. |
| R-11 | Stale plan self-description: section 11.1 map said `[PLANNED]` for implemented branches; section 16 said implementation had not begun. | plan | Refresh markers and status text. | **resolved 2026-07-09** — this update. |
| R-12 | `ruff format --check` fails on 13 files, all touched by this implementation. | Unit A+B / gates | One `ruff format` pass over the touched files before the handoff gate. | **resolved 2026-07-09** — `ruff format` run; `ruff format --check`, `ruff check`, and mypy all pass. |
| R-13 | Minor robustness notes, fix-with-owning-slice or accept: survivor diagnostics record cleanly-exited children as "survival uncertain" and sample `kill_issued` after `kill()` (`manager.py:3452-3471`); `_active_cleanup_deadline or time.monotonic()` falsy-`or` on a float (`manager.py:1092`); first stubborn child can consume the whole remaining TERM budget (plan-compliant, fairness note); `max_iterations` outranks the pending-termination truth-table row; closed-sink lease resurrection could strand a writer-registry entry if a post-close probe ever lands (`external_log.py:383-389`); no single factored sink-construction helper (two sites, not three); three-child deadline test uses stub processes (non-multiplication proven; live-tree escalation coverage arrives with R-1); `_stop_worker_lanes` busy-spins if a worker lane ever calls stop (contract-forbidden). | owning slices | Fix opportunistically inside the owning slice or leave with this record; none is a correctness blocker on its own. | **accepted with record** |
| R-14 | The three-flag cleanup-authority model omitted a standalone owned wait, so a foreign stop could finalize while the owner still touched waiter/strategy state. | Unit A / lifecycle | Add lock-protected `wait_active`, defer external finalization while set, finalize pending stop from wait `finally`, and update [CC-2.2.1]/[IMPL.10]/plan authority text. | **resolved 2026-07-10** — `test_base_task_foreign_stop_defers_cleanup_during_owned_standalone_wait` fires the race; section 8 records the correction. |
| R-15 | Base cleanup appeared to omit both the primary watcher queue and additional watcher-owned queue facades held only in `_queues`. | Unit A / cleanup + SimpleBroker | Prove whether the primary is already identity-cached; close every remaining watcher facade only when SimpleBroker proves cross-process SQLite lease release cannot invalidate another process's WAL generation. | **open: externally blocked 2026-07-10** — `test_base_task_cleanup_covers_primary_watcher_queue_handle` proves the primary premise false and its finalizer closed through `_queue_cache`. Section 13.2 retains the exact failing `_queues`-close patch and control. The remaining queue-layer fix belongs in SimpleBroker and gates engineering clearance. |
| R-16 | Consumer session cleanup could start fresh waits after the absolute deadline or weaken hard process-tree escalation. | Unit A / cleanup deadline | Thread one remaining deadline through Consumer, AgentSession, and CommandSession cleanup without dropping kill escalation. | **resolved 2026-07-10** — fired by `test_consumer_cleanup_propagates_one_deadline_to_owned_sessions`, the AgentSession deadline/IPC-feeder/tree tests, and the CommandSession expired-deadline/tree tests in `tests/tasks/test_task_execution.py`. |
| R-17 | The public-template class guard inspected only the subclass namespace, so a left-hand mixin in the MRO could replace a final reactor template. | Unit A / public templates | Audit the effective MRO at class creation and reject inherited public-template replacement. | **resolved 2026-07-10** — fired by `test_base_task_rejects_public_template_override_in_left_hand_mixin`. |
| R-18 | TaskMonitor worker snapshots and worker failure paths did not exhaustively classify instance fields or uniformly turn setup/body/store/sink/queue close failures into typed failed or retryable outcomes. | Unit B / worker isolation | Enforce an exhaustive field allowlist/copy policy; attempt all closes; suppress success/diagnostic commit after close failure; keep runtime cleanup pending; prove both worker entries across store/sink/queue failures. | **resolved 2026-07-10** — fired by the unclassified-state test, non-default scalar reset matrix (including `_wait_active`), report-only setup failure test, all-resource close test, builtin success-replacement test, runtime retry test, and the two-entry close-failure matrix in `tests/tasks/test_task_monitor.py`. |
| R-19 | Manager survivor diagnostics vanished when the direct wrapper exited even if a managed descendant PID was still alive at or after deadline expiry. | Unit A / Manager cleanup | Probe the captured managed PID set after teardown and retain both all managed PIDs and the still-live subset whenever either wrapper or managed process survives. | **resolved 2026-07-10** — `test_manager_cleanup_retains_live_managed_pid_after_wrapper_exit` fires the exited-wrapper/expired-deadline case. |
| R-20 | Final traceability omitted the accepted frozen `spec`/`io` identity-sharing detail, new hook docstrings used bare codes instead of full spec paths, and the Unreleased dependency note still named the old floors. | Phase 6 / docs | Reconcile [IMPL.11], use full-path citations on the new ownership/resource seams, and name SimpleBroker 5.2.0 plus simplebroker-pg 3.1.0 without changing dependency files. | **resolved 2026-07-10** — spec mapping, code docstrings, and CHANGELOG corrected in tree. |
| R-21 | The normative Backstitch gate required zero new diagnostics and clearing all four new codes while the execution record accepted a known parser-shaped delta. | Phase 6 / traceability | Replace the contradictory requirement with one formal deviation rule, preserve before/after evidence, require zero new diagnostics outside the four codes, and continue to report the global/tooling gate as not passed. | **accepted with record 2026-07-10** — section 8 and section 12.3 now agree; 69 mapping warnings and 3 unmapped-section infos remain an explicit infrastructure blocker, not a successful Backstitch gate. |

### 13.2 R-15 retained SQLite reproducer (2026-07-10)

The healthy control is the current tree:

```bash
./.venv/bin/python -m pytest \
  tests/tasks/test_task_execution.py::test_base_task_cleanup_covers_primary_watcher_queue_handle \
  tests/core/test_manager.py::test_manager_autostart_pipeline_ensure_restarts \
  -n 0 -q --tb=short --show-capture=no
# 2 passed
```

Apply this minimal patch to `BaseTask._cleanup_base_task_resources()` immediately
after `queue_handles` is initialized from `_queue_cache`:

```diff
 queue_handles: list[Queue] = list(self._queue_cache.values())
+queue_handles.extend(config.queue for config in self._queues.values())
```

Run the same command. The primary-handle test passes, the first autostart pipeline
child completes, then every pipeline recompile fails in
`simplebroker._timestamp.TimestampGenerator._initialize()` with
`sqlite3.OperationalError: disk I/O error`; harness teardown fails opening the same
database. Replacing `close()` for the additional facades with
`Queue.cleanup_connections()` reproduced the same failure. An attempted parent-core
recycle changed the observed failure to `sqlite3.DatabaseError: file is not a
database`, so that is not an acceptable Weft workaround. Removing the patch restores
the two-test control.

The queue-layer mechanism has independent sibling evidence in
`../simplebroker/docs/plans/2026-07-09-sqlite-wal-generation-guard-plan.md` and its
generation-replacement tests in
`../simplebroker/tests/test_wal_generation_guard.py`. R-15 remains open until an
upstream regression combines that generation guard with explicit final release of
all child watcher leases while a parent process retains a live SQLite session.

## 14. What Already Exists and Is Reused

- `BaseTask`'s bounded worker-result queue, per-turn drain budget, local wake event,
  and `TASK_REACTOR_WAKEUP_MAX_SECONDS` remain the worker backpressure mechanism.
  This plan adds ownership/finalization around them rather than a second event plane.
- `MultiQueueWatcher` remains the queue readiness and native-wait owner. BaseTask
  adds an owner check at its inherited wait seam. Standalone watchers already track
  membership generation/signature, invalidate stale waiters, and rebuild on a later
  direct wait. Background strategy rebind is not implemented and is explicitly
  deferred in section 6.5; bounded polling remains the correctness fallback.
- The existing queue-name resolution and TaskSpec construction boundary remains the
  canonical source for local roles. The new validator consumes its resolved output
  and adds fixed support/subtype roles; it does not create a QueueFactory.
- Manager's current `_begin_shutdown_drain()`, SIGUSR1 kill behavior, child cleanup,
  and broker-free launch workers remain the policy. The new protected termination
  hook only makes that policy reachable from the shared pending-source snapshot.
- TaskMonitor's existing MonitorStore deferred-write rows, batch configuration,
  report IDs, exact-delete policy, and owner-thread result application remain the
  durable mechanism. The plan adds missing first-turn/budget/retry proof rather than
  a second ledger.
- `ExternalTaskLogSink` keeps its JSONL format, health/counter facade, and rotating
  handler behavior. Ownership changes from handler-per-sink to one private
  refcounted writer per path so rotation is serialized without changing the file
  contract.
- SimpleBroker's background start, polling strategy, waiter, thread-local cleanup,
  and weakref finalizer are reused through their protected seams. No SimpleBroker
  runtime file is edited.

## 15. Out of Scope

- a general reactor framework shared across repositories;
- changes to SimpleBroker's runtime code;
- owner-thread native-waiter rebuild/rebind for standalone background
  `MultiQueueWatcher` runtime membership changes; section 6.5 defines the separate
  fix, tests, and promotion triggers while this plan retains bounded polling
  fallback;
- a new QueueFactory or event bus;
- a general registry or policy for every payload-directed queue name; only the
  existing Heartbeat dynamic-egress contract is handled here;
- queue aliases or automatic migration for invalid overlapping role names;
- sidecar input checkpoints or an ordinary-task durable output outbox;
- exactly-once execution or delivery claims;
- changes to reserved recovery policy;
- worker timeouts, leases, dead-letter queues, or result compaction;
- preempting an operating-system/backend `close()` call after it has begun;
- TaskMonitor retention, deletion selection, schema, or policy redesign;
- multi-process coordination for several Weft processes writing one external JSONL
  path; this slice guarantees one rotating handler per path within one process and
  retains the existing deployment assumption for cross-process paths;
- splitting large cohesive Weft files on line-count grounds;
- new dependencies or project configuration;
- cleanup of pre-existing global Backstitch debt.

## 16. Fresh-Eyes Self-Review

Status: revised after the initial draft reviews, the 2026-07-09 implementation
and reference-ledger audits, the 2026-07-09 four-agent in-flight review, and the
2026-07-10 completed-work review (section 13.1). The runtime implementation is
present in the uncommitted worktree. R-14 and R-16 through R-20 correct issues
found after the earlier R-0 through R-13 pass; R-15 remains open and externally
blocked in SimpleBroker. Those earlier full-suite/static results
are historical evidence, not a current clearance claim. A fresh full gate run
and independent completed-work closeout remain required. The Backstitch
comparison is recorded under the formal section 8 deviation, but its global
zero-warning gate did not pass. The plan therefore remains `draft`; it may be
marked `completed` only after current verification, independent review per unit,
and the required Unit A/Unit B landings are complete.

Findings and corrections from the first fresh-eyes pass:

1. **The first scope was too close to copying the reference outbox.** That would
   create durable state alongside Weft's reserved queue and violate [MF-2]. The plan
   now explicitly preserves reservation delivery and adds clarifying spec text.
2. **Putting `_claim_reactor_thread()` only in BaseTask's old turn body was
   insufficient.** Heartbeat and TaskMonitor do not call that body consistently.
   The plan now requires a non-overridden public template and an atomic migration of
   all five concrete policies.
3. **Launcher consolidation initially lost parent-loss semantics.** The plan now
   separates signal-safe real-signal recording from ordinary-thread parent-loss
   state, normalizes both on the owner thread, preserves Manager's graceful drain
   through a policy hook, preserves SIGUSR1 priority, and uses the existing bounded
   parent-watch cap rather than claiming the active waiter can be notified
   immediately.
4. **"Close once" was ambiguous on startup, idle manual ownership, timeout, and
   cleanup failure.** The plan now uses explicit lock-protected
   `STARTING`/turn/wait/loop authority, one absolute wait deadline through
   driver/workers/Manager launch and multi-child teardown, an always-run private
   base cleanup phase, and a retained cleanup-error snapshot. A live active driver
   or standalone wait keeps ownership; a merely live idle manual owner does not.
5. **TaskMonitor cleanup was initially described only as a shallow-copy concern.**
   Inspection showed both worker paths can share live resources, same-path sink
   objects share a global logger, cumulative counters can reset, and the runtime
   cleanup result cannot return external/deferred diagnostics. Phase 5 now covers
   both workers, explicit TaskSpec/config snapshots, a shareable-field allowlist,
   one refcounted same-path writer/handler, typed result extension, counter merge,
   and store/sink/queue close ordering including close failures.
6. **The traceability gate was initially stated as a clean global Backstitch run.**
   The current repo has no local Backstitch config and the explicit full-corpus probe
   has large pre-existing debt. The plan now records that evidence, requires no new
   diagnostics outside the four new codes, classifies the accepted 69-warning and
   3-info parser-shaped delta within those codes, and forbids hiding the global
   blocker or claiming the gate passed.
7. **The role inventory and wording were both incomplete.** It now covers BaseTask
   fixed support routes, Manager internal/services-registry queues, Pipeline
   controller/edge/status/state/internal-spawn routes, and the ordinary forwarding
   Monitor's downstream route, with narrow tested aliases before queue construction
   can collapse keys. The invariant is narrowed to construction topology; Heartbeat
   payload destinations have a separate reserved-`weft.` runtime-egress validator.
8. **The plan risked breaking or overclaiming standalone dynamic watchers.** It now
   seals topology only at BaseTask, preserves the MultiQueueWatcher API and polling
   fallback, and records background native-waiter rebuild/rebind as a separate
   deferred defect rather than treating the direct-wait test as full proof.
9. **Drive ownership omitted the public wait seam.** BaseTask now verifies the owner
   before waiter creation/reset or pending-state hints, makes wait a final public
   template with a protected hook, removes Manager/test override bypasses, and
   leaves standalone watcher waits unchanged.
10. **The reference ledger confused adjacent coverage with equivalence.** First-turn
    replay, per-cycle durable flush budgeting, and same-instance retry are now exact
    adapted ports; exact broker-ID replay is not applicable; backlog input blocking
    is separated from applicable control responsiveness.
11. **Wake tests could pass without exercising a blocking wait.** Both worker and
    input wake tests now require an entered-real-wait event before the triggering
    action.
12. **The original landing unit was too broad.** Core reactor lifecycle and
    TaskMonitor resource isolation now land as ordered Unit A and Unit B reviews;
    reference acceptance ports may land separately before runtime edits.
13. **The first topology correction still treated subtype support routes as a
    closed partial list.** The plan now mandates a repo-wide fixed-route audit,
    names Manager services and Pipeline status/state/spawn outputs explicitly, and
    protects Heartbeat against the whole reserved `weft.` runtime namespace.
14. **Standalone dynamic membership still had a PostgreSQL native-wakeup gap.**
    Generation/signature invalidation rebuilds a waiter for a later direct wait, but
    an already-running inherited strategy is not owner-rebound. Section 6.5 records
    the exact limitation, corrects the overclaim that messages are permanently
    invisible (polling remains), specifies the owner-thread replace design and
    PostgreSQL firing tests, and defers it because BaseTask is sealed and no
    production caller mutates watcher membership.

Residual implementation risks, each with a firing gate:

- whether every Manager and Consumer call to the base turn hook remains at the exact
  current scheduling point;
- whether a concrete stopping hook has hidden policy beyond bounded result drain;
- whether any third-party subclass depends on the now-forbidden public template
  overrides, direct unowned BaseTask wait, protected support queue names, or
  unconditional pre-terminal first turn (mandatory repository search and changelog
  note);
- whether cross-process deployments intentionally point multiple TaskMonitor
  processes at one external JSONL path. This remains explicitly out of scope and
  must be documented if the deployment audit finds it;
- global Backstitch debt prevents a truthful zero-warning corpus claim until the
  repository's separate traceability rollout resolves it.

The earlier independent plan-review clearance is superseded by the 2026-07-10
completed-work findings. R-14 and R-16 through R-20 are corrected in tree, R-15
is the open SimpleBroker lifecycle blocker, and R-21 is a formal accepted
tooling deviation, but current full verification and independent
completed-work review have not yet cleared the result. Unit A and Unit B also
remain uncommitted and unlanded.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope and strategy | 0 | NOT RUN | Backend hardening; no product-scope review required. |
| Codex Review | `/codex review` | Independent second opinion | 0 | NOT RUN | Independent repository subagents reviewed the plan instead. |
| Eng Review | `/plan-eng-review` | Architecture and tests (required) | 3 | REOPENED | Completed-work review found R-14 through R-21. Code/docs corrections are in tree except for R-15's open queue-layer blocker in SimpleBroker; current gates and independent closeout remain open; Backstitch is explicitly not passed. |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | NOT APPLICABLE | No UI scope. |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | NOT RUN | Compatibility and changelog checks are included in Phase 6. |

- **UNRESOLVED:** R-15's upstream SimpleBroker SQLite lease/WAL lifecycle fix;
  current verification, independent
  completed-work review, and Unit A/Unit B landing gates remain open. The
  Backstitch global gate remains explicitly not passed under R-21.
- **VERDICT:** NOT CLEARED. R-14 and R-16 through R-20 are corrected in the
  uncommitted worktree; R-15 remains externally blocked. Engineering clearance
  requires the upstream queue-layer fix, the current gate run, and
  independent completed-work closeout. Section 6.5 remains deferred and becomes
  a prerequisite if any listed production/runtime trigger fires.
