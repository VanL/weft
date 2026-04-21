# Client Follow Hardening Plan

## 1. Goal

Harden the new public Python client follow surfaces so a missed or already-read
terminal observation cannot hang CI or downstream callers indefinitely. The
change keeps the queue-first state model intact, preserves the existing default
unbounded follow behavior for long-lived UI streams, and adds explicit bounded
waits for tests and callers that need fail-fast behavior.

## 2. Source Documents

Source specs:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- `docs/specifications/07-System_Invariants.md` [OBS.1], [OBS.2], [OBS.3]
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-2.1], [DJ-2.2],
  [DJ-12.1], [DJ-12.3]

Guidance:

- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`
- `docs/agent-context/runbooks/testing-patterns.md`

## 3. Context and Key Files

Files to modify:

- `weft/commands/events.py`
- `weft/commands/tasks.py`
- `weft/client/_task.py`
- `weft/client/_namespaces.py`
- `tests/core/test_client.py`
- `tests/core/test_ops_shared.py`
- `docs/specifications/13C-Using_Weft_With_Django.md`

Read first:

- `weft/commands/result.py`, especially `_await_result_materialization()`
- `weft/commands/_result_wait.py`, especially completion grace handling
- `weft/core/queue_wait.py`, especially `QueueChangeMonitor.close()`
- `tests/helpers/weft_harness.py`

Current structure:

- `Task.result()` already reuses `weft.commands.result.await_task_result()`
  and accepts a timeout.
- `Task.follow()`, `Task.events(follow=True)`,
  `Task.realtime_events(follow=True)`, and `client.tasks.watch()` are generator
  surfaces over shared command-layer event/status helpers.
- `iter_task_realtime_events()` calls `_await_result_materialization()` before
  opening outbox, ctrl-out, and log watchers. That helper can already observe a
  terminal task-log event and return the terminal status, but the current
  realtime iterator does not consume that terminal observation.

Comprehension checks:

- Which queue proves durable lifecycle state? `weft.log.tasks`.
- Which helper already owns task result materialization and completion grace?
  `weft.commands.result._await_result_materialization()` plus
  `weft.commands._result_wait.await_one_shot_result()`.

## 4. Invariants and Constraints

- Preserve [MF-5]: task lifecycle state is reconstructed from task-local queues
  and `weft.log.tasks`; no second state database.
- Preserve [OBS.1]-[OBS.3]: lifecycle events stay queue-visible and global-log
  backed.
- Preserve default public behavior: `follow=True` remains unbounded unless the
  caller supplies a timeout or cancel event.
- Do not change queue names, TaskSpec schema, result payload shape, manager
  startup sequencing, or reserved-queue policy.
- Keep queue-history reads generator-based; do not introduce fixed-limit
  history scans for correctness.
- Keep tests broker-backed for client lifecycle behavior. Mock only the narrow
  command-layer seams needed to prove timeout propagation or already-observed
  terminal handling.

Rollback:

- The code changes are additive and backward-compatible at the public API
  surface. Rollback can remove optional timeout plumbing and restore the
  previous generators without touching queue data or persisted TaskSpecs.

Out of scope:

- Redesigning `QueueChangeMonitor`.
- Changing SimpleBroker watcher behavior.
- Changing CLI `status --watch` or `queue watch` semantics.
- Adding a global pytest timeout in this slice.

## 5. Tasks

1. Preserve already-observed terminal state in realtime events.
   - Files: `weft/commands/events.py`, `tests/core/test_ops_shared.py`
   - Reuse `ResultMaterialization.terminal_status` and
     `.terminal_error_message`; do not reread the same event from a fixed
     queue peek.
   - Done when a unit regression proves `iter_task_realtime_events()` emits
     `result` and `end` even when materialization already consumed the terminal
     log boundary.

2. Add explicit bounded follow/watch support.
   - Files: `weft/commands/events.py`, `weft/commands/tasks.py`,
     `weft/client/_task.py`, `weft/client/_namespaces.py`
   - Add optional `timeout: float | None = None` keyword arguments to follow
     generators and pass deadlines through wrappers.
   - On explicit timeout, raise `TimeoutError` with the task id and operation
     name. Default `None` stays unbounded.
   - Stop if the implementation wants a new task state or a synthetic durable
     timeout event; explicit follow timeout is caller-local, not task state.

3. Bound the new client tests and add race regressions.
   - Files: `tests/core/test_client.py`, `tests/core/test_ops_shared.py`
   - Use real `WeftTestHarness` for end-to-end client tests.
   - Keep small timeouts large enough for Windows CI but finite.
   - Done when failure would be a pytest assertion or `TimeoutError`, not a
     job-level hang.

4. Update the client spec.
   - File: `docs/specifications/13C-Using_Weft_With_Django.md`
   - Add a plan backlink and note that client follow/watch surfaces may accept
     explicit caller-local timeouts without changing task lifecycle state.

## 6. Testing Plan

Targeted commands:

```bash
./.venv/bin/python -m pytest tests/core/test_ops_shared.py -q
./.venv/bin/python -m pytest tests/core/test_client.py -vv --tb=short --override-ini="addopts=-ra -q --strict-markers"
```

Expanded command if targeted tests pass:

```bash
./.venv/bin/python -m pytest tests/core/test_client.py tests/core/test_ops_shared.py tests/commands/test_result.py tests/commands/test_status.py -q
```

Observable proof:

- `Task.follow(timeout=...)`, `Task.realtime_events(timeout=...)`, and
  `client.tasks.watch(timeout=...)` complete for real `echo` tasks.
- A simulated already-materialized terminal state produces realtime `result`
  and `end` events without waiting for another log write.
- Explicit timeout failures are bounded and local; they do not publish task
  timeout state.
