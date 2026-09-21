# Watcher Reactor Restoration Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2.1], [CC-2.5]; docs/specifications/03-Manager_Architecture.md [MA-1.6a]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-3]; docs/specifications/07-System_Invariants.md [IMPL.8], [IMPL.10]
Superseded by: none

Class: 5. This restores an existing task execution path across the watcher,
task driver and manager and corrects the normative text that currently
describes the split wait path. It changes scheduling, so the risky
execution-path trigger applies. Promotion uses strategy A: land the reviewed
paragraph text in the cited active specs without implementation-link claims,
then land firing tests, code and reciprocal mappings against that text. It
depends on the published SimpleBroker 8.4.0 `PollingStrategy` contract: an
optional native wait deadline plus a narrowed local-notification contract. No
new broker abstraction, waiter type or watcher seam is required. One
Manager-local source adapter replaces the existing child-exit poll.

Revised 2026-09-18 after owner review. The review record at the end of this
file lists what changed and why.

## Design Rule

This is the owner's design intent and the normative test for every decision in
this plan:

> Each task is a reactor with one wake arbiter: the retained SimpleBroker
> `PollingStrategy`. The arbiter receives a small, closed set of inputs. On
> SQLite, backend activity is the database-wide `data_version` hint filtered
> by a durable pending check. On PostgreSQL, it is the multi-queue listener
> whose membership is this task's queues. Local events enter the same arbiter.
> Timers bound the arbiter only when clock work or the absence of an expected
> event is itself meaningful. If no relevant input arrives and no timer is
> due, the task has nothing to do and must not run a policy turn.

Every wait input belongs to exactly one of three classes:

| Class | Members | How it reaches the reactor |
| --- | --- | --- |
| Backend event | A write to one of this task's watched queues | The retained strategy: `data_version` change or native notification, then a live pending check |
| Local event | Worker result, finite worker-lane retirement, deferred signal, parent loss, stop, child-process exit, a self-write to a watched queue | A source adapter records its authoritative state, then calls `PollingStrategy.notify_activity()` on the retained strategy. No wait cap, slice or second task loop |
| Timer | Absence detection and clock-driven emission in service tasks | `next_wait_timeout()` passed as the wait deadline |

An ordinary default-config task has no recurring timer after its deferred
process title settles. A Consumer or Pipeline using transition reporting then
passes no wait deadline. Poll reporting and process-title deferral are explicit
timers when configured. Service timers exist where work is defined by a clock
or by the absence of an event: `HeartbeatTask` and `LivenessMonitor` due heaps,
`TaskMonitor`, and Manager idle timeout, probe expiry, registry heartbeat,
leader check, service convergence, autostart scan and post-exit terminal-proof
grace. Child exit itself is a positive local event. An absence can never arrive
as a notification; that is why these few waits need deadlines.

Any recurring wait bound that is not one of these three classes is cruft and
is deleted by this plan. In particular the 50 ms process poll interval, the
50 ms active-worker cap, the parent-loss wait ceiling, the 50 ms child-exit
poll and the abandoned 50 ms Manager / 25 ms PONG-response intervals are
polling substitutes for event paths that were not connected. A 50 or 150 ms
probe *expiry* remains a domain timer until separately retuned; its value does
not control queue or reply responsiveness.

## Spec Baseline

- `3faeff21f527bbd32c8ccfb53d662a68a0ccb024` is the committed baseline for
  `docs/specifications/01-Core_Components.md`,
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/04-SimpleBroker_Integration.md`,
  `docs/specifications/05-Message_Flow_and_State.md` and
  `docs/specifications/07-System_Invariants.md`.
- Before implementation, land the completed submission scan/session
  optimization as its own explicit change and remove the abandoned interval
  experiment with targeted edits. Slice 0 records both resulting SHAs so the
  promotion diff contains only this plan's delta.
- Plan type: implementation with spec revision.
- Promotion baseline: the commit produced by slice 0. Record the spec diff
  against that commit after the exact delta below is applied and before
  runtime implementation begins.
- Upstream dependency: satisfied. SimpleBroker 8.4.0 publishes the required
  deadline and local-wake contract. Dependency declaration and lockfile work
  are owned and confirmed outside this plan.

## Goal

Make the BaseTask drive path use the retained strategy as its only wait
primitive, route local sources into that strategy, pass service timers as the
wait deadline, and delete every polling substitute.

```text
SimpleBroker activity policy (retained PollingStrategy)
  SQLite: data_version, burst, gradual backoff, 100 ms quiet base interval
  PostgreSQL: filtered multi-queue listener, slow safety recheck
  Local: notify_activity() from worker, signal, parent-loss, stop,
         child-exit adapter and self-write
  Deadline: optional timeout = next real timer, otherwise None
                              |
                              v
MultiQueueWatcher hint -> live pending check -> fair queue drain
                              |
                              v
BaseTask bounded policy turn + worker results + due timers
```

No new reactor, polling strategy, event bus, backend adapter or watcher seam is
in scope. The one child-exit source adapter has a bounded Manager lifecycle and
feeds the existing local-event path. The change otherwise deletes bypasses and
caps.

## Existing Design and Evidence

### The oracle

The reference for correct behavior is SimpleBroker's
`BaseWatcher._process_messages()` loop plus the Design Rule above:

1. start one retained `PollingStrategy`;
2. perform an initial drain;
3. wait through the strategy;
4. treat wakeups as hints and check durable queue state;
5. drain through ordinary queue operations;
6. call `notify_activity()` only after useful work;
7. repeat until the shared stop event ends the owner.

Standalone `MultiQueueWatcher.run_forever()` already follows this loop through
the inherited `_process_messages()`. Only the `BaseTask` manual drive path
(`process_once()` / `wait_for_activity()`) departs from it.

### Provenance of the drift

The completed
[multi-queue waiter integration plan](./2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md)
*specified* the bypass: its manual `wait_for_activity()` is "pending check,
then `waiter.wait(timeout)`, otherwise `self._stop_event.wait(timeout)`". The
completed
[task reactor plan](./2026-05-15-task-reactor-and-evidence-worker-plan.md)
acknowledged that "SQLite manual `wait_for_activity()` currently uses only a
timeout fallback" and made a BaseTask wake cap the SQLite fallback. Those plans
are the origin of the drift, not a record of the intended design. Do not use
them as the review oracle.

### Compatibility targets

The relevant SimpleBroker tests are the compatibility target, not code to copy:

- `tests/test_watcher_burst_mode.py::test_burst_mode_resets_on_activity`:
  useful work returns the strategy to burst mode.
- `test_burst_mode_no_reset_on_empty_wake`: false or unrelated wakeups must not
  keep the reactor hot.
- `test_burst_mode_gradual_backoff` and
  `tests/test_watcher.py::test_polling_backoff`: the initial 100 checks are
  immediate, then delay grows to the configured 100 ms base interval with its
  existing jitter.
- `tests/test_watcher.py::test_polling_with_data_version` and the connection
  replacement tests: SQLite readiness is driven by database-wide
  `data_version`, including cache synchronization after connection replacement.
- `tests/test_watcher_race_conditions.py::test_pre_check_race_no_message_loss`:
  check/arm races must not lose work.
- `test_native_activity_waiter_wake_still_checks_empty_queue`: a native wake is
  a hint, not proof of claimable work.
- `tests/test_watcher_thundering_herd.py::test_unrelated_write_does_not_drain_idle_watchers`:
  database-level noise may cause a check but never delivery on an unrelated
  queue.
- PostgreSQL `test_multi_queue_activity_waiter_filters_by_watched_queues` and
  `test_polling_strategy_replaces_postgres_waiter_for_dynamic_queue_set`:
  exact listener membership and candidate-before-displaced replacement.
- watcher lifecycle and PG listener-close tests: stop/close must wake the wait,
  leave one cleanup owner and close the installed waiter once.
- `TestPollingStrategy::test_native_deadline_uses_one_budget_and_preserves_shortened_pass_state`
  and the real PostgreSQL/Redis deadline tests: native timer expiry is quiet and
  preserves cadence state, while polling fallback keeps one ordinary pass.
- `test_downgraded_local_notification_has_exact_owner_state_vector` and
  `test_notify_activity_only_arms_latch_across_supported_contexts`: notification
  side effects are deferred to owner consumption, and an empty-check downgrade
  is not overwritten by a coalesced foreign notification.
- `test_worker_result_latch_wakes_long_strategy_wait_and_enters_burst` and
  `test_base_reactor_composes_one_deadline_across_quiet_strategy_passes`: the
  reference reactor has one retained strategy, rechecks authoritative local
  state after each return, and composes one deadline without a second Event.

### Facts about the retained strategy that this plan relies on

Verified in the published SimpleBroker 8.4.0 implementation and its promoted
`[SB-API-6]` contract:

- `wait_for_activity()` tests `_local_activity_pending` at the top of every
  loop pass. With a native waiter each pass is one `waiter.wait()` of at most
  `max(delay, burst_sleep)`, so a `notify_activity()` from any thread is
  observed no later than the next strategy pass. At the default quiet setting
  that is nominally 100 ms, with the configured jitter and scheduler delay.
  Without a native waiter the call returns after one quantum of at most the
  same nominal length, and the owner sees the local source then. Local
  notification does not interrupt an in-progress native condition wait; this
  one-pass bound is the intended first-wakeup contract. Useful work then
  returns the strategy to burst mode.
- `notify_activity()` now performs exactly the published foreign-context
  operation: it assigns one coalescing local-activity latch. The strategy owner
  performs the drain-hint, backoff, burst and native-idle-deadline mutations
  when its next `wait_for_activity()` observes that latch. A direct
  `consume_local_activity_hint()` immediately after notification therefore
  returns false; Weft must drive the wait before consuming the hint.
- `mark_local_activity_as_empty_check()` can downgrade a pending owner
  notification so latch consumption publishes an empty-check hint instead of
  the drain hint. A foreign notification that coalesces after that downgrade
  still wakes the owner but does not restore the drain hint. SimpleBroker's
  contract therefore requires foreign source state to remain authoritative.
  Weft's bounded multi-queue drain does not use this downgrade: remaining
  backlog and a same-connection self-write both still require the ordinary
  local-hint discovery path.
- A finite non-negative timeout is one monotonic budget only for an internally
  looping native waiter. A caller-shortened final native pass changes neither
  `_check_count` nor remaining burst count. The polling fallback ignores the
  argument and completes one ordinary pass, including for zero. Weft preserves
  its own zero-timeout wrapper rule by returning before it calls the strategy.
- With a native waiter the call does not return on a quiet pass. It returns on
  a native hint, a local hint, stop, or the staggered 1-2 s idle-poll deadline.
  A PostgreSQL notification therefore wakes the task immediately; the 1-2 s
  figure is only how long one call holds when nothing arrives.
- The PostgreSQL `wait_any()` condition is not notified by the watcher stop
  event. Stop latency on PostgreSQL is bounded by the strategy's pass length,
  not by the listener.
- `BaseWatcher._on_data_version_change()` is the existing protected hook fired
  when `data_version` moves. Weft does not override it today.
- `data_version` moves only for commits from *other* connections. A task's
  write on its own reactor connection is invisible to it.

`examples/reference_reactor.py::BaseReactor` is the application-level ownership
reference. Its shipped 8.4.0 implementation removed the parallel local Event,
passes one remaining deadline to the strategy, and after every strategy return
checks authoritative local state first, then durable state when no local result
is pending. Its retained 50 ms output-backlog retry applies while durable output
rows remain pending or publication is blocked; it is an application timer, not
an idle reactor cadence.

`MultiQueueWatcher` remains the deep module seam: one strategy, one fan-in
waiter, queue membership generation, owner-confined replacement, pending
prechecks, fair drain and one drive owner. New callers use that seam rather
than expose more of its implementation.

## Confirmed Drift and Defects

### D1. The task drive path bypasses the retained strategy

`BaseTask` starts the inherited strategy, but
`MultiQueueWatcher._wait_for_activity_body()` never drives it. It calls the
native waiter directly, or performs `_has_pending_messages()` followed by
`_stop_event.wait(timeout)`.

`BaseTask.run_until_stopped()` compounds this: a non-`None`
`next_wait_timeout()` *replaces* `poll_interval` instead of joining it with
`min()`. An idle SQLite Manager whose nearest timer is the 1 s leader check
therefore sleeps on the stop event for about 1 s, blind to queue writes. That
is the latency the interval experiment tried to cap.

Consequences:

- SQLite tasks receive none of SimpleBroker's `data_version`, burst and
  gradual-backoff behavior.
- PostgreSQL tasks use the listener but bypass the strategy's local-activity
  latch, burst state and strategy-owned safety recheck. `notify_activity()`
  after a useful drain sets a latch that the manual path never consumes.
- For tasks without timers, the launcher's fixed 50 ms `poll_interval` is the
  real scheduler: twenty full policy turns per second while idle.
- `_wait_for_activity_body()` sets a misleading
  `_pending_messages_precheck_confirmed` flag solely because `waiter.wait()`
  returned true. Dispatch still performs live durable checks:
  `_update_active_queues()` uses that flag to scan inactive watched queues with
  `has_pending()`. The defect is duplicated readiness policy and a false state
  name, not a bypass of durable validation.

This is the central architectural defect.

### D2. The upstream service-deadline gap is closed; Weft does not use it yet

SimpleBroker 8.3.1 accepted no wait deadline. On SQLite the call returned every
quantum, so an owner loop could test "timer due?" between calls with about
100 ms precision. On PostgreSQL the call held until a hint or the 1-2 s idle
poll, so a 1 s heartbeat could become 1-3 s and a 50 ms probe expiry could
become up to 2 s. SimpleBroker 8.4.0 closed that backend difference by adding
`timeout: float | None = None` to the existing method. Weft's remaining defect
is that its manual task drive path bypasses the method.

The published contract is:

- `None` preserves current behavior exactly.
- A finite value is one monotonic deadline for the internally looping native
  waiter branch. It clamps each `waiter.wait()` to the remaining time and
  returns when the deadline passes. The polling branch retains its existing
  one-pass behavior: it already returns to the owner once per configured quiet
  pass, so the outer reactor observes a due timer within that nominal 100 ms
  pass without creating a second, shorter SQLite cadence.
- Native deadline expiry is a quiet return: no activity hint, no burst reset,
  and the truncated pass does not increment `_check_count`. It cannot spin,
  because a positive remaining budget blocks until that deadline or an earlier
  strategy event. Upstream `timeout=0` instead performs one nonblocking native
  observation; Weft's wrapper returns on a nonpositive budget before it calls
  the strategy. On polling fallback the owner checks the timer again after the
  current strategy pass.
- The notifying path sets only one coalescing latch and performs no lock, wait,
  I/O, clock or random call. The owner consumes the latch and then resets check
  count, sets the drain hint unless it was downgraded to an empty check, enters
  burst and reschedules the native idle poll. Callers record authoritative
  source state before notifying, so notifications that coalesce or race
  consumption are covered by the owner after the wake.

This is a deadline parameter and a correction to the existing local-input seam,
not a new wait API. The upstream implementation and release are complete.
Reimplementing `data_version` and backoff in Weft, or a timer thread that calls
`notify_activity()` and falsely resets burst, would both be drift.

### D3. Manager PONG probe expiry is not a published timer

Manager leadership and service probes are incremental state machines with
`deadline_ns`, but `Manager.next_wait_timeout()` omits those deadlines. Probe
expiry is an absence and therefore a timer under the Design Rule. Publish it.

The probe *reply* is a backend event that currently lands on the responder's
`ctrl_out`, a queue the probing Manager does not watch. That is a control
contract defect, not a watcher defect, and it is out of scope here; see
Follow-up. Until then both backends discover a PONG at probe expiry (50 ms
leadership, 150 ms service). PostgreSQL receives no notification because the
reply queue is outside listener membership. SQLite sees the database-wide
`data_version` change, but its live filtering checks only the Manager's watched
queues and correctly finds no relevant work. The 50/150 ms values are liveness
policy budgets, not reactor polling intervals. The reply-to follow-up turns a
PONG into relevant backend activity and removes that reply latency.

Publishing deadlines also exposes an existing orphan case. A pending leader or
service probe is normally advanced only while its originating candidate is
visited. If that evidence disappears before expiry, the expired entry can
remain in `_leader_probe_pending` or `_service_probe_pending` and force
`next_wait_timeout()` to return zero forever. At the start of each Manager turn,
clear per-turn visited-key sets. Candidate evaluation marks the matching probe
key visited. At the end of the turn, retire every expired, unvisited probe:
delete its exact request row, sweep request-keyed reply rows, remove the pending
entry, and do not create a cache result for evidence that no longer exists.
Pending probe state always retains the canonical target control queue and
`request_id`, even when post-write message-ID discovery fails. If the exact
request-row message ID is unavailable, cleanup scans only that target queue and
deletes PING request rows with the matching `request_id`; it never broad-deletes
the queue. The reply sweep uses the same request key.
Normal visited probes still advance through their existing state machine so an
expired live candidate produces its ordinary timeout proof rather than being
re-probed. The later `reply_to=ctrl_in` follow-up replaces reply-row sweeping
with its own handled-row contract but retains the same orphan retirement rule.

### D4. Work that silently depends on the implicit 50 ms turn

Removing the default `poll_interval` removes twenty free turns per second.
Anything time-dependent that runs "whenever a turn happens" and publishes no
timer will stall on an idle task. Known case:
`BaseTask._maybe_emit_poll_report()` for `reporting_interval == "poll"`. The
process-title deferral already publishes `seconds_until_due()` and is a valid
timer. The completed audit of every `_process_reactor_turn()` override is:

| Owner | Time-dependent work or silent local source | Class and durable predicate | Published wake or disposition |
| --- | --- | --- | --- |
| `BaseTask` | poll reporting; deferred process title | timer; next report/title due | `BaseTask.next_wait_timeout()` publishes poll reporting; the final wait boundary composes `process_title.seconds_until_due()` |
| `BaseTask` worker lane | result publication; finite worker-lane retirement | local event; result queue content or live-worker set changed | producer records the result or retirement first, then calls retained-strategy `notify_activity()` |
| `Consumer` | target completion | local event; worker-result queue and worker retirement | common completion-predicate driver; no private clock |
| `Pipeline` | queue-driven stage work | backend event; watched queues have eligible durable work | shared watcher only; no private clock |
| `HeartbeatTask` | heartbeat emission, idle shutdown, singleton ownership audit | timers; due heap, empty-since deadline, next audit time | exact values composed in `HeartbeatTask.next_wait_timeout()` |
| `LivenessMonitor` | probe due heap, full reconciliation, state refresh | timers; next due/reconcile/refresh times | exact values composed in `LivenessMonitor.next_wait_timeout()`; pause suppresses private clocks |
| `TaskMonitor` | initial/requested cycle, periodic cycle, heartbeat registration retry | immediate local state or timer; pending flag, next cycle time, retry time | exact values composed in `TaskMonitor.next_wait_timeout()`; an in-flight worker has no polling deadline |
| `Manager` | admission/control retry, service/leadership/registry/idle/autostart/log clocks, probe expiry, post-sentinel PID recheck, terminal-proof grace | timers; the corresponding stored deadline | exact values composed in `Manager.next_wait_timeout()` |
| `Manager` child adapter | child-process exit | local event; fired exact `(tid, sentinel)` identity | one sentinel observer records the identity, then notifies the retained strategy; the reactor validates and reaps |
| Launcher parent watcher | parent loss | local event; recorded parent-loss state | independently named source-adapter cadence records state, then notifies; it is not a task-reactor deadline |
| Service worker queues and blocking retry/finalization paths | queue backpressure, worker shutdown, seed/publication/drain retries | source adapter or bounded finalization | retained as named operation/finalization bounds; they do not schedule steady-state reactor turns |

The audit found no additional task policy that relied on free 50 ms turns.
Worker retirement was added as a distinct local event because a result can be
drained before the publishing worker has removed itself from the live-worker
set; without that second notification a direct persistent Consumer can remain
asleep after its completion predicate becomes true.

### D5. The audit found a child-exit poll and separate blocking-turn risks

`MANAGER_CHILD_EXIT_POLL_INTERVAL` is architectural drift. Process exit is a
positive OS event, not an absence. Replace that interval with one Manager-owned
source adapter that waits over the current `multiprocessing.Process.sentinel`
handles and a membership/stop wake pipe through
`multiprocessing.connection.wait()`. The Manager publishes immutable
`(tid, sentinel)` membership snapshots; the adapter places the fired
`(tid, sentinel)` identity on one broker-free local queue and calls the retained
strategy's `notify_activity()`.
It suppresses a fired sentinel until that process identity leaves membership,
so a level-triggered dead-process handle cannot spin. A coalesced pipe token
wakes membership replacement or shutdown. The adapter may observe; it must not
mutate `_child_processes`, call `Process` methods, touch broker handles or reap.
The reactor drains the TID queue, remains the only owner of
`_cleanup_children()`, and keeps `_child_has_exited()` as the authoritative
validation, including its PID fallback for platform lag. Use one adapter for
the Manager, not one thread per child. If the OS sentinel fires before the
Python process view confirms exit, publish the existing PID-liveness recheck
as a timer for that child. When a confirmed dead child lacks terminal proof,
publish the existing grace expiry as a real timer rather than polling until it
elapses.

The adapter lifecycle is exact. The Manager reactor owns the send endpoint,
membership snapshot and result-queue drain; the observer thread alone reads the
receive endpoint and waits on process sentinels. The adapter object owns both
endpoints for cleanup. Create the pipe and start the observer before publishing
the first snapshot. An adapter-internal lock protects only the current immutable
snapshot, the stopping flag and one wake-token-pending bit. A membership or stop
update writes at most one pipe token while that bit is set; the observer drains
the token, takes the latest snapshot and clears the bit. Pipe closure or an
unexpected send/wait failure follows the same fatal observer-failure path, so a
full or broken pipe cannot become a silent lost wake. During shutdown, stop new
child launches, keep the observer live through child termination and final
reaping, publish the adapter stop, wake and join it, close both endpoints exactly
once, and only then close the retained strategy. A fired result carries both TID
and sentinel identity; the reactor drops it if the registry no longer contains
that exact pair. If the observer fails, it publishes one failure result and
local notification; the Manager treats silent loss of child observation as
fatal rather than falling back to polling.

Manager inbox-seed retries, terminal-publication retries and interactive drain
loops can sleep while the reactor owner is inside a policy turn. They are
blocking-retry concerns, not competing steady-state broker pollers. Record
them as audited follow-up work and do not change them in this plan. Docker/SDK
polling, filesystem retry and cleanup waits are source adapters or bounded
finalization, not broker readiness; the audit must still name their owner and
why they cannot stall the task reactor.

## Required Behavior

### MultiQueueWatcher wait algorithm

`_wait_for_activity_body(timeout)` remains the single protected implementation
used by `BaseTask`. The public standalone `wait_for_activity(None)` wrapper
retains its current no-op compatibility; on the protected path `None` means
"no timer is due".

1. A stopped watcher or nonpositive timeout returns without queue I/O.
2. Ensure the strategy has started exactly once before it is driven.
3. Compute one monotonic deadline from `timeout`, or none.
4. Loop:
   1. Stop set: return.
   2. Recompute the remaining monotonic budget. If it is non-`None` and
      nonpositive, return for the timer turn before queue I/O or a strategy
      call. SimpleBroker rejects negative timeout arguments.
   3. Call `self._strategy.wait_for_activity(timeout=remaining_or_None)`.
   4. Stop set: return.
   5. Local hint (`consume_local_activity_hint()`): this hint becomes visible
      only after the strategy owner consumes the notification latch in the
      preceding `wait_for_activity()` call. Validate the authoritative local
      state, then run one live `_has_pending_messages()` check because a one-bit
      latch cannot distinguish a same-connection self-write from a worker or
      signal wake. If pending work exists, request the same broad active-set
      refresh as a broker hint. Return to the policy turn even when no queue is
      pending so worker, termination, parent-loss and child-exit state can run.
      Every adapter must notify only after recording its source state. After a
      useful drain this also yields one immediate follow-up turn, which is the
      upstream backlog continuation behavior.
   6. Broker hint (`consume_native_activity_hint()`, or the flag set by the
      overridden `_on_data_version_change()`): run the live
      `_has_pending_messages()` check. If true, request a broad live active-set
      refresh and return. Rename the current "precheck confirmed" flag so it
      describes that work accurately. If false, the wake was empty or
      unrelated: do not call `notify_activity()`, and continue the loop.
   7. Quiet return with `uses_native_activity()` true and the deadline not yet
      reached: this is the strategy's idle poll. Run the durable pending check
      as the single authoritative PostgreSQL safety recheck. Weft's
      inactive-queue discovery interval keeps its existing role inside the
      drain only.
   8. Deadline reached: return for the timer turn. Timer expiry is not broker
      activity and resets nothing.
   9. Otherwise (a quiet SQLite quantum): continue without queue I/O.
5. `uses_native_activity()` is the only backend test. There is no Weft-level
   slice, no second Event poll and no per-backend branch.
6. The `_on_data_version_change()` override calls `super()` and sets the
   broker-hint flag. It also fires on the first cache-sync observation; that
   errs toward one extra check and is correct.
7. Actual drained work calls the existing `notify_activity()` once. Empty or
   unrelated wakes do not. The next strategy pass consumes the notification
   latch and owns all burst/backoff mutation. Do not call
   `mark_local_activity_as_empty_check()` on this path: one bounded multi-queue
   scheduling pass may leave known backlog, and a watched-queue self-write can
   have coalesced into the same latch. Suppressing the drain hint would then
   suppress the discovery pass. If a later design introduces the downgrade, it
   must first add and test an independent authoritative-local-source check after
   every strategy return, as required by SimpleBroker `[SB-API-6]`.
8. Native failure detaches/closes that waiter through existing ownership and
   the strategy continues on its polling branch. No second waiter thread.
9. Self-write rule: a task that writes to one of its own watched queues on its
   reactor connection calls `notify_activity()` after the write, because
   `data_version` will not move. The local-hint pending check above must activate
   an inactive watched queue immediately; the 1 s discovery interval is only a
   safety backstop. `Manager._managed_internal_spawn_enqueued` is the existing
   instance: replace its zero-timeout continuation with `notify_activity()` so
   the write follows the same arbiter path. Slice 3 audits for other
   self-writes; no same-turn direct-continuation exception is allowed.
10. A handled control row must be deleted so a PEEK-mode queue cannot keep the
    pending check true. Retained rows must never drive readiness.

Instrument strategy calls, durable pending checks and full task turns
separately. This proves a latency improvement is not more full-turn polling.

### Strategy lifecycle ownership

`MultiQueueWatcher` owns the retained strategy's started state for all three
entry paths. Override its inherited `_start_strategy()` only to delegate to
`BaseWatcher` and record a successful start; do not duplicate upstream setup.
Remove BaseTask's parallel ownership flag and make its
`_ensure_task_strategy_started()` consult the watcher-owned state.

- The first topology-exclusive manual wait starts once. Repeated manual waits
  reuse cadence, data-version and waiter state without resetting them.
- The inherited background `BaseWatcher` retry loop intentionally calls the
  overridden start on every retry attempt, preserving its existing restart and
  waiter-replacement semantics. It records the new successful generation.
- BaseTask's owner-confined driver ensures one initial start and never starts
  again during ordinary turns or waits.
- A native waiter failure detaches and closes the expected waiter through
  `_reset_multi_activity_waiter()` but leaves the strategy started in fallback
  mode. It does not create or restart another strategy.
- Final runtime cleanup detaches/closes the installed waiter, closes the
  strategy once, then marks it not started. No wait is legal after final close;
  a background retry before final cleanup remains the only restart path.

Firing tests cover initial manual start, repeated manual reuse without cadence
reset, inherited retry restart, BaseTask no-double-start, native failure to
fallback, and exactly-once final close.

### Task driver

- Change the production default `poll_interval` to `None`. It is an optional
  manual/embedding deadline, not a scheduler.
- Combine an explicitly supplied manual interval, `next_wait_timeout()` and the
  process-title due time with `min()` over present values. Zero wins. No
  present value means an unbounded wait.
- Delete the active-worker cap (`TASK_REACTOR_WAKEUP_MAX_SECONDS`) and the
  parent-loss ceiling from `_wait_for_reactor_activity()`. Do not add a
  signal-check deadline.
- `_publish_worker_result()`, `note_termination_signal()` and
  `note_parent_loss()` each call the retained strategy's `notify_activity()`
  after recording their in-memory state. The signal handler stays plain-state
  only plus the upstream coalescing latch; it performs no Event, lock,
  wait, I/O, clock, random or broker operation.
- `BaseTask.next_wait_timeout()` publishes the poll-report due time when
  `reporting_interval == "poll"`, and every other timer found by the D4 audit.
- `Manager.next_wait_timeout()` publishes leadership and service probe
  `deadline_ns` plus post-exit terminal-proof grace deadlines.
- Replace `MANAGER_CHILD_EXIT_POLL_INTERVAL` with one lifecycle-owned child
  sentinel adapter. Keep the private implementation in `manager.py` unless
  tests prove a separate cohesive module is clearer. It uses one receive-only
  wake pipe plus immutable membership snapshots, publishes fired
  `(tid, sentinel)` identities to a local queue before `notify_activity()`,
  suppresses already-fired identities,
  and leaves registry mutation and reaping to the reactor. Child add/remove
  and Manager shutdown update or stop that adapter without a thread per child.
  The reactor is the sole send-end user; the observer is the sole receive-end
  user; the adapter closes both once after join and before strategy cleanup.
- Extract the inner process/wait/stop driver with a caller-supplied completion
  predicate. `run_until_stopped()` uses the existing stop/terminal predicate;
  `Consumer.run_work_item()` uses its current direct-result-settled predicate.
  Persistent direct consumers must still return after their one item while
  remaining `running`, with unchanged resource/finalization behavior.
- Update detached and foreground manager launch paths together. Remove
  `MANAGER_FALLBACK_POLL_INTERVAL_SECONDS`, `_fallback_poll_interval` and
  `CONTROL_PING_POLL_INTERVAL_SECONDS` from the abandoned interval experiment.
- Decouple the parent-loss *watcher thread* cadence from `poll_interval`; keep
  its floor/ceiling policy as a named launcher concern. Change launcher and
  manager-process transport types to `float | None`, including an explicit
  detached-process encoding for `None`, while preserving numeric overrides.

## Proposed Spec Delta

Promotion strategy A: land the reviewed single-arbiter paragraphs in the
existing active specs without implementation-link claims before runtime code
changes. Add reciprocal mappings only with the implementation slice. The
following text is the review target.

| Spec file | Strategy | Sections touched |
| --- | --- | --- |
| `docs/specifications/01-Core_Components.md` | A | [CC-2.1], [CC-2.2.1], [CC-2.5] |
| `docs/specifications/03-Manager_Architecture.md` | A | [MA-1.6a] |
| `docs/specifications/04-SimpleBroker_Integration.md` | A | [SB-0.4] |
| `docs/specifications/07-System_Invariants.md` | A | [IMPL.10] |

### [CC-2.1] exact replacement

Delete the three `Current role` bullets beginning “own a backend-neutral wait
seam”, “treat native waiter activity” and “treat zero-timeout waits”. Insert the
following text after the remaining `Current role` bullets:

> A running watcher or task has one drive owner and one wake arbiter. The
> owner drives the retained SimpleBroker `PollingStrategy`; it does not create
> a second broker polling loop and does not slice the wait to poll local
> state. The strategy owns SQLite `data_version`, burst and backoff behavior,
> PostgreSQL native activity waiting and its slow safety recheck. Its wait
> accepts the owner's next timer as an optional deadline. Native notifications
> and data-version changes are readiness hints only: the owner checks current
> durable queue state before dispatch. Consuming a local notification also
> performs one live watched-queue check so a same-connection self-write can
> activate an inactive queue even though SQLite `data_version` does not change.
> Empty, unrelated and deadline wakes do not reset useful-activity state.
> `MultiQueueWatcher` owns strategy start, restart and final-close state across
> inherited background driving, manual waits and `BaseTask`; no entry path owns
> a parallel started flag. `BaseTask` invokes the same protected wait under its
> reactor lifecycle guard; the public manual wrapper retains its separate
> ownership exclusion and `None` compatibility. A finite positive manual timeout
> is exact for a native waiter and is observed after at most one configured quiet
> pass on polling fallback; zero returns without queue I/O.

### [CC-2.2.1] insertion after the task-loop contract

> Every task wait input is a backend event, a local event or a timer. Local
> events (worker result publication, finite worker-lane retirement, deferred
> signals, parent loss, stop, child-process exit, and a task's own write to a watched queue) wake the wait through the
> retained strategy's coalescing local-activity notification; no wait cap
> exists for them. Timers are published through `next_wait_timeout()` and
> passed as the wait deadline.
> A task that publishes no timer waits without a deadline and runs no policy
> turn while all wake inputs are silent. Only durable eligible work, a local
> event, stop or a due timer returns control to the policy loop. Direct
> Consumer work uses the same driver with its own result-settled completion
> predicate, so a persistent Consumer returns from `run_work_item()` after that
> item without terminating the task.

### [CC-2.5] exact replacement

In the implementation-mapping paragraph, delete only the sentence beginning
“While worker lanes are active” and ending “without task-specific poll loops.”
Do not add a future implementation claim during strategy-A
promotion. The normative worker-notification requirement is in [CC-2.2.1].
Slice 3 adds the reciprocal implementation mapping only after the code and
firing tests land.

### [MA-1.6a] exact replacement

After slice 0 has removed the uncommitted interval experiment, replace only the
sentence beginning “Parent-side cleanup for user child process handles” and
ending “do not force that cadence” with:

> Child-process exit reaches the Manager reactor as a local event from one
> Manager-owned sentinel adapter; post-exit terminal-proof grace remains a
> published timer.

Retain unchanged the approximately one-second service-reconciliation and
leadership timer, prompt queue-activity wake, and foreground stale-probe
timeout rules that follow it. Immediately after the sentence “Queue activity
still wakes the manager promptly through the shared watcher”, insert:

> Manager and service PONG probes publish their expiry through
> `next_wait_timeout()`. No manager-specific fallback poll interval or PONG
> response poll interval controls steady-state latency. At the end of each turn, an
> expired pending probe whose originating evidence was not visited is retired:
> its exact request and request-keyed reply rows are removed, its pending entry
> is dropped and no evidence result is cached. Pending state retains the target
> control queue and request ID; when the request message ID is unavailable,
> retirement deletes only matching keyed PING rows from that target queue. The
> sentinel adapter has one
> observer thread, immutable membership snapshots, a coalesced membership/stop
> wake token and fired `(tid, sentinel)` identities; the Manager reactor alone
> validates identities, mutates the child registry and reaps children. Adapter
> failure is fatal, and shutdown joins it and closes its endpoints before the
> retained strategy closes.

### [SB-0.4] exact replacement

Delete the final two integration bullets beginning “when the backend returns a
native activity waiter” and “when no native activity waiter is available”.
Replace them with:

> - `MultiQueueWatcher` drives its retained SimpleBroker `PollingStrategy` for
>   every backend. It installs the optional multi-queue activity waiter through
>   the strategy lifecycle seam and never waits on that native waiter directly.
>   A service timer is passed as the optional strategy deadline; polling
>   fallback keeps its configured one-pass cadence rather than adopting a
>   second Weft polling interval.
> - Native notifications, SQLite `data_version` changes and local notifications
>   are readiness hints. The owner performs a live watched-queue pending check
>   before queue dispatch; local-hint consumption performs the same check so a
>   same-connection self-write can activate an inactive watched queue. Quiet,
>   empty, unrelated and timer-deadline returns do not assert durable work or
>   reset useful-activity state. Zero-timeout local timer boundaries return
>   without queue probes.

### [IMPL.10] insertion after the reactor timing contract

> `poll_interval` is an optional manual or embedding deadline, not the
> production scheduler. Production tasks derive waits from watcher policy and
> published timers. The deferred-signal handler records plain in-memory state
> and sets the strategy's coalescing local-activity latch; it performs
> no Event, lock, wait, I/O, clock, random or broker operation. The launcher's
> parent-loss watcher cadence is
> independently named and does not derive from `poll_interval`.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| [CC-2.2.1] | Worker completion was one local event. | Result publication and finite worker-lane retirement are separate local events. | A result may be drained before the publishing thread retires; the retirement wake is required for the common direct-Consumer completion predicate. | Promoted in [CC-2.2.1] by this plan. |

## Implementation Slices

### 0. Separate the two uncommitted changes

- With the owner, commit the completed submission scan/session optimization
  on its own. It shares `weft/core/control_probe.py`,
  `tests/core/test_control_probe.py` and specs 03/05 with the interval
  experiment.
- Remove the complete abandoned interval experiment in this slice: the 50 ms
  Manager and 25 ms PONG constants, `_fallback_poll_interval`, interval-specific
  tests and normative paragraphs. Use targeted edits only. Never `git checkout`
  or `git restore` a shared dirty file.

Gate: the submission optimization commit contains no reactor-restoration
changes; the interval-experiment subtraction is recorded separately; the
resulting promotion baseline SHA is recorded in Spec Baseline.

### Satisfied prerequisite — SimpleBroker released first

- The SimpleBroker owner executes the separate
  [`PollingStrategy` native deadline and local wake plan](../../../simplebroker/docs/plans/2026-09-18-polling-strategy-native-deadline-and-local-wake-plan.md)
  against SimpleBroker's own promoted `[SB-API-6]` contract, full suite, static
  checks and real PostgreSQL/Redis matrix.
- No Weft install, candidate-wheel run or Weft test is a gate for that release.
- Those first-party gates passed and SimpleBroker 8.4.0 was published before
  this Weft slice. Its `[SB-API-6]` contract is the implementation oracle.

Gate: satisfied. This plan does not edit, release, pin or relock the sibling
SimpleBroker dependency.

### Spec promotion — establish the reactor contract

- Apply only the exact paragraphs under Proposed Spec Delta to the cited
  active specs and add this plan to their related-plan lists.
- Under strategy A, do not add implementation mappings, code backlinks or
  verification claims in this slice. Record the reviewed spec-promotion SHA in
  Spec Baseline.
- Confirm the spec-only commit passes the documentation and traceability gates
  without new warning-class debt.

Gate: independent review approves the exact delta and the promoted text is the
one governing contract before firing tests or runtime changes begin. Stop if
the active specs cannot carry unlinked paragraph text as strategy A assumes.

### 1. Prove the bypass

- Add a firing test showing a normally driven SQLite task reaches
  `PollingStrategy.wait_for_activity()` rather than `_stop_event.wait()` during
  a quiet turn. Confirm it fails before implementation.
- Add the PostgreSQL equivalent through the installed multi-queue waiter.

Gate: the test proves the production `run_until_stopped()` path, not a direct
call to a private helper.

### 2. Verify the published SimpleBroker contract at the Weft seam

- Dependency declaration and lockfile updates are separately owned and already
  confirmed. Do not change or revalidate their artifact selection in this
  slice.
- Add a narrow Weft seam test proving the installed strategy accepts the
  optional `wait_for_activity(timeout=...)` argument, leaves the polling
  fallback on one ordinary pass, and defers the local drain hint until the
  owner consumes the latch.
- Prove the Weft path never applies the upstream empty-check downgrade after a
  bounded multi-queue drain. A same-connection self-write and remaining backlog
  must both survive into the local-hint discovery pass.

Gate: the seam tests pass before Weft changes its call sites. The upstream
release and dependency update are settled inputs, not outputs of this plan.

### 3. Restore the strategy path and delete the caps

- Implement the wait algorithm in `_wait_for_activity_body()` and the
  `_on_data_version_change()` override.
- Implement the Strategy lifecycle ownership section: `MultiQueueWatcher`
  becomes the sole started-state owner across manual, inherited and task-driven
  entry paths. Preserve SimpleBroker's existing start/retry mechanics and
  Weft's topology-replacement and final-cleanup serialization.
- After code and firing tests land, update [CC-2.5]'s implementation mapping to
  state that the worker-result queue is authoritative, workers record results
  before notifying the retained strategy, and `MultiQueueWatcher` drives that
  strategy. Add reciprocal code backlinks in the same slice.
- Implement the Task driver section: `None` default, `min()` composition,
  local sources via `notify_activity()`, cap deletion, extracted driver.
- Run the D4 audit of every `_process_reactor_turn()` override and publish or
  delete each time-dependent action. Record the audit table in this plan.
- Run the self-write audit and apply rule 9.
- Publish Manager probe expiry in `next_wait_timeout()` and implement the
  visited-key orphan sweep from D3 at the end of every Manager turn. Test
  disappearing leader and service evidence before expiry on SQLite and
  PostgreSQL; at expiry each request/reply row is retired, the pending entry is
  removed, and the next timeout is not pinned to zero. Force post-write request
  message-ID discovery to fail and prove the target-queue/request-ID fallback
  removes only the matching keyed PING.
- Add the single Manager child-sentinel adapter, delete
  `MANAGER_CHILD_EXIT_POLL_INTERVAL`, and publish post-exit terminal-proof grace
  plus any post-sentinel PID-view recheck as timers. Prove registration, exit,
  coalesced membership changes, a level-triggered sentinel, child add/remove,
  stop, wake-pipe close and cleanup races without giving the adapter broker,
  `Process` or child-registry ownership.

Gate: SQLite and PostgreSQL task suites pass; the idle-task firing test in the
matrix passes; `reporting_interval == "poll"` tasks still report while idle.

### 4. Audit the remaining waits

- Classify TaskMonitor summary waits, heartbeat startup waits, terminal
  snapshot waits, `QueueChangeMonitor`, child exit and
  `send_keyed_ping_probe()` as task reactors, caller observers or source
  adapters.
- For each, record its Design Rule class, durable predicate, cursor/ownership
  rule and wake source. Remove a wait only when the existing watcher seam
  represents it without retained-row spin or a second drive owner.
- Add an architecture check for production task code that forbids broker-wait
  loops and wait caps outside `MultiQueueWatcher`, with named exceptions for
  source adapters and finalization.
- Leave manager seed retry, terminal publication and interactive drain
  unchanged. File a separate plan if measurement shows they harm control
  latency.

Gate: every wait has an owner, a class and a disposition; this plan acquires no
retry or state-machine work.

### 5. Verify fidelity and cost

- Treat the published SimpleBroker contract and its completed first-party
  release evidence as the dependency boundary. Do not rerun the sibling
  repository as a Weft gate.
- Run Weft's task/manager/heartbeat/monitor/pipeline and observer suites on
  both backends.
- Measure quiet CPU, strategy calls, durable pending checks, full task turns
  and wake-to-handler latency separately.

## Firing Test Matrix

| Contract | Required proof |
| --- | --- |
| Silent source means no work | Over a quiet window an idle ordinary task (settled title, transition reporting) runs zero full policy turns. On PostgreSQL it makes zero durable pending checks outside idle polls; on SQLite only after a `data_version` change. |
| SQLite quiet responsiveness | A real task quiets through `PollingStrategy`, a write moves `data_version`, and the handler runs with no process poll interval. |
| SQLite burst/backoff | Useful work resets burst; an empty/unrelated wake does not; quiet delay returns to the configured base interval and jitter. |
| PostgreSQL listener | A watched queue wakes the task immediately; an unrelated queue does not dispatch or reset useful-work state. |
| Hint semantics | Native/data-version activity always goes through a live pending check before reserve/read/peek. |
| Initial race | Work present before start and written between empty check and wait is handled exactly once. |
| Local events | With no deadline, worker result publication, finite worker-lane retirement, SIGTERM, parent loss and child exit are each handled within one strategy pass on both backends, with no task-reactor wait cap present. At default quiet settings the first wake is nominally within 100 ms plus configured jitter and scheduler delay; useful work then enters burst. |
| Local notification race | A foreign-thread or Python-signal notification racing strategy consumption is covered by the current or next source-state drain; the notifier executes only the coalescing latch assignment. The drain hint is false before owner wait consumption and true afterward. The bounded multi-queue path never applies the empty-check downgrade. |
| Self-write | An inactive watched queue populated on the reactor's own SQLite connection is found by the consumed-local-hint live pending check, marked for broad refresh and drained within one strategy pass, without the one-second inactive discovery backstop. |
| Timers | A service timer is observed at its native deadline or after at most one configured polling pass; timer expiry is not broker activity; a default-config ordinary task with a settled title passes no deadline. Poll reporting and title deferral publish their real deadlines. |
| Implicit-turn audit | A `reporting_interval == "poll"` task emits reports while idle; every audited time-dependent action has a published timer. |
| Probe expiry | An unanswered Manager probe concludes at its native deadline or the next configured SQLite pass, without a Manager-specific poll interval. |
| Orphan probe expiry | Leader or service evidence disappears after sending PING but before expiry; the expired unvisited probe retires request/reply rows and pending state on both backends and cannot pin the next timeout at zero. A second case forces post-write request-message-ID discovery to fail and proves target-queue plus request-ID cleanup removes only that keyed PING. |
| Child exit | One Manager-owned sentinel adapter wakes the reactor on process exit. Tests cover start-before-snapshot, add/remove, level-trigger suppression, stale `(tid, sentinel)` discard, observer failure, shutdown during exit, stop/join before strategy close, and exactly-once endpoint closure. It neither polls nor mutates the child registry; PID-view recheck and missing-terminal-proof grace use exact timers. |
| Topology lifecycle | Candidate waiter is installed before displaced close; stop/replacement races have one owner and one close. |
| Strategy lifecycle | First manual wait starts once; repeated manual waits preserve cadence; background retry restarts intentionally; BaseTask never double-starts; native failure detaches/closes once and continues on the same strategy in fallback; final cleanup closes once. |
| Stop local event | A stop request recorded from another thread or deferred signal is observed within one strategy pass on SQLite and PostgreSQL, with cleanup remaining on the owner. |
| Native waiter failure | A waiter failure during a finite native deadline detaches and closes the expected waiter once, preserves the retained strategy, and continues through polling fallback without a second owner or strategy. |
| Common task driver | `Consumer.run_work_item()` and normal task startup use the same process/wait/stop loop. |
| Wait audit | Every remaining wait is classified by owner, class, durable predicate and wake source; no retained-row observer spins. |

Use real queues and existing backend fixtures. Fake waiters are acceptable only
for deterministic ownership/race tests. Do not mock queue delivery to prove
responsiveness.

## Performance Gate

Use the same host and toolchain as the earlier measurements. Run three quiet
30-second windows after warmup for each backend, with no tracing during CPU
measurement.

Record:

- Manager CPU and idle ordinary-task CPU as percent of one core;
- PostgreSQL server CPU separately;
- strategy calls, data-version checks, native notifications, durable pending
  checks and full task turns;
- committed-message-to-handler latency in quiet and burst states;
- threads and physical connections for one and many watched queues.

Acceptance:

- idle Manager remains below 2.5% of one core on SQLite and PostgreSQL;
- an idle ordinary task runs zero full turns in the window;
- backend or local activity reaches the policy handler within one quiet
  strategy pass. At the default configuration that pass is nominally 100 ms,
  may include the existing configured jitter and scheduler delay, and becomes
  burst-fast after useful work. The current SQLite implementation is normally
  faster because it rechecks `data_version` in 20 ms chunks, but that chunk is
  an implementation detail rather than the contract;
- PostgreSQL remains notification-driven and acquires no fast SQL poll;
- useful activity enters burst mode and returns to the configured 100 ms quiet
  base interval with its existing jitter;
- no fixed manager, task-driver, worker-result, signal or child-exit interval
  schedules steady-state task-reactor turns; published timers are the only
  task-reactor deadlines. Independently named source-adapter, backpressure and
  finalization bounds remain outside the wake arbiter, including the launcher
  parent watcher and local worker-queue operation timeout.

## Verification

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/tasks/test_task_execution.py tests/core/test_manager.py tests/core/test_control_probe.py tests/core/test_queue_wait.py -q
bin/pytest-pg --fast
./.venv/bin/python -m pytest
./.venv/bin/ruff check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
git diff --check
```

## Implementation Evidence (2026-09-18)

The slice is implemented and validated in the current working tree. The plan
remains `draft` until the owner lands that tree; plan status records repository
history, not whether an uncommitted implementation has passed its gates.

The implementation uses `MultiQueueWatcher` as the sole retained-strategy
owner and `BaseTask` as the sole task drive owner. Local worker completion and
retirement, deferred signals, parent loss, stop, Manager child exit, and
same-connection self-writes record their source state before notifying the
strategy. Service tasks publish only their real clock deadlines. The retired
Manager/task/worker/signal/child-exit caps do not remain in production task
code. The Manager child adapter starts before its first membership snapshot,
owns one observer thread and pipe, and leaves all process and registry work on
the reactor thread.

The final backend gates passed:

- SQLite: `5145 passed, 28 skipped` from `./.venv/bin/python -m pytest`.
- PostgreSQL: `4996 passed, 13 skipped` from `bin/pytest-pg --fast`.
- Ruff, Ruff format, mypy across the repository and supported integrations,
  plan/spec hygiene, the Ruff suppression policy, the DOM-15 fixture check,
  suppression-index consistency, and `git diff --check` all passed.

The first independent implementation review found five remaining places where
implicit turns or failure ordering still violated that contract: an
unconditional zero timeout throughout Manager drain, a redundant sentinel PID
recheck during terminal-proof grace, child-observer failure aborting shutdown
reaping, the suppressed ServiceTask poll report retaining its inherited timer,
and stale child-launch recovery lacking a deadline after worker retirement.
The implementation now publishes exact drain, proof-grace and stale-launch
deadlines; preserves observer failure while completing child termination; and
removes the unused service deadline. Firing tests cover each correction. The
next two review passes found the remaining shutdown edges: a queued observer
failure after the final child had already been reaped, and an observer failure
published between child termination and adapter close. Shutdown now drains at
termination entry and again after the observer joins, so the exact failure
cannot be lost on either side of child cleanup. The fourth independent review
reported no blockers. The backend and static gate results above are the final
reruns after all review remediation.

CPU was measured without tracing after a five-second warmup in three separate
30-second quiet windows. Percentages are percent of one core:

| Backend | Window | Manager | Ordinary task | PostgreSQL server |
| --- | ---: | ---: | ---: | ---: |
| SQLite | 1 | 1.29% | 1.09% | n/a |
| SQLite | 2 | 1.11% | 0.92% | n/a |
| SQLite | 3 | 1.01% | 0.84% | n/a |
| PostgreSQL | 1 | 0.73% | 0.24% | 0.45% |
| PostgreSQL | 2 | 0.72% | 0.23% | 0.42% |
| PostgreSQL | 3 | 0.74% | 0.23% | 0.46% |

A separate five-second counted run avoided perturbing those CPU samples. On
SQLite it observed 194 strategy calls and 367 data-version checks; on
PostgreSQL it observed four strategy calls and zero data-version checks. The
ordinary task made one initial policy turn on each backend and zero policy
turns during the quiet measurement. The SQLite run recorded two durable
pending checks and the PostgreSQL run four, including startup/shutdown hints
and PostgreSQL's slow native safety passes; neither backend acquired a fast
full-turn poll. Native notifications were zero in the quiet window, as
expected.

Manager PING measurements used committed control rows and observed the matching
reply without instrumenting the Manager. SQLite's first quiet reply took
20.23 ms; subsequent active replies took 2.01 to 22.40 ms. PostgreSQL's first
quiet reply took 52.84 ms; subsequent active replies took 4.04 to 8.94 ms.
These are conservative end-to-end observer measurements rather than internal
handler timestamps, and all remain within one nominal 100 ms quiet strategy
pass.

Resource inventory did not scale with queue count. A standalone SQLite watcher
used one thread and three database/WAL/SHM file handles with both one and 32
watched queues. A standalone PostgreSQL watcher used six process threads and
two established TCP connections with both one and 32 watched queues. The idle
Manager used three threads on SQLite and eight on PostgreSQL; the idle ordinary
task used one and six respectively.

## Rollback and Stop Conditions

The change has no schema, queue-name or payload migration. The compatible
SimpleBroker release precedes the Weft scheduling slice. Old callers remain
valid because the no-argument strategy behavior is unchanged. Roll back Weft
independently by restoring its prior driver; dependency selection remains under
its separate owner, and the already-published optional SimpleBroker parameter
remains. Preserve accepted tasks and the separate submission session/scan
optimization.

Stop and re-evaluate if implementation requires a new public SimpleBroker
abstraction beyond the deadline above, a Weft-level wait slice or wait cap for
any local source, a second task drive owner, backend branching beyond
`uses_native_activity()`, a thread or connection per observed queue, retained
PEEK rows to drive readiness, or looser timeout/queue ownership semantics. If
a local source cannot meet one strategy pass through `notify_activity()`,
report the measurement instead of restoring a cap.

## Files and Ownership

Read first:

- `../simplebroker/simplebroker/watcher.py` and
  `../simplebroker/examples/reference_reactor.py`: the retained strategy and
  single-owner reference behavior;
- the SimpleBroker tests named under Compatibility targets;
- `weft/core/tasks/multiqueue_watcher.py`: sole owner of broker readiness,
  waiter membership, live pending checks and fair drain;
- `weft/core/tasks/base.py`: sole owner of the task process/wait/stop driver,
  local-event notification and timer composition;
- `weft/core/launcher.py`, `weft/manager_process.py` and
  `weft/core/manager_runtime.py`: process-boundary transport for the optional
  manual interval plus the independent parent-loss watcher cadence;
- `weft/core/manager.py`: manager timers including probe expiry;
- the Manager child-source adapter module selected during slice 3: sole owner
  of process-sentinel observation and its membership/stop wake handle. Keep it
  broker-free and registry-observer-only; if a new module is unnecessary,
  retain the same boundary inside `manager.py`.

Expected implementation files are those modules plus
`weft/core/tasks/consumer.py`, `weft/core/tasks/pipeline.py`,
`weft/core/tasks/heartbeat.py`, `weft/core/tasks/liveness_monitor.py`,
`weft/core/monitor/task_monitor.py` (timer audit only), `weft/_constants.py`,
Weft's focused tests and the cited specs. The sibling SimpleBroker source and
tests are read-only context for the already-published contract. Do not move
backend selection, waiter lifecycle or pending checks out of
`MultiQueueWatcher`. Do not add a second reactor base class.

The spec delta must replace the current description of a strategy-started but
manually waited task path with the Design Rule: one task owner drives the
retained watcher policy; wakes are hints followed by a live durable predicate;
local events enter through the strategy's local-activity notification; timers
are the only deadlines. Update nearby implementation mappings and backlink
this plan.

## Follow-up: event-routed Manager PONGs

The separate
[Event-Routed Manager PONG Plan](./2026-09-18-event-routed-manager-pong-plan.md)
owns this follow-up. Owner decisions recorded there select the probing
Manager's configured `ctrl_in`, treat `reply_to` as trusted nonblank routing
metadata, require one response destination, retain external blocking probes on
the target's `ctrl_out`, and intentionally provide no mixed-version
compatibility during beta. It also removes the restoration slice's temporary
50 ms leadership and 150 ms service probe-expiry clocks: a PONG is backend
activity, while no-PONG is evaluated on the next existing leadership or active
service policy timer. The separate one-second leadership PONG cache TTL is
folded into leadership-cadence-scoped reuse. This restoration plan does not
implement that wire or lifecycle change.

## Review Record (2026-09-18)

Changes from the first draft, with the evidence that drove them:

- Added the Design Rule and three-class table; the owner's intent, not the May
  plans, is the oracle. The May-05 plan specified the bypass.
- Corrected “one event source” to one wake arbiter with three input classes.
  Backend sources, local source adapters and real timers remain distinct; only
  the arbiter and task drive owner are singular.
- Deleted the PostgreSQL wait-slice loop and per-backend wait choice. The
  strategy already tests its local-activity latch every pass, so local sources
  use `notify_activity()` and the watcher makes one kind of call.
- Deleted the active-worker cap, parent-loss wait ceiling and the proposed
  signal-check deadline instead of keeping them "until proven".
- Narrowed the SimpleBroker deadline to the native-waiter branch. SQLite
  already returns after one configured quiet pass, so shortening that pass
  would recreate a second cadence. Defined that a truncated native pass does
  not count as a check.
- Rejected the earlier claim that the current `notify_activity()` is already a
  signal-safe seam. It mutates burst/backoff state and computes a randomized
  deadline. The upstream plan reduces it to one coalescing latch assignment and
  moves all policy mutation to the strategy owner.
- Named `_on_data_version_change()` as the SQLite hint seam and made the
  strategy idle poll the single PostgreSQL safety recheck.
- Added the self-write rule (`data_version` is blind to same-connection
  writes) and the implicit-turn audit (poll reports would stall once the 50 ms
  turn is gone).
- Corrected PONG behavior: a write to an unwatched responder `ctrl_out` is
  irrelevant to the probing Manager on both backends. SQLite observes the
  database change but filters it out; PostgreSQL never receives the queue
  notification. Both therefore use expiry until the reply-to follow-up lands.
- Classified child exit as a positive local event and replaced its 50 ms poll
  with one Manager-owned sentinel adapter. Only missing terminal proof after
  exit remains a timer.
- Removed observation interests, the [QUEUE.7]/[QUEUE.8] delta and the
  `QueueChangeMonitor` conversion. Probe expiry stays as a timer; reply
  delivery moves to the reply-queue follow-up.
- Added slice 0 because the working tree mixes this experiment with the
  submission optimization in the same files.
- Added a live watched-queue check on local-hint consumption so SQLite
  same-connection self-writes cannot remain hidden on inactive queues.
- Added end-of-turn retirement for expired probes whose originating evidence
  disappeared, preventing a stale probe from pinning the next timer at zero.
- Made `MultiQueueWatcher` the one strategy lifecycle owner across manual,
  inherited and task-driven entry paths.
- Specified the child-sentinel adapter's coalesced pipe-token protocol, stale
  identity handling, failure behavior and shutdown order.
- Changed promotion from post-implementation reconciliation to strategy A so
  the reviewed reactor contract governs the firing tests and code.
- Added exact replacement anchors for [CC-2.1], [CC-2.5], [MA-1.6a] and
  [SB-0.4], including removal of the old direct-native-wait and worker wake-cap
  text before strategy-A promotion.
- Chose one self-write rule: same-connection writes always notify the retained
  strategy; Manager's zero-timeout internal-spawn continuation is removed.
- Made keyed request identity survive failed message-ID discovery so orphan
  probe cleanup remains exact, and added that failure as a firing case.
- Assigned the complete abandoned interval subtraction to slice 0; slice 1 now
  only proves the bypass.
- Restored the release boundary: SimpleBroker passes and releases against its
  own contract first. Weft consumes the owner-published version afterward; no
  Weft install or test gates the upstream release.
- Rechecked the plan against the exact published SimpleBroker 8.4.0 code and
  promoted `[SB-API-6]` contract. The deadline and one-latch implementation
  match the design; no reactor redesign is needed.
- Recorded the deferred-hint rule: `notify_activity()` does not publish the
  drain hint until the owner drives `wait_for_activity()`. Prohibited the
  upstream empty-check downgrade on Weft's bounded multi-queue path because it
  can hide remaining backlog or a coalesced same-connection self-write.
- Removed dependency declaration, lockfile and artifact selection from this
  plan. They are confirmed under separate ownership; this plan verifies only
  the behavioral seam it consumes.

## Deviations From the Superseded Draft

The superseded central-polling draft misread an existing design as a missing
abstraction. It proposed `WaitReason`, `wait_once`, native local wake and a
strategy factory in SimpleBroker. Those additions are rejected. The existing
watcher reactor is the design. The upstream delta is the native-wait deadline
that gives service timers the same roughly 100 ms precision on listener-backed
and polling backends, plus a small
correction that makes `notify_activity()` a true local-input seam rather than
mutating owner state from foreign threads or signal handlers.

Independent review must compare the final implementation to the Design Rule,
`BaseWatcher._process_messages()` and the existing SimpleBroker responsiveness
tests. A review that merely checks the new code against this plan, or against
the 2026-05-05/2026-05-15 plans, is insufficient.

## Completion Record (2026-09-21)

The reactor restoration and reply routing landed in `4370bb0f`. Subsequent
interactive-source work in `970cc097` applied the same retained-strategy local
notification rule to session output and process exit. The implementation is
complete; later work is maintenance of the restored reactor contract rather
than an open slice of this plan.
