# Spawn Request Reconciliation Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## Goal

Fix the false-failure race in queue-first task submission without undoing the
queue-first design. After `weft run` enqueues a spawn request, later bootstrap
or cleanup failures must reconcile that submitted TID against the durable
surfaces Weft already owns instead of pretending the enqueue can always be
rolled back. The implementation must keep the current durable spine, preserve
queue-first submission, and make the exceptional recovery path explicit and
observable.

## Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-1], [MA-2], [MA-3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-1], [MF-2], [MF-5], [MF-6], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [MANAGER.4], [MANAGER.5], [MANAGER.6], [OBS.3]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-1.1.1]
- [`docs/specifications/06-Resource_Management.md`](../specifications/06-Resource_Management.md) "Recovery Boundary"

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)

Relevant existing plans and lessons:

- [`docs/plans/2026-04-09-manager-bootstrap-unification-plan.md`](./2026-04-09-manager-bootstrap-unification-plan.md)
- [`docs/plans/2026-04-13-detached-manager-bootstrap-hardening-plan.md`](./2026-04-13-detached-manager-bootstrap-hardening-plan.md)
- [`docs/lessons.md`](../lessons.md), especially "Manager Bootstrap Unification" and "Manager Startup Races"

Source spec gap:

- None. This is a contract-hardening bug fix for an existing queue-first
  submission model.

## Target Contract

This section is the contract to implement. Do not improvise beyond it.

1. Queue-first submission stays intact.
   - `weft run` continues to enqueue the spawn request before waiting for
     manager readiness.
   - Do not move `_ensure_manager()` ahead of `_enqueue_taskspec()`.
   - Do not create a second direct child-launch path that bypasses
     `weft.spawn.requests`.

2. After enqueue, the CLI must reconcile by TID rather than assume rollback.
   - Once `_enqueue_taskspec()` returns a TID, later exceptional handling must
     treat that TID as durable user intent.
   - The CLI may delete the request only when it can still prove the exact
     message remains unclaimed in `weft.spawn.requests`.
   - If the request has already been claimed into a manager reserved queue, the
     CLI must not claim rollback succeeded.

3. Successful startup proof and auxiliary cleanup are different things.
   - After `_start_manager()` has proven a canonical active manager record for
     the launched PID, late launcher-acknowledgement or startup-log cleanup
     failures are auxiliary failures.
   - Auxiliary failures must not flip a successful background manager start into
     a submission failure.

4. Exceptional submission outcomes must be explicit and narrow.
   - `spawned`: the submitted TID is already visible through task status, task
     log, or TID mapping surfaces; continue normal run/result handling.
   - `rejected`: the manager has already emitted `task_spawn_rejected` for the
     submitted child TID; fail with the manager-side reason.
   - `queued`: the exact spawn request is still unclaimed in
     `weft.spawn.requests`; delete it and fail.
   - `reserved`: the exact spawn request is no longer in the public inbox but
     is still present in a canonical manager reserved queue; fail with a clear
     operator-recovery message that includes the TID and reserved queue.
   - `unknown`: no durable proof of success or rollback exists yet; fail
     loudly, include the TID, and do not claim cleanup succeeded.

5. No automatic retry, orphan adoption, or queue redesign in this slice.
   - Do not add background reconciliation services.
   - Do not add a new recovery command.
   - Do not redesign manager reserved-queue ownership or queue names.

6. Operator recovery remains manual and queue-first.
   - The fix should document how to inspect and recover a stuck reserved spawn
     request with existing queue primitives.
   - This slice documents recovery. It does not introduce a richer recovery UX.

## Context and Key Files

Files to modify:

- `weft/commands/_manager_bootstrap.py`
- `weft/commands/run.py`
- `weft/commands/tasks.py` only if a tiny helper extraction is needed to reuse
  current task-status lookup without circular imports
- `weft/commands/status.py` only if a tiny helper extraction is needed to reuse
  current snapshot reconstruction without circular imports
- `weft/core/spawn_requests.py` only if a shared exact-message lookup helper is
  genuinely needed there
- `tests/commands/test_run.py`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`
- `docs/lessons.md` only if implementation exposes a reusable lesson not
  already captured

Likely new helper file:

- `weft/commands/_spawn_submission.py`

Read first:

- `weft/commands/run.py`
  - Current owner of queue-first submission and the failing exception path.
  - Today `_run_inline`, `_run_spec_via_manager`, and `_run_pipeline` enqueue
    first, then call `_ensure_manager()`, then blindly call
    `_delete_spawn_request()` if `_ensure_manager()` raises.
- `weft/core/spawn_requests.py`
  - Current owner of exact-timestamp submit and delete helpers.
  - Today `delete_spawn_request()` only deletes from `weft.spawn.requests`; it
    does not inspect manager reserved queues or task-log state.
- `weft/commands/_manager_bootstrap.py`
  - Current owner of canonical manager bootstrap, registry selection, and late
    launch acknowledgement.
  - Today `_start_manager()` treats `_acknowledge_manager_launch_success()` as
    fatal even after registry proof has already succeeded.
- `weft/core/manager.py`
  - Current owner of manager-side spawn-request consumption.
  - Spawn requests are consumed from `weft.spawn.requests` in reserve mode and
    acknowledged by deleting from the manager reserved queue after launch.
- `weft/commands/tasks.py` and `weft/commands/status.py`
  - Current public task-status reconstruction path.
  - If the new reconciliation helper needs "has this TID already become a
    task?" proof, prefer these existing status/log readers over new ad hoc log
    replay.
- `tests/commands/test_run.py`
  - Already contains the queue-first ordering tests and the current
    "delete spawn request when ensure_manager fails" tests.
  - Those tests currently prove only the easy case where no manager has already
    claimed the request.
- `tests/tasks/test_multiqueue_watcher.py`
  - Current proof that reserve-mode processing moves the message before the
    handler and requires acknowledgement from the reserved queue.

Style and guidance:

- Follow `AGENTS.md` and the repo style in the files above.
- Use `from __future__ import annotations`.
- Keep imports grouped stdlib, third-party, local.
- Use `collections.abc` for abstract container types.
- Use existing helpers instead of adding broad abstractions.
- Prefer ASCII.
- Add comments only when the code is genuinely hard to read without them.

Shared paths and helpers to reuse:

- `submit_spawn_request()` and `delete_spawn_request()` in
  `weft/core/spawn_requests.py`
- `_ensure_manager()`, `_start_manager()`, `_select_active_manager()`, and
  `_list_manager_records()` in `weft/commands/_manager_bootstrap.py`
- `task_status()` in `weft/commands/tasks.py` if it can be reused without
  cyclic imports
- `iter_queue_entries()` / `iter_queue_json_entries()` in `weft/helpers.py`
- Existing result waiting in `weft/commands/_result_wait.py` and
  `_wait_for_task_completion()` in `weft/commands/run.py`

Current structure:

- The queue-first submission contract is already part of the spec and code.
- The current bug is not "submission happens before manager startup." That is
  intentional and should remain.
- The current bug is that the exceptional path still assumes submission can be
  rolled back from the public inbox even after a manager may have already moved
  the message into its reserved queue or spawned the child task.
- The current test suite proves queue-first ordering and easy deletion, but it
  does not prove the hard case where bootstrap fails after manager proof or
  after queue claim.

Comprehension checks before editing:

1. Which queue message ID becomes the child task TID, and where is that
   correlation enforced today?
2. Which function currently decides whether a live canonical manager exists,
   and which step inside that function is core startup proof versus auxiliary
   cleanup?
3. Which durable surfaces can already prove "this submitted TID became a real
   task" without inventing a new state store?
4. Why is moving `_ensure_manager()` ahead of `_enqueue_taskspec()` the wrong
   direction for this fix?

## Invariants and Constraints

- Preserve one durable submission spine:
  `CLI -> weft.spawn.requests -> Manager reserve/launch -> child task/logs`.
- Preserve TID format and immutability.
- Preserve forward-only state transitions.
- Preserve the rule that the spawn-request message ID becomes the child TID.
- Preserve manager reserve-mode semantics.
- Preserve `spec` and `io` immutability after resolved `TaskSpec` creation.
- Preserve runtime-only treatment of `weft.state.*` queues.
- Preserve append-only queue reading discipline. Use generator-based helpers for
  correctness-critical scans. Do not add `peek_many(limit=N)` logic to find a
  submitted TID.
- Keep best-effort failures and fatal failures distinct:
  - fatal: corrupt submission state, false success without durable proof, wrong
    TID, duplicate launch path, wrong reserved-queue behavior
  - best-effort: late launcher acknowledgement, startup stderr cleanup, extra
    diagnostics cleanup
- Do not add a second execution path, a new queue, or a new persistent state
  store.
- Do not add a new dependency.
- Do not auto-requeue or auto-delete manager reserved messages.
- Do not broaden this work into general manager recovery, orphan adoption, or a
  queue-CLI redesign.
- DRY:
  - reuse the existing task-status and log readers where practical
  - do not duplicate task snapshot reconstruction in a new helper unless there
    is a real cycle or contract reason
- YAGNI:
  - solve only the specific false-failure and reconciliation problem
  - avoid "submission state machine" frameworks or new generic retry layers
- Red-green TDD:
  - write failing proofs first for the race and the reconciliation outcomes
  - keep broker-backed and process-backed tests real
  - mock only the narrow boundary needed to force the late-failure timing

Hidden couplings to respect:

- `_start_manager()` is on the same critical path as `weft run` and
  `weft manager start`.
- `weft run` no-wait and wait modes both depend on the same submission TID.
- `task_status()` reconstructs from logs and runtime state. It is not a pending
  submission queue reader, so queued-vs-spawned decisions still need direct
  queue inspection.
- The "started_here" bookkeeping in `run.py` only makes sense if
  `_ensure_manager()` returns normally. This plan relies on tightening
  `_start_manager()` first so late bootstrap-ack failures stop raising after
  successful manager proof.

Review gates:

- no second submission or startup path
- no drive-by refactor
- no new dependency
- no public CLI flag change
- no mock-heavy substitute for real broker/process proof
- no spec drift between the touched docs and the implementation

Out of scope:

- redesign of manager reserved-queue acknowledgement semantics
- background recovery daemons
- a new `weft task recover` or higher-level recovery UX on top of the existing
  queue commands
- broader manager lifecycle or registry changes not needed for this bug
- pipeline runtime changes outside the shared run submission path

## Tasks

1. Add failing regression tests that lock the current bug before any code change.
   - Outcome: the test suite fails on the current implementation for the exact
     behaviors this plan is fixing.
   - Files to touch:
     - `tests/commands/test_run.py`
   - Read first:
     - `weft/commands/run.py`
     - `weft/commands/_manager_bootstrap.py`
     - `weft/core/spawn_requests.py`
     - `tests/commands/test_run.py`
     - `tests/tasks/test_multiqueue_watcher.py`
   - Use real broker-backed queues and the real `_run_inline()` /
     `_run_spec_via_manager()` path. Do not mock the broker or replace the
     manager with a fake queue consumer.
   - Allowed mocks:
     - a narrow patch of `_acknowledge_manager_launch_success()` to force a late
       failure after manager proof
     - a narrow patch around `_enqueue_taskspec()` only to record the real TID
       returned by the original helper
   - Tests to add first:
     1. Late acknowledgement failure after live manager proof does not make
        `_run_inline(..., wait=False)` fail.
        - Patch `_enqueue_taskspec()` with a wrapper that records the real TID
          and delegates to the original helper.
        - Patch `_acknowledge_manager_launch_success()` so it waits until the
          recorded child TID becomes visible through `task_status()` or a
          `task_spawned` log event, then raises.
        - Use a function target that succeeds without extra input. Prefer the
          existing `tests.tasks.sample_targets:provide_payload` helper so the
          test failure is about submission semantics, not task arguments.
        - Assert the command path returns success and the recorded TID is
          observable as a real task.
     2. Late acknowledgement failure after live manager proof does not make the
        wait path fail.
        - Same forcing strategy as above.
        - Assert the command path reaches normal result waiting for the recorded
          TID and returns the real task result instead of a submit error.
     3. Claimed-but-not-acknowledged spawn requests are not treated as safely
        deletable.
        - Write a canonical manager registry record first so the future helper
          has a real manager TID to inspect.
        - Use a real queue move from `weft.spawn.requests` into that manager's
          actual reserved queue name, `T{manager_tid}.reserved`.
        - Assert the future reconciliation helper classifies the message as
          reserved rather than queued.
     4. Unclaimed spawn requests are still rollback-safe.
        - Write a spawn request to `weft.spawn.requests`.
        - Assert the future reconciliation helper classifies it as queued and
          only then allows deletion.
     5. Manager-side rejection is surfaced as rejection, not unknown failure.
        - Write the smallest realistic task-log event sequence that includes
          `task_spawn_rejected` with `child_tid=<submitted tid>`.
        - Assert the future reconciliation helper returns a rejection result
          with the manager error.
   - Stop if:
     - the tests start mocking `Queue`, `_select_active_manager()`, or full
       task-log replay
     - the forcing mechanism requires changing production behavior before the
       first failing test exists
   - Done when:
     - the tests fail on the current code for the right reasons
     - the failure messages point at false submit failure or wrong
       reconciliation state, not generic timeouts

2. Tighten `_start_manager()` so late acknowledgement is auxiliary after live proof.
   - Outcome: once a canonical active manager record for the launched PID is
     proven, `_start_manager()` returns success even if launcher
     acknowledgement/cleanup then fails.
   - Files to touch:
     - `weft/commands/_manager_bootstrap.py`
     - `tests/commands/test_run.py`
   - Read first:
     - `weft/commands/_manager_bootstrap.py`
     - [`docs/plans/2026-04-13-detached-manager-bootstrap-hardening-plan.md`](./2026-04-13-detached-manager-bootstrap-hardening-plan.md)
   - Required approach:
     - keep the current registry-and-PID proof as the core startup success test
     - split "manager is durably running" from "detached launcher exited cleanly
       after success"
     - after proof succeeds, wrap launcher acknowledgement in a best-effort
       branch that may emit a warning or debug log but must not raise
       submission failure
   - Constraints:
     - do not weaken the existing live-PID and matching-registry proof
     - do not mark success before the canonical registry proof is satisfied
     - do not kill the manager solely because acknowledgement cleanup failed
   - Tests to update/add:
     - extend the new late-ack regression so it passes here
     - add a focused `_start_manager()` test showing that a post-proof
       acknowledgement exception returns the active manager record instead of
       raising
   - Stop if:
     - the change starts relaxing startup proof itself instead of only the late
       auxiliary cleanup
     - `_start_manager()` begins returning success without a matching canonical
       registry record for the launched PID
   - Done when:
     - late-ack failure no longer produces a false startup error after proof
     - existing early-failure tests for detached startup still pass

3. Add a narrow submission-reconciliation helper and wire the `run` paths to use it.
   - Outcome: the exceptional post-enqueue path classifies the submitted TID
     using existing durable surfaces instead of blindly calling
     `_delete_spawn_request()`.
   - Files to touch:
     - new `weft/commands/_spawn_submission.py`
     - `weft/commands/run.py`
     - `weft/commands/tasks.py` only if a tiny cycle-breaking helper extraction
       is genuinely required
     - `weft/commands/status.py` only if a tiny cycle-breaking helper
       extraction is genuinely required
     - `tests/commands/test_run.py`
   - Read first:
     - `weft/commands/run.py`
     - `weft/commands/tasks.py`
     - `weft/commands/status.py`
     - `weft/helpers.py`
     - `weft/core/spawn_requests.py`
   - Preferred design:
     - add a small local result type such as a `dataclass` or `NamedTuple`
       representing:
       `spawned`, `rejected`, `queued`, `reserved`, `unknown`
     - keep the helper command-local; do not move it into runtime code
     - give the helper one job: reconcile a submitted TID on the exceptional
       path
   - Required algorithm:
     1. Poll for a short bounded window. Start with `1.0s` and adjust only if
        the tests show the real broker path needs more. Do not invent long
        retries.
     2. Check whether the submitted TID is already visible as a task through
        `task_status()` or a direct log/TID-mapping signal.
     3. Check for a `task_spawn_rejected` log event keyed by
        `child_tid == submitted_tid`.
     4. Check whether the exact timestamp is still present in
        `weft.spawn.requests`.
     5. Check whether the exact timestamp is present in a canonical manager
        reserved queue derived from the latest canonical manager registry
        records, including stopped records for managers that may have claimed
        work before exiting.
        - Reuse the shared manager-registry reader rather than replaying
          `weft.state.managers` by hand a second time.
     6. If none of the above prove success or rollback, return `unknown`.
   - Wiring requirements in `weft/commands/run.py`:
     - replace unconditional `_delete_spawn_request()` in the `_ensure_manager()`
       exception path with the reconciliation helper
     - only call `_delete_spawn_request()` for the `queued` result
     - if the helper returns `spawned`, continue with the same TID and the
       existing wait/no-wait behavior
     - if the helper returns `rejected`, surface the manager rejection error
     - if the helper returns `reserved`, raise a clear error that includes:
       submitted TID, reserved queue name, and the fact that manual recovery is
       required
     - if the helper returns `unknown`, raise a clear error that includes the
       submitted TID and says rollback could not be proven
   - Constraints:
     - do not call `task_status()` or log replay in a way that introduces a
       cycle between `run.py` and the status/task modules; if a cycle appears,
       extract the smallest helper from `tasks.py` or `status.py` instead of
       duplicating the whole snapshot pipeline
     - do not broaden this helper into a general submission framework
     - do not auto-delete from manager reserved queues
   - Tests to add/update:
     - the new reconciliation-state tests from Task 1
     - regression coverage for `_run_spec_via_manager()` and `_run_pipeline()`
       so all three submission entry points share the same fixed behavior
   - Stop if:
     - the helper starts reconstructing full task snapshots by copy-paste
     - implementation pressure pushes you toward reordering enqueue and ensure
     - the code starts inferring success from a manager registry record alone
       without a task/log/TID mapping signal for the submitted TID
   - Done when:
     - all three `run` entry points share the same reconciliation behavior
     - delete-on-failure only runs for provably unclaimed queue entries
     - the late-failure regression from Task 1 passes

4. Document the fixed contract and the manual recovery path using existing tools.
   - Outcome: the specs and agent runbook tell the truth about queue-first
     submission after enqueue and about reserved spawn-request recovery.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/agent-context/runbooks/runtime-and-context-patterns.md`
     - `docs/lessons.md` only if implementation exposes a new durable lesson
   - Spec changes to make:
     - in Manager Architecture and Message Flow, state plainly that queue-first
       submission is durable once the spawn request is written, and later CLI
       error handling reconciles by TID rather than assuming rollback
     - in CLI Interface, state plainly that enqueue happens before manager wait
       and that post-enqueue failures reconcile against task/log/queue state
     - keep the public CLI shape unchanged
   - Runbook changes to make:
     - add a short "Recovering a stuck reserved spawn request" section to the
       existing runtime runbook instead of creating a new recovery subsystem
     - document:
       1. how to identify the submitted TID and candidate manager reserved
          queue
       2. how to inspect that queue with `weft queue peek --timestamps`
       3. how to clear a stale reserved message with
          `weft queue delete <queue> --message <tid>`
       4. how to requeue only when the operator has first proved the child did
          not already spawn, using
          `weft queue move <reserved> weft.spawn.requests --message <tid>`
   - Constraints:
     - do not write docs that imply automatic recovery exists
     - do not write recovery guidance that moves or deletes more than the exact
       target message
   - Stop if:
     - the docs start specifying a new public recovery command that is not part
       of the implementation
   - Done when:
     - the specs and runbook describe the fixed contract accurately
     - the spec backlinks include this plan

5. Run the full verification slice and do a hostile reread before merge.
   - Outcome: the change is proven on the real paths it touched and the docs do
     not leave an implementer room to guess wrong.
   - Files to touch:
     - none unless the reread finds ambiguity or drift
   - Verification commands to run in sequence:
     1. `. ./.envrc`
     2. `./.venv/bin/python -m pytest tests/commands/test_run.py -q`
     3. `./.venv/bin/python -m pytest tests/commands/test_run.py tests/core/test_manager.py -q`
     4. `./.venv/bin/python -m pytest tests/commands/test_status.py tests/commands/test_task_commands.py -q`
        if Task 3 reuses or extracts status/task helpers
     5. `./.venv/bin/python -m mypy weft`
     6. `./.venv/bin/ruff check weft`
   - Reread checklist:
     - does the implementation still preserve queue-first submission?
     - can a late auxiliary cleanup failure still report a false submit error?
     - can the exceptional path still claim rollback without proving the
       message stayed unclaimed?
     - does any doc imply a new recovery feature that was not actually built?
     - did any helper accidentally duplicate task-status reconstruction instead
       of reusing the existing path?
   - Review note:
     - independent review is preferred because this work crosses CLI, manager,
       queue, and docs boundaries
     - if no second reviewer is available, do a hostile self-review against the
       checklist above and record any unresolved tradeoff in the final change
       summary
   - Done when:
     - targeted tests pass
     - adjacent manager/status tests pass
     - type check and lint pass
     - docs and code still match the plan

## Testing Strategy

Keep real:

- real broker-backed queues
- real reserve-mode semantics
- real `_run_inline()` / `_run_spec_via_manager()` / `_run_pipeline()` command
  helpers
- real detached manager bootstrap path up to the narrow forced late-failure
  seam
- real task-log and TID-mapping surfaces

Mock only:

- the late acknowledgement boundary needed to force the post-proof exception
- tiny wrappers used only to record the real submitted TID in tests

Do not mock:

- `simplebroker.Queue`
- manager registry replay
- reserve-mode queue movement
- task-log replay
- result waiting

Test design rules for this change:

- prefer one failing test per externally visible contract
- keep timing assertions bounded and tied to durable signals, not blind sleeps
- when polling is required, poll for a durable signal such as:
  - `task_status(tid) is not None`
  - a `task_spawned` or `task_spawn_rejected` log event
  - exact message presence in a queue by timestamp
- if a test must force a race, force it at the narrow seam after durable proof
  exists rather than with random sleeps earlier in startup

## Rollout and Rollback

Rollout:

- Ship Task 2 before relying on Task 3 for spawned-after-exception recovery.
  That ordering removes the main ambiguous case where a newly started manager
  exists but `_ensure_manager()` still raised after success proof.
- Keep queue names, TIDs, and task-log event shapes backward-compatible.

Rollback:

- Revert the manager-bootstrap success-boundary change and the submission
  reconciliation change together if the new behavior misfires.
- Do not roll back only the docs or only the exception-path helper.
- If rollback is required after code lands, preserve the added regression tests
  until the old behavior is intentionally restored or replaced.

## Non-Goals and Bad Ideas

Do not do these as part of this plan:

- moving `_ensure_manager()` ahead of `_enqueue_taskspec()`
- adding a new submission database or cache
- teaching the CLI to auto-adopt or auto-kill unknown spawned work
- broadening the scope into manager reserved-queue redesign
- adding new queue commands just to make recovery prettier
- rewriting task-status reconstruction because a small helper extraction felt
  annoying

If implementation pressure pushes hard toward any of those, stop and explain
why. That is scope drift away from the discussed fix.
