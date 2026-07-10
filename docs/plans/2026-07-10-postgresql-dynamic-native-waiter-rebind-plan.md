# PostgreSQL Dynamic Native-Waiter Rebind Plan

Status: completed
Source specs: `docs/specifications/01-Core_Components.md` [CC-2.1], `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4], `docs/specifications/07-System_Invariants.md` [QUEUE.7] and [QUEUE.8]
Superseded by: none

Date: 2026-07-10

Plan type: Weft implementation with spec revision against a released
SimpleBroker prerequisite.

Owner: Weft watcher integration. SimpleBroker 5.3.0 and the coordinated
first-party backend extensions already provide the required strategy seam and
fixed-set waiter behavior.

Primary Weft code owner: `weft/core/tasks/multiqueue_watcher.py`.

Released dependency reference: `simplebroker.ext.PollingStrategy` in
SimpleBroker 5.3.0.

## 1. Goal

Make runtime `MultiQueueWatcher.add_queue()` and `remove_queue()` safe and
complete for an already-running standalone watcher. A topology change must be
applied by the watcher drive owner between wait and drain phases, rebuild the
optional multi-queue activity waiter from the exact new membership, replace the
waiter through a supported `PollingStrategy` API, and close the displaced
waiter only after it is no longer the strategy's wait target.

For the standalone BaseWatcher drive, the strategy remains the only background
wait path. Weft must not add a second wait thread, call PostgreSQL
`LISTEN`/`NOTIFY` APIs, or wait directly on a new waiter while the inherited
strategy is still running. A missing or failed
native waiter still falls back to the existing bounded polling behavior, so
queue delivery semantics do not change.

This plan implements the deferral recorded in section 6.5 of
[`2026-07-09-reference-reactor-safety-hardening-plan.md`](./2026-07-09-reference-reactor-safety-hardening-plan.md).
That earlier plan is execution context, not the normative behavior contract.
The promoted spec text named below becomes authoritative before Weft code is
changed.

## 2. Requested Outcomes

The implementation is not complete until all of these are true:

- The installed SimpleBroker floor is at least 5.3.0 and exposes the supported
  `PollingStrategy.replace_activity_waiter(...)` seam, which does not restart
  strategy lifecycle state and does not close the displaced waiter.
- A running standalone `MultiQueueWatcher` never mutates `_queues`, rebuilds a
  waiter, binds a waiter, or closes a waiter on a foreign mutation thread.
- `add_queue()` and `remove_queue()` keep their synchronous `None`-returning
  public shape: a foreign caller returns only after the owner has applied or
  rejected the requested mutation.
- The owner applies each accepted mutation in a deterministic linear order and
  binds a waiter built from the exact current activity-wait membership.
- Adding queue `C` to `{A, B}` replaces the native waiter with `{A, B, C}`;
  removing `B` then replaces it with `{A, C}`.
- A write to an added PostgreSQL queue wakes through the replacement real
  `LISTEN`/`NOTIFY` waiter before its handler runs.
- A removed PostgreSQL queue no longer wakes the replacement waiter.
- Native waiter creation failure detaches the stale waiter and leaves the
  strategy on polling fallback. A later successful topology generation can
  bind a native waiter again.
- Stop and topology mutation are linearizable: a mutation committed before
  stop may succeed; a mutation for which stop wins fails without binding after
  close. No caller or watcher thread is stranded.
- One synchronous main-thread SIGINT, plus signals coalesced while the topology
  commit window remains active, cannot deadlock on the topology lock or expose
  a half-published waiter; `KeyboardInterrupt` is delivered after the current
  topology request is published or rolled back and signaled. Nested re-entry
  into inherited stop after that window is the separate exclusion in section
  17.
- Removing the final queue uses an empty-membership polling state without
  calling the multi-queue waiter factory with an empty list; a later add can
  restore native waiting.
- Direct/manual wait is serialized against drive start, mutation, and stop
  cleanup, so no thread closes its waiter while `wait()` is active.
- `BaseTask` remains construction-fixed and continues to reject both dynamic
  mutators before effects. Its canonical reactor wait calls the shared
  protected broker-wait body under `BaseTask`'s existing lifecycle guard rather
  than entering the standalone public manual-wait ownership wrapper.
- No queue names, messages, delivery guarantees, task states, TaskSpec fields,
  CLI output, or storage formats change.
- Documentation, dependency floors, spec mappings, and reciprocal code
  backlinks describe the shipped behavior exactly.

## 3. Source Documents and Required Reading

### 3.1 Read in this order before editing Weft

1. `AGENTS.md`, especially sections 1.1, 4, 5, and 7.
2. `docs/agent-context/decision-hierarchy.md`.
3. `docs/agent-context/engineering-principles.md`, especially sections 4,
   4.1, 6, 7, 9, and 11.
4. `docs/agent-context/runbooks/runtime-and-context-patterns.md`, especially
   queue-handle ownership and spawn recreation.
5. `docs/agent-context/runbooks/testing-patterns.md`.
6. `docs/agent-context/runbooks/adversarial-acceptance-probes.md`, runtime
   adaptation.
7. `docs/specifications/01-Core_Components.md` [CC-2.1], [CC-2.2.1].
8. `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4].
9. `docs/specifications/07-System_Invariants.md` [QUEUE.7], [IMPL.10].
10. `weft/core/tasks/multiqueue_watcher.py` in full.
11. `weft/core/tasks/base.py`, focusing on the dynamic mutator rejection,
    final public reactor templates, and cleanup ownership.
12. `tests/tasks/test_multiqueue_watcher.py` in full.
13. `tests/conftest.py`, focusing on `broker_env`, backend classification, and
    the shared PostgreSQL test path.
14. `tests/helpers/test_backend.py` and `bin/pytest-pg`.
15. `weft/commands/interactive.py`, the only current production constructor of
    a standalone `MultiQueueWatcher`. It currently uses static membership.
16. The completed historical plan
    `docs/plans/2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md`.
    Read it for why Weft owns membership and SimpleBroker owns wake mechanics;
    do not copy its now-superseded foreign-thread reset behavior.
17. Section 6.5 of
    `docs/plans/2026-07-09-reference-reactor-safety-hardening-plan.md`.

### 3.2 Read in this order before editing SimpleBroker

There was no sibling `AGENTS.md` at plan authoring time. Re-check before
editing because repository instructions may appear later.

1. `../simplebroker/README.md`, "Advanced watcher integrations" and the
   development commands near the end.
2. `../simplebroker/simplebroker/watcher.py`, especially `BaseWatcher` and
   `PollingStrategy`.
3. `../simplebroker/simplebroker/_backend_plugins.py::ActivityWaiter`.
4. `../simplebroker/simplebroker/ext.py`, which exports `PollingStrategy` as
   the stable embedder surface.
5. `../simplebroker/tests/test_watcher.py::TestPollingStrategy`.
6. `../simplebroker/tests/test_watcher_race_conditions.py`.
7. `../simplebroker/docs/plans/2026-07-06-watcher-embedder-lifecycle-hooks-plan.md`,
   especially the explicit statement that `PollingStrategy.start()` is not a
   live hot-swap API.
8. `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/runner.py`,
   `PostgresMultiQueueActivityWaiter` only. It intentionally captures an
   immutable queue-name tuple.
9. `../simplebroker/extensions/simplebroker_pg/tests/test_pg_notify.py`, which
   already proves fixed-set fan-in filtering and close behavior.

### 3.3 Comprehension gate

Before the first edit, the implementer must be able to answer these questions
in the execution log:

1. Why is `PollingStrategy.start()` the wrong replacement seam? It resets
   provider, callback, counters, and lifecycle state. It is startup/retry
   initialization, not a live waiter rotation.
2. Why may the mutation thread call `PollingStrategy.notify_activity()` but
   not detach or close the current waiter? `notify_activity()` is the existing
   cross-thread hint used by stop; waiter ownership and replacement must remain
   serialized with the drive owner's calls to `wait_for_activity()`.
3. Why must the owner still probe and drain queues after a native wake? An
   activity waiter is only a hint. It does not prove a message is pending or
   claimable.
4. Why is a PostgreSQL-only implementation in Weft forbidden? Backend
   selection and native waiter construction belong to SimpleBroker. Weft owns
   only queue membership and dispatch policy.
5. Why does `BaseTask` topology stay unchanged while one call site changes? Its
   overrides still reject both dynamic mutators, but its canonical reactor must
   call the protected shared broker-wait body directly so standalone manual
   ownership does not wrap or finalize a task-reactor wait.
6. What happens when native waiter construction fails? The stale waiter is
   removed, polling remains correct, and a later successful topology generation
   may reattach native waiting.
7. Why is `RLock` necessary but insufficient for SIGINT? It prevents inherited
   signal-stop from self-deadlocking in short locked sections, but Phase C must
   also defer `KeyboardInterrupt` until waiter and mapping ownership agree.
8. What happens when the final queue is removed? The owner installs `None`,
   clears active scheduling, and skips the factory's invalid empty-list input.
9. Why does direct/manual wait need temporary ownership? Without it, start or
   stop can bind or close the same cached waiter while its `wait()` call is
   active, recreating the second-path/foreign-close race outside the drive loop.
Stop before editing if any answer is unclear. Re-read the named code rather
than inferring from generic watcher patterns.

## 4. Baselines and Upstream Release Assumption

### 4.1 Weft spec baseline

The governing specs are already modified in the uncommitted reactor-hardening
worktree. Therefore the baseline is the named worktree content, not bare HEAD:

- Weft HEAD: `2b02032f895ade32efa601f2b3ae0d59751dacfb`.
- `docs/specifications/01-Core_Components.md` worktree blob:
  `8f27ac89844de9a94e9c5984c07e53d5494e8ced`.
- `docs/specifications/04-SimpleBroker_Integration.md` worktree blob:
  `820a2e3aa5c771abfac14d08d94a627810df8503`.
- `docs/specifications/07-System_Invariants.md` worktree blob:
  `30ab6e636612ae5d135d00aeb892186f7bfd7ae0`.

At implementation start, rerun `git rev-parse HEAD` and `git hash-object` for
all three specs. If any identifier changed, review the changed text and update
this baseline before promoting the proposed delta. Never overwrite the active
reactor-hardening edits from another owner.

### 4.2 Released SimpleBroker baseline

The upstream prerequisite is complete and is not an implementation slice in
this plan:

- SimpleBroker release: `5.3.0`;
- coordinated PostgreSQL extension release: `simplebroker-pg 3.2.0`;
- coordinated Redis/Valkey extension release: `simplebroker-redis 3.2.0`;
- SimpleBroker release commit:
  `b77093b57ae219cf3a7c0c3e5bc923e55ce193fd`;
- `../simplebroker/simplebroker/watcher.py` worktree blob at rebase:
  `4f12dba310d2debd73f7662717fd22707b22e913`;
- public method:
  `PollingStrategy.replace_activity_waiter(ActivityWaiter | None) -> ActivityWaiter | None`;
- Weft declared floor: `simplebroker>=5.3.0`;
- Weft declared PostgreSQL floors: `simplebroker-pg>=3.2.0`;
- Weft lock resolves both exact releases from the package index;
- Weft's managed environment reports `simplebroker=5.3.0`,
  `simplebroker-pg=3.2.0`, and a callable replacement method.

Do not edit the sibling SimpleBroker repository from this plan. Tasks 2 through
4 are retained below only as satisfied prerequisite evidence and must not be
executed. The active implementation sequence is spec promotion followed by the
Weft-only Tasks 5 through 12.

### 4.3 Promotion baseline

Promotion strategy is **A: in-file text before link claims**.

After independent review of this plan, the spec-promotion slice adds the exact
requirements in section 10 to the active specs but does not yet add
implementation mappings that claim the code exists. Record the post-promotion
commit SHA or, for uncommitted review, record HEAD plus the three new worktree
blob hashes. All later code and mapping claims are judged against that promotion
baseline.

Promotion baseline (uncommitted review, 2026-07-10):

- Weft HEAD: `2b02032f895ade32efa601f2b3ae0d59751dacfb`;
- `docs/specifications/01-Core_Components.md` worktree blob:
  `6c52ec7b26744e23094f885357b22f3d87602513`;
- `docs/specifications/04-SimpleBroker_Integration.md` worktree blob:
  `6f77993da454f173ac7cfbd3061c5a7b8b2abafc`;
- `docs/specifications/07-System_Invariants.md` worktree blob:
  `0fc32d2e4c24f9e8968328dd6f351add44b15eef`.

Independent architecture review passed after the rollback ownership transition
was made explicit: restoration consumes rollback ownership exactly once and
never closes a reused pre-transaction cached waiter.

## 5. Current Structure and Proven Defect

### 5.1 Current ownership

| Concern | Current owner | Current behavior |
| --- | --- | --- |
| Queue membership and modes | `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher` | `_queues` maps each queue name to its `QueueRuntimeConfig`; `add_queue()` and `remove_queue()` mutate it immediately on the calling thread. |
| Standalone background wait | `simplebroker.watcher.PollingStrategy` | `BaseWatcher._process_messages()` calls only `self._strategy.wait_for_activity()` before pending checks and drains. This remains the sole wait path for the standalone inherited drive. BaseTask keeps its final reactor wrapper over the protected shared broker-wait body. |
| Native fan-in waiter creation | `simplebroker.create_activity_waiter_for_queues(...)` | The backend returns `ActivityWaiter | None`. PostgreSQL returns one fixed-set `PostgresMultiQueueActivityWaiter`; SQLite returns `None`. |
| Weft waiter cache | `MultiQueueWatcher._multi_activity_waiter` plus generation/signature fields | Direct/manual `wait_for_activity()` can rebuild the cached waiter for a changed generation. |
| Strategy attachment | `BaseWatcher._start_strategy()` through `MultiQueueWatcher._create_activity_waiter()` | The strategy attaches the current multi-queue waiter once when the watcher starts or retries. |
| Dynamic invalidation | `MultiQueueWatcher._reset_multi_activity_waiter()` | The calling thread detaches and closes the cached waiter. It does not install a replacement in the running strategy. |
| Task topology | `BaseTask.add_queue()` and `BaseTask.remove_queue()` | Both reject all runtime mutation. This remains unchanged. |
| Production standalone caller | `weft/commands/interactive.py::InteractiveClient` | Constructs a static watcher today. No current production caller mutates membership after start. |

### 5.2 Reproduced baseline

A read-only probe against the current Weft tree used a real temporary SQLite
broker, a background `MultiQueueWatcher`, and only a fake `ActivityWaiter`
protocol object. It observed:

```text
before_native=True
after_add_native=False
waiter_sets=[('a', 'b')]
close_counts=[1]
creator_threads=['MainThread']
```

The foreign `add_queue("c", ...)` closed and detached the `{a, b}` waiter. No
`{a, b, c}` waiter was created or rebound to the running strategy. Polling still
finds messages eventually, so this is a native-wakeup and ownership defect, not
a message-loss claim.

### 5.3 The exact failure sequence

```text
Mutation thread                         Watcher drive owner
---------------                         -------------------
add_queue(C)
  mutates _queues
  increments generation
  detaches old waiter  <--------------  strategy may be inside old.wait(...)
  closes old waiter
  returns
                                        next strategy wait has no native waiter
                                        bounded polling eventually discovers C
```

The corrected sequence is:

```text
Mutation thread                         Watcher drive owner
---------------                         -------------------
validate request
append one _TopologyMutation
strategy.notify_activity()
wait on request.done  ----------------> finish current wait/turn
                                        pop request in linear order
                                        build candidate membership
                                        build waiter for exact candidate set
                                        strategy.replace_activity_waiter(new)
                                        publish membership/generation
                                        close displaced waiter exactly once
                                        set request result + done
return None             <---------------
```

If waiter creation fails, `new` is `None`; replacement still detaches the stale
fixed-set waiter and the strategy resumes through its existing polling path.

## 6. Scope Challenge and Chosen Shape

### 6.1 What already solves part of the problem

- `MultiQueueWatcher` already owns deterministic membership order,
  `_queue_generation`, exact membership signatures, waiter construction,
  fallback behavior, and queue dispatch. Reuse all of it.
- `PollingStrategy.notify_activity()` already carries a supported cross-thread
  local hint. Reuse it; do not add a wake thread or event bus.
- Released `PollingStrategy.replace_activity_waiter(...)` already provides the
  owner-confined live ownership transfer, preserves data/local state, resets
  native-generation state, accepts `None`, and returns the displaced waiter
  without closing it. Weft must call this public seam rather than
  `start()`, `detach_activity_waiter()`, or private strategy fields.
- PostgreSQL already has one process-local shared listener and fixed-set fan-in
  waiter. Do not change its registration model or add mutable backend
  membership.
- `BaseWatcher._process_messages()` already keeps the strategy as the single
  wait path. Do not override or copy that loop.
- `broker_env` and `bin/pytest-pg` already provide real cross-backend proof.
- `BaseTask` already seals runtime topology. One narrow MRO-preservation edit is
  required: its canonical reactor wait must call the shared protected
  `MultiQueueWatcher` wait body directly so standalone manual-wait ownership
  does not capture task-reactor waits. No task topology policy changes.

### 6.2 Minimum complete change

The smallest complete runtime design has one topology state machine in
`MultiQueueWatcher` plus one narrow `BaseTask` call-site preservation edit:
serialize running standalone mutations through one private request record,
apply them at existing owner-owned pending/drain boundaries, extract the
existing broker wait body into a protected helper, and let `BaseTask` call that
helper under its existing lifecycle guard. The released SimpleBroker method is
a dependency, not code owned by this slice.

One private `_TopologyMutation` dataclass is permitted because it names the
cross-thread state machine. Do not add a generic command framework, mutation
executor, topology service, queue factory, event bus, or backend adapter.

The change touches more than eight files only because the code crosses two
repositories and three normative docs. Runtime implementation remains two
modules and one internal request type. Reducing the documentation or tests
would hide the boundary; adding more runtime abstractions would be overbuilt.

### 6.3 Deliberate API decisions

- Keep `add_queue()` and `remove_queue()` synchronous and returning `None`.
  Changing them to fire-and-forget would create an unstated eventual-topology
  contract and make construction or backend errors invisible to the caller.
- A running foreign mutation may block behind the current bounded strategy
  wait call and the current handler/drain pass. There is no separate mutation
  timeout. The caller must not hold an application lock that any watcher
  handler needs. Do not add an interrupt API solely to reduce this handoff
  latency.
- Before a drive owner exists, apply mutations synchronously on the caller as
  today while holding `_topology_lock` through the complete mutation. The
  `run_in_thread()` override reserves its exact `Thread` under the same lock
  before starting it. If immediate mutation gets the lock first, it completes
  before reservation; if reservation wins, later mutation queues for that drive
  instead of applying on the caller.
- While `run()` or `run_in_thread()` owns the watcher, foreign mutations enqueue
  and wait for owner completion.
- Direct/manual `wait_for_activity(timeout)` has one temporary manual-wait owner.
  While it is active, a second manual wait, drive start, and topology mutation
  raise `RuntimeError` before effects. Public stop sets the stop event but leaves
  waiter close and inherited strategy cleanup to the manual-wait thread's
  `finally`; it never closes a waiter underneath `wait()`.
- Reject mutation called from the drive owner while a watcher is running. A
  handler cannot block waiting for its own turn to finish, and applying inside
  dispatch would violate the between-turn boundary. The error occurs before
  topology, queue, waiter, or generation effects.
- Apply queued mutations in deque order. Each successful request reaches a
  complete waiter/fallback state before the next request. Do not add batching
  or coalescing until measured production churn exists.
- `list_queues()` and `get_queue()` report applied topology only. Because a
  public mutation blocks until application, the requesting caller sees the new
  topology when the call returns. Both accessors take `_topology_lock` only long
  enough to snapshot the mapping or selected queue reference.
- Successful removal preserves the current queue-facade lifetime behavior. This
  slice does not add explicit removed-handle close, anchor reuse, or facade
  cleanup policy. A newly opened add candidate that fails before publication is
  rollback state and must close on the thread that opened it.

These choices favor explicit linear behavior over clever eventual state.

## 7. Invariants and Constraints

### 7.1 Wait and ownership invariants

1. `PollingStrategy.wait_for_activity()` remains the only background wait call
   for the standalone inherited BaseWatcher drive. BaseTask's final reactor
   wrapper continues to own its direct call to the protected shared body.
   The existing public `MultiQueueWatcher.wait_for_activity(timeout)` remains a
   direct/manual API for a watcher with no reserved or active drive. It is not a
   second background path and must not be called after `run_in_thread()` has
   accepted a drive or while `run()` owns the watcher. Runtime guards enforce
   this and allow only one manual-wait thread at a time.
2. No mutation thread calls `waiter.wait()`, `_ensure_multi_activity_waiter()`,
   `_reset_multi_activity_waiter()`, `detach_activity_waiter()`,
   `replace_activity_waiter()`, or `waiter.close()` while an owner exists.
3. Only the drive owner creates the replacement waiter for a running watcher.
4. Replacement occurs between calls to strategy wait and queue drain, never
   during either operation or handler dispatch.
5. The replacement waiter signature equals the ordered names returned by
   `_activity_wait_configs()` for the topology being committed.
6. The displaced waiter is closed once, after replacement, on the drive owner.
7. `replace_activity_waiter(None)` is the supported transition to polling
   fallback.
8. Local activity hints survive waiter replacement. Native hints and burst
   state tied to the displaced waiter do not.
9. `PollingStrategy.start()` remains startup/retry initialization and is not
   called for topology mutation.

### 7.2 Queue and delivery invariants

- [QUEUE.4], [QUEUE.5], and [QUEUE.6] remain unchanged. A wake never consumes,
  reserves, moves, deletes, or acknowledges a message.
- `READ`, `PEEK`, and `RESERVE` dispatch keep the existing
  `_fetch_next_message()` and `_process_queue_message()` paths.
- Queue iteration order and priority behavior stay unchanged except for the
  requested membership delta.
- Removing an active queue also removes its name from `_active_queues` and
  rebuilds `_queue_iterator` before the next drain.
- No live queue, waiter, or connection crosses a process boundary.
- Successful remove does not add an explicit `Queue.close()` call or change
  BaseWatcher's anchor behavior. Candidate add handles close only when their
  request fails before publication.

### 7.3 Public compatibility invariants

- `add_queue(...) -> None` and `remove_queue(...) -> None` keep their signatures.
- Existing `ValueError` and `TypeError` validation shapes remain.
- Duplicate add and missing remove errors are delivered to the requesting
  thread, not logged only on the watcher thread.
- `BaseTask` rejection wording and pre-effect behavior remain green.
- Static `InteractiveClient` behavior remains unchanged.
- A positive direct/manual `wait_for_activity()` call keeps its signature and
  behavior when used alone. While that call owns the cached waiter, drive
  start, topology mutation, and a second manual wait fail synchronously with
  `RuntimeError` before they create or replace resources.
- No public CLI, TaskSpec, payload, task state, queue name, or result shape
  changes.

### 7.4 Failure priorities

| Failure | Required behavior | Fatal? |
| --- | --- | --- |
| Invalid mutator arguments | Reject synchronously before enqueue. | Caller error only. |
| Duplicate add or missing remove | Owner rejects request; same exception reaches caller; watcher continues. | Caller error only. |
| Queue construction/open failure | Candidate topology is not published; error reaches caller; watcher continues on old topology. | Request failure. |
| Native waiter factory returns `None` | Commit topology, replace old waiter with `None`, close old waiter, use polling. | No. |
| Expected waiter factory exception (`BrokerError`, `OSError`, `RuntimeError`, `TypeError`, `ValueError`) | Debug-log, treat as unavailable for this generation, commit topology on polling fallback. | No. |
| Strategy replacement fails before effects | Upstream's exception-atomic contract leaves the old waiter installed. Do not publish candidate topology; close candidate waiter/new queue handles; fail the request and re-raise into inherited watcher retry. | Yes for that drive attempt. |
| Strategy replacement throws after changing state | Contract violation. Do not guess how to restore private strategy state; stop and re-plan against the upstream defect. | Fatal blocker. |
| Non-SIGINT interruption after replacement but before publication | Restore the displaced waiter through the replacement seam before closing the candidate; close unpublished add resources; fail-stop. Never close the candidate while it remains installed. | Watcher exit. |
| SIGINT during Phase C or its cleanup | Defer handler raise/stop re-entry, finish one consistent request result and ownership cleanup, then raise `KeyboardInterrupt`. | Watcher exit. |
| Installed, displaced, or candidate waiter close raises `Exception` | Keep the committed state when applicable and debug-log once at the defensive backend-cleanup boundary. Do not retry because `ActivityWaiter.close()` does not promise post-exception idempotence. | Best effort cleanup. |
| Unpublished add Queue rollback close raises `Exception` | Debug-log at the defensive rollback boundary and preserve the original request/drive failure. Never apply this catch to successful remove. | Best effort rollback. |
| Stop wins before commit | Reject request with `RuntimeError`; close candidate resources; never bind after close. | Request failure. |
| Mutation commits before stop is observed | Request succeeds, then normal owner shutdown closes waiter state. | No. |
| Drive start, mutation, or second manual wait races a live manual wait | Reject the new operation synchronously before Queue/waiter effects. The manual caller retains ownership. | Caller error only. |
| Stop races a live manual wait | Signal stop and wake the strategy, but do not call inherited stop, join, or close from the stopping thread because inherited no-drive cleanup can close the strategy. The manual wait's `finally` closes its waiter once, clears ownership, and invokes inherited non-joining cleanup on the manual owner. | Normal shutdown. |
| Final removal produces empty activity membership | Do not call the factory with `[]`; install `None`, close the displaced waiter once, clear active scheduling, and allow a later add to restore native waiting. | No. |
| Owner exits with an in-flight or queued request | Candidate rollback runs; owner `finally` fails/signals the in-flight and every queued request so no caller blocks forever. | Watcher exit. |

Do not catch broad `Exception` around the entire owner loop. Catch only the
named request/backend boundary failures. Unexpected programming defects must
still reach SimpleBroker's watcher retry or thread exception path.

### 7.5 Engineering constraints

- DRY: factor existing immediate add/remove bodies into private owner-safe
  helpers used by pre-start and running paths. Do not maintain two validation or
  queue-construction implementations.
- YAGNI: no async API, mutation IDs, public status API, batching, debounce,
  topology persistence, background executor, new dependency, or mutable
  PostgreSQL waiter.
- Red-green TDD is mandatory for every behavior task. Record the exact expected
  red failure before production code changes.
- Keep `from __future__ import annotations`, standard/third-party/local import
  grouping, modern type syntax, `collections.abc` abstract types, and complete
  annotations.
- Use a private `@dataclass(slots=True)` for `_TopologyMutation`; do not expose
  it from `weft/core/tasks/__init__.py`.
- Import the public `ActivityWaiter` protocol from `simplebroker.ext` for typed
  cached waiter state. Deduplicate waiter close candidates with
  identity checks, not a set that assumes protocol objects are hashable.
- If immutable policy constants become necessary, put them in
  `weft/_constants.py`. This design needs no new timeout or polling constant.
- Broad catches need the repository's defensive pragma and a real boundary
  rationale. Prefer the exact exceptions listed above.
- Do not split `multiqueue_watcher.py` for size. Membership, waiter ownership,
  active scheduling, and dispatch share live state and are cohesive.

## 8. Detailed Design

### 8.1 Upstream `PollingStrategy` seam

Add this public method near `detach_activity_waiter()` in
`../simplebroker/simplebroker/watcher.py`:

```python
def replace_activity_waiter(
    self,
    activity_waiter: ActivityWaiter | None,
) -> ActivityWaiter | None:
    """Replace the live activity waiter without closing the displaced waiter."""
```

Required semantics:

- This is an owner-confined live replacement API. Its docstring must say the
  caller serializes it with `wait_for_activity()`, `start()`, `close()`, and
  other replacements. Do not claim it is safe to call concurrently with a
  waiter wait.
- If `activity_waiter is self._activity_waiter`, return `None` and change
  nothing. The caller must not receive the still-installed object and close it
  accidentally.
- Otherwise detach the old waiter using the existing ownership/state rules,
  install the replacement, reset `_check_count`, clear native pending/burst
  state belonging to the displaced waiter, and reschedule the native idle poll
  from an initial state.
- The method is exception-atomic and invokes no waiter, callback, provider, or
  close method. Perform the only plausibly raising internal scheduling step
  before detaching the old waiter. After the first state mutation, the method
  contains only non-raising internal assignments. A raised exception therefore
  leaves the old waiter and all strategy state intact.
- Preserve `_data_version_provider`, `_data_change_callback`,
  `_local_activity_pending`, `_local_activity_pending_for_drain`, and
  `_local_activity_empty_check`.
- Return the displaced waiter without closing it. The embedder that created it
  owns close ordering.
- Passing `None` explicitly selects the existing polling fallback.
- Do not make `ActivityWaiter` mutable and do not modify PostgreSQL, Redis, or
  SQLite backend waiter implementations.
- Do not implement this by calling `start()`.

Use `detach_activity_waiter()` internally so native-state clearing remains one
implementation. Keep `start()`'s broader initialization semantics unchanged.

### 8.2 Private topology request

Add one private `_TopologyMutation` record in
`weft/core/tasks/multiqueue_watcher.py`. It carries only what the existing
public mutators already accept. Place it after `QueueRuntimeConfig`, where
`QueueMode` and the handler signatures already exist:

```python
@dataclass(slots=True)
class _TopologyMutation:
    action: Literal["add", "remove"]
    queue_name: str
    handler: Callable[[str, int, QueueMessageContext], None] | None = None
    mode: QueueMode = QueueMode.READ
    reserved_queue: str | None = None
    error_handler: Callable[[Exception, str, int], bool | None] | None = None
    priority: int = QUEUE_PRIORITY_NORMAL
    done: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None
```

Add `weakref`, `deque` from `collections`, `field` from `dataclasses`, and
`Literal` from `typing` to the existing import groups. Never store or catch
`BaseException` in a request; cancellation and interpreter-exit exceptions are
not caller result values.

Do not store broker objects in the request. The drive owner creates any new
`Queue` and waiter.

State added to `MultiQueueWatcher`:

- `_topology_lock: threading.RLock`. Reentrancy is required only because
  SimpleBroker's main-thread SIGINT handler can call `stop(join=False)` on the
  drive owner while a short topology section holds the lock.
- `_topology_pending: threading.Event` as the no-work fast path
- `_pending_topology_mutations: deque[_TopologyMutation]`
- `_topology_inflight_mutation: _TopologyMutation | None`, set when the owner
  dequeues and cleared only after that request is signaled
- `_topology_owner_thread: threading.Thread | None`
- `_topology_reserved_thread: threading.Thread | None`, used only between an
  accepted `run_in_thread()` call and that exact thread's owner claim
- `_topology_manual_wait_thread: threading.Thread | None`, set only for the
  duration of public direct/manual `wait_for_activity(timeout)`
- `_topology_stopping: bool`, initialized `False` and set permanently by public
  `stop()` under `_topology_lock`
- `_topology_sigint_critical: bool`, owner-thread-only state that is true from
  entry to Phase C through request cleanup
- `_topology_sigint_deferred: threading.Event`, set by the SIGINT handler only
  when `_topology_sigint_critical` is true

Initialize this state in `__init__` with the other processing state, after the
queue mappings exist and before the final `_ensure_multi_activity_waiter()`
call. Constructor-time helpers and accessors then always find the lock. Keep
cache reset/replacement helpers owner-confined. Do not rely on reentrancy for
ordinary control flow; it exists only for inherited signal-stop re-entry.

The existing strategy local hint is the wake mechanism. Do not wait on
`_topology_pending` from a second thread.

### 8.3 Public mutation flow

`add_queue()` and `remove_queue()` keep topology-independent argument validation
at their public boundary: handler and error-handler callability, mode/reserved
compatibility, and priority type. Duplicate-add and missing-remove checks depend
on serialized membership and therefore run only in the shared apply helper.
Then, under `_topology_lock`:

1. If `_topology_stopping` or `_stop_event.is_set()`, raise `RuntimeError`
   before queue, topology, waiter, or request effects. This check precedes the
   no-owner path, so mutation after a stopped drive cannot reopen resources.
2. If `_topology_manual_wait_thread` is not `None`, raise `RuntimeError` before
   effects. There is no drive loop that could service a queued request, and
   immediate mutation could close the waiter under its active `wait()`.
3. If both `_topology_owner_thread` and `_topology_reserved_thread` are `None`,
   apply through the shared immediate helper on the current thread before
   releasing the lock. No accepted drive can begin concurrently because drive
   reservation and owner claim use the same lock.
4. If the current thread is the drive owner, raise `RuntimeError` before any
   effect. The message must tell callers not to mutate from a watcher handler.
5. Otherwise append one request, set `_topology_pending`, release the lock,
   call `_strategy.notify_activity()`, and wait on `request.done`.
6. After wake, re-raise `request.error` if set; otherwise return `None`.

`threading.Event.wait()` is the completion wait. Do not add an arbitrary
timeout parameter or polling sleep. The owner `finally` path is responsible for
signaling every queued request on exit.

`notify_activity()` records the existing local hint but does not have to
interrupt a native waiter already inside its current bounded `wait()` call.
The request therefore may wait through one configured strategy interval, plus
normal jitter, before the owner reaches the next safe point. This is expected
handoff latency, not a reason to add another wait path.

#### 8.3.1 Direct/manual wait flow

First extract the existing native-hint/pending/polling implementation into
`_wait_for_activity_body(timeout)`. It performs no standalone ownership
reservation. `BaseTask._wait_for_reactor_activity()` calls this helper directly
under `BaseTask`'s final lifecycle guard. The public standalone
`MultiQueueWatcher.wait_for_activity(timeout)` keeps the existing
`timeout is None or timeout <= 0` immediate return and wraps only positive
manual calls with the following ownership:

1. Acquire `_topology_lock`. If `_topology_stopping` is true or
   `_stop_event.is_set()`, return without creating a waiter. If an owner,
   reserved drive, or manual-wait thread already exists, raise `RuntimeError`
   before `_ensure_multi_activity_waiter()`.
2. Record `threading.current_thread()` in `_topology_manual_wait_thread`, then
   release the lock and run the existing native-hint/pending/polling wait body.
3. In `finally`, reacquire `_topology_lock`. If stop became requested, reset and
   close the cached waiter on this manual-wait thread before clearing
   `_topology_manual_wait_thread` by identity. Release the lock, then call
   `super().stop(join=False)` to complete the cleanup deferred by public stop.
   Without stop, retain the reusable cache, clear manual ownership, and do not
   call inherited stop.

Drive reservation/claim checks include `_topology_manual_wait_thread` and reject
before starting a drive. Topology mutation rejects rather than queues while a
manual wait is active. Public stop treats a manual-wait thread as live ownership:
it sets stop state and calls `_strategy.notify_activity()`, but does not reset
the cache or call inherited stop while manual ownership remains. This explicit
branch is required because `BaseWatcher.stop()` sees no drive `_thread` and may
therefore close the strategy immediately. The manual wait's `finally` owns
waiter close, clears manual ownership, and only then calls inherited stop with
`join=False` to finish strategy/thread-local/finalizer cleanup safely. Public
`stop()` does not join this caller-owned thread; tests and callers that place
manual wait in a thread must join that thread themselves.

Do not use MRO introspection, `isinstance(BaseTask)`, or a task-specific flag in
`MultiQueueWatcher`. The protected wait-body seam is the explicit boundary:
standalone public manual calls acquire topology-manual ownership; BaseTask's
canonical reactor calls the body under its own `_wait_active` ownership and
retains all finalization responsibility.

### 8.4 Owner declaration and safe points

Override `MultiQueueWatcher.run_in_thread()` only for atomic drive reservation:

- create the daemon `Thread` with `target=self.run_forever`, matching
  BaseWatcher;
- under `_topology_lock`, reject if an owner, reservation, or manual wait
  already exists or if `_topology_stopping`/`_stop_event` already records
  public stop;
  otherwise store that exact thread in `_topology_reserved_thread` and store
  its weak reference in BaseWatcher's `_thread` before calling `thread.start()`;
- hold the lock through `thread.start()` so the child cannot claim before the
  reference is published. If start raises, clear the reservation and weak
  reference by identity before re-raising;
- return the started thread. Do not add a readiness wait. Public mutation sees
  the reservation and queues safely during the short claim window.

Do not override `start()`: inherited `BaseWatcher.start()` already dispatches
to `self.run_in_thread()`, so it receives the same reservation behavior.

Override `MultiQueueWatcher.run_forever()` only as a thin lifecycle wrapper:

- under `_topology_lock`, accept the current thread if it is the reserved
  thread, clear that reservation, and claim `_topology_owner_thread`;
- for direct synchronous `run()`, require both reservation and owner to be
  empty, require no manual wait, and reject a previously stopped watcher before
  claiming the current thread and storing its weak reference;
- if a different reservation or owner exists, reject the second drive with
  `RuntimeError` before changing either state;
- do not copy retry, initial-drain, signal, or process-message loops;
- in `finally`, acquire `_topology_lock` on the same owner, reject remaining
  queued requests plus any unsignaled in-flight request, reset the multi waiter,
  clear owner state only if it still names the current thread, and clear
  `_thread` only if its weak reference still resolves to the current drive
  owner. These identity checks prevent one drive from clearing another's state.
  Tracking the in-flight request ensures even a test-only `BaseException` during
  Phase B cannot strand its synchronous caller. Leaving a weak reference to a
  synchronous caller thread would make later stop cleanup think the watcher is
  still running.

Owner mutation application is called only from these existing safe points:

1. `_create_activity_waiter()` before strategy startup/retry attaches a waiter;
2. `_has_pending_messages()` after strategy wait and before queue probing;
3. `_drain_queue()` before active membership iteration, covering initial drain.

The helper first checks `_topology_pending.is_set()` and returns without taking
the lock on the common no-mutation path. Do not override
`BaseWatcher._process_messages()` or `_run_with_retries()`.

#### 8.4.1 Main-thread SIGINT behavior

Synchronous `run()` on the main thread installs BaseWatcher's SIGINT handler.
That handler normally calls `self.stop(join=False)` and raises
`KeyboardInterrupt`. A signal can arrive at any Python bytecode, including
after strategy waiter ownership changes but before topology publication.

Override `_sigint_handler(signum, frame)` with this narrow rule:

- when `_topology_sigint_critical` is false, delegate to
  `super()._sigint_handler(...)` unchanged. `_topology_lock` is an `RLock`, so
  inherited stop can re-enter a short Phase A lock section without deadlock;
- when `_topology_sigint_critical` is true, do not acquire `_topology_lock`, call
  `stop()`, or raise inside the half-committed window. Log receipt, set
  `_topology_sigint_deferred`, set `_stop_event`, call
  `_strategy.notify_activity()`, and return;
- set `_topology_sigint_critical = True` immediately before Phase C lock
  acquisition. Keep it true through publication or rollback, old/candidate
  cleanup, request signaling, and in-flight-slot clearing;
- exit through one `_finish_topology_sigint_critical()` helper. It first sets the
  critical flag false. If no deferred signal exists, return. If one exists,
  clear its Event, set `_topology_stopping = True` under `_topology_lock`, and
  raise `KeyboardInterrupt`. A later signal after the flag clears delegates to
  inherited behavior. The transaction's
  outermost `finally` calls this helper on success, ordinary error, and
  `BaseException`, after request/resource cleanup.

Thus SIGINT before Phase C aborts normally through inherited stop. SIGINT during
Phase C is delivered only after one consistent result: stop observed before
replacement rejects the mutation, while a signal after replacement starts lets
that already-committing mutation publish and signal success before
`KeyboardInterrupt` propagates. The strategy remains the only standalone-drive
background wait path. Do not suppress SIGINT or convert it to a normal return.

The contract and subprocess test cover one SIGINT and any repeated signals that
arrive while `_topology_sigint_critical` is still true (the Event coalesces
them). They do not claim to repair nested SIGINT that re-enters inherited
`BaseWatcher.stop()` while its private non-reentrant `_stop_lock` is already
held. That is an upstream SimpleBroker signal-stop concern. Do not cite the
topology `RLock` as proof for `_stop_lock` re-entry.

### 8.5 One-request transaction

For each request, in deque order, use three phases. Backend resource creation
must not hold `_topology_lock`, so public stop can linearize promptly.

**Phase A: snapshot under `_topology_lock`**

1. Dequeue one request. If the deque becomes empty, clear `_topology_pending`;
   a later enqueue sets it again. Store the request in
   `_topology_inflight_mutation` before releasing the lock.
2. Recheck `_topology_stopping` and `_stop_event`. If stop won, fail and signal
   the request without effects.
3. Validate duplicate add or missing remove against `_queues`.
4. Capture a shallow base mapping, `_queue_generation`, the prior cached waiter
   by identity, and `strategy_had_native = self._strategy.uses_native_activity()`.
   Derive `prior_installed_waiter` as the prior cached waiter when native is
   true, otherwise `None`. The cache/strategy invariant requires a non-`None`
   cached waiter whenever native is true; treat violation as an internal fatal
   defect and test the invariant. Then release `_topology_lock`.

**Phase B: prepare on the drive owner without `_topology_lock`**

5. For add, create one real `Queue` using the current shared target/config and
   build its `QueueRuntimeConfig`. For remove, do not add explicit close, anchor
   reuse, or other queue-facade lifecycle policy.
6. Build a candidate mapping from the snapshot and requested delta. Build the
   exact candidate activity signature through the shared
   `_activity_wait_configs(mapping=...)` path.
7. Prebuild every scheduling object that publication needs: next generation,
   candidate active-name list with a removed name filtered out, and the next
   `itertools.cycle`. No list, tuple, iterator, Queue, or waiter allocation may
   occur after strategy replacement starts.
   A successful add also prebuilds publication state that forces one
   authoritative inactive-queue discovery on the immediate post-commit drain.
   This covers a message already pending on C before C's replacement waiter was
   registered; no second write or timer expiry may be required.
8. If the candidate activity list is empty, set signature to `()` and candidate
   waiter to `None` without calling `create_activity_waiter_for_queues([])`.
   Otherwise call the factory on the drive owner. Expected unavailability
   yields `None` and polling fallback.

Track four distinct local ownership facts from this point:

- `candidate_waiter_installed`: set immediately after
  a distinct candidate becomes strategy-owned. Once true, generic rollback
  must never close the candidate directly because the strategy owns it.
- `strategy_changed`: set only when the candidate identity differs from the
  captured installed waiter identity. The released API's same-object exact
  no-op returns `None`, which is otherwise ambiguous with a normal
  polling-to-native transition. Rollback restores only when this flag is true.
- `candidate_rollback_owned`: true only for a non-`None` waiter object that is
  not the captured prior cached waiter. Classify this immediately after factory
  return. A candidate identical to the prior cache/installed waiter is
  pre-existing watcher/strategy-owned state and must never be closed by Phase B
  abort, stop-before-commit, or rollback cleanup.
- `topology_published`: set only after all prebuilt state is assigned. A newly
  opened add Queue is rollback-owned while false and mapping-owned while true.

An outer `try/finally` uses these facts. Before waiter installation, it closes
only genuinely rollback-owned Queue/waiter resources on any exit. After waiter installation
but before publication, it first restores the displaced strategy waiter with
the same exception-atomic replacement API. Direct candidate cleanup is forbidden
while `candidate_waiter_installed` is true. On successful restoration, close
the waiter returned by restoration only when `candidate_rollback_owned` is
true, then immediately clear both `candidate_rollback_owned` and
`candidate_waiter_installed` so the outer `finally` cannot close it again. If
restoration returns the prior cached waiter, retain it unclosed as the restored
pre-transaction cache because it was pre-existing watcher-owned state. Close
the unpublished add Queue separately. It never closes a still-installed waiter.
If restoration itself violates its exception-atomic contract, leave the
installed waiter open, fail-stop the drive, and re-plan rather than guessing at
private state. This rollback rule does not change successful-remove facade
lifetime.

**Phase C: commit under `_topology_lock`**

9. Set `_topology_sigint_critical = True`, then acquire `_topology_lock`.
10. Verify the current thread is still `_topology_owner_thread` and that
   `_queue_generation` still equals the captured generation. A mismatch is an
   internal invariant failure. Release the lock, close candidate-only resources,
   fail/signal the request, and re-raise into inherited retry handling.
11. Recheck `_topology_stopping` and `_stop_event`. If stop won while Phase B was
   building resources, release the lock, close candidate-only resources, and
   fail/signal the request without replacement.
12. Compare the candidate identity with the captured installed waiter identity,
    then call `self._strategy.replace_activity_waiter(candidate_waiter)` and
    retain the returned strategy waiter by identity. Set `strategy_changed`
    only for a distinct identity and set `candidate_waiter_installed` only when
    the strategy now owns a distinct non-`None` candidate. This includes the
    safe point immediately before first strategy startup. Do not add or infer a
    separate "strategy started" flag, and do not treat a `None` return alone as
    proof of either no-op or displacement.
13. Publish only prebuilt references and scalar values: candidate mapping, next
    generation, signature, cached waiter, active-name list, and iterator. Set
    `topology_published = True` before releasing the lock. There is no allocating
    or externally callable operation between replacement and this flag.

If `_publish_topology_locked()` raises after replacement, catch that failure
inside the Phase C lock. Restore the displaced waiter through
`replace_activity_waiter()` before releasing the same lock whenever
`strategy_changed` is true. Apply the same ownership consumption rule there:
close the waiter returned by restoration only if `candidate_rollback_owned` is
true, then clear both candidate ownership flags before leaving the lock; never
close a returned prior cached waiter. Then release the lock and close remaining
rollback-owned candidate resources. This prevents public stop from linearizing
between failed publication and restoration and forbids reinstalling an old
waiter after a stop-first outcome. A same-object no-op performs no restoration
and leaves the pre-existing waiter installed and unclosed.

After commit, without `_topology_lock`, build an identity-deduplicated close
list from the prior cached waiter and the strategy-displaced waiter, excluding
`None` and the installed candidate. Close each old waiter once. This covers the
first-start case where the cached waiter was never installed in the strategy
and steady state where both references name the same waiter. The defensive
backend-cleanup helper debug-logs any ordinary `Exception`; it does not retry
because the protocol does not promise safe retry after partial failure. Signal
the request only after required candidate/old-waiter cleanup finishes, then clear
`_topology_inflight_mutation` by identity under the lock. Every ordinary
`Exception` path must likewise store its caller result, signal, and clear the
in-flight slot before it continues or re-raises. Then exit
`_topology_sigint_critical` through the helper in 8.4.1, which may deliver a
deferred `KeyboardInterrupt` only after ownership is consistent.

Before any drive reservation or owner exists, immediate mutation remains one
locked synchronous path: validate, create/build, invalidate and close the
unbound cached waiter, publish, and return. The next direct wait or strategy
start creates the current waiter. This pre-drive path may hold the lock during
Queue construction; no drive exists yet, and avoiding a second reservation
state is the smaller design. Successful removal retains current
facade-lifetime behavior in both paths. The empty-membership shortcut is shared:
`{A} -> {}` publishes signature `()`, installs/caches `None`, clears active
scheduling, and never calls the multi-queue factory with an empty list. A later
add is an ordinary new generation and may restore native waiting.

If candidate waiter creation returns `None` or raises an expected availability
error, Phase C replaces the old waiter with `None`; the topology still commits.
If replacement fails under its upstream exception-atomic contract, release the
lock, close candidate-only resources, leave the old mapping and waiter
published, fail/signal the request, and re-raise on the owner into inherited
retry handling. Do not strand the requesting thread while triggering retry
behavior. If evidence shows replacement changed strategy state before raising,
stop and re-plan; Weft must not reconstruct private `PollingStrategy` state.

Successful add publication must also mark the next pending scan authoritative
(for example via `_pending_messages_precheck_confirmed = True`). This is a
dispatch correctness requirement, not a native-wait optimization: work that
predates waiter registration must be visible on the immediate post-commit
turn.

The mutation linearization point is publication in Phase C while holding
`_topology_lock`. The public stop linearization point is setting
`_topology_stopping` while holding the same lock. The lock order decides the
race: if stop writes its flag first, the mutation fails without replacement; if
the owner publishes first, the mutation succeeds and stop follows. A raw caller
that directly sets the injected `_stop_event` is only a shutdown hint, not the
public sequencing API; the owner still aborts when it observes that event.

### 8.6 Stop and close ordering

Change `MultiQueueWatcher.stop()` so waiter cleanup depends on whether an
accepted drive exists:

- acquire `_topology_lock`, set `_topology_stopping = True`, and determine
  whether `_topology_owner_thread`, `_topology_reserved_thread`, or
  `_topology_manual_wait_thread` exists. This is the public stop linearization
  point and is idempotent;
- if manual ownership exists, record stop directly by setting `_stop_event` and
  calling `_strategy.notify_activity()`, then return without calling inherited
  stop. `BaseWatcher.stop()` treats a missing drive `_thread` as permission to
  close the strategy, so delegating here could close an attached cached waiter
  underneath the caller's active `wait()`. The manual wait's `finally` performs
  the deferred inherited cleanup described below;
- if no owner, reservation, or manual wait exists, reset the cached waiter
  before inherited stop while still serialized. `_reset_multi_activity_waiter()` must clear its
  cache fields before close, detach the expected waiter from the strategy, and
  close it once. Inherited `PollingStrategy.close()` then sees no waiter. This
  ordering also covers a protected pre-start `_start_strategy()` that attached
  the cached waiter without a recorded drive and prevents later stop from
  retrying a close that raised;
- release `_topology_lock` before inherited stop;
- call inherited stop without holding `_topology_lock`; it records the stop
  event, notifies the strategy, and may join the owner. Holding the topology
  lock across that join would deadlock owner finalization;
- a live owner exits its wait/turn and closes waiter state from the
  `run_forever()` wrapper's `finally`;
- a live manual owner exits its wait, reacquires `_topology_lock`, resets and
  closes the cached waiter, clears `_topology_manual_wait_thread` by identity,
  releases the lock, and calls `super().stop(join=False)`. At that point
  inherited strategy cleanup cannot close the active waiter because the wait
  has returned and the cache has already been detached. This finalization must
  run even if the wait body exits through an exception;
- after inherited stop returns, try `_topology_lock.acquire(blocking=False)`.
  If it is unavailable, return and let the owner finish cleanup; do not defeat
  inherited join timeout by blocking on owner finalization. If acquired, reset
  only a still-present cache when owner/reservation/manual ownership is now gone,
  then release in `finally`. If a live owner remains because `join=False` or the
  join timeout elapsed, or a manual wait remains, leave cleanup to that owner;
- idempotent repeated stop must not duplicate waiter close;
- this plan does not redesign BaseTask finalization or queue-facade cleanup.

Never close the currently installed waiter before it is detached or replaced.

### 8.7 Private helper map

Use these local helpers so the thread rules are visible and add/remove logic is
not duplicated:

- `_submit_topology_mutation(request) -> None`: public-mutator tail. It performs
  the stop/owner checks under `_topology_lock`, applies immediately when there
  is no owner, reservation, or manual wait, or enqueues, notifies, waits, and
  re-raises the request error.
- `_validate_topology_mutation(mapping, request) -> None`: pure duplicate,
  missing-name, and add-field invariant checks against the supplied mapping.
  Both immediate and owner paths call it before opening a Queue.
- `_open_topology_add_queue(request) -> Queue`: the one direct-`Queue`
  construction path for dynamic add. It uses the watcher's shared target,
  persistence, and config, then calls `_detach_queue_stop_event()`. Immediate
  and owner paths choose which thread/lock context calls it; they do not copy
  construction code.
- `_build_candidate_topology(mapping, request, *, added_queue)`: one
  deterministic mapping/config builder. It shallow-copies the supplied mapping,
  creates the add `QueueRuntimeConfig` from the already-opened `added_queue` or
  removes the requested name, and returns the candidate. It does not publish,
  close, wait, or touch strategy state.
- `_apply_topology_mutation_immediately_locked(request) -> None`: pre-drive path
  only. It requires no owner/reservation and `_topology_lock`; it uses the two
  shared helpers, performs current caller-owned Queue construction/cache
  invalidation, and publishes synchronously.
- `_apply_topology_mutation_on_owner(request) -> None`: implements the three
  phases in 8.5. It is called only by the recorded owner and deliberately drops
  `_topology_lock` during Queue/waiter creation and old-resource close. Its two
  ownership flags control rollback; no generic cleanup may close an installed
  candidate waiter.
- `_publish_topology_locked(...) -> None`: requires `_topology_lock` and accepts
  only Phase B's prebuilt mapping, scalar, list, iterator, signature, and waiter
  references. It performs assignments only, sets no ownership flag itself, and
  calls no external code. Keeping publication named makes the post-replacement
  failure test precise without mocking the drive loop.
- `_apply_pending_topology_mutations() -> None`: safe-point entry. It uses the
  event fast path, then delegates requests in deque order to the owner
  transaction. Expected caller/resource failures complete only that request;
  unexpected defects complete that request and are re-raised to inherited retry
  handling.
- `_reject_unfinished_topology_mutations_locked(reason: str) -> None`:
  finalization helper. It requires the lock, stores a fresh `RuntimeError` with
  the same reason on an unsignaled in-flight request and each queued request,
  signals each event, clears the in-flight slot/deque, and clears
  `_topology_pending`.
- `_activity_wait_configs(mapping=...)`: extend the existing helper with an
  optional mapping argument. The default remains `self._queues`; candidate
  waiter construction passes the candidate mapping. Do not add a second
  membership-filter implementation.
- `_close_activity_waiter_once(waiter) -> None`: installed/displaced-waiter close
  boundary used by cache reset and replacement. It catches broad `Exception`
  with the repository's explicit defensive backend-cleanup comment, logs once,
  and never retries.
- `_close_candidate_resource_once(resource) -> None`: rollback-only boundary for
  an unpublished `ActivityWaiter` or newly opened add `Queue`. It catches broad
  `Exception` with the repository's explicit defensive rollback comment,
  debug-logs, and never masks the original request/drive failure. It never runs
  for successful remove or a published add.
- `_finish_topology_sigint_critical() -> None`: implements the final handoff in
  8.4.1 and is the only code that converts a deferred SIGINT back into
  `KeyboardInterrupt`.
- `_finish_manual_wait(thread) -> bool`: requires no drive owner, resets the
  cached waiter when stop is set, and clears manual ownership only when it still
  names `thread`. It returns whether stop cleanup was deferred. The manual
  wait's outer `finally` calls `super().stop(join=False)` after this helper
  releases `_topology_lock` when the return is true; never call inherited stop
  while holding `_topology_lock`.

Keep these helpers private in the existing module. This is local decomposition
of one state machine, not a reusable topology framework.

## 9. Files to Touch

### 9.1 Released SimpleBroker dependency: read-only

Read for contract and acceptance context, but do not modify:

- `../simplebroker/simplebroker/watcher.py`
- `../simplebroker/tests/test_watcher.py`
- `../simplebroker/README.md`
- `../simplebroker/CHANGELOG.md`
- `../simplebroker/simplebroker/_backend_plugins.py`
- `../simplebroker/simplebroker/ext.py`
- `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/runner.py`
- `../simplebroker/extensions/simplebroker_pg/tests/test_pg_notify.py`

No upstream or backend edit is allowed from this Weft plan. Stop and re-plan if
the released contract is insufficient; do not patch a sibling checkout as a
shortcut.

### 9.2 Weft spec-promotion and implementation slices

Files to modify:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/07-System_Invariants.md`
- `weft/core/tasks/multiqueue_watcher.py`
- `weft/core/tasks/base.py`
  - change only `_wait_for_reactor_activity()` to call the protected shared
    broker-wait body; retain final lifecycle ownership and fixed topology
- `tests/tasks/test_multiqueue_watcher.py`
- `tests/helpers/multiqueue_sigint_probe.py`
  - subprocess-only main-thread probe for deterministic SIGINT during
    post-replacement publication
- `tests/conftest.py`
  - move the existing `thread_exception_guard` fixture verbatim from
    `tests/tasks/test_task_execution.py` so both background-drive suites reuse
    it; do not invent a second fixture.
- `tests/tasks/test_task_execution.py`
  - delete only the moved local fixture, retain its existing uses, and add one
    regression proving BaseTask wait/stop finalization remains BaseTask-owned
- `README.md`
- `CHANGELOG.md`
- `pyproject.toml` and `uv.lock` only to verify the existing released floors;
  do not bump or regenerate them unless a red metadata test proves drift
- `tests/system/test_optional_extras.py`
  - add a packaging-metadata test that parses the root dependency and proves
    its minimum is at least `5.3.0`
- `docs/plans/README.md`
- this plan's execution, deviation, and review records

Read but do not modify without a new red test and an explicit deviation:

- `weft/core/manager.py`
- `weft/core/launcher.py`
- `weft/commands/interactive.py`
- any PostgreSQL backend implementation under `../simplebroker/extensions/`

## 10. Proposed Spec Delta

Promotion strategy: **A, in-file text before link claims**.

| Spec file | Strategy | Sections touched |
| --- | --- | --- |
| `docs/specifications/01-Core_Components.md` | A | [CC-2.1] current role and dynamic-topology ownership |
| `docs/specifications/04-SimpleBroker_Integration.md` | A | [SB-0.4] watcher integration contract |
| `docs/specifications/07-System_Invariants.md` | A | [QUEUE.7] deferral removal; new [QUEUE.8] |

### 10.1 `docs/specifications/01-Core_Components.md` [CC-2.1]

Add after the current-role bullets and before "Why this exists":

> Standalone `MultiQueueWatcher` topology may change at runtime, but a running
> watcher's drive owner alone applies membership and waiter effects. Before a
> drive owner exists, `add_queue()` and `remove_queue()` apply
> synchronously. While `run()` or `run_in_thread()` owns the watcher, a foreign
> mutator submits a synchronous request and returns only after the owner applies
> or rejects it between wait and drain phases. A mutator invoked from the drive
> owner during dispatch is rejected before effects. The owner rebuilds the
> optional activity waiter from the exact committed activity-wait membership,
> replaces it through SimpleBroker's supported live strategy seam, and only
> then closes the displaced waiter. `PollingStrategy` remains the sole
> background wait path. Native-waiter unavailability commits the requested
> topology on bounded polling fallback; a later successful topology generation
> may attach native waiting again. The direct
> `MultiQueueWatcher.wait_for_activity(timeout)` API is for manual use only when
> no background drive is reserved or owned; one manual-wait owner excludes
> drive start and mutation, and closes its waiter after a concurrent stop.
> `BaseTask` does not enter that standalone manual-owner wrapper: its final
> reactor lifecycle calls the same protected broker-wait body under
> `BaseTask`'s existing drive/wait ownership guard.
> Public stop and topology commit
> share one serialization boundary: stop-first rejects the mutation without
> replacement; commit-first completes the mutation before shutdown. A
> main-thread SIGINT that lands inside waiter replacement is delivered as
> `KeyboardInterrupt` only after the request reaches a consistent published or
> rolled-back state. Empty membership uses polling state without invoking the
> multi-queue waiter factory with an empty list. `BaseTask` topology remains
> fixed and rejects both mutators.

In the implementation mapping, do not add new claims during promotion. In the
final traceability slice, add the named owner/mutation/rebind methods and
`tests/tasks/test_multiqueue_watcher.py` firing coverage.

### 10.2 `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]

Add after the bullet that says the fan-in waiter passes through the
SimpleBroker watcher lifecycle hook:

> - a running standalone `MultiQueueWatcher` changes native waiter membership
>   only on its drive owner. The owner creates a replacement with
>   `simplebroker.create_activity_waiter_for_queues(...)`, installs it through
>   the public owner-confined `PollingStrategy.replace_activity_waiter(...)`
>   seam, and closes the returned displaced waiter. The replacement seam keeps
>   strategy data-version callbacks and local wake hints intact and accepts
>   `None` for polling fallback. Weft does not call backend-specific listener
>   APIs and does not use `PollingStrategy.start()` as a live replacement.

### 10.3 `docs/specifications/07-System_Invariants.md` [QUEUE.7], [QUEUE.8]

Replace the final two sentences of [QUEUE.7], beginning "This does not remove
the standalone...", with:

> This does not remove the standalone `MultiQueueWatcher` dynamic-topology API;
> standalone mutation follows the drive-owner and exact-membership contract in
> [QUEUE.8], [CC-2.1], and [SB-0.4]. `BaseTask` remains construction-fixed and
> rejects runtime `add_queue()` and `remove_queue()`.

Add immediately after [QUEUE.7]:

> - **QUEUE.8**: a running standalone `MultiQueueWatcher` has one drive owner
>   for dynamic topology effects. Foreign `add_queue()` and `remove_queue()`
>   calls are synchronous requests applied in a deterministic linear order
>   between wait and drain phases; owner-thread mutation during dispatch is
>   rejected before effects. Public stop and topology commit use the same
>   serialization boundary, so no stop-first mutation can bind a waiter. Each
>   committed membership generation has one exact
>   activity-wait signature. The owner replaces the strategy's optional native
>   waiter before closing the displaced waiter, and stop cannot install a
>   waiter after close. Native-waiter creation
>   failure leaves the committed topology on bounded polling fallback without
>   changing delivery semantics; a later topology generation may restore native
>   waiting. Empty activity membership installs no waiter without calling the
>   factory with an empty list. Main-thread SIGINT cannot expose or close a
>   half-published waiter; `KeyboardInterrupt` is delivered after request and
>   ownership cleanup. A direct/manual wait temporarily owns its waiter and
>   excludes drive start and mutation; stop signals that wait but does not close
>   its waiter from another thread.

Do not add an `_Implementation mapping_` paragraph in the promotion slice. Add
it atomically with the Weft code, tests, and reciprocal module/method `Spec:`
backlinks.

## 11. Bite-Sized Red-Green Tasks

Execute active tasks in order. Tasks 2 through 4 are satisfied upstream
evidence and are not executable work. The active sequence is Task 0, Task 1,
then Tasks 5 through 12. Do not edit the sibling SimpleBroker repository.

### Task 0: Reconfirm and record the satisfied released dependency

Outcome: prove the plan targets the released upstream baseline Weft will
actually consume.

Files to read:

- all baseline files in section 4
- sibling `git status` and `git diff -- simplebroker/watcher.py`
- the installed and locked SimpleBroker versions in Weft

Commands:

```bash
git status --short
git rev-parse HEAD
git hash-object docs/specifications/01-Core_Components.md
git hash-object docs/specifications/04-SimpleBroker_Integration.md
git hash-object docs/specifications/07-System_Invariants.md

git -C ../simplebroker status --short
git -C ../simplebroker rev-parse HEAD
git -C ../simplebroker hash-object simplebroker/watcher.py
git -C ../simplebroker hash-object tests/test_watcher.py
```

Required actions:

- Update section 4 if hashes changed.
- Confirm the locked and installed versions are SimpleBroker 5.3.0 and
  simplebroker-pg 3.2.0.
- Confirm `PollingStrategy.replace_activity_waiter` is callable through
  `simplebroker.ext`.
- Confirm the dependency sources are package-index artifacts rather than an
  editable or sibling path.

Stop if:

- the lock or environment resolves an unpublished/editable checkout;
- SimpleBroker changed `ActivityWaiter` or strategy ownership materially;
- the current Weft spec removed dynamic standalone topology.

Done when the execution log names the new stable identifiers and any remaining
external blocker.

### Task 1: Independent review, then spec promotion

Outcome: the decided behavior is normative before code cites it.

Files to touch:

- the three specs in section 10
- their `## Related Plans` lists
- this plan's promotion baseline record

Red-green discipline:

- Before promotion, add or update plan/spec hygiene expectations only if an
  existing repository gate cannot see the new plan backlink.
- Apply the exact section 10 text. Do not improvise implementation claims.

Commands:

```bash
./.venv/bin/python -m pytest \
  tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -n 0 -q
git diff --check
```

Stop if independent review changes architecture or public semantics. Revise and
re-review this plan before promotion rather than silently drifting.

Done when all three specs carry the requirement text and backlinks, no mapping
claims say code exists, and a promotion baseline identifier is recorded.

### Task 2: SATISFIED UPSTREAM — do not execute

Historical evidence only: SimpleBroker 5.3.0 already contains the focused
replacement tests and public API. Do not edit or run red tests in the sibling
repository from this task. The original instructions below are retained only
to explain the dependency contract reviewed when this plan was authored.

Outcome: focused tests fail because `PollingStrategy` has no supported live
replacement API.

Files to touch:

- `../simplebroker/tests/test_watcher.py`

Tests to add under `TestPollingStrategy`:

1. `test_replace_activity_waiter_returns_displaced_waiter_without_closing`
   - use two tiny `ActivityWaiter` fakes;
   - start with A, replace with B;
   - assert returned object is A, neither waiter was closed, and B receives the
     next wait.
2. `test_replace_activity_waiter_same_object_is_noop`
   - install A, replace with A;
   - assert return is `None` and no native/local state changes.
3. `test_replace_activity_waiter_none_restores_polling_fallback`
   - install A, replace with `None`;
   - assert A is returned, `uses_native_activity()` is false, and data-version
     polling remains available.
4. `test_replace_activity_waiter_preserves_local_hints_and_callbacks`
   - seed local activity and installed data-provider/change callbacks;
   - replace A with B;
   - assert local hints and callback identities survive.
5. `test_replace_activity_waiter_clears_displaced_native_state`
   - seed native pending/burst state for A;
   - replace with B;
   - assert stale native state does not leak to B and initial idle scheduling is
     reset.
6. `test_replace_activity_waiter_is_exception_atomic`
   - install A and seed native/local state;
   - monkeypatch only the internal idle-schedule helper to raise before state
     mutation;
   - assert A remains installed and every seeded strategy field is unchanged.

Fakes are correct here because `ActivityWaiter` is the exact two-method external
protocol under test. Do not mock `PollingStrategy` itself.

Red command:

```bash
cd ../simplebroker
uv run pytest -n 0 tests/test_watcher.py \
  -k 'replace_activity_waiter' -q
```

Expected red: missing `replace_activity_waiter`, not a fixture or environment
failure. Capture the output in the execution log.

### Task 3: SATISFIED UPSTREAM — do not execute

Historical evidence only: the released implementation uses direct
exception-atomic field publication after precomputing its idle deadline. The
original proposed implementation notes below are superseded by the released
source and must not be followed.

Outcome: Task 2 tests pass with the smallest public method described in 8.1.

Files to touch:

- `../simplebroker/simplebroker/watcher.py`
- `../simplebroker/README.md`
- `../simplebroker/CHANGELOG.md`

Implementation rules:

- reuse `detach_activity_waiter()`;
- preserve data-version providers, callbacks, and local hints;
- schedule the next native idle poll before detaching so the valid-input path
  is exception-atomic; invoke no embedder or waiter callbacks;
- do not close the returned waiter;
- do not call `start()`;
- do not add locks or claim concurrent replacement safety;
- do not modify backend waiters or backend API versioning.

Green and neighboring commands:

```bash
cd ../simplebroker
uv run pytest -n 0 tests/test_watcher.py \
  -k 'replace_activity_waiter or detach_activity_waiter or start_does_not_close_same_activity_waiter or start_closes_replaced_activity_waiter' -q
uv run pytest -n 0 tests/test_watcher.py \
  tests/test_watcher_race_conditions.py tests/test_activity_waiter_api.py -q
uv run ruff check simplebroker/watcher.py tests/test_watcher.py
uv run ruff format --check simplebroker/watcher.py tests/test_watcher.py
uv run mypy simplebroker bin/release.py
```

Stop if the tests require a mutable `ActivityWaiter`, a backend change, or a
second strategy wait path. Those are materially different designs.

Done when the public method, docs, focused tests, neighboring watcher tests,
lint, format, and mypy are green on the released-source baseline plus this
narrow upstream change.

### Task 3A: SATISFIED UPSTREAM — do not execute

Historical evidence only: upstream review and release clearance completed in
the SimpleBroker repository before 5.3.0 publication.

Outcome: the first irreversible checkpoint is reviewed code, not publication.

Files to review:

- `../simplebroker/simplebroker/watcher.py`
- `../simplebroker/tests/test_watcher.py`
- `../simplebroker/README.md`
- `../simplebroker/CHANGELOG.md`
- the untouched backend waiter protocol and PostgreSQL waiter files from 9.1

Required actions:

- freeze the upstream HEAD, status, focused diff, and touched-file blob hashes
  in this plan's execution log;
- give an independent reviewer the frozen identifiers, sections 7.1 and 8.1,
  Task 2's red evidence, and Task 3's full green/static-check output;
- require the reviewer to inspect exception atomicity, same-object behavior,
  preserved callbacks/local hints, native-state reset, displaced-waiter
  ownership, lack of concurrent-safety overclaim, and the absence of backend
  protocol changes;
- resolve every finding, rerun Task 3's commands, freeze new identifiers, and
  re-review after any production-code change.

The review must return a recorded `READY` against the final frozen identifiers.
Do not tag, publish, or start Task 4 before that clearance. Documentation-only
corrections after clearance still require focused review if they alter the
public ownership contract.

### Task 4: SATISFIED UPSTREAM — do not execute

Historical evidence only: SimpleBroker 5.3.0 and coordinated extension 3.2.0
artifacts are published and selected by Weft's lock. The old placeholder probe
below must not be rerun from the source checkout; Task 0 records the installed
and locked evidence instead.

Outcome: Weft can declare a real minimum version rather than depending on a
sibling checkout by accident.

Files to read:

- `../simplebroker/pyproject.toml`
- upstream release notes/workflow
- Weft `pyproject.toml` and `uv.lock`

Required actions:

- Have the upstream maintainer select and publish the version. Do not invent a
  version number in Weft.
- Record the first published core version containing
  `replace_activity_waiter()` as `<SB_REBIND_VERSION>` in this plan's execution
  log.
- Confirm the existing `simplebroker-pg>=3.1.0` remains backend-API compatible.
  No PG extension bump is required unless upstream's release tooling says so.

Verification probe from the Weft repository root. Replace the placeholder with
the exact version recorded above. This command creates an isolated temporary
environment, disables the repository `PYTHONPATH`, fetches the exact version
from the configured package index, and rejects editable/direct-URL metadata. It
must not use `./.venv`, the Weft project, or `../simplebroker`:

```bash
SB_REBIND_VERSION="REPLACE_WITH_RECORDED_VERSION"
PYTHONPATH= SB_REBIND_VERSION="$SB_REBIND_VERSION" \
uv run --isolated --no-project \
  --with "simplebroker==$SB_REBIND_VERSION" \
  python - <<'PY'
import json
import os
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path

expected = os.environ["SB_REBIND_VERSION"]
assert expected != "REPLACE_WITH_RECORDED_VERSION"

distribution = metadata.distribution("simplebroker")
assert distribution.version == expected

direct_url = distribution.read_text("direct_url.json")
if direct_url is not None:
    direct_url_data = json.loads(direct_url)
    assert not direct_url_data.get("dir_info", {}).get("editable", False)
    assert "url" not in direct_url_data

spec = find_spec("simplebroker")
assert spec is not None and spec.origin is not None
origin = Path(spec.origin).resolve()
repository = Path.cwd().resolve()
assert repository not in origin.parents
assert repository.parent / "simplebroker" not in origin.parents
assert "site-packages" in origin.parts

from simplebroker.ext import PollingStrategy

assert hasattr(PollingStrategy, "replace_activity_waiter")
print(f"simplebroker={distribution.version} origin={origin}")
PY
```

Record the printed version and origin in this plan's execution log. Stop if the
exact version cannot be fetched, has editable or direct-URL metadata, resolves
outside the isolated environment's `site-packages`, or lacks the API. The Weft
dependency slice must not claim installable compatibility yet.

### Task 5: Share the background-thread exception fixture

Outcome: every new thread test fails on an uncaught watcher exception without
duplicating fixture code.

Files to touch:

- `tests/conftest.py`
- `tests/tasks/test_task_execution.py`
- `tests/tasks/test_multiqueue_watcher.py`

Actions:

- Move `thread_exception_guard` verbatim from
  `tests/tasks/test_task_execution.py` into `tests/conftest.py`.
- Add the required top-level `threading` import to `tests/conftest.py`; do not
  hide it in the fixture.
- Keep its yield/finally behavior and type annotations.
- Apply it to every new background mutation, stop-race, fallback, and PG test.
  The isolated SIGINT subprocess is the sole exception; its helper catches the
  expected `KeyboardInterrupt`, validates every child thread, and reports one
  machine-readable result to the parent.

Verification:

```bash
./.venv/bin/python -m pytest \
  tests/tasks/test_task_execution.py \
  tests/tasks/test_multiqueue_watcher.py -n 0 -q
```

Stop if moving the fixture changes unrelated test semantics. Revert the move
and use a small imported helper rather than copy the fixture.

### Task 6: Write Weft owner-thread mutation red tests

Outcome: tests prove the current foreign-thread mutation and stale strategy
behavior before production code changes.

Files to touch:

- `tests/tasks/test_multiqueue_watcher.py`

Extend `FakeWaiter` or add one focused blocking waiter that records:

- creation thread
- wait-entry and release events
- whether close overlapped wait
- close count and close thread
- wait results/errors

Use real `broker_env` queues and real `run_in_thread()`. Replace only
`create_activity_waiter_for_queues` for waiter-protocol evidence. The named
startup tests may wrap the real Queue constructor, gate a test subclass, or
instrument `threading.Thread.start` exactly as specified; they must not replace
queue/message behavior.

Red-green order: do not add this whole list at once. Execute tests 1 and 4 as
the tracer slice, implement their minimum owner/request path, then add one test
at a time in the listed order (grouping only a parameterized pair). Return to
green after each addition.

Red tests:

1. `test_background_add_queue_rebinds_exact_set_on_drive_owner`
   - start `{A, B}` and block inside old wait;
   - call public `add_queue(C)` from another thread;
   - assert the call does not return and old waiter is not closed while wait is
     blocked;
   - release the wait;
   - assert replacement `{A, B, C}` was created, bound, and old waiter closed
     once on the drive owner;
   - assert `list_queues()` is `{A, B, C}` when add returns.
2. `test_background_remove_queue_rebinds_exact_remaining_set`
   - start `{A, B, C}`;
   - remove B from a foreign thread;
   - assert the replacement signature is `{A, C}`, B is absent from applied
     topology, and the old waiter closes once on owner.
3. `test_background_topology_mutations_linearize_in_request_order`
   - gate several foreign calls so their enqueue order is explicit;
   - assert each completes only after its generation is fully rebound and the
     final set matches the recorded order;
   - do not assert scheduler-dependent order for truly simultaneous calls.
4. `test_background_mutation_from_handler_is_rejected_before_effects`
   - let a real queue handler call `add_queue()`;
   - assert `RuntimeError`, unchanged membership/generation/signature, and no
     new Queue or waiter.
5. `test_background_membership_errors_return_to_requesting_thread`
   - parameterize duplicate add and missing remove;
   - assert `ValueError` appears in the mutator thread while watcher remains
     running and processes later real work.
6. `test_background_mutation_racing_owner_start_completes_before_strategy_start`
   - use a wrapper around the real `Queue` constructor to block one pre-owner
     add during real queue construction;
   - invoke `run_in_thread()` from a separate starter thread while construction
     is blocked. Capture constructor-time Queue/waiter counts, then assert that
     call cannot reserve/start a drive, no additional strategy waiter is
     created after that baseline, and no handler runs before the add is
     released;
   - release construction and assert the add completes, the owner then starts
     with the exact resulting membership, and every thread exits;
   - delegate to the real constructor. Do not substitute a fake queue.
7. `test_background_mutation_after_drive_reservation_waits_for_owner_claim`
   - use a test subclass whose `run_forever()` gates before delegating to
     `super()`, leaving the thread reserved but not yet owner-claimed;
   - let `run_in_thread()` return, invoke add C from a mutator thread, and assert
     the call remains blocked with no caller-thread Queue/waiter construction;
   - release owner claim and assert C is created/rebound on that drive thread.
8. `test_background_rebind_before_first_strategy_start_closes_unbound_cached_waiter`
   - use a test subclass that only gates entry to
     `_create_activity_waiter()` before delegating to `super()`;
   - after the owner is claimed but before `PollingStrategy.start()`, enqueue
     add C, release the gate, and assert the candidate waiter is installed;
   - assert the constructor-created cached waiter that was never installed in
     the strategy closes exactly once on the owner. This prevents a first-start
     leak hidden by tests that cover only steady-state replacement.
9. `test_background_topology_second_drive_entry_is_rejected_without_replacing_owner`
   - start one real background drive and parameterize direct `run()` plus
     `run_in_thread()` as the second entry;
   - assert each call raises `RuntimeError` synchronously and no second thread
     or weak-reference overwrite is left behind;
   - prove the original watcher still processes a real message and stops
     cleanly.
10. `test_background_topology_thread_start_failure_rolls_back_drive_reservation`
    - monkeypatch only `threading.Thread.start`; inside the instrumented method,
      assert the attempted thread is already the exact reservation and weak
      reference, then raise;
    - assert `run_in_thread()` re-raises and clears the exact reservation and
      weak reference by identity;
    - restore real start, then prove the same watcher can start, process one real
      message, and stop cleanly.
11. `test_background_add_with_preexisting_message_forces_immediate_discovery`
    - create C and write one real message before runtime membership includes C;
    - set the inactive discovery deadline far in the future, start `{A, B}`, and
      add C from a foreign thread;
    - assert the owner commits C and handles that pre-existing message on the
      immediate post-commit drain without another C write or timer expiry.

Red command:

```bash
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py \
  -k 'background and (rebind or topology or mutation)' -n 0 -q
```

Expected red must show foreign close, no replacement, or missing ownership
serialization. A hang is not an acceptable red. Every wait/join has a bounded
test timeout and useful failure output.

### Task 7: Implement the private mutation state machine

Outcome: Task 6 passes without copying the SimpleBroker drive loop.

Files to touch:

- `weft/core/tasks/multiqueue_watcher.py`
- `weft/core/tasks/base.py` only when the protected wait-body regression is the
  active red slice

Actions:

- add `_TopologyMutation` and the reservation/owner/request core state from 8.2;
  defer `_topology_stopping` edge refinements and SIGINT fields until Task 8's
  firing tests require them;
- implement the core private-helper path from 8.7; factor current add/remove
  validation and immediate bodies so both pre-start and owner-applied paths
  reuse the same validation, Queue-open, and candidate-build logic;
- add the thin `run_in_thread()` reservation override and `run_forever()` owner
  wrapper from 8.4;
- extract `_wait_for_activity_body(timeout)` without changing its existing
  native/pending/poll behavior and change
  `BaseTask._wait_for_reactor_activity()` to call that helper under BaseTask's
  existing guard. Leave standalone public behavior otherwise unchanged in
  Task 7; add the manual-owner wrapper only when Task 8 tests 16 and 17 are red;
- apply requests only at the three safe points in 8.4;
- keep immediate mutation, drive reservation, and owner claim mutually
  exclusive through the same `RLock`; do not add a startup event or readiness
  wait;
- make `list_queues()` and `get_queue()` take a short locked snapshot;
- always use `replace_activity_waiter()` once an owner exists; do not add a
  separate strategy-started flag;
- preserve public return and exception behavior;
- keep the no-pending fast path lock-free through `_topology_pending`;
- add full-path `Spec:` docstrings for [CC-2.1], [SB-0.4], and [QUEUE.8] on the
  public mutators, owner apply helper, waiter replacement helper, and lifecycle
  wrapper;
- add a short inline ASCII ownership diagram near the request state because the
  cross-thread ordering is non-obvious and load-bearing.

Tasks 6 and 7 are one interleaved vertical sequence, not a horizontal
tests-then-code pair. For each numbered Task 6 behavior, write one firing test,
observe its intended red, implement only the corresponding Task 7 behavior,
return the focused selection to green, and only then add the next test.

Task 7 is the successful non-empty topology path only: drive reservation/claim,
FIFO request handoff, duplicate/missing errors, exact add/remove replacement,
first-start cached-waiter displacement, and owner-callback rejection. Do not yet
add manual-wait ownership, stop linearization, SIGINT deferral, empty membership,
factory-error fallback, Queue-open recovery, post-replacement restoration,
close-error policy, or fatal-exit cleanup. Those branches belong to Task 8 and
must be driven by its tests first. If a Task 8 assertion is already green solely
because the typed happy path handles `None` correctly, record it as an existing
characterization; do not introduce an artificial failure to manufacture red.

Do not:

- override `_process_messages()` or `_run_with_retries()`;
- import `simplebroker_pg`;
- add another thread or executor;
- change `BaseTask` beyond the one protected wait-body call and its focused
  lifecycle regression;
- add a public async request API.

Green command:

```bash
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py \
  -k 'background and (rebind or topology or mutation)' -n 0 -q
```

Stop if keeping the inherited strategy as the sole standalone-drive wait path becomes
impossible. Do not solve that by copying the watcher loop.

### Task 8: Write and implement fallback, stop, and resource red tests

Outcome: error and teardown paths are explicit, bounded, and owner-confined.

Files to touch:

- `tests/tasks/test_multiqueue_watcher.py`
- `tests/tasks/test_task_execution.py`
- `tests/helpers/multiqueue_sigint_probe.py`
- `weft/core/tasks/multiqueue_watcher.py`
- `weft/core/tasks/base.py`

Execute this task as four independent red-green micro-slices, not one large
test dump:

- **8A, resource/fallback transaction:** tests 1, 4, 5, 6, and the preserved
  direct contract in 9.
- **8B, stop and close ordering:** tests 2, 3, 7, 8, 12, and 13.
- **8C, owner/thread finalization:** tests 10 and 11.
- **8D, boundary acceptance:** tests 14 through 20.

For each micro-slice, write and run its tests to the intended red state, make
the smallest production change, and return that slice to green before starting
the next. Do not pre-implement later-slice behavior.

Red tests:

1. `test_background_rebind_unavailable_falls_back_then_later_generation_restores_native_wait`
   - parameterize the waiter factory returning `None` and raising an expected
     availability error for add C;
   - add succeeds, exact topology includes C, old waiter closes on owner, and
     strategy reports no native waiter;
   - a real write to C is processed through fallback;
   - a later add D with a successful factory binds `{A, B, C, D}`.
2. `test_background_mutation_racing_stop_never_binds_after_owner_close`
   - block the first request's waiter factory in Phase B, where the owner does
     not hold `_topology_lock`, then enqueue several more ordered mutations;
   - call public stop and prove it sets both `_topology_stopping` and
     `_stop_event` while the factory remains blocked; release the factory;
   - assert every mutator receives `RuntimeError`, no replacement binds, the
     old waiter closes once on owner, all threads exit, and
     `thread_exception_guard` is empty.
3. `test_mutation_after_stop_is_rejected_before_effects`
   - stop a never-run watcher and a previously driven watcher;
   - for each, assert add and remove raise `RuntimeError` without changing
     mapping, generation, cached waiter, or opening a candidate queue.
4. `test_background_queue_open_failure_preserves_old_generation`
   - wrap the real `Queue` constructor and raise `OSError` only for add C;
   - assert the requesting thread receives the error, the old mapping,
     generation, waiter, and handler behavior remain live, and no replacement
     or old-waiter close occurs.
5. `test_background_strategy_replacement_failure_fails_request_and_retries_drive`
   - use a `PollingStrategy` test subclass whose replacement method raises once
     before any state change, then delegates normally on the next drive;
   - assert the same error reaches the requesting thread, candidate-only
     resources close, the old mapping remains, and the inherited watcher retry
     later processes real work. Do not mock the drive loop.
6. `test_background_post_replace_publication_failure_restores_old_waiter`
   - use a test subclass whose `_publish_topology_locked()` raises one
     `RuntimeError` before assignments, after real strategy replacement;
   - assert rollback reinstalls the displaced old waiter, closes the returned
     candidate waiter and unpublished add Queue once on owner, preserves the old
     mapping/generation, signals the same error to the mutator, and lets inherited
     retry process later real work.
   - race public stop against the injected publication failure and assert stop
     cannot linearize until restoration under the same Phase C lock completes;
     no old waiter may be installed after stop-first.
7. `test_background_mutation_committed_before_stop_returns_success`
   - use a `PollingStrategy` test subclass that gates at the start of
     `replace_activity_waiter()`, after the owner acquired the Phase C commit
     lock but before the exception-atomic replacement changes state;
   - assert stop cannot set `_topology_stopping` while the transaction owns the
     lock, release replacement, and assert publication/mutation success occurs
     before stop linearizes;
   - assert stop then closes the installed replacement once.
8. `test_waiter_close_failure_is_logged_once_and_not_retried`
   - parameterize `RuntimeError` and `ValueError` from a waiter fake whose first
     close marks itself closed and then raises;
   - exercise stop twice after replacement and assert the failed close is
     logged but never called again. This encodes the lack of a safe post-error
     retry contract.
9. Preserve and update
   `test_queue_set_changes_close_stale_multi_queue_waiter` as the non-running
   direct/manual contract. It is not evidence for background owner rebind.
10. `test_owner_fatal_exit_signals_every_queued_mutator`
   - make the first request's Phase B waiter factory raise one test-only
     `BaseException` sentinel after that request is in-flight and several more
     mutations are queued;
   - run through a test-owned thread target that catches only that expected
     sentinel, so it does not become an uncaught thread failure;
   - assert the in-flight and every queued mutator receive `RuntimeError`, every
     request event is set, the in-flight/deque/event/owner state clears, the
     unpublished add Queue and installed waiter close on the owner, and the weak
     `_thread` reference no longer names the exited drive;
   - instrument that real unpublished Queue's close to raise `ValueError` after
     recording the call. Assert the rollback log appears but the original
     sentinel still escapes to the test wrapper; call the saved real close in
     teardown. Production code must not catch `BaseException`.
11. `test_synchronous_run_registers_and_clears_drive_thread_for_stop`
    - launch an outer test-owned `threading.Thread` whose target calls
      `watcher.run()`, sets `run_returned`, and then waits on a separate
      `release_outer_thread` Event;
    - apply one foreign mutation, call public `stop(join=True)`, and wait for
      `run_returned`. Assert owner state and `watcher._thread` are clear while
      the outer thread is intentionally still alive;
    - call stop again and prove it neither joins that live outer thread nor skips
      waiter cleanup, then release/join the outer thread.
12. `test_stop_join_timeout_does_not_block_on_owner_finalization_lock`
    - configure a fake installed waiter whose close announces entry and blocks;
    - call `stop(join=True, timeout=<small bounded value>)` from a test thread;
      public stop first linearizes normally, then drives the owner into that
      blocked finalizer while it holds `_topology_lock`;
    - assert a stop-return Event fires within a generous outer test timeout
      while waiter close is still blocked;
    - release close, join the owner, and assert final state is clear. Do not use
      a tight elapsed-time performance assertion.
13. `test_no_owner_stop_closes_attached_cached_waiter_once`
    - construct a watcher with a close-recording waiter and call the real
      protected `_start_strategy()` to establish the legal attached-cache,
      no-owner state;
    - make that waiter's first close raise, then call public stop twice;
    - assert pre-super reset detached it, close was attempted exactly once, cache
      fields stayed clear, and inherited strategy close did not retry it.
14. `test_background_remove_last_queue_uses_empty_waiter_shortcut_then_add_restores_native`
    - start `{A}` with a recording factory that fails the test if called with an
      empty list;
    - remove A and assert applied membership/active scheduling are empty,
      signature is `()`, strategy native use is false, and A's waiter closes
      once;
    - add B and assert exact `{B}` replacement restores native waiting and real
      B work is processed.
15. `test_main_thread_sigint_after_waiter_replace_finishes_consistent_commit`
    - add `tests/helpers/multiqueue_sigint_probe.py` and execute it with
      `[sys.executable, "-m", "tests.helpers.multiqueue_sigint_probe"]`, no shell,
      captured output, and a bounded subprocess timeout;
    - the probe uses real temporary SQLite queues plus protocol waiters. Its
      `PollingStrategy` subclass calls `super().replace_activity_waiter()`, then
      calls `signal.raise_signal(signal.SIGINT)` before returning the displaced
      waiter. Use the interpreter signal path, not `os.kill`, so Windows and
      POSIX exercise Python's installed handler consistently;
    - run `watcher.run()` on the subprocess main thread so SimpleBroker installs
      the real signal handler. A helper thread submits add C;
    - assert no deadlock, the mutator sees success, `{A, B, C}` publishes, old
      and candidate waiters each close once in correct ownership order,
      `KeyboardInterrupt` reaches the main caller only after request completion,
      all threads exit, and the subprocess returns zero with one JSON result;
    - skip only when `signal.SIGINT` or `signal.raise_signal` is unavailable,
      with an explicit reason.
16. `test_manual_wait_excludes_drive_start_mutation_and_second_manual_wait`
    - run public `wait_for_activity()` in a manual thread with a blocking waiter
      and assert it records that thread as temporary manual owner;
    - while `wait()` is active, assert `run_in_thread()`, add, remove, and a
      second manual wait each raise `RuntimeError` before Queue/waiter effects and
      never close the active waiter;
    - release the wait, join it, then prove a real drive can start and process a
      message, showing manual ownership cleared.
17. `test_stop_during_manual_wait_leaves_close_to_manual_owner`
    - call the real protected `_start_strategy()` first so the cached waiter is
      attached to the strategy in the legal no-drive state covered by test 13;
    - block that same waiter in a manual wait, call public stop from another
      thread, and assert stop signals but does not call inherited stop, strategy
      close, join, detach, or waiter close while the wait remains active;
    - release the wait and assert its `finally` detaches and closes the cached
      waiter exactly once on the manual thread, clears manual ownership, then
      runs inherited non-joining cleanup on that same thread;
    - assert the test can join the manual thread and a later stop is idempotent
      without retrying waiter close.
18. `test_same_waiter_replacement_publication_failure_preserves_installed_owner`
    - make the candidate factory return the exact waiter already installed in
      the strategy, then inject publication failure;
    - assert the released API's same-object no-op is not misread as a displaced
      `None`, no restore/detach occurs, and the pre-existing waiter remains
      installed and unclosed with the old topology.
    - parameterize a stop or injected Phase B failure before replacement and
      assert candidate rollback never closes that same pre-existing
      cache/strategy-owned waiter.
    - include the pre-first-start case where the factory returns the prior
      cached-but-not-installed waiter; on rollback it remains the restored
      pre-transaction cache and is not closed.
19. `test_base_task_reactor_wait_bypasses_standalone_manual_ownership`
    - drive one real BaseTask wait through its final public lifecycle wrapper;
    - assert `_wait_for_reactor_activity()` reaches the protected shared broker
      wait body without setting `_topology_manual_wait_thread` or invoking
      standalone manual-stop finalization;
    - race stop and assert `_wait_active` clears before BaseTask performs its
      existing one-time finalization, with no duplicate strategy/waiter close.
20. `test_drive_start_after_public_stop_is_rejected_before_thread_creation`
    - stop a never-run watcher, then parameterize `run_in_thread()` and direct
      `run()`;
    - assert both raise `RuntimeError` before constructing/starting a thread or
      changing owner/reservation/weak-reference state.

Implementation rules:

- creation failure is polling fallback, not mutation failure;
- stop-before-commit fails the mutation;
- log ordinary waiter-close exceptions once; do not retain or retry a partially
  closed waiter;
- preserve current successful-remove queue-facade lifetime behavior. Close only
  newly opened add candidates on rollback.

Verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py -n 0 -q
./.venv/bin/python -m pytest \
  tests/tasks/test_task_execution.py -k 'dynamic_queue_mutators or topology' \
  -n 0 -q
```

### Task 9: Add the real PostgreSQL LISTEN/NOTIFY acceptance test

Outcome: one real-backend test proves added and removed membership, not just
fake waiter call order.

Files to touch:

- `tests/tasks/test_multiqueue_watcher.py`

Test:

`test_postgres_background_dynamic_membership_rebinds_native_waiter`

Required shape:

1. Use `broker_env`, real `Queue` writers, real `MultiQueueWatcher`, and
   `run_in_thread()` under `BROKER_TEST_BACKEND=postgres`.
2. Wrap, but do not replace, the real
   `create_activity_waiter_for_queues(...)` result with a recording
   `ActivityWaiter` proxy. The proxy delegates real `wait()` and `close()` and
   records which replacement returned `True`. This is permitted instrumentation
   because the backend waiter remains real.
3. Start with A and B. Add C through the public foreign-thread method and wait
   for its synchronous return.
4. Write only to C. The C handler must assert that the `{A, B, C}` real waiter
   recorded a native `True` wake before handler entry. This avoids using elapsed
   time as the main proof.
5. Remove B. Write to B and establish a short bounded quiet window consistent
   with the upstream PG noise-filter test. Assert the `{A, C}` waiter did not
   record a B-driven wake and no B handler ran.
6. Write to C again and assert the `{A, C}` waiter wakes and C is processed.
7. Stop/join in `finally`; close writer queues; assert no thread exceptions.

Do not:

- fake Queue, the PostgreSQL runner, listener, or notification;
- assert only `uses_native_activity()`;
- treat a sleep as positive proof;
- inspect private `simplebroker_pg` listener registries from Weft tests.

Red/green command with automatic Docker provisioning:

```bash
. ./.envrc
./.venv/bin/python bin/pytest-pg \
  tests/tasks/test_multiqueue_watcher.py
```

The new test is in a module already classified `shared`. Import
`POSTGRES_TEST_BACKEND` and `active_test_backend` from
`tests.helpers.test_backend`; at test start call `pytest.skip(...)` unless
`active_test_backend() == POSTGRES_TEST_BACKEND`. Do not add a new marker or
module. Keep the backend-neutral dynamic membership behavior in the shared
tests from Tasks 6 through 8; this test adds only the real native-wake proof.

### Task 10: Verify the existing dependency floor and close documentation

Outcome: installed Weft cannot select a SimpleBroker without the API it calls.

Files to touch:

- `pyproject.toml`
- `uv.lock`
- `tests/system/test_optional_extras.py`
- `README.md`
- `CHANGELOG.md`
- the three specs' implementation mappings and related-plan backlinks
- `weft/core/tasks/multiqueue_watcher.py` reciprocal `Spec:` docstrings
- this plan's promotion baseline, execution log, and deviation log

Actions:

- preserve the already-selected `simplebroker>=5.3.0` and
  `simplebroker-pg>=3.2.0` floors; do not bump them again in this slice;
- verify `uv.lock` continues to resolve the exact published 5.3.0/3.2.0
  artifacts after implementation changes;
- add
  `test_simplebroker_floor_includes_dynamic_waiter_rebind_api()` to
  `tests/system/test_optional_extras.py`. Reuse `_minimum_dependency_version()`
  and `_version_tuple()`; assert the parsed root floor is greater than or equal
  to `5.3.0` rather than comparing a raw dependency string;
- update README's PostgreSQL connection-budget section to state that a running
  standalone dynamic watcher replaces its one fan-in waiter on its drive
  owner; do not imply BaseTask topology is dynamic;
- add an Unreleased changelog item for the fix and dependency floor;
- add final implementation mappings for [CC-2.1], [SB-0.4], and [QUEUE.8]
  atomically with reciprocal code/test backlinks;
- replace the old section 6.5 deferral references only where they would become
  false. Preserve the historical deferral record in the old plan's review log;
  add a follow-on link instead of rewriting history.

Commands:

```bash
. ./.envrc
uv sync --all-extras
./.venv/bin/python - <<'PY'
from importlib import metadata

from simplebroker.ext import PollingStrategy

print(f"simplebroker={metadata.version('simplebroker')}")
assert hasattr(PollingStrategy, "replace_activity_waiter")
PY
./.venv/bin/python -m pytest tests/system/test_optional_extras.py -n 0 -q
./.venv/bin/python -m pytest \
  tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -n 0 -q
```

Stop if lock resolution selects an unpublished/local-only SimpleBroker version
or changes unrelated dependencies materially. The installed-env probe confirms
that the existing floor remains effective; Task 0 records the package-index
lock evidence.

### Task 11: Run neighboring regression and stress gates

Outcome: prove the new state machine does not disturb static watchers, task
reactors, queue modes, priority, or stop behavior.

Commands:

```bash
. ./.envrc
./.venv/bin/python -m pytest \
  tests/tasks/test_multiqueue_watcher.py \
  tests/commands/test_interactive_client.py \
  tests/tasks/test_task_execution.py -n 0 -q

for run in 1 2 3 4 5 6 7 8 9 10; do
  ./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py \
    -k 'background and (rebind or topology or mutation)' -n 0 -q || exit 1
done

./.venv/bin/python bin/pytest-pg \
  tests/tasks/test_multiqueue_watcher.py \
  tests/commands/test_interactive_client.py
```

Required assertions across the suite:

- all three queue modes still fire;
- priority order and inactive discovery are unchanged;
- direct/manual wait still rebuilds after pre-start mutation;
- background strategy remains the only standalone BaseWatcher drive wait path;
- BaseTask rejects mutations;
- stop is idempotent and all test threads join;
- no `threading.excepthook` errors;
- SQLite fallback and real PostgreSQL native paths both pass.

Do not weaken timeouts or add sleeps to make stress green. Diagnose the
linearization or cleanup defect.

### Task 12: Final gates and closeout review

Outcome: current-tree evidence supports every completion claim.

The released dependency is immutable for this slice; do not rerun or modify the
sibling SimpleBroker tree. Run Weft gates from the Weft root:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/python bin/pytest-pg --all
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
./.venv/bin/ruff format --check weft tests
./.venv/bin/python -m pytest \
  tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -n 0 -q
git diff --check
```

Traceability probe:

```bash
uv --project ../backstitch run backstitch check \
  --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --format json
```

Save before/after JSON outside the repository. The current corpus has known
Backstitch debt. This slice must add no new diagnostics outside its intended
new-section promotion/linking lifecycle, and the final state must have no new
`SPEC_SECTION_MISSING`, `SPEC_MAPPING_RECIPROCAL_MISSING`, or
`CODE_BACKLINK_RECIPROCAL_MISSING` diagnostics for [CC-2.1], [SB-0.4], or
[QUEUE.8]. If `../backstitch` is unavailable, report that as an unpassed tooling
gate; do not install it as part of this change.

Finally run the independent completed-work review described in section 15.
Do not mark this plan completed until the locked dependency proof, real PG
test, full SQLite tests, full PG-compatible tests, static checks, spec hygiene,
and independent review are all current and green.

## 12. Test Coverage Matrix

```text
CODE PATHS                                              OBSERVABLE PROOF
[+] PollingStrategy.replace_activity_waiter
  |-- A -> B                                            old returned, neither closed, B waits
  |-- A -> A                                            no-op, no closeable return
  |-- A -> None                                         native off, polling intact
  |-- native state                                      old hints cleared
  |-- local/data-version state                          preserved
  `-- internal schedule failure                         old state unchanged

[+] MultiQueueWatcher.add_queue/remove_queue
  |-- no owner                                          immediate direct behavior retained
  |-- manual wait active                                start/mutation/second wait rejected
  |-- owner claim races immediate mutation              one lock; mutation or owner wins cleanly
  |-- reserved but not claimed                          mutation waits for exact owner
  |-- reserved thread fails to start                    reservation/reference rolled back
  |-- mutation before first strategy start              unbound cached waiter closed once
  |-- foreign caller + owner                            request blocks until exact rebind
  |-- second drive attempt                              rejected; original owner preserved
  |-- owner callback                                    pre-effect RuntimeError
  |-- duplicate/missing                                 caller gets error; watcher survives
  |-- Queue open failure                                old topology remains
  |-- candidate rollback                                unpublished Queue/waiter closed on owner
  |-- replacement failure                               request signaled; inherited retry continues
  |-- post-replace publication failure                  old waiter restored; candidate closed
  |-- waiter available                                  exact replacement set
  |-- waiter unavailable/fails                          polling fallback; topology commits
  |-- final remove                                      empty shortcut; no factory([])
  `-- later successful generation                       native wait restored

[+] Stop interleavings
  |-- mutation commit wins                              success, then normal close
  |-- stop wins                                         mutation fails, no post-close bind
  |-- queued request on owner exit                      caller unblocked with error
  |-- in-flight request on owner exit                   caller unblocked; candidate rolled back
  |-- synchronous run                                   drive thread reference cleared
  |-- main-thread SIGINT in commit                      defer, finish ownership, KeyboardInterrupt
  |-- no-owner attached cache                           detach/close once before inherited close
  |-- stop during manual wait                           manual owner closes after wait
  |-- join timeout during finalization                  stop returns; owner later clears
  `-- repeated stop                                     no duplicate close

[+] Real backend flows
  |-- SQLite                                            no native waiter; bounded fallback
  |-- PostgreSQL add C                                  real native True before C handler
  |-- PostgreSQL remove B                               B ignored by {A,C} waiter
  `-- PostgreSQL stop                                   watcher/test process exits cleanly
```

Every branch above needs a firing test. A structural assertion may support the
test, but it cannot replace the real background thread and broker observation.

## 13. Test Design Rules for This Slice

### Keep real

- `MultiQueueWatcher`
- `PollingStrategy`, except for the named subclass gates that override only
  `replace_activity_waiter()` before delegating or raising before effects
- `run_in_thread()` and stop/join lifecycle
- public direct/manual `wait_for_activity(timeout)` and its caller-owned thread
- broker target resolution
- `Queue` construction and all read/peek/move/has-pending behavior
- PostgreSQL runner, listener, notifications, and actual queue writes
- SQLite fallback

### Permitted fakes or instrumentation

- a tiny `ActivityWaiter` fake in pure strategy and owner-order tests because
  that two-method protocol is the boundary under test;
- a delegating recording proxy around the real PostgreSQL waiter;
- one saved-bound-method wrapper on a real unpublished add Queue to inject and
  clean up rollback-close failure;
- narrow wrappers around the real Queue constructor and `Thread.start`, plus
  test subclasses that only insert Events/errors at the named owner-claim,
  startup, replacement, or publication boundaries. All delegate to production
  behavior outside the injected gate;
- the subprocess-only main-thread SIGINT helper from Task 8. It still uses real
  queues and the production signal installation path.

### Forbidden mock-heavy substitutes

- fake Queue dictionaries instead of `broker_env`;
- mocked `add_queue()`/`remove_queue()` calls;
- direct calls to the private apply helper as the primary behavior proof;
- a fake PostgreSQL listener or runner;
- asserting a mocked method call without also observing membership, handler,
  fallback, or thread lifecycle;
- `sleep()` as the positive wake proof;
- private `simplebroker_pg` registry inspection from Weft tests.

### Timing discipline

- use Events for wait entry, release, mutation completion, and handler entry;
- every thread join and quiet-window assertion has a bounded timeout;
- capture `threading.excepthook`;
- assert ordering facts, not tight elapsed-time thresholds;
- the one removed-queue quiet window mirrors the upstream PG filter test and is
  negative evidence only. The positive C wake uses the real waiter's return
  event.

## 14. Rollout, Rollback, and Runtime Observation

### Rollout order

1. Start from the project-owner-selected published SimpleBroker dependency
   baseline. Do not condition this plan on unrelated lifecycle work.
2. Complete independent plan review and promote the Weft specs.
3. Implement and independently review the frozen additive SimpleBroker slice.
4. Land and release the reviewed SimpleBroker replacement API.
5. Verify the released API from Weft's isolated environment.
6. Land Weft tests and implementation.
7. Raise the Weft SimpleBroker floor and lockfile to the first release that
   includes the replacement API.
8. Run full SQLite and PostgreSQL Weft gates before release.

Old Weft works with the newer SimpleBroker, so upstream-first is backward
compatible. New Weft must not ship against an older SimpleBroker because it
calls the new public method.

### Rollback

- Revert the Weft mutation/rebind code and dependency-floor change together.
- Keeping the additive upstream SimpleBroker method is harmless to old Weft.
- No queue data, message IDs, schemas, payloads, or TaskSpecs need migration.
- Polling fallback remains the safe runtime mode if native waiter creation is
  unavailable.
- Do not roll back by retaining a stale fixed-set waiter; that recreates the
  defect.

### Runtime observation

The release proof is behavioral, not a new permanent metric:

- a standalone PG watcher remains native-backed after each applied membership
  generation;
- an added queue wakes and processes promptly through real notifications;
- a removed queue does not wake the new waiter;
- watcher and mutator threads terminate cleanly on stop;
- no new debug logs report repeated waiter rebuild or close failures.

Do not add a public topology status API or production counter for this slice.
If real production use later needs an SLO, open a separate observability plan.

## 15. Independent Review Loop

This is risky runtime and cross-repository work. Plan review, upstream
pre-release review, and completed-work review are all mandatory.

### Plan review

Use a reviewer that did not author the plan. A different agent family is
preferred; if only same-family reviewers are available, record that limitation.

Prompt:

> Read `docs/plans/2026-07-10-postgresql-dynamic-native-waiter-rebind-plan.md`,
> its `## Proposed Spec Delta`, and the current Weft and SimpleBroker watcher
> code. Look for errors, bad ideas, hidden races, API ambiguity, mock-heavy
> tests, missing failure paths, or scope drift. Do not implement. Could a
> zero-context engineer implement the plan confidently and correctly while
> preserving `PollingStrategy` as the sole standalone-drive background wait path?

The author responds to every finding in the Review Record. Accepted findings
change the plan before spec promotion. Rejected findings record concrete
counter-evidence.

### Upstream pre-release review

Task 3A is a hard release gate. It reviews the completed, frozen SimpleBroker
slice before any tag or package publication. Its clearance is distinct from the
plan review and from final cross-repository review. Publication is not a
substitute for review, and Task 4's isolated artifact proof is not a code-review
gate.

### Completed-work review

Reviewer reads:

- this plan and deviation log
- promoted [CC-2.1], [SB-0.4], [QUEUE.8]
- upstream strategy method/tests/docs
- Weft watcher/tests/docs/dependency diff
- current focused, full SQLite, and full PG gate output

Prompt:

> Review the completed implementation against the promoted specs and this
> plan. Focus on concurrent wait/replace/close ordering, stop linearization,
> exact membership, fallback correctness, candidate-resource rollback, dependency
> compatibility, and whether the real PostgreSQL test proves LISTEN/NOTIFY
> rather than mocks. Report findings first. Could you sign off confidently?

Any production-code change after that review invalidates its clearance and
requires a new focused review.

### 15.1 Execution Record

Implemented and verified against uncommitted Weft HEAD
`2b02032f895ade32efa601f2b3ae0d59751dacfb` on 2026-07-10. The worktree was
already dirty with the preceding reactor-hardening slice; this implementation
preserved those changes and remains uncommitted for owner review.

Dependency evidence:

- managed environment: `simplebroker=5.3.0`, `simplebroker-pg=3.2.0`;
- declared floors: `simplebroker>=5.3.0`, `simplebroker-pg>=3.2.0`;
- `PollingStrategy.replace_activity_waiter` is callable;
- lock entries resolve published package-index artifacts rather than a sibling
  editable checkout.

Final implementation evidence:

- `weft/core/tasks/multiqueue_watcher.py` blob:
  `69cb62850149a1576d18e9f21e3d51e48d247bd3`;
- `weft/core/tasks/base.py` blob:
  `1de9ac38bd40ae0132e5dcb047f519ef5ef3db31`;
- TaskMonitor snapshot-policy integration blobs:
  `weft/_constants.py` `a2ca3852237d3a06aa6dcbe3a1e2b6642b8e20b2`,
  `weft/core/monitor/task_monitor.py`
  `f82a5064761b5a4be199e9d565b98230187a9599`;
- primary acceptance test blob:
  `tests/tasks/test_multiqueue_watcher.py`
  `282290aadb7f86adb3554d329ac93cc2af61933e`;
- SIGINT subprocess probe blob:
  `e902de92a512aa786fc0f90b9f7cadc6743990b1`.

Current-tree gates:

- default SQLite suite: `2355 passed, 3 skipped`;
- all-markers SQLite suite: `2356 passed, 14 skipped`;
- full PostgreSQL-compatible suite through `bin/pytest-pg --all`:
  `2298 passed, 12 skipped`;
- ten repeated owner/rebind/topology stress selections: green;
- mypy: 177 source files, green;
- Ruff lint and format check: green;
- plan/spec metadata and hygiene: green;
- `git diff --check`: green;
- Backstitch current output saved outside the repository at
  `/tmp/weft-rebind-backstitch-after.json`; targeted
  `SPEC_SECTION_MISSING`, `SPEC_MAPPING_RECIPROCAL_MISSING`, and
  `CODE_BACKLINK_RECIPROCAL_MISSING` diagnostics for [CC-2.1], [SB-0.4], and
  [QUEUE.8]: zero.

Independent completed-work review first found four defects: the SIGINT critical
window ended before displaced-waiter cleanup/request signaling, direct
`run()` did not exclude manual ownership, the pre-drain safe point was missing,
and transaction failures did not enter inherited retry. All received firing
tests and fixes. The final review cleared runtime blob
`69cb62850149a1576d18e9f21e3d51e48d247bd3` with no unresolved findings.

## 16. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| [IMPL.11] worker snapshot boundary | Section 9 did not expect TaskMonitor snapshot-policy files to change. | Added the nine topology fields plus `_topology_pending` to the explicit expected/replaced ledgers in `weft/_constants.py`, and rebuilt each as worker-local inert state in `TaskMonitor._worker_local_monitor_clone()`. | The first full-suite run produced 51 failures from one exact guard: inherited topology fields were unclassified, so every maintenance worker refused to clone. Sharing locks, deques, waiter ownership, or stop state into a worker would violate [IMPL.11]; explicit replacement is the narrow correct integration. | None. This preserves the existing worker-isolation contract and changes no public behavior. |

Append one row before implementing any departure. `pending` is not allowed at
plan completion. If a departure changes public semantics or owner ordering,
stop and re-review rather than treating it as a local implementation choice.

## 17. Out of Scope

- Making `BaseTask` topology dynamic. It remains explicitly sealed.
- Adding a production dynamic caller. The plan hardens the retained standalone
  API without inventing a use case.
- Mutable membership inside `PostgresMultiQueueActivityWaiter`. Rebuild and
  replace at the strategy boundary instead.
- Changing PostgreSQL listener registry design, notification SQL, pooling, or
  connection budgets.
- Solving global process or database connection counts.
- Rewriting `BaseWatcher`, `PollingStrategy` backoff, or Weft queue scheduling.
- An asyncio/event-loop conversion, second wait thread, generic reactor
  framework, or topology command bus.
- New CLI, TaskSpec, queue, payload, status, or persistence surfaces.
- SQLite lifecycle work, its release timing, and its regression proof. This
  plan neither tests nor conditions on that independent work.
- Nested repeated SIGINT while inherited SimpleBroker stop already holds its
  private `_stop_lock`. This plan fixes topology-lock re-entry and coalesces
  signals during Phase C; it does not change upstream stop-lock semantics.
- Full BaseTask or standalone watcher-facade cleanup, anchor reuse policy, or a
  Weft SQLite keepalive workaround. Successful removal preserves current
  facade-lifetime behavior.
- A new production observability API or latency SLO.
- Batching/coalescing topology requests. Deterministic per-request application
  is sufficient until measured churn proves otherwise.

No `TODOS.md` entry is needed: this plan is the active, fully scoped follow-on,
not deferred backlog.

## 18. Stop and Re-Plan Conditions

Stop and return to the user rather than drifting if any of these occur:

- the integrated SimpleBroker tree no longer has fixed-set native waiters;
- a live replacement cannot be expressed without changing `ActivityWaiter` or
  backend plugin protocols;
- preserving the strategy as sole standalone-drive background wait path requires copying
  `_process_messages()` or `_run_with_retries()`;
- `BaseTask` must become dynamic to make the test pass;
- the real PG test cannot distinguish native wake from polling without testing
  backend internals;
- the implementation wants an async API, extra thread, event bus, or generic
  framework;
- stop ordering cannot guarantee every waiting mutator is signaled;
- the proposed spec no longer matches the active reactor-hardening worktree.

These are design or dependency changes, not permission for an ad hoc workaround.

## 19. Fresh-Eyes Checklist for the Implementer

Before starting each slice, ask:

- Is the file owner and exact method named?
- Is this code running on the mutation thread or drive owner?
- Can a waiter still be inside `wait()` when this close happens?
- Does the replacement signature exactly equal current activity membership?
- Does a native failure preserve polling and delivery?
- Is the public caller told about request failure?
- Can stop leave this caller or thread blocked?
- Am I reusing the current queue/strategy path or creating a second one?
- Is the proof using real queues and a real watcher thread?
- Did I write and observe the red test before production code?
- Does the diff touch a file the plan says not to modify?

If the answer to the last question is yes, record a deviation and re-review
before continuing.

## 20. Author Fresh-Eyes Review Record

The author reread the complete plan after each correction rather than reviewing
only the amended paragraph. The pre-implementation architecture was
independently cleared before spec promotion; the completed implementation then
received a separate current-blob review recorded in section 15.1.

| Pass | Sections challenged | Finding | Required correction | Result |
| --- | --- | --- | --- | --- |
| 1. Scope and source-of-truth | 1, 3, 4, 9, 10, 17 | The dynamic waiter fix could accidentally become coupled to unrelated SQLite lifecycle work or grow into backend mutation. | Kept the replacement seam in core `PollingStrategy`, prohibited backend waiter mutation, and made SQLite lifecycle timing, implementation, and proof explicit non-gates. | Clear. |
| 2. Owner and drive lifecycle | 6.3, 8.2 through 8.4, Tasks 6 through 8 | A thread reservation gap, a second drive, or an uncleared weak `_thread` could let foreign work race owner claim or strand stop. | Added exact pre-start reservation, identity-checked owner claim/clear, in-flight request tracking, owner-finally rejection, and firing start/fatal-exit tests. | Clear. |
| 3. Waiter transaction | 7.4, 8.1, 8.5 through 8.7, Task 8 | Candidate Queue ownership, strategy waiter ownership, and topology publication were initially too easy to conflate; a post-replacement exception could close an installed waiter. | Split Phase A/B/C, staged all publication allocations, tracked `candidate_waiter_installed` separately from `topology_published`, and required exception-atomic restoration before rollback close. | Clear. |
| 4. Stop and manual wait | 2, 6.3, 7.3 through 7.4, 8.3.1, 8.6, Tasks 8.16 and 8.17 | Direct/manual wait was outside the drive ownership model. A later review then found that inherited no-drive stop could close an attached cached waiter during manual `wait()`. | Added temporary manual ownership. Start, mutation, and a second wait reject before effects. Stop records/notifies without inherited cleanup; the manual owner detaches/closes after `wait()` returns, clears ownership, then invokes inherited non-joining cleanup. Test 17 creates the legal attached-cache state with real `_start_strategy()`. | Clear. |
| 5. Signals and interruption | 2, 7.4, 8.4.1, Task 8.15, 17 | A topology `RLock` alone did not make arbitrary repeated SIGINT safe because inherited stop has its own non-reentrant lock; the first subprocess trigger was not portable. | Scoped the guarantee to one SIGINT plus Phase C coalescing, excluded nested inherited stop re-entry, deferred delivery until ownership is consistent, and used `signal.raise_signal()` instead of `os.kill`. | Clear. |
| 6. Red-green test order and test quality | 7.5, Tasks 2 through 9, 12, 13 | Task 7 originally risked implementing fallback/rollback before Task 8 could demonstrate red. Several lifecycle branches also needed real Queue/thread behavior rather than call-only mocks. | Limited Task 7 to the non-empty happy path, divided Task 8 into four red-green micro-slices, named 17 firing tests, retained real queues and drive loops, and reserved protocol fakes only for actual external seams. | Clear. |
| 7. Distribution and irreversible gates | Tasks 3A, 4, 10, 14, 15 | An environment `hasattr` probe could import an editable/sibling checkout, and publication could precede independent review of the upstream code slice. | Added a frozen upstream pre-release review gate and an exact-version `uv run --isolated --no-project` package-index probe that rejects editable/direct-URL and non-`site-packages` origins. | Clear. |

Final ambiguity audit:

- every state transition names its owner and lock boundary;
- every external or blocking call is identified as inside or outside the lock;
- every expected failure has caller, cleanup, and fatality behavior;
- each normative spec delta names its exact file and section code;
- each implementation task names files, red evidence, green commands, stop
  conditions, and review gates;
- no unresolved choice is delegated to implementing taste.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope and strategy | 0 | not run | Backend lifecycle follow-on; no product-scope review required |
| Codex Review | `/codex review` | Independent second opinion | architecture plus completed-work reviews | CLEAR | The completed-work review found four P1/P2 issues; all received firing tests and fixes, and runtime blob `69cb62850149a1576d18e9f21e3d51e48d247bd3` was cleared with zero unresolved findings. |
| Eng Review | `/plan-eng-review` | Architecture and tests (required) | 1 | CLEAR | Owner model, transaction, failure matrix, 17 firing tests, distribution gate, and review sequencing audited; 0 unresolved |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | not applicable | no UI scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not run | not required for internal runtime fix |

- **UNRESOLVED:** 0.
- **VERDICT:** CODEX + ENG CLEARED. Implementation and current-tree release
  gates are complete against SimpleBroker 5.3.0 and coordinated backend 3.2.0.
