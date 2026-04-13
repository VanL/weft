# Task Lifecycle Stop/Drain Audit Findings

Date: 2026-04-08
Plan: [task-lifecycle-stop-drain-audit-plan.md](./task-lifecycle-stop-drain-audit-plan.md)
Platform: Darwin

## Summary

The original preserve-cleanup corruption finding has been superseded by
revalidation against `simplebroker 3.1.5`.

Current conclusion:

- The prior "SimpleBroker concurrent DB initialization race" diagnosis is not
  supported by the current evidence.
- The more plausible root cause for the earlier preserve-cleanup corruption is
  SimpleBroker's old shared-sidecar cleanup behavior in
  `cleanup_marker_files()`, which Weft's preserve-cleanup path exercised
  heavily through many short-lived queue handles.
- Under `simplebroker 3.1.5`, the Weft preserve-cleanup stress repro is now
  green locally.
- The previously observed Darwin manager-teardown symptom is not currently
  reproducible in a stable synced dev environment, so there is no current red
  repro in this audit family.

## Revalidated Findings

### [P1] The old startup-validation-race diagnosis should be withdrawn

Why:

- The environment is now using `simplebroker 3.1.5`.
- The SQLite startup validation code in
  `simplebroker/_backends/sqlite/runtime.py::setup_connection_phase()` still
  contains the same pre-validation shape:
  `is_new_database = not (exists and size > 0)` followed by
  `is_valid_database(...)` for existing files.
- Despite that code remaining unchanged, the original Weft preserve-cleanup
  repro now passes repeatedly.

Evidence:

- `uv run python -c ...` reports `simplebroker 3.1.5` from the active venv.
- `uv.lock` is pinned to `simplebroker 3.1.5`.
- The installed `setup_connection_phase()` still does not contain a retry or
  backoff around `is_valid_database(...)`.
- `uv run pytest tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse -q -n 0`
  passed once.
- The same test then passed `3/3` repeated runs.

Assessment:

- The startup-race theory does not fit the current state because the alleged
  faulty code path is still present while the Weft repro is now green.
- That makes the old F1 root-cause claim low confidence and unsuitable as the
  next implementation target.

### [P1] The SimpleBroker shared-sidecar cleanup fix is the most plausible explanation for the preserve-cleanup recovery

Why:

- `simplebroker 3.1.5` includes a changed `cleanup_marker_files()` in
  `simplebroker/_runner.py` that preserves shared setup sidecars for real
  database paths:
  `.connection.lock`, `.connection.done`, `.optimization.lock`,
  `.optimization.done`.
- `BrokerCore.close()` and `BrokerCore.shutdown()` both call
  `cleanup_marker_files()`.
- Weft's preserve-cleanup and liveness helpers create and close many short-lived
  queue handles while managers and tasks are still winding down.
- That is exactly the access pattern that would be sensitive to one handle
  unlinking shared setup-sidecar files that another live process still depends
  on.

Evidence:

- Installed `simplebroker/_runner.py` now preserves shared setup sidecars for
  non-mock database paths.
- Installed `simplebroker/db.py` still calls `cleanup_marker_files()` on handle
  close/shutdown.
- Weft's preserve-cleanup path repeatedly opens and closes short-lived queues in
  helpers such as `_load_tid_mapping_payloads()`, `_latest_task_events()`, and
  `_drain_registry_queue()` in `tests/helpers/weft_harness.py`.
- SimpleBroker's targeted regression slice passed:
  `uv run --extra dev pytest -q -n 0 tests/test_runner_error_handling.py -k "cleanup_marker_files or readonly_database_error"`

Assessment:

- High confidence.
- This explanation fits both the old failure window and the new passing
  behavior under `3.1.5`.
- It also aligns with the other agent's report without requiring a speculative
  new Weft-side manager-startup lock.

### [P2] Weft preserve-cleanup is no longer red locally under `simplebroker 3.1.5`

Why it matters:

- The main audit repro that originally motivated the preserve-cleanup findings
  is now green in the current repo state.
- That means the preserve-cleanup path should not currently be treated as an
  active Weft bug based on the old evidence alone.

Evidence:

- `uv run pytest tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse -q -n 0`
  passed.
- The same test passed `3/3` repeated runs.

Assessment:

- Confirmed non-reproduction in the current environment.
- If the preserve-cleanup issue returns, start by checking the active
  SimpleBroker version and whether the shared-sidecar cleanup behavior changed.

### [P2] The earlier Darwin manager-teardown symptom is not currently reproducible

Why it matters:

- The earlier `exit 158` / `setproctitle` crash observations influenced the
  audit narrative, but a current implementation plan should only target bugs
  with a live red repro.
- This symptom is independent from the preserve-cleanup corruption story and
  should not be silently bundled into the resolved broker issue.

Evidence:

- `uv sync --extra dev` completed before the recheck.
- `uv run --extra dev pytest tests/core/test_manager.py::test_manager_registry_entries -q -n 0 --maxfail=1`
  passed `5/5` repeated runs.
- Direct `Manager(...)` construction also passed under `uv run --extra dev python`
  with process titles still enabled.

Assessment:

- Not currently red.
- Do not land a speculative Weft-side `setproctitle` workaround without a fresh,
  stable repro.

### [P3] The broader host stop/kill and CLI result/status surfaces still look resolved locally

Evidence:

- `uv run pytest tests/tasks/test_command_runner_parity.py -q -n 0` passed.
- `uv run pytest tests/cli/test_cli_pipeline.py tests/cli/test_cli_result_all.py tests/cli/test_cli_list_task.py tests/commands/test_task_commands.py -q -n 0` passed.

Assessment:

- The local evidence still supports keeping any next Weft fix narrow.

### [P3] CI/runtime gating still relies partly on runtime self-skip

Why it matters:

- This is a real test-infrastructure sharp edge even though it does not explain
  the preserve-cleanup failure family.
- A broken runner preflight would fail hard instead of being excluded earlier by
  markers or workflow selection.

Evidence:

- [test.yml](/Users/van/Developer/weft/.github/workflows/test.yml) installs
  `.[dev]` and runs `pytest -m "not slow"`, not runner-specific extras.
- [release-gate.yml](/Users/van/Developer/weft/.github/workflows/release-gate.yml)
  installs `.[dev,docker,macos-sandbox]` for the release gate.
- [pyproject.toml](/Users/van/Developer/weft/pyproject.toml) currently defines
  only `slow`, `shared`, and `sqlite_only` pytest markers; there are no
  dedicated `docker` or `macos_sandbox` markers.
- Runner parity tests call `_skip_unavailable_runner(...)` at runtime in
  [test_command_runner_parity.py](/Users/van/Developer/weft/tests/tasks/test_command_runner_parity.py).

Assessment:

- Low severity.
- Worth tightening eventually, but not involved in the currently open Darwin
  manager teardown issue.

## Retained Non-Findings

### `tid_mappings` is still not being used as durable liveness or outcome truth

Why:

- The harness liveness path skips manager tids and terminal tasks before it even
  checks candidate PIDs.
- CLI stop/kill still sends control first and waits on the control surface
  before falling back to PID or runner-handle operations.

Evidence:

- [weft_harness.py#L364](/Users/van/Developer/weft/tests/helpers/weft_harness.py#L364)
  filters out registered workers, `role == "manager"`, and
  `TERMINAL_TASK_EVENTS` before PID liveness checks.
- [tasks.py#L203](/Users/van/Developer/weft/weft/commands/tasks.py#L203),
  [tasks.py#L296](/Users/van/Developer/weft/weft/commands/tasks.py#L296), and
  [tasks.py#L333](/Users/van/Developer/weft/weft/commands/tasks.py#L333) show
  `stop_tasks()` and `kill_tasks()` waiting on `_await_control_surface(...)`
  before fallback behavior.
- [helpers.py#L226](/Users/van/Developer/weft/weft/helpers.py#L226) still
  rejects zombie processes in `pid_is_live()`.

Assessment:

- Keep future fixes away from treating `tid_mappings` as the authoritative place
  to solve lifecycle truth.

### Manager leadership convergence still looks graceful, not force-kill based

Why:

- The earlier preserve-cleanup corruption should not be re-attributed to the
  manager election path without new evidence.

Evidence:

- [run.py#L354](/Users/van/Developer/weft/weft/commands/run.py#L354) explicitly
  avoids terminating a concurrently starting manager from the CLI launcher and
  leaves convergence to the manager process itself.

Assessment:

- This remains a useful disproved theory even though the separate Darwin
  teardown `158` symptom is still open.

### Preserve-cleanup still looks correct in design but under-documented

Why:

- The current evidence no longer supports preserve-cleanup as an active Weft
  corruption bug, but the contract is still important and still deserves a spec
  home.

Evidence:

- [weft_harness.py#L494](/Users/van/Developer/weft/tests/helpers/weft_harness.py#L494)
  shows preserve cleanup signaling workers first, then lingering tasks after a
  grace period, and requiring 100ms of quiescence before returning.

Assessment:

- Keep the documentation follow-up in
  [08-Testing_Strategy.md](/Users/van/Developer/weft/docs/specifications/08-Testing_Strategy.md),
  but frame it as a contract clarification rather than an active bug write-up.

## Preserve-Cleanup Documentation Target

Default follow-up documentation target:
- [08-Testing_Strategy.md](/Users/van/Developer/weft/docs/specifications/08-Testing_Strategy.md)

Updated interpretation:

- Preserve-cleanup semantics still belong in test-infrastructure docs.
- The document update should describe the intended contract, but it should no
  longer frame preserve cleanup as a known active corruption bug in Weft when
  the environment is on `simplebroker 3.1.5` or later.

## Recommended Next Steps

1. Treat the old preserve-cleanup F1 diagnosis as superseded.
2. Keep the SimpleBroker `3.1.5` upgrade in Weft and do not add a speculative
   Weft-side manager-startup lock unless a new repro proves it is still needed.
3. Open a fresh, narrow Weft investigation for the manager teardown `158`
   symptom on Darwin.
4. Update [08-Testing_Strategy.md](/Users/van/Developer/weft/docs/specifications/08-Testing_Strategy.md)
   with the preserve-cleanup contract and note that the earlier corruption repro
   was resolved by the broker dependency upgrade.
5. Optionally add explicit `docker` and `macos_sandbox` test selection or
   workflow gating so runner-specific coverage does not depend entirely on
   runtime self-skip.

## Deferred Work

- Cross-platform Task 7 remains deferred.
  Reason:
  The preserve-cleanup failure no longer reproduces locally, and the remaining
  live issue is a Darwin-specific manager teardown symptom.

- Optional Follow-Up Task A remains deferred.
  Reason:
  Runner-native liveness/control analysis is still not blocking the next narrow
  fix.
