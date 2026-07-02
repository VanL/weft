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

## Table of Contents

- [Related Plans](#related-plans)
- [1. TaskSpec (`weft/core/taskspec/model.py`) [CC-1]](#1-taskspec-weftcoretaskspecmodelpy-cc-1)
  - [1.1 Internal State-Machine Helper (`weft/core/state_machines.py`) [CC-1.1]](#11-internal-state-machine-helper-weftcorestate_machinespy-cc-11)
- [2. Task Execution Architecture](#2-task-execution-architecture)
  - [2.1 MultiQueueWatcher (`weft/core/tasks/multiqueue_watcher.py`) [CC-2.1]](#21-multiqueuewatcher-weftcoretasksmultiqueue_watcherpy-cc-21)
  - [2.2 BaseTask (`weft/core/tasks/base.py`) [CC-2.2]](#22-basetask-weftcoretasksbasepy-cc-22)
  - [2.3 Specialized Task Types [CC-2.3]](#23-specialized-task-types-cc-23)
  - [2.4 Control and State Expectations [CC-2.4]](#24-control-and-state-expectations-cc-24)
  - [2.4.1 Runtime Endpoint Registry [CC-2.4.1]](#241-runtime-endpoint-registry-cc-241)
  - [2.5 Execution Flow [CC-2.5]](#25-execution-flow-cc-25)
- [3. TaskRunner (`weft/core/tasks/runner.py`) [CC-3]](#3-taskrunner-weftcoretasksrunnerpy-cc-3)
  - [3.1 Runner Plugin Boundary [CC-3.1]](#31-runner-plugin-boundary-cc-31)
  - [3.2 Runtime Handles and Control [CC-3.2]](#32-runtime-handles-and-control-cc-32)
  - [3.3 Validation and Preflight [CC-3.3]](#33-validation-and-preflight-cc-33)
  - [3.4 Monitoring Ownership [CC-3.4]](#34-monitoring-ownership-cc-34)
- [Related Plans](#related-plans-1)
- [Related Documents](#related-documents)

## Related Plans

- [`docs/plans/2026-06-01-critical-review-remediation-plan.md`](../plans/2026-06-01-critical-review-remediation-plan.md)
- [`docs/plans/2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md`](../plans/2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md)
- [`docs/plans/2026-05-28-docker-container-profiles-plan.md`](../plans/2026-05-28-docker-container-profiles-plan.md)
- [`docs/plans/2026-05-26-service-task-worker-api-plan.md`](../plans/2026-05-26-service-task-worker-api-plan.md)
- [`docs/plans/2026-05-24-monitor-policy-progress-contract-plan.md`](../plans/2026-05-24-monitor-policy-progress-contract-plan.md)
- [`docs/plans/2026-05-23-monitor-cleanup-policy-convergence-plan.md`](../plans/2026-05-23-monitor-cleanup-policy-convergence-plan.md)
- [`docs/plans/2026-05-23-monitor-cleanup-executor-plan.md`](../plans/2026-05-23-monitor-cleanup-executor-plan.md)
- [`docs/plans/2026-05-20-monitor-collation-table-retirement-plan.md`](../plans/2026-05-20-monitor-collation-table-retirement-plan.md)
- [`docs/plans/2026-05-20-monitor-reactor-worker-refactor-plan.md`](../plans/2026-05-20-monitor-reactor-worker-refactor-plan.md)
- [`docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`](../plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md)
- [`docs/plans/2026-05-16-monitor-store-hardening-and-layering-plan.md`](../plans/2026-05-16-monitor-store-hardening-and-layering-plan.md)
- [`docs/plans/2026-05-08-agent-session-and-task-startup-observability-plan.md`](../plans/2026-05-08-agent-session-and-task-startup-observability-plan.md)
- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)
- [`docs/plans/2026-04-06-persistent-agent-runtime-implementation-plan.md`](../plans/2026-04-06-persistent-agent-runtime-implementation-plan.md)
- [`docs/plans/2026-04-07-active-control-main-thread-plan.md`](../plans/2026-04-07-active-control-main-thread-plan.md)
- [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)
- [`docs/plans/2026-04-17-canonical-owner-fence-plan.md`](../plans/2026-04-17-canonical-owner-fence-plan.md)
- [`docs/plans/2026-04-17-heartbeat-service-plan.md`](../plans/2026-04-17-heartbeat-service-plan.md)
- [`docs/plans/2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md`](../plans/2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md)
- [`docs/plans/2026-05-06-terminal-publication-hardening-plan.md`](../plans/2026-05-06-terminal-publication-hardening-plan.md)
- [`docs/plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md`](../plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md)
- [`docs/plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`](../plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md)
- [`docs/plans/2026-05-08-manager-owned-internal-service-supervision-plan.md`](../plans/2026-05-08-manager-owned-internal-service-supervision-plan.md)
- [`docs/plans/2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`](../plans/2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md)
- [`docs/plans/2026-05-09-runtime-liveness-probe-registry-plan.md`](../plans/2026-05-09-runtime-liveness-probe-registry-plan.md)
- [`docs/plans/2026-05-09-prune-path-unification-plan.md`](../plans/2026-05-09-prune-path-unification-plan.md)
- [`docs/plans/2026-05-13-internal-state-machine-helper-plan.md`](../plans/2026-05-13-internal-state-machine-helper-plan.md)
- [`docs/plans/2026-05-15-manager-hot-loop-reduction-plan.md`](../plans/2026-05-15-manager-hot-loop-reduction-plan.md)
- [`docs/plans/2026-05-15-manager-reactor-hot-loop-follow-up-plan.md`](../plans/2026-05-15-manager-reactor-hot-loop-follow-up-plan.md)
- [`docs/plans/2026-05-16-monitor-durable-collation-store-plan.md`](../plans/2026-05-16-monitor-durable-collation-store-plan.md)
- [`docs/plans/2026-05-18-reactive-task-loop-hot-probe-plan.md`](../plans/2026-05-18-reactive-task-loop-hot-probe-plan.md)

## 1. TaskSpec (`weft/core/taskspec/model.py`) [CC-1]

**Purpose**: define execution intent, queue wiring, and mutable runtime state in
one validated model while freezing the parts that should not drift after task
creation.

_Implementation mapping_: `weft/core/taskspec/model.py` — `TaskSpec`,
`SpecSection`, `IOSection`, `StateSection`, `LimitsSection`, `RunnerSection`,
`AgentSection`, `FrozenList`, `FrozenDict`, `resolve_taskspec_payload`,
`apply_bundle_root_to_taskspec_payload`; task lifecycle transition selection
lives in `weft/core/task_lifecycle.py`; supporting parameterization and
run-input validation live in `weft/core/taskspec/parameterization.py` and
`weft/core/taskspec/run_input.py`, and runner-environment materialization lives
in `weft/core/environment_profiles.py`.

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

### 1.1 Internal State-Machine Helper (`weft/core/state_machines.py`) [CC-1.1]

**Purpose**: provide a small internal helper for pure reducer-style transition
tables where the decision can be expressed as current labeled state plus an
evidence snapshot.

_Implementation mapping_: `weft/core/state_machines.py` — `StateMachine`,
`Transition`, and `StateDecision`.

Current contract:

- the helper is internal support code, not a public API
- reducers using it must stay pure: no queue reads, process probes, writes,
  sleeps, logging-as-correctness, or side effects
- inputs are caller-built evidence snapshots or simple values, not live handles
- outputs name selected target state, action, transition ID, and reason; side
  effects stay in the existing owner such as Manager, command helpers,
  BaseTask, or Consumer
- helper construction validates declared states, actions, transition IDs,
  terminal-state outgoing edges, and non-terminal source coverage
- tests can assert structural reachability, transition-ID coverage, state
  coverage, and action coverage explicitly

Why this exists:

- deterministic reducer tables are easier to review and test than scattered
  branching when multiple evidence sources influence a lifecycle decision
- the helper gives tests a common way to prove all declared states and actions
  are reachable and exercised without introducing an external state-machine
  dependency or a second runtime control plane

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
- own a backend-neutral wait seam that uses SimpleBroker's multi-queue
  activity waiter when available and falls back to polling otherwise
- treat native waiter activity as a hard scheduling hint: the next drain may
  run broad inactive-queue discovery for queues that count as ordinary wait
  activity, rather than waiting for the periodic discovery interval
- treat zero-timeout waits as local due-timer boundaries only. They must not
  scan queues. Backends without a native waiter may still perform a bounded
  positive-timeout pending precheck as the portable polling fallback
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
formatting, reserved-policy helpers, cleanup, task-run loops, the private
mechanical worker-lane launcher, and the private worker-result channel used by
reactor-style task implementations.

Current responsibilities:

- translate `TaskSpec.io` into queue configs
- manage `ctrl_in` and `ctrl_out`
- report task lifecycle changes to `weft.log.tasks`
- maintain TID mappings and process titles
- own reserved-queue policy application
- optionally claim and release one stable runtime endpoint name for the live task
- expose `process_once()`, `run_until_stopped()`, and `next_wait_timeout()`
  as the shared task-loop contract
- own `_submit_worker_lane(...)` as the neutral thread/result plumbing for
  local worker lanes, while `_submit_worker_call(...)` remains the
  broker-free wrapper used for ordinary Weft runtime worker callables
- own the broker-free worker-result queue used when a concrete task moves
  blocking work out of the main task reactor thread; this queue is bounded
  and drained in bounded batches so worker progress cannot monopolize a
  reactor turn

Why this exists:

- state publication should come from one shared path
- control semantics should not drift between task types
- reserved-queue policy belongs to task ownership, not to ad hoc helper code

### 2.3 Specialized Task Types [CC-2.3]

Concrete task classes express different queue behaviors on top of `BaseTask`.
Long-lived services that share activation, due-time helpers, and single-flight
worker-lane bookkeeping may inherit from the internal `ServiceTask` layer; that
layer does not own queue readiness, service convergence, or a generic service
turn.

_Implementation mapping_: `weft/core/tasks/consumer.py`,
`weft/core/tasks/observer.py`, `weft/core/tasks/monitor.py`,
`weft/core/monitor/task_monitor.py`, `weft/core/tasks/pipeline.py`,
`weft/core/tasks/debugger.py`, `weft/core/tasks/heartbeat.py`,
`weft/core/tasks/interactive.py`, `weft/core/tasks/service.py`,
`weft/core/tasks/sessions.py`;
task primitives are re-exported from `weft/core/tasks/__init__.py` where
intended for package-level use. The internal `ServiceTask` helper is imported
directly from `weft/core/tasks/service.py`, while the TaskMonitor runtime is
owned by `weft/core/monitor/`.

Current task families:

- `ServiceTask`: internal helper for long-lived task-shaped services. It
  reuses `BaseTask` queue/control behavior, publishes the common
  `task_spawning`/`task_started` activation sequence when asked, exposes
  due-time math, and owns the internal service-worker API for registered
  in-memory worker groups. A service-worker group has a callable target,
  positional/keyword arguments, one local Python input queue, one or more local
  worker threads, typed `ServiceWorkerEvent` publication through
  `BaseTask._publish_worker_result(...)`, cached in-memory snapshots, and
  best-effort stop sentinels during cleanup. Compatibility single-flight lane
  helpers use the same worker registry while concrete services keep their own
  policy and result-commit logic. Service activity is live-only: subclasses may
  update activity for process titles, PONG/status responses, and TID mappings,
  but the service layer suppresses `task_activity` and poll-report rows in
  `weft.log.tasks` so long-lived manager, heartbeat, and TaskMonitor work
  cannot amplify the lifecycle log that cleanup itself consumes. It does not
  implement `process_once()` and does not know about manager leadership,
  service keys, cleanup selection, heartbeat registration, or queue scheduling
  policy.
- `Consumer`: reserves inbox messages on the main task reactor thread, runs
  blocking target execution in a broker-free worker lane, and commits
  outbox/state/reserved-policy effects back on the main thread
- `Observer`: peeks without consuming
- `SelectiveConsumer`: conditionally consumes based on a selector
- `Monitor`: forwards while observing
- `SamplingObserver`: observer variant with interval-based sampling
- `TaskMonitor`: task-log monitor used both by the foreground
  `weft system task-monitor` command and by the manager-supervised internal
  monitor service. Its foreground `scan_once()` path scans `weft.log.tasks`
  with generator-based peek semantics. Its persistent path wakes from its own
  `T{tid}.inbox`, scans task-log history by high-water cursor, and can build a
  Monitor-owned durable collation store from retained task-log rows. The store
  is a derived operational read model: it starts from the logged `TaskSpec`
  summary when present, keeps exact raw task-log message IDs only while those
  raw rows still need exact deletion or retry, emits compact terminal task
  summaries through the configured monitor sink, and may support table-backed
  exact raw deletion after durable Monitor collation; external task-log JSONL
  emit gates family summary/disposition retry, not raw deletion.
  After raw deletion is reconciled, child message rows are physically removed
  from `weft_monitor_task_messages`; after summary, disposition, raw deletion,
  task-local control cleanup, and any required reserved cleanup proof complete,
  the compact parent collation row may also be physically retired. External
  lifecycle retention output is owned by
  `weft/core/monitor/external_log.py`; `WEFT_TASK_MONITOR_LOG_SINK` remains
  monitor operational output, not the external lifecycle-retention contract.
  The external JSONL sink is backed by Python's standard rotating logfile
  handler and defaults to `logs/weft.log` under the Weft project root when a
  mode enables it. The configured external path is resolved at TaskMonitor
  startup and requires a restart to change; the resolved path is re-probed once
  per monitor cycle and the cached health is exposed through PONG and passive
  service-status diagnostics.
  `jsonl_then_delete` builds reusable lifetime reports in
  `weft/core/monitor/lifetime_report.py` and writes them through the external
  JSONL sink or the Monitor-owned `weft_monitor_deferred_writes` outbox before
  exact deletion. Deferred writes are retried by bounded monitor cycles and
  are operational outbox state only.
  The store is not lifecycle truth, result authority, queue truth, or a public
  status dependency. Terminal task summaries and terminal task-local runtime
  cleanup are separate maintenance slices: summary emission records
  `summary_emitted_at_ns`; retained runtime cleanup later uses Monitor-store
  readiness to delete whole standard stale task-local queues. Standard
  `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and `T{tid}.inbox` are stale at
  terminal cleanup time. Standard `T{tid}.outbox` is retained until task-log
  retention age. For old nonterminal service-owner rows, same-service
  registry proof may authorize only standard `T{tid}.ctrl_in` and
  `T{tid}.ctrl_out` cleanup after a stale/superseded disposition; it does
  not turn the row into terminal lifecycle truth. Eligible stale standard
  `T{tid}.reserved` queues are deleted
  through the reserved cleanup policy after monitor-table proof or stale
  no-monitor evidence. Cleanup records `task_control_deleted_at_ns`,
  `reserved_cleanup_checked_at_ns` for required reserved probes, and terminal
  disposition when needed. The monitor also caches `policy_progress`
  summaries so PONG can explain whether each cleanup policy reached a bounded
  waypoint, reached base for now, deferred future work, or was blocked by an
  error without performing live scans on the control path. The monitor exposes
  exactly five top-level cleanup policy identities:
  `task_log.retention`, `monitor_store.lifecycle`,
  `task_local.terminal_runtime`, `task_local.dead_tid`, and
  `runtime_state.retention`. Policy run result values share the internal
  result type in `weft/core/monitor/policies/types.py`; private helper phases
  must not create additional policy identities. The persistent
  monitor also calls the configured task-monitor processor. The persistent
  monitor is a reactor: it owns task-local control, heartbeat registration,
  scheduling, and commits cached diagnostics from worker results. Custom
  processors run from the resulting candidate snapshot through the registered
  `ServiceTask` processor worker group; the reactor commits the processor
  result, checkpoint, and cached diagnostics after the typed worker event
  returns. Built-in cleanup processors run through a separate registered
  TaskMonitor-owned built-in cycle worker group; that group may open fresh
  broker/store handles, fold retained task-log rows into the Monitor store,
  emit configured operational summaries, and delete exact rows through the
  canonical prune implementation.
  Runtime cleanup remains a separate registered maintenance worker group: the
  runtime-cleanup worker may open fresh broker/store handles and delete one
  class of standard stale task-local queues per worker slice. Terminal
  control/inbox cleanup, eligible reserved cleanup, and dead-TID queue cleanup
  are discrete slices with shared policy definitions in
  `weft/core/monitor/policies/runtime_control.py`. The reactor launches each
  slice on the registered runtime-cleanup worker group and only commits cached
  result data after the worker returns; workers must not answer control messages.
  Runtime cleanup records per-slice job counts plus pending/cap/deadline
  diagnostics, and the reactor may launch the next discrete slice only after
  the previous worker result has been applied.
  The launcher asks the persistent monitor for its next wait timeout so the
  monitor sleeps until heartbeat/local due time or task-local input instead of
  polling at the default task-process interval. The supervised monitor builds
  lifecycle and cleanup
  candidate snapshots, including Weft lifecycle anomalies, domain failures,
  stale runtime-state rows, and superseded task-log rows. Its default `delete`
  mode may delete exact safe cleanup candidates only through the canonical
  prune implementation under `weft/core/pruning/`; it must not consume,
  reserve, move, unclaim, or delete active, ambiguous, claimed, malformed,
  unknown, or non-exact lifecycle messages. `report_only` remains available as
  a non-destructive override. Built-in cleanup modes run in the
  TaskMonitor built-in cycle worker group so the reactor remains responsive
  while exact deletes still stay in the canonical prune path. Successful completed
  lifecycle proof does not require a reserved-queue probe; reserved probing is
  for failure-like or suspected-loss cases. `jsonl_then_delete` is the built-in
  log-then-delete mode: it requires collated external logging and the Monitor
  store, emits or durably defers a `task_lifetime_report`, and only then
  applies the same exact delete selected by policy. Raw external
  task-log mode is a separate deletion owner: it emits retained raw rows before
  exact deletion and does not write the Monitor collation tables in that cycle.
- `PipelineTask`: internal orchestrator for first-class linear pipelines
- `PipelineEdgeTask`: generated one-shot edge task for pipeline handoff
- `HeartbeatTask`: manager-supervised internal interval emitter for
  runtime-scoped periodic queue writes. It exposes registration due time,
  idle shutdown, and singleton supersession checks through
  `next_wait_timeout()` and returns from `process_once()` after one bounded
  reactor turn; it does not run a private inner wait loop or task-local queue
  pending probe. Queue readiness is owned by `MultiQueueWatcher`.
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
- task subclasses that override STOP/KILL handling must declare their
  `TaskControlPolicy` in code; the declaration states whether control is
  immediate, deferred, draining, broadcast, or local-only, and whether
  reserved-policy handling is applied by the base class, by the subclass, or is
  intentionally not applicable
- handled raw and structured STOP/KILL commands must produce a `ctrl_out`
  acknowledgement, echo `request_id` when present, and must not emit duplicate
  terminal state events when the task is already terminal

Why this boundary matters:

- operators need one durable truth and one lightweight live explanation
- control replies should stay off the data plane
- task-local streaming state should remain explicit and inspectable

_Implementation mapping_: `weft/core/tasks/base.py` owns
`TaskControlPolicy`, default STOP/KILL behavior, late-terminal guards, and the
shared `process_once()`/`run_until_stopped()`/`next_wait_timeout()` task-loop
contract;
specialized policies live on `Manager`, `Consumer`, `PipelineTask`, and
`Monitor`.

Implementation plan backlinks:
[`2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md`](../plans/2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md);
[`2026-05-15-task-reactor-and-evidence-worker-plan.md`](../plans/2026-05-15-task-reactor-and-evidence-worker-plan.md);
[`2026-05-20-service-task-shared-reactor-extraction-plan.md`](../plans/2026-05-20-service-task-shared-reactor-extraction-plan.md);
[`2026-05-26-service-task-worker-api-plan.md`](../plans/2026-05-26-service-task-worker-api-plan.md).

### 2.4.1 Runtime Endpoint Registry [CC-2.4.1]

**Purpose**: expose one thin discovery primitive for long-lived tasks that want
a stable project-local name.

_Implementation mapping_: `weft/core/endpoints.py` — `EndpointRecord`,
`ResolvedEndpoint`, `list_resolved_endpoints()`, `resolve_endpoint()`;
`weft/core/tasks/base.py` — `register_endpoint_name()`,
`unregister_endpoint_name()`;
`weft/helpers/__init__.py` — `canonical_owner_tid()`;
`weft.state.endpoints` — runtime queue used for the registry records.

Current rules:

- registration is explicit and opt-in; unnamed tasks remain the default
- one live task currently owns at most one active named-endpoint claim at a
  time
- endpoint records point at ordinary task-local queues (`inbox`, `outbox`,
  `ctrl_in`, `ctrl_out`)
- when duplicate live claimants exist for the same endpoint name, discovery
  remains conflict-tolerant: the lowest live claimant TID is canonical for
  resolution, and the duplicate count stays observable through
  `live_candidates`
- names under `_weft.` are reserved for Weft-owned internal runtime services;
  public naming surfaces such as `weft run --name` reject them
- the current shipped reserved internal endpoint is `_weft.heartbeat`, owned by
  the built-in heartbeat service
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

_Implementation mapping_: `weft/core/tasks/base.py` owns the shared task-loop
entry points and worker-result reactor lane. While worker lanes are active,
`BaseTask.wait_for_activity()` bounds queue waits to the fast task-reactor wake
cap so child/process completion is observed promptly without task-specific
poll loops. Worker-result delivery uses a bounded local Python queue and a
per-turn drain budget; full queues apply backpressure to worker lanes rather
than growing process memory without bound. `weft/core/tasks/multiqueue_watcher.py`
owns queue readiness and lets task subclasses narrow what counts as wait
activity when a queue already contains work owned by the current reactor turn,
such as a Consumer reserved message while its worker lane is active.
`weft/core/launcher.py` honors `next_wait_timeout()` and pending worker
activity for spawned task processes; `weft/core/tasks/consumer.py` owns
work-item reservation, worker dispatch,
main-thread finalization, and active control; `weft/core/tasks/runner.py` owns
runner dispatch; `weft/cli/run.py` owns CLI submission and wait behavior.

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
`weft/core/runners/`; optional first-party runner extensions in
`extensions/weft_docker`, `extensions/weft_macos_sandbox`, and
`extensions/weft_microsandbox`.

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
- plugins may materialize runner-specific declarative defaults before runner
  construction, but core still sees ordinary runner options, env, and working
  directory values. The first-party Docker extension owns
  `spec.runner.options.container_profile` for command tasks; core does not
  interpret Docker profile files. The first-party Microsandbox extension owns
  `spec.runner.options.mode`, `image`, `executable`, `network`,
  `workspace_mode`, `mounts`, `cwd`, and `sandbox_name_prefix`; core does not
  interpret Microsandbox images or guest executable paths.
- core owns lifecycle, queues, and control semantics

Current first-party optional runner names:

- `docker`: command tasks and Docker-backed one-shot `provider_cli` agent tasks
  for providers with shipped image recipes.
- `macos-sandbox`: macOS `sandbox-exec` command tasks only.
- `microsandbox`: disposable Microsandbox command tasks and one-shot
  `provider_cli` agent tasks. It rejects function, interactive, persistent,
  and long-lived agent-session execution in the first shipped slice.

Stability note: `weft.ext` is the declared plugin contract, but the shipped
first-party runner plugins (`docker`, `macos-sandbox`, `microsandbox`) also
import `weft.core` internals that carry no stability guarantee. Third-party
runner plugins are not yet supported at arm's length and should pin an exact
weft version.

### 3.2 Runtime Handles and Control [CC-3.2]

Current rule:

- control and observability must work through a durable runtime handle, not
  only through a host PID
- the runtime handle JSON contract is exactly:
  `runner`, `kind`, `id`, `control`, `observations`, `metadata`
- `control.authority` defines who may act on the handle. `host-pid` means
  `observations.host_pids` are scoped host PIDs; `runner` means the named
  runner plugin owns control; `external-supervisor` means Weft records
  identity but does not send direct runtime control.
- task processes publish a `host-pid` runtime handle for their own process when
  no runner-specific runtime handle exists. This keeps endpoint liveness and
  control proof on the runtime-handle contract rather than legacy top-level PID
  fields.
- legacy handle keys such as `runner_name`, `runtime_id`, and top-level
  `host_pids` are invalid at runtime-contract boundaries
- manager records use the same `runtime_handle` shape. Detached host launch
  publishes a scoped host-process handle; supervised/container managers must
  be given an explicit handle or use `external-supervisor`. Extensions may
  register process-local runtime liveness probes for specific handle runners;
  inconclusive or missing probes do not replace the generic heartbeat boundary.

_Implementation mapping_: `weft/ext.py` `RunnerHandle`; `weft/core/tasks/base.py`
`register_runtime_handle()`; CLI status/control surfaces in
`weft/commands/status.py` and `weft/commands/tasks.py`.

Plan backlink:
[`docs/plans/2026-04-24-runtime-handle-authority-migration-plan.md`](../plans/2026-04-24-runtime-handle-authority-migration-plan.md).
[`docs/plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md`](../plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md)
hardens manager-owned singleton force-kill authority so raw task-log PIDs are
not treated as scoped runtime-handle proof.
Manager singleton duplicate cleanup may force-reap scoped host PIDs from a
runtime handle, but must not derive force-kill authority from caller metadata or
legacy logged PID fields alone.

Why this matters:

- some runtimes are not reducible to one host process
- CLI status and control need a cross-runner abstraction that still carries
  concrete identity when available
- Microsandbox uses `RunnerHandle.kind == "sandboxed-process"` and
  `RunnerHandle.id` as the sandbox identity. Any host PID observations are
  best-effort evidence only; task lifecycle truth remains in queues and state
  logs.

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
- runner diagnostics are operational metadata about process/session startup,
  runtime handles, exits, and private readiness handshakes; they are not task
  lifecycle truth

That means:

- the host runner and session helpers use Weft's psutil-based monitor path
- alternate runners may surface runtime-native monitoring
- status surfaces should prefer runner-native descriptions when available
- terminal task-log events may include bounded `runner_diagnostics` when a
  runner or runtime boundary fails, but status and result code must continue to
  derive lifecycle state from lifecycle events, terminal control evidence, and
  result evidence rather than from diagnostics alone

_Implementation mapping_: `weft/core/resource_monitor.py`,
`weft/core/runner_diagnostics.py`, `weft/core/runners/host.py`,
`weft/core/runners/subprocess_runner.py`, `weft/core/tasks/sessions.py`,
`weft/core/tasks/consumer.py`. Monitor-owned durable collation is implemented
by `weft/core/monitor/store.py`, `weft/core/monitor/sql.py`,
`weft/core/monitor/collation.py`, and `weft/core/monitor/task_monitor.py`.
Terminal summary readiness and control-cleanup readiness live in the Monitor
store/SQL layer; physical task-local runtime queue cleanup remains at the
TaskMonitor runtime boundary.

## Related Plans

- [`docs/plans/2026-06-29-manager-task-spawned-retention-policy-plan.md`](../plans/2026-06-29-manager-task-spawned-retention-policy-plan.md)
- [`docs/plans/2026-06-17-microsandbox-runner-plan.md`](../plans/2026-06-17-microsandbox-runner-plan.md)
- [`docs/plans/2026-06-01-critical-review-remediation-plan.md`](../plans/2026-06-01-critical-review-remediation-plan.md)
- [`docs/plans/2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md`](../plans/2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md)
- [`docs/plans/2026-05-29-task-monitor-general-lifetime-reporting-plan.md`](../plans/2026-05-29-task-monitor-general-lifetime-reporting-plan.md)
- [`docs/plans/2026-05-30-task-monitor-mode-and-rotating-log-plan.md`](../plans/2026-05-30-task-monitor-mode-and-rotating-log-plan.md)
- [`docs/plans/2026-05-30-task-monitor-external-log-health-plan.md`](../plans/2026-05-30-task-monitor-external-log-health-plan.md)
- [`docs/plans/2026-05-29-reliability-and-doc-fixes-plan.md`](../plans/2026-05-29-reliability-and-doc-fixes-plan.md)
- [`docs/plans/2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md`](../plans/2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md)
- [`docs/plans/2026-05-20-monitor-collation-table-retirement-plan.md`](../plans/2026-05-20-monitor-collation-table-retirement-plan.md)
- [`docs/plans/2026-05-20-monitor-fair-cleanup-scheduling-plan.md`](../plans/2026-05-20-monitor-fair-cleanup-scheduling-plan.md)
- [`docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`](../plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md)
- [`docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`](../plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md)
- [`docs/plans/2026-05-19-task-monitor-bounded-control-cleanup-plan.md`](../plans/2026-05-19-task-monitor-bounded-control-cleanup-plan.md)
- [`docs/plans/2026-05-18-reactive-task-loop-hot-probe-plan.md`](../plans/2026-05-18-reactive-task-loop-hot-probe-plan.md)
- [`docs/plans/2026-06-09-evaluation-findings-remediation-plan.md`](../plans/2026-06-09-evaluation-findings-remediation-plan.md)

## Related Documents

- [`02-TaskSpec.md`](02-TaskSpec.md)
- [`04-SimpleBroker_Integration.md`](04-SimpleBroker_Integration.md)
- [`06-Resource_Management.md`](06-Resource_Management.md)
- [`13-Agent_Runtime.md`](13-Agent_Runtime.md)
