# Manager Architecture: The Recursive Model

This document describes how Weft realizes the “managers are tasks” concept. A
Manager is implemented as a long-running `BaseTask` that consumes spawn requests
from SimpleBroker queues, launches child tasks, and reports lifecycle events.
The CLI component that submits work is referred to as the **Client**.

**Terminology**
- **Task**: Any executable unit described by a TaskSpec.
- **Consumer**: The default task executor for command/function targets.
- **Manager**: A long-lived task that spawns child tasks and manages autostart.
- **Manager (CLI)**: The CLI namespace for managing Managers (not a distinct runtime).

_Implementation status_: `Manager` (`weft/core/manager.py`), the spawn registry
queue, and the CLI wrappers (`weft manager …`, `weft run`) are fully
implemented. The Client (`weft/cli/run.py`) builds TaskSpec templates,
enqueues them on `weft.spawn.requests`, and optionally waits for completion by
tailing task logs/outboxes. The spawn-request message ID becomes the task TID.
Managers consume those requests, expand the TaskSpec, launch child Consumers via
`launch_task_process`, and emit `weft.log.tasks` events just like any other task.
Once that spawn request is written, submission is durable user intent; later
CLI failure handling must reconcile by TID instead of assuming the enqueue can
always be rolled back from the public request queue.

## Related Plans

- [Config Precedence and Parsing Alignment Plan](../plans/2026-04-14-config-precedence-and-parsing-alignment-plan.md) – align broker target precedence, Weft env parsing, and manager-timeout config semantics with the documented contract.
- [Spawn Request Reconciliation Plan](../plans/2026-04-14-spawn-request-reconciliation-plan.md) – tighten queue-first submission error handling so post-enqueue failures reconcile by TID instead of assuming rollback, and keep late bootstrap acknowledgement failures from downgrading successful startup.
- [Manager Bootstrap Unification Plan](../plans/2026-04-09-manager-bootstrap-unification-plan.md) – Collapse `weft manager start` onto the canonical manager bootstrap path used by `weft run`.
- [Manager Lifecycle Command Consolidation Plan](../plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md) – Collapse remaining `manager list|status|stop` and `weft status` manager lifecycle reads onto the same shared control-plane helper as bootstrap.
- [Weft Serve Supervised Manager Plan](../plans/2026-04-09-weft-serve-supervised-manager-plan.md) – Add a minimal foreground `weft manager serve` command for supervisor-managed persistent managers and align manager TERM/INT with graceful drain.
- [Detached Manager Bootstrap Hardening Plan](../plans/2026-04-13-detached-manager-bootstrap-hardening-plan.md) – Replace parent-dependent detached bootstrap with a real detached-launch wrapper, stronger startup proof, and actionable early-failure diagnostics.
- [Manager Bootstrap Readiness And Cleanup Test Plan](../plans/2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md) – Replace the fixed startup delay with event-based readiness proof and split cleanup-vs-startup stress coverage.
- [Autostart Hardening And Contract Alignment Plan](../plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md) – Make project-level autostart intent durable, fix manager-side enqueue bookkeeping, and align manifest policy docs with enforced behavior.
- [Pipeline Autostart Extension Plan](../plans/2026-04-16-pipeline-autostart-extension-plan.md) – Extend autostart manifests so stored pipeline targets compile and launch through the ordinary first-class pipeline runtime.
- [Canonical Owner Fence Plan](../plans/2026-04-17-canonical-owner-fence-plan.md) – Add a shared canonical-owner reduction helper and harden manager child dispatch with a final ownership fence before launch.
- [Heartbeat Service Plan](../plans/2026-04-17-heartbeat-service-plan.md) – Add the built-in runtime heartbeat service, reserve its internal endpoint namespace, and reuse the canonical-owner fence in a long-lived interval emitter.
- [Result Evidence And Superseded Manager Reconciliation Plan](../plans/2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md) – Keep task-list/status read models aligned with selected active-manager registry truth.
- [Manager Selection PING/PONG Liveness Plan](../plans/2026-05-07-manager-selection-ping-pong-liveness-plan.md) – Use keyed manager PONGs as bounded positive liveness evidence when startup or selection encounters stale-looking manager registry rows.
- [Persistent Agent Runtime Implementation Plan](../plans/2026-04-06-persistent-agent-runtime-implementation-plan.md) – references Manager Architecture for long-lived agent sessions.
- [TaskSpec Clean Design Plan](../plans/2026-04-06-taskspec-clean-design-plan.md) – references Manager Architecture for TaskSpec schema alignment.
- [Phase 7 Task Monitor Supervision And Cleanup Plan](../plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md) – proposes manager-supervised `TaskMonitor` lifecycle and safe autonomous cleanup slices.
- [Manager-Owned Internal Service Supervision Plan](../plans/2026-05-08-manager-owned-internal-service-supervision-plan.md) – unify manager-owned `once`/`ensure` reconciliation for heartbeat, TaskMonitor, and autostarts.
- [Phase 7 Manager Service Reconciler Cleanup Plan](../plans/2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md) – closes the single-reconciler implementation gaps for heartbeat, TaskMonitor, and autostart lifecycle handling.
- [Deterministic Manager Service Reconciler Plan](../plans/2026-05-08-deterministic-manager-service-reconciler-plan.md) – supersedes the cleanup plan for the pure reducer, transition-table, and full test support work.
- [Manager Stop Timeout Hardening Plan](../plans/2026-05-09-manager-stop-timeout-hardening-plan.md) – separates the manager's internal child-drain timeout from caller-side stop confirmation defaults so slower backends have observation margin.
- [Internal Spawn Priority Queue Plan](../plans/2026-05-09-internal-spawn-priority-queue-plan.md) – adds `weft.spawn.internal` as strict-priority manager-owned service spawn work while preserving the shared manager launch path.
- [Runtime Liveness Probe Registry Plan](../plans/2026-05-09-runtime-liveness-probe-registry-plan.md) – draft plan to move runtime-specific manager liveness checks behind a lightweight core registry with Docker-specific probing owned by `weft_docker`.
- [Prune Path Unification Plan](../plans/2026-05-09-prune-path-unification-plan.md) – draft plan to make CLI pruning and manager-supervised TaskMonitor cleanup share one core prune engine.
- [Service Liveness And Health Convergence Plan](../plans/2026-05-09-service-liveness-and-health-convergence-plan.md) – draft plan to make manager ownership, heartbeat supersession, singleton restart timing, and status liveness diagnostics share one interpretation path.
- [Manager Service Authority Boundary Hardening Plan](../plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md) – tighten manager-owned singleton evidence authority, duplicate PID force-kill authority, and service-key helper structure.
- [Manager Work-Stealing Dispatch Plan](../plans/2026-05-11-manager-work-stealing-dispatch-plan.md) – draft plan to make atomic spawn-request reservation the public dispatch authority while keeping singleton service correctness in the manager-owned reducer.
- [Manager Serve Operational Log Plan](../plans/2026-05-11-manager-serve-operational-log-plan.md) – draft plan for level-controlled foreground `manager serve` operational logs that expose registry, loop, service-convergence, spawn, and TaskMonitor decisions without writing lifecycle state.
- [Manager Replace Start And Serve Plan](../plans/2026-05-13-manager-replace-start-serve-plan.md) – completed plan for explicit `weft manager start --replace` and `weft manager serve --replace` operator replacement semantics using the existing STOP control path.
- [Manager Liveness And Leadership Robustness Plan](../plans/2026-05-13-manager-liveness-and-leadership-robustness-plan.md) – completed plan requiring strong live dispatch-authority proof before leadership yield, keeping external-supervisor `unknown` evidence from acting as authority, and bounding leadership-drain recovery without broad status scans.
- [Internal State Machine Helper Plan](../plans/2026-05-13-internal-state-machine-helper-plan.md) – draft plan for a reusable pure reducer helper that can express manager-service and other transition tables without moving side effects out of their current owners.
- [Internal Service Observability Plan](../plans/2026-05-11-internal-service-observability-plan.md) – adds an ops read model that reports heartbeat and TaskMonitor state from manager launch evidence, child task logs, TID mappings, and internal spawn queues.

## Conceptual Model: Everything is a Task [MA-0]

- **No special cases** – Managers inherit from `BaseTask`, so they use the same
  queue wiring, process-title formatting, and control semantics as regular
  tasks.
- **Uniform observability** – Managers publish manager service-owner rows in
  `weft.state.services`, participate in the task log, and respond on the
  control channel (`STOP`, `PING`, `STATUS`).
- **Self-hosting** – Managers spawn child Consumers by writing back into the
  same SimpleBroker database they are monitoring.

_Implementation mapping_:
- `weft/core/manager.py` — `Manager(BaseTask)` inherits queue wiring and control semantics.
- `weft/core/tasks/base.py` — `BaseTask` provides queue wiring, process-title formatting (`_update_process_title`), control message handling (`_handle_control_message`, `_handle_control_command`), and state reporting (`_report_state_change`).

## Manager Behaviour [MA-1]

Key responsibilities implemented in `weft/core/manager.py`:

1. **Spawn queue consumption** – Managers bind their public inbox to
   `weft.spawn.requests`, and canonical public managers also monitor
   `weft.spawn.internal` for manager-owned internal service spawn requests.
   Internal spawn work has strict priority: when internal and public work are
   both pending, the manager drains internal spawn work before public work.
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
   A live public manager advances public backlog with a bounded per-turn direct
   reservation drain after normal watcher scheduling. The drain treats
   `has_pending()` and backend activity waiters as hints only; an atomic
   reservation attempt is the progress authority, so missed or stale pending
   hints cannot strand accepted public spawn work under load.
   Pipeline-owned runtime child bootstrap is also eligible for the internal
   spawn lane once the top-level pipeline task has already been accepted from
   public dispatch; those child requests are implementation work for that
   accepted task, not new public backlog.
   Pending internal spawn work is manager-owned convergence work: it forces the
   next scheduling drain to probe inactive queues so convergence does not
   depend on the periodic broad-probe interval. Internal spawn requests are
   advanced with a bounded per-turn drain without consuming ordinary public
   spawn work. Successful child launch emits
   durable `task_spawned` evidence with the child TID, child TaskSpec, and
   child PID so manager-owned singleton convergence can observe a live
   replacement before the child has completed its own initialization log writes.
2. **Child process launch** – Validated TaskSpecs are executed via
   `launch_task_process(Consumer, …)`, preserving the standard isolation model
   (a dedicated subprocess per child task).
3. **Initial payload delivery** – If the spawn payload includes
   `inbox_message`, the manager writes it into the child’s inbox queue before the
   child starts processing.
4. **Registry heartbeat** – Managers publish an `active` service-owner record to
   `weft.state.services` on startup (including queue names, PID, role, and
   capabilities), refresh that active record periodically while healthy, and
   replace it with a `stopped` record during shutdown. Operator replacement may
   replace it with a `superseded` record after sending STOP. The active record
   refresh path must treat a latest self-owned `superseded` row as a shutdown
   signal and must not publish a newer `active` row over it. The active record
   is pruned when the manager exits cleanly. The registry is read as a live
   queue: callers reduce to the latest relevant record per service key and
   owner TID, prune dead or expired active records, and then filter to
   canonical non-superseded live managers before treating the result as the
   active-manager view. Host-managed records use scoped host
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
   replaying the full service registry every loop turn. When startup or manager
   selection sees an otherwise canonical active manager row that lacks strong
   runtime proof, it may send one bounded keyed PING to that manager's
   task-local control queue. A matched PONG from the same TID can rescue the row
   as positive liveness evidence for selection only when its manager-selection
   fields prove dispatch eligibility: manager role, `weft.spawn.requests`,
   matching control queues, matching context when present, non-terminal
   `task_status`, and `should_stop` not true. An absent, malformed,
   non-matching, draining, or stopping PONG is not negative proof and does not
   authorize unsafe takeover by itself. Canonical ownership is lowest-live-TID
   among canonical dispatch-eligible claimants for status, selection, and
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
   cached `Queue.last_ts` read.
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
7. **Managed services** – The manager reconciles autostarts, heartbeat, and
   `TaskMonitorTask` through one deterministic manager-owned service path. The
   live public managers may supervise built-in internal singleton services;
   correctness is enforced by manager-owned metadata and the singleton reducer,
   not by a global dispatch-owner fence. Scoped managers may still reconcile
   their own autostart manifests. The built-in heartbeat service is desired
   only when an enabled internal dependent needs it. The internal
   `TaskMonitorTask` is an `ensure` service when `WEFT_TASK_MONITOR_ENABLED` is
   true. Draining or stopped managers do not start or restart singleton
   services. Live service ownership can be proved by a tracked child, a live
   runtime handle, including the task-process host handle published in TID
   mappings, manager-authored internal runtime envelope/class evidence,
   manager-authored autostart metadata, or a matched keyed `PING`/`PONG`;
   caller-owned TaskSpec metadata alone is not service authority. Task-owned
   terminal proof for that TID (terminal envelope or terminal task-log status)
   wins over host process liveness, so a service process still unwinding after
   `control_kill` is not a live singleton owner. Stale non-terminal task-log
   rows without live proof do not count as a live singleton. The
   manager enqueues services through its own inbox using manager-owned internal
   runtime envelopes, tracks these children as supervision rather than user
   work, and applies bounded restart backoff after TaskMonitor exit. The
   manager reduces pending-spawn, live, terminal, and uncertain evidence
   through one pure transition table before applying queue or process side
   effects. Each manager turn advances service convergence with bounded passes
   over child reap, service reconciliation, and manager-owned internal spawn
   drain; pending internal spawn work bypasses the stable-service throttle
   because it is accepted-task work, not background audit work. Backend
   activity waiters are hints, not the clock that drives service supervision.
   Recent nonterminal service rows without live proof are
   uncertain evidence, not terminal proof, so short probe misses cannot clear a
   freshly launched singleton and start duplicates. If multiple live owners
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
   terminal, and TID-mapping evidence wins over that manager launch hint.
8. **Control channel** – In addition to STOP/STATUS, managers inherit the
  task control contract: `PING` replies with `PONG` plus a live task-local
  status snapshot on `ctrl_out`, echoing `request_id` for structured PING
  envelopes. Manager PONG snapshots also include manager-selection fields
  (`role`, `inbox`/`requests`, `ctrl_in`, `ctrl_out`, `outbox`, and
  `weft_context` when available) so external selection code can validate the
  responding task without importing command-layer helpers.

_Implementation mapping_:
- [MA-1.1] Spawn queue consumption — `Manager._build_queue_configs`, `Manager._handle_work_message`, `Manager._build_child_spec`, `Manager._drain_public_spawn_requests`, `Manager._drain_spawn_requests_from_queue`.
- [MA-1.2] Child process launch — `Manager._launch_child_task`, `launch_task_process` (`weft/core/launcher.py`).
- [MA-1.3] Initial payload delivery — `Manager._launch_child_task` (inbox seeding block).
- [MA-1.4] Registry heartbeat and leadership view — `weft/core/service_convergence.py`, `Manager._register_manager`, `Manager._unregister_manager`, `Manager._atexit_unregister`, `Manager._read_active_manager_records`, `Manager._active_manager_records`, `Manager._leader_tid`, `Manager._evaluate_dispatch_ownership`, `Manager._maybe_yield_leadership`, `weft/commands/system.py::_collect_task_snapshot_records`, plus manager-record projection in `weft/core/manager_runtime.py`.
- [MA-1.5] Idle timeout — `Manager.process_once` (idle-timeout check), `Manager._read_broker_timestamp`, `Manager._update_idle_activity_from_broker`.
- [MA-1.6] Autostart manifests — `Manager._reconcile_managed_services`, `Manager._tick_autostart`, `Manager._desired_autostart_services`, `Manager._mark_autostart_enqueued`, `Manager._prune_autostart_state`, `Manager._build_autostart_spawn_payload`, `Manager._load_autostart_manifest`, `Manager._load_autostart_taskspec`, `Manager._load_autostart_pipeline`, `Manager._active_autostart_sources`, `Manager._cleanup_children`, plus `weft/core/pipelines.py::compile_linear_pipeline` for stored pipeline targets. Autostarts share `weft/core/manager_services.py` metadata/state primitives and `reduce_managed_service_state` transition selection with built-in singleton services.
- [MA-1.6a] Managed internal service supervision — `Manager._run_managed_service_convergence`, `Manager._reconcile_managed_services`, `Manager._tick_internal_services`, `Manager._tick_managed_service`, `Manager._service_supervision_allowed`, `Manager._build_heartbeat_spawn_payload`, `Manager._build_task_monitor_spawn_payload`, `Manager._pending_service_keys`, `Manager._trusted_service_key_from_metadata`, `Manager._service_key_for_child`, `Manager._observed_service_candidates_by_key`, `Manager._service_candidate_from_task_log`, `Manager._candidate_force_kill_pids`, `Manager._runtime_handle_force_kill_pids`, `Manager._enqueue_managed_service_request`, `Manager._drain_internal_spawn_requests`, `Manager._cleanup_children`, `Manager.wait_for_activity`, and `Manager._user_work_children`; public submission sanitization lives in `weft/core/spawn_requests.py::submit_spawn_request`; shared service models and `reduce_managed_service_state` live in `weft/core/manager_services.py`, runtime TaskMonitor behavior lives in `weft/core/tasks/task_monitor.py`, processor contracts live in `weft/core/task_monitoring.py`, and ops service status reduction lives in `weft/commands/system.py::_collect_internal_service_snapshots`. Implementation plans: [`2026-05-10-control-and-service-convergence-state-machine-plan.md`](../plans/2026-05-10-control-and-service-convergence-state-machine-plan.md), [`2026-05-11-internal-service-observability-plan.md`](../plans/2026-05-11-internal-service-observability-plan.md); hardening plan: [`2026-05-10-manager-service-authority-boundary-hardening-plan.md`](../plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md).
- [MA-1.7] Control channel — inherited from `BaseTask._handle_control_command` (`weft/core/tasks/base.py`) and extended by `Manager._control_snapshot_fields` (`weft/core/manager.py`); structured PING/PONG snapshots, STOP, STATUS, KILL handling.

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
- Public TID generation — `weft/cli/run.py` :: `_generate_tid` (uses `Queue.generate_timestamp` on the spawn-requests queue); manager-owned internal service requests use the message timestamp assigned by `weft.spawn.internal`.
- Enqueue with TID as message timestamp — `weft/cli/run.py` :: `_enqueue_taskspec` (writes spawn payload at `int(task_tid)`).
- Manager-side TID propagation — `weft/core/manager.py` :: `Manager._build_child_spec` (passes `str(timestamp)` as child TID via `resolve_taskspec_payload`).

## Bootstrap and Lifecycle [MA-3]

`weft run` guarantees that a manager is available by minting a manager TaskSpec
and launching a short-lived detached bootstrap helper when required. That
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
task-local queue. The canonical
manager for bootstrap and leadership is the live canonical manager registered
for `weft.spawn.requests`. Canonical records are the live `role="manager"`
entries whose `requests` field points at `weft.spawn.requests`; leadership
chooses the lowest-TID non-superseded live canonical record. Manager records
bound to another request queue do not participate in default-manager selection.
`weft manager start --replace` and `weft manager serve --replace` explicitly
send STOP to each selected incumbent manager control queue, write a
`superseded` manager service-owner row for that owner, reselect until no active
manager remains, and then start or serve the replacement. Replacement does not
wait for stopped confirmation in v1; ordinary `weft manager stop` still waits
for stopped registry/process evidence. External
Voluntary leadership drain does not kill user children. It waits for already
owned non-persistent children while the lower-TID leader remains positively
proved; if that proof disappears, the manager publishes
`manager_leadership_resumed`, restores active ownership, and resumes serving.
`SIGTERM` and `SIGINT` against a Manager enter the same drain path as STOP. A
manager drain has a bounded child-exit window; if child tasks do not exit after
STOP is broadcast and the drain window expires, the Manager forcefully reaps
tracked child process trees before publishing its drained terminal event.
`SIGUSR1` retains immediate kill semantics. Caller-facing manager stop defaults
must be materially longer than the internal manager drain window, because a
Manager may use the full drain budget before publishing its stopped registry
record, and slower broker backends can add observable registry propagation and
scheduler delay under release-load parallelism.

_Implementation mapping_:
- Shared manager lifecycle owner — `weft/core/manager_runtime.py` :: `_build_manager_runtime_invocation`, `_select_active_manager`, `_ensure_manager`, `_start_manager`, `_replace_active_manager`, `_serve_manager_foreground`, `_list_manager_records`, `_manager_record`, `_stop_manager`; `weft/runtime_liveness.py` :: `runtime_liveness_from_registered_probe`; plus `weft/core/control_probe.py` :: `send_keyed_ping_probe` (owns canonical manager bootstrap, explicit replacement, foreground serve, normalized registry replay, extension-provided supervised-manager stale checks, bounded manager PONG liveness probes, and graceful/forced stop observation).
- Command-side capability surface — `weft/commands/manager.py` and `weft/commands/serve.py` (thin manager-facing commands layered over the shared runtime helper).
- Detached bootstrap launcher — `weft/manager_detached_launcher.py` :: `main` (short-lived wrapper that starts the real manager runtime in a detached session/process-group boundary and reports early launch status back to `_start_manager`).
- Manager process launch — `weft/core/manager_runtime.py` :: `_start_manager` (builds manager TaskSpec, launches the detached wrapper, requires matching pid-plus-registry readiness before success, treats post-proof acknowledgement cleanup as best effort, and reports early bootstrap diagnostics on failure).
- Manager process entry point — `weft/manager_process.py` :: `run_manager_process`, `main` (shared runtime helper plus standalone module invoked via `python -m weft.manager_process`).
- Leadership election and advisory dispatch view — `weft/core/manager.py` :: `Manager._maybe_yield_leadership`, `Manager._leader_tid`, `Manager._read_active_manager_records`, `Manager._active_manager_records`, `Manager._evaluate_dispatch_ownership`; `weft/helpers.py::is_canonical_manager_record`; `weft/helpers.py::canonical_owner_tid` (lowest-TID non-superseded canonical manager wins for status and eventual duplicate-manager convergence, while public spawn dispatch itself is work-stealing by atomic reservation).
- Internal runtime submission envelope — `weft/core/spawn_requests.py`
  `submit_spawn_request` carries internal runtime selectors on the manager-owned
  spawn envelope, and `weft/core/manager.py` `Manager._build_child_spec`
  re-applies them when materializing the child TaskSpec.
- External signal handling — `weft/core/manager.py` :: `Manager.handle_termination_signal` (TERM/INT drain, SIGUSR1 kill).
- CLI management — `weft/commands/manager.py` :: `start_command`, `stop_command`, `list_command`, `status_command`; these commands are thin wrappers over the shared lifecycle helper. `weft/commands/status.py` :: `_collect_manager_records` reuses the same lifecycle reader for manager views.
- Foreground supervision command and process-log diagnostics — `weft/commands/serve.py` :: `serve_command`, registered in `weft/cli/app.py` as `weft manager serve`; structured process-log emission lives in `weft/core/serve_log.py`, `weft/core/manager.py`, and `weft/core/tasks/task_monitor.py`.

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
