# Resource Management and Error Handling

This document describes the current resource-monitoring and error-handling
contract. The current system favors simple, explicit termination and visible
failure over complex partial mitigation.

_Implementation snapshot_: the host runner enforces limits through a
psutil-based monitor path. Limit violations become task failures that are
visible through state, control, and reserved-queue behavior.

_Implementation mapping_: `weft/core/resource_monitor.py`,
`weft/core/runners/host.py`, `weft/core/tasks/runner.py`,
`weft/core/tasks/base.py`, `weft/core/tasks/consumer.py`.

See also:

- planned companion:
  [`06A-Resource_Management_Planned.md`](06A-Resource_Management_Planned.md)
- system guarantees:
  [`07-System_Invariants.md`](07-System_Invariants.md)

## Resource Management [RM-0]

Current Weft resource management is monitor-and-react:

- sample live process metrics
- compare against configured limits
- terminate when a hard violation is confirmed
- surface the failure durably

The reason for this design is clarity. Hard termination is easier to explain and
audit than a mix of throttling, grace-period heuristics, and silent recovery.

### 1. Memory Management [RM-1]

Current behavior:

- memory usage is sampled from the runtime process tree
- configured memory limits are treated as hard limits
- violations terminate the task and surface a limit failure

_Implementation mapping_: `weft/core/resource_monitor.py`
`PsutilResourceMonitor.check_limits`; `weft/core/taskspec.py`
`LimitsSection.memory_mb`.

### 2. CPU Management [RM-2]

Current behavior:

- CPU usage is evaluated over a rolling sample window
- the host runner treats sustained over-limit CPU as a hard violation
- the response is termination, not throttling

_Implementation mapping_: `weft/core/resource_monitor.py`
`PsutilResourceMonitor._is_sustained_cpu_violation`,
`_get_average_cpu`; `weft/core/taskspec.py`
`LimitsSection.cpu_percent`.

### 3. File Descriptor Management [RM-3]

Current behavior:

- open-file counts are sampled when the backend can provide them
- configured fd limits are enforced as hard limits

_Implementation mapping_: `weft/core/resource_monitor.py`
`PsutilResourceMonitor._open_file_count`; `weft/core/taskspec.py`
`LimitsSection.max_fds`.

### 4. Network Connection Management [RM-4]

Current behavior:

- connection counts are sampled when the backend can provide them
- configured connection limits are enforced as hard limits

_Implementation mapping_: `weft/core/resource_monitor.py`
`PsutilResourceMonitor._connection_count`; `weft/core/taskspec.py`
`LimitsSection.max_connections`.

## Resource Monitoring Implementation [RM-5]

Resource monitoring lives at the runner boundary rather than in a separate
global service.

Why:

- limits apply to concrete runtimes
- monitoring ownership should stay close to runtime creation and teardown
- alternate runners may need alternate monitoring backends

### Default psutil-Based Monitor [RM-5.1]

The current default monitor is `PsutilResourceMonitor`.

_Implementation mapping_: `weft/core/resource_monitor.py`
(`ResourceMetrics`, `BaseResourceMonitor`, `PsutilResourceMonitor`,
`load_resource_monitor`); `weft/core/runners/host.py`
(`HostTaskRunner.run_with_hooks`).

Current responsibilities:

- start and stop monitoring for a live runtime
- capture the latest resource metrics
- compare those metrics to configured limits
- retain recent metrics so the task can publish peak or recent values

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

Current resource isolation is observe-and-react, not sandboxing. The runtime
can terminate over-limit work, but it does not promise cgroup or container
isolation as part of the current contract.

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
automatic retry patterns live in the companion doc:

- [`06A-Resource_Management_Planned.md`](06A-Resource_Management_Planned.md)

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)

## Related Documents

- [`01-Core_Components.md`](01-Core_Components.md)
- [`07-System_Invariants.md`](07-System_Invariants.md)
