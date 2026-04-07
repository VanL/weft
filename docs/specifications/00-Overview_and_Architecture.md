# Weft System Overview and Architecture

## Executive Summary

The Weft Task System is a lightweight task execution framework built on SimpleBroker's SQLite-backed message queue system. It provides reliable, monitored execution of both Python functions and system commands with comprehensive resource tracking, state management, and queue-based communication.

_Implementation mapping_: Entry point `weft/cli.py`, core logic in `weft/core/`, task runtime in `weft/core/tasks/`, command handlers in `weft/commands/`.

### Core Mission
The system enables **structured interaction between Unix tools and AI agents** with variable latencies (milliseconds to minutes). It acts as "durable Unix pipes" - providing persistent, observable, and resource-controlled process coordination through message queues. The design prioritizes **correctness and concurrency handling** over massive scale, focusing on multiplexing and coordinating a relatively small (usually <200) number of independent, mostly ephemeral processes.

Weft is optimized for **asynchronous multi-agent workflows**: background agents
can subscribe to queues or file events, report back via queues/logs, and shut
down cleanly when the manager exits. Operators enable these services by placing
autostart manifests in `.weft/autostart/` that reference stored task specs or
pipelines—no extra commands required.

_Implementation mapping_: Autostart manifests are scanned and launched by `weft/core/manager.py` (`_tick_autostart`, `_load_autostart_manifest`, `_build_autostart_spawn_payload`). Context/directory setup in `weft/context.py`. Pipeline references in autostart manifests are parsed but **[NOT YET IMPLEMENTED]** as a first-class orchestration primitive (only simple task-spec references are fully supported).

### SimpleBroker Foundation
SimpleBroker provides features that align with the ephemeral task model:
- **No queue pre-registration**: Queues are created on first use
- **Self-cleaning**: Empty queues automatically disappear  
- **SQLite-backed**: Simple, reliable, single-node persistence
- **Exactly-once delivery**: Queue read/move operations are exactly-once; task
  processing is at-least-once depending on reserved queue policy
- **Auto-initialization**: Database created automatically on first message write
- **Timestamp IDs**: Every message gets a unique 64-bit hybrid timestamp ID
  (microseconds + logical counter; treat as opaque)

### Key Design Principles
- **Queue-First Architecture**: All communication via persistent message queues
- **Recursive Task Model**: Workers are Tasks with long-running targets - everything is a Task
- **Partial Immutability**: Task configuration (spec) is immutable after creation; state and metadata are mutable for runtime updates
- **Forward-Only State Transitions**: No rollback, only progression through states
- **Unified Reservation Pattern**: Single `.reserved` queue serves as both work-in-progress and dead-letter queue
- **Observable State**: All state changes flow through `weft.log.tasks` for global visibility
- **TID Correlation**: Spawn-request message IDs become Task IDs, providing end-to-end correlation
- **Resource Monitoring**: Continuous tracking with configurable limits
- **Fail-Safe Defaults**: Conservative resource limits and timeout behaviors
- **Progressive Disclosure UX**: Default workflows stay as simple as SimpleBroker (e.g., `weft run <command>`), while advanced orchestration is opt-in.
- **Operator-Intent Autostart**: Background agents are launched only when manifests exist in `.weft/autostart/`, and they stop cleanly when the manager exits.

## System Architecture

### Layered Architecture

```
┌─────────────────────────────────────────────────┐
│                 User Interface                  │
│            (CLI, API, Programmatic)             │
├─────────────────────────────────────────────────┤
│                Task Orchestration               │
│         (Client, Scheduler, Pool)          │
├─────────────────────────────────────────────────┤
│                  Task Execution                 │
│        (Task - Universal Primitive)             │
│    Workers are Tasks with run_forever targets   │
├─────────────────────────────────────────────────┤
│               State & Monitoring                │
│     (TaskSpec, StateSection, ResourceMonitor)   │
├─────────────────────────────────────────────────┤
│                 Queue System                    │
│        (SimpleBroker, Queue, Watcher)           │
├─────────────────────────────────────────────────┤
│              Persistent Storage                 │
│              (SQLite Database)                  │
└─────────────────────────────────────────────────┘
```

_Implementation mapping per layer_:

- **User Interface**: `weft/cli.py` (CLI via Typer), `weft/commands/` (command handlers). API and Programmatic interfaces are **[NOT YET IMPLEMENTED]** beyond internal Python imports.
- **Task Orchestration**: `weft/core/manager.py` (Manager as dispatcher). **Client** exists only as `InteractiveStreamClient` in `weft/commands/interactive.py` for streaming. **Scheduler** and **Pool** are **[NOT YET IMPLEMENTED]** as distinct components; the Manager handles dispatch directly.
- **Task Execution**: `weft/core/tasks/base.py` (`BaseTask`), `weft/core/tasks/consumer.py` (`Consumer`), `weft/core/tasks/runner.py`, `weft/core/tasks/interactive.py`. Runners in `weft/core/runners/` (`host.py`, `subprocess_runner.py`).
- **State & Monitoring**: `weft/core/taskspec.py` (`TaskSpec`, `StateSection`), `weft/core/resource_monitor.py` (`ResourceMonitor`, `PsutilResourceMonitor`).
- **Queue System**: SimpleBroker (external dependency). `weft/core/tasks/multiqueue_watcher.py` (`MultiQueueWatcher`), `weft/core/tasks/base.py` (queue wiring).
- **Persistent Storage**: SQLite via SimpleBroker.

### Component Interactions (Recursive Architecture)

```
Bootstrap ──creates──> Primordial Worker (Task)
                            │
                            └──spawns──> Worker Tasks
                                              │
                                              └──spawn──> Regular Tasks
                                                             │
                                                             └──> Ephemeral execution

Manager (Dispatcher Task) ──monitors──> weft.spawn.requests
     │                           │
     └──validates──> Registry    └──> Uses spawn-request message ID as Task TID
                        │                    │
                        └──creates──> TaskSpec with TID correlation
                                          │
                                          └──spawns──> Child Task
```

_Implementation mapping_: The Manager (`weft/core/manager.py`) implements the dispatcher role, monitoring `weft.spawn.requests` and spawning child tasks. Bootstrap and Manager startup handled by `weft/core/launcher.py` and `weft/manager_process.py`. The "Primordial Worker" concept is not a named class; the Manager itself acts as the primordial task. **Registry** (pre-defined task validation) is **[NOT YET IMPLEMENTED]** as a standalone component; the Manager validates inline. TID correlation (spawn-request message ID becomes Task TID) is implemented in `weft/core/manager.py`.

## Performance Targets

**[NOT YET IMPLEMENTED]** No formal benchmarking suite exists to validate these targets. They remain design aspirations.

### Throughput Metrics (for <200 ephemeral tasks)
- Task creation: 100 tasks/second (design target)
- Queue messages: 1000 messages/second (SimpleBroker capability)
- Concurrent tasks: 200 (design target)
- Pipeline stages: 10 stages typical, 20 maximum **[NOT YET IMPLEMENTED]** — pipelines are not a first-class primitive
- Process title updates: <1ms per update

_Implementation mapping_: Process titles set via `setproctitle` in `weft/core/manager.py`, `weft/core/tasks/consumer.py`, `weft/core/tasks/base.py`, and others.

### Latency Metrics
- Task startup: <100ms
- Queue read/write: <10ms
- State update to log: <5ms
- Control response: <50ms
- Process title update: <1ms
- TID lookup: <10ms
- Variable tolerance: ms to minutes for AI agents

_Implementation mapping_: TID mappings stored in `weft.state.tid_mappings` queue, managed by `weft/core/tasks/base.py`, read by `weft/commands/status.py` and `weft/commands/tasks.py`.

### Resource Metrics
- Memory per task: <10MB overhead (ephemeral)
- CPU monitoring: <2% overhead (low-frequency sampling)
- Database size: <100KB per 100 tasks
- Queue auto-cleanup: Empty queues disappear
- Process title overhead: <100KB per task
- TID mapping storage: <1KB per task

_Implementation mapping_: Resource monitoring in `weft/core/resource_monitor.py` (`ResourceMonitor`, `PsutilResourceMonitor`). Queue auto-cleanup is a SimpleBroker feature.

## Security Model

### Trust Model
- **User-level permissions**: Tasks run as user (not adversarial)
- **No privilege escalation**: Standard Unix permissions apply
- **AI agent restrictions**: Pre-defined task registry only — **[NOT YET IMPLEMENTED]** as a standalone registry; no formal allow-list enforcement exists
- **Audit trail**: All actions logged to weft.log.tasks

_Implementation mapping_: State events written to `weft.log.tasks` by `weft/core/tasks/base.py` and `weft/core/manager.py`. Process titles set in task lifecycle code across `weft/core/tasks/`.

### Observable Security
- All state changes in weft.log.tasks
- Failed tasks visible in reserved queues
- Resource violations tracked in state
- Complete audit trail for debugging
- Process titles expose task identity for management
- TID mappings enable emergency intervention
- Standard Unix tools can monitor/control tasks
- No hidden or untrackable processes

## Related Plans

- [`docs/plans/agent-runtime-boundary-cleanup-plan.md`](../plans/agent-runtime-boundary-cleanup-plan.md) — Agent runtime boundary cleanup
- [`docs/plans/agent-runtime-implementation-plan.md`](../plans/agent-runtime-implementation-plan.md) — Agent runtime implementation
- [`docs/plans/persistent-agent-runtime-implementation-plan.md`](../plans/persistent-agent-runtime-implementation-plan.md) — Persistent agent runtime
- [`docs/plans/simplebroker-backend-generalization-plan.md`](../plans/simplebroker-backend-generalization-plan.md) — SimpleBroker backend generalization
- [`docs/plans/runner-extension-point-plan.md`](../plans/runner-extension-point-plan.md) — Runner extension point
- [`docs/plans/taskspec-clean-design-plan.md`](../plans/taskspec-clean-design-plan.md) — TaskSpec clean design
- [`docs/plans/piped-input-support-plan.md`](../plans/piped-input-support-plan.md) — Piped input support

## Related Documents

_Implementation mapping_: `weft/cli.py` (CLI entry points), `weft/core/manager.py` (manager/worker), `weft/core/tasks/base.py` (task runtime), `weft/core/tasks/runner.py` (execution), `weft/context.py` (context discovery).

- **[01-Core_Components.md](01-Core_Components.md)** - Detailed component architecture
- **[02-TaskSpec.md](02-TaskSpec.md)** - Task configuration specification
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)** - Queue system integration
- **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)** - Communication patterns
- **[06-Resource_Management.md](06-Resource_Management.md)** - Resource controls and error handling
- **[07-System_Invariants.md](07-System_Invariants.md)** - System guarantees and constraints
- **[08-Testing_Strategy.md](08-Testing_Strategy.md)** - Testing approach and standards
- **[09-Implementation_Plan.md](09-Implementation_Plan.md)** - Development roadmap
