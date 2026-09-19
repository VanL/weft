# Event-Routed PING/PONG Plan

Status: completed
Source specs: `docs/specifications/00-Overview_and_Architecture.md`; `docs/specifications/00-Quick_Reference.md`; `docs/specifications/01-Core_Components.md` [CC-2.4]; `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-1.6a], [MA-1.7]; `docs/specifications/05-Message_Flow_and_State.md` [MF-3], [MF-6]; `docs/specifications/07-System_Invariants.md` [QUEUE.2a], [MANAGER.8], [MANAGER.15]; `docs/specifications/10-CLI_Interface.md` [CLI-1.3]; `docs/specifications/14-Python_API_Surfaces.md` [PY-2]
Superseded by: none

Class: 5. This changes the control-message contract and the Manager liveness
path. It is also risky because the change crosses the request producer,
responder, requester reactor, and Manager authority reducer.

Plan type: implementation with spec revision.

## Goal

Make PING/PONG follow Weft's ordinary task reactor design.

A requester writes a keyed PING to the target's `ctrl_in`. The PING names the
requester's own `ctrl_in` as `reply_to`. The target writes one PONG to that
queue. The requester's watcher wakes, recognizes the PONG, and passes it to the
existing probe matcher and result handling.

```text
requester ctrl_in  <---- PONG ----  target task
       ^                              ^
       |                              |
 requester watcher        PING {request_id, reply_to}
```

Every requester owns the queue it names. A long-lived task uses its configured
`ctrl_in`. A client request — a CLI invocation or an embedded client call in
`weft/commands/` — becomes an **ephemeral control requester**: it mints a TID from the
existing generator, names `T{tid}.ctrl_in` as `reply_to`, drives the same
`MultiQueueWatcher` wait on that queue, consumes its PONG, and deletes the
queue.

There is one PING path, one wire shape, and one wait implementation. A PONG is
not discovered by polling the target's `ctrl_out`.

## Outcome Checklist

- [x] Every PING is keyed and names the requester's own `ctrl_in` in
  `reply_to`: a long-lived task's configured queue, or an ephemeral requester's
  `T{tid}.ctrl_in`.
- [x] Ephemeral requesters mint their TID from the existing neutral primitive
  `generate_spawn_request_timestamp()`, so the value is a valid hybrid timestamp
  and cannot collide with a real task TID, without importing `manager_runtime`
  from `control_probe`.
- [x] A synchronous probe's timeout is bounded below the dead-TID cleanup age,
  so a probe cannot outlive the protection of its own reply queue.
- [x] The target writes exactly one PONG to `reply_to`.
- [x] No PING falls back to the target's `ctrl_out`.
- [x] After strict request parsing, the normal `ctrl_in` drain dispatches the
  row to a protected reply hook, acknowledges it once, and lets the requester's
  existing matcher recognize a PONG.
- [x] A PONG never enters PING request dispatch and cannot produce another
  PONG.
- [x] Manager leadership and service probes reuse their existing request-ID,
  target-TID, payload validation, and result reduction.
- [x] Manager probes do not scan or sweep the target's `ctrl_out`.
- [x] The 50 ms leadership and 150 ms service probe deadlines are removed.
- [x] A missing PONG is evaluated by the existing leadership or service policy
  cadence. It does not create a new reactor timer.
- [x] No new queue kind, watcher membership, polling loop, or response protocol
  is added. An ephemeral requester reuses the existing `T{tid}.ctrl_in` naming.
- [x] All Weft-owned PING producers and consumers use the same wire shape and
  the same `MultiQueueWatcher` wait; no producer keeps a private sleep loop.
- [x] An ephemeral requester deletes its reply queue on every exit path, so a
  successful probe leaves no queue name behind.
- [x] A reply queue orphaned by requester death or by a post-cleanup late reply
  is retired by the existing name-derived dead-task sweep. No new retirement
  mechanism is added.
- [x] SQLite and PostgreSQL tests prove that PONG arrival wakes the requester's
  watcher and reaches the existing result handler, for both a long-lived task
  and an ephemeral requester.

## Conceptual Fit

Weft is queue-first and task-shaped. A task already owns `ctrl_in`, and the
reactor already watches it. PONG is therefore another control-queue event. It
does not need a private polling mechanism.

The present Manager path diverged from that model:

1. the Manager writes PING to a target's `ctrl_in`;
2. the target writes PONG to its own `ctrl_out`;
3. the Manager periodically scans that foreign queue;
4. short deadlines force later scans.

The change restores the direct model:

1. the Manager writes PING with `reply_to=self.ctrl_in`;
2. the target writes PONG to that named queue;
3. the Manager's existing watcher wakes;
4. the normal control drain feeds the PONG to the existing probe handling.

The same rule applies to every PING. The Manager is the latency-sensitive
case, not a separate protocol.

Client-originated PINGs fit the same model rather than forming a second one.
`weft/commands/` is the shared CLI and client capability layer; by the repo's
one-way layering (`cli -> commands -> core`) it can never be a long-lived task
and owns no durable queue. Rather than giving it a fallback route, it adopts a
task-shaped control identity for the duration of the probe: mint a TID, own
`T{tid}.ctrl_in`, drive the same watcher, consume the reply, delete the queue.
The same control-message rule therefore holds for every requester, and
`reply_to` means exactly one thing everywhere: a queue the requester drains.

Two properties make the ephemeral form safe without new machinery. Because the
requester consumes its own reply queue, `has_pending()` self-clears, so the
shared `MultiQueueWatcher` wait cannot spin on retained rows — the failure mode
that rules out observing a queue the requester does not drain. And because the
queue is named `T{tid}.*`, an orphan left by requester death is already owned by
the existing name-derived dead-task sweep.

## Settled Decisions

1. `reply_to` is the requester's own `ctrl_in` queue name: a long-lived task's
   configured queue, or an ephemeral requester's `T{tid}.ctrl_in`. In both
   cases it is a queue the requester drains.
2. `reply_to` is required on PING. There is no legacy PING response route and
   no client-only route.
3. `request_id` is required on PING so the requester can correlate the PONG.
4. The target trusts the named queue. Weft assumes user-level trust; do not add
   queue ownership, authorization, or capability checks.
5. The target writes one response. A write failure is logged through the
   existing best-effort response failure path and does not trigger a second
   destination.
6. PONG keeps its current semantic payload and echoes `request_id`. It does not
   echo `reply_to`.
7. Strict request parsing runs before reply dispatch because both a PING and a
   PONG carry `command="PING"`.
8. The base reply hook is a no-op. The Manager override reuses the existing
   canonical PONG matcher for pending leadership and service probes.
9. No dynamic watcher membership is needed. The requester's `ctrl_in` is
   already watched.
10. No PONG-specific wake interval is needed. Arrival is an event; absence is
    evaluated by the existing policy timer.
11. The Manager drains `ctrl_in` before registration, leadership, or service
    work that can resolve a probe. The existing bounded control drain remains
    bounded. If a PONG is behind more than one batch, burst mode drains later
    batches without a quiet wait. A due policy pass may resolve the probe before
    such a deeper PONG is reached; that is documented overload behavior. No
    drain-completeness state, backlog guard, or deadline is added.
12. No backward compatibility is required. Weft is in beta and its current
    users are internal.
13. A synchronous probe uses an ephemeral control requester. It mints a TID from
    `generate_spawn_request_timestamp()` (hybrid timestamp, no collision with
    real task TIDs; the neutral primitive, because `manager_runtime` already
    imports `control_probe`), uses `T{tid}.ctrl_in` as `reply_to`, and is the
    sole reader of that queue.
    It registers no task record, TaskSpec, tid-mapping, or runtime handle: it
    is a reply address, not a scheduled unit of work.
14. The ephemeral requester waits with `MultiQueueWatcher` through the same
    protected wait body a long-lived task uses. The existing keyed-probe sleep
    loop is removed rather than reshaped, and `QueueChangeMonitor` is not used
    for PING; that class stays owned by the long client waits that already use
    it.
15. Consuming the reply empties the queue but leaves its name listed. The
    ephemeral requester therefore deletes the queue on every exit path —
    success, timeout, and error — so a completed probe leaves zero residue.
16. Retirement of an orphan reply queue uses the existing name-derived
    dead-task sweep and no new mechanism. TaskMonitor already scans
    `T*.ctrl_in`, and its selector explicitly owns families that have no
    Monitor collation row, which is exactly the shape of an abandoned
    ephemeral requester. A synchronous probe timeout is bounded at half the
    2400 s minimum cleanup age, reserving the other half for setup, teardown,
    and sweep latency so the sweep cannot race a live probe. This also settles
    the previously open unknown-owner retirement bound.

## Source Files and Ownership

- `weft/core/control_messages.py`: exact PING request grammar.
- `weft/core/tasks/base.py`: normal `ctrl_in` drain, PING response routing, PONG
  dispatch hook, and exact acknowledgement.
- `weft/core/tasks/consumer.py`: active-worker control drain must continue to
  delegate non-command rows to the shared BaseTask path.
- `weft/core/tasks/pipeline.py`: pipeline PING response uses the same
  `reply_to` routing while preserving its PONG fields.
- `weft/core/manager.py`: writes Manager and service PINGs, stores pending
  probes, and applies existing PONG validation and authority rules.
- `weft/core/control_probe.py`: the existing canonical PONG matcher is retained;
  `send_keyed_ping_probe()`'s target-`ctrl_out` scan, sleep loop, and reply
  sweep are replaced by the ephemeral-requester probe. It must not remain as a
  second PING route.
- `weft/_constants.py`: removes the 50 ms and 150 ms incremental-probe timeout
  constants, and `CONTROL_SURFACE_WAIT_INTERVAL` if the probe was its last
  caller.

Synchronous PING producers. Every one of these blocks its caller inside
`send_keyed_ping_probe()`, so each gets an ephemeral requester through the
shared helper. Most are reached from the command layer, which by the
`cli -> commands -> core` rule can never be a long-lived task; one
(`heartbeat.py`) is reached from a real task that nevertheless blocks its own
reactor, which is why the boundary is call shape rather than caller identity:

- `weft/commands/tasks.py`: `task_ping()` backs the `weft task ping` command,
  and `known_tid_evidence()` callers reach the probe through task evidence.
- `weft/core/task_evidence.py`: `ping_pong_evidence()`.
- `weft/core/manager_runtime.py`: two distinct producers.
  `_probe_recovery_candidate()`, reached from `observe_manager_availability()`
  and `decide_manager_recovery()` and only on the uncertain-incumbent branch;
  and `_manager_record_has_matched_pong()`, reached from
  `_manager_registry_disposition()`, `_manager_record_diagnostic()`, and
  `_manager_record_is_unreachable_foreground_supervisor()`. Both are reached
  from `weft/commands/submission.py` and from `TaskMonitor` via
  `ensure_heartbeat_service()` -> `ensure_manager()`. All get an ephemeral
  requester; the helper never borrows a caller's `ctrl_in`, so nothing needs
  threading through this layer.
- `weft/core/heartbeat.py`: `_heartbeat_endpoint_candidate()`, reached from
  `TaskMonitor._ensure_heartbeat_registered()` inside `_process_reactor_turn()`.
  `TaskMonitor` is a real task, but this call blocks its reactor, so it is a
  synchronous probe and uses an ephemeral requester like any other.

Read before implementation:

- `BaseTask._handle_control_message()` and `_handle_control_command()`;
- `Manager._advance_manager_pong_probe()` and
  `Manager._advance_service_pong_probe()`;
- Manager leadership and service `next_wait_timeout()` composition;
- Consumer active-worker control handling;
- the MultiQueueWatcher tests that prove `ctrl_in` activity wakes SQLite and
  PostgreSQL task reactors;
- `generate_spawn_request_timestamp()` in `weft/core/spawn_requests.py`, the
  neutral hybrid-timestamp primitive an ephemeral requester mints from, and
  `manager_runtime.generate_tid()` which wraps it — note `manager_runtime`
  imports `control_probe`, so the probe must use the primitive directly;
- `is_old_enough()` in `weft/core/monitor/policies/dead_task.py`, which derives
  sweep eligibility from the TID and therefore bounds how long a probe may
  wait; and
- `select_runtime_dead_task_cleanup_candidates()` in
  `weft/core/monitor/policies/runtime_control.py`, the name-derived sweep that
  retires an abandoned reply queue.

Comprehension gate:

1. Why does routing PONG to requester `ctrl_in` remove the need to watch or
   scan target `ctrl_out`?
2. Why must the parser distinguish a PONG before it can enter PING command
   dispatch?
3. Which existing policy cadence decides that a leadership or service PONG did
   not arrive?
4. Why can an ephemeral requester use the shared `MultiQueueWatcher` wait when
   a peek-only observer of a shared queue cannot? Expected: it is the sole
   reader of its own reply queue, so `has_pending()` self-clears and the wait
   cannot spin on a retained row.
5. What retires an ephemeral reply queue when the requester dies mid-probe, and
   why can that sweep not race a live probe?

## Invariants

- PING/PONG remains best-effort positive liveness evidence. A missing PONG is
  not proof that a task is dead.
- Manager authority still requires the existing exact target TID, request ID,
  role, queue, context, status, and stop-state checks.
- Pending probes remain uncertain until a matching PONG or an eligible policy
  evaluation resolves them.
- A pending service probe is active service-convergence work and therefore uses
  the existing active cadence rather than the stable-audit cadence.
- Within the current reconciliation scope, a pending service probe keeps its
  `service_key` in the existing evidence set until the probe is reduced or
  retired. The key comes from the pending probe value; pending-map keys are
  composite candidate identities.
- A reply matches exactly one pending probe. Unmatched, malformed and
  ambiguous replies are consumed and dropped without a state transition.
- Late replies split by whether the requester still exists. A late reply that
  arrives while its requester is still draining is consumed and dropped like
  any other non-matching row. A reply written after an ephemeral requester has
  exited has no reader: it stays as an orphan row and is retired by the
  name-derived dead-task sweep.
- A stored PONG must be available to the existing reducer in the same reactor
  turn, subject to existing safety fences such as an active child launch.
- Reply-driven work must not reset leadership or service policy clocks.
- Constructor bootstrap anchors the existing policy cadence after it creates a
  probe. The first reactor turn does not turn that new probe into no-PONG.
- A successful complete policy scan may retire pending probes whose source is
  no longer present. A failed or incomplete scan does not prove absence.
- When autostart is included and a known autostart probe has a stored PONG, the
  current manifest scan runs even if its ordinary scan throttle is not due, so
  the existing reducer can compare the reply with current source evidence.
- The normal 32-row control batch and burst-mode behavior do not change.
- Other control responses and terminal notifications keep their existing
  routes. This plan changes PING/PONG only.
- Task state, TID, TaskSpec immutability, reserved policy, and spawn-process
  invariants do not change.
- An ephemeral requester TID is a reply address only. It is minted from the
  existing hybrid-timestamp generator and is therefore a valid, unique TID, but
  it registers no task-log record, TaskSpec, tid-mapping, runtime handle, or
  registry row. It must not appear as a task in `weft task list`, monitor
  collation, or leadership candidacy.
- An ephemeral requester owns exactly one queue, `T{tid}.ctrl_in`, and is its
  sole reader. It creates no inbox, outbox, reserved, or `ctrl_out` queue.
- The ephemeral requester stops its watcher and then best-effort deletes any
  remaining reply rows, on success, timeout, and error. Neither failure changes
  the probe result.
- A reply queue name is derived from message rows, not from a registered queue
  object, so a fully consumed and vacuumed queue leaves nothing. A claimed row
  keeps the name listed until vacuum, which is why cleanup is explicit rather
  than implied by consumption.
- Two paths can leave an orphan row: requester death mid-probe, and a **late
  reply** — the target writing its PONG after the requester timed out and
  cleaned up, which re-creates a row under that name. Both are retired by the
  existing name-derived dead-task sweep under its existing minimum-age gate.
  This plan adds no sweeper, no retention field, and no registry entry.
- A PONG that arrives after its requester exited is retired with that queue. It
  is never redirected and never becomes another task's input.
- No dependency or SimpleBroker change is needed.

## Spec Baseline

- Diff base: `a872826b847a4bdd9d8c129639fb81d847fe4649`.
- Worktree baseline: the reactor-restoration implementation and related spec
  edits are uncommitted. This plan changes those in-flight specs again; the
  exact spec diff is part of the review input.
- Promotion baseline: diff base `a872826b847a4bdd9d8c129639fb81d847fe4649`
  plus the uncommitted seven-spec promotion diff identified by SHA-256
  `713bbf3828d9d3812bb4b842c2a4f0da1266e266fe97ba9139a7eb759ddcdaa8`
  on 2026-09-19. Supporting current-doc corrections in `docs/lessons.md` are
  part of the same worktree state but not normative spec input.

## Proposed Spec Delta

Promotion strategy: A, in-file text before implementation-link claims.

| Spec file | Strategy | Sections |
| --- | --- | --- |
| `docs/specifications/00-Quick_Reference.md` | A | Queue names; control messages |
| `docs/specifications/01-Core_Components.md` | A | [CC-2.4] |
| `docs/specifications/03-Manager_Architecture.md` | A | [MA-1.4], [MA-1.6a], [MA-1.7] |
| `docs/specifications/05-Message_Flow_and_State.md` | A | [MF-3], [MF-6] |
| `docs/specifications/07-System_Invariants.md` | A | [QUEUE.2a], [MANAGER.8], [MANAGER.15] |
| `docs/specifications/10-CLI_Interface.md` | A | [CLI-1.3] |
| `docs/specifications/14-Python_API_Surfaces.md` | A | [PY-2] |

### [CC-2.4] and [QUEUE.2a]

Replace the PING envelope and response-routing text with:

> A PING request is exactly `{command, request_id, reply_to}`. `command` is
> exactly `PING`; `request_id` and `reply_to` are nonblank strings; and
> `reply_to` is the requester's own `ctrl_in` queue — the configured queue of a
> long-lived task, or `T{tid}.ctrl_in` for an ephemeral requester that minted a
> TID for this probe. In both cases the requester is the sole reader of that
> queue. Other control requests retain their existing exact envelopes. A valid PING writes
> exactly one PONG to `reply_to`; it never writes that PONG to the responder's
> `ctrl_out`. The PONG echoes `request_id` and does not echo `reply_to`.
>
> The normal `ctrl_in` handler first parses strict requests. A row that is not
> a request is passed to the task's reply hook and acknowledged once. The
> requester uses its canonical PONG matcher there. A PONG is never dispatched
> as a PING request and never produces another response.

### [MF-3]

Replace the PING/PONG flow and reply-retirement text with:

> ```text
> requester ctrl_in  <---- PONG ----  target task
>        ^                              ^
>        |                              |
>  task reactor             PING {request_id, reply_to}
> ```
>
> A requester sends a keyed PING to the target's `ctrl_in` and names its own
> `ctrl_in` in `reply_to`. A long-lived task names its configured queue. A
> synchronous probe uses an ephemeral control requester: it mints a TID, names
> `T{tid}.ctrl_in`, drains that queue through the same watcher wait, and
> deletes it on every exit path. An ephemeral requester registers no task
> record and is a reply address only. Two paths can leave an orphan row —
> requester death mid-probe, and a reply written after the requester timed out
> and cleaned up — and both are retired by the existing name-derived dead-task
> sweep.
> The target writes one PONG to that queue.
> A reactor-integrated requester drains the PONG on its ordinary control
> drain; a synchronous probe drains it through the temporary watcher it owns
> for that probe. Either way the requester matches exact `request_id` and
> target TID and retires the row. Malformed, unmatched and ambiguous replies
> are retired without a state transition. A reply written after its requester
> has exited is retired by the name-derived dead-task sweep, not by a
> requester. PING/PONG uses no
> target-`ctrl_out` scan, reply sweep, dynamic watcher membership, or separate
> wait loop. A synchronous probe's own ephemeral reply queue is part of this
> contract, not an exception to it: the requester is that queue's sole reader
> and retires it.

Replace the automatic-recovery scheduling sentence with:

> PONG arrival is control-queue activity for the requester. Lack of a PONG is
> evaluated only by the existing policy cadence that owns the liveness
> decision; PING/PONG adds no private reactor deadline.

### [MA-1.4], [MA-1.6a], [MA-1.7], [MF-6], [MANAGER.8], and [MANAGER.15]

Replace Manager incremental-probe routing and scheduling text with:

> Manager leadership and service-owner probes write one keyed PING with
> `reply_to` set to the probing Manager's configured `ctrl_in`. A matching PONG
> arrives through the Manager's existing watcher and is stored on the matching
> pending probe before the existing leadership or service reducer runs. The
> probe has no response deadline and contributes no timeout to
> `next_wait_timeout()`.
>
> A stored PONG makes its owning reducer actionable immediately without moving
> the periodic policy clock. An unanswered leadership probe may be resolved as
> no-PONG only by the next eligible ordinary leadership evaluation. An
> unanswered service probe may be resolved as no-PONG only by the next eligible
> ordinary active-service evaluation. A pending service probe selects that
> active cadence. Constructor bootstrap anchors the applicable cadence after
> creating a probe so the first reactor turn cannot immediately resolve it as
> no-PONG. Within the current reconciliation scope, pending service probes keep
> their service keys in evidence collection until reduced or retired. When
> autostart is included, a stored PONG for a known autostart source bypasses the
> manifest-scan throttle so the reply is reduced against current source
> evidence. A successful complete policy scan may retire a probe whose source
> is absent; a failed or partial scan may not. Existing launch, authority, and
> source evidence rules continue to apply.

### [CLI-1.3] and [PY-2]

Add to both public surfaces:

> A synchronous keyed PING owns an ephemeral reply queue whose lifetime is
> bounded by the dead-task cleanup age. `--timeout` and the equivalent Python
> argument must be finite and satisfy
> `0 <= timeout <= CONTROL_PING_MAX_TIMEOUT_SECONDS`. A value outside that
> inclusive range is rejected with `CommandUsageError` before any queue is
> created; it is not silently clamped, and it is distinct from a probe that
> times out.

## Implementation Plan

### Preflight slice: capture the baseline

Run before the spec-promotion slice, on unmodified code, because once any part
of this plan lands a clean baseline is unrecoverable.

Construct an **uncertain-incumbent** case that definitely reaches
`_probe_recovery_candidate()` — for example a registry record for a manager
that is no longer answering — since `observe_manager_availability()` returns
early for `ready` and `absent` and a healthy `weft run` sends no PING.

Record in the Execution Log, for SQLite and PostgreSQL: probe wall-clock;
connections, sessions, and PostgreSQL listener registrations at rest, at peak
during one probe, and after the probe returns; and the same counts after N
sequential probes. Also record a `weft task ping` round trip against a live
task.

Gate: the baseline numbers are in the Execution Log and reviewed before
promotion begins. No code changes in this slice.

### Spec-promotion slice

Apply the text above to the seven governing specs. Add this plan to each touched
spec's related-plan or implementation-plan list. Record the promotion baseline
before code changes cite the new contract.

Verification:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py -q
bin/check-doc-paths
```

### Slice 1: one PING wire shape

Write failing codec tests, then:

1. Add required `request_id` and `reply_to` fields to the PING request model and
   encoder.
2. Keep the current exact request shapes for non-PING commands.
3. Retain the existing canonical PONG matcher. It must continue to require
   `command="PING"`, `status="ok"`, `message="PONG"`, exact target `tid`, and
   exact `request_id`, while allowing current task and Manager PONG extension
   fields. Do not add a second matcher.
4. Update every Weft-owned PING producer to use the same encoder and supply a
   `reply_to` it drains: a long-lived task's configured `ctrl_in`, or an
   ephemeral requester's `T{tid}.ctrl_in` from Slice 3.
5. Remove any unkeyed or reply-less PING producer. Do not add a compatibility
   branch. No producer is deleted for lacking a `ctrl_in`; the ephemeral
   requester gives every client producer one.

Firing tests:

- valid PING round-trip encoding;
- missing, blank, or non-string `request_id` or `reply_to` rejected;
- `reply_to` on non-PING rejected;
- duplicate and extra request keys rejected; and
- current non-PING request envelopes unchanged.

### Slice 2: route PONG and dispatch it from `ctrl_in`

Write failing task tests, then:

1. Change the PING branch in BaseTask to write the current PONG payload to
   `request.reply_to`. Remove PipelineTask's duplicate branch so it delegates
   PING through the inherited BaseTask path.
2. Use the existing task-owned broker/session. Do not create a queue facade,
   session, or watcher interest for the destination.
3. In the normal `ctrl_in` handler, parse a strict request first. If it is not a
   request, pass the raw row to a protected `_handle_control_reply()` hook. The
   base implementation is a no-op. The shared handler then acknowledges the row
   once, as it does for malformed input today.
4. Make Consumer's active-worker control path delegate reply-like rows to this
   shared handler instead of acknowledging them before BaseTask can see them.
5. Keep one acknowledgement owner and preserve STOP/KILL deferral.

Firing tests:

- target writes one PONG to `reply_to` and none to target `ctrl_out`;
- PONG payload retains current fields and omits `reply_to`;
- a PONG on `ctrl_in` invokes the reply hook once and is acknowledged once;
- malformed and unmatched rows do not block later control messages;
- a PONG cannot cause a second PONG; and
- Consumer active work and PipelineTask use the same path.

### Slice 3: the ephemeral requester

This slice gives the command layer a requester identity so Slice 1's required
`reply_to` is satisfiable everywhere. Write failing probe tests, then:

1. Replace `send_keyed_ping_probe()`'s body with the ephemeral-requester flow:
   mint a TID, write the keyed PING with
   `reply_to=f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"`; wait on that one queue; consume
   the reply; return the existing `ControlProbeResult`.

   **Mint from the neutral primitive, not `manager_runtime`.**
   `weft/core/manager_runtime.py` already imports `send_keyed_ping_probe` from
   `control_probe`, so calling `manager_runtime.generate_tid()` here would
   close an import cycle. Call
   `weft.core.spawn_requests.generate_spawn_request_timestamp()` directly —
   the same primitive `generate_tid()` wraps — or relocate a shared TID helper
   to a neutral module. Do not import `manager_runtime` from `control_probe`.

   **Bound the probe timeout below the cleanup age — by rejecting, not
   clamping.** Sweep eligibility is computed from the TID itself
   (`is_old_enough(int(tid), now_ns, min_age_seconds)`), so an ephemeral
   requester's reply queue becomes sweep-eligible
   `TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS` after minting.
   `weft task ping --timeout` is unbounded today. The contract:

   - **Maximum**: a new `CONTROL_PING_MAX_TIMEOUT_SECONDS`, *derived* as
     `TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS / 2` (currently 1200 s) so
     it cannot drift out of sync if the cleanup age is retuned. The remaining
     1200 s is margin for mint, setup, teardown, and sweep-pass latency.
   - **Behavior: reject, do not clamp.** Clamping would silently return a
     different contract than the caller asked for. House style is to validate
     at the boundary and reject unsupported input explicitly rather than ignore
     it (§4.11).
   - **Validation owner**: `send_keyed_ping_probe()` itself, the one place the
     ephemeral TID is minted, so the CLI and the Python client are bound by the
     same rule and neither can bypass it. Validate before minting or writing.
   - **Error shape**: raise `CommandUsageError` specifically. `weft task ping`
     already catches `commands.WeftError` and exits through `_command_exit()`,
     which maps `CommandUsageError` to usage exit code 2. It is not a
     `ControlProbeResult.error`, which means a broker failure.
   - **Boundary tests**: accept zero, just under the maximum, and exactly the
     maximum; reject values just above it, negative values, NaN, and positive
     or negative infinity; and assert the derivation holds if
     `TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS` changes.

   This changes two public surfaces, so `10-CLI_Interface.md [CLI-1.3]` and
   `14-Python_API_Surfaces.md [PY-2]` are in the spec-promotion slice.

   Use `MultiQueueWatcher` in **manual-wait mode**, not its drive loop. The
   exact shape, because this is easy to get wrong:

   - construct it with a single `queue_configs` entry for the reply queue;
   - its `handler` is required by the constructor but is **never called**,
     because a manual wait does not dispatch. Supply a no-op and say so in a
     comment;
   - call the public `wait_for_activity(timeout)`, which runs on the **calling
     thread**, honours the caller's remaining budget, and starts the retained
     strategy on first use. Do not start a drive thread;
   - after the wait returns, the prober does its own `read_one()` on the reply
     queue and applies the canonical matcher;
   - loop wait-then-read until the matching PONG, the caller's deadline, or
     stop.

   This uses the same `_wait_for_activity_body()` and the same retained
   `PollingStrategy` a long-lived task uses, with no extra thread, no Event,
   and no second wait implementation.
2. **Always mint. Never borrow a caller's `ctrl_in`.** `send_keyed_ping_probe()`
   is synchronous: the caller is blocked inside it, so the helper — not the
   caller's reactor — must drain the reply queue. Draining a live task's
   `ctrl_in` would destructively read that task's own control messages. A STOP,
   KILL, or STATUS landing before the PONG would be consumed by the probe,
   rejected by the PONG matcher, and discarded; the task's control handler would
   never see it.

   This is reachable today: `TaskMonitor._ensure_heartbeat_registered()` is
   called from `_process_reactor_turn()`, so `TaskMonitor` blocks its own
   reactor inside this helper via `ensure_heartbeat_service()`.

   The boundary is therefore by **call shape, not caller identity**:

   - reactor-integrated probes (Manager leadership and service, Slice 4) are
     asynchronous, use the Manager's own configured `ctrl_in`, and are resolved
     by `_handle_control_reply()` on the Manager's normal drain;
   - every synchronous `send_keyed_ping_probe()` call owns an ephemeral
     requester, even when the outer caller happens to be a task.

   The helper takes no caller-supplied `reply_to`.
3. Delete the target-`ctrl_out` scan, the `time.sleep` poll loop, and the
   request-ID sweep from this helper. Keep the canonical matcher.
4. Tear down in a `finally` on success, timeout, and error, in this order:

   1. **Stop the watcher.** Construct this bounded watcher with
      `persistent=False`: it owns its queue operations, retained polling
      strategy, and (on PostgreSQL) listener registration without creating a
      `BrokerSession` that cannot close while an outer caller-owned broker
      operation remains open on the same thread. `stop()` releases those
      resources. Omitting it leaks a connection per probe — unacceptable
      anywhere, and especially on the submission hot path.
   2. **Best-effort delete any remaining reply rows.** Log a failure; do not
      change the probe result.

   Read through the watcher's public `get_queue()` rather than constructing a
   second `Queue`, so the probe holds one lease rather than two.

   On residue: `list_queues()` derives names from message rows, so there is no
   registered queue object to leak. A consumed row stays physically present
   until vacuum, so the name lingers until then; the delete is prompt cleanup,
   and it also covers malformed or extra rows the matcher rejected.
5. Do not register a task record, TaskSpec, tid-mapping, runtime handle, or
   registry row for the minted TID, and do not create any queue other than the
   reply queue.

Firing tests:

- SQLite and PostgreSQL: a PONG written to `T{tid}.ctrl_in` wakes the probe
  through the shared watcher wait and returns the matched payload;
- after a successful probe the reply queue name is absent from `list_queues()`;
- a timeout deletes the reply queue and returns the existing timeout result;
- a broker error deletes the reply queue and returns the existing error result;
- the minted TID creates no task-log, monitor, tid-mapping, or registry row, and
  does not appear in `weft task list`;
- an unmatched or malformed row on the reply queue does not satisfy the probe;
- the helper exposes no `reply_to` parameter, and every synchronous probe mints
  its own TID;
- **a control message sent to a task while that task is blocked in a probe is
  still handled by the task.** Drive a `TaskMonitor` probe, write STOP to
  `TaskMonitor`'s `ctrl_in` before the PONG arrives, and assert the probe does
  not consume it and `TaskMonitor` processes it on its next turn;
- the watcher is stopped on success, timeout, and error: assert no leaked queue
  operation or PostgreSQL listener registration after a probe, the borrowed
  broker remains usable, and connection counts do not accumulate across
  repeated probes;
- a late reply — the target writing its PONG after the requester timed out and
  cleaned up — leaves one row that the dead-task sweep later retires, and is
  never delivered to another requester;
- the probe starts no drive thread: assert structurally that the watcher's
  drive owner and reserved-drive slots stay unset for the probe's lifetime and
  that neither `run_in_thread()` nor `run_forever()` is called, rather than
  sampling `threading.active_count()`, which is racy under xdist. The
  connection and listener cleanup assertions above carry the rest of this
  guarantee; and
- an abandoned reply queue is selected by
  `select_runtime_dead_task_cleanup_candidates()` once past its minimum age, and
  is not selected while a probe is in flight.

### Slice 4: feed Manager probe handling from the control event

Write failing Manager tests, then:

1. Put the Manager's configured `ctrl_in` in every leadership and service PING.
2. Override `_handle_control_reply()` in Manager. Use the existing canonical
   matcher to match the PONG by exact target TID and request ID against pending
   leadership and service probes.
3. Store the parsed payload on the existing pending probe state.
4. Reuse the current PONG eligibility and service-candidate reduction. Change
   only where the parsed payload comes from.
5. Move the bounded control drain to the start of the Manager turn, before
   registration refresh, stale-reserved cleanup, leadership evaluation, or
   service convergence can resolve a probe.
6. Make stored PONG state bypass the ordinary cadence gate so it is reduced in
   the drain turn. Preserve existing child-launch and authority fences.
7. Remove target `ctrl_out` scans, exact-reply deletion, request-ID sweeps, and
   any private polling path from incremental Manager probes.
8. Remove pending `deadline_ns`, the 50 ms and 150 ms constants, and their
   `next_wait_timeout()` contributions.
9. Treat a pending service probe as an existing active-convergence reason, so
   it selects the one-second active cadence instead of the stable-audit cadence.
10. Within the current reconciliation scope, union
    `{probe.service_key for probe in self._service_probe_pending.values()}` into
    the existing `keys_needing_evidence` set. Do not use the pending map's
    composite keys as service keys.
11. If autostart is included and a known autostart probe carries a stored PONG,
    bypass the manifest-scan throttle for that pass and perform the current
    source scan before reduction. Do not pull autostart into a pass that already
    excludes it.
12. Anchor the leadership or service cadence when constructor bootstrap creates
   a probe. The first reactor turn may adopt and advance the probe but may not
   resolve it as no-PONG.
13. Resolve an unanswered prior-turn probe only on the next ordinary policy pass
   that already owns that liveness decision. Forced or reply-only passes do not
   manufacture absence or reset the policy clock.
14. At the end of a successful complete owning-policy scan, remove pending
    probes whose manager row or service candidate is no longer in that scan's
    source scope. Failed, throttled, or partial scans retain them.

Keep the state small: request ID, target TID, creation turn or equivalent
causal guard, and optional stored PONG. Do not add a second readiness cache,
reply index, drain-completeness flag, or PONG scheduler.

Firing tests:

- SQLite and PostgreSQL PONG arrival wakes the existing Manager watcher;
- exact leadership and service probes receive the PONG;
- wrong, ambiguous, late, and malformed PONGs are dropped;
- an already queued PONG is drained before registration, cleanup, leadership,
  and service policy work;
- stored PONG is reduced before a distant policy timer;
- no-PONG is decided only on the existing owning cadence;
- a pending service probe selects active cadence;
- a pending duplicate registry candidate remains in evidence after the
  duplicate-scan marker clears even when a live local candidate exists;
- a stored autostart PONG bypasses the manifest-scan throttle when autostart is
  included and reaches the existing reducer;
- a constructor-created probe survives the first reactor turn; and
- a complete scan retires vanished-source probes while a failed or partial scan
  retains them;
- reply-only work does not move policy clocks;
- active child launch preserves a stored leadership PONG until the existing
  fence clears;
- target `ctrl_out` is never scanned or swept; and
- pending probes add no `next_wait_timeout()` deadline.

### Slice 5: delete the old PONG discovery path

Search all production and test code for target-`ctrl_out` PONG matching, probe
reply sweeps, and PONG poll intervals. Every Weft-owned PING must use the wire
and requester-side handling from Slices 1-3. `send_keyed_ping_probe()` is
rewritten by Slice 3, not deleted; it remains the one client entry point.

Delete code whose only purpose was:

- polling target `ctrl_out` for PONG;
- periodically waking to perform that poll;
- deleting matched or expired PONG rows from target `ctrl_out`; or
- choosing between directed and default PONG destinations.

Do not replace it with a new abstraction. Retain shared payload validation if
it still has callers.

Firing test: an `rg`-backed architecture test or equally direct structural
assertion proves no production PING path scans target `ctrl_out`, omits
`reply_to`, or keeps a private sleep loop; and that the only broker wait on a
PING path is the shared `MultiQueueWatcher` one. Extend
`tests/specs/test_reactor_architecture.py` rather than adding a parallel gate.

### Slice 6: performance and full verification

Measure without turning observations into deadlines:

- quiet SQLite PING to requester-handler latency;
- quiet PostgreSQL PING to requester-handler latency;
- burst-mode latency;
- idle CPU for an otherwise idle Manager;
- the documented 100-row control backlog case; and
- **the submission probe path.** Note the probe is *not* on every submit:
  `observe_manager_availability()` returns early for a `ready` or `absent`
  outcome and only reaches `_probe_recovery_candidate()` for an **uncertain
  incumbent** record. A plain `weft run` against a healthy manager therefore
  sends no PING and would measure nothing. The benchmark must construct an
  uncertain-incumbent case that definitely probes — for example a registry
  record for a manager that is no longer answering — and measure that.

  Re-measure immediately after Slice 3, before Slice 4 begins, against the
  preflight baseline, on both backends.

  What is measured, and what fails. A transient nonpersistent
  `MultiQueueWatcher` and PostgreSQL listener registration are **inherent to
  the chosen design**, so their existence is not a regression — the gate
  measures their bound and their release, not their presence:

  1. **Peak per active probe** — connections and listener
     registrations held while one probe is in flight. Record the number; an
     unexpected multiple (a second lease, a second listener) fails.
  2. **Teardown to baseline** — after the probe returns, counts return to the
     pre-probe values. Any residue fails; this is the `watcher.stop()`
     guarantee.
  3. **No accumulation** — run N probes in sequence and assert the counts are
     flat, not growing with N. This is the churn failure mode and it fails
     outright.
  4. **Wall-clock** — compared to the preflight baseline for owner review, not
     an automatic failure.

  A failure of 1-3 is a **stop-and-reconsider for the whole plan**, not a
  partial landing: `reply_to` is a required field and the responder writes only
  to it, so a caller left on the old route would emit PINGs the codec rejects.
  That is also why Rollback treats this as one unit.

The existing backlog measurement is context, not a target:

| Backend | PING position | Active turns | Median | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| SQLite | 90 of 100 | 3 | 14.219 ms | 15.229 ms | 17.561 ms |
| PostgreSQL 18 | 90 of 100 | 3 | 202.513 ms | 271.983 ms | 358.993 ms |

That benchmark starts with the first active reactor turn and excludes initial
listener wake and queue prefill. It shows the 32-row batch remains reasonable;
it does not define a PONG timeout.

Run focused tests first, then the repository gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_control_messages.py tests/core/test_control_probe.py tests/core/test_manager.py tests/tasks/test_control_channel.py tests/tasks/test_task_execution.py tests/tasks/test_pipeline_runtime.py -q
./bin/pytest-pg tests/core/test_manager.py tests/tasks/test_control_channel.py -q
./.venv/bin/python -m pytest
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py -q
bin/check-doc-paths
git diff --check
```

## Review and Completion Gates

- Independent plan review must confirm that the plan describes one PING path
  and no fallback route, no dynamic watcher membership, no target-`ctrl_out`
  scan, and no PONG-specific deadline. A synchronous probe's own ephemeral
  reply queue is expected; a reply queue owned by anyone other than its sole
  reader is not.
- Independent plan review must confirm no synchronous probe drains a queue it
  does not own, and that `send_keyed_ping_probe()` accepts no caller-supplied
  `reply_to`.
- Implementation review must trace one real SQLite and one real PostgreSQL
  PING from request write through PONG handling.
- The final diff must keep PING-specific code limited to request recognition,
  response routing, reply recognition, and existing result handling.
- Spec, plan, code docstrings, implementation mappings, and tests must agree.
- The work remains uncommitted for user review unless the user asks for a
  commit.

## Rollback

This is one contract change and must roll back as one unit: wire grammar,
responder route, requester handler, Manager probe state, specs, and tests. Do
not restore only the target-`ctrl_out` poller or retain dual routing.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| `00-Quick_Reference.md`; [MANAGER.15] | The reviewed table named six direct governing specs. | Promotion also synchronizes the quick-reference wire shape and the service-cadence invariant, and revises the superseded deadline lesson in place. | Independent promotion review found that leaving these current documents unchanged would make the corpus contradict the approved contract. | Added to the promoted source-spec set before implementation. |
| [CC-2.1], [SB-0.4] | The ephemeral requester waits through `MultiQueueWatcher` with `persistent=False`. | On SQLite only, the watcher confirms pending state after each shared fallback strategy turn because a fresh connection cannot retain connection-local `PRAGMA data_version` state. Persistent SQLite task reactors still use `data_version`; PostgreSQL still uses the native waiter. | The performance benchmark reproduced missed SQLite wake hints that delayed completed PONGs until the caller's two-second timeout. Keeping the confirmation inside `MultiQueueWatcher` preserves the one-reactor design and the normal 100 ms fallback bound without adding a probe-owned polling loop. | No normative delta. This is the existing watcher's fallback-readiness guarantee for a transient queue facade. |

## Decision Log

| Date | Decision |
| --- | --- |
| 2026-09-18 | PONG always goes to the requester's named `ctrl_in`. Superseded 2026-09-19 on the "no temporary reply queue" clause: a synchronous probe owns an ephemeral `T{tid}.ctrl_in` it alone reads. The intent that clause carried — no shared or third-party reply queue — still holds. |
| 2026-09-18 | Wakeup comes from the requester's own watcher and never from dynamic topology. Refined 2026-09-19: a reactor-integrated requester uses its existing watcher; a synchronous probe uses the temporary manual-wait watcher it owns. No watcher membership changes either way. |
| 2026-09-18 | One PING route replaces target-`ctrl_out` polling and fallback behavior. |
| 2026-09-18 | PONG absence uses existing reactor policy timing; 50 ms and 150 ms probe deadlines are removed. |
| 2026-09-18 | The existing 32-row control batch is retained; overload behavior is documented without new scheduling state. |
| 2026-09-19 | A synchronous probe uses an ephemeral control requester: mint a TID, own `T{tid}.ctrl_in`, drive the shared watcher wait, consume the PONG, delete the queue. Rejected alternatives: a client-only `ctrl_out` fallback (two routes), a reply queue namespaced under the target (second queue kind, and a shared queue forces peek), deleting the client PING producers (loses `weft task ping` and the manager-liveness check), and routing client probes through the Manager (circular for manager-liveness probes). |
| 2026-09-19 | The ephemeral requester uses `MultiQueueWatcher`, not `QueueChangeMonitor`. It is the sole reader of its reply queue, so `has_pending()` self-clears and the shared wait cannot spin; `QueueChangeMonitor` would add a thread and a connection per probe on SQLite, which has no native multi-queue waiter. |
| 2026-09-19 | Orphan reply queues are retired by the existing name-derived dead-task sweep; no new mechanism. Verified: TaskMonitor scans `T*.ctrl_in`, its selector owns families with no Monitor collation row, and sweep age derives from the TID itself. Because that age is TID-derived rather than activity-derived, a probe must not outlive it, which is why the synchronous timeout is bounded at half the cleanup age. This settles the previously open unknown-owner retirement bound. |
| 2026-09-19 | Cleanup is explicit rather than implied by consumption. `list_queues()` derives names from message rows, so there is no registered queue object, but a consumed row stays physically present until vacuum and keeps the name listed until then. The requester therefore stops its watcher and best-effort deletes remaining rows on every exit path; this also covers rows the matcher rejected. |
| 2026-09-19 | The ephemeral requester uses `MultiQueueWatcher` in manual-wait mode rather than SimpleBroker primitives directly. Checked: SimpleBroker's public watcher surface is run-loop-plus-handler (`start`, `run`, `run_forever`, `run_in_thread`, `stop`) with no synchronous bounded wait, so `QueueWatcher` would need a thread and an Event per probe — the `QueueChangeMonitor` shape, on the submission hot path — while `Queue` + `PollingStrategy` raw would hand-roll the hint-then-check loop and re-create the drift the reactor-restoration plan just consolidated. `MultiQueueWatcher.wait_for_activity(timeout)` is the only API in either library giving a synchronous caller a bounded wait on its own thread over the one shared event source. Accepted costs: a vestigial no-op handler, and a large class used for a one-queue probe. |
| 2026-09-19 | Layering checked, no violation: the rule forbids `core -> commands/cli/client`, and `commands -> core` is the permitted direction that `weft/commands/tasks.py` already uses. `send_keyed_ping_probe()` also lives in `weft/core/control_probe.py`, so the rewrite is core-to-core and crosses no boundary. |
| 2026-09-19 | The synchronous watcher uses `persistent=False`. A default persistent watcher shares SimpleBroker's process-session key with an outer borrowed broker operation and cannot close its `BrokerSession` while that operation is open. Nonpersistent mode preserves the same `MultiQueueWatcher.wait_for_activity()` path and PostgreSQL native listener while giving the bounded probe independent cleanup. |
| 2026-09-19 | A nonpersistent SQLite watcher confirms pending state after each shared strategy turn. `PRAGMA data_version` is connection-local and a nonpersistent queue opens a fresh connection per operation, so treating it as a retained change detector caused completed PONGs to wait until the caller deadline. The confirmation stays in `MultiQueueWatcher`; probe code still owns no sleep or polling loop. |

## Execution Log

- Preflight baseline (SQLite): live keyed PING/PONG responder, 12 probes:
  median 60.436 ms, p95 62.462 ms, maximum 109.884 ms. The existing borrowed
  manager-probe connection test passed and proved zero additional connection or
  listener ownership on the borrowed path.
- Preflight baseline (PostgreSQL 18): live keyed PING/PONG responder, 12 probes:
  median 84.286 ms, p95 89.566 ms, maximum 92.740 ms. The existing borrowed
  manager-probe connection test passed and proved zero additional connection or
  listener ownership on the borrowed path.
- Spec-promotion baseline: diff base
  `a872826b847a4bdd9d8c129639fb81d847fe4649` plus seven-spec worktree diff
  SHA-256 `713bbf3828d9d3812bb4b842c2a4f0da1266e266fe97ba9139a7eb759ddcdaa8`.
- Post-Slice-3 synchronous probe measurement, 20 measured samples after three
  warmups against a live keyed responder:

  | Backend | Preflight median / p95 / max | Event-routed median / p95 / max |
  | --- | --- | --- |
  | SQLite | 60.436 / 62.462 / 109.884 ms | 113.681 / 174.387 / 174.807 ms |
  | PostgreSQL 18 | 84.286 / 89.566 / 92.740 ms | 146.793 / 151.811 / 162.122 ms |

  The added wall time is transient requester setup and teardown. It is confined
  to synchronous probes; a healthy submission does not probe. The first SQLite
  measurement exposed completed replies delayed to the two-second caller
  deadline because nonpersistent queue operations could not retain
  connection-local `data_version`. The watcher fallback correction removed
  every two-second miss in the repeat run.
- A separate instrumented PostgreSQL probe attributed the approximately 160 ms
  total to TID minting (20–33 ms), watcher construction (11 ms), PING write
  (13–21 ms), wait plus reply read (30–35 ms), watcher stop (48–56 ms), and
  reply-queue deletion (18–26 ms). The message round trip is therefore about
  one fifth of the synchronous path; ephemeral requester ownership and cleanup
  dominate the rest. A process-owned requester could amortize that setup, but
  would add shared lifecycle, concurrent reply demultiplexing, fork handling,
  and process-exit cleanup. Keep the current per-probe ownership while this is
  a CLI and recovery path rather than a healthy-submission hot path; revisit
  only if synchronous probes become frequent.
- Uncertain-incumbent submission measurement, using
  `observe_manager_availability()` with a registry record that necessarily
  reached `_probe_recovery_candidate()`, 20 measured samples after three
  warmups: SQLite median 163.691 ms, p95 280.069 ms, maximum 282.434 ms;
  PostgreSQL 18 median 169.063 ms, p95 266.721 ms, maximum 267.394 ms.
- Long-lived Manager wake, burst, idle, and backlog measurements remain those
  recorded by the immediately preceding reactor-restoration slice because this
  change does not alter their watcher or scheduling mechanics. Quiet PING reply
  observation was 20.23 ms on SQLite and 52.84 ms on PostgreSQL; subsequent
  active replies were 2.01–22.40 ms and 4.04–8.94 ms respectively. Idle
  Manager CPU was 1.01–1.29% of one core on SQLite and 0.72–0.74% on
  PostgreSQL. The 100-row backlog at position 90 remained three active turns:
  SQLite median 14.219 ms, p95 15.229 ms, maximum 17.561 ms; PostgreSQL 18
  median 202.513 ms, p95 271.983 ms, maximum 358.993 ms.
- Probe-owned resource inventory is bounded and returns to baseline. SQLite
  has zero listener registrations and retains zero probe connections at rest
  or after return; at peak it owns one operation-local connection. PostgreSQL
  has zero probe listener registrations at rest and after return; at peak it
  owns one shared listener registration with one listener connection and at
  most one operation-local connection. Three sequential borrowed-probe tests
  on each backend left no open probe connection, no requester queue, and the
  caller-owned broker remained usable. There was no growth with N.
- Slice reviews found and corrected five implementation defects: stale target
  `ctrl_out` assumptions in tests and evidence labels; cleanup errors replacing
  a successful synchronous result; incomplete autostart scans pruning pending
  probes; complete absent scans retaining unanswered probes; and failed
  service-registry scans dropping retained probes from reducer evidence and
  permitting a duplicate singleton request. Firing tests cover each
  correction. The performance pass also found and corrected the transient
  SQLite `data_version` issue above. The final structural pass removed
  PipelineTask's redundant PING response branch and added a gate that keeps
  BaseTask as the sole task-side responder owner.
- A PostgreSQL follow-up exposed three heartbeat tests that required at least
  900 ms to remain on a one-second monotonic deadline after a real reactor
  turn. A roughly 170 ms PostgreSQL turn correctly left about 830 ms. The tests
  now share the actual contract assertion: the remainder is positive and no
  greater than the audit interval. The full heartbeat file passes on SQLite
  and PostgreSQL after the correction.
- Completion commands: SQLite full suite, 5,182 passed and 28 skipped;
  PostgreSQL 18 `./bin/pytest-pg --fast`, 5,033 passed and 13 skipped; mypy,
  Ruff, plan metadata/spec hygiene, `bin/check-doc-paths`, and
  `git diff --check` passed.
- Independent final review: PASS. The reviewer traced failed service-registry
  scans with both unanswered and stored-PONG probes on SQLite and PostgreSQL.
  Unanswered probes remained uncertain and blocked replacement; stored PONGs
  became live evidence, retired only the resolved probe, and blocked
  replacement.
