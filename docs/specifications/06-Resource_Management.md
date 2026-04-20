# Resource Management and Error Handling

This document describes the current resource-monitoring and error-handling
contract. The current system favors simple, explicit termination and visible
failure over complex partial mitigation.

_Implementation snapshot_: resource limits are enforced at the runner
boundary. The default host-path monitor is psutil-based, but runner plugins
may also map limits into native runtime controls. Limit violations become task
failures that are visible through state, control, and reserved-queue behavior.

_Implementation mapping_: `weft/core/resource_monitor.py`,
`weft/core/runners/host.py`, `weft/core/runners/subprocess_runner.py`,
`weft/core/tasks/runner.py`, `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`, `weft/ext.py`, `weft/_runner_plugins.py`.

See also:

- planned companion:
  [`06A-Resource_Management_Planned.md`](06A-Resource_Management_Planned.md)
- system guarantees:
  [`07-System_Invariants.md`](07-System_Invariants.md)

## Resource Management [RM-0]

Current Weft resource management is monitor-and-react at the runner boundary:

- sample live process metrics
- compare against configured limits
- terminate when a hard violation is confirmed
- surface the failure durably

The reason for this design is clarity. Hard termination is easier to explain and
audit than a mix of throttling, grace-period heuristics, and silent recovery.

### 1. Memory Management [RM-1]

Current behavior:

- memory usage is sampled from the runtime process tree on psutil-backed
  runtimes and from runner-native stats on Docker-backed runtimes
- configured memory limits are treated as hard limits
- violations terminate the task and surface a limit failure

_Implementation mapping_: `weft/core/resource_monitor.py`
`ResourceMonitor`, `PsutilResourceMonitor.check_limits`;
`weft/core/taskspec/model.py`
`LimitsSection.memory_mb`.

### 2. CPU Management [RM-2]

Current behavior:

- CPU usage is evaluated over a rolling sample window on psutil-backed
  runtimes
- the host path treats sustained over-limit CPU as a hard violation
- Docker-backed runners map CPU limits into native runtime quotas instead of
  relying on the psutil window
- the response is termination, not throttling

_Implementation mapping_: `weft/core/resource_monitor.py`
`PsutilResourceMonitor._is_sustained_cpu_violation`,
`_get_average_cpu`; `weft/core/taskspec/model.py`
`LimitsSection.cpu_percent`.

### 3. File Descriptor Management [RM-3]

Current behavior:

- open-file counts are sampled when the backend can provide them
- psutil-backed runtimes enforce configured fd limits as hard limits
- Docker-backed runners map fd limits into native runtime ulimits

_Implementation mapping_: `weft/core/resource_monitor.py`
`PsutilResourceMonitor._open_file_count`; `weft/core/taskspec/model.py`
`LimitsSection.max_fds`.

### 4. Network Connection Management [RM-4]

Current behavior:

- connection counts are sampled when the backend can provide them
- psutil-backed runtimes enforce configured connection limits as hard limits
- Docker-backed runners currently support only `max_connections=0`, which maps
  to network isolation; other values are rejected

_Implementation mapping_: `weft/core/resource_monitor.py`
`PsutilResourceMonitor._connection_count`; `weft/core/taskspec/model.py`
`LimitsSection.max_connections`.

## Resource Monitoring Implementation [RM-5]

Resource monitoring lives at the runner boundary rather than in a separate
global service.

Why:

- limits apply to concrete runtimes
- monitoring ownership should stay close to runtime creation and teardown
- runner plugins also own runtime-specific stop, kill, and describe hooks
- alternate runners may need alternate monitoring backends or native runtime
  controls

### Default psutil-Based Monitor [RM-5.1]

The current default monitor name is `weft.core.resource_monitor.ResourceMonitor`;
it aliases the psutil-backed `PsutilResourceMonitor` implementation.

_Implementation mapping_: `weft/core/resource_monitor.py`
(`ResourceMetrics`, `BaseResourceMonitor`, `ResourceMonitor`,
`PsutilResourceMonitor`, `load_resource_monitor`);
`weft/core/runners/host.py` (`HostTaskRunner.run_with_hooks`,
`HostRunnerPlugin.stop`, `HostRunnerPlugin.kill`, `HostRunnerPlugin.describe`);
`weft/core/runners/subprocess_runner.py` (`run_monitored_subprocess`);
`weft/ext.py` (`RunnerPlugin`, `RunnerHandle`); `weft/_runner_plugins.py`
(`get_runner_plugin`, `require_runner_plugin`).

Current responsibilities:

- start and stop monitoring for a live runtime
- capture the latest resource metrics
- compare those metrics to configured limits
- retain recent metrics so the task can publish peak or recent values
- surface runner-specific stop/kill/describe hooks through the active plugin

## Error Handling

### Unified Reservation Pattern

The current error-handling model is queue-first:

- in-flight work is visible in reserved queues
- successful completion clears or finalizes reserved state
- failures leave enough state behind for inspection or explicit recovery

This exists so failure never disappears into a private runtime path.

### Error Categories and State

Current terminal error classes exposed through state include:

- ordinary failure
- timeout
- limit violation
- external stop/kill

The CLI and status surfaces reconstruct these from task-log events and task
state rather than from a separate error database.

### Recovery Boundary

Current recovery is explicit and operator-driven:

- inspect reserved queues
- move or requeue messages manually with queue commands
- rerun work intentionally

There is no built-in automatic retry or recovery framework in the current
contract.

## Security Considerations

### Command Execution Safety

Weft validates task shape and runner capability, but it does not currently
enforce a global command allowlist. The trust model is user-level operation in
an already trusted environment.

### Resource Isolation

Current resource isolation is observe-and-react on the host path, with
backend-specific controls in some runner plugins. The contract does not promise
a universal cgroup or container sandbox, and it does not require every runner
to expose the same native controls.

### Environment Variables

Current runners merge environment data needed for execution, but there is no
strong protected-variable policy in the canonical contract yet. Callers should
not assume that the runtime sanitizes or rejects every risky environment
override.

### Agent Constraints

Agent tasks are constrained primarily by TaskSpec shape, runner capability, and
the configured runtime/tool set. There is no current global registry/allowlist
contract for agent actions.

### Large Output Safety

Large task output handling is current behavior, but it is owned by the task
runtime rather than by a generic broker wrapper. Output spill paths therefore
need to be treated as task-runtime artifacts, not as arbitrary caller-supplied
filesystem destinations.

## Scope Boundary

Soft limits, throttling, explicit sandboxing, richer validation helpers, and
automatic retry patterns live in the companion doc. Backend-specific native
controls stay here only when they are already shipped and observable:

- [`06A-Resource_Management_Planned.md`](06A-Resource_Management_Planned.md)

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)

## Related Documents

- [`01-Core_Components.md`](01-Core_Components.md)
- [`07-System_Invariants.md`](07-System_Invariants.md)
