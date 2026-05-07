# Terminal Publication And Wrapper-Loss Hardening Plan

Status: draft
Source specs: see Source Documents below
Superseded by: none

Parent plan:
[`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
Release slice: Release 3

## 1. Goal

Harden the task write path so normal terminal outcomes publish enough durable
evidence for the Release 2 evidence reader to reconstruct coherent task state.
The main target is ordinary one-shot success: once a task has emitted a final
outbox result and marked itself completed, it should also publish a typed
task-owned terminal `ctrl_out` envelope. If the task-log terminal event is
missing because of a broker write failure or a narrow supervisor race, status
and task inspection should still have terminal proof other than the outbox
payload itself.

This release also tightens manager-authored wrapper-loss envelopes so their
shape, constants, and tests stay aligned with the shared evidence model.

This is a write-path hardening release. It does not add monitor, reaper,
archive, retention policy, queue cleanup, retries, or domain-task fixes.

## 2. Non-Goals

Do not solve these in Release 3:

- Cross-queue atomicity. Weft currently writes outbox, task log, and `ctrl_out`
  as separate queue operations. This release must not invent a transaction
  layer, add a lifecycle SQL table, or change SimpleBroker semantics.
  In practical terms, do not make success depend on one all-or-nothing commit
  across `T{tid}.outbox`, `weft.log.tasks`, `T{tid}.ctrl_out`, and
  `T{tid}.reserved`. A future SimpleBroker multi-queue transaction API may be
  useful, but that is not this Weft slice.
- Guaranteed recovery from `SIGKILL`, kernel crash, host power loss, or process
  death in the exact gap between result publication and terminal-envelope
  publication. The Release 2 `result_without_terminal` classification remains
  necessary for those cases.
- Queue cleanup. No messages should be deleted beyond the existing task-owned
  reserved-policy and cleanup behavior.
- Long-term audit storage. Broker queues are still coordination and recovery
  surfaces, not the archive sink.
- Task-domain failures. Wazuh idempotency errors, missing target files, and LLM
  schema failures remain higher-level workload issues.
- Public lifecycle-state expansion. Do not add public states such as
  `wrapper_lost`, `result_without_terminal`, or `conflicted`.

If implementation starts requiring any of those changes, stop and revisit the
architecture. That would mean this phase is drifting away from the agreed
release train.

## 3. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.2], [CC-2.3], [CC-2.4], [CC-2.5]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-5], [MF-6], "Queue Management Patterns"
- [`docs/specifications/06-Resource_Management.md`](../specifications/06-Resource_Management.md)
  [RM-1], [RM-2], [RM-5], [RM-5.1]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [STATE.2], [STATE.4], [STATE.6], [QUEUE.2], [OBS.1],
  [OBS.2], [OBS.3], [OBS.11], [EXEC.4], [IMPL.5], [IMPL.6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.2.1], [CLI-1.2]

Guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Related plans:

- [`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
  defines the release train and the Release 3 boundary.
- [`docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`](./2026-05-06-status-coherence-and-stale-pid-liveness-plan.md)
  is Release 1. It made terminal lifecycle proof win over stale runtime
  evidence in public status output.
- [`docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`](./2026-05-06-task-evidence-reconciliation-model-plan.md)
  is Release 2. It introduced shared read-only task evidence classification
  for task-log, typed terminal `ctrl_out`, final outbox, runtime mapping, and
  stale observer evidence.
- [`docs/plans/2026-04-30-known-tid-terminal-snapshot-api-plan.md`](./2026-04-30-known-tid-terminal-snapshot-api-plan.md)
  added known-TID terminal snapshot helpers that already understand typed
  terminal `ctrl_out` envelopes.
- [`docs/plans/2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md`](./2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md)
  defines important manager spawn and control boundaries that must not be
  weakened while touching manager child supervision.

Spec delta required before implementation is done:

- Update [MF-3] to explicitly say one-shot non-persistent task success
  publishes a task-owned typed terminal `ctrl_out` envelope, not only failures
  and controls.
- Update [MF-5] to say `result_without_terminal` remains a diagnostic for
  historical rows and unavoidable crash windows, but the normal success write
  path must publish terminal task-log evidence and task-local terminal
  `ctrl_out` evidence.
- Update [CLI-1.2.1] / [CLI-1.2] only if JSON diagnostics or metadata fields
  change. A reduction in new `result_without_terminal` rows does not itself
  require a CLI contract change.
- Add backlinks from touched spec sections to this plan.

## 4. Live Evidence And Release 3 Target Cases

Ops evidence from 2026-05-06 showed two Weft-owned evidence gaps after the
read-model fixes:

- A created/waiting task had a manager-authored terminal `ctrl_out` envelope:
  `status="failed"`, `error="Task wrapper exited before publishing terminal state"`.
  Release 2 now reads that coherently, but Release 3 should keep the manager
  envelope shape centralized and tested so the write path cannot drift from the
  reader.
- A one-shot `wazuh-case-rollup` task had an outbox result, no visible worker
  process, and no terminal task-log event. Release 2 can classify this as
  `result_without_terminal`. Release 3 should reduce new cases by publishing
  a task-owned terminal `ctrl_out` envelope on successful one-shot completion.

Important interpretation:

- `result_without_terminal` is not always a task failure. It is an evidence
  quality problem: outbox result exists, but terminal lifecycle publication is
  incomplete.
- Wrapper loss is not a result. It is a manager supervisor observation and must
  remain on the child `ctrl_out` queue.
- A task-owned terminal `ctrl_out` envelope is terminal observation proof. It
  is not a replacement for `weft.log.tasks`.

## 5. Current Structure And Key Files

Files to modify:

- `weft/core/tasks/consumer.py`
  - Main implementation file for this release.
  - `_execute_work_item()` currently emits the result, then calls
    `_finalize_message()`.
  - `_finalize_message()` currently marks one-shot success as completed and
    writes `work_completed`, but it does not publish a task-owned terminal
    `ctrl_out` envelope on success.
  - `_finalize_terminal_outcome()` already publishes terminal `ctrl_out` for
    failure, timeout, limit, and cancelled outcomes. Preserve that behavior.
- `weft/core/tasks/base.py`
  - Owns `_send_terminal_envelope()` and control terminal behavior.
  - Touch only if centralizing literals to constants or adding a tiny helper
    that removes real duplication. Do not move Consumer-specific finalization
    here unless the shared behavior is already present in multiple subclasses.
- `weft/core/manager.py`
  - Owns `_child_terminal_proof_visible()` and
    `_write_manager_terminal_envelope()`.
  - Use constants from `weft/_constants.py` instead of repeating envelope type
    and wrapper-loss strings.
  - Preserve the layer boundary: core code must not import
    `weft.commands.task_evidence`.
- `weft/_constants.py`
  - Already owns `TERMINAL_ENVELOPE_TYPE`, `TASK_TERMINAL_ENVELOPE_SOURCES`,
    `TERMINAL_TASK_STATUSES`, and `WRAPPER_LOST_ERROR`.
  - Add constants only if implementation discovers another repeated policy
    literal. Do not move logic here.
- `weft/commands/task_evidence.py`
  - Read-only evidence model from Release 2.
  - Touch only if a test proves its classification needs a narrow update for
    task-owned success envelopes. It should already accept terminal envelopes
    from source `task`.
- `weft/commands/system.py`, `weft/commands/tasks.py`,
  `weft/commands/result.py`, `weft/commands/_result_wait.py`
  - Avoid changes unless a failing integration test proves a command surface
    mishandles the new task-owned success envelope.

Tests to modify or add:

- `tests/tasks/test_task_execution.py`
  - Main Consumer success-path tests.
  - Add tests that execute real work through a real isolated broker and inspect
    `T{tid}.ctrl_out`.
- `tests/tasks/test_task_observability.py`
  - Best place for edge tests around state publication and best-effort
    terminal envelope behavior.
- `tests/core/test_manager.py`
  - Manager wrapper-loss envelope shape, constants, and proof guard tests.
- `tests/commands/test_task_evidence.py`
  - Add or adjust only if the shared evidence reader needs a direct regression
    for task-owned success `ctrl_out` evidence.
- `tests/commands/test_status.py` and `tests/commands/test_task_commands.py`
  - Add integration coverage only if status or task command output changes.
    Do not duplicate command-level tests if `task_evidence` already covers the
    interpretation.

Docs to update during implementation:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md` if an invariant is clarified
- `docs/specifications/10-CLI_Interface.md` only if public JSON shape changes
- `docs/lessons.md` only if implementation exposes a durable engineering lesson

Files to read before editing:

- `weft/core/tasks/consumer.py`
  - `_begin_work_item()`
  - `_execute_work_item()`
  - `_finalize_message()`
  - `_emit_result()` and `_emit_single_output()`
  - `_finalize_terminal_outcome()`
  - `_ensure_outcome_ok()`
  - `_handle_external_stop()` and `_handle_external_kill()`
- `weft/core/tasks/base.py`
  - `_report_state_change()`
  - `_send_terminal_envelope()`
  - `_handle_stop_request()`
  - `_handle_kill_request()`
  - `_emit_pipeline_terminal_event()`
- `weft/core/manager.py`
  - `_child_terminal_proof_visible()`
  - `_write_manager_terminal_envelope()`
  - `_cleanup_children()`
- `weft/commands/task_evidence.py`
  - `coerce_terminal_envelope()`
  - `peek_terminal_ctrl_out_evidence()`
  - `task_local_terminal_evidence()`
  - `known_tid_evidence()`
- `tests/tasks/test_task_execution.py`
  - Existing success, failure, start-token, and reserved-policy tests.
- `tests/core/test_manager.py`
  - `test_manager_terminal_envelope_does_not_cache_child_ctrl_out_queue()`.

Comprehension checks before editing:

- What queue is durable lifecycle truth? Answer: `weft.log.tasks`.
- What queue carries user-visible result payloads? Answer: `T{tid}.outbox`.
- What queue carries task-local control replies and terminal observation
  envelopes? Answer: `T{tid}.ctrl_out`.
- Which method writes task-log events? Answer:
  `BaseTask._report_state_change()`.
- Which method writes typed task terminal envelopes? Answer:
  `BaseTask._send_terminal_envelope()`.
- Which method should get the one-shot success call to
  `_send_terminal_envelope()`? Answer: `Consumer._finalize_message()`, inside
  the non-persistent success branch after `taskspec.mark_completed()`.
- Why should persistent tasks not publish a terminal envelope for every work
  item? Answer: `work_item_completed` is not terminal task completion. The
  persistent task remains running and waits for more inbox work.
- Can core code import `weft.commands.task_evidence` to reuse parsing? Answer:
  no. That would invert the core -> commands dependency boundary.

## 6. Target Contract

For a one-shot, non-persistent task that completes successfully:

- The task writes the final outbox result as it does today.
- The task clears or finalizes its reserved message according to the existing
  success path.
- The task calls `taskspec.mark_completed(return_code=0)`.
- The task writes `work_completed` to `weft.log.tasks`.
- The task writes one typed task-owned terminal envelope to `T{tid}.ctrl_out`:

```json
{
  "type": "terminal",
  "source": "task",
  "tid": "<tid>",
  "status": "completed",
  "timestamp": 1770000000000000000,
  "return_code": 0
}
```

- The task emits pipeline terminal metadata exactly as it does today.
- The task updates process title, performs existing cleanup, ends streaming
  session state, and stops.

For failure, timeout, limit violation, cancellation, STOP, KILL, and external
signals:

- Existing terminal-envelope behavior must remain intact.
- Do not add duplicate task-owned terminal envelopes for the same terminal
  transition.
- Do not change reserved-policy behavior.

For persistent tasks:

- `work_item_completed` remains a per-message event.
- The task must not publish a task-owned terminal envelope for each
  successful work item.
- A persistent task should publish terminal envelope only when the task itself
  becomes terminal through existing stop, kill, signal, failure, or future
  explicit shutdown paths.

For manager wrapper loss:

- The manager writes a manager-owned typed terminal envelope to the child
  `ctrl_out` queue only when no task-owned terminal proof and no terminal
  task-log proof is visible.
- The envelope uses the same constants as the reader:
  `TERMINAL_ENVELOPE_TYPE` and `WRAPPER_LOST_ERROR`.
- The manager never writes wrapper-loss observations to the child outbox.

## 7. Engineering Rules For The Implementer

Use red-green TDD:

1. Write the smallest failing test for one behavior.
2. Run that test and confirm it fails for the expected reason.
3. Make the smallest implementation change.
4. Run the focused test until it passes.
5. Run the nearby test file.
6. Refactor only after the behavior is green.

Prefer real broker-backed tests:

- Use existing `broker_env`, `task_factory`, or `WeftTestHarness` fixtures.
- Exercise `Consumer` against real isolated queues.
- Inspect queue messages with `peek_one()`, `peek_many()`, or generator helpers.
- Do not mock `Queue` for normal Consumer behavior.

Use mocks only for hard-to-produce failure injection:

- It is acceptable to monkeypatch `_report_state_change()` or
  `_ctrl_out_queue.write()` in a tightly scoped test when the goal is to
  simulate a broker failure window that SimpleBroker cannot produce
  deterministically.
- Do not mock `TaskSpec`, `Consumer`, manager state, or the evidence reader
  just to avoid setting up queues.
- Do not assert private call counts when the observable queue payload proves
  behavior.

Keep the design small:

- Reuse `_send_terminal_envelope()`.
- Reuse constants in `weft/_constants.py`.
- Do not create a new state-machine abstraction.
- Do not create a new terminal-envelope model class unless a concrete
  duplication problem appears in more than one layer and cannot be solved with
  constants.
- Do not add a cleanup service, monitor, or background task.
- Do not move command evidence helpers into core. If a helper is needed in
  core, keep it core-owned and layer-neutral.

Use repo style:

- `from __future__ import annotations` stays at the top of Python files.
- Imports stay grouped stdlib, third-party, local.
- Prefer `Path`, modern type hints, and existing constants.
- Keep comments sparse. Add a comment only where ordering matters and the code
  would otherwise invite a wrong refactor.
- Avoid broad exception handling except at process or broker boundaries with
  existing pragma style.

## 8. Invariants And Gates

These invariants must pass before this release can be called done:

- Public task states remain only:
  `created`, `spawning`, `running`, `completed`, `failed`, `timeout`,
  `cancelled`, `killed`.
- A completed one-shot task has terminal task-local proof in `ctrl_out` under
  the normal success path.
- Persistent successful work items do not publish terminal task-local proof
  because the task remains running.
- Failure, timeout, limit, stop, kill, and signal paths still publish terminal
  task-local proof exactly once for the task terminal transition.
- Manager wrapper-loss envelopes are supervisor observations in child
  `ctrl_out`, not outbox results.
- Manager wrapper-loss uses the same envelope type and error constant as the
  shared evidence reader.
- Release 2 classifications still work:
  - terminal task-log proof wins when present
  - task-owned terminal `ctrl_out` classifies as `terminal_ctrl_out` when log
    terminal proof is missing
  - manager-owned wrapper-loss classifies as `wrapper_lost`
  - final outbox without terminal proof remains `result_without_terminal`
- No test depends on host PID numbers, ops-specific task names, Docker
  container IDs, Wazuh, or governance project paths.
- No command consumes or deletes messages while merely inspecting status or
  evidence.

Hard gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_observability.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py -q
./.venv/bin/python -m pytest tests/commands/test_status.py -q
./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q
./.venv/bin/python -m mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
```

If time is tight during implementation, run the focused tests first, but do not
ship until the hard gates pass.

## 9. Bite-Sized Implementation Tasks

### Task 0: Bootstrap And Baseline

Owner: implementing engineer.

Files to read:

- `AGENTS.md`
- `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3], [MF-5]
- `docs/specifications/07-System_Invariants.md` [STATE.1], [STATE.2],
  [STATE.6], [OBS.1], [OBS.3]
- `docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`
- `weft/core/tasks/consumer.py`
- `weft/core/tasks/base.py`
- `weft/core/manager.py`
- `weft/commands/task_evidence.py`

Actions:

1. Source the repo environment:

   ```bash
   . ./.envrc
   ```

2. Run the smallest baseline suite before editing:

   ```bash
   ./.venv/bin/python -m pytest tests/tasks/test_task_execution.py tests/core/test_manager.py tests/commands/test_task_evidence.py -q
   ```

3. If baseline fails, stop and identify whether the failure is pre-existing.
   Do not build Release 3 on a failing unknown baseline.

Expected result:

- You know the current tests pass or you have documented a pre-existing
  blocker.

### Task 1: Add A Red Test For One-Shot Success Terminal Envelope

Owner: implementing engineer.

File to touch:

- `tests/tasks/test_task_execution.py`

Test design:

- Use a real isolated broker through `broker_env`.
- Use an existing helper such as `make_function_taskspec()` or
  `make_command_taskspec()`.
- Execute a non-persistent one-shot task through the real Consumer path:
  `task._drain_queue()` with an inbox message, or `task.run_work_item()` if the
  test does not need reserved queue behavior.
- Inspect `spec.io.control["ctrl_out"]`.
- Decode JSON messages and filter for typed terminal envelopes:
  `type == "terminal"`, `source == "task"`, `tid == unique_tid`.

Required assertions:

- Exactly one task-owned terminal envelope exists for a successful one-shot
  completion.
- Envelope status is `"completed"`.
- Envelope return code is `0`.
- Outbox still contains the expected result.
- `weft.log.tasks` still contains `work_completed`.
- The task state is `completed`.

Expected red failure before implementation:

- No task-owned terminal envelope exists on successful one-shot completion.

Do not:

- Assert the absolute timestamp value.
- Mock `_send_terminal_envelope()`.
- Read from `ctrl_out` destructively if later assertions need the same queue.
  Prefer `peek_many()`.

### Task 2: Implement One-Shot Success Terminal Publication

Owner: implementing engineer.

File to touch:

- `weft/core/tasks/consumer.py`

Implementation:

- In `Consumer._finalize_message()`, inside the non-persistent success branch,
  call `self._send_terminal_envelope()` after:

  ```python
  self.taskspec.mark_completed(return_code=0)
  self._clear_activity()
  self._report_state_change(event="work_completed", ...)
  ```

- Place the call before `_emit_pipeline_terminal_event(status="completed")`
  unless a focused test shows a better ordering. This keeps task-local terminal
  proof near task-log terminal proof and before pipeline-owner notification.
- Do not call `_send_terminal_envelope()` in the persistent
  `work_item_completed` branch.
- Do not change `_emit_result()` ordering in this task.
- Do not wrap the whole success path in a broad `except`.

Suggested final ordering:

1. ack/delete reserved message if present
2. monitor resource usage
3. persistent branch returns after `work_item_completed`
4. `taskspec.mark_completed(return_code=0)`
5. clear activity
6. `work_completed` task-log event
7. task-owned terminal `ctrl_out` envelope
8. pipeline terminal event
9. process title and cleanup
10. end streaming session
11. stop event

Why this order:

- Outbox is already written before `_finalize_message()`.
- Task-log remains primary lifecycle truth.
- `ctrl_out` becomes secondary terminal proof if the task-log write fails
  best-effort or if a reader sees `ctrl_out` before global log replay catches
  up.
- Pipeline notification should not be the first terminal proof for ordinary
  task inspection.

Run:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q
```

Expected result:

- The new red test is green.
- Existing failure and reserved-policy tests remain green.

### Task 3: Add A Regression Test For Persistent Work Items

Owner: implementing engineer.

File to touch:

- `tests/tasks/test_task_execution.py` or the existing persistent-task test
  file if one has a better local helper.

Test design:

- Build a persistent task using existing helpers.
- Execute one successful work item.
- Assert `work_item_completed` exists in `weft.log.tasks`.
- Assert the task status remains `running` or the existing persistent status.
- Assert there is no typed task-owned terminal envelope with
  `status="completed"` in `ctrl_out` for that work item.

Expected behavior:

- This should pass after Task 2 if the implementation is scoped correctly.
- If it fails, the success envelope was added too broadly.

Do not:

- Invent a fake persistent task model.
- Assert that `ctrl_out` is empty. Persistent tasks may have ordinary control
  replies or stream messages. Filter only typed terminal envelopes.

### Task 4: Add A Regression Test For Log-Missing But Ctrl-Out-Present Success

Owner: implementing engineer.

Files to touch:

- Prefer `tests/tasks/test_task_observability.py` for the write-path edge.
- Optionally add one focused read-model assertion in
  `tests/commands/test_task_evidence.py` if no existing test covers task-owned
  terminal `ctrl_out` success.

Test design:

- Use real queues for outbox and `ctrl_out`.
- Use a narrow monkeypatch only for the task-log write surface. Good options:
  - monkeypatch `task._report_state_change` so `work_completed` is skipped
    while other task logic remains real
  - or monkeypatch the global log queue write if the existing fixture exposes a
    narrow way to do that and lets `_report_state_change()` swallow the broker
    failure as it does in production
- Execute a successful one-shot task.
- Assert outbox result exists.
- Assert task-owned terminal `ctrl_out` envelope exists with
  `status="completed"`.
- If adding a read-model assertion, call `task_evidence.task_local_terminal_evidence()`
  or `known_tid_evidence()` and assert classification is `terminal_ctrl_out`,
  not `result_without_terminal`.

Boundaries:

- This is the one place where a monkeypatch is acceptable. The system needs a
  deterministic way to simulate a broker/log publication gap.
- Do not make `_report_state_change()` raise directly in this test. The real
  method catches broker write failures and continues, so a raising monkeypatch
  would test an artificial control flow and could prevent the terminal envelope
  call from running.
- Do not mock the evidence reader result. Write actual queue messages and read
  them.
- Do not make `_report_state_change()` failure swallowing broader unless a test
  proves that is necessary and safe.

Expected result:

- The normal write path now leaves terminal proof in `ctrl_out` even when the
  terminal task-log event is not visible.

### Task 5: Centralize Manager Wrapper-Loss Envelope Constants

Owner: implementing engineer.

Files to touch:

- `weft/core/manager.py`
- `tests/core/test_manager.py`

Implementation:

- Import and use existing constants from `weft/_constants.py`:
  - `TERMINAL_ENVELOPE_TYPE`
  - `WRAPPER_LOST_ERROR`
  - optionally `STATUS_FAILED` if it keeps status literals consistent without
    making imports noisy
- In `_write_manager_terminal_envelope()`, replace the literal
  `"terminal"` and wrapper-loss error string with constants.
- In `_child_terminal_proof_visible()`, replace the literal terminal envelope
  type with `TERMINAL_ENVELOPE_TYPE`.
- Keep `source="manager"` and `source="task"` as plain strings unless a real
  duplication problem appears. `TASK_TERMINAL_ENVELOPE_SOURCES` is a reader
  acceptance set, not a source enum.

Tests:

- Extend `test_manager_terminal_envelope_does_not_cache_child_ctrl_out_queue()`
  to assert:
  - `payload["type"] == TERMINAL_ENVELOPE_TYPE`
  - `payload["error"] == WRAPPER_LOST_ERROR`
  - `payload["source"] == "manager"`
  - `payload["status"] == "failed"`
  - `return_code` is present when child exit code is present
- Add or extend a test that preloads a task-owned terminal `ctrl_out` envelope
  and asserts `_write_manager_terminal_envelope()` writes nothing.
  Existing `_child_terminal_proof_visible()` behavior may already cover this;
  keep the test minimal.

Do not:

- Import `weft.commands.task_evidence` from `weft/core/manager.py`.
- Change manager child cleanup semantics.
- Treat an existing manager-authored envelope as task-owned terminal proof
  unless a separate duplicate-write bug is proven. That would be a policy
  change and needs its own test.

Run:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py -q
```

### Task 6: Check Failure, Stop, Kill, Timeout, And Limit Paths

Owner: implementing engineer.

Files to inspect first:

- `tests/tasks/test_task_execution.py`
- `tests/tasks/test_task_observability.py`
- `tests/tasks/test_control_channel.py`
- timeout or resource-limit test files found by:

  ```bash
  rg -n "timeout|limit|work_limit|work_timeout|control_stop|control_kill|task_signal" tests
  ```

Actions:

1. Identify existing tests that already assert terminal `ctrl_out` for failure,
   stop, kill, timeout, or limit.
2. Add only the missing coverage. Do not duplicate the same behavior across
   four files.
3. If adding tests, filter typed terminal envelopes and assert exactly one
   envelope for the terminal transition.

Minimum expected coverage:

- successful one-shot completion publishes task-owned completed envelope
- failure path still publishes failed envelope
- STOP/CANCEL path still publishes cancelled envelope
- manager wrapper-loss remains manager-owned failed envelope
- persistent per-item success does not publish completed terminal envelope

Do not:

- Overfit to process timing.
- Use sleeps for correctness unless an existing test harness already wraps a
  polling wait with a timeout.
- Add a giant parametrized test that becomes hard to debug. Prefer two or
  three focused tests if the setup differs.

### Task 7: Verify Command-Surface Reconciliation Still Works

Owner: implementing engineer.

Files to touch only if needed:

- `tests/commands/test_task_evidence.py`
- `tests/commands/test_status.py`
- `tests/commands/test_task_commands.py`

Actions:

1. Run existing Release 2 tests:

   ```bash
   ./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/commands/test_status.py tests/commands/test_task_commands.py -q
   ```

2. If they pass without command changes, do not edit command code.
3. If a command test fails because task-owned success terminal envelopes are
   ignored, add one focused test to `tests/commands/test_task_evidence.py`
   that writes:
   - a non-terminal or stale task-log event
   - an outbox result
   - a task-owned terminal `ctrl_out` envelope with `status="completed"`
   Then assert `known_tid_evidence()` or `task_local_terminal_evidence()`
   classifies it as terminal `completed` from `ctrl_out`.

Expected result:

- Release 2 evidence classifications still pass.
- Task-owned completed `ctrl_out` terminal evidence is understood without
  creating a new public state.

### Task 8: Update Specs And Plan Backlinks

Owner: implementing engineer.

Files to touch:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md` if invariants are clarified
- `docs/specifications/10-CLI_Interface.md` only if public JSON shape changes
- `docs/plans/README.md`

Required spec updates:

- [MF-3] should say task-owned terminal `ctrl_out` envelopes are emitted for
  one-shot successful completion as well as failure/control terminal paths.
- [MF-5] should preserve the Release 2 evidence priority and clarify that
  `result_without_terminal` is a diagnostic fallback, not the normal success
  proof for new tasks after this release.
- Related plan sections should link this plan.

Rules:

- Specs are current-state docs. Do not change behavior text until the code and
  tests for that behavior have landed.
- Keep the wording narrow. Do not describe monitor, reaper, archive, or
  retention behavior in this release.
- If no public CLI JSON shape changed, do not invent CLI spec deltas. A
  backlink is enough.

### Task 9: Full Verification

Owner: implementing engineer.

Run:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_observability.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py -q
./.venv/bin/python -m pytest tests/commands/test_status.py -q
./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q
./.venv/bin/python -m mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
```

If any suite is too slow locally, document the skipped gate and why. Do not
mark the release done until the gate has run somewhere.

### Task 10: Release And Ops Validation

Owner: release engineer or implementing engineer, depending on the current
release process.

This repo plan does not define packaging commands. Use the current Weft release
process in the repo and do not push or deploy without operator approval.

Ops validation after deploy:

1. SSH to ops and source environment:

   ```bash
   ssh ops
   cd ~/governance
   . .env
   ```

   If `.envrc` is the active environment file on ops, source that instead.
   The key point is that Weft must see the broker credentials before running.

2. Run a new one-shot task using the deployed Weft binary:

   ```bash
   /opt/venv/bin/weft run --no-wait python -c 'print("phase3-terminal-proof")'
   ```

3. Capture the returned TID.

4. Inspect the task:

   ```bash
   /opt/venv/bin/weft task status <tid> --json
   /opt/venv/bin/weft queue peek T<tid>.ctrl_out --limit 20
   /opt/venv/bin/weft queue peek T<tid>.outbox --limit 20
   ```

5. Confirm:
   - task status is `completed`
   - outbox contains the command output
   - `T<tid>.ctrl_out` contains a typed terminal envelope with
     `source="task"`, `status="completed"`, and `return_code=0`
   - no manager wrapper-loss envelope exists for that TID

6. Run project status:

   ```bash
   /opt/venv/bin/weft status --json
   ```

7. Confirm:
   - no new task from this release validation is classified as
     `result_without_terminal`
   - historical `result_without_terminal` and `wrapper_lost` rows may remain
     visible. Do not treat that as a release failure.

Rollback signal:

- If newly submitted one-shot successful tasks still produce outbox results
  without task-log terminal proof and without task-owned terminal `ctrl_out`,
  rollback or pause the release. That means the write path was not hardened.

## 10. Test Design Details

Good tests for this release look like this:

- They create a real TaskSpec with existing helpers.
- They execute the real Consumer or Manager method under test.
- They inspect real queues.
- They assert public payload shape, not private method call counts.
- They filter typed terminal envelopes rather than assuming `ctrl_out` only
  contains terminal messages.

Poor tests for this release look like this:

- Mocking `Queue.write()` for normal success paths.
- Mocking `Consumer._send_terminal_envelope()` and asserting it was called.
  That proves an implementation detail, not terminal evidence.
- Building hand-rolled fake TaskSpec dicts when a real `TaskSpec` helper exists.
- Sleeping to "wait" for synchronous direct Consumer behavior.
- Repeating the same terminal envelope assertion in status, task, result, and
  core tests. Put parsing/classification tests in `test_task_evidence.py` and
  write-path tests in task/manager tests.

Suggested helper for tests, if repeated locally:

```python
def _terminal_envelopes(messages: list[str], *, tid: str, source: str) -> list[dict[str, object]]:
    envelopes = []
    for raw in messages:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(payload, dict)
            and payload.get("type") == "terminal"
            and payload.get("tid") == tid
            and payload.get("source") == source
        ):
            envelopes.append(payload)
    return envelopes
```

Keep this helper private to the test file unless at least two test files need
it. DRY matters, but premature test utility modules are another form of
over-design.

## 11. Edge Cases To Think Through

Outbox write succeeds, task-log write succeeds, terminal `ctrl_out` write
fails:

- Status remains coherent because task-log terminal proof exists.
- `_send_terminal_envelope()` is best-effort and already logs debug on broker
  errors.
- This release should not fail the task because `ctrl_out` publication failed.

Outbox write succeeds, task-log write fails with a broker error, terminal
`ctrl_out` write succeeds:

- This is the primary Release 3 improvement.
- Status/task inspection can classify terminal from `ctrl_out`.

Outbox write succeeds, process dies before task-log and terminal `ctrl_out`:

- Release 3 cannot fully fix this without cross-queue atomicity.
- Release 2 `result_without_terminal` remains the correct diagnostic.

Task-log terminal event exists, manager later sees child exit:

- Manager must not write wrapper-loss if `_child_terminal_proof_visible()` sees
  terminal task-log proof.

Task-owned terminal `ctrl_out` exists, task-log terminal event is missing:

- Manager must not write wrapper-loss if `_child_terminal_proof_visible()` sees
  the task-owned terminal envelope.

Manager cannot inspect child `ctrl_out` or global task log because broker read
fails:

- Existing code returns "proof visible" on inspection failure to avoid writing
  false wrapper-loss envelopes. Preserve that fail-closed behavior.

Persistent task emits a work-item result:

- Do not publish task terminal proof. The task is still live.

Live command streaming emits stream chunks:

- Do not classify stream chunks as terminal. The typed terminal envelope must
  remain distinct from stream payloads.

## 12. Independent Review Checklist

Before implementation is merged, ask an independent reviewer to check:

- The code change is limited to terminal publication and wrapper-loss
  consistency.
- No cleanup, monitor, retention, retry, or archive behavior slipped into this
  release.
- Core modules do not import command modules.
- The success terminal envelope is emitted only after `mark_completed()`.
- Persistent work item success does not emit terminal task proof.
- Manager wrapper-loss envelope shape still matches
  `weft.commands.task_evidence.coerce_terminal_envelope()`.
- Tests use real broker queues for normal behavior.
- Any monkeypatch is narrow and justified by a deterministic failure-injection
  need.
- Spec updates are narrow and have backlinks to this plan.

## 13. Fresh-Eyes Review Of This Plan

Review pass 1 found one tempting but wrong direction: making outbox, task-log,
and `ctrl_out` writes atomic. That would require SimpleBroker transaction
semantics or a parallel lifecycle store. This plan rejects that for Release 3
and keeps `result_without_terminal` as the fallback for unavoidable crash
windows.

Review pass 2 found a possible over-DRY mistake: moving terminal-envelope
parsing from `weft.commands.task_evidence` into core so manager could reuse it.
That would violate the dependency boundary. The corrected plan uses shared
constants across layers and leaves command-only evidence classification in the
command layer.

Review pass 3 found an ambiguity around persistent tasks. The corrected plan
states that `work_item_completed` is not terminal task completion and must not
produce a task-owned terminal envelope.

Review pass 4 found an ambiguity around testing. The corrected plan requires
real broker-backed tests for ordinary behavior and permits monkeypatching only
for deterministic publication-gap injection.

Review pass 5 found a scope risk in CLI docs. The corrected plan says to update
CLI specs only if public JSON shape changes. This release should mainly change
write-path evidence, not the CLI contract.

The resulting plan still matches the discussed architecture: keep the Manager
focused on supervision, keep Release 2 as the shared read model, and harden the
task write path before building monitor/reaper cleanup.
