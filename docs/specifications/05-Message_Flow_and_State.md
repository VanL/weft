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
result waiting live in `weft/commands/system.py`, `weft/commands/result.py`,
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
  `2026-04-16-runtime-endpoint-registry-boundary-plan` (retired — source 396c3b85; see `docs/plans/README.md`)
- liveness cleanup custody plan:
  `2026-08-29-liveness-reaper-and-custody-split-plan` (retired — source 396c3b85; see `docs/plans/README.md`)
- related service-health convergence plan:
  `2026-05-09-service-liveness-and-health-convergence-plan` (retired — source 396c3b85; see `docs/plans/README.md`)
- related manager-service authority hardening plan:
  `2026-05-10-manager-service-authority-boundary-hardening-plan` (retired — source 396c3b85; see `docs/plans/README.md`)
- related reactive task-loop hot-probe plan:
  `2026-05-18-reactive-task-loop-hot-probe-plan` (retired — source 396c3b85; see `docs/plans/README.md`)
- event-routed PING/PONG plan:
  [`docs/plans/2026-09-18-event-routed-manager-pong-plan.md`](../plans/2026-09-18-event-routed-manager-pong-plan.md)

## Table of Contents

- [Message Flow Patterns [MF-0]](#message-flow-patterns-mf-0)
  - [1. Task Submission Flow [MF-1]](#1-task-submission-flow-mf-1)
  - [2. Message Processing Flow with Reservation [MF-2]](#2-message-processing-flow-with-reservation-mf-2)
  - [3. Control Flow [MF-3]](#3-control-flow-mf-3)
  - [3.1 Named Endpoint Discovery [MF-3.1]](#31-named-endpoint-discovery-mf-31)
  - [3.2 Heartbeat Service Flow [MF-3.2]](#32-heartbeat-service-flow-mf-32)
  - [4. Pipeline Flow [MF-4]](#4-pipeline-flow-mf-4)
  - [5. State Observation Flow [MF-5]](#5-state-observation-flow-mf-5)
  - [6. Manager Spawn Flow [MF-6]](#6-manager-spawn-flow-mf-6)
  - [7. Manager Bootstrap Flow [MF-7]](#7-manager-bootstrap-flow-mf-7)
  - [8. Failure Recovery Flow](#8-failure-recovery-flow)
- [State Machine](#state-machine)
- [TaskSpec Redaction](#taskspec-redaction)
- [Large Output Handling](#large-output-handling)
  - [Strategy for Outputs Exceeding the Broker Message Limit](#strategy-for-outputs-exceeding-the-broker-message-limit)
  - [Current Consumer and CLI Behavior](#current-consumer-and-cli-behavior)
  - [Large-Output Reference Format [MF-2.1]](#large-output-reference-format-mf-21)
  - [Cleanup Boundary](#cleanup-boundary)
- [Queue Management Patterns](#queue-management-patterns)
  - [Unified Reservation Pattern](#unified-reservation-pattern)
  - [Current Queue Lifecycle Management](#current-queue-lifecycle-management)
- [Scope Boundary](#scope-boundary)
- [Related Documents](#related-documents)
- [Related Plans](#related-plans)

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

Every ordinary submission, including same-context runtime submissions, enters `weft.spawn.requests`. System-owned root, submitter, and finite-ancestor (`ancestry_tids`) identity travel in the spawn envelope and runtime transport; no future child count is required. Caller metadata does not grant internal routing, lifecycle class, or ancestry authority. The scheduler selects an exact accepted request ID; admission precedes its atomic move to the Manager's standard reserved queue. Existing validation, launch, drain fences, and reserved recovery remain authoritative, with acknowledgement subject to the launch-handoff rule below. The removed `weft.spawn.children` source and `children_reserved` role have no steady-state producer, consumer, registry field, or admission policy. The existing trusted internal source is retained.

Unclassifiable malformed requests reach the existing validator through bounded exact-ID reservation, terminal-error, and acknowledgement handling. Validation-only rejection consumes no live frontier and may proceed under resource pressure; it cannot launch valid work around the measured gate. Uncertain observations remain pending for retry.

Queue-first ordering is deliberate. A successful spawn-request write commits
acceptance under its returned TID before manager bootstrap proof completes.
Manager discovery and bootstrap are post-acceptance availability work. A later
readiness failure must not delete, resubmit, or rewrite the accepted request or
remove its TID from the caller result. Reconciliation by that TID distinguishes
spawned, queued, reserved, rejected, and unknown-location evidence. All but an
authoritative rejection retain historical acceptance; read failure also
preserves acceptance with an explicit diagnostic. Spawn-write failure or an
ambiguous write outcome remains a pre-acceptance error.

Public spawn submission is also a trust boundary. Submission strips reserved
internal runtime, endpoint-claim, manager-service, and autostart authority
metadata from caller-provided TaskSpecs before queueing. Manager-owned internal
spawn paths may still use those keys inside explicit internal runtime
envelopes.

During one abnormal submission-reconciliation observation, retained task/child spawn
evidence and an inspected task-log horizon prevent replay of already inspected history.
Both exact top-level `tid` and manager `child_tid` records remain supported. Queue
presence, reservation, spawned proof, and rejection keep their existing precedence.
Administrative backfill behind a completed horizon requires a fresh observation,
consistent with [MF-5].

_Implementation mapping_: `tests/core/test_spawn_requests.py`; `tests/helpers/family_workload.py`; `weft/commands/run.py::_enqueue_taskspec`;
`weft/commands/submission.py::ensure_manager_after_submission`;
`weft/commands/_spawn_submission.py::reconcile_submitted_spawn`;
`weft/core/spawn_requests.py` `submit_spawn_request`;
`weft/core/manager.py` `Manager._handle_work_message`,
`Manager._build_child_spec`, `Manager._launch_child_task`.
`weft/core/runtime_identity.py` owns immutable same-context caller identity;
`weft/core/launcher.py` and `weft/core/runners/host.py` install and transport it.
The shared spawn writer selects the source, and
`weft/commands/_spawn_submission.py` reconciles every source/reserved pair.
`weft/core/manager_runtime.py::_read_public_spawn_backlog` recognizes ordinary backlog for the existing manager-availability recovery owner.
`tests/core/test_child_spawn_validation.py` covers submitter validation and
accepted-child custody after parent exit; `tests/core/test_runtime_identity.py`
covers wrapper/worker transport.
`tests/system/test_child_admission.py` covers the public host-worker route;
`tests/system/test_family_scheduling.py` proves delayed descendants and early root exit under measured admission and work-conserving family ordering;
`tests/system/test_measured_admission_pg.py` proves progress under measured PostgreSQL pressure;
`tests/system/test_family_recovery.py` proves missing-snapshot startup release, actual Manager crash, cleanup custody, cold adoption, and eventual handoff on both backends.

Implementation resource-ownership note: the initial availability check borrows
from the prepared submission's session. An explicitly retained client may lend
that session; otherwise submission owns one bounded session. Both paths own one
short connection operation across the committed spawn write and initial
availability observation, capture the accepted TID before operation exit, and
end the operation before per-TID reconciliation, manager startup, or any wait.
Submission never closes or recycles a borrowed session. See the
`2026-09-17-submission-manager-check-cost-plan` (retired — source 396c3b85; see `docs/plans/README.md`).

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
- error, timeout, or external control applies the configured reserved
  policy (`keep` or `clear`) once within that disposition operation; rows
  that survive a failed application, or a failed success-path
  acknowledgement delete, are left in `T{tid}.reserved` for the explicit
  recovery path below
- a successful work outcome uses only success acknowledgement for its
  input; deferred STOP/KILL completes task termination and acknowledges
  control without applying reserved policy to that completed input,
  even when its acknowledgement delete failed. A later independent idle
  control operation may apply configured bulk `clear` to remaining backlog
- a live task may return a reserved row it has not begun executing to
  its own inbox (pause, or a second reservation arriving while a work
  item is in flight); that is reservation bookkeeping under [QUEUE.5],
  not a reserved policy
- crash leaves the message in reserved state for explicit operator
  recovery; no policy is applied at restart. Re-execution of preserved
  work is a new task plus a queue move
- direct `run_work_item()` execution has no reserved message and must not
  mutate unrelated reserved backlog as if it did
- persistent tasks emit `work_item_completed` for each completed message and
  `work_completed` only when the task itself reaches a terminal finish
- `weft.state.streaming` marks active outboxes that `weft result --all` must
  skip so live stream sessions are not re-read as static results

Agent work follows the same outer reservation flow as command and function
targets.

Reactor ownership hardening does not replace Weft's reservation contract with
peek/checkpoint delivery or a second durable output ledger. A crash still leaves
the active input in `T{tid}.reserved` for operator recovery ([MF-5] prune);
no reserved policy is applied at restart.
The SimpleBroker reference reactor's sidecar outbox is specific to its
peek/checkpoint example and is not part of ordinary Weft task delivery.

_Implementation mapping_: `weft/core/tasks/consumer.py`,
`weft/core/tasks/base.py`, `weft/core/outbox.py::decode_stream_text`,
`tests/tasks/test_task_execution.py`.

### 3. Control Flow [MF-3]

Current flow:

```text
requester ctrl_in  <---- PONG ----  target task
       ^                              ^
       |                              |
 task reactor             PING {request_id, reply_to}
```

The control plane is explicit:

- a PING request is exactly `{command, request_id, reply_to}`. `command` is
  exactly `PING`; `request_id` and `reply_to` are nonblank strings; and
  `reply_to` is the requester's own `ctrl_in` queue. Other control requests
  retain the exact `{command}` or `{command, request_id}` envelopes. Raw
  command strings, extra keys, and alternate casing are not supported.
- every Weft-owned controller uses the shared control-envelope encoder.
  `BaseTask` uses the matching parser and rejects malformed, non-object, or
  unsupported envelopes without treating them as commands. A rejected row is
  exact-acknowledged without a `ctrl_out` reply so it cannot block a later
  valid request.
- `ctrl_out` carries non-PONG task-local replies and terminal notifications.
  Readers ignore malformed or unrelated replies as protocol noise; this
  robustness does not make removed reply formats current contracts.
- `weft.log.tasks` remains the runtime lifecycle evidence stream rather than
  the interactive reply channel. It is durable while retained, but it is not
  legal, forensic, or audit-retention evidence.
- a requester sends a keyed PING to the target's `ctrl_in` and names its own
  `ctrl_in` in `reply_to`. A long-lived task names its configured queue. A
  synchronous probe mints a TID, owns `T{tid}.ctrl_in` for the duration of the
  probe, drains it through `MultiQueueWatcher`, and deletes it on every exit
  path. The ephemeral requester registers no task record and creates no other
  task queue. Requester death or a reply written after requester cleanup may
  leave an orphan row; the existing name-derived dead-task sweep retires it.
- `PING` writes exactly one JSON `PONG` control response to `reply_to`, never
  to the target's `ctrl_out`. The response contains `tid`,
  `timestamp`, `task_status`, `paused`, `should_stop`, `runner`, optional
  `activity`, optional `waiting_on`, and optional best-effort `runtime`
  details from the active runner; it echoes `request_id` and omits `reply_to`
- a manager PONG contains `role="manager"`, `requests`, `ctrl_in`, `ctrl_out`,
  `outbox`, `weft_context`, `task_status`, and `should_stop`. A matched PONG
  proves manager selection authority only when every field is present with its
  exact type, every queue and context value matches the candidate and resolved
  context, `task_status` is exactly `created`, `spawning`, or `running`, and
  `should_stop is False`. A draining manager publishes `should_stop=true` in
  its snapshot even while its drain loop remains active. Missing, malformed,
  mismatched, draining, stopping, or terminal PONG data is absence of authority
  proof, not proof that the manager is dead
- a keyed PONG matches only the exact canonical reply fields
  `command="PING"`, `status="ok"`, `message="PONG"`, the requested `request_id`,
  and the target `tid`. Readers ignore normalized or otherwise malformed reply
  noise rather than turning it into liveness proof
- automatic manager recovery has at most two keyed proof rounds. Each uses an
  ephemeral requester and the
  control-surface timeout rather than the shorter competing-launch settlement
  grace. Timeout and I/O failure remain distinct, and reply matching remains
  separate from manager dispatch eligibility. These synchronous client probes
  are separate from Manager reactor probes, whose absence is evaluated by the
  existing leadership or active-service policy cadence
- an abnormal automatic-start decision emits one structured diagnostic with the
  incumbent TID, submitted TID when known, last probe request ID, and decision
  reason. It contains no control payload or secret and does not alter proof or
  acceptance
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

A task publishes completion through its existing lifecycle-reporting path. A task-owned
terminal record is both a lifecycle event and a typed terminal envelope: it retains the
lifecycle fields, including the redacted TaskSpec, and includes `type="terminal"`,
`source="task"`, `tid`, `status`, `timestamp`, and applicable `error` and `return_code`.
The same serialized record is atomically published to `weft.log.tasks` and the
configured task `ctrl_out`, using SimpleBroker's public exact-destination broadcast with
missing destinations created. Duplicate destination names receive one copy. Nonterminal
persistent `work_item_completed` boundaries are published to the same two destinations,
without the terminal discriminator. Other nonterminal lifecycle events remain global-log
events.

This completion publication applies to every BaseTask terminal path, including
pipeline/edge tasks, interactive startup/finalization, cancellation, timeout, and
failure. It replaces separate task-owned global-terminal and local-terminal writes. A
work-item boundary remains nonterminal. Completion records use the existing bounded
completion-write retry policy; retry exhaustion remains reported and nonfatal to process
shutdown. Retrying the same serialized fact may leave duplicate records after an
ambiguous commit; readers do not turn duplicate observations into duplicate output
settlement.

Completion is published after the task has finalized the output belonging to that
boundary. Interactive STOP/KILL, external termination signals, and parent loss record
the terminal state on the owner thread, unwind the session, and publish completion after
its final stdout/stderr frames. Queued control is acknowledged after this unwind. It does not publish local completion while the session can
still produce final output. The payload for one publication attempt series is serialized
once and reused on retry. Terminal runtime mapping publication follows completion
publication attempts, so a control observer cannot act on a terminal mapping before
the task has attempted to commit its completion proof. The mapping remains best-effort
liveness evidence, including after bounded completion-publication failure.

- readers must ignore ordinary control replies, malformed JSON, and unrelated
  `ctrl_out` payloads when looking for terminal state
- after strict request parsing, a non-request `ctrl_in` row is passed to the
  task's protected reply hook and acknowledged once. A reactor-integrated
  requester matches PONG there; a synchronous requester reads its own
  ephemeral queue after the same watcher wait. Malformed and unmatched replies,
  plus late replies that arrive while the requester still exists, are consumed
  without a state transition. A reply written after an ephemeral requester has
  exited remains under that requester's queue name until the name-derived
  dead-task sweep retires it. PING/PONG uses no target-`ctrl_out` scan, reply
  sweep, dynamic watcher membership, or separate polling loop
- non-PONG keyed control replies remain owned and retired by the prober that
  issued the `request_id`: an exact matching reply is deleted on match, and the
  prober may sweep rows bearing its own request ID at timeout or abandonment.
  Rows keyed to another request and terminal envelopes remain untouched
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
- active STOP/KILL may delete the structured `ctrl_in` message as an internal
  handoff detail, but public acknowledgement remains the post-unwind
  `ctrl_out` reply plus the terminal task-log event on the main task thread
- active STOP/KILL deferral applies to the structured JSON control envelope.
  STOP/KILL envelopes that include `request_id` must echo that value in the
  eventual post-unwind acknowledgement.
- every other reply or acknowledgement to a valid keyed control request,
  including PAUSE and RESUME, also echoes the request's `request_id`.
- a `KILL` acknowledgement means the task accepted the command; it is progress
  evidence only. Readers and CLI control helpers must not promote ack-only
  evidence into terminal lifecycle state. Kill success is proven by terminal
  `killed` evidence, by observed controlled host PIDs becoming dead after a
  kill action, or by an authoritative runner control surface when no
  host-observable PID exists.

_Implementation mapping_: canonical wire encoding and parsing live in
`weft/core/control_messages.py::encode_control_message`,
`weft/core/control_messages.py::decode_control_object`, and
`weft/core/control_messages.py::parse_control_request`; task policy lives in
`weft/core/tasks/base.py::BaseTask._handle_control_message`,
`weft/core/tasks/base.py::BaseTask._handle_control_command`,
`weft/core/tasks/consumer.py::Consumer._poll_active_control_once`, and
`weft/core/tasks/consumer.py::Consumer._finalize_deferred_active_control`;
pipeline broadcast and policy live in
`weft/core/tasks/pipeline.py::PipelineTask._broadcast_control` and
`weft/core/tasks/pipeline.py::PipelineTask._handle_control_command`; manager
policy, drain-time control, and snapshots live in
`weft/core/manager.py::Manager._process_reactor_turn`,
`weft/core/manager.py::Manager._handle_control_command`, and
`weft/core/manager.py::Manager._control_snapshot_fields`; manager probes and
runtime stop writes live in `weft/core/control_probe.py::send_keyed_ping_probe`,
`weft/core/control_probe.py::coerce_pong_response`,
`weft/core/control_probe.py::pong_proves_dispatch_eligible`, and
`weft/core/manager_runtime.py::_send_stop`; command producers live in
`weft/commands/tasks.py::_send_control` and
`weft/commands/run.py::_stop_interactive_run_session`. Terminal
interpretation and CLI convergence live in `weft/core/task_evidence.py` and
`weft/commands/control_convergence.py`. Direct protocol coverage lives in
`tests/core/test_control_messages.py`, `tests/core/test_control_probe.py`,
`tests/tasks/test_control_channel.py`, and `tests/tasks/test_task_execution.py`.
Interactive startup failure and session finalization use
`weft/core/tasks/base.py::BaseTask._report_state_change`;
`weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_handle_control` acknowledges after
shutdown. Ordering and retry coverage is in
`tests/tasks/test_task_interactive.py`; see `2026-08-31-monitor-and-task-correctness-fixes-plan` (retired — source 396c3b85; see `docs/plans/README.md`).
`weft/commands/tasks.py::_observe_control_envelopes` consumes ordinary replies while
retaining valid completion facts for every owner of a shared control route.
The wire module owns shape only, not task or manager policy.

Implementation plan backlinks:
`2026-08-25-bounded-tid-mapping-publication-plan` (retired — source 396c3b85; see `docs/plans/README.md`);

- retired: 2026-04-30-known-tid-terminal-snapshot-api-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-06-task-evidence-reconciliation-model-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-10-control-and-service-convergence-state-machine-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-06-terminal-publication-hardening-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-07-extended-ping-pong-state-probe-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-07-lifecycle-monitor-archive-sink-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-07-manager-selection-ping-pong-liveness-plan — source 396c3b85; see docs/plans/README.md

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
- resolve and list surfaces classify claims whose owner is terminal or
  no longer live out of their results; they never delete registry rows.
  The owning task exact-deletes its own claim on clean shutdown; the
  runtime pruning engine is the sole deleter of stale claims written by
  other processes, behind its minimum-age gate
- after matching endpoint claims, read valid snapshots only for candidate owner
  TIDs. Empty candidate sets require no state reads; pure liveness predicates
  receive evidence values, not broker callbacks
- Exact-name endpoint resolution reads lifecycle and runtime evidence only for matched
  candidate owner TIDs, using shared task-evidence classification. Explicitly bulk
  unfiltered or glob listings may retain one global history pass to avoid repeated
  backend substring scans. Terminal proof retains precedence over live runtime handles.
  An empty candidate set performs no history or task-state reads. Candidate filtering
  bounds transferred/decoded history; it does not claim that public broker substring
  search is an indexed TID lookup. Task processes publish host-PID liveness through
  `runtime_handle` when no runner-specific handle exists; there is no separate endpoint
  lease or heartbeat contract.
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
- Heartbeat `destination_queue` remains payload-directed runtime egress, not a
  construction-fixed reactor role. Upsert validation rejects a destination
  equal to the Heartbeat task's own inbox, reserved, or control lanes and rejects
  BaseTask's reserved `weft.` system queue namespace before storing the
  registration. An ordinary destination task queue remains valid. Rejection uses
  the existing invalid-request lifecycle event and reserved-on-error policy and
  must not block a later valid mutation.
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
`weft/core/tasks/heartbeat.py::HeartbeatTask._handle_work_message` validates
payload-directed destinations against its resolved task-local roles and the
reserved weft. system queue namespace before storing a registration;
dynamic-egress rejection/progress coverage lives in
`tests/tasks/test_heartbeat.py`.

### 4. Pipeline Flow [MF-4]

Current pipeline flow is task-shaped:

- `weft run --pipeline` submits one pipeline task
- the pipeline task owns public task-style surfaces
- stage and edge work are materialized as ordinary child tasks underneath that
  parent
- pipeline status is exposed without inventing a second top-level operator
  model
- the current first-class pipeline contract is linear: each pipeline-compatible
  stage emits exactly one handoff payload for the next edge. A stage that leaves
  multiple visible handoff payloads is a contract violation and the edge must
  fail clearly rather than move one payload and strand the rest. Fan-out,
  batching, merge stages, and arbitrary multi-output handoff require a separate
  spec.

The reason for this shape is consistency. Pipelines are composition over tasks,
not a separate workflow product.

### 5. State Observation Flow [MF-5]

Current flow:

```text
Task snapshots -> weft.state.tasks.<tid> -> current status/list inventory
Task completion + configured output queues -> known-TID status/result
Task lifecycle events -> weft.log.tasks -> explicit --log/history observation
Task lifecycle events -> weft.log.tasks -> task-monitor JSONL logs
Task lifecycle events -> weft.log.tasks -> Monitor-owned collation tables
TaskMonitor heartbeat wake -> T{monitor_tid}.inbox -> bounded processor cycle
manager serve operational events -> process stderr/stdout only
```

LivenessMonitor adds a periodic reaper flow, not a request/reply service:

```text
periodic due time -> latest tid-mapping row / runtime generation
                  -> bounded host or extension probe worker
                  -> reactor commits in-memory observation and deadline
                  -> policy: retain, or exact-delete the mapping row
```

Discovery uses one generator-based startup replay, then message-ID-cursor
incremental reads, with periodic full reconciliation. There is one latest row
and one due-heap entry per TID/generation. Missed intervals coalesce, and at
most one probe per TID is in flight with token-plus-generation commit guards.
Blocking inspection runs in bounded `ServiceTask` worker lanes; the reactor
alone touches queues. First-slice constants are: enabled by default; 5-second
interval; 300-second retirement timeout; 2-second cooperative budget;
600-second reconciliation; eight lanes. Only the enabled flag is configurable.

_Implementation mapping_: status and foreground-monitor projection in
`weft/commands/system.py` and `weft/commands/task_monitor.py`; durable and
external Monitor projection/restoration in `weft/core/monitor/store.py`,
`weft/core/monitor/external_log.py`, and
`weft/core/monitor/lifetime_report.py`; maintenance construction, explicit
`MaintenanceInputs` work records, typed diagnostic results, resource cleanup,
and owner-thread result merge in `weft/core/monitor/task_monitor.py`
(`MaintenanceWorker`, `MaintenanceWorker.capture_diagnostics`,
`_maintenance_worker_scope`, `TaskMonitor._apply_maintenance_diagnostics`,
`TaskMonitor._handle_builtin_cycle_worker_result`,
`TaskMonitor._handle_control_cleanup_worker_result`, and
`TaskMonitor._apply_worker_external_task_log_status`); manager operational projection in
`weft/core/serve_log.py`; task-owned observation acquisition in
`weft/commands/tasks.py`, `weft/commands/_result_wait.py`, and
`weft/commands/run.py::_stop_interactive_run_session`. Shared validated configuration
and evidence selection belong to `weft/core/task_evidence.py`; public snapshot
reduction belongs to `weft/commands/_task_snapshot_reducer.py`.
Maintenance input, resource and state-transfer regressions are in
`tests/tasks/test_maintenance_worker.py`; maintenance policy, scheduling and
control responsiveness coverage is in `tests/tasks/test_task_monitor.py`.

Current rules:

- `weft.log.tasks` supplies explicit retained history and debugging, not an
  ordinary status/result fallback or an audit guarantee.
- BaseTask owns lifecycle and current-observation publication. Public command
  helpers acquire task-owned queues. TaskMonitor owns private collation,
  export, cleanup selection, and diagnostics. Ordinary public operation never
  depends on Monitor tables. Verification proves public observation with absent
  Monitor storage and empty raw logs, and exact policy-authorized cleanup.
- opt-in `weft manager serve` operational JSONL records are process-log
  diagnostics only. They must not be written to `weft.log.tasks`, per-task
  queues, control queues, or runtime-state queues, and they must not be used as
  lifecycle truth, pruning evidence, or status/result reconstruction input.
- external JSONL is an [SB-0.2] boundary only for the manager-owned
  `message_timestamp`, `observed_timestamp`, and `superseded_message_id`
  fields. Operational `timestamp_ns` is a Unix-clock measurement and remains
  numeric; arbitrary diagnostic mappings are not traversed.
- CLI status surfaces read newest-valid task projections. Explicit `--log`
  adds retained raw history. Known-TID observers read resolved local queues;
  no Monitor-store fallback is available.
- terminal task-log events may carry `runner_diagnostics` for process/session
  startup, runtime readiness, and runner-boundary failure evidence. These
  diagnostics are operational metadata only. They may explain an already
  selected lifecycle event such as `work_failed`, `work_timeout`, or
  `work_limit_violation`, but they must not create, override, or infer public
  lifecycle state by themselves.
- On the task-owned terminal reporting path, terminal TID-mapping publication
  is attempted independently of terminal task-log and control publication.
  The latest mapping carries `terminal: true`, so raw task-log collation or
  deletion cannot make a completed external runner look non-terminal to the
  payload-only mapping probe. The outer hint remains runtime evidence; validated nested terminal projection
  additionally supplies lifecycle evidence under the shared precedence. A failed
  best-effort mapping write leaves the previous row conservatively live or
  undecidable.
- `weft system task-monitor` can scan `weft.log.tasks` without consuming
  broker messages and emit compact JSONL records to stdout or append-only disk
  log files under `.weft/logs/task-monitor/`
- task-monitor checkpoints under `.weft/state/task-monitor/` are
  operational cursors only; deleting or corrupting them may affect duplicate
  log output, but it must not change task lifecycle truth, public status,
  result materialization, or cleanup decisions
- task-log row/checkpoint IDs remain integers during scanning, reduction,
  foreground checkpoint-file persistence, and Python result construction.
  Monitor-table checkpoint JSON follows the explicit string-at-rest rule
  below. External task-monitor JSONL and command JSON project
  `last_task_log_timestamp`/`checkpoint_timestamp` to [SB-0.2] strings.
  Task-summary `reconciliation.observed_at` follows the same conditional
  broker-ID versus wall-clock rule as [CLI-1.2.1].
- the supervised `TaskMonitor` may maintain Monitor-owned durable collation
  tables: `weft_monitor_meta`, `weft_monitor_task_collations`, and
  `weft_monitor_task_messages`. It may also maintain
  `weft_monitor_deferred_writes`, a Monitor-owned operational outbox for
  external JSONL records that could not be written to the configured file path.
  These tables are operational read models derived from retained
  `weft.log.tasks` rows or accepted report handoffs. They are not queues, are
  not exposed through `weft queue *`, and are not public lifecycle or result
  authority. The Monitor creates/verifies only these Monitor tables, in an
  already initialized Weft broker database, and records a cached PONG error if
  the store is unavailable.
  Relational message-ID columns remain integers. Monitor-owned exact broker IDs
  embedded in table JSON text use [SB-0.2] strings at rest: checkpoint metadata
  `value_json.message_id`, collation `lifecycle_json.message_id`, nested
  pipeline `lifecycle_json.checkpoint.message_id`, collation
  `bookkeeping_json.last_message_id`, and the owned fields of deferred external
  `body_json.subject.message_id`, `body_json.monitor.message_id`,
  `body_json.monitor.first_message_id`, `body_json.monitor.last_message_id`,
  nullable `body_json.monitor.terminal_message_id`,
  `body_json.observations.message_id`, and every
  `body_json.observations.message_ids[]`. Store reads immediately restore these
  fields to integers in Python. Other TaskSpec, state, resource, diagnostic,
  and extension mappings are opaque and are not recursively rewritten.
- task-monitor log records and processor summaries are operational output only
  in the current release. The monitor may mark broker messages as lifecycle or
  cleanup candidates. With the default `delete` mode it may delete exact
  rows selected by explicit bounded cleanup policies from supported Weft-owned
  cleanup queues. Those policies may delete malformed rows only from queues
  whose schema is owned by Weft, such as `weft.log.tasks`. TID mappings are
  owned by LivenessMonitor. With the built-in `report_only` mode it does
  not delete, reserve, move, prune, reap, acknowledge, or unclaim rows. The
  `jsonl_then_delete` mode emits one `task_lifetime_report` JSONL record
  for each destructive policy-selected subject before exact deletion. If the
  configured external JSONL write fails, the Monitor writes the same report to
  `weft_monitor_deferred_writes`; deletion may proceed only after either the
  external write or the deferred write succeeds. If both fail, the selected
  subject remains undeleted and retryable.
  Passive `weft status` surfaces must render cached external-log failures and
  pending deferred writes as visible warnings without PINGing the TaskMonitor,
  opening the configured log path, querying Monitor tables, or flushing
  deferred writes.
  Lifetime report records use `record_type=task_lifetime_report`,
  `schema_version=2`, deterministic `report_id`, `source_policy` constrained
  to the four top-level cleanup policies, a `subject`, a top-level `taskspec`
  field, a baseline `lifetime` object, compact `monitor` provenance, and
  policy-specific `observations`. `taskspec` is populated whenever the policy
  has TaskSpec-shaped evidence and is `null` for inferred or state-only
  reports. For normal Monitor-store families, `taskspec` is derived from the
  collation row's TaskSpec summary and `monitor` carries the small Monitor-only
  context such as first/last/terminal message IDs and service classification.
  For inferred cleanup policies, `monitor` carries the exact
  queue/message or queue-name evidence used by the policy. Records are lifetime
  reports, not delete audit records; they do not carry an `effect=delete`
  field. The external JSONL stream is at-least-once and downstream consumers
  deduplicate by `report_id`. The configured external path is resolved when
  the TaskMonitor starts and requires a monitor restart to change; the current
  resolved path's writability is re-probed once per monitor cycle. Probe
  failures update cached diagnostics and may emit foreground operational
  warnings, but they do not write lifetime records and do not replace the
  write-or-defer-before-delete handoff rule.
  External Monitor raw records format `message_id`; collated summaries format
  `first_message_id`, `last_message_id`, and nullable
  `terminal_message_id`; lifetime reports format owned `subject`, `monitor`,
  and observation message-ID fields according to [SB-0.2]. Builders, Monitor
  relational message-ID columns, checkpoint/result objects, PONG/processor
  summaries, and cleanup candidates remain integer-valued; the owned Monitor
  JSON-at-rest fields named above are strings and normalize back to integers on
  read. Lifetime `report_id` is computed from the internal integer report
  before a non-mutating external projection, so representation alone does not
  change durable dedup identity. A deferred external write stores that
  projected record so later flush emits the same canonical JSON shape.
- when enabled, the canonical manager supervises one internal
  `TaskMonitor`. The supervised monitor reads task-log lifecycle evidence by
  generator/high-water cursor for observation and custom processors. Custom
  processors run from the resulting candidate snapshot through the registered
  `ServiceTask` processor worker group; the TaskMonitor reactor commits the
  processor result, checkpoint, and cached diagnostics after the typed worker
  event returns. Built-in cleanup modes run through a separate registered
  TaskMonitor-owned built-in cycle worker group; the reactor stays available
  for task-local PING/STATUS/STOP/KILL, heartbeat registration, and schedule
  bookkeeping while the worker scans, writes Monitor-store rows, and applies
  exact deletes.

  Before submitting maintenance, the TaskMonitor reactor captures explicit
  configuration, work identity and required state values. A fresh
  MaintenanceWorker consumes that request with invocation-owned broker resources
  on the registered lane's per-request thread and returns typed diagnostic
  updates after cleanup. The synchronous custom-collation adapter instead lends
  its reactor session on the same thread and closes only invocation-owned
  resources, preserving reactor connection reuse. State is not transferred
  by copying the live TaskMonitor or sharing it through a weak proxy. The
  reactor remains the owner of maintenance deadlines, cleanup continuation,
  cumulative logging counters and cached public diagnostics.

  For the built-in `delete` mode under collated ownership, retained
  `weft.log.tasks` rows are processed in FIFO order:
  malformed rows are exact-deleted; valid rows are folded into the Monitor
  table and then exact-deleted in the same bounded pass when running the
  built-in `delete` mode. Terminal families may be summarized and
  disposed only after the pass reaches a complete FIFO high-water; open
  families continue to use `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` plus
  stale-open policy before summary/disposition. Runtime-state pruning runs
  in the monitor's maintenance pass
  (`weft.state.tasks.<tid>` is owned by LivenessMonitor); the Monitor
  collation store is the supervised task-log deletion authority. The manager
  owns only child supervision; it does not scan
  lifecycle queues.
- under collated ownership, each retained FIFO pass reduces valid
  task-log rows through `weft/core/monitor/collation.py`, upserts one summary
  row per TID, and records each incorporated raw message ID in
  `weft_monitor_task_messages` before deleting the raw broker row. Child rows
  in `weft_monitor_task_messages` are temporary pending raw-message
  references: after exact broker deletion succeeds, or after retry observes the
  raw broker row is already absent, the Monitor physically deletes those child
  rows and reconciles the parent `raw_deleted_at_ns` when no child refs remain.
  A private manager launch-event compaction subphase may also delete exact old
  manager-authored `task_spawned` raw rows and their Monitor child refs after
  preserving the newest launch hints per manager TID. That subphase reports
  under `task_log.retention`, uses exact message IDs from
  `weft_monitor_task_messages`, and intentionally does not reconcile the
  manager parent `raw_deleted_at_ns` because the manager service family may
  still be open.
  Bounded retry sweeps use the same rule for already-ingested child refs from
  older cycles. For the `delete` mode, terminal summary emission is not a
  prerequisite for deleting a raw row that has already been folded into the
  Monitor store. For `jsonl_then_delete`, valid raw task-log rows are folded
  into the Monitor store first and exact-deleted only after the owning family
  has accepted a lifetime-report handoff. In this mode,
  `summary_emitted_at_ns` records accepted report handoff, either external
  JSONL write success or durable deferred write success; it is not public
  lifecycle truth.
  A bounded orphan-recovery pass may search for and exact-delete raw task-log
  rows for terminal/disposed families whose child refs are already absent but
  whose raw broker rows remain from an interrupted current cleanup cycle.
  Orphan recovery is retryable on probe/delete errors. A successful probe that
  finds no raw broker rows records `orphan_raw_recovery_checked_at_ns` on the
  parent collation row so the same family is not selected again until new raw
  task-log evidence resets the recovery marker.
  A separate bounded pre-checkpoint recovery pass may scan visible
  `weft.log.tasks` rows older than the forward Monitor checkpoint. It selects
  only rows missing from `weft_monitor_task_messages`, skips active TIDs and
  rows younger than the retention floor, folds valid rows back into the
  Monitor store without moving the checkpoint, and lets the ordinary
  summary/report/delete path own any later exact deletion. Malformed or
  unrecognized pre-checkpoint rows may be exact-deleted only through the same
  durable-before-delete rule used by the selected monitor mode.
  Version 5 child rows marked with `deleted_at_ns` are physically removed by
  the version 5 to 6 schema migration, not by a normal-cycle compatibility
  lane. Replaying the same raw row after a delete failure is idempotent. A
  terminal family may emit a
  compact operational summary only after the FIFO pass reaches a
  completed high-water mark: the scanned FIFO prefix reaches queue end before a
  scan limit or write/delete error. If the pass stops on scan limit, store
  write failure, or queue delete failure, family summary/disposition is skipped
  for that cycle. Summary
  readiness uses the family high-water (`last_message_id`/`last_seen_at_ns`),
  not the first terminal event timestamp. Family disposition is explicit
  retryable compact state (`disposition_reason` plus `disposition_at_ns`),
  separate from `summary_emitted_at_ns`, so summary emission can be retried
  separately from task-local runtime cleanup. Terminal task-local runtime
  cleanup runs as a separate bounded maintenance slice selected by
  Monitor-store readiness
  (`summary_emitted_at_ns`, no prior `task_control_deleted_at_ns`, and either
  terminal proof or an already disposed Monitor family). Terminal task-local
  runtime cleanup runs on a dedicated
  TaskMonitor maintenance worker after the summary/disposition high-water is
  reached; that worker owns only the queue-delete plus Monitor-store mark
  transaction for standard stale task-local queues. Standard
  `T{tid}.ctrl_in` and `T{tid}.inbox` are stale at terminal cleanup time.
  Standard `T{tid}.ctrl_out` and `T{tid}.outbox` are retained until task-log
  retention age, measured from terminal evidence when available and TID age
  only under the existing fallback. Reserved queues retain their separate
  cleanup policy. For
  old nonterminal manager or manager-supervised service owner rows, the
  Monitor may record a `stale_service_owner` or `superseded_service_owner`
  disposition without rewriting task lifecycle truth only after same-service
  registry evidence proves the row stale: either the same owner has a latest
  non-live owner row, or a different live owner exists for the same service
  key, and no active runtime evidence protects the old TID. Ambiguous rows,
  missing service-key proof, active owner rows, and rows with live runtime
  handles stay open for the normal stale-open path. A stale/superseded service-owner disposition may authorize standard
  `T{tid}.ctrl_in` cleanup immediately and standard `T{tid}.ctrl_out` cleanup
  only at result-retention age. This does not turn the owner row into terminal
  lifecycle truth or authorize deleting its inbox, outbox, reserved, global,
  or custom queues.
  For terminal non-completed families, the reserved policy is Monitor-table
  driven: it selects rows with `reserved_probe_needed`, records
  `reserved_cleanup_checked_at_ns` after successfully deleting the reserved
  queue or proving it is already absent, and leaves the row retryable on
  delete/probe errors.
  Once no child message rows remain and raw deletion, summary emission,
  disposition, task-local control cleanup, and any required reserved cleanup
  proof have all been recorded, the Monitor may physically retire the compact
  parent row from
  `weft_monitor_task_collations`.
  A separate maintenance worker slice may delete eligible stale standard
  `T{tid}.reserved` queues after monitor-table proof or stale no-monitor
  evidence, while preserving active runtime owners. Missing standard reserved
  queues are successful no-row probes for monitor-table families, not repeated
  work. Each built-in cycle attempts
  retained raw task-log exact deletion in the built-in cycle worker before the
  reactor launches another registered runtime-cleanup worker. The reactor launches
  terminal cleanup, reserved cleanup, and dead-TID cleanup as discrete
  runtime-cleanup worker slices; one worker result must not mix those cleanup
  classes or start nested cleanup executor threads. It marks catch-up pending
  when backlog remains or the internal slice cap/deadline is hit. PONG
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

Monitor repair handles only states reachable from the current writer and
current cleanup transactions, including interrupted exact deletion,
pre-checkpoint gaps, pending deferred writes, and forced-process residue. Data
written by an older Monitor schema is handled by its schema migration, not by
a permanent normal-cycle compatibility lane.

- terminal Monitor collation rows may emit compact operational summaries
  through the configured task-monitor sink. Ordinary user-task rows emit
  `record_type=task_summary`. Manager, built-in service, and manager-authored
  managed-service rows emit `record_type=service_summary`, keep the compact
  task row under `task`, and include a service classification under `service`.
  The classification is conservative: it may use `role=manager`, built-in
  service roles, reserved service metadata, autostart metadata, and internal
  runtime class metadata, but it must not classify domain-specific metadata
  such as `runtime=internal` as service evidence by itself. External collated
  JSONL uses the same classification: task rows emit
  `record_type=task_summary` and manager or managed-service rows emit
  `record_type=service_summary`. The external writer does not rename both
  classes to `task_log_collated` or add a redundant compatibility
  discriminator. This record-shape change advances the shared external
  task-log `schema_version` from 1 to 2; all external task-summary,
  service-summary, raw, and lifetime-report records use version 2 after the
  cutover. In collated mode, durable
  Monitor ingestion gates raw `weft.log.tasks` deletion; external summary
  emission gates only family summary/disposition retry, not resurrection of
  already ingested raw rows. Raw external mode remains emit-before-delete and
  does not write Monitor collation rows. Successful completed lifecycles do not
  require reserved-queue probes. After any required summary is emitted,
  terminal disposition may delete whole standard task-local runtime queues
  through public SimpleBroker queue APIs from the dedicated TaskMonitor
  runtime-cleanup worker. For a proven-dead TID, `T{tid}.ctrl_in` and `T{tid}.inbox` are stale
  immediately. `T{tid}.ctrl_out`, `T{tid}.outbox`, and `T{tid}.reserved` remain
  retention-gated and are selected by the same
  dead-TID policy. Standard completion/output use the snapshot-presence hold
  and task-authored local/Monitor terminal anchors described below; missing
  Monitor records never bypass task-owned evidence. Only absent qualifying
  evidence permits the name-derived TID-age fallback. Reserved retention
  keeps its independent existing age policy. Dead-TID cleanup excludes manager, global, service, and custom
  control queues; the only manager/service exception is the disposed
  stale-service-owner path above, and that exception is limited to the standard
  task-local control pair.
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
- Task-state publication follows [OBS.6] and [OBS.6a]. Producers blindly
  append complete snapshots to their own `weft.state.tasks.<tid>`; equivalent
  consecutive snapshots are valid. Readers select the newest suffix-bound
  valid snapshot, paginating past malformed tails. Point and candidate reads
  stay local to requested TIDs; names-only discovery is not valid state or
  process proof. Publication remains best-effort; only a validated terminal projection supplies
  task-authored lifecycle evidence, never proof of OS process exit.
Task-owned `weft.state.tasks.<tid>` snapshots ([OBS.6]) carry immutable `root_tid`, nullable `submitter_tid`, ordered finite ancestor chain `ancestry_tids`, validated `scheduling_class` (`"finite"`, `"background"`, or `"internal"`), and opaque `connection_owner` alongside existing runtime handle and scoped host-process evidence, replacing the per-task `admission` budget record. Manager-owned `weft.state.tasks.<manager_tid>` snapshots publish `scheduler_frontiers` (`stacks`, `residents`, `startup_feedback_tids` (a list of unfinished ordinary startup TIDs), and the owning ordinary `source_queue`/`reserved_queue`): active frontier stacks, retained resident task lineage/class/process evidence, exact unresolved reservation references, and the current bounded startup-feedback membership. Startup-feedback membership is diagnostic and is forward-written in the existing
Manager snapshots. It does not authorize admission, acknowledgement or replay.
Resident identity, process receipts and exact request custody retain their existing
durable publication and recovery requirements. Bootstrap uses those resident
records and fresh resource observation, not the diagnostic membership list. Detached tasks that survive Manager turnover are adopted and reconciled by bounded liveness probes. Task-owned publication remains best-effort; until handoff the reservation/checkpoint pair preserves recovery evidence under the custody guard above. After handoff, surviving tasks and pending tokens reconstruct their exact live finite ancestor path from `ancestry_tids` and `scheduling_class` even after a prior Manager checkpoint or intermediate ancestor task snapshot is deleted by `LivenessMonitor`. Publication remains edge-triggered, and `LivenessMonitor`-only deletion ownership ([LIVENESS.R3]) remains unchanged.

`weft.state.labels.<root_tid>.pending.<parent_tid>` queues (where `<parent_tid>` is `submitter_tid` or `root_tid`) are runtime-only, rebuildable consume-on-dispatch pending indexes (`schema: "weft.family.pending.v1"`, `request_id`, `root_tid`, `submitter_tid`, `ancestry_tids`). Each token references an accepted `weft.spawn.requests` row by `request_id` and uses a fresh physical SimpleBroker message ID. Primary enqueue commits acceptance independently of pending-index writes. When a Manager reserves the referenced primary row (or observes it absent from `weft.spawn.requests`), it immediately deletes the pending token so `.pending.<parent_tid>` empties and auto-disappears naturally as children are dispatched. Startup, reserved-return handling, and bounded periodic reconciliation repair missing or orphaned tokens, including explicit TIDs and returned rows below classification watermarks. When a resident parent exits with unconsumed tokens in `.pending.<exited_tid>`, the Manager attaches `.pending.<exited_tid>` directly to the nearest still-resident finite ancestor in `ancestry_tids` (`<root_tid>` if none remains) and continues consuming `.pending.<exited_tid>` directly without bulk-relocating tokens across queues. Ordered pending indexes use logarithmic worst-case in-memory maintenance; candidate selection is in-memory and proportional to live paths, without backlog-sized broker reads at completion. Runtime pending queues remain excluded from dump/load; durable request lineage in `weft.spawn.requests` rebuilds them.

On both backends, the existing pre-launch Manager checkpoint must persist the exact source/reserved/request reference and validated runtime identity before process creation. Post-launch checkpointing adds scoped process evidence and must report publication failure. All acknowledgement paths retain the original reserved request until a valid task-owned snapshot matches its TID, context, lineage, scheduling class, and scoped runtime identity, or existing positive launch-failure/whole-process settlement evidence resolves it. Startup feedback alone is insufficient. Exact acknowledgement and confirmed absence precede removal of the checkpoint obligation; subsequent Manager snapshots retain unresolved entries. Successors may resolve old reservations but never write the old Manager's task-state queue. A post-spawn crash without durable process evidence remains ambiguous; missing evidence never authorizes replay or a claim of guaranteed drain. If a retained pre-launch reference has no durable post-launch process receipt, a successor preserves its validated identity, unresolved reservation, and finite frontier where applicable. It blocks further ordinary dispatch until a matching positive task-owned snapshot or durable scoped receipt resolves the ambiguity. Absence of a PID or snapshot does not prove the request was unlaunched once its pre-launch reference exists. This crash window can require operator resolution and is outside the conditional drain guarantee.

`LivenessMonitor` remains the sole task-state deleter. Its newest-Manager-checkpoint retirement gains an exact-reservation custody guard: any referenced reservation still present, or an uncertain read, defers both stale and unknown-timeout retirement. TaskMonitor's reserved/dead-task deletion paths apply the same protection immediately before deletion and leave cleanup pending. Manager yield, shutdown, and reserved policies operate by exact `message_id` rather than bulk queue moves/reads, preserving launched or ambiguous requests so only positively unlaunched requests return through normal pre-launch policy; reserved rows retained in launch/handoff custody do not count as pending reactor work or trigger `PEEK` wakeup activity. These guards inspect checkpoint references, not descendant graphs. After acknowledgement, normal checkpoint retirement resumes with existing age, generation, and exact-delete rules. No new queue or reserved-message format is introduced.

- shared task evidence classification lives in
  `weft/core/task_evidence.py`; status, task inspection, known-TID terminal
  snapshots, result helpers, and realtime event iteration reuse that
  interpretation instead of each inventing their own priority rules. Realtime
  iteration reuses it after its first snapshot as well, for late typed terminal
  `ctrl_out` envelopes and eligible readable final one-shot outbox evidence.
  Realtime observation uses known immutable task configuration when available.
  When configuration is missing, it discovers and validates the task's metadata
  before treating ordinary outbox values as one-shot completion evidence. Late
  discovery may establish explicit routes and collection mode without consuming
  those queues; once known,
  immutable configuration is retained for that observation. Explicit terminal
  evidence remains sufficient without TaskSpec discovery. Persistent and
  interactive work-item output is not task completion. Fresh lifecycle
  observations remain separate from immutable configuration.
- shared terminal evidence priority is exact-TID task-authored local completion,
  validated terminal task-state projection, then supervisor terminal evidence.
  Source authority precedes source-local time. Explicit raw-history observation
  retains terminal task-log proof; ordinary acquisition never consults Monitor
  tables or implicit global history.
- a matched keyed PONG is authoritative live task-local evidence at its
  timestamp for explicit known-TID current-state probes; reconciliation compares
  task-log lifecycle events, typed terminal `ctrl_out` envelopes, and matched
  PONGs by timestamp when possible, while equal or unordered terminal evidence
  remains preferred over PONG
- when task-log terminal proof is missing, a typed terminal `ctrl_out` envelope
  may classify the task as terminal, including `wrapper_lost` for
  manager-authored wrapper-exit envelopes
- among terminal `ctrl_out` envelopes, a task-owned terminal envelope takes
  precedence over a manager-authored `wrapper_lost` envelope regardless of
  timestamp; the `wrapper_lost` failsafe classifies the task as terminal only when
  no task-owned terminal envelope is present, and within the same source class the
  latest envelope wins. This keeps the manager terminal-proof grace window a
  liveness mechanism rather than a correctness arbiter: a real task verdict that
  becomes durable after the grace window is not overwritten by the failsafe
- task completion uses the same lifecycle fact in `weft.log.tasks` and
  configured `ctrl_out`, under [MF-3]
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

`weft run`, `weft result`, and Python result retrieval use one shared result
capability. Shared result behavior has one implementation owner and is
reused by callers and collection modes; separate implementations of the same
behavior are defects. Mode-specific completion rules remain explicit within
that shared capability. The caller owns observation and retrieves output
directly from the task's configured result surface. A parent task does not relay results
solely to satisfy another caller's wait.

An operation with the accepted canonical task specification, assigned TID,
and effective resolved context derives its routes and collection mode from
those values. It must not rediscover the same specification through task
history or wait for child initialization, queue existence, or a task-owned
TaskSpec event before observing those routes. Explicit `io` routes take
precedence over canonical TID defaults. Submission-time shaping is complete
before this configuration is retained.

Ordinary status, result, control, and client observation use task-owned queues,
never Monitor tables or external log files. Bulk inventory reads newest-valid
task snapshots. Known configuration remains bound to the observation; bare-TID
acquisition reads the snapshot's observation object or a validated exact-TID,
task-authored standard completion containing complete immutable observation
configuration. Unrelated redacted fields do not disqualify it; missing,
invalid, or redacted required collection/routing fields do. No required
field is defaulted during discovery. A standard completion queue is only a
read-only discovery candidate, never permission to assume default transport.
Waiting observers register task-state and standard control interests before
discovery reads, then register resolved routes before consuming output. Pure
point queries need no activity subscription. Missing configuration is
unavailable evidence, not task failure or proof of absence. Explicit raw
lifecycle/history operations may read retained `weft.log.tasks`; they do not
provide a fallback for ordinary readers. Monitor collations are private export
and cleanup state, never public lifecycle or routing authority.

`projection` has exact keys: required `version` (exact int 1), `status` (member
of `TASK_LIFECYCLE_STATUS_VALUES`), nonblank `event`, nullable nonnegative exact-int
`started_at`, and redacted JSON-object `metadata`. Optional `completed_at`
(nonnegative exact int), `return_code` (exact int), and `error` (nonblank string)
are present only when non-null and terminal. Completion cannot precede start;
the outer snapshot `terminal` flag must agree with projected status.
Both lifecycle timestamps are Unix nanoseconds, as in `TaskSpec.state`. Reject booleans as integers.
Reject extra keys and unsupported versions. Observation time is the broker row
ID; duration is computed by the reader. No results, arguments, environment,
full TaskSpec, derived runtime description, or reconciliation are copied.

`observation` has exact keys: `version` (exact int 1), `mode`
(`one_shot|persistent|pipeline`), `stream_output` (bool), and `routes`. Routes
have exactly `inbox`, `outbox`, `ctrl_in`, `ctrl_out` (nonempty resolved queue
names) and `pipeline_status` (nonempty name for pipeline; null otherwise).
Mode is derived once from the resolved TaskSpec: pipeline role first, otherwise
persistent OR interactive, otherwise one-shot. Pipeline status honors the
existing runtime metadata override. These queue identifiers are operational
routing metadata, not secret payloads. Projection metadata uses existing
TaskSpec redaction; do not copy arbitrary event extras.

The newest liveness-valid row is selected first. Projection and observation
validity are independent. Missing or invalid nested objects never authorize an
older row, history fallback, or guessed route. Invalid projection displays
`unknown`, safe identity/runtime fields, empty metadata, and bounded diagnostic
`task_state_projection_invalid`; absent projection uses
`task_state_projection_unavailable`. Diagnostics contain no field contents.

For conflicting lifecycle outcomes, a validated exact-TID task-authored local
completion wins over the terminal projection, and either wins over supervisor
terminal evidence. Do not let a later snapshot timestamp invert this order.
Existing outbox collection/settlement stays mode-aware; ordinary output and
persistent batch completion do not by themselves become task termination.
Nonterminal projections never overrule task-authored completion.

With resolved configuration and no task-authored local completion, a terminal
projection may settle the task through the existing output/grace rules, including
when only supervisor completion is present. It cannot fabricate stream-end
frames, persistent work-item IDs, batch boundaries, acknowledgements, or missing
output. A terminal projection without usable configuration still displays its
outcome in status, but result acquisition follows the metadata-unavailable rule
below: it cannot know which queues or collection mode to use. Include the
observed terminal status in reconciliation, without claiming a collected result.
Unresolved-route controls still fail before writing; when routes are resolved,
terminal evidence follows the existing already-terminal control behavior.
These rules do not change OS liveness: terminal publication precedes process
exit and cannot authorize a reaper to assume the process is gone.

Discovery from a standard completion requires `source="task"`, exact matching
outer and nested TaskSpec TIDs, JSON objects for `spec`, `io`, and `metadata`,
and explicit actual booleans for persistent, interactive, and stream-output.
Only required immutable fields are converted; no executable TaskSpec is rebuilt.
Missing role means non-pipeline only inside valid metadata. Pipeline discovery
requires the resolved status route, including its runtime override. Redacted or
malformed required containers/fields, including `[REDACTED]` sentinel strings,
make configuration unavailable. Accepted full TaskSpecs retain normal default
resolution before conversion. Once resolved, configuration remains bound even
if state later disappears.

Without timeout, unresolved result acquisition returns unknown with
`observation_metadata_unavailable` reconciliation (CLI exit 1). With finite
timeout, discovery waits until the original caller deadline and raises the
existing observation-timeout error (CLI exit 124), without publishing synthetic
task timeout. Unresolved task
control routes produce a typed error before any write (CLI exit 1).

The observation owner registers interest in its exact result and completion
queues before checking current durable contents, including queues that have
not received their first write. It then uses the existing watcher/reactor
waiting foundation for backend activity, interruption, and actual timeout or
completion-grace deadlines. There is no separate Weft sleep/probe cadence or
arbitrary maximum wait slice for result discovery. Backend-native waiting
and SimpleBroker's own polling remain broker concerns. If Weft's observation
adapter loses its native waiter, it must transition to another live wake
source or fail explicitly. It must not silently wait until a caller deadline
or indefinitely when no deadline exists.
Notifications are hints; durable messages determine the result.

Ordinary result waits with known immutable task configuration observe the configured
outbox and `ctrl_out`, plus the existing task-local runtime wake/evidence surfaces. They
do not subscribe to or scan `weft.log.tasks` for live completion. Task-local completion
records carry the same lifecycle fact as global history; existing terminal precedence,
persistent work boundaries, output grace, and claim settlement apply.

Full-TID snapshot absence alone does not establish absence of an accepted request. When task-state metadata and positive task-local evidence are absent, known-TID point status and non-consuming terminal snapshots read the exact pending spawn-request ID in ordinary/internal source queues and canonical manager ordinary/internal reserved queues in the resolved context. Source custody reports `queued`; reserved custody reports `reserved`. These are nonterminal observations of broker custody, not TaskSpec lifecycle states or proof that execution has not started. Task-local evidence retains precedence; invalid projection inside a valid row and stale valid task-state metadata retain their existing interpretation. Malformed task-state rows retain [OBS.6] semantics and do not create a new custody suppression rule. Claimed residue and manager liveness alone do not establish submission custody.

Custody reads do not mutate registry or queue state, start services, reconstruct task configuration from spawn payloads, or acquire global task history or Monitor data. Discovery includes retained stopped/superseded canonical manager owners. Source queues are checked before reserved queues. One observation checks task-local evidence, then source queues, then (on source miss) the registry and reserved queues, then rechecks task-local evidence and current projection after the custody lookup whether custody was found or not. Fresh task evidence/current projection wins over custody. There is no second registry/custody pass. Reads do not promise an atomic cross-queue snapshot. Without positive evidence the observation remains unknown, which does not authorize resubmission or imply terminal failure. Database failures remain typed errors.

Positive-timeout terminal observation continues to wait for terminal evidence or the original caller deadline using existing task-local subscriptions. Intermediate custody transitions do not require a wakeup. At the original deadline an otherwise unknown snapshot receives a fresh custody observation. The timeout bounds terminal waiting, not total call duration; the final custody observation and task-evidence refresh may require additional backend I/O, with no fixed overrun bound. Intermediate wait iterations do not acquire custody. Pure point reads create no activity subscription. Control-decision loops, interactive STOP, bulk inventory and status/event watch retain their existing acquisition and output semantics; these paths do not gain custody lookups. One-shot public snapshots attached to ping/stop/kill results inherit the point-snapshot contract without changing command acknowledgement, success or failure decisions. Result acquisition, controls, settlement, liveness and cleanup keep their existing contracts.

The explicit runtime-liveness command reads the newest valid task-state row
through `read_task_state_snapshot`, probes its validated runtime identity, then
re-reads once using `read_task_state_snapshot(..., previous=selected)`. Use two
`broker=None` reader calls so their existing scopes release connection leases
before the potentially slow probe, preserving any caller-owned session. Do not
wrap both reads and the callback in one broker loan. Use the existing
`@typed_command_errors` boundary for the command owner. Compare selected rows by message ID, never tuple/object identity: changed means
the re-read returns `None` or a different message ID. A changed or removed row
invalidates the result: return `unknown` / `snapshot_changed`, retaining the
original input row ID and identity as provenance. This rule takes precedence
over the probe outcome, including timeout or death. Do not re-probe a replacement
or fall back to an older runtime identity; malformed tails retain [OBS.6]
reader semantics. With no initial valid row, return `unknown` /
`runtime_handle_unavailable` without a probe. A final read cannot provide
linearizability: a subsequent change is outside this point observation.
Broker/read failures remain typed command errors rather than unknown values.

The reason-code vocabulary is exactly `runtime_live`, `runtime_dead`,
`runtime_unresolved`, `probe_unavailable`, `probe_failed`,
`runtime_handle_unavailable`, `authority_unsupported`, `probe_timeout`, and
`snapshot_changed`. The operation does not read spawn custody, service registry,
global lifecycle history or Monitor state; it writes no queue, sends no signal
or control message, and cannot authorize resubmission, cleanup or result
acknowledgement. It never rewrites task lifecycle status. Public custody
acquisition and handoff ordering remain shared as specified for slices 1–4;
this separate runtime probe does not enter that fallback or broaden its callers.

Bulk status/list/stats default to current nonterminal projections. Explicit
`--all` / `include_terminal=True` adds retained terminal projections. Explicit
`--log` / `include_log_history=True` unions retained raw history with current
projections: terminal task-log proof wins, otherwise valid current projection
wins. It implies historical inclusion; `--log --watch` is a usage error.
Bulk acquisition performs no per-task control/output reads, active PING, implicit
global-history replay, or Monitor reads. It reports the last successful task
publication and can lag a durable completion or supervisor `wrapper_lost` after
abrupt child death or failed final publication. Known-TID reads acquiring local
completion still surface terminal evidence immediately. Event following remains
an explicit retained/live raw stream and cannot promise lossless history.

Interactive shutdown uses its accepted configuration and resolved local queues,
with the existing STOP/ACK, cancellation, deadline, and KILL behavior; it performs
no direct global-history or Monitor acquisition. Internal service inventory uses
already acquired registry, projected child identity, internal spawn/reserved
evidence without independent log replay or new Manager PING. Missing startup
evidence is unknown, not death. Explicit `--log` may enrich from Manager events.

Result/control drains preserve every valid completion record in a configured `ctrl_out`,
including records for another TID sharing that queue. They fold only the requested TID
and never consume those records as stream noise. An identical serialized persistent
boundary observed again after a publication retry is the same boundary, even when its
broker message ID differs.

Existing evidence classification and precedence apply to acquired evidence.
Persistent result waits retain `work_item_completed` and `work_completed`
boundaries; a work-item result is not task termination. Streaming follows
unread output using the existing completion semantics. Output already emitted
as a completed one-shot result remains evidence against a later false timeout.
Result waits tolerate the existing completion/output visibility gap, honor
the completion grace, and perform the last non-blocking output drain before
returning completed. Claimed-result recovery remains scoped to actual claimed
rows rather than repeated on empty turns.

_Implementation mapping_: `weft/core/spawn_requests.py::_submit_spawn_request_with_payload`
and `weft/commands/submission.py::_bind_accepted_taskspec` own accepted transport
capture/binding. `weft/client/_task.py::_task_with_configuration` and
`weft/commands/run.py::_LiveRunSession` retain it;
`weft/commands/_result_wait.py::_accepted_result_materialization` derives immutable
routes without treating submission state as lifecycle evidence.
`weft/commands/_result_wait.py::result_observation` owns shared queue
acquisition, registration-before-read and cancellation wake registration.
`weft/core/queue_wait.py::QueueChangeMonitor` owns native/fallback thread
custody, latched operational failure, wake and close. Shared typed error
translation remains in `weft/commands/_boundary.py`.
`weft/core/task_evidence.py::read_task_history` owns explicit raw-history reads,
exact identity validation, ordering, and inspected progress. Explicit history
and raw-event stream adapters delegate to it.
`weft/core/task_evidence.py::known_tid_evidence` selects task-owned local
completion, current projection, supervisor, and runtime evidence; it reads no
Monitor tables or global history. Explicit history projection belongs to
`weft/commands/system.py::_merge_raw_task_history`.
`weft/core/task_evidence.py::parse_task_projection` and
`weft/core/task_evidence.py::parse_task_observation` own shared nested validation;
`weft/core/task_evidence.py::discover_task_observation` acquires configuration
from task-owned queues.
`weft/commands/_result_wait.py::_acquire_result_observation` advances task-state
and standard-completion discovery and retains configuration;
`weft/commands/_result_wait.py::_classify_result_observation` supplies acquired
local queues and the current snapshot to shared evidence selection.
`weft/_constants.py::_parse_task_observation_retention_seconds` validates the
independent observation minimum. Firing coverage includes
`tests/core/test_task_observation.py`,
`tests/commands/test_task_owned_observation.py`,
`tests/system/test_constants.py::test_task_observation_retention_has_independent_validated_floor`,
`tests/tasks/test_liveness_monitor.py::test_terminal_projection_retains_snapshot_without_repeated_probes`,
and `tests/tasks/test_task_observability.py::test_task_state_projection_tracks_owner_edges_and_redacts_metadata`.
`weft/commands/_result_wait.py::_CollectedResult.select_claims` and
`weft/commands/_result_wait.py::_CollectedResult.settle`
own shared row eligibility, completion and deadline rules for consuming and
realtime observations. `weft/commands/_result_wait.py::_drain_result_rows`
claims selected IDs individually
and decodes and records successful claims through the shared assembly path.
`weft/commands/_result_wait.py::_iter_consuming_result` drives consuming
collection; `weft/commands/result.py::_iter_task_result` is its typed adapter.
`weft/commands/events.py::iter_task_realtime_events` projects a read-only output window
through the same settlement owner and emits the current task-owned projection
as its initial snapshot. Raw lifecycle following is an explicit separate stream. Shared cancellation wake
registration lives in `weft/helpers/__init__.py::_register_observation_wakeup`.
The typed result adapter is used by result retrieval, streaming and
`weft/commands/events.py::follow_task_events`.
`weft/commands/tasks.py::_task_status_record` and
`weft/commands/system.py::collect_known_tid_record` retain fresh snapshot fields
while borrowing accepted routing. Coverage lives in
`tests/core/test_task_history_reader.py`,
`tests/commands/test_task_owned_observation.py`,
`tests/commands/test_point_snapshot_loans.py`, and
`tests/commands/test_retained_result_history.py`,
`tests/commands/test_active_interactive_lifecycle.py`,
`tests/commands/test_consuming_result_collection.py`,
`tests/commands/test_event_observation_connections.py`,
`tests/commands/test_observer_command_failures.py`,
`tests/commands/test_result_activity_waits.py`,
`tests/commands/test_result_settlement_policy.py`, and
`tests/commands/test_terminal_snapshot_loans.py`.
`weft/commands/result.py::_task_result_event` owns the common settled-result
projection. `weft/commands/events.py::_terminal_evidence_identity` compares
semantic proof without treating a fresh diagnostic timestamp as new evidence.
`weft/commands/queue.py::watch_queue` shares activity-driven command observation;
`weft/commands/queue.py` owns its queue adapter.

Related plan: [Direct result observation spec changes](../plans/2026-10-09-direct-result-observation-spec-plan.md).
Implementation repair plan: [Direct result observation](../plans/2026-10-09-direct-result-observation-implementation-plan.md).
Related plan: [Result observation simplification](../plans/2026-10-10-result-observation-simplification-plan.md).
Earlier implementation work: [Burst result observation](../plans/2026-10-09-burst-result-observation-plan.md).

_Implementation mapping_: `weft/core/family_scheduling.py`; `weft/core/task_state.py`; `tests/core/test_family_scheduling.py`; `tests/system/test_family_recovery.py`; `weft/core/tasks/base.py` `_report_state_change`;
`weft/core/tasks/base.py::BaseTask._build_tid_state_payload` preserves immutable
lineage, validated scheduling class, connection owner and scoped process identities. Snapshots include a
host_processes list containing wrapper PID/create-time identity, active worker
identities, and retained managed process identities; runtime_handle keeps
its existing runner meaning. `weft/core/runtime_identity.py`
validates launch records and transports them through `weft/core/launcher.py`
and `weft/core/runners/host.py`. `weft/core/manager.py` records pending and
committed launch evidence and unresolved reservation references. Its bootstrap projection uses only child-specific
launch fields from task_spawned: a reporting Manager's runtime_handle is never
child process identity. `tests/system/test_measured_admission_pg.py`
checks equality between the actual task snapshot and Manager launch event;
`tests/core/test_admission_recovery.py` exercises retained pending launches,
older-manager reservations, shared liveness judgment for crash recovery,
bounded evidence refresh, and failure containment;
`tests/core/test_runtime_identity.py` verifies the wrapper/worker handoff;
`weft/core/monitor/task_monitor.py` foreground monitor scan primitive and
supervised monitor task;
`weft/core/monitor/runtime.py` task-monitor runtime config, candidate, and
processor contract;
`weft/core/task_evidence.py` shared lifecycle/result evidence classification,
runtime description, and structured stdout/stderr extraction;
`weft/core/outbox.py` shared outbox decoding;
`weft/commands/system.py` queue/runtime evidence acquisition and ordered log
replay; `weft/commands/_task_snapshot_reducer.py` pure event folding, evidence
precedence, snapshot construction, filtering, and ordering;
`weft/commands/_result_wait.py` shared acquisition, collection and settlement;
`weft/commands/result.py` typed result and CLI projections;
`weft/commands/events.py` `iter_task_realtime_events` non-consuming realtime
observation, discovering late metadata before shared terminal classification;
`weft/commands/events.py` `_open_realtime_routes` owns observation route leases
and monitor replacement when late metadata identifies custom queues;
`weft/core/task_evidence.py` shared lifecycle/result evidence;
`weft/commands/tasks.py` task control and runner-diagnostic presentation over
`weft/core/runner_diagnostics.py`;
`weft/commands/task_monitor.py` archive summaries and checkpoints;
`weft/commands/prune.py` explicit prune dispatch, reporting, and rendering;
`weft/core/pruning/runtime.py` runtime-only candidate selection and
exact-message deletion;
`weft/commands/_result_wait.py`;
`weft/core/serve_log.py` manager-serve process-log record construction;
`weft/commands/_task_history.py`;
`weft/commands/_streaming.py`;
`tests/tasks/test_task_monitor.py`.

Plan backlink:
`2026-07-29-task-snapshot-reducer-plan` (retired — source 396c3b85; see `docs/plans/README.md`).

Implementation plan backlink:
`2026-05-07-runtime-state-pruning-plan` (retired — source 396c3b85; see `docs/plans/README.md`).

#### Supervisor terminal observations in operational collations

TaskMonitor may reconcile a retained operational collation without terminal task-log proof from exact typed terminal evidence in that task's configured standard ctrl_out, using the same evidence selection and task-owned-before-supervisor precedence as the public task read model. Family disposition or a recorded cleanup pass does not end reconciliation: include disposed families while the collation is retained. Project the selected envelope's outcome and retain its source, including task-owned envelopes available only in ctrl_out. Compare each completed inspection with the persisted selected control observation through that same selector: removal of its source row does not let a later supervisor fallback demote a previously observed task-owned verdict. Task-log terminal proof still takes precedence. Preserve the envelope's reported time, exact queue/message identity, and the Monitor's first observation time separately; a missing reported time remains unknown. Missing runtime state or process alone supplies no terminal outcome. A later task-owned terminal verdict supersedes the supervisor projection regardless of their timestamp order. Ordinary nonterminal lifecycle updates must not demote an already observed control-terminal outcome. Evidence first published after the final verified empty ctrl_out inspection preceding physical family retirement is outside this retained-evidence guarantee. Control reconciliation does not recreate retired collations; ordinary task-log ingestion and retention remain unchanged. Do not extend retention indefinitely.

This operational projection changes operational status and provenance in diagnostics only. It leaves terminal_seen, terminal_status, terminal_event, terminal_message_id and reserved_probe_needed unchanged, preserving the absence of task-owned terminal task-log evidence. It does not manufacture a lifecycle event, advance global-log bounds/high-water marks with a control-queue ID, consume a result, or grant additional inbox/reserved deletion authority under [OBS.13.7]. A changed selected terminal identity/outcome clears summary_emitted_at_ns in the same store transaction; replay of unchanged evidence does not. Task-log terminal evidence superseding a control projection performs the same invalidation. A corrected summary may be emitted for an already-disposed family without undoing or broadening its disposition. Structured lifetime reporting retains exact provenance and gives different selected evidence different stable report identities. Existing high-water, report handoff/verification, live-owner, launch-custody, and retention gates still apply before control evidence is removed or the family retires.

Reconciliation uses the existing Monitor maintenance cadence, family batch bound, task-log scan row budget, and cleanup slice deadline. Candidate rotation and unfinished control scans remain bounded process-local state. Each scan inspects a fixed queue horizon in resumable pages and reduces candidates with the shared selector, keeping only its best envelope. Exhausting a page or time budget is incomplete inspection, not proof that task-owned evidence is absent. Only a fully inspected horizon may publish its selected control outcome. Probe failure preserves the collation/evidence and remains retryable without starving other families. Reconciliation runs independently of global task-log catchup; corrected summary emission retains its existing high-water gate. No additional watcher or polling service is introduced.

For a retained family without terminal task-log proof, control-evidence deletion is limited to the fully inspected horizon whose selected evidence has completed the existing report handoff/verification. Later ordinary publications survive that deletion and remain eligible for reconciliation while the collation exists. Full control-cleanup completion and physical collation retirement use a final verified empty inspection of ctrl_out, not merely a prior cleanup-pass marker: a successful public queue-stat read must report total == 0, including pending and claimed rows. This inspection is the retained-evidence guarantee's boundary; queue inspection and collation retirement are separate operations. A publication after that inspection may outlive a retired collation without another collation correction report. Preserve that queue row during this retirement; subsequent ordinary dead-TID cleanup keeps its existing retention/reporting policy. Control reconciliation does not recreate retired collations; ordinary task-log ingestion is unchanged. A cleanup-pass marker may record ctrl_in-only progress while ctrl_out is retention-protected; it neither proves full control cleanup nor blocks the later reserved and dead-TID cleanup slices. A failed inspection defers retirement and remains retryable. Concurrent exact-ID operator restoration or moves behind an inspection cursor require paused Monitor cleanup and discarded continuations before resumption.

_Implementation mapping_: `weft/core/task_evidence.py::scan_terminal_ctrl_out_evidence` owns bounded shared envelope inspection; `weft/core/monitor/collation.py::ControlTerminalObservation` owns detached provenance and precedence; `weft/core/monitor/store.py::MonitorStore.project_control_terminal_observation` owns transactional projection, and `MonitorStore.record_task_log_updates` uses the same row-lock protocol for task-log merges. `weft/core/monitor/task_monitor.py::MaintenanceWorker` owns reconciliation, correction handoff, covered-horizon cleanup, and final empty inspection; `weft/core/monitor/store.py::MonitorStore.retire_completed_collation_families` rechecks SQL eligibility under row locking after that inspection. `weft/core/monitor/lifetime_report.py` and `weft/core/monitor/external_log.py` own stable selected-evidence identity and exact-ID external codecs. Contract tests live in `tests/commands/test_task_evidence.py`, `tests/core/test_monitor_collation.py`, `tests/core/test_monitor_store.py`, `tests/tasks/test_task_monitor.py`, `tests/tasks/test_maintenance_worker.py`, `tests/core/monitor/test_lifetime_report.py`, and `tests/core/test_monitor_external_log.py`.

### 6. Manager Spawn Flow [MF-6]

Spawn reconciliation observes the exact submitted `weft.state.tasks.<tid>`
queue through a TID-specific subscription, not a static shared-state route.
Open the lazy handle before the queue exists so its first publication wakes
the waiter. Preserve dynamic manager-reserved subscriptions, route rebuilds,
valid snapshot/log/queued/reserved/unknown precedence, and resource cleanup.
A names-only entry is not spawn evidence.

Every ordinary submission, including same-context runtime submissions, enters `weft.spawn.requests`. System-owned root, submitter, and finite-ancestor (`ancestry_tids`) identity travel in the spawn envelope and runtime transport; no future child count is required. Caller metadata does not grant internal routing, lifecycle class, or ancestry authority. The scheduler selects an exact accepted request ID; admission precedes its atomic move to the Manager's standard reserved queue. Existing validation, launch, drain fences, and reserved recovery remain authoritative, with acknowledgement subject to the launch-handoff rule below. The removed `weft.spawn.children` source and `children_reserved` role have no steady-state producer, consumer, registry field, or admission policy. The existing trusted internal source is retained.

Unclassifiable malformed requests reach the existing validator through bounded exact-ID reservation, terminal-error, and acknowledgement handling. Validation-only rejection consumes no live frontier and may proceed under resource pressure; it cannot launch valid work around the measured gate. Uncertain observations remain pending for retry.

Admission capacity and SQLite live-TID observation follow [MA-1.8]. A blocked source that is already known active is
suppressed as ordinary wait activity so retained backlog cannot create a
zero-timeout loop. Admission propagates task-state snapshot read errors to its
listed fail-closed exception boundary. Other callers retain their own recovery
policy; the shared reader never converts backend errors into empty state.

Denial or observation failure remains suppressed until its universal
one-second retry deadline; child reap or launch-worker progress may wake it
earlier. A failed child launch cannot leave a restored or retained source
suppressed past that deadline. The public PostgreSQL usage observation catches
only `DatabaseError` and `ValueError`; its separate runtime/recovery evidence
refresh catches `BrokerError`, `OSError`, `RuntimeError`, `TypeError`, and
`ValueError` and fails closed. SQLite catches only `BrokerError`, `OSError`, and `RuntimeError`;
shutdown/control `BaseException` subclasses propagate. While dispatch is blocked, the Manager continues control
handling, cleanup, child reaping, leadership convergence, service
reconciliation, and shutdown. Admission remains before reservation. Acknowledgement follows the launch-custody handoff contract in [MF-5]. Native
and fallback waits suppress only the blocked spawn source; custody-retained reserved rows are suppressed from pending work and PEEK activity; bounded recovery remains scheduled. A Manager that has proved another live
lower-TID primary reserves no new work from any shared spawn inbox. Shared
unreserved internal backlog does not veto that yield; already-reserved rows,
active launches, locally tracked children, and actionable control retain their
existing ownership behavior.

The spawn-request message ID is the submitted task TID. Dump/load paths that
include spawn queues must preserve broker message IDs on import; in
SimpleBroker `simplebroker-dump` v1 files, message records carry that value as
`id`. If an active backend cannot import rows at exact IDs, load must fail
before writes begin rather than rewriting runnable spawn requests under new
IDs.

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
While retained, manager `task_spawned` rows are durable launch hints, not child
lifecycle truth. The TaskMonitor may compact high-volume historical manager
launch hints by deleting exact older `task_spawned` rows after preserving the
newest launch hints per manager TID and leaving child task-log rows and TID
mappings under their normal owners.

Current submission-reconciliation rules:

- if the submitted TID is already visible through task logs or TID mappings,
  the submission is treated as spawned
- if a manager has already emitted `task_spawn_rejected` for that child TID,
  the submission is treated as rejected
- if the exact message is still in `weft.spawn.requests` or `weft.spawn.internal`, it remains accepted
  under its original TID; post-acceptance recovery does not delete or requeue it
- if the exact message has moved into a manager reserved queue, it remains
  accepted and ends further manager-start attempts for that submission;
  execution observation and reserved recovery remain with their existing owners
- if none of those surfaces locate the accepted request, the caller reports an
  explicit unknown location keyed by TID without treating that as queued or
  authorizing another manager
- an authoritative `task_spawn_rejected` event raises the existing typed
  submission error with the accepted TID and rejection reason
- reconciliation read failure retains acceptance and reports a diagnostic

Current manager-dispatch rules:

- public spawn dispatch is work-stealing: a manager that atomically reserves a
  message from `weft.spawn.requests` owns that exact
  launch attempt
- manager-owned internal spawn dispatch uses the same launch path and remains
  strict-priority over ordinary work
- registry leadership is advisory for status, startup selection, and voluntary
  duplicate-manager convergence; stale or ambiguous registry proof must not
  block a manager that already reserved ordinary or internal spawn work
- successful launch retains the exact reserved message until validated task-snapshot handoff or positive settlement. Exact deletion and confirmed absence precede checkpoint obligation removal under [MF-5]. Positively unlaunched requests retain pre-launch reserved policy; ambiguous or launched requests are preserved.
- child-launch workers return only local Python results. They do not touch
  SimpleBroker queues, mutate TaskSpec state, report task-log events, or send
  control responses. Those effects belong to the manager reactor.
- manager-to-manager liveness PINGs are reactor state, not blocking helper
  calls. The probing manager writes one keyed PING with `reply_to` set to its
  configured `ctrl_in`, stores the pending request, and receives the matching
  PONG through its ordinary control drain. Pending probes are unknown evidence;
  they must not immediately prune or supersede the target manager. Lack of a
  PONG is evaluated by the ordinary leadership cadence; the probe adds no
  private deadline.
- service-owner liveness PINGs use the same non-blocking pattern. The manager
  writes one keyed PING with the same Manager `reply_to`, stores the pending
  probe, and receives the matching PONG through its ordinary control drain.
  Pending service probes are uncertain evidence and select the existing active
  service cadence. Lack of a PONG is evaluated only by that cadence.
- constructor bootstrap anchors the applicable leadership or service cadence
  after it creates a probe, so the first reactor turn cannot classify that new
  probe as no-PONG. A stored PONG makes its owning reducer actionable without
  moving that cadence. Within the current reconciliation scope, pending service
  probes keep their service keys in evidence collection. When autostart is
  included, a stored PONG for a known autostart source bypasses the
  manifest-scan throttle so the reply is reduced against current source
  evidence. A successful complete owning-policy scan may retire a probe whose
  source is absent; failed, throttled, and partial scans retain it.
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
in-memory startup state. Submission-scoped recovery never consumes, moves, or
deletes a reserved request.

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

_Implementation mapping_: `tests/core/test_family_source_reconciliation.py`; `tests/commands/test_family_migration.py`; `weft/core/manager.py` — `Manager._handle_work_message`,
`Manager._build_child_spec`, `Manager._launch_child_task`,
`Manager._run_child_launch_service_worker`, `Manager._run_child_launch_worker`,
`Manager._handle_service_worker_event`, `Manager._handle_child_launch_result`,
`Manager._commit_child_launch_success`, `Manager._control_allows_child_launch`,
`Manager._tick_managed_service`,
`Manager._enqueue_managed_service_request`,
`weft/core/manager.py::Manager._process_reactor_turn`;
`weft/core/manager_services.py`;
`weft/core/spawn_requests.py`;
`weft/cli/run.py`; `weft/commands/submission.py::ensure_manager_after_submission`;
`weft/commands/_spawn_submission.py` —
`_inspect_task_log_for_tid`, `_reconcile_submitted_spawn_once`,
`weft/commands/_spawn_submission.py` static reconciliation queue inventory,
`weft/commands/_spawn_submission.py::_find_reserved_spawn_request_queue`,
and `weft/commands/_spawn_submission.py::_reserved_spawn_request_queue_names` recognize every source/reserved pair;
`weft/core/runtime_identity.py` supplies the runtime submitter and launch record;
`weft/core/service_convergence.py::project_manager_service_record` preserves
the source/reserved routes in projected Manager ownership records;
`tests/core/test_manager.py`; `tests/core/test_child_spawn_validation.py`;
`tests/system/test_child_admission.py`; `tests/system/test_measured_admission_pg.py`.

Implementation plan backlinks:
[`2026-08-25-manager-admission-control-plan.md`](../plans/2026-08-25-manager-admission-control-plan.md);
`2026-04-21-run-boundary-dispatch-fence-control-contract-plan` (retired — source 396c3b85; see `docs/plans/README.md`);
`2026-05-11-internal-service-observability-plan` (retired — source 396c3b85; see `docs/plans/README.md`).

### 7. Manager Bootstrap Flow [MF-7]

`weft run` performs post-acceptance manager availability recovery through the
shared bootstrap helper. Direct `weft manager start` still requires readiness.
`weft manager serve` runs the same canonical manager runtime in the foreground
for supervision.

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
  manager just because it cannot prove the incumbent live. Automatic recovery
  follows [MA-1]: two proof rounds at most, the first round counts toward the
  namespace-ambiguity grace, and final fresh registry and backlog evidence
  controls any helper launch. Submission recovery also requires the exact
  accepted TID to remain queued immediately before launch.
- detached-launcher acknowledgement and startup-stderr cleanup are best-effort
  post-proof steps; they may warn, but they do not downgrade a successfully
  proven manager start into submission failure
- early detached-bootstrap failure surfaces child exit status and startup
  stderr context
- foreground serve forces `idle_timeout=0.0` for that invocation
- the shared lifecycle helper owns manager discovery, bootstrap, and stop
  observation

_Implementation mapping_: `weft/core/manager_runtime.py` —
`observe_manager_availability`, `decide_manager_recovery`, `ensure_manager`;
`weft/core/control_probe.py::send_keyed_ping_probe`;
`weft/commands/submission.py::ensure_manager_after_submission`,
`weft/cli/run.py`, `weft/commands/manager.py`,
`weft/commands/serve.py`, `weft/manager_detached_launcher.py`,
`weft/manager_process.py`.

Plan backlink:
`2026-09-17-manager-discovery-and-durable-submission-plan` (retired — source 396c3b85; see `docs/plans/README.md`);
`2026-04-24-runtime-handle-authority-migration-plan` (retired — source 396c3b85; see `docs/plans/README.md`);
`2026-05-09-runtime-liveness-probe-registry-plan` (retired — source 396c3b85; see `docs/plans/README.md`).

### 8. Failure Recovery Flow

Current recovery is explicit:

- inspect reserved queues
- move work intentionally with queue commands
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

_Implementation mapping_: `weft/helpers/__init__.py` `redact_taskspec_dump`;
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
`weft/commands/result.py` result rendering. Spill files and per-tid directories
use ordinary filesystem creation defaults under OS and operator policy ([CTX.5]);
existing permissions are not changed.

### Cleanup Boundary

`WEFT_TASK_OBSERVATION_RETENTION_SECONDS` defaults to
172800 (48 hours), finite and at least `LIVENESS_MAPPING_MIN_AGE_SECONDS`
(currently 2400). It is a minimum for the newest liveness-valid snapshot **with
a validated terminal projection**, and for standard task-owned terminal
`ctrl_out`/outbox evidence. It is separate from raw-log retention. Nonterminal
snapshots, legacy snapshots without a valid terminal projection, and
superseded/malformed history keep their existing age/retirement rules. Invalid
nested data does not invalidate liveness evidence or permit earlier deletion
than those existing rules. A live or otherwise protected newest row never
becomes deletable merely because it is old.

Liveness evidence decides whether cleanup is allowed; retention evidence decides
when. Preserve the existing probe, unknown-timeout, generation, and custody
rules. Only after those rules permit retirement may a validated terminal row
wait solely for its observation floor. Reuse the existing reactor due scheduling
to defer that row to its retention deadline instead of probing every five
seconds for 48 hours. New row/generation evidence invalidates the deferral. At
the deadline, obtain current liveness authorization and recheck generation,
launch custody, and older-row absence before exact deletion. Restart may
reconstruct this transient deferral from queues; it creates no durable index.
Terminal lifecycle publication alone is never proof of OS process exit.

Newest terminal-snapshot age is measured from its broker row ID by
LivenessMonitor. Monitor uses snapshot presence as the queue-retention hold;
it does not also compute a projection-based age. While the row exists the hold
protects standard completion/output, including when observation metadata is
invalid; after LivenessMonitor retires it the snapshot's retention period has
already elapsed. This avoids duplicating one retention decision in two owners.

With no task-state hold, standard completion/output retention uses the latest
broker row ID among a validated exact-TID task-authored local terminal completion
and the existing Monitor terminal-evidence anchor when available. Retain the
existing Monitor anchor semantics; newly acquired local anchors must be actual
task termination, not PONG, supervisor failure, ordinary output, or persistent
work-item completion. Read the standard control source without consuming it;
this requires no route reconstruction. Only otherwise actionable families
without a snapshot hold need that read, even when there is no Monitor record.

Absence of a Monitor record is not absence of task-owned evidence. Use the
name-derived TID-age fallback only after the relevant queue reads succeed and
no qualifying terminal evidence exists; read errors defer cleanup. Feed the
same evidence/clock rule through candidate selection, deferred-work selection,
and the final destructive boundary. Immediately before deletion, revalidate
custody/liveness and reread task-owned terminal evidence so a candidate selected
before a fresh terminal publication cannot delete that publication. Preserve
existing exact-ID/covered-horizon safeguards. For standard ctrl_out/outbox only,
a current liveness-valid task-state row is an additional deletion hold, even
when terminal: wait for LivenessMonitor to retire it under its existing process
and custody rules. Recheck this row-presence hold at the destructive boundary;
read failure retains the queues. Do not use TaskMonitor's current
`_nonterminal_mapping_row_tids` filter for this hold: it deliberately omits
terminal rows. Do not widen `_active_runtime_tids` globally, since immediate
control/inbox and reserved policies are unchanged. TaskMonitor adds no runtime
probes and writes/deletes no task-state rows. Snapshot absence still requires
existing launch-custody and retention checks; it is not a new proof of death.
This uses the existing sole-deleter handoff instead of asking TaskMonitor to
reprove OS liveness. Do not add a parallel cleanup framework, Monitor lookup
requirement, or another durable store.

In record-backed lanes, a snapshot hold or an unexpired retention anchor narrows
the deletion plan: remove ctrl_out/outbox from the eligible queue set, perform
any still-eligible ctrl_in/inbox cleanup, and record the existing
`mark_task_controls_deleted` progress. A hold is not a custody failure and must
not leave the whole record pending at the front of the limited SQL query. Only
launch custody, read/deletion errors, and actual incomplete eligible work retain
their existing pending semantics. Preserve the existing later path for retained
queues: open-control reconciliation where applicable, and recordless cleanup
after normal Monitor-record retirement. No new cursor or SQL lifecycle is added
to record-backed lanes. Retention is a minimum, so a longer-lived Monitor record
may postpone final cleanup. Verify that held records do not block a later ready
record and that residual queues remain eligible through the existing handoff.

Normal terminal publication therefore retains routing for at least the standard
result window. Missing final snapshot publication, abrupt death, administrative
pruning, and dump/load (which excludes runtime state) cannot promise a fresh
routing window. A retained valid local completion still protects standard
completion/output by its own clock, and can supply configuration under the discovery rules above.
Never synthesize a terminal outcome or renew a row on behalf of a dead task.
Custom/shared queues remain outside automatic cleanup and may outlive route
discoverability. Reserved-policy and reserved-cleanup age protections are unchanged.


Implementation owners: `weft/core/tasks/liveness_monitor.py` owns snapshot
retirement; `weft/core/monitor/task_monitor.py` acquires bounded cleanup evidence
and rechecks deletion; `weft/core/monitor/policies/runtime_control.py` and
`weft/core/monitor/policies/dead_task.py` own pure queue-eligibility decisions.

Cleanup has three owners: task-owned exit purge, monitor-owned lifecycle and
self-maintenance, and explicit operator commands for force and compaction:

- Task-owned cleanup clears standard task-local `T{tid}.ctrl_in` whenever the
  task unwinds through `cleanup()`, including success, failure, timeout, STOP,
  and KILL. It retains standard `T{tid}.ctrl_out` as completion evidence until
  the [MF-5] retention policy permits deletion. Outbox remains outside task-owned
  cleanup. A retained standard `ctrl_out` after terminal completion is expected.
- task-owned cleanup may remove spilled output when `cleanup_on_exit` is enabled
- the supervised task monitor additionally owns a default-on self-maintenance
  pass on a monotonic deadline (hourly by default): backend vacuum of claimed
  rows plus conservative runtime-state pruning of the
  managers/services/streaming/endpoints/pipelines groups with the foreground
  defaults (`min_age` 3600s, newest row per key retained). TID mappings are
  outside TaskMonitor maintenance custody. Maintenance is gated by
  `WEFT_TASK_MONITOR_MAINTENANCE` and its interval setting only;
  `WEFT_TASK_MONITOR_MODE` (including `report_only`) does not suppress it
- standard terminal completion/output cleanup follows the independent observation
  minimum below; custom/shared output queues remain outside automatic cleanup
- `LivenessMonitor` is the sole deleter of `weft.state.tasks.<tid>`.
  TaskMonitor has no per-cycle TID-mapping cleanup, and the `tid-mappings`
  group of the runtime pruning engine and `weft system prune` does not exist.
  One policy
  module owns malformed, superseded, and newest-row deletability. All categories
  retain their 2,400-second age fence; newest rows with validated terminal
  projections additionally satisfy the observation minimum below. Latest retirement strictly rereads
  history, matches the probed newest-valid ID, and verifies older valid rows
  absent before exact-deleting latest ([LIVENESS.R3]); failures preserve latest
  and retry. No component deletes a whole task-state queue. Tasks remain
  the only writers, and publication remains edge-triggered and append-only.
  A newest valid non-terminal snapshot means "live, or not yet proven dead."
  A names-only entry is insufficient; streaming protection likewise requires
  valid snapshot presence. Candidate cleanup still discovers owners without
  state queues and never starves eligible candidates behind protected ones.
  TaskMonitor's destruction-protection gate reads row presence plus live
  service-registry evidence and performs no host or runtime probing. If
  LivenessMonitor is down, rows persist and protection persists.
- the supervised task monitor exists in the current contract. Its default
  `delete` mode may delete exact message IDs selected by supported Weft-owned
  cleanup paths. A task whose mapping row was retired republishes on its next
  mapping edge. Post-disposal events clear the family's
  `task_control_deleted_at_ns` so recreated queues re-enter normal terminal
  cleanup. Without terminal lifecycle proof, plain `delete` mode
  automatically removes only stale control queues and preserves pending
  inbox, reserved, and unread outbox rows. Operators may later harvest those
  rows through explicit task-local retention pruning with `--apply --force`
  and its required archive. In `jsonl_then_delete` mode the pre-delete family
  report adds `observations.task_local_salvage` with nested schema
  `weft.task_local_salvage.v1`. Its `rows` are the first capped visible inbox,
  reserved, and unread outbox rows ordered by `(message_id, role, queue)`;
  each row carries queue, role, message ID, `utf-8+base64` body bytes,
  original/retained byte counts, and a truncation boolean. The object also
  carries total data-row count, total and per-role overflow counts, and
  ctrl-in/ctrl-out visible row counts. Nested message IDs use integers in the
  internal report and [SB-0.2] strings in external JSON. The complete object
  participates in the existing report ID; the nested schema versions this
  addition without changing the surrounding lifetime-report schema. Nothing
  is ever auto-requeued. `weft.log.tasks` is now
  table driven under collated ownership: the monitor scans visible
  rows in FIFO order up to `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT`, deletes
  malformed rows, folds valid rows into the Monitor table and, in `delete`
  mode, then
  deletes those exact raw rows once the fold is durably recorded
  ([OBS.13.3]); in `jsonl_then_delete` mode valid raw rows are retained
  until the family's `task_lifetime_report` has been handed off, and only
  malformed rows are deleted at fold time. Family summaries are emitted only
  after a
  completed FIFO high-water pass, never after a scan-limited or error-limited
  pass. Rows with non-terminal events such as
  `task_activity` do not close lifecycle groups solely because they carry a
  terminal-looking status. Terminal disposition may clear standard task-local
  residual `ctrl_in` and `inbox` queues left by forced process death or cleanup
  failure. Standard `ctrl_out`, `outbox`, and `reserved` remain retention-gated
  under their separate observation and reserved clocks. Standard completion/
  output use task-state presence, then latest qualifying terminal evidence,
  then TID fallback only after successful evidence reads. Retained non-interactive
  command stream frames, including stderr diagnostics, live in outbox rather
  than `ctrl_out`; completion evidence in `ctrl_out` has the same retention gate.
  `WEFT_TASK_MONITOR_BATCH_SIZE` caps retained task-log rows or cleanup
  candidates per cycle; it is not the task-log scan depth. Reserved rows remain
  protected recovery-sensitive evidence by default: the store-backed reserved
  cleanup worker slice (`select_reserved_cleanup_pending_tasks`) age-gates
  deletion the same way, on terminal-evidence age where available and TID age
  as fallback, so a `ReservedPolicy.KEEP` row is never deleted minutes after
  failure (see [OBS.13.5]). Per-cycle collation
  summaries are operational TaskMonitor evidence only, not durable archive
  records. Long-lived `ServiceTask` subclasses, including Manager, Heartbeat,
  and TaskMonitor, publish durable lifecycle transitions but suppress
  `task_activity` and poll-report rows in `weft.log.tasks`; their live activity
  is exposed through PONG/status, service registry, process title, and TID
  mapping surfaces. The supervised monitor may also derive dead-task cleanup
  candidates by listing standard task-local queue names, parsing `T{tid}.*`
  identities, and subtracting live runtime TIDs. That dead-task cleanup policy
  is owned by
  `weft/core/monitor/policies/dead_task.py` (TID discovery and per-TID cleanup
  plans), `weft/core/monitor/policies/runtime_control.py` (cheap eligibility
  and Monitor-record probe gating), and the bounded acquisition/selection loop
  in `weft/core/monitor/task_monitor.py::_select_dead_task_cleanup_slice`: it
  schedules a dead-TID cleanup
  job only when at least one currently existing standard task-local queue is
  eligible now. Without terminal lifecycle proof it selects only standard
  stale `T{tid}.ctrl_in` immediately and `T{tid}.ctrl_out` only at retention
  age; inbox, reserved, and unread outbox rows remain preserved for explicit
  archived forced pruning. With
  terminal proof, existing retention and cleanup gates continue to govern the
  standard task-local family.
  Manager-owned `T{manager_tid}.internal_reserved` queues are not standard
  task-local queues and are not selected by `task_local.dead_tid`. The active
  Manager owns those internal spawn reservations: it applies exact-ID pre-launch policy on shutdown regardless of task `cleanup_on_exit`, preserving unresolved launch custody,
  and it may immediately delete bounded batches from `.internal_reserved`
  queues whose manager TID is no longer present in the live manager registry, only after the exact checkpoint custody guard permits deletion.
  Retention-deferred dead-TID queues are counted for diagnostics but do not
  keep runtime cleanup pending or trigger raw task-log coalescing. Dead-TID
  cleanup must not mark Monitor collation families; if a Monitor row exists,
  terminal or reserved cleanup owns that family. Reserved cleanup marks
  `reserved_cleanup_checked_at_ns` for Monitor rows that need a reserved probe,
  including the already-absent no-row case, so a completed check converges
  without rediscovering queue names. Each cleanup cycle records
  cached policy/store stats and `policy_progress` summaries so PONG can
  distinguish "ran and selected zero", "reached a bounded waypoint", "reached
  a base case for now", "deferred future work", and "blocked by an error".
  The supervised monitor reports exactly four top-level cleanup policies:
  `task_log.retention` for retained raw task-log ingestion/deletion,
  `monitor_store.lifecycle` for durable store summary, repair, retirement, and
  orphan recovery work, `task_local.terminal_runtime` for terminal task-local
  queue cleanup, and `task_local.dead_tid` for name-derived dead-TID fallback
  cleanup. Runtime-state maintenance is reported only in the non-policy
  `maintenance` block; LivenessMonitor mapping retention is reported by its
  own service. Private cleanup phases may remain in reason counts or details, but
  they must not create additional `policy_progress[*].policy` identities.
  PONG must expose only cached progress and must not perform live cleanup
  scans or Monitor-store reads. The monitor must not delete active work,
  ambiguous task-local evidence, claimed outbox residue, user payload rows,
  spawn requests, manager control rows, or candidates without exact message
  IDs. `report_only` remains available as a non-destructive override for the
  cleanup policies; the self-maintenance pass has its own gate (below).
  Self-maintenance reports through a separate top-level non-policy
  `maintenance` STATUS block (`last_run_at_ns`, `vacuum_ok`, `runtime_prune`
  counters, `last_error`); it adds no `policy_progress[*].policy` identity.
- terminal-family readiness gates compare message IDs against the store's
  durable ingest checkpoint, never against host wall-clock time: with zero
  terminal retention, a terminal family is summary-ready (and control-cleanup
  ready) once `last_message_id` is at or behind the `weft.log.tasks`
  checkpoint. Wall-clock cutoffs remain correct only for observed-staleness
  windows (stale-open classification). In `jsonl_then_delete`, no raw
  task-log row of a family may be deleted before that family's
  `summary_emitted_at_ns` is set. The main deletion path and the
  marked-without-refs orphan recovery path both carry the summary gate.
  `raw_deleted_at_ns` means verified deletion: the exact-row apply layer
  re-verifies each candidate per ID when a batch under-deletes, so a
  still-present row is never reported deleted and families cannot be marked
  vacuously.
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
`weft/commands/prune.py`. The single `weft.state.tasks.*` cleanup policy
lives under `weft/liveness/`; its broker-aware executor is the peer task
`weft/core/tasks/liveness_monitor.py`. TaskMonitor and the foreground pruning
engine do not classify or delete that namespace. Shared broker-aware
namespace reads live in `weft/core/task_state.py`; `weft/liveness/` remains
broker-free evidence and policy.

## Queue Management Patterns

### Unified Reservation Pattern

The `.reserved` queue is both the in-flight work marker and the failure holding
area. That unification is deliberate. It keeps failure visibility and work
ownership in one place instead of splitting them across separate hidden queues.

_Implementation mapping_: `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`.

### Current Queue Lifecycle Management

Current rules:

- queue creation is implicit on first write; opening a task-state queue
  handle and subscribing before that first write is valid and must wake on
  publication, even while names-only listing has no entry
- Task cleanup closes task-owned handles, clears standard task-local
  `T{tid}.ctrl_in` rows, and retains standard `T{tid}.ctrl_out` completion
  evidence under [MF-5]. Custom queue ownership remains unchanged.
- `weft.state.services`, `weft.state.tasks.<tid>`, `weft.state.streaming`,
  `weft.state.endpoints`, and `weft.state.pipelines` are runtime-only
  bookkeeping queues; they may be read for live reconciliation but are not
  durable application history
- `weft system prune` defaults to dry-run and applies only with
  `--apply`; apply mode deletes exact candidate message IDs only
- foreground runtime pruning must preserve recent rows, unknown-shape
  rows (no recognized Weft schema tag), and rows whose live owner
remains active or ambiguous under existing liveness rules and within
the runtime pruner's minimum age and the external-supervisor staleness
window; a `weft.state.services` row that carries the current
service-owner schema tag but fails that schema's field validation is
disposable once older than the minimum age ([OBS.13.6]), and each such
deletion is logged at error level when Weft logging is enabled
- runtime-state pruning must not delete from `weft.log.tasks`,
  `weft.spawn.requests`, manager control queues, or task-local `T{tid}.*`
  queues
- retention pruning may delete selected rows from `weft.log.tasks`,
  `T{tid}.outbox`, `T{tid}.ctrl_out`, `T{tid}.ctrl_in`, `T{tid}.inbox`, and
  `T{tid}.reserved`. Ordinary apply protects active/ambiguous task evidence,
  claimed outbox residue, malformed/unknown-shape rows, and inbox/reserved work
  unless the class is safe for ordinary deletion. `--force --apply` is the
  explicit human override for those ordinary protections.
- the manager-supervised `TaskMonitor` deletes through two owners only:
  retained `weft.log.tasks` cleanup is orchestrated by
  `weft/core/monitor/task_monitor.py` with durable collation in
  `weft/core/monitor/store.py`, and task-local runtime queue cleanup runs
  in the TaskMonitor-owned runtime cleanup slices
  (`weft/core/monitor/policies/runtime_control.py`). Runtime-state
  pruning is the separate maintenance pass through
  `weft/core/pruning/runtime.py`. All destructive paths still use the
  canonical exact-delete helper shared with foreground `weft system
  prune`. The supervised monitor
  must not apply archive side effects, force-only retention cleanup, or
  task-local retention cleanup in this slice. Normal task exit owns standard
  control-queue cleanup; the monitor-owned runtime pass is a backstop for
  forced exits, cleanup failures, and stale pre-change rows. Task-log cleanup and external
  retention output use one public minimum-age clock:
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`, defaulting to 172800 seconds.
  `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT` remains the per-cycle task-log scan
  depth and defaults to 50000 rows. Eligible standard `T{tid}.reserved` queues
  are deleted by the same supervised monitor `delete` mode path, bounded
  by the control cleanup limit and protected by active runtime evidence. For
  Monitor-owned terminal non-completed families, a successful reserved cleanup
  or no-row reserved probe records `reserved_cleanup_checked_at_ns`; physical
  parent-row retirement waits for that marker when `reserved_probe_needed` is
  set. When a
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
  uses `weft/core/monitor/lifetime_report.py` for reusable
  `task_lifetime_report` JSON shapes; file output uses Python's standard
  rotating logfile handler and defaults to `logs/weft.log` under the Weft
  project root when a mode enables it. It uses the same canonical exact-delete
  helper after durable table ingestion for collated mode, after accepted
  lifetime-report handoff for `jsonl_then_delete`, and after raw external emit
  for raw mode.
  The Monitor collation store is always enabled; there is no disabled
  mode. Retained `weft.log.tasks` deletion has exactly two owners, selected
  by configuration, not by `WEFT_TASK_MONITOR_MODE`: when
  `WEFT_LOG_TASKS_EXTERNAL_ENABLED=true` with
  `WEFT_LOG_TASKS_EXTERNAL_MODE=raw`, raw rows are emitted and deleted
  without table ingest (`raw_external` ownership); otherwise the collation
  store owns them. Under collated ownership the supervised `delete` mode
  deletes retained collated raw rows when the store is available;
  `report_only` is the non-destructive override and continues table ingest
  and checkpoint advance without deletion. Under `raw_external` ownership
  `report_only` performs no ingest and advances no checkpoint. "Available"
  means opened and verified per [SB-0.4a]; under collated ownership, if the
  store is unavailable,
  well-formed task-log rows remain visible, no task-local runtime cleanup
  runs, and there is no fallback deleter. Claimed `weft.log.tasks` rows are
  outside Monitor collation: they are not ingested, and their physical
  removal is SimpleBroker vacuum's job — auto-vacuum, the monitor
  maintenance pass, or `weft system tidy` — none of which is
  unconditional. A configured external path by
  itself is only a destination and does not trigger logging. Open families with
  a usable reporting interval may be
  classified `suspected_inactive` after the configured reporting gap; open
  families without a usable interval may be classified `stale_open` only after
  `WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS`. These classifications are
  operational Monitor-table state, not public lifecycle truth. `stale_open`
  disposal is gated on destruction-protection evidence before it is applied,
  not only at later queue-delete time: a candidate whose TID is in the
  Monitor's destruction-protected set (`_destruction_protected_runtime_tids`)
  is skipped, mirroring how a quiet-but-live plain task — one with no
  reporting interval, which by definition emits nothing after `work_started`
  — is otherwise indistinguishable from an abandoned family in the task log
  alone. The destruction-protected set covers proven-live owners (live
  host-PID `(pid, create_time)` proof, live service-registry rows) plus
  owners whose newest `weft.state.tasks.<tid>` row is undecidable
  (non-host runner handles with no probeable host PIDs), applying the same
  undecidable-means-live rule the tid-mapping cleanup policy uses to keep
  the row itself. Delete-time rechecks apply the same standard to disposed
  families without terminal proof, while terminal-proven families keep the
  positive-evidence recheck only. See [OBS.13.7] for the full
  evidence-model statement. PONG exposes
  cached external-log status, deferred-write counts, and retention settings
  only; it does not open files, scan queues, flush deferred writes, or query
  Monitor tables. The persistent TaskMonitor runtime mapping in
  `weft.state.tasks.<tid>` includes the same compact external-log diagnostics
  so passive status surfaces can report external-log health without PINGing
  the monitor or touching the configured path.
- `weft system tidy` handles backend-native cleanup of empty queues and broker
  maintenance
- autonomous destructive queue lifecycle is limited to the supervised
  task-monitor `delete` mode and only for exact safe candidates

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


### Recovery before the process-creation checkpoint [MF-6.2]

A valid structural Manager checkpoint retains its ordinary source and reserved queue names. Any remaining ordinary reservation protects that checkpoint and the reserved queue from automatic cleanup, including the interval after reservation but before the mandatory pre-launch checkpoint. On cold recovery, a positively dead Manager's current-protocol checkpoint proves that an unreferenced reservation did not cross the process-creation fence. A successor may exact-requeue that request to its recorded ordinary source only if no launch checkpoint references the ID and no task-owned runtime snapshot exists for that TID. Unknown owner/protocol, uncertain reads, existing task evidence, and unresolved launch references preserve the reservation without replay. Claim eligibility remains SimpleBroker's responsibility. Recovery retries blocked exact moves in bounded maintenance; it does not infer absence from a claim race.

_Implementation mapping_: `weft/core/manager.py::Manager._recover_unlaunched_reservations`, `weft/core/manager.py::Manager._maintain_orphan_reservations`, `weft/core/task_state.py::manager_launch_custody_pending`, `tests/system/test_family_recovery.py`, `tests/tasks/test_liveness_monitor.py`, `tests/tasks/test_task_monitor.py`.

### One-time removal of the ordinary child source [MF-6.1]

A coordinated upgrade quiesces old producers and runtimes before migration. The explicit submission-recovery adapter preflights old child-reserved rows with positive scoped settlement evidence and preserves ambiguous rows without replay. Atomic exact-ID moves return positively undispatched child-source requests to the primary source without changing their IDs, TaskSpec sections, or stored bodies. For the inventoried legacy cohort only, the adapter normalizes the old submitter envelope at delivery as independent-root scheduling with unknown ancestry, then delegates to the ordinary Manager's canonical selection, validation, measured admission, launch, checkpoint, and acknowledgement path. The normal Manager has no legacy decoder or child-source watcher. Reruns recover the cohort from retained request queues; a rollback inventory never becomes the sole custody of accepted intent. The adapter follows normal Manager lifetime and must not terminate live children simply because migration handoff succeeded. Finite work drains naturally; explicitly persistent work can require continuing operator supervision.

_Implementation mapping_: `weft/commands/_spawn_submission.py::LegacyChildMigration`, `weft/commands/_spawn_submission.py::prepare_legacy_child_migration`, `weft/commands/_spawn_submission.py::LegacyChildMigrationManager`, `tests/commands/test_family_migration.py`.

## Related Plans

- [Bounded startup no-wait fast path](../plans/2026-10-20-bounded-startup-fast-path.md) governs bounded startup feedback under [MA-1.8].

Implementation mapping: `weft/core/spawn_requests.py::observe_spawn_request_custody`
owns source-before-reserved acquisition;
`weft/commands/tasks.py::_submission_point_observation` owns the shared final
task-evidence refresh for both public projections.
`weft/commands/tasks.py::task_runtime_liveness` owns explicit runtime probing
and selected-message-ID revalidation. Coverage:
`tests/commands/test_submission_observation.py` and
`tests/commands/test_runtime_liveness_observation.py`.

Implementation plan: [Public submission custody observation](../plans/2026-10-16-public-submission-custody-observation-plan.md) (custody contract promoted 2026-10-19; implemented 2026-10-19; runtime-liveness slice 5 promoted 2026-10-19; implemented 2026-10-19).

- [Operator-Owned Filesystem Permissions](../plans/2026-10-15-operator-owned-permissions.md) records removal of Weft permission enforcement under [CTX.5].

- [Work-conserving family scheduling](../plans/2026-10-15-work-conserving-family-scheduling.md) removes the frontier cap and enables native PostgreSQL measured admission by default.

- [Measured admission and bounded family scheduling](../plans/2026-10-14-measured-admission-family-scheduling.md) governs the measured-admission and bounded-family implementation.

- [Supervisor terminal observations in operational collations](../plans/2026-10-13-supervisor-terminal-projection.md) implements the operational supervisor-terminal projection under [MF-5].
- [Connection-aware admission and child progress](../plans/2026-10-13-connection-aware-child-admission-plan.md) is superseded for routing and admission under [MF-1], [MF-5], and [MF-6].

- [Burst submission complexity](../plans/2026-10-12-burst-submission-complexity-plan.md) - implements completion routing, retention, incremental spawn observation, and candidate-scoped endpoint evidence.

- [Result observation simplification](../plans/2026-10-10-result-observation-simplification-plan.md)

- [Direct result observation implementation repair](../plans/2026-10-09-direct-result-observation-implementation-plan.md)

- [Direct result observation spec changes](../plans/2026-10-09-direct-result-observation-spec-plan.md)

- [Client-owned submission session reuse](../plans/2026-09-25-client-owned-submission-session-plan.md) - retains only the session lease across client submissions while preserving queue-first acceptance and bounded operations.

- [TaskMonitor MaintenanceWorker](../plans/2026-09-22-task-monitor-maintenance-worker-plan.md): explicit maintenance construction and reactor-owned state under [IMPL.11].

- [Event-routed PING/PONG](../plans/2026-09-18-event-routed-manager-pong-plan.md) - routes PONG through requester-owned `ctrl_in` queues and returns Manager probe absence to the existing owning-policy cadence.

- [Watcher Reactor Restoration Plan](../plans/2026-09-17-watcher-reactor-restoration-plan.md) - makes the watcher reactor the single task wait owner and publishes the remaining service clocks as reactor deadlines.

- retired: 2026-09-11-per-tid-task-state-namespace-plan — source 396c3b85; see docs/plans/README.md

- [Audit regression fixes](../plans/2026-09-11-audit-regression-fixes-plan.md)

- retired: 2026-08-31-collation-store-toggle-removal-plan — source 396c3b85; see docs/plans/README.md

- retired: 2026-08-31-registry-custody-contracts-plan — source 396c3b85; see docs/plans/README.md

- retired: 2026-08-31-monitor-and-task-correctness-fixes-plan — source 396c3b85; see docs/plans/README.md

- [Reserved disposition and task requeue removal](../plans/2026-08-31-reserved-disposition-and-requeue-removal-plan.md)

- retired: 2026-08-10-canonical-contract-and-dead-code-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-08-10-simplebroker-7-json-message-id-boundary-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-08-10-interactive-session-lifecycle-refactor-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-08-10-result-observation-and-control-transition-refactor-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-07-29-deduplication-and-test-integrity-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-07-11-simplebroker-committed-write-id-adoption-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-07-09-reference-reactor-safety-hardening-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-07-02-runtime-correctness-and-retention-remediation-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-06-29-manager-task-spawned-retention-policy-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-06-20-weft-django-terminal-status-monitor-store-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-06-11-simplebroker-dump-load-adoption-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-06-10-self-healing-runtime-maintenance-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-06-01-critical-review-remediation-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-31-task-monitor-orphan-log-and-status-reconciliation-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-30-task-monitor-mode-and-rotating-log-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-30-task-monitor-external-log-health-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-28-stale-service-owner-runtime-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-27-service-collation-reporting-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-26-monitor-five-cleanup-policy-consolidation-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-26-service-task-worker-api-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-25-monitor-dead-task-catchup-convergence-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-24-monitor-policy-progress-contract-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-23-monitor-cleanup-policy-convergence-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-23-monitor-cleanup-executor-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-22-monitor-policy-modules-and-dead-task-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-20-monitor-collation-table-retirement-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-20-simplebroker-api-adoption-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-20-monitor-reactor-worker-refactor-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-20-monitor-fair-cleanup-scheduling-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-16-task-log-external-logging-and-retention-policy-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-18-monitor-table-driven-retained-log-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-16-monitor-store-hardening-and-layering-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-08-agent-session-and-task-startup-observability-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-06-lifecycle-reconciliation-architecture-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-06-status-coherence-and-stale-pid-liveness-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-06-task-evidence-reconciliation-model-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-06-terminal-publication-hardening-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-07-lifecycle-monitor-archive-sink-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-04-14-spawn-request-reconciliation-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-04-13-spec-corpus-current-vs-planned-split-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-04-09-manager-bootstrap-unification-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-04-13-pipeline-spec-expansion-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-04-13-result-stream-implementation-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-04-16-autostart-hardening-and-contract-alignment-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-04-16-pipeline-autostart-extension-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-04-17-canonical-owner-fence-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-08-manager-owned-internal-service-supervision-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-08-phase-7-manager-service-reconciler-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-08-deterministic-manager-service-reconciler-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-09-runtime-liveness-probe-registry-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-09-prune-path-unification-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-10-manager-service-authority-boundary-hardening-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-11-manager-work-stealing-dispatch-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-11-manager-serve-operational-log-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-12-bounded-task-monitor-cleanup-policy-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-12-task-monitor-cleanup-composition-refactor-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-13-manager-liveness-and-leadership-robustness-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-13-internal-state-machine-helper-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-13-task-monitor-pong-policy-stats-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-15-swappable-task-log-family-scanner-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-15-manager-hot-loop-reduction-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-15-manager-reactor-hot-loop-follow-up-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-16-monitor-durable-collation-store-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-19-task-monitor-bounded-control-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-18-reactive-task-loop-hot-probe-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-29-reliability-and-doc-fixes-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-29-task-monitor-general-lifetime-reporting-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-06-09-evaluation-findings-remediation-plan — source 396c3b85; see docs/plans/README.md

- [Manager Polling Latency](../plans/2026-09-17-manager-polling-latency-plan.md)

Implementation plan: [Task-owned observation and read separation](../plans/2026-10-14-task-monitor-read-separation-and-simplification-plan.md).
