# Task Log Cursor High-Water Mark Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Fix repeated global task-log rescans in follow/wait paths that watch one known
task while unrelated tasks are also writing to `weft.log.tasks`.

The bug is not that these paths read the global task log. The global task log
is still the durable lifecycle stream. The bug is that their cursor only moves
when an event matches the target TID. If unrelated events arrive after the
target task's last event, each poll can scan the same unrelated suffix again.

This plan addresses two findings:

- Finding 2: `weft.commands._streaming.poll_log_events()` does not advance the
  log high-water mark over unrelated events, so `Task.result()` and
  `weft result` can repeatedly rescan unrelated log events.
- Finding 4: `weft.commands.events.iter_task_events()` has the same cursor
  behavior in follow mode, so `Task.events(follow=True)` can repeatedly rescan
  unrelated log events while the target task is quiet.

The desired behavior is simple:

- scan new task-log records once
- return only records for the target TID
- advance the caller's cursor to the highest timestamp scanned, even when no
  target-TID event matched
- preserve existing terminal-state and timeout semantics

## 2. Source Documents

Read these before implementing:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
  - Task lifecycle truth lives in `weft.log.tasks`.
  - `weft result` and `weft run` share completion wait semantics.
- `docs/specifications/07-System_Invariants.md` [OBS.1], [OBS.2], [OBS.3]
  - Lifecycle changes are written to `weft.log.tasks`.
  - Weft does not use a separate task lifecycle database.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2]
  - Result/status/task-facing CLI behavior.
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-2.1], [DJ-2.2]
  - Public client follow/result APIs and timeout behavior.
- `docs/agent-context/engineering-principles.md`
  - Especially "Queues Are the Canonical State" and "Secondary Rules".
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`
  - Especially "For Append-Only Queues, Iterate by Generator, Not Fixed
    Limits".
- `docs/agent-context/runbooks/testing-patterns.md`
  - Use real broker-backed queues for queue semantics.
- `AGENTS.md`
  - Project-specific coding, testing, and style rules.

Related plans:

- `docs/plans/2026-04-30-known-tid-terminal-snapshot-api-plan.md`
  addresses known-TID snapshot and list/stats replay issues. This plan is
  separate on purpose. Do not merge the concerns during implementation.

## 3. Current Code Shape

Owner files:

- `weft/commands/_streaming.py`
  - `poll_log_events(log_queue, last_timestamp, target_tid)` scans
    `weft.log.tasks` and returns events for one target task.
  - It currently updates `last_timestamp` only when target-TID events were
    found.
- `weft/commands/_result_wait.py`
  - `await_one_shot_result()` calls `poll_log_events()`.
  - This powers `Task.result()` and non-streaming `weft result` for one-shot
    tasks.
- `weft/commands/result.py`
  - `_await_result_materialization()` and `_await_single_result()` call
    `poll_log_events()`.
  - These paths can poll repeatedly while waiting for task metadata, result
    queues, persistent-task boundaries, or terminal events.
- `weft/commands/events.py`
  - `iter_task_events()` currently tracks `last_timestamp` only from matching
    target-TID events.
  - `follow_task_events()` depends on `iter_task_events(follow=True)`.
  - `iter_task_realtime_events()` has the same log-cursor bug, but it is
    intentionally deferred. The realtime iterator also interleaves outbox and
    ctrl-out cursors, cancellation, terminal result peeking, and synthetic
    snapshot emission. Fixing it safely needs a separate focused plan and test
    matrix.

Tests likely to touch:

- `tests/commands/test_result.py`
- `tests/commands/test_task_commands.py` if existing task/client tests are a
  better fit for public API coverage
- `tests/commands/test_status.py` only if existing helpers for event seeding
  live there; do not add status-specific coverage for this bug unless needed
- `tests/core/test_client.py` for a thin public `Task.events()` /
  `Task.result()` regression if a command-level test cannot cover the public
  contract cleanly

Current behavior to preserve:

- `poll_log_events()` returns only matching target-TID events.
- `poll_log_events()` ignores malformed/non-dict queue messages through the
  existing `iter_queue_json_entries()` helper.
- Result waiters still use task-local outbox/ctrl-out plus log events.
- Completion grace behavior stays unchanged.
- Follow iterators still stop on terminal state, time out according to the
  caller's local timeout, and do not mutate task lifecycle state.

## 4. Non-Goals

Do not implement any of these in this slice:

- A task-log index, cache queue, SQLite table, materialized view, or background
  compactor.
- A replacement for `weft.log.tasks` as the audit stream.
- A broader known-TID status optimization. That belongs to
  `2026-04-30-known-tid-terminal-snapshot-api-plan.md`.
- Realtime event cursor repair in `iter_task_realtime_events()`. The repeated
  log scan exists there too, but it is out of scope for this plan because the
  realtime iterator has additional outbox/ctrl-out/cancellation behavior to
  verify.
- Changes to task state transitions, terminal-state classification, result
  payload shapes, or outbox consumption rules.
- Fixed-size `peek_many(limit=...)` correctness reads.
- New dependencies.
- Broad refactors of result waiting, event following, streaming, or realtime
  event code.

## 5. Invariants

Preserve these invariants:

- `weft.log.tasks` remains the durable lifecycle log and audit surface.
- Queue history reads for append-only logs use generator-based reads, not fixed
  limits.
- Cursors are monotonic. A helper must never return a new cursor lower than the
  cursor it was given.
- Matching semantics do not change. Helpers return only events whose
  `payload["tid"] == target_tid`.
- Terminal semantics do not change. A target task still reaches terminal state
  only from its own matching terminal event or from existing result/outbox
  logic.
- Local timeout budgets remain local wait budgets. They must not publish task
  timeout lifecycle state.
- Non-matching task events must never cause a target task to appear completed,
  failed, cancelled, killed, or timed out.
- Public follow/result APIs must not consume additional queues because of this
  fix. This plan changes only task-log cursor advancement.

Stop and re-plan if:

- the implementation starts filtering at the broker level by task ID through a
  new custom index or store
- a helper starts treating unrelated task events as target task state
- result wait behavior changes around completion grace, outbox consumption, or
  ctrl-out handling
- event following begins dropping matching target events that arrive after
  unrelated events
- tests need heavy fake broker implementations to pass
- the change starts pulling in the known-TID terminal snapshot plan or list/stats
  work
- the change starts modifying `iter_task_realtime_events()` without a separate
  scoped realtime plan and tests

## 6. Design

Use one shared cursor model for target-filtered task-log scans:

```text
input cursor  -> scan all queue entries after cursor
              -> collect target-TID events
              -> remember highest scanned timestamp across all valid entries
              -> return target events plus highest scanned timestamp
```

For `poll_log_events()`:

- Keep the public function name and signature if possible:
  `poll_log_events(log_queue, last_timestamp, target_tid)`.
- Change its meaning of returned `last_timestamp`:
  - old: timestamp of the last matching target event, or unchanged if none
  - new: highest scanned log timestamp, or unchanged if no entries were scanned
- Keep returning `events` as only target-TID events.
- Use `since_timestamp=last_timestamp` or the existing inclusive/exclusive
  convention consistently with the caller. Preserve the current guard:
  `if last_timestamp is not None and timestamp <= last_timestamp: continue`.
- Do not "harmonize" timestamp exclusivity across call sites in this slice.
  `poll_log_events()` currently gets exclusive-after behavior by passing
  `since_timestamp=last_timestamp` and keeping the explicit
  `timestamp <= last_timestamp` skip guard. `iter_task_events()` currently gets
  exclusive-after behavior by passing `since_timestamp=last_timestamp + 1`.
  Keep those local conventions unless a focused test proves one is wrong.
- Track a separate local variable such as `max_scanned_timestamp`.
- At the end, return:
  `max_scanned_timestamp if max_scanned_timestamp is not None else last_timestamp`.
  Do not use truthiness because timestamp `0` should still be handled
  correctly if a test or backend ever produces it.

For `iter_task_events()`:

- Apply the same high-water rule inside the follow loop.
- The loop may keep one variable named `last_timestamp`, but it must represent
  the highest scanned timestamp, not the last matching target event.
- Matching target events are still yielded.
- If a terminal target event is seen, return after yielding it.
- If only unrelated events are seen and `follow=True`, the iterator should not
  yield anything, but it should advance the cursor and wait for later changes.
- Keep the existing `last_timestamp + 1` call-site convention in
  `iter_task_events()` unless a test proves an off-by-one bug. This plan is
  about the value stored in `last_timestamp`, not about changing broker
  timestamp inclusivity.

Optional small helper:

- If implementation duplication appears between `_streaming.py` and
  `events.py`, add a narrow helper such as
  `iter_target_log_events_with_cursor(queue, since_timestamp, target_tid)`.
- Put it only where it improves DRY without creating a new abstraction layer.
  Likely homes:
  - `weft/commands/_streaming.py` if only command wait/event helpers need it
  - a new private module only if import direction stays clean and tests prove
    reuse is worthwhile
- Do not create a public event-store API in this slice.

Recommended simplest design:

1. First fix `poll_log_events()` directly.
2. Add tests.
3. Then fix `iter_task_events()` directly or extract the small helper if the
   second fix would copy the same cursor logic.
4. Do not extract before the second caller proves the duplication is real.

## 7. Code Style

Follow local style:

- Keep `from __future__ import annotations` at the top of Python files.
- Use stdlib imports, then third-party imports, then local imports.
- Use modern typing: `str | None`, `list[tuple[dict[str, Any], int]]`.
- Use `collections.abc` for abstract types if new abstract types are needed.
- Avoid `Optional`, `List`, `Dict`.
- Add docstrings only where they clarify the cursor boundary. If you change
  `poll_log_events()`, update its docstring to state that the returned cursor
  is the highest scanned timestamp, not the last matching timestamp.
- Keep helper names explicit and dull. Good:
  `_advance_task_log_cursor`. Bad: `_smart_scan`.
- Do not add new constants unless a repeated magic value appears in production
  code. Test-only values should stay local to tests.
- Do not add comments that narrate obvious code. A short comment explaining
  inclusive timestamp handling is acceptable if it prevents off-by-one bugs.

## 8. Tasks

1. Add red tests for `poll_log_events()` high-water cursor behavior.
   - Files:
     - `tests/commands/test_result.py`
   - Put these tests in `tests/commands/test_result.py` because that file
     already owns result-helper behavior and result wait regressions. Do not
     split this coverage into `test_task_commands.py` unless existing fixtures
     make `test_result.py` impossible.
   - Use a real broker-backed queue from the existing test fixtures. If no
     fixture is close, add a small local helper that creates a `WeftContext`
     or `Queue` against a temporary broker DB. Do not fake SimpleBroker queue
     behavior.
   - Test setup:
     - choose a target TID that is lower than the broker timestamps that will
       be assigned to the test messages. A safe pattern is
       `target_tid = str(time.time_ns())`, then write the queue messages after
       that value is captured.
     - choose one or two unrelated TIDs. Their payload TID values do not
       control queue ordering.
     - write JSON log messages to the queue in order:
       - target running event
       - unrelated running event
       - unrelated completed event
     - inspect the queue with `peek_generator(with_timestamps=True)` or
       `iter_queue_json_entries()` to capture the broker-assigned message
       timestamps. Do not assume the payload `tid` or a payload `timestamp`
       field equals the message timestamp.
     - call `poll_log_events(log_queue, None, target_tid)` and assert:
       - returned events contain only the target event
       - returned cursor equals the highest scanned broker message timestamp,
         not the target event's broker message timestamp
     - call `poll_log_events(log_queue, returned_cursor, target_tid)` and
       assert:
       - no events are returned
       - cursor remains unchanged
   - Add a second red test for "no target events":
     - write only unrelated events
     - capture their broker-assigned timestamps from the queue
     - call `poll_log_events(log_queue, None, target_tid)`
     - assert no events and cursor advances to the highest unrelated broker
       message timestamp
   - Gate:
     - The tests must fail on the current implementation because the cursor
       remains on the last target event or `None`.

2. Implement the `poll_log_events()` cursor fix.
   - Files:
     - `weft/commands/_streaming.py`
   - Update `poll_log_events()` so returned `last_timestamp` is the highest
     scanned timestamp across all valid JSON object entries, not only matching
     target entries.
   - Keep returned `events` filtered to `target_tid`.
   - Preserve the existing `timestamp <= last_timestamp` skip rule.
   - Update the docstring to make the cursor semantics explicit.
   - Run focused tests:
     - `./.venv/bin/python -m pytest tests/commands/test_result.py -q -n 0`
     - or the exact test file where the red tests were added
   - DRY gate:
     - Do not create a broad abstraction yet unless the event-follower task
       would otherwise duplicate non-trivial logic.
   - YAGNI gate:
     - Do not change `_await_result_materialization()`,
       `await_one_shot_result()`, or `_await_single_result()` unless tests show
       they have caller-specific assumptions about the returned cursor.
     - The cursor propagated through
       `ResultMaterialization.log_last_timestamp` into downstream waiters is
       intentionally more advanced after this fix. That is a semantic no-op for
       the target task and reduces rescanning. Do not investigate it as a
       behavior change unless a test shows a real target event is skipped.
   - Done when result helper tests pass and existing result semantics are
     unchanged except for cursor advancement.

3. Add red tests for `iter_task_events()` follow cursor behavior.
   - Files:
     - `tests/commands/test_task_commands.py`, `tests/commands/test_result.py`,
       or `tests/core/test_client.py`
     - Prefer the command-level helper test if it can exercise
       `events.iter_task_events()` directly with a real queue.
   - Use a real broker-backed `WeftContext` and write directly to
     `weft.log.tasks`.
   - Test the internal command helper first:
     - create a target TID with `str(time.time_ns())`, then write log messages
       after that value is captured so `iter_task_events()` starts before those
       message timestamps
     - write a target `running` event
     - write several unrelated events after it
     - capture the broker-assigned timestamp of the last unrelated event
     - monkeypatch `events.QueueChangeMonitor` with a no-sleep monitor whose
       `wait()` returns immediately. Do not replace the queue itself.
     - instantiate `iter_task_events(context, target_tid, follow=True,
       timeout=10.0)` or another non-expiring local budget
     - consume the first yielded event and assert it is the target running
       event
     - continue the iterator only far enough to open the next scan; do not wait
       for a real timeout
   - Because repeated scans are not directly visible from output, instrument one
     narrow seam:
     - monkeypatch `events.iter_queue_json_entries` with a wrapper that delegates
       to the real helper and records each `since_timestamp`
     - after the wrapper records the second scan's `since_timestamp`, raise a
       test-local sentinel exception such as `_StopAfterSecondScan`
     - assert the next scan starts after the highest unrelated broker message
       timestamp, not after the target running event timestamp
   - Sentinel mechanics:
     - record `since_timestamp` at wrapper call time
     - return a generator wrapper around the real generator
     - on the first `next()` of the second scan's generator, raise the sentinel
       exception
     - in the test, catch the sentinel outside the `iter_task_events()` consumer
       and then explicitly close the generator so the implementation's
       `finally` cleanup runs
     - do not raise the sentinel before returning the generator, because that
       tests call setup rather than scan iteration
   - Add a non-follow test:
     - `iter_task_events(..., follow=False)` should still yield historical
       target events and return without waiting
     - non-target events should not be yielded
   - Gate:
     - Use monkeypatch only to observe `since_timestamp`, not to replace queue
       semantics.
     - The queue contents should still be real broker-backed messages.

4. Implement the `iter_task_events()` cursor fix.
   - Files:
     - `weft/commands/events.py`
   - Change `iter_task_events()` so its cursor tracks the highest scanned
     timestamp, not only matching target events.
   - Keep yielding only target-TID events.
   - Keep terminal detection based only on target-TID events.
   - Keep timeout behavior unchanged.
   - Review `follow_task_events()` after the change:
     - It should not need direct code changes.
     - If a test fails, fix the cursor boundary rather than changing
       `follow_task_events()` result semantics.
   - Run focused tests:
     - `./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q -n 0`
     - `./.venv/bin/python -m pytest tests/core/test_client.py -q -n 0` if
       client coverage was added
   - DRY gate:
     - If the final code now has two copies of the same 10+ line cursor scan,
       extract a tiny private helper and update both call sites.
     - Do not create a general event bus or log-store abstraction.
   - YAGNI gate:
     - Do not change `iter_task_realtime_events()` in this task. It has the
       same log-cursor bug, but fixing it safely belongs to a separate
       realtime-focused plan because it also coordinates outbox, ctrl-out,
       cancellation, snapshot emission, and terminal result peeking.
   - Done when event follower tests pass and target event output is unchanged.

5. Add public regression coverage for result/follow API behavior.
   - Files:
     - `tests/core/test_client.py` if public `WeftClient` tests already exist
     - otherwise add command-level tests in the files above and skip this task
       only if public coverage would duplicate the same path without adding
       signal
   - Test `Task.result()` lightly:
     - seed or run a simple completed task path if existing client fixtures make
       it cheap
     - interleave unrelated task-log events while waiting
     - assert result status/value remains correct
   - Test `Task.events(follow=True)` lightly:
     - seed log events and assert only target events are yielded
     - assert unrelated events do not produce target events
   - Do not build a slow integration test just to prove cursor internals if the
     command-level tests already prove the cursor and public wrappers are thin.
   - Test-design gate:
     - Prefer one high-signal public wrapper test over several slow end-to-end
       process tests.
     - Do not mock `Task.result()` or `Task.events()` themselves.

6. Run focused verification and update nearby docs/comments.
   - Files:
     - `weft/commands/_streaming.py`
     - `weft/commands/events.py`
     - test files touched above
     - docs only if a spec or implementation snapshot describes cursor
       semantics incorrectly
   - Required commands:
     - `./.venv/bin/python -m pytest tests/commands/test_result.py -q -n 0`
     - `./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q -n 0`
     - `./.venv/bin/python -m pytest tests/core/test_client.py -q -n 0`
       if client tests were touched
     - `./.venv/bin/python -m ruff check weft tests`
     - `./.venv/bin/python -m mypy weft`
     - `git diff --check`
   - If the touched tests live in different files, run those exact files too.
   - Use the in-repo virtualenv binaries. Do not assume global `pytest`,
     `ruff`, or `mypy`.
   - Done when:
     - cursor tests fail before the fix and pass after it
     - existing result/follow behavior still passes
     - lint/type/whitespace checks pass

## 9. Testing Strategy

Use real queue data for queue semantics.

Good tests:

- write real JSON messages into a broker-backed `weft.log.tasks` queue
- call `poll_log_events()` or `iter_task_events()` against that queue/context
- assert returned target events and returned/observed cursor positions
- use a monkeypatch wrapper only to observe `since_timestamp`

Bad tests:

- replace SimpleBroker queues with a fake list object
- mock `poll_log_events()` while testing result behavior
- mock `iter_task_events()` while testing `Task.events()`
- assert only call counts without checking returned events or cursor behavior
- add sleeps to "prove" performance

Required proofs:

- `poll_log_events()` advances the returned cursor over unrelated events.
- `poll_log_events()` returns only target-TID events.
- `poll_log_events()` returns the input cursor unchanged when no new valid
  entries were scanned.
- `iter_task_events(follow=True)` advances the scan start over unrelated
  events after the last target event.
- `iter_task_events()` yields only target-TID events.
- Unrelated terminal events do not terminate the target task's event iterator.
- Target terminal events still terminate the target task's event iterator.
- Public `Task.result()` and `Task.events()` behavior remains compatible.

Negative proofs:

- No fixed `peek_many(limit=...)` correctness read is introduced.
- No task-log index/cache/store is introduced.
- No unrelated task event changes target task status/result.
- No result/outbox/ctrl-out consumption behavior changes.
- `iter_task_realtime_events()` is not modified in this slice.

## 10. Rollout And Compatibility

This is an internal performance/correctness fix for polling cursors. It should
not change public return shapes or command output.

Backward compatibility:

- `poll_log_events()` keeps the same function signature.
- `iter_task_events()` keeps the same public behavior.
- `Task.result()`, `Task.events()`, and `Task.follow()` keep their return types
  and timeout behavior.
- Existing task-log audit semantics do not change.

Operator-visible impact:

- Result waits and event followers should stop doing repeated work when other
  tasks are writing to `weft.log.tasks`.
- No new queues, config, commands, cleanup steps, or migration are required.

Post-ship observation:

- In a busy broker, one waiting result/follow client should not repeatedly scan
  the same unrelated task-log suffix.
- Existing result and event-follow integration tests should remain stable.
