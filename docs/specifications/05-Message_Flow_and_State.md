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

- `ctrl_in` receives commands such as `STOP`, `STATUS`, and `PING`
- `ctrl_out` carries task-local replies and terminal notifications
- `weft.log.tasks` remains the durable audit trail rather than the interactive
  reply channel
- active STOP/KILL may delete the raw `ctrl_in` message as an internal
  handoff detail, but public acknowledgement remains the post-unwind
  `ctrl_out` reply plus the terminal task-log event on the main task thread

_Implementation mapping_: `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`, `weft/commands/tasks.py`.

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

### 3.2 Heartbeat Service Flow

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
- helper startup ensures a manager exists, submits the internal heartbeat
  runtime through the ordinary spawn path, and waits for live endpoint
  resolution of `_weft.heartbeat`
- registration messages are ordinary inbox payloads with `action` `upsert` or
  `cancel`
- `upsert` resets `next_due_at` to `now + interval_seconds`
- intervals are integer seconds with a first-slice minimum of 60 seconds
- on each due wake, the canonical owner writes at most one message per
  registration to the configured destination queue, then advances to the next
  future slot
- if a due registration is late, the service coalesces to one emit and does
  not replay every missed slot
- duplicate heartbeat services converge by loser exit once a lower-TID live
  owner is positively visible
- the heartbeat service is an interval emitter, not a scheduler: there is no
  cron syntax, wall-clock scheduling, timezone handling, or missed-run replay

_Implementation mapping_: `weft/core/heartbeat.py`;
`weft/core/tasks/heartbeat.py`; `weft/core/endpoints.py`.

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
```

Current rules:

- `weft.log.tasks` is the durable lifecycle log
- CLI status surfaces reconstruct task snapshots from that log plus the latest
  `weft.state.tid_mappings` entries and live runtime liveness where needed; they
  do not depend on a separate state database
- host tasks that still look `running` or `spawning` in the durable log but
  have no runtime proof are treated as stale after the configured status
  liveness window, unless a live manager registry record still proves a manager
  task is active
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
`weft/commands/status.py` log replay and snapshot collection;
`weft/commands/result.py` materialization and completion waits;
`weft/commands/_result_wait.py`;
`weft/commands/_task_history.py`;
`weft/commands/_streaming.py`.

### 6. Manager Spawn Flow [MF-6]

Managers consume `weft.spawn.requests`, validate and expand TaskSpecs, launch
child tasks, and seed initial inbox payloads when present.

Current submission-reconciliation rules:

- if the submitted TID is already visible through task logs or TID mappings,
  the submission is treated as spawned
- if a manager has already emitted `task_spawn_rejected` for that child TID,
  the submission is treated as rejected
- if the exact message is still in `weft.spawn.requests`, the CLI may delete it
  and report submission failure
- if the exact message has moved into a manager reserved queue, the CLI must
  not claim rollback succeeded; recovery is manual from that reserved queue
- if none of those surfaces prove success or rollback, the CLI reports an
  explicit unknown submission outcome keyed by TID

Current manager-dispatch fence rules:

- after reserving a spawn request and building the child TaskSpec, the manager
  performs one final ownership check before `_launch_child_task()`
- only positive `self` ownership permits launch
- on positive `other` ownership proof, the manager must not launch the child;
  it becomes non-dispatching for later loop iterations before attempting to
  move the exact reserved message back to `weft.spawn.requests`
- if that exact-message move succeeds, the manager emits
  `manager_spawn_fenced_requeued` with required fields `child_tid`,
  `leader_tid`, `reserved_queue`, and `message_id`
- if that exact-message move fails, the manager leaves the exact request in
  reserved and emits `manager_spawn_fenced_stranded` with required fields
  `child_tid`, `leader_tid`, `reserved_queue`, and `message_id`
- on `none` or `unknown`, the manager must not launch and must not requeue on
  guesswork; it leaves the exact request in reserved, emits
  `manager_spawn_fence_suspended` with required fields `child_tid`,
  `reserved_queue`, `message_id`, and `ownership_state`, and enters
  manager-wide dispatch suspension
- while dispatch-suspended, the manager may continue control handling and child
  supervision, but it must not reserve or launch later spawn requests until a
  later ownership check resolves to `self` or `other`
- while a fenced exact request is still pending recovery in the manager's
  private reserved queue, the manager must not leadership-yield, idle-exit, or
  run ensure-style autostart scans that enqueue more undispatchable work
- if ownership later resolves back to `self`, the manager exact-message
  requeues the older fenced request back to `weft.spawn.requests` before later
  inbox work resumes
- exact-message requeue recovery is bounded by
  `MANAGER_DISPATCH_RECOVERY_MAX_ATTEMPTS`; when the ceiling is reached, the
  manager emits `manager_spawn_fence_recovery_exhausted` with required fields
  `child_tid`, `leader_tid`, `reserved_queue`, `message_id`,
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
`weft run --pipeline`, and internal runtime classes such as pipelines and the
built-in heartbeat service travel on the manager-owned spawn envelope rather
than public TaskSpec metadata. The manager only treats a manifest launch or restart
as consumed after the synthesized spawn request is successfully written to the
ordinary manager inbox queue, and ensure-mode manifests are rescanned
immediately after a tracked autostart child exits.

_Implementation mapping_: `weft/core/manager.py` — `Manager._handle_work_message`,
`Manager._build_child_spec`, `Manager._apply_final_dispatch_fence`,
`Manager._requeue_reserved_spawn_request`,
`Manager._record_dispatch_recovery_failure`,
`Manager._control_allows_child_launch`, `Manager.process_once`;
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
  runtime handle; generic manager code must not inspect Docker or trust a
  container-local PID as a host PID
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
[`docs/plans/2026-04-24-runtime-handle-authority-migration-plan.md`](../plans/2026-04-24-runtime-handle-authority-migration-plan.md).

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

Current cleanup is task-owned:

- task-owned cleanup may remove spilled output when `cleanup_on_exit` is enabled
- there is no built-in age-based output sweeper in the current contract

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
  and `weft.state.endpoints` are runtime-only bookkeeping queues; they may be
  read for live reconciliation but are not durable application history
- `weft system tidy` handles backend-native cleanup of empty queues and broker
  maintenance
- there is no separate queue-lifecycle service in the current contract

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

- [`docs/plans/2026-04-14-spawn-request-reconciliation-plan.md`](../plans/2026-04-14-spawn-request-reconciliation-plan.md)
- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-09-manager-bootstrap-unification-plan.md`](../plans/2026-04-09-manager-bootstrap-unification-plan.md)
- [`docs/plans/2026-04-13-pipeline-spec-expansion-plan.md`](../plans/2026-04-13-pipeline-spec-expansion-plan.md)
- [`docs/plans/2026-04-13-result-stream-implementation-plan.md`](../plans/2026-04-13-result-stream-implementation-plan.md)
- [`docs/plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md`](../plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md)
- [`docs/plans/2026-04-16-pipeline-autostart-extension-plan.md`](../plans/2026-04-16-pipeline-autostart-extension-plan.md)
- [`docs/plans/2026-04-17-canonical-owner-fence-plan.md`](../plans/2026-04-17-canonical-owner-fence-plan.md)
