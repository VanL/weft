# `weft manager serve` Supervised Manager Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## Goal

Add a minimal top-level `weft manager serve` command for operators who want a
supervisor-managed persistent manager process. This command should solve the
`systemd` / `launchd` / `supervisord` use case without changing the normal
`weft run` or `weft manager start` experience: it must run the canonical
manager in the foreground, disable idle timeout in this mode, and shut down
cleanly on supervisor stop signals. Keep the design small. Do not add daemon
features, adoption logic, PID files, or supervisor-specific protocols.

## Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-1.5], [MA-1.7], [MA-3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-6], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [MANAGER.1], [MANAGER.6], [MANAGER.7]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-0.2], [CLI-1.1] and the `worker` command section

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)

Relevant current implementation and docs:

- [`weft/_constants.py`](../../weft/_constants.py)
- [`weft/commands/_manager_bootstrap.py`](../../weft/commands/_manager_bootstrap.py)
- [`weft/manager_process.py`](../../weft/manager_process.py)
- [`weft/core/launcher.py`](../../weft/core/launcher.py)
- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`weft/cli.py`](../../weft/cli.py)
- [`weft/commands/manager.py`](../../weft/commands/manager.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- [`tests/conftest.py`](../../tests/conftest.py)
- [`README.md`](../../README.md)

Source spec gap:

- There is no current `weft manager serve` spec section. The implementation must add
  it before or alongside the code change. Do not treat this as an
  implementation-only refactor.

## Target Contract

This section is the contract to implement. Do not improvise beyond it.

1. `weft manager serve` is a new top-level CLI command.
   - Shape: `weft manager serve [--context PATH]`
   - No other flags in the first slice.

2. `weft manager serve` runs the canonical manager in the foreground.
   - The command blocks until the manager exits.
   - It is the correct target for `systemd`, `launchd`, and `supervisord`.
   - `weft manager start` remains the detached bootstrap command and is not the
     right target for supervisor `ExecStart` / `ProgramArguments` / `command`.
   - The supervisor-owned process is the actual manager runtime. `weft manager serve`
     must not spawn a child manager process and merely wait on it.

3. `weft manager serve` uses the same canonical manager spec builder as detached
   bootstrap.
   - No user-supplied manager TaskSpec input.
   - No direct `Manager(...)` construction in CLI code.
   - No second durable manager execution path.

4. `weft manager serve` forces persistent-manager lifetime in this mode.
   - The manager TaskSpec used by `weft manager serve` must set `metadata.idle_timeout`
     to `0.0`.
   - This override is local to `weft manager serve`.
   - `weft run` and `weft manager start` keep their current timeout behavior.

5. `weft manager serve` refuses to start if another live canonical manager already
   exists for the same context.
   - Exit code is `1` with a clear operator-facing message.
   - Do not silently succeed by “attaching” to an existing manager.
   - Do not auto-stop or replace the existing manager.
   - This preflight is best-effort. If two starters race, existing
     manager-side leadership convergence remains the final safety net.

6. Supervisor stop signals must map to graceful manager drain.
   - `SIGTERM` and `SIGINT` on the manager process must behave like manager
     STOP, not like a hard kill.
   - That means: stop accepting new spawn work, apply the existing reserved
     queue policy, STOP current child tasks, wait for drain, then exit.
   - Preserve the current kill-style semantics for `SIGUSR1` if present.
   - Minimal-scope decision for this slice: `SIGINT` and `SIGTERM` are the
     same graceful-drain signal. No second-signal escalation behavior is added
     in this change.

7. `weft manager serve` is intentionally minimal.
   - No `sd_notify`.
   - No PID file.
   - No daemon / background mode.
   - No supervisor config generator.
   - No adoption or replacement flags.
   - No new readiness protocol beyond the existing registry record.

8. Output stays minimal.
   - Do not add a long-running log stream.
   - Do not add a fake “ready” banner unless readiness is actually proven.
   - Prefer no success-path stdout in the first slice. Rely on
     `weft manager list|status` and `weft status` for observation.

## Context and Key Files

Files to modify:

- `weft/cli.py`
- `weft/commands/__init__.py`
- `weft/commands/_manager_bootstrap.py`
- `weft/commands/serve.py` (new)
- `weft/manager_process.py`
- `weft/core/launcher.py`
- `weft/core/manager.py`
- `tests/commands/test_serve.py` (new)
- `tests/cli/test_cli_serve.py` (new)
- `tests/core/test_manager.py`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `README.md`

Only touch `tests/conftest.py` if two or more test files need the same new
long-running CLI helper. Prefer a file-local helper first.

Read first:

- `weft/commands/_manager_bootstrap.py`
  - Owns canonical manager bootstrap today.
  - Already knows how to build the canonical manager TaskSpec and detect the
    active canonical manager.
  - Existing manager preflight is best-effort only. Final convergence under a
    race is still owned by manager-side leadership yield.
  - If this module is absent on the implementer's branch, stop and reconcile
    with the manager bootstrap/lifecycle consolidation work before continuing.
    Do not recreate pre-consolidation split paths just to land `weft manager serve`.
- `weft/_constants.py`
  - Confirms the current `WEFT_MANAGER_LIFETIME_TIMEOUT` config/env surface.
- `weft/manager_process.py`
  - Current detached manager subprocess entry point.
  - This is the natural place to share foreground and detached manager runtime
    execution without duplicating the task-process loop.
- `weft/core/launcher.py`
  - `_task_process_entry()` is the current task-process loop and signal handler
    installation point.
  - Important boundary: the launcher installs OS signal handlers once and then
    delegates to `task.handle_termination_signal()`. The manager-specific fix
    belongs in `Manager.handle_termination_signal()`, not in a second launcher
    signal layer.
  - The shared `run_manager_process()` helper in this plan should be a thin
    wrapper around `_task_process_entry()`, not a replacement for it.
- `weft/core/manager.py`
  - Owns STOP drain, leadership yield, idle timeout, and the current signal
    behavior.
  - Important hidden issue: `handle_termination_signal()` currently terminates
    children and cancels immediately. That is wrong for supervisor-managed
    graceful stop.
  - Important existing exit path: after STOP drain completes,
    `_finish_graceful_shutdown()` sets `should_stop = True`. The signal-driven
    drain path must converge on that same exit point.
  - Established idle-timeout rule today: `idle_timeout <= 0` disables idle
    shutdown. `0.0` is therefore the existing no-timeout sentinel, not a new
    meaning introduced by this plan.
- `weft/cli.py`
  - Top-level Typer wiring. Keep this file thin.
- `tests/core/test_manager.py`
  - Already contains the closest real proofs for manager STOP drain and
    leadership behavior.
- `tests/cli/test_cli_manager.py`
  - Shows the current style for real CLI manager lifecycle tests.
- `tests/conftest.py`
  - `run_cli()` env setup and timeout diagnostics. Reuse its environment shape
    if a new long-running `Popen` helper is needed.
- `tests/helpers/weft_harness.py`
  - Real broker/process test harness. Use it instead of mocking broker or task
    state.

Local style rules and guidance to follow:

- Keep command modules thin. Put CLI argument parsing in `weft/cli.py` and the
  command behavior in `weft/commands/serve.py`.
- Reuse the shared manager lifecycle module. Do not reintroduce manager logic
  into CLI wrappers.
- Keep module docstrings and `Spec:` references current when adding or moving
  ownership.
- Use `from __future__ import annotations`.
- Prefer full type hints, `Path`, and existing helper functions over ad hoc
  shelling or custom process wrappers.

Shared paths to reuse. Do not duplicate these:

- `weft/commands/_manager_bootstrap.py`
  - `_build_manager_spec()`
  - `_select_active_manager()`
  - `_manager_record()` / `_list_manager_records()` if the command needs
    registry observation
- `weft/manager_process.py`
  - Shared manager runtime entry once refactored in this slice
- `weft/core/manager.py`
  - `_begin_graceful_shutdown()`
  - `_signal_children_to_stop()`
  - existing STOP drain path
- `weft.helpers`
  - `is_canonical_manager_record()`
  - `iter_queue_json_entries()`
  - `pid_is_live()`
  - `terminate_process_tree()`

Comprehension checks before editing:

1. Why is `weft manager start` not sufficient for `systemd` / `launchd` /
   `supervisord`?
2. Which function currently builds the canonical manager TaskSpec, and where is
   the detached manager process started?
3. What is the behavioral difference today between manager STOP and manager
   `SIGTERM`?
4. Which registry field marks the canonical default manager queue, and why must
   the `requests == weft.spawn.requests` record shape remain the canonical
   default-manager marker for `weft manager serve` too?

If the implementer cannot answer those four questions from the code and spec,
they are not ready to edit.

## Invariants and Constraints

- Keep one durable manager runtime spine:
  CLI wrapper -> shared manager lifecycle helper -> shared manager process
  runner -> `Manager`.
- The `weft manager serve` process itself must become the manager runtime. Do not turn
  it into a wrapper that supervises a second child manager process.
- Do not construct `Manager(db_path, spec)` directly in `weft manager serve`.
  Reuse the same task-process entry path that detached bootstrap uses.
- `weft manager serve` is additive. Do not change the public behavior of:
  - `weft run`
  - `weft manager start`
  - `weft manager stop`
  - `weft manager list|status`
- Preserve canonical-manager identity rules:
  canonical default managers are the live manager records whose `requests`
  field equals `weft.spawn.requests`.
- Preserve current TID format and immutability.
- Preserve forward-only state transitions.
- Preserve reserved queue policy behavior on manager STOP and drain.
- Preserve `spec` and `io` immutability after TaskSpec creation.
- Preserve spawn-based process behavior for broker-connected code.
- Preserve the current manager leader-election rules.
- `SIGTERM` / `SIGINT` for managers must become graceful drain signals.
  Do not spread that change to all tasks through `BaseTask` unless a concrete
  blocker forces it.
- Preserve `SIGUSR1` kill behavior if available.
- Preserve existing process-title behavior by reusing the shared task-process
  runtime path.
- No new dependency.
- No unrelated drive-by refactor.
- No queue-name changes.
- No new public persistence format.
- No mock-heavy substitute for real broker/process proofs when the real path is
  practical.

Out of scope for this slice:

- service file generation
- `sd_notify` or readiness IPC
- PID files
- replacing or adopting an existing manager
- special “serve” manager role or queue
- changes to manager exit-code semantics beyond preflight failures
- changes to `WEFT_MANAGER_REUSE_ENABLED`
- changes to detached bootstrap default lifetime policy
- supervisor restart-policy ownership inside Weft
- multi-manager pools or specialized manager queues

Stop-and-re-evaluate gates:

- A second manager execution path appears that does not run through the shared
  task-process entry.
- The implementation wants to instantiate `Manager` directly in CLI code.
- The implementation starts adding daemon features not required by the target
  contract.
- The implementation starts adding a second signal-handling layer in the
  launcher instead of fixing manager-specific signal delegation.
- The signal-handling fix starts modifying `BaseTask` or child task semantics
  broadly instead of keeping the change manager-local.
- The tests start mocking `Queue`, manager lifecycle, or process exit instead
  of using the real broker/process path.

## Rollout, Rollback, and Risk Notes

Rollout shape:

- This is an additive CLI surface plus a manager signal-semantics correction.
- There is no queue migration and no persistence-format migration.
- The only cross-cutting runtime behavior change is manager `SIGTERM` /
  `SIGINT` handling.

Risk concentration:

- The new command itself is low risk if it reuses the shared runtime path.
- The signal-handling correction is the real risky slice because it affects all
  managers, not only `weft manager serve`.
- The supervisor-facing edge is leadership yield after a start race. The plan
  must define that outcome clearly enough to avoid restart loops.

Rollback plan:

- If the new command is wrong but the signal fix is sound, revert only the
  `weft manager serve` CLI surface and docs.
- If the signal fix causes regressions, revert the `Manager` signal change and
  the `weft manager serve` command together. Do not leave `weft manager serve` shipping with
  abrupt supervisor-stop behavior.

One-way-door note:

- Do not introduce docs that promise platform-specific service-manager features
  that the code does not implement. Documentation drift here becomes an
  operational trap.

## Tasks

1. Lock the `weft manager serve` contract in the specs before writing production code.
   - Outcome:
     - The exact command shape and shutdown semantics are written down before
       the implementation starts.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Read first:
     - `docs/specifications/03-Manager_Architecture.md` [MA-1.5], [MA-1.7], [MA-3]
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-7]
     - `docs/specifications/10-CLI_Interface.md` command structure and manager section
   - Required changes:
     - Add a `weft manager serve` section to the CLI spec.
     - Extend the manager/bootstrap architecture spec to describe detached
       bootstrap versus foreground supervised serve.
     - Extend the message-flow spec so `MF-7` covers both detached bootstrap
       and foreground serve, without inventing a separate manager role.
     - Update the invariants spec so manager external TERM/INT is documented as
       graceful drain behavior.
     - Update nearby implementation-mapping notes and related-plan backlinks in
       the touched spec files so code, spec, and plan stay bidirectional.
   - Be explicit:
     - `weft manager start` remains detached and idempotent.
     - `weft manager serve` is foreground and non-idempotent.
     - `weft manager serve` refuses an already-active canonical manager.
     - `weft manager serve` forces `idle_timeout=0`.
   - Stop if:
     - the spec text starts promising PID files, readiness notifications, or
       replacement/adoption behavior
   - Done when:
     - a zero-context implementer can read the specs and tell the difference
       between `weft manager start` and `weft manager serve` without looking at code

2. Add red tests for the two load-bearing behaviors: foreground serve and
   supervisor-signal drain.
   - Outcome:
     - There are failing tests that prove the current code does not yet satisfy
       the target contract.
   - Files to touch:
     - `tests/core/test_manager.py`
     - `tests/commands/test_serve.py` (new)
     - `tests/cli/test_cli_serve.py` (new)
   - Read first:
     - `tests/core/test_manager.py`
     - `tests/cli/test_cli_manager.py`
     - `tests/conftest.py`
     - `tests/helpers/weft_harness.py`
   - Required tests:
     - A core manager test proving `SIGTERM` drains non-persistent children the
       same way STOP does.
       - Reuse the existing STOP-drain test pattern in
         `tests/core/test_manager.py`.
       - Use a real child process and real queues.
       - Assert the child is stopped via the drain path, not hard-killed
         immediately.
     - A core manager test proving `SIGUSR1` keeps its current kill-style
       behavior when the platform exposes that signal.
       - Be explicit in the test docstring: current behavior is the existing
         immediate kill path and terminal `task_signal_kill` event.
     - A command-wrapper test for `weft.commands.serve.serve_command()`.
       - This can monkeypatch the shared helper because the wrapper itself is
         the unit under test.
       - Keep it narrow: context construction, delegation, exit code / message.
     - A real CLI test showing `weft manager serve` stays alive until explicitly
       stopped.
       - Start `python -m weft.cli serve ...` with `subprocess.Popen`.
       - Poll the real registry until the manager appears.
       - Assert the process is still running before stop.
       - Submit real work through `weft run` while `weft manager serve` is active and
         prove the system still has exactly one active canonical manager.
       - Stop it explicitly, then assert the foreground process exits.
     - A real CLI test showing the foreground `weft manager serve` process handles
       `SIGTERM` cleanly.
       - Start `python -m weft.cli serve ...`.
       - Wait for the manager registry record.
       - Send `process.terminate()`.
       - Assert the process exits and the manager ends through the graceful
         drain path rather than abrupt child termination.
     - A real CLI test showing a second `weft manager serve` refuses to start while the
       first canonical manager is active.
     - A real CLI or helper-level test proving `weft manager serve` forces the
       no-timeout manager lifetime contract.
       - Recommended real-path proof: set
         `WEFT_MANAGER_LIFETIME_TIMEOUT=0.05`, start `weft manager serve`, wait longer
         than that while idle, and assert the foreground manager is still
         alive.
   - Test-seam guidance:
     - Do not mock `simplebroker.Queue`.
     - Do not mock manager `process_once()`.
     - Do not mock `_task_process_entry()`.
     - Use a file-local `popen_cli()` helper if needed. Only lift it into
       `tests/conftest.py` if a second test file actually needs it.
   - Red-phase commands:
     - `uv run pytest tests/core/test_manager.py -k sigterm -q`
     - `uv run pytest tests/commands/test_serve.py -q`
     - `uv run pytest tests/cli/test_cli_serve.py -q`
   - Red-phase note:
     - For new command/module tests, `ImportError` or unknown-command failures
       are acceptable red proofs before Task 3/Task 5 land. Do not paper over
       that by creating fake stubs that bypass the real runtime path.
   - Stop if:
     - the test plan depends on sleeps for correctness rather than bounded
       polling and real queue state
     - the tests start faking the manager runtime instead of launching it
   - Done when:
     - the tests fail on current code for the expected reasons

3. Extract one shared foreground manager runner without creating a second
   durable path.
   - Outcome:
     - Detached bootstrap and foreground serve both run through one shared
       manager-process runtime function.
   - Files to touch:
     - `weft/manager_process.py`
     - `weft/commands/_manager_bootstrap.py`
   - Read first:
     - `weft/manager_process.py`
     - `weft/core/launcher.py`
     - `weft/commands/_manager_bootstrap.py`
   - Required action:
     - Factor `weft/manager_process.py` so the actual manager runtime execution
       is a reusable function, and `main()` becomes the decoding wrapper around
       it.
     - Recommended shape:
       - `run_manager_process(task_cls_path, broker_target, spec, config, poll_interval)`
       - `spec` is a validated `TaskSpec`, not a JSON string
       - `main()` decodes argv, validates `TaskSpec`, and calls that function
       - `run_manager_process()` is a thin shared wrapper over
         `_task_process_entry()`
       - the new foreground serve helper calls that same function directly
     - Keep the current detached CLI bootstrap using `python -m
       weft.manager_process`.
     - Add a foreground serve helper in `weft/commands/_manager_bootstrap.py`
       that:
       - builds the canonical manager TaskSpec
       - overrides idle timeout to `0.0`
       - uses an explicit `_build_manager_spec(..., idle_timeout_override=0.0)`
         style parameter rather than mutating the resolved TaskSpec after the
         fact
       - preflights against `_select_active_manager()`
       - calls the shared manager runtime function in-process
   - Reuse:
     - `_build_manager_spec()`
     - `_select_active_manager()`
     - the shared task-process entry path
   - Small DRY rule:
     - If the same manager poll interval literal is needed in more than one
       place, extract a private module constant. Do not promote it to a public
       env var in this slice.
   - Verify explicitly in this task:
     - calling the shared runtime in the original CLI process does not require
       preserving any Typer-specific signal handling after command handoff
     - process-title and cleanup side effects remain acceptable when the CLI
       process becomes the manager runtime
   - Stop if:
     - the foreground helper starts calling `launch_task_process()`
     - the foreground helper instantiates `Manager` directly
     - detached and foreground paths start diverging in TaskSpec shape
   - Done when:
     - both detached and foreground manager execution use the same runtime
       function
     - `weft run` / `weft manager start` behavior stays unchanged in code shape
     - signal-related tests may still be red until Task 4 lands; that does not
       mean the Task 3 extraction itself drifted

4. Correct manager TERM/INT handling so supervisor stop drains instead of
   hard-killing children.
   - Outcome:
     - External `SIGTERM` / `SIGINT` against a manager triggers the same drain
       policy as STOP.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Read first:
     - `weft/core/manager.py` around `_handle_control_command()`,
       `_begin_graceful_shutdown()`, and `handle_termination_signal()`
   - Required action:
     - Generalize `_begin_graceful_shutdown()` only as much as needed so it can
       be reused by both:
       - STOP control messages
       - external TERM/INT signals
     - Keep STOP control-message behavior and events intact.
     - For external TERM/INT:
       - do not call the current immediate-kill `BaseTask` stop path
       - do not let `BaseTask` set `should_stop = True` before drain is
         complete
       - rely on the launcher's existing delegation to
         `task.handle_termination_signal()`, then keep the fix entirely inside
         `Manager.handle_termination_signal()`
       - begin manager drain
       - STOP current child tasks
       - let the existing drain loop finish shutdown
       - converge on `_finish_graceful_shutdown()` so the exit flag is still
         set in one place after drain completes
     - Preserve current kill-style handling for `SIGUSR1` if available.
     - Keep repeated signals idempotent when already draining.
     - Unless the spec update explicitly chooses a different existing event,
       prefer the current `task_signal_stop` terminal event for TERM/INT. Do
       not invent a new event name in this slice.
     - Race note for this task and Task 5: if two managers start concurrently,
       leader election remains the final convergence rule. The losing manager
       should exit through the existing leadership-yield path rather than a new
       ad hoc serve-only exit path.
     - Explicit supervisor-facing rule: if a foreground `weft manager serve` process
       loses leadership after startup, it should exit cleanly with exit code
       `0` after leadership drain completes. Treat that path as successful
       convergence, not as a failure restart signal.
   - Design warning:
     - Do not “fix” this by changing `BaseTask.handle_termination_signal()` for
       every task. This slice is manager-specific.
   - Red/green focus:
     - make the new `SIGTERM` core test pass
     - keep existing STOP-drain and leadership tests green
   - Stop if:
     - the patch starts redesigning generic task signal semantics
     - the manager loses reserved-policy handling during signal drain
   - Done when:
     - external TERM/INT enters the same no-new-spawn + child-STOP + drain path
       as manager STOP

5. Add the top-level `weft manager serve` command as a thin wrapper.
   - Outcome:
     - The CLI exposes the new command without duplicating lifecycle logic.
   - Files to touch:
     - `weft/commands/serve.py` (new)
     - `weft/commands/__init__.py`
     - `weft/cli.py`
     - `tests/commands/test_serve.py`
     - `tests/cli/test_cli_serve.py`
   - Read first:
     - `weft/commands/manager.py`
     - `weft/cli.py`
   - Required action:
     - Add `serve_command(*, context_path: Path | None = None)` in
       `weft/commands/serve.py`.
     - The command module should:
       - build context
       - delegate to the shared foreground serve helper
       - return exit code plus optional message in the same style as other
         command modules
     - Register `weft manager serve` as a top-level Typer command in `weft/cli.py`.
     - If `weft/commands/__init__.py` re-exports command modules for package
       consumers, add the minimal `serve` export needed to match the existing
       package pattern. Do not add broader package reshaping.
   - Wrapper constraints:
     - Keep CLI parsing in `weft/cli.py`.
     - Keep lifecycle behavior in `weft/commands/serve.py` and
       `_manager_bootstrap.py`.
     - Do not add long-running stdout streaming.
   - Suggested behavior:
     - Preflight failures should emit a single clear message and exit non-zero.
     - Success path may remain quiet while the process is running.
     - Leadership-yield convergence after startup should be treated as a clean
       exit, not a wrapper error.
   - Stop if:
     - the wrapper starts reimplementing manager preflight, TaskSpec building,
       or signal logic
   - Done when:
     - the wrapper tests pass
     - the real CLI test shows `weft manager serve` blocks until stop

6. Update operator-facing docs so the right command is obvious.
   - Outcome:
     - Operators can tell when to use `weft manager serve` versus `weft manager start`.
   - Files to touch:
     - `README.md`
   - Required doc content:
     - A short “supervised manager” section in `README.md`
     - Explicit warning that `weft manager start` daemonizes and exits, so it is
       not the right service-manager entrypoint
     - Explicit note that `weft manager serve` is foreground and should be used by
       `systemd`, `launchd`, and `supervisord`
     - Explicit note that restart policy belongs to the supervisor, not Weft
     - Explicit note that this minimal slice does not add a Weft-side drain
       timeout or second-signal escalation; operators should configure the
       supervisor's stop timeout accordingly
     - Explicit note that `weft manager stop <tid>` still sends a graceful STOP,
       but an auto-restarting supervisor may bring the service back unless the
       supervisor itself is stopped
   - Keep docs minimal:
     - one concise example or snippet is enough
     - do not write a full operations manual
   - Stop if:
     - the docs start describing platform-specific automation that the code
       does not provide
   - Done when:
     - a new operator can choose the right command without reading the code

7. Run the full verification ladder and require one independent review before
   merge.
   - Outcome:
     - The change is proven at the contract level and reviewed for author
       blindness.
   - Verification order:
     1. New targeted tests:
        - `uv run pytest tests/core/test_manager.py -k "sigterm or sigusr1" -q`
        - `uv run pytest tests/commands/test_serve.py -q`
        - `uv run pytest tests/cli/test_cli_serve.py -q`
     2. Neighboring manager lifecycle suites:
        - `uv run pytest tests/commands/test_run.py tests/commands/test_manager_commands.py tests/cli/test_cli_manager.py tests/core/test_manager.py -q`
     3. Broader CLI confidence pass:
        - `uv run pytest tests/cli/test_cli_run.py -q`
     4. Static checks:
        - `uv run mypy weft`
        - `uv run ruff check weft tests`
   - Manual smoke test before calling it done:
     - Terminal 1: `weft manager serve --context /path/to/project`
     - Terminal 2: `weft run --function tests.tasks.sample_targets:simulate_work --kw duration=5 --no-wait --context /path/to/project`
     - Confirm `weft manager list --context /path/to/project` shows exactly one
       active canonical manager.
     - Send `SIGTERM` to the `weft manager serve` process or stop it through the
       supervisor.
     - Confirm the manager drains and the child task stops through the STOP
       path rather than a hard-kill path.
   - Independent review requirement:
     - Use a different model family if available.
     - Review after implementation, not just at the plan stage.
     - Ask the reviewer whether they could implement or sign off confidently.
   - Stop if:
     - any verification failure is “explained away” without a concrete fix
     - the docs and code disagree on preflight or signal behavior
   - Done when:
     - all verification commands pass
     - the independent review findings are resolved or explicitly rejected with
       written rationale

## Test Design Notes

Keep these tests real:

- manager registry writes and reads
- foreground CLI process lifetime
- STOP messages and drain behavior
- child process exit under manager drain

It is acceptable to monkeypatch only these narrow seams:

- `build_context()` inside a command-wrapper unit test
- the shared foreground helper inside a command-wrapper unit test

Do not replace real runtime proof with:

- mocked broker queues
- mocked manager event loops
- mocked subprocess lifetimes
- call-list assertions that never read a real queue or observe a real exit

If the test needs timing:

- use bounded polling with short sleeps
- prefer existing harness helpers and queue state
- register spawned PIDs with `WeftTestHarness` or clean them up explicitly in
  `finally` blocks

## Final Review Checklist

Before implementation is called done, verify all of these are true:

- `weft manager serve` is foreground only
- `weft manager serve` forces `idle_timeout=0`
- `weft manager serve` refuses an already-active canonical manager
- `weft manager serve` reuses the canonical manager TaskSpec builder
- detached bootstrap still uses the same manager runtime code path
- manager `SIGTERM` / `SIGINT` drains like STOP
- manager `SIGUSR1` kill behavior still works if supported on the platform
- no new daemon / PID / readiness features slipped in
- README and specs both explain why service managers must use `weft manager serve`
