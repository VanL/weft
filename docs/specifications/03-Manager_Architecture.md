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
- [Task Lifecycle Stop/Drain Audit Plan](../plans/2026-04-08-task-lifecycle-stop-drain-audit-plan.md) – Audit manager drain, registry semantics, and lifecycle ownership before changing worker behavior.
- [Manager Bootstrap Unification Plan](../plans/2026-04-09-manager-bootstrap-unification-plan.md) – Collapse `weft manager start` onto the canonical manager bootstrap path used by `weft run`.
- [Manager Lifecycle Command Consolidation Plan](../plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md) – Collapse remaining `manager list|status|stop` and `weft status` manager lifecycle reads onto the same shared control-plane helper as bootstrap.
- [Weft Serve Supervised Manager Plan](../plans/2026-04-09-weft-serve-supervised-manager-plan.md) – Add a minimal foreground `weft manager serve` command for supervisor-managed persistent managers and align manager TERM/INT with graceful drain.
- [Detached Manager Bootstrap Hardening Plan](../plans/2026-04-13-detached-manager-bootstrap-hardening-plan.md) – Replace parent-dependent detached bootstrap with a real detached-launch wrapper, stronger startup proof, and actionable early-failure diagnostics.
- [Manager Bootstrap Readiness And Cleanup Test Plan](../plans/2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md) – Replace the fixed startup delay with event-based readiness proof and split cleanup-vs-startup stress coverage.
- [Weft Road To Excellent Plan](../plans/2026-04-14-weft-road-to-excellent-plan.md) – Top-level roadmap for making manager bootstrap boring under contention and aligning the rest of the system to the same reliability bar.
- [Autostart Hardening And Contract Alignment Plan](../plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md) – Make project-level autostart intent durable, fix manager-side enqueue bookkeeping, and align manifest policy docs with enforced behavior.
- [Pipeline Autostart Extension Plan](../plans/2026-04-16-pipeline-autostart-extension-plan.md) – Extend autostart manifests so stored pipeline targets compile and launch through the ordinary first-class pipeline runtime.
- [Review Findings Remediation Plan](../plans/2026-04-16-review-findings-remediation-plan.md) – Fix forced broker-activity freshness, autostart-state pruning, and nearby contract ambiguity surfaced by the deep-read review.
- [Canonical Owner Fence Plan](../plans/2026-04-17-canonical-owner-fence-plan.md) – Add a shared canonical-owner reduction helper and harden manager child dispatch with a final ownership fence before launch.
- [Heartbeat Service Plan](../plans/2026-04-17-heartbeat-service-plan.md) – Add the built-in runtime heartbeat service, reserve its internal endpoint namespace, and reuse the canonical-owner fence in a long-lived interval emitter.
- [Agent Runtime Implementation Plan](../plans/2026-04-06-agent-runtime-implementation-plan.md) – references `MA-2` TID correlation.
- [Persistent Agent Runtime Implementation Plan](../plans/2026-04-06-persistent-agent-runtime-implementation-plan.md) – references Manager Architecture for long-lived agent sessions.
- [TaskSpec Clean Design Plan](../plans/2026-04-06-taskspec-clean-design-plan.md) – references Manager Architecture for TaskSpec schema alignment.

## Conceptual Model: Everything is a Task [MA-0]

- **No special cases** – Managers inherit from `BaseTask`, so they use the same
  queue wiring, process-title formatting, and control semantics as regular
  tasks.
- **Uniform observability** – Managers register themselves in
  `weft.state.managers`, participate in the task log, and respond on the
  control channel (`STOP`, `PING`, `STATUS`).
- **Self-hosting** – Managers spawn child Consumers by writing back into the
  same SimpleBroker database they are monitoring.

_Implementation mapping_:
- `weft/core/manager.py` — `Manager(BaseTask)` inherits queue wiring and control semantics.
- `weft/core/tasks/base.py` — `BaseTask` provides queue wiring, process-title formatting (`_update_process_title`), control message handling (`_handle_control_message`, `_handle_control_command`), and state reporting (`_report_state_change`).

## Manager Behaviour [MA-1]

Key responsibilities implemented in `weft/core/manager.py`:

1. **Spawn queue consumption** – A manager’s inbox is bound to
   `weft.spawn.requests`. Each message contains a serialized child TaskSpec and
   an optional `inbox_message`. Messages are reserved, parsed, and acknowledged
   via the `BaseTask` helpers. The manager performs a final dispatch-ownership
   check immediately before child launch side effects. Only positive `self`
   ownership preserves the normal launch path.
2. **Child process launch** – Validated TaskSpecs are executed via
   `launch_task_process(Consumer, …)`, preserving the standard isolation model
   (a dedicated subprocess per child task).
3. **Initial payload delivery** – If the spawn payload includes
   `inbox_message`, the manager writes it into the child’s inbox queue before the
   child starts processing.
4. **Registry heartbeat** – Managers publish an `active` record to
   `weft.state.managers` on startup (including queue names, PID, role, and
   capabilities) and replace it with a `stopped` record during shutdown. The
   active record is pruned when the manager exits cleanly. The registry is
   read as a live queue: callers reduce to the latest relevant record per TID,
   prune dead active records, and then filter to canonical live managers
   before treating the result as the active-manager view. Canonical ownership
   is lowest-live-TID among canonical claimants for `weft.spawn.requests`.
   Manager dispatch keeps four runtime-owned outcomes distinct:
   `self`, `other`, `none`, and `unknown`.
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
   manager spawn queue (`weft.spawn.requests`). Manifest lifecycle policy
   (`once`/`ensure`) governs restarts while the manager is running. Launch and
   restart accounting advances only after a spawn request is successfully
   written. Ensure-mode manifests are reconsidered immediately after a tracked
   autostart child exits. Scan bookkeeping for deleted manifest paths is
   pruned during refresh so long-lived managers do not accumulate stale
   manifest state indefinitely. Pipeline targets are compiled into the same
   first-class pipeline task used by `weft run --pipeline` before the manager
   enqueues the compiled top-level pipeline task on the spawn queue.
7. **Control channel** – In addition to STOP/STATUS, managers reply `PONG` to
  `PING` messages on `ctrl_out`, supporting simple health checks.

_Implementation mapping_:
- [MA-1.1] Spawn queue consumption — `Manager._handle_work_message`, `Manager._build_child_spec`.
- [MA-1.2] Child process launch — `Manager._launch_child_task`, `launch_task_process` (`weft/core/launcher.py`).
- [MA-1.3] Initial payload delivery — `Manager._launch_child_task` (inbox seeding block).
- [MA-1.4] Registry heartbeat and leadership view — `Manager._register_manager`, `Manager._unregister_manager`, `Manager._atexit_unregister`, `Manager._read_active_manager_records`, `Manager._active_manager_records`, `Manager._leader_tid`, `Manager._evaluate_dispatch_ownership`, `Manager._refresh_dispatch_suspension`, `Manager._maybe_yield_leadership`, plus `weft/helpers.py::is_canonical_manager_record` and `weft/helpers.py::canonical_owner_tid`.
- [MA-1.5] Idle timeout — `Manager.process_once` (idle-timeout check), `Manager._read_broker_timestamp`, `Manager._update_idle_activity_from_broker`.
- [MA-1.6] Autostart manifests — `Manager._tick_autostart`, `Manager._prune_autostart_state`, `Manager._build_autostart_spawn_payload`, `Manager._load_autostart_manifest`, `Manager._load_autostart_taskspec`, `Manager._load_autostart_pipeline`, `Manager._active_autostart_sources`, `Manager._enqueue_autostart_request`, `Manager._cleanup_children`, plus `weft/core/pipelines.py::compile_linear_pipeline` for stored pipeline targets.
- [MA-1.7] Control channel — inherited from `BaseTask._handle_control_command` (`weft/core/tasks/base.py`); PING/PONG, STOP, STATUS, KILL handling.

Current ownership-fence rules:

- `self` means this manager is still positively dispatch-eligible
- `other` means a lower-TID canonical manager is positively proved
- `none` means the registry replay succeeded, but no canonical dispatch owner
  is positively visible, including this manager
- `unknown` means runtime state could not be read confidently enough to choose
  among the other outcomes
- `other` prevents launch, marks this manager non-dispatching for later loop
  iterations, and attempts an exact-message move of the reserved spawn request
  back to `weft.spawn.requests`
- `none` and `unknown` prevent launch and place the manager into
  dispatch-suspended mode; while suspended the manager may still handle
  control, supervise existing children, and re-check ownership, but it must
  not reserve or launch later spawn requests
- while a fenced exact request is still pending recovery in the manager's
  private reserved queue, the manager must not leadership-yield, idle-exit, or
  run ensure-style autostart scans that enqueue more undispatchable work
- if ownership later resolves back to `self`, the manager must exact-message
  requeue the older fenced request back to `weft.spawn.requests` before later
  inbox work resumes
- dispatch eligibility and child supervision may diverge after supersession:
  a manager may keep supervising already-launched persistent children while
  refusing all new dispatch

Current fence diagnostics:

- `manager_spawn_fenced_requeued` with required fields `child_tid`,
  `leader_tid`, `reserved_queue`, and `message_id`
- `manager_spawn_fenced_stranded` with required fields `child_tid`,
  `leader_tid`, `reserved_queue`, and `message_id`
- `manager_spawn_fence_suspended` with required fields `child_tid`,
  `reserved_queue`, `message_id`, and `ownership_state`

These are manager-scoped diagnostics, not spawn rejection signals.

## TID Correlation [MA-2]

Task IDs are the message IDs assigned when spawn requests are written to
`weft.spawn.requests`. The Manager copies that ID into the expanded TaskSpec
`tid`, enabling correlation across registry entries, task logs, and
outbox/control messages.

_Implementation mapping_:
- TID generation — `weft/cli/run.py` :: `_generate_tid` (uses `Queue.generate_timestamp` on the spawn-requests queue).
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
`weft.state.managers`; only PID/process-exit checks remain as bounded
non-queue fallbacks. Early bootstrap failures surface the detached child exit status and
startup stderr context instead of discarding them. Operators can also manage
managers explicitly via `weft manager start|stop|list|status` and
`weft manager serve`. `weft manager start` is a thin operator wrapper over the
same bootstrap helper as `weft run`; it does not accept an arbitrary manager
TaskSpec. `weft manager serve` uses that same canonical manager TaskSpec but
runs it in the foreground for `systemd`, `launchd`, or `supervisord` style
supervision, forcing `idle_timeout=0.0` for that invocation only. The canonical
manager for bootstrap and leadership is the live canonical manager registered
for `weft.spawn.requests`. Canonical records are the live `role="manager"`
entries whose `requests` field points at `weft.spawn.requests`; leadership
chooses the lowest-TID live canonical record. Manager records bound to another
request queue do not participate in default-manager selection. External
`SIGTERM` and `SIGINT` against a Manager enter the same drain path as STOP;
`SIGUSR1` retains immediate kill semantics.

_Implementation mapping_:
- Shared manager lifecycle owner — `weft/core/manager_runtime.py` :: `_build_manager_runtime_invocation`, `_select_active_manager`, `_ensure_manager`, `_start_manager`, `_serve_manager_foreground`, `_list_manager_records`, `_manager_record`, `_stop_manager` (owns canonical manager bootstrap, foreground serve, normalized registry replay, and graceful/forced stop observation).
- Command-side capability surface — `weft/commands/manager.py` and `weft/commands/serve.py` (thin manager-facing commands layered over the shared runtime helper).
- Detached bootstrap launcher — `weft/manager_detached_launcher.py` :: `main` (short-lived wrapper that starts the real manager runtime in a detached session/process-group boundary and reports early launch status back to `_start_manager`).
- Manager process launch — `weft/core/manager_runtime.py` :: `_start_manager` (builds manager TaskSpec, launches the detached wrapper, requires matching pid-plus-registry readiness before success, treats post-proof acknowledgement cleanup as best effort, and reports early bootstrap diagnostics on failure).
- Manager process entry point — `weft/manager_process.py` :: `run_manager_process`, `main` (shared runtime helper plus standalone module invoked via `python -m weft.manager_process`).
- Leadership election and ownership fencing — `weft/core/manager.py` :: `Manager._maybe_yield_leadership`, `Manager._leader_tid`, `Manager._read_active_manager_records`, `Manager._active_manager_records`, `Manager._evaluate_dispatch_ownership`, `Manager._refresh_dispatch_suspension`, `Manager._apply_final_dispatch_fence`, `Manager._requeue_reserved_spawn_request`; `weft/helpers.py::is_canonical_manager_record`; `weft/helpers.py::canonical_owner_tid` (lowest-TID canonical manager wins; non-leaders yield, requeue, or suspend according to the final ownership proof).
- Internal runtime submission envelope — `weft/core/spawn_requests.py`
  `submit_spawn_request` carries internal runtime selectors on the manager-owned
  spawn envelope, and `weft/core/manager.py` `Manager._build_child_spec`
  re-applies them when materializing the child TaskSpec.
- External signal handling — `weft/core/manager.py` :: `Manager.handle_termination_signal` (TERM/INT drain, SIGUSR1 kill).
- CLI management — `weft/commands/manager.py` :: `start_command`, `stop_command`, `list_command`, `status_command`; these commands are thin wrappers over the shared lifecycle helper. `weft/commands/status.py` :: `_collect_manager_records` reuses the same lifecycle reader for manager views.
- Foreground supervision command — `weft/commands/serve.py` :: `serve_command`, registered in `weft/cli/app.py` as `weft manager serve`.

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
