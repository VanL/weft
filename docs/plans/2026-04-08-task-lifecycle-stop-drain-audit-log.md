# Task Lifecycle Stop/Drain Audit Log

Status: audit-log
Source specs: None - audit artifact
Superseded by: none

Date: 2026-04-08
Plan: [task-lifecycle-stop-drain-audit-plan.md](./task-lifecycle-stop-drain-audit-plan.md)
Platform: Darwin
Started: 2026-04-08 06:30:12 CDT

## Symptom Matrix

| Symptom | Current probe | Current result | Likely ownership |
| --- | --- | --- | --- |
| Preserve-cleanup can leave SQLite malformed after manager reuse stress | `tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse` | No longer reproduces locally under `simplebroker 3.1.5`; earlier runs reproduced before the broker upgrade | Earlier failure now most plausibly owned by SimpleBroker shared-sidecar cleanup, not an active Weft bug |
| Host stop/kill parity regressions | `tests/tasks/test_command_runner_parity.py` | Did not reproduce in serial local run | Consumer/base control path currently looks stable on the host runner |
| CLI result/status/list regressions | `tests/cli/test_cli_pipeline.py`, `tests/cli/test_cli_result_all.py`, `tests/cli/test_cli_list_task.py`, `tests/commands/test_task_commands.py` | Did not reproduce in serial local run | User-surface commands currently look stable in this environment |
| Manager/registry/drain instability | `tests/core/test_manager.py` | Reproduced as process exit code `158` after a passing test body | Manager teardown or cleanup path; likely signal/teardown interaction on macOS |

## Experiments

### 1. Harness registration slice

- Date/time: 2026-04-08 06:30 CDT
- Command: `uv run pytest tests/test_harness_registration.py -q -n 0`
- Expected result: harness-only registration and preserve-cleanup contract tests pass
- Actual result: passed with exit code `0`
- Supports/disproves: supports using the harness tests as a stable first-pass probe; does not reproduce the broader corruption symptom by itself
- Next step: run the `tid_mappings` role propagation proof

### 2. `tid_mappings` role propagation

- Date/time: 2026-04-08 06:31 CDT
- Command: `uv run pytest tests/tasks/test_task_observability.py::test_tid_mapping_includes_metadata_role -q -n 0`
- Expected result: role propagation in `tid_mappings` stays intact
- Actual result: passed with exit code `0`
- Supports/disproves: supports the current distinction between manager-vs-task role metadata in `tid_mappings`
- Next step: run the preserve-cleanup stress repro

### 3. Primary preserve-cleanup repro

- Date/time: 2026-04-08 06:31 CDT
- Command: `uv run pytest tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse -q -n 0`
- Expected result: SQLite integrity remains clean before and after `harness.cleanup(preserve_database=True)`
- Actual result: failed with `sqlite3.DatabaseError: database disk image is malformed` after `harness.cleanup(preserve_database=True)`; the same test had already reached `_assert_sqlite_integrity(db_path)` successfully before cleanup
- Supports/disproves: strongly supports that preserve-cleanup is a real corruption surface, not just a timeout or false live-task report
- Next step: check whether host stop/kill parity is also red locally or whether the current failure family is narrower

### 4. Targeted host stop/kill parity

- Date/time: 2026-04-08 06:32 CDT
- Command: `uv run pytest tests/tasks/test_command_runner_parity.py::test_command_runners_stop_cancel_equivalently[host] tests/tasks/test_command_runner_parity.py::test_command_runners_kill_mark_killed_equivalently[host] -q -n 0`
- Expected result: host stop/kill contract stays green
- Actual result: command exited `0`; output shape only showed a passing dot stream
- Supports/disproves: does not reproduce the host stop/kill parity failures in this environment
- Next step: run the full parity file to avoid over-trusting a targeted selection quirk

### 5. Full command runner parity file

- Date/time: 2026-04-08 06:33 CDT
- Command: `uv run pytest tests/tasks/test_command_runner_parity.py -q -n 0`
- Expected result: no host-runner parity failures under serial local execution
- Actual result: passed with exit code `0`
- Supports/disproves: further weakens the theory that the current local failure family is primarily in host stop/kill parity
- Next step: run the CLI/result/status slice named in the symptom inventory

### 6. CLI/result/status slice

- Date/time: 2026-04-08 06:34 CDT
- Command: `uv run pytest tests/cli/test_cli_pipeline.py tests/cli/test_cli_result_all.py tests/cli/test_cli_list_task.py tests/commands/test_task_commands.py -q -n 0`
- Expected result: user-surface result/status/list flows stay green in serial local execution
- Actual result: passed with exit code `0`
- Supports/disproves: suggests the current reproducible failure is narrower than a broad CLI regression
- Next step: probe the manager slice because preserve-cleanup and manager reuse remain implicated

### 7. Manager slice

- Date/time: 2026-04-08 06:35 CDT
- Command: `uv run pytest tests/core/test_manager.py -q -n 0`
- Expected result: manager tests either pass or emit a normal pytest failure
- Actual result: pytest process exited with code `158` and no assertion output after printing pass dots
- Supports/disproves: supports a process-level teardown/signal problem in the manager path rather than a normal assertion failure
- Next step: rerun with verbosity to see whether the failure happens after a passing test body

### 8. Manager slice with verbose output

- Date/time: 2026-04-08 06:36 CDT
- Command: `uv run pytest tests/core/test_manager.py -vv -n 0`
- Expected result: verbose output identifies the failing test
- Actual result: `tests/core/test_manager.py::test_manager_spawns_child PASSED`, then the pytest process exited with code `158` before reporting the next test
- Supports/disproves: strongly supports that the symptom happens after the first passing test body, likely in teardown or process cleanup
- Next step: rerun a single simple manager test to confirm the problem is not specific to one test body

### 9. Single manager registry test

- Date/time: 2026-04-08 06:37 CDT
- Command: `uv run pytest tests/core/test_manager.py::test_manager_registry_entries -vv -n 0`
- Expected result: one passing test exits cleanly
- Actual result: pytest reported `PASSED` for the single test, then the process still exited with code `158`
- Supports/disproves: strongly supports a manager fixture or cleanup-path teardown issue that survives even when the test body itself passes
- Next step: inspect `Manager.cleanup()`, `BaseTask.stop()/cleanup()`, and related signal/termination helpers before writing findings

### 10. Manager-reuse ordering probe

- Date/time: 2026-04-08 06:38 CDT
- Command: `uv run pytest tests/cli/test_cli_run.py::test_cli_run_parallel_no_wait_adopts_active_manager -q -n 0`
- Expected result: manager reuse adoption path still passes on its own
- Actual result: passed with exit code `0`
- Supports/disproves: suggests the preserve-cleanup corruption does not require this earlier manager-reuse probe to fail first
- Next step: finish code-ownership analysis and write findings

### 11. CI/runtime gating read

- Date/time: 2026-04-08 06:39 CDT
- Command: read `.github/workflows/test.yml`, `.github/workflows/release-gate.yml`, `.github/workflows/release.yml`, plus runner test preflight helpers
- Expected result: enough evidence to answer Task 8 without extra runtime churn
- Actual result:
  - `test.yml` runs `pytest -m "not slow"` across Ubuntu, macOS, and Windows but installs only `.[dev]`, not docker or macOS sandbox extras
  - `release-gate.yml` installs `.[dev,docker,macos-sandbox]` on Ubuntu and runs the full SQLite suite plus the PG suite
  - Docker parity tests self-skip through runner preflight when the plugin/binary/runtime is unavailable
  - macOS sandbox plugin tests are mostly validation/preflight tests; parity tests also self-skip when runner preflight fails
- Supports/disproves: supports that runner availability in CI is partly enforced by workflow setup and partly by test-side preflight/self-skip, not by one explicit gating probe
- Next step: write findings and mark any remaining cross-platform work as deferred unless needed

### 12. Dependency revalidation after `simplebroker 3.1.5`

- Date/time: 2026-04-08 later follow-up
- Command: `uv run python - <<'PY' ... import simplebroker ... PY`
- Expected result: confirm the active Weft environment is on the newly released broker version
- Actual result: `simplebroker 3.1.5` loaded from the active venv; `uv.lock` also pins `3.1.5`
- Supports/disproves: supports re-running the audit against the new broker state instead of trusting the older preserve-cleanup repro
- Next step: inspect the installed broker code paths and rerun the Weft repro

### 13. Broker code-path inspection

- Date/time: 2026-04-08 later follow-up
- Command: read installed `simplebroker/_backends/sqlite/runtime.py`, `simplebroker/_runner.py`, and `simplebroker/db.py`
- Expected result: distinguish whether `3.1.5` changed startup validation or cleanup-side sidecar handling
- Actual result:
  - `setup_connection_phase()` still contains the old `is_new_database` plus `is_valid_database(...)` validation flow
  - `cleanup_marker_files()` now preserves shared `.connection.*` and `.optimization.*` sidecars for real database paths
  - `BrokerCore.close()` / `shutdown()` still invoke `cleanup_marker_files()`
- Supports/disproves: strongly disfavors the old startup-race diagnosis and strongly supports the shared-sidecar cleanup explanation
- Next step: rerun the original Weft preserve-cleanup repro under `3.1.5`

### 14. Weft preserve-cleanup repro under `simplebroker 3.1.5`

- Date/time: 2026-04-08 later follow-up
- Command: `uv run pytest tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse -q -n 0`
- Expected result: if the broker fix is relevant, the original preserve-cleanup repro should now pass
- Actual result: passed with exit code `0`
- Supports/disproves: strongly supports that the previous preserve-cleanup corruption was fixed by the broker upgrade, not by any new Weft change
- Next step: repeat a few times to raise confidence

### 15. Repeat preserve-cleanup repro (`3/3`)

- Date/time: 2026-04-08 later follow-up
- Command: run the same preserve-cleanup repro three times serially
- Expected result: repeated passes if the broker-side fix is real
- Actual result: passed `3/3`
- Supports/disproves: further weakens the old startup-race diagnosis because the startup-validation code is unchanged while the Weft repro is now green
- Next step: verify the broker regression slice and recheck any remaining Weft-only symptom

### 16. SimpleBroker regression slice

- Date/time: 2026-04-08 later follow-up
- Command: `uv run --extra dev pytest -q -n 0 tests/test_runner_error_handling.py -k "cleanup_marker_files or readonly_database_error"` (from `/Users/van/Developer/simplebroker`)
- Expected result: new cleanup-marker regression tests pass
- Actual result: passed
- Supports/disproves: supports the broker-side patch and regression coverage claim
- Next step: recheck the Weft manager-teardown symptom separately

### 17. Weft manager-teardown recheck

- Date/time: 2026-04-08 later follow-up
- Command: `uv run --extra dev pytest tests/core/test_manager.py::test_manager_registry_entries -vv -n 0`
- Expected result: verify whether the earlier exit-`158` symptom still reproduces after the broker upgrade
- Actual result: test body reported `PASSED`, then the pytest process exited with code `158`
- Supports/disproves: supports treating the manager teardown symptom as a separate unresolved Weft issue rather than part of the preserve-cleanup broker corruption story
- Next step: update findings to split the resolved broker issue from the still-open manager teardown issue

### 18. Stable-env manager recheck

- Date/time: 2026-04-08 later follow-up
- Command: `uv sync --extra dev` followed by `uv run --extra dev pytest tests/core/test_manager.py::test_manager_registry_entries -q -n 0 --maxfail=1` repeated five times
- Expected result: if the earlier Darwin symptom was an active Weft bug, it should reproduce under a stable synced dev environment
- Actual result: passed `5/5`; direct `Manager(...)` construction under `uv run --extra dev python` also passed with process titles enabled
- Supports/disproves: disfavors treating the earlier `exit 158` / `setproctitle` crash as a current Weft repro
- Next step: keep the note in the findings doc as "not currently reproducible" and avoid speculative process-title changes without a fresh red repro
