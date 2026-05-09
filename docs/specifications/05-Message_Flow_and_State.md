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
- `weft.log.tasks` remains the durable audit trail rather than the interactive
  reply channel
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
- terminal task-local notifications must be typed JSON envelopes with
  `type="terminal"`, `source="task"` or `source="manager"`, `tid`, `status`,
  and `timestamp`; optional fields include `error` and `return_code`
- one-shot non-persistent task success publishes a task-owned typed terminal
  `ctrl_out` envelope after the task reaches `completed`, so task-local
  terminal proof exists alongside the `work_completed` task-log event
- readers must ignore ordinary control replies, stderr stream chunks, malformed
  JSON, and other `ctrl_out` payloads when looking for terminal state
- the shared task-evidence reader may use a typed terminal `ctrl_out` envelope
  as terminal observation proof when task-log terminal proof is missing
- manager-authored terminal envelopes are supervisor observations for child
  wrapper death only; they must be written to the child `ctrl_out` queue, never
  to outbox, and only when task-owned terminal proof is not already visible
- active STOP/KILL may delete the raw `ctrl_in` message as an internal
  handoff detail, but public acknowledgement remains the post-unwind
  `ctrl_out` reply plus the terminal task-log event on the main task thread

_Implementation mapping_: `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`, `weft/core/manager.py`,
`weft/commands/task_evidence.py`, `weft/commands/tasks.py`.

Implementation plan backlinks:

- `docs/plans/2026-04-30-known-tid-terminal-snapshot-api-plan.md`
- `docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`
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
  there is no separate endpoint lease or heartbeat contract
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
- duplicate heartbeat services converge through the manager-owned singleton
  service contract. Live ownership is proved by tracked child state, a live
  runtime handle, or keyed `PING`/`PONG`; stale non-terminal rows without live
  proof do not block replacement. Helper-side endpoint validation classifies
  the endpoint as service-candidate evidence and uses the shared singleton
  summary rules, so terminal task-log proof for the endpoint TID rejects stale
  endpoint rows even if older endpoint registry data remains.
- the heartbeat service is an interval emitter, not a scheduler: there is no
  cron syntax, wall-clock scheduling, timezone handling, or missed-run replay
- the supervised `TaskMonitorTask` uses heartbeat registrations for periodic
  wake messages to its own `T{tid}.inbox`; if registration is temporarily
  unavailable, the monitor records operational health and falls back to its
  bounded local interval

_Implementation mapping_: `weft/core/heartbeat.py`;
`weft/core/tasks/heartbeat.py`; `weft/core/manager.py`;
`weft/core/manager_services.py`; `weft/core/endpoints.py`.

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
TaskMonitor heartbeat wake -> T{monitor_tid}.inbox -> bounded processor cycle
```

Current rules:

- `weft.log.tasks` is the durable lifecycle log
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
- task-monitor log records and processor summaries are operational output only
  in the current release. The monitor may mark broker messages as lifecycle or
  cleanup candidates. With the default `delete` processor it may delete exact
  safe cleanup candidates only. With the built-in `report_only` processor it
  does not delete, reserve, move, prune, reap, acknowledge, or unclaim them.
  The `jsonl_then_delete` processor remains fail-closed until the operational
  logging callback lands.
- when enabled, the canonical manager supervises one internal
  `TaskMonitorTask`. The supervised monitor reads the same task-log evidence by
  generator/high-water cursor, reduces lifecycle rows by TID, builds
  candidate classes such as `domain_failure`,
  `result_without_terminal`, `claimed_result_without_terminal`,
  `runtime_conflict`, `superseded_tid_mapping`, `stale_manager_registry`, and
  task-log/task-local retention classes for superseded logs and terminal
  outbox/ctrl_out rows with independent terminal-log proof, calls the
  configured processor, and advances its operational checkpoint only after a
  successful processor result. The manager owns only child supervision; it does
  not scan lifecycle queues.
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
  `weft.state.managers`; a historical non-terminal manager task row must not be
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
- `weft result` first waits for taskspec metadata or outbox/control queue names
  to materialize when those surfaces are not yet visible, then falls back to the
  shared completion wait

_Implementation mapping_: `weft/core/tasks/base.py` `_report_state_change`;
`weft/core/tasks/task_monitor.py` foreground monitor scan primitive and
supervised non-destructive monitor task;
`weft/core/task_monitoring.py` task-monitor runtime config, candidate, and
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

Current manager-dispatch fence rules:

- after reserving a spawn request and building the child TaskSpec, the manager
  performs one final ownership check before `_launch_child_task()`
- only positive `self` ownership permits launch
- on positive `other` ownership proof, the manager must not launch the child;
  it becomes non-dispatching for later loop iterations before attempting to
  move the exact reserved message back to the original spawn source queue
- if that exact-message move succeeds, the manager emits
  `manager_spawn_fenced_requeued` with required fields `child_tid`,
  `leader_tid`, `source_queue`, `reserved_queue`, and `message_id`
- if that exact-message move fails, the manager leaves the exact request in
  reserved and emits `manager_spawn_fenced_stranded` with required fields
  `child_tid`, `leader_tid`, `source_queue`, `reserved_queue`, and `message_id`
- on `none` or `unknown`, the manager must not launch and must not requeue on
  guesswork; it leaves the exact request in reserved, emits
  `manager_spawn_fence_suspended` with required fields `child_tid`,
  `source_queue`, `reserved_queue`, `message_id`, and `ownership_state`, and
  enters manager-wide dispatch suspension
- while dispatch-suspended, the manager may continue control handling and child
  supervision, but it must not reserve or launch later spawn requests until a
  later ownership check resolves to `self` or `other`
- while a fenced exact request is still pending recovery in the manager's
  private reserved queue, the manager must not leadership-yield, idle-exit, or
  run ensure-style autostart scans that enqueue more undispatchable work
- if ownership later resolves back to `self`, the manager exact-message
  requeues the older fenced request back to its original spawn source queue
  before later inbox work resumes
- exact-message requeue recovery is bounded by
  `MANAGER_DISPATCH_RECOVERY_MAX_ATTEMPTS`; when the ceiling is reached, the
  manager emits `manager_spawn_fence_recovery_exhausted` with required fields
  `child_tid`, `leader_tid`, `source_queue`, `reserved_queue`, `message_id`,
  `ownership_state`, and `attempts`
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
`Manager._build_child_spec`, `Manager._apply_final_dispatch_fence`,
`Manager._requeue_reserved_spawn_request`,
`Manager._record_dispatch_recovery_failure`,
`Manager._control_allows_child_launch`, `Manager._tick_managed_service`,
`Manager._enqueue_managed_service_request`, `Manager.process_once`;
`weft/core/manager_services.py`;
`weft/core/spawn_requests.py`;
`weft/cli/run.py`; `weft/commands/_spawn_submission.py` —
`_inspect_task_log_for_tid`, `_reconcile_submitted_spawn_once`.

Implementation plan backlink:
[`2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md`](../plans/2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md).

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
- external-supervisor manager records are still live records, not permanent
  truth: the manager refreshes its active registry record periodically, and
  lifecycle readers treat an expired external-supervisor heartbeat as stale
  unless an extension has registered a process-local liveness probe that proves
  the handle live. Lifecycle readers may mark the manager live or stale from
  that probe. A missing or inconclusive probe falls back to the heartbeat rule.
  Generic lifecycle readers must not treat supervisor/container-local PID
  fields as host process identity.
- host-pid manager records require scoped host-process identity, not just raw
  PID existence; if `observations.host_processes` includes process creation
  times, PID liveness checks must reject records whose current process identity
  does not match the recorded one
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

- task-owned cleanup may remove spilled output when `cleanup_on_exit` is enabled
- there is no built-in age-based output sweeper in the current contract
- the supervised task monitor exists in the current contract. It classifies
  lifecycle and cleanup candidates. Its default `delete` processor may delete
  exact safe cleanup candidates from runtime-state, task-log, and task-local
  queues. It must not delete active work, ambiguous evidence, claimed outbox
  residue, malformed/unknown rows, or candidates without exact message IDs.
  `report_only` remains available as a non-destructive override.
- `weft system prune --family task-log|task-local|retention` is an explicit
  operator action, not a background sweeper. Ordinary apply mode requires an
  archive artifact before deletion. Force apply mode is a human override for
  ordinary retention protections, but it still deletes only selected exact
  broker message IDs.

_Implementation mapping_: canonical prune candidate selection and exact delete
live in `weft/core/pruning/`; command rendering and CLI adaptation live in
`weft/commands/runtime_prune.py` and `weft/commands/retention_prune.py`.

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
- task cleanup closes task-owned handles
- `weft.state.managers`, `weft.state.tid_mappings`, `weft.state.streaming`,
  `weft.state.endpoints`, and `weft.state.pipelines` are runtime-only
  bookkeeping queues; they may be read for live reconciliation but are not
  durable application history
- `weft system prune` defaults to dry-run and applies only with
  `--apply`; apply mode deletes exact candidate message IDs only
- runtime pruning must preserve recent rows, malformed or unknown-shape rows,
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
- the manager-supervised `TaskMonitorTask` reports and deletes through the same
  canonical `weft/core/pruning/` candidate and exact-delete path used by
  foreground `weft system prune`, with monitor-safe policy arguments. If
  configured with `WEFT_TASK_MONITOR_PROCESSOR=delete`, it may delete exact safe
  candidate message IDs only. It must not apply archive side effects,
  force-only retention cleanup, or logging-before-delete behavior in this
  slice.
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
