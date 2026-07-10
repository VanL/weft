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
- related service-health convergence plan:
  [`docs/plans/2026-05-09-service-liveness-and-health-convergence-plan.md`](../plans/2026-05-09-service-liveness-and-health-convergence-plan.md)
- related internal state-machine helper plan:
  [`docs/plans/2026-05-13-internal-state-machine-helper-plan.md`](../plans/2026-05-13-internal-state-machine-helper-plan.md)

## Table of Contents

- [System Invariants](#system-invariants)
  - [Immutability Invariants](#immutability-invariants)
  - [State Machine Invariants](#state-machine-invariants)
  - [Deterministic Reducer Helper Invariants](#deterministic-reducer-helper-invariants)
  - [Queue Invariants](#queue-invariants)
  - [Resource Invariants](#resource-invariants)
  - [Execution Invariants](#execution-invariants)
  - [Observability Invariants](#observability-invariants)
  - [Implementation Invariants](#implementation-invariants)
  - [Manager Invariants](#manager-invariants)
  - [Context Invariants](#context-invariants)
- [Validation and Enforcement](#validation-and-enforcement)
- [Error Classes](#error-classes)
- [Monitoring and Alerting](#monitoring-and-alerting)
- [Scope Boundary](#scope-boundary)
- [Related Plans](#related-plans)
- [Related Documents](#related-documents)

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
- **QUEUE.7**: a live task declares every construction-fixed reactor role and
  fixed support route that it watches, reserves into, or uses for durable task
  or runtime output. The five BaseTask roles (`inbox`, `reserved`, `outbox`,
  `ctrl_in`, `ctrl_out`), BaseTask support routes, and subtype-fixed roles are
  pairwise distinct for the task lifetime, except for explicit, subtype-owned
  semantic aliases named in code and firing tests. Runtime construction rejects
  every other collision before building queue configs or opening or mutating
  broker state. Payload-directed Heartbeat destinations are governed separately
  by [MF-3.2] ingestion validation and are not construction-fixed roles. This
  does not remove the standalone `MultiQueueWatcher` dynamic-topology API;
  standalone mutation follows the drive-owner and exact-membership contract in
  [QUEUE.8], [CC-2.1], and [SB-0.4]. `BaseTask` remains construction-fixed and
  rejects runtime `add_queue()` and `remove_queue()`.

  _Implementation mapping_: `BaseTask._reactor_queue_roles()`, its fixed support
  route inventory, and `_allowed_reactor_queue_aliases()` define and validate
  construction topology before queue configuration. Manager, PipelineTask,
  PipelineEdgeTask, Monitor, and TaskMonitor extend the inventory for subtype
  lanes, support routes, and named semantic aliases. Heartbeat validates
  payload-directed destinations against the reserved `weft.` namespace at
  message ingestion under [MF-3.2]. The canonical, support-route, subtype, and
  Heartbeat dynamic-egress matrices in `tests/tasks/test_task_execution.py`,
  `tests/core/test_manager.py`, `tests/tasks/test_pipeline_runtime.py`,
  `tests/tasks/test_task_observer_behavior.py`, `tests/tasks/test_task_monitor.py`,
  and `tests/tasks/test_heartbeat.py` fire this invariant.

- **QUEUE.8**: a running standalone `MultiQueueWatcher` has one drive owner for
  dynamic topology effects. Foreign `add_queue()` and `remove_queue()` calls
  are synchronous requests applied in a deterministic linear order between
  wait and drain phases; owner-thread mutation during dispatch is rejected
  before effects. Public stop and topology commit use the same serialization
  boundary, so no stop-first mutation can bind a waiter. Each committed
  membership generation has one exact activity-wait signature. The owner
  replaces the strategy's optional native waiter before closing the displaced
  waiter, and stop cannot install a waiter after close. Native-waiter creation
  failure leaves the committed topology on bounded polling fallback without
  changing delivery semantics; a later topology generation may restore native
  waiting. Empty activity membership installs no waiter without calling the
  factory with an empty list. Main-thread SIGINT cannot expose or close a
  half-published waiter; `KeyboardInterrupt` is delivered after request and
  ownership cleanup. A direct/manual wait temporarily owns its waiter and
  excludes drive start and mutation; stop signals that wait but does not close
  its waiter from another thread.

_Implementation mapping_: `weft/core/tasks/multiqueue_watcher.py` owns [QUEUE.8]
through `MultiQueueWatcher._submit_topology_mutation()`,
`_apply_pending_topology_mutations()`,
`_apply_topology_mutation_on_owner()`, `run_in_thread()`, `run_forever()`,
`wait_for_activity()`, and `stop()`. `tests/tasks/test_multiqueue_watcher.py`
fires its SQLite, concurrency, cleanup, SIGINT, fallback, and real PostgreSQL
paths.

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
  such as claimed outbox residue or a superseded internal service row
  reconciliation.
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
  runtime-prune reports, retention-prune reports/archives, processor results,
  and Monitor-owned collation tables are operational evidence only. They must
  not become task lifecycle truth, result authority, control authority, or a
  replacement for task-owned queues and `weft.log.tasks`. The sub-invariants
  below define the manager-supervised `TaskMonitor` cleanup and collation
  contract.
  - **OBS.13.1**: Monitor-owned tables `weft_monitor_meta`,
    `weft_monitor_task_collations`, and `weft_monitor_task_messages` are
    derived operational state. The Monitor may create, verify, and additively
    migrate only those tables inside an already initialized Weft broker
    database; it must not create or initialize the broker database. The store
    may support command-layer derived status fallback after raw task-log
    retirement, but it must not become result authority, control authority, or
    lifecycle truth.
  - **OBS.13.2**: Dealing with processes can be messy: children may die between
    probes, managers may restart, retained logs may outlive queues, and cleanup
    passes may stop after partial progress. The Monitor exists to make that
    operational cleanup bounded, retryable, and observable while keeping
    lifecycle truth in task-owned queues and logs. Tests for this boundary must
    prove both that Monitor tables and reports are derived evidence only and
    that cleanup effects are exact, policy-selected, and retryable after
    partial failure.
  - **OBS.13.3**: With the built-in `delete` mode, retained
    `weft.log.tasks` cleanup may delete exact cleanup rows selected by explicit
    supported paths only: malformed rows are deleted, valid retained rows are
    folded into Monitor-owned tables, and then those exact raw rows are
    deleted. `weft_monitor_task_messages` is a temporary pending-reference
    table for exact raw-message deletion and retry, not a shadow queue ledger.
    After exact raw broker deletion succeeds, or retry observes the raw row is
    already absent, the Monitor deletes the child refs and reconciles the
    parent family. The manager `task_spawned` event-trim helper is the narrow
    exception: it may delete selected launch-event child refs while leaving the
    open manager parent family without `raw_deleted_at_ns`. Raw deletion is
    gated by successful Monitor collation, not by terminal summary emission.
  - **OBS.13.4**: Family disposition and parent retirement are explicit,
    retryable table state. Parents may be physically retired only after raw
    deletion, summary emission, disposition, task-local control cleanup, any
    required reserved cleanup proof, and zero remaining child refs are all
    recorded. Parent rows already marked `raw_deleted_at_ns` while child refs
    remain must be repaired before retirement. Orphan raw-log recovery records
    `orphan_raw_recovery_checked_at_ns` only after proving no raw broker rows
    remain; probe/delete errors leave the family retryable, and new raw
    task-log evidence clears the marker through normal collation merge.
  - **OBS.13.5**: Reserved cleanup proof is required only for rows with
    `reserved_probe_needed`. The Monitor records
    `reserved_cleanup_checked_at_ns` after the standard `T{tid}.reserved`
    queue is deleted or proven already absent. Probe/delete errors leave the
    marker unset so the family remains retryable. Reserved cleanup uses public
    SimpleBroker queue APIs from the TaskMonitor runtime-cleanup worker, never
    private queue-table SQL. Deletion is additionally gated on a minimum age
    (`WEFT_TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS`; when unset it
    follows the configured task-log retention period so both gates agree
    even under a retention-only override, and an explicit value wins)
    measured from terminal evidence age where available
    (`terminal_message_id`) and TID age as fallback, so a
    `ReservedPolicy.KEEP` row stays inspectable via
    `weft queue peek T{tid}.reserved` for the configured window rather than
    being deleted as soon as cleanup proof exists [QUEUE.6].
  - **OBS.13.6**: Runtime-state queue cleanup is policy driven. Malformed rows
    are deletable only from Weft-owned schema queues whose policy says
    malformed rows are disposable, such as `weft.log.tasks` and
    `weft.state.tid_mappings`. Runtime cleanup does not create new lifecycle
    evidence and remains bounded by the selected cleanup policy.
  - **OBS.13.7**: Monitor cleanup must not delete active work, ambiguous
    task-local evidence, claimed outbox residue, user payload rows, unknown
    rows outside an explicit cleanup policy, inbox/reserved work without
    terminal task-log proof for the same TID in the cleanup pass, or non-exact
    lifecycle evidence. Manager/global/custom control queues and custom
    task-local queues are excluded from default monitor cleanup. For
    `weft.state.tid_mappings`, this is enforced by keep-newest-per-key +
    payload-liveness gating: the cleanup policy
    (`weft/core/monitor/policies/tid_mapping.py`) keeps the newest row per
    mapping key (`full` TID) regardless of age unless that row's own payload
    fails a liveness probe against its runtime handle's `(pid, create_time)`
    pairs; superseded (non-newest) rows for a key keep the age-only rule. The
    probe consults only the row payload — never a terminal-evidence lookup or
    a Monitor collation-store reach — and a payload with no probeable host
    PIDs is undecidable and therefore treated as live (skip, never delete).
    This preserves the only durable liveness evidence for a plain running
    task, which endpoint resolution, runtime pruning, manager kill-pid
    resolution, and this same destructive-slice safety check all depend on.
    `stale_open` classification (a non-service open family with no usable
    reporting interval, aged past `stale_open_family_seconds`) is gated on
    destruction protection at disposal time: a candidate whose TID is in
    the Monitor's destruction-protected set
    (`_destruction_protected_runtime_tids`) is excluded from summary
    emission and family disposition, not just from later queue deletion. A
    quiet running task (state events are transition-only; a running task
    with no reporting interval emits nothing after `work_started`) is
    otherwise indistinguishable in the task log from an abandoned family,
    so gating only at delete time would still let the family be marked
    disposed — and therefore control-cleanup eligible — while the process
    is alive. Evidence model: the destruction-protected set is a superset
    of `_active_runtime_tids` (live service-registry owners plus runtime
    handles with live host-PID proof — the same `(pid, create_time)` check
    as `handle_has_live_host_process`) that additionally protects every
    TID whose newest `weft.state.tid_mappings` row is undecidable under
    the tid-mapping cleanup policy's own probe (`mapping_row_is_live`): no
    runtime handle, or a handle with no probeable host PIDs (e.g. an
    external/container runner), is undecidable and therefore protected —
    the same undecidable-means-live rule that keeps the row itself from
    deletion. A newest row whose probeable host processes are all dead, or
    a family with no mapping row at all, grants no protection. The same
    destruction-protected standard applies at delete time to
    already-disposed families that lack terminal task-log proof; families
    with terminal proof keep the positive-evidence (`_active_runtime_tids`)
    recheck only, because an undecidable newest mapping row is never
    deleted by the tid-mapping policy and would otherwise block terminal
    control-queue cleanup for every external-runner task indefinitely.
    `_active_runtime_tids` itself is unchanged: it answers "which owners
    are proven live?" for staleness proof; the destruction-protected set
    answers "which TIDs are safe to destroy?" — the two questions are
    deliberately separate.
  - **OBS.13.8**: Task-log collation summaries are operational evidence about
    cleanup work performed, not durable lifecycle truth or archival records.
    User-task rows use `collation_kind=user_task`; manager, built-in service,
    and manager-authored service rows use service classification and
    `record_type=service_summary`; external collated JSONL keeps
    `record_type=task_log_collated` for compatibility. In collated mode,
    Monitor ingestion precedes raw deletion, and external summary failure
    blocks family disposition retry rather than resurrecting ingested rows. In
    raw external mode, external emit precedes raw deletion; external sink
    validation or emit failure must not prevent TaskMonitor service startup.
    When external logging is enabled, the configured path is restart-scoped;
    the TaskMonitor must re-probe the resolved path once per monitor cycle and
    cache health transitions without emitting lifetime-log records.
  - **OBS.13.9**: Normal task cleanup clears standard task-local
    `T{tid}.ctrl_in` and `T{tid}.ctrl_out` runtime queues before process exit.
    Terminal disposition may later remove residual standard `T{tid}.ctrl_in`,
    `T{tid}.ctrl_out`, and `T{tid}.inbox` queues, including visible and claimed
    rows, after required summary emission when those queues were left by forced
    process death, cleanup failure, or older releases. Standard
    `T{tid}.outbox` is retained until task-log retention age, and standard
    `T{tid}.reserved` remains owned by the reserved cleanup policy.
  - **OBS.13.10**: Built-in task-log cleanup and runtime cleanup are the only
    TaskMonitor worker lanes allowed to own broker/store cleanup effects. The
    reactor must continue servicing task-local control while either lane is in
    flight. Runtime cleanup runs as bounded, discrete worker slices launched by
    the reactor after the previous worker result is applied. Terminal
    stale-queue cleanup, eligible reserved cleanup, and eligible dead-TID
    cleanup must not be mixed inside one worker result, and the runtime-cleanup
    worker must not start nested cleanup executor threads. The monitor also
    owns a default-on self-maintenance pass (backend vacuum plus conservative
    runtime-state pruning of the non-tid-mapping groups) on a monotonic
    deadline, gated by `WEFT_TASK_MONITOR_MAINTENANCE` /
    `WEFT_TASK_MONITOR_MAINTENANCE_INTERVAL_SECONDS` and reported through the
    non-policy `maintenance` STATUS block.
  - **OBS.13.11**: TaskMonitor PONG cleanup diagnostics are cached from the
    last cleanup cycle. They may report queue-level stats, policy-level stats
    including zero-selected policy rows, cached Monitor-store availability,
    checkpoint, collation, summary, external-log health, table-backed deletion
    counts, and whether terminal control cleanup is currently in flight. PONG
    must not perform queue scans, open or validate external log files, query
    the Monitor store, recompute cleanup candidates, or delete/report rows
    while answering a liveness request.
  - **OBS.13.12**: The top-level cleanup policy identities are exactly
    `task_log.retention`, `monitor_store.lifecycle`,
    `task_local.terminal_runtime`, `task_local.dead_tid`, and
    `runtime_state.retention`. Each policy run must remain bounded, report
    base/waypoint/blocked status, and avoid spinning when only future-eligible
    or blocked work remains. Private cleanup phases belong in reason counts or
    cached details, not new policy identities. Manager `task_spawned` row
    trimming is reported through `task_log.retention` reason counts and does
    not add a sixth policy identity.
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

_Plan backlinks_:
- [`docs/plans/2026-05-07-task-local-reaper-retention-policy-plan.md`](../plans/2026-05-07-task-local-reaper-retention-policy-plan.md)
- [`docs/plans/2026-05-30-cleanup-progress-fifo-boundary-plan.md`](../plans/2026-05-30-cleanup-progress-fifo-boundary-plan.md)

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
- **IMPL.8**: a task runtime's main task thread is the reactor owner for ordinary
  Weft broker effects. Queue reservation, ordinary queue writes/deletes,
  reserved-policy application, lifecycle state/log publication, endpoint state,
  and task-local control responses are committed from the owning reactor thread,
  not from task worker lanes. `BaseTask._submit_worker_lane(...)` is only the
  mechanical thread/result channel; `ServiceTask` builds its internal
  registered service-worker API on top of that channel with local Python input
  queues and typed events. Neither layer grants broker/store authority to
  ordinary workers. The declared exception is the manager-supervised
  TaskMonitor's bounded maintenance lanes, which may open fresh broker/store
  handles for Monitor-owned cleanup work and return cached results to the
  reactor.
- **IMPL.9**: task worker lanes are broker-free Weft runtime paths. They may
  run blocking target work, child process launch, or custom processor callables
  through the `_submit_worker_call(...)` broker-free wrapper or the internal
  `ServiceTask` service-worker API and return local Python results/events, but
  Weft must not rely on worker-thread SimpleBroker reads/writes or direct
  TaskSpec mutation for runtime correctness. The local worker-result channel is
  bounded, and the reactor drains it in bounded batches so worker progress
  applies backpressure instead of growing memory or starving queue/control
  turns. User-supplied Python code may still open its own broker connection;
  that is outside the Weft-owned worker-lane contract. The TaskMonitor built-in
  cycle and runtime-cleanup lanes are the only Weft-owned broker/store worker
  exceptions.
- **IMPL.10**: every task reactor instance has exactly one drive-owning thread.
  `BaseTask.process_once()` enforces ownership before concrete turn policy;
  `BaseTask.wait_for_activity()` verifies the same owner before waiter effects;
  `BaseTask.run_until_stopped()` is the only task process/wait/finalize loop;
  and stop never closes reactor-owned resources while startup, a turn, a
  standalone wait, or a drive loop can still touch them. Startup, turn, wait,
  and loop authority is published under the lifecycle lock; a standalone wait
  that observes pending stop owns finalization after the protected wait unwinds.
  Background, foreground, exceptional, and
  manual exits converge on one idempotent finalizer with one absolute wait
  deadline for driver/worker joins and Manager launch-drain/child-termination
  escalation. Cleanup failures cannot strand `FINALIZING` or skip private base
  cleanup. Public `wait_for_activity()`, `stop()`, and `cleanup()` cannot be
  overridden; task-specific waiting and shutdown extend protected owned-wait
  and deadline-aware cleanup hooks.

  _Implementation mapping_: `BaseTask.process_once()`, `wait_for_activity()`,
  `run_until_stopped()`, `run_forever()`, `stop()`, `cleanup()`, and
  `_finalize_task_once()` in `weft/core/tasks/base.py` enforce drive
  ownership, deadline propagation, and lifecycle ordering;
  `weft/core/launcher.py::_task_process_entry` delegates once. Protected
  cleanup hooks in Consumer, ServiceTask, Manager, and TaskMonitor receive the
  absolute deadline; the private BaseTask cleanup phase always runs afterward.
  Manager's protected termination-policy hook
  (`weft/core/manager.py::Manager._apply_termination_request`) preserves
  graceful drain and SIGUSR1 priority; Manager launch drain, child-exit
  polling, joins, and process-tree escalation consume the same absolute
  cleanup deadline with within-budget SIGKILL escalation for TERM-resistant
  descendants. Cleanup diagnostics retain any still-live managed PIDs even
  after the direct wrapper exits or the deadline expires. Lifecycle, signal,
  managed-survivor, multi-child deadline, wait-owner,
  stop-idempotence, and cleanup-failure tests in
  `tests/tasks/test_task_execution.py`, `tests/tasks/test_signal_deferral.py`,
  `tests/tasks/test_service_task.py`, and `tests/core/test_manager.py` fire
  this invariant.
- **IMPL.11**: TaskMonitor maintenance workers close every worker-owned queue,
  Monitor store, TaskSpec/config snapshot, and external-sink facade in
  `finally` and never share watcher, lifecycle, queue, store, sink counters,
  or mutable task state with the reactor. Same-path sink facades lease one
  process-local writer/rotation owner so only one live rotating handler exists
  per resolved path. Built-in and runtime-cleanup results return frozen typed
  diagnostics only after worker resources close; close failures produce
  failed/pending results; cumulative external/deferred status is merged on the
  reactor thread, including the deferred backing fields and health-transition
  notification, so a later status refresh cannot revert a worker's result.

  _Implementation mapping_: TaskMonitor built-in and runtime-cleanup
  worker-context creation, snapshot/close helpers, typed results, and the
  owner-thread diagnostic merge
  (`_apply_worker_external_task_log_status`) live in
  `weft/core/monitor/task_monitor.py`. Worker TaskSpec snapshots intentionally
  identity-share the immutable frozen `spec`/`io` interiors while owning a
  shell copy, deep-copied mutable `state`/`metadata`, and an independent
  configuration snapshot. The single-handler-per-path writer
  lease and worker-local sink counters live in
  `weft/core/monitor/external_log.py`. The worker-snapshot isolation,
  same-path rotation, jsonl-then-delete, retryable body/close failure,
  close-order, deferred-status merge, and live-control tests in
  `tests/tasks/test_task_monitor.py`, `tests/core/test_monitor_external_log.py`,
  and `tests/core/test_monitor_store.py` fire this invariant.

### Manager Invariants

_Implementation mapping_: `weft/core/manager.py`,
`weft/core/manager_runtime.py`, `weft/core/control_probe.py`,
`weft/commands/manager.py`,
`weft/cli/run.py`, `weft/commands/serve.py`,
`weft/core/spawn_requests.py`, `weft/helpers/__init__.py`,
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
  bounded namespace-ambiguity grace window. Pong-proof probes retire their
  replies: a matched keyed PONG is deleted on match, and the prober sweeps
  rows bearing its own request id when a pending probe times out or is
  abandoned.
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
  runtime handle. Monitor cleanup may dispose an old nonterminal service-owner
  collation as `stale_service_owner` or `superseded_service_owner` only after
  same-service registry proof shows that owner is non-live or replaced and no
  active runtime evidence protects the TID; that disposition is cleanup
  authority, not terminal lifecycle truth.
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

_Implementation mapping_: `weft/context.py` (`build_context` directory
creation), `weft/_constants.py`, `weft/commands/tidy.py`,
`weft/helpers/__init__.py` (`ensure_owner_only_dir`).

- **CTX.1**: one Weft project context resolves to one broker target
- **CTX.2**: contexts are isolated by default
- **CTX.3**: cleanup commands operate within the selected context boundary
- **CTX.4**: `.weft/` remains the Weft-owned project home even when the broker
  backend is not file-backed
- **CTX.5**: Weft-created metadata directories (the `.weft/` project home,
  its `outputs/` child, and the autostart directory) are owner-only (0700),
  including tightening of pre-existing looser directories at context build.
  The default logs directory is protected by its 0700 parent rather than
  its own mode; custom `WEFT_LOGS_DIR` and custom database locations keep
  caller-owned modes.

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
- manager-supervised `TaskMonitor` health via task-local `PING`/`STATUS`
- the test suite

There is now a manager-supervised `TaskMonitor` in addition to the
foreground `weft system task-monitor` command. In the current contract it is
operational only. The default mode is `delete`, which may delete exact
rows selected by supported cleanup paths. Retained task-log cleanup is
Monitor-table driven: malformed `weft.log.tasks` rows are exact-deleted; valid
rows older than `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` are folded into
Monitor-owned tables before exact deletion; exact raw deletion is reconciled
by physically deleting the corresponding pending child refs from
`weft_monitor_task_messages`; visible raw rows older than the forward Monitor
checkpoint and missing from `weft_monitor_task_messages` are recovered by a
bounded pre-checkpoint pass that does not move the checkpoint; legacy orphan
raw rows for terminal/disposed families may be exact-deleted by a bounded
recovery pass that records a completed no-row probe on the parent family;
parent raw-deleted rows that still have child refs are repaired by a bounded
child-ref cleanup pass; and family
summaries/disposition can run only after the FIFO pass reaches a completed
high-water mark
(`empty` or first too-young visible row). A batch-limited, scan-limited, or
error-limited pass must not close a task family.
Terminal family disposition records retryable compact table state while raw
deletion, summary, and runtime cleanup remain incomplete. Once raw deletion,
summary emission, disposition, task-local control cleanup, and any required
reserved cleanup proof are all recorded and no child message refs remain, the
Monitor may physically retire the compact parent family row. Open families are
classified operationally:
`suspected_inactive` requires a usable reporting interval and a reporting gap;
`stale_open` requires the explicit hard-age threshold for families without a
usable interval. Neither classification is public lifecycle truth.
Task-log cleanup must use a scan-depth limit separate from the processed batch
size, so backlog catch-up can make progress without a hot private loop.
Task-log cleanup must not treat diagnostic rows such as `task_activity` as
terminal lifecycle proof solely because their status field is terminal-looking.
Ordinary supervised cleanup may delete standard task-local `T{tid}.reserved`
queues only through the TaskMonitor-owned runtime cleanup slice when the Monitor
table already proves the family terminal/disposed/raw-deleted, or when no
Monitor row exists and the TID is older than the task-log retention period with
no active runtime-owner evidence. Monitor-table reserved cleanup is driven by
`reserved_probe_needed` plus a missing `reserved_cleanup_checked_at_ns`; an
already-absent reserved queue is a successful no-row check, not a reason to
keep the family pending. Reserved deletion must use public
SimpleBroker queue APIs and must remain bounded by the same runtime cleanup
limit and per-slice deadline safeguards as task-local control queue cleanup.
A separate runtime cleanup slice may identify dead tasks from standard
`T{tid}.*` queue names minus live runtime TIDs. For those dead TIDs, the
dead-task policy may delete stale standard `ctrl_in`, `ctrl_out`, and `inbox`
queues immediately; it may delete standard `outbox` and `reserved` queues only
after the task-log retention period. Dead-TID cleanup is actionable-work
driven: retention-deferred `outbox` or `reserved` queues may be reported, but
they must not keep the runtime cleanup worker pending or trigger raw task-log
coalescing. Dead-TID cleanup must not mark Monitor collation families; if a
Monitor row exists, terminal or reserved cleanup owns that family. That
name-derived cleanup must not delete manager/service controls, custom
controls, manager-owned `T{manager_tid}.internal_reserved` queues, or active
runtime owners. Manager-owned `.internal_reserved` cleanup is a Manager
responsibility: the current manager may clear its own private internal
reserved queue during shutdown, and active managers may immediately delete
bounded batches from stale `.internal_reserved` queues only after the owning
manager TID is absent from the live manager registry.
Collation summaries, cleanup policy stats, cached `policy_progress`, and
Monitor-owned collation tables remain operational TaskMonitor output only.
Those deletes, summaries, progress records, and tables do not make
task-monitor output lifecycle truth or result authority.
Monitor collation summaries must classify Weft-owned manager/service rows
separately from ordinary user-task rows without creating another cleanup
policy. Ordinary user-task rows use `collation_kind=user_task` and
`record_type=task_summary`. Manager, built-in service, and manager-authored
managed-service rows use a service classification and
`record_type=service_summary` on the task-monitor sink. External collated JSONL
keeps its compatibility `task_log_collated` record type and carries
`collation_kind`/`service` fields when present. Service classification must be
derived from Weft-owned role, reserved service/autostart metadata, or internal
runtime class markers; domain-specific metadata alone must not remove failed
work from the generic task bucket.
The monitor has exactly five top-level cleanup policy identities:
`task_log.retention`, `monitor_store.lifecycle`,
`task_local.terminal_runtime`, `task_local.dead_tid`, and
`runtime_state.retention`. Each policy run must remain bounded, must report
whether it reached base, reached a bounded waypoint, or blocked, and must not
spin when only future-eligible or blocked work remains. Private cleanup phases
must be represented through reason counts or cached details rather than new
policy identities.
Deferred-only cleanup work is base-for-now and must not keep catch-up pending;
only a bounded waypoint requests catch-up cadence. Blocked cleanup reports an
error and uses existing error/backoff behavior rather than becoming a hot
catch-up loop by default.
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
`weft/core/monitor/collation.py`; reusable lifetime report JSON shape lives in
`weft/core/monitor/lifetime_report.py`; runtime queue cleanup slice policy lives in
`weft/core/monitor/policies/runtime_control.py`; runtime queue cleanup
readiness lives in the Monitor store/SQL layer and TaskMonitor boundary;
terminal control and eligible reserved queue deletion live at the TaskMonitor
runtime boundary;
external task-log file emission lives in
`weft/core/monitor/external_log.py`; exact raw-message deletion still goes
through `weft/core/pruning/apply.py`.
TaskMonitor built-in cleanup behavior is selected by `WEFT_TASK_MONITOR_MODE`,
not by the external log path. The default external JSONL path is
`logs/weft.log` under the Weft project root, and file output uses Python's
standard rotating logfile handler.
The default mode remains `delete`: queues are the live source of truth, and
retained task-log rows are operational runtime evidence, not the audit record.
Operators who want Weft's built-in task-lifetime audit handoff should use
`jsonl_then_delete` and monitor the external file path plus deferred-write
outbox.
`jsonl_then_delete` must be durable-before-delete per selected subject: a
`task_lifetime_report` must either be written to the configured external JSONL
path or recorded in `weft_monitor_deferred_writes` before the selected exact
delete is attempted. If both report handoff paths fail, that subject remains
undeleted and retryable. Deferred writes are operational outbox rows only; they
are flushed by bounded monitor work and must not become lifecycle truth, result
authority, or a PONG-time live scan. TaskMonitor external-log diagnostics may
be cached into the task's `weft.state.tid_mappings` runtime mapping for passive
status reporting, but they remain diagnostics rather than service lifecycle
truth.

## Scope Boundary

Future idempotency-key schemes, centralized invariant checkers, dedicated
invariant exception classes, and active alerting logic live in the companion
doc:

- [`07A-System_Invariants_Planned.md`](07A-System_Invariants_Planned.md)

## Related Plans

- [`docs/plans/2026-07-10-postgresql-dynamic-native-waiter-rebind-plan.md`](../plans/2026-07-10-postgresql-dynamic-native-waiter-rebind-plan.md)
- [`docs/plans/2026-07-09-reference-reactor-safety-hardening-plan.md`](../plans/2026-07-09-reference-reactor-safety-hardening-plan.md)
- [`docs/plans/2026-07-02-runtime-correctness-and-retention-remediation-plan.md`](../plans/2026-07-02-runtime-correctness-and-retention-remediation-plan.md)
- [`docs/plans/2026-06-29-manager-task-spawned-retention-policy-plan.md`](../plans/2026-06-29-manager-task-spawned-retention-policy-plan.md)
- [`docs/plans/2026-06-18-hypothesis-property-testing-plan.md`](../plans/2026-06-18-hypothesis-property-testing-plan.md)
- [`docs/plans/2026-06-10-self-healing-runtime-maintenance-plan.md`](../plans/2026-06-10-self-healing-runtime-maintenance-plan.md)
- [`docs/plans/2026-06-01-critical-review-remediation-plan.md`](../plans/2026-06-01-critical-review-remediation-plan.md)
- [`docs/plans/2026-05-31-task-monitor-orphan-log-and-status-reconciliation-plan.md`](../plans/2026-05-31-task-monitor-orphan-log-and-status-reconciliation-plan.md)
- [`docs/plans/2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md`](../plans/2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md)
- [`docs/plans/2026-05-29-task-monitor-general-lifetime-reporting-plan.md`](../plans/2026-05-29-task-monitor-general-lifetime-reporting-plan.md)
- [`docs/plans/2026-05-30-task-monitor-mode-and-rotating-log-plan.md`](../plans/2026-05-30-task-monitor-mode-and-rotating-log-plan.md)
- [`docs/plans/2026-05-29-reliability-and-doc-fixes-plan.md`](../plans/2026-05-29-reliability-and-doc-fixes-plan.md)
- [`docs/plans/2026-05-28-stale-service-owner-runtime-cleanup-plan.md`](../plans/2026-05-28-stale-service-owner-runtime-cleanup-plan.md)
- [`docs/plans/2026-05-27-service-collation-reporting-plan.md`](../plans/2026-05-27-service-collation-reporting-plan.md)
- [`docs/plans/2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md`](../plans/2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md)
- [`docs/plans/2026-05-26-service-task-worker-api-plan.md`](../plans/2026-05-26-service-task-worker-api-plan.md)
- [`docs/plans/2026-05-25-monitor-dead-task-catchup-convergence-plan.md`](../plans/2026-05-25-monitor-dead-task-catchup-convergence-plan.md)
- [`docs/plans/2026-05-24-monitor-policy-progress-contract-plan.md`](../plans/2026-05-24-monitor-policy-progress-contract-plan.md)
- [`docs/plans/2026-05-23-monitor-cleanup-policy-convergence-plan.md`](../plans/2026-05-23-monitor-cleanup-policy-convergence-plan.md)
- [`docs/plans/2026-05-23-monitor-cleanup-executor-plan.md`](../plans/2026-05-23-monitor-cleanup-executor-plan.md)
- [`docs/plans/2026-05-20-service-task-shared-reactor-extraction-plan.md`](../plans/2026-05-20-service-task-shared-reactor-extraction-plan.md)
- [`docs/plans/2026-05-30-task-monitor-external-log-health-plan.md`](../plans/2026-05-30-task-monitor-external-log-health-plan.md)
- [`docs/plans/2026-05-20-monitor-collation-table-retirement-plan.md`](../plans/2026-05-20-monitor-collation-table-retirement-plan.md)
- [`docs/plans/2026-05-20-monitor-reactor-worker-refactor-plan.md`](../plans/2026-05-20-monitor-reactor-worker-refactor-plan.md)
- [`docs/plans/2026-05-20-monitor-fair-cleanup-scheduling-plan.md`](../plans/2026-05-20-monitor-fair-cleanup-scheduling-plan.md)
- [`docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`](../plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md)
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
- [`docs/plans/2026-06-09-evaluation-findings-remediation-plan.md`](../plans/2026-06-09-evaluation-findings-remediation-plan.md)

## Related Documents

- [`01-Core_Components.md`](01-Core_Components.md)
- [`05-Message_Flow_and_State.md`](05-Message_Flow_and_State.md)
- [`06-Resource_Management.md`](06-Resource_Management.md)
