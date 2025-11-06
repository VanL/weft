# Manager Architecture: The Recursive Model

This document describes how Weft realizes the “workers are tasks” concept. A
Manager is implemented as a long-running `BaseTask` that consumes spawn requests
from SimpleBroker queues, launches child tasks, and reports lifecycle events.
The CLI component that submits work is referred to as the **Client**.

_Implementation status_: `Manager` (`weft/core/manager.py`), the spawn registry
queue, and the CLI wrappers (`weft worker …`, `weft run`) are fully
implemented. The Client (`weft/commands/run.py`) builds TaskSpecs, mints TIDs via
`generate_timestamp()`, enqueues them on `weft.spawn.requests`, and optionally
waits for completion by tailing task logs/outboxes. Managers consume those
requests, launch child Consumers via `launch_task_process`, and emit
`weft.tasks.log` events just like any other task.

## Conceptual Model: Everything is a Task [WA-0]

- **No special cases** – Managers inherit from `BaseTask`, so they use the same
  queue wiring, process-title formatting, and control semantics as regular
  tasks.
- **Uniform observability** – Managers register themselves in
  `weft.workers.registry`, participate in the task log, and respond on the
  control channel (`STOP`, `PAUSE`, `RESUME`, `PING`, `STATUS`).
- **Self-hosting** – Managers spawn child Consumers by writing back into the
  same SimpleBroker database they are monitoring.

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
   `weft.workers.registry` on startup (including queue names, PID, role, and
   capabilities) and replace it with a `stopped` record during shutdown. The
   active record is pruned when the manager exits cleanly.
5. **Idle timeout** – Managers honour the `idle_timeout` metadata field (default
   `WEFT_MANAGER_LIFETIME_TIMEOUT`) and will self-terminate after prolonged
   inactivity while ensuring all child processes are reaped.
6. **Autostart templates** – When `WEFT_AUTOSTART_TASKS` is enabled the manager
   scans `.weft/autostart/` for TaskSpec templates, mints new TIDs via
   SimpleBroker, and launches each task using the same spawn pipeline as queue
   submissions. Active templates are de-duplicated using the task log, and the
   manager issues `STOP` on shutdown so background daemons exit cleanly.
7. **Control channel** – In addition to STOP/PAUSE/RESUME/STATUS, managers reply
   `PONG` to `PING` messages on `ctrl_out`, supporting simple health checks.

## TID Correlation [WA-2]

Clients obtain task IDs by calling `BrokerDB.generate_timestamp()` before
enqueuing spawn requests. The timestamp is embedded in both the message ID on
`weft.spawn.requests` and the child TaskSpec `tid`, enabling correlation across
registry entries, task logs, and outbox/control messages.

## Bootstrap and Lifecycle [WA-3]

`weft run` guarantees that a manager is available by minting a manager TaskSpec
and launching `weft.manager_process` when required. The helper waits for the
registry entry to appear, prints diagnostic information (TID, PID, queue names),
and then exits—leaving the dedicated manager process running in the background.
Operators can also manage managers explicitly via `weft worker start|stop|list|status`.

## Specialisation and Future Work [WA-4]

The current implementation favours a single-manager pattern where child tasks
are all Consumers. Specialized worker pools (for example, dedicated AI workers
or pipeline orchestrators) can be introduced by publishing alternative TaskSpecs
through the same spawn queue or by running multiple managers with distinct
metadata and capabilities lists.
