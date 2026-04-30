# Known-TID Terminal Snapshot API Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Add a bounded direct lookup API for callers that already know a task TID and
need to reconcile terminal state without replaying the global task log per
task. The new path should read task-local surfaces first, preserve
`weft.log.tasks` as the durable audit and fallback surface, and let
`weft-django` map `status(tid)` onto the direct known-TID API for normal
Django reconciliation.

Also remove the existing hidden slow path from known-TID diagnostic snapshots.
When a caller supplies a full 19-digit TID, `Task.snapshot()`,
`client.tasks.status(tid)`, and the command-layer `task_snapshot()` /
`task_status()` path must not replay `weft.log.tasks` from the beginning.
They may use a bounded task-log replay starting at `int(tid) - 1`, and they may
fall back to the legacy full replay for short or ambiguous identifiers.

Also remove the N+1 replay from task listing and task stats. Whole-project
status/list operations may perform one global task-log replay because the
caller asked for a global view. They must not perform one additional global
task-log replay per task just to recover a TaskSpec payload that was already
seen during the first replay.

This plan intentionally separates two questions:

- "What is the authoritative audit history for this task?" remains
  `weft.log.tasks`.
- "Can a caller with a known TID cheaply prove terminal or live state?" should
  use task-local queues, runtime mapping, and bounded fallback only.
- "Can a caller with a known TID get the existing full diagnostic snapshot?"
  should use the same bounded known-TID rule, while keeping broad project
  summaries on their current one-replay global status path.

## 2. Source Documents

Source specs:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3], [MF-5],
  [MF-6]
- `docs/specifications/07-System_Invariants.md` [QUEUE.1], [QUEUE.2],
  [OBS.1]
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2]
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-1], [DJ-2.1],
  [DJ-2.2], [DJ-8.3]

Guidance:

- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/testing-patterns.md`

Related plans:

- `docs/plans/2026-04-20-weft-client-pythonic-surface-and-path-unification-plan.md`
  introduced the public `weft.client` shape.
- `docs/plans/2026-04-21-client-follow-hardening-plan.md` hardened client
  follow/result behavior around terminal observations.
- `docs/plans/2026-04-21-weft-client-and-django-first-class-hardening-plan.md`
  made `weft-django` depend on the public client boundary.

The current specs do not yet define a direct known-TID terminal snapshot or
terminal ctrl-out envelope for ordinary one-shot tasks. This plan therefore
requires spec updates before the implementation is considered done.

## 3. Context And Key Files

Files to modify:

- `weft/commands/types.py`
- `weft/commands/result.py`
- `weft/commands/tasks.py`
- `weft/commands/_streaming.py`
- `weft/commands/system.py`
- `weft/core/tasks/base.py`
- `weft/core/tasks/consumer.py`
- `weft/core/manager.py`
- `weft/client/_task.py`
- `weft/client/_namespaces.py`
- `weft/client/_types.py`
- `weft/client/__init__.py`
- `integrations/weft_django/weft_django/client.py`
- `integrations/weft_django/weft_django/__init__.py`
- `integrations/weft_django/README.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/13C-Using_Weft_With_Django.md`
- targeted tests listed below

Read first:

- `weft/commands/result.py`, especially `_await_result_materialization()`,
  `_await_single_result()`, and `await_task_result()`
- `weft/commands/tasks.py`, especially `task_snapshot()`, `task_status()`,
  `_ctrl_out_for_tid()`, and `mapping_for_tid()`
- `weft/commands/system.py`, especially `_collect_task_snapshots()` and
  `_effective_public_status()`
- `weft/core/tasks/base.py`, especially `_send_control_response()` and
  `_report_state_change()`
- `weft/core/tasks/consumer.py`, especially `_ensure_outcome_ok()`,
  `_finalize_terminal_outcome()`, and `_finalize_message()`
- `weft/core/runners/host.py` and
  `weft/core/runners/subprocess_runner.py`, especially how return codes,
  timeouts, cancellations, and no-result worker exits become `RunnerOutcome`
- `integrations/weft_django/weft_django/client.py`, especially
  `DjangoWeftClient.status()`, module-level `status()`, and
  `WeftSubmission.status()`

Current structure:

- `weft_django.status(tid)` calls `get_core_client().task(tid).snapshot()`.
- `Task.snapshot()` calls `weft.commands.tasks.task_snapshot()`.
- `task_snapshot()` calls `task_status()`, which calls
  `system._collect_task_snapshots()` with a TID filter.
- `_collect_task_snapshots()` still replays `weft.log.tasks` from the
  beginning, then applies the TID filter.
- `client.tasks.status(tid)` uses the same `task_snapshot()` path.
- `client.tasks.list()` calls `tasks.list_task_snapshots()`, which first
  reconstructs task snapshots from the global log and then calls
  `load_latest_taskspec_payload(ctx, snapshot.tid)` once per returned task.
  That helper scans `weft.log.tasks` again from each TID. This is the N+1
  replay to remove.
- `client.tasks.stats()` currently builds on `list_task_snapshots()`, so it
  inherits that N+1 replay even though it only needs status counts.
- `weft result TID` already uses task-local outbox and ctrl-out surfaces, but
  it consumes outbox messages and still uses log events for completion
  boundaries.
- `ctrl_out` currently carries multiple payload families: control responses,
  stderr stream chunks, interactive terminal envelopes, and other task-local
  replies. It is not safe to treat every ctrl-out message as terminal.

Comprehension checkpoints:

- Which current public path replays `weft.log.tasks` even when the caller has a
  full TID?
- Which current public path performs one global replay and then a replay per
  task?
- Which queue is task public output, and which queue is task-local control and
  terminal observation?
- Which current result helper consumes outbox messages and must not be reused
  as a non-consuming status probe without changes?

## 4. Invariants And Constraints

Preserve these invariants:

- TID format and immutability do not change.
- State transitions remain forward-only.
- Resolved `TaskSpec.spec` and `TaskSpec.io` stay immutable.
- Queue names stay stable: `T{tid}.outbox`, `T{tid}.ctrl_out`, and current
  custom IO names remain valid.
- `weft.log.tasks` remains the durable lifecycle audit trail. The direct API is
  a fast known-TID probe, not a competing lifecycle database.
- A full 19-digit known-TID status or snapshot lookup must not replay
  `weft.log.tasks` from the beginning in the normal path.
- A broad project status/list view may replay `weft.log.tasks` once. It must
  not replay it once per task.
- The new read API must use non-consuming reads by default.
- Any consuming acknowledgement must delete exact messages only, never clear an
  entire queue.
- Do not treat arbitrary `ctrl_out` messages as terminal. Only typed terminal
  envelopes count.
- Do not write supervisor-generated failure into `outbox` unless a future spec
  defines an explicit public result envelope for supervisor output. In this
  plan, outbox remains task result output.
- Do not make Django own task lifecycle truth. Django may own app-domain rows
  keyed by `weft_tid`.
- Do not make `weft_django` import `weft.commands.*` or `weft.core.*`; it must
  use `weft.client`.

Error-priority rules:

- Failure to write the terminal ctrl-out envelope is observability loss, not a
  reason to change a correctly published terminal task-log state.
- Failure to acknowledge a terminal/result message after app state commits
  should leave the message visible for idempotent reconciliation.
- If direct queues and runtime mapping cannot prove terminal or live state, the
  API returns `unknown` or `pending` until the configured stale boundary passes.
  It must not immediately convert absence of liveness into failure.
- Absence of liveness may become `failed` only when there is positive proof
  that the task had previously entered a live or spawn-owned state. Acceptable
  proof sources are defined in the direct lookup algorithm below.

Rollback:

- New terminal envelopes are additive. Old readers ignore them.
- `weft_django.status(tid)` mapping can be reverted to `Task.snapshot()` if the
  direct API causes downstream issues.
- The exact-message acknowledgement helper must be optional. Rolling it back
  must not strand correctness because retention cleanup can still be handled by
  later `tidy` work or manual queue operations.

Out of scope:

- Replacing project-wide `weft status`; project summaries may still replay the
  global log once.
- Adding a durable status index, TaskSpec index, cache queue, cache table, or
  background compactor. This slice removes hidden replays without adding a
  second lifecycle state store.
- Replacing `weft result`; result consumption and streaming remain separate.
- A Django result backend or task state table inside `weft-django`.
- Broad queue-retention policy or automatic background cleanup.
- New dependencies.

Stop and re-plan if:

- the implementation starts scanning `weft.log.tasks` from the beginning in the
  new known-TID API
- `Task.snapshot()` or `client.tasks.status(full_tid)` still routes through a
  full global replay after the bounded known-TID helper exists
- `client.tasks.list()` or `client.tasks.stats()` still calls
  `load_latest_taskspec_payload()` once per task
- the new API consumes outbox or ctrl-out messages by default
- a snapshot helper deletes messages directly instead of returning an exact
  acknowledgement target for a later explicit ack
- the implementation adds a second lifecycle state store
- `weft_django` starts importing command or core modules directly
- terminal envelope cleanup becomes bulk queue deletion

Engineering standards for this slice:

- Prefer red-green TDD. Each task below names the smallest failing test to
  write first. Do not implement the production code first unless the task says
  why a direct red test is impractical.
- Keep changes DRY by reusing existing result decoding, TID normalization,
  queue helpers, runtime liveness helpers, and client wrapper patterns. Do not
  create a second result decoder, a second TID resolver, or a second Django
  client stack.
- Apply YAGNI. Do not add retention policy, background cleanup jobs, new CLI
  commands, custom queue-name indexing, or broad status redesign unless the
  task explicitly calls for it.
- Keep tests contract-focused and broker-backed where queue semantics matter.
  Mock only narrow seams that prove "this expensive path was not called" or
  isolate the Django wrapper from core behavior.
- Do not test queue semantics with fake queue classes when a real
  SimpleBroker-backed queue is practical. Fakes are acceptable only to
  instrument a narrow call such as `since_timestamp` forwarding.
- Prefer small commits/slices in implementation order. A developer should be
  able to stop after any numbered task with tests passing for that slice.

Code style reminders:

- Follow the existing import style: `from __future__ import annotations`,
  stdlib imports first, third-party imports next, local imports last.
- Use `collections.abc` for abstract collection types.
- Use modern typing (`str | None`, `list[str]`, `dict[str, Any]`), not
  `Optional`, `List`, or `Dict`.
- Put new constants in `weft/_constants.py` only if they are true shared
  constants. Do not create constants for one-off test values.
- Keep helper names boring and precise. Prefer `_reduce_task_log_event()` over
  clever abstraction names.
- Add docstrings only where a helper owns a spec or performance boundary.
  Include `Spec:` references when the helper implements a documented contract.
- Do not add a new dependency.
- Do not use late imports to dodge a design problem. If an import cycle
  appears, re-check the layer boundary.

## 5. Proposed API

Core public dataclasses:

Add a public dataclass in `weft/commands/types.py`, re-exported through
`weft.client`, tentatively:

```python
@dataclass(frozen=True, slots=True)
class QueueAckTarget:
    queue: str
    message_id: int


@dataclass(frozen=True, slots=True)
class TaskTerminalSnapshot:
    tid: str
    status: str
    source: str
    value: Any | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
    return_code: int | None = None
    terminal: bool = False
    ack_targets: tuple[QueueAckTarget, ...] = ()
    observed_at: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

Status values:

- `completed`: proven final result from outbox or terminal envelope.
- `failed`, `timeout`, `cancelled`, `killed`: proven terminal failure state.
- `running`: positive liveness proof exists.
- `pending`: spawn/task surfaces may still be materializing and the stale
  boundary has not elapsed.
- `unknown`: caller requested no wait and no direct proof exists.
- `missing`: TID is malformed or impossible after normalization.

Source values:

- `outbox`: final public result was visible in the task outbox.
- `ctrl_out`: typed terminal envelope was visible in task ctrl-out.
- `runtime`: positive live runtime or task-process mapping.
- `observer`: stale no-liveness inference from the direct known-TID probe.
- `log_fallback`: bounded task-log fallback proved state.

Public client methods:

- `Task.terminal_snapshot(timeout=0.0) -> TaskTerminalSnapshot`
- `client.tasks.terminal_snapshot(tid, timeout=0.0)`
- Exact ack helper: `client.tasks.ack_terminal_snapshot(snapshot) -> bool`

Snapshot helpers are observation-only. They must never delete queue messages.
The ack helper is valid only when `snapshot.ack_targets` is non-empty. It must
delete only those exact queue/message-id pairs.

Ack target rules:

- A ctrl-out terminal envelope produces one ack target.
- A one-shot outbox result that fits in one final message produces one ack
  target.
- A streamed or multi-message outbox result may produce multiple ack targets
  only if the parser can identify the exact complete final-result message set
  without consuming. If it cannot, the snapshot should return no outbox ack
  targets and leave retention to explicit result consumption or later cleanup.
- Persistent task output, stream chunks, and partial output must not produce
  outbox ack targets for this terminal API. They are not terminal proof.
- `ack_terminal_snapshot()` should return `False` when no ack targets exist.
  It should not raise for an unackable snapshot unless the caller passes a
  malformed object.

`Task.result(timeout=None)` keeps its current semantics and may consume output.
The new snapshot API must be documented as non-consuming by default.

Existing diagnostic snapshot APIs:

- `Task.snapshot()` keeps returning `TaskSnapshot | None`.
- `client.tasks.status(tid)` keeps returning `TaskSnapshot | None`.
- For a full 19-digit TID, both must use the new bounded known-TID diagnostic
  snapshot helper, not `_collect_task_snapshots(..., tid_filters=...)` over an
  unbounded log replay.
- For short IDs and ambiguous IDs, the existing TID mapping and broad replay
  fallback may remain. Do not break short-TID ergonomics while optimizing the
  full-TID path.

Existing list/stats APIs:

- `client.tasks.list()` keeps returning `list[TaskSnapshot]`.
- `client.tasks.stats()` keeps returning `dict[str, int]`.
- Both must avoid per-task calls to `load_latest_taskspec_payload()` after a
  broad status replay. Reuse the TaskSpec payload already collected while
  reducing the global log.

`weft-django` mapping:

- `weft_django.status(tid)` should call the new core terminal snapshot API and
  return `TaskTerminalSnapshot | None`.
- `DjangoWeftClient.status(tid)` should do the same.
- `WeftSubmission.status()` can keep returning `str | None` by returning
  `submission.terminal_snapshot().status`, preserving its current ergonomic
  shape.
- Add `weft_django.terminal_snapshot(tid, timeout=0.0)` as an explicit alias
  for callers that want the new name rather than `status()`.
- Add `weft_django.snapshot(tid)` and `DjangoWeftClient.snapshot(tid)` in this
  slice for full diagnostic `TaskSnapshot | None` behavior. This preserves a
  documented path for callers that used fields beyond `.status` on the old
  `weft_django.status()` return value.
- Document the compatibility break plainly: module-level
  `weft_django.status(tid)` keeps `.status` compatibility but no longer
  promises full `TaskSnapshot` fields such as `name`, `metadata`, `runtime`,
  `started_at`, or `completed_at`. Callers needing those fields must use
  `weft_django.snapshot(tid)`.

## 6. Terminal Envelope Shape

Add a typed terminal envelope written to `ctrl_out` for ordinary terminal paths:

```json
{
  "type": "terminal",
  "source": "task",
  "tid": "177...",
  "status": "failed",
  "error": "Command exited with -9",
  "return_code": -9,
  "timestamp": 1770000000000000000
}
```

For manager-observed task-wrapper death, implement this in the same slice:

```json
{
  "type": "terminal",
  "source": "manager",
  "tid": "177...",
  "status": "failed",
  "error": "Task wrapper exited before publishing terminal state",
  "timestamp": 1770000000000000000
}
```

Rules:

- The envelope is additive and best effort.
- It does not replace `weft.log.tasks`.
- It does not go to outbox.
- Task-authored terminal envelopes are the only ctrl-out messages that can
  directly encode the task's own terminal state.
- Manager-authored terminal envelopes encode supervisor observation, not task
  result output. They are a failure observation surface for case 4, not a
  replacement for the task-owned terminal path.
- Existing `ctrl_out` control replies and stream chunks stay valid and must be
  ignored by terminal snapshot parsing unless `type == "terminal"`.
- For successful one-shot tasks, outbox remains the primary fast path. A
  success terminal ctrl-out envelope is optional; if added, it must not be the
  only success signal.
- Manager-authored terminal envelopes are only for wrapper/process death that
  the manager observes after the child task process exits without publishing a
  terminal task-owned envelope. The manager must not overwrite a task-owned
  terminal envelope or task-log terminal state.
- Before writing a manager-authored envelope, the manager must perform bounded
  direct inspection for that TID. If task-owned terminal ctrl-out, final outbox
  terminal proof, or a task-log terminal state is already visible, the manager
  must not write a duplicate manager terminal envelope.
- If manager inspection cannot determine task-owned terminal state because the
  broker is temporarily unavailable, prefer not writing the manager envelope
  over writing a duplicate or contradictory terminal observation. The normal
  task-log and later direct lookup fallback remain available.

## 7. Direct Lookup Algorithm

Implement one shared command-layer helper, tentatively
`weft.commands.tasks.task_terminal_snapshot(context, tid, timeout=0.0)`.

Algorithm:

1. Normalize full TID with the existing public normalization rules. Reject empty
   or non-numeric TIDs as `missing` or raise `ValueError` consistently with the
   client layer.
2. Resolve task IO names with a bounded TaskSpec lookup:
   - For full 19-digit TIDs, scan `weft.log.tasks` only with
     `since_timestamp=int(tid) - 1`.
   - If a TaskSpec-bearing event is found, use its declared
     `io.outputs.outbox` and `io.control.ctrl_out`.
   - If no TaskSpec-bearing event is visible yet, use canonical
     `T{tid}.outbox` and `T{tid}.ctrl_out`.
   - Never perform an unbounded task-log replay just to find custom queue
     names in the known-TID path.
3. Peek outbox non-consumingly.
   - Decode final one-shot result messages using existing result decoding
     helpers.
   - A visible outbox value means `status="completed"` only when the helper
     can prove it is the final one-shot public result for a non-persistent
     task, or when a separate terminal success boundary is also proven.
   - If the TaskSpec is visible and marks the task persistent, outbox data is
     never terminal proof for this API.
   - For persistent tasks, stream chunks, or partial output, visible outbox
     data means result/output is available but does not by itself prove task
     terminal state. Return `running`, `pending`, or a non-terminal status as
     appropriate unless a terminal boundary is also visible.
   - Include exact ack targets only for final result messages whose complete
     message set is known without consuming.
4. Peek ctrl-out non-consumingly.
   - Iterate messages with timestamps.
   - Only consider JSON objects with `type == "terminal"`.
   - Return the latest terminal envelope for the requested TID.
   - Include exact queue/message id for explicit ack.
5. Check runtime liveness using `weft.state.tid_mappings` for that TID.
   - Positive host PID or runner-native liveness returns `running`,
     `source="runtime"`.
   - Use generator-based mapping reads; do not fixed-limit correctness reads.
6. If no direct proof exists and timeout remains, wait on outbox, ctrl-out, and
   TID-mapping queue changes until timeout expires.
7. After the stale boundary only, infer `failed`, `source="observer"` when both
   conditions are true:
   - no terminal outbox or terminal ctrl-out proof exists
   - there is positive prior-live proof for the task

   Acceptable prior-live proof:
   - a bounded task-log event for this TID with status `spawning` or `running`
   - a TID mapping record for this TID that contains a runtime handle or host
     PID observation
   - a manager-owned spawn event for this child TID

   If none of those proofs exist, return `unknown` or `pending`; do not infer
   failure from absence alone.
8. As a bounded fallback only, inspect `weft.log.tasks` with
   `since_timestamp=int(tid) - 1` and stop once the requested TID has a terminal
   event. Never scan from the beginning in the normal known-TID path. If the
   bounded fallback cannot prove state, return `unknown` or `pending`.

Custom queue-name rule:

- Supporting custom outbox and ctrl-out names is required for full 19-digit
  known-TID lookup when the TaskSpec-bearing event is visible in the bounded
  `since_timestamp=int(tid)-1` window.
- Before the TaskSpec-bearing event appears, the helper may probe canonical
  `T{tid}.*` names only and return `unknown` or `pending` if no direct proof is
  present.

## 7.1 Bounded Diagnostic Snapshot Algorithm

Implement one command/system-layer helper for the existing diagnostic snapshot
path, tentatively `weft.commands.system.collect_known_tid_snapshot(ctx, tid,
include_terminal=True)`.

This helper is different from `task_terminal_snapshot()`:

- `task_terminal_snapshot()` is a compact reconciliation/status probe.
- `collect_known_tid_snapshot()` preserves the existing richer `TaskSnapshot`
  fields for `Task.snapshot()` and `client.tasks.status(tid)`.

Algorithm:

1. Accept only a normalized full 19-digit TID. If the caller passes a short ID,
   resolve it before calling this helper or use the existing broad fallback.
2. Read latest TID mapping for this TID using the existing mapping helpers.
   Generator-based mapping reads are allowed. Fixed `peek_many(limit=...)`
   correctness reads are not.
3. Read manager records as `_collect_task_snapshots()` does today, because
   manager liveness affects public status for manager tasks. Do not change
   manager election or registry semantics.
4. Replay `weft.log.tasks` with `since_timestamp=int(tid) - 1`.
   The helper must never call `_iter_log_events(log_queue)` without a bounded
   `since_timestamp` for a full TID.
5. Reduce only events whose `payload["tid"] == tid`.
   Reuse the same status reduction rules as `_collect_task_snapshots()`:
   task activity events should update activity/waiting fields until terminal;
   TaskSpec-bearing events should update name, status, timestamps, metadata,
   and runtime payload; terminal states remain terminal.
6. Build the public status with the existing helpers:
   `_merge_runtime_entry()`, `_runtime_handle_from_mapping()`,
   `_runner_name_for_snapshot()`, `_describe_runtime_handle()`, and
   `_effective_public_status()`. Do not fork runner liveness policy.
7. Return `None` if no TaskSpec-bearing event is found for the TID.
8. `tasks.task_status()` should use this helper for full 19-digit TIDs. It may
   retain the old `_collect_task_snapshots(..., tid_filters=...)` path for
   short IDs or unresolved aliases.

DRY rule:

- If the implementation copies most of `_collect_task_snapshots()` into a new
  helper, stop. Extract the shared "reduce one event into one record" and
  "build TaskSnapshot from reduced record" pieces instead. The bounded helper
  and the global helper should share reduction/building logic.

YAGNI rule:

- Do not add a persistent status index, cache, SQLite table, or background
  compactor in this slice. The bounded known-TID helper is enough.

## 7.2 Batch Snapshot Materialization Algorithm

Fix the list/stats N+1 replay by changing the internal collection boundary.

Preferred shape:

```python
@dataclass(frozen=True, slots=True)
class CollectedTaskSnapshot:
    snapshot: TaskSnapshot
    taskspec_payload: dict[str, Any] | None
```

Implementation rules:

1. Add an internal collection helper such as
   `_collect_task_snapshot_records(ctx, include_terminal, tid_filters)`.
2. This helper should perform the same single global log replay as
   `_collect_task_snapshots()` does today.
3. While reducing events, preserve the latest TaskSpec payload already stored
   in the reduced record.
4. Return `CollectedTaskSnapshot` records containing both the built snapshot and
   its latest TaskSpec payload.
5. Keep `_collect_task_snapshots()` as a compatibility wrapper that returns
   `[record.snapshot for record in _collect_task_snapshot_records(...)]`.
6. Update `weft.commands.tasks.list_task_snapshots()` to use records and pass
   `record.taskspec_payload` to `_public_snapshot()` instead of calling
   `load_latest_taskspec_payload(ctx, snapshot.tid)` per task.
7. Update `task_stats()` so it counts statuses without materializing full
   public snapshots when it does not need TaskSpec-derived fields. It may call
   `list_tasks()` or the new record helper directly; it must not call
   `list_task_snapshots()` if that causes extra TaskSpec work.

Boundary:

- Project-wide status may still do one replay of `weft.log.tasks`.
- Project-wide task list may still do one replay of `weft.log.tasks`.
- Neither path may replay `weft.log.tasks` once per task.

## 8. Tasks

1. Update specs for the new direct known-TID contract.
   - Files:
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/13C-Using_Weft_With_Django.md`
   - Add the terminal ctrl-out envelope contract and the direct known-TID
     snapshot API.
   - State that `weft.log.tasks` remains audit and fallback, not normal
     known-TID reconciliation.
   - Add a backlink to this plan.
   - Red test first: none. This is spec-first work because the desired
     contract does not currently exist.
   - Done when the spec tells a zero-context reader:
     - which queues the direct known-TID path reads first
     - which payload shape counts as terminal on `ctrl_out`
     - when bounded `weft.log.tasks` fallback is allowed
     - what remains the full diagnostic/status path
   - Stop if the spec text starts redefining project-wide status behavior.

2. Add public data shape and core parser helpers.
   - Files:
     - `weft/commands/types.py`
     - `weft/commands/_streaming.py` or a narrow new helper module
     - `tests/commands/test_result.py` or `tests/commands/test_task_commands.py`
   - Add `QueueAckTarget` and `TaskTerminalSnapshot`.
   - Add helpers to parse final outbox values without consuming and to parse
     typed terminal ctrl-out envelopes.
   - Parser helpers must return exact ack targets only for messages they can
     identify precisely. Do not return an ack target for arbitrary ctrl-out
     replies, stream chunks, persistent output, or partial outbox output.
   - Reuse `process_outbox_message()` decoding behavior where possible.
   - Red test first:
     - create a broker-backed test queue containing a PING response, stderr
       stream chunk, malformed JSON, and one terminal envelope
     - assert only the terminal envelope is selected and its exact ack target
       is returned
     - create an outbox queue with a final value and assert peek parsing does
       not consume it
     - create a persistent-task output message or stream chunk and assert it
       does not produce `status="completed"` or an outbox ack target
   - DRY gate: if the implementation copies `process_outbox_message()` or
     `aggregate_public_outputs()` logic instead of reusing or lightly wrapping
     it, stop and revise.
   - YAGNI gate: do not add generic queue-envelope registries or parser plugin
     abstractions.
   - Done when parser tests pass and the public dataclass is re-export-ready
     without changing existing result behavior.

3. Emit terminal ctrl-out envelopes from task-owned terminal paths.
   - Files:
     - `weft/core/tasks/base.py`
     - `weft/core/tasks/consumer.py`
     - `weft/core/manager.py`
     - `tests/tasks/test_consumer_terminal_events.py`
     - `tests/tasks/test_control_channel.py`
     - `tests/core/test_manager.py`
   - Add a narrow helper such as `_send_terminal_envelope(source="task", ...)`.
   - Call it from normal terminal failure paths:
     `failed`, `timeout`, `cancelled`, `killed`.
   - Add manager-authored terminal envelope emission for task-wrapper death
     only when the manager observes the child process has exited and bounded
     direct inspection shows no task-owned terminal envelope for that child.
     This should write `source="manager"` to the child `ctrl_out` queue and
     should not write to outbox.
   - The manager inspection rule is required, not optional. Use the direct
     known-TID helper or the same bounded lower-level parsing pieces. Do not
     call project-wide status from the manager path.
   - Consider successful completion optional because outbox is the success
     proof. If success terminal envelopes are added, they must be additive and
     not replace outbox.
   - Keep envelope writes best effort and do not alter state if ctrl-out write
     fails.
   - Red test first:
     - extend or add broker-backed Consumer tests that run a command/function
       failure and assert one terminal ctrl-out envelope with
       `type="terminal"`, `source="task"`, `tid`, and terminal `status`
     - add timeout/cancel/kill coverage where an existing test already
       exercises the real path; do not invent a mock-only path for process
       lifecycle behavior
     - add a manager test where a child wrapper dies without a task-owned
       terminal envelope and the manager writes exactly one
       `source="manager"` terminal envelope to that child's `ctrl_out`
     - add a manager test where a task-owned terminal envelope or task-log
       terminal state already exists and the manager writes no duplicate
       terminal envelope
   - DRY gate: use one helper for terminal envelope construction. Do not
     hand-build slightly different terminal JSON in multiple task methods.
   - Error gate: a forced ctrl-out write failure must not change the terminal
     task-log event or final task state.
   - Done when task-owned failure paths still publish exactly one terminal
     task-log event and now also expose the typed ctrl-out terminal envelope.

4. Add the direct terminal snapshot command helper.
   - Files:
     - `weft/commands/tasks.py`
     - `tests/commands/test_task_commands.py`
     - `tests/commands/test_status.py`
   - Implement the algorithm in section 7.
   - Do not call `_collect_task_snapshots()` in the normal path.
   - Add a regression test that monkeypatches or instruments the global log
     replay path to fail if `task_terminal_snapshot()` uses it when outbox or
     terminal ctrl-out can answer directly.
   - Add stale no-liveness tests that prove the helper returns `pending` before
     stale threshold and `failed` only after the threshold.
   - Add a no-prior-live-proof test. If outbox, ctrl-out, mapping, and bounded
     log are all absent for a syntactically valid TID, the helper must return
     `unknown` or `pending`, never inferred `failed`.
   - Red test first:
     - outbox present: helper returns `completed`, source `outbox`, and outbox
       message remains readable afterward
     - terminal ctrl-out present: helper returns the terminal failure status,
       source `ctrl_out`, and ctrl-out message remains readable afterward
     - outbox/ctrl-out present: monkeypatch `system._collect_task_snapshots` to
       raise and assert the direct helper still succeeds
     - no direct proof: assert bounded fallback uses `since_timestamp=int(tid)-1`
       by instrumenting `iter_queue_json_entries()` or a fake queue generator
     - custom IO: write a TaskSpec-bearing event with custom outbox and
       ctrl-out names in the bounded window; assert the helper reads those
       queues and does not probe only canonical `T{tid}.*`
     - persistent task: write visible outbox output for a persistent task and
       assert the helper does not return terminal `completed` from outbox
       alone
   - DRY gate: reuse existing `_queue_names_for_tid()`, `_ctrl_out_for_tid()`,
     `mapping_for_tid()`, and runtime liveness helpers where they fit. Do not
     fork status reconstruction logic.
   - Custom IO gate: support custom IO names only through bounded TaskSpec
     lookup from `int(tid)-1`. Do not add a custom queue-name index.
   - YAGNI gate: if custom IO support appears to require a broader index, stop
     and write a follow-up plan.
   - Done when the direct helper answers known-TID terminal state without full
     global replay and without consuming task-local messages by default.

5. Add bounded known-TID diagnostic snapshots for existing API paths.
   - Files:
     - `weft/commands/system.py`
     - `weft/commands/tasks.py`
     - `weft/client/_task.py` only if the client wrapper needs no-op import or
       docstring updates
     - `weft/client/_namespaces.py` only if namespace docs/tests need updates
     - `tests/commands/test_status.py`
     - `tests/commands/test_task_commands.py`
     - `tests/core/test_client.py`
   - Implement the algorithm in section 7.1.
   - Use the existing `TaskSnapshot` return shape. Do not introduce a second
     diagnostic snapshot type.
   - Route these full-TID paths through the bounded helper:
     - `weft.commands.tasks.task_status(full_tid, ...)`
     - `weft.commands.tasks.task_snapshot(full_tid, ...)`
     - `WeftClient.task(full_tid).snapshot()`
     - `client.tasks.status(full_tid)`
   - Keep short-ID behavior working:
     - `task_status(short_tid, ...)` may still use TID mapping and legacy
       broad replay when needed.
     - Do not make full-TID optimization break short-TID CLI or client
       ergonomics.
   - Red test first:
     - create a broker-backed context with many old unrelated log events whose
       timestamps are lower than the target TID, then write target task events
       at or after the target TID; assert `task_snapshot(full_tid)` returns the
       target snapshot
     - instrument the global-log generator or monkeypatch `_iter_log_events()`
       to record `since_timestamp`; assert the full-TID path starts at
       `int(full_tid) - 1`
     - monkeypatch `system._collect_task_snapshots()` to raise and assert
       `task_snapshot(full_tid)` and `WeftClient.task(full_tid).snapshot()`
       still succeed when the bounded helper can answer
     - assert `task_status(short_tid)` still resolves through the existing
       mapping path and returns the expected task
   - DRY gate:
     - Extract shared event-reduction/building helpers from
       `_collect_task_snapshots()` if needed.
     - Do not paste a second copy of the full snapshot reconstruction loop.
   - YAGNI gate:
     - Do not add a status cache, task index queue, or new persistence table.
     - Do not change `weft status` broad project behavior in this task.
   - Test-design gate:
     - Use real SimpleBroker-backed queues for the task-log and mapping data.
     - A monkeypatch is acceptable only to prove the old full-replay path was
       not called or to inspect the `since_timestamp` argument.
   - Done when full-TID diagnostic snapshot APIs are bounded and existing
     short-ID behavior remains covered.

6. Remove the task list/stats N+1 global-log replay.
   - Files:
     - `weft/commands/system.py`
     - `weft/commands/tasks.py`
     - `tests/commands/test_task_commands.py`
     - `tests/commands/test_status.py`
     - `tests/core/test_client.py`
   - Implement the algorithm in section 7.2.
   - Add an internal `CollectedTaskSnapshot` dataclass in
     `weft/commands/system.py` unless a tighter local type name is clearer.
     Keep it internal. Do not expose it through `weft.client`.
   - Preserve `_collect_task_snapshots()` as a compatibility wrapper so
     existing command/status code keeps working.
   - Update `tasks.list_task_snapshots()` so it uses already-collected
     TaskSpec payloads from the new record helper. It must not call
     `load_latest_taskspec_payload()` once per snapshot.
   - Update `tasks.task_stats()` so counting statuses does not require public
     snapshot materialization or per-task TaskSpec replay.
   - Red test first:
     - seed multiple task records in a broker-backed context; assert
       `list_task_snapshots(include_terminal=True)` returns expected public
       snapshots when `tasks.load_latest_taskspec_payload` is monkeypatched to
       raise
     - assert `task_stats(include_terminal=True)` returns expected counts when
       `tasks.load_latest_taskspec_payload` is monkeypatched to raise
     - instrument `_iter_log_events()` or `iter_queue_json_entries()` narrowly
       and assert list/stats perform one global replay for the broad view, not
       one replay per task
   - DRY gate:
     - The broad list path and bounded known-TID path should share the same
       record-to-snapshot construction helpers where practical.
     - `_public_snapshot()` should receive the TaskSpec payload from the
       collected record instead of re-reading history.
   - YAGNI gate:
     - Do not optimize by adding a new durable TaskSpec index.
     - Do not redesign `weft status` output or sorting.
     - Do not change public `TaskSnapshot` fields.
   - Test-design gate:
     - Use real queue writes to seed log and mapping records.
     - Avoid tests that assert only implementation call counts without also
       asserting returned behavior.
   - Done when global list/stats uses one replay for the global view and no
     per-task `load_latest_taskspec_payload()` calls.

7. Add exact-message acknowledgement.
   - Files:
     - `weft/commands/tasks.py`
     - `weft/client/_namespaces.py`
     - `tests/commands/test_task_commands.py`
   - Add a helper that deletes exactly each `QueueAckTarget` in
     `snapshot.ack_targets`.
   - Return `False` when no ack targets exist.
   - Do not bulk-delete ctrl-out or outbox.
   - This helper is the only consuming cleanup surface added in this plan.
     `terminal_snapshot()` and all `status()` wrappers remain observation-only.
   - Red test first:
     - queue contains three messages; snapshot points at the middle terminal
       message; ack deletes only that timestamp and leaves the other two
     - snapshot contains multiple ack targets across one or more queues; ack
       deletes only those exact messages
     - ack with no ack targets returns `False`
   - Correctness gate: ack must be safe to call after an app commits its own
     terminal row. If ack fails, app correctness must still be preserved.
   - YAGNI gate: do not add retention windows, background sweepers, or "clear
     all task queues" helpers in this slice.
   - Done when exact-message ack is idempotent enough for reconciliation and
     cannot remove unrelated control replies.

8. Expose through `weft.client`.
   - Files:
     - `weft/client/_task.py`
     - `weft/client/_namespaces.py`
     - `weft/client/_types.py`
     - `weft/client/__init__.py`
     - `tests/core/test_client.py`
   - Add `Task.terminal_snapshot()` and
     `client.tasks.terminal_snapshot()`.
   - Add `client.tasks.ack_terminal_snapshot()`.
   - Re-export `TaskTerminalSnapshot`.
   - Keep `Task.snapshot()` unchanged as the full diagnostic snapshot.
   - Red test first:
     - `WeftClient.task(tid).terminal_snapshot()` delegates to the command
       helper and returns the public dataclass
     - `client.tasks.terminal_snapshot(tid)` returns the same shape
     - `Task.snapshot()` still returns the existing diagnostic `TaskSnapshot`
       shape and does not call the terminal snapshot helper
   - Layering gate: client code may import command-layer helpers and public
     types, but must not import task internals or runner internals.
   - Done when public imports work from `weft.client` and root `weft` without
     breaking existing client tests.

9. Update `weft-django`.
   - Files:
     - `integrations/weft_django/weft_django/client.py`
     - `integrations/weft_django/weft_django/__init__.py`
     - `integrations/weft_django/README.md`
     - `integrations/weft_django/tests/test_weft_django.py`
   - Add `terminal_snapshot(tid, timeout=0.0)`.
   - Add `snapshot(tid)` for full diagnostic `TaskSnapshot | None` behavior.
   - Map module-level `status(tid)` to the new terminal snapshot API.
   - Map `DjangoWeftClient.status(tid)` to the new terminal snapshot API.
   - Preserve `WeftSubmission.status() -> str | None`.
   - Document that `status(tid)` is now a direct known-TID reconciliation
     helper, while full task snapshots remain available through the client
     handle.
   - Tests should prove `weft_django.status()` does not call
     `Task.snapshot()` and does call `Task.terminal_snapshot()`.
   - Red test first:
     - monkeypatch the public client `Task.terminal_snapshot()` to return a
       fake `TaskTerminalSnapshot`; assert module-level
       `weft_django.status(tid)` returns it
     - monkeypatch `Task.snapshot()` to raise; assert `weft_django.status(tid)`
       does not use it
     - assert `WeftSubmission.status()` still returns a plain status string
     - assert `weft_django.snapshot(tid)` still returns the full client
       `TaskSnapshot | None` path
   - Layering gate: `weft-django` must continue to import only from
     `weft.client`, not `weft.commands` or `weft.core`.
   - Compatibility gate: downstream code that does
     `snapshot = weft_django.status(tid); snapshot.status` must keep working.
     Code that expected the full `TaskSnapshot` fields should be pointed to
     `weft_django.snapshot(tid)` or `get_client().task(tid).snapshot()`.
   - Done when Django tests prove the wrapper changed behavior without adding a
     Django-owned task state store.

10. Add an mm-governance-style reconciliation proof in Weft tests.
   - Files:
     - `integrations/weft_django/tests/test_weft_django.py`
   - Use a small broker-backed Django task or native spec.
   - Submit, wait for terminal state through `weft_django.status(tid)`, and
     verify no result consumption is required.
   - Ack the terminal/result snapshot after a simulated app-row update when
     `ack_targets` are present and verify only exact messages are removed.
   - Red test first:
     - submit a task that fails in the worker, call `weft_django.status(tid)`,
       and assert the returned status is terminal without calling
       `weft_django.result()`
     - for a success result, call `weft_django.status(tid)` and then
       `weft_django.result(tid, timeout=...)`; result should still be
       available because status was non-consuming
   - Test boundary: this is an integration test. Use the existing fixture
     project and real broker-backed execution. Do not mock the broker, manager,
     Consumer, or TaskRunner.
   - Done when the test would fail under the old `status()` global-replay
     dependency or consuming-result behavior.

11. Run focused verification and update docs.
   - Commands:
     - `./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q -n 0`
     - `./.venv/bin/python -m pytest tests/commands/test_result.py tests/commands/test_status.py -q -n 0`
     - `./.venv/bin/python -m pytest tests/tasks/test_consumer_terminal_events.py tests/tasks/test_control_channel.py -q -n 0`
     - `./.venv/bin/python -m pytest tests/core/test_client.py -q -n 0`
     - `./.venv/bin/python -m pytest integrations/weft_django/tests/test_weft_django.py -q -n 0`
     - `./.venv/bin/python -m ruff check weft integrations/weft_django`
     - `./.venv/bin/python -m mypy weft integrations/weft_django/weft_django`
   - Add broader test runs if terminal envelope changes touch shared task
     teardown behavior.
   - Use the in-repo virtualenv binaries. Do not assume global `pytest`,
     `ruff`, or `mypy`.
   - Run `git diff --check` before handoff.
   - Done when each requested behavior has an evidence line: file path changed,
     test command run, and observed queue/client behavior.

## 9. Testing Strategy

Use real broker-backed queues for correctness. Do not mock the task-local queue
surfaces except for narrow "this path was not called" assertions around global
log replay.

Preferred test order:

1. Red parser tests for non-consuming outbox and terminal ctrl-out parsing.
2. Red direct-helper tests proving no full global replay on direct hits.
3. Red bounded known-TID diagnostic snapshot tests proving full-TID
   `Task.snapshot()` / `client.tasks.status()` do not use unbounded replay.
4. Red broad list/stats tests proving one global replay, not one replay per
   task.
5. Red Consumer tests for terminal ctrl-out envelope emission.
6. Red client wrapper tests.
7. Red `weft-django` wrapper tests.
8. Broker-backed Django integration proof.

Do not move to the next layer until the lower layer has a failing test and then
passes. If a test is hard to write without mocking core queues or task
processes, stop and reassess the design; this feature is about queue-visible
behavior.

Required proofs:

- Direct one-shot final outbox result returns `completed` without consuming the
  outbox by default.
- Direct outbox output from a persistent task does not return terminal
  `completed` unless a separate terminal boundary is proven.
- Direct ctrl-out terminal envelope returns failed/timeout/cancelled/killed
  without consuming ctrl-out by default.
- Non-terminal ctrl-out content is ignored.
- Exact ack removes only the selected terminal/result message.
- A live runtime mapping returns `running`.
- Missing liveness before stale threshold does not immediately become failed.
- Stale no-liveness after threshold returns failed with `source="observer"`.
- Stale no-liveness without prior-live or spawn-owned proof does not infer
  failed.
- Custom IO outbox and ctrl-out names are honored when their TaskSpec is
  visible in the bounded known-TID window.
- Manager-observed task-wrapper death writes a manager terminal ctrl-out
  envelope only when no task-owned terminal proof is already visible.
- Bounded log fallback starts at `int(tid) - 1` and does not full-replay.
- Full-TID `Task.snapshot()` and `client.tasks.status(full_tid)` use bounded
  known-TID replay, not `_collect_task_snapshots()` over the whole log.
- Short-TID `Task.snapshot()` / `client.tasks.status(short_tid)` still resolve
  through TID mapping and return the right task.
- `client.tasks.list()` and `client.tasks.stats()` do not call
  `load_latest_taskspec_payload()` once per task.
- Broad list/status views perform at most one global task-log replay for the
  view.
- `weft_django.status(tid)` uses the new direct API.

Negative proofs:

- `task_terminal_snapshot()` must not call `system._collect_task_snapshots()`
  when outbox, terminal ctrl-out, or runtime mapping can answer.
- `terminal_snapshot()` must not remove outbox or ctrl-out messages.
- `ack_terminal_snapshot()` must not delete non-matching timestamps.
- `ack_terminal_snapshot()` must not delete persistent output, stream chunks,
  control replies, or partial outbox output that was not returned as an exact
  `QueueAckTarget`.
- `task_snapshot(full_tid)` must not fall back to the unbounded project-wide
  snapshot collector when the bounded known-TID helper can answer.
- `list_task_snapshots()` must not re-read `weft.log.tasks` per task to recover
  TaskSpec payloads.
- `task_stats()` must not materialize full public snapshots if it only needs
  status counts.
- `weft_django.status()` must not call `Task.snapshot()`.
- Project-wide `weft status` behavior must not change.

What not to mock:

- SimpleBroker queues for parser/helper behavior.
- Consumer terminal paths.
- TaskRunner process/return-code behavior where process failure classification
  is under test.
- `weft-django` broker-backed integration tests.

Acceptable mocks:

- Monkeypatching `_collect_task_snapshots()` to raise in a direct-helper test
  that proves no full replay.
- Monkeypatching `Task.terminal_snapshot()` and `Task.snapshot()` in narrow
  `weft-django` wrapper tests.
- Fake queue generator instrumentation to prove `since_timestamp` is bounded.

## 10. Rollout And Compatibility

Ship order:

1. Core data shape and non-consuming direct helper.
2. Task-owned and manager-owned terminal ctrl-out envelopes.
3. Bounded known-TID diagnostic snapshot helper for existing full-TID
   `Task.snapshot()` / `client.tasks.status()` paths.
4. Batch snapshot materialization fix for list/stats N+1 replay.
5. Public client methods.
6. `weft-django` mapping.
7. Exact-message acknowledgement helper and docs for reconciliation cleanup.
8. Optional downstream mm-governance adoption.

Backward compatibility:

- Existing `Task.snapshot()` stays available and keeps authoritative
  diagnostic behavior, but full-TID calls should use bounded replay.
- Existing `client.tasks.list()` and `client.tasks.stats()` keep their return
  shapes while avoiding per-task TaskSpec replay.
- Existing `weft_django.status(tid)` keeps returning an object with `.status`;
  callers that only read `.status` continue to work.
- New terminal ctrl-out envelopes are additive. Existing readers that ignore
  unknown ctrl-out payloads continue to work.
- Exact-message ack is opt-in.

Post-ship observation:

- Django reconciliation jobs should stop scaling with total `weft.log.tasks`
  history length.
- `T{tid}.ctrl_out` depth should stay bounded when consumers ack exact terminal
  messages after durable app-state updates.
- Global `weft status` and full task snapshots should retain current behavior.
