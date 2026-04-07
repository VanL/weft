# Active Control Main-Thread Ownership Plan

## Goal

Fix the post-`0.6.4` active STOP/KILL regression by removing the background
active-control poller from `Consumer` and making active control polling part of
the main task thread's runtime loop. The correct end state is:

- only the main task thread owns terminal state publication,
- only the main task thread applies reserved-queue policy,
- only the main task thread emits control ACKs for active STOP/KILL,
- host active-control responsiveness stays fast enough for `weft task stop`
  and `weft task kill`,
- and the real broker-backed lifecycle remains intact across SQLite, Postgres,
  Linux, macOS, and Windows.

This is a correctness fix first. Do not treat it as an opportunity to redesign
task control, runner plugins, or queue cleanup.

## Root Cause Summary

The source bug is the background active-control poller introduced in
`weft/core/tasks/consumer.py`.

Today, during active work:

1. `Consumer._run_task()` enters `_active_control_poller()`.
2. `_active_control_poller()` starts a background thread that opens its own
   `ctrl_in` queue and polls with `peek_many()`.
3. On exit, the main thread blocks in `worker.join()`.
4. The poller queue is not wired to a `Queue.set_stop_event(...)`, so broker
   retry waits are not interruptible.
5. The main task thread can therefore stall before it publishes terminal task
   state or completion state, even after the worker/runtime is already gone.

That architecture is wrong even when it does not hang:

- it splits active control across two execution contexts,
- it tempts future changes to let a background thread publish task state,
- and it couples shutdown correctness to broker-thread timing.

The fix must remove that architecture rather than patching around it.

## Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md) [CC-2.3], [CC-2.4], [CC-2.5], [CC-3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-2], [MF-3], [MF-5]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [STATE.1], [STATE.2], [QUEUE.4], [QUEUE.6], [EXEC.1], [OBS.1], [WORKER.7]
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-1.2], [CLI-1.3]

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Current implementation paths:

- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
- [`weft/core/runners/host.py`](../../weft/core/runners/host.py)
- [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)
- [`weft/commands/tasks.py`](../../weft/commands/tasks.py)
- [`tests/tasks/test_task_stop_sqlite_only.py`](../../tests/tasks/test_task_stop_sqlite_only.py)
- [`tests/tasks/test_command_runner_parity.py`](../../tests/tasks/test_command_runner_parity.py)
- [`tests/commands/test_task_commands.py`](../../tests/commands/test_task_commands.py)
- [`tests/cli/test_cli_result_all.py`](../../tests/cli/test_cli_result_all.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)

Relevant external implementation detail:

- [`../simplebroker/simplebroker/sbqueue.py`](../../../simplebroker/simplebroker/sbqueue.py)
- [`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py)

## Context and Key Files

### Files to Modify

- `weft/core/tasks/consumer.py`
- `weft/core/runners/host.py`
- `weft/core/tasks/sessions.py`
- `weft/commands/tasks.py`
- `weft/_constants.py` only if you need one shared internal control-poll
  interval constant
- `tests/tasks/test_task_stop_sqlite_only.py`
- `tests/tasks/test_command_runner_parity.py`
- `tests/commands/test_task_commands.py`
- `tests/cli/test_cli_result_all.py`
- `tests/cli/test_cli_run.py`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/lessons.md` if the fix teaches a durable pattern worth preserving

### Read First

- `docs/specifications/01-Core_Components.md` [CC-2.4], [CC-2.5]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3], [MF-5]
- `docs/specifications/07-System_Invariants.md` [STATE.1], [STATE.2], [QUEUE.4], [QUEUE.6], [OBS.1]
- `weft/core/tasks/consumer.py`
- `weft/core/tasks/base.py`
- `weft/core/runners/host.py`
- `weft/core/tasks/sessions.py`
- `weft/commands/tasks.py`
- `tests/helpers/weft_harness.py`
- `../simplebroker/simplebroker/sbqueue.py`
- `../simplebroker/simplebroker/db.py`

### Style and Guidance

- Follow `AGENTS.md` house style exactly.
- Use `apply_patch` for file edits.
- Keep constants in `weft/_constants.py` if you introduce a new shared timing
  value.
- Prefer using existing callbacks and helpers over creating a new abstraction.
- Do not add a new dependency.

### Shared Paths and Helpers to Reuse

- `Consumer._cancel_requested()` is the preferred seam for synchronous active
  control polling. Reuse it if possible.
- `BaseTask._handle_control_message()` remains the canonical path for
  non-active control commands and for control ACK writes.
- `Consumer._defer_active_control(...)` and
  `Consumer._finalize_deferred_active_control()` already encode the correct
  ownership split for active STOP/KILL. Preserve that idea rather than creating
  a second terminal-state path.
- `HostTaskRunner.run_with_hooks(...)` is the canonical host one-shot polling
  loop.
- `AgentSession.execute(...)` is the canonical long-lived session polling loop.
- `tests/helpers/weft_harness.py` is the canonical real broker/process harness.

### Current Structure

- `Consumer._run_task()` currently wraps active work in `_active_control_poller()`.
- `_active_control_poller()` starts a background thread that opens its own
  `ctrl_in` queue and blocks the main task thread on `worker.join()` during
  teardown.
- `HostTaskRunner.run_with_hooks()` currently uses `monitor_interval` as both
  resource-monitor cadence and runtime wake cadence. With the default
  `polling_interval=1.0`, active STOP/KILL can be delayed for up to one second.
- `AgentSession.execute()` already wakes frequently (`0.05s`) but hard-codes
  that value instead of sharing it.
- `stop_tasks()` / `kill_tasks()` in `weft/commands/tasks.py` currently send a
  control message, wait only briefly, then may kill the runtime handle or PID
  before the consumer has durably published the terminal task state.

### Comprehension Questions

Before editing, the implementer should be able to answer these:

1. Which queue/log is the public proof that a task has reached a terminal
   state?
   Answer: `weft.log.tasks`, consumed via `status.py` snapshots.
2. Which layer currently owns reserved-queue policy and task state transitions?
   Answer: `BaseTask` / `Consumer`, not the runner plugin.
3. Why is the current active-control poller unsafe?
   Answer: it moves active control into a background thread and then blocks the
   main thread on a queue operation that is not interruptible by the same stop
   event.

## Invariants and Constraints

- Do not create a second durable execution path.
- Do not let any background thread call:
  `_handle_control_message`,
  `_handle_stop_request`,
  `_handle_kill_request`,
  `_apply_reserved_policy`,
  `_report_state_change`,
  or `_send_control_response`.
- Terminal task state must still be published by the main task thread only.
- State transitions must remain forward-only. Preserve [STATE.1] and [STATE.2].
- Reserved-queue semantics must remain unchanged. Preserve [MF-2], [QUEUE.4],
  and [QUEUE.6].
- `spec` and `io` immutability are out of scope and must not be affected.
- Keep the runner plugin surface stable for this patch. Do not widen the
  plugin protocol unless you can prove the existing `cancel_requested` seam is
  insufficient.
- Keep the public CLI shape stable.
- No new dependency.
- No queue-cleanup redesign.
- No SQLite-specific workaround that papers over the real ownership bug.
- Tests must use real broker/process paths where practical. Do not mock:
  `simplebroker.Queue`, the manager/consumer lifecycle, reservation semantics,
  or task-log observation.

## Hidden Couplings and Rollback

### Hidden Couplings

- `weft.log.tasks` is the public truth for terminal state. If the worker/runtime
  disappears before the consumer logs that state, CLI and tests see a live
  snapshot with a missing runtime.
- `weft.state.tid_mappings` may still show a runtime handle while the terminal
  state is not yet published. CLI stop/kill code must not assume that runtime
  disappearance is equivalent to durable terminal state.
- The host runner's wait cadence is currently coupled to resource monitoring.
  That is convenient but wrong for control responsiveness.
- SimpleBroker queue backoff is only interruptible when the queue has a stop
  event. The current background poller creates a fresh queue that is not wired
  into the consumer's stop event.

### Rollback

Rollback is simple only if this patch stays narrow:

- Keep the runner plugin contract unchanged.
- Keep CLI command names and arguments unchanged.
- Keep queue names and state payloads unchanged.
- Keep deferred STOP/KILL semantics inside `Consumer`, not in the runner.

If implementation pressure starts pushing toward:

- a new runner plugin callback contract,
- a new queue/control protocol,
- or a broader CLI lifecycle rewrite,

stop and re-plan before merging anything.

## Tasks

### 1. Lock the Red Proof Around the Real Bug

- Outcome:
  There is one tight root-cause regression and the existing contract tests are
  identified as non-negotiable gates.
- Files to touch:
  - `tests/tasks/test_task_stop_sqlite_only.py`
  - possibly `tests/tasks/test_command_runner_parity.py`
- Read first:
  - `tests/tasks/test_task_stop_sqlite_only.py`
  - `tests/tasks/test_command_runner_parity.py`
  - `tests/helpers/weft_harness.py`
- Required changes:
  - Keep a real broker-backed SQLite regression that proves:
    1. a host command task is running,
    2. STOP is sent during active work,
    3. terminal state becomes `cancelled`,
    4. the consumer process fully exits,
    5. `PRAGMA integrity_check` returns `ok`.
  - Use the current `test_task_stop_sqlite_only.py` as the starting point if
    it still expresses that contract cleanly. Simplify it if needed. Do not
    replace it with mocks.
  - Treat these existing tests as contract proofs that must stay green:
    - `tests/tasks/test_command_runner_parity.py::test_command_runners_stop_cancel_equivalently[host]`
    - `tests/tasks/test_command_runner_parity.py::test_command_runners_kill_mark_killed_equivalently[host]`
    - `tests/commands/test_task_commands.py`
    - `tests/cli/test_cli_result_all.py::test_result_all_json_output`
    - `tests/cli/test_cli_run.py::test_cli_run_persistent_spec_no_wait_consumes_initial_piped_stdin`
- Stop and re-evaluate if:
  - you need extensive monkeypatching to make the regression red,
  - or the new test no longer proves real task-log and SQLite durability.
- Suggested commands:

```bash
uv run pytest tests/tasks/test_task_stop_sqlite_only.py -q -n 0
uv run pytest tests/tasks/test_command_runner_parity.py -q -n 0
uv run pytest tests/commands/test_task_commands.py -q -n 0
uv run pytest tests/cli/test_cli_result_all.py tests/cli/test_cli_run.py -q -n 0
```

### 2. Remove the Background Active-Control Poller From `Consumer`

- Outcome:
  `Consumer` no longer starts a background thread for active control polling.
- Files to touch:
  - `weft/core/tasks/consumer.py`
- Read first:
  - `weft/core/tasks/consumer.py`
  - `weft/core/tasks/base.py`
  - `docs/specifications/05-Message_Flow_and_State.md` [MF-3]
- Required changes:
  - Delete `_active_control_poller()` and `_poll_control_queue_while_active()`.
  - Remove the extra thread-specific `Queue(...)` construction for active
    control polling.
  - Add one synchronous helper in `Consumer` that reads at most one active
    control message from `ctrl_in`.
  - Preferred shape:
    - for STOP/KILL: call `_defer_active_control(...)`, acknowledge/delete the
      control message, and return without publishing terminal state yet;
    - for PING/STATUS/PAUSE/RESUME/unknown commands: route through
      `_handle_control_message(...)` on the main thread.
  - Prefer reusing the normal cached queue path from the owning thread. Do not
    create a second queue object for active control unless you can prove the
    main-thread queue reuse is impossible.
  - Make `Consumer._cancel_requested()` call this synchronous poll helper
    before checking `should_stop` / `_stop_event`.
- Design rule:
  Do not move STOP/KILL terminal-state publication into the polling helper.
  Active STOP/KILL during work must stay deferred until the runtime unwinds.
- Stop and re-evaluate if:
  - you are about to add another background thread,
  - or you feel forced to move state publication into `BaseTask` for all task
    types.
- Suggested command:

```bash
uv run pytest tests/tasks/test_task_stop_sqlite_only.py -q -n 0
```

### 3. Make the Host Runtime Loop Responsive Without Widening the Runner API

- Outcome:
  Host one-shot work checks active control promptly even when
  `spec.polling_interval` is `1.0`.
- Files to touch:
  - `weft/core/runners/host.py`
  - `weft/core/tasks/sessions.py`
  - `weft/_constants.py` only if you need a shared internal poll interval
- Read first:
  - `weft/core/runners/host.py`
  - `weft/core/tasks/sessions.py`
  - `weft/_constants.py`
- Required changes:
  - Keep using the existing `cancel_requested` callback. This is the preferred
    control seam.
  - In `HostTaskRunner.run_with_hooks(...)`, separate:
    - active-control wake cadence,
    - result-queue polling cadence,
    - resource-monitor cadence.
  - The host runner must wake frequently enough for STOP/KILL to be responsive
    even when `monitor_interval` is large.
  - Recommended direction:
    - use a small internal control tick such as `0.05s`,
    - run `monitor.check_limits()` only when its own interval has elapsed,
    - keep timeout semantics unchanged,
    - keep outcome/result handling unchanged.
  - In `AgentSession.execute(...)`, replace the magic `0.05` with the same
    shared internal control tick if you introduce one.
- Do not:
  - add a new runner-plugin callback,
  - add a second task-owned poll loop,
  - or make control latency depend on monitor cadence.
- Stop and re-evaluate if:
  - you are about to change `TaskRunner.run_with_hooks(...)` or the plugin
    protocol just to carry a new polling hook.
- Suggested commands:

```bash
uv run pytest tests/tasks/test_command_runner_parity.py -q -n 0
uv run pytest tests/cli/test_cli_result_all.py tests/cli/test_cli_run.py -q -n 0
```

### 4. Keep STOP/KILL Finalization Single-Threaded and After Runtime Exit

- Outcome:
  Deferred STOP/KILL still finalizes only after the runtime loop returns and
  only on the main task thread.
- Files to touch:
  - `weft/core/tasks/consumer.py`
  - `weft/core/tasks/base.py` only if a tiny helper extraction is genuinely
    needed
- Read first:
  - `weft/core/tasks/consumer.py` `_ensure_outcome_ok(...)`
  - `weft/core/tasks/base.py` `_handle_stop_request(...)`,
    `_handle_kill_request(...)`
- Required changes:
  - Preserve the current ownership rule:
    `Consumer` finalizes deferred STOP/KILL after the runner/session returns.
  - Do not let the runner return `killed` directly for a KILL that was only
    observed through active control polling. The runner should remain a runtime
    executor, not a task-state publisher.
  - Ensure `_finalize_deferred_active_control()` is still reached on the main
    thread for both active STOP and active KILL before normal success/error
    finalization can publish a conflicting state.
  - If you simplify `_ensure_outcome_ok(...)`, keep the ordering explicit and
    test-backed.
- Fatal mistakes to avoid:
  - calling `_handle_stop_request(...)` or `_handle_kill_request(...)` from the
    active poll helper,
  - applying reserved policy before the runtime actually stops,
  - or allowing a second terminal transition after deferred STOP/KILL.
- Suggested commands:

```bash
uv run pytest tests/tasks/test_command_runner_parity.py -q -n 0
```

### 5. Tighten CLI STOP/KILL Escalation So the Consumer Publishes State First

- Outcome:
  CLI control waits for durable terminal state before it falls back to direct
  runtime or process termination.
- Files to touch:
  - `weft/commands/tasks.py`
  - `tests/commands/test_task_commands.py`
  - possibly `tests/tasks/test_command_runner_parity.py`
- Read first:
  - `weft/commands/tasks.py`
  - `weft/core/tasks/base.py` `handle_termination_signal(...)`
  - `docs/specifications/10-CLI_Interface.md` [CLI-1.3]
- Required changes:
  - `stop_tasks()` and `kill_tasks()` should still send the control message
    first.
  - After that, prefer waiting for the terminal snapshot published through
    `weft.log.tasks`.
  - Do not keep the current "half-second then escalate" behavior if the
    consumer now owns durable publication. Use a named internal timeout rather
    than a new magic number. The bounded wait should be long enough to cover
    multiple active-control ticks plus consumer finalization. Prefer something
    on the order of `2.0s`, not `0.2s` or `0.5s`.
  - If escalation is still needed:
    - for non-host runners, use the persisted runner handle;
    - for host tasks, prefer escalating the task process / task process tree
      only after giving the consumer a fair chance to publish terminal state.
  - Do not make "worker/runtime disappeared" the primary success signal.
  - Keep the public CLI shape unchanged.
- Specific caution:
  For host tasks, killing only the runtime handle too early widens the race,
  because the consumer is the process that writes the durable terminal state.
- Stop and re-evaluate if:
  - you are about to encode runner-specific semantics in CLI code beyond
    handle-vs-host fallback,
  - or you need a longer-term control protocol change.
- Suggested commands:

```bash
uv run pytest tests/commands/test_task_commands.py -q -n 0
uv run pytest tests/tasks/test_command_runner_parity.py -q -n 0
```

### 6. Update the Specs and Comments to Match the Real Ownership Model

- Outcome:
  The docs stop claiming that active control is polled from a background thread.
- Files to touch:
  - `docs/specifications/01-Core_Components.md`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `docs/specifications/10-CLI_Interface.md`
  - any touched code module/function docstrings or nearby spec comments
- Read first:
  - the touched code after Tasks 2-5 are complete
  - current spec wording in `[CC-2.4]`, `[CC-2.5]`, `[MF-3]`, `[CLI-1.3]`
- Required changes:
  - Update `MF-3` implementation mapping to describe synchronous active control
    polling on the main task thread.
  - Clarify in `CC-2.4` / `CC-2.5` that control/state publication remains
    task-owned even while the runtime is active.
  - Clarify in `CLI-1.3` that stop/kill operate through control first and only
    escalate to runner/PID fallback when needed.
  - Add or refresh local spec-reference comments/docstrings in changed code if
    the implementation mapping moved.
- Do not:
  - rewrite unrelated specs,
  - or broaden this into a runner/control architecture doc rewrite.

### 7. Run the Real Gates and Do Not Declare Success Early

- Outcome:
  The fix is proven locally and in GitHub, not just by reasoning.
- Files to touch:
  - none, unless a test exposes a real gap
- Required local gates:

```bash
uv run pytest tests/tasks/test_task_stop_sqlite_only.py -q -n 0
uv run pytest tests/tasks/test_command_runner_parity.py -q -n 0
uv run pytest tests/commands/test_task_commands.py -q -n 0
uv run pytest tests/cli/test_cli_result_all.py tests/cli/test_cli_run.py -q -n 0
uv run pytest -q -n 0
uv run mypy weft
uv run ruff check weft tests
```

- Required Linux proof if you are not already on Linux:
  Run the targeted SQLite and CLI reproductions in Docker so the original
  platform is exercised locally before trusting GitHub.

```bash
docker run --rm \
  -v /Users/van/Developer/weft:/workspace \
  -w /workspace \
  python:3.12 \
  bash -lc '
    python -m pip install -q uv >/dev/null &&
    uv pip install --system -e ".[dev]" >/dev/null &&
    pytest tests/tasks/test_task_stop_sqlite_only.py \
      tests/tasks/test_command_runner_parity.py \
      tests/cli/test_cli_result_all.py \
      tests/cli/test_cli_run.py -q -n 0
  '
```

- Required GitHub proof:
  - the `Test` workflow must pass on Linux, macOS, and Windows;
  - the `Release Gate` workflow must pass after the retagged release.

Do not consider the patch done until GitHub is green.

## Independent Review Loop

This patch crosses the durable task spine and should not rely on author
confidence alone.

- After Tasks 2-5 are implemented, ask for an independent review using
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.
- Prefer a different agent family or reviewer path than the author if one is
  available in the current environment.
- Reviewer prompt:

> Read `docs/plans/active-control-main-thread-plan.md` and the touched code.
> Look for errors, bad ideas, and latent ambiguities. Could you implement or
> sign off on this confidently and correctly?

- If no independent reviewer is available, record that limitation in the final
  notes and perform an explicit self-review against the checklist below before
  merge.

## Testing Plan

### What Must Stay Real

- `WeftTestHarness`
- actual `Consumer` processes
- real queue writes/reads against the backend
- real `weft.log.tasks` reconstruction through `status.py`
- real `stop_tasks()` / `kill_tasks()` CLI helper behavior

### What May Be Mocked Sparingly

- Runner plugins only at the narrow boundary already exercised in
  `tests/commands/test_task_commands.py` for "runner handle dispatch vs direct
  PID fallback" behavior.

### Contract Assertions to Keep

- STOP during active work yields `cancelled` after the runtime actually stops.
- KILL during active work yields `killed` after the runtime actually stops.
- The consumer process exits after terminal state.
- The broker remains valid (`PRAGMA integrity_check = ok`) in the SQLite
  regression.
- Ordinary non-control completion still writes results and terminal state.
- CLI result and list/status surfaces still observe durable terminal state.

### Test-Design Warnings

- Do not use `time.sleep(...)` as the primary proof. Poll queue-visible state.
- Do not assert internal flags without also asserting public behavior.
- Do not add a mock-only unit test for the control loop if the real broker path
  can express the behavior.

## Out of Scope

- Redesigning queue cleanup or broker lifecycle
- Redesigning the runner plugin protocol
- Changing TaskSpec schema
- Changing public CLI arguments
- Generalizing control semantics for all future runner types
- Reworking interactive-session UX unless a current regression forces it

## Fresh-Eyes Review Checklist

Before implementation is considered review-ready, re-check all of these:

1. Is there any background thread left that can publish task state, reserved
   policy, or control ACKs during active work?
   If yes, the design is still wrong.
2. Does any fix depend on worker/runtime disappearance as the success signal
   instead of the terminal snapshot in `weft.log.tasks`?
   If yes, the CLI race is still present.
3. Did the patch widen the runner plugin surface?
   If yes, justify it from first principles or revert that part.
4. Did the patch couple STOP/KILL responsiveness to `monitor_interval` again?
   If yes, host stop/kill will remain flaky.
5. Did the test strategy drift into mocks because the real broker path was
   inconvenient?
   If yes, the proof is weak.
6. Did the patch wander into queue-cleanup redesign or unrelated control
   refactors?
   If yes, trim it back.

If any answer is "yes", revise before implementation or merge.
