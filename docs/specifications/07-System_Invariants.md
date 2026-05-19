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
- active service-health convergence plan:
  [`docs/plans/2026-05-09-service-liveness-and-health-convergence-plan.md`](../plans/2026-05-09-service-liveness-and-health-convergence-plan.md)
- draft internal state-machine helper plan:
  [`docs/plans/2026-05-13-internal-state-machine-helper-plan.md`](../plans/2026-05-13-internal-state-machine-helper-plan.md)

## System Invariants

### Immutability Invariants

_Implementation mapping_: `weft/core/taskspec/model.py`.

- **IMMUT.1**: `TaskSpec.spec` is immutable after creation
- **IMMUT.2**: `TaskSpec.io` is immutable after creation
- **IMMUT.3**: resolved `TaskSpec.tid` is immutable and unique
- **IMMUT.4**: runtime state and metadata remain mutable

### State Machine Invariants

_Implementation mapping_: `weft/core/task_lifecycle.py` transition table,
`weft/core/taskspec/model.py` state validators, and `TaskSpec.set_status()`.

- **STATE.1**: valid transitions are `created -> spawning|failed|cancelled`,
  `spawning -> running|completed|failed|timeout|cancelled|killed`, and
  `running -> completed|failed|timeout|cancelled|killed`
- **STATE.2**: terminal states do not transition back to non-terminal states
- **STATE.3**: running state requires `started_at`
- **STATE.4**: terminal states require `completed_at` once execution has started
- **STATE.5**: `completed_at` is later than `started_at` when both exist
- **STATE.6**: public snapshots must not present non-terminal status with
  terminal timestamps; runtime conflicts are diagnostics, not lifecycle
  back-transitions

### Deterministic Reducer Helper Invariants

_Implementation mapping_: `weft/core/state_machines.py`;
runtime-specific reducer instances live in modules such as
`weft/core/task_lifecycle.py` and `weft/commands/control_convergence.py`.

- **REDUCER.1**: generic state-machine helpers must be pure. They select
  decisions from caller-provided state and evidence only, and they must not
  read queues, inspect processes, mutate domain models, write logs, sleep, or
  apply side effects.
- **REDUCER.2**: state-machine decisions name the selected target state,
  action, transition ID, and reason. Side effects remain with the existing
  runtime owner that called the reducer.
- **REDUCER.3**: state-machine construction validates declared states, actions,
  transition IDs, static transition edges, terminal-state outgoing edges, and
  non-terminal source coverage. Dynamic transition targets are not supported
  by the current helper.
- **REDUCER.4**: reducer tests should prove structural reachability separately
  from concrete test coverage. A state can be reachable but still untested.

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
  available, live under `runtime_handle.observations.host_pids`. A task
  process with no runner-specific runtime handle publishes its own host PID
  through the same `runtime_handle` shape so endpoint and control surfaces have
  current-state liveness proof without trusting legacy top-level PID fields.
- **EXEC.4**: return-code publication belongs to terminal execution outcomes

### Observability Invariants

_Implementation mapping_: `weft/core/tasks/base.py`,
`weft/core/monitor/task_monitor.py`, `weft/commands/status.py`,
`weft/commands/task_monitor.py`, `weft/_constants.py`.

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
- **OBS.11**: live runtime evidence may explain a conflict, but read-only
  public status reconstruction must not use it to reanimate terminal lifecycle
  state.
- **OBS.11a**: missing or stale liveness evidence may explain a nonterminal
  row through reconciliation metadata, but it must not synthesize terminal
  failure without task-owned terminal proof or an explicit recovery diagnostic
  such as claimed outbox residue.
- **OBS.12**: a matched keyed PONG is an authoritative live task-local
  observation at its timestamp for explicit current-state probes. It is not a
  lifecycle mutation, and it must not use a second runner-specific inspection
  path outside the existing runner handle/plugin description contract. For
  manager selection, a matched keyed PONG may rescue stale-looking canonical
  registry evidence; an absent or unmatched PONG is not proof that takeover is
  safe.
- **OBS.12a**: task-local control acknowledgements are not lifecycle
  mutations. A `KILL` ack can shorten the path to fallback control, but it
  must not be reported as terminal `killed` state unless terminal evidence,
  controlled host-process death, or an authoritative no-host-PID runner kill
  proof is also present. Command-layer result classification is selected by
  the pure reducer in `weft/commands/control_convergence.py`; queue waits,
  runner fallback, and process termination remain owned by
  `weft/commands/tasks.py`.
- **OBS.13**: task monitor log files, task monitor checkpoints,
  runtime-prune reports, and retention-prune reports/archives are operational
  outputs only. They must not become task lifecycle truth, status authority,
  or result authority. The manager-supervised `TaskMonitorTask` is also
  operational: its lifecycle classifications, cleanup-candidate
  classifications, processor results, durable Monitor collation tables, and
  checkpoints do not change public lifecycle reconstruction. The Monitor-owned
  tables `weft_monitor_meta`, `weft_monitor_task_collations`, and
  `weft_monitor_task_messages` are derived operational state. The Monitor may
  create, verify, and additively migrate only those tables inside an already
  initialized Weft broker database; it must not create or initialize the broker
  database. If configured with the built-in `delete`
  processor, it may delete exact cleanup rows selected by explicit supported
  paths only. For retained `weft.log.tasks`, the supported supervised path is:
  delete malformed rows, fold valid retained rows into Monitor-owned tables,
  then delete those exact raw rows. For runtime-state queues, cleanup remains
  policy driven. Malformed rows are deletable only from Weft-owned schema
  queues whose policy says malformed rows are disposable, such as
  `weft.log.tasks` and `weft.state.tid_mappings`. It must not delete active
  work, ambiguous task-local evidence, claimed outbox residue, user payload
  rows, unknown rows outside an explicit cleanup policy, inbox/reserved work
  without terminal task-log proof for the same TID in the cleanup pass, or
  non-exact lifecycle evidence. Task-log collation summaries emitted by the
  monitor are operational evidence about cleanup work performed; they are not
  durable task lifecycle truth or archival records. In collated mode, durable
  Monitor table ingestion happens before raw `weft.log.tasks` deletion, and
  external summary failure blocks family disposition retry rather than
  resurrecting already ingested raw rows. Family disposition is explicit table
  state and is separate from summary emission. Terminal disposition may remove
  whole standard task-local `T{tid}.ctrl_in` and `T{tid}.ctrl_out` runtime
  queues, including visible and claimed rows, after required summary emission
  succeeds. The selection is bounded by Monitor-store readiness and the delete
  uses public SimpleBroker queue APIs; it must not use private queue-table SQL.
  Manager/global/custom control queues and task-local inbox/outbox/reserved
  queues are excluded from this default monitor cleanup. In raw external mode,
  external emit still happens before deletion of the affected raw row. External sink
  validation or emit failure must not prevent the TaskMonitor service from
  starting. TaskMonitor PONG cleanup
  diagnostics are cached from the last cleanup cycle. They may report both
  queue-level stats and policy-level stats, including zero-selected policy
  rows, plus cached Monitor-store availability, checkpoint, collation, summary,
  external-log health, and table-backed deletion counts. PONG must not perform
  queue scans, open or validate external log files, query the Monitor store,
  recompute cleanup candidates, or delete/report rows while answering a
  liveness request.
- **OBS.14**: claimed outbox residue is recovery evidence, not decoded result
  evidence. Status/result readers may surface
  `claimed_result_without_terminal`, but they must not delete, unclaim, or
  treat that claimed row as a readable final result.
- **OBS.15**: manager task snapshots must not publish a non-active historical
  manager row as `running` when `weft.state.services` has selected a different
  active manager.
- **OBS.16**: explicit runtime-state pruning is limited to supported
  runtime-only `weft.state.*` queues and exact message IDs. It must not prune
  task-local queues, `weft.log.tasks`, spawn requests, or manager control
  queues.
- **OBS.17**: explicit retention pruning may delete selected task-local and
  lifecycle-log rows only by exact message ID. Ordinary apply requires archive
  records before deletion and preserves recovery-sensitive evidence such as
  active/ambiguous tasks, claimed outbox residue, malformed/unknown-shape rows,
  and inbox/reserved work unless the candidate class is safe for ordinary
  deletion. `--force --apply` is a human override for those ordinary retention
  protections, but it does not override explicit scope, dry-run/apply mode,
  exact-message identity, or backend deletion capability.

_Plan backlink_: [`docs/plans/2026-05-07-task-local-reaper-retention-policy-plan.md`](../plans/2026-05-07-task-local-reaper-retention-policy-plan.md).

### Implementation Invariants

_Implementation mapping_: `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`, `weft/core/launcher.py`,
`weft/core/manager.py`, `weft/core/monitor/task_monitor.py`,
`weft/core/tasks/heartbeat.py`, `weft/core/runners/host.py`,
`weft/_constants.py`.

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
- **IMPL.8**: a task runtime's main task thread is the reactor owner for Weft
  broker effects. Queue reservation, queue writes/deletes, reserved-policy
  application, lifecycle state/log publication, endpoint state, and task-local
  control responses are committed from the owning reactor thread, not from
  task worker lanes.
- **IMPL.9**: task worker lanes are broker-free Weft runtime paths. They may
  run blocking target work, child process launch, or custom processor callables
  and return local Python results, but Weft must not rely on worker-thread
  SimpleBroker reads/writes or direct TaskSpec mutation for runtime correctness.
  The local worker-result channel is bounded, and the reactor drains it in
  bounded batches so worker progress applies backpressure instead of growing
  memory or starving queue/control turns. User-supplied Python code may still
  open its own broker connection; that is outside the Weft-owned worker-lane
  contract.

### Manager Invariants

_Implementation mapping_: `weft/core/manager.py`,
`weft/core/manager_runtime.py`, `weft/commands/manager.py`,
`weft/cli/run.py`, `weft/commands/serve.py`,
`weft/core/spawn_requests.py`, `weft/helpers.py`,
`weft/manager_detached_launcher.py`, `weft/manager_process.py`.

- **MANAGER.1**: managers are task-shaped runtimes with a long-lived dispatcher
  loop
- **MANAGER.2**: manager TIDs follow the same TID rules as other tasks
- **MANAGER.3**: managers publish manager service-owner rows in
  `weft.state.services`. A manager must treat its own active canonical service
  row as live while it has not unregistered and is not stopping; external
  liveness probes are for other manager records. Managers periodically refresh
  their own active row so other processes have current evidence even when no
  PONG probe is in flight.
- **MANAGER.4**: spawn-request message ID becomes child task TID
- **MANAGER.5**: managers spawn child tasks through the same overall task model
- **MANAGER.6**: one shared manager bootstrap path owns startup and
  foreground-serve behavior for `weft run`, `weft manager start`, and
  `weft manager serve`
- **MANAGER.7**: managers obey the same control semantics as other tasks, with
  graceful drain on normal termination signals
- **MANAGER.8**: live canonical managers converge on one lowest-TID leader per
  context for status, manager selection, and voluntary duplicate-manager
  cleanup; non-leaders yield or drain only after a lower-TID manager is
  positively live and dispatch-eligible, once they have no actionable queue
  work and no persistent children to protect. Unknown external-supervisor
  evidence is not voluntary-yield authority. Keyed PONG liveness may help
  decide which canonical records are live, including host-PID rows whose PID is
  not visible from the current PID namespace, but it is not a lease, election
  vote, or substitute for lowest-TID ownership reduction. Startup must not
  convert a fresh namespace-ambiguous canonical incumbent into permission to
  launch another manager unless public spawn backlog remains pending past the
  bounded namespace-ambiguity grace window.
- **MANAGER.8a**: a lower-TID live canonical manager may proactively publish a
  `superseded` manager service-owner row for a higher-TID manager when it
  observes a newly published canonical `active` row for that higher TID after
  the lower manager's previous registry watermark. The fresh higher active row
  is positive writer-liveness evidence because it proves the higher manager is
  refreshing the same registry it must poll on its 1-second leadership cadence.
  This does not apply to old rows from initial replay, non-active rows,
  non-canonical rows, lower-TID rows, or unknown/stale liveness evidence.
  Non-active manager rows must be ignored for active-manager election but kept
  visible until normal expiry or owner cleanup so the target manager can observe
  its `superseded` row.
- **MANAGER.9**: public spawn dispatch is work-stealing. Atomic reservation of
  a `weft.spawn.requests` message authorizes that manager to attempt the child
  launch for that exact message; registry `self`, `other`, `none`, or
  `unknown` ownership is not launch authority for public work.
- **MANAGER.10**: after a manager reserves a public or internal spawn request,
  STOP/KILL control and drain state are the pre-launch safety fence. A draining
  or stopped manager must not launch the reserved child.
- **MANAGER.11**: a successful manager child launch deletes exactly the
  reserved spawn message after the launch side effect succeeds. Invalid payload
  handling, pre-launch failure, and shutdown continue to use the manager's
  reserved-queue policy and visible reserved queues.
- **MANAGER.12**: registry leadership must not block a manager from draining
  already-reserved public or manager-owned internal spawn work. Leadership
  yield is advisory and idle-biased: it must not preempt actionable control,
  spawn, reserved, or child-supervision work.
- **MANAGER.12a**: pending public spawn backlog is not owned manager work. A
  manager may service a public spawn request it successfully reserved, but
  unreserved messages still in `weft.spawn.requests` must not by themselves
  prevent a higher-TID manager from yielding to the lower-TID live canonical
  manager.
- **MANAGER.13**: manager-owned singleton service correctness is enforced at
  the service boundary, not by a global public dispatch fence. Manager-authored
  service metadata and the service reducer decide when to start, wait, or
  terminate duplicates.
- **MANAGER.14**: stale or ambiguous manager registry proof must degrade status
  and selection confidence, not halt public spawn dispatch or manager-owned
  service convergence.
- **MANAGER.15**: manager-owned singleton services reduce all pending-spawn,
  live, terminal, and uncertain evidence through one deterministic transition
  table before Manager applies side effects. Terminal proof for a TID wins over
  live evidence for the same TID, and uncertain evidence must become visible
  degraded wait rather than an unqualified duplicate launch. A recent
  nonterminal service row without live proof is uncertain, not terminal;
  stale nonterminal rows may become restart evidence only after the recent
  evidence window has elapsed. Broker timestamps may order service evidence,
  but restart backoff is scheduled from the manager's observation clock. A
  stable singleton audit may run on a slower cadence than active convergence,
  but pending spawn work, missing required owners, duplicate scans, uncertain
  evidence, or due autostart scans must return the manager to the active
  convergence cadence. Manager-authored internal runtime envelopes,
  manager-authored autostart metadata, tracked children, runtime handles, and
  keyed PONG replies may establish singleton service authority; caller-owned
  TaskSpec metadata alone must not. A manager-authored `task_spawned` row with a
  live child PID is valid live-owner evidence for the spawned service child
  before the child publishes its own task lifecycle row. When more than one live
  owner is visible, the canonical live owner is the lowest live TID and
  non-canonical live owners must be signaled to terminate. The manager may
  force-reap only PIDs tied to the current scoped authority that proved the
  non-canonical owner live, such as a tracked child process or a `host-pid`
  runtime handle.
- **MANAGER.16**: managers must drain manager-owned internal spawn work before
  ordinary public spawn work whenever both are pending and launch is otherwise
  authorized by local control/drain state. Manager-authored singleton-service
  spawn requests must
  force the next scheduler drain to probe inactive queues and must advance to a
  launch attempt in the same manager turn without consuming ordinary public
  spawn work; they must not wait for the periodic broad-probe interval or a
  backend activity-waiter wakeup before they can advance. The manager may use a
  small fixed pass limit to prevent busy-loop bugs, but each pass must move
  monotonically through reap, reduce, enqueue, and internal-drain side effects.
- **MANAGER.17**: manager wait scheduling must distinguish local due timers
  from queue evidence. `next_wait_timeout() == 0.0` authorizes due local work;
  it does not authorize probing `weft.spawn.requests` or manager `.reserved`
  queues. Public spawn work advances only after the shared watcher marks the
  public spawn queue active through native activity, fallback polling evidence,
  or bounded periodic discovery.

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
- foreground `weft system task-monitor` log scans
- manager-supervised `TaskMonitorTask` health via task-local `PING`/`STATUS`
- the test suite

There is now a manager-supervised `TaskMonitorTask` in addition to the
foreground `weft system task-monitor` command. In the current contract it is
operational only. The default processor is `delete`, which may delete exact
rows selected by supported cleanup paths. Retained task-log cleanup is
Monitor-table driven: malformed `weft.log.tasks` rows are exact-deleted; valid
rows older than `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` are folded into
Monitor-owned tables before exact deletion; and family summaries/disposition
can run only after the FIFO pass reaches a completed high-water mark
(`empty` or first too-young visible row). A batch-limited, scan-limited, or
error-limited pass must not close a task family.
Terminal family disposition records a compact table tombstone instead of
physically purging the family row. Open families are classified operationally:
`suspected_inactive` requires a usable reporting interval and a reporting gap;
`stale_open` requires the explicit hard-age threshold for families without a
usable interval. Neither classification is public lifecycle truth.
Task-log cleanup must use a scan-depth limit separate from the processed batch
size, so backlog catch-up can make progress without a hot private loop.
Task-log cleanup must not treat diagnostic rows such as `task_activity` as
terminal lifecycle proof solely because their status field is terminal-looking.
Ordinary supervised cleanup must not probe task-local `T{tid}.reserved` queues.
Reserved rows stay protected by default; successful completed terminal proof
does not require a reserved-queue probe.
Collation summaries, cleanup policy stats, and Monitor-owned collation tables
remain operational TaskMonitor output only. Those deletes, summaries, and
tables do not make task-monitor output lifecycle truth or result authority.
`weft.log.tasks` remains runtime lifecycle evidence while retained, not audit,
forensic, or legal-retention evidence. `report_only` remains available as a
non-destructive override. External task-log retention output is optional
operational JSONL file output and is owned by
`weft/core/monitor/external_log.py`. Cleanup
orchestration lives in
`weft/core/monitor/task_monitor.py`; reusable runtime-state row policies live
under `weft/core/pruning/policies/`; legacy/foreground task-log scan helpers
live in `weft/core/monitor/task_log_scanner.py`; durable Monitor collation lives in
`weft/core/monitor/store.py`, `weft/core/monitor/sql.py`, and
`weft/core/monitor/collation.py`; terminal control-cleanup readiness lives in
the Monitor store/SQL layer; terminal control queue deletion lives at the
TaskMonitor runtime boundary; external task-log file emission lives in
`weft/core/monitor/external_log.py`; exact raw-message deletion still goes
through `weft/core/pruning/apply.py`.

## Scope Boundary

Future idempotency-key schemes, centralized invariant checkers, dedicated
invariant exception classes, and active alerting logic live in the companion
doc:

- [`07A-System_Invariants_Planned.md`](07A-System_Invariants_Planned.md)

## Related Plans

- [`docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`](../plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md)
- [`docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`](../plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md)
- [`docs/plans/2026-05-19-task-monitor-bounded-control-cleanup-plan.md`](../plans/2026-05-19-task-monitor-bounded-control-cleanup-plan.md)
- [`docs/plans/2026-05-16-monitor-store-hardening-and-layering-plan.md`](../plans/2026-05-16-monitor-store-hardening-and-layering-plan.md)
- [`docs/plans/2026-05-08-agent-session-and-task-startup-observability-plan.md`](../plans/2026-05-08-agent-session-and-task-startup-observability-plan.md)
- [`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](../plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md)
- [`docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`](../plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md)
- [`docs/plans/2026-05-06-terminal-publication-hardening-plan.md`](../plans/2026-05-06-terminal-publication-hardening-plan.md)
- [`docs/plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md`](../plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md)
- [`docs/plans/2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md`](../plans/2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md)
- [`docs/plans/2026-05-07-manager-selection-ping-pong-liveness-plan.md`](../plans/2026-05-07-manager-selection-ping-pong-liveness-plan.md)
- [`docs/plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`](../plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md)
- [`docs/plans/2026-05-08-manager-owned-internal-service-supervision-plan.md`](../plans/2026-05-08-manager-owned-internal-service-supervision-plan.md)
- [`docs/plans/2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`](../plans/2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md)
- [`docs/plans/2026-05-08-deterministic-manager-service-reconciler-plan.md`](../plans/2026-05-08-deterministic-manager-service-reconciler-plan.md)
- [`docs/plans/2026-05-09-managed-service-restart-clock-hardening-plan.md`](../plans/2026-05-09-managed-service-restart-clock-hardening-plan.md)
- [`docs/plans/2026-05-09-prune-path-unification-plan.md`](../plans/2026-05-09-prune-path-unification-plan.md)
- [`docs/plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md`](../plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md)
- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-17-canonical-owner-fence-plan.md`](../plans/2026-04-17-canonical-owner-fence-plan.md)
- [`docs/plans/2026-05-11-manager-work-stealing-dispatch-plan.md`](../plans/2026-05-11-manager-work-stealing-dispatch-plan.md)
- [`docs/plans/2026-05-12-bounded-task-monitor-cleanup-policy-plan.md`](../plans/2026-05-12-bounded-task-monitor-cleanup-policy-plan.md)
- [`docs/plans/2026-05-12-task-monitor-cleanup-composition-refactor-plan.md`](../plans/2026-05-12-task-monitor-cleanup-composition-refactor-plan.md)
- [`docs/plans/2026-05-13-task-monitor-pong-policy-stats-plan.md`](../plans/2026-05-13-task-monitor-pong-policy-stats-plan.md)
- [`docs/plans/2026-05-15-swappable-task-log-family-scanner-plan.md`](../plans/2026-05-15-swappable-task-log-family-scanner-plan.md)
- [`docs/plans/2026-05-15-manager-hot-loop-reduction-plan.md`](../plans/2026-05-15-manager-hot-loop-reduction-plan.md)
- [`docs/plans/2026-05-15-manager-reactor-hot-loop-follow-up-plan.md`](../plans/2026-05-15-manager-reactor-hot-loop-follow-up-plan.md)
- [`docs/plans/2026-05-16-monitor-durable-collation-store-plan.md`](../plans/2026-05-16-monitor-durable-collation-store-plan.md)
- [`docs/plans/2026-05-18-reactive-task-loop-hot-probe-plan.md`](../plans/2026-05-18-reactive-task-loop-hot-probe-plan.md)

## Related Documents

- [`01-Core_Components.md`](01-Core_Components.md)
- [`05-Message_Flow_and_State.md`](05-Message_Flow_and_State.md)
- [`06-Resource_Management.md`](06-Resource_Management.md)
