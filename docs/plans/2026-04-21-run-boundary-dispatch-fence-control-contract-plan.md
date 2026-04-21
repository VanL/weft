# Run Boundary, Dispatch Fence, And Control Contract Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## Goal

Address the follow-up review findings around `weft run` boundary drift,
dispatch-fence liveness, subclass control-message policy, brittle test timing,
private-helper monkeypatching, and missing edge coverage. The plan prioritizes
the current failing test and runtime liveness risks before the broader
`run.py` extraction, because the latter is a large seam change and should not
block smaller correctness fixes.

## Source Documents

Source specs:

- `docs/specifications/10-CLI_Interface.md` [CLI-0.1], [CLI-1.1.1]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-1], [MF-2],
  [MF-3], [MF-5], [MF-6], [MF-7]
- `docs/specifications/01-Core_Components.md` [CC-2.2], [CC-2.3],
  [CC-2.4], [CC-2.5], [CC-3.2]
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1]
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-2.1], [DJ-2.2]

Source guidance:

- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

Related plans:

- `docs/plans/2026-04-20-weft-client-pythonic-surface-and-path-unification-plan.md`
  is completed but explicitly records that `weft.commands.run` remains
  CLI-oriented and is not yet a public `WeftClient.run()` base.
- `docs/plans/2026-04-21-weft-client-and-django-first-class-hardening-plan.md`
  is completed and introduced `WeftClient.prepare*`; this plan must preserve
  that prepared-submission seam.
- `docs/plans/2026-04-17-canonical-owner-fence-plan.md` is completed and is
  the prior context for manager owner fencing.

No current spec defines a finite retry ceiling for dispatch-fence recovery or
a named task-control override declaration. This plan therefore includes spec
updates before implementation for those two deltas.

## Completion Notes

Implemented in this slice:

- repaired the prepared-submission shared-ops test drift
- added bounded dispatch-fence recovery with
  `manager_spawn_fence_recovery_exhausted`
- declared `TaskControlPolicy` for STOP/KILL override surfaces and added guard
  tests
- replaced the cited naked leadership/idle sleeps with deterministic manager
  turns
- added late-terminal STOP, canonical lower-manager, resource-monitor race, and
  POSIX skip-rationale coverage
- added structured `execute_run()` results and moved Typer adapter rendering to
  `weft/cli/run.py`; the command-layer `cmd_run()` remains as a compatibility
  renderer, and interactive prompt mode remains presentation-adjacent

Verification is recorded in the implementation handoff for this slice.

## Review Findings Addressed

Accepted findings:

- `weft/commands/run.py` is still a CLI-oriented command module. It owns
  output rendering, interactive prompt behavior, and exit-code shaping in the
  same module as submission and wait orchestration.
- Dispatch-fence recovery can remain pending indefinitely if exact requeue of a
  reserved spawn request keeps failing.
- STOP/KILL behavior differs across task subclasses without a single declared
  control-policy contract.
- A small number of tests use naked sleeps or internal timing constants.
- `tests/commands/test_run.py` relies heavily on monkeypatching private
  helpers because `run.py` mixes I/O and capability logic.
- Coverage gaps exist around late control messages, split-brain/corrupt
  manager registry behavior, resource monitor completion races, and POSIX-only
  skip rationale.

Additional current issue discovered during evaluation:

- `tests/core/test_ops_shared.py::test_client_submit_uses_shared_submission_module`
  fails after the prepared-submission hardening slice. The test still patches
  `submission.submit`, but `WeftClient.submit()` now intentionally routes
  through `prepare(...).submit()`.

## Context And Key Files

Current `weft run` boundary:

- `weft/commands/run.py` owns CLI-facing run orchestration. Its module
  docstring says it is not yet the basis for a public programmatic
  `WeftClient.run()` surface.
- `weft/cli/run.py` is the Typer adapter, but much presentation still lives
  below it in `weft/commands/run.py`.
- `weft/commands/submission.py` owns the newer public client submission seam:
  `prepare`, `prepare_spec`, `prepare_pipeline`, `submit_prepared`,
  `submit_command`, `submit_spec`, and `submit_pipeline`.
- `weft/client/_client.py` uses `weft.commands.submission`, not
  `weft.commands.run`.
- `tests/commands/test_run.py` tests `run.py` largely by patching private
  helpers such as `_echo`, `_read_piped_stdin`, `_enqueue_taskspec`, and
  `_ensure_manager`.

Current dispatch fence:

- `weft/core/manager.py` `DispatchSuspension` is in-memory manager state for
  one fenced reserved spawn request.
- `Manager._apply_final_dispatch_fence()` emits
  `manager_spawn_fenced_requeued`, `manager_spawn_fenced_stranded`, or
  `manager_spawn_fence_suspended`.
- `Manager._refresh_dispatch_suspension()` retries exact requeue when
  ownership later resolves to `self` or positive `other`.
- `Manager._maybe_yield_leadership()` refuses to yield while
  `_dispatch_recovery_pending()` is true.

Current control-message behavior:

- `weft/core/tasks/base.py` owns the default `PING`, `STATUS`, `STOP`, `KILL`,
  `PAUSE`, and `RESUME` handling.
- `Manager` overrides STOP to drain children before exit.
- `Consumer` defers active STOP/KILL to the main task thread so reserved-policy
  operations occur safely after the runner unwinds.
- `PipelineTask` broadcasts STOP/KILL to child tasks and updates pipeline
  status.
- `Monitor` cancels its local forwarding loop without reserved-queue policy.

Current tests to read before editing:

- `tests/core/test_ops_shared.py`
- `tests/commands/test_run.py`
- `tests/core/test_manager.py`
- `tests/tasks/test_control_messages.py`
- `tests/commands/test_task_commands.py`
- `tests/core/test_manager.py`
- `tests/tasks/test_task_stop_sqlite_only.py`
- `tests/tasks/test_command_runner_parity.py`

Comprehension questions before editing:

- For a submitted spawn request, which queue position proves rollback is safe,
  and which position requires explicit recovery?
- For a STOP received during active consumer work, which thread may apply the
  reserved-queue policy?
- Which layer is allowed to write user-visible CLI output after this plan's run
  boundary work lands?

## Invariants And Constraints

Must not change:

- TIDs remain 19-digit SimpleBroker timestamp-compatible identifiers.
- State transitions remain forward-only.
- `spec` and `io` remain immutable after runtime TaskSpec resolution.
- Manager spawn requests still use `weft.spawn.requests`; per-task queue names
  stay unchanged.
- Queue-first submission remains the durable contract. Do not move manager
  bootstrap before spawn enqueue.
- Fenced reserved spawn requests must not be silently deleted.
- Fenced diagnostics must not be interpreted as `task_spawn_rejected`.
- Reserved-queue policy stays honored for STOP/KILL/error paths.
- `weft.state.*` queues remain runtime-only hints.
- No hidden runner/provider preflight may be added to `weft run`.
- No public CLI grammar change is in scope.
- No public `WeftClient.run()` surface is in scope unless a separate spec
  update explicitly approves it.

Testing constraints:

- Keep broker and process paths real where practical.
- Do not replace queue reservation, manager lifecycle, or task lifecycle tests
  with mock-only proofs.
- Private monkeypatching may remain temporarily where the plan explicitly
  identifies existing debt, but new tests should prefer queue-visible,
  task-log-visible, or CLI-visible behavior.

Stop-and-replan gates:

- A second execution path appears for task submission, waiting, or control.
- The fix needs to mutate resolved TaskSpec `spec` or `io`.
- Dispatch-fence recovery starts deleting stranded work automatically.
- The run extraction starts changing user-visible CLI behavior.
- The test strategy requires broad mocking of SimpleBroker queues or manager
  runtime behavior.

## Proposed Ordering

Implement in this order:

1. repair the current red test from the prepared-submission slice
2. fix the dispatch-fence unbounded-recovery risk
3. define and test the control override contract
4. stabilize brittle sleeps and add the small missing edge tests
5. extract `weft run` capability/presentation boundaries in a separate larger
   slice

The run extraction is intentionally last. It is real design debt, but the
dispatch fence is a runtime liveness risk and should not wait for a broad
module refactor.

## Tasks

### 1. Repair Prepared-Submission Test Drift

Outcome: the shared ops test suite matches the current `WeftClient.submit()`
contract.

Files to touch:

- `tests/core/test_ops_shared.py`

Read first:

- `weft/client/_client.py`
- `weft/client/_prepared.py`
- `weft/commands/submission.py`
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-2.1], [DJ-2.2]

Implementation:

- Update `test_client_submit_uses_shared_submission_module` so it no longer
  patches `submission.submit`.
- Preferred shape: patch `submission.prepare` and `submission.submit_prepared`
  together, then assert `client.submit(...)` returns the TID from
  `submit_prepared` and passes the expected payload into preparation.
- Acceptable alternate shape: replace the monkeypatch test with a real
  broker-backed test that proves `client.submit(...)` writes a spawn request
  through the shared submission path.
- Do not revert `WeftClient.submit()` to call `submission.submit()` directly.
  The prepared-submission seam is now intentional.

Verification:

```bash
./.venv/bin/python -m pytest tests/core/test_ops_shared.py -q -n 0
./.venv/bin/python -m pytest tests/core/test_client.py -q -n 0
```

Done signal:

- `tests/core/test_ops_shared.py` passes without changing client behavior.

### 2. Add A Bounded Dispatch-Fence Recovery Ceiling

Outcome: a manager cannot keep `recovery_pending=True` forever under repeated
reserved-queue requeue failures. The stranded request remains visible for
operator recovery, and the manager can yield or resume after emitting a clear
durable diagnostic.

Files to touch:

- `weft/_constants.py`
- `weft/core/manager.py`
- `tests/core/test_manager.py`
- `docs/specifications/05-Message_Flow_and_State.md` [MF-6]
- `docs/specifications/01-Core_Components.md` [CC-2.5]

Read first:

- `weft/core/manager.py` `DispatchSuspension`
- `weft/core/manager.py` `_apply_final_dispatch_fence`
- `weft/core/manager.py` `_refresh_dispatch_suspension`
- `weft/core/manager.py` `_maybe_yield_leadership`
- `tests/core/test_manager.py` fence tests around stranded, suspended, and
  leadership-yield behavior

Implementation:

- Add a constant such as
  `MANAGER_DISPATCH_RECOVERY_MAX_ATTEMPTS: Final[int] = 5`.
- Add a new durable event constant such as
  `MANAGER_SPAWN_FENCE_RECOVERY_EXHAUSTED_EVENT`.
- Extend `DispatchSuspension` with an attempt counter. This is in-memory only,
  so no queue format migration is needed.
- Increment the counter only when an actual exact-message requeue attempt was
  made and failed. Do not count cycles where ownership is still `none` or
  `unknown` and no requeue was attempted.
- When the ceiling is reached, emit the new event with at least:
  `child_tid`, `leader_tid` when known, `reserved_queue`, `message_id`,
  `ownership_state`, and `attempts`.
- Do not delete the reserved message. Do not emit `task_spawn_rejected`.
- If ownership is `other`, stop blocking leadership yield after exhaustion so
  the non-leader manager can drain/yield while leaving a durable recovery
  event.
- If ownership is `self`, clear dispatch suspension after exhaustion so later
  spawn work can continue. This is a deliberate availability tradeoff after a
  bounded number of failed exact-message recovery attempts.
- Keep the existing happy path unchanged: one successful exact requeue clears
  suspension and older work becomes visible in `weft.spawn.requests` before
  later work resumes.

Tests:

- Add a test where `_requeue_reserved_spawn_request` fails until the ceiling
  and assert:
  - the reserved message remains in the manager reserved queue
  - the new exhausted event is emitted once with required fields
  - `recovery_pending` no longer blocks leadership yield on positive `other`
- Add a test where ownership becomes `self`, requeue keeps failing until the
  ceiling, and later inbox work can proceed only after the exhausted event.
- Keep existing tests for `manager_spawn_fenced_stranded`,
  `manager_spawn_fenced_requeued`, and suspended recovery passing.

Verification:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py -q -n 0
./.venv/bin/python -m pytest tests/commands/test_run.py -q -n 0
```

Done signal:

- Repeated broker-level requeue failures produce a bounded, durable,
  operator-visible recovery event instead of unbounded in-memory wedging.

### 3. Declare And Test The Task Control Override Contract

Outcome: task subclasses that override STOP/KILL behavior must declare their
policy and must continue to ack, publish state, and apply or explicitly decline
reserved-policy handling.

Files to touch:

- `weft/core/tasks/base.py`
- `weft/core/manager.py`
- `weft/core/tasks/consumer.py`
- `weft/core/tasks/pipeline.py`
- `weft/core/tasks/monitor.py`
- possibly `weft/core/tasks/interactive.py`
- `tests/tasks/test_control_messages.py`
- `tests/core/test_manager.py`
- `tests/tasks/test_pipeline_runtime.py` or the closest existing pipeline
  control test file
- `docs/specifications/01-Core_Components.md` [CC-2.4]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3]

Read first:

- `BaseTask._handle_control_message`
- `BaseTask._handle_control_command`
- `BaseTask._handle_stop_request`
- `BaseTask._handle_kill_request`
- `Consumer._defer_active_control`
- `Consumer._finalize_deferred_active_control`
- `Manager._handle_control_command`
- `PipelineTask._handle_control_command`
- `Monitor._handle_control_command`

Implementation:

- Add a small declaration shape near `BaseTask`, for example a frozen
  `TaskControlPolicy` dataclass or a `ClassVar` policy tuple. Keep it simple:
  this is a contract declaration, not a plugin framework.
- Each class that overrides STOP or KILL should declare:
  - whether STOP is immediate, draining, deferred, broadcast, or local-cancel
  - whether reserved-policy handling is applied by BaseTask, by the subclass,
    or intentionally not applicable
  - whether ctrl-out ack is immediate or post-unwind
  - whether terminal state/log emission is immediate or post-unwind
- Add a checklist docstring to `BaseTask._handle_control_command` explaining
  the subclass override obligations.
- Add a guard test that enumerates known subclasses overriding
  `_handle_control_command` and asserts they declare a control policy.
- Add or update behavior tests for each declared policy where practical:
  - BaseTask-style STOP sends ctrl-out ack and emits terminal state.
  - Manager STOP sends ack with `draining=True` and does not abandon live
    children.
  - Consumer active STOP acknowledges only after main-thread finalization.
  - Pipeline STOP broadcasts to children and publishes pipeline status.
  - Monitor STOP cancels locally and does not apply reserved-policy cleanup.
- Do not force every subclass into the same implementation path. The current
  policies are different for valid reasons; the goal is explicit declaration
  and tests, not flattening behavior.

Verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_control_messages.py -q -n 0
./.venv/bin/python -m pytest tests/core/test_manager.py -q -n 0
./.venv/bin/python -m pytest tests/tasks/test_pipeline_runtime.py -q -n 0
```

Done signal:

- A new task subclass cannot override STOP/KILL behavior without an explicit
  policy declaration and at least one behavior test.

### 4. Remove Brittle Sleep-Based Leadership Tests

Outcome: manager leadership tests use deterministic triggers instead of naked
sleep windows or internal timing constants.

Files to touch:

- `tests/core/test_manager.py`
- optional shared test helper if a small helper avoids duplicated test code

Read first:

- `Manager._maybe_yield_leadership`
- `Manager.process_once`
- `test_manager_leadership_yield_drains_non_persistent_children`
- `test_manager_leadership_yield_waits_while_persistent_children_exist`

Implementation:

- Replace `time.sleep(manager._leader_check_interval_ns / 1_000_000_000 + 0.05)`
  with a deterministic trigger. Preferred shapes:
  - call `_maybe_yield_leadership(force=True)` directly when the test is
    explicitly about leadership logic, or
  - set `_last_leader_check_ns = 0` before `process_once()` if the test must go
    through the process loop.
- Replace `time.sleep(0.4)` in the persistent-child test with an explicit
  forced leadership check after the child is visible.
- If the test needs to prove "nothing happened", assert a specific observable:
  no `manager_leadership_yielded` event, `should_stop is False`, and persistent
  child still tracked after a forced check.
- Keep bounded polling loops where they wait for a positive observable such as
  child registration or terminal task state.

Verification:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py -q -n 0
```

Done signal:

- The two cited naked sleeps are gone or replaced by deterministic forcing.

### 5. Add Missing Edge Coverage

Outcome: the known control, split-brain, resource-monitor, and skip-rationale
gaps have targeted tests or explicit documented deferrals.

Files to touch:

- `tests/tasks/test_control_messages.py`
- `tests/core/test_manager.py`
- `tests/tasks/test_command_runner_parity.py`
- `tests/commands/test_run.py`
- possibly `tests/tasks/test_resource_monitor.py` or the nearest existing
  resource-monitor test module

Implementation:

- Late control after terminal:
  - submit or construct a task that is already terminal
  - send STOP after completion
  - assert ctrl-out ack is produced
  - assert terminal state does not regress from completed/failed/timeout to
    cancelled
  - assert no duplicate terminal state event changes durable truth
- Manager split-brain / registry corruption:
  - add a test with two live manager registry entries and assert only the
    canonical owner dispatches
  - add a malformed/unreadable registry scenario and assert dispatch suspends
    rather than launching duplicate children
- Resource monitor completion race:
  - add a task-runner or host-runner test where the process exits while monitor
    polling is active
  - assert terminal completed result wins when no limit was violated
  - assert monitor cleanup does not write a late limit violation after normal
    completion
- POSIX-only skips:
  - add a short comment above the two `tests/commands/test_run.py` skipif cases
    explaining the POSIX-specific zombie/dead-PID semantics being exercised.
  - Do not remove the skips unless equivalent Windows behavior is implemented.

Verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_control_messages.py -q -n 0
./.venv/bin/python -m pytest tests/core/test_manager.py -q -n 0
./.venv/bin/python -m pytest tests/tasks/test_command_runner_parity.py -q -n 0
./.venv/bin/python -m pytest tests/commands/test_run.py -q -n 0
```

Done signal:

- Each listed coverage gap has either a passing targeted test or a documented
  deferral with rationale.

### 6. Extract The `weft run` Capability Boundary

Outcome: capability logic for non-interactive `weft run` no longer writes to
stdout/stderr or owns CLI exit-code presentation. CLI rendering moves to the
adapter layer. Interactive streaming remains explicit and isolated if it cannot
be extracted safely in the same slice.

Files to touch:

- `weft/commands/run.py`
- possibly a new `weft/commands/run_execution.py` or
  `weft/commands/run_model.py`
- `weft/cli/run.py`
- `weft/commands/types.py`
- `weft/commands/submission.py`
- `weft/client/_client.py` only if a non-public helper reuse is required
- `tests/commands/test_run.py`
- `tests/cli/test_cli_run.py` or existing CLI run tests
- `tests/core/test_ops_shared.py`
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-1], [MF-5], [MF-6]
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`

Read first:

- `weft/commands/run.py` module docstring and all `_echo` uses
- `weft/cli/run.py`
- `weft/commands/submission.py` prepared-submission functions
- `weft/commands/result.py`
- `weft/commands/_result_wait.py`
- `tests/commands/test_run.py`
- `tests/cli` run-related tests

Implementation approach:

- Start with non-interactive command, function, spec, and pipeline paths.
- Define a structured command-layer result such as:
  - submitted TID
  - terminal status when waited
  - result value / stdout / stderr / error
  - presentation-neutral warnings or manager-started metadata
- Move stdout/stderr writes and JSON/text formatting to `weft/cli/run.py`.
- Keep spec-aware help string generation presentation-adjacent. It can remain
  a pure string helper, but Typer output should still happen in the CLI layer.
- Route spawn writes through `weft.commands.submission.prepare*` and
  `submit_prepared` where possible. If direct `submit_spawn_request` remains
  necessary for an internal runtime edge, document why in the function
  docstring and add a test.
- Keep `weft run --interactive` as a named exception if extracting the prompt
  loop would make the first slice too large. If it remains in
  `weft.commands.run`, isolate it behind a small adapter/sink and keep the
  module docstring honest.
- Do not add public `WeftClient.run()` in this slice. After the command-layer
  result model is stable, a separate spec update can decide whether that API is
  worth exposing.

Testing strategy:

- Convert tests that only patch `_echo` to assert structured command-layer
  results.
- Add CLI adapter tests that assert text and JSON output shape through the
  public CLI runner.
- Reduce private monkeypatching around `_read_piped_stdin` by adding an input
  provider seam or testing through subprocess stdin where practical.
- Keep real broker proofs for spawn enqueue, manager bootstrap, wait, and
  result behavior.
- Do not try to eliminate every private monkeypatch in one slice; track any
  remaining ones with a short comment explaining the external boundary being
  simulated.

Verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_run.py -q -n 0
./.venv/bin/python -m pytest tests/cli -q -n 0
./.venv/bin/python -m pytest tests/core/test_ops_shared.py -q -n 0
./.venv/bin/python -m pytest tests/core/test_client.py -q -n 0
```

Done signal:

- Non-interactive `weft run` capability logic can be tested without patching
  `_echo`.
- The CLI adapter owns output formatting and exit-code presentation.
- Client and CLI submission paths still share the same command-layer
  submission primitives.

### 7. Documentation And Traceability

Outcome: specs, plan index, and implementation docstrings describe the shipped
boundaries.

Files to touch:

- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `docs/plans/README.md`
- touched code module docstrings

Implementation:

- Update [CLI-1.1.1] to describe the new command-layer run capability and CLI
  adapter presentation boundary after Task 6 lands.
- Update [MF-6] with bounded dispatch-fence recovery semantics and the new
  exhausted event.
- Update [CC-2.4] with the task-control policy declaration requirement.
- Update implementation mappings that still refer to `weft/cli/run.py` if the
  real owner is `weft/commands/run.py` or a new command-layer module.
- Keep docstrings on changed modules pointing to the exact spec sections.

Verification:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q -n 0
./.venv/bin/python -m pytest tests/specs -q -n 0
```

Done signal:

- A zero-context implementer can navigate from spec to plan to code for run
  execution, dispatch-fence recovery, and control-message policy.

## Rollback

Tasks 1, 4, and 5 are test-only or test-heavy and can be reverted
independently if they prove too broad.

Task 2 rollback:

- Revert the retry-ceiling constant, new event, and `DispatchSuspension`
  attempt counter together.
- Existing fence behavior should fall back to the current unbounded pending
  state.
- Do not leave the spec claiming bounded recovery if the code is reverted.

Task 3 rollback:

- Revert policy declarations and guard tests together.
- Keep any behavior tests that caught real regressions if they still pass.

Task 6 rollback:

- Revert the run-boundary extraction as one unit if CLI output changes
  unexpectedly.
- Do not leave CLI adapters calling a removed command-layer result model.
- Do not roll back prepared-submission work from the completed Django/client
  hardening slice.

No rollback path should delete queued or reserved task work.

## Review Plan

Independent review is required before implementation because this plan touches
manager liveness, task control, and CLI/client boundaries.

Planning review prompt:

> Read `docs/plans/2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md`
> and the cited code/spec sections. Could you implement this confidently and
> correctly? Identify missing invariants, bad sequencing, overbroad scope, or
> tests that would prove the wrong thing.

Completed-work review should run after:

- Task 2 lands
- Task 3 lands
- Task 6 lands
- final documentation and verification

Reviewer should focus on:

- no silent loss of fenced reserved work
- no duplicate manager dispatch under ownership uncertainty
- STOP/KILL ack/state/reserved-policy behavior
- no CLI output regression
- no new second execution path

## Full Verification

Run the smallest task-specific tests after each slice, then run:

```bash
./.venv/bin/python -m pytest tests/core/test_ops_shared.py -q -n 0
./.venv/bin/python -m pytest tests/core/test_manager.py -q -n 0
./.venv/bin/python -m pytest tests/tasks/test_control_messages.py -q -n 0
./.venv/bin/python -m pytest tests/commands/test_run.py -q -n 0
./.venv/bin/python -m pytest tests/cli -q -n 0
./.venv/bin/python -m pytest tests/core/test_client.py -q -n 0
./.venv/bin/python -m pytest tests/specs -q -n 0
./.venv/bin/python -m ruff check weft tests
./.venv/bin/python -m ruff format --check weft tests
./.venv/bin/python -m mypy weft --config-file pyproject.toml
git diff --check
```
