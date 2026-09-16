# Explicit Broker Session Lifetimes

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2.1], [CC-2.3]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.4], [SB-0.4a]; docs/specifications/07-System_Invariants.md [IMPL.8], [IMPL.9], [IMPL.10], [IMPL.11], [QUEUE.8]; docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-2]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-12.1], [DJ-12.2]
Superseded by: none

Class: 5. Planned session ownership contracts and an additive context factory
cross task, watcher, command, and thread-cleanup boundaries. Hardening applies.

## Goal

Adopt SimpleBroker 8.3.0 and simplebroker-pg 4.3.0 through shared, automatic
execution scopes. Ordinary BaseTask subclasses, not just services, should get
correct persistent connection reuse and owner-thread cleanup without each
subclass implementing it. Monitor maintenance workers retain their declared
database authority but get their own automatic scopes. Commands and observers
follow the same owner/borrower rule. This is a plan, not authorization to publish
or to change the live specs before review and approval.

## Spec Baseline

- Weft `736f37d4`: the source specs named above and implementation inspected.
- Upstream `d5d5c81`, tag `v8.3.0`: `simplebroker/session.py`,
  `simplebroker/_broker_session.py`, `simplebroker/watcher.py`,
  `docs/specs/16-python-library-api.md` [SB-API-3], [SB-API-6], [SB-API-11],
  and `docs/guides/python.md` (embedding and connection lifetime).
- Plan type: implementation with spec revision. Promotion baseline: slices 1-2
  promote their core/floor text from Weft `736f37d4`; Monitor store,
  command/observer, and Django transport deltas remain unpromoted until their
  gated slices are approved.

The earlier [connection reuse plan](./2026-09-14-bounded-registry-connection-reuse-plan.md)
records the landed borrowing fixes. Preserve those fixes. This plan addresses
its remaining lifetime issue with the released upstream API; it does not
supersede the earlier evidence or reinterpret it as unimplemented work.

Required implementation guidance: `AGENTS.md`,
`docs/agent-context/runbooks/runtime-and-context-patterns.md`,
`docs/agent-context/runbooks/writing-plans.md`,
`docs/agent-context/runbooks/hardening-plans.md`, and
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.
Before editing, explain which handle owns leases, which thread owns a warmed
core, which scope owns an active operation, and why foreign stop cannot replace
driver-thread exit. Trace ordinary Consumer construction/run/cleanup as well as
both Monitor maintenance entry points; service-only evidence is insufficient.

## Evidence And Interpretation

On the published 8.3.0 wheel, five sequential workers with a surviving keeper:

| Pattern | Retained worker cores before keeper close |
| --- | --- |
| Persistent Queue, only `Queue.close()` | 5 |
| Worker-local `with BrokerSession.connect(...)` | 0 |
| Same context, body raises | 0 |
| Worker creates session; main thread closes it after join | 5 |

A Consumer driven by `process_once()` on a thread, then stopped from the main
thread, can be CLOSED while its former driver's core is still retained. A
driver-local session context releases that core even if foreign stop already
finalized the task. This is why simply putting `session.close()` in the current
task finalizer is insufficient.

8.3.0 supplies the explicit cleanup boundary, not a dead-thread reaper.
Queue lease ownership, thread-cache lifetime, and transaction lifetime are
different. Handles with identical resolved target/config share a per-thread
core; session handles do not create isolated pools. Context exit recycles the
calling thread, including sibling handles with that same key.

Compatibility probe on unchanged Weft: 94 passed, 22 failed, one PG-only skip.
Monitor clones reject upstream's new `_owns_queue` field. A temporary in-memory
classification experiment yielded 114 passed, two PG-only skips, and one test
still checking its separately imported original field inventory. These are
diagnostic results, not a migration sign-off. Evidence logs from the evaluation:
`/tmp/weft-sb830-compatibility.log` and `/tmp/weft-sb830-clone-diagnostic.log`.
Recreate the probes as repository tests; do not depend on temporary files.

## Design Decisions

### 1. Use SimpleBroker's Session, Not A Weft Session Framework

Propose `WeftContext.session() -> BrokerSession` as a stateless factory using
the context's exact `broker_target` and immutable `broker_config`. The normal
form is `with context.session() as session:`. The factory does not cache a
live session on the context or WeftClient. This is an additive public method
on an already exported context and requires approval with the spec delta.

Keep `context.queue()` and `context.broker()` semantics unchanged. They remain
useful for caller-owned standalone queues and intentionally independent broker
scopes. Do not hide a global session, introduce ambient thread-local Weft
configuration, create a QueueFactory, or wrap the upstream session protocol.

### 2. Make BaseTask The Default Lifetime Owner

Add a task-owned session handle for its fixed queue inventory. Mint those queues
through `session.queue(name)`. The shared cleanup owner explicitly closes those
queues before session exit, which is idempotent over already-closed queues. The allocation
owner in MultiQueueWatcher and BaseTask must be the same, not two competing
queue registries. Preserve the existing cache for stable logical queue lookup.
Provide one protected connection scope on the existing owner so cross-queue
helpers can use `session.connection()` without selecting an arbitrary inbox.
Existing helpers that already accept a borrowed broker keep that contract.

Concrete allocation sequence: MultiQueueWatcher resolves the target and config
at its existing point, then creates the retained session before allocating its
primary queue or calling the upstream watcher constructor. For persistent
watchers it mints the initial queue inventory from this session. BaseTask uses
that inherited handle for its additional fixed queues; it does not create a
second inventory session or duplicate target resolution. The protected resource
release helper owns the ordered queue-close/session-close sequence. BaseTask
invokes it after task-specific cleanup, waiter/iterator shutdown, and final
writes. Enumerate the unique owned handles from Weft's existing inventory/cache
and watcher registries, not BrokerSession's private queue list. Attempt every
minted and separately owned dynamic queue close, then attempt inventory-session
close even if an earlier close failed. Remove per-queue core recycling, not
explicit queue lease release. Standalone watcher teardown uses the same helper.
The cache is lookup-only, not a competing cleanup owner. The
nonpersistent watcher path retains explicit Queue ownership without minting
persistent queues. This is reuse of the existing allocation owner, not a new
factory or public constructor parameter.

Queue.close() has no active-operation refusal guard: while the inventory session
is still leased it can release each queue's lease even if session.close() will
refuse. It can still raise a waiter/backend cleanup error; record that failure
and attempt the remaining handles and session. Normal session exit may revisit
the closed minted queues safely. If all queue closes succeed but inventory close
is refused, those queue leases are released; the inventory lease and unrecycled
cache may remain. Do not describe that outcome as complete resource release.

Keep the existing once-only task finalization contract. CLOSED means the reactor
is no longer driveable and its finalization phases have been attempted, not that
all broker resources were successfully released. The finalizer retains cleanup
failures in _cleanup_errors and warnings, including an inventory-close refusal;
repeated stop/cleanup on CLOSED does not retry partial finalization. A refused
inventory session can remain leased while the task retains it, until eventual
release/collection. That fallback is not an owner-thread cleanup guarantee.
The independent driver guard still attempts to recycle its own thread at scope
exit, but cannot release the refused inventory handle or recycle a foreign
stopping thread's cache. Do not add a new lifecycle state, automatic retry, or
private-refusal recovery mechanism in this slice. Teardown-order violations
remain observable cleanup failures with this explicitly limited fallback.

Separate the task's queue-inventory lifetime from the executing thread's cache
scope. Propose one BaseTask `drive_scope()` context manager, reused by
`run_until_stopped()` and by manual drivers. It claims/verifies the existing
drive owner and holds an independent BrokerSession context over the whole
driving lifetime. Its exit requests existing task cleanup and then always
attempts its own session exit on the driver thread, even if task state is already
CLOSED. An ordinary close failure is a cleanup failure, not successful release;
the exception-priority rules below distinguish it from BaseException propagation.
Do not store this guard as the task handle that a foreign finalizer can close.
Upstream recycle_thread() is a no-op after its handle has been released; using
that released task handle in the driver finally block would reintroduce the leak.

`run()`, `run_forever()`, `run_in_thread()`, and the spawned launcher must reach
this same boundary, not implement independent cleanup loops. A scope does not
start a background thread, open a transaction, change task scheduling, or reset
drive ownership. Keep a single active scope per task; reject nested/reentrant or
foreign scope entry before broker work, just as existing drive entry does.
Retain a strong Thread identity. Scope bookkeeping must not prevent its own
finalizer from running, and must not bypass existing active turn/wait protections.

Manual usage becomes:

```python
with task.drive_scope():
    while application_needs_to_drive(task):  # Includes stop/terminal handling.
        task.process_once()
        task.wait_for_activity(timeout=task.next_wait_timeout())
```

Do not repurpose `with task:`: inherited context-manager behavior starts a
background drive. Bare `process_once()` remains compatible; it cannot promise
automatic release after an arbitrary caller abandons its thread. Document the
scoped pattern for a cleanup guarantee. No recycle on every turn or wait.
The scope is a lifetime guard, not permission to keep driving a closed task.
Manual drivers retain the existing stop/terminal protocol and must handle a
foreign stop racing the next drive call; do not retry a closed-reactor error.

Session close refusal is thread-and-key-wide, not handle-local: an active
operation or suspended queue iterator owned by unrelated code on the same
thread and process-session key also prevents close. Do not import the private
refusal exception, match its class name/message, inspect operation-depth internals,
or special-case RuntimeError as proof of refusal. Treat every close failure as
a cleanup failure through the appropriate boundary's error-recording/propagation
path. For ordinary Exception failures, preserve the body failure as primary when
both fail. A public catchable refusal type
would require a separate SimpleBroker API request, not a Weft workaround.

Do not wrap driver-session context exit in the task finalizer's catch-all.
Upstream __exit__ catches ordinary Exception cleanup failures and attaches them
to an active body exception. A close failure outside Exception, such as
KeyboardInterrupt or SystemExit, propagates from the driver scope even if the
body also failed. It must not be converted into only an entry in _cleanup_errors.
Task finalization's existing catch-all for BaseException remains unchanged;
that is a different cleanup boundary, not a precedent for suppressing interrupts
from the independent driver scope.

The harness and manual-driver owners must register observers for deterministic
teardown and close their active iterators/connection scopes before driver-scope
exit, including observers of sibling tasks on the same key. Quiesce all such
operations before closing any affected session; do not assume independently
owned observers are independent caches. Preserve observer lifetime across
ordinary drive-until calls, but end it before the whole driving lifetime ends.
Manual process_once() can itself finalize a STOP_REQUESTED task. Therefore end
observer read operations before drive/stop calls that can finalize, not merely
at outer scope exit. Keep the logical observer and its cursor alive between
reads; ending an operation must not recycle its persistent session per turn.
Use explicit ownership/ExitStack ordering, not GC discovery or per-turn recycling.
Weft must not close an unrelated caller's iterator behind its back. Caller-owned
operations left active cause visible cleanup failure, never a false success or
an assertion that a weakref finalizer supplied owner-thread cleanup.

Construction also does broker work. Use ExitStack in the queue allocation owner
to unwind partial allocation before ownership transfers. Add one protected
BaseTask initialization scope for broker-using initialization blocks. On success,
call recycle_thread() on the still-live inventory session; no third session
handle is needed. On failure, unwind acquired resources with ExitStack and then
attempt inventory-session close, which also recycles the construction thread.
Use a low-level partial-initialization-safe path, not virtual task finalization;
attempt remaining cleanup even after one callback fails and preserve failure
priority. Register acquired
resources immediately, including acquired workers and sinks, rather than
depending on all task fields being present.
BaseTask uses it around its eager initialization effects. First-party derived
constructors use that same helper around post-super setup that can fail or do
broker work, including Consumer's pipeline-start publication. Pure assignments
need no additional guard. Test failure after super explicitly.

Unlike close(), recycle_thread() can defer disposal until an outstanding
same-key operation ends. Finish initialization-owned operations before recycling;
do not treat the call returning as proof of immediate physical release if a
caller-owned operation remains active. That caller retains responsibility for
ending its operation on the construction thread. Record recycle failures as
cleanup failures too; do not add a separate refusal-detection path.

This means bounded connection recycling at initialization-layer boundaries,
before the persistent driving lifetime begins. It does not recycle per turn.
Preserve eager lifecycle writes and endpoint claims; their durable failure
semantics remain unchanged. Do not infer that releasing a connection rolls back
earlier committed writes. Construct-on-A/drive-on-B must leave no cache on A.
Launchers retain their existing child-local construction/run boundary. No
metaclass, arbitrary constructor wrapping, new mandatory task factory, or lazy
initialization is proposed. Third-party post-super broker work must use the
documented protected scope; BaseTask cannot catch code that executes after its
own constructor returns. This limited explicit hook is preferable to changing
every downstream constructor's invocation contract.

### 3. Keep Workers Broker-Free By Default

ServiceTask's general thread/result channel stays broker-free. Do not give every
worker a DB session or a broker argument. Manager child launch, Consumer target
execution, and LivenessMonitor probes keep their current authority.

TaskMonitor's two maintenance lanes use one Monitor-owned context helper for
both worker bodies. It creates an isolated clone and a fresh worker session,
binds that session to the clone's queue/store access, and closes resources on
the worker thread before returning its typed result. Never copy the reactor's
session into the clone. Preserve snapshot isolation and explicitly classify
`_owns_queue` and new session fields; do not disable the unknown-field check.
Concretely, add `_owns_queue` to both `_WORKER_SNAPSHOT_EXPECTED_FIELDS` and
`_WORKER_SNAPSHOT_REPLACED_FIELDS`, not either shared-fields set. The clone
allocates and owns its worker queue, so initialize its own `_owns_queue = True`
rather than copy the reactor's borrowed-primary flag. Classify new session and
scope-bookkeeping fields as replaced too: install the fresh worker inventory
session and reset drive-only state. The worker scope remains the final resource
owner; the flag must not introduce a second reactor finalization path. Test that
worker teardown releases the clone's queue/session and leaves reactor resources
usable, including when the reactor's flag is False.

MonitorStore borrows the owner's session or active broker; it does not own
or close it. Prefer one clear borrowed-session parameter for owned task paths,
with an explicit bounded standalone fallback. Retain the existing migration
raw-row independent-connection exception: queue reads cannot run inside a
sidecar transaction on the same core. Each sidecar transaction still ends within
its operation. Custom processors remain broker-free.

Keep one-item maintenance workers in this migration. A long-lived worker is a
separate scheduling decision, not necessary to obtain correct scoped cleanup.

### 4. Watchers Own Membership, Not Every Historical Queue

MultiQueueWatcher must close the queues it creates, including its primary
queue, which upstream treats as borrowed because Weft passes a Queue object.
Preserve serialized stop/run ownership, retry behavior, and waiter replacement.
Use the upstream run/stop cleanup hooks rather than copying its retry loop.
Stop/join owned watcher threads before releasing caller-owned observed queues.
Construction failure, idle stop, run exit, handler failure, and partial native
waiter setup must all release their owned resources.

`session.queue()` retains every minted facade until session destruction.
Use it for BaseTask's fixed inventory, not for unlimited standalone watcher
add/remove history. Dynamic membership may use explicitly owned persistent
Queue leases with the same target/config, while the session owns the run-thread
cache boundary. Remove/close displaced handles at the existing topology commit
point. Do not mutate upstream's private session queue list. Keep the existing
`persistent=False` standalone watcher option; do not silently turn it persistent.

Apply the same distinction to BaseTask._queue callers: task-fixed support routes
are session-owned, while arbitrary child-TID, reply, or discovered routes are
not automatically added to the session's permanent inventory. Audit Manager's
variable-name calls explicitly. Use a short session.connection() loan with a
named broker operation where no facade/cursor is needed; otherwise retain an
explicit facade only for its active child/subscription lifetime and release it
when that ownership ends. Do not simply replace every Queue(...) with
session.queue(...), or introduce an unrelated LRU cache policy.

### 5. Commands Own A Scope; Helpers Borrow

Use existing command operation/observer owners and ExitStacks to enter one
session for each bounded observation/submission/follow lifetime. Queue monitors
and iterators close before the session; the scope must unwind on timeout,
cancellation, generator close, setup failure, and application error. Do not
hold an active connection context across a yield when a short per-turn borrow
within the persistent session suffices. Do not extend transactions across waits.

Treat GeneratorExit as stream-lifecycle control, not an application failure for
session cleanup. Upstream BrokerSession.__exit__ attaches close failures to an
active exception; generator.close() then suppresses GeneratorExit and can hide
that attached failure. Existing stream resource owners must close inner iterators
first and explicitly perform exception-neutral session exit (for example, their
ExitStack.close() in finally). A close failure must reach the synchronous close
caller or async cleanup owner and its diagnostics. Preserve genuine application
failures as primary and record secondary cleanup failures; do not blindly replace
them with an ExitStack close exception. Test closing a suspended stream with an
injected session-close failure, not only normal iterator exhaustion.

Keep explicit broker loans through core helpers; do not open nested session
contexts in every evidence, registry, or pruning helper. Nested scope exit can
recycle the caller's shared cache or reject an outstanding same-key operation.
For one-shot commands, an already bounded `context.broker()` is correct: no
mandatory rewrite merely for spelling consistency. Client and integration
adapters should reach these same command paths, not own hidden global sessions.

Threaded/async adapters must keep iteration and cleanup on the same owner thread.
Django Channels currently advances an iterator with repeated `asyncio.to_thread`
calls, then closes it on the event-loop thread. SSE wraps a synchronous iterator
in StreamingHttpResponse without owning the server's iteration/close thread.
Use one private lifetime bridge shared by these existing transports in
`weft_django/realtime.py`: a per-stream single-worker executor creates, advances,
and closes the broker iterator, with at most one outstanding advance and no
unbounded prefetch. ASGI SSE exposes an async iterator and WSGI SSE a synchronous
iterator; both delegate broker work to that owner. Select the response iterator
for the actual request mode instead of letting Django materialize a synchronous
infinite stream under ASGI. Keep routes, authorization, framing, payloads, and
follow semantics unchanged. The uniform bridge is a judgment call for one
implementation across transports, not a BrokerSession requirement for WSGI.
Direct synchronous WSGI iteration/closure on one handler thread can satisfy
the same ownership contract without another worker. The proposed common bridge
costs an additional worker per WSGI stream; retain it only if the slice-4 review
accepts that cost over a separately tested direct WSGI path. ASGI/Channels must
resolve thread affinity regardless of that choice. No bridge work starts before
the slice-2 gate and the remaining-scope decision below.
Do not use a global single-thread executor that serializes independent clients.
On disconnect, set the existing cancellation event, stop scheduling advances,
and queue final closure behind any in-flight advance. Preserve an owned cleanup
future through async cancellation; do not call generator.close concurrently or
shut down the executor before closure runs. Keep blocking joins off the event
loop. A disconnect deadline is not proof the worker stopped: retain ownership
and diagnostics until it unwinds. Use an async context/cleanup owner in this
adapter, not a new broker framework. Close before first advance must allocate
nothing or unwind setup; duplicate close is idempotent. SSE/realtime wrappers
must explicitly propagate close to their inner streams. Document owner-thread
iteration/close
for synchronous broker-backed streams; do not promise arbitrary thread hopping.

## Repository-Wide Ownership Inventory

| Surface / files | Planned action and boundary |
| --- | --- |
| `weft/context.py`, `weft/client/_client.py` | Context session factory only; client stays a context holder, no permanently open connection. |
| `weft/core/tasks/base.py`, `multiqueue_watcher.py` | Fixed task queue ownership, shared driver scope, constructor unwind, standalone watcher membership and run/stop cleanup. |
| `weft/core/launcher.py`, `weft/manager_process.py`, `weft/manager_detached_launcher.py` | Construct child-local resources under a real execution boundary; pass only target/config across spawn, not a live session. |
| `weft/core/tasks/consumer.py`, `interactive.py`, `observer.py`, `monitor.py`, `pipeline.py`, `heartbeat.py`, `liveness_monitor.py`, `weft/core/manager.py` | Inherit common lifetime; audit extra queues, synchronous entry points, and cleanup hooks. No service-only implementation. |
| `weft/core/tasks/service.py` | Keep Python input/result queues and broker-free default; no indiscriminate session-per-worker option. |
| `weft/core/monitor/task_monitor.py`, `store.py`, `task_log_scanner.py`, `runtime.py`, `policies/` | One automatic maintenance scope; borrowed store access; synchronous scan paths get a bounded owner. |
| `weft/core/queue_wait.py`, `manager_runtime.py`, `queue_window.py`, `task_state.py`, `control_probe.py`, `heartbeat.py`, `endpoints.py`, `spawn_requests.py`, `pruning/` | Borrow on task/command paths; clearly owned standalone fallback; watcher-before-queue-before-session teardown. |
| `weft/commands/tasks.py`, `_spawn_submission.py`, `_result_wait.py`, `result.py`, `events.py`, `run.py`, `interactive.py`, `system.py` | Session spans the actual command/observer lifetime, including dynamically rebound watches and interactive shutdown. |
| `weft/commands/queue.py`, `dump.py`, `load.py`, `tidy.py`, CLI adapters | Audit stream and watch owners; retain bounded one-shot APIs, no duplicate CLI-only ownership. |
| `weft/commands/task_monitor.py`, `bin/launch_manager.py::_wait_for_registry` | Bound synchronous Monitor scans and registry polling; the launch helper's nonpersistent polling queue needs explicit ownership. |
| `weft/core/runners/host.py`, `subprocess_runner.py`, `serve_log.py` | Distinguish in-memory queues and subprocess sessions from broker use; preserve broker-free worker paths and optional log fallback. |
| `extensions/`, `integrations/weft_django/` | Runners stay broker-free; Channels stream iteration/close has one worker owner; SSE and realtime wrappers propagate close. No package-specific broker pool. |
| `tests/helpers/weft_harness.py`, `reactor_driver.py`, `long_session_utils.py`, domain manual drivers | Harness whole-driver scope, not one scope per drive-until observation; preserve production behavior under test. Replace reliance on GC-discovered Queue cleanup, which cannot account for session leases. |

Specific operation owners to reuse: result materialization/collection ExitStacks;
control observation's `_ControlSurfaceResources`; event route rebinding;
`_LiveRunSession` and `_InteractiveRunLifecycle`; manager start/settlement/stop
and spawn reconciliation. Keep sessions outside replaceable subscription groups.
`_LiveRunSession` should retain one input facade, not mint one per input message.
Queue watch needs distinct read/watch cursor facades. Include system public-status
streams and interactive prompt/watcher threads, not only task event generators.
QueueChangeMonitor borrows observed queues and owns its waiters; it must not
close the caller's session. A timed-out join cannot release resources still in use.

Inventory is about ownership, not a textual replacement list. Before editing,
repeat the Queue/open_broker/get_connection/cleanup_connections search across
Weft, extensions, integrations, and bin. Record every concrete owner as migrated,
borrowed, independent bounded scope, or non-broker false positive in slice review.
Do not turn this temporary inventory into a permanent source-pattern test.

## Proposed Spec Delta

Promotion strategy A: exact text below goes into existing specs before code
implements it. Add this plan's backlinks at promotion. Add reciprocal owning-code
references and implementation mappings with the code slice, not speculative
claims now. Existing requirements not replaced below remain unchanged.

### [SB-0.1] in 04-SimpleBroker_Integration.md

Replace the dependency-floor and provided-features paragraph with:

> Weft requires SimpleBroker 8.3.0 or newer. Installations using the optional
> PostgreSQL backend require simplebroker-pg 4.3.0 or newer. These coordinated
> floors provide backend API v9, ascending public-message-ID default selection,
> surrogate-free SQL schema v6, bounded dump watermarks, immutable
> invocation/handle configuration snapshots, typed queue result overloads,
> public closeable queue iterator types, ID-cursor live peek pagination,
> synchronized watcher lifecycle and owned-versus-borrowed queue cleanup, and
> the public BrokerSession connect/queue/connection/recycle_thread/close and
> context-manager lifetime contract used by Weft.

Keep the existing v7-to-v8 cold-cutover requirements unchanged; this 8.2-to-8.3
adoption does not waive them for installations coming from older schemas.

### [SB-0.4] in 04-SimpleBroker_Integration.md

Replace the first "Current behavior" bullet with:

> Tasks use persistent SimpleBroker BrokerSession ownership by default. Shared
> task infrastructure owns fixed queue leases and the executing thread's session
> scope; subclasses and helpers borrow that ownership. A complete drive or
> maintenance-worker scope exits on the thread that used the cached core, on
> both normal and exceptional exit. Connection reuse spans turns, not SQL
> transactions: transactions end before waits, yields, external I/O, and thread
> handoff. Cleanup releases waiters and iterators before final queue writes and
> session exit. This ordering includes caller-owned observers with active
> operations on the same thread and process-session key, not just the task's
> own handles. Close failures follow the owning boundary's recording or
> propagation contract; Weft does not depend on a private upstream refusal type
> or report a refused close as success.
> Dynamic queue discovery and watcher membership must not retain
> every historical queue handle. Independent bounded connections remain valid
> where no owner exists or a documented operation cannot use the owned session.

### [CC-2.2.1] in 01-Core_Components.md

Add after the drive ownership paragraph:

> BaseTask owns one shared drive_scope() implementation. Normal run loops enter
> it automatically; manual driving may use it around the complete driver
> lifetime without starting a background thread. It preserves the existing
> driving-thread identity and performs caller-thread cache cleanup on exit once
> active same-key operations have ended, even if a foreign stop has already
> finalized an idle task. Cleanup failures remain observable. It does not grant
> worker threads broker authority or change per-turn control policy. Bare
> process_once() remains supported, but thread abandonment without an owner-thread
> lifetime boundary cannot guarantee cache release while sibling sessions live.
> Existing task context-manager background-start behavior remains unchanged.

> Fixed queue inventory and drive-thread cleanup have separate session handles
> sharing one process-session key, not separate pools. Releasing the inventory
> cannot release the still-active driver's independent cleanup guard.

> Base-resource cleanup attempts each owned queue close before inventory-session
> close, after ending owned operations and completing final writes. It attempts
> remaining cleanup despite individual failures. Already-closed minted queues
> can be closed again by the session. If inventory close is refused, successful
> queue closes have still released their leases; the inventory lease and cached
> resources may remain. CLOSED means reactor driving has ended and once-only
> finalization was attempted, not that every resource was released. Cleanup
> failures remain recorded/logged, and stop/cleanup on CLOSED does not retry
> partial finalization. Collection is not an owner-thread cleanup guarantee.
> The independent driver guard releases only its own handle and caller-thread
> cache; it cannot repair a refused foreign-thread inventory close.

> Driver-session exit follows SimpleBroker's exception priority: ordinary
> cleanup exceptions propagate when no body failure exists and remain secondary
> to a body failure otherwise. Cleanup BaseExceptions outside Exception propagate
> from the driver scope, including when its body also failed. This does not
> change the task finalizer's existing BaseException-recording convention.

> Shared initialization scopes recycle construction-thread caches through the
> still-live inventory session without another session handle or deferring eager
> lifecycle publication. Finish owned operations before recycling; caller-owned
> active operations can defer disposal until they end. Partial initialization
> unwinds acquired resources and closes the inventory session without invoking
> full task finalization. First-party constructors
> cover broker-using or fallible post-super setup; downstream constructors use
> the protected initialization scope for equivalent work.

### [CC-2.1] and [IMPL.11]

Add to 01-Core_Components.md [CC-2.1]:

> MultiQueueWatcher owns all queue leases it constructs. Upstream treating a
> passed primary Queue as borrowed does not transfer Weft's ownership. Normal
> drive exit releases the drive thread's cache; idle stop closes owned leases
> without claiming cleanup of another thread. Dynamic remove releases displaced
> resources at the existing serialized topology boundary.

Add to 07-System_Invariants.md [IMPL.11]:

> TaskMonitor maintenance workers use an automatic worker-local BrokerSession
> scope shared by both maintenance entry points. The worker session is never
> borrowed from the reactor or copied with its snapshot. Worker store access
> borrows that session; result publication occurs after local cleanup. The
> clone replaces queue ownership and session state with its own, never shared
> reactor values. The ordinary broker-free worker contract is unchanged.

### [SB-0.4a] and [PY-1]

Replace the task-owned-store opening sentences in 04-SimpleBroker_Integration.md:

> Task-owned stores borrow the owner's BrokerSession and enter a short sidecar
> context for each operation. Store.close() does not close that borrowed owner.
> Standalone store operations retain a bounded independent fallback. Migration
> raw-row checks retain their separate-connection exception inside a sidecar
> transaction.

Add to 14-Python_API_Surfaces.md [PY-1]:

> WeftContext.session() returns a new SimpleBroker BrokerSession bound to the
> context's resolved target and Config. It is intended for a with block entered
> and exited by the executing thread. WeftContext and WeftClient do not cache a
> live session. Existing queue() and broker() ownership and defaults are unchanged.

Add to 14-Python_API_Surfaces.md [PY-2] and the transport ownership paragraphs in
13C-Using_Weft_With_Django.md [DJ-12.1], [DJ-12.2]:

> Broker-backed stream advancement and final closure share an executing-thread
> owner. Transport adapters preserve that ownership across async delivery and
> cancellation; they do not close an iterator concurrently with an active next.
> Django realtime transports preserve one serialized owner for broker iterator
> creation, advancement, and closure, including SSE response closure. Async
> transports use an owner worker; a direct synchronous WSGI path is valid when
> iteration and closure retain the same handler-thread owner.
> ASGI SSE uses an asynchronous response iterator; WSGI SSE uses a synchronous
> one. Wrappers propagate close to their inner stream. Transport disconnect requests
> cancellation but does not establish that a still-running backend call ended.
> GeneratorExit is lifecycle control for cleanup: session cleanup failure must
> remain observable to the stream's close owner rather than disappear with
> generator.close(). Genuine application failures retain priority, with secondary
> cleanup diagnostics.

## Invariants, Failure Priority, And Non-Goals

- Preserve task TIDs, immutable spec/io, forward-only state, queue names,
  reservation policy, lifecycle/result order, cleanup selection, and payloads.
- Preserve pure-reactor effects in Manager, Consumer, Heartbeat, and Liveness
  Monitor. Only the existing TaskMonitor maintenance exception owns worker DB work.
- Session.connect/close is not a transaction, lock, dead-thread reaper, or
  cross-thread resource handoff. Do not inspect upstream private core registries
  in production or manufacture thread-finalizer callbacks.
- Body failures stay primary if session cleanup raises an ordinary Exception.
  Attempt remaining owned cleanup and retain secondary diagnostics. A cleanup
  BaseException outside Exception propagates from the independent driver scope,
  matching upstream; do not route it through task finalization's catch-all.
  Cleanup failure must not be converted into a successful worker result or
  silently discarded. CLOSED is a reactor lifecycle outcome, not cleanup success.
- Session.close rejects active same-key caller-thread operations. Close iterators
  and exit connection/sidecar scopes in reverse order before session exit,
  including independently owned same-key observers. No private-exception
  detection or successful-cleanup claim after failure.
- No per-turn recycling, timeout inflation, worker-count reduction, new global
  singleton, new dependency beyond upgrading the existing coordinated pair,
  pure-reactor rewrite, or conversion to a long-lived Monitor worker in this plan.
- Do not alter SIGINT deferral, waiter replacement, native LISTEN ownership,
  fork/spawn policy, cancellation wiring, or cleanup deadlines incidentally.
  Stop remains a request with bounded waits, not proof an OS/backend operation
  has been interrupted. Never close worker resources from the stopping thread.
- Queue reuse after close can acquire resources again upstream. Weft lifecycle
  guards, not session closure alone, must prevent stopped task work from restarting.

## Rollout And Rollback

Upgrade existing dependency floors together: SimpleBroker >=8.3.0 and
simplebroker-pg >=4.3.0 in root extras and any actual manifests that declare
them; refresh the lock with the repo toolchain. Do not publish a dependency-only
bump before Monitor compatibility and owner cleanup pass. No queue/schema/wire
change is intended; processes on prior Weft versions can coexist on the same
target under the existing concurrency contracts.
Backend plugin API v9 is an in-process compatibility change: all installed
backend plugins must match it. Mixed-process storage compatibility does not
permit loading a v8 backend plugin into the upgraded process.

Land reviewed slices in dependency order. Roll back the session integration,
version floors, and lock together to the recorded baseline if needed; preserve
the earlier connection-borrowing fixes. Never use destructive git reset on a
shared tree. No release or tag is part of this planning task. During eventual
release, retain full parallelism, SQLite/PG gates, Windows teardown coverage,
and CI timing diagnostics.

## Implementation Slices

### 1. Promote Contracts And Establish The Upgrade Baseline

Files: the five specs named above, this plan/index, `pyproject.toml`, `uv.lock`,
`weft/_constants.py` snapshot inventory, Monitor snapshot tests.
Confirm approval of `WeftContext.session()` and the manual-drive scope contract.
Promote only the core/floor text needed by slices 1-2, record baseline, upgrade
the coordinated pair, and classify
upstream `_owns_queue` deliberately. Add regression tests for its ownership
meaning, not just its name. Run the compatibility slice before and after.
Leave Monitor store and command/Django contract promotion pending their gated
slice approval; the presence of proposed text is not authorization for that work.

### 2. Shared BaseTask And Watcher Ownership

Files: `weft/context.py`, `weft/core/tasks/base.py`, `multiqueue_watcher.py`,
`weft/core/launcher.py`, relevant constructors, `tests/helpers/weft_harness.py`;
tests in `tests/context/`, `tests/tasks/test_task_execution.py`,
`test_multiqueue_watcher.py`, `tests/core/test_task_runtime_connections.py`.
Introduce the context factory, owned fixed queues, driver scope, construction
unwind, and standalone watcher ownership. Keep normal launch/drive automatic.
Replace the harness's manual cleanup-only loop with the shared drive scope.
Close harness-owned same-key observers before whole-driver teardown, without
shortening their ordinary observation lifetime. Make the minimum Monitor
snapshot compatibility changes needed for the inherited session fields; do not
start the full worker/store migration as incidental work in this slice.
Prove the foreign-stop race while a keeper remains alive and prove reuse across
successive turns. Review this slice before migrating Monitor and commands.
Exercise foreign idle-task stop from inside an unrelated same-key operation:
queue leases release before inventory close refuses, the finalizer records the
failure and reaches CLOSED, and no repeated stop silently claims recovery.

**Gate before slices 3-5:** stop after the slice-2 implementation and independent
review. Require real SQLite and PG evidence for driver-thread release with a
surviving keeper, construct-on-A/drive-on-B cleanup, persistent reuse across
turns, and harness observer-before-driver teardown. Include a negative test
where an unrelated same-key active operation prevents close and cleanup failure
is recorded without private-type matching. Do not start the remaining slices
until these outcomes pass and the remaining scope is confirmed with the user.
This proves the shared task/driver fix, not the still-unmigrated Monitor worker
or every adapter path. Narrow verification and documentation for slices 1-2
are mandatory now; the gate does not defer their correctness checks to slice 5.

### 3. Monitor Maintenance And Store Borrowing

Files: `weft/core/monitor/task_monitor.py`, `store.py`, `_constants.py`,
`tests/tasks/test_task_monitor.py`, `tests/core/test_monitor_store.py`.
Use one context helper for both maintenance lanes, bind independent session
ownership to clones, and consolidate redundant per-queue cleanup only after
tests prove all handles and the worker cache are covered. Preserve sink closure
and typed failure results. Update the snapshot inventory for real ownership.
No changes to cleanup evidence/selection or the scheduling model.
Conditional on the slice-2 gate; promote the matching store/worker spec delta
only when this slice is approved.

### 4. Commands, Observers, And Remaining Callers

Files: inventory command/core-helper paths, `tests/commands/test_*observation_connections.py`,
`tests/core/test_queue_wait.py`, `tests/core/test_manager_runtime_connections.py`,
`tests/core/test_service_helper_connections.py`, affected adapter tests.
Move scope ownership into existing operation/resource owners, keep borrowed
helper contracts, and verify generator close, timeout, rebind, and partial setup.
Inspect all task subclasses and first-party adapters; make no edits where the
shared BaseTask or command change already supplies correct behavior.
Migrate the shared Channels/SSE thread bridge with the stream lifetime, not
after it. Exercise real WSGI and ASGI response paths, cancellation, and executor
cleanup; directly joining streaming_content alone does not prove server cleanup.
Conditional on the slice-2 gate. Reassess each owner before editing and shrink
this slice wherever existing one-shot context.broker() scopes already give
bounded lifetime or shared changes have solved it. The audit is comprehensive;
the rewrite is not. Confirm the direct-versus-bridged WSGI choice and promote
only the command/Django contract text needed by the retained scope.

### 5. Full Verification, Traceability, And Release Readiness

Update source spec mappings/backlinks, README embedding examples, relevant
lessons, and CHANGELOG. Document ownership, not temporary debug machinery.
Independent review after shared lifecycle, after Monitor/observer adoption,
and once at completion. Use real backend outcomes and narrow fault injection;
do not mock Queue/Manager to prove ownership. Close this plan only after all
scope migrations and gates are complete; publication needs separate instruction.
Conditional on the confirmed scope after slice 2; keep verification proportional
to that scope, without omitting backend or production-path correctness checks.

## Verification Matrix

| Boundary | Required firing evidence |
| --- | --- |
| Session factory | Exact target/config retained; no ambient reread; context/client hold no hidden live resource. |
| Ordinary BaseTask families | Consumer and service subclass real runs reuse warmed physical connections; shared entry paths do not duplicate cleanup. |
| Scope exit | Successful exits release owned resources. Ordinary cleanup Exception alone propagates; with a body failure it remains secondary. KeyboardInterrupt/SystemExit from driver-session close propagate even with a body failure, rather than becoming only a recorded finalizer error. Do not assert release when cleanup failed. |
| Driver ownership | Same-thread run, background run, manual drive, foreign stop during active turn/wait, foreign stop while idle then scope exit, rejected foreign/nested entry. |
| Construction | Invalid topology before DB effects; partial queue setup and post-super subclass failure; construct on A/drive on B leaves neither cache behind. |
| Shared key | Independent surviving task/queue remains usable; recycle happens only at declared scope exits, never per poll; no assumptions of per-handle cache isolation. |
| Thread-wide close refusal | An unrelated same-key operation or suspended iterator causes observable cleanup failure, without private exception imports/name/message matching; ending observer operations before a finalizing manual turn/stop and observers before driver exit releases the core while a sibling keeper remains alive, without resetting the observer cursor or per-turn session recycling. |
| Refusal during foreign finalization | Driver ownership was previously claimed on thread A by bare manual driving, but no `drive_scope()` is active. Foreign thread B holds an unrelated same-key operation and calls stop. Owned queue closes are attempted before inventory close; successfully closed minted queues release their leases despite refusal. Finalization reaches CLOSED with retained/logged cleanup failure, not full resource release. Repeated stop does not retry; driving is rejected. No owner guard exists to repair the retained inventory lease or either thread's cache. A separate active-`drive_scope()` case proves foreign stop defers finalization to the owner. Test both with real backend operations and a surviving keeper. |
| Queue-close failure | Narrow fault injection at an owned queue's close proves remaining queue closes and inventory close are still attempted; retain failure diagnostics. Successful inventory close tolerates the already-closed minted queues. |
| Monitor | Both lanes, setup/body/close failures, disjoint reactor/worker cores, bounded transaction freshness, control responsive during blocked work. |
| Watcher | Passed queue remains usable; all internally created leases close; dynamic add/remove churn does not retain historical facades; native/fallback waits and retries survive. |
| Observer | Timeout, cancellation, generator close, setup failure, route rebind, partial watcher start; no context exits with borrowed iterator active. A session-close failure during GeneratorExit reaches the close owner; a real body failure remains primary. |
| Realtime adapter (if retained after gate) | Channels/ASGI and any bridged WSGI create/next/close run on one worker; a retained direct WSGI path proves handler-thread iteration/close. Cancellation during next and send, cross-thread bridge close, close before first advance, duplicate close, disconnect deadline, setup failure, and normal exhaustion release the owner core with a sibling keeper alive; independent streams do not block each other. |
| Pool/connection cleanup | Real SQLite and PG physical resource counts or closed-connection evidence after worker exit with keeper alive; fresh independent commits visible before shutdown. |

No minimum rows/second or global timing threshold is introduced. Keep existing
behavioral liveness and leak checks; tests must fail on actual incorrect
ownership, lost evidence, resource growth, or blocked control.

Commands (source `. ./.envrc` first, use repo-managed tools):

```bash
./.venv/bin/python -m pytest tests/context tests/tasks/test_task_execution.py tests/tasks/test_multiqueue_watcher.py tests/tasks/test_service_task.py tests/tasks/test_task_monitor.py tests/core/test_task_runtime_connections.py tests/core/test_queue_wait.py
./.venv/bin/python -m pytest tests/commands/test_control_observation_connections.py tests/commands/test_result_observation_connections.py tests/commands/test_event_observation_connections.py tests/commands/test_spawn_observation_connections.py
./.venv/bin/python bin/pytest-pg --all
./.venv/bin/python -m pytest -m ''
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
```

Use the normal 12-worker cap, not serial scheduling. Run tests that mutate the
same environment sequentially; independent read-only reviews may run in parallel.
Before completion run the configured traceability checker if the repo enables
one, reconcile code/spec/plan links, and include integration/extension gates from
the release helper without bypassing them. Windows CI must cover real file
releasability, not just weakref collection.

## Review And Approval

Planning approval requested for the additive context factory, manual-drive API,
protected initialization hook, and explicit stream-thread ownership contract.
No new public exports, CLI/storage changes, or expansion of ordinary worker
authority are proposed. Constructor ownership needs its failing test before
slice 2 proceeds; do not weaken eager initialization or add per-turn cleanup.

Fresh-eyes self-review found and corrected these gaps:

| Finding | Risk | Disposition |
| --- | --- | --- |
| Constructor effects precede the drive scope | Construct-on-A/drive-on-B or post-super failure can strand a core even after correct drive cleanup. | Shared partial-initialization guard, explicit first-party post-super coverage, and unchanged eager-write semantics added to Decision 2 and its spec delta. |
| Async stream advancement changes threads | Session exit on the event loop cannot recycle a pool worker; concurrent close can race next. | Decision 5 names a per-stream worker and cancellation-safe ordered cleanup, with matching [PY-2]/[DJ-12] text and tests. |
| Minted facade history can grow without bound | Mechanical Queue-to-session.queue conversion makes churn permanent. | Fixed versus dynamic ownership applies to watchers, Manager routes, live-run input, and observer rebinding. No cache-policy framework added. |

Independent Claude review of the initial draft returned BLOCKED. Verified
findings and dispositions:

| ID | Finding | Disposition |
| --- | --- | --- |
| B1 | Exact [SB-0.1] dependency/API text was absent. | Added the reviewed 8.3.0/4.3.0 paragraph and preserved the older-schema cold-cutover rule. |
| B2 | Single allocation ownership was asserted without a sequence. | MultiQueueWatcher's existing target-resolution/allocation point creates the inventory session; BaseTask reuses it; one protected resource helper closes it. This avoids the reviewer's suggested extra injection parameter. |
| B3 | Construction guard was deferred to its own implementation slice. | Replaced the deferral with shared low-level initialization scopes and explicit post-super coverage. Declined reliance on launcher-only cleanup: direct constructors are supported and a failed constructor need not return an object to its caller. |

Also recorded the released-handle recycle no-op, the two-handle rationale in
the spec delta, and the manual driver's closed-reactor caveat.

Follow-up independent Codex review accepted the revised core architecture and
identified three remaining corrections: backend plugin API is v9 (verified in
upstream _backend_plugins.py and the PG plugin), GeneratorExit can hide attached
session-cleanup failures, and SSE response closure needs the same thread owner
as Channels. Decision 5, its spec delta, rollout, and verification now cover
those points, including the extra WSGI worker cost. Partial-construction rollback
registration includes any acquired workers and sinks, not just queue handles.
Final independent correction review returned PASS: accepted findings are
resolved at the plan level, with no new correctness or resource-management
defect found in the revisions. The proposal is ready for user approval, not a
claim of implementation completion. Planning verification: plan metadata and
spec hygiene, six tests passed with 12 workers; git diff --check passed. No
runtime migration verification is claimed.

### User Review Revision

The subsequent user review narrowed the design and execution scope:

| Finding | Disposition |
| --- | --- |
| Close refusal affects every active operation on the calling thread/key, not only this handle. | No private refusal detection. Record every close failure; explicitly end harness observers before driver-session exit. Added positive ordering and negative refusal evidence to the slice-2 gate. |
| A third construction session is unnecessary. | Initialization success recycles the live inventory handle; failure unwinds resources then attempts inventory close. Kept the post-super hook and documented deferred recycling while caller-owned operations remain active. |
| Clone ownership classification was vague. | `_owns_queue` is expected and replaced, with clone-local True; new session/drive fields are replaced, never shared. Queue/core release and reactor survival are the proof. |
| A worker for synchronous WSGI is not intrinsically required. | Common bridge is a simplicity/cost judgment. The spec requires serialized ownership, not an extra WSGI thread. Slice 4 can retain a proven direct handler-thread path. |
| The migration scope is too large to execute automatically. | Slices 3-5 require slice-2 backend evidence, independent review, and user confirmation of remaining scope. Defer corresponding spec promotion and shrink command work where bounded broker scopes already suffice. |

Scoped independent review of these five revisions returned PASS. A further
ordering clarification follows from BaseTask.process_once(): a manual stopping
turn can finalize before drive-scope exit, so harness observers must end active
reads before such drive/stop calls while retaining their logical cursor/session.
Independent review of that clarification also returned PASS. Verification
includes that earlier-finalization path. Documentation checks:
six tests passed with 12 workers; git diff --check passed. Runtime code and
dependencies remain outside this planning revision.

### Refused Inventory Close Revision

The user identified that the finalizer can retain a refused inventory close and
still transition to CLOSED. The session refuses before closing minted queues,
so relying solely on its close unnecessarily retains every queue lease too.
Verified against BaseTask._finalize_task_once, Queue.close(), and
BrokerSession.close()/__exit__ at the recorded baselines.

Decision 2 and the [CC-2.2.1] delta now require explicit queue-lease closes before
inventory close, with all attempts made despite individual failures. This keeps
one cleanup owner and uses upstream's idempotent close behavior. Queue.close()
has no active-operation refusal but can still raise waiter/backend errors;
successful queue closes are the condition for claiming their leases released.
CLOSED now explicitly denotes attempted once-only finalization and no further
driving, not successful release. The plan preserves existing retained failures,
no automatic retry on CLOSED, and the residual inventory-lease/cache risk.
The verification matrix adds foreign-thread stop while that thread holds an
unrelated same-key operation, plus partial queue-close failure.

The scope-exit matrix distinguishes ordinary Exception failure priority from
KeyboardInterrupt/SystemExit propagation at driver-session exit. Task
finalization's existing BaseException catch-all is unchanged. Scoped independent
review returned PASS against the upstream and BaseTask cleanup contracts.
Documentation verification: six metadata/spec-hygiene tests passed with 12
workers; git diff --check passed. At planning review time, no runtime migration
had been implemented.

## Implementation Checkpoint

Slices 1-5 are implemented. The dependency floors, public
context session factory, fixed watcher/task inventory ownership, task drive
scope, constructor unwind, harness driver ownership, and minimum Monitor clone
compatibility are present. The user confirmed all remaining slices after the
slice-2 gate. Slice 3 adds one automatic worker-local session scope for both
Monitor maintenance lanes; MonitorStore borrows the owner session through a
session-minted persistent sidecar anchor; queue, sink, store, and session close
failures remain typed before result publication. The [IMPL.11] and [SB-0.4a]
spec deltas are promoted. Slice 4 adds command-owned session scopes to bounded
and streaming command paths, bounded standalone core-helper fallbacks, and
owner-thread Django ASGI/Channels iterator bridges while retaining direct
handler-thread WSGI iteration. The [PY-2], [DJ-12.1], and [DJ-12.2] spec deltas
are promoted. Slice 5 completes the public embedding guidance, source-spec
implementation mappings and backlinks, durable cleanup lessons, changelog,
full backend verification, extension verification, and package-build gates.

The slice-2 gate passed after independent review remediation. SQLite evidence:
656 passed and 4 skipped across the core, Liveness, Monitor, ownership, and
harness matrix. PostgreSQL evidence: 618 passed across the same shared matrix.
The focused ownership matrix passed 25 tests on each backend. Ruff check,
Ruff format check, full configured mypy, git diff check, and six plan/spec
hygiene tests pass. The final independent re-review reported no findings and
PASS. The user then confirmed the remaining Monitor, command/observer, and
Django scope.

Slice-3 SQLite evidence: 316 tests completed across MonitorStore and
TaskMonitor, with two PostgreSQL-only cases skipped. PostgreSQL evidence: 315
passed and one SQLite-only case skipped. The close-failure matrix covers store,
sink, queue, and session failures on both maintenance lanes. The PostgreSQL
physical-connect probe verifies repeated sidecar operations do not create new
connections after each owner path is warmed. Full configured mypy, repository
Ruff check/format, six plan/spec hygiene tests, and git diff check pass.
Independent review findings on owner-identity validation, clone acquisition
unwind, and BaseException-safe cleanup were remediated with firing tests. The
final slice-3 re-review returned PASS with no remaining findings.

Slice-4 SQLite evidence passed the command, observer, Manager, Monitor-helper,
queue-wait, and full Django integration matrix with only backend-specific
skips. PostgreSQL evidence passed 786 tests with one SQLite-only skip. The
matrix includes suspended public generator close, body-versus-cleanup exception
priority, Manager partial setup, real ASGI ``send_response()`` cancellation,
early WSGI response close, close-before-first-advance, duplicate close, setup
failure, independent streams, bounded Channels disconnect, and delayed cleanup
diagnostics. Ruff check/format, full configured mypy, policy/spec hygiene, and
git diff checks pass. Independent review found and drove fixes for an unbounded
Channels cancellation wait, Manager partial-setup leakage, missing public
boundary tests, and duplicate detached-cleanup diagnostics; final re-review is
complete and returned PASS with no remaining findings.

Slice-5 full-suite evidence: SQLite passed 5,096 tests with 39 expected skips;
PostgreSQL passed 5,040 tests with 24 expected skips on the final clean runs.
The first PostgreSQL run exposed a test-boundary defect in three mocked spawn
reconciliation tests: their 100 ms behavior deadline included real session and
queue setup, so full-suite connection pressure could consume the deadline
before the mocked monitor outcome. The production deadline was not changed.
Those tests now replace only the command module's monotonic clock with a
deterministic clock; the focused matrix passes on both backends.

The extension gates passed with 117 Django tests and one expected PG-only skip,
76 microsandbox tests, 113 Docker tests, and 26 macOS sandbox tests. Repository
Ruff check and format check, configured mypy over 434 source files, 96 policy
and spec-hygiene tests, and git diff checks pass. The root package and all four
subpackages build both source and wheel distributions at their current
versions. Windows teardown and publication remain CI/release gates and were not
claimed by this local implementation task.

The final independent review found five lifecycle gaps despite the green broad
matrix: driver-session entry failure could bypass finalization; reactor sink
close failure was suppressed; MonitorStore compared less config material than
the upstream process-session key; a published dynamic queue was registered only
after fallible displaced-waiter cleanup; and a Monitor sink was assigned only
after fallible validation. Each finding received a firing regression and an
ordering or ownership fix. The first remediation review accepted four fixes and
found that value-equal `ConfigField` declarations still differ in the upstream
key. MonitorStore now requires the exact context `Config` object, matching every
in-tree owner path. The final narrow review returned PASS with no findings.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

No implementation deviations are recorded for slices 1-5. The
foreign-stop matrix wording was corrected during implementation review so an
active owner scope is never bypassed by a foreign finalizer.
