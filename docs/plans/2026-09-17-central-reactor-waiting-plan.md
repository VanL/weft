# Central Reactor Waiting: Audit and Implementation Plan

Status: draft
Source specs: docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2.1], [CC-2.5]; docs/specifications/03-Manager_Architecture.md [MA-1.6a]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-3]; docs/specifications/07-System_Invariants.md [IMPL.8], [IMPL.9], [IMPL.10]; docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2], [CLI-1.3]; docs/specifications/14-Python_API_Surfaces.md [PY-2]
Superseded by: [Watcher Reactor Restoration Plan](./2026-09-17-watcher-reactor-restoration-plan.md)

This draft incorrectly treated architectural drift as a missing upstream
abstraction. The replacement plan restores the existing `MultiQueueWatcher`
reactor and rejects the proposed SimpleBroker API expansion.

Class: 5. Proposed changes to the shared waiting contract, broker extension
boundary, reply observation, cancellation, and runtime scheduling. Hardening
applies. This task produces an audit and plan, not runtime implementation.

## Outcome and Scope

- One broker waiting policy, owned by SimpleBroker, drives both task reactors
  and synchronous/streaming observation adapters.
- Each task retains one owner-thread reactor. Broker activity, local completion,
  cancellation, pending reply interests, and due timers feed that owner; none
  requires a task-specific broker poll loop or a second task driver.
- Audit all production wait families, including apparently event-driven paths
  whose subscriptions, deadlines, local wakes, or failure recovery are incomplete.
- Preserve durable semantics and prove real SQLite/PostgreSQL behavior before
  removing periodic checks that currently mask missing events.
- Measure idle CPU and latency separately. Retain the user's local target of
  less than 2.5% of one core; do not choose architecture by shortening sleeps.

"Central" means a common implementation/policy with one wait owner per task or
caller scope and effective broker context. It does not mean one process-global
reactor, a new daemon, a durable event bus, or funneling every task through the
Manager. Operating-system and SDK work remains in bounded source adapters or
worker lanes. All waits are audited; not all are broker waits.

## Baseline, Prior Work, and Required Reading

Weft baseline: `3faeff21f527bbd32c8ccfb53d662a68a0ccb024`, plus the preceding
submission-resource and fixed-interval experiment changes in this session.
SimpleBroker baseline: `9efa0a5acd06091b2c68e673fe3e4ae1bdbb9c35`; its `simplebroker/watcher.py` and bundled PG
`runner.py` matched the installed copies at audit time. Source locations below
refer to these files/functions; line numbers in the working tree are not a
stable identifier. Record exact full SHAs and diffs again when implementing.

This plan supersedes the **direction** of
[Manager Polling Latency](2026-09-17-manager-polling-latency-plan.md): it retains
its measurements and counterexamples but rejects independent 50 ms Manager and
25 ms PONG caps as the permanent solution. It does not supersede the
[submission manager check cost work](2026-09-17-submission-manager-check-cost-plan.md).

First implementation slice must retire only the fixed-interval experiment:
`MANAGER_FALLBACK_POLL_INTERVAL_SECONDS`, `_fallback_poll_interval`, its sleep
clamp, `CONTROL_PING_POLL_INTERVAL_SECONDS`, associated scheduling assertions,
and the spec03/spec05 paragraphs prescribing those independent intervals.
Preserve earlier submission scan/session changes and their tests/docs. Keep
useful generic tests for native wait preservation, immediate replies and shorter
deadlines, rewriting their oracles around the shared strategy. Never reset whole
files to HEAD: the two efforts share specs and the plan index.

Required context: the source specs above; `00-Overview_and_Architecture.md`;
agent-context decision hierarchy, engineering principles, runtime/context,
testing, writing-plans, hardening and review runbooks; lessons on manager hot
loops, poll floors, admission, reactor ownership, connection reuse and distinct
resource lifetimes. For upstream work, read `../simplebroker/AGENTS.md` and its
required context, `docs/specs/16-python-library-api.md` [SB-API-6], watcher tests,
and bundled PostgreSQL activity-waiter lifecycle tests before edits there.

Comprehension questions: why is timer expiry not queue activity? Why can an
unmatched retained PONG or blocked spawn row not count as useful work? Why does
setting a Python Event not interrupt a PostgreSQL Condition wait? Why can a new
SQLite connection's unchanged raw data_version still require reconciliation?
Why must observer interests remain separate from immutable task dispatch queues?

## Audit Method and Findings

Read-only production audit covered `weft/`, `integrations/`, and `extensions/`,
plus SimpleBroker watcher and bundled PG listener implementations. Search all
Python sleep/wait/poll/deadline/data-version/strategy uses; supplement with while
loops, joins, queue gets, select and async waits. The initial Weft searches found
117 wait/policy references and 261 secondary control-flow references, **not**
378 independent loops. Inspect surrounding call chains, resource lifetimes,
specs, and closest tests. Exclude test scaffolding and user-supplied task code
from production migration, while retaining them as validation inputs. CLI/client
wrappers that delegate are covered by their owning command functions.

Reproduce the search with `rg -n 'sleep\(|\.wait\(|wait_for_activity|PollingStrategy|data_version|poll_interval|polling_interval' weft integrations extensions --glob '*.py'`
and `rg -n 'while |\.poll\(|\.join\(|select\(|get\(.*timeout' weft --glob '*.py'`.
A hit alone is not a finding; the dispositions below are source-reviewed.

### A. Task and broker-source audit

| ID | Owner / current path | Finding and required disposition |
| --- | --- | --- |
| A01 | `weft/core/tasks/base.py::run_until_stopped`, `_ensure_task_strategy_started`, `_wait_for_reactor_activity`; `weft/core/tasks/multiqueue_watcher.py::_wait_for_activity_body` | Every task starts the inherited strategy but normal manual driving bypasses its wait. PG uses its native waiter directly; SQLite performs queue precheck plus Event sleep. Route both through the same strategy; retain the existing one-owner lifecycle. |
| A02 | `simplebroker/watcher.py::PollingStrategy.wait_for_activity` | Adaptive SQLite data-version and PG wait exist, but no caller timeout or genuine local interrupt. Add the narrow upstream capabilities before changing task scheduling. Preserve 100 initial checks, configured 100 ms max backoff and burst behavior; installed SQLite backoff also checks versions in at-most-20 ms chunks. |
| A03 | `weft/core/tasks/base.py::_publish_worker_result`, `_sync_worker_result_event` | Worker Event is set but not part of active broker waiting. A 50 ms active-worker cap substitutes for a wake. Add a latched local interrupt, preserving bounded publication/drain and broker-free workers. |
| A04 | `weft/core/manager.py::_manager_pong_dispatch_proof`, `_advance_manager_pong_probe`, `_service_candidate_from_pong`, `_advance_service_pong_probe` | Probes are incremental but reply ctrl_out queues are absent from wait membership and pending deadlines absent from next_wait_timeout. Add temporary read-only interests and earliest probe deadlines; retire all exits correctly. |
| A05 | `weft/core/control_probe.py::send_keyed_ping_probe` | Independent scan/sleep loop, used by manager runtime, task evidence, heartbeat endpoint and task ping. Replace waiting with a shared observer scope; preserve exact request matching, cleanup and uncertainty. Do not invoke a synchronous nested task driver inside a reactor turn. |
| A06 | `weft/core/manager.py::_seed_child_inbox` | Broker-write retry sleeps occur on the reactor before launch. Represent retries as owner state plus due deadline; seed-before-launch and typed failure remain mandatory. |
| A07 | `weft/core/tasks/base.py::_write_state_queue_message` | Terminal publication retry sleeps can block the owner. Use a bounded terminal-delivery/finalization mode and explicit retry deadlines; preserve [OBS.1] terminal-proof priority. No recursive reactor drive during finalization. |
| A08 | `weft/core/tasks/consumer.py::run_work_item` | Alternate direct-execution adapter uses process_once/wait with hardcoded .05. Reuse shared driving/deadline behavior without changing its result/error contract or creating a second owner. |
| A09 | `weft/core/tasks/interactive.py::_interactive_flush_outputs`, `_interactive_drain_remaining`, `_interactive_shutdown`; `weft/core/tasks/sessions.py` | Reader threads buffer output/EOF but do not wake task wait; terminal drain and STOP contain nested sleep loops. Feed local output/EOF/exit events to the owner; make live-task shutdown incremental, bounded and fair. |
| A10 | `Manager.next_wait_timeout`, `HeartbeatTask.next_wait_timeout`, `TaskMonitor.next_wait_timeout`, `LivenessMonitor.next_wait_timeout` | Due schedules are legitimate; arbitrary queue-response caps are compensation. Keep leadership, registry, admission, service, heartbeat, monitor, idle and filesystem timers; remove compensating caps only after event inputs work. TaskMonitor intentionally does not subscribe to retained task-log history. |
| A11 | `weft/core/manager.py::_cleanup_children`, `_wait_for_children_to_exit`, `_drain_active_child_launches_for_cleanup`; `weft/core/tasks/service.py::_stop_service_worker` | Process observation and terminal cleanup have real deadlines. Feed active-process observation through a local event or named OS timer. Finalization reuses deadline/local-wake mechanics but never resumes normal dispatch. |
| A12 | `weft/core/tasks/service.py::ServiceWorkerContext.get_work`; BaseTask result queue; watcher topology rendezvous | Blocking worker queues/backpressure and foreign-thread mutation acknowledgement are legitimate. Preserve execution placement. Local wake/sentinel cancellation may replace timeout checks, but not by moving work onto the reactor. |
| A13 | `weft/core/tasks/pipeline.py`, pipeline edge, heartbeat and monitor task families | No additional independent broker wait loop found beyond inherited task path. Regression coverage must prove they adopt shared waiting, maintain due timers and preserve queue policies. |
| A14 | `weft/core/heartbeat.py::ensure_heartbeat_service` | External endpoint startup repeatedly resolves registry and sleeps. Use shared predicate wait over endpoint/service/task evidence with original total deadline. |
| A15 | `BaseTask.run_until_stopped(poll_interval=...)`; `weft/core/launcher.py`; `weft/core/manager_runtime.py::_build_manager_process_command` and `_run_manager_process_foreground`; `weft/manager_process.py` | Production bootstrap passes a fixed TASK_PROCESS_POLL_INTERVAL even for tasks with no due timer. Remove this implicit application deadline from production; otherwise merely routing the wait still wakes every 50 ms. Retain the optional manual-driver argument, default None, with deliberately revised maximum-wait semantics described below. Separate the parent-loss OS watchdog cadence from this retired default. |
| A16 | `BaseTask.note_termination_signal`; `launcher.py::_install_signal_handlers` | Signal handlers only append to a deque and cannot safely call locking wake APIs. Register a named central signal-check deadline while these handlers are installed; inspect the latch without broker I/O, and enter termination policy only when pending. No separate polling loop. |

### B. Adapter and observer audit

| ID | Owner / current path | Finding and required disposition |
| --- | --- | --- |
| B01 | `weft/core/queue_wait.py::QueueChangeMonitor` | Third wait architecture: SQLite creates one QueueWatcher thread per queue; PG uses a fan-in waiter thread. Consolidate behind the shared strategy, one owner scope, exact interests. A native error currently signals once then exits without recovery: outer polling is masking that failure. |
| B02 | `weft/commands/_result_wait.py::await_one_shot_result`; `weft/commands/result.py::_await_single_result` | Already event-assisted but independently cap rescans. Keep completion/output/batch/quiet grace semantics, move deadlines into the common wait, then remove only redundant safety caps. |
| B03 | `weft/commands/result.py::_await_result_materialization` | Watches only task log while testing additional task-state/output/control evidence. Complete interests or retain a named discovery deadline until routing is known; otherwise a purely event-driven rewrite can hang. |
| B04 | `weft/commands/events.py::iter_task_events`; `weft/commands/system.py::_iter_public_status_events_owned`; `weft/commands/queue.py::watch_queue`; `weft/commands/tasks.py::watch_task_status` | Consolidate source waiting, preserve append cursors and consume/peek/move distinctions. Existing public interval parameters remain maximum observation/reconciliation intervals; represent them as caller deadlines, not a second polling engine. |
| B05 | `weft/commands/events.py::iter_task_realtime_events`, `_open_realtime_routes` | Dynamic route subscriptions, fixed rescan caps, grace deadlines and cancellation checked between waits. Add state interests, owner-serialized replacement/recheck and direct cancellation wake. Preserve partial frames and terminal/output precedence. |
| B06 | `weft/commands/_spawn_submission.py::reconcile_submitted_spawn` | Dynamic registry/log/spawn/reserved observers and capped waits. Preserve accepted TID and queue-first ownership; discover/replace interests without a lost-publication gap. |
| B07 | `weft/commands/tasks.py::_wait_for_control_surface`, `_ControlSurfaceResources` | Good migration test: dynamic task/pipeline/control interests plus STOP/KILL grace timers. Keep proof precedence and authority while moving waiting. |
| B08 | `weft/commands/tasks.py::task_terminal_snapshot` | Raw sleep between state/mapping/evidence reads. Use nonconsuming shared predicate observation; timeout remains caller-local and must not publish task timeout. |
| B09 | `weft/commands/task_monitor.py::_TaskMonitorSummaryStream.__next__` | Raw catchup sleep reruns observer reduction; close only sets a flag. Use log activity plus legitimate summary/reconciliation timer, and interrupt wait on close. Keep high-water/checkpoint semantics. |
| B10 | `weft/commands/interactive.py::InteractiveTaskClient` | Existing background watcher + completion Event/response Condition is genuinely event-driven. Reuse common strategy on its existing owner thread. Do not move queue consumption to waiting callers. |
| B11 | `weft/commands/run.py::_InteractiveRunLifecycle.wait_for_completion`, `request_exit` | Separate .05 loop observes completion, log and external Monitor store; control waits sliced for completion checks. Compose broker/local/control inputs and deadlines. External store needs an explicit reconciliation timer unless a supported event source is established. |
| B12 | `weft/core/manager_runtime.py::start_manager`, `_await_manager_start_settlement`, `_await_manager_stop_confirmation` | Registry events plus periodic process evidence. Share queue source; retain competing-startup and stop budgets, and explicit PID/process recheck deadlines. Broker silence cannot prove process death. |
| B13 | `weft/client/*`, CLI delegation, `integrations/weft_django/weft_django/client.py` | No distinct timed loop found; migrate owning commands and test public adapters. |
| B14 | `integrations/weft_django/weft_django/channels.py` and async realtime delivery | Async cleanup/advance waits are not broker polling. Cancellation must wake underlying observer; generator creation/next/close stay serialized on its owner thread. Caller timeout is not proof that worker cleanup ended. |

### C. External and terminal waits: audited, explicit dispositions

| ID | Owner | Disposition and boundary |
| --- | --- | --- |
| C01 | `weft/core/launcher.py::_start_parent_loss_watcher` | Retain portable OS watchdog polling unless a supported parent-exit primitive replaces it. Signal common local wake on loss. It must still detect loss if ordinary task work is stuck. |
| C02 | `weft/manager_detached_launcher.py` handshake; `manager_runtime.py::_read_launcher_first_line` | Bootstrap process/pipe observation precedes a usable task reactor. Preserve bounded portable fallback (Windows anonymous pipes cannot use ordinary select). Source adapters may emit readiness; do not substitute broker events for pipe/process proof. |
| C03 | `weft/helpers/__init__.py::_wait_for_verified_processes`; `tasks.py::_observed_host_pids_are_dead`; process waits/joins | OS evidence, not queue work. Preserve PID/create-time identity and owned reap. Use local process completion where available; otherwise named bounded timer, including teardown. |
| C04 | `weft/core/runners/subprocess_runner.py::run_monitored_subprocess`, `_drain_streams_until_closed`; host runner/terminal handoff | Broker-free worker-local process/pipe/resource monitoring. Keep it off reactor; forward result/progress promptly. Poll-based source adapters may remain bounded where OS/SDK cannot signal. Blocking select/pipe waits already are events. |
| C05 | `extensions/weft_docker/weft_docker/_sdk.py::wait_for_container_runtime_start`, `plugin.py::_wait_for_container`, agent runner execution | External Docker readiness/status/timeout checks. Retain bounded SDK adapter pending a separately proven Docker event/wait source. Central broker policy must not absorb Docker API semantics. |
| C06 | `extensions/weft_microsandbox/weft_microsandbox/_runtime.py` execution await | Execution completion is event-driven, but .05 timeout checks cancellation callback. Add awaitable/local cancellation bridge when runner interfaces support it, preserving kill and bounded unwind. This is a local-source migration, not broker polling. |
| C07 | `weft/commands/interactive.py` completion/response Conditions; CLI input/terminal prompts | Genuine local/terminal blocking events. Retain, connect completion/cancellation into common wake where needed. |
| C08 | `weft/helpers/__init__.py::write_file_atomically` | Bounded PermissionError filesystem replacement retry. Keep a documented leaf-I/O exception; if invoked in a responsiveness-sensitive reactor path, move blocking file work to its permitted worker, not to a broker waiter. |
| C09 | ResourceMonitor timing, TaskSpec polling_interval, worker queue timeouts, finite history/pagination/drain loops | Sampling cadence/backpressure/finite iteration are not independent queue event sources. Preserve declared behavior; do not globally rewrite these constants or loops. No separate broker poll found in macOS sandbox adapter. |

The audit is architectural, not a claim that every listed path is faulty.
C01-C09 are explicit retained or staged source-adapter cases, not exclusions
from inspection. A future implementation must either preserve these reasons or
replace them with a concrete event source and prove the same semantics.

## Why the Divergence Happened

History shows separate local decisions, not one recent regression:
`36704985` (May 5) introduced native-or-timeout manual waiting;
`a6ea4b3a` (May 7) introduced the synchronous keyed PONG scan/sleep helper;
`b4df6fef` (May 14) let Manager due timers replace the caller poll interval;
`73e4c74f` (May 15) added worker-result lanes with bounded response caps;
`70df4cb5` (July 10) strengthened ownership around the existing wait body.
The current specs distinguish background PollingStrategy from task manual
waiting, so this requires an explicit spec correction as well as code.

## Invariants and Failure Model

1. Queues remain truth. A wake is a hint, never proof of liveness, delivery,
   termination, capacity or dispatch permission. Re-read authoritative state.
2. One task drive owner; no nested process_once, background task driver, broker
   I/O from ordinary workers, or caller-thread handoff of synchronous iterators.
3. Dispatch queue topology stays construction-fixed. Transient observation-only
   interests do not grant READ/RESERVE ownership or handler registration.
4. Preserve priority, budgets, reservations, exact keyed reply cleanup, admission
   suppression and retained-row rules. Pending is not synonymous with actionable.
5. Arm/subscribe before relying on an empty predicate, then recheck. Publication
   before first wait, between precheck/arm, during membership replacement, and
   during local-event clear must not be lost. Coalescing is permitted.
6. Timers use monotonic wait budgets and advance independently of traffic.
   Existing durable wall timestamps/freshness semantics remain unchanged.
7. No transaction or unclosed iterator spans wait/yield. Queue lease, operation,
   caller-thread core, listener and session have explicit distinct owners.
8. Stop interrupts waiting; active owner unwinds before close/replacement. Cleanup
   has one absolute budget and no new normal dispatch. Failed close is visible.
9. Native waiter failure must retain a bounded path to authoritative checks.
   Do not delete compensating caller caps until that path is proven.
10. Preserve zero-timeout no-broker-probe semantics, public timeout/interval
    meaning, accepted-TID reconciliation, and output/terminal proof precedence.

## Proposed Spec Delta

These are proposed replacement/addition paragraphs for review, not active rules
from this plan. Promotion strategy **A**: after upstream API review and delivery,
promote the corresponding Weft contract immediately before each implementation
slice, without new mapping claims until reciprocal code exists. Existing active
spec files stay active. Record each promotion SHA or diff-base/content hash.
Planning alone does not promote these rules or erase the current spec baseline.

### 01-Core_Components.md [CC-2.1], [CC-2.2.1], [CC-2.5]

> Background watchers, manually driven tasks, and caller-owned observers use
> one SimpleBroker-owned broker activity strategy. No Weft task or observer
> defines an independent queue-read/sleep policy. Each drive owner waits for
> broker activity, local completion/cancellation, or its earliest due deadline.
> No implicit task-process polling interval is an application timer. A task
> with no due work supplies no application deadline; None means no deadline,
> while zero means an immediate local timer boundary. An explicitly supplied
> manual-driver interval is a maximum wait: choose the minimum of it and the
> next task deadline, ignoring absent bounds; zero wins. This deliberately
> replaces the old task-timeout override semantics. It is not a second broker
> policy. An ordinary timer wake is not queue activity and does not itself trigger broad
> queue discovery. The broker strategy owns adaptive checks and safety rechecks;
> task policy owns authoritative predicates and timer actions.
>
> Every task retains one drive owner and fixed dispatch topology. The owner may
> add or remove bounded read-only observation interests for pending replies or
> lifecycle evidence between turns. Such interests neither consume messages nor
> add handlers, reservation rights, or public topology mutation. Membership
> replacement is serialized with waiting and closing, followed by a durable
> recheck. Local signals are latched and interrupt the shared wait without
> broker I/O from their publishing worker. Python OS-signal handlers remain
> append-only and acquire no locks; a named central signal-check timer observes
> their latch. An empty signal check causes neither broker scans nor policy turns.
>
> Controls, worker progress, output and due timers are drained in bounded batches
> so no source starves the others. Live-task control handlers must not run nested
> sleep/retry/drain loops. Finalization has a separate bounded terminal mode and
> does not recursively enter public reactor driving.

### 03-Manager_Architecture.md [MA-1.6a] and leadership/probe rules

> The Manager uses the shared task event wait, with no Manager-specific polling
> interval. Housekeeping and admission retries retain explicit due deadlines.
> Pending leadership/service probes register read-only reply interests and
> their own deadlines; response or expiry schedules owner-side reconciliation.
> A reply is matched and retired only by its existing request owner. Blocked
> spawn sources and stalled control rows remain suppressed until actionable.

Replace the experimental 50 ms fallback paragraph; preserve unrelated
submission-resource implementation notes.

### 04-SimpleBroker_Integration.md [SB-0.4]

> SimpleBroker owns backend activity detection, configured adaptive polling,
> local wake interruption and bounded safety rechecks. Weft composes interests
> and deadlines through that supported API; it does not issue its own PRAGMA
> data_version, manage backend listener internals, or duplicate burst policy.
> SQLite uses normalized connection-aware version evidence; PostgreSQL uses the
> native listener when usable. Native failure must continue through explicit
> strategy-owned bounded fallback rather than leave a silent dead observer.
>
> Every observer registers exact known interests before first publication or
> reliance on an empty read, then checks durable state. Unknown routing is
> covered by a named discovery deadline until interests are complete. Waiters
> remain hints; a same-owner write schedules immediate recheck/local activity.
> Resources are created, replaced and closed by their scope owner, with no
> transaction spanning the wait. Retained unrelated rows do not reset burst or
> cause an endless ready loop.

### 05-Message_Flow_and_State.md [MF-3]

> All keyed PING reply waiting uses the shared activity mechanism. A synchronous
> external caller owns a bounded observer scope. A task owner represents an
> in-flight probe as incremental state, including reply interest and deadline;
> it does not block or recursively drive its task while awaiting the reply.
> Match, expiry, I/O failure, owner-record replacement and stop retire observation
> interests and owned probe state. Exact request-id matching/deletion and late
> reply hygiene remain unchanged. Probe timeout never proves process death.

Replace the experimental 25 ms PONG sleep rule; retain the single-reader and
exact-ID retirement contract.

### 07-System_Invariants.md [IMPL.10] (additive)

> The one task drive owner also owns its activity strategy and observation
> interests. Broker, local and timer readiness share that wait boundary.
> Cross-thread signal publication is allowed only through the thread-safe local
> wake handle and permitted worker-result channel; it neither transfers broker
> ownership nor executes task policy. Shutdown/terminal retries preserve the
> same owner and absolute budget without resuming ordinary dispatch.

### 10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2], [CLI-1.3]; 14-Python_API_Surfaces.md [PY-2]

> CLI and Python observation loops retain their public outputs, cursor and
> consume/peek semantics, maximum observation interval arguments, cancellation
> behavior and caller-local timeouts. They share the core activity source and
> express grace periods, route discovery, external evidence reconciliation and
> user-specified maximum intervals as explicit deadlines. Closing a stream
> interrupts its wait; iterator advancement and cleanup remain serialized on
> its owner thread. Expiring a wait does not cancel or time out durable work.

Upstream exact API delta and implementation slices are specified below; its
[SB-API-6] promotion must precede reliance on new APIs in Weft.

## Concrete Ownership and API Design

### SimpleBroker owns the mechanism

Extend existing `simplebroker/watcher.py`, not a Weft clone of PollingStrategy.
Proposed additive public API in `simplebroker.ext` under [SB-API-6]:

```python
class WaitReason(Enum):
    BROKER_ACTIVITY = "broker_activity"
    POLL_DUE = "poll_due"
    LOCAL_WAKE = "local_wake"
    DEADLINE = "deadline"
    STOPPED = "stopped"


# On the existing PollingStrategy:
def wait_once(self, *, timeout: float | None = None) -> WaitReason: ...
def wake(self) -> None: ...


# Factory returns existing initialized strategy; no adapter thread or queue lease.
def create_polling_strategy_for_queues(
    queues: Sequence[Queue],
    *,
    stop_event: threading.Event,
    config: Mapping[str, Any] | None = None,
) -> PollingStrategy: ...
```

Retain existing `wait_for_activity() -> None` as a compatibility adapter calling
`wait_once()` and discarding its result. There is one algorithm, not two waiting
implementations. With nonempty interests, the adapter must retain periodic POLL_DUE returns even with timeout=None, so standalone QueueWatcher still runs its pending check. Existing injected/custom strategy implementations must either
implement the additive bounded interface or be rejected explicitly by new
bounded callers; do not silently ignore deadlines. Existing unbounded callers
retain their supported behavior. The upstream API/spec review owns exact type
exports and compatibility tests before release; these signatures are the plan's
proposal, not claims that the APIs already exist.

Extract the connection-normalized provider currently inside
`BaseWatcher._start_strategy` into one private helper used by that method and
the factory. Same raw version on a replacement connection is a reconciliation
point. The factory reuses the existing native factory compatibility_key validation to reject mixed broker targets before provider construction, uses one queue for
SQLite's database-wide version and all supplied names for native fan-in, and
borrows queues. Caller closes strategy before its queue leases. For empty
interests the strategy handles local/deadline waits without invoking the native
factory with an empty list; no database probe is needed. Empty membership
suspends broker backoff and POLL_DUE: only local wake, stop and caller deadlines
remain (including a named raw-Event compatibility-check deadline when needed).
The factory creates no strategy/adapter-owned thread; native setup may create
SimpleBroker's existing shared PostgreSQL listener thread.

`wait_once` semantics:

- Compute one monotonic deadline and clamp every native wait, burst sleep and
  SQLite backoff chunk to remaining time. Keep the strategy alive across calls;
  timeout expiry does not reset adaptive state.
- STOPPED wins when stop is already set; an otherwise zero/expired caller
  timeout returns DEADLINE without broker I/O or clearing queued local/broker
  activity. LOCAL_WAKE does not mean broker backlog. Simultaneous sources
  remain latched for subsequent turns; consuming one reason cannot erase another.
- BROKER_ACTIVITY means a version/native hint; POLL_DUE authorizes the bounded
  safety/fallback authoritative check. Both require semantic reread. DEADLINE
  and LOCAL_WAKE alone do not force broad queue discovery.
- Retain existing burst hints and configured initial checks/backoff. Confirmed
  useful work may reset burst; timer expiry, arbitrary wake calls, unmatched
  replies and unrelated writes must not keep burst permanently active.
- Prime version/subscription state before relying on an empty initial read.
  Perform a post-registration authoritative check; never wait across an
  unprotected initialization or replacement gap.

`wake()` is the only new cross-thread entry point. It performs no broker I/O,
no callback dispatch and no ownership transfer. It latches a generation/pending
signal before interrupting the current wait. Use a separate wake generation from existing notify_activity/_local_activity_pending, whose useful-activity semantics can reset burst. SQLite uses the strategy's local
interruptible wait; PG needs an optional native-waiter `wake()` capability in
`simplebroker/_backend_plugins.py` and
`extensions/simplebroker_pg/simplebroker_pg/runner.py`, implemented under the
listener condition lock. Waking just before wait must remain observable.
Never close or replace a waiter from a publishing worker to interrupt it.
Native wake unblocks wait without fabricating broker activity or consuming real
queue notification versions. After native return, the strategy checks its local
generation and preserves any concurrent broker hint for a later turn. Protect
native-handle publication and wake-versus-replace/close lifetime with bounded
synchronization; never hold that lock across blocking wait. A wake concurrent
with replacement must reach either the current wait or the next wait through
the latched generation. Closed handles must not be used by a late publisher.

Built-in SQLite/PG must support prompt local interruption. Legacy third-party
native waiters without the optional capability retain an explicitly bounded
strategy-owned compatibility fallback for local/stop checks, using broker
configuration rather than a private Weft task interval. That degraded capability
must be documented and tested; it cannot claim immediate wake. Raw externally
supplied threading.Events or cancellation callbacks likewise need either a
known producer wired to `wake()` or a named shared compatibility-check deadline;
Python Events do not provide callback registration. Do not monkeypatch them or
create a polling thread per event.

Native failure is explicit: after failed wait unwinds, its owner detaches and
closes the waiter once, returns POLL_DUE, and continues strategy-owned bounded
polling for the remainder of that owner scope. PostgreSQL may return no useful
data-version token; periodic authoritative POLL_DUE remains necessary. This
first implementation does **not** promise automatic listener reconnection.
Demotion is latched for the strategy owner lifetime: changing reply membership
must not silently retry native acquisition or reset the failure backoff. The
owner's interest replacement path preserves this state and uses polling while
demoted; a genuinely new strategy scope is the next native setup attempt.
Current PG registry acquire can return the same failed shared listener, and
release is keyed only by DSN/schema. Safe automatic recovery would separately
require health-aware listener generations and identity-bound releases; it is
outside this slice. New observer scopes may attempt native setup normally but
must demote safely if that shared listener is still failed.

### Weft owns dispatch, interests and semantic deadlines

`MultiQueueWatcher` and BaseTask continue to use their existing strategy instance.
The protected shared wait delegates to `wait_once` and translates readiness into
existing queue-discovery/local/due-work handling. Do not construct a second
strategy for a task. Queue eligibility filtering remains Weft policy. On
POLL_DUE, the shared owner wait performs bounded eligible dispatch checks AND
owner-confined observation-interest reconciliation. Each keyed reply interest
supplies a read-only matching predicate/cursor; a newly matched reply permits a
policy turn even when no dispatch queue has work. BROKER_ACTIVITY uses the same
combined readiness gate. Unmatched retained replies do not count as useful work.
If no dispatch, observation, local or timer work is ready, resume waiting under
the original absolute deadline rather than running the full Manager policy turn.
No subclass reimplements this wait/check loop. Count checks and turns separately.

Production driving no longer injects TASK_PROCESS_POLL_INTERVAL or the foreground
MANAGER_POLL_INTERVAL alias as an application deadline. Make run_until_stopped
and launch_task_process(poll_interval=...) optional with default None, retaining
explicit caller arguments through task bootstrap. Deliberately change explicit
interval semantics to min(explicit_limit, task_due), ignoring absent bounds;
zero wins. This is not preservation of the old task-timeout override: update
`test_task_run_until_stopped_uses_next_wait_timeout` (.25 previously became .75)
and the governing spec. Remove the detached manager interval transport field
and foreground fixed argument together; remove aliases only once unused.
Update launcher/config-transport test fakes and manager-process parser tests.
Keep the parent watchdog's OS cadence under its own named policy.
None on task wait means no application deadline; update the old manual no-op
expectation deliberately. Zero remains no broker I/O. Public CLI/client timeout
semantics are unchanged.

OS signals are a distinct input. `note_termination_signal` stays append-only:
never call Event.set, strategy.wake or broker operations from a Python signal
handler. For this migration, while these handlers are installed, register a
named 50 ms signal-check deadline in the central owner wait. Its due callback
only checks the in-memory latch; no pending signal means rearm and resume without
queue scans or a full policy turn. Pending termination enters the owner policy.
This explicitly retained timer bounds idle signal observation without retaining
the generic task polling cap. Tests cover idle SIGINT/SIGTERM/SIGUSR1 (where
supported) and delivery inside a broker operation. A future wakeup-fd bridge may
replace this timer only with portable ownership and handler-restoration proof;
it is not required or implicitly promised here. Centralizing polling does not
remove necessary OS-source timers. Include this timer in idle CPU measurements.

The factory's supplied queue handles remain borrowed for the entire strategy
lifetime. An adapter that changes that set constructs/arms a candidate scope
before retiring the old strategy and its leases, then performs the mandatory
durable recheck. Task wait membership may replace native registrations while
its construction-owned data-version seed queue stays alive. Do not close the
queue captured by a retained version provider.

Add a small owner-confined read-only interest overlay, separate from `_queues`.
Its union with eligible dispatch queues supplies native wait membership.
Registration/removal occurs between turns; duplicate probe interests share an
owned lease/refcount, and failure during replacement keeps old ownership valid.
Install candidate waiter, then close displaced waiter through the existing
public owner-confined strategy seam. Close removed observation queue handles
only after they are no longer in the installed waiter. Task stop retires all
interests. No new QueueMode, TaskSpec field or public live add_queue permission.

Do not implement reply observation with current MultiQueueWatcher PEEK mode:
its peek_one/has_pending behavior can dispatch a retained row forever. A reply
interest wakes the owner and participates in the separate matching-readiness gate. The probe scans/matches its keyed reply, and
ordinary queue pending checks exclude the observation overlay. Unmatched rows
remain visible without becoming perpetual useful activity.

`QueueChangeMonitor` becomes a caller-owner adapter using the new initialized
strategy factory. Its API remains the command integration seam; native listener
thread ownership stays in SimpleBroker, while its own native monitor thread and
per-queue SQLite watcher threads disappear. No new durable queue or daemon is
introduced. Observer callers still own predicates, queue consumption, cursors
and grace periods; the monitor supplies change/deadline/cancel readiness only.
It must not return LOCAL_WAKE as proof of queue progress.

Membership discovery, authoritative first scan, wait, final scan, cancellation,
replacement and cleanup must remain on the same owner thread. A foreign close
requests stop/wake; actual teardown occurs after that owner's active wait or
iterator advance unwinds. The existing async generator cleanup future remains
owned until finished.

## Implementation Slices and Gates

These are five implementation phases, each divided into reviewable changes.
Do not declare centralization finished after phase 2 while caller paths still
poll independently. Phase dependencies are sequential; audits, pure test design
and static review may run in parallel. Performance runs are always isolated and
sequential, without concurrent test suites or reviewers executing code.

### 1. Freeze the baseline, retire the experiment, and deliver broker support

- Record current SHAs, dirty diff inventory and representative benchmark before
  changing code. Retire only the interval experiment as listed above, preserving
  submission optimizations. Reproduce old manual-path bypass with a failing
  real-task test; do not merely assert a constant or mocked call count.
- In SimpleBroker, promote the reviewed [SB-API-6] API delta: the signatures,
  reason semantics, factory/provider custody, local wake, finite timeout and
  demotion contracts above. Keep delivery/peek contracts unchanged.
- Implement timeout/reason/provider factory first, then wake capability and PG
  condition integration as separately reviewed commits. Required source files:
  `simplebroker/watcher.py`, `simplebroker/ext.py`, `_backend_plugins.py`, bundled
  PG `runner.py`, and relevant package exports/tests. Run upstream context gates.
- Prove finite/zero deadlines, data-version initial race/core replacement,
  native failure, no lost wake and thread-affine close on both real backends.
  Measure factory ownership and connection count with surviving sibling leases.
- Release/use the supported compatible SimpleBroker version before Weft relies
  on it. Updating existing dependency constraints/lockfiles is an explicit
  reviewed release step, not a new dependency or an untracked local patch.

**Stop/re-evaluate:** unsupported custom strategy silently ignores timeout;
provider logic is copied into Weft; wake requires foreign-thread broker access;
PG reconnect is claimed without generation-safe registry work; or dependency
release is unavailable. Do not hide these behind a Weft fallback implementation.

### 2. Unify task waiting and local wake inputs

- Promote [CC-2.1], relevant [CC-2.2.1]/[CC-2.5], [SB-0.4] and [IMPL.10] paragraphs
  before code; mapping notes describe only implemented ownership.
- Change `weft/core/tasks/multiqueue_watcher.py` and `base.py` to use their retained
  strategy. Wire worker publication, stop/parent loss and pending topology work
  to the latched interrupt. Retain existing event-clear/queue-recheck race guards.
- Update production driver/launch cadence (A15) in `base.py`, `launcher.py`,
  `manager_runtime.py` and `manager_process.py` together, including argument
  parsing/serialization and startup tests. No implicit .05 application deadline remains. Retain the named signal-latch
  check timer (A16), which does no broker scan/full turn when the latch is empty.
  Test deliberately revised explicit driver limits and both manager launch paths.
- Remove Manager-specific clamp and active-worker/parent-loss response caps only
  after the corresponding event source is proven. Parent-loss watchdog cadence
  itself remains the OS fallback. Adapt `Consumer.run_work_item` to shared waits.
- Preserve zero-timer no-query behavior, native hint discovery, bounded control
  and worker draining, reserved queue suppression and admission policy.
- Run existing Manager/Consumer/ServiceTask/heartbeat/monitor/pipeline suites on
  SQLite/PG, adding actual idle-write and broker-free worker completion tests.

**Stop/re-evaluate:** a retained row causes spin; a local result requires a broker
write to wake; an idle timer can be delayed by sustained traffic; control latency
regresses; two owner threads or strategies appear for one task.

### 3. Complete task-owned interests and eliminate blocking live-turn waits

- Promote Manager/[MF-3] probe contract; add read-only overlay, reply deadlines,
  keyed matching and all cleanup exits. Convert any synchronous probe reached
  on a task turn to incremental owner state; synchronous external adapter stays
  synchronous over the shared mechanism.
- Add local stream-output, EOF and process-exit wake from command session workers.
  Convert live interactive drain/STOP into explicit phases and due deadlines.
  Preserve bounded output batches, ACK semantics and trailing-output grace.
- Convert Manager inbox-seed retries into state/deadline scheduling. Model
  terminal publication retries in the bounded terminal mode without recursively
  driving public task lifecycle methods. Keep fatal errors versus best-effort
  cleanup behavior explicit.
- Keep monitor/heartbeat/liveness and OS reaping schedules as due work, removing
  only response caps. Do not subscribe TaskMonitor blindly to retained log rows.
- Inspect and test every A04-A14 exit and source. Scope is all task families,
  not a Manager-only special path.

**Stop/re-evaluate:** observation membership changes dispatch rights; PONG row
ownership changes; unknown process visibility becomes absence; seed-before-launch
or terminal-output ordering cannot be preserved; retry work steals control turns.

### 4. Consolidate caller observers and adapters, then retire masked polling

- Promote [SB-0.4], CLI and [PY-2] observer contracts. Replace QueueChangeMonitor's
  machinery behind its existing seam. Keep caller caps during this initial
  substitution until native demotion, cancellation and complete interests pass.
- Migrate keyed external PING, heartbeat startup, task terminal snapshots and
  task-monitor summary follow. Use initial/post-arm/final predicate scans;
  timeout and failed probes retain their current outcome types.
- For each B02-B12 observer, list every authoritative queue/store/process surface
  it reads. Register all known queue interests, plus a discovery deadline while
  routing is unknown. Dynamic replacement requires authoritative recheck.
- Translate completion, quiet, batch-boundary, STOP/KILL grace, startup, idle,
  route discovery and user interval values into named deadlines. Keep external
  Monitor-store/PID reconciliation timers where no reliable event source exists.
  Only then remove redundant fixed rescan caps. Keep historical/log cursors and
  one-shot/persistent result consumption unchanged.
- InteractiveTaskClient retains its actual consuming owner thread and local
  response Conditions. Connect that owner's event source to lifecycle evidence.
  Async/Django cancellation uses the same wake but never transfers iterator
  advancement or cleanup ownership to the async thread.
- For microsandbox and other runner callback cancellation, wire known producers
  to an awaitable/local signal; keep an explicit bounded compatibility timer for
  third-party callback-only interfaces. Docker/OS sampling remains the C-table
  source-adapter boundary unless a separate concrete native source is implemented.

**Stop/re-evaluate:** removing a cap causes silent wait after listener failure;
unknown route can publish without wake or discovery; close destroys another
thread's active iterator; any public interval/timeout/output semantics change.

### 5. Prove end-to-end behavior, resource cost and architectural coverage

- Run the acceptance matrix below and independently review the entire migrated
  graph, including upstream and Weft ownership. Re-run runtime gates from final
  state on both backends; do not rely on the fixed-interval experiment's tests.
- Add a narrow architecture guard that inventories production broker-read/sleep
  loops and owner bypasses. Known external/terminal exceptions are named by
  function with rationale, not a blanket ban on sleep or a module-wide allowlist.
  The guard supports, but cannot replace, real event-path tests.
- Benchmark quiet/burst/idle-to-active work, local completions, PONG and fanout;
  verify no linear per-queue thread/connection growth in SQLite observers.
  Resolve CPU or latency failures in the shared mechanism, not by restoring
  per-task/per-caller interval knobs.
- Reconcile spec mapping/backlinks and test inventory; record upstream minimum
  version and supported degraded behavior. Retain pending implementation plans
  until evidence supports closure; commit/push only by explicit instruction.

**Stop/re-evaluate:** a claimed path lacks a firing integration test; idle cost is
above target without explanation; more than one independent queue-wait policy
survives; or native failure tests depend on command-side sleep backstops.

## Acceptance Matrix

| Contract | Required proof (keep broker/process real) |
| --- | --- |
| One strategy on actual production paths | Drive real Task/Manager and caller observer; show shared strategy used and no private sleep fallback. Cover foreground, detached, direct Consumer and standalone watcher drivers. |
| Initial/subscription races | PONG/work published before first wait, between empty read and arm, during replacement, and on first SQLite version baseline is found without unrelated traffic. |
| Local signal races | Worker progress/completion/EOF arrives with no broker write; signal before wait, during wait, during event clear and at final worker exit. Stop similarly interrupts PG/SQLite waits. |
| Deadlines and reasons | Finite timeout spans repeated native waits; zero/expired timeout makes no broker query; timer/local wake does not become broad discovery or reset burst; repeated traffic cannot defer timer. |
| Useful work versus noise | Unrelated SQLite writes, stale/unmatched PONG, retained reserved work, blocked admission and stalled control do not busy-loop or starve control. |
| Observation-only readiness | Matching PONG with all dispatch queues empty advances on native PG, demoted PG and SQLite; unmatched retained replies do not spin. |
| Signal deferral | Idle OS signals wake termination policy within the named check bound with no broker traffic; signal frames acquire no locks and perform no broker calls. Empty checks do not cause full turns. |
| Pending probe lifecycle | Leadership/service success, expiry, I/O error, owner-row replacement, stop and late reply; exact cleanup, uncertain evidence, no deletion of other request IDs. |
| Native failure | Lose/fail PG waiter during idle wait; owner demotes once and continues authoritative checks. No indefinite observer hang and no double close/release. Automatic reconnect is not assumed. |
| Dynamic interests | Route changes and duplicate probe memberships preserve old ownership until successful replacement, then close displaced resources once. Observation never consumes/reserves. Race local wake with replacement while a real PONG is pending on the old registration; the post-replacement scan must find it. |
| Interactive execution | Output, EOF and process exit independently wake; continuous output cannot starve PING/STOP; final trailing data and envelopes remain ordered under timeout/cancel. |
| Retry/terminal phases | Inject seed-write failure and terminal-write failure while controls arrive; bounded attempts/deadlines and seed-before-launch/terminal proof remain correct. |
| All timer-driven families | Heartbeat, TaskMonitor, LivenessMonitor, Manager admission/idle/autostart and pipeline work progress with no incidental database traffic. |
| Caller evidence completeness | Task state/output/control arrives without task-log wake; result materialization, realtime, terminal snapshot, spawn reserved state and startup/stop settle correctly. |
| Close/cancellation | Observer close, async disconnect, iterator advance and cleanup races preserve one owner, interrupt promptly, retain outstanding cleanup and release all physical resources. |
| API parity | No public CLI/client/Django timeout, interval, return/error, cursor, claimed-row, consume/peek/move or durable-acceptance contract changes. Include short-timeout materialization-to-result phases and preserve their total budget, not a fresh timeout per phase. |
| Backends/resources | SQLite and PG, non-default context/config, surviving sibling handle, connection replacement, failed acquire/close, stop during wait/replacement; no transaction across wait. |

Use deterministic barriers/events and injected clock boundaries for ordering;
mock only those boundaries, not queue delivery or native notification behavior.
First prove new regression tests fail on the baseline. Optional OS/SDK services
may use existing SDK mocks but need explicit source-adapter contract tests;
claims about real Docker/microsandbox latency require their real integration gate.

## Verification Commands and Evidence

For implementation, source `.envrc` and use repository tools. Initial test owners:

- SimpleBroker: `tests/test_watcher.py` (TestPollingStrategy),
  `tests/test_watcher_burst_mode.py`,
  `tests/test_python_library_api_contract_sb_api.py`, and
  `extensions/simplebroker_pg/tests/test_pg_activity_waiter_lifecycle.py`.
  Run according to upstream's own environment/backend instructions.
- Weft: `tests/core/test_manager.py`, `tests/core/test_control_probe.py`,
  `tests/core/test_queue_wait.py`, `tests/core/test_manager_runtime_connections.py`,
  `tests/tasks/test_multiqueue_watcher.py`, `tests/tasks/test_task_execution.py`,
  control/service/heartbeat/liveness/interactive/pipeline tests, command result/
  realtime/status/control/submission/monitor suites, all observation-connection
  suites, client and Django integration tests. Add tests to the owning modules;
  any new architecture-inventory test path is explicitly a planned addition.

Commands for Weft final implementation gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest
bin/pytest-pg --fast
./.venv/bin/python -m pytest -m slow tests/core/test_manager.py tests/tasks/test_task_execution.py tests/tasks/test_control_channel.py tests/tasks/test_task_interactive.py tests/tasks/test_pipeline_runtime.py
./.venv/bin/ruff check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
git diff --check
```

Apply formatter only to edited Python files. Run the equivalent broker-related
slow acceptance cases with PostgreSQL using its supported wrapper. Existing
spec hygiene and plan metadata are this checkout's traceability gates; no
standalone backstitch configuration was found. This planning task itself runs
document gates and link/file checks, not the full runtime implementation gates.

Performance protocol: run sequentially on the same recorded host/toolchain with
real broker and detached manager, after warmup, at least three quiet windows
of 30 seconds per configuration. Measure manager CPU, worker CPU and PG server
CPU separately as percent of one core; report combined context cost as well.
Disable per-wakeup trace I/O for CPU runs. Separately record cheap version checks,
SQL pending/evidence scans, full reactor turns, native notifications, wake reasons,
threads and physical connections. Compare one versus many observed queues and
stable services, empty versus retained-history state, and burst-to-quiet return.
Use event timestamps for committed-message-to-handler and PONG observation; no
forced periodic database traffic to manufacture wakeups. Run no parallel tests.

Local acceptance target is idle Manager below 2.5% of one core on SQLite and PG,
with server cost reported separately; native PG should not acquire fast idle SQL
polling. Aim for idle queued-message-to-handler p95 near/below 25 ms on the benchmark
host and materially improved PONG round trip, without introducing a hard portable
real-time promise. Correctness/race tests are deterministic; performance targets
are recorded benchmark gates, not brittle wall-clock assertions in ordinary CI.
A target miss blocks claiming the performance goal, not permission to weaken the
shared-source invariant. Report cold startup and degraded-listener behavior
separately from healthy steady state.

Earlier fixed-interval measurements are diagnostic context only: SQLite 25 ms
full-turn polling cost 3.8%, 40 ms 2.13-2.33%, 50 ms 1.80-2.13%; PG native idle roughly
0.4-0.53% manager plus0.29-0.45% server. They do not estimate the cost of shared
cheap data-version checks and must not be used to set the new core strategy.

## Rollout, Rollback, and Non-goals

Roll out additive broker API and built-in native wake support first, then Weft
capability use. Existing broker consumers retain the old wait_for_activity None
return adapter. Pin a supported dependency floor and restart task processes to
adopt the new owner behavior. No queue/schema migration or new service exists;
old and new task processes can share the broker without payload changes.

Rollback is the coordinated code/dependency slice, preserving queue data,
accepted tasks, exact reply ownership, submission optimizations and evidence.
Do not downgrade the broker below required APIs while upgraded Weft remains
installed. An owner never switches backends/strategies mid-wait to roll back.
Resource lifecycle changes need full stop/unwind before restart.

Non-goals: new asyncio task runtime; global daemon/event bus; SQL hidden in Weft;
new persistent wake queues; TaskSpec/public API redesign; collapsing SDK sampling
into broker polling; rewriting all clocks/history reducers; automatic PG listener
reconnect without generation-safe design; blanket deletion of sleeps; shipping
the earlier fixed-interval experiment as the final architecture.

## Independent Review, Self-review, and Deviations

Audit slices were independently read by agents for task/runtime paths, caller/
adapters and SimpleBroker/PG capability boundaries. They made no code edits.
Plan self-review findings and dispositions:

- A timeout-only strategy extension would leave worker wakes ineffective under
  PG Condition.wait. Added sticky local wake capability and race gates.
- Ordinary PEEK mode would spin on retained replies. Added read-only interest
  overlay, distinct from fixed dispatch topology and pending checks.
- Removing caller caps before repairing QueueChangeMonitor failure handling
  would hang observers. Added demotion-first gate and complete-interest audit.
- PG waiter replacement can reacquire a dead shared listener. Chose truthful
  owner-lifetime polling demotion; deferred automatic reconnect explicitly.
- Existing public wait return is None. Preserve it; add one reason-bearing
  wait_once implementation, plus compatibility adapter, not duplicate algorithms.
- Local cancellation callbacks/raw Events are not universally interruptible.
  Require producer wiring or declared central compatibility timers.
- Broad external-source conversion would create unsupported OS/SDK promises.
  Keep audited bounded exceptions and ownership tests, not generic abstractions.

Independent plan review uses a different agent family when available (Claude CLI
is installed). Reviewer must read this plan's exact API/spec deltas and target
sources, challenge completeness/overengineering/lost wakes, and return PASS or
BLOCKED with reasons. Resolve each finding before reporting the plan review-ready.
Implementation still requires its own slice reviews and final review.

### Deviation log

| Boundary | Earlier approach | Revised approach | Reason |
| --- | --- | --- | --- |
| Manager/PONG latency | Independent fixed intervals | One strategy with event/deadline composition | User identified divergence from shared reactor design; audit confirmed it. |
| Native recovery | Could recreate waiter | Explicit bounded demotion for owner lifetime | Current PG registry can retain failed listener; naive replacement is not recovery. |
| All polling | Could mean deleting all sleeps | Central queue policy plus explicit external/terminal source adapters | Process/SDK/backpressure evidence is not represented by broker events. |

Review results and planning-task verification will be recorded below.

### Scoped review dispositions

- Adapter auditor re-review: PASS. Added short-timeout materialization/result
  budget proof; raw external Event compatibility is explicit in the shared API
  and applies to caller observers as well as runners.
- Broker auditor re-review: accepted all four precision fixes: native local
  interrupt cannot masquerade as broker activity; wake/replace/close lifetime
  protection; empty interests suspend broker polling; no adapter thread does
  not prohibit the existing PG listener thread. Added combined local-wake,
  replacement and pending-PONG race test.

- Additional author pass found the bootstrap TASK_PROCESS_POLL_INTERVAL would
  retain an implicit 50 ms task deadline after strategy routing. Added A15 and
  coordinated driver/transport change, plus explicit None/no-deadline semantics.
  Also clarified POLL_DUE does not require a full policy turn, provider seed
  queue lifetime, and demotion persistence across interest replacement.

- Whole-plan independent review (Claude, read-only): PASS, no blockers. Integrated
  its refinements: preserve standalone periodic POLL_DUE, keep wake separate from
  useful-activity hints, reuse compatibility_key validation, and clarify units.
- Task-wait reviewer raised observation-only readiness, signal-handler deferral
  and explicit interval semantics. Added separate keyed-interest reconciliation,
  A16 central signal timer, both manager launch producers, and deliberate minimum
  bound semantics with regression coverage. Scoped re-review: PASS; all three
  findings resolved, no remaining blocker.

### Planning verification

- Audited 39 production wait families (A01-A16, B01-B14, C01-C09).
- Plan metadata and spec hygiene tests: 6 passed. Relative plan link targets and
  git diff whitespace checks pass. No runtime implementation was made for this
  planning task; preceding runtime changes remain uncommitted and untouched.
- Plan remains a draft proposal pending implementation authorization; independent
  plan review is complete, not a claim that the reactor migration is implemented.
