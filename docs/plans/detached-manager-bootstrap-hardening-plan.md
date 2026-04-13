# Detached Manager Bootstrap Hardening Plan

## Goal

Make detached manager bootstrap satisfy the existing `MA-3` contract in
practice, not just in the happy path. `weft run` and `weft manager start`
should only report success after they have launched the canonical manager
through an independent detached-launch wrapper, verified that the expected
manager PID is still live, and observed a stable canonical registry record for
that same manager. Early bootstrap failure must surface actionable diagnostics
instead of silently discarding stderr.

## Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [MANAGER.1], [MANAGER.6], [MANAGER.7]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-0.2], [CLI-1.1], [CLI-1.1.2]

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)

Relevant existing plans and reports:

- [`docs/plans/manager-bootstrap-unification-plan.md`](./manager-bootstrap-unification-plan.md)
- [`docs/plans/weft-serve-supervised-manager-plan.md`](./weft-serve-supervised-manager-plan.md)
- [`problem-report-detached-manager-bootstrap.md`](../../problem-report-detached-manager-bootstrap.md)
- [`problem-report-manager-status-surface-divergence.md`](../../problem-report-manager-status-surface-divergence.md)

Source spec gap:

- None. This is a contract-hardening bug fix for an existing spec promise.

## Target Contract

This section is the contract to implement. Do not improvise beyond it.

1. Detached bootstrap remains the default background manager path.
   - `weft run` and `weft manager start` still share the same canonical manager
     bootstrap helper.
   - `weft manager serve` remains the foreground supervised path and must not
     route through the detached-launch wrapper.

2. Detached bootstrap must establish an independent process boundary before the
   caller returns.
   - The long-lived manager runtime must not remain in the caller CLI process
     group on POSIX.
   - The long-lived manager runtime must not remain a direct child of the CLI
     process by the time `_start_manager()` returns.
   - The implementation may use a short-lived helper process, but the durable
     runtime must still be `weft.manager_process`.

3. Success must mean more than "a registry entry appeared once."
   - `_start_manager()` must only report success after all of these are true:
     - the detached-launch helper reported a manager PID
     - that PID is still live
     - the canonical registry record for `manager_tid` exists, is active, and
       carries that same PID
     - the record and PID remain valid through a short startup stability window
   - If another canonical manager wins a race and remains live, the caller may
     adopt that manager as `started_here=False`, but it must not report that the
     launched manager succeeded.

4. Early launch diagnostics must be observable.
   - If the detached manager exits before the startup proof is satisfied, the
     CLI must surface the child exit status and a useful stderr tail or startup
     log path.
   - Startup-diagnostic capture is for the bounded bootstrap window only. This
     plan does not add a permanent stderr logging feature for long-lived
     managers.

5. No public CLI shape change in this slice.
   - Keep `weft manager start [--context PATH]`.
   - Keep `weft run` behavior and output shape unchanged except for improved
     failure messaging when detached manager startup fails.

6. No second durable manager runtime path.
   - Wrapper logic for detachment is allowed.
   - The actual manager runtime must still execute through:
     `_start_manager()` -> `weft.manager_process` -> `run_manager_process()` ->
     `_task_process_entry()` -> `Manager`.

## Context and Key Files

Files to modify:

- `weft/commands/_manager_bootstrap.py`
- `weft/manager_process.py`
- `weft/manager_detached_launcher.py` (new)
- `weft/context.py` only if a startup-log directory helper is genuinely needed
- `tests/commands/test_run.py`
- `tests/commands/test_manager_commands.py`
- `tests/cli/test_cli_manager.py`
- `tests/cli/test_cli_serve.py` only if a regression check for the foreground path
  needs a small helper reuse
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/lessons.md` if implementation work exposes a reusable repo lesson

Read first:

- `weft/commands/_manager_bootstrap.py`
  - Current owner of canonical manager bootstrap, registry replay, and startup wait.
  - Today `_start_manager()` launches `weft.manager_process` directly, suppresses
    `stderr`, and returns once a canonical registry record appears.
- `weft/manager_process.py`
  - Current durable runtime entry point for the manager.
  - This file must stay the actual manager runtime path.
- `weft/core/manager.py`
  - Owns registry registration, leadership yield, idle timeout, and signal handling.
  - The plan should not move bootstrap logic into `Manager`.
- `tests/cli/test_cli_manager.py`
  - Current CLI proof only checks immediate `manager start` success and immediate
    `manager list/status`.
  - It does not prove survival after caller teardown or process-group separation.
- `tests/cli/test_cli_serve.py`
  - Current foreground proof keeps a real manager live under explicit supervision.
  - This file is the right regression check for "do not break `manager serve`."
- `problem-report-detached-manager-bootstrap.md`
  - Captures the operator-facing failure mode and why transient registry success
    is not good enough.

Shared paths and helpers to reuse:

- `_build_manager_runtime_invocation()` and `_build_manager_process_command()` in
  `weft/commands/_manager_bootstrap.py`
- `_manager_record()`, `_select_active_manager()`, `_is_pid_alive()`, and
  `_terminate_manager_process()` in `weft/commands/_manager_bootstrap.py`
- `run_manager_process()` in `weft/manager_process.py`
- `pid_is_live()` and `iter_queue_json_entries()` in `weft/helpers.py`
- `WeftContext.logs_dir` in `weft/context.py` if temporary startup diagnostics
  need a repo-owned location

Current structure:

- Detached bootstrap is owned by `_start_manager()` in
  `weft/commands/_manager_bootstrap.py`.
- The current code launches the manager runtime directly with plain
  `subprocess.Popen(...)` and only redirects `stdout` / `stderr`.
- The current success condition is "matching canonical registry record appeared
  before timeout."
- The manager runtime itself is not the problem boundary. Foreground
  `manager serve` reuses that runtime and behaves correctly.
- The current failing PTY repro and the surviving interactive-shell repro both
  show the same design issue: bootstrap depends on ambient parent/session cleanup
  behavior instead of creating its own lifecycle boundary.

Comprehension checks before editing:

1. Which function currently owns detached bootstrap success/failure semantics,
   and what exact event makes it return success today?
2. Which state surfaces must agree before detached bootstrap can honestly claim
   "the background manager is running"?
3. Why must the detached-launch wrapper remain wrapper-only rather than becoming
   a second manager runtime path?

## Invariants and Constraints

- Preserve one durable runtime spine:
  detached wrapper (if any) -> `weft.manager_process` -> `run_manager_process()`
  -> `_task_process_entry()` -> `Manager`.
- Preserve TID format and immutability.
- Preserve forward-only task state transitions.
- Preserve registry record shape for active/stopped managers. Existing `tid`,
  `pid`, `status`, `requests`, `ctrl_in`, `ctrl_out`, `outbox`, and `role`
  fields must continue to exist.
- Preserve canonical-manager selection semantics for the spawn queue. Do not
  change the canonical manager queue identity or leadership rules in this slice.
- Preserve `weft manager serve` as the only foreground/supervisor path.
- Do not add new CLI flags, new queue names, or new TaskSpec fields.
- Do not route `manager serve` through detached-launch code just to share more.
  Wrapper logic and runtime logic are different boundaries.
- Keep bootstrap failure diagnostics bounded to startup. Do not add a new
  persistent logging subsystem.
- Best-effort cleanup failures for temporary stderr capture must not downgrade a
  genuinely successful manager start.
- No new dependency.

Hidden couplings to respect:

- `weft run` and `weft manager start` both rely on `_ensure_manager()`.
- Manager startup success is visible through at least three surfaces:
  manager registry, task log, and PID liveness.
- The status surface now infers `failed` for dead host-runner tasks with stale
  `running` snapshots. Bootstrap changes must not fight that reconciliation.
- `tests/cli/test_cli_manager.py` currently proves only immediate activation.
  New tests must prove structural detachment, not only another immediate list call.

Out of scope:

- redesign of manager leadership or registry schema
- status-surface redesign beyond startup-related regressions
- service-manager features beyond the existing `manager serve` contract
- permanent manager stderr logging or log rotation
- multi-manager pools, replacement policies, or PID-file support

## Tasks

1. Lock the detached-bootstrap contract in the specs before changing code.
   - Outcome: the spec says what "detached background manager started" means in
     observable terms, not just implementation prose.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Add explicit language that detached bootstrap success requires:
     - launcher-created independent background process
     - matching canonical registry record for the requested manager TID
     - live PID confirmation after a bounded startup stability window
     - surfaced failure diagnostics on early launch failure
   - Keep the public CLI shape unchanged in the spec.
   - Stop if the spec draft starts inventing a new public readiness protocol or
     new CLI flags. That is not needed for this fix.
   - Done when the spec distinguishes detached wrapper logic from the shared
     manager runtime and links this plan.

2. Introduce a detached-launch wrapper that breaks the parent lifecycle dependency.
   - Outcome: `_start_manager()` no longer launches the durable manager runtime
     as a plain direct child of the CLI process.
   - Files to touch:
     - `weft/commands/_manager_bootstrap.py`
     - `weft/manager_detached_launcher.py` (new)
     - `weft/manager_process.py`
   - Preferred design:
     - keep `_build_manager_process_command()` as the source of truth for the
       real runtime command
     - add a thin wrapper module that receives the encoded manager-runtime
       command, launches it with platform-appropriate detached semantics, and
       reports structured spawn metadata back to the caller
     - POSIX path must create a new session/process-group boundary and must not
       leave the durable manager process in the caller process group
     - Windows path should use the standard detached/new-process-group creation
       flags rather than inventing a second runtime model
   - Keep wrapper responsibilities narrow:
     - spawn the real manager runtime
     - return manager PID plus bootstrap-diagnostic metadata
     - do not rebuild TaskSpecs
     - do not read or write manager queues
   - Stop if wrapper code starts duplicating registry polling, manager spec
     construction, or `Manager` runtime logic.
   - Done when detached bootstrap has a real wrapper boundary and
     `weft.manager_process` remains the only durable runtime entry point.

3. Strengthen startup observation and failure reporting in `_start_manager()`.
   - Outcome: success now means "expected manager pid is live and durably visible,"
     not "registry flickered once."
   - Files to touch:
     - `weft/commands/_manager_bootstrap.py`
     - `weft/context.py` only if a repo-owned startup-log path helper is needed
   - Change `_start_manager()` to:
     - start the detached-launch wrapper instead of the raw runtime
     - capture wrapper output that includes the actual manager pid and any early
       stderr location or stderr tail
     - poll for the specific `manager_tid`, not only the generic selected manager
     - require `record["pid"] == launched_manager_pid`
     - require the PID to remain live and the canonical active record to remain
       present through a short startup stability window before returning success
     - if a competing canonical manager wins and remains live, adopt it as
       `started_here=False` only after the launched manager is proven not to have
       satisfied the startup proof
     - if the launched manager exits or loses the startup proof, raise a failure
       that includes exit status plus useful stderr context
   - Keep the success output shape the same (`Started manager ...` / `Manager ... already running`)
     unless failure details require richer stderr text.
   - Treat startup-diagnostic cleanup as best-effort.
   - Stop if the implementation falls back to "first registry sighting wins" or
     starts selecting arbitrary active managers without matching the launched TID
     and PID.
   - Done when detached bootstrap failure messages are actionable and the parent
     only returns success after the bounded startup proof passes.

4. Add tests that prove structural detachment and startup-proof behavior.
   - Outcome: coverage no longer depends on a fragile PTY harness repro to catch
     the bug.
   - Files to touch:
     - `tests/commands/test_run.py`
     - `tests/commands/test_manager_commands.py`
     - `tests/cli/test_cli_manager.py`
     - `tests/cli/test_cli_serve.py` only for a focused regression assertion
   - Required proofs:
     - command/helper test: `_start_manager()` does not return success if the
       launched manager dies before the startup stability window finishes
     - command/helper test: startup failure surfaces exit-status and stderr
       context from the wrapper path
     - real CLI proof on POSIX: after `weft manager start` returns, the active
       manager PID is not in the caller CLI process group
       (use the recorded `caller_pid` from task-log payloads or a wrapper process
       pid; do not depend on a PTY cleanup policy)
     - real CLI proof: the manager remains active and discoverable after the
       short-lived caller process exits
     - regression proof: `weft manager serve` still works unchanged
   - Keep real broker-backed lifecycle behavior in the CLI tests. Do not replace
     the detached-lifecycle proof with mock-only tests.
   - Allow narrow mocking only at the wrapper process boundary for deterministic
     stderr/exit-status error-path tests.
   - Stop if the test plan turns into "mock subprocess and assert flags." That
     would miss the contract again.
   - Done when the suite proves detached-launch separation structurally and the
     foreground path still passes.

5. Sync nearby docs and record any durable lesson.
   - Outcome: plan, spec, and implementation remain traceable and future work
     does not regress to ambient-lifecycle assumptions.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/lessons.md` only if implementation exposed a reusable rule
   - Keep the docs explicit that `manager start` is detached bootstrap and
     `manager serve` is foreground supervision.
   - If a reusable lesson emerges, capture the rule directly, for example:
     "background control-plane bootstrap must prove independent lifecycle, not
     merely initial registry visibility."

## Testing Plan

Run in dependency order:

1. `./.venv/bin/python -m pytest tests/commands/test_run.py tests/commands/test_manager_commands.py -q`
2. `./.venv/bin/python -m pytest tests/cli/test_cli_manager.py tests/cli/test_cli_serve.py -q`
3. `./.venv/bin/python -m pytest tests/commands/test_status.py tests/cli/test_status.py -q`
4. `./.venv/bin/python -m mypy weft`
5. `./.venv/bin/python -m ruff check weft`

Keep real:

- real manager registry replay
- real broker-backed task-log inspection
- real detached `manager start` CLI flow for at least one lifecycle proof
- real foreground `manager serve` CLI flow for regression coverage

Mock only:

- wrapper-process error injection for narrow stderr / exit-status paths that are
  hard to trigger deterministically with real subprocess timing

Observable success criteria beyond unit tests:

- detached `manager start` returns success and the resulting manager remains
  listed as active from a fresh CLI process
- on POSIX, the active manager pid is not in the caller CLI process group
- early detached-start failure yields actionable stderr or exit diagnostics
- `manager serve` behavior remains unchanged

## Rollout and Rollback

Rollout notes:

- Ship the detached-launch wrapper and the stronger startup proof together.
  Landing only one side is unsafe:
  - wrapper without stronger proof still allows false-positive success
  - stronger proof without wrapper still leaves lifecycle parent-dependent
- There is no queue-schema or TaskSpec compatibility rollout here. Existing live
  managers remain manageable because registry shape and control queues stay the same.
- `manager serve` must remain on the shared runtime path but off the detached wrapper.

Rollback notes:

- Revert the detached-launch wrapper and the startup-proof change together.
  Do not leave `_start_manager()` expecting wrapper metadata while launching the
  raw runtime directly.
- If a temporary startup-log file path was added, rollback must also remove the
  creation and cleanup path in the same change.

## Review Note

Independent review is required before implementation and again after the
launcher/startup-proof slice lands. Available reviewer families in this
environment include provider-backed Claude, Gemini, and Codex. Prefer a
non-Codex reviewer for the first pass because this plan is written by Codex.

Recommended review checkpoints:

1. Review this plan before code starts.
2. Review the detached-launch wrapper plus `_start_manager()` proof slice before
   broader test/doc cleanup.
3. Review the final diff for startup-failure diagnostics and `manager serve`
   regressions.
