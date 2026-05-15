# SimpleBroker Multi-Queue Waiter Integration

Status: completed
Source specs: see Source Documents below
Superseded by: none

Date: 2026-05-05

Owner: Weft

Primary code owner: `weft/core/tasks/multiqueue_watcher.py`

## Goal

Use SimpleBroker 3.3.2's multi-queue activity waiter API in Weft's real
multi-queue runtime paths. PostgreSQL-backed Weft tasks should wait on one
backend-native fan-in waiter per process instead of composing per-queue waiters
or sleeping until the next poll. SQLite should keep its current safe fallback
behavior: no backend-specific code, no PG imports, and ordinary polling when no
native multi-queue waiter is available.

This is not a connection-pool or process-count solution. It reduces per-process
watcher fanout and wake latency. Too many Weft processes can still exceed a
database's global connection budget and must be managed outside this slice.

## Source Documents

Source specs:

- `docs/specifications/01-Core_Components.md` [CC-2.1], [CC-2.2], [CC-2.5]
- `docs/specifications/03-Manager_Architecture.md` [MA-1.2], [MA-3]
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.4]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3], [MF-5]
- `docs/specifications/07-System_Invariants.md` [QUEUE.4], [QUEUE.5],
  [QUEUE.6], [IMPL.5], [IMPL.6], [MANAGER.5]
- `docs/specifications/08-Testing_Strategy.md`

Repo guidance:

- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/testing-patterns.md`

Upstream API facts:

- Weft now depends on `simplebroker>=3.3.2`.
- The `pg` extra now depends on `simplebroker-pg>=1.1.0`.
- SimpleBroker exports
  `simplebroker.create_activity_waiter_for_queues(queues, stop_event=...)`.
- The helper returns `ActivityWaiter | None`.
- `None` means the backend has no efficient multi-queue waiter and the caller
  should fall back to polling.
- A returned waiter is caller-owned. Weft must close it explicitly.
- The waiter is a hint only. `wait(timeout) is True` means some watched queue
  may have changed; Weft must still call normal queue APIs before it drains or
  reserves work.

## Current Structure

`weft/core/tasks/multiqueue_watcher.py`

- `MultiQueueWatcher` extends SimpleBroker's `BaseWatcher`.
- It builds one `Queue` per configured queue, all on one resolved broker target.
- `_has_pending_messages()` checks all configured queues.
- `_drain_queue()` does round-robin dispatch across active queues and preserves
  per-queue `READ`, `PEEK`, and `RESERVE` behavior.
- The class does not currently own an explicit multi-queue wait primitive.

`weft/core/launcher.py`

- `_task_process_entry()` is the real spawned task process loop.
- It calls `task.process_once()`, checks terminal state, then sleeps for
  `TASK_PROCESS_POLL_INTERVAL`.
- Managers run through this same entry point via `weft/manager_process.py`.
- Therefore, integrating only SimpleBroker's `BaseWatcher.run_forever()` would
  miss the ordinary manager and task execution path.

`weft/core/tasks/base.py`

- `BaseTask` inherits `MultiQueueWatcher`.
- `BaseTask.process_once()` drains queues and emits poll reports.
- `BaseTask.run_until_stopped()` has its own loop used by tests and direct
  in-process callers.
- `BaseTask.cleanup()` closes cached queue handles. It does not currently know
  about a caller-owned multi-queue activity waiter.

`weft/core/tasks/heartbeat.py`

- `HeartbeatTask` is a long-lived internal task.
- It currently builds separate single-queue activity waiters for inbox and
  control queues, then slices timeout across them.
- This is exactly the local fanout pattern the new SimpleBroker API can replace.

`weft/core/queue_wait.py`

- `QueueChangeMonitor` powers CLI/result/status wait surfaces.
- It currently starts one `QueueWatcher` thread per queue.
- For PostgreSQL, SimpleBroker 3.3.2 already shares the physical listener under
  single-queue waiters, but this still creates N watcher threads and N logical
  waiters. It should use the multi-queue waiter when available, with the
  existing per-queue `QueueWatcher` behavior as the fallback.

Dependency files:

- `pyproject.toml` already has `simplebroker>=3.3.2` and
  `simplebroker-pg>=1.1.0` for `pg`.
- `pyproject.toml` still needs to be checked for `all` and `dev` extras. They
  must not allow `simplebroker-pg<1.1.0` if this plan relies on the multi-queue
  PG hook during development or all-extra installs.
- `uv.lock` currently resolves `simplebroker==3.3.2` and
  `simplebroker-pg==1.1.0`.

Comprehension checks before editing:

1. Why does changing only `BaseWatcher.run_forever()` not help ordinary manager
   and consumer processes? Answer: spawned tasks and managers call
   `_task_process_entry()`, which loops over `process_once()` and sleeps.
2. Why must Weft still call `has_pending`, `read_one`, `peek_one`, or
   `move_one` after a native wake? Answer: SimpleBroker activity waiters are
   hints, not proof that a queue has claimable work.
3. Which layer owns PG-specific LISTEN/NOTIFY behavior? Answer: SimpleBroker
   and `simplebroker-pg`, not Weft.

## Files To Touch

Primary implementation:

- `weft/core/tasks/multiqueue_watcher.py`
- `weft/core/tasks/base.py`
- `weft/core/launcher.py`
- `weft/core/tasks/heartbeat.py`
- `weft/core/queue_wait.py`

Tests:

- `tests/tasks/test_multiqueue_watcher.py`
- `tests/tasks/test_heartbeat.py`
- `tests/commands/test_result.py`
- `tests/commands/test_queue.py`
- `tests/commands/test_status.py`
- add a focused file if clearer:
  `tests/core/test_queue_wait.py`

Dependency and docs:

- `pyproject.toml`
- `uv.lock` only if dependency constraints change
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/08-Testing_Strategy.md` only if test strategy wording
  needs a backend-specific note
- `docs/lessons.md` only if implementation exposes a repeated failure mode

Files to read but not casually edit:

- `weft/core/manager.py`
- `weft/manager_process.py`
- `weft/commands/_result_wait.py`
- `weft/commands/_spawn_submission.py`
- `weft/commands/result.py`
- `weft/commands/tasks.py`
- `tests/helpers/test_backend.py`
- `tests/helpers/weft_harness.py`
- `tests/multiqueue_polling_benchmark.py`

## Invariants And Constraints

Preserve these invariants:

- [QUEUE.4] reserve/claim semantics stay broker-owned. Do not replace
  `move_one`, `read_one`, `peek_one`, `has_pending`, or delete behavior with
  wake notifications.
- [QUEUE.5] a reserved work item stays the single in-flight work item for a
  task.
- [QUEUE.6] reserved-policy behavior stays in `BaseTask`/task ownership, not
  in waiter code.
- [IMPL.5] child processes recreate broker-connected state. Do not pass live
  Queue objects, waiters, or runner connections across `spawn`.
- [IMPL.6] spawned task processes still use spawn-style multiprocessing.
- [MANAGER.5] managers still spawn child tasks through the same task model.
- A native waiter wake is a hint only. It must not bypass `_has_pending_messages`
  or `_drain_queue`.
- SQLite must remain backend-neutral. It should call the same helper and accept
  `None`, then fall back to the existing polling/sleep path.
- PostgreSQL-specific imports are forbidden in Weft core. Import only from
  `simplebroker`.
- Caller-owned waiters must be closed explicitly on every lifecycle path:
  `stop()`, `cleanup()`, monitor close, and defensive finalizer paths.
- Dynamic queue changes must not leave stale waiter registrations. If
  `add_queue()` or `remove_queue()` changes the watched set, invalidate and
  close the existing multi-queue waiter before the next wait.
- No new dependency.
- No public CLI shape change.
- No queue name, payload, status, result, or TaskSpec schema change.
- No broad rewrite of `MultiQueueWatcher` scheduling. Add one wait seam and
  reuse the existing drain path.
- Do not add a Weft-owned PG pool, listener, multiplexer, or daemon.
- Do not build a generic abstraction beyond what the current watcher and monitor
  need.

Rollback:

- The change must be removable by reverting Weft's use of
  `create_activity_waiter_for_queues`.
- Queue data remains compatible because the change must not alter payloads,
  queue names, message IDs, or reserved queues.
- If the native waiter path fails at runtime, Weft should log at debug level and
  fall back to polling rather than failing task execution.

Out of scope:

- Solving global "too many Weft processes".
- Running or managing PgBouncer.
- Changing manager leadership, canonical-owner fences, or dispatch semantics.
- Replacing SimpleBroker `QueueWatcher` internals.
- Reworking all CLI wait logic beyond `QueueChangeMonitor`.
- Changing task polling intervals or public timeout defaults.

## Bite-Sized Tasks

### 0. Preflight And Dependency Check

Outcome: confirm the new API is really available in the in-repo environment and
tighten dependency constraints if needed.

Files to read:

- `pyproject.toml`
- `uv.lock`
- `weft/core/tasks/multiqueue_watcher.py`
- `weft/core/queue_wait.py`

Commands:

```bash
. ./.envrc
uv sync --all-extras
./.venv/bin/python - <<'PY'
import inspect
import simplebroker

assert simplebroker.__version__ >= "3.3.2"
assert hasattr(simplebroker, "create_activity_waiter_for_queues")
print(simplebroker.__version__)
print(inspect.signature(simplebroker.create_activity_waiter_for_queues))
PY
```

Required actions:

- If `pyproject.toml` still lists `simplebroker-pg>=1.0.7` under `all` or
  `dev`, update those to `simplebroker-pg>=1.1.0`.
- If dependency constraints change, refresh `uv.lock` with the repo-standard
  command and include the lockfile change.

Stop and re-evaluate if:

- The in-repo `.venv` cannot import `create_activity_waiter_for_queues`.
- Satisfying the dependency would require a SimpleBroker source checkout or an
  unpublished package. The user stated the released packages are available, so
  that would mean the environment is not matching the request.

Verification:

```bash
./.venv/bin/python -m pytest tests/system/test_release_script.py -q
```

### 1. Add A MultiQueueWatcher Wait-Seam With Red Tests

Outcome: `MultiQueueWatcher` has one backend-neutral method that waits for
possible activity across all configured queues, using the SimpleBroker
multi-queue waiter when available and polling fallback when not.

Files to touch:

- `tests/tasks/test_multiqueue_watcher.py`
- `weft/core/tasks/multiqueue_watcher.py`

Read first:

- `weft/core/tasks/multiqueue_watcher.py`
- installed SimpleBroker `create_activity_waiter_for_queues` source in the
  `.venv`
- `docs/specifications/01-Core_Components.md` [CC-2.1]
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]

Design:

- Import `create_activity_waiter_for_queues` from `simplebroker`.
- Add a private helper that returns the current watched queues in deterministic
  order:

  ```python
  def _activity_wait_queues(self) -> list[Queue]:
      return [config.queue for config in self._queues.values()]
  ```

- Add a private `_ensure_multi_activity_waiter()` that:
  - tracks a `_queue_generation` counter and a
    `_multi_activity_waiter_generation`;
  - does nothing if already initialized for the current queue generation,
  - calls `create_activity_waiter_for_queues(self._activity_wait_queues(),
    stop_event=self._stop_event)`,
  - stores the returned waiter or `None`,
  - catches only expected backend/setup failures (`BrokerError`, `OSError`,
    `RuntimeError`, `TypeError`, `ValueError`) and falls back to polling with a
    debug log.
- Add a private `_start_strategy_for_configured_queues()` that mirrors the
  SimpleBroker strategy startup shape but gets its `activity_waiter` from
  `_ensure_multi_activity_waiter()`:
  - use the primary queue from `_get_queue_for_data_version()` for SQLite
    data-version checks, because SQLite data version is database-wide;
  - refresh that queue's `last_ts` before polling, preserving current
    SimpleBroker watcher behavior;
  - pass the multi-queue waiter, or `None`, into
    `self._strategy.start(...)`.
- Add `wait_for_activity(timeout: float | None) -> None`:
  - return immediately if `timeout` is `None` or `<= 0` only after checking
    `self._has_pending_messages()`;
  - return immediately if `_has_pending_messages()` is true;
  - if a multi waiter exists, call `waiter.wait(timeout)`;
  - if `waiter.wait(...)` raises an expected backend/setup failure, debug-log
    it, reset the stored waiter, and use the polling fallback for that turn;
  - otherwise wait on `self._stop_event.wait(timeout)`;
  - never drain, reserve, delete, or dispatch messages.
- Add `_reset_multi_activity_waiter()`:
  - close the stored waiter if present,
  - clear waiter state,
  - called from `add_queue()`, `remove_queue()`, `stop()`, and any local
    cleanup path.
- Increment `_queue_generation` after every successful `add_queue()` and
  `remove_queue()` so the next wait rebuilds the native waiter against the
  current watched set.
- Override `stop()` in `MultiQueueWatcher` to call `super().stop(...)` and then
  `_reset_multi_activity_waiter()`. This gives raw `MultiQueueWatcher` users the
  same waiter cleanup that `BaseTask.cleanup()` will get through inheritance.
- Override `_run_with_retries()` only because SimpleBroker 3.3.2 does not expose
  a protected "create activity waiter" hook. Keep the body as close as possible
  to the installed SimpleBroker `BaseWatcher._run_with_retries()` and change
  only strategy startup:
  - call `_start_strategy_for_configured_queues()`;
  - keep initial drain, `_process_messages()`, `_StopLoop`, `KeyboardInterrupt`,
    retry timeout, and `_handle_retry()` behavior the same;
  - add a short code comment explaining that this override exists solely to use
    SimpleBroker's multi-queue waiter for a multi-queue subclass.

Do not:

- Override queue delivery semantics.
- Create a per-queue waiter list in `MultiQueueWatcher`.
- Import `simplebroker_pg`.
- Rewrite the SimpleBroker retry loop. The narrow `_run_with_retries()` override
  above is allowed only because there is no smaller public/protected seam in
  SimpleBroker 3.3.2.
- Use the native waiter as proof that any queue has work.

Red tests to write first:

- A test that constructs a real `MultiQueueWatcher` with two real broker-backed
  queues, monkeypatches only
  `weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues`, and
  asserts the helper receives both managed `Queue` objects and the watcher's
  stop event.
- A test that returns a fake waiter from the monkeypatched helper, calls
  `wait_for_activity(timeout=...)`, and asserts `FakeWaiter.wait()` is called.
- A test that writes a real message before `wait_for_activity()` and asserts the
  fake waiter is not called because pending work should be processed without
  waiting.
- A test that has the helper return `None` and asserts `wait_for_activity()`
  uses the stop-event wait fallback without raising.
- A test that calls `add_queue()` or `remove_queue()` after a waiter exists and
  asserts the old waiter is closed before the watched set is rebuilt.
- A test that calls `watcher.stop(join=False)` or cleanup and asserts the stored
  waiter is closed.
- A test where `FakeWaiter.wait()` raises an expected exception and
  `wait_for_activity()` falls back without propagating.
- A `run_in_thread()` test with a fake multi waiter that proves the inherited
  background watcher path uses the multi-queue waiter, not only the new
  process-loop wait seam.

Test design notes:

- Use real `Queue` objects and the existing `broker_env` fixture.
- It is acceptable to monkeypatch the imported SimpleBroker helper to return a
  fake waiter; the test's subject is Weft's lifecycle and fallback logic.
- Do not mock `Queue.has_pending`, `Queue.read_one`, `Queue.move_one`, or
  `Queue.peek_one`.
- The fake waiter should be tiny and observable:

  ```python
  class FakeWaiter:
      def __init__(self) -> None:
          self.wait_calls: list[float] = []
          self.close_calls = 0

      def wait(self, timeout: float) -> bool:
          self.wait_calls.append(timeout)
          return False

      def close(self) -> None:
          self.close_calls += 1
  ```

Verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py -q
```

### 2. Use The Wait Seam In Spawned Task And Direct Task Loops

Outcome: ordinary Weft task and manager process loops use the new
`MultiQueueWatcher.wait_for_activity()` seam between `process_once()` calls.

Files to touch:

- `weft/core/launcher.py`
- `weft/core/tasks/base.py`
- `tests/tasks/test_task_execution.py`
- `tests/core/test_manager.py` only if an existing manager-loop test is the
  right nearest proof

Read first:

- `weft/core/launcher.py`
- `weft/manager_process.py`
- `weft/core/tasks/base.py`
- `docs/specifications/03-Manager_Architecture.md` [MA-1.2], [MA-3]
- `docs/specifications/01-Core_Components.md` [CC-2.5]

Design:

- In `_task_process_entry()`, after `task.process_once()` and after terminal
  checks, replace unconditional `time.sleep(poll_interval)` with:

  ```python
  wait_for_activity = getattr(task, "wait_for_activity", None)
  if callable(wait_for_activity):
      wait_for_activity(timeout=poll_interval)
  else:
      time.sleep(poll_interval)
  ```

- In `BaseTask.run_until_stopped()`, make the same replacement so direct
  in-process callers and tests exercise the same behavior.
- Keep `poll_interval` as the maximum wait budget. Do not change constants.
- Do not remove the terminal-state check before waiting.
- Do not call `wait_for_activity()` before `process_once()`, or existing
  ready work may wait unnecessarily.

Red tests to write first:

- A launcher-level test that uses a tiny task class with `process_once()` and
  `wait_for_activity()` counters, then asserts the launcher calls
  `wait_for_activity(timeout=poll_interval)` instead of sleeping when the task
  stays non-terminal for one iteration.
- A `BaseTask.run_until_stopped()` test using an existing concrete task fixture
  or a small concrete subclass that asserts `wait_for_activity()` is called
  between turns.
- A regression that a terminal task does not wait after the terminal
  `process_once()` turn.

Test design notes:

- Prefer a real concrete task when practical. If a tiny subclass is needed, keep
  it in the test file and use real broker-backed queues.
- Do not mock `time.sleep` as the only proof. It is fine as a supporting
  assertion, but the observable contract is that the task's wait seam is called
  with the configured timeout.

Verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -k "process_once or idle" -q
```

### 3. Replace Heartbeat's Per-Queue Waiter List

Outcome: `HeartbeatTask` uses the shared `MultiQueueWatcher.wait_for_activity()`
seam instead of creating separate single-queue waiters for inbox and control
queues.

Files to touch:

- `weft/core/tasks/heartbeat.py`
- `tests/tasks/test_heartbeat.py`

Read first:

- `weft/core/tasks/heartbeat.py`
- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-2.4]
- `docs/lessons.md` 2026-04-04 PG parity entries

Design:

- Remove `_activity_waiters`.
- Remove the constructor loop that calls
  `self._queue(queue_name).create_activity_waiter(...)`.
- In `_wait_for_activity(timeout=...)`, keep the current due-time and pending
  input checks, but replace the per-waiter loop with:

  ```python
  self.wait_for_activity(timeout=chunk)
  ```

- Superseded by
  [`2026-05-15-task-reactor-and-evidence-worker-plan.md`](2026-05-15-task-reactor-and-evidence-worker-plan.md):
  do not keep task-local `_has_pending_runtime_input()` checks. Queue pending
  detection belongs to `MultiQueueWatcher.wait_for_activity()`; task
  `next_wait_timeout()` hooks only expose timer/local-worker deadlines.
- Let `MultiQueueWatcher` own waiter close/reset lifecycle.
- Keep fallback bounded by the same timeout budget.

Red tests to write first:

- Update `test_heartbeat_wait_fallback_uses_bounded_stop_event_sleep` so it
  proves fallback still waits on the stop event when no native waiter exists.
- Add a test that monkeypatches `HeartbeatTask.wait_for_activity` and asserts
  `_wait_for_activity()` calls it with a bounded chunk when no registration is
  due and no input is pending.
- Add a test that pending runtime input returns before the wait seam is called.
- Remove test setup that mutates `task._activity_waiters`; that list should no
  longer exist.

Test design notes:

- Keep heartbeat registration and queue behavior real.
- Do not mock destination queues or inbox queues.
- Monkeypatching `wait_for_activity` is acceptable only to prove heartbeat's
  orchestration; queue semantics still need real queues.

Verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py -q
```

### 4. Teach QueueChangeMonitor To Prefer One Multi-Queue Waiter

Outcome: CLI/result/status wait surfaces use one multi-queue activity waiter
when the backend provides it. SQLite and unsupported backends keep the current
`QueueWatcher` fallback.

Files to touch:

- `weft/core/queue_wait.py`
- add `tests/core/test_queue_wait.py` or extend the nearest existing command
  tests if the helper already has direct coverage
- update command tests only where their fakes assume the old internal watcher
  shape

Read first:

- `weft/core/queue_wait.py`
- `weft/commands/_result_wait.py`
- `weft/commands/result.py`
- `weft/commands/tasks.py`
- `weft/core/manager_runtime.py`
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]
- `docs/specifications/10-CLI_Interface.md` wait/watch sections relevant to
  result, queue, status, and events surfaces

Design:

- Import `create_activity_waiter_for_queues` from `simplebroker`.
- In `QueueChangeMonitor.__init__`, first try the multi-queue helper:
  - if it returns a waiter, store it and start one daemon thread that loops on
    `waiter.wait(timeout)` and sets `_activity_event` when it returns true;
  - the thread should also wake when `_stop_event` is set;
  - use a small bounded waiter timeout such as 1 second so close can join
    promptly even if a backend misses the stop event;
  - if the helper returns `None`, keep the existing per-queue `QueueWatcher`
    fallback unchanged.
- If helper construction raises an expected backend/setup error, debug-log it
  and fall back to existing `QueueWatcher` behavior.
- If `waiter.wait(...)` raises after construction, debug-log it, set
  `_activity_event` once so the caller rescans through normal queue APIs, close
  the waiter, and let the thread exit. Do not leave a dead waiter thread
  spinning.
- `close()` must set `_stop_event`, close the stored multi waiter, join the
  monitor thread, then stop fallback watchers if any.
- Do not read or consume queue messages from `QueueChangeMonitor`.

Red tests to write first:

- A direct `QueueChangeMonitor` test with real queues and a monkeypatched
  helper that returns a fake waiter. Assert:
  - the helper receives all queues and the monitor's stop event,
  - one background thread is started,
  - `wait()` returns true when the fake waiter wakes,
  - `close()` closes the fake waiter and joins the thread.
- A fallback test where the helper returns `None` and existing `QueueWatcher`
  fallback is used.
- A close-idempotency test.
- A waiter-runtime-error test where the fake waiter raises, the monitor wakes
  the caller once, and `close()` still returns cleanly.
- A no-message-consumption test using real queues: write a message, let the
  monitor wake, then assert the message remains readable by normal queue APIs.

Test design notes:

- Do not mock public command behavior to prove this helper. Test
  `QueueChangeMonitor` directly with real queues.
- Existing command tests may keep their `QueueChangeMonitor` fakes because they
  are testing command-level orchestration, not this helper.

Verification:

```bash
./.venv/bin/python -m pytest tests/core/test_queue_wait.py -q
./.venv/bin/python -m pytest tests/commands/test_result.py tests/commands/test_queue.py tests/commands/test_status.py -q
```

### 5. Add PostgreSQL-Backed Integration Proofs

Outcome: the new Weft wait seams work against a real PostgreSQL backend when
`SIMPLEBROKER_PG_TEST_DSN` is available, and they do not degrade SQLite.

Files to touch:

- `tests/tasks/test_multiqueue_watcher.py`
- `tests/tasks/test_heartbeat.py`
- `tests/core/test_queue_wait.py`
- possibly `tests/multiqueue_polling_benchmark.py` if it needs a new metric
  label, but do not turn dev benchmarks into hard gates

Read first:

- `tests/helpers/test_backend.py`
- `tests/conftest.py`
- `tests/multiqueue_polling_benchmark.py`

Tests to add:

- A backend-agnostic `MultiQueueWatcher.run_in_thread()` test:
  - prepare a project root through existing backend helpers,
  - create two watched queues,
  - start the watcher thread,
  - write to queue B,
  - assert queue B's handler runs,
  - stop the watcher and assert no pending watcher thread remains.
- A backend-agnostic `wait_for_activity()` test:
  - create two real queues,
  - call `wait_for_activity()` with a short timeout when empty,
  - write to one queue from another thread,
  - assert the next `process_once()` drains it.
- A PG-only test only if needed to observe native waiter selection:
  - skip unless `BROKER_TEST_BACKEND=postgres` and
    `SIMPLEBROKER_PG_TEST_DSN` are set,
  - do not import `simplebroker_pg` from production code,
  - test may inspect `type(waiter).__name__` only inside tests if no public
    SimpleBroker API exposes the selection.

Test design notes:

- Prefer backend-agnostic tests that pass under SQLite and PostgreSQL.
- Use the existing backend helper environment. Do not invent a new PG fixture.
- Do not use arbitrary sleeps as assertions. Use bounded events or polling
  helpers.
- Do not require PostgreSQL for the default fast suite.

Manual benchmark, not a merge gate:

```bash
./.venv/bin/python -m tests.multiqueue_polling_benchmark --backends sqlite
BROKER_TEST_BACKEND=postgres SIMPLEBROKER_PG_TEST_DSN='postgresql://...' \
  ./.venv/bin/python -m tests.multiqueue_polling_benchmark --backends postgres
```

Verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/tasks/test_heartbeat.py tests/core/test_queue_wait.py -q
BROKER_TEST_BACKEND=postgres SIMPLEBROKER_PG_TEST_DSN='postgresql://...' \
  ./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/tasks/test_heartbeat.py tests/core/test_queue_wait.py -q
```

### 6. Documentation And Traceability

Outcome: specs, plan backlinks, and implementation notes explain that Weft uses
SimpleBroker's multi-queue native wait helper where available.

Files to touch:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/08-Testing_Strategy.md` if new backend-agnostic test
  guidance is added
- `docs/lessons.md` only if a repeated lifecycle mistake is found

Required updates:

- Add this plan to the `Related Plans` section of touched specs.
- In `01-Core_Components.md` [CC-2.1], update the implementation mapping or
  current role to say `MultiQueueWatcher` owns a backend-neutral multi-queue
  wait seam in addition to round-robin draining.
- In `04-SimpleBroker_Integration.md` [SB-0.4], note that Weft calls
  `simplebroker.create_activity_waiter_for_queues()` for multi-queue watcher
  and queue-monitor surfaces, and treats `None` as polling fallback.
- Keep wording backend-neutral. PostgreSQL may be named as a backend that
  currently provides an efficient waiter, but no spec should require PG-specific
  code in Weft.

Do not:

- Document a user-visible CLI change. There should not be one.
- Promise global connection-limit protection.
- Claim SQLite has LISTEN/NOTIFY or native multi-queue activity support.

Verification:

```bash
rg -n "create_activity_waiter_for_queues|multi-queue wait|activity waiter" docs/specifications docs/plans
```

### 7. Full Verification Gate

Run after all implementation tasks:

```bash
. ./.envrc
uv sync --all-extras
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/tasks/test_heartbeat.py tests/core/test_queue_wait.py -q
./.venv/bin/python -m pytest tests/commands/test_result.py tests/commands/test_queue.py tests/commands/test_status.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py tests/tasks/test_task_execution.py -q
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

If PostgreSQL is available:

```bash
BROKER_TEST_BACKEND=postgres SIMPLEBROKER_PG_TEST_DSN='postgresql://...' \
  ./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/tasks/test_heartbeat.py tests/core/test_queue_wait.py -q
```

Definition of done:

- SQLite tests pass without `simplebroker-pg` behavior being required.
- PG-specific tests skip cleanly when no DSN is configured.
- No Weft production module imports `simplebroker_pg`.
- Every caller-owned waiter created by Weft is closed on normal and defensive
  cleanup paths.
- Queue payloads, TaskSpec schema, CLI output, and lifecycle events are
  unchanged.
- Native wakeups never bypass normal queue pending/drain checks.

## Implementation Shape Summary

Expected steady-state ownership:

```text
SimpleBroker:
  Queue and backend-native waiter mechanics
  create_activity_waiter_for_queues(...)

Weft MultiQueueWatcher:
  queue set ownership
  caller-owned waiter lifecycle
  "wait for possible activity" seam
  existing round-robin drain semantics

BaseTask / launcher:
  call process_once()
  if still live, wait through the watcher seam instead of raw sleep

HeartbeatTask:
  keep heartbeat due-time logic
  use the watcher seam for queue activity waits

QueueChangeMonitor:
  use one multi-queue waiter when available
  keep QueueWatcher fallback when unavailable
```

## Fresh-Eyes Review

After drafting this plan, review it as if the implementer will choose the wrong
abstraction whenever the plan is loose.

Risk: changing only SimpleBroker `BaseWatcher.run_forever()` would miss the
real task and manager process loop.

Correction: this plan routes `_task_process_entry()` and
`BaseTask.run_until_stopped()` through a Weft-owned `wait_for_activity()` seam.

Risk: treating a native wake as proof of work would break correctness for PG
notifications, because notifications are hints and can be false positives.

Correction: every task says the wake seam must not drain, reserve, or skip
`_has_pending_messages()` / normal queue APIs.

Risk: using the new API only in PG-specific code would violate layering.

Correction: the plan imports only from `simplebroker`; SQLite gets the same
call and uses the `None` fallback.

Risk: caller-owned waiters could leak if the implementer assumes
`Queue.close()` owns them.

Correction: the plan requires explicit close paths in `MultiQueueWatcher`,
`QueueChangeMonitor`, `stop()`, cleanup, and dynamic queue reset tests.

Risk: this could become a broad rewrite of queue monitoring.

Correction: the plan forbids queue contract changes, public CLI shape changes,
and new abstractions outside the current watcher and monitor surfaces.

Risk: dependency metadata could still allow `simplebroker-pg<1.1.0` through the
`dev` or `all` extras even though `pg` is correct.

Correction: Task 0 requires checking and tightening those extras before relying
on the PG hook.

Second-pass issue found: `HeartbeatTask` already has a custom wait loop and
would not benefit from only changing `MultiQueueWatcher`.

Correction: Task 3 explicitly removes heartbeat's per-queue waiter list and
uses the shared wait seam without changing heartbeat scheduling semantics.

Second-pass issue found: CLI wait surfaces use `QueueChangeMonitor`, not
`MultiQueueWatcher`.

Correction: Task 4 wires the same SimpleBroker API into `QueueChangeMonitor`
with the existing `QueueWatcher` path as fallback.

Final scope check:

- The plan remains aligned with the discussed direction.
- It uses the new SimpleBroker primitive without PG coupling in Weft.
- It improves the real task/manager loop rather than only a secondary watcher
  path.
- It keeps SQLite behavior safe and conservative.
- It does not try to solve global database connection limits.

Stop and re-plan if implementation discovers that SimpleBroker's public helper
cannot be used from Weft without private SimpleBroker imports, or if the change
requires modifying queue payloads, TaskSpec fields, manager ownership, or
reserved-policy behavior. That would be a materially different direction.
