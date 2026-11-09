# Manager Architecture: The Recursive Model

This document describes how Weft realizes the “managers are tasks” concept. A
Manager is implemented as a long-running `ServiceTask` over `BaseTask` that
consumes spawn requests from SimpleBroker queues, launches child tasks, and
reports lifecycle events. The CLI component that submits work is referred to as
the **Client**.

**Terminology**
- **Task**: Any executable unit described by a TaskSpec.
- **Consumer**: The default task executor for command/function targets.
- **Manager**: A long-lived task that spawns child tasks and manages autostart.
- **Manager (CLI)**: The CLI namespace for managing Managers (not a distinct runtime).

The Client (`weft/cli/run.py`) builds TaskSpec templates,
enqueues them on `weft.spawn.requests`, and optionally waits for completion by
tailing task logs/outboxes. The spawn-request message ID becomes the task TID.
Managers consume those requests, expand the TaskSpec, launch child Consumers
through the manager's broker-free child-launch worker lane, and emit
`weft.log.tasks` events just like any other task.
Once that spawn request is written, submission is durable user intent; later
CLI failure handling must reconcile by TID instead of assuming the enqueue can
always be rolled back from the public request queue.

## Table of Contents

- [Related Plans](#related-plans)
- [Conceptual Model: Everything is a Task [MA-0]](#conceptual-model-everything-is-a-task-ma-0)
- [Manager Behaviour [MA-1]](#manager-behaviour-ma-1)
- [TID Correlation [MA-2]](#tid-correlation-ma-2)
- [Bootstrap and Lifecycle [MA-3]](#bootstrap-and-lifecycle-ma-3)
- [Scope Boundary [MA-4]](#scope-boundary-ma-4)

## Related Plans

- [Manager Checkpoint Volume](../plans/2026-11-08-manager-checkpoint-volume-plan.md) publishes undurable process receipts before a turn's ordinary dispatch and the rest of the turn's checkpoint changes once when it ends; the [MA-1.8] dispatch barrier is unchanged ([MA-1.8], [MF-5]).

- [Operator consumption ends pending launch custody](../plans/2026-11-05-operator-consumption-of-launch-custody-plan.md): ordinary pending visibility, explicit operator read/delete, and no-receipt hold retirement.

- [Manager Published Dispatch Refusals](../plans/2026-11-05-manager-published-refusals-plan.md) publishes scoped standing dispatch observations in complete Manager task-state snapshots and exposes them through ordinary status ([MA-1.8], [MF-5], [OBS.6], [CLI-1.2.1], [CLI-1.3]).

- [Scoped Dispatch Around Ambiguous Launches](../plans/2026-11-05-scoped-ambiguous-launch-dispatch-plan.md) holds retained no-receipt launches and their dependent ancestry closure while unrelated work remains eligible; required checkpoint durability barriers remain ([MA-1.8], [MF-5], [MF-6], [OBS.6]).
- [Idempotent Wrapper-Lost Settlement](../plans/2026-11-05-wrapper-lost-settlement-idempotence-plan.md) keeps one committed supervisor terminal envelope and the real local Process exit code under either cleanup/retirement ordering ([MA-1.6a]).

- [Program Theory Revision: Published Refusals, Wake Predicates, Observable Quiet, and Behaviour Probes](../plans/2026-11-05-program-theory-behaviour-and-refusal-revision-plan.md) moves frontier and bucket compaction mechanics from program theory [ALT-WT-016] into [MA-1.8]; no behaviour change.
- [Manager Wake, Idle Clock, and Launch Settlement Repairs](../plans/2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md) publishes the idle deadlines only while the idle clock runs, counts a source row above the classification watermark as pending reactor work, binds a successor's launch settlement to the `wrapper_lost` rule, skips retained rows of another runtime identity at bootstrap, and titles internal services, under [MA-1] and [MA-1.8].
- [Context-Level Service Singletons: Managers Ensure, Services Own Their Lifetime](../plans/2026-11-04-context-level-service-singletons-plan.md) makes internal services context-level singletons: a manager ensures them and never stops them, the STOP drain has no services phase, and `weft system stop` empties a context ([MA-1] item 7, [MA-3]).
- retired: `2026-11-04-system-stop-snapshot-targets-plan` (source 0ff8cfbf; see `docs/plans/README.md`) makes phase 1 of a system STOP find every live user-work task through its own snapshot, whichever Manager launched it and whether that Manager is alive, and sends the first STOP rung as one exact-name broadcast under [MA-3].
- retired: `2026-11-04-admission-evidence-read-failure-plan` (source 0ff8cfbf; see `docs/plans/README.md`) removes the Manager's admission-evidence mode: a failed read of a task's state snapshot during reconciliation or reap is logged and changes nothing under [MA-1.8]; a failed post-launch checkpoint publication keeps gating dispatch through the undurable set, and a successor's failed first publication keeps gating it through the incomplete bootstrap.
- retired: `2026-11-02-task-monitor-heartbeat-clock-plan` (source 0ff8cfbf; see `docs/plans/README.md`) removes the TaskMonitor heartbeat registration; its cycle deadline is its only clock ([MA-1] item 7). Heartbeat registrations become rows on `weft.state.heartbeats`, each with its own lease; the heartbeat runs as a standing internal service of the canonical Manager and is the only reader and deleter of that queue, and the Manager neither reads nor deletes it ([MA-1] item 7).
- [System STOP ordering](../plans/2026-11-02-system-stop-ordering-plan.md) ordered a Manager STOP drain as user work, then internal services, then the Manager, under [MA-3]; the services phase is since removed (internal services are context-level singletons, see the first entry above), and its user-work phase and snapshot targets stand.

- retired: `2026-11-03-family-scheduler-frontier-ring-plan` (source 0ff8cfbf; see `docs/plans/README.md`) replaces the quadratic frontier rebuild with a cursor ring over childless residents and contracted scheduling-parent pointers, and states the branch rotation order under [MA-1.8].

- retired: `2026-10-27-runtime-and-test-repair-plan` (source 0ff8cfbf; see `docs/plans/README.md`) describes command-lifecycle ownership, pending-index reclamation, and test-sensitivity repair design.

- retired: `2026-10-22-bounded-concurrent-manager-launches` (source 0ff8cfbf; see `docs/plans/README.md`) compares duplicate-sweep removal and serial dispatch batching before any concurrent-worker revision.

- retired: `2026-10-20-bounded-startup-fast-path` (source 0ff8cfbf; see `docs/plans/README.md`) governs the bounded low-usage startup window under [MA-1.8].

- [Work-conserving family scheduling](../plans/2026-10-15-work-conserving-family-scheduling.md) removes the frontier cap and enables native PostgreSQL measured admission by default.

- [Measured admission and bounded family scheduling](../plans/2026-10-14-measured-admission-family-scheduling.md) governs the measured-admission and bounded-family implementation.

- retired: `2026-10-13-supervisor-terminal-projection` (source 0ff8cfbf; see `docs/plans/README.md`) implements Monitor projection of retained supervisor evidence under [MA-1.6a] and [MF-5].
- [Connection-aware admission and child progress](../plans/2026-10-13-connection-aware-child-admission-plan.md) is superseded by measured admission under [MA-1.8].

- retired: `2026-10-12-burst-submission-complexity-plan` (source 0ff8cfbf; see `docs/plans/README.md`) - implements local completion proof, incremental sentinel membership, and exact pending service-request references.

- [AdmissionController: Progress-Aware Admission Waiting](../plans/2026-10-06-admission-controller-plan.md) - isolates admission policy and tracks bounded PG resource-drain diagnostics.

- retired: `2026-09-18-event-routed-manager-pong-plan` (source 0ff8cfbf; see `docs/plans/README.md`) - routes Manager and service probe replies through the Manager's existing control watcher and removes probe-specific polling deadlines.

- retired: `2026-09-17-watcher-reactor-restoration-plan` (source 0ff8cfbf; see `docs/plans/README.md`) - restores the Manager to the shared watcher reactor, publishes the remaining service clocks as deadlines, and routes child exit through one local sentinel adapter.

- retired: 2026-09-17-manager-discovery-and-durable-submission-plan — source 396c3b85; see docs/plans/README.md

- retired: 2026-09-11-per-tid-task-state-namespace-plan — source 396c3b85; see docs/plans/README.md

- retired: `2026-08-29-liveness-reaper-and-custody-split-plan` (source 396c3b85; see `docs/plans/README.md`) - adds LivenessMonitor as a third built-in persistent service and moves tid-mapping cleanup custody to it.
- [Manager Admission Control Plan](../plans/2026-08-25-manager-admission-control-plan.md) - adds a lane-aware pre-reservation admission gate over one backend-specific observed usage value.
- retired: `2026-08-10-canonical-contract-and-dead-code-cleanup-plan` (source 396c3b85; see `docs/plans/README.md`) - establish one current service-owner schema, bounded bootstrap discard, and one mutable managed-service state owner.
- retired: `2026-05-31-task-monitor-orphan-log-and-status-reconciliation-plan` (source 396c3b85; see `docs/plans/README.md`) - plan stale internal-service status reconciliation and bounded TaskMonitor recovery for pre-checkpoint raw task-log rows that missed Monitor-store collation.
- retired: `2026-05-26-service-task-worker-api-plan` (source 396c3b85; see `docs/plans/README.md`) - consolidate Manager child launch and TaskMonitor maintenance lanes onto the shared internal `ServiceTask` service-worker API.
- retired: `2026-05-28-stale-service-owner-runtime-cleanup-plan` (source 396c3b85; see `docs/plans/README.md`) - plan stale manager/service Monitor disposition, task-local control cleanup, and internal probe residue cleanup without weakening active manager protection.
- retired: `2026-04-14-config-precedence-and-parsing-alignment-plan` (source 396c3b85; see `docs/plans/README.md`) – align broker target precedence, Weft env parsing, and manager-timeout config semantics with the documented contract.
- retired: `2026-04-14-spawn-request-reconciliation-plan` (source 396c3b85; see `docs/plans/README.md`) – tighten queue-first submission error handling so post-enqueue failures reconcile by TID instead of assuming rollback, and keep late bootstrap acknowledgement failures from downgrading successful startup.
- retired: `2026-04-09-manager-bootstrap-unification-plan` (source 396c3b85; see `docs/plans/README.md`) – Collapse `weft manager start` onto the canonical manager bootstrap path used by `weft run`.
- retired: `2026-04-09-manager-lifecycle-command-consolidation-plan` (source 396c3b85; see `docs/plans/README.md`) – Collapse remaining `manager list|status|stop` and `weft status` manager lifecycle reads onto the same shared control-plane helper as bootstrap.
- retired: `2026-04-09-weft-serve-supervised-manager-plan` (source 396c3b85; see `docs/plans/README.md`) – Add a minimal foreground `weft manager serve` command for supervisor-managed persistent managers and align manager TERM/INT with graceful drain.
- retired: `2026-04-13-detached-manager-bootstrap-hardening-plan` (source 396c3b85; see `docs/plans/README.md`) – Replace parent-dependent detached bootstrap with a real detached-launch wrapper, stronger startup proof, and actionable early-failure diagnostics.
- retired: `2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan` (source 396c3b85; see `docs/plans/README.md`) – Replace the fixed startup delay with event-based readiness proof and split cleanup-vs-startup stress coverage.
- retired: `2026-04-16-autostart-hardening-and-contract-alignment-plan` (source 396c3b85; see `docs/plans/README.md`) – Make project-level autostart intent durable, fix manager-side enqueue bookkeeping, and align manifest policy docs with enforced behavior.
- retired: `2026-04-16-pipeline-autostart-extension-plan` (source 396c3b85; see `docs/plans/README.md`) – Extend autostart manifests so stored pipeline targets compile and launch through the ordinary first-class pipeline runtime.
- retired: `2026-04-17-canonical-owner-fence-plan` (source 396c3b85; see `docs/plans/README.md`) – Add a shared canonical-owner reduction helper and harden manager child dispatch with a final ownership fence before launch.
- retired: `2026-04-17-heartbeat-service-plan` (source 396c3b85; see `docs/plans/README.md`) – Add the built-in runtime heartbeat service, reserve its internal endpoint namespace, and reuse the canonical-owner fence in a long-lived interval emitter.
- retired: `2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan` (source 396c3b85; see `docs/plans/README.md`) – Keep task-list/status read models aligned with selected active-manager registry truth.
- retired: `2026-05-07-manager-selection-ping-pong-liveness-plan` (source 396c3b85; see `docs/plans/README.md`) – Use keyed manager PONGs as bounded positive liveness evidence when startup or selection encounters stale-looking manager registry rows.
- retired: `2026-04-06-persistent-agent-runtime-implementation-plan` (source 396c3b85; see `docs/plans/README.md`) – references Manager Architecture for long-lived agent sessions.
- retired: `2026-04-06-taskspec-clean-design-plan` (source 396c3b85; see `docs/plans/README.md`) – references Manager Architecture for TaskSpec schema alignment.
- retired: `2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan` (source 396c3b85; see `docs/plans/README.md`) – proposes manager-supervised `TaskMonitor` lifecycle and safe autonomous cleanup slices.
- retired: `2026-05-08-manager-owned-internal-service-supervision-plan` (source 396c3b85; see `docs/plans/README.md`) – unify manager-owned `once`/`ensure` reconciliation for heartbeat, TaskMonitor, and autostarts.
- retired: `2026-05-08-phase-7-manager-service-reconciler-cleanup-plan` (source 396c3b85; see `docs/plans/README.md`) – closes the single-reconciler implementation gaps for heartbeat, TaskMonitor, and autostart lifecycle handling.
- retired: `2026-05-18-reactive-task-loop-hot-probe-plan` (source 396c3b85; see `docs/plans/README.md`) – restore the manager/task reactive wait contract so PG-backed idle managers wake from NOTIFY or bounded timers rather than empty queue probes.
- retired: `2026-05-08-deterministic-manager-service-reconciler-plan` (source 396c3b85; see `docs/plans/README.md`) – supersedes the cleanup plan for the pure reducer, transition-table, and full test support work.
- retired: `2026-05-09-manager-stop-timeout-hardening-plan` (source 396c3b85; see `docs/plans/README.md`) – separates the manager's internal child-drain timeout from caller-side stop confirmation defaults so slower backends have observation margin.
- retired: `2026-05-09-internal-spawn-priority-queue-plan` (source 396c3b85; see `docs/plans/README.md`) – adds `weft.spawn.internal` as strict-priority manager-owned service spawn work while preserving the shared manager launch path.
- retired: `2026-05-09-runtime-liveness-probe-registry-plan` (source 396c3b85; see `docs/plans/README.md`) – runtime-specific manager liveness checks behind a lightweight core registry with Docker-specific probing owned by `weft_docker`.
- retired: `2026-05-09-prune-path-unification-plan` (source 396c3b85; see `docs/plans/README.md`) – CLI pruning and manager-supervised TaskMonitor cleanup share one core prune engine.
- retired: `2026-05-09-service-liveness-and-health-convergence-plan` (source 396c3b85; see `docs/plans/README.md`) – manager ownership, heartbeat supersession, singleton restart timing, and status liveness diagnostics share one interpretation path.
- retired: `2026-05-10-manager-service-authority-boundary-hardening-plan` (source 396c3b85; see `docs/plans/README.md`) – tighten manager-owned singleton evidence authority, duplicate PID force-kill authority, and service-key helper structure.
- retired: `2026-05-11-manager-work-stealing-dispatch-plan` (source 396c3b85; see `docs/plans/README.md`) – atomic spawn-request reservation is the public dispatch authority while singleton service correctness stays in the manager-owned reducer.
- retired: `2026-05-11-manager-serve-operational-log-plan` (source 396c3b85; see `docs/plans/README.md`) – level-controlled foreground `manager serve` operational logs expose registry, loop, service-convergence, spawn, and TaskMonitor decisions without writing lifecycle state.
- retired: `2026-05-13-manager-replace-start-serve-plan` (source 396c3b85; see `docs/plans/README.md`) – explicit `weft manager start --replace` and `weft manager serve --replace` operator replacement semantics use the existing STOP control path.
- retired: `2026-05-13-manager-liveness-and-leadership-robustness-plan` (source 396c3b85; see `docs/plans/README.md`) – strong live dispatch-authority proof is required before leadership yield, external-supervisor `unknown` evidence cannot act as authority, and leadership-drain recovery remains bounded without broad status scans.
- retired: `2026-05-14-manager-list-diagnostics-plan` (source 396c3b85; see `docs/plans/README.md`) – improves cross-namespace manager liveness fallback through keyed PING/PONG and adds an explicit operator diagnostic list mode for manager registry liveness, proof source, and canonical-owner visibility without changing election behavior.
- retired: `2026-05-15-manager-hot-loop-reduction-plan` (source 396c3b85; see `docs/plans/README.md`) – identified manager supervisory hot loops before the broader reactor migration.
- retired: `2026-05-15-task-reactor-and-evidence-worker-plan` (source 396c3b85; see `docs/plans/README.md`) – phased plan that moved blocking task work behind broker-free worker lanes while keeping queue reservation, acknowledgement, lifecycle state, and control responses on the task reactor.
- retired: `2026-05-15-manager-reactor-hot-loop-follow-up-plan` (source 396c3b85; see `docs/plans/README.md`) – follow-up that removed remaining manager proof churn after the reactor migration by caching process-stable context, reordering leadership proof, gating idle probes, and bounding service pending evidence.
- retired: `2026-05-13-internal-state-machine-helper-plan` (source 396c3b85; see `docs/plans/README.md`) – reusable pure reducer helper that can express manager-service and other transition tables without moving side effects out of their current owners.
- retired: `2026-05-11-internal-service-observability-plan` (source 396c3b85; see `docs/plans/README.md`) – adds an ops read model that reports heartbeat and TaskMonitor state from manager launch evidence, child task logs, TID mappings, and internal spawn queues.
- retired: `2026-05-20-service-task-shared-reactor-extraction-plan` (source 396c3b85; see `docs/plans/README.md`) – extracts shared long-lived service helpers for Manager, TaskMonitor, and Heartbeat while keeping Manager scheduling and dispatch authority local.
- retired: `2026-05-29-reliability-and-doc-fixes-plan` (source 396c3b85; see `docs/plans/README.md`) – create-time-gated shutdown reap and a shared PONG dispatch-eligibility gate (Slices 3-4).

## Conceptual Model: Everything is a Task [MA-0]

- **No special cases** – Managers inherit from `ServiceTask`, which inherits
  from `BaseTask`, so they use the same queue wiring, process-title formatting,
  and control semantics as regular tasks.
- **Uniform observability** – Managers publish manager service-owner rows in
  `weft.state.services`, participate in the task log, and respond on the
  control channel (`STOP`, `PING`, `STATUS`).
- **Self-hosting** – Managers spawn child Consumers by writing back into the
  same SimpleBroker database they are monitoring.

_Implementation mapping_:
- `weft/core/manager.py` — `Manager(ServiceTask)` inherits long-lived service activation, queue wiring, and control semantics.
- `weft/core/tasks/service.py` — `ServiceTask` provides shared long-lived service helpers without owning manager dispatch, leadership, or service-convergence policy.
- `weft/core/tasks/base.py` — `BaseTask` provides queue wiring, process-title formatting (`_update_process_title`), control message handling (`_handle_control_message`, `_handle_control_command`), and state reporting (`_report_state_change`).

## Manager Behaviour [MA-1]

Key responsibilities implemented in `weft/core/manager.py`:

1. **Spawn queue consumption** – Managers bind their public inbox to
   `weft.spawn.requests` for all ordinary requests and monitor
   `weft.spawn.internal` for trusted manager-owned runtime requests.
   Eligible internal work precedes ordinary frontier selection under [MA-1.8].
   Each message contains a serialized child TaskSpec and an optional
   `inbox_message`. Messages are reserved into source-specific reserved queues,
   parsed, launched, and acknowledged via the standard manager launch path.
   Atomic reservation is the dispatch authority for public spawn work: multiple
   live managers may compete for public messages, and exactly one manager owns a
   message once the broker moves it into that manager's reserved queue. A
   manager may service public work it has successfully reserved, regardless of
   advisory registry leadership. Pending messages still sitting in
   `weft.spawn.requests` are unowned backlog and must not by themselves block a
   superseded manager from yielding to the lower-TID live canonical manager.
   A live public manager advances public backlog only after the shared
   `BaseTask`/`MultiQueueWatcher` path has identified the relevant public spawn queue as
   active. The optional bounded per-turn drain may continue draining an already
   active spawn queue, but it must not probe either public source with an empty
   reservation attempt merely to discover whether work exists. Once a queue is
   active, atomic reservation remains the dispatch authority for the specific
   public spawn message. Manager `.reserved` queues are not ordinary wait or
   idle-discovery activity; they are inspected by explicit reservation,
   recovery, ownership-proof, and reserved-policy paths.
   Pipeline-owned runtime child bootstrap is also eligible for the internal
   spawn lane once the top-level pipeline task has already been accepted from
   public dispatch; those child requests are implementation work for that
   accepted task, not new public backlog.
   Pending internal spawn work is manager-owned convergence work: it forces the
   next shared scheduling drain to probe inactive queues so convergence does not
   depend on the periodic broad-probe interval. Internal spawn requests are
   advanced only after the watcher has identified the internal queue as active,
   without consuming ordinary public spawn work. Successful child launch emits
   durable `task_spawned` evidence with the child TID, child TaskSpec, and
   child PID so manager-owned singleton convergence can observe a live
   replacement before the child has completed its own initialization log writes.
2. **Child process launch** – Validated TaskSpecs are submitted to a
   broker-free manager worker lane. That worker calls
   `launch_task_process(Consumer, …)` and returns a local Python result to the
   manager reactor. The worker must not touch SimpleBroker queues, mutate
   TaskSpec state, or publish lifecycle evidence. The manager reactor commits
   the launched child to `_child_processes`, publishes `task_spawned`, updates
   service-owner state, and acknowledges the exact reserved spawn message.
   This preserves the standard isolation model (a dedicated subprocess per
   child task) without blocking task-local control or queue handling while the
   process is launched.
3. **Initial payload delivery** – If the spawn payload includes
   `inbox_message`, the manager writes it into the child’s inbox queue before the
   child starts processing.
4. **Registry heartbeat** – Managers publish an `active` service-owner record to
   `weft.state.services` on startup (including queue names, PID, role, and
   capabilities), refresh that active record periodically while healthy, and
   replace it with a `stopped` record during shutdown. Operator replacement may
   replace it with a `superseded` record after sending STOP. The active record
   refresh path must treat a latest self-owned `superseded` row as a shutdown
   signal and must not publish a newer `active` row over it. Manager-owned
   leadership checks must also treat a fresh self-owned `superseded` row in
   `weft.state.services` as shutdown authority without consulting spawn queues.
   The active record is pruned when the manager exits cleanly. The registry is
   read as a live queue: callers reduce to the latest relevant record per
   service key and owner TID, filter out dead or expired active records, and
   then filter to canonical non-superseded live managers before treating the
   result as the active-manager view. Readers do not delete registry rows. Host-managed records use scoped host
   process identity: a PID is live only when it still matches the recorded
   process creation time when that identity is available. Externally supervised
   records use the process-local runtime liveness probe registry when an
   extension can prove the handle live or stale. Generic manager readers must
   not reinterpret container-local or supervisor-local PIDs as host process
   identity for these handles. A definitive extension `stale` result is treated
   as stale immediately; inconclusive or missing probes are `unknown` evidence,
   not leadership authority. Startup and explicit operator replacement may use
   bounded fallback policy for ambiguous foreground-supervisor rows, but a
   running dispatch-capable manager must not voluntarily yield to an unknown
   external-supervisor row. Manager-owned leadership checks keep a scoped
   in-memory registry view and update it from broker timestamps instead of
   replaying the full service registry every loop turn. When startup, manager
   selection, or manager-owned leadership convergence sees an otherwise
   canonical active manager row that lacks strong runtime proof, including a
   host-PID row whose PID is not visible from the current PID namespace, it may
   send one bounded keyed PING to that manager's task-local control queue. A
   matched PONG from the same TID can rescue the row as positive liveness
   evidence for selection only when its manager-selection fields prove dispatch
   eligibility: manager role, `weft.spawn.requests`, matching control and
   outbox queues, required matching context, `task_status` exactly `created`,
   `spawning`, or `running`, and `should_stop is False`. An absent, malformed,
   non-matching, draining, or stopping PONG is not negative proof and does not
   authorize unsafe takeover by itself. Automatic manager recovery reduces the
   latest canonical records and applies the existing 300-second unknown-record
   age cutoff. An older unknown row no longer suppresses automatic startup, but
   expiry remains recovery policy rather than proof that its process died.
   Registry read failure is uncertainty, not an empty registry.

   For a fresh uncertain incumbent, the caller records monotonic time and
   pending public work before one keyed proof attempt. Positive proof reuses the
   manager. If proof remains uncertain and work was and remains pending, the
   caller waits only the remainder of the namespace-ambiguity grace from that
   first observation, attempts keyed proof once more against fresh registry
   evidence, and then decides. The first probe's elapsed time counts toward the
   grace; the sequence does not restart or insert intermediate probes. Fresh
   proof wins. Empty backlog, failed final reads, or a changed unproved
   incumbent suppress helper startup. An expired or absent incumbent permits
   ordinary startup after a successful fresh registry check. The same unproved
   incumbent plus pending work at both observations permits a helper after the
   grace. Presence is not proof of stalled dispatch: a busy healthy manager may
   qualify. Existing atomic reservation and convergence remain authoritative.
   Submission callers additionally reconcile their exact TID before startup;
   reserved, spawned, rejected, or unknown-location work cannot authorize a
   helper for that submission. Core discovery receives no submission predicate.

   Manager-owned liveness PINGs are non-blocking reactor probes: one turn
   writes a keyed PING with `reply_to` set to the probing Manager's configured
   `ctrl_in`; the matching PONG arrives through the Manager's existing watcher
   and is stored on the pending probe before the leadership reducer runs.
   `ping_pending` remains unknown evidence until a matching PONG or the next
   eligible ordinary leadership evaluation. The probe contributes no private
   deadline to `next_wait_timeout()`. Canonical ownership is lowest-live-TID among
   canonical dispatch-eligible claimants for status, selection, and
   duplicate-manager convergence. It is advisory for public dispatch because
   atomic queue reservation owns public spawn exclusivity.
   Task-list/status read models use the same selected active-manager view. A
   historical manager task-log row is not enough to publish that manager as
   `running` after a different active manager has been selected.
5. **Idle timeout** – Managers honour the `idle_timeout` metadata field first.
   When that metadata is absent they fall back to
   `WEFT_MANAGER_LIFETIME_TIMEOUT`, which must parse as a non-negative float at
   config-load time. Managers then self-terminate after prolonged inactivity
   while ensuring all child processes are reaped. The final idle-timeout
   decision performs a forced broker-activity refresh instead of trusting a
   cached `Queue.last_ts` read. On the control queue only a canonical control
   request counts as activity. A reply to the manager's own PING probe, or an
   unparseable control row, is consumed without resetting the idle clock, so
   recurring liveness probes cannot hold an idle manager alive.
6. **Autostart manifests** – When autostart is enabled for the effective
   context, the manager scans `.weft/autostart/` for manifests that reference
   stored task specs or pipelines and launches each target through the ordinary
   manager inbox, which is normally `weft.spawn.requests` for the canonical
   background manager and may be a scoped inbox for tests or embedded managers.
   Manifest lifecycle policy
   (`once`/`ensure`) is represented with the same manager-owned service
   metadata used by built-in singleton services. Launch and restart accounting
   advances only after a spawn request is successfully written. Ensure-mode
   manifests are reconsidered immediately after a tracked autostart child exits.
   Scan bookkeeping for deleted manifest paths is pruned during refresh so
   long-lived managers do not accumulate stale manifest state indefinitely.
   Pipeline targets are compiled into the same first-class pipeline task used
   by `weft run --pipeline` before the manager enqueues the compiled top-level
   pipeline task on the spawn queue.
   `ManagedServiceState` is the sole mutable owner of built-in and autostart
   service launch, restart, deadline, and backoff state. Autostart source
   discovery may retain a source identity index, but it must not mirror those
   lifecycle values in parallel maps.
7. **Managed services** – The manager reconciles autostarts, heartbeat, and
   `TaskMonitor` through one deterministic manager-owned service path. It also
   supervises `LivenessMonitor` as a third built-in persistent service through
   the same internal spawn queue, runtime envelope, service candidate reducer,
   canonical-owner fence, restart/backoff, and duplicate-convergence path. No
   separate launcher or reconciler exists. The closed service inventory gains
   runtime task class `liveness_monitor`, service key
   `_weft.service.liveness_monitor`, and role `liveness_monitor`; the service
   claims no endpoint. Its internal payload matches the other internal
   services and leaves `enable_process_title` unset, so every internal
   service carries a process title like any task ([OBS.4]). The
   live public managers may supervise built-in internal singleton services;
   correctness is enforced by manager-owned metadata and the singleton reducer,
   not by a global dispatch-owner fence. Scoped managers may still reconcile
   their own autostart manifests. Internal services are context-level singletons. The canonical manager
   ensures the built-in heartbeat service exists, whatever the monitor flags
   say: launched at manager startup if no live owner exists, relaunched by
   ordinary managed-service convergence if it exits, and never stopped,
   signalled or terminated by any manager stop, supersession, leadership yield,
   idle exit, late-launch reap or final cleanup. The same holds for TaskMonitor
   and LivenessMonitor when their flags make them desired. A manager terminates
   a service on exactly three paths: SIGUSR1 terminates its whole child table,
   duplicate convergence sends KILL to a non-canonical duplicate owner, and a
   launch whose result lands after the manager's cleanup deadline is reaped
   ([IMPL.10]). Each service defines its own work and ends itself after
   `SERVICE_IDLE_EXIT_SECONDS` without work and without a live non-superseded
   manager in the context ([MF-3.2]); checking for work is not work. The
   LivenessMonitor's work is a live (non-terminal) user-work mapping row or
   an in-flight probe of one; a terminal row waits for its retention
   retirement without keeping the monitor alive
   (`weft/core/tasks/liveness_monitor.py::LivenessMonitor._mapping_counts_as_work`). An
   explicit STOP on a service's `ctrl_in` stops it as for any task. A service process runs with its context root as
   its working directory (`weft/core/launcher.py::service_working_directory`),
   never the directory its launcher happened to be in. `weft
   system stop` stops the manager and then the context's internal services by
   registry record; it is the one command that empties a context. The
   manager neither reads nor deletes `weft.state.heartbeats`; the heartbeat
   service is its only reader and deleter ([MF-3.2]). The internal `TaskMonitor` is an
   `ensure` service when `WEFT_TASK_MONITOR_ENABLED` is
   true. `WEFT_LIVENESS_MONITOR_ENABLED` independently makes LivenessMonitor
   desired whether or not TaskMonitor is enabled. The liveness flag is launch
   policy, not active-stop authority. Draining or stopped managers do not start
   or restart singleton
   services. Live service ownership can be proved by a tracked child, a live
   runtime handle, including the task-process host handle published in TID
   mappings, manager-authored internal runtime envelope/class evidence,
   manager-authored autostart metadata, or a matched keyed `PING`/`PONG`;
   caller-owned TaskSpec metadata alone is not service authority. Task-owned
   terminal proof for that TID (terminal envelope or terminal task-log status)
   wins over host process liveness, so a service process still unwinding after
   `control_kill` is not a live singleton owner. Stale non-terminal task-log
   rows without live proof do not count as a live singleton. The manager enqueues services through its own inbox using manager-owned
   internal runtime envelopes, tracks a launched service for exit evidence and
   reaping only (its `manager_tid` on the registry row and in status is
   provenance, not custody), and applies bounded restart backoff after
   TaskMonitor exit. The
   manager reduces pending-spawn, live, terminal, and uncertain evidence
   through one pure transition table before applying queue or process side
   effects.
   Pending service evidence first uses a live tracked candidate or an in-flight launch
   when the reducer gives that evidence precedence. Otherwise, Manager-local service
   state may retain one exact pending spawn-request message ID. Pending evidence from
   that reference requires a non-consuming exact lookup and trusted service-key
   validation in the same source/reserved queues currently in scope. An absent or
   invalid reference triggers durable recovery scanning; a successful scan may retain
   one matching ID or prove absence. Failed reads or partial scans do not prove absence
   or clear the reference. Restart reconstructs pending evidence from queues.
   ID-preserving reserve/requeue operations remain visible without a timestamp
   watermark. The reference is not lifecycle authority and does not alter service
   dispatch lanes or reducer decisions.
   Scheduling only determines when evidence is collected; it must not
   bypass `reduce_managed_service_state` for lifecycle decisions. Each manager
   turn advances service convergence with bounded passes over child reap,
   service reconciliation, and manager-owned internal spawn drain; pending
   internal spawn work bypasses the stable-service throttle because it is
   accepted-task work, not background audit work. An autostart scan that is due
   also bypasses the convergence throttle once so the due bit is either cleared
   by an empty scan or converted into concrete service work, instead of keeping
   the task loop on a zero timeout. Manager due timers are exposed
   through `Manager.next_wait_timeout()` and then bounded by the shared
   `BaseTask`/`MultiQueueWatcher` activity wait path. Backend activity waiters
   are hints, not the clock that drives service supervision. Child-process exit
   reaches the Manager reactor as a local event from one Manager-owned sentinel
   adapter; post-exit terminal-proof grace remains a published timer.
   After child exit, the Manager retires child bookkeeping and acknowledges any
   remaining unacknowledged spawn reservation only after observing task-owned local
   terminal proof or committing a
   Manager-source `wrapper_lost` envelope. A successor that settles an adopted
   launch whose reservation was never handed off is bound by the same rule:
   it commits the envelope, or finds task-owned proof, before it acknowledges.
   A failed terminal-proof read is unknown, not
   terminal proof. A failed failsafe publication retains the existing exited-child
   record and any unacknowledged reservation, with a bounded retry deadline through the
   existing reactor timeout path. Cleanup retires a due publication-retry deadline
   when servicing it, so any proof-grace interval that then starts becomes the
   active deadline; another failed publication installs a fresh retry. It neither spins
   on an expired grace deadline nor
   waits for unrelated queue activity. Task-owned terminal proof still wins if it
   arrives later. Bounded Manager shutdown may end retries at its existing deadline; it
   leaves unresolved reserved work under the existing recovery policy rather than
   asserting successful acknowledgement. Successful-launch acknowledgement follows snapshot handoff or positive settlement under [MF-6].
   TaskMonitor may project exact retained local terminal observations into its
   operational collation under [MF-5] and [OBS.13.13]. This adds no Manager
   publication or task-log lifecycle event and grants no deletion authority.
   Ordinary
   service reconciliation and leadership convergence run on the slower
   approximately one-second timer while idle. Queue activity still wakes the
   manager promptly through the shared watcher. PONG arrival makes its owning
   reducer actionable immediately without moving the periodic policy clock.
   An unanswered leadership probe is evaluated only by the next eligible
   ordinary leadership pass; an unanswered service probe is evaluated only by
   the next eligible ordinary active-service pass. PING/PONG contributes no
   timeout to `next_wait_timeout()` and has no manager-specific fallback poll
   interval. A successful complete owning-policy scan may retire pending probes
   whose source is no longer present; a failed or partial scan retains them.
   The sentinel adapter has one observer thread, one lock-protected membership
   dictionary keyed by TID, the existing coalesced membership/stop pipe wake, and fired
   `(tid, sentinel)` identities. The Manager registers or removes one child at each
   authoritative child-registry mutation. The observer snapshots membership when
   preparing a standard `multiprocessing.connection.wait`; the reactor does not rebuild
   all membership on each launch. Identical re-registration preserves exit suppression;
   replacement and removal invalidate stale identities. The Manager reactor alone
   validates identities, mutates the child registry, and reaps children. Adapter failure
   remains fatal, and shutdown joins it and closes its endpoints before the retained
   strategy closes.
   Drain progress is deadline-driven rather than turn-driven. A newly entered
   drain publishes one immediate continuation, then only the deadlines that can
   change its state: child STOP-to-SIGTERM escalation, the bounded shutdown
   window, or leadership-drain revalidation. A child whose sentinel has fired
   publishes the PID-liveness recheck only until terminal-proof grace begins;
   terminal-proof grace then becomes the sole child deadline. If a child-launch
   worker retires without publishing a result, the launch-start grace is also a
   published recovery deadline. These clocks must not be replaced by an
   unconditional zero timeout.
   Housekeeping keeps its own due timers; unrelated wakes do not accelerate
   leadership or service audit intervals. The idle clock runs only while
   idle shutdown is enabled and no user-work child is live. While it is not
   running, neither the broker-probe deadline nor the idle-expiry deadline
   is published, so a stale timestamp cannot schedule an immediate wait
   timeout.
   Ordinary manager-tracked service convergence should use local child liveness
   and child reap rather than replaying global task-log terminal proof while the
   child is still locally live. Targeted internal-service ticks and public read
   models may still scan terminal proof when they need explicit task-owned
   terminal evidence.
   Recent nonterminal service rows without live proof are
   uncertain evidence, not terminal proof, so short probe misses cannot clear a
   freshly launched singleton and start duplicates. The Monitor may later
   dispose old nonterminal service-owner collations for cleanup only after
   same-service registry proof shows the owner is non-live or replaced and no
   runtime evidence protects that TID; that disposition does not change manager
   lifecycle truth. If multiple live owners
   are visible, the reducer selects the canonical live owner and the manager
   sends kill control to non-canonical live owners. Force-reaping is limited to
   PIDs tied to the same authority that made the candidate live, currently a
   tracked child process or a scoped `host-pid` runtime handle. Logged task
   state PIDs and manager `task_spawned` child PIDs are display and liveness
   evidence, not standalone force-kill authority. The manager does not scan
   task-log history for cleanup and does not run monitor processors itself.
   Ops service status is a read model over the same durable evidence. A
   manager `task_spawned` row with `child_tid`, `child_taskspec`, and
   `child_pid` may publish heartbeat or TaskMonitor as launched before the
   child has emitted its own lifecycle row; later child-local task-log,
   terminal, TID-mapping, and service-registry evidence wins over that manager
   launch hint. Status may reconcile an old internal-service child task row as
   failed when no runtime proof remains and the service registry proves a newer
   same-service owner, but that is public read-model reconciliation, not a
   manager lifecycle transition or cleanup effect.
   Service-candidate PING fallback is also non-blocking reactor state: the
   manager writes one keyed PING with its configured `ctrl_in` as `reply_to`,
   records the pending service probe, and receives the matching PONG through
   its ordinary control drain. A pending service probe selects the existing
   active-service cadence and keeps its service key in the current evidence
   scope. `ping_pending` candidates remain uncertain evidence until a matching
   PONG or the next eligible ordinary active-service evaluation.
   Constructor bootstrap anchors the applicable cadence after creating a probe,
   so the first reactor turn cannot classify it as no-PONG. A stored PONG makes
   its owning reducer actionable without moving the periodic policy clock.
   Within the current reconciliation scope, pending service probes keep their
   service keys in evidence collection. When autostart is included, a stored
   PONG for a known autostart source bypasses the manifest-scan throttle so the
   reply is reduced against current source evidence. A successful complete
   owning-policy scan may retire a probe whose source is absent; failed,
   throttled, and partial scans retain it.
8. **Control channel** – In addition to STOP/STATUS, managers inherit the
  task control contract: `PING` replies with `PONG` plus a live task-local
  status snapshot on the requester's named `ctrl_in`, echoing `request_id` and
  omitting `reply_to`. Manager PONG snapshots also include manager-selection fields
  (`role`, `requests`, `ctrl_in`, `ctrl_out`, `outbox`, and required
  `weft_context`) so external selection code can validate the
  responding task without importing command-layer helpers.

**Admission control [MA-1.8]**

A standing dispatch refusal is reported in dispatch_refusals on the Manager's complete weft.state.tasks.<manager_tid> snapshot. It describes actual accepted work refused beyond one due maintenance pass, not every retained cleanup obligation. The observation names the existing reason, actual request/ancestry-path or dispatch-lane scope, causal retained records or resource measurements and ending condition; it never grants dispatch, handoff, replay or settlement. A lane refusal is a fixed-size lane/barrier aggregate with an owner-indexed pending-count observation and oldest pending source witness, never an enumeration of queued subjects. A normal per-task read failure changes no Manager mode, checkpoint or diagnostic. Diagnostic publication uses the complete task snapshot without completing bootstrap, clearing undurable responsibility or substituting for the checkpoint owner's success transition. A successor derives observations from retained evidence, preserving predecessor custody. D1's post-launch started evidence may end an eligibility hold without satisfying scoped custody handoff or death proof.

Implementation mapping: `weft/core/manager.py::Manager._assess_dispatch_refusals`
projects final owner gates after a normal reactor turn; `_dispatch_lane_refusals`
reuses bounded source witnesses, and `_build_tid_state_payload` only serializes
cached observations. `weft/core/dispatch_refusals.py` shares pure conversion and
semantic comparison; `weft/_constants.py::DISPATCH_REFUSAL_DESCRIPTORS` owns the
reason/ending inventory. `tests/core/test_admission_recovery.py` and
`tests/core/test_manager_family_maintenance.py` exercise custody, generations,
append/read cost and real crash recovery.

`Manager._admission_backend_name` uses the task's bound runtime context to
identify the backend on the dispatch path. See the
[nested admission load plan](../plans/2026-11-07-nested-admission-load-plan.md).

Ordinary admission uses one coherent SimpleBroker observed-usage population. Available capacity is the configured applicable ceiling minus the configured operating reserve minus observed usage. A positive available value permits consideration of one eligible launch; it is not a claim that the task will consume one connection. There is no per-task connection budget, lifetime deficit, or ordinary child admission class. Usage includes external and unattributed pressure. Failed or unknown observation defers dispatch without rejecting accepted work. Admission and launch are not globally atomic. Internal runtime requests retain their documented reserve eligibility; their measured use participates in the same population. SQLite retains its live/unknown TID accounting and local-launch union. On SQLite, a disabled resource gate does not disable family classification or launch custody.

On PostgreSQL, unset or zero `WEFT_ADMISSION_MAX_CONNECTIONS` means automatic backend capacity, not disabled admission. On each fresh admission observation the Manager reads `max_connections` through SimpleBroker's public `get_connection_stats()` on its existing Queue, then reads the coherent total/by-owner usage through public `get_connection_usage()` outside an encompassing caller transaction. The ordinary gate is `available = max(0, C - R - U)` with that backend maximum C and the existing global reserve R. A positive configured ceiling overrides automatic capacity. Failure to obtain either capacity or usage defers launches as an unknown observation and preserves accepted work, custody and existing drain-wait clocks. Updating the effective ceiling does not reset those clocks. There is no family count in this calculation. On SQLite, unset or zero retains the existing disabled resource mode; positive ceilings retain actual live/unknown-TID accounting. A disabled SQLite gate supplies no implicit process-count bound.

Ordinary launch selection is work-conserving family scheduling over dynamically observed ancestry. There is no configured frontier count or family concurrency cap. Active branches receive round-robin turns and offer newest eligible tip children first. Active branches rotate in ring order from a cursor, and a new path joins the rotation last: it is inserted immediately before the path the cursor names next, so it is visited after every path already waiting; a path keeps its place when it extends to a child or contracts to its parent; a path that becomes a prefix of a sibling's path leaves the rotation; each selection asks branches in rotation order starting after the branch that supplied the previous selection; a recovery rebuild orders paths in resident adoption order and restarts the cursor at the first of them; no other order among branches is promised. If none has an eligible tip child, the oldest independent pending family enters FIFO by original root ID; otherwise an active family may offer its oldest unassigned sibling subtree, choosing families round-robin. A live finite tip and its ancestors retain lineage, not a capacity permit. A child extends its matching ancestry path; a sibling starts another path when selected and when measured admission and existing launch custody allow it. Positive process exit contracts a path without dropping known descendants. Logical family membership remains while pending or resident descendants exist; late-discovered work rejoins at the original root FIFO position or extends a matching live tip. Queued requests incur no process or prospective connection charge. Multiple Managers retain exact-ID work stealing and measure the same applicable cross-process resource population.

Every ordinary PostgreSQL launch requires a fresh coherent usage observation
and the existing measured admission decision. Let L=max(0,C-R), H=floor(4L/5),
and W=6. If H>=W and observed usage U<H, a Manager may have up to W unfinished
ordinary startups. Otherwise it waits until no ordinary startup is unfinished
before authorizing one launch. Unknown usage denies dispatch. Window occupancy
counts selected launch-worker requests and processes awaiting startup feedback
once per TID, starting before process creation; it does not count queued work
or running tasks whose startup feedback has settled. SQLite retains serialized
startup. The window bounds unobserved launch exposure, not connection consumption;
it adds no per-task resource charge, family cap, or global admission atomicity.
The window is local to one Manager incarnation. Surviving tasks adopted after
restart remain subject to existing resident/custody recovery and fresh measured
admission, not reconstruction into the new Manager's local startup window.
Startup settles independently for each TID when its wrapper becomes observable
in `weft.state.tasks.<tid>` or attributed broker usage, or through existing
failure/reap handling or the bounded missing-publication fallback below.
A negative startup-readiness decision suppresses ordinary source readiness until
startup membership or launch-worker evidence changes, or the existing admission
recheck deadline arrives. A due recheck can reopen the window with a fresh usage
observation. Positive admission is never cached across launches. Startup blocking
does not start or reset the resource-exhaustion drain-wait episode.

Because initial task-state publication is best-effort, if `MANAGER_ADMISSION_RECHECK_SECONDS` elapses while the launched wrapper remains alive without a visible snapshot, the Manager retries snapshot observation once and falls back to retaining the live task in local state and a successfully published Manager checkpoint from launch-time `RuntimeIdentity` and scoped process evidence, emits a rate-limited diagnostic, refreshes observed usage, and clears that TID's startup feedback. Gate release does not acknowledge the reserved request. A launch with known post-launch process evidence must preserve recovery custody through task-owned snapshot handoff/positive settlement or a successful post-launch checkpoint before another ordinary dispatch; failed required checkpoint publication gates dispatch and retries through bounded maintenance. A retained no-receipt launch follows the scoped hold rule below. A failed read of a task's state snapshot during reconciliation or reap is logged and changes nothing: the resident record, its reservation, the published checkpoint, and ordinary dispatch all stand, and no Manager mode records the failure. A successor dispatches no ordinary work until its bootstrap completes: its inherited checkpoints read and its own first checkpoint published. A retained row whose recorded runtime identity does not match the reading Manager (it names another context, or its connection owner does not match its runtime identity) is not that Manager's evidence: bootstrap skips it, emits one runtime warning naming the skipped rows, and continues. If a retained pre-launch reference has no durable post-launch process receipt, a successor preserves its validated identity, unresolved reservation, and finite frontier where applicable. The ambiguous launch and accepted requests whose immutable ancestry includes its TID remain held; unrelated roots and sibling branches may dispatch under the normal measured admission and frontier rules. A validated task-authored snapshot matching the immutable runtime identity and reporting a started process, or a durable post-launch process receipt, ends the scheduling hold. A snapshot with an unknown process creation time may establish started evidence for eligibility but does not authorize custody acknowledgement or prove process death; missing evidence never authorizes replay or terminal inference. A failed required checkpoint publication remains a separate ordinary-dispatch barrier until durable custody is established. A fresh in-flight launch retains its startup-feedback gate, but a retained ambiguous launch does not occupy that transient observation window indefinitely. While the exact reserved request remains pending and no resolving evidence arrives, the request stays held; operator queue consumption follows the escape-hatch rule below. Such held work is outside the conditional drain guarantee.

Launch custody follows ordinary pending-message semantics. A successful default
peek finding the exact reserved request absent, including after an operator
queue read or exact delete, ends that pending custody. Reconciliation retires a
no-receipt resident and releases its derived ancestry hold without replay or a
terminal/death inference. A retained checkpoint cannot recreate custody for a
request that is no longer pending. Pending ambiguous requests remain held;
failed reads preserve the obligation. Active launch workers/local children and
positive process supervision retain their existing protections.

Startup feedback does not certify a task's future maximum use. Admission waiting, classification, reconciliation, controls, and reaping remain non-blocking and use bounded reactor work. A bounded classification page must not become a fixed FIFO visibility window.

Startup settlement and a successful launch-worker receipt with room remaining
in the startup window reactivate the ordinary public source. The next reactor
turn reevaluates known work through normal measured admission and custody gates
without waiting for inactive-source rediscovery. The normal pending check makes
the source idle again when empty. A negative startup-readiness decision suppresses
unchanged blocked backlog as ordinary wait activity.

Each unfinished ordinary startup has one transient notification-only interest
in `weft.state.tasks.<tid>` once its launch-worker receipt is available. There
are at most W outstanding interests (one on SQLite).
These queues participate in the existing activity waiter, not delivery: no
handler, consumption, reservation or task-state mutation is added. Install
interest before reading and retain a strict publication cursor per TID. A new
publication returns control even if no delivery queue is actionable. Validate
exact snapshot identity before handoff or acknowledgement. Rejected evidence
advances only its own cursor and retains its interest without unchanged-row
spinning. Reconcile ready TIDs independently, including out-of-order publication;
do not wait for an older TID or the periodic reconciliation tick. The existing
one-second absence fallback applies independently to each startup. Feedback,
positive exit and failure release only their own interest; cleanup closes all
owned interests. Native waiter
replacement registers the candidate before displacing and closing the old waiter,
under existing owner/stop serialization. The process-launch worker stays
broker-free; no helper reader thread or historical handles are retained.
PostgreSQL reuses SimpleBroker's shared process-local listener; SQLite keeps
its existing data-version/polling mechanism.

Family ordering and resource admission answer different questions. The scheduler must offer eligible indexed work whenever it exists; only measured admission and the existing startup, ownership, and recovery-custody gates may defer its launch. This uses available resource capacity without a fixed family limit. Finite independent-subtree workloads can drain when leaves terminate, required descendants are eventually submitted, broker maintenance is fair, and measured headroom eventually permits each required launch. Depth-first preference prevents already-known descendants from sitting behind independent parent backlogs. It does not guarantee progress if already-running parents retain all capacity needed by descendants submitted later. Resource limits, external saturation, unbounded work, ambiguous launch custody, and arbitrary cross-task waits remain real limits. There is no runtime frontier allowance, prospective path reservation, or child-count declaration in this condition.

Validated persistent or interactive tasks use the ordinary primary source and measured admission. Their lineage remains in the startup path until positive startup evidence or the bounded missing-snapshot fallback clears the existing startup gate; afterward they become background residents while their actual usage remains measured. Their validated scheduling class and lineage remain in the retained Manager checkpoint until task-owned snapshot handoff or positive settlement. Releasing startup lineage does not acknowledge the reservation. Background ancestry is transparent when locating a finite child scheduling parent. A child with no active finite ancestor starts a new ancestry path subject to ordinary measured admission and custody, without a spare-frontier requirement. Activity snapshots or timeouts cannot infer this lifecycle class. Unmarked long-running one-shot tasks retain their finite ancestry. Background and finite resource use both participate in measured usage.

Classification advances in bounded pages even while admission is denied. A periodic resumable source repair covers explicit older request IDs and returned rows below the classification watermark. Completion and frontier reuse consume known indexed work directly; their broker-operation count must not grow with unrelated backlog. Cache contracted scheduling-parent pointers; do not derive a full graph per dispatch. Startup reconstructs living paths from task snapshots and surviving Manager checkpoints, then repairs source indexes. In-memory selection and path bookkeeping scale with live ancestry, not a fixed number of frontier places; no replacement concurrency cap or fixed FIFO visibility window is introduced.

A source row above the classification watermark is pending reactor work whenever classification can advance. A newly accepted request therefore wakes the Manager through the shared watcher; it does not wait for a housekeeping deadline. The check reads the source queue at the watermark and adds no timer.

Derived pending indexes must not retain unbounded selection history while
their live pending population stays bounded. Stale-entry reclamation must
preserve selection order and cheap parent-bucket meld, operate on the affected
bucket rather than scanning unrelated pending work, and leave broker custody
unchanged. A bucket-local linear compaction is permitted with an amortized
bound and measured latency evidence; this is not permission for a full pending
graph or dictionary scan on each dispatch or exit.

The frontier's contracted-pointer table and each pending bucket compact when their physical entries exceed twice their live entries.

Family scheduling partitions known pending requests into eligible and held in-memory indexes according to retained ambiguous ancestors. Holding a request does not consume its source row or pending token. Hold-set changes reclassify known pending requests once; normal selection does not repeatedly scan a held request prefix. Successor recovery rebuilds this derived eligibility from retained custody and lineage.

Reserve fraction is finite in `[0,1)` and defaults to `0.1`; reserve `R` is `max(ceil(C*f), 3 + int(liveness_monitor_enabled))`. Internal runtime work may use the reserve but still requires measured usage below C. Unset or zero `WEFT_ADMISSION_MAX_CONNECTIONS` selects the backend maximum on PostgreSQL and disables the resource gate only on SQLite. A positive configured ceiling overrides automatic capacity. The removed `WEFT_ADMISSION_TASK_CONNECTION_BUDGET` and `WEFT_SCHEDULER_MAX_FRONTIERS` settings raise actionable configuration errors directing operators to measured admission and its global reserve.

SQLite is context-scoped:
it reads the newest valid snapshot from each `weft.state.tasks.<tid>` queue
in one shared broker scope, keeps the rows
whose payloads the shared `weft/liveness/policy.py::mapping_row_is_live`
admission probe reports live or undecidable, and unions that set with this
Manager's in-flight child launches
and committed child processes; each full TID counts once. A task-owned
`terminal: true` mapping with no positive scoped host-process proof probes
dead. Positive scoped `(pid, create_time)` liveness remains stronger than the
terminal hint. Undecidable non-terminal rows stay counted. Local launch and
process evidence remains counted until the existing launch-failure or
child-reap path clears it. Probe verdicts are memoized per TID: the fingerprint
includes the normalized `terminal` hint and runtime handle, a dead verdict is
permanent while that fingerprint is unchanged, and a live verdict expires on
the admission recheck interval. Admission adds no second liveness policy,
durable cache, index, cleanup, or freshness lifecycle beyond that shared
probe.
Denial or observation failure leaves the row unreserved until the
existing one-second retry deadline or an earlier child/launch-worker wake.
Enabled
PostgreSQL admission also tracks one Manager-local drain-wait episode while
any configured lane is blocked. Its progress clock advances only for a strict new
episode-low usage observation or an actual child reap; probe success, launch
completion and retry expiry are not drain progress. An unavailable initial
observation retains its start clock when a first valid baseline arrives.
Thirty seconds without drain progress or 180 seconds since episode start
marks the wait stalled. Fresh capacity that opens all configured lanes ends the
episode, as does an existing hint-driven scan proving all configured spawn sources
empty without extra reads; skipped, failed or stopped probes cannot establish
emptiness. Unobserved idle gaps conservatively retain episode age. Internal
admission alone does not end it. Progress may clear an idle stall
before 180 seconds, but never renews the absolute assessment cap. Stalling
changes bounded operational diagnostics only: queued work and the same
periodic/early recovery checks remain. It never grants capacity, fails work,
or blocks control and cleanup. These deadlines are monotonic assessment
budgets, not broker-call or task execution timeouts. SQLite policy is unchanged.
The public PostgreSQL usage observation catches only `DatabaseError` and
`ValueError`. Its separate runtime/recovery evidence refresh catches
`BrokerError`, `OSError`, `RuntimeError`, `TypeError`, and `ValueError`;
these failures also produce an unknown observation and deny admission. SQLite catches only
`BrokerError`, `OSError`, and `RuntimeError`. Shutdown or control
`BaseException` subclasses propagate.
Configuration is fixed for one Manager lifetime and takes effect after restart.
Pipeline-generated stage/edge work keeps its existing internal routing and measured usage population; trusted internal ancestors are transparent to finite frontier selection.
Both native and fallback waits suppress only a blocked
spawn source as ordinary queue activity; unresolved custody is suppressed from pending-work and PEEK activity while bounded reconciliation remains scheduled.
Once another live lower-TID primary is proven, a non-primary Manager reserves
no new ordinary or internal work. Shared unreserved internal backlog is not work
that the non-primary owns; existing reservations, active launches, locally
tracked children, and actionable control remain owned work.

_Implementation mapping_:
- [MA-1.8] Source readiness — `Manager._family_classification_can_advance` and `Manager._unclassified_source_row_pending` share watermark eligibility with classification, including admission-blocked input.
- [MA-1.8] Pending-index reclamation — `weft/core/family_scheduling.py::_PendingIndex.remove`, `_compact`, and `_owns` preserve bucket-local physical bounds and current ownership while retaining leftist meld.
- [MA-1.8] Frontier ring and contracted ancestry — `weft/core/family_scheduling.py::FamilyScheduler` keeps one entry per childless resident in a circular linked ring with a cursor (`_ring_next`, `_ring_prev`, `_ring_tip`, `_tip_stack`, `_turn`), contracted scheduling-parent pointers over residents and former residents (`_up`; `_find` compresses on lookup, `_compact_up` drops former residents once they outnumber residents), and live-child counts (`_live_children`). `launched`, `retire`, and `finish_recovery` maintain them; `select` walks the ring from the cursor; `frontiers` derives each path from its tip's `ancestry_tids` for the Manager checkpoint. This realizes "Cache contracted scheduling-parent pointers" for residents. Verification: `tests/core/test_family_scheduling.py`.
- [MA-1.1] Spawn queue consumption — `Manager._build_queue_configs`, `Manager._handle_work_message`, `Manager._build_child_spec`, `Manager._drain_public_spawn_requests`, `Manager._drain_spawn_requests_from_queue`, `Manager._handle_child_launch_result`.
- [MA-1.2] Child process launch — `Manager._launch_child_task` prepares the launch request, enqueues it through the registered `ServiceTask` child-launch worker group, `Manager._run_child_launch_service_worker` reads the local Python input queue, `Manager._run_child_launch_worker` calls `launch_task_process` (`weft/core/launcher.py`) on the broker-free worker lane, the completed launch is handed to the reactor under `Manager._launch_handoff_lock` (or reaped by `Manager._reap_unaccepted_launch` once cleanup has closed acceptance, [IMPL.10]), and `Manager._handle_accepted_launch_results` with `Manager._commit_child_launch_success` applies durable effects on the manager reactor.
- [MA-1.3] Initial payload delivery — `Manager._launch_child_task` (inbox seeding block).
- [MA-1.4] Registry heartbeat and leadership view — `weft/core/service_convergence.py::build_service_owner_payload`, `weft/core/service_convergence.py::build_manager_service_payload`, `weft/core/service_convergence.py::parse_service_owner_row`, `weft/core/service_convergence.py::project_manager_service_record`, and `weft/core/service_convergence.py::discard_v1_service_registry_rows`; Manager bootstrap plus `Manager._register_manager`, `Manager._unregister_manager`, `Manager._atexit_unregister`, `Manager._update_manager_registry_snapshot`, `Manager._publish_superseded_manager_record`, `Manager._read_active_manager_records`, `Manager._active_manager_records`, `Manager._leader_tid`, `Manager._evaluate_dispatch_ownership`, `Manager._manager_pong_dispatch_proof`, `Manager._advance_manager_pong_probe`, and `Manager._maybe_yield_leadership`; `weft/core/manager_runtime.py::_registry_queue`, `weft/core/manager_runtime.py::_snapshot_registry`, `weft/core/manager_runtime.py::_manager_registry_disposition`, and `weft/core/manager_runtime.py::_retain_latest_included_manager_record`; `weft/commands/system.py::_collect_service_registry_evidence`; and `weft/core/monitor/task_monitor.py::TaskMonitor._latest_service_owner_records`. The one complete PONG dispatch-eligibility gate is `weft/core/control_probe.py::pong_proves_dispatch_eligible`; manager and manager-runtime selection paths delegate to it without adding a second narrowing rule. Registry-record context fallback may help resolve the expected context, but a manager PONG without its own exact matching `weft_context` never proves authority.
- [MA-1.5] Idle timeout — `Manager._idle_clock_running` couples idle deadline publication and evaluation; `Manager.process_once` (idle-timeout check), `Manager._note_control_request_activity` (only canonical control requests reset idle activity), `Manager._read_broker_timestamp`, `Manager._update_idle_activity_from_broker`, `Manager._managed_service_convergence_active` (active convergence resets idle activity), `Manager._manager_owned_work_pending`, `Manager._autostart_ensure_obligation_pending`.
- [MA-1.6] Autostart manifests — `Manager._reconcile_managed_services`, `Manager._tick_autostart`, `Manager._desired_autostart_services`, `Manager._mark_autostart_enqueued`, `Manager._prune_autostart_state`, `Manager._build_autostart_spawn_payload`, `Manager._load_autostart_manifest`, `Manager._load_autostart_taskspec`, `Manager._load_autostart_pipeline`, `Manager._active_autostart_sources`, `Manager._cleanup_children`, plus `weft/core/pipelines.py::compile_linear_pipeline` for stored pipeline targets. `weft/core/manager_services.py::ManagedServiceState` is the sole launch/restart/backoff state owner for autostarts and built-ins; `weft/core/manager.py` retains autostart source identity only, while `reduce_managed_service_state` owns transition selection.
- [MA-1.6a] Managed internal service supervision — `Manager._run_managed_service_convergence`, `Manager._reconcile_managed_services`, `Manager._tick_internal_services`, `Manager._tick_managed_service`, `Manager._service_supervision_allowed`, `Manager._build_heartbeat_spawn_payload`, `Manager._build_task_monitor_spawn_payload`, `Manager._build_liveness_monitor_spawn_payload`, `Manager._liveness_monitor_service_spec`, `Manager._pending_service_keys`, `weft/core/manager.py::Manager._pending_request_reference_is_valid`, `weft/core/manager.py::Manager._recover_pending_service_requests`, `weft/core/manager.py::Manager._tracked_service_terminal_proof_visible`, `Manager._trusted_service_key_from_metadata`, `Manager._service_key_for_child`, `Manager._observed_service_candidates_by_key`, `Manager._service_candidate_from_task_log`, `Manager._service_pong_candidate`, `Manager._advance_service_pong_probe`, `Manager._candidate_force_kill_pids`, `Manager._runtime_handle_force_kill_pids`, `Manager._enqueue_managed_service_request`, `Manager._drain_internal_spawn_requests`, `Manager._cleanup_children`, `weft/core/manager.py::Manager._child_terminal_proof_visible`, `weft/core/manager.py::Manager._write_manager_terminal_envelope`, `Manager._commit_wrapper_lost_envelope`, `Manager._terminal_proof_visible`, `Manager._terminal_envelope_visible`, and `Manager._unhanded_launch_ctrl_out_name`, `weft/core/manager.py::Manager._manager_child_timeouts`, `Manager._drain_child_sentinel_events`, `weft/core/manager.py::Manager._register_child_sentinel`, `Manager._retire_expired_unvisited_probes`, `Manager.next_wait_timeout`, `Manager.wait_for_activity`, and `Manager._user_work_children`; `_ManagerChildSentinelAdapter` is the broker-free local child-exit source; public submission sanitization lives in `weft/core/spawn_requests.py::submit_spawn_request`; shared service models and `reduce_managed_service_state` live in `weft/core/manager_services.py`, runtime TaskMonitor behavior lives in `weft/core/monitor/task_monitor.py`, LivenessMonitor runtime behavior lives in `weft/core/tasks/liveness_monitor.py` with pure evidence and policy in `weft/liveness/`, processor contracts live in `weft/core/monitor/runtime.py`, and ops service status reduction lives in `weft/commands/system.py::_collect_internal_service_snapshots`; a service's own lifetime (`ServiceTask._has_work`, `ServiceTask._work_undetermined`, `ServiceTask._live_manager_present`, `ServiceTask._settle_idle_clock`, the idle clause of `ServiceTask.next_wait_timeout`, and the `_has_work` overrides in `weft/core/tasks/heartbeat.py`, `weft/core/monitor/task_monitor.py` and `weft/core/tasks/liveness_monitor.py`) lives in `weft/core/tasks/service.py`; `weft/commands/system.py::stop_system` stops the Manager and then the context's services by registry record through `weft/commands/system.py::stop_internal_services`, the service-only half a caller that has already handled the Manager (the test harness) uses directly.
- [MA-1.7] Control channel — inherited from `BaseTask._handle_control_command` (`weft/core/tasks/base.py`) and extended by `Manager._control_snapshot_fields` (`weft/core/manager.py`); structured PING/PONG snapshots, STOP, STATUS, KILL handling.
- [MA-1.8] Admission: `weft/core/admission.py::AdmissionController` owns pure measured decisions and drain-wait diagnostics; `weft/core/admission.py::AdmissionController.set_max_connections` updates the effective ceiling without resetting that history. `weft/core/manager.py::Manager._read_postgres_connection_stats` resolves automatic backend capacity. `weft/core/manager.py` owns exact-ID selection, frontiers, incremental pending indexes, startup feedback, checkpoint custody, bounded recovery and process probes. `weft/core/manager.py::Manager._activity_wait_queues`, `weft/core/manager.py::Manager._has_pending_observation`, and `weft/core/manager.py::Manager._clear_startup_notification_interest` own the bounded notification interest and reactor handoff. `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher._ensure_multi_activity_waiter`, `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher._wait_for_activity_body`, and `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher._activity_hint_action` own subscription replacement and observation wake filtering. `weft/_constants.py` owns configuration. `weft/core/tasks/base.py` owns task snapshots; `weft/core/runtime_identity.py` owns immutable transport. LivenessMonitor and TaskMonitor retain their cleanup boundaries under [LIVENESS.R3–4], [LIVENESS.R7]. Additional verification: `weft/core/family_scheduling.py`, `tests/core/test_admission.py`, `tests/core/test_connection_admission.py`, `tests/core/test_work_conserving_admission.py`, `tests/core/test_family_scheduling.py`, `tests/core/test_manager_family_maintenance.py`, `tests/helpers/family_workload.py`, `tests/system/test_child_admission.py`, `tests/system/test_family_scheduling.py`, `tests/system/test_measured_admission_pg.py`.

The bounded [MA-1.8] startup decision is
`weft/core/admission.py::_startup_window_allows_launch`, applied only after a
fresh resource decision. `Manager._startup_dispatch_blocked` shares negative
readiness between dispatch and waiting; `Manager._resume_startup_dispatch`
reactivates known work after a receipt or per-TID settlement.
`Manager._refresh_scheduler_evidence` visits every local startup with one lazy
coherent usage sample per pass. Startup records are transient observations,
not durable custody or restart authority.

Implementation plan backlink for [MA-1.4]:
`2026-08-08-registry-selection-pruning-authority-refactor-plan` (retired — source 396c3b85; see `docs/plans/README.md`)
and `2026-08-10-canonical-contract-and-dead-code-cleanup-plan` (retired — source 396c3b85; see `docs/plans/README.md`).

Implementation note for [MA-1.4]:
`weft/core/service_convergence.py::discard_v1_service_registry_rows` uses its
initial complete scan as absence verification when there is nothing to delete;
a deletion retains the separate verification scan. Manager selection still
performs its own pending-row read. `weft/core/manager_runtime.py::observe_manager_availability`
shares one bounded outer connection across its registry and backlog reads. A
synchronous keyed proof owns a temporary nonpersistent `MultiQueueWatcher` and
listener, which it releases before returning. Nonpersistent mode lets the
watcher close while an outer caller-owned broker operation remains open on the
same thread.
See `2026-09-17-submission-manager-check-cost-plan` (retired — source 396c3b85; see `docs/plans/README.md`).

Implementation plans for [MA-1.6a]:
`2026-05-10-control-and-service-convergence-state-machine-plan` (retired — source 396c3b85; see `docs/plans/README.md`),
`2026-05-11-internal-service-observability-plan` (retired — source 396c3b85; see `docs/plans/README.md`),
`2026-05-15-manager-hot-loop-reduction-plan` (retired — source 396c3b85; see `docs/plans/README.md`),
`2026-05-15-task-reactor-and-evidence-worker-plan` (retired — source 396c3b85; see `docs/plans/README.md`),
`2026-05-15-manager-reactor-hot-loop-follow-up-plan` (retired — source 396c3b85; see `docs/plans/README.md`),
`2026-05-18-reactive-task-loop-hot-probe-plan` (retired — source 396c3b85; see `docs/plans/README.md`),
and the hardening plan
`2026-05-10-manager-service-authority-boundary-hardening-plan` (retired — source 396c3b85; see `docs/plans/README.md`).

`weft.state.services` stores one `schema="weft.service_owner.v2"`
service-owner object. Required keys are `schema`, `service_key`,
`service_type`, `owner_tid`, `name`, `status`, `queues`, `runtime_handle`, and
`metadata`. Manager rows also carry `role` and `capabilities`. `queues` is the
only persisted queue-name mapping. The persisted object does not duplicate
`owner_tid` as `tid`, does not copy queue names to top-level fields, does not
alias `requests` as `inbox`, and does not carry `legacy_role`. Manager/status
read models may project documented convenience fields from this canonical
object, but they are not alternate persisted schemas.

`weft.state.services` custody: a row's logical owner may exact-delete
its rows at any time; the runtime pruning engine may delete any row
only under a named predicate; readers otherwise never delete. Owner
classes: (1) rows whose `owner_tid` is a manager's own TID — its
registration history, heartbeat supersession, unregistration, and a
`superseded` row an operator wrote for it — are owned by that manager.
(2) Managed-service rows a manager writes for the singleton services it
supervises carry the service's `owner_tid`; managers do not delete them.
They are read through the service-owner TTL and are deleted only by the
runtime pruning engine once older than both that TTL and the pruner's
minimum age, together with superseded and surplus history rows.
(3) Rows of the retired v1 service-owner schema are removed by every
reader's mandatory migration sweep ([MANAGER.3]); this is a migration
rule, not custody. (4) Rows carrying the current service-owner schema
tag that fail its field validation are disposable by the runtime
pruning engine once older than its minimum age ([MF-5], [OBS.13.6]);
rows with no recognized schema tag are preserved. Appending records for other TIDs (proactive
supersession per [MA-3], operator replacement) is unaffected. The
pruner's predicate for manager rows is: the owner is stale — a
host-managed identity that is definitively stale, or an externally
supervised handle whose registered probe reports `stale` — and the row
is older than the pruner's minimum age —
applied alike to `active`, `draining`, `stopped`, and `superseded` rows,
including the newest row for every status; a row whose owner's liveness is `unknown` (an inconclusive or
missing probe, or a host identity that cannot be evaluated) is pruned
once it is older than both the minimum age and the external-supervisor
staleness window, without a probe at prune time; a `draining` or
`superseded` row whose owner is live is drain or supersession authority
and is never pruned.
Host-managed identity is tri-state: an observable identity with a
different creation time, or a PID absent from a host-namespace reader,
is definitively stale and is not probed; a PID absent from a
containerized reader, or a recorded identity without a creation time,
is `unknown`; a handle is live if any of its recorded processes is live.
Readers and manager leadership checks omit unknown rows older than
`MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS` without keyed PING.
Younger unknown rows keep the bounded keyed-PING rescue described above;
both the definite-stale and expired-unknown pruning predicates override
keep-newest for every manager status.

Foreground startup may supersede an unconfirmed active canonical
public-request manager in the same scoped manager service only when its
handle has external-supervisor authority and `foreground_serve=True`
metadata. It reduces to the newest row per owner and appends a superseded
owner record; draining and terminal rows are not takeover candidates.
Positive runtime or matching PONG evidence blocks this takeover.
Candidates are considered in ascending TID order: a lower unconfirmed
foreground incumbent is superseded before a higher proved-live manager blocks
startup. A lower proved-live manager blocks without superseding higher rows.
This does not delete peer history or classify unknown identity as dead;
failure to append supersession blocks takeover.

Implementation: `weft/core/manager_runtime.py::_foreground_serve_blocking_manager`.
Correction plan: `2026-09-08-complexity-review-corrections-plan` (retired — source 396c3b85; see `docs/plans/README.md`).

Implementation plan: `2026-08-31-registry-custody-contracts-plan` (retired — source 396c3b85; see `docs/plans/README.md`).

Before an existing `weft.state.services` queue is passed to manager, status,
or Monitor logic, the shared
`discard_v1_service_registry_rows(queue)` bootstrap helper scans it and
exact-deletes only rows whose schema discriminator is
`weft.service_owner.v1`. It does not parse or transform those bodies and never
republishes them with a fresh timestamp. A verification scan must find no v1
row before bootstrap returns. Existing v2 rows and their message IDs are
untouched. An unknown future schema fails bootstrap. Normal service-owner
parsing accepts v2 only. Running managers and services rebuild discarded state
by publishing their own current v2 heartbeats.

Full-TID public submission-custody observation under [MF-5] is a read-only exception to this bootstrap requirement. It may inspect retained service records without deleting v1 rows. It folds the latest valid v2 record per owner for the resolved context's manager service key before canonical filtering; a newer malformed row does not displace a valid one. It uses the resulting canonical records as reserved-route evidence, ignoring malformed and unsupported-schema rows, including future schemas. It does not claim registry compatibility or manager readiness. Broker read failures remain errors. This exception does not change private submission reconciliation, manager/service bootstrap, system status or Monitor bootstrap, including their existing future-schema rejection and legacy-row cleanup.

Implementation mapping: `weft/core/spawn_requests.py::iter_spawn_reserved_routes`
owns read-only latest-valid registry folding and retained route discovery.
`weft/commands/_spawn_submission.py::_reserved_spawn_request_queue_names`
retains private compatibility bootstrap. Coverage:
`tests/commands/test_submission.py` and
`tests/commands/test_submission_observation.py`.

Implementation plan: `2026-10-16-public-submission-custody-observation-plan` (retired — source 0ff8cfbf; see `docs/plans/README.md`) (custody contract promoted 2026-10-19; implemented 2026-10-19; runtime-liveness slice 5 promoted 2026-10-19; implemented 2026-10-19).

Current leadership-view rules:

- `self` means this manager is the lowest live canonical registry claimant
- `other` means a lower-TID canonical manager is positively proved
- `none` means the registry replay succeeded, but no canonical manager is
  positively visible
- `unknown` means runtime state could not be read confidently enough to choose
  among the other outcomes
- these outcomes inform status, manager selection, and voluntary duplicate
  manager yield, but they do not block a manager that has atomically reserved
  public or manager-owned internal spawn work
- a manager may keep supervising already-launched persistent children after a
  lower-TID leader appears
- a manager with non-persistent children that voluntarily yields leadership
  enters leadership drain and revalidates the replacement; if the replacement
  becomes stale or unknown, it may publish `manager_leadership_resumed`, restore
  active ownership, and return to the ordinary manager loop
- a manager treats its own active canonical registry row as live while it has
  not unregistered and is not stopping; host/PONG liveness probes are for other
  processes, and the manager refreshes its own registry row periodically so
  external readers have current evidence

## TID Correlation [MA-2]

Task IDs are the message IDs assigned when spawn requests are written to
`weft.spawn.requests` or `weft.spawn.internal`. The Manager copies that ID into
the expanded TaskSpec `tid`, enabling correlation across registry entries,
task logs, and outbox/control messages.

_Implementation mapping_:
- Manager lifecycle TID generation — `weft/core/manager_runtime.py::generate_tid`
  calls `weft/core/spawn_requests.py::generate_spawn_request_timestamp`;
  manager-owned internal service requests use the message timestamp assigned by
  `weft.spawn.internal`.
- Enqueue with TID as message timestamp —
  `weft/commands/run.py::_enqueue_taskspec` writes the spawn payload at
  `int(task_tid)`.
- Manager-side TID propagation — `weft/core/manager.py` :: `Manager._build_child_spec` (passes `str(timestamp)` as child TID via `resolve_taskspec_payload`).

## Bootstrap and Lifecycle [MA-3]

_Implementation maintenance_: `2026-09-09-load-sensitive-test-lifecycle-fixes-plan` (retired — source 396c3b85; see `docs/plans/README.md`) bounds the detached launcher first-event read and retains child cleanup ownership before registry readiness.

After accepting a spawn request, `weft run` attempts to make a manager
available by minting a manager TaskSpec and launching a short-lived detached
bootstrap helper when required. A readiness-only failure does not revoke the
accepted TID. That
helper starts `weft.manager_process`, then the shared lifecycle code waits for a
stronger startup proof than "registry entry appeared once": the launched manager
PID must still be live and the canonical registry record for the requested
manager TID must exist with that same PID. Detached-launcher success
acknowledgement and startup-log cleanup happen after that proof and are
best-effort diagnostics cleanup, not the startup truth boundary. This keeps
startup proof tied to current manager readiness rather than a fixed sleep
window. Queue-backed parts of that wait use broker-native queue waiting on
`weft.state.services`; only PID/process-exit checks remain as bounded
non-queue fallbacks. Early bootstrap failures surface the detached child exit status and
startup stderr context instead of discarding them. Operators can also manage
managers explicitly via `weft manager start|stop|list|status` and
`weft manager serve`. `weft manager start` is a thin operator wrapper over the
same bootstrap helper as `weft run`; it does not accept an arbitrary manager
TaskSpec. `weft manager serve` uses that same canonical manager TaskSpec but
runs it in the foreground for `systemd`, `launchd`, or `supervisord` style
supervision, forcing `idle_timeout=0.0` for that invocation only. That
foreground command may enable a level-controlled structured operational JSONL
stream on the process log for registry, ownership, service-convergence, spawn,
and TaskMonitor diagnostics. These records are supervisor diagnostics only;
they are not lifecycle truth and are not written to `weft.log.tasks` or any
task-local queue. At `trace`, phase-timing records may expose inclusive reactor,
wait, admission, launch, dispatch, checkpoint, and maintenance spans with
correlation and dispatch-state fields. Nested spans may overlap and carry no
performance threshold; launch-worker measurements cross back as immutable
result data and are published only by the Manager reactor. The canonical
   manager for bootstrap and leadership is the live canonical manager registered
   for `weft.spawn.requests`. Canonical records are the live `role="manager"`
   entries whose `requests` field points at `weft.spawn.requests`; leadership
   chooses the lowest-TID non-superseded live canonical record. Manager records
   bound to another request queue do not participate in default-manager selection.
   A lower-TID live canonical manager that observes a newly published higher-TID
   canonical `active` row after its previous registry watermark treats that
   fresh row as positive writer-liveness evidence for the higher manager. It may
   publish a `superseded` manager service-owner row for that higher TID without
   first sending PING, because the higher manager has just refreshed the same
   registry it must read on its next 1-second leadership pass. This proactive
   supersession applies only to newly observed active rows in the same scoped
   manager service; old rows from initial replay, non-active rows,
   non-canonical rows, lower-TID rows, and unknown/stale proof do not qualify.
   Non-active manager rows are ignored for active-manager election but remain in
   the registry until normal expiry or owner cleanup so the target manager can
   observe its `superseded` row.
   `weft manager start --replace` and `weft manager serve --replace` explicitly
   send STOP to each selected incumbent manager control queue, write a
   `superseded` manager service-owner row for that owner, reselect until no
   active manager remains, and then start or serve the replacement.
   Replacement does not wait for stopped confirmation in v1; ordinary
   `weft manager stop` still waits for stopped registry/process evidence.
Voluntary leadership drain does not kill user children. It waits for already
owned non-persistent children while the lower-TID leader remains positively
proved; if that proof disappears, the manager publishes
`manager_leadership_resumed`, restores active ownership, and resumes serving.
`SIGTERM` and `SIGINT` against a Manager enter the same drain path as STOP.
That drain is a system stop: the Manager stops active user work, then
itself. Internal services are context-level singletons and are not drain
members ([MA-1] item 7).

1. User work. The Manager takes no new intake (a draining Manager launches
   no reserved child) and broadcasts STOP to every tracked child that is not
   an internal service. Internal services are the supervision-only children:
   heartbeat, TaskMonitor, and LivenessMonitor. Every other child is user
   work, including pipelines, autostart children, and persistent tasks; STOP
   is the documented end of a persistent task's lifetime. If a user child
   does not exit after STOP, the Manager sends host SIGTERM after the named
   escalation delay. If the phase's bounded drain window expires, the
   Manager forcefully reaps the remaining user-work process trees. Internal
   services keep running, unsignalled, throughout the drain and after it.
   Phase 1 lasts until no user-work child remains and no child launch that
   was in flight when the drain began is still unsettled, or until its
   window expires. A launch still unsettled at that expiry is logged and
   released from the phase; a child it later commits is terminated by the
   Manager's final cleanup, not by a return to phase 1.
   Phase 1 also covers every live, user-work task in the scoped context.
   The Manager finds these tasks through task-owned snapshots, the
   checkpoints of earlier Managers that are no longer live, and the
   checkpoints of other live Managers, including a superseded Manager still
   draining ([MF-5]). That evidence names the tasks; the authority to stop
   them is the system-STOP rule. A task another live Manager covers is a
   STOP-drain member only: that Manager keeps its custody and its handles
   and reaps it, and the stopping Manager never sends it a host signal.
   For any such task, STOP goes to the `ctrl_in` route resolved from the
   task's snapshot observation; a task whose route cannot be resolved is
   reported and released from the phase, never sent a guessed route. Host
   SIGTERM and forced reap apply only to same-host processes whose
   `(pid, create_time)` identity is verified and that no other live Manager
   covers. Phase 1 ends only when no tracked user-work child and no other
   member with live evidence remains; a member leaves on task-owned
   terminal proof or verified process exit, whichever Manager sent the STOP
   it acted on. A member whose liveness is still unknown when the window
   expires is reported and released from the phase, never treated as
   exited. A system STOP stops the tasks live when it runs; another live
   Manager that is not itself stopped may still restart a service it keeps
   desired or launch new work.
2. The Manager. Once no tracked user-work child remains, apart from any
   child committed by a launch released at the window expiry, the Manager
   detaches any service it launched (the process keeps running; the Manager
   only stops tracking it), publishes its drained terminal event and exits.
   That event is the last lifecycle event the Manager publishes for the stop,
   with two bounded exceptions: a child committed by a launch released at the
   window expiry is terminated by the Manager's final cleanup, which runs after
   the drained event, so that child's own lifecycle events may follow it; and a
   detached internal service publishes its own lifecycle events on its own
   clock, before or after the Manager's. No other child's event does.

A phase with no members completes at once. A draining Manager starts no
service; a service that exits during the drain is relaunched by whichever
live Manager keeps it desired. The Manager applies its own `reserved_policy_on_stop` when
the drain begins; each task applies its own policy when it handles STOP. A
fatal child sentinel observer error is preserved as the cleanup failure, but
it cannot skip the termination and reap sequence: cleanup on that path
attempts user-work termination under one absolute cleanup deadline,
detaches any internal service it launched, then raises the original
failure. Manager
supersession, leadership yield, idle exit, and crash are not system stops
and never signal running user work.
`SIGUSR1` retains immediate kill semantics, with no phases. Caller-facing
manager stop defaults must be materially longer than the drain window plus
its forced-reap deadline, because a Manager may use that whole budget before
publishing its stopped registry record, and slower broker backends can add
observable registry propagation and scheduler delay under release-load
parallelism.

Reader omission alone does not prove manager exit. When a previously observed
row disappears from the filtered view, stop confirmation requires definitive
stale runtime evidence or exit of the caller-owned process, plus the existing
local process exit gate. Missing PID or handle evidence remains unknown.
Explicit stopped-record and foreground-serve proofs retain their documented
semantics.

Missing, invalid or empty runtime identity remains unknown. Expiration
of the reader or pruning age windows does not prove manager exit. Valid
service rows with unknown identity remain subject to the unknown-row
pruning rule in [MA-1], including when the runtime handle is missing.

Signal handlers record requests through `note_termination_signal`. The manager
reactor applies them through its owner-thread transition path. There is no
Manager-specific synchronous signal alias.

_Implementation mapping_:
- `tests/core/test_manager_start_observer_failure.py` verifies cleanup when
  manager-start observation fails.
- Registry liveness reduction —
  `weft/core/manager_runtime.py::manager_registry_record_liveness`; missing
  handles or an empty scoped host-process set yield unknown. Registry readers
  only filter; `weft/core/pruning/runtime.py::_manager_candidates` owns the
  cross-owner stale/unknown age rules and `_service_candidates` owns service
  history expiry.
- Stop confirmation — `weft/core/manager_runtime.py::_await_manager_stop_confirmation`.
- Shared manager lifecycle owner —
  `weft/core/manager_runtime.py::generate_tid`,
  `weft/core/manager_runtime.py::build_manager_spec`,
  `weft/core/manager_runtime.py::select_active_manager`,
  `weft/core/manager_runtime.py::observe_manager_availability`,
  `weft/core/manager_runtime.py::decide_manager_recovery`,
  `weft/core/manager_runtime.py::ensure_manager`,
  `weft/core/manager_runtime.py::start_manager`,
  `weft/core/manager_runtime.py::replace_active_manager`,
  `weft/core/manager_runtime.py::serve_manager_foreground`,
  `weft/core/manager_runtime.py::list_manager_records`,
  `weft/core/manager_runtime.py::manager_diagnostic_records`,
  `weft/core/manager_runtime.py::manager_record`, and
  `weft/core/manager_runtime.py::stop_manager`;
  runtime-specific manager liveness dispatch —
  `weft/liveness/registry.py::runtime_liveness_from_registered_probe`;
  `weft/core/control_probe.py::send_keyed_ping_probe` (owns canonical manager
  bootstrap, explicit replacement, foreground serve, normalized registry
  replay, explicit manager-list diagnostics, extension-provided
  supervised-manager stale checks, bounded manager PONG liveness probes, and
  graceful/forced stop observation).
- Command-side capability surface — `weft/commands/manager.py` and `weft/commands/serve.py` (thin manager-facing commands layered over the shared runtime helper).
- Detached bootstrap launcher — `weft/manager_detached_launcher.py::main` (short-lived wrapper that starts the real manager runtime in a detached session/process-group boundary and reports early launch status back to `weft/core/manager_runtime.py::start_manager`).
- Manager process launch — `weft/core/manager_runtime.py::start_manager` (builds manager TaskSpec, launches the detached wrapper, requires matching pid-plus-registry readiness before success, treats post-proof acknowledgement cleanup as best effort, and reports early bootstrap diagnostics on failure).
- Manager process entry point — `weft/manager_process.py::run_manager_process` and `weft/manager_process.py::main` (shared runtime helper plus standalone module invoked via `python -m weft.manager_process`).
- Leadership election and advisory dispatch view — `weft/core/manager.py` :: `Manager._maybe_yield_leadership`, `Manager._leader_tid`, `Manager._read_active_manager_records`, `Manager._active_manager_records`, `Manager._evaluate_dispatch_ownership`; `weft/helpers/__init__.py::is_canonical_manager_record`; `weft/helpers/__init__.py::canonical_owner_tid` (lowest-TID non-superseded canonical manager wins for status and eventual duplicate-manager convergence, while public spawn dispatch itself is work-stealing by atomic reservation).
- Internal runtime submission envelope — `weft/core/spawn_requests.py`
  `submit_spawn_request` carries internal runtime selectors on the manager-owned
  spawn envelope, and `weft/core/manager.py` `Manager._build_child_spec`
  re-applies them when materializing the child TaskSpec.
- External signal handling —
  `weft/core/tasks/base.py::BaseTask.note_termination_signal` records the
  signal in plain in-memory state (signal handlers must not touch broker
  connections); `weft/core/manager.py::Manager._apply_termination_request`
  applies it on the next `process_once` turn (TERM/INT drain, SIGUSR1 kill).
- System STOP phase ordering — `weft/core/manager.py` ::
  `Manager._begin_shutdown_drain` (enters phase 1 and signals user work),
  `Manager._continue_shutdown_drain` and `Manager._drain_user_work_phase_holds`
  (phase 1, the in-flight-launch hold bounded by the drain window, and
  the drained event once no user-work child remains),
  `Manager._drain_phase_children` (the drain's members, from the one
  snapshot pass plus this Manager's user-work children),
  `Manager._detach_launched_services` (services dropped from tracking,
  never signalled, at the drained event and at final cleanup), `Manager._signal_children_to_stop`
  (the one STOP-then-SIGTERM rung), `Manager._terminate_children`
  (the forced-reap rung, limited to the drain's TIDs), and
  `Manager._manager_drain_timeouts` (drain clocks only). Error-path
  order — `Manager._cleanup_task_resources` (launched services detached,
  then user work only, under one deadline). Tests:
  `tests/core/test_manager.py::test_manager_stop_drains_user_work_then_manager_leaving_services`,
  `tests/core/test_manager.py::test_manager_sentinel_failure_during_shutdown_still_reaps_children`,
  `tests/core/test_manager.py::test_manager_stop_does_not_hold_past_window_for_stuck_launch`.
- System STOP coverage beyond the child table (Reading B) — `weft/core/manager.py` ::
  `Manager._collect_drain_stop_targets` (every live user-work task named by a
  task-owned snapshot in one fresh snapshot pass, whichever Manager launched it
  and whether that Manager is alive, plus this Manager's residents; residents
  other live Managers cover are marked covered for escalation only; route,
  service-role, terminal and host-escalation filters), `Manager._signal_children_to_stop`
  (the first STOP rung as one exact-name broadcast with `create_missing`, [SB-BCAST-2], [SB-BCAST-4]),
  `Manager._manager_checkpoint_residents`
  (the one `covered` rule shared with `Manager._bootstrap_scheduler_evidence`),
  `Manager._settle_drain_stop_targets` (release on task-owned terminal proof
  or verified exit; an unknown identity probe keeps the target),
  `Manager._drain_stop_terminal_events` (the targets' terminal envelopes in
  `weft.log.tasks`, peeked past a cursor that starts just before the oldest
  target's retained snapshot and advances only after a complete read),
  `Manager._release_drain_stop_targets_at_expiry` (forced reap of eligible
  targets under phase 1's one forced-reap deadline, report and release of
  the rest),
  `Manager._drain_phase_members` (escalation clocks), and
  `Manager._drain_stop_target_queues` / `Manager._has_pending_drain_stop_observation`
  (wake interest on each target's snapshot queue and on `weft.log.tasks`). Tests:
  `tests/core/test_manager.py::test_manager_stop_skips_terminal_and_internal_adopted_snapshots`,
  `tests/core/test_manager.py::test_manager_stop_keeps_target_whose_identity_probe_fails`,
  `tests/core/test_manager.py::test_manager_stop_releases_target_on_terminal_log_event`,
  `tests/core/test_manager.py::test_manager_stop_releases_target_on_terminal_event_before_collection`,
  `tests/cli/test_cli_manager.py::test_manager_stop_reaches_earlier_manager_detached_task`,
  `tests/cli/test_cli_manager.py::test_manager_stop_reaches_task_under_another_live_manager`,
  `tests/cli/test_cli_manager.py::test_manager_stop_leaves_internal_services_live`.
- CLI management — `weft/commands/manager.py` :: `cmd_manager_start`,
  `cmd_manager_stop`, `cmd_manager_list`, `cmd_manager_status`; these structured
  commands are thin wrappers over the shared lifecycle helper.
  `weft/commands/system.py` :: `_collect_manager_records` reuses the same
  lifecycle reader for manager views.
- Foreground supervision command and process-log diagnostics — `weft/commands/serve.py` :: `cmd_manager_serve` and `_serve_manager_context` (preserves explicit client broker/config), registered in `weft/cli/app.py` as `weft manager serve`; structured process-log emission lives in `weft/core/serve_log.py`, `weft/core/manager.py`, and `weft/core/monitor/task_monitor.py`.

Manager queue delivery may reuse child cleanup already performed at that turn's
maintenance boundary. The work handler omits its duplicate sweep only within an
explicit reactor-owned scope covering ordinary queue delivery; standalone handler
calls, service convergence and shutdown retain their cleanup. The scope is cleared
on failure. Before each spawn delivery in the scope, including the first, a ready
child-sentinel observation or a due child sentinel/post-exit maintenance deadline
yields to immediate normal settlement. Child-cleanup lifecycle commits do not run
inside this ordinary delivery scope. Reusing this child cleanup does not change
the normal phases or deadlines of other maintenance owners or make a serviced
classification-page continuation a veto on the first eligible dispatch.

See the `2026-10-22-bounded-concurrent-manager-launches` (retired — source 0ff8cfbf; see `docs/plans/README.md`),
B promotion against baseline `8123b129`.

_Implementation mapping_: `weft/core/manager.py::Manager._process_manager_reactor_turn`
owns the ordinary delivery scope and clears it with `try/finally`;
`Manager._process_queue_message` checks the existing child-sentinel observation
and `Manager._manager_child_timeouts` before reservation;
`Manager._handle_work_message` retains cleanup outside that scope.

## Scope Boundary [MA-4]

The current manager architecture favors one canonical manager per context and a
shared child-task launch path. Specialized pools or richer orchestration layers
are outside the current contract and are tracked in the companion doc:

- [`03A-Manager_Architecture_Planned.md`](03A-Manager_Architecture_Planned.md)

_Implementation mapping_: `weft/core/manager.py`
`Manager._resolve_child_task_class()` currently routes to `Consumer`,
`PipelineTask`, `PipelineEdgeTask`, or `HeartbeatTask` via the manager-owned
internal runtime envelope. Public submission surfaces do not authorize
internal runtime class selection through stored TaskSpec metadata alone.
Runner extensibility affects execution backends, not manager specialization.

Implementation maintenance: `2026-08-31-dead-generation-retirement-plan` (retired — source 396c3b85; see `docs/plans/README.md`) retains the shared lifecycle owner behind typed manager command and client adapters.
