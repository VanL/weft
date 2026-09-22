# TaskMonitor MaintenanceWorker

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [IMPL.8], [IMPL.9], [IMPL.10], [IMPL.11], [OBS.13.10], [OBS.13.12]; docs/specifications/01-Core_Components.md [CC-2.2.1], [CC-2.3]; docs/specifications/05-Message_Flow_and_State.md [MF-5]
Superseded by: none

Class: 5. Replacing the worker clone changes the implementation boundary named
by [IMPL.11]. Hardening applies: this crosses reactor/worker ownership,
resource cleanup, and destructive maintenance execution boundaries.

## Goal and Requested Outcomes

Replace TaskMonitor's shallow-copy/reset maintenance runtime with a normally
constructed, private `MaintenanceWorker` in the registered lane's per-request
thread. TaskMonitor owns ongoing state and scheduling. Workers receive explicit
detached inputs and return typed results through the existing in-process queue
channels. They do not inherit `BaseTask` or `TaskMonitor`, and do not access either through a
strong reference, weak proxy, bound policy callback, or attribute forwarding.

- [x] Replace both maintenance clone call sites with fresh construction.
- [x] Keep the existing service-worker input queues and result/wakeup path.
- [x] Make configuration, per-request inputs, state continuity, and result
  merge ownership explicit without introducing another durable state store.
- [x] Preserve maintenance policy, deadlines, cancellation/finalization bounds,
  queue-visible effects, external logging, and public diagnostics.
- [x] Remove the clone/reset machinery and its inherited-field inventory only
  after both maintenance paths and shared synchronous callers are migrated.
- [x] Consolidate repeated diagnostic state and shared status assembly (slice 6).

The approved design, implementation sequence and execution evidence are recorded
below. All six slices are implemented, independently reviewed and verified.
Slice 6 verification is recorded separately from the earlier extraction evidence.

## Source Documents and Baseline

Plan type: implementation with spec revision.

Spec/code baseline: `9fc913c117d389314fc9cb4f3cec3d272a708b36`.
All source paths below are relative to the repository root.

- [System Invariants](../specifications/07-System_Invariants.md): [IMPL.8–11]
  define reactor authority, worker exceptions, shutdown and resource ownership;
  [OBS.13.10], [OBS.13.12] govern maintenance and cleanup sequencing.
- [Core Components](../specifications/01-Core_Components.md) [CC-2.3]: the
  internal ServiceTask API and TaskMonitor's role.
- [Message Flow](../specifications/05-Message_Flow_and_State.md) [MF-5]: retained
  logs, collation, cleanup slices, external/deferred output, and scheduling.
- `docs/agent-context/decision-hierarchy.md`, `engineering-principles.md`, and
  runbooks `writing-plans.md`, `hardening-plans.md`,
  `review-loops-and-agent-bootstrap.md`, `runtime-and-context-patterns.md`,
  `testing-patterns.md`, `adversarial-acceptance-probes.md`.
- `docs/lessons.md`, especially resource lifetime ownership and reactor waiting.

The [explicit broker session lifetimes plan](2026-09-15-explicit-broker-session-lifetimes-plan.md)
is implemented history for the session/cleanup contract, not a backlog. This
plan changes construction while retaining that contract.

Promotion baseline (2026-09-22 implementation): diff base
`9fc913c117d389314fc9cb4f3cec3d272a708b36` plus the promoted spec snapshots below.
The specs now govern implementation; the proposed delta is historical review
material. Final implementation-mapping reconciliation may update these digests.

| Promoted spec | SHA256 |
| --- | --- |
| `01-Core_Components.md` | `9c199357a4ed31610bfd29f28f82377bf77d1fc678b60fc18330eb817bc1a9bc` |
| `05-Message_Flow_and_State.md` | `e2070052c6db6367e66d945bfadece831f289506d639c7d09cc4bd6d108c7831` |
| `07-System_Invariants.md` | `af213b4f2b01d0fca6f410ce4409f72718df9d060f9dcab71c53eb97f24cc801` |

## Current Structure and Required Reading

Read these paths before implementation:

| File | Relevant ownership at the baseline |
| --- | --- |
| `weft/core/monitor/task_monitor.py` | `_worker_local_monitor_clone`, `_worker_local_maintenance_scope`, `_close_worker_local_resources`; both worker entry points; work/result dataclasses; diagnostic capture/apply and external-status merge; maintenance algorithms and custom-mode synchronous collation |
| `weft/core/tasks/service.py` | ServiceWorkerSpec/Context/Event, registered single-flight lanes, `queue.Queue` input, stop sentinels and typed result publication |
| `weft/core/tasks/base.py` | `_publish_worker_result`, worker result wakeup/cleanup, context/queue helpers, and constructor side effects |
| `weft/_constants.py` | `_WORKER_SNAPSHOT_*` field inventories; existing maintenance policy and timing constants |
| `weft/core/monitor/store.py` | Worker-session borrowing, durable checkpoints, deferred writes, cleanup proofs |
| `weft/core/monitor/external_log.py` | Per-facade counters and shared per-path writer/rotation owner |
| `tests/tasks/test_task_monitor.py` | Isolation, close failure/order, diagnostic merge, real control responsiveness, maintenance cadence, exact deletion and runtime slice sequencing |
| `tests/tasks/test_maintenance_worker.py` (new), `tests/conftest.py` | Explicit input/result/resource boundary regressions and shared-backend classification |
| `tests/tasks/test_service_task.py` | Existing worker channel and lifecycle contracts |
| `tests/core/test_monitor_store.py`, `tests/core/test_monitor_external_log.py` | Store and external sink behavior that must remain stable |
| `tests/core/test_service_convergence.py` | Service-registry selection regression whose private algorithm seam moves to MaintenanceWorker |

`TaskMonitor -> ServiceTask -> BaseTask -> MultiQueueWatcher` explains the current
inventory burden. The clone copies the whole monitor, then removes queue/session
handles, waiter state, locks, task lifecycle and worker registry state. It also
copies data that is meaningful to maintenance. Adding an unrelated watcher field
therefore requires a monitor snapshot decision.

The clone is used by `_run_builtin_cycle_worker` and
`_run_terminal_control_cleanup_worker`. Both are already called from registered
service-worker groups. Built-in work and runtime cleanup are bounded requests;
`terminal_control`, `reserved`, and `dead_tid` remain separate cleanup slices.
The custom processor is a third, broker-free worker and is not converted.

Ordinary `TaskMonitor(...)` construction is not a substitute: BaseTask eagerly
opens queues, publishes initialization/mapping, and may claim endpoints or set
a process title; TaskMonitor registers service groups and may activate itself.
A maintenance worker has no independent task identity or lifecycle.

Comprehension checks: which thread creates the current clone? Which state is
read later by a worker rather than frozen before submission? Which custom-mode
path calls `_run_monitor_store_cycle` synchronously? Why would resetting the
maintenance deadline to zero run vacuum every cycle? Which boundary closes
resources before a service-worker result is published?
Why does closing a second session on the reactor thread recycle the reactor's
cached connection, and which adapter must therefore borrow its existing session?

## Architecture and Ownership

Keep this flow and its existing queue implementation:

```text
TaskMonitor reactor: capture explicit request, record in-flight lane
    -> ServiceTask input queue.Queue
registered lane's per-request thread: construct MaintenanceWorker, run one bounded request
    -> close owned resources in finally
    -> existing ServiceWorkerEvent / BaseTask result queue + reactor wakeup
TaskMonitor reactor: validate request identity, merge result, schedule next work
```

### Construction and code ownership

Define `MaintenanceWorker` as a private implementation class in
`weft/core/monitor/task_monitor.py` initially. It is not a public export and
inherits no task/watcher runtime. Keep work/result types in the same module to
avoid a new import cycle. Module extraction is not an objective.

Construct one instance per work item inside the existing worker entry point.
It accepts only a detached input record and explicitly owned or borrowed
resources supplied by its context manager. Queued lanes own their resources;
the synchronous adapter borrows only the reactor session on the same thread,
as specified below. No `vars(task)`, `copy(task)`, `__new__` partial construction,
`getattr` forwarding, inheritance from the task, method rebinding, or callback
that exposes the reactor. The existing service dispatch adapter may be a bound
TaskMonitor method, but it may only consume the already-prepared request and
invoke the worker; it must not read reactor policy/cached state on that thread.

Use three bounded operations on the same class: `run_builtin_cycle(work)`,
`run_runtime_cleanup(work)`, and `run_collation(work)` for the synchronous
custom-mode preparation. Extend the existing two queued work records with a
typed `MaintenanceInputs` value; add a collation-only work value for the third
operation. A module-local
`_maintenance_worker_scope(inputs, *, borrowed_session=None)` context manager
constructs the worker/resources and closes only resources it owns. The optional
session argument is permitted only for synchronous `run_collation`, never a
queued request. The calling adapter adds cleanup outcomes to the typed result before
returning. These are domain operations, not an extensible command registry.

Move coherent maintenance methods to the worker with their existing bodies,
then replace BaseTask conveniences with explicit resource access. Preserve the
existing SimpleBroker boundary: use WeftContext/BrokerTarget/config resolution,
one supplied BrokerSession, cached owned queue handles, public broker APIs, and generator
history reads. No replacement queue factory or new persistence layer.

The inherited surface reached by the moved bodies must be replaced explicitly:

| Existing dependency | MaintenanceWorker replacement |
| --- | --- |
| `_get_connected_queue().get_connection()` (including the `ExitStack` use) | A bounded `_connection()` context over the invocation-cached global-log queue's `get_connection()`; reuse its connection wrapper to avoid repeated PostgreSQL target validation. No synthetic inbox queue or watcher lookup |
| `_queue(WEFT_SERVICES_REGISTRY_QUEUE)` | Cached `queue(name)` over invocation-owned facades. Queued work uses `session.queue(name)`; the synchronous borrowed-session scope uses `context.queue(name, persistent=True)` on the same resolved broker key, for the lifetime reason below |
| `_set_activity` in `_run_raw_external_task_log_cycle` | Delete this call and its snapshot guard from the moved body; reactor dispatch/result handling retains activity ownership |
| Guarded `_register_tid_state` and serve-log health transition | Delete worker-side notification branches; return observed status for the reactor's existing merge/notification path |
| `_task_context()` / `_monitor_context()` | Explicit resolved, detached context input; preserve immutable broker configuration identity |
| `taskspec` and `tid` | Explicit monitor TID and resolved config values actually needed; no TaskSpec shell or metadata copy |
| `_runtime_handle` and `_manager_tid_for_log` through operational logging | No worker replacement. Serve-log emission and its identity resolution stay on TaskMonitor |

The guarded scheduling path reached through `start_control_cleanup` is removed
as specified below. Re-trace the helper closure during slice 1; do not turn a
method count into a new inherited-field admission mechanism.

Maintenance algorithm ownership includes the transitive helpers reached from
`_run_builtin_cycle_worker_local` and
`_run_terminal_control_cleanup_worker_local`: retained ingestion/recovery,
collation emission/retirement, raw external deletion, terminal/reserved/dead-TID
cleanup, deferred-output handling, and vacuum/runtime pruning. Reuse existing
store, pruning, lifetime-report and external-log modules rather than duplicating
them. Pure module-level helpers remain shared.

The reactor retains task activation, heartbeat, scheduling, PONG/status,
activity/TID mapping, custom candidate scans/processors, work dispatch, and
result application. Maintenance never starts another worker or updates task
lifecycle. The worker emits no serve-log records. Config-once, cycle/cleanup
results and external-health transitions remain reactor-side. Do not introduce
an operational-log identity descriptor or carry `_manager_tid_for_log`,
RuntimeHandle, parent/manager metadata or TaskSpec into the worker. External
task-log output still needs the monitor TID and its existing sink facade.

### Explicit input and retained-state contract

Build the following detached values on the reactor before enqueueing. Frozen
outer dataclasses alone do not make nested dicts immutable: copy nested mutable
configuration/report containers once at this boundary, transfer exclusive
ownership, and never retain a mutable alias on the reactor. Immutable broker
metadata may be shared. `WeftContext.project_config` is a mutable dict despite
the frozen outer context; detach it too. Do not pass a live broker session or
Queue in queued requests. The synchronous adapter supplies its borrowed
session separately from the detached request, without crossing a thread boundary.

| Input/state | Owner and transfer | Result/merge rule |
| --- | --- | --- |
| Broker target and resolved context | Reactor resolves existing `_task_context()` metadata; queued work opens its own session, synchronous collation borrows the reactor session separately. Include project root and resolved broker config, not a live task context with handles. | Never merged as runtime state. |
| Maintenance policy/config and identity | Detached resolved config; monitor TID for exclusions/external task-log output; resolved external sink path and mode. No parent/manager identity descriptor. | No full TaskSpec, task state, callback observer or runtime handle in the worker. |
| Request metadata | Existing request ID, `now_ns`, `task_log_owner`; cleanup `slice_kind`. | Validate the lane/request identity with the existing event/result checks before applying or scheduling. |
| Maintenance cadence | Reactor owns `_next_maintenance_due_monotonic`; request includes that deadline. Worker evaluates it at the existing point in the cycle. | If maintenance ran, return its report and the next deadline computed after execution using the existing interval. If skipped, return no maintenance update; retain the previous deadline/report. |
| Cleanup continuation | Reactor owns `_runtime_cleanup_queue_discovery_pending` and `_next_runtime_cleanup_queue_discovery_due_monotonic`; cleanup work already includes their inputs. | Preserve `_handle_control_cleanup_worker_result` rules: pending/failure uses catchup; only completed `dead_tid` discovery advances normal interval; skipped earlier slices cannot postpone discovery. Built-in diagnostics must not overwrite this reactor-owned scheduling state. |
| External/deferred status seed | Detached last health/error/last-emission status plus deferred pending/error/last-flush values where failure/skip paths must preserve them. Worker facade totals start at zero, as today. | Return facade counter deltas and latest observed status. Reactor retains cumulative totals, applies deferred backing fields, and emits health/TID-map changes through the existing merge helpers. Never add an inherited cumulative total twice. |
| Previous diagnostic reports | Reactor retains last reports. These are not work inputs merely to round-trip them. | Use typed optional update groups: `None` means unchanged, a present group with zero counts means performed/reset to zero. Preserve that distinction on skip/failure paths. |
| Durable progress | Store/broker owns collation checkpoints, raw message references, deferred outbox and cleanup proofs. Worker reads through its supplied broker resources. | No in-memory checkpoint copy becomes authoritative. Custom scan `_last_checkpoint` remains on its existing reactor path. |

The typed result owns ordinary diagnostic groups, a conditional maintenance
report, the external-status observation/deltas, runtime-cleanup readiness, and
close errors. Retain current public STATUS/PONG shapes. Replace the blanket
`_TaskMonitorCachedDiagnostics` round-trip where it carries unchanged reactor
state with explicit group updates. For each current diagnostic field, record
its group and whether each mode writes zero, produces a value, or leaves it
unchanged in a parametrized contract test before changing the merge. In
particular, raw-external mode resets its listed collation/control fields but
must not accidentally erase retained reserved-cleanup or maintenance reports.
Do not use an untyped dictionary of arbitrary attribute names as the new update
protocol.

### Shared synchronous callers

`_run_monitor_cycle` in custom mode calls `_run_monitor_store_cycle` on the
reactor before the custom processor. Preserve that ordering and broker-free
custom processor contract. Route that synchronous maintenance sub-operation
through the same fresh MaintenanceWorker and resource scope on the calling
thread, with a mode-specific request that performs collation only. Apply its
result immediately on the reactor, including existing cleanup-readiness
scheduling. Do not accidentally run vacuum, the built-in processor, or a second
custom scan there. Foreground `scan_once()` behavior and its checkpoint ownership
remain unchanged. Tests that directly call private maintenance methods should
construct the worker or exercise the owner adapter; do not retain clone-era
monkeypatch propagation as a production feature.

Session ownership is an explicit adapter choice, not inferred from the class
name. For this synchronous adapter, pass the reactor's existing BrokerSession
as `borrowed_session`. Never open a second session, close the lent session,
call `recycle_thread`, or perform thread-cache cleanup. A second session on the
same thread shares the process-session key: closing it recycles the reactor's
cached core and is refused while that thread has any same-key operation open.
The production turn hook calls custom collation outside an operation; the
refusal is a latent manual-driver/test hazard, while per-cycle core recycling
would be a real behavior change introduced by the earlier draft.

Keep owned Queue facades in an invocation-local cache. In this adapter create
them with the existing `context.queue(name, persistent=True)` and close them
after their iterators/operations finish. The context must retain the borrowed
session's exact backend target/options/config identity: the reactor obtains it
from `TaskMonitor._monitor_context()` (which delegates to `_task_context()`),
detaches mutable context data, and preserves the resolved broker target and
immutable `broker_config` object. Do not reconstruct it from ambient settings
on the worker. Do not mint these
facades via `borrowed_session.queue`: that session retains every minted facade
until its own lifetime ends, even if the facade has been closed. Open the
synchronous store with `open_monitor_store(queue=...)`, lending it the cached
global-log facade; the scope owns and closes that facade. The services-registry
read also needs this cache, even in non-destructive custom mode, through
stale-service/stale-open summary evaluation. Queued lanes retain
`open_monitor_store(session=...)` and their own session lifetime. These are
existing broker/store APIs; no MonitorStore or SimpleBroker API change is needed.

Verify repeated synchronous cycles preserve the reactor's physical core and
do not retain per-cycle queue facades in its session. Include a real custom
collation call under an outer same-key connection context and a stale-service
summary case that reaches the services queue. Closing invocation-owned facades
must leave the borrowed session and its existing owner queues usable. This
does not authorize holding transactions across waits or thread dispatch.

`run_collation` returns a typed collation-preparation result with observations,
cleanup readiness, and operation/close errors. The synchronous adapter applies
produced observations only after successful resource closure. An ordinary
store failure can produce partial/error observations and custom scanning still
continues, matching the baseline. Pre-execution construction or close failure
applies no worker observation, records `_last_collation_store_error` and an
unavailable `MonitorStoreStatus` with that error on the reactor, and authorizes
no cleanup. It still proceeds to the existing custom candidate scan/processor;
that processor retains ownership of its own success/error outcome. A fatal
BaseException propagates after all cleanup attempts. Do not reset/probe the sink
a second time in this operation: custom-mode reactor preparation already does
that before collation. Test each of these paths, not only successful collation.
Remove the incidental reactor store reopening in `_apply_cached_diagnostics`
when it is replaced: cached availability/status is a value, not a requirement
to hold a reactor store handle. Move tests of direct store maintenance to the
worker boundary instead of retaining that resource solely for private tests.
Eliminate the reactor's `_monitor_store` field and its `_ensure_monitor_store`
role; the worker owns the lazy store opener and lifetime. PONG's
`monitor_store_status` comes only from cached merged observations and never
opens a store. Preserve existing cached status fields independently of handles.
Before the first maintenance/collation observation, retain the constructor's
cached unavailable-store default. Test that a cold-start PONG reports that
default without probing or opening a store.
This removes an incidental handle from the reactor, not a live resource leak:
`MonitorStore.close` does not close a lent session, and the existing reactor
cleanup does not close `_monitor_store`.

Remove the `start_control_cleanup` switch from the moved maintenance algorithm:
it always has the effect of today's `start_control_cleanup=False`, returning
`runtime_cleanup_ready` instead of submitting work. The synchronous reactor
adapter calls `_maybe_start_terminal_control_cleanup_worker(now_ns=...)` only
when that signal is true after successful closure, before continuing the custom
scan. The existing custom policy is non-destructive, so it does not manufacture
readiness under current settings. Preserve that policy; this refactor must not
enable deletion or cleanup scheduling merely because a shared adapter exists.

### Resources, errors and stop

- Queued lanes own one worker-local BrokerSession; the store borrows it.
  Synchronous collation borrows the reactor session and lends its own cached
  global-log queue to the store, as above. Cache/reuse invocation-owned queue
  facades. Sink facades share only the existing per-path writer lease.
- Establish the cleanup scope before opening session/store/sink resources.
  Partial construction failure must unwind everything already acquired.
- Preserve close-all behavior and order: store, sink, owned queues (deduplicated),
  owned session; skip session close entirely for the synchronous borrower.
  Retain the first fatal unwind and attach later cleanup failures.
  No task finalizer, task STOP/cleanup call, or reactor resource close.
- Publish typed success/diagnostics only after cleanup. Keep the existing
  failed/pending conversion for ordinary body/close errors; do not discard a
  more specific fatal BaseException. Deferred logging failures still gate
  deletion according to the current policy; vacuum remains best-effort.
- Preserve the precise failure merge: built-in body failure can return partial
  observations after successful close; any close error blocks those observations
  and sets runtime-cleanup readiness false. Runtime body failure stays pending;
  a close error also clears next-slice selection and blocks store/external-status
  merge, while cleanup counts/errors/pending still reach their owner handler.
  Construction/session/sink initialization failure before executing work returns
  no diagnostic updates. Store-open/schema/checkpoint failure inside an admitted
  collation operation remains a produced unavailable-store observation, as the
  existing report-only failure tests require. Remove the current
  fallback that reads `_capture_cached_diagnostics()` on the reactor object
  from the worker thread; the reactor simply retains its own last observations.
- Retain existing ServiceWorkerContext stop events, lane admission, shutdown
  deadline and result wakeup. This refactor does not add mid-SQL cancellation,
  change admitted-request completion behavior, or make stop wait indefinitely.
  A timed-out owner cleanup does not prove the thread stopped: remaining worker
  resources stay owned until its finally runs. Never close them from the reactor.
- No extra thread, queue transport, polling loop, executor, task ID, or process.
  Weak references are not a synchronization or state-transfer mechanism.

## Invariants, Risks, Rollout and Rollback

Preserve [IMPL.8–11], [OBS.13.10], [OBS.13.12], task lifecycle ordering,
TaskSpec immutability, reservation policy, exact-delete evidence and age gates,
queue names, log/result formats, and runtime-only `weft.state.*` semantics.
Do not change the declared worker exception allowing monitor broker effects or
extend it to ordinary workers. TID mappings remain LivenessMonitor's cleanup
custody. Keep cleanup slices separate and prevent nested cleanup executors.

Primary risks and required defenses:

| Risk | Required defense |
| --- | --- |
| Fresh defaults erase cadence or reports | Multi-cycle deadline and optional-update-group contract tests |
| Input captures live reactor state too late | Capture at submission; pause worker, mutate reactor values, prove detached input |
| Moving methods silently changes custom mode | Real synchronous collation/custom processor regression; same maintenance implementation |
| Maintenance closes reactor resources or leaks partial initialization | Real surviving sibling handles; construction/body/close failure injection at owned boundaries; verify owner remains usable |
| Synchronous worker recycles reactor core or retains queue facades | Borrow the existing session, close only invocation-owned facades, preserve exact core identity and bounded facade lifetime across repeated custom cycles |
| Fresh Task construction emits duplicate lifecycle evidence | Worker never inherits/constructs BaseTask; assert no extra initialized/started/mapping/endpoint effects through a completed maintenance request |
| Construction-failure fallback re-merges cumulative totals as deltas (confirmed live bug) | Primary red test seeds emitted total 3 and applies two failed-cycle results; total must remain 3, not become 6 then 12. Return no diagnostic update on construction failure |
| Health reverts after result application | Preserve existing delta/backing-field merge and same-path rotation tests |
| Cached diagnostic snapshot overwrites another lane (latent risk) | Merge only produced groups; scheduling stays reactor-owned. Current turn-hook admission returns early during cleanup, preventing this stale-discovery overwrite today |

Rollout is one coordinated release of spec, owner adapter, worker, and tests.
The slices below are local review checkpoints, not independently deployable
mixed implementations. No runtime fallback to cloning and no feature flag.
Queue/store formats are unchanged, so no data migration or one-way door is
introduced. Rollback reverts the coordinated change and spec to the prior clone
implementation, then restarts the monitor through its normal lifecycle. Do not
hot-swap a live worker or force-kill it merely to roll back. Existing retry-safe
store/broker proofs remain the recovery mechanism after interrupted work.

Observable success: repeated bounded maintenance cycles retain correct STATUS
counts/deadlines, real PING/STOP stays responsive while work is paused, queues
are deleted only under the same proofs, and worker handles close without
changing the reactor's ownership or opening extra task lifecycles.

## Proposed Spec Delta

Promotion strategy: **B, atomic**. At the beginning of the implementation
change apply the reviewed normative text; add matching implementation mappings
and code in that same landing unit. No intermediate spec-only release claiming
MaintenanceWorker exists. Record the promotion baseline at that point. The
planning task adds backlinks only, not these behavioral requirements.

### `07-System_Invariants.md` [IMPL.11]: replace its normative paragraph

> **IMPL.11**: TaskMonitor maintenance executes in a freshly constructed
> MaintenanceWorker with explicit detached inputs. The worker has no task or
> watcher lifecycle and no reference or proxy to reactor task/watcher state.
> TaskMonitor owns ongoing scheduling, cumulative counters and cached status;
> requests are captured on the owner thread before submission. Built-in and
> runtime-cleanup work uses the existing registered service-worker input queues
> and typed result channel on the registered lane's per-request thread. Each
> queued invocation owns a BrokerSession, queue facades, Monitor store and
> external-sink facade. Its store borrows that session. No watcher, lifecycle,
> queue, store, sink counters, broker session or mutable task state is shared
> across the reactor/worker thread boundary.
>
> Synchronous custom-mode collation
> reuses the same implementation on the reactor thread and explicitly borrows
> the reactor session; it never closes or recycles that session. It owns its
> temporary queue facades, store and sink facade, with the store borrowing its
> cached global-log queue. Its resource scopes preserve the reactor's cached
> connection and do not accumulate per-invocation queue facades in the reactor
> session.
>
> Both adapters close all invocation-owned resources in finally,
> including after partial initialization. Typed results are applied/published
> only after owned store, sink, queue and, for queued work, session cleanup.
> Same-path sink facades lease one process-local writer/rotation owner so only
> one live rotating handler exists per resolved path. Close failures produce
> failed/pending results. The reactor applies only the diagnostic groups and
> scheduling updates produced by that invocation; omitted updates preserve
> existing values. Cumulative external/deferred status is merged on the reactor
> thread, including deferred backing fields and health-transition notification,
> so later status refresh cannot revert a worker's result. Serve-log emission
> and activity/TID-mapping updates remain on the reactor. Existing cleanup
> policy, task-control responsiveness and shutdown bounds remain unchanged.

Replace clone/TaskSpec-copy details in its implementation mapping with the
actual constructor/input types, worker scope/close helpers, result types and
TaskMonitor result merge functions. Keep sink/store/test references and update
the nearby scheduling ownership note if method ownership moves. Code docstrings
must cite this file and [IMPL.11] at construction, cleanup and merge boundaries.

### `01-Core_Components.md` [CC-2.3]: append to the TaskMonitor description

> TaskMonitor's built-in and runtime-cleanup maintenance is implemented by an
> internal MaintenanceWorker constructed for each bounded invocation. It is
> not a BaseTask or a second monitor. TaskMonitor supplies detached inputs and
> commits returned state updates; the existing ServiceTask worker groups own
> per-request thread dispatch and in-process queue communication. Synchronous
> custom collation uses the same implementation on the reactor thread, lending
> its existing broker session without transferring cleanup ownership.

### `05-Message_Flow_and_State.md` [MF-5]: insert after built-in worker ownership

> Before submitting maintenance, the TaskMonitor reactor captures explicit
> configuration, work identity and required state values. A fresh
> MaintenanceWorker consumes that request with invocation-owned broker resources
> on the registered lane's per-request thread and returns typed diagnostic
> updates after cleanup. The synchronous custom-collation adapter instead lends
> its reactor session on the same thread and closes only invocation-owned
> resources, preserving reactor connection reuse. State is not transferred
> by copying the live TaskMonitor or sharing it through a weak proxy. The
> reactor remains the owner of maintenance deadlines, cleanup continuation,
> cumulative logging counters and cached public diagnostics.

Existing deletion, age, checkpoint, retention and scheduling rules in [MF-5]
remain unchanged. Update nearby implementation mappings and the plan backlinks
with actual code ownership as part of the atomic change.
Also reconcile [CC-2.2.1]'s existing worker/cleanup ownership mapping; its
generic reactor and lifecycle contract is unchanged.

## Implementation Slices

Execute in order. One implementation owner edits the shared module and owns
formatting. Independent reviewers may inspect in parallel; overlapping writes,
source mutation probes, and spec promotion are sequential.

1. **Characterize ownership and state, then review the delta.**
   Files: `tests/tasks/test_task_monitor.py`, new
   `tests/tasks/test_maintenance_worker.py`, `tests/conftest.py`, this plan as
   evidence is learned.
   Read the input/result and merge functions above. Add/retain real-broker tests
   for the diagnostic mode/update matrix, skipped maintenance cadence, and
   cleanup continuation. The primary red test is construction-failure counter
   doubling: establish cumulative external emitted total 3 through the existing
   result/status path, force clone/sink-lease/snapshot construction failure, and
   apply two built-in results through the reactor handler. Assert both failures
   remain visible, no cleanup is authorized, and totals remain 3 after each
   result. Baseline instead produces 3, 6, 12 because the fallback returns
   reactor cumulative diagnostics as worker deltas. Port the same test to the
   new construction seam; repeat the assertion for blocked-deletion totals and
   retained deferred status. Do not mock the merge that exposes the bug.
   Retain the secondary coupling regression: attach an unrelated reactor-only
   field and run real maintenance; baseline fails worker clone admission.
   Add a worker construction test requiring no BaseTask/TaskMonitor
   constructor and detached submission inputs; it becomes green after cutover.
   Characterize synchronous session reuse with real resources before moving
   bodies: same core across cycles, callable under an outer connection context,
   no accumulated queue facades, and stale-service summary evaluation reaching
   the services registry. These baseline-green cases must stay green.
   Use existing harness/driver fixtures; do not synthesize lifecycle proofs.
   Record exact method/field disposition in test cases, not another global
   inherited-field ledger. Stop if evidence implies a policy change.

2. **Spec-promotion slice and explicit work/result boundary.**
   Files: the three governing specs, `task_monitor.py`, tests and this plan.
   Apply strategy B text inside the atomic implementation unit; record the
   promotion baseline. Extend existing work records with detached input values;
   define typed diagnostic groups/optional maintenance update. Capture them on
   the reactor, not in `_run_*_worker`. Preserve channel names/events and no new
   framework in ServiceTask. Verify paused-worker input isolation, unchanged vs
   zero-result merging and cumulative status. Stop if a live task reference,
   new task lifecycle, generic attribute-patch protocol or transport is needed.

3. **Construct MaintenanceWorker and migrate built-in/shared collation.**
   Files: `task_monitor.py`, `tests/tasks/test_task_monitor.py`,
   `tests/tasks/test_maintenance_worker.py`,
   `tests/core/test_service_convergence.py`; store/external tests only for moved
   observation seams. Move the coherent built-in helper
   closure, introduce the resource scope and explicit broker/context helpers,
   and preserve custom-mode synchronous collation through the same object.
   Apply the explicit owned/borrowed session split and queue-cache factories;
   the synchronous context is detached from `_monitor_context()` with broker
   identity preserved, and the store borrows its cached global-log facade.
   Verify no core recycle and no accumulation of session-owned facades. Do not
   change store.py or SimpleBroker to invent another ownership API.
   Keep one body for each algorithm. Reuse broker/store/pruning APIs and actual
   sink facades. No inheritance from TaskMonitor and no method rebinding.
   Verify retained ingestion/recovery, external/deferred output, best-effort
   maintenance cadence, custom processor ordering, report preservation and
   control responsiveness. Stop if a second maintenance implementation appears.

4. **Migrate runtime cleanup and remove copy/reset machinery.**
   Files: `task_monitor.py`, `_constants.py`, `tests/tasks/test_task_monitor.py`;
   `docs/ruff-suppression-registry.md` and `tests/specs/test_ruff_policy.py` only
   as existing suppressed owners move/disappear. Migrate terminal_control,
   reserved and dead_tid through the same worker construction/cleanup boundary.
   Preserve slice caps, chaining, protection gates and exact-delete behavior.
   Remove `_worker_local_monitor_clone`, old reset-only helpers/finalizer,
   `_WORKER_SNAPSHOT_*`, and clone-only imports/tests once unused. Replace tests
   of whole-object field equality with explicit input/result/resource contracts;
   retain all behavioral regressions. Verify partial init and ordinary/fatal
   body/close failures, stop deadline and a surviving owner/sibling broker handle.
   Stop if removal would change BaseTask, ServiceTask or cleanup authority.

   Failure-injection migration: use the module-local
   `_maintenance_worker_scope(inputs, close_errors, *, borrowed_session=None)` as the
   construction/ownership seam, with
   `MaintenanceWorker`'s lazy store opener and its close helper as the resource
   seams. Keep the scope active before any session/sink acquisition; do not put
   unguarded I/O in the constructor. Replace patches of
   `_worker_local_monitor_clone` with wrappers at these concrete boundaries,
   and wrap real session/store/sink/queue close methods to record order and
   inject failure. Wrap real session and sink acquisition within the scope too:
   fail at each acquisition stage and verify resources acquired earlier close.
   Use Events to hold a real worker before/after acquisition or
   result return. Do not add production-only test hooks or replace resources
   with a fake broker. Port (rather than delete) the existing setup-failure,
   clone-resource-failure, body-failure, close-attempts-all-resources,
   BaseException, both-entry close-failure matrix, and owner-core-survival tests
   in `tests/tasks/test_task_monitor.py`. Seven baseline tests patch instance
   methods that the clone rebinds; port their behavioral intent to class-level
   patches on `MaintenanceWorker`, the sanctioned algorithm seam after cutover.
   Retire only the field inventory and the expectation that arbitrary TaskMonitor
   instance attributes/methods are copied. Resource-fault tests keep the scope
   and real resource seams above; no instance-patch propagation is retained.

5. **Reconcile traceability and verify the complete change.**
   Files: specs/mappings/backlinks, this plan/index, CHANGELOG; tests as fixes
   require. Run final gates below. Review each meaningful code slice and the
   final delta independently, preferably across agent families. Disposition
   every finding. Update lessons only for a repeated discovered mistake.
   Close only after verification and commit authorization; verify the actual
   commit with git before marking plan/index completed. Do not push.

6. **Consolidate shared diagnostic functionality.**
   Files: `weft/core/monitor/task_monitor.py`,
   `tests/tasks/test_maintenance_worker.py`, `tests/tasks/test_task_monitor.py`.
   This is a behavior-preserving follow-up under [IMPL.11] and [MF-5], within
   the existing Class 5 work. No new normative spec delta is needed.

   Use the existing typed diagnostic groups as the stored state in both
   TaskMonitor and MaintenanceWorker, with defaults defined once. Each object
   owns its own values. Update the existing readers/writers, capture and apply
   paths to use those groups directly, removing repeated flat-field plumbing.
   Preserve partial updates within a group and detach nested mutable values
   at the result boundary. Keep `None` meaning no update and a present zero
   meaning reset; group defaults must not turn skipped work into an observation.

   Factor the common parts of `_refresh_external_task_log_status` into one
   small pure helper used by both objects. Preserve disabled-sink and retained
   health/deferred-status behavior. Cumulative totals, scheduling and
   health/TID-map notifications remain TaskMonitor's responsibility; worker
   observations remain per invocation. Keep the existing maintenance algorithms,
   resource scopes and dispatch paths. This is consolidation, not a new state
   framework, inheritance layer, generic field registry or compatibility layer.

   Adapt the existing field-contract and external-status tests to the grouped
   representation. Preserve their behavioral assertions, including defaults,
   omitted/zero/partial updates, detached values, cumulative totals and health
   after a later refresh. This refactor has no claimed new behavioral defect:
   use baseline-green characterization and inspect that shared functionality
   now has one implementation. Run the existing targeted SQLite/PostgreSQL
   commands and final gates below; obtain a scoped independent review of the
   consolidation, then repeat slice 5's closeout checks. Do not treat the
   extraction's earlier passing results as verification of this follow-up.

## Test Matrix and Verification

All contract cases use real queues/stores and owner/worker paths. Use narrow
wrappers or barriers for deterministic failures and interleavings; never mock
away reservation, exact deletion, session ownership or result publication.

| Contract | Required firing proof |
| --- | --- |
| No runtime clone dependency | Unknown reactor-only field no longer prevents maintenance; no TaskMonitor/BaseTask construction or extra task initialization/mapping/endpoint effects |
| Detached inputs | Pause before worker consumption, mutate owner config/nested values/status; worker sees submitted values and cannot mutate owner through aliases |
| Diagnostic transfer | Enumerate current cached diagnostic fields across collated/raw-external/custom-collation and cleanup results; assert unchanged vs zero vs produced values |
| Cold-start diagnostics | PONG before the first maintenance/collation observation retains the initial unavailable-store status and performs no store open/probe |
| Cadence and continuation | Existing monotonic maintenance test across at least three cycles; skipped earlier cleanup leaves discovery deadline; final dead_tid advances it; pending/failure uses catchup |
| External output | Existing same-path rotation, worker counter deltas, deferred status/backing fields and health-change tests on both result paths |
| Construction failure does not duplicate totals | Primary red test applies two failed constructions to the real reactor merge: seeded emitted and blocked-deletion totals remain unchanged; deferred status is preserved; errors remain visible and no cleanup is authorized |
| Resource ownership | Owner stays usable; invocation-owned session/store/sink/queues close on their owner thread, borrowed session remains open; each acquisition stage fails with prior owned resources closed, body error, every close failure, and fatal unwind |
| Synchronous borrowing | Same owner core across repeated custom cycles; operation succeeds inside an outer same-key connection context; no session close/recycle; temporary facades released rather than retained in owner session; stale-service summary fires services-registry queue path; exact context/session broker identity preserved |
| Communication/lifecycle | Existing registered lanes, request identity checks, single-flight behavior, result wakeup and stop/deadline behavior; existing per-request threads with no additional dispatch, persistent worker or poll loop |
| Cleanup safety | Existing terminal/reserved/dead-TID cases including live/missing evidence, retention age, reserved policy, exact deletion and retry after partial work |
| Other callers | Foreground scan behavior and custom-mode synchronous collation/processor order stay stable; ordinary setup/store/body/close failure still reaches custom scanning, no failed-close observation merge, fatal unwind cleans up first, no second sink probe/reset |

Planning verification (this task): run plan metadata, spec hygiene and whitespace
checks; inspect baseline references and backlink targets. These documentation
checks do not claim runtime readiness.

Implementation commands, after `. ./.envrc` (use repo-managed tools):

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py tests/tasks/test_maintenance_worker.py tests/tasks/test_service_task.py tests/core/test_monitor_store.py tests/core/test_monitor_external_log.py tests/core/test_service_convergence.py -q
bin/pytest-pg --fast tests/tasks/test_task_monitor.py tests/tasks/test_maintenance_worker.py tests/tasks/test_service_task.py tests/core/test_monitor_store.py tests/core/test_monitor_external_log.py tests/core/test_service_convergence.py
./.venv/bin/python -m pytest tests/specs -q
./.venv/bin/python -m pytest
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
git diff --check
```

Per-slice tests select the matrix cases owned by that slice. Final verification
includes the default SQLite suite and targeted PostgreSQL suite above. Run
relevant slow cases if a changed boundary is only covered there; a green default
suite does not prove excluded cases. Traceability gate here is
`tests/specs/test_spec_hygiene.py`, plan metadata, and manual reconciliation of
exact references/mappings. At baseline no backstitch executable/configuration
is available; do not invent a passing result. If the checkout gains a mandated
traceability tool before implementation, run its actual configured gate too.

For behavior-preserving moves, retain baseline-green characterization tests and
rerun after each move. Construction-failure double counting is the primary
failing regression, with inherited-state coupling a secondary one; new
constructor/aliasing contracts must fire as tests, not
merely be asserted in prose. Record exact commands/results and any deviations.

## Out of Scope

No manager-spawned MaintenanceTask, new BaseTask mode, new process, reusable
worker framework, new public API/dependency/configuration, CLI/CI change,
persistence migration, retention policy change, extra cancellation mechanism,
weak-proxy state sharing, or module split based on size. Do not redesign the
custom processor or refactor unrelated services.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| [IMPL.11], [MF-5] connection reuse | F3 originally proposed `session.connection()` for every operation | `_connection()` borrows the invocation-cached global-log queue's connection wrapper | Existing physical-connection regression failed on PostgreSQL in both owned/borrowed modes: six operations opened six extra validation connections through new DBConnection wrappers. Reusing the queue wrapper preserves warm operation reuse without changing resource ownership | No normative delta needed; the promoted spec requires preserved reactor connection/resource lifetimes and does not prescribe a connection accessor |
| [IMPL.11], [MF-5] synchronous notifications | Preserve custom collation's existing notification timing | Shared status merge now explicitly suppresses notifications for synchronous collation; queued results retain them | Review found that the extraction added an earlier task-state snapshot and emit-time health event. Real sync/queued success/failure tests reproduced the extra sync edges; the correction keeps merging shared while preserving existing probe/activity publication | No normative delta; restore the baseline behavior. `_register_tid_mapping` is a historical method name writing `weft.state.tasks.<tid>`, not the retired shared queue |

The accessor adjustment above preserves the promoted contract. Append a row
and explicitly revise the spec before implementing any discovered contract change.

## Review and Evidence

Implementation slice 1, 2026-09-22: before runtime edits,
`python -m pytest -n 0 tests/tasks/test_maintenance_worker.py -q` produced one
expected failure and two passes. The real reactor handler doubled seeded
emitted/blocked totals `(3, 5)` to `(6, 10)` then `(12, 20)` on construction
failure; both synchronous connection-reuse cases passed, including an outer
same-key operation. Spec promotion's exact-text check failed before promotion
and passed afterward; all six metadata/spec-hygiene tests passed.

Author fresh-eyes and independent plan/delta review are required before code
work. Review the plan and exact delta against the baseline sources and tests;
answer PASS/BLOCKED on implementability and whether the change would degrade
correctness, isolation, or robustness. Prefer removal of unnecessary work over
speculative abstractions. Record findings and disposition here, then verify
accepted fixes in a scoped second pass. Reviewer availability, findings and
planning gate results will be recorded before the planning handoff.

Author fresh-eyes review, 2026-09-22: corrected two ambiguities before independent
review completion. Synchronous custom collation must use the same algorithm
owner without running built-in processing or vacuum. Pre-execution construction
failure must preserve diagnostics, while a store setup failure during collation
must report store unavailability. The field/update matrix and failure tests make
these distinctions executable. No implementation has been performed.

Read-only independent dependency analysis confirmed explicit context must detach
`project_config`, maintenance due checking stays after the cycle body, and
shutdown remains bounded-slice completion rather than immediate cancellation.
All three are incorporated above.

Independent reviewers: a native same-family reviewer traced dependencies and
the state/failure boundary; Claude Code was available and completed the broader
read-only plan/spec/code review using its configured model (reported as
`claude-opus-4-8`). No reviewer edited repository files.

| Finding | Disposition | Verification |
| --- | --- | --- |
| Native P2: synchronous custom collation lacked precise setup/close failure and continuation rules | Accepted; explicit collation result, no failed-close observation merge, continued custom scan, fatal cleanup and no duplicate sink probe/reset | Native scoped second pass: PASS, no new defect |
| Claude B1: reactor store field/reopen/opener had no explicit final owner | Accepted; eliminate reactor `_monitor_store` and opener role, remove diagnostic-apply reopen, worker owns store, PONG uses cached observations | Claude scoped second pass: PASS |
| Claude F2: moved `start_control_cleanup` could reach reactor submission from worker | Accepted; remove switch, return readiness, reactor conditionally schedules after successful close. Explicitly retain current custom mode's non-destructive policy; do not assume it currently authorizes cleanup | Claude scoped second pass: PASS |
| Claude F3: clone deletion removes failure-injection seams | Accepted; name module-local scope and real resource opener/close wrappers, port the existing failure families and owner-core survival tests | Claude scoped second pass: PASS |
| Claude second-pass nonblocking notes: pin initial PONG status and acquisition-failure seams | Accepted; added cold-start cached-status test and explicit session/sink acquisition-stage fault wrappers | Author checked initial cached status against the constructor and the test matrix against the resource scope |

Initial independent plan review, 2026-09-22: PASS. Both reviewers verified their
scoped fixes; the broader reviewer found no new defect. The two nonblocking
test clarifications above were incorporated in the final author pass. This is
approval of plan implementability, not runtime readiness or spec promotion.
The later F1 finding below invalidated that verdict for the session design;
the revised boundary requires its own scoped verification.

### F1–F7 revision, 2026-09-22

Unit: revisions prompted by the user's subsequent code-grounded review, against
the same recorded code/spec baseline. No runtime implementation or normative
spec promotion is part of this revision.

| Finding | Disposition and reason | Required verification |
| --- | --- | --- |
| F1 P1: fresh synchronous session changes connection lifetime | Accepted. Only queued lanes own sessions; same-thread custom collation borrows the reactor session and never closes/recycles it. Temporary queues use `context.queue` and the store borrows the cached global-log queue, avoiding retention by the long-lived session | Real same-core, outer-operation, queue-lifetime and services-registry branch tests; scoped review of exact spec delta |
| F2 P2: unnecessary operational-log identity descriptor | Accepted. No worker serve-log emission or manager/parent descriptor; the existing reactor emission/merge paths remain owners | Trace config, cycle, cleanup and health emission sites; retain their existing behavior tests |
| F3 P2: unspecified inherited surface | Accepted. Explicit replacement table covers connection access, services queue, activity, TID notifications, context, TaskSpec/TID and runtime-log identity; remove guarded scheduling from moved bodies | Re-trace transitive helper closure and exercise all three operations without BaseTask conveniences |
| F4 P2: clone-failure double counting is the strongest regression | Accepted. Primary slice 1 red test covers two failed constructions through the real reactor merge; unclassified-field admission remains secondary | Local baseline probe reproduced emitted totals 3, 6, 12; implementation must keep cumulative totals unchanged and retain failure reporting |
| F5 P3: reactor-store removal is not a live leak fix | Accepted. Preserve B1's ownership cleanup and explicitly disclaim the incorrect leak rationale | `MonitorStore.close` does not close lent resources; inspect reactor cleanup and preserve owner session lifetime |
| F6 P3: scheduling overwrite is latent | Accepted. Retain the no-overwrite contract, but state that current turn admission prevents built-in work during cleanup | Existing admission/continuation tests remain firing |
| F7 P3: per-request thread and instance-patch seams | Accepted. Name the registered lane's per-request thread; migrate instance-rebound algorithm tests to class-level MaintenanceWorker patches | Verify `_submit_worker_lane` thread creation and port test intent without instance rebinding |

Author fresh-eyes and independent source review resolved a follow-on lifetime
hazard in F1.
`BrokerSession.queue` strongly retains minted facades; `MonitorStore(session=)`
mints one even when the worker convenience connection no longer uses a queue.
Also, custom summary evaluation can read the services registry without enabling
deletion. The selected existing-API construction above covers both facades,
preserves same-key reuse and needs no broker/store source changes. This is an
ownership correction to the proposal, not a new cleanup policy.

Baseline evidence: a local real-TaskMonitor probe forced clone construction to
raise, then applied each returned diagnostic through the existing merge. It
printed `{'failed_cycle_external_totals': [3, 6, 12]}`. Independent real-SQLite
probes confirmed same-thread session close recycles the owner core, open
same-key operations refuse that close, session-minted closed facades remain
retained, and directly minted persistent facades can close under an open owner
connection without recycling it and become collectible afterward. These
probes justify the plan revision; they do not replace the implementation tests.

Scoped revision review: both the native reviewer and Claude Code returned
**no blocker** on F1–F7 and the revised spec delta. Neither reviewer edited
files. The native review included the real SQLite lifetime probes above; the
different-family review independently traced the ownership and merge code.

| Follow-up finding | Disposition |
| --- | --- |
| R1 nit: dense IMPL.11 paragraph | Accepted editorial split into queued, synchronous and common cleanup paragraphs; contract unchanged |
| R2 low: name the exact context accessor | Accepted; synchronous adapter derives detached context from `_monitor_context()` / `_task_context()` while preserving broker identity, also named in slice 3 |
| R3 information: second reviewer did not re-verify the latent-risk mitigation | No change needed; author inspected `_process_reactor_turn`'s cleanup-in-flight early return. No live-overwrite defect is claimed |

Author final pass: the two clarifications do not change the reviewed ownership
design. All accepted F1–F7 findings have explicit dispositions and firing-test
requirements. This review covers the revised plan, not runtime readiness.

Planning evidence: plan-metadata checks first failed on the missing index entry,
then passed after index registration. Final planning selection
`python -m pytest -n 0 tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q`
passed all six tests. Relative plan links, baseline spec objects, reciprocal
backlinks, and `git diff --check` passed. These are documentation checks only;
implementation gates above remain required when the runtime work is performed.

### Implementation execution, 2026-09-22

Slices 2–4 introduced normally constructed `MaintenanceWorker` and detached
`MaintenanceInputs`, moved the coherent maintenance algorithm closure, and
replaced copied diagnostics with optional typed groups. TaskMonitor captures
requests and applies produced results; the existing ServiceTask lanes, threads,
request identity checks and result wakeups remain the transport and lifecycle
owners. Synchronous custom collation lends its reactor session while owning
temporary queue/store/sink facades. The clone, finalizer, snapshot inventory and
reactor store were removed. Only three clone inventory tests were retired;
behavioral tests were migrated to the class-level MaintenanceWorker seam.

The new `tests/tasks/test_maintenance_worker.py` has 21 firing cases covering
the construction-failure counter regression, detached queued inputs, diagnostic
group updates across six mode/cadence combinations, cold PONG, session/sink
acquisition failures, synchronous borrowed-session/core lifetime, temporary
facade collection, partial store initialization, fatal cleanup and continued
custom scanning after collation failures. Existing physical PostgreSQL
connection tests drove the cached-wrapper adjustment recorded in the deviation
log. `tests/core/test_service_convergence.py` follows its moved private method;
its registry semantics are unchanged.

Native independent code review found three defects in the initial extraction.
Each received a firing red test before the fix, then a green regression and a
scoped second review:

| Finding | Correction and verification |
| --- | --- |
| Unexpected synchronous body errors were lost when a non-null diagnostic record existed | Apply permitted diagnostic groups, then record the typed body error and unavailable store status; pre-body and post-body failure cases preserve custom scanning. Scoped second review: PASS |
| Fatal body unwind could lose ordinary cleanup errors or notes attached to a fatal cleanup error | Preserve the original fatal exception identity and attach all secondary cleanup evidence. Real-resource cleanup regression and scoped second review: PASS |
| Store ownership began after schema/checkpoint initialization, leaving fatal setup and close failures outside the common cleanup gate | Record the acquired store immediately, then initialize it; the enclosing scope owns every close. Fatal setup and ordinary setup/close-failure regressions and scoped second review: PASS |

Final reconciliation updates all three spec implementation mappings and the
plan backlinks, CHANGELOG, shared-backend test classification and the existing
Ruff suppression inventory. No SimpleBroker, MonitorStore, BaseTask or
ServiceTask implementation change was required.

The initial full default test run produced 5,219 passes, 29 skips and one
test-audit failure because the new module was missing from `_SHARED_MODULES`.
Adding its shared-backend classification corrected the audit. Plan metadata,
spec hygiene and test classification then passed all 12 selected tests.

Targeted PostgreSQL verification used `bin/pytest-pg --fast` in three groups:
`tests/tasks/test_task_monitor.py` (184 passed),
`tests/tasks/test_service_task.py tests/core/test_monitor_store.py tests/core/test_monitor_external_log.py`
(169 passed, one SQLite-specific collation test skipped), and
`tests/tasks/test_maintenance_worker.py tests/core/test_service_convergence.py`
(61 passed). Together these are 414 passes and one expected skip. No affected
boundary depends on a slow-marked test omitted by this selection.

The final default `./.venv/bin/python -m pytest` run passed 5,220 tests with
29 expected backend-specific skips in 166.26 seconds. Full repository mypy
passed across 436 source files; `ruff check .`, `ruff format --check .`
(761 formatted files), suppression-index `--check`, and `git diff --check`
passed. Suppression policy/index coverage passed all 82 selected cases.
After final documentation reconciliation, `python -m pytest tests/specs -q`
passed all 164 selected cases and the full mypy command passed again.

Final independent read-only Claude Code implementation review: **PASS**.
The reviewer traced construction, input aliasing, diagnostic groups, cumulative
counters, cadence, cleanup chaining, synchronous borrowing and the three fixed
failure paths, and found no P1/P2 or introduced correctness defects.

| Final review observation | Disposition |
| --- | --- |
| N1 P3: `_run_monitor_cycle` contains an unreachable second non-custom branch | Confirmed unchanged at the baseline (lines 5030–5039); deferred as unrelated existing scaffolding, with no effect on this change |
| N2 P3 informational: unexpected synchronous body errors apply partial observations, then report unavailable store status | Matches the reviewed contract and the new before/after-body failure tests; no change required |

Author final fresh-eyes pass verified that removed methods are the clone/reset
helpers or renamed worker entry points, and that only the three inventory tests
were retired. No inherited watcher/task resource path remains in the worker.
The final cleanup-construction catch was simplified to construct an omitted
store observation directly; both entry-point construction-failure tests passed
again. Runtime/spec work passed the extraction gates; the consolidation and
final commit verification are recorded below.

Final spec snapshots after implementation-mapping reconciliation:

| Spec | SHA256 |
| --- | --- |
| `01-Core_Components.md` | `163de792897c7bef39d3e38b3a61f99ad756efbc7b3f2dcba1e2db4fab259a4b` |
| `05-Message_Flow_and_State.md` | `b760d5b54229d1083cd1d71f1fc4bf5d8be4bbbb232fcd70864a7ec479613461` |
| `07-System_Invariants.md` | `101d671a560af0da7be5625c192e1987692a809cf9097fc9c929c867f5c2d3fa` |

### Consolidation revision, 2026-09-22

At the user's request, slice 6 adds consolidation of existing functionality
without changing the architecture or promoted contract. Author fresh-eyes and
a scoped independent native review found no ambiguity or blocker (PASS).
Plan metadata and spec hygiene passed all six tests; whitespace checks passed.
This review covered only the plan revision. The following execution record
contains consolidation verification; the earlier extraction results do not
cover it.

### Slice 6 execution, 2026-09-22

Baseline characterization passed 228 tests with two PostgreSQL-only skips
across the monitor, worker-boundary and external-log files. The consolidation
uses the five existing frozen diagnostic groups as each object's state, with
defaults defined once. Capture returns produced groups, detaching nested mutable
scan/progress values; apply replaces ordinary groups, while cleanup handlers
retain partial updates and scheduling/counter ownership. A single pure helper
assembles shared external status. No runtime AST tooling, field registry,
compatibility properties or new state framework was introduced.

Tests now also exercise independent default containers, detached nested result
values, partial cleanup retention and owner/worker differences when the sink is
disabled. Two findings were verified with red/green evidence:

| Finding | Correction and proof |
| --- | --- |
| Group replacement captured collation state before the summary-emission helper updated other counters | Evaluate the helper first, then replace the emitted-count field on the latest group. A real full-cycle stale-service case records the produced diagnostic group; the old-expression mutation loses its suspect classification and fails, while the correction passes. An initial assertion against the final owner value was rejected because later cycles can legitimately reset that value |
| Synchronous result merging added snapshot/health notification edges | Preserve shared cache/counter/deferred merging with an explicit notification flag. Four real-broker cases cover sync/queued success/failure: both sync cases were red before correction and all four pass afterward. The reviewer’s claim of next-cycle-only visibility was too strong: normal custom scanning can publish a task-state snapshot later in the same turn through its existing activity edge |

Slice 6 verification: the default suite passed 5,228 tests with 29 expected
skips; the six-file PostgreSQL selection passed 422 tests with one
SQLite-specific skip. Full mypy passed across 436 source files, Ruff lint
passed, and all three slice-owned Python files passed formatting checks.
A concurrent workspace rename changed TID-mapping helper names to TID-state
names, including `_register_tid_state`; it was preserved. The subsequent
monitor/worker/spec selection passed 375 tests with two PostgreSQL-only skips
against that current code. The repository-wide formatting check identified two
concurrently edited files outside this slice (`tests/commands/test_tid_mapping_contracts.py`
and `tests/test_harness_registration.py`); this is distinct from the passing
format check on the consolidation files.

Final scoped independent Claude Code review of slice 6: **PASS**, no actionable
P1/P2 findings. The reviewer traced all 43 fields, result-group ownership,
partial cleanup updates, nested mutable detachment, replacement evaluation
order, disabled-sink differences and both notification adapters. It confirmed
both fixes and their firing tests. One optional P3 suggestion to batch further
adjacent single-field replacements was considered and left out: the current
explicit updates are correct, and no additional consolidation is required to
remove the duplicated schema or shared status behavior. Author final review
also found no remaining flat diagnostic cache or parallel status assembler.
Final combined verification and commit evidence follow below.

The final repository-wide Ruff lint and format checks both passed (761 files
formatted); the earlier two formatting findings were resolved by the concurrent
work. The final metadata/spec-hygiene selection passed all six tests and
`git diff --check` passed. Spec snapshots after slice 6 mapping reconciliation
and the concurrent TID-state naming update:

| Spec | SHA256 |
| --- | --- |
| `01-Core_Components.md` | `03d0295396543ade6583c2d20849a9e6c5b2f814b253d0e99a6148da11a5b718` |
| `05-Message_Flow_and_State.md` | `f6d32092acc196b670005306929659a3650049b7c3644f8cfda854c2c385d03b` |
| `07-System_Invariants.md` | `c91fa8da969269560617a691126b5258e17050079d4224900accede98df009c1` |

### Combined closeout verification, 2026-09-22

The user included the concurrent TID-state naming cleanup in the final scope.
The combined default suite passed 5,228 tests with 29 expected skips in
165.48 seconds. Full mypy passed across 436 source files, full Ruff lint and
format checks passed (761 files), and suppression-index and whitespace checks
passed. The six plan-metadata/spec-hygiene tests passed after the documentation
cleanup. The earlier six-file PostgreSQL run passed 422 tests with one expected
SQLite-specific skip.

Independent review of the naming cleanup found no runtime blocker: definitions,
imports, callers and keyword arguments agree. The renamed malformed-row reason
does not change consumer decisions. A stale spec reference to a retired equality
helper was replaced with the actual task-state publication owner. Historical
plans' helper references use the current names for navigation; their recorded
baseline commits remain the source for original spellings. Historical queue
names were preserved rather than inventing a `tid_states` queue.

At the user's direction, obsolete namespace upgrade and rollback steps were
removed from the README, release notes and namespace plan. The concise history
of the queue rename and its original cutover rationale remain because they
explain older versions. No queue name or stored payload changes in this cleanup.

### Closure, 2026-09-22

Implementation commit `e7023a8a407d34d92f1462f5e533bde7c5966c68` contains the
MaintenanceWorker extraction, diagnostic consolidation, task-state helper naming
cleanup, tests, spec promotion and documentation reconciliation. Its existence
and contents were verified with git before marking this plan and its index
completed. All requested outcomes and review dispositions are complete.
