# Manager Replace Start And Serve Plan

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.4], [MA-1.7], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-7]; docs/specifications/10-CLI_Interface.md [CLI-1.1.2], manager-control surfaces; docs/specifications/07-System_Invariants.md [MANAGER.6], [MANAGER.7], [MANAGER.8], [MANAGER.9], [MANAGER.10]
Superseded by: none

## 1. Goal

Add explicit operator replacement for manager startup: `weft manager start
--replace` and `weft manager serve --replace`. Replacement means the caller is
declaring that the new manager should become the manager for this context. For a
live incumbent, the implementation must first send graceful STOP to the
incumbent manager's `ctrl_in` queue, then record the incumbent manager's latest
service-owner row as `superseded` in `weft.state.services` so operators can
distinguish intentional replacement from ordinary shutdown. Replacement v1 does
not wait for stopped confirmation after the STOP write; convergence moves on
from the old owner once the `superseded` row is written. The existing foreground
serve stale-row cleanup may still retire unreachable foreground
`external-supervisor` rows without STOP, and that retirement also writes
`superseded` rather than plain `stopped`.

## 2. Source Documents

- `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-1.7], [MA-3]:
  manager registry, control, bootstrap, and foreground serve behavior.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-7]: shared manager
  bootstrap flow for `weft run`, `weft manager start`, and foreground serve.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.2] and manager-control
  surfaces: public CLI contract for `manager serve`, `manager start`, and
  `manager stop`.
- `docs/specifications/07-System_Invariants.md` [MANAGER.6], [MANAGER.7],
  [MANAGER.8], [MANAGER.9], [MANAGER.10]: shared bootstrap, control semantics,
  manager ownership, public dispatch, and STOP/KILL launch fences.
- `weft/core/service_convergence.py`: current service-owner parser accepts only
  `active`, `draining`, `stopped`, `terminal`, and `uncertain`; adding
  `superseded` is a service-owner contract update.
- Existing plans:
  - `docs/plans/2026-04-09-weft-serve-supervised-manager-plan.md` is completed
    historical context for foreground `manager serve`.
  - `docs/plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md` is
    completed historical context for shared lifecycle helpers.
  - `docs/plans/2026-05-09-manager-stop-timeout-hardening-plan.md` is completed
    context for caller-side STOP confirmation budgets.
- Local guidance:
  - `AGENTS.md`
  - `docs/agent-context/decision-hierarchy.md`
  - `docs/agent-context/engineering-principles.md`
  - `docs/agent-context/runbooks/writing-plans.md`
  - `docs/agent-context/runbooks/hardening-plans.md`
  - `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

## 3. Context And Key Files

Files to modify:

- `weft/core/manager_runtime.py`
- `weft/_constants.py`
- `weft/core/service_convergence.py`
- `weft/commands/manager.py`
- `weft/commands/serve.py`
- `weft/cli/app.py`
- `tests/commands/test_manager_commands.py`
- `tests/commands/test_serve.py`
- `tests/commands/test_run.py` if shared manager-runtime helper behavior needs
  direct unit coverage
- `tests/cli/test_cli_manager.py`
- `tests/cli/test_cli_serve.py`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/10-CLI_Interface.md`
- `README.md`
- `docs/plans/README.md`

Read first:

- `weft/core/manager_runtime.py`: `_ensure_manager()`, `_start_manager()`,
  `_serve_manager_foreground()`, `_stop_manager()`,
  `_await_manager_stop_confirmation()`, `_send_stop()`, `_mark_manager_stopped()`,
  `_select_active_manager()`, and `_manager_record_has_matched_pong()`.
- `weft/_constants.py`: `SERVICE_STATUS_*` constants and
  `LIVE_SERVICE_STATUSES`.
- `weft/core/service_convergence.py`: `ServiceOwnerStatus`,
  `parse_service_owner_row()`, `select_canonical_live_owner()`, and
  `reduce_service_ownership()`.
- `weft/commands/manager.py`: `start_command()` and `stop_command()`.
- `weft/commands/serve.py`: `serve_command()`.
- `weft/cli/app.py`: Typer command definitions for `manager start`, `manager
  serve`, and `manager stop`.
- `weft/core/manager.py`: `_handle_control_command()`,
  `_begin_graceful_shutdown()`, `_unregister_manager()`, and
  `_atexit_unregister()`.
- `tests/cli/test_cli_serve.py`: real foreground serve process coverage.
- `tests/cli/test_cli_manager.py`: manager start/stop CLI coverage.
- `tests/commands/test_manager_commands.py` and `tests/commands/test_serve.py`:
  command-layer helper tests.

Current structure:

- Ordinary `weft run` and `weft manager start` use `_ensure_manager()`, which
  selects an active manager or starts a detached manager.
- `weft manager serve` uses `_serve_manager_foreground()`, which currently
  rejects an already selected active manager before running the foreground
  manager process.
- `weft manager stop <tid>` uses `_stop_manager()`. `_stop_manager()` sends
  STOP through `_send_stop()` to the target control queue, then waits for
  stopped registry or process evidence. `_send_stop()` chooses the control
  queue from the registry row when present and falls back to `T{tid}.ctrl_in`.
  With `force=True`, `_stop_manager()` may terminate a host process tree after
  STOP confirmation fails.
- `weft.state.services` service-owner rows are parsed by
  `weft/core/service_convergence.py`. `superseded` does not exist yet and must
  be accepted as non-live manager/service-owner evidence before any code writes
  it.
- Manager tasks already understand `STOP`; `Manager._handle_control_command()`
  turns it into graceful child drain and writes a STOP ack. Do not add a second
  control channel.
- `Manager.__init__()` already registers an `atexit` cleanup hook, and
  `_atexit_unregister()` best-effort writes its own stopped registry row.
  This is useful cleanup, but it cannot cover SIGKILL, hard container death,
  supervisor kill paths that skip interpreter shutdown, or process crashes.

Shared path to reuse:

- Reuse `_send_stop()` for incumbent replacement. Do not implement a new queue
  writer for STOP.
- Reuse `_select_active_manager()` with `probe_stale=True` and the existing
  keyed PING/PONG helper for positive liveness checks.
- Reuse or minimally extend `_mark_manager_stopped()` for replacement so it can
  write `superseded` instead of `stopped`. Do not duplicate registry rewrite
  semantics in a second helper.
- Surface `_send_stop()` queue-write failures as the helper's return value
  rather than uncaught exceptions.

Comprehension questions before editing:

1. Which function currently writes STOP to a manager's `ctrl_in`, and how does
   it choose the control queue name?
2. Which registry evidence lets `_stop_manager()` decide that a manager stopped?
3. Why is a fresh foreground external-supervisor row without PONG not enough to
   block foreground `serve --replace` forever? See
   `_foreground_serve_blocking_manager()` in `weft/core/manager_runtime.py`.

## 4. Invariants And Constraints

- Replacement must send STOP to the incumbent manager's `ctrl_in` before
  marking that selected incumbent `superseded`.
- Replacement does not wait for STOP confirmation in v1. This accepts a short
  possible overlap where the old manager process is still draining or about to
  consume STOP, while convergence has already excluded that owner.
- A matched manager PING/PONG or registered runtime-liveness `live` result
  proves a live incumbent. Replacement of that incumbent must go through STOP.
- Foreground stale-row cleanup without STOP applies only when all of these are true:
  `runtime_handle.control.authority == "external-supervisor"`,
  `runtime_handle.metadata.foreground_serve is True`,
  `runtime_liveness_from_registered_probe(handle) != "live"`, and
  `_manager_record_has_matched_pong(...) is False`. Every other selected row
  blocks normal foreground serve startup.
- The replacement STOP path writes replacement outcome evidence as `superseded`
  after the STOP message is written successfully. It does not claim the old
  manager acknowledged STOP.
- `superseded` is non-live service-owner evidence. It must not be included in
  `LIVE_SERVICE_STATUSES`, must not be selected by
  `select_canonical_live_owner()`, and must not block future manager startup.
- Manager convergence is lowest non-superseded live TID, not just lowest TID.
  `superseded` rows are explicit exclusion evidence for that owner TID. A
  historical lower-TID manager with a latest `superseded` row must not regain
  leadership, block replacement startup, or cause a newer manager to yield.
- A still-running manager that observes its latest self-owned service-owner row
  is `superseded` must not refresh itself back to `active`. It should enter the
  normal stop/drain path; publishing a later `stopped` row is acceptable.
- Do not change ordinary `weft run` autostart behavior. Submissions should
  still auto-manage Weft by default.
- Do not add "wait for manager, do not autostart" in this slice. It may be a
  future explicit submission mode, but it is weaker than the operator intent
  needed here.
- Do not add a new `SUPERSEDE` or `REPLACE` control message in this slice.
  STOP is the control contract; `--replace` is CLI wrapper intent around STOP.
- Do not change queue names, TID format, TaskSpec schema, or result payloads.
- Do not weaken manager leadership or work-stealing semantics. Public spawn
  dispatch remains atomic reservation based.
- Runtime-only `weft.state.*` queues remain runtime evidence, not persisted
  configuration.
- Use generator-based registry reads through the existing helpers. Do not add
  fixed-limit registry scans.
- No new dependencies.
- No drive-by refactors.
- Concurrent `--replace` callers race through the existing manager selection
  and bootstrap path. No new lock is required; a later caller may observe and
  reuse the first caller's replacement manager.

Failure priority:

- Fatal: replacement cannot send STOP to an incumbent, registry writes fail
  when writing `superseded`, or replacement cannot reach a state where no
  non-superseded active manager remains selected.
- Best effort: auxiliary cleanup such as detached-launcher acknowledgement or
  startup stderr cleanup must not downgrade a successfully proved replacement.

Rollback:

- Revert CLI flag additions, helper changes, tests, and docs. Existing
  `manager stop`, `manager start`, and `manager serve` behavior remains
  backward-compatible because `--replace` is additive.

Rollout:

- Ship `--replace` before changing systemd units. Then update systemd to run
  `weft manager serve --replace`.
- Cron or worker submissions should not use `--replace` unless the operator
  truly wants that environment to own the manager for the context.
- `--replace` is graceful-only in this slice. Operators that need forceful
  intervention should run `weft manager stop --force` first, then run the
  replacement command. Adding `--replace --force` is out of scope for v1.

## 5. Tasks

1. Write failing command-layer tests for replacement helper behavior.
   - Files to touch: `tests/commands/test_manager_commands.py`,
     `tests/commands/test_serve.py`, and possibly `tests/commands/test_run.py`.
   - Read first: `weft/core/manager_runtime.py` stop/start helpers.
   - Test cases:
     - `manager start --replace` resolves a selected active manager, calls the
       shared STOP writer before `_start_manager()`, and starts only after the
       STOP write and `superseded` registry write succeed.
     - `manager serve --replace` does the same before
       `_run_manager_process_foreground()`.
     - replacement failure from STOP write or `superseded` registry write
       returns non-zero and does not start the replacement.
     - ambiguous external-supervisor foreground row with no positive live proof
       is marked `superseded` and then startup proceeds.
     - positive PING/PONG external-supervisor row receives STOP before
       `_mark_manager_stopped()`.
     - both `manager start --replace` and `manager serve --replace` cover the
       external-supervisor discriminator so the two entry points cannot drift.
   - Mock only the expensive process-launch boundary. Keep registry queues real
     for tests that assert row retirement.
   - Stop if the test design mocks the replacement helper in every case and never
     proves a STOP write or registry effect.
   - Stop if the serve-replace retire test would pass even if the replace
     helper were not called; the test must prove the `--replace` path, not only
     the existing foreground serve preflight.
   - Done when tests fail for the current implementation for the intended
     reasons.

2. Add a shared replacement helper in `weft/core/manager_runtime.py`.
   - Outcome: one core helper owns "replace selected active manager" semantics
     for both start and serve.
   - Files to touch: `weft/core/manager_runtime.py`.
   - Approach:
     - Select active manager with `probe_stale=True`.
     - If no active manager is selected, return success.
     - Repeat `select -> STOP -> supersede` until `_select_active_manager()` returns
       `None`, capped by one overall `MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS`
       budget from helper entry. On budget expiry, return failure.
     - Call `_send_stop(context, tid, record=record)` for each selected row.
     - After STOP is written, rewrite the selected manager's service-owner
       evidence as `superseded` through a shared registry marking helper.
     - Keep the existing foreground serve cleanup path for ambiguous foreground
       external-supervisor rows with no positive live proof, but make it write
       `superseded`.
     - Re-select after superseding a row. If another active manager is revealed,
       handle it before startup.
     - Ensure registry reduction keeps the latest row per service key and owner
       TID before live-owner selection so a latest `superseded` row excludes
       older active rows for the same TID.
   - Reuse: `_select_active_manager()`, `_manager_handle_from_record()`,
     `_send_stop()`, `_mark_manager_stopped()` or a narrow status parameter on
     that helper.
   - Do not add new control commands.
   - Stop if implementation adds a second STOP queue writer instead of using
     `_send_stop()`.
   - Done when command-layer tests around STOP ordering and row retirement pass.

3. Add `--replace` to `weft manager start`.
   - Files to touch: `weft/commands/manager.py`, `weft/cli/app.py`,
     `tests/commands/test_manager_commands.py`, `tests/cli/test_cli_manager.py`.
   - Approach:
     - Add a boolean `replace` option to the command wrapper.
     - If `replace` is false, preserve current behavior exactly.
     - If `replace` is true, call the shared replacement helper before the
       existing `_ensure_manager()` or `_start_manager()` path.
     - Prefer starting a fresh manager after replacement rather than returning
       an unrelated already-running manager row.
     - If another manager appears after replacement and before `_ensure_manager`
       settles, reuse the existing `_ensure_manager` convergence behavior
       rather than adding a lock.
   - Stop if `--replace` changes default `manager start` behavior.
   - Done when `manager start --replace` tests prove STOP-before-start and
     default `manager start` tests still pass.

4. Add `--replace` to `weft manager serve`.
   - Files to touch: `weft/commands/serve.py`, `weft/cli/app.py`,
     `weft/core/manager_runtime.py`, `tests/commands/test_serve.py`,
     `tests/cli/test_cli_serve.py`.
   - Approach:
     - Add a boolean `replace` option to the foreground serve command.
     - If `replace` is false, preserve current duplicate-manager preflight.
     - If `replace` is true, run the shared replacement helper before
       foreground runtime invocation.
     - After replacement helper succeeds, call `_serve_manager_foreground()`
       unchanged. Its duplicate preflight should still run as a second guard
       against a post-STOP race.
   - Real process coverage:
     - Start a foreground serve manager, then run a second `manager serve
       --replace` in a controlled test only if the harness can avoid flakes.
       If full concurrent process replacement is too expensive, use a narrower
       command-layer proof plus existing `test_serve_runs_in_foreground...` and
       `test_serve_sigterm_drains_children_cleanly`.
   - Stop if implementation needs a `SUPERSEDE` control message.
   - Done when serve command tests prove replacement and existing serve tests
     still pass.

5. Update documentation and specs.
   - Files to touch: `docs/specifications/03-Manager_Architecture.md`,
     `docs/specifications/10-CLI_Interface.md`, `README.md`,
     `docs/plans/README.md`.
   - Required doc updates:
     - Add this plan to the relevant spec plan backlink sections.
     - Document `weft manager start --replace`.
     - Document `weft manager serve --replace`.
     - Explain that replacement sends STOP to `ctrl_in`, writes `superseded`,
       and does not wait for stopped evidence in v1.
     - Document `superseded` as non-live manager service-owner evidence in
       `weft.state.services`.
     - Explain that `atexit` cleanup is best-effort and not the reliability
       boundary.
   - Stop if docs imply `--replace` is a general submission mode or cron
     default.
   - Done when plan metadata tests pass and README/spec behavior match code.

## 6. Testing Plan

Use real broker-backed queues for registry and STOP queue behavior. Use mocks
only for expensive launch boundaries and for deterministic PING/PONG outcomes.

Tests to add or update:

- `tests/commands/test_manager_commands.py`
  - `test_start_replace_sends_stop_before_starting_replacement`
  - `test_start_replace_fails_without_start_when_stop_fails`
  - `test_start_replace_marks_stopped_incumbent_superseded`
  - `test_start_replace_retires_unreachable_foreground_supervisor_as_superseded`
- `tests/commands/test_serve.py`
  - `test_serve_replace_sends_stop_before_foreground_start`
  - `test_serve_replace_retires_unreachable_external_supervisor_row_as_superseded`
  - `test_serve_replace_stops_pong_live_external_supervisor_row`
- `tests/core/test_service_convergence.py` or the existing service-convergence
  test module if present
  - parser accepts `superseded`
  - `superseded` is not live and cannot become canonical live owner
  - a lower-TID owner with older `active` and newer `superseded` rows does not
    beat a higher-TID active owner during canonical selection
  - a manager that observes its own latest `superseded` row stops without
    publishing a newer active row
- `tests/cli/test_cli_manager.py`
  - CLI parse coverage for `manager start --replace`.
- `tests/cli/test_cli_serve.py`
  - CLI parse or command behavior coverage for `manager serve --replace`.
  - Keep existing process tests for foreground serve and SIGTERM drain.
- `tests/specs/test_plan_metadata.py`
  - Run after plan index updates.

Observable behavior to prove:

- STOP message is written to the incumbent `ctrl_in` queue for live
  replacement.
- Replacement startup does not run if STOP fails.
- Ambiguous foreground external-supervisor row is replaced by a `superseded`
  row, then startup continues.
- Positive external-supervisor liveness uses STOP, not direct retirement.
- `superseded` rows are visible in `manager list --all`/status surfaces where
  stopped rows are visible, but do not count as active managers.
- A lower-TID superseded manager does not cause a newer active manager to yield.
- A superseded manager cannot resurrect itself by refreshing its active
  service-owner row.
- Default `manager start` and `manager serve` behavior is unchanged.

Tempting but out of scope:

- Full systemd integration test. Local tests should model the registry and
  control behavior. A deployed systemd run is an operational validation step,
  not required in the unit test suite.
- A new submission mode such as `weft run --no-manager-autostart`.
- A new manager control message.
- `--replace --force`; v1 replacement is graceful.

## 7. Verification And Gates

Per-task commands:

```bash
./.venv/bin/python -m pytest tests/commands/test_manager_commands.py -q
./.venv/bin/python -m pytest tests/commands/test_serve.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_manager.py::test_manager_start_and_status -q
./.venv/bin/python -m pytest tests/cli/test_cli_serve.py::test_serve_runs_in_foreground_and_reuses_single_manager -q
./.venv/bin/python -m pytest tests/cli/test_cli_serve.py::test_serve_sigterm_drains_children_cleanly -q
```

Final gates:

```bash
./.venv/bin/python -m pytest tests/commands/test_manager_commands.py tests/commands/test_serve.py tests/cli/test_cli_manager.py tests/cli/test_cli_serve.py::test_serve_runs_in_foreground_and_reuses_single_manager tests/cli/test_cli_serve.py::test_serve_sigterm_drains_children_cleanly
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py
./.venv/bin/python -m ruff check weft tests/commands/test_manager_commands.py tests/commands/test_serve.py tests/cli/test_cli_manager.py tests/cli/test_cli_serve.py
./.venv/bin/python -m mypy weft/core/manager_runtime.py weft/commands/manager.py weft/commands/serve.py weft/cli/app.py
```

Runtime observation after deploy:

- systemd unit uses `weft manager serve --replace`.
- During redeploy, observe one successful replacement rather than repeated
  `manager serve` exits until the stale timeout.
- `weft manager list --all --json` should show the old manager as superseded
  and the new foreground manager active.

## 8. Independent Review Loop

Reviewer availability checked during planning: same-session tools can run a
provider-backed review agent. A Claude read-only plan review was run before
implementation. The review said the plan was directionally sound but not
implementation-ready until several ambiguities were fixed.

Review prompt:

> Read `docs/plans/2026-05-13-manager-replace-start-serve-plan.md` and the
> referenced manager runtime, CLI, and test files. Look for errors, bad ideas,
> and latent ambiguities. Do not implement. Could you implement this confidently
> and correctly if asked?

The author must respond to each review finding by updating this plan or
recording why the current path is still correct.

Review findings addressed in this draft:

- Retire-without-STOP is now limited to foreground `external-supervisor` rows
  with no positive live proof in the existing foreground serve cleanup path.
  Explicit `--replace` sends STOP before writing `superseded`.
- Live-row STOP now explicitly uses `_send_stop()` and does not wait for
  stopped confirmation in v1.
- `serve --replace` now explicitly keeps the normal foreground serve preflight
  after replacement as a post-STOP race guard.
- The select/STOP-or-retire loop now has an overall timeout budget.
- `--replace --force` is explicitly out of scope for v1.
- Start and serve require parallel tests around external-supervisor
  replacement behavior.
- `superseded` is now explicit service-owner evidence, and convergence is
  lowest non-superseded live TID.

## 9. Out Of Scope

- `weft run --wait-for-manager` or any submission mode that refuses to
  auto-start a manager.
- New control messages such as `SUPERSEDE`.
- Changing the default manager stale timeout.
- Changing SimpleBroker behavior.
- Reworking manager election, lowest-TID leadership, or public work-stealing.
- Changing autostart manifest semantics.
- Adding systemd-specific code paths or environment detection.

## 10. Fresh-Eyes Review

Fresh-eyes pass status: completed after the independent review updates. Before
implementation, re-read this plan as a zero-context engineer and verify again:

- every touched file is named
- replacement always sends STOP to `ctrl_in` for selected rows before marking
  them `superseded`
- foreground stale-row cleanup without STOP is limited to ambiguous
  external-supervisor rows with no positive live proof
- startup cannot proceed after failed STOP write or failed superseded write
- tests prove queue-visible STOP behavior with real broker-backed queues
- docs do not imply cron submissions should use replacement by default
