# Manager Architecture: The Recursive Model

This document describes how Weft realizes the “workers are tasks” concept. A
Manager is implemented as a long-running `BaseTask` that consumes spawn requests
from SimpleBroker queues, launches child tasks, and reports lifecycle events.
The CLI component that submits work is referred to as the **Client**.

**Terminology**
- **Task**: Any executable unit described by a TaskSpec.
- **Consumer**: The default task executor for command/function targets.
- **Manager**: A long-lived task that spawns child tasks and manages autostart.
- **Worker (CLI)**: The CLI namespace for managing Managers (not a distinct runtime).

_Implementation status_: `Manager` (`weft/core/manager.py`), the spawn registry
queue, and the CLI wrappers (`weft worker …`, `weft run`) are fully
implemented. The Client (`weft/commands/run.py`) builds TaskSpec templates,
enqueues them on `weft.spawn.requests`, and optionally waits for completion by
tailing task logs/outboxes. The spawn-request message ID becomes the task TID.
Managers consume those requests, expand the TaskSpec, launch child Consumers via
`launch_task_process`, and emit `weft.log.tasks` events just like any other task.

## Related Plans

- [Task Lifecycle Stop/Drain Audit Plan](../plans/task-lifecycle-stop-drain-audit-plan.md) – Audit manager drain, registry semantics, and lifecycle ownership before changing worker behavior.
- [Agent Runtime Implementation Plan](../plans/agent-runtime-implementation-plan.md) – references `WA-2` TID correlation.
- [Persistent Agent Runtime Implementation Plan](../plans/persistent-agent-runtime-implementation-plan.md) – references Worker Architecture for long-lived agent sessions.
- [TaskSpec Clean Design Plan](../plans/taskspec-clean-design-plan.md) – references Worker Architecture for TaskSpec schema alignment.

## Conceptual Model: Everything is a Task [WA-0]

- **No special cases** – Managers inherit from `BaseTask`, so they use the same
  queue wiring, process-title formatting, and control semantics as regular
  tasks.
- **Uniform observability** – Managers register themselves in
  `weft.state.workers`, participate in the task log, and respond on the
  control channel (`STOP`, `PING`, `STATUS`).
- **Self-hosting** – Managers spawn child Consumers by writing back into the
  same SimpleBroker database they are monitoring.

_Implementation mapping_:
- `weft/core/manager.py` — `Manager(BaseTask)` inherits queue wiring and control semantics.
- `weft/core/tasks/base.py` — `BaseTask` provides queue wiring, process-title formatting (`_update_process_title`), control message handling (`_handle_control_message`, `_handle_control_command`), and state reporting (`_report_state_change`).

## Manager Behaviour [WA-1]

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
   `weft.state.workers` on startup (including queue names, PID, role, and
   capabilities) and replace it with a `stopped` record during shutdown. The
   active record is pruned when the manager exits cleanly.
5. **Idle timeout** – Managers honour the `idle_timeout` metadata field (default
   `WEFT_MANAGER_LIFETIME_TIMEOUT`) and will self-terminate after prolonged
   inactivity while ensuring all child processes are reaped.
6. **Autostart manifests** – When `WEFT_AUTOSTART_TASKS` is enabled the manager
   scans `.weft/autostart/` for manifests that reference stored task specs or
   pipelines and launches each target using the same spawn pipeline as queue
   submissions. Manifest lifecycle policy (`once`/`ensure`) governs restarts
   while the manager is running. **[NOT YET IMPLEMENTED: pipeline targets]** —
   `Manager._build_autostart_spawn_payload` logs a warning and returns `None` for
   `target.type == "pipeline"`.
7. **Control channel** – In addition to STOP/STATUS, managers reply `PONG` to
  `PING` messages on `ctrl_out`, supporting simple health checks.

_Implementation mapping_:
- [WA-1.1] Spawn queue consumption — `Manager._handle_work_message`, `Manager._build_child_spec`.
- [WA-1.2] Child process launch — `Manager._launch_child_task`, `launch_task_process` (`weft/core/launcher.py`).
- [WA-1.3] Initial payload delivery — `Manager._launch_child_task` (inbox seeding block).
- [WA-1.4] Registry heartbeat — `Manager._register_worker`, `Manager._unregister_worker`, `Manager._atexit_unregister`.
- [WA-1.5] Idle timeout — `Manager.process_once` (idle-timeout check), `Manager._update_idle_activity_from_broker`.
- [WA-1.6] Autostart manifests — `Manager._tick_autostart`, `Manager._build_autostart_spawn_payload`, `Manager._load_autostart_manifest`, `Manager._load_autostart_taskspec`, `Manager._enqueue_autostart_request`. Pipeline targets: **[NOT YET IMPLEMENTED]**.
- [WA-1.7] Control channel — inherited from `BaseTask._handle_control_command` (`weft/core/tasks/base.py`); PING/PONG, STOP, STATUS, KILL handling.

## TID Correlation [WA-2]

Task IDs are the message IDs assigned when spawn requests are written to
`weft.spawn.requests`. The Manager copies that ID into the expanded TaskSpec
`tid`, enabling correlation across registry entries, task logs, and
outbox/control messages.

_Implementation mapping_:
- TID generation — `weft/commands/run.py` :: `_generate_tid` (uses `Queue.generate_timestamp` on the spawn-requests queue).
- Enqueue with TID as message timestamp — `weft/commands/run.py` :: `_enqueue_taskspec` (writes spawn payload at `int(task_tid)`).
- Manager-side TID propagation — `weft/core/manager.py` :: `Manager._build_child_spec` (passes `str(timestamp)` as child TID via `resolve_taskspec_payload`).

## Bootstrap and Lifecycle [WA-3]

`weft run` guarantees that a manager is available by minting a manager TaskSpec
and launching `weft.manager_process` when required. The helper waits for the
registry entry to appear, prints diagnostic information (TID, PID, queue names),
and then exits—leaving the dedicated manager process running in the background.
Operators can also manage managers explicitly via `weft worker start|stop|list|status`.

_Implementation mapping_:
- Manager bootstrap — `weft/commands/run.py` :: `_ensure_manager` (checks for existing active manager, delegates to `_start_manager` if none found).
- Manager process launch — `weft/commands/run.py` :: `_start_manager` (builds manager TaskSpec, launches `weft.manager_process` via `subprocess.Popen`, polls registry until entry appears).
- Manager process entry point — `weft/manager_process.py` (standalone module invoked via `python -m weft.manager_process`).
- Leadership election — `weft/core/manager.py` :: `Manager._maybe_yield_leadership`, `Manager._leader_tid`, `Manager._active_manager_records` (lowest-TID manager wins; duplicates self-cancel).
- CLI management — `weft/commands/worker.py` :: `start_command`, `stop_command`, `list_command`, `status_command`; registered via `weft/cli.py` as `weft worker start|stop|list|status`.

## Specialisation and Future Work [WA-4]

**[NOT YET IMPLEMENTED]**

The current implementation favours a single-manager pattern where child tasks
are all Consumers. Specialized worker pools (for example, dedicated AI workers
or pipeline orchestrators) can be introduced by publishing alternative TaskSpecs
through the same spawn queue or by running multiple managers with distinct
metadata and capabilities lists.

_Implementation mapping_: No dedicated code exists for specialized worker pools. The Manager always launches `Consumer` as the child task class (hardcoded in `Manager._launch_child_task` via `launch_task_process(Consumer, ...)`). The runner plugin system (`weft/_runner_plugins.py`, `weft/core/tasks/runner.py`) provides execution-backend extensibility but not worker-pool specialization.
