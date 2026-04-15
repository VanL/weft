# Runtime and Context Patterns

This runbook captures Weft-specific runtime patterns that are easy to miss if
you only read the specs at a high level.

## 1. Use `build_context()`, Not a Custom Discovery Path

Project context resolution is centralized in `weft/context.py`.

Rules:

- Use `build_context()` or `WeftContext` when resolving the active project.
- Treat `spec.weft_context` as the explicit override when present.
- Do not reimplement your own upward `.weft/` search logic in commands or
  runtime code.
- Do not infer the Weft artifact directory from broker DB naming. `.weft/`
  stays fixed even if broker file naming changes.

Why:

- Context resolution now delegates to SimpleBroker's public project API.
- The returned context already carries:
  - the resolved broker target,
  - translated `BROKER_*` config,
  - Weft-specific directories such as `.weft/outputs/` and `.weft/logs/`,
  - and the autostart directory/config.

## 2. Reuse Queue Handles on Live Task Paths

Inside tasks and watchers, use the existing queue/cache helpers rather than
opening new `Queue(...)` handles casually.

Rules:

- In `BaseTask` subclasses, use `_queue()` so handles share the task's broker
  connection pool and stop event wiring.
- In `MultiQueueWatcher`, let the watcher own queue objects for watched queues.
- Use `WeftContext.queue()` in command/helpers when you just need a
  context-bound queue.
- Raw `Queue(...)` construction is acceptable at edges, but not as a
  replacement for task/watcher queue caches.

Why:

- Shared handles carry the configured broker target and stop-event behavior.
- Recreating queue handles in hot paths makes observability and shutdown logic
  harder to reason about.

## 3. Child Processes Must Recreate Broker Connections

Weft uses `multiprocessing.get_context("spawn")` for task and worker
processes.

Rules:

- Never rely on inherited queue/database handles across child processes.
- Pass serializable data into the child and recreate broker-backed objects
  there.
- If you add a new subprocess boundary, keep it on the same spawn-based model
  unless the relevant spec changes.

Why:

- Queue connections cannot be shared safely across process boundaries.
- Spawn keeps process state explicit and avoids inherited file descriptors.

## 4. For Append-Only Queues, Iterate by Generator, Not Fixed Limits

Many important Weft queues are append-only histories:

- `weft.log.tasks`
- `weft.state.tid_mappings`
- `weft.state.managers`
- task outboxes in some CLI/result flows

Rules:

- Prefer `iter_queue_entries()` / `iter_queue_json_entries()` or
  `peek_generator()` when reading queue history.
- Avoid `peek_many(limit=N)` for correctness-critical history reads unless the
  queue is known to be small and bounded.
- When building snapshots from append-only queues, reduce by latest timestamp
  per logical key such as `tid`.

Why:

- Fixed-size peeks silently miss older or newer entries once queues grow beyond
  the chosen limit.
- The helper iterator APIs exist specifically to avoid that trap.

## 5. Keep the Template/Resolved TaskSpec Boundary Clean

TaskSpec has two phases, and the code depends on keeping them separate.

Rules:

- Templates may omit `tid`, `io`, `state`, and `spec.weft_context`.
- Template validation should use the template path:
  `context={"template": True, "auto_expand": False}`.
- Resolved TaskSpecs should be created through the shared resolution path,
  especially `resolve_taskspec_payload()`.
- Do not hand-roll queue-name defaults or TID rewrites in multiple places.
- Do not mutate `tid`, `spec`, or `io` after a resolved TaskSpec is built.

Why:

- The code intentionally freezes resolved `spec` and `io`.
- TID assignment, queue defaulting, and context fill belong to pre-validation
  resolution, not ad hoc runtime mutation.

## 6. Treat `weft.state.*` as Runtime-Only

These queues are runtime aids, not durable application state:

- `weft.state.managers`
- `weft.state.tid_mappings`
- `weft.state.streaming`

Rules:

- Keep them excluded from dump/load and similar persistence features unless a
  spec explicitly changes that rule.
- Do not build correctness-critical business features that depend on these
  queues surviving export/import round trips.

Why:

- Import/export tooling already skips them by prefix.
- They represent live runtime bookkeeping, not durable user intent.

## 7. Manager Registry State Is a Snapshot Derived from an Append-Only Log

Manager registry entries are written over time; callers reconstruct the current
view.

Rules:

- Resolve the latest record per `tid`.
- When interpreting `status="active"` manager entries, check PID liveness
  before trusting them.
- Prefer pruning stale active-manager records rather than treating them as
  authoritative forever.

Why:

- Registry records are append-only observations.
- The command layer already reduces them into a snapshot and prunes stale
  manager entries by PID liveness.

## 8. Completion Events and Result Availability Are Not Always Simultaneous

CLI/result code intentionally allows a short grace period after terminal log
events.

Rules:

- Do not assume a completion log event means the outbox message is already
  visible in the same instant.
- Reuse the existing result/wait helpers in tests and command flows rather than
  reimplementing immediate-read assumptions.

Why:

- Completion and final outbox persistence can be separated by a small timing
  window.
- Existing helpers already encode the correct behavior.

## 9. Queue-First Submission Means Post-Enqueue Recovery Is Reconciliation

`weft run` writes the spawn request before it proves manager readiness.

Rules:

- Do not "fix" submission races by moving manager bootstrap ahead of the
  enqueue step unless the spec changes.
- Once the spawn request write returns, treat that submitted TID as durable
  user intent.
- Post-enqueue failures must reconcile by submitted TID using durable surfaces:
  task logs, TID mappings, `weft.spawn.requests`, and manager reserved queues.
- Only requests still provably present in `weft.spawn.requests` are safe to
  delete as rollback.
- If the request is already in a manager reserved queue, do not claim cleanup
  succeeded. That is an operator recovery case.

Why:

- Queue-first ordering is the contract that avoids a different startup race.
- The public spawn queue is not the sole owner of the message after a manager
  reserves it.

## 10. Recovering a Stuck Reserved Spawn Request

When submission reconciliation reports that a spawn request is stuck in a
manager reserved queue, use the existing queue commands and operate on the
exact message ID only.

Rules:

- Identify the submitted child TID first. That message ID is the spawn-request
  timestamp and the child task TID.
- Identify the candidate reserved queue, usually `T{manager_tid}.reserved`.
- Inspect before mutating:
  `weft queue peek T{manager_tid}.reserved --timestamps`
- Delete only the exact message when you have proved it is stale:
  `weft queue delete T{manager_tid}.reserved --message <tid>`
- Requeue only after you have first proved the child did not already spawn:
  `weft queue move T{manager_tid}.reserved weft.spawn.requests --message <tid>`
- Never bulk-delete or bulk-move a manager reserved queue as a shortcut.

Why:

- The reserved queue may hold a real in-flight submission, not dead garbage.
- Exact-message recovery preserves the queue-first contract and avoids
  duplicating or losing unrelated work.
