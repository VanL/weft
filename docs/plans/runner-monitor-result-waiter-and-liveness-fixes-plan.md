# Runner Monitor, Result Waiter, and Liveness Fixes Plan

## Goal

Fix four correctness issues without widening scope into a broader runner or CLI
redesign:

1. caller-supplied external runner monitors must actually be used,
2. `weft run --wait` must stop maintaining a second one-shot result waiter,
3. host runtime liveness must stop treating zombie PIDs as running,
4. shared runner parity tests must skip cleanly when an optional runner extra is
   not installed.

The governing principle for this plan is simple: one shared code path per
behavior. Do not patch each caller separately when the bug lives in shared
logic.

## Source Documents

Source specs:

- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md) [TS-1.3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-2], [MF-3], [MF-5], [MF-6]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [STATE.1], [STATE.2], [EXEC.2], [OBS.3], [IMPL.1]
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-1.1.1]

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)

Relevant existing plans and context:

- [`docs/plans/runner-extension-point-plan.md`](./runner-extension-point-plan.md)
- [`docs/plans/task-lifecycle-stop-drain-audit-plan.md`](./task-lifecycle-stop-drain-audit-plan.md)
- [`docs/lessons.md`](../lessons.md)

## Context and Key Files

Files to modify:

- `weft/core/runners/subprocess_runner.py`
- `weft/commands/run.py`
- `weft/commands/result.py`
- `weft/core/runners/host.py`
- `tests/tasks/test_runner.py`
- `tests/commands/test_run.py`
- `tests/commands/test_result.py`
- `tests/cli/test_cli_run.py`
- `tests/commands/test_status.py`
- `tests/tasks/test_command_runner_parity.py`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`

Likely new shared helper file:

- `weft/commands/_result_wait.py`

Read first:

- `weft/core/runners/subprocess_runner.py`
  - Shared monitored subprocess boundary used by the built-in host path and by
    external runner plugins.
- `extensions/weft_docker/weft_docker/plugin.py`
  - Concrete external caller that constructs a runner-native monitor and passes
    it into `run_monitored_subprocess(...)`.
- `weft/commands/result.py`
  - Current source of truth for one-shot result waiting, terminal-status
    interpretation, output grace handling, and timeout exit-code semantics.
- `weft/commands/run.py`
  - Duplicate one-shot waiter and the CLI-side exit-code shaping that currently
    drifts from `weft result`.
- `weft/core/runners/host.py`
  - Host runner plugin `describe(...)` method that currently uses raw
    `psutil.pid_exists(...)`.
- `weft/helpers.py`
  - `pid_is_live(...)`, which already rejects zombie processes and is the
    helper this slice should standardize on for host runtime description.
- `tests/commands/test_run.py`
  - Existing queue-backed tests for the duplicate `run.py` wait helper.
- `tests/commands/test_result.py`
  - Existing queue-backed tests for the canonical result waiter.
- `tests/cli/test_cli_run.py`
  - Real CLI surface tests using `WeftTestHarness` and `run_cli()`.
- `tests/commands/test_status.py`
  - Existing public-status tests that are the right home for a host runtime
    liveness regression.
- `tests/tasks/test_runner.py`
  - Existing runner execution tests. Keep the supplied-monitor regression here
    unless a very small dedicated test file is clearly cleaner.
- `tests/tasks/test_command_runner_parity.py`
  - Shared runner parity suite with the optional docker test that currently
    imports the plugin too early.

Style and guidance:

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/testing-patterns.md`

Shared paths and helpers to reuse:

- `weft.commands.result` current wait semantics
  - `_await_single_result(...)`
  - `_terminal_status_from_event(...)`
  - `_terminal_error_message(...)`
- `weft.commands.run` / `weft.commands.result` queue helpers already in use
  - `aggregate_public_outputs(...)`
  - `drain_available_outbox_values(...)`
  - `poll_log_events(...)`
- `weft.helpers.pid_is_live(...)`
- `tests.helpers.weft_harness.WeftTestHarness`
- `tests.conftest.run_cli`
- `weft._runner_plugins.require_runner_plugin(...)`
- `pytest.importorskip(...)`

Current structure:

- `extensions/weft_docker/weft_docker/plugin.py` creates a
  `DockerContainerMonitor` and passes it to
  `run_monitored_subprocess(...)`.
- `weft/core/runners/subprocess_runner.py` immediately overwrites the incoming
  `monitor` argument with `None`, which makes the caller-supplied monitor path
  impossible.
- `weft/commands/result.py` already owns the canonical one-shot result wait
  path, including terminal-status parsing and the grace period between a
  terminal log event and final outbox readability.
- `weft/commands/run.py` duplicated that path in `_wait_for_task_completion(...)`
  and now disagrees with `weft result`.
- `weft/core/runners/host.py` uses `psutil.pid_exists(...)` in
  `HostRunnerPlugin.describe(...)`, so zombie PIDs can keep a runtime publicly
  visible as `running`.
- `tests/tasks/test_command_runner_parity.py` accesses `weft_docker.plugin`
  before any optional-plugin guard, so a plain dev install fails with
  `ModuleNotFoundError` instead of skipping.

Comprehension checks before editing:

1. Which function is the current source of truth for one-shot terminal-event
   interpretation and result grace handling?
2. Which shared function is supposed to honor a caller-supplied external
   monitor, and which external plugin currently exercises that seam?
3. Which helper already rejects zombie PIDs, and why is raw `pid_exists(...)`
   weaker than that helper for public runtime state?
4. Which tests in this slice must use real broker queues or real CLI execution,
   and where is a narrow monkeypatch acceptable because the helper itself is the
   contract under test?

## Invariants and Constraints

- Keep one one-shot result-wait spine. `weft run --wait` and `weft result` may
  format output differently, but they must not keep separate terminal-event
  classification or output-grace logic.
- Do not change queue names, message ordering assumptions, or the current grace
  behavior that tolerates `work_completed` appearing slightly before the final
  outbox message.
- Preserve TID format and immutability.
- Preserve forward-only state transitions and terminal-state immutability.
- Preserve the existing runner plugin contract. This slice fixes the shared
  monitor handoff bug; it does not redesign runner APIs.
- If a caller passes `monitor=...`, that monitor wins. Do not instantiate a
  second monitor from `monitor_class` as a side effect.
- Keep monitor failures best-effort in the current spirit of the code.
  Monitoring failure may disable metrics collection, but it must not silently
  change a successful subprocess outcome into a different terminal state unless
  existing logic already does so.
- Host liveness scope is narrow here: fix `HostRunnerPlugin.describe(...)`.
  Do not expand this slice into a repo-wide PID-liveness refactor unless a
  blocker proves that impossible.
- Optional-runner shared tests must skip cleanly when the optional extra is not
  installed. Do not “solve” the parity-test issue by making docker a required
  shared-suite dependency.
- No new public CLI flags, no new dependency, and no drive-by refactor.
- No new second helper table in `weft/commands/run.py` for terminal-event
  mapping after this change.
- Do not hard-code new semantics for `work_limit_violation` from memory or from
  the audit note. `weft run` must inherit the semantics that the canonical
  result waiter actually exposes today. If code and spec disagree, stop and
  document the disagreement before shipping.
- Interactive-session live streaming is out of scope unless a tiny local reuse
  falls out for free. Do not turn this plan into an interactive I/O redesign.
- `weft/commands/tasks.py` private-import cleanup is out of scope for this
  slice. Do not opportunistically fold it in.

## Rollout and Rollback

This slice should not change persisted formats or queue names. The only
rollout-sensitive part is the `run` versus `result` behavioral convergence.

Rollout order:

1. land the shared result-wait helper,
2. point `cmd_result` and `cmd_run` at it,
3. update docs after the shared path is proven by tests.

Rollback rules:

- Roll back the shared result-wait extraction and both callers together.
  Reverting only one command would restore the same duplicated-path bug that
  caused the audit finding.
- The monitor handoff fix, host liveness fix, and parity-test hardening can be
  reverted independently if needed.

## Tasks

1. Fix the supplied-monitor handoff in the shared subprocess boundary.
   - Outcome:
     - caller-supplied monitors are actually used by
       `run_monitored_subprocess(...)`.
   - Files to touch:
     - `weft/core/runners/subprocess_runner.py`
     - `tests/tasks/test_runner.py`
   - Read first:
     - `weft/core/runners/subprocess_runner.py`
     - `extensions/weft_docker/weft_docker/plugin.py`
     - existing timeout/limit tests in `tests/tasks/test_runner.py`
   - Tests first:
     - add a narrow regression around `run_monitored_subprocess(...)` with:
       - a real short-lived subprocess,
       - a fake monitor object implementing the methods the helper actually
         calls (`start`, `check_limits`, `last_metrics`, `snapshot`, `stop` as
         needed),
       - and a guard that `load_resource_monitor(...)` is not called when a
         monitor object was already supplied.
     - assert at least:
       - `start(...)` receives the worker PID,
       - the supplied monitor is stopped,
       - returned metrics come from the supplied monitor path when available.
   - Implementation rules:
     - remove the local overwrite that discards the incoming `monitor`.
     - preserve the existing fallback path:
       only load `monitor_class` when no explicit monitor was supplied.
     - do not patch the Docker plugin to work around the bug. Fix the shared
       boundary once.
   - Stop if:
     - you are inventing a new monitor abstraction,
     - or editing Docker-specific code becomes the main fix.
   - Done when:
     - the supplied-monitor regression fails before the fix and passes after it,
     - and the shared subprocess helper no longer creates a second monitor when
       one was already provided.

2. Extract one canonical one-shot result-wait helper.
   - Outcome:
     - one internal module owns terminal-status parsing, terminal-error
       extraction, and one-shot outbox-grace behavior.
   - Files to touch:
     - `weft/commands/_result_wait.py` (new)
     - `weft/commands/result.py`
     - `tests/commands/test_result.py`
     - `tests/commands/test_run.py`
   - Read first:
     - `_await_single_result(...)` in `weft/commands/result.py`
     - `_terminal_status_from_event(...)` and `_terminal_error_message(...)` in
       `weft/commands/result.py`
     - `_wait_for_task_completion(...)` in `weft/commands/run.py`
   - Required action:
     - move the canonical one-shot wait logic into a shared internal helper
       module instead of letting `run.py` import private symbols from
       `result.py`.
     - keep `cmd_result` and `cmd_run` as thin wrappers over that helper.
     - do not destabilize the persistent `weft result` path just to share more
       code. If persistent batching needs a small wrapper that remains in
       `result.py`, keep that wrapper there and share only the one-shot core.
     - pass resolved queue names or the resolved TaskSpec payload into the
       helper when practical, so `run.py` does not need to replay task history
       just to wait on a task it just launched.
     - preserve the current outbox grace behavior after `work_completed`.
   - Tests first:
     - move or rewrite the existing `_wait_for_task_completion(...)` queue tests
       so they cover the shared helper instead of the duplicate private helper.
     - add a regression proving a terminal timeout event is classified as
       `timeout`, not generic `failed`.
     - keep the regression that proves the helper waits for late outbox data
       after `work_completed`.
     - if you add a `work_limit_violation` regression, assert the semantics the
       canonical waiter already exposes. Do not invent new semantics in the
       test.
   - Stop if:
     - the change starts redesigning interactive streaming,
     - persistent batching becomes the main refactor,
     - or `run.py` still needs its own event-to-status mapping table afterward.
   - Done when:
     - one internal module owns one-shot terminal wait semantics,
     - and there is no duplicate one-shot terminal-status table left in
       `weft/commands/run.py`.

3. Make `weft run --wait` a thin wrapper over canonical result semantics.
   - Outcome:
     - `weft run --wait` and `weft result` agree on terminal classification and
       timeout exit-code behavior for the same one-shot task.
   - Files to touch:
     - `weft/commands/run.py`
     - `weft/commands/_result_wait.py`
     - `tests/commands/test_run.py`
     - `tests/cli/test_cli_run.py`
   - Read first:
     - the non-interactive wait path in `weft/commands/run.py`
     - the exit-code branches in `cmd_result(...)`
   - Required action:
     - delete `_wait_for_task_completion(...)` or reduce it to a no-logic shim
       that immediately delegates to the shared helper. Prefer deletion.
     - route the non-interactive wait path in `run.py` through the shared
       helper.
     - centralize the final status-to-exit-code mapping in `run.py` so timeout
       uses the canonical timeout exit code instead of generic failure.
     - keep interactive mode separate unless a tiny reuse is trivial and does
       not change its live stream contract.
   - Tests first:
     - add a real CLI regression in `tests/cli/test_cli_run.py` using
       `WeftTestHarness`, `run_cli()`, `sys.executable`, and a short timeout.
       Use the real CLI path, not a fake queue test, for the user-visible
       exit-code proof.
     - keep a queue-backed command-layer regression in `tests/commands/test_run.py`
       that proves `run` no longer reinterprets timeout as generic failure.
   - Manual smoke after tests pass:
     - run `weft run --timeout 0.1 -- python -c "import time; time.sleep(1)"`
       in a real project context and confirm exit code `124`.
     - run the same task with `--no-wait`, then `weft result <tid>`, and confirm
       the same terminal classification and error family.
   - Stop if:
     - you are adding CLI flags,
     - changing JSON payload shape,
     - or altering `cmd_result(...)` formatting just to simplify `run.py`.
   - Done when:
     - the one-shot `run` path has no independent terminal-event mapping,
     - and the reproduced timeout path matches `weft result`.

4. Make host runtime description use zombie-safe liveness.
   - Outcome:
     - public runtime state stops calling zombie host tasks “running”.
   - Files to touch:
     - `weft/core/runners/host.py`
     - `tests/commands/test_status.py`
   - Read first:
     - `weft.helpers.pid_is_live(...)`
     - `weft.commands.status._describe_runtime_handle(...)`
     - existing runtime-detail tests in `tests/commands/test_status.py`
   - Required action:
     - replace raw `psutil.pid_exists(...)` in
       `HostRunnerPlugin.describe(...)` with `pid_is_live(...)`.
     - keep the returned metadata and shape unchanged.
   - Tests first:
     - add a status-level regression that uses a real status reconstruction path
       and monkeypatches the liveness helper at the host-plugin seam.
     - assert a mapped host task reports runtime state `missing` when the host
       liveness helper says the PID is not truly live.
   - Test-design note:
     - a narrow monkeypatch of `pid_is_live(...)` is acceptable here because the
       helper choice is itself the contract under test.
     - do not mock the whole status command or queue replay.
   - Stop if:
     - this starts turning into a repo-wide PID audit.
   - Done when:
     - `HostRunnerPlugin.describe(...)` uses the shared zombie-safe liveness
       helper,
     - and a public status regression covers the bug.

5. Harden the optional docker parity test.
   - Outcome:
     - the shared parity suite skips cleanly when the docker extra is absent.
   - Files to touch:
     - `tests/tasks/test_command_runner_parity.py`
   - Read first:
     - `_skip_unavailable_runner(...)`
     - `weft._runner_plugins.require_runner_plugin(...)`
     - nearby `pytest.importorskip(...)` examples in the repo
   - Required action:
     - gate `weft_docker.plugin` access before monkeypatching it.
     - preferred pattern:
       - `module = pytest.importorskip("weft_docker.plugin")`
       - `monkeypatch.setattr(module.os, "name", "nt")`
     - keep `_skip_unavailable_runner("docker", ...)` as the real contract under
       test.
   - Tests:
     - the existing Windows skip regression should still prove that docker is
       considered unavailable on Windows when the plugin is present.
     - on environments without the docker extra, the test should skip cleanly
       instead of failing with `ModuleNotFoundError`.
   - Stop if:
     - the change starts pushing toward “install docker everywhere” as the
       solution.
   - Done when:
     - a plain dev environment can run the shared suite without this test
       exploding on import.

6. Update docs after the code path is unified.
   - Outcome:
     - spec docs match the code and no longer leave room for the same drift.
   - Files to touch:
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
     - optionally `docs/lessons.md` if implementation exposes a reusable lesson
   - Read first:
     - the implementation snapshots in the touched spec files
     - the final merged diff for Tasks 2 and 3
   - Required action:
     - update `CLI-1.1.1` implementation text and exit-code notes so `weft run`
       timeout behavior matches reality.
     - update any message-flow implementation notes that still imply duplicated
       `run` versus `result` wait logic after consolidation.
     - add a durable lesson only if the implementation uncovered a repeated
       repo pattern worth preserving, for example “do not let multiple commands
       own terminal-event mapping tables.”
   - Stop if:
     - doc text starts describing behavior the tests do not prove.
   - Done when:
     - spec prose and implementation mapping match the new shared path,
     - and there is no known doc/code drift around timeout exit semantics.

## Testing Plan

Use red-green TDD where practical. For each task, make the regression fail
first, then implement the smallest change that makes it pass.

Async test rule:

- when a test is proving result visibility, use the existing queue-backed wait
  helpers or bounded polling. Do not turn `work_completed` into an assumption
  that a single immediate outbox read must succeed.

Targeted commands:

1. `uv run --extra dev pytest tests/tasks/test_runner.py -q`
2. `uv run --extra dev pytest tests/commands/test_result.py tests/commands/test_run.py -q`
3. `uv run --extra dev pytest tests/cli/test_cli_run.py -q`
4. `uv run --extra dev pytest tests/commands/test_status.py -q`
5. `uv run --extra dev pytest tests/tasks/test_command_runner_parity.py -q`

Focused smoke if a targeted file is still too large:

1. `uv run --extra dev pytest tests/tasks/test_runner.py -k monitor -q`
2. `uv run --extra dev pytest tests/commands/test_result.py -k timeout -q`
3. `uv run --extra dev pytest tests/commands/test_run.py -k wait -q`
4. `uv run --extra dev pytest tests/cli/test_cli_run.py -k timeout -q`
5. `uv run --extra dev pytest tests/commands/test_status.py -k runtime -q`
6. `uv run --extra dev pytest tests/tasks/test_command_runner_parity.py -k docker_runner_is_skipped_on_windows -q`

Merge gate:

1. `uv run --extra dev pytest -q`
2. `uv run --extra dev mypy weft`
3. `uv run --extra dev ruff check weft`

What must stay real:

- Real subprocess execution for the supplied-monitor regression.
- Real broker-backed queues for shared result-wait tests.
- Real CLI execution through `run_cli()` plus `WeftTestHarness` for the timeout
  exit-code proof.
- Real status reconstruction path for the host liveness regression.

What may be narrowly faked:

- the supplied monitor object in Task 1,
- the `pid_is_live(...)` helper return value in Task 4,
- optional-plugin availability through `pytest.importorskip(...)` in Task 5.

What not to mock:

- `simplebroker.Queue` in tests that claim to prove durable result behavior.
- `WeftTestHarness` or manager lifecycle for the CLI timeout regression.
- task-log replay or status reconstruction when proving the host liveness fix.

## Verification and Gates

Per-task gates:

- Task 1:
  - supplied-monitor regression fails before the code change and passes after it
  - `run_monitored_subprocess(...)` no longer overwrites the incoming monitor
- Tasks 2 and 3:
  - grep the final `weft/commands/run.py` diff and confirm there is no second
    one-shot event-to-status table
  - the timeout CLI regression returns `124`
  - `tests/commands/test_result.py` still proves late outbox reads after
    `work_completed`
- Task 4:
  - public runtime state shows `missing` when the host liveness helper says the
    PID is not live
- Task 5:
  - the docker parity test no longer fails with `ModuleNotFoundError` when the
    optional extra is absent
- Task 6:
  - docs describe the same timeout and wait semantics the code now implements

Stop-and-re-evaluate conditions:

- a second one-shot wait helper appears during implementation
- `run.py` and `result.py` still each own their own terminal-event mapping table
- the change requires rewriting interactive mode to make progress
- queue-history reads start moving toward fixed-size snapshots where generator
  replay already exists
- the test plan drifts toward “mock the queue” or “mock the CLI” just to make
  the change easy
- the implementation starts broadening the host liveness fix into unrelated PID
  helper cleanup

## Independent Review Loop

Run an independent review after Task 3 and again before merge. Prefer a
reviewer who did not author the plan or main implementation. A different agent
family or a human reviewer is ideal.

Review pass after Task 3:

1. Is there still any independent one-shot terminal-status table in
   `weft/commands/run.py`?
2. Did the extraction accidentally change persistent result batching or
   interactive live-stream behavior?
3. Does the shared helper own timeout classification and outbox grace in one
   place?

Review pass before merge:

1. Does the supplied-monitor fix live in the shared subprocess helper rather
   than a Docker-only workaround?
2. Are the queue-visible and CLI-visible contracts proven with real tests rather
   than call-list mocks?
3. Is the host liveness fix narrow and correct?
4. Does the docker parity test now degrade cleanly when the optional extra is
   missing without weakening the actual contract under test?
5. Do the docs match the final behavior?

## Out of Scope

- interactive-session streaming redesign
- repo-wide PID-liveness cleanup
- `weft/commands/tasks.py` private-import cleanup
- Docker CI provisioning or making docker a required shared-suite dependency
- broader runner plugin redesign
- new public CLI flags or output-shape changes unrelated to these bugs

## Fresh-Eyes Review

This plan was re-checked against the common ways an implementation could drift.
These are the specific ambiguities that were corrected in the plan:

1. The tempting “small fix” for the waiter bug is to patch `run.py` to mimic
   `result.py`. That is the wrong direction. It preserves two code paths. The
   plan now requires a shared internal helper instead.
2. The audit finding wording around `work_limit_violation` could push the
   implementer into hard-coding timeout semantics for that event. The plan now
   forbids that. `weft run` must inherit whatever the canonical waiter actually
   exposes.
3. The host liveness bug could easily turn into a broad PID cleanup project.
   The plan now makes the narrow scope explicit: fix
   `HostRunnerPlugin.describe(...)` and cover it at the public status surface.
4. The external-monitor bug could be “fixed” in the Docker plugin only. That
   would leave the shared boundary broken for every external runner. The plan
   now explicitly forbids Docker-only workarounds.
5. The parity-test issue could drift into making docker mandatory for the shared
   suite. The plan now makes the intended direction explicit: skip cleanly when
   the optional extra is absent.
6. Interactive mode is related but not the same contract. The plan now calls it
   out as out of scope unless a tiny local reuse falls out without changing
   behavior.

If implementation pressure still pulls this work into a materially broader
direction than the four findings above, stop and re-plan instead of smuggling a
larger refactor in under the bug-fix label.
