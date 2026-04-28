# System Invariants and Constraints

This document records the guarantees the current implementation must preserve.
It is intentionally current-state only: if Weft does not actually enforce a
guarantee today, that idea belongs in the companion planned doc instead.

See also:

- planned companion:
  [`07A-System_Invariants_Planned.md`](07A-System_Invariants_Planned.md)
- current task/runtime contracts:
  [`01-Core_Components.md`](01-Core_Components.md),
  [`05-Message_Flow_and_State.md`](05-Message_Flow_and_State.md)

## System Invariants

### Immutability Invariants

_Implementation mapping_: `weft/core/taskspec/model.py`.

- **IMMUT.1**: `TaskSpec.spec` is immutable after creation
- **IMMUT.2**: `TaskSpec.io` is immutable after creation
- **IMMUT.3**: resolved `TaskSpec.tid` is immutable and unique
- **IMMUT.4**: runtime state and metadata remain mutable

### State Machine Invariants

_Implementation mapping_: `weft/core/taskspec/model.py` state validators and
`TaskSpec.set_status()`.

- **STATE.1**: valid transitions are `created -> spawning|failed|cancelled`,
  `spawning -> running|completed|failed|timeout|cancelled|killed`, and
  `running -> completed|failed|timeout|cancelled|killed`
- **STATE.2**: terminal states do not transition back to non-terminal states
- **STATE.3**: running state requires `started_at`
- **STATE.4**: terminal states require `completed_at` once execution has started
- **STATE.5**: `completed_at` is later than `started_at` when both exist

### Queue Invariants

_Implementation mapping_: `weft/core/taskspec/model.py`, `weft/core/tasks/base.py`,
`weft/_constants.py`.

- **QUEUE.1**: every task has one inbox, reserved queue, and outbox
- **QUEUE.2**: every task has one `ctrl_in` and `ctrl_out`
- **QUEUE.3**: default task-local queue names are `T{tid}.…`
- **QUEUE.4**: delivery uses reserve/claim semantics rather than ad hoc
  destructive reads
- **QUEUE.5**: the active reserved message, if present, is treated as the
  single in-flight work item for that task
- **QUEUE.6**: reserved-policy handling is explicit: `keep` leaves the
  reserved message in place, `requeue` moves it back to inbox, and `clear`
  deletes it

### Resource Invariants

_Implementation mapping_: `weft/core/taskspec/model.py`, `weft/core/resource_monitor.py`,
`weft/core/tasks/consumer.py`, `weft/core/tasks/sessions.py`,
`weft/core/runners/host.py`, `weft/core/runners/subprocess_runner.py`.

- **RES.1**: limits live under `spec.limits`
- **RES.2**: memory limits must be positive when present
- **RES.3**: CPU limits stay in the valid percent range when present
- **RES.4**: fd limits are positive when present
- **RES.5**: current metrics never exceed the recorded peak metrics
- **RES.6**: confirmed resource violations terminate the task

### Execution Invariants

_Implementation mapping_: `weft/core/tasks/consumer.py`,
`weft/core/runners/host.py`, `weft/core/runners/subprocess_runner.py`,
`weft/core/taskspec/model.py`, `weft/core/tasks/sessions.py`, `weft/ext.py`.

- **EXEC.1**: each work item is executed exactly once per successful reservation
  path
- **EXEC.2**: timeout enforcement happens through the active runner loop
- **EXEC.3**: TID mappings do not publish top-level host PID fields. Runtime
  identity is published through `runtime_handle`; scoped host PIDs, when
  available, live under `runtime_handle.observations.host_pids`.
- **EXEC.4**: return-code publication belongs to terminal execution outcomes

### Observability Invariants

_Implementation mapping_: `weft/core/tasks/base.py`,
`weft/commands/status.py`, `weft/_constants.py`.

- **OBS.1**: lifecycle changes are written to `weft.log.tasks`
- **OBS.2**: Weft does not use a separate state database for task lifecycle
- **OBS.3**: task state remains observable through task-local queues and the
  global task log
- **OBS.4**: process titles follow `weft-{context_short}-{tid_short}:{name}:{status}[:details]`
- **OBS.5**: TID short form uses the low-order digits from the resolved
  19-digit TID
- **OBS.6**: TID mappings are written to `weft.state.tid_mappings`
- **OBS.7**: process-title segments are sanitized for shell-safe use
- **OBS.8**: process titles stay within the allowed character vocabulary
- **OBS.9**: endpoint names under `_weft.` are reserved for Weft-owned
  internal runtime services and are not claimable from public naming surfaces
- **OBS.10**: durable task-log history may replay basic lifecycle status even
  when an old or invalid runtime-handle payload is ignored. Runtime control and
  liveness probes must reject invalid handle shapes instead of inferring
  authority from legacy PID fields.

### Implementation Invariants

_Implementation mapping_: `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`, `weft/core/launcher.py`,
`weft/core/runners/host.py`, `weft/_constants.py`.

- **IMPL.1**: exit code `124` means timeout
- **IMPL.2**: queue payload size is bounded by the broker's configured message
  limit
- **IMPL.3**: oversized task output is spilled outside the queue payload when
  the runtime needs that path
- **IMPL.4**: large-output references carry enough metadata for inspection
- **IMPL.5**: child processes recreate broker-connected state instead of sharing
  inherited connections
- **IMPL.6**: spawned child process creation uses spawn-style multiprocessing
  context
- **IMPL.7**: public submission surfaces do not authorize internal runtime
  class selection through stored TaskSpec metadata alone; internal runtime
  selection travels on the manager-owned spawn envelope

### Manager Invariants

_Implementation mapping_: `weft/core/manager.py`,
`weft/core/manager_runtime.py`, `weft/commands/manager.py`,
`weft/cli/run.py`, `weft/commands/serve.py`,
`weft/core/spawn_requests.py`, `weft/helpers.py`,
`weft/manager_detached_launcher.py`, `weft/manager_process.py`.

- **MANAGER.1**: managers are task-shaped runtimes with a long-lived dispatcher
  loop
- **MANAGER.2**: manager TIDs follow the same TID rules as other tasks
- **MANAGER.3**: managers register themselves in `weft.state.managers`
- **MANAGER.4**: spawn-request message ID becomes child task TID
- **MANAGER.5**: managers spawn child tasks through the same overall task model
- **MANAGER.6**: one shared manager bootstrap path owns startup and
  foreground-serve behavior for `weft run`, `weft manager start`, and
  `weft manager serve`
- **MANAGER.7**: managers obey the same control semantics as other tasks, with
  graceful drain on normal termination signals
- **MANAGER.8**: live canonical managers converge on one lowest-TID leader per
  context; non-leaders yield or drain once they no longer need to protect
  persistent children
- **MANAGER.9**: only positive `self` ownership authorizes child launch from a
  reserved spawn request; `none` and `unknown` are not launch authority
- **MANAGER.10**: if a lower-TID canonical owner is positively proved after
  reservation but before launch, the manager must keep the exact work item
  durable by either exact-message requeue to `weft.spawn.requests` or visibly
  stranded reserved state
- **MANAGER.11**: manager-scoped fence diagnostics
  (`manager_spawn_fenced_requeued`, `manager_spawn_fenced_stranded`,
  `manager_spawn_fence_suspended`) must not be reused as spawn rejection
  signals
- **MANAGER.12**: dispatch suspension blocks later spawn reservation and launch
  until ownership is re-established, while still allowing control handling and
  supervision of already-running children
- **MANAGER.13**: a manager that still owns a fenced exact spawn request in its
  private reserved queue must not leadership-yield, idle-exit, or enqueue new
  ensure-mode autostart work before that exact request is recovered
- **MANAGER.14**: if dispatch ownership returns to `self` after suspension, the
  exact fenced spawn request is requeued to `weft.spawn.requests` before later
  inbox work resumes

### Context Invariants

_Implementation mapping_: `weft/context.py`, `weft/_constants.py`,
`weft/commands/tidy.py`.

- **CTX.1**: one Weft project context resolves to one broker target
- **CTX.2**: contexts are isolated by default
- **CTX.3**: cleanup commands operate within the selected context boundary
- **CTX.4**: `.weft/` remains the Weft-owned project home even when the broker
  backend is not file-backed

## Validation and Enforcement

Current enforcement is distributed, not centralized.

That means invariants are enforced by:

- Pydantic validation in TaskSpec models
- runtime checks in task and runner code
- queue and state ownership paths
- tests that exercise those boundaries

This is deliberate. The current system does not use one global invariant engine.

## Error Classes

The current codebase does not expose one dedicated invariant-exception family.
Instead, invariant failures appear through the owning boundary:

- validation errors from TaskSpec/model checks
- runtime errors from runner or consumer enforcement
- CLI errors from command surfaces

## Monitoring and Alerting

Current invariant visibility comes from:

- `weft.log.tasks`
- task-local control and output queues
- process titles
- CLI status/result/task inspection
- the test suite

There is no separate invariant-monitor daemon in the current contract.

## Scope Boundary

Future idempotency-key schemes, centralized invariant checkers, dedicated
invariant exception classes, and active alerting logic live in the companion
doc:

- [`07A-System_Invariants_Planned.md`](07A-System_Invariants_Planned.md)

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-17-canonical-owner-fence-plan.md`](../plans/2026-04-17-canonical-owner-fence-plan.md)

## Related Documents

- [`01-Core_Components.md`](01-Core_Components.md)
- [`05-Message_Flow_and_State.md`](05-Message_Flow_and_State.md)
- [`06-Resource_Management.md`](06-Resource_Management.md)
