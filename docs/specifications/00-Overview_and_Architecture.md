# Weft System Overview and Architecture

## Executive Summary

The Weft Task System is a lightweight task execution framework built on SimpleBroker's SQLite-backed message queue system. It provides reliable, monitored execution of both Python functions and system commands with comprehensive resource tracking, state management, and queue-based communication.

### Core Mission
The system enables **structured interaction between Unix tools and AI agents** with variable latencies (milliseconds to minutes). It acts as "durable Unix pipes" - providing persistent, observable, and resource-controlled process coordination through message queues. The design prioritizes **correctness and concurrency handling** over massive scale, focusing on multiplexing and coordinating a relatively small (usually <200) number of independent, mostly ephemeral processes.

### SimpleBroker Foundation
SimpleBroker provides features that align with the ephemeral task model:
- **No queue pre-registration**: Queues are created on first use
- **Self-cleaning**: Empty queues automatically disappear  
- **SQLite-backed**: Simple, reliable, single-node persistence
- **Exactly-once delivery**: Built-in message claim semantics
- **Auto-initialization**: Database created automatically on first message write
- **Timestamp IDs**: Every message gets a unique 64-bit timestamp ID (nanosecond precision)

### Key Design Principles
- **Queue-First Architecture**: All communication via persistent message queues
- **Recursive Task Model**: Workers are Tasks with long-running targets - everything is a Task
- **Partial Immutability**: Task configuration (spec) is immutable after creation; state and metadata are mutable for runtime updates
- **Forward-Only State Transitions**: No rollback, only progression through states
- **Unified Reservation Pattern**: Single `.reserved` queue serves as both work-in-progress and dead-letter queue
- **Observable State**: All state changes flow through `weft.tasks.log` for global visibility
- **TID Correlation**: Message IDs become Task IDs, providing end-to-end correlation
- **Resource Monitoring**: Continuous tracking with configurable limits
- **Fail-Safe Defaults**: Conservative resource limits and timeout behaviors

## System Architecture

### Layered Architecture

```
┌─────────────────────────────────────────────────┐
│                 User Interface                  │
│            (CLI, API, Programmatic)             │
├─────────────────────────────────────────────────┤
│                Task Orchestration               │
│         (TaskManager, Scheduler, Pool)          │
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

### Component Interactions (Recursive Architecture)

```
Bootstrap ──creates──> Primordial Worker (Task)
                            │
                            └──spawns──> Worker Tasks
                                              │
                                              └──spawn──> Regular Tasks
                                                             │
                                                             └──> Ephemeral execution

Worker (Task) ──monitors──> weft.spawn.requests
     │                           │
     └──validates──> Registry    └──> Uses msg._timestamp as child TID
                        │                    │
                        └──creates──> TaskSpec with TID correlation
                                          │
                                          └──spawns──> Child Task
```

## Performance Targets

### Throughput Metrics (for <200 ephemeral tasks)
- Task creation: 50 tasks/second (sufficient for scale)
- Queue messages: 1000 messages/second (SimpleBroker capability)
- Concurrent tasks: 200 (design target)
- Pipeline stages: 10 stages typical, 20 maximum
- Process title updates: <1ms per update

### Latency Metrics
- Task startup: <100ms
- Queue read/write: <10ms
- State update to log: <5ms
- Control response: <50ms
- Process title update: <1ms
- TID lookup: <10ms
- Variable tolerance: ms to minutes for AI agents

### Resource Metrics
- Memory per task: <10MB overhead (ephemeral)
- CPU monitoring: <2% overhead (low-frequency sampling)
- Database size: <100KB per 100 tasks
- Queue auto-cleanup: Empty queues disappear
- Process title overhead: <100KB per task
- TID mapping storage: <1KB per task

## Security Model

### Trust Model
- **User-level permissions**: Tasks run as user (not adversarial)
- **No privilege escalation**: Standard Unix permissions apply
- **AI agent restrictions**: Pre-defined task registry only
- **Audit trail**: All actions logged to weft.tasks.log

### Observable Security
- All state changes in weft.tasks.log
- Failed tasks visible in reserved queues
- Resource violations tracked in state
- Complete audit trail for debugging
- Process titles expose task identity for management
- TID mappings enable emergency intervention
- Standard Unix tools can monitor/control tasks
- No hidden or untrackable processes

## Related Documents

- **[01-Core_Components.md](01-Core_Components.md)** - Detailed component architecture
- **[02-TaskSpec.md](02-TaskSpec.md)** - Task configuration specification
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)** - Queue system integration
- **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)** - Communication patterns
- **[06-Resource_Management.md](06-Resource_Management.md)** - Resource controls and error handling
- **[07-System_Invariants.md](07-System_Invariants.md)** - System guarantees and constraints
- **[08-Testing_Strategy.md](08-Testing_Strategy.md)** - Testing approach and standards
- **[09-Implementation_Plan.md](09-Implementation_Plan.md)** - Development roadmap