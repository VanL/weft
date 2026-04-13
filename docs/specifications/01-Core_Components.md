# Core Components

This document describes the current component boundaries that make Weft work
today. It keeps the "why" behind those boundaries because the reasoning is part
of the contract: Weft stays comprehensible by keeping runtime ownership narrow
and explicit.

See also:

- planned companion:
  [`01A-Core_Components_Planned.md`](01A-Core_Components_Planned.md)
- context and broker integration:
  [`04-SimpleBroker_Integration.md`](04-SimpleBroker_Integration.md)

## Related Plans

- [`docs/plans/spec-corpus-current-vs-planned-split-plan.md`](../plans/spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/runner-extension-point-plan.md`](../plans/runner-extension-point-plan.md)
- [`docs/plans/persistent-agent-runtime-implementation-plan.md`](../plans/persistent-agent-runtime-implementation-plan.md)
- [`docs/plans/active-control-main-thread-plan.md`](../plans/active-control-main-thread-plan.md)

## 1. TaskSpec (`weft/core/taskspec.py`) [CC-1]

**Purpose**: define execution intent, queue wiring, and mutable runtime state in
one validated model while freezing the parts that should not drift after task
creation.

_Implementation mapping_: `weft/core/taskspec.py` — `TaskSpec`, `SpecSection`,
`IOSection`, `StateSection`, `LimitsSection`, `RunnerSection`, `AgentSection`,
`FrozenList`, `FrozenDict`, `resolve_taskspec_payload`.

Current contract:

- `spec` and `io` are immutable after TaskSpec creation
- `state` and metadata remain mutable
- stored templates omit runtime-expanded fields such as `tid`, `io`, and
  `state`
- the manager resolves runtime TaskSpecs from templates at spawn time
- runner selection lives in `spec.runner`
- resource limits live in `spec.limits`

Why this shape exists:

- it prevents runtime code from mutating execution intent mid-flight
- it keeps task storage and replay safe
- it lets the manager resolve runtime-only values once, at the proper boundary

## 2. Task Execution Architecture

Weft's task runtime is a small hierarchy, not a pile of unrelated worker types.
That is deliberate. Queue wiring, state emission, control handling, and runner
dispatch should not be reimplemented separately in every task class.

### 2.1 MultiQueueWatcher (`weft/core/tasks/multiqueue_watcher.py`) [CC-2.1]

**Purpose**: schedule multiple queues with explicit per-queue processing modes.

_Implementation mapping_: `weft/core/tasks/multiqueue_watcher.py` —
`MultiQueueWatcher`, `QueueMode`, `QueueMessageContext`.

Current role:

- monitor multiple queues on one resolved broker target
- support `READ`, `PEEK`, and reserve-oriented processing semantics
- expose a small scheduling primitive that higher-level tasks reuse

Why this exists:

- queue semantics differ by surface (`inbox` is not `ctrl_in`)
- task types should share one watcher foundation rather than reimplementing
  their own loops

### 2.2 BaseTask (`weft/core/tasks/base.py`) [CC-2.2]

**Purpose**: bind a validated TaskSpec to queues, state reporting, control
handling, and process-title updates.

_Implementation mapping_: `weft/core/tasks/base.py` — queue resolution and
queue-config helpers, control handling, state reporting, process-title
formatting, reserved-policy helpers, cleanup, and task-run loops.

Current responsibilities:

- translate `TaskSpec.io` into queue configs
- manage `ctrl_in` and `ctrl_out`
- report task lifecycle changes to `weft.log.tasks`
- maintain TID mappings and process titles
- own reserved-queue policy application
- expose `process_once()` and `run_until_stopped()`

Why this exists:

- state publication should come from one shared path
- control semantics should not drift between task types
- reserved-queue policy belongs to task ownership, not to ad hoc helper code

### 2.3 Specialized Task Types [CC-2.3]

Concrete task classes express different queue behaviors on top of `BaseTask`.

_Implementation mapping_: `weft/core/tasks/consumer.py`,
`weft/core/tasks/observer.py`, `weft/core/tasks/monitor.py`,
`weft/core/tasks/interactive.py`; re-exported from `weft/core/tasks/__init__.py`.

Current task families:

- `Consumer`: reserves inbox messages and executes targets
- `Observer`: peeks without consuming
- `SelectiveConsumer`: conditionally consumes based on a selector
- `Monitor`: forwards while observing
- `SamplingObserver`: observer variant with interval-based sampling

Interactive command sessions reuse the same task/runtime conventions rather than
inventing a second terminal subsystem.

### 2.4 Control and State Expectations [CC-2.4]

Current required control behavior:

- `STOP`, `STATUS`, and `PING` round trips exist for live tasks
- durable `state.status` remains the canonical lifecycle state
- user-facing surfaces may expose derived live `activity` without creating a
  second durable state machine
- process titles stay shell-friendly and reflect durable state plus optional
  live detail
- streaming markers in `weft.state.streaming` reflect live stream ownership for
  result/status surfaces

Why this boundary matters:

- operators need one durable truth and one lightweight live explanation
- control replies should stay off the data plane
- task-local streaming state should remain explicit and inspectable

### 2.5 Execution Flow [CC-2.5]

Current high-level flow:

1. instantiate a concrete task with a validated runtime TaskSpec
2. wire queues and publish startup state
3. reserve or read work
4. dispatch execution through `TaskRunner`
5. apply control and reserved-policy rules
6. publish terminal state and cleanup

_Implementation mapping_: `weft/core/tasks/consumer.py` owns work-item
execution and finalization; `weft/commands/run.py` owns CLI submission and wait
behavior.

Why this stays shared:

- task execution must not bypass the same lifecycle and visibility rules just
  because the target is a function, command, or agent

## 3. TaskRunner (`weft/core/tasks/runner.py`) [CC-3]

**Purpose**: dispatch task execution to the configured runner plugin while
keeping queue semantics and task lifecycle in core Weft code.

_Implementation mapping_: `weft/core/tasks/runner.py` — `TaskRunner`; plugin
loading in `weft/_runner_plugins.py`; runner plugin interface in `weft/ext.py`;
built-in host runner in `weft/core/runners/`.

Why this boundary exists:

- queue ownership should stay in Weft
- execution backends should be swappable
- non-host runtimes should not force the rest of the system into a host-PID-only
  model

### 3.1 Runner Plugin Boundary [CC-3.1]

Current rules:

- runners resolve by name through the plugin surface
- `host` is always available and is the default
- plugins own runtime-specific execution details
- core owns lifecycle, queues, and control semantics

### 3.2 Runtime Handles and Control [CC-3.2]

Current rule:

- control and observability must work through a durable runtime handle, not
  only through a host PID

_Implementation mapping_: `weft/ext.py` `RunnerHandle`, CLI status/control
surfaces in `weft/commands/status.py` and `weft/commands/tasks.py`.

Why this matters:

- some runtimes are not reducible to one host process
- CLI status and control need a cross-runner abstraction that still carries
  concrete identity when available

### 3.3 Validation and Preflight [CC-3.3]

Current validation is layered:

- TaskSpec schema validation checks the shape of `spec.runner`
- capability validation checks whether the runner supports the declared task
  shape
- optional preflight checks runtime availability on the current machine

_Implementation mapping_: `weft/core/runner_validation.py`,
`weft/commands/validate_taskspec.py`.

### 3.4 Monitoring Ownership [CC-3.4]

Current rule:

- monitoring belongs at the runner boundary

That means:

- the host runner uses Weft's psutil-based monitor path
- alternate runners may surface runtime-native monitoring
- status surfaces should prefer runner-native descriptions when available

## Related Documents

- [`02-TaskSpec.md`](02-TaskSpec.md)
- [`04-SimpleBroker_Integration.md`](04-SimpleBroker_Integration.md)
- [`06-Resource_Management.md`](06-Resource_Management.md)
- [`13-Agent_Runtime.md`](13-Agent_Runtime.md)
