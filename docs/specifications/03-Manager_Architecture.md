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
implemented. The Client (`weft/commands/run.py`) builds TaskSpec templates,
enqueues them on `weft.spawn.requests`, and optionally waits for completion by
tailing task logs/outboxes. The spawn-request message ID becomes the task TID.
Managers consume those requests, expand the TaskSpec, launch child Consumers via
`launch_task_process`, and emit `weft.log.tasks` events just like any other task.

## Related Plans

- [Task Lifecycle Stop/Drain Audit Plan](../plans/task-lifecycle-stop-drain-audit-plan.md) – Audit manager drain, registry semantics, and lifecycle ownership before changing worker behavior.
- [Manager Bootstrap Unification Plan](../plans/manager-bootstrap-unification-plan.md) – Collapse `weft manager start` onto the canonical manager bootstrap path used by `weft run`.
- [Manager Lifecycle Command Consolidation Plan](../plans/manager-lifecycle-command-consolidation-plan.md) – Collapse remaining `manager list|status|stop` and `weft status` manager lifecycle reads onto the same shared control-plane helper as bootstrap.
- [Weft Serve Supervised Manager Plan](../plans/weft-serve-supervised-manager-plan.md) – Add a minimal foreground `weft manager serve` command for supervisor-managed persistent managers and align manager TERM/INT with graceful drain.
- [Detached Manager Bootstrap Hardening Plan](../plans/detached-manager-bootstrap-hardening-plan.md) – Replace parent-dependent detached bootstrap with a real detached-launch wrapper, stronger startup proof, and actionable early-failure diagnostics.
- [Manager Bootstrap Readiness And Cleanup Test Plan](../plans/manager-bootstrap-readiness-and-cleanup-test-plan.md) – Replace the fixed startup delay with event-based readiness proof and split cleanup-vs-startup stress coverage.
- [Agent Runtime Implementation Plan](../plans/agent-runtime-implementation-plan.md) – references `MA-2` TID correlation.
- [Persistent Agent Runtime Implementation Plan](../plans/persistent-agent-runtime-implementation-plan.md) – references Manager Architecture for long-lived agent sessions.
- [TaskSpec Clean Design Plan](../plans/taskspec-clean-design-plan.md) – references Manager Architecture for TaskSpec schema alignment.

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
   via the `BaseTask` helpers.
2. **Child process launch** – Validated TaskSpecs are executed via
   `launch_task_process(Consumer, …)`, preserving the standard isolation model
   (a dedicated subprocess per child task).
3. **Initial payload delivery** – If the spawn payload includes
   `inbox_message`, the manager writes it into the child’s inbox queue before the
   child starts processing.
4. **Registry heartbeat** – Managers publish an `active` record to
   `weft.state.managers` on startup (including queue names, PID, role, and
   capabilities) and replace it with a `stopped` record during shutdown. The
   active record is pruned when the manager exits cleanly.
5. **Idle timeout** – Managers honour the `idle_timeout` metadata field (default
   `WEFT_MANAGER_LIFETIME_TIMEOUT`) and will self-terminate after prolonged
   inactivity while ensuring all child processes are reaped.
6. **Autostart manifests** – When `WEFT_AUTOSTART_TASKS` is enabled the manager
   scans `.weft/autostart/` for manifests that reference stored task specs or
   pipelines and launches each target using the same spawn pipeline as queue
   submissions. Manifest lifecycle policy (`once`/`ensure`) governs restarts
   while the manager is running. Current runtime support is limited to `task`
   targets; `pipeline` targets are logged as unsupported and skipped.
7. **Control channel** – In addition to STOP/STATUS, managers reply `PONG` to
  `PING` messages on `ctrl_out`, supporting simple health checks.

_Implementation mapping_:
- [MA-1.1] Spawn queue consumption — `Manager._handle_work_message`, `Manager._build_child_spec`.
- [MA-1.2] Child process launch — `Manager._launch_child_task`, `launch_task_process` (`weft/core/launcher.py`).
- [MA-1.3] Initial payload delivery — `Manager._launch_child_task` (inbox seeding block).
- [MA-1.4] Registry heartbeat — `Manager._register_manager`, `Manager._unregister_manager`, `Manager._atexit_unregister`.
- [MA-1.5] Idle timeout — `Manager.process_once` (idle-timeout check), `Manager._update_idle_activity_from_broker`.
- [MA-1.6] Autostart manifests — `Manager._tick_autostart`, `Manager._build_autostart_spawn_payload`, `Manager._load_autostart_manifest`, `Manager._load_autostart_taskspec`, `Manager._enqueue_autostart_request`. Current runtime support covers `task` targets; `pipeline` targets are skipped with a warning.
- [MA-1.7] Control channel — inherited from `BaseTask._handle_control_command` (`weft/core/tasks/base.py`); PING/PONG, STOP, STATUS, KILL handling.

## TID Correlation [MA-2]

Task IDs are the message IDs assigned when spawn requests are written to
`weft.spawn.requests`. The Manager copies that ID into the expanded TaskSpec
`tid`, enabling correlation across registry entries, task logs, and
outbox/control messages.

_Implementation mapping_:
- TID generation — `weft/commands/run.py` :: `_generate_tid` (uses `Queue.generate_timestamp` on the spawn-requests queue).
- Enqueue with TID as message timestamp — `weft/commands/run.py` :: `_enqueue_taskspec` (writes spawn payload at `int(task_tid)`).
- Manager-side TID propagation — `weft/core/manager.py` :: `Manager._build_child_spec` (passes `str(timestamp)` as child TID via `resolve_taskspec_payload`).

## Bootstrap and Lifecycle [MA-3]

`weft run` guarantees that a manager is available by minting a manager TaskSpec
and launching a short-lived detached bootstrap helper when required. That
helper starts `weft.manager_process`, then the shared lifecycle code waits for a
stronger startup proof than "registry entry appeared once": the launched manager
PID must still be live, the canonical registry record for the requested manager
TID must exist with that same PID, and the detached launcher must still be able
to acknowledge success before the caller returns. This keeps startup proof tied
to current manager readiness rather than a fixed sleep window. Early bootstrap
failures surface the detached child exit status and startup stderr context
instead of discarding them. Operators can also manage managers explicitly via
`weft manager start|stop|list|status` and `weft manager serve`. `weft manager start`
is a thin operator wrapper over the same bootstrap helper as `weft run`; it
does not accept an arbitrary manager TaskSpec. `weft manager serve` uses that
same canonical manager TaskSpec but runs it in the foreground for `systemd`,
`launchd`, or `supervisord` style supervision, forcing `idle_timeout=0.0` for
that invocation only. The canonical manager for bootstrap and leadership is the
live manager registered for `weft.spawn.requests`. Manager records bound to
another request queue do not participate in default-manager selection. External
`SIGTERM` and `SIGINT` against a Manager enter the same drain path as STOP;
`SIGUSR1` retains immediate kill semantics.

_Implementation mapping_:
- Shared manager lifecycle helper — `weft/commands/_manager_bootstrap.py` :: `_ensure_manager`, `_serve_manager_foreground`, `_list_manager_records`, `_manager_record`, `_stop_manager` (owns canonical manager bootstrap, foreground serve, normalized registry replay, and graceful/forced stop observation for CLI callers).
- Detached bootstrap launcher — `weft/manager_detached_launcher.py` :: `main` (short-lived wrapper that starts the real manager runtime in a detached session/process-group boundary and reports early launch status back to `_start_manager`).
- Manager process launch — `weft/commands/_manager_bootstrap.py` :: `_start_manager` (builds manager TaskSpec, launches the detached wrapper, requires matching pid-plus-registry readiness plus launcher acknowledgement before success, and reports early bootstrap diagnostics on failure).
- Manager process entry point — `weft/manager_process.py` :: `run_manager_process`, `main` (shared runtime helper plus standalone module invoked via `python -m weft.manager_process`).
- Leadership election — `weft/core/manager.py` :: `Manager._maybe_yield_leadership`, `Manager._leader_tid`, `Manager._active_manager_records` (lowest-TID canonical manager wins; duplicates self-cancel).
- External signal handling — `weft/core/manager.py` :: `Manager.handle_termination_signal` (TERM/INT drain, SIGUSR1 kill).
- CLI management — `weft/commands/manager.py` :: `start_command`, `stop_command`, `list_command`, `status_command`; these commands are thin wrappers over the shared lifecycle helper. `weft/commands/status.py` :: `_collect_manager_records` reuses the same lifecycle reader for manager views.
- Foreground supervision command — `weft/commands/serve.py` :: `serve_command`, registered in `weft/cli.py` as `weft manager serve`.

## Scope Boundary [MA-4]

The current manager architecture favors one canonical manager per context and a
shared child-task launch path. Specialized pools or richer orchestration layers
are outside the current contract and are tracked in the companion doc:

- [`03A-Manager_Architecture_Planned.md`](03A-Manager_Architecture_Planned.md)

_Implementation mapping_: the Manager currently launches `Consumer` as the
child task class via `Manager._launch_child_task`. Runner extensibility affects
execution backends, not manager specialization.
