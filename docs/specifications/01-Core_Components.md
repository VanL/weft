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

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)
- [`docs/plans/2026-04-06-persistent-agent-runtime-implementation-plan.md`](../plans/2026-04-06-persistent-agent-runtime-implementation-plan.md)
- [`docs/plans/2026-04-07-active-control-main-thread-plan.md`](../plans/2026-04-07-active-control-main-thread-plan.md)
- [`docs/plans/2026-04-16-review-findings-remediation-plan.md`](../plans/2026-04-16-review-findings-remediation-plan.md)
- [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)

## 1. TaskSpec (`weft/core/taskspec.py`) [CC-1]

**Purpose**: define execution intent, queue wiring, and mutable runtime state in
one validated model while freezing the parts that should not drift after task
creation.

_Implementation mapping_: `weft/core/taskspec.py` — `TaskSpec`, `SpecSection`,
`IOSection`, `StateSection`, `LimitsSection`, `RunnerSection`, `AgentSection`,
`FrozenList`, `FrozenDict`, `resolve_taskspec_payload`,
`apply_bundle_root_to_taskspec_payload`; supporting parameterization and
run-input validation live in `weft/core/spec_parameterization.py` and
`weft/core/spec_run_input.py`, and runner-environment materialization lives in
`weft/core/environment_profiles.py`.

Current contract:

- `spec` and `io` are immutable after TaskSpec creation
- `state` and metadata remain mutable
- stored templates may omit runtime-expanded values such as `tid` and the
  resolved queue names inside `io`
- stored templates may also declare explicit submission-time shaping through
  `spec.parameterization` and `spec.run_input`
- the manager resolves runtime TaskSpecs from templates at spawn time
- runner selection lives in `spec.runner`
- `spec.runner.environment_profile_ref` participates in runner environment
  materialization before capability and plugin checks
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
- optionally claim and release one stable runtime endpoint name for the live task
- expose `process_once()` and `run_until_stopped()`

Why this exists:

- state publication should come from one shared path
- control semantics should not drift between task types
- reserved-queue policy belongs to task ownership, not to ad hoc helper code

### 2.3 Specialized Task Types [CC-2.3]

Concrete task classes express different queue behaviors on top of `BaseTask`.

_Implementation mapping_: `weft/core/tasks/consumer.py`,
`weft/core/tasks/observer.py`, `weft/core/tasks/monitor.py`,
`weft/core/tasks/pipeline.py`, `weft/core/tasks/debugger.py`,
`weft/core/tasks/interactive.py`, `weft/core/tasks/sessions.py`;
re-exported from `weft/core/tasks/__init__.py`.

Current task families:

- `Consumer`: reserves inbox messages and executes targets
- `Observer`: peeks without consuming
- `SelectiveConsumer`: conditionally consumes based on a selector
- `Monitor`: forwards while observing
- `SamplingObserver`: observer variant with interval-based sampling
- `PipelineTask`: internal orchestrator for first-class linear pipelines
- `PipelineEdgeTask`: generated one-shot edge task for pipeline handoff
- `Debugger`: in-process diagnostic command surface for interactive debugging

Interactive command sessions reuse the same task/runtime conventions rather
than inventing a second terminal subsystem, and long-lived agent sessions reuse
the same session helpers in `weft/core/tasks/sessions.py`.

### 2.4 Control and State Expectations [CC-2.4]

Current required control behavior:

- `STOP`, `KILL`, `STATUS`, and `PING` round trips exist for live tasks
- `PAUSE` and `RESUME` are supported on task types that opt into live pausing
- durable `state.status` remains the canonical lifecycle state
- user-facing surfaces may expose derived live `activity` without creating a
  second durable state machine
- process titles stay shell-friendly and reflect durable state plus optional
  live detail
- streaming markers in `weft.state.streaming` reflect live stream ownership for
  result/status surfaces
- streaming markers are runtime-only ownership hints and must clear before a
  persistent task returns to `waiting`

Why this boundary matters:

- operators need one durable truth and one lightweight live explanation
- control replies should stay off the data plane
- task-local streaming state should remain explicit and inspectable

### 2.4.1 Runtime Endpoint Registry [CC-2.4.1]

**Purpose**: expose one thin discovery primitive for long-lived tasks that want
a stable project-local name.

_Implementation mapping_: `weft/core/endpoints.py` — `EndpointRecord`,
`ResolvedEndpoint`, `list_resolved_endpoints()`, `resolve_endpoint()`;
`weft/core/tasks/base.py` — `register_endpoint_name()`,
`unregister_endpoint_name()`;
`weft.state.endpoints` — runtime queue used for the registry records.

Current rules:

- registration is explicit and opt-in; unnamed tasks remain the default
- one live task currently owns at most one active named-endpoint claim at a
  time
- endpoint records point at ordinary task-local queues (`inbox`, `outbox`,
  `ctrl_in`, `ctrl_out`)
- the registry is discovery only; it does not introduce a second transport,
  task kind, or workflow router
- endpoint records are runtime-only hints and must not become durable domain
  state inside Weft core

Why this boundary exists:

- higher-level systems often need a stable way to find a long-lived task
- queue ownership must stay visible and task-local
- service semantics such as request schemas, fan-out, or replacement policy
  belong above this primitive

### 2.5 Execution Flow [CC-2.5]

Current high-level flow:

1. instantiate a concrete task with a validated runtime TaskSpec
2. wire queues and publish startup state
3. reserve or read work
4. dispatch execution through `TaskRunner`
5. apply control and reserved-policy rules
6. publish terminal state and cleanup

_Implementation mapping_: `weft/core/tasks/consumer.py` owns work-item
execution and finalization; `weft/core/tasks/runner.py` owns runner dispatch;
`weft/commands/run.py` owns CLI submission and wait behavior.

Why this stays shared:

- task execution must not bypass the same lifecycle and visibility rules just
  because the target is a function, command, or agent

## 3. TaskRunner (`weft/core/tasks/runner.py`) [CC-3]

**Purpose**: dispatch task execution to the configured runner plugin while
keeping queue semantics and task lifecycle in core Weft code.

_Implementation mapping_: `weft/core/tasks/runner.py` — `TaskRunner`; runner
environment-profile materialization in `weft/core/environment_profiles.py`;
task runner validation in `weft/core/agents/validation.py` and
`weft/core/runner_validation.py`; plugin loading in `weft/_runner_plugins.py`;
runner plugin interface in `weft/ext.py`; built-in host runner in
`weft/core/runners/`.

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

_Implementation mapping_: `weft/ext.py` `RunnerHandle`; `weft/core/tasks/base.py`
`register_runtime_handle()`; CLI status/control surfaces in
`weft/commands/status.py` and `weft/commands/tasks.py`.

Why this matters:

- some runtimes are not reducible to one host process
- CLI status and control need a cross-runner abstraction that still carries
  concrete identity when available

### 3.3 Validation and Preflight [CC-3.3]

Current validation is layered:

- TaskSpec schema validation checks the shape of `spec.runner`
- runner environment-profile validation and materialization happen before
  runner capability or plugin preflight checks
- capability validation checks whether the runner supports the declared task
  shape
- optional preflight checks runtime availability on the current machine when an
  operator explicitly asks for validation
- ordinary `weft run` submission does not add a hidden "can this binary run"
  gate; the task attempts startup on the canonical execution path and reports
  concrete startup failures there

_Implementation mapping_: `weft/core/runner_validation.py`,
`weft/commands/validate_taskspec.py`,
`weft/core/agents/validation.py`.

### 3.4 Monitoring Ownership [CC-3.4]

Current rule:

- monitoring belongs at the runner boundary

That means:

- the host runner and session helpers use Weft's psutil-based monitor path
- alternate runners may surface runtime-native monitoring
- status surfaces should prefer runner-native descriptions when available

_Implementation mapping_: `weft/core/resource_monitor.py`,
`weft/core/runners/host.py`, `weft/core/runners/subprocess_runner.py`,
`weft/core/tasks/sessions.py`, `weft/core/tasks/consumer.py`.

## Related Documents

- [`02-TaskSpec.md`](02-TaskSpec.md)
- [`04-SimpleBroker_Integration.md`](04-SimpleBroker_Integration.md)
- [`06-Resource_Management.md`](06-Resource_Management.md)
- [`13-Agent_Runtime.md`](13-Agent_Runtime.md)
