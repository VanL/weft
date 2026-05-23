# Message Flow Patterns and State Management

This document describes the current message flows that make Weft observable and
durable. The central rule is simple: task lifecycle truth lives in queues and
task-log events, not in a separate hidden state store.

_Implementation snapshot_: reservation, control, task-local queues, spillover,
and endpoint/streaming runtime claims live in `weft/core/tasks/base.py` and
`weft/core/tasks/consumer.py`; manager spawn, bootstrap, and reconciliation
flow live in `weft/core/manager.py`, `weft/core/spawn_requests.py`,
`weft/cli/run.py`, `weft/commands/_spawn_submission.py`, and
`weft/core/manager_runtime.py`; status, task-history replay, and shared
result waiting live in `weft/commands/status.py`, `weft/commands/result.py`,
`weft/commands/_result_wait.py`, `weft/commands/_task_history.py`, and
`weft/commands/_streaming.py`.

Queue names and control message constants are summarized in
[`00-Quick_Reference.md`](00-Quick_Reference.md).

See also:

- planned companion:
  [`05A-Message_Flow_and_State_Planned.md`](05A-Message_Flow_and_State_Planned.md)
- invariants:
  [`07-System_Invariants.md`](07-System_Invariants.md)
- implementation plan:
  [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)
- active service-health convergence plan:
  [`docs/plans/2026-05-09-service-liveness-and-health-convergence-plan.md`](../plans/2026-05-09-service-liveness-and-health-convergence-plan.md)
- active manager-service authority hardening plan:
  [`docs/plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md`](../plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md)
- active reactive task-loop hot-probe plan:
  [`docs/plans/2026-05-18-reactive-task-loop-hot-probe-plan.md`](../plans/2026-05-18-reactive-task-loop-hot-probe-plan.md)

## Message Flow Patterns [MF-0]

### 1. Task Submission Flow [MF-1]

Current flow:

```text
User -> CLI -> weft.spawn.requests -> Manager -> T{tid}.inbox
                                   \-> weft.log.tasks
```

The CLI submits a spawn request. The manager expands it into a runtime
TaskSpec, uses the spawn-request message ID as the task TID, seeds the initial
inbox payload when provided, and records the lifecycle event in
`weft.log.tasks`.

Queue-first ordering is deliberate. `weft run` writes the spawn request before
manager bootstrap proof completes. Once the request is written, later CLI
error handling reconciles that submitted TID against durable task, log, and
queue surfaces instead of assuming the public inbox delete path can always roll
the request back. Only requests still provably present in
`weft.spawn.requests` are safe to delete as rollback.

Public spawn submission is also a trust boundary. Submission strips reserved
internal runtime, endpoint-claim, manager-service, and autostart authority
metadata from caller-provided TaskSpecs before queueing. Manager-owned internal
spawn paths may still use those keys inside explicit internal runtime
envelopes.

_Implementation mapping_: `weft/cli/run.py` `_enqueue_taskspec`;
`weft/commands/_spawn_submission.py` `reconcile_submitted_spawn`;
`weft/core/spawn_requests.py` `submit_spawn_request`,
`delete_spawn_request`;
`weft/core/manager.py` `Manager._handle_work_message`,
`Manager._build_child_spec`, `Manager._launch_child_task`.

### 2. Message Processing Flow with Reservation [MF-2]

Current flow:

```text
T{tid}.inbox -> reserve/move -> T{tid}.reserved -> execute -> T{tid}.outbox
                                       |
                                       +-> policy on stop/error
                                       \-> lifecycle events to weft.log.tasks
```

Current rules:

- inbox work is reserved before execution
- success clears or finalizes reserved state
- error, timeout, or external control applies the configured reserved policy
- crash leaves the message in reserved state for explicit operator recovery
- direct `run_work_item()` execution has no reserved message and must not
  mutate unrelated reserved backlog as if it did
- persistent tasks emit `work_item_completed` for each completed message and
  `work_completed` only when the task itself reaches a terminal finish
- `weft.state.streaming` marks active outboxes that `weft result --all` must
  skip so live stream sessions are not re-read as static results

Agent work follows the same outer reservation flow as command and function
targets.

_Implementation mapping_: `weft/core/tasks/consumer.py`,
`weft/core/tasks/base.py`.

### 3. Control Flow [MF-3]

Current flow:

```text
Controller -> T{tid}.ctrl_in -> Task -> T{tid}.ctrl_out
                                  \-> weft.log.tasks
```

The control plane is explicit:

- `ctrl_in` receives raw commands such as `STOP`, `STATUS`, and `PING`, plus
  structured JSON command envelopes for keyed `PING`/`STATUS` style probes
- `ctrl_out` carries task-local replies and terminal notifications
- `weft.log.tasks` remains the runtime lifecycle evidence stream rather than
  the interactive reply channel. It is durable while retained, but it is not
  legal, forensic, or audit-retention evidence.
- `PING` replies with a JSON `PONG` control response containing `tid`,
  `timestamp`, `task_status`, `paused`, `should_stop`, `runner`, optional
  `activity`, optional `waiting_on`, and optional best-effort `runtime`
  details from the active runner; structured PING envelopes with `request_id`
  must echo the same `request_id` in the PONG
- manager PONG responses include manager-selection fields when available:
  `role="manager"`, `inbox`/`requests`, `ctrl_in`, `ctrl_out`, `outbox`, and
  `weft_context`; selection code may use those fields to validate a matched
  manager liveness proof, but missing PONGs remain absence of proof rather than
  proof of death
- runner-specific PONG `runtime` details must come from the existing runner
  handle/plugin description contract, such as Docker's
  `RunnerRuntimeDescription`; failure to collect those details must not
  suppress the core PONG response
- tasks may register an optional PONG extension provider; when present, its
  JSON-serializable mapping is nested under the `extended` key, and extension
  failures are reported under that same key without suppressing the core PONG
  response
- terminal task-local notifications must be typed JSON envelopes with
  `type="terminal"`, `source="task"` or `source="manager"`, `tid`, `status`,
  and `timestamp`; optional fields include `error` and `return_code`
- one-shot non-persistent task success publishes a task-owned typed terminal
  `ctrl_out` envelope after the task reaches `completed`, so task-local
  terminal proof exists alongside the `work_completed` task-log event
- readers must ignore ordinary control replies, legacy stderr stream chunks,
  malformed JSON, and other `ctrl_out` payloads when looking for terminal state
- non-interactive command `stream_output` writes stdout and stderr stream frames
  to `T{tid}.outbox`; stderr frames are diagnostics for live/event consumers
  and do not become the public task result
- the shared task-evidence reader may use a typed terminal `ctrl_out` envelope
  as terminal observation proof when task-log terminal proof is missing
- CLI control waiters may accept a typed terminal `ctrl_out` envelope as
  sufficient stop/kill completion proof for that task without replaying the
  global task log first; ordinary control acknowledgements remain progress
  evidence only
- manager-authored terminal envelopes are supervisor observations for child
  wrapper death only; they must be written to the child `ctrl_out` queue, never
  to outbox, and only when task-owned terminal proof is not already visible
- active STOP/KILL may delete the raw `ctrl_in` message as an internal
  handoff detail, but public acknowledgement remains the post-unwind
  `ctrl_out` reply plus the terminal task-log event on the main task thread
- a `KILL` acknowledgement means the task accepted the command; it is progress
  evidence only. Readers and CLI control helpers must not promote ack-only
  evidence into terminal lifecycle state. Kill success is proven by terminal
  `killed` evidence, by observed controlled host PIDs becoming dead after a
  kill action, or by an authoritative runner control surface when no
  host-observable PID exists.

_Implementation mapping_: `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`, `weft/core/manager.py`,
`weft/commands/task_evidence.py`, `weft/commands/tasks.py`,
`weft/commands/control_convergence.py`.

Implementation plan backlinks:

- `docs/plans/2026-04-30-known-tid-terminal-snapshot-api-plan.md`
- `docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`
- `docs/plans/2026-05-10-control-and-service-convergence-state-machine-plan.md`
- `docs/plans/2026-05-06-terminal-publication-hardening-plan.md`
- `docs/plans/2026-05-07-extended-ping-pong-state-probe-plan.md`
- `docs/plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md`
- `docs/plans/2026-05-07-manager-selection-ping-pong-liveness-plan.md`

### 3.1 Named Endpoint Discovery [MF-3.1]

Current flow:

```text
Task -> weft.state.endpoints -> resolve/list -> ordinary queue write to inbox or ctrl_in
```

Current rules:

- a long-lived task may explicitly publish one active named-endpoint claim by
  writing a runtime record that points at its ordinary task-local queues
- current claim paths are explicit task-side registration helpers and explicit
  `weft run --name TEXT` on persistent top-level runs
- clean shutdown deletes the task's active claim
- resolve and list surfaces opportunistically prune stale claims whose owner is
  terminal or no longer live
- current liveness checks use `weft.log.tasks` plus `weft.state.tid_mappings`;
  task processes publish host-PID liveness through `runtime_handle` when no
  runner-specific handle exists, and there is no separate endpoint lease or
  heartbeat contract
- if multiple live tasks claim the same name, the canonical live owner is the
  lowest eligible TID; duplicate live claims remain observable conflicts
- names under `_weft.` are reserved for Weft-owned internal runtime services;
  public naming surfaces reject that namespace
- the current shipped internal runtime endpoint is `_weft.heartbeat`, owned by
  the built-in heartbeat service
- sending to a named endpoint is still an ordinary queue write to the resolved
  inbox or `ctrl_in`
- missing-name resolution is an explicit failure. It does not auto-spawn,
  auto-register, or redirect work elsewhere

_Implementation mapping_: `weft/core/tasks/base.py`
`register_endpoint_name()` and `unregister_endpoint_name()`;
`weft/core/endpoints.py`; `weft/commands/queue.py`.

### 3.2 Heartbeat Service Flow [MF-3.2]

Current heartbeat flow is task-shaped:

```text
helper -> resolve _weft.heartbeat -> ordinary queue write to service inbox
service inbox -> in-memory registration heap -> ordinary queue write to destination
```

Current rules:

- the built-in heartbeat service is one internal persistent task that
  multiplexes many registrations in one process
- heartbeat registrations are runtime-only and disappear on service or Weft
  restart
- helper startup ensures a manager exists and waits for a live manager-owned
  heartbeat endpoint. The helper does not submit heartbeat tasks directly;
  heartbeat launch and restart belong to the manager-owned internal service
  path.
- registration messages are ordinary inbox payloads with `action` `upsert` or
  `cancel`
- `upsert` resets `next_due_at` to `now + interval_seconds`
- intervals are integer seconds with a first-slice minimum of 60 seconds
- on each due wake, the canonical owner writes at most one message per
  registration to the configured destination queue, then advances to the next
  future slot
- if a due registration is late, the service coalesces to one emit and does
  not replay every missed slot
- the heartbeat service is driven by the shared task reactor loop. It handles
  at most one bounded control/registration/due-emission turn in
  `process_once()`, exposes the next registration due time or idle shutdown
  through `next_wait_timeout()`, and leaves all destination queue writes on the
  main task thread. It does not inspect inbox/control pending state in
  `next_wait_timeout()`; queue wakeup belongs to `MultiQueueWatcher`, and the
  heartbeat timer is only the timeout supplied to that same wait path.
- duplicate heartbeat services converge through the manager-owned singleton
  service contract. Live ownership is proved by tracked child state, a live
  runtime handle, including the task process host handle in TID mappings, or
  keyed `PING`/`PONG`; stale non-terminal rows without live proof do not block
  replacement. Helper-side endpoint validation classifies the endpoint as
  service-candidate evidence and uses the shared singleton summary rules, so
  terminal task-log proof or dead host-process proof for the endpoint TID
  rejects stale endpoint rows even if older endpoint registry data remains.
  Service status summaries must prefer a separate live singleton owner over a
  terminal duplicate from another TID, while terminal proof still overrides
  launch evidence for the same TID.
- the heartbeat service is an interval emitter, not a scheduler: there is no
  cron syntax, wall-clock scheduling, timezone handling, or missed-run replay
- the supervised `TaskMonitor` uses heartbeat registrations for periodic
  wake messages to its own `T{tid}.inbox`; if registration is temporarily
  unavailable, the monitor records operational health and falls back to its
  bounded local interval
- the supervised `TaskMonitor` includes cached operational diagnostics in
  PONG extension data under `extended.task_monitor`: configuration, heartbeat
  registration state, scheduling state, and the previous cycle summary. For
  built-in cleanup processors, the summary includes cached queue-level cleanup
  stats and cached policy-level cleanup stats under
  `cleanup_policy_stats`. This extension must not scan queues or recompute
  cleanup candidates during PING.

_Implementation mapping_: `weft/core/heartbeat.py`;
`weft/core/tasks/heartbeat.py`; `weft/core/manager.py`;
`weft/core/manager_services.py`; `weft/core/endpoints.py`;
`weft/core/monitor/task_monitor.py`.

### 4. Pipeline Flow [MF-4]

Current pipeline flow is task-shaped:

- `weft run --pipeline` submits one pipeline task
- the pipeline task owns public task-style surfaces
- stage and edge work are materialized as ordinary child tasks underneath that
  parent
- pipeline status is exposed without inventing a second top-level operator
  model

The reason for this shape is consistency. Pipelines are composition over tasks,
not a separate workflow product.

### 5. State Observation Flow [MF-5]

Current flow:

```text
Task lifecycle events -> weft.log.tasks -> status/result reconstruction
Task lifecycle events -> weft.log.tasks -> task-monitor JSONL logs
Task lifecycle events -> weft.log.tasks -> Monitor-owned collation tables
TaskMonitor heartbeat wake -> T{monitor_tid}.inbox -> bounded processor cycle
manager serve operational events -> process stderr/stdout only
```

Current rules:

- `weft.log.tasks` is runtime lifecycle evidence. Status, result, and debugging
  surfaces may use it while retained, but it is not legal, forensic, or audit
  evidence. Its retention requirements are operational.
- opt-in `weft manager serve` operational JSONL records are process-log
  diagnostics only. They must not be written to `weft.log.tasks`, per-task
  queues, control queues, or runtime-state queues, and they must not be used as
  lifecycle truth, pruning evidence, or status/result reconstruction input.
- CLI status surfaces reconstruct task snapshots from that log plus the latest
  `weft.state.tid_mappings` entries and live runtime liveness where needed; they
  do not depend on a separate state database
- terminal task-log events may carry `runner_diagnostics` for process/session
  startup, runtime readiness, and runner-boundary failure evidence. These
  diagnostics are operational metadata only. They may explain an already
  selected lifecycle event such as `work_failed`, `work_timeout`, or
  `work_limit_violation`, but they must not create, override, or infer public
  lifecycle state by themselves.
- `weft system task-monitor` can scan `weft.log.tasks` without consuming
  broker messages and emit compact JSONL records to stdout or append-only disk
  log files under `.weft/logs/task-monitor/`
- task-monitor checkpoints under `.weft/state/task-monitor/` are
  operational cursors only; deleting or corrupting them may affect duplicate
  log output, but it must not change task lifecycle truth, public status,
  result materialization, or cleanup decisions
- the supervised `TaskMonitor` may maintain Monitor-owned durable collation
  tables: `weft_monitor_meta`, `weft_monitor_task_collations`, and
  `weft_monitor_task_messages`. These tables are operational read models
  derived from retained `weft.log.tasks` rows. They are not queues, are not
  exposed through `weft queue *`, and are not public lifecycle or result
  authority. The Monitor creates/verifies only these Monitor tables, in an
  already initialized Weft broker database, and records a cached PONG error if
  the store is unavailable.
- task-monitor log records and processor summaries are operational output only
  in the current release. The monitor may mark broker messages as lifecycle or
  cleanup candidates. With the default `delete` processor it may delete exact
  rows selected by explicit bounded cleanup policies from supported Weft-owned
  cleanup queues. Those policies may delete malformed rows only from queues
  whose schema is owned by Weft, such as `weft.log.tasks` and
  `weft.state.tid_mappings`. With the built-in `report_only` processor it does
  not delete, reserve, move, prune, reap, acknowledge, or unclaim rows. The
  `jsonl_then_delete` processor remains fail-closed until the operational
  logging callback lands.
- when enabled, the canonical manager supervises one internal
  `TaskMonitor`. The supervised monitor reads task-log lifecycle evidence by
  generator/high-water cursor for observation and custom processors. Custom
  processors run from the resulting candidate snapshot in a broker-free worker
  lane; the TaskMonitor reactor commits the processor result, checkpoint, and
  cached diagnostics after the worker returns. Built-in cleanup processors run
  in a separate TaskMonitor-owned built-in cycle worker lane; the reactor stays
  available for task-local PING/STATUS/STOP/KILL, heartbeat registration, and
  schedule bookkeeping while the worker scans, writes Monitor-store rows, and
  applies exact deletes. For the built-in `delete` processor with Monitor
  collation enabled, retained `weft.log.tasks` rows are processed in FIFO order:
  malformed rows are exact-deleted and valid rows are folded into the Monitor
  table without first deleting open lifecycle evidence. Terminal families may
  be summarized and exact-deleted after the pass reaches a complete FIFO
  high-water; open families continue to use
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` plus stale-open policy before
  summary/deletion. Built-in cleanup still
  runs runtime-state policies such as `weft.state.tid_mappings`, but it no
  longer uses bounded task-log family windows as the supervised task-log
  deletion authority. The manager owns only child supervision; it does not scan
  lifecycle queues.
- when table collation is enabled, each retained FIFO pass reduces valid
  task-log rows through `weft/core/monitor/collation.py`, upserts one summary
  row per TID, and records each incorporated raw message ID in
  `weft_monitor_task_messages` before deleting the raw broker row. Child rows
  in `weft_monitor_task_messages` are temporary pending raw-message
  references: after exact broker deletion succeeds, or after retry observes the
  raw broker row is already absent, the Monitor physically deletes those child
  rows and reconciles the parent `raw_deleted_at_ns` when no child refs remain.
  Legacy child rows marked with `deleted_at_ns` from older releases are
  physically pruned in bounded Monitor-store cleanup slices. Replaying the same
  raw row after a delete failure is idempotent. A terminal family may emit a
  compact operational summary only after the FIFO pass reaches a
  completed high-water mark: the scanned FIFO prefix reaches queue end before a
  scan limit or write/delete error. If the pass stops on scan limit, store
  write failure, or queue delete failure, family summary/disposition is skipped
  for that cycle. Summary
  readiness uses the family high-water (`last_message_id`/`last_seen_at_ns`),
  not the first terminal event timestamp. Family disposition is explicit
  retryable compact state (`disposition_reason` plus `disposition_at_ns`),
  separate from `summary_emitted_at_ns`, so summary emission can be retried
  separately from task-local runtime cleanup. Terminal task-local runtime cleanup runs as
  a separate bounded maintenance slice selected by Monitor-store readiness
  (`summary_emitted_at_ns`, no prior `task_control_deleted_at_ns`, and either
  terminal proof past the terminal retirement high-water or an already disposed
  Monitor family). Terminal task-local runtime cleanup runs on a dedicated
  TaskMonitor maintenance worker after the summary/disposition high-water is
  reached; that worker owns only the queue-delete plus Monitor-store mark
  transaction for standard stale task-local queues. Standard
  `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and `T{tid}.inbox` are stale at
  terminal cleanup time. Standard `T{tid}.outbox` is retention-gated, and
  standard `T{tid}.reserved` is handled by the reserved cleanup policy.
  Once no child message rows remain and raw deletion, summary emission,
  disposition, and task-local control cleanup have all been recorded, the
  Monitor may physically retire the compact parent row from
  `weft_monitor_task_collations`.
  The same maintenance worker may delete eligible stale standard
  `T{tid}.reserved` queues after monitor-table proof or stale no-monitor
  evidence, while preserving active runtime owners. Each built-in cycle attempts
  retained raw task-log exact deletion in the built-in cycle worker before the
  reactor launches another runtime-cleanup worker. The runtime worker processes
  one fair slice at a time with a bounded mixed cleanup executor: terminal
  stale-queue cleanup, eligible reserved cleanup, and eligible dead-TID cleanup
  may all run in the same epoch under one total worker cap. It marks catch-up
  pending when backlog remains or the internal slice cap/deadline is hit. PONG
  reports cached store availability,
  checkpoint, rows processed,
  tasks updated, terminal tasks observed, summaries emitted, families
  disposed/classified, whether runtime cleanup is in flight,
  task-control families processed, task-control queues deleted, estimated
  task-control rows deleted, reserved queues deleted, reserved rows deleted,
  raw rows deleted, runtime cleanup pending/cap/deadline state, and cleanup
  executor job counts by kind from the last completed cleanup result. PONG must
  not query the store or scan queues
  while answering `PING`.
- terminal Monitor collation rows may emit compact operational task summaries
  through the configured task-monitor sink. In collated mode, durable Monitor
  ingestion gates raw `weft.log.tasks` deletion; external summary emission
  gates only family summary/disposition retry, not resurrection of already
  ingested raw rows. Raw external mode remains emit-before-delete and does not
  write Monitor collation rows. Successful completed lifecycles do not require
  reserved-queue probes. After any required summary is emitted, terminal
  disposition may delete whole standard task-local runtime queues through
  public SimpleBroker queue APIs from the dedicated TaskMonitor
  runtime-cleanup worker. For a proven-dead TID, `T{tid}.ctrl_in`,
  `T{tid}.ctrl_out`, and `T{tid}.inbox` are stale immediately. `T{tid}.outbox`
  and `T{tid}.reserved` remain retention-gated and are selected by the same
  dead-TID policy only when the TID is older than the task-log retention
  period. Manager, global, service, and custom control queues are excluded.
- `weft system prune` is a separate foreground maintenance command.
  `--family runtime-state` reports or deletes exact message IDs from supported
  `weft.state.*` queues after conservative live/recent checks.
  `--family task-local`, `--family task-log`, and `--family retention` report
  or delete task-local/lifecycle-log retention candidates through
  archive-backed exact-message pruning. Prune reports and archives are
  operational evidence only; they do not become task lifecycle truth and
  status/result reconstruction must not depend on them.
- for read-only status reconstruction, terminal task-log lifecycle proof wins
  for public `status`; runtime liveness disagreement may be exposed through
  `reconciliation` diagnostics, but it must not rewrite a terminal task back to
  `running`
- shared task evidence classification lives in
  `weft/commands/task_evidence.py`; status, task inspection, known-TID terminal
  snapshots, and result helpers reuse that interpretation instead of each
  inventing their own priority rules
- shared task evidence priority is: terminal task-log lifecycle proof, typed
  terminal `ctrl_out`, readable final one-shot outbox, live runtime evidence,
  then stale observer fallback
- a matched keyed PONG is authoritative live task-local evidence at its
  timestamp for explicit known-TID current-state probes; reconciliation compares
  task-log lifecycle events, typed terminal `ctrl_out` envelopes, and matched
  PONGs by timestamp when possible, while equal or unordered terminal evidence
  remains preferred over PONG
- when task-log terminal proof is missing, a typed terminal `ctrl_out` envelope
  may classify the task as terminal, including `wrapper_lost` for
  manager-authored wrapper-exit envelopes
- for new one-shot non-persistent success paths, terminal proof should normally
  include both `work_completed` in `weft.log.tasks` and a task-owned typed
  terminal `ctrl_out` envelope
- for one-shot non-persistent tasks, a final unambiguous outbox result may be
  classified as `result_without_terminal`; persistent, interactive, streaming,
  partial, or ambiguous outbox traffic does not prove task completion, and
  `result_without_terminal` remains a diagnostic fallback for historical rows
  or unavoidable crash windows rather than the normal success proof
- claimed outbox residue is not decoded result evidence. When no readable
  result remains and stale observer fallback would otherwise produce a generic
  stale-liveness diagnostic, status/result surfaces may classify the row as
  `claimed_result_without_terminal`; this is a recovery diagnostic, not proof
  that the result value is readable
- status surfaces must not emit `status="running"` with `completed_at` set
- host tasks that still look `running` or `spawning` in the durable log but
  have no runtime proof are reported with stale-liveness reconciliation after
  the configured status liveness window, unless a live manager registry record
  still proves a manager task is active. Stale liveness alone is not terminal
  failure proof.
- manager task snapshots must respect the selected active manager from
  `weft.state.services`; a historical non-terminal manager task row must not be
  published as `running` when a different active manager has been selected
- shared waiters use the same terminal-state interpretation for `weft run` and
  `weft result`
- `weft result` and `weft run` share the same wait helper path: they watch the
  outbox, ctrl-out, and log queues, tolerate the short gap between a terminal
  log event and final outbox visibility, and do one last non-blocking outbox
  drain before returning `completed`
- persistent result waits treat both `work_item_completed` and
  `work_completed` as completion boundaries for the same task
- `weft result --stream` follows unread outbox stream chunks without changing
  the task-log boundary events that define completion
- when a one-shot streamed result has already been emitted to the caller, shared
  result waiters retain that emitted-result fact as completion evidence so a
  delayed terminal log cannot turn visible output into a later timeout
- `weft result` first waits for taskspec metadata or outbox/control queue names
  to materialize when those surfaces are not yet visible, then falls back to the
  shared completion wait

_Implementation mapping_: `weft/core/tasks/base.py` `_report_state_change`;
`weft/core/monitor/task_monitor.py` foreground monitor scan primitive and
supervised monitor task;
`weft/core/monitor/runtime.py` task-monitor runtime config, candidate, and
processor contract;
`weft/core/task_evidence.py` shared lifecycle/result evidence classification;
`weft/core/outbox.py` shared outbox decoding;
`weft/commands/status.py` and `weft/commands/system.py` log replay and snapshot
collection;
`weft/commands/result.py` materialization and completion waits;
`weft/commands/task_evidence.py` compatibility re-exports;
`weft/commands/task_monitor.py` archive summaries and checkpoints;
`weft/commands/runtime_prune.py` explicit runtime-only prune reports and
exact-message deletion;
`weft/commands/_result_wait.py`;
`weft/core/serve_log.py` manager-serve process-log record construction;
`weft/commands/_task_history.py`;
`weft/commands/_streaming.py`.

Implementation plan backlink:
[`2026-05-07-runtime-state-pruning-plan.md`](../plans/2026-05-07-runtime-state-pruning-plan.md).

### 6. Manager Spawn Flow [MF-6]

Managers consume `weft.spawn.requests` and, for canonical managers,
`weft.spawn.internal`. Internal spawn requests are manager-owned service work
and are drained before public spawn requests whenever both queues are pending.
Both queues share the same validation, TaskSpec expansion, child launch, initial
inbox seeding, and acknowledgement path.

Child process creation is the only blocking part of the launch path and runs in
the manager's broker-free child-launch worker lane. The manager reactor owns all
broker effects: moving the request into a reserved queue, seeding the child
inbox before worker submission, committing `task_spawned` and service-owner
state after the worker returns, and deleting or applying reserved policy to the
exact reserved spawn message. While a launch worker is active, the manager must
continue to answer task-local control messages such as `PING`, but it must not
accept another spawn message in the same turn.

Internal service observability has two publication phases. A successful manager
launch first appears as a manager-authored `task_spawned` row with `child_tid`,
`child_taskspec`, and optionally `child_pid`; the child later emits its own
task-log rows and TID mapping. Ops status may report heartbeat or TaskMonitor
as launched from the manager row before child-local lifecycle rows exist.
Child-local terminal evidence and TID-mapping/runtime evidence override the
manager launch hint when they are present. An empty `weft.spawn.internal` queue
means only that no unclaimed internal spawn request is waiting there; it is not
evidence that the internal service is absent.

Current submission-reconciliation rules:

- if the submitted TID is already visible through task logs or TID mappings,
  the submission is treated as spawned
- if a manager has already emitted `task_spawn_rejected` for that child TID,
  the submission is treated as rejected
- if the exact message is still in `weft.spawn.requests`, the CLI may delete it
  and report submission failure
- if the exact message has moved into a manager reserved queue, the CLI must
  not claim rollback succeeded; it observes for spawned/rejected child
  evidence before surfacing manual recovery from that reserved queue
- if none of those surfaces prove success or rollback, the CLI reports an
  explicit unknown submission outcome keyed by TID

Current manager-dispatch rules:

- public spawn dispatch is work-stealing: a manager that atomically reserves a
  message from `weft.spawn.requests` owns that exact launch attempt
- manager-owned internal spawn dispatch uses the same launch path and remains
  strict-priority over public spawn work
- registry leadership is advisory for status, startup selection, and voluntary
  duplicate-manager convergence; stale or ambiguous registry proof must not
  block a manager that already reserved public or internal spawn work
- after a successful launch, the manager deletes the exact reserved message; if
  child launch fails before that point, the message remains governed by the
  manager's reserved-queue policy and visible reserved state
- child-launch workers return only local Python results. They do not touch
  SimpleBroker queues, mutate TaskSpec state, report task-log events, or send
  control responses. Those effects belong to the manager reactor.
- manager-to-manager liveness PINGs are reactor state, not blocking helper
  calls. The probing manager writes one keyed PING, stores the pending request,
  and peeks for the matching PONG on later turns until the deadline. Pending
  probes are unknown evidence; they must not immediately prune or supersede the
  target manager.
- service-owner liveness PINGs use the same non-blocking pattern. The manager
  writes one keyed PING to a candidate service owner, stores the pending probe,
  and checks for the matching PONG on later service-convergence turns. Pending
  service probes are uncertain evidence; after timeout, ordinary recent/stale
  candidate classification resumes.
- STOP/KILL control that arrives after reservation but before launch still wins:
  a draining or stopped manager must not start a new child from the in-flight
  reserved request
- singleton services do not rely on global manager dispatch ownership for
  correctness; manager-authored service metadata, pending-spawn evidence,
  live/terminal evidence, and duplicate cleanup converge through the
  manager-owned service reducer
- exhausted recovery never deletes the reserved message and never emits
  `task_spawn_rejected`; it leaves the stranded work operator-visible in the
  reserved queue
- after exhausted recovery, a non-owner manager may leadership-yield and a
  self-owner manager may clear the in-memory suspension so later spawn work can
  proceed; this is an availability tradeoff after durable diagnostics prove the
  older exact request needs manual recovery
- these manager-scoped fence diagnostics are not spawn rejection and must not
  be interpreted as `task_spawn_rejected`

The reconciliation helper reads only durable surfaces. It does not guess from
in-memory startup state.

Autostart manifests follow the same overall spawn path. Current autostart
runtime support covers stored task specs and stored pipeline targets. Pipeline
targets are compiled into the same top-level pipeline task submitted by
`weft run --pipeline`, and internal runtime classes such as pipelines,
heartbeat, and TaskMonitor travel on the manager-owned spawn envelope rather
than public TaskSpec metadata. Autostarts, heartbeat, and TaskMonitor share
manager-owned service metadata for `once`/`ensure` lifecycle policy. The
manager only treats a manifest launch or restart as consumed after the
synthesized spawn request is successfully written to the ordinary manager inbox
queue, and ensure-mode manifests are rescanned immediately after a tracked
autostart child exits.

_Implementation mapping_: `weft/core/manager.py` — `Manager._handle_work_message`,
`Manager._build_child_spec`, `Manager._launch_child_task`,
`Manager._run_child_launch_worker`, `Manager._handle_child_launch_result`,
`Manager._commit_child_launch_success`, `Manager._control_allows_child_launch`,
`Manager._tick_managed_service`,
`Manager._enqueue_managed_service_request`, `Manager.process_once`;
`weft/core/manager_services.py`;
`weft/core/spawn_requests.py`;
`weft/cli/run.py`; `weft/commands/_spawn_submission.py` —
`_inspect_task_log_for_tid`, `_reconcile_submitted_spawn_once`.

Implementation plan backlinks:
[`2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md`](../plans/2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md);
[`2026-05-11-internal-service-observability-plan.md`](../plans/2026-05-11-internal-service-observability-plan.md).

### 7. Manager Bootstrap Flow [MF-7]

`weft run` and `weft manager start` ensure a manager exists through the shared
bootstrap helper. `weft manager serve` runs the same canonical manager runtime
in the foreground for supervision.

Current rules:

- detached bootstrap launches the real manager runtime through a short-lived
  detached wrapper rather than keeping it as a plain CLI child
- detached bootstrap returns success only after the launched host process is
  live and the canonical registry record for that same manager TID contains a
  matching `runtime_handle` with `control.authority == "host-pid"` and the
  launched PID in `observations.host_pids`
- supervised or containerized manager launches must provide an explicit
  `WEFT_MANAGER_RUNTIME_HANDLE_JSON` value or use an external-supervisor
  runtime handle; generic manager code must not trust a container-local PID as a
  host PID
- when no explicit manager runtime handle is supplied, manager startup may use
  conservative local container detection markers such as `/.dockerenv`,
  `/run/.containerenv`, Kubernetes environment, or cgroup hints to publish an
  `external-supervisor` handle instead of an unsafe host-pid handle
- external-supervisor manager records are current observations, not permanent
  truth and not self-proving liveness. The manager refreshes its active registry
  record periodically. A registered process-local liveness probe may prove the
  exact handle `live` or `stale`; missing or inconclusive probes are `unknown`.
  Unknown external-supervisor evidence may delay destructive cleanup or trigger
  a bounded manager PING, but it must not cause a running dispatch-capable
  manager to yield leadership. Generic lifecycle readers must not treat
  supervisor/container-local PID fields as host process identity.
- host-pid manager records require scoped host-process identity, not just raw
  PID existence; if `observations.host_processes` includes process creation
  times, PID liveness checks must reject records whose current process identity
  does not match the recorded one
- a host-pid manager record whose PID cannot be observed from the current PID
  namespace is stale-looking but not enough by itself to erase a dispatchable
  manager. Startup, selection, and manager-owned leadership checks may rescue
  the row with a bounded keyed PING/PONG when the PONG proves manager role,
  queue identity, context, and non-terminal status. If the row is fresh but
  remains namespace-ambiguous, `ensure_manager` must not start a competing
  manager just because it cannot prove the incumbent live. That suppression is
  bounded by pending work: if public spawn backlog remains pending past the
  namespace-ambiguity grace window and the incumbent still lacks PONG/runtime
  proof, startup may launch another manager to recover progress.
- detached-launcher acknowledgement and startup-stderr cleanup are best-effort
  post-proof steps; they may warn, but they do not downgrade a successfully
  proven manager start into submission failure
- early detached-bootstrap failure surfaces child exit status and startup
  stderr context
- foreground serve forces `idle_timeout=0.0` for that invocation
- the shared lifecycle helper owns manager discovery, bootstrap, and stop
  observation

_Implementation mapping_: `weft/core/manager_runtime.py`,
`weft/cli/run.py`, `weft/commands/manager.py`,
`weft/commands/serve.py`, `weft/manager_detached_launcher.py`,
`weft/manager_process.py`.

Plan backlink:
[`docs/plans/2026-04-24-runtime-handle-authority-migration-plan.md`](../plans/2026-04-24-runtime-handle-authority-migration-plan.md);
[`docs/plans/2026-05-09-runtime-liveness-probe-registry-plan.md`](../plans/2026-05-09-runtime-liveness-probe-registry-plan.md).

### 8. Failure Recovery Flow

Current recovery is explicit:

- inspect reserved queues
- move or requeue work intentionally
- use `weft queue` primitives to recover from failure

There is no separate built-in retry-orchestrator surface in the current
contract.

## State Machine

Current durable lifecycle states:

- `created`
- `spawning`
- `running`
- `completed`
- `failed`
- `timeout`
- `cancelled`
- `killed`

Current rules:

- transitions are forward-only
- fast-path completion may move from `spawning` to a terminal state for very
  short work
- each state-change event includes enough TaskSpec data for log-driven
  reconstruction

_Implementation mapping_: `weft/core/taskspec/model.py` state helpers and validators;
`weft/core/tasks/base.py` state reporting.

## TaskSpec Redaction

Task-log events redact configured secret fields before writing TaskSpec data to
`weft.log.tasks`.

_Implementation mapping_: `weft/helpers.py` `redact_taskspec_dump`;
`weft/core/tasks/base.py` and manager state-reporting paths.

## Large Output Handling

### Strategy for Outputs Exceeding the Broker Message Limit

When task output is too large for the broker payload limit, the task runtime
spills that output outside the queue payload and writes a reference message.
The runtime also emits an `output_spilled` task-log event with the spill path
and digest so the spill can be inspected later.

_Implementation mapping_: `weft/core/tasks/base.py` `_spill_large_output`;
`weft/core/tasks/consumer.py` output emission helpers.

### Current Consumer and CLI Behavior

Current behavior:

- outbox payloads may contain a large-output reference instead of inline data
- `weft result` reads the outbox payload as written
- `weft result` does not currently auto-dereference large-output references

### Large-Output Reference Format [MF-2.1]

When output exceeds the configured threshold, the consumer writes a JSON
reference envelope to `T{tid}.outbox` instead of the raw output. The threshold
defaults to `DEFAULT_OUTPUT_SIZE_LIMIT_MB` (10 MB) and can be overridden per
task via `spec.output_size_limit_mb`.

Reference envelope (all fields present):

```json
{
  "type": "large_output",
  "path": "/tmp/weft/outputs/{tid}/output.dat",
  "size": 12345678,
  "size_mb": 11.77,
  "truncated_preview": "<first 1024 bytes decoded as UTF-8 with replacement>",
  "sha256": "<hex-encoded SHA-256 of the raw encoded bytes>",
  "message": "Output too large (12345678 bytes); saved to /tmp/weft/outputs/{tid}/output.dat"
}
```

Field definitions:

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"large_output"` | Discriminator; always this literal string |
| `path` | string (absolute path) | Path to the spill file on the local filesystem |
| `size` | integer | Raw byte count of the encoded output |
| `size_mb` | float | `size / (1024 * 1024)`, rounded to 2 decimal places |
| `truncated_preview` | string | First 1024 bytes decoded as UTF-8 with error replacement |
| `sha256` | string | Hex-encoded SHA-256 digest of the full encoded output |
| `message` | string | Human-readable summary for display in CLI output |

Path locality rules:

- If `spec.weft_context` is set, the spill directory is
  `{weft_context}/{WEFT_DIRECTORY_NAME}/outputs/{tid}/output.dat`, where
  `WEFT_DIRECTORY_NAME` defaults to `.weft`.
- Otherwise it falls back to `{tempdir}/weft/outputs/{tid}/output.dat`
  (where `{tempdir}` is the platform temporary directory).
- The file path is always absolute and points to the file written for that
  task's spill directory.

Consumers reading the outbox must inspect `type` to detect a reference before
treating the payload as inline output. The `sha256` field can be used to verify
file integrity after dereferencing.

_Implementation mapping_: `weft/core/tasks/base.py` `_spill_large_output`,
`_outputs_base_dir`; `weft/core/tasks/consumer.py` `_emit_single_output`;
`weft/commands/result.py` result rendering.

### Cleanup Boundary

Current cleanup is task-owned unless an operator explicitly invokes foreground
system pruning:

- task-owned cleanup clears standard task-local `T{tid}.ctrl_in` and
  `T{tid}.ctrl_out` runtime queues whenever the task unwinds through
  `cleanup()`, including success, failure, timeout, STOP, and KILL paths. Those
  queues are runtime control channels, not retained result surfaces. `T{tid}.outbox`
  remains outside this cleanup and is retained until result policy consumes it.
  Residual standard control queues after a terminal task imply forced process
  death before cleanup, cleanup failure, or pre-existing stale state.
- task-owned cleanup may remove spilled output when `cleanup_on_exit` is enabled
- there is no built-in age-based output sweeper in the current contract
- the supervised task monitor exists in the current contract. Its default
  `delete` processor may delete exact message IDs selected by supported
  Weft-owned cleanup paths. Runtime-state queues such as
  `weft.state.tid_mappings` remain policy driven. `weft.log.tasks` is now
  table driven when Monitor collation is enabled: the monitor scans visible
  rows in FIFO order up to `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT`, deletes
  malformed rows, folds valid rows into the Monitor table, and then deletes
  exact raw rows only after the table proves a terminal or classified-stale
  family has been summarized. Family summaries are emitted only after a
  completed FIFO high-water pass, never after a scan-limited or error-limited
  pass. Rows with non-terminal events such as
  `task_activity` do not close lifecycle groups solely because they carry a
  terminal-looking status. Terminal disposition may clear standard task-local
  residual `ctrl_in`, `ctrl_out`, and `inbox` queues left by forced process
  death, cleanup failure, or older releases. For proven-dead TIDs, the same
  policy may clear standard `outbox` and `reserved` queues only when the TID is
  older than the configured task-log retention period. Retained non-interactive
  command stream frames, including stderr diagnostics, live in outbox rather
  than cleanup-owned `ctrl_out` while the retention gate protects them.
  `WEFT_TASK_MONITOR_BATCH_SIZE` caps retained task-log rows or cleanup
  candidates per cycle; it is not the task-log scan depth. Reserved rows remain
  protected recovery-sensitive evidence by default. Per-cycle collation
  summaries are operational TaskMonitor evidence only, not durable archive
  records. Long-lived `ServiceTask` subclasses, including Manager, Heartbeat,
  and TaskMonitor, publish durable lifecycle transitions but suppress
  `task_activity` and poll-report rows in `weft.log.tasks`; their live activity
  is exposed through PONG/status, service registry, process title, and TID
  mapping surfaces. The supervised monitor may also derive dead-task cleanup
  candidates by listing standard task-local queue names, parsing `T{tid}.*`
  identities, and subtracting live runtime TIDs. That dead-task cleanup policy
  is owned by
  `weft/core/monitor/policies/dead_task.py`: it selects standard stale
  `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and `T{tid}.inbox` immediately, and
  selects standard `T{tid}.outbox` and `T{tid}.reserved` only after the
  retention period. Each cleanup cycle records cached policy/store stats so
  PONG can distinguish "ran and selected zero" from "did not run". The monitor
  must not delete active work,
  ambiguous task-local evidence, claimed outbox residue, user payload rows,
  spawn requests, manager control rows, or candidates without exact message
  IDs. `report_only` remains available as a non-destructive override.
- `weft system prune --family task-log|task-local|retention` is an explicit
  operator action, not a background sweeper. Ordinary apply mode requires an
  archive artifact before deletion. Force apply mode is a human override for
  ordinary retention protections, but it still deletes only selected exact
  broker message IDs.

_Implementation mapping_: canonical prune candidate selection and exact delete
live in `weft/core/pruning/`; Monitor durable collation lives in
`weft/core/monitor/store.py`, `weft/core/monitor/sql.py`, and
`weft/core/monitor/collation.py`; monitor cycle wiring lives in
`weft/core/monitor/task_monitor.py`; command rendering and CLI adaptation live in
`weft/commands/runtime_prune.py` and
`weft/commands/retention_prune.py`.

## Queue Management Patterns

### Unified Reservation Pattern

The `.reserved` queue is both the in-flight work marker and the failure holding
area. That unification is deliberate. It keeps failure visibility and work
ownership in one place instead of splitting them across separate hidden queues.

_Implementation mapping_: `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`.

### Current Queue Lifecycle Management

Current rules:

- queue creation is implicit on first write
- task cleanup closes task-owned handles and clears standard task-local
  `T{tid}.ctrl_in` / `T{tid}.ctrl_out` rows before closing those handles
- `weft.state.services`, `weft.state.tid_mappings`, `weft.state.streaming`,
  `weft.state.endpoints`, and `weft.state.pipelines` are runtime-only
  bookkeeping queues; they may be read for live reconciliation but are not
  durable application history
- `weft system prune` defaults to dry-run and applies only with
  `--apply`; apply mode deletes exact candidate message IDs only
- foreground runtime pruning must preserve recent rows, malformed or
  unknown-shape rows,
  and rows whose live owner remains active or ambiguous under existing
  liveness rules
- runtime-state pruning must not delete from `weft.log.tasks`,
  `weft.spawn.requests`, manager control queues, or task-local `T{tid}.*`
  queues
- retention pruning may delete selected rows from `weft.log.tasks`,
  `T{tid}.outbox`, `T{tid}.ctrl_out`, `T{tid}.ctrl_in`, `T{tid}.inbox`, and
  `T{tid}.reserved`. Ordinary apply protects active/ambiguous task evidence,
  claimed outbox residue, malformed/unknown-shape rows, and inbox/reserved work
  unless the class is safe for ordinary deletion. `--force --apply` is the
  explicit human override for those ordinary protections.
- the manager-supervised `TaskMonitor` reports and deletes through the
  TaskMonitor-owned cleanup runner in `weft/core/monitor/cleanup.py` for
  runtime-state queues and legacy/foreground policy surfaces. Retained
  `weft.log.tasks` cleanup is orchestrated by
  `weft/core/monitor/task_monitor.py` with durable collation in
  `weft/core/monitor/store.py`, not by the old bounded family-window task-log
  policy runner. All destructive paths still use the canonical exact-delete
  helper shared with foreground `weft system prune`. The supervised monitor
  must not apply archive side effects, force-only retention cleanup, or
  task-local retention cleanup in this slice. Normal task exit owns standard
  control-queue cleanup; the monitor-owned runtime pass is a backstop for
  forced exits, cleanup failures, and stale pre-change rows. Task-log cleanup and external
  retention output use one public minimum-age clock:
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`, defaulting to 172800 seconds.
  `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT` remains the per-cycle task-log scan
  depth and defaults to 50000 rows. Eligible standard `T{tid}.reserved` queues
  are deleted by the same supervised monitor `delete` processor path, bounded
  by the control cleanup limit and protected by active runtime evidence. When a
  cycle stops because the FIFO pass hit its scan limit, the monitor uses
  `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS` for the next wake instead of the
  full heartbeat interval.
- the durable table-backed collation path uses
  `weft/core/monitor/store.py` for schema, checkpoints,
  task-summary rows, child raw-message rows, and the table access layer; it uses
  `weft/core/monitor/sql.py` for SQL construction and trusted identifier
  validation; it uses
  `weft/core/monitor/collation.py` for pure row reduction; it uses
  `weft/core/monitor/external_log.py` for optional external JSONL emission; it
  uses the same canonical exact-delete helper after durable table ingestion for
  collated mode, and after raw external emit for raw mode.
  Disabling `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED` leaves the tables in
  place and removes them from the monitor cycle. `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED`
  is retained as a legacy guard for the older table-backed raw-delete path; the
  current supervised `delete` path deletes retained collated raw rows when the
  collation store is enabled and available. If the store is unavailable,
  well-formed task-log rows remain visible rather than falling back to the old
  family-window deleter. If `WEFT_LOG_TASKS_EXTERNAL_PATH` is enabled in
  `raw` mode, raw retained rows are emitted and deleted without writing Monitor
  collation tables. Open families with a usable reporting interval may be
  classified `suspected_inactive` after the configured reporting gap; open
  families without a usable interval may be classified `stale_open` only after
  `WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS`. These classifications are
  operational Monitor-table state, not public lifecycle truth. PONG exposes cached
  external-log status and retention settings only; it does not open files,
  scan queues, or query Monitor tables.
- `weft system tidy` handles backend-native cleanup of empty queues and broker
  maintenance
- autonomous destructive queue lifecycle is limited to the supervised
  task-monitor `delete` processor and only for exact safe candidates

## Scope Boundary

Design-reference helper classes for state tracking, large-output readers,
output cleanup sweeps, reservation recovery helpers, and richer queue-lifecycle
management live in the companion doc:

- [`05A-Message_Flow_and_State_Planned.md`](05A-Message_Flow_and_State_Planned.md)

## Related Documents

- [`00-Quick_Reference.md`](00-Quick_Reference.md)
- [`03-Manager_Architecture.md`](03-Manager_Architecture.md)
- [`07-System_Invariants.md`](07-System_Invariants.md)
- [`10-CLI_Interface.md`](10-CLI_Interface.md)

## Related Plans

- [`docs/plans/2026-05-23-monitor-cleanup-executor-plan.md`](../plans/2026-05-23-monitor-cleanup-executor-plan.md)
- [`docs/plans/2026-05-22-monitor-policy-modules-and-dead-task-cleanup-plan.md`](../plans/2026-05-22-monitor-policy-modules-and-dead-task-cleanup-plan.md)
- [`docs/plans/2026-05-20-monitor-collation-table-retirement-plan.md`](../plans/2026-05-20-monitor-collation-table-retirement-plan.md)
- [`docs/plans/2026-05-20-simplebroker-api-adoption-plan.md`](../plans/2026-05-20-simplebroker-api-adoption-plan.md)
- [`docs/plans/2026-05-20-monitor-reactor-worker-refactor-plan.md`](../plans/2026-05-20-monitor-reactor-worker-refactor-plan.md)
- [`docs/plans/2026-05-20-monitor-fair-cleanup-scheduling-plan.md`](../plans/2026-05-20-monitor-fair-cleanup-scheduling-plan.md)
- [`docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`](../plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md)
- [`docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`](../plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md)
- [`docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`](../plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md)
- [`docs/plans/2026-05-16-monitor-store-hardening-and-layering-plan.md`](../plans/2026-05-16-monitor-store-hardening-and-layering-plan.md)
- [`docs/plans/2026-05-08-agent-session-and-task-startup-observability-plan.md`](../plans/2026-05-08-agent-session-and-task-startup-observability-plan.md)
- [`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](../plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md)
- [`docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`](../plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md)
- [`docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`](../plans/2026-05-06-task-evidence-reconciliation-model-plan.md)
- [`docs/plans/2026-05-06-terminal-publication-hardening-plan.md`](../plans/2026-05-06-terminal-publication-hardening-plan.md)
- [`docs/plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md`](../plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md)
- [`docs/plans/2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md`](../plans/2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md)
- [`docs/plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`](../plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md)
- [`docs/plans/2026-04-14-spawn-request-reconciliation-plan.md`](../plans/2026-04-14-spawn-request-reconciliation-plan.md)
- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-09-manager-bootstrap-unification-plan.md`](../plans/2026-04-09-manager-bootstrap-unification-plan.md)
- [`docs/plans/2026-04-13-pipeline-spec-expansion-plan.md`](../plans/2026-04-13-pipeline-spec-expansion-plan.md)
- [`docs/plans/2026-04-13-result-stream-implementation-plan.md`](../plans/2026-04-13-result-stream-implementation-plan.md)
- [`docs/plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md`](../plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md)
- [`docs/plans/2026-04-16-pipeline-autostart-extension-plan.md`](../plans/2026-04-16-pipeline-autostart-extension-plan.md)
- [`docs/plans/2026-04-17-canonical-owner-fence-plan.md`](../plans/2026-04-17-canonical-owner-fence-plan.md)
- [`docs/plans/2026-05-08-manager-owned-internal-service-supervision-plan.md`](../plans/2026-05-08-manager-owned-internal-service-supervision-plan.md)
- [`docs/plans/2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`](../plans/2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md)
- [`docs/plans/2026-05-08-deterministic-manager-service-reconciler-plan.md`](../plans/2026-05-08-deterministic-manager-service-reconciler-plan.md)
- [`docs/plans/2026-05-09-runtime-liveness-probe-registry-plan.md`](../plans/2026-05-09-runtime-liveness-probe-registry-plan.md)
- [`docs/plans/2026-05-09-prune-path-unification-plan.md`](../plans/2026-05-09-prune-path-unification-plan.md)
- [`docs/plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md`](../plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md)
- [`docs/plans/2026-05-11-manager-work-stealing-dispatch-plan.md`](../plans/2026-05-11-manager-work-stealing-dispatch-plan.md)
- [`docs/plans/2026-05-11-manager-serve-operational-log-plan.md`](../plans/2026-05-11-manager-serve-operational-log-plan.md)
- [`docs/plans/2026-05-12-bounded-task-monitor-cleanup-policy-plan.md`](../plans/2026-05-12-bounded-task-monitor-cleanup-policy-plan.md)
- [`docs/plans/2026-05-12-task-monitor-cleanup-composition-refactor-plan.md`](../plans/2026-05-12-task-monitor-cleanup-composition-refactor-plan.md)
- [`docs/plans/2026-05-13-manager-liveness-and-leadership-robustness-plan.md`](../plans/2026-05-13-manager-liveness-and-leadership-robustness-plan.md)
- [`docs/plans/2026-05-13-internal-state-machine-helper-plan.md`](../plans/2026-05-13-internal-state-machine-helper-plan.md)
- [`docs/plans/2026-05-13-task-monitor-pong-policy-stats-plan.md`](../plans/2026-05-13-task-monitor-pong-policy-stats-plan.md)
- [`docs/plans/2026-05-15-swappable-task-log-family-scanner-plan.md`](../plans/2026-05-15-swappable-task-log-family-scanner-plan.md)
- [`docs/plans/2026-05-15-manager-hot-loop-reduction-plan.md`](../plans/2026-05-15-manager-hot-loop-reduction-plan.md)
- [`docs/plans/2026-05-15-manager-reactor-hot-loop-follow-up-plan.md`](../plans/2026-05-15-manager-reactor-hot-loop-follow-up-plan.md)
- [`docs/plans/2026-05-16-monitor-durable-collation-store-plan.md`](../plans/2026-05-16-monitor-durable-collation-store-plan.md)
- [`docs/plans/2026-05-19-task-monitor-bounded-control-cleanup-plan.md`](../plans/2026-05-19-task-monitor-bounded-control-cleanup-plan.md)
- [`docs/plans/2026-05-18-reactive-task-loop-hot-probe-plan.md`](../plans/2026-05-18-reactive-task-loop-hot-probe-plan.md)
